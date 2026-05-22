from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings

engine = create_async_engine(settings.database_url, echo=False, pool_pre_ping=True)

_session_factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


@asynccontextmanager
async def get_session() -> AsyncSession:
    async with _session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def init_db() -> None:
    """Warm up the connection pool."""
    await db_healthcheck()


async def db_healthcheck() -> bool:
    try:
        async with get_session() as session:
            await session.execute(__import__("sqlalchemy").text("SELECT 1"))
        return True
    except Exception:
        return False
