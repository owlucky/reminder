"""Имена каналов, под которые можно поднять воркер.

Отправка живёт в процессе воркера. Сервис только ставит задачу в очередь
с полем channel и не импортирует клиенты Telegram, Макса и т.д.
"""

KNOWN_CHANNELS = ("console", "max", "telegram")


def is_known_channel(name: str) -> bool:
    return name in KNOWN_CHANNELS
