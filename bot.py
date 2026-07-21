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

# Посилання на пряме зображення карти (PNG або JPG)
MAP_IMAGE_URL = "https://alerts.in.ua/map.png"

CHECK_INTERVAL = 15  # Інтервал перевірки карти у секундах
USERS_FILE = "users.json"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(name)s - %(message)s",
)

bot = Bot(token=TELEGRAM_BOT_TOKEN)
dp = Dispatcher()

# База регіонів з координатами (X, Y) пікселя на зображенні розміром, наприклад, 1200x800
# 📌 Скоригуй координати під конкретний розмір вашої карти!
REGIONS = {
    "kiev": {"name": "м. Київ", "x": 580, "y": 290},
    "kiev_obl": {"name": "Київська область", "x": 600, "y": 320},
    "kharkiv": {"name": "Харківська область", "x": 920, "y": 310},
    "odesa": {"name": "Одеська область", "x": 530, "y": 620},
    "dnipro": {"name": "Дніпропетровська область", "x": 780, "y": 450},
    "lviv": {"name": "Львівська область", "x": 210, "y": 330},
}

# Стан тривог по регіонах: {reg_key: {"is_alert": bool, "start_time": datetime}}
regions_state = {
    reg_key: {"is_alert": False, "start_time": None} for reg_key in REGIONS
}

# --- ЗБЕРЕЖЕННЯ КОРИСТУВАЧІВ ---


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
        logging.error(f"Помилка запису в {USERS_FILE}: {e}")


user_regions = load_user_regions()


def is_night_mode() -> bool:
    now = datetime.now().time()
    return now >= time(23, 0) or now < time(7, 0)


def get_regions_keyboard() -> InlineKeyboardMarkup:
    buttons = []
    for reg_key, reg_data in REGIONS.items():
        buttons.append(
            [
                InlineKeyboardButton(
                    text=reg_data["name"], callback_data=f"set_reg_{reg_key}"
                )
            ]
        )
    return InlineKeyboardMarkup(inline_keyboard=buttons)


# --- ФУНКЦІЯ АНАЛІЗУ КОЛЬОРУ ПІКСЕЛЯ ---


def is_pixel_red(r: int, g: int, b: int) -> bool:
    """Визначає, чи є колір пікселя (RGB) 'червоним' (сигналізує про тривогу)."""
    return r > 160 and g < 100 and b < 100


async def check_alerts_by_image_loop():
    """Фоновий цикл: завантажує карту та перевіряє пікселі для кожного регіону."""
    async with aiohttp.ClientSession() as session:
        while True:
            try:
                async with session.get(MAP_IMAGE_URL) as response:
                    if response.status == 200:
                        image_data = await response.read()
                        # Відкриваємо зображення та конвертуємо в RGB
                        img = Image.open(BytesIO(image_data)).convert("RGB")

                        now = datetime.now()
                        disable_sound = is_night_mode()

                        for reg_key, reg_info in REGIONS.items():
                            x, y = reg_info["x"], reg_info["y"]

                            # Отримуємо значення RGB пікселя за координатами (x, y)
                            r, g, b = img.getpixel((x, y))
                            is_alert_now = is_pixel_red(r, g, b)

                            st = regions_state[reg_key]

                            # 🚨 ПОЧАТОК ТРИВОГИ (піксель став червоним)
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

                            # 🟢 ВІДБІЙ ТРИВОГИ (піксель перестав бути червоним)
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
                            f"Не вдалося завантажити карту. Статус: {response.status}"
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
        user_regions[chat_id] = "kiev"
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
        "• <b>статус</b> — поточна ситуація\n"
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
    reg_key = user_regions.get(message.chat.id, "kiev")
    reg_info = REGIONS.get(reg_key, REGIONS["kiev"])
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


# --- ФІКТИВНИЙ ВЕБ-СЕРВЕР ДЛЯ RENDER WEB SERVICE ---


async def handle_health_check(request):
    return web.Response(text="Pixel Check Bot is running!")


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
