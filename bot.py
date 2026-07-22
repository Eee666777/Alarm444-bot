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

# Ендпоінт статусу тривог
ALERTS_API_URL = "https://ubilling.net.ua/aerialalerts/"
BACKUP_API_URL = "https://alerts.in.ua/api/states"

CHECK_INTERVAL = 30  # Інтервал перевірки (30 секунд запобігає помилці 429)
USERS_FILE = "users.json"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(name)s - %(message)s",
)

bot = Bot(token=TELEGRAM_BOT_TOKEN)
dp = Dispatcher()

# --- СПИСОК ОБЛАСТЕЙ ТА ЇХ СТАНДАРТНІ НАЗВИ ---
REGIONS = {
    "volyn": {"name": "Волинська область", "search_keys": ["волинська", "волинь"]},
    "rivne": {"name": "Рівненська область", "search_keys": ["рівненська", "рівне"]},
    "lviv": {"name": "Львівська область", "search_keys": ["львівська", "львів"]},
    "ternopil": {"name": "Тернопільська область", "search_keys": ["тернопільська", "тернопіль"]},
    "ivano-frankivsk": {"name": "Івано-Франківська область", "search_keys": ["івано-франківська", "франківськ"]},
    "zakarpattia": {"name": "Закарпатська область", "search_keys": ["закарпатська", "закарпаття"]},
    "chernivtsi": {"name": "Чернівецька область", "search_keys": ["чернівецька", "чернівці"]},
    "khmelnytskyi": {"name": "Хмельницька область", "search_keys": ["хмельницька", "хмельницький"]},
    "zhytomyr": {"name": "Житомирська область", "search_keys": ["житомирська", "житомир"]},
    "kyiv_obl": {"name": "Київська область", "search_keys": ["київська область"]},
    "kyiv_city": {"name": "м. Київ", "search_keys": ["м. київ", "м.київ", "місто київ"]},
    "chernihiv": {"name": "Чернігівська область", "search_keys": ["чернігівська", "чернігів"]},
    "sumy": {"name": "Сумська область", "search_keys": ["сумська", "суми"]},
    "vinnytsia": {"name": "Вінницька область", "search_keys": ["вінницька", "вінниця"]},
    "cherkasy": {"name": "Черкаська область", "search_keys": ["черкаська", "черкаси"]},
    "poltava": {"name": "Полтавська область", "search_keys": ["полтавська", "полтава"]},
    "kirovohrad": {"name": "Кіровоградська область", "search_keys": ["кіровоградська", "кропивницький"]},
    "kharkiv": {"name": "Харківська область", "search_keys": ["харківська", "харків"]},
    "dnipro": {"name": "Дніпропетровська область", "search_keys": ["дніпропетровська", "дніпро"]},
    "donetsk": {"name": "Донецька область", "search_keys": ["донецька", "донецьк"]},
    "luhansk": {"name": "Луганська область", "search_keys": ["луганська", "луганськ"]},
    "odesa": {"name": "Одеська область", "search_keys": ["одеська", "одеса"]},
    "mykolaiv": {"name": "Миколаївська область", "search_keys": ["миколаївська", "миколаїв"]},
    "kherson": {"name": "Херсонська область", "search_keys": ["херсонська", "херсон"]},
    "zaporizhzhia": {"name": "Запорізька область", "search_keys": ["запорізька", "запоріжжя"]},
    "crimea": {"name": "АР Крим", "search_keys": ["крим", "ар крим"]},
}

# Поточний стан тривог для кожної області
regions_state = {
    reg_key: {"status": "CLEAR", "start_time": None} for reg_key in REGIONS
}

# --- МЕНЕДЖЕР КОРИСТУВАЧІВ ---

def load_user_regions() -> dict:
    if os.path.exists(USERS_FILE):
        try:
            with open(USERS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                result = {}
                for chat_id, regs in data.items():
                    if isinstance(regs, list):
                        result[int(chat_id)] = regs
                    else:
                        result[int(chat_id)] = [str(regs)]
                return result
        except Exception as e:
            logging.error(f"Помилка читання {USERS_FILE}: {e}")
    return {}

def save_user_regions():
    try:
        with open(USERS_FILE, "w", encoding="utf-8") as f:
            json.dump(user_regions, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logging.error(f"Помилка запису у {USERS_FILE}: {e}")

user_regions = load_user_regions()

def is_silent_mode() -> bool:
    """З 21:00 до 05:59 ранку без звуку, з 06:00 до 20:59 зі звуком."""
    now_hour = datetime.now().hour
    return now_hour >= 21 or now_hour < 6

# --- КЛАВІАТУРИ ---

def get_main_menu_keyboard() -> ReplyKeyboardMarkup:
    kb = [
        [
            KeyboardButton(text="👁 Перегляд підписок"),
            KeyboardButton(text="➕ Додати область"),
        ],
        [KeyboardButton(text="📊 Статус"), KeyboardButton(text="💾 Зберегти")],
    ]
    return ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)

def get_add_regions_keyboard(chat_id: int) -> InlineKeyboardMarkup:
    user_subs = user_regions.get(chat_id, [])
    buttons = []
    available_keys = [k for k in REGIONS.keys() if k not in user_subs]

    for i in range(0, len(available_keys), 2):
        row = [
            InlineKeyboardButton(
                text=REGIONS[available_keys[i]]["name"],
                callback_data=f"add_reg_{available_keys[i]}",
            )
        ]
        if i + 1 < len(available_keys):
            row.append(
                InlineKeyboardButton(
                    text=REGIONS[available_keys[i + 1]]["name"],
                    callback_data=f"add_reg_{available_keys[i + 1]}",
                )
            )
        buttons.append(row)

    buttons.append(
        [InlineKeyboardButton(text="💾 Зберегти та завершити", callback_data="save_changes")]
    )
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def get_subscribed_regions_keyboard(chat_id: int) -> InlineKeyboardMarkup:
    user_subs = user_regions.get(chat_id, [])
    buttons = []

    for reg_key in user_subs:
        reg_name = REGIONS.get(reg_key, {}).get("name", reg_key)
        buttons.append(
            [InlineKeyboardButton(text=f"📍 {reg_name}", callback_data=f"select_sub_{reg_key}")]
        )

    buttons.append(
        [InlineKeyboardButton(text="💾 Зберегти зміни", callback_data="save_changes")]
    )
    return InlineKeyboardMarkup(inline_keyboard=buttons)

# --- ПАРСИНГ ТА ПЕРЕВІРКА ТРИВОГ ---

async def fetch_alerts_data(session: aiohttp.ClientSession) -> dict:
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
        "Accept": "application/json",
    }
    
    # Перша спроба з основними API
    try:
        async with session.get(ALERTS_API_URL, headers=headers, timeout=10) as resp:
            if resp.status == 200:
                data = await resp.json()
                if isinstance(data, dict):
                    return data
            else:
                logging.warning(f"Основний API повернув статус: {resp.status}")
    except Exception as e:
        logging.warning(f"Не вдалося з'єднатися з основним API: {e}")

    # Резервний запит
    try:
        async with session.get(BACKUP_API_URL, headers=headers, timeout=10) as resp:
            if resp.status == 200:
                return await resp.json()
            else:
                logging.warning(f"Резервний API повернув статус: {resp.status}")
    except Exception as e:
        logging.error(f"Не вдалося з'єднатися з резервним API: {e}")

    return {}

