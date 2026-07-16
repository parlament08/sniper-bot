import unittest

from dataclasses import dataclass

from core.scenario_scanner import ScenarioEvent, scan_scenarios


def event(event_type, direction="bullish", index=1, quality=80, payload=None):
    return ScenarioEvent(
        event_type=event_type,
        direction=direction,
        index=index,
        quality_score=quality,
        payload=payload,
    )


@dataclass(frozen=True)
class PayloadObject:
    type: str
    index: object
    quality_score: int


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

    def test_fvg_before_confirmed_bos_is_not_counted(self):
        output = scan_scenarios(
            events=[
                event("HTF_CONTEXT_CONFIRMED", index=1),
                event("POI_TOUCHED", index=2),
                event("SFP_CONFIRMED", index=3),
                event("EARLY_TRIGGER_CONFIRMED", index=4),
                event("FVG_CREATED", index=4.5, payload={"created_index": 4.5}),
                event("CONFIRMED_TRIGGER_CONFIRMED", index=5, payload={"type": "bullish_bos"}),
                event("FVG_RETESTED", index=6, payload={"created_index": 4.5}),
                event("DISPLACEMENT_CONFIRMED", index=7, payload={"created_index": 4.5}),
                event("RISK_VALID", direction=None, index=8),
            ],
            expected_direction="LONG",
            htf_structure={"trend": "bullish"},
            premium_discount={"valid_for_buy": True},
        )

        scenario = output.selected_scenario
        self.assertEqual(scenario.next_expected_step, "FVG_CREATED")
        self.assertFalse(output.signal_allowed)

    def test_fvg_from_other_candidate_or_trigger_is_ignored(self):
        output = scan_scenarios(
            events=[
                event("HTF_CONTEXT_CONFIRMED", index=1),
                event("POI_TOUCHED", index=2),
                event("SFP_CONFIRMED", index=3),
                event("EARLY_TRIGGER_CONFIRMED", index=4),
                event("CONFIRMED_TRIGGER_CONFIRMED", index=5, payload={"type": "bullish_bos"}),
                event("FVG_CREATED", index=6, payload={"source_candidate_id": "OTHER"}),
                event("FVG_CREATED", index=7, payload={"source_confirmed_trigger_id": "OTHER_TRIGGER"}),
                event("FVG_RETESTED", index=8, payload={"source_candidate_id": "OTHER"}),
                event("DISPLACEMENT_CONFIRMED", index=9, payload={"source_candidate_id": "OTHER"}),
                event("RISK_VALID", direction=None, index=10),
            ],
            expected_direction="LONG",
            htf_structure={"trend": "bullish"},
            premium_discount={"valid_for_buy": True},
        )

        scenario = output.selected_scenario
        self.assertEqual(scenario.next_expected_step, "FVG_CREATED")
        self.assertFalse(output.signal_allowed)

    def test_opposite_fvg_is_not_counted_for_selected_candidate(self):
        output = scan_scenarios(
            events=[
                event("HTF_CONTEXT_CONFIRMED", index=1),
                event("POI_TOUCHED", index=2),
                event("SFP_CONFIRMED", index=3),
                event("EARLY_TRIGGER_CONFIRMED", index=4),
                event("CONFIRMED_TRIGGER_CONFIRMED", index=5, payload={"type": "bullish_bos"}),
                event("FVG_CREATED", direction="bearish", index=6),
                event("FVG_RETESTED", direction="bearish", index=7),
                event("DISPLACEMENT_CONFIRMED", direction="bearish", index=8),
                event("RISK_VALID", direction=None, index=9),
            ],
            expected_direction="LONG",
            htf_structure={"trend": "bullish"},
            premium_discount={"valid_for_buy": True},
        )

        scenario = output.selected_scenario
        self.assertEqual(scenario.next_expected_step, "FVG_CREATED")
        self.assertFalse(output.signal_allowed)

    def test_fvg_retest_must_happen_after_creation(self):
        output = scan_scenarios(
            events=[
                event("HTF_CONTEXT_CONFIRMED", index=1),
                event("POI_TOUCHED", index=2),
                event("SFP_CONFIRMED", index=3),
                event("EARLY_TRIGGER_CONFIRMED", index=4),
                event("CONFIRMED_TRIGGER_CONFIRMED", index=5, payload={"type": "bullish_bos"}),
                event("FVG_RETESTED", index=5.5),
                event("FVG_CREATED", index=6),
            ],
            expected_direction="LONG",
            htf_structure={"trend": "bullish"},
            premium_discount={"valid_for_buy": True},
        )

        self.assertIsNotNone(output.selected_scenario)
        self.assertNotEqual(output.selected_scenario.status, "complete")
        self.assertFalse(output.signal_allowed)

    def test_bos_displacement_does_not_replace_post_retest_displacement(self):
        output = scan_scenarios(
            events=[
                event("HTF_CONTEXT_CONFIRMED", index=1),
                event("POI_TOUCHED", index=2),
                event("SFP_CONFIRMED", index=3),
                event("EARLY_TRIGGER_CONFIRMED", index=4),
                event("CONFIRMED_TRIGGER_CONFIRMED", index=5, payload={"type": "bullish_bos"}),
                event("FVG_CREATED", index=6),
                event("DISPLACEMENT_CONFIRMED", index=6.5, payload={"displacement_stage": "bos_displacement"}),
                event("FVG_RETESTED", index=7),
                event("DISPLACEMENT_CONFIRMED", index=8, payload={"displacement_stage": "post_retest"}),
                event("RISK_VALID", direction=None, index=9),
            ],
            expected_direction="LONG",
            htf_structure={"trend": "bullish"},
            premium_discount={"valid_for_buy": True},
        )

        self.assertTrue(output.signal_allowed)
        used = [item.event_type for item in output.selected_scenario.events_used]
        self.assertEqual(used.count("DISPLACEMENT_CONFIRMED"), 1)

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
                event("CONFIRMED_TRIGGER_CONFIRMED", index=5, quality=84, payload={"type": "bullish_bos", "index": 5, "quality_score": 84, "candidate_id": None}),
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
        self.assertEqual(scenario.trigger_scan["confirmed_trigger"]["candidate_id"], scenario.candidate_id)
        self.assertEqual(scenario.trigger_scan["selected_trigger"]["candidate_id"], scenario.candidate_id)
        self.assertIsNotNone(scenario.scenario_key)
        self.assertIsNotNone(scenario.to_dict()["scenario_key"])
        self.assertEqual(scenario.trigger_scan["selected_trigger"], scenario.trigger_scan["confirmed_trigger"])
        self.assertEqual(scenario.trigger_scan["waiting_for"], "bullish FVG after confirmed BOS")

    def test_confirmed_sfp_chain_beats_newer_poi_only_candidate(self):
        output = scan_scenarios(
            events=[
                event("HTF_CONTEXT_CONFIRMED", direction="bearish", index=1),
                event("SFP_CONFIRMED", direction="bearish", index=3, quality=82),
                event("EARLY_TRIGGER_CONFIRMED", direction="bearish", index=4, quality=82),
                event(
                    "CONFIRMED_TRIGGER_CONFIRMED",
                    direction="bearish",
                    index=5,
                    quality=97,
                    payload={"type": "bearish_bos", "index": 5, "quality_score": 97},
                ),
                event("POI_TOUCHED", direction="bearish", index=10, quality=90),
            ],
            expected_direction="SHORT",
            htf_structure={"trend": "bearish"},
            premium_discount={"valid_for_sell": True},
        )

        scenario = output.selected_scenario
        self.assertEqual(scenario.anchor_type, "SFP_CONFIRMED")
        self.assertEqual(scenario.completed_steps, 5)
        self.assertTrue(scenario.trigger_scan["trigger_confirmed"])
        self.assertEqual(scenario.trigger_scan["confirmed_trigger"]["type"], "bearish_bos")
        self.assertEqual(scenario.waiting_for, "bearish FVG after confirmed BOS")

    def test_progress_beats_quality_and_recency_for_living_candidates(self):
        output = scan_scenarios(
            events=[
                event("HTF_CONTEXT_CONFIRMED", direction="bearish", index=1, quality=60),
                event("SFP_CONFIRMED", direction="bearish", index=3, quality=60),
                event("EARLY_TRIGGER_CONFIRMED", direction="bearish", index=4, quality=60),
                event(
                    "CONFIRMED_TRIGGER_CONFIRMED",
                    direction="bearish",
                    index=5,
                    quality=70,
                    payload={"type": "bearish_bos", "index": 5, "quality_score": 70},
                ),
                event("POI_TOUCHED", direction="bearish", index=100, quality=100),
            ],
            expected_direction="SHORT",
            htf_structure={"trend": "bearish"},
            premium_discount={"valid_for_sell": True},
        )

        scenario = output.selected_scenario
        self.assertEqual(scenario.anchor_type, "SFP_CONFIRMED")
        self.assertEqual(scenario.completed_steps, 5)
        self.assertTrue(scenario.trigger_scan["trigger_confirmed"])
        self.assertGreater(output.top_candidates[1].anchor_index, scenario.anchor_index)

    def test_invalidated_progressed_candidate_reports_selection_ineligible(self):
        output = scan_scenarios(
            events=[
                event("HTF_CONTEXT_CONFIRMED", direction="bearish", index=1, quality=60),
                event("SFP_CONFIRMED", direction="bearish", index=3, quality=80),
                event("EARLY_TRIGGER_CONFIRMED", direction="bearish", index=4, quality=82),
                event(
                    "CONFIRMED_TRIGGER_CONFIRMED",
                    direction="bearish",
                    index=5,
                    quality=97,
                    payload={"type": "bearish_bos", "index": 5, "quality_score": 97},
                ),
                event("FVG_CREATED", direction="bearish", index=6, quality=85, payload={"invalidated": True}),
                event("POI_TOUCHED", direction="bearish", index=10, quality=90),
            ],
            expected_direction="SHORT",
            htf_structure={"trend": "bearish"},
            premium_discount={"valid_for_sell": True},
        )

        selected = output.selected_scenario
        self.assertEqual(selected.anchor_type, "POI_TOUCHED")
        self.assertEqual(selected.rank, 2)
        self.assertTrue(selected.selection_eligible)
        self.assertIsNone(selected.selection_rejected_reason)

        snapshot = output.to_dict()
        top_candidate = snapshot["top_candidates"][0]
        self.assertEqual(top_candidate["status"], "invalidated")
        self.assertEqual(top_candidate["completed_steps"], 6)
        self.assertEqual(top_candidate["rank"], 1)
        self.assertEqual(top_candidate["progress_rank"], 1)
        self.assertFalse(top_candidate["selection_eligible"])
        self.assertEqual(top_candidate["selection_rejected_reason"], "candidate_invalidated")
        self.assertFalse(top_candidate["is_selected"])
        self.assertEqual(snapshot["selected_scenario"]["rank"], 2)
        self.assertTrue(snapshot["selected_scenario"]["selection_eligible"])

    def test_opposite_early_trigger_after_early_is_conflict_only(self):
        output = scan_scenarios(
            events=[
                event("HTF_CONTEXT_CONFIRMED", index=1),
                event("POI_TOUCHED", index=2),
                event("SFP_CONFIRMED", index=3),
                event("EARLY_TRIGGER_CONFIRMED", index=4, quality=88),
                event("EARLY_TRIGGER_CONFIRMED", direction="bearish", index=5, quality=92),
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
        self.assertTrue(scenario.trigger_scan["opposite_trigger_detected"])
        self.assertFalse(scenario.trigger_scan["candidate_invalidated"])
        self.assertEqual(scenario.waiting_for, "confirmed bullish BOS after early CHOCH")

    def test_opposite_confirmed_bos_after_early_invalidates_candidate(self):
        output = scan_scenarios(
            events=[
                event("HTF_CONTEXT_CONFIRMED", index=1),
                event("POI_TOUCHED", index=2),
                event("SFP_CONFIRMED", index=3),
                event("EARLY_TRIGGER_CONFIRMED", index=4, quality=88),
                event("CONFIRMED_TRIGGER_CONFIRMED", direction="bearish", index=5, quality=92, payload={"type": "bearish_bos"}),
            ],
            expected_direction="LONG",
            htf_structure={"trend": "bullish"},
            premium_discount={"valid_for_buy": True},
        )

        scenario = output.best_long_scenario
        self.assertEqual(scenario.status, "invalidated")
        self.assertEqual(scenario.invalidated_reason, "opposite_confirmed_bos")
        self.assertTrue(scenario.trigger_scan["opposite_trigger_detected"])
        self.assertEqual(scenario.trigger_scan["opposite_trigger_type"], "bearish_bos")
        self.assertEqual(scenario.trigger_scan["opposite_trigger_quality"], 92)
        self.assertEqual(scenario.trigger_scan["opposite_trigger_index"], "5")
        self.assertTrue(scenario.trigger_scan["candidate_invalidated"])
        self.assertFalse(output.signal_allowed)

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
        self.assertEqual(debug["rejected_candidates"][0]["candidate_id"], scenario.candidate_id)
        self.assertEqual(debug["rejected_candidates"][0]["candidate_anchor_index"], str(scenario.anchor_index))
        self.assertEqual(debug["rejected_candidates"][0]["early_trigger_index"], "4")

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
        self.assertEqual(output.best_long_scenario.invalidated_reason, "opposite_confirmed_bos")

    def test_opposite_bos_before_anchor_does_not_invalidate_new_candidate(self):
        output = scan_scenarios(
            events=[
                event("HTF_CONTEXT_CONFIRMED", index=1),
                event("BOS_CONFIRMED", direction="bearish", index=2, quality=95),
                event("SFP_CONFIRMED", index=3),
            ],
            expected_direction="LONG",
            htf_structure={"trend": "bullish"},
            premium_discount={"valid_for_buy": True},
        )

        self.assertEqual(output.best_long_scenario.status, "waiting_for_confirmation")
        self.assertIsNone(output.best_long_scenario.invalidated_reason)
        self.assertFalse(output.best_long_scenario.opposite_trigger_detected)

    def test_opposite_bos_from_unrelated_candidate_does_not_invalidate(self):
        output = scan_scenarios(
            events=[
                event("HTF_CONTEXT_CONFIRMED", index=1),
                event("SFP_CONFIRMED", index=3),
                event("EARLY_TRIGGER_CONFIRMED", index=4, quality=88),
                event(
                    "BOS_CONFIRMED",
                    direction="bearish",
                    index=5,
                    quality=95,
                    payload={"type": "bearish_bos", "candidate_id": "OTHER_SCENARIO"},
                ),
            ],
            expected_direction="LONG",
            htf_structure={"trend": "bullish"},
            premium_discount={"valid_for_buy": True},
        )

        self.assertEqual(output.best_long_scenario.status, "waiting_for_confirmation")
        self.assertIsNone(output.best_long_scenario.invalidated_reason)
        self.assertFalse(output.best_long_scenario.opposite_trigger_detected)

    def test_new_candidate_can_live_after_opposite_bos_invalidates_previous(self):
        output = scan_scenarios(
            events=[
                event("HTF_CONTEXT_CONFIRMED", index=1),
                event("SFP_CONFIRMED", index=3),
                event("EARLY_TRIGGER_CONFIRMED", index=4, quality=88),
                event("BOS_CONFIRMED", direction="bearish", index=5, quality=95, payload={"type": "bearish_bos"}),
                event("SFP_CONFIRMED", index=10, quality=82),
            ],
            expected_direction="LONG",
            htf_structure={"trend": "bullish"},
            premium_discount={"valid_for_buy": True},
        )

        self.assertEqual(len(output.long_candidates), 2)
        self.assertEqual(output.long_candidates[0].status, "invalidated")
        self.assertEqual(output.long_candidates[0].invalidated_reason, "opposite_confirmed_bos")
        self.assertEqual(output.selected_scenario, output.long_candidates[1])
        self.assertEqual(output.selected_scenario.anchor_index, 10)

    def test_opposite_confirmed_bos_prevents_a_plus_signal(self):
        output = scan_scenarios(
            events=[
                event("HTF_CONTEXT_CONFIRMED", index=1),
                event("SFP_CONFIRMED", index=2),
                event("EARLY_TRIGGER_CONFIRMED", index=3, quality=88),
                event("BOS_CONFIRMED", index=4, quality=90),
                event("FVG_CREATED", index=5),
                event("FVG_RETESTED", index=6),
                event("DISPLACEMENT_CONFIRMED", index=7),
                event("RISK_VALID", direction=None, index=8),
                event("BOS_CONFIRMED", direction="bearish", index=9, quality=95, payload={"type": "bearish_bos"}),
            ],
            expected_direction="LONG",
            htf_structure={"trend": "bullish"},
            premium_discount={"valid_for_buy": True},
        )

        self.assertEqual(output.best_long_scenario.status, "invalidated")
        self.assertEqual(output.best_long_scenario.invalidated_reason, "opposite_confirmed_bos")
        self.assertFalse(output.signal_allowed)
        self.assertFalse(output.scenario_valid)

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

    def test_repeated_poi_touch_reuses_candidate(self):
        output = scan_scenarios(
            events=[
                event("HTF_CONTEXT_CONFIRMED", index=0),
                event("POI_TOUCHED", index=1, payload={"poi_id": "discount-zone"}),
                event("POI_TOUCHED", index=2, payload={"poi_id": "discount-zone"}),
                event("POI_TOUCHED", index=3, payload={"poi_id": "discount-zone"}),
                event("POI_TOUCHED", index=4, payload={"poi_id": "discount-zone"}),
            ],
            expected_direction="LONG",
            htf_structure={"trend": "bullish"},
            premium_discount={"valid_for_buy": True},
        )

        self.assertEqual(len(output.long_candidates), 1)
        scenario = output.long_candidates[0]
        self.assertEqual(scenario.anchor_index, 1)
        self.assertEqual(scenario.anchor_first_touch_index, 1)
        self.assertEqual(scenario.anchor_last_touch_index, 4)
        self.assertEqual(scenario.age_bars, 3)
        self.assertEqual(scenario.update_count, 3)

    def test_repeated_pd_location_reuses_candidate_without_poi_touch(self):
        output = scan_scenarios(
            events=[
                event("HTF_CONTEXT_CONFIRMED", index=0),
                event("PD_LOCATION_VALID", index=1, payload={"zone": "discount", "range_timeframe": "4H"}),
                event("PD_LOCATION_VALID", index=2, payload={"zone": "discount", "range_timeframe": "4H"}),
                event("PD_LOCATION_VALID", index=3, payload={"zone": "discount", "range_timeframe": "4H"}),
            ],
            expected_direction="LONG",
            htf_structure={"trend": "bullish"},
            premium_discount={"valid_for_buy": True},
        )

        self.assertEqual(len(output.long_candidates), 1)
        scenario = output.long_candidates[0]
        self.assertEqual(scenario.anchor_type, "PD_LOCATION_VALID")
        self.assertEqual(scenario.anchor_index, 1)
        self.assertEqual(scenario.current_step, "pd_location_valid")
        self.assertEqual(scenario.age_bars, 2)
        self.assertIsNone(scenario.trigger_scan["poi_index"])
        self.assertEqual(scenario.trigger_scan["pd_location_index"], "1")

    def test_concrete_poi_keeps_poi_identity_and_bounds(self):
        output = scan_scenarios(
            events=[
                event("HTF_CONTEXT_CONFIRMED", index=0),
                event("POI_TOUCHED", index=1, payload={"poi_id": "fvg-1", "bottom": 10.0, "top": 11.0, "source": "fvg"}),
                event("POI_TOUCHED", index=2, payload={"poi_id": "fvg-1", "bottom": 10.0, "top": 11.0, "source": "fvg"}),
            ],
            expected_direction="LONG",
            htf_structure={"trend": "bullish"},
            premium_discount={"valid_for_buy": True},
        )

        self.assertEqual(len(output.long_candidates), 1)
        scenario = output.long_candidates[0]
        self.assertEqual(scenario.anchor_type, "POI_TOUCHED")
        self.assertEqual(scenario.trigger_scan["poi_index"], "1")
        self.assertEqual(scenario.events_used[1].payload["poi_id"], "fvg-1")
        self.assertEqual(scenario.events_used[1].payload["bottom"], 10.0)
        self.assertEqual(scenario.events_used[1].payload["top"], 11.0)

    def test_candidate_id_remains_stable_across_scans(self):
        first_scan = scan_scenarios(
            events=[
                event("HTF_CONTEXT_CONFIRMED", index=0),
                event("POI_TOUCHED", index=1, payload={"poi_id": "discount-zone"}),
            ],
            expected_direction="LONG",
            htf_structure={"trend": "bullish"},
            premium_discount={"valid_for_buy": True},
        )
        second_scan = scan_scenarios(
            events=[
                event("HTF_CONTEXT_CONFIRMED", index=0),
                event("POI_TOUCHED", index=1, payload={"poi_id": "discount-zone"}),
                event("POI_TOUCHED", index=2, payload={"poi_id": "discount-zone"}),
            ],
            expected_direction="LONG",
            htf_structure={"trend": "bullish"},
            premium_discount={"valid_for_buy": True},
        )

        self.assertEqual(first_scan.best_long_scenario.candidate_id, second_scan.best_long_scenario.candidate_id)
        self.assertEqual(second_scan.best_long_scenario.anchor_index, 1)
        self.assertGreater(second_scan.best_long_scenario.age_bars, first_scan.best_long_scenario.age_bars)

    def test_pd_candidate_id_remains_stable_across_stateless_scans_in_same_zone(self):
        base_payload = {
            "zone": "discount",
            "range_timeframe": "4H",
            "range_low": 1.512,
            "range_high": 1.557,
        }
        first_scan = scan_scenarios(
            events=[
                event("HTF_CONTEXT_CONFIRMED", index=0),
                event("PD_LOCATION_VALID", index="2026-07-16T16:45:00", payload={**base_payload, "zone_depth": "shallow"}),
            ],
            expected_direction="LONG",
            htf_structure={"trend": "bullish"},
            premium_discount={"valid_for_buy": True},
        )
        second_scan = scan_scenarios(
            events=[
                event("HTF_CONTEXT_CONFIRMED", index=0),
                event("PD_LOCATION_VALID", index="2026-07-16T17:00:00", payload={**base_payload, "zone_depth": "deep"}),
            ],
            expected_direction="LONG",
            htf_structure={"trend": "bullish"},
            premium_discount={"valid_for_buy": True},
        )

        self.assertEqual(first_scan.best_long_scenario.candidate_id, second_scan.best_long_scenario.candidate_id)
        self.assertNotIn("2026-07-16T164500", first_scan.best_long_scenario.candidate_id)
        self.assertNotIn("2026-07-16T170000", second_scan.best_long_scenario.candidate_id)

    def test_leaving_and_reentering_poi_creates_new_candidate(self):
        output = scan_scenarios(
            events=[
                event("HTF_CONTEXT_CONFIRMED", index=0),
                event("POI_TOUCHED", index=1, payload={"poi_id": "discount-zone", "poi_touch_session": "first"}),
                event("POI_LEFT", index=2, payload={"poi_id": "discount-zone"}),
                event("POI_TOUCHED", index=5, payload={"poi_id": "discount-zone", "poi_touch_session": "second"}),
            ],
            expected_direction="LONG",
            htf_structure={"trend": "bullish"},
            premium_discount={"valid_for_buy": True},
        )

        self.assertEqual(len(output.long_candidates), 2)
        self.assertNotEqual(output.long_candidates[0].candidate_id, output.long_candidates[1].candidate_id)
        self.assertEqual(output.long_candidates[1].anchor_index, 5)

    def test_invalidated_candidate_is_not_reused(self):
        output = scan_scenarios(
            events=[
                event("HTF_CONTEXT_CONFIRMED", index=0),
                event("POI_TOUCHED", index=1, payload={"poi_id": "discount-zone"}),
                event("INVALIDATION", direction=None, index=2, payload={"reason": "poi_invalidated"}),
                event("POI_TOUCHED", index=3, payload={"poi_id": "discount-zone"}),
            ],
            expected_direction="LONG",
            htf_structure={"trend": "bullish"},
            premium_discount={"valid_for_buy": True},
        )

        self.assertEqual(len(output.long_candidates), 2)
        self.assertEqual(output.long_candidates[0].status, "invalidated")
        self.assertEqual(output.long_candidates[1].anchor_index, 3)

    def test_new_sfp_upgrades_poi_candidate_without_duplicate(self):
        output = scan_scenarios(
            events=[
                event("HTF_CONTEXT_CONFIRMED", index=0),
                event("POI_TOUCHED", index=1, payload={"poi_id": "discount-zone"}),
                event("POI_TOUCHED", index=2, payload={"poi_id": "discount-zone"}),
                event("SFP_CONFIRMED", index=3, quality=88, payload={"type": "bullish_sfp"}),
            ],
            expected_direction="LONG",
            htf_structure={"trend": "bullish"},
            premium_discount={"valid_for_buy": True},
        )

        self.assertEqual(len(output.long_candidates), 1)
        scenario = output.long_candidates[0]
        self.assertEqual(scenario.anchor_type, "SFP_CONFIRMED")
        self.assertEqual(scenario.anchor_index, 3)
        self.assertEqual(scenario.anchor_first_touch_index, 1)
        self.assertEqual(scenario.completed_steps, 3)
        self.assertEqual(scenario.trigger_scan["poi_index"], "1")

    def test_dataclass_payload_is_serialized_for_candidate_snapshot(self):
        output = scan_scenarios(
            events=[
                event("HTF_CONTEXT_CONFIRMED", index=0),
                event("SFP_CONFIRMED", index=1, quality=88, payload={"type": "bullish_sfp"}),
                event("EARLY_TRIGGER_CONFIRMED", index=2, quality=84, payload=PayloadObject("bullish_early_choch", 2, 84)),
                event("CONFIRMED_TRIGGER_CONFIRMED", index=3, quality=92, payload=PayloadObject("bullish_bos", 3, 92)),
            ],
            expected_direction="LONG",
            htf_structure={"trend": "bullish"},
        )

        scenario = output.selected_scenario
        self.assertIsNotNone(scenario)
        self.assertEqual(scenario.trigger_scan["confirmed_trigger"]["type"], "bullish_bos")
        self.assertEqual(scenario.trigger_scan["confirmed_trigger"]["candidate_id"], scenario.candidate_id)
        self.assertEqual(scenario.trigger_scan["confirmed_trigger"]["candidate_anchor_index"], scenario.anchor_index)

    def test_candidates_from_opposite_directions_remain_separate(self):
        output = scan_scenarios(
            events=[
                event("HTF_CONTEXT_CONFIRMED", direction=None, index=0),
                event("POI_TOUCHED", direction="bullish", index=1, payload={"poi_id": "shared-zone"}),
                event("POI_TOUCHED", direction="bearish", index=1, payload={"poi_id": "shared-zone"}),
            ],
            htf_structure={"trend": "bullish"},
            premium_discount={"valid_for_buy": True, "valid_for_sell": True},
        )

        self.assertEqual(len(output.long_candidates), 1)
        self.assertEqual(output.short_candidates, [])
        bearish_output = scan_scenarios(
            events=[
                event("HTF_CONTEXT_CONFIRMED", direction=None, index=0),
                event("POI_TOUCHED", direction="bullish", index=1, payload={"poi_id": "shared-zone"}),
                event("POI_TOUCHED", direction="bearish", index=1, payload={"poi_id": "shared-zone"}),
            ],
            htf_structure={"trend": "bearish"},
            premium_discount={"valid_for_buy": True, "valid_for_sell": True},
        )
        self.assertEqual(bearish_output.long_candidates, [])
        self.assertEqual(len(bearish_output.short_candidates), 1)
        self.assertNotEqual(output.long_candidates[0].candidate_id, bearish_output.short_candidates[0].candidate_id)

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
