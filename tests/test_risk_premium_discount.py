import unittest

from core.premium_discount import PremiumDiscountResult
from core.risk import calculate_setup_score, format_setup_direction, resolve_session_decision, select_best_setup


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

        self.assertEqual(result['raw_score'], 25)
        self.assertEqual(result['total_score'], 0)
        self.assertEqual(result['decision'], 'Ignore')
        self.assertEqual(result['breakdown']['trend'], '+25 (Сильный тренд, совпадает с направлением)')
        self.assertIn('BLOCK (premium', result['breakdown']['premium_discount'])
        self.assertIn('score 25->0', result['breakdown']['premium_discount'])

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

        self.assertEqual(result['raw_score'], 25)
        self.assertEqual(result['total_score'], 0)
        self.assertEqual(result['decision'], 'Ignore')
        self.assertEqual(result['breakdown']['trend'], '+25 (Сильный тренд, совпадает с направлением)')
        self.assertIn('BLOCK (discount', result['breakdown']['premium_discount'])
        self.assertIn('score 25->0', result['breakdown']['premium_discount'])

    def test_valid_premium_discount_keeps_raw_score_as_total_score(self):
        result = self._base_score(
            'long',
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

        self.assertEqual(result['raw_score'], 25)
        self.assertEqual(result['total_score'], 25)
        self.assertIn('OK (discount', result['breakdown']['premium_discount'])

    def test_premium_discount_label_shows_equilibrium_and_range_distance(self):
        result = self._base_score(
            'long',
            PremiumDiscountResult(
                zone='discount',
                range_high=120,
                range_low=80,
                equilibrium=100,
                price=99.84,
                distance_from_equilibrium_percent=-0.16,
                valid_for_buy=True,
                valid_for_sell=False,
                reason='test',
                distance_from_equilibrium_range_percent=2.4,
            ),
        )

        self.assertIn('-0.16% от EQ', result['breakdown']['premium_discount'])
        self.assertIn('2.40% range', result['breakdown']['premium_discount'])

    def test_blocked_setup_still_exposes_component_sum_before_gate(self):
        result = calculate_setup_score(
            trade_direction='long',
            current_price=100.0,
            trend_data={'is_bullish': True, 'strength': 'flat'},
            context_structure_data=None,
            trigger_structure_data=None,
            sfp_data_in_window={
                'type': 'bullish_sfp',
                'index': 10,
                'quality_score': 80,
                'liquidity_depth': 0.73,
                'rejection_strength': 86,
                'volume_confirmed': False,
            },
            fvg_test_data=None,
            fvg_data=[],
            macro_data={'score': 0, 'reason': 'test'},
            premium_discount_data=PremiumDiscountResult(
                zone='equilibrium',
                range_high=120,
                range_low=80,
                equilibrium=100,
                price=99.99,
                distance_from_equilibrium_percent=-0.01,
                valid_for_buy=False,
                valid_for_sell=False,
                reason='test',
            ),
        )

        self.assertEqual(result['raw_score'], 30)
        self.assertEqual(result['total_score'], 0)
        self.assertEqual(result['breakdown']['trend'], '+10 (Цена по тренду, слабый импульс/откат)')
        self.assertIn('+20 (SFP Q80 D0.73 R86', result['breakdown']['liquidity'])
        self.assertIn('score 30->0', result['breakdown']['premium_discount'])

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

    def test_weak_ignore_formats_as_no_trade(self):
        text, emoji = format_setup_direction('LONG', total_score=15, decision='Ignore')

        self.assertEqual(text, 'NO TRADE')
        self.assertEqual(emoji, '⚪')

    def test_watchlist_keeps_directional_display(self):
        text, emoji = format_setup_direction('SHORT', total_score=45, decision='Watchlist')

        self.assertEqual(text, 'SHORT')
        self.assertEqual(emoji, '🔴')

    def test_high_score_outside_kill_zone_is_watch_only(self):
        decision = resolve_session_decision({'total_score': 87, 'decision': 'A+'}, in_kill_zone=False)

        self.assertEqual(decision, 'A+ WATCH ONLY')

    def test_high_score_inside_kill_zone_keeps_a_plus(self):
        decision = resolve_session_decision({'total_score': 87, 'decision': 'A+'}, in_kill_zone=True)

        self.assertEqual(decision, 'A+')

    def test_low_score_outside_kill_zone_stays_ignore(self):
        decision = resolve_session_decision({'total_score': 55, 'decision': 'Watchlist'}, in_kill_zone=False)

        self.assertEqual(decision, 'Ignore')


if __name__ == '__main__':
    unittest.main()
