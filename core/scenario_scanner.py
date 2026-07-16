from dataclasses import asdict, dataclass, field
from typing import Any, Literal, Optional

import pandas as pd

from core.payload import payload_to_dict


ScenarioDirection = Literal["LONG", "SHORT"]
ScenarioStatus = Literal[
    "not_started",
    "building",
    "waiting_for_confirmation",
    "complete",
    "invalidated",
]


@dataclass
class ScenarioEvent:
    event_type: str
    direction: Optional[str]
    index: Any
    quality_score: Optional[float] = None
    source: Optional[str] = None
    payload: Optional[dict] = None

    def to_dict(self) -> dict:
        data = asdict(self)
        if data["index"] is not None:
            data["index"] = str(data["index"])
        return data


@dataclass
class ScenarioScanResult:
    direction: ScenarioDirection
    status: ScenarioStatus
    current_step: str
    next_expected_step: Optional[str]
    signal_allowed: bool
    scenario_valid: bool
    completion_ratio: float
    completed_steps: int
    total_steps: int
    quality_score: float
    events_used: list[ScenarioEvent] = field(default_factory=list)
    missing_steps: list[str] = field(default_factory=list)
    invalidated_reason: Optional[str] = None
    last_invalidated_component: Optional[str] = None
    waiting_for: Optional[str] = None
    anchor_index: Optional[Any] = None
    last_event_index: Optional[Any] = None
    risk_valid: bool = False
    risk_reason: Optional[str] = None
    candidate_id: Optional[str] = None
    candidate_created_at: Optional[Any] = None
    candidate_updated_at: Optional[Any] = None
    anchor_type: Optional[str] = None
    anchor_first_touch_index: Optional[Any] = None
    anchor_last_touch_index: Optional[Any] = None
    age_bars: int = 0
    update_count: int = 0
    trigger_scan: Optional[dict] = None
    is_selected: bool = False
    rank: Optional[int] = None
    progress_rank: Optional[int] = None
    selection_eligible: bool = False
    selection_rejected_reason: Optional[str] = None
    opposite_trigger_detected: bool = False
    opposite_trigger_type: Optional[str] = None
    opposite_trigger_quality: Optional[float] = None
    opposite_trigger_index: Optional[Any] = None
    candidate_invalidated: bool = False
    scenario_key: Optional[object] = None
    market_age_bars: int = 0
    runtime_update_count: int = 0

    def to_dict(self) -> dict:
        data = asdict(self)
        data["events_used"] = [event.to_dict() if hasattr(event, "to_dict") else event for event in self.events_used]
        if data["anchor_index"] is not None:
            data["anchor_index"] = str(data["anchor_index"])
        if data["last_event_index"] is not None:
            data["last_event_index"] = str(data["last_event_index"])
        for key in ("candidate_created_at", "candidate_updated_at", "anchor_first_touch_index", "anchor_last_touch_index"):
            if data[key] is not None:
                data[key] = str(data[key])
        if data["opposite_trigger_index"] is not None:
            data["opposite_trigger_index"] = str(data["opposite_trigger_index"])
        if data["scenario_key"] is not None:
            data["scenario_key"] = _scenario_key_snapshot(data["scenario_key"])
        return data


@dataclass
class ScenarioScannerOutput:
    best_long_scenario: Optional[ScenarioScanResult]
    best_short_scenario: Optional[ScenarioScanResult]
    selected_scenario: Optional[ScenarioScanResult]
    selected_direction: Optional[str]
    signal_allowed: bool
    scenario_valid: bool
    reason: str
    long_candidates: list[ScenarioScanResult] = field(default_factory=list)
    short_candidates: list[ScenarioScanResult] = field(default_factory=list)
    top_candidates: list[ScenarioScanResult] = field(default_factory=list)
    candidate_counts: dict = field(default_factory=dict)
    selected_scenario_id: Optional[str] = None
    direction_block_reasons: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "long_candidates": [item.to_dict() for item in self.long_candidates],
            "short_candidates": [item.to_dict() for item in self.short_candidates],
            "best_long_scenario": self.best_long_scenario.to_dict() if self.best_long_scenario else None,
            "best_short_scenario": self.best_short_scenario.to_dict() if self.best_short_scenario else None,
            "selected_scenario": self.selected_scenario.to_dict() if self.selected_scenario else None,
            "selected_scenario_id": self.selected_scenario_id,
            "selected_direction": self.selected_direction,
            "signal_allowed": self.signal_allowed,
            "scenario_valid": self.scenario_valid,
            "reason": self.reason,
            "top_candidates": [_candidate_summary(item) for item in self.top_candidates],
            "candidate_counts": dict(self.candidate_counts),
            "direction_block_reasons": dict(self.direction_block_reasons),
        }


FLOW = (
    "HTF_CONTEXT_CONFIRMED",
    "POI_TOUCHED",
    "SFP_CONFIRMED",
    "EARLY_TRIGGER_CONFIRMED",
    "CONFIRMED_TRIGGER_CONFIRMED",
    "FVG_CREATED",
    "FVG_RETESTED",
    "DISPLACEMENT_CONFIRMED",
    "RISK_VALID",
    "SIGNAL_ALLOWED",
)

STEP_LABELS = {
    "HTF_CONTEXT_CONFIRMED": "htf_context_confirmed",
    "PD_LOCATION_VALID": "pd_location_valid",
    "POI_IDENTIFIED": "poi_identified",
    "POI_TOUCHED": "poi_touched",
    "SFP_CONFIRMED": "liquidity_sweep_confirmed",
    "EARLY_TRIGGER_CONFIRMED": "early_trigger_confirmed",
    "CONFIRMED_TRIGGER_CONFIRMED": "confirmed_trigger_confirmed",
    "CHOCH_CONFIRMED": "choch_confirmed",
    "BOS_CONFIRMED": "bos_confirmed",
    "FVG_CREATED": "fvg_created",
    "FVG_RETESTED": "fvg_retested",
    "DISPLACEMENT_CONFIRMED": "displacement_confirmed",
    "RISK_VALID": "risk_valid",
    "SIGNAL_ALLOWED": "signal_allowed",
}

WAITING_TEXT = {
    "POI_TOUCHED": "waiting_for_poi",
    "SFP_CONFIRMED": "liquidity sweep / SFP",
    "EARLY_TRIGGER_CONFIRMED": "bullish CHOCH/BOS after SFP",
    "CONFIRMED_TRIGGER_CONFIRMED": "confirmed bullish BOS after early CHOCH",
    "FVG_CREATED": "bullish FVG",
    "FVG_RETESTED": "bullish FVG retest",
    "DISPLACEMENT_CONFIRMED": "bullish displacement",
    "RISK_VALID": "risk plan",
}

CONFIRMED_TRIGGER_MIN_QUALITY = 70


