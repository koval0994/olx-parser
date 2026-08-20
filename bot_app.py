import os
import logging
from aiohttp import web
from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import CommandStart
from aiogram.types import (
    ReplyKeyboardMarkup,
    KeyboardButton,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    KeyboardButtonRequestChat
)
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application

# Логирование
logging.basicConfig(level=logging.INFO)

# КОНФИГУРАЦИЯ (Исправлено имя переменной)
BOT_TOKEN = os.getenv("TELEGRAM_TOKEN")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")
WEBHOOK_PATH = "/webhook"
BASE_WEBHOOK_URL = f"{WEBHOOK_URL}{WEBHOOK_PATH}"

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# ----------------- КЛАВИАТУРЫ -----------------

main_keyboard = ReplyKeyboardMarkup(
    keyboard=[
        [
            KeyboardButton(text="🔍 Поиск OLX"),
            KeyboardButton(text="📊 Мои подписки")
        ],
        [
            KeyboardButton(text="⚙️ Настройки"),
            KeyboardButton(
                text="👥 Поделиться группой",
                request_chat=KeyboardButtonRequestChat(
                    request_id=1,
                    chat_is_channel=False,
                    bot_is_member=True
                )
            )
        ]
    ],
    resize_keyboard=True
)

# ----------------- ОБРАБОТЧИКИ -----------------

@dp.message(CommandStart())
async def cmd_start(message: types.Message):
    await message.answer(
        "👋 **Привет! Я OLX Parser Бот.**\n\nВыберите действие в меню ниже:",
        reply_markup=main_keyboard,
        parse_mode="Markdown"
    )

@dp.message(F.text == "🔍 Поиск OLX")
async def process_search(message: types.Message):
    await message.answer("Пришлите ссылку на OLX или ключевое слово для поиска:")

@dp.message(F.text == "📊 Мои подписки")
async def process_subscriptions(message: types.Message):
    await message.answer("У вас пока нет активных отслеживаний OLX.")

@dp.message(F.text == "⚙️ Настройки")
async def process_settings(message: types.Message):
    inline_kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="⏱ Интервал проверки", callback_data="set_interval")],
            [InlineKeyboardButton(text="🔔 Уведомления", callback_data="toggle_notify")]
        ]
    )
    await message.answer("Настройки бота:", reply_markup=inline_kb)

@dp.message(F.chat_shared)
async def process_chat_shared(message: types.Message):
    shared_chat_id = message.chat_shared.chat_id
    await message.answer(
        f"✅ Группа успешно подключена!\nID группы: `{shared_chat_id}`",
        parse_mode="Markdown"
    )

# ----------------- ЛОГИКА ВЕБХУКА -----------------

async def on_startup(bot: Bot) -> None:
    logging.info(f"Установка вебхука: {BASE_WEBHOOK_URL}")
    await bot.set_webhook(BASE_WEBHOOK_URL, drop_pending_updates=True)

async def on_shutdown(bot: Bot) -> None:
    # ФИКС: Вебхук НЕ удаляется при выключении контейнера
    logging.info("Остановка сервера (вебхук сохраняется).")
    pass

def main():
    dp.startup.register(on_startup)
    dp.shutdown.register(on_shutdown)

    app = web.Application()

    webhook_requests_handler = SimpleRequestHandler(
        dispatcher=dp,
        bot=bot,
    )
    webhook_requests_handler.register(app, path=WEBHOOK_PATH)

    setup_application(app, dp, bot=bot)

    port = int(os.getenv("PORT", 10000))
    web.run_app(app, host="0.0.0.0", port=port)

if __name__ == "__main__":
    main()
