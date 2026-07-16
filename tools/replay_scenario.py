#!/usr/bin/env python3
"""Offline scenario replay validator.

Feeds closed 15m candles one-by-one into the existing snapshot analyzer and
stores candidate progression without sending Telegram alerts.
"""

import argparse
import json
from pathlib import Path
from typing import Dict, Iterable, List, Optional

import pandas as pd

import analyzer
from core.journal import _json_safe
from services.market_data import fetch_candles


TIMEFRAME_FREQ = {
    "15m": "15min",
    "1h": "1h",
    "4h": "4h",
}


def load_candles(symbol: str, timeframe: str, data_dir: Optional[Path] = None, limit: int = 1000) -> pd.DataFrame:
    if data_dir:
        path = data_dir / f"{symbol}_{timeframe}.csv"
        if not path.exists():
            raise FileNotFoundError(f"Missing replay candles: {path}")
        df = pd.read_csv(path)
        timestamp_col = "timestamp" if "timestamp" in df.columns else df.columns[0]
        df[timestamp_col] = pd.to_datetime(df[timestamp_col])
        df = df.set_index(timestamp_col)
    else:
        df = fetch_candles(symbol, timeframe, limit=limit)
        if df is None or df.empty:
            raise RuntimeError(f"No candles for {symbol} {timeframe}")

    numeric_cols = ["open", "high", "low", "close", "volume"]
    for col in numeric_cols:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    return df.sort_index()[numeric_cols].dropna()


