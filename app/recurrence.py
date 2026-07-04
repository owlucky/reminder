import re
from datetime import datetime, timedelta

from dateutil.relativedelta import relativedelta

from .models import Recurrence

_DURATION_RE = re.compile(r"^\s*(\d+)\s*([smhdw]?)\s*$", re.IGNORECASE)
_UNIT_SECONDS = {
    "s": 1,
    "m": 60,
    "h": 3600,
    "d": 86400,
    "w": 604800,
    "": 1,  
}


def parse_duration(value: str | int) -> int:

    if isinstance(value, bool):  
        raise ValueError("Смещение не может быть булевым значением")
    if isinstance(value, int):
        seconds = value
    else:
        match = _DURATION_RE.match(str(value))
        if not match:
            raise ValueError(
                f"Некорректное смещение: {value!r}. "
                "Примеры: '0', '30m', '2h', '1d', '1w'"
            )
        amount, unit = match.groups()
        seconds = int(amount) * _UNIT_SECONDS[unit.lower()]

    if seconds < 0:
        raise ValueError("Смещение не может быть отрицательным")
    return seconds


def humanize_duration(seconds: int) -> str:


    if seconds <= 0:
        return "в момент события"

    units = [
        (604800, "нед."),
        (86400, "дн."),
        (3600, "ч."),
        (60, "мин."),
        (1, "сек."),
    ]
    parts: list[str] = []
    remaining = seconds
    for size, label in units:
        if remaining >= size:
            count, remaining = divmod(remaining, size)
            parts.append(f"{count} {label}")
    return "за " + " ".join(parts)


def has_future_occurrence(
    event_time: datetime,
    rule: Recurrence,
    interval: int,
    now: datetime,
) -> bool:

    if rule == Recurrence.once:
        return event_time >= now
    return True


def occurrence_at(
    anchor: datetime, rule: Recurrence, interval: int, k: int
) -> datetime:
    

    if rule == Recurrence.once or k == 0:
        return anchor
    if rule == Recurrence.daily:
        return anchor + timedelta(days=interval * k)
    if rule == Recurrence.weekly:
        return anchor + timedelta(weeks=interval * k)
    if rule == Recurrence.monthly:
        return anchor + relativedelta(months=interval * k)
    if rule == Recurrence.yearly:
        return anchor + relativedelta(years=interval * k)
    raise ValueError(f"Неизвестный тип повторения: {rule}")


def _estimate_k(anchor: datetime, rule: Recurrence, interval: int, start: datetime) -> int:


    if rule == Recurrence.daily:
        approx = (start - anchor).total_seconds() / (86400 * interval)
    elif rule == Recurrence.weekly:
        approx = (start - anchor).total_seconds() / (604800 * interval)
    elif rule == Recurrence.monthly:
        months = (start.year - anchor.year) * 12 + (start.month - anchor.month)
        approx = months / interval
    elif rule == Recurrence.yearly:
        approx = (start.year - anchor.year) / interval
    else:
        approx = 0
    return int(approx)


def occurrences_in_range(
    anchor: datetime,
    rule: Recurrence,
    interval: int,
    start: datetime,
    end: datetime,
    max_iter: int = 5000,
) -> list[datetime]:

    if end < start:
        return []
    if rule == Recurrence.once:
        return [anchor] if start <= anchor <= end else []

    interval = max(1, interval)

    k = max(0, _estimate_k(anchor, rule, interval, start) - 1)
    while k > 0 and occurrence_at(anchor, rule, interval, k) > start:
        k -= 1

    result: list[datetime] = []
    iterations = 0
    while iterations < max_iter:
        occ = occurrence_at(anchor, rule, interval, k)
        if occ > end:
            break
        if occ >= start:
            result.append(occ)
        k += 1
        iterations += 1
    return result
