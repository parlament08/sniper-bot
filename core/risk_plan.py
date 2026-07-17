from dataclasses import asdict, dataclass
from typing import Any, Dict, List, Literal, Optional


TradeDirection = Literal["LONG", "SHORT"]


@dataclass(frozen=True)
class RiskPlanConfig:
    atr_buffer: float = 0.1
    min_stop_distance_atr: float = 0.1
    min_rr_for_a_plus: float = 2.0
    min_rr_for_watchlist: float = 1.5
    max_entry_distance_from_poi_atr: float = 0.5
    max_stop_distance_percent: float = 6.0
    fallback_rr_target: float = 3.0


@dataclass(frozen=True)
class RiskPlan:
    direction: TradeDirection
    entry: Optional[float]
    stop_loss: Optional[float]
    invalidation_level: Optional[float]
    target_1: Optional[float]
    target_2: Optional[float]
    risk_per_unit: Optional[float]
    rr_to_target_1: Optional[float]
    rr_to_target_2: Optional[float]
    stop_distance_percent: Optional[float]
    entry_distance_from_poi_atr: Optional[float]
    valid: bool
    reason: str
    entry_model: str
    stop_model: str
    target_model: str
    late_entry: bool = False
    risk_plan_status: str = "execution_plan"
    preliminary_risk: Optional[Dict[str, Any]] = None
    source_candidate_id: Optional[str] = None
    nearest_obstacle: Optional[Dict[str, Any]] = None
    target_1_info: Optional[Dict[str, Any]] = None
    target_2_info: Optional[Dict[str, Any]] = None
    alternative_targets: Optional[List[Dict[str, Any]]] = None
    risk_geometry: str = "ok"
    poi_price: Optional[float] = None
    current_price_distance_from_poi_atr: Optional[float] = None
    max_entry_distance_from_poi_atr: Optional[float] = None
    minimum_stop_distance_atr: Optional[float] = None
    minimum_stop_distance_percent: Optional[float] = None

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
    source_candidate_id: Optional[str] = None,
    candidate_fvg_created: Optional[bool] = None,
    candidate_fvg_retested: Optional[bool] = None,
    post_retest_displacement_confirmed: Optional[bool] = None,
) -> Optional[RiskPlan]:
    config = config or RiskPlanConfig()
    if direction not in ("LONG", "SHORT") or current_price <= 0:
        return None

    atr = float(atr or 0.0)
    if atr <= 0:
        return None

    if candidate_fvg_created is False:
        return _not_available_plan(
            direction,
            reason="candidate_fvg_not_created",
            preliminary_risk=_preliminary_risk(direction, liquidity_map),
            source_candidate_id=source_candidate_id,
        )

    active_fvg = _select_active_fvg(direction, fvg_data or [], fvg_test_data)
    if active_fvg is None:
        return _not_available_plan(
            direction,
            reason="entry_model_not_formed",
            preliminary_risk=_preliminary_risk(direction, liquidity_map),
            source_candidate_id=source_candidate_id,
        )

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
        return _not_available_plan(
            direction,
            reason="entry_not_available",
            preliminary_risk=_preliminary_risk(direction, liquidity_map),
            source_candidate_id=source_candidate_id,
        )

    entry_distance_from_poi_atr = abs(entry - poi_price) / atr if poi_price is not None else None
    current_distance_from_poi_atr = abs(current_price - poi_price) / atr if poi_price is not None else None
    late_entry = current_distance_from_poi_atr is not None and current_distance_from_poi_atr > config.max_entry_distance_from_poi_atr

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
            source_candidate_id=source_candidate_id,
        )

    (
        target_1,
        target_2,
        target_model,
        nearest_obstacle,
        target_1_info,
        target_2_info,
        alternative_targets,
        risk_geometry,
    ) = _select_targets(direction, entry, risk_per_unit, liquidity_map, config)
    rr_to_target_1 = _rr(direction, entry, target_1, risk_per_unit)
    rr_to_target_2 = _rr(direction, entry, target_2, risk_per_unit) if target_2 is not None else None
    stop_distance_percent = (risk_per_unit / entry) * 100 if entry > 0 else 0.0
    minimum_stop_distance_percent = ((config.min_stop_distance_atr * atr) / entry) * 100 if entry > 0 else None

    valid = True
    reasons = []
    if late_entry:
        valid = False
        reasons.append("late entry: price moved too far from POI")
    if stop_distance_percent > config.max_stop_distance_percent:
        valid = False
        reasons.append("stop distance too wide")
    if risk_geometry == "blocked_by_near_obstacle":
        valid = False
        reasons.append("blocked_by_near_obstacle")
    if target_model == "none":
        valid = False
        reasons.append("no valid liquidity target")
    if target_1 is not None and rr_to_target_1 < config.min_rr_for_watchlist:
        valid = False
        reasons.append("RR to target 1 below minimum")
    elif target_1 is not None and rr_to_target_1 < config.min_rr_for_a_plus:
        valid = False
        reasons.append("RR to target 1 is Watchlist only")
    if not reasons:
        reasons.append("Risk plan valid")

    risk_plan_status = "execution_plan" if active_fvg.get("tested", bool(fvg_test_data)) else "tentative_plan"
    if candidate_fvg_retested is False:
        risk_plan_status = "tentative_plan"
    if post_retest_displacement_confirmed is False:
        risk_plan_status = "tentative_plan"

    return RiskPlan(
        direction=direction,
        entry=round(entry, 8),
        stop_loss=round(stop_loss, 8),
        invalidation_level=round(invalidation_level, 8),
        target_1=round(target_1, 8) if target_1 is not None else None,
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
        risk_plan_status=risk_plan_status,
        source_candidate_id=source_candidate_id,
        nearest_obstacle=nearest_obstacle,
        target_1_info=target_1_info,
        target_2_info=target_2_info,
        alternative_targets=alternative_targets,
        risk_geometry=risk_geometry,
        poi_price=round(poi_price, 8) if poi_price is not None else None,
        current_price_distance_from_poi_atr=round(current_distance_from_poi_atr, 4) if current_distance_from_poi_atr is not None else None,
        max_entry_distance_from_poi_atr=config.max_entry_distance_from_poi_atr,
        minimum_stop_distance_atr=config.min_stop_distance_atr,
        minimum_stop_distance_percent=round(minimum_stop_distance_percent, 4) if minimum_stop_distance_percent is not None else None,
    )


