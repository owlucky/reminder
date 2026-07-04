import logging
import threading
from datetime import datetime, timedelta

import httpx
from sqlalchemy import select

from .config import get_settings
from .database import SessionLocal
from .dispatcher import build_message
from .models import Recipient, Recurrence, Reminder
from .recurrence import (
    has_future_occurrence,
    humanize_duration,
    occurrences_in_range,
    parse_duration,
)
from .utils import (
    format_local,
    is_valid_timezone,
    local_to_utc,
    to_naive_utc,
    utcnow,
)

log = logging.getLogger(__name__)

TIMEZONE_CHOICES: list[tuple[str, str]] = [
    ("UTC-8 Лос-Анджелес", "America/Los_Angeles"),
    ("UTC-7 Денвер", "America/Denver"),
    ("UTC-6 Чикаго", "America/Chicago"),
    ("UTC-5 Нью-Йорк", "America/New_York"),
    ("UTC-3 Сан-Паулу", "America/Sao_Paulo"),
    ("UTC-1 Азоры", "Atlantic/Azores"),
    ("UTC±0 Лондон", "Europe/London"),
    ("UTC±0 (UTC)", "UTC"),
    ("UTC+1 Берлин", "Europe/Berlin"),
    ("UTC+2 Калининград", "Europe/Kaliningrad"),
    ("UTC+3 Москва", "Europe/Moscow"),
    ("UTC+4 Самара", "Europe/Samara"),
    ("UTC+5 Екатеринбург", "Asia/Yekaterinburg"),
    ("UTC+6 Омск", "Asia/Omsk"),
    ("UTC+7 Красноярск", "Asia/Krasnoyarsk"),
    ("UTC+8 Иркутск", "Asia/Irkutsk"),
    ("UTC+9 Якутск", "Asia/Yakutsk"),
    ("UTC+10 Владивосток", "Asia/Vladivostok"),
    ("UTC+11 Магадан", "Asia/Magadan"),
    ("UTC+12 Камчатка", "Asia/Kamchatka"),
]

RECURRENCE_CHOICES: list[tuple[str, str]] = [
    ("Разово", "once"),
    ("Ежедневно", "daily"),
    ("Еженедельно", "weekly"),
    ("Ежемесячно", "monthly"),
    ("Ежегодно (ДР)", "yearly"),
]

RECURRENCE_LABELS = {v: label for label, v in RECURRENCE_CHOICES}

OFFSET_CHOICES: list[tuple[str, str]] = [
    ("🔔 В момент события", "0"),
    ("За 1 час", "1h"),
    ("За 1 день", "1d"),
    ("За неделю", "1w"),
    ("Неделя + день + в момент", "1w,1d,0"),
    ("✏️ Свой вариант (ввести текстом)", "custom"),
]

SNOOZE_CHOICES: list[tuple[str, str]] = [
    ("1 час", "1h"),
    ("1 день", "1d"),
    ("3 дня", "3d"),
    ("1 неделя", "1w"),
    ("2 недели", "2w"),
    ("1 месяц (30 дней)", "30d"),
    ("✏️ Свой вариант (ввести текстом)", "custom"),
]

_RECURRENCE_ALIASES: dict[str, Recurrence] = {
    "разово": Recurrence.once,
    "once": Recurrence.once,
    "ежедневно": Recurrence.daily,
    "daily": Recurrence.daily,
    "еженедельно": Recurrence.weekly,
    "weekly": Recurrence.weekly,
    "ежемесячно": Recurrence.monthly,
    "monthly": Recurrence.monthly,
    "ежегодно": Recurrence.yearly,
    "yearly": Recurrence.yearly,
    "др": Recurrence.yearly,
}

_WHEN_FORMATS = (
    "%d.%m.%Y %H:%M",
    "%d.%m.%Y",
    "%Y-%m-%d %H:%M",
    "%Y-%m-%d",
    "%d.%m.%Y %H:%M:%S",
)

_HELP = (
    "Я бот-напоминалка. Управление — кнопками.\n\n"
    "/add — создать напоминание\n"
    "/list — мои напоминания (с кнопками управления)\n"
    "/timezone — изменить часовой пояс\n"
    "/cancel — отменить текущий ввод\n"
    "/help — эта справка"
)


