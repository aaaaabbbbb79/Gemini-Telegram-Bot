import traceback
import io
from PIL import Image
import gemini as gemini
from telebot import TeleBot
from telebot.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from md2tgmd import escape
from config import conf
from access_control import (
    are_access_requests_enabled,
    get_admin_user_ids,
    get_access_subject,
    get_approved_access_records,
    get_subject_access_status,
    is_admin,
    is_subject_authorized,
    is_user_authorized,
    request_access,
    review_access,
    revoke_access,
    set_access_request_enabled,
)
from utils import clear_history, get_current_model, list_available_models, select_model

error_info              =       conf["error_info"]
before_generate_info    =       conf["before_generate_info"]
download_pic_notify     =       conf["download_pic_notify"]
MODEL_CALLBACK_PREFIX   =       "model:"
ACCESS_CALLBACK_PREFIX  =       "access:"

def build_access_markup(subject_type: str, subject_id: int) -> InlineKeyboardMarkup:
    markup = InlineKeyboardMarkup(row_width=2)
    markup.add(
        InlineKeyboardButton("Approve", callback_data=f"{ACCESS_CALLBACK_PREFIX}approve:{subject_type}:{subject_id}"),
        InlineKeyboardButton("Reject", callback_data=f"{ACCESS_CALLBACK_PREFIX}reject:{subject_type}:{subject_id}"),
    )
    return markup

def format_access_request(message: Message) -> str:
    user = message.from_user
    username = f"@{user.username}" if user.username else "N/A"
    full_name = " ".join(
        part for part in [user.first_name, user.last_name] if part
    ) or "N/A"
    return (
        "New user access request\n"
        f"User ID: {user.id}\n"
        f"Username: {username}\n"
        f"Name: {full_name}"
    )

async def notify_admins_access_request(message: Message, bot: TeleBot) -> None:
    subject_type, subject_id = get_access_subject(message)
    for admin_id in get_admin_user_ids():
        try:
            await bot.send_message(
                admin_id,
                format_access_request(message),
                reply_markup=build_access_markup(subject_type, subject_id),
            )
        except Exception:
            traceback.print_exc()

async def ensure_authorized(message: Message, bot: TeleBot) -> bool:
    subject_type, subject_id = get_access_subject(message)
    if await is_subject_authorized(subject_type, subject_id, message.from_user.id):
        return True

    current_status = await get_subject_access_status(subject_type, subject_id)
    if current_status is None and not await are_access_requests_enabled():
        await bot.reply_to(message, "Access requests are currently closed. Please contact the administrator.")
        return False

    status, created = await request_access(message)
    if status == "approved":
        return True

    if status == "rejected":
        await bot.reply_to(message, "This access request was rejected. Please contact the administrator.")
        return False

    if status == "revoked":
        await bot.reply_to(message, "This access was revoked. Please contact the administrator.")
        return False

    if created:
        await notify_admins_access_request(message, bot)

    await bot.reply_to(message, "Your access request has been submitted. Please wait for administrator approval.")
    return False

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
        if not await ensure_authorized(message, bot):
            return
        await bot.reply_to(message , escape("Welcome, you can ask me questions now. \nFor example: `Who is john lennon?`"), parse_mode="MarkdownV2")
        await send_model_picker(message, bot)
    except Exception:
        traceback.print_exc()
        await bot.reply_to(message, error_info)

async def gemini_handler(message: Message, bot: TeleBot) -> None:
    if not await ensure_authorized(message, bot):
        return
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
    if not await ensure_authorized(message, bot):
        return
    if message.chat.type != "private":
        await bot.reply_to(message, "Please use /clear in a private chat.")
        return
    await clear_history(message.from_user.id)
    await bot.reply_to(message, "Your history has been cleared")

async def model(message: Message, bot: TeleBot) -> None:
    if not await ensure_authorized(message, bot):
        return
    if message.chat.type != "private":
        await bot.reply_to(message, "Please use /model in a private chat.")
        return
    await send_model_picker(message, bot)

