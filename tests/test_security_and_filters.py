"""Tests for security filters, access control, and setting value coercion."""
from unittest.mock import MagicMock
import pytest
from aiogram.types import CallbackQuery, Message, User as AiogramUser

from src.bot.filters import OwnerOnly
from src.bot.handlers.free_text import _coerce_setting_value
from src.config import settings


@pytest.mark.asyncio
async def test_owner_only_filter_allows_owner(monkeypatch):
    monkeypatch.setattr(settings, "owner_telegram_id", 123456789)
    filter_obj = OwnerOnly()

    # Event from owner
    event = MagicMock(spec=Message)
    event.from_user = AiogramUser(id=123456789, is_bot=False, first_name="Owner")

    assert await filter_obj(event) is True


@pytest.mark.asyncio
async def test_owner_only_filter_rejects_strangers(monkeypatch):
    monkeypatch.setattr(settings, "owner_telegram_id", 123456789)
    filter_obj = OwnerOnly()

    # Event from unauthorized stranger
    event = MagicMock(spec=Message)
    event.from_user = AiogramUser(id=987654321, is_bot=False, first_name="Stranger")

    assert await filter_obj(event) is False


@pytest.mark.asyncio
async def test_owner_only_filter_rejects_none_user(monkeypatch):
    filter_obj = OwnerOnly()
    event = MagicMock(spec=Message)
    event.from_user = None

    assert await filter_obj(event) is False


def test_coerce_setting_values():
    # Booleans
    val, err = _coerce_setting_value("bool", True)
    assert val is True and err is None

    val, err = _coerce_setting_value("bool", "yes")
    assert val is True and err is None

    val, err = _coerce_setting_value("bool", "false")
    assert val is False and err is None

    val, err = _coerce_setting_value("bool", "invalid")
    assert val is None and "true/false" in err

    # Integers
    val, err = _coerce_setting_value("int", "42")
    assert val == 42 and err is None

    val, err = _coerce_setting_value("int", "abc")
    assert val is None

    # Time (HH:MM)
    val, err = _coerce_setting_value("hm", "08:30")
    assert val == "08:30" and err is None

    val, err = _coerce_setting_value("hm", "25:70")
    assert val is None

    # Choices
    val, err = _coerce_setting_value("choice:static,smart", "smart")
    assert val == "smart" and err is None

    val, err = _coerce_setting_value("choice:static,smart", "unknown")
    assert val is None
