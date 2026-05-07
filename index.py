import argparse
import asyncio
import os
import telebot
import traceback
from datetime import datetime
from telebot.async_telebot import AsyncTeleBot
import handlers
from access_control import init_admin_user_ids
from storage import init_db
from utils import init_client
from flask import Flask, request

# --- 1. 配置與初始化 ---
parser = argparse.ArgumentParser()
parser.add_argument("--db-path", default="/tmp/bot.db", help="SQLite database path")
parser.add_argument("--admin-user-ids", default=None, help="Comma-separated Telegram admin user ids")
options = parser.parse_args()

# 強制路徑與環境變數讀取
options.db_path = "/tmp/bot.db"
tg_token = os.getenv("TELEGRAM_BOT_API_KEY", "")
gemini_api_key = os.getenv("GEMINI_API_KEYS", "").split(',')[0].strip()
admin_user_ids = options.admin_user_ids or os.getenv("ADMIN_USER_IDS", "")

# 驗證必要參數
if not tg_token.strip() or not gemini_api_key.strip() or not admin_user_ids.strip():
    raise RuntimeError("Missing necessary environment variables.")

# 初始化組件
init_admin_user_ids(admin_user_ids)
init_client(gemini_api_key)
init_db(options.db_path)

# 建立 Bot 實例
bot = AsyncTeleBot(tg_token)

# --- 2. 註冊 Handler (只需註冊一次) ---
bot.register_message_handler(handlers.start, commands=['start'], pass_bot=True)
bot.register_message_handler(handlers.gemini_handler, commands=['gemini'], pass_bot=True)
bot.register_message_handler(handlers.clear, commands=['clear'], pass_bot=True)
bot.register_message_handler(handlers.model, commands=['model'], pass_bot=True)
bot.register_message_handler(handlers.access, commands=['access'], pass_bot=True)
bot.register_message_handler(handlers.accessrequest, commands=['accessrequest'], pass_bot=True)
bot.register_message_handler(handlers.gemini_photo_handler, content_types=["photo"], pass_bot=True)
bot.register_message_handler(handlers.gemini_private_handler, content_types=['text'], pass_bot=True, func=lambda m: m.chat.type == "private")
bot.register_callback_query_handler(handlers.model_callback, func=lambda c: (c.data or "").startswith("model:"), pass_bot=True)
bot.register_callback_query_handler(handlers.access_callback, func=lambda c: (c.data or "").startswith("access:"), pass_bot=True)
# --- [新增] 註冊占星專屬指令 ---
bot.register_message_handler(handlers.astrology_handler, commands=['horoscope', 'compatibility'], pass_bot=True)

# --- [新增] 註冊按鈕點擊事件 (Callback) ---
# 注意：這裡要放在原本 model_callback 的附近
bot.register_callback_query_handler(handlers.model_callback, func=lambda call: call.data.startswith(handlers.MODEL_CALLBACK_PREFIX), pass_bot=True)
bot.register_callback_query_handler(handlers.astrology_callback, func=lambda call: True, pass_bot=True)

# --- 3. Flask App ---
app = Flask(__name__)

@app.route('/', methods=['POST'])
async def webhook():
    if request.headers.get('content-type') == 'application/json':
        print(f"[{datetime.now()}] --- Webhook 收到新請求 ---")
        json_string = request.get_data().decode('utf-8')
        update = telebot.types.Update.de_json(json_string)
        
        try:
            # 核心：處理更新
            await bot.process_new_updates([update])
            print(f"[{datetime.now()}] Update {update.update_id} 處理完成")
        except Exception as e:
            print(f"[{datetime.now()}] 處理錯誤: {e}")
            traceback.print_exc()
        finally:
            # --- 關鍵修正：手動關閉 aiohttp session 避免報錯 ---
            try:
                # 取得 bot 內部的 session 
                session = await bot.get_session()
                if session and not session.closed:
                    await session.close()
            except Exception as e:
                print(f"Session 清理失敗: {e}")
                
        return ''
    return 'Forbidden', 403

# Vercel 需要這個 app 對象
if __name__ == "__main__":
    # 本地測試時使用
    app.run()
