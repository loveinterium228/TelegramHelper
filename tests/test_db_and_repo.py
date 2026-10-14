"""Comprehensive tests for Database Models, Repositories, FTS5 Search, and Encryption at rest."""
import asyncio
from datetime import datetime, timezone
import json
import pytest
from sqlalchemy import select

from src.db.models import TelegramSession, User
from src.db.repo import (
    add_auto_reply_log,
    add_commitment,
    add_news_topic,
    create_pending_action,
    delete_api_key,
    delete_news_topic,
    delete_pending_action,
    delete_telegram_session,
    fetch_chat_messages,
    fts_search,
    get_api_key,
    get_contact,
    get_or_create_user,
    get_pending_action,
    list_contacts,
    list_news_topics,
    list_open_commitments,
    load_telegram_session,
    save_telegram_session,
    update_commitment_status,
    upsert_api_key,
    upsert_contact,
    upsert_message,
)


@pytest.mark.asyncio
async def test_get_or_create_user_idempotency(db_session):
    telegram_id = 99887766

    user1 = await get_or_create_user(db_session, telegram_id)
    assert user1.telegram_id == telegram_id
    assert user1.settings is not None
    assert user1.settings.timezone == "UTC"

    # Calling again must return existing user without duplicating
    user2 = await get_or_create_user(db_session, telegram_id)
    assert user2.id == user1.id


@pytest.mark.asyncio
async def test_telegram_session_encryption_roundtrip(db_session):
    user = await get_or_create_user(db_session, 112233)

    api_id = 1234567
    api_hash = "abcdef0123456789abcdef0123456789"
    session_str = "1BVtsOMQBu8abcdefghij..."
    phone = "+1234567890"

    await save_telegram_session(
        db_session,
        user,
        api_id=api_id,
        api_hash=api_hash,
        session_string=session_str,
        phone=phone,
        account_label="Main Account",
    )
    await db_session.flush()

    # Verify data in database is encrypted at rest
    raw_row = await db_session.get(TelegramSession, user.id)
    assert raw_row.api_hash_enc != api_hash
    assert raw_row.session_string_enc != session_str
    assert "gAAAAA" in raw_row.api_hash_enc  # Fernet cipher signature

    # Loading session must decrypt correctly
    loaded = await load_telegram_session(db_session, user)
    assert loaded is not None
    loaded_api_id, loaded_api_hash, loaded_session_str = loaded
    assert loaded_api_id == api_id
    assert loaded_api_hash == api_hash
    assert loaded_session_str == session_str

    # Deleting session
    await delete_telegram_session(db_session, user)
    await db_session.flush()
    assert await load_telegram_session(db_session, user) is None


@pytest.mark.asyncio
async def test_api_keys_encryption_and_upsert(db_session):
    user = await get_or_create_user(db_session, 445566)

    await upsert_api_key(db_session, user, "openai", "sk-proj-secret-key-1")
    await db_session.flush()

    key = await get_api_key(db_session, user, "openai")
    assert key == "sk-proj-secret-key-1"

    # Overwrite key
    await upsert_api_key(db_session, user, "openai", "sk-proj-secret-key-2")
    await db_session.flush()

    updated = await get_api_key(db_session, user, "openai")
    assert updated == "sk-proj-secret-key-2"

    # Non-existent provider returns None
    assert await get_api_key(db_session, user, "gemini") is None

    # Delete key
    await delete_api_key(db_session, user, "openai")
    await db_session.flush()
    assert await get_api_key(db_session, user, "openai") is None


@pytest.mark.asyncio
async def test_contacts_upsert_and_filtering(db_session):
    user = await get_or_create_user(db_session, 123)

    await upsert_contact(
        db_session, user,
        peer_id=1001, peer_kind="user", display_name="Alice", username="alice",
    )
    await upsert_contact(
        db_session, user,
        peer_id=1002, peer_kind="user", display_name="Bot Helper", is_bot=True,
    )
    await upsert_contact(
        db_session, user,
        peer_id=2001, peer_kind="channel", display_name="Tech News", is_news_source=True,
    )
    await upsert_contact(
        db_session, user,
        peer_id=3001, peer_kind="user", display_name="Old Archived", is_archived=True,
    )
    await db_session.flush()

    # Filter only humans (exclude bots, exclude archived)
    contacts = await list_contacts(db_session, user, kinds=("user",), include_bots=False, include_archived=False)
    assert len(contacts) == 1
    assert contacts[0].display_name == "Alice"

    # Filter news sources
    news_channels = await list_contacts(db_session, user, only_news_sources=True)
    assert len(news_channels) == 1
    assert news_channels[0].display_name == "Tech News"


@pytest.mark.asyncio
async def test_message_upsert_and_sqlite_fts5_search(db_session):
    user = await get_or_create_user(db_session, 888)
    now = datetime.now(timezone.utc).replace(tzinfo=None)

    # Insert test messages
    await upsert_message(
        db_session,
        user_id=user.id,
        peer_id=501,
        message_id=1,
        sender_id=501,
        sender_name="Charlie",
        is_outgoing=False,
        date=now,
        kind="text",
        text="Can we discuss the architecture diagram for the new release?",
    )
    await upsert_message(
        db_session,
        user_id=user.id,
        peer_id=501,
        message_id=2,
        sender_id=888,
        sender_name="Owner",
        is_outgoing=True,
        date=now,
        kind="text",
        text="Sure, I uploaded the high-resolution architecture blueprint to the cloud.",
    )
    await upsert_message(
        db_session,
        user_id=user.id,
        peer_id=502,
        message_id=10,
        sender_id=502,
        sender_name="David",
        is_outgoing=False,
        date=now,
        kind="text",
        text="Ordering pizza for lunch today.",
    )
    await db_session.flush()

    # Search for 'architecture' via SQLite FTS5 index
    hits = await fts_search(db_session, user.id, "architecture")
    assert len(hits) == 2
    assert all("architecture" in h.snippet.lower() for h in hits)

    # Search for 'pizza'
    pizza_hits = await fts_search(db_session, user.id, "pizza")
    assert len(pizza_hits) == 1
    assert pizza_hits[0].peer_id == 502


@pytest.mark.asyncio
async def test_commitments_lifecycle(db_session):
    user = await get_or_create_user(db_session, 777)
    now = datetime.now(timezone.utc).replace(tzinfo=None)

    c1 = await add_commitment(
        db_session,
        user_id=user.id,
        peer_id=10,
        peer_name="Client",
        message_id=100,
        direction="mine",
        text="Send contract draft by 5pm",
        deadline_at=now,
    )
    c2 = await add_commitment(
        db_session,
        user_id=user.id,
        peer_id=10,
        peer_name="Client",
        message_id=101,
        direction="theirs",
        text="Review wireframes",
        deadline_at=None,
    )
    await db_session.flush()

    open_items = await list_open_commitments(db_session, user)
    assert len(open_items) == 2

    # Filter direction
    my_items = await list_open_commitments(db_session, user, direction="mine")
    assert len(my_items) == 1
    assert my_items[0].text == "Send contract draft by 5pm"

    # Update status to done
    await update_commitment_status(db_session, c1.id, "done")
    await db_session.flush()

    remaining_open = await list_open_commitments(db_session, user)
    assert len(remaining_open) == 1
    assert remaining_open[0].id == c2.id


@pytest.mark.asyncio
async def test_pending_action_storage(db_session):
    user = await get_or_create_user(db_session, 666)
    action = await create_pending_action(
        db_session,
        user_id=user.id,
        kind="send_message",
        payload={"peer_id": 12345, "text": "Confirmed meeting"},
    )
    await db_session.flush()

    loaded = await get_pending_action(db_session, action.id)
    assert loaded is not None
    assert loaded.kind == "send_message"
    data = json.loads(loaded.payload)
    assert data["text"] == "Confirmed meeting"

    await delete_pending_action(db_session, action.id)
    await db_session.flush()
    assert await get_pending_action(db_session, action.id) is None


@pytest.mark.asyncio
async def test_news_topics_management(db_session):
    user = await get_or_create_user(db_session, 555)

    t1 = await add_news_topic(db_session, user, topic="Artificial Intelligence", hours=24)
    t2 = await add_news_topic(db_session, user, topic="Space Exploration", hours=48)
    await db_session.flush()

    topics = await list_news_topics(db_session, user)
    assert len(topics) == 2

    await delete_news_topic(db_session, user, t1.id)
    await db_session.flush()

    remaining = await list_news_topics(db_session, user)
    assert len(remaining) == 1
    assert remaining[0].topic == "Space Exploration"
