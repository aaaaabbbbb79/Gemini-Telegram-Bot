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
        # 根據報錯日誌，強化對 429 RESOURCE_EXHAUSTED 的判定
        if status == "RESOURCE_EXHAUSTED" or "429" in error_text or "quota" in error_text:
            return conf["quota_error_info"]
    return error_info

async def gemini_stream(bot: TeleBot, message: Message, contents: str | list) -> str | None:
    # 1. 發送思考中的初始訊息
    sent_message = await bot.reply_to(message, "🤖 Gemini 正在連結星象與氣象數據...")
    
    # 2. 獲取使用者 session
    session_data = await init_user(message.from_user.id)
    chat = session_data.get("chat")
    model_name = session_data.get("model")
    lock = session_data.get("lock")
    
    if chat is None or model_name is None:
        await bot.edit_message_text("請先選擇模型再開始探索命運。", chat_id=sent_message.chat.id, message_id=sent_message.message_id)
        return None

    # --- 修正後的模型名稱定義 ---
    target_model = model_name if model_name.startswith("models/") else f"models/{model_name}"
    
    # --- 系統時間與核心 Prompt 強化 (修正強制搜尋的死循環) ---
    tz_delta = datetime.timedelta(hours=8)
    current_time_str = (datetime.datetime.utcnow() + tz_delta).strftime("%Y-%m-%d %H:%M")
    system_instruction = f"[系統公告：當前台灣時間為 {current_time_str}。]"

    original_prompt = contents if isinstance(contents, str) else "[多媒體內容分析]"

    async with lock:
        try:
            # 3. 內容格式化
            formatted_contents = []
            
            # 加入時間前綴
            formatted_contents.append(system_instruction)

            if isinstance(contents, list):
                for item in contents:
                    if isinstance(item, dict) and "data" in item:
                        formatted_contents.append(
                            types.Part.from_bytes(data=item["data"], mime_type=item["mime_type"])
                        )
                    else:
                        formatted_contents.append(str(item))
            else:
                formatted_contents.append(contents)

            # 4. 配置聯網工具與 AFC 限制 (核心修復)
            config = types.GenerateContentConfig(
                tools=[types.Tool(google_search=types.GoogleSearch())],
                temperature=0.7,
                top_p=0.95,
                # 強制鎖死自動調用次數，避免 AI 找不到答案時瘋狂重試耗盡額度
                automatic_function_calling=types.AutomaticFunctionCallingConfig(
                    maximum_remote_calls=1
                )
            )

            # 5. 執行對話
            response = await chat.send_message(formatted_contents, config=config)
            full_response = response.text

            # 6. 更新 Telegram UI
            try:
                await bot.edit_message_text(
                    escape(full_response), 
                    chat_id=sent_message.chat.id, 
                    message_id=sent_message.message_id, 
                    parse_mode="MarkdownV2"
                )
            except Exception:
                await bot.edit_message_text(
                    full_response, 
                    chat_id=sent_message.chat.id, 
                    message_id=sent_message.message_id
                )
            
            # 7. 成功後儲存紀錄
            await save_turn(message.from_user.id, original_prompt, full_response)
            return full_response

        except Exception as e:
            traceback.print_exc()
            # 統一透過錯誤處理函式回傳提示
            await bot.edit_message_text(
                f"❌ {get_user_error_message(e)}", 
                chat_id=sent_message.chat.id, 
                message_id=sent_message.message_id
            )
            return None
