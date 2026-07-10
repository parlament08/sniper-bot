import json
import tempfile
import unittest
from pathlib import Path

from research.analyze_journal import load_journal, summarize


class ResearchJournalTest(unittest.TestCase):
    def test_load_and_summarize_journal_directory(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "scans_2026-07-10.jsonl"
            path.write_text(
                "\n".join([
                    json.dumps({
                        "symbol": "BTC",
                        "decision": "Ignore",
                        "no_trade_reason": "neutral_htf",
                        "score": 0,
                        "features": {
                            "market_structure_4h": {"trend": "neutral", "reason": "ADX below neutral threshold", "confidence": 24},
                            "premium_discount": {"zone": "equilibrium", "zone_depth": "equilibrium", "range_timeframe": "4H"},
                            "sfp": {
                                "type": "bullish_sfp",
                                "quality_score": 84,
                                "liquidity_depth": 0.46,
                                "rejection_strength": 91,
                            },
                            "risk_plan": {"valid": False, "reason": "no logical liquidity target"},
                        },
                        "diagnostics": {"pd_valid": False, "sfp_present": True, "scenario_valid": False},
                    }),
                    json.dumps({
                        "symbol": "ADA",
                        "decision": "Watchlist",
                        "no_trade_reason": "context_only",
                        "score": 40,
                        "features": {
                            "context_1h": {"type": "bullish_bos", "quality_score": 95},
                            "trigger_15m": {"type": "bullish_bos", "quality_score": 91},
                            "premium_discount": {"zone": "discount", "zone_depth": "normal", "range_timeframe": "4H"},
                            "risk_plan": {"valid": True, "reason": "Risk plan valid", "rr_to_target_1": 2.4},
                        },
                        "diagnostics": {"pd_valid": True, "trigger_structure_aligned": True, "scenario_valid": True},
                    }),
                ]),
                encoding="utf-8",
            )

            df = load_journal(Path(tmpdir))
            summary = summarize(df)

        self.assertEqual(summary["rows"], 2)
        self.assertEqual(summary["decision_counts"]["Ignore"], 1)
        self.assertEqual(summary["decision_counts"]["Watchlist"], 1)
        self.assertEqual(summary["score_max"], 40.0)
        self.assertEqual(summary["features"]["market_structure_4h"]["trend_counts"]["neutral"], 1)
        self.assertEqual(summary["features"]["context_1h"]["detected"], 1)
        self.assertEqual(summary["features"]["trigger_15m"]["q90_plus"], 1)
        self.assertEqual(summary["features"]["sfp"]["strong_tier"], 1)
        self.assertEqual(summary["features"]["premium_discount"]["zone_counts"]["discount"], 1)
        self.assertEqual(summary["features"]["risk_plan"]["valid_counts"]["true"], 1)
        self.assertEqual(summary["gates"]["pd_valid"]["false"], 1)
        self.assertEqual(summary["score_by_symbol"]["ADA"]["score_max"], 40.0)


if __name__ == "__main__":
    unittest.main()
