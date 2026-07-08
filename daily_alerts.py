import os
from dotenv import load_dotenv
import pandas as pd
from services.market_data import fetch_candles
from core.logger import logger
from core.indicators import calculate_ema, calculate_atr
from core.structure import find_swings, find_fvg
from services.macro_context import get_macro_context

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
    
    swing_highs, swing_lows = find_swings(df_4h_closed, left_bars=20, right_bars=20)
    all_fvgs = find_fvg(df_4h_closed, atr_series=df_4h_closed['atr'], min_size_atr_ratio=0.5)

    # 4. Поиск ближайших зон интереса (POI) относительно ЖИВОЙ ЦЕНЫ с жесткой изоляцией
    long_poi_zone, long_poi_reason = None, ""
    # ЖЕСТКОЕ ПРАВИЛО: Вся зона Long (даже ее верхняя граница) должна быть НИЖЕ текущей цены
    bullish_fvgs_below = [
        f for f in all_fvgs
        if f['type'] == 'bullish' and not f.get('invalidated', False) and f['top'] < current_live_price
    ]
    
    # Приоритет: сначала ищем нетронутую макро-ликвидность (Unmitigated SSL)
    if not swing_lows.empty:
        relevant_lows = swing_lows[swing_lows['low'] < current_live_price]
        
        # Фильтрация нетронутых (unmitigated) минимумов
        unmitigated_lows = []
        for idx, row in relevant_lows.iterrows():
            future_price_action = df_4h_closed.loc[idx:].iloc[1:]
            if future_price_action.empty or future_price_action['low'].min() >= row['low']:
                unmitigated_lows.append(row)

        if unmitigated_lows:
            closest_low_series = unmitigated_lows[-1] # Берем ближайший нетронутый
            long_poi_zone = (closest_low_series['low'], closest_low_series['low'])
            long_poi_reason = "Unmitigated SSL"
    # Если структурного дна рядом нет, берем FVG
    elif bullish_fvgs_below:
        closest_fvg = max(bullish_fvgs_below, key=lambda x: x['top'])
        long_poi_zone = (closest_fvg['bottom'], closest_fvg['top'])
        long_poi_reason = "FVG"

    short_poi_zone, short_poi_reason = None, ""
    # ЖЕСТКОЕ ПРАВИЛО: Вся зона Short (даже ее нижняя граница) должна быть ВЫШЕ текущей цены
    bearish_fvgs_above = [
        f for f in all_fvgs
        if f['type'] == 'bearish' and not f.get('invalidated', False) and f['bottom'] > current_live_price
    ]
    
    # Приоритет: сначала ищем нетронутую макро-ликвидность (Unmitigated BSL)
    if not swing_highs.empty:
        relevant_highs = swing_highs[swing_highs['high'] > current_live_price]

        # Фильтрация нетронутых (unmitigated) максимумов
        unmitigated_highs = []
        for idx, row in relevant_highs.iterrows():
            future_price_action = df_4h_closed.loc[idx:].iloc[1:]
            if future_price_action.empty or future_price_action['high'].max() <= row['high']:
                unmitigated_highs.append(row)

        if unmitigated_highs:
            closest_high_series = unmitigated_highs[-1] # Берем ближайший нетронутый
            short_poi_zone = (closest_high_series['high'], closest_high_series['high'])
            short_poi_reason = "Unmitigated BSL"
    # Если структурной вершины рядом нет, берем FVG
    elif bearish_fvgs_above:
        closest_fvg = min(bearish_fvgs_above, key=lambda x: x['bottom'])
        short_poi_zone = (closest_fvg['bottom'], closest_fvg['top'])
        short_poi_reason = "FVG"
            
    # 5. Форматирование отчета и умные Алерты
    is_bullish = last_close > last_ema99_4h
    trend_emoji = "🟢" if is_bullish else "🔴"

    # --- Long POI and Alert ---
    alert_down = "Н/Д"
    if long_poi_zone:
        long_bottom, long_top = long_poi_zone
        
        # ЭСТЕТИЧЕСКИЙ ФИКС: Уровень vs Диапазон
        if long_bottom == long_top:
            long_zone_line = f"• 📈 <b>Зона Long (Discount):</b> Уровень {long_bottom:.4f} ({long_poi_reason})"
        else:
            long_zone_line = f"• 📈 <b>Зона Long (Discount):</b> Диапазон {long_bottom:.4f} - {long_top:.4f} ({long_poi_reason})"
        
        ideal_alert = long_top * 1.005 # Отступ +0.5%
        
        if ideal_alert >= current_live_price:
            proposed_alert = (current_live_price + long_top) / 2
        else:
            proposed_alert = ideal_alert
            
        # PROXIMITY FILTER: Проверка дистанции (0.1%)
        distance_pct = abs(current_live_price - proposed_alert) / current_live_price
        if distance_pct <= 0.001 or current_live_price <= long_top:
            alert_down = "Цена уже в зоне алерта — переходи на 15m!"
        else:
            alert_down = f"{proposed_alert:.4f}"
    else:
        long_zone_line = "• 📈 <b>Зона Long (Discount):</b> Не найдена"

    # --- Short POI and Alert ---
    alert_up = "Н/Д"
    if short_poi_zone:
        short_bottom, short_top = short_poi_zone
        
        # ЭСТЕТИЧЕСКИЙ ФИКС: Уровень vs Диапазон
        if short_bottom == short_top:
            short_zone_line = f"• 📉 <b>Зона Short (Premium):</b> Уровень {short_bottom:.4f} ({short_poi_reason})"
        else:
            short_zone_line = f"• 📉 <b>Зона Short (Premium):</b> Диапазон {short_bottom:.4f} - {short_top:.4f} ({short_poi_reason})"
        
        ideal_alert = short_bottom * 0.995 # Отступ -0.5%
        
        if ideal_alert <= current_live_price:
            proposed_alert = (current_live_price + short_bottom) / 2
        else:
            proposed_alert = ideal_alert
            
        # PROXIMITY FILTER: Проверка дистанции (0.1%)
        distance_pct = abs(current_live_price - proposed_alert) / current_live_price
        if distance_pct <= 0.001 or current_live_price >= short_bottom:
            alert_up = "Цена уже в зоне алерта — переходи на 15m!"
        else:
            alert_up = f"{proposed_alert:.4f}"
    else:
        short_zone_line = "• 📉 <b>Зона Short (Premium):</b> Не найдена"

    # Получаем макро-данные
    macro = get_macro_context()
    dxy_trend = macro.get('DXY', {}).get('trend', 'Н/Д')
    spx_trend = macro.get('SPX', {}).get('trend', 'Н/Д')
    btc_d = macro.get('BTC.D', {}).get('price', 'Н/Д')
    
    macro_string = f"DXY: {dxy_trend} | SPX: {spx_trend} | BTC.D: {btc_d}%"

    # --- Final Template V.5.0 ---
    return f"""📡 <b>УТРЕННЯЯ РАЗВЕДКА [{coin}/USDT] (4H)</b>
• <b>Market Structure:</b> {trend_emoji} HTF Bias. Текущая цена ({current_live_price:.4f}) находится {'ВЫШЕ' if is_bullish else 'НИЖЕ'} EMA(99).
• <b>Межрыночный фон:</b> {macro_string}

🎯 <b>ЗОНЫ ИНТЕРЕСА (POI) & АЛЕРТЫ</b>
{long_zone_line}
{short_zone_line}
• 🔔 <b>Алерты для Binance:</b>
🔽 {alert_down}
🔼 {alert_up}

🔥 <b>ГОРЯЧИЕ ПРАВИЛА:</b>
• При срабатывании алерта переход на 15m. Нет подтверждения (CHoCH) на 15m? Сетап не сформирован. Ждем.
"""
