#!/usr/bin/env python3
"""Offline Early Trigger quality audit from local scan journals."""

from __future__ import annotations

import argparse
import csv
import glob
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from statistics import median
from typing import Any, Iterable, Optional

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.structure import BOSConfig, find_swings
from research.analyze_candidate_funnel import (
    build_candidates,
    candidate_items,
    load_records,
    nested_get,
    normalize_direction,
    percentile,
    to_float,
    write_csv,
)


MAX_BARS_AFTER_SFP = 24
MAX_BARS_AFTER_POI = 24
MIN_EARLY_TRIGGER_QUALITY = 55
MIN_EARLY_TRIGGER_BODY_RATIO = 0.45
MIN_EARLY_TRIGGER_DISPLACEMENT_ATR = 0.5
MIN_EARLY_TRIGGER_RVOL = 1.2
EARLY_LONG_TRIGGER_TYPES = {"bullish_early_choch", "bullish_mss", "bullish_micro_break", "bullish_reclaim"}
EARLY_SHORT_TRIGGER_TYPES = {"bearish_early_choch", "bearish_mss", "bearish_micro_break", "bearish_rejection"}
GENERATED_EARLY_TRIGGER_TYPES = {"bullish_early_choch", "bearish_early_choch"}
HORIZONS = (4, 8, 16, 32)


def parse_ts(value: Any) -> Optional[pd.Timestamp]:
    if value in (None, ""):
        return None
    try:
        return pd.Timestamp(value).tz_localize(None)
    except (TypeError, ValueError):
        return None


def ts_text(value: Any) -> Optional[str]:
    ts = parse_ts(value)
    return str(ts) if ts is not None else (str(value) if value not in (None, "") else None)


def event_payload(event: dict[str, Any]) -> dict[str, Any]:
    payload = event.get("payload")
    return payload if isinstance(payload, dict) else {}


def candidate_events(candidate: dict[str, Any]) -> list[dict[str, Any]]:
    return [event for event in candidate.get("events_used") or [] if isinstance(event, dict)]


def first_event(candidate: dict[str, Any], event_types: Iterable[str]) -> Optional[dict[str, Any]]:
    wanted = set(event_types)
    events = [event for event in candidate_events(candidate) if event.get("event_type") in wanted]
    if not events:
        return None
    return sorted(events, key=lambda item: parse_ts(item.get("index")) or pd.Timestamp.min)[0]


def trigger_scan(candidate: dict[str, Any]) -> dict[str, Any]:
    scan = candidate.get("trigger_scan")
    return scan if isinstance(scan, dict) else {}


def early_trigger(candidate: dict[str, Any]) -> Optional[dict[str, Any]]:
    scan = trigger_scan(candidate)
    if isinstance(scan.get("early_trigger"), dict):
        return scan["early_trigger"]
    event = first_event(candidate, ("EARLY_TRIGGER_CONFIRMED",))
    if event:
        payload = event_payload(event)
        return payload or {"index": event.get("index"), "quality_score": event.get("quality_score")}
    return None


def confirmed_trigger(candidate: dict[str, Any]) -> Optional[dict[str, Any]]:
    scan = trigger_scan(candidate)
    if isinstance(scan.get("confirmed_trigger"), dict):
        return scan["confirmed_trigger"]
    event = first_event(candidate, ("CONFIRMED_TRIGGER_CONFIRMED", "CHOCH_CONFIRMED", "BOS_CONFIRMED"))
    if event:
        payload = event_payload(event)
        return payload or {"index": event.get("index"), "quality_score": event.get("quality_score")}
    return None


def sfp_event(candidate: dict[str, Any]) -> Optional[dict[str, Any]]:
    event = first_event(candidate, ("SFP_CONFIRMED", "LIQUIDITY_SWEEP_CONFIRMED"))
    if event:
        payload = event_payload(event)
        return payload or {"index": event.get("index"), "quality_score": event.get("quality_score")}
    return None


def has_sfp(candidate: dict[str, Any]) -> bool:
    if sfp_event(candidate):
        return True
    stages = {event.get("event_type") for event in candidate_events(candidate)}
    return bool({"EARLY_TRIGGER_CONFIRMED", "CONFIRMED_TRIGGER_CONFIRMED", "FVG_CREATED"} & stages)


