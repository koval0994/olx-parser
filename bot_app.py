import asyncio
import json
import logging
import os
import aiosqlite
from curl_cffi.requests import AsyncSession
from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart
from aiogram.types import Message, CallbackQuery, ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application
from aiohttp import web

# --- НАСТРОЙКИ (Берутся из переменных Render) ---
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "8407733703:AAHHNco7EI3ZB1i83ug742EbxiIzUuF3Pnc")
WEBHOOK_URL = os.getenv("WEBHOOK_URL", "") # URL от Render
PORT = int(os.getenv("PORT", 10000))       # Порт от Render
ADMIN_ID = 1897986722
DB_PATH = "bot_data.db"
DELAY_BETWEEN_REQUESTS = 2.0

logging.basicConfig(level=logging.INFO)
bot = Bot(token=TELEGRAM_TOKEN)
dp = Dispatcher(storage=MemoryStorage())
is_monitoring_active = True
parse_queue = asyncio.Queue()

# --- СОСТОЯНИЯ ---
class Form(StatesGroup):
    add_group_name = State()
    add_model_name = State()
    add_model_prices = State()

# --- СТОП-СЛОВА И ПРЕСЕТЫ ---
TITLE_STOP_WORDS = ["чехол", "стекло", "кабель", "зарядка", "запчасти", "донор", "коробка"]
DESC_STOP_WORDS = ["на запчасти", "разбит", "трещина", "icloud", "заблокирован", "mdm", "r-sim"]

PRESETS = {
    "iphone_13": {
        "name": "iPhone 13 Series",
        "items": [{"query": "iphone 13", "min": 12000, "max": 18000}]
    },
    "streetwear_hype": {
        "name": "Streetwear (BAPE, Denim Tears)",
        "items": [
            {"query": "bape", "min": 500, "max": 6000},
            {"query": "denim tears", "min": 1000, "max": 8000}
        ]
    }
}

# --- БАЗА ДАННЫХ ---
async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("CREATE TABLE IF NOT EXISTS subscribers (user_id INTEGER PRIMARY KEY)")
        await db.execute("CREATE TABLE IF NOT EXISTS user_groups (user_id INTEGER PRIMARY KEY, groups_json TEXT)")
        await db.execute("CREATE TABLE IF NOT EXISTS seen_offers (user_id INTEGER, offer_url TEXT, PRIMARY KEY (user_id, offer_url))")
        await db.commit()
        await db.execute("INSERT OR IGNORE INTO subscribers (user_id) VALUES (?)", (ADMIN_ID,))
        await db.commit()

async def get_all_subscribers():
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT user_id FROM subscribers") as cursor:
            return [r[0] for r in await cursor.fetchall()]

async def get_user_groups(user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT groups_json FROM user_groups WHERE user_id = ?", (user_id,)) as cursor:
            row = await cursor.fetchone()
            return json.loads(row[0]) if row and row[0] else []

async def save_user_groups(user_id: int, groups: list):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT INTO user_groups (user_id, groups_json) VALUES (?, ?) ON CONFLICT(user_id) DO UPDATE SET groups_json = excluded.groups_json", (user_id, json.dumps(groups, ensure_ascii=False)))
        await db.commit()

async def is_seen(user_id: int, offer_url: str) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT 1 FROM seen_offers WHERE user_id = ? AND offer_url = ?", (user_id, offer_url)) as cursor:
            return await cursor.fetchone() is not None

async def add_seen(user_id: int, offer_url: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT OR IGNORE INTO seen_offers (user_id, offer_url) VALUES (?, ?)", (user_id, offer_url))
        await db.commit()

# --- ИНТЕРФЕЙС БОТА ---
def main_kb():
    return ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text="🟢 Вкл" if is_monitoring_active else "🔴 Выкл"), KeyboardButton(text="📂 Мои группы")],
        [KeyboardButton(text="➕ Добавить пресет")]
    ], resize_keyboard=True)

@dp.message(CommandStart())
async def start_cmd(message: Message):
    await message.answer("🚀 Панель управления парсером (Render Webhook Edition)", reply_markup=main_kb())

