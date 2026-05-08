import io
import time
import traceback
import datetime
import asyncio
from google.genai import errors, types  # 關鍵：引入 types
from telebot.types import Message
from md2tgmd import escape
from telebot import TeleBot

from config import conf
from utils import init_user, save_turn, get_client

error_info              =       conf["error_info"]
before_generate_info    =       conf["before_generate_info"]
download_pic_notify     =       conf["download_pic_notify"]

def get_user_error_message(error: Exception) -> str:
    status = getattr(error, "status", None)
    error_text = str(error).lower()
    if isinstance(error, errors.ClientError):
        if status == "RESOURCE_EXHAUSTED" or "429" in error_text:
            return conf["quota_error_info"]
    return error_info

async def gemini_stream(bot: TeleBot, message: Message, contents: str | list) -> str | None:
    sent_message = await bot.reply_to(message, "🤖 Gemini is thinking...")
    session_data = await init_user(message.from_user.id)
    chat = session_data.get("chat")
    model_name = session_data.get("model")
    lock = session_data.get("lock")
    
    if chat is None or model_name is None:
        await bot.edit_message_text("Please choose a model first.", chat_id=sent_message.chat.id, message_id=sent_message.message_id)
        return None

    tz_delta = datetime.timedelta(hours=8)
    current_time_str = (datetime.datetime.utcnow() + tz_delta).strftime("%Y-%m-%d %H:%M")
    original_prompt = contents if isinstance(contents, str) else "[多媒體內容分析]"

    async with lock:
        try:
            target_model = model_name if model_name.startswith("models/") else f"models/{model_name}"

            if isinstance(contents, list):
                # 【核心修正】將原始 dict 轉換為 SDK 認可的 types.Part
                formatted_contents = []
                for item in contents:
                    if isinstance(item, dict) and "data" in item:
                        # 這是圖片或影片數據，包裝成 Part
                        formatted_contents.append(
                            types.Part.from_bytes(data=item["data"], mime_type=item["mime_type"])
                        )
                    else:
                        # 這是純文字描述
                        formatted_contents.append(item)
                
                response = await get_client().aio.models.generate_content(
                    model=target_model,
                    contents=formatted_contents
                )
            else:
                text_content = f"[System Time: {current_time_str}]\n{contents}"
                response = await chat.send_message(text_content)
            
            full_response = response.text
            try:
                await bot.edit_message_text(escape(full_response), chat_id=sent_message.chat.id, message_id=sent_message.message_id, parse_mode="MarkdownV2")
            except Exception:
                await bot.edit_message_text(full_response, chat_id=sent_message.chat.id, message_id=sent_message.message_id)
            
            await save_turn(message.from_user.id, original_prompt, full_response)
            return full_response

        except Exception as e:
            traceback.print_exc()
            await bot.edit_message_text(f"❌ {get_user_error_message(e)}", chat_id=sent_message.chat.id, message_id=sent_message.message_id)
            return None
