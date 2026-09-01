# -*- coding: utf-8 -*-
"""Engine prompt composers (§3b integration seam).

Thin, additive entry points the UI/engine can call for the panel / welcome /
saved-action correction paths. Each compiles a request-scoped prompt using the
unified PromptCompiler's fresh 128-bit nonce markers, so the content is framed
with an unpredictable, per-request delimiter. These do NOT touch the
static-marker streaming/patch path (_build_correction_messages), which is
deliberately left unchanged.
"""

from __future__ import annotations

from stet.llm.prompt_compiler import format_content, generate_nonce


def _compile(content: str) -> str:
    """Frame content with a fresh request nonce via the unified compiler."""
    return format_content(content, generate_nonce())


def compile_panel_prompt(content: str) -> str:
    """Compile the chat-panel correction prompt with request-scoped markers."""
    return _compile(content)


def compile_saved_action_prompt(action_label: str, content: str) -> str:
    """Compile a saved-action prompt: the action label plus the framed content."""
    body = f"[{action_label}]\n{content}"
    return _compile(body)


def compile_welcome_prompt(content: str) -> str:
    """Compile the welcome/first-run correction prompt."""
    return _compile(content)
