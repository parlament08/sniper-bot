"""Deterministic comparison helpers for scan execution paths.

This module is diagnostic-only. It does not change trading thresholds or the
runtime analyzer flow; it copies inputs before calling the analyzer so the
comparison itself cannot mutate caller-owned candle frames.
"""

from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import asdict, is_dataclass
from typing import Any, Callable, Mapping, Optional

import pandas as pd


OHLCV_COLUMNS = ("open", "high", "low", "close", "volume")
TIMEFRAME_MINUTES = {
    "15m": 15,
    "1h": 60,
    "4h": 240,
}

STAGE_PATHS = {
    "HTF structure": ("analysis", "market_structure"),
    "swing points": ("analysis", "htf_context", "swing_points"),
    "BOS": ("analysis", "trigger_diagnostics", "components", "BOS"),
    "CHoCH": ("analysis", "trigger_diagnostics", "components", "CHoCH"),
    "SFP": ("analysis", "sfp_data"),
    "liquidity map": ("analysis", "liquidity_map"),
    "premium/discount": ("analysis", "premium_discount_data"),
    "POI": ("analysis", "active_fvg"),
    "early trigger": ("analysis", "trigger_scan", "early_trigger"),
    "confirmed trigger": ("analysis", "trigger_scan", "confirmed_trigger"),
    "FVG candidates": ("analysis", "fvg_candidates"),
    "selected FVG": ("analysis", "active_fvg"),
    "scenario candidates": ("score", "diagnostics", "scenario_scan", "top_candidates"),
    "selected scenario": ("score", "diagnostics", "scenario_scan", "selected_scenario"),
    "risk plan": ("analysis", "risk_plan"),
    "score": ("score", "total_score"),
    "decision": ("score", "decision"),
    "A+ gates": ("score", "diagnostics", "a_plus_delivery_gate"),
}

FINAL_PATHS = {
    "score": ("score", "total_score"),
    "decision": ("score", "decision"),
    "final_decision": ("score", "final_decision"),
    "scenario_status": ("score", "scenario_status"),
    "execution_status": ("score", "execution_status"),
    "selected_candidate": ("score", "diagnostics", "scenario_scan", "selected_scenario", "candidate_id"),
    "risk_reason": ("analysis", "risk_plan", "reason"),
}


def compare_scan_paths(
    symbol: str,
    candles_by_timeframe: Mapping[str, pd.DataFrame],
    analysis_time: Any,
    runtime_state: Optional[Mapping[str, Any]] = None,
    *,
    other_candles_by_timeframe: Optional[Mapping[str, pd.DataFrame]] = None,
    macro_context: Optional[Mapping[str, Any]] = None,
    apply_closed_candle_filter: bool = False,
    path_a_name: str = "live",
    path_b_name: str = "replay",
    runner: Optional[Callable[[str, Mapping[str, pd.DataFrame], Mapping[str, Any]], tuple[Any, Any]]] = None,
) -> dict[str, Any]:
    """Compare two scan paths over normalized candle inputs and stage outputs."""

    left_inputs = _prepare_candles(candles_by_timeframe, analysis_time, apply_closed_candle_filter)
    right_inputs = _prepare_candles(
        other_candles_by_timeframe or candles_by_timeframe,
        analysis_time,
        apply_closed_candle_filter,
    )
    left_hashes = candle_hashes(left_inputs)
    right_hashes = candle_hashes(right_inputs)
    input_diffs = _input_diffs(left_inputs, right_inputs)
    inputs_equal = left_hashes == right_hashes

    left_output = _run_path(symbol, left_inputs, macro_context or {}, runtime_state, runner)
    right_output = _run_path(symbol, right_inputs, macro_context or {}, runtime_state, runner)
    left_stages = stage_hashes(left_output)
    right_stages = stage_hashes(right_output)
    stage_diffs = {
        stage: {
            path_a_name: left_stages.get(stage),
            path_b_name: right_stages.get(stage),
            "left_value": _stage_value(left_output, stage),
            "right_value": _stage_value(right_output, stage),
        }
        for stage in STAGE_PATHS
        if left_stages.get(stage) != right_stages.get(stage)
    }
    final_left = _final_snapshot(left_output)
    final_right = _final_snapshot(right_output)
    final_equal = _stable_hash(final_left) == _stable_hash(final_right)
    return {
        "inputs_equal": inputs_equal,
        "input_hashes": {
            path_a_name: left_hashes,
            path_b_name: right_hashes,
        },
        "input_diffs": input_diffs,
        "stage_hashes": {
            path_a_name: left_stages,
            path_b_name: right_stages,
        },
        "first_divergent_stage": next(iter(stage_diffs), None),
        "stage_diffs": stage_diffs,
        "final_equal": final_equal,
        "final": {
            path_a_name: final_left,
            path_b_name: final_right,
        },
    }


