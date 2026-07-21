import asyncio
from datetime import datetime, time
from io import BytesIO
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
    Message,
)
import aiohttp
from aiohttp import web
from PIL import Image

# --- НАЛАШТУВАННЯ ---
TELEGRAM_BOT_TOKEN = "8841892288:AAEvW9PrcWJ1gD4iVTAw2ouAaNu99V_P55M"

# Пряме Raw-посилання на малюнок карти у твоєму репозиторії GitHub
MAP_IMAGE_URL = "https://raw.githubusercontent.com/Eee666777/Alarm444-bot/main/web.html"

CHECK_INTERVAL = 15  # Інтервал перевірки карти (у секундах)
USERS_FILE = "users.json"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(name)s - %(message)s",
)

bot = Bot(token=TELEGRAM_BOT_TOKEN)
dp = Dispatcher()

# --- КООРДИНАТИ ПІКСЕЛІВ ДЛЯ КАРТИ (1024 x 682) ---
REGIONS = {
    # Захід
    "volyn": {"name": "Волинська область", "x": 160, "y": 180},
    "rivne": {"name": "Рівненська область", "x": 260, "y": 180},
    "lviv": {"name": "Львівська область", "x": 100, "y": 320},
    "ternopil": {"name": "Тернопільська область", "x": 200, "y": 360},
    "ivano-frankivsk": {"name": "Івано-Франківська область", "x": 150, "y": 450},
    "zakarpattia": {"name": "Закарпатська область", "x": 75, "y": 480},
    "chernivtsi": {"name": "Чернівецька область", "x": 220, "y": 510},
    "khmelnytskyi": {"name": "Хмельницька область", "x": 280, "y": 330},
    # Центр та Північ
    "zhytomyr": {"name": "Житомирська область", "x": 370, "y": 210},
    "kyiv_obl": {"name": "Київська область", "x": 480, "y": 280},
    "kyiv_city": {"name": "м. Київ", "x": 465, "y": 272},
    "chernihiv": {"name": "Чернігівська область", "x": 520, "y": 150},
    "sumy": {"name": "Сумська область", "x": 650, "y": 170},
    "vinnytsia": {"name": "Вінницька область", "x": 370, "y": 420},
    "cherkasy": {"name": "Черкаська область", "x": 490, "y": 400},
    "poltava": {"name": "Полтавська область", "x": 610, "y": 330},
    "kirovohrad": {"name": "Кіровоградська область", "x": 570, "y": 450},
    # Схід
    "kharkiv": {"name": "Харківська область", "x": 770, "y": 330},
    "dnipro": {"name": "Дніпропетровська область", "x": 700, "y": 480},
    "donetsk": {"name": "Донецька область", "x": 820, "y": 510},
    "luhansk": {"name": "Луганська область", "x": 930, "y": 420},
    # Південь
    "odesa": {"name": "Одеська область", "x": 420, "y": 560},
    "mykolaiv": {"name": "Миколаївська область", "x": 510, "y": 430},
    "kherson": {"name": "Херсонська область", "x": 610, "y": 480},
    "zaporizhzhia": {"name": "Запорізька область", "x": 740, "y": 440},
    "crimea": {"name": "АР Крим", "x": 680, "y": 580},
}

# Стан тривог
regions_state = {
    reg_key: {"is_alert": False, "start_time": None} for reg_key in REGIONS
}

# --- РОБОТА З ФАЙЛОМ КОРИСТУВАЧІВ ---


