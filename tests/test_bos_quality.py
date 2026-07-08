import unittest

import pandas as pd

from core.structure import BOSConfig, BOSResult, detect_structure_break


class BOSQualityTest(unittest.TestCase):
    def setUp(self):
        self.swing_highs = pd.DataFrame(
            {'high': [100.0, 105.0]},
            index=[1, 5],
        )
        self.swing_lows = pd.DataFrame(
            {'low': [90.0, 95.0]},
            index=[2, 6],
        )
        self.config = BOSConfig()

    def _detect(self, candle, **kwargs):
        return detect_structure_break(
            pd.Series(candle, name=10),
            self.swing_highs,
            self.swing_lows,
            right_bars=0,
            config=kwargs.pop('config', self.config),
            **kwargs,
        )

    def test_impulsive_bos_gets_high_quality_score(self):
        result = self._detect({
            'open': 104.2,
            'high': 107.0,
            'low': 104.0,
            'close': 106.5,
            'atr': 1.5,
            'rvol': 2.1,
        })

        self.assertIsInstance(result, BOSResult)
        self.assertTrue(result.detected)
        self.assertGreaterEqual(result.quality_score, 80)
        self.assertGreater(result.displacement_ratio, 1.5)
        self.assertGreater(result.body_ratio, 0.75)
        self.assertTrue(result.volume_confirmed)
        self.assertTrue(result.close_confirmed)
        self.assertEqual(result.get('type'), 'bullish_bos')
        self.assertEqual(result['level'], 105.0)

    def test_weak_candle_is_not_confirmed_as_bos(self):
        result = self._detect({
            'open': 105.1,
            'high': 106.0,
            'low': 104.0,
            'close': 105.3,
            'atr': 2.0,
            'rvol': 2.4,
        })

        self.assertFalse(result.detected)
        self.assertLess(result.body_ratio, self.config.min_body_ratio)
        self.assertLess(result.displacement_ratio, self.config.min_displacement_atr)

    def test_close_must_clear_atr_buffer(self):
        result = self._detect({
            'open': 104.0,
            'high': 107.0,
            'low': 103.7,
            'close': 105.05,
            'atr': 1.5,
            'rvol': 2.2,
        })

        self.assertFalse(result.detected)
        self.assertFalse(result.close_confirmed)

    def test_immediate_return_invalidates_bos_when_hold_confirmation_is_required(self):
        future_candles = pd.DataFrame(
            [{'open': 106.2, 'high': 106.4, 'low': 104.8, 'close': 104.9}],
            index=[11],
        )
        config = BOSConfig(hold_confirmation_bars=1)

        result = self._detect({
            'open': 104.2,
            'high': 107.0,
            'low': 104.0,
            'close': 106.5,
            'atr': 1.5,
            'rvol': 2.1,
        }, config=config, future_candles=future_candles)

        self.assertFalse(result.detected)
        self.assertFalse(result.hold_confirmed)


if __name__ == '__main__':
    unittest.main()
