import dataclasses
from collections.abc import Mapping
from typing import Optional


def payload_to_dict(payload: Optional[object]) -> dict:
    if payload is None:
        return {}

    if isinstance(payload, dict):
        return dict(payload)

    if dataclasses.is_dataclass(payload):
        return dataclasses.asdict(payload)

    if isinstance(payload, Mapping):
        return dict(payload.items())

    if hasattr(payload, "to_dict"):
        return payload.to_dict()

    if hasattr(payload, "__dict__"):
        return {
            key: value
            for key, value in vars(payload).items()
            if not key.startswith("_")
        }

    raise TypeError(f"Unsupported payload type: {type(payload).__name__}")
