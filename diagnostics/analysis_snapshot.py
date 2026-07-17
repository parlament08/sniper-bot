"""Compact manual analysis snapshots built from scanner JSONL records."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Iterable, Optional


SNAPSHOT_SECTION_NAMES = (
    "market",
    "htf_context",
    "scenario",
    "state_machine",
    "trigger_debug",
    "trigger_diagnostics",
    "risk_plan",
    "decision",
    "shadow_candidate",
    "decision_trace",
    "state_machine_timeline",
    "chart_objects",
    "debug_metrics",
)


def normalize_symbol(value: Any) -> str:
    text = str(value or "").strip().upper()
    if text.endswith("USDT"):
        text = text[:-4]
    return text


def display_symbol(value: Any) -> str:
    base = normalize_symbol(value)
    return f"{base}USDT" if base else ""


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


def parse_timestamp(value: Any, *, default_tz: timezone = timezone.utc) -> Optional[datetime]:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    parsed: Optional[datetime] = None
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M"):
            try:
                parsed = datetime.strptime(text, fmt)
                break
            except ValueError:
                continue
    if parsed is None:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=default_tz)
    return parsed.astimezone(timezone.utc)


def iso_z(dt: Optional[datetime]) -> Optional[str]:
    if dt is None:
        return None
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def compact_json(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): compact_json(v) for k, v in value.items()}
    if isinstance(value, list):
        return [compact_json(item) for item in value]
    if isinstance(value, tuple):
        return [compact_json(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if hasattr(value, "isoformat"):
        try:
            return value.isoformat()
        except Exception:
            pass
    return str(value)


def _empty(value: Any) -> bool:
    return value is None or value == {} or value == []


def _selected_scenario(record: dict[str, Any]) -> Any:
    return first_present(
        record,
        (
            "features.scenario_scan.selected_scenario",
            "diagnostics.scenario_scan.selected_scenario",
            "scenario_scan.selected_scenario",
        ),
    )


def extract_snapshot_sections(record: dict[str, Any], *, timeline: Optional[list[dict[str, Any]]] = None) -> dict[str, Any]:
    features = record.get("features") if isinstance(record.get("features"), dict) else {}
    diagnostics = record.get("diagnostics") if isinstance(record.get("diagnostics"), dict) else {}
    selected = _selected_scenario(record)
    trigger_debug = first_present(record, ("features.trigger_debug", "trigger_debug", "breakdown.trigger_debug"))
    trigger_diagnostics = first_present(record, ("trigger_diagnostics", "features.trigger_diagnostics"))

    market = {
        "current_price": record.get("current_price"),
        "atr": record.get("atr"),
        "open_15m": record.get("market_open_15m"),
        "high_15m": record.get("market_high_15m"),
        "low_15m": record.get("market_low_15m"),
        "close_15m": record.get("market_close_15m"),
        "timeframes": record.get("timeframes"),
        "market_data_timestamp_15m": record.get("market_data_timestamp_15m"),
        "market_data_timestamp_1h": record.get("market_data_timestamp_1h"),
        "market_data_timestamp_4h": record.get("market_data_timestamp_4h"),
        "session": record.get("session"),
    }
    market = {key: value for key, value in market.items() if value is not None}

    scenario = first_present(record, ("features.scenario_scan", "diagnostics.scenario_scan", "scenario_scan"))
    state_machine = {
        "status": first_present(record, ("breakdown.state_machine", "diagnostics.state_machine_state", "state_machine_state")),
        "allowed": record.get("state_machine_allowed") if record.get("state_machine_allowed") is not None else diagnostics.get("state_machine_allowed"),
    }
    state_machine = {key: value for key, value in state_machine.items() if value is not None}

    decision = {
        "decision": record.get("decision"),
        "final_decision": record.get("final_decision"),
        "direction": record.get("direction"),
        "score": record.get("score"),
        "raw_score": record.get("raw_score"),
        "no_trade_reason": record.get("no_trade_reason") or diagnostics.get("no_trade_reason"),
        "scenario_status": record.get("scenario_status"),
        "execution_status": record.get("execution_status"),
        "production_a_plus_allowed": record.get("production_a_plus_allowed"),
        "delivery_gate_checks": record.get("delivery_gate_checks"),
    }
    decision = {key: value for key, value in decision.items() if value is not None}

    shadow_candidate = first_present(record, ("features.shadow_candidate", "shadow_candidate"))
    if _empty(shadow_candidate):
        shadow_candidate = {
            "shadow_candidate_id": record.get("shadow_candidate_id"),
            "shadow_created_at": record.get("shadow_created_at"),
            "shadow_direction": record.get("shadow_direction"),
            "shadow_tier": record.get("shadow_tier"),
            "shadow_rejection_reasons": record.get("shadow_rejection_reasons"),
        }
        shadow_candidate = {key: value for key, value in shadow_candidate.items() if value is not None}

    decision_trace = []
    gates = first_present(record, ("delivery_gate_checks.gates", "diagnostics.a_plus_delivery_gate.gates"), {})
    failed_gates = first_present(record, ("delivery_gate_checks.failed_gates", "diagnostics.a_plus_delivery_gate.failed_gates"), [])
    if isinstance(gates, dict):
        for name, passed in gates.items():
            decision_trace.append({"gate": name, "passed": bool(passed), "failed": name in (failed_gates or [])})

    chart_objects = {
        "liquidity_map": first_present(record, ("features.liquidity_map", "liquidity_map")),
        "premium_discount": first_present(record, ("features.premium_discount", "premium_discount")),
        "sfp": first_present(record, ("features.sfp", "sfp")),
        "context_1h": first_present(record, ("features.context_1h", "context_1h")),
        "trigger_15m": first_present(record, ("features.trigger_15m", "trigger_15m")),
        "scenario_trigger_15m": first_present(record, ("features.scenario_trigger_15m", "scenario_trigger_15m")),
        "selected_scenario": selected,
    }
    chart_objects = {key: value for key, value in chart_objects.items() if not _empty(value)}

    debug_metrics = {
        "breakdown": record.get("breakdown"),
        "diagnostics": diagnostics,
        "score_components": record.get("score_components"),
        "score_components_total": record.get("score_components_total"),
        "score_consistent": record.get("score_consistent"),
    }
    debug_metrics = {key: value for key, value in debug_metrics.items() if not _empty(value)}

    sections = {
        "market": market,
        "htf_context": first_present(record, ("htf_context", "features.htf_context")),
        "scenario": scenario,
        "state_machine": state_machine,
        "trigger_debug": trigger_debug,
        "trigger_diagnostics": trigger_diagnostics,
        "risk_plan": first_present(record, ("features.risk_plan", "risk_plan")),
        "decision": decision,
        "shadow_candidate": shadow_candidate,
        "decision_trace": decision_trace,
        "state_machine_timeline": timeline or [],
        "chart_objects": chart_objects,
        "debug_metrics": debug_metrics,
    }
    return compact_json(sections)


def compact_history_summary(record: dict[str, Any]) -> dict[str, Any]:
    scenario = first_present(record, ("features.scenario_scan", "diagnostics.scenario_scan", "scenario_scan"), {})
    selected = scenario.get("selected_scenario") if isinstance(scenario, dict) else {}
    trigger_diag = first_present(record, ("trigger_diagnostics", "features.trigger_diagnostics"), {})
    trigger_debug = first_present(record, ("features.trigger_debug", "trigger_debug"), {})
    return compact_json(
        {
            "timestamp": record.get("timestamp"),
            "htf_direction": first_present(record, ("htf_context.direction", "features.htf_context.direction", "features.market_structure_4h.trend")),
            "scenario_status": record.get("scenario_status") or (selected or {}).get("status"),
            "candidate_id": record.get("candidate_id") or (selected or {}).get("candidate_id"),
            "state_machine_state": first_present(record, ("breakdown.state_machine", "diagnostics.state_machine_state", "state_machine_state")),
            "early_trigger": first_present({"td": trigger_diag, "tb": trigger_debug}, ("td.early_trigger_detected", "tb.early_trigger_confirmed")),
            "confirmed_trigger": first_present({"td": trigger_diag, "tb": trigger_debug, "raw": record}, ("td.confirmed_trigger_detected", "tb.trigger_confirmed", "raw.diagnostics.trigger_confirmed")),
            "decision": record.get("final_decision") or record.get("decision"),
            "missing_conditions": (trigger_diag or {}).get("missing_conditions") if isinstance(trigger_diag, dict) else [],
        }
    )


def additional_scan_data(record: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "record_type",
        "run_id",
        "app_version",
        "git_commit",
        "build_time",
        "config_hash",
        "candidate_id",
        "scenario_id",
        "scan_interval",
        "htf_context_class",
        "planned_entry",
        "poi_price",
        "current_price_distance_from_poi_atr",
        "planned_entry_distance_from_poi_atr",
        "max_entry_distance_from_poi_atr",
        "minimum_stop_distance_atr",
        "minimum_stop_distance_percent",
        "entry_filled",
        "entry_filled_at",
        "expired",
        "outcome_at",
        "stop_hit",
        "target_1_hit",
        "reached_1r",
        "reached_2r",
        "max_favorable_excursion_r",
        "max_adverse_excursion_r",
    )
    return compact_json({key: record.get(key) for key in keys if record.get(key) is not None})


def build_manual_snapshot(
    record: dict[str, Any],
    *,
    source: dict[str, Any],
    requested_symbol: str,
    selected_scan_time: Optional[datetime],
    recent_history: Optional[list[dict[str, Any]]] = None,
    timeline: Optional[list[dict[str, Any]]] = None,
    exported_at: Optional[datetime] = None,
) -> dict[str, Any]:
    sections = extract_snapshot_sections(record, timeline=timeline)
    missing_sections = [name for name in SNAPSHOT_SECTION_NAMES if _empty(sections.get(name))]
    snapshot = {
        "schema_version": "manual-snapshot-1.0",
        "exported_at": iso_z(exported_at or datetime.now(timezone.utc)),
        "source": compact_json(source),
        "symbol": display_symbol(record.get("symbol") or requested_symbol),
        "scan_timestamp": iso_z(selected_scan_time) or record.get("timestamp"),
        **sections,
        "missing_sections": missing_sections,
        "additional_scan_data": additional_scan_data(record),
        "recent_history": compact_json(recent_history or []),
    }
    return compact_json(snapshot)
