import os
import time
import json
import requests
import pandas as pd
import google.generativeai as genai
from datetime import time as dt_time
from dotenv import load_dotenv

from core.logger import logger
from services.market_data import fetch_candles
#from services.macro_context import get_macro_context, check_macro_confirmation
from services.macro_context import get_macro_context, evaluate_macro_score
from core.structure import find_swings, find_fvg, detect_structure_break, detect_sfp
from core.indicators import calculate_ema, calculate_atr, calculate_rvol, calculate_adx, evaluate_trend
from core.risk import calculate_setup_score

# Загружаем переменные окружения из .env файла
load_dotenv()

# Инициализация API Gemini
api_key = os.environ.get("GEMINI_API_KEY")
if not api_key:
    raise ValueError("❌ Не найден GEMINI_API_KEY. Выполни в терминале: export GEMINI_API_KEY='твой_ключ'")

genai.configure(api_key=api_key)

# Настройки Telegram
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

A_PLUS_NARRATOR_INSTRUCTION = """
Ты — профессиональный финансовый диктор. Оформи этот JSON с А+ сетапом в красивый HTML для Telegram с тегами <b> и <code>. Ничего не выдумывай от себя.
"""

model = genai.GenerativeModel(model_name='models/gemini-3.1-flash-lite')
COINS_LIST = ['BTC', 'ETH', 'SOL', 'XRP', 'ADA', 'LINK', 'INJ', 'HYPE', 'LTC', 'DOT']
last_alert_time = {coin: 0 for coin in COINS_LIST}

def send_telegram_alert(text):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": text, "parse_mode": "HTML"}
    try:
        res = requests.post(url, data=payload, timeout=10)
        if res.status_code != 200:
            logger.error(f"Ошибка Telegram API: {res.text}")
    except Exception as e:
        logger.error(f"Не удалось отправить пуш в Telegram: {e}")

def prepare_and_analyze(coin, macro_context):
    df_4h = fetch_candles(coin, '4h', limit=200)
    df_15m = fetch_candles(coin, '15m', limit=150) # Увеличен лимит для прогрева индикаторов

    if df_4h is None or df_15m is None or len(df_4h) < 100 or len(df_15m) < 100:
        logger.warning(f"Недостаточно данных для {coin}.")
        return None, None

    numeric_cols = ['open', 'high', 'low', 'close', 'volume']
    for col in numeric_cols:
        df_4h[col] = df_4h[col].astype(float)
        df_15m[col] = df_15m[col].astype(float)

    df_4h['ema99'] = calculate_ema(df_4h, 99)
    adx_df = calculate_adx(df_4h, 14)
    if adx_df is not None:
        df_4h = df_4h.join(adx_df)

    df_15m['atr'] = calculate_atr(df_15m, 14)
    df_15m['rvol'] = calculate_rvol(df_15m, 20)

    df_4h.dropna(inplace=True)
    df_15m.dropna(inplace=True)

    if df_4h.empty or df_15m.empty:
        logger.warning(f"Недостаточно данных для {coin} после расчета индикаторов.")
        return None, None

    df_15m_closed = df_15m.iloc[:-1].copy()
    last_closed_15m = df_15m_closed.iloc[-1]
    window_15m = df_15m_closed.tail(100)
    
    # ❗️ ВАЖНО: Тренд оценивается по 4H данным для глобального контекста
    trend_data = evaluate_trend(df_4h.iloc[:-1])

    # --- Анализ в окне памяти (20 свечей) ---
    sfp_data_in_window = None
    structure_data_in_window = None
    is_bullish_fvg_tested_in_window = False
    is_bearish_fvg_tested_in_window = False

    swing_config = {'left_bars': 5, 'right_bars': 3}
    swing_highs_full, swing_lows_full = find_swings(df_15m_closed, **swing_config)

    # Итерируемся по окну С КОНЦА, чтобы найти ПОСЛЕДНИЕ (самые релевантные) события SFP и BOS
    for index, candle in window_15m.iloc[::-1].iterrows():
        swings_before_candle_h = swing_highs_full[swing_highs_full.index < index]
        swings_before_candle_l = swing_lows_full[swing_lows_full.index < index]

        # Ищем SFP (только если еще не нашли)
        if not sfp_data_in_window:
            sfp = detect_sfp(candle, swings_before_candle_h, swings_before_candle_l, right_bars=swing_config['right_bars'])
            if sfp:
                sfp['rvol'] = candle.get('rvol', 0)
                sfp_data_in_window = sfp

        # Ищем BOS/CHoCH (только если еще не нашли)
        if not structure_data_in_window:
            structure_break = detect_structure_break(candle, swings_before_candle_h, swings_before_candle_l, right_bars=swing_config['right_bars'])
            if structure_break:
                # 'rvol' уже добавляется в detect_structure_break
                structure_data_in_window = structure_break
        
        # Если нашли оба самых свежих паттерна, выходим для оптимизации
        if sfp_data_in_window and structure_data_in_window:
            break

    all_fvgs = find_fvg(df_15m_closed, atr_series=df_15m_closed['atr'], min_size_atr_ratio=0.5)
    for fvg in all_fvgs:
        fvg_bottom, fvg_top = fvg['bottom'], fvg['top']
        if fvg['type'] == 'bullish' and any((window_15m['low'] <= fvg_top) & (window_15m['high'] >= fvg_bottom)): is_bullish_fvg_tested_in_window = True
        elif fvg['type'] == 'bearish' and any((window_15m['low'] <= fvg_top) & (window_15m['high'] >= fvg_bottom)): is_bearish_fvg_tested_in_window = True
        if is_bullish_fvg_tested_in_window and is_bearish_fvg_tested_in_window: break

    current_price = float(last_closed_15m['close'])

    is_altcoin = coin != "BTC"
    
    # ✅ НОВАЯ ЛОГИКА МАКРО
    long_macro_score, long_macro_reason = evaluate_macro_score('long', macro_context, is_altcoin=is_altcoin)
    short_macro_score, short_macro_reason = evaluate_macro_score('short', macro_context, is_altcoin=is_altcoin)

    long_macro_data = {'score': long_macro_score, 'reason': long_macro_reason}
    short_macro_data = {'score': short_macro_score, 'reason': short_macro_reason}
    
    # ✅ ВОТ ЭТИ ДВЕ СТРОКИ НУЖНО ВЕРНУТЬ (Вызов калькулятора баллов):
    long_score = calculate_setup_score('long', current_price, trend_data, structure_data_in_window, sfp_data_in_window, is_bullish_fvg_tested_in_window, all_fvgs, None, long_macro_data)
    short_score = calculate_setup_score('short', current_price, trend_data, structure_data_in_window, sfp_data_in_window, is_bearish_fvg_tested_in_window, all_fvgs, None, short_macro_data)

    if long_score['total_score'] >= short_score['total_score']:
        final_score_result = long_score
        direction = "LONG"
    else:
        final_score_result = short_score
        direction = "SHORT"    

    analysis_data = {
        "trend_data": trend_data,
        "structure_data": structure_data_in_window,
        "direction": direction,
        "last_closed_15m": last_closed_15m,
    }

    return final_score_result, analysis_data

