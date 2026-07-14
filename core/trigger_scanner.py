from dataclasses import dataclass
from typing import Any, Iterable, Literal, Optional

import pandas as pd

from core.structure import BOSConfig, _build_bos_result, find_swings


Direction = Literal["LONG", "SHORT", "NEUTRAL"]

CONFIRMED_LONG_TRIGGER_TYPES = {"bullish_bos", "bullish_choch"}
CONFIRMED_SHORT_TRIGGER_TYPES = {"bearish_bos", "bearish_choch"}
EARLY_LONG_TRIGGER_TYPES = {"bullish_early_choch", "bullish_mss", "bullish_micro_break", "bullish_reclaim"}
EARLY_SHORT_TRIGGER_TYPES = {"bearish_early_choch", "bearish_mss", "bearish_micro_break", "bearish_rejection"}


@dataclass
class TriggerScanResult:
    expected_direction: Direction
    selected_trigger: Optional[object]
    confirmed_trigger: Optional[object]
    early_trigger: Optional[object]
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
    early_trigger_confirmed: bool
    rejected_reason: Optional[str]
    waiting_for: Optional[str]
    confirmed_trigger_debug: Optional[dict] = None

    def to_dict(self) -> dict:
        return {
            "expected_direction": self.expected_direction,
            "selected_trigger": self.selected_trigger,
            "confirmed_trigger": self.confirmed_trigger,
            "early_trigger": self.early_trigger,
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
            "early_trigger_confirmed": self.early_trigger_confirmed,
            "rejected_reason": self.rejected_reason,
            "waiting_for": self.waiting_for,
            "confirmed_trigger_debug": self.confirmed_trigger_debug,
        }


