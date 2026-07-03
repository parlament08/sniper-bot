import os
import time
import requests
import pandas as pd
from macro_context import get_macro_context
from market_data import fetch_candles
import google.generativeai as genai
from datetime import time as dt_time

# Инициализация API Gemini
api_key = os.environ.get("GEMINI_API_KEY")
if not api_key:
    raise ValueError("❌ Не найден GEMINI_API_KEY. Выполни в терминале: export GEMINI_API_KEY='твой_ключ'")

genai.configure(api_key=api_key)

# Настройки Telegram
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

# --- МАТЕМАТИЧЕСКИЙ ДВИЖОК ИНДИКАТОРОВ И СТРУКТУРЫ ---

def calculate_ema(df, period=99):
    """Рассчитывает экспоненциальную скользящую среднюю (EMA)"""
    try:
        close_prices = df['close'].astype(float)
        return close_prices.ewm(span=period, adjust=False).mean()
    except Exception as e:
        print(f"⚠️ Ошибка расчета EMA: {e}")
        return None

def calculate_rsi(df, period=6):
    """Рассчитывает стандартный индекс относительной силы (RSI)"""
    try:
        close_prices = df['close'].astype(float)
        delta = close_prices.diff()
        gain = delta.clip(lower=0)
        loss = -delta.clip(upper=0)
        
        # Стандартное сглаживание по методу Уайлдера
        ema_gain = gain.ewm(com=period - 1, adjust=False).mean()
        ema_loss = loss.ewm(com=period - 1, adjust=False).mean()
        
        rs = ema_gain / ema_loss
        rsi = 100 - (100 / (1 + rs))
        return rsi
    except Exception as e:
        print(f"⚠️ Ошибка расчета RSI: {e}")
        return None

def calculate_macd(df, fast=12, slow=26, signal=9):
    """Рассчитывает схождение/расхождение скользящих средних (MACD)"""
    try:
        close_prices = df['close'].astype(float)
        exp1 = close_prices.ewm(span=fast, adjust=False).mean()
        exp2 = close_prices.ewm(span=slow, adjust=False).mean()
        macd_line = exp1 - exp2
        signal_line = macd_line.ewm(span=signal, adjust=False).mean()
        histogram = macd_line - signal_line
        return macd_line, signal_line, histogram
    except Exception as e:
        print(f"⚠️ Ошибка расчета MACD: {e}")
        return None, None, None

