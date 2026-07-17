#!/usr/bin/env python3
"""Analyze research-only shadow candidates from scanner JSONL history."""

from __future__ import annotations

import argparse
import csv
import glob
import html
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional


TIER_ORDER = {"C": 1, "B": 2, "A": 3, "A+": 4}
EXPECTED_B_PER_DAY = (5, 20)
EXPECTED_A_PER_DAY = (1, 5)
EXPECTED_A_PLUS_PER_WEEK = (1, 3)


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


def as_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def nested_get(data: dict[str, Any], path: str, default: Any = None) -> Any:
    current: Any = data
    for part in path.split("."):
        if isinstance(current, dict) and part in current:
            current = current[part]
        else:
            return default
    return current


def resolve_input_files(patterns: Iterable[str]) -> list[Path]:
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


def load_symbol_scans(files: list[Path], symbol: Optional[str] = None) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for path in files:
        with path.open("r", encoding="utf-8") as fh:
            for line_no, line in enumerate(fh, start=1):
                if not line.strip():
                    continue
                try:
                    raw = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if raw.get("record_type") not in (None, "symbol_scan"):
                    continue
                if symbol and str(raw.get("symbol", "")).upper() != symbol.upper():
                    continue
                raw["_source_file"] = str(path)
                raw["_source_line"] = line_no
                raw["_timestamp"] = parse_timestamp(raw.get("timestamp"))
                records.append(raw)
    records.sort(key=lambda item: (item.get("_timestamp") or datetime.min.replace(tzinfo=timezone.utc), item.get("_source_file"), item.get("_source_line")))
    return records


def candidate_payload(row: dict[str, Any]) -> dict[str, Any]:
    payload = nested_get(row, "features.shadow_candidate", {}) or {}
    return payload if isinstance(payload, dict) else {}


def candidate_id(row: dict[str, Any]) -> Optional[str]:
    value = row.get("shadow_candidate_id") or candidate_payload(row).get("shadow_candidate_id")
    return str(value) if value else None


def candidate_tier(row: dict[str, Any]) -> Optional[str]:
    value = row.get("shadow_tier") or candidate_payload(row).get("shadow_tier")
    return str(value) if value else None


def candidate_direction(row: dict[str, Any]) -> Optional[str]:
    value = row.get("shadow_direction") or candidate_payload(row).get("shadow_direction")
    return str(value) if value else None


def candidate_price(row: dict[str, Any], key: str) -> Optional[float]:
    return as_float(candidate_payload(row).get(key))


def candle_high(row: dict[str, Any]) -> Optional[float]:
    return as_float(row.get("market_high_15m") or row.get("current_price") or row.get("market_close_15m"))


def candle_low(row: dict[str, Any]) -> Optional[float]:
    return as_float(row.get("market_low_15m") or row.get("current_price") or row.get("market_close_15m"))


def hit_price(direction: str, high: Optional[float], low: Optional[float], price: Optional[float]) -> bool:
    if high is None or low is None or price is None:
        return False
    return low <= price <= high


def excursion_r(direction: str, high: Optional[float], low: Optional[float], entry: float, risk: float) -> tuple[float, float]:
    if high is None or low is None or risk <= 0:
        return 0.0, 0.0
    if direction == "LONG":
        mfe = max(0.0, (high - entry) / risk)
        mae = max(0.0, (entry - low) / risk)
    else:
        mfe = max(0.0, (entry - low) / risk)
        mae = max(0.0, (high - entry) / risk)
    return mfe, mae


