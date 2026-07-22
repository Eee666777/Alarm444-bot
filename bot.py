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
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
)
import aiohttp
from aiohttp import web
from PIL import Image

# --- НАЛАШТУВАННЯ ---
TELEGRAM_BOT_TOKEN = "8841892288:AAEvW9PrcWJ1gD4iVTAw2ouAaNu99V_P55M"

# Джерела та посилання
MAP_PAGE_URL = "https://alerts.in.ua/"
MAP_IMAGE_URL = (
    "https://github.com/Eee666777/Alarm444-bot/blob/main/chrome_proxy.exe"
)

CHECK_INTERVAL = 15  # Інтервал перевірки карти у секундах
USERS_FILE = "users.json"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(name)s - %(message)s",
)

bot = Bot(token=TELEGRAM_BOT_TOKEN)
dp = Dispatcher()

# --- КОРДИНАТИ ПІКСЕЛІВ ОБЛАСТЕЙ (1366 x 642) ---
REGIONS = {
    # Захід
    "volyn": {"name": "Волинська область", "x": 420, "y": 180},
    "rivne": {"name": "Рівненська область", "x": 480, "y": 180},
    "lviv": {"name": "Львівська область", "x": 360, "y": 300},
    "ternopil": {"name": "Тернопільська область", "x": 440, "y": 350},
    "ivano-frankivsk": {"name": "Івано-Франківська область", "x": 390, "y": 420},
    "zakarpattia": {"name": "Закарпатська область", "x": 330, "y": 450},
    "chernivtsi": {"name": "Чернівецька область", "x": 460, "y": 460},
    "khmelnytskyi": {"name": "Хмельницька область", "x": 510, "y": 320},
    # Центр та Північ
    "zhytomyr": {"name": "Житомирська область", "x": 570, "y": 230},
    "kyiv_obl": {"name": "Київська область", "x": 650, "y": 280},
    "kyiv_city": {"name": "м. Київ", "x": 645, "y": 255},
    "chernihiv": {"name": "Чернігівська область", "x": 700, "y": 160},
    "sumy": {"name": "Сумська область", "x": 800, "y": 200},
    "vinnytsia": {"name": "Вінницька область", "x": 570, "y": 400},
    "cherkasy": {"name": "Черкаська область", "x": 680, "y": 380},
    "poltava": {"name": "Полтавська область", "x": 780, "y": 310},
    "kirovohrad": {"name": "Кіровоградська область", "x": 710, "y": 440},
    # Схід
    "kharkiv": {"name": "Харківська область", "x": 900, "y": 330},
    "dnipro": {"name": "Дніпропетровська область", "x": 840, "y": 450},
    "donetsk": {"name": "Донецька область", "x": 960, "y": 480},
    "luhansk": {"name": "Луганська область", "x": 1010, "y": 380},
    # Південь
    "odesa": {"name": "Одеська область", "x": 600, "y": 550},
    "mykolaiv": {"name": "Миколаївська область", "x": 720, "y": 520},
    "kherson": {"name": "Херсонська область", "x": 790, "y": 550},
    "zaporizhzhia": {"name": "Запорізька область", "x": 890, "y": 530},
    "crimea": {"name": "АР Крим", "x": 840, "y": 620},
}

# Стан кожної області: status = 'CLEAR', 'WARNING', або 'ALERT'
regions_state = {
    reg_key: {"status": "CLEAR", "start_time": None} for reg_key in REGIONS
}

# --- ЗБЕРЕЖЕННЯ КОРИСТУВАЧІВ ТА ЇХ ОБЛАСТЕЙ (СПИСОК) ---


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
            logging.error(f"Помилка зчитування {USERS_FILE}: {e}")
    return {}


