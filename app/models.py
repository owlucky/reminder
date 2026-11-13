from datetime import datetime

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Table,
    Text,
    UniqueConstraint,
)
from sqlalchemy.types import JSON
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base
from .recurrence import Recurrence
from .utils import utcnow


reminder_recipients = Table(
    "reminder_recipients",
    Base.metadata,
    Column(
        "reminder_id",
        ForeignKey("reminders.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column(
        "recipient_id",
        ForeignKey("recipients.id", ondelete="CASCADE"),
        primary_key=True,
    ),
)


class Recipient(Base):
    __tablename__ = "recipients"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    channel: Mapped[str] = mapped_column(String(50), default="telegram")
    address: Mapped[str] = mapped_column(String(200))
    # Какой экземпляр воркера владеет адресом. Для Telegram — "telegram:<bot_id>".
    # Пустое значение может забрать любой воркер этого канала.
    route_key: Mapped[str | None] = mapped_column(String(200), nullable=True)
    timezone: Mapped[str | None] = mapped_column(String(64), nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    reminders: Mapped[list["Reminder"]] = relationship(
        secondary=reminder_recipients,
        back_populates="recipients",
    )


class Reminder(Base):


    __tablename__ = "reminders"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(300))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    event_time: Mapped[datetime] = mapped_column(DateTime)

    recurrence: Mapped[Recurrence] = mapped_column(
        Enum(Recurrence), default=Recurrence.once
    )
    interval: Mapped[int] = mapped_column(Integer, default=1)

    offsets_seconds: Mapped[list[int]] = mapped_column(JSON, default=list)

    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    snooze_until: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, onupdate=utcnow
    )

    recipients: Mapped[list[Recipient]] = relationship(
        secondary=reminder_recipients,
        back_populates="reminders",
    )
    sent_notifications: Mapped[list["SentNotification"]] = relationship(
        back_populates="reminder",
        cascade="all, delete-orphan",
    )


class SentNotification(Base):
    __tablename__ = "sent_notifications"
    __table_args__ = (
        UniqueConstraint(
            "reminder_id",
            "occurrence_time",
            "offset_seconds",
            name="uq_sent_once",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    reminder_id: Mapped[int] = mapped_column(
        ForeignKey("reminders.id", ondelete="CASCADE")
    )
    occurrence_time: Mapped[datetime] = mapped_column(DateTime)
    fire_time: Mapped[datetime] = mapped_column(DateTime)
    offset_seconds: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(30), default="sent")
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    reminder: Mapped[Reminder] = relationship(back_populates="sent_notifications")


class DeliveryTask(Base):
    """Одна доставка одному получателю. Формирует сервис, забирает воркер."""

    __tablename__ = "delivery_tasks"
    __table_args__ = (
        UniqueConstraint("dedupe_key", name="uq_delivery_dedupe"),
        Index("ix_delivery_pull", "status", "channel", "available_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    dedupe_key: Mapped[str] = mapped_column(String(200))
    reminder_id: Mapped[int] = mapped_column(
        ForeignKey("reminders.id", ondelete="CASCADE")
    )
    recipient_id: Mapped[int] = mapped_column(
        ForeignKey("recipients.id", ondelete="CASCADE")
    )
    occurrence_time: Mapped[datetime] = mapped_column(DateTime)
    fire_time: Mapped[datetime] = mapped_column(DateTime)
    offset_seconds: Mapped[int] = mapped_column(Integer)
    channel: Mapped[str] = mapped_column(String(50))
    route_key: Mapped[str | None] = mapped_column(String(200), nullable=True)
    address: Mapped[str] = mapped_column(String(200))
    title: Mapped[str] = mapped_column(String(300))
    body: Mapped[str] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(String(20), default="scheduled")
    status: Mapped[str] = mapped_column(String(20), default="queued")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    available_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    lease_owner: Mapped[str | None] = mapped_column(String(100), nullable=True)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, onupdate=utcnow
    )
