from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..database import get_db
from ..dispatcher import build_message
from ..models import Recipient, Reminder
from ..recurrence import has_future_occurrence, occurrences_in_range
from ..schemas import (
    MessagePreview,
    ReminderCreate,
    ReminderRead,
    ReminderUpdate,
    SnoozeRequest,
    _normalize_offsets,
)
from ..utils import utcnow

router = APIRouter(prefix="/reminders", tags=["reminders"])


def _get_or_404(db: Session, reminder_id: int) -> Reminder:
    reminder = db.get(Reminder, reminder_id)
    if reminder is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Напоминание не найдено")
    return reminder


def _resolve_recipients(db: Session, ids: list[int]) -> list[Recipient]:
    if not ids:
        return []
    recipients = db.execute(
        select(Recipient).where(Recipient.id.in_(ids))
    ).scalars().all()
    found = {rc.id for rc in recipients}
    missing = set(ids) - found
    if missing:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"Получатели не найдены: {sorted(missing)}",
        )
    return list(recipients)


@router.post("", response_model=ReminderRead, status_code=status.HTTP_201_CREATED)
def create_reminder(payload: ReminderCreate, db: Session = Depends(get_db)):
    recipients = _resolve_recipients(db, payload.recipient_ids)
    reminder = Reminder(
        title=payload.title,
        description=payload.description,
        event_time=payload.event_time,
        recurrence=payload.recurrence,
        interval=payload.interval,
        offsets_seconds=_normalize_offsets(payload.offsets),
        recipients=recipients,
    )
    db.add(reminder)
    db.commit()
    db.refresh(reminder)
    return ReminderRead.from_model(reminder)


@router.get("", response_model=list[ReminderRead])
def list_reminders(db: Session = Depends(get_db)):
    reminders = db.execute(select(Reminder)).scalars().all()
    return [ReminderRead.from_model(r) for r in reminders]


@router.get("/{reminder_id}", response_model=ReminderRead)
def get_reminder(reminder_id: int, db: Session = Depends(get_db)):
    return ReminderRead.from_model(_get_or_404(db, reminder_id))


@router.patch("/{reminder_id}", response_model=ReminderRead)
def update_reminder(
    reminder_id: int, payload: ReminderUpdate, db: Session = Depends(get_db)
):
    reminder = _get_or_404(db, reminder_id)
    data = payload.model_dump(exclude_unset=True)

    if "offsets" in data and data["offsets"] is not None:
        reminder.offsets_seconds = _normalize_offsets(data.pop("offsets"))
    else:
        data.pop("offsets", None)

    if "recipient_ids" in data:
        reminder.recipients = _resolve_recipients(db, data.pop("recipient_ids") or [])

    for key, value in data.items():
        setattr(reminder, key, value)

    if not has_future_occurrence(
        reminder.event_time, reminder.recurrence, reminder.interval, utcnow()
    ):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Разовое напоминание нельзя перенести в прошлое",
        )

    db.commit()
    db.refresh(reminder)
    return ReminderRead.from_model(reminder)


@router.delete("/{reminder_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_reminder(reminder_id: int, db: Session = Depends(get_db)):
    reminder = _get_or_404(db, reminder_id)
    db.delete(reminder)
    db.commit()


@router.post("/{reminder_id}/enable", response_model=ReminderRead)
def enable_reminder(reminder_id: int, db: Session = Depends(get_db)):

    reminder = _get_or_404(db, reminder_id)
    reminder.enabled = True
    reminder.snooze_until = None
    db.commit()
    db.refresh(reminder)
    return ReminderRead.from_model(reminder)


@router.post("/{reminder_id}/disable", response_model=ReminderRead)
def disable_reminder(reminder_id: int, db: Session = Depends(get_db)):

    reminder = _get_or_404(db, reminder_id)
    reminder.enabled = False
    db.commit()
    db.refresh(reminder)
    return ReminderRead.from_model(reminder)


@router.post("/{reminder_id}/snooze", response_model=ReminderRead)
def snooze_reminder(
    reminder_id: int,
    payload: SnoozeRequest | None = None,
    db: Session = Depends(get_db),
):

    reminder = _get_or_404(db, reminder_id)
    payload = payload or SnoozeRequest()

    if payload.until is not None:
        reminder.snooze_until = payload.until
    else:
        days = payload.days or get_settings().default_snooze_days
        reminder.snooze_until = utcnow() + timedelta(days=days)

    db.commit()
    db.refresh(reminder)
    return ReminderRead.from_model(reminder)


@router.post("/{reminder_id}/unsnooze", response_model=ReminderRead)
def unsnooze_reminder(reminder_id: int, db: Session = Depends(get_db)):

    reminder = _get_or_404(db, reminder_id)
    reminder.snooze_until = None
    db.commit()
    db.refresh(reminder)
    return ReminderRead.from_model(reminder)


@router.get("/{reminder_id}/upcoming", response_model=list[str])
def upcoming_fire_times(
    reminder_id: int, days: int = 400, db: Session = Depends(get_db)
):

    reminder = _get_or_404(db, reminder_id)
    now = utcnow()
    horizon = now + timedelta(days=days)
    occurrences = occurrences_in_range(
        reminder.event_time, reminder.recurrence, reminder.interval, now, horizon
    )
    fire_times: set = set()
    for occ in occurrences:
        for offset in reminder.offsets_seconds or [0]:
            ft = occ - timedelta(seconds=offset)
            if now <= ft <= horizon:
                fire_times.add(ft)
    return [ft.isoformat() for ft in sorted(fire_times)]


@router.post("/{reminder_id}/test", response_model=MessagePreview)
def test_reminder(reminder_id: int, db: Session = Depends(get_db)):

    reminder = _get_or_404(db, reminder_id)

    errors: list[str] = []
    preview = build_message(reminder, reminder.event_time, 0)
    from ..channels.registry import get_channel

    for rc in reminder.recipients:
        if not rc.enabled:
            continue
        message = build_message(reminder, reminder.event_time, 0, rc.timezone)
        preview = message
        try:
            get_channel(rc.channel).send(rc.address, message)
        except Exception as exc:
            errors.append(f"{rc.channel}:{rc.address}: {exc}")

    if errors:
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            "Ошибки доставки: " + "; ".join(errors),
        )
    return MessagePreview(title=preview.title, body=preview.body)
