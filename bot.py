import asyncio
from datetime import datetime
import json
import logging
import os

from aiogram import Bot, Dispatcher, F
from aiogram.enums import ParseMode
from aiogram.filters import CommandStart
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
)
import aiohttp
from aiohttp import web

# --- НАЛАШТУВАННЯ ---
TELEGRAM_BOT_TOKEN = "8841892288:AAEvW9PrcWJ1gD4iVTAw2ouAaNu99V_P55M"

# Твій новий токен від ukrainealarm.com
UKRAINE_ALARM_TOKEN = "72b33dc3:bfa08d61c3d0e08623a7a68fb80247b5"
API_URL = "https://api.ukrainealarm.com/api/v3/alerts"

CHECK_INTERVAL = 20  # Інтервал перевірки (секунди)
USERS_FILE = "users.json"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(name)s - %(message)s",
)

bot = Bot(token=TELEGRAM_BOT_TOKEN)
dp = Dispatcher()

# Списочок областей
REGIONS = {
    "volyn": {"name": "Волинська область", "keys": ["волинська", "волинь"]},
    "rivne": {"name": "Рівненська область", "keys": ["рівненська", "рівне"]},
    "lviv": {"name": "Львівська область", "keys": ["львівська", "львів"]},
    "ternopil": {"name": "Тернопільська область", "keys": ["тернопільська", "тернопіль"]},
    "ivano-frankivsk": {"name": "Івано-Франківська область", "keys": ["івано-франківська", "франківськ"]},
    "zakarpattia": {"name": "Закарпатська область", "keys": ["закарпатська", "закарпаття"]},
    "chernivtsi": {"name": "Чернівецька область", "keys": ["чернівецька", "чернівці"]},
    "khmelnytskyi": {"name": "Хмельницька область", "keys": ["хмельницька", "хмельницький"]},
    "zhytomyr": {"name": "Житомирська область", "keys": ["житомирська", "житомир"]},
    "kyiv_obl": {"name": "Київська область", "keys": ["київська область"]},
    "kyiv_city": {"name": "м. Київ", "keys": ["м. київ", "м.київ", "київ"]},
    "chernihiv": {"name": "Чернігівська область", "keys": ["чернігівська", "чернігів"]},
    "sumy": {"name": "Сумська область", "keys": ["сумська", "суми"]},
    "vinnytsia": {"name": "Вінницька область", "keys": ["вінницька", "вінниця"]},
    "cherkasy": {"name": "Черкаська область", "keys": ["черкаська", "черкаси"]},
    "poltava": {"name": "Полтавська область", "keys": ["полтавська", "полтава"]},
    "kirovohrad": {"name": "Кіровоградська область", "keys": ["кіровоградська", "кропивницький"]},
    "kharkiv": {"name": "Харківська область", "keys": ["харківська", "харків"]},
    "dnipro": {"name": "Дніпропетровська область", "keys": ["дніпропетровська", "дніпро"]},
    "donetsk": {"name": "Донецька область", "keys": ["донецька", "донецьк"]},
    "luhansk": {"name": "Луганська область", "keys": ["луганська", "луганськ"]},
    "odesa": {"name": "Одеська область", "keys": ["одеська", "одеса"]},
    "mykolaiv": {"name": "Миколаївська область", "keys": ["миколаївська", "миколаїв"]},
    "kherson": {"name": "Херсонська область", "keys": ["херсонська", "херсон"]},
    "zaporizhzhia": {"name": "Запорізька область", "keys": ["запорізька", "запоріжжя"]},
    "crimea": {"name": "АР Крим", "keys": ["крим", "ар крим"]},
}

regions_state = {
    reg_key: {"status": "CLEAR", "start_time": None} for reg_key in REGIONS
}

# --- МЕНЕДЖЕР КОРИСТУВАЧІВ ---

