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
# 取得第一個 API Key
gemini_api_keys = os.getenv("GEMINI_API_KEYS", "").split(',')
gemini_api_key = gemini_api_keys[0].strip() if gemini_api_keys else ""
admin_user_ids = options.admin_user_ids or os.getenv("ADMIN_USER_IDS", "")

# 驗證必要參數
if not tg_token.strip() or not gemini_api_key.strip() or not admin_user_ids.strip():
    raise RuntimeError("Missing necessary environment variables: TELEGRAM_BOT_API_KEY, GEMINI_API_KEYS, or ADMIN_USER_IDS")

# 初始化組件
init_admin_user_ids(admin_user_ids)
init_client(gemini_api_key)
init_db(options.db_path)

# 建立 Bot 實例 (使用非同步版本)
bot = AsyncTeleBot(tg_token)

# --- 2. 註冊 Handler (順序極其重要) ---

# [1] 最優先：功能選單指令
bot.register_message_handler(handlers.start, commands=['start', 'help'], pass_bot=True)

# [2] 功能型指令
bot.register_message_handler(handlers.gemini_handler, commands=['gemini'], pass_bot=True)
bot.register_message_handler(handlers.clear, commands=['clear'], pass_bot=True)
bot.register_message_handler(handlers.model, commands=['model'], pass_bot=True)
bot.register_message_handler(handlers.access, commands=['access'], pass_bot=True)
bot.register_message_handler(handlers.accessrequest, commands=['accessrequest'], pass_bot=True)
bot.register_message_handler(handlers.astrology_handler, commands=['horoscope', 'compatibility'], pass_bot=True)

# [3] 媒體類處理
bot.register_message_handler(handlers.gemini_photo_handler, content_types=["photo"], pass_bot=True)

# [4] 萬用文字處理 (放在私訊指令最後)
bot.register_message_handler(handlers.gemini_private_handler, content_types=['text'], pass_bot=True, func=lambda m: m.chat.type == "private")

# --- 註冊 Callback (按鈕點擊) ---

# 特定前綴優先
bot.register_callback_query_handler(handlers.model_callback, func=lambda c: (c.data or "").startswith("model:"), pass_bot=True)
bot.register_callback_query_handler(handlers.access_callback, func=lambda c: (c.data or "").startswith("access:"), pass_bot=True)

# 占星選單保底處理 (必須放在最後，因為它的 func=True 會攔截所有 callback)
bot.register_callback_query_handler(handlers.astrology_callback, func=lambda call: True, pass_bot=True)

# --- 3. Flask App 與 Webhook 處理 ---
app = Flask(__name__)

@app.route('/', methods=['POST'])
def webhook():
    """
    使用同步方式接收 Webhook，內部透過 asyncio 執行非同步任務
    """
    if request.headers.get('content-type') == 'application/json':
        json_string = request.get_data().decode('utf-8')
        update = telebot.types.Update.de_json(json_string)
        
        # 使用新的事件迴圈處理非同步 Bot 任務
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(bot.process_new_updates([update]))
        except Exception as e:
            print(f"Update 處理出錯: {e}")
            traceback.print_exc()
        finally:
            # 確保執行完畢後清理資源，但不關閉全局 bot session
            loop.close()
            
        return '', 200
    return 'Forbidden', 403

# Vercel 部署不需要 __main__ 啟動，但保留供本地測試
if __name__ == "__main__":
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))
