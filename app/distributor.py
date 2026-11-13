"""Распределитель: воркеры забирают задачи своего канала и сдают результат.

Несколько процессов одного канала конкурируют за очередь. Задача с route_key
достаётся только воркеру с тем же ключом — так два Telegram-бота не перехватывают
чаты друг друга. Пустой route_key может взять любой воркер канала.
"""

import logging
import threading
from datetime import datetime, timedelta

from sqlalchemy import or_, select, update
from sqlalchemy.orm import Session

from .config import get_settings
from .models import DeliveryTask
from .utils import utcnow

log = logging.getLogger(__name__)

_lock = threading.Lock()
_workers: dict[str, dict] = {}


def note_worker(
    worker_id: str,
    channel: str,
    route_key: str | None,
    *,
    delivered: int = 0,
    failed: int = 0,
) -> None:
    now = utcnow()
    with _lock:
        state = _workers.get(worker_id)
        if state is None:
            state = {
                "worker_id": worker_id,
                "channel": channel,
                "route_key": route_key,
                "last_seen": now,
                "delivered": 0,
                "failed": 0,
            }
            _workers[worker_id] = state
        state["channel"] = channel
        if route_key is not None:
            state["route_key"] = route_key
        state["last_seen"] = now
        state["delivered"] += delivered
        state["failed"] += failed


def list_workers() -> list[dict]:
    now = utcnow()
    with _lock:
        snapshot = [dict(item) for item in _workers.values()]
    for item in snapshot:
        seen: datetime = item["last_seen"]
        item["last_seen"] = seen.isoformat()
        item["alive"] = (now - seen).total_seconds() < 90
    snapshot.sort(key=lambda item: (item["channel"], item["worker_id"]))
    return snapshot


def release_expired(db: Session, now: datetime | None = None) -> None:
    now = now or utcnow()
    db.execute(
        update(DeliveryTask)
        .where(
            DeliveryTask.status == "leased",
            DeliveryTask.lease_until.is_not(None),
            DeliveryTask.lease_until < now,
        )
        .values(status="queued", lease_owner=None, lease_until=None)
        .execution_options(synchronize_session=False)
    )
    db.commit()


def lease_next(
    db: Session,
    worker_id: str,
    channel: str,
    route_key: str | None,
) -> DeliveryTask | None:
    now = utcnow()
    release_expired(db, now)
    note_worker(worker_id, channel, route_key)

    stmt = (
        select(DeliveryTask)
        .where(
            DeliveryTask.status == "queued",
            DeliveryTask.channel == channel,
            DeliveryTask.available_at <= now,
            or_(
                DeliveryTask.route_key.is_(None),
                DeliveryTask.route_key == route_key,
            ),
        )
        .order_by(DeliveryTask.id)
        .limit(5)
    )
    candidates = db.execute(stmt).scalars().all()
    lease_until = now + timedelta(seconds=get_settings().task_lease_seconds)
    for task in candidates:
        result = db.execute(
            update(DeliveryTask)
            .where(DeliveryTask.id == task.id, DeliveryTask.status == "queued")
            .values(
                status="leased",
                lease_owner=worker_id,
                lease_until=lease_until,
            )
            .execution_options(synchronize_session=False)
        )
        if result.rowcount != 1:
            continue
        db.commit()
        db.refresh(task)
        log.info(
            "Задача #%s (%s) отдана воркеру %s",
            task.id,
            task.channel,
            worker_id,
        )
        return task
    db.commit()
    return None


def complete(
    db: Session,
    task_id: int,
    worker_id: str,
    success: bool,
    message: str | None,
) -> DeliveryTask:
    task = db.get(DeliveryTask, task_id)
    if task is None:
        raise KeyError(task_id)
    if task.status != "leased" or task.lease_owner != worker_id:
        raise PermissionError(task_id)

    now = utcnow()
    if success:
        task.status = "sent"
        task.last_error = None
        task.lease_owner = None
        task.lease_until = None
        note_worker(worker_id, task.channel, task.route_key, delivered=1)
        log.info("Задача #%s выполнена воркером %s", task.id, worker_id)
    else:
        task.attempts += 1
        task.last_error = (message or "")[:2000] or None
        task.lease_owner = None
        task.lease_until = None
        limit = get_settings().max_delivery_attempts
        if task.attempts >= limit:
            task.status = "failed"
            log.warning(
                "Задача #%s провалена окончательно (%s): %s",
                task.id,
                task.attempts,
                task.last_error,
            )
        else:
            task.status = "queued"
            delay = min(300, 2 ** task.attempts)
            task.available_at = now + timedelta(seconds=delay)
            log.warning(
                "Задача #%s вернётся в очередь через %s с: %s",
                task.id,
                delay,
                task.last_error,
            )
        note_worker(worker_id, task.channel, task.route_key, failed=1)

    db.commit()
    db.refresh(task)
    return task
