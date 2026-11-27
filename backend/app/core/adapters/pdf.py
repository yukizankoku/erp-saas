from app.core.ports.pdf import PdfRenderer


class UnavailablePdfRenderer:
    """PDF server-side belum tersedia di fase awal; cetak dilakukan di browser."""

    async def render(self, html: str) -> bytes:
        raise NotImplementedError("server-side PDF rendering is not available yet")


_: PdfRenderer = UnavailablePdfRenderer()
