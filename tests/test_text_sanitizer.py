"""Tests for Telegram HTML text sanitization and whitelist validation."""
import pytest

from src.core.text_sanitizer import sanitize_html


def test_sanitize_empty_and_none():
    assert sanitize_html(None) == ""
    assert sanitize_html("") == ""
    assert sanitize_html("   ") == ""


def test_escape_plain_text():
    raw = "Formula: x < 5 & y > 10"
    sanitized = sanitize_html(raw)
    assert sanitized == "Formula: x &lt; 5 &amp; y &gt; 10"


def test_preserve_whitelisted_tags():
    html_input = "<b>Bold</b>, <i>Italic</i>, <code>code</code>, <u>Underline</u>, <s>Strike</s>"
    assert sanitize_html(html_input) == "<b>Bold</b>, <i>Italic</i>, <code>code</code>, <u>Underline</u>, <s>Strike</s>"


def test_normalize_tag_aliases():
    html_input = "<strong>Strong</strong> and <em>Emphasis</em>"
    assert sanitize_html(html_input) == "<b>Strong</b> and <i>Emphasis</i>"


def test_strip_disallowed_tags():
    html_input = "<script>alert('xss')</script>Hello <img src='test.jpg'/> World!"
    sanitized = sanitize_html(html_input)
    assert "<script>" not in sanitized
    assert "<img>" not in sanitized
    assert "alert('xss')" in sanitized
    assert "Hello" in sanitized
    assert "World!" in sanitized


def test_links_preserved():
    html_input = '<a href="https://github.com/loveinterium228">GitHub</a>'
    assert sanitize_html(html_input) == '<a href="https://github.com/loveinterium228">GitHub</a>'


def test_markdown_code_fences():
    md_input = "```python\ndef hello():\n    return 'world'\n```"
    sanitized = sanitize_html(md_input)
    assert sanitized.startswith("<pre>")
    assert sanitized.endswith("</pre>")
    assert "def hello():" in sanitized


def test_block_elements_converted_to_newlines():
    html_input = "<p>First paragraph</p><p>Second paragraph</p>"
    sanitized = sanitize_html(html_input)
    assert "First paragraph" in sanitized
    assert "Second paragraph" in sanitized
    assert "\n" in sanitized
