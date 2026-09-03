"""Unified CorrectionEngine Implementation (Phase 3).

Orchestrates the entire Phase 3 pipeline:
DocumentProtector -> ContextPlanner -> PromptCompiler -> LLM Execution ->
UnitValidator -> OffsetReassembler -> DocumentValidator -> CorrectionResult.
"""

from __future__ import annotations

from typing import Callable, Dict, List, Optional, Tuple

from stet.core.context_planner import ContextPlan, ContextPlanner
from stet.core.document_protector import DocumentProtector, ProtectedDocument
from stet.core.engine_types import (
    CorrectionEngine,
    CorrectionRequest,
    CorrectionResult,
)
from stet.core.prompt_compiler import PromptCompiler
from stet.core.reassembler import OffsetReassembler
from stet.core.validators import DocumentValidator, UnitValidator

# Type for an LLM generator: takes messages and max_tokens, returns (output_text, finish_reason, token_usage)
InferenceCallable = Callable[[List[Dict[str, str]], int], Tuple[str, str, Dict[str, int]]]


class CorrectionEngineImpl(CorrectionEngine):
    """Production implementation of the Phase 3 Unified Correction Engine."""

    def __init__(
        self,
        inference_provider: Optional[InferenceCallable] = None,
        tokenizer: Optional[Callable[[str], int]] = None,
    ):
        self.inference_provider = inference_provider
        self.tokenizer = tokenizer

    def run(
        self,
        request: CorrectionRequest,
        inference_override: Optional[InferenceCallable] = None,
    ) -> CorrectionResult:
        """Execute a correction or rewrite request through the complete Phase 3 pipeline."""
        original_text = request.text
        if not original_text or not original_text.strip():
            return CorrectionResult(
                text=original_text,
                changed=False,
                status="success",
                finish_reason="stop",
            )

        spec = request.task_spec
        provider = inference_override or self.inference_provider
        if provider is None:
            return CorrectionResult(
                text=original_text,
                changed=False,
                status="error",
                message="No LLM inference provider configured",
            )

        # 1. Protect sensitive entities into immutable atom table
        protected_doc: ProtectedDocument = DocumentProtector.protect(
            text=original_text,
            protect_code_blocks=spec.guard_set.preserve_code_blocks,
        )

        # 2. Token-budget-aware context planning
        plan: ContextPlan = ContextPlanner.plan(
            text=protected_doc.masked_text,
            mode_index=spec.mode_index,
            user_instruction=spec.user_instruction,
            budget_policy=spec.budget_policy,
            tokenizer=self.tokenizer,
        )

        # 3. Execute plan windows
        window_outputs: List[str] = []
        total_tokens = {"prompt_tokens": 0, "completion_tokens": 0}
        last_finish_reason = "stop"

        for window in plan.windows:
            raw_output, finish_reason, usage = provider(
                window.compiled_prompt.messages,
                window.max_tokens,
            )
            last_finish_reason = finish_reason

            for k, v in (usage or {}).items():
                total_tokens[k] = total_tokens.get(k, 0) + v

            if finish_reason == "length":
                # Generation truncated by context limit
                return CorrectionResult(
                    text=original_text,
                    changed=False,
                    status="generation_truncated",
                    finish_reason="length",
                    token_usage=total_tokens,
                    message="Model generation hit context token limit",
                )

            # Extract output between cryptographic nonces
            extracted, markers_found = PromptCompiler.extract_content(
                raw_output,
                window.compiled_prompt,
            )

            if not markers_found:
                # Truncated or preamble/injection outside markers — never
                # reassemble untrusted text. Retain original for this unit.
                window_outputs.append(window.context_text)
                continue

            # Validate unit
            val = UnitValidator.validate(
                input_text=window.context_text,
                output_text=extracted,
                guard_set=spec.guard_set,
                mode_index=spec.mode_index,
            )

            if val.valid:
                window_outputs.append(val.cleaned_text)
            else:
                # Safe fallback: retain original context text for this failed unit
                window_outputs.append(window.context_text)

        # 4. Reassemble output across windows and restore protected atoms
        assembled_text, all_atoms_preserved = OffsetReassembler.reassemble(
            plan=plan,
            window_outputs=window_outputs,
            protected_doc=protected_doc,
        )

        if not all_atoms_preserved:
            # A protected atom (URL/code/email) was dropped by the model.
            # Never silently delete user data — fall back to original.
            return CorrectionResult(
                text=original_text,
                changed=False,
                status="atoms_dropped",
                finish_reason=last_finish_reason,
                token_usage=total_tokens,
                message="Protected content dropped by model; retained original",
            )

        # 5. Whole-document validation (post-restore: no stray [REFn]
        # unless the user typed it literally).
        doc_val = DocumentValidator.validate(
            original_doc=original_text,
            assembled_doc=assembled_text,
            guard_set=spec.guard_set,
        )

        final_text = doc_val.cleaned_text if doc_val.valid else original_text
        changed = final_text != original_text

        return CorrectionResult(
            text=final_text,
            changed=changed,
            status="success" if doc_val.valid else "aborted_guard",
            finish_reason=last_finish_reason,
            token_usage=total_tokens,
            message=doc_val.reason,
        )
