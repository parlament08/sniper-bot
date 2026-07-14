import unittest

import pandas as pd

from core.trigger_scanner import find_confirmed_trigger_after_early, scan_post_anchor_trigger


class TriggerScannerTest(unittest.TestCase):
    def _early(self, trigger_type, index=105, quality=68):
        return {
            "type": trigger_type,
            "index": index,
            "quality_score": quality,
            "body_ratio": 0.55,
            "displacement_ratio": 0.75,
            "rvol": 1.4,
        }

    def _df(self, rows):
        return pd.DataFrame(rows).set_index("index")

    def _swings(self, highs=None, lows=None):
        high_rows = highs or []
        low_rows = lows or []
        return (
            pd.DataFrame(high_rows).set_index("index") if high_rows else pd.DataFrame(columns=["high"]),
            pd.DataFrame(low_rows).set_index("index") if low_rows else pd.DataFrame(columns=["low"]),
        )

    def test_long_trigger_after_sfp_confirms(self):
        result = scan_post_anchor_trigger(
            expected_direction="LONG",
            sfp={"type": "bullish_sfp", "index": 100},
            long_trigger_candidate={"type": "bullish_bos", "index": 110, "quality_score": 88},
        )

        self.assertTrue(result.trigger_confirmed)
        self.assertEqual(result.selected_trigger["type"], "bullish_bos")
        self.assertIsNone(result.rejected_reason)

    def test_long_trigger_before_sfp_is_saved_but_not_selected(self):
        result = scan_post_anchor_trigger(
            expected_direction="LONG",
            sfp={"type": "bullish_sfp", "index": 100},
            long_trigger_candidate={"type": "bullish_bos", "index": 90, "quality_score": 97},
        )

        self.assertFalse(result.trigger_confirmed)
        self.assertIsNone(result.selected_trigger)
        self.assertEqual(result.pre_sfp_trigger["type"], "bullish_bos")
        self.assertEqual(result.rejected_reason, "trigger_before_sfp")

    def test_long_only_opposite_after_sfp_waits_for_bullish_trigger(self):
        result = scan_post_anchor_trigger(
            expected_direction="LONG",
            sfp={"type": "bullish_sfp", "index": 100},
            short_trigger_candidate={"type": "bearish_bos", "index": 110, "quality_score": 81},
        )

        self.assertFalse(result.trigger_confirmed)
        self.assertIsNone(result.selected_trigger)
        self.assertEqual(result.opposite_trigger["type"], "bearish_bos")
        self.assertEqual(result.rejected_reason, "no_bullish_trigger_after_sfp_or_poi")
        self.assertEqual(result.waiting_for, "bullish CHOCH/BOS after SFP/POI")

    def test_short_trigger_after_sfp_confirms(self):
        result = scan_post_anchor_trigger(
            expected_direction="SHORT",
            sfp={"type": "bearish_sfp", "index": 100},
            short_trigger_candidate={"type": "bearish_bos", "index": 108, "quality_score": 90},
        )

        self.assertTrue(result.trigger_confirmed)
        self.assertEqual(result.selected_trigger["type"], "bearish_bos")
        self.assertIsNone(result.rejected_reason)

    def test_short_bullish_trigger_after_sfp_is_opposite(self):
        result = scan_post_anchor_trigger(
            expected_direction="SHORT",
            sfp={"type": "bearish_sfp", "index": 100},
            long_trigger_candidate={"type": "bullish_bos", "index": 105, "quality_score": 91},
        )

        self.assertFalse(result.trigger_confirmed)
        self.assertIsNone(result.selected_trigger)
        self.assertEqual(result.opposite_trigger["type"], "bullish_bos")
        self.assertEqual(result.rejected_reason, "no_bearish_trigger_after_sfp_or_poi")
        self.assertEqual(result.waiting_for, "bearish CHOCH/BOS after SFP/POI")

    def test_neutral_skips_trigger_selection_and_keeps_candidate(self):
        result = scan_post_anchor_trigger(
            expected_direction="NEUTRAL",
            short_trigger_candidate={"type": "bearish_choch", "index": 100, "quality_score": 84},
        )

        self.assertFalse(result.trigger_confirmed)
        self.assertIsNone(result.selected_trigger)
        self.assertEqual(result.candidate_trigger["type"], "bearish_choch")
        self.assertIsNone(result.opposite_trigger)
        self.assertEqual(result.rejected_reason, "no_trade_direction")

    def test_trigger_after_confirmation_window_is_rejected(self):
        result = scan_post_anchor_trigger(
            expected_direction="LONG",
            sfp={"type": "bullish_sfp", "index": 100},
            long_trigger_candidate={"type": "bullish_bos", "index": 130, "quality_score": 90},
            max_bars_after_sfp=24,
        )

        self.assertFalse(result.trigger_confirmed)
        self.assertEqual(result.rejected_reason, "trigger_outside_confirmation_window")

    def test_poi_anchor_used_when_sfp_is_missing(self):
        result = scan_post_anchor_trigger(
            expected_direction="SHORT",
            poi={"index": 200},
            short_trigger_candidate={"type": "bearish_choch", "index": 205, "quality_score": 84},
        )

        self.assertTrue(result.trigger_confirmed)
        self.assertEqual(result.selected_trigger["type"], "bearish_choch")
        self.assertEqual(result.post_poi_trigger["type"], "bearish_choch")
        self.assertEqual(result.anchor_index, 200)

    def test_ape_like_pre_sfp_expected_and_post_sfp_opposite_does_not_confirm(self):
        result = scan_post_anchor_trigger(
            expected_direction="LONG",
            sfp={"type": "bullish_sfp", "index": 100},
            long_trigger_candidate={"type": "bullish_bos", "index": 90, "quality_score": 97},
            short_trigger_candidate={"type": "bearish_bos", "index": 110, "quality_score": 93},
        )

        self.assertFalse(result.trigger_confirmed)
        self.assertIsNone(result.selected_trigger)
        self.assertEqual(result.pre_sfp_trigger["type"], "bullish_bos")
        self.assertEqual(result.candidate_trigger["type"], "bullish_bos")
        self.assertEqual(result.opposite_trigger["type"], "bearish_bos")
        self.assertEqual(result.rejected_reason, "no_bullish_trigger_after_sfp_or_poi")

    def test_long_early_trigger_after_sfp_waits_for_confirmed_trigger(self):
        result = scan_post_anchor_trigger(
            expected_direction="LONG",
            sfp={"type": "bullish_sfp", "index": 100},
            trigger_candidates=[self._early("bullish_early_choch")],
        )

        self.assertTrue(result.early_trigger_confirmed)
        self.assertFalse(result.trigger_confirmed)
        self.assertEqual(result.early_trigger["type"], "bullish_early_choch")
        self.assertEqual(result.early_trigger["trigger_stage"], "early")
        self.assertIsNone(result.selected_trigger)
        self.assertEqual(result.rejected_reason, "confirmed_trigger_missing")
        self.assertEqual(result.waiting_for, "confirmed bullish BOS after early CHOCH")

    def test_long_confirmed_trigger_beats_early_trigger(self):
        result = scan_post_anchor_trigger(
            expected_direction="LONG",
            sfp={"type": "bullish_sfp", "index": 100},
            trigger_candidates=[
                self._early("bullish_early_choch", index=105),
                {"type": "bullish_bos", "index": 112, "quality_score": 86},
            ],
        )

        self.assertTrue(result.trigger_confirmed)
        self.assertTrue(result.early_trigger_confirmed)
        self.assertEqual(result.confirmed_trigger["type"], "bullish_bos")
        self.assertEqual(result.selected_trigger["type"], "bullish_bos")
        self.assertEqual(result.early_trigger["type"], "bullish_early_choch")

    def test_confirmed_trigger_must_be_after_early_trigger_when_early_exists(self):
        result = scan_post_anchor_trigger(
            expected_direction="LONG",
            sfp={"type": "bullish_sfp", "index": 100},
            trigger_candidates=[
                {"type": "bullish_bos", "index": 104, "quality_score": 90},
                self._early("bullish_early_choch", index=108),
            ],
        )

        self.assertTrue(result.early_trigger_confirmed)
        self.assertFalse(result.trigger_confirmed)
        self.assertIsNone(result.selected_trigger)
        self.assertEqual(result.early_trigger["index"], 108)
        self.assertEqual(result.candidate_trigger["index"], 108)
        self.assertEqual(result.rejected_reason, "confirmed_trigger_missing")
        self.assertEqual(result.waiting_for, "confirmed bullish BOS after early CHOCH")

    def test_pre_early_confirmed_candidate_is_historical_debug_only(self):
        result = scan_post_anchor_trigger(
            expected_direction="LONG",
            sfp={"type": "bullish_sfp", "index": 100},
            trigger_candidates=[
                {"type": "bullish_choch", "index": 104, "quality_score": 92},
                self._early("bullish_early_choch", index=108, quality=88),
            ],
        )

        self.assertTrue(result.early_trigger_confirmed)
        self.assertFalse(result.trigger_confirmed)
        self.assertEqual(result.confirmed_trigger_debug["candidate_bos_count"], 0)
        self.assertEqual(result.confirmed_trigger_debug["candidate_choch_count"], 0)
        self.assertEqual(result.confirmed_trigger_debug["rejected_candidates"], [])
        self.assertEqual(result.confirmed_trigger_debug["historical_rejected_candidates"][0]["index"], "104")
        self.assertEqual(
            result.confirmed_trigger_debug["historical_rejected_candidates"][0]["rejected_reason"],
            "before_early_trigger",
        )
        self.assertEqual(result.confirmed_trigger_debug["final_reason"], "no_confirmed_bos_after_early_trigger")

    def test_confirmed_trigger_after_early_trigger_confirms_follow_up(self):
        result = scan_post_anchor_trigger(
            expected_direction="SHORT",
            sfp={"type": "bearish_sfp", "index": 100},
            trigger_candidates=[
                self._early("bearish_early_choch", index=106),
                {"type": "bearish_bos", "index": 112, "quality_score": 88},
            ],
        )

        self.assertTrue(result.early_trigger_confirmed)
        self.assertTrue(result.trigger_confirmed)
        self.assertEqual(result.early_trigger["index"], 106)
        self.assertEqual(result.confirmed_trigger["index"], 112)
        self.assertEqual(result.selected_trigger["type"], "bearish_bos")

    def test_confirmed_trigger_after_early_quality_below_min_has_debug(self):
        result = scan_post_anchor_trigger(
            expected_direction="LONG",
            sfp={"type": "bullish_sfp", "index": 100},
            trigger_candidates=[
                self._early("bullish_early_choch", index=106),
                {"type": "bullish_bos", "index": 112, "quality_score": 62},
            ],
        )

        self.assertTrue(result.early_trigger_confirmed)
        self.assertFalse(result.trigger_confirmed)
        self.assertEqual(result.rejected_reason, "confirmed_trigger_missing")
        self.assertEqual(result.confirmed_trigger_debug["candidate_bos_count"], 1)
        self.assertEqual(result.confirmed_trigger_debug["final_reason"], "quality_below_min")
        self.assertEqual(result.confirmed_trigger_debug["rejected_candidates"][0]["rejected_reason"], "quality_below_min")

    def test_no_confirmed_trigger_after_early_has_zero_count_debug(self):
        result = scan_post_anchor_trigger(
            expected_direction="LONG",
            sfp={"type": "bullish_sfp", "index": 100},
            trigger_candidates=[self._early("bullish_early_choch", index=106)],
        )

        self.assertTrue(result.early_trigger_confirmed)
        self.assertFalse(result.trigger_confirmed)
        self.assertEqual(result.confirmed_trigger_debug["candidate_bos_count"], 0)
        self.assertEqual(result.confirmed_trigger_debug["candidate_choch_count"], 0)
        self.assertEqual(result.confirmed_trigger_debug["final_reason"], "no_confirmed_bos_after_early_trigger")

    def test_short_early_trigger_after_sfp_waits_for_confirmed_trigger(self):
        result = scan_post_anchor_trigger(
            expected_direction="SHORT",
            sfp={"type": "bearish_sfp", "index": 100},
            trigger_candidates=[self._early("bearish_mss", index=106, quality=71)],
        )

        self.assertTrue(result.early_trigger_confirmed)
        self.assertFalse(result.trigger_confirmed)
        self.assertEqual(result.early_trigger["type"], "bearish_mss")
        self.assertEqual(result.waiting_for, "confirmed bearish BOS after early CHOCH")

    def test_early_trigger_before_sfp_is_ignored(self):
        result = scan_post_anchor_trigger(
            expected_direction="LONG",
            sfp={"type": "bullish_sfp", "index": 100},
            trigger_candidates=[self._early("bullish_early_choch", index=90)],
        )

        self.assertFalse(result.early_trigger_confirmed)
        self.assertFalse(result.trigger_confirmed)
        self.assertIsNone(result.early_trigger)
        self.assertEqual(result.rejected_reason, "no_bullish_trigger_after_sfp_or_poi")

    def test_opposite_early_trigger_after_sfp_is_opposite(self):
        result = scan_post_anchor_trigger(
            expected_direction="LONG",
            sfp={"type": "bullish_sfp", "index": 100},
            trigger_candidates=[self._early("bearish_mss", index=105)],
        )

        self.assertFalse(result.early_trigger_confirmed)
        self.assertFalse(result.trigger_confirmed)
        self.assertEqual(result.opposite_trigger["type"], "bearish_mss")

    def test_confirmed_bullish_bos_generated_after_early_trigger(self):
        df = self._df([
            {"index": 100, "open": 10.0, "high": 10.2, "low": 9.8, "close": 10.0, "atr": 1.0, "rvol": 1.0},
            {"index": 110, "open": 10.0, "high": 10.4, "low": 9.9, "close": 10.3, "atr": 1.0, "rvol": 1.4},
            {"index": 115, "open": 10.2, "high": 10.5, "low": 10.0, "close": 10.2, "atr": 1.0, "rvol": 1.0},
            {"index": 120, "open": 10.4, "high": 12.0, "low": 10.3, "close": 11.8, "atr": 1.0, "rvol": 2.0},
        ])
        result = scan_post_anchor_trigger(
            expected_direction="LONG",
            sfp={"type": "bullish_sfp", "index": 100},
            trigger_candidates=[self._early("bullish_early_choch", index=110, quality=88)],
            df_15m_closed=df,
            swing_points=self._swings(highs=[{"index": 115, "high": 10.5}]),
        )

        self.assertTrue(result.trigger_confirmed)
        self.assertEqual(result.confirmed_trigger["type"], "bullish_bos")
        self.assertEqual(result.confirmed_trigger["index"], 120)
        self.assertEqual(result.confirmed_trigger["trigger_stage"], "confirmed")
        self.assertGreaterEqual(result.confirmed_trigger["quality_score"], 70)
        self.assertTrue(result.confirmed_trigger_debug["generator_called"])
        self.assertEqual(result.confirmed_trigger_debug["checked_candles"][-1]["candidate_created"], True)

    def test_confirmed_bearish_bos_generated_after_early_trigger(self):
        df = self._df([
            {"index": 100, "open": 10.0, "high": 10.2, "low": 9.8, "close": 10.0, "atr": 1.0, "rvol": 1.0},
            {"index": 110, "open": 10.0, "high": 10.1, "low": 9.6, "close": 9.7, "atr": 1.0, "rvol": 1.4},
            {"index": 115, "open": 9.8, "high": 10.0, "low": 9.5, "close": 9.8, "atr": 1.0, "rvol": 1.0},
            {"index": 120, "open": 9.6, "high": 9.7, "low": 8.0, "close": 8.2, "atr": 1.0, "rvol": 2.0},
        ])
        result = scan_post_anchor_trigger(
            expected_direction="SHORT",
            sfp={"type": "bearish_sfp", "index": 100},
            trigger_candidates=[self._early("bearish_early_choch", index=110, quality=88)],
            df_15m_closed=df,
            swing_points=self._swings(lows=[{"index": 115, "low": 9.5}]),
        )

        self.assertTrue(result.trigger_confirmed)
        self.assertEqual(result.confirmed_trigger["type"], "bearish_bos")
        self.assertEqual(result.confirmed_trigger["index"], 120)

    def test_generated_confirmed_bos_before_early_trigger_is_ignored(self):
        df = self._df([
            {"index": 100, "open": 10.0, "high": 10.2, "low": 9.8, "close": 10.0, "atr": 1.0, "rvol": 1.0},
            {"index": 104, "open": 10.1, "high": 12.0, "low": 10.0, "close": 11.8, "atr": 1.0, "rvol": 2.0},
            {"index": 110, "open": 10.0, "high": 10.4, "low": 9.9, "close": 10.3, "atr": 1.0, "rvol": 1.4},
        ])
        result = scan_post_anchor_trigger(
            expected_direction="LONG",
            sfp={"type": "bullish_sfp", "index": 100},
            trigger_candidates=[self._early("bullish_early_choch", index=110, quality=88)],
            df_15m_closed=df,
            swing_points=self._swings(highs=[{"index": 102, "high": 10.5}]),
        )

        self.assertTrue(result.early_trigger_confirmed)
        self.assertFalse(result.trigger_confirmed)
        self.assertIsNone(result.confirmed_trigger)

    def test_generated_low_quality_confirmed_bos_rejected_with_reason(self):
        df = self._df([
            {"index": 100, "open": 10.0, "high": 10.2, "low": 9.8, "close": 10.0, "atr": 1.0, "rvol": 1.0},
            {"index": 110, "open": 10.0, "high": 10.4, "low": 9.9, "close": 10.3, "atr": 1.0, "rvol": 1.4},
            {"index": 115, "open": 10.2, "high": 10.5, "low": 10.0, "close": 10.2, "atr": 1.0, "rvol": 1.0},
            {"index": 120, "open": 10.55, "high": 10.8, "low": 10.4, "close": 10.65, "atr": 1.0, "rvol": 1.0},
        ])
        result = scan_post_anchor_trigger(
            expected_direction="LONG",
            sfp={"type": "bullish_sfp", "index": 100},
            trigger_candidates=[self._early("bullish_early_choch", index=110, quality=88)],
            df_15m_closed=df,
            swing_points=self._swings(highs=[{"index": 115, "high": 10.5}]),
        )

        self.assertFalse(result.trigger_confirmed)
        self.assertTrue(result.confirmed_trigger_debug["generator_called"])
        self.assertEqual(result.confirmed_trigger_debug["candidate_bos_count"], 1)
        self.assertEqual(result.confirmed_trigger_debug["rejected_candidates"][0]["rejected_reason"], "quality_below_min")
        self.assertEqual(result.confirmed_trigger_debug["checked_candles"][-1]["rejected_reason"], "quality_below_min")

    def test_no_candles_after_early_trigger_debug_reason(self):
        df = self._df([
            {"index": 100, "open": 10.0, "high": 10.2, "low": 9.8, "close": 10.0, "atr": 1.0, "rvol": 1.0},
            {"index": 110, "open": 10.0, "high": 10.4, "low": 9.9, "close": 10.3, "atr": 1.0, "rvol": 1.4},
        ])
        result = scan_post_anchor_trigger(
            expected_direction="LONG",
            sfp={"type": "bullish_sfp", "index": 100},
            trigger_candidates=[self._early("bullish_early_choch", index=110, quality=88)],
            df_15m_closed=df,
            swing_points=self._swings(highs=[{"index": 105, "high": 10.5}]),
        )

        self.assertFalse(result.trigger_confirmed)
        self.assertTrue(result.confirmed_trigger_debug["generator_called"])
        self.assertEqual(result.confirmed_trigger_debug["candles_after_early"], 0)
        self.assertIsNone(result.confirmed_trigger_debug["first_candle_after_early"])
        self.assertIsNone(result.confirmed_trigger_debug["last_candle_after_early"])
        self.assertEqual(result.confirmed_trigger_debug["final_reason"], "not_enough_candles_after_early_trigger")

    def test_no_candles_after_early_keeps_old_candidate_historical(self):
        df = self._df([
            {"index": 100, "open": 10.0, "high": 10.2, "low": 9.8, "close": 10.0, "atr": 1.0, "rvol": 1.0},
            {"index": 110, "open": 10.0, "high": 10.4, "low": 9.9, "close": 10.3, "atr": 1.0, "rvol": 1.4},
        ])
        result = scan_post_anchor_trigger(
            expected_direction="LONG",
            sfp={"type": "bullish_sfp", "index": 100},
            trigger_candidates=[
                {"type": "bullish_choch", "index": 90, "quality_score": 91},
                self._early("bullish_early_choch", index=110, quality=88),
            ],
            df_15m_closed=df,
            swing_points=self._swings(highs=[{"index": 105, "high": 10.5}]),
        )

        self.assertFalse(result.trigger_confirmed)
        self.assertEqual(result.confirmed_trigger_debug["candles_after_early"], 0)
        self.assertEqual(result.confirmed_trigger_debug["candidate_choch_count"], 0)
        self.assertEqual(result.confirmed_trigger_debug["rejected_candidates"], [])
        self.assertEqual(result.confirmed_trigger_debug["historical_rejected_candidates"][0]["index"], "90")
        self.assertEqual(result.confirmed_trigger_debug["final_reason"], "not_enough_candles_after_early_trigger")

    def test_candles_after_early_without_break_level_debug_reason(self):
        df = self._df([
            {"index": 100, "open": 10.0, "high": 10.2, "low": 9.8, "close": 10.0, "atr": 1.0, "rvol": 1.0},
            {"index": 110, "open": 10.0, "high": 10.4, "low": 9.9, "close": 10.3, "atr": 1.0, "rvol": 1.4},
            {"index": 120, "open": 10.3, "high": 10.8, "low": 10.2, "close": 10.7, "atr": 1.0, "rvol": 1.2},
        ])
        result = scan_post_anchor_trigger(
            expected_direction="LONG",
            sfp={"type": "bullish_sfp", "index": 100},
            trigger_candidates=[self._early("bullish_early_choch", index=110, quality=88)],
            df_15m_closed=df,
            swing_points=self._swings(),
        )

        self.assertFalse(result.trigger_confirmed)
        self.assertTrue(result.confirmed_trigger_debug["generator_called"])
        self.assertEqual(result.confirmed_trigger_debug["candles_after_early"], 1)
        self.assertIsNone(result.confirmed_trigger_debug["break_level"])
        self.assertEqual(result.confirmed_trigger_debug["final_reason"], "no_confirmed_break_level_after_early_trigger")

    def test_candles_after_early_without_close_beyond_level_debug_reason(self):
        df = self._df([
            {"index": 100, "open": 10.0, "high": 10.2, "low": 9.8, "close": 10.0, "atr": 1.0, "rvol": 1.0},
            {"index": 110, "open": 10.0, "high": 10.4, "low": 9.9, "close": 10.3, "atr": 1.0, "rvol": 1.4},
            {"index": 115, "open": 10.2, "high": 10.8, "low": 10.0, "close": 10.2, "atr": 1.0, "rvol": 1.0},
            {"index": 120, "open": 10.3, "high": 10.7, "low": 10.2, "close": 10.6, "atr": 1.0, "rvol": 1.2},
        ])
        result = scan_post_anchor_trigger(
            expected_direction="LONG",
            sfp={"type": "bullish_sfp", "index": 100},
            trigger_candidates=[self._early("bullish_early_choch", index=110, quality=88)],
            df_15m_closed=df,
            swing_points=self._swings(highs=[{"index": 115, "high": 10.8}]),
        )

        self.assertFalse(result.trigger_confirmed)
        self.assertEqual(result.confirmed_trigger_debug["break_level"], 10.8)
        self.assertEqual(result.confirmed_trigger_debug["first_candle_after_early"], "115")
        self.assertEqual(result.confirmed_trigger_debug["last_candle_after_early"], "120")
        self.assertEqual(result.confirmed_trigger_debug["candidate_bos_count"], 0)
        self.assertEqual(result.confirmed_trigger_debug["checked_candles"][-1]["breaks_level"], False)
        self.assertEqual(result.confirmed_trigger_debug["final_reason"], "no_candle_closed_beyond_break_level")

    def test_public_confirmed_trigger_generator_returns_best_valid_candidate(self):
        df = self._df([
            {"index": 110, "open": 10.0, "high": 10.4, "low": 9.9, "close": 10.3, "atr": 1.0, "rvol": 1.4},
            {"index": 115, "open": 10.2, "high": 10.5, "low": 10.0, "close": 10.2, "atr": 1.0, "rvol": 1.0},
            {"index": 120, "open": 10.4, "high": 12.0, "low": 10.3, "close": 11.8, "atr": 1.0, "rvol": 2.0},
        ])
        candidate = find_confirmed_trigger_after_early(
            df,
            "LONG",
            110,
            100,
            self._swings(highs=[{"index": 115, "high": 10.5}]),
            atr_series=None,
        )

        self.assertIsNotNone(candidate)
        self.assertEqual(candidate["type"], "bullish_bos")


if __name__ == "__main__":
    unittest.main()
