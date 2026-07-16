import unittest

from core.premium_discount import PremiumDiscountResult
from core.risk import calculate_setup_score


class RiskScoreAssemblyTest(unittest.TestCase):
    def _premium_discount(self):
        return PremiumDiscountResult(
            zone='discount',
            range_high=120,
            range_low=80,
            equilibrium=100,
            price=90,
            distance_from_equilibrium_percent=-10,
            valid_for_buy=True,
            valid_for_sell=False,
            reason='test',
            distance_from_equilibrium_range_percent=25,
            range_timeframe='4H',
            zone_depth='normal',
            zone_strength=75,
        )

    def _fvg_data(self):
        return [{
            'type': 'bullish',
            'top': 101.0,
            'bottom': 99.0,
            'quality_score': 92,
            'tested': True,
            'invalidated': False,
            'age_bars': 3,
            'retest_count': 1,
        }]

    def _sfp(self):
        return {
            'type': 'bullish_sfp',
            'index': 8,
            'quality_score': 84,
            'liquidity_depth': 0.46,
            'rejection_strength': 90,
            'volume_confirmed': True,
            'rvol': 2.0,
        }

    def test_total_score_is_clamped_to_100_while_raw_score_keeps_sum(self):
        result = calculate_setup_score(
            trade_direction='long',
            current_price=100.0,
            trend_data={'is_bullish': True, 'strength': 'strong'},
            context_structure_data={
                'type': 'bullish_bos',
                'index': 7,
                'rvol': 2.0,
                'quality_score': 95,
                'displacement_ratio': 1.8,
                'body_ratio': 0.82,
            },
            trigger_structure_data={
                'type': 'bullish_bos',
                'index': 10,
                'rvol': 2.0,
                'quality_score': 95,
                'displacement_ratio': 1.8,
                'body_ratio': 0.82,
            },
            sfp_data_in_window=self._sfp(),
            fvg_test_data={'index': 9},
            fvg_data=self._fvg_data(),
            macro_data={'score': 10, 'reason': 'test macro'},
            premium_discount_data=self._premium_discount(),
        )

        self.assertGreater(result['raw_score'], 100)
        self.assertEqual(result['total_score'], 100)
        self.assertEqual(result['decision'], 'A+')
        self.assertIn('OK', result['breakdown']['scenario'])
        self.assertTrue(result['score_consistent'])
        self.assertEqual(result['score_components_total'], result['raw_score'])
        self.assertTrue(all(item['score_eligible'] for item in result['score_components']))

    def test_high_score_without_trigger_scenario_is_watchlist_only(self):
        result = calculate_setup_score(
            trade_direction='long',
            current_price=100.0,
            trend_data={'is_bullish': True, 'strength': 'strong'},
            context_structure_data={
                'type': 'bullish_bos',
                'index': 7,
                'rvol': 2.0,
                'quality_score': 95,
                'displacement_ratio': 1.8,
                'body_ratio': 0.82,
            },
            trigger_structure_data=None,
            sfp_data_in_window=self._sfp(),
            fvg_test_data={'index': 9},
            fvg_data=self._fvg_data(),
            macro_data={'score': 10, 'reason': 'test macro'},
            premium_discount_data=self._premium_discount(),
        )

        self.assertGreaterEqual(result['raw_score'], 70)
        self.assertEqual(result['total_score'], 69)
        self.assertEqual(result['decision'], 'Watchlist')
        self.assertIn('Scenario Gate', result['breakdown']['scenario'])

    def test_opposite_trigger_event_does_not_add_structure_points(self):
        result = calculate_setup_score(
            trade_direction='long',
            current_price=100.0,
            trend_data=None,
            context_structure_data=None,
            trigger_structure_data={
                'type': 'bearish_bos',
                'index': 10,
                'quality_score': 95,
                'candidate_id': 'SHORT_CANDIDATE',
            },
            sfp_data_in_window=None,
            fvg_test_data=None,
            fvg_data=[],
            macro_data=None,
            premium_discount_data=self._premium_discount(),
        )

        self.assertEqual(result['raw_score'], 0)
        self.assertEqual(result['score_components_total'], 0)
        self.assertTrue(result['score_consistent'])

    def test_historical_sfp_is_not_score_eligible(self):
        sfp = self._sfp()
        sfp['historical_only'] = True
        sfp['candidate_id'] = 'OLD_CANDIDATE'
        result = calculate_setup_score(
            trade_direction='long',
            current_price=100.0,
            trend_data=None,
            context_structure_data=None,
            trigger_structure_data=None,
            sfp_data_in_window=sfp,
            fvg_test_data=None,
            fvg_data=[],
            macro_data=None,
            premium_discount_data=self._premium_discount(),
        )

        self.assertEqual(result['raw_score'], 0)
        self.assertEqual(result['score_components_total'], 0)
        self.assertTrue(result['score_consistent'])


if __name__ == '__main__':
    unittest.main()
