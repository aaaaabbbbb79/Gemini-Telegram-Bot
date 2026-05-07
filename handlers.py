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

# --- 輔助函數：鍵盤生成器 ---

def build_zodiac_keyboard(prefix="select_sign", extra_data=""):
    """
    生成星座選擇器。
    prefix: callback 的開頭 (例如 select_sign 或 match_p_sign)
    extra_data: 需要往後傳遞的舊數據 (例如 我的星座:我的性別)
    """
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
    """
    生成性別選擇器。
    prefix: user_gender (選自己) 或 match_p_gender (選對方)
    """
    markup = InlineKeyboardMarkup(row_width=2)
    # 分別為 男、女
    for g in ["男", "女"]:
        cb_data = f"{prefix}:{sign}:{g}"
        if extra_data:
            cb_data += f":{extra_data}"
        markup.add(InlineKeyboardButton(g, callback_data=cb_data))
    return markup

def build_feature_keyboard(sign, gender):
    """主功能選單，包含星座配對按鈕"""
    markup = InlineKeyboardMarkup(row_width=2)
    # 這裡的 callback 會帶上自己的星座和性別
    btns = [
        InlineKeyboardButton("🌌 今日星象", callback_data=f"astro_daily:{sign}:{gender}"),
        InlineKeyboardButton("❤️ 星座配對", callback_data=f"match_start:{sign}:{gender}"),
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
    """
    時間維度選擇器。
    如果是普通功能，只會有 sign/gender；如果是配對，會有 p_sign/p_gender。
    """
    markup = InlineKeyboardMarkup(row_width=3)
    # 組合數據：時間:功能:我星:我性:他星:他性
    suffix = f"{action}:{sign}:{gender}:{p_sign}:{p_gender}"
    btns = [
        InlineKeyboardButton("📅 當日", callback_data=f"time_select:day:{suffix}"),
        InlineKeyboardButton("📅 當月", callback_data=f"time_select:month:{suffix}"),
        InlineKeyboardButton("📅 當年", callback_data=f"time_select:year:{suffix}"),
    ]
    markup.add(*btns)
    # 返回按鈕判斷
    if p_sign:
        markup.add(InlineKeyboardButton("🔙 返回選對象性別", callback_data=f"match_p_sign:{p_sign}:{sign}:{gender}"))
    else:
        markup.add(InlineKeyboardButton("🔙 返回功能清單", callback_data=f"set_gender:{sign}:{gender}"))
    return markup

def build_access_markup(subject_type: str, subject_id: int) -> InlineKeyboardMarkup:
    markup = InlineKeyboardMarkup(row_width=2)
    markup.add(
        InlineKeyboardButton("Approve", callback_data=f"{ACCESS_CALLBACK_PREFIX}approve:{subject_type}:{subject_id}"),
        InlineKeyboardButton("Reject", callback_data=f"{ACCESS_CALLBACK_PREFIX}reject:{subject_type}:{subject_id}"),
    )
    return markup

# --- 權限與管理邏輯 (保持不變) ---

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
        welcome_text = "✨ **奕川的解毒劑 - 占星導航** ✨\n\n請先點選您的 **星座**："
        await bot.reply_to(message, escape(welcome_text), reply_markup=build_zodiac_keyboard("select_sign"), parse_mode="MarkdownV2")
    except Exception:
        traceback.print_exc()

async def astrology_callback(call: CallbackQuery, bot: TeleBot) -> None:
    try:
        data = call.data
        chat_id = call.message.chat.id
        msg_id = call.message.message_id

        # 1. 選擇自己星座後 -> 詢問自己性別
        if data.startswith("select_sign:"):
            sign = data.split(":")[1]
            await bot.edit_message_text(f"好的，你是 **{sign}**。那請教你的性別是？", chat_id=chat_id, message_id=msg_id, reply_markup=build_gender_keyboard(sign, "set_gender"), parse_mode="MarkdownV2")
        
        # 2. 選擇自己性別後 -> 進入主功能選單
        elif data.startswith("set_gender:"):
            _, sign, gender = data.split(":")
            text = f"✨ **{sign} ({gender})** 的專屬解毒劑選單 ✨\n\n請選擇您感興趣的項目："
            await bot.edit_message_text(escape(text), chat_id=chat_id, message_id=msg_id, reply_markup=build_feature_keyboard(sign, gender), parse_mode="MarkdownV2")

        # 3. 點選普通星象功能 -> 詢問時間維度
        elif data.startswith("astro_"):
            parts = data.split(":")
            action, sign, gender = parts[0], parts[1], parts[2]
            action_names = {"astro_daily": "今日星象", "astro_lucky": "幸運指南", "astro_advice": "星象建議", "astro_stress": "舒壓療癒", "astro_motivation": "動力激勵", "astro_reflection": "心靈練習"}
            display_name = action_names.get(action, "星象功能")
            text = f"🔮 **{sign}({gender}) - {display_name}**\n\n請選擇您想查看的時間維度："
            await bot.edit_message_text(escape(text), chat_id=chat_id, message_id=msg_id, reply_markup=build_time_picker_keyboard(action, sign, gender), parse_mode="MarkdownV2")

        # 4. [配對流] 點選星座配對 -> 詢問對象星座
        elif data.startswith("match_start:"):
            _, my_sign, my_gender = data.split(":")
            text = f"❤️ **星座配對**\n\n您的設定：{my_sign}({my_gender})\n\n請選擇 **對方的星座**："
            await bot.edit_message_text(escape(text), chat_id=chat_id, message_id=msg_id, reply_markup=build_zodiac_keyboard("match_p_sign", f"{my_sign}:{my_gender}"), parse_mode="MarkdownV2")

        # 5. [配對流] 選擇對象星座後 -> 詢問對象性別
        elif data.startswith("match_p_sign:"):
            parts = data.split(":") # match_p_sign : p_sign : my_sign : my_gender
            _, p_sign, my_sign, my_gender = parts
            text = f"❤️ **星座配對**\n\n您：{my_sign}({my_gender})\n對象：{p_sign}\n\n請選擇 **對方的性別**："
            await bot.edit_message_text(escape(text), chat_id=chat_id, message_id=msg_id, reply_markup=build_gender_keyboard(p_sign, "match_p_gender", f"{my_sign}:{my_gender}"), parse_mode="MarkdownV2")

        # 6. [配對流] 選擇對象性別後 -> 詢問時間維度
        elif data.startswith("match_p_gender:"):
            parts = data.split(":") # match_p_gender : p_sign : p_gender : my_sign : my_gender
            _, p_sign, p_gender, my_sign, my_gender = parts
            text = f"❤️ **星座配對**\n\n您：{my_sign}({my_gender})\n對象：{p_sign}({p_gender})\n\n請選擇想查看的配對時效："
            await bot.edit_message_text(escape(text), chat_id=chat_id, message_id=msg_id, reply_markup=build_time_picker_keyboard("match_final", my_sign, my_gender, p_sign, p_gender), parse_mode="MarkdownV2")

        # 7. 最終執行：發送 Prompt 給 Gemini
        elif data.startswith("time_select:"):
            # 結構: time_select : timeframe : action : my_sign : my_gender : p_sign : p_gender
            parts = data.split(":")
            _, time_frame, action, my_sign, my_gender, p_sign, p_gender = parts
            
            time_map = {"day": "今日/當天", "month": "本月/當月", "year": "今年/年度"}
            t_text = time_map.get(time_frame, "今日")

            if action == "match_final":
                user_prompt = f"請詳細分析一個「{my_gender}性{my_sign}」與一個「{p_gender}性{p_sign}」在「{t_text}」的星座配對運勢。包含默契指數、溝通建議、可能的摩擦點以及開運相處小撇步。"
            else:
                prompts = {
                    "astro_daily": f"請分析「{my_gender}性{my_sign}」在「{t_text}」的整體星象走勢與能量變化。",
                    "astro_lucky": f"請為「{my_gender}性{my_sign}」提供在「{t_text}」的幸運指南（幸運色、開運物、方位）。",
                    "astro_advice": f"請針對「{my_gender}性{my_sign}」在「{t_text}」的事業、財運與感情給予具體建議。",
                    "astro_stress": f"身為「{my_gender}性{my_sign}」，在「{t_text}」壓力大時該如何透過星象能量療癒？",
                    "astro_motivation": f"給予「{my_gender}性{my_sign}」在「{t_text}」的激勵行動指引。",
                    "astro_reflection": f"適合「{my_gender}性{my_sign}」在「{t_text}」進行的心靈反思題目。"
                }
                user_prompt = prompts.get(action, f"請分析{my_sign}的運勢")

            await bot.answer_callback_query(call.id, text="正在觀測能量星圖...")
            response = await gemini.gemini_stream(bot, call.message, user_prompt)
            if response:
                await save_turn(call.from_user.id, user_prompt, response)

        elif data == "nav_back_to_zodiac":
            await bot.edit_message_text("請選擇您的 **星座**：", chat_id=chat_id, message_id=msg_id, reply_markup=build_zodiac_keyboard("select_sign"), parse_mode="MarkdownV2")
        
        elif data == "nav_model":
            await send_model_picker(call.message, bot)

    except Exception:
        traceback.print_exc()

# --- 其餘指令與邏輯 (保持不變) ---

async def astrology_handler(message: Message, bot: TeleBot) -> None:
    if not await ensure_authorized(message, bot): return
    text = message.text.split()
    if len(text) < 2: return
    sign = text[1]
    prompt = f"分析{sign}的這個月運勢。" if 'horoscope' in text[0] else f"分析{sign}與{text[2] if len(text)>2 else '另一半'}的配對。"
    await gemini.gemini_stream(bot, message, prompt)

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
    parts = call.data.split(":")
    if len(parts) < 4: return
    _, action, subject_type, subject_id = parts
    if action == "revoke":
        await revoke_access(subject_type, int(subject_id), call.from_user.id)
    else:
        status = "approved" if action == "approve" else "rejected"
        await review_access(subject_type, int(subject_id), status, call.from_user.id)
    await bot.answer_callback_query(call.id, text=f"Done")

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
