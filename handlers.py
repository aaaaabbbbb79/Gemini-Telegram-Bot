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

# --- 基礎配置 ---
MODEL_CALLBACK_PREFIX   = "model:"
ACCESS_CALLBACK_PREFIX  = "access:"

# --- 1. 鍵盤生成器：星座與通用部分 ---

def build_zodiac_keyboard(prefix="select_sign", extra_data=""):
    """生成 12 星座選擇器"""
    zodiacs = ["牡羊座", "金牛座", "雙子座", "巨蟹座", "獅子座", "處女座", "天秤座", "天蠍座", "射手座", "摩羯座", "水瓶座", "雙魚座"]
    markup = InlineKeyboardMarkup(row_width=3)
    btns = []
    for z in zodiacs:
        cb_data = f"{prefix}:{z}"
        if extra_data: 
            cb_data += f":{extra_data}"
        btns.append(InlineKeyboardButton(z, callback_data=cb_data))
    markup.add(*btns)
    return markup

def build_gender_keyboard(sign, prefix="set_gender", extra_data=""):
    """生成性別選擇器"""
    markup = InlineKeyboardMarkup(row_width=2)
    for g in ["男", "女"]:
        cb_data = f"{prefix}:{sign}:{g}"
        if extra_data: 
            cb_data += f":{extra_data}"
        markup.add(InlineKeyboardButton(g, callback_data=cb_data))
    return markup

def build_feature_keyboard(sign, gender):
    """主功能選單"""
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
    """占星專用時間選擇器"""
    markup = InlineKeyboardMarkup(row_width=2)
    suffix = f"{action}:{sign}:{gender}:{p_sign}:{p_gender}"
    btns = [
        InlineKeyboardButton("📅 當日", callback_data=f"time_select:day:{suffix}"),
        InlineKeyboardButton("📅 當月", callback_data=f"time_select:month:{suffix}"),
        InlineKeyboardButton("📅 當年", callback_data=f"time_select:year:{suffix}"),
        InlineKeyboardButton("♾️ 此生", callback_data=f"time_select:life:{suffix}")
    ]
    markup.add(*btns)
    if p_sign:
        markup.add(InlineKeyboardButton("🔙 返回選對象性別", callback_data=f"match_p_sign:{p_sign}:{sign}:{gender}"))
    else:
        markup.add(InlineKeyboardButton("🔙 返回功能清單", callback_data=f"set_gender:{sign}:{gender}"))
    return markup

# --- 2. 鍵盤生成器：天氣專用 ---

def build_weather_country_keyboard(sign, gender):
    """天氣第一層：選擇區域"""
    markup = InlineKeyboardMarkup(row_width=2)
    regions = [("🇹🇼 台灣", "Taiwan"), ("🇨🇳 中國", "China"), ("🇯🇵 日本", "Japan")]
    for name, code in regions:
        markup.add(InlineKeyboardButton(name, callback_data=f"weather_city:{code}:{sign}:{gender}"))
    markup.add(InlineKeyboardButton("🔙 返回功能清單", callback_data=f"set_gender:{sign}:{gender}"))
    return markup

def build_weather_city_keyboard(country_code, sign, gender):
    """天氣第二層：選擇城市"""
    markup = InlineKeyboardMarkup(row_width=3)
    cities = []
    if country_code == "Taiwan":
        cities = [("台北", "Taipei"), ("台中", "Taichung"), ("高雄", "Kaohsiung"), ("桃園", "Taoyuan"), ("台南", "Tainan")]
    elif country_code == "China":
        cities = [("菏澤", "Heze"), ("濟南", "Jinan"), ("青島", "Qingdao"), ("北京", "Beijing"), ("上海", "Shanghai")]
    else:
        cities = [("東京", "Tokyo"), ("大阪", "Osaka"), ("首爾", "Seoul")]
    
    for name, code in cities:
        markup.add(InlineKeyboardButton(name, callback_data=f"weather_type:{code}:{sign}:{gender}"))
    markup.add(InlineKeyboardButton("🔙 返回選國家", callback_data=f"weather_country:{sign}:{gender}"))
    return markup

