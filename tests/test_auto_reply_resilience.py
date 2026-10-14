"""Tests for Auto-Reply concurrency deduplication and offline status verification."""
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from src.userbot import auto_reply


@pytest.mark.asyncio
async def test_auto_reply_rejects_concurrent_duplicate():
    """If a reply is already actively being processed for (owner_id, peer_id),

    incoming duplicate messages during typing delay must be safely dropped.
    """
    owner_id = 123456
    sender_id = 999888

    client = MagicMock()
    handler = auto_reply._make_handler(client, owner_id)

    # Event mock
    mock_sender = MagicMock()
    mock_sender.bot = False
    mock_event = MagicMock()
    mock_event.message.out = False
    mock_event.is_private = True
    mock_event.get_sender = AsyncMock(return_value=mock_sender)
    mock_sender.id = sender_id

    # Simulate key already in _active_replies
    reply_key = (owner_id, sender_id)
    auto_reply._active_replies.add(reply_key)

    try:
        # Run handler; it should immediately return without doing anything
        await handler(mock_event)
        mock_event.respond.assert_not_called()
    finally:
        auto_reply._active_replies.discard(reply_key)


@pytest.mark.asyncio
async def test_auto_reply_aborts_if_owner_comes_online_during_typing():
    """If owner comes online while typing delay is running, do not send the automated reply."""
    owner_id = 123456
    sender_id = 888777

    client = MagicMock()
    client.action = MagicMock(return_value=AsyncMock(__aenter__=AsyncMock(), __aexit__=AsyncMock()))
    client.send_read_acknowledge = AsyncMock()

    mock_sender = MagicMock()
    mock_sender.bot = False
    mock_sender.first_name = "Alice"
    mock_sender.last_name = None
    mock_sender.username = "alice"
    mock_sender.phone = None
    mock_sender.id = sender_id

    mock_event = MagicMock()
    mock_event.message.out = False
    mock_event.message.text = "Привет, ты тут?"
    mock_event.message.id = 101
    mock_event.chat_id = sender_id
    mock_event.is_private = True
    mock_event.get_sender = AsyncMock(return_value=mock_sender)
    mock_event.respond = AsyncMock()

    # Mock DB user with auto-reply enabled and static text
    mock_user = MagicMock()
    mock_user.id = 1
    mock_user.settings.auto_reply_enabled = True
    mock_user.settings.ignore_archived = False
    mock_user.settings.auto_reply_always = False
    mock_user.settings.auto_reply_cooldown_min = 0
    mock_user.settings.auto_reply_mode = "static"
    mock_user.settings.auto_reply_text = "Сейчас занят, отвечу позже"

    # Sequence for _is_offline: First check returns True (offline), second check returns False (owner came online)
    is_offline_mock = AsyncMock(side_effect=[True, False])

    with patch("src.userbot.auto_reply.get_session"), \
         patch("src.userbot.auto_reply.get_or_create_user", AsyncMock(return_value=mock_user)), \
         patch("src.userbot.auto_reply.get_contact", AsyncMock(return_value=None)), \
         patch("src.userbot.auto_reply.upsert_contact", AsyncMock()), \
         patch("src.userbot.auto_reply._recently_replied", AsyncMock(return_value=False)), \
         patch("src.userbot.auto_reply._is_first_reply", AsyncMock(return_value=False)), \
         patch("src.userbot.auto_reply.asyncio.sleep", AsyncMock()), \
         patch("src.userbot.auto_reply._is_offline", is_offline_mock):

        handler = auto_reply._make_handler(client, owner_id)
        await handler(mock_event)

        # Ensure respond was NOT called because owner came online
        mock_event.respond.assert_not_called()
        # Verify key was cleared from _active_replies
        assert (owner_id, sender_id) not in auto_reply._active_replies
