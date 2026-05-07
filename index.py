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

options.db_path = "/tmp/bot.db"
tg_token = os.getenv("TELEGRAM_BOT_API_KEY", "")
gemini_api_key = os.getenv("GEMINI_API_KEYS", "").split(',')[0].strip()
admin_user_ids = options.admin_user_ids or os.getenv("ADMIN_USER_IDS", "")

if not tg_token.strip() or not gemini_api_key.strip() or not admin_user_ids.strip():
    raise RuntimeError("Missing necessary environment variables.")

init_admin_user_ids(admin_user_ids)
init_client(gemini_api_key)
init_db(options.db_path)

# 建立 Bot 實例
bot = AsyncTeleBot(tg_token)

# --- 2. 註冊 Handler ---
bot.register_message_handler(handlers.start, commands=['start', 'help'], pass_bot=True)
bot.register_message_handler(handlers.gemini_handler, commands=['gemini'], pass_bot=True)
bot.register_message_handler(handlers.clear, commands=['clear'], pass_bot=True)
bot.register_message_handler(handlers.model, commands=['model'], pass_bot=True)
bot.register_message_handler(handlers.access, commands=['access'], pass_bot=True)
bot.register_message_handler(handlers.accessrequest, commands=['accessrequest'], pass_bot=True)
bot.register_message_handler(handlers.astrology_handler, commands=['horoscope', 'compatibility'], pass_bot=True)
bot.register_message_handler(handlers.gemini_photo_handler, content_types=["photo"], pass_bot=True)
bot.register_message_handler(handlers.gemini_private_handler, content_types=['text'], pass_bot=True, func=lambda m: m.chat.type == "private")

# 回調註冊
bot.register_callback_query_handler(handlers.model_callback, func=lambda c: (c.data or "").startswith("model:"), pass_bot=True)
bot.register_callback_query_handler(handlers.access_callback, func=lambda c: (c.data or "").startswith("access:"), pass_bot=True)
bot.register_callback_query_handler(handlers.astrology_callback, func=lambda call: True, pass_bot=True)

# --- 3. Flask App ---
app = Flask(__name__)

# 支援 GET 訪問，避免雲端服務健康檢查噴 405
@app.route('/', methods=['GET'])
def index():
    return "Bot is running...", 200

@app.route('/', methods=['POST'])
def webhook():
    if request.headers.get('content-type') == 'application/json':
        json_string = request.get_data().decode('utf-8')
        update = telebot.types.Update.de_json(json_string)
        
        # 核心修正：確保每次處理完都乾淨關閉
        async def process():
            try:
                await bot.process_new_updates([update])
            finally:
                # 取得當前 bot 的 session 並關閉，解決 Unclosed client session
                session = await bot.get_session()
                if session:
                    await session.close()

        asyncio.run(process())
        return '', 200
    return 'Forbidden', 403

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=5000)
