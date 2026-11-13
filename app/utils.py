from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None, microsecond=0)


def to_naive_utc(dt: datetime) -> datetime:
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt.replace(microsecond=0)


def is_valid_timezone(name: str) -> bool:
    try:
        ZoneInfo(name)
        return True
    except Exception:
        return False


def local_to_utc(naive_local: datetime, tz_name: str | None) -> datetime:

    naive_local = naive_local.replace(microsecond=0)
    if not tz_name:
        return naive_local
    aware = naive_local.replace(tzinfo=ZoneInfo(tz_name))
    return aware.astimezone(timezone.utc).replace(tzinfo=None)


def utc_to_local(naive_utc: datetime, tz_name: str | None) -> datetime:

    aware_utc = naive_utc.replace(tzinfo=timezone.utc)
    if not tz_name:
        return aware_utc
    return aware_utc.astimezone(ZoneInfo(tz_name))


def _offset_label(offset: timedelta | None) -> str:
    total = int((offset or timedelta(0)).total_seconds() // 60)
    sign = "+" if total >= 0 else "-"
    hh, mm = divmod(abs(total), 60)
    return f"UTC{sign}{hh}" + (f":{mm:02d}" if mm else "")


def format_local(naive_utc: datetime, tz_name: str | None) -> str:

    if not tz_name:
        return naive_utc.strftime("%d.%m.%Y %H:%M") + " (UTC)"
    local = utc_to_local(naive_utc, tz_name)
    return local.strftime("%d.%m.%Y %H:%M") + f" ({_offset_label(local.utcoffset())})"
