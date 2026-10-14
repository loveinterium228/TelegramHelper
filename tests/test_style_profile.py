"""Tests for communication style profiling and prompt hint generation."""
import json
import pytest

from src.core.style_profile import (
    _parse_json_safe,
    build_style_profile,
    style_profile_as_prompt_hint,
)
from tests.conftest import MockLLMProvider


def test_parse_json_safe():
    valid_json = '{"register": "informal", "emoji_usage": "moderate"}'
    assert _parse_json_safe(valid_json) == {"register": "informal", "emoji_usage": "moderate"}

    fenced_json = '```json\n{"register": "formal"}\n```'
    assert _parse_json_safe(fenced_json) == {"register": "formal"}

    malformed = "Just some random text without JSON"
    assert _parse_json_safe(malformed) == {}


def test_style_profile_as_prompt_hint():
    profile = {
        "address": "first_name",
        "register": "friendly",
        "length": "concise",
        "emoji_usage": "rare",
        "phrases": ["sounds good", "let me know"],
        "notes": "Direct and casual tone",
    }
    hint = style_profile_as_prompt_hint(json.dumps(profile))
    assert "обращение: first_name" in hint
    assert "регистр: friendly" in hint
    assert "длина: concise" in hint
    assert "sounds good" in hint
    assert "Direct and casual tone" in hint


@pytest.mark.asyncio
async def test_build_style_profile():
    mock_data = {
        "address": "informal",
        "register": "conversational",
        "length": "short",
        "emoji_usage": "none",
        "punctuation": "relaxed",
    }
    provider = MockLLMProvider(response_text=json.dumps(mock_data))

    profile = await build_style_profile(
        provider,
        contact_label="Partner",
        my_messages_text="Hey, checking in on the report. Will send over by noon.",
    )
    assert profile["register"] == "conversational"
    assert profile["length"] == "short"
