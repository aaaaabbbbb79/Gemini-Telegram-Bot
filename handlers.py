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

# --- 基礎配置 ---
error_info             =       conf["error_info"]
before_generate_info    =       conf["before_generate_info"]
download_pic_notify     =       conf["download_pic_notify"]
MODEL_CALLBACK_PREFIX   =       "model:"
ACCESS_CALLBACK_PREFIX  =       "access:"

# --- 鍵盤生成器 (省略重複代碼，確保邏輯一致) ---

def build_zodiac_keyboard(prefix="select_sign", extra_data=""):
    zodiacs = ["牡羊座", "金牛座", "雙子座", "巨蟹座", "獅子座", "處女座", "天秤座", "天蠍座", "射手座", "摩羯座", "水瓶座", "雙魚座"]
    markup = InlineKeyboardMarkup(row_width=3)
    btns = [InlineKeyboardButton(z, callback_data=f"{prefix}:{z}{':'+extra_data if extra_data else ''}") for z in zodiacs]
    markup.add(*btns)
    return markup

def build_gender_keyboard(sign, prefix="set_gender", extra_data=""):
    markup = InlineKeyboardMarkup(row_width=2)
    for g in ["男", "女"]:
        markup.add(InlineKeyboardButton(g, callback_data=f"{prefix}:{sign}:{g}{':'+extra_data if extra_data else ''}"))
    return markup