def scan_scenarios(
    *,
    events: list[ScenarioEvent],
    expected_direction: Optional[str] = None,
    htf_structure: Optional[object] = None,
    premium_discount: Optional[object] = None,
    risk_plan: Optional[object] = None,
    strict_bos_after_choch: bool = True,
) -> ScenarioScannerOutput:
    normalized_events = sorted([_as_event(event) for event in events or []], key=lambda item: _event_sort_key(item.index))
    htf_trend = _htf_trend(htf_structure, normalized_events)
    if htf_trend == "neutral":
        return ScenarioScannerOutput(
            best_long_scenario=None,
            best_short_scenario=None,
            selected_scenario=None,
            selected_direction=None,
            signal_allowed=False,
            scenario_valid=False,
            reason="htf_neutral_no_scenario",
            long_candidates=[],
            short_candidates=[],
            top_candidates=[],
            candidate_counts=_candidate_counts([], []),
            direction_block_reasons={},
        )

    direction_block_reasons = {
        direction: reason
        for direction, reason in {
            "LONG": _direction_block_reason("LONG", htf_trend, premium_discount),
            "SHORT": _direction_block_reason("SHORT", htf_trend, premium_discount),
        }.items()
        if reason
    }
    long_candidates = _scan_direction_candidates(
        "LONG",
        normalized_events,
        htf_trend=htf_trend,
        premium_discount=premium_discount,
        risk_plan=risk_plan,
        strict_bos_after_choch=strict_bos_after_choch,
    )
    short_candidates = _scan_direction_candidates(
        "SHORT",
        normalized_events,
        htf_trend=htf_trend,
        premium_discount=premium_discount,
        risk_plan=risk_plan,
        strict_bos_after_choch=strict_bos_after_choch,
    )
    all_candidates = long_candidates + short_candidates
    ranked_candidates = sorted(
        all_candidates,
        key=lambda item: _selection_rank(item, expected_direction, htf_trend),
        reverse=True,
    )
    for rank, candidate in enumerate(ranked_candidates, start=1):
        candidate.rank = rank
        candidate.progress_rank = rank
        candidate.selection_eligible = _is_living(candidate)
        candidate.selection_rejected_reason = None if candidate.selection_eligible else _selection_rejected_reason(candidate)

    best_long = _best_candidate(long_candidates, expected_direction, htf_trend)
    best_short = _best_candidate(short_candidates, expected_direction, htf_trend)
    living_candidates = [item for item in all_candidates if _is_living(item)]

    if not living_candidates:
        expected_invalid = _expected_scenario(expected_direction, best_long, best_short)
        expected_block_reason = _expected_block_reason(expected_direction, direction_block_reasons)
        return ScenarioScannerOutput(
            best_long_scenario=best_long,
            best_short_scenario=best_short,
            selected_scenario=None,
            selected_direction=None,
            signal_allowed=False,
            scenario_valid=False,
            reason=(
                expected_invalid.invalidated_reason
                if expected_invalid and expected_invalid.invalidated_reason
                else expected_block_reason or "no_valid_scenario"
            ),
            long_candidates=long_candidates,
            short_candidates=short_candidates,
            top_candidates=ranked_candidates[:5],
            candidate_counts=_candidate_counts(long_candidates, short_candidates),
            direction_block_reasons=direction_block_reasons,
        )

    selected = max(living_candidates, key=lambda item: _selection_rank(item, expected_direction, htf_trend))
    selected.is_selected = True
    return ScenarioScannerOutput(
        best_long_scenario=best_long,
        best_short_scenario=best_short,
        selected_scenario=selected,
        selected_direction=selected.direction,
        signal_allowed=selected.signal_allowed,
        scenario_valid=selected.scenario_valid,
        reason=_output_reason(selected),
        long_candidates=long_candidates,
        short_candidates=short_candidates,
        top_candidates=ranked_candidates[:5],
        candidate_counts=_candidate_counts(long_candidates, short_candidates),
        selected_scenario_id=selected.candidate_id,
        direction_block_reasons=direction_block_reasons,
    )


def _scan_direction_candidates(
    direction: ScenarioDirection,
    events: list[ScenarioEvent],
    *,
    htf_trend: Optional[str],
    premium_discount: Optional[object],
    risk_plan: Optional[object],
    strict_bos_after_choch: bool,
) -> list[ScenarioScanResult]:
    hard_invalid_reason = _direction_block_reason(direction, htf_trend, premium_discount)
    if hard_invalid_reason:
        return []

    anchors = _candidate_anchors(direction, events)
    if not anchors:
        return []

    candidates = []
    for sequence, anchor in enumerate(anchors, start=1):
        candidates.append(
            _scan_direction(
                direction,
                _events_for_candidate(direction, events, anchor),
                htf_trend=htf_trend,
                premium_discount=None,
                risk_plan=risk_plan,
                strict_bos_after_choch=strict_bos_after_choch,
                anchor_event=anchor,
                candidate_sequence=sequence,
            )
        )
    return candidates


