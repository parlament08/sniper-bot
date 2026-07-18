import json
import os
import unittest
from unittest.mock import patch

import pandas as pd

os.environ.setdefault("GEMINI_API_KEY", "test-key")

import analyzer
from core.scenario_scanner import ScenarioEvent, ScenarioScanResult, ScenarioScannerOutput
from core.structure import find_fvg
from research.analyze_trigger_fvg_outcomes import fvg_rows_from_research_trace, latest_research_trace


class ScenarioResearchTraceTest(unittest.TestCase):
    def _candidate(self, *, status="waiting_for_confirmation", reason=None):
        confirmed = {
            "type": "bearish_bos",
            "index": "2026-01-01 10:00:00",
            "quality_score": 91,
            "body_ratio": 0.72,
            "displacement_ratio": 1.1,
            "close_position": 0.84,
            "rvol": 1.8,
            "failed_conditions": [],
        }
        return ScenarioScanResult(
            direction="SHORT",
            status=status,
            current_step="CONFIRMED_TRIGGER_CONFIRMED",
            next_expected_step="FVG_CREATED",
            signal_allowed=False,
            scenario_valid=False,
            completion_ratio=0.5,
            completed_steps=4,
            total_steps=8,
            quality_score=80,
            events_used=[
                ScenarioEvent("HTF_CONTEXT_CONFIRMED", "bearish", "2026-01-01 09:00:00"),
                ScenarioEvent("SFP_CONFIRMED", "bearish", "2026-01-01 09:30:00"),
                ScenarioEvent("EARLY_TRIGGER_CONFIRMED", "bearish", "2026-01-01 09:45:00", payload={"type": "bearish_choch", "index": "2026-01-01 09:45:00"}),
                ScenarioEvent("CONFIRMED_TRIGGER_CONFIRMED", "bearish", "2026-01-01 10:00:00", payload=confirmed),
            ],
            invalidated_reason=reason,
            candidate_id="C1",
            trigger_scan={"confirmed_trigger": confirmed, "early_trigger": {"type": "bearish_choch", "index": "2026-01-01 09:45:00"}},
        )

    def _analysis_data(self, candidate, fvgs=None):
        output = ScenarioScannerOutput(
            best_long_scenario=None,
            best_short_scenario=candidate,
            selected_scenario=candidate,
            selected_direction="SHORT",
            signal_allowed=False,
            scenario_valid=False,
            reason="candidate_fvg_not_created",
        )
        return {
            "scenario_scan": output,
            "direction": "bearish",
            "fvg_candidates": fvgs or [],
            "active_fvg": None,
            "research_15m_candles": [],
            "trend_data": {},
            "liquidity_map": None,
            "last_closed_15m": None,
            "market_data_timestamps": {},
        }

    def test_fvg_diagnostic_json_serialization(self):
        fvg = {
            "type": "bearish",
            "top": 10.0,
            "bottom": 9.5,
            "end_index": pd.Timestamp("2026-01-01 10:15:00"),
            "quality_score": 76,
            "detected": True,
            "invalidated": True,
            "invalidation_reason": "price_closed_through_fvg",
            "invalidated_at": pd.Timestamp("2026-01-01 10:30:00"),
            "invalidation_price": 10.2,
            "invalidation_boundary": 10.0,
            "invalidation_operator": "close > top",
            "overlap_percent": 100,
        }

        snapshot = analyzer._fvg_diagnostic_snapshot(fvg)
        encoded = json.dumps(snapshot, sort_keys=True)

        self.assertIn("price_closed_through_fvg", encoded)
        self.assertEqual(snapshot["gap_size"], 0.5)
        self.assertFalse(snapshot["valid"])

    def test_match_trace_uses_production_matcher(self):
        fvg = {
            "type": "bearish",
            "end_index": "2026-01-01 10:15:00",
            "source_candidate_id": "C1",
            "source_confirmed_trigger_index": "2026-01-01 10:00:00",
        }

        boolean_result = analyzer._fvg_matches_state_machine_scenario(
            fvg,
            "bearish",
            expected_candidate_id="C1",
            confirmed_trigger_index=analyzer._event_sort_key("2026-01-01 10:00:00"),
            confirmed_trigger_id=None,
        )
        diagnostic_result = analyzer._fvg_matches_state_machine_scenario(
            fvg,
            "bearish",
            expected_candidate_id="C1",
            confirmed_trigger_index=analyzer._event_sort_key("2026-01-01 10:00:00"),
            confirmed_trigger_id=None,
            return_diagnostics=True,
        )

        self.assertEqual(boolean_result, diagnostic_result["matched"])
        self.assertTrue(diagnostic_result["checks"]["accepted"])

    def test_invalidated_fvg_contains_exact_reason(self):
        index = pd.date_range("2026-01-01 10:00:00", periods=4, freq="15min")
        df = pd.DataFrame(
            {
                "open": [10.0, 9.8, 9.2, 10.2],
                "high": [10.0, 9.9, 9.3, 10.3],
                "low": [9.8, 9.4, 9.0, 10.0],
                "close": [9.9, 9.5, 9.1, 10.2],
                "volume": [1000, 1100, 1200, 1300],
            },
            index=index,
        )

        fvg = next(item for item in find_fvg(df, min_size_atr_ratio=0.1) if item.type == "bearish")

        self.assertTrue(fvg.invalidated)
        self.assertEqual(fvg.invalidation_reason, "price_closed_through_fvg")
        self.assertEqual(fvg.invalidation_operator, "close > top")
        self.assertEqual(str(fvg.invalidated_at), "2026-01-01 10:45:00")

    def test_opposite_trigger_is_explicitly_marked(self):
        candidate = self._candidate(status="invalidated", reason="opposite_confirmed_bos")

        diagnostics = analyzer._trigger_research_diagnostics(candidate, self._analysis_data(candidate))

        self.assertEqual(diagnostics["candidate_invalidated_reason"], "opposite_confirmed_bos")
        self.assertEqual(diagnostics["candidate_invalidated_reason_normalized"], "opposite_confirmed_trigger")
        self.assertTrue(diagnostics["opposite_trigger_invalidation"])

    def test_trace_candle_window_is_limited(self):
        idx = pd.date_range("2026-01-01 00:00:00", periods=100, freq="15min")
        df = pd.DataFrame(
            {"open": range(100), "high": range(100), "low": range(100), "close": range(100), "volume": range(100)},
            index=idx,
        )

        candles = analyzer._research_candles_for_candidate(df, self._candidate(), max_candles=12)

        self.assertLessEqual(len(candles), 12)
        self.assertTrue(all(candle["closed"] for candle in candles))

    def test_feature_flag_off_does_not_change_journal_payload(self):
        candidate = self._candidate()
        with patch.object(analyzer, "ENABLE_SCENARIO_RESEARCH_TRACE", False):
            record = analyzer._build_scan_journal_record(
                "run-1",
                "2026-01-01T10:15:00Z",
                "ADAUSDT",
                {},
                {"diagnostics": {}, "breakdown": {}, "total_score": 69},
                self._analysis_data(candidate),
                {},
            )

        self.assertNotIn("scenario_research_trace", record)

    def test_journal_row_size_protection(self):
        candidate = self._candidate()
        fvgs = [
            {"type": "bearish", "top": 10 + i, "bottom": 9 + i, "end_index": f"2026-01-01 10:{15 + i:02d}:00", "detected": True}
            for i in range(20)
        ]
        with patch.object(analyzer, "SCENARIO_RESEARCH_TRACE_MAX_FVGS", 3):
            trace = analyzer._build_scenario_research_trace("ADAUSDT", "2026-01-01T10:15:00Z", {}, self._analysis_data(candidate, fvgs))

        self.assertEqual(len(trace["fvg_diagnostics"]["candidates"]), 3)
        self.assertLess(len(json.dumps(trace)), 10000)

    def test_old_journal_row_without_diagnostics_is_supported(self):
        candidate = {"research_traces": []}

        self.assertEqual(latest_research_trace(candidate), {})

    def test_new_journal_row_allows_fvg_geometry(self):
        candidate = {"candidate_id": "C1", "symbol": "ADAUSDT"}
        trace = {
            "fvg_diagnostics": {
                "candidates": [
                    {
                        "fvg_id": "F1",
                        "direction": "bearish",
                        "created_at": "2026-01-01 10:15:00",
                        "upper": 10.0,
                        "lower": 9.5,
                        "gap_size": 0.5,
                        "invalidated": True,
                        "invalidation_reason": "price_closed_through_fvg",
                    }
                ]
            },
            "fvg_match_trace": [{"fvg_id": "F1", "accepted": False, "rejection_reasons": ["price_closed_through_fvg"]}],
        }

        fvg_rows, trace_rows = fvg_rows_from_research_trace(candidate, trace)

        self.assertEqual(fvg_rows[0]["top"], 10.0)
        self.assertEqual(fvg_rows[0]["bottom"], 9.5)
        self.assertEqual(trace_rows[0]["first_rejection_reason"], "price_closed_through_fvg")


if __name__ == "__main__":
    unittest.main()