def compute_outcome(first: dict[str, Any], future_rows: list[dict[str, Any]], max_bars: int = 96) -> dict[str, Any]:
    direction = candidate_direction(first)
    entry = candidate_price(first, "entry")
    stop = candidate_price(first, "stop_loss")
    target = candidate_price(first, "target_1")
    if direction not in {"LONG", "SHORT"} or entry is None or stop is None:
        return empty_outcome()
    risk = abs(entry - stop)
    if risk <= 0:
        return empty_outcome()

    entry_filled = False
    entry_filled_at = None
    max_mfe = 0.0
    max_mae = 0.0
    reached_1r = False
    reached_2r = False
    target_1_hit = False
    stop_hit = False
    outcome_at = None
    checked = 0

    for row in future_rows:
        if checked >= max_bars:
            break
        checked += 1
        high = candle_high(row)
        low = candle_low(row)
        ts = row.get("timestamp")
        if not entry_filled:
            if hit_price(direction, high, low, entry):
                entry_filled = True
                entry_filled_at = ts
            else:
                continue
        mfe, mae = excursion_r(direction, high, low, entry, risk)
        max_mfe = max(max_mfe, mfe)
        max_mae = max(max_mae, mae)
        reached_1r = reached_1r or max_mfe >= 1.0
        reached_2r = reached_2r or max_mfe >= 2.0
        target_1_hit = target_1_hit or hit_price(direction, high, low, target)
        stop_hit = stop_hit or hit_price(direction, high, low, stop)
        if target_1_hit or stop_hit:
            outcome_at = ts
            break

    return {
        "entry_filled": entry_filled,
        "entry_filled_at": entry_filled_at,
        "max_favorable_excursion_r": round(max_mfe, 4) if entry_filled else None,
        "max_adverse_excursion_r": round(max_mae, 4) if entry_filled else None,
        "reached_1r": reached_1r if entry_filled else None,
        "reached_2r": reached_2r if entry_filled else None,
        "target_1_hit": target_1_hit if entry_filled else None,
        "stop_hit": stop_hit if entry_filled else None,
        "expired": bool(entry_filled and not target_1_hit and not stop_hit),
        "outcome_at": outcome_at,
    }


def empty_outcome() -> dict[str, Any]:
    return {
        "entry_filled": None,
        "entry_filled_at": None,
        "max_favorable_excursion_r": None,
        "max_adverse_excursion_r": None,
        "reached_1r": None,
        "reached_2r": None,
        "target_1_hit": None,
        "stop_hit": None,
        "expired": None,
        "outcome_at": None,
    }


def pct(count: int, total: int) -> float:
    return round((count / total) * 100.0, 2) if total else 0.0


