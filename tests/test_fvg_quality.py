import unittest

import pandas as pd

from core.structure import FVGResult, find_fvg


class FVGQualityTest(unittest.TestCase):
    def _df(self, extra_rows=None):
        rows = [
            {'open': 9.5, 'high': 10.0, 'low': 9.0, 'close': 9.8, 'volume': 100, 'atr': 2.0, 'rvol': 1.0},
            {'open': 10.0, 'high': 14.5, 'low': 9.8, 'close': 14.0, 'volume': 240, 'atr': 2.0, 'rvol': 2.4},
            {'open': 12.2, 'high': 13.0, 'low': 12.0, 'close': 12.5, 'volume': 150, 'atr': 2.0, 'rvol': 1.5},
        ]
        if extra_rows:
            rows.extend(extra_rows)
        return pd.DataFrame(rows)

    def _first_bullish_fvg(self, df):
        fvgs = find_fvg(df, atr_series=df['atr'], rvol_series=df['rvol'], min_size_atr_ratio=0.5)
        return next(fvg for fvg in fvgs if fvg['type'] == 'bullish' and fvg['end_index'] == 2)

    def test_strong_untested_fvg_gets_high_quality_score(self):
        fvg = self._first_bullish_fvg(self._df())

        self.assertIsInstance(fvg, FVGResult)
        self.assertTrue(fvg.detected)
        self.assertFalse(fvg.tested)
        self.assertFalse(fvg.invalidated)
        self.assertEqual(fvg.age_bars, 0)
        self.assertEqual(fvg.overlap_percent, 0)
        self.assertGreaterEqual(fvg.quality_score, 80)
        self.assertGreaterEqual(fvg.size_atr_ratio, 1.0)
        self.assertGreaterEqual(fvg.displacement_ratio, 2.0)
        self.assertTrue(fvg.volume_confirmed)

    def test_partial_retest_reduces_quality_but_keeps_zone_valid(self):
        df = self._df([
            {'open': 12.8, 'high': 13.2, 'low': 11.0, 'close': 12.4, 'volume': 130, 'atr': 2.0, 'rvol': 1.1},
        ])

        fvg = self._first_bullish_fvg(df)

        self.assertTrue(fvg.tested)
        self.assertFalse(fvg.invalidated)
        self.assertEqual(fvg.age_bars, 1)
        self.assertEqual(fvg.overlap_percent, 50)
        self.assertEqual(fvg.retest_count, 1)
        self.assertGreater(fvg.quality_score, 50)

    def test_fully_filled_fvg_is_invalidated_with_min_quality(self):
        df = self._df([
            {'open': 12.2, 'high': 12.4, 'low': 9.8, 'close': 10.5, 'volume': 150, 'atr': 2.0, 'rvol': 1.2},
        ])

        fvg = self._first_bullish_fvg(df)

        self.assertTrue(fvg.tested)
        self.assertTrue(fvg.invalidated)
        self.assertEqual(fvg.overlap_percent, 100)
        self.assertEqual(fvg.quality_score, 0)
        self.assertFalse(bool(fvg))


if __name__ == '__main__':
    unittest.main()