def _select_active_fvg(direction: TradeDirection, fvg_data: List[Dict[str, Any]], fvg_test_data: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    target_type = "bullish" if direction == "LONG" else "bearish"
    candidates = [
        fvg for fvg in fvg_data
        if fvg.get("type") == target_type
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
        return midpoint, "fvg_midpoint", midpoint

    return None, "entry_not_available", None


def _select_stop(direction, entry, atr, active_fvg, sfp_data, structure_data, config):
    buffer = max(atr * config.atr_buffer, atr * config.min_stop_distance_atr)
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

    raw_candidates = [item for item in (nearest, strongest) if item is not None]
    valid_targets = []
    nearest_obstacle = None
    risk_geometry = "ok"

    for level in raw_candidates:
        price = _get(level, "price")
        if not _target_is_valid(direction, entry, price):
            continue
        if bool(_get(level, "swept", False)):
            continue
        snapshot = _target_snapshot(level, direction, entry, risk_per_unit)
        if snapshot["rr"] < config.min_rr_for_watchlist:
            if nearest_obstacle is None:
                nearest_obstacle = snapshot
                risk_geometry = "blocked_by_near_obstacle"
            continue
        valid_targets.append(snapshot)

    deduped_targets = []
    seen_prices = set()
    for target in sorted(valid_targets, key=lambda item: item["rr"]):
        price = target.get("price")
        if price in seen_prices:
            continue
        seen_prices.add(price)
        deduped_targets.append(target)

    if not deduped_targets:
        return None, None, "none", nearest_obstacle, None, None, [], risk_geometry if nearest_obstacle else "no_valid_target"

    target_1_info = deduped_targets[0]
    target_2_info = deduped_targets[1] if len(deduped_targets) > 1 else None
    return (
        float(target_1_info["price"]),
        float(target_2_info["price"]) if target_2_info else None,
        "valid_liquidity_target",
        nearest_obstacle,
        target_1_info,
        target_2_info,
        deduped_targets[2:],
        risk_geometry,
    )


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


def _not_available_plan(direction, reason, preliminary_risk=None, source_candidate_id=None):
    return RiskPlan(
        direction=direction,
        entry=None,
        stop_loss=None,
        invalidation_level=None,
        target_1=None,
        target_2=None,
        risk_per_unit=None,
        rr_to_target_1=None,
        rr_to_target_2=None,
        stop_distance_percent=None,
        entry_distance_from_poi_atr=None,
        valid=False,
        reason=reason,
        entry_model="entry_not_available",
        stop_model="none",
        target_model="none",
        late_entry=False,
        risk_plan_status="not_available",
        preliminary_risk=preliminary_risk,
        source_candidate_id=source_candidate_id,
        nearest_obstacle=None,
        target_1_info=None,
        target_2_info=None,
        alternative_targets=[],
        risk_geometry="not_available",
    )


def _preliminary_risk(direction, liquidity_map):
    nearest_key = "nearest_buy_side" if direction == "LONG" else "nearest_sell_side"
    strongest_key = "strongest_buy_side" if direction == "LONG" else "strongest_sell_side"
    nearest = _get(liquidity_map, nearest_key)
    strongest = _get(liquidity_map, strongest_key)
    return {
        "nearest_obstacle": _liquidity_snapshot(nearest),
        "potential_target": _liquidity_snapshot(strongest or nearest),
        "feasible": None,
    }


def _liquidity_snapshot(level):
    if level is None:
        return None
    if hasattr(level, "to_dict"):
        return level.to_dict()
    if hasattr(level, "__dict__"):
        return dict(level.__dict__)
    if hasattr(level, "get"):
        return dict(level)
    return level


def _target_snapshot(level, direction, entry, risk_per_unit):
    snapshot = _liquidity_snapshot(level) or {}
    if not isinstance(snapshot, dict):
        snapshot = {"price": _get(level, "price")}
    price = float(snapshot.get("price"))
    snapshot.update({
        "price": price,
        "direction": direction,
        "distance": round(abs(price - entry), 8),
        "rr": round(_rr(direction, entry, price, risk_per_unit), 4),
        "type": snapshot.get("type"),
        "strength": snapshot.get("strength"),
        "freshness": snapshot.get("age_bars"),
    })
    return snapshot


def _invalid_plan(direction, entry, stop_loss, invalidation_level, target_1, target_2, risk_per_unit, stop_distance_percent, entry_distance_from_poi_atr, reason, entry_model, stop_model, target_model, late_entry, source_candidate_id=None):
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
        risk_plan_status="invalid",
        source_candidate_id=source_candidate_id,
        nearest_obstacle=None,
        target_1_info=None,
        target_2_info=None,
        alternative_targets=[],
        risk_geometry="invalid",
    )
