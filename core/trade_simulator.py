from dataclasses import asdict, dataclass
from typing import Any, Dict, Literal, Optional

import pandas as pd


TradeDirection = Literal["LONG", "SHORT"]
TradeOutcome = Literal["win", "loss", "timeout", "no_fill"]


@dataclass(frozen=True)
class TradeSimulationResult:
    direction: TradeDirection
    outcome: TradeOutcome
    entry: float
    stop_loss: float
    target_1: float
    target_2: Optional[float]
    bars_held: int
    gross_r: float
    net_r: float
    mae_r: float
    mfe_r: float
    exit_price: float
    exit_reason: str

    def get(self, key: str, default: Any = None) -> Any:
        return asdict(self).get(key, default)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def simulate_trade(
    candles: pd.DataFrame,
    direction: TradeDirection,
    entry: float,
    stop_loss: float,
    target_1: float,
    target_2: Optional[float] = None,
    max_bars: int = 96,
    fee_per_side_percent: float = 0.04,
    slippage_percent: float = 0.03,
) -> TradeSimulationResult:
    if candles.empty:
        return _result(direction, "no_fill", entry, stop_loss, target_1, target_2, 0, 0.0, 0.0, 0.0, entry, "no candles")

    risk_per_unit = _risk_per_unit(direction, entry, stop_loss)
    if risk_per_unit <= 0:
        return _result(direction, "no_fill", entry, stop_loss, target_1, target_2, 0, 0.0, 0.0, 0.0, entry, "invalid risk")

    mae_r = 0.0
    mfe_r = 0.0
    watched = candles.head(max_bars)

    for bar_number, (_, candle) in enumerate(watched.iterrows(), start=1):
        high = float(candle["high"])
        low = float(candle["low"])
        mae_r = min(mae_r, _adverse_r(direction, entry, low, high, risk_per_unit))
        mfe_r = max(mfe_r, _favorable_r(direction, entry, low, high, risk_per_unit))

        stop_hit = low <= stop_loss if direction == "LONG" else high >= stop_loss
        target_hit = high >= target_1 if direction == "LONG" else low <= target_1

        if stop_hit and target_hit:
            gross_r = -1.0
            return _result(direction, "loss", entry, stop_loss, target_1, target_2, bar_number, gross_r, mae_r, mfe_r, stop_loss, "SL and TP in same candle: conservative SL first", fee_per_side_percent, slippage_percent)
        if stop_hit:
            gross_r = -1.0
            return _result(direction, "loss", entry, stop_loss, target_1, target_2, bar_number, gross_r, mae_r, mfe_r, stop_loss, "stop_loss", fee_per_side_percent, slippage_percent)
        if target_hit:
            gross_r = _reward_r(direction, entry, target_1, risk_per_unit)
            return _result(direction, "win", entry, stop_loss, target_1, target_2, bar_number, gross_r, mae_r, mfe_r, target_1, "target_1", fee_per_side_percent, slippage_percent)

    last_close = float(watched.iloc[-1]["close"])
    gross_r = _reward_r(direction, entry, last_close, risk_per_unit)
    return _result(direction, "timeout", entry, stop_loss, target_1, target_2, len(watched), gross_r, mae_r, mfe_r, last_close, "timeout_close", fee_per_side_percent, slippage_percent)


def _result(direction, outcome, entry, stop_loss, target_1, target_2, bars_held, gross_r, mae_r, mfe_r, exit_price, exit_reason, fee_per_side_percent=0.0, slippage_percent=0.0):
    cost_r = ((fee_per_side_percent * 2) + slippage_percent) / 100
    risk_percent = abs(entry - stop_loss) / entry if entry else 0.0
    cost_in_r = cost_r / risk_percent if risk_percent > 0 else 0.0
    return TradeSimulationResult(
        direction=direction,
        outcome=outcome,
        entry=round(entry, 8),
        stop_loss=round(stop_loss, 8),
        target_1=round(target_1, 8),
        target_2=round(target_2, 8) if target_2 is not None else None,
        bars_held=bars_held,
        gross_r=round(gross_r, 4),
        net_r=round(gross_r - cost_in_r, 4),
        mae_r=round(mae_r, 4),
        mfe_r=round(mfe_r, 4),
        exit_price=round(exit_price, 8),
        exit_reason=exit_reason,
    )


def _risk_per_unit(direction: TradeDirection, entry: float, stop_loss: float) -> float:
    return entry - stop_loss if direction == "LONG" else stop_loss - entry


def _reward_r(direction: TradeDirection, entry: float, price: float, risk_per_unit: float) -> float:
    reward = price - entry if direction == "LONG" else entry - price
    return reward / risk_per_unit if risk_per_unit > 0 else 0.0


def _adverse_r(direction: TradeDirection, entry: float, low: float, high: float, risk_per_unit: float) -> float:
    adverse_price = low if direction == "LONG" else high
    return min(_reward_r(direction, entry, adverse_price, risk_per_unit), 0.0)


def _favorable_r(direction: TradeDirection, entry: float, low: float, high: float, risk_per_unit: float) -> float:
    favorable_price = high if direction == "LONG" else low
    return max(_reward_r(direction, entry, favorable_price, risk_per_unit), 0.0)
