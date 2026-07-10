import unittest

import pandas as pd

from core.trade_simulator import simulate_trade


class TradeSimulatorTest(unittest.TestCase):
    def test_long_trade_hits_target(self):
        candles = pd.DataFrame([
            {"open": 100, "high": 104.2, "low": 99.5, "close": 103.5},
        ])

        result = simulate_trade(
            candles,
            direction="LONG",
            entry=100,
            stop_loss=98,
            target_1=104,
            fee_per_side_percent=0,
            slippage_percent=0,
        )

        self.assertEqual(result.outcome, "win")
        self.assertEqual(result.gross_r, 2.0)
        self.assertEqual(result.exit_reason, "target_1")

    def test_same_candle_sl_and_tp_uses_conservative_stop_first(self):
        candles = pd.DataFrame([
            {"open": 100, "high": 104.5, "low": 97.5, "close": 101},
        ])

        result = simulate_trade(
            candles,
            direction="LONG",
            entry=100,
            stop_loss=98,
            target_1=104,
            fee_per_side_percent=0,
            slippage_percent=0,
        )

        self.assertEqual(result.outcome, "loss")
        self.assertEqual(result.gross_r, -1.0)
        self.assertIn("conservative", result.exit_reason)

    def test_short_trade_times_out_with_market_close_r(self):
        candles = pd.DataFrame([
            {"open": 100, "high": 101, "low": 99, "close": 99.5},
            {"open": 99.5, "high": 100.5, "low": 98.8, "close": 99.0},
        ])

        result = simulate_trade(
            candles,
            direction="SHORT",
            entry=100,
            stop_loss=102,
            target_1=94,
            max_bars=2,
            fee_per_side_percent=0,
            slippage_percent=0,
        )

        self.assertEqual(result.outcome, "timeout")
        self.assertEqual(result.gross_r, 0.5)
        self.assertEqual(result.bars_held, 2)


if __name__ == "__main__":
    unittest.main()
