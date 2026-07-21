import asyncio
from datetime import datetime, time
import json
logging_level = logging.INFO
import logging
import os
from aiogram import Bot, Dispatcher, F
from aiogram.enums import ParseMode
from aiogram.filters import CommandStart
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    WebAppInfo,
)
import aiohttp
from aiohttp import web

# --- НАЛАШТУВАННЯ ---
TELEGRAM_BOT_TOKEN = "ТВІЙ_TELEGRAM_BOT_TOKEN"

# Отримай токен за 1 хв у Telegram: @ukrainealarm_api_bot
UKRAINE_ALARM_TOKEN = "ТВІЙ_AJAX_API_TOKEN"

MAP_URL = "https://alerts.in.ua/"
CHECK_INTERVAL = 10
USERS_FILE = "users.json"

logging.basicConfig(level=logging_level)
bot = Bot(token=TELEGRAM_BOT_TOKEN)
dp = Dispatcher()

# База регіонів (ID -> Назва)
REGIONS = {
    "31": "м. Київ",
    "10": "Київська область",
    "16": "Харківська область",
    "15": "Одеська область",
    "11": "Дніпропетровська область",
    "13": "Львівська область",
    "8": "Запорізька область",
    "17": "Херсонська область",
    "14": "Миколаївська область",
}

# Словник типів загроз
ALERT_TYPES = {
    "AIR": "🚨 Повітряна тривога",
    "MISSILE": "🚀 Ракетна небезпека / Загроза балістики",
    "DRONE": "🛸 Загроза БПЛА (Шахедів)",
    "ARTILLERY": "💥 Артилерійський обстріл",
    "URBAN": "✈️ Загроза авіації / КАБи",
}

# Стан тривог по регіонах: {region_id: {"is_alert": bool, "threat": str, "start_time": datetime}}
regions_state = {
    r_id: {"is_alert": False, "threat": "AIR", "start_time": None}
    for r_id in REGIONS
}

# --- РОБОТА З JSON ФАЙЛОМ (ЗБЕРЕЖЕННЯ КОРИСТУВАЧІВ) ---


