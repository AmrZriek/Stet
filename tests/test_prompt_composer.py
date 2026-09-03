# -*- coding: utf-8 -*-
"""Tests for the engine prompt compilers (§3b integration seam).

Provides a thin, additive entry point the UI/engine can call for the panel /
welcome / saved-action correction paths (where per-request nonce markers
belong), WITHOUT touching the static-marker streaming path. The function
delegates to the unified PromptCompiler.
"""

from stet.llm.prompt_composer import (
    compile_panel_prompt,
    compile_saved_action_prompt,
    compile_welcome_prompt,
)


def test_panel_prompt_uses_request_nonce():
    prompt = compile_panel_prompt("correct this: hello world")
    assert "CONTENT_BEGIN_" in prompt
    assert "CONTENT_END_" in prompt
    # The actual user content is present, framed.
    assert "hello world" in prompt


def test_panel_prompt_content_is_framed_between_request_nonce():
    prompt = compile_panel_prompt("fix me please")
    import re
    m = re.search(r"CONTENT_BEGIN_([0-9a-f]{32})\n(.*?)\nCONTENT_END_\1", prompt, re.S)
    assert m is not None
    assert m.group(2) == "fix me please"


def test_two_panel_prompts_have_distinct_nonces():
    p1 = compile_panel_prompt("same content")
    p2 = compile_panel_prompt("same content")
    assert p1 != p2


def test_saved_action_prompt_frames_content():
    prompt = compile_saved_action_prompt("apply template", "the body text")
    assert "the body text" in prompt
    assert "CONTENT_BEGIN_" in prompt


def test_welcome_prompt_frames_content():
    prompt = compile_welcome_prompt("sample text for welcome")
    assert "sample text for welcome" in prompt
    assert "CONTENT_BEGIN_" in prompt
