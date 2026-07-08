import pandas as pd
import numpy as np
from typing import List, Tuple, Dict, Optional
from core.logger import logger

def find_swings(df: pd.DataFrame, left_bars: int = 2, right_bars: int = 2) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """
    Находит фрактальные свинги (максимумы и минимумы) на графике.
    Это векторизованная реализация, которая избегает медленных циклов.

    - Swing High: свеча, у которой High выше, чем у N свечей слева и M свечей справа.
    - Swing Low: свеча, у которой Low ниже, чем у N свечей слева и M свечей справа.

    Args:
        df (pd.DataFrame): DataFrame с OHLC данными.
        left_bars (int): Количество баров слева для сравнения.
        right_bars (int): Количество баров справа для сравнения.

    Returns:
        Tuple[pd.DataFrame, pd.DataFrame]: Два DataFrame, содержащие строки,
        соответствующие Swing Highs и Swing Lows.
    """
    # Условия для Swing High
    high_conditions = []
    for i in range(1, left_bars + 1):
        high_conditions.append(df['high'] > df['high'].shift(i))
    for i in range(1, right_bars + 1):
        high_conditions.append(df['high'] > df['high'].shift(-i))
    
    # np.all собирает все булевы условия в один массив
    is_swing_high = np.all(high_conditions, axis=0)
    swing_highs = df[is_swing_high]

    # Условия для Swing Low
    low_conditions = []
    for i in range(1, left_bars + 1):
        low_conditions.append(df['low'] < df['low'].shift(i))
    for i in range(1, right_bars + 1):
        low_conditions.append(df['low'] < df['low'].shift(-i))
        
    is_swing_low = np.all(low_conditions, axis=0)
    swing_lows = df[is_swing_low]
    
    return swing_highs, swing_lows

def find_fvg(
    df: pd.DataFrame, 
    min_size_atr_ratio: float = 0.5, 
    volume_filter: bool = False,
    atr_series: Optional[pd.Series] = None,
    rvol_series: Optional[pd.Series] = None
) -> List[Dict]:
    """
    Находит разрывы справедливой стоимости (FVG) с фильтрацией по размеру и объему.
    
    - Bullish FVG: Low свечи [i] > High свечи [i-2].
    - Bearish FVG: High свечи [i] < Low свечи [i-2].

    Args:
        df (pd.DataFrame): DataFrame с OHLC данными.
        min_size_atr_ratio (float): Минимальный размер FVG в долях ATR для фильтрации шума.
        volume_filter (bool): Если True, требует, чтобы импульсная свеча имела RVOL > 1.5.
        atr_series (Optional[pd.Series]): Пре-рассчитанная серия ATR.
        rvol_series (Optional[pd.Series]): Пре-рассчитанная серия RVOL.

    Returns:
        List[Dict]: Список словарей, где каждый словарь описывает валидный FVG.
    """
    fvgs = []
    
    is_bullish_fvg = (df['low'] > df['high'].shift(2))
    is_bearish_fvg = (df['high'] < df['low'].shift(2))
    
    bullish_indices = df.index[is_bullish_fvg]
    bearish_indices = df.index[is_bearish_fvg]
    
    for idx in bullish_indices:
        pos = df.index.get_loc(idx)
        if pos < 2: continue
        
        prev_1_idx = df.index[pos - 1]
        prev_2_idx = df.index[pos - 2]
        
        top = float(df.loc[idx, 'low'])
        bottom = float(df.loc[prev_2_idx, 'high'])
        fvg_size = top - bottom

        if atr_series is not None and fvg_size < (atr_series.loc[idx] * min_size_atr_ratio):
            continue

        if volume_filter and rvol_series is not None and rvol_series.loc[prev_1_idx] <= 1.5:
            continue
        
        fvgs.append({
            'type': 'bullish',
            'top': top,
            'bottom': bottom,
            'start_index': prev_2_idx,
            'end_index': idx,
        })
        
    for idx in bearish_indices:
        pos = df.index.get_loc(idx)
        if pos < 2: continue
        
        prev_1_idx = df.index[pos - 1]
        prev_2_idx = df.index[pos - 2]

        top = float(df.loc[prev_2_idx, 'low'])
        bottom = float(df.loc[idx, 'high'])
        fvg_size = top - bottom

        if atr_series is not None and fvg_size < (atr_series.loc[idx] * min_size_atr_ratio):
            continue

        if volume_filter and rvol_series is not None and rvol_series.loc[prev_1_idx] <= 1.5:
            continue

        fvgs.append({
            'type': 'bearish',
            'top': top,
            'bottom': bottom,
            'start_index': prev_2_idx,
            'end_index': idx,
        })
        
    return fvgs

