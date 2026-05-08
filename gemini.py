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

# 【修正點】返回類型改為 str | None，以便 handlers.py 接收對話內容
async def gemini_stream(bot: TeleBot, message: Message, contents: str | list) -> str | None:
    # 1. 發送一個正在思考的提示
    sent_message = await bot.reply_to(message, "🤖 Gemini is thinking...")
    
    session = await init_user(message.from_user.id)
    chat = session["chat"]
    lock = session["lock"]
    
    if chat is None:
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
    
    # 記錄原始 prompt 以供儲存（不含 System Time 標籤）
    original_prompt = contents if isinstance(contents, str) else str(contents)
    
    if isinstance(contents, str):
        contents = f"[System Time: {current_time_str}]\n{contents}"

    full_response = None

    async with lock:
        try:
            # 1. 判斷 contents 型態並發送訊息
            if isinstance(contents, list):
                # 這裡最保險的做法是直接用 model 生成，繞過 chat 的 list 檢查
                response = await model.generate_content(contents)
            else:
                response = await chat.send_message(contents)
            
            # 2. 務必將結果賦值給 full_response
            full_response = response.text

            # 3. 更新訊息顯示
            try:
                await bot.edit_message_text(
                    escape(full_response),
                    chat_id=sent_message.chat.id,
                    message_id=sent_message.message_id,
                    parse_mode="MarkdownV2"
                )
            except Exception:
                # 如果 Markdown 失敗，改用純文字發送
                await bot.edit_message_text(
                    full_response,
                    chat_id=sent_message.chat.id,
                    message_id=sent_message.message_id
                )
        except Exception as e:
            print(f"發送訊息失敗: {e}")
            full_response = "處理失敗，請稍後再試。"

            # 4. 儲存對話紀錄
            try:
                # 這裡建議存 original_prompt，避免紀錄裡全是 System Time 標籤
                await save_turn(message.from_user.id, original_prompt, full_response)
            except Exception:
                traceback.print_exc()

            # 【修正點】成功生成後回傳結果
            return full_response

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
            return None # 錯誤時回傳 None
