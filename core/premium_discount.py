from dataclasses import asdict, dataclass
from typing import Any, Dict, Literal, Optional

import pandas as pd


ZoneType = Literal["premium", "discount", "equilibrium"]
EQUILIBRIUM_TOLERANCE_PERCENT = 2.0


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
    distance_percent = ((price - equilibrium) / equilibrium) * 100 if equilibrium else 0.0

    if abs(distance_percent) <= tolerance_percent:
        zone: ZoneType = "equilibrium"
    elif price < equilibrium:
        zone = "discount"
    else:
        zone = "premium"

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
        reason=_reason(zone),
    )


def _reason(zone: ZoneType) -> str:
    if zone == "discount":
        return "Price is in discount: valid for buy, invalid for sell"
    if zone == "premium":
        return "Price is in premium: valid for sell, invalid for buy"
    return "Price is near equilibrium: avoid weak entries"


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if pd.isna(value):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default
