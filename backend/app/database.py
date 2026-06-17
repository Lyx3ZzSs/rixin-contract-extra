from collections.abc import AsyncGenerator
from pathlib import Path

from sqlalchemy import JSON, event
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.types import TypeDecorator

from app.config import settings


def _normalize_database_url(database_url: str) -> str:
    url = make_url(database_url)
    if url.get_backend_name() == "sqlite" and url.database and url.database not in (":memory:", ""):
        database_path = Path(url.database)
        if not database_path.is_absolute():
            backend_dir = Path(__file__).resolve().parent.parent
            database_path = backend_dir / database_path
        return url.set(database=str(database_path)).render_as_string(hide_password=False)
    return database_url


def _engine_kwargs(database_url: str) -> dict:
    url = make_url(database_url)
    kwargs: dict = {"echo": False}
    if url.get_backend_name() == "sqlite":
        if url.database and url.database not in (":memory:", ""):
            Path(url.database).parent.mkdir(parents=True, exist_ok=True)
        return kwargs
    kwargs.update({"pool_size": 20, "max_overflow": 10})
    return kwargs


database_url = _normalize_database_url(settings.database_url)
engine = create_async_engine(database_url, **_engine_kwargs(database_url))


@event.listens_for(engine.sync_engine, "connect")
def _configure_sqlite(dbapi_connection, _connection_record) -> None:
    if not engine.url.get_backend_name() == "sqlite":
        return
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA busy_timeout=5000")
    cursor.close()

async_session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


class JSONType(TypeDecorator):
    """SQLite-compatible JSON type."""
    impl = JSON
    cache_ok = True

    def load_dialect_impl(self, dialect):
        return dialect.type_descriptor(JSON())


class Base(DeclarativeBase):
    pass


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with async_session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