@dp.message(F.text == "📂 Мои группы")
async def show_groups(message: Message):
    groups = await get_user_groups(message.from_user.id)
    if not groups:
        return await message.answer("Групп нет.")
    for g in groups:
        text = f"📂 <b>{g['name']}</b>\n" + "\n".join([f"• <code>{i['query']}</code>" for i in g.get("items", [])])
        await message.answer(text, parse_mode="HTML")

@dp.message(F.text == "➕ Добавить пресет")
async def add_preset(message: Message):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📱 iPhone 13", callback_data="preset_iphone_13")],
        [InlineKeyboardButton(text="👕 Streetwear", callback_data="preset_streetwear_hype")]
    ])
    await message.answer("Выберите готовый набор:", reply_markup=kb)

@dp.callback_query(F.data.startswith("preset_"))
async def apply_preset(call: CallbackQuery):
    key = call.data.replace("preset_", "")
    groups = await get_user_groups(call.from_user.id)
    groups.append(PRESETS[key])
    await save_user_groups(call.from_user.id, groups)
    await call.message.edit_text(f"✅ Набор **{PRESETS[key]['name']}** добавлен!", parse_mode="Markdown")

# --- ЗАЩИЩЕННЫЙ ПАРСЕР И ОЧЕРЕДЬ ЗАДАЧ ---
async def fetch_olx_data(session: AsyncSession, query: str, min_p: int, max_p: int):
    url = f"https://www.olx.ua/api/v1/offers/?query={query}&offset=0&limit=40&filter_float_price:from={min_p}&filter_float_price:to={max_p}"
    try:
        response = await session.get(url, impersonate="chrome120")
        if response.status_code == 200:
            return response.json().get("data", [])
    except Exception as e:
        logging.error(f"Ошибка сети: {e}")
    return []

async def parser_worker():
    async with AsyncSession() as session:
        while True:
            task = await parse_queue.get()
            uid, group_name, item_conf = task
            offers = await fetch_olx_data(session, item_conf["query"], item_conf["min"], item_conf["max"])
            
            for item in offers:
                url = item.get("url", "")
                if await is_seen(uid, url):
                    continue
                    
                title = item.get("title", "").lower()
                desc = item.get("description", "").lower()
                
                if any(w in title for w in TITLE_STOP_WORDS) or any(w in desc for w in DESC_STOP_WORDS):
                    await add_seen(uid, url)
                    continue
                    
                is_olx_delivery = item.get("delivery", {}).get("is_active", False)
                if not is_olx_delivery:
                    await add_seen(uid, url)
                    continue

                msg = f"🏷 <b>{group_name}</b>\n📱 <b>{item.get('title')}</b>\n🔗 {url}"
                try:
                    await bot.send_message(uid, msg, parse_mode="HTML")
                except Exception:
                    pass
                await add_seen(uid, url)

            parse_queue.task_done()
            await asyncio.sleep(DELAY_BETWEEN_REQUESTS)

async def task_generator():
    while True:
        if is_monitoring_active:
            subscribers = await get_all_subscribers()
            for uid in subscribers:
                groups = await get_user_groups(uid)
                for group in groups:
                    for item in group.get("items", []):
                        await parse_queue.put((uid, group["name"], item))
        await asyncio.sleep(30)

# --- ИНТЕГРАЦИЯ ВЕБХУКОВ И СЕРВЕРА AIOHTTP ---
async def on_startup(bot: Bot):
    await init_db()
    if WEBHOOK_URL:
        await bot.set_webhook(f"{WEBHOOK_URL}/webhook")
        logging.info(f"✅ Webhook установлен на {WEBHOOK_URL}/webhook")
    
    asyncio.create_task(task_generator())
    asyncio.create_task(parser_worker())

async def on_shutdown(bot: Bot):
    await bot.delete_webhook()
    logging.info("❌ Webhook удален")

async def ping_handler(request):
    return web.Response(text="Bot is alive and parsing OLX!")

def main():
    dp.startup.register(on_startup)
    dp.shutdown.register(on_shutdown)

    app = web.Application()
    app.router.add_get("/", ping_handler)
    
    webhook_requests_handler = SimpleRequestHandler(dispatcher=dp, bot=bot)
    webhook_requests_handler.register(app, path="/webhook")
    setup_application(app, dp, bot=bot)

    logging.info(f"Запуск сервера на порту {PORT}...")
    web.run_app(app, host="0.0.0.0", port=PORT)

if __name__ == "__main__":
    main()