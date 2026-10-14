"""Tests for timezone utilities and date formatting."""
from datetime import datetime, timezone
import pytest

from src.core.timeutil import (
    fmt_local,
    is_valid_tz,
    now_in_tz,
    parse_tz,
    tz_short,
    utc_to_local,
)


def test_is_valid_tz():
    assert is_valid_tz("UTC") is True
    assert is_valid_tz("Europe/London") is True
    assert is_valid_tz("America/New_York") is True
    assert is_valid_tz("Asia/Tokyo") is True
    assert is_valid_tz("Mars/Olympus") is False
    assert is_valid_tz("") is False


def test_parse_tz_fallback():
    tz_utc = parse_tz("UTC")
    assert str(tz_utc) == "UTC"

    # Invalid names must safely fallback to UTC
    tz_fallback = parse_tz("Invalid/TimeZone")
    assert str(tz_fallback) == "UTC"

    tz_none = parse_tz(None)
    assert str(tz_none) == "UTC"


def test_now_in_tz():
    now_london = now_in_tz("Europe/London")
    assert now_london.tzinfo is not None
    assert "London" in str(now_london.tzinfo)


def test_utc_to_local():
    # Naive UTC datetime
    utc_dt = datetime(2026, 5, 10, 12, 0, 0)
    local_dt = utc_to_local(utc_dt, "Europe/London")
    assert local_dt.tzinfo is not None
    # London in May is BST (UTC+1)
    assert local_dt.hour == 13


def test_fmt_local():
    assert fmt_local(None, "UTC") == "—"

    utc_dt = datetime(2026, 1, 15, 10, 30, 0)
    formatted = fmt_local(utc_dt, "UTC", fmt="%Y-%m-%d %H:%M")
    assert formatted == "2026-01-15 10:30"


def test_tz_short():
    short_utc = tz_short("UTC")
    assert "UTC" in short_utc
    assert "UTC+0" in short_utc or "UTC" in short_utc