def load_user_regions() -> dict:
    if os.path.exists(USERS_FILE):
        try:
            with open(USERS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                return {int(k): (v if isinstance(v, list) else [str(v)]) for k, v in data.items()}
        except Exception as e:
            logging.error(f"Помилка зчитування {USERS_FILE}: {e}")
    return {}

def save_user_regions():
    try:
        with open(USERS_FILE, "w", encoding="utf-8") as f:
            json.dump(user_regions, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logging.error(f"Помилка запису у {USERS_FILE}: {e}")

user_regions = load_user_regions()

def is_silent_mode() -> bool:
    """З 21:00 до 05:59 без звуку"""
    now_hour = datetime.now().hour
    return now_hour >= 21 or now_hour < 6

def format_duration(start_time: datetime, end_time: datetime) -> str:
    delta_seconds = int((end_time - start_time).total_seconds())
    hours = delta_seconds // 3600
    minutes = (delta_seconds % 3600) // 60
    if hours > 0:
        return f"{hours} год {minutes} хв"
    return f"{minutes} хв"

# --- КЛАВІАТУРИ ---

def get_main_menu_keyboard() -> ReplyKeyboardMarkup:
    kb = [
        [
            KeyboardButton(text="👁 Перегляд підписок"),
            KeyboardButton(text="➕ Додати область"),
        ],
        [
            KeyboardButton(text="📊 Статус"),
            KeyboardButton(text="🗺 Карта"),
            KeyboardButton(text="💾 Зберегти"),
        ],
    ]
    return ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)

def get_add_regions_keyboard(chat_id: int) -> InlineKeyboardMarkup:
    user_subs = user_regions.get(chat_id, [])
    buttons = []
    available_keys = [k for k in REGIONS.keys() if k not in user_subs]

    for i in range(0, len(available_keys), 2):
        row = [InlineKeyboardButton(text=REGIONS[available_keys[i]]["name"], callback_data=f"add_reg_{available_keys[i]}")]
        if i + 1 < len(available_keys):
            row.append(InlineKeyboardButton(text=REGIONS[available_keys[i+1]]["name"], callback_data=f"add_reg_{available_keys[i+1]}"))
        buttons.append(row)

    buttons.append([InlineKeyboardButton(text="💾 Зберегти та завершити", callback_data="save_changes")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def get_subscribed_regions_keyboard(chat_id: int) -> InlineKeyboardMarkup:
    user_subs = user_regions.get(chat_id, [])
    buttons = []

    for reg_key in user_subs:
        reg_name = REGIONS.get(reg_key, {}).get("name", reg_key)
        buttons.append([InlineKeyboardButton(text=f"📍 {reg_name}", callback_data=f"select_sub_{reg_key}")])

    buttons.append([InlineKeyboardButton(text="💾 Зберегти зміни", callback_data="save_changes")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

# --- ЗАПИТ ДО ОФІЦІЙНОГО API ---

async def fetch_api_alerts(session: aiohttp.ClientSession) -> str:
    headers = {
        "Authorization": UKRAINE_ALARM_TOKEN,
        "Accept": "application/json",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
    }
    try:
        async with session.get(API_URL, headers=headers, timeout=12) as resp:
            if resp.status == 200:
                text_data = await resp.text()
                return text_data.lower()
            else:
                logging.warning(f"API повернув статус: {resp.status}")
    except Exception as e:
        logging.error(f"Помилка запиту до API ukrainealarm: {e}")

    return ""

# --- ЦИКЛ МЕРЕЖЕВОГО МОНІТОРИНГУ ---

async def check_alerts_loop():
    connector = aiohttp.TCPConnector(ssl=False)
    async with aiohttp.ClientSession(connector=connector) as session:
        while True:
            raw_response = await fetch_api_alerts(session)

            if raw_response:
                now = datetime.now()
                disable_sound = is_silent_mode()

                for reg_key, reg_info in REGIONS.items():
                    has_alert = any(k in raw_response for k in reg_info["keys"])
                    
                    current_status = "ALERT" if has_alert else "CLEAR"
                    st = regions_state[reg_key]
                    old_status = st["status"]

                    if current_status != old_status:
                        sound_str = "🔕 (Без звуку)" if disable_sound else "🔊 (Зі звуком)"

                        if current_status == "ALERT":
                            st["status"] = "ALERT"
                            st["start_time"] = now

                            text = (
                                f"🚨 <b>ПОЧАТОК ТРИВОГИ!</b> {sound_str}\n\n"
                                f"📍 <b>Регіон:</b> {reg_info['name']}\n"
                                f"🕒 <b>Початок:</b> {now.strftime('%H:%M:%S')}\n\n"
                                f"Прямуйте в укриття! 🛡️"
                            )
                            await send_to_subscribers(reg_key, text, disable_sound)

                        elif current_status == "CLEAR":
                            start_time = st["start_time"] or now
                            end_time = now
                            duration = format_duration(start_time, end_time)

                            st["status"] = "CLEAR"
                            st["start_time"] = None

                            text = (
                                f"✅ <b>ВІДБІЙ ТРИВОГИ!</b> {sound_str}\n\n"
                                f"📍 <b>Регіон:</b> {reg_info['name']}\n"
                                f"🛫 <b>Початок:</b> {start_time.strftime('%H:%M')}\n"
                                f"🛬 <b>Кінець:</b> {end_time.strftime('%H:%M')}\n"
                                f"⏱ <b>Тривалість:</b> {duration}\n\n"
                                f"Загроза минула. 🟢"
                            )
                            await send_to_subscribers(reg_key, text, disable_sound)

            await asyncio.sleep(CHECK_INTERVAL)

async def send_to_subscribers(reg_key: str, text: str, disable_sound: bool):
    for chat_id, user_subs in list(user_regions.items()):
        if reg_key in user_subs:
            try:
                await bot.send_message(
                    chat_id, text, parse_mode=ParseMode.HTML, disable_notification=disable_sound
                )
            except Exception as e:
                logging.error(f"Не вдалося надіслати користувачу {chat_id}: {e}")

# --- ОБРОБНИКИ КОМАНД І КНОПОК ---

@dp.message(CommandStart())
async def cmd_start(message: Message):
    chat_id = message.chat.id
    if chat_id not in user_regions:
        user_regions[chat_id] = ["kyiv_city"]
        save_user_regions()

    await message.answer(
        "Вітаю! Бот успішно авторизований через офіційний API і готовий до роботи.\n\n"
        "⏰ <b>Режим сповіщень:</b>\n"
        "• 06:00 - 20:59 — 🔊 <b>Зі звуком</b>\n"
        "• 21:00 - 05:59 — 🔕 <b>Без звуку (Нічний режим)</b>",
        reply_markup=get_main_menu_keyboard(),
        parse_mode=ParseMode.HTML,
    )

@dp.message(F.text == "🗺 Карта")
async def cmd_map(message: Message):
    map_kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🌐 Відкрити онлайн-карту", url="https://alerts.in.ua/")]
        ]
    )
    await message.answer(
        "🗺 <b>Онлайн-карта повітряних тривог України:</b>\n\nНатисніть кнопку нижче, щоб відкрити її в браузері.",
        reply_markup=map_kb,
        parse_mode=ParseMode.HTML,
    )

@dp.message(F.text == "👁 Перегляд підписок")
async def cmd_view_subscriptions(message: Message):
    chat_id = message.chat.id
    subs = user_regions.get(chat_id, [])

    if not subs:
        await message.answer("У вас немає обраних областей. Натисніть '➕ Додати область'.")
        return

    await message.answer(
        "Ваші обрані області (натисніть на область, щоб видалити її):",
        reply_markup=get_subscribed_regions_keyboard(chat_id),
    )

@dp.message(F.text == "➕ Додати область")
async def cmd_add_region(message: Message):
    chat_id = message.chat.id
    kb = get_add_regions_keyboard(chat_id)

    if not kb.inline_keyboard or len(kb.inline_keyboard) == 1:
        await message.answer("Ви вже підписані на всі доступні області!")
        return

    await message.answer("Оберіть область зі списку:", reply_markup=kb)

@dp.callback_query(F.data.startswith("add_reg_"))
async def cb_add_region(callback: CallbackQuery):
    chat_id = callback.message.chat.id
    reg_key = callback.data.replace("add_reg_", "")

    if chat_id not in user_regions:
        user_regions[chat_id] = []

    if reg_key not in user_regions[chat_id]:
        user_regions[chat_id].append(reg_key)

    reg_name = REGIONS.get(reg_key, {}).get("name", reg_key)
    await callback.answer(f"Додано: {reg_name}")

    await callback.message.edit_text(
        f"✅ Додано <b>{reg_name}</b>.\nОберіть ще або збережіть:",
        reply_markup=get_add_regions_keyboard(chat_id),
        parse_mode=ParseMode.HTML,
    )

@dp.callback_query(F.data.startswith("select_sub_"))
async def cb_select_sub(callback: CallbackQuery):
    reg_key = callback.data.replace("select_sub_", "")
    reg_name = REGIONS.get(reg_key, {}).get("name", reg_key)

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=f"❌ Видалити {reg_name}", callback_data=f"delete_reg_{reg_key}")],
            [InlineKeyboardButton(text="🔙 Назад до списку", callback_data="back_to_subs")],
        ]
    )

    await callback.message.edit_text(
        f"Ви обрали: <b>{reg_name}</b>\nБажаєте видалити її з підписок?",
        reply_markup=kb,
        parse_mode=ParseMode.HTML,
    )

