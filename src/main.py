import argparse
import asyncio
import telebot
from telebot.async_telebot import AsyncTeleBot
import handlers
from utils import init_client

# Init args
parser = argparse.ArgumentParser()
parser.add_argument("tg_token", help="telegram token")
parser.add_argument("GOOGLE_GEMINI_KEY", help="Google Gemini API key")
options = parser.parse_args()
init_client(options.GOOGLE_GEMINI_KEY)
print("Arg parse done.")


async def main():
    # Init bot
    bot = AsyncTeleBot(options.tg_token)
    await bot.delete_my_commands(scope=None, language_code=None)
    await bot.set_my_commands(
    commands=[
        telebot.types.BotCommand("start", "Start"),
        telebot.types.BotCommand("gemini", "Chat with gemini"),
        telebot.types.BotCommand("clear", "Clear all history"),
        telebot.types.BotCommand("model","Choose model")
    ],
)
    print("Bot init done.")

    # Init commands
    bot.register_message_handler(handlers.start,                         commands=['start'],         pass_bot=True)
    bot.register_message_handler(handlers.gemini_handler,                commands=['gemini'],        pass_bot=True)
    bot.register_message_handler(handlers.clear,                         commands=['clear'],         pass_bot=True)
    bot.register_message_handler(handlers.model,                         commands=['model'],         pass_bot=True)
    bot.register_message_handler(handlers.gemini_photo_handler,          content_types=["photo"],    pass_bot=True)
    bot.register_message_handler(handlers.gemini_private_handler,        content_types=['text'],     pass_bot=True, func=lambda message: message.chat.type == "private")
    bot.register_callback_query_handler(handlers.model_callback,          func=lambda call: (call.data or "").startswith("model:"), pass_bot=True)

    # Start bot
    print("Starting Gemini_Telegram_Bot.")
    await bot.polling(none_stop=True)

if __name__ == '__main__':
    asyncio.run(main())
