import logging
import os
import sqlite3
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
import telebot
import google.generativeai as genai

# Настройки конфигурации
API_TOKEN = os.getenv("TELEGRAM_TOKEN", "BOT_TOKEN_PLACEHOLDER")
INVITE_CODE = "start"
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "ВАШ_GEMINI_API_KEY")

# Настройка логирования
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# Проверка токена и ключей перед запуском
if not API_TOKEN or ":" not in API_TOKEN or "ВАШ_ТЕЛЕГРАМ_ТОКЕН" in API_TOKEN:
    logging.error("❌ КРИТИЧЕСКАЯ ОШИБКА: Токен Telegram не указан или указан неверно!")
    logging.error("Убедитесь, что вы добавили переменную окружения TELEGRAM_TOKEN в настройках Render (вкладка Environment).")
    logging.error("Токен от BotFather должен содержать двоеточие, например: 123456:ABC-DEF1234ghIkl-zyx57W2v1u1")
    import sys
    sys.exit(1)

if not GEMINI_API_KEY or "ВАШ_GEMINI_API_KEY" in GEMINI_API_KEY:
    logging.error("❌ КРИТИЧЕСКАЯ ОШИБКА: Ключ API Gemini не указан!")
    logging.error("Пожалуйста, добавьте переменную окружения GEMINI_API_KEY в настройках Render -> Environment")
    import sys
    sys.exit(1)

# Инициализация ИИ
genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel(
    model_name="gemini-1.5-flash",
    system_instruction="You are an expert diagnostic assistant. Your task is to analyze the user's birthdate and their answers to the psychological test, and deliver a comprehensive personality profile.\nUse the following methodology for analysis:\n1. Astrological / Numerological profile based on their birthdate.\n2. Character traits, strengths, and hidden blind spots based on test answers.\n3. Actionable recommendation (1-3 tips) for daily life.\nKeep the tone helpful, professional, and slightly mystical but grounded in psychological insights.\nAt the end, ask if they have any follow-up questions about this analysis."
)

# Инициализация Telegram Бота
bot = telebot.TeleBot(API_TOKEN)

# База данных для истории диалогов и проверки доступа
DB_FILE = "bot_database.db"

def init_db():
    conn = sqlite3.connect(DB_FILE, check_same_thread=False)
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
    conn.close()

init_db()

def check_user_access(user_id):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT has_access FROM users WHERE user_id = ?", (user_id,))
    row = cursor.fetchone()
    conn.close()
    return row and row[0] == 1

def grant_user_access(user_id):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("INSERT OR REPLACE INTO users (user_id, has_access) VALUES (?, 1)", (user_id,))
    conn.commit()
    conn.close()

def save_message(user_id, role, content):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("INSERT INTO chat_history (user_id, role, content) VALUES (?, ?, ?)", (user_id, role, content))
    conn.commit()
    conn.close()

def get_chat_history(user_id):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT role, content FROM chat_history WHERE user_id = ? ORDER BY id ASC", (user_id,))
    rows = cursor.fetchall()
    conn.close()
    history = []
    for role, content in rows:
        history.append({"role": "user" if role == "user" else "model", "parts": [content]})
    return history

# Команда /start
@bot.message_handler(commands=['start'])
def send_welcome(message):
    user_id = message.from_user.id
    # Получаем аргументы команды /start <код>
    args = message.text.split()[1:] if len(message.text.split()) > 1 else []
    arg = args[0] if args else ""
    
    if arg == INVITE_CODE or check_user_access(user_id):
        grant_user_access(user_id)
        
        # Очищаем историю чата при новом старте
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        cursor.execute("DELETE FROM chat_history WHERE user_id = ?", (user_id,))
        conn.commit()
        conn.close()
        
        welcome_text = "Приветствую! Я бот ИИ-диагностики. Пришлите вашу дату рождения и ответы на тест одним сообщением для получения полного разбора.\n\nВопросы теста:\n1. Опишите ваше главное стремление в жизни?\n2. Что пугает вас сильнее всего?\n3. Какой ваш идеальный день?"
        bot.reply_to(message, welcome_text)
    else:
        bot.reply_to(
            message,
            "⚠️ Доступ ограничен! Этот бот доступен только по специальной пригласительной ссылке.\n"
            "Пожалуйста, используйте ссылку, предоставленную вашим куратором."
        )

# Обработка всех остальных текстовых сообщений
@bot.message_handler(func=lambda message: True)
def handle_chat(message):
    user_id = message.from_user.id
    
    if not check_user_access(user_id):
        bot.reply_to(message, "⚠️ Доступ ограничен! Пожалуйста, перейдите по ссылке-приглашению для активации бота.")
        return

    user_text = message.text
    save_message(user_id, "user", user_text)

    # Показываем статус "печатает"
    bot.send_chat_action(message.chat.id, 'typing')

    try:
        # Получаем историю чата
        history = get_chat_history(user_id)
        
        # Создаем сессию чата ИИ с историей
        chat = model.start_chat(history=history[:-1]) # исключая последнее только что добавленное сообщение
        response = chat.send_message(user_text)
        
        ai_response = response.text
        save_message(user_id, "model", ai_response)
        
        bot.reply_to(message, ai_response, parse_mode="Markdown")
        
    except Exception as e:
        error_msg = str(e)
        logging.error(f"Error calling Gemini: {error_msg}")
        
        if "api_key_invalid" in error_msg.lower() or "api key" in error_msg.lower() or "not found" in error_msg.lower() or "invalid" in error_msg.lower():
            bot.reply_to(
                message,
                "⚠️ Ошибка ИИ: Проблема с API-ключом Gemini!\n\nПожалуйста, убедитесь, что вы правильно добавили переменную окружения GEMINI_API_KEY в настройках Render (вкладка Environment) и перезапустили деплой."
            )
        elif "quota" in error_msg.lower() or "limit" in error_msg.lower() or "429" in error_msg:
            bot.reply_to(
                message,
                "⚠️ Ошибка ИИ: Превышен лимит запросов (Quota Exceeded)!\n\nПожалуйста, проверьте ваш лимит запросов Gemini или создайте новый API-ключ в Google AI Studio."
            )
        else:
            bot.reply_to(
                message,
                f"⚠️ Ошибка при запросе к ИИ:\n\n`{error_msg}`\n\nПожалуйста, убедитесь, что вы добавили переменную окружения GEMINI_API_KEY в настройках Render!"
            )

# Простой веб-сервер для прохождения Port Check на Render.com (запускается в фоновом потоке)
class PingHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-Type', 'text/plain; charset=utf-8')
        self.end_headers()
        self.wfile.write("Бот запущен и работает!".encode('utf-8'))

def run_web_server():
    port = int(os.environ.get("PORT", "8080"))
    server = HTTPServer(('0.0.0.0', port), PingHandler)
    logging.info(f"Фоновый веб-сервер запущен на порту {port}")
    server.serve_forever()

if __name__ == '__main__':
    # Запускаем фоновый веб-сервер в отдельном потоке
    web_thread = threading.Thread(target=run_web_server, daemon=True)
    web_thread.start()
    
    # Запуск бота в режиме бесконечного опроса (polling)
    logging.info("Бот запущен...")
    bot.infinity_polling()
