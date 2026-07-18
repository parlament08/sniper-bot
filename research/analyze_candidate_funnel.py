#!/usr/bin/env python3
"""Offline scenario candidate funnel audit from scan journals."""

from __future__ import annotations

import argparse
import csv
import glob
import json
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Optional

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.structure import BOSConfig


STAGES = [
    "HTF_CONTEXT_CONFIRMED",
    "POI_TOUCHED",
    "SFP_CONFIRMED",
    "EARLY_TRIGGER_CONFIRMED",
    "CONFIRMED_TRIGGER_CONFIRMED",
    "FVG_CREATED",
    "FVG_RETESTED",
    "RISK_PLAN_VALID",
    "SCORE_GATE_PASSED",
    "A_PLUS_ALLOWED",
    "DELIVERED",
]

EVENT_STAGE_MAP = {
    "HTF_CONTEXT_CONFIRMED": "HTF_CONTEXT_CONFIRMED",
    "PD_LOCATION_VALID": "POI_TOUCHED",
    "POI_TOUCHED": "POI_TOUCHED",
    "LIQUIDITY_SWEEP_CONFIRMED": "SFP_CONFIRMED",
    "SFP_CONFIRMED": "SFP_CONFIRMED",
    "EARLY_TRIGGER_CONFIRMED": "EARLY_TRIGGER_CONFIRMED",
    "CONFIRMED_TRIGGER_CONFIRMED": "CONFIRMED_TRIGGER_CONFIRMED",
    "FVG_CREATED": "FVG_CREATED",
    "FVG_RETESTED": "FVG_RETESTED",
    "DISPLACEMENT_CONFIRMED": "FVG_RETESTED",
    "RISK_VALID": "RISK_PLAN_VALID",
    "SIGNAL_ALLOWED": "RISK_PLAN_VALID",
}

REASON_ALIASES = {
    "confirmed_trigger_missing": "confirmed_bos_not_found",
    "waiting_for_confirmed_bullish_bos": "confirmed_bos_not_found",
    "waiting_for_confirmed_bearish_bos": "confirmed_bos_not_found",
    "confirmed bullish BOS after early CHOCH": "confirmed_bos_not_found",
    "confirmed bearish BOS after early CHOCH": "confirmed_bos_not_found",
    "trigger_quality_below_min": "quality_below_min",
    "quality_score_below_min": "quality_below_min",
    "bos_quality_below_threshold": "quality_below_min",
    "displacement_quality_below_threshold": "displacement_below_min",
    "volume_impulse_below_threshold": "volume_not_confirmed",
    "candidate_fvg_not_created": "candidate_fvg_not_created",
    "fvg_not_created": "candidate_fvg_not_created",
    "fvg_not_retested": "fvg_not_retested",
    "scenario_valid": "scenario_not_valid",
    "signal_allowed": "signal_not_allowed",
    "trigger_confirmed": "confirmed_bos_not_found",
    "score_threshold": "score_below_min",
    "scenario_risk_valid": "risk_invalid",
}


@dataclass
class CandidateAggregate:
    key: str
    candidate_id: str
    symbol: str
    direction: str
    scenario_type: str = "unknown"
    htf_regime: str = "unknown"
    created_at: Optional[str] = None
    first_seen_at: Optional[str] = None
    last_seen_at: Optional[str] = None
    stages: set[str] = field(default_factory=set)
    max_score: Optional[float] = None
    final_decision: Optional[str] = None
    row_count: int = 0
    used_candidate_id: bool = False
    primary_reasons: Counter = field(default_factory=Counter)
    secondary_conditions: Counter = field(default_factory=Counter)
    waiting_for: Counter = field(default_factory=Counter)
    failed_gate_reasons: Counter = field(default_factory=Counter)
    source_files: set[str] = field(default_factory=set)

    def max_stage(self) -> str:
        for stage in reversed(STAGES):
            if stage in self.stages:
                return stage
        return "not_recorded"

    def primary_stop_reason(self) -> str:
        if self.max_stage() == "DELIVERED":
            return "delivered"
        if self.primary_reasons:
            return self.primary_reasons.most_common(1)[0][0]
        if self.failed_gate_reasons:
            return normalize_reason(self.failed_gate_reasons.most_common(1)[0][0])
        if self.waiting_for:
            return normalize_reason(self.waiting_for.most_common(1)[0][0])
        return "unknown"