def scan_post_anchor_trigger(
    expected_direction: Direction,
    sfp: Optional[dict] = None,
    poi: Optional[dict] = None,
    long_trigger_candidate: Optional[dict] = None,
    short_trigger_candidate: Optional[dict] = None,
    trigger_candidates: Optional[Iterable[dict]] = None,
    df_15m_closed: Optional[pd.DataFrame] = None,
    swing_points: Optional[object] = None,
    atr_series: Optional[pd.Series] = None,
    rvol_series: Optional[pd.Series] = None,
    config: Optional[object] = None,
    max_bars_after_sfp: int = 24,
    max_bars_after_poi: int = 24,
    min_trigger_quality: int = 70,
    min_early_trigger_quality: int = 55,
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
            confirmed_trigger=None,
            early_trigger=None,
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
            early_trigger_confirmed=False,
            rejected_reason="no_trade_direction",
            waiting_for=None,
        )

    confirmed_candidates = [item for item in candidates if _trigger_matches_direction(expected_direction, item)]
    early_candidates = [item for item in candidates if _early_trigger_matches_direction(expected_direction, item)]
    expected_candidates = confirmed_candidates + early_candidates
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
            confirmed_trigger=None,
            early_trigger=None,
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
            early_trigger_confirmed=False,
            rejected_reason="no_sfp_or_poi_anchor",
            waiting_for=waiting_for,
        )

    early_after_candidates = _after(early_candidates, anchor_index)
    early_after_anchor = _best_within_window(early_after_candidates, anchor_index, max_bars) or _best_trigger(early_after_candidates)
    early_trigger = early_after_anchor if _early_trigger_is_valid(expected_direction, early_after_anchor, anchor_index, max_bars, min_early_trigger_quality) else None
    confirmed_anchor_index = _event_index(early_trigger) if early_trigger else anchor_index
    generated_confirmed = _confirmed_trigger_candidates_after_early(
        df_15m_closed,
        expected_direction,
        early_trigger,
        anchor_index,
        swing_points=swing_points,
        atr_series=atr_series,
        rvol_series=rvol_series,
        config=config,
        max_bars=max_bars,
    )
    if generated_confirmed:
        confirmed_candidates = _compact_candidates(list(confirmed_candidates) + generated_confirmed, None, None)
    confirmed_debug = _confirmed_trigger_debug(
        expected_direction,
        confirmed_candidates,
        early_trigger,
        confirmed_anchor_index,
        max_bars,
        min_trigger_quality,
        df_15m_closed=df_15m_closed,
        swing_points=swing_points,
    )
    valid_confirmed_candidates = [
        item for item in confirmed_candidates
        if _confirmed_trigger_rejected_reason(
            expected_direction,
            item,
            early_trigger,
            confirmed_anchor_index,
            max_bars,
            min_trigger_quality,
        ) is None
    ]
    trigger_after_candidates = _after(valid_confirmed_candidates, confirmed_anchor_index)
    trigger_after_anchor = _best_within_window(trigger_after_candidates, confirmed_anchor_index, max_bars) or _best_trigger(trigger_after_candidates)
    if not trigger_after_anchor:
        early_result = _early_trigger_result(
            expected_direction,
            early_trigger,
            pre_sfp_trigger,
            post_sfp_trigger,
            pre_poi_trigger,
            post_poi_trigger,
            opposite_trigger,
            sfp_index,
            poi_index,
            anchor_index,
            max_bars,
            min_early_trigger_quality,
            confirmed_debug,
        )
        if early_result is not None:
            return early_result

        candidate_trigger = _best_before(confirmed_candidates, confirmed_anchor_index) or _best_trigger(confirmed_candidates)
        rejected_reason = _missing_after_reason(expected_direction) if opposite_after_anchor else (
            f"trigger_before_{anchor_kind}" if candidate_trigger else _missing_after_reason(expected_direction)
        )
        return TriggerScanResult(
            expected_direction=expected_direction,
            selected_trigger=None,
            confirmed_trigger=None,
            early_trigger=None,
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
            early_trigger_confirmed=False,
            rejected_reason=rejected_reason,
            waiting_for=waiting_for,
            confirmed_trigger_debug=confirmed_debug,
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
            early_trigger=early_trigger,
            confirmed_trigger_debug=confirmed_debug,
        )

    if not _within_confirmation_window(trigger_index, confirmed_anchor_index, max_bars):
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
            early_trigger=early_trigger,
            confirmed_trigger_debug=confirmed_debug,
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
            early_trigger=early_trigger,
            confirmed_trigger_debug=confirmed_debug,
        )

    return TriggerScanResult(
        expected_direction=expected_direction,
        selected_trigger=_mark_trigger_stage(trigger_after_anchor, "confirmed"),
        confirmed_trigger=_mark_trigger_stage(trigger_after_anchor, "confirmed"),
        early_trigger=_mark_trigger_stage(early_trigger, "early") if early_trigger else None,
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
        early_trigger_confirmed=True,
        rejected_reason=None,
        waiting_for=None,
        confirmed_trigger_debug=confirmed_debug,
    )


def find_confirmed_trigger_after_early(
    df_15m_closed,
    direction,
    early_trigger_index,
    candidate_anchor_index,
    swing_points,
    atr_series,
    rvol_series=None,
    config=None,
):
    candidates = _generate_confirmed_trigger_candidates_after_early(
        df_15m_closed,
        _normalize_direction(direction),
        early_trigger_index,
        candidate_anchor_index,
        swing_points=swing_points,
        atr_series=atr_series,
        rvol_series=rvol_series,
        config=config,
        max_bars=_config_value(config, "max_bars_after_early", 24),
    )
    valid = [item for item in candidates if bool(item.get("detected"))]
    return _best_trigger(valid) if valid else None


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
    early_trigger=None,
    confirmed_trigger_debug=None,
):
    early_trigger = _mark_trigger_stage(early_trigger, "early") if early_trigger else None
    return TriggerScanResult(
        expected_direction=expected_direction,
        selected_trigger=None,
        confirmed_trigger=None,
        early_trigger=early_trigger,
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
        early_trigger_confirmed=bool(early_trigger),
        rejected_reason=rejected_reason,
        waiting_for=waiting_for,
        confirmed_trigger_debug=confirmed_trigger_debug,
    )


