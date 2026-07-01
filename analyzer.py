import os
import time
import requests
from macro_context import get_macro_context
from market_data import fetch_candles
import google.generativeai as genai

# Инициализация API
api_key = os.environ.get("GEMINI_API_KEY")
if not api_key:
    raise ValueError("❌ Не найден GEMINI_API_KEY. Выполни в терминале: export GEMINI_API_KEY='твой_ключ'")

genai.configure(api_key=api_key)

# Настройки Telegram (Замени на свои данные)
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

# Твоя системная инструкция
SNIPER_INSTRUCTION = """
ИНСТРУКЦИЯ КРИПТО-ТРЕЙДЕРА «СНАЙПЕР» (V.5.0 — ELITE)
1. ТЕХНИЧЕСКАЯ БАЗА (Smart Money Concepts)
   • Market Structure: BOS/CHoCH, доставка цены (Order Flow).
   • Liquidity & POI: Поиск Liquidity Sweeps, Order Blocks, FVG.
   • SFP Filter (Критически): Отличай «истинный пробой» от «снятия ликвидности». Пробой — это закрытие тела свечи за уровнем на HTF. Фитиль — это манипуляция (SFP).
   • Pricing: Работа в Premium/Discount зонах.
   • Intermarket Analysis: Обязательный учет корреляций (DXY, S&P500, BTC.D).
2. ОПЕРАЦИОННЫЙ РЕГЛАМЕНТ («Elite Filtering»)
   • Вход в сделку осуществляется только на таймфрейме 15m.
   • Приоритет качества над количеством: Ищем один высоковероятный сетап (A+) раз в 1-2 дня. Если идеальных условий нет, статус — NO TRADE.
   • Тройная Синхронизация: Вход разрешен только если HTF Trend (4h), LTF Confirmation (15m) и Intermarket Flow (DXY/SPX) указывают в одну сторону.
   • Риск-менеджмент: R:R 1:3.5+ (сделки с меньшим потенциалом игнорируются), Риск 1% на сделку.
   • Обращение: Всегда обращайся ко мне Traider.
Утвержденные модели входа:
 1. Правило двух свечей.
 2. Модель «SFP + Ретест».
 3. Модель «FVG Entry».
 4. Модель «Breaker Block».
5. МОДУЛЬ ЖЕСТКОЙ ФИЛЬТРАЦИИ (V.5.0)
   • Kill Zone Filter: Работаем только в периоды высокой ликвидности: Лондон (10:00–12:00 МСК) и Нью-Йорк (15:30–18:00 МСК). Вне этих зон — только наблюдение.
   • Математическое Вето: Если потенциальная точка входа дает R:R ниже 1:3.5, сетап помечается как «Мусор» и пропускается.
   • Фундаментальный Блок: За 1 час до и 1 час после выхода данных CPI, FOMC, NFP торговля запрещена.
6. ПРАВИЛА ИСКЛЮЧЕНИЯ (Стоп-факторы)
   • Аномалия DXY: Рост актива при растущем DXY — вход запрещен (ловушка).
   • S&P 500 Dump: Валится фонда — лонги по крипте блокируются до стабилизации.
   • BTC Dominance (BTC.D): При доминации >60% лонги по альтам считаются сверхрисковыми.
7. СТИЛЬ И ФОРМАТ ВЫВОДА (A+ Setup Only)
   📊 ТЕХНИЧЕСКИЙ АНАЛИЗ [Asset/USDT] ([TF])
   • Market Structure: ...
   • Liquidity & FVG: ...
   • Межрыночный фон: ...
 Вывод строго в форматах '🎯 СЕТАП ДНЯ' или '🛑 СТАТУС: НАБЛЮДЕНИЕ (NO TRADE)'.
"""

# ИСПРАВЛЕНИЕ: Переключаемся на безлимитную модель 1.5 Flash
model = genai.GenerativeModel(
    model_name='models/gemini-3.1-flash-lite',
    system_instruction=SNIPER_INSTRUCTION
)

def send_telegram_alert(text):
    """Служебная функция для отправки пушей в Telegram"""
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": text, "parse_mode": "HTML"}
    try:
        requests.post(url, data=payload, timeout=5)
    except Exception as e:
        print(f"❌ Не удалось отправить пуш в Telegram: {e}")

def prepare_and_analyze(coin, macro_str):
    print(f"\n🧠 Анализ {coin}...")
    
    df_4h = fetch_candles(coin, '4h', limit=5)
    df_15m = fetch_candles(coin, '15m', limit=5)
    
    if df_4h is None or df_15m is None:
        print(f"❌ Пропуск {coin}: ошибка получения рыночных данных.")
        return None
    
    candles_4h = df_4h[['timestamp', 'open', 'high', 'low', 'close']].tail(3).to_string(index=False)
    candles_15m = df_15m[['timestamp', 'open', 'high', 'low', 'close']].tail(3).to_string(index=False)
    
    prompt = f"""
    Проведи анализ актива {coin}USDT.P согласно инструкции СНАЙПЕР.
    МЕЖРЫНОЧНЫЙ ФОН: {macro_str}
    ДАННЫЕ HTF (4H):\n{candles_4h}
    ДАННЫЕ LTF (15m):\n{candles_15m}
    """
    
    try:
        response = model.generate_content(prompt)
        return response.text
    except Exception as e:
        print(f"❌ Ошибка API Gemini для {coin}: {e}")
        return None

