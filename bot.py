import asyncio
from datetime import datetime
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

# Посилання на оперативну карту або рендер карти
MAP_IMAGE_URL = "https://alerts.in.ua/box/map.png"

CHECK_INTERVAL = 20  # Інтервал перевірки карти (у секундах)
USERS_FILE = "users.json"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(name)s - %(message)s",
)

bot = Bot(token=TELEGRAM_BOT_TOKEN)
dp = Dispatcher()

# --- КООРДИНАТИ ПІКСЕЛІВ ДЛЯ КАРТИ ---
REGIONS = {
    # Захід
    "volyn": {"name": "Волинська область", "x": 120, "y": 80},
    "rivne": {"name": "Рівненська область", "x": 180, "y": 80},
    "lviv": {"name": "Львівська область", "x": 80, "y": 160},
    "ternopil": {"name": "Тернопільська область", "x": 140, "y": 180},
    "ivano-frankivsk": {"name": "Івано-Франківська область", "x": 100, "y": 230},
    "zakarpattia": {"name": "Закарпатська область", "x": 50, "y": 240},
    "chernivtsi": {"name": "Чернівецька область", "x": 160, "y": 250},
    "khmelnytskyi": {"name": "Хмельницька область", "x": 200, "y": 170},
    # Центр та Північ
    "zhytomyr": {"name": "Житомирська область", "x": 260, "y": 120},
    "kyiv_obl": {"name": "Київська область", "x": 330, "y": 140},
    "kyiv_city": {"name": "м. Київ", "x": 325, "y": 125},
    "chernihiv": {"name": "Чернігівська область", "x": 370, "y": 70},
    "sumy": {"name": "Сумська область", "x": 460, "y": 90},
    "vinnytsia": {"name": "Вінницька область", "x": 260, "y": 220},
    "cherkasy": {"name": "Черкаська область", "x": 360, "y": 200},
    "poltava": {"name": "Полтавська область", "x": 450, "y": 180},
    "kirovohrad": {"name": "Кіровоградська область", "x": 380, "y": 260},
    # Схід
    "kharkiv": {"name": "Харківська область", "x": 550, "y": 190},
    "dnipro": {"name": "Дніпропетровська область", "x": 490, "y": 270},
    "donetsk": {"name": "Донецька область", "x": 600, "y": 300},
    "luhansk": {"name": "Луганська область", "x": 650, "y": 220},
    # Південь
    "odesa": {"name": "Одеська область", "x": 290, "y": 350},
    "mykolaiv": {"name": "Миколаївська область", "x": 390, "y": 330},
    "kherson": {"name": "Херсонська область", "x": 450, "y": 360},
    "zaporizhzhia": {"name": "Запорізька область", "x": 530, "y": 340},
    "crimea": {"name": "АР Крим", "x": 490, "y": 440},
}

# Поточний стан та час початку небезпеки для кожної області
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
    """З 21:00 до 05:59 ранку без звуку, з 06:00 до 20:59 зі звуком."""
    now_hour = datetime.now().hour
    return now_hour >= 21 or now_hour < 6


# --- АНАЛІЗ КОЛЬОРУ ПІКСЕЛЯ ---


