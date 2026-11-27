import logging

from app.core.ports.mailer import EmailMessage, Mailer

logger = logging.getLogger("app.mailer")


class ConsoleMailer:
    """Dev: tulis ke log (hanya penerima & subjek; isi pesan tidak dicatat)."""

    async def send(self, message: EmailMessage) -> None:
        logger.info("email to=%s subject=%s", ", ".join(message.to), message.subject)


class MemoryMailer:
    """Test: menyimpan pesan terkirim di `sent`."""

    def __init__(self) -> None:
        self.sent: list[EmailMessage] = []

    async def send(self, message: EmailMessage) -> None:
        self.sent.append(message)


_a: Mailer = ConsoleMailer()
_b: Mailer = MemoryMailer()
