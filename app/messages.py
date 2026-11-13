from dataclasses import dataclass
from datetime import datetime

from .recurrence import humanize_duration
from .utils import format_local


@dataclass
class Message:
    title: str
    body: str


def build_message(
    title: str,
    description: str | None,
    occurrence: datetime,
    offset: int,
    tz_name: str | None = None,
) -> Message:
    when = format_local(occurrence, tz_name)
    if offset <= 0:
        lead = "Наступает событие"
    else:
        lead = f"Напоминание ({humanize_duration(offset)} до события)"

    lines = [lead, f"Когда: {when}"]
    if description:
        lines.append("")
        lines.append(description)
    return Message(title=title, body="\n".join(lines))