def candidate_key(symbol: str, candidate: dict[str, Any]) -> str:
    if candidate.get("candidate_id"):
        return str(candidate["candidate_id"])
    return "|".join(
        [
            symbol or "unknown",
            normalize_direction(candidate.get("direction")),
            str(candidate.get("anchor_index") or ""),
            str(candidate.get("candidate_created_at") or ""),
        ]
    )


def dedupe_sfp_candidates(rows: Iterable[tuple[str, dict[str, Any]]]) -> dict[str, dict[str, Any]]:
    found: dict[str, dict[str, Any]] = {}
    for path, record in rows:
        if record.get("record_type") != "symbol_scan":
            continue
        symbol = str(record.get("symbol") or "unknown")
        for candidate in candidate_items(record):
            if not has_sfp(candidate):
                continue
            key = candidate_key(symbol, candidate)
            current = found.setdefault(
                key,
                {
                    "candidate_id": str(candidate.get("candidate_id") or key),
                    "symbol": symbol,
                    "direction": normalize_direction(candidate.get("direction")),
                    "scenario_type": candidate.get("anchor_type") or nested_get(candidate, "scenario_key.anchor_type") or "unknown",
                    "sfp_timestamp": None,
                    "sfp_quality": None,
                    "poi": None,
                    "htf_regime": record.get("htf_context_class") or nested_get(record, "features.htf_context.direction") or "unknown",
                    "candidate_created_at": candidate.get("candidate_created_at") or candidate.get("anchor_index"),
                    "first_seen_at": record.get("timestamp"),
                    "last_seen_at": record.get("timestamp"),
                    "expired_active_invalidated": candidate.get("status"),
                    "early_trigger_detected": False,
                    "early_trigger_confirmed": False,
                    "trigger_type": None,
                    "trigger_timestamp": None,
                    "primary_stop_reason": None,
                    "scan_cycles": 0,
                    "max_market_age_bars": 0,
                    "max_runtime_update_count": 0,
                    "source_files": set(),
                    "_rows": [],
                    "_candidate": candidate,
                },
            )
            event = sfp_event(candidate)
            if event:
                current["sfp_timestamp"] = current["sfp_timestamp"] or ts_text(event.get("index"))
                current["sfp_quality"] = current["sfp_quality"] if current["sfp_quality"] is not None else event.get("quality_score")
            scan = trigger_scan(candidate)
            early = early_trigger(candidate)
            confirmed = confirmed_trigger(candidate)
            current["poi"] = current["poi"] or scan.get("poi_index")
            current["last_seen_at"] = max(str(current["last_seen_at"] or ""), str(record.get("timestamp") or ""))
            current["expired_active_invalidated"] = candidate.get("status") or current["expired_active_invalidated"]
            current["early_trigger_detected"] = current["early_trigger_detected"] or bool(early)
            current["early_trigger_confirmed"] = current["early_trigger_confirmed"] or bool(scan.get("early_trigger_confirmed") or early)
            if early and not current["trigger_timestamp"]:
                current["trigger_type"] = early.get("type")
                current["trigger_timestamp"] = ts_text(early.get("index"))
            current["primary_stop_reason"] = scan.get("rejected_reason") or candidate.get("invalidated_reason") or candidate.get("waiting_for") or current["primary_stop_reason"]
            current["scan_cycles"] += 1
            current["max_market_age_bars"] = max(int(current["max_market_age_bars"] or 0), int(candidate.get("market_age_bars") or candidate.get("age_bars") or 0))
            current["max_runtime_update_count"] = max(int(current["max_runtime_update_count"] or 0), int(candidate.get("runtime_update_count") or 0))
            current["source_files"].add(path)
            current["_rows"].append((path, record, candidate))
            if confirmed:
                current["confirmed_trigger_timestamp"] = ts_text(confirmed.get("index"))
    return found


