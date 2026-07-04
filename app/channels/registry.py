from .base import Message, NotificationChannel

_registry: dict[str, NotificationChannel] = {}


def register(channel: NotificationChannel) -> None:
    _registry[channel.name] = channel


def get_channel(name: str) -> NotificationChannel:
    channel = _registry.get(name)
    if channel is None:
        raise ValueError(
            f"Канал '{name}' не зарегистрирован. "
            f"Доступные каналы: {', '.join(available_channels()) or '—'}"
        )
    return channel


def is_registered(name: str) -> bool:
    return name in _registry


def available_channels() -> list[str]:
    return sorted(_registry)


__all__ = [
    "Message",
    "NotificationChannel",
    "register",
    "get_channel",
    "is_registered",
    "available_channels",
]
