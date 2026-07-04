import pandas as pd
from services.market_data import fetch_candles
from core.structure import find_swings, find_fvg, detect_structure_break, detect_sfp
from core.indicators import calculate_atr, calculate_rvol

def run_smc_tests():
    print("🚀 Запуск полигона SMC Engine...\n")
    
    # 1. Забираем реальные данные (те же, что смотрит бот)
    symbol = "BTC"
    timeframe = "15m"
    df = fetch_candles(symbol, timeframe, limit=100)
    
    if df is None or df.empty:
        print("❌ Ошибка: нет данных от биржи.")
        return
        
    print(f"📊 Данные загружены: {symbol} {timeframe}, {len(df)} свечей.")

    # Принудительная конвертация колонок в числовой формат для избежания TypeError
    numeric_cols = ['open', 'high', 'low', 'close', 'volume']
    for col in numeric_cols:
        if col in df.columns:
            df[col] = df[col].astype(float)

    # ⚡️ Рассчитываем ATR и RVOL для фильтрации
    df['atr'] = calculate_atr(df, period=14)
    df['rvol'] = calculate_rvol(df, period=20)
    # Удаляем строки с NaN, которые появляются после расчета индикаторов
    df.dropna(inplace=True)
    print(f"Индикаторы ATR и RVOL рассчитаны. Рабочих свечей: {len(df)}.")
    
    # Захватываем последнюю закрытую свечу (индекс -2, так как -1 это текущая формирующаяся)
    last_closed_candle = df.iloc[-2]
    
    # Если датафрейм использует datetime индекс, выводим его
    ts = last_closed_candle.name if 'timestamp' not in last_closed_candle else last_closed_candle['timestamp']
    print(f"Последняя закрытая свеча: {ts} | Close: {last_closed_candle['close']}\n")

    # 2. Тестируем Свинги
    print("🔍 ТЕСТ 1: Поиск Swing High / Swing Low")
    swing_highs, swing_lows = find_swings(df, left_bars=2, right_bars=2)
    
    if not swing_highs.empty:
        print("Последние 3 Swing Highs (Время | Цена):")
        # Выводим только колонку high для компактности
        print(swing_highs[['high']].tail(3).to_string(header=False))
    else:
        print("Swing Highs не найдены.")
        
    if not swing_lows.empty:
        print("\nПоследние 3 Swing Lows (Время | Цена):")
        print(swing_lows[['low']].tail(3).to_string(header=False))
    else:
        print("Swing Lows не найдены.")
    print("\n" + "-"*40 + "\n")

    # 3. Тестируем FVG
    print("🧲 ТЕСТ 2: Поиск FVG (Имбалансов) с фильтром по ATR > 0.5")
    fvgs = find_fvg(df, atr_series=df['atr'], min_size_atr_ratio=0.5)
    
    # Разделяем FVG по типам
    bullish_fvgs = [f for f in fvgs if f['type'] == 'bullish']
    bearish_fvgs = [f for f in fvgs if f['type'] == 'bearish']
    
    print("Последние 2 отфильтрованных Bullish FVG (Зоны поддержки):")
    if bullish_fvgs:
        for f in bullish_fvgs[-2:]:
            print(f"  -> Зона: {f['bottom']:.2f} - {f['top']:.2f} | Размер: {f['top'] - f['bottom']:.2f}")
    else:
        print("  -> Не найдено.")
        
    print("\nПоследние 2 отфильтрованных Bearish FVG (Зоны сопротивления):")
    if bearish_fvgs:
        for f in bearish_fvgs[-2:]:
            print(f"  -> Зона: {f['bottom']:.2f} - {f['top']:.2f} | Размер: {f['top'] - f['bottom']:.2f}")
    else:
         print("  -> Не найдено.")
    print("\n" + "-"*40 + "\n")

    # 4. Тестируем SFP и Слом структуры (CHoCH/BOS) на последней закрытой свече
    print("⚡️ ТЕСТ 3: SFP и Слом структуры (BOS/CHoCH)")
    
    sfp_result = detect_sfp(last_closed_candle, swing_highs, swing_lows)
    break_result = detect_structure_break(last_closed_candle, swing_highs, swing_lows)
    
    if sfp_result:
        print(f"⚠️ SFP обнаружен: {sfp_result['type']} за уровнем {sfp_result['level']}")
    else:
        print("SFP: НЕТ")
        
    if break_result:
        print(f"🚨 Слом структуры: {break_result['type']} пробой уровня {break_result['level']}")
    else:
        print("Слом структуры: НЕТ")
    print("\n" + "="*40 + "\n")

if __name__ == "__main__":
    run_smc_tests()