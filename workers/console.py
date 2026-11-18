"""Воркер канала console: печатает доставку в лог. Удобен без Telegram."""

import logging
import signal
import threading
import uuid

from app.config import get_settings
from workers.loop import PullLoop
from workers.queue_client import QueueClient

log = logging.getLogger(__name__)


def _deliver(task: dict) -> None:
    log.info(
        "[console -> %s] %s\n%s",
        task.get("address"),
        task.get("title"),
        task.get("body"),
    )


def main() -> None:
    settings = get_settings()
    logging.basicConfig(
        level=settings.log_level.upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    stop = threading.Event()

    def _request_stop(signum, frame) -> None:
        stop.set()

    signal.signal(signal.SIGINT, _request_stop)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, _request_stop)

    worker_id = settings.worker_id or f"console-{uuid.uuid4().hex[:8]}"
    queue = QueueClient(settings.service_grpc_target, settings.internal_token)
    loop = PullLoop(
        queue,
        worker_id=worker_id,
        channel="console",
        route_key=None,
        deliver=_deliver,
        stop=stop,
        poll_seconds=settings.worker_poll_seconds,
    )
    try:
        loop.run()
    finally:
        queue.close()


if __name__ == "__main__":
    main()
