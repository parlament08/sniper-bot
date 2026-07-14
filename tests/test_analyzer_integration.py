import os
import unittest
from unittest.mock import patch

import pandas as pd

os.environ.setdefault("GEMINI_API_KEY", "test-key")

import analyzer
from core.risk_plan import RiskPlan
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
        self.assertEqual(score_result["breakdown"]["liquidity_map"], "BSL: none | SSL: none")
        self.assertIs(analysis_data["liquidity_map"], liquidity_map)

    def test_analyze_symbol_snapshot_does_not_fetch_live_data(self):
        liquidity_map = {
            "nearest_buy_side": None,
            "nearest_sell_side": None,
            "strongest_buy_side": None,
            "strongest_sell_side": None,
        }

        with patch("analyzer.fetch_candles", side_effect=AssertionError("snapshot must not fetch")), \
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
            score_result, analysis_data = analyzer.analyze_symbol_snapshot(
                "BTC",
                self._df(freq="4h"),
                self._df(freq="1h"),
                self._df(freq="15min"),
                {},
            )

        self.assertEqual(score_result["total_score"], 0)
        self.assertEqual(analysis_data["direction"], "NEUTRAL")

    def test_prepare_calculates_rvol_on_direct_1h_candles(self):
        rvol_time_steps = []

        def fake_fetch(coin, timeframe, limit):
            freq = "4h" if timeframe == "4h" else "1h" if timeframe == "1h" else "15min"
            return self._df(freq=freq)

        def fake_rvol(df, period):
            rvol_time_steps.append(df.index[1] - df.index[0])
            return pd.Series(1.0, index=df.index)

        with patch("analyzer.fetch_candles", side_effect=fake_fetch), \
            patch("analyzer.calculate_ema", side_effect=lambda df, period: pd.Series(100.0, index=df.index)), \
            patch("analyzer.calculate_adx", side_effect=lambda df, period: pd.DataFrame({"adx": 25.0}, index=df.index)), \
            patch("analyzer.calculate_atr", side_effect=lambda df, period: pd.Series(1.0, index=df.index)), \
            patch("analyzer.calculate_rvol", side_effect=fake_rvol), \
            patch("analyzer.find_swings", side_effect=lambda df, left_bars=2, right_bars=2: self._swings(df)), \
            patch("analyzer.build_liquidity_map", return_value=None), \
            patch(
                "analyzer.evaluate_market_structure",
                return_value=MarketStructure(trend="neutral", confidence=20, reason="test neutral"),
            ):
            analyzer.prepare_and_analyze("BTC", {})

        self.assertIn(pd.Timedelta(hours=1), rvol_time_steps)
        self.assertIn(pd.Timedelta(minutes=15), rvol_time_steps)

    def test_prepare_caps_high_score_when_state_machine_gate_fails(self):
        def fake_fetch(coin, timeframe, limit):
            freq = "4h" if timeframe == "4h" else "1h" if timeframe == "1h" else "15min"
            return self._df(freq=freq)

        high_score = {
            "raw_score": 92,
            "total_score": 92,
            "decision": "A+",
            "breakdown": {
                "trend": "+25",
                "structure": "+30",
                "liquidity": "+20",
                "fvg": "+15",
                "volume": "+10",
                "macro": "0",
                "premium_discount": "OK",
            },
            "diagnostics": {},
        }
        low_score = {
            "raw_score": 0,
            "total_score": 0,
            "decision": "Ignore",
            "breakdown": {},
            "diagnostics": {},
        }
        blocked_state = type(
            "BlockedState",
            (),
            {
                "signal_allowed": False,
                "state": type("State", (), {"value": "waiting_for_liquidity_sweep"})(),
                "missing_steps": ["liquidity_sweep_confirmed"],
            },
        )()
        valid_risk = RiskPlan(
            direction="LONG",
            entry=100.0,
            stop_loss=98.0,
            invalidation_level=98.2,
            target_1=106.0,
            target_2=None,
            risk_per_unit=2.0,
            rr_to_target_1=3.0,
            rr_to_target_2=None,
            stop_distance_percent=2.0,
            entry_distance_from_poi_atr=0.1,
            valid=True,
            reason="Risk plan valid",
            entry_model="fvg_midpoint",
            stop_model="structural_invalidation",
            target_model="nearest_liquidity",
        )

        with patch("analyzer.fetch_candles", side_effect=fake_fetch), \
            patch("analyzer.calculate_ema", side_effect=lambda df, period: pd.Series(100.0, index=df.index)), \
            patch("analyzer.calculate_adx", side_effect=lambda df, period: pd.DataFrame({"adx": 25.0}, index=df.index)), \
            patch("analyzer.calculate_atr", side_effect=lambda df, period: pd.Series(1.0, index=df.index)), \
            patch("analyzer.calculate_rvol", side_effect=lambda df, period: pd.Series(1.0, index=df.index)), \
            patch("analyzer.find_swings", side_effect=lambda df, left_bars=2, right_bars=2: self._swings(df)), \
            patch("analyzer.build_liquidity_map", return_value=None), \
            patch("analyzer.detect_structure_break", return_value=None), \
            patch("analyzer.find_fvg", return_value=[]), \
            patch(
                "analyzer.evaluate_market_structure",
                return_value=MarketStructure(trend="bullish", confidence=80, reason="test bullish"),
            ), \
            patch("analyzer._resolve_premium_discount", return_value={"valid_for_buy": True, "valid_for_sell": False, "zone": "discount"}), \
            patch("analyzer.calculate_setup_score", side_effect=[high_score.copy(), low_score.copy()]), \
            patch("analyzer._state_machine_diagnostic", return_value=("waiting_for_liquidity_sweep C25", blocked_state)), \
            patch("analyzer.build_risk_plan", return_value=valid_risk):
            score_result, _ = analyzer.prepare_and_analyze("BTC", {})

        self.assertEqual(score_result["total_score"], 69)
        self.assertEqual(score_result["decision"], "Watchlist")
        self.assertEqual(score_result["no_trade_reason"], "waiting_for_liquidity_sweep")
        self.assertEqual(score_result["diagnostics"]["scenario_scan_reason"], "waiting_for_liquidity_sweep")
        self.assertTrue(score_result["diagnostics"]["risk_geometry_valid"])
        self.assertFalse(score_result["diagnostics"]["scenario_risk_valid"])

    def test_prepare_caps_high_score_when_risk_plan_is_invalid(self):
        def fake_fetch(coin, timeframe, limit):
            freq = "4h" if timeframe == "4h" else "1h" if timeframe == "1h" else "15min"
            return self._df(freq=freq)

        high_score = {
            "raw_score": 92,
            "total_score": 92,
            "decision": "A+",
            "breakdown": {
                "trend": "+25",
                "structure": "+30",
                "liquidity": "+20",
                "fvg": "+15",
                "volume": "+10",
                "macro": "0",
                "premium_discount": "OK",
            },
            "diagnostics": {},
        }
        low_score = {
            "raw_score": 0,
            "total_score": 0,
            "decision": "Ignore",
            "breakdown": {},
            "diagnostics": {},
        }
        allowed_state = type(
            "AllowedState",
            (),
            {
                "signal_allowed": True,
                "state": type("State", (), {"value": "signal_ready"})(),
                "missing_steps": [],
            },
        )()
        invalid_risk = RiskPlan(
            direction="LONG",
            entry=100.0,
            stop_loss=98.0,
            invalidation_level=98.2,
            target_1=102.0,
            target_2=None,
            risk_per_unit=2.0,
            rr_to_target_1=1.0,
            rr_to_target_2=None,
            stop_distance_percent=2.0,
            entry_distance_from_poi_atr=0.1,
            valid=False,
            reason="RR to target 1 below minimum",
            entry_model="fvg_midpoint",
            stop_model="structural_invalidation",
            target_model="nearest_liquidity",
        )

        with patch("analyzer.fetch_candles", side_effect=fake_fetch), \
            patch("analyzer.calculate_ema", side_effect=lambda df, period: pd.Series(100.0, index=df.index)), \
            patch("analyzer.calculate_adx", side_effect=lambda df, period: pd.DataFrame({"adx": 25.0}, index=df.index)), \
            patch("analyzer.calculate_atr", side_effect=lambda df, period: pd.Series(1.0, index=df.index)), \
            patch("analyzer.calculate_rvol", side_effect=lambda df, period: pd.Series(1.0, index=df.index)), \
            patch("analyzer.find_swings", side_effect=lambda df, left_bars=2, right_bars=2: self._swings(df)), \
            patch("analyzer.build_liquidity_map", return_value=None), \
            patch("analyzer.detect_structure_break", return_value=None), \
            patch("analyzer.find_fvg", return_value=[]), \
            patch(
                "analyzer.evaluate_market_structure",
                return_value=MarketStructure(trend="bullish", confidence=80, reason="test bullish"),
            ), \
            patch("analyzer._resolve_premium_discount", return_value={"valid_for_buy": True, "valid_for_sell": False, "zone": "discount"}), \
            patch("analyzer.calculate_setup_score", side_effect=[high_score.copy(), low_score.copy()]), \
            patch("analyzer._state_machine_diagnostic", return_value=("signal_ready C100", allowed_state)), \
            patch("analyzer.build_risk_plan", return_value=invalid_risk):
            score_result, analysis_data = analyzer.prepare_and_analyze("BTC", {})

        self.assertEqual(score_result["total_score"], 69)
        self.assertEqual(score_result["decision"], "Watchlist")
        self.assertEqual(score_result["no_trade_reason"], "waiting_for_liquidity_sweep")
        self.assertEqual(score_result["diagnostics"]["scenario_scan_reason"], "waiting_for_liquidity_sweep")
        self.assertFalse(score_result["diagnostics"]["risk_geometry_valid"])
        self.assertFalse(score_result["diagnostics"]["scenario_risk_valid"])
        self.assertIs(analysis_data["risk_plan"], invalid_risk)

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

    def test_send_telegram_blocks_splits_oversized_single_block(self):
        header = ["<b>header</b>"]
        block = "\n".join(["line-" + ("A" * 70), "line-" + ("B" * 70), "line-" + ("C" * 70)])
        sent_messages = []

        with patch("analyzer.send_telegram_alert", side_effect=sent_messages.append):
            analyzer.send_telegram_blocks(header, [block], max_length=120)

        self.assertGreater(len(sent_messages), 1)
        self.assertTrue(all(len(message) <= 120 for message in sent_messages))

    def test_a_plus_delivery_gate_requires_full_scenario_pipeline(self):
        score_result = {
            "total_score": 90,
            "diagnostics": {
                "scenario_scan_valid": True,
                "scenario_scan_signal_allowed": True,
                "trigger_confirmed": True,
                "scenario_risk_valid": True,
            },
        }
        analysis_data = {
            "scenario_scan": {
                "selected_scenario": {
                    "scenario_valid": True,
                    "signal_allowed": True,
                    "risk_valid": True,
                    "events_used": [
                        {"event_type": "FVG_CREATED"},
                        {"event_type": "FVG_RETESTED"},
                        {"event_type": "DISPLACEMENT_CONFIRMED"},
                    ],
                }
            }
        }

        self.assertTrue(analyzer.is_a_plus_delivery_allowed(score_result, analysis_data, in_kill_zone=True))

        analysis_data["scenario_scan"]["selected_scenario"]["events_used"] = [
            {"event_type": "FVG_CREATED"},
            {"event_type": "DISPLACEMENT_CONFIRMED"},
        ]
        gate = analyzer._annotate_a_plus_delivery_gate(score_result, analysis_data, in_kill_zone=True)

        self.assertFalse(gate["allowed"])
        self.assertIn("fvg_retested", gate["failed_gates"])
        self.assertFalse(score_result["diagnostics"]["a_plus_delivery_allowed"])

    def test_run_summary_separates_active_and_historical_confirmed_triggers(self):
        record = analyzer._build_run_summary_record(
            run_id="run-1",
            started_at="2026-07-14T10:00:00+03:00",
            finished_at="2026-07-14T10:01:00+03:00",
            duration_seconds=60.123,
            report_mode="FULL",
            session={"in_kill_zone": True},
            symbol_results=[
                {
                    "symbol": "FET",
                    "success": True,
                    "decision": "Watchlist",
                    "diagnostics": {
                        "sfp_present": True,
                        "early_trigger_confirmed": False,
                        "trigger_confirmed": False,
                        "scenario_scan_valid": False,
                        "scenario_scan_signal_allowed": False,
                        "risk_geometry_valid": True,
                        "scenario_risk_valid": False,
                        "a_plus_delivery_allowed": False,
                    },
                    "analysis_data": {
                        "global_trigger_scan": {"trigger_confirmed": True},
                        "scenario_scan": {
                            "top_candidates": [
                                {"status": "invalidated", "selection_eligible": False},
                                {"status": "waiting_for_confirmation", "selection_eligible": True},
                            ],
                            "selected_scenario": {
                                "events_used": [{"event_type": "FVG_CREATED"}],
                            },
                        },
                    },
                },
                {
                    "symbol": "WLD",
                    "success": True,
                    "decision": "Ignore",
                    "diagnostics": {
                        "sfp_present": True,
                        "early_trigger_confirmed": True,
                        "trigger_confirmed": False,
                    },
                    "analysis_data": {"global_trigger_scan": {"trigger_confirmed": False}},
                },
                {
                    "symbol": "BAD",
                    "success": False,
                    "error": {"symbol": "BAD", "error": "fetch_failed"},
                },
            ],
            errors=[{"symbol": "BAD", "error": "fetch_failed"}],
        )

        self.assertEqual(record["record_type"], "run_summary")
        self.assertEqual(record["symbols_success"], 2)
        self.assertEqual(record["symbols_failed"], 1)
        self.assertEqual(record["watchlist_count"], 1)
        self.assertEqual(record["ignore_count"], 1)
        self.assertEqual(record["active_confirmed_trigger_count"], 0)
        self.assertEqual(record["historical_confirmed_trigger_count"], 1)
        self.assertEqual(record["risk_geometry_valid_count"], 1)
        self.assertEqual(record["scenario_risk_valid_count"], 0)
        self.assertEqual(record["invalidated_scenario_count"], 1)
        self.assertEqual(record["selection_ineligible_count"], 1)
        self.assertEqual(record["fvg_created_count"], 1)
        self.assertEqual(record["app_version"], analyzer.APP_VERSION)

    def test_format_scenario_scan_humanizes_waiting_and_no_valid_reasons(self):
        waiting_scan = {
            "reason": "waiting_for_liquidity_sweep",
            "selected_scenario": {
                "status": "waiting_for_confirmation",
                "direction": "SHORT",
                "waiting_for": "waiting_for_liquidity_sweep",
                "completed_steps": 2,
                "total_steps": 10,
            },
        }
        no_valid_scan = {
            "reason": "htf_direction_conflict",
            "selected_scenario": None,
        }

        self.assertEqual(
            analyzer._format_scenario_scan(waiting_scan),
            "waiting for liquidity sweep / SFP | 2/10 steps",
        )
        self.assertEqual(
            analyzer._format_scenario_scan(no_valid_scan),
            "no valid scenario — HTF direction conflict",
        )

    def test_format_trigger_and_scenario_scan_show_early_trigger_progress(self):
        trigger_scan = {
            "expected_direction": "LONG",
            "selected_trigger": None,
            "early_trigger": {
                "type": "bullish_early_choch",
                "quality_score": 68,
                "index": "2026-01-01 10:15:00",
                "trigger_stage": "early",
                "is_early": True,
            },
            "early_trigger_confirmed": True,
            "trigger_confirmed": False,
            "rejected_reason": "confirmed_trigger_missing",
            "waiting_for": "confirmed bullish BOS after early CHOCH",
            "sfp_index": "2026-01-01 10:00:00",
        }
        scenario_scan = {
            "reason": "waiting_for_confirmed_bullish_bos",
            "selected_scenario": {
                "status": "waiting_for_confirmation",
                "direction": "LONG",
                "waiting_for": "confirmed bullish BOS after early CHOCH",
                "completed_steps": 4,
                "total_steps": 10,
            },
        }

        self.assertEqual(
            analyzer._format_trigger_scan(trigger_scan),
            "early bullish CHOCH Q68 after SFP — waiting for confirmed BOS",
        )
        self.assertEqual(
            analyzer._format_scenario_scan(scenario_scan),
            "waiting for confirmed bullish BOS | 4/10 steps",
        )

    def test_candidate_scoped_trigger_scan_mirrors_selected_scenario(self):
        scenario_scan = {
            "reason": "waiting_for_confirmed_bullish_bos",
            "selected_direction": "LONG",
            "selected_scenario": {
                "candidate_id": "LONG_SFP_CONFIRMED_100_1",
                "status": "waiting_for_confirmation",
                "direction": "LONG",
                "trigger_scan": {
                    "candidate_id": "LONG_SFP_CONFIRMED_100_1",
                    "expected_direction": "LONG",
                    "early_trigger": {"type": "bullish_early_choch", "quality_score": 88, "index": "110"},
                    "confirmed_trigger": None,
                    "selected_trigger": None,
                    "sfp_index": "100",
                    "early_trigger_confirmed": True,
                    "trigger_confirmed": False,
                    "rejected_reason": "confirmed_trigger_missing",
                    "waiting_for": "confirmed bullish BOS after early CHOCH",
                },
            },
        }

        scoped = analyzer._candidate_scoped_trigger_scan(scenario_scan, "LONG")

        self.assertEqual(scoped, scenario_scan["selected_scenario"]["trigger_scan"])
        self.assertTrue(scoped["early_trigger_confirmed"])
        self.assertFalse(scoped["trigger_confirmed"])
        self.assertEqual(
            analyzer._format_trigger_scan(scoped),
            "early bullish CHOCH Q88 after SFP — waiting for confirmed BOS",
        )

    def test_candidate_scoped_trigger_scan_falls_back_when_no_selected_scenario(self):
        scenario_scan = {
            "reason": "htf_direction_conflict",
            "selected_direction": None,
            "selected_scenario": None,
        }

        scoped = analyzer._candidate_scoped_trigger_scan(scenario_scan, "LONG")

        self.assertFalse(scoped["early_trigger_confirmed"])
        self.assertFalse(scoped["trigger_confirmed"])
        self.assertEqual(scoped["rejected_reason"], "htf_direction_conflict")

    def test_format_trigger_and_scenario_scan_show_confirmed_after_early(self):
        trigger_scan = {
            "expected_direction": "LONG",
            "selected_trigger": {"type": "bullish_bos", "quality_score": 84, "index": "120"},
            "confirmed_trigger": {"type": "bullish_bos", "quality_score": 84, "index": "120"},
            "early_trigger": {"type": "bullish_early_choch", "quality_score": 88, "index": "110"},
            "sfp_index": "100",
            "early_trigger_confirmed": True,
            "trigger_confirmed": True,
            "waiting_for": "bullish FVG after confirmed BOS",
        }
        scenario_scan = {
            "reason": "waiting_for_bullish_fvg_after_confirmed_bos",
            "selected_scenario": {
                "status": "waiting_for_confirmation",
                "direction": "LONG",
                "current_step": "confirmed_trigger_confirmed",
                "next_expected_step": "FVG_CREATED",
                "waiting_for": "bullish FVG after confirmed BOS",
                "completed_steps": 5,
                "total_steps": 10,
            },
        }

        self.assertEqual(
            analyzer._format_trigger_scan(trigger_scan),
            "confirmed bullish BOS Q84 after early CHOCH",
        )
        self.assertEqual(
            analyzer._format_scenario_scan(scenario_scan),
            "waiting for bullish FVG | 5/10 steps",
        )

    def test_format_trigger_scan_shows_confirmed_debug_rejection(self):
        trigger_scan = {
            "expected_direction": "SHORT",
            "early_trigger": {"type": "bearish_early_choch", "quality_score": 96, "index": "110"},
            "sfp_index": "100",
            "early_trigger_confirmed": True,
            "trigger_confirmed": False,
            "rejected_reason": "confirmed_trigger_missing",
            "waiting_for": "confirmed bearish BOS after early CHOCH",
            "confirmed_trigger_debug": {
                "candidate_bos_count": 1,
                "candidate_choch_count": 0,
                "final_reason": "quality_below_min",
                "rejected_candidates": [
                    {"type": "bearish_bos", "index": "120", "quality_score": 62, "rejected_reason": "quality_below_min"}
                ],
            },
        }

        self.assertEqual(
            analyzer._format_trigger_scan(trigger_scan),
            "early bearish CHOCH Q96 after SFP — waiting for confirmed BOS | candidates 1 rejected: quality below min",
        )

    def test_detects_bullish_early_trigger_candidate_after_anchor(self):
        index = pd.date_range("2026-01-01 10:00:00", periods=7, freq="15min")
        df = pd.DataFrame(
            {
                "open": [100.0, 100.1, 100.4, 100.3, 100.2, 101.0, 101.4],
                "high": [100.5, 100.7, 101.2, 100.8, 100.9, 101.8, 101.9],
                "low": [99.8, 100.0, 100.1, 100.0, 100.1, 100.8, 101.0],
                "close": [100.2, 100.3, 100.6, 100.2, 100.4, 101.65, 101.6],
                "atr": [1.0] * 7,
                "rvol": [1.0, 1.0, 1.1, 1.0, 1.0, 1.35, 1.0],
            },
            index=index,
        )

        candidates = analyzer._detect_early_trigger_candidates(
            df,
            {"type": "bullish_sfp", "index": index[0]},
            None,
            max_bars=6,
        )

        bullish = [item for item in candidates if item["type"] == "bullish_early_choch"]
        self.assertTrue(bullish)
        self.assertEqual(bullish[0]["index"], index[5])
        self.assertTrue(bullish[0]["is_early"])
        self.assertEqual(bullish[0]["trigger_stage"], "early")

    def test_early_trigger_detector_ignores_break_before_anchor(self):
        index = pd.date_range("2026-01-01 10:00:00", periods=7, freq="15min")
        df = pd.DataFrame(
            {
                "open": [100.0, 100.1, 100.4, 100.3, 100.2, 101.0, 101.1],
                "high": [100.5, 100.7, 101.2, 100.8, 100.9, 101.1, 101.2],
                "low": [99.8, 100.0, 100.1, 100.0, 100.1, 100.8, 100.9],
                "close": [100.2, 100.3, 100.6, 100.2, 100.4, 101.0, 101.05],
                "atr": [1.0] * 7,
                "rvol": [1.0, 1.0, 1.1, 1.0, 1.0, 1.35, 1.0],
            },
            index=index,
        )

        candidates = analyzer._detect_early_trigger_candidates(
            df,
            {"type": "bullish_sfp", "index": index[5]},
            None,
            max_bars=2,
        )

        self.assertEqual(candidates, [])

    def test_detects_bearish_early_trigger_candidate_after_anchor(self):
        index = pd.date_range("2026-01-01 10:00:00", periods=7, freq="15min")
        df = pd.DataFrame(
            {
                "open": [100.0, 99.9, 99.4, 99.7, 99.8, 99.0, 98.6],
                "high": [100.2, 100.0, 99.6, 99.9, 99.9, 99.2, 98.9],
                "low": [99.5, 99.3, 98.8, 99.2, 99.1, 98.2, 98.1],
                "close": [99.8, 99.7, 99.4, 99.8, 99.6, 98.35, 98.4],
                "atr": [1.0] * 7,
                "rvol": [1.0, 1.0, 1.1, 1.0, 1.0, 1.35, 1.0],
            },
            index=index,
        )

        candidates = analyzer._detect_early_trigger_candidates(
            df,
            {"type": "bearish_sfp", "index": index[0]},
            None,
            max_bars=6,
        )

        bearish = [item for item in candidates if item["type"] == "bearish_early_choch"]
        self.assertTrue(bearish)
        self.assertEqual(bearish[0]["index"], index[5])
        self.assertTrue(bearish[0]["is_early"])
        self.assertEqual(bearish[0]["trigger_stage"], "early")

    def test_premium_discount_poi_uses_last_closed_timestamp_instead_of_synthetic_index(self):
        last_closed = pd.Series({"close": 100.0}, name=pd.Timestamp("2026-01-01 12:15:00"))

        events = analyzer._build_scenario_events(
            "LONG",
            {"trend": "bullish", "confidence": 65},
            {"valid_for_buy": True, "valid_for_sell": False, "zone_strength": 75},
            None,
            {},
            None,
            [],
            None,
            None,
            last_closed,
        )

        poi_events = [event for event in events if event.event_type == "POI_TOUCHED"]
        self.assertEqual(len(poi_events), 1)
        self.assertEqual(poi_events[0].index, pd.Timestamp("2026-01-01 12:15:00"))
        self.assertNotEqual(poi_events[0].index, -1)

    def test_premium_discount_without_real_timestamp_does_not_create_poi_event(self):
        events = analyzer._build_scenario_events(
            "LONG",
            {"trend": "bullish", "confidence": 65},
            {"valid_for_buy": True, "valid_for_sell": False, "zone_strength": 75},
            None,
            {},
            None,
            [],
            None,
            None,
            None,
        )

        self.assertFalse(any(event.event_type == "POI_TOUCHED" for event in events))

    def test_trigger_scan_chain_adds_scenario_scoped_sfp_anchor(self):
        last_closed = pd.Series({"close": 100.0}, name=pd.Timestamp("2026-01-01 10:00:00"))
        trigger_scan = {
            "expected_direction": "SHORT",
            "sfp_index": pd.Timestamp("2026-01-01 01:00:00"),
            "early_trigger": {
                "type": "bearish_early_choch",
                "index": pd.Timestamp("2026-01-01 02:00:00"),
                "quality_score": 82,
            },
            "selected_trigger": {
                "type": "bearish_bos",
                "index": pd.Timestamp("2026-01-01 03:00:00"),
                "quality_score": 97,
            },
            "confirmed_trigger": {
                "type": "bearish_bos",
                "index": pd.Timestamp("2026-01-01 03:00:00"),
                "quality_score": 97,
            },
            "trigger_confirmed": True,
            "early_trigger_confirmed": True,
        }
        events = analyzer._build_scenario_events(
            "SHORT",
            {"trend": "bearish", "confidence": 65},
            {"valid_for_buy": False, "valid_for_sell": True, "zone_strength": 75},
            {"type": "bullish_sfp", "index": pd.Timestamp("2026-01-01 01:00:00"), "quality_score": 80},
            trigger_scan,
            None,
            [],
            None,
            None,
            last_closed,
        )
        scenario = analyzer.scan_scenarios(
            events=events,
            expected_direction="SHORT",
            htf_structure={"trend": "bearish"},
            premium_discount={"valid_for_sell": True},
        ).selected_scenario

        self.assertIsNotNone(scenario)
        self.assertEqual(scenario.anchor_type, "SFP_CONFIRMED")
        self.assertEqual(str(scenario.anchor_index), "2026-01-01 01:00:00")
        self.assertEqual(scenario.completed_steps, 5)
        self.assertTrue(scenario.trigger_scan["trigger_confirmed"])
        self.assertEqual(scenario.trigger_scan["confirmed_trigger"]["type"], "bearish_bos")

    def test_dashboard_block_shows_no_trade_reason_gates_and_sweep_label(self):
        score_result = {
            "total_score": 0,
            "decision": "Ignore",
            "no_trade_reason": "pd_block",
            "diagnostics": {
                "pd_valid": False,
                "sfp_present": True,
                "trigger_structure_aligned": False,
                "fvg_test_present": False,
            },
            "breakdown": {
                "trend": "+10 (test)",
                "adx": "ADX 20.00 | weak/neutral",
                "structure": "0 (Нет валидной структуры)",
                "liquidity": "+20 (SFP Q80 D0.73 R86)",
                "liquidity_map": "BSL: none | SSL: none",
                "fvg": "0 (FVG close invalidated после retest)",
                "volume": "0 (RVOL n/a)",
                "premium_discount": "BLOCK (4H equilibrium shallow)",
                "trigger_scan": "waiting — no bullish trigger after SFP/POI",
                "scenario_scan": "waiting for bullish CHOCH/BOS after SFP | 3/10 steps",
                "state_machine": "waiting_for_liquidity_sweep C20 (2/8, next: sweep)",
                "macro": "0 (mixed)",
            },
        }
        analysis_data = {
            "direction": "LONG",
            "trend_data": {"is_bullish": True, "adx_value": 20.0, "strength": "flat"},
            "market_structure": MarketStructure(trend="bullish", confidence=55, reason="test"),
        }

        block = analyzer._build_dashboard_block("BTC", score_result, analysis_data, "Ignore", in_kz=True)

        self.assertIn("NO TRADE — P/D block", block)
        self.assertIn("Sweep/SFP", block)
        self.assertIn("Trigger Scan", block)
        self.assertIn("waiting — no bullish trigger after SFP/POI", block)
        self.assertIn("Scenario Scan", block)
        self.assertIn("waiting for bullish CHOCH/BOS after SFP", block)
        self.assertIn("🚧 Gates:", block)
        self.assertIn("P/D FAIL", block)

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
                "quality_score": 90,
                "age_bars": 2,
                "retest_count": 1,
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
                "quality_score": 90,
                "age_bars": 2,
                "retest_count": 1,
            }],
            current_price=100.0,
            current_bar=7,
        )

        self.assertTrue(result.signal_allowed)
        self.assertIn("signal_ready", status)

    def test_state_machine_ignores_old_low_quality_fvg_as_scenario_event(self):
        market_structure = MarketStructure(trend="bullish", confidence=80, reason="test")
        pd_result = {
            "valid_for_buy": True,
            "valid_for_sell": False,
            "zone": "discount",
        }
        old_fvg = {
            "type": "bullish",
            "end_index": 1,
            "tested": True,
            "invalidated": False,
            "quality_score": 40,
            "age_bars": 203,
            "retest_count": 8,
        }
        annotated = analyzer._annotate_scenario_fvgs([old_fvg])

        status, result = analyzer._state_machine_diagnostic(
            "LONG",
            market_structure,
            pd_result,
            liquidity_map=None,
            sfp_data={"type": "bullish_sfp", "index": 2, "detected": True, "swept": True},
            context_structure={"type": "bullish_choch", "index": 3, "quality_score": 90},
            trigger_structure={"type": "bullish_bos", "index": 4, "quality_score": 95},
            fvg_test_data={"index": 6, "displacement_index": 7},
            fvg_data=annotated,
            current_price=100.0,
            current_bar=7,
        )

        self.assertFalse(annotated[0]["scenario_valid"])
        self.assertEqual(annotated[0]["scenario_reject_reason"], "fvg_quality_below_min")
        self.assertFalse(result.signal_allowed)
        self.assertIn("waiting_for_fvg", status)
        self.assertNotIn("Unexpected fvg_created", status)

    def test_trigger_debug_reports_stale_low_quality_fvg(self):
        debug = analyzer._build_trigger_debug(
            "LONG",
            {"type": "bullish_bos", "index": 10, "quality_score": 88},
            {"type": "bullish_sfp", "index": 8},
            {"index": 9},
            analyzer._annotate_scenario_fvgs([{
                "type": "bullish",
                "end_index": 4,
                "tested": True,
                "invalidated": False,
                "quality_score": 40,
                "age_bars": 203,
                "retest_count": 8,
            }]),
            {"valid_for_buy": True},
        )

        self.assertEqual(debug["trigger_rejected_reason"], "fvg_quality_below_min")
        self.assertEqual(debug["fvg_rejected_reason"], "fvg_quality_below_min")

    def test_trigger_debug_reports_missing_15m_trigger(self):
        debug = analyzer._build_trigger_debug(
            "LONG",
            None,
            {"type": "bullish_sfp", "index": 8},
            None,
            [],
            {"valid_for_buy": True},
        )

        self.assertEqual(debug["trigger_rejected_reason"], "no_bullish_trigger_after_sfp_or_poi")

    def test_long_debug_keeps_bearish_trigger_as_opposite(self):
        debug = analyzer._build_trigger_debug(
            "LONG",
            {"type": "bearish_bos", "index": 110, "quality_score": 81},
            {"type": "bullish_sfp", "index": 100},
            None,
            [],
            {"valid_for_buy": True},
            long_trigger_candidate=None,
            short_trigger_candidate={"type": "bearish_bos", "index": 110, "quality_score": 81},
        )

        self.assertIsNone(debug["selected_trigger"])
        self.assertEqual(debug["opposite_trigger"]["type"], "bearish_bos")
        self.assertEqual(debug["trigger_rejected_reason"], "no_bullish_trigger_after_sfp_or_poi")
        self.assertFalse(debug["trigger_confirmed"])

    def test_short_debug_keeps_bullish_trigger_as_opposite(self):
        debug = analyzer._build_trigger_debug(
            "SHORT",
            {"type": "bullish_bos", "index": 110, "quality_score": 85},
            {"type": "bearish_sfp", "index": 100},
            None,
            [],
            {"valid_for_sell": True},
            long_trigger_candidate={"type": "bullish_bos", "index": 110, "quality_score": 85},
            short_trigger_candidate=None,
        )

        self.assertIsNone(debug["selected_trigger"])
        self.assertEqual(debug["opposite_trigger"]["type"], "bullish_bos")
        self.assertEqual(debug["trigger_rejected_reason"], "no_bearish_trigger_after_sfp_or_poi")
        self.assertFalse(debug["trigger_confirmed"])

    def test_long_debug_confirms_bullish_trigger_after_sfp(self):
        debug = analyzer._build_trigger_debug(
            "LONG",
            {"type": "bullish_bos", "index": 110, "quality_score": 88},
            {"type": "bullish_sfp", "index": 100},
            None,
            [],
            {"valid_for_buy": True},
            long_trigger_candidate={"type": "bullish_bos", "index": 110, "quality_score": 88},
            short_trigger_candidate=None,
        )

        self.assertEqual(debug["selected_trigger"]["type"], "bullish_bos")
        self.assertTrue(debug["trigger_confirmed"])
        self.assertIsNone(debug["trigger_rejected_reason"])

    def test_long_debug_rejects_bullish_trigger_before_sfp(self):
        debug = analyzer._build_trigger_debug(
            "LONG",
            {"type": "bullish_bos", "index": 90, "quality_score": 88},
            {"type": "bullish_sfp", "index": 100},
            None,
            [],
            {"valid_for_buy": True},
            long_trigger_candidate={"type": "bullish_bos", "index": 90, "quality_score": 88},
            short_trigger_candidate=None,
        )

        self.assertIsNone(debug["selected_trigger"])
        self.assertEqual(debug["trigger_rejected_reason"], "trigger_before_sfp")
        self.assertFalse(debug["trigger_confirmed"])

    def test_ape_like_debug_reports_no_bullish_trigger_with_opposite_bearish(self):
        debug = analyzer._build_trigger_debug(
            "LONG",
            {"type": "bearish_bos", "index": 110, "quality_score": 81},
            {"type": "bullish_sfp", "index": 100},
            None,
            [],
            {"valid_for_buy": True},
            long_trigger_candidate=None,
            short_trigger_candidate={"type": "bearish_bos", "index": 110, "quality_score": 81},
        )

        self.assertIsNone(debug["selected_trigger"])
        self.assertEqual(debug["opposite_trigger"]["type"], "bearish_bos")
        self.assertEqual(debug["trigger_rejected_reason"], "no_bullish_trigger_after_sfp_or_poi")

    def test_neutral_debug_reports_candidate_without_opposite(self):
        debug = analyzer._build_trigger_debug(
            "NEUTRAL",
            {"type": "bearish_choch", "index": 100, "quality_score": 84},
            {"type": "bullish_sfp", "index": 95},
            None,
            [],
            None,
            short_trigger_candidate={"type": "bearish_choch", "index": 100, "quality_score": 84},
        )

        self.assertIsNone(debug["selected_trigger"])
        self.assertIsNone(debug["opposite_trigger"])
        self.assertEqual(debug["candidate_trigger"]["type"], "bearish_choch")
        self.assertEqual(debug["trigger_rejected_reason"], "no_trade_direction")
        self.assertIn("candidate: bearish CHOCH Q84", analyzer._format_trigger_debug(debug))
        self.assertNotIn("opposite", analyzer._format_trigger_debug(debug))

    def test_trigger_scan_format_waits_for_bos_or_choch_and_reports_opposite(self):
        scan = analyzer.scan_post_anchor_trigger(
            expected_direction="LONG",
            sfp={"type": "bullish_sfp", "index": 100},
            long_trigger_candidate={"type": "bullish_bos", "index": 90, "quality_score": 97},
            short_trigger_candidate={"type": "bearish_bos", "index": 110, "quality_score": 93},
        )

        self.assertEqual(
            analyzer._format_trigger_scan(scan),
            "waiting — no bullish CHOCH/BOS after SFP/POI | opposite: bearish BOS Q93",
        )


if __name__ == "__main__":
    unittest.main()
