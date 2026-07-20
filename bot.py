import asyncio
from datetime import datetime, time
import logging
from aiogram import Bot, Dispatcher, F
from aiogram.enums import ParseMode
from aiogram.filters import CommandStart
from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    WebAppInfo,
)
import aiohttp

# --- НАЛАШТУВАННЯ ---
TELEGRAM_BOT_TOKEN = "ВАШ_TELEGRAM_BOT_TOKEN"
ALERTS_API_TOKEN = "ВАШ_ALERTS_IN_UA_TOKEN"

# ID регіону згідно з API alerts.in.ua ("31" - м. Київ, "10" - Київська область)
REGION_ID = "31"
REGION_NAME = "м. Київ"

# Посилання на онлайн-карту тривог
MAP_URL = "https://alerts.in.ua/"

# Інтервал перевірки API (у секундах)
CHECK_INTERVAL = 10

# Налаштування логування та бота
logging.basicConfig(level=logging.INFO)
bot = Bot(token=TELEGRAM_BOT_TOKEN)
dp = Dispatcher()

# Внутрішній стан бота
state = {
    "is_alert": False,
    "start_time": None,
    "last_alert_start": None,
    "last_alert_end": None,
    "subscribers": set(),
    "pinned_messages": {},
}


def is_night_mode() -> bool:
    """Перевіряє, чи зараз нічний час (23:00 - 07:00)."""
    now = datetime.now().time()
    return now >= time(23, 0) or now < time(7, 0)


async def check_alerts_loop():
    """Фонова задача для періодичної перевірки стану тривоги."""
    headers = {"Authorization": f"Bearer {ALERTS_API_TOKEN}"}
    url = f"https://alerts.in.ua/api/v1/alerts/{REGION_ID}.json"

    async with aiohttp.ClientSession() as session:
        while True:
            try:
                async with session.get(url, headers=headers) as response:
                    if response.status == 200:
                        data = await response.json()
                        current_alert = len(data.get("alerts", [])) > 0
                        await process_alert_change(current_alert)
            except Exception as e:
                logging.error(f"Помилка під час запиту до API: {e}")

            await asyncio.sleep(CHECK_INTERVAL)


async def process_alert_change(current_alert: bool):
    """Обробляє зміну стану тривоги та сповіщає користувачів."""
    now = datetime.now()

    # ПОЧАТОК ТРИВОГИ
    if current_alert and not state["is_alert"]:
        state["is_alert"] = True
        state["start_time"] = now
        state["last_alert_start"] = now

        disable_sound = is_night_mode()

        for chat_id in list(state["subscribers"]):
            try:
                msg = await bot.send_message(
                    chat_id,
                    "🚨 <b>Початок тривоги.</b>",
                    parse_mode=ParseMode.HTML,
                    disable_notification=disable_sound,
                )
                await bot.pin_chat_message(
                    chat_id, msg.message_id, disable_notification=True
                )
                state["pinned_messages"][chat_id] = msg.message_id
            except Exception as e:
                logging.error(
                    f"Не вдалося надіслати/закріпити у чат {chat_id}: {e}"
                )

    # ВІДБІЙ ТРИВОГИ
    elif not current_alert and state["is_alert"]:
        state["is_alert"] = False
        state["last_alert_end"] = now

        disable_sound = is_night_mode()

        for chat_id in list(state["subscribers"]):
            try:
                if chat_id in state["pinned_messages"]:
                    await bot.unpin_chat_message(
                        chat_id, state["pinned_messages"][chat_id]
                    )
                    del state["pinned_messages"][chat_id]

                await bot.send_message(
                    chat_id,
                    "✅ <b>Відбій тривоги.</b>",
                    parse_mode=ParseMode.HTML,
                    disable_notification=disable_sound,
                )
            except Exception as e:
                logging.error(
                    f"Не вдалося надіслати/відкріпити у чат {chat_id}: {e}"
                )


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
        start = state["start_time"]
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


async def main():
    # Запускаємо фонову перевірку тривог
    asyncio.create_task(check_alerts_loop())
    # Запускаємо бота
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
