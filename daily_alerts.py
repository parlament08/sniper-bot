import os
import time
from datetime import datetime
from dotenv import load_dotenv
import requests
import pandas as pd
from market_data import fetch_candles
import google.generativeai as genai

# Загружаем переменные из .env файла в корне проекта
load_dotenv()

# Инициализация API Gemini
api_key = os.environ.get("GEMINI_API_KEY")
if not api_key:
    raise ValueError("❌ Не найден GEMINI_API_KEY в .env файле!")

genai.configure(api_key=api_key)
model = genai.GenerativeModel(model_name='models/gemini-3.1-flash-lite')

# Настройки Telegram
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

# --- ИНТЕГРАЦИЯ МАТЕМАТИЧЕСКОГО ДВИЖКА ИЗ V10.1 ---

def calculate_ema(df, period=99):
    try:
        close_prices = df['close'].astype(float)
        return close_prices.ewm(span=period, adjust=False).mean()
    except Exception as e:
        return None

def calculate_rsi(df, period=6):
    try:
        close_prices = df['close'].astype(float)
        delta = close_prices.diff()
        gain = delta.clip(lower=0)
        loss = -delta.clip(upper=0)
        ema_gain = gain.ewm(com=period - 1, adjust=False).mean()
        ema_loss = loss.ewm(com=period - 1, adjust=False).mean()
        rs = ema_gain / ema_loss
        rsi = 100 - (100 / (1 + rs))
        return rsi
    except Exception as e:
        return None

def calculate_macd(df, fast=12, slow=26, signal=9):
    try:
        close_prices = df['close'].astype(float)
        exp1 = close_prices.ewm(span=fast, adjust=False).mean()
        exp2 = close_prices.ewm(span=slow, adjust=False).mean()
        macd_line = exp1 - exp2
        signal_line = macd_line.ewm(span=signal, adjust=False).mean()
        histogram = macd_line - signal_line
        return macd_line, signal_line, histogram
    except Exception as e:
        return None, None, None

def send_telegram_alert(text):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": text, "parse_mode": "HTML"}
    try:
        requests.post(url, data=payload, timeout=5)
    except Exception as e:
        print(f"❌ Ошибка отправки в TG: {e}")

# --- СПЕЦИАЛЬНАЯ ИНСТРУКЦИЯ ДЛЯ ГЕНЕРАЦИИ АЛЕРТОВ ---
ALERTS_INSTRUCTION = """
Ты — профессиональный SMC трейдер. Твоя задача — проанализировать график и разметить ближайшие Зоны Интереса (POI), чтобы трейдер мог поставить на них ручные алерты (будильники) в терминале Binance.

ПРАВИЛА ПОИСКА ЗОН:
1. Зона Long (Support POI): Ищи ближайший неперекрытый FVG (Имбаланс) ниже текущей цены ИЛИ очевидный пул ликвидности (sell-side liquidity), снятие которого даст отскок вверх.
2. Зона Short (Resistance POI): Ищи ближайший неперекрытый FVG выше текущей цены ИЛИ очевидный пул ликвидности (buy-side liquidity), снятие которого даст реакцию вниз.
3. Расчет Алертов: Алерт должен сработать ЗАРАНЕЕ — прямо перед тем, как цена коснется зоны. 
   - Алерт 🔽 (Вниз) ставь на 0.3-0.5% выше Зоны Long.
   - Алерт 🔼 (Вверх) ставь на 0.3-0.5% ниже Зоны Short.

ОТВЕТ СТРОГО ПО ШАБЛОНУ (без лишних слов и приветствий, используй только этот текст, заменяя скобки на цифры):

📉 <b>Зона Long (Поддержка):</b> [Ценовой диапазон, например: 61500 - 61700] ([Причина: FVG / OB / Liquidity Pool])
📈 <b>Зона Short (Сопротивление):</b> [Ценовой диапазон, например: 63000 - 63300] ([Причина: FVG / OB / Liquidity Pool])
🔔 <b>Алерты для Binance:</b> 🔽 <code>[Точная цена алерта вниз]</code> | 🔼 <code>[Точная цена алерта вверх]</code>
"""

