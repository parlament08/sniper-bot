from dataclasses import dataclass
from typing import Any, Iterable, Literal, Optional

import pandas as pd


Direction = Literal["LONG", "SHORT", "NEUTRAL"]

VALID_LONG_TRIGGER_TYPES = {"bullish_bos", "bullish_choch", "bullish_mss"}
VALID_SHORT_TRIGGER_TYPES = {"bearish_bos", "bearish_choch", "bearish_mss"}


@dataclass
class TriggerScanResult:
    expected_direction: Direction
    selected_trigger: Optional[object]
    pre_sfp_trigger: Optional[object]
    post_sfp_trigger: Optional[object]
    pre_poi_trigger: Optional[object]
    post_poi_trigger: Optional[object]
    candidate_trigger: Optional[object]
    opposite_trigger: Optional[object]
    sfp_index: Optional[Any]
    poi_index: Optional[Any]
    anchor_index: Optional[Any]
    trigger_index: Optional[Any]
    trigger_confirmed: bool
    rejected_reason: Optional[str]
    waiting_for: Optional[str]

    def to_dict(self) -> dict:
        return {
            "expected_direction": self.expected_direction,
            "selected_trigger": self.selected_trigger,
            "pre_sfp_trigger": self.pre_sfp_trigger,
            "post_sfp_trigger": self.post_sfp_trigger,
            "pre_poi_trigger": self.pre_poi_trigger,
            "post_poi_trigger": self.post_poi_trigger,
            "candidate_trigger": self.candidate_trigger,
            "opposite_trigger": self.opposite_trigger,
            "sfp_index": self.sfp_index,
            "poi_index": self.poi_index,
            "anchor_index": self.anchor_index,
            "trigger_index": self.trigger_index,
            "trigger_confirmed": self.trigger_confirmed,
            "rejected_reason": self.rejected_reason,
            "waiting_for": self.waiting_for,
        }