def load_symbol_candles(rows: Iterable[tuple[str, dict[str, Any]]]) -> dict[str, pd.DataFrame]:
    by_symbol: dict[str, dict[pd.Timestamp, dict[str, Any]]] = defaultdict(dict)
    for _path, record in rows:
        if record.get("record_type") != "symbol_scan":
            continue
        symbol = record.get("symbol")
        ts = parse_ts(record.get("market_data_timestamp_15m"))
        if not symbol or ts is None:
            continue
        required = ("market_open_15m", "market_high_15m", "market_low_15m", "market_close_15m")
        if any(record.get(key) is None for key in required):
            continue
        by_symbol[str(symbol)][ts] = {
            "open": float(record["market_open_15m"]),
            "high": float(record["market_high_15m"]),
            "low": float(record["market_low_15m"]),
            "close": float(record["market_close_15m"]),
            "volume": to_float(record.get("volume")) or to_float(record.get("market_volume_15m")),
            "atr": to_float(record.get("atr")),
            "rvol": to_float(record.get("rvol")),
        }
    return {symbol: pd.DataFrame.from_dict(items, orient="index").sort_index() for symbol, items in by_symbol.items()}


def early_quality(body_ratio: float, displacement_ratio: float, rvol: float, close_position: float, direction: str) -> int:
    quality = 40
    quality += min(body_ratio * 20, 20)
    quality += min(displacement_ratio * 15, 20)
    quality += min(rvol * 5, 10)
    if direction == "bullish":
        quality += max(0.0, close_position - 0.5) * 20
    else:
        quality += max(0.0, 0.5 - close_position) * 20
    return int(round(max(0, min(100, quality))))


def latest_micro_level(swings: pd.DataFrame, column: str, index: Any, anchor: Any) -> Optional[float]:
    if swings is None or swings.empty:
        return None
    idx = parse_ts(index)
    anchor_ts = parse_ts(anchor)
    if idx is None or anchor_ts is None:
        return None
    eligible = swings[(swings.index > anchor_ts) & (swings.index < idx)]
    if eligible.empty:
        return None
    return float(eligible.iloc[-1][column])


def classify_early_candle(candle: pd.Series, index: Any, direction: str, micro_highs: pd.DataFrame, micro_lows: pd.DataFrame, anchor: Any) -> dict[str, Any]:
    open_price = float(candle.get("open", 0.0) or 0.0)
    high_price = float(candle.get("high", 0.0) or 0.0)
    low_price = float(candle.get("low", 0.0) or 0.0)
    close_price = float(candle.get("close", 0.0) or 0.0)
    candle_range = high_price - low_price
    body_size = abs(close_price - open_price)
    body_ratio = body_size / candle_range if candle_range > 0 else 0.0
    atr = float(candle.get("atr", 0.0) or 0.0)
    rvol = float(candle.get("rvol", 0.0) or 0.0)
    displacement_ratio = body_size / atr if atr > 0 else 0.0
    close_position = (close_price - low_price) / candle_range if candle_range > 0 else 0.0
    upper_wick_ratio = (high_price - max(open_price, close_price)) / candle_range if candle_range > 0 else 0.0
    lower_wick_ratio = (min(open_price, close_price) - low_price) / candle_range if candle_range > 0 else 0.0
    failed: list[str] = []
    if candle_range <= 0:
        failed.append("range_not_positive")
    if body_ratio < MIN_EARLY_TRIGGER_BODY_RATIO:
        failed.append("body_ratio_below_min")
    if displacement_ratio < MIN_EARLY_TRIGGER_DISPLACEMENT_ATR:
        failed.append("displacement_below_min")
    absorption_warning = bool(rvol >= 1.8 and body_ratio < 0.35)
    if absorption_warning:
        failed.append("absorption_warning")
    if direction == "long":
        level = latest_micro_level(micro_highs, "high", index, anchor)
        trigger_type = "bullish_early_choch"
        if level is None:
            failed.append("micro_swing_high_missing")
        elif close_price <= level:
            failed.append("close_not_beyond_micro_high")
        if close_position < 0.6:
            failed.append("close_position_below_min")
        opposite_wick_ratio = lower_wick_ratio
        close_beyond = None if level is None else close_price - level
        micro_break = level is not None and close_price > level
        production_direction_ok = close_price > open_price
    else:
        level = latest_micro_level(micro_lows, "low", index, anchor)
        trigger_type = "bearish_early_choch"
        if level is None:
            failed.append("micro_swing_low_missing")
        elif close_price >= level:
            failed.append("close_not_beyond_micro_low")
        if close_position > 0.4:
            failed.append("close_position_above_max")
        opposite_wick_ratio = upper_wick_ratio
        close_beyond = None if level is None else level - close_price
        micro_break = level is not None and close_price < level
        production_direction_ok = close_price < open_price
    quality = early_quality(body_ratio, displacement_ratio, rvol, close_position, "bullish" if direction == "long" else "bearish")
    if rvol < MIN_EARLY_TRIGGER_RVOL and not micro_break:
        failed.append("rvol_below_min_without_micro_break")
    if quality < MIN_EARLY_TRIGGER_QUALITY:
        failed.append("quality_score_below_min")
    return {
        "candle_timestamp": ts_text(index),
        "trigger_type_candidate": trigger_type,
        "open": open_price,
        "high": high_price,
        "low": low_price,
        "close": close_price,
        "volume": candle.get("volume"),
        "ATR": atr,
        "RVOL": rvol,
        "body_size": round(body_size, 8),
        "body_ratio": round(body_ratio, 4),
        "range": round(candle_range, 8),
        "displacement_ratio": round(displacement_ratio, 4),
        "close_position": round(close_position, 4),
        "upper_wick_ratio": round(upper_wick_ratio, 4),
        "lower_wick_ratio": round(lower_wick_ratio, 4),
        "opposite_wick_ratio": round(opposite_wick_ratio, 4),
        "structure_level": None if level is None else round(float(level), 8),
        "close_beyond_structure_distance": None if close_beyond is None else round(float(close_beyond), 8),
        "micro_BOS_CHoCH_detected": bool(micro_break),
        "quality_score": quality,
        "production_decision": "pass" if not failed else "reject",
        "primary_reason": failed[0] if failed else None,
        "failed_conditions": failed,
        "hard_passed_count": 7 - len([x for x in failed if x != "quality_score_below_min"]),
        "production_direction_ok": production_direction_ok,
    }


