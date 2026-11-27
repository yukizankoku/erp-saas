import logging

from app.core.ports.mailer import EmailMessage, Mailer

logger = logging.getLogger("app.mailer")


def _mask(address: str) -> str:
    local, _, domain = address.partition("@")
    return f"{local[:1]}***@{domain}" if domain else "***"


class ConsoleMailer:
    """Dev: tulis ke log (hanya penerima & subjek; isi pesan tidak dicatat)."""

    async def send(self, message: EmailMessage) -> None:
        recipients = ", ".join(_mask(a) for a in message.to)
        logger.info(
            "email to=%s subject=%s", recipients, message.subject.encode("unicode_escape").decode()
        )


class MemoryMailer:
    """Test: menyimpan pesan terkirim di `sent`."""

    def __init__(self) -> None:
        self.sent: list[EmailMessage] = []

    async def send(self, message: EmailMessage) -> None:
        self.sent.append(message)


_a: Mailer = ConsoleMailer()
_b: Mailer = MemoryMailer()
