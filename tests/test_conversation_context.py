"""Tests for in-memory short-term conversation context store."""
import time
import pytest

from src.core.conversation_context import (
    MAX_TURNS,
    add_turn,
    get_last_peer,
    get_recent_turns,
    render_history_block,
    set_last_peer,
)


def test_add_and_retrieve_turns():
    user_id = 12345
    add_turn(user_id, "Hello bot", "Hello owner!")
    add_turn(user_id, "What's up?", "All systems operational.")

    turns = get_recent_turns(user_id)
    assert len(turns) == 2
    assert turns[0] == ("Hello bot", "Hello owner!")
    assert turns[1] == ("What's up?", "All systems operational.")


def test_turn_sliding_window_limit():
    user_id = 99999
    for i in range(MAX_TURNS + 5):
        add_turn(user_id, f"Question {i}", f"Answer {i}")

    turns = get_recent_turns(user_id)
    assert len(turns) == MAX_TURNS
    # Oldest turns evicted; newest turn is last
    assert turns[-1] == (f"Question {MAX_TURNS + 4}", f"Answer {MAX_TURNS + 4}")


def test_last_peer_ttl_expiration(monkeypatch):
    user_id = 55555
    current_time = 100000.0
    monkeypatch.setattr(time, "time", lambda: current_time)

    set_last_peer(user_id, 101, "Alice")
    assert get_last_peer(user_id) == (101, "Alice")

    # Advance time by 31 minutes (TTL is 30 minutes)
    monkeypatch.setattr(time, "time", lambda: current_time + 1860)
    assert get_last_peer(user_id) is None


def test_render_history_block(monkeypatch):
    user_id = 77777
    monkeypatch.setattr(time, "time", lambda: 100000.0)

    set_last_peer(user_id, 202, "Bob")
    add_turn(user_id, "Can you message Bob?", "Drafted reply for Bob.")

    block = render_history_block(user_id)
    assert "Bob" in block
    assert "202" in block
    assert "Can you message Bob?" in block
