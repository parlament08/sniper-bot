from dataclasses import dataclass
from typing import Any, Mapping, Optional

import pandas as pd

from core.models import Direction

MIN_DISPLACEMENT_SCORE = 70


@dataclass(frozen=True)
class DisplacementResult:
    direction: Direction
    score: float
    body: float
    candle_range: float
    body_ratio: float
    atr_ratio: float
    close_position: float
    volume_ratio: Optional[float]
    momentum_score: float
    valid: bool
    reason: str


def evaluate_displacement(
    candle: Mapping[str, Any],
    atr: Optional[float] = None,
    rvol: Optional[float] = None,
    direction: Optional[Direction] = None,
    min_score: int = MIN_DISPLACEMENT_SCORE,
) -> DisplacementResult:
    open_price = _safe_float(_get_value(candle, "open"))
    high = _safe_float(_get_value(candle, "high"))
    low = _safe_float(_get_value(candle, "low"))
    close = _safe_float(_get_value(candle, "close"))

    resolved_direction = direction or _infer_direction(open_price, close)
    body = abs(close - open_price)
    candle_range = max(high - low, 0.0)
    body_ratio = body / candle_range if candle_range > 0 else 0.0

    resolved_atr = _safe_float(atr, None)
    if resolved_atr is None:
        resolved_atr = _safe_float(_get_value(candle, "atr"))
    atr_ratio = body / resolved_atr if resolved_atr > 0 else 0.0

    volume_ratio = _safe_float(rvol, None)
    if volume_ratio is None:
        volume_ratio = _safe_float(_get_value(candle, "rvol"), None)

    close_position = _close_position(resolved_direction, high, low, close, candle_range)

    score = 0.0
    score += min(body_ratio * 35, 35)
    score += min(atr_ratio * 25, 25)
    score += min(close_position * 25, 25)

    if volume_ratio is not None:
        if volume_ratio >= 1.5:
            score += 15
        elif volume_ratio >= 1.2:
            score += 8

    score = round(max(0.0, min(100.0, score)), 2)
    valid = score >= min_score

    return DisplacementResult(
        direction=resolved_direction,
        score=score,
        body=round(body, 8),
        candle_range=round(candle_range, 8),
        body_ratio=round(body_ratio, 4),
        atr_ratio=round(atr_ratio, 4),
        close_position=round(close_position, 4),
        volume_ratio=volume_ratio,
        momentum_score=score,
        valid=valid,
        reason=_reason(valid, resolved_direction, candle_range),
    )


def _get_value(candle: Mapping[str, Any], key: str) -> Any:
    if isinstance(candle, pd.Series):
        return candle.get(key)
    return candle.get(key)


def _safe_float(value: Any, default: Optional[float] = 0.0) -> Optional[float]:
    try:
        if pd.isna(value):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _infer_direction(open_price: float, close: float) -> Direction:
    if close > open_price:
        return "bullish"
    if close < open_price:
        return "bearish"
    return "neutral"


def _close_position(direction: Direction, high: float, low: float, close: float, candle_range: float) -> float:
    if candle_range <= 0 or direction == "neutral":
        return 0.0
    if direction == "bullish":
        value = (close - low) / candle_range
    else:
        value = (high - close) / candle_range
    return max(0.0, min(1.0, value))


def _reason(valid: bool, direction: Direction, candle_range: float) -> str:
    if candle_range <= 0:
        return "Zero candle range"
    if direction == "neutral":
        return "Neutral candle body"
    if valid:
        return "Strong displacement"
    return "Displacement score below threshold"
