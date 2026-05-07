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

# --- 配置 ---
MODEL_CALLBACK_PREFIX   = "model:"
ACCESS_CALLBACK_PREFIX  = "access:"

# --- 核心邏輯：確保記憶延續 ---
async def execute_and_save(user_id, bot, message, prompt):
    await save_turn(user_id, prompt, "")
    await asyncio.sleep(0.1) 
    response = await gemini.gemini_stream(bot, message, prompt)
    if response:
        await save_turn(user_id, prompt, response)

# --- 鍵盤生成組件 ---

def build_zodiac_keyboard(prefix="select_sign"):
    zodiacs = ["牡羊座", "金牛座", "雙子座", "巨蟹座", "獅子座", "處女座", "天秤座", "天蠍座", "射手座", "摩羯座", "水瓶座", "雙魚座"]
    markup = InlineKeyboardMarkup(row_width=3)
    btns = [InlineKeyboardButton(z, callback_data=f"{prefix}:{z}") for z in zodiacs]
    markup.add(*btns)
    return markup

def build_gender_keyboard(sign, prefix="set_gender"):
    markup = InlineKeyboardMarkup(row_width=2)
    for g in ["男", "女"]:
        markup.add(InlineKeyboardButton(g, callback_data=f"{prefix}:{sign}:{g}"))
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

# --- 天氣模組 (找回縣市資料) ---

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
        "Taiwan": [
            ("基隆", "Keelung"), ("台北", "Taipei"), ("新北", "NewTaipei"), ("桃園", "Taoyuan"),
            ("新竹", "Hsinchu"), ("苗栗", "Miaoli"), ("台中", "Taichung"), ("彰化", "Changhua"),
            ("南投", "Nantou"), ("雲林", "Yunlin"), ("嘉義", "Chiayi"), ("台南", "Tainan"),
            ("高雄", "Kaohsiung"), ("屏東", "Pingtung"), ("宜蘭", "Yilan"), ("花蓮", "Hualien"),
            ("台東", "Taitung"), ("澎湖", "Penghu"), ("金門", "Kinmen"), ("連江", "Lienchiang")
        ],
        "China": [
            ("菏澤", "Heze"), ("濟南", "Jinan"), ("青島", "Qingdao"), ("北京", "Beijing"),
            ("上海", "Shanghai"), ("廣州", "Guangzhou"), ("深圳", "Shenzhen"), ("杭州", "Hangzhou")
        ],
        "Japan": [("東京", "Tokyo"), ("大阪", "Osaka"), ("京都", "Kyoto"), ("福岡", "Fukuoka"), ("沖繩", "Okinawa")],
        "SG_MY": [("新加坡", "Singapore"), ("吉隆坡", "KualaLumpur"), ("檳城", "Penang"), ("新山", "JohorBahru")]
    }
    cities = city_db.get(country_code, [])
    btns = [InlineKeyboardButton(name, callback_data=f"weather_type:{code}:{sign}:{gender}") for name, code in cities]
    markup.add(*btns)
    markup.add(InlineKeyboardButton("🔙 返回國家選單", callback_data=f"weather_country:{sign}:{gender}"))
    return markup

def build_weather_type_keyboard(city_code, sign, gender):
    markup = InlineKeyboardMarkup(row_width=1)
    markup.add(
        InlineKeyboardButton("🌈 本日整體天氣 (精簡)", callback_data=f"weather_final:today:{city_code}:{sign}:{gender}"),
        InlineKeyboardButton("⏰ 每小時詳細預報", callback_data=f"weather_final:hourly:{city_code}:{sign}:{gender}"),
        InlineKeyboardButton("📅 本週天氣趨勢", callback_data=f"weather_final:weekly:{city_code}:{sign}:{gender}")
    )
    markup.add(InlineKeyboardButton("🔙 返回城市選單", callback_data=f"weather_city:Taiwan:{sign}:{gender}")) # 預設回台灣
    return markup

def build_time_picker_keyboard(action, sign, gender):
    markup = InlineKeyboardMarkup(row_width=2)
    suffix = f"{action}:{sign}:{gender}"
    btns = [
        InlineKeyboardButton("📅 當日", callback_data=f"time_select:day:{suffix}"),
        InlineKeyboardButton("📅 當月", callback_data=f"time_select:month:{suffix}"),
        InlineKeyboardButton("📅 當年", callback_data=f"time_select:year:{suffix}"),
        InlineKeyboardButton("♾️ 此生", callback_data=f"time_select:life:{suffix}")
    ]
    markup.add(*btns)
    markup.add(InlineKeyboardButton("🔙 返回功能清單", callback_data=f"set_gender:{sign}:{gender}"))
    return markup

# --- Handlers 入口點 ---

async def start(message: Message, bot: TeleBot) -> None:
    welcome = "✨ **命運導航系統** ✨\n請選擇您的 **星座** 開始解析："
    await bot.reply_to(message, escape(welcome), reply_markup=build_zodiac_keyboard(), parse_mode="MarkdownV2")

async def model(message: Message, bot: TeleBot) -> None:
    await send_model_picker(message, bot)

async def clear(message: Message, bot: TeleBot) -> None:
    await clear_history(message.from_user.id)
    await bot.reply_to(message, "✅ 對話記憶已清除。")

# --- Callback 處理邏輯 ---

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
            await bot.edit_message_text("☀️ 選擇區域：", c_id, m_id, reply_markup=build_weather_country_keyboard(sign, gender))

        elif data.startswith("weather_city:"):
            _, country, sign, gender = data.split(":")
            await bot.edit_message_text(f"📍 城市選單：", c_id, m_id, reply_markup=build_weather_city_keyboard(country, sign, gender))

        elif data.startswith("weather_type:"):
            _, city, sign, gender = data.split(":")
            await bot.edit_message_text(f"🌦️ 欲查詢哪種天氣資訊？", c_id, m_id, reply_markup=build_weather_type_keyboard(city, sign, gender))

        elif data.startswith("weather_final:"):
            _, t_type, city, sign, gender = data.split(":")
            prompt = f"分析「{city}」的天氣 ({t_type})。以占星師口吻為「{gender}性{sign}」提供生活建議。"
            await bot.answer_callback_query(call.id, text="正在調取數據...")
            await execute_and_save(u_id, bot, call.message, prompt)

        elif data.startswith("astro_"):
            action, sign, gender = data.split(":")
            await bot.edit_message_text("🔮 請選擇時間維度：", c_id, m_id, reply_markup=build_time_picker_keyboard(action, sign, gender))

        elif data.startswith("time_select:"):
            _, t_frame, action, sign, gender = data.split(":")
            prompt = f"請詳細分析「{gender}性{sign}」在「{t_frame}」的「{action}」狀況。"
            await bot.answer_callback_query(call.id, text="命運解析中...")
            await execute_and_save(u_id, bot, call.message, prompt)

        elif data == "nav_back_to_zodiac":
            await bot.edit_message_text("請選擇您的 **星座**：", c_id, m_id, reply_markup=build_zodiac_keyboard())

        elif data == "nav_model":
            await send_model_picker(call.message, bot)

    except Exception:
        traceback.print_exc()

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

# --- 消息處理 ---

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
    caption = message.caption or "這張照片是什麼內容？"
    
    await save_turn(message.from_user.id, f"[圖片對話] {caption}", "")
    response = await gemini.gemini_stream(bot, message, [image, caption])
    if response:
        await save_turn(message.from_user.id, f"[圖片對話] {caption}", response)

async def ensure_authorized(message: Message, bot: TeleBot) -> bool:
    # 預留權限校驗接口
    return True