# --- СИСТЕМНАЯ ИНСТРУКЦИЯ V.10.1 (STRICT LAYOUT & SAFE HTML) ---
SNIPER_INSTRUCTION = """
Ты — профессиональный алгоритмический трейдер Traider. Твоя торговая стратегия основана на Smart Money Concepts (SMC) с применением жесткой фильтрации структуры и индикаторов.

РЕГЛАМЕНТ ПРИНЯТИЯ РЕШЕНИЙ:
1. АНАЛИЗ ГЛОБАЛЬНОГО КОНТЕКСТА:
   - Определи HTF Bias (4H) и тренд по DXY, SPX, BTC.D.
   - Сравни это с текущим Price Action на 15m.
   - Если нет синхронизации (например, рост крипты при растущем DXY) — АКТИВИРУЙ ВЕТО.

2. МОДУЛЬ ФИЛЬТРАЦИИ И СЕССИЙ:
   - ТЕКУЩИЙ СТАТУС СЕССИИ: {session_status}
   - АБСОЛЮТНОЕ ПРАВИЛО СЕССИЙ: Тебе ЗАПРЕЩЕНО самостоятельно вычислять торговые сессии. Опирайся ТОЛЬКО на переданный "ТЕКУЩИЙ СТАТУС СЕССИИ".
   - Если статус "ВНЕ KILL ZONE" — статус сделки строго "НАБЛЮДЕНИЕ".
   - Если статус "В KILL ZONE" — ищи сетап. Если сетапа нет, статус "НАБЛЮДЕНИЕ", но в логике ссылайся ТОЛЬКО на отсутствие структуры (CHoCH, FVG), а НЕ на время!

3. ЖЕСТКИЕ ТЕХНИЧЕСКИЕ ИНДИКАТОРНЫЕ И СТРУКТУРНЫЕ ПРАВИЛА:
   - ГЛОБАЛЬНЫЙ ТРЕНД (EMA 99 на 4H): 
     * Если Текущая цена НИЖЕ EMA(99) на 4H — глобальный тренд СТРОГО медвежий. Запрещено называть структуру 4H бычьей! Лонги блокируются.
     * Если Текущая цена ВЫШЕ EMA(99) на 4H — глобальный тренд бычий.
   
   - БЛОКИРОВКА SFP (SWING FAILURE PATTERN):
     * Истинный пробой уровня (BOS) подтверждается ТОЛЬКО когда тело свечи закрывается за уровнем.
     * Если цена заколола предыдущий максимум `peak_high_4h`, но вернулась обратно и тело свечи 4H не закрепилось выше уровня — это SFP (Снятие ликвидности). Вход в LONG в сторону пробоя СТРОГО ЗАПРЕЩЕНО. В таком случае приоритетен SHORT на разворот структуры.
     * Если цена заколола локальный минимум `peak_low_4h`, но тело свечи не закрепилось ниже — это SFP снизу. Вход в SHORT строго запрещен.

   - ПЕРЕГРЕТОСТЬ РЫНКА (RSI 6 на 15m) И ИМПУЛЬС (MACD):
     * Если RSI(6) > 75 — рынок критически ПЕРЕКУПЛЕН. Открытие любых LONG-позиций СТРОГО ЗАПРЕЩЕНО.
     * Если RSI(6) < 25 — рынок критически ПЕРЕПРОДАН. Открытие любых SHORT-позиций СТРОГО ЗАПРЕЩЕНО.
     * Учитывай состояние гистограммы MACD: затухание гистограммы (смена цвета/высоты столбиков) около ключевых POI — сильный ранний признак разворота.

   - ФИЛЬТР ПАДАЮЩЕГО НОЖА (No Knife Catching):
     * Если последняя закрытая свеча 15m является полнотелой медвежьей (направление Bearish 🔴 и соотношение тела к диапазону > 0.60), ловить лонги лимитными ордерами ЗАПРЕЩЕНО. Вход в LONG возможен только после появления разворотной свечи с длинной нижней тенью.

4. АЛГОРИТМ ВЫБОРА НАПРАВЛЕНИЯ (LONG/SHORT):
   - ПРИОРИТЕТ ШОРТ: Если SPX/DXY указывают на Risk-Off, а цена ниже 4H EMA(99) ИЛИ сформирован SFP на пиковом максимуме — ищи паттерны на ПРОДАЖУ при условии, что RSI(6) на 15m в диапазоне 35-75.
   - ПРИОРИТЕТ ЛОНГ: Если DXY падает, SPX стабилен, цена выше 4H EMA(99) И цена закрепилась ТЕЛОМ выше предыдущего хая — ищи паттерны на ПОКУПКУ при условии, что RSI(6) на 15m в диапазоне 25-65.
   - Если R:R сделки ниже 1:3.5 — статус строго "НАБЛЮДЕНИЕ" (низкий R:R).

СТРОГИЙ РЕГЛАМЕНТ ВЫБОРА ФОРМАТА ОТВЕТА:
Проанализируй рынок. Если условия для открытия сделки не идеальны — ты обязан выбрать ШАБЛОН 1. Если сформирован истинный сетап А+ в Kill Zone — выбери ШАБЛОН 2.

=== ШАБЛОН 1: ЕСЛИ СТАТУС "НАБЛЮДЕНИЕ" (КАТЕГОРИЧЕСКИ ЗАПРЕЩЕНО ВЫВОДИТЬ ПОЛЯ ОРДЕРА) ===
🛑 **СТАТУС: НАБЛЮДЕНИЕ (NO TRADE)**
• **Причина:** [СТРОГО начни с одной из фраз: "Сетап не сформирован. Ждем CHoCH.", "Вето по SFP.", "Вето по RSI перекупленности/перепроданности.", "Вето по Падающему ножу." или "Вне торговой сессии." Далее добавь краткий технический контекст в одно предложение (например, затухание MACD или премаркет)]

📊 **ТЕХНИЧЕСКИЙ АНАЛИЗ [Asset/USDT] ([TF])**
• **Market Structure:** (Bias. Укажи положение цены относительно 4H EMA(99) и пиков peak_high_4h/peak_low_4h, текущую LTF 15m структуру, значение RSI(6) и MACD).
• **Liquidity & FVG:** (Где деньги, пулы ликвидности, имбалансы, цели).
• **Межрыночный фон:** (DXY, SPX, BTC.D — сила тренда, корреляция и состояние МЕЖРЫНОЧНОГО ВЕТО-ФИЛЬТРА. Если DXY и SPX падают одновременно — это КРИТИЧЕСКИЙ РАССИНХРОН, ЛОНГ-позиции блокируются).

=== ШАБЛОН 2: ЕСЛИ СТАТУС "A+ SETUP" (ВЫВОДИТСЯ ТОЛЬКО ДЛЯ РЕАЛЬНЫХ СДЕЛОК) ===
🎯 **СТАТУС: [Укажи строго "A+ SETUP (LONG)" или "A+ SETUP (SHORT)"]**
• **Логика:** (Опиши структуру и почему это сетап А+)
• **Точка входа:** [Конкретная цена]
• **Stop-Loss:** [Конкретная цена]
• **Take-Profit:** [Конкретная цена]
• **R:R:** [Соотношение, минимум 1:3.5]
• **Расчет позиции:** (Формула: (5$ / разница входа и стопа) * цена входа. Укажи объем в USDT)

📊 **ТЕХНИЧЕСКИЙ АНАЛИЗ [Asset/USDT] ([TF])**
• **Market Structure:** (Bias. Укажи положение цены относительно 4H EMA(99) и пиков peak_high_4h/peak_low_4h, текущую LTF 15m структуру, значение RSI(6) и MACD).
• **Liquidity & FVG:** (Где деньги, пулы ликвидности, имбалансы, цели).
• **Межрыночный фон:** (DXY, SPX, BTC.D — сила тренда, корреляция и состояние МЕЖРЫНОЧНОГО ВЕТО-ФИЛЬТРА).

⚠️ ПРАВИЛО ОПИСАНИЯ СВЕЧЕЙ: При описании графиков КАТЕГОРИЧЕСКИ ЗАПРЕЩЕНО упоминать время конкретных свечей. Используй только ценовые уровни и структурные термины.

🔥 ГОРЯЧИЕ ПРАВИЛА:
- ДИРЕКТИВА СЕССИИ: {phase_rule}
- Макроэкономический VETO: Если в блоке фона переданы новости высокой важности (CPI/FOMC/NFP) — ИИ обязан выставить статус "НАБЛЮДЕНИЕ" и заблокировать любые входы за 30 минут до и 30 минут после публикации.
- Нью-Йоркский Judas Swing: На Нью-Йоркской сессии отдавай приоритет паттернам SFP (ложный пробой), которые снимают экстремумы прошедшей Лондонской сессии. Это сетапы А+.
- Никакой гибкости: Вне Kill Zone = strict veto.
- При использовании ШАБЛОНА 1 (НАБЛЮДЕНИЕ) не генерируй пустые строки для Точки входа, Стопа или Тейка. Ограничься только ШАБЛОНОМ 1!
"""

