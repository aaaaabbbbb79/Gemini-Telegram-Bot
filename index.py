import asyncio
import os
import telebot
import traceback
from telebot.async_telebot import AsyncTeleBot
import handlers
from access_control import init_admin_user_ids
from storage import init_db
from utils import init_client
from flask import Flask, request

# --- 1. 初始化 ---
tg_token = os.getenv("TELEGRAM_BOT_API_KEY", "")
gemini_api_key = os.getenv("GEMINI_API_KEYS", "").split(',')[0].strip()
admin_user_ids = os.getenv("ADMIN_USER_IDS", "")

init_admin_user_ids(admin_user_ids)
init_client(gemini_api_key)
init_db("/tmp/bot.db")

bot = AsyncTeleBot(tg_token)

# --- 2. 註冊所有 Handler ---
bot.register_message_handler(handlers.start, commands=['start', 'help'], pass_bot=True)
bot.register_message_handler(handlers.gemini_handler, commands=['gemini'], pass_bot=True)
bot.register_message_handler(handlers.clear, commands=['clear'], pass_bot=True)
bot.register_message_handler(handlers.model, commands=['model'], pass_bot=True)
bot.register_message_handler(handlers.astrology_handler, commands=['horoscope', 'compatibility'], pass_bot=True)
bot.register_message_handler(handlers.gemini_photo_handler, content_types=["photo"], pass_bot=True)
bot.register_message_handler(handlers.gemini_private_handler, content_types=['text'], pass_bot=True, func=lambda m: m.chat.type == "private")

# 回調註冊
bot.register_callback_query_handler(handlers.model_callback, func=lambda c: (c.data or "").startswith("model:"), pass_bot=True)
bot.register_callback_query_handler(handlers.access_callback, func=lambda c: (c.data or "").startswith("access:"), pass_bot=True)
bot.register_callback_query_handler(handlers.astrology_callback, func=lambda call: True, pass_bot=True)

# --- 3. Flask 路由 ---
app = Flask(__name__)

@app.route('/', methods=['GET'])
def index():
    return "Bot is running...", 200

@app.route('/', methods=['POST'])
def webhook():
    if request.headers.get('content-type') == 'application/json':
        json_string = request.get_data().decode('utf-8')
        update = telebot.types.Update.de_json(json_string)
        
        # 使用現有的事件迴圈執行，避免建立新迴圈導致的 500 錯誤
        loop = asyncio.get_event_loop()
        loop.run_until_complete(bot.process_new_updates([update]))
        
        return '', 200
    return 'Forbidden', 403

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=5000)
