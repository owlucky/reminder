import logging

from .base import Message, NotificationChannel

log = logging.getLogger(__name__)


class ConsoleChannel(NotificationChannel):
    name = "console"

    def send(self, address: str, message: Message) -> None:
        self.validate_address(address)
        log.info(
            "[console -> %s] %s\n%s",
            address,
            message.title,
            message.body,
        )
