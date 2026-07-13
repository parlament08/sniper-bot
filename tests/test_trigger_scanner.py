import unittest

from core.trigger_scanner import scan_post_anchor_trigger


class TriggerScannerTest(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
