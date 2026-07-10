from dataclasses import asdict, dataclass
from typing import Any, Dict, List, Literal, Optional


TradeDirection = Literal["LONG", "SHORT"]


@dataclass(frozen=True)
class RiskPlanConfig:
    atr_buffer: float = 0.1
    min_rr_for_a_plus: float = 2.0
    min_rr_for_watchlist: float = 1.5
    max_entry_distance_from_poi_atr: float = 0.5
    max_stop_distance_percent: float = 6.0
    fallback_rr_target: float = 3.0


@dataclass(frozen=True)
class RiskPlan:
    direction: TradeDirection
    entry: float
    stop_loss: float
    invalidation_level: float
    target_1: float
    target_2: Optional[float]
    risk_per_unit: float
    rr_to_target_1: float
    rr_to_target_2: Optional[float]
    stop_distance_percent: float
    entry_distance_from_poi_atr: float
    valid: bool
    reason: str
    entry_model: str
    stop_model: str
    target_model: str
    late_entry: bool = False

    def get(self, key: str, default: Any = None) -> Any:
        return asdict(self).get(key, default)

    def __getitem__(self, key: str) -> Any:
        data = asdict(self)
        if key not in data:
            raise KeyError(key)
        return data[key]

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def build_risk_plan(
    *,
    direction: TradeDirection,
    current_price: float,
    atr: float,
    liquidity_map: Optional[object] = None,
    fvg_data: Optional[List[Dict[str, Any]]] = None,
    fvg_test_data: Optional[Dict[str, Any]] = None,
    sfp_data: Optional[Dict[str, Any]] = None,
    structure_data: Optional[Dict[str, Any]] = None,
    config: Optional[RiskPlanConfig] = None,
) -> Optional[RiskPlan]:
    config = config or RiskPlanConfig()
    if direction not in ("LONG", "SHORT") or current_price <= 0:
        return None

    atr = float(atr or 0.0)
    if atr <= 0:
        return None

    active_fvg = _select_active_fvg(direction, fvg_data or [], fvg_test_data)
    entry, entry_model, poi_price = _select_entry(
        direction,
        current_price,
        atr,
        active_fvg,
        sfp_data,
        structure_data,
        config,
    )
    if entry is None:
        return None

    entry_distance_from_poi_atr = abs(entry - poi_price) / atr if poi_price is not None else 999.0
    current_distance_from_poi_atr = abs(current_price - poi_price) / atr if poi_price is not None else entry_distance_from_poi_atr
    late_entry = current_distance_from_poi_atr > config.max_entry_distance_from_poi_atr

    invalidation_level, stop_loss, stop_model = _select_stop(
        direction,
        entry,
        atr,
        active_fvg,
        sfp_data,
        structure_data,
        config,
    )
    risk_per_unit = _risk_per_unit(direction, entry, stop_loss)
    if risk_per_unit <= 0:
        return _invalid_plan(
            direction,
            entry,
            stop_loss,
            invalidation_level,
            target_1=entry,
            target_2=None,
            risk_per_unit=0.0,
            stop_distance_percent=0.0,
            entry_distance_from_poi_atr=entry_distance_from_poi_atr,
            reason="Invalid stop placement",
            entry_model=entry_model,
            stop_model=stop_model,
            target_model="none",
            late_entry=late_entry,
        )

    target_1, target_2, target_model = _select_targets(direction, entry, risk_per_unit, liquidity_map, config)
    rr_to_target_1 = _rr(direction, entry, target_1, risk_per_unit)
    rr_to_target_2 = _rr(direction, entry, target_2, risk_per_unit) if target_2 is not None else None
    stop_distance_percent = (risk_per_unit / entry) * 100 if entry > 0 else 0.0

    valid = True
    reasons = []
    if late_entry:
        valid = False
        reasons.append("late entry: price moved too far from POI")
    if stop_distance_percent > config.max_stop_distance_percent:
        valid = False
        reasons.append("stop distance too wide")
    if target_model == "3R_fallback_no_liquidity":
        valid = False
        reasons.append("no logical liquidity target")
    if rr_to_target_1 < config.min_rr_for_watchlist:
        valid = False
        reasons.append("RR to target 1 below minimum")
    elif rr_to_target_1 < config.min_rr_for_a_plus:
        valid = False
        reasons.append("RR to target 1 is Watchlist only")
    if not reasons:
        reasons.append("Risk plan valid")

    return RiskPlan(
        direction=direction,
        entry=round(entry, 8),
        stop_loss=round(stop_loss, 8),
        invalidation_level=round(invalidation_level, 8),
        target_1=round(target_1, 8),
        target_2=round(target_2, 8) if target_2 is not None else None,
        risk_per_unit=round(risk_per_unit, 8),
        rr_to_target_1=round(rr_to_target_1, 4),
        rr_to_target_2=round(rr_to_target_2, 4) if rr_to_target_2 is not None else None,
        stop_distance_percent=round(stop_distance_percent, 4),
        entry_distance_from_poi_atr=round(entry_distance_from_poi_atr, 4),
        valid=valid,
        reason="; ".join(reasons),
        entry_model=entry_model,
        stop_model=stop_model,
        target_model=target_model,
        late_entry=late_entry,
    )


