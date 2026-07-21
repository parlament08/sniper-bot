import json
import tempfile
import unittest
from pathlib import Path

from research.analyze_scan_history import analyze, candidate_id, main, resolve_input_files


class ScanHistoryAnalyticsTest(unittest.TestCase):
    def test_candidate_id_reads_top_level_scenario_scan_id(self):
        raw = {
            "scenario_scan": {
                "selected_scenario_id": "CAND-TOP",
                "selected_scenario": None,
            },
        }

        self.assertEqual(candidate_id(raw), "CAND-TOP")

    def test_candidate_id_falls_back_to_candidate_lists(self):
        raw = {
            "features": {
                "scenario_scan": {
                    "selected_scenario_id": None,
                    "selected_scenario": None,
                    "top_candidates": [{"candidate_id": "CAND-LIST"}],
                },
            },
        }

        self.assertEqual(candidate_id(raw), "CAND-LIST")

    def test_analyze_scan_history_outputs_core_tables_and_malformed_lines(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            journal = Path(tmpdir) / "scans_2026-07-17.jsonl"
            rows = [
                {
                    "record_type": "symbol_scan",
                    "timestamp": "2026-07-17T10:00:00+03:00",
                    "run_id": "run-1",
                    "symbol": "BTC",
                    "no_trade_reason": "neutral_htf",
                    "features": {
                        "htf_context": {
                            "direction": "neutral",
                            "reason": "ADX below neutral threshold",
                            "adx": 18.5,
                            "adx_threshold": 20,
                            "last_closed_4h": "2026-07-17 08:00:00",
                            "market_data_age_seconds": 11,
                            "protected_high": 650.0,
                            "protected_low": 600.0,
                            "swing_sequence": ["HH", "HL", "LH", "LL"],
                            "reason_flags": ["adx_below_threshold", "conflicting_swings"],
                        },
                        "market_structure_4h": {"trend": "neutral", "reason": "ADX low"},
                        "trend_4h": {"adx": 18.5},
                        "scenario_scan": {"selected_scenario": None, "candidate_counts": {"long_total": 0, "short_total": 0}},
                    },
                    "diagnostics": {"trigger_confirmed": False},
                },
                {
                    "record_type": "symbol_scan",
                    "timestamp": "2026-07-17T10:15:00+03:00",
                    "run_id": "run-2",
                    "symbol": "BTC",
                    "no_trade_reason": "risk_plan_block",
                    "features": {
                        "market_structure_4h": {"trend": "bullish", "reason": "HH/HL"},
                        "trend_4h": {"adx": 31.0},
                        "risk_plan": {
                            "valid": True,
                            "late_entry": True,
                            "entry_model": "fvg_midpoint",
                            "entry": 100.0,
                            "entry_distance_from_poi_atr": 0.0,
                            "stop_distance_percent": 0.005,
                            "rr_to_target_1": 21.5,
                            "target_model": "fallback_rr_target",
                            "reason": "Risk plan valid",
                        },
                        "premium_discount": {"price": 101.0},
                        "scenario_scan": {
                            "scenario_valid": True,
                            "signal_allowed": True,
                            "selected_direction": "LONG",
                            "selected_scenario": {
                                "candidate_id": "CAND-1",
                                "direction": "LONG",
                                "status": "complete",
                                "completed_steps": 10,
                                "risk_valid": True,
                                "events_used": [
                                    {"event_type": "FVG_CREATED"},
                                    {"event_type": "FVG_RETESTED"},
                                    {"event_type": "DISPLACEMENT_CONFIRMED"},
                                ],
                            },
                        },
                    },
                    "diagnostics": {
                        "trigger_confirmed": True,
                        "scenario_scan_valid": True,
                        "scenario_scan_signal_allowed": True,
                        "scenario_risk_valid": True,
                        "a_plus_delivery_allowed": True,
                    },
                },
                {
                    "record_type": "symbol_scan",
                    "timestamp": "2026-07-17T10:30:00+03:00",
                    "run_id": "run-3",
                    "symbol": "ETH",
                    "no_trade_reason": "scenario_waiting",
                    "features": {
                        "market_structure_4h": {"trend": "bullish", "reason": "HH/HL"},
                        "scenario_scan": {
                            "scenario_valid": False,
                            "signal_allowed": False,
                            "selected_direction": "LONG",
                            "selected_scenario": {
                                "candidate_id": "CAND-WAIT",
                                "direction": "LONG",
                                "status": "building",
                                "current_step": "liquidity_sweep_confirmed",
                                "next_expected_step": "CONFIRMED_TRIGGER_CONFIRMED",
                                "waiting_for": "confirmed bullish BOS after early CHOCH",
                                "completed_steps": 4,
                                "events_used": [
                                    {"event_type": "HTF_CONTEXT_CONFIRMED", "index": "2026-07-17 08:00:00"},
                                    {"event_type": "SFP_CONFIRMED", "index": "2026-07-17 10:00:00"},
                                ],
                            },
                        },
                        "trigger_scan": {
                            "trigger_confirmed": False,
                            "rejected_reason": "no_bullish_trigger_after_sfp_or_poi",
                        },
                    },
                    "diagnostics": {
                        "trigger_confirmed": False,
                        "scenario_scan_valid": False,
                        "scenario_scan_signal_allowed": False,
                    },
                    "trigger_diagnostics": {
                        "candidate_id": "CAND-WAIT",
                        "trigger_stage": "waiting_for_early_trigger",
                        "required_next_event": "CONFIRMED_TRIGGER_CONFIRMED",
                        "bars_waiting": 4,
                        "scans_waiting": 2,
                        "early_trigger_detected": False,
                        "confirmed_trigger_detected": False,
                        "missing_conditions": [
                            "choch_not_detected",
                            "displacement_quality_below_threshold",
                        ],
                        "last_observed_events": ["HTF_CONTEXT_CONFIRMED", "SFP_CONFIRMED"],
                        "near_miss": {
                            "closest_failed_condition": "displacement",
                            "condition_value": 0.49,
                            "condition_threshold": 0.5,
                            "near_miss_ratio": 0.98,
                        },
                    },
                },
                {
                    "record_type": "symbol_scan",
                    "timestamp": "2026-07-17T10:45:00+03:00",
                    "run_id": "run-4",
                    "symbol": "ETH",
                    "no_trade_reason": "scenario_waiting",
                    "features": {
                        "market_structure_4h": {"trend": "bullish", "reason": "HH/HL"},
                        "scenario_scan": {
                            "scenario_valid": False,
                            "signal_allowed": False,
                            "selected_direction": "LONG",
                            "selected_scenario": {
                                "candidate_id": "CAND-WAIT",
                                "direction": "LONG",
                                "status": "building",
                                "current_step": "liquidity_sweep_confirmed",
                                "next_expected_step": "CONFIRMED_TRIGGER_CONFIRMED",
                                "waiting_for": "confirmed bullish BOS after early CHOCH",
                                "completed_steps": 4,
                                "events_used": [
                                    {"event_type": "HTF_CONTEXT_CONFIRMED", "index": "2026-07-17 08:00:00"},
                                    {"event_type": "SFP_CONFIRMED", "index": "2026-07-17 10:00:00"},
                                ],
                            },
                        },
                        "trigger_scan": {
                            "trigger_confirmed": False,
                            "rejected_reason": "no_bullish_trigger_after_sfp_or_poi",
                        },
                    },
                    "diagnostics": {
                        "trigger_confirmed": False,
                        "scenario_scan_valid": False,
                        "scenario_scan_signal_allowed": False,
                    },
                    "trigger_diagnostics": {
                        "candidate_id": "CAND-WAIT",
                        "trigger_stage": "waiting_for_early_trigger",
                        "required_next_event": "CONFIRMED_TRIGGER_CONFIRMED",
                        "bars_waiting": 5,
                        "scans_waiting": 3,
                        "early_trigger_detected": False,
                        "confirmed_trigger_detected": False,
                        "missing_conditions": [
                            "choch_not_detected",
                            "displacement_quality_below_threshold",
                        ],
                        "last_observed_events": ["HTF_CONTEXT_CONFIRMED", "SFP_CONFIRMED"],
                        "near_miss": {
                            "closest_failed_condition": "displacement",
                            "condition_value": 0.49,
                            "condition_threshold": 0.5,
                            "near_miss_ratio": 0.98,
                        },
                    },
                },
                {
                    "record_type": "scenario_transition",
                    "timestamp": "2026-07-17T10:30:01+03:00",
                    "run_id": "run-3",
                    "symbol": "ETH",
                    "candidate_id": "CAND-WAIT",
                    "from_state": None,
                    "to_state": "WAITING_FOR_CONFIRMED_TRIGGER_CONFIRMED",
                    "event_type": "SFP_CONFIRMED",
                    "event_time": "2026-07-17 10:00:00",
                },
                {
                    "record_type": "telegram_delivery",
                    "timestamp": "2026-07-17T10:16:00+03:00",
                    "run_id": "run-2",
                    "message_type": "A_PLUS",
                    "sent": True,
                    "candidate_id": "CAND-1",
                    "symbol": "BTC",
                    "delivery_gate_result": {"allowed": True, "failed_gates": []},
                },
            ]
            journal.write_text(
                "\n".join(json.dumps(row) for row in rows) + "\n{bad json\n",
                encoding="utf-8",
            )

            summary, tables = analyze([journal])

        self.assertEqual(summary["file_count"], 1)
        self.assertEqual(summary["scan_runs"], 4)
        self.assertEqual(summary["symbol_scan_records"], 4)
        self.assertEqual(summary["telegram_delivered"], 1)
        self.assertEqual(summary["malformed_line_count"], 1)
        self.assertEqual(summary["no_trade_reason_counts"]["neutral_htf"], 1)
        self.assertEqual(tables["filter_funnel"][0]["true"], 4)
        self.assertEqual(tables["candidate_lifetime"][0]["candidate_id"], "CAND-1")
        self.assertTrue(tables["candidate_lifetime"][0]["risk_plan_became_valid"])
        self.assertTrue(tables["candidate_lifetime"][0]["a_plus_delivery_allowed"])
        self.assertTrue(tables["candidate_lifetime"][0]["telegram_delivery_happened"])
        trigger_loss_by_id = {row["candidate_id"]: row for row in tables["trigger_loss_report"]}
        self.assertEqual(trigger_loss_by_id["CAND-WAIT"]["required_next_event"], "CONFIRMED_TRIGGER_CONFIRMED")
        self.assertEqual(trigger_loss_by_id["CAND-WAIT"]["trigger_stage"], "waiting_for_early_trigger")
        self.assertEqual(trigger_loss_by_id["CAND-WAIT"]["waited_for_current_event_scans"], 2)
        self.assertEqual(trigger_loss_by_id["CAND-WAIT"]["waited_for_current_event_minutes"], 15.0)
        self.assertEqual(trigger_loss_by_id["CAND-WAIT"]["missing_conditions"], "choch_not_detected,displacement_quality_below_threshold")
        self.assertEqual(trigger_loss_by_id["CAND-WAIT"]["closest_failed_condition"], "displacement")
        self.assertEqual(trigger_loss_by_id["CAND-WAIT"]["near_miss_ratio"], 0.98)
        self.assertEqual(trigger_loss_by_id["CAND-WAIT"]["latest_transition_state"], "WAITING_FOR_CONFIRMED_TRIGGER_CONFIRMED")
        self.assertFalse(trigger_loss_by_id["CAND-WAIT"]["trigger_seen_ever"])
        self.assertEqual(summary["trigger_seen_candidate_count"], 1)
        self.assertEqual(summary["trigger_missing_condition_counts"]["choch_not_detected"], 1)
        self.assertEqual(tables["trigger_stage_distribution"][0]["trigger_stage"], "not_waiting_for_trigger")
        self.assertEqual(tables["missing_condition_counts"][0]["missing_condition"], "choch_not_detected")
        self.assertEqual(tables["near_miss_candidates"][0]["candidate_id"], "CAND-WAIT")
        self.assertEqual(tables["waiting_time_by_required_event"][0]["required_next_event"], "CONFIRMED_TRIGGER_CONFIRMED")
        self.assertEqual(tables["telegram_deliveries"][0]["message_kind"], "trade_alert")
        self.assertEqual(tables["telegram_deliveries"][0]["a_plus_delivery_allowed"], "true")
        self.assertEqual(tables["run_completeness"][0]["symbols_scanned_per_run"], 1)
        self.assertIn("cohort", tables["filter_funnel"][0])
        self.assertEqual(tables["htf_metrics_timeline"][0]["structure_reason"], "ADX below neutral threshold")
        self.assertEqual(tables["htf_metrics_timeline"][0]["last_closed_4h"], "2026-07-17 08:00:00")
        self.assertIn("adx_below_threshold", tables["htf_metrics_timeline"][0]["reason_flags"])
        self.assertIn("rr_above_20r", tables["late_entry_report"][0]["anomalies"])
        self.assertIn("fallback_target_used", tables["late_entry_report"][0]["anomalies"])
        self.assertEqual(tables["late_entry_incidents"][0]["candidate_id"], "CAND-1")

    def test_cli_writes_requested_report_files(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            journal = Path(tmpdir) / "scans_2026-07-17.jsonl"
            out_dir = Path(tmpdir) / "reports"
            journal.write_text(
                json.dumps(
                    {
                        "record_type": "symbol_scan",
                        "timestamp": "2026-07-17T10:00:00+03:00",
                        "run_id": "run-1",
                        "symbol": "ETH",
                        "no_trade_reason": "neutral_htf",
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            files = resolve_input_files([str(journal)])
            self.assertEqual(files, [journal])

            import sys

            old_argv = sys.argv
            try:
                sys.argv = [
                    "analyze_scan_history.py",
                    str(journal),
                    "--output-dir",
                    str(out_dir),
                    "--format",
                    "json,csv,html",
                ]
                self.assertEqual(main(), 0)
            finally:
                sys.argv = old_argv

            self.assertTrue((out_dir / "summary.json").exists())
            self.assertTrue((out_dir / "filter_funnel.csv").exists())
            self.assertTrue((out_dir / "trigger_loss_report.csv").exists())
            self.assertTrue((out_dir / "trigger_stage_distribution.csv").exists())
            self.assertTrue((out_dir / "missing_condition_counts.csv").exists())
            self.assertTrue((out_dir / "near_miss_candidates.csv").exists())
            self.assertTrue((out_dir / "waiting_time_by_required_event.csv").exists())
            self.assertTrue((out_dir / "telegram_deliveries.csv").exists())
            self.assertTrue((out_dir / "run_completeness.csv").exists())
            self.assertTrue((out_dir / "report.html").exists())

    def test_trigger_replay_detects_future_fvg_from_closed_candles(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            journal = Path(tmpdir) / "scans_2026-07-17.jsonl"
            rows = [
                {
                    "record_type": "symbol_scan",
                    "timestamp": "2026-07-17T10:00:00+03:00",
                    "run_id": "run-1",
                    "symbol": "SOL",
                    "candidate_id": "CAND-FVG",
                    "market_data_timestamp_15m": "2026-07-17 10:00:00",
                    "market_open_15m": 100.0,
                    "market_high_15m": 101.0,
                    "market_low_15m": 99.0,
                    "market_close_15m": 100.5,
                    "atr": 2.0,
                    "features": {
                        "scenario_scan": {
                            "selected_scenario": {
                                "candidate_id": "CAND-FVG",
                                "candidate_created_at": "2026-07-17 10:00:00",
                                "direction": "LONG",
                                "status": "waiting_for_confirmation",
                                "current_step": "early_trigger_confirmed",
                                "next_expected_step": "FVG_CREATED",
                                "completed_steps": 5,
                                "events_used": [
                                    {"event_type": "HTF_CONTEXT_CONFIRMED", "index": "-2"},
                                    {"event_type": "SFP_CONFIRMED", "index": "2026-07-17 09:45:00"},
                                    {"event_type": "EARLY_TRIGGER_CONFIRMED", "index": "2026-07-17 10:00:00"},
                                ],
                            }
                        }
                    },
                },
                {
                    "record_type": "symbol_scan",
                    "timestamp": "2026-07-17T10:15:00+03:00",
                    "run_id": "run-2",
                    "symbol": "SOL",
                    "candidate_id": "CAND-FVG",
                    "market_data_timestamp_15m": "2026-07-17 10:15:00",
                    "market_open_15m": 100.5,
                    "market_high_15m": 101.0,
                    "market_low_15m": 100.0,
                    "market_close_15m": 100.8,
                    "atr": 2.0,
                    "features": {"scenario_scan": {"selected_scenario": {"candidate_id": "CAND-FVG", "candidate_created_at": "2026-07-17 10:00:00", "direction": "LONG", "events_used": []}}},
                },
                {
                    "record_type": "symbol_scan",
                    "timestamp": "2026-07-17T10:30:00+03:00",
                    "run_id": "run-3",
                    "symbol": "SOL",
                    "candidate_id": "CAND-FVG",
                    "market_data_timestamp_15m": "2026-07-17 10:30:00",
                    "market_open_15m": 101.0,
                    "market_high_15m": 104.0,
                    "market_low_15m": 100.5,
                    "market_close_15m": 103.5,
                    "atr": 2.0,
                    "features": {"scenario_scan": {"selected_scenario": {"candidate_id": "CAND-FVG", "candidate_created_at": "2026-07-17 10:00:00", "direction": "LONG", "events_used": []}}},
                },
                {
                    "record_type": "symbol_scan",
                    "timestamp": "2026-07-17T10:45:00+03:00",
                    "run_id": "run-4",
                    "symbol": "SOL",
                    "candidate_id": "CAND-FVG",
                    "market_data_timestamp_15m": "2026-07-17 10:45:00",
                    "market_open_15m": 104.0,
                    "market_high_15m": 107.0,
                    "market_low_15m": 105.0,
                    "market_close_15m": 106.5,
                    "atr": 2.0,
                    "features": {"scenario_scan": {"selected_scenario": {"candidate_id": "CAND-FVG", "candidate_created_at": "2026-07-17 10:00:00", "direction": "LONG", "events_used": []}}},
                },
                {
                    "record_type": "symbol_scan",
                    "timestamp": "2026-07-17T11:00:00+03:00",
                    "run_id": "run-5",
                    "symbol": "SOL",
                    "candidate_id": "CAND-FVG",
                    "market_data_timestamp_15m": "2026-07-17 11:00:00",
                    "market_open_15m": 106.5,
                    "market_high_15m": 106.8,
                    "market_low_15m": 104.5,
                    "market_close_15m": 105.5,
                    "atr": 2.0,
                    "features": {"scenario_scan": {"selected_scenario": {"candidate_id": "CAND-FVG", "candidate_created_at": "2026-07-17 10:00:00", "direction": "LONG", "events_used": []}}},
                },
            ]
            journal.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")

            summary, tables = analyze([journal])

        replay = {
            row["component"]: row
            for row in tables["trigger_replay_report"]
            if row["candidate_id"] == "CAND-FVG"
        }
        self.assertTrue(replay["fvg_creation"]["detected_in_replay"])
        self.assertEqual(replay["fvg_creation"]["candidate_source"], "scenario")
        self.assertFalse(replay["fvg_creation"]["detected_live"])
        self.assertFalse(replay["fvg_creation"]["live_vs_replay_match"])
        self.assertEqual(replay["fvg_creation"]["first_seen_after_bars"], 3)
        self.assertTrue(replay["fvg_retest"]["detected_in_replay"])
        self.assertEqual(summary["trigger_replay"]["fvg_within_5_bars"], 1)


if __name__ == "__main__":
    unittest.main()
