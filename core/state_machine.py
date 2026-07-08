from dataclasses import asdict, dataclass
from enum import Enum
from typing import Any, Dict, Iterable, List, Optional, Tuple

from core.models import Direction


class SniperState(str, Enum):
    WAITING_FOR_HTF_CONTEXT = "waiting_for_htf_context"
    WAITING_FOR_POI_TOUCH = "waiting_for_poi_touch"
    WAITING_FOR_LIQUIDITY_SWEEP = "waiting_for_liquidity_sweep"
    WAITING_FOR_CHOCH = "waiting_for_choch"
    WAITING_FOR_BOS = "waiting_for_bos"
    WAITING_FOR_FVG = "waiting_for_fvg"
    WAITING_FOR_FVG_RETEST = "waiting_for_fvg_retest"
    WAITING_FOR_DISPLACEMENT_CONFIRMATION = "waiting_for_displacement_confirmation"
    SIGNAL_READY = "signal_ready"
    INVALIDATED = "invalidated"


class SniperEvent(str, Enum):
    HTF_CONTEXT_CONFIRMED = "htf_context_confirmed"
    POI_TOUCHED = "poi_touched"
    LIQUIDITY_SWEEP_CONFIRMED = "liquidity_sweep_confirmed"
    CHOCH_CONFIRMED = "choch_confirmed"
    BOS_CONFIRMED = "bos_confirmed"
    FVG_CREATED = "fvg_created"
    FVG_RETESTED = "fvg_retested"
    DISPLACEMENT_CONFIRMED = "displacement_confirmed"
    INVALIDATION = "invalidation"
    TIMEOUT = "timeout"


@dataclass(frozen=True)
class StateMachineConfig:
    max_bars_after_poi_touch: int = 20
    max_bars_after_liquidity_sweep: int = 12
    max_bars_after_choch: int = 10
    max_bars_after_bos: int = 10
    max_bars_after_fvg_created: int = 16
    max_bars_after_fvg_retest: int = 8


@dataclass(frozen=True)
class StateMachineResult:
    state: SniperState
    signal_allowed: bool
    direction: Direction
    completed_steps: List[str]
    missing_steps: List[str]
    invalidation_reason: Optional[str]
    confidence: float

    def get(self, key: str, default: Any = None) -> Any:
        return self.to_dict().get(key, default)

    def __getitem__(self, key: str) -> Any:
        data = self.to_dict()
        if key not in data:
            raise KeyError(key)
        return data[key]

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["state"] = self.state.value
        return data