def _early_trigger_result(
    expected_direction,
    early_trigger,
    pre_sfp_trigger,
    post_sfp_trigger,
    pre_poi_trigger,
    post_poi_trigger,
    opposite_trigger,
    sfp_index,
    poi_index,
    anchor_index,
    max_bars,
    min_early_trigger_quality,
    confirmed_trigger_debug=None,
):
    if not early_trigger:
        return None
    reason = _early_trigger_rejected_reason(expected_direction, early_trigger, anchor_index, max_bars, min_early_trigger_quality)
    if reason is not None:
        return None
    early_trigger = _mark_trigger_stage(early_trigger, "early")
    return TriggerScanResult(
        expected_direction=expected_direction,
        selected_trigger=None,
        confirmed_trigger=None,
        early_trigger=early_trigger,
        pre_sfp_trigger=pre_sfp_trigger,
        post_sfp_trigger=post_sfp_trigger,
        pre_poi_trigger=pre_poi_trigger,
        post_poi_trigger=post_poi_trigger,
        candidate_trigger=early_trigger,
        opposite_trigger=opposite_trigger,
        sfp_index=sfp_index,
        poi_index=poi_index,
        anchor_index=anchor_index,
        trigger_index=_event_index(early_trigger),
        trigger_confirmed=False,
        early_trigger_confirmed=True,
        rejected_reason="confirmed_trigger_missing",
        waiting_for=_waiting_for_confirmed_trigger(expected_direction),
        confirmed_trigger_debug=confirmed_trigger_debug,
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
        return trigger_type in CONFIRMED_LONG_TRIGGER_TYPES
    if expected_direction == "SHORT":
        return trigger_type in CONFIRMED_SHORT_TRIGGER_TYPES
    return False


def _early_trigger_matches_direction(expected_direction: Direction, trigger: Optional[dict]) -> bool:
    if not trigger:
        return False
    trigger_type = str(trigger.get("type", "")).lower()
    if expected_direction == "LONG":
        return trigger_type in EARLY_LONG_TRIGGER_TYPES
    if expected_direction == "SHORT":
        return trigger_type in EARLY_SHORT_TRIGGER_TYPES
    return False


def _trigger_is_opposite(expected_direction: Direction, trigger: Optional[dict]) -> bool:
    if expected_direction == "LONG":
        return _trigger_matches_direction("SHORT", trigger) or _early_trigger_matches_direction("SHORT", trigger)
    if expected_direction == "SHORT":
        return _trigger_matches_direction("LONG", trigger) or _early_trigger_matches_direction("LONG", trigger)
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


def _confirmed_trigger_candidates_after_early(
    df_15m_closed,
    expected_direction,
    early_trigger,
    candidate_anchor_index,
    *,
    swing_points=None,
    atr_series=None,
    rvol_series=None,
    config=None,
    max_bars=None,
):
    if not early_trigger:
        return []
    return _generate_confirmed_trigger_candidates_after_early(
        df_15m_closed,
        expected_direction,
        _event_index(early_trigger),
        candidate_anchor_index,
        early_trigger=early_trigger,
        swing_points=swing_points,
        atr_series=atr_series,
        rvol_series=rvol_series,
        config=config,
        max_bars=max_bars,
    )


def _generate_confirmed_trigger_candidates_after_early(
    df_15m_closed,
    expected_direction,
    early_trigger_index,
    candidate_anchor_index,
    *,
    early_trigger=None,
    swing_points=None,
    atr_series=None,
    rvol_series=None,
    config=None,
    max_bars=None,
):
    expected_direction = _normalize_direction(expected_direction)
    if expected_direction not in ("LONG", "SHORT"):
        return []
    if df_15m_closed is None or len(df_15m_closed) == 0 or early_trigger_index is None:
        return []

    df = _with_indicator_columns(df_15m_closed, atr_series, rvol_series)
    if df.empty:
        return []

    early_sort = _event_sort_key(early_trigger_index)
    after_early = df[_event_sort_key_index(df.index) > early_sort]
    if max_bars is not None:
        after_early = after_early.head(max_bars)
    if after_early.empty:
        return []

    swing_highs, swing_lows = _resolve_swing_points(df, swing_points)
    bos_config = config if isinstance(config, BOSConfig) else BOSConfig()
    direction = "bullish" if expected_direction == "LONG" else "bearish"
    candidates = []

    for index, candle in after_early.iterrows():
        if expected_direction == "LONG":
            level = _latest_confirmed_micro_level(swing_highs, "high", index, early_trigger_index)
            if level is None:
                level = _early_trigger_break_level(early_trigger)
            if level is None or float(candle.get("close", 0.0) or 0.0) <= level:
                continue
            trigger_type = "bullish_bos"
        else:
            level = _latest_confirmed_micro_level(swing_lows, "low", index, early_trigger_index)
            if level is None:
                level = _early_trigger_break_level(early_trigger)
            if level is None or float(candle.get("close", 0.0) or 0.0) >= level:
                continue
            trigger_type = "bearish_bos"

        future_candles = after_early[_event_sort_key_index(after_early.index) > _event_sort_key(index)]
        result = _build_bos_result(candle, trigger_type, direction, level, future_candles, bos_config)
        candidate = result.to_dict()
        candidate.update({
            "type": trigger_type,
            "direction": direction,
            "index": index,
            "level": round(float(level), 8),
            "candidate_anchor_index": candidate_anchor_index,
            "early_trigger_index": early_trigger_index,
            "trigger_stage": "confirmed",
            "is_early": False,
            "is_confirmed": bool(result.detected),
            "reason": "confirmed BOS candidate after early trigger",
        })
        candidates.append(candidate)

    return candidates


def _with_indicator_columns(df, atr_series=None, rvol_series=None):
    data = df.copy()
    if atr_series is not None and "atr" not in data.columns:
        data["atr"] = atr_series.reindex(data.index)
    if rvol_series is not None and "rvol" not in data.columns:
        data["rvol"] = rvol_series.reindex(data.index)
    return data


def _resolve_swing_points(df, swing_points):
    if isinstance(swing_points, tuple) and len(swing_points) == 2:
        return swing_points
    if isinstance(swing_points, dict):
        highs = swing_points.get("highs")
        if highs is None:
            highs = swing_points.get("swing_highs")
        lows = swing_points.get("lows")
        if lows is None:
            lows = swing_points.get("swing_lows")
        if highs is not None and lows is not None:
            return highs, lows
    return find_swings(df, left_bars=2, right_bars=1)


def _latest_confirmed_micro_level(swings, column, index, early_trigger_index):
    if swings is None or swings.empty:
        return None
    eligible = swings[
        (_event_sort_key_index(swings.index) > _event_sort_key(early_trigger_index))
        & (_event_sort_key_index(swings.index) < _event_sort_key(index))
    ]
    if eligible.empty:
        return None
    return float(eligible.iloc[-1][column])


def _early_trigger_break_level(early_trigger):
    if not early_trigger:
        return None
    for key in ("level", "break_level", "micro_swing_high", "micro_swing_low"):
        value = early_trigger.get(key)
        if value is not None:
            return float(value)
    return None


def _event_sort_key_index(index):
    return pd.Index([_event_sort_key(item) for item in index])


def _config_value(config, key, default):
    if isinstance(config, dict):
        return config.get(key, default)
    return getattr(config, key, default)


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


def _confirmed_trigger_debug(
    expected_direction,
    confirmed_candidates,
    early_trigger,
    search_anchor_index,
    max_bars,
    min_trigger_quality,
    *,
    df_15m_closed=None,
    swing_points=None,
):
    if not early_trigger:
        return None
    early_index = _event_index(early_trigger)
    context = _confirmed_trigger_search_context(
        df_15m_closed,
        expected_direction,
        early_trigger,
        max_bars,
        swing_points,
    )
    rejected = []
    historical_rejected = []
    valid_count = 0
    bos_count = 0
    choch_count = 0
    active_candidates = []
    for candidate in confirmed_candidates or []:
        reason = _confirmed_trigger_rejected_reason(
            expected_direction,
            candidate,
            early_trigger,
            search_anchor_index,
            max_bars,
            min_trigger_quality,
        )
        if reason == "before_early_trigger":
            historical_rejected.append(_candidate_debug_snapshot(candidate, reason))
            continue

        active_candidates.append(candidate)
        trigger_type = str(candidate.get("type", "")).lower()
        if "bos" in trigger_type:
            bos_count += 1
        if "choch" in trigger_type:
            choch_count += 1
        if reason is None:
            valid_count += 1
            continue
        rejected.append(_candidate_debug_snapshot(candidate, reason))

    _attach_candidate_debug_to_checked_candles(context, active_candidates, rejected)
    final_reason = None
    if valid_count == 0:
        final_reason = "no_confirmed_bos_after_early_trigger"
        if context.get("candles_after_early") == 0:
            final_reason = "not_enough_candles_after_early_trigger"
        elif context.get("break_level") is None and context.get("candles_after_early") is not None:
            final_reason = "no_confirmed_break_level_after_early_trigger"
        elif context.get("candles_after_early") is not None and not rejected and bos_count == 0 and choch_count == 0:
            final_reason = "no_candle_closed_beyond_break_level"
        if rejected:
            final_reason = rejected[0].get("rejected_reason") or final_reason

    return {
        "early_trigger_index": _string_index(early_index),
        "search_window_start": _string_index(early_index),
        "search_window_end": _string_index(_window_end_index(early_index, max_bars)),
        "expected_direction": expected_direction,
        **context,
        "candidate_bos_count": bos_count,
        "candidate_choch_count": choch_count,
        "valid_candidate_count": valid_count,
        "rejected_candidates": rejected,
        "historical_rejected_candidates": historical_rejected,
        "final_reason": final_reason,
    }


def _candidate_debug_snapshot(candidate, reason):
    return {
        "type": candidate.get("type"),
        "index": _string_index(_event_index(candidate)),
        "quality_score": candidate.get("quality_score"),
        "rejected_reason": reason,
        "body_ratio": candidate.get("body_ratio"),
        "close_position": candidate.get("close_position"),
        "displacement_ratio": candidate.get("displacement_ratio"),
    }


def _confirmed_trigger_rejected_reason(
    expected_direction,
    trigger,
    early_trigger,
    search_anchor_index,
    max_bars,
    min_trigger_quality,
):
    if not trigger:
        return "no_confirmed_trigger"
    if not early_trigger:
        return None
    if not _trigger_matches_direction(expected_direction, trigger):
        return "direction_conflict"
    trigger_index = _event_index(trigger)
    early_index = _event_index(early_trigger)
    if trigger_index is None:
        return "missing_index"
    if early_index is None or _event_sort_key(trigger_index) <= _event_sort_key(early_index):
        return "before_early_trigger"
    if not _within_confirmation_window(trigger_index, search_anchor_index, max_bars):
        return "outside_confirmation_window"
    if _trigger_quality(trigger) < min_trigger_quality:
        return "quality_below_min"
    if bool(trigger.get("absorption_warning")):
        return "absorption_warning"
    if "detected" in trigger and not bool(trigger.get("detected")):
        return "quality_below_min"
    return None


def _confirmed_trigger_search_context(df_15m_closed, expected_direction, early_trigger, max_bars, swing_points):
    early_index = _event_index(early_trigger)
    if df_15m_closed is None or early_index is None:
        return {"generator_called": False}
    if len(df_15m_closed) == 0:
        return {
            "generator_called": True,
            "candles_after_early": 0,
            "first_candle_after_early": None,
            "last_candle_after_early": None,
            "micro_swing_high": None,
            "micro_swing_low": None,
            "break_level": None,
            "checked_candles": [],
        }
    after_early = df_15m_closed[_event_sort_key_index(df_15m_closed.index) > _event_sort_key(early_index)]
    if max_bars is not None:
        after_early = after_early.head(max_bars)
    swing_highs, swing_lows = _resolve_swing_points(df_15m_closed, swing_points)
    end_index = after_early.index[-1] if not after_early.empty else None
    high_level = _latest_window_level(swing_highs, "high", early_index, end_index)
    low_level = _latest_window_level(swing_lows, "low", early_index, end_index)
    swing_break_level = high_level if expected_direction == "LONG" else low_level
    break_level = swing_break_level
    if break_level is None:
        break_level = _early_trigger_break_level(early_trigger)
    return {
        "generator_called": True,
        "candles_after_early": int(len(after_early)),
        "first_candle_after_early": _string_index(after_early.index[0]) if not after_early.empty else None,
        "last_candle_after_early": _string_index(after_early.index[-1]) if not after_early.empty else None,
        "micro_swing_high": _round_optional(high_level),
        "micro_swing_low": _round_optional(low_level),
        "break_level": _round_optional(break_level),
        "checked_candles": _checked_candle_debug(after_early, expected_direction, break_level),
    }


def _checked_candle_debug(after_early, expected_direction, break_level):
    checked = []
    for index, candle in after_early.iterrows():
        candle_range = float(candle.get("high", 0.0) or 0.0) - float(candle.get("low", 0.0) or 0.0)
        open_price = float(candle.get("open", 0.0) or 0.0)
        close_price = float(candle.get("close", 0.0) or 0.0)
        high_price = float(candle.get("high", 0.0) or 0.0)
        low_price = float(candle.get("low", 0.0) or 0.0)
        atr = float(candle.get("atr", 0.0) or 0.0)
        body = abs(close_price - open_price)
        body_ratio = body / candle_range if candle_range > 0 else 0.0
        displacement_ratio = body / atr if atr > 0 else 0.0
        if expected_direction == "LONG":
            direction_ok = close_price > open_price
            close_position = (close_price - low_price) / candle_range if candle_range > 0 else 0.0
            breaks_level = break_level is not None and close_price > float(break_level)
        else:
            direction_ok = close_price < open_price
            close_position = (high_price - close_price) / candle_range if candle_range > 0 else 0.0
            breaks_level = break_level is not None and close_price < float(break_level)
        checked.append({
            "index": _string_index(index),
            "close": _round_optional(close_price),
            "high": _round_optional(high_price),
            "low": _round_optional(low_price),
            "body_ratio": round(body_ratio, 4),
            "close_position": round(max(0.0, min(close_position, 1.0)), 4),
            "displacement_ratio": round(displacement_ratio, 4),
            "rvol": _round_optional(candle.get("rvol")),
            "breaks_level": bool(breaks_level),
            "direction_ok": bool(direction_ok),
            "candidate_created": False,
        })
    return checked


def _attach_candidate_debug_to_checked_candles(context, confirmed_candidates, rejected_candidates):
    checked = context.get("checked_candles") or []
    if not checked:
        return
    candidates_by_index = {
        _string_index(_event_index(candidate)): candidate
        for candidate in confirmed_candidates or []
        if _event_index(candidate) is not None
    }
    rejected_by_index = {
        item.get("index"): item
        for item in rejected_candidates or []
        if item.get("index") is not None
    }
    for candle in checked:
        index = candle.get("index")
        candidate = candidates_by_index.get(index)
        if not candidate:
            continue
        candle["candidate_created"] = True
        candle["quality_score"] = candidate.get("quality_score")
        candle["absorption_warning"] = bool(candidate.get("absorption_warning"))
        if index in rejected_by_index:
            candle["rejected_reason"] = rejected_by_index[index].get("rejected_reason")


def _latest_window_level(swings, column, start_index, end_index):
    if swings is None or swings.empty or end_index is None:
        return None
    eligible = swings[
        (_event_sort_key_index(swings.index) > _event_sort_key(start_index))
        & (_event_sort_key_index(swings.index) <= _event_sort_key(end_index))
    ]
    if eligible.empty:
        return None
    return float(eligible.iloc[-1][column])


def _round_optional(value):
    if value is None:
        return None
    return round(float(value), 8)


def _window_end_index(index, max_bars):
    if index is None or max_bars is None:
        return None
    try:
        if isinstance(index, (int, float)):
            return index + max_bars
        return pd.Timestamp(index) + pd.Timedelta(minutes=max_bars * 15)
    except (TypeError, ValueError):
        return None


def _string_index(index):
    return str(index) if index is not None else None


def _early_trigger_is_valid(expected_direction, trigger, anchor_index, max_bars, min_early_trigger_quality):
    return _early_trigger_rejected_reason(expected_direction, trigger, anchor_index, max_bars, min_early_trigger_quality) is None


def _early_trigger_rejected_reason(expected_direction, trigger, anchor_index, max_bars, min_early_trigger_quality):
    if not trigger:
        return "no_early_trigger_after_sfp_or_poi"
    if not _early_trigger_matches_direction(expected_direction, trigger):
        return "early_trigger_direction_conflict"
    trigger_index = _event_index(trigger)
    if anchor_index is None or trigger_index is None or _event_sort_key(trigger_index) <= _event_sort_key(anchor_index):
        return "early_trigger_before_sfp"
    if not _within_confirmation_window(trigger_index, anchor_index, max_bars):
        return "early_trigger_outside_confirmation_window"
    if _trigger_quality(trigger) < min_early_trigger_quality:
        return "early_trigger_quality_below_min"
    if bool(trigger.get("absorption_warning")):
        return "early_trigger_absorption_warning"
    body_ratio = trigger.get("body_ratio")
    if body_ratio is not None and float(body_ratio or 0.0) < 0.45:
        return "early_trigger_quality_below_min"
    displacement_ratio = trigger.get("displacement_ratio")
    if displacement_ratio is not None and float(displacement_ratio or 0.0) < 0.5:
        return "early_trigger_quality_below_min"
    if not _has_early_confirmation(trigger):
        return "early_trigger_quality_below_min"
    return None


def _has_early_confirmation(trigger):
    rvol = trigger.get("rvol")
    close_position = trigger.get("close_position")
    if rvol is not None and float(rvol or 0.0) >= 1.2:
        return True
    if trigger.get("reclaim_confirmed") or trigger.get("micro_break_confirmed"):
        return True
    if close_position is not None:
        close_position = float(close_position or 0.0)
        trigger_type = str(trigger.get("type", "")).lower()
        if trigger_type.startswith("bullish") and close_position >= 0.6:
            return True
        if trigger_type.startswith("bearish") and close_position <= 0.4:
            return True
    return False


def _mark_trigger_stage(trigger, stage):
    if not trigger:
        return None
    data = dict(trigger)
    data["trigger_stage"] = stage
    data["is_early"] = stage == "early"
    data["is_confirmed"] = stage == "confirmed"
    return data


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


def _waiting_for_confirmed_trigger(expected_direction: Direction) -> str:
    direction_word = "bullish" if expected_direction == "LONG" else "bearish"
    return f"confirmed {direction_word} BOS after early CHOCH"
