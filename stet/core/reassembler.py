"""OffsetReassembler (Phase 3d).

Reassembles windowed correction outputs using character-offset ownership
intervals [start_anchor, end_anchor), discards context overlap seams,
restores immutable protected atoms, and verifies seam consistency.
"""

from __future__ import annotations

import difflib
from typing import List, Sequence, Tuple

from stet.core.context_planner import ContextPlan, PlanRegime
from stet.core.document_protector import ProtectedDocument


class OffsetReassembler:
    """Reassembles generated outputs across windows into a cohesive final document."""

    @classmethod
    def reassemble(
        cls,
        plan: ContextPlan,
        window_outputs: Sequence[str],
        protected_doc: ProtectedDocument,
    ) -> Tuple[str, bool]:
        """Reassemble window outputs and restore protected atoms.

        Returns (final_text, all_atoms_preserved).
        """
        if not window_outputs:
            return protected_doc.original_text, True

        # 1. Monolithic regime: single window directly replaces document
        if plan.regime == PlanRegime.MONOLITHIC or len(plan.windows) == 1:
            raw_assembled = window_outputs[0]
            return protected_doc.restore(raw_assembled)

        # 2. Windowed regime: splice owning spans
        assembled_chunks: List[str] = []
        original_masked = protected_doc.masked_text

        for window, out_text in zip(plan.windows, window_outputs):
            owning_start, owning_end = window.owning_span
            ctx_start, ctx_end = window.context_span

            # If window has no context overlap (monolithic chunk), take directly
            if (owning_start, owning_end) == (ctx_start, ctx_end):
                assembled_chunks.append(out_text)
                continue

            # Context overlap handling: extract owning portion from out_text
            extracted = cls._extract_owning_portion(
                context_input=window.context_text,
                context_output=out_text,
                owning_input=original_masked[owning_start:owning_end],
            )
            assembled_chunks.append(extracted)

        # Join assembled chunks, preserving original inter-window whitespace separators (newlines, indentation)
        if not assembled_chunks:
            return protected_doc.restore("")

        result_parts = [assembled_chunks[0]]
        for i in range(1, len(assembled_chunks)):
            prev_end = plan.windows[i - 1].owning_span[1]
            curr_start = plan.windows[i].owning_span[0]
            sep = original_masked[prev_end:curr_start] if curr_start >= prev_end else ""
            if sep:
                result_parts.append(sep)
            else:
                # Owning spans are contiguous (separator whitespace lives at the
                # end of the previous sentence span). Don't inject a space if
                # either side already has whitespace — avoids doubling.
                prev_ends_ws = bool(result_parts[-1][-1:].isspace()) if result_parts[-1] else True
                next_starts_ws = bool(assembled_chunks[i][:1].isspace()) if assembled_chunks[i] else True
                if not prev_ends_ws and not next_starts_ws:
                    result_parts.append(" ")
            result_parts.append(assembled_chunks[i])
        assembled_masked = "".join(result_parts)
        return protected_doc.restore(assembled_masked)

    @classmethod
    def _extract_owning_portion(
        cls,
        context_input: str,
        context_output: str,
        owning_input: str,
    ) -> str:
        """Extract the output segment that corresponds to the owning input."""
        if not context_output:
            return owning_input

        # If context input equals owning input, output is entirely owned
        if context_input.strip() == owning_input.strip():
            return context_output

        # Match context_input and context_output via sequence matcher
        matcher = difflib.SequenceMatcher(None, context_input, context_output)
        # Find where owning_input sits inside context_input
        own_pos = context_input.find(owning_input)
        if own_pos == -1:
            # Fallback: if owning input cannot be located exactly in context, return context output
            return context_output

        own_end = own_pos + len(owning_input)

        # Map character offsets from context_input to context_output
        out_start: int | None = None
        out_end: int | None = None

        for tag, i1, i2, j1, j2 in matcher.get_opcodes():
            if out_start is None and i2 >= own_pos:
                out_start = j1 + max(0, own_pos - i1)
            if i2 >= own_end and out_end is None:
                out_end = j1 + min(j2 - j1, own_end - i1)

        if out_start is not None and out_end is not None and out_end > out_start:
            return context_output[out_start:out_end]

        return context_output