model = genai.GenerativeModel(model_name='models/gemini-3.1-flash-lite')

def md_to_html(text):
    """
    Конвертирует простейшую markdown-разметку (**текст** и `код`) в HTML-теги для Telegram.
    Экранирует опасные символы '<' и '>', предотвращая краши парсера Telegram API.
    """
    if not text:
        return ""
    
    # ⚡️ ЭКРАНИРОВАНИЕ: меняем системные скобки на безопасные HTML сущности
    safe_text = text.replace("<", "&lt;").replace(">", "&gt;")
    
    # Конвертируем **жирный** в <b>жирный</b>
    parts_bold = safe_text.split("**")
    for i in range(1, len(parts_bold), 2):
        parts_bold[i] = f"<b>{parts_bold[i]}</b>"
    text_bold = "".join(parts_bold)
    
    # Конвертируем `код` в <code>код</code>
    parts_code = text_bold.split("`")
    for i in range(1, len(parts_code), 2):
        parts_code[i] = f"<code>{parts_code[i]}</code>"
    
    return "".join(parts_code)

def send_telegram_alert(text):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": text, "parse_mode": "HTML"}
    try:
        requests.post(url, data=payload, timeout=5)
    except Exception as e:
        print(f"❌ Не удалось отправить пуш в Telegram: {e}")

