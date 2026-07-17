#!/usr/bin/env python3
"""Export one compact manual analysis snapshot from scanner JSONL logs."""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Optional

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    ZoneInfo = None

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from diagnostics.analysis_snapshot import (  # noqa: E402
    build_manual_snapshot,
    compact_history_summary,
    display_symbol,
    iso_z,
    normalize_symbol,
    parse_timestamp,
)


DEFAULT_OUTPUT_DIR = Path("runtime_data/manual_snapshots")
LOCAL_TZ = ZoneInfo("Europe/Chisinau") if ZoneInfo is not None else timezone.utc


@dataclass
class ScanRecord:
    raw: dict[str, Any]
    timestamp: Optional[datetime]
    file: Path
    line_number: int


@dataclass
class LoadResult:
    records: list[ScanRecord]
    skipped_invalid_lines: int
    skipped_missing_timestamps: int


def discover_input_file(scan_date: date, *, root: Path = REPO_ROOT) -> Path:
    dashed = scan_date.strftime("%Y-%m-%d")
    compact = scan_date.strftime("%Y%m%d")
    candidates = [
        root / "data" / "journal" / f"scans_{dashed}.jsonl",
        root / "data" / "journal" / f"scans_{compact}.jsonl",
        root / f"scans_{dashed}.jsonl",
        root / f"scans_{compact}.jsonl",
        root / "runtime_data" / f"scans_{dashed}.jsonl",
        root / "runtime_data" / f"scans_{compact}.jsonl",
        root / "logs" / f"scans_{dashed}.jsonl",
        root / "logs" / f"scans_{compact}.jsonl",
    ]
    for path in candidates:
        if path.is_file():
            return path
    checked = ", ".join(str(path.relative_to(root)) for path in candidates)
    raise FileNotFoundError(f"No scan file found for {dashed}. Checked: {checked}")


def parse_date(value: Optional[str]) -> date:
    if not value:
        return datetime.now(LOCAL_TZ).date()
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"Invalid --date value: {value}") from exc