def _scan_direction(
    direction: ScenarioDirection,
    events: list[ScenarioEvent],
    *,
    htf_trend: Optional[str],
    premium_discount: Optional[object],
    risk_plan: Optional[object],
    strict_bos_after_choch: bool,
    anchor_event: Optional[ScenarioEvent] = None,
    candidate_sequence: int = 1,
) -> ScenarioScanResult:
    scenario_side = _direction_side(direction)
    opposite_side = _opposite_side(direction)
    anchor_type = _normalize_event_type(anchor_event.event_type) if anchor_event else None
    anchor_index = anchor_event.index if anchor_event else None
    expected_candidate_id = _candidate_id_for_anchor(direction, anchor_event, candidate_sequence)

    if htf_trend in ("bullish", "bearish") and htf_trend != scenario_side:
        return _finalize_candidate(_invalid_result(direction, "htf_direction_conflict"), anchor_event, candidate_sequence)

    if premium_discount is not None and not _pd_valid_for_direction(direction, premium_discount):
        return _finalize_candidate(_invalid_result(direction, "pd_invalid_for_direction"), anchor_event, candidate_sequence)

    used: list[ScenarioEvent] = []
    completed = 0
    sfp_index = None
    has_early_trigger_event = any(
        _normalize_event_type(event.event_type) == "EARLY_TRIGGER_CONFIRMED"
        and _normalize_side(event.direction) == scenario_side
        for event in events
    )
    early_trigger_seen = False
    early_trigger_index = None
    confirmed_trigger_seen = False
    confirmed_trigger_index = None
    confirmed_trigger_id = None
    opposite_trigger_event = None
    rejected_confirmed_candidates = []
    fvg_seen = False
    fvg_created_index = None
    retest_seen = False
    retest_index = None
    risk_valid = False
    risk_reason = None
    risk_event_seen = False
    last_invalidated_component = None

    for event in events:
        event_type = _normalize_event_type(event.event_type)
        side = _normalize_side(event.direction)
        if event_type == "INVALIDATION" and _event_applies(direction, event):
            return _finalize_candidate(_invalid_result(direction, _payload_reason(event, "invalidation"), used), anchor_event, candidate_sequence)

        if side not in (None, scenario_side, opposite_side, "neutral"):
            continue

        if event_type == "HTF_CONTEXT_CONFIRMED":
            if side == "neutral":
                return _finalize_candidate(_invalid_result(direction, "htf_direction_conflict", used), anchor_event, candidate_sequence)
            if side == scenario_side and completed < 1:
                used.append(event)
                completed = max(completed, 1)
            elif side == opposite_side:
                return _finalize_candidate(_invalid_result(direction, "htf_direction_conflict", used), anchor_event, candidate_sequence)
            continue

        if completed == 0:
            continue

        if event_type == "PD_LOCATION_VALID":
            if side in (None, scenario_side) and completed < 2:
                used.append(event)
                completed = max(completed, 2)
            continue

        if event_type == "POI_TOUCHED":
            if side in (None, scenario_side) and completed < 2:
                used.append(event)
                completed = max(completed, 2)
            continue

        if event_type in ("SFP_CONFIRMED", "LIQUIDITY_SWEEP_CONFIRMED"):
            if side == scenario_side and completed < 3:
                used.append(_with_type(event, "SFP_CONFIRMED"))
                completed = max(completed, 3)
                sfp_index = event.index
            continue

        if event_type in ("EARLY_TRIGGER_CONFIRMED", "CHOCH_CONFIRMED", "BOS_CONFIRMED", "CONFIRMED_TRIGGER_CONFIRMED") and side == scenario_side and completed < 3:
            last_invalidated_component = "trigger_before_sfp"
            continue

        if event_type == "EARLY_TRIGGER_CONFIRMED" and side == opposite_side and completed >= 3:
            opposite_trigger_event = event
            continue

        if event_type in ("CHOCH_CONFIRMED", "BOS_CONFIRMED", "CONFIRMED_TRIGGER_CONFIRMED") and side == opposite_side and completed >= 3:
            if not _event_candidate_matches(event, expected_candidate_id):
                continue
            opposite_trigger_event = event
            if _is_confirmed_bos_event(event) and float(event.quality_score or 0.0) >= CONFIRMED_TRIGGER_MIN_QUALITY:
                return _finalize_candidate(_invalid_result(
                    direction,
                    "opposite_confirmed_bos",
                    used + [event],
                    opposite_trigger_event=event,
                    last_invalidated_component="opposite_confirmed_bos",
                ), anchor_event, candidate_sequence)
            rejected_confirmed_candidates.append(_rejected_confirmed_candidate(event, "opposite_structure_warning"))
            continue

        if event_type == "EARLY_TRIGGER_CONFIRMED" and side == scenario_side:
            if completed < 4:
                used.append(_with_candidate_payload(event, expected_candidate_id, anchor_index=anchor_index))
                completed = max(completed, 4)
            early_trigger_seen = True
            early_trigger_index = event.index
            continue

        if event_type in ("CHOCH_CONFIRMED", "BOS_CONFIRMED", "CONFIRMED_TRIGGER_CONFIRMED") and side == scenario_side:
            if has_early_trigger_event and not early_trigger_seen:
                last_invalidated_component = "confirmed_trigger_before_early"
                rejected_confirmed_candidates.append(_rejected_confirmed_candidate(event, "before_early_trigger"))
                continue
            if has_early_trigger_event and float(event.quality_score or 0.0) < CONFIRMED_TRIGGER_MIN_QUALITY:
                last_invalidated_component = "confirmed_trigger_quality_below_min"
                rejected_confirmed_candidates.append(_rejected_confirmed_candidate(event, "quality_below_min"))
                continue
            if completed < 5:
                used.append(_with_type(_with_candidate_payload(
                    event,
                    expected_candidate_id,
                    anchor_index=anchor_index,
                    early_trigger_index=early_trigger_index,
                ), "CONFIRMED_TRIGGER_CONFIRMED"))
                completed = max(completed, 5)
            confirmed_trigger_seen = True
            confirmed_trigger_index = event.index
            confirmed_trigger_id = _event_id(event)
            continue

        if event_type == "FVG_CREATED" and side == scenario_side:
            if not confirmed_trigger_seen:
                continue
            if _event_sort_key(event.index) <= _event_sort_key(confirmed_trigger_index):
                last_invalidated_component = "fvg_before_bos"
                continue
            if not _fvg_event_matches_candidate(event, expected_candidate_id, confirmed_trigger_id):
                continue
            if _payload_bool(event, "historical_only") or _payload_bool(event, "is_reconstructed"):
                continue
            if _payload_bool(event, "invalidated"):
                return _finalize_candidate(_invalid_result(direction, "fvg_invalidated", used + [event]), anchor_event, candidate_sequence)
            if completed < 6:
                used.append(_with_candidate_payload(
                    event,
                    expected_candidate_id,
                    anchor_index=anchor_index,
                    confirmed_trigger_id=confirmed_trigger_id,
                ))
                completed = max(completed, 6)
            fvg_seen = True
            fvg_created_index = event.index
            continue

        if event_type == "FVG_RETESTED" and side == scenario_side:
            if not confirmed_trigger_seen:
                continue
            if not _fvg_event_matches_candidate(event, expected_candidate_id, confirmed_trigger_id):
                continue
            if _fvg_created_before_confirmed(event, confirmed_trigger_index):
                continue
            if not fvg_seen:
                return _finalize_candidate(_invalid_result(direction, "retest_before_fvg", used + [event]), anchor_event, candidate_sequence)
            if _event_sort_key(event.index) <= _event_sort_key(fvg_created_index):
                return _finalize_candidate(_invalid_result(direction, "retest_before_fvg", used + [event]), anchor_event, candidate_sequence)
            if completed < 7:
                used.append(_with_candidate_payload(
                    event,
                    expected_candidate_id,
                    anchor_index=anchor_index,
                    confirmed_trigger_id=confirmed_trigger_id,
                ))
                completed = max(completed, 7)
            retest_seen = True
            retest_index = event.index
            continue

        if event_type == "DISPLACEMENT_CONFIRMED" and side == scenario_side:
            if not confirmed_trigger_seen:
                continue
            if not _fvg_event_matches_candidate(event, expected_candidate_id, confirmed_trigger_id):
                continue
            if _fvg_created_before_confirmed(event, confirmed_trigger_index):
                continue
            if not retest_seen:
                if _payload_value(event, "displacement_stage") == "bos_displacement":
                    continue
                return _finalize_candidate(_invalid_result(direction, "displacement_before_retest", used + [event]), anchor_event, candidate_sequence)
            if _event_sort_key(event.index) <= _event_sort_key(retest_index):
                return _finalize_candidate(_invalid_result(direction, "displacement_before_retest", used + [event]), anchor_event, candidate_sequence)
            if completed < 8:
                used.append(_with_candidate_payload(
                    event,
                    expected_candidate_id,
                    anchor_index=anchor_index,
                    confirmed_trigger_id=confirmed_trigger_id,
                ))
                completed = max(completed, 8)
            continue

        if event_type in ("RISK_VALID", "RISK_INVALID"):
            if event_type == "RISK_INVALID":
                reason = _risk_invalid_reason(payload_to_dict(event.payload), risk_plan)
                last_invalidated_component = reason
                risk_reason = reason
                if completed < 8:
                    continue
                continue
            risk_event_seen = True
            if completed < 8:
                continue
            used.append(_with_type(event, "RISK_VALID"))
            completed = max(completed, 10)
            risk_valid = True
            risk_reason = _payload_reason(event, None)

    if risk_plan is not None and not risk_event_seen:
        if _get(risk_plan, "valid", False):
            if completed >= 8:
                risk_payload = _to_dict(risk_plan)
                if _risk_plan_matches_candidate(risk_payload, expected_candidate_id):
                    risk_event = ScenarioEvent("RISK_VALID", scenario_side, _last_index(used), source="risk_plan", payload=risk_payload)
                    used.append(risk_event)
                    completed = max(completed, 10)
                    risk_valid = True
                    risk_reason = _get(risk_plan, "reason")
        else:
            if completed >= 8:
                reason = _risk_invalid_reason({}, risk_plan)
                last_invalidated_component = reason
                risk_reason = reason

    if completed == 0:
        return _finalize_candidate(_partial_result(
        direction,
        "not_started",
        used,
        completed,
        risk_valid=risk_valid,
        risk_reason=risk_reason,
        last_invalidated_component=last_invalidated_component,
        opposite_trigger_event=opposite_trigger_event,
        confirmed_trigger_debug=_confirmed_trigger_debug(
            direction,
            early_trigger_index,
            events,
            rejected_confirmed_candidates,
            confirmed_trigger_seen,
            candidate_id=expected_candidate_id,
            candidate_anchor_index=anchor_index,
        ),
    ), anchor_event, candidate_sequence)
    if completed >= len(FLOW):
        return _finalize_candidate(_complete_result(direction, used, risk_valid=risk_valid, risk_reason=risk_reason), anchor_event, candidate_sequence)
    return _finalize_candidate(_partial_result(
        direction,
        "waiting_for_confirmation",
        used,
        completed,
        risk_valid=risk_valid,
        risk_reason=risk_reason,
        anchor_index=sfp_index,
        last_invalidated_component=last_invalidated_component,
        opposite_trigger_event=opposite_trigger_event,
        confirmed_trigger_debug=_confirmed_trigger_debug(
            direction,
            early_trigger_index,
            events,
            rejected_confirmed_candidates,
            confirmed_trigger_seen,
            candidate_id=expected_candidate_id,
            candidate_anchor_index=anchor_index,
        ),
    ), anchor_event, candidate_sequence)


def _partial_result(
    direction,
    status,
    used,
    completed,
    *,
    risk_valid=False,
    risk_reason=None,
    anchor_index=None,
    last_invalidated_component=None,
    opposite_trigger_event=None,
    confirmed_trigger_debug=None,
) -> ScenarioScanResult:
    next_step = FLOW[completed] if completed < len(FLOW) else None
    missing = list(FLOW[completed:])
    current_step = _current_step_label(used, completed)
    waiting_for = _waiting_for(direction, next_step, last_invalidated_component)
    trigger_scan_extra = {}
    if opposite_trigger_event is not None:
        trigger_scan_extra["opposite_trigger"] = _event_payload_or_snapshot(opposite_trigger_event, stage="confirmed")
    if confirmed_trigger_debug is not None:
        trigger_scan_extra["confirmed_trigger_debug"] = confirmed_trigger_debug
    result = ScenarioScanResult(
        direction=direction,
        status=status,
        current_step=current_step,
        next_expected_step=next_step,
        signal_allowed=False,
        scenario_valid=False,
        completion_ratio=round(completed / len(FLOW), 4),
        completed_steps=completed,
        total_steps=len(FLOW),
        quality_score=_quality_score(used),
        events_used=list(used),
        missing_steps=missing,
        last_invalidated_component=last_invalidated_component,
        waiting_for=waiting_for,
        anchor_index=anchor_index,
        last_event_index=_last_index(used),
        risk_valid=risk_valid,
        risk_reason=risk_reason,
        trigger_scan=trigger_scan_extra or None,
    )
    return _apply_opposite_trigger_fields(result, opposite_trigger_event, candidate_invalidated=False)


