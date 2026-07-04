import pandas as pd
from services.market_data import fetch_candles
from core.indicators import (
    calculate_ema, 
    calculate_atr, 
    calculate_rvol, 
    calculate_adx, 
    evaluate_trend
)
from core.structure import (
    find_swings, 
    find_fvg, 
    detect_sfp, 
    detect_structure_break
)
from core.risk import calculate_setup_score
from core.logger import logger

def run_score_engine_test():
    """
    Комплексный тест, который прогоняет данные через весь конвейер:
    Indicators -> Structure -> Score Engine.
    """
    logger.info("🚀 Запуск полигона Score Engine...")
    
    # 1. Загрузка и подготовка данных
    symbol = "BTC"
    timeframe = "15m"
    df = fetch_candles(symbol, timeframe, limit=100)
    
    if df is None or df.empty:
        logger.error("❌ Ошибка: нет данных от биржи.")
        return
        
    logger.info(f"📊 Данные загружены: {symbol} {timeframe}, {len(df)} свечей.")

    # Принудительная конвертация колонок в числовой формат
    numeric_cols = ['open', 'high', 'low', 'close', 'volume']
    for col in numeric_cols:
        if col in df.columns:
            df[col] = df[col].astype(float)

    # 2. Расчет всех необходимых индикаторов
    df['ema99'] = calculate_ema(df, 99)
    df['atr'] = calculate_atr(df, 14)
    df['rvol'] = calculate_rvol(df, 20)
    adx_df = calculate_adx(df, 14)
    if adx_df is not None:
        df = df.join(adx_df)
    
    df.dropna(inplace=True) # Удаляем строки с NaN после прогрева индикаторов
    logger.info(f"⚙️ Индикаторы рассчитаны. Рабочих свечей: {len(df)}.")
    
    # 3. Сбор данных для Score Engine
    last_closed_candle = df.iloc[-2]

    # 3.1. Данные по тренду
    trend_data = evaluate_trend(df.iloc[:-1]) # Оцениваем по предпоследней свече

    # 3.2. Данные по структуре
    swing_highs, swing_lows = find_swings(df, left_bars=2, right_bars=2)
    structure_data = detect_structure_break(last_closed_candle, swing_highs, swing_lows)
    sfp_data = detect_sfp(last_closed_candle, swing_highs, swing_lows)
    fvg_data = find_fvg(df, atr_series=df['atr'], min_size_atr_ratio=0.5)

    # 3.3. Данные по объему
    volume_data = {'rvol': last_closed_candle['rvol']}

    # 3.4. Mock макро-данных
    macro_data = {'confirms': True}

    logger.info("🧩 Все компоненты для анализа собраны. Запускаю Score Engine...")
    print("\n" + "="*50)

    # 4. Расчет скоринга для LONG
    long_score_result = calculate_setup_score(
        trade_direction='long',
        trend_data=trend_data,
        structure_data=structure_data,
        sfp_data=sfp_data,
        fvg_data=fvg_data,
        volume_data=volume_data,
        macro_data=macro_data
    )

    print(f"🟢 ОЦЕНКА LONG СЕТАПА: {long_score_result['total_score']} баллов | РЕШЕНИЕ: {long_score_result['decision']}")
    print("─" * 20)
    for key, value in long_score_result['breakdown'].items():
        print(f"  • {key.capitalize()}: {value}")
    
    print("\n" + "="*50)

    # 5. Расчет скоринга для SHORT
    short_score_result = calculate_setup_score(
        trade_direction='short',
        trend_data=trend_data,
        structure_data=structure_data,
        sfp_data=sfp_data,
        fvg_data=fvg_data,
        volume_data=volume_data,
        macro_data=macro_data
    )
    
    print(f"🔴 ОЦЕНКА SHORT СЕТАПА: {short_score_result['total_score']} баллов | РЕШЕНИЕ: {short_score_result['decision']}")
    print("─" * 20)
    for key, value in short_score_result['breakdown'].items():
        print(f"  • {key.capitalize()}: {value}")

    print("\n" + "="*50)
    logger.info("✅ Полигон Score Engine завершил работу.")


if __name__ == "__main__":
    # Для запуска из корневой директории: python -m tests.test_score
    run_score_engine_test()