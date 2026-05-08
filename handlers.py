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

error_info              =       conf["error_info"]
before_generate_info    =       conf["before_generate_info"]
download_pic_notify     =       conf["download_pic_notify"]
MODEL_CALLBACK_PREFIX   =       "model:"
ACCESS_CALLBACK_PREFIX  =       "access:"

# --- 核心邏輯：執行並同步存檔以維持 30 分鐘後的記憶 ---

async def execute_and_save(user_id, bot, message, prompt, image=None):
    # 1. 預存 Prompt
    save_prompt = prompt if not image else f"[多媒體對話] {prompt}"
    await save_turn(user_id, save_prompt, "")
    
    await asyncio.sleep(0.1)

    # 2. 呼叫 Gemini 生成
    content = [image, prompt] if image else prompt
    
    # --- 注意！這裡的 try 必須縮排在函數內部 ---
    try:
        # 第 49 行：必須比 try 再多縮排 4 個空格！
        response = await gemini.gemini_stream(bot, message, content)
    except Exception as e:
        # except 必須跟 try 垂直對齊
        print(f"Gemini 處理出錯: {e}")
        response = "抱歉，分析時發生超時或錯誤，請嘗試傳送較短的內容。"

    # 3. 存入 AI 的回答 (這兩行也要對齊函數內部)
    if response:
        await save_turn(user_id, save_prompt, response)
        
    return response

