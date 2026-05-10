from google.genai.chats import AsyncChat
from google import genai
from google.genai import types 
from asyncio import Lock, to_thread
import asyncio
from time import time
from typing import Any, TypedDict

from config import conf
from storage import (
    append_turn,
    clear_user_history,
    get_user_model,
    load_history,
    set_user_model,
)

class UserSession(TypedDict):
    chat: AsyncChat | None
    lock: Lock
    model: str | None

chat_dict: dict[int, UserSession] = {}
client: genai.Client | None = None

def init_client(api_key: str) -> None:
    global client
    client = genai.Client(api_key=api_key, http_options={'api_version': 'v1alpha'})

def get_client() -> genai.Client:
    if client is None:
        raise RuntimeError("Gemini client is not initialized")
    return client

def _format_history_for_sdk(raw_history: list) -> list[types.Content]:
    """嚴格格式化歷史紀錄"""
    formatted = []
    for turn in raw_history:
        if len(turn) >= 2:
            formatted.append(types.Content(role="user", parts=[types.Part.from_text(text=turn[0])]))
            formatted.append(types.Content(role="model", parts=[types.Part.from_text(text=turn[1])]))
    return formatted

async def init_user(user_id: int) -> UserSession:
    """
    強化的恢復邏輯，並加入 DEBUG 日誌。
    """
    if user_id not in chat_dict:
        lock = Lock()
        model = await to_thread(get_user_model, user_id) or "gemini-3.1-flash-lite"
        full_model_name = model if model.startswith("models/") else f"models/{model}"
        
        try:
            raw_history = await to_thread(load_history, user_id, conf["max_history_turns"])
            history = _format_history_for_sdk(raw_history)
            print(f"🔄 [DEBUG] 使用者 {user_id} 從資料庫讀取到 {len(history)//2} 組歷史對話")
        except Exception as e:
            print(f"❌ [ERROR] 載入歷史失敗: {e}")
            history = []

        chat = get_client().aio.chats.create(model=full_model_name, history=history)
        chat_dict[user_id] = {"chat": chat, "lock": lock, "model": model}
        
    return chat_dict[user_id]

async def save_turn(user_id: int, contents: str | list[Any], model_text: str) -> None:
    """
    核心檢測點：確認對話是否有真正存進資料庫。
    """
    if not model_text or not model_text.strip():
        return
    
    session = chat_dict.get(user_id)
    if not session or session["model"] is None:
        return
    
    user_text = _normalize_contents_for_history(contents)
    
    try:
        # 同步執行資料庫寫入
        await to_thread(append_turn, user_id, session["model"], user_text, model_text, conf["max_history_turns"])
        print(f"✅ [DEBUG] 使用者 {user_id} 的對話已成功存入資料庫")
        
        # 手動同步記憶體中的 chat 歷史，防止 SDK 延遲
        if session["chat"]:
            session["chat"].history.append(types.Content(role="user", parts=[types.Part.from_text(text=user_text)]))
            session["chat"].history.append(types.Content(role="model", parts=[types.Part.from_text(text=model_text)]))
            print(f"📊 [DEBUG] 記憶體歷史更新完成，目前長度: {len(session['chat'].history)}")
            
    except Exception as e:
        print(f"❌ [ERROR] 儲存對話失敗: {e}")

# 以下為保持不變的輔助函數 ...
def _normalize_contents_for_history(contents: str | list[Any]) -> str:
    if isinstance(contents, str): return contents
    caption = next((item.strip() for item in contents if isinstance(item, str)), "")
    prefix = "[Media] " if any(not isinstance(item, str) for item in contents) else ""
    return f"{prefix}{caption}".strip() or "[Multi-modal Content]"

async def select_model(user_id: int, new_model: str) -> str:
    session = await init_user(user_id)
    async with session["lock"]:
        raw_history = await to_thread(load_history, user_id, conf["max_history_turns"])
        history = _format_history_for_sdk(raw_history)
        full_model_name = new_model if new_model.startswith("models/") else f"models/{new_model}"
        new_chat = get_client().aio.chats.create(model=full_model_name, history=history)
        await to_thread(set_user_model, user_id, new_model)
        chat_dict[user_id] = {"chat": new_chat, "lock": session["lock"], "model": new_model}
        return new_model

async def clear_history(user_id: int) -> None:
    session = await init_user(user_id)
    async with session["lock"]:
        await to_thread(clear_user_history, user_id)
        new_chat = get_client().aio.chats.create(model=session["model"])
        session["chat"] = new_chat
