"""Concurrency and race condition tests for SQLite database operations.

Verifies that concurrent background tasks (digest, reminders, news, sync)
accessing get_or_create_user, upsert_contact, and cache_transcript concurrently
do not trigger sqlite3.IntegrityError: UNIQUE constraint failed.
"""
import asyncio
from pathlib import Path
import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.db.models import Contact, TranscriptionCache, User, UserSettings
from src.db.repo import cache_transcript, get_or_create_user, upsert_contact
from src.db.session import init_db


@pytest.fixture
async def file_sqlite_engine(tmp_path: Path):
    """File-based SQLite engine with WAL mode and timeout to test real concurrent disk operations."""
    db_file = tmp_path / "test_concurrent.db"
    db_url = f"sqlite+aiosqlite:///{db_file}"
    engine = create_async_engine(db_url, future=True, connect_args={"timeout": 30.0})
    await init_db(custom_engine=engine)
    yield engine
    await engine.dispose()


@pytest.mark.asyncio
async def test_concurrent_user_creation_race_condition(file_sqlite_engine):
    """20 concurrent tasks calling get_or_create_user on an empty database must not raise UNIQUE constraint errors."""
    session_factory = async_sessionmaker(file_sqlite_engine, expire_on_commit=False, class_=AsyncSession)
    target_telegram_id = 777000111

    async def worker(worker_id: int):
        async with session_factory() as session:
            user = await get_or_create_user(session, target_telegram_id)
            await session.commit()
            return user.id, user.telegram_id, user.settings is not None

    # Run 20 workers simultaneously via asyncio.gather
    results = await asyncio.gather(*[worker(i) for i in range(20)], return_exceptions=False)

    assert len(results) == 20
    user_ids = {r[0] for r in results}
    assert len(user_ids) == 1, f"Expected 1 unique user ID, got {user_ids}"

    # Verify every worker got valid settings attached
    assert all(r[2] is True for r in results)

    # Verify exactly 1 user and 1 user_settings in the database
    async with session_factory() as session:
        user_count = (await session.execute(select(func.count(User.id)))).scalar_one()
        settings_count = (await session.execute(select(func.count(UserSettings.user_id)))).scalar_one()
        assert user_count == 1
        assert settings_count == 1


@pytest.mark.asyncio
async def test_concurrent_contact_upsert_race_condition(file_sqlite_engine):
    """Multiple concurrent workers updating the same contact must not conflict."""
    session_factory = async_sessionmaker(file_sqlite_engine, expire_on_commit=False, class_=AsyncSession)

    async with session_factory() as session:
        user = await get_or_create_user(session, 888111222)
        await session.commit()

    async def contact_worker(worker_id: int):
        async with session_factory() as session:
            # Re-fetch user in worker session
            u = await get_or_create_user(session, 888111222)
            c = await upsert_contact(
                session,
                u,
                peer_id=999888,
                peer_kind="user",
                display_name=f"Contact Name {worker_id}",
                username="testuser",
                is_bot=False,
            )
            await session.commit()
            return c.id

    results = await asyncio.gather(*[contact_worker(i) for i in range(15)], return_exceptions=False)
    assert len(results) == 15
    assert len(set(results)) == 1

    async with session_factory() as session:
        contact_count = (await session.execute(select(func.count(Contact.id)))).scalar_one()
        assert contact_count == 1


@pytest.mark.asyncio
async def test_concurrent_transcription_cache_upsert(file_sqlite_engine):
    """Concurrent workers caching transcription for the same file_id must succeed atomically."""
    session_factory = async_sessionmaker(file_sqlite_engine, expire_on_commit=False, class_=AsyncSession)
    file_id = "unique_audio_file_999"

    async def cache_worker(worker_id: int):
        async with session_factory() as session:
            await cache_transcript(
                session,
                file_id=file_id,
                text=f"Transcribed text from worker {worker_id}",
                duration_seconds=12.5,
            )
            await session.commit()

    await asyncio.gather(*[cache_worker(i) for i in range(15)], return_exceptions=False)

    async with session_factory() as session:
        cache_count = (await session.execute(select(func.count(TranscriptionCache.file_id)))).scalar_one()
        assert cache_count == 1
