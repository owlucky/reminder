import html
import logging

import httpx

from ..config import get_settings
from .base import Message, NotificationChannel

log = logging.getLogger(__name__)


class TelegramChannel(NotificationChannel):
    name = "telegram"

    def send(self, address: str, message: Message) -> None:
        self.validate_address(address)

        settings = get_settings()
        token = settings.telegram_bot_token
        if not token:
            raise RuntimeError(
                "TELEGRAM_BOT_TOKEN не задан — канал telegram не настроен"
            )

        text = (
            f"<b>{html.escape(message.title)}</b>\n\n"
            f"{html.escape(message.body)}"
        )
        url = f"{settings.telegram_api_base}/bot{token}/sendMessage"

        resp = httpx.post(
            url,
            json={
                "chat_id": address,
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            },
            timeout=15,
        )
        if resp.status_code != 200:
            raise RuntimeError(
                f"Telegram API вернул {resp.status_code}: {resp.text}"
            )
