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
from stet.llm.validator import DocumentValidator, UnitValidator
from stet.llm.window_splitter import WindowSplitter


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

        # 2. Decide monolithic vs windowed via the closed budget.
        tokens = self._estimate_tokens(protected)
        usable = self._usable_content_budget()
        if tokens <= usable:
            return self._correct_monolithic(protected, table, text)
        return self._correct_windowed(protected, table, text)

    def _estimate_tokens(self, text: str) -> int:
        # Cheap token proxy for the windowed decision (word + punctuation).
        if not text:
            return 0
        return max(1, len(text.split()) + text.count("."))

    def _usable_content_budget(self) -> int:
        from stet.llm.context_planner import solve_closed_budget
        return solve_closed_budget(
            self._config.n_ctx, self._config.reserve, self._config.safety_margin
        )

    def _correct_monolithic(self, protected: str, table, original: str) -> SessionResult:
        # Single request over the whole protected document.
        nonce = generate_nonce()
        from stet.llm.prompt_compiler import format_content
        prompt = format_content(protected, nonce)
        raw = self._client.complete(prompt)
        units = [
            WindowResult(start=0, end=len(protected), text=raw, truncated=False),
        ]
        try:
            reassembled = Reassembler(units, original=protected).run()
        except ReassemblyError as e:
            return SessionResult(text=original, success=False, reason=str(e))
        check = self._validator.check(protected, reassembled.text)
        if not check.ok:
            return SessionResult(text=original, success=False, reason=check.reason)
        final = restore_atoms(reassembled.text, table)
        return SessionResult(text=final, success=True)

    def _correct_windowed(self, protected: str, table, original: str) -> SessionResult:
        # Split into windows, correct each, then reassemble by owning offsets.
        splitter = WindowSplitter()
        windows = splitter.split(protected, max_sentences=2)
        if not windows:
            return SessionResult(text=original, success=False, reason="no windows")
        pieces = []
        for w in windows:
            nonce = generate_nonce()
            from stet.llm.prompt_compiler import format_content
            prompt = format_content(w.owning_text, nonce)
            raw = self._client.complete(prompt)
            # A degenerate model may return empty; mark truncated to abort.
            if len(raw) < min(1, len(w.owning_text)):
                return SessionResult(text=original, success=False, reason="window truncated")
            pieces.append(
                WindowResult(
                    start=w.start_anchor,
                    end=w.end_anchor,
                    text=raw,
                    truncated=False,
                )
            )
        try:
            reassembled = Reassembler(pieces, original=protected).run()
        except ReassemblyError as e:
            return SessionResult(text=original, success=False, reason=str(e))
        dv = DocumentValidator()
        dr = dv.validate([w.owning_text for w in windows], reassembled.text)
        if not dr.is_valid:
            return SessionResult(text=original, success=False, reason=dr.reason)
        final = restore_atoms(reassembled.text, table)
        return SessionResult(text=final, success=True)