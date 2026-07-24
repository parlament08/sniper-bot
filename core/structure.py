import pandas as pd
import numpy as np
from dataclasses import dataclass, asdict
from typing import List, Tuple, Dict, Optional, Any, Union
from core.displacement import evaluate_displacement
from core.logger import logger


@dataclass(frozen=True)
class BOSConfig:
    min_quality_score: int = 70
    min_body_ratio: float = 0.55
    excellent_body_ratio: float = 0.85
    min_close_position: float = 0.65
    excellent_close_position: float = 0.9
    min_displacement_atr: float = 0.8
    excellent_displacement_atr: float = 1.8
    close_buffer_atr: float = 0.1
    excellent_close_buffer_atr: float = 0.4
    min_rvol: float = 1.5
    max_opposite_wick_ratio: float = 0.35
    hold_confirmation_bars: int = 0
    hold_buffer_atr: float = 0.0
    body_score_weight: int = 15
    displacement_score_weight: int = 25
    close_score_weight: int = 15
    close_position_score_weight: int = 10
    volume_score_weight: int = 15
    wick_score_weight: int = 10
    hold_score_weight: int = 10


@dataclass(frozen=True)
class CHoCHConfig:
    min_quality_score: int = 70
    min_confidence: int = 75
    sequence_score_weight: int = 35
    impulse_score_weight: int = 65


@dataclass(frozen=True)
class SFPConfig:
    min_quality_score: int = 65
    min_liquidity_depth_atr: float = 0.08
    excellent_liquidity_depth_atr: float = 0.45
    min_liquidity_level_strength: float = 35.0
    candle_quality_weight: float = 0.65
    level_quality_weight: float = 0.35
    min_rejection_atr: float = 0.15
    excellent_rejection_atr: float = 0.7
    min_displacement_atr: float = 0.2
    excellent_displacement_atr: float = 1.0
    min_rvol: float = 1.5
    max_opposite_wick_ratio: float = 0.35
    hold_confirmation_bars: int = 0
    hold_buffer_atr: float = 0.0
    depth_score_weight: int = 20
    rejection_score_weight: int = 25
    close_position_score_weight: int = 15
    opposite_wick_score_weight: int = 10
    displacement_score_weight: int = 15
    volume_score_weight: int = 10
    hold_score_weight: int = 5


@dataclass(frozen=True)
class FVGConfig:
    min_quality_score: int = 20
    excellent_size_atr: float = 1.2
    excellent_displacement_atr: float = 1.6
    min_rvol: float = 1.5
    ideal_min_age_bars: int = 2
    max_fresh_age_bars: int = 60
    invalid_quality_score: int = 0
    size_score_weight: int = 25
    displacement_score_weight: int = 25
    volume_score_weight: int = 15
    age_score_weight: int = 10
    overlap_score_weight: int = 15
    retest_score_weight: int = 10
    wick_violation_penalty: int = 12


@dataclass(frozen=True)
class MarketStructureConfig:
    adx_neutral_threshold: float = 18.0
    min_range_atr_ratio: float = 2.0
    range_lookback_bars: int = 20
    swing_lookback: int = 4
    conflicting_swing_confidence: int = 23
    compressed_swing_confidence: int = 28
    neutral_confidence_cap: int = 35
    directional_confidence_floor: int = 55
    trend_alignment_bonus: int = 10


@dataclass(frozen=True)
class BOSResult:
    detected: bool
    quality_score: int
    displacement_ratio: float
    body_ratio: float
    volume_confirmed: bool
    close_confirmed: bool
    type: Optional[str] = None
    level: Optional[float] = None
    rvol: float = 0.0
    index: Any = None
    body_size: float = 0.0
    candle_range: float = 0.0
    opposite_wick_ratio: float = 0.0
    hold_confirmed: bool = True
    close_position: float = 0.0
    absorption_warning: bool = False
    absorption_score: float = 0.0
    open: Optional[float] = None
    high: Optional[float] = None
    low: Optional[float] = None
    close: Optional[float] = None

    def __bool__(self) -> bool:
        return self.detected

    def get(self, key: str, default: Any = None) -> Any:
        return asdict(self).get(key, default)

    def __getitem__(self, key: str) -> Any:
        data = asdict(self)
        if key not in data:
            raise KeyError(key)
        return data[key]

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class CHoCHResult:
    confirmed: bool
    quality_score: int
    confidence: int
    swing_sequence_valid: bool
    type: Optional[str] = None
    level: Optional[float] = None
    rvol: float = 0.0
    index: Any = None
    displacement_ratio: float = 0.0
    body_ratio: float = 0.0
    volume_confirmed: bool = False
    close_confirmed: bool = False
    body_size: float = 0.0
    candle_range: float = 0.0
    opposite_wick_ratio: float = 0.0
    hold_confirmed: bool = True
    swing_sequence: Tuple[str, ...] = ()
    absorption_warning: bool = False
    absorption_score: float = 0.0
    open: Optional[float] = None
    high: Optional[float] = None
    low: Optional[float] = None
    close: Optional[float] = None

    @property
    def detected(self) -> bool:
        return self.confirmed

    def __bool__(self) -> bool:
        return self.confirmed

    def get(self, key: str, default: Any = None) -> Any:
        data = asdict(self)
        if key == 'detected':
            return self.detected
        return data.get(key, default)

    def __getitem__(self, key: str) -> Any:
        if key == 'detected':
            return self.detected
        data = asdict(self)
        if key not in data:
            raise KeyError(key)
        return data[key]

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data['detected'] = self.detected
        return data


