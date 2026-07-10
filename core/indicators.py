import pandas as pd
import numpy as np
from typing import Tuple, Optional, Dict
from core.logger import logger

def calculate_ema(df: pd.DataFrame, period: int = 99) -> Optional[pd.Series]:
    """Рассчитывает экспоненциальную скользящую среднюю (EMA)."""
    if 'close' not in df.columns:
        logger.error("Колонка 'close' не найдена в DataFrame для расчета EMA.")
        return None
    try:
        close_prices = df['close'].astype(float)
        return close_prices.ewm(span=period, adjust=False).mean()
    except Exception as e:
        logger.warning(f"Ошибка расчета EMA: {e}")
        return None

def calculate_rsi(df: pd.DataFrame, period: int = 6) -> Optional[pd.Series]:
    """Рассчитывает стандартный индекс относительной силы (RSI)."""
    if 'close' not in df.columns:
        logger.error("Колонка 'close' не найдена в DataFrame для расчета RSI.")
        return None
    try:
        close_prices = df['close'].astype(float)
        delta = close_prices.diff()
        gain = delta.clip(lower=0)
        loss = -delta.clip(upper=0)
        
        ema_gain = gain.ewm(com=period - 1, adjust=False).mean()
        ema_loss = loss.ewm(com=period - 1, adjust=False).mean()
        
        ema_loss = ema_loss.replace(0, 1e-9)
        rs = ema_gain / ema_loss
        rsi = 100 - (100 / (1 + rs))
        return rsi
    except Exception as e:
        logger.warning(f"Ошибка расчета RSI: {e}")
        return None

def calculate_macd(df: pd.DataFrame, fast: int = 12, slow: int = 26, signal: int = 9) -> Tuple[Optional[pd.Series], Optional[pd.Series], Optional[pd.Series]]:
    """Рассчитывает схождение/расхождение скользящих средних (MACD)."""
    if 'close' not in df.columns:
        logger.error("Колонка 'close' не найдена в DataFrame для расчета MACD.")
        return None, None, None
    try:
        close_prices = df['close'].astype(float)
        exp1 = close_prices.ewm(span=fast, adjust=False).mean()
        exp2 = close_prices.ewm(span=slow, adjust=False).mean()
        macd_line = exp1 - exp2
        signal_line = macd_line.ewm(span=signal, adjust=False).mean()
        histogram = macd_line - signal_line
        return macd_line, signal_line, histogram
    except Exception as e:
        logger.warning(f"Ошибка расчета MACD: {e}")
        return None, None, None

def calculate_atr(df: pd.DataFrame, period: int = 14) -> Optional[pd.Series]:
    """Рассчитывает средний истинный диапазон (Average True Range, ATR)."""
    required_cols = ['high', 'low', 'close']
    if not all(col in df.columns for col in required_cols):
        logger.error(f"Необходимые колонки {required_cols} не найдены для расчета ATR.")
        return None
    try:
        df_atr = df.copy()
        for col in required_cols:
            df_atr[col] = df_atr[col].astype(float)

        high_low = df_atr['high'] - df_atr['low']
        high_close = (df_atr['high'] - df_atr['close'].shift()).abs()
        low_close = (df_atr['low'] - df_atr['close'].shift()).abs()

        tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
        atr = tr.ewm(com=period - 1, min_periods=period, adjust=False).mean()
        return atr
    except Exception as e:
        logger.warning(f"Ошибка расчета ATR: {e}")
        return None

def calculate_rvol(df: pd.DataFrame, period: int = 20) -> Optional[pd.Series]:
    """Рассчитывает относительный объем (Relative Volume, RVOL)."""
    if 'volume' not in df.columns:
        logger.error("Колонка 'volume' не найдена в DataFrame для расчета RVOL.")
        return None
    try:
        df_vol = df.copy()
        df_vol['volume'] = df_vol['volume'].astype(float)
        average_volume = df_vol['volume'].rolling(window=period, min_periods=period).mean()
        average_volume = average_volume.replace(0, 1e-9)
        rvol = df_vol['volume'] / average_volume
        return rvol
    except Exception as e:
        logger.warning(f"Ошибка расчета RVOL: {e}")
        return None

