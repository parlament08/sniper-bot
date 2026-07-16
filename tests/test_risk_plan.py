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
        self.assertEqual(plan.target_model, "valid_liquidity_target")
        self.assertEqual(plan.target_1_info["type"], "buy_side")
        self.assertEqual(plan.target_1_info["strength"], 80)
        self.assertEqual(plan.target_1_info["freshness"], 10)
        self.assertGreaterEqual(plan.rr_to_target_1, 2.0)

    def test_close_liquidity_is_obstacle_not_target(self):
        liquidity = LiquidityMap(
            levels=[],
            nearest_buy_side=self._level("buy_side", 102.0),
            nearest_sell_side=None,
            strongest_buy_side=self._level("old_high", 118.0),
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
        self.assertEqual(plan.risk_geometry, "blocked_by_near_obstacle")
        self.assertEqual(plan.nearest_obstacle["price"], 102.0)
        self.assertEqual(plan.target_1, 118.0)
        self.assertIn("blocked_by_near_obstacle", plan.reason)

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
        self.assertEqual(plan.target_model, "none")
        self.assertIsNone(plan.target_1)
        self.assertEqual(plan.risk_geometry, "no_valid_target")
        self.assertIn("no valid liquidity target", plan.reason)

    def test_before_entry_model_risk_plan_is_not_available_without_999_sentinel(self):
        liquidity = LiquidityMap(
            levels=[],
            nearest_buy_side=self._level("buy_side", 112.0),
            nearest_sell_side=None,
            strongest_buy_side=self._level("old_high", 118.0),
            strongest_sell_side=None,
        )

        plan = build_risk_plan(
            direction="LONG",
            current_price=101.0,
            atr=2.0,
            liquidity_map=liquidity,
            fvg_data=[],
            fvg_test_data=None,
            sfp_data={"type": "bullish_sfp", "level": 98.0},
            structure_data={"type": "bullish_bos", "level": 100.0},
        )

        self.assertIsNotNone(plan)
        self.assertFalse(plan.valid)
        self.assertEqual(plan.risk_plan_status, "not_available")
        self.assertEqual(plan.reason, "entry_model_not_formed")
        self.assertIsNone(plan.entry)
        self.assertIsNone(plan.entry_distance_from_poi_atr)
        self.assertIsNotNone(plan.preliminary_risk)
        self.assertIsNone(plan.preliminary_risk["feasible"])

    def test_created_fvg_before_retest_builds_tentative_plan(self):
        liquidity = LiquidityMap(
            levels=[],
            nearest_buy_side=self._level("buy_side", 112.0),
            nearest_sell_side=None,
            strongest_buy_side=self._level("old_high", 118.0),
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
                "tested": False,
                "invalidated": False,
                "end_index": 5,
            }],
            fvg_test_data=None,
            sfp_data={"type": "bullish_sfp", "level": 98.0},
        )

        self.assertEqual(plan.risk_plan_status, "tentative_plan")
        self.assertIsNotNone(plan.entry)
        self.assertIsNotNone(plan.entry_distance_from_poi_atr)

    def test_candidate_fvg_gates_prevent_execution_plan_until_displacement(self):
        liquidity = LiquidityMap(
            levels=[],
            nearest_buy_side=self._level("buy_side", 112.0),
            nearest_sell_side=None,
            strongest_buy_side=self._level("old_high", 118.0),
            strongest_sell_side=None,
        )
        base_args = {
            "direction": "LONG",
            "current_price": 101.0,
            "atr": 2.0,
            "liquidity_map": liquidity,
            "fvg_data": [{
                "type": "bullish",
                "top": 102.0,
                "bottom": 100.0,
                "tested": True,
                "invalidated": False,
                "end_index": 5,
            }],
            "fvg_test_data": {"index": 6},
            "sfp_data": {"type": "bullish_sfp", "level": 98.0},
            "source_candidate_id": "LONG_SFP_1",
        }

        not_created = build_risk_plan(**base_args, candidate_fvg_created=False)
        no_retest = build_risk_plan(**base_args, candidate_fvg_created=True, candidate_fvg_retested=False)
        no_displacement = build_risk_plan(
            **base_args,
            candidate_fvg_created=True,
            candidate_fvg_retested=True,
            post_retest_displacement_confirmed=False,
        )
        execution = build_risk_plan(
            **base_args,
            candidate_fvg_created=True,
            candidate_fvg_retested=True,
            post_retest_displacement_confirmed=True,
        )

        self.assertEqual(not_created.risk_plan_status, "not_available")
        self.assertEqual(not_created.reason, "candidate_fvg_not_created")
        self.assertEqual(no_retest.risk_plan_status, "tentative_plan")
        self.assertEqual(no_displacement.risk_plan_status, "tentative_plan")
        self.assertEqual(execution.risk_plan_status, "execution_plan")
        self.assertEqual(execution.source_candidate_id, "LONG_SFP_1")

    def test_stop_has_atr_buffer_and_does_not_equal_entry_or_invalidation(self):
        liquidity = LiquidityMap(
            levels=[],
            nearest_buy_side=self._level("buy_side", 120.0),
            nearest_sell_side=None,
            strongest_buy_side=self._level("old_high", 125.0),
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
            sfp_data={"type": "bullish_sfp", "level": 100.9},
        )

        self.assertNotEqual(plan.stop_loss, plan.entry)
        self.assertNotEqual(plan.stop_loss, plan.invalidation_level)
        self.assertGreater(plan.risk_per_unit, 0)
        self.assertGreater(plan.stop_distance_percent, 0)


if __name__ == "__main__":
    unittest.main()
