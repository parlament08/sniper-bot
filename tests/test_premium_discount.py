import unittest

import pandas as pd

from core.premium_discount import PremiumDiscountResult, evaluate_premium_discount


class PremiumDiscountTest(unittest.TestCase):
    def setUp(self):
        self.swing_highs = pd.DataFrame({'high': [120.0]}, index=[10])
        self.swing_lows = pd.DataFrame({'low': [80.0]}, index=[5])

    def test_buy_in_discount_is_valid(self):
        result = evaluate_premium_discount(90.0, self.swing_highs, self.swing_lows)

        self.assertIsInstance(result, PremiumDiscountResult)
        self.assertEqual(result.zone, 'discount')
        self.assertTrue(result.valid_for_buy)
        self.assertFalse(result.valid_for_sell)

    def test_buy_in_premium_is_invalid(self):
        result = evaluate_premium_discount(110.0, self.swing_highs, self.swing_lows)

        self.assertEqual(result.zone, 'premium')
        self.assertFalse(result.valid_for_buy)

    def test_sell_in_premium_is_valid(self):
        result = evaluate_premium_discount(110.0, self.swing_highs, self.swing_lows)

        self.assertEqual(result.zone, 'premium')
        self.assertTrue(result.valid_for_sell)
        self.assertFalse(result.valid_for_buy)

    def test_sell_in_discount_is_invalid(self):
        result = evaluate_premium_discount(90.0, self.swing_highs, self.swing_lows)

        self.assertEqual(result.zone, 'discount')
        self.assertFalse(result.valid_for_sell)

    def test_price_near_equilibrium_returns_equilibrium(self):
        result = evaluate_premium_discount(100.5, self.swing_highs, self.swing_lows)

        self.assertEqual(result.zone, 'equilibrium')
        self.assertFalse(result.valid_for_buy)
        self.assertFalse(result.valid_for_sell)
        self.assertIn('equilibrium', result.reason)

    def test_equilibrium_tolerance_uses_range_not_absolute_price(self):
        result = evaluate_premium_discount(101.0, self.swing_highs, self.swing_lows)

        self.assertEqual(result.zone, 'premium')
        self.assertEqual(result.zone_depth, 'shallow')
        self.assertTrue(result.valid_for_sell)
        self.assertAlmostEqual(result.distance_from_equilibrium_percent, 1.0)
        self.assertAlmostEqual(result.distance_from_equilibrium_range_percent, 2.5)

    def test_normal_discount_depth(self):
        result = evaluate_premium_discount(90.0, self.swing_highs, self.swing_lows)

        self.assertEqual(result.zone, 'discount')
        self.assertEqual(result.zone_depth, 'normal')
        self.assertEqual(result.zone_strength, 75.0)

    def test_deep_discount_depth(self):
        result = evaluate_premium_discount(84.0, self.swing_highs, self.swing_lows)

        self.assertEqual(result.zone, 'discount')
        self.assertEqual(result.zone_depth, 'deep')
        self.assertEqual(result.zone_strength, 100.0)

    def test_result_keeps_range_timeframe_metadata(self):
        result = evaluate_premium_discount(
            90.0,
            self.swing_highs,
            self.swing_lows,
            range_timeframe='4H',
            range_type='last_swing',
        )

        self.assertEqual(result.range_timeframe, '4H')
        self.assertEqual(result.range_type, 'last_swing')


if __name__ == '__main__':
    unittest.main()