def _candidate_anchors(direction, events):
    scenario_side = _direction_side(direction)
    anchor_types = {"PD_LOCATION_VALID", "POI_TOUCHED", "SFP_CONFIRMED", "LIQUIDITY_SWEEP_CONFIRMED", "FVG_RETESTED"}
    anchors = []
    seen = set()
    active_poi = None
    for event in events:
        event_type = _normalize_event_type(event.event_type)
        side = _normalize_side(event.direction)
        if event_type in ("INVALIDATION", "POI_LEFT", "POI_EXITED") and _event_applies(direction, event):
            active_poi = None
            continue
        if event_type not in anchor_types:
            continue
        if side not in (None, scenario_side):
            continue
        if event_type in ("PD_LOCATION_VALID", "POI_TOUCHED"):
            poi_key = _anchor_zone_key(direction, event)
            if active_poi and active_poi["key"] == poi_key and not _payload_bool(event, "new_independent_touch") and not _payload_bool(event, "reentered"):
                active_poi["touches"].append(event)
                anchors[active_poi["anchor_pos"]] = _zone_anchor_from_group(direction, active_poi, anchors[active_poi["anchor_pos"]])
                continue

            active_poi = {"key": poi_key, "touches": [event], "anchor_pos": len(anchors), "event_type": event_type}
            anchor = _zone_anchor_from_group(direction, active_poi, event)
            seen.add((_scenario_key_from_anchor(direction, anchor),))
            anchors.append(anchor)
            continue

        if event_type in ("SFP_CONFIRMED", "LIQUIDITY_SWEEP_CONFIRMED") and active_poi is not None and not active_poi.get("upgraded"):
            upgraded = _upgrade_poi_anchor_with_sfp(direction, active_poi, event)
            anchors[active_poi["anchor_pos"]] = upgraded
            active_poi["upgraded"] = True
            continue

        key = (event_type, str(event.index))
        if key in seen:
            continue
        seen.add(key)
        anchors.append(event)
    return anchors


def _events_for_candidate(direction, events, anchor):
    scenario_side = _direction_side(direction)
    anchor_type = _normalize_event_type(anchor.event_type)
    anchor_sort = _event_sort_key(anchor.index)
    candidate_events = []
    latest_poi_before_anchor = None
    anchor_payload = payload_to_dict(anchor.payload)
    upgraded_poi_index = anchor_payload.get("anchor_first_touch_index") if anchor_payload.get("upgraded_from_anchor_type") in ("PD_LOCATION_VALID", "POI_TOUCHED") else None

    for event in events:
        event_type = _normalize_event_type(event.event_type)
        side = _normalize_side(event.direction)
        if event_type == "HTF_CONTEXT_CONFIRMED":
            candidate_events.append(event)
            continue
        if event_type in ("PD_LOCATION_VALID", "POI_TOUCHED") and side in (None, scenario_side) and _event_sort_key(event.index) <= anchor_sort:
            if upgraded_poi_index is not None and _event_sort_key(event.index) != _event_sort_key(upgraded_poi_index):
                continue
            latest_poi_before_anchor = event

    if anchor_type not in ("PD_LOCATION_VALID", "POI_TOUCHED") and latest_poi_before_anchor is not None:
        candidate_events.append(latest_poi_before_anchor)

    if anchor_type == "FVG_RETESTED":
        candidate_events.append(_with_type(anchor, "POI_TOUCHED"))
    else:
        candidate_events.append(anchor)

    for event in events:
        if event is anchor:
            continue
        event_type = _normalize_event_type(event.event_type)
        if event_type in ("HTF_CONTEXT_CONFIRMED", "PD_LOCATION_VALID", "POI_TOUCHED"):
            continue
        if _event_sort_key(event.index) < anchor_sort:
            continue
        candidate_events.append(event)

    return sorted(candidate_events, key=lambda item: _event_sort_key(item.index))


def _zone_anchor_from_group(direction, group, current_anchor):
    if _normalize_event_type(current_anchor.event_type) in ("SFP_CONFIRMED", "LIQUIDITY_SWEEP_CONFIRMED"):
        return _upgrade_poi_anchor_with_sfp(direction, group, current_anchor)

    touches = group["touches"]
    first_touch = touches[0]
    last_touch = touches[-1]
    anchor_type = group.get("event_type") or _normalize_event_type(first_touch.event_type)
    payload = payload_to_dict(first_touch.payload)
    scenario_key = _scenario_key(direction, anchor_type, first_touch.index, group["key"])
    payload.update(
        {
            "scenario_key": scenario_key,
            "anchor_index": first_touch.index,
            "candidate_created_at": first_touch.index,
            "candidate_updated_at": last_touch.index,
            "anchor_first_touch_index": first_touch.index,
            "anchor_last_touch_index": last_touch.index,
            "age_bars": _touch_age_bars(touches),
            "update_count": max(0, len(touches) - 1),
            "poi_id": group["key"] if anchor_type == "POI_TOUCHED" else None,
            "pd_location_id": group["key"] if anchor_type == "PD_LOCATION_VALID" else None,
        }
    )
    return ScenarioEvent(
        event_type=anchor_type,
        direction=first_touch.direction,
        index=first_touch.index,
        quality_score=_latest_quality(touches, first_touch.quality_score),
        source=first_touch.source,
        payload=payload,
    )


def _poi_anchor_from_group(direction, group, current_anchor):
    return _zone_anchor_from_group(direction, group, current_anchor)


def _upgrade_poi_anchor_with_sfp(direction, group, sfp_event):
    touches = group["touches"]
    first_touch = touches[0]
    last_touch = touches[-1]
    upgraded_from = group.get("event_type") or _normalize_event_type(first_touch.event_type)
    payload = payload_to_dict(sfp_event.payload)
    scenario_key = _scenario_key(direction, upgraded_from, first_touch.index, group["key"])
    payload.update(
        {
            "scenario_key": scenario_key,
            "anchor_index": sfp_event.index,
            "candidate_created_at": first_touch.index,
            "candidate_updated_at": sfp_event.index,
            "anchor_first_touch_index": first_touch.index,
            "anchor_last_touch_index": last_touch.index,
            "age_bars": _touch_age_bars(touches),
            "update_count": max(0, len(touches) - 1),
            "poi_id": group["key"] if upgraded_from == "POI_TOUCHED" else None,
            "pd_location_id": group["key"] if upgraded_from == "PD_LOCATION_VALID" else None,
            "upgraded_from_anchor_type": upgraded_from,
            "upgraded_from_anchor_index": first_touch.index,
        }
    )
    return ScenarioEvent(
        event_type=_normalize_event_type(sfp_event.event_type),
        direction=sfp_event.direction,
        index=sfp_event.index,
        quality_score=sfp_event.quality_score,
        source=sfp_event.source,
        payload=payload,
    )


def _anchor_zone_key(direction, event):
    if _normalize_event_type(event.event_type) == "PD_LOCATION_VALID":
        return _pd_scenario_key(direction, event)
    return _poi_scenario_key(direction, event)


def _pd_scenario_key(direction, event):
    payload = payload_to_dict(event.payload)
    explicit = payload.get("pd_location_id") or payload.get("pd_id")
    if explicit is not None:
        return explicit
    return (
        payload.get("source") or event.source or "premium_discount",
        payload.get("zone"),
        payload.get("range_timeframe"),
        payload.get("range_low"),
        payload.get("range_high"),
    )


