import argparse
import json
from pathlib import Path
from typing import Optional

import pandas as pd


def load_journal(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    files = [path] if path.is_file() else sorted(path.glob("*.jsonl"))
    rows = []
    for file_path in files:
        with file_path.open("r", encoding="utf-8") as fh:
            for line in fh:
                if line.strip():
                    rows.append(json.loads(line))
    return pd.json_normalize(rows)


def _column(df: pd.DataFrame, name: str) -> pd.Series:
    if name not in df:
        return pd.Series(dtype="object")
    return df[name]


def _count_values(df: pd.DataFrame, name: str, limit: Optional[int] = None) -> dict:
    series = _column(df, name).dropna()
    if series.empty:
        return {}
    counts = series.astype(str).value_counts()
    if limit:
        counts = counts.head(limit)
    return {str(key): int(value) for key, value in counts.items()}


def _bool_counts(df: pd.DataFrame, name: str) -> dict:
    series = _column(df, name).dropna()
    if series.empty:
        return {}
    counts = series.astype(bool).value_counts()
    return {str(key).lower(): int(value) for key, value in counts.items()}


def _numeric_summary(df: pd.DataFrame, name: str) -> dict:
    series = pd.to_numeric(_column(df, name), errors="coerce").dropna()
    return _numeric_summary_from_series(series)


def _numeric_summary_from_series(series: pd.Series) -> dict:
    if series.empty:
        return {}
    return {
        "mean": round(float(series.mean()), 4),
        "max": round(float(series.max()), 4),
        "min": round(float(series.min()), 4),
    }


def _detected_count(df: pd.DataFrame, type_column: str) -> int:
    return int(_column(df, type_column).dropna().shape[0])


def _quality_block(df: pd.DataFrame, prefix: str) -> dict:
    return {
        "detected": _detected_count(df, f"{prefix}.type"),
        "type_counts": _count_values(df, f"{prefix}.type"),
        "quality": _numeric_summary(df, f"{prefix}.quality_score"),
        "q80_plus": int((pd.to_numeric(_column(df, f"{prefix}.quality_score"), errors="coerce") >= 80).sum()),
        "q90_plus": int((pd.to_numeric(_column(df, f"{prefix}.quality_score"), errors="coerce") >= 90).sum()),
    }


def _sfp_summary(df: pd.DataFrame) -> dict:
    quality = pd.to_numeric(_column(df, "features.sfp.quality_score"), errors="coerce")
    rejection = pd.to_numeric(_column(df, "features.sfp.rejection_strength"), errors="coerce")
    depth = pd.to_numeric(_column(df, "features.sfp.liquidity_depth_atr"), errors="coerce")
    legacy_depth = pd.to_numeric(_column(df, "features.sfp.liquidity_depth"), errors="coerce")
    depth = depth.combine_first(legacy_depth)

    strong = (quality >= 80) & (rejection >= 75) & (depth >= 0.15)
    weak_pierce = (depth < 0.15) | (rejection < 60)
    result = _quality_block(df, "features.sfp")
    result.update({
        "strong_tier": int(strong.sum()),
        "weak_pierce_or_rejection": int(weak_pierce.sum()),
        "rejection": _numeric_summary(df, "features.sfp.rejection_strength"),
        "depth_atr": _numeric_summary_from_series(depth.dropna()),
    })
    return result


def _premium_discount_summary(df: pd.DataFrame) -> dict:
    return {
        "zone_counts": _count_values(df, "features.premium_discount.zone"),
        "depth_counts": _count_values(df, "features.premium_discount.zone_depth"),
        "timeframe_counts": _count_values(df, "features.premium_discount.range_timeframe"),
        "pd_valid_counts": _bool_counts(df, "diagnostics.pd_valid"),
        "shallow_counts": _bool_counts(df, "diagnostics.pd_shallow"),
        "distance_from_eq_percent": _numeric_summary(df, "features.premium_discount.distance_from_equilibrium_percent"),
    }


def _risk_plan_summary(df: pd.DataFrame) -> dict:
    return {
        "valid_counts": _bool_counts(df, "features.risk_plan.valid"),
        "reason_counts": _count_values(df, "features.risk_plan.reason", limit=8),
        "entry_model_counts": _count_values(df, "features.risk_plan.entry_model"),
        "target_model_counts": _count_values(df, "features.risk_plan.target_model"),
        "rr_to_target_1": _numeric_summary(df, "features.risk_plan.rr_to_target_1"),
        "stop_distance_percent": _numeric_summary(df, "features.risk_plan.stop_distance_percent"),
    }


def _liquidity_map_summary(df: pd.DataFrame) -> dict:
    return {
        "nearest_buy_side_type_counts": _count_values(df, "features.liquidity_map.nearest_buy_side.type"),
        "nearest_sell_side_type_counts": _count_values(df, "features.liquidity_map.nearest_sell_side.type"),
        "strongest_buy_side_strength": _numeric_summary(df, "features.liquidity_map.strongest_buy_side.strength"),
        "strongest_sell_side_strength": _numeric_summary(df, "features.liquidity_map.strongest_sell_side.strength"),
        "nearest_buy_side_swept": _bool_counts(df, "features.liquidity_map.nearest_buy_side.swept"),
        "nearest_sell_side_swept": _bool_counts(df, "features.liquidity_map.nearest_sell_side.swept"),
    }


def _gate_summary(df: pd.DataFrame) -> dict:
    return {
        "pd_valid": _bool_counts(df, "diagnostics.pd_valid"),
        "pd_shallow": _bool_counts(df, "diagnostics.pd_shallow"),
        "with_trend": _bool_counts(df, "diagnostics.with_trend"),
        "context_structure_aligned": _bool_counts(df, "diagnostics.context_structure_aligned"),
        "trigger_structure_aligned": _bool_counts(df, "diagnostics.trigger_structure_aligned"),
        "trigger_confirmed": _bool_counts(df, "diagnostics.trigger_confirmed"),
        "sfp_present": _bool_counts(df, "diagnostics.sfp_present"),
        "fvg_test_present": _bool_counts(df, "diagnostics.fvg_test_present"),
        "scenario_valid": _bool_counts(df, "diagnostics.scenario_valid"),
    }


def _symbol_summary(df: pd.DataFrame) -> dict:
    if "symbol" not in df:
        return {}
    symbols = {}
    for symbol, group in df.groupby("symbol", dropna=True):
        score = pd.to_numeric(_column(group, "score"), errors="coerce").dropna()
        symbols[str(symbol)] = {
            "rows": int(len(group)),
            "score_mean": round(float(score.mean()), 4) if not score.empty else None,
            "score_max": round(float(score.max()), 4) if not score.empty else None,
            "decision_counts": _count_values(group, "decision"),
            "no_trade_reason_counts": _count_values(group, "no_trade_reason"),
        }
    return dict(sorted(symbols.items()))


def summarize(df: pd.DataFrame) -> dict:
    if df.empty:
        return {"rows": 0}
    return {
        "rows": int(len(df)),
        "symbols": sorted(df["symbol"].dropna().unique().tolist()) if "symbol" in df else [],
        "decision_counts": _count_values(df, "decision"),
        "no_trade_reason_counts": _count_values(df, "no_trade_reason"),
        "score_mean": round(float(df["score"].dropna().mean()), 4) if "score" in df and not df["score"].dropna().empty else None,
        "score_max": round(float(df["score"].dropna().max()), 4) if "score" in df and not df["score"].dropna().empty else None,
        "score_by_symbol": _symbol_summary(df),
        "features": {
            "trend_4h": {
                "strength_counts": _count_values(df, "features.trend_4h.strength"),
                "adx": _numeric_summary(df, "features.trend_4h.adx"),
            },
            "market_structure_4h": {
                "trend_counts": _count_values(df, "features.market_structure_4h.trend"),
                "reason_counts": _count_values(df, "features.market_structure_4h.reason", limit=8),
                "confidence": _numeric_summary(df, "features.market_structure_4h.confidence"),
            },
            "context_1h": _quality_block(df, "features.context_1h"),
            "trigger_15m": _quality_block(df, "features.trigger_15m"),
            "sfp": _sfp_summary(df),
            "premium_discount": _premium_discount_summary(df),
            "liquidity_map": _liquidity_map_summary(df),
            "risk_plan": _risk_plan_summary(df),
        },
        "gates": _gate_summary(df),
        "state_machine_counts": _count_values(df, "breakdown.state_machine", limit=10),
    }


def main():
    parser = argparse.ArgumentParser(description="Summarize Sniper JSONL scan journal.")
    parser.add_argument("path", nargs="?", default="data/journal", help="Journal JSONL file or directory")
    args = parser.parse_args()

    path = Path(args.path)
    df = load_journal(path)
    summary = summarize(df)
    summary["path"] = str(path)
    summary["exists"] = path.exists()
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
