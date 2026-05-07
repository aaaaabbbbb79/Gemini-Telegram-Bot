import traceback
import io
import asyncio
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

# --- 全域配置 ---
MODEL_CALLBACK_PREFIX = "model:"
ACCESS_CALLBACK_PREFIX = "access:"

# --- 核心邏輯：修復 30 分鐘後的記憶斷層 ---
async def execute_and_save(user_id, bot, message, prompt):
    """
    確保先寫入 Prompt 佔位，避免非同步併發時資料庫還沒寫入，AI 就去讀歷史。
    """
    await save_turn(user_id, prompt, "")
    await asyncio.sleep(0.1) 
    response = await gemini.gemini_stream(bot, message, prompt)
    if response:
        await save_turn(user_id, prompt, response)

# --- 1. 指令 Handlers (index.py 呼叫點) ---

async def start(message: Message, bot: TeleBot) -> None:
    welcome = "✨ **命運導航系統** ✨\n\n請選擇您的 **星座** 開始探索："
    await bot.reply_to(message, escape(welcome), reply_markup=build_zodiac_keyboard(), parse_mode="MarkdownV2")

async def model(message: Message, bot: TeleBot) -> None:
    await send_model_picker(message, bot)

async def clear(message: Message, bot: TeleBot) -> None:
    await clear_history(message.from_user.id)
    await bot.reply_to(message, "✅ 對話記憶已清除。")

async def astrology_handler(message: Message, bot: TeleBot) -> None:
    """處理 /horoscope 與 /compatibility 指令"""
    if not await ensure_authorized(message, bot): return
    text = message.text.split()
    if len(text) < 2:
        await bot.reply_to(message, "請在指令後加上星座，例如：`/horoscope 牡羊座`")
        return
    
    command = text[0].lower()
    sign = text[1]
    prompt = f"請分析「{sign}」的運勢。" if 'horoscope' in command else f"請分析「{sign}」的配對建議。"
    await execute_and_save(message.from_user.id, bot, message, prompt)

# --- 2. 權限審核 Callback (修復 AttributeError: access_callback) ---

async def access_callback(call: CallbackQuery, bot: TeleBot) -> None:
    """處理管理員對權限請求的『核准/拒絕』按鈕"""
    try:
        if not is_admin(call.from_user.id):
            await bot.answer_callback_query(call.id, "權限不足，僅限管理員操作。")
            return

        # access:review_access:decision:subject_type:subject_id:user_id
        parts = call.data.split(":")
        if len(parts) < 6: return
        
        decision = parts[2] # 'approve' or 'decline'
        s_type = parts[3]
        s_id = int(parts[4])
        u_id = int(parts[5])

        result = await review_access(decision, s_type, s_id, u_id, call.from_user.id)
        await bot.edit_message_text(f"✅ 審核完成：{result}", call.message.chat.id, call.message.message_id)
        
        # 通知申請人
        try:
            status_msg = "恭喜！您的存取請求已被核准。" if decision == "approve" else "抱歉，您的請求已被拒絕。"
            await bot.send_message(u_id, status_msg)
        except: pass

    except Exception:
        traceback.print_exc()

# --- 3. 鍵盤組件 (含縣市清單) ---

def build_zodiac_keyboard():
    zodiacs = ["牡羊座", "金牛座", "雙子座", "巨蟹座", "獅子座", "處女座", "天秤座", "天蠍座", "射手座", "摩羯座", "水瓶座", "雙魚座"]
    markup = InlineKeyboardMarkup(row_width=3)
    btns = [InlineKeyboardButton(z, callback_data=f"select_sign:{z}") for z in zodiacs]
    markup.add(*btns)
    return markup

def build_gender_keyboard(sign):
    markup = InlineKeyboardMarkup(row_width=2)
    for g in ["男", "女"]:
        markup.add(InlineKeyboardButton(g, callback_data=f"set_gender:{sign}:{g}"))
    return markup

