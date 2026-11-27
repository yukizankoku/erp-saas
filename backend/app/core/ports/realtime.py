"""Port `Realtime` berbasis polling (cukup untuk fase Vercel)."""

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class Realtime(Protocol):
    async def publish(self, channel: str, event: dict[str, Any]) -> None: ...

    async def poll(self, channel: str, since_cursor: int) -> tuple[list[dict[str, Any]], int]:
        """Event setelah `since_cursor` (0 = dari awal) dan cursor terbaru."""
        ...
