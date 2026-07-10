from dataclasses import asdict, dataclass
from datetime import datetime, time, timedelta
from typing import Any, Dict, Optional, Sequence
from zoneinfo import ZoneInfo


DEFAULT_TIMEZONE = "Europe/Chisinau"
KILL_ZONES = (
    {"name": "London", "start": "10:00", "end": "12:00"},
    {"name": "New York", "start": "15:30", "end": "18:00"},
)


@dataclass(frozen=True)
class SessionResult:
    in_kill_zone: bool
    session_name: str
    local_time: str
    timezone: str
    minutes_to_session_end: Optional[int]
    minutes_to_next_session: Optional[int]
    reason: str

    def get(self, key: str, default: Any = None) -> Any:
        return asdict(self).get(key, default)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def evaluate_session(
    now: Optional[datetime] = None,
    timezone: str = DEFAULT_TIMEZONE,
    kill_zones: Sequence[Dict[str, str]] = KILL_ZONES,
) -> SessionResult:
    local_now = _local_now(now, timezone)
    current_minutes = (local_now.hour * 60) + local_now.minute

    parsed_zones = [
        {
            "name": zone["name"],
            "start": _parse_time_to_minutes(zone["start"]),
            "end": _parse_time_to_minutes(zone["end"]),
        }
        for zone in kill_zones
    ]

    for zone in parsed_zones:
        if zone["start"] <= current_minutes < zone["end"]:
            minutes_to_end = zone["end"] - current_minutes
            return SessionResult(
                in_kill_zone=True,
                session_name=zone["name"],
                local_time=local_now.strftime("%H:%M"),
                timezone=timezone,
                minutes_to_session_end=minutes_to_end,
                minutes_to_next_session=0,
                reason=f"{zone['name']} Kill Zone",
            )

    future_starts = [zone["start"] for zone in parsed_zones if zone["start"] > current_minutes]
    if future_starts:
        minutes_to_next = min(future_starts) - current_minutes
    else:
        minutes_to_next = (24 * 60 - current_minutes) + min(zone["start"] for zone in parsed_zones)

    return SessionResult(
        in_kill_zone=False,
        session_name="Outside KZ",
        local_time=local_now.strftime("%H:%M"),
        timezone=timezone,
        minutes_to_session_end=None,
        minutes_to_next_session=minutes_to_next,
        reason="Outside Kill Zone",
    )


def next_quarter_close(
    now: Optional[datetime] = None,
    timezone: str = DEFAULT_TIMEZONE,
    buffer_seconds: int = 5,
) -> datetime:
    local_now = _local_now(now, timezone)
    minutes_to_next = 15 - (local_now.minute % 15)
    if minutes_to_next == 0 and local_now.second >= buffer_seconds:
        minutes_to_next = 15
    target = local_now + timedelta(minutes=minutes_to_next)
    target = target.replace(second=buffer_seconds, microsecond=0)
    if target <= local_now:
        target = target + timedelta(minutes=15)
    return target


def _local_now(now: Optional[datetime], timezone: str) -> datetime:
    tz = ZoneInfo(timezone)
    if now is None:
        return datetime.now(tz)
    if now.tzinfo is None:
        return now.replace(tzinfo=tz)
    return now.astimezone(tz)


def _parse_time_to_minutes(value: str) -> int:
    parsed = time.fromisoformat(value)
    return (parsed.hour * 60) + parsed.minute
