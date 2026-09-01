# -*- coding: utf-8 -*-
"""EngineSession (Phase 3 orchestration glue).

Composes PromptCompiler + DocumentProtector + ContextPlanner + WindowSplitter +
Reassembler + Validator into a coherent correction flow. The model client is
abstracted out (a small Protocol), so the whole pipeline runs in tests without a
live LLM. This is NOT yet wired into production model_manager.py: it is the
testable core the production engine will delegate to at the v1.6.0 checkpoint.
"""

from __future__ import annotations

from dataclasses import dataclass

from stet.llm.context_planner import ContextPlanner, ReservePolicy
from stet.llm.document_protector import extract_atoms_from_text, restore_atoms
from stet.llm.prompt_compiler import generate_nonce
from stet.llm.reassembler import ReassemblyError, Reassembler, WindowResult
from stet.llm.validator import UnitValidator


@dataclass(frozen=True)
class EngineConfig:
    n_ctx: int = 4096
    safety_margin: int = 16
    reserve: ReservePolicy = ReservePolicy()


@dataclass(frozen=True)
class SessionResult:
    text: str
    success: bool
    reason: str = ""


class FakeModelClient:
    """A thin adapter over a callable correction function."""

    def __init__(self, fn):
        self._fn = fn

    def complete(self, prompt: str) -> str:
        return self._fn(prompt)


class EngineSession:
    """Runs a correction through the Phase 3 module pipeline."""

    def __init__(self, client, config: EngineConfig | None = None):
        self._client = client
        self._config = config or EngineConfig()
        self._validator = UnitValidator()

    def correct(self, text: str) -> SessionResult:
        if not text.strip():
            return SessionResult(text=text, success=True)

        # 1. Protect atoms so URLs/emails/paths are never mangled.
        table = extract_atoms_from_text(text)
        protected = table.replace_with_placeholders()

        # 2. Compile a request-scoped prompt with a fresh nonce.
        nonce = generate_nonce()
        from stet.llm.prompt_compiler import format_content
        prompt = format_content(protected, nonce)

        # 3. Query the (fake) model.
        raw = self._client.complete(prompt)

        # 4. Reassemble / validate. A truncated (too-short) output is a failure.
        units = [
            WindowResult(start=0, end=len(protected), text=raw, truncated=False),
        ]
        try:
            reassembled = Reassembler(units, original=protected).run()
        except ReassemblyError as e:
            return SessionResult(text=text, success=False, reason=str(e))

        # 5. Validate: the output must not prompt-inject and must be plausible.
        check = self._validator.check(protected, reassembled.text)
        if not check.ok:
            return SessionResult(text=text, success=False, reason=check.reason)

        # 6. Restore any protected atoms the model may have dropped.
        final = restore_atoms(reassembled.text, table)
        return SessionResult(text=final, success=True)