@dataclass(frozen=True)
class SFPResult:
    detected: bool
    quality_score: int
    liquidity_depth: float
    rejection_strength: int
    volume_confirmed: bool
    type: Optional[str] = None
    level: Optional[float] = None
    rvol: float = 0.0
    index: Any = None
    return_inside_ratio: float = 0.0
    close_position_score: int = 0
    displacement_ratio: float = 0.0
    opposite_wick_ratio: float = 0.0
    hold_confirmed: bool = True
    swept: bool = True
    rejection_wick_ratio: float = 0.0
    level_type: Optional[str] = None
    level_strength: float = 0.0
    level_touches: int = 0
    level_age_bars: int = 0
    level_distance_atr: float = 0.0
    level_swept: bool = False
    absorption_warning: bool = False
    absorption_score: float = 0.0
    open: Optional[float] = None
    high: Optional[float] = None
    low: Optional[float] = None
    close: Optional[float] = None

    def __bool__(self) -> bool:
        return self.detected

    def get(self, key: str, default: Any = None) -> Any:
        return asdict(self).get(key, default)

    def __getitem__(self, key: str) -> Any:
        data = asdict(self)
        if key not in data:
            raise KeyError(key)
        return data[key]

    def __setitem__(self, key: str, value: Any) -> None:
        object.__setattr__(self, key, value)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class FVGResult:
    detected: bool
    quality_score: int
    tested: bool
    invalidated: bool
    age_bars: int
    overlap_percent: int
    type: Optional[str] = None
    top: float = 0.0
    bottom: float = 0.0
    start_index: Any = None
    end_index: Any = None
    size_atr_ratio: float = 0.0
    displacement_ratio: float = 0.0
    rvol: float = 0.0
    volume_confirmed: bool = False
    retest_depth: float = 0.0
    retest_count: int = 0
    wick_violated: bool = False
    close_invalidated: bool = False
    absorption_warning: bool = False
    absorption_score: float = 0.0
    invalidation_reason: Optional[str] = None
    invalidated_at: Any = None
    invalidation_price: Optional[float] = None
    invalidation_boundary: Optional[float] = None
    invalidation_operator: Optional[str] = None

    def __bool__(self) -> bool:
        return self.detected and not self.invalidated

    def get(self, key: str, default: Any = None) -> Any:
        return asdict(self).get(key, default)

    def __getitem__(self, key: str) -> Any:
        data = asdict(self)
        if key not in data:
            raise KeyError(key)
        return data[key]

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class MarketStructure:
    trend: str
    confidence: int
    reason: str

    def __bool__(self) -> bool:
        return self.trend != 'neutral'

    def get(self, key: str, default: Any = None) -> Any:
        return asdict(self).get(key, default)

    def __getitem__(self, key: str) -> Any:
        data = asdict(self)
        if key not in data:
            raise KeyError(key)
        return data[key]

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

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


def evaluate_market_structure(
    df: pd.DataFrame,
    swing_highs: pd.DataFrame,
    swing_lows: pd.DataFrame,
    trend_data: Optional[Dict] = None,
    recent_structure_events: Optional[List[Dict]] = None,
    config: Optional[MarketStructureConfig] = None,
) -> MarketStructure:
    """
    Оценивает общий market state без принудительного выбора направления.
    Намеренное изменение поведения: при конфликтной/узкой структуре возвращается neutral,
    чтобы analyzer не искал сделки в рынке без подтвержденного направления.
    """
    config = config or MarketStructureConfig()

    if df.empty:
        return MarketStructure(trend='neutral', confidence=0, reason='No closed candles')

    recent_highs = swing_highs.sort_index().tail(config.swing_lookback)
    recent_lows = swing_lows.sort_index().tail(config.swing_lookback)

    if len(recent_highs) < 2 or len(recent_lows) < 2:
        return MarketStructure(
            trend='neutral',
            confidence=config.neutral_confidence_cap,
            reason='No confirmed swing structure',
        )

    high_values = recent_highs['high'].astype(float)
    low_values = recent_lows['low'].astype(float)
    high_diff = high_values.diff().dropna()
    low_diff = low_values.diff().dropna()

    has_hh = bool((high_diff > 0).any())
    has_lh = bool((high_diff < 0).any())
    has_hl = bool((low_diff > 0).any())
    has_ll = bool((low_diff < 0).any())
    latest_high_change = float(high_diff.iloc[-1]) if not high_diff.empty else 0.0
    latest_low_change = float(low_diff.iloc[-1]) if not low_diff.empty else 0.0
    latest_hh = latest_high_change > 0
    latest_lh = latest_high_change < 0
    latest_hl = latest_low_change > 0
    latest_ll = latest_low_change < 0

    if recent_structure_events and len(recent_structure_events) >= 2:
        event_directions = [
            'bullish' if 'bullish' in event.get('type', '') else 'bearish'
            for event in recent_structure_events[-2:]
            if event.get('type')
        ]
        if len(set(event_directions)) > 1:
            return MarketStructure(
                trend='neutral',
                confidence=config.neutral_confidence_cap,
                reason='Conflicting recent BOS',
            )

    if latest_hh and latest_ll:
        return MarketStructure(
            trend='neutral',
            confidence=config.conflicting_swing_confidence,
            reason='Conflicting swing structure',
        )

    if latest_lh and latest_hl and not (latest_hh or latest_ll):
        return MarketStructure(
            trend='neutral',
            confidence=config.compressed_swing_confidence,
            reason='Compressed swing structure',
        )

    adx_value = trend_data.get('adx_value') if trend_data else None
    if adx_value is not None and float(adx_value) < config.adx_neutral_threshold:
        return MarketStructure(
            trend='neutral',
            confidence=round(float(adx_value)),
            reason='ADX below neutral threshold',
        )

    range_window = df.tail(config.range_lookback_bars)
    if not range_window.empty and 'atr' in range_window.columns and not range_window['atr'].isnull().all():
        market_range = float(range_window['high'].max() - range_window['low'].min())
        avg_atr = float(range_window['atr'].mean())
        if avg_atr > 0 and (market_range / avg_atr) < config.min_range_atr_ratio:
            return MarketStructure(
                trend='neutral',
                confidence=round((market_range / avg_atr) * 10),
                reason='Range too narrow',
            )

    if latest_hh and latest_hl:
        confidence = config.directional_confidence_floor + (config.trend_alignment_bonus if trend_data and trend_data.get('is_bullish') else 0)
        return MarketStructure(trend='bullish', confidence=min(confidence, 100), reason='Confirmed HH/HL structure')

    if latest_lh and latest_ll:
        confidence = config.directional_confidence_floor + (config.trend_alignment_bonus if trend_data and trend_data.get('is_bullish') is False else 0)
        return MarketStructure(trend='bearish', confidence=min(confidence, 100), reason='Confirmed LH/LL structure')

    return MarketStructure(
        trend='neutral',
        confidence=config.neutral_confidence_cap,
        reason='No confirmed directional structure',
    )