async def check_alerts_loop():
    connector = aiohttp.TCPConnector(ssl=False)
    async with aiohttp.ClientSession(connector=connector) as session:
        while True:
            alerts_data = await fetch_alerts_data(session)

            if alerts_data:
                now = datetime.now()
                disable_sound = is_silent_mode()

                # Створюємо єдиний текст у нижньому регістрі для швидкого пошуку
                raw_text = json.dumps(alerts_data, ensure_ascii=False).lower()

                for reg_key, reg_info in REGIONS.items():
                    # Перевіряємо чи присутня область у списку активних тривог
                    is_alert = any(
                        key_word in raw_text for key_word in reg_info["search_keys"]
                    )
                    
                    current_status = "ALERT" if is_alert else "CLEAR"
                    st = regions_state[reg_key]
                    old_status = st["status"]

                    if current_status != old_status:
                        st["status"] = current_status
                        st["start_time"] = now

                        sound_str = "🔕 (Без звуку)" if disable_sound else "🔊 (Зі звуком)"

                        if current_status == "ALERT":
                            text = (
                                f"🚨 <b>ПОЧАТОК ТРИВОГИ!</b> {sound_str}\n\n"
                                f"📍 <b>Регіон:</b> {reg_info['name']}\n"
                                f"🕒 <b>Час:</b> {now.strftime('%H:%M')}\n\n"
                                f"Прямуйте в укриття! 🛡️"
                            )
                        else:
                            text = (
                                f"✅ <b>ВІДБІЙ ТРИВОГИ!</b> {sound_str}\n\n"
                                f"📍 <b>Регіон:</b> {reg_info['name']}\n"
                                f"🕒 <b>Час:</b> {now.strftime('%H:%M')}\n\n"
                                f"Можна залишати укриття. 🟢"
                            )

                        await send_to_subscribers(reg_key, text, disable_sound)

            await asyncio.sleep(CHECK_INTERVAL)

async def send_to_subscribers(reg_key: str, text: str, disable_sound: bool):
    for chat_id, user_subs in list(user_regions.items()):
        if reg_key in user_subs:
            try:
                await bot.send_message(
                    chat_id,
                    text,
                    parse_mode=ParseMode.HTML,
                    disable_notification=disable_sound,
                )
            except Exception as e:
                logging.error(f"Не вдалося надіслати повідомлення {chat_id}: {e}")

# --- ОБРОБНИКИ КОМАНД І КНОПОК ---

@dp.message(CommandStart())
async def cmd_start(message: Message):
    chat_id = message.chat.id
    if chat_id not in user_regions:
        user_regions[chat_id] = ["kyiv_city"]
        save_user_regions()

    await message.answer(
        "Вітаю! Бот готовий до роботи та сповістить вас про зміну стану тривог.\n\n"
        "⏰ <b>Режим сповіщень:</b>\n"
        "• 06:00 - 20:59 — 🔊 <b>Зі звуком</b>\n"
        "• 21:00 - 05:59 — 🔕 <b>Без звуку (Нічний режим)</b>",
        reply_markup=get_main_menu_keyboard(),
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
        f"✅ Додано <b>{reg_name}</b>.\nОберіть ще або натисніть 'Зберегти':",
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

        start = st["start_time"] or now
        minutes_passed = int((now - start).total_seconds() // 60)

        if st["status"] == "ALERT":
            text_lines.append(
                f"🔴 <b>{reg_info['name']}</b>: Тривога! (триває {minutes_passed} хв)"
            )
        else:
            text_lines.append(f"🟢 <b>{reg_info['name']}</b>: Спокійно")

    await message.answer("\n".join(text_lines), parse_mode=ParseMode.HTML)

# --- СЕРВЕР ДЛЯ РЕНДЕРУ (HEALTH CHECK) ---

async def handle_health_check(request):
    return web.Response(text="Radar Bot is active!")

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
