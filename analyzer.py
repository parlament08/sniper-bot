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
Ты — профессиональный алгоритмический трейдер Traider. Твоя торговая стратегия основана на Smart Money Concepts (SMC) с применением жесткой фильтрации.

РЕГЛАМЕНТ ПРИНЯТИЯ РЕШЕНИЙ:
1. АНАЛИЗ ГЛОБАЛЬНОГО КОНТЕКСТА:
   - Определи HTF Bias (4H) и тренд по DXY, SPX, BTC.D.
   - Сравни это с текущим Price Action на 15m.
   - Если нет синхронизации (например, рост крипты при растущем DXY) — АКТИВИРУЙ ВЕТО.

2. МОДУЛЬ ФИЛЬТРАЦИИ:
   - KILL ZONE: Если текущее время не попадает в окна (10:00–12:00 или 15:30–18:00 МСК) — статус "НАБЛЮДЕНИЕ".
   - R:R ВЕТО: Если потенциальный профит дает менее 1:3.5 — статус "NO TRADE (Низкий R:R)".
   - ФУНДАМЕНТ: При наличии данных о CPI/FOMC/NFP (если в контексте) — полная блокировка.

3. АЛГОРИТМ РАБОТЫ:
   - Ищи только А+ сетапы: Правило двух свечей, SFP+Ретест, FVG Entry, Breaker Block.
   - Отличай SFP (Манипуляция) от истинного пробоя.
   - При обнаружении паттерна проверь условия: CHoCH подтвержден? Ликвидность собрана? Имбаланс присутствует?

ФОРМАТ ОТВЕТА (Строго):

📊 ТЕХНИЧЕСКИЙ АНАЛИЗ [Asset/USDT] ([TF])
• Market Structure: (4H Bias vs 15m статус).
• Liquidity & FVG: (Где деньги, куда идем).
• Межрыночный фон: (DXY, SPX, BTC.D — синхрон или рассинхрон).

🎯 СТАТУС: A+ SETUP (или 🛑 СТАТУС: НАБЛЮДЕНИЕ)
• Логика: (Обоснование вероятности >70% по SMC).
• Точка входа: [Цена]
• Stop-Loss: [Цена]
• Take-Profit: [Цена]
• R:R: [Соотношение]
• Расчитай позицию в USDT, если есть баланс (например, 500 USDT) и риск 1% на сделку: [Объем позиции в USDT]

🔥 ГОРЯЧИЕ ПРАВИЛА:
- Если 15m сетап не готов — пиши причину (например, "Ждем CHoCH", "Азиатский флэт").
- Соблюдай тон Traider. Будь краток, профессионален, бескомпромиссен.
"""

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
last_alert_time = {coin: 0 for coin in ['BTC', 'ETH', 'SOL', 'XRP', 'ADA', 'LINK', 'INJ', 'HYPE', 'LTC', 'DOT']}

def market_scan(report_mode="HUNT"):
    """
    report_mode = "FULL" (отправляет все монеты, плановый брифинг)
    report_mode = "HUNT" (ищет только сетапы А+, тихий режим)
    """
    work_pairs = ['BTC', 'ETH', 'SOL', 'XRP', 'ADA', 'LINK', 'INJ', 'HYPE', 'LTC', 'DOT']
    
    current_time_str = datetime.now().strftime("%H:%M")
    
    if report_mode == "FULL":
        print(f"\n📊 [{current_time_str}] Запуск планового брифинга (Лондон/Нью-Йорк)...")
        send_telegram_alert(f"<b>🕒 РЫНОЧНЫЙ БРИФИНГ | {current_time_str}</b>\nНачинаю анализ рынка...")
    else:
        print(f"\n🔎 [{current_time_str}] Тихий поиск сетапов (15m)...")

    # Получаем макро-данные
    macro = get_macro_context()
    
    # Формируем полную строку для ИИ с BTC.D
    macro_str = f"DXY: {macro.get('DXY', {}).get('trend')} | SPX: {macro.get('SPX', {}).get('trend')} | BTC.D: {macro.get('BTC.D', {}).get('price')}% ({macro.get('BTC.D', {}).get('trend')})"
    
    # Отправляем макро-обзор в Телеграм только утром в FULL режиме
    if report_mode == "FULL":
        macro_msg = (
            f"🌍 <b>ГЛОБАЛЬНЫЙ КОНТЕКСТ:</b>\n"
            f"💵 DXY: {macro.get('DXY', {}).get('trend')}\n"
            f"📈 S&P500: {macro.get('SPX', {}).get('trend')}\n"
            f"👑 BTC.D: {macro.get('BTC.D', {}).get('price')}% ({macro.get('BTC.D', {}).get('trend')})"
        )
        send_telegram_alert(macro_msg)
        time.sleep(1) # Небольшая пауза, чтобы сообщения в ТГ шли по порядку
    
    for coin in work_pairs:
        result = prepare_and_analyze(coin, macro_str)
        if not result:
            continue
            
        is_setup = "A+ SETUP" in result
        
        if report_mode == "FULL":
            status_emoji = "🎯 СЕТАП А+" if is_setup else "🛑 НАБЛЮДЕНИЕ"
            send_telegram_alert(f"<b>{status_emoji} | {coin}USDT.P</b>\n\n{result}")
            print(f"📋 {coin} — Полный отчет отправлен в плановый брифинг.")
            
        elif report_mode == "HUNT":
            if is_setup:
                current_timestamp = time.time()
                if current_timestamp - last_alert_time[coin] > 7200: 
                    send_telegram_alert(f"🚨 <b>СНАЙПЕР ОБНАРУЖИЛ СЕТАП!</b> 🚨\n\n{result}")
                    last_alert_time[coin] = current_timestamp
                    print(f"🔥 ВНИМАНИЕ: Найдена сделка по {coin}! Алерт улетел в Telegram.")
                else:
                    print(f"⏳ Сетап по {coin} еще актуален, но пуш на паузе (анти-спам кулдаун).")
            else:
                print(f"ℹ️ {coin} отфильтрован ИИ. Статус: НАБЛЮДЕНИЕ (В Telegram не отправляем).")

if __name__ == "__main__":
    print("🚀 Радар «СНАЙПЕР» переведен в боевой режим.")
    print("🔹 Плановые отчеты: 09:00")
    print("🔹 Поиск сетапов: каждые 15 минут\n")

    # --- ТЕСТОВЫЙ ПРОГРЕВ И ПРОВЕРКА СВЯЗИ ---
    send_telegram_alert("👋 <b>СИСТЕМА ОНЛАЙН</b>\nTraider, радар СНАЙПЕР запущен.")
    
    market_scan(report_mode="FULL")
    # -----------------------------------------

    # 1. Плановый брифинг ТОЛЬКО в 09:00
    schedule.every().day.at("09:00").do(market_scan, report_mode="FULL")

    # 2. Настраиваем мониторинг сетапов
    schedule.every().hour.at(":00").do(market_scan, report_mode="HUNT")
    schedule.every().hour.at(":15").do(market_scan, report_mode="HUNT")
    schedule.every().hour.at(":30").do(market_scan, report_mode="HUNT")
    schedule.every().hour.at(":45").do(market_scan, report_mode="HUNT")

    # Бесконечный цикл
    while True:
        schedule.run_pending()
        time.sleep(1)