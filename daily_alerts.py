import os
from dotenv import load_dotenv
import pandas as pd
from services.market_data import fetch_candles
from core.logger import logger
from core.indicators import calculate_ema, calculate_atr
from core.structure import find_swings, find_fvg

# Загружаем переменные из .env файла в корне проекта
load_dotenv()

def generate_coin_alert(coin):
    logger.info(f"Сканирую уровни SMC для {coin}...")
    
    # 1. Загрузка и подготовка данных
    df_4h = fetch_candles(coin, '4h', limit=200)
    df_15m = fetch_candles(coin, '15m', limit=150)

    if df_4h is None or df_15m is None or len(df_4h) < 50 or len(df_15m) < 50:
        logger.warning(f"Недостаточно данных для {coin}")
        return None

    # Приведение типов
    numeric_cols = ['open', 'high', 'low', 'close', 'volume']
    for col in numeric_cols:
        df_4h[col] = df_4h[col].astype(float)
        df_15m[col] = df_15m[col].astype(float)

    # 2. Расчет индикаторов
    df_4h['ema99'] = calculate_ema(df_4h, 99)
    df_15m['atr'] = calculate_atr(df_15m, 14)
    
    df_4h.dropna(inplace=True)
    df_15m.dropna(inplace=True)

    if df_4h.empty or df_15m.empty:
        logger.warning(f"Недостаточно данных для {coin} после расчета индикаторов.")
        return None

    # 3. Поиск структурных элементов (только по закрытым свечам)
    df_15m_closed = df_15m.iloc[:-1].copy()
    last_closed_candle = df_15m_closed.iloc[-1]
    last_close = float(last_closed_candle['close'])
    
    # Определяем тренд по 4H EMA
    last_ema99_4h = float(df_4h.iloc[-1]['ema99'])
    
    swing_highs, swing_lows = find_swings(df_15m_closed, left_bars=5, right_bars=5)
    all_fvgs = find_fvg(df_15m_closed, atr_series=df_15m_closed['atr'], min_size_atr_ratio=0.5)

    # 4. Поиск ближайших зон интереса (POI)
    long_poi_zone, long_poi_reason = None, ""
    bullish_fvgs_below = [f for f in all_fvgs if f['type'] == 'bullish' and f['top'] < last_close]
    if bullish_fvgs_below:
        closest_fvg = max(bullish_fvgs_below, key=lambda x: x['top'])
        long_poi_zone = (closest_fvg['bottom'], closest_fvg['top'])
        long_poi_reason = "FVG"
    elif not swing_lows.empty:
        relevant_lows = swing_lows[swing_lows['low'] < last_close]
        if not relevant_lows.empty:
            closest_low = relevant_lows.iloc[-1]
            long_poi_zone = (closest_low['low'], closest_low['low'])
            long_poi_reason = "Liquidity Pool"

    short_poi_zone, short_poi_reason = None, ""
    bearish_fvgs_above = [f for f in all_fvgs if f['type'] == 'bearish' and f['bottom'] > last_close]
    if bearish_fvgs_above:
        closest_fvg = min(bearish_fvgs_above, key=lambda x: x['bottom'])
        short_poi_zone = (closest_fvg['bottom'], closest_fvg['top'])
        short_poi_reason = "FVG"
    elif not swing_highs.empty:
        relevant_highs = swing_highs[swing_highs['high'] > last_close]
        if not relevant_highs.empty:
            closest_high = relevant_highs.iloc[-1]
            short_poi_zone = (closest_high['high'], closest_high['high'])
            short_poi_reason = "Liquidity Pool"
            
    # 5. Форматирование отчета
    trend_emoji = "🟢 Бычий" if last_close > last_ema99_4h else "🔴 Медвежий"
    header_line = f"💎 <b>{coin}</b> | Тренд: {trend_emoji}"

    alert_down, alert_up = None, None

    # Формируем строку для Long зоны
    if long_poi_zone:
        zone_str = f"{long_poi_zone[0]:.4f}" if long_poi_zone[0] == long_poi_zone[1] else f"{long_poi_zone[0]:.4f} - {long_poi_zone[1]:.4f}"
        long_zone_line = f"📉 <b>Зона Long:</b> {zone_str} ({long_poi_reason})"
        alert_down = long_poi_zone[1] * 1.003
    else:
        long_zone_line = "📉 <b>Зона Long:</b> Не найдена"

    # Формируем строку для Short зоны
    if short_poi_zone:
        zone_str = f"{short_poi_zone[0]:.4f}" if short_poi_zone[0] == short_poi_zone[1] else f"{short_poi_zone[0]:.4f} - {short_poi_zone[1]:.4f}"
        short_zone_line = f"📈 <b>Зона Short:</b> {zone_str} ({short_poi_reason})"
        alert_up = short_poi_zone[0] * 0.997
    else:
        short_zone_line = "📈 <b>Зона Short:</b> Не найдена"

    # Формируем строку для алертов
    alert_down_str = f"<code>{alert_down:.4f}</code>" if alert_down else "Н/Д"
    alert_up_str = f"<code>{alert_up:.4f}</code>" if alert_up else "Н/Д"
    alerts_line = f"🔔 <b>Алерты:</b> 🔽 {alert_down_str} | 🔼 {alert_up_str}"

    # Собираем все строки в одно сообщение
    return "\n".join([header_line, long_zone_line, short_zone_line, alerts_line])