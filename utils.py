from google.genai.chats import AsyncChat
from google import genai
from google.genai import types  # 必須引入 types 進行格式封裝
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
EXCLUDED_MODEL_NAME_PARTS = (
    "computer-use",
    "customtools",
    "embedding",
    "robotics",
    "tts",
)

def init_client(api_key: str) -> None:
    global client
    # 使用 v1alpha 版本以支援最新的 3.1 功能並減少連線問題
    client = genai.Client(api_key=api_key, http_options={'api_version': 'v1alpha'})

def get_client() -> genai.Client:
    if client is None:
        raise RuntimeError("Gemini client is not initialized")
    return client

def _normalize_model_name(model_name: str) -> str:
    return model_name.replace("models/", "")

def _is_chat_model(model_name: str, supported_actions: list[str]) -> bool:
    if not model_name.startswith("gemini-"):
        return False
    if "generateContent" not in supported_actions:
        return False
    return not any(part in model_name for part in EXCLUDED_MODEL_NAME_PARTS)

def _fetch_available_models() -> list[str]:
    models: list[str] = []
    try:
        for model in get_client().models.list():
            name = _normalize_model_name(model.name)
            supported_actions = getattr(model, "supported_actions", []) or []
            if _is_chat_model(name, supported_actions):
                models.append(name)
    except Exception as e:
        print(f"Fetch models error: {e}")
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

def _format_history_for_sdk(raw_history: list) -> list[types.Content]:
    """將資料庫載入的原始歷史轉換為 SDK 要求的 Content 物件列表"""
    formatted = []
    for turn in raw_history:
        # 假設 turn 格式為 [user_text, model_text]
        if len(turn) >= 2:
            formatted.append(types.Content(role="user", parts=[types.Part.from_text(text=turn[0])]))
            formatted.append(types.Content(role="model", parts=[types.Part.from_text(text=turn[1])]))
    return formatted

async def init_user(user_id: int) -> UserSession:
    """
    強化的恢復邏輯：即使記憶體快取失效，也會從資料庫完美恢復對話脈絡。
    """
    if user_id not in chat_dict:
        lock = Lock()
        # 1. 恢復模型選擇
        model = await to_thread(get_user_model, user_id)
        if not model:
            model = "gemini-3.1-flash-lite"
            await to_thread(set_user_model, user_id, model)
        
        full_model_name = model if model.startswith("models/") else f"models/{model}"
        
        # 2. 載入並格式化歷史紀錄
        try:
            raw_history = await to_thread(load_history, user_id, conf["max_history_turns"])
            history = _format_history_for_sdk(raw_history)
        except Exception as e:
            print(f"Load history error for user {user_id}: {e}")
            history = []

        # 3. 建立帶有記憶的 chat 物件
        chat = get_client().aio.chats.create(model=full_model_name, history=history)
        chat_dict[user_id] = {"chat": chat, "lock": lock, "model": model}
        print(f"DEBUG: [User {user_id}] 成功恢復 {len(history)//2} 組對話記憶，使用模型: {full_model_name}")
        
    return chat_dict[user_id]

async def select_model(user_id: int, new_model: str) -> str:
    """切換模型時，透過重新封裝歷史紀錄來確保上下文不中斷。"""
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
        model = session["model"]
        if model is None:
            session["chat"] = None
            return
        await to_thread(clear_user_history, user_id)
        full_model_name = model if model.startswith("models/") else f"models/{model}"
        new_chat = get_client().aio.chats.create(model=full_model_name)
        session["chat"] = new_chat

def _normalize_contents_for_history(contents: str | list[Any]) -> str:
    if isinstance(contents, str):
        return contents
    caption = ""
    has_media = False
    for item in contents:
        if isinstance(item, str):
            caption = item.strip()
        else:
            has_media = True
    
    prefix = "[Media]" if has_media else ""
    return f"{prefix} {caption}".strip() if prefix or caption else "[Multi-modal Content]"

async def save_turn(user_id: int, contents: str | list[Any], model_text: str) -> None:
    if not model_text or not model_text.strip():
        return
    
    session = chat_dict.get(user_id)
    if not session or session["model"] is None:
        return
    
    user_text = _normalize_contents_for_history(contents)
    await to_thread(append_turn, user_id, session["model"], user_text, model_text, conf["max_history_turns"])
