import unittest

from core.displacement import evaluate_displacement


class TestDisplacementEngine(unittest.TestCase):
    def test_strong_bullish_displacement(self):
        result = evaluate_displacement(
            {'open': 100, 'high': 106, 'low': 99, 'close': 105.5, 'rvol': 1.6},
            atr=3,
        )

        self.assertEqual(result.direction, 'bullish')
        self.assertTrue(result.valid)
        self.assertGreaterEqual(result.score, 85)
        self.assertGreater(result.body_ratio, 0.75)
        self.assertGreater(result.atr_ratio, 1.5)
        self.assertEqual(result.volume_ratio, 1.6)

    def test_weak_bullish_candle_is_invalid(self):
        result = evaluate_displacement(
            {'open': 100, 'high': 106, 'low': 99, 'close': 101, 'rvol': 1.0},
            atr=3,
        )

        self.assertEqual(result.direction, 'bullish')
        self.assertFalse(result.valid)
        self.assertLess(result.score, 70)
        self.assertLess(result.body_ratio, 0.2)

    def test_strong_bearish_displacement(self):
        result = evaluate_displacement(
            {'open': 105, 'high': 106, 'low': 99, 'close': 100, 'rvol': 1.6},
            atr=3,
        )

        self.assertEqual(result.direction, 'bearish')
        self.assertTrue(result.valid)
        self.assertGreaterEqual(result.score, 80)
        self.assertGreater(result.body_ratio, 0.7)
        self.assertGreater(result.atr_ratio, 1.5)

    def test_doji_returns_invalid(self):
        result = evaluate_displacement(
            {'open': 100, 'high': 103, 'low': 97, 'close': 100, 'rvol': 2.0},
            atr=3,
        )

        self.assertEqual(result.direction, 'neutral')
        self.assertFalse(result.valid)
        self.assertEqual(result.body, 0)
        self.assertEqual(result.body_ratio, 0)

    def test_zero_atr_handled_safely(self):
        result = evaluate_displacement(
            {'open': 100, 'high': 106, 'low': 99, 'close': 105.5},
            atr=0,
        )

        self.assertEqual(result.atr_ratio, 0)
        self.assertFalse(result.valid)
        self.assertLess(result.score, 70)

    def test_zero_range_handled_safely(self):
        result = evaluate_displacement(
            {'open': 100, 'high': 100, 'low': 100, 'close': 100, 'rvol': 2.0},
            atr=1,
        )

        self.assertEqual(result.candle_range, 0)
        self.assertEqual(result.body_ratio, 0)
        self.assertEqual(result.close_position, 0)
        self.assertFalse(result.valid)
        self.assertEqual(result.reason, 'Zero candle range')

    def test_high_rvol_weak_body_returns_absorption_warning(self):
        result = evaluate_displacement(
            {'open': 100, 'high': 110, 'low': 99, 'close': 102, 'rvol': 2.5},
            atr=4,
            direction='bullish',
        )

        self.assertTrue(result.absorption_warning)
        self.assertGreaterEqual(result.absorption_score, 70)
        self.assertEqual(result.reason, 'High RVOL with weak body/close, possible absorption')


if __name__ == '__main__':
    unittest.main()
