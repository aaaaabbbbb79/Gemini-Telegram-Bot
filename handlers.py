import traceback
import io
import asyncio
import datetime  # 新增：用於解決 2026 年時間認知問題
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

# 定義功能名稱映射
ACTION_NAMES = {
    "astro_daily": "今日星象", 
    "astro_lucky": "幸運指南", 
    "astro_advice": "星象建議", 
    "astro_stress": "舒壓療癒", 
    "astro_motivation": "動力激勵", 
    "astro_reflection": "心靈練習",
    "match_final": "星座配對"
}

# --- 核心邏輯：執行並同步存檔以維持記憶 (修正防阻塞邏輯) ---

async def execute_and_save(user_id, bot, message, prompt, image=None, virtual_prompt=None):
    # 1. 獲取當前真實時間並注入 Prompt 前綴
    current_time_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    time_injection = f"[System Time: {current_time_str}]\n"
    
    # 2. 處理預存紀錄
    display_prompt = virtual_prompt if virtual_prompt else prompt
    save_prompt = display_prompt if not image else f"[多媒體對話] {display_prompt}"
    
    # 【關鍵修正】：使用 create_task 丟到背景執行，不要在這裡 await 卡死 Telegram 的回應
    asyncio.create_task(save_turn(user_id, save_prompt, ""))

    # 3. 組合發送給 Gemini 的最終內容
    final_prompt = time_injection + prompt
    contents = [image, final_prompt] if image else final_prompt

    try:
        # 4. 呼叫 gemini_stream (gemini.py 中我們已經加上了 429 防護)
        response = await gemini.gemini_stream(bot, message, contents)
    except Exception as e:
        print(f"Gemini 處理出錯: {e}")
        response = None

    # 5. 存入 AI 的回答，確保對話鏈完整 (同樣丟背景)
    if response:
        asyncio.create_task(save_turn(user_id, save_prompt, response))
        
    return response

# --- 輔助函數：鍵盤生成器 (保持原樣，確保功能不減少) ---

def build_zodiac_keyboard(prefix="select_sign", extra_data=""):
    zodiacs = ["牡羊座", "金牛座", "雙子座", "巨蟹座", "獅子座", "處女座", "天秤座", "天蠍座", "射手座", "摩羯座", "水瓶座", "雙魚座"]
    markup = InlineKeyboardMarkup(row_width=3)
    btns = []
    for z in zodiacs:
        cb_data = f"{prefix}:{z}"
        if extra_data: cb_data += f":{extra_data}"
        btns.append(InlineKeyboardButton(z, callback_data=cb_data))
    markup.add(*btns)
    return markup

def build_gender_keyboard(sign, prefix="set_gender", extra_data=""):
    markup = InlineKeyboardMarkup(row_width=2)
    for g in ["男", "女"]:
        cb_data = f"{prefix}:{sign}:{g}"
        if extra_data: cb_data += f":{extra_data}"
        markup.add(InlineKeyboardButton(g, callback_data=cb_data))
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
        InlineKeyboardButton("🤖 切換模型", callback_data="nav_model"),
        InlineKeyboardButton("🔙 返回選星座", callback_data="nav_back_to_zodiac")
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
        InlineKeyboardButton("📅 此生", callback_data=f"time_select:life:{suffix}")
    ]
    markup.add(*btns)
    if p_sign:
        markup.add(InlineKeyboardButton("🔙 返回選對象性別", callback_data=f"match_p_sign:{p_sign}:{sign}:{gender}"))
    else:
        markup.add(InlineKeyboardButton("🔙 返回功能清單", callback_data=f"set_gender:{sign}:{gender}"))
    return markup

def build_weather_country_keyboard(sign, gender):
    markup = InlineKeyboardMarkup(row_width=2)
    countries = [
        ("🇹🇼 台灣", "Taiwan"), ("🇨🇳 中國", "China"), ("🇯🇵 日本", "Japan"),
        ("🇸🇬 新加坡", "Singapore"), ("🇲🇾 馬來西亞", "Malaysia"), ("🇹🇭 泰國", "Thailand"),
        ("🇻🇳 越南", "Vietnam"), ("🇵🇭 菲律賓", "Philippines")
    ]
    for name, code in countries:
        markup.add(InlineKeyboardButton(name, callback_data=f"weather_city:{code}:{sign}:{gender}"))
    markup.add(InlineKeyboardButton("🔙 返回功能清單", callback_data=f"set_gender:{sign}:{gender}"))
    return markup

