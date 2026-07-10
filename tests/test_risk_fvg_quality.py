import unittest

from core.risk import calculate_setup_score


class RiskFVGQualityTest(unittest.TestCase):
    def _score_with_fvg(self, fvg):
        return calculate_setup_score(
            trade_direction='long',
            current_price=103.0,
            trend_data=None,
            context_structure_data=None,
            trigger_structure_data=None,
            sfp_data_in_window=None,
            fvg_test_data={'index': 12},
            fvg_data=[fvg],
            macro_data={'score': 0, 'reason': 'test'},
        )

    def test_high_quality_fvg_gets_full_score_after_fresh_retest_even_outside_zone(self):
        result = self._score_with_fvg({
            'type': 'bullish',
            'bottom': 100.0,
            'top': 102.0,
            'end_index': 10,
            'tested': True,
            'invalidated': False,
            'quality_score': 92,
            'age_bars': 2,
            'retest_count': 1,
        })

        self.assertEqual(result['total_score'], 15)
        self.assertIn('+15 (Тест FVG Q92 age2 retests1, свежий ретест, зона удержана', result['breakdown']['fvg'])

    def test_mid_quality_fvg_gets_reduced_score(self):
        result = self._score_with_fvg({
            'type': 'bullish',
            'bottom': 100.0,
            'top': 102.0,
            'end_index': 10,
            'tested': True,
            'invalidated': False,
            'quality_score': 78,
            'age_bars': 4,
            'retest_count': 1,
        })

        self.assertEqual(result['total_score'], 10)
        self.assertIn('+10 (Тест FVG Q78 age4 retests1', result['breakdown']['fvg'])

    def test_low_quality_fvg_does_not_score(self):
        result = self._score_with_fvg({
            'type': 'bullish',
            'bottom': 100.0,
            'top': 102.0,
            'end_index': 10,
            'tested': True,
            'invalidated': False,
            'quality_score': 45,
            'age_bars': 9,
            'retest_count': 3,
        })

        self.assertEqual(result['total_score'], 0)
        self.assertIn('ниже quality tier', result['breakdown']['fvg'])

    def test_invalidated_fvg_does_not_score(self):
        result = self._score_with_fvg({
            'type': 'bullish',
            'bottom': 100.0,
            'top': 102.0,
            'end_index': 10,
            'tested': True,
            'invalidated': True,
            'quality_score': 95,
            'age_bars': 1,
            'retest_count': 1,
        })

        self.assertEqual(result['total_score'], 0)
        self.assertEqual(result['breakdown']['fvg'], '0 (FVG close invalidated после retest)')


if __name__ == '__main__':
    unittest.main()
