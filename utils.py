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
    client = genai.Client(api_key=api_key, http_options={'api_version': 'v1alpha'})

def get_client() -> genai.Client:
    if client is None:
        raise RuntimeError("Gemini client is not initialized")
    return client

# --- 模型管理 ---
def _normalize_model_name(model_name: str) -> str:
    return model_name.replace("models/", "")

async def list_available_models(force_refresh: bool = False) -> list[str]:
    now = time()
    if not force_refresh and model_cache["models"] and (now - model_cache["updated_at"] < MODEL_CACHE_TTL):
        return model_cache["models"]
    
    def _fetch():
        models = []
        try:
            for m in get_client().models.list():
                name = _normalize_model_name(m.name)
                if name.startswith("gemini-") and "generateContent" in m.supported_actions:
                    if not any(p in name for p in EXCLUDED_MODEL_NAME_PARTS):
                        models.append(name)
        except:
            return ["gemini-1.5-flash", "gemini-1.5-pro", "gemini-3.1-flash-lite"]
        return sorted(set(models))

    models = await to_thread(_fetch)
    model_cache["models"] = models
    model_cache["updated_at"] = now
    return models

# --- 記憶核心：格式轉換與初始化 ---
def _format_history_for_sdk(raw_history: list) -> list[types.Content]:
    formatted = []
    for turn in raw_history:
        if len(turn) >= 2:
            role = "user" if turn[0] == "user" else "model"
            formatted.append(types.Content(
                role=role, 
                parts=[types.Part.from_text(text=str(turn[1]))]
            ))
    return formatted

async def init_user(user_id: int, force_reload: bool = False) -> UserSession:
    if user_id not in chat_dict or force_reload:
        lock = Lock()
        model = await to_thread(get_user_model, user_id) or "gemini-3.1-flash-lite"
        full_model_name = model if model.startswith("models/") else f"models/{model}"
        
        raw_history = await to_thread(load_history, user_id, conf.get("max_history_turns", 10))
        history = _format_history_for_sdk(raw_history)
        
        chat = get_client().aio.chats.create(model=full_model_name, history=history)
        chat_dict[user_id] = {"chat": chat, "lock": lock, "model": model}
        
    return chat_dict[user_id]

# --- 補回 handlers.py 需要的導出函數 ---
async def get_current_model(user_id: int) -> str | None:
    session = await init_user(user_id)
    return session["model"]

async def select_model(user_id: int, new_model: str) -> str:
    session = await init_user(user_id)
    async with session["lock"]:
        raw_history = await to_thread(load_history, user_id, conf.get("max_history_turns", 10))
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
        session["chat"] = get_client().aio.chats.create(model=session["model"])

# --- 核心修正：強制更新記憶鏈 ---
async def save_turn(user_id: int, contents: str | list[Any], model_text: str) -> None:
    if not model_text or not model_text.strip(): return
    session = chat_dict.get(user_id)
    if not session: return
    
    user_text = _normalize_contents_for_history(contents)
    limit = conf.get("max_history_turns", 10)
    
    # 1. 持久化到資料庫
    await to_thread(append_turn, user_id, session["model"], user_text, model_text, limit)
    
    # 2. 暴力同步記憶
    if session["chat"] and hasattr(session["chat"], "_history"):
        session["chat"]._history.append(
            types.Content(role="user", parts=[types.Part.from_text(text=user_text)])
        )
        session["chat"]._history.append(
            types.Content(role="model", parts=[types.Part.from_text(text=model_text)])
        )

def _normalize_contents_for_history(contents: str | list[Any]) -> str:
    if isinstance(contents, str): return contents
    caption = next((item.strip() for item in contents if isinstance(item, str)), "")
    return f"[Media] {caption}".strip() if any(not isinstance(i, str) for i in contents) else caption
