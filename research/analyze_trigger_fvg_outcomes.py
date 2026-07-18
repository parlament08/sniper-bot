#!/usr/bin/env python3
"""Audit confirmed-trigger to candidate-scoped FVG bottleneck from journals."""

from __future__ import annotations

import argparse
import csv
import glob
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Iterable, Optional

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.structure import BOSConfig
from research.analyze_candidate_funnel import build_candidates, candidate_items, load_records, normalize_direction, to_float


FVG_MIN_SIZE_ATR_RATIO = 0.5
HORIZONS = (4, 8, 16, 32)


def parse_ts(value: Any):
    import pandas as pd

    if value is None or value == "":
        return None
    return pd.Timestamp(value).tz_localize(None)


def state_direction(direction: str) -> str:
    direction = normalize_direction(direction)
    if direction == "long":
        return "bullish"
    if direction == "short":
        return "bearish"
    return direction


def load_symbol_candles(rows: Iterable[tuple[str, dict[str, Any]]]) -> dict[str, list[dict[str, Any]]]:
    candles: dict[str, dict[Any, dict[str, Any]]] = {}
    for _path, record in rows:
        if record.get("record_type") != "symbol_scan":
            continue
        ts = parse_ts(record.get("market_data_timestamp_15m"))
        symbol = record.get("symbol")
        if ts is None or not symbol:
            continue
        required = ["market_open_15m", "market_high_15m", "market_low_15m", "market_close_15m"]
        if any(record.get(key) is None for key in required):
            continue
        candles.setdefault(symbol, {})[ts] = {
            "timestamp": ts,
            "open": float(record["market_open_15m"]),
            "high": float(record["market_high_15m"]),
            "low": float(record["market_low_15m"]),
            "close": float(record["market_close_15m"]),
            "atr": to_float(record.get("atr")),
            "rvol": to_float(record.get("rvol")),
        }
    return {symbol: [items[key] for key in sorted(items)] for symbol, items in candles.items()}


def find_confirmed_trigger_candidates(rows: list[tuple[str, dict[str, Any]]]) -> list[dict[str, Any]]:
    candidates, _stats = build_candidates(rows)
    confirmed_ids = {item.candidate_id for item in candidates.values() if item.max_stage() == "CONFIRMED_TRIGGER_CONFIRMED"}
    found = {}
    for path, record in rows:
        if record.get("record_type") != "symbol_scan":
            continue
        for candidate in candidate_items(record):
            candidate_id = candidate.get("candidate_id")
            if candidate_id not in confirmed_ids:
                continue
            trigger = confirmed_trigger(candidate)
            if not trigger:
                continue
            current = found.setdefault(
                candidate_id,
                {
                    "candidate_id": candidate_id,
                    "symbol": record.get("symbol"),
                    "direction": normalize_direction(candidate.get("direction")),
                    "scenario_type": candidate.get("anchor_type"),
                    "created_at": candidate.get("candidate_created_at") or candidate.get("anchor_index"),
                    "anchor_timestamp": candidate.get("anchor_index"),
                    "early_trigger_timestamp": None,
                    "confirmed_trigger_timestamp": trigger.get("index"),
                    "confirmed_bos_quality": trigger.get("quality_score"),
                    "selected_scenario_status": candidate.get("status"),
                    "max_score": record.get("score"),
                    "risk_reason": candidate.get("risk_reason"),
                    "final_decision": record.get("final_decision") or record.get("decision"),
                    "confirmed_trigger": trigger,
                    "events": candidate.get("events_used") or [],
                    "journal_rows": [],
                    "journal_fvg_summaries": [],
                    "research_traces": [],
                },
            )
            early = early_trigger(candidate)
            if early and current.get("early_trigger_timestamp") is None:
                current["early_trigger_timestamp"] = early.get("index")
            current["max_score"] = max([x for x in [to_float(current.get("max_score")), to_float(record.get("score"))] if x is not None], default=None)
            current["journal_rows"].append(
                {
                    "source_file": path,
                    "timestamp": record.get("timestamp"),
                    "market_data_timestamp_15m": record.get("market_data_timestamp_15m"),
                    "score": record.get("score"),
                    "decision": record.get("decision"),
                    "status": candidate.get("status"),
                    "current_step": candidate.get("current_step"),
                    "next_expected_step": candidate.get("next_expected_step"),
                    "risk_reason": candidate.get("risk_reason"),
                }
            )
            trigger_debug = (record.get("features") or {}).get("trigger_debug") or {}
            research_trace = record.get("scenario_research_trace")
            if isinstance(research_trace, dict) and research_trace.get("candidate_id") == candidate_id:
                current["research_traces"].append(research_trace)
            if trigger_debug.get("fvg_index") is not None:
                current["journal_fvg_summaries"].append(
                    {
                        "timestamp": trigger_debug.get("fvg_index"),
                        "direction": state_direction(candidate.get("direction")),
                        "quality_score": "not_recorded",
                        "historical_only": "not_recorded",
                        "is_reconstructed": "not_recorded",
                        "invalidated": trigger_debug.get("fvg_rejected_reason") == "fvg_invalidated",
                        "source_confirmed_trigger_id": "not_recorded",
                        "source_confirmed_trigger_index": "not_recorded",
                        "candidate_id": "not_recorded",
                        "fvg_scenario_valid": trigger_debug.get("fvg_scenario_valid"),
                        "rejected_reason": trigger_debug.get("fvg_rejected_reason"),
                        "evidence": "journal_trigger_debug_summary",
                    }
                )
    return list(found.values())


