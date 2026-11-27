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


@runtime_checkable
class Mailer(Protocol):
    async def send(self, message: EmailMessage) -> None: ...
