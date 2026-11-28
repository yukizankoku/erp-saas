"""Port `Realtime` berbasis polling (cukup untuk fase Vercel)."""

from typing import Any, Protocol, runtime_checkable

from app.core.ports.storage import assert_tenant_owned
from app.core.tenancy.context import require_tenant_id


@runtime_checkable
class Realtime(Protocol):
    async def publish(self, channel: str, event: dict[str, Any]) -> None: ...

    async def poll(self, channel: str, since_cursor: int) -> tuple[list[dict[str, Any]], int]:
        """Event setelah `since_cursor` (0 = dari awal) dan cursor terbaru."""
        ...


def tenant_channel(name: str) -> str:
    """Nama channel terikat tenant konteks: `t/<tenant_id>/<name>` (fail-closed tanpa konteks)."""
    return assert_tenant_owned(f"t/{require_tenant_id()}/{name}")