def load_user_regions() -> dict:
    if os.path.exists(USERS_FILE):
        try:
            with open(USERS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                return {int(chat_id): str(reg) for chat_id, reg in data.items()}
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


def is_night_mode() -> bool:
    now = datetime.now().time()
    return now >= time(23, 0) or now < time(7, 0)


def get_regions_keyboard() -> InlineKeyboardMarkup:
    buttons = []
    keys = list(REGIONS.keys())
    for i in range(0, len(keys), 2):
        row = [
            InlineKeyboardButton(
                text=REGIONS[keys[i]]["name"], callback_data=f"set_reg_{keys[i]}"
            )
        ]
        if i + 1 < len(keys):
            row.append(
                InlineKeyboardButton(
                    text=REGIONS[keys[i + 1]]["name"],
                    callback_data=f"set_reg_{keys[i + 1]}",
                )
            )
        buttons.append(row)
    return InlineKeyboardMarkup(inline_keyboard=buttons)


# --- ФУНКЦІЯ АНАЛІЗУ КОЛЬОРУ ПІКСЕЛЯ ---


def is_pixel_red(r: int, g: int, b: int) -> bool:
    """Визначає, чи є піксель червоним (повітряна тривога)."""
    return r > 130 and g < 60 and b < 60


async def check_alerts_by_image_loop():
    """Фоновий цикл: завантажує карту з GitHub і перевіряє пікселі."""
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        )
    }

    async with aiohttp.ClientSession(headers=headers) as session:
        while True:
            try:
                async with session.get(MAP_IMAGE_URL) as response:
                    if response.status == 200:
                        image_data = await response.read()
                        img = Image.open(BytesIO(image_data)).convert("RGB")

                        now = datetime.now()
                        disable_sound = is_night_mode()

                        for reg_key, reg_info in REGIONS.items():
                            x, y = reg_info["x"], reg_info["y"]

                            try:
                                r, g, b = img.getpixel((x, y))
                                is_alert_now = is_pixel_red(r, g, b)
                            except IndexError:
                                continue

                            st = regions_state[reg_key]

                            # 🚨 ПОЧАТОК ТРИВОГИ
                            if is_alert_now and not st["is_alert"]:
                                st["is_alert"] = True
                                st["start_time"] = now

                                text = (
                                    f"🚨 <b>ПОЧАТОК ТРИВОГИ!</b>\n\n"
                                    f"📍 <b>Регіон:</b> {reg_info['name']}\n"
                                    f"🕒 <b>Час:</b> {now.strftime('%H:%M')}\n\n"
                                    f"Прямуйте в укриття! 🛡️"
                                )
                                await send_to_subscribers(
                                    reg_key, text, disable_sound
                                )

                            # 🟢 ВІДБІЙ ТРИВОГИ
                            elif not is_alert_now and st["is_alert"]:
                                st["is_alert"] = False
                                text = (
                                    f"✅ <b>ВІДБІЙ ТРИВОГИ!</b>\n\n"
                                    f"📍 <b>Регіон:</b> {reg_info['name']}\n"
                                    f"🕒 <b>Час:</b> {now.strftime('%H:%M')}\n\n"
                                    f"Можна залишати укриття. 🟢"
                                )
                                await send_to_subscribers(
                                    reg_key, text, disable_sound
                                )
                    else:
                        logging.warning(
                            f"Не вдалося завантажити карту з GitHub. Статус: {response.status}"
                        )
            except Exception as e:
                logging.error(f"Помилка обробки зображення карти: {e}")

            await asyncio.sleep(CHECK_INTERVAL)


async def send_to_subscribers(reg_key: str, text: str, disable_sound: bool):
    for chat_id, user_reg in list(user_regions.items()):
        if user_reg == reg_key:
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
    chat_id = message.chat.id
    if chat_id not in user_regions:
        user_regions[chat_id] = "kyiv_city"
        save_user_regions()

    await message.answer(
        "Вітаю! Оберіть свій регіон для відстеження тривоги за картою:",
        reply_markup=get_regions_keyboard(),
    )


@dp.callback_query(F.data.startswith("set_reg_"))
async def cb_set_region(callback: CallbackQuery):
    reg_key = callback.data.replace("set_reg_", "")
    user_regions[callback.message.chat.id] = reg_key
    save_user_regions()

    reg_name = REGIONS.get(reg_key, {}).get("name", "Невідомий")

    await callback.message.edit_text(
        f"✅ Регіон успішно змінено на: <b>{reg_name}</b>\n\n"
        "Доступні команди:\n"
        "• <b>статус</b> — поточний стан тривоги\n"
        "• <b>регіон</b> — змінити область/місто",
        parse_mode=ParseMode.HTML,
    )


@dp.message(F.text.lower() == "регіон")
async def cmd_change_region(message: Message):
    await message.answer(
        "Оберіть новий регіон зі списку:", reply_markup=get_regions_keyboard()
    )


@dp.message(F.text.lower() == "статус")
async def cmd_status(message: Message):
    reg_key = user_regions.get(message.chat.id, "kyiv_city")
    reg_info = REGIONS.get(reg_key, REGIONS["kyiv_city"])
    st = regions_state.get(reg_key, {"is_alert": False, "start_time": None})

    if st["is_alert"]:
        now = datetime.now()
        start = st["start_time"] or now
        duration = now - start
        minutes_passed = int(duration.total_seconds() // 60)

        text = (
            f"📍 <b>{reg_info['name']}</b>\n\n"
            f"🔴 <b>Триває повітряна тривога!</b>\n"
            f"🕒 <b>Початок:</b> {start.strftime('%H:%M')}\n"
            f"⏱ <b>Минуло:</b> {minutes_passed} хв"
        )
    else:
        text = (
            f"📍 <b>{reg_info['name']}</b>\n\n"
            f"🟢 <b>Повітряної тривоги немає.</b>\n"
            f"У вашому регіоні наразі спокійно."
        )

    await message.answer(text, parse_mode=ParseMode.HTML)


# --- ФІКТИВНИЙ ВЕБ-СЕРВЕР ДЛЯ RENDER ---


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
    asyncio.create_task(check_alerts_by_image_loop())
    await start_web_server()
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
