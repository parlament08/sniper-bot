import unittest

from core.premium_discount import PremiumDiscountResult
from core.risk import calculate_setup_score, select_best_setup


class RiskPremiumDiscountTest(unittest.TestCase):
    def _base_score(self, trade_direction, premium_discount_data):
        return calculate_setup_score(
            trade_direction=trade_direction,
            current_price=100.0,
            trend_data={'is_bullish': trade_direction == 'long', 'strength': 'strong'},
            context_structure_data=None,
            trigger_structure_data=None,
            sfp_data_in_window=None,
            fvg_test_data=None,
            fvg_data=[],
            macro_data={'score': 0, 'reason': 'test'},
            premium_discount_data=premium_discount_data,
        )

    def test_buy_in_premium_is_blocked(self):
        result = self._base_score(
            'long',
            PremiumDiscountResult(
                zone='premium',
                range_high=120,
                range_low=80,
                equilibrium=100,
                price=110,
                distance_from_equilibrium_percent=10,
                valid_for_buy=False,
                valid_for_sell=True,
                reason='test',
            ),
        )

        self.assertEqual(result['total_score'], 0)
        self.assertEqual(result['decision'], 'Ignore')
        self.assertIn('BLOCK (premium', result['breakdown']['premium_discount'])

    def test_sell_in_discount_is_blocked(self):
        result = self._base_score(
            'short',
            PremiumDiscountResult(
                zone='discount',
                range_high=120,
                range_low=80,
                equilibrium=100,
                price=90,
                distance_from_equilibrium_percent=-10,
                valid_for_buy=True,
                valid_for_sell=False,
                reason='test',
            ),
        )

        self.assertEqual(result['total_score'], 0)
        self.assertEqual(result['decision'], 'Ignore')
        self.assertIn('BLOCK (discount', result['breakdown']['premium_discount'])

    def test_zero_zero_direction_selection_returns_neutral(self):
        long_score = {'total_score': 0, 'decision': 'Ignore', 'breakdown': {'premium_discount': 'BLOCK'}}
        short_score = {'total_score': 0, 'decision': 'Ignore', 'breakdown': {'premium_discount': 'BLOCK'}}

        selected_score, direction = select_best_setup(long_score, short_score)

        self.assertIs(selected_score, long_score)
        self.assertEqual(direction, 'NEUTRAL')

    def test_positive_score_keeps_directional_selection(self):
        long_score = {'total_score': 5, 'decision': 'Ignore', 'breakdown': {}}
        short_score = {'total_score': 0, 'decision': 'Ignore', 'breakdown': {}}

        selected_score, direction = select_best_setup(long_score, short_score)

        self.assertIs(selected_score, long_score)
        self.assertEqual(direction, 'LONG')


if __name__ == '__main__':
    unittest.main()
