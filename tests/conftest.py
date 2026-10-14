"""Pytest fixtures for TelegramHelper async test suite."""
import asyncio
from typing import AsyncIterator
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.db.models import Base
from src.db.session import _FTS_SETUP, init_db
from src.llm.base import ChatMessage, LLMProvider


@pytest.fixture(scope="session")
def event_loop():
    """Create an instance of the default event loop for each test session."""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture
async def async_engine():
    """Create a pristine, in-memory SQLite async database engine for testing."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    await init_db(custom_engine=engine)
    yield engine
    await engine.dispose()


@pytest.fixture
async def db_session(async_engine) -> AsyncIterator[AsyncSession]:
    """Yield an active AsyncSession bound to the in-memory SQLite engine."""
    session_factory = async_sessionmaker(async_engine, expire_on_commit=False, class_=AsyncSession)
    async with session_factory() as session:
        yield session
        await session.rollback()


class MockLLMProvider(LLMProvider):
    """Mock LLM Provider for unit and integration tests."""
    name = "mock"

    def __init__(self, response_text: str = "{}") -> None:
        self.response_text = response_text
        self.call_history: list[list[ChatMessage]] = []

    async def validate_key(self) -> bool:
        return True

    async def chat(self, messages: list[ChatMessage], *, heavy: bool = False) -> str:
        self.call_history.append(messages)
        return self.response_text

    async def embed(self, text: str) -> list[float]:
        # Return a deterministic 4-dimensional mock vector
        val = float(len(text) % 10) / 10.0
        return [val, 0.1, 0.2, 0.3]


@pytest.fixture
def mock_llm() -> MockLLMProvider:
    return MockLLMProvider()