# --- 輔助函數：鍵盤生成器 ---

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
            await bot.edit_message_text("☀️ **天氣預報服務**\n\n請選擇您所在的區域：", chat_id=chat_id, message_id=msg_id, reply_markup=build_weather_country_keyboard(sign, gender), parse_mode="MarkdownV2")

        elif data.startswith("weather_city:"):
            _, country, sign, gender = data.split(":")
            await bot.edit_message_text(f"📍 **區域：{country}**\n\n請選擇查詢城市：", chat_id=chat_id, message_id=msg_id, reply_markup=build_weather_city_keyboard(country, sign, gender), parse_mode="MarkdownV2")

        elif data.startswith("weather_type:"):
            _, city, sign, gender = data.split(":")
            await bot.edit_message_text(f"🌦️ **城市：{city}**\n\n您想查看哪種天氣資訊？", chat_id=chat_id, message_id=msg_id, reply_markup=build_weather_type_keyboard(city, sign, gender), parse_mode="MarkdownV2")

        elif data.startswith("weather_final:"):
            _, t_type, city, sign, gender = data.split(":")
            t_name = {"today": "本日整體", "hourly": "每小時預報", "weekly": "本週趨勢"}.get(t_type, "預報")
            await bot.answer_callback_query(call.id, text=f"正在調取 {city} 的 {t_name}...")
            
            weather_prompts = {
                "today": f"請查詢並解析「{city}」的今日整體天氣。以貼心占星師的口吻，為「{gender}性{sign}」提供穿搭建議與心情小語。",
                "hourly": f"請詳細列出「{city}」今天逐小時的天氣變化。針對「{gender}性{sign}」的活動規律，給予準確的降雨與溫差提醒。",
                "weekly": f"請分析「{city}」未來一週的天氣趨勢。為「{gender}性{sign}」規劃這週最適合外出的日子。"
            }
            await execute_and_save(u_id, bot, call.message, weather_prompts.get(t_type, f"查詢{city}天氣"))

        elif data.startswith("astro_"):
            parts = data.split(":")
            action, sign, gender = parts[0], parts[1], parts[2]
            action_names = {"astro_daily": "今日星象", "astro_lucky": "幸運指南", "astro_advice": "星象建議", "astro_stress": "舒壓療癒", "astro_motivation": "動力激勵", "astro_reflection": "心靈練習"}
            display_name = action_names.get(action, "星象功能")
            text = f"🔮 **{sign}({gender}) - {display_name}**\n\n請選擇您想查看的時間維度："
            await bot.edit_message_text(escape(text), chat_id=chat_id, message_id=msg_id, reply_markup=build_time_picker_keyboard(action, sign, gender), parse_mode="MarkdownV2")

        elif data.startswith("match_start:"):
            _, my_sign, my_gender = data.split(":")
            text = f"❤️ **星座配對**\n\n您的設定：{my_sign}({my_gender})\n\n請選擇 **對方的星座**："
            await bot.edit_message_text(escape(text), chat_id=chat_id, message_id=msg_id, reply_markup=build_zodiac_keyboard("match_p_sign", f"{my_sign}:{my_gender}"), parse_mode="MarkdownV2")

        elif data.startswith("match_p_sign:"):
            parts = data.split(":")
            _, p_sign, my_sign, my_gender = parts
            text = f"❤️ **星座配對**\n\n您：{my_sign}({my_gender})\n對象：{p_sign}\n\n請選擇 **對方的性別**："
            await bot.edit_message_text(escape(text), chat_id=chat_id, message_id=msg_id, reply_markup=build_gender_keyboard(p_sign, "match_p_gender", f"{my_sign}:{my_gender}"), parse_mode="MarkdownV2")

        elif data.startswith("match_p_gender:"):
            parts = data.split(":")
            _, p_sign, p_gender, my_sign, my_gender = parts
            text = f"❤️ **星座配對**\n\n您：{my_sign}({my_gender})\n對象：{p_sign}({p_gender})\n\n請選擇想查看的配對時效："
            await bot.edit_message_text(escape(text), chat_id=chat_id, message_id=msg_id, reply_markup=build_time_picker_keyboard("match_final", my_sign, my_gender, p_sign, p_gender), parse_mode="MarkdownV2")

        elif data.startswith("time_select:"):
            parts = data.split(":")
            _, time_frame, action, my_sign, my_gender, p_sign, p_gender = parts
            time_map = {"day": "今日/當天", "month": "本月/當月", "year": "今年/年度", "life": "這輩子/此生/本命"}
            t_text = time_map.get(time_frame, "今日")

            if action == "match_final":
                if time_frame == "life":
                    user_prompt = f"請深入分析一個「{my_gender}性{my_sign}」與一個「{p_gender}性{p_sign}」這輩子的「宿命緣分」。包含長期的相處課題、兩人是否適合共同生活、靈魂契合度以及白頭偕老的關鍵建議。"
                else:
                    user_prompt = f"請詳細分析一個「{my_gender}性{my_sign}」與一個「{p_gender}性{p_sign}」在「{t_text}」的星座配對運勢。包含默契指數、溝通建議以及相處小撇步。"
            else:
                prompts_life = {
                    "astro_daily": f"請從星象格局分析「{my_gender}性{my_sign}」這輩子的「生命核心目標」與命運基調。",
                    "astro_lucky": f"請解析「{my_gender}性{my_sign}」此生的「貴人格局」與能夠帶來長期好運的核心特質。",
                    "astro_advice": f"請針對「{my_gender}性{my_sign}」這輩子在「事業、財富與感情」這三大維度的重要人生建議。",
                    "astro_stress": f"身為「{my_gender}性{my_sign}」這輩子最容易遇到的「心靈坎坷」是什麼？該如何調整來化解？",
                    "astro_motivation": f"什麼樣的人生願景最能激發「{my_gender}性{my_sign}」一輩子的行動力？",
                    "astro_reflection": f"請給「{my_gender}性{my_sign}」一個這輩子都值得持續反思、深刻探索的「生命命題」。"
                }
                prompts_normal = {
                    "astro_daily": f"請分析「{my_gender}性{my_sign}」在「{t_text}」的整體星象走勢與能量變化。",
                    "astro_lucky": f"請為「{my_gender}性{my_sign}」提供在「{t_text}」的幸運指南。",
                    "astro_advice": f"請針對「{my_gender}性{my_sign}」在「{t_text}」的具體建議。",
                    "astro_stress": f"身為「{my_gender}性{my_sign}」，在「{t_text}」壓力大時該如何療癒？",
                    "astro_motivation": f"給予「{my_gender}性{my_sign}」在「{t_text}」的激勵行動指引。",
                    "astro_reflection": f"適合「{my_gender}性{my_sign}」在「{t_text}」進行的心靈反思題目。"
                }
                user_prompt = prompts_life.get(action) if time_frame == "life" else prompts_normal.get(action)

            await bot.answer_callback_query(call.id, text=f"正在啟動 {t_text} 的能量解析...")
            await execute_and_save(u_id, bot, call.message, user_prompt or f"請分析{my_sign}的運勢")

        elif data == "nav_back_to_zodiac":
            await bot.edit_message_text("請選擇您的 **星座**：", chat_id=chat_id, message_id=msg_id, reply_markup=build_zodiac_keyboard("select_sign"), parse_mode="MarkdownV2")
        
        elif data == "nav_model":
            await send_model_picker(call.message, bot)

    except Exception:
        traceback.print_exc()