def prepare_and_analyze(coin, macro_str, session_status, current_time_str, phase_rule):
    print(f"\n🧠 Анализ {coin}...")
    # Увеличиваем лимит до 500 свечей для устранения погрешности прогрева EMA и MACD
    df_4h = fetch_candles(coin, '4h', limit=500)
    df_15m = fetch_candles(coin, '15m', limit=500)
    
    if df_4h is None or df_15m is None or len(df_4h) < 100 or len(df_15m) < 100:
        return None, None
    
    # Расчет скользящих, RSI и MACD на прогретых исторических данных
    df_4h['ema99'] = calculate_ema(df_4h, 99)
    df_15m['rsi6'] = calculate_rsi(df_15m, 6)
    
    macd_line, macd_signal, macd_hist = calculate_macd(df_15m)
    df_15m['macd_line'] = macd_line
    df_15m['macd_signal'] = macd_signal
    df_15m['macd_hist'] = macd_hist
    
    # Рассчитываем локальные пики за последние 20 закрытых свечей 4H (индекс -22:-2)
    prev_candles_4h = df_4h.iloc[-22:-2]
    peak_high_4h = float(prev_candles_4h['high'].max())
    peak_low_4h = float(prev_candles_4h['low'].min())
    
    # Свежие метрики последней закрытой 4H свечи (индекс -2)
    last_close_4h = float(df_4h['close'].iloc[-2])
    last_high_4h = float(df_4h['high'].iloc[-2])
    last_low_4h = float(df_4h['low'].iloc[-2])
    last_ema99 = float(df_4h['ema99'].iloc[-2]) if df_4h['ema99'].iloc[-2] is not None else 0
    
    # Самая последняя цена (close текущей незакрытой 15m свечи)
    last_close_15m = float(df_15m['close'].iloc[-1])
    
    # Оценка последней закрытой свечи 15m на предмет "падающего ножа"
    last_closed_15m = df_15m.iloc[-2]
    c_open = float(last_closed_15m['open'])
    c_close = float(last_closed_15m['close'])
    c_high = float(last_closed_15m['high'])
    c_low = float(last_closed_15m['low'])
    
    body_size = abs(c_close - c_open)
    total_range = (c_high - c_low) if (c_high - c_low) > 0 else 0.0001
    body_ratio = body_size / total_range
    is_bearish_15m = c_close < c_open
    
    last_rsi6 = float(df_15m['rsi6'].iloc[-1]) if df_15m['rsi6'].iloc[-1] is not None else 50
    last_macd_line = float(df_15m['macd_line'].iloc[-1]) if df_15m['macd_line'].iloc[-1] is not None else 0
    last_macd_signal = float(df_15m['macd_signal'].iloc[-1]) if df_15m['macd_signal'].iloc[-1] is not None else 0
    last_macd_hist = float(df_15m['macd_hist'].iloc[-1]) if df_15m['macd_hist'].iloc[-1] is not None else 0
    
    # Проверяем потенциальное состояние SFP прямо сейчас
    sfp_high_detected = "ДА" if (last_high_4h > peak_high_4h and last_close_4h < peak_high_4h) else "НЕТ"
    sfp_low_detected = "ДА" if (last_low_4h < peak_low_4h and last_close_4h > peak_low_4h) else "НЕТ"

    # Собираем технический контекст в промпт
    prompt = f"{SNIPER_INSTRUCTION.format(session_status=session_status, phase_rule=phase_rule)}\n\n" \
             f"🕒 ЛОКАЛЬНОЕ ВРЕМЯ (МСК/Кишинев): {current_time_str}\n" \
             f"📐 МАТЕМАТИЧЕСКИЕ ИНДИКАТОРЫ И СТРУКТУРНЫЕ ДАННЫЕ:\n" \
             f"• Текущая рыночная цена {coin}: {last_close_15m:.4f}\n" \
             f"• Глобальная EMA(99) на 4H: {last_ema99:.4f} (Цена {'ВЫШЕ' if last_close_15m > last_ema99 else 'НИЖЕ'} EMA99)\n" \
             f"• Предыдущий пиковый максимум 4H (Ликвидность сверху): {peak_high_4h:.4f}\n" \
             f"• Предыдущий пиковый минимум 4H (Ликвидность снизу): {peak_low_4h:.4f}\n" \
             f"• Текущие экстремумы последней закрытой 4H свечи: High {last_high_4h:.4f} | Low {last_low_4h:.4f}\n" \
             f"• Признак SFP максимума (Закол сверху без закрытия телом): {sfp_high_detected}\n" \
             f"• Признак SFP минимума (Закол снизу без закрытия телом): {sfp_low_detected}\n" \
             f"• Сила импульса 15m RSI(6): {last_rsi6:.2f}\n" \
             f"• Последняя закрытая свеча 15m: Направление: {'Bearish 🔴' if is_bearish_15m else 'Bullish 🟢'} | Полнотелость (тело/диапазон): {body_ratio:.2f} {'(Опасность ПАДАЮЩЕГО НОЖА!)' if (is_bearish_15m and body_ratio > 0.60) else ''}\n\n" \
             f"⚠️ ВНИМАНИЕ: Таймстемпы в таблицах свечей ниже указаны по UTC (отстают на 3 часа). " \
             f"Для определения сессий ориентируйся СТРОГО на ЛОКАЛЬНОЕ ВРЕМЯ и ТЕКУЩИЙ СТАТУС СЕССИИ!\n\n" \
             f"АКТИВ: {coin} | ФОН: {macro_str}\n" \
             f"HTF (4H):\n{df_4h.tail(20).to_string()}\n" \
             f"LTF (15m) [MACD Line: {last_macd_line:.5f} | Signal: {last_macd_signal:.5f} | Hist: {last_macd_hist:.5f}]:\n{df_15m.tail(20).to_string()}"
    
    try:
        ai_response = model.generate_content(prompt).text
        metrics = {
            'price': last_close_15m,
            'ema99': last_ema99,
            'rsi6': last_rsi6,
            'sfp_high': sfp_high_detected,
            'sfp_low': sfp_low_detected
        }
        return ai_response, metrics
    except Exception as e:
        print(f"❌ Ошибка Gemini для {coin}: {e}")
        return None, None

