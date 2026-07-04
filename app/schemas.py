from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .models import Recurrence
from .recurrence import has_future_occurrence, humanize_duration, parse_duration
from .utils import is_valid_timezone, to_naive_utc, utcnow


def _check_timezone(v: str | None) -> str | None:
    if v is not None and not is_valid_timezone(v):
        raise ValueError(f"Неизвестный часовой пояс: {v!r}")
    return v


class RecipientBase(BaseModel):
    name: str = Field(..., max_length=200)
    channel: str = Field(default="telegram", max_length=50)
    address: str = Field(..., max_length=200)
    timezone: str | None = Field(default=None, max_length=64)
    enabled: bool = True

    @field_validator("timezone")
    @classmethod
    def _tz(cls, v: str | None) -> str | None:
        return _check_timezone(v)


class RecipientCreate(RecipientBase):
    pass


class RecipientUpdate(BaseModel):
    name: str | None = Field(default=None, max_length=200)
    channel: str | None = Field(default=None, max_length=50)
    address: str | None = Field(default=None, max_length=200)
    timezone: str | None = Field(default=None, max_length=64)
    enabled: bool | None = None

    @field_validator("timezone")
    @classmethod
    def _tz(cls, v: str | None) -> str | None:
        return _check_timezone(v)


class RecipientRead(RecipientBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    created_at: datetime


def _normalize_offsets(offsets: list[str | int] | None) -> list[int]:
    if not offsets:
        return [0]
    seconds = sorted({parse_duration(o) for o in offsets}, reverse=True)
    return seconds


class ReminderBase(BaseModel):
    title: str = Field(..., max_length=300)
    description: str | None = None
    event_time: datetime
    recurrence: Recurrence = Recurrence.once
    interval: int = Field(default=1, ge=1)
    offsets: list[str | int] = Field(default_factory=lambda: ["0"])

    @field_validator("event_time")
    @classmethod
    def _event_time_utc(cls, v: datetime) -> datetime:
        return to_naive_utc(v)

    @field_validator("offsets")
    @classmethod
    def _validate_offsets(cls, v: list[str | int]) -> list[str | int]:
       
        for item in v:
            parse_duration(item)
        return v


class ReminderCreate(ReminderBase):
    recipient_ids: list[int] = Field(default_factory=list)

    @model_validator(mode="after")
    def _not_in_past(self) -> "ReminderCreate":
        if not has_future_occurrence(
            self.event_time, self.recurrence, self.interval, utcnow()
        ):
            raise ValueError(
                "Разовое напоминание нельзя создать в прошлом — "
                "укажите будущие дату и время"
            )
        return self


class ReminderUpdate(BaseModel):
    title: str | None = Field(default=None, max_length=300)
    description: str | None = None
    event_time: datetime | None = None
    recurrence: Recurrence | None = None
    interval: int | None = Field(default=None, ge=1)
    offsets: list[str | int] | None = None
    enabled: bool | None = None
    recipient_ids: list[int] | None = None

    @field_validator("event_time")
    @classmethod
    def _event_time_utc(cls, v: datetime | None) -> datetime | None:
        return to_naive_utc(v) if v is not None else None

    @field_validator("offsets")
    @classmethod
    def _validate_offsets(cls, v: list[str | int] | None) -> list[str | int] | None:
        if v is not None:
            for item in v:
                parse_duration(item)
        return v


class OffsetInfo(BaseModel):
    seconds: int
    label: str


class ReminderRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    description: str | None
    event_time: datetime
    recurrence: Recurrence
    interval: int
    offsets: list[OffsetInfo]
    enabled: bool
    snooze_until: datetime | None
    state: str
    recipients: list[RecipientRead]
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_model(cls, reminder) -> "ReminderRead":
        now = utcnow()
        if not reminder.enabled:
            state = "disabled"
        elif reminder.snooze_until and reminder.snooze_until > now:
            state = "snoozed"
        else:
            state = "active"

        offsets = [
            OffsetInfo(seconds=s, label=humanize_duration(s))
            for s in (reminder.offsets_seconds or [0])
        ]
        return cls(
            id=reminder.id,
            title=reminder.title,
            description=reminder.description,
            event_time=reminder.event_time,
            recurrence=reminder.recurrence,
            interval=reminder.interval,
            offsets=offsets,
            enabled=reminder.enabled,
            snooze_until=reminder.snooze_until,
            state=state,
            recipients=[RecipientRead.model_validate(rc) for rc in reminder.recipients],
            created_at=reminder.created_at,
            updated_at=reminder.updated_at,
        )


class SnoozeRequest(BaseModel):

    days: int | None = Field(default=None, ge=1)
    until: datetime | None = None

    @field_validator("until")
    @classmethod
    def _until_utc(cls, v: datetime | None) -> datetime | None:
        return to_naive_utc(v) if v is not None else None


class MessagePreview(BaseModel):
    title: str
    body: str


__all__ = [
    "RecipientCreate",
    "RecipientUpdate",
    "RecipientRead",
    "ReminderCreate",
    "ReminderUpdate",
    "ReminderRead",
    "OffsetInfo",
    "SnoozeRequest",
    "MessagePreview",
    "_normalize_offsets",
]
