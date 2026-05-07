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
    # 在 callback_data 裡放入 "功能:星座"
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

# --- 修改後的 Start 函數：顯示 12 星座 ---
async def start(message: Message, bot: TeleBot) -> None:
    try:
        welcome_text = (
            "✨ **奕川的解毒劑 - 占星導航** ✨\n\n"
            "請先點選你的 **星座** 來開啟專屬功能選單："
        )
        await bot.reply_to(message, escape(welcome_text), reply_markup=build_zodiac_keyboard(), parse_mode="MarkdownV2")
    except Exception:
        traceback.print_exc()
        await bot.reply_to(message, error_info)

# --- 核心：處理所有按鈕點擊 ---
async def astrology_callback(call: CallbackQuery, bot: TeleBot) -> None:
    try:
        data = call.data
        
        # 1. 處理「選擇星座」的動作
        if data.startswith("select_sign:"):
            sign = data.split(":")[1]
            text = f"✨ **{sign}** 的專屬解毒劑選單 ✨\n\n請選擇你想要進行的觀測項目："
            await bot.edit_message_text(
                escape(text),
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                reply_markup=build_feature_keyboard(sign),
                parse_mode="MarkdownV2"
            )
            await bot.answer_callback_query(call.id, text=f"已選擇 {sign}")

        # 2. 處理「回歸星座選擇」
        elif data == "nav_back_to_zodiac":
            await bot.edit_message_text(
                "請先點選你的 **星座** 來開啟專屬功能選單：",
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                reply_markup=build_zodiac_keyboard()
            )
            await bot.answer_callback_query(call.id)

        # 3. 處理「6 大功能」 (格式：astro_功能:星座)
        elif data.startswith("astro_"):
            parts = data.split(":")
            action = parts[0]
            sign = parts[1] if len(parts) > 1 else "神秘星座"
            
            prompts = {
                "astro_daily": f"請以占星師身份，分析「{sign}」今天的整體星象氣氛與今日行動建議。",
                "astro_lucky": f"請告訴「{sign}」目前的幸運色、幸運數字與幸運方位。",
                "astro_advice": f"請針對「{sign}」的工作、財運、健康、感情提供目前的星象建議。",
                "astro_stress": f"「{sign}」現在感到壓力很大，請提供舒壓與自我療癒的方法。",
                "astro_motivation": f"「{sign}」現在缺乏動力，請給我專屬的激勵建議。",
                "astro_reflection": f"請給「{sign}」一個適合本週思考的心靈練習題目。"
            }
            
            if action in prompts:
                await bot.answer_callback_query(call.id, text=f"正在為 {sign} 觀測星象...")
                await gemini.gemini_stream(bot, call.message, prompts[action])

        # 4. 其他導航功能
        elif data == "nav_model":
            await send_model_picker(call.message, bot)
            await bot.answer_callback_query(call.id)
        elif data == "nav_clear":
            await clear_history(call.from_user.id)
            await bot.answer_callback_query(call.id, text="✅ 記憶已清空")
            await bot.send_message(call.message.chat.id, "記憶已清除。")

    except Exception:
        traceback.print_exc()
        await bot.send_message(call.message.chat.id, error_info)

# --- 以下保留你原本的其他 handler (gemini_handler, access 等) ---
async def gemini_handler(message: Message, bot: TeleBot) -> None:
    try:
        parts = message.text.strip().split(maxsplit=1)
        if len(parts) < 2:
            await bot.reply_to(message, escape("Please add what you want to say after /gemini. \nExample: `/gemini 誰是約翰藍儂?`"), parse_mode="MarkdownV2")
            return
        contents = parts[1].strip()
    except IndexError:
        await bot.reply_to(message, "Please add context.")
        return
    
    if await get_current_model(message.from_user.id) is None:
        await send_model_picker(message, bot)
        return
    await gemini.gemini_stream(bot, message, contents)

async def astrology_handler(message: Message, bot: TeleBot) -> None:
    text = message.text.split()
    if len(text) < 2:
        await bot.reply_to(message, "請輸入星座，例如：/horoscope 雙子座")
        return
    cmd = text[0].replace('/', '')
    args = text[1:]
    
    if cmd == 'horoscope':
        prompt = f"請詳細分析{args[0]}在這個月的詳細運勢，包含事業、感情與財運。"
    elif cmd == 'compatibility' and len(args) >= 2:
        prompt = f"請深入分析{args[0]}與{args[1]}之間的互動、配對優勢與挑戰。"
    else:
        await bot.reply_to(message, "請輸入正確格式，例如：/compatibility 雙子座 牡羊座")
        return
    await gemini.gemini_stream(bot, message, prompt)

# ... (後續的 clear, model, access_callback 等函數保持不變，直接貼上你原本的即可) ...
# 注意：為了長度簡潔，我這裡省略了你原本沒動到的 access 相關函數，請確保你自己檔案裡那些部分還在。

async def clear(message: Message, bot: TeleBot) -> None:
    if message.chat.type != "private":
        await bot.reply_to(message, "Please use /clear in a private chat.")
        return
    await clear_history(message.from_user.id)
    await bot.reply_to(message, "Your history has been cleared")

async def model(message: Message, bot: TeleBot) -> None:
    if message.chat.type != "private":
        await bot.reply_to(message, "Please use /model in a private chat.")
        return
    await send_model_picker(message, bot)

async def model_callback(call: CallbackQuery, bot: TeleBot) -> None:
    try:
        model_index = int(call.data.removeprefix(MODEL_CALLBACK_PREFIX))
        models = await list_available_models()
        selected_model = models[model_index]
        model_name = await select_model(call.from_user.id, selected_model)
        await bot.answer_callback_query(call.id, text=f"Using {model_name}")
        if call.message:
            await bot.edit_message_text(
                "Now you are using " + model_name,
                chat_id=call.message.chat.id,
                message_id=call.message.message_id
            )
    except Exception:
        traceback.print_exc()
        await bot.answer_callback_query(call.id, text="Failed to switch model", show_alert=True)

async def gemini_private_handler(message: Message, bot: TeleBot) -> None:
    contents = message.text.strip()
    if await get_current_model(message.from_user.id) is None:
        await send_model_picker(message, bot)
        return
    await gemini.gemini_stream(bot, message, contents)

async def gemini_photo_handler(message: Message, bot: TeleBot) -> None:
    s = message.caption or ""
    if message.chat.type != "private" and not s.startswith("/gemini"):
        return
    if await get_current_model(message.from_user.id) is None:
        await send_model_picker(message, bot)
        return
    try:
        m = s.strip().split(maxsplit=1)[1].strip() if len(s.strip().split(maxsplit=1)) > 1 else ""
        file = await bot.get_file(message.photo[-1].file_id)
        photo_file = await bot.download_file(file.file_path)
        image = Image.open(io.BytesIO(photo_file))
        contents = [image, m]
    except Exception:
        traceback.print_exc()
        await bot.reply_to(message, error_info)
        return
    await gemini.gemini_stream(bot, message, contents)