def latest_research_trace(candidate: dict[str, Any]) -> dict[str, Any]:
    traces = candidate.get("research_traces") or []
    if not traces:
        return {}
    return sorted(traces, key=lambda item: str(item.get("analysis_time") or ""))[-1]


def fvg_rows_from_research_trace(candidate: dict[str, Any], trace: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    diagnostics = trace.get("fvg_diagnostics") if isinstance(trace, dict) else {}
    fvg_candidates = diagnostics.get("candidates") if isinstance(diagnostics, dict) else []
    match_items = trace.get("fvg_match_trace") if isinstance(trace, dict) else []
    match_by_id = {item.get("fvg_id"): item for item in match_items if isinstance(item, dict)}
    fvg_rows = []
    trace_rows = []
    for fvg in fvg_candidates or []:
        if not isinstance(fvg, dict):
            continue
        match = match_by_id.get(fvg.get("fvg_id"), {})
        first_rejection = None
        rejection_reasons = match.get("rejection_reasons") if isinstance(match, dict) else None
        if isinstance(rejection_reasons, list) and rejection_reasons:
            first_rejection = rejection_reasons[0]
        elif isinstance(rejection_reasons, str) and rejection_reasons:
            first_rejection = rejection_reasons.split(";")[0]
        row = {
            **fvg,
            "timestamp": fvg.get("created_at") or fvg.get("created_index"),
            "direction": fvg.get("direction"),
            "type": fvg.get("direction"),
            "top": fvg.get("upper"),
            "bottom": fvg.get("lower"),
            "created_candle": fvg.get("created_at") or fvg.get("created_index"),
            "owner_candidate_id": candidate["candidate_id"],
            "symbol": candidate["symbol"],
            "trace_accepted": match.get("accepted") if isinstance(match, dict) else "not_recorded",
            "trace_first_rejection_reason": first_rejection,
            "evidence": "scenario_research_trace",
        }
        fvg_rows.append(row)
        trace_rows.append(
            {
                **(match if isinstance(match, dict) else {}),
                "owner_candidate_id": candidate["candidate_id"],
                "symbol": candidate["symbol"],
                "fvg_timestamp": row["timestamp"],
                "fvg_direction": fvg.get("direction"),
                "first_rejection_reason": first_rejection,
                "evidence": "scenario_research_trace",
                "invalidation_reason": fvg.get("invalidation_reason"),
                "invalidated_at": fvg.get("invalidated_at"),
                "invalidation_price": fvg.get("invalidation_price"),
                "invalidation_boundary": fvg.get("invalidation_boundary"),
                "invalidation_operator": fvg.get("invalidation_operator"),
            }
        )
    return fvg_rows, trace_rows


def confirmed_trigger(candidate: dict[str, Any]) -> Optional[dict[str, Any]]:
    trigger_scan = candidate.get("trigger_scan") if isinstance(candidate.get("trigger_scan"), dict) else {}
    if isinstance(trigger_scan.get("confirmed_trigger"), dict):
        return trigger_scan["confirmed_trigger"]
    for event in candidate.get("events_used") or []:
        if event.get("event_type") == "CONFIRMED_TRIGGER_CONFIRMED":
            payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
            return payload or {"index": event.get("index"), "quality_score": event.get("quality_score")}
    return None


def early_trigger(candidate: dict[str, Any]) -> Optional[dict[str, Any]]:
    trigger_scan = candidate.get("trigger_scan") if isinstance(candidate.get("trigger_scan"), dict) else {}
    if isinstance(trigger_scan.get("early_trigger"), dict):
        return trigger_scan["early_trigger"]
    for event in candidate.get("events_used") or []:
        if event.get("event_type") == "EARLY_TRIGGER_CONFIRMED":
            payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
            return payload or {"index": event.get("index"), "quality_score": event.get("quality_score")}
    return None


def detect_fvgs(candles: list[dict[str, Any]], *, min_size_atr_ratio: float = FVG_MIN_SIZE_ATR_RATIO) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    fvgs = []
    geometry = []
    for i in range(2, len(candles)):
        c0 = candles[i - 2]
        c1 = candles[i - 1]
        c2 = candles[i]
        atr = c2.get("atr") or abs(c2["high"] - c2["low"]) or 1.0
        min_gap = atr * min_size_atr_ratio
        checks = [
            ("bullish", c2["low"] - c0["high"], c2["low"], c0["high"], c1),
            ("bearish", c0["low"] - c2["high"], c0["low"], c2["high"], c1),
        ]
        for direction, raw_gap, top, bottom, impulse in checks:
            detected = raw_gap > 0 and raw_gap >= min_gap
            reason = "detected" if detected else "no_geometric_gap" if raw_gap <= 0 else "gap_below_min_size_atr"
            geometry.append(
                {
                    "timestamps": f"{c0['timestamp']}|{c1['timestamp']}|{c2['timestamp']}",
                    "fvg_timestamp": c2["timestamp"],
                    "direction": direction,
                    "prev2_high": c0["high"],
                    "prev2_low": c0["low"],
                    "current_high": c2["high"],
                    "current_low": c2["low"],
                    "raw_gap": raw_gap,
                    "atr": atr,
                    "minimum_required_gap": min_gap,
                    "detected": detected,
                    "reason": reason,
                }
            )
            if detected:
                future = candles[i + 1 :]
                invalidated = False
                overlap = 0.0
                if future:
                    gap_size = top - bottom
                    if direction == "bullish":
                        invalidated = any(item["close"] < bottom for item in future)
                        deepest = min(item["low"] for item in future)
                        overlap = max(0.0, min((top - deepest) / gap_size, 1.0))
                    else:
                        invalidated = any(item["close"] > top for item in future)
                        deepest = max(item["high"] for item in future)
                        overlap = max(0.0, min((deepest - bottom) / gap_size, 1.0))
                fvgs.append(
                    {
                        "timestamp": str(c2["timestamp"]),
                        "direction": direction,
                        "type": direction,
                        "top": round(top, 8),
                        "bottom": round(bottom, 8),
                        "gap_size": round(top - bottom, 10),
                        "gap_size_atr": round((top - bottom) / atr, 4) if atr else None,
                        "quality_score": "not_recomputed",
                        "created_candle": str(c2["timestamp"]),
                        "source_impulse_candle": str(impulse["timestamp"]),
                        "end_index": str(c2["timestamp"]),
                        "historical_only": False,
                        "is_reconstructed": False,
                        "invalidated": invalidated,
                        "filled_percentage": round(overlap * 100, 2),
                        "source_confirmed_trigger_id": None,
                        "source_confirmed_trigger_index": None,
                        "candidate_id": None,
                    }
                )
    return fvgs, geometry


def match_trace(fvg: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
    confirmed_index = parse_ts(candidate["confirmed_trigger_timestamp"])
    created_index = parse_ts(fvg.get("end_index") or fvg.get("created_candle") or fvg.get("timestamp"))
    expected_direction = state_direction(candidate["direction"])
    source_trigger_id = fvg.get("source_confirmed_trigger_id")
    confirmed_trigger_id = (candidate.get("confirmed_trigger") or {}).get("event_id")
    source_candidate_id = fvg.get("source_candidate_id") or fvg.get("candidate_id")
    source_trigger_index = fvg.get("source_confirmed_trigger_index")
    checks = {
        "same_direction": fvg.get("type") == expected_direction,
        "after_confirmed_trigger": created_index is not None and confirmed_index is not None and created_index > confirmed_index,
        "candidate_id_match": None if source_candidate_id is None else str(source_candidate_id) == str(candidate["candidate_id"]),
        "source_trigger_id_match": None if source_trigger_id is None or confirmed_trigger_id is None else str(source_trigger_id) == str(confirmed_trigger_id),
        "source_trigger_index_match": None if source_trigger_index is None else parse_ts(source_trigger_index) == confirmed_index,
        "historical_only": bool(fvg.get("historical_only")),
        "is_reconstructed": bool(fvg.get("is_reconstructed")),
        "invalidated": bool(fvg.get("invalidated")),
    }
    rejection = []
    if not checks["same_direction"]:
        rejection.append("direction_mismatch")
    if not checks["after_confirmed_trigger"]:
        rejection.append("created_before_or_at_confirmed_trigger")
    if checks["candidate_id_match"] is False:
        rejection.append("candidate_id_mismatch")
    if checks["source_trigger_id_match"] is False:
        rejection.append("source_trigger_id_mismatch")
    if checks["source_trigger_index_match"] is False:
        rejection.append("source_trigger_index_mismatch")
    if checks["historical_only"]:
        rejection.append("historical_only")
    if checks["is_reconstructed"]:
        rejection.append("is_reconstructed")
    if checks["invalidated"]:
        rejection.append("invalidated")
    return {
        **checks,
        "accepted": not rejection,
        "first_rejection_reason": rejection[0] if rejection else None,
        "rejection_reasons": ";".join(rejection),
    }


def fvg_window(candles: list[dict[str, Any]], confirmed_ts: Any, before=10, after=20) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    ts = parse_ts(confirmed_ts)
    indices = [i for i, c in enumerate(candles) if c["timestamp"] == ts]
    if not indices:
        return [], {"coverage": "OHLCV unavailable", "requested_before": before, "requested_after": after, "available_before": 0, "available_after": 0}
    pos = indices[0]
    start = max(0, pos - before)
    end = min(len(candles), pos + after + 1)
    return candles[start:end], {
        "coverage": "complete" if start == pos - before and end == pos + after + 1 else "partial evidence",
        "requested_before": before,
        "requested_after": after,
        "available_before": pos - start,
        "available_after": end - pos - 1,
    }


def future_outcome(candles: list[dict[str, Any]], timestamp: Any, direction: str, *, base_price: Optional[float] = None) -> dict[str, Any]:
    ts = parse_ts(timestamp)
    idx = next((i for i, c in enumerate(candles) if c["timestamp"] == ts), None)
    if idx is None:
        return {"status": "OHLCV unavailable", "base_timestamp": str(timestamp)}
    base = base_price if base_price is not None else candles[idx]["close"]
    atr = candles[idx].get("atr") or abs(candles[idx]["high"] - candles[idx]["low"]) or None
    out = {"status": "ok", "base_timestamp": str(candles[idx]["timestamp"]), "base_price": base, "atr": atr}
    sign = 1 if normalize_direction(direction) == "long" else -1
    for horizon in HORIZONS:
        future = candles[idx + 1 : idx + horizon + 1]
        key = f"{horizon}_candles"
        if not future:
            out[key] = {"status": "not_recorded"}
            continue
        favorable = [sign * (c["high"] - base) if sign == 1 else sign * (c["low"] - base) for c in future]
        adverse = [sign * (c["low"] - base) if sign == 1 else sign * (c["high"] - base) for c in future]
        mfe = max(favorable)
        mae = min(adverse)
        mfe_idx = favorable.index(mfe)
        out[key] = {
            "status": "ok" if len(future) >= horizon else "partial evidence",
            "available_candles": len(future),
            "mfe": round(mfe, 10),
            "mae": round(mae, 10),
            "mfe_atr": round(mfe / atr, 4) if atr else None,
            "mae_atr": round(mae / atr, 4) if atr else None,
            "mfe_percent": round(mfe / base * 100, 4) if base else None,
            "mae_percent": round(mae / base * 100, 4) if base else None,
            "time_to_mfe": str(future[mfe_idx]["timestamp"]),
            "continuation_ge_1atr": bool(atr and mfe >= atr),
            "false_break": bool(atr and mae <= -atr),
        }
    return out


def bos_hard_field_classification(row: dict[str, Any]) -> str:
    required = ("body_ratio", "displacement_ratio", "close_position", "rvol", "quality_score")
    present = [row.get(key) not in (None, "", "not_recorded") for key in required]
    if all(present):
        return "all_hard_fields_recorded"
    if any(present):
        return "hard_fields_partially_recorded"
    return "hard_fields_not_recorded"


def double_filter_classification(row: dict[str, Any]) -> str:
    if bos_hard_field_classification(row) != "all_hard_fields_recorded":
        return "unknown_missing_hard_fields"
    config = BOSConfig()
    quality_failed = float(row["quality_score"]) < config.min_quality_score
    hard_failed = (
        float(row["body_ratio"]) < config.min_body_ratio
        or float(row["displacement_ratio"]) < config.min_displacement_atr
        or float(row["close_position"]) < config.min_close_position
        or float(row["rvol"]) < config.min_rvol
    )
    if quality_failed and hard_failed:
        return "hard_conditions_and_quality_failed"
    if quality_failed:
        return "passed_all_hard_conditions_but_quality_failed"
    if hard_failed:
        return "quality_passed_but_hard_condition_failed"
    return "passed_all_recorded_filters"


def bos_rows_with_outcomes(rows: list[tuple[str, dict[str, Any]]], candles_by_symbol: dict[str, list[dict[str, Any]]], bos_csv: Path) -> list[dict[str, Any]]:
    if not bos_csv.exists():
        return []
    source_rows = list(csv.DictReader(bos_csv.open(encoding="utf-8")))
    out = []
    for row in source_rows:
        candles = candles_by_symbol.get(row.get("symbol") or "", [])
        outcome = future_outcome(candles, row.get("timestamp"), row.get("direction"))
        summary = {
            **row,
            "hard_field_recording": bos_hard_field_classification(row),
            "double_filter_classification": double_filter_classification(row),
        }
        for horizon in HORIZONS:
            item = outcome.get(f"{horizon}_candles") or {}
            summary[f"{horizon}_mfe_atr"] = item.get("mfe_atr")
            summary[f"{horizon}_mae_atr"] = item.get("mae_atr")
            summary[f"{horizon}_continuation_ge_1atr"] = item.get("continuation_ge_1atr")
            summary[f"{horizon}_false_break"] = item.get("false_break")
            summary[f"{horizon}_status"] = item.get("status")
        out.append(summary)
    return out


def summarize_bos_outcomes(rows: list[dict[str, Any]]) -> dict[str, Any]:
    groups = {
        "confirmed_bos": [r for r in rows if str(r.get("confirmed")).lower() == "true"],
        "near_miss_bos": [r for r in rows if is_near_miss(r)],
        "weak_rejected_bos": [r for r in rows if str(r.get("confirmed")).lower() != "true" and not is_near_miss(r)],
    }
    return {name: _bos_group_summary(items) for name, items in groups.items()}


def is_near_miss(row: dict[str, Any]) -> bool:
    config = BOSConfig()
    q = to_float(row.get("quality_score"))
    disp = to_float(row.get("displacement_ratio"))
    close = to_float(row.get("close_position"))
    rvol = to_float(row.get("rvol"))
    return bool(
        (q is not None and config.min_quality_score - 10 <= q < config.min_quality_score)
        or (disp is not None and config.min_displacement_atr * 0.9 <= disp < config.min_displacement_atr)
        or (close is not None and config.min_close_position * 0.9 <= close < config.min_close_position)
        or (rvol is not None and config.min_rvol * 0.85 <= rvol < config.min_rvol)
    )


def _bos_group_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    mfe = sorted(to_float(r.get("16_mfe_atr")) for r in rows if to_float(r.get("16_mfe_atr")) is not None)
    mae = sorted(to_float(r.get("16_mae_atr")) for r in rows if to_float(r.get("16_mae_atr")) is not None)
    return {
        "count": len(rows),
        "median_mfe_atr_4h": median(mfe),
        "median_mae_atr_4h": median(mae),
        "continuation_rate_ge_1atr_4h": rate(rows, "16_continuation_ge_1atr"),
        "false_break_rate_4h": rate(rows, "16_false_break"),
    }


def median(values: list[float]) -> Optional[float]:
    if not values:
        return None
    mid = len(values) // 2
    if len(values) % 2:
        return values[mid]
    return round((values[mid - 1] + values[mid]) / 2, 4)


def rate(rows: list[dict[str, Any]], key: str) -> Optional[float]:
    values = [r.get(key) for r in rows if r.get(key) not in (None, "")]
    if not values:
        return None
    yes = sum(str(v).lower() == "true" or v is True for v in values)
    return round(yes / len(values) * 100, 2)


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = sorted({key for row in rows for key in row})
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def run(input_globs: list[str], output_dir: Path, bos_csv: Path) -> dict[str, Any]:
    paths = sorted({path for pattern in input_globs for path in glob.glob(pattern)})
    rows, _quality = load_records(paths)
    candles_by_symbol = load_symbol_candles(rows)
    candidates = find_confirmed_trigger_candidates(rows)
    fvg_rows = []
    trace_rows = []
    candidate_summaries = []
    geometry_by_candidate = {}
    for candidate in candidates:
        candles = candles_by_symbol.get(candidate["symbol"], [])
        window, coverage = fvg_window(candles, candidate["confirmed_trigger_timestamp"])
        research_trace = latest_research_trace(candidate)
        trace_fvg_rows, trace_match_rows = fvg_rows_from_research_trace(candidate, research_trace) if research_trace else ([], [])
        fvgs, geometry = detect_fvgs(window) if window and not trace_fvg_rows else ([], [])
        geometry_by_candidate[candidate["candidate_id"]] = geometry
        outcome = future_outcome(candles, candidate["confirmed_trigger_timestamp"], candidate["direction"])
        accepted_count = 0
        invalidated_count = 0
        for row in trace_fvg_rows:
            accepted_count += int(row.get("trace_accepted") is True)
            invalidated_count += int(bool(row.get("invalidated")))
            fvg_rows.append(row)
        trace_rows.extend(trace_match_rows)
        for fvg in fvgs:
            trace = match_trace(fvg, candidate)
            accepted_count += int(trace["accepted"])
            invalidated_count += int(bool(fvg.get("invalidated")))
            row = {**fvg, "owner_candidate_id": candidate["candidate_id"], "symbol": candidate["symbol"], "trace_accepted": trace["accepted"], "trace_first_rejection_reason": trace["first_rejection_reason"]}
            fvg_rows.append(row)
            trace_rows.append({**trace, "owner_candidate_id": candidate["candidate_id"], "symbol": candidate["symbol"], "fvg_timestamp": fvg["timestamp"], "fvg_direction": fvg["direction"]})
        candidate_summaries.append(
            {
                **{k: v for k, v in candidate.items() if k not in {"journal_rows", "events", "confirmed_trigger", "journal_fvg_summaries", "research_traces"}},
                "journal_row_count": len(candidate["journal_rows"]),
                "research_trace_count": len(candidate.get("research_traces") or []),
                "fvg_window_coverage": coverage,
                "fvgs_found_in_window": len(trace_fvg_rows) if trace_fvg_rows else len(fvgs),
                "candidate_matched_fvgs": accepted_count,
                "candidate_invalidated_fvgs": invalidated_count,
                "accepted_vs_invalidated_fvgs": {
                    "accepted": accepted_count,
                    "invalidated": invalidated_count,
                    "source": "scenario_research_trace" if trace_fvg_rows else "reconstructed_from_journal_market_fields",
                },
                "absence_reason": "no_fvg_detected_in_available_window" if not trace_fvg_rows and not fvgs else "all_detected_fvgs_rejected_by_binding",
                "journal_fvg_summaries": candidate.get("journal_fvg_summaries") or [],
                "future_outcome": outcome,
            }
        )
        for summary_fvg in candidate.get("journal_fvg_summaries") or []:
            summary_trace = match_trace(
                {
                    "type": summary_fvg.get("direction"),
                    "end_index": summary_fvg.get("timestamp"),
                    "invalidated": summary_fvg.get("invalidated"),
                    "historical_only": False if summary_fvg.get("historical_only") == "not_recorded" else summary_fvg.get("historical_only"),
                    "is_reconstructed": False if summary_fvg.get("is_reconstructed") == "not_recorded" else summary_fvg.get("is_reconstructed"),
                },
                candidate,
            )
            if summary_fvg.get("rejected_reason") and not summary_trace["rejection_reasons"]:
                summary_trace["accepted"] = False
                summary_trace["first_rejection_reason"] = summary_fvg.get("rejected_reason")
                summary_trace["rejection_reasons"] = summary_fvg.get("rejected_reason")
            fvg_rows.append(
                {
                    **summary_fvg,
                    "owner_candidate_id": candidate["candidate_id"],
                    "symbol": candidate["symbol"],
                    "trace_accepted": "not_reproducible",
                    "trace_first_rejection_reason": summary_fvg.get("rejected_reason") or "not_recorded",
                    "top": "not_recorded",
                    "bottom": "not_recorded",
                    "gap_size": "not_recorded",
                    "gap_size_atr": "not_recorded",
                    "created_candle": summary_fvg.get("timestamp"),
                    "source_impulse_candle": "not_recorded",
                    "filled_percentage": "not_recorded",
                }
            )
            trace_rows.append(
                {
                    **summary_trace,
                    "owner_candidate_id": candidate["candidate_id"],
                    "symbol": candidate["symbol"],
                    "fvg_timestamp": summary_fvg.get("timestamp"),
                    "fvg_direction": summary_fvg.get("direction"),
                    "evidence": "journal_trigger_debug_summary",
                }
            )
    bos_outcomes = bos_rows_with_outcomes(rows, candles_by_symbol, bos_csv)
    summary = {
        "input_files": paths,
        "confirmed_trigger_candidates": candidate_summaries,
        "fvg_geometry": geometry_by_candidate,
        "fvg_match_trace_counts": dict(Counter(row.get("trace_first_rejection_reason") or row.get("first_rejection_reason") or "accepted" for row in trace_rows)),
        "bos_outcome_summary": summarize_bos_outcomes(bos_outcomes),
        "double_filter_recheck": dict(Counter(row["double_filter_classification"] for row in bos_outcomes)),
        "hard_field_recording": dict(Counter(row["hard_field_recording"] for row in bos_outcomes)),
        "data_limitations": [
            "OHLCV reconstructed only from local journal market_*_15m fields.",
            "No external Binance data used.",
            "PYTH confirmed trigger at 2026-07-17 12:00:00 has incomplete local candle coverage around trigger.",
            "FVG quality was not fully recomputed because journal rows do not record full rolling ATR/RVOL for every candle; geometry and ATR gap checks are reproduced from available fields.",
        ],
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "confirmed_trigger_fvg_audit.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    write_csv(output_dir / "confirmed_trigger_fvg_candidates.csv", candidate_summaries)
    write_csv(output_dir / "fvg_match_trace.csv", trace_rows)
    write_csv(output_dir / "bos_future_outcomes.csv", bos_outcomes)
    return summary


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Audit confirmed trigger -> FVG outcomes from local journals.")
    parser.add_argument("--input", action="append", default=["data/journal/scans_*.jsonl"])
    parser.add_argument("--output-dir", type=Path, default=Path("runtime_data/research"))
    parser.add_argument("--bos-csv", type=Path, default=Path("runtime_data/research/bos_near_misses.csv"))
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    print(json.dumps(run(args.input, args.output_dir, args.bos_csv), ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