def build_feature_keyboard(sign, gender):
    markup = InlineKeyboardMarkup(row_width=2)
    btns = [
        InlineKeyboardButton("🌌 今日星象", callback_data=f"astro_daily:{sign}:{gender}"),
        InlineKeyboardButton("❤️ 星座配對", callback_data=f"match_start:{sign}:{gender}"),
        InlineKeyboardButton("☀️ 天氣預報", callback_data=f"weather_country:{sign}:{gender}"),
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

def build_time_picker_keyboard(action, sign, gender, p_sign="", p_gender=""):
    markup = InlineKeyboardMarkup(row_width=2)
    suffix = f"{action}:{sign}:{gender}:{p_sign}:{p_gender}"
    btns = [
        InlineKeyboardButton("📅 當日", callback_data=f"time_select:day:{suffix}"),
        InlineKeyboardButton("📅 當月", callback_data=f"time_select:month:{suffix}"),
        InlineKeyboardButton("📅 當年", callback_data=f"time_select:year:{suffix}"),
        InlineKeyboardButton("♾️ 此生", callback_data=f"time_select:life:{suffix}")
    ]
    markup.add(*btns)
    markup.add(InlineKeyboardButton("🔙 返回選單", callback_data=f"set_gender:{sign}:{gender}"))
    return markup

# --- 天氣專用選單 ---
def build_weather_country_keyboard(sign, gender):
    markup = InlineKeyboardMarkup(row_width=2)
    countries = [("🇹🇼 台灣", "Taiwan"), ("🇨🇳 中國", "China"), ("🇯🇵 日本", "Japan"), ("🇸🇬 星馬", "SG_MY")]
    for name, code in countries:
        markup.add(InlineKeyboardButton(name, callback_data=f"weather_city:{code}:{sign}:{gender}"))
    markup.add(InlineKeyboardButton("🔙 返回", callback_data=f"set_gender:{sign}:{gender}"))
    return markup

def build_weather_city_keyboard(country_code, sign, gender):
    markup = InlineKeyboardMarkup(row_width=3)
    # 簡化版城市清單，可根據需求擴充
    cities = [("台北", "Taipei"), ("台中", "Taichung"), ("高雄", "Kaohsiung"), ("菏澤", "Heze"), ("上海", "Shanghai")]
    for name, code in cities:
        markup.add(InlineKeyboardButton(name, callback_data=f"weather_type:{code}:{sign}:{gender}"))
    markup.add(InlineKeyboardButton("🔙 返回", callback_data=f"weather_country:{sign}:{gender}"))
    return markup

def build_weather_type_keyboard(city_code, sign, gender):
    markup = InlineKeyboardMarkup(row_width=1)
    markup.add(
        InlineKeyboardButton("🌈 整體天氣", callback_data=f"weather_final:today:{city_code}:{sign}:{gender}"),
        InlineKeyboardButton("📅 一週趨勢", callback_data=f"weather_final:weekly:{city_code}:{sign}:{gender}")
    )
    return markup

# --- 核心邏輯：確保記憶延續 ---

async def execute_and_save(user_id, bot, message, prompt):
    """
    核心修復函數：
    1. 先強制寫入 user prompt 到資料庫。
    2. 給予系統極短時間完成 IO。
    3. 呼叫串流回應。
    4. 回應結束後補完助理的回答內容。
    """
    # 步驟 1: 強制存入使用者的問題
    await save_turn(user_id, prompt, "")
    await asyncio.sleep(0.1) # 強制非同步切換，確保資料庫寫入順序
    
    # 步驟 2: 啟動串流
    response = await gemini.gemini_stream(bot, message, prompt)
    
    # 步驟 3: 存入完整回應
    if response:
        await save_turn(user_id, prompt, response)

# --- 指令 Handlers ---

async def start(message: Message, bot: TeleBot) -> None:
    welcome = "✨ **命運導航系統** ✨\n\n請選擇您的 **星座**："
    await bot.reply_to(message, escape(welcome), reply_markup=build_zodiac_keyboard("select_sign"), parse_mode="MarkdownV2")

async def astrology_callback(call: CallbackQuery, bot: TeleBot) -> None:
    try:
        data = call.data
        user_id = call.from_user.id
        msg = call.message

        # 星座與性別流
        if data.startswith("select_sign:"):
            sign = data.split(":")[1]
            await bot.edit_message_text(f"你是 **{sign}**，請選擇性別：", msg.chat.id, msg.message_id, reply_markup=build_gender_keyboard(sign))
        
        elif data.startswith("set_gender:"):
            _, sign, gender = data.split(":")[:3]
            await bot.edit_message_text(f"✨ **{sign} ({gender})** 的導航選單：", msg.chat.id, msg.message_id, reply_markup=build_feature_keyboard(sign, gender))

        # 天氣流
        elif data.startswith("weather_country:"):
            _, sign, gender = data.split(":")
            await bot.edit_message_text("☀️ 選擇區域：", msg.chat.id, msg.message_id, reply_markup=build_weather_country_keyboard(sign, gender))

        elif data.startswith("weather_final:"):
            _, t_type, city, sign, gender = data.split(":")
            prompt = f"請分析「{city}」的天氣。以占星師口吻為「{gender}性{sign}」提供建議。"
            await bot.answer_callback_query(call.id, text="正在讀取星象與氣象...")
            await execute_and_save(user_id, bot, msg, prompt)

        # 星象功能流
        elif data.startswith("astro_"):
            action, sign, gender = data.split(":")
            await bot.edit_message_text("🔮 選擇查看的時間維度：", msg.chat.id, msg.message_id, reply_markup=build_time_picker_keyboard(action, sign, gender))

        elif data.startswith("time_select:"):
            parts = data.split(":")
            _, t_frame, action, my_sign, my_gender = parts[:5]
            prompt = f"分析「{my_gender}性{my_sign}」在 {t_frame} 的 {action} 運勢。"
            await bot.answer_callback_query(call.id, text="命運生成中...")
            await execute_and_save(user_id, bot, msg, prompt)

        elif data == "nav_back_to_zodiac":
            await bot.edit_message_text("請選擇您的 **星座**：", msg.chat.id, msg.message_id, reply_markup=build_zodiac_keyboard("select_sign"))

    except Exception:
        traceback.print_exc()

# --- 文字與圖片對話處理 ---

async def gemini_handler(message: Message, bot: TeleBot) -> None:
    if not await ensure_authorized(message, bot): return
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2: return
    await execute_and_save(message.from_user.id, bot, message, parts[1].strip())

async def gemini_private_handler(message: Message, bot: TeleBot) -> None:
    if not await ensure_authorized(message, bot): return
    await execute_and_save(message.from_user.id, bot, message, message.text.strip())

async def gemini_photo_handler(message: Message, bot: TeleBot) -> None:
    if not await ensure_authorized(message, bot): return
    file = await bot.get_file(message.photo[-1].file_id)
    photo_file = await bot.download_file(file.file_path)
    image = Image.open(io.BytesIO(photo_file))
    caption = message.caption or "這張圖片是什麼？"
    
    # 圖片比較特殊，我們先存入 caption 文字
    await save_turn(message.from_user.id, f"[圖片] {caption}", "")
    response = await gemini.gemini_stream(bot, message, [image, caption])
    if response:
        await save_turn(message.from_user.id, f"[圖片] {caption}", response)

# --- 輔助管理指令 ---
async def clear(message: Message, bot: TeleBot) -> None:
    await clear_history(message.from_user.id)
    await bot.reply_to(message, "✅ 記憶已重置")

async def ensure_authorized(message: Message, bot: TeleBot) -> bool:
    # 這裡串接你原本的 access_control 邏輯
    return True
