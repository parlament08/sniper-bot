import unittest

import pandas as pd

from core.structure import MarketStructure, MarketStructureConfig, evaluate_market_structure


class MarketStructureTest(unittest.TestCase):
    def _df(self, highs=None, lows=None, atr=2.0):
        highs = highs or [10, 11, 12, 13, 14]
        lows = lows or [8, 9, 10, 11, 12]
        rows = []
        for high, low in zip(highs, lows):
            rows.append({
                'open': low + 0.5,
                'high': high,
                'low': low,
                'close': high - 0.5,
                'atr': atr,
            })
        return pd.DataFrame(rows)

    def test_conflicting_hh_and_ll_returns_neutral(self):
        swing_highs = pd.DataFrame({'high': [100.0, 110.0]}, index=[1, 3])
        swing_lows = pd.DataFrame({'low': [95.0, 90.0]}, index=[2, 4])

        result = evaluate_market_structure(
            self._df(),
            swing_highs,
            swing_lows,
            trend_data={'adx_value': 30, 'is_bullish': True},
        )

        self.assertIsInstance(result, MarketStructure)
        self.assertEqual(result.trend, 'neutral')
        self.assertEqual(result.reason, 'Conflicting swing structure')
        self.assertEqual(result.confidence, 23)

    def test_low_adx_returns_neutral_even_with_directional_swings(self):
        swing_highs = pd.DataFrame({'high': [100.0, 110.0]}, index=[1, 3])
        swing_lows = pd.DataFrame({'low': [90.0, 95.0]}, index=[2, 4])

        result = evaluate_market_structure(
            self._df(),
            swing_highs,
            swing_lows,
            trend_data={'adx_value': 12, 'is_bullish': True},
        )

        self.assertEqual(result.trend, 'neutral')
        self.assertEqual(result.reason, 'ADX below neutral threshold')

    def test_narrow_range_returns_neutral(self):
        swing_highs = pd.DataFrame({'high': [100.0, 110.0]}, index=[1, 3])
        swing_lows = pd.DataFrame({'low': [90.0, 95.0]}, index=[2, 4])
        config = MarketStructureConfig(adx_neutral_threshold=10, min_range_atr_ratio=2.0)

        result = evaluate_market_structure(
            self._df(highs=[10, 10.5, 10.7], lows=[9.5, 9.8, 10.0], atr=2.0),
            swing_highs,
            swing_lows,
            trend_data={'adx_value': 30, 'is_bullish': True},
            config=config,
        )

        self.assertEqual(result.trend, 'neutral')
        self.assertEqual(result.reason, 'Range too narrow')

    def test_confirmed_hh_hl_returns_bullish(self):
        swing_highs = pd.DataFrame({'high': [100.0, 110.0]}, index=[1, 3])
        swing_lows = pd.DataFrame({'low': [90.0, 95.0]}, index=[2, 4])

        result = evaluate_market_structure(
            self._df(),
            swing_highs,
            swing_lows,
            trend_data={'adx_value': 30, 'is_bullish': True},
        )

        self.assertEqual(result.trend, 'bullish')
        self.assertGreaterEqual(result.confidence, 65)

    def test_confirmed_lh_ll_returns_bearish(self):
        swing_highs = pd.DataFrame({'high': [110.0, 100.0]}, index=[1, 3])
        swing_lows = pd.DataFrame({'low': [95.0, 90.0]}, index=[2, 4])

        result = evaluate_market_structure(
            self._df(),
            swing_highs,
            swing_lows,
            trend_data={'adx_value': 30, 'is_bullish': False},
        )

        self.assertEqual(result.trend, 'bearish')
        self.assertGreaterEqual(result.confidence, 65)

    def test_conflicting_recent_bos_returns_neutral(self):
        swing_highs = pd.DataFrame({'high': [110.0, 100.0]}, index=[1, 3])
        swing_lows = pd.DataFrame({'low': [95.0, 90.0]}, index=[2, 4])

        result = evaluate_market_structure(
            self._df(),
            swing_highs,
            swing_lows,
            trend_data={'adx_value': 30, 'is_bullish': False},
            recent_structure_events=[
                {'type': 'bullish_bos'},
                {'type': 'bearish_bos'},
            ],
        )

        self.assertEqual(result.trend, 'neutral')
        self.assertEqual(result.reason, 'Conflicting recent BOS')


if __name__ == '__main__':
    unittest.main()