def check_is_setup(result_text):
    """
    Отказоустойчивая проверка строки статуса.
    """
    if not result_text:
        return False
    
    lines = result_text.split('\n')
    status_line = ""
    for line in lines:
        if "статус:" in line.lower() or "status:" in line.lower():
            status_line = line.lower()
            break
            
    if not status_line:
        return False
        
    if "наблюдение" in status_line or "observation" in status_line or "no trade" in status_line:
        return False
        
    normalized_status = status_line.replace('а', 'a')
    
    setup_patterns = [
        "a+ setup",
        "a+ сетап",
        "сетап а+"
    ]
    
    return any(pattern in normalized_status for pattern in setup_patterns)

last_alert_time = {coin: 0 for coin in ['BTC', 'ETH', 'SOL', 'XRP', 'ADA', 'LINK', 'INJ', 'HYPE', 'LTC', 'DOT']}

def market_scan(report_mode="HUNT"):
    work_pairs = ['BTC', 'ETH', 'SOL', 'XRP', 'ADA', 'LINK', 'INJ', 'HYPE', 'LTC', 'DOT']
    
    t = time.time() + 10800
    local_struct = time.gmtime(t)
    
    hour = local_struct.tm_hour
    minute = local_struct.tm_min
    
    current_time_str = f"{hour:02d}:{minute:02d}"
    curr_t = dt_time(hour, minute)
    
    in_kz = (dt_time(10, 0) <= curr_t <= dt_time(12, 0)) or \
            (dt_time(15, 30) <= curr_t <= dt_time(18, 0))
    
    session_status = "В KILL ZONE" if in_kz else "ВНЕ KILL ZONE"
    
    if curr_t < dt_time(10, 0):
        phase_rule = 'СЕЙЧАС АЗИЯ (до 10:00). Если структура 15m флэт, ВЫВОДИ СТРОГО: "Азиатский флэт — ждем открытия Лондона для выноса ликвидности".'
    elif dt_time(15, 0) <= curr_t < dt_time(15, 30):
        phase_rule = 'СЕЙЧАС ПРЕМАРКЕТ США (15:00-15:30). Если сетапа нет, пиши СТРОГО: "Премаркет США — ожидаем открытия Нью-Йорка и реакцию на макроданные".'
    elif in_kz:
        phase_rule = 'СЕЙЧАС KILL ZONE. КАТЕГОРИЧЕСКИ ЗАПРЕЩЕНО писать про "Азиатский флэт" или "Премаркет США". Описывай только сетап по графику.'
    else:
        phase_rule = 'МЕЖСЕССИОННОЕ ВРЕМЯ. Рынок вне Kill Zone, ожидаем следующую сессию.'

    macro = get_macro_context()
    
    if report_mode == "FULL":
        print(f"\n📊 [{current_time_str}] Запуск планового брифинга...")
        send_telegram_alert(f"<b>🕒 РЫНОЧНЫЙ БРИФИНГ | {current_time_str}</b>\nНачинаю анализ рынка...\nСессия: {session_status}")
        
        macro_msg = (
            f"🌍 <b>ГЛОБАЛЬНЫЙ КОНТЕКСТ:</b>\n"
            f"💵 DXY: {macro.get('DXY', {}).get('trend')}\n"
            f"📈 S&P500: {macro.get('SPX', {}).get('trend')}\n"
            f"👑 BTC.D: {macro.get('BTC.D', {}).get('price')}% ({macro.get('BTC.D', {}).get('trend')})"
        )
        send_telegram_alert(macro_msg)
        time.sleep(1)
    else:
        print(f"\n🔎 [{current_time_str}] Поиск сетапов | Status: {session_status}")

    macro_str = f"DXY: {macro.get('DXY', {}).get('trend')} | SPX: {macro.get('SPX', {}).get('trend')} | BTC.D: {macro.get('BTC.D', {}).get('price')}%"
    
    hunt_metrics = []
    
    for coin in work_pairs:
        result, metrics = prepare_and_analyze(coin, macro_str, session_status, current_time_str, phase_rule)
        if not result: continue
            
        if metrics:
            hunt_metrics.append((coin, metrics))
            
        is_setup = check_is_setup(result)
        
        # Конвертируем Markdown-разметку ИИ в валидный Telegram HTML с экранированием опасных символов
        html_result = md_to_html(result)
        
        if report_mode == "FULL":
            status_emoji = "🎯 СЕТАП А+" if is_setup else "🛑 НАБЛЮДЕНИЕ"
            send_telegram_alert(f"<b>{status_emoji} | {coin}</b>\n\n{html_result}")
        elif is_setup:
            if time.time() - last_alert_time[coin] > 7200: 
                send_telegram_alert(f"🚨 <b>СНАЙПЕР ОБНАРУЖИЛ СЕТАП!</b> 🚨\n\n{html_result}")
                last_alert_time[coin] = time.time()
                
    # --- ОТПРАВКА HEARTBEAT ДАШБОРДА В РЕЖИМЕ HUNT ---
    if report_mode == "HUNT" and hunt_metrics:
        summary_lines = [
            f"📡 <b>СНАЙПЕР ОНЛАЙН | {current_time_str}</b>",
            f"⚡️ Сессия: <code>{session_status}</code>",
            f"🌍 Макро: <code>{macro_str}</code>",
            f"────────────────"
        ]
        for coin, m in hunt_metrics:
            trend_emoji = "🟢" if m['price'] > m['ema99'] else "🔴"
            
            rsi_val = m['rsi6']
            rsi_suffix = ""
            if rsi_val > 75:
                rsi_suffix = " 🔥"
            elif rsi_val < 25:
                rsi_suffix = " ❄️"
                
            sfp_str = "❌"
            if m['sfp_high'] == "ДА":
                sfp_str = "⚡️ SFP H"
            elif m['sfp_low'] == "ДА":
                sfp_str = "⚡️ SFP L"
                
            summary_lines.append(
                f"• <b>{coin}</b>: {m['price']:.2f}$ | {trend_emoji} | RSI: {rsi_val:.1f}{rsi_suffix} | SFP: {sfp_str}"
            )
            
        send_telegram_alert("\n".join(summary_lines))