def find_fvg(
    df: pd.DataFrame, 
    min_size_atr_ratio: float = 0.5, 
    volume_filter: bool = False,
    atr_series: Optional[pd.Series] = None,
    rvol_series: Optional[pd.Series] = None,
    config: Optional[FVGConfig] = None,
) -> List[FVGResult]:
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
    config = config or FVGConfig()
    fvgs = []

    def _count_retests(touch_mask: pd.Series) -> int:
        retests = 0
        was_touching = False
        for is_touching in touch_mask:
            if is_touching and not was_touching:
                retests += 1
            was_touching = bool(is_touching)
        return retests

    def _age_score(age_bars: int) -> int:
        if age_bars < config.ideal_min_age_bars:
            return round(config.age_score_weight * 0.5)
        if age_bars <= config.max_fresh_age_bars:
            return config.age_score_weight
        stale_ratio = max(0.0, 1 - ((age_bars - config.max_fresh_age_bars) / config.max_fresh_age_bars))
        return round(config.age_score_weight * stale_ratio)

    def _build_fvg_result(
        fvg_type: str,
        top: float,
        bottom: float,
        start_index: Any,
        end_index: Any,
        impulse_index: Any,
    ) -> FVGResult:
        pos = df.index.get_loc(end_index)
        future_candles = df.iloc[pos + 1:]
        fvg_size = max(top - bottom, 0.0)
        atr = _safe_float(atr_series.loc[end_index]) if atr_series is not None and end_index in atr_series.index else 0.0
        if atr <= 0:
            atr = fvg_size if fvg_size > 0 else 1.0

        rvol = 0.0
        if rvol_series is not None and impulse_index in rvol_series.index:
            rvol = _safe_float(rvol_series.loc[impulse_index])
        elif 'rvol' in df.columns:
            rvol = _safe_float(df.loc[impulse_index, 'rvol'])

        displacement = evaluate_displacement(
            df.loc[impulse_index],
            atr=atr,
            rvol=rvol if rvol > 0 else None,
            direction=fvg_type,
        )
        displacement_ratio = displacement.atr_ratio
        size_atr_ratio = fvg_size / atr
        volume_confirmed = (
            displacement.volume_ratio is not None
            and displacement.volume_ratio >= config.min_rvol
            and not displacement.absorption_warning
        )
        age_bars = len(df) - pos - 1

        if future_candles.empty or fvg_size <= 0:
            tested = False
            invalidated = False
            wick_violated = False
            close_invalidated = False
            overlap_percent = 0
            retest_depth = 0.0
            retest_count = 0
        elif fvg_type == 'bullish':
            touch_mask = (future_candles['low'] <= top) & (future_candles['high'] >= bottom)
            tested = bool(touch_mask.any())
            deepest_price = _safe_float(future_candles['low'].min(), top)
            retest_depth = max(0.0, min((top - deepest_price) / fvg_size, 1.0))
            overlap_percent = round(retest_depth * 100)
            wick_violated = bool((future_candles['low'] <= bottom).any())
            close_invalidations = future_candles[future_candles['close'] < bottom]
            close_invalidated = bool(not close_invalidations.empty)
            invalidated = close_invalidated
            retest_count = _count_retests(touch_mask)
        else:
            touch_mask = (future_candles['high'] >= bottom) & (future_candles['low'] <= top)
            tested = bool(touch_mask.any())
            deepest_price = _safe_float(future_candles['high'].max(), bottom)
            retest_depth = max(0.0, min((deepest_price - bottom) / fvg_size, 1.0))
            overlap_percent = round(retest_depth * 100)
            wick_violated = bool((future_candles['high'] >= top).any())
            close_invalidations = future_candles[future_candles['close'] > top]
            close_invalidated = bool(not close_invalidations.empty)
            invalidated = close_invalidated
            retest_count = _count_retests(touch_mask)

        size_score = _score_ratio(size_atr_ratio, min_size_atr_ratio, config.excellent_size_atr, config.size_score_weight)
        displacement_score = _score_ratio(
            displacement_ratio,
            min_size_atr_ratio,
            config.excellent_displacement_atr,
            config.displacement_score_weight,
        )
        overlap_score = round(config.overlap_score_weight * max(0.0, 1 - (overlap_percent / 100)))
        retest_score = config.retest_score_weight if retest_count <= 1 else max(0, config.retest_score_weight - ((retest_count - 1) * 4))

        quality_score = round(
            size_score
            + displacement_score
            + (config.volume_score_weight if volume_confirmed else 0)
            + _age_score(age_bars)
            + overlap_score
            + retest_score
            - (config.wick_violation_penalty if wick_violated and not close_invalidated else 0)
        )
        quality_score = max(0, min(100, quality_score))

        if close_invalidated:
            quality_score = config.invalid_quality_score
            invalidated = True
        invalidation_reason = None
        invalidated_at = None
        invalidation_price = None
        invalidation_boundary = None
        invalidation_operator = None
        if close_invalidated and not future_candles.empty:
            invalidation_reason = "price_closed_through_fvg"
            invalidation_row = close_invalidations.iloc[0]
            invalidated_at = close_invalidations.index[0]
            invalidation_price = _safe_float(invalidation_row.get('close'))
            if fvg_type == 'bullish':
                invalidation_boundary = bottom
                invalidation_operator = "close < bottom"
            else:
                invalidation_boundary = top
                invalidation_operator = "close > top"

        return FVGResult(
            detected=True,
            quality_score=quality_score,
            tested=tested,
            invalidated=invalidated,
            age_bars=age_bars,
            overlap_percent=overlap_percent,
            type=fvg_type,
            top=round(top, 8),
            bottom=round(bottom, 8),
            start_index=start_index,
            end_index=end_index,
            size_atr_ratio=round(size_atr_ratio, 4),
            displacement_ratio=round(displacement_ratio, 4),
            rvol=rvol,
            volume_confirmed=volume_confirmed,
            retest_depth=round(retest_depth, 4),
            retest_count=retest_count,
            wick_violated=wick_violated,
            close_invalidated=close_invalidated,
            absorption_warning=displacement.absorption_warning,
            absorption_score=displacement.absorption_score,
            invalidation_reason=invalidation_reason,
            invalidated_at=invalidated_at,
            invalidation_price=invalidation_price,
            invalidation_boundary=invalidation_boundary,
            invalidation_operator=invalidation_operator,
        )
    
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
        
        fvgs.append(_build_fvg_result('bullish', top, bottom, prev_2_idx, idx, prev_1_idx))
        
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

        fvgs.append(_build_fvg_result('bearish', top, bottom, prev_2_idx, idx, prev_1_idx))
        
    return fvgs


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if pd.isna(value):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _score_ratio(value: float, minimum: float, excellent: float, weight: int) -> float:
    if value < minimum:
        return 0.0
    if excellent <= minimum:
        return float(weight)
    normalized = min((value - minimum) / (excellent - minimum), 1.0)
    return weight * normalized


