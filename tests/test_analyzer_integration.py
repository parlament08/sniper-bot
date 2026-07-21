import os
import unittest
from unittest.mock import patch

import pandas as pd

os.environ.setdefault("GEMINI_API_KEY", "test-key")

import analyzer
from core.risk_plan import RiskPlan
from core.scenario_scanner import ScenarioEvent, ScenarioScanResult, ScenarioScannerOutput
from core.structure import MarketStructure


class AnalyzerIntegrationTest(unittest.TestCase):
    def setUp(self):
        analyzer.reset_scenario_runtime_state()

    def tearDown(self):
        analyzer.reset_scenario_runtime_state()

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

    def test_runtime_state_export_is_isolated_json_snapshot(self):
        analyzer._SCENARIO_RUNTIME_STATE[("SOL", "CAND-1")] = {
            "runtime_update_count": 1,
            "first_index": pd.Timestamp("2026-01-01 10:00:00"),
        }
        analyzer._SCENARIO_TRANSITION_STATE[("SOL", "CAND-1")] = "waiting_for_confirmation"

        snapshot = analyzer.export_scenario_runtime_state()
        snapshot["scenario_runtime_state"][0]["state"]["runtime_update_count"] = 99

        self.assertEqual(analyzer._SCENARIO_RUNTIME_STATE[("SOL", "CAND-1")]["runtime_update_count"], 1)
        text = analyzer.json.dumps(snapshot, ensure_ascii=False, sort_keys=True)
        self.assertIn("scenario_runtime_state", text)

    def test_runtime_state_import_is_isolated_and_replaceable(self):
        snapshot = {
            "schema_version": 1,
            "scenario_runtime_state": [
                {
                    "symbol": "SOL",
                    "candidate_id": "CAND-1",
                    "state": {"runtime_update_count": 3, "first_index": "2026-01-01T07:00:00Z"},
                }
            ],
            "scenario_transition_state": [
                {"symbol": "SOL", "candidate_id": "CAND-1", "state": "early_trigger_confirmed"}
            ],
        }

        analyzer.import_scenario_runtime_state(snapshot)
        snapshot["scenario_runtime_state"][0]["state"]["runtime_update_count"] = 99

        self.assertEqual(analyzer._SCENARIO_RUNTIME_STATE[("SOL", "CAND-1")]["runtime_update_count"], 3)
        self.assertEqual(analyzer._SCENARIO_TRANSITION_STATE[("SOL", "CAND-1")], "early_trigger_confirmed")

    def test_runtime_state_roundtrip_preserves_hash(self):
        analyzer._SCENARIO_RUNTIME_STATE[("INJ", "CAND-1")] = {"runtime_update_count": 2}
        analyzer._SCENARIO_TRANSITION_STATE[("INJ", "CAND-1")] = "waiting_for_confirmation"
        snapshot = analyzer.export_scenario_runtime_state()
        expected_hash = analyzer.scenario_runtime_state_hash(snapshot)

        analyzer.reset_scenario_runtime_state()
        analyzer.import_scenario_runtime_state(snapshot)

        self.assertEqual(analyzer.scenario_runtime_state_hash(), expected_hash)

    def test_runtime_state_context_restores_globals_on_exception(self):
        original = {
            "schema_version": 1,
            "scenario_runtime_state": [
                {"symbol": "BTC", "candidate_id": "BASE", "state": {"runtime_update_count": 7}}
            ],
            "scenario_transition_state": [],
        }
        isolated = {
            "schema_version": 1,
            "scenario_runtime_state": [
                {"symbol": "BTC", "candidate_id": "TEMP", "state": {"runtime_update_count": 1}}
            ],
            "scenario_transition_state": [],
        }
        analyzer.import_scenario_runtime_state(original)
        expected = analyzer.export_scenario_runtime_state()

        with self.assertRaises(RuntimeError):
            with analyzer.scenario_runtime_state(isolated, persist=False):
                analyzer._SCENARIO_RUNTIME_STATE[("BTC", "TEMP")]["runtime_update_count"] = 2
                raise RuntimeError("boom")

        self.assertEqual(analyzer.export_scenario_runtime_state(), expected)

    def test_neutral_htf_builds_shadow_candidate_without_a_plus(self):
        liquidity_map = {
            "nearest_buy_side": {"price": 110.0},
            "nearest_sell_side": {"price": 96.0},
        }
        trigger = {
            "type": "bullish_bos",
            "index": pd.Timestamp("2026-01-01 10:00:00"),
            "level": 101.0,
            "quality_score": 82,
        }
        shadow = analyzer._build_shadow_candidate(
            symbol="BNB",
            htf_context={"direction": "neutral"},
            market_structure=MarketStructure(trend="neutral", confidence=22, reason="ADX below neutral threshold"),
            context_break_1h=None,
            trigger_break_15m=trigger,
            long_trigger_candidate=trigger,
            short_trigger_candidate=None,
            sfp_data={"type": "bullish_sfp", "index": pd.Timestamp("2026-01-01 09:30:00"), "level": 98.0},
            premium_discount_data=None,
            liquidity_map=liquidity_map,
            current_price=102.0,
            atr=2.0,
            risk_plan=None,
            scenario_scan=None,
            created_at="2026-01-01 10:15:00",
        )

        self.assertIsNotNone(shadow)
        self.assertEqual(shadow["shadow_tier"], "B")
        self.assertEqual(shadow["shadow_direction"], "LONG")
        self.assertEqual(shadow["htf_context_class"], "neutral")
        self.assertIn("htf_not_directionally_supportive", shadow["shadow_rejection_reasons"])

    def test_shadow_candidate_id_is_stable_across_repeated_scan_timestamp(self):
        liquidity_map = {"nearest_buy_side": {"price": 110.0}, "nearest_sell_side": {"price": 96.0}}
        trigger = {"type": "bullish_bos", "index": "2026-01-01 10:00:00", "level": 101.0, "quality_score": 82}
        first = analyzer._build_shadow_candidate(
            symbol="BNB",
            htf_context={"direction": "neutral"},
            market_structure=MarketStructure(trend="neutral", confidence=20, reason="neutral"),
            context_break_1h=None,
            trigger_break_15m=trigger,
            long_trigger_candidate=trigger,
            short_trigger_candidate=None,
            sfp_data={"type": "bullish_sfp", "index": "2026-01-01 09:30:00", "level": 98.0},
            premium_discount_data=None,
            liquidity_map=liquidity_map,
            current_price=102.0,
            atr=2.0,
            created_at="2026-01-01 10:15:00",
        )
        second = analyzer._build_shadow_candidate(
            symbol="BNB",
            htf_context={"direction": "neutral"},
            market_structure=MarketStructure(trend="neutral", confidence=20, reason="neutral"),
            context_break_1h=None,
            trigger_break_15m=trigger,
            long_trigger_candidate=trigger,
            short_trigger_candidate=None,
            sfp_data={"type": "bullish_sfp", "index": "2026-01-01 09:30:00", "level": 98.0},
            premium_discount_data=None,
            liquidity_map=liquidity_map,
            current_price=102.0,
            atr=2.0,
            created_at="2026-01-01 10:30:00",
        )

        self.assertEqual(first["shadow_candidate_id"], second["shadow_candidate_id"])

    def test_shadow_candidate_does_not_relax_production_delivery_gate(self):
        score_result = {
            "total_score": 92,
            "diagnostics": {
                "scenario_scan_valid": False,
                "scenario_scan_signal_allowed": False,
                "trigger_confirmed": True,
                "scenario_risk_valid": False,
            },
        }
        analysis_data = {
            "scenario_scan": None,
            "shadow_candidate": {"shadow_tier": "B", "shadow_candidate_id": "SHADOW_BNB_LONG_1"},
        }

        gate = analyzer._a_plus_delivery_gate(score_result, analysis_data, in_kill_zone=True)

        self.assertFalse(gate["allowed"])
        self.assertIn("scenario_valid", gate["failed_gates"])
        self.assertIn("signal_allowed", gate["failed_gates"])

    def test_trigger_diagnostics_explains_waiting_for_early_trigger(self):
        scenario = ScenarioScanResult(
            direction="LONG",
            status="waiting_for_confirmation",
            current_step="liquidity_sweep_confirmed",
            next_expected_step="EARLY_TRIGGER_CONFIRMED",
            signal_allowed=False,
            scenario_valid=False,
            completion_ratio=0.4,
            completed_steps=3,
            total_steps=10,
            quality_score=70,
            candidate_id="CAND-EARLY",
            market_age_bars=5,
            runtime_update_count=2,
            events_used=[
                ScenarioEvent(
                    "SFP_CONFIRMED",
                    "bullish",
                    pd.Timestamp("2026-01-01 10:00:00"),
                    payload={"type": "bullish_sfp", "index": "2026-01-01 10:00:00", "quality_score": 90},
                )
            ],
            trigger_scan={
                "expected_direction": "LONG",
                "candidate_trigger": {
                    "type": "bullish_choch",
                    "index": "2026-01-01 10:30:00",
                    "quality_score": 61,
                    "displacement_ratio": 0.49,
                    "rvol": 1.8,
                    "close_position": 0.7,
                },
                "early_trigger": None,
                "confirmed_trigger": None,
                "trigger_confirmed": False,
                "early_trigger_confirmed": False,
                "rejected_reason": "early_trigger_quality_below_min",
                "waiting_for": "bullish CHOCH/BOS after SFP/POI",
            },
        )
        scan = ScenarioScannerOutput(
            best_long_scenario=scenario,
            best_short_scenario=None,
            selected_scenario=scenario,
            selected_direction="LONG",
            signal_allowed=False,
            scenario_valid=False,
            reason="waiting_for_bullish_choch_or_bos",
        )

        diagnostics = analyzer._build_trigger_diagnostics(
            {"diagnostics": {}},
            {
                "scenario_scan": scan,
                "sfp_data": {"type": "bullish_sfp", "index": "2026-01-01 10:00:00"},
                "trigger_break_15m": scenario.trigger_scan["candidate_trigger"],
                "fvg_candidates": [],
                "current_price": 100.0,
                "atr": 1.0,
                "last_closed_15m": pd.Series({"close": 100.0}, name=pd.Timestamp("2026-01-01 11:15:00")),
                "scan_interval": 15.0,
            },
            session=type("Session", (), {"in_kill_zone": True})(),
        )

        self.assertEqual(diagnostics["trigger_stage"], "waiting_for_early_trigger")
        self.assertIn("displacement_quality_below_threshold", diagnostics["missing_conditions"])
        self.assertIn("fvg_not_created", diagnostics["missing_conditions"])
        self.assertEqual(diagnostics["near_miss"]["closest_failed_condition"], "displacement")
        self.assertAlmostEqual(diagnostics["near_miss"]["near_miss_ratio"], 0.98)
        self.assertEqual(diagnostics["bars_waiting"], 5)
        self.assertEqual(diagnostics["scans_waiting"], 2)

    def test_trigger_diagnostics_explains_waiting_for_confirmed_trigger(self):
        scenario = ScenarioScanResult(
            direction="LONG",
            status="waiting_for_confirmation",
            current_step="early_trigger_confirmed",
            next_expected_step="CONFIRMED_TRIGGER_CONFIRMED",
            signal_allowed=False,
            scenario_valid=False,
            completion_ratio=0.5,
            completed_steps=4,
            total_steps=10,
            quality_score=75,
            candidate_id="CAND-CONFIRM",
            events_used=[
                ScenarioEvent("SFP_CONFIRMED", "bullish", "2026-01-01 10:00:00", payload={"type": "bullish_sfp", "index": "2026-01-01 10:00:00"}),
                ScenarioEvent("EARLY_TRIGGER_CONFIRMED", "bullish", "2026-01-01 10:15:00", payload={"type": "bullish_early_choch", "index": "2026-01-01 10:15:00", "quality_score": 91}),
            ],
            trigger_scan={
                "expected_direction": "LONG",
                "early_trigger": {"type": "bullish_early_choch", "index": "2026-01-01 10:15:00", "quality_score": 91, "displacement_ratio": 0.8, "rvol": 1.4, "close_position": 0.8},
                "confirmed_trigger": None,
                "trigger_confirmed": False,
                "early_trigger_confirmed": True,
                "rejected_reason": "confirmed_trigger_missing",
                "waiting_for": "confirmed bullish BOS after early CHOCH",
                "confirmed_trigger_debug": {
                    "final_reason": "quality_below_min",
                    "break_level": 101.0,
                    "checked_candles": [
                        {"index": "2026-01-01 10:30:00", "close": 100.8, "breaks_level": False, "close_position": 0.7, "displacement_ratio": 0.7, "rvol": 1.4}
                    ],
                    "rejected_candidates": [
                        {"type": "bullish_bos", "index": "2026-01-01 10:30:00", "quality_score": 68, "rejected_reason": "quality_below_min"}
                    ],
                },
            },
        )
        scan = ScenarioScannerOutput(
            best_long_scenario=scenario,
            best_short_scenario=None,
            selected_scenario=scenario,
            selected_direction="LONG",
            signal_allowed=False,
            scenario_valid=False,
            reason="waiting_for_confirmed_bullish_bos",
        )

        diagnostics = analyzer._build_trigger_diagnostics(
            {"diagnostics": {}},
            {
                "scenario_scan": scan,
                "fvg_candidates": [],
                "current_price": 100.0,
                "atr": 1.0,
                "last_closed_15m": pd.Series({"close": 100.0}, name=pd.Timestamp("2026-01-01 10:45:00")),
                "scan_interval": 15.0,
            },
            session=type("Session", (), {"in_kill_zone": True})(),
        )

        self.assertEqual(diagnostics["trigger_stage"], "waiting_for_confirmed_trigger")
        self.assertTrue(diagnostics["early_trigger_detected"])
        self.assertFalse(diagnostics["confirmed_trigger_detected"])
        self.assertIn("bos_quality_below_threshold", diagnostics["missing_conditions"])
        self.assertIn("close_beyond_structure_missing", diagnostics["missing_conditions"])
        self.assertEqual(diagnostics["near_miss"]["closest_failed_condition"], "BOS")
        self.assertAlmostEqual(diagnostics["near_miss"]["near_miss_ratio"], round(68 / 70, 4))

    def test_trigger_diagnostics_do_not_affect_production_delivery_gate(self):
        score_result = {
            "total_score": 92,
            "diagnostics": {
                "scenario_scan_valid": False,
                "scenario_scan_signal_allowed": False,
                "trigger_confirmed": False,
                "scenario_risk_valid": False,
            },
        }
        analysis_data = {
            "scenario_scan": None,
            "trigger_diagnostics": {
                "missing_conditions": [],
                "near_miss": {"near_miss_ratio": 0.99},
            },
        }

        gate = analyzer._a_plus_delivery_gate(score_result, analysis_data, in_kill_zone=True)

        self.assertFalse(gate["allowed"])
        self.assertIn("trigger_confirmed", gate["failed_gates"])

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

    def test_analyze_symbol_snapshot_does_not_mutate_input_dataframes(self):
        liquidity_map = {
            "nearest_buy_side": None,
            "nearest_sell_side": None,
            "strongest_buy_side": None,
            "strongest_sell_side": None,
        }
        df_4h = self._df(freq="4h")
        df_1h = self._df(freq="1h")
        df_15m = self._df(freq="15min")
        original_4h = df_4h.copy(deep=True)
        original_1h = df_1h.copy(deep=True)
        original_15m = df_15m.copy(deep=True)

        def run_once():
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
                return analyzer.analyze_symbol_snapshot(
                    "BTC",
                    df_4h,
                    df_1h,
                    df_15m,
                    {},
                    analysis_time=pd.Timestamp("2026-01-01 12:00:00+03:00"),
                )

        first_score, first_analysis = run_once()
        second_score, second_analysis = run_once()

        pd.testing.assert_frame_equal(df_4h, original_4h)
        pd.testing.assert_frame_equal(df_1h, original_1h)
        pd.testing.assert_frame_equal(df_15m, original_15m)
        self.assertEqual(first_score, second_score)
        self.assertEqual(first_analysis["htf_context"], second_analysis["htf_context"])

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

    def test_prepare_does_not_cap_high_score_when_diagnostic_state_machine_gate_fails(self):
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
        complete_scenario = ScenarioScanResult(
            direction="LONG",
            status="complete",
            current_step="signal_allowed",
            next_expected_step=None,
            signal_allowed=True,
            scenario_valid=True,
            completion_ratio=1.0,
            completed_steps=10,
            total_steps=10,
            quality_score=92,
            risk_valid=True,
            candidate_id="LONG_SFP_2026-01-01T10:00:00",
            events_used=[
                ScenarioEvent("FVG_CREATED", "LONG", pd.Timestamp("2026-01-01 10:30:00")),
                ScenarioEvent("FVG_RETESTED", "LONG", pd.Timestamp("2026-01-01 10:45:00")),
                ScenarioEvent("DISPLACEMENT_CONFIRMED", "LONG", pd.Timestamp("2026-01-01 11:00:00")),
            ],
        )
        complete_scan = ScenarioScannerOutput(
            best_long_scenario=complete_scenario,
            best_short_scenario=None,
            selected_scenario=complete_scenario,
            selected_direction="LONG",
            signal_allowed=True,
            scenario_valid=True,
            reason="signal_allowed",
            long_candidates=[complete_scenario],
            top_candidates=[complete_scenario],
            selected_scenario_id=complete_scenario.candidate_id,
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
            patch("analyzer.scan_scenarios", return_value=complete_scan), \
            patch("analyzer.build_risk_plan", return_value=valid_risk):
            score_result, _ = analyzer.prepare_and_analyze("BTC", {})

        self.assertEqual(score_result["total_score"], 92)
        self.assertEqual(score_result["decision"], "A+")
        self.assertNotIn("no_trade_reason", score_result)
        self.assertEqual(score_result["diagnostics"]["scenario_scan_reason"], "signal_allowed")
        self.assertFalse(score_result["diagnostics"]["state_machine_allowed"])
        self.assertTrue(score_result["diagnostics"]["risk_geometry_valid"])
        self.assertTrue(score_result["diagnostics"]["scenario_risk_valid"])

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

    def test_a_plus_delivery_gate_can_bypass_kill_zone_only_for_complete_setup(self):
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

        with patch.object(analyzer, "SEND_A_PLUS_OUTSIDE_KZ", True):
            gate = analyzer._a_plus_delivery_gate(score_result, analysis_data, in_kill_zone=False)
        self.assertTrue(gate["allowed"])
        self.assertTrue(gate["kill_zone_bypassed"])
        self.assertFalse(gate["gates"]["in_kill_zone"])
        self.assertTrue(gate["gates"]["kill_zone_gate"])

        with patch.object(analyzer, "SEND_A_PLUS_OUTSIDE_KZ", False):
            gate = analyzer._a_plus_delivery_gate(score_result, analysis_data, in_kill_zone=False)
        self.assertFalse(gate["allowed"])
        self.assertIn("kill_zone_gate", gate["failed_gates"])

        analysis_data["scenario_scan"]["selected_scenario"]["events_used"] = [{"event_type": "FVG_CREATED"}]
        with patch.object(analyzer, "SEND_A_PLUS_OUTSIDE_KZ", True):
            gate = analyzer._a_plus_delivery_gate(score_result, analysis_data, in_kill_zone=False)
        self.assertFalse(gate["allowed"])
        self.assertIn("fvg_retested", gate["failed_gates"])

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

    def test_run_summary_watchlist_count_uses_final_decision(self):
        record = analyzer._build_run_summary_record(
            run_id="run-2",
            started_at="2026-01-01T00:00:00Z",
            finished_at="2026-01-01T00:01:00Z",
            duration_seconds=60,
            report_mode="HUNT",
            session={},
            symbol_results=[
                {
                    "symbol": "AAA",
                    "success": True,
                    "decision": "Ignore",
                    "context_decision": "Watchlist",
                    "final_decision": "Ignore",
                    "diagnostics": {},
                    "analysis_data": {},
                },
                {
                    "symbol": "BBB",
                    "success": True,
                    "decision": "Watchlist",
                    "context_decision": "A+",
                    "final_decision": "Watchlist",
                    "diagnostics": {},
                    "analysis_data": {},
                },
            ],
            errors=[],
        )

        self.assertEqual(record["watchlist_count"], 1)
        self.assertEqual(record["context_watchlist_count"], 1)
        self.assertEqual(record["ignore_count"], 1)
        self.assertEqual(record["app_version"], analyzer.APP_VERSION)

    def test_build_metadata_includes_commit_config_hash_and_build_time(self):
        metadata = analyzer._build_metadata()

        self.assertEqual(metadata["app_version"], analyzer.APP_VERSION)
        self.assertIn("git_commit", metadata)
        self.assertRegex(metadata["config_hash"], r"^[0-9a-f]{16}$")
        self.assertRegex(metadata["code_hash"], r"^[0-9a-f]{16}$")
        self.assertTrue(metadata["build_time"])

    def test_code_hash_can_be_overridden_for_deploy_fingerprint(self):
        with patch.dict(os.environ, {"CODE_HASH": "deploy-build-123"}):
            self.assertEqual(analyzer._code_hash(), "deploy-build-123")

    def test_analysis_error_includes_exception_traceback(self):
        try:
            raise KeyError(0)
        except Exception as exc:
            error = analyzer._analysis_error("LTC", "risk_plan", exc, "run-err")

        self.assertEqual(error["symbol"], "LTC")
        self.assertEqual(error["stage"], "risk_plan")
        self.assertEqual(error["exception_type"], "KeyError")
        self.assertIn("0", error["exception_message"])
        self.assertIn("KeyError", error["traceback"])
        self.assertEqual(error["run_id"], "run-err")

    def test_run_summary_reports_status_coverage_and_audit_eligibility(self):
        with patch.object(analyzer, "COINS_LIST", ["AAA", "BBB"]):
            success_record = analyzer._build_run_summary_record(
                run_id="run-success",
                started_at="2026-01-01T00:00:00Z",
                finished_at="2026-01-01T00:01:00Z",
                duration_seconds=60,
                report_mode="HUNT",
                session={},
                symbol_results=[
                    {"symbol": "AAA", "success": True, "decision": "Ignore", "diagnostics": {}, "analysis_data": {}},
                    {"symbol": "BBB", "success": True, "decision": "Ignore", "diagnostics": {}, "analysis_data": {}},
                ],
                errors=[],
            )
            partial_record = analyzer._build_run_summary_record(
                run_id="run-partial",
                started_at="2026-01-01T00:00:00Z",
                finished_at="2026-01-01T00:01:00Z",
                duration_seconds=60,
                report_mode="HUNT",
                session={},
                symbol_results=[
                    {"symbol": "AAA", "success": True, "decision": "Ignore", "diagnostics": {}, "analysis_data": {}},
                    {
                        "symbol": "BBB",
                        "success": False,
                        "error": analyzer._analysis_error("BBB", "prepare_and_analyze", "NoAnalysisData", "run-partial"),
                    },
                ],
                errors=[analyzer._analysis_error("BBB", "prepare_and_analyze", "NoAnalysisData", "run-partial")],
            )
            failed_record = analyzer._build_run_summary_record(
                run_id="run-failed",
                started_at="2026-01-01T00:00:00Z",
                finished_at="2026-01-01T00:01:00Z",
                duration_seconds=60,
                report_mode="HUNT",
                session={},
                symbol_results=[
                    {
                        "symbol": "AAA",
                        "success": False,
                        "error": analyzer._analysis_error("AAA", "symbol_analysis", "NoAnalysisData", "run-failed"),
                    },
                    {
                        "symbol": "BBB",
                        "success": False,
                        "error": analyzer._analysis_error("BBB", "symbol_analysis", "NoAnalysisData", "run-failed"),
                    },
                ],
                errors=[
                    analyzer._analysis_error("AAA", "symbol_analysis", "NoAnalysisData", "run-failed"),
                    analyzer._analysis_error("BBB", "symbol_analysis", "NoAnalysisData", "run-failed"),
                ],
            )

        self.assertEqual(success_record["run_status"], "SUCCESS")
        self.assertEqual(success_record["coverage_percent"], 100.0)
        self.assertTrue(success_record["audit_eligible"])
        self.assertEqual(partial_record["run_status"], "PARTIAL_SUCCESS")
        self.assertEqual(partial_record["coverage_percent"], 50.0)
        self.assertFalse(partial_record["audit_eligible"])
        self.assertEqual(failed_record["run_status"], "FAILED")
        self.assertEqual(failed_record["coverage_percent"], 0.0)
        self.assertFalse(failed_record["audit_eligible"])

    def test_telegram_delivery_record_contains_attempt_status(self):
        record = analyzer._build_telegram_delivery_record(
            run_id="run-tg",
            message_type="A_PLUS",
            attempted=True,
            sent=False,
            error="rate_limited",
            status_code=429,
            message_length=512,
            in_kill_zone=False,
            outside_kz_delivery_enabled=True,
            kill_zone_bypassed=True,
        )

        self.assertEqual(record["record_type"], "telegram_delivery")
        self.assertEqual(record["run_id"], "run-tg")
        self.assertEqual(record["message_type"], "A_PLUS")
        self.assertTrue(record["attempted"])
        self.assertFalse(record["sent"])
        self.assertEqual(record["error"], "rate_limited")
        self.assertEqual(record["status_code"], 429)
        self.assertFalse(record["in_kill_zone"])
        self.assertTrue(record["outside_kz_delivery_enabled"])
        self.assertTrue(record["kill_zone_bypassed"])

    def test_event_snapshot_includes_timing_metadata(self):
        event = {
            "type": "bullish_bos",
            "index": pd.Timestamp("2026-01-01 10:00:00"),
            "detected_at": pd.Timestamp("2026-01-01 10:15:00"),
            "historical_only": True,
        }

        snapshot = analyzer._event_snapshot(event)

        self.assertEqual(snapshot["event_time"], "2026-01-01 10:00:00")
        self.assertEqual(snapshot["detected_at"], "2026-01-01 10:15:00")
        self.assertEqual(snapshot["detection_delay_seconds"], 900.0)
        self.assertTrue(snapshot["is_reconstructed"])

    def test_scenario_transition_record_is_written_once_per_state_change(self):
        analyzer._SCENARIO_TRANSITION_STATE.clear()
        candidate = ScenarioScanResult(
            direction="LONG",
            status="waiting_for_confirmation",
            current_step="early_trigger_confirmed",
            next_expected_step="CONFIRMED_TRIGGER_CONFIRMED",
            signal_allowed=False,
            scenario_valid=False,
            completion_ratio=0.4,
            completed_steps=4,
            total_steps=10,
            quality_score=80,
            candidate_id="LONG_SFP_1",
            events_used=[
                ScenarioEvent(
                    "EARLY_TRIGGER_CONFIRMED",
                    "LONG",
                    pd.Timestamp("2026-01-01 10:15:00"),
                    payload={"detected_at": pd.Timestamp("2026-01-01 10:30:00")},
                )
            ],
        )
        scan = ScenarioScannerOutput(
            best_long_scenario=candidate,
            best_short_scenario=None,
            selected_scenario=candidate,
            selected_direction="LONG",
            signal_allowed=False,
            scenario_valid=False,
            reason="waiting_for_confirmed_bullish_bos",
            top_candidates=[candidate],
            selected_scenario_id=candidate.candidate_id,
        )

        first = analyzer._build_scenario_transition_records("run-1", "2026-01-01T10:30:00Z", "SOL", scan)
        second = analyzer._build_scenario_transition_records("run-1", "2026-01-01T10:45:00Z", "SOL", scan)
        candidate.next_expected_step = "FVG_CREATED"
        candidate.current_step = "confirmed_trigger_confirmed"
        candidate.events_used.append(
            ScenarioEvent("CONFIRMED_TRIGGER_CONFIRMED", "LONG", pd.Timestamp("2026-01-01 10:45:00"))
        )
        third = analyzer._build_scenario_transition_records("run-1", "2026-01-01T10:45:00Z", "SOL", scan)

        self.assertEqual(len(first), 1)
        self.assertEqual(first[0]["record_type"], "scenario_transition")
        self.assertIsNone(first[0]["from_state"])
        self.assertEqual(first[0]["to_state"], "WAITING_FOR_CONFIRMED_TRIGGER_CONFIRMED")
        self.assertEqual(first[0]["event_type"], "EARLY_TRIGGER_CONFIRMED")
        self.assertEqual(first[0]["event_time"], "2026-01-01 10:15:00")
        self.assertEqual(first[0]["detected_at"], "2026-01-01 10:30:00")
        self.assertEqual(second, [])
        self.assertEqual(len(third), 1)
        self.assertEqual(third[0]["from_state"], "WAITING_FOR_CONFIRMED_TRIGGER_CONFIRMED")
        self.assertEqual(third[0]["to_state"], "WAITING_FOR_FVG_CREATED")
        analyzer._SCENARIO_TRANSITION_STATE.clear()

    def test_scenario_transition_invalidation_contains_component_and_reason(self):
        analyzer._SCENARIO_TRANSITION_STATE.clear()
        candidate = ScenarioScanResult(
            direction="SHORT",
            status="invalidated",
            current_step="invalidated",
            next_expected_step=None,
            signal_allowed=False,
            scenario_valid=False,
            completion_ratio=0.3,
            completed_steps=3,
            total_steps=10,
            quality_score=70,
            candidate_id="SHORT_POI_1",
            invalidated_reason="opposite_confirmed_bos",
            last_invalidated_component="structure",
            events_used=[ScenarioEvent("BOS_CONFIRMED", "LONG", pd.Timestamp("2026-01-01 11:00:00"))],
        )
        scan = ScenarioScannerOutput(
            best_long_scenario=None,
            best_short_scenario=candidate,
            selected_scenario=candidate,
            selected_direction="SHORT",
            signal_allowed=False,
            scenario_valid=False,
            reason="opposite_confirmed_bos",
            top_candidates=[candidate],
            selected_scenario_id=candidate.candidate_id,
        )

        records = analyzer._build_scenario_transition_records("run-2", "2026-01-01T11:00:00Z", "XRP", scan)

        self.assertEqual(records[0]["to_state"], "INVALIDATED")
        self.assertEqual(records[0]["invalidation_component"], "structure")
        self.assertEqual(records[0]["invalidated_reason"], "opposite_confirmed_bos")
        analyzer._SCENARIO_TRANSITION_STATE.clear()

    def test_risk_plan_provenance_mismatch_downgrades_to_not_available(self):
        candidate = ScenarioScanResult(
            direction="LONG",
            status="waiting_for_confirmation",
            current_step="pd_location_valid",
            next_expected_step="SFP_CONFIRMED",
            signal_allowed=False,
            scenario_valid=False,
            completion_ratio=0.2,
            completed_steps=2,
            total_steps=10,
            quality_score=70,
            candidate_id="SCENARIO_LONG_PD_LOCATION_VALID_NEW",
        )
        stale_risk = RiskPlan(
            direction="LONG",
            entry=100.0,
            stop_loss=99.0,
            invalidation_level=99.0,
            target_1=103.0,
            target_2=None,
            risk_per_unit=1.0,
            rr_to_target_1=3.0,
            rr_to_target_2=None,
            stop_distance_percent=1.0,
            entry_distance_from_poi_atr=0.1,
            valid=True,
            reason="Risk plan valid",
            entry_model="fvg_midpoint",
            stop_model="fvg_invalid",
            target_model="liquidity",
            source_candidate_id="SCENARIO_LONG_PD_LOCATION_VALID_OLD",
        )

        guarded = analyzer._risk_plan_for_selected_candidate(stale_risk, candidate, "LONG")

        self.assertFalse(guarded.valid)
        self.assertEqual(guarded.risk_plan_status, "not_available")
        self.assertEqual(guarded.reason, "candidate_provenance_mismatch")
        self.assertEqual(guarded.source_candidate_id, candidate.candidate_id)

    def test_runtime_update_count_increments_for_stable_candidate_between_scans(self):
        analyzer._SCENARIO_RUNTIME_STATE.clear()
        first_candidate = ScenarioScanResult(
            direction="LONG",
            status="waiting_for_confirmation",
            current_step="pd_location_valid",
            next_expected_step="SFP_CONFIRMED",
            signal_allowed=False,
            scenario_valid=False,
            completion_ratio=0.2,
            completed_steps=2,
            total_steps=10,
            quality_score=70,
            candidate_id="SCENARIO_LONG_PD_LOCATION_VALID_STABLE",
            anchor_index=pd.Timestamp("2026-07-16 16:45:00"),
            last_event_index=pd.Timestamp("2026-07-16 16:45:00"),
            trigger_scan={"candidate_id": "SCENARIO_LONG_PD_LOCATION_VALID_STABLE"},
        )
        first_scan = ScenarioScannerOutput(
            best_long_scenario=first_candidate,
            best_short_scenario=None,
            selected_scenario=first_candidate,
            selected_direction="LONG",
            signal_allowed=False,
            scenario_valid=False,
            reason="liquidity_sweep_confirmed",
            top_candidates=[first_candidate],
            selected_scenario_id=first_candidate.candidate_id,
        )
        second_candidate = ScenarioScanResult(
            direction="LONG",
            status="waiting_for_confirmation",
            current_step="pd_location_valid",
            next_expected_step="SFP_CONFIRMED",
            signal_allowed=False,
            scenario_valid=False,
            completion_ratio=0.2,
            completed_steps=2,
            total_steps=10,
            quality_score=70,
            candidate_id=first_candidate.candidate_id,
            anchor_index=pd.Timestamp("2026-07-16 17:00:00"),
            last_event_index=pd.Timestamp("2026-07-16 17:00:00"),
            trigger_scan={"candidate_id": first_candidate.candidate_id},
        )
        second_scan = ScenarioScannerOutput(
            best_long_scenario=second_candidate,
            best_short_scenario=None,
            selected_scenario=second_candidate,
            selected_direction="LONG",
            signal_allowed=False,
            scenario_valid=False,
            reason="liquidity_sweep_confirmed",
            top_candidates=[second_candidate],
            selected_scenario_id=second_candidate.candidate_id,
        )

        analyzer._apply_runtime_update_counts("RENDER", first_scan)
        self.assertEqual(first_candidate.runtime_update_count, 0)
        self.assertEqual(first_candidate.market_age_bars, 0)
        self.assertEqual(first_candidate.trigger_scan["runtime_update_count"], 0)

        analyzer._apply_runtime_update_counts("RENDER", second_scan)
        self.assertEqual(second_candidate.runtime_update_count, 1)
        self.assertEqual(second_candidate.market_age_bars, 1)
        self.assertEqual(second_candidate.trigger_scan["runtime_update_count"], 1)
        self.assertEqual(second_candidate.trigger_scan["market_age_bars"], 1)
        empty_scan = ScenarioScannerOutput(
            best_long_scenario=None,
            best_short_scenario=None,
            selected_scenario=None,
            selected_direction=None,
            signal_allowed=False,
            scenario_valid=False,
            reason="pd_block",
        )
        analyzer._apply_runtime_update_counts("RENDER", empty_scan)

        reentry_candidate = ScenarioScanResult(
            direction="LONG",
            status="waiting_for_confirmation",
            current_step="pd_location_valid",
            next_expected_step="SFP_CONFIRMED",
            signal_allowed=False,
            scenario_valid=False,
            completion_ratio=0.2,
            completed_steps=2,
            total_steps=10,
            quality_score=70,
            candidate_id=first_candidate.candidate_id,
            anchor_index=pd.Timestamp("2026-07-16 17:30:00"),
            last_event_index=pd.Timestamp("2026-07-16 17:30:00"),
            trigger_scan={"candidate_id": first_candidate.candidate_id},
        )
        reentry_scan = ScenarioScannerOutput(
            best_long_scenario=reentry_candidate,
            best_short_scenario=None,
            selected_scenario=reentry_candidate,
            selected_direction="LONG",
            signal_allowed=False,
            scenario_valid=False,
            reason="liquidity_sweep_confirmed",
            top_candidates=[reentry_candidate],
            selected_scenario_id=reentry_candidate.candidate_id,
        )
        analyzer._apply_runtime_update_counts("RENDER", reentry_scan)
        self.assertEqual(reentry_candidate.runtime_update_count, 0)
        self.assertEqual(reentry_candidate.market_age_bars, 0)
        analyzer._SCENARIO_RUNTIME_STATE.clear()

    def _early_wait_candidate(self, candidate_id="SCENARIO_LONG_SFP_CONFIRMED_2026-07-18T133000_none", direction="LONG", anchor="2026-07-18 13:30:00"):
        return ScenarioScanResult(
            direction=direction,
            status="building",
            current_step="liquidity_sweep_confirmed",
            next_expected_step="EARLY_TRIGGER_CONFIRMED",
            signal_allowed=False,
            scenario_valid=True,
            completion_ratio=0.3,
            completed_steps=3,
            total_steps=10,
            quality_score=75,
            candidate_id=candidate_id,
            anchor_type="SFP_CONFIRMED",
            anchor_index=pd.Timestamp(anchor),
            last_event_index=pd.Timestamp(anchor),
            waiting_for=f"{'bullish' if direction == 'LONG' else 'bearish'} CHOCH/BOS after SFP",
            trigger_scan={
                "candidate_id": candidate_id,
                "sfp_index": str(pd.Timestamp(anchor)),
                "anchor_index": str(pd.Timestamp(anchor)),
                "rejected_reason": "no_bullish_trigger_after_sfp_or_poi" if direction == "LONG" else "no_bearish_trigger_after_sfp_or_poi",
                "waiting_for": f"{'bullish' if direction == 'LONG' else 'bearish'} CHOCH/BOS after SFP",
                "early_trigger_confirmed": False,
                "trigger_confirmed": False,
            },
        )

    def _scan_with_candidate(self, candidate):
        return ScenarioScannerOutput(
            best_long_scenario=candidate if candidate.direction == "LONG" else None,
            best_short_scenario=candidate if candidate.direction == "SHORT" else None,
            selected_scenario=candidate,
            selected_direction=candidate.direction,
            signal_allowed=False,
            scenario_valid=True,
            reason="waiting_for_early_trigger",
            top_candidates=[candidate],
            long_candidates=[candidate] if candidate.direction == "LONG" else [],
            short_candidates=[candidate] if candidate.direction == "SHORT" else [],
            selected_scenario_id=candidate.candidate_id,
        )

    def test_bars_waiting_from_candle_timestamps_and_boundaries(self):
        expectations = [(0, "building"), (1, "building"), (23, "building"), (24, "building"), (25, "invalidated")]
        for bars, status in expectations:
            analyzer.reset_scenario_runtime_state()
            candidate = self._early_wait_candidate(candidate_id=f"CAND-{bars}")
            scan = self._scan_with_candidate(candidate)
            analyzer._apply_runtime_update_counts("LDO", scan, analysis_time=pd.Timestamp("2026-07-18 13:30:00") + pd.Timedelta(minutes=15 * bars))
            self.assertEqual(candidate.market_age_bars, bars)
            self.assertEqual(candidate.trigger_scan["bars_waiting"], bars)
            self.assertEqual(candidate.status, status)
        self.assertEqual(candidate.invalidated_reason, analyzer.EARLY_TRIGGER_WINDOW_EXPIRED_REASON)

    def test_same_candle_repeated_scan_does_not_increase_bar_age_or_scan_count(self):
        candidate = self._early_wait_candidate(candidate_id="SAME-CANDLE")
        scan = self._scan_with_candidate(candidate)
        analysis_time = pd.Timestamp("2026-07-18 14:00:00")
        analyzer._apply_runtime_update_counts("LDO", scan, analysis_time=analysis_time)
        analyzer._apply_runtime_update_counts("LDO", scan, analysis_time=analysis_time)
        self.assertEqual(candidate.market_age_bars, 2)
        self.assertEqual(candidate.runtime_update_count, 0)

    def test_runtime_roundtrip_preserves_age_and_expiration_status(self):
        candidate = self._early_wait_candidate(candidate_id="ROUNDTRIP")
        scan = self._scan_with_candidate(candidate)
        analyzer._apply_runtime_update_counts("LDO", scan, analysis_time="2026-07-18 20:00:00")
        self.assertEqual(candidate.status, "invalidated")
        snapshot = analyzer.export_scenario_runtime_state()

        analyzer.reset_scenario_runtime_state()
        analyzer.import_scenario_runtime_state(snapshot)
        restored = self._early_wait_candidate(candidate_id="ROUNDTRIP")
        restored_scan = self._scan_with_candidate(restored)
        analyzer._apply_runtime_update_counts("LDO", restored_scan, analysis_time="2026-07-18 13:45:00")
        self.assertEqual(restored.status, "invalidated")
        self.assertEqual(restored.invalidated_reason, analyzer.EARLY_TRIGGER_WINDOW_EXPIRED_REASON)
        self.assertEqual(restored.market_age_bars, 26)

    def test_late_trigger_rejected_after_window_boundary(self):
        sfp = {"index": pd.Timestamp("2026-07-18 13:30:00")}
        on_time = {"type": "bullish_early_choch", "index": pd.Timestamp("2026-07-18 19:30:00"), "quality_score": 80, "body_ratio": 0.7, "displacement_ratio": 1.0, "close_position": 0.8, "micro_break_confirmed": True}
        late = dict(on_time, index=pd.Timestamp("2026-07-18 19:45:00"))
        accepted = analyzer.scan_post_anchor_trigger("LONG", sfp=sfp, trigger_candidates=[on_time], min_early_trigger_quality=55)
        rejected = analyzer.scan_post_anchor_trigger("LONG", sfp=sfp, trigger_candidates=[late], min_early_trigger_quality=55)
        self.assertTrue(accepted.early_trigger_confirmed)
        self.assertFalse(rejected.early_trigger_confirmed)
        self.assertEqual(rejected.rejected_reason, "no_bullish_trigger_after_sfp_or_poi")

    def test_expired_candidate_terminal_and_stage_aware_reason(self):
        candidate = self._early_wait_candidate(candidate_id="EXPIRE")
        scan = self._scan_with_candidate(candidate)
        analyzer._apply_runtime_update_counts("AAVE", scan, analysis_time="2026-07-18 20:00:00")
        self.assertIsNone(scan.selected_scenario)
        self.assertEqual(scan.reason, analyzer.EARLY_TRIGGER_WINDOW_EXPIRED_REASON)
        self.assertEqual(candidate.status, "invalidated")
        self.assertEqual(candidate.trigger_scan["rejected_reason"], analyzer.EARLY_TRIGGER_WINDOW_EXPIRED_REASON)
        self.assertIn("candidate_expired", [item["event_type"] for item in candidate.pending_observations])

    def test_expired_candidate_stays_expired_and_new_sfp_is_fresh(self):
        old = self._early_wait_candidate(candidate_id="OLD", anchor="2026-07-18 13:30:00")
        old_scan = self._scan_with_candidate(old)
        analyzer._apply_runtime_update_counts("LDO", old_scan, analysis_time="2026-07-18 20:00:00")
        self.assertEqual(old.status, "invalidated")

        new = self._early_wait_candidate(candidate_id="NEW", anchor="2026-07-18 20:15:00")
        new_scan = self._scan_with_candidate(new)
        analyzer._apply_runtime_update_counts("LDO", new_scan, analysis_time="2026-07-18 20:15:00")
        self.assertEqual(new.status, "building")
        self.assertEqual(new.market_age_bars, 0)
        self.assertEqual(new.runtime_update_count, 0)

    def test_fresh_aave_wld_like_candidates_keep_trigger_decision_inside_window(self):
        for symbol, direction, reason in [
            ("AAVE", "SHORT", "no_bearish_trigger_after_sfp_or_poi"),
            ("WLD", "SHORT", "no_bearish_trigger_after_sfp_or_poi"),
        ]:
            candidate = self._early_wait_candidate(candidate_id=f"{symbol}-FRESH", direction=direction, anchor="2026-07-19 01:15:00")
            before_reason = candidate.trigger_scan["rejected_reason"] = reason
            scan = self._scan_with_candidate(candidate)
            analyzer._apply_runtime_update_counts(symbol, scan, analysis_time="2026-07-19 02:15:00")
            self.assertEqual(candidate.status, "building")
            self.assertEqual(candidate.trigger_scan["rejected_reason"], before_reason)
            self.assertFalse(candidate.trigger_scan["early_trigger_confirmed"])

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

    def test_premium_discount_location_uses_last_closed_timestamp_instead_of_synthetic_index(self):
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

        pd_events = [event for event in events if event.event_type == "PD_LOCATION_VALID"]
        self.assertEqual(len(pd_events), 1)
        self.assertEqual(pd_events[0].index, pd.Timestamp("2026-01-01 12:15:00"))
        self.assertNotEqual(pd_events[0].index, -1)
        self.assertFalse(any(event.event_type == "POI_TOUCHED" for event in events))
        self.assertEqual(pd_events[0].source, "premium_discount")

    def test_premium_discount_without_real_timestamp_does_not_create_location_event(self):
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

        self.assertFalse(any(event.event_type == "PD_LOCATION_VALID" for event in events))
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

    def test_fvg_events_include_candidate_and_confirmed_trigger_provenance(self):
        last_closed = pd.Series({"close": 100.0}, name=pd.Timestamp("2026-01-01 12:00:00"))
        trigger_scan = {
            "selected_trigger": {
                "type": "bullish_bos",
                "index": pd.Timestamp("2026-01-01 10:30:00"),
                "quality_score": 90,
                "candidate_id": "LONG_SFP_1",
                "event_id": "bos-1",
            },
            "confirmed_trigger": {
                "type": "bullish_bos",
                "index": pd.Timestamp("2026-01-01 10:30:00"),
                "quality_score": 90,
                "candidate_id": "LONG_SFP_1",
                "event_id": "bos-1",
            },
            "early_trigger": {"type": "bullish_early_choch", "index": pd.Timestamp("2026-01-01 10:00:00")},
            "sfp_index": pd.Timestamp("2026-01-01 09:45:00"),
        }

        events = analyzer._build_scenario_events(
            "LONG",
            {"trend": "bullish", "confidence": 80},
            {"valid_for_buy": True, "valid_for_sell": False, "zone_strength": 75},
            None,
            trigger_scan,
            None,
            [{
                "type": "bullish",
                "end_index": pd.Timestamp("2026-01-01 10:45:00"),
                "test_index": pd.Timestamp("2026-01-01 11:00:00"),
                "displacement_index": pd.Timestamp("2026-01-01 11:15:00"),
                "quality_score": 82,
                "tested": True,
            }],
            {"index": pd.Timestamp("2026-01-01 11:00:00")},
            None,
            last_closed,
        )

        fvg_created = next(event for event in events if event.event_type == "FVG_CREATED")
        fvg_retested = next(event for event in events if event.event_type == "FVG_RETESTED")
        displacement = next(event for event in events if event.event_type == "DISPLACEMENT_CONFIRMED")
        self.assertEqual(fvg_created.payload["source_candidate_id"], "LONG_SFP_1")
        self.assertEqual(fvg_created.payload["source_confirmed_trigger_id"], "bos-1")
        self.assertEqual(fvg_created.payload["created_index"], pd.Timestamp("2026-01-01 10:45:00"))
        self.assertEqual(fvg_retested.payload["retested_index"], pd.Timestamp("2026-01-01 11:00:00"))
        self.assertEqual(displacement.payload["displacement_stage"], "post_retest")

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

    def test_state_machine_diagnostic_ignores_historical_fvg(self):
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
        self.assertIn("waiting_for_fvg", status)
        self.assertNotIn("Unexpected fvg_created", status)

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

    def test_state_machine_diagnostic_allows_direct_bos_without_context_choch(self):
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
            context_structure=None,
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
        self.assertNotIn("Unexpected bos_confirmed", status)

    def test_state_machine_diagnostic_advances_on_created_fvg_without_retest(self):
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
            trigger_structure={"type": "bullish_bos", "index": 4, "quality_score": 95, "event_id": "bos-A"},
            fvg_test_data=None,
            fvg_data=[{
                "type": "bullish",
                "end_index": 5,
                "tested": False,
                "invalidated": False,
                "quality_score": 90,
                "age_bars": 2,
                "retest_count": 0,
                "source_candidate_id": "CAND-A",
                "source_confirmed_trigger_id": "bos-A",
            }],
            current_price=100.0,
            current_bar=7,
            expected_candidate_id="CAND-A",
        )

        self.assertFalse(result.signal_allowed)
        self.assertIn("waiting_for_fvg_retest", status)
        self.assertIn("next: fvg_retested", status)
        self.assertIn("fvg_created", result.completed_steps)

    def test_state_machine_diagnostic_uses_selected_candidate_sfp_when_global_sfp_is_missing(self):
        market_structure = MarketStructure(trend="bearish", confidence=80, reason="test")
        pd_result = {
            "valid_for_buy": False,
            "valid_for_sell": True,
            "zone": "premium",
        }
        selected = ScenarioScanResult(
            direction="SHORT",
            status="waiting_for_confirmation",
            current_step="confirmed_trigger_confirmed",
            next_expected_step="FVG_CREATED",
            signal_allowed=False,
            scenario_valid=True,
            completion_ratio=0.5,
            completed_steps=4,
            total_steps=8,
            quality_score=80,
            candidate_id="SCENARIO_SHORT_SFP_CONFIRMED_2026-07-19T220000_none",
            events_used=[
                ScenarioEvent(
                    "SFP_CONFIRMED",
                    "SHORT",
                    "2026-07-19 22:00:00",
                    payload={"detected": True, "swept": True},
                ),
                ScenarioEvent(
                    "BOS_CONFIRMED",
                    "SHORT",
                    "2026-07-19 23:00:00",
                    payload={"type": "bearish_bos", "quality_score": 95},
                ),
            ],
        )

        candidate_sfp = analyzer._candidate_sfp(selected)
        status, result = analyzer._state_machine_diagnostic(
            "SHORT",
            market_structure,
            pd_result,
            liquidity_map=None,
            sfp_data=candidate_sfp,
            context_structure=None,
            trigger_structure=analyzer._candidate_confirmed_trigger(selected),
            fvg_test_data=None,
            fvg_data=None,
            current_price=100.0,
            current_bar=7,
            expected_candidate_id=selected.candidate_id,
        )

        self.assertEqual(candidate_sfp["index"], "2026-07-19 22:00:00")
        self.assertEqual(candidate_sfp["direction"], "bearish")
        self.assertFalse(result.signal_allowed)
        self.assertIn("waiting_for_fvg", status)
        self.assertNotIn("Unexpected bos_confirmed", status)
        self.assertNotIn("waiting for liquidity_sweep_confirmed", status)

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

    def test_state_machine_ignores_unrelated_candidate_fvg(self):
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
            trigger_structure={"type": "bullish_bos", "index": 4, "quality_score": 95, "event_id": "bos-A"},
            fvg_test_data={"index": 6, "displacement_index": 7},
            fvg_data=[{
                "type": "bullish",
                "end_index": 5,
                "tested": True,
                "invalidated": False,
                "quality_score": 90,
                "age_bars": 2,
                "retest_count": 1,
                "source_candidate_id": "CAND-B",
                "source_confirmed_trigger_id": "bos-B",
            }],
            current_price=100.0,
            current_bar=7,
            expected_candidate_id="CAND-A",
        )

        self.assertFalse(result.signal_allowed)
        self.assertIn("waiting_for_fvg", status)
        self.assertNotIn("Unexpected fvg_created", status)

    def test_state_machine_accepts_valid_candidate_fvg(self):
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
            trigger_structure={"type": "bullish_bos", "index": 4, "quality_score": 95, "event_id": "bos-A"},
            fvg_test_data={"index": 6, "displacement_index": 7},
            fvg_data=[{
                "type": "bullish",
                "end_index": 5,
                "tested": True,
                "invalidated": False,
                "quality_score": 90,
                "age_bars": 2,
                "retest_count": 1,
                "source_candidate_id": "CAND-A",
                "source_confirmed_trigger_id": "bos-A",
            }],
            current_price=100.0,
            current_bar=7,
            expected_candidate_id="CAND-A",
        )

        self.assertTrue(result.signal_allowed)
        self.assertIn("signal_ready", status)

    def test_inj_state_machine_regression_ignores_global_fvg_without_confirmed_trigger(self):
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
            sfp_data={"type": "bullish_sfp", "index": "2026-07-18 06:45", "detected": True, "swept": True},
            context_structure={"type": "bullish_choch", "index": "2026-07-18 07:00", "quality_score": 82},
            trigger_structure=None,
            fvg_test_data={"index": "2026-07-18 09:30", "displacement_index": "2026-07-18 09:45"},
            fvg_data=[{
                "type": "bullish",
                "end_index": "2026-07-18 09:15",
                "tested": True,
                "invalidated": False,
                "quality_score": 90,
                "age_bars": 1,
                "retest_count": 1,
                "source_candidate_id": "CAND-B",
                "source_confirmed_trigger_id": "bos-B",
            }],
            current_price=100.0,
            current_bar=7,
            expected_candidate_id="CAND-A",
        )

        self.assertFalse(result.signal_allowed)
        self.assertIn("waiting_for_bos", status)
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
