import asyncio
from datetime import datetime, time
import logging
import os
from aiogram import Bot, Dispatcher, F
from aiogram.enums import ParseMode
from aiogram.filters import CommandStart
from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    WebAppInfo,
)
from aiohttp import web

# --- НАЛАШТУВАННЯ ---
TELEGRAM_BOT_TOKEN = "8841892288:AAHYKuht11w8JzQ21RkdRr_VHgwvdtvMaWA"

# ID регіону та назва (для відображення у текстах)
REGION_NAME = "м. Київ"

# Посилання на онлайн-карту тривог
MAP_URL = "https://alerts.in.ua/"

logging.basicConfig(level=logging.INFO)
bot = Bot(token=TELEGRAM_BOT_TOKEN)
dp = Dispatcher()

# Внутрішній стан бота (у тестовому режимі)
state = {
    "is_alert": False,
    "start_time": None,
    "last_alert_start": None,
    "last_alert_end": None,
    "subscribers": set(),
}


def is_night_mode() -> bool:
    """Перевіряє, чи зараз нічний час (23:00 - 07:00)."""
    now = datetime.now().time()
    return now >= time(23, 0) or now < time(7, 0)


# --- ОБРОБНИКИ КОМАНД ТА ТЕКСТУ ---


@dp.message(CommandStart())
async def cmd_start(message: Message):
    """Автоматично підписує користувача на сповіщення при старті."""
    state["subscribers"].add(message.chat.id)
    await message.answer(
        f"Вітаю! Я повідомлятиму про повітряні тривоги у регіоні <b>{REGION_NAME}</b>.\n\n"
        "Доступні команди:\n"
        "• <b>карта</b> — відкрити онлайн-карту\n"
        "• <b>статус</b> — перевірити поточний стан",
        parse_mode=ParseMode.HTML,
    )


@dp.message(F.text.lower() == "карта")
async def cmd_map(message: Message):
    """Відповідає на слово 'карта' та додає кнопку WebApp."""
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🗺️ Відкрити карту", web_app=WebAppInfo(url=MAP_URL)
                )
            ]
        ]
    )
    await message.answer("🗺️ Відкрити карту України", reply_markup=keyboard)


@dp.message(F.text.lower() == "статус")
async def cmd_status(message: Message):
    """Формує та надсилає поточний статус тривоги."""
    if state["is_alert"]:
        now = datetime.now()
        start = state["start_time"] or now
        duration = now - start
        minutes_passed = int(duration.total_seconds() // 60)

        text = (
            f"📍 <b>{REGION_NAME}</b>\n\n"
            f"🔴 <b>Зараз триває повітряна тривога.</b>\n"
            f"🕒 Початок: {start.strftime('%H:%M')}\n\n"
            f"⏱ Минуло: {minutes_passed} хв"
        )
    else:
        last_start_str = (
            state["last_alert_start"].strftime("%H:%M")
            if state["last_alert_start"]
            else "--:--"
        )
        last_end_str = (
            state["last_alert_end"].strftime("%H:%M")
            if state["last_alert_end"]
            else "--:--"
        )
        date_str = (
            state["last_alert_end"].strftime("%d.%m.%Y")
            if state["last_alert_end"]
            else datetime.now().strftime("%d.%m.%Y")
        )

        text = (
            f"📍 <b>{REGION_NAME}</b>\n\n"
            f"🟢 <b>Повітряної тривоги немає.</b>\n\n"
            f"Остання тривога:\n"
            f"{date_str}\n"
            f"{last_start_str}–{last_end_str}"
        )

    await message.answer(text, parse_mode=ParseMode.HTML)


# --- ФІКТИВНИЙ ВЕБ-СЕРВЕР ДЛЯ RENDER WEB SERVICE ---


async def handle_health_check(request):
    """Простий ендпоінт, щоб Render бачив, що сервіс працює."""
    return web.Response(text="Bot is running in test mode!")


async def start_web_server():
    """Запуск сервера на порту, який вимагає Render."""
    app = web.Application()
    app.router.add_get("/", handle_health_check)
    runner = web.AppRunner(app)
    await runner.setup()

    port = int(os.environ.get("PORT", 10000))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()


async def main():
    # Запускаємо міні-сервер для Web Service Render
    await start_web_server()
    # Запускаємо бота
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