def _poi_scenario_key(direction, event):
    payload = payload_to_dict(event.payload)
    explicit = (
        payload.get("poi_id")
        or payload.get("zone_id")
        or payload.get("fvg_id")
        or payload.get("order_block_id")
        or payload.get("liquidity_id")
    )
    session = payload.get("poi_touch_session") or payload.get("touch_session") or payload.get("session_id")
    if explicit is not None:
        base = explicit
    else:
        base = (
            payload.get("source") or event.source or "poi",
            payload.get("zone"),
            payload.get("zone_depth"),
            payload.get("range_timeframe"),
            payload.get("range_low"),
            payload.get("range_high"),
            payload.get("bottom"),
            payload.get("top"),
        )
    if session is not None:
        return (base, "session", session)
    return base


def _scenario_key_from_anchor(direction, anchor):
    payload = payload_to_dict(anchor.payload)
    return payload.get("scenario_key") or _scenario_key(
        direction,
        _normalize_event_type(anchor.event_type),
        anchor.index,
        _anchor_zone_key(direction, anchor) if _normalize_event_type(anchor.event_type) in ("PD_LOCATION_VALID", "POI_TOUCHED") else None,
    )


def _scenario_key(direction, anchor_type, normalized_anchor_index, poi_id=None):
    normalized_anchor_type = _normalize_event_type(anchor_type)
    if normalized_anchor_type in ("PD_LOCATION_VALID", "POI_TOUCHED") and poi_id is not None:
        identity_parts = tuple(poi_id) if isinstance(poi_id, (list, tuple)) else (poi_id,)
        return (str(direction).upper(), normalized_anchor_type, *identity_parts)
    return (
        str(direction).upper(),
        normalized_anchor_type,
        _string_index(normalized_anchor_index),
        _stable_key_part(poi_id if poi_id is not None else "none"),
    )


def _touch_age_bars(touches):
    if not touches:
        return 0
    first = _event_sort_key(touches[0].index)
    last = _event_sort_key(touches[-1].index)
    if all(isinstance(item.index, (int, float)) for item in touches):
        return max(0, int(last - first))
    return max(0, len(touches) - 1)


def _latest_quality(events, default=None):
    for event in reversed(events or []):
        if event.quality_score is not None:
            return event.quality_score
    return default


def _finalize_candidate(result, anchor_event, sequence):
    if anchor_event is not None:
        result.anchor_type = _normalize_event_type(anchor_event.event_type)
        payload = payload_to_dict(anchor_event.payload)
        result.anchor_index = payload.get("anchor_index", anchor_event.index)
        result.candidate_created_at = payload.get("candidate_created_at")
        result.candidate_updated_at = payload.get("candidate_updated_at")
        result.anchor_first_touch_index = payload.get("anchor_first_touch_index")
        result.anchor_last_touch_index = payload.get("anchor_last_touch_index")
        result.scenario_key = payload.get("scenario_key") or _scenario_key_from_anchor(result.direction, anchor_event)
        reconstructed_age = _market_age_bars(result.anchor_index, result.last_event_index)
        result.market_age_bars = max(int(payload.get("market_age_bars", payload.get("age_bars", 0)) or 0), reconstructed_age)
        result.runtime_update_count = int(payload.get("runtime_update_count", payload.get("update_count", 0)) or 0)
        result.age_bars = result.market_age_bars
        result.update_count = result.runtime_update_count
        result.candidate_id = _candidate_id(
            result.direction,
            result.anchor_type,
            result.anchor_index,
            sequence,
            scenario_key=result.scenario_key,
        )
    else:
        result.candidate_id = _candidate_id(result.direction, "BASE", result.anchor_index, sequence)
        result.scenario_key = _scenario_key(result.direction, "BASE", result.anchor_index, None)
        result.market_age_bars = _market_age_bars(result.anchor_index, result.last_event_index)
        result.age_bars = result.market_age_bars
    result.quality_score = _candidate_quality(result)
    result.trigger_scan = _candidate_trigger_scan(result)
    return result


def _candidate_trigger_scan(result: ScenarioScanResult) -> dict:
    extra_scan = dict(result.trigger_scan or {})
    early_event = _first_used_event(result.events_used, ("EARLY_TRIGGER_CONFIRMED",))
    confirmed_event = _first_used_event(result.events_used, ("CONFIRMED_TRIGGER_CONFIRMED", "CHOCH_CONFIRMED", "BOS_CONFIRMED"))
    sfp_event = _first_used_event(result.events_used, ("SFP_CONFIRMED", "LIQUIDITY_SWEEP_CONFIRMED"))
    early_trigger = _event_payload_or_snapshot(early_event, stage="early")
    confirmed_trigger = _event_payload_or_snapshot(confirmed_event, stage="confirmed")
    _attach_candidate_metadata(early_trigger, result, early_event)
    _attach_candidate_metadata(confirmed_trigger, result, confirmed_event, early_event=early_event)
    opposite_trigger = extra_scan.get("opposite_trigger")
    confirmed_trigger_debug = extra_scan.get("confirmed_trigger_debug")

    rejected_reason = result.invalidated_reason
    waiting_for = result.waiting_for
    if result.status != "invalidated":
        if sfp_event is None and result.next_expected_step == "SFP_CONFIRMED":
            rejected_reason = "waiting_for_sfp"
            waiting_for = "liquidity sweep / SFP"
        elif early_trigger and not confirmed_trigger:
            rejected_reason = "confirmed_trigger_missing"
        elif not confirmed_trigger and result.next_expected_step in ("EARLY_TRIGGER_CONFIRMED", "CONFIRMED_TRIGGER_CONFIRMED"):
            rejected_reason = _missing_after_reason(result.direction)
        elif confirmed_trigger and result.next_expected_step == "FVG_CREATED":
            waiting_for = f"{_direction_side(result.direction)} FVG after confirmed BOS"

    trigger_confirmed = confirmed_trigger is not None
    scan = {
        "candidate_id": result.candidate_id,
        "candidate_created_at": _string_index(result.candidate_created_at),
        "candidate_updated_at": _string_index(result.candidate_updated_at),
        "anchor_type": result.anchor_type,
        "anchor_index": _string_index(result.anchor_index),
        "anchor_first_touch_index": _string_index(result.anchor_first_touch_index),
        "anchor_last_touch_index": _string_index(result.anchor_last_touch_index),
        "age_bars": result.age_bars,
        "update_count": result.update_count,
        "market_age_bars": result.market_age_bars,
        "runtime_update_count": result.runtime_update_count,
        "scenario_key": _scenario_key_snapshot(result.scenario_key),
        "expected_direction": result.direction,
        "direction": result.direction,
        "selected_trigger": confirmed_trigger if trigger_confirmed else None,
        "confirmed_trigger": confirmed_trigger,
        "early_trigger": early_trigger,
        "opposite_trigger": opposite_trigger,
        "sfp_index": _string_index(sfp_event.index) if sfp_event else None,
        "poi_index": _string_index(_first_index(result.events_used, ("POI_TOUCHED",))),
        "trigger_index": _string_index(confirmed_event.index) if confirmed_event else None,
        "early_trigger_confirmed": early_trigger is not None,
        "trigger_confirmed": trigger_confirmed,
        "rejected_reason": rejected_reason,
        "waiting_for": waiting_for,
        "confirmed_trigger_debug": confirmed_trigger_debug,
        "pd_location_index": _string_index(_first_index(result.events_used, ("PD_LOCATION_VALID",))),
    }
    if result.opposite_trigger_detected:
        scan.update({
            "opposite_trigger_detected": True,
            "opposite_trigger_type": result.opposite_trigger_type,
            "opposite_trigger_quality": result.opposite_trigger_quality,
            "opposite_trigger_index": _string_index(result.opposite_trigger_index),
            "candidate_invalidated": result.candidate_invalidated,
            "invalidated_reason": result.invalidated_reason,
        })
    return scan


def _candidate_id(direction, anchor_type, anchor_index, sequence, *, scenario_key=None):
    if scenario_key:
        return "SCENARIO_" + "_".join(_stable_key_part(part) for part in scenario_key)
    index = str(anchor_index).replace(" ", "T") if anchor_index is not None else "none"
    return f"{direction}_{anchor_type}_{index}_{sequence}"


