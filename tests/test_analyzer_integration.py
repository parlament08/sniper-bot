import os
import unittest
from unittest.mock import patch

import pandas as pd

os.environ.setdefault("GEMINI_API_KEY", "test-key")

import analyzer
from core.structure import MarketStructure


class AnalyzerIntegrationTest(unittest.TestCase):
    def _df(self, periods=140, freq="15min"):
        index = pd.date_range("2026-01-01", periods=periods, freq=freq)
        base = pd.Series(range(periods), index=index).astype(float)
        return pd.DataFrame(
            {
                "open": 100 + base * 0.01,
                "high": 101 + base * 0.01,
                "low": 99 + base * 0.01,
                "close": 100.5 + base * 0.01,
                "volume": 1000 + base,
            },
            index=index,
        )

    def _swings(self, df):
        highs = df.iloc[[20, 60, 100]][["high"]]
        lows = df.iloc[[10, 50, 90]][["low"]]
        return highs, lows

    def test_prepare_fetches_direct_1h_and_reports_liquidity_map(self):
        calls = []

        def fake_fetch(coin, timeframe, limit):
            calls.append((coin, timeframe, limit))
            freq = "4h" if timeframe == "4h" else "1h" if timeframe == "1h" else "15min"
            return self._df(freq=freq)

        liquidity_map = {
            "nearest_buy_side": None,
            "nearest_sell_side": None,
            "strongest_buy_side": None,
            "strongest_sell_side": None,
        }

        with patch("analyzer.fetch_candles", side_effect=fake_fetch), \
            patch("analyzer.calculate_ema", side_effect=lambda df, period: pd.Series(100.0, index=df.index)), \
            patch("analyzer.calculate_adx", side_effect=lambda df, period: pd.DataFrame({"adx": 25.0}, index=df.index)), \
            patch("analyzer.calculate_atr", side_effect=lambda df, period: pd.Series(1.0, index=df.index)), \
            patch("analyzer.calculate_rvol", side_effect=lambda df, period: pd.Series(1.0, index=df.index)), \
            patch("analyzer.find_swings", side_effect=lambda df, left_bars=2, right_bars=2: self._swings(df)), \
            patch("analyzer.build_liquidity_map", return_value=liquidity_map), \
            patch(
                "analyzer.evaluate_market_structure",
                return_value=MarketStructure(trend="neutral", confidence=20, reason="test neutral"),
            ):
            score_result, analysis_data = analyzer.prepare_and_analyze("BTC", {})

        self.assertIn(("BTC", "1h", 300), calls)
        self.assertEqual(score_result["breakdown"]["liquidity_map"], "Buy: none | Sell: none | Strongest B/S: none / none")
        self.assertIs(analysis_data["liquidity_map"], liquidity_map)

    def test_send_telegram_blocks_splits_long_dashboard_without_cutting_blocks(self):
        header = ["<b>header</b>", "macro"]
        blocks = ["A" * 80, "B" * 80, "C" * 80]
        sent_messages = []

        with patch("analyzer.send_telegram_alert", side_effect=sent_messages.append):
            analyzer.send_telegram_blocks(header, blocks, max_length=120)

        self.assertEqual(len(sent_messages), 3)
        self.assertTrue(all(len(message) <= 120 for message in sent_messages))
        self.assertIn("A" * 80, sent_messages[0])
        self.assertIn("B" * 80, sent_messages[1])
        self.assertIn("C" * 80, sent_messages[2])

    def test_strong_reversal_context_requires_sfp_choch_and_bos(self):
        direction = analyzer._has_strong_reversal_context(
            {
                "type": "bullish_sfp",
                "quality_score": 84,
                "liquidity_depth": 0.46,
                "rejection_strength": 90,
            },
            {
                "type": "bullish_bos",
                "quality_score": 92,
                "displacement_ratio": 1.4,
            },
            {
                "type": "bullish_choch",
                "quality_score": 88,
                "displacement_ratio": 1.1,
            },
        )

        self.assertEqual(direction, "bullish")

    def test_low_adx_override_caps_a_plus_to_watchlist(self):
        score_result = {
            "total_score": 82,
            "decision": "A+",
            "breakdown": {"trend": "+25 (Сильный тренд)"},
        }

        result = analyzer._cap_low_adx_override(score_result, "bullish")

        self.assertEqual(result["total_score"], 69)
        self.assertEqual(result["decision"], "Watchlist")
        self.assertIn("A+ blocked", result["breakdown"]["trend"])

    def test_adx_formatter_shows_di_values(self):
        text = analyzer._format_adx({"adx_value": 17.2, "p_di": 22.1, "n_di": 18.4, "strength": "flat"})

        self.assertIn("ADX 17.20", text)
        self.assertIn("+DI 22.10 / -DI 18.40", text)
        self.assertIn("weak/neutral", text)

    def test_state_machine_diagnostic_respects_event_chronology(self):
        market_structure = MarketStructure(trend="bullish", confidence=80, reason="test")
        pd_result = {
            "valid_for_buy": True,
            "valid_for_sell": False,
            "zone": "discount",
        }

        status, result = analyzer._state_machine_diagnostic(
            "LONG",
            market_structure,
            pd_result,
            liquidity_map=None,
            sfp_data={"type": "bullish_sfp", "index": 10, "detected": True, "swept": True},
            context_structure=None,
            trigger_structure={"type": "bullish_bos", "index": 5, "quality_score": 95},
            fvg_test_data={"index": 6, "displacement_index": 7},
            fvg_data=[{
                "type": "bullish",
                "end_index": 4,
                "tested": True,
                "invalidated": False,
            }],
            current_price=100.0,
            current_bar=12,
        )

        self.assertFalse(result.signal_allowed)
        self.assertIn("invalidated", status)

    def test_state_machine_diagnostic_allows_ordered_full_sequence(self):
        market_structure = MarketStructure(trend="bullish", confidence=80, reason="test")
        pd_result = {
            "valid_for_buy": True,
            "valid_for_sell": False,
            "zone": "discount",
        }

        status, result = analyzer._state_machine_diagnostic(
            "LONG",
            market_structure,
            pd_result,
            liquidity_map=None,
            sfp_data={"type": "bullish_sfp", "index": 2, "detected": True, "swept": True},
            context_structure={"type": "bullish_choch", "index": 3, "quality_score": 90},
            trigger_structure={"type": "bullish_bos", "index": 4, "quality_score": 95},
            fvg_test_data={"index": 6, "displacement_index": 7},
            fvg_data=[{
                "type": "bullish",
                "end_index": 5,
                "tested": True,
                "invalidated": False,
            }],
            current_price=100.0,
            current_bar=7,
        )

        self.assertTrue(result.signal_allowed)
        self.assertIn("signal_ready", status)


if __name__ == "__main__":
    unittest.main()