def _build_swing_sequence(swing_highs: pd.DataFrame, swing_lows: pd.DataFrame) -> List[Dict[str, Any]]:
    points = []

    previous_high = None
    for idx, row in swing_highs.sort_index().iterrows():
        price = _safe_float(row.get('high'))
        if previous_high is None:
            label = 'H'
        elif price > previous_high:
            label = 'HH'
        elif price < previous_high:
            label = 'LH'
        else:
            label = 'EH'

        points.append({'kind': 'high', 'label': label, 'price': price, 'index': idx})
        previous_high = price

    previous_low = None
    for idx, row in swing_lows.sort_index().iterrows():
        price = _safe_float(row.get('low'))
        if previous_low is None:
            label = 'L'
        elif price > previous_low:
            label = 'HL'
        elif price < previous_low:
            label = 'LL'
        else:
            label = 'EL'

        points.append({'kind': 'low', 'label': label, 'price': price, 'index': idx})
        previous_low = price

    return sorted(points, key=lambda point: point['index'])


def _labels_between(points: List[Dict[str, Any]], start_idx: Any, end_idx: Any) -> Tuple[str, ...]:
    return tuple(
        point['label']
        for point in points
        if start_idx <= point['index'] <= end_idx
    )


def _sequence_score(sequence_labels: Tuple[str, ...], required_labels: Tuple[str, ...]) -> int:
    if not sequence_labels:
        return 0

    matched = 0
    search_from = 0
    for required_label in required_labels:
        try:
            found_at = sequence_labels.index(required_label, search_from)
        except ValueError:
            continue
        matched += 1
        search_from = found_at + 1

    return round((matched / len(required_labels)) * 100)


def _sequence_matches_order(sequence_labels: Tuple[str, ...], required_order: Tuple[str, ...]) -> bool:
    return _sequence_score(sequence_labels, required_order) == 100


def _build_choch_result(
    base_result: BOSResult,
    struct_type: str,
    swing_sequence_valid: bool,
    sequence_score: int,
    sequence_labels: Tuple[str, ...],
    config: CHoCHConfig,
) -> CHoCHResult:
    confidence = round(
        (base_result.quality_score * config.impulse_score_weight / 100)
        + (sequence_score * config.sequence_score_weight / 100)
    )
    confidence = max(0, min(100, confidence))
    confirmed = (
        base_result.detected
        and swing_sequence_valid
        and base_result.quality_score >= config.min_quality_score
        and confidence >= config.min_confidence
    )

    return CHoCHResult(
        confirmed=confirmed,
        quality_score=base_result.quality_score,
        confidence=confidence,
        swing_sequence_valid=swing_sequence_valid,
        type=struct_type,
        level=base_result.level,
        rvol=base_result.rvol,
        index=base_result.index,
        displacement_ratio=base_result.displacement_ratio,
        body_ratio=base_result.body_ratio,
        volume_confirmed=base_result.volume_confirmed,
        close_confirmed=base_result.close_confirmed,
        body_size=base_result.body_size,
        candle_range=base_result.candle_range,
        opposite_wick_ratio=base_result.opposite_wick_ratio,
        hold_confirmed=base_result.hold_confirmed,
        swing_sequence=sequence_labels,
        absorption_warning=base_result.absorption_warning,
        absorption_score=base_result.absorption_score,
        open=base_result.open,
        high=base_result.high,
        low=base_result.low,
        close=base_result.close,
    )


