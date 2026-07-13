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
        self.assertEqual(scenario.next_expected_step, "CHOCH_CONFIRMED")
        self.assertEqual(scenario.waiting_for, "bullish CHOCH/BOS after SFP")
        self.assertFalse(output.signal_allowed)

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

    def test_trigger_before_sfp_waits_for_fresh_confirmation(self):
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
        self.assertEqual(output.best_long_scenario.last_invalidated_component, "trigger_before_sfp")
        self.assertEqual(output.best_long_scenario.waiting_for, "bullish CHOCH/BOS after SFP")
        self.assertFalse(output.signal_allowed)

    def test_fvg_before_bos_waits_instead_of_invalidating_context(self):
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
        self.assertEqual(output.best_long_scenario.last_invalidated_component, "fvg_before_bos")
        self.assertEqual(output.best_long_scenario.waiting_for, "valid bullish FVG after SFP")

    def test_displacement_before_retest_waits_for_retest(self):
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

        self.assertEqual(output.best_long_scenario.status, "waiting_for_confirmation")
        self.assertIsNone(output.best_long_scenario.invalidated_reason)
        self.assertEqual(output.best_long_scenario.last_invalidated_component, "displacement_before_retest")
        self.assertEqual(output.best_long_scenario.waiting_for, "bullish FVG retest")

    def test_opposite_bos_after_sfp_waits_for_expected_confirmation(self):
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

        self.assertEqual(output.best_long_scenario.status, "waiting_for_confirmation")
        self.assertIsNone(output.best_long_scenario.invalidated_reason)
        self.assertEqual(output.best_long_scenario.last_invalidated_component, "opposite_bos_after_sfp")
        self.assertEqual(output.best_long_scenario.waiting_for, "bullish CHOCH/BOS after SFP")

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
        self.assertEqual(output.best_long_scenario.next_expected_step, "CHOCH_CONFIRMED")
        self.assertEqual(output.best_long_scenario.waiting_for, "bullish CHOCH/BOS after SFP")
        self.assertFalse(output.signal_allowed)

    def test_invalidated_fvg_after_structure_waits_for_valid_fvg(self):
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
        self.assertEqual(scenario.status, "waiting_for_confirmation")
        self.assertIsNone(scenario.invalidated_reason)
        self.assertEqual(scenario.last_invalidated_component, "fvg_invalidated")
        self.assertEqual(scenario.waiting_for, "valid bearish FVG after SFP")


if __name__ == "__main__":
    unittest.main()
