"""Формирователь: из наступивших напоминаний делает задачи доставки.

Один проход планировщика только ставит строки в delivery_tasks.
Кто и как отправит сообщение, решает распределитель и воркер канала.
"""

import logging
import uuid
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .messages import Message, build_message
from .models import DeliveryTask, Recipient, Reminder, SentNotification
from .recurrence import occurrences_in_range
from .utils import utcnow

log = logging.getLogger(__name__)


def is_active(reminder: Reminder, now: datetime) -> bool:
    if not reminder.enabled:
        return False
    if reminder.snooze_until and reminder.snooze_until > now:
        return False
    return True


def _legacy_sent(
    db: Session, reminder_id: int, occurrence: datetime, offset: int
) -> bool:
    """Журнал старой схемы: одна запись на все каналы сразу."""
    stmt = select(SentNotification.id).where(
        SentNotification.reminder_id == reminder_id,
        SentNotification.occurrence_time == occurrence,
        SentNotification.offset_seconds == offset,
    )
    return db.execute(stmt).first() is not None


def _dedupe_key(
    reminder_id: int, recipient_id: int, occurrence: datetime, offset: int
) -> str:
    return (
        f"scheduled:{reminder_id}:{recipient_id}:"
        f"{occurrence.isoformat()}:{offset}"
    )


def enqueue(
    db: Session,
    *,
    dedupe_key: str,
    reminder_id: int,
    recipient: Recipient,
    occurrence: datetime,
    fire_time: datetime,
    offset: int,
    kind: str,
    message: Message,
    available_at: datetime | None = None,
) -> DeliveryTask | None:
    task = DeliveryTask(
        dedupe_key=dedupe_key,
        reminder_id=reminder_id,
        recipient_id=recipient.id,
        occurrence_time=occurrence,
        fire_time=fire_time,
        offset_seconds=offset,
        channel=recipient.channel,
        route_key=recipient.route_key,
        address=recipient.address,
        title=message.title,
        body=message.body,
        kind=kind,
        status="queued",
        available_at=available_at or utcnow(),
    )
    db.add(task)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        return None
    db.refresh(task)
    return task


def formulate_due(db: Session, now: datetime | None = None) -> int:
    from .config import get_settings

    settings = get_settings()
    now = now or utcnow()
    window_start = now - timedelta(seconds=settings.missed_grace_seconds)

    reminders = db.execute(select(Reminder)).scalars().all()
    pending: list[dict] = []
    for reminder in reminders:
        if not is_active(reminder, now):
            continue
        offsets = list(reminder.offsets_seconds or [0])
        occ_end = now + timedelta(seconds=max(offsets)) + timedelta(minutes=1)
        occurrences = occurrences_in_range(
            reminder.event_time,
            reminder.recurrence,
            reminder.interval,
            window_start,
            occ_end,
        )
        recipients = [
            {
                "id": rc.id,
                "channel": rc.channel,
                "address": rc.address,
                "route_key": rc.route_key,
                "timezone": rc.timezone,
                "enabled": rc.enabled,
            }
            for rc in reminder.recipients
            if rc.enabled
        ]
        for occ in occurrences:
            for offset in offsets:
                fire_time = occ - timedelta(seconds=offset)
                if not (window_start <= fire_time <= now):
                    continue
                if _legacy_sent(db, reminder.id, occ, offset):
                    continue
                for rc in recipients:
                    message = build_message(
                        reminder.title,
                        reminder.description,
                        occ,
                        offset,
                        rc["timezone"],
                    )
                    pending.append(
                        {
                            "dedupe_key": _dedupe_key(
                                reminder.id, rc["id"], occ, offset
                            ),
                            "reminder_id": reminder.id,
                            "recipient_id": rc["id"],
                            "occurrence": occ,
                            "fire_time": fire_time,
                            "offset": offset,
                            "channel": rc["channel"],
                            "route_key": rc["route_key"],
                            "address": rc["address"],
                            "title": message.title,
                            "body": message.body,
                        }
                    )

    created = 0
    for item in pending:
        task = DeliveryTask(
            dedupe_key=item["dedupe_key"],
            reminder_id=item["reminder_id"],
            recipient_id=item["recipient_id"],
            occurrence_time=item["occurrence"],
            fire_time=item["fire_time"],
            offset_seconds=item["offset"],
            channel=item["channel"],
            route_key=item["route_key"],
            address=item["address"],
            title=item["title"],
            body=item["body"],
            kind="scheduled",
            status="queued",
            available_at=now,
        )
        db.add(task)
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            continue
        created += 1

    if created:
        log.info("Сформулировано задач доставки: %s", created)
    return created


def enqueue_test(db: Session, reminder: Reminder) -> tuple[Message, int]:
    """Тестовая отправка: отдельные задачи, дедупликация расписания их не глушит."""
    preview = build_message(reminder.title, reminder.description, reminder.event_time, 0)
    queued = 0
    now = utcnow()
    for rc in reminder.recipients:
        if not rc.enabled:
            continue
        message = build_message(
            reminder.title, reminder.description, reminder.event_time, 0, rc.timezone
        )
        preview = message
        task = enqueue(
            db,
            dedupe_key=f"test:{uuid.uuid4()}",
            reminder_id=reminder.id,
            recipient=rc,
            occurrence=reminder.event_time,
            fire_time=now,
            offset=0,
            kind="test",
            message=message,
            available_at=now,
        )
        if task is not None:
            queued += 1
    return preview, queued
