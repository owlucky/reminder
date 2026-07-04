from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class Message:

    title: str
    body: str


class NotificationChannel(ABC):

    name: str = "base"

    @abstractmethod
    def send(self, address: str, message: Message) -> None:
        pass

    def validate_address(self, address: str) -> None:

        if not address:
            raise ValueError("Адрес получателя не может быть пустым")
