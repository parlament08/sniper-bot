import unittest

from core.liquidity import LiquidityLevel, LiquidityMap
from core.risk_plan import build_risk_plan


class RiskPlanTest(unittest.TestCase):
    def _level(self, level_type, price, strength=80):
        return LiquidityLevel(
            type=level_type,
            price=price,
            strength=strength,
            touches=2,
            age_bars=10,
            distance_percent=1.0,
            distance_atr=1.0,
            swept=False,
            swept_at=None,
            source_index=1,
            description="test liquidity",
        )

    def test_long_uses_fvg_entry_structural_stop_and_buy_side_target(self):
        liquidity = LiquidityMap(
            levels=[],
            nearest_buy_side=self._level("buy_side", 112.0),
            nearest_sell_side=self._level("sell_side", 95.0),
            strongest_buy_side=self._level("old_high", 118.0),
            strongest_sell_side=self._level("old_low", 90.0),
        )

        plan = build_risk_plan(
            direction="LONG",
            current_price=101.0,
            atr=2.0,
            liquidity_map=liquidity,
            fvg_data=[{
                "type": "bullish",
                "top": 102.0,
                "bottom": 100.0,
                "tested": True,
                "invalidated": False,
                "end_index": 5,
            }],
            fvg_test_data={"index": 6},
            sfp_data={"type": "bullish_sfp", "level": 98.0},
        )

        self.assertTrue(plan.valid)
        self.assertEqual(plan.entry_model, "fvg_midpoint")
        self.assertIn("structural_invalidation", plan.stop_model)
        self.assertEqual(plan.target_model, "nearest_liquidity")
        self.assertGreaterEqual(plan.rr_to_target_1, 2.0)

    def test_close_liquidity_target_blocks_a_plus_quality(self):
        liquidity = LiquidityMap(
            levels=[],
            nearest_buy_side=self._level("buy_side", 102.0),
            nearest_sell_side=None,
            strongest_buy_side=self._level("old_high", 103.0),
            strongest_sell_side=None,
        )

        plan = build_risk_plan(
            direction="LONG",
            current_price=101.0,
            atr=2.0,
            liquidity_map=liquidity,
            fvg_data=[{
                "type": "bullish",
                "top": 102.0,
                "bottom": 100.0,
                "tested": True,
                "invalidated": False,
                "end_index": 5,
            }],
            fvg_test_data={"index": 6},
            sfp_data={"type": "bullish_sfp", "level": 98.0},
        )

        self.assertFalse(plan.valid)
        self.assertIn("RR to target 1 below minimum", plan.reason)

    def test_late_entry_is_invalid_even_with_good_target(self):
        liquidity = LiquidityMap(
            levels=[],
            nearest_buy_side=self._level("buy_side", 120.0),
            nearest_sell_side=None,
            strongest_buy_side=self._level("old_high", 125.0),
            strongest_sell_side=None,
        )

        plan = build_risk_plan(
            direction="LONG",
            current_price=105.5,
            atr=2.0,
            liquidity_map=liquidity,
            fvg_data=[{
                "type": "bullish",
                "top": 102.0,
                "bottom": 100.0,
                "tested": True,
                "invalidated": False,
                "end_index": 5,
            }],
            fvg_test_data={"index": 6},
            sfp_data={"type": "bullish_sfp", "level": 98.0},
        )

        self.assertFalse(plan.valid)
        self.assertTrue(plan.late_entry)
        self.assertIn("late entry", plan.reason)

    def test_missing_liquidity_target_keeps_plan_invalid(self):
        plan = build_risk_plan(
            direction="SHORT",
            current_price=99.0,
            atr=2.0,
            liquidity_map=LiquidityMap([], None, None, None, None),
            fvg_data=[{
                "type": "bearish",
                "top": 100.0,
                "bottom": 98.0,
                "tested": True,
                "invalidated": False,
                "end_index": 5,
            }],
            fvg_test_data={"index": 6},
            sfp_data={"type": "bearish_sfp", "level": 102.0},
        )

        self.assertFalse(plan.valid)
        self.assertEqual(plan.target_model, "3R_fallback_no_liquidity")
        self.assertIn("no logical liquidity target", plan.reason)


if __name__ == "__main__":
    unittest.main()
