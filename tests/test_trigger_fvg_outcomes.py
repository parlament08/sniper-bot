import unittest

from research.analyze_trigger_fvg_outcomes import (
    double_filter_classification,
    future_outcome,
    match_trace,
)


class TriggerFvgOutcomeTest(unittest.TestCase):
    def _candidate(self, direction="short"):
        return {
            "candidate_id": "C1",
            "direction": direction,
            "confirmed_trigger_timestamp": "2026-01-01 10:00:00",
            "confirmed_trigger": {"event_id": "CONFIRMED_TRIGGER_CONFIRMED:2026-01-01 10:00:00"},
        }

    def test_fvg_match_trace_accepts_after_trigger_when_fields_match(self):
        fvg = {
            "type": "bearish",
            "end_index": "2026-01-01 10:15:00",
            "source_candidate_id": "C1",
            "source_confirmed_trigger_id": "CONFIRMED_TRIGGER_CONFIRMED:2026-01-01 10:00:00",
            "source_confirmed_trigger_index": "2026-01-01 10:00:00",
        }

        trace = match_trace(fvg, self._candidate("short"))

        self.assertTrue(trace["accepted"])
        self.assertIsNone(trace["first_rejection_reason"])

    def test_fvg_before_trigger_is_rejected_first(self):
        fvg = {"type": "bearish", "end_index": "2026-01-01 09:45:00"}

        trace = match_trace(fvg, self._candidate("short"))

        self.assertFalse(trace["accepted"])
        self.assertEqual(trace["first_rejection_reason"], "created_before_or_at_confirmed_trigger")

    def test_first_rejection_reason_reports_direction_before_later_checks(self):
        fvg = {"type": "bullish", "end_index": "2026-01-01 09:45:00", "invalidated": True}

        trace = match_trace(fvg, self._candidate("short"))

        self.assertEqual(trace["first_rejection_reason"], "direction_mismatch")
        self.assertIn("invalidated", trace["rejection_reasons"])

    def test_missing_binding_fields_are_unknown_not_failure(self):
        fvg = {"type": "bearish", "end_index": "2026-01-01 10:15:00"}

        trace = match_trace(fvg, self._candidate("short"))

        self.assertTrue(trace["accepted"])
        self.assertIsNone(trace["candidate_id_match"])
        self.assertIsNone(trace["source_trigger_id_match"])

    def test_mfe_mae_long(self):
        candles = [
            {"timestamp": __import__("pandas").Timestamp("2026-01-01 10:00:00"), "open": 100, "high": 101, "low": 99, "close": 100, "atr": 2},
            {"timestamp": __import__("pandas").Timestamp("2026-01-01 10:15:00"), "open": 100, "high": 104, "low": 99, "close": 103, "atr": 2},
        ]

        outcome = future_outcome(candles, "2026-01-01 10:00:00", "long")

        self.assertEqual(outcome["4_candles"]["mfe"], 4)
        self.assertEqual(outcome["4_candles"]["mae"], -1)
        self.assertEqual(outcome["4_candles"]["mfe_atr"], 2.0)

    def test_mfe_mae_short(self):
        candles = [
            {"timestamp": __import__("pandas").Timestamp("2026-01-01 10:00:00"), "open": 100, "high": 101, "low": 99, "close": 100, "atr": 2},
            {"timestamp": __import__("pandas").Timestamp("2026-01-01 10:15:00"), "open": 100, "high": 103, "low": 95, "close": 96, "atr": 2},
        ]

        outcome = future_outcome(candles, "2026-01-01 10:00:00", "short")

        self.assertEqual(outcome["4_candles"]["mfe"], 5)
        self.assertEqual(outcome["4_candles"]["mae"], -3)
        self.assertEqual(outcome["4_candles"]["mfe_atr"], 2.5)

    def test_double_filter_requires_complete_hard_fields(self):
        row = {"quality_score": "35", "body_ratio": "0.5"}

        self.assertEqual(double_filter_classification(row), "unknown_missing_hard_fields")

    def test_double_filter_classifies_complete_hard_fields(self):
        row = {
            "quality_score": "65",
            "body_ratio": "0.6",
            "displacement_ratio": "0.9",
            "close_position": "0.7",
            "rvol": "1.6",
        }

        self.assertEqual(double_filter_classification(row), "passed_all_hard_conditions_but_quality_failed")


if __name__ == "__main__":
    unittest.main()