def detect_pixel_status(r: int, g: int, b: int) -> str:
    """
    Червоне / Бордове -> ALERT (Тривога)
    Жовте / Помаранчеве -> WARNING (Підвищена небезпека)
    Інше -> CLEAR (Відбій)
    """
    # Червоне або бордове (R превалює над G та B)
    if r > 120 and r > g + 40 and r > b + 40:
        return "ALERT"

    # Жовте або помаранчеве (Високий R та G, але низький B)
    if r > 160 and g > 100 and b < 100:
        return "WARNING"

    return "CLEAR"


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
        [
            InlineKeyboardButton(
                text="💾 Зберегти та завершити", callback_data="save_changes"
            )
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_subscribed_regions_keyboard(chat_id: int) -> InlineKeyboardMarkup:
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


# --- ФОРМАТУВАННЯ ЧАСУ ---


def format_duration(start_time: datetime, end_time: datetime) -> str:
    delta_seconds = int((end_time - start_time).total_seconds())
    hours = delta_seconds // 3600
    minutes = (delta_seconds % 3600) // 60

    if hours > 0:
        return f"{hours} год {minutes} хв"
    return f"{minutes} хв"


# --- ЦИКЛ МОНІТОРИНГУ ТРИВОГ ---


async def check_alerts_loop():
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        )
    }

    connector = aiohttp.TCPConnector(ssl=False)
    async with aiohttp.ClientSession(
        connector=connector, headers=headers
    ) as session:
        while True:
            try:
                async with session.get(MAP_IMAGE_URL, timeout=12) as resp:
                    if resp.status == 200:
                        image_data = await resp.read()
                        img = Image.open(BytesIO(image_data)).convert("RGB")

                        now = datetime.now()
                        disable_sound = is_silent_mode()

                        for reg_key, reg_info in REGIONS.items():
                            x, y = reg_info["x"], reg_info["y"]

                            try:
                                r, g, b = img.getpixel((x, y))
                                current_status = detect_pixel_status(r, g, b)
                            except IndexError:
                                continue

                            st = regions_state[reg_key]
                            old_status = st["status"]

                            # Якщо стан області змінився
                            if current_status != old_status:
                                sound_str = (
                                    "🔕 (Без звуку)"
                                    if disable_sound
                                    else "🔊 (Зі звуком)"
                                )

                                # 1. ПОЧАТОК ТРИВОГИ (Червоне / Бордове)
                                if current_status == "ALERT":
                                    st["start_time"] = now
                                    st["status"] = current_status

                                    text = (
                                        f"🚨 <b>ПОЧАТОК ТРИВОГИ!</b> {sound_str}\n\n"
                                        f"📍 <b>Регіон:</b> {reg_info['name']}\n"
                                        f"🕒 <b>Початок:</b> {now.strftime('%H:%M:%S')}\n\n"
                                        f"Прямуйте в укриття! 🛡️"
                                    )
                                    await send_to_subscribers(
                                        reg_key, text, disable_sound
                                    )

                                # 2. ПІДВИЩЕНА НЕБЕЗПЕКА (Жовте / Помаранчеве)
                                elif current_status == "WARNING":
                                    if not st["start_time"]:
                                        st["start_time"] = now
                                    st["status"] = current_status

                                    text = (
                                        f"⚠️ <b>ПІДВИЩЕНА НЕБЕЗПЕКА!</b> {sound_str}\n\n"
                                        f"📍 <b>Регіон:</b> {reg_info['name']}\n"
                                        f"🕒 <b>Час:</b> {now.strftime('%H:%M:%S')}\n\n"
                                        f"Будьте уважні та обережні! ⚠️"
                                    )
                                    await send_to_subscribers(
                                        reg_key, text, disable_sound
                                    )

                                # 3. ВІДБІЙ ТРИВОГИ (Ні червоного, ні жовтого)
                                elif current_status == "CLEAR":
                                    start_time = st["start_time"] or now
                                    end_time = now
                                    duration_str = format_duration(
                                        start_time, end_time
                                    )

                                    text = (
                                        f"✅ <b>ВІДБІЙ ТРИВОГИ!</b> {sound_str}\n\n"
                                        f"📍 <b>Регіон:</b> {reg_info['name']}\n"
                                        f"🛫 <b>Початок:</b> {start_time.strftime('%H:%M')}\n"
                                        f"🛬 <b>Кінець:</b> {end_time.strftime('%H:%M')}\n"
                                        f"⏱ <b>Тривалість:</b> {duration_str}\n\n"
                                        f"Загроза минула. 🟢"
                                    )

                                    # Скидаємо стан
                                    st["status"] = "CLEAR"
                                    st["start_time"] = None

                                    await send_to_subscribers(
                                        reg_key, text, disable_sound
                                    )

                    else:
                        logging.warning(
                            f"Завантаження карти повернуло статус: {resp.status}"
                        )

            except Exception as e:
                logging.error(f"Помилка під час аналізу карти: {e}")

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
                logging.error(
                    f"Не вдалося надіслати повідомлення {chat_id}: {e}"
                )


# --- ОБРОБНИКИ КОМАНД ТА КНОПОК ---


@dp.message(CommandStart())
async def cmd_start(message: Message):
    chat_id = message.chat.id
    if chat_id not in user_regions:
        user_regions[chat_id] = ["kyiv_city"]
        save_user_regions()

    await message.answer(
        "Вітаю! Бот готовий до моніторингу тривог за допомогою аналізу кольорів карти.\n\n"
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
        await message.answer(
            "У вас немає обраних областей. Натисніть '➕ Додати область'."
        )
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
    chat_id = (
        event.chat.id if isinstance(event, Message) else event.message.chat.id
    )
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
        st = regions_state.get(
            reg_key, {"status": "CLEAR", "start_time": None}
        )

        if st["status"] == "ALERT":
            start = st["start_time"] or now
            duration = format_duration(start, now)
            text_lines.append(
                f"🔴 <b>{reg_info['name']}</b>: Тривога! (триває {duration})"
            )
        elif st["status"] == "WARNING":
            text_lines.append(
                f"⚠️ <b>{reg_info['name']}</b>: Підвищена небезпека!"
            )
        else:
            text_lines.append(f"🟢 <b>{reg_info['name']}</b>: Спокійно")

    await message.answer("\n".join(text_lines), parse_mode=ParseMode.HTML)


# --- ВЕБ-СЕРВЕР ДЛЯ RENDER (HEALTH CHECK) ---


async def handle_health_check(request):
    return web.Response(text="Radar Bot active!")


async def start_web_server():
    app = web.Application()
    app.router.add_get("/", handle_health_check)
    runner = web.AppRunner(app)
    await runner.setup()

    port = int(os.environ.get("PORT", 10000))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()


# --- ГОЛОВНА ФУНКЦІЯ ---


async def main():
    asyncio.create_task(check_alerts_loop())
    await start_web_server()
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