def build_weather_city_keyboard(country_code, sign, gender):
    markup = InlineKeyboardMarkup(row_width=3)
    city_db = {
        "Taiwan": [("基隆", "Keelung"), ("台北", "Taipei"), ("新北", "NewTaipei"), ("桃園", "Taoyuan"), ("新竹市", "HsinchuCity"), ("新竹縣", "HsinchuCounty"), ("苗栗", "Miaoli"), ("台中", "Taichung"), ("彰化", "Changhua"), ("南投", "Nantou"), ("雲林", "Yunlin"), ("嘉義市", "ChiayiCity"), ("嘉義縣", "ChiayiCounty"), ("台南", "Tainan"), ("高雄", "Kaohsiung"), ("屏東", "Pingtung"), ("宜蘭", "Yilan"), ("花蓮", "Hualien"), ("台東", "Taitung"), ("澎湖", "Penghu"), ("金門", "Kinmen"), ("連江", "Lienchiang")],
        "China": [("菏澤", "Heze"), ("濟南", "Jinan"), ("青島", "Qingdao"), ("北京", "Beijing"), ("上海", "Shanghai"), ("廣州", "Guangzhou"), ("深圳", "Shenzhen"), ("杭州", "Hangzhou")],
        "Japan": [("東京", "Tokyo"), ("大阪", "Osaka"), ("京都", "Kyoto"), ("福岡", "Fukuoka"), ("沖繩", "Okinawa"), ("札幌", "Sapporo")],
        "Singapore": [("新加坡", "Singapore")],
        "Malaysia": [("吉隆坡", "KualaLumpur"), ("檳城", "Penang"), ("新山", "JohorBahru"), ("馬六甲", "Malacca")],
        "Thailand": [("曼谷", "Bangkok"), ("清邁", "ChiangMai"), ("普吉島", "Phuket"), ("蘇梅島", "KohSamui")],
        "Vietnam": [("胡志明市", "HCMC"), ("河內", "Hanoi"), ("峴港", "DaNang"), ("芽莊", "NhaTrang")],
        "Philippines": [("馬尼拉", "Manila"), ("宿霧", "Cebu"), ("長灘島", "Boracay"), ("巴拉望", "Palawan")]
    }
    cities = city_db.get(country_code, [("未知城市", "Unknown")])
    for name, code in cities:
        markup.add(InlineKeyboardButton(name, callback_data=f"weather_type:{code}:{sign}:{gender}"))
    markup.add(InlineKeyboardButton("🔙 返回選國家", callback_data=f"weather_country:{sign}:{gender}"))
    return markup

def build_weather_type_keyboard(city_code, sign, gender):
    markup = InlineKeyboardMarkup(row_width=1)
    markup.add(
        InlineKeyboardButton("🌈 本日整體天氣 (精簡)", callback_data=f"weather_final:today:{city_code}:{sign}:{gender}"),
        InlineKeyboardButton("⏰ 每小時詳細預報", callback_data=f"weather_final:hourly:{city_code}:{sign}:{gender}"),
        InlineKeyboardButton("📅 本週天氣趨勢", callback_data=f"weather_final:weekly:{city_code}:{sign}:{gender}")
    )
    markup.add(InlineKeyboardButton("🔙 返回選國家", callback_data=f"weather_country:{sign}:{gender}"))
    return markup

# --- 權限與管理邏輯 ---

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

# --- 指令 Handlers ---

async def start(message: Message, bot: TeleBot) -> None:
    try:
        welcome_text = "✨ **奕川的解毒劑 - 命運導航** ✨\n\n請先點選您的 **星座** 開始探索："
        await bot.reply_to(message, escape(welcome_text), reply_markup=build_zodiac_keyboard("select_sign"), parse_mode="MarkdownV2")
    except Exception:
        traceback.print_exc()

async def astrology_callback(call: CallbackQuery, bot: TeleBot) -> None:
    # 【關鍵修正】：不管發生什麼事，先回報 Telegram「我收到點擊了」，斷絕瘋狂重傳！
    try:
        await bot.answer_callback_query(call.id)
    except Exception:
        pass # 忽略已回報或超時的錯誤

    try:
        data = call.data
        u_id = call.from_user.id
        chat_id = call.message.chat.id
        msg_id = call.message.message_id

        if data.startswith("select_sign:"):
            sign = data.split(":")[1]
            await bot.edit_message_text(f"好的，你是 **{sign}**。那請教你的性別是？", chat_id=chat_id, message_id=msg_id, reply_markup=build_gender_keyboard(sign, "set_gender"), parse_mode="MarkdownV2")
        
        elif data.startswith("set_gender:"):
            _, sign, gender = data.split(":")
            text = f"✨ **{sign} ({gender})** 的專屬解毒劑選單 ✨\n\n請選擇您感興趣的項目："
            await bot.edit_message_text(escape(text), chat_id=chat_id, message_id=msg_id, reply_markup=build_feature_keyboard(sign, gender), parse_mode="MarkdownV2")

        elif data.startswith("weather_country:"):
            _, sign, gender = data.split(":")
            await bot.edit_message_text("☀️ **天氣預報服務**\n\n請選擇您所在的區域：", chat_id=chat_id, message_id=msg_id
