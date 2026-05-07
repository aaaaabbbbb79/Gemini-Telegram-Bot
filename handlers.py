import traceback
import io
from PIL import Image
import gemini as gemini
from telebot.async_telebot import AsyncTeleBot as TeleBot
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

# --- 輔助函數：建立 12 星座鍵盤 ---
def build_zodiac_keyboard():
    zodiacs = [
        "牡羊座", "金牛座", "雙子座", "巨蟹座",
        "獅子座", "處女座", "天秤座", "天蠍座",
        "射手座", "摩羯座", "水瓶座", "雙魚座"
    ]
    markup = InlineKeyboardMarkup(row_width=3)
    btns = [InlineKeyboardButton(z, callback_data=f"select_sign:{z}") for z in zodiacs]
    markup.add(*btns)
    return markup

# --- 輔助函數：建立功能鍵盤 (帶入星座) ---
def build_feature_keyboard(sign):
    markup = InlineKeyboardMarkup(row_width=2)
    btns = [
        InlineKeyboardButton("🌌 今日星象", callback_data=f"astro_daily:{sign}"),
        InlineKeyboardButton("🍀 幸運指南", callback_data=f"astro_lucky:{sign}"),
        InlineKeyboardButton("💡 星象建議", callback_data=f"astro_advice:{sign}"),
        InlineKeyboardButton("🧘 舒壓療癒", callback_data=f"astro_stress:{sign}"),
        InlineKeyboardButton("🔥 動力激勵", callback_data=f"astro_motivation:{sign}"),
        InlineKeyboardButton("🌱 心靈練習", callback_data=f"astro_reflection:{sign}"),
        InlineKeyboardButton("🤖 切換模型", callback_data="nav_model"),
        InlineKeyboardButton("🔙 返回選星座", callback_data="nav_back_to_zodiac")
    ]
    markup.add(*btns)
    return markup

# --- 權限管理相關輔助函數 (修正 AttributeError 的核心) ---
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
    full_name = " ".join(part for part in [user.first_name, user.last_name] if part) or "N/A"
    return f"New user access request\nUser ID: {user.id}\nUsername: {username}\nName: {full_name}"

async def notify_admins_access_request(message: Message, bot: TeleBot) -> None:
    subject_type, subject_id = get_access_subject(message)
    for admin_id in get_admin_user_ids():
        try:
            await bot.send_message(admin_id, format_access_request(message), reply_markup=build_access_markup(subject_type, subject_id))
        except Exception:
            traceback.print_exc()

async def ensure_authorized(message: Message, bot: TeleBot) -> bool:
    subject_type, subject_id = get_access_subject(message)
    if await is_subject_authorized(subject_type, subject_id, message.from_user.id):
        return True
    current_status = await get_subject_access_status(subject_type, subject_id)
    if current_status is None and not await are_access_requests_enabled():
        await bot.reply_to(message, "Access requests are currently closed.")
        return False
    status, created = await request_access(message)
    if status == "approved": return True
    if created: await notify_admins_access_request(message, bot)
    await bot.reply_to(message, "Your access request has been submitted. Please wait.")
    return False

# --- 主 Handler 函數 ---

async def start(message: Message, bot: TeleBot) -> None:
    try:
        welcome_text = "✨ **奕川的解毒劑 - 占星導航** ✨\n\n請先點選你的 **星座** 來開啟專屬功能選單："
        await bot.reply_to(message, escape(welcome_text), reply_markup=build_zodiac_keyboard(), parse_mode="MarkdownV2")
    except Exception:
        traceback.print_exc()
        await bot.reply_to(message, error_info)

async def astrology_callback(call: CallbackQuery, bot: TeleBot) -> None:
    try:
        data = call.data
        if data.startswith("select_sign:"):
            sign = data.split(":")[1]
            text = f"✨ **{sign}** 的專屬解毒劑選單 ✨\n\n請選擇項目："
            await bot.edit_message_text(escape(text), chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=build_feature_keyboard(sign), parse_mode="MarkdownV2")
        elif data == "nav_back_to_zodiac":
            await bot.edit_message_text("請選擇你的 **星座**：", chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=build_zodiac_keyboard())
        elif data.startswith("astro_"):
            parts = data.split(":")
            action, sign = parts[0], parts[1] if len(parts) > 1 else "未知"
            prompts = {
                "astro_daily": f"分析「{sign}」今天的整體星象與建議。",
                "astro_lucky": f"告訴「{sign}」目前的幸運色、數字與方位。",
                "astro_advice": f"針對「{sign}」的工作、財運、感情提供星象建議。",
                "astro_stress": f"「{sign}」現在壓力大，請提供舒壓方法。",
                "astro_motivation": f"「{sign}」缺乏動力，請給予激勵。",
                "astro_reflection": f"給「{sign}」一個本週心靈練習題目。"
            }
            if action in prompts:
                await bot.answer_callback_query(call.id, text=f"正在為 {sign} 觀測星象...")
                await gemini.gemini_stream(bot, call.message, prompts[action])
        elif data == "nav_model":
            await send_model_picker(call.message, bot)
    except Exception:
        traceback.print_exc()

async def gemini_handler(message: Message, bot: TeleBot) -> None:
    if not await ensure_authorized(message, bot): return
    parts = message.text.strip().split(maxsplit=1)
    if len(parts) < 2: return
    await gemini.gemini_stream(bot, message, parts[1].strip())

async def astrology_handler(message: Message, bot: TeleBot) -> None:
    if not await ensure_authorized(message, bot): return
    text = message.text.split()
    if