def market_scan(report_mode="HUNT"):
    # Логика времени (Кишинев UTC+3)
    t = time.time() + 10800
    local_struct = time.gmtime(t)
    current_time_str = f"{local_struct.tm_hour:02d}:{local_struct.tm_min:02d}"
    curr_t = dt_time(local_struct.tm_hour, local_struct.tm_min)
    
    in_kz = (dt_time(10, 0) <= curr_t <= dt_time(12, 0)) or \
            (dt_time(15, 30) <= curr_t <= dt_time(18, 0))
    session_status = "В KILL ZONE" if in_kz else "ВНЕ KILL ZONE"
    
    macro = get_macro_context()
    macro_str = f"DXY: {macro.get('DXY', {}).get('trend')} | SPX: {macro.get('SPX', {}).get('trend')} | BTC.D: {macro.get('BTC.D', {}).get('price')}%"
    logger.info(f"[{current_time_str}] Запуск сканирования | Режим: {report_mode} | Сессия: {session_status}")

    dashboard_lines = []

    for coin in COINS_LIST:
        try:
            score_result, analysis_data = prepare_and_analyze(coin, macro)
            if not score_result or not analysis_data:
                dashboard_lines.append(f"• <b>{coin}</b>: Н/Д (ошибка данных)")
                continue

            total_score = score_result.get('total_score', 0)
            is_high_score_setup = total_score >= 85
            #is_high_score_setup = total_score >= 0
            decision = score_result.get('decision', 'Ignore') if in_kz else "Ignore"

            # Отправка А+ сетапа
            if is_high_score_setup and in_kz:
                direction = analysis_data['direction']
                atr = analysis_data['last_closed_15m']['atr']
                entry_price = analysis_data.get('structure_data', {}).get('level')

                if entry_price and (time.time() - last_alert_time.get(coin, 0) > 7200):
                    entry_price = float(entry_price)
                    if direction == 'LONG':
                        stop_loss = entry_price - 2 * atr
                        take_profit = entry_price + 3 * (entry_price - stop_loss)
                    else:
                        stop_loss = entry_price + 2 * atr
                        take_profit = entry_price - 3 * (stop_loss - entry_price)

                    setup_details_json = {
                        "coin": coin, 
                        "direction": direction,
                        "entry_price": f"{entry_price:.4f}",
                        "stop_loss": f"{stop_loss:.4f}",
                        "take_profit": f"{take_profit:.4f}",
                        "score": total_score
                    }
                    
                    prompt = f"{A_PLUS_NARRATOR_INSTRUCTION}\n\nJSON ДАННЫЕ:\n{json.dumps(setup_details_json, indent=2, ensure_ascii=False)}"
                    try:
                        ai_response = model.generate_content(prompt).text
                        send_telegram_alert(f"🚨 <b>СНАЙПЕР ОБНАРУЖИЛ СЕТАП! ({total_score}/100)</b> 🚨\n\n{ai_response}")
                        last_alert_time[coin] = time.time()
                    except Exception as e:
                        logger.error(f"Ошибка Gemini для A+ сетапа {coin}: {e}")

            trend_data = analysis_data.get('trend_data')
            trend_strength = trend_data.get('strength', 'Н/Д') if trend_data else "Н/Д"
            
            # Добавляем строку в дашборд
            # if coin in ['BTC', 'ETH', 'SOL']:
            if coin in COINS_LIST:
                breakdown = score_result.get('breakdown', {})
                direction = analysis_data['direction']
                
                # 1. Форматируем заголовок с направлением сетапа (15m)
                setup_direction_text = direction
                setup_emoji = "🟢" if direction == "LONG" else "🔴"
                header = f"💎 <b>{coin}</b> | Сетап: <b>{setup_direction_text} {setup_emoji}</b> | Score: <b>{total_score}/100</b> | {decision}"

                # 2. Форматируем строку тренда с направлением (4H)
                trend_data = analysis_data.get('trend_data')
                trend_4h_direction = "Н/Д"
                if trend_data and 'is_bullish' in trend_data:
                    trend_4h_direction = "ВВЕРХ ↗️" if trend_data['is_bullish'] else "ВНИЗ ↘️"
                
                trend_line = f"📊 Тренд (4H): {trend_4h_direction} | {breakdown.get('trend', '0')}"
                structure_line = f"⚙️ Структура: {breakdown.get('structure', '0')}"
                liquidity_line = f"💧 Ликвидность: {breakdown.get('liquidity', '0')}"
                fvg_line = f"🎯 FVG: {breakdown.get('fvg', '0')}"
                volume_line = f"📈 Объем: {breakdown.get('volume', '0')}"
                macro_line = f"🌍 Макро: {breakdown.get('macro', '0')}"
                separator = "──────────────────"
                
                detailed_report = "\n".join([header, trend_line, structure_line, liquidity_line, fvg_line, volume_line, macro_line, separator])
                dashboard_lines.append(detailed_report)
            else:
                dashboard_lines.append(f"• <b>{coin}</b>: {total_score} баллов | {decision} | Тренд: {trend_strength}")

        except Exception as e:
            logger.error(f"Критическая ошибка при анализе {coin}: {e}", exc_info=True)
            dashboard_lines.append(f"• <b>{coin}</b>: ОШИБКА АНАЛИЗА")

    # Отправка единого дашборда
    if dashboard_lines and (report_mode == "FULL" or in_kz):
        header_text = "РЫНОЧНЫЙ БРИФИНГ" if report_mode == "FULL" else "СНАЙПЕР ОНЛАЙН"
        summary_header = [
            f"📡 <b>{header_text} | {current_time_str}</b>",
            f"⚡️ Сессия: <code>{session_status}</code>",
            f"🌍 Макро: <code>{macro_str}</code>",
            "────────────────"
        ]
        full_message = "\n".join(summary_header + dashboard_lines)
        send_telegram_alert(full_message)
    elif report_mode == "HUNT" and not in_kz:
        logger.info(f"[{current_time_str}] Вне Kill Zone. Дашборд скрыт для экономии эфира.")