def potential_trigger_candles(candidates: dict[str, dict[str, Any]], candles_by_symbol: dict[str, pd.DataFrame]) -> list[dict[str, Any]]:
    rows = []
    for candidate in candidates.values():
        sfp_ts = parse_ts(candidate.get("sfp_timestamp"))
        df = candles_by_symbol.get(candidate["symbol"])
        if sfp_ts is None or df is None or df.empty:
            continue
        direction = candidate["direction"]
        if direction not in {"long", "short"}:
            continue
        window = df[df.index > sfp_ts].head(MAX_BARS_AFTER_SFP)
        if window.empty:
            continue
        micro_highs, micro_lows = find_swings(df, left_bars=2, right_bars=1)
        for pos, (index, candle) in enumerate(window.iterrows(), start=1):
            classified = classify_early_candle(candle, index, direction, micro_highs, micro_lows, sfp_ts)
            rows.append(
                {
                    "candidate_id": candidate["candidate_id"],
                    "symbol": candidate["symbol"],
                    "direction": direction,
                    "SFP_timestamp": candidate.get("sfp_timestamp"),
                    "candle_index": pos,
                    **classified,
                    "failed_conditions": ";".join(classified["failed_conditions"]) if classified["failed_conditions"] else "",
                }
            )
    return rows


def shortfall(actual: Optional[float], required: float) -> Optional[dict[str, Any]]:
    if actual is None or actual >= required:
        return None
    absolute = required - actual
    return {
        "actual": round(actual, 6),
        "required": required,
        "absolute_shortfall": round(absolute, 6),
        "percentage_shortfall": round((absolute / required) * 100, 4) if required else None,
    }