def build_weather_type_keyboard(city_code, sign, gender):
    """天氣第三層：三種維度選擇"""
    markup = InlineKeyboardMarkup(row_width=1)
    markup.add(
        InlineKeyboardButton("🌈 本日整體天氣 (精簡)", callback_data=f"weather_final:today:{city_code}:{sign}:{gender}"),
        InlineKeyboardButton("⏰ 每小時詳細預報", callback_data=f"weather_final:hourly:{city_code}:{sign}:{gender}"),
        InlineKeyboardButton("📅 本週天氣趨勢", callback_data=f"weather_final:weekly:{city_code}:{sign}:{gender}")
    )
    markup.add(InlineKeyboardButton("🔙 返回選城市", callback_data=f"weather_country:{sign}:{gender}"))
    return markup

# --- 3. 核心邏輯處理 ---

async def astrology_callback(call: CallbackQuery, bot: TeleBot) -> None:
    try:
        data = call.data
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
            await bot.edit_message_text("☀️ **天氣預報查詢**\n\n請選擇您想查詢的區域：", chat_id=chat_id, message_id=msg_id, reply_markup=build_weather_country_keyboard(sign, gender), parse_mode="MarkdownV2")

        elif data.startswith("weather_city:"):
            _, country, sign, gender = data.split(":")
            await bot.edit_message_text(f"📍 **正在定位區域**\n\n請選擇城市：", chat_id=chat_id, message_id=msg_id, reply_markup=build_weather_city_keyboard(country, sign, gender), parse_mode="MarkdownV2")

        elif data.startswith("weather_type:"):
            _, city, sign, gender = data.split(":")
            await bot.edit_message_text(f"🌦️ **城市：{city}**\n\n您想查看哪種天氣資訊？", chat_id=chat_id, message_id=msg_id, reply_markup=build_weather_type_keyboard(city, sign, gender), parse_mode="MarkdownV2")

        elif data.startswith("weather_final:"):
            _, t_type, city, sign, gender = data.split(":")
            t_name = {"today": "本日整體", "hourly": "逐小時", "weekly": "本週"}.get(t_type, "天氣預報")
            await bot.answer_callback_query(call.id, text=f"正在調取 {city} 的 {t_name} 數據...")
            
            weather_prompts = {
                "today": f"請查詢並分析「{city}」的今日整體天氣概況。以暖心占星師口吻，為「{gender}性{sign}」提供穿搭與出門建議。",
                "hourly": f"請詳細列出「{city}」今天每小時的天氣變化。針對「{gender}性{sign}」給予精確降雨與溫差提醒。",
                "weekly": f"請分析「{city}」未來一週天氣走向。為「{gender}性{sign}」提供生活規劃建議。"
            }
            user_prompt = weather_prompts.get(t_type, f"查詢{city}天氣")
            await gemini.gemini_stream(bot, call.message, user_prompt)

        elif data.startswith("astro_"):
            parts = data.split(":")
            action, sign, gender = parts[0], parts[1], parts[2]
            action_names = {"astro_daily": "今日星象", "astro_lucky": "幸運指南", "astro_advice": "星象建議", "astro_stress": "舒壓療癒", "astro_motivation": "動力激勵", "astro_reflection": "心靈練習"}
            display_name = action_names.get(action, "星象功能")
            text = f"🔮 **{sign}({gender}) - {display_name}**\n\n請選擇時間維度："
            await bot.edit_message_text(escape(text), chat_id=chat_id, message_id=msg_id, reply_markup=build_time_picker_keyboard(action, sign, gender), parse_mode="MarkdownV2")

        elif data.startswith("match_start:"):
            _, my_sign, my_gender = data.split(":")
            text = f"❤️ **星座配對**\n\n您的設定：{my_sign}({my_gender})\n\n請選擇 **對方的星座**："
            await bot.edit_message_text(escape(text), chat_id=chat_id, message_id=msg_id, reply_markup=build_zodiac_keyboard("match_p_sign", f"{my_sign}:{my_gender}"), parse_mode="MarkdownV2")

        elif data.startswith("match_p_sign:"):
            _, p_sign, my_sign, my_gender = data.split(":")
            text = f"❤️ **星座配對**\n\n您：{my_sign}({my_gender})\n對象：{p_sign}\n\n請選擇 **對方的性別**："
            await bot.edit_message_text(escape(text), chat_id=chat_id, message_id=msg_id, reply_markup=build_gender_keyboard(p_sign, "match_p_gender", f"{my_sign}:{my_gender}"), parse_mode="MarkdownV2")

        elif data.startswith("match_p_gender:"):
            _, p_sign, p_gender, my_sign, my_gender = data.split(":")
            text = f"❤️ **星座配對**\n\n您：{my_sign}({my_gender})\n對象：{p_sign}({p_gender})\n\n選擇配對時效："
            await bot.edit_message_text(escape(text), chat_id=chat_id, message_id=msg_id, reply_markup=build_time_picker_keyboard("match_final", my_sign, my_gender, p_sign, p_gender), parse_mode="MarkdownV2")

        elif data.startswith("time_select:"):
            parts = data.split(":")
            _, time_frame, action, my_sign, my_gender, p_sign, p_gender = parts
            time_map = {"day": "今日", "month": "本月", "year": "今年", "life": "此生/本命"}
            t_text = time_map.get(time_frame, "今日")

            if action == "match_final":
                if time_frame == "life":
                    user_prompt = f"分析「{my_gender}性{my_sign}」與「{p_gender}性{p_sign}」這輩子的「宿命緣分」。包含長期相處課題與建議。"
                else:
                    user_prompt = f"分析「{my_gender}性{my_sign}」與「{p_gender}性{p_sign}」在「{t_text}」的星座配對運勢。"
            else:
                if time_frame == "life":
                    prompts = {
                        "astro_daily": f"解析「{my_gender}性{my_sign}」這輩子的「生命核心目標」與命運基調。",
                        "astro_lucky": f"解析「{my_gender}性{my_sign}」此生的「貴人格局」與長期幸運特質。",
                        "astro_advice": f"針對「{my_gender}性{my_sign}」這輩子在「事業、財富與感情」的大方向建議。",
                        "astro_stress": f"解析「{my_gender}性{my_sign}」這輩子最容易遇到的「心靈坎坷」與化解之道。",
                        "astro_motivation": f"什麼樣的人生願景能激發「{my_gender}性{my_sign}」一輩子的行動力？",
                        "astro_reflection": f"給「{my_gender}性{my_sign}」一個這輩子值得探索的「生命命題」。"
                    }
                else:
                    prompts = {
                        "astro_daily": f"分析「{my_gender}性{my_sign}」在「{t_text}」的整體星象走勢。",
                        "astro_lucky": f"為「{my_gender}性{my_sign}」提供在「{t_text}」的幸運指南。",
                        "astro_advice": f"針對「{my_gender}性{my_sign}」在「{t_text}」的具體行動建議。"
                    }
                user_prompt = prompts.get(action, f"解析{my_sign}運勢")

            await bot.answer_callback_query(call.id, text=f"正在啟動 {t_text} 的能量解析...")
            response = await gemini.gemini_stream(bot, call.message, user_prompt)
            if response: await save_turn(call.from_user.id, user_prompt, response)

        elif data == "nav_back_to_zodiac":
            await bot.edit_message_text("✨ **請選擇您的星座**：", chat_id=chat_id, message_id=msg_id, reply_markup=build_zodiac_keyboard("select_sign"), parse_mode="MarkdownV2")
        
        elif data == "nav_model":
            await send_model_picker(call.message, bot)

    except Exception:
        traceback.print_exc()

# --- 4. 其餘不變之 Handler ---

async def start(message: Message, bot: TeleBot) -> None:
    welcome_text = "✨ **奕川的解毒劑 - 命運導航系統** ✨\n\n請先點選您的 **星座** 開始探索："
    await bot.reply_to(message, escape(welcome_text), reply_markup=build_zodiac_keyboard("select_sign"), parse_mode="MarkdownV2")

async def send_model_picker(message: Message, bot: TeleBot) -> None:
    models = await list_available_models()
    markup = InlineKeyboardMarkup()
    for i, m in enumerate(models):
        markup.add(InlineKeyboardButton(m, callback_data=f"{MODEL_CALLBACK_PREFIX}{i}"))
    await bot.send_message(message.chat.id, "請選擇欲使用的 AI 模型：", reply_markup=markup)