def analyze(files: list[Path], symbol: Optional[str] = None) -> tuple[dict[str, Any], dict[str, list[dict[str, Any]]]]:
    rows = load_symbol_scans(files, symbol=symbol)
    by_symbol = defaultdict(list)
    by_candidate = defaultdict(list)
    for row in rows:
        if row.get("symbol"):
            by_symbol[str(row["symbol"])].append(row)
        cid = candidate_id(row)
        if cid:
            by_candidate[cid].append(row)

    candidates = []
    for cid, group in by_candidate.items():
        group.sort(key=lambda item: item.get("_timestamp") or datetime.min.replace(tzinfo=timezone.utc))
        first = group[0]
        max_tier = max((candidate_tier(item) for item in group if candidate_tier(item)), key=lambda item: TIER_ORDER.get(item, 0), default=None)
        future = [item for item in by_symbol[str(first.get("symbol"))] if (item.get("_timestamp") and first.get("_timestamp") and item["_timestamp"] > first["_timestamp"])]
        outcome = compute_outcome(first, future)
        gates = first.get("delivery_gate_checks") or {}
        failed_gates = gates.get("failed_gates") if isinstance(gates, dict) else []
        reasons = first.get("shadow_rejection_reasons") or candidate_payload(first).get("shadow_rejection_reasons") or []
        candidates.append({
            "shadow_candidate_id": cid,
            "symbol": first.get("symbol"),
            "first_seen": first.get("timestamp"),
            "last_seen": group[-1].get("timestamp"),
            "snapshot_count": len(group),
            "tier": max_tier,
            "direction": candidate_direction(first),
            "htf_context_class": first.get("htf_context_class") or candidate_payload(first).get("htf_context_class"),
            "entry": candidate_price(first, "entry"),
            "stop_loss": candidate_price(first, "stop_loss"),
            "target_1": candidate_price(first, "target_1"),
            "rr_to_target_1": candidate_price(first, "rr_to_target_1"),
            "production_a_plus_allowed": bool(first.get("production_a_plus_allowed")),
            "failed_production_gates": ",".join(str(item) for item in (failed_gates or [])),
            "shadow_rejection_reasons": ",".join(str(item) for item in (reasons or [])),
            **outcome,
        })

    tier_counts = Counter(row["tier"] for row in candidates if row.get("tier"))
    symbol_day_counts = Counter((row.get("symbol"), str(row.get("first_seen", ""))[:10], row.get("tier")) for row in candidates)
    candidate_rows_by_day = [
        {"symbol": symbol, "day": day, "tier": tier, "count": count}
        for (symbol, day, tier), count in sorted(symbol_day_counts.items())
    ]
    grouped_rows = build_grouped_rows(candidates, "htf_context_class") + build_grouped_rows(candidates, "failed_production_gates")
    day_count = max(1, len({str(row.get("first_seen", ""))[:10] for row in candidates if row.get("first_seen")}))
    week_count = max(1.0, day_count / 7.0)
    b_per_day = tier_counts.get("B", 0) / day_count
    a_per_day = tier_counts.get("A", 0) / day_count
    ap_per_week = tier_counts.get("A+", 0) / week_count
    warnings = []
    if b_per_day < EXPECTED_B_PER_DAY[0] or b_per_day > EXPECTED_B_PER_DAY[1]:
        warnings.append(f"B candidates/day {b_per_day:.2f} outside expected {EXPECTED_B_PER_DAY[0]}-{EXPECTED_B_PER_DAY[1]}")
    if a_per_day < EXPECTED_A_PER_DAY[0] or a_per_day > EXPECTED_A_PER_DAY[1]:
        warnings.append(f"A candidates/day {a_per_day:.2f} outside expected {EXPECTED_A_PER_DAY[0]}-{EXPECTED_A_PER_DAY[1]}")
    if ap_per_week < EXPECTED_A_PLUS_PER_WEEK[0] or ap_per_week > EXPECTED_A_PLUS_PER_WEEK[1]:
        warnings.append(f"A+ candidates/week {ap_per_week:.2f} outside expected {EXPECTED_A_PLUS_PER_WEEK[0]}-{EXPECTED_A_PLUS_PER_WEEK[1]}")

    total = len(candidates)
    filled = sum(1 for row in candidates if row.get("entry_filled") is True)
    summary = {
        "symbol_scan_records": len(rows),
        "shadow_candidate_count": total,
        "tier_counts": dict(tier_counts),
        "conversion": {
            "C_to_B_percent": pct(tier_counts.get("B", 0) + tier_counts.get("A", 0) + tier_counts.get("A+", 0), total),
            "B_to_A_percent": pct(tier_counts.get("A", 0) + tier_counts.get("A+", 0), tier_counts.get("B", 0) + tier_counts.get("A", 0) + tier_counts.get("A+", 0)),
            "A_to_A_plus_percent": pct(tier_counts.get("A+", 0), tier_counts.get("A", 0) + tier_counts.get("A+", 0)),
        },
        "fill_rate_percent": pct(filled, total),
        "reached_1r_percent": pct(sum(1 for row in candidates if row.get("reached_1r") is True), filled),
        "reached_2r_percent": pct(sum(1 for row in candidates if row.get("reached_2r") is True), filled),
        "target_1_hit_percent": pct(sum(1 for row in candidates if row.get("target_1_hit") is True), filled),
        "stop_hit_percent": pct(sum(1 for row in candidates if row.get("stop_hit") is True), filled),
        "frequency": {
            "b_candidates_per_day": round(b_per_day, 4),
            "a_candidates_per_day": round(a_per_day, 4),
            "a_plus_candidates_per_week": round(ap_per_week, 4),
            "warnings": warnings,
        },
    }
    return summary, {
        "shadow_candidates": candidates,
        "candidate_count_by_symbol_day": candidate_rows_by_day,
        "grouped_results": grouped_rows,
    }


