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
MODEL_CACHE_TTL = 3600
EXCLUDED_MODEL_NAME_PARTS = ("computer-use", "customtools", "embedding", "robotics", "tts")

# --- 客戶端初始化 ---
def init_client(api_key: str) -> None:
    global client
    # 確保使用穩定版 API 避免 session 洩漏
    client = genai.Client(api_key=api_key, http_options={'api_version': 'v1alpha'})

def get_client() -> genai.Client:
    if client is None:
        raise RuntimeError("Gemini client is not initialized")
    return client

# --- 模型列表管理 ---
def _normalize_model_name(model_name: str) -> str:
    return model_name.replace("models/", "")

def _is_chat_model(model_name: str, supported_actions: list[str]) -> bool:
    if not model_name.startswith("gemini-"): return False
    if "generateContent" not in supported_actions: return False
    return not any(part in model_name for part in EXCLUDED_MODEL_NAME_PARTS)

def _fetch_available_models() -> list[str]:
    models: list[str] = []
    try:
        for model in get_client().models.list():
            name = _normalize_model_name(model.name)
            if _is_chat_model(name, getattr(model, "supported_actions", [])):
                models.append(name)
    except Exception:
        return ["gemini-1.5-flash", "gemini-1.5-pro", "gemini-3.1-flash-lite"]
    return sorted(set(models))

async def list_available_models(force_refresh: bool = False) -> list[str]:
    now = time()
    if not force_refresh and model_cache["models"] and (now - model_cache["updated_at"] < MODEL_CACHE_TTL):
        return model_cache["models"]
    models = await to_thread(_fetch_available_models)
    model_cache["models"] = models
    model_cache["updated_at"] = now
    return models

# --- 核心記憶處理 ---
def _format_history_for_sdk(raw_history: list) -> list[types.Content]:
    formatted = []
    for turn in raw_history:
        if len(turn) >= 2:
            # 嚴格對應 SDK 要求格式
            formatted.append(types.Content(role="user", parts=[types.Part.from_text(text=str(turn[0]))]))
            formatted.append(types.Content(role="model", parts=[types.Part.from_text(text=str(turn[1]))]))
    return formatted

async def init_user(user_id: int, force_reload: bool = False) -> UserSession:
    """
    修改點：增加強制重載邏輯。
    如果 chat_dict 裡的對話失效或需要同步資料庫紀錄，會重新讀取。
    """
    if user_id not in chat_dict or force_reload:
        lock = Lock()
        # 1. 從資料庫讀取偏好的模型
        model = await to_thread(get_user_model, user_id) or "gemini-3.1-flash-lite"
        full_model_name = model if model.startswith("models/") else f"models/{model}"
        
        # 2. 關鍵：從資料庫抓回歷史對話 (修復失憶的核心)
        raw_history = await to_thread(load_history, user_id, conf["max_history_turns"])
        history = _format_history_for_sdk(raw_history)
        
        # 3. 建立帶有歷史紀錄的對話物件
        chat = get_client().aio.chats.create(model=full_model_name, history=history)
        chat_dict[user_id] = {"chat": chat, "lock": lock, "model": model}
        
    return chat_dict[user_id]

async def select_model(user_id: int, new_model: str) -> str:
    # 切換模型時，也必須帶上歷史紀錄，否則會斷層
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
        # 清除後給予一個全新的空白對話
        new_chat = get_client().aio.chats.create(model=session["model"])
        session["chat"] = new_chat

async def save_turn(user_id: int, contents: str | list[Any], model_text: str) -> None:
    """
    修改點：不再手動操作 AsyncChat.history，
    而是確保資料庫存檔成功，下次 init_user 時會自動恢復。
    """
    if not model_text or not model_text.strip(): return
    session = chat_dict.get(user_id)
    if not session or session["model"] is None: return
    
    user_text = _normalize_contents_for_history(contents)
    # 將對話存入資料庫
    await to_thread(append_turn, user_id, session["model"], user_text, model_text, conf["max_history_turns"])

def _normalize_contents_for_history(contents: str | list[Any]) -> str:
    if isinstance(contents, str): return contents
    caption = next((item.strip() for item in contents if isinstance(item, str)), "")
    prefix = "[Media] " if any(not isinstance(item, str) for item in contents) else ""
    return f"{prefix}{caption}".strip() or "[Multi-modal Content]"