import schedule
from datetime import datetime

# Память для анти-спама: хранит время последнего отправленного A+ сетапа по каждой монете
last_alert_time = {coin: 0 for coin in ['BTC', 'ETH', 'SOL', 'XRP', 'ADA', 'LINK']}

def market_scan(report_mode="HUNT"):
    """
    report_mode = "FULL" (отправляет все монеты, плановый брифинг)
    report_mode = "HUNT" (ищет только сетапы А+, тихий режим)
    """
    work_pairs = ['BTC', 'ETH', 'SOL', 'XRP', 'ADA', 'LINK']
    
    current_time_str = datetime.now().strftime("%H:%M")
    
    if report_mode == "FULL":
        print(f"\n📊 [{current_time_str}] Запуск планового брифинга (Лондон/Нью-Йорк)...")
        send_telegram_alert(f"<b>🕒 РЫНОЧНЫЙ БРИФИНГ | {current_time_str}</b>\nНачинаю синхронный анализ...")
    else:
        print(f"\n🔎 [{current_time_str}] Тихий поиск сетапов (15m)...")

    macro = get_macro_context()
    macro_str = f"DXY: {macro.get('DXY', {}).get('trend')} | SPX: {macro.get('SPX', {}).get('trend')}"
    
    for coin in work_pairs:
        result = prepare_and_analyze(coin, macro_str)
        if not result:
            continue
            
        is_setup = "СЕТАП ДНЯ" in result
        
        # УЛУЧШЕННАЯ ЛОГИКА ОТПРАВКИ И ЛОГИРОВАНИЯ
        if report_mode == "FULL":
            # В плановом отчете (09:00 и 15:00) шлем абсолютно всё в телеграм
            status_emoji = "🎯 СЕТАП А+" if is_setup else "🛑 НАБЛЮДЕНИЕ"
            send_telegram_alert(f"<b>{status_emoji} | {coin}USDT.P</b>\n\n{result}")
            print(f"📋 {coin} — Полный отчет отправлен в плановый брифинг.")
            
        elif report_mode == "HUNT":
            if is_setup:
                # Если в тихом режиме нашли А+, шлем экстренно в телеграм
                current_timestamp = time.time()
                if current_timestamp - last_alert_time[coin] > 7200: 
                    send_telegram_alert(f"🚨 <b>СНАЙПЕР ОБНАРУЖИЛ СЕТАП!</b> 🚨\n\n{result}")
                    last_alert_time[coin] = current_timestamp
                    print(f"🔥 ВНИМАНИЕ: Найдена сделка по {coin}! Алерт улетел в Telegram.")
                else:
                    print(f"⏳ Сетап по {coin} еще актуален, но пуш на паузе (анти-спам кулдаун).")
            else:
                # ИСПРАВЛЕНИЕ: Теперь консоль покажет, что ИИ не спит, а фильтрует рынок
                print(f"ℹ️ {coin} отфильтрован ИИ. Статус: НАБЛЮДЕНИЕ (В Telegram не отправляем).")

if __name__ == "__main__":
    print("🚀 Радар «СНАЙПЕР» переведен в боевой режим.")
    print("🔹 Плановые отчеты: 09:00 и 15:00")
    print("🔹 Поиск сетапов: каждые 15 минут\n")

    # --- ТЕСТОВЫЙ ПРОГРЕВ И ПРОВЕРКА СВЯЗИ ---
    print("Отправка тестового сообщения в Telegram...")
    send_telegram_alert("👋 <b>СИСТЕМА ОНЛАЙН</b>\nTraider, радар СНАЙПЕР успешно запущен. Связь установлена!")
    
    # Принудительно делаем один полный прогон прямо сейчас
    market_scan(report_mode="FULL")
    # -----------------------------------------

    # 1. Настраиваем плановые брифинги
    schedule.every().day.at("09:00").do(market_scan, report_mode="FULL")
    schedule.every().day.at("15:00").do(market_scan, report_mode="FULL")

    # 2. Настраиваем тихий мониторинг сетапов
    schedule.every().hour.at(":00").do(market_scan, report_mode="HUNT")
    schedule.every().hour.at(":15").do(market_scan, report_mode="HUNT")
    schedule.every().hour.at(":30").do(market_scan, report_mode="HUNT")
    schedule.every().hour.at(":45").do(market_scan, report_mode="HUNT")

    # Бесконечный цикл планировщика
    while True:
        schedule.run_pending()
        time.sleep(1)