def _candidate_id_for_anchor(direction, anchor_event, sequence):
    if anchor_event is None:
        return _candidate_id(direction, "BASE", None, sequence)
    payload = payload_to_dict(anchor_event.payload)
    anchor_type = _normalize_event_type(anchor_event.event_type)
    anchor_index = payload.get("anchor_index", anchor_event.index)
    scenario_key = payload.get("scenario_key") or _scenario_key_from_anchor(direction, anchor_event)
    return _candidate_id(direction, anchor_type, anchor_index, sequence, scenario_key=scenario_key)


def _stable_key_part(value):
    return str(value).replace(" ", "T").replace("/", "-").replace(":", "")


def _confirmed_trigger_debug(direction, early_trigger_index, events, rejected_candidates, confirmed_trigger_seen, *, candidate_id=None, candidate_anchor_index=None):
    if early_trigger_index is None:
        return None
    carried_debug = _carried_confirmed_trigger_debug(direction, early_trigger_index, events)
    if carried_debug:
        debug = dict(carried_debug)
        if rejected_candidates:
            existing_rejected = list(debug.get("rejected_candidates") or [])
            debug["rejected_candidates"] = existing_rejected + [
                _scoped_rejected_candidate(item, candidate_id, candidate_anchor_index, early_trigger_index)
                for item in (rejected_candidates or [])
            ]
            if not confirmed_trigger_seen and not debug.get("final_reason"):
                debug["final_reason"] = debug["rejected_candidates"][0].get("rejected_reason")
        return debug
    scenario_side = _direction_side(direction)
    confirmed_events = [
        event for event in events or []
        if _normalize_event_type(event.event_type) in ("CHOCH_CONFIRMED", "BOS_CONFIRMED", "CONFIRMED_TRIGGER_CONFIRMED")
        and _normalize_side(event.direction) == scenario_side
    ]
    final_reason = None
    if not confirmed_trigger_seen:
        final_reason = "no_confirmed_bos_after_early_trigger"
        if rejected_candidates:
            final_reason = rejected_candidates[0].get("rejected_reason") or final_reason
    return {
        "early_trigger_index": _string_index(early_trigger_index),
        "search_window_start": _string_index(early_trigger_index),
        "search_window_end": None,
        "expected_direction": direction,
        "candidate_bos_count": sum(1 for event in confirmed_events if "bos" in str(_event_type_from_payload(event)).lower()),
        "candidate_choch_count": sum(1 for event in confirmed_events if "choch" in str(_event_type_from_payload(event)).lower()),
        "rejected_candidates": [
            _scoped_rejected_candidate(item, candidate_id, candidate_anchor_index, early_trigger_index)
            for item in (rejected_candidates or [])
        ],
        "final_reason": final_reason,
    }


def _carried_confirmed_trigger_debug(direction, early_trigger_index, events):
    scenario_side = _direction_side(direction)
    for event in events or []:
        if _normalize_event_type(event.event_type) != "EARLY_TRIGGER_CONFIRMED":
            continue
        if _normalize_side(event.direction) != scenario_side:
            continue
        if _event_sort_key(event.index) != _event_sort_key(early_trigger_index):
            continue
        payload = payload_to_dict(event.payload)
        debug = payload.get("confirmed_trigger_debug")
        if debug:
            return debug
    return None


def _rejected_confirmed_candidate(event, reason):
    return {
        "type": _event_type_from_payload(event),
        "index": _string_index(event.index),
        "quality_score": event.quality_score,
        "rejected_reason": reason,
    }


def _scoped_rejected_candidate(candidate, candidate_id, candidate_anchor_index, early_trigger_index):
    item = dict(candidate or {})
    if candidate_id is not None:
        item["candidate_id"] = candidate_id
    if candidate_anchor_index is not None:
        item["candidate_anchor_index"] = _string_index(candidate_anchor_index)
    if early_trigger_index is not None:
        item["early_trigger_index"] = _string_index(early_trigger_index)
    return item


def _event_type_from_payload(event):
    payload = payload_to_dict(event.payload)
    return payload.get("type") or _trigger_type_from_event(event, "confirmed")


def _is_confirmed_bos_event(event):
    return "bos" in str(_event_type_from_payload(event)).lower()


def _event_candidate_matches(event, expected_candidate_id):
    payload = payload_to_dict(event.payload)
    candidate_id = payload.get("candidate_id")
    return candidate_id is None or expected_candidate_id is None or str(candidate_id) == str(expected_candidate_id)


def _event_id(event):
    payload = payload_to_dict(event.payload)
    return payload.get("event_id") or payload.get("id") or f"{_normalize_event_type(event.event_type)}:{event.index}"


def _payload_value(event, key, default=None):
    payload = payload_to_dict(event.payload)
    return payload.get(key, default)


def _fvg_event_matches_candidate(event, expected_candidate_id, confirmed_trigger_id):
    payload = payload_to_dict(event.payload)
    source_candidate_id = payload.get("source_candidate_id") or payload.get("candidate_id")
    if source_candidate_id is None and event.source == "fvg":
        return False
    if source_candidate_id is not None and expected_candidate_id is not None and str(source_candidate_id) != str(expected_candidate_id):
        return False
    source_trigger_id = payload.get("source_confirmed_trigger_id")
    if source_trigger_id is not None and confirmed_trigger_id is not None and str(source_trigger_id) != str(confirmed_trigger_id):
        return False
    return True


def _fvg_created_before_confirmed(event, confirmed_trigger_index):
    created_index = _payload_value(event, "created_index")
    return created_index is not None and _event_sort_key(created_index) <= _event_sort_key(confirmed_trigger_index)


def _candidate_quality(result):
    base = _quality_score(result.events_used)
    recency_bonus = 5.0 if result.anchor_index is not None else 0.0
    risk_bonus = 5.0 if result.risk_valid else 0.0
    invalid_penalty = 25.0 if result.status == "invalidated" else 0.0
    return round(max(0.0, min(100.0, base + recency_bonus + risk_bonus - invalid_penalty)), 2)


def _best_candidate(candidates, expected_direction, htf_trend):
    if not candidates:
        return None
    return max(candidates, key=lambda item: _selection_rank(item, expected_direction, htf_trend))


def _is_living(candidate):
    return candidate.status in {"building", "waiting_for_confirmation", "complete"}


def _selection_rejected_reason(candidate):
    if candidate.status == "invalidated":
        return "candidate_invalidated"
    if candidate.status not in {"building", "waiting_for_confirmation", "complete"}:
        return f"candidate_status_{candidate.status}"
    return None


def _candidate_counts(long_candidates, short_candidates):
    all_candidates = list(long_candidates or []) + list(short_candidates or [])
    return {
        "long_total": len(long_candidates or []),
        "short_total": len(short_candidates or []),
        "living": sum(1 for item in all_candidates if _is_living(item)),
        "invalidated": sum(1 for item in all_candidates if item.status == "invalidated"),
        "complete": sum(1 for item in all_candidates if item.status == "complete"),
    }


def _candidate_summary(candidate):
    data = {
        "candidate_id": candidate.candidate_id,
        "direction": candidate.direction,
        "anchor_type": candidate.anchor_type,
        "anchor_index": str(candidate.anchor_index) if candidate.anchor_index is not None else None,
        "candidate_created_at": str(candidate.candidate_created_at) if candidate.candidate_created_at is not None else None,
        "candidate_updated_at": str(candidate.candidate_updated_at) if candidate.candidate_updated_at is not None else None,
        "anchor_first_touch_index": str(candidate.anchor_first_touch_index) if candidate.anchor_first_touch_index is not None else None,
        "anchor_last_touch_index": str(candidate.anchor_last_touch_index) if candidate.anchor_last_touch_index is not None else None,
        "age_bars": candidate.age_bars,
        "update_count": candidate.update_count,
        "market_age_bars": candidate.market_age_bars,
        "runtime_update_count": candidate.runtime_update_count,
        "scenario_key": _scenario_key_snapshot(candidate.scenario_key),
        "status": candidate.status,
        "completed_steps": candidate.completed_steps,
        "quality_score": candidate.quality_score,
        "rank": candidate.rank,
        "progress_rank": candidate.progress_rank,
        "selection_eligible": candidate.selection_eligible,
        "selection_rejected_reason": candidate.selection_rejected_reason,
        "is_selected": candidate.is_selected,
        "opposite_trigger_detected": candidate.opposite_trigger_detected,
        "opposite_trigger_type": candidate.opposite_trigger_type,
        "opposite_trigger_quality": candidate.opposite_trigger_quality,
        "opposite_trigger_index": str(candidate.opposite_trigger_index) if candidate.opposite_trigger_index is not None else None,
        "candidate_invalidated": candidate.candidate_invalidated,
    }
    if candidate.waiting_for:
        data["waiting_for"] = candidate.waiting_for
    if candidate.invalidated_reason:
        data["invalidated_reason"] = candidate.invalidated_reason
    return data