def _parse_when(text: str) -> datetime:

    text = text.strip()
    for fmt in _WHEN_FORMATS:
        try:
            return to_naive_utc(datetime.strptime(text, fmt))
        except ValueError:
            continue
    try:
        return to_naive_utc(datetime.fromisoformat(text.replace("Z", "+00:00")))
    except ValueError as exc:
        raise ValueError(
            "Не понял дату. Примеры: 15.07.2026 18:00, 15.07.2026, "
            "2026-07-15 18:00"
        ) from exc


def _parse_when_local(text: str, tz_name: str | None) -> datetime:

    text = text.strip()
    for fmt in _WHEN_FORMATS:
        try:
            naive = datetime.strptime(text, fmt)
            return local_to_utc(naive, tz_name)
        except ValueError:
            continue
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(
            "Не понял дату. Примеры: 15.07.2026 18:00, 15.07.2026, "
            "2026-07-15 18:00"
        ) from exc
    if dt.tzinfo is not None:
        return to_naive_utc(dt)
    return local_to_utc(dt, tz_name)


def _parse_offsets(text: str) -> list[int]:
    text = text.strip()
    if text.lower() in ("нет", "-", "в момент"):
        tokens = ["0"]
    else:
        tokens = [t for t in text.replace(",", " ").split() if t]
    if not tokens:
        tokens = ["0"]
    return sorted({parse_duration(t) for t in tokens}, reverse=True)


def _inline(rows: list[list[tuple[str, str]]]) -> dict:
    return {
        "inline_keyboard": [
            [{"text": text, "callback_data": data} for text, data in row]
            for row in rows
        ]
    }


def _tz_keyboard() -> dict:
    rows: list[list[tuple[str, str]]] = []
    row: list[tuple[str, str]] = []
    for label, zone in TIMEZONE_CHOICES:
        row.append((label, f"tz:{zone}"))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    return _inline(rows)


def _recurrence_keyboard(cb, include_once: bool = True) -> dict:

    choices = [
        (label, value)
        for label, value in RECURRENCE_CHOICES
        if include_once or value != "once"
    ]
    rows: list[list[tuple[str, str]]] = []
    row: list[tuple[str, str]] = []
    for label, value in choices:
        row.append((label, cb(value)))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    return _inline(rows)


def _offset_keyboard(cb) -> dict:

    return _inline([[(label, cb(value))] for label, value in OFFSET_CHOICES])


def _edit_menu_keyboard(rid: int) -> dict:
    return _inline(
        [
            [("🔔 Когда напоминать", f"edit:off:{rid}")],
            [("🔁 Повтор", f"edit:rec:{rid}")],
            [("📅 Дата и время", f"edit:when:{rid}")],
            [("✏️ Заголовок", f"edit:title:{rid}")],
            [("⬅️ Назад", f"edit:back:{rid}")],
        ]
    )


def _snooze_keyboard(rid: int) -> dict:
    rows: list[list[tuple[str, str]]] = [
        [(label, f"snz:{rid}:{value}")] for label, value in SNOOZE_CHOICES
    ]
    rows.append([("⬅️ Назад", f"edit:back:{rid}")])
    return _inline(rows)


