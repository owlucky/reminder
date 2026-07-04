import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from .channels import setup_channels
from .config import get_settings
from .database import init_db
from .routers import recipients, reminders, system
from .scheduler import start_scheduler, stop_scheduler
from .telegram_bot import start_bot, stop_bot


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    logging.basicConfig(
        level=settings.log_level.upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    setup_channels()
    init_db()
    start_scheduler()
    start_bot()
    try:
        yield
    finally:
        stop_bot()
        stop_scheduler()


app = FastAPI(
    title="Reminder Service",
    description=(
        "Лёгкий сервис-напоминалка: разовые и периодические напоминания, "
        "несколько напоминаний до события (за неделю/за день/...), "
        "получатели-люди и группы, доставка через подключаемые каналы "
        "(сейчас — Telegram). Полное и временное отключение."
    ),
    version="0.1.0",
    lifespan=lifespan,
)

app.include_router(system.router)
app.include_router(recipients.router)
app.include_router(reminders.router)


@app.get("/", include_in_schema=False)
def root():
    return {"service": "reminder-service", "docs": "/docs"}