def _complete_result(direction, used, *, risk_valid=False, risk_reason=None):
    return ScenarioScanResult(
        direction=direction,
        status="complete",
        current_step="signal_allowed",
        next_expected_step=None,
        signal_allowed=True,
        scenario_valid=True,
        completion_ratio=1.0,
        completed_steps=len(FLOW),
        total_steps=len(FLOW),
        quality_score=_quality_score(used),
        events_used=list(used),
        missing_steps=[],
        anchor_index=_first_index(used, ("SFP_CONFIRMED", "LIQUIDITY_SWEEP_CONFIRMED")),
        last_event_index=_last_index(used),
        risk_valid=risk_valid,
        risk_reason=risk_reason,
    )


def _invalid_result(
    direction,
    reason,
    used=None,
    *,
    risk_valid=False,
    risk_reason=None,
    opposite_trigger_event=None,
    last_invalidated_component=None,
):
    used = list(used or [])
    completed = _completed_count(used)
    result = ScenarioScanResult(
        direction=direction,
        status="invalidated",
        current_step=STEP_LABELS[FLOW[max(completed - 1, 0)]] if completed else "not_started",
        next_expected_step=None,
        signal_allowed=False,
        scenario_valid=False,
        completion_ratio=round(completed / len(FLOW), 4),
        completed_steps=completed,
        total_steps=len(FLOW),
        quality_score=0.0,
        events_used=used,
        missing_steps=[],
        invalidated_reason=reason,
        last_invalidated_component=last_invalidated_component,
        waiting_for=None,
        anchor_index=_first_index(used, ("SFP_CONFIRMED", "LIQUIDITY_SWEEP_CONFIRMED")),
        last_event_index=_last_index(used),
        risk_valid=risk_valid,
        risk_reason=risk_reason,
    )
    _apply_opposite_trigger_fields(result, opposite_trigger_event, candidate_invalidated=True)
    return result


def _apply_opposite_trigger_fields(result, opposite_trigger_event, *, candidate_invalidated=False):
    if opposite_trigger_event is None:
        return result
    result.opposite_trigger_detected = True
    result.opposite_trigger_type = _event_type_from_payload(opposite_trigger_event)
    result.opposite_trigger_quality = opposite_trigger_event.quality_score
    result.opposite_trigger_index = opposite_trigger_event.index
    result.candidate_invalidated = bool(candidate_invalidated)
    return result


def _selection_rank(result, expected_direction, htf_trend):
    status_priority = {
        "complete": 4,
        "waiting_for_confirmation": 3,
        "building": 2,
        "not_started": 1,
        "invalidated": 0,
    }.get(result.status, 0)
    expected = 1 if expected_direction and result.direction == str(expected_direction).upper() else 0
    htf_aligned = 1 if _direction_side(result.direction) == htf_trend else 0
    risk_valid = 1 if result.risk_valid else 0
    trigger_confirmed = 1 if _candidate_has_confirmed_trigger(result) else 0
    early_trigger_confirmed = 1 if _candidate_has_early_trigger(result) else 0
    return (
        1 if result.scenario_valid else 0,
        1 if result.signal_allowed else 0,
        result.completed_steps,
        trigger_confirmed,
        early_trigger_confirmed,
        status_priority,
        result.quality_score,
        _event_sort_key(result.last_event_index),
        _event_sort_key(result.anchor_index),
        htf_aligned,
        risk_valid,
        expected,
    )


def _candidate_has_confirmed_trigger(result):
    trigger_scan = result.trigger_scan or {}
    if trigger_scan.get("trigger_confirmed"):
        return True
    return any(
        _normalize_event_type(event.event_type) in ("CONFIRMED_TRIGGER_CONFIRMED", "CHOCH_CONFIRMED", "BOS_CONFIRMED")
        for event in result.events_used or []
    )


def _candidate_has_early_trigger(result):
    trigger_scan = result.trigger_scan or {}
    if trigger_scan.get("early_trigger_confirmed"):
        return True
    return any(
        _normalize_event_type(event.event_type) == "EARLY_TRIGGER_CONFIRMED"
        for event in result.events_used or []
    )


def _expected_scenario(expected_direction, best_long, best_short):
    expected = str(expected_direction or "").upper()
    if expected == "LONG":
        return best_long
    if expected == "SHORT":
        return best_short
    return None


def _expected_block_reason(expected_direction, direction_block_reasons):
    expected = str(expected_direction or "").upper()
    if expected in ("LONG", "SHORT"):
        return (direction_block_reasons or {}).get(expected)
    reasons = list((direction_block_reasons or {}).values())
    return reasons[0] if reasons and len(set(reasons)) == 1 else None


def _output_reason(result):
    if result.status == "complete":
        return "complete_scenario"
    if result.status == "invalidated":
        return result.invalidated_reason or "invalidated"
    if result.waiting_for:
        return _reason_from_waiting(result.waiting_for)
    return "scenario_not_started"


def _reason_from_waiting(waiting_for):
    mapping = {
        "liquidity sweep / SFP": "waiting_for_liquidity_sweep",
        "bullish CHOCH/BOS after SFP": "waiting_for_bullish_choch_or_bos",
        "bearish CHOCH/BOS after SFP": "waiting_for_bearish_choch_or_bos",
        "confirmed bullish BOS after early CHOCH": "waiting_for_confirmed_bullish_bos",
        "confirmed bearish BOS after early CHOCH": "waiting_for_confirmed_bearish_bos",
        "bullish FVG after confirmed BOS": "waiting_for_bullish_fvg_after_confirmed_bos",
        "bearish FVG after confirmed BOS": "waiting_for_bearish_fvg_after_confirmed_bos",
        "bullish BOS": "waiting_for_bullish_bos",
        "bearish BOS": "waiting_for_bearish_bos",
    }
    return mapping.get(waiting_for, str(waiting_for).replace(" ", "_"))


def _waiting_for(direction, next_step, last_invalidated_component=None):
    if not next_step:
        return None
    side = _direction_side(direction)
    if next_step == "FVG_CREATED" and last_invalidated_component in {"fvg_invalidated", "fvg_before_bos"}:
        return f"valid {side} FVG after SFP"
    if next_step == "FVG_CREATED":
        return f"{side} FVG after confirmed BOS"
    if next_step == "RISK_VALID" and last_invalidated_component:
        return "valid risk plan"
    text = WAITING_TEXT.get(next_step, next_step.lower())
    if side == "bearish":
        text = text.replace("bullish", "bearish")
    return text


def _completed_count(events):
    completed = 0
    for event in events or []:
        event_type = _normalize_event_type(event.event_type)
        if event_type == "LIQUIDITY_SWEEP_CONFIRMED":
            event_type = "SFP_CONFIRMED"
        if event_type in ("CHOCH_CONFIRMED", "BOS_CONFIRMED"):
            event_type = "CONFIRMED_TRIGGER_CONFIRMED"
        if event_type in FLOW:
            completed = max(completed, FLOW.index(event_type) + 1)
    if any(_normalize_event_type(event.event_type) == "RISK_VALID" for event in events or []):
        completed = max(completed, len(FLOW))
    return completed