def _detect_confirmed_choch(
    candle: pd.Series,
    valid_highs: pd.DataFrame,
    valid_lows: pd.DataFrame,
    future_candles: Optional[pd.DataFrame],
    bos_config: BOSConfig,
    choch_config: CHoCHConfig,
) -> Optional[CHoCHResult]:
    if len(valid_highs) < 3 or len(valid_lows) < 3:
        return None

    highs = valid_highs.sort_index()
    lows = valid_lows.sort_index()
    h_prev2, h_prev1, h_last = highs.iloc[-3], highs.iloc[-2], highs.iloc[-1]
    l_prev2, l_prev1, l_last = lows.iloc[-3], lows.iloc[-2], lows.iloc[-1]
    h_prev2_idx, h_prev1_idx, h_last_idx = highs.index[-3], highs.index[-2], highs.index[-1]
    l_prev2_idx, l_prev1_idx, l_last_idx = lows.index[-3], lows.index[-2], lows.index[-1]
    close_price = _safe_float(candle.get('close'))
    swing_points = _build_swing_sequence(highs, lows)

    prior_bullish_sequence = (
        _safe_float(h_prev1.get('high')) > _safe_float(h_prev2.get('high'))
        and _safe_float(l_prev1.get('low')) > _safe_float(l_prev2.get('low'))
    )
    bearish_transition = (
        _safe_float(l_last.get('low')) < _safe_float(l_prev1.get('low'))
        and _safe_float(h_last.get('high')) < _safe_float(h_prev1.get('high'))
        and l_prev1_idx < l_last_idx < h_last_idx < candle.name
    )
    bearish_break_level = _safe_float(l_last.get('low'))
    if prior_bullish_sequence and bearish_transition and close_price < bearish_break_level:
        base_result = _build_bos_result(
            candle,
            'bearish_choch',
            'bearish',
            bearish_break_level,
            future_candles,
            bos_config,
        )
        sequence_labels = _labels_between(swing_points, h_prev2_idx, h_last_idx)
        sequence_score = _sequence_score(sequence_labels, ('HH', 'HL', 'LL', 'LH'))
        sequence_valid = _sequence_matches_order(sequence_labels, ('HH', 'HL', 'LL', 'LH'))
        return _build_choch_result(
            base_result,
            'bearish_choch',
            sequence_valid,
            sequence_score,
            sequence_labels,
            choch_config,
        )

    prior_bearish_sequence = (
        _safe_float(h_prev1.get('high')) < _safe_float(h_prev2.get('high'))
        and _safe_float(l_prev1.get('low')) < _safe_float(l_prev2.get('low'))
    )
    bullish_transition = (
        _safe_float(h_last.get('high')) > _safe_float(h_prev1.get('high'))
        and _safe_float(l_last.get('low')) > _safe_float(l_prev1.get('low'))
        and h_prev1_idx < h_last_idx < l_last_idx < candle.name
    )
    bullish_break_level = _safe_float(h_last.get('high'))
    if prior_bearish_sequence and bullish_transition and close_price > bullish_break_level:
        base_result = _build_bos_result(
            candle,
            'bullish_choch',
            'bullish',
            bullish_break_level,
            future_candles,
            bos_config,
        )
        sequence_labels = _labels_between(swing_points, l_prev2_idx, l_last_idx)
        sequence_score = _sequence_score(sequence_labels, ('LL', 'LH', 'HH', 'HL'))
        sequence_valid = _sequence_matches_order(sequence_labels, ('LL', 'LH', 'HH', 'HL'))
        return _build_choch_result(
            base_result,
            'bullish_choch',
            sequence_valid,
            sequence_score,
            sequence_labels,
            choch_config,
        )

    return None


def _check_hold_confirmation(
    direction: str,
    level: float,
    atr: float,
    future_candles: Optional[pd.DataFrame],
    config: BOSConfig
) -> bool:
    if config.hold_confirmation_bars <= 0 or future_candles is None:
        return True

    confirmation_window = future_candles.head(config.hold_confirmation_bars)
    if len(confirmation_window) < config.hold_confirmation_bars:
        return False

    buffer = atr * config.hold_buffer_atr
    closes = confirmation_window['close'].astype(float)

    if direction == 'bullish':
        return bool((closes >= level - buffer).all())

    return bool((closes <= level + buffer).all())


def _check_sfp_hold_confirmation(
    direction: str,
    level: float,
    atr: float,
    future_candles: Optional[pd.DataFrame],
    config: SFPConfig,
) -> bool:
    if config.hold_confirmation_bars <= 0 or future_candles is None:
        return True

    confirmation_window = future_candles.head(config.hold_confirmation_bars)
    if len(confirmation_window) < config.hold_confirmation_bars:
        return False

    buffer = atr * config.hold_buffer_atr
    closes = confirmation_window['close'].astype(float)

    if direction == 'bearish':
        return bool((closes <= level + buffer).all())

    return bool((closes >= level - buffer).all())


def _level_value(level: Optional[Any], key: str, default: Any = None) -> Any:
    if level is None:
        return default
    if isinstance(level, dict):
        return level.get(key, default)
    return getattr(level, key, default)


