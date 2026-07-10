import unittest

from core.models import FVGResult, SFPResult, StructureResult
from core.premium_discount import PremiumDiscountResult
from core.state_machine import (
    SniperEvent,
    SniperState,
    SniperStateMachine,
    StateMachineResult,
)


class SniperStateMachineTest(unittest.TestCase):
    def _premium_discount(self, zone, valid_for_buy, valid_for_sell):
        return PremiumDiscountResult(
            zone=zone,
            range_high=120,
            range_low=80,
            equilibrium=100,
            price=90 if zone == 'discount' else 110,
            distance_from_equilibrium_percent=-10 if zone == 'discount' else 10,
            valid_for_buy=valid_for_buy,
            valid_for_sell=valid_for_sell,
            reason='test',
        )

    def _neutral_structure(self):
        return StructureResult(
            detected=False,
            direction='neutral',
            quality_score=0,
            confidence=20,
            reason='neutral',
            trend='neutral',
            bos_detected=False,
            choch_detected=False,
            neutral=True,
        )

    def _structure(self, bos=False, choch=False):
        return StructureResult(
            detected=bos or choch,
            direction='bullish',
            quality_score=82,
            confidence=88,
            reason='test',
            trend='bullish',
            bos_detected=bos,
            choch_detected=choch,
            neutral=False,
        )

    def _valid_sequence(self):
        return [
            SniperEvent.HTF_CONTEXT_CONFIRMED,
            SniperEvent.POI_TOUCHED,
            SniperEvent.LIQUIDITY_SWEEP_CONFIRMED,
            SniperEvent.CHOCH_CONFIRMED,
            SniperEvent.BOS_CONFIRMED,
            SniperEvent.FVG_CREATED,
            SniperEvent.FVG_RETESTED,
            SniperEvent.DISPLACEMENT_CONFIRMED,
        ]

    def test_valid_buy_sequence_allows_signal(self):
        machine = SniperStateMachine(direction='bullish')

        result = machine.update(events=self._valid_sequence())

        self.assertIsInstance(result, StateMachineResult)
        self.assertEqual(result.state, SniperState.SIGNAL_READY)
        self.assertTrue(result.signal_allowed)
        self.assertEqual(result.direction, 'bullish')
        self.assertEqual(result.confidence, 100)
        self.assertEqual(result.missing_steps, [])

    def test_valid_sell_sequence_allows_signal(self):
        machine = SniperStateMachine(direction='bearish')

        result = machine.update(events=self._valid_sequence())

        self.assertEqual(result.state, SniperState.SIGNAL_READY)
        self.assertTrue(result.signal_allowed)
        self.assertEqual(result.direction, 'bearish')

    def test_fvg_before_choch_invalidates(self):
        machine = SniperStateMachine(direction='bullish')

        result = machine.update(events=[
            SniperEvent.HTF_CONTEXT_CONFIRMED,
            SniperEvent.POI_TOUCHED,
            SniperEvent.LIQUIDITY_SWEEP_CONFIRMED,
            SniperEvent.FVG_CREATED,
        ])

        self.assertEqual(result.state, SniperState.INVALIDATED)
        self.assertFalse(result.signal_allowed)
        self.assertIn('waiting for choch_confirmed', result.invalidation_reason)

    def test_bos_before_sweep_invalidates(self):
        machine = SniperStateMachine(direction='bullish')

        result = machine.update(events=[
            SniperEvent.HTF_CONTEXT_CONFIRMED,
            SniperEvent.POI_TOUCHED,
            SniperEvent.BOS_CONFIRMED,
        ])

        self.assertEqual(result.state, SniperState.INVALIDATED)
        self.assertIn('waiting for liquidity_sweep_confirmed', result.invalidation_reason)

    def test_retest_before_fvg_invalidates(self):
        machine = SniperStateMachine(direction='bearish')

        result = machine.update(events=[
            SniperEvent.HTF_CONTEXT_CONFIRMED,
            SniperEvent.POI_TOUCHED,
            SniperEvent.LIQUIDITY_SWEEP_CONFIRMED,
            SniperEvent.CHOCH_CONFIRMED,
            SniperEvent.BOS_CONFIRMED,
            SniperEvent.FVG_RETESTED,
        ])

        self.assertEqual(result.state, SniperState.INVALIDATED)
        self.assertIn('waiting for fvg_created', result.invalidation_reason)

    def test_timeout_after_liquidity_sweep_invalidates(self):
        machine = SniperStateMachine(direction='bullish')
        machine.update(events=[SniperEvent.HTF_CONTEXT_CONFIRMED], current_bar=0)
        machine.update(events=[SniperEvent.POI_TOUCHED], current_bar=1)
        machine.update(events=[SniperEvent.LIQUIDITY_SWEEP_CONFIRMED], current_bar=2)

        result = machine.update(current_bar=15)

        self.assertEqual(result.state, SniperState.INVALIDATED)
        self.assertEqual(result.invalidation_reason, 'Timeout after liquidity sweep')

    def test_neutral_htf_context_invalidates(self):
        machine = SniperStateMachine(direction='bullish')

        result = machine.update(structure_result=self._neutral_structure())

        self.assertEqual(result.state, SniperState.INVALIDATED)
        self.assertEqual(result.invalidation_reason, 'HTF context is neutral')

    def test_buy_in_premium_invalidates(self):
        machine = SniperStateMachine(direction='bullish')

        result = machine.update(
            premium_discount_result=self._premium_discount('premium', valid_for_buy=False, valid_for_sell=True)
        )

        self.assertEqual(result.state, SniperState.INVALIDATED)
        self.assertEqual(result.invalidation_reason, 'BUY in premium invalidates setup')

    def test_sell_in_discount_invalidates(self):
        machine = SniperStateMachine(direction='bearish')

        result = machine.update(
            premium_discount_result=self._premium_discount('discount', valid_for_buy=True, valid_for_sell=False)
        )

        self.assertEqual(result.state, SniperState.INVALIDATED)
        self.assertEqual(result.invalidation_reason, 'SELL in discount invalidates setup')

    def test_object_results_can_drive_valid_buy_sequence(self):
        machine = SniperStateMachine(direction='bullish')
        pd_result = self._premium_discount('discount', valid_for_buy=True, valid_for_sell=False)

        machine.update(structure_result=self._structure(), premium_discount_result=pd_result)
        machine.update(poi_touched=True, premium_discount_result=pd_result)
        machine.update(
            sfp_result=SFPResult(
                detected=True,
                direction='bullish',
                quality_score=80,
                confidence=82,
                reason='sell-side swept',
                level=95,
                liquidity_depth_atr=0.4,
                rejection_strength=85,
                swept=True,
            ),
            premium_discount_result=pd_result,
        )
        machine.update(structure_result=self._structure(choch=True), premium_discount_result=pd_result)
        machine.update(structure_result=self._structure(bos=True), premium_discount_result=pd_result)
        machine.update(
            fvg_result=FVGResult(
                detected=True,
                direction='bullish',
                quality_score=78,
                confidence=80,
                reason='fresh fvg',
                upper=105,
                lower=102,
                midpoint=103.5,
                tested=False,
                invalidated=False,
                age_bars=2,
            ),
            premium_discount_result=pd_result,
        )
        machine.update(
            fvg_result=FVGResult(
                detected=True,
                direction='bullish',
                quality_score=78,
                confidence=80,
                reason='retested fvg',
                upper=105,
                lower=102,
                midpoint=103.5,
                tested=True,
                invalidated=False,
                age_bars=4,
            ),
            premium_discount_result=pd_result,
        )
        result = machine.update(
            displacement_result={'valid': True, 'direction': 'bullish'},
            premium_discount_result=pd_result,
        )

        self.assertEqual(result.state, SniperState.SIGNAL_READY)
        self.assertTrue(result.signal_allowed)

    def test_fvg_retest_without_displacement_is_not_signal_ready(self):
        machine = SniperStateMachine(direction='bullish')
        pd_result = self._premium_discount('discount', valid_for_buy=True, valid_for_sell=False)

        result = machine.update(
            events=[
                SniperEvent.HTF_CONTEXT_CONFIRMED,
                SniperEvent.POI_TOUCHED,
                SniperEvent.LIQUIDITY_SWEEP_CONFIRMED,
                SniperEvent.CHOCH_CONFIRMED,
                SniperEvent.BOS_CONFIRMED,
                SniperEvent.FVG_CREATED,
                SniperEvent.FVG_RETESTED,
            ],
            current_bar=10,
        )

        self.assertEqual(result.state, SniperState.WAITING_FOR_DISPLACEMENT_CONFIRMATION)
        self.assertFalse(result.signal_allowed)
        self.assertEqual(result.missing_steps, [SniperEvent.DISPLACEMENT_CONFIRMED.value])


if __name__ == '__main__':
    unittest.main()
