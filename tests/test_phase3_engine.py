"""Comprehensive Unit & Integration Test Suite for Phase 3 Correction Engine."""


from stet.core.context_planner import ContextPlan, ContextPlanner, PlanRegime
from stet.core.document_protector import DocumentProtector
from stet.core.engine import CorrectionEngineImpl
from stet.core.engine_types import (
    BudgetPolicy,
    CorrectionRequest,
    TaskSpec,
    TaskType,
)
from stet.core.prompt_compiler import PromptCompiler
from stet.core.reassembler import OffsetReassembler
from stet.core.validators import UnitValidator


class TestDocumentProtector:
    """Test entity masking, atom table creation, and lossless restoration."""

    def test_protects_urls_paths_and_code(self):
        text = (
            "Visit https://github.com/stet-ai/stet and open C:\\Projects\\Stet\\main.py. "
            "Run `cargo test` or check this code:\n```rust\nfn main() {}\n```"
        )
        doc = DocumentProtector.protect(text)
        assert doc.has_protected_atoms is True
        assert len(doc.atoms) == 4
        assert "[REF1]" in doc.masked_text
        assert "[REF2]" in doc.masked_text
        assert "[REF3]" in doc.masked_text
        assert "[REF4]" in doc.masked_text
        assert "https://github.com/stet-ai/stet" not in doc.masked_text

        # Lossless restoration
        restored, all_preserved = doc.restore(doc.masked_text)
        assert all_preserved is True
        assert restored == text

    def test_protects_user_terms(self):
        text = "Contact Alice at alice@example.com regarding AcmeCorp."
        doc = DocumentProtector.protect(text, user_terms=["AcmeCorp"])
        assert "[REF1]" in doc.masked_text  # email
        assert "[REF2]" in doc.masked_text  # AcmeCorp
        restored, all_preserved = doc.restore(doc.masked_text)
        assert all_preserved is True
        assert restored == text

    def test_handles_empty_or_plain_text(self):
        doc = DocumentProtector.protect("")
        assert doc.masked_text == ""
        assert len(doc.atoms) == 0

        doc_plain = DocumentProtector.protect("Simple sentence without hazards.")
        assert doc_plain.masked_text == "Simple sentence without hazards."
        assert len(doc_plain.atoms) == 0


class TestPromptCompiler:
    """Test cryptographic nonce generation, message formatting, and extraction."""

    def test_nonce_markers_generation(self):
        compiled = PromptCompiler.compile("Test input text.", mode_index=0)
        assert len(compiled.nonce) == 32  # 128-bit hex
        assert compiled.begin_marker == f"CONTENT_BEGIN_{compiled.nonce}"
        assert compiled.end_marker == f"CONTENT_END_{compiled.nonce}"
        assert compiled.begin_marker in compiled.user_prompt
        assert compiled.end_marker in compiled.user_prompt

    def test_extract_content_between_markers(self):
        compiled = PromptCompiler.compile("Input text.", mode_index=0)
        model_output = (
            f"Here is the result:\n{compiled.begin_marker}\nCorrected text here.\n{compiled.end_marker}\nHope this helps!"
        )
        extracted, found = PromptCompiler.extract_content(model_output, compiled)
        assert found is True
        assert extracted == "Corrected text here."

    def test_extract_content_fallback_when_markers_omitted(self):
        compiled = PromptCompiler.compile("Input text.", mode_index=0)
        model_output = "Plain corrected output without markers."
        extracted, found = PromptCompiler.extract_content(model_output, compiled)
        assert found is False
        assert extracted == "Plain corrected output without markers."


class TestContextPlanner:
    """Test closed budget arithmetic and monolithic vs windowed planning."""

    def test_monolithic_regime_for_short_text(self):
        text = "This is a short sentence that easily fits in monolithic context."
        policy = BudgetPolicy(context_size=4096, safety_margin=256)
        plan = ContextPlanner.plan(text, mode_index=0, budget_policy=policy)
        assert plan.regime == PlanRegime.MONOLITHIC
        assert len(plan.windows) == 1
        assert plan.windows[0].owning_span == (0, len(text))
        assert plan.windows[0].context_span == (0, len(text))

    def test_windowed_regime_for_long_text(self):
        # Create a document of many sentences
        sentences = [f"This is sentence number {i} with some descriptive words." for i in range(1, 40)]
        text = " ".join(sentences)

        # Force small context budget to trigger windowing
        policy = BudgetPolicy(context_size=512, safety_margin=64, max_generation_tokens=256)
        plan = ContextPlanner.plan(text, mode_index=0, budget_policy=policy)
        assert plan.regime in (PlanRegime.WINDOWED, PlanRegime.PRE_PASS)
        assert len(plan.windows) > 1

        # Check disjoint ownership intervals
        last_end = 0
        for win in plan.windows:
            start, end = win.owning_span
            assert start == last_end
            assert end > start
            last_end = end
        assert last_end == len(text)


class TestValidators:
    """Test unit-level and document-level safety checks."""

    def test_unit_validator_detects_refusals(self):
        res = UnitValidator.validate("Input text", "I am sorry, but I cannot fulfill this request.")
        assert res.valid is False
        assert "refusal" in res.reason.lower()

    def test_unit_validator_detects_missing_markers(self):
        res = UnitValidator.validate("See [REF1] and [REF2]", "See [REF1] only.")
        assert res.valid is False
        assert "missing" in res.reason.lower()

    def test_unit_validator_detects_wild_divergence(self):
        res = UnitValidator.validate("Short sentence.", "This is an extremely long response that balloons the word count way beyond allowed limits.")
        assert res.valid is False
        assert "ratio" in res.reason.lower()

    def test_unit_validator_passes_valid_correction(self):
        res = UnitValidator.validate("Teh quick brown fox.", "The quick brown fox.")
        assert res.valid is True
        assert res.cleaned_text == "The quick brown fox."


class TestOffsetReassembler:
    """Test splicing window outputs and restoring protected atoms."""

    def test_monolithic_reassembly(self):
        doc = DocumentProtector.protect("Original https://example.com document.")
        plan = ContextPlan(
            regime=PlanRegime.MONOLITHIC,
            windows=(),
            n_ctx_slot=4096,
            budget_policy=BudgetPolicy(),
            total_estimated_tokens=50,
        )
        reassembled, preserved = OffsetReassembler.reassemble(
            plan=plan,
            window_outputs=["Corrected [REF1] document."],
            protected_doc=doc,
        )
        assert preserved is True
        assert reassembled == "Corrected https://example.com document."


class TestCorrectionEngineIntegration:
    """Test end-to-end execution of CorrectionEngineImpl with mock inference."""

    def test_engine_successful_correction(self):
        def mock_inference(messages, max_tokens):
            user_msg = messages[-1]["content"]
            begin_idx = user_msg.rfind("CONTENT_BEGIN_")
            end_idx = user_msg.rfind("CONTENT_END_")
            if begin_idx != -1 and end_idx != -1:
                begin_marker = user_msg[begin_idx : begin_idx + 46]
                end_marker = user_msg[end_idx : end_idx + 44]
                content = user_msg[begin_idx + 47 : end_idx].strip()
                corrected = content.replace("teh", "the")
                return f"{begin_marker}\n{corrected}\n{end_marker}", "stop", {"prompt_tokens": 30, "completion_tokens": 10}
            return "No change", "stop", {}

        engine = CorrectionEngineImpl(inference_provider=mock_inference)
        req = CorrectionRequest(
            text="This is teh first sentence with https://stet.ai.",
            task_spec=TaskSpec(task_type=TaskType.CORRECT),
        )

        res = engine.run(req)
        assert res.ok is True
        assert res.changed is True
        assert "This is the first sentence with https://stet.ai." in res.text
        assert res.token_usage["prompt_tokens"] > 0

    def test_engine_handles_context_length_truncation(self):
        def truncating_inference(messages, max_tokens):
            return "Partial...", "length", {"prompt_tokens": 100, "completion_tokens": 50}

        engine = CorrectionEngineImpl(inference_provider=truncating_inference)
        req = CorrectionRequest(text="Long text that gets cut off.", task_spec=TaskSpec())
        res = engine.run(req)
        assert res.status == "generation_truncated"
        assert res.finish_reason == "length"
        assert res.changed is False
        assert res.text == "Long text that gets cut off."
    def test_ensure_context_for_tokens_rule_and_threshold(self):
        from stet.llm.model_manager import ModelManager
        from unittest.mock import MagicMock
        cfg = MagicMock()
        cfg.get.side_effect = lambda k, d=None: 12800 if "context_size" in k else d
        mgr = ModelManager(cfg)
        mgr.actual_ctx_size = 12800
        # Under 40% (<= 5120 tokens): no reload
        assert mgr.ensure_context_for_tokens(1000) is True
        assert mgr.ensure_context_for_tokens(5120) is True
        assert getattr(mgr, "_dynamic_context_size", None) is None

        # Over 40% (> 5120 tokens): triggers expansion and reload
        mgr.is_loaded = MagicMock(return_value=True)
        mgr.unload_model = MagicMock()
        mgr.load_model = MagicMock(return_value=True)
        res = mgr.ensure_context_for_tokens(6000)
        assert res is True
        assert mgr._dynamic_context_size > 12800
        mgr.unload_model.assert_called_once()
        mgr.load_model.assert_called_once()
