import unittest

from research.analyze_shadow_candidates import compute_outcome


class ShadowCandidateReportTest(unittest.TestCase):
    def test_entry_is_not_counted_until_future_candle_reaches_entry(self):
        first = {
            "timestamp": "2026-01-01T10:00:00+00:00",
            "shadow_direction": "LONG",
            "features": {
                "shadow_candidate": {
                    "entry": 100.0,
                    "stop_loss": 95.0,
                    "target_1": 110.0,
                }
            },
        }
        future_rows = [
            {"timestamp": "2026-01-01T10:15:00+00:00", "market_high_15m": 99.5, "market_low_15m": 98.5},
            {"timestamp": "2026-01-01T10:30:00+00:00", "market_high_15m": 101.0, "market_low_15m": 99.0},
        ]

        outcome = compute_outcome(first, future_rows)

        self.assertTrue(outcome["entry_filled"])
        self.assertEqual(outcome["entry_filled_at"], "2026-01-01T10:30:00+00:00")
        self.assertEqual(outcome["max_adverse_excursion_r"], 0.2)

    def test_outcome_uses_only_future_rows_given_to_analyzer(self):
        first = {
            "timestamp": "2026-01-01T10:00:00+00:00",
            "shadow_direction": "SHORT",
            "features": {
                "shadow_candidate": {
                    "entry": 100.0,
                    "stop_loss": 105.0,
                    "target_1": 90.0,
                }
            },
        }
        pre_entry_like_past_row_is_not_passed = []

        outcome = compute_outcome(first, pre_entry_like_past_row_is_not_passed)

        self.assertFalse(outcome["entry_filled"])
        self.assertIsNone(outcome["max_favorable_excursion_r"])


if __name__ == "__main__":
    unittest.main()
