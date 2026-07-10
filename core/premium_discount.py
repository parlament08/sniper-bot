from dataclasses import asdict, dataclass
from typing import Any, Dict, Literal, Optional

import pandas as pd


ZoneType = Literal["premium", "discount", "equilibrium"]
ZoneDepth = Literal["equilibrium", "shallow", "normal", "deep"]
EQUILIBRIUM_TOLERANCE_PERCENT = 2.0
SHALLOW_ZONE_MAX_RANGE_PERCENT = 20.0
DEEP_ZONE_MIN_RANGE_PERCENT = 35.0


@dataclass(frozen=True)
class PremiumDiscountResult:
    zone: ZoneType
    range_high: float
    range_low: float
    equilibrium: float
    price: float
    distance_from_equilibrium_percent: float
    valid_for_buy: bool
    valid_for_sell: bool
    reason: str
    distance_from_equilibrium_range_percent: float = 0.0
    range_timeframe: str = "unknown"
    range_type: str = "last_swing"
    zone_depth: ZoneDepth = "normal"
    zone_strength: float = 0.0
    range_age_bars: int = 0

    def get(self, key: str, default: Any = None) -> Any:
        return asdict(self).get(key, default)

    def __getitem__(self, key: str) -> Any:
        data = asdict(self)
        if key not in data:
            raise KeyError(key)
        return data[key]

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def evaluate_premium_discount(
    price: float,
    swing_highs: pd.DataFrame,
    swing_lows: pd.DataFrame,
    tolerance_percent: float = EQUILIBRIUM_TOLERANCE_PERCENT,
    range_timeframe: str = "unknown",
    range_type: str = "last_swing",
) -> PremiumDiscountResult:
    if swing_highs.empty or swing_lows.empty:
        raise ValueError("Premium/discount requires at least one confirmed swing high and swing low")
    if "high" not in swing_highs.columns or "low" not in swing_lows.columns:
        raise ValueError("Swing data must contain high and low columns")

    last_high = swing_highs.sort_index().iloc[-1]
    last_low = swing_lows.sort_index().iloc[-1]
    range_high = _safe_float(last_high.get("high"))
    range_low = _safe_float(last_low.get("low"))

    if range_high < range_low:
        range_high, range_low = range_low, range_high
    if range_high == range_low:
        raise ValueError("Premium/discount range cannot have equal high and low")

    equilibrium = (range_high + range_low) / 2
    dealing_range = range_high - range_low
    distance_percent = ((price - equilibrium) / equilibrium) * 100 if equilibrium else 0.0
    distance_from_equilibrium_range_percent = (abs(price - equilibrium) / dealing_range) * 100 if dealing_range > 0 else 0.0

    if distance_from_equilibrium_range_percent <= tolerance_percent:
        zone: ZoneType = "equilibrium"
    elif price < equilibrium:
        zone = "discount"
    else:
        zone = "premium"

    zone_depth = _zone_depth(zone, distance_from_equilibrium_range_percent)
    zone_strength = _zone_strength(zone_depth)
    range_age_bars = _range_age_bars(swing_highs, swing_lows)
    valid_for_buy = zone == "discount"
    valid_for_sell = zone == "premium"

    return PremiumDiscountResult(
        zone=zone,
        range_high=round(range_high, 8),
        range_low=round(range_low, 8),
        equilibrium=round(equilibrium, 8),
        price=round(price, 8),
        distance_from_equilibrium_percent=round(distance_percent, 4),
        valid_for_buy=valid_for_buy,
        valid_for_sell=valid_for_sell,
        reason=_reason(zone, zone_depth),
        distance_from_equilibrium_range_percent=round(distance_from_equilibrium_range_percent, 4),
        range_timeframe=range_timeframe,
        range_type=range_type,
        zone_depth=zone_depth,
        zone_strength=zone_strength,
        range_age_bars=range_age_bars,
    )


def _zone_depth(zone: ZoneType, range_distance_percent: float) -> ZoneDepth:
    if zone == "equilibrium":
        return "equilibrium"
    if range_distance_percent < SHALLOW_ZONE_MAX_RANGE_PERCENT:
        return "shallow"
    if range_distance_percent >= DEEP_ZONE_MIN_RANGE_PERCENT:
        return "deep"
    return "normal"


def _zone_strength(zone_depth: ZoneDepth) -> float:
    if zone_depth == "deep":
        return 100.0
    if zone_depth == "normal":
        return 75.0
    if zone_depth == "shallow":
        return 35.0
    return 0.0


def _range_age_bars(swing_highs: pd.DataFrame, swing_lows: pd.DataFrame) -> int:
    try:
        high_pos = len(swing_highs.sort_index()) - 1
        low_pos = len(swing_lows.sort_index()) - 1
        return abs(high_pos - low_pos)
    except Exception:
        return 0


def _reason(zone: ZoneType, zone_depth: ZoneDepth) -> str:
    if zone == "discount":
        return f"Price is in {zone_depth} discount: valid for buy, invalid for sell"
    if zone == "premium":
        return f"Price is in {zone_depth} premium: valid for sell, invalid for buy"
    return "Price is near equilibrium: avoid weak entries"


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if pd.isna(value):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default
