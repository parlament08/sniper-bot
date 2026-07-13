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
                            "trigger_debug": {
                                "trigger_rejected_reason": "no_bullish_trigger_after_sfp_or_poi",
                                "expected_direction": "LONG",
                                "selected_trigger": None,
                                "opposite_trigger": {"type": "bearish_bos", "quality_score": 81},
                                "long_trigger_candidate": None,
                                "short_trigger_candidate": {"type": "bearish_bos", "quality_score": 81},
                                "trigger_confirmed": False,
                                "fvg_scenario_valid": False,
                                "fvg_rejected_reason": "fvg_quality_below_min",
                            },
                            "trigger_scan": {
                                "expected_direction": "LONG",
                                "selected_trigger": None,
                                "pre_sfp_trigger": {"type": "bullish_bos", "quality_score": 97},
                                "candidate_trigger": {"type": "bullish_bos", "quality_score": 97},
                                "opposite_trigger": {"type": "bearish_bos", "quality_score": 81},
                                "anchor_index": "2026-07-10 10:00:00",
                                "trigger_confirmed": False,
                                "rejected_reason": "trigger_before_sfp",
                            },
                            "scenario_scan": {
                                "selected_direction": "LONG",
                                "signal_allowed": False,
                                "scenario_valid": False,
                                "reason": "waiting_for_bullish_choch",
                                "selected_scenario": {
                                    "direction": "LONG",
                                    "status": "waiting_for_confirmation",
                                    "completion_ratio": 0.3,
                                    "completed_steps": 3,
                                    "quality_score": 84,
                                },
                                "best_long_scenario": {
                                    "status": "waiting_for_confirmation",
                                    "invalidated_reason": None,
                                },
                                "best_short_scenario": {
                                    "status": "invalidated",
                                    "invalidated_reason": "htf_direction_conflict",
                                },
                            },
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
                            "trigger_debug": {
                                "selected_trigger": {"type": "bullish_bos", "quality_score": 91},
                                "long_trigger_candidate": {"type": "bullish_bos", "quality_score": 91},
                                "trigger_rejected_reason": None,
                                "expected_direction": "LONG",
                                "trigger_confirmed": True,
                                "fvg_scenario_valid": True,
                            },
                            "trigger_scan": {
                                "expected_direction": "LONG",
                                "selected_trigger": {"type": "bullish_bos", "quality_score": 91},
                                "candidate_trigger": {"type": "bullish_bos", "quality_score": 91},
                                "anchor_index": "2026-07-10 11:00:00",
                                "trigger_confirmed": True,
                                "rejected_reason": None,
                            },
                            "scenario_scan": {
                                "selected_direction": "LONG",
                                "signal_allowed": True,
                                "scenario_valid": True,
                                "reason": "complete_scenario",
                                "selected_scenario": {
                                    "direction": "LONG",
                                    "status": "complete",
                                    "completion_ratio": 1.0,
                                    "completed_steps": 10,
                                    "quality_score": 91,
                                },
                                "best_long_scenario": {
                                    "status": "complete",
                                    "invalidated_reason": None,
                                },
                                "best_short_scenario": {
                                    "status": "invalidated",
                                    "invalidated_reason": "htf_direction_conflict",
                                },
                            },
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
        self.assertEqual(summary["features"]["trigger_debug"]["rejected_reason_counts"]["no_bullish_trigger_after_sfp_or_poi"], 1)
        self.assertEqual(summary["features"]["trigger_debug"]["selected_type_counts"]["bullish_bos"], 1)
        self.assertEqual(summary["features"]["trigger_debug"]["opposite_type_counts"]["bearish_bos"], 1)
        self.assertEqual(summary["features"]["trigger_debug"]["trigger_confirmed_counts"]["true"], 1)
        self.assertEqual(summary["features"]["trigger_debug"]["fvg_rejected_reason_counts"]["fvg_quality_below_min"], 1)
        self.assertEqual(summary["features"]["trigger_scan"]["rejected_reason_counts"]["trigger_before_sfp"], 1)
        self.assertEqual(summary["features"]["trigger_scan"]["selected_type_counts"]["bullish_bos"], 1)
        self.assertEqual(summary["features"]["trigger_scan"]["pre_sfp_type_counts"]["bullish_bos"], 1)
        self.assertEqual(summary["features"]["trigger_scan"]["candidate_type_counts"]["bullish_bos"], 2)
        self.assertEqual(summary["features"]["trigger_scan"]["anchor_present_counts"]["true"], 2)
        self.assertEqual(summary["features"]["scenario_scan"]["reason_counts"]["complete_scenario"], 1)
        self.assertEqual(summary["features"]["scenario_scan"]["selected_status_counts"]["complete"], 1)
        self.assertEqual(summary["features"]["scenario_scan"]["signal_allowed_counts"]["true"], 1)
        self.assertEqual(summary["gates"]["pd_valid"]["false"], 1)
        self.assertEqual(summary["score_by_symbol"]["ADA"]["score_max"], 40.0)


if __name__ == "__main__":
    unittest.main()
