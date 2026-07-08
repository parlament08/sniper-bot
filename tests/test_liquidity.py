import unittest

import pandas as pd

from core.liquidity import LiquidityConfig, LiquidityLevel, LiquidityMap, build_liquidity_map


class LiquidityEngineTest(unittest.TestCase):
    def _df(self, highs, lows, closes=None, atr=2.0):
        closes = closes or [(high + low) / 2 for high, low in zip(highs, lows)]
        rows = []
        for high, low, close in zip(highs, lows, closes):
            rows.append({
                'open': close,
                'high': high,
                'low': low,
                'close': close,
                'atr': atr,
            })
        return pd.DataFrame(rows)

    def test_equal_highs_create_buy_side_liquidity(self):
        df = self._df(
            highs=[101, 103, 104, 105.0, 103, 104, 105.2, 104, 103],
            lows=[98, 99, 100, 101, 99, 100, 101, 100, 99],
            closes=[100, 101, 102, 103, 102, 102, 103, 102, 101],
        )
        swing_highs = pd.DataFrame({'high': [105.0, 105.2]}, index=[3, 6])
        swing_lows = pd.DataFrame({'low': [98.0, 99.0]}, index=[0, 4])

        result = build_liquidity_map(df, swing_highs, swing_lows)

        self.assertIsInstance(result, LiquidityMap)
        equal_highs = [level for level in result.levels if level.type == 'equal_highs']
        self.assertEqual(len(equal_highs), 1)
        self.assertIsInstance(equal_highs[0], LiquidityLevel)
        self.assertEqual(equal_highs[0].touches, 2)
        self.assertFalse(equal_highs[0].swept)
        self.assertEqual(result.nearest_buy_side.type, 'equal_highs')

    def test_equal_lows_create_sell_side_liquidity(self):
        df = self._df(
            highs=[105, 104, 103, 102, 103, 102, 103, 104, 105],
            lows=[99, 97, 96, 95.0, 96, 97, 94.9, 96, 98],
            closes=[102, 101, 100, 99, 100, 100, 99, 101, 102],
        )
        swing_highs = pd.DataFrame({'high': [104.0, 105.0]}, index=[1, 8])
        swing_lows = pd.DataFrame({'low': [95.0, 94.9]}, index=[3, 6])

        result = build_liquidity_map(df, swing_highs, swing_lows)

        equal_lows = [level for level in result.levels if level.type == 'equal_lows']
        self.assertEqual(len(equal_lows), 1)
        self.assertEqual(equal_lows[0].touches, 2)
        self.assertFalse(equal_lows[0].swept)
        self.assertEqual(result.nearest_sell_side.type, 'equal_lows')

    def test_swept_buy_side_liquidity_is_marked_and_not_nearest(self):
        df = self._df(
            highs=[101, 103, 105, 104, 103, 106, 104],
            lows=[98, 99, 100, 99, 98, 100, 99],
            closes=[100, 101, 102, 101, 100, 101, 100],
        )
        swing_highs = pd.DataFrame({'high': [105.0]}, index=[2])
        swing_lows = pd.DataFrame({'low': [98.0]}, index=[0])

        result = build_liquidity_map(df, swing_highs, swing_lows)

        buy_levels = [level for level in result.levels if level.type == 'buy_side']
        self.assertTrue(buy_levels[0].swept)
        self.assertEqual(buy_levels[0].swept_at, 5)
        self.assertIsNone(result.nearest_buy_side)

    def test_swept_sell_side_liquidity_is_marked_and_not_nearest(self):
        df = self._df(
            highs=[105, 104, 103, 102, 103, 102, 104],
            lows=[99, 97, 95, 96, 97, 94, 98],
            closes=[102, 101, 100, 100, 101, 100, 101],
        )
        swing_highs = pd.DataFrame({'high': [105.0]}, index=[0])
        swing_lows = pd.DataFrame({'low': [95.0]}, index=[2])

        result = build_liquidity_map(df, swing_highs, swing_lows)

        sell_levels = [level for level in result.levels if level.type == 'sell_side']
        self.assertTrue(sell_levels[0].swept)
        self.assertEqual(sell_levels[0].swept_at, 5)
        self.assertIsNone(result.nearest_sell_side)

    def test_internal_and_external_liquidity_are_distinguished(self):
        df = self._df(
            highs=[100, 112, 106, 108, 105, 104],
            lows=[90, 94, 96, 98, 95, 97],
            closes=[100, 100, 101, 102, 101, 100],
        )
        swing_highs = pd.DataFrame({'high': [112.0, 106.0]}, index=[1, 2])
        swing_lows = pd.DataFrame({'low': [90.0, 96.0]}, index=[0, 2])
        config = LiquidityConfig(old_level_min_age_bars=3, range_lookback_bars=10)

        result = build_liquidity_map(df, swing_highs, swing_lows, config=config)

        external_levels = [level for level in result.levels if level.type == 'external']
        internal_levels = [level for level in result.levels if level.type == 'internal']
        self.assertTrue(any(level.price == 112.0 for level in external_levels))
        self.assertTrue(any(level.price == 106.0 for level in internal_levels))
        self.assertTrue(any(level.price == 96.0 for level in internal_levels))


if __name__ == '__main__':
    unittest.main()
