#!/usr/bin/env python3
"""Analyze scanner JSONL history without touching production scanner behavior."""

from __future__ import annotations

import argparse
import csv
import glob
import html
import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Optional


UNKNOWN = "unknown"
DEFAULT_EXPECTED_SCAN_INTERVAL_MINUTES = 15.0
DEFAULT_GAP_MULTIPLIER = 2.0


@dataclass(frozen=True)
class ParseIssue:
    file: str
    line: int
    error: str


@dataclass
class NormalizedRecord:
    raw: dict[str, Any]
    record_type: str
    timestamp: Optional[datetime]
    timestamp_raw: Optional[str]
    run_id: Optional[str]
    symbol: Optional[str]
    file: str
    line: int


def nested_get(data: Any, path: str, default: Any = None) -> Any:
    current = data
    for part in path.split("."):
        if isinstance(current, dict) and part in current:
            current = current[part]
        else:
            return default
    return current


def first_present(data: dict[str, Any], paths: Iterable[str], default: Any = None) -> Any:
    for path in paths:
        value = nested_get(data, path)
        if value is not None:
            return value
    return default


def as_bool(value: Any) -> Optional[bool]:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "yes", "1", "pass", "ok"}:
            return True
        if lowered in {"false", "no", "0", "fail", "none", "null"}:
            return False
    return None