def save_user_regions():
    try:
        with open(USERS_FILE, "w", encoding="utf-8") as f:
            json.dump(user_regions, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logging.error(f"Помилка запису у {USERS_FILE}: {e}")


# Формат у файлі: {chat_id: ["kyiv_city", "lviv"]}
user_regions = load_user_regions()

# --- ПЕРЕВІРКА РЕЖИМУ ЗВУКУ ---


def is_silent_mode() -> bool:
    """З 21:00 вечора до 05:59 ранку режим без звуку (True). з 06:00 до 20:59 — зі звуком (False)."""
    now_hour = datetime.now().hour
    return now_hour >= 21 or now_hour < 6


# --- КЛАВІАТУРИ МЕНЮ ---


def get_main_menu_keyboard() -> ReplyKeyboardMarkup:
    """Головне текстове меню бота."""
    kb = [
        [
            KeyboardButton(text="👁 Перегляд підписок"),
            KeyboardButton(text="➕ Додати область"),
        ],
        [KeyboardButton(text="📊 Статус"), KeyboardButton(text="💾 Зберегти")],
    ]
    return ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)


def get_add_regions_keyboard(chat_id: int) -> InlineKeyboardMarkup:
    """Показує список областей, які користувач ще НЕ додав."""
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
        [
            InlineKeyboardButton(
                text="💾 Зберегти та завершити", callback_data="save_changes"
            )
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_subscribed_regions_keyboard(chat_id: int) -> InlineKeyboardMarkup:
    """Показує список обраних областей для подальшого вибору/видалення."""
    user_subs = user_regions.get(chat_id, [])
    buttons = []

    for reg_key in user_subs:
        reg_name = REGIONS.get(reg_key, {}).get("name", reg_key)
        buttons.append(
            [
                InlineKeyboardButton(
                    text=f"📍 {reg_name}", callback_data=f"select_sub_{reg_key}"
                )
            ]
        )

    buttons.append(
        [
            InlineKeyboardButton(
                text="💾 Зберегти зміни", callback_data="save_changes"
            )
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=buttons)


# --- ДЕТЕКЦІЯ КОЛЬОРІВ КАРТИ ---


def detect_pixel_status(r: int, g: int, b: int) -> str:
    # Жовтий / Помаранчевий (Підвищена небезпека)
    if r > 150 and g > 80 and b < 80:
        return "WARNING"
    # Червоний / Червоно-коричневий (Повітряна тривога)
    if r > 80 and r > g + 30 and r > b + 30:
        return "ALERT"
    # Спокійно
    return "CLEAR"


# --- ФОНОВИЙ ЦИКЛ ПЕРЕВІРКИ КАРТИ ---


async def check_alerts_by_image_loop():
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
                        disable_sound = (
                            is_silent_mode()
                        )  # Без звуку з 21:00 до 05:59

                        for reg_key, reg_info in REGIONS.items():
                            x, y = reg_info["x"], reg_info["y"]

                            try:
                                r, g, b = img.getpixel((x, y))
                                current_status = detect_pixel_status(r, g, b)
                            except IndexError:
                                continue

                            st = regions_state[reg_key]
                            old_status = st["status"]

                            if current_status != old_status:
                                st["status"] = current_status
                                st["start_time"] = now

                                sound_status_str = (
                                    "🔕 (Без звуку - Нічний режим)"
                                    if disable_sound
                                    else "🔊 (Зі звуком)"
                                )

                                if current_status == "ALERT":
                                    text = (
                                        f"🚨 <b>ПОЧАТОК ТРИВОГИ!</b> {sound_status_str}\n\n"
                                        f"📍 <b>Регіон:</b> {reg_info['name']}\n"
                                        f"🕒 <b>Час:</b> {now.strftime('%H:%M')}\n\n"
                                        f"Прямуйте в укриття! 🛡️\n"
                                        f"🔗 Мапа: {MAP_PAGE_URL}"
                                    )
                                    await send_to_subscribers(
                                        reg_key, text, disable_sound
                                    )

                                elif current_status == "WARNING":
                                    text = (
                                        f"⚠️ <b>ПІДВИЩЕНА НЕБЕЗПЕКА!</b> {sound_status_str}\n\n"
                                        f"📍 <b>Регіон:</b> {reg_info['name']}\n"
                                        f"🕒 <b>Час:</b> {now.strftime('%H:%M')}\n\n"
                                        f"Будьте особливо уважні! ⚠️\n"
                                        f"🔗 Мапа: {MAP_PAGE_URL}"
                                    )
                                    await send_to_subscribers(
                                        reg_key, text, disable_sound
                                    )

                                elif current_status == "CLEAR":
                                    text = (
                                        f"✅ <b>ВІДБІЙ ТРИВОГИ!</b> {sound_status_str}\n\n"
                                        f"📍 <b>Регіон:</b> {reg_info['name']}\n"
                                        f"🕒 <b>Час:</b> {now.strftime('%H:%M')}\n\n"
                                        f"Можна залишати укриття. 🟢\n"
                                        f"🔗 Мапа: {MAP_PAGE_URL}"
                                    )
                                    await send_to_subscribers(
                                        reg_key, text, disable_sound
                                    )
                    else:
                        logging.warning(
                            f"Завантаження карти повернуло статус: {response.status}"
                        )
            except Exception as e:
                logging.error(f"Помилка фонової перевірки: {e}")

            await asyncio.sleep(CHECK_INTERVAL)


async def send_to_subscribers(reg_key: str, text: str, disable_sound: bool):
    """Надсилає повідомлення лише тим користувачам, які підписані на дану область."""
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
                logging.error(f"Помилка відправки в chat_id {chat_id}: {e}")


# --- ОБРОБНИКИ КОМАНД І КНОПОК ---


@dp.message(CommandStart())
async def cmd_start(message: Message):
    chat_id = message.chat.id
    if chat_id not in user_regions:
        user_regions[chat_id] = ["kyiv_city"]  # За замовчуванням
        save_user_regions()

    await message.answer(
        "Вітаю! Оберіть необхідну дію в меню нижче.\n\n"
        "⏰ <b>Режим сповіщень:</b>\n"
        "• 06:00 - 20:59 — 🔊 <b>Зі звуком</b>\n"
        "• 21:00 - 05:59 — 🔕 <b>Без звуку (Нічний режим)</b>",
        reply_markup=get_main_menu_keyboard(),
        parse_mode=ParseMode.HTML,
    )


# 1. ПЕРЕГЛЯД ПІДПИСОК
@dp.message(F.text == "👁 Перегляд підписок")
async def cmd_view_subscriptions(message: Message):
    chat_id = message.chat.id
    subs = user_regions.get(chat_id, [])

    if not subs:
        await message.answer(
            "У вас немає обраних областей. Натисніть '➕ Додати область'.",
            reply_markup=get_main_menu_keyboard(),
        )
        return

    await message.answer(
        "Ваші обрані області (натисніть на область, щоб видалити її):",
        reply_markup=get_subscribed_regions_keyboard(chat_id),
    )


# 2. ДОДАТИ ОБЛАСТЬ
@dp.message(F.text == "➕ Додати область")
async def cmd_add_region(message: Message):
    chat_id = message.chat.id
    kb = get_add_regions_keyboard(chat_id)

    if not kb.inline_keyboard or len(kb.inline_keyboard) == 1:
        await message.answer("Ви вже підписані на всі доступні області!")
        return

    await message.answer(
        "Оберіть область зі списку, яку бажаєте додати:", reply_markup=kb
    )


# Додавання області через інлайн кнопу
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
    # Оновлюємо список кнопок без уже доданої області
    await callback.message.edit_text(
        f"✅ Додано <b>{reg_name}</b>.\nМожете обрати ще або натиснути 'Зберегти':",
        reply_markup=get_add_regions_keyboard(chat_id),
        parse_mode=ParseMode.HTML,
    )


# Вибір області з обраних для видалення
@dp.callback_query(F.data.startswith("select_sub_"))
async def cb_select_sub(callback: CallbackQuery):
    reg_key = callback.data.replace("select_sub_", "")
    reg_name = REGIONS.get(reg_key, {}).get("name", reg_key)

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=f"❌ Видалити {reg_name}",
                    callback_data=f"delete_reg_{reg_key}",
                )
            ],
            [
                InlineKeyboardButton(
                    text="🔙 Назад до списку", callback_data="back_to_subs"
                )
            ],
        ]
    )

    await callback.message.edit_text(
        f"Ви обрали область: <b>{reg_name}</b>\nБажаєте видалити її з підписок?",
        reply_markup=kb,
        parse_mode=ParseMode.HTML,
    )


