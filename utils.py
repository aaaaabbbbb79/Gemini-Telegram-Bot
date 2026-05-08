from google.genai.chats import AsyncChat
from google import genai
from asyncio import Lock, to_thread
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
    client = genai.Client(api_key=api_key)

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
        return ["gemini-1.5-flash", "gemini-1.5-pro", "gemini-2.0-flash-exp"]
    return sorted(set(models))

async def list_available_models(force_refresh: bool = False) -> list[str]:
    now = time()
    if not force_refresh and model_cache["models"] and (now - model_cache["updated_at"] < MODEL_CACHE_TTL):
        return model_cache["models"]
    models = await to_thread(_fetch_available_models)
    model_cache["models"] = models
    model_cache["updated_at"] = now
    return models

async def init_user(user_id: int) -> UserSession:
    if user_id not in chat_dict:
        lock = Lock()
        model = await to_thread(get_user_model, user_id)
        if not model:
            model = "gemini-1.5-flash"
            await to_thread(set_user_model, user_id, model)
        history = await to_thread(load_history, user_id, conf["max_history_turns"])
        chat = get_client().aio.chats.create(model=model, history=history)
        chat_dict[user_id] = {"chat": chat, "lock": lock, "model": model}
    return chat_dict[user_id]

async def select_model(user_id: int, new_model: str) -> str:
    session = await init_user(user_id)
    async with session["lock"]:
        history = await to_thread(load_history, user_id, conf["max_history_turns"])
        new_chat = get_client().aio.chats.create(model=new_model, history=history)
        await to_thread(set_user_model, user_id, new_model)
        session["chat"] = new_chat
        session["model"] = new_model
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
        new_chat = get_client().aio.chats.create(model=model)
        session["chat"] = new_chat

def _normalize_contents_for_history(contents: str | list[Any]) -> str:
    if isinstance(contents, str):
        return contents
    # 支援圖片與影片的紀錄顯示
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
    # 僅執行資料庫寫入，不在此處重建 chat 物件以避免連線洩漏
    await to_thread(append_turn, user_id, session["model"], user_text, model_text, conf["max_history_turns"])