def build_grouped_rows(candidates: list[dict[str, Any]], key: str) -> list[dict[str, Any]]:
    groups = defaultdict(list)
    for row in candidates:
        value = row.get(key) or "none"
        if key == "failed_production_gates" and "," in str(value):
            for part in str(value).split(","):
                groups[part or "none"].append(row)
        else:
            groups[str(value)].append(row)
    output = []
    for value, rows in sorted(groups.items()):
        filled = sum(1 for row in rows if row.get("entry_filled") is True)
        mfes = [as_float(row.get("max_favorable_excursion_r")) for row in rows if as_float(row.get("max_favorable_excursion_r")) is not None]
        maes = [as_float(row.get("max_adverse_excursion_r")) for row in rows if as_float(row.get("max_adverse_excursion_r")) is not None]
        output.append({
            "group": key,
            "value": value,
            "candidate_count": len(rows),
            "fill_rate_percent": pct(filled, len(rows)),
            "target_1_hit_percent": pct(sum(1 for row in rows if row.get("target_1_hit") is True), filled),
            "stop_hit_percent": pct(sum(1 for row in rows if row.get("stop_hit") is True), filled),
            "average_mfe_r": round(sum(mfes) / len(mfes), 4) if mfes else None,
            "average_mae_r": round(sum(maes) / len(maes), 4) if maes else None,
        })
    return output


def write_json(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fieldnames = sorted({key for row in rows for key in row})
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def write_html(path: Path, summary: dict[str, Any], tables: dict[str, list[dict[str, Any]]]) -> None:
    def table(rows: list[dict[str, Any]], title: str) -> str:
        if not rows:
            return f"<h2>{html.escape(title)}</h2><p>No rows.</p>"
        columns = list(rows[0].keys())
        head = "".join(f"<th>{html.escape(str(col))}</th>" for col in columns)
        body = "".join(
            "<tr>" + "".join(f"<td>{html.escape(str(row.get(col, '')))}</td>" for col in columns) + "</tr>"
            for row in rows[:500]
        )
        return f"<h2>{html.escape(title)}</h2><table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"

    content = f"""<!doctype html>
<html><head><meta charset="utf-8"><title>Shadow Candidate Report</title>
<style>body{{font-family:-apple-system,BlinkMacSystemFont,Segoe UI,sans-serif;margin:24px;color:#17202a}}table{{border-collapse:collapse;width:100%;font-size:12px}}th,td{{border:1px solid #d7dde5;padding:6px;text-align:left;vertical-align:top}}th{{background:#eef2f6}}code,pre{{background:#f6f8fa;padding:8px;display:block;overflow:auto}}</style>
</head><body>
<h1>Shadow Candidate Report</h1>
<pre>{html.escape(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))}</pre>
{table(tables["candidate_count_by_symbol_day"], "Candidate Count By Symbol And Day")}
{table(tables["grouped_results"], "Grouped Results")}
{table(tables["shadow_candidates"], "Shadow Candidates")}
</body></html>"""
    path.write_text(content, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Analyze research-only shadow candidates from scanner JSONL logs.")
    parser.add_argument("inputs", nargs="+", help="One or more JSONL files, directories, or glob patterns.")
    parser.add_argument("--symbol", help="Limit analysis to one symbol.")
    parser.add_argument("--output-dir", default="reports/shadow_candidates", help="Directory for generated reports.")
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
        write_csv(output_dir / "shadow_candidates.csv", tables["shadow_candidates"])
        write_csv(output_dir / "candidate_count_by_symbol_day.csv", tables["candidate_count_by_symbol_day"])
        write_csv(output_dir / "grouped_results.csv", tables["grouped_results"])
    if "html" in formats:
        write_html(output_dir / "report.html", summary, tables)
    print(json.dumps({"output_dir": str(output_dir), "files": len(files), "shadow_candidate_count": summary["shadow_candidate_count"]}, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
