"""Async database engine and session management."""

from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.config import get_settings


class Base(DeclarativeBase):
    pass


settings = get_settings()

# Support sqlite for local/test without PostgreSQL
_url = settings.database_url
_connect_args = {}
if _url.startswith("sqlite"):
    _connect_args = {"check_same_thread": False}

engine = create_async_engine(
    _url,
    echo=settings.debug,
    pool_pre_ping=True,
    connect_args=_connect_args,
)

AsyncSessionLocal = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def init_db() -> None:
    from app import models  # noqa: F401

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(_ensure_short_run_column)


def _ensure_short_run_column(sync_conn) -> None:
    if sync_conn.dialect.name != "sqlite":
        return
    rows = sync_conn.exec_driver_sql("PRAGMA table_info(user_strategy_settings)").fetchall()
    names = {row[1] for row in rows}
    if names and "short_run_json" not in names:
        sync_conn.exec_driver_sql("ALTER TABLE user_strategy_settings ADD COLUMN short_run_json TEXT DEFAULT '{}'")
