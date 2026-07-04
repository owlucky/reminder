import logging

from apscheduler.schedulers.background import BackgroundScheduler

from .config import get_settings
from .database import SessionLocal
from .dispatcher import process_due
from .utils import utcnow

log = logging.getLogger(__name__)

_scheduler: BackgroundScheduler | None = None


def _poll_job() -> None:
    db = SessionLocal()
    try:
        count = process_due(db)
        if count:
            log.debug("Опрос: отправлено уведомлений: %s", count)
    except Exception:
        log.exception("Ошибка в задании планировщика")
    finally:
        db.close()


def start_scheduler() -> BackgroundScheduler:
    global _scheduler
    if _scheduler is not None:
        return _scheduler

    settings = get_settings()
    _scheduler = BackgroundScheduler(timezone="UTC")
    _scheduler.add_job(
        _poll_job,
        trigger="interval",
        seconds=settings.poll_interval_seconds,
        id="reminder_poll",
        max_instances=1,
        coalesce=True,
        next_run_time=utcnow(),
    )
    _scheduler.start()
    log.info(
        "Планировщик запущен (интервал %s с)", settings.poll_interval_seconds
    )
    return _scheduler


def stop_scheduler() -> None:
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None
        log.info("Планировщик остановлен")
