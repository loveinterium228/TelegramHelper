"""Tests for LLM agent intent router and JSON parsing resilience."""
import json
import pytest

from src.core.agent import _safe_parse, _strip_fence, route_intent
from tests.conftest import MockLLMProvider


def test_strip_fence():
    fenced_json = "```json\n{\"intent\": \"list_todos\"}\n```"
    assert _strip_fence(fenced_json) == '{"intent": "list_todos"}'

    fenced_bare = "```\n{\"intent\": \"search\", \"query\": \"test\"}\n```"
    assert _strip_fence(fenced_bare) == '{"intent": "search", "query": "test"}'

    unfenced = '{"intent": "catchup"}'
    assert _strip_fence(unfenced) == '{"intent": "catchup"}'


def test_safe_parse_valid():
    raw = '{"intent": "send_message", "recipient": "Sarah", "text": "See you at 6"}'
    parsed = _safe_parse(raw)
    assert parsed["intent"] == "send_message"
    assert parsed["recipient"] == "Sarah"


def test_safe_parse_embedded_in_text():
    raw = 'Sure! Here is the JSON payload:\n{"intent": "list_todos"}\nLet me know if you need more info.'
    parsed = _safe_parse(raw)
    assert parsed["intent"] == "list_todos"


def test_safe_parse_malformed():
    raw = "Not a valid JSON at all!"
    parsed = _safe_parse(raw)
    assert parsed == {"intent": "unknown"}


@pytest.mark.asyncio
async def test_route_intent_send_message():
    expected = {"intent": "send_message", "recipient": "Alex", "text": "Meeting at 5"}
    provider = MockLLMProvider(response_text=json.dumps(expected))

    result = await route_intent(provider, "Text Alex that the meeting is at 5")
    assert result["intent"] == "send_message"
    assert result["recipient"] == "Alex"
    assert result["text"] == "Meeting at 5"


@pytest.mark.asyncio
async def test_route_intent_multi_action():
    expected = {
        "intent": "multi",
        "actions": [
            {"intent": "set_setting", "key": "news_enabled", "value": True},
            {"intent": "set_setting", "key": "news_digest_time", "value": "08:00"},
        ],
    }
    provider = MockLLMProvider(response_text=json.dumps(expected))

    result = await route_intent(provider, "Turn on news and set digest to 8am")
    assert result["intent"] == "multi"
    assert len(result["actions"]) == 2
    assert result["actions"][0]["key"] == "news_enabled"


@pytest.mark.asyncio
async def test_route_intent_timezone_and_history_injection():
    provider = MockLLMProvider(response_text='{"intent": "list_todos"}')
    await route_intent(
        provider,
        "What are my tasks for tomorrow?",
        now_local="2026-05-10 14:00",
        tz_name="Europe/London",
        history_block="Last contact was John.",
    )

    system_message = provider.call_history[0][0].content
    assert "2026-05-10 14:00" in system_message
    assert "Europe/London" in system_message
    assert "Last contact was John." in system_message
