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
# 縮減排除清單，確保 3.1 預覽版不會被誤殺
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
    # 移除 models/ 前綴，讓 SDK 內部自行決定 API 版本 (v1 或 v1beta)
    return model_name.replace("models/", "")

def _is_chat_model(model_name: str, supported_actions: list[str]) -> bool:
    # 擴大識別範圍，確保 gemini-2.0, 2.5, 3, 3.1 都能通過
    if not model_name.startswith("gemini-"):
        return False
    if "generateContent" not in supported_actions:
        return False
    return not any(part in model_name for part in EXCLUDED_MODEL_NAME_PARTS)

def _fetch_available_models() -> list[str]:
    models: list[str] = []
    try:
        # 動態抓取 Google AI Studio 釋出的所有可用模型
        for model in get_client().models.list():
            name = _normalize_model_name(model.name)
            supported_actions = getattr(model, "supported_actions", []) or []
            if _is_chat_model(name, supported_actions):
                models.append(name)
    except Exception as e:
        print(f"Fetch models error: {e}")
        # 保底清單：包含你截圖中需要的核心模型
        return ["gemini-3.1-flash-lite-preview", "gemini-1.5-flash-latest", "gemini-2.0-flash-exp"]
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
        
        # 預設改為你常用的 3.1 Flash 預覽版標籤
        if not model:
            model = "gemini-3.1-flash-lite-preview"
            await to_thread(set_user_model, user_id, model)

        history = await to_thread(load_history, user_id, conf["max_history_turns"])
        # 重要：使用 SDK 的 aio 介面並確保模型名稱正確
        chat = get_client().aio.chats.create(model=model, history=history)
        
        chat_dict[user_id] = {
            "chat": chat,
            "lock": lock,
            "model": model,
        }
    return chat_dict[user_id]

async def select_model(user_id: int, new_model: str) -> str:
    session = await init_user(user_id)
    async with session["lock"]:
        history = await to_thread(load_history, user_id, conf["max_history_turns"])
        # 切換模型時重新建立 AsyncChat 對象
        new_chat = get_client().aio.chats.create(model=new_model, history=history)
        await to_thread(set_user_model, user_id, new_model)
        session["chat"] = new_chat
        session["model"] = new_model
        return new_model

# ... 其他部分保持不變 (get_current_model, clear_history, save_turn 等) ...
