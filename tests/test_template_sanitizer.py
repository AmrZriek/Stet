"""Tests for stet.llm.template_sanitizer -- generic hard-prime detection and
sanitization of chat templates that unconditionally emit a think open-tag.

The six inline fixture templates cover: hard-prime in add_generation_prompt,
enable_thinking-gated think, generation/message.thinking-guarded emission,
tool_use-named gated variant, statically-false conditional, and think inside
a Jinja comment. Only the first must be detected and sanitized.
"""

import tempfile

import pytest

from stet.llm.template_sanitizer import (
    detect_hard_prime,
    sanitize_template,
    write_sanitized_template,
)

# (i) LFM-style: literal <think> inside the add_generation_prompt emission.
_HARD_PRIME = (
    "{%- if add_generation_prompt -%}\n"
    "    {{- \"<|im_start|>assistant\\n<think>\" -}}\n"
    "{%- endif -%}\n"
)

# (ii) enable_thinking-gated think (emits only when thinking is enabled).
_GATED = (
    "{%- if enable_thinking -%}\n"
    "    {{- \"<|im_start|>assistant\\n<think>\" -}}\n"
    "{%- endif -%}\n"
)

# (iii) generation-guarded emission + message.thinking handling.
_GENERATION_GUARDED = (
    "{%- if add_generation_prompt -%}\n"
    "    {{- generation -}}\n"
    "{%- endif -%}\n"
    "{%- for message in messages -%}\n"
    "    {%- if message.thinking -%}\n"
    "        {{- \"<think>\" + message.thinking + \"</think>\" -}}\n"
    "    {%- endif -%}\n"
    "{%- endfor -%}\n"
)

# (iv) tool_use-named variant whose think is still gated.
_TOOL_USE_GATED = (
    "{%- if tools and add_generation_prompt -%}\n"
    "    {%- if enable_thinking -%}\n"
    "        {{- \"<think>\" -}}\n"
    "    {%- endif -%}\n"
    "{%- endif -%}\n"
)

# (v) condition that is statically false — the prime never emits.
_STATICALLY_FALSE = (
    "{%- if false -%}\n"
    "    {{- \"<think>\" -}}\n"
    "{%- endif -%}\n"
)

# (vi) think tag inside a Jinja comment.
_COMMENTED = (
    "{# <think> lives in a comment only #}\n"
    "{%- if add_generation_prompt -%}\n"
    "    {{- \"<|im_start|>assistant\\n\" -}}\n"
    "{%- endif -%}\n"
)


class TestDetectHardPrime:
    @pytest.mark.parametrize(
        "template",
        [_HARD_PRIME],
    )
    def test_detects_hard_prime(self, template):
        assert detect_hard_prime(template) is True

    @pytest.mark.parametrize(
        "template",
        [
            _GATED,
            _GENERATION_GUARDED,
            _TOOL_USE_GATED,
            _STATICALLY_FALSE,
            _COMMENTED,
        ],
        ids=[
            "enable_thinking_gated",
            "generation_guarded",
            "tool_use_gated",
            "statically_false",
            "jinja_comment",
        ],
    )
    def test_does_not_detect_gated_or_guarded(self, template):
        assert detect_hard_prime(template) is False

    def test_none_and_empty_are_conservative(self):
        assert detect_hard_prime(None) is False
        assert detect_hard_prime("") is False
        assert detect_hard_prime(12345) is False

    def test_malformed_template_is_conservative(self):
        # Unclosed {% block -> parse difficulty -> False (never raise)
        assert detect_hard_prime("{%- if add_generation_prompt -%}{{- \"x\" -}}") is False


class TestSanitizeTemplate:
    def test_strips_hard_prime_only(self):
        """Sanitizer edits ONLY the hard-primed template."""
        sanitized = sanitize_template(_HARD_PRIME)
        assert sanitized is not None
        assert "<think>" not in sanitized
        # Assistant header survives; prime removed from the emission — no
        # stray '>' from a partial <think> match.
        assert "<|im_start|>assistant" in sanitized
        assert "add_generation_prompt" in sanitized
        expected = (
            "{%- if add_generation_prompt -%}\n"
            "    {{- \"<|im_start|>assistant\\n\" -}}\n"
            "{%- endif -%}\n"
        )
        assert sanitized == expected

    @pytest.mark.parametrize(
        "template",
        [
            _GATED,
            _GENERATION_GUARDED,
            _TOOL_USE_GATED,
            _STATICALLY_FALSE,
            _COMMENTED,
        ],
        ids=[
            "enable_thinking_gated",
            "generation_guarded",
            "tool_use_gated",
            "statically_false",
            "jinja_comment",
        ],
    )
    def test_returns_template_byte_identical(self, template):
        assert sanitize_template(template) == template

    def test_malformed_input_returns_none_never_raises(self):
        assert sanitize_template(None) is None
        assert sanitize_template(42) is None
        assert sanitize_template(["<think>"]) is None

    def test_malformed_template_returns_none_never_raises(self):
        # Unclosed quote / block -> conservative None, no exception.
        assert sanitize_template("{{- \"<think>\" -}}") is not None
        assert sanitize_template("{%- if false -%}{{- \"<think>\" -}}") is None


class TestWriteSanitizedTemplate:
    def test_creates_file_under_tempdir(self, tmp_path, monkeypatch):
        monkeypatch.setattr(tempfile, "gettempdir", lambda: str(tmp_path))
        out = write_sanitized_template("{{- 'x' -}}", r"C:\models\AnyModel.gguf")

        assert out.parent == tmp_path / "stet_templates"
        assert out.exists()
        assert out.suffix == ".jinja"
        assert len(out.stem) == 16  # sha256 prefix
        assert out.read_text(encoding="utf-8") == "{{- 'x' -}}"

    def test_deterministic_path_for_same_input(self, tmp_path, monkeypatch):
        monkeypatch.setattr(tempfile, "gettempdir", lambda: str(tmp_path))
        first = write_sanitized_template("tpl", "model.gguf")
        second = write_sanitized_template("tpl", "model.gguf")
        assert first == second

    def test_distinct_path_for_different_template(self, tmp_path, monkeypatch):
        monkeypatch.setattr(tempfile, "gettempdir", lambda: str(tmp_path))
        a = write_sanitized_template("tpl-a", "model.gguf")
        b = write_sanitized_template("tpl-b", "model.gguf")
        assert a != b
