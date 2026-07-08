from dataclasses import asdict, dataclass, fields, is_dataclass
from typing import Any, Dict, Literal, Optional


Direction = Literal["bullish", "bearish", "neutral"]


@dataclass
class BaseSignalResult:
    detected: bool
    direction: Direction
    quality_score: float
    confidence: float
    reason: str

    def __post_init__(self) -> None:
        self.quality_score = _clamp_score(self.quality_score)
        self.confidence = _clamp_score(self.confidence)

    def __bool__(self) -> bool:
        return self.detected

    def get(self, key: str, default: Any = None) -> Any:
        return self.to_dict().get(key, default)

    def __getitem__(self, key: str) -> Any:
        data = self.to_dict()
        if key not in data:
            raise KeyError(key)
        return data[key]

    def to_dict(self) -> Dict[str, Any]:
        return _to_serializable_dict(self)


@dataclass
class StructureResult(BaseSignalResult):
    trend: Direction
    bos_detected: bool
    choch_detected: bool
    neutral: bool


@dataclass
class FVGResult(BaseSignalResult):
    upper: float
    lower: float
    midpoint: float
    tested: bool
    invalidated: bool
    age_bars: int


@dataclass
class SFPResult(BaseSignalResult):
    level: float
    liquidity_depth_atr: float
    rejection_strength: float
    swept: bool


@dataclass
class SetupContext:
    symbol: str
    timeframe: str
    trend: Direction
    structure: Optional[StructureResult]
    liquidity: Optional[object]
    fvg: Optional[FVGResult]
    sfp: Optional[SFPResult]
    displacement: Optional[object]
    premium_discount: Optional[object]

    def get(self, key: str, default: Any = None) -> Any:
        return self.to_dict().get(key, default)

    def __getitem__(self, key: str) -> Any:
        data = self.to_dict()
        if key not in data:
            raise KeyError(key)
        return data[key]

    def to_dict(self) -> Dict[str, Any]:
        return _to_serializable_dict(self)


def _clamp_score(value: Any) -> float:
    try:
        score = float(value)
    except (TypeError, ValueError):
        score = 0.0
    return max(0.0, min(100.0, score))


def _to_serializable_dict(value: Any) -> Dict[str, Any]:
    data = asdict(value)
    return {
        field.name: _serialize_value(data[field.name])
        for field in fields(value)
    }


def _serialize_value(value: Any) -> Any:
    if is_dataclass(value):
        if hasattr(value, "to_dict"):
            return value.to_dict()
        return asdict(value)
    if isinstance(value, dict):
        return {key: _serialize_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_serialize_value(item) for item in value]
    if hasattr(value, "to_dict"):
        return value.to_dict()
    return value