def format_approved_access_record(record: dict[str, object]) -> str:
    subject_id = record["subject_id"]
    username = record["username"]
    full_name = " ".join(
        str(part)
        for part in [record["first_name"], record["last_name"]]
        if part
    ) or "N/A"
    if username:
        return f"User: @{username} {full_name} ({subject_id})"
    return f"User: {full_name} ({subject_id})"

def build_revoke_markup(subject_type: str, subject_id: int) -> InlineKeyboardMarkup:
    markup = InlineKeyboardMarkup(row_width=1)
    markup.add(
        InlineKeyboardButton("Revoke user", callback_data=f"{ACCESS_CALLBACK_PREFIX}revoke:{subject_type}:{subject_id}")
    )
    return markup

async def access(message: Message, bot: TeleBot) -> None:
    if not is_admin(message.from_user.id):
        await bot.reply_to(message, "Only administrators can view access records.")
        return

    records = await get_approved_access_records()
    if not records:
        await bot.reply_to(message, "No approved access records.")
        return

    await bot.reply_to(message, "Approved access records:")
    for record in records:
        subject_type = str(record["subject_type"])
        subject_id = int(record["subject_id"])
        await bot.send_message(
            message.chat.id,
            format_approved_access_record(record),
            reply_markup=build_revoke_markup(subject_type, subject_id),
        )

async def accessrequest(message: Message, bot: TeleBot) -> None:
    if not is_admin(message.from_user.id):
        await bot.reply_to(message, "Only administrators can change access request settings.")
        return

    enabled = not await are_access_requests_enabled()
    await set_access_request_enabled(enabled)
    state = "open" if enabled else "closed"
    await bot.reply_to(message, f"Access requests are now {state}.")

async def model_callback(call: CallbackQuery, bot: TeleBot) -> None:
    try:
        if not await is_user_authorized(call.from_user.id):
            await bot.answer_callback_query(call.id, text="Access approval required", show_alert=True)
            return
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

async def access_callback(call: CallbackQuery, bot: TeleBot) -> None:
    try:
        if not is_admin(call.from_user.id):
            await bot.answer_callback_query(call.id, text="Only administrators can review requests", show_alert=True)
            return

        _, action, subject_type, subject_id_text = call.data.split(":", maxsplit=3)
        if action not in {"approve", "reject", "revoke"}:
            await bot.answer_callback_query(call.id, text="Unknown review action", show_alert=True)
            return
        if subject_type != "user":
            await bot.answer_callback_query(call.id, text="Unknown access subject", show_alert=True)
            return

        subject_id = int(subject_id_text)
        if action == "revoke":
            status = "revoked"
            await revoke_access(subject_type, subject_id, call.from_user.id)
        else:
            status = "approved" if action == "approve" else "rejected"
            await review_access(subject_type, subject_id, status, call.from_user.id)

        await bot.answer_callback_query(call.id, text=f"Request {status}")
        if call.message:
            await bot.edit_message_text(
                f"Access {subject_type} {subject_id} {status}.",
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
            )

        if subject_type == "user":
            try:
                if status == "approved":
                    await bot.send_message(subject_id, "Your access request was approved. You can now use /model to choose a model.")
                elif status == "rejected":
                    await bot.send_message(subject_id, "Your access request was rejected. Please contact the administrator.")
                else:
                    await bot.send_message(subject_id, "Your access was revoked. Please contact the administrator.")
            except Exception:
                traceback.print_exc()
    except Exception:
        traceback.print_exc()
        await bot.answer_callback_query(call.id, text="Failed to review request", show_alert=True)

async def gemini_private_handler(message: Message, bot: TeleBot) -> None:
    if not await ensure_authorized(message, bot):
        return
    contents = message.text.strip()
    if await get_current_model(message.from_user.id) is None:
        await send_model_picker(message, bot)
        return
    await gemini.gemini_stream(bot,message,contents)

async def gemini_photo_handler(message: Message, bot: TeleBot) -> None:
    s = message.caption or ""
    if message.chat.type != "private" and not s.startswith("/gemini"):
        return
    if not await ensure_authorized(message, bot):
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
