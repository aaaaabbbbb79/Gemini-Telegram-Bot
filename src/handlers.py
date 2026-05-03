import traceback
import io
from PIL import Image
import gemini as gemini
from telebot import TeleBot
from telebot.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from md2tgmd import escape
from config import conf
from utils import clear_history, get_current_model, list_available_models, select_model

error_info              =       conf["error_info"]
before_generate_info    =       conf["before_generate_info"]
download_pic_notify     =       conf["download_pic_notify"]
MODEL_CALLBACK_PREFIX   =       "model:"

def build_model_markup(models: list[str]) -> InlineKeyboardMarkup:
    markup = InlineKeyboardMarkup(row_width=1)
    for index, model in enumerate(models):
        markup.add(InlineKeyboardButton(model, callback_data=f"{MODEL_CALLBACK_PREFIX}{index}"))
    return markup

async def send_model_picker(message: Message, bot: TeleBot) -> None:
    try:
        models = await list_available_models()
    except Exception:
        traceback.print_exc()
        await bot.reply_to(message, error_info)
        return

    if not models:
        await bot.reply_to(message, "No available Gemini chat models found.")
        return

    current_model = await get_current_model(message.from_user.id)
    text = "Please choose a Gemini model:"
    if current_model:
        text += f"\nCurrent model: {current_model}"
    await bot.reply_to(message, text, reply_markup=build_model_markup(models))

async def start(message: Message, bot: TeleBot) -> None:
    try:
        await bot.reply_to(message , escape("Welcome, you can ask me questions now. \nFor example: `Who is john lennon?`"), parse_mode="MarkdownV2")
        await send_model_picker(message, bot)
    except Exception:
        traceback.print_exc()
        await bot.reply_to(message, error_info)

async def gemini_handler(message: Message, bot: TeleBot) -> None:
    try:
        contents = message.text.strip().split(maxsplit=1)[1].strip()
    except IndexError:
        await bot.reply_to(message, escape("Please add what you want to say after /gemini. \nFor example: `/gemini Who is john lennon?`"), parse_mode="MarkdownV2")
        return
    if await get_current_model(message.from_user.id) is None:
        await send_model_picker(message, bot)
        return
    await gemini.gemini_stream(bot, message, contents)

async def clear(message: Message, bot: TeleBot) -> None:
    await clear_history(message.from_user.id)
    await bot.reply_to(message, "Your history has been cleared")

async def model(message: Message, bot: TeleBot) -> None:
    await send_model_picker(message, bot)

async def model_callback(call: CallbackQuery, bot: TeleBot) -> None:
    try:
        model_index = int(call.data.removeprefix(MODEL_CALLBACK_PREFIX))
        models = await list_available_models()
        selected_model = models[model_index]
        model = await select_model(call.from_user.id, selected_model)
        await bot.answer_callback_query(call.id, text=f"Using {model}")
        if call.message:
            await bot.edit_message_text(
                "Now you are using " + model,
                chat_id=call.message.chat.id,
                message_id=call.message.message_id
            )
    except Exception:
        traceback.print_exc()
        await bot.answer_callback_query(call.id, text="Failed to switch model", show_alert=True)

async def gemini_private_handler(message: Message, bot: TeleBot) -> None:
    contents = message.text.strip()
    if await get_current_model(message.from_user.id) is None:
        await send_model_picker(message, bot)
        return
    await gemini.gemini_stream(bot,message,contents)

async def gemini_photo_handler(message: Message, bot: TeleBot) -> None:
    s = message.caption or ""
    if message.chat.type != "private" and not s.startswith("/gemini"):
        return
    if await get_current_model(message.from_user.id) is None:
        await send_model_picker(message, bot)
        return
    try:
        m = s.strip().split(maxsplit=1)[1].strip() if len(s.strip().split(maxsplit=1)) > 1 else ""
        file = await bot.get_file(message.photo[-1].file_id)
        photo_file = await bot.download_file(file.file_path)
        image_stream = io.BytesIO(photo_file)
        image = Image.open(image_stream)
        contents = [image, m]
    except Exception:
        traceback.print_exc()
        await bot.reply_to(message, error_info)
        return
    await gemini.gemini_stream(bot, message, contents)