# Видалення області
@dp.callback_query(F.data.startswith("delete_reg_"))
async def cb_delete_region(callback: CallbackQuery):
    chat_id = callback.message.chat.id
    reg_key = callback.data.replace("delete_reg_", "")

    if chat_id in user_regions and reg_key in user_regions[chat_id]:
        user_regions[chat_id].remove(reg_key)

    reg_name = REGIONS.get(reg_key, {}).get("name", reg_key)
    await callback.answer(f"Видалено: {reg_name}")

    await callback.message.edit_text(
        f"❌ Область <b>{reg_name}</b> видалено.\nОновлений список підписок:",
        reply_markup=get_subscribed_regions_keyboard(chat_id),
        parse_mode=ParseMode.HTML,
    )


# Повернення до списку підписок
@dp.callback_query(F.data == "back_to_subs")
async def cb_back_to_subs(callback: CallbackQuery):
    chat_id = callback.message.chat.id
    await callback.message.edit_text(
        "Ваші обрані області:",
        reply_markup=get_subscribed_regions_keyboard(chat_id),
    )


# 3. КНОПКА ЗБЕРЕГТИ
@dp.message(F.text == "💾 Зберегти")
@dp.callback_query(F.data == "save_changes")
async def save_and_confirm(event):
    chat_id = (
        event.chat.id if isinstance(event, Message) else event.message.chat.id
    )

    save_user_regions()

    subs = user_regions.get(chat_id, [])
    names = [REGIONS.get(k, {}).get("name", k) for k in subs]

    text_msg = (
        "💾 <b>Налаштування успішно збережено!</b>\n\n"
        "Ваш актуальний список областей для сповіщень:\n"
        + ("\n".join([f"• {name}" for name in names]) if names else "<i>Список порожній</i>")
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


# 4. СТАТУС ОБРАНИХ ОБЛАСТЕЙ
@dp.message(F.text == "📊 Статус")
async def cmd_status(message: Message):
    chat_id = message.chat.id
    subs = user_regions.get(chat_id, [])

    if not subs:
        await message.answer(
            "У вас не обрано жодної області. Додайте області за допомогою '➕ Додати область'."
        )
        return

    now = datetime.now()
    text_lines = ["📊 <b>ПОТОЧНИЙ СТАН ВАШИХ ОБЛАСТЕЙ:</b>\n"]

    for reg_key in subs:
        reg_info = REGIONS.get(reg_key, {"name": reg_key})
        st = regions_state.get(
            reg_key, {"status": "CLEAR", "start_time": None}
        )

        start = st["start_time"] or now
        minutes_passed = int((now - start).total_seconds() // 60)

        if st["status"] == "ALERT":
            text_lines.append(
                f"🔴 <b>{reg_info['name']}</b>: Тривога! (триває {minutes_passed} хв)"
            )
        elif st["status"] == "WARNING":
            text_lines.append(
                f"⚠️ <b>{reg_info['name']}</b>: Підвищена небезпека!"
            )
        else:
            text_lines.append(f"🟢 <b>{reg_info['name']}</b>: Спокійно")

    text_lines.append(f"\n🔗 Карта онлайн: {MAP_PAGE_URL}")
    await message.answer("\n".join(text_lines), parse_mode=ParseMode.HTML)


# --- ФІКТИВНИЙ ВЕБ-СЕРВЕР ДЛЯ RENDER ---


async def handle_health_check(request):
    return web.Response(text="Bot with Multi-Region support is running!")


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
