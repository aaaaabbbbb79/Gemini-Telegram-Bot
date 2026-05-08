import io
import time
import traceback
import datetime
import asyncio
from google.genai import errors
from telebot.types import Message
from md2tgmd import escape
from telebot import TeleBot

# 引入 get_client 以便調用非同步模型功能
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
    # 1. 發送思考中的初始訊息
    sent_message = await bot.reply_to(message, "🤖 Gemini is thinking...")
    
    # 2. 獲取使用者 session
    session_data = await init_user(message.from_user.id)
    chat = session_data.get("chat")
    model_name = session_data.get("model") # 這裡獲取的是模型名稱字串
    lock = session_data.get("lock")
    
    if chat is None or model_name is None:
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
    
    # 記錄原始 prompt 以供存檔
    original_prompt = contents if isinstance(contents, str) else "[多媒體內容分析]"
    
    if isinstance(contents, str):
        contents = f"[System Time: {current_time_str}]\n{contents}"

    full_response = ""

    # 使用 lock 確保同個使用者不會同時觸發多個生成任務
    async with lock:
        try:
            # 3. 執行內容生成
            if isinstance(contents, list):
                # 【關鍵修正】修正 AttributeError: 'str' object has no attribute 'generate_content'
                # 透過 get_client().aio.models.generate_content 並傳入模型名稱來處理列表
                response = await get_client().aio.models.generate_content(
                    model=model_name,
                    contents=contents
                )
            else:
                # 純文字維持使用 chat 模式以保留對話上下文
                response = await chat.send_message(contents)
            
            # 4. 取得生成文字
            full_response = response.text

            # 5. 更新 Telegram UI
            try:
                await bot.edit_message_text(
                    escape(full_response),
                    chat_id=sent_message.chat.id,
                    message_id=sent_message.message_id,
                    parse_mode="MarkdownV2"
                )
            except Exception:
                # Markdown 渲染失敗時降級為純文字
                await bot.edit_message_text(
                    full_response,
                    chat_id=sent_message.chat.id,
                    message_id=sent_message.message_id
                )
            
            # 6. 成功後儲存紀錄
            await save_turn(message.from_user.id, original_prompt, full_response)
            return full_response

        except Exception as e:
            traceback.print_exc()
            error_msg = get_user_error_message(e)
            try:
                # 發生報錯時，在 UI 顯示錯誤原因
                await bot.edit_message_text(
                    f"❌ {error_msg}",
                    chat_id=sent_message.chat.id,
                    message_id=sent_message.message_id
                )
            except Exception:
                pass
            return None