def calculate_adx(df: pd.DataFrame, period: int = 14) -> Optional[pd.DataFrame]:
    """Рассчитывает Average Directional Index (ADX), +DI, и -DI."""
    required_cols = ['high', 'low', 'close']
    if not all(col in df.columns for col in required_cols):
        logger.error(f"Необходимые колонки {required_cols} не найдены для расчета ADX.")
        return None
    
    if len(df) < period * 2:
        logger.warning(f"Недостаточно данных для корректного расчета ADX (нужно минимум {period*2} свечей).")
        return None
        
    try:
        df_adx = df.copy()
        for col in required_cols:
            df_adx[col] = df_adx[col].astype(float)

        high_low = df_adx['high'] - df_adx['low']
        high_close = (df_adx['high'] - df_adx['close'].shift()).abs()
        low_close = (df_adx['low'] - df_adx['close'].shift()).abs()
        tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
        
        move_up = df_adx['high'].diff()
        move_down = df_adx['low'].shift() - df_adx['low']
        
        p_dm = np.where((move_up > move_down) & (move_up > 0), move_up, 0)
        n_dm = np.where((move_down > move_up) & (move_down > 0), move_down, 0)

        tr_smoothed = tr.ewm(com=period - 1, min_periods=period, adjust=False).mean()
        p_dm_smoothed = pd.Series(p_dm, index=df.index).ewm(com=period - 1, min_periods=period, adjust=False).mean()
        n_dm_smoothed = pd.Series(n_dm, index=df.index).ewm(com=period - 1, min_periods=period, adjust=False).mean()

        tr_smoothed = tr_smoothed.replace(0, 1e-9)
        p_di = 100 * (p_dm_smoothed / tr_smoothed)
        n_di = 100 * (n_dm_smoothed / tr_smoothed)

        dx_num = (p_di - n_di).abs()
        dx_den = (p_di + n_di).replace(0, 1e-9)
        dx = 100 * (dx_num / dx_den)
        adx = dx.ewm(com=period - 1, min_periods=period, adjust=False).mean()

        result_df = pd.DataFrame({'adx': adx, 'p_di': p_di, 'n_di': n_di})
        result_df.index = df.index
        return result_df
    except Exception as e:
        logger.warning(f"Ошибка расчета ADX: {e}")
        return None

def evaluate_trend(df: pd.DataFrame) -> Optional[Dict]:
    """
    Анализирует последнюю свечу и определяет состояние тренда с учетом глубоких откатов (SMC).
    """
    required_cols = ['close', 'ema99', 'adx', 'p_di', 'n_di']
    if not all(col in df.columns for col in required_cols) or df.empty or any(df[col].isnull().all() for col in required_cols):
        logger.warning("Недостаточно данных для оценки тренда.")
        return None

    last_row = df.iloc[-1]
    try:
        price = float(last_row['close'])
        ema99 = float(last_row['ema99'])
        adx = float(last_row['adx'])
        p_di = float(last_row['p_di'])
        n_di = float(last_row['n_di'])
        
        # Базовое направление зависит ТОЛЬКО от фундаментальной EMA99
        is_bullish = price > ema99
        
        # Сила тренда зависит от совпадения ADX и DI с базовым направлением
        if is_bullish and adx > 25 and p_di > n_di:
            strength = 'strong'
        elif not is_bullish and adx > 25 and n_di > p_di:
            strength = 'strong'
        else:
            strength = 'flat' # Охватывает консолидации и глубокие контртрендовые откаты к POI
            
        return {
            'is_bullish': is_bullish,
            'strength': strength,
            'adx_value': round(adx, 2),
            'p_di': round(p_di, 2),
            'n_di': round(n_di, 2),
        }
    except (ValueError, TypeError) as e:
        logger.error(f"Ошибка конвертации данных при оценке тренда: {e}")
        return None