class SniperStateMachine:
    _FLOW: Tuple[SniperEvent, ...] = (
        SniperEvent.HTF_CONTEXT_CONFIRMED,
        SniperEvent.POI_TOUCHED,
        SniperEvent.LIQUIDITY_SWEEP_CONFIRMED,
        SniperEvent.CHOCH_CONFIRMED,
        SniperEvent.BOS_CONFIRMED,
        SniperEvent.FVG_CREATED,
        SniperEvent.FVG_RETESTED,
        SniperEvent.DISPLACEMENT_CONFIRMED,
    )

    _STATE_BY_NEXT_EVENT: Dict[SniperEvent, SniperState] = {
        SniperEvent.HTF_CONTEXT_CONFIRMED: SniperState.WAITING_FOR_HTF_CONTEXT,
        SniperEvent.POI_TOUCHED: SniperState.WAITING_FOR_POI_TOUCH,
        SniperEvent.LIQUIDITY_SWEEP_CONFIRMED: SniperState.WAITING_FOR_LIQUIDITY_SWEEP,
        SniperEvent.CHOCH_CONFIRMED: SniperState.WAITING_FOR_CHOCH,
        SniperEvent.BOS_CONFIRMED: SniperState.WAITING_FOR_BOS,
        SniperEvent.FVG_CREATED: SniperState.WAITING_FOR_FVG,
        SniperEvent.FVG_RETESTED: SniperState.WAITING_FOR_FVG_RETEST,
        SniperEvent.DISPLACEMENT_CONFIRMED: SniperState.WAITING_FOR_DISPLACEMENT_CONFIRMATION,
    }

    _TIMEOUTS_BY_STATE: Dict[SniperState, Tuple[str, str]] = {
        SniperState.WAITING_FOR_LIQUIDITY_SWEEP: ("max_bars_after_poi_touch", "Timeout after POI touch"),
        SniperState.WAITING_FOR_CHOCH: ("max_bars_after_liquidity_sweep", "Timeout after liquidity sweep"),
        SniperState.WAITING_FOR_BOS: ("max_bars_after_choch", "Timeout after CHoCH"),
        SniperState.WAITING_FOR_FVG: ("max_bars_after_bos", "Timeout after BOS"),
        SniperState.WAITING_FOR_FVG_RETEST: ("max_bars_after_fvg_created", "Timeout after FVG created"),
        SniperState.WAITING_FOR_DISPLACEMENT_CONFIRMATION: ("max_bars_after_fvg_retest", "Timeout after FVG retest"),
    }

    def __init__(self, direction: Direction, config: Optional[StateMachineConfig] = None):
        if direction not in ("bullish", "bearish"):
            raise ValueError("SniperStateMachine direction must be bullish or bearish")

        self.direction = direction
        self.config = config or StateMachineConfig()
        self.state = SniperState.WAITING_FOR_HTF_CONTEXT
        self.completed_steps: List[str] = []
        self._next_index = 0
        self._last_step_bar: Optional[int] = None
        self._invalidation_reason: Optional[str] = None

    def update(
        self,
        events: Optional[Iterable[SniperEvent]] = None,
        current_bar: Optional[int] = None,
        structure_result: Optional[object] = None,
        liquidity_map: Optional[object] = None,
        fvg_result: Optional[object] = None,
        sfp_result: Optional[object] = None,
        displacement_result: Optional[object] = None,
        premium_discount_result: Optional[object] = None,
        poi_touched: bool = False,
    ) -> StateMachineResult:
        if self.state == SniperState.INVALIDATED:
            return self.result()

        self._check_context_filters(structure_result, premium_discount_result)
        if self.state == SniperState.INVALIDATED:
            return self.result()

        self._check_timeout(current_bar)
        if self.state == SniperState.INVALIDATED:
            return self.result()

        inferred_events = self._infer_events(
            structure_result=structure_result,
            liquidity_map=liquidity_map,
            fvg_result=fvg_result,
            sfp_result=sfp_result,
            displacement_result=displacement_result,
            premium_discount_result=premium_discount_result,
            poi_touched=poi_touched,
        )

        for event in list(events or []) + inferred_events:
            self._apply_event(event, current_bar)
            if self.state == SniperState.INVALIDATED:
                break

        return self.result()

    def result(self) -> StateMachineResult:
        missing_steps = [event.value for event in self._FLOW[self._next_index:]]
        confidence = 0.0 if self.state == SniperState.INVALIDATED else round((len(self.completed_steps) / len(self._FLOW)) * 100, 2)

        return StateMachineResult(
            state=self.state,
            signal_allowed=self.state == SniperState.SIGNAL_READY,
            direction=self.direction,
            completed_steps=list(self.completed_steps),
            missing_steps=missing_steps,
            invalidation_reason=self._invalidation_reason,
            confidence=confidence,
        )

    def _apply_event(self, event: SniperEvent, current_bar: Optional[int]) -> None:
        if self.state in (SniperState.INVALIDATED, SniperState.SIGNAL_READY):
            return

        if event == SniperEvent.INVALIDATION:
            self._invalidate("Invalidation event")
            return
        if event == SniperEvent.TIMEOUT:
            self._invalidate("Timeout")
            return

        expected = self._FLOW[self._next_index]
        if event != expected:
            self._invalidate(f"Unexpected {event.value} while waiting for {expected.value}")
            return

        self.completed_steps.append(event.value)
        self._next_index += 1
        self._last_step_bar = current_bar

        if self._next_index >= len(self._FLOW):
            self.state = SniperState.SIGNAL_READY
            return

        self.state = self._STATE_BY_NEXT_EVENT[self._FLOW[self._next_index]]

    def _check_context_filters(self, structure_result: Optional[object], premium_discount_result: Optional[object]) -> None:
        if _get(structure_result, "neutral", False) or _get(structure_result, "trend") == "neutral":
            self._invalidate("HTF context is neutral")
            return

        if premium_discount_result is None:
            return

        is_valid = (
            _get(premium_discount_result, "valid_for_buy", False)
            if self.direction == "bullish"
            else _get(premium_discount_result, "valid_for_sell", False)
        )
        if not is_valid:
            zone = _get(premium_discount_result, "zone", "unknown")
            self._invalidate(f"{self._trade_label()} in {zone} invalidates setup")

    def _check_timeout(self, current_bar: Optional[int]) -> None:
        if current_bar is None or self._last_step_bar is None:
            return

        timeout_attr = self._TIMEOUTS_BY_STATE.get(self.state)
        if timeout_attr is None:
            return

        timeout = getattr(self.config, timeout_attr[0])
        reason = timeout_attr[1]
        if current_bar - self._last_step_bar > timeout:
            self._invalidate(reason)

    def _infer_events(
        self,
        structure_result: Optional[object],
        liquidity_map: Optional[object],
        fvg_result: Optional[object],
        sfp_result: Optional[object],
        displacement_result: Optional[object],
        premium_discount_result: Optional[object],
        poi_touched: bool,
    ) -> List[SniperEvent]:
        events: List[SniperEvent] = []

        if self._should_infer(SniperEvent.HTF_CONTEXT_CONFIRMED) and self._htf_context_confirmed(structure_result, premium_discount_result):
            events.append(SniperEvent.HTF_CONTEXT_CONFIRMED)
        if self._should_infer(SniperEvent.POI_TOUCHED) and poi_touched:
            events.append(SniperEvent.POI_TOUCHED)
        if self._should_infer(SniperEvent.LIQUIDITY_SWEEP_CONFIRMED) and self._liquidity_sweep_confirmed(sfp_result, liquidity_map):
            events.append(SniperEvent.LIQUIDITY_SWEEP_CONFIRMED)
        if self._should_infer(SniperEvent.CHOCH_CONFIRMED) and self._structure_event_confirmed(structure_result, "choch"):
            events.append(SniperEvent.CHOCH_CONFIRMED)
        if self._should_infer(SniperEvent.BOS_CONFIRMED) and self._structure_event_confirmed(structure_result, "bos"):
            events.append(SniperEvent.BOS_CONFIRMED)
        if self._should_infer(SniperEvent.FVG_CREATED) and self._fvg_created(fvg_result):
            events.append(SniperEvent.FVG_CREATED)
        if self._should_infer(SniperEvent.FVG_RETESTED) and self._fvg_retested(fvg_result):
            events.append(SniperEvent.FVG_RETESTED)
        if self._should_infer(SniperEvent.DISPLACEMENT_CONFIRMED) and self._displacement_confirmed(displacement_result):
            events.append(SniperEvent.DISPLACEMENT_CONFIRMED)

        return events

    def _should_infer(self, event: SniperEvent) -> bool:
        return self._FLOW.index(event) >= self._next_index

    def _htf_context_confirmed(self, structure_result: Optional[object], premium_discount_result: Optional[object]) -> bool:
        trend = _get(structure_result, "trend")
        if trend == self.direction:
            return True
        if premium_discount_result is None:
            return False
        return _get(premium_discount_result, "valid_for_buy" if self.direction == "bullish" else "valid_for_sell", False)

    def _liquidity_sweep_confirmed(self, sfp_result: Optional[object], liquidity_map: Optional[object]) -> bool:
        if sfp_result is not None:
            detected = _get(sfp_result, "detected", True)
            swept = _get(sfp_result, "swept", True)
            direction = _get(sfp_result, "direction")
            struct_type = _get(sfp_result, "type", "")
            direction_ok = direction in (None, self.direction) or self.direction in str(struct_type)
            return bool(detected and swept and direction_ok)

        if liquidity_map is None:
            return False
        target = _get(liquidity_map, "nearest_sell_side") if self.direction == "bullish" else _get(liquidity_map, "nearest_buy_side")
        return bool(target and _get(target, "swept", False))

    def _structure_event_confirmed(self, structure_result: Optional[object], event_name: str) -> bool:
        if structure_result is None:
            return False

        detected_key = f"{event_name}_detected"
        if _get(structure_result, detected_key, False) is True:
            direction = _get(structure_result, "direction", self.direction)
            return direction == self.direction

        struct_type = str(_get(structure_result, "type", ""))
        detected = _get(structure_result, "detected", _get(structure_result, "confirmed", False))
        return bool(detected and event_name in struct_type and self.direction in struct_type)

    def _fvg_created(self, fvg_result: Optional[object]) -> bool:
        if fvg_result is None:
            return False
        detected = _get(fvg_result, "detected", True)
        invalidated = _get(fvg_result, "invalidated", False)
        direction = _get(fvg_result, "direction")
        fvg_type = _get(fvg_result, "type", "")
        direction_ok = direction == self.direction or self.direction in str(fvg_type)
        return bool(detected and not invalidated and direction_ok)

    def _fvg_retested(self, fvg_result: Optional[object]) -> bool:
        return self._fvg_created(fvg_result) and bool(_get(fvg_result, "tested", False))

    def _displacement_confirmed(self, displacement_result: Optional[object]) -> bool:
        if displacement_result is None:
            return False
        valid = _get(displacement_result, "valid", False)
        direction = _get(displacement_result, "direction")
        return bool(valid and direction == self.direction)

    def _invalidate(self, reason: str) -> None:
        self.state = SniperState.INVALIDATED
        self._invalidation_reason = reason

    def _trade_label(self) -> str:
        return "BUY" if self.direction == "bullish" else "SELL"


def _get(source: Optional[object], key: str, default: Any = None) -> Any:
    if source is None:
        return default
    if isinstance(source, dict):
        return source.get(key, default)
    if hasattr(source, "get"):
        return source.get(key, default)
    return getattr(source, key, default)
