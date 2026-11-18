"""Общий цикл воркера: арендовать задачу, доставить, сдать результат."""

import logging
import threading
from collections.abc import Callable

from workers.queue_client import QueueClient
from workers.service_client import ServiceError

log = logging.getLogger(__name__)


class PullLoop:
    def __init__(
        self,
        client: QueueClient,
        *,
        worker_id: str,
        channel: str,
        route_key: str | None,
        deliver: Callable[[dict], None],
        stop: threading.Event,
        poll_seconds: float,
    ) -> None:
        self._client = client
        self._worker_id = worker_id
        self._channel = channel
        self._route_key = route_key
        self._deliver = deliver
        self._stop = stop
        self._poll_seconds = poll_seconds

    def run(self) -> None:
        log.info(
            "Воркер %s слушает канал %s (route_key=%s)",
            self._worker_id,
            self._channel,
            self._route_key or "—",
        )
        while not self._stop.is_set():
            try:
                task = self._client.lease(
                    self._worker_id, self._channel, self._route_key
                )
            except ServiceError as exc:
                log.warning("Сервис отказал в задаче: %s", exc)
                self._stop.wait(3)
                continue
            except Exception:
                log.exception("Не удалось запросить задачу")
                self._stop.wait(3)
                continue

            if task is None:
                self._stop.wait(self._poll_seconds)
                continue

            try:
                self._deliver(task)
            except Exception as exc:
                log.warning("Доставка задачи #%s не удалась: %s", task.get("id"), exc)
                self._report(task["id"], False, str(exc))
            else:
                self._report(task["id"], True, None)

    def _report(self, task_id: int, success: bool, message: str | None) -> None:
        try:
            self._client.complete(task_id, self._worker_id, success, message)
        except Exception:
            log.exception("Не удалось сдать результат задачи #%s", task_id)