def _current_step_label(used, completed):
    if completed <= 0:
        return "not_started"
    last_event_type = _normalize_event_type(used[-1].event_type) if used else None
    if completed == 2 and last_event_type in ("PD_LOCATION_VALID", "POI_IDENTIFIED", "POI_TOUCHED"):
        return STEP_LABELS.get(last_event_type, STEP_LABELS["POI_TOUCHED"])
    return STEP_LABELS[FLOW[completed - 1]]


def _quality_score(events):
    qualities = [float(event.quality_score) for event in events or [] if event.quality_score is not None]
    if not qualities:
        return 0.0
    return round(sum(qualities) / len(qualities), 2)


def _risk_invalid_reason(payload, risk_plan):
    reason = str(payload.get("reason") or _get(risk_plan, "reason", "") or "").lower()
    late_entry = bool(payload.get("late_entry") or _get(risk_plan, "late_entry", False))
    if "rr to target 1 below minimum" in reason or "rr below" in reason:
        return "risk_rr_below_min"
    if late_entry or "late entry" in reason:
        return "risk_late_entry"
    if "watchlist only" in reason:
        return "risk_rr_below_min"
    return payload.get("reason") or _get(risk_plan, "reason", "risk_invalid")


def _pd_valid_for_direction(direction, premium_discount):
    if direction == "LONG":
        return bool(_get(premium_discount, "valid_for_buy", False))
    return bool(_get(premium_discount, "valid_for_sell", False))


def _direction_block_reason(direction, htf_trend, premium_discount):
    scenario_side = _direction_side(direction)
    if htf_trend in ("bullish", "bearish") and htf_trend != scenario_side:
        return "htf_direction_conflict"
    if premium_discount is not None and not _pd_valid_for_direction(direction, premium_discount):
        return "pd_invalid_for_direction"
    return None


def _htf_trend(htf_structure, events):
    trend = _get(htf_structure, "trend")
    if trend in ("bullish", "bearish", "neutral"):
        return trend
    for event in events:
        if _normalize_event_type(event.event_type) == "HTF_CONTEXT_CONFIRMED":
            return _normalize_side(event.direction)
    return None


def _as_event(event):
    if isinstance(event, ScenarioEvent):
        return event
    return ScenarioEvent(
        event_type=event.get("event_type"),
        direction=event.get("direction"),
        index=event.get("index"),
        quality_score=event.get("quality_score"),
        source=event.get("source"),
        payload=event.get("payload"),
    )


def _with_type(event, event_type):
    return ScenarioEvent(
        event_type=event_type,
        direction=event.direction,
        index=event.index,
        quality_score=event.quality_score,
        source=event.source,
        payload=event.payload,
    )


def _with_candidate_payload(event, candidate_id, *, anchor_index=None, early_trigger_index=None, confirmed_trigger_id=None):
    if event is None:
        return event
    payload = payload_to_dict(event.payload)
    payload.setdefault("candidate_id", candidate_id)
    payload.setdefault("candidate_anchor_index", anchor_index)
    if early_trigger_index is not None:
        payload.setdefault("early_trigger_index", _string_index(early_trigger_index))
    if _normalize_event_type(event.event_type) in ("FVG_CREATED", "FVG_RETESTED", "DISPLACEMENT_CONFIRMED"):
        payload.setdefault("source_candidate_id", candidate_id)
        if confirmed_trigger_id is not None:
            payload.setdefault("source_confirmed_trigger_id", confirmed_trigger_id)
    return ScenarioEvent(
        event_type=event.event_type,
        direction=event.direction,
        index=event.index,
        quality_score=event.quality_score,
        source=event.source,
        payload=payload,
    )


def _event_applies(direction, event):
    side = _normalize_side(event.direction)
    return side in (None, _direction_side(direction))


def _payload_bool(event, key):
    return bool(payload_to_dict(event.payload).get(key))


def _payload_reason(event, default):
    return payload_to_dict(event.payload).get("reason") or default


def _normalize_event_type(event_type):
    return str(event_type or "").upper()


def _normalize_side(direction):
    value = str(direction or "").lower()
    if value in ("long", "buy"):
        return "bullish"
    if value in ("short", "sell"):
        return "bearish"
    if value in ("bullish", "bearish", "neutral"):
        return value
    return None


def _direction_side(direction):
    return "bullish" if str(direction).upper() == "LONG" else "bearish"


def _opposite_side(direction):
    return "bearish" if str(direction).upper() == "LONG" else "bullish"


def _first_index(events, event_types):
    wanted = set(event_types)
    for event in events or []:
        if _normalize_event_type(event.event_type) in wanted:
            return event.index
    return None


def _first_used_event(events, event_types):
    wanted = set(event_types)
    for event in events or []:
        if _normalize_event_type(event.event_type) in wanted:
            return event
    return None


def _event_payload_or_snapshot(event, stage):
    if event is None:
        return None
    payload = payload_to_dict(event.payload)
    if not payload:
        payload = {
            "type": _trigger_type_from_event(event, stage),
            "direction": event.direction,
            "index": event.index,
            "quality_score": event.quality_score,
        }
    payload.setdefault("index", event.index)
    payload.setdefault("quality_score", event.quality_score)
    if payload.get("index") is not None:
        payload["index"] = _string_index(payload.get("index"))
    payload["trigger_stage"] = stage
    payload["is_early"] = stage == "early"
    payload["is_confirmed"] = stage == "confirmed"
    return payload


def _attach_candidate_metadata(payload, result, event=None, early_event=None):
    if payload is None:
        return payload
    if not payload.get("candidate_id"):
        payload["candidate_id"] = result.candidate_id
    if not payload.get("candidate_anchor_index"):
        payload["candidate_anchor_index"] = result.anchor_index
    if event is not None:
        payload.setdefault("event_id", _event_id(event))
    if early_event is not None:
        if not payload.get("early_trigger_index"):
            payload["early_trigger_index"] = _string_index(early_event.index)
    return payload


def _trigger_type_from_event(event, stage):
    side = _normalize_side(event.direction) or "trigger"
    if stage == "early":
        return f"{side}_early_choch"
    return f"{side}_bos"


def _missing_after_reason(direction):
    return "no_bullish_trigger_after_sfp_or_poi" if direction == "LONG" else "no_bearish_trigger_after_sfp_or_poi"


def _string_index(index):
    return str(index) if index is not None else None


def _last_index(events):
    if not events:
        return None
    return events[-1].index


def _event_sort_key(index):
    if index is None:
        return 0.0
    if isinstance(index, (int, float)):
        return float(index)
    try:
        return float(pd.Timestamp(index).value)
    except (TypeError, ValueError):
        return 0.0


def _get(obj, key, default=None):
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(key, default)
    if hasattr(obj, "get"):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _to_dict(obj):
    return payload_to_dict(obj) if obj is not None else None


def _risk_plan_matches_candidate(risk_payload, expected_candidate_id):
    source_candidate_id = (risk_payload or {}).get("source_candidate_id")
    if source_candidate_id is None:
        return False
    return expected_candidate_id is None or str(source_candidate_id) == str(expected_candidate_id)


def _market_age_bars(anchor_index, last_index):
    if anchor_index is None or last_index is None:
        return 0
    if isinstance(anchor_index, (int, float)) and isinstance(last_index, (int, float)):
        return max(0, int(float(last_index) - float(anchor_index)))
    anchor_ts = _timestamp_or_none(anchor_index)
    last_ts = _timestamp_or_none(last_index)
    if anchor_ts is None or last_ts is None or last_ts < anchor_ts:
        return 0
    return max(0, int((last_ts - anchor_ts) / pd.Timedelta(minutes=15)))


def _timestamp_or_none(value):
    try:
        return pd.Timestamp(value)
    except (TypeError, ValueError):
        return None


def _scenario_key_snapshot(key):
    if key is None:
        return None
    if isinstance(key, dict):
        return dict(key)
    values = list(key) if isinstance(key, (list, tuple)) else [key]
    return {
        "direction": values[0] if len(values) > 0 else None,
        "anchor_type": values[1] if len(values) > 1 else None,
        "anchor_index": values[2] if len(values) > 2 else None,
        "poi_id": values[3] if len(values) > 3 else None,
    }
