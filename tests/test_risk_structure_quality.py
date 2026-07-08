import unittest

from core.risk import calculate_setup_score


class RiskStructureQualityTest(unittest.TestCase):
    def _score_with_trigger(self, trigger_structure_data):
        return calculate_setup_score(
            trade_direction='short',
            current_price=100.0,
            trend_data={'is_bullish': False, 'strength': 'strong'},
            context_structure_data=None,
            trigger_structure_data=trigger_structure_data,
            sfp_data_in_window=None,
            fvg_test_data=None,
            fvg_data=[],
            macro_data={'score': 0, 'reason': 'test'},
        )

    def _score_with_context_and_trigger(self, context_structure_data, trigger_structure_data):
        return calculate_setup_score(
            trade_direction='short',
            current_price=100.0,
            trend_data=None,
            context_structure_data=context_structure_data,
            trigger_structure_data=trigger_structure_data,
            sfp_data_in_window=None,
            fvg_test_data=None,
            fvg_data=[],
            macro_data={'score': 0, 'reason': 'test'},
        )

    def test_structure_breakdown_includes_quality_metrics(self):
        result = self._score_with_trigger({
            'type': 'bearish_choch',
            'index': 10,
            'rvol': 1.2,
            'quality_score': 74,
            'displacement_ratio': 1.21,
            'body_ratio': 0.68,
        })

        self.assertIn('15m CHoCH Q74 DR1.21 BR0.68', result['breakdown']['structure'])
        self.assertIn('без POI/SFP confirmation', result['breakdown']['structure'])

    def test_unconfirmed_structure_volume_requires_quality_80(self):
        result = self._score_with_trigger({
            'type': 'bearish_bos',
            'index': 10,
            'rvol': 2.0,
            'quality_score': 74,
            'displacement_ratio': 1.2,
            'body_ratio': 0.7,
        })

        self.assertEqual(result['total_score'], 30)
        self.assertEqual(result['breakdown']['volume'], '0 (Объем есть, но 15m структура без POI/SFP и Q<90)')

    def test_high_quality_unconfirmed_structure_does_not_get_full_volume_score(self):
        result = self._score_with_trigger({
            'type': 'bearish_bos',
            'index': 10,
            'rvol': 2.0,
            'quality_score': 94,
            'displacement_ratio': 1.8,
            'body_ratio': 0.82,
        })

        self.assertEqual(result['total_score'], 35)
        self.assertEqual(result['breakdown']['volume'], '+5 (Объем на экстремальном 15m BOS без POI/SFP)')

    def test_context_structure_volume_message_uses_1h_when_context_has_priority(self):
        result = self._score_with_context_and_trigger(
            {
                'type': 'bearish_bos',
                'index': 8,
                'rvol': 2.0,
                'quality_score': 100,
                'displacement_ratio': 3.18,
                'body_ratio': 0.89,
            },
            {
                'type': 'bearish_bos',
                'index': 10,
                'rvol': 2.0,
                'quality_score': 100,
                'displacement_ratio': 2.4,
                'body_ratio': 0.86,
            },
        )

        self.assertIn('+10 (1H BOS Q100 DR3.18 BR0.89 only)', result['breakdown']['structure'])
        self.assertEqual(result['breakdown']['volume'], '+5 (Объем на 1H сломе с Q>=90)')


if __name__ == '__main__':
    unittest.main()
