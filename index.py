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

# --- 2. 註冊 Handler (順序極其重要：由上而下匹配) ---

# [1] 最優先：功能選單指令 (確保 /help 能觸發按鈕)
bot.register_message_handler(handlers.start, commands=['start', 'help'], pass_bot=True)

# [2] 功能型指令 (特定指令優先於萬用文字處理)
bot.register_message_handler(handlers.gemini_handler, commands=['gemini'], pass_bot=True)
bot.register_message_handler(handlers.clear, commands=['clear'], pass_bot=True)
bot.register_message_handler(handlers.model, commands=['model'], pass_bot=True)
bot.register_message_handler(handlers.access, commands=['access'], pass_bot=True)
bot.register_message_handler(handlers.accessrequest, commands=['accessrequest'], pass_bot=True)
bot.register_message_handler(handlers.astrology_handler, commands=['horoscope', 'compatibility'], pass_bot=True)

# [3] 媒體類處理
bot.register_message_handler(handlers.gemini_photo_handler, content_types=["photo"], pass_bot=True)

# [4] 最後防線：處理私訊純文字對話 (當上面的指令都沒匹配到時，才當作一般聊天)
bot.register_message_handler(handlers.gemini_private_handler, content_types=['text'], pass_bot=True, func=lambda m: m.chat.type == "private")

# --- 註冊 Callback (按鈕點擊事件) ---

# [1] 優先處理特定前綴的按鈕 (模型與權限)
bot.register_callback_query_handler(handlers.model_callback, func=lambda c: (c.data or "").startswith("model:"), pass_bot=True)
bot.register_callback_query_handler(handlers.access_callback, func=lambda c: (c.data or "").startswith("access:"), pass_bot=True)

# [2] 處理占星選單與導航按鈕 (作為保底匹配)
bot.register_callback_query_handler(handlers.astrology_callback, func=lambda call: True, pass_bot=True)


# --- 3. Flask App ---
app = Flask(__name__)

@app.route('/', methods=['POST'])
async def webhook():
    if request.headers.get('content-type') == 'application/json':
        print(f"[{datetime.now()}] --- Webhook 收到新請求 ---")
        json_string = request.get_data().decode('utf-8')