def _select_active_fvg(direction: TradeDirection, fvg_data: List[Dict[str, Any]], fvg_test_data: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    target_type = "bullish" if direction == "LONG" else "bearish"
    candidates = [
        fvg for fvg in fvg_data
        if fvg.get("type") == target_type
        and fvg.get("tested", bool(fvg_test_data))
        and not fvg.get("invalidated", False)
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda item: item.get("end_index", -1))


def _select_entry(direction, current_price, atr, active_fvg, sfp_data, structure_data, config):
    if active_fvg:
        midpoint = _midpoint(active_fvg)
        distance_to_midpoint = abs(current_price - midpoint) / atr
        if distance_to_midpoint <= config.max_entry_distance_from_poi_atr:
            return midpoint, "fvg_midpoint", midpoint

        if direction == "LONG" and current_price <= float(active_fvg.get("top", midpoint)) + (atr * config.max_entry_distance_from_poi_atr):
            return current_price, "confirmation_close", midpoint
        if direction == "SHORT" and current_price >= float(active_fvg.get("bottom", midpoint)) - (atr * config.max_entry_distance_from_poi_atr):
            return current_price, "confirmation_close", midpoint

    if sfp_data and sfp_data.get("level") is not None:
        return float(sfp_data["level"]), "reclaim_level_after_sweep", float(sfp_data["level"])

    if structure_data and structure_data.get("level") is not None:
        return float(structure_data["level"]), "structure_level_fallback", None

    return current_price, "confirmation_close_fallback", None


def _select_stop(direction, entry, atr, active_fvg, sfp_data, structure_data, config):
    buffer = atr * config.atr_buffer
    candidates = []
    labels = []

    if sfp_data and sfp_data.get("level") is not None:
        candidates.append(float(sfp_data["level"]))
        labels.append("sweep_level")
    if active_fvg:
        fvg_level = float(active_fvg.get("bottom" if direction == "LONG" else "top"))
        candidates.append(fvg_level)
        labels.append("fvg_invalidation")
    if structure_data and structure_data.get("level") is not None:
        candidates.append(float(structure_data["level"]))
        labels.append("structure_level")

    if direction == "LONG":
        invalidation = min(candidates) if candidates else entry - (2 * atr)
        stop_loss = invalidation - buffer
    else:
        invalidation = max(candidates) if candidates else entry + (2 * atr)
        stop_loss = invalidation + buffer

    stop_model = "structural_invalidation" if candidates else "atr_fallback"
    if labels:
        stop_model = f"{stop_model}:{'/'.join(labels)}"
    return invalidation, stop_loss, stop_model


def _select_targets(direction, entry, risk_per_unit, liquidity_map, config):
    nearest_key = "nearest_buy_side" if direction == "LONG" else "nearest_sell_side"
    strongest_key = "strongest_buy_side" if direction == "LONG" else "strongest_sell_side"
    nearest = _get(liquidity_map, nearest_key)
    strongest = _get(liquidity_map, strongest_key)

    target_1 = _get(nearest, "price")
    target_2 = _get(strongest, "price")
    if _target_is_valid(direction, entry, target_1):
        if not _target_is_valid(direction, entry, target_2) or target_2 == target_1:
            target_2 = None
        return float(target_1), float(target_2) if target_2 is not None else None, "nearest_liquidity"

    fallback = entry + (risk_per_unit * config.fallback_rr_target) if direction == "LONG" else entry - (risk_per_unit * config.fallback_rr_target)
    return fallback, None, "3R_fallback_no_liquidity"


def _target_is_valid(direction, entry, target):
    if target is None:
        return False
    target = float(target)
    return target > entry if direction == "LONG" else target < entry


def _risk_per_unit(direction, entry, stop_loss):
    return entry - stop_loss if direction == "LONG" else stop_loss - entry


def _rr(direction, entry, target, risk_per_unit):
    if target is None or risk_per_unit <= 0:
        return 0.0
    reward = float(target) - entry if direction == "LONG" else entry - float(target)
    return max(reward / risk_per_unit, 0.0)


def _midpoint(fvg: Dict[str, Any]) -> float:
    return (float(fvg["top"]) + float(fvg["bottom"])) / 2


def _get(source: Optional[object], key: str, default: Any = None) -> Any:
    if source is None:
        return default
    if hasattr(source, "get"):
        return source.get(key, default)
    return getattr(source, key, default)


def _invalid_plan(direction, entry, stop_loss, invalidation_level, target_1, target_2, risk_per_unit, stop_distance_percent, entry_distance_from_poi_atr, reason, entry_model, stop_model, target_model, late_entry):
    return RiskPlan(
        direction=direction,
        entry=round(entry, 8),
        stop_loss=round(stop_loss, 8),
        invalidation_level=round(invalidation_level, 8),
        target_1=round(target_1, 8),
        target_2=round(target_2, 8) if target_2 is not None else None,
        risk_per_unit=round(risk_per_unit, 8),
        rr_to_target_1=0.0,
        rr_to_target_2=None,
        stop_distance_percent=round(stop_distance_percent, 4),
        entry_distance_from_poi_atr=round(entry_distance_from_poi_atr, 4),
        valid=False,
        reason=reason,
        entry_model=entry_model,
        stop_model=stop_model,
        target_model=target_model,
        late_entry=late_entry,
    )
