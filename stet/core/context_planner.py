"""Token-Budget-Aware ContextPlanner (Phase 3a).

Implements closed budget arithmetic:
    I(x) + G(I(x)) + S <= n_ctx_slot
where:
    I(x) is compiled prompt tokens,
    G(i) = clamp(ceil(1.25 * i + overhead), G_min, G_max) is generation reservation,
    S is safety margin (default 256).

Produces Monolithic, Windowed (with 2-sentence context overlap and disjoint
character-offset ownership intervals), or Pre-Pass plans.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from enum import Enum
from typing import Callable, List, Optional, Tuple

from stet.core.engine_types import BudgetPolicy
from stet.core.prompt_compiler import CompiledPrompt, PromptCompiler

_SENTENCE_END_RE = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"'(\[])")


class PlanRegime(str, Enum):
    MONOLITHIC = "monolithic"
    WINDOWED = "windowed"
    PRE_PASS = "pre_pass"


@dataclass(frozen=True, slots=True)
class WindowSpec:
    """Specification for one windowed chunk execution."""

    index: int
    owning_span: Tuple[int, int]  # Disjoint [start, end) character interval in full text
    context_span: Tuple[int, int]  # [start, end) in full text including overlap
    context_text: str
    compiled_prompt: CompiledPrompt
    max_tokens: int


@dataclass(frozen=True, slots=True)
class ContextPlan:
    """Complete execution plan for a document under the active token budget."""

    regime: PlanRegime
    windows: Tuple[WindowSpec, ...]
    n_ctx_slot: int
    budget_policy: BudgetPolicy
    total_estimated_tokens: int


def estimate_tokens(text: str) -> int:
    """Conservative token count estimation (approx 3.5 chars per token + overhead)."""
    if not text:
        return 0
    return max(1, math.ceil(len(text) / 3.5) + 4)


def calculate_generation_reservation(
    prompt_tokens: int,
    policy: BudgetPolicy,
    overhead: int = 32,
) -> int:
    """G(i) = clamp(ceil(growth_multiplier * i + overhead), min_tokens, max_tokens)."""
    raw = math.ceil(policy.growth_multiplier * prompt_tokens + overhead)
    return max(policy.min_generation_tokens, min(policy.max_generation_tokens, raw))


class ContextPlanner:
    """Plans document execution into monolithic or windowed regimes based on token budget."""

    @classmethod
    def plan(
        cls,
        text: str,
        mode_index: int = 0,
        user_instruction: str = "",
        budget_policy: Optional[BudgetPolicy] = None,
        tokenizer: Optional[Callable[[str], int]] = None,
    ) -> ContextPlan:
        """Create a ContextPlan for text under the given budget policy."""
        if budget_policy is None:
            budget_policy = BudgetPolicy()

        n_ctx_slot = budget_policy.context_size
        count_tokens = tokenizer if tokenizer is not None else estimate_tokens

        # 1. Test Monolithic Pass with exact compiled prompt
        compiled_full = PromptCompiler.compile(
            content=text,
            mode_index=mode_index,
            user_instruction=user_instruction,
        )
        prompt_tokens = count_tokens(compiled_full.user_prompt)
        gen_tokens = calculate_generation_reservation(prompt_tokens, budget_policy)

        total_needed = prompt_tokens + gen_tokens + budget_policy.safety_margin

        if total_needed <= n_ctx_slot:
            single_window = WindowSpec(
                index=0,
                owning_span=(0, len(text)),
                context_span=(0, len(text)),
                context_text=text,
                compiled_prompt=compiled_full,
                max_tokens=gen_tokens,
            )
            return ContextPlan(
                regime=PlanRegime.MONOLITHIC,
                windows=(single_window,),
                n_ctx_slot=n_ctx_slot,
                budget_policy=budget_policy,
                total_estimated_tokens=total_needed,
            )

        # 2. Windowed Pass: partition text into sentence units
        sentences, sentence_spans = cls._split_sentences(text)
        if not sentences:
            # Empty or single whitespace block
            single_window = WindowSpec(
                index=0,
                owning_span=(0, len(text)),
                context_span=(0, len(text)),
                context_text=text,
                compiled_prompt=compiled_full,
                max_tokens=gen_tokens,
            )
            return ContextPlan(
                regime=PlanRegime.MONOLITHIC,
                windows=(single_window,),
                n_ctx_slot=n_ctx_slot,
                budget_policy=budget_policy,
                total_estimated_tokens=total_needed,
            )

        # Group sentences into owning chunks that fit the budget
        # Target ~400 tokens per owning chunk to leave room for overlap and generation
        target_chunk_tokens = max(100, (n_ctx_slot - budget_policy.safety_margin) // 4)

        chunks_sentence_indices: List[Tuple[int, int]] = []  # [start_sent, end_sent)
        current_start = 0
        current_tokens = 0

        for i, sent in enumerate(sentences):
            sent_tokens = count_tokens(sent)
            if current_tokens + sent_tokens > target_chunk_tokens and i > current_start:
                chunks_sentence_indices.append((current_start, i))
                current_start = i
                current_tokens = sent_tokens
            else:
                current_tokens += sent_tokens

        if current_start < len(sentences):
            chunks_sentence_indices.append((current_start, len(sentences)))

        windows: List[WindowSpec] = []
        for win_idx, (start_sent, end_sent) in enumerate(chunks_sentence_indices):
            # Owning span: exactly covers sentences from start_sent to end_sent
            owning_start = sentence_spans[start_sent][0]
            owning_end = sentence_spans[end_sent - 1][1]

            # Context span: 2 sentences before and 2 sentences after for overlap
            ctx_start_sent = max(0, start_sent - 2)
            ctx_end_sent = min(len(sentences), end_sent + 2)

            ctx_start = sentence_spans[ctx_start_sent][0]
            ctx_end = sentence_spans[ctx_end_sent - 1][1]

            ctx_text = text[ctx_start:ctx_end]
            compiled_window = PromptCompiler.compile(
                content=ctx_text,
                mode_index=mode_index,
                user_instruction=user_instruction,
            )
            win_prompt_tokens = count_tokens(compiled_window.user_prompt)
            win_gen_tokens = calculate_generation_reservation(win_prompt_tokens, budget_policy)

            windows.append(
                WindowSpec(
                    index=win_idx,
                    owning_span=(owning_start, owning_end),
                    context_span=(ctx_start, ctx_end),
                    context_text=ctx_text,
                    compiled_prompt=compiled_window,
                    max_tokens=win_gen_tokens,
                )
            )

        regime = PlanRegime.PRE_PASS if len(windows) > 8 else PlanRegime.WINDOWED

        return ContextPlan(
            regime=regime,
            windows=tuple(windows),
            n_ctx_slot=n_ctx_slot,
            budget_policy=budget_policy,
            total_estimated_tokens=sum(w.max_tokens for w in windows),
        )

    @staticmethod
    def _split_sentences(text: str) -> Tuple[List[str], List[Tuple[int, int]]]:
        """Split text into sentences while tracking exact character spans continuously."""
        if not text:
            return [], []

        spans: List[Tuple[int, int]] = []
        sentences: List[str] = []
        cursor = 0

        for match in _SENTENCE_END_RE.finditer(text):
            end = match.end()
            if end > cursor:
                sentences.append(text[cursor:end])
                spans.append((cursor, end))
            cursor = end

        if cursor < len(text):
            sentences.append(text[cursor:])
            spans.append((cursor, len(text)))

        return sentences, spans
