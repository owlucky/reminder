import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from .config import get_settings
from .database import init_db
from .grpc_server import start_grpc, stop_grpc
from .routers import recipients, reminders, system
from .scheduler import start_scheduler, stop_scheduler


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    logging.basicConfig(
        level=settings.log_level.upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    init_db()
    start_grpc()
    start_scheduler()
    try:
        yield
    finally:
        stop_scheduler()
        stop_grpc()


app = FastAPI(
    title="Reminder Service",
    description=(
        "Лёгкий сервис-напоминалка: разовые и периодические напоминания, "
        "несколько напоминаний до события (за неделю/за день/...), "
        "получатели-люди и группы. Сервис формулирует задачи доставки, "
        "их забирают воркеры каналов (Telegram, консоль, дальше — Макс)."
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