@dp.callback_query(F.data.startswith("delete_reg_"))
async def cb_delete_region(callback: CallbackQuery):
    chat_id = callback.message.chat.id
    reg_key = callback.data.replace("delete_reg_", "")

    if chat_id in user_regions and reg_key in user_regions[chat_id]:
        user_regions[chat_id].remove(reg_key)

    reg_name = REGIONS.get(reg_key, {}).get("name", reg_key)
    await callback.answer(f"Видалено: {reg_name}")

    await callback.message.edit_text(
        f"❌ Область <b>{reg_name}</b> видалено.\nОновлений список:",
        reply_markup=get_subscribed_regions_keyboard(chat_id),
        parse_mode=ParseMode.HTML,
    )

@dp.callback_query(F.data == "back_to_subs")
async def cb_back_to_subs(callback: CallbackQuery):
    chat_id = callback.message.chat.id
    await callback.message.edit_text(
        "Ваші обрані області:",
        reply_markup=get_subscribed_regions_keyboard(chat_id),
    )

@dp.message(F.text == "💾 Зберегти")
@dp.callback_query(F.data == "save_changes")
async def save_and_confirm(event):
    chat_id = event.chat.id if isinstance(event, Message) else event.message.chat.id
    save_user_regions()

    subs = user_regions.get(chat_id, [])
    names = [REGIONS.get(k, {}).get("name", k) for k in subs]

    text_msg = (
        "💾 <b>Збережено!</b>\n\n"
        "Ваші області для сповіщень:\n"
        + ("\n".join([f"• {name}" for name in names]) if names else "<i>Порожньо</i>")
    )

    if isinstance(event, CallbackQuery):
        await event.answer("Збережено!")
        await event.message.edit_text(text_msg, parse_mode=ParseMode.HTML)
    else:
        await event.answer(
            text_msg,
            parse_mode=ParseMode.HTML,
            reply_markup=get_main_menu_keyboard(),
        )

