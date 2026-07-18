import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd

os.environ.setdefault("GEMINI_API_KEY", "test-key")

from core.scenario_scanner import ScenarioEvent, ScenarioScanResult, ScenarioScannerOutput
from tools import replay_scenario


class OfflineReplayScenarioTest(unittest.TestCase):
    def setUp(self):
        replay_scenario.analyzer.reset_scenario_runtime_state()

    def tearDown(self):
        replay_scenario.analyzer.reset_scenario_runtime_state()

    def _write_15m_csv(self, directory: Path, symbol: str):
        index = pd.date_range("2026-07-14 00:00:00", periods=8, freq="15min")
        df = pd.DataFrame(
            {
                "timestamp": index,
                "open": [100 + i for i in range(len(index))],
                "high": [101 + i for i in range(len(index))],
                "low": [99 + i for i in range(len(index))],
                "close": [100.5 + i for i in range(len(index))],
                "volume": [1000 + i for i in range(len(index))],
            }
        )
        df.to_csv(directory / f"{symbol}_15m.csv", index=False)
        return index

    def test_replay_symbol_feeds_closed_candles_without_future_data(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            data_dir = Path(tmpdir)
            index = self._write_15m_csv(data_dir, "SOL")
            seen_last_indices = []

            def fake_analyze(symbol, df_4h, df_1h, df_15m, macro, analysis_time=None):
                seen_last_indices.append(df_15m.index[-1])
                step = len(seen_last_indices)
                candidate = ScenarioScanResult(
                    direction="LONG",
                    status="waiting_for_confirmation",
                    current_step="pd_location_valid",
                    next_expected_step="SFP_CONFIRMED",
                    signal_allowed=False,
                    scenario_valid=False,
                    completion_ratio=0.2,
                    completed_steps=2,
                    total_steps=10,
                    quality_score=60,
                    candidate_id="LONG_PD_1",
                    events_used=[
                        ScenarioEvent("PD_LOCATION_VALID", "LONG", df_15m.index[-1])
                    ],
                )
                scan = ScenarioScannerOutput(
                    best_long_scenario=candidate,
                    best_short_scenario=None,
                    selected_scenario=candidate,
                    selected_direction="LONG",
                    signal_allowed=False,
                    scenario_valid=False,
                    reason="waiting_for_sfp",
                    top_candidates=[candidate],
                    selected_scenario_id=candidate.candidate_id,
                )
                return (
                    {
                        "total_score": step,
                        "decision": "Ignore",
                        "scenario_status": "waiting_for_anchor",
                        "execution_status": "not_ready",
                        "diagnostics": {"trigger_confirmed": False},
                    },
                    {"scenario_scan": scan, "risk_plan": None},
                )

            with patch("tools.replay_scenario.analyzer.analyze_symbol_snapshot", side_effect=fake_analyze):
                result = replay_scenario.replay_symbol(
                    "SOL",
                    pd.Timestamp("2026-07-14 00:15:00"),
                    pd.Timestamp("2026-07-14 00:45:00"),
                    data_dir=data_dir,
                )

        self.assertEqual(seen_last_indices, list(index[1:4]))
        self.assertEqual(len(result["steps"]), 3)
        self.assertFalse(result["future_candles_used"])
        self.assertFalse(result["telegram_sent"])
        self.assertEqual(result["runtime_mode"], "sequential")
        self.assertEqual(result["final_step"]["candle_time"], "2026-07-14 00:45:00")
        self.assertTrue(result["transitions"])

    def test_sequential_replay_passes_runtime_state_between_steps(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            data_dir = Path(tmpdir)
            self._write_15m_csv(data_dir, "SOL")

            def fake_analyze(symbol, df_4h, df_1h, df_15m, macro, analysis_time=None):
                candidate = ScenarioScanResult(
                    direction="LONG",
                    status="waiting_for_confirmation",
                    current_step="pd_location_valid",
                    next_expected_step="SFP_CONFIRMED",
                    signal_allowed=False,
                    scenario_valid=False,
                    completion_ratio=0.2,
                    completed_steps=2,
                    total_steps=10,
                    quality_score=60,
                    candidate_id="LONG_PD_1",
                    anchor_index=df_15m.index[0],
                    last_event_index=df_15m.index[-1],
                    events_used=[ScenarioEvent("PD_LOCATION_VALID", "LONG", df_15m.index[-1])],
                )
                scan = ScenarioScannerOutput(
                    best_long_scenario=candidate,
                    best_short_scenario=None,
                    selected_scenario=candidate,
                    selected_direction="LONG",
                    signal_allowed=False,
                    scenario_valid=False,
                    reason="waiting_for_sfp",
                    top_candidates=[candidate],
                    selected_scenario_id=candidate.candidate_id,
                )
                replay_scenario.analyzer._apply_runtime_update_counts(symbol, scan, analysis_time=analysis_time)
                return (
                    {
                        "total_score": 0,
                        "decision": "Ignore",
                        "scenario_status": "waiting_for_anchor",
                        "execution_status": "not_ready",
                        "diagnostics": {"trigger_confirmed": False},
                    },
                    {"scenario_scan": scan, "risk_plan": None},
                )

            with patch("tools.replay_scenario.analyzer.analyze_symbol_snapshot", side_effect=fake_analyze):
                result = replay_scenario.replay_symbol(
                    "SOL",
                    pd.Timestamp("2026-07-14 00:15:00"),
                    pd.Timestamp("2026-07-14 00:45:00"),
                    data_dir=data_dir,
                    runtime_mode="sequential",
                )

        counts = [step["runtime_update_count"] for step in result["steps"]]
        self.assertEqual(counts, [0, 1, 2])
        self.assertEqual(
            result["steps"][1]["runtime_state_input_hash"],
            result["steps"][0]["runtime_state_output_hash"],
        )
        self.assertEqual(
            result["steps"][2]["runtime_state_input_hash"],
            result["steps"][1]["runtime_state_output_hash"],
        )
        self.assertEqual(replay_scenario.analyzer.export_scenario_runtime_state(), replay_scenario.analyzer._empty_scenario_runtime_state())

    def test_diff_replay_results_reports_changed_steps(self):
        left = {"steps": [{"candle_time": "t1", "score": 10}, {"candle_time": "t2", "score": 20}]}
        right = {"steps": [{"candle_time": "t1", "score": 10}, {"candle_time": "t2", "score": 25}]}

        diff = replay_scenario.diff_replay_results(left, right)

        self.assertFalse(diff["matches"])
        self.assertEqual(diff["changed_steps"][0]["candle_time"], "t2")


if __name__ == "__main__":
    unittest.main()