if __name__ == "__main__":
    print("🚀 Радар «СНАЙПЕР» онлайн. Версия 10.1 [Strict Layout & Safe HTML] запущена.")
    
    send_telegram_alert("👋 <b>СИСТЕМА ОНЛАЙН [V.10.1]</b>\nTraider, радар СНАЙПЕР запущен. Форматирование вывода приведено к золотому стандарту.")
    
    # Первоначальный сканирующий брифинг при старте
    market_scan(report_mode="FULL")
    
    print("\n⏰ Вхожу в режим точной синхронизации с 15-минутными свечами...")
    
    while True:
        t_now = time.time()
        
        # Вычисляем секунды до следующей ровной 15-минутки (900 секунд) по системному timestamp
        seconds_past_quarter = int(t_now) % 900
        seconds_to_wait = 900 - seconds_past_quarter
        
        # Добавляем буфер в 2 секунды на обновление исторических свечей на биржах
        seconds_to_wait += 2
        
        # Точное локальное время следующего запуска для логов
        next_run_time = time.gmtime(t_now + seconds_to_wait + 10800)
        print(f"💤 Ожидаю закрытия свечи. Следующее сканирование запустится ровно в: {next_run_time.tm_hour:02d}:{next_run_time.tm_min:02d}:02")
        
        time.sleep(seconds_to_wait)
        
        # ⚡️ АВТОМАТИЧЕСКОЕ ПЕРЕКЛЮЧЕНИЕ РЕЖИМОВ 09:00 / 15:00
        # Если следующая свеча закроется ровно в 9:00 или 15:00, запускаем плановый брифинг (FULL)
        if next_run_time.tm_min == 0 and next_run_time.tm_hour in [9, 15]:
            current_mode = "FULL"
        else:
            current_mode = "HUNT"
            
        try:
            market_scan(report_mode=current_mode)
        except Exception as e:
            print(f"❌ Критическая ошибка во время сканирования рынка: {e}")