def nested_get(data: Any, path: str, default: Any = None) -> Any:
    current = data
    for part in path.split("."):
        if isinstance(current, dict):
            current = current.get(part)
        else:
            return default
    return default if current is None else current


def first_present(data: dict[str, Any], paths: Iterable[str], default: Any = None) -> Any:
    for path in paths:
        value = nested_get(data, path)
        if value is not None:
            return value
    return default


def normalize_reason(reason: Any) -> str:
    if reason is None or reason == "":
        return "unknown"
    text = str(reason).strip()
    return REASON_ALIASES.get(text, text)


def normalize_direction(value: Any) -> str:
    text = str(value or "unknown").strip().lower()
    if text in {"long", "bullish"}:
        return "long"
    if text in {"short", "bearish"}:
        return "short"
    return text or "unknown"


def scenario_scan(record: dict[str, Any]) -> dict[str, Any]:
    scan = first_present(record, ("features.scenario_scan", "diagnostics.scenario_scan", "scenario_scan"), {})
    return scan if isinstance(scan, dict) else {}


def candidate_items(record: dict[str, Any]) -> list[dict[str, Any]]:
    scan = scenario_scan(record)
    items: list[dict[str, Any]] = []
    for key in ("selected_scenario", "best_long_scenario", "best_short_scenario"):
        item = scan.get(key)
        if isinstance(item, dict):
            items.append(item)
    for key in ("top_candidates", "long_candidates", "short_candidates"):
        for item in scan.get(key) or []:
            if isinstance(item, dict):
                items.append(item)

    seen: set[str] = set()
    unique: list[dict[str, Any]] = []
    for item in items:
        key = str(item.get("candidate_id") or id(item))
        if key in seen:
            continue
        seen.add(key)
        unique.append(item)
    return unique


def candidate_key(symbol: str, candidate: dict[str, Any]) -> tuple[str, bool]:
    candidate_id = candidate.get("candidate_id")
    if candidate_id:
        return f"id:{candidate_id}", True
    parts = [
        "heuristic",
        symbol or "unknown",
        normalize_direction(candidate.get("direction")),
        str(candidate.get("anchor_type") or candidate.get("current_step") or "unknown"),
        str(candidate.get("anchor_index") or candidate.get("candidate_created_at") or candidate.get("last_event_index") or "unknown"),
        str(candidate.get("candidate_created_at") or "unknown"),
    ]
    return "|".join(parts), False


def infer_scenario_type(candidate: dict[str, Any]) -> str:
    return str(candidate.get("anchor_type") or nested_get(candidate, "scenario_key.anchor_type") or candidate.get("current_step") or "unknown")


def collect_stages(record: dict[str, Any], candidate: dict[str, Any], delivered_ids: set[str]) -> set[str]:
    stages: set[str] = set()
    for event in candidate.get("events_used") or []:
        stage = EVENT_STAGE_MAP.get(str(event.get("event_type") or ""))
        if stage:
            stages.add(stage)
    current_step = str(candidate.get("current_step") or "")
    next_step = str(candidate.get("next_expected_step") or "")
    trigger_scan = candidate.get("trigger_scan") if isinstance(candidate.get("trigger_scan"), dict) else {}
    if candidate.get("risk_valid") is True:
        stages.add("RISK_PLAN_VALID")
    if trigger_scan.get("early_trigger_confirmed") or current_step == "early_trigger_confirmed":
        stages.add("EARLY_TRIGGER_CONFIRMED")
    if trigger_scan.get("trigger_confirmed") or current_step == "confirmed_trigger_confirmed":
        stages.add("CONFIRMED_TRIGGER_CONFIRMED")
    if current_step in {"fvg_created", "fvg_retested", "signal_allowed"}:
        stages.add("FVG_CREATED")
    if current_step in {"fvg_retested", "displacement_confirmed", "risk_valid", "signal_allowed"}:
        stages.add("FVG_RETESTED")
    if (record.get("score") or 0) >= score_threshold(record):
        stages.add("SCORE_GATE_PASSED")
    gates = first_present(record, ("delivery_gate_checks.gates", "diagnostics.a_plus_delivery_gate.gates"), {})
    if isinstance(gates, dict) and gates.get("score_threshold"):
        stages.add("SCORE_GATE_PASSED")
    if record.get("production_a_plus_allowed") or record.get("a_plus_delivery_allowed"):
        stages.add("A_PLUS_ALLOWED")
    if candidate.get("candidate_id") in delivered_ids:
        stages.add("DELIVERED")
    if "EARLY_TRIGGER_CONFIRMED" in stages:
        stages.add("SFP_CONFIRMED")
    if "SFP_CONFIRMED" in stages:
        stages.add("HTF_CONTEXT_CONFIRMED")
    if "CONFIRMED_TRIGGER_CONFIRMED" in stages:
        stages.add("EARLY_TRIGGER_CONFIRMED")
    if "FVG_CREATED" in stages:
        stages.add("CONFIRMED_TRIGGER_CONFIRMED")
    if "FVG_RETESTED" in stages:
        stages.add("FVG_CREATED")
    if "RISK_PLAN_VALID" in stages:
        stages.add("FVG_RETESTED")
    if candidate.get("candidate_id") and candidate.get("candidate_id") in delivered_ids:
        stages.update({"A_PLUS_ALLOWED", "SCORE_GATE_PASSED"})
    return stages


