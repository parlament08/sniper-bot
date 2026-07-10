import unittest

import pandas as pd

from services.macro_context import _tradfi_trend, evaluate_macro_score


class MacroContextTest(unittest.TestCase):
    def test_alt_long_with_unavailable_btc_d_gets_no_macro_bonus(self):
        score, reason = evaluate_macro_score(
            'long',
            {
                'DXY': {'bias': 'bearish', 'stale': False},
                'SPX': {'bias': 'bullish', 'stale': False},
                'BTC.D': {'bias': 'unavailable', 'price': None},
            },
            is_altcoin=True,
        )

        self.assertEqual(score, 0)
        self.assertEqual(reason, 'BTC.D unavailable')

    def test_mixed_dxy_spx_long_gets_clear_zero_reason(self):
        score, reason = evaluate_macro_score(
            'long',
            {
                'DXY': {'bias': 'bearish', 'stale': False},
                'SPX': {'bias': 'bearish', 'stale': False},
                'BTC.D': {'bias': 'neutral', 'price': 56.0},
            },
            is_altcoin=False,
        )

        self.assertEqual(score, 0)
        self.assertIn('Смешанный фон', reason)
        self.assertIn('DXY поддерживает риск', reason)

    def test_partial_macro_alignment_gets_reduced_bonus(self):
        score, reason = evaluate_macro_score(
            'long',
            {
                'DXY': {'bias': 'bearish', 'stale': False},
                'SPX': {'bias': 'neutral', 'stale': False},
                'BTC.D': {'bias': 'neutral', 'price': 56.0},
            },
            is_altcoin=True,
        )

        self.assertEqual(score, 5)
        self.assertIn('Частичная поддержка', reason)

    def test_stale_tradfi_data_gets_no_macro_bonus(self):
        score, reason = evaluate_macro_score(
            'short',
            {
                'DXY': {'bias': 'bullish', 'stale': True},
                'SPX': {'bias': 'bearish', 'stale': False},
                'BTC.D': {'bias': 'neutral', 'price': 56.0},
            },
            is_altcoin=True,
        )

        self.assertEqual(score, 0)
        self.assertIn('устарели', reason)

    def test_tradfi_trend_uses_multi_day_bias(self):
        index = pd.date_range(pd.Timestamp.utcnow() - pd.Timedelta(days=4), periods=5, freq='D')
        hist = pd.DataFrame({'Close': [100.0, 99.7, 99.2, 98.8, 98.4]}, index=index)

        result = _tradfi_trend(hist, 'DXY')

        self.assertEqual(result['bias'], 'bearish')
        self.assertIn('Bearish', result['trend'])
        self.assertFalse(result['stale'])


if __name__ == '__main__':
    unittest.main()