@dp.message(F.text == "📊 Статус")
async def cmd_status(message: Message):
    chat_id = message.chat.id
    subs = user_regions.get(chat_id, [])

    if not subs:
        await message.answer("У вас не обрано жодної області!")
        return

    now = datetime.now()
    text_lines = ["📊 <b>ПОТОЧНИЙ СТАН ВАШИХ ОБЛАСТЕЙ:</b>\n"]

    for reg_key in subs:
        reg_info = REGIONS.get(reg_key, {"name": reg_key})
        st = regions_state.get(reg_key, {"status": "CLEAR", "start_time": None})

        if st["status"] == "ALERT":
            start = st["start_time"] or now
            duration = format_duration(start, now)
            text_lines.append(f"🔴 <b>{reg_info['name']}</b>: Тривога! (триває {duration})")
        else:
            text_lines.append(f"🟢 <b>{reg_info['name']}</b>: Спокійно")

    await message.answer("\n".join(text_lines), parse_mode=ParseMode.HTML)

# --- СЕРВЕР ДЛЯ RENDER ---

async def handle_health_check(request):
    return web.Response(text="Bot is running with Official API Key!")

async def start_web_server():
    app = web.Application()
    app.router.add_get("/", handle_health_check)
    runner = web.AppRunner(app)
    await runner.setup()

    port = int(os.environ.get("PORT", 10000))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()

# --- ТОЧКА ВХОДУ ---

async def main():
    asyncio.create_task(check_alerts_loop())
    await start_web_server()
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