def _build_sfp_result(
    candle: pd.Series,
    struct_type: str,
    direction: str,
    level: float,
    future_candles: Optional[pd.DataFrame],
    config: SFPConfig,
    liquidity_level: Optional[Any] = None,
) -> SFPResult:
    candle_open = _safe_float(candle.get('open'))
    candle_high = _safe_float(candle.get('high'))
    candle_low = _safe_float(candle.get('low'))
    candle_close = _safe_float(candle.get('close'))
    atr = _safe_float(candle.get('atr'))
    rvol = _safe_float(candle.get('rvol'))

    candle_range = max(candle_high - candle_low, 0.0)
    if atr <= 0:
        atr = candle_range if candle_range > 0 else 1.0

    displacement = evaluate_displacement(
        candle,
        atr=atr,
        rvol=rvol if rvol > 0 else None,
        direction=direction,
    )

    if direction == 'bearish':
        liquidity_depth = max(candle_high - level, 0.0) / atr
        return_inside_ratio = max(level - candle_close, 0.0) / atr
        rejection_wick = max(candle_high - max(candle_open, candle_close), 0.0)
        opposite_wick = max(min(candle_open, candle_close) - candle_low, 0.0)
        close_returned_inside = candle_close < level
    else:
        liquidity_depth = max(level - candle_low, 0.0) / atr
        return_inside_ratio = max(candle_close - level, 0.0) / atr
        rejection_wick = max(min(candle_open, candle_close) - candle_low, 0.0)
        opposite_wick = max(candle_high - max(candle_open, candle_close), 0.0)
        close_returned_inside = candle_close > level

    rejection_ratio = rejection_wick / candle_range if candle_range > 0 else 0.0
    displacement_ratio = displacement.atr_ratio
    close_position = displacement.close_position
    opposite_wick_ratio = opposite_wick / candle_range if candle_range > 0 else 1.0
    volume_confirmed = (
        displacement.volume_ratio is not None
        and displacement.volume_ratio >= config.min_rvol
        and not displacement.absorption_warning
    )
    hold_confirmed = _check_sfp_hold_confirmation(direction, level, atr, future_candles, config)

    depth_score = _score_ratio(
        liquidity_depth,
        config.min_liquidity_depth_atr,
        config.excellent_liquidity_depth_atr,
        config.depth_score_weight,
    )
    rejection_score = _score_ratio(
        return_inside_ratio,
        config.min_rejection_atr,
        config.excellent_rejection_atr,
        config.rejection_score_weight,
    )
    close_position_score = round(max(0.0, min(close_position, 1.0)) * config.close_position_score_weight)
    displacement_score = _score_ratio(
        displacement_ratio,
        config.min_displacement_atr,
        config.excellent_displacement_atr,
        config.displacement_score_weight,
    )

    candle_quality_score = round(
        depth_score
        + rejection_score
        + close_position_score
        + (config.opposite_wick_score_weight if opposite_wick_ratio <= config.max_opposite_wick_ratio else 0)
        + displacement_score
        + (config.volume_score_weight if volume_confirmed else 0)
        + (config.hold_score_weight if hold_confirmed else 0)
    )
    candle_quality_score = max(0, min(100, candle_quality_score))

    level_type = _level_value(liquidity_level, 'type')
    level_strength = float(_level_value(liquidity_level, 'strength', 0.0) or 0.0)
    level_touches = int(_level_value(liquidity_level, 'touches', 0) or 0)
    level_age_bars = int(_level_value(liquidity_level, 'age_bars', 0) or 0)
    level_distance_atr = float(_level_value(liquidity_level, 'distance_atr', 0.0) or 0.0)
    level_swept = bool(_level_value(liquidity_level, 'swept', False))

    quality_score = candle_quality_score
    level_is_usable = True
    if liquidity_level is not None:
        level_is_usable = (not level_swept) and level_strength >= config.min_liquidity_level_strength
        quality_score = round(
            candle_quality_score * config.candle_quality_weight
            + level_strength * config.level_quality_weight
        )
        quality_score = max(0, min(100, quality_score))

    rejection_strength = round(
        _score_ratio(return_inside_ratio, config.min_rejection_atr, config.excellent_rejection_atr, 60)
        + max(0.0, min(close_position, 1.0)) * 25
        + (15 if opposite_wick_ratio <= config.max_opposite_wick_ratio else 0)
    )
    rejection_strength = max(0, min(100, rejection_strength))

    detected = all([
        close_returned_inside,
        liquidity_depth >= config.min_liquidity_depth_atr,
        return_inside_ratio >= config.min_rejection_atr,
        opposite_wick_ratio <= config.max_opposite_wick_ratio,
        hold_confirmed,
        level_is_usable,
        quality_score >= config.min_quality_score,
    ])

    return SFPResult(
        detected=detected,
        quality_score=quality_score,
        liquidity_depth=round(liquidity_depth, 4),
        rejection_strength=rejection_strength,
        volume_confirmed=volume_confirmed,
        type=struct_type,
        level=level,
        rvol=rvol,
        index=candle.name,
        return_inside_ratio=round(return_inside_ratio, 4),
        close_position_score=close_position_score,
        displacement_ratio=round(displacement_ratio, 4),
        opposite_wick_ratio=round(opposite_wick_ratio, 4),
        hold_confirmed=hold_confirmed,
        swept=close_returned_inside,
        rejection_wick_ratio=round(rejection_ratio, 4),
        level_type=level_type,
        level_strength=round(level_strength, 2),
        level_touches=level_touches,
        level_age_bars=level_age_bars,
        level_distance_atr=round(level_distance_atr, 4),
        level_swept=level_swept,
        absorption_warning=displacement.absorption_warning,
        absorption_score=displacement.absorption_score,
        open=round(candle_open, 8),
        high=round(candle_high, 8),
        low=round(candle_low, 8),
        close=round(candle_close, 8),
    )


