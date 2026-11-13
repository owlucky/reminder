"""Процесс одного Telegram-бота.

Один токен — один процесс: long polling апдейтов и разбор очереди доставки.
Второй бот — второй процесс с другим TELEGRAM_BOT_TOKEN. route_key
`telegram:<id бота>` не даёт им забирать чаты друг друга.
"""

import html
import logging
import signal
import threading

import httpx

from app.config import get_settings
from workers.loop import PullLoop
from workers.service_client import ServiceClient
from workers.telegram_bot import TelegramBot

log = logging.getLogger(__name__)


def bot_route_key(token: str, api_base: str) -> str:
    response = httpx.get(f"{api_base}/bot{token}/getMe", timeout=15)
    response.raise_for_status()
    body = response.json()
    if not body.get("ok"):
        raise RuntimeError(f"getMe не удался: {body}")
    return f"telegram:{body['result']['id']}"


def deliver(token: str, api_base: str, task: dict) -> None:
    text = (
        f"<b>{html.escape(task['title'])}</b>\n\n"
        f"{html.escape(task['body'])}"
    )
    response = httpx.post(
        f"{api_base}/bot{token}/sendMessage",
        json={
            "chat_id": task["address"],
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        },
        timeout=15,
    )
    if response.status_code != 200:
        raise RuntimeError(
            f"Telegram API вернул {response.status_code}: {response.text}"
        )


def main() -> None:
    settings = get_settings()
    logging.basicConfig(
        level=settings.log_level.upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    token = settings.telegram_bot_token
    if not token:
        log.error("TELEGRAM_BOT_TOKEN не задан — воркер Telegram не запущен")
        raise SystemExit(0)

    route_key = bot_route_key(token, settings.telegram_api_base)
    worker_id = settings.worker_id or f"telegram-{route_key.split(':', 1)[1]}"
    client = ServiceClient(settings.service_url, settings.internal_token)
    stop = threading.Event()

    def _request_stop(signum, frame) -> None:
        stop.set()

    signal.signal(signal.SIGINT, _request_stop)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, _request_stop)

    bot = TelegramBot(token, settings.telegram_api_base, client, route_key)
    bot.start()
    loop = PullLoop(
        client,
        worker_id=worker_id,
        channel="telegram",
        route_key=route_key,
        deliver=lambda task: deliver(token, settings.telegram_api_base, task),
        stop=stop,
        poll_seconds=settings.worker_poll_seconds,
    )
    try:
        loop.run()
    finally:
        bot.stop()
        client.close()


if __name__ == "__main__":
    main()
