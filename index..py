import argparse
import asyncio
import os
import telebot
from telebot.async_telebot import AsyncTeleBot
import handlers
from access_control import init_admin_user_ids
from storage import init_db
from utils import init_client

# Init args
parser = argparse.ArgumentParser()
parser.add_argument("--db-path", default="data/bot.db", help="SQLite database path")
parser.add_argument("--admin-user-ids", default=None, help="Comma-separated Telegram admin user ids")
options = parser.parse_args()
tg_token = os.getenv("TELEGRAM_BOT_API_KEY", "")
gemini_api_key = os.getenv("GEMINI_API_KEYS", "")
admin_user_ids = options.admin_user_ids or os.getenv("ADMIN_USER_IDS", "")
if not tg_token.strip():
    parser.error("TELEGRAM_BOT_API_KEY is required")
if not gemini_api_key.strip():
    parser.error("GEMINI_API_KEYS is required")
if not admin_user_ids.strip():
    parser.error("--admin-user-ids or ADMIN_USER_IDS is required")
try:
    init_admin_user_ids(admin_user_ids)
except ValueError as exc:
    parser.error(str(exc))
init_client(gemini_api_key)
init_db(options.db_path)
print("Arg parse done.")


async def main():
    # Init bot
    bot = AsyncTeleBot(tg_token)
    await bot.delete_my_commands(scope=None, language_code=None)
    await bot.set_my_commands(
    commands=[
        telebot.types.BotCommand("start", "Start"),
        telebot.types.BotCommand("gemini", "Chat with gemini"),
        telebot.types.BotCommand("clear", "Clear all history"),
        telebot.types.BotCommand("model","Choose model"),
        telebot.types.BotCommand("access","Manage access"),
        telebot.types.BotCommand("accessrequest","Open or close access requests")
    ],
)
    print("Bot init done.")

    # Init commands
    bot.register_message_handler(handlers.start,                         commands=['start'],         pass_bot=True)
    bot.register_message_handler(handlers.gemini_handler,                commands=['gemini'],        pass_bot=True)
    bot.register_message_handler(handlers.clear,                         commands=['clear'],         pass_bot=True)
    bot.register_message_handler(handlers.model,                         commands=['model'],         pass_bot=True)
    bot.register_message_handler(handlers.access,                        commands=['access'],        pass_bot=True)
    bot.register_message_handler(handlers.accessrequest,                 commands=['accessrequest'], pass_bot=True)
    bot.register_message_handler(handlers.gemini_photo_handler,          content_types=["photo"],    pass_bot=True)
    bot.register_message_handler(handlers.gemini_private_handler,        content_types=['text'],     pass_bot=True, func=lambda message: message.chat.type == "private")
    bot.register_callback_query_handler(handlers.model_callback,          func=lambda call: (call.data or "").startswith("model:"), pass_bot=True)
    bot.register_callback_query_handler(handlers.access_callback,         func=lambda call: (call.data or "").startswith("access:"), pass_bot=True)

    # Start bot
    print("Starting Gemini_Telegram_Bot.")
    await bot.polling(none_stop=True)

if __name__ == '__main__':
    asyncio.run(main())