def scan_post_anchor_trigger(
    expected_direction: Direction,
    sfp: Optional[dict] = None,
    poi: Optional[dict] = None,
    long_trigger_candidate: Optional[dict] = None,
    short_trigger_candidate: Optional[dict] = None,
    trigger_candidates: Optional[Iterable[dict]] = None,
    max_bars_after_sfp: int = 24,
    max_bars_after_poi: int = 24,
    min_trigger_quality: int = 70,
) -> TriggerScanResult:
    expected_direction = _normalize_direction(expected_direction)
    sfp_index = _event_index(sfp)
    poi_index = _event_index(poi)
    anchor_kind, anchor_index, max_bars = _select_anchor(
        sfp_index,
        poi_index,
        max_bars_after_sfp=max_bars_after_sfp,
        max_bars_after_poi=max_bars_after_poi,
    )
    candidates = _compact_candidates(trigger_candidates, long_trigger_candidate, short_trigger_candidate)

    if expected_direction == "NEUTRAL":
        candidate_trigger = _best_trigger(candidates)
        return TriggerScanResult(
            expected_direction=expected_direction,
            selected_trigger=None,
            pre_sfp_trigger=None,
            post_sfp_trigger=None,
            pre_poi_trigger=None,
            post_poi_trigger=None,
            candidate_trigger=candidate_trigger,
            opposite_trigger=None,
            sfp_index=sfp_index,
            poi_index=poi_index,
            anchor_index=anchor_index,
            trigger_index=None,
            trigger_confirmed=False,
            rejected_reason="no_trade_direction",
            waiting_for=None,
        )

    expected_candidates = [item for item in candidates if _trigger_matches_direction(expected_direction, item)]
    opposite_candidates = [item for item in candidates if _trigger_is_opposite(expected_direction, item)]
    opposite_after_anchor = _best_after(opposite_candidates, anchor_index)
    opposite_trigger = opposite_after_anchor or _best_trigger(opposite_candidates)
    waiting_for = _waiting_for(expected_direction)

    pre_sfp_trigger = _best_before(expected_candidates, sfp_index) if sfp_index is not None else None
    post_sfp_trigger = _best_after(expected_candidates, sfp_index) if sfp_index is not None else None
    pre_poi_trigger = _best_before(expected_candidates, poi_index) if poi_index is not None else None
    post_poi_trigger = _best_after(expected_candidates, poi_index) if poi_index is not None else None

    if anchor_index is None:
        candidate_trigger = _best_trigger(expected_candidates)
        return TriggerScanResult(
            expected_direction=expected_direction,
            selected_trigger=None,
            pre_sfp_trigger=pre_sfp_trigger,
            post_sfp_trigger=post_sfp_trigger,
            pre_poi_trigger=pre_poi_trigger,
            post_poi_trigger=post_poi_trigger,
            candidate_trigger=candidate_trigger,
            opposite_trigger=opposite_trigger,
            sfp_index=sfp_index,
            poi_index=poi_index,
            anchor_index=anchor_index,
            trigger_index=None,
            trigger_confirmed=False,
            rejected_reason="no_sfp_or_poi_anchor",
            waiting_for=waiting_for,
        )

    trigger_after_candidates = _after(expected_candidates, anchor_index)
    trigger_after_anchor = _best_within_window(trigger_after_candidates, anchor_index, max_bars) or _best_trigger(trigger_after_candidates)
    if not trigger_after_anchor:
        candidate_trigger = _best_before(expected_candidates, anchor_index) or _best_trigger(expected_candidates)
        rejected_reason = _missing_after_reason(expected_direction) if opposite_after_anchor else (
            f"trigger_before_{anchor_kind}" if candidate_trigger else _missing_after_reason(expected_direction)
        )
        return TriggerScanResult(
            expected_direction=expected_direction,
            selected_trigger=None,
            pre_sfp_trigger=pre_sfp_trigger,
            post_sfp_trigger=post_sfp_trigger,
            pre_poi_trigger=pre_poi_trigger,
            post_poi_trigger=post_poi_trigger,
            candidate_trigger=candidate_trigger,
            opposite_trigger=opposite_trigger,
            sfp_index=sfp_index,
            poi_index=poi_index,
            anchor_index=anchor_index,
            trigger_index=None,
            trigger_confirmed=False,
            rejected_reason=rejected_reason,
            waiting_for=waiting_for,
        )

    trigger_index = _event_index(trigger_after_anchor)
    if _trigger_quality(trigger_after_anchor) < min_trigger_quality:
        return _rejected_result(
            expected_direction,
            trigger_after_anchor,
            pre_sfp_trigger,
            post_sfp_trigger,
            pre_poi_trigger,
            post_poi_trigger,
            trigger_after_anchor,
            opposite_trigger,
            sfp_index,
            poi_index,
            anchor_index,
            "trigger_quality_below_min",
            waiting_for,
        )

    if not _within_confirmation_window(trigger_index, anchor_index, max_bars):
        return _rejected_result(
            expected_direction,
            trigger_after_anchor,
            pre_sfp_trigger,
            post_sfp_trigger,
            pre_poi_trigger,
            post_poi_trigger,
            trigger_after_anchor,
            opposite_trigger,
            sfp_index,
            poi_index,
            anchor_index,
            "trigger_outside_confirmation_window",
            waiting_for,
        )

    if not _trigger_matches_direction(expected_direction, trigger_after_anchor):
        return _rejected_result(
            expected_direction,
            trigger_after_anchor,
            pre_sfp_trigger,
            post_sfp_trigger,
            pre_poi_trigger,
            post_poi_trigger,
            trigger_after_anchor,
            opposite_trigger,
            sfp_index,
            poi_index,
            anchor_index,
            "trigger_direction_conflict",
            waiting_for,
        )

    return TriggerScanResult(
        expected_direction=expected_direction,
        selected_trigger=trigger_after_anchor,
        pre_sfp_trigger=pre_sfp_trigger,
        post_sfp_trigger=post_sfp_trigger,
        pre_poi_trigger=pre_poi_trigger,
        post_poi_trigger=post_poi_trigger,
        candidate_trigger=trigger_after_anchor,
        opposite_trigger=opposite_trigger,
        sfp_index=sfp_index,
        poi_index=poi_index,
        anchor_index=anchor_index,
        trigger_index=trigger_index,
        trigger_confirmed=True,
        rejected_reason=None,
        waiting_for=None,
    )


def _rejected_result(
    expected_direction,
    trigger,
    pre_sfp_trigger,
    post_sfp_trigger,
    pre_poi_trigger,
    post_poi_trigger,
    candidate_trigger,
    opposite_trigger,
    sfp_index,
    poi_index,
    anchor_index,
    rejected_reason,
    waiting_for,
):
    return TriggerScanResult(
        expected_direction=expected_direction,
        selected_trigger=None,
        pre_sfp_trigger=pre_sfp_trigger,
        post_sfp_trigger=post_sfp_trigger,
        pre_poi_trigger=pre_poi_trigger,
        post_poi_trigger=post_poi_trigger,
        candidate_trigger=candidate_trigger,
        opposite_trigger=opposite_trigger,
        sfp_index=sfp_index,
        poi_index=poi_index,
        anchor_index=anchor_index,
        trigger_index=_event_index(trigger),
        trigger_confirmed=False,
        rejected_reason=rejected_reason,
        waiting_for=waiting_for,
    )


def _normalize_direction(direction) -> Direction:
    value = str(direction or "NEUTRAL").upper()
    if value in ("LONG", "SHORT"):
        return value
    return "NEUTRAL"


def _event_index(event):
    return event.get("index") if event else None


