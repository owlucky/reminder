import os
from collections.abc import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from .config import get_settings

settings = get_settings()

_connect_args = (
    {"check_same_thread": False}
    if settings.database_url.startswith("sqlite")
    else {}
)

engine = create_engine(
    settings.database_url,
    connect_args=_connect_args,
    future=True,
)

SessionLocal = sessionmaker(
    bind=engine,
    autoflush=False,
    autocommit=False,
    expire_on_commit=False,
)


class Base(DeclarativeBase):
    pass


def _ensure_sqlite_dir() -> None:
    url = settings.database_url
    prefix = "sqlite:///"
    if url.startswith(prefix):
        path = url[len(prefix):]
        directory = os.path.dirname(path)
        if directory:
            os.makedirs(directory, exist_ok=True)


def _migrate() -> None:

    from sqlalchemy import inspect, text

    inspector = inspect(engine)
    if "recipients" in inspector.get_table_names():
        columns = {c["name"] for c in inspector.get_columns("recipients")}
        if "timezone" not in columns:
            with engine.begin() as conn:
                conn.execute(
                    text("ALTER TABLE recipients ADD COLUMN timezone VARCHAR(64)")
                )


def init_db() -> None:

    _ensure_sqlite_dir()
    from . import models

    Base.metadata.create_all(engine)
    _migrate()


def get_db() -> Iterator[Session]:

    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