def candle_hashes(candles_by_timeframe: Mapping[str, pd.DataFrame]) -> dict[str, str]:
    return {
        timeframe: _hash_candles(df)
        for timeframe, df in sorted(candles_by_timeframe.items())
    }


def stage_hashes(output: Mapping[str, Any]) -> dict[str, str]:
    return {stage: _stable_hash(_stage_value(output, stage)) for stage in STAGE_PATHS}


def stable_candidate_order(candidates: list[Any]) -> list[Any]:
    return sorted(candidates, key=lambda item: _candidate_tie_key(item))


def _run_path(symbol, candles_by_timeframe, macro_context, runtime_state, runner):
    if runner is not None:
        score, analysis = runner(symbol, candles_by_timeframe, macro_context)
        return {"score": score, "analysis": analysis}

    import analyzer

    previous_runtime = copy.deepcopy(getattr(analyzer, "_SCENARIO_RUNTIME_STATE", {}))
    previous_transition = copy.deepcopy(getattr(analyzer, "_SCENARIO_TRANSITION_STATE", {}))
    try:
        analyzer._SCENARIO_RUNTIME_STATE.clear()
        analyzer._SCENARIO_TRANSITION_STATE.clear()
        if runtime_state:
            analyzer._SCENARIO_RUNTIME_STATE.update(copy.deepcopy(runtime_state.get("scenario_runtime", {})))
            analyzer._SCENARIO_TRANSITION_STATE.update(copy.deepcopy(runtime_state.get("scenario_transitions", {})))
        score, analysis = analyzer.analyze_symbol_snapshot(
            symbol,
            candles_by_timeframe.get("4h").copy(deep=True),
            candles_by_timeframe.get("1h").copy(deep=True),
            candles_by_timeframe.get("15m").copy(deep=True),
            dict(macro_context or {}),
        )
        return {"score": score or {}, "analysis": analysis or {}}
    finally:
        analyzer._SCENARIO_RUNTIME_STATE.clear()
        analyzer._SCENARIO_RUNTIME_STATE.update(previous_runtime)
        analyzer._SCENARIO_TRANSITION_STATE.clear()
        analyzer._SCENARIO_TRANSITION_STATE.update(previous_transition)


def _prepare_candles(candles_by_timeframe, analysis_time, apply_closed_candle_filter):
    prepared = {}
    for timeframe, df in candles_by_timeframe.items():
        normalized = _normalize_candle_frame(df)
        if apply_closed_candle_filter:
            normalized = _closed_candles(normalized, timeframe, analysis_time)
        prepared[timeframe] = normalized
    return prepared


def _normalize_candle_frame(df: pd.DataFrame) -> pd.DataFrame:
    normalized = df.copy(deep=True)
    if "timestamp" in normalized.columns:
        normalized["timestamp"] = pd.to_datetime(normalized["timestamp"])
        normalized = normalized.set_index("timestamp")
    normalized.index = pd.to_datetime(normalized.index)
    normalized = normalized.sort_index()
    for column in OHLCV_COLUMNS:
        normalized[column] = pd.to_numeric(normalized[column], errors="coerce")
    return normalized.loc[:, list(OHLCV_COLUMNS)].dropna()


def _closed_candles(df: pd.DataFrame, timeframe: str, analysis_time: Any) -> pd.DataFrame:
    minutes = TIMEFRAME_MINUTES.get(timeframe)
    if minutes is None:
        return df
    as_of = pd.Timestamp(analysis_time)
    if as_of.tzinfo is not None and df.index.tz is None:
        as_of = as_of.tz_convert(None)
    boundary = as_of.floor(f"{minutes}min")
    return df[df.index < boundary].copy()