def near_miss_rows(trigger_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    near = []
    for row in trigger_rows:
        failed = [item for item in str(row.get("failed_conditions") or "").split(";") if item]
        metrics = {
            "body_ratio": shortfall(to_float(row.get("body_ratio")), MIN_EARLY_TRIGGER_BODY_RATIO),
            "displacement_ratio": shortfall(to_float(row.get("displacement_ratio")), MIN_EARLY_TRIGGER_DISPLACEMENT_ATR),
            "quality_score": shortfall(to_float(row.get("quality_score")), MIN_EARLY_TRIGGER_QUALITY),
        }
        if row.get("direction") == "long":
            metrics["close_position"] = shortfall(to_float(row.get("close_position")), 0.6)
        else:
            cp = to_float(row.get("close_position"))
            metrics["close_position"] = None if cp is None or cp <= 0.4 else {
                "actual": round(cp, 6),
                "required": 0.4,
                "absolute_shortfall": round(cp - 0.4, 6),
                "percentage_shortfall": round(((cp - 0.4) / 0.4) * 100, 4),
            }
        rvol_miss = shortfall(to_float(row.get("RVOL")), MIN_EARLY_TRIGGER_RVOL)
        if rvol_miss and "rvol_below_min_without_micro_break" in failed:
            metrics["rvol"] = rvol_miss
        close_beyond = to_float(row.get("close_beyond_structure_distance"))
        atr = to_float(row.get("ATR"))
        if close_beyond is not None and atr and close_beyond < 0:
            metrics["close_beyond_structure"] = {
                "actual": round(close_beyond, 6),
                "required": 0.0,
                "absolute_shortfall": round(abs(close_beyond), 6),
                "percentage_shortfall": None,
            }
        active = {key: value for key, value in metrics.items() if value}
        close_enough = {
            key: value for key, value in active.items()
            if key == "quality_score" and value["absolute_shortfall"] <= 10
            or key == "rvol" and value["percentage_shortfall"] <= 15
            or key not in {"quality_score", "rvol"} and (value["percentage_shortfall"] is None or value["percentage_shortfall"] <= 10)
        }
        if not close_enough and len(failed) > 2:
            continue
        category = "single_condition_near_miss" if len(failed) == 1 else "multi_condition_near_miss"
        for metric, data in close_enough.items() or {"failed_conditions": {"actual": None, "required": None, "absolute_shortfall": None, "percentage_shortfall": None}}.items():
            near.append(
                {
                    **{k: row.get(k) for k in ("candidate_id", "symbol", "direction", "SFP_timestamp", "candle_timestamp", "trigger_type_candidate")},
                    "near_miss_category": category,
                    "metric": metric,
                    **data,
                    "all_failed_conditions": ";".join(failed) if failed else "",
                    "passed_conditions_count": row.get("hard_passed_count"),
                    "failed_conditions_count": len(failed),
                }
            )
    return near


def outcome_for(candles: pd.DataFrame, timestamp: Any, direction: str, horizon: int) -> dict[str, Any]:
    ts = parse_ts(timestamp)
    if ts is None or candles is None or candles.empty or ts not in candles.index:
        return {"outcome_available": False, "outcome_unavailable_reason": "trigger_candle_missing"}
    pos = candles.index.get_loc(ts)
    future = candles.iloc[pos + 1: pos + 1 + horizon]
    if future.empty or len(future) < horizon:
        return {"outcome_available": False, "outcome_unavailable_reason": "insufficient_future_candles"}
    base = float(candles.iloc[pos]["close"])
    atr = float(candles.iloc[pos].get("atr") or 0.0)
    if direction == "long":
        mfe_price = float(future["high"].max())
        mae_price = float(future["low"].min())
        close_cont = float(future["close"].max()) - base
        wick_cont = mfe_price - base
        mfe = mfe_price - base
        mae = base - mae_price
        ttm = int(future["high"].idxmax() == future.index[0]) if len(future) == 1 else int(list(future.index).index(future["high"].idxmax()) + 1)
    else:
        mfe_price = float(future["low"].min())
        mae_price = float(future["high"].max())
        close_cont = base - float(future["close"].min())
        wick_cont = base - mfe_price
        mfe = base - mfe_price
        mae = mae_price - base
        ttm = int(list(future.index).index(future["low"].idxmin()) + 1)
    mfe_atr = mfe / atr if atr > 0 else None
    mae_atr = mae / atr if atr > 0 else None
    labels = []
    if mfe_atr is not None:
        for threshold in (0.5, 1, 2):
            if mfe_atr >= threshold:
                labels.append(f"continuation>={threshold:g}ATR")
    if mfe_atr is not None and mae_atr is not None and mae_atr >= 0.5 and mfe_atr < 0.5:
        labels.append("immediate_reversal")
    if mfe_atr is not None and mfe_atr < 0.5 and mae_atr is not None and mae_atr < 0.5:
        labels.append("no_meaningful_movement")
    if mfe_atr is not None and mae_atr is not None and mae_atr > mfe_atr:
        labels.append("false_break")
    return {
        "outcome_available": True,
        "base_price": base,
        "horizon_candles": horizon,
        "MFE_percent": round((mfe / base) * 100, 4) if base else None,
        "MAE_percent": round((mae / base) * 100, 4) if base else None,
        "MFE_ATR": round(mfe_atr, 4) if mfe_atr is not None else None,
        "MAE_ATR": round(mae_atr, 4) if mae_atr is not None else None,
        "time_to_MFE": ttm,
        "maximum_close_continuation": round(close_cont, 8),
        "maximum_wick_continuation": round(wick_cont, 8),
        "outcome_labels": ";".join(labels),
    }


def outcome_rows(trigger_rows: list[dict[str, Any]], candles_by_symbol: dict[str, pd.DataFrame]) -> list[dict[str, Any]]:
    rows = []
    seen = set()
    for row in trigger_rows:
        key = (row.get("candidate_id"), row.get("candle_timestamp"))
        if key in seen:
            continue
        seen.add(key)
        group = "confirmed_early_trigger" if row.get("production_decision") == "pass" else ("near_miss" if len([x for x in str(row.get("failed_conditions") or "").split(";") if x]) <= 2 else "weak_rejected_trigger")
        for horizon in HORIZONS:
            rows.append(
                {
                    **{k: row.get(k) for k in ("candidate_id", "symbol", "direction", "SFP_timestamp", "candle_timestamp", "trigger_type_candidate")},
                    "comparison_group": group,
                    **outcome_for(candles_by_symbol.get(str(row.get("symbol"))), row.get("candle_timestamp"), row.get("direction"), horizon),
                }
            )
    return rows


def lifetime_rows(candidates: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for item in candidates.values():
        sfp_ts = parse_ts(item.get("sfp_timestamp"))
        last_market_ts = None
        for _path, record, _candidate in item.get("_rows") or []:
            ts = parse_ts(record.get("market_data_timestamp_15m"))
            if ts is not None and (last_market_ts is None or ts > last_market_ts):
                last_market_ts = ts
        bars_since_sfp = None if sfp_ts is None or last_market_ts is None else max(0, int((last_market_ts - sfp_ts) / pd.Timedelta(minutes=15)))
        status = "candidate_age_unknown"
        if bars_since_sfp is not None:
            status = "stale_active_candidate" if bars_since_sfp > MAX_BARS_AFTER_SFP and item.get("expired_active_invalidated") != "invalidated" else "within_window_or_inactive"
        early_ts = parse_ts(item.get("trigger_timestamp"))
        accepted_late = early_ts is not None and sfp_ts is not None and int((early_ts - sfp_ts) / pd.Timedelta(minutes=15)) > MAX_BARS_AFTER_SFP
        out.append(
            {
                "candidate_id": item["candidate_id"],
                "symbol": item["symbol"],
                "direction": item["direction"],
                "sfp_timestamp": item.get("sfp_timestamp"),
                "last_seen_at": item.get("last_seen_at"),
                "last_market_candle": ts_text(last_market_ts),
                "bars_since_sfp_by_market_timestamp": bars_since_sfp,
                "recorded_market_age_bars": item.get("max_market_age_bars"),
                "recorded_runtime_update_count": item.get("max_runtime_update_count"),
                "scan_cycles": item.get("scan_cycles"),
                "status": item.get("expired_active_invalidated"),
                "lifetime_class": "late_triggers_accepted" if accepted_late else status,
                "late_trigger_accepted": accepted_late,
                "max_bars_after_sfp": MAX_BARS_AFTER_SFP,
            }
        )
    return out


def double_filter_class(row: dict[str, Any]) -> str:
    required = ("body_ratio", "displacement_ratio", "close_position", "RVOL", "quality_score")
    if any(row.get(key) in (None, "") for key in required):
        return "unknown_due_to_missing_fields"
    failed = {x for x in str(row.get("failed_conditions") or "").split(";") if x}
    hard_failed = bool(failed - {"quality_score_below_min"})
    quality_failed = to_float(row.get("quality_score")) is not None and to_float(row.get("quality_score")) < MIN_EARLY_TRIGGER_QUALITY
    if hard_failed and quality_failed:
        return "failed_hard_and_quality"
    if hard_failed:
        return "quality_passed_but_hard_condition_failed"
    if quality_failed:
        return "passed_all_hard_conditions_but_failed_quality"
    return "all_conditions_passed"


def comparison_summary(outcomes: list[dict[str, Any]]) -> dict[str, Any]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in outcomes:
        if row.get("horizon_candles") == 16 and row.get("outcome_available"):
            groups[str(row.get("comparison_group"))].append(row)
    summary = {}
    for group, rows in sorted(groups.items()):
        mfe = [to_float(row.get("MFE_ATR")) for row in rows if to_float(row.get("MFE_ATR")) is not None]
        mae = [to_float(row.get("MAE_ATR")) for row in rows if to_float(row.get("MAE_ATR")) is not None]
        labels = [str(row.get("outcome_labels") or "") for row in rows]
        summary[group] = {
            "count": len(rows),
            "median_MFE_ATR": round(median(mfe), 4) if mfe else None,
            "median_MAE_ATR": round(median(mae), 4) if mae else None,
            "continuation_gte_1ATR_rate": rate(labels, "continuation>=1ATR"),
            "continuation_gte_2ATR_rate": rate(labels, "continuation>=2ATR"),
            "false_break_rate": rate(labels, "false_break"),
            "immediate_reversal_rate": rate(labels, "immediate_reversal"),
        }
    return summary


def rate(labels: list[str], needle: str) -> Optional[float]:
    if not labels:
        return None
    return round(sum(1 for item in labels if needle in item) / len(labels), 4)


def candidate_public_rows(candidates: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for item in candidates.values():
        rows.append({k: (";".join(sorted(v)) if isinstance(v, set) else v) for k, v in item.items() if not k.startswith("_")})
    return sorted(rows, key=lambda r: (str(r.get("symbol")), str(r.get("direction")), str(r.get("candidate_id"))))


def summarize(candidates: dict[str, dict[str, Any]], trigger_rows: list[dict[str, Any]], near_rows: list[dict[str, Any]], outcomes: list[dict[str, Any]], lifetime: list[dict[str, Any]], rows: list[tuple[str, dict[str, Any]]]) -> dict[str, Any]:
    candidate_aggs, _stats = build_candidates(rows)
    sfp_candidates = list(candidates.values())
    funnel = []
    stages = [
        ("SFP_CONFIRMED", lambda c: True),
        ("early trigger candidate exists", lambda c: any(r["candidate_id"] == c["candidate_id"] for r in trigger_rows)),
        ("hard conditions passed", lambda c: any(r["candidate_id"] == c["candidate_id"] and double_filter_class(r) in {"all_conditions_passed", "passed_all_hard_conditions_but_failed_quality"} for r in trigger_rows)),
        ("quality passed", lambda c: any(r["candidate_id"] == c["candidate_id"] and to_float(r.get("quality_score")) is not None and to_float(r.get("quality_score")) >= MIN_EARLY_TRIGGER_QUALITY for r in trigger_rows)),
        ("EARLY_TRIGGER_CONFIRMED", lambda c: bool(c.get("early_trigger_confirmed"))),
        ("CONFIRMED_TRIGGER_CONFIRMED", lambda c: bool(c.get("confirmed_trigger_timestamp"))),
    ]
    previous_ids = {c["candidate_id"] for c in sfp_candidates}
    previous = len(previous_ids)
    for name, predicate in stages:
        stage_ids = {c["candidate_id"] for c in sfp_candidates if c["candidate_id"] in previous_ids and predicate(c)}
        count = len(stage_ids)
        funnel.append({"stage": name, "unique_candidates": count, "conversion_from_previous": round((count / previous) * 100, 2) if previous else 0.0, "dropped_candidates": max(previous - count, 0), "unknown_due_to_missing_diagnostics": 0})
        previous_ids = stage_ids
        previous = count
    return {
        "production_thresholds": {
            "min_early_trigger_quality": MIN_EARLY_TRIGGER_QUALITY,
            "min_early_trigger_body_ratio": MIN_EARLY_TRIGGER_BODY_RATIO,
            "min_early_trigger_displacement_atr": MIN_EARLY_TRIGGER_DISPLACEMENT_ATR,
            "min_early_trigger_rvol": MIN_EARLY_TRIGGER_RVOL,
            "search_window_bars_after_sfp": MAX_BARS_AFTER_SFP,
            "confirmed_bos": BOSConfig().__dict__,
        },
        "supported_early_trigger_types": {
            "accepted_by_scanner_long": sorted(EARLY_LONG_TRIGGER_TYPES),
            "accepted_by_scanner_short": sorted(EARLY_SHORT_TRIGGER_TYPES),
            "generated_by_production": sorted(GENERATED_EARLY_TRIGGER_TYPES),
        },
        "counts": {
            "sfp_candidates": len(sfp_candidates),
            "potential_trigger_candles": len(trigger_rows),
            "confirmed_early_triggers": sum(1 for c in sfp_candidates if c.get("early_trigger_confirmed")),
            "near_misses": len({(r.get("candidate_id"), r.get("candle_timestamp")) for r in near_rows}),
        },
        "funnel": funnel,
        "top_failed_conditions": Counter(x for row in trigger_rows for x in str(row.get("failed_conditions") or "").split(";") if x).most_common(20),
        "near_miss_groups": dict(Counter(row.get("near_miss_category") for row in near_rows)),
        "double_filter_analysis": dict(Counter(double_filter_class(row) for row in trigger_rows)),
        "outcome_comparison": comparison_summary(outcomes),
        "lifetime_audit": dict(Counter(row.get("lifetime_class") for row in lifetime)),
        "fresh_2026_07_19_focus": focus_rows(candidates, trigger_rows, outcomes),
        "data_quality": {
            "journal_rows": len(rows),
            "candidate_age_unknown": sum(1 for row in lifetime if row.get("lifetime_class") == "candidate_age_unknown"),
            "outcomes_unavailable": sum(1 for row in outcomes if not row.get("outcome_available")),
        },
        "crosscheck_candidate_funnel_unique_candidates": len(candidate_aggs),
        "recommendation": "нужно больше данных",
        "production_logic_changed": False,
    }


def focus_rows(candidates: dict[str, dict[str, Any]], trigger_rows: list[dict[str, Any]], outcomes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    wanted = {("AAVE", "short"), ("LDO", "long"), ("WLD", "short")}
    result = []
    for item in candidates.values():
        if (item.get("symbol"), item.get("direction")) not in wanted:
            continue
        cand_rows = [row for row in trigger_rows if row.get("candidate_id") == item["candidate_id"]]
        nearest = sorted(cand_rows, key=lambda row: (len([x for x in str(row.get("failed_conditions") or "").split(";") if x]), -(to_float(row.get("quality_score")) or 0)))
        future = [row for row in outcomes if row.get("candidate_id") == item["candidate_id"] and row.get("horizon_candles") == 16]
        result.append(
            {
                "candidate_id": item["candidate_id"],
                "symbol": item["symbol"],
                "direction": item["direction"],
                "sfp_anchor": item.get("sfp_timestamp"),
                "sfp_quality": item.get("sfp_quality"),
                "closed_candles_after_sfp_in_research_window": len(cand_rows),
                "production_scan_cycles": item.get("scan_cycles"),
                "closest_trigger_candle": nearest[0] if nearest else None,
                "future_outcome_4h": future[0] if future else None,
                "bars_waiting_recorded": item.get("max_market_age_bars"),
                "scans_waiting_recorded": item.get("max_runtime_update_count"),
            }
        )
    return result


def run(input_globs: list[str], output_dir: Path) -> dict[str, Any]:
    paths = sorted({path for pattern in input_globs for path in glob.glob(pattern)})
    rows, _quality = load_records(paths)
    candidates = dedupe_sfp_candidates(rows)
    candles = load_symbol_candles(rows)
    trigger_rows = potential_trigger_candles(candidates, candles)
    near_rows = near_miss_rows(trigger_rows)
    outcomes = outcome_rows(trigger_rows, candles)
    lifetime = lifetime_rows(candidates)
    summary = summarize(candidates, trigger_rows, near_rows, outcomes, lifetime, rows)
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "early_trigger_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    write_csv(output_dir / "early_trigger_candidates.csv", candidate_public_rows(candidates))
    write_csv(output_dir / "early_trigger_near_misses.csv", near_rows)
    write_csv(output_dir / "early_trigger_future_outcomes.csv", outcomes)
    write_csv(output_dir / "early_trigger_lifetime_audit.csv", lifetime)
    return summary


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Audit Early Trigger quality from local scan journals.")
    parser.add_argument("--input", action="append", default=["data/journal/scans_*.jsonl"], help="Input glob; may be repeated")
    parser.add_argument("--output-dir", type=Path, default=Path("runtime_data/research"))
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    print(json.dumps(run(args.input, args.output_dir), ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