def filter_window(df: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
    return df[(df.index >= start) & (df.index <= end)].copy()


def resample_ohlcv(df: pd.DataFrame, timeframe: str) -> pd.DataFrame:
    freq = TIMEFRAME_FREQ[timeframe]
    return (
        df.resample(freq, label="left", closed="left")
        .agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"})
        .dropna()
    )


def _selected_candidate_snapshot(analysis_data: dict) -> dict:
    scenario_scan = analysis_data.get("scenario_scan")
    snapshot = analyzer._scenario_scan_snapshot(scenario_scan) or {}
    return snapshot.get("selected_scenario") or {}


def _step_snapshot(symbol: str, candle_time, score_result: dict, analysis_data: dict) -> dict:
    selected = _selected_candidate_snapshot(analysis_data)
    diagnostics = score_result.get("diagnostics", {})
    risk_plan = analysis_data.get("risk_plan")
    return {
        "symbol": symbol,
        "candle_time": str(candle_time),
        "score": score_result.get("total_score"),
        "decision": score_result.get("final_decision") or score_result.get("decision"),
        "scenario_status": score_result.get("scenario_status"),
        "execution_status": score_result.get("execution_status"),
        "candidate_id": selected.get("candidate_id"),
        "candidate_status": selected.get("status"),
        "early_trigger_index": (selected.get("trigger_scan") or {}).get("early_trigger_index"),
        "trigger_confirmed": diagnostics.get("trigger_confirmed"),
        "risk_plan_status": risk_plan.get("risk_plan_status") if risk_plan else None,
        "a_plus_eligible": diagnostics.get("a_plus_delivery_allowed", False),
    }


def replay_symbol(
    symbol: str,
    start: pd.Timestamp,
    end: pd.Timestamp,
    *,
    data_dir: Optional[Path] = None,
    macro_context: Optional[dict] = None,
) -> dict:
    full_15m = load_candles(symbol, "15m", data_dir=data_dir)
    replay_15m = filter_window(full_15m, start, end)
    if replay_15m.empty:
        raise RuntimeError(f"No 15m replay candles for {symbol} in {start}..{end}")

    run_id = f"offline-replay-{symbol}-{start.date()}-{end.date()}"
    analyzer._SCENARIO_TRANSITION_STATE.clear()
    steps: List[dict] = []
    transitions: List[dict] = []

    for candle_time in replay_15m.index:
        prefix_15m = full_15m[full_15m.index <= candle_time].copy()
        prefix_1h = resample_ohlcv(prefix_15m, "1h")
        prefix_4h = resample_ohlcv(prefix_15m, "4h")
        score_result, analysis_data = analyzer.analyze_symbol_snapshot(
            symbol,
            prefix_4h,
            prefix_1h,
            prefix_15m,
            macro_context or {},
        )
        steps.append(_step_snapshot(symbol, candle_time, score_result or {}, analysis_data or {}))
        transitions.extend(
            analyzer._build_scenario_transition_records(
                run_id,
                pd.Timestamp(candle_time).isoformat(),
                symbol,
                (analysis_data or {}).get("scenario_scan"),
                detected_at=pd.Timestamp(candle_time).isoformat(),
            )
        )

    analyzer._SCENARIO_TRANSITION_STATE.clear()
    return {
        **analyzer._build_metadata(),
        "record_type": "offline_replay",
        "run_id": run_id,
        "symbol": symbol,
        "from": start.isoformat(),
        "to": end.isoformat(),
        "future_candles_used": False,
        "telegram_sent": False,
        "steps": steps,
        "transitions": transitions,
        "final_step": steps[-1] if steps else None,
    }


def replay_universe(
    symbols: Iterable[str],
    start: pd.Timestamp,
    end: pd.Timestamp,
    *,
    data_dir: Optional[Path] = None,
) -> dict:
    symbols = list(symbols)
    results = []
    errors = []
    for symbol in symbols:
        try:
            results.append(replay_symbol(symbol, start, end, data_dir=data_dir))
        except Exception as exc:
            errors.append(analyzer._analysis_error(symbol, "offline_replay", exc, "offline-replay"))
    return {
        **analyzer._build_metadata(),
        "record_type": "offline_replay_universe",
        "from": start.isoformat(),
        "to": end.isoformat(),
        "symbols_total": len(symbols),
        "symbols_success": len(results),
        "symbols_failed": len(errors),
        "results": results,
        "errors": errors,
    }


def diff_replay_results(left: dict, right: dict) -> dict:
    left_steps = {item.get("candle_time"): item for item in left.get("steps", [])}
    right_steps = {item.get("candle_time"): item for item in right.get("steps", [])}
    changed = []
    for candle_time in sorted(set(left_steps) | set(right_steps)):
        if left_steps.get(candle_time) != right_steps.get(candle_time):
            changed.append({
                "candle_time": candle_time,
                "left": left_steps.get(candle_time),
                "right": right_steps.get(candle_time),
            })
    return {"changed_steps": changed, "matches": not changed}


def _write_output(payload: dict, output: Optional[Path]) -> None:
    text = json.dumps(_json_safe(payload), ensure_ascii=False, indent=2, sort_keys=True)
    if output:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(text + "\n", encoding="utf-8")
    else:
        print(text)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Replay scenario scanner one closed candle at a time.")
    scope = parser.add_mutually_exclusive_group(required=True)
    scope.add_argument("--symbol", help="Single symbol, e.g. SOL")
    scope.add_argument("--universe", action="store_true", help="Replay analyzer.COINS_LIST")
    parser.add_argument("--from", dest="start", required=True, help="Inclusive start timestamp/date")
    parser.add_argument("--to", dest="end", required=True, help="Inclusive end timestamp/date")
    parser.add_argument("--data-dir", type=Path, help="Directory with SYMBOL_15m.csv files")
    parser.add_argument("--output", type=Path, help="Where to write replay JSON")
    parser.add_argument("--diff", type=Path, help="Compare output with a previous replay JSON")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    start = pd.Timestamp(args.start)
    end = pd.Timestamp(args.end)
    if args.universe:
        payload = replay_universe(analyzer.COINS_LIST, start, end, data_dir=args.data_dir)
    else:
        payload = replay_symbol(args.symbol, start, end, data_dir=args.data_dir)
    if args.diff:
        previous = json.loads(args.diff.read_text(encoding="utf-8"))
        payload["diff"] = diff_replay_results(previous, payload)
    _write_output(payload, args.output)


if __name__ == "__main__":
    main()
