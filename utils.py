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
model_cache: dict[str, object] = {
    "models": [],
    "updated_at": 0.0,
}

def init_client(api_key: str) -> None:
    global client
    # 使用 v1alpha 版本
    client = genai.Client(api_key=api_key, http_options={'api_version': 'v1alpha'})

def get_client() -> genai.Client:
    if client is None:
        raise RuntimeError("Gemini client is not initialized")
    return client

def _format_history_for_sdk(raw_history: list) -> list[types.Content]:
    formatted = []
    for turn in raw_history:
        if len(turn) >= 2:
            formatted.append(types.Content(role="user", parts=[types.Part.from_text(text=turn[0])]))
            formatted.append(types.Content(role="model", parts=[types.Part.from_text(text=turn[1])]))
    return formatted

async def init_user(user_id: int) -> UserSession:
    """修復後的恢復邏輯：移除會報錯的手動 history 操作"""
    if user_id not in chat_dict:
        lock = Lock()
        model = await to_thread(get_user_model, user_id) or "gemini-3.1-flash-lite"
        full_model_name = model if model.startswith("models/") else f"models/{model}"
        
        # 從資料庫抓回記憶
        raw_history = await to_thread(load_history, user_id, conf["max_history_turns"])
        history = _format_history_for_sdk(raw_history)
        
        # 建立對話，SDK 會自動管理後續記憶
        chat = get_client().aio.chats.create(model=full_model_name, history=history)
        chat_dict[user_id] = {"chat": chat, "lock": lock, "model": model}
        
    return chat_dict[user_id]

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

async def get_current_model(user_id: int) -> str | None:
    session = await init_user(user_id)
    return session["model"]

async def clear_history(user_id: int) -> None:
    session = await init_user(user_id)
    async with session["lock"]:
        await to_thread(clear_user_history, user_id)
        new_chat = get_client().aio.chats.create(model=session["model"])
        session["chat"] = new_chat

async def save_turn(user_id: int, contents: str | list[Any], model_text: str) -> None:
    """
    【修正點】：移除 session["chat"].history.append。
    因為 AsyncChat 本身在發送訊息時就會記住當前對話，
    我們只需要確保資料庫有存（以便伺服器重啟後可以恢復）即可。
    """
    if not model_text or not model_text.strip(): return
    session = chat_dict.get(user_id)
    if not session or session["model"] is None: return
    
    user_text = _normalize_contents_for_history(contents)
    # 確保對話存入資料庫（這是恢復記憶的唯一來源）
    await to_thread(append_turn, user_id, session["model"], user_text, model_text, conf["max_history_turns"])

def _normalize_contents_for_history(contents: str | list[Any]) -> str:
    if isinstance(contents, str): return contents
    caption = next((item.strip() for item in contents if isinstance(item, str)), "")
    prefix = "[Media] " if any(not isinstance(item, str) for item in contents) else ""
    return f"{prefix}{caption}".strip() or "[Multi-modal Content]"

# 其餘 list_available_models 等函數保持原樣...