def parse_requested_time(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    parsed = parse_timestamp(value, default_tz=LOCAL_TZ)
    if parsed is None:
        raise ValueError(f"Invalid --at value: {value}")
    return parsed


def read_symbol_records(path: Path, symbol: str) -> LoadResult:
    records: list[ScanRecord] = []
    skipped_invalid = 0
    skipped_missing_timestamps = 0
    wanted = normalize_symbol(symbol)
    with path.open("r", encoding="utf-8") as fh:
        for line_number, line in enumerate(fh, start=1):
            if not line.strip():
                continue
            try:
                raw = json.loads(line)
            except json.JSONDecodeError:
                skipped_invalid += 1
                continue
            if not isinstance(raw, dict):
                skipped_invalid += 1
                continue
            if raw.get("record_type", "symbol_scan") != "symbol_scan":
                continue
            if normalize_symbol(raw.get("symbol")) != wanted:
                continue
            timestamp = parse_timestamp(raw.get("timestamp"), default_tz=LOCAL_TZ)
            if timestamp is None:
                skipped_missing_timestamps += 1
                continue
            records.append(ScanRecord(raw=raw, timestamp=timestamp, file=path, line_number=line_number))
    records.sort(key=lambda item: (item.timestamp or datetime.min.replace(tzinfo=timezone.utc), item.line_number))
    return LoadResult(records=records, skipped_invalid_lines=skipped_invalid, skipped_missing_timestamps=skipped_missing_timestamps)


def read_timeline_records(path: Path, symbol: str, candidate_id: Optional[str]) -> list[dict[str, Any]]:
    if not candidate_id:
        return []
    wanted = normalize_symbol(symbol)
    timeline: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as fh:
        for line_number, line in enumerate(fh, start=1):
            if not line.strip():
                continue
            try:
                raw = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(raw, dict) or raw.get("record_type") != "scenario_transition":
                continue
            if normalize_symbol(raw.get("symbol")) != wanted or str(raw.get("candidate_id") or "") != str(candidate_id):
                continue
            timeline.append(
                {
                    "timestamp": raw.get("timestamp"),
                    "line_number": line_number,
                    "candidate_id": raw.get("candidate_id"),
                    "from_state": raw.get("from_state"),
                    "to_state": raw.get("to_state"),
                    "event_type": raw.get("event_type"),
                    "event_time": raw.get("event_time"),
                    "reason": raw.get("reason"),
                }
            )
    return timeline


def select_record(records: list[ScanRecord], requested_time: Optional[datetime]) -> tuple[ScanRecord, Optional[int]]:
    if not records:
        raise LookupError("No matching records")
    if requested_time is None:
        return records[-1], None
    selected = min(records, key=lambda item: abs((item.timestamp - requested_time).total_seconds()) if item.timestamp else float("inf"))
    return selected, int(abs((selected.timestamp - requested_time).total_seconds()))


def output_filename(symbol: str, timestamp: datetime) -> str:
    symbol_part = re.sub(r"[^A-Z0-9_-]+", "_", display_symbol(symbol))
    time_part = timestamp.astimezone(timezone.utc).strftime("%Y-%m-%dT%H%M%SZ")
    return f"{symbol_part}_{time_part}.json"


def build_recent_history(records: list[ScanRecord], selected: ScanRecord, count: int) -> list[dict[str, Any]]:
    if count <= 0:
        return []
    before = [item for item in records if (item.timestamp, item.line_number) < (selected.timestamp, selected.line_number)]
    return [compact_history_summary(item.raw) for item in before[-count:]]


def candidate_id_from_record(record: dict[str, Any]) -> Optional[str]:
    if record.get("candidate_id"):
        return str(record.get("candidate_id"))
    scenario = (((record.get("features") or {}).get("scenario_scan") or {}).get("selected_scenario") or {})
    if isinstance(scenario, dict) and scenario.get("candidate_id"):
        return str(scenario.get("candidate_id"))
    trigger_diag = record.get("trigger_diagnostics")
    if isinstance(trigger_diag, dict) and trigger_diag.get("candidate_id"):
        return str(trigger_diag.get("candidate_id"))
    return None


def create_snapshot(args: argparse.Namespace) -> tuple[Path, dict[str, Any], LoadResult, Optional[int], Optional[datetime]]:
    scan_date = parse_date(args.date)
    input_path = Path(args.input).expanduser() if args.input else discover_input_file(scan_date)
    if not input_path.is_file():
        raise FileNotFoundError(f"Scan file does not exist: {input_path}")

    requested_time = parse_requested_time(args.at)
    load_result = read_symbol_records(input_path, args.symbol)
    if not load_result.records:
        raise LookupError(f"No scan records found for {display_symbol(args.symbol)}")

    selected, diff_seconds = select_record(load_result.records, requested_time)
    if diff_seconds is not None and args.max_time_diff_minutes is not None:
        max_seconds = float(args.max_time_diff_minutes) * 60.0
        if diff_seconds > max_seconds:
            raise TimeoutError(
                f"Closest scan is {diff_seconds} seconds away, exceeding "
                f"--max-time-diff-minutes {args.max_time_diff_minutes}"
            )

    history = build_recent_history(load_result.records, selected, args.history)
    timeline = read_timeline_records(input_path, args.symbol, candidate_id_from_record(selected.raw))
    source = {
        "file": str(input_path),
        "line_number": selected.line_number,
        "requested_symbol": display_symbol(args.symbol),
        "requested_time": iso_z(requested_time),
        "selected_scan_time": iso_z(selected.timestamp),
        "time_difference_seconds": diff_seconds,
        "skipped_invalid_lines": load_result.skipped_invalid_lines,
        "skipped_missing_timestamps": load_result.skipped_missing_timestamps,
    }
    snapshot = build_manual_snapshot(
        selected.raw,
        source=source,
        requested_symbol=args.symbol,
        selected_scan_time=selected.timestamp,
        recent_history=history,
        timeline=timeline,
    )

    output_dir = Path(args.output_dir).expanduser()
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / output_filename(args.symbol, selected.timestamp)
    with output_path.open("w", encoding="utf-8") as fh:
        json.dump(snapshot, fh, ensure_ascii=False, indent=2, sort_keys=True)
        fh.write("\n")
    return output_path, snapshot, load_result, diff_seconds, requested_time


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Export one manual analysis snapshot from scanner JSONL logs.")
    parser.add_argument("symbol", help="Symbol to export, e.g. INJ or INJUSDT")
    parser.add_argument("--input", help="Explicit scans_*.jsonl input file")
    parser.add_argument("--date", help="Scan date to discover, YYYY-MM-DD")
    parser.add_argument("--at", help="Select scan closest to this time, e.g. '2026-07-17 19:15' or ISO 8601")
    parser.add_argument("--max-time-diff-minutes", type=float, help="Reject closest scan if farther than this many minutes")
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR), help="Output directory")
    parser.add_argument("--history", type=int, default=0, help="Include up to N previous compact state summaries")
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        output_path, _snapshot, load_result, diff_seconds, requested_time = create_snapshot(args)
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        return 1

    print("Snapshot created:")
    print(output_path)
    if load_result.skipped_invalid_lines:
        print(f"Skipped malformed JSONL lines: {load_result.skipped_invalid_lines}")
    if load_result.skipped_missing_timestamps:
        print(f"Skipped records without timestamps: {load_result.skipped_missing_timestamps}")
    if requested_time is not None:
        selected_time = _snapshot.get("scan_timestamp")
        print(f"Requested time: {iso_z(requested_time)}")
        print(f"Selected scan:  {selected_time}")
        print(f"Difference:     {diff_seconds} seconds")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
