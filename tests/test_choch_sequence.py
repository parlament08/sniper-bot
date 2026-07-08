import unittest

import pandas as pd

from core.structure import CHoCHResult, detect_structure_break


class CHoCHSequenceTest(unittest.TestCase):
    def test_single_break_against_bullish_structure_is_not_choch(self):
        swing_highs = pd.DataFrame({'high': [100.0, 110.0]}, index=[1, 3])
        swing_lows = pd.DataFrame({'low': [90.0, 100.0]}, index=[2, 4])
        candle = pd.Series({
            'open': 101.0,
            'high': 102.0,
            'low': 97.0,
            'close': 98.0,
            'atr': 2.0,
            'rvol': 2.0,
        }, name=5)

        result = detect_structure_break(candle, swing_highs, swing_lows, right_bars=0)

        self.assertIsNone(result)

    def test_classic_bearish_choch_requires_sequence_confirmation(self):
        swing_highs = pd.DataFrame({'high': [100.0, 110.0, 105.0]}, index=[1, 3, 7])
        swing_lows = pd.DataFrame({'low': [90.0, 100.0, 95.0]}, index=[2, 4, 6])
        candle = pd.Series({
            'open': 98.0,
            'high': 99.0,
            'low': 92.0,
            'close': 93.0,
            'atr': 3.0,
            'rvol': 2.2,
        }, name=8)

        result = detect_structure_break(candle, swing_highs, swing_lows, right_bars=0)

        self.assertIsInstance(result, CHoCHResult)
        self.assertTrue(result.confirmed)
        self.assertEqual(result.type, 'bearish_choch')
        self.assertTrue(result.swing_sequence_valid)
        self.assertGreaterEqual(result.quality_score, 80)
        self.assertGreaterEqual(result.confidence, 85)
        self.assertIn('HH', result.swing_sequence)
        self.assertIn('HL', result.swing_sequence)
        self.assertIn('LL', result.swing_sequence)
        self.assertIn('LH', result.swing_sequence)

    def test_classic_bullish_choch_requires_sequence_confirmation(self):
        swing_highs = pd.DataFrame({'high': [110.0, 105.0, 115.0]}, index=[1, 4, 6])
        swing_lows = pd.DataFrame({'low': [100.0, 90.0, 95.0]}, index=[2, 5, 7])
        candle = pd.Series({
            'open': 112.0,
            'high': 119.0,
            'low': 111.0,
            'close': 117.5,
            'atr': 3.0,
            'rvol': 2.0,
        }, name=8)

        result = detect_structure_break(candle, swing_highs, swing_lows, right_bars=0)

        self.assertIsInstance(result, CHoCHResult)
        self.assertTrue(result.confirmed)
        self.assertEqual(result.type, 'bullish_choch')
        self.assertTrue(result.swing_sequence_valid)
        self.assertGreaterEqual(result.quality_score, 80)
        self.assertGreaterEqual(result.confidence, 85)
        self.assertIn('LL', result.swing_sequence)
        self.assertIn('LH', result.swing_sequence)
        self.assertIn('HH', result.swing_sequence)
        self.assertIn('HL', result.swing_sequence)


if __name__ == '__main__':
    unittest.main()