def load_user_regions() -> dict:
    """Завантажує збережені регіони з JSON-файлу при старті."""
    if os.path.exists(USERS_FILE):
        try:
            with open(USERS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                return {int(chat_id): str(r_id) for chat_id, r_id in data.items()}
        except Exception as e:
            logging.error(f"Помилка зчитування {USERS_FILE}: {e}")
    return {}


def save_user_regions():
    """Зберігає поточний стан користувачів у JSON-файл."""
    try:
        with open(USERS_FILE, "w", encoding="utf-8") as f:
            json.dump(user_regions, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logging.error(f"Помилка запису у {USERS_FILE}: {e}")


# Ініціалізація даних з файлу
user_regions = load_user_regions()


def is_night_mode() -> bool:
    """Нічний режим (23:00 - 07:00) без звуку."""
    now = datetime.now().time()
    return now >= time(23, 0) or now < time(7, 0)


def get_regions_keyboard() -> InlineKeyboardMarkup:
    """Клавіатура вибору регіону."""
    buttons = []
    for r_id, r_name in REGIONS.items():
        buttons.append(
            [InlineKeyboardButton(text=r_name, callback_data=f"set_reg_{r_id}")]
        )
    return InlineKeyboardMarkup(inline_keyboard=buttons)


# --- ФОНОВА ПЕРЕВІРКА АПІ ---


async def check_alerts_loop():
    """Перевірка статусу тривог та конкретних загроз."""
    url = "https://api.ukrainealarm.com/api/v3/alerts"
    headers = {
        "Authorization": UKRAINE_ALARM_TOKEN,
        "Content-Type": "application/json",
    }

    async with aiohttp.ClientSession() as session:
        while True:
            try:
                async with session.get(url, headers=headers) as response:
                    if response.status == 200:
                        alerts_data = await response.json()
                        await parse_and_notify(alerts_data)
                    else:
                        logging.warning(
                            f"Статус відповіді API: {response.status}"
                        )
            except Exception as e:
                logging.error(f"Помилка API: {e}")

            await asyncio.sleep(CHECK_INTERVAL)


async def parse_and_notify(alerts_data):
    """Обробка даних про тривоги та розсилка користувачам."""
    active_regions = {}

    for item in alerts_data:
        r_id = str(item.get("regionId"))
        if r_id in REGIONS:
            active_alerts = item.get("activeAlerts", [])
            if active_alerts:
                threat_type = active_alerts[0].get("type", "AIR")
                active_regions[r_id] = threat_type

    now = datetime.now()
    disable_sound = is_night_mode()

    for r_id, name in REGIONS.items():
        is_currently_active = r_id in active_regions
        current_threat = active_regions.get(r_id, "AIR")
        st = regions_state[r_id]

        # ПОЧАТОК ТРИВОГИ
        if is_currently_active and not st["is_alert"]:
            st["is_alert"] = True
            st["threat"] = current_threat
            st["start_time"] = now

            threat_text = ALERT_TYPES.get(current_threat, "🚨 Повітряна тривога")
            text = (
                f"🚨 <b>ПОЧАТОК ТРИВОГИ!</b>\n\n"
                f"📍 <b>Регіон:</b> {name}\n"
                f"⚠️ <b>Загроза:</b> {threat_text}\n"
                f"🕒 <b>Час:</b> {now.strftime('%H:%M')}\n\n"
                f"Прямуйте в укриття! 🛡️"
            )
            await send_to_subscribers(r_id, text, disable_sound)

        # ВІДБІЙ ТРИВОГИ
        elif not is_currently_active and st["is_alert"]:
            st["is_alert"] = False
            text = (
                f"✅ <b>ВІДБІЙ ТРИВОГИ!</b>\n\n"
                f"📍 <b>Регіон:</b> {name}\n"
                f"🕒 <b>Час:</b> {now.strftime('%H:%M')}\n\n"
                f"Можна залишати укриття. 🟢"
            )
            await send_to_subscribers(r_id, text, disable_sound)


async def send_to_subscribers(region_id: str, text: str, disable_sound: bool):
    """Надсилає сповіщення тим користувачам, які обрали цей регіон."""
    for chat_id, r_id in list(user_regions.items()):
        if r_id == region_id:
            try:
                await bot.send_message(
                    chat_id,
                    text,
                    parse_mode=ParseMode.HTML,
                    disable_notification=disable_sound,
                )
            except Exception as e:
                logging.error(f"Не вдалося надіслати у чат {chat_id}: {e}")


# --- ОБРОБНИКИ КОМАНД ---


@dp.message(CommandStart())
async def cmd_start(message: Message):
    """Старт та вибір регіону."""
    chat_id = message.chat.id
    if chat_id not in user_regions:
        user_regions[chat_id] = "31"  # По дефолту м. Київ
        save_user_regions()

    await message.answer(
        "Вітаю! Оберіть свій регіон для отримання сповіщень про тривоги та типи загроз (ракети, шахеди тощо):",
        reply_markup=get_regions_keyboard(),
    )


@dp.callback_query(F.data.startswith("set_reg_"))
async def cb_set_region(callback: CallbackQuery):
    """Збереження обраного регіону."""
    r_id = callback.data.split("_")[2]
    user_regions[callback.message.chat.id] = r_id
    save_user_regions()

    r_name = REGIONS.get(r_id, "Невідомий")

    await callback.message.edit_text(
        f"✅ Регіон успішно змінено на: <b>{r_name}</b>\n\n"
        "Доступні команди:\n"
        "• <b>карта</b> — онлайн-карта тривог\n"
        "• <b>статус</b> — поточна ситуація\n"
        "• <b>регіон</b> — змінити область/місто",
        parse_mode=ParseMode.HTML,
    )


@dp.message(F.text.lower() == "регіон")
async def cmd_change_region(message: Message):
    """Команда для зміни регіону."""
    await message.answer(
        "Оберіть новий регіон зі списку:", reply_markup=get_regions_keyboard()
    )


@dp.message(F.text.lower() == "карта")
async def cmd_map(message: Message):
    """Відображення карти."""
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🗺️ Відкрити карту", web_app=WebAppInfo(url=MAP_URL)
                )
            ]
        ]
    )
    await message.answer("🗺️ Відкрити інтерактивну карту:", reply_markup=keyboard)


@dp.message(F.text.lower() == "статус")
async def cmd_status(message: Message):
    """Перевірка статусу для обраного користувачем регіону."""
    r_id = user_regions.get(message.chat.id, "31")
    r_name = REGIONS.get(r_id, "м. Київ")
    st = regions_state.get(r_id, {"is_alert": False, "threat": "AIR", "start_time": None})

    if st["is_alert"]:
        now = datetime.now()
        start = st["start_time"] or now
        duration = now - start
        minutes_passed = int(duration.total_seconds() // 60)
        threat_text = ALERT_TYPES.get(st["threat"], "🚨 Повітряна тривога")

        text = (
            f"📍 <b>{r_name}</b>\n\n"
            f"🔴 <b>Триває повітряна тривога!</b>\n"
            f"⚠️ <b>Загроза:</b> {threat_text}\n"
            f"🕒 <b>Початок:</b> {start.strftime('%H:%M')}\n"
            f"⏱ <b>Минуло:</b> {minutes_passed} хв"
        )
    else:
        text = (
            f"📍 <b>{r_name}</b>\n\n"
            f"🟢 <b>Повітряної тривоги немає.</b>\n"
            f"У вашому регіоні наразі спокійно."
        )

    await message.answer(text, parse_mode=ParseMode.HTML)


# --- ФІКТИВНИЙ ВЕБ-СЕРВЕР ДЛЯ RENDER WEB SERVICE ---


async def handle_health_check(request):
    return web.Response(text="Bot is running!")


async def start_web_server():
    app = web.Application()
    app.router.add_get("/", handle_health_check)
    runner = web.AppRunner(app)
    await runner.setup()

    port = int(os.environ.get("PORT", 10000))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()


async def main():
    asyncio.create_task(check_alerts_loop())
    await start_web_server()
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