if __name__ == "__main__":
    logger.info("🚀 Радар «СНАЙПЕР» онлайн. Версия 11.0 [Orchestrator Deterministic] запущена.")
    send_telegram_alert("👋 <b>СИСТЕМА ОНЛАЙН [V.11.0]</b>\nRadar запущен. Включен новый детерминированный Score Engine.")
    
    # Первый запуск при старте
    market_scan(report_mode="FULL")
    
    while True:
        t_now = time.time()
        seconds_past_quarter = int(t_now) % 900
        seconds_to_wait = 900 - seconds_past_quarter + 5  # 5 секунд буфера на закрытие свечи биржи
        
        next_run_time = time.gmtime(t_now + seconds_to_wait + 10800)
        logger.info(f"💤 Ожидаю закрытия свечи. Следующий запуск в: {next_run_time.tm_hour:02d}:{next_run_time.tm_min:02d}:05")
        
        time.sleep(seconds_to_wait)
        
        # Автоматический выбор режима отчета
        next_run_struct = time.gmtime(time.time() + 10800)
        current_mode = "FULL" if (next_run_struct.tm_min == 0 and next_run_struct.tm_hour in [9, 15]) else "HUNT"
            
        try:
            market_scan(report_mode=current_mode)
        except Exception as e:
            logger.error(f"Критическая ошибка в главном цикле: {e}", exc_info=True)