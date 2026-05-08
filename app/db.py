from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy import text

from app.config import settings


engine = create_async_engine(settings.database_url, echo=False, pool_pre_ping=True)
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False)


async def init_db() -> None:
    async with engine.begin() as conn:
        await conn.execute(text("""
        CREATE TABLE IF NOT EXISTS raw_messages (
            id BIGSERIAL PRIMARY KEY,
            created_at TIMESTAMPTZ DEFAULT now(),
            telegram_user_id TEXT,
            message_type TEXT,
            original_text TEXT,
            transcript TEXT,
            intent TEXT,
            parsed_json JSONB,
            status TEXT DEFAULT 'received',
            error TEXT
        );
        """))

        await conn.execute(text("""
        CREATE TABLE IF NOT EXISTS ops_actions (
            id BIGSERIAL PRIMARY KEY,
            created_at TIMESTAMPTZ DEFAULT now(),
            requested_by TEXT,
            action_type TEXT,
            status TEXT,
            risk_level TEXT,
            plan_json JSONB,
            commands_json JSONB,
            approval_required BOOLEAN DEFAULT true,
            approved_at TIMESTAMPTZ,
            executed_at TIMESTAMPTZ,
            result_log TEXT,
            error_log TEXT
        );
        """))

        await conn.execute(text("""
        CREATE TABLE IF NOT EXISTS system_state (
            key TEXT PRIMARY KEY,
            value JSONB,
            updated_at TIMESTAMPTZ DEFAULT now()
        );
        """))


async def save_raw_message(
    telegram_user_id: str | None,
    message_type: str,
    original_text: str | None,
    transcript: str | None,
    intent: str | None,
    parsed_json: str | None,
    status: str,
    error: str | None = None,
) -> None:
    async with AsyncSessionLocal() as session:
        await session.execute(
            text("""
            INSERT INTO raw_messages
            (telegram_user_id, message_type, original_text, transcript, intent, parsed_json, status, error)
            VALUES
            (:telegram_user_id, :message_type, :original_text, :transcript, :intent, CAST(:parsed_json AS JSONB), :status, :error)
            """),
            {
                "telegram_user_id": telegram_user_id,
                "message_type": message_type,
                "original_text": original_text,
                "transcript": transcript,
                "intent": intent,
                "parsed_json": parsed_json,
                "status": status,
                "error": error,
            }
        )
        await session.commit()


async def db_healthcheck() -> bool:
    try:
        async with AsyncSessionLocal() as session:
            await session.execute(text("SELECT 1"))
        return True
    except Exception:
        return False
