import os
import time
import telebot
from dotenv import load_dotenv
from daily_alerts import generate_coin_alert
from analyzer import market_scan
from core.logger import logger

# Загружаем переменные из .env файла
load_dotenv()

# Получаем учетные данные из переменных окружения
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

if not TELEGRAM_TOKEN or not CHAT_ID:
    raise ValueError("TELEGRAM_TOKEN и CHAT_ID должны быть установлены в .env файле")

# Инициализируем бота
bot = telebot.TeleBot(TELEGRAM_TOKEN)

WORK_PAIRS = ['BTC', 'ETH', 'SOL', 'XRP', 'ADA', 'LINK', 'INJ', 'HYPE', 'LTC', 'DOT']

@bot.message_handler(commands=['alerts'])
def handle_alerts_command(message):
    """Обрабатывает команду /alerts, проверяет пользователя и запускает генерацию отчетов."""
    # Проверка безопасности: отвечаем только авторизованному пользователю
    if str(message.chat.id) != CHAT_ID:
        logger.warning(f"Несанкционированный доступ от chat_id: {message.chat.id}")
        bot.send_message(message.chat.id, "У вас нет доступа к этому боту.")
        return

    logger.info(f"Получена команда /alerts от авторизованного пользователя.")
    bot.send_message(CHAT_ID, "⏳ Собираю свежую SMC разметку для Binance...", parse_mode="HTML")

    alerts_report = []
    for coin in WORK_PAIRS:
        report = generate_coin_alert(coin)
        if report:
            alerts_report.append(report)
        time.sleep(1)  # Небольшая задержка между запросами к API биржи

    # Отправляем отчеты пачками по 3 монеты
    if alerts_report:
        chunk_size = 3
        for i in range(0, len(alerts_report), chunk_size):
            chunk = "\n\n──────────────────\n\n".join(alerts_report[i:i + chunk_size])
            bot.send_message(CHAT_ID, chunk, parse_mode="HTML")
            time.sleep(1)  # Задержка между отправкой сообщений в Telegram
        bot.send_message(CHAT_ID, "✅ <b>Разметка завершена.</b>", parse_mode="HTML")

@bot.message_handler(commands=['scan'])
def handle_scan_command(message):
    """Обрабатывает команду /scan для ручного запуска полного сканирования рынка."""
    # Проверка безопасности: отвечаем только авторизованному пользователю
    if str(message.chat.id) != CHAT_ID:
        logger.warning(f"Несанкционированный доступ от chat_id: {message.chat.id} для команды /scan")
        bot.send_message(message.chat.id, "У вас нет доступа к этому боту.")
        return

    logger.info(f"Получена команда /scan от авторизованнго пользователя.")
    bot.send_message(CHAT_ID, "⏳ Запускаю ручное сканирование рынка...", parse_mode="HTML")
    
    try:
        # Вызываем market_scan в режиме полного брифинга.
        # Эта функция сама отправит итоговый отчет в Telegram.
        market_scan(report_mode="FULL")
    except Exception as e:
        logger.error(f"Ошибка при выполнении ручного сканирования по команде /scan: {e}", exc_info=True)
        bot.send_message(CHAT_ID, "❌ Произошла ошибка во время сканирования. Проверьте логи.", parse_mode="HTML")

if __name__ == "__main__":
    logger.info("🚀 Telegram Listener запущен. Ожидаю команду /alerts...")
    bot.infinity_polling()