import tempfile
import unittest
from pathlib import Path

import pandas as pd

from research.analyze_early_trigger_quality import (
    MAX_BARS_AFTER_SFP,
    classify_early_candle,
    dedupe_sfp_candidates,
    double_filter_class,
    lifetime_rows,
    near_miss_rows,
    outcome_for,
    potential_trigger_candles,
    run,
)


def scan_record(symbol="AAVE", candidate_id="C1", direction="short", ts="2026-07-19T09:00:00", market_ts="2026-07-19 09:00:00", candidate=None):
    candidate = candidate or candidate_fixture(candidate_id, direction)
    return (
        "fixture.jsonl",
        {
            "record_type": "symbol_scan",
            "symbol": symbol,
            "timestamp": ts,
            "market_data_timestamp_15m": market_ts,
            "market_open_15m": 10.0,
            "market_high_15m": 10.5,
            "market_low_15m": 9.0,
            "market_close_15m": 9.2,
            "atr": 1.0,
            "features": {"scenario_scan": {"top_candidates": [candidate]}, "htf_context": {"direction": "bearish"}},
        },
    )


def candidate_fixture(candidate_id="C1", direction="short", status="building", trigger_scan=None):
    return {
        "candidate_id": candidate_id,
        "direction": direction,
        "status": status,
        "anchor_type": "sfp_reversal",
        "anchor_index": "2026-07-19 08:00:00",
        "candidate_created_at": "2026-07-19 08:00:00",
        "market_age_bars": 0,
        "runtime_update_count": 0,
        "events_used": [
            {"event_type": "SFP_CONFIRMED", "index": "2026-07-19 08:00:00", "quality_score": 82, "payload": {"index": "2026-07-19 08:00:00", "quality_score": 82}},
        ],
        "trigger_scan": trigger_scan or {"rejected_reason": "waiting_for_early_trigger", "early_trigger_confirmed": False},
    }