def _hash_candles(df: pd.DataFrame) -> str:
    rows = []
    for index, row in _normalize_candle_frame(df).iterrows():
        rows.append("|".join([
            _normalize_value(index),
            *[_normalize_value(row[column]) for column in OHLCV_COLUMNS],
        ]))
    return hashlib.sha256("\n".join(rows).encode("utf-8")).hexdigest()


def _input_diffs(left, right):
    diffs = {}
    for timeframe in sorted(set(left) | set(right)):
        left_df = left.get(timeframe, pd.DataFrame(columns=OHLCV_COLUMNS))
        right_df = right.get(timeframe, pd.DataFrame(columns=OHLCV_COLUMNS))
        left_hash = _hash_candles(left_df)
        right_hash = _hash_candles(right_df)
        if left_hash == right_hash:
            continue
        first_diff = None
        all_indices = sorted(set(left_df.index) | set(right_df.index))
        for index in all_indices:
            left_row = _row_snapshot(left_df, index)
            right_row = _row_snapshot(right_df, index)
            if left_row != right_row:
                first_diff = {"timestamp": _normalize_value(index), "left": left_row, "right": right_row}
                break
        diffs[timeframe] = {
            "left_count": len(left_df),
            "right_count": len(right_df),
            "left_first": _index_or_none(left_df, 0),
            "right_first": _index_or_none(right_df, 0),
            "left_last": _index_or_none(left_df, -1),
            "right_last": _index_or_none(right_df, -1),
            "first_diff": first_diff,
        }
    return diffs


def _stage_value(output, stage):
    return _get_path(output, STAGE_PATHS[stage])


def _final_snapshot(output):
    return {name: _get_path(output, path) for name, path in FINAL_PATHS.items()}


def _get_path(data, path):
    current = data
    for part in path:
        current = _to_plain(current)
        if isinstance(current, Mapping):
            current = current.get(part)
        else:
            return None
    return _to_plain(current)


def _stable_hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(_to_plain(value), ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _to_plain(value):
    if is_dataclass(value):
        value = asdict(value)
    elif hasattr(value, "to_dict"):
        value = value.to_dict()
    if isinstance(value, pd.DataFrame):
        return [_row_snapshot(value, index) for index in value.index]
    if isinstance(value, pd.Series):
        return {str(key): _to_plain(val) for key, val in value.to_dict().items()}
    if isinstance(value, Mapping):
        return {str(key): _to_plain(val) for key, val in sorted(value.items(), key=lambda item: str(item[0]))}
    if isinstance(value, (list, tuple)):
        return [_to_plain(item) for item in value]
    return _normalize_scalar(value)


def _normalize_scalar(value):
    if isinstance(value, pd.Timestamp):
        return _normalize_value(value)
    if hasattr(value, "item"):
        try:
            return _normalize_scalar(value.item())
        except Exception:
            pass
    if isinstance(value, float):
        return round(value, 10)
    return value


def _normalize_value(value):
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    if hasattr(value, "isoformat") and not isinstance(value, str):
        try:
            return value.isoformat()
        except Exception:
            pass
    if isinstance(value, float):
        return format(round(value, 10), ".10g")
    if hasattr(value, "item"):
        try:
            return _normalize_value(value.item())
        except Exception:
            pass
    return str(value)


def _row_snapshot(df, index):
    if index not in df.index:
        return None
    row = df.loc[index]
    if isinstance(row, pd.DataFrame):
        row = row.iloc[0]
    return {column: _normalize_scalar(row[column]) for column in OHLCV_COLUMNS if column in row}


def _index_or_none(df, pos):
    if df.empty:
        return None
    return _normalize_value(df.index[pos])


def _candidate_tie_key(item):
    plain = _to_plain(item)
    if not isinstance(plain, Mapping):
        return ("", "", "", "")
    return (
        str(plain.get("direction") or ""),
        str(plain.get("candidate_id") or ""),
        str(plain.get("anchor_index") or ""),
        str(plain.get("last_event_index") or ""),
    )