def _build_bos_result(
    candle: pd.Series,
    struct_type: str,
    direction: str,
    level: float,
    future_candles: Optional[pd.DataFrame],
    config: BOSConfig
) -> BOSResult:
    candle_open = _safe_float(candle.get('open'))
    candle_high = _safe_float(candle.get('high'))
    candle_low = _safe_float(candle.get('low'))
    candle_close = _safe_float(candle.get('close'))
    atr = _safe_float(candle.get('atr'))
    rvol = _safe_float(candle.get('rvol'))

    displacement = evaluate_displacement(
        candle,
        atr=atr,
        rvol=rvol if rvol > 0 else None,
        direction=direction,
    )
    body_size = displacement.body
    candle_range = displacement.candle_range
    body_ratio = displacement.body_ratio
    displacement_ratio = displacement.atr_ratio
    close_position = displacement.close_position
    close_buffer = atr * config.close_buffer_atr if atr > 0 else 0.0

    if direction == 'bullish':
        close_confirmed = candle_close > level + close_buffer
        opposite_wick = min(candle_open, candle_close) - candle_low
        close_distance = max(candle_close - level, 0.0)
    else:
        close_confirmed = candle_close < level - close_buffer
        opposite_wick = candle_high - max(candle_open, candle_close)
        close_distance = max(level - candle_close, 0.0)

    opposite_wick_ratio = opposite_wick / candle_range if candle_range > 0 else 1.0
    volume_confirmed = (
        displacement.volume_ratio is not None
        and displacement.volume_ratio >= config.min_rvol
        and not displacement.absorption_warning
    )
    hold_confirmed = _check_hold_confirmation(direction, level, atr, future_candles, config)

    close_score = 0.0
    if close_confirmed and atr > 0:
        close_score = _score_ratio(
            close_distance / atr,
            config.close_buffer_atr,
            config.excellent_close_buffer_atr,
            config.close_score_weight,
        )
    elif close_confirmed:
        close_score = float(config.close_score_weight)

    quality_score = round(
        _score_ratio(body_ratio, config.min_body_ratio, config.excellent_body_ratio, config.body_score_weight)
        + _score_ratio(
            displacement_ratio,
            config.min_displacement_atr,
            config.excellent_displacement_atr,
            config.displacement_score_weight,
        )
        + close_score
        + _score_ratio(
            close_position,
            config.min_close_position,
            config.excellent_close_position,
            config.close_position_score_weight,
        )
        + (config.volume_score_weight if volume_confirmed else 0)
        + (config.wick_score_weight if opposite_wick_ratio <= config.max_opposite_wick_ratio else 0)
        + (config.hold_score_weight if hold_confirmed else 0)
    )
    quality_score = max(0, min(100, quality_score))

    detected = all([
        close_confirmed,
        body_ratio >= config.min_body_ratio,
        displacement_ratio >= config.min_displacement_atr,
        close_position >= config.min_close_position,
        opposite_wick_ratio <= config.max_opposite_wick_ratio,
        hold_confirmed,
        quality_score >= config.min_quality_score,
    ])

    return BOSResult(
        detected=detected,
        quality_score=quality_score,
        displacement_ratio=round(displacement_ratio, 4),
        body_ratio=round(body_ratio, 4),
        volume_confirmed=volume_confirmed,
        close_confirmed=close_confirmed,
        type=struct_type,
        level=level,
        rvol=rvol,
        index=candle.name,
        body_size=round(body_size, 8),
        candle_range=round(candle_range, 8),
        opposite_wick_ratio=round(opposite_wick_ratio, 4),
        hold_confirmed=hold_confirmed,
        close_position=round(close_position, 4),
        absorption_warning=displacement.absorption_warning,
        absorption_score=displacement.absorption_score,
        open=round(candle_open, 8),
        high=round(candle_high, 8),
        low=round(candle_low, 8),
        close=round(candle_close, 8),
    )


def detect_structure_break(
    last_closed_candle: pd.Series,
    swing_highs: pd.DataFrame,
    swing_lows: pd.DataFrame,
    right_bars: int = 2,
    timeframe_minutes: int = 15,
    config: Optional[BOSConfig] = None,
    choch_config: Optional[CHoCHConfig] = None,
    future_candles: Optional[pd.DataFrame] = None,
) -> Optional[Union[BOSResult, CHoCHResult]]:
    """
    Определяет слом структуры (BOS/CHoCH) на последней закрытой свече.
    Включает универсальную защиту от Lookahead Bias. Поддерживает MTF-анализ.
    """
    config = config or BOSConfig()
    choch_config = choch_config or CHoCHConfig()
    current_idx = last_closed_candle.name
    
    # ЗАЩИТА ОТ LOOKAHEAD BIAS (MTF-совместимая)
    if isinstance(current_idx, (int, float)):
        offset_minutes = right_bars * timeframe_minutes
        if current_idx > 1e11: 
            offset = offset_minutes * 60 * 1000 # Миллисекунды
        elif current_idx > 1e8: 
            offset = offset_minutes * 60        # Секунды
        else: 
            offset = right_bars                 # Индексы
    else:
        offset = pd.Timedelta(minutes=timeframe_minutes * right_bars)
        
    valid_highs = swing_highs[swing_highs.index < (current_idx - offset)]
    valid_lows = swing_lows[swing_lows.index < (current_idx - offset)]

    if len(valid_highs) < 2 or len(valid_lows) < 2:
        return None

    last_h1, last_h2 = valid_highs.iloc[-1], valid_highs.iloc[-2]
    last_l1, last_l2 = valid_lows.iloc[-1], valid_lows.iloc[-2]

    level_high = float(last_h1['high'])
    level_low = float(last_l1['low'])
    close_price = float(last_closed_candle['close'])

    choch_result = _detect_confirmed_choch(
        last_closed_candle,
        valid_highs,
        valid_lows,
        future_candles,
        config,
        choch_config,
    )
    if choch_result:
        return choch_result

    # 2. ОПРЕДЕЛЕНИЕ ТЕКУЩЕГО ХАРАКТЕРА СТРУКТУРЫ
    is_making_higher_highs = last_h1['high'] > last_h2['high']
    is_making_higher_lows = last_l1['low'] > last_l2['low']
    is_making_lower_highs = last_h1['high'] < last_h2['high']
    is_making_lower_lows = last_l1['low'] < last_l2['low']

    is_bullish_struct = is_making_higher_highs or (is_making_higher_lows and not is_making_lower_highs)
    is_bearish_struct = is_making_lower_lows or (is_making_lower_highs and not is_making_higher_lows)

    if not is_bullish_struct and not is_bearish_struct:
        return None

    # 3. КЛАССИФИКАЦИЯ ПРОБОЯ
    if close_price > level_high:
        if is_bearish_struct:
            return None
        struct_type = 'bullish_bos'
        return _build_bos_result(last_closed_candle, struct_type, 'bullish', level_high, future_candles, config)

    if close_price < level_low:
        if is_bullish_struct:
            return None
        struct_type = 'bearish_bos'
        return _build_bos_result(last_closed_candle, struct_type, 'bearish', level_low, future_candles, config)
        
    return None

