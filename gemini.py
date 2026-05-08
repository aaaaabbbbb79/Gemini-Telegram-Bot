import io
import time
import traceback
import datetime
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

async def gemini_stream(bot: TeleBot, message: Message, contents: str | list) -> str | None:
    # 1. 發送正在思考的提示
    sent_message = await bot.reply_to(message, "🤖 Gemini is thinking...")
    
    session = await init_user(message.from_user.id)
    chat = session.get("chat")
    model = session.get("model")  # 從 session 取得 model 實例
    lock = session.get("lock")
    
    if chat is None or model is None:
        await bot.edit_message_text(
            "Please choose a model first with /model.",
            chat_id=sent_message.chat.id,
            message_id=sent_message.message_id
        )
        return None

    # --- 系統時間校正 ---
    tz_delta = datetime.timedelta(hours=8)
    current_time = datetime.datetime.utcnow() + tz_delta
    current_time_str = current_time.strftime("%Y-%m-%d %H:%M")
    
    # 記錄原始 prompt 以供儲存
    original_prompt = contents if isinstance(contents, str) else "[多媒體內容]"
    
    if isinstance(contents, str):
        contents = f"[System Time: {current_time_str}]\n{contents}"

    full_response = None

    async with lock:
        try:
            # 1. 執行內容生成 (根據型態選擇方法)
            if isinstance(contents, list):
                # 針對包含影片/圖片的清單，使用 generate_content 避開 chat 模式的格式限制
                response = await model.generate_content(contents)
            else:
                # 純文字則使用 chat 模式保留對話上下文
                response = await chat.send_message(contents)
            
            # 2. 取得生成的文字內容
            full_response = response.text

            # 3. 更新訊息顯示 (處理 MarkdownV2 渲染)
            try:
                await bot.edit_message_text(
                    escape(full_response),
                    chat_id=sent_message.chat.id,
                    message_id=sent_message.message_id,
                    parse_mode="MarkdownV2"
                )
            except Exception:
                # 若渲染失敗則退回純文字模式
                await bot.edit_message_text(
                    full_response,
                    chat_id=sent_message.chat.id,
                    message_id=sent_message.message_id
                )

            # 4. 成功後儲存對話紀錄並回傳
            await save_turn(message.from_user.id, original_prompt, full_response)
            return full_response

        except Exception as e:
            # 錯誤路徑：列印異常、顯示錯誤提示並返回 None
            traceback.print_exc()
            error_msg = get_user_error_message(e)
            try:
                await bot.edit_message_text(
                    error_msg,
                    chat_id=sent_message.chat.id,
                    message_id=sent_message.message_id
                )
            except Exception:
                pass
            return None
