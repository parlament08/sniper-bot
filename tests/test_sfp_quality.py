import unittest

import pandas as pd

from core.liquidity import LiquidityLevel
from core.structure import SFPConfig, SFPResult, detect_sfp, detect_sfp_against_liquidity_levels


class SFPQualityTest(unittest.TestCase):
    def setUp(self):
        self.swing_highs = pd.DataFrame({'high': [100.0, 105.0]}, index=[1, 5])
        self.swing_lows = pd.DataFrame({'low': [95.0, 90.0]}, index=[2, 6])

    def _detect(self, candle, **kwargs):
        return detect_sfp(
            pd.Series(candle, name=10),
            self.swing_highs,
            self.swing_lows,
            right_bars=0,
            **kwargs,
        )

    def test_strong_bearish_sfp_gets_high_quality_score(self):
        result = self._detect({
            'open': 105.7,
            'high': 106.0,
            'low': 103.8,
            'close': 103.9,
            'atr': 2.0,
            'rvol': 2.1,
        })

        self.assertIsInstance(result, SFPResult)
        self.assertTrue(result.detected)
        self.assertEqual(result.type, 'bearish_sfp')
        self.assertGreaterEqual(result.quality_score, 75)
        self.assertGreaterEqual(result.liquidity_depth, 0.45)
        self.assertGreaterEqual(result.rejection_strength, 75)
        self.assertTrue(result.volume_confirmed)

    def test_strong_bullish_sfp_gets_high_quality_score(self):
        result = self._detect({
            'open': 89.2,
            'high': 91.3,
            'low': 88.9,
            'close': 91.0,
            'atr': 2.0,
            'rvol': 1.8,
        })

        self.assertIsInstance(result, SFPResult)
        self.assertTrue(result.detected)
        self.assertEqual(result.type, 'bullish_sfp')
        self.assertGreaterEqual(result.quality_score, 75)
        self.assertGreaterEqual(result.liquidity_depth, 0.5)
        self.assertGreaterEqual(result.rejection_strength, 75)
        self.assertTrue(result.volume_confirmed)

    def test_minor_level_pierce_is_not_quality_sfp(self):
        result = self._detect({
            'open': 105.02,
            'high': 105.05,
            'low': 104.8,
            'close': 104.98,
            'atr': 2.0,
            'rvol': 2.0,
        })

        self.assertIsNone(result)

    def test_return_must_hold_inside_when_confirmation_required(self):
        future_candles = pd.DataFrame(
            [{'open': 104.2, 'high': 105.4, 'low': 104.0, 'close': 105.2}],
            index=[11],
        )
        config = SFPConfig(hold_confirmation_bars=1)

        result = self._detect({
            'open': 105.7,
            'high': 106.0,
            'low': 103.8,
            'close': 103.9,
            'atr': 2.0,
            'rvol': 2.1,
        }, config=config, future_candles=future_candles)

        self.assertIsNone(result)

    def test_sfp_against_liquidity_level_includes_level_quality(self):
        level = LiquidityLevel(
            type='equal_lows',
            price=90.0,
            strength=85.0,
            touches=3,
            age_bars=12,
            distance_percent=1.0,
            distance_atr=0.5,
            swept=False,
            swept_at=None,
            source_index=5,
            description='Equal lows sell-side liquidity',
        )

        result = detect_sfp_against_liquidity_levels(
            pd.Series({
                'open': 89.2,
                'high': 91.3,
                'low': 88.9,
                'close': 91.0,
                'atr': 2.0,
                'rvol': 1.8,
            }, name=10),
            [level],
        )

        self.assertIsInstance(result, SFPResult)
        self.assertTrue(result.detected)
        self.assertEqual(result.type, 'bullish_sfp')
        self.assertEqual(result.level_type, 'equal_lows')
        self.assertEqual(result.level_strength, 85.0)
        self.assertEqual(result.level_touches, 3)
        self.assertGreaterEqual(result.quality_score, 75)

    def test_sfp_against_swept_liquidity_level_is_ignored(self):
        level = LiquidityLevel(
            type='equal_lows',
            price=90.0,
            strength=85.0,
            touches=3,
            age_bars=12,
            distance_percent=1.0,
            distance_atr=0.5,
            swept=True,
            swept_at=8,
            source_index=5,
            description='Equal lows sell-side liquidity',
        )

        result = detect_sfp_against_liquidity_levels(
            pd.Series({
                'open': 89.2,
                'high': 91.3,
                'low': 88.9,
                'close': 91.0,
                'atr': 2.0,
                'rvol': 1.8,
            }, name=10),
            [level],
        )

        self.assertIsNone(result)


if __name__ == '__main__':
    unittest.main()