def score_threshold(record: dict[str, Any]) -> float:
    return float(nested_get(record, "delivery_gate_checks.threshold", 85) or 85)


def collect_reasons(record: dict[str, Any], candidate: dict[str, Any]) -> tuple[list[str], list[str], list[str], list[str]]:
    trigger_scan = candidate.get("trigger_scan") if isinstance(candidate.get("trigger_scan"), dict) else {}
    primary_raw = [
        candidate.get("invalidated_reason"),
        candidate.get("risk_reason"),
        candidate.get("last_invalidated_component"),
        trigger_scan.get("rejected_reason"),
        first_present(record, ("diagnostics.trigger_scan_rejected_reason", "trigger_diagnostics.trigger_stage")),
        record.get("no_trade_reason"),
    ]
    primary = [normalize_reason(item) for item in primary_raw if item not in (None, "", [])]
    waiting = [normalize_reason(item) for item in [candidate.get("waiting_for"), trigger_scan.get("waiting_for")] if item]
    secondary: list[str] = []
    for event in candidate.get("event_diagnostics") or []:
        secondary.extend(normalize_reason(x) for x in event.get("failed_conditions") or [])
    debug = trigger_scan.get("confirmed_trigger_debug") if isinstance(trigger_scan.get("confirmed_trigger_debug"), dict) else {}
    for item in (debug.get("rejected_candidates") or []) + (debug.get("checked_candles") or []):
        secondary.extend(normalize_reason(x) for x in item.get("failed_conditions") or [])
        if item.get("primary_reason"):
            primary.append(normalize_reason(item.get("primary_reason")))
    missing = first_present(record, ("trigger_diagnostics.missing_conditions", "features.trigger_diagnostics.missing_conditions"), [])
    secondary.extend(normalize_reason(x) for x in missing or [])
    failed_gates = first_present(record, ("delivery_gate_checks.failed_gates", "diagnostics.a_plus_delivery_gate.failed_gates"), [])
    gates = [normalize_reason(x) for x in failed_gates or []]
    if (record.get("score") is not None) and float(record.get("score") or 0) < score_threshold(record):
        gates.append("score_below_min")
    return primary, secondary, waiting, gates


def update_candidate(
    candidates: dict[str, CandidateAggregate],
    record: dict[str, Any],
    candidate: dict[str, Any],
    path: str,
    delivered_ids: set[str],
) -> None:
    symbol = str(record.get("symbol") or nested_get(candidate, "payload.symbol") or "unknown")
    key, used_id = candidate_key(symbol, candidate)
    candidate_id = str(candidate.get("candidate_id") or key)
    if key not in candidates:
        candidates[key] = CandidateAggregate(
            key=key,
            candidate_id=candidate_id,
            symbol=symbol,
            direction=normalize_direction(candidate.get("direction")),
            scenario_type=infer_scenario_type(candidate),
            htf_regime=str(record.get("htf_context_class") or nested_get(record, "features.htf_context.direction") or "unknown"),
            created_at=candidate.get("candidate_created_at") or candidate.get("anchor_index"),
            used_candidate_id=used_id,
        )
    agg = candidates[key]
    timestamp = str(record.get("timestamp") or "")
    agg.first_seen_at = min(filter(None, [agg.first_seen_at, timestamp])) if agg.first_seen_at else timestamp
    agg.last_seen_at = max(filter(None, [agg.last_seen_at, timestamp])) if agg.last_seen_at else timestamp
    agg.stages.update(collect_stages(record, candidate, delivered_ids))
    agg.max_score = max([x for x in [agg.max_score, record.get("score")] if x is not None], default=None)
    agg.final_decision = str(record.get("final_decision") or record.get("decision") or agg.final_decision or "unknown")
    agg.row_count += 1
    agg.source_files.add(path)
    primary, secondary, waiting, gates = collect_reasons(record, candidate)
    agg.primary_reasons.update(primary)
    agg.secondary_conditions.update(secondary)
    agg.waiting_for.update(waiting)
    agg.failed_gate_reasons.update(gates)


def load_records(paths: Iterable[str]) -> tuple[list[tuple[str, dict[str, Any]]], dict[str, Any]]:
    rows: list[tuple[str, dict[str, Any]]] = []
    quality = {
        "malformed_rows": 0,
        "rows_without_candidate_id": 0,
        "rows_with_candidate_id": 0,
        "rows_without_failed_conditions": 0,
        "rows_with_failed_conditions": 0,
    }
    for path in paths:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    quality["malformed_rows"] += 1
                    continue
                rows.append((path, record))
                if record.get("candidate_id"):
                    quality["rows_with_candidate_id"] += 1
                else:
                    quality["rows_without_candidate_id"] += 1
                text = line
                if "failed_conditions" in text:
                    quality["rows_with_failed_conditions"] += 1
                else:
                    quality["rows_without_failed_conditions"] += 1
    return rows, quality


def delivered_candidate_ids(rows: Iterable[tuple[str, dict[str, Any]]]) -> set[str]:
    delivered = set()
    for _path, record in rows:
        if record.get("record_type") == "telegram_delivery" and record.get("sent") and record.get("candidate_id"):
            delivered.add(str(record["candidate_id"]))
    return delivered


def build_candidates(rows: Iterable[tuple[str, dict[str, Any]]]) -> tuple[dict[str, CandidateAggregate], dict[str, Any]]:
    rows = list(rows)
    delivered = delivered_candidate_ids(rows)
    candidates: dict[str, CandidateAggregate] = {}
    stats = {"symbol_scan_rows": 0, "scan_rows_without_candidates": 0, "heuristic_candidates": 0}
    for path, record in rows:
        if record.get("record_type") != "symbol_scan":
            continue
        stats["symbol_scan_rows"] += 1
        items = candidate_items(record)
        if not items:
            stats["scan_rows_without_candidates"] += 1
        for item in items:
            before = len(candidates)
            update_candidate(candidates, record, item, path, delivered)
            if len(candidates) > before and not item.get("candidate_id"):
                stats["heuristic_candidates"] += 1
    return candidates, stats


def funnel(candidates: Iterable[CandidateAggregate], *, group_key: Optional[str] = None) -> dict[str, list[dict[str, Any]]]:
    groups: dict[str, list[CandidateAggregate]] = defaultdict(list)
    for candidate in candidates:
        key = getattr(candidate, group_key) if group_key else "all"
        groups[str(key)].append(candidate)
    return {key: funnel_table(items) for key, items in sorted(groups.items())}


def funnel_table(candidates: list[CandidateAggregate]) -> list[dict[str, Any]]:
    total = len(candidates)
    previous = total
    rows = []
    for stage in STAGES:
        stage_index = STAGES.index(stage)
        reached = sum(1 for item in candidates if _stage_index(item.max_stage()) >= stage_index)
        rows.append(
            {
                "stage": stage,
                "reached": reached,
                "pct_total": round((reached / total * 100), 2) if total else 0.0,
                "conversion_from_previous": round((reached / previous * 100), 2) if previous else 0.0,
                "dropped": max(previous - reached, 0),
            }
        )
        previous = reached
    return rows