def _trigger_quality(trigger) -> int:
    return int(trigger.get("quality_score", 0) or 0) if trigger else 0


def _trigger_matches_direction(expected_direction: Direction, trigger: Optional[dict]) -> bool:
    if not trigger:
        return False
    trigger_type = str(trigger.get("type", "")).lower()
    if expected_direction == "LONG":
        return trigger_type in VALID_LONG_TRIGGER_TYPES
    if expected_direction == "SHORT":
        return trigger_type in VALID_SHORT_TRIGGER_TYPES
    return False


def _trigger_is_opposite(expected_direction: Direction, trigger: Optional[dict]) -> bool:
    if expected_direction == "LONG":
        return _trigger_matches_direction("SHORT", trigger)
    if expected_direction == "SHORT":
        return _trigger_matches_direction("LONG", trigger)
    return False


def _compact_candidates(
    trigger_candidates: Optional[Iterable[dict]],
    long_trigger_candidate: Optional[dict],
    short_trigger_candidate: Optional[dict],
) -> list:
    candidates = []
    seen = set()
    for trigger in list(trigger_candidates or []) + [long_trigger_candidate, short_trigger_candidate]:
        if not trigger:
            continue
        key = (trigger.get("type"), str(trigger.get("index")), trigger.get("quality_score"))
        if key in seen:
            continue
        seen.add(key)
        candidates.append(trigger)
    return candidates


def _select_anchor(sfp_index, poi_index, max_bars_after_sfp: int, max_bars_after_poi: int):
    if sfp_index is not None:
        return "sfp", sfp_index, max_bars_after_sfp
    if poi_index is not None:
        return "poi", poi_index, max_bars_after_poi
    return None, None, None


def _best_trigger(candidates):
    candidates = [item for item in (candidates or []) if item]
    if not candidates:
        return None
    return max(candidates, key=_trigger_rank)


def _best_before(candidates, anchor_index):
    if anchor_index is None:
        return None
    return _best_trigger([
        item for item in (candidates or [])
        if _event_index(item) is not None and _event_sort_key(_event_index(item)) <= _event_sort_key(anchor_index)
    ])


def _best_after(candidates, anchor_index):
    return _best_trigger(_after(candidates, anchor_index))


def _after(candidates, anchor_index):
    if anchor_index is None:
        return []
    return [
        item for item in (candidates or [])
        if _event_index(item) is not None and _event_sort_key(_event_index(item)) > _event_sort_key(anchor_index)
    ]


def _best_within_window(candidates, anchor_index, max_bars):
    return _best_trigger([
        item for item in (candidates or [])
        if _within_confirmation_window(_event_index(item), anchor_index, max_bars)
    ])


def _trigger_rank(trigger):
    return (_trigger_quality(trigger), _event_sort_key(_event_index(trigger)))


def _relative_trigger(trigger, anchor_index, before: bool):
    if not trigger or anchor_index is None:
        return None
    trigger_index = _event_index(trigger)
    if trigger_index is None:
        return None
    is_before_or_equal = _event_sort_key(trigger_index) <= _event_sort_key(anchor_index)
    if before and is_before_or_equal:
        return trigger
    if not before and not is_before_or_equal:
        return trigger
    return None


def _within_confirmation_window(trigger_index, anchor_index, max_bars: Optional[int]) -> bool:
    if max_bars is None or trigger_index is None or anchor_index is None:
        return True
    try:
        if isinstance(trigger_index, (int, float)) and isinstance(anchor_index, (int, float)):
            if trigger_index > 1e11 or anchor_index > 1e11:
                return (trigger_index - anchor_index) <= max_bars * 15 * 60 * 1000
            if trigger_index > 1e8 or anchor_index > 1e8:
                return (trigger_index - anchor_index) <= max_bars * 15 * 60
            return (trigger_index - anchor_index) <= max_bars
        return (pd.Timestamp(trigger_index) - pd.Timestamp(anchor_index)) <= pd.Timedelta(minutes=max_bars * 15)
    except (TypeError, ValueError):
        return True


def _event_sort_key(index):
    if index is None:
        return 0.0
    if isinstance(index, (int, float)):
        return float(index)
    try:
        return float(pd.Timestamp(index).value)
    except (TypeError, ValueError):
        return 0.0


def _missing_after_reason(expected_direction: Direction) -> str:
    if expected_direction == "LONG":
        return "no_bullish_trigger_after_sfp_or_poi"
    if expected_direction == "SHORT":
        return "no_bearish_trigger_after_sfp_or_poi"
    return "no_trade_direction"


def _waiting_for(expected_direction: Direction) -> str:
    direction_word = "bullish" if expected_direction == "LONG" else "bearish"
    return f"{direction_word} CHOCH/BOS after SFP/POI"
