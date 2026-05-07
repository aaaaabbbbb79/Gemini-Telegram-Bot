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

async def gemini_stream(bot: TeleBot, message: Message, contents: str | list) -> None:
    # 1. 發送一個正在思考的提示
    sent_message = await bot.reply_to(message, "🤖 Gemini is thinking...")
    from datetime import datetime
    current_time_str = datetime.now().strftime("%Y-%m-%d %H:%M")
    if isinstance(contents, str):
        contents = f"【系統校時：現在是 {current_time_str}，地點在台灣】\n\n{contents}"
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
            # 2. 修改點：捨棄 stream 模式，改用一次性獲取
            # 這樣連線只會開一次，最符合 Serverless 環境
            response = await chat.send_message(contents)
            full_response = response.text

            # 3. 嘗試用 MarkdownV2 格式更新訊息
            try:
                await bot.edit_message_text(
                    escape(full_response),
                    chat_id=sent_message.chat.id,
                    message_id=sent_message.message_id,
                    parse_mode="MarkdownV2"
                )
            except Exception as e:
                # 如果 Markdown 解析失敗（常見於代碼區塊），改用純文字
                await bot.edit_message_text(
                    full_response,
                    chat_id=sent_message.chat.id,
                    message_id=sent_message.message_id
                )

            # 4. 儲存對話紀錄
            try:
                await save_turn(message.from_user.id, contents, full_response)
            except Exception:
                traceback.print_exc()

        except Exception as e:
            traceback.print_exc()
            error_msg = get_user_error_message(e)
            try:
                await bot.edit_message_text(
                    error_msg,
                    chat_id=sent_message.chat.id,
                    message_id=sent_message.message_id
                )
            except Exception:
                traceback.print_exc()
