"""Async database session management and SQLite FTS5 full-text schema setup."""
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.config import settings
from src.db.models import Base


connect_args = {"timeout": 30.0} if "sqlite" in settings.database_url else {}
engine = create_async_engine(settings.database_url, future=True, connect_args=connect_args)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


# SQLite FTS5: virtual table + triggers to synchronize with messages table.
# Uses external content table with rowid = messages.id.
_FTS_SETUP = [
    """
    CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(
        text,
        transcript,
        extracted_text,
        sender_name,
        content='messages',
        content_rowid='id',
        tokenize='unicode61 remove_diacritics 2'
    );
    """,
    """
    CREATE TRIGGER IF NOT EXISTS messages_fts_ai AFTER INSERT ON messages BEGIN
        INSERT INTO messages_fts(rowid, text, transcript, extracted_text, sender_name)
        VALUES (new.id, new.text, new.transcript, new.extracted_text, new.sender_name);
    END;
    """,
    """
    CREATE TRIGGER IF NOT EXISTS messages_fts_ad AFTER DELETE ON messages BEGIN
        INSERT INTO messages_fts(messages_fts, rowid, text, transcript, extracted_text, sender_name)
        VALUES('delete', old.id, old.text, old.transcript, old.extracted_text, old.sender_name);
    END;
    """,
    """
    CREATE TRIGGER IF NOT EXISTS messages_fts_au AFTER UPDATE ON messages BEGIN
        INSERT INTO messages_fts(messages_fts, rowid, text, transcript, extracted_text, sender_name)
        VALUES('delete', old.id, old.text, old.transcript, old.extracted_text, old.sender_name);
        INSERT INTO messages_fts(rowid, text, transcript, extracted_text, sender_name)
        VALUES (new.id, new.text, new.transcript, new.extracted_text, new.sender_name);
    END;
    """,
]


async def init_db(custom_engine=None) -> None:
    """Initialize database tables, FTS5 virtual tables, triggers, and lightweight schema migrations."""
    target_engine = custom_engine or engine
    if custom_engine is None:
        settings.data_dir  # Ensure data directory exists on disk

    async with target_engine.begin() as conn:
        if "sqlite" in str(target_engine.url):
            try:
                await conn.execute(text("PRAGMA journal_mode=WAL"))
                await conn.execute(text("PRAGMA busy_timeout=30000"))
            except Exception:
                pass

        await conn.run_sync(Base.metadata.create_all)
        for stmt in _FTS_SETUP:
            await conn.execute(text(stmt))

        # Lightweight schema migrations for existing SQLite installations
        res = await conn.execute(text("PRAGMA table_info(user_settings)"))
        cols = {row[1] for row in res.fetchall()}
        if "auto_reply_always" not in cols:
            await conn.execute(text("ALTER TABLE user_settings ADD COLUMN auto_reply_always BOOLEAN DEFAULT 0"))
        if "owner_persona" not in cols:
            await conn.execute(text("ALTER TABLE user_settings ADD COLUMN owner_persona TEXT"))


@asynccontextmanager
async def get_session(custom_sessionmaker=None) -> AsyncIterator[AsyncSession]:
    """Async context manager yielding a managed SQLAlchemy AsyncSession."""
    sm = custom_sessionmaker or SessionLocal
    async with sm() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
