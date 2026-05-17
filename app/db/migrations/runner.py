import logging
import os
import re

from sqlalchemy import text

from app.db.engine import get_session

logger = logging.getLogger(__name__)

MIGRATIONS_DIR = os.path.dirname(__file__)


async def run_migrations() -> None:
    async with get_session() as session:
        await session.execute(text("""
            CREATE TABLE IF NOT EXISTS schema_migrations (
                id SERIAL PRIMARY KEY,
                filename TEXT NOT NULL UNIQUE,
                applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
        """))

        result = await session.execute(text("SELECT filename FROM schema_migrations ORDER BY filename"))
        applied = {row[0] for row in result.fetchall()}

        sql_files = sorted(
            f for f in os.listdir(MIGRATIONS_DIR)
            if re.match(r"^\d{3}_.*\.sql$", f)
        )

        for filename in sql_files:
            if filename in applied:
                continue

            filepath = os.path.join(MIGRATIONS_DIR, filename)
            with open(filepath) as f:
                sql = f.read()

            logger.info(f"Applying migration: {filename}")
            await session.execute(text(sql))
            await session.execute(
                text("INSERT INTO schema_migrations (filename) VALUES (:f)"),
                {"f": filename},
            )
            logger.info(f"Applied: {filename}")
