import logging

from .base import Message, NotificationChannel

log = logging.getLogger(__name__)


class SmsChannel(NotificationChannel):
    name = "sms"

    def send(self, address: str, message: Message) -> None:
        self.validate_address(address)
        raise NotImplementedError(
            "SMS-канал ещё не реализован — см. app/channels/sms.py"
        )
