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
from utils import clear_history, get_current_model, list_available_models, select_model, save_turn

error_info              =       conf["error_info"]
before_generate_info    =       conf["before_generate_info"]
download_pic_notify     =       conf["download_pic_notify"]
MODEL_CALLBACK_PREFIX   =       "model:"
ACCESS_CALLBACK_PREFIX  =       "access:"

# --- 輔助函數：建立鍵盤 ---
def build_zodiac_keyboard():
    zodiacs = ["牡羊座", "金牛座", "雙子座", "巨蟹座", "獅子座", "處女座", "天秤座", "天蠍座", "射手座", "摩羯座", "水瓶座", "雙魚座"]
    markup = InlineKeyboardMarkup(row_width=3)
    btns = [InlineKeyboardButton(z, callback_data=f"select_sign:{z}") for z in zodiacs]
    markup.add(*btns)
    return markup

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

def build_time_picker_keyboard(action, sign):
    markup = InlineKeyboardMarkup(row_width=3)
    btns = [
        InlineKeyboardButton("📅 當日", callback_data=f"time_select:day:{action}:{sign}"),
        InlineKeyboardButton("📅 當月", callback_data=f"time_select:month:{action}:{sign}"),
        InlineKeyboardButton("📅 當年", callback_data=f"time_select:year:{action}:{sign}"),
        InlineKeyboardButton("🔙 返回功能清單", callback_data=f"select_sign:{sign}")
    ]
    markup.add(*btns)
    return markup

# --- 核心邏輯與權限 ---
async def ensure_authorized(message: Message, bot: TeleBot) -> bool:
    subject_type, subject_id = get_access_subject(message)
    if await is_subject_authorized(subject_type, subject_id, message.from_user.id):
        return True
    status, created = await request_access(message)
    if status == "approved": return True
    return False

# --- 指令處理器 (Handlers) ---

async def start(message: Message, bot: TeleBot) -> None:
    try:
        welcome_text = "✨ **奕川的解毒劑 - 占星導航** ✨\n\n請先點選你的 **星座** 來開啟專屬功能選單："
        await bot.reply_to(message, escape(welcome_text), reply_markup=build_zodiac_keyboard(), parse_mode="MarkdownV2")
    except Exception:
        traceback.print_exc()

# 【補回失蹤的 Handler】解決 AttributeError
async def astrology_handler(message: Message, bot: TeleBot) -> None:
    if not await ensure_authorized(message, bot): return
    text = message.text.split()
    if len(text) < 2: 
        await bot.reply_to(message, "請輸入格式，例如：/horoscope 雙子座")
        return
    sign = text[1]
    # 這裡的邏輯可以根據需求調整
    if 'horoscope' in text[0]:
        prompt = f"請詳細分析「{sign}」本月的整體運勢、事業、財運與感情建議。"
    else:
        target = text[2] if len(text) > 2 else "伴侶"
        prompt = f"請分析「{sign}」與「{target}」的星座配對指數與相處建議。"
    
    await gemini.gemini_stream(bot, message, prompt)

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
            action, sign = parts[0], parts[1]
            action_names = {"astro_daily": "今日星象", "astro_lucky": "幸運指南", "astro_advice": "星象建議", "astro_stress": "舒壓療癒", "astro_motivation": "動力激勵", "astro_reflection": "心靈練習"}
            display_name = action_names.get(action, "星象功能")
            text = f"🔮 **{sign} - {display_name}**\n\n請選擇您想查看的時間維度："
            await bot.edit_message_text(escape(text), chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=build_time_picker_keyboard(action, sign), parse_mode="MarkdownV2")

        elif data.startswith("time_select:"):
            _, time_frame, action, sign = data.split(":")
            time_map = {"day": "今日/當天", "month": "本月/當月", "year": "今年/年度"}
            time_text = time_map.get(time_frame, "今日")

            prompts = {
                "astro_daily": f"請分析「{sign}」在「{time_text}」的整體星象走勢與能量變化。",
                "astro_lucky": f"請提供「{sign}」在「{time_text}」的幸運色、開運數字、貴人星座與方位建議。",
                "astro_advice": f"請針對「{sign}」在「{time_text}」的工作事業、財富投資、健康狀況與感情生活提供具體星象建議。",
                "astro_stress": f"「{sign}」在「{time_text}」感到壓力較大，請結合當前星象提供合適的療癒舒壓方法與心態調整方案。",
                "astro_motivation": f"「{sign}」在「{time_text}」缺乏前進動力，請根據星象能量給予振奮人心的激勵與行動指引。",
                "astro_reflection": f"請給「{sign}」一個適合在「{time_text}」進行的深度心靈練習或自我對話題目。"
            }

            if action in prompts:
                user_id = call.from_user.id
                user_prompt = prompts[action]
                await bot.answer_callback_query(call.id, text=f"正在觀測 {sign} 的 {time_text} 能量...")
                
                # 執行並接住回傳值以實現話題延續
                response_text = await gemini.gemini_stream(bot, call.message, user_prompt)
                if response_text:
                    await save_turn(user_id, user_prompt, response_text)

        elif data == "nav_model":
            await send_model_picker(call.message, bot)
    except Exception:
        traceback.print_exc()

async def gemini_handler(message: Message, bot: TeleBot) -> None:
    if not await ensure_authorized(message, bot): return
    parts = message.text.strip().split(maxsplit=1)
    if len(parts) < 2: return
    await gemini.gemini_stream(bot, message, parts[1].strip())

async def clear(message: Message, bot: TeleBot) -> None:
    await clear_history(message.from_user.id)
    await bot.reply_to(message, "Cleared")

async def model(message: Message, bot: TeleBot) -> None:
    await send_model_picker(message, bot)

async def model_callback(call: CallbackQuery, bot: TeleBot) -> None:
    try:
        model_index = int(call.data.removeprefix(MODEL_CALLBACK_PREFIX))
        models = await list_available_models()
        model_name = await select_model(call.from_user.id, models[model_index])
        await bot.edit_message_text(f"Model: {model_name}", chat_id=call.message.chat.id, message_id=call.message.message_id)
    except:
        traceback.print_exc()

async def gemini_private_handler(message: Message, bot: TeleBot) -> None:
    if not await ensure_authorized(message, bot): return
    await gemini.gemini_stream(bot, message, message.text.strip())

async def gemini_photo_handler(message: Message, bot: TeleBot) -> None:
    if not await ensure_authorized(message, bot): return
    file = await bot.get_file(message.photo[-1].file_id)
    photo_file = await bot.download_file(file.file_path)
    image = Image.open(io.BytesIO(photo_file))
    await gemini.gemini_stream(bot, message, [image, message.caption or ""])

async def send_model_picker(message: Message, bot: TeleBot) -> None:
    models = await list_available_models()
    markup = InlineKeyboardMarkup()
    for i, m in enumerate(models):
        markup.add(InlineKeyboardButton(m, callback_data=f"{MODEL_CALLBACK_PREFIX}{i}"))
    await bot.send_message(message.chat.id, "Select model:", reply_markup=markup)
