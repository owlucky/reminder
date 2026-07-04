import logging
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from .channels.registry import Message, get_channel
from .config import get_settings
from .models import Reminder, SentNotification
from .recurrence import humanize_duration, occurrences_in_range
from .utils import format_local, utcnow

log = logging.getLogger(__name__)


def build_message(
    reminder: Reminder,
    occurrence: datetime,
    offset: int,
    tz_name: str | None = None,
) -> Message:
    when = format_local(occurrence, tz_name)
    if offset <= 0:
        lead = "Наступает событие"
    else:
        lead = f"Напоминание ({humanize_duration(offset)} до события)"

    lines = [lead, f"Когда: {when}"]
    if reminder.description:
        lines.append("")
        lines.append(reminder.description)
    return Message(title=reminder.title, body="\n".join(lines))


def _already_sent(
    db: Session, reminder_id: int, occurrence: datetime, offset: int
) -> bool:
    stmt = select(SentNotification.id).where(
        SentNotification.reminder_id == reminder_id,
        SentNotification.occurrence_time == occurrence,
        SentNotification.offset_seconds == offset,
    )
    return db.execute(stmt).first() is not None


def _fire(
    db: Session,
    reminder: Reminder,
    occurrence: datetime,
    offset: int,
    fire_time: datetime,
) -> bool:

    recipients = [rc for rc in reminder.recipients if rc.enabled]

    successes = 0
    errors: list[str] = []
    for rc in recipients:
        try:
            channel = get_channel(rc.channel)
            message = build_message(reminder, occurrence, offset, rc.timezone)
            channel.send(rc.address, message)
            successes += 1
        except Exception as exc:  
            log.warning(
                "Не удалось отправить напоминание #%s получателю %s (%s): %s",
                reminder.id,
                rc.address,
                rc.channel,
                exc,
            )
            errors.append(f"{rc.channel}:{rc.address}: {exc}")

    if recipients and successes == 0:
        return False

    status = "sent" if successes else "no_recipients"
    db.add(
        SentNotification(
            reminder_id=reminder.id,
            occurrence_time=occurrence,
            fire_time=fire_time,
            offset_seconds=offset,
            status=status,
            detail="; ".join(errors) or None,
        )
    )
    db.commit()
    log.info(
        "Напоминание #%s отправлено (%s получателям, событие %s, смещение %sс)",
        reminder.id,
        successes,
        occurrence.isoformat(),
        offset,
    )
    return True


def is_active(reminder: Reminder, now: datetime) -> bool:

    if not reminder.enabled:
        return False
    if reminder.snooze_until and reminder.snooze_until > now:
        return False
    return True


def process_due(db: Session, now: datetime | None = None) -> int:

    settings = get_settings()
    now = now or utcnow()
    window_start = now - timedelta(seconds=settings.missed_grace_seconds)

    reminders = db.execute(select(Reminder)).scalars().all()
    fired = 0
    for reminder in reminders:
        if not is_active(reminder, now):
            continue

        offsets = reminder.offsets_seconds or [0]
        max_offset = max(offsets)
        occ_end = now + timedelta(seconds=max_offset) + timedelta(minutes=1)

        occurrences = occurrences_in_range(
            reminder.event_time,
            reminder.recurrence,
            reminder.interval,
            window_start,
            occ_end,
        )
        for occ in occurrences:
            for offset in offsets:
                fire_time = occ - timedelta(seconds=offset)
                if not (window_start <= fire_time <= now):
                    continue
                if _already_sent(db, reminder.id, occ, offset):
                    continue
                if _fire(db, reminder, occ, offset, fire_time):
                    fired += 1
    return fired
