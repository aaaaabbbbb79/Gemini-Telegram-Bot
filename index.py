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

# --- 2. 註冊 Handler ---
bot.register_message_handler(handlers.start, commands=['start', 'help'], pass_bot=True)
bot.register_message_handler(handlers.gemini_handler, commands=['gemini'], pass_bot=True)
bot.register_message_handler(handlers.clear, commands=['clear'], pass_bot=True)
bot.register_message_handler(handlers.model, commands=['model'], pass_bot=True)
bot.register_message_handler(handlers.astrology_handler, commands=['horoscope', 'compatibility'], pass_bot=True)
bot.register_message_handler(handlers.gemini_photo_handler, content_types=["photo"], pass_bot=True)
bot.register_message_handler(handlers.gemini_private_handler, content_types=['text'], pass_bot=True, func=lambda m: m.chat.type == "private")

# 回調 (順序重要)
bot.register_callback_query_handler(handlers.model_callback, func=lambda c: (c.data or "").startswith("model:"), pass_bot=True)
bot.register_callback_query_handler(handlers.access_callback, func=lambda c: (c.data or "").startswith("access:"), pass_bot=True)
bot.register_callback_query_handler(handlers.astrology_callback, func=lambda call: True, pass_bot=True)

# --- 3. Flask App ---
app = Flask(__name__)

@app.route('/', methods=['GET'])
def index():
    return "Bot is running...", 200

@app.route('/', methods=['POST'])
def webhook():
    if request.headers.get('content-type') == 'application/json':
        json_string = request.get_data().decode('utf-8')
        update = telebot.types.Update.de_json(json_string)
        
        # 核心：使用單一異步入口處理，避免重複創建 loop
        async def main():
            await bot.process_new_updates([update])
            # 處理完立刻關閉 session，避免 Aiohttp 報錯
            session = await bot.get_session()
            if session: await session.close()

        try:
            asyncio.run(main())
        except Exception as e:
            print(f"Loop error: {e}")
            traceback.print_exc()
            
        return '', 200
    return 'Forbidden', 403

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=5000)
