import io
import time
import traceback
from google.genai import errors
from telebot.types import Message
from md2tgmd import escape
from telebot import TeleBot

from config import conf
from utils import init_user, save_turn

error_info              =       conf["error_info"]
before_generate_info    =       conf["before_generate_info"]
download_pic_notify     =       conf["download_pic_notify"]

def get_user_error_message(error: Exception) -> str:
    status = getattr(error, "status", None)
    error_text = str(error).lower()

    if isinstance(error, errors.ClientError):
        if status == "RESOURCE_EXHAUSTED" or "429" in error_text:
            return conf["quota_error_info"]
        if status == "UNAUTHENTICATED" or status == "PERMISSION_DENIED":
            return conf["auth_error_info"]
        if status == "INVALID_ARGUMENT":
            return conf["invalid_error_info"]

    if "resource_exhausted" in error_text or "quota" in error_text or "429" in error_text:
        return conf["quota_error_info"]
    if "unauthorized" in error_text or "permission" in error_text or "api key" in error_text:
        return conf["auth_error_info"]
    if "invalid_argument" in error_text or "400" in error_text:
        return conf["invalid_error_info"]
    if "timeout" in error_text or "timed out" in error_text:
        return conf["timeout_error_info"]
    return error_info

async def gemini_stream(bot:TeleBot, message:Message, contents:str|list) -> None:
    sent_message = await bot.reply_to(message, "🤖 Generating answers...")
    session = await init_user(message.from_user.id)
    chat = session["chat"]
    lock = session["lock"]
    if chat is None:
        await bot.edit_message_text(
            "Please choose a model first with /model.",
            chat_id=sent_message.chat.id,
            message_id=sent_message.message_id
        )
        return

    async with lock:
        try:
            response = await chat.send_message_stream(contents)

            full_response = ""
            last_update = time.time()
            update_interval = conf["streaming_update_interval"]

            async for chunk in response:
                if hasattr(chunk, 'text') and chunk.text:
                    full_response += chunk.text
                    current_time = time.time()

                    if current_time - last_update >= update_interval:

                        try:
                            await bot.edit_message_text(
                                escape(full_response),
                                chat_id=sent_message.chat.id,
                                message_id=sent_message.message_id,
                                parse_mode="MarkdownV2"
                                )
                        except Exception as e:
                            if "parse markdown" in str(e).lower():
                                await bot.edit_message_text(
                                    full_response,
                                    chat_id=sent_message.chat.id,
                                    message_id=sent_message.message_id
                                    )
                            else:
                                if "message is not modified" not in str(e).lower():
                                    print(f"Error updating message: {e}")
                        last_update = current_time

            try:
                await bot.edit_message_text(
                    escape(full_response),
                    chat_id=sent_message.chat.id,
                    message_id=sent_message.message_id,
                    parse_mode="MarkdownV2"
                )
            except Exception as e:
                try:
                    if "parse markdown" in str(e).lower():
                        await bot.edit_message_text(
                            full_response,
                            chat_id=sent_message.chat.id,
                            message_id=sent_message.message_id
                        )
                except Exception:
                    traceback.print_exc()

            try:
                await save_turn(message.from_user.id, contents, full_response)
            except Exception:
                traceback.print_exc()

        except Exception as e:
            traceback.print_exc()
            try:
                await bot.edit_message_text(
                    get_user_error_message(e),
                    chat_id=sent_message.chat.id,
                    message_id=sent_message.message_id
                )
            except Exception:
                traceback.print_exc()
