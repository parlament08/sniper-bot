import os
import unittest

import pandas as pd

os.environ.setdefault("GEMINI_API_KEY", "test-key")

from diagnostics.scan_path_compare import compare_scan_paths, stable_candidate_order


class ScanPathCompareTest(unittest.TestCase):
    def _candles(self, periods=120, start="2026-01-01 00:00:00", freq="15min"):
        index = pd.date_range(start, periods=periods, freq=freq)
        base = pd.Series(range(periods), index=index).astype(float)
        return pd.DataFrame(
            {
                "open": 100 + base,
                "high": 101 + base,
                "low": 99 + base,
                "close": 100.5 + base,
                "volume": 1000 + base,
            },
            index=index,
        )

    def _inputs(self, periods=120):
        base_15m = self._candles(periods=periods)
        return {
            "15m": base_15m,
            "1h": base_15m.resample("1h", label="left", closed="left").agg(
                {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
            ).dropna(),
            "4h": base_15m.resample("4h", label="left", closed="left").agg(
                {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
            ).dropna(),
        }

    def _runner(self, symbol, candles_by_timeframe, macro_context):
        selected_id = "CAND-" + str(len(candles_by_timeframe["15m"]))
        score = {
            "total_score": len(candles_by_timeframe["15m"]),
            "decision": "Ignore",
            "final_decision": "Ignore",
            "scenario_status": "waiting_for_confirmation",
            "execution_status": "not_ready",
            "diagnostics": {
                "scenario_scan": {
                    "top_candidates": [{"candidate_id": selected_id}],
                    "selected_scenario": {"candidate_id": selected_id},
                },
                "a_plus_delivery_gate": {"allowed": False},
            },
        }
        analysis = {
            "market_structure": {"trend": "bullish"},
            "sfp_data": None,
            "risk_plan": {"reason": "entry_model_not_formed"},
            "trigger_scan": {"early_trigger": None, "confirmed_trigger": None},
            "fvg_candidates": [],
            "active_fvg": None,
        }
        return score, analysis

    def test_identical_inputs_have_equal_stage_and_final_hashes(self):
        candles = self._inputs()

        result = compare_scan_paths(
            "SOL",
            candles,
            pd.Timestamp("2026-01-02 06:00:00"),
            runner=self._runner,
        )

        self.assertTrue(result["inputs_equal"])
        self.assertIsNone(result["first_divergent_stage"])
        self.assertEqual(result["stage_diffs"], {})
        self.assertTrue(result["final_equal"])

    def test_open_candle_difference_is_reported_and_closed_filter_restores_match(self):
        live = self._inputs(periods=124)
        replay = self._inputs(periods=123)
        analysis_time = pd.Timestamp("2026-01-02 06:50:00")

        mismatch = compare_scan_paths(
            "SOL",
            live,
            analysis_time,
            other_candles_by_timeframe=replay,
            runner=self._runner,
        )
        self.assertFalse(mismatch["inputs_equal"])
        self.assertIn("15m", mismatch["input_diffs"])

        filtered = compare_scan_paths(
            "SOL",
            live,
            analysis_time,
            other_candles_by_timeframe=replay,
            apply_closed_candle_filter=True,
            runner=self._runner,
        )
        self.assertTrue(filtered["inputs_equal"])
        self.assertTrue(filtered["final_equal"])

    def test_different_warmup_length_is_detected_before_trading_result(self):
        long_history = self._inputs(periods=140)
        short_history = self._inputs(periods=120)

        result = compare_scan_paths(
            "SOL",
            long_history,
            pd.Timestamp("2026-01-02 06:00:00"),
            other_candles_by_timeframe=short_history,
            runner=self._runner,
        )

        self.assertFalse(result["inputs_equal"])
        self.assertIn("15m", result["input_diffs"])
        self.assertNotEqual(
            result["input_diffs"]["15m"]["left_count"],
            result["input_diffs"]["15m"]["right_count"],
        )

    def test_fixed_analysis_time_is_deterministic(self):
        candles = self._inputs()
        analysis_time = pd.Timestamp("2026-01-02 06:00:00")

        first = compare_scan_paths("SOL", candles, analysis_time, runner=self._runner)
        second = compare_scan_paths("SOL", candles, analysis_time, runner=self._runner)

        self.assertEqual(first["input_hashes"], second["input_hashes"])
        self.assertEqual(first["stage_hashes"], second["stage_hashes"])
        self.assertEqual(first["final"], second["final"])

    def test_stable_candidate_tie_break(self):
        candidates = [
            {"candidate_id": "CAND-B", "direction": "LONG", "anchor_index": 1, "last_event_index": 3},
            {"candidate_id": "CAND-A", "direction": "LONG", "anchor_index": 1, "last_event_index": 3},
        ]

        first = stable_candidate_order(candidates)[0]
        second = stable_candidate_order(list(reversed(candidates)))[0]

        self.assertEqual(first["candidate_id"], "CAND-A")
        self.assertEqual(second["candidate_id"], "CAND-A")


if __name__ == "__main__":
    unittest.main()
