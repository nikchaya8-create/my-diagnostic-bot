import logging
import os
import sqlite3
import asyncio
from aiohttp import web
from aiogram import Bot, Dispatcher, types
from aiogram.contrib.fsm_storage.memory import MemoryStorage
from aiogram.utils import executor
import google.generativeai as genai

# Настройки конфигурации
API_TOKEN = "BOT_TOKEN_PLACEHOLDER"
INVITE_CODE = "start"
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "ВАШ_GEMINI_API_KEY")

# Настройка логирования
logging.basicConfig(level=logging.INFO)

# Инициализация ИИ
genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel(
    model_name="gemini-1.5-flash",
    system_instruction="""You are an expert diagnostic assistant. Your task is to analyze the user's birthdate and their answers to the psychological test, and deliver a comprehensive personality profile.
Use the following methodology for analysis:
1. Astrological / Numerological profile based on their birthdate.
2. Character traits, strengths, and hidden blind spots based on test answers.
3. Actionable recommendation (1-3 tips) for daily life.
Keep the tone helpful, professional, and slightly mystical but grounded in psychological insights.
At the end, ask if they have any follow-up questions about this analysis."""
)

# Инициализация Telegram Бота
bot = Bot(token=API_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(bot, storage=storage)

# Веб-сервер для прохождения Port Check на Render.com
async def handle_ping(request):
    return web.Response(text="Bot is running!")

async def start_web_server():
    app = web.Application()
    app.router.add_get('/', handle_ping)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get("PORT", "8080"))
    site = web.TCPSite(runner, '0.0.0.0', port)
    await site.start()
    logging.info(f"Web server started on port {port}")

async def on_startup(dp):
    # Запускаем фоновый веб-сервер
    asyncio.create_task(start_web_server())

# База данных для истории диалогов и проверки доступа
conn = sqlite3.connect("bot_database.db", check_same_thread=False)
cursor = conn.cursor()
cursor.execute("""
    CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY,
        has_access INTEGER DEFAULT 0
    )
""")
cursor.execute("""
    CREATE TABLE IF NOT EXISTS chat_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        role TEXT,
        content TEXT
    )
""")
conn.commit()

def check_user_access(user_id):
    cursor.execute("SELECT has_access FROM users WHERE user_id = ?", (user_id,))
    row = cursor.fetchone()
    return row and row[0] == 1

def grant_user_access(user_id):
    cursor.execute("INSERT OR REPLACE INTO users (user_id, has_access) VALUES (?, 1)", (user_id,))
    conn.commit()

def save_message(user_id, role, content):
    cursor.execute("INSERT INTO chat_history (user_id, role, content) VALUES (?, ?, ?)", (user_id, role, content))
    conn.commit()

def get_chat_history(user_id):
    cursor.execute("SELECT role, content FROM chat_history WHERE user_id = ? ORDER BY id ASC", (user_id,))
    rows = cursor.fetchall()
    history = []
    for role, content in rows:
        history.append({"role": "user" if role == "user" else "model", "parts": [content]})
    return history

@dp.message_handler(commands=['start'])
async def send_welcome(message: types.Message):
    args = message.get_args()
    user_id = message.from_user.id
    
    if args == INVITE_CODE or check_user_access(user_id):
        grant_user_access(user_id)
        welcome_text = """Приветствую! Я бот ИИ-диагностики. Пришлите вашу дату рождения и ответы на тест одним сообщением для получения полного разбора.

Вопросы теста:
1. Опишите ваше главное стремление в жизни?
2. Что пугает вас сильнее всего?
3. Какой ваш идеальный день?"""
        await message.reply(welcome_text)
        # Очищаем прошлую историю при новом старте по ссылке
        cursor.execute("DELETE FROM chat_history WHERE user_id = ?", (user_id,))
        conn.commit()
    else:
        await message.reply(
            "⚠️ Доступ ограничен! Этот бот доступен только по специальной пригласительной ссылке.\n"
            "Пожалуйста, используйте ссылку, предоставленную вашим куратором."
        )

@dp.message_handler()
async def handle_chat(message: types.Message):
    user_id = message.from_user.id
    
    if not check_user_access(user_id):
        await message.reply("⚠️ Доступ ограничен! Пожалуйста, перейдите по ссылке-приглашению для активации бота.")
        return

    user_text = message.text
    save_message(user_id, "user", user_text)

    # Показываем статус "печатает"
    await bot.send_chat_action(chat_id=message.chat.id, action=types.ChatActions.TYPING)

    try:
        # Получаем историю чата
        history = get_chat_history(user_id)
        
        # Создаем сессию чата ИИ с историей
        chat = model.start_chat(history=history[:-1]) # исключая последнее только что добавленное сообщение
        response = chat.send_message(user_text)
        
        ai_response = response.text
        save_message(user_id, "model", ai_response)
        
        await message.reply(ai_response, parse_mode="Markdown")
        
    except Exception as e:
        logging.error(f"Error calling Gemini: {e}")
        await message.reply("Произошла ошибка при обработке вашего запроса ИИ. Попробуйте написать позже.")

if __name__ == '__main__':
    executor.start_polling(dp, skip_updates=True, on_startup=on_startup)
