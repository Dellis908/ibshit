"""Tests for the HTML sanitiser used on question content."""

from __future__ import annotations

import pytest

from app.sanitize import sanitize_html, sanitize_plain, to_plain_text


# --------------------------------------------------------------- XSS payloads

@pytest.mark.parametrize(
    "payload,dangerous",
    [
        ("<script>alert(1)</script>", "<script"),
        ("<SCRIPT>alert(1)</SCRIPT>", "<script"),
        ('<img src=x onerror="alert(1)">', "onerror"),
        ('<a href="javascript:alert(1)">x</a>', "javascript:"),
        ('<div style="background:url(javascript:alert(1))">x</div>', "javascript:"),
        ('<iframe src="http://evil.example"></iframe>', "<iframe"),
        ("<object data=\"x\"></object>", "<object"),
        ("<embed src=x>", "<embed"),
        ("<style>body{display:none}</style>", "<style"),
        ('<svg onload="alert(1)"></svg>', "onload"),
        ("<noscript><p>x</p></noscript>", "<noscript"),
        ("<template><p>x</p></template>", "<template"),
        ("<p onclick=alert(1)>x</p>", "onclick"),
        ("<a href=\"data:text/html,<script>alert(1)</script>\">x</a>", "data:text/html"),
    ],
)
def test_dangerous_markup_is_removed(payload, dangerous):
    cleaned = sanitize_html(payload)
    assert dangerous.lower() not in cleaned.lower(), cleaned


def test_script_content_is_dropped_not_just_the_tag():
    cleaned = sanitize_html("before<script>alert('secret')</script>after")
    assert "alert" not in cleaned
    assert "secret" not in cleaned
    assert "before" in cleaned
    assert "after" in cleaned


def test_nested_dangerous_tags_are_fully_dropped():
    cleaned = sanitize_html("<div><script><iframe>inner</iframe></script>visible</div>")
    assert "inner" not in cleaned
    assert "visible" in cleaned


# ------------------------------------------------------------- safe formatting

@pytest.mark.parametrize(
    "payload,expected",
    [
        ("CO<sub>2</sub>", "<sub>2</sub>"),
        ("10<sup>-3</sup>", "<sup>-3</sup>"),
        ("<b>bold</b>", "<b>bold</b>"),
        ("<em>emphasis</em>", "<em>emphasis</em>"),
        ("line<br>break", "<br>"),
        ("<ul><li>one</li><li>two</li></ul>", "<li>one</li>"),
    ],
)
def test_safe_formatting_is_preserved(payload, expected):
    assert expected in sanitize_html(payload)


def test_tables_with_rowspan_are_preserved():
    cleaned = sanitize_html('<table><tr><td colspan="2">x</td></tr></table>')
    assert "<table>" in cleaned
    assert 'colspan="2"' in cleaned


def test_text_is_escaped():
    cleaned = sanitize_html("5 > 3 < 7 & rising")
    assert "&gt;" in cleaned
    assert "&lt;" in cleaned
    assert "&amp;" in cleaned


def test_unsupported_but_harmless_tags_are_unwrapped():
    cleaned = sanitize_html("<article><h1>Title</h1></article>")
    assert "<article>" not in cleaned
    assert "Title" in cleaned


def test_unclosed_tags_are_closed():
    cleaned = sanitize_html("<p>unclosed paragraph")
    assert cleaned.count("<p>") == cleaned.count("</p>")


def test_empty_and_none_inputs():
    assert sanitize_html("") == ""
    assert sanitize_html(None) == ""
    assert to_plain_text(None) == ""


# ---------------------------------------------------------------- plain text

def test_to_plain_text_strips_markup_and_decodes_entities():
    assert to_plain_text("<p>CO<sub>2</sub> &amp; H<sub>2</sub>O</p>") == "CO2 & H2O"


def test_sanitize_plain_truncates():
    assert len(sanitize_plain("abcdefghij", max_length=5)) == 5


def test_sanitize_plain_removes_all_tags():
    assert sanitize_plain("<b>hello</b> <script>x</script>") == "hello"


def test_sanitiser_survives_malformed_markup():
    # Unbalanced and junk markup must not raise.
    for payload in ["<b", "<>", "</>", "<p <b>>x</b>", "<!-- comment -->", "<?php ?>"]:
        sanitize_html(payload)
