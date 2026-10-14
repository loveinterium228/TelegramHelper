"""Tests for commitment and action item extractor."""
from datetime import datetime
import json
import pytest

from src.core.commitment_extractor import _parse_iso, _parse_json_array, extract_and_save_commitments
from src.db.models import Contact, Message
from tests.conftest import MockLLMProvider


def test_parse_json_array_valid():
    raw = '[{"direction": "mine", "text": "Send slides", "deadline": "2026-05-10T12:00:00Z"}]'
    items = _parse_json_array(raw)
    assert len(items) == 1
    assert items[0]["text"] == "Send slides"


def test_parse_json_array_fenced():
    raw = '```json\n[{"direction": "theirs", "text": "Review code"}]\n```'
    items = _parse_json_array(raw)
    assert len(items) == 1
    assert items[0]["direction"] == "theirs"


def test_parse_json_array_invalid():
    raw = "There are no commitments mentioned here."
    items = _parse_json_array(raw)
    assert items == []


def test_parse_iso():
    dt = _parse_iso("2026-05-10T15:30:00Z")
    assert isinstance(dt, datetime)
    assert dt.year == 2026
    assert dt.month == 5
    assert dt.day == 10
    assert dt.hour == 15
    assert dt.minute == 30

    assert _parse_iso(None) is None
    assert _parse_iso("not-a-date") is None


@pytest.mark.asyncio
async def test_extract_and_save_commitments(db_session, monkeypatch):
    mock_items = [
        {"direction": "mine", "text": "Prepare presentation", "deadline": "2026-06-01T10:00:00Z", "message_id": 42},
        {"direction": "theirs", "text": "Send feedback", "deadline": None, "message_id": 43},
    ]
    provider = MockLLMProvider(response_text=json.dumps(mock_items))

    from contextlib import asynccontextmanager
    @asynccontextmanager
    async def mock_get_session():
        yield db_session

    monkeypatch.setattr("src.core.commitment_extractor.get_session", mock_get_session)

    contact = Contact(peer_id=500, display_name="Manager")
    messages = [
        Message(id=1, user_id=1, peer_id=500, message_id=42, is_outgoing=True, text="I will prepare the presentation.", kind="text", date=datetime.now()),
        Message(id=2, user_id=1, peer_id=500, message_id=43, is_outgoing=False, text="I will send feedback tomorrow.", kind="text", date=datetime.now()),
    ]

    saved = await extract_and_save_commitments(
        provider,
        user_id=1,
        contact=contact,
        messages=messages,
    )
    assert len(saved) == 2
    assert saved[0]["text"] == "Prepare presentation"
