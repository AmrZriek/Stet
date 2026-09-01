# -*- coding: utf-8 -*-
"""Tests for the unified PromptCompiler (§3b).

Phase 3b requires a single compilation function for panel, silent, welcome,
and saved-action paths with a fresh cryptographically random 128-bit nonce
per request: `CONTENT_BEGIN_<nonce>` / `CONTENT_END_<nonce>`. The parser
accepts only the exact markers generated for that request and treats any
marker-looking text inside user content as content.
"""

import re

import pytest

from stet.llm.prompt_compiler import (
    ContainedContent,
    ContentMarkers,
    format_content,
    generate_nonce,
    parse_compiled_content,
    strip_markers,
)


def test_generate_nonce_is_128bit_hex():
    nonce = generate_nonce()
    assert len(nonce) == 32  # 128 bits = 16 bytes = 32 hex chars
    assert re.fullmatch(r"[0-9a-f]{32}", nonce)


def test_generate_nonce_is_random_per_call():
    assert generate_nonce() != generate_nonce()


def test_format_content_uses_request_nonce_in_markers():
    nonce = generate_nonce()
    wrapped = format_content("hello world", nonce)
    assert f"CONTENT_BEGIN_{nonce}" in wrapped
    assert f"CONTENT_END_{nonce}" in wrapped
    assert "hello world" in wrapped


def test_parse_compiled_content_roundtrips():
    nonce = generate_nonce()
    text = "The quick brown fox."
    wrapped = format_content(text, nonce)
    parsed = parse_compiled_content(wrapped, nonce)
    assert parsed == text


def test_parse_rejects_wrong_nonce():
    gen_nonce = generate_nonce()
    other_nonce = generate_nonce()
    wrapped = format_content("content", gen_nonce)
    with pytest.raises(ValueError):
        parse_compiled_content(wrapped, other_nonce)


def test_parse_treats_other_markers_inside_content_as_content():
    nonce = generate_nonce()
    # A marker-looking line inside user content must NOT be treated as a marker.
    text = "CONTENT_END_others\nstill content\nCONTENT_BEGIN_xxx"
    wrapped = format_content(text, nonce)
    parsed = parse_compiled_content(wrapped, nonce)
    assert "CONTENT_END_others" in parsed
    assert "CONTENT_BEGIN_xxx" in parsed


def test_strip_markers_removes_request_markers():
    nonce = generate_nonce()
    wrapped = format_content("text here", nonce)
    stripped = strip_markers(wrapped, nonce)
    assert "text here" in stripped
    assert "CONTENT_BEGIN" not in stripped
    assert "CONTENT_END" not in stripped


def test_parse_requires_closing_marker():
    nonce = generate_nonce()
    with pytest.raises(ValueError):
        parse_compiled_content(f"CONTENT_BEGIN_{nonce}\nunclosed", nonce)


def test_contained_content_dataclass_holds_text_and_markers():
    nonce = generate_nonce()
    markers = ContentMarkers(open_marker=f"CONTENT_BEGIN_{nonce}", close_marker=f"CONTENT_END_{nonce}")
    c = ContainedContent(text="abc", markers=markers)
    assert c.text == "abc"
    assert c.markers.open_marker.startswith("CONTENT_BEGIN_")
    assert c.markers.close_marker.startswith("CONTENT_END_")