def generate_coin_alert(coin):
    print(f"🔎 Сканирую уровни SMC для {coin}...")
    
    # ⚡️ 1. Глубокий прогрев индикаторов (500 свечей)
    df_4h = fetch_candles(coin, '4h', limit=500)
    df_15m = fetch_candles(coin, '15m', limit=500)
    
    if df_4h is None or df_15m is None or len(df_4h) < 100 or len(df_15m) < 100:
        return None
        
    df_4h['ema99'] = calculate_ema(df_4h, 99)
    df_15m['rsi6'] = calculate_rsi(df_15m, 6)
    macd_line, macd_signal, macd_hist = calculate_macd(df_15m)
    df_15m['macd_line'] = macd_line
    df_15m['macd_signal'] = macd_signal
    df_15m['macd_hist'] = macd_hist
    
    # ⚡️ 2. Расчет исторической базы по закрытым свечам 4H (Индекс -2)
    prev_candles_4h = df_4h.iloc[-22:-2]
    peak_high_4h = float(prev_candles_4h['high'].max())
    peak_low_4h = float(prev_candles_4h['low'].min())
    
    last_ema99_4h = float(df_4h['ema99'].iloc[-2]) if df_4h['ema99'].iloc[-2] is not None else 0
    
    # Текущая актуальная цена
    last_close_15m = float(df_15m['close'].iloc[-1])
    last_rsi6 = float(df_15m['rsi6'].iloc[-1]) if df_15m['rsi6'].iloc[-1] is not None else 50
    
    trend_emoji = "🟢 Бычий" if last_close_15m > last_ema99_4h else "🔴 Медвежий"
    
    prompt = f"{ALERTS_INSTRUCTION}\n\n" \
             f"Анализируемый актив: {coin}\n" \
             f"Текущая цена: {last_close_15m:.4f}\n" \
             f"Глобальный тренд (4H): {trend_emoji} (Цена {'ВЫШЕ' if last_close_15m > last_ema99_4h else 'НИЖЕ'} EMA99: {last_ema99_4h:.4f})\n" \
             f"Пиковые уровни ликвидности 4H: High {peak_high_4h:.4f} | Low {peak_low_4h:.4f}\n" \
             f"Моментум 15m (RSI 6): {last_rsi6:.2f}\n\n" \
             f"Последние 20 свечей (4H):\n{df_4h.tail(20).to_string()}\n\n" \
             f"Последние 20 свечей (15m):\n{df_15m.tail(20).to_string()}"
             
    try:
        response = model.generate_content(prompt).text
        # Безопасное экранирование тегов
        safe_response = response.replace("<", "&lt;").replace(">", "&gt;")
        safe_response = safe_response.replace("&lt;b&gt;", "<b>").replace("&lt;/b&gt;", "</b>")
        safe_response = safe_response.replace("&lt;code&gt;", "<code>").replace("&lt;/code&gt;", "</code>")
        
        return f"💎 <b>{coin}</b> | Тренд: {trend_emoji}\n{safe_response}"
    except Exception as e:
        print(f"❌ Ошибка генерации для {coin}: {e}")
        return None

def run_morning_alerts():
    work_pairs = ['BTC', 'ETH', 'SOL', 'XRP', 'ADA', 'LINK', 'INJ', 'HYPE', 'LTC', 'DOT']
    send_telegram_alert("🌅 <b>УТРЕННЯЯ SMC РАЗМЕТКА [08:30]</b>\nTraider, сканирую зоны интереса для расстановки ручных алертов с движком V10.1...")
    
    alerts_report = []
    for coin in work_pairs:
        report = generate_coin_alert(coin)
        if report:
            alerts_report.append(report)
        time.sleep(2) 
        
    if alerts_report:
        chunk_size = 2
        for i in range(0, len(alerts_report), chunk_size):
            chunk = "\n\n──────────────────\n\n".join(alerts_report[i:i + chunk_size])
            send_telegram_alert(chunk)
            time.sleep(1)
            
    send_telegram_alert("✅ <b>Разметка завершена.</b>\nВыставь алерты в Binance. Удачной охоты!")

if __name__ == "__main__":
    print("🚀 Генератор утренних алертов SMC (V10.1 Engine) запущен. Жду 08:30...")
    run_morning_alerts()
    
    while True:
        t_now = time.time()
        local_struct = time.gmtime(t_now + 10800) 
        
        current_hour = local_struct.tm_hour
        current_minute = local_struct.tm_min
        
        if current_hour == 8 and current_minute == 30:
            print("⏰ Время пришло (08:30). Запускаю генерацию алертов...")
            run_morning_alerts()
            time.sleep(61)
        else:
            time.sleep(30)