def _stage_index(stage: str) -> int:
    try:
        return STAGES.index(stage)
    except ValueError:
        return -1


def reason_tables(candidates: Iterable[CandidateAggregate]) -> dict[str, list[dict[str, Any]]]:
    primary_candidates: dict[str, set[str]] = defaultdict(set)
    secondary_candidates: dict[str, set[str]] = defaultdict(set)
    gate_candidates: dict[str, set[str]] = defaultdict(set)
    primary_rows = Counter()
    secondary_rows = Counter()
    gate_rows = Counter()
    for candidate in candidates:
        for reason, count in candidate.primary_reasons.items():
            primary_candidates[reason].add(candidate.key)
            primary_rows[reason] += count
        for reason, count in candidate.secondary_conditions.items():
            secondary_candidates[reason].add(candidate.key)
            secondary_rows[reason] += count
        for reason, count in candidate.failed_gate_reasons.items():
            gate_candidates[reason].add(candidate.key)
            gate_rows[reason] += count
    return {
        "primary_rejection_reasons": _reason_rows(primary_candidates, primary_rows),
        "secondary_failed_conditions": _reason_rows(secondary_candidates, secondary_rows),
        "delivery_gate_reasons": _reason_rows(gate_candidates, gate_rows),
    }


def _reason_rows(candidate_sets: dict[str, set[str]], row_counter: Counter) -> list[dict[str, Any]]:
    return [
        {"reason": reason, "unique_candidates": len(keys), "scan_rows_or_occurrences": row_counter[reason]}
        for reason, keys in sorted(candidate_sets.items(), key=lambda item: (-len(item[1]), item[0]))
    ]


