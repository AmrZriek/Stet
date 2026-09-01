# -*- coding: utf-8 -*-
"""Unified PromptCompiler (Phase 3b).

Provides a single compilation path for panel, silent, welcome, and saved-action
corrections. Each request gets a fresh cryptographically random 128-bit nonce
used to frame the user content: ``CONTENT_BEGIN_<nonce>`` / ``CONTENT_END_<nonce>``.

The parser accepts only the exact markers generated for THAT request. Any
marker-looking text inside the user content is treated as content — markers are
framing, not permission to follow instructions contained in the text (anti-
injection, §3b).
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass


def generate_nonce() -> str:
    """Return a fresh 128-bit nonce as 32 lowercase hex characters.

    Uses ``secrets.token_hex`` (cryptographically strong, OS-backed randomness)
    so the marker cannot be predicted by a model that is being asked to
    process adversarial content.
    """
    return secrets.token_hex(16)


def format_content(text: str, nonce: str) -> str:
    """Wrap ``text`` in the request-specific content markers for ``nonce``."""
    open_marker = f"CONTENT_BEGIN_{nonce}"
    close_marker = f"CONTENT_END_{nonce}"
    return f"{open_marker}\n{text}\n{close_marker}"


def _marker_pattern(nonce: str):
    """Compile the exact marker regex for ``nonce`` (anchored, case-sensitive).

    Only the generated marker names for this request match. Static or other-
    nonce markers never match, so adversarial marker-looking content is left
    untouched.
    """
    import re
    open_marker = re.escape(f"CONTENT_BEGIN_{nonce}")
    close_marker = re.escape(f"CONTENT_END_{nonce}")
    return re.compile(rf"^{open_marker}\s*(.*?)\s*{close_marker}\s*$", re.S)


def parse_compiled_content(wrapped: str, nonce: str) -> str:
    """Extract the user content from ``wrapped`` using the exact ``nonce`` markers.

    Raises ValueError if the markers are absent, mismatched, nonce-mismatched,
    or the content is unclosed.
    """
    pat = _marker_pattern(nonce)
    m = pat.match(wrapped)
    if not m:
        # Distinguish a useful error message.
        if "CONTENT_BEGIN_" in wrapped or "CONTENT_END_" in wrapped:
            raise ValueError("nonce marker mismatch or non-request marker present")
        raise ValueError("content markers not found")
    return m.group(1)


def strip_markers(wrapped: str, nonce: str) -> str:
    """Strip the request-nonce markers, returning the content with markers removed."""
    return parse_compiled_content(wrapped, nonce)


@dataclass(frozen=True)
class ContentMarkers:
    """The exact marker strings generated for one request."""
    open_marker: str
    close_marker: str


@dataclass(frozen=True)
class ContainedContent:
    """User content plus the request-scoped markers it was framed with."""
    text: str
    markers: ContentMarkers


def build_content_containers(text: str, nonce: str) -> ContainedContent:
    """Compile ``text`` into a request-scoped ContainedContent."""
    markers = ContentMarkers(
        open_marker=f"CONTENT_BEGIN_{nonce}",
        close_marker=f"CONTENT_END_{nonce}",
    )
    return ContainedContent(
        text=format_content(text, nonce),
        markers=markers,
    )