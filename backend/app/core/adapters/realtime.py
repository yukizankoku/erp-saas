from typing import Any

from app.core.ports.realtime import Realtime


class MemoryRealtime:
    """Event per channel dengan cursor integer naik-monoton (mulai dari 1)."""

    def __init__(self) -> None:
        self._events: dict[str, list[dict[str, Any]]] = {}

    async def publish(self, channel: str, event: dict[str, Any]) -> None:
        self._events.setdefault(channel, []).append(dict(event))

    async def poll(self, channel: str, since_cursor: int) -> tuple[list[dict[str, Any]], int]:
        events = self._events.get(channel, [])
        return [dict(e) for e in events[max(since_cursor, 0) :]], len(events)


_: Realtime = MemoryRealtime()
