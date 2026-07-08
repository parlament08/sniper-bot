from dataclasses import dataclass
from typing import Any, List, Literal, Optional, Sequence

import pandas as pd


LiquidityType = Literal[
    "equal_highs",
    "equal_lows",
    "buy_side",
    "sell_side",
    "internal",
    "external",
    "old_high",
    "old_low",
]

EQUAL_HIGH_TOLERANCE_ATR = 0.15
EQUAL_LOW_TOLERANCE_ATR = 0.15
MIN_EQUAL_HIGH_TOUCHES = 2
MIN_EQUAL_LOW_TOUCHES = 2
OLD_LEVEL_MIN_AGE_BARS = 50


@dataclass(frozen=True)
class LiquidityLevel:
    type: LiquidityType
    price: float
    strength: float
    touches: int
    age_bars: int
    distance_percent: float
    distance_atr: float
    swept: bool
    swept_at: Optional[int]
    source_index: int
    description: str


@dataclass(frozen=True)
class LiquidityMap:
    levels: List[LiquidityLevel]
    nearest_buy_side: Optional[LiquidityLevel]
    nearest_sell_side: Optional[LiquidityLevel]
    strongest_buy_side: Optional[LiquidityLevel]
    strongest_sell_side: Optional[LiquidityLevel]


@dataclass(frozen=True)
class LiquidityConfig:
    equal_high_tolerance_atr: float = EQUAL_HIGH_TOLERANCE_ATR
    equal_low_tolerance_atr: float = EQUAL_LOW_TOLERANCE_ATR
    min_equal_high_touches: int = MIN_EQUAL_HIGH_TOUCHES
    min_equal_low_touches: int = MIN_EQUAL_LOW_TOUCHES
    old_level_min_age_bars: int = OLD_LEVEL_MIN_AGE_BARS
    range_lookback_bars: int = 80


def build_liquidity_map(
    df: pd.DataFrame,
    swing_highs: pd.DataFrame,
    swing_lows: pd.DataFrame,
    atr_series: Optional[pd.Series] = None,
    current_price: Optional[float] = None,
    config: Optional[LiquidityConfig] = None,
) -> LiquidityMap:
    config = config or LiquidityConfig()
    if df.empty:
        return LiquidityMap([], None, None, None, None)

    closed_df = df.copy()
    price = _safe_float(current_price, _safe_float(closed_df.iloc[-1].get("close")))
    atr = _resolve_atr(closed_df, atr_series)
    range_high, range_low = _current_range(closed_df, config.range_lookback_bars)
    levels: List[LiquidityLevel] = []

    high_points = _swing_points(swing_highs, "high", closed_df)
    low_points = _swing_points(swing_lows, "low", closed_df)

    levels.extend(
        _build_equal_levels(
            closed_df,
            high_points,
            "equal_highs",
            tolerance=atr * config.equal_high_tolerance_atr,
            min_touches=config.min_equal_high_touches,
            current_price=price,
            atr=atr,
            description="Equal highs buy-side liquidity",
        )
    )
    levels.extend(
        _build_equal_levels(
            closed_df,
            low_points,
            "equal_lows",
            tolerance=atr * config.equal_low_tolerance_atr,
            min_touches=config.min_equal_low_touches,
            current_price=price,
            atr=atr,
            description="Equal lows sell-side liquidity",
        )
    )

    if high_points:
        last_high = max(high_points, key=lambda point: point["source_index"])
        levels.append(
            _make_level(
                closed_df,
                "buy_side",
                last_high["price"],
                1,
                last_high["source_index"],
                current_price=price,
                atr=atr,
                description="Buy-side liquidity above latest significant swing high",
            )
        )

    if low_points:
        last_low = max(low_points, key=lambda point: point["source_index"])
        levels.append(
            _make_level(
                closed_df,
                "sell_side",
                last_low["price"],
                1,
                last_low["source_index"],
                current_price=price,
                atr=atr,
                description="Sell-side liquidity below latest significant swing low",
            )
        )

    old_high = _old_extreme(high_points, "high", config.old_level_min_age_bars, len(closed_df))
    if old_high:
        levels.append(
            _make_level(
                closed_df,
                "old_high",
                old_high["price"],
                old_high["touches"],
                old_high["source_index"],
                current_price=price,
                atr=atr,
                description="Old high external buy-side liquidity",
            )
        )

    old_low = _old_extreme(low_points, "low", config.old_level_min_age_bars, len(closed_df))
    if old_low:
        levels.append(
            _make_level(
                closed_df,
                "old_low",
                old_low["price"],
                old_low["touches"],
                old_low["source_index"],
                current_price=price,
                atr=atr,
                description="Old low external sell-side liquidity",
            )
        )

    for point in high_points + low_points:
        level_type: LiquidityType = "external" if point["price"] >= range_high or point["price"] <= range_low else "internal"
        side = "buy-side" if point["price"] > price else "sell-side"
        levels.append(
            _make_level(
                closed_df,
                level_type,
                point["price"],
                1,
                point["source_index"],
                current_price=price,
                atr=atr,
                description=f"{level_type.title()} {side} liquidity",
            )
        )

    levels = sorted(levels, key=lambda level: (-level.strength, level.distance_atr, level.source_index))
    fresh_levels = [level for level in levels if not level.swept]
    buy_side = [level for level in fresh_levels if level.price > price]
    sell_side = [level for level in fresh_levels if level.price < price]

    nearest_buy_side = min(buy_side, key=lambda level: level.distance_atr, default=None)
    nearest_sell_side = min(sell_side, key=lambda level: level.distance_atr, default=None)
    strongest_buy_side = max(buy_side, key=lambda level: level.strength, default=None)
    strongest_sell_side = max(sell_side, key=lambda level: level.strength, default=None)

    return LiquidityMap(
        levels=levels,
        nearest_buy_side=nearest_buy_side,
        nearest_sell_side=nearest_sell_side,
        strongest_buy_side=strongest_buy_side,
        strongest_sell_side=strongest_sell_side,
    )