def detect_structure_break(last_closed_candle: pd.Series, swing_highs: pd.DataFrame, swing_lows: pd.DataFrame) -> Optional[Dict]:
    """
    Определяет слом структуры (BOS/CHoCH) на последней закрытой свече.
    Проверяет, закрылось ли тело последней свечи за последним свингом.

    Args:
        last_closed_candle (pd.Series): Строка DataFrame, соответствующая последней закрытой свече.
        swing_highs (pd.DataFrame): DataFrame с найденными Swing Highs.
        swing_lows (pd.DataFrame): DataFrame с найденными Swing Lows.

    Returns:
        Optional[Dict]: Словарь с информацией о сломе или None.
    """
    if swing_highs.empty or swing_lows.empty: return None

    relevant_highs = swing_highs[swing_highs.index < last_closed_candle.name]
    relevant_lows = swing_lows[swing_lows.index < last_closed_candle.name]

    if relevant_highs.empty or relevant_lows.empty: return None

    last_swing_high = relevant_highs.iloc[-1]
    last_swing_low = relevant_lows.iloc[-1]

    level_high = float(last_swing_high['high'])
    level_low = float(last_swing_low['low'])
    close_price = float(last_closed_candle['close'])
    rvol = last_closed_candle.get('rvol', 0)

    if close_price > level_high:
        return {'type': 'bullish_break', 'level': level_high, 'rvol': rvol}

    if close_price < level_low:
        return {'type': 'bearish_break', 'level': level_low, 'rvol': rvol}
        
    return None

def detect_sfp(last_closed_candle: pd.Series, swing_highs: pd.DataFrame, swing_lows: pd.DataFrame) -> Optional[Dict]:
    """
    Определяет паттерн "Захват ликвидности" (SFP) на последней закрытой свече.
    Проверяет, пробила ли цена уровень свинга тенью, но закрылась обратно.

    Args:
        last_closed_candle (pd.Series): Строка DataFrame, соответствующая последней закрытой свече.
        swing_highs (pd.DataFrame): DataFrame с найденными Swing Highs.
        swing_lows (pd.DataFrame): DataFrame с найденными Swing Lows.

    Returns:
        Optional[Dict]: Словарь с информацией о SFP или None.
    """
    if swing_highs.empty or swing_lows.empty: return None

    relevant_highs = swing_highs[swing_highs.index < last_closed_candle.name]
    relevant_lows = swing_lows[swing_lows.index < last_closed_candle.name]

    if relevant_highs.empty or relevant_lows.empty: return None

    last_swing_high = relevant_highs.iloc[-1]
    last_swing_low = relevant_lows.iloc[-1]

    level_high = float(last_swing_high['high'])
    level_low = float(last_swing_low['low'])
    candle_high = float(last_closed_candle['high'])
    candle_low = float(last_closed_candle['low'])
    candle_close = float(last_closed_candle['close'])

    if candle_high > level_high and candle_close < level_high:
        return {'type': 'bearish_sfp', 'level': level_high}

    if candle_low < level_low and candle_close > level_low:
        return {'type': 'bullish_sfp', 'level': level_low}
        
    return None