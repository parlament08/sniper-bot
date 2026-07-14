import unittest

from core.scenario_scanner import ScenarioEvent, scan_scenarios


def event(event_type, direction="bullish", index=1, quality=80, payload=None):
    return ScenarioEvent(
        event_type=event_type,
        direction=direction,
        index=index,
        quality_score=quality,
        payload=payload,
    )


class ScenarioScannerTest(unittest.TestCase):
    def test_complete_long_scenario(self):
        output = scan_scenarios(
            events=[
                event("HTF_CONTEXT_CONFIRMED", index=1),
                event("POI_TOUCHED", index=2),
                event("SFP_CONFIRMED", index=3),
                event("CHOCH_CONFIRMED", index=4),
                event("BOS_CONFIRMED", index=5),
                event("FVG_CREATED", index=6),
                event("FVG_RETESTED", index=7),
                event("DISPLACEMENT_CONFIRMED", index=8),
                event("RISK_VALID", direction=None, index=9),
            ],
            expected_direction="LONG",
            htf_structure={"trend": "bullish"},
            premium_discount={"valid_for_buy": True},
        )

        self.assertEqual(output.selected_scenario.direction, "LONG")
        self.assertEqual(output.selected_scenario.status, "complete")
        self.assertTrue(output.scenario_valid)
        self.assertTrue(output.signal_allowed)

    def test_long_waiting_for_choch(self):
        output = scan_scenarios(
            events=[
                event("HTF_CONTEXT_CONFIRMED", index=1),
                event("POI_TOUCHED", index=2),
                event("SFP_CONFIRMED", index=3),
            ],
            expected_direction="LONG",
            htf_structure={"trend": "bullish"},
            premium_discount={"valid_for_buy": True},
        )

        scenario = output.selected_scenario
        self.assertEqual(scenario.status, "waiting_for_confirmation")
        self.assertEqual(scenario.current_step, "liquidity_sweep_confirmed")
        self.assertEqual(scenario.next_expected_step, "EARLY_TRIGGER_CONFIRMED")
        self.assertEqual(scenario.waiting_for, "bullish CHOCH/BOS after SFP")
        self.assertFalse(output.signal_allowed)

    def test_early_trigger_progresses_living_scenario_without_signal_allowed(self):
        output = scan_scenarios(
            events=[
                event("HTF_CONTEXT_CONFIRMED", index=1),
                event("POI_TOUCHED", index=2),
                event("SFP_CONFIRMED", index=3),
                event("EARLY_TRIGGER_CONFIRMED", index=4, quality=68),
            ],
            expected_direction="LONG",
            htf_structure={"trend": "bullish"},
            premium_discount={"valid_for_buy": True},
        )

        scenario = output.selected_scenario
        self.assertEqual(scenario.status, "waiting_for_confirmation")
        self.assertEqual(scenario.current_step, "early_trigger_confirmed")
        self.assertEqual(scenario.next_expected_step, "CONFIRMED_TRIGGER_CONFIRMED")
        self.assertEqual(scenario.completed_steps, 4)
        self.assertEqual(scenario.waiting_for, "confirmed bullish BOS after early CHOCH")
        self.assertFalse(scenario.signal_allowed)
        self.assertFalse(scenario.scenario_valid)

    def test_confirmed_trigger_before_early_does_not_complete_trigger_step(self):
        output = scan_scenarios(
            events=[
                event("HTF_CONTEXT_CONFIRMED", index=1),
                event("POI_TOUCHED", index=2),
                event("SFP_CONFIRMED", index=3),
                event("CONFIRMED_TRIGGER_CONFIRMED", index=4, quality=90),
                event("EARLY_TRIGGER_CONFIRMED", index=5, quality=70),
            ],
            expected_direction="LONG",
            htf_structure={"trend": "bullish"},
            premium_discount={"valid_for_buy": True},
        )

        scenario = output.selected_scenario
        self.assertEqual(scenario.status, "waiting_for_confirmation")
        self.assertEqual(scenario.current_step, "early_trigger_confirmed")
        self.assertEqual(scenario.next_expected_step, "CONFIRMED_TRIGGER_CONFIRMED")
        self.assertEqual(scenario.completed_steps, 4)
        self.assertEqual(scenario.last_invalidated_component, "confirmed_trigger_before_early")
        self.assertEqual(scenario.waiting_for, "confirmed bullish BOS after early CHOCH")

    def test_waiting_for_liquidity_sweep_uses_human_text_but_stable_reason(self):
        output = scan_scenarios(
            events=[
                event("HTF_CONTEXT_CONFIRMED", index=1),
                event("POI_TOUCHED", index=2),
            ],
            expected_direction="LONG",
            htf_structure={"trend": "bullish"},
            premium_discount={"valid_for_buy": True},
        )

        scenario = output.selected_scenario
        self.assertEqual(scenario.waiting_for, "liquidity sweep / SFP")
        self.assertEqual(output.reason, "waiting_for_liquidity_sweep")
        self.assertIsNotNone(scenario.trigger_scan)
        self.assertIsNone(scenario.trigger_scan["early_trigger"])
        self.assertFalse(scenario.trigger_scan["early_trigger_confirmed"])
        self.assertFalse(scenario.trigger_scan["trigger_confirmed"])
        self.assertEqual(scenario.trigger_scan["rejected_reason"], "waiting_for_sfp")
        self.assertEqual(scenario.trigger_scan["waiting_for"], "liquidity sweep / SFP")

    def test_sfp_candidate_trigger_scan_includes_scoped_early_trigger(self):
        output = scan_scenarios(
            events=[
                event("HTF_CONTEXT_CONFIRMED", index=1),
                event("POI_TOUCHED", index=2),
                event("SFP_CONFIRMED", index=3),
                event(
                    "EARLY_TRIGGER_CONFIRMED",
                    index=4,
                    quality=88,
                    payload={"type": "bullish_early_choch", "index": 4, "quality_score": 88},
                ),
            ],
            expected_direction="LONG",
            htf_structure={"trend": "bullish"},
            premium_discount={"valid_for_buy": True},
        )

        scenario = output.selected_scenario
        self.assertEqual(scenario.current_step, "early_trigger_confirmed")
        self.assertEqual(scenario.completed_steps, 4)
        self.assertTrue(scenario.trigger_scan["early_trigger_confirmed"])
        self.assertFalse(scenario.trigger_scan["trigger_confirmed"])
        self.assertEqual(scenario.trigger_scan["early_trigger"]["type"], "bullish_early_choch")
        self.assertEqual(scenario.trigger_scan["waiting_for"], "confirmed bullish BOS after early CHOCH")

    def test_confirmed_trigger_after_early_moves_scenario_to_fvg_wait(self):
        output = scan_scenarios(
            events=[
                event("HTF_CONTEXT_CONFIRMED", index=1),
                event("POI_TOUCHED", index=2),
                event("SFP_CONFIRMED", index=3),
                event("EARLY_TRIGGER_CONFIRMED", index=4, quality=88, payload={"type": "bullish_early_choch", "index": 4, "quality_score": 88}),
                event("CONFIRMED_TRIGGER_CONFIRMED", index=5, quality=84, payload={"type": "bullish_bos", "index": 5, "quality_score": 84}),
            ],
            expected_direction="LONG",
            htf_structure={"trend": "bullish"},
            premium_discount={"valid_for_buy": True},
        )

        scenario = output.selected_scenario
        self.assertEqual(scenario.current_step, "confirmed_trigger_confirmed")
        self.assertEqual(scenario.next_expected_step, "FVG_CREATED")
        self.assertEqual(scenario.completed_steps, 5)
        self.assertEqual(scenario.waiting_for, "bullish FVG after confirmed BOS")
        self.assertTrue(scenario.trigger_scan["early_trigger_confirmed"])
        self.assertTrue(scenario.trigger_scan["trigger_confirmed"])
        self.assertEqual(scenario.trigger_scan["confirmed_trigger"]["type"], "bullish_bos")
        self.assertEqual(scenario.trigger_scan["selected_trigger"], scenario.trigger_scan["confirmed_trigger"])
        self.assertEqual(scenario.trigger_scan["waiting_for"], "bullish FVG after confirmed BOS")

    def test_opposite_confirmed_trigger_after_early_is_diagnostic_only(self):
        output = scan_scenarios(
            events=[
                event("HTF_CONTEXT_CONFIRMED", index=1),
                event("POI_TOUCHED", index=2),
                event("SFP_CONFIRMED", index=3),
                event("EARLY_TRIGGER_CONFIRMED", index=4, quality=88),
                event("CONFIRMED_TRIGGER_CONFIRMED", direction="bearish", index=5, quality=92),
            ],
            expected_direction="LONG",
            htf_structure={"trend": "bullish"},
            premium_discount={"valid_for_buy": True},
        )

        scenario = output.selected_scenario
        self.assertEqual(scenario.current_step, "early_trigger_confirmed")
        self.assertEqual(scenario.next_expected_step, "CONFIRMED_TRIGGER_CONFIRMED")
        self.assertFalse(scenario.trigger_scan["trigger_confirmed"])
        self.assertIsNone(scenario.trigger_scan["confirmed_trigger"])
        self.assertIsNotNone(scenario.trigger_scan["opposite_trigger"])
        self.assertEqual(scenario.waiting_for, "confirmed bullish BOS after early CHOCH")

    def test_low_quality_confirmed_trigger_after_early_has_debug_rejection(self):
        output = scan_scenarios(
            events=[
                event("HTF_CONTEXT_CONFIRMED", index=1),
                event("POI_TOUCHED", index=2),
                event("SFP_CONFIRMED", index=3),
                event("EARLY_TRIGGER_CONFIRMED", index=4, quality=88),
                event("CONFIRMED_TRIGGER_CONFIRMED", index=5, quality=62, payload={"type": "bullish_bos", "index": 5, "quality_score": 62}),
            ],
            expected_direction="LONG",
            htf_structure={"trend": "bullish"},
            premium_discount={"valid_for_buy": True},
        )

        scenario = output.selected_scenario
        debug = scenario.trigger_scan["confirmed_trigger_debug"]
        self.assertEqual(scenario.current_step, "early_trigger_confirmed")
        self.assertFalse(scenario.trigger_scan["trigger_confirmed"])
        self.assertEqual(debug["candidate_bos_count"], 1)
        self.assertEqual(debug["final_reason"], "quality_below_min")
        self.assertEqual(debug["rejected_candidates"][0]["rejected_reason"], "quality_below_min")

    def test_carried_confirmed_trigger_debug_survives_candidate_scan(self):
        carried_debug = {
            "generator_called": True,
            "early_trigger_index": "4",
            "search_window_start": "4",
            "search_window_end": "28",
            "candles_after_early": 2,
            "expected_direction": "LONG",
            "micro_swing_high": 10.8,
            "micro_swing_low": None,
            "break_level": 10.8,
            "checked_candles": [
                {
                    "index": "5",
                    "close": 10.6,
                    "high": 10.7,
                    "low": 10.2,
                    "body_ratio": 0.5,
                    "close_position": 0.8,
                    "displacement_ratio": 0.6,
                    "breaks_level": False,
                    "direction_ok": True,
                    "candidate_created": False,
                }
            ],
            "candidate_bos_count": 0,
            "candidate_choch_count": 0,
            "rejected_candidates": [],
            "final_reason": "no_candle_closed_beyond_break_level",
        }
        output = scan_scenarios(
            events=[
                event("HTF_CONTEXT_CONFIRMED", index=1),
                event("POI_TOUCHED", index=2),
                event("SFP_CONFIRMED", index=3),
                event(
                    "EARLY_TRIGGER_CONFIRMED",
                    index=4,
                    quality=88,
                    payload={
                        "type": "bullish_early_choch",
                        "index": 4,
                        "quality_score": 88,
                        "confirmed_trigger_debug": carried_debug,
                    },
                ),
            ],
            expected_direction="LONG",
            htf_structure={"trend": "bullish"},
            premium_discount={"valid_for_buy": True},
        )

        scenario = output.selected_scenario
        debug = scenario.trigger_scan["confirmed_trigger_debug"]
        self.assertTrue(debug["generator_called"])
        self.assertEqual(debug["candles_after_early"], 2)
        self.assertEqual(debug["checked_candles"][0]["index"], "5")
        self.assertEqual(debug["final_reason"], "no_candle_closed_beyond_break_level")

    def test_candidate_does_not_use_pre_anchor_bos(self):
        output = scan_scenarios(
            events=[
                event("HTF_CONTEXT_CONFIRMED", index=1),
                event("BOS_CONFIRMED", index=2),
                event("SFP_CONFIRMED", index=3),
            ],
            expected_direction="LONG",
            htf_structure={"trend": "bullish"},
            premium_discount={"valid_for_buy": True},
        )

        self.assertEqual(output.best_long_scenario.status, "waiting_for_confirmation")
        self.assertIsNone(output.best_long_scenario.invalidated_reason)
        self.assertIsNone(output.best_long_scenario.last_invalidated_component)
        self.assertEqual(output.best_long_scenario.waiting_for, "bullish CHOCH/BOS after SFP")
        self.assertNotIn(2, [item.index for item in output.best_long_scenario.events_used])
        self.assertFalse(output.signal_allowed)

    def test_fvg_before_bos_is_ignored_until_branch_has_bos(self):
        output = scan_scenarios(
            events=[
                event("HTF_CONTEXT_CONFIRMED", index=1),
                event("SFP_CONFIRMED", index=2),
                event("FVG_CREATED", index=3),
                event("CHOCH_CONFIRMED", index=4),
                event("BOS_CONFIRMED", index=5),
            ],
            expected_direction="LONG",
            htf_structure={"trend": "bullish"},
            premium_discount={"valid_for_buy": True},
        )

        self.assertEqual(output.best_long_scenario.status, "waiting_for_confirmation")
        self.assertIsNone(output.best_long_scenario.invalidated_reason)
        self.assertEqual(output.best_long_scenario.current_step, "confirmed_trigger_confirmed")
        self.assertEqual(output.best_long_scenario.waiting_for, "bullish FVG after confirmed BOS")

    def test_poi_candidate_with_pseudo_index_ignores_unbranched_invalid_fvg(self):
        output = scan_scenarios(
            events=[
                event("HTF_CONTEXT_CONFIRMED", index=-2),
                event("POI_TOUCHED", index=-1),
                event("FVG_CREATED", index=3, payload={"invalidated": True}),
            ],
            expected_direction="LONG",
            htf_structure={"trend": "bullish"},
            premium_discount={"valid_for_buy": True},
        )

        scenario = output.best_long_scenario
        self.assertEqual(scenario.status, "waiting_for_confirmation")
        self.assertIsNone(scenario.invalidated_reason)
        self.assertEqual(scenario.completed_steps, 2)
        self.assertEqual(scenario.waiting_for, "liquidity sweep / SFP")

    def test_displacement_before_retest_invalidates_only_that_candidate(self):
        output = scan_scenarios(
            events=[
                event("HTF_CONTEXT_CONFIRMED", index=1),
                event("SFP_CONFIRMED", index=2),
                event("CHOCH_CONFIRMED", index=3),
                event("BOS_CONFIRMED", index=4),
                event("FVG_CREATED", index=5),
                event("DISPLACEMENT_CONFIRMED", index=6),
            ],
            expected_direction="LONG",
            htf_structure={"trend": "bullish"},
            premium_discount={"valid_for_buy": True},
        )

        self.assertEqual(output.best_long_scenario.status, "invalidated")
        self.assertEqual(output.best_long_scenario.invalidated_reason, "displacement_before_retest")

    def test_opposite_bos_after_sfp_invalidates_only_that_candidate(self):
        output = scan_scenarios(
            events=[
                event("HTF_CONTEXT_CONFIRMED", index=1),
                event("POI_TOUCHED", index=2),
                event("SFP_CONFIRMED", index=3),
                event("BOS_CONFIRMED", direction="bearish", index=4),
            ],
            expected_direction="LONG",
            htf_structure={"trend": "bullish"},
            premium_discount={"valid_for_buy": True},
        )

        self.assertEqual(output.best_long_scenario.status, "invalidated")
        self.assertEqual(output.best_long_scenario.invalidated_reason, "opposite_bos_after_sfp")

    def test_htf_neutral_has_no_scenario(self):
        output = scan_scenarios(
            events=[
                event("HTF_CONTEXT_CONFIRMED", direction="neutral", index=1),
                event("SFP_CONFIRMED", index=2),
                event("BOS_CONFIRMED", index=3),
            ],
            expected_direction="LONG",
            htf_structure={"trend": "neutral"},
        )

        self.assertIsNone(output.selected_scenario)
        self.assertFalse(output.scenario_valid)
        self.assertFalse(output.signal_allowed)
        self.assertEqual(output.reason, "htf_neutral_no_scenario")

    def test_risk_invalid_blocks_complete_sequence(self):
        output = scan_scenarios(
            events=[
                event("HTF_CONTEXT_CONFIRMED", index=1),
                event("POI_TOUCHED", index=2),
                event("SFP_CONFIRMED", index=3),
                event("CHOCH_CONFIRMED", index=4),
                event("BOS_CONFIRMED", index=5),
                event("FVG_CREATED", index=6),
                event("FVG_RETESTED", index=7),
                event("DISPLACEMENT_CONFIRMED", index=8),
                event("RISK_INVALID", direction=None, index=9, payload={"reason": "RR to target 1 below minimum"}),
            ],
            expected_direction="LONG",
            htf_structure={"trend": "bullish"},
            premium_discount={"valid_for_buy": True},
        )

        self.assertEqual(output.best_long_scenario.status, "waiting_for_confirmation")
        self.assertIsNone(output.best_long_scenario.invalidated_reason)
        self.assertEqual(output.best_long_scenario.last_invalidated_component, "risk_rr_below_min")
        self.assertEqual(output.best_long_scenario.waiting_for, "valid risk plan")
        self.assertFalse(output.scenario_valid)
        self.assertFalse(output.signal_allowed)
        self.assertEqual(output.reason, "valid_risk_plan")

    def test_context_only_waits_for_choch_when_opposite_trigger_is_not_scenario_event(self):
        output = scan_scenarios(
            events=[
                event("HTF_CONTEXT_CONFIRMED", index=1),
                event("POI_TOUCHED", index=2),
                event("SFP_CONFIRMED", index=3),
            ],
            expected_direction="LONG",
            htf_structure={"trend": "bullish"},
            premium_discount={"valid_for_buy": True},
        )

        self.assertEqual(output.best_long_scenario.status, "waiting_for_confirmation")
        self.assertEqual(output.best_long_scenario.next_expected_step, "EARLY_TRIGGER_CONFIRMED")
        self.assertEqual(output.best_long_scenario.waiting_for, "bullish CHOCH/BOS after SFP")
        self.assertFalse(output.signal_allowed)

    def test_invalidated_fvg_after_structure_invalidates_only_that_candidate(self):
        output = scan_scenarios(
            events=[
                event("HTF_CONTEXT_CONFIRMED", direction="bearish", index=1),
                event("POI_TOUCHED", direction="bearish", index=2),
                event("SFP_CONFIRMED", direction="bearish", index=3),
                event("CHOCH_CONFIRMED", direction="bearish", index=4),
                event("BOS_CONFIRMED", direction="bearish", index=5),
                event("FVG_CREATED", direction="bearish", index=6, payload={"invalidated": True}),
            ],
            expected_direction="SHORT",
            htf_structure={"trend": "bearish"},
            premium_discount={"valid_for_sell": True},
        )

        scenario = output.best_short_scenario
        self.assertEqual(scenario.status, "invalidated")
        self.assertEqual(scenario.invalidated_reason, "fvg_invalidated")

    def test_two_long_candidates_first_invalidated_second_waiting(self):
        output = scan_scenarios(
            events=[
                event("HTF_CONTEXT_CONFIRMED", index="2026-01-01 09:00"),
                event("SFP_CONFIRMED", index="2026-01-01 10:00", quality=70),
                event("CHOCH_CONFIRMED", index="2026-01-01 10:30", quality=75),
                event("BOS_CONFIRMED", index="2026-01-01 11:00", quality=80),
                event("FVG_CREATED", index="2026-01-01 12:00", quality=65, payload={"invalidated": True}),
                event("SFP_CONFIRMED", index="2026-01-01 14:00", quality=82),
            ],
            expected_direction="LONG",
            htf_structure={"trend": "bullish"},
        )

        self.assertEqual(len(output.long_candidates), 2)
        self.assertEqual(output.long_candidates[0].status, "invalidated")
        self.assertEqual(output.long_candidates[0].invalidated_reason, "fvg_invalidated")
        self.assertEqual(output.long_candidates[1].status, "waiting_for_confirmation")
        self.assertEqual(output.long_candidates[1].waiting_for, "bullish CHOCH/BOS after SFP")
        self.assertIs(output.selected_scenario, output.long_candidates[1])

    def test_complete_candidate_beats_waiting_candidate(self):
        output = scan_scenarios(
            events=[
                event("HTF_CONTEXT_CONFIRMED", index=0),
                event("SFP_CONFIRMED", index=1),
                event("CHOCH_CONFIRMED", index=2),
                event("BOS_CONFIRMED", index=3),
                event("FVG_CREATED", index=4),
                event("FVG_RETESTED", index=5),
                event("DISPLACEMENT_CONFIRMED", index=6),
                event("RISK_VALID", direction=None, index=7),
                event("SFP_CONFIRMED", index=20, quality=95),
            ],
            expected_direction="LONG",
            htf_structure={"trend": "bullish"},
        )

        self.assertEqual(output.selected_scenario.status, "complete")
        self.assertTrue(output.signal_allowed)

    def test_waiting_candidate_beats_invalidated_candidate_with_more_steps(self):
        output = scan_scenarios(
            events=[
                event("HTF_CONTEXT_CONFIRMED", index=0),
                event("SFP_CONFIRMED", index=1),
                event("CHOCH_CONFIRMED", index=2),
                event("BOS_CONFIRMED", index=3),
                event("FVG_CREATED", index=4, payload={"invalidated": True}),
                event("SFP_CONFIRMED", index=10),
            ],
            expected_direction="LONG",
            htf_structure={"trend": "bullish"},
        )

        self.assertEqual(output.selected_scenario.anchor_index, 10)
        self.assertEqual(output.selected_scenario.status, "waiting_for_confirmation")

    def test_higher_quality_candidate_wins_same_status_and_steps(self):
        output = scan_scenarios(
            events=[
                event("HTF_CONTEXT_CONFIRMED", index=0, quality=80),
                event("SFP_CONFIRMED", index=1, quality=65),
                event("SFP_CONFIRMED", index=2, quality=95),
            ],
            expected_direction="LONG",
            htf_structure={"trend": "bullish"},
        )

        self.assertEqual(output.selected_scenario.anchor_index, 2)

    def test_more_recent_candidate_wins_same_quality(self):
        output = scan_scenarios(
            events=[
                event("HTF_CONTEXT_CONFIRMED", index=0, quality=80),
                event("SFP_CONFIRMED", index=1, quality=80),
                event("SFP_CONFIRMED", index=2, quality=80),
            ],
            expected_direction="LONG",
            htf_structure={"trend": "bullish"},
        )

        self.assertEqual(output.selected_scenario.anchor_index, 2)

    def test_htf_direction_conflict_blocks_candidate(self):
        output = scan_scenarios(
            events=[
                event("HTF_CONTEXT_CONFIRMED", direction="bullish", index=0),
                event("SFP_CONFIRMED", direction="bearish", index=1),
            ],
            expected_direction="SHORT",
            htf_structure={"trend": "bullish"},
        )

        self.assertIsNone(output.best_short_scenario)
        self.assertEqual(output.short_candidates, [])
        self.assertEqual(output.direction_block_reasons["SHORT"], "htf_direction_conflict")
        self.assertEqual(output.reason, "htf_direction_conflict")

    def test_pd_direction_conflict_does_not_create_anchorless_candidate(self):
        output = scan_scenarios(
            events=[
                event("HTF_CONTEXT_CONFIRMED", direction="bullish", index=0),
                event("POI_TOUCHED", direction="bullish", index=1),
            ],
            expected_direction="LONG",
            htf_structure={"trend": "bullish"},
            premium_discount={"valid_for_buy": False},
        )

        self.assertEqual(output.long_candidates, [])
        self.assertIsNone(output.best_long_scenario)
        self.assertEqual(output.direction_block_reasons["LONG"], "pd_invalid_for_direction")
        self.assertEqual(output.candidate_counts["long_total"], 0)

    def test_no_anchor_does_not_create_candidate(self):
        output = scan_scenarios(
            events=[
                event("HTF_CONTEXT_CONFIRMED", direction="bullish", index=0),
            ],
            expected_direction="LONG",
            htf_structure={"trend": "bullish"},
        )

        self.assertEqual(output.long_candidates, [])
        self.assertIsNone(output.best_long_scenario)
        self.assertEqual(output.candidate_counts["long_total"], 0)

    def test_top_candidates_output_limit(self):
        output = scan_scenarios(
            events=[
                event("HTF_CONTEXT_CONFIRMED", index=0),
                *[event("SFP_CONFIRMED", index=i, quality=60 + i) for i in range(1, 11)],
            ],
            expected_direction="LONG",
            htf_structure={"trend": "bullish"},
        )

        self.assertLessEqual(len(output.top_candidates), 5)
        self.assertEqual(output.top_candidates[0].rank, 1)
        self.assertEqual(output.candidate_counts["long_total"], 10)


if __name__ == "__main__":
    unittest.main()