def _swing_points(swings: pd.DataFrame, price_column: str, df: pd.DataFrame) -> List[dict]:
    points = []
    if swings.empty or price_column not in swings.columns:
        return points

    for idx, row in swings.sort_index().iterrows():
        if idx not in df.index:
            continue
        points.append(
            {
                "price": _safe_float(row.get(price_column)),
                "source_index": int(df.index.get_loc(idx)),
                "index": idx,
            }
        )
    return points


def _build_equal_levels(
    df: pd.DataFrame,
    points: Sequence[dict],
    level_type: LiquidityType,
    tolerance: float,
    min_touches: int,
    current_price: float,
    atr: float,
    description: str,
) -> List[LiquidityLevel]:
    if len(points) < min_touches:
        return []

    groups: List[List[dict]] = []
    for point in sorted(points, key=lambda item: item["price"]):
        matched = False
        for group in groups:
            group_prices = [item["price"] for item in group]
            if abs(point["price"] - (sum(group_prices) / len(group_prices))) <= tolerance:
                group.append(point)
                matched = True
                break
        if not matched:
            groups.append([point])

    levels = []
    for group in groups:
        if len(group) < min_touches:
            continue
        price = sum(item["price"] for item in group) / len(group)
        source_index = max(item["source_index"] for item in group)
        levels.append(
            _make_level(
                df,
                level_type,
                price,
                len(group),
                source_index,
                current_price=current_price,
                atr=atr,
                description=description,
            )
        )
    return levels


def _make_level(
    df: pd.DataFrame,
    level_type: LiquidityType,
    price: float,
    touches: int,
    source_index: int,
    current_price: float,
    atr: float,
    description: str,
) -> LiquidityLevel:
    swept, swept_at = _sweep_state(df, level_type, price, source_index)
    age_bars = max((len(df) - 1) - source_index, 0)
    distance = abs(price - current_price)
    distance_percent = (distance / current_price) * 100 if current_price > 0 else 0.0
    distance_atr = distance / atr if atr > 0 else 0.0
    strength = _strength(level_type, touches, age_bars, distance_atr, swept)

    return LiquidityLevel(
        type=level_type,
        price=round(price, 8),
        strength=strength,
        touches=touches,
        age_bars=age_bars,
        distance_percent=round(distance_percent, 4),
        distance_atr=round(distance_atr, 4),
        swept=swept,
        swept_at=swept_at,
        source_index=source_index,
        description=description,
    )


def _sweep_state(df: pd.DataFrame, level_type: LiquidityType, price: float, source_index: int) -> tuple:
    future = df.iloc[source_index + 1 :]
    if future.empty:
        return False, None

    if _is_buy_side_level(level_type, price, _safe_float(df.iloc[-1].get("close"))):
        swept_mask = future["high"].astype(float) > price
    else:
        swept_mask = future["low"].astype(float) < price

    if not bool(swept_mask.any()):
        return False, None

    swept_label = swept_mask[swept_mask].index[0]
    return True, int(df.index.get_loc(swept_label))


def _is_buy_side_level(level_type: LiquidityType, price: float, current_price: float) -> bool:
    if level_type in ("equal_highs", "buy_side", "old_high"):
        return True
    if level_type in ("equal_lows", "sell_side", "old_low"):
        return False
    return price > current_price


def _old_extreme(points: Sequence[dict], side: str, min_age_bars: int, df_length: int) -> Optional[dict]:
    old_points = [
        point for point in points
        if ((df_length - 1) - point["source_index"]) >= min_age_bars
    ]
    if not old_points:
        return None

    if side == "high":
        price = max(point["price"] for point in old_points)
    else:
        price = min(point["price"] for point in old_points)

    touches = sum(1 for point in old_points if point["price"] == price)
    source_index = max(point["source_index"] for point in old_points if point["price"] == price)
    return {"price": price, "touches": touches, "source_index": source_index}


def _strength(level_type: LiquidityType, touches: int, age_bars: int, distance_atr: float, swept: bool) -> float:
    strength = 0.0
    strength += min(touches * 15, 45)

    if level_type in ("external", "old_high", "old_low"):
        strength += 20

    if age_bars < 20:
        strength += 10
    elif age_bars > 100:
        strength -= 10

    if distance_atr < 0.3:
        strength += 10

    if swept:
        strength -= 40

    return round(max(0.0, min(100.0, strength)), 2)


def _resolve_atr(df: pd.DataFrame, atr_series: Optional[pd.Series]) -> float:
    if atr_series is not None and not atr_series.empty:
        latest_atr = _safe_float(atr_series.dropna().iloc[-1] if not atr_series.dropna().empty else 0.0)
        if latest_atr > 0:
            return latest_atr

    if "atr" in df.columns and not df["atr"].dropna().empty:
        latest_atr = _safe_float(df["atr"].dropna().iloc[-1])
        if latest_atr > 0:
            return latest_atr

    avg_range = (df["high"].astype(float) - df["low"].astype(float)).tail(14).mean()
    return _safe_float(avg_range, 1.0) or 1.0


def _current_range(df: pd.DataFrame, lookback_bars: int) -> tuple:
    window = df.tail(lookback_bars)
    return _safe_float(window["high"].max()), _safe_float(window["low"].min())


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if pd.isna(value):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default
