"""Tests for send handler error resilience, pending action preservation, and HTML escaping."""
import json
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from src.bot.handlers import send


@pytest.mark.asyncio
async def test_send_failure_preserves_pending_action_and_escapes_html():
    """If client.send_message raises an RPC or network exception:

    1. PendingAction must NOT be deleted, allowing retry.
    2. Exception text containing '<' or '>' must be HTML-escaped.
    """
    action_id = 42
    callback = MagicMock()
    callback.from_user.id = 111222
    callback.data = f"send:confirm:{action_id}"
    callback.message = MagicMock()
    callback.message.edit_text = AsyncMock()
    callback.answer = AsyncMock()

    client = MagicMock()
    client.get_entity = AsyncMock(return_value="target_entity")
    # Simulate Telethon exception with raw brackets
    client.send_message = AsyncMock(side_effect=Exception("<RpcError 420: FLOOD_WAIT_10>"))

    userbot_manager = MagicMock()
    userbot_manager.get_client.return_value = client

    mock_action = MagicMock()
    mock_action.payload = json.dumps({"peer_id": 999888, "text": "Test message"})

    with patch("src.bot.handlers.send.get_session"), \
         patch("src.bot.handlers.send.get_pending_action", AsyncMock(return_value=mock_action)), \
         patch("src.bot.handlers.send.delete_pending_action", AsyncMock()) as mock_delete:

        await send.cb_confirm(callback, userbot_manager)

        # 1. Action was NOT deleted
        mock_delete.assert_not_called()

        # 2. Callback edit_text received escaped HTML
        callback.message.edit_text.assert_awaited_once()
        sent_text = callback.message.edit_text.call_args[0][0]
        assert "&lt;RpcError 420: FLOOD_WAIT_10&gt;" in sent_text
        assert "<RpcError" not in sent_text


@pytest.mark.asyncio
async def test_send_success_deletes_pending_action():
    """If client.send_message succeeds, PendingAction is removed."""
    action_id = 43
    callback = MagicMock()
    callback.from_user.id = 111222
    callback.data = f"send:confirm:{action_id}"
    callback.message = MagicMock()
    callback.message.edit_text = AsyncMock()
    callback.answer = AsyncMock()

    client = MagicMock()
    client.get_entity = AsyncMock(return_value="target_entity")
    client.send_message = AsyncMock()

    userbot_manager = MagicMock()
    userbot_manager.get_client.return_value = client

    mock_action = MagicMock()
    mock_action.payload = json.dumps({"peer_id": 999888, "text": "Success message"})

    with patch("src.bot.handlers.send.get_session"), \
         patch("src.bot.handlers.send.get_pending_action", AsyncMock(return_value=mock_action)), \
         patch("src.bot.handlers.send.delete_pending_action", AsyncMock()) as mock_delete:

        await send.cb_confirm(callback, userbot_manager)

        # Successfully sent message
        client.send_message.assert_awaited_once_with("target_entity", "Success message")
        # PendingAction deleted
        mock_delete.assert_awaited_once()
        # Success message shown
        callback.message.edit_text.assert_awaited_once_with("✅ Сообщение отправлено.")
