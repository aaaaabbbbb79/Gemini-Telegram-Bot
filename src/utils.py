from google.genai.chats import AsyncChat
from google import genai
from asyncio import Lock, to_thread
from time import time
from typing import TypedDict

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
    "image",
    "live",
    "robotics",
    "tts",
)

def init_client(api_key: str) -> None:
    """Initialize the Gemini client once during application startup."""
    global client
    client = genai.Client(api_key=api_key)

def get_client() -> genai.Client:
    if client is None:
        raise RuntimeError("Gemini client is not initialized")
    return client

def _normalize_model_name(model_name: str) -> str:
    return model_name.removeprefix("models/")

def _is_chat_model(model_name: str, supported_actions: list[str]) -> bool:
    if not model_name.startswith("gemini-"):
        return False
    if "generateContent" not in supported_actions:
        return False
    return not any(part in model_name for part in EXCLUDED_MODEL_NAME_PARTS)

def _fetch_available_models() -> list[str]:
    models: list[str] = []
    for model in get_client().models.list():
        name = _normalize_model_name(model.name)
        supported_actions = getattr(model, "supported_actions", []) or []
        if not _is_chat_model(name, supported_actions):
            continue
        models.append(name)
    return sorted(set(models))

async def list_available_models(force_refresh: bool = False) -> list[str]:
    now = time()
    cached_models = model_cache["models"]
    updated_at = model_cache["updated_at"]
    if (
        not force_refresh
        and isinstance(cached_models, list)
        and cached_models
        and isinstance(updated_at, float)
        and now - updated_at < MODEL_CACHE_TTL
    ):
        return cached_models

    models = await to_thread(_fetch_available_models)
    model_cache["models"] = models
    model_cache["updated_at"] = now
    return models

async def init_user(user_id: int) -> UserSession:
    """if user not exist in chat_dict, create one
    
    Args:
        user_id: (int): user's id

    Returns:
        UserSession: user's chat session state
    """
    if user_id not in chat_dict:#if not find user's chat
        lock = Lock()
        chat_dict[user_id] = {
            "chat": None,
            "lock": lock,
            "model": None,
        }
    return chat_dict[user_id]

async def select_model(user_id: int, new_model: str) -> str:
    """Select user's chat model, keeping history when a chat already exists.
    
    Args:
        user_id (int): user's id
        new_model (str): model to use

    Returns:
        str: chat's current model
    """
    session = await init_user(user_id)
    lock = session["lock"]

    async with lock:
        old_chat = session["chat"]
        history = old_chat.get_history() if old_chat else None
        if history:
            new_chat = get_client().aio.chats.create(model=new_model, history=history)
        else:
            new_chat = get_client().aio.chats.create(model=new_model)
        session["chat"] = new_chat
        session["model"] = new_model

        return new_model

async def get_current_model(user_id: int) -> str | None:
    session = await init_user(user_id)
    return session["model"]

async def clear_history(user_id: int) -> None:
    """clear user's history
    
    Args:
        user_id (int): user's id

    Returns:
        None
    """
    session = await init_user(user_id)
    lock = session["lock"]

    async with lock:
        model = session["model"]
        if model is None:
            session["chat"] = None
            return
        new_chat = get_client().aio.chats.create(model=model)
        session["chat"] = new_chat
