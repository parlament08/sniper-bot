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

    if df_4h is None or len(df_4h) < 50:
        logger.warning(f"Недостаточно данных для {coin}")
        return None

    # Приведение типов
    numeric_cols = ['open', 'high', 'low', 'close', 'volume']
    for col in numeric_cols:
        df_4h[col] = df_4h[col].astype(float)

    # 2. Расчет индикаторов
    df_4h['ema99'] = calculate_ema(df_4h, 99)
    df_4h['atr'] = calculate_atr(df_4h, 14)
    
    df_4h.dropna(inplace=True)

    if df_4h.empty:
        logger.warning(f"Недостаточно данных для {coin} после расчета индикаторов.")
        return None

    # 3. Поиск структурных элементов (только по закрытым свечам)
    df_4h_closed = df_4h.iloc[:-1].copy()
    
    # 🔴 ИСПРАВЛЕНИЕ: Берем реальную ТЕКУЩУЮ (живую) цену для алертов
    current_live_price = float(df_4h.iloc[-1]['close'])
    
    last_closed_candle = df_4h_closed.iloc[-1]
    # last_close оставляем только для определения тренда
    last_close = float(last_closed_candle['close']) 
    
    # Определяем тренд по 4H EMA
    last_ema99_4h = float(last_closed_candle['ema99'])
    
    swing_highs, swing_lows = find_swings(df_4h_closed, left_bars=5, right_bars=5)
    all_fvgs = find_fvg(df_4h_closed, atr_series=df_4h_closed['atr'], min_size_atr_ratio=0.5)

    # 4. Поиск ближайших зон интереса (POI) относительно ЖИВОЙ ЦЕНЫ
    long_poi_zone, long_poi_reason = None, ""
    # Ищем бычьи FVG, у которых дно ниже текущей цены (чтобы не пропустить тот, в котором мы находимся)
    bullish_fvgs_below = [f for f in all_fvgs if f['type'] == 'bullish' and f['bottom'] < current_live_price]
    if bullish_fvgs_below:
        closest_fvg = max(bullish_fvgs_below, key=lambda x: x['top'])
        long_poi_zone = (closest_fvg['bottom'], closest_fvg['top'])
        long_poi_reason = "FVG"
    elif not swing_lows.empty:
        relevant_lows = swing_lows[swing_lows['low'] < current_live_price]
        if not relevant_lows.empty:
            closest_low = relevant_lows.iloc[-1]
            long_poi_zone = (closest_low['low'], closest_low['low'])
            long_poi_reason = "Liquidity Pool"

    short_poi_zone, short_poi_reason = None, ""
    # Ищем медвежьи FVG, у которых верхушка выше текущей цены (чтобы не пропустить тот, в котором мы находимся)
    bearish_fvgs_above = [f for f in all_fvgs if f['type'] == 'bearish' and f['top'] > current_live_price]
    if bearish_fvgs_above:
        closest_fvg = min(bearish_fvgs_above, key=lambda x: x['bottom'])
        short_poi_zone = (closest_fvg['bottom'], closest_fvg['top'])
        short_poi_reason = "FVG"
    elif not swing_highs.empty:
        relevant_highs = swing_highs[swing_highs['high'] > current_live_price]
        if not relevant_highs.empty:
            closest_high = relevant_highs.iloc[-1]
            short_poi_zone = (closest_high['high'], closest_high['high'])
            short_poi_reason = "Liquidity Pool"
            
    # 5. Форматирование отчета
    is_bullish = last_close > last_ema99_4h
    trend_emoji = "🟢" if is_bullish else "🔴"

    # --- Long POI and Alert ---
    alert_down = "Н/Д"
    if long_poi_zone:
        long_bottom, long_top = long_poi_zone
        long_zone_line = f"• 📈 <b>Зона Long (Discount):</b> {long_bottom:.2f} - {long_top:.2f} ({long_poi_reason})"
        # 🔴 ИСПРАВЛЕНИЕ: Проверяем нахождение ЖИВОЙ цены внутри зоны
        if long_bottom <= current_live_price <= long_top:
            alert_down = "Цена уже внутри зоны Long!"
        else:
            alert_price = long_top * 1.005 # Отступ +0.5%
            alert_down = f"{alert_price:.2f}"
    else:
        long_zone_line = "• 📈 <b>Зона Long (Discount):</b> Не найдена"

    # --- Short POI and Alert ---
    alert_up = "Н/Д"
    if short_poi_zone:
        short_bottom, short_top = short_poi_zone
        short_zone_line = f"• 📉 <b>Зона Short (Premium):</b> {short_bottom:.2f} - {short_top:.2f} ({short_poi_reason})"
        # 🔴 ИСПРАВЛЕНИЕ: Проверяем нахождение ЖИВОЙ цены внутри зоны
        if short_bottom <= current_live_price <= short_top:
            alert_up = "Алерт не требуется, цена тестирует Short FVG!"
        else:
            alert_price = short_bottom * 0.995 # Отступ -0.5%
            alert_up = f"{alert_price:.2f}"
    else:
        short_zone_line = "• 📉 <b>Зона Short (Premium):</b> Не найдена"

    # --- Final Template V.5.0 ---
    return f"""📡 <b>УТРЕННЯЯ РАЗВЕДКА [{coin}/USDT] (4H)</b>
    • <b>Market Structure:</b> {trend_emoji} HTF Bias. Цена находится {'ВЫШЕ' if is_bullish else 'НИЖЕ'} SMMA(99).
    • <b>Межрыночный фон:</b> [ОЖИДАНИЕ ИНТЕГРАЦИИ MACRO: Требуется подвязка DXY и S&P500]

    🎯 <b>ЗОНЫ ИНТЕРЕСА (POI) & АЛЕРТЫ</b>
    {long_zone_line}
    {short_zone_line}
    • 🔔 <b>Алерты для Binance:</b>
    🔽 {alert_down}
    🔼 {alert_up}

    🔥 <b>ГОРЯЧИЕ ПРАВИЛА:</b>
    • При срабатывании алерта переход на 15m. Нет подтверждения (CHoCH) на 15m? Сетап не сформирован. Ждем.
    """