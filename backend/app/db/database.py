"""
db/database.py
---------------
SQLAlchemy engine + session factory.

Reads DATABASE_URL from settings (backend.app.core.config).
On Railway: add a Postgres plugin to your project — Railway auto-injects
DATABASE_URL as an environment variable, which pydantic-settings will
pick up automatically (no manual .env edit needed on Railway).

Locally: set DATABASE_URL in your .env, e.g.:
    DATABASE_URL=postgresql://postgres:password@localhost:5432/smartfill

If DATABASE_URL is not set at all, falls back to a local SQLite file
(smartfill.db in the backend folder) purely for convenience during
development — production should always use Postgres.
"""

from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from backend.app.core.config import get_settings
from backend.app.core.logging import get_logger

logger = get_logger(__name__)

settings = get_settings()

# Railway's DATABASE_URL sometimes starts with "postgres://" (old Heroku-style
# scheme) but SQLAlchemy 2.x + psycopg2 requires "postgresql://".
_db_url = settings.database_url
if _db_url.startswith("postgres://"):
    _db_url = _db_url.replace("postgres://", "postgresql://", 1)

_connect_args = {"check_same_thread": False} if _db_url.startswith("sqlite") else {}

engine = create_engine(_db_url, connect_args=_connect_args, pool_pre_ping=True)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    pass


def get_db():
    """FastAPI dependency — yields a DB session, always closes it after."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    """Create all tables if they don't exist. Called once at startup."""
    from backend.app.db import models  # noqa: F401 — ensures models are registered
    Base.metadata.create_all(bind=engine)
    logger.info("Database tables ready | url=%s", _db_url.split("@")[-1] if "@" in _db_url else _db_url)
