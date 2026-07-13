import csv
import json
import os
from collections import Counter
from datetime import datetime
from pathlib import Path

# Чтобы analyzer.py не падал при импорте в диагностическом режиме,
# если GEMINI_API_KEY не задан локально.
# Генерация Gemini здесь не используется.
os.environ.setdefault("GEMINI_API_KEY", "diagnostic-only")
os.environ.setdefault("SCAN_JOURNAL_ENABLED", "false")

from analyzer import prepare_and_analyze
from services.macro_context import get_macro_context


TOP_50_COINS = [
    'BTC', 'ETH', 'SOL', 'XRP', 'BNB',
    'DOGE', 'ADA', 'AVAX', 'LINK', 'DOT',
    'TRX', 'LTC', 'BCH', 'UNI', 'SUI',
    'NEAR', 'APT', 'ICP', 'FIL', 'ETC',
    'ATOM', 'ARB', 'OP', 'INJ', 'TIA',
    'SEI', 'AAVE', 'MKR', 'RUNE', 'LDO',
    'ORDI', 'WIF', 'PEPE', 'BONK', 'FET',
    'RENDER', 'GRT', 'JUP', 'PYTH', 'ENA',
    'HYPE', 'TON', 'WLD', 'ALGO', 'SAND',
    'MANA', 'APE', 'DYDX', 'IMX', 'STX',
]


def safe_get(data, *keys, default=None):
    current = data
    for key in keys:
        if current is None:
            return default
        if isinstance(current, dict):
            current = current.get(key)
        else:
            return default
    return current if current is not None else default


def score_bucket(score: int) -> str:
    if score >= 70:
        return "70+"
    if score >= 56:
        return "56-69"
    if score >= 41:
        return "41-55"
    if score >= 26:
        return "26-40"
    if score >= 11:
        return "11-25"
    return "0-10"


def extract_row(symbol, score_result, analysis_data):
    breakdown = score_result.get("breakdown", {})
    diagnostics = score_result.get("diagnostics", {})
    trigger_debug = analysis_data.get("trigger_debug") or {}

    risk_plan = analysis_data.get("risk_plan")
    risk_valid = bool(risk_plan and risk_plan.get("valid"))

    liquidity_map = analysis_data.get("liquidity_map")

    return {
        "symbol": symbol,
        "score": score_result.get("total_score", score_result.get("score", 0)),
        "raw_score": score_result.get("raw_score", score_result.get("total_score", 0)),
        "decision": score_result.get("decision"),
        "direction": score_result.get("direction"),
        "no_trade_reason": score_result.get("no_trade_reason") or diagnostics.get("no_trade_reason"),

        "trend": breakdown.get("trend"),
        "htf_structure": breakdown.get("htf_structure"),
        "structure": breakdown.get("structure"),
        "liquidity": breakdown.get("liquidity"),
        "fvg": breakdown.get("fvg"),
        "volume": breakdown.get("volume"),
        "premium_discount": breakdown.get("premium_discount"),
        "risk_plan": breakdown.get("risk_plan"),
        "trigger_debug": breakdown.get("trigger_debug"),
        "selected_trigger": safe_get(trigger_debug, "selected_trigger", "type"),
        "opposite_trigger": safe_get(trigger_debug, "opposite_trigger", "type"),
        "long_trigger_candidate": safe_get(trigger_debug, "long_trigger_candidate", "type"),
        "short_trigger_candidate": safe_get(trigger_debug, "short_trigger_candidate", "type"),
        "state_machine": breakdown.get("state_machine"),
        "scenario": breakdown.get("scenario"),
        "macro": breakdown.get("macro"),

        "with_trend": diagnostics.get("with_trend"),
        "pd_valid": diagnostics.get("pd_valid"),
        "pd_shallow": diagnostics.get("pd_shallow"),
        "sfp_present": diagnostics.get("sfp_present"),
        "trigger_confirmed": diagnostics.get("trigger_confirmed"),
        "trigger_structure_aligned": diagnostics.get("trigger_structure_aligned"),
        "fvg_test_present": diagnostics.get("fvg_test_present"),
        "scenario_valid": diagnostics.get("scenario_valid"),
        "state_machine_allowed": diagnostics.get("state_machine_allowed"),
        "risk_plan_valid": risk_valid,
        "trigger_rejected_reason": trigger_debug.get("trigger_rejected_reason") or diagnostics.get("trigger_rejected_reason"),
        "fvg_scenario_valid": diagnostics.get("fvg_scenario_valid"),
        "fvg_rejected_reason": diagnostics.get("fvg_rejected_reason"),

        "nearest_buy_side": str(getattr(liquidity_map, "nearest_buy_side", None)) if liquidity_map else None,
        "nearest_sell_side": str(getattr(liquidity_map, "nearest_sell_side", None)) if liquidity_map else None,
    }


def main():
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_dir = Path("research/output")
    output_dir.mkdir(parents=True, exist_ok=True)

    csv_path = output_dir / f"top50_scan_{timestamp}.csv"
    json_path = output_dir / f"top50_scan_{timestamp}.json"

    print("Fetching macro context...")
    macro = get_macro_context()

    rows = []
    errors = []

    for index, symbol in enumerate(TOP_50_COINS, start=1):
        print(f"[{index:02d}/{len(TOP_50_COINS)}] Scanning {symbol}...")

        try:
            score_result, analysis_data = prepare_and_analyze(symbol, macro)

            if not score_result or not analysis_data:
                errors.append({"symbol": symbol, "error": "No result"})
                continue

            row = extract_row(symbol, score_result, analysis_data)
            rows.append(row)

            print(
                f"  -> {row['score']}/100 | "
                f"{row['decision']} | "
                f"{row['direction']} | "
                f"{row['no_trade_reason']}"
            )

        except Exception as exc:
            errors.append({"symbol": symbol, "error": str(exc)})
            print(f"  ERROR: {exc}")

    rows_sorted = sorted(rows, key=lambda r: int(r["score"] or 0), reverse=True)

    with csv_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows_sorted[0].keys()) if rows_sorted else [])
        if rows_sorted:
            writer.writeheader()
            writer.writerows(rows_sorted)

    with json_path.open("w", encoding="utf-8") as f:
        json.dump(
            {
                "timestamp": timestamp,
                "count": len(rows_sorted),
                "errors": errors,
                "rows": rows_sorted,
            },
            f,
            ensure_ascii=False,
            indent=2,
            default=str,
        )

    print("\n==============================")
    print("TOP 15 BY SCORE")
    print("==============================")
    for row in rows_sorted[:15]:
        print(
            f"{row['symbol']:>8} | "
            f"{row['score']:>3}/100 | "
            f"{row['decision']:<10} | "
            f"{row['direction']:<7} | "
            f"{row['no_trade_reason']}"
        )

    print("\n==============================")
    print("SCORE DISTRIBUTION")
    print("==============================")
    distribution = Counter(score_bucket(int(row["score"] or 0)) for row in rows_sorted)
    for bucket in ["0-10", "11-25", "26-40", "41-55", "56-69", "70+"]:
        print(f"{bucket:>6}: {distribution.get(bucket, 0)}")

    print("\n==============================")
    print("NO TRADE REASONS")
    print("==============================")
    reasons = Counter(row["no_trade_reason"] for row in rows_sorted)
    for reason, count in reasons.most_common():
        print(f"{reason}: {count}")

    print("\n==============================")
    print("GATE FAILURE SUMMARY")
    print("==============================")
    gate_fields = [
        "with_trend",
        "pd_valid",
        "sfp_present",
        "trigger_confirmed",
        "trigger_structure_aligned",
        "fvg_test_present",
        "scenario_valid",
        "state_machine_allowed",
        "risk_plan_valid",
    ]

    for field in gate_fields:
        failed = sum(1 for row in rows_sorted if not row.get(field))
        passed = sum(1 for row in rows_sorted if row.get(field))
        print(f"{field}: PASS {passed} / FAIL {failed}")

    print("\n==============================")
    print("TRIGGER REJECT REASONS")
    print("==============================")
    trigger_reasons = Counter(row.get("trigger_rejected_reason") for row in rows_sorted)
    for reason, count in trigger_reasons.most_common():
        print(f"{reason}: {count}")

    print("\n==============================")
    print("FVG SCENARIO REJECT REASONS")
    print("==============================")
    fvg_reasons = Counter(row.get("fvg_rejected_reason") for row in rows_sorted if row.get("fvg_rejected_reason"))
    for reason, count in fvg_reasons.most_common():
        print(f"{reason}: {count}")

    print("\nSaved:")
    print(f"CSV : {csv_path}")
    print(f"JSON: {json_path}")

    if errors:
        print("\nErrors:")
        for item in errors:
            print(f"{item['symbol']}: {item['error']}")


if __name__ == "__main__":
    main()