def build_feature_keyboard(sign, gender):
    markup = InlineKeyboardMarkup(row_width=2)
    btns = [
        InlineKeyboardButton("🌌 今日星象", callback_data=f"astro_daily:{sign}:{gender}"),
        InlineKeyboardButton("☀️ 天氣預報", callback_data=f"weather_country:{sign}:{gender}"),
        InlineKeyboardButton("❤️ 星座配對", callback_data=f"match_start:{sign}:{gender}"),
        InlineKeyboardButton("🍀 幸運指南", callback_data=f"astro_lucky:{sign}:{gender}"),
        InlineKeyboardButton("💡 星象建議", callback_data=f"astro_advice:{sign}:{gender}"),
        InlineKeyboardButton("🧘 舒壓療癒", callback_data=f"astro_stress:{sign}:{gender}"),
        InlineKeyboardButton("🔥 動力激勵", callback_data=f"astro_motivation:{sign}:{gender}"),
        InlineKeyboardButton("🌱 心靈練習", callback_data=f"astro_reflection:{sign}:{gender}"),
        InlineKeyboardButton("🤖 模型切換", callback_data="nav_model"),
        InlineKeyboardButton("🔙 返回星座", callback_data="nav_back_to_zodiac")
    ]
    markup.add(*btns)
    return markup

def build_weather_country_keyboard(sign, gender):
    markup = InlineKeyboardMarkup(row_width=2)
    countries = [("🇹🇼 台灣", "Taiwan"), ("🇨🇳 中國", "China"), ("🇯🇵 日本", "Japan"), ("🇸🇬 星馬", "SG_MY")]
    for name, code in countries:
        markup.add(InlineKeyboardButton(name, callback_data=f"weather_city:{code}:{sign}:{gender}"))
    markup.add(InlineKeyboardButton("🔙 返回", callback_data=f"set_gender:{sign}:{gender}"))
    return markup

def build_weather_city_keyboard(country_code, sign, gender):
    markup = InlineKeyboardMarkup(row_width=3)
    city_db = {
        "Taiwan": [("基隆", "Keelung"), ("台北", "Taipei"), ("新北", "NewTaipei"), ("桃園", "Taoyuan"), ("台中", "Taichung"), ("高雄", "Kaohsiung"), ("宜蘭", "Yilan"), ("花蓮", "Hualien"), ("澎湖", "Penghu")],
        "China": [("菏澤", "Heze"), ("濟南", "Jinan"), ("上海", "Shanghai"), ("北京", "Beijing"), ("廣州", "Guangzhou")],
        "Japan": [("東京", "Tokyo"), ("大阪", "Osaka"), ("京都", "Kyoto")],
        "SG_MY": [("新加坡", "Singapore"), ("吉隆坡", "KualaLumpur")]
    }
    cities = city_db.get(country_code, [])
    btns = [InlineKeyboardButton(name, callback_data=f"weather_type:{code}:{sign}:{gender}:{country_code}") for name, code in cities]
    markup.add(*btns)
    markup.add(InlineKeyboardButton("🔙 返回", callback_data=f"weather_country:{sign}:{gender}"))
    return markup

def build_weather_type_keyboard(city_code, sign, gender, country_code):
    markup = InlineKeyboardMarkup(row_width=1)
    markup.add(
        InlineKeyboardButton("🌈 整體天氣", callback_data=f"weather_final:today:{city_code}:{sign}:{gender}"),
        InlineKeyboardButton("📅 一週趨勢", callback_data=f"weather_final:weekly:{city_code}:{sign}:{gender}")
    )
    markup.add(InlineKeyboardButton("🔙 返回", callback_data=f"weather_city:{country_code}:{sign}:{gender}"))
    return markup

def build_time_picker_keyboard(action, sign, gender):
    markup = InlineKeyboardMarkup(row_width=2)
    btns = [
        InlineKeyboardButton("📅 當日", callback_data=f"time_select:day:{action}:{sign}:{gender}"),
        InlineKeyboardButton("📅 當月", callback_data=f"time_select:month:{action}:{sign}:{gender}"),
        InlineKeyboardButton("📅 當年", callback_data=f"time_select:year:{action}:{sign}:{gender}"),
        InlineKeyboardButton("♾️ 此生", callback_data=f"time_select:life:{action}:{sign}:{gender}")
    ]
    markup.add(*btns)
    markup.add(InlineKeyboardButton("🔙 返回", callback_data=f"set_gender:{sign}:{gender}"))
    return markup

# --- 4. 核心 Callback 邏輯 ---

