"""Tests for fuzzy contact resolver and candidate ranking."""
import pytest
from unittest.mock import AsyncMock, MagicMock

from src.core.contact_resolver import ContactCandidate, _searchable, resolve
from src.db.models import Contact, User
from src.db.repo import upsert_contact


def test_contact_candidate_label():
    cand_with_user = ContactCandidate(peer_id=1, display_name="John Doe", username="johndoe", peer_kind="user", score=95)
    assert cand_with_user.label() == "John Doe (@johndoe)"

    cand_without_user = ContactCandidate(peer_id=2, display_name="Sarah", username=None, peer_kind="user", score=90)
    assert cand_without_user.label() == "Sarah"


def test_searchable_representation():
    contact = Contact(display_name="Michael Scott", username="mscott", phone="+123456789")
    searchable = _searchable(contact)
    assert "Michael Scott" in searchable
    assert "@mscott" in searchable
    assert "+123456789" in searchable


@pytest.mark.asyncio
async def test_fuzzy_resolution_matches_typos(db_session, monkeypatch):
    user = User(id=1, telegram_id=123)

    # Populate contacts in test DB
    await upsert_contact(db_session, user, peer_id=101, peer_kind="user", display_name="Alexander Hamilton", username="ahamilton")
    await upsert_contact(db_session, user, peer_id=102, peer_kind="user", display_name="George Washington", username="gwash")
    await db_session.flush()

    # Mock get_session in contact_resolver to return our test db_session
    from contextlib import asynccontextmanager
    @asynccontextmanager
    async def mock_get_session():
        yield db_session

    monkeypatch.setattr("src.core.contact_resolver.get_session", mock_get_session)

    mock_client = MagicMock()
    # Query with slight typo 'Alexandr'
    candidates = await resolve(mock_client, user, "Alexandr")
    assert len(candidates) >= 1
    assert candidates[0].peer_id == 101
    assert candidates[0].display_name == "Alexander Hamilton"
    assert candidates[0].score >= 80
