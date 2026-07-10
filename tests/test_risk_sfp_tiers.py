import unittest

from core.risk import calculate_setup_score


class RiskSFPTierTest(unittest.TestCase):
    def _score_with_sfp(self, sfp_data):
        return calculate_setup_score(
            trade_direction='long',
            current_price=100.0,
            trend_data=None,
            context_structure_data=None,
            trigger_structure_data=None,
            sfp_data_in_window=sfp_data,
            fvg_test_data=None,
            fvg_data=[],
            macro_data={'score': 0, 'reason': 'test'},
        )

    def test_strong_sfp_gets_full_liquidity_and_volume(self):
        result = self._score_with_sfp({
            'type': 'bullish_sfp',
            'index': 10,
            'rvol': 2.0,
            'quality_score': 84,
            'liquidity_depth': 0.46,
            'rejection_strength': 99,
            'volume_confirmed': True,
        })

        self.assertEqual(result['total_score'], 30)
        self.assertIn('+20 (SFP Q84 D0.46 R99', result['breakdown']['liquidity'])
        self.assertEqual(result['breakdown']['volume'], '+10 (Сильный SFP volume confirmation: RVOL 2.00, Q84)')

    def test_medium_sfp_gets_reduced_liquidity_and_no_volume_bonus(self):
        result = self._score_with_sfp({
            'type': 'bullish_sfp',
            'index': 10,
            'rvol': 2.0,
            'quality_score': 70,
            'liquidity_depth': 1.57,
            'rejection_strength': 64,
            'volume_confirmed': True,
        })

        self.assertEqual(result['total_score'], 10)
        self.assertIn('+10 (SFP Q70 D1.57 R64', result['breakdown']['liquidity'])
        self.assertEqual(result['breakdown']['volume'], '0 (RVOL 2.00 есть, но SFP не strong-tier: Q70)')

    def test_shallow_or_weak_rejection_sfp_gets_only_token_liquidity(self):
        shallow_result = self._score_with_sfp({
            'type': 'bullish_sfp',
            'index': 10,
            'rvol': 2.0,
            'quality_score': 76,
            'liquidity_depth': 0.09,
            'rejection_strength': 93,
            'volume_confirmed': True,
        })
        weak_rejection_result = self._score_with_sfp({
            'type': 'bullish_sfp',
            'index': 10,
            'rvol': 2.0,
            'quality_score': 74,
            'liquidity_depth': 0.87,
            'rejection_strength': 40,
            'volume_confirmed': True,
        })

        self.assertEqual(shallow_result['total_score'], 5)
        self.assertEqual(weak_rejection_result['total_score'], 5)
        self.assertIn('+5 (SFP Q76 D0.09 R93', shallow_result['breakdown']['liquidity'])
        self.assertIn('+5 (SFP Q74 D0.87 R40', weak_rejection_result['breakdown']['liquidity'])
        self.assertEqual(shallow_result['breakdown']['volume'], '0 (RVOL 2.00 есть, но SFP не strong-tier: Q76)')
        self.assertEqual(weak_rejection_result['breakdown']['volume'], '0 (RVOL 2.00 есть, но SFP не strong-tier: Q74)')

    def test_sfp_liquidity_label_mentions_map_level_source(self):
        result = self._score_with_sfp({
            'type': 'bullish_sfp',
            'index': 10,
            'rvol': 2.0,
            'quality_score': 82,
            'liquidity_depth': 0.32,
            'rejection_strength': 88,
            'volume_confirmed': True,
            'level_type': 'equal_lows',
            'level_strength': 85.0,
        })

        self.assertIn('equal_lows S85', result['breakdown']['liquidity'])
        self.assertIn('на equal_lows liquidity', result['breakdown']['liquidity'])

    def test_absorption_warning_blocks_sfp_volume_bonus(self):
        result = self._score_with_sfp({
            'type': 'bullish_sfp',
            'index': 10,
            'rvol': 2.5,
            'quality_score': 86,
            'liquidity_depth': 0.46,
            'rejection_strength': 91,
            'volume_confirmed': False,
            'absorption_warning': True,
        })

        self.assertEqual(result['total_score'], 20)
        self.assertEqual(result['breakdown']['volume'], '0 (RVOL 2.50 высокий, но слабое закрытие / absorption warning)')


if __name__ == '__main__':
    unittest.main()