def extract_bos(rows: Iterable[tuple[str, dict[str, Any]]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for path, record in rows:
        if record.get("record_type") != "symbol_scan":
            continue
        for candidate in candidate_items(record):
            trigger_scan = candidate.get("trigger_scan") if isinstance(candidate.get("trigger_scan"), dict) else {}
            debug = trigger_scan.get("confirmed_trigger_debug") if isinstance(trigger_scan.get("confirmed_trigger_debug"), dict) else {}
            confirmed = trigger_scan.get("confirmed_trigger")
            if isinstance(confirmed, dict) and "bos" in str(confirmed.get("type", "")).lower():
                out.append(_bos_row(path, record, candidate, confirmed, detected=True, confirmed=True))
            for item in debug.get("rejected_candidates") or []:
                if isinstance(item, dict) and "bos" in str(item.get("type", "")).lower():
                    out.append(_bos_row(path, record, candidate, item, detected=True, confirmed=False))
            for item in debug.get("checked_candles") or []:
                if isinstance(item, dict) and item.get("candidate_created") and not item.get("quality_score") is None:
                    out.append(_bos_row(path, record, candidate, item, detected=True, confirmed=False))
    return dedupe_bos(out)


def _bos_row(path: str, record: dict[str, Any], candidate: dict[str, Any], item: dict[str, Any], *, detected: bool, confirmed: bool) -> dict[str, Any]:
    failed = [normalize_reason(x) for x in item.get("failed_conditions") or []]
    return {
        "source_file": path,
        "symbol": record.get("symbol"),
        "candidate_id": candidate.get("candidate_id"),
        "direction": normalize_direction(candidate.get("direction") or item.get("direction")),
        "timestamp": item.get("index"),
        "type": item.get("type") or ("bearish_bos" if normalize_direction(candidate.get("direction")) == "short" else "bullish_bos"),
        "quality_score": item.get("quality_score"),
        "detected": detected,
        "confirmed": confirmed,
        "body_ratio": item.get("body_ratio"),
        "displacement_ratio": item.get("displacement_ratio"),
        "close_position": item.get("close_position"),
        "rvol": item.get("rvol"),
        "opposite_wick_ratio": item.get("opposite_wick_ratio"),
        "hold_confirmed": item.get("hold_confirmed"),
        "primary_reason": normalize_reason(item.get("primary_reason") or item.get("rejected_reason")),
        "failed_conditions": ";".join(failed) if failed else "not_recorded",
    }


def dedupe_bos(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen = set()
    out = []
    for row in rows:
        key = (row.get("symbol"), row.get("candidate_id"), row.get("timestamp"), row.get("quality_score"), row.get("confirmed"))
        if key in seen:
            continue
        seen.add(key)
        out.append(row)
    return out


def bos_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    config = BOSConfig()
    distributions = {
        metric: numeric_distribution([row.get(metric) for row in rows])
        for metric in ("quality_score", "body_ratio", "displacement_ratio", "close_position", "rvol")
    }
    near = Counter()
    double = Counter()
    for row in rows:
        quality = to_float(row.get("quality_score"))
        disp = to_float(row.get("displacement_ratio"))
        close = to_float(row.get("close_position"))
        rvol = to_float(row.get("rvol"))
        failed = set((row.get("failed_conditions") or "").split(";")) if row.get("failed_conditions") != "not_recorded" else set()
        hard_failed = bool(failed - {"quality_below_min", "quality_score_below_min"})
        quality_failed = quality is not None and quality < config.min_quality_score
        if quality is not None and config.min_quality_score - 10 <= quality < config.min_quality_score:
            near["quality_within_10_points"] += 1
        if disp is not None and config.min_displacement_atr * 0.9 <= disp < config.min_displacement_atr:
            near["displacement_shortfall_lte_10pct"] += 1
        if close is not None and config.min_close_position * 0.9 <= close < config.min_close_position:
            near["close_position_shortfall_lte_10pct"] += 1
        if rvol is not None and config.min_rvol * 0.85 <= rvol < config.min_rvol:
            near["rvol_shortfall_lte_15pct"] += 1
        if quality_failed and hard_failed:
            double["hard_conditions_and_quality_failed"] += 1
        elif quality_failed:
            double["all_recorded_hard_conditions_passed_but_quality_failed"] += 1
        elif hard_failed:
            double["quality_passed_but_hard_condition_failed"] += 1
        elif row.get("confirmed"):
            double["confirmed"] += 1
        else:
            double["not_recorded_or_other"] += 1
    return {
        "thresholds": {
            "quality_score": config.min_quality_score,
            "body_ratio": config.min_body_ratio,
            "displacement_ratio": config.min_displacement_atr,
            "close_position": config.min_close_position,
            "rvol": config.min_rvol,
        },
        "total_bos_candidates": len(rows),
        "confirmed_bos_candidates": sum(1 for row in rows if row.get("confirmed")),
        "distributions": distributions,
        "near_miss_groups": dict(near),
        "double_filter_counts": dict(double),
    }


def numeric_distribution(values: Iterable[Any]) -> dict[str, Any]:
    nums = sorted(x for x in (to_float(v) for v in values) if x is not None)
    if not nums:
        return {"count": 0, "min": None, "p25": None, "median": None, "p75": None, "max": None}
    return {
        "count": len(nums),
        "min": nums[0],
        "p25": percentile(nums, 0.25),
        "median": percentile(nums, 0.5),
        "p75": percentile(nums, 0.75),
        "max": nums[-1],
    }


def percentile(nums: list[float], q: float) -> float:
    if len(nums) == 1:
        return round(nums[0], 4)
    pos = (len(nums) - 1) * q
    lo = int(pos)
    hi = min(lo + 1, len(nums) - 1)
    frac = pos - lo
    return round(nums[lo] * (1 - frac) + nums[hi] * frac, 4)


def to_float(value: Any) -> Optional[float]:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def candidate_rows(candidates: Iterable[CandidateAggregate]) -> list[dict[str, Any]]:
    rows = []
    for item in sorted(candidates, key=lambda c: (c.symbol, c.direction, c.candidate_id)):
        rows.append(
            {
                "candidate_id": item.candidate_id,
                "symbol": item.symbol,
                "direction": item.direction,
                "scenario_type": item.scenario_type,
                "htf_regime": item.htf_regime,
                "created_at": item.created_at,
                "first_seen_at": item.first_seen_at,
                "last_seen_at": item.last_seen_at,
                "max_stage": item.max_stage(),
                "primary_stop_reason": item.primary_stop_reason(),
                "failed_conditions": ";".join(sorted(item.secondary_conditions)) if item.secondary_conditions else "not_recorded",
                "max_score": item.max_score,
                "final_decision": item.final_decision,
                "scan_rows": item.row_count,
                "used_candidate_id": item.used_candidate_id,
            }
        )
    return rows


def bottlenecks(funnel_rows: list[dict[str, Any]], reasons: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    top_reasons = reasons.get("primary_rejection_reasons") or []
    reason = top_reasons[0]["reason"] if top_reasons else "unknown"
    rows = sorted(
        [row for row in funnel_rows if row["stage"] not in {"HTF_CONTEXT_CONFIRMED", "DELIVERED"}],
        key=lambda row: row["conversion_from_previous"],
    )
    result = []
    for row in rows[:5]:
        result.append(
            {
                "stage": row["stage"],
                "conversion_rate": row["conversion_from_previous"],
                "main_reason": reason,
                "why_it_may_be_normal": "Filter may be excluding incomplete or low-quality setups before delivery.",
                "why_it_may_be_too_strict": "Large drop combined with near-miss or duplicated hard/quality checks can indicate over-filtering.",
                "missing_data": "Trade outcome labels per rejected candidate are not recorded in these journals.",
            }
        )
    return result


def summarize(rows: list[tuple[str, dict[str, Any]]], candidates: dict[str, CandidateAggregate], quality: dict[str, Any], build_stats: dict[str, Any]) -> dict[str, Any]:
    scan_rows = [record for _path, record in rows if record.get("record_type") == "symbol_scan"]
    symbols = sorted({str(record.get("symbol")) for record in scan_rows if record.get("symbol")})
    timestamps = sorted(str(record.get("timestamp")) for record in scan_rows if record.get("timestamp"))
    bos_rows = extract_bos(rows)
    reason_summary = reason_tables(candidates.values())
    all_funnel = funnel_table(list(candidates.values()))
    return {
        "input_files": sorted({path for path, _record in rows}),
        "period": {"from": timestamps[0] if timestamps else None, "to": timestamps[-1] if timestamps else None},
        "scan_rows": len(scan_rows),
        "symbols": {"count": len(symbols), "values": symbols},
        "unique_candidates": len(candidates),
        "quality": {
            **quality,
            **build_stats,
            "candidates_with_candidate_id": sum(1 for c in candidates.values() if c.used_candidate_id),
            "heuristic_deduplicated_candidates": sum(1 for c in candidates.values() if not c.used_candidate_id),
            "pre_patch_or_sparse_rows": quality.get("rows_without_failed_conditions", 0),
            "post_patch_failed_condition_rows": quality.get("rows_with_failed_conditions", 0),
        },
        "funnel": {"all": all_funnel},
        "funnel_by_direction": funnel(candidates.values(), group_key="direction"),
        "funnel_by_symbol": funnel(candidates.values(), group_key="symbol"),
        "funnel_by_scenario_type": funnel(candidates.values(), group_key="scenario_type"),
        "funnel_by_htf_regime": funnel(candidates.values(), group_key="htf_regime"),
        "reasons": reason_summary,
        "bos_audit": bos_summary(bos_rows),
        "bottlenecks": bottlenecks(all_funnel, reason_summary),
    }


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = sorted({key for row in rows for key in row.keys()})
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def run(input_globs: list[str], output_dir: Path) -> dict[str, Any]:
    paths = []
    for pattern in input_globs:
        paths.extend(glob.glob(pattern))
    paths = sorted(set(paths))
    rows, quality = load_records(paths)
    candidates, build_stats = build_candidates(rows)
    summary = summarize(rows, candidates, quality, build_stats)
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "candidate_funnel_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    write_csv(output_dir / "candidate_funnel_candidates.csv", candidate_rows(candidates.values()))
    write_csv(output_dir / "bos_near_misses.csv", extract_bos(rows))
    return summary


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Audit scenario candidate funnel from scan journals.")
    parser.add_argument("--input", action="append", default=["data/journal/scans_*.jsonl"], help="Input glob; may be repeated")
    parser.add_argument("--output-dir", type=Path, default=Path("runtime_data/research"))
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    summary = run(args.input, args.output_dir)
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