class TelegramBot:
    def __init__(self, token: str, api_base: str) -> None:
        self._token = token
        self._base = f"{api_base}/bot{token}"
        self._client = httpx.Client(timeout=40)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._conv: dict[int, dict] = {}

    def start(self) -> None:
        self._thread = threading.Thread(
            target=self._run, name="telegram-bot", daemon=True
        )
        self._thread.start()
        log.info("Telegram-бот запущен (long polling)")

    def stop(self) -> None:
        self._stop.set()
        try:
            self._client.close()
        except Exception:
            pass

    def _set_my_commands(self) -> None:

        commands = [
            {"command": "add", "description": "Создать напоминание"},
            {"command": "list", "description": "Мои напоминания"},
            {"command": "timezone", "description": "Часовой пояс"},
            {"command": "help", "description": "Справка"},
            {"command": "cancel", "description": "Отменить текущий ввод"},
        ]
        try:
            self._client.post(
                f"{self._base}/setMyCommands", json={"commands": commands}
            )
        except httpx.HTTPError as exc:
            log.warning("Не удалось задать команды бота: %s", exc)

    def _run(self) -> None:
        self._set_my_commands()
        offset: int | None = None
        while not self._stop.is_set():
            try:
                resp = self._client.post(
                    f"{self._base}/getUpdates",
                    json={
                        "timeout": 25,
                        "offset": offset,
                        "allowed_updates": ["message", "callback_query"],
                    },
                )
                data = resp.json()
                if not data.get("ok"):
                    log.warning("getUpdates вернул ошибку: %s", data)
                    self._stop.wait(3)
                    continue
                for update in data.get("result", []):
                    offset = update["update_id"] + 1
                    try:
                        self._handle_update(update)
                    except Exception:
                        log.exception("Ошибка обработки апдейта")
            except httpx.HTTPError as exc:
                if not self._stop.is_set():
                    log.warning("Сетевая ошибка long polling: %s", exc)
                    self._stop.wait(3)
            except Exception:
                log.exception("Непредвиденная ошибка в цикле бота")
                self._stop.wait(3)

    def _send(self, chat_id: int, text: str, keyboard: dict | None = None) -> None:
        payload: dict = {"chat_id": chat_id, "text": text}
        if keyboard:
            payload["reply_markup"] = keyboard
        try:
            self._client.post(f"{self._base}/sendMessage", json=payload)
        except httpx.HTTPError as exc:
            log.warning("Не удалось отправить сообщение в чат %s: %s", chat_id, exc)

    def _edit(
        self,
        chat_id: int,
        message_id: int,
        text: str,
        keyboard: dict | None = None,
    ) -> None:
        payload: dict = {
            "chat_id": chat_id,
            "message_id": message_id,
            "text": text,
        }
        if keyboard:
            payload["reply_markup"] = keyboard
        try:
            self._client.post(f"{self._base}/editMessageText", json=payload)
        except httpx.HTTPError as exc:
            log.warning("Не удалось изменить сообщение %s: %s", message_id, exc)

    def _answer_callback(self, callback_id: str, text: str | None = None) -> None:
        payload: dict = {"callback_query_id": callback_id}
        if text:
            payload["text"] = text
        try:
            self._client.post(f"{self._base}/answerCallbackQuery", json=payload)
        except httpx.HTTPError:
            pass

    def _handle_update(self, update: dict) -> None:
        if "callback_query" in update:
            self._handle_callback(update["callback_query"])
            return

        message = update.get("message")
        if not message or "text" not in message:
            return
        chat = message["chat"]
        chat_id = chat["id"]
        text = message["text"].strip()

        if text.startswith("/"):
            self._handle_command(chat_id, chat, text)
        elif chat_id in self._conv:
            self._handle_text_step(chat_id, chat, text)
        else:
            self._send(chat_id, "Не понял. Наберите /help для списка команд.")

    def _handle_command(self, chat_id: int, chat: dict, text: str) -> None:
        command = text.split()[0].split("@", 1)[0].lower()

        if command in ("/start", "/help"):
            self._cmd_start(chat_id, chat, command == "/start")
        elif command == "/timezone":
            self._send(chat_id, "Выберите часовой пояс:", _tz_keyboard())
        elif command == "/add":
            self._cmd_add_start(chat_id, chat)
        elif command == "/list":
            self._cmd_list(chat_id)
        elif command == "/cancel":
            self._conv.pop(chat_id, None)
            self._send(chat_id, "Отменено.")
        else:
            self._send(chat_id, "Неизвестная команда. /help — список команд.")

    def _chat_name(self, chat: dict) -> str:
        return (
            chat.get("title")
            or chat.get("first_name")
            or chat.get("username")
            or f"chat {chat['id']}"
        )

    def _get_recipient(self, db, chat_id: int) -> Recipient | None:
        return db.execute(
            select(Recipient).where(
                Recipient.channel == "telegram",
                Recipient.address == str(chat_id),
            )
        ).scalar_one_or_none()

    def _get_or_create_recipient(self, db, chat_id: int, chat: dict) -> Recipient:
        recipient = self._get_recipient(db, chat_id)
        if recipient is None:
            recipient = Recipient(
                name=self._chat_name(chat),
                channel="telegram",
                address=str(chat_id),
            )
            db.add(recipient)
            db.commit()
            db.refresh(recipient)
        return recipient

    def _chat_timezone(self, chat_id: int) -> str | None:
        with SessionLocal() as db:
            recipient = self._get_recipient(db, chat_id)
            return recipient.timezone if recipient else None

    def _cmd_start(self, chat_id: int, chat: dict, greet: bool) -> None:
        with SessionLocal() as db:
            recipient = self._get_or_create_recipient(db, chat_id, chat)
            tz = recipient.timezone
        if greet:
            self._send(chat_id, "Привет! " + _HELP)
        if not tz:
            self._send(
                chat_id,
                "Для начала выберите ваш часовой пояс — чтобы время "
                "напоминаний совпадало с вашим:",
                _tz_keyboard(),
            )
        elif not greet:
            self._send(chat_id, _HELP)

    def _cmd_add_start(self, chat_id: int, chat: dict) -> None:
        with SessionLocal() as db:
            recipient = self._get_or_create_recipient(db, chat_id, chat)
            tz = recipient.timezone

        if not tz:
            self._conv[chat_id] = {"step": "await_tz", "data": {}}
            self._send(
                chat_id,
                "Сначала выберите часовой пояс (нужно один раз):",
                _tz_keyboard(),
            )
            return

        self._conv[chat_id] = {"step": "title", "data": {"tz": tz}}
        self._send(
            chat_id,
            "Создаём напоминание. Введите заголовок "
            "(например: «День рождения Ивана» или «Дедлайн отчёта»).\n"
            "Отмена — /cancel.",
        )

    def _cmd_list(self, chat_id: int) -> None:
        now = utcnow()
        with SessionLocal() as db:
            recipient = self._get_recipient(db, chat_id)
            if recipient is None or not recipient.reminders:
                self._send(chat_id, "Список пуст. Создайте первое: /add")
                return
            tz = recipient.timezone
            reminders = sorted(recipient.reminders, key=lambda x: x.id)
            self._send(chat_id, f"Ваших напоминаний: {len(reminders)}")
            for number, reminder in enumerate(reminders, start=1):
                text, keyboard = self._render_reminder(reminder, tz, now, number)
                self._send(chat_id, text, keyboard)

    def _display_number(self, recipient: Recipient, reminder_id: int) -> int:

        ordered = sorted(recipient.reminders, key=lambda x: x.id)
        for index, reminder in enumerate(ordered, start=1):
            if reminder.id == reminder_id:
                return index
        return 0

    def _owned_reminder(self, db, chat_id: int, reminder_id: int):

        recipient = self._get_recipient(db, chat_id)
        reminder = db.get(Reminder, reminder_id)
        if (
            reminder is None
            or recipient is None
            or recipient not in reminder.recipients
        ):
            return None, None
        return recipient, reminder

    def _reminder_text(
        self, reminder: Reminder, tz: str | None, now: datetime, number: int
    ) -> str:
        if not reminder.enabled:
            state = "🚫 выключено"
        elif reminder.snooze_until and reminder.snooze_until > now:
            state = f"⏸ пауза до {format_local(reminder.snooze_until, tz)}"
        else:
            state = "✅ активно"

        offs = ", ".join(
            humanize_duration(s) for s in (reminder.offsets_seconds or [0])
        )
        return (
            f"№{number} — {reminder.title}\n"
            f"Событие: {format_local(reminder.event_time, tz)}\n"
            f"Повтор: {RECURRENCE_LABELS.get(reminder.recurrence.value, reminder.recurrence.value)}\n"
            f"Напоминать: {offs}\n"
            f"Статус: {state}"
        )

    def _reminder_keyboard(self, reminder: Reminder, now: datetime) -> dict:
        rid = reminder.id
        rows: list[list[tuple[str, str]]] = [
            [("📨 Тест", f"act:test:{rid}"), ("✏️ Изменить", f"edit:menu:{rid}")]
        ]
        if not reminder.enabled:
            rows.append([("✅ Включить", f"act:enable:{rid}")])
        elif reminder.snooze_until and reminder.snooze_until > now:
            rows.append(
                [
                    ("▶️ Снять паузу", f"act:enable:{rid}"),
                    ("🚫 Выключить", f"act:disable:{rid}"),
                ]
            )
        else:
            rows.append(
                [
                    ("⏸ Пауза", f"act:snooze:{rid}"),
                    ("🚫 Выключить", f"act:disable:{rid}"),
                ]
            )
        rows.append([("🗑 Удалить", f"act:del:{rid}")])
        return _inline(rows)

    def _render_reminder(
        self, reminder: Reminder, tz: str | None, now: datetime, number: int
    ):
        return (
            self._reminder_text(reminder, tz, now, number),
            self._reminder_keyboard(reminder, now),
        )

    def _handle_callback(self, cq: dict) -> None:
        data = cq.get("data", "")
        message = cq.get("message") or {}
        chat = message.get("chat") or {}
        chat_id = chat.get("id")
        message_id = message.get("message_id")
        self._answer_callback(cq["id"])
        if chat_id is None:
            return

        if data.startswith("tz:"):
            self._cb_set_timezone(chat_id, chat, message_id, data[3:])
        elif data.startswith("rec:"):
            self._cb_recurrence(chat_id, message_id, data[4:])
        elif data.startswith("off:"):
            self._cb_offsets(chat_id, chat, message_id, data[4:])
        elif data.startswith("act:"):
            self._cb_action(chat_id, message_id, data[4:])
        elif data.startswith("edit:"):
            self._cb_edit_menu(chat_id, message_id, data[5:])
        elif data.startswith("eoff:"):
            self._cb_edit_offsets(chat_id, message_id, data[5:])
        elif data.startswith("erec:"):
            self._cb_edit_recurrence(chat_id, message_id, data[5:])
        elif data.startswith("snz:"):
            self._cb_snooze(chat_id, message_id, data[4:])

    def _cb_set_timezone(
        self, chat_id: int, chat: dict, message_id: int, zone: str
    ) -> None:
        if not is_valid_timezone(zone):
            self._send(chat_id, "Неизвестный пояс, попробуйте снова: /timezone")
            return
        with SessionLocal() as db:
            recipient = self._get_or_create_recipient(db, chat_id, chat)
            recipient.timezone = zone
            db.commit()

        self._edit(chat_id, message_id, f"Часовой пояс установлен: {zone} ✅")

        conv = self._conv.get(chat_id)
        if conv and conv["step"] == "await_tz":
            conv["step"] = "title"
            conv["data"]["tz"] = zone
            self._send(
                chat_id,
                "Отлично! Теперь введите заголовок напоминания "
                "(например: «День рождения Ивана»).",
            )

    def _cb_recurrence(self, chat_id: int, message_id: int, value: str) -> None:
        conv = self._conv.get(chat_id)
        if not conv or conv["step"] != "recurrence":
            self._send(chat_id, "Сессия истекла. Начните заново: /add")
            return
        try:
            rule = Recurrence(value)
        except ValueError:
            self._send(chat_id, "Не понял вариант. Начните заново: /add")
            return

        event_time = conv["data"].get("event_time")
        if event_time is not None and not has_future_occurrence(
            event_time, rule, 1, utcnow()
        ):
            self._edit(
                chat_id,
                message_id,
                "⛔ Разовое напоминание нельзя поставить в прошлое.",
            )
            conv["step"] = "when"
            self._send(
                chat_id,
                "Введите будущие дату и время "
                "(формат ДД.ММ.ГГГГ ЧЧ:ММ), либо выберите повторяющийся тип.",
            )
            return

        conv["data"]["recurrence"] = rule
        conv["step"] = "offsets"
        self._edit(
            chat_id, message_id, f"Повтор: {RECURRENCE_LABELS.get(value, value)} ✅"
        )
        self._send(
            chat_id,
            "За сколько времени до события напоминать?",
            _offset_keyboard(lambda v: f"off:{v}"),
        )

    def _cb_offsets(
        self, chat_id: int, chat: dict, message_id: int, value: str
    ) -> None:
        conv = self._conv.get(chat_id)
        if not conv or conv["step"] != "offsets":
            self._send(chat_id, "Сессия истекла. Начните заново: /add")
            return
        if value == "custom":
            conv["step"] = "offsets_custom"
            self._edit(chat_id, message_id, "Свой вариант ✍️")
            self._send(
                chat_id,
                "Введите смещения текстом через пробел. Единицы: m, h, d, w. "
                "0 — в момент события.\nНапример: 1w 1d 0",
            )
            return
        try:
            offsets = _parse_offsets(value)
        except ValueError:
            self._send(chat_id, "Не понял вариант. Начните заново: /add")
            return
        self._edit(chat_id, message_id, "Когда напоминать — выбрано ✅")
        conv["data"]["offsets_seconds"] = offsets
        self._finish_add(chat_id, chat, conv["data"])
        self._conv.pop(chat_id, None)

    def _cb_action(self, chat_id: int, message_id: int, payload: str) -> None:
        parts = payload.split(":")
        if len(parts) != 2 or not parts[1].isdigit():
            return
        action, rid_str = parts
        rid = int(rid_str)
        now = utcnow()

        with SessionLocal() as db:
            recipient, reminder = self._owned_reminder(db, chat_id, rid)
            if reminder is None:
                self._edit(chat_id, message_id, "Напоминание не найдено.")
                return
            tz = recipient.timezone
            number = self._display_number(recipient, rid)

            if action == "test":
                message = build_message(reminder, now, 0, tz)
                self._send(chat_id, f"{message.title}\n\n{message.body}")
                return
            if action == "del":
                self._edit(
                    chat_id,
                    message_id,
                    f"Удалить напоминание №{number} «{reminder.title}»?",
                    _inline(
                        [
                            [
                                ("❌ Да, удалить", f"act:delyes:{rid}"),
                                ("Отмена", f"act:delno:{rid}"),
                            ]
                        ]
                    ),
                )
                return
            if action == "delyes":
                db.delete(reminder)
                db.commit()
                self._edit(chat_id, message_id, f"Напоминание №{number} удалено. 🗑")
                return
            if action == "snooze":
                self._edit(
                    chat_id,
                    message_id,
                    "На сколько поставить паузу?",
                    _snooze_keyboard(rid),
                )
                return
            if action == "disable":
                reminder.enabled = False
            elif action == "enable":
                reminder.enabled = True
                reminder.snooze_until = None
            elif action != "delno":
                return
            db.commit()
            db.refresh(reminder)
            text, keyboard = self._render_reminder(reminder, tz, now, number)

        self._edit(chat_id, message_id, text, keyboard)

    def _cb_edit_menu(self, chat_id: int, message_id: int, payload: str) -> None:
        parts = payload.split(":")
        if len(parts) != 2 or not parts[1].isdigit():
            return
        sub, rid = parts[0], int(parts[1])
        now = utcnow()

        with SessionLocal() as db:
            recipient, reminder = self._owned_reminder(db, chat_id, rid)
            if reminder is None:
                self._edit(chat_id, message_id, "Напоминание не найдено.")
                return
            tz = recipient.timezone
            number = self._display_number(recipient, rid)
            title = reminder.title
            event_time = reminder.event_time

        if sub == "menu":
            self._edit(
                chat_id,
                message_id,
                f"Изменение напоминания №{number} «{title}».\nЧто меняем?",
                _edit_menu_keyboard(rid),
            )
        elif sub == "back":
            with SessionLocal() as db:
                recipient, reminder = self._owned_reminder(db, chat_id, rid)
                text, keyboard = self._render_reminder(reminder, tz, now, number)
            self._edit(chat_id, message_id, text, keyboard)
        elif sub == "off":
            self._edit(
                chat_id,
                message_id,
                "За сколько времени до события напоминать?",
                _offset_keyboard(lambda v: f"eoff:{rid}:{v}"),
            )
        elif sub == "rec":
            allow_once = has_future_occurrence(
                event_time, Recurrence.once, 1, utcnow()
            )
            prompt = "Как часто повторять?"
            if not allow_once:
                prompt = (
                    "Событие в прошлом — «Разово» недоступно. "
                    "Выберите повторяющийся вариант:"
                )
            self._edit(
                chat_id,
                message_id,
                prompt,
                _recurrence_keyboard(
                    lambda v: f"erec:{rid}:{v}", include_once=allow_once
                ),
            )
        elif sub == "when":
            self._conv[chat_id] = {
                "step": "edit_when",
                "data": {"edit_id": rid, "tz": tz},
            }
            hint = "по вашему поясу" if tz else "в UTC"
            self._edit(
                chat_id,
                message_id,
                f"Введите новую дату и время ({hint}), "
                "например 15.07.2026 18:00.\nОтмена — /cancel.",
            )
        elif sub == "title":
            self._conv[chat_id] = {"step": "edit_title", "data": {"edit_id": rid}}
            self._edit(
                chat_id,
                message_id,
                "Введите новый заголовок.\nОтмена — /cancel.",
            )

    def _cb_edit_offsets(self, chat_id: int, message_id: int, payload: str) -> None:
        rid_str, _, value = payload.partition(":")
        if not rid_str.isdigit() or not value:
            return
        rid = int(rid_str)

        if value == "custom":
            self._conv[chat_id] = {
                "step": "edit_offsets_custom",
                "data": {"edit_id": rid},
            }
            self._edit(
                chat_id,
                message_id,
                "Введите смещения текстом через пробел (например 1w 1d 0). "
                "Единицы: m, h, d, w. 0 — в момент события.\nОтмена — /cancel.",
            )
            return
        try:
            offsets = _parse_offsets(value)
        except ValueError:
            self._send(chat_id, "Не понял вариант.")
            return
        self._apply_edit(chat_id, message_id, rid, offsets_seconds=offsets)

    def _cb_edit_recurrence(self, chat_id: int, message_id: int, payload: str) -> None:
        rid_str, _, value = payload.partition(":")
        if not rid_str.isdigit() or not value:
            return
        try:
            rule = Recurrence(value)
        except ValueError:
            return
        rid = int(rid_str)

        with SessionLocal() as db:
            _, reminder = self._owned_reminder(db, chat_id, rid)
            event_time = reminder.event_time if reminder else None
        if event_time is not None and not has_future_occurrence(
            event_time, rule, 1, utcnow()
        ):
            self._send(
                chat_id,
                "⛔ Событие в прошлом — сделать напоминание разовым нельзя. "
                "Сначала измените дату (📅 Дата и время).",
            )
            return
        self._apply_edit(chat_id, message_id, rid, recurrence=rule)

    def _cb_snooze(self, chat_id: int, message_id: int, payload: str) -> None:
        rid_str, _, value = payload.partition(":")
        if not rid_str.isdigit() or not value:
            return
        rid = int(rid_str)

        if value == "custom":
            self._conv[chat_id] = {"step": "snooze_custom", "data": {"edit_id": rid}}
            self._edit(
                chat_id,
                message_id,
                "Введите срок паузы (например 2d, 1w, 12h, 30m). "
                "Единицы: m, h, d, w.\nОтмена — /cancel.",
            )
            return
        try:
            seconds = parse_duration(value)
        except ValueError:
            self._send(chat_id, "Не понял срок.")
            return
        self._apply_edit(
            chat_id, message_id, rid, snooze_until=utcnow() + timedelta(seconds=seconds)
        )

    def _apply_edit(
        self,
        chat_id: int,
        message_id: int | None,
        rid: int,
        *,
        offsets_seconds: list[int] | None = None,
        recurrence: Recurrence | None = None,
        event_time: datetime | None = None,
        title: str | None = None,
        snooze_until: datetime | None = None,
    ) -> None:

        now = utcnow()
        with SessionLocal() as db:
            recipient, reminder = self._owned_reminder(db, chat_id, rid)
            if reminder is None:
                if message_id is not None:
                    self._edit(chat_id, message_id, "Напоминание не найдено.")
                return
            if offsets_seconds is not None:
                reminder.offsets_seconds = offsets_seconds
            if recurrence is not None:
                reminder.recurrence = recurrence
            if event_time is not None:
                reminder.event_time = event_time
            if title is not None:
                reminder.title = title
            if snooze_until is not None:
                reminder.snooze_until = snooze_until
            db.commit()
            db.refresh(reminder)
            tz = recipient.timezone
            number = self._display_number(recipient, rid)
            text, keyboard = self._render_reminder(reminder, tz, now, number)

        if message_id is not None:
            self._edit(chat_id, message_id, text, keyboard)
        else:
            self._send(chat_id, "Изменено ✅")
            self._send(chat_id, text, keyboard)

    def _handle_text_step(self, chat_id: int, chat: dict, text: str) -> None:
        conv = self._conv[chat_id]
        step = conv["step"]
        data = conv["data"]

        if step == "await_tz":
            self._send(chat_id, "Пожалуйста, выберите пояс кнопкой выше.")
        elif step == "title":
            data["title"] = text
            conv["step"] = "when"
            tz = data.get("tz")
            hint = "по вашему поясу" if tz else "в UTC"
            self._send(
                chat_id,
                f"Когда событие? Введите дату и время ({hint}).\n"
                "Формат: ДД.ММ.ГГГГ ЧЧ:ММ — например 15.07.2026 18:00",
            )
        elif step == "when":
            try:
                data["event_time"] = _parse_when_local(text, data.get("tz"))
            except ValueError as exc:
                self._send(chat_id, str(exc))
                return
            conv["step"] = "recurrence"
            allow_once = has_future_occurrence(
                data["event_time"], Recurrence.once, 1, utcnow()
            )
            prompt = "Как часто повторять?"
            if not allow_once:
                prompt = (
                    "Указанная дата в прошлом, поэтому «Разово» недоступно — "
                    "выберите повторяющийся вариант:"
                )
            self._send(
                chat_id,
                prompt,
                _recurrence_keyboard(lambda v: f"rec:{v}", include_once=allow_once),
            )
        elif step == "offsets_custom":
            try:
                data["offsets_seconds"] = _parse_offsets(text)
            except ValueError as exc:
                self._send(chat_id, f"Не понял смещения: {exc}")
                return
            self._finish_add(chat_id, chat, data)
            self._conv.pop(chat_id, None)
        elif step == "edit_when":
            try:
                new_dt = _parse_when_local(text, data.get("tz"))
            except ValueError as exc:
                self._send(chat_id, str(exc))
                return
            with SessionLocal() as db:
                _, reminder = self._owned_reminder(db, chat_id, data["edit_id"])
                rule = reminder.recurrence if reminder else Recurrence.once
            if not has_future_occurrence(new_dt, rule, 1, utcnow()):
                self._send(
                    chat_id,
                    "⛔ Дата в прошлом. Для разового напоминания введите "
                    "будущие дату и время.",
                )
                return
            self._conv.pop(chat_id, None)
            self._apply_edit(chat_id, None, data["edit_id"], event_time=new_dt)
        elif step == "edit_title":
            self._conv.pop(chat_id, None)
            self._apply_edit(chat_id, None, data["edit_id"], title=text)
        elif step == "snooze_custom":
            try:
                seconds = parse_duration(text.replace(",", " ").split()[0])
            except (ValueError, IndexError) as exc:
                self._send(chat_id, f"Не понял срок: {exc}")
                return
            self._conv.pop(chat_id, None)
            self._apply_edit(
                chat_id,
                None,
                data["edit_id"],
                snooze_until=utcnow() + timedelta(seconds=seconds),
            )
        elif step == "edit_offsets_custom":
            try:
                offsets = _parse_offsets(text)
            except ValueError as exc:
                self._send(chat_id, f"Не понял смещения: {exc}")
                return
            self._conv.pop(chat_id, None)
            self._apply_edit(chat_id, None, data["edit_id"], offsets_seconds=offsets)

    def _finish_add(self, chat_id: int, chat: dict, data: dict) -> None:
        now = utcnow()
        if not has_future_occurrence(
            data["event_time"], data["recurrence"], 1, now
        ):
            self._send(
                chat_id,
                "⛔ Разовое напоминание в прошлом создать нельзя. "
                "Начните заново: /add",
            )
            return
        with SessionLocal() as db:
            recipient = self._get_or_create_recipient(db, chat_id, chat)
            tz = recipient.timezone
            reminder = Reminder(
                title=data["title"],
                event_time=data["event_time"],
                recurrence=data["recurrence"],
                interval=1,
                offsets_seconds=data["offsets_seconds"],
                recipients=[recipient],
            )
            db.add(reminder)
            db.commit()
            db.refresh(reminder)
            number = self._display_number(recipient, reminder.id)

            horizon = now + timedelta(days=400)
            occs = occurrences_in_range(
                reminder.event_time,
                reminder.recurrence,
                reminder.interval,
                now,
                horizon,
            )
            next_fires = sorted(
                {
                    occ - timedelta(seconds=s)
                    for occ in occs
                    for s in reminder.offsets_seconds
                    if now <= occ - timedelta(seconds=s) <= horizon
                }
            )
            offs = ", ".join(humanize_duration(s) for s in reminder.offsets_seconds)
            event_str = format_local(reminder.event_time, tz)

        text = (
            f"✅ Создано напоминание №{number}: «{reminder.title}»\n"
            f"Событие: {event_str}\n"
            f"Повтор: {RECURRENCE_LABELS.get(reminder.recurrence.value)}; "
            f"напоминать: {offs}.\n"
        )
        if next_fires:
            text += "Ближайшее срабатывание: " + format_local(next_fires[0], tz)
        else:
            text += "Ближайших срабатываний в течение года нет."
        self._send(chat_id, text)


_bot: TelegramBot | None = None


def start_bot() -> TelegramBot | None:

    global _bot
    settings = get_settings()
    if not settings.telegram_bot_token or not settings.telegram_bot_polling:
        log.info("Telegram-бот не запущен (нет токена или polling выключен)")
        return None
    if _bot is not None:
        return _bot
    _bot = TelegramBot(settings.telegram_bot_token, settings.telegram_api_base)
    _bot.start()
    return _bot


def stop_bot() -> None:
    global _bot
    if _bot is not None:
        _bot.stop()
        _bot = None
