from .base import Message, NotificationChannel
from .console import ConsoleChannel
from .registry import (
    available_channels,
    get_channel,
    is_registered,
    register,
)
from .telegram import TelegramChannel


def setup_channels() -> None:

    register(TelegramChannel())
    register(ConsoleChannel())


__all__ = [
    "Message",
    "NotificationChannel",
    "setup_channels",
    "register",
    "get_channel",
    "is_registered",
    "available_channels",
]
