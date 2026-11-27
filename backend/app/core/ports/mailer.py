"""Port `Mailer`."""

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True, slots=True)
class EmailMessage:
    to: tuple[str, ...]
    subject: str
    text: str
    html: str | None = None
    reply_to: str | None = None

    def __post_init__(self) -> None:
        # Cegah header injection (CRLF) pada penyedia yang merangkai header.
        for value in (*self.to, self.subject, self.reply_to or ""):
            if "\r" in value or "\n" in value:
                raise ValueError("CR/LF is not allowed in email headers")


@runtime_checkable
class Mailer(Protocol):
    async def send(self, message: EmailMessage) -> None: ...