def as_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def parse_timestamp(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        dt = datetime.fromisoformat(text)
    except ValueError:
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
            try:
                dt = datetime.strptime(text, fmt)
                break
            except ValueError:
                dt = None
        if dt is None:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def iso(dt: Optional[datetime]) -> Optional[str]:
    return dt.isoformat() if dt is not None else None


def minutes_between(start: Optional[datetime], end: Optional[datetime]) -> Optional[float]:
    if start is None or end is None:
        return None
    return round(max(0.0, (end - start).total_seconds() / 60.0), 4)


def resolve_input_files(patterns: list[str]) -> list[Path]:
    files: list[Path] = []
    for pattern in patterns:
        matches = [Path(item) for item in glob.glob(pattern)]
        if not matches:
            path = Path(pattern)
            if path.is_dir():
                matches = sorted(path.glob("scans_*.jsonl"))
            elif path.exists():
                matches = [path]
        for path in sorted(matches):
            if path.is_file() and path not in files:
                files.append(path)
    return files


def load_records(files: list[Path], symbol: Optional[str] = None) -> tuple[list[NormalizedRecord], list[ParseIssue]]:
    records: list[NormalizedRecord] = []
    issues: list[ParseIssue] = []
    for path in files:
        with path.open("r", encoding="utf-8") as fh:
            for line_no, line in enumerate(fh, start=1):
                if not line.strip():
                    continue
                try:
                    raw = json.loads(line)
                except json.JSONDecodeError as exc:
                    issues.append(ParseIssue(str(path), line_no, str(exc)))
                    continue
                if not isinstance(raw, dict):
                    issues.append(ParseIssue(str(path), line_no, "JSON value is not an object"))
                    continue
                record_type = str(raw.get("record_type") or ("symbol_scan" if raw.get("symbol") else "unknown"))
                record_symbol = raw.get("symbol")
                if symbol and record_symbol and str(record_symbol).upper() != symbol.upper():
                    continue
                timestamp_raw = raw.get("timestamp") or raw.get("finished_at") or raw.get("started_at")
                records.append(
                    NormalizedRecord(
                        raw=raw,
                        record_type=record_type,
                        timestamp=parse_timestamp(timestamp_raw),
                        timestamp_raw=str(timestamp_raw) if timestamp_raw is not None else None,
                        run_id=raw.get("run_id"),
                        symbol=str(record_symbol) if record_symbol is not None else None,
                        file=str(path),
                        line=line_no,
                    )
                )
    records.sort(key=lambda item: (item.timestamp or datetime.min.replace(tzinfo=timezone.utc), item.file, item.line))
    return records, issues


def symbol_scan_records(records: list[NormalizedRecord]) -> list[NormalizedRecord]:
    return [item for item in records if item.record_type in {"symbol_scan", "unknown"} and item.symbol]


def scenario_transition_records(records: list[NormalizedRecord]) -> list[NormalizedRecord]:
    return [item for item in records if item.record_type == "scenario_transition"]


def telegram_delivery_records(records: list[NormalizedRecord]) -> list[NormalizedRecord]:
    return [item for item in records if item.record_type == "telegram_delivery"]


def selected_scenario(raw: dict[str, Any]) -> Optional[dict[str, Any]]:
    value = first_present(
        raw,
        [
            "features.scenario_scan.selected_scenario",
            "diagnostics.scenario_scan.selected_scenario",
            "scenario_scan.selected_scenario",
        ],
    )
    return value if isinstance(value, dict) else None


def scenario_scan(raw: dict[str, Any]) -> dict[str, Any]:
    value = first_present(raw, ["features.scenario_scan", "diagnostics.scenario_scan", "scenario_scan"], {})
    return value if isinstance(value, dict) else {}


def candidate_id(raw: dict[str, Any]) -> Optional[str]:
    selected = selected_scenario(raw) or {}
    value = first_present(
        {"selected": selected, "raw": raw},
        [
            "selected.candidate_id",
            "selected.trigger_scan.candidate_id",
            "raw.features.scenario_scan.selected_scenario_id",
            "raw.diagnostics.scenario_scan.selected_scenario_id",
            "raw.features.trigger_scan.candidate_id",
            "raw.diagnostics.trigger_scan.candidate_id",
            "raw.candidate_id",
        ],
    )
    return str(value) if value else None


def candidate_direction(raw: dict[str, Any]) -> Optional[str]:
    selected = selected_scenario(raw) or {}
    value = selected.get("direction") or first_present(raw, ["features.scenario_scan.selected_direction", "diagnostics.scenario_scan.selected_direction", "direction"])
    return str(value) if value is not None else None


def replay_candidate_id(raw: dict[str, Any]) -> Optional[str]:
    return candidate_id(raw) or first_present(raw, ["shadow_candidate_id", "features.shadow_candidate.shadow_candidate_id"])


def replay_candidate_direction(raw: dict[str, Any]) -> Optional[str]:
    if candidate_id(raw):
        return candidate_direction(raw)
    return first_present(raw, ["shadow_direction", "features.shadow_candidate.shadow_direction"]) or candidate_direction(raw)


def replay_candidate_source(raw: dict[str, Any]) -> str:
    return "scenario" if candidate_id(raw) else "shadow"


def event_types(raw: dict[str, Any]) -> set[str]:
    selected = selected_scenario(raw) or {}
    result = set()
    for event in selected.get("events_used") or []:
        if isinstance(event, dict):
            event_type = event.get("event_type") or event.get("type")
            if event_type:
                result.add(str(event_type).upper())
    return result


def tri_bool(value: Optional[bool]) -> str:
    if value is True:
        return "true"
    if value is False:
        return "false"
    return UNKNOWN


def count_values(values: Iterable[Any]) -> dict[str, int]:
    counter = Counter(str(value if value is not None else UNKNOWN) for value in values)
    return dict(sorted(counter.items(), key=lambda item: (-item[1], item[0])))


def pct(part: int, total: int) -> float:
    return round((part / total) * 100.0, 4) if total else 0.0


def build_global_summary(records: list[NormalizedRecord], files: list[Path], issues: list[ParseIssue]) -> dict[str, Any]:
    scans = symbol_scan_records(records)
    transitions = scenario_transition_records(records)
    deliveries = telegram_delivery_records(records)
    timestamps = [item.timestamp for item in records if item.timestamp is not None]
    run_ids = {item.run_id for item in scans if item.run_id}
    no_trade_counts = count_values(first_present(item.raw, ["no_trade_reason", "diagnostics.no_trade_reason"]) for item in scans)
    no_trade_total = sum(no_trade_counts.values())
    run_summaries = [item for item in records if item.record_type == "run_summary"]
    run_summary_count = len(run_summaries)
    run_summary_ids = {item.run_id for item in run_summaries if item.run_id}
    runs_without_summary = sorted(str(run_id) for run_id in run_ids if run_id not in run_summary_ids)
    delivery_type_counts = delivery_counts_by_type(deliveries)
    return {
        "files": [str(path) for path in files],
        "file_count": len(files),
        "scan_runs": len(run_ids) or run_summary_count,
        "runs_with_summary": len(run_summary_ids),
        "runs_without_summary": len(runs_without_summary),
        "incomplete_runs": len(runs_without_summary),
        "incomplete_run_ids": runs_without_summary[:200],
        "unique_symbols": len({item.symbol for item in scans if item.symbol}),
        "symbols": sorted({item.symbol for item in scans if item.symbol}),
        "symbol_scan_records": len(scans),
        "unique_candidates": len({candidate_id(item.raw) for item in scans if candidate_id(item.raw)}),
        "scenario_transitions": len(transitions),
        "telegram_deliveries": len(deliveries),
        "telegram_delivered": sum(1 for item in deliveries if as_bool(item.raw.get("sent")) is True),
        **delivery_type_counts,
        "first_timestamp": iso(min(timestamps)) if timestamps else None,
        "last_timestamp": iso(max(timestamps)) if timestamps else None,
        "malformed_line_count": len(issues),
        "malformed_lines": [issue.__dict__ for issue in issues],
        "no_trade_reason_counts": no_trade_counts,
        "no_trade_reason_percent": {key: pct(value, no_trade_total) for key, value in no_trade_counts.items()},
        "record_type_counts": count_values(item.record_type for item in records),
    }


def delivery_kind(message_type: Any) -> str:
    text = str(message_type or UNKNOWN).upper()
    if text == "A_PLUS":
        return "trade_alert"
    if text in {"DASHBOARD", "FULL", "HUNT"}:
        return "diagnostic_message"
    if "ERROR" in text:
        return "error_message"
    return "other_message"


def sanitize_text(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    return re.sub(r"/bot[^/\s]+/", "/bot<redacted>/", value)


def delivery_counts_by_type(deliveries: list[NormalizedRecord]) -> dict[str, int]:
    counts = Counter()
    for item in deliveries:
        if as_bool(item.raw.get("sent")) is True:
            counts[f"{delivery_kind(item.raw.get('message_type'))}_delivered"] += 1
    return {
        "trade_alert_delivered": counts.get("trade_alert_delivered", 0),
        "diagnostic_message_delivered": counts.get("diagnostic_message_delivered", 0),
        "error_message_delivered": counts.get("error_message_delivered", 0),
        "other_message_delivered": counts.get("other_message_delivered", 0),
    }


def htf_direction(raw: dict[str, Any]) -> Any:
    return first_present(raw, ["htf_context.direction", "features.htf_context.direction", "features.market_structure_4h.trend", "diagnostics.htf_trend"])


def has_directional_htf(raw: dict[str, Any]) -> Optional[bool]:
    trend = htf_direction(raw)
    if trend is None:
        return None
    return str(trend).lower() in {"bullish", "bearish"}


def has_candidate(raw: dict[str, Any]) -> Optional[bool]:
    scan = scenario_scan(raw)
    selected = selected_scenario(raw)
    counts = scan.get("candidate_counts") if isinstance(scan.get("candidate_counts"), dict) else {}
    if selected is not None:
        return True
    if counts:
        total = sum(int(value or 0) for key, value in counts.items() if key.endswith("_total") or key in {"living", "complete", "invalidated"})
        return total > 0
    if scan:
        return False
    return None


def trigger_detected(raw: dict[str, Any]) -> Optional[bool]:
    value = first_present(raw, ["diagnostics.trigger_confirmed", "features.trigger_scan.trigger_confirmed", "features.trigger_debug.trigger_confirmed"])
    parsed = as_bool(value)
    if parsed is not None:
        return parsed
    if first_present(raw, ["features.trigger_scan.selected_trigger", "features.trigger_debug.selected_trigger"]) is not None:
        return True
    return None


def trigger_diagnostics(raw: dict[str, Any]) -> dict[str, Any]:
    value = first_present(raw, ["trigger_diagnostics", "features.trigger_diagnostics"], {})
    return value if isinstance(value, dict) else {}


def risk_plan_valid(raw: dict[str, Any]) -> Optional[bool]:
    return as_bool(first_present(raw, ["features.risk_plan.valid", "diagnostics.risk_plan_valid", "diagnostics.risk_geometry_valid"]))


def a_plus_allowed(raw: dict[str, Any]) -> Optional[bool]:
    return as_bool(first_present(raw, ["diagnostics.a_plus_delivery_allowed", "diagnostics.a_plus_delivery_gate.allowed"]))


def build_filter_funnel(records: list[NormalizedRecord]) -> list[dict[str, Any]]:
    scans = symbol_scan_records(records)
    deliveries = telegram_delivery_records(records)
    stages: list[tuple[str, Iterable[NormalizedRecord], Callable[[dict[str, Any]], Optional[bool]]]] = [
        ("symbols scanned", scans, lambda raw: True),
        ("HTF directional context", scans, has_directional_htf),
        ("scenario candidate", scans, has_candidate),
        ("trigger detected", scans, trigger_detected),
        ("risk plan valid", scans, risk_plan_valid),
        ("A+ delivery allowed", scans, a_plus_allowed),
    ]
    rows = []
    for stage, source, fn in stages:
        values = [fn(item.raw) for item in source]
        true_count = sum(1 for value in values if value is True)
        false_count = sum(1 for value in values if value is False)
        unknown_count = sum(1 for value in values if value is None)
        rows.append(
            {
                "stage": stage,
                "cohort": "symbol_scan",
                "true": true_count,
                "false": false_count,
                "unknown": unknown_count,
                "total": len(values),
                "true_percent": pct(true_count, len(values)),
            }
        )
    sent = sum(1 for item in deliveries if as_bool(item.raw.get("sent")) is True)
    attempted = sum(1 for item in deliveries if as_bool(item.raw.get("attempted")) is not False)
    rows.append({"stage": "Telegram delivery attempted", "cohort": "telegram_delivery", "true": attempted, "false": len(deliveries) - attempted, "unknown": 0, "total": len(deliveries), "true_percent": pct(attempted, len(deliveries))})
    rows.append({"stage": "Telegram delivered", "cohort": "telegram_delivery", "true": sent, "false": len(deliveries) - sent, "unknown": 0, "total": len(deliveries), "true_percent": pct(sent, len(deliveries))})
    return rows


def symbol_timeline_point(item: NormalizedRecord) -> dict[str, Any]:
    raw = item.raw
    selected = selected_scenario(raw) or {}
    risk = first_present(raw, ["features.risk_plan"], {})
    risk = risk if isinstance(risk, dict) else {}
    trend = first_present(raw, ["features.trend_4h"], {})
    trend = trend if isinstance(trend, dict) else {}
    scan = scenario_scan(raw)
    state_machine_state = first_present(raw, ["breakdown.state_machine", "diagnostics.state_machine_state", "state_machine_state"])
    return {
        "symbol": item.symbol,
        "timestamp": iso(item.timestamp),
        "run_id": item.run_id,
        "htf_direction": htf_direction(raw),
        "adx": trend.get("adx"),
        "structure_reason": first_present(raw, ["features.market_structure_4h.reason"]),
        "candidate_direction": candidate_direction(raw),
        "candidate_id": candidate_id(raw),
        "scenario_state": selected.get("status") or scan.get("reason"),
        "state_machine_state": state_machine_state,
        "state_machine_diagnostic": state_machine_diagnostic_kind(state_machine_state),
        "no_trade_reason": first_present(raw, ["no_trade_reason", "diagnostics.no_trade_reason"]),
        "risk_plan_status": risk.get("risk_plan_status") or ("valid" if risk.get("valid") is True else "invalid" if risk else None),
        "a_plus_eligibility": tri_bool(a_plus_allowed(raw)),
        "telegram_delivery": UNKNOWN,
        "last_closed_4h": first_present(raw, ["market_data_timestamp_4h", "timeframes.4h_last_closed"]),
        "last_closed_1h": first_present(raw, ["market_data_timestamp_1h", "timeframes.1h_last_closed"]),
        "last_closed_15m": first_present(raw, ["market_data_timestamp_15m", "timeframes.15m_last_closed"]),
        "market_data_age": first_present(raw, ["market_data_age", "diagnostics.market_data_age"]),
    }


def state_machine_diagnostic_kind(value: Any) -> Optional[str]:
    text = str(value or "")
    if "Unexpected" in text:
        return "unexpected_transition"
    if "invalid" in text.lower():
        return "invalid_transition"
    return None


def build_htf_metrics_timeline(records: list[NormalizedRecord]) -> list[dict[str, Any]]:
    rows = []
    for item in symbol_scan_records(records):
        raw = item.raw
        htf = first_present(raw, ["htf_context", "features.htf_context"], {})
        htf = htf if isinstance(htf, dict) else {}
        structure = first_present(raw, ["features.market_structure_4h"], {})
        structure = structure if isinstance(structure, dict) else {}
        trend = first_present(raw, ["features.trend_4h"], {})
        trend = trend if isinstance(trend, dict) else {}
        rows.append({
            "symbol": item.symbol,
            "timestamp": iso(item.timestamp),
            "run_id": item.run_id,
            "adx": htf.get("adx", trend.get("adx")),
            "adx_threshold": htf.get("adx_threshold"),
            "p_di": trend.get("p_di"),
            "n_di": trend.get("n_di"),
            "swing_highs": first_present(raw, ["features.market_structure_4h.swing_highs", "swing_highs"]),
            "swing_lows": first_present(raw, ["features.market_structure_4h.swing_lows", "swing_lows"]),
            "swing_sequence": serialize_cell(htf.get("swing_sequence")),
            "swing_points": serialize_cell(htf.get("swing_points")),
            "hh_hl_lh_ll_classification": first_present(raw, ["features.market_structure_4h.classification", "features.market_structure_4h.structure_type"]),
            "htf_direction": htf.get("direction", structure.get("trend")),
            "structure_reason": htf.get("reason", structure.get("reason")),
            "protected_high": htf.get("protected_high"),
            "protected_low": htf.get("protected_low"),
            "last_break_type": htf.get("last_break_type"),
            "last_break_direction": htf.get("last_break_direction"),
            "last_break_index": htf.get("last_break_index"),
            "bull_score": htf.get("bull_score"),
            "bear_score": htf.get("bear_score"),
            "neutral_score": htf.get("neutral_score"),
            "conflicting_structure": htf.get("conflicting_structure"),
            "reason_flags": serialize_cell(htf.get("reason_flags")),
            "last_closed_4h": htf.get("last_closed_4h") or first_present(raw, ["market_data_timestamp_4h", "timeframes.4h_last_closed"]),
            "last_closed_1h": htf.get("last_closed_1h") or first_present(raw, ["market_data_timestamp_1h", "timeframes.1h_last_closed"]),
            "market_data_age_seconds": htf.get("market_data_age_seconds"),
            "market_data_age": first_present(raw, ["market_data_age", "diagnostics.market_data_age"]),
        })
    return rows


def compress_symbol_timelines(records: list[NormalizedRecord]) -> list[dict[str, Any]]:
    rows = [symbol_timeline_point(item) for item in symbol_scan_records(records)]
    rows.sort(key=lambda row: (str(row["symbol"]), row["timestamp"] or ""))
    output: list[dict[str, Any]] = []
    fields = [
        "htf_direction",
        "candidate_direction",
        "candidate_id",
        "scenario_state",
        "state_machine_state",
        "state_machine_diagnostic",
        "no_trade_reason",
        "risk_plan_status",
        "a_plus_eligibility",
        "telegram_delivery",
    ]
    for symbol, group_rows in group_by(rows, lambda row: row["symbol"]).items():
        current = None
        for row in group_rows:
            key = tuple(row.get(field) for field in fields)
            if current is None:
                current = {"symbol": symbol, "start_timestamp": row["timestamp"], "end_timestamp": row["timestamp"], "scan_count": 1, "run_ids": [row["run_id"]], **{field: row.get(field) for field in fields}, "adx": row.get("adx"), "structure_reason": row.get("structure_reason")}
                current["_key"] = key
                continue
            if current["_key"] == key:
                current["end_timestamp"] = row["timestamp"]
                current["scan_count"] += 1
                current["run_ids"].append(row["run_id"])
            else:
                current["run_ids"] = ",".join(str(run_id) for run_id in current["run_ids"] if run_id)
                current.pop("_key", None)
                output.append(current)
                current = {"symbol": symbol, "start_timestamp": row["timestamp"], "end_timestamp": row["timestamp"], "scan_count": 1, "run_ids": [row["run_id"]], **{field: row.get(field) for field in fields}, "adx": row.get("adx"), "structure_reason": row.get("structure_reason")}
                current["_key"] = key
        if current is not None:
            current["run_ids"] = ",".join(str(run_id) for run_id in current["run_ids"] if run_id)
            current.pop("_key", None)
            output.append(current)
    return output


def group_by(items: Iterable[Any], key_fn: Callable[[Any], Any]) -> dict[Any, list[Any]]:
    result: dict[Any, list[Any]] = defaultdict(list)
    for item in items:
        result[key_fn(item)].append(item)
    return dict(result)


def build_no_trade_reasons(records: list[NormalizedRecord]) -> list[dict[str, Any]]:
    scans = symbol_scan_records(records)
    counts = Counter(first_present(item.raw, ["no_trade_reason", "diagnostics.no_trade_reason"], UNKNOWN) for item in scans)
    total = sum(counts.values())
    return [{"no_trade_reason": str(reason), "count": count, "percent": pct(count, total)} for reason, count in counts.most_common()]


def build_symbol_state_summary(records: list[NormalizedRecord]) -> list[dict[str, Any]]:
    rows = [symbol_timeline_point(item) for item in symbol_scan_records(records)]
    rows.sort(key=lambda row: (str(row["symbol"]), row["timestamp"] or ""))
    output = []
    for symbol, group_rows in group_by(rows, lambda row: row["symbol"]).items():
        states = [str(row.get("no_trade_reason") or UNKNOWN) for row in group_rows]
        state_counts = Counter(states)
        changes = sum(1 for prev, cur in zip(states, states[1:]) if prev != cur)
        intervals = compress_state_intervals(group_rows, "no_trade_reason")
        durations = [row["duration_minutes"] for row in intervals if row["duration_minutes"] is not None]
        observed_durations = [row["observed_duration_minutes"] for row in intervals if row["observed_duration_minutes"] is not None]
        data_gaps = sum(int(row.get("data_gap_count", 0) or 0) for row in intervals)
        htf_changes = count_changes([row.get("htf_direction") for row in group_rows])
        candidate_direction_changes = count_changes([row.get("candidate_direction") for row in group_rows])
        neutral_pct = pct(state_counts.get("neutral_htf", 0), len(group_rows))
        output.append(
            {
                "symbol": symbol,
                "scan_count": len(group_rows),
                "state_changes": changes,
                "average_state_duration_minutes": round(sum(durations) / len(durations), 4) if durations else 0.0,
                "longest_unchanged_state_minutes": max(durations) if durations else 0.0,
                "average_observed_state_duration_minutes": round(sum(observed_durations) / len(observed_durations), 4) if observed_durations else 0.0,
                "longest_observed_unchanged_state_minutes": max(observed_durations) if observed_durations else 0.0,
                "data_gap_count": data_gaps,
                "htf_direction_changes": htf_changes,
                "candidate_direction_changes": candidate_direction_changes,
                "neutral_htf_percent": neutral_pct,
                "neutral_htf_warning": neutral_pct > 80.0,
                "state_percentages": json.dumps({state: pct(count, len(group_rows)) for state, count in sorted(state_counts.items())}, ensure_ascii=False, sort_keys=True),
            }
        )
    return sorted(output, key=lambda row: str(row["symbol"]))


def count_changes(values: list[Any]) -> int:
    cleaned = [value for value in values if value is not None]
    return sum(1 for prev, cur in zip(cleaned, cleaned[1:]) if prev != cur)


def compress_state_intervals(rows: list[dict[str, Any]], field: str) -> list[dict[str, Any]]:
    intervals = []
    current = None
    gap_threshold = DEFAULT_EXPECTED_SCAN_INTERVAL_MINUTES * DEFAULT_GAP_MULTIPLIER
    for row in rows:
        value = row.get(field) or UNKNOWN
        ts = parse_timestamp(row.get("timestamp"))
        if current is None:
            current = {"state": value, "start": ts, "end": ts, "scans": 1, "observed_duration_minutes": 0.0, "data_gap_count": 0}
            continue
        gap_minutes = minutes_between(current["end"], ts)
        if gap_minutes is not None and gap_minutes > gap_threshold:
            current["data_gap_count"] += 1
        if current["state"] == value:
            if gap_minutes is not None and gap_minutes <= gap_threshold:
                current["observed_duration_minutes"] += gap_minutes
            current["end"] = ts
            current["scans"] += 1
        else:
            intervals.append({**current, "duration_minutes": minutes_between(current["start"], current["end"])})
            current = {"state": value, "start": ts, "end": ts, "scans": 1, "observed_duration_minutes": 0.0, "data_gap_count": 0}
    if current is not None:
        intervals.append({**current, "duration_minutes": minutes_between(current["start"], current["end"])})
    return intervals


def build_candidate_lifetime(records: list[NormalizedRecord]) -> list[dict[str, Any]]:
    scans = [item for item in symbol_scan_records(records) if candidate_id(item.raw)]
    deliveries_by_candidate = {
        str(first_present(item.raw, ["candidate_id", "source_candidate_id"]))
        for item in telegram_delivery_records(records)
        if first_present(item.raw, ["candidate_id", "source_candidate_id"]) and as_bool(item.raw.get("sent")) is True
    }
    deliveries_by_run_symbol = {
        (item.run_id, item.symbol)
        for item in telegram_delivery_records(records)
        if as_bool(item.raw.get("sent")) is True and item.run_id and item.symbol
    }
    output = []
    for cid, group in group_by(scans, lambda item: candidate_id(item.raw)).items():
        group.sort(key=lambda item: item.timestamp or datetime.min.replace(tzinfo=timezone.utc))
        first = group[0]
        last = group[-1]
        completed_steps = [as_float(first_present(item.raw, ["features.scenario_scan.selected_scenario.completed_steps", "diagnostics.scenario_scan.selected_scenario.completed_steps"])) or 0 for item in group]
        risk_valid_ever = any(risk_plan_valid(item.raw) is True for item in group)
        a_plus_ever = any(a_plus_allowed(item.raw) is True for item in group)
        selected_last = selected_scenario(last.raw) or {}
        output.append(
            {
                "candidate_id": cid,
                "symbol": first.symbol,
                "direction": candidate_direction(first.raw),
                "first_seen": iso(first.timestamp),
                "last_seen": iso(last.timestamp),
                "lifetime_scans": len(group),
                "lifetime_minutes": minutes_between(first.timestamp, last.timestamp),
                "maximum_completed_scenario_step": int(max(completed_steps)) if completed_steps else None,
                "final_state": selected_last.get("status") or first_present(last.raw, ["features.scenario_scan.reason", "diagnostics.scenario_scan.reason"]),
                "invalidation_reason": selected_last.get("invalidated_reason") or first_present(last.raw, ["features.scenario_scan.best_long_scenario.invalidated_reason", "features.scenario_scan.best_short_scenario.invalidated_reason"]),
                "risk_plan_became_valid": risk_valid_ever,
                "a_plus_delivery_allowed": a_plus_ever,
                "telegram_delivery_happened": True if cid in deliveries_by_candidate else (True if (last.run_id, last.symbol) in deliveries_by_run_symbol else UNKNOWN),
            }
        )
    return sorted(output, key=lambda row: (str(row["symbol"]), str(row["first_seen"])))


def scenario_events_summary(selected: dict[str, Any]) -> tuple[list[str], Optional[str], Optional[str]]:
    event_names = []
    last_event_type = None
    last_event_time = None
    for event in selected.get("events_used") or []:
        if not isinstance(event, dict):
            continue
        event_type = event.get("event_type") or event.get("type")
        if event_type:
            event_names.append(str(event_type))
            last_event_type = str(event_type)
        if event.get("index") is not None:
            last_event_time = str(event.get("index"))
    return event_names, last_event_type, last_event_time


def required_next_event(selected: dict[str, Any], raw: dict[str, Any]) -> Optional[str]:
    trigger_scan = selected.get("trigger_scan") if isinstance(selected.get("trigger_scan"), dict) else {}
    value = (
        selected.get("next_expected_step")
        or selected.get("waiting_for")
        or trigger_scan.get("waiting_for")
        or trigger_scan.get("rejected_reason")
        or first_present(raw, ["diagnostics.trigger_scan_rejected_reason"])
    )
    return str(value) if value is not None else None


def stopped_reason(last: NormalizedRecord, selected: dict[str, Any], disappeared: bool) -> str:
    invalidated = selected.get("invalidated_reason")
    if invalidated:
        return f"invalidated:{invalidated}"
    if selected.get("status") == "complete":
        return "complete_but_not_delivered"
    next_event = required_next_event(selected, last.raw)
    trigger_ok = trigger_detected(last.raw)
    if trigger_ok is not True and next_event:
        return f"waiting_for:{next_event}"
    if trigger_ok is True:
        return "trigger_seen_waiting_for_downstream_gate"
    if disappeared:
        return "candidate_disappeared_before_window_end"
    return "candidate_still_waiting"


def build_trigger_loss_report(records: list[NormalizedRecord]) -> list[dict[str, Any]]:
    scans = [item for item in symbol_scan_records(records) if candidate_id(item.raw)]
    if not scans:
        return []

    latest_symbol_scan: dict[str, NormalizedRecord] = {}
    for item in symbol_scan_records(records):
        if item.symbol and item.timestamp:
            current = latest_symbol_scan.get(item.symbol)
            if current is None or (current.timestamp and item.timestamp > current.timestamp):
                latest_symbol_scan[item.symbol] = item

    transitions_by_candidate = group_by(
        [item for item in scenario_transition_records(records) if item.raw.get("candidate_id")],
        lambda item: str(item.raw.get("candidate_id")),
    )
    rows = []
    for cid, group in group_by(scans, lambda item: candidate_id(item.raw)).items():
        group.sort(key=lambda item: item.timestamp or datetime.min.replace(tzinfo=timezone.utc))
        first = group[0]
        last = group[-1]
        latest_for_symbol = latest_symbol_scan.get(first.symbol)
        disappeared = bool(
            latest_for_symbol is not None
            and latest_for_symbol.timestamp is not None
            and last.timestamp is not None
            and latest_for_symbol.timestamp > last.timestamp
        )
        selected_last = selected_scenario(last.raw) or {}
        completed_steps = [
            as_float(first_present(item.raw, [
                "features.scenario_scan.selected_scenario.completed_steps",
                "diagnostics.scenario_scan.selected_scenario.completed_steps",
            ])) or 0
            for item in group
        ]
        first_required = None
        wait_started_at = None
        last_required = required_next_event(selected_last, last.raw)
        for item in group:
            selected = selected_scenario(item.raw) or {}
            req = required_next_event(selected, item.raw)
            if first_required is None:
                first_required = req
            if req == last_required and wait_started_at is None:
                wait_started_at = item.timestamp
        event_names, last_event_type, last_event_time = scenario_events_summary(selected_last)
        trigger_diag = trigger_diagnostics(last.raw)
        near_miss = trigger_diag.get("near_miss") if isinstance(trigger_diag.get("near_miss"), dict) else {}
        missing_conditions = trigger_diag.get("missing_conditions") if isinstance(trigger_diag.get("missing_conditions"), list) else []
        last_observed_events = trigger_diag.get("last_observed_events") if isinstance(trigger_diag.get("last_observed_events"), list) else event_names
        trigger_stage = trigger_diag.get("trigger_stage") or _trigger_stage_from_required_for_report(last_required)
        bars_waiting = trigger_diag.get("bars_waiting")
        scans_waiting = trigger_diag.get("scans_waiting")
        transition_group = transitions_by_candidate.get(str(cid), [])
        transition_group.sort(key=lambda item: item.timestamp or datetime.min.replace(tzinfo=timezone.utc))
        transition_states = [str(item.raw.get("to_state")) for item in transition_group if item.raw.get("to_state")]
        trigger_seen_ever = any(trigger_detected(item.raw) is True for item in group)
        risk_seen_ever = any(risk_plan_valid(item.raw) is True for item in group)
        a_plus_seen_ever = any(a_plus_allowed(item.raw) is True for item in group)
        expired_because = None
        if disappeared:
            expired_because = "candidate_disappeared_before_window_end"
        elif selected_last.get("invalidated_reason"):
            expired_because = selected_last.get("invalidated_reason")
        elif latest_for_symbol is not None and latest_for_symbol is last:
            expired_because = "still_visible_at_report_window_end"
        rows.append(
            {
                "symbol": first.symbol,
                "candidate_id": cid,
                "direction": candidate_direction(last.raw) or candidate_direction(first.raw),
                "first_seen": iso(first.timestamp),
                "last_seen": iso(last.timestamp),
                "lifetime_scans": len(group),
                "lifetime_minutes": minutes_between(first.timestamp, last.timestamp),
                "last_completed_step": selected_last.get("current_step"),
                "maximum_completed_scenario_step": int(max(completed_steps)) if completed_steps else None,
                "final_status": selected_last.get("status"),
                "why_stopped": stopped_reason(last, selected_last, disappeared),
                "trigger_stage": trigger_stage,
                "required_next_event": last_required,
                "first_required_event": first_required,
                "waited_for_current_event_scans": sum(1 for item in group if required_next_event(selected_scenario(item.raw) or {}, item.raw) == last_required),
                "waited_for_current_event_minutes": minutes_between(wait_started_at, last.timestamp),
                "bars_waiting": bars_waiting,
                "scans_waiting": scans_waiting,
                "missing_conditions": ",".join(str(item) for item in missing_conditions),
                "last_observed_events": ",".join(str(item) for item in last_observed_events),
                "closest_failed_condition": near_miss.get("closest_failed_condition"),
                "condition_value": near_miss.get("condition_value"),
                "condition_threshold": near_miss.get("condition_threshold"),
                "near_miss_ratio": near_miss.get("near_miss_ratio"),
                "expired_because": expired_because,
                "candidate_disappeared_before_window_end": disappeared,
                "trigger_seen_ever": trigger_seen_ever,
                "risk_plan_valid_ever": risk_seen_ever,
                "a_plus_allowed_ever": a_plus_seen_ever,
                "last_event_type": last_event_type,
                "last_event_time": last_event_time,
                "events_seen": ",".join(event_names),
                "transition_count": len(transition_group),
                "transition_path": " -> ".join(transition_states),
                "latest_transition_state": transition_states[-1] if transition_states else None,
                "invalidation_reason": selected_last.get("invalidated_reason"),
                "trigger_rejected_reason": first_present(last.raw, [
                    "features.trigger_scan.rejected_reason",
                    "diagnostics.trigger_scan_rejected_reason",
                    "features.trigger_debug.trigger_rejected_reason",
                ]),
                "scenario_scan_reason": first_present(last.raw, [
                    "features.scenario_scan.reason",
                    "diagnostics.scenario_scan_reason",
                    "diagnostics.scenario_scan.reason",
                ]),
            }
        )
    return sorted(rows, key=lambda row: (str(row["symbol"]), str(row["first_seen"]), str(row["candidate_id"])))


def _trigger_stage_from_required_for_report(required_next_event: Any) -> str:
    text = str(required_next_event or "").upper()
    if "EARLY_TRIGGER" in text or "CHOCH" in text:
        return "waiting_for_early_trigger"
    if "CONFIRMED_TRIGGER" in text or "BOS" in text:
        return "waiting_for_confirmed_trigger"
    if "FVG_CREATED" in text:
        return "waiting_for_fvg_creation"
    if "FVG_RETESTED" in text:
        return "waiting_for_fvg_retest"
    if "DISPLACEMENT" in text:
        return "waiting_for_displacement"
    return "not_waiting_for_trigger"


def build_trigger_stage_distribution(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    counts = Counter(row.get("trigger_stage") or UNKNOWN for row in rows)
    total = sum(counts.values())
    return [
        {"trigger_stage": stage, "candidate_count": count, "percent": pct(count, total)}
        for stage, count in counts.most_common()
    ]


def build_missing_condition_counts(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    counts = Counter()
    for row in rows:
        for condition in str(row.get("missing_conditions") or "").split(","):
            if condition:
                counts[condition] += 1
    total = len(rows)
    return [
        {"missing_condition": condition, "candidate_count": count, "percent_of_candidates": pct(count, total)}
        for condition, count in counts.most_common()
    ]


def build_near_miss_candidates(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    output = []
    for row in rows:
        ratio = as_float(row.get("near_miss_ratio"))
        if ratio is None:
            continue
        output.append({
            "symbol": row.get("symbol"),
            "candidate_id": row.get("candidate_id"),
            "trigger_stage": row.get("trigger_stage"),
            "closest_failed_condition": row.get("closest_failed_condition"),
            "condition_value": row.get("condition_value"),
            "condition_threshold": row.get("condition_threshold"),
            "near_miss_ratio": ratio,
            "required_next_event": row.get("required_next_event"),
            "missing_conditions": row.get("missing_conditions"),
        })
    return sorted(output, key=lambda row: float(row.get("near_miss_ratio") or 0.0), reverse=True)


def build_waiting_time_by_required_event(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    output = []
    for required, group in group_by(rows, lambda row: row.get("required_next_event") or UNKNOWN).items():
        minutes = [as_float(row.get("waited_for_current_event_minutes")) for row in group if as_float(row.get("waited_for_current_event_minutes")) is not None]
        scans = [as_float(row.get("scans_waiting") or row.get("waited_for_current_event_scans")) for row in group if as_float(row.get("scans_waiting") or row.get("waited_for_current_event_scans")) is not None]
        bars = [as_float(row.get("bars_waiting")) for row in group if as_float(row.get("bars_waiting")) is not None]
        output.append({
            "required_next_event": required,
            "candidate_count": len(group),
            "average_wait_minutes": round(sum(minutes) / len(minutes), 4) if minutes else None,
            "average_scans_waiting": round(sum(scans) / len(scans), 4) if scans else None,
            "average_bars_waiting": round(sum(bars) / len(bars), 4) if bars else None,
        })
    return sorted(output, key=lambda row: str(row["required_next_event"]))


def candidate_creation_timestamp(raw: dict[str, Any]) -> Optional[datetime]:
    selected = selected_scenario(raw) or {}
    value = first_present(
        {"selected": selected, "raw": raw},
        [
            "selected.candidate_created_at",
            "selected.anchor_index",
            "raw.shadow_created_at",
            "raw.features.shadow_candidate.shadow_created_at",
            "raw.market_data_timestamp_15m",
            "raw.timeframes.15m_last_closed",
            "raw.timestamp",
        ],
    )
    return parse_timestamp(value)


def market_timestamp(raw: dict[str, Any]) -> Optional[datetime]:
    return parse_timestamp(first_present(raw, ["market_data_timestamp_15m", "timeframes.15m_last_closed", "timestamp"]))


def candle_from_record(item: NormalizedRecord) -> Optional[dict[str, Any]]:
    ts = market_timestamp(item.raw)
    open_price = as_float(first_present(item.raw, ["market_open_15m"]))
    high = as_float(first_present(item.raw, ["market_high_15m"]))
    low = as_float(first_present(item.raw, ["market_low_15m"]))
    close = as_float(first_present(item.raw, ["market_close_15m", "current_price"]))
    if ts is None or open_price is None or high is None or low is None or close is None:
        return None
    return {
        "timestamp": ts,
        "timestamp_raw": first_present(item.raw, ["market_data_timestamp_15m", "timeframes.15m_last_closed", "timestamp"]),
        "open": open_price,
        "high": high,
        "low": low,
        "close": close,
        "atr": as_float(item.raw.get("atr")),
        "raw": item.raw,
    }


def direction_side(direction: Any) -> Optional[str]:
    text = str(direction or "").lower()
    if text in {"long", "bullish"}:
        return "bullish"
    if text in {"short", "bearish"}:
        return "bearish"
    return None


def event_matches_direction(event_type: Any, direction: Any) -> bool:
    side = direction_side(direction)
    text = str(event_type or "").lower()
    if side == "bullish":
        return "bullish" in text or str(direction or "").upper() == "LONG"
    if side == "bearish":
        return "bearish" in text or str(direction or "").upper() == "SHORT"
    return False


def first_live_event(group: list[NormalizedRecord], component: str, direction: Any) -> tuple[bool, Optional[int], Optional[str]]:
    wanted = {
        "liquidity_sweep": {"SFP_CONFIRMED", "LIQUIDITY_SWEEP_CONFIRMED"},
        "choch": {"CHOCH_CONFIRMED", "EARLY_TRIGGER_CONFIRMED"},
        "bos": {"BOS_CONFIRMED", "CONFIRMED_TRIGGER_CONFIRMED"},
        "fvg_creation": {"FVG_CREATED"},
        "fvg_retest": {"FVG_RETESTED"},
        "displacement": {"DISPLACEMENT_CONFIRMED"},
    }.get(component, set())
    for offset, item in enumerate(group, start=0):
        selected = selected_scenario(item.raw) or {}
        for event in selected.get("events_used") or []:
            if not isinstance(event, dict):
                continue
            event_type = str(event.get("event_type") or event.get("type") or "").upper()
            if event_type in wanted:
                return True, offset, str(event.get("index") or item.raw.get("market_data_timestamp_15m") or item.timestamp_raw)
        if component == "liquidity_sweep":
            sfp = first_present(item.raw, ["features.sfp"], {})
            if isinstance(sfp, dict) and event_matches_direction(sfp.get("type"), direction):
                return True, offset, str(sfp.get("index") or item.raw.get("market_data_timestamp_15m") or item.timestamp_raw)
        if component in {"choch", "bos"}:
            trigger = first_present(item.raw, ["features.trigger_15m"], {})
            trigger_type = trigger.get("type") if isinstance(trigger, dict) else None
            if trigger_type and component in str(trigger_type).lower() and event_matches_direction(trigger_type, direction):
                return True, offset, str(trigger.get("index") or item.raw.get("market_data_timestamp_15m") or item.timestamp_raw)
    return False, None, None


def replay_displacement(candles: list[dict[str, Any]], direction: Any, threshold: float = 0.5) -> tuple[bool, Optional[int], Optional[str]]:
    side = direction_side(direction)
    for idx, candle in enumerate(candles, start=1):
        atr = candle.get("atr") or 0.0
        if atr <= 0:
            continue
        direction_ok = candle["close"] > candle["open"] if side == "bullish" else candle["close"] < candle["open"]
        ratio = abs(candle["close"] - candle["open"]) / atr
        if direction_ok and ratio >= threshold:
            return True, idx, str(candle["timestamp_raw"])
    return False, None, None


def replay_fvg_creation(candles: list[dict[str, Any]], direction: Any, min_size_atr_ratio: float = 0.5) -> tuple[bool, Optional[int], Optional[str], Optional[dict[str, Any]]]:
    side = direction_side(direction)
    for pos in range(2, len(candles)):
        current = candles[pos]
        prev_2 = candles[pos - 2]
        atr = current.get("atr") or 0.0
        if side == "bullish" and current["low"] > prev_2["high"]:
            top = current["low"]
            bottom = prev_2["high"]
        elif side == "bearish" and current["high"] < prev_2["low"]:
            top = prev_2["low"]
            bottom = current["high"]
        else:
            continue
        size = top - bottom
        if size <= 0:
            continue
        if atr and size < atr * min_size_atr_ratio:
            continue
        fvg = {"type": side, "top": top, "bottom": bottom, "created_at": current["timestamp_raw"], "created_pos": pos}
        return True, pos + 1, str(current["timestamp_raw"]), fvg
    return False, None, None, None


def replay_fvg_retest(candles: list[dict[str, Any]], fvg: Optional[dict[str, Any]]) -> tuple[bool, Optional[int], Optional[str]]:
    if not fvg:
        return False, None, None
    for pos in range(int(fvg["created_pos"]) + 1, len(candles)):
        candle = candles[pos]
        if candle["low"] <= fvg["top"] and candle["high"] >= fvg["bottom"]:
            return True, pos + 1, str(candle["timestamp_raw"])
    return False, None, None


def component_row(candidate: dict[str, Any], component: str, replay: tuple[Any, ...], live: tuple[Any, ...]) -> dict[str, Any]:
    replay_detected, replay_bars, replay_ts = replay[:3]
    live_detected, live_bars, live_ts = live[:3]
    return {
        "candidate_id": candidate["candidate_id"],
        "candidate_source": candidate["candidate_source"],
        "symbol": candidate["symbol"],
        "direction": candidate["direction"],
        "candidate_created_at": candidate["created_at"],
        "component": component,
        "first_seen_after_bars": replay_bars,
        "first_seen_timestamp": replay_ts,
        "detected_in_replay": bool(replay_detected),
        "detected_live": bool(live_detected),
        "live_first_seen_after_bars": live_bars,
        "live_first_seen_timestamp": live_ts,
        "live_vs_replay_match": bool(replay_detected) == bool(live_detected),
    }


def build_trigger_replay_report(records: list[NormalizedRecord], lookahead_bars: int = 20) -> list[dict[str, Any]]:
    scans = [item for item in symbol_scan_records(records) if replay_candidate_id(item.raw)]
    if not scans:
        return []
    symbol_scans = group_by(symbol_scan_records(records), lambda item: item.symbol)
    for group in symbol_scans.values():
        group.sort(key=lambda item: market_timestamp(item.raw) or item.timestamp or datetime.min.replace(tzinfo=timezone.utc))

    rows = []
    for cid, group in group_by(scans, lambda item: replay_candidate_id(item.raw)).items():
        group.sort(key=lambda item: market_timestamp(item.raw) or item.timestamp or datetime.min.replace(tzinfo=timezone.utc))
        first = group[0]
        direction = replay_candidate_direction(first.raw)
        created = candidate_creation_timestamp(first.raw) or market_timestamp(first.raw) or first.timestamp
        if created is None:
            continue
        future_items = []
        for item in symbol_scans.get(first.symbol, []):
            ts = market_timestamp(item.raw) or item.timestamp
            if ts is not None and ts > created:
                future_items.append(item)
            if len(future_items) >= lookahead_bars:
                break
        candles = [candle for candle in (candle_from_record(item) for item in future_items) if candle is not None]
        candidate = {
            "candidate_id": cid,
            "candidate_source": replay_candidate_source(first.raw),
            "symbol": first.symbol,
            "direction": direction,
            "created_at": iso(created),
        }

        live_group = [
            item for item in group
            if (market_timestamp(item.raw) or item.timestamp or created) >= created
        ][:lookahead_bars]
        replay_fvg, fvg_bars, fvg_ts, fvg_payload = replay_fvg_creation(candles, direction)
        replay_retest, retest_bars, retest_ts = replay_fvg_retest(candles, fvg_payload)
        component_replays = {
            "liquidity_sweep": first_live_event(future_items, "liquidity_sweep", direction),
            "choch": first_live_event(future_items, "choch", direction),
            "bos": first_live_event(future_items, "bos", direction),
            "displacement": replay_displacement(candles, direction),
            "fvg_creation": (replay_fvg, fvg_bars, fvg_ts),
            "fvg_retest": (replay_retest, retest_bars, retest_ts),
        }
        for component, replay in component_replays.items():
            live = first_live_event(live_group, component, direction)
            rows.append(component_row(candidate, component, replay, live))
    return sorted(rows, key=lambda row: (str(row["symbol"]), str(row["candidate_id"]), str(row["component"])))


def build_trigger_replay_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    candidates = sorted({row["candidate_id"] for row in rows})
    fvg_rows = [row for row in rows if row.get("component") == "fvg_creation"]
    summary = {
        "candidate_count": len(candidates),
        "candidate_source_counts": dict(Counter(row["candidate_source"] for row in rows if row.get("component") == "fvg_creation").most_common()),
        "component_detected_in_replay_counts": dict(Counter(row["component"] for row in rows if row.get("detected_in_replay") is True).most_common()),
        "component_detected_live_counts": dict(Counter(row["component"] for row in rows if row.get("detected_live") is True).most_common()),
        "live_vs_replay_mismatch_count": sum(1 for row in rows if row.get("live_vs_replay_match") is False),
    }
    for source, source_rows in group_by(fvg_rows, lambda row: row.get("candidate_source") or UNKNOWN).items():
        source_count = len(source_rows)
        source_detected = sum(1 for row in source_rows if row.get("detected_in_replay") is True)
        summary[f"{source}_fvg_within_20_bars"] = source_detected
        summary[f"{source}_fvg_within_20_bars_percent"] = pct(source_detected, source_count)
    for window in (5, 10, 20):
        count = sum(
            1 for row in fvg_rows
            if row.get("detected_in_replay") is True and as_float(row.get("first_seen_after_bars")) is not None and as_float(row.get("first_seen_after_bars")) <= window
        )
        summary[f"fvg_within_{window}_bars"] = count
        summary[f"fvg_within_{window}_bars_percent"] = pct(count, len(fvg_rows))
    return summary


def build_telegram_delivery_report(records: list[NormalizedRecord]) -> list[dict[str, Any]]:
    scan_by_run_symbol = {}
    for item in symbol_scan_records(records):
        if item.run_id and item.symbol:
            scan_by_run_symbol[(item.run_id, item.symbol)] = item
    rows = []
    for item in telegram_delivery_records(records):
        linked_scan = scan_by_run_symbol.get((item.run_id, item.symbol))
        gate = item.raw.get("delivery_gate_result")
        if not isinstance(gate, dict) and linked_scan is not None:
            gate = first_present(linked_scan.raw, ["diagnostics.a_plus_delivery_gate", "delivery_gate_checks"])
        failed = gate.get("failed_gates") if isinstance(gate, dict) else None
        rows.append({
            "timestamp": iso(item.timestamp),
            "run_id": item.run_id,
            "symbol": item.symbol,
            "candidate_id": first_present(item.raw, ["candidate_id", "source_candidate_id"]) or (candidate_id(linked_scan.raw) if linked_scan else None),
            "scenario_id": item.raw.get("scenario_id") or (linked_scan.raw.get("scenario_id") if linked_scan else None),
            "message_type": item.raw.get("message_type"),
            "message_kind": delivery_kind(item.raw.get("message_type")),
            "delivery_status": "sent" if as_bool(item.raw.get("sent")) is True else "failed",
            "a_plus_delivery_allowed": tri_bool(as_bool(gate.get("allowed")) if isinstance(gate, dict) else (a_plus_allowed(linked_scan.raw) if linked_scan else None)),
            "delivery_gate_reasons": ",".join(str(reason) for reason in failed) if isinstance(failed, list) else None,
            "telegram_message_id": item.raw.get("telegram_message_id"),
            "status_code": item.raw.get("status_code"),
            "error": sanitize_text(item.raw.get("error")),
        })
    return rows


def build_run_completeness(records: list[NormalizedRecord]) -> list[dict[str, Any]]:
    scans_by_run = group_by(symbol_scan_records(records), lambda item: item.run_id)
    summaries = {item.run_id: item for item in records if item.record_type == "run_summary" and item.run_id}
    rows = []
    for run_id in sorted({key for key in scans_by_run if key} | set(summaries.keys())):
        scans = scans_by_run.get(run_id, [])
        summary = summaries.get(run_id)
        expected = first_present(summary.raw, ["symbols_total"]) if summary else None
        scanned = len(scans)
        timestamps = [item.timestamp for item in scans if item.timestamp]
        rows.append({
            "run_id": run_id,
            "has_run_summary": summary is not None,
            "symbols_scanned_per_run": scanned,
            "expected_symbols_per_run": expected,
            "incomplete_run": bool(summary is None or (expected is not None and scanned < int(expected))),
            "missing_symbol_count": (int(expected) - scanned) if expected is not None else None,
            "first_symbol_timestamp": iso(min(timestamps)) if timestamps else None,
            "last_symbol_timestamp": iso(max(timestamps)) if timestamps else None,
        })
    return rows


def build_state_machine_invalidations(records: list[NormalizedRecord]) -> list[dict[str, Any]]:
    rows = []
    for item in scenario_transition_records(records):
        invalidated_reason = item.raw.get("invalidated_reason")
        if invalidated_reason:
            rows.append(
                {
                    "symbol": item.symbol,
                    "timestamp": iso(item.timestamp),
                    "candidate_id": item.raw.get("candidate_id"),
                    "current_state": item.raw.get("from_state"),
                    "to_state": item.raw.get("to_state"),
                    "expected_event": None,
                    "received_event": item.raw.get("event_type"),
                    "direction": item.raw.get("direction"),
                    "invalidation_reason": invalidated_reason,
                    "source": "scenario_scanner",
                    "diagnostic": "scenario_transition_invalidation",
                    "related_scenario_events": None,
                }
            )
    for item in symbol_scan_records(records):
        selected = selected_scenario(item.raw) or {}
        trigger_scan = selected.get("trigger_scan") if isinstance(selected.get("trigger_scan"), dict) else first_present(item.raw, ["features.trigger_scan", "diagnostics.trigger_scan"], {})
        waiting_for = selected.get("waiting_for") or (trigger_scan.get("waiting_for") if isinstance(trigger_scan, dict) else None)
        next_expected = selected.get("next_expected_step")
        events = selected.get("events_used") or []
        contains_bos = any(str(event.get("event_type") or event.get("type") or "").upper() in {"BOS_CONFIRMED", "CONFIRMED_TRIGGER_CONFIRMED"} and "BOS" in str(event.get("event_type") or event.get("type") or "").upper() for event in events if isinstance(event, dict))
        waiting_for_choch = "choch" in str(waiting_for or next_expected or "").lower()
        if contains_bos and waiting_for_choch:
            rows.append(
                {
                    "symbol": item.symbol,
                    "timestamp": iso(item.timestamp),
                    "candidate_id": candidate_id(item.raw),
                    "current_state": selected.get("current_step") or selected.get("status"),
                    "to_state": selected.get("status"),
                    "expected_event": next_expected or waiting_for,
                    "received_event": "bos_confirmed",
                    "direction": selected.get("direction") or candidate_direction(item.raw),
                    "invalidation_reason": selected.get("invalidated_reason"),
                    "source": "scenario_scanner",
                    "diagnostic": "Unexpected bos_confirmed while waiting for choch_confirmed",
                    "related_scenario_events": json.dumps(events, ensure_ascii=False),
                }
            )
        state_machine_state = first_present(item.raw, ["breakdown.state_machine", "diagnostics.state_machine_state", "state_machine_state"])
        unexpected = parse_unexpected_state_machine(state_machine_state)
        if unexpected:
            rows.append({
                "symbol": item.symbol,
                "timestamp": iso(item.timestamp),
                "candidate_id": candidate_id(item.raw),
                "current_state": unexpected.get("current_state"),
                "to_state": None,
                "expected_event": unexpected.get("expected_event"),
                "received_event": unexpected.get("received_event"),
                "direction": candidate_direction(item.raw),
                "invalidation_reason": None,
                "source": "sniper_state_machine",
                "diagnostic": unexpected.get("diagnostic"),
                "related_scenario_events": json.dumps((selected_scenario(item.raw) or {}).get("events_used") or [], ensure_ascii=False),
            })
    return rows


def parse_unexpected_state_machine(value: Any) -> Optional[dict[str, Any]]:
    text = str(value or "")
    if "Unexpected" not in text:
        return None
    match = re.search(r"Unexpected\s+([A-Za-z0-9_]+)\s+while\s+waiting\s+for\s+([A-Za-z0-9_]+)", text)
    if not match:
        return {"diagnostic": text, "received_event": None, "expected_event": None, "current_state": text}
    return {
        "diagnostic": f"Unexpected {match.group(1)} while waiting for {match.group(2)}",
        "received_event": match.group(1),
        "expected_event": match.group(2),
        "current_state": f"waiting_for_{match.group(2)}",
    }


def build_state_machine_stats(records: list[NormalizedRecord], invalidation_rows: list[dict[str, Any]]) -> dict[str, Any]:
    transitions = scenario_transition_records(records)
    expected_pairs = Counter()
    to_states = Counter()
    invalidation_reasons = Counter()
    for item in transitions:
        from_state = item.raw.get("from_state") or UNKNOWN
        to_state = item.raw.get("to_state") or UNKNOWN
        expected_pairs[f"{from_state}->{to_state}"] += 1
        to_states[to_state] += 1
        if item.raw.get("invalidated_reason"):
            invalidation_reasons[str(item.raw.get("invalidated_reason"))] += 1
    unexpected_count = sum(
        1
        for row in invalidation_rows
        if row.get("diagnostic") == "Unexpected bos_confirmed while waiting for choch_confirmed"
    )
    return {
        "transition_records": len(transitions),
        "expected_transition_counts": dict(expected_pairs.most_common()),
        "to_state_counts": dict(to_states.most_common()),
        "invalid_transition_count": len([row for row in invalidation_rows if row.get("invalidation_reason")]),
        "scenario_scanner_invalidations": len([row for row in invalidation_rows if row.get("source") == "scenario_scanner" and row.get("invalidation_reason")]),
        "sniper_state_machine_invalidations": len([row for row in invalidation_rows if row.get("source") == "sniper_state_machine"]),
        "invalidation_reason_counts": dict(invalidation_reasons.most_common()),
        "unexpected_bos_while_waiting_for_choch_count": unexpected_count,
    }


def build_late_entry_report(records: list[NormalizedRecord]) -> list[dict[str, Any]]:
    output = []
    for item in symbol_scan_records(records):
        risk = first_present(item.raw, ["features.risk_plan"], {})
        risk = risk if isinstance(risk, dict) else {}
        if as_bool(risk.get("late_entry")) is not True:
            continue
        pd_data = first_present(item.raw, ["features.premium_discount"], {})
        pd_data = pd_data if isinstance(pd_data, dict) else {}
        current_price = first_present(item.raw, ["current_price", "features.current_price", "features.premium_discount.price"])
        poi = first_present(risk, ["poi", "poi_price", "entry"])
        rr = as_float(risk.get("rr_to_target_1"))
        stop_pct = as_float(risk.get("stop_distance_percent"))
        entry_distance = as_float(risk.get("entry_distance_from_poi_atr"))
        target_model = risk.get("target_model")
        reason = str(risk.get("reason") or "")
        anomalies = []
        if entry_distance == 0:
            anomalies.append("late_entry_true_but_stored_poi_distance_is_0")
        if stop_pct is not None and stop_pct < 0.01:
            anomalies.append("stop_distance_below_configured_minimum")
        if rr is not None and rr > 20:
            anomalies.append("rr_above_20r")
        if "no logical liquidity target" in reason.lower() or target_model in {None, "none"}:
            anomalies.append("missing_logical_liquidity_target")
        if target_model and "fallback" in str(target_model).lower():
            anomalies.append("fallback_target_used")
        output.append(
            {
                "symbol": item.symbol,
                "timestamp": iso(item.timestamp),
                "candidate_id": candidate_id(item.raw),
                "entry_model": risk.get("entry_model"),
                "planned_entry": risk.get("entry"),
                "current_price": current_price,
                "poi": poi,
                "atr": first_present(item.raw, ["features.atr", "atr", "features.trend_4h.atr"]),
                "planned_entry_distance_from_poi_atr": entry_distance,
                "current_price_distance_from_poi_atr": first_present(risk, ["current_price_distance_from_poi_atr"]),
                "max_entry_distance_from_poi_atr": first_present(risk, ["max_entry_distance_from_poi_atr"]),
                "stop_distance_percent": stop_pct,
                "rr_to_target_1": rr,
                "target_model": target_model,
                "risk_plan_reason": risk.get("reason"),
                "anomalies": ",".join(anomalies),
                "premium_discount_price": pd_data.get("price"),
            }
        )
    return output


def build_late_entry_incidents(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[Any, Any], list[dict[str, Any]]] = defaultdict(list)
    missing_candidate: list[dict[str, Any]] = []
    for row in rows:
        cid = row.get("candidate_id")
        if cid:
            grouped[(row.get("symbol"), cid)].append(row)
        else:
            missing_candidate.append(row)
    output = []
    for (symbol, cid), group in grouped.items():
        first = group[0]
        last = group[-1]
        anomaly_counts = Counter()
        for row in group:
            for anomaly in str(row.get("anomalies") or "").split(","):
                if anomaly:
                    anomaly_counts[anomaly] += 1
        output.append({
            "symbol": symbol,
            "candidate_id": cid,
            "snapshot_count": len(group),
            "first_timestamp": first.get("timestamp"),
            "last_timestamp": last.get("timestamp"),
            "anomaly_counts": json.dumps(dict(anomaly_counts), ensure_ascii=False, sort_keys=True),
            "latest_risk_plan_reason": last.get("risk_plan_reason"),
        })
    if missing_candidate:
        output.append({
            "symbol": UNKNOWN,
            "candidate_id": None,
            "snapshot_count": len(missing_candidate),
            "first_timestamp": missing_candidate[0].get("timestamp"),
            "last_timestamp": missing_candidate[-1].get("timestamp"),
            "anomaly_counts": json.dumps({"missing_candidate_id": len(missing_candidate)}, ensure_ascii=False, sort_keys=True),
            "latest_risk_plan_reason": None,
        })
    return sorted(output, key=lambda row: (str(row["symbol"]), str(row.get("candidate_id"))))


def write_json(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    fieldnames: list[str] = []
    for row in rows:
        for key in row:
            if key not in fieldnames:
                fieldnames.append(key)
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames or ["empty"])
        writer.writeheader()
        for row in rows:
            writer.writerow({key: serialize_cell(row.get(key)) for key in fieldnames})


def serialize_cell(value: Any) -> Any:
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return value


def render_table(title: str, rows: list[dict[str, Any]], limit: int = 50) -> str:
    if not rows:
        return f"<section><h2>{html.escape(title)}</h2><p>No rows.</p></section>"
    keys = list(rows[0].keys())
    head = "".join(f"<th>{html.escape(str(key))}</th>" for key in keys)
    body = []
    for row in rows[:limit]:
        body.append("<tr>" + "".join(f"<td>{html.escape(str(row.get(key, '')))}</td>" for key in keys) + "</tr>")
    more = f"<p>Showing {min(limit, len(rows))} of {len(rows)} rows.</p>" if len(rows) > limit else ""
    return f"<section><h2>{html.escape(title)}</h2>{more}<table><thead><tr>{head}</tr></thead><tbody>{''.join(body)}</tbody></table></section>"


def write_html(path: Path, summary: dict[str, Any], tables: dict[str, list[dict[str, Any]]]) -> None:
    cards = [
        ("Files", summary.get("file_count")),
        ("Runs", summary.get("scan_runs")),
        ("Symbols", summary.get("unique_symbols")),
        ("Symbol scans", summary.get("symbol_scan_records")),
        ("Candidates", summary.get("unique_candidates")),
        ("Telegram delivered", summary.get("telegram_delivered")),
        ("Trade alerts", summary.get("trade_alert_delivered")),
        ("Incomplete runs", summary.get("incomplete_runs")),
        ("Late-entry incidents", summary.get("late_entry_unique_incidents")),
        ("Malformed lines", summary.get("malformed_line_count")),
    ]
    card_html = "".join(f"<div class='card'><span>{html.escape(str(label))}</span><strong>{html.escape(str(value))}</strong></div>" for label, value in cards)
    sections = [
        render_table("Filtering Funnel", tables["filter_funnel"]),
        render_table("No Trade Reasons", tables["no_trade_reasons"]),
        render_table("Per Symbol State", tables["symbol_state_summary"]),
        render_table("Candidate Lifetime", tables["candidate_lifetime"]),
        render_table("Trigger Loss Report", tables["trigger_loss_report"]),
        render_table("Trigger Stage Distribution", tables["trigger_stage_distribution"]),
        render_table("Missing Condition Counts", tables["missing_condition_counts"]),
        render_table("Near-Miss Candidates", tables["near_miss_candidates"]),
        render_table("Average Waiting Time by Required Event", tables["waiting_time_by_required_event"]),
        render_table("Trigger Replay Report", tables["trigger_replay_report"]),
        render_table("Telegram Deliveries", tables["telegram_deliveries"]),
        render_table("Run Completeness", tables["run_completeness"]),
        render_table("State-Machine Invalidations", tables["state_machine_invalidations"]),
        render_table("Late Entry Anomalies", tables["late_entry_report"]),
        render_table("Late Entry Unique Incidents", tables["late_entry_incidents"]),
    ]
    doc = f"""<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <title>Scan History Report</title>
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; margin: 24px; color: #17202a; }}
    .cards {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 12px; margin: 18px 0 28px; }}
    .card {{ border: 1px solid #d6dde5; border-radius: 8px; padding: 12px; background: #f8fafc; }}
    .card span {{ display: block; font-size: 12px; color: #5d6d7e; }}
    .card strong {{ font-size: 22px; }}
    table {{ border-collapse: collapse; width: 100%; font-size: 13px; }}
    th, td {{ border: 1px solid #d6dde5; padding: 6px 8px; vertical-align: top; }}
    th {{ background: #edf2f7; text-align: left; position: sticky; top: 0; }}
    section {{ margin: 28px 0; overflow-x: auto; }}
    code {{ background: #edf2f7; padding: 1px 4px; border-radius: 4px; }}
  </style>
</head>
<body>
  <h1>Scan History Report</h1>
  <p>Window: <code>{html.escape(str(summary.get('first_timestamp')))}</code> to <code>{html.escape(str(summary.get('last_timestamp')))}</code></p>
  <div class="cards">{card_html}</div>
  {''.join(sections)}
</body>
</html>
"""
    path.write_text(doc, encoding="utf-8")


def analyze(files: list[Path], symbol: Optional[str] = None) -> tuple[dict[str, Any], dict[str, list[dict[str, Any]]]]:
    records, issues = load_records(files, symbol=symbol)
    summary = build_global_summary(records, files, issues)
    late_entry_rows = build_late_entry_report(records)
    trigger_loss_rows = build_trigger_loss_report(records)
    trigger_replay_rows = build_trigger_replay_report(records)
    tables = {
        "filter_funnel": build_filter_funnel(records),
        "no_trade_reasons": build_no_trade_reasons(records),
        "symbol_timeline": compress_symbol_timelines(records),
        "symbol_state_summary": build_symbol_state_summary(records),
        "htf_metrics_timeline": build_htf_metrics_timeline(records),
        "candidate_lifetime": build_candidate_lifetime(records),
        "trigger_loss_report": trigger_loss_rows,
        "trigger_stage_distribution": build_trigger_stage_distribution(trigger_loss_rows),
        "missing_condition_counts": build_missing_condition_counts(trigger_loss_rows),
        "near_miss_candidates": build_near_miss_candidates(trigger_loss_rows),
        "waiting_time_by_required_event": build_waiting_time_by_required_event(trigger_loss_rows),
        "trigger_replay_report": trigger_replay_rows,
        "telegram_deliveries": build_telegram_delivery_report(records),
        "run_completeness": build_run_completeness(records),
        "state_machine_invalidations": build_state_machine_invalidations(records),
        "late_entry_report": late_entry_rows,
        "late_entry_incidents": build_late_entry_incidents(late_entry_rows),
    }
    summary["neutral_htf_warnings"] = [row["symbol"] for row in tables["symbol_state_summary"] if row.get("neutral_htf_warning")]
    summary["filter_funnel"] = tables["filter_funnel"]
    summary["state_machine_diagnostics"] = build_state_machine_stats(records, tables["state_machine_invalidations"])
    summary["late_entry_snapshot_count"] = len(tables["late_entry_report"])
    summary["late_entry_unique_incidents"] = len([row for row in tables["late_entry_incidents"] if row.get("candidate_id")])
    summary["late_entry_missing_candidate_id_count"] = sum(row["snapshot_count"] for row in tables["late_entry_incidents"] if not row.get("candidate_id"))
    summary["trigger_loss_candidate_count"] = len(trigger_loss_rows)
    summary["trigger_loss_reason_counts"] = dict(Counter(row.get("why_stopped") or UNKNOWN for row in trigger_loss_rows).most_common())
    summary["trigger_loss_required_next_event_counts"] = dict(Counter(row.get("required_next_event") or UNKNOWN for row in trigger_loss_rows).most_common())
    summary["trigger_missing_condition_counts"] = dict(Counter(
        condition
        for row in trigger_loss_rows
        for condition in str(row.get("missing_conditions") or "").split(",")
        if condition
    ).most_common())
    summary["trigger_loss_disappeared_count"] = sum(1 for row in trigger_loss_rows if row.get("candidate_disappeared_before_window_end") is True)
    summary["trigger_seen_candidate_count"] = sum(1 for row in trigger_loss_rows if row.get("trigger_seen_ever") is True)
    summary["trigger_replay"] = build_trigger_replay_summary(trigger_replay_rows)
    summary["a_plus_false_but_trade_delivery_true"] = sum(
        1 for row in tables["telegram_deliveries"]
        if row.get("message_kind") == "trade_alert" and row.get("delivery_status") == "sent" and row.get("a_plus_delivery_allowed") == "false"
    )
    return summary, tables


def main() -> int:
    parser = argparse.ArgumentParser(description="Build research analytics for scanner JSONL logs.")
    parser.add_argument("inputs", nargs="+", help="One or more JSONL files, directories, or glob patterns.")
    parser.add_argument("--symbol", help="Limit analysis to one symbol.")
    parser.add_argument("--output-dir", default="reports/scan_history", help="Directory for generated reports.")
    parser.add_argument("--format", default="json,csv,html", help="Comma-separated output formats: json,csv,html.")
    args = parser.parse_args()

    files = resolve_input_files(args.inputs)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    formats = {item.strip().lower() for item in args.format.split(",") if item.strip()}

    summary, tables = analyze(files, symbol=args.symbol)
    if "json" in formats:
        write_json(output_dir / "summary.json", summary)
    if "csv" in formats:
        write_csv(output_dir / "filter_funnel.csv", tables["filter_funnel"])
        write_csv(output_dir / "no_trade_reasons.csv", tables["no_trade_reasons"])
        write_csv(output_dir / "symbol_state_summary.csv", tables["symbol_state_summary"])
        write_csv(output_dir / "symbol_timeline.csv", tables["symbol_timeline"])
        write_csv(output_dir / "htf_metrics_timeline.csv", tables["htf_metrics_timeline"])
        write_csv(output_dir / "candidate_lifetime.csv", tables["candidate_lifetime"])
        write_csv(output_dir / "trigger_loss_report.csv", tables["trigger_loss_report"])
        write_csv(output_dir / "trigger_stage_distribution.csv", tables["trigger_stage_distribution"])
        write_csv(output_dir / "missing_condition_counts.csv", tables["missing_condition_counts"])
        write_csv(output_dir / "near_miss_candidates.csv", tables["near_miss_candidates"])
        write_csv(output_dir / "waiting_time_by_required_event.csv", tables["waiting_time_by_required_event"])
        write_csv(output_dir / "trigger_replay_report.csv", tables["trigger_replay_report"])
        write_csv(output_dir / "telegram_deliveries.csv", tables["telegram_deliveries"])
        write_csv(output_dir / "run_completeness.csv", tables["run_completeness"])
        write_csv(output_dir / "state_machine_invalidations.csv", tables["state_machine_invalidations"])
        write_csv(output_dir / "late_entry_report.csv", tables["late_entry_report"])
        write_csv(output_dir / "late_entry_incidents.csv", tables["late_entry_incidents"])
    if "html" in formats:
        write_html(output_dir / "report.html", summary, tables)

    print(json.dumps({"output_dir": str(output_dir), "files": len(files), "symbol_scan_records": summary["symbol_scan_records"], "malformed_line_count": summary["malformed_line_count"]}, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