async def astrology_callback(call: CallbackQuery, bot: TeleBot) -> None:
    try:
        data = call.data
        u_id = call.from_user.id
        c_id = call.message.chat.id
        m_id = call.message.message_id

        if data.startswith("select_sign:"):
            sign = data.split(":")[1]
            await bot.edit_message_text(f"你是 **{sign}**，請問性別是？", c_id, m_id, reply_markup=build_gender_keyboard(sign))

        elif data.startswith("set_gender:"):
            _, sign, gender = data.split(":")
            await bot.edit_message_text(f"✨ **{sign} ({gender})** 導航選單：", c_id, m_id, reply_markup=build_feature_keyboard(sign, gender))

        elif data.startswith("weather_country:"):
            _, sign, gender = data.split(":")
            await bot.edit_message_text("☀️ 請選擇區域：", c_id, m_id, reply_markup=build_weather_country_keyboard(sign, gender))

        elif data.startswith("weather_city:"):
            _, country, sign, gender = data.split(":")
            await bot.edit_message_text(f"📍 城市選單：", c_id, m_id, reply_markup=build_weather_city_keyboard(country, sign, gender))

        elif data.startswith("weather_type:"):
            parts = data.split(":")
            _, city, sign, gender, country = parts
            await bot.edit_message_text(f"🌦️ 欲查詢哪種天氣？", c_id, m_id, reply_markup=build_weather_type_keyboard(city, sign, gender, country))

        elif data.startswith("weather_final:"):
            _, t_type, city, sign, gender = data.split(":")
            prompt = f"請分析「{city}」的天氣 ({t_type})。針對「{gender}性{sign}」給予出門建議。"
            await bot.answer_callback_query(call.id, text="讀取中...")
            await execute_and_save(u_id, bot, call.message, prompt)

        elif data.startswith("astro_"):
            action, sign, gender = data.split(":")
            await bot.edit_message_text("🔮 選擇維度：", c_id, m_id, reply_markup=build_time_picker_keyboard(action, sign, gender))

        elif data.startswith("time_select:"):
            _, t_frame, action, sign, gender = data.split(":")
            prompt = f"分析「{gender}性{sign}」在「{t_frame}」的「{action}」運勢。"
            await bot.answer_callback_query(call.id, text="解析中...")
            await execute_and_save(u_id, bot, call.message, prompt)

        elif data == "nav_back_to_zodiac":
            await bot.edit_message_text("請選擇星座：", c_id, m_id, reply_markup=build_zodiac_keyboard())

        elif data == "nav_model":
            await send_model_picker(call.message, bot)

    except Exception:
        traceback.print_exc()

# --- 5. 模型切換 ---

async def send_model_picker(message: Message, bot: TeleBot) -> None:
    models = await list_available_models()
    markup = InlineKeyboardMarkup()
    for i, m in enumerate(models):
        markup.add(InlineKeyboardButton(m, callback_data=f"{MODEL_CALLBACK_PREFIX}{i}"))
    await bot.send_message(message.chat.id, "請選擇 AI 模型：", reply_markup=markup)

async def model_callback(call: CallbackQuery, bot: TeleBot) -> None:
    try:
        idx = int(call.data.removeprefix(MODEL_CALLBACK_PREFIX))
        models = await list_available_models()
        name = await select_model(call.from_user.id, models[idx])
        await bot.edit_message_text(f"✅ 模型切換至：{name}", call.message.chat.id, call.message.message_id)
    except:
        traceback.print_exc()

# --- 6. 訊息處理 Handlers ---

async def gemini_private_handler(message: Message, bot: TeleBot) -> None:
    if not await ensure_authorized(message, bot): return
    await execute_and_save(message.from_user.id, bot, message, message.text.strip())

async def gemini_handler(message: Message, bot: TeleBot) -> None:
    if not await ensure_authorized(message, bot): return
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2: return
    await execute_and_save(message.from_user.id, bot, message, parts[1].strip())

async def gemini_photo_handler(message: Message, bot: TeleBot) -> None:
    if not await ensure_authorized(message, bot): return
    file = await bot.get_file(message.photo[-1].file_id)
    photo_file = await bot.download_file(file.file_path)
    image = Image.open(io.BytesIO(photo_file))
    caption = message.caption or "請分析照片。"
    
    await save_turn(message.from_user.id, f"[圖片] {caption}", "")
    response = await gemini.gemini_stream(bot, message, [image, caption])
    if response:
        await save_turn(message.from_user.id, f"[圖片] {caption}", response)

async def ensure_authorized(message: Message, bot: TeleBot) -> bool:
    # 與 access_control.py 對接
    subject_type, subject_id = get_access_subject(message)
    if await is_subject_authorized(subject_type, subject_id, message.from_user.id):
        return True
    
    # 若無權限，發送請求按鈕 (可依需求實作)
    await bot.reply_to(message, "⚠️ 您目前沒有權限使用此機器人。")
    return False