class EarlyTriggerQualityTest(unittest.TestCase):
    def test_candidate_deduplication_same_candidate_across_scans(self):
        rows = [
            scan_record(ts="2026-07-19T09:00:00"),
            scan_record(ts="2026-07-19T09:15:00", market_ts="2026-07-19 09:15:00"),
        ]
        candidates = dedupe_sfp_candidates(rows)
        self.assertEqual(len(candidates), 1)
        item = next(iter(candidates.values()))
        self.assertEqual(item["scan_cycles"], 2)

    def test_trigger_candidate_extraction_from_candles(self):
        candidate = candidate_fixture("L1", "long")
        candidates = dedupe_sfp_candidates([scan_record(symbol="LDO", candidate_id="L1", direction="long", candidate=candidate)])
        idx = pd.to_datetime(["2026-07-19 08:15", "2026-07-19 08:30", "2026-07-19 08:45", "2026-07-19 09:00", "2026-07-19 09:15"])
        candles = pd.DataFrame(
            {
                "open": [10, 10.1, 10.2, 10.3, 10.5],
                "high": [10.0, 10.2, 10.8, 10.6, 11.2],
                "low": [9.8, 9.9, 10.0, 10.1, 10.4],
                "close": [9.9, 10.0, 10.4, 10.2, 11.1],
                "atr": [1, 1, 1, 1, 1],
                "rvol": [1, 1, 1, 1, 1],
            },
            index=idx,
        )
        rows = potential_trigger_candles(candidates, {"LDO": candles})
        self.assertEqual(len(rows), 5)
        self.assertTrue(any(row["production_decision"] == "pass" for row in rows))

    def test_long_outcome_mfe_mae(self):
        candles = pd.DataFrame(
            {"open": [10, 10, 10], "high": [10, 12, 11], "low": [10, 9.5, 10.5], "close": [10, 11, 10.8], "atr": [1, 1, 1]},
            index=pd.to_datetime(["2026-01-01 00:00", "2026-01-01 00:15", "2026-01-01 00:30"]),
        )
        result = outcome_for(candles, "2026-01-01 00:00", "long", 2)
        self.assertTrue(result["outcome_available"])
        self.assertEqual(result["MFE_ATR"], 2.0)
        self.assertEqual(result["MAE_ATR"], 0.5)

    def test_short_outcome_mfe_mae(self):
        candles = pd.DataFrame(
            {"open": [10, 10, 10], "high": [10, 10.5, 10.2], "low": [10, 8, 9], "close": [10, 9, 9.3], "atr": [1, 1, 1]},
            index=pd.to_datetime(["2026-01-01 00:00", "2026-01-01 00:15", "2026-01-01 00:30"]),
        )
        result = outcome_for(candles, "2026-01-01 00:00", "short", 2)
        self.assertTrue(result["outcome_available"])
        self.assertEqual(result["MFE_ATR"], 2.0)
        self.assertEqual(result["MAE_ATR"], 0.5)

    def test_near_miss_relative_shortfall_single_and_multi(self):
        rows = [
            {
                "candidate_id": "C1",
                "symbol": "AAVE",
                "direction": "long",
                "SFP_timestamp": "2026-01-01 00:00",
                "candle_timestamp": "2026-01-01 00:15",
                "trigger_type_candidate": "bullish_early_choch",
                "body_ratio": 0.44,
                "displacement_ratio": 0.6,
                "close_position": 0.7,
                "quality_score": 60,
                "RVOL": 1.5,
                "ATR": 1,
                "failed_conditions": "body_ratio_below_min",
                "hard_passed_count": 6,
            },
            {
                "candidate_id": "C2",
                "symbol": "WLD",
                "direction": "short",
                "SFP_timestamp": "2026-01-01 00:00",
                "candle_timestamp": "2026-01-01 00:15",
                "trigger_type_candidate": "bearish_early_choch",
                "body_ratio": 0.44,
                "displacement_ratio": 0.49,
                "close_position": 0.39,
                "quality_score": 54,
                "RVOL": 1.5,
                "ATR": 1,
                "failed_conditions": "body_ratio_below_min;displacement_below_min;quality_score_below_min",
                "hard_passed_count": 5,
            },
        ]
        near = near_miss_rows(rows)
        categories = {row["candidate_id"]: row["near_miss_category"] for row in near}
        self.assertEqual(categories["C1"], "single_condition_near_miss")
        self.assertEqual(categories["C2"], "multi_condition_near_miss")
        body = next(row for row in near if row["candidate_id"] == "C1" and row["metric"] == "body_ratio")
        self.assertAlmostEqual(body["percentage_shortfall"], 2.2222)

    def test_missing_fields_classified_unknown_for_double_filter(self):
        self.assertEqual(double_filter_class({"body_ratio": None, "displacement_ratio": 0.6, "close_position": 0.7, "RVOL": 1.5, "quality_score": 70}), "unknown_due_to_missing_fields")

    def test_double_filter_requires_complete_fields(self):
        row = {"body_ratio": 0.5, "displacement_ratio": 0.6, "close_position": 0.7, "RVOL": 1.5, "quality_score": 54, "failed_conditions": "quality_score_below_min"}
        self.assertEqual(double_filter_class(row), "passed_all_hard_conditions_but_failed_quality")

    def test_candidate_age_calculation_and_stale_detection(self):
        candidate = candidate_fixture("STALE", "long", status="building")
        rows = [scan_record(symbol="LDO", candidate_id="STALE", direction="long", candidate=candidate, market_ts="2026-07-19 15:00:00")]
        candidates = dedupe_sfp_candidates(rows)
        lifetime = lifetime_rows(candidates)
        self.assertGreater(lifetime[0]["bars_since_sfp_by_market_timestamp"], MAX_BARS_AFTER_SFP)
        self.assertEqual(lifetime[0]["lifetime_class"], "stale_active_candidate")

    def test_aave_ldo_wld_like_fixture_cases(self):
        rows = [
            scan_record(symbol="AAVE", candidate_id="AAVE1", direction="short", candidate=candidate_fixture("AAVE1", "short")),
            scan_record(symbol="LDO", candidate_id="LDO1", direction="long", candidate=candidate_fixture("LDO1", "long")),
            scan_record(symbol="WLD", candidate_id="WLD1", direction="short", candidate=candidate_fixture("WLD1", "short")),
        ]
        candidates = dedupe_sfp_candidates(rows)
        self.assertEqual({item["symbol"] for item in candidates.values()}, {"AAVE", "LDO", "WLD"})

    def test_run_writes_artifacts(self):
        with tempfile.TemporaryDirectory() as tmp:
            journal = Path(tmp) / "scan.jsonl"
            _path, record = scan_record()
            journal.write_text(__import__("json").dumps(record) + "\n", encoding="utf-8")
            output = Path(tmp) / "out"
            summary = run([str(journal)], output)
            self.assertFalse(summary["production_logic_changed"])
            self.assertTrue((output / "early_trigger_summary.json").exists())
            self.assertTrue((output / "early_trigger_candidates.csv").exists())


if __name__ == "__main__":
    unittest.main()