# --- 指令與其餘邏輯 ---

async def astrology_handler(message: Message, bot: TeleBot) -> None:
    if not await ensure_authorized(message, bot): return
    text = message.text.split()
    if len(text) < 2: return
    sign = text[1]
    prompt = f"分析{sign}的這個月運勢。" if 'horoscope' in text[0] else f"分析{sign}與{text[2] if len(text)>2 else '另一半'}的配對。"
    await execute_and_save(message.from_user.id, bot, message, prompt)

async def access(message: Message, bot: TeleBot) -> None:
    if not is_admin(message.from_user.id): return
    records = await get_approved_access_records()
    if not records:
        await bot.reply_to(message, "No approved access records.")
        return
    for record in records:
        text = f"User: {record['username']} ({record['subject_id']})"
        markup = InlineKeyboardMarkup().add(InlineKeyboardButton("Revoke", callback_data=f"access:revoke:user:{record['subject_id']}"))
        await bot.send_message(message.chat.id, text, reply_markup=markup)

async def accessrequest(message: Message, bot: TeleBot) -> None:
    if not is_admin(message.from_user.id): return
    enabled = not await are_access_requests_enabled()
    await set_access_request_enabled(enabled)
    await bot.reply_to(message, f"Access requests are now {'open' if enabled else 'closed'}.")

async def access_callback(call: CallbackQuery, bot: TeleBot) -> None:
    """處理管理員操作：Revoke, Approve, Reject"""
    parts = call.data.split(":")
    if len(parts) < 4: return
    _, action, subject_type, subject_id = parts
    
    if action == "revoke":
        await revoke_access(subject_type, int(subject_id), call.from_user.id)
    else:
        # 核對決策狀態：'approved' 或 'rejected'
        decision = "approved" if action == "approve" else "rejected"
        await review_access(decision, subject_type, int(subject_id), call.from_user.id)
    
    await bot.answer_callback_query(call.id, text=f"Done")
    await bot.edit_message_text(f"Action {action} processed for {subject_id}", call.message.chat.id, call.message.message_id)

async def gemini_handler(message: Message, bot: TeleBot) -> None:
    if not await ensure_authorized(message, bot): return
    parts = message.text.strip().split(maxsplit=1)
    if len(parts) < 2: return
    await execute_and_save(message.from_user.id, bot, message, parts[1].strip())

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
    await execute_and_save(message.from_user.id, bot, message, message.text.strip())

async def gemini_photo_handler(message: Message, bot: TeleBot) -> None:
    if not await ensure_authorized(message, bot): return
    file = await bot.get_file(message.photo[-1].file_id)
    photo_file = await bot.download_file(file.file_path)
    image = Image.open(io.BytesIO(photo_file))
    await execute_and_save(message.from_user.id, bot, message, message.caption or "分析此圖", image=image)

# --- 新增的影片處理邏輯 ---

async def gemini_video_handler(message: Message, bot: TeleBot) -> None:
    """處理一般影片與影音訊息 (video_note)"""
    if not await ensure_authorized(message, bot): return
    
    # 提醒使用者正在處理 (影片分析較慢)
    sent_msg = await bot.reply_to(message, "🎬 正在接收並分析影片，請稍候...")
    
    try:
        # 取得影片物件 (支援 video 或 video_note)
        video_obj = message.video or message.video_note
        file_info = await bot.get_file(video_obj.file_id)
        video_file = await bot.download_file(file_info.file_path)
        
        # 標註影片類型與數據
        video_part = {"mime_type": "video/mp4", "data": video_file}
        
        # 取得文字描述，若無則提供預設
        caption = message.caption or "請分析這段影片的內容。"
        
        # 執行生成並存檔
        await execute_and_save(message.from_user.id, bot, message, caption, image=video_part)
        
        # 任務完成，刪除提示訊息
        await bot.delete_message(message.chat.id, sent_msg.message_id)
        
    except Exception:
        traceback.print_exc()
        await bot.edit_message_text("❌ 影片分析失敗，請確認影片大小或格式是否正確。", message.chat.id, sent_msg.message_id)

async def send_model_picker(message: Message, bot: TeleBot) -> None:
    models = await list_available_models()
    markup = InlineKeyboardMarkup()
    for i, m in enumerate(models):
        markup.add(InlineKeyboardButton(m, callback_data=f"{MODEL_CALLBACK_PREFIX}{i}"))
    await bot.send_message(message.chat.id, "Select model:", reply_markup=markup)
