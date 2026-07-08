import unittest

from core.models import (
    BaseSignalResult,
    FVGResult,
    SFPResult,
    SetupContext,
    StructureResult,
)


class ConfidenceModelTest(unittest.TestCase):
    def test_base_signal_clamps_quality_and_confidence(self):
        result = BaseSignalResult(
            detected=True,
            direction='bullish',
            quality_score=140,
            confidence=-20,
            reason='test',
        )

        self.assertTrue(result.detected)
        self.assertTrue(result)
        self.assertEqual(result.quality_score, 100)
        self.assertEqual(result.confidence, 0)
        self.assertEqual(result.get('direction'), 'bullish')
        self.assertEqual(result['reason'], 'test')

    def test_structure_result_serializes_and_reads_fields(self):
        result = StructureResult(
            detected=True,
            direction='bearish',
            quality_score=84,
            confidence=91,
            reason='confirmed sequence',
            trend='bearish',
            bos_detected=True,
            choch_detected=False,
            neutral=False,
        )

        data = result.to_dict()
        self.assertEqual(data['quality_score'], 84)
        self.assertEqual(data['confidence'], 91)
        self.assertTrue(data['bos_detected'])
        self.assertFalse(result.get('neutral'))

    def test_fvg_result_has_quality_confidence_and_zone_fields(self):
        result = FVGResult(
            detected=True,
            direction='bullish',
            quality_score=76.5,
            confidence=88.0,
            reason='fresh imbalance',
            upper=110.0,
            lower=105.0,
            midpoint=107.5,
            tested=True,
            invalidated=False,
            age_bars=6,
        )

        self.assertEqual(result['upper'], 110.0)
        self.assertEqual(result['midpoint'], 107.5)
        self.assertFalse(result['invalidated'])

    def test_sfp_result_has_quality_confidence_and_sweep_fields(self):
        result = SFPResult(
            detected=True,
            direction='bullish',
            quality_score=82,
            confidence=79,
            reason='swept sell-side and reclaimed',
            level=95.0,
            liquidity_depth_atr=0.42,
            rejection_strength=91,
            swept=True,
        )

        self.assertTrue(result.get('swept'))
        self.assertEqual(result.get('liquidity_depth_atr'), 0.42)
        self.assertEqual(result.to_dict()['confidence'], 79)

    def test_setup_context_serializes_nested_results(self):
        structure = StructureResult(
            detected=True,
            direction='bullish',
            quality_score=80,
            confidence=85,
            reason='HH/HL',
            trend='bullish',
            bos_detected=True,
            choch_detected=False,
            neutral=False,
        )
        context = SetupContext(
            symbol='BTC',
            timeframe='15m',
            trend='bullish',
            structure=structure,
            liquidity=None,
            fvg=None,
            sfp=None,
            displacement=None,
            premium_discount=None,
        )

        data = context.to_dict()
        self.assertEqual(data['symbol'], 'BTC')
        self.assertEqual(data['structure']['quality_score'], 80)
        self.assertEqual(context.get('trend'), 'bullish')


if __name__ == '__main__':
    unittest.main()
