import logging

from .base import Message, NotificationChannel

log = logging.getLogger(__name__)


class EmailChannel(NotificationChannel):
    name = "email"

    def send(self, address: str, message: Message) -> None:
        self.validate_address(address)
        raise NotImplementedError(
            "Email-канал ещё не реализован — см. app/channels/email.py"
        )