def detect_sfp(
    last_closed_candle: pd.Series,
    swing_highs: pd.DataFrame,
    swing_lows: pd.DataFrame,
    right_bars: int = 2,
    timeframe_minutes: int = 15,
    config: Optional[SFPConfig] = None,
    future_candles: Optional[pd.DataFrame] = None,
) -> Optional[SFPResult]:
    """
    Определяет паттерн "Захват ликвидности" (SFP) на последней закрытой свече.
    Включает универсальную защиту от Lookahead Bias. Поддерживает MTF-анализ.
    """
    config = config or SFPConfig()
    if swing_highs.empty or swing_lows.empty: 
        return None

    current_idx = last_closed_candle.name
    
    # ЗАЩИТА ОТ LOOKAHEAD BIAS (MTF-совместимая)
    if isinstance(current_idx, (int, float)):
        offset_minutes = right_bars * timeframe_minutes
        if current_idx > 1e11: 
            offset = offset_minutes * 60 * 1000
        elif current_idx > 1e8: 
            offset = offset_minutes * 60
        else: 
            offset = right_bars
    else:
        offset = pd.Timedelta(minutes=timeframe_minutes * right_bars)
        
    relevant_highs = swing_highs[swing_highs.index < (current_idx - offset)]
    relevant_lows = swing_lows[swing_lows.index < (current_idx - offset)]

    if relevant_highs.empty or relevant_lows.empty: 
        return None

    last_swing_high = relevant_highs.iloc[-1]
    last_swing_low = relevant_lows.iloc[-1]

    level_high = float(last_swing_high['high'])
    level_low = float(last_swing_low['low'])
    candle_high = float(last_closed_candle['high'])
    candle_low = float(last_closed_candle['low'])
    candle_close = float(last_closed_candle['close'])

    if candle_high > level_high and candle_close < level_high:
        result = _build_sfp_result(
            last_closed_candle,
            'bearish_sfp',
            'bearish',
            level_high,
            future_candles,
            config,
        )
        return result if result else None

    if candle_low < level_low and candle_close > level_low:
        result = _build_sfp_result(
            last_closed_candle,
            'bullish_sfp',
            'bullish',
            level_low,
            future_candles,
            config,
        )
        return result if result else None
        
    return None


def _liquidity_level_side(level: Any, candle_close: float) -> str:
    level_type = str(_level_value(level, 'type', ''))
    if level_type in ('equal_highs', 'buy_side', 'old_high'):
        return 'buy_side'
    if level_type in ('equal_lows', 'sell_side', 'old_low'):
        return 'sell_side'
    price = float(_level_value(level, 'price', 0.0) or 0.0)
    return 'buy_side' if price > candle_close else 'sell_side'


def detect_sfp_against_liquidity_levels(
    last_closed_candle: pd.Series,
    liquidity_levels: Optional[List[Any]],
    config: Optional[SFPConfig] = None,
    future_candles: Optional[pd.DataFrame] = None,
) -> Optional[SFPResult]:
    """
    Ищет SFP против свежих уровней Liquidity Map.
    Функция ожидает карту, построенную только на свечах ДО проверяемой свечи.
    """
    config = config or SFPConfig()
    if not liquidity_levels:
        return None

    candle_high = _safe_float(last_closed_candle.get('high'))
    candle_low = _safe_float(last_closed_candle.get('low'))
    candle_close = _safe_float(last_closed_candle.get('close'))
    candidates: List[SFPResult] = []

    for level_data in liquidity_levels:
        if bool(_level_value(level_data, 'swept', False)):
            continue
        if float(_level_value(level_data, 'strength', 0.0) or 0.0) < config.min_liquidity_level_strength:
            continue

        level = float(_level_value(level_data, 'price', 0.0) or 0.0)
        side = _liquidity_level_side(level_data, candle_close)

        if side == 'buy_side' and candle_high > level and candle_close < level:
            result = _build_sfp_result(
                last_closed_candle,
                'bearish_sfp',
                'bearish',
                level,
                future_candles,
                config,
                liquidity_level=level_data,
            )
            if result:
                candidates.append(result)

        if side == 'sell_side' and candle_low < level and candle_close > level:
            result = _build_sfp_result(
                last_closed_candle,
                'bullish_sfp',
                'bullish',
                level,
                future_candles,
                config,
                liquidity_level=level_data,
            )
            if result:
                candidates.append(result)

    if not candidates:
        return None

    return max(
        candidates,
        key=lambda item: (
            item.quality_score,
            item.level_strength,
            item.rejection_strength,
            item.liquidity_depth,
        ),
    )
