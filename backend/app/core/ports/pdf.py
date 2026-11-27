"""Port `PdfRenderer` (stub; fase awal mencetak di browser, PRD §6.5)."""

from typing import Protocol, runtime_checkable


@runtime_checkable
class PdfRenderer(Protocol):
    async def render(self, html: str) -> bytes: ...
