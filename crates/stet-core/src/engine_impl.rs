//! Concrete correction engine (Phase 1c contract, made real).
//!
//! The Phase 1c trait `CorrectionEngine::run` is the single entry point. This module
//! provides a deterministic implementation that computes a CorrectionResult from a
//! request, using a pluggable size/budget check (so it is testable without an LLM).
//! A `finish_reason == "length"` result is never pasteable.

use crate::engine::{CorrectionRequest, CorrectionResult};
use crate::index_types::InputCode;

/// A deterministic engine that returns a session result from a request.
/// The real LLM-backed implementation is Phase 3 (Python) / Phase 2 (Rust glue);
/// this concrete contract implementation makes the trait usable and testable.
pub struct SimpleEngine {
    /// Output generator: maps input text to corrected text (injected in tests).
    pub generate: Box<dyn Fn(&str) -> String + Send + Sync>,
    /// Whether generation reported a truncation (finish_reason == "length").
    pub truncated: bool,
}

impl SimpleEngine {
    pub fn new(generate: Box<dyn Fn(&str) -> String + Send + Sync>) -> Self {
        SimpleEngine { generate, truncated: false }
    }

    pub fn maybe_truncate(mut self, truncated: bool) -> Self {
        self.truncated = truncated;
        self
    }
}

impl crate::engine::CorrectionEngine for SimpleEngine {
    fn run(&self, request: &CorrectionRequest) -> CorrectionResult {
        let corrected = (self.generate)(&request.selected_text);
        let finish = if self.truncated { Some("length".to_string()) } else { None };
        let code = if self.truncated { InputCode::GenerationTruncated } else { InputCode::Ok };
        CorrectionResult {
            code,
            corrected_text: if self.truncated { None } else { Some(corrected) },
            transaction_id: request.transaction_id.clone(),
            finish_reason: finish,
            message: if self.truncated { "generation truncated".into() } else { "ok".into() },
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::engine::{CorrectionEngine, TaskSpec, TaskType};

    fn req(text: &str) -> CorrectionRequest {
        CorrectionRequest {
            task: TaskSpec {
                task_type: TaskType::Correct,
                newline_policy: "target_default".into(),
                budget_policy: "balanced".into(),
                guard_set: vec![],
            },
            selected_text: text.into(),
            transaction_id: "txn-1".into(),
        }
    }

    #[test]
    fn engine_produces_pasteable_result_on_success() {
        let eng = SimpleEngine::new(Box::new(|s| format!("[fixed] {s}")));
        let r = eng.run(&req("hello"));
        assert!(r.is_pasteable());
        assert_eq!(r.corrected_text.as_deref(), Some("[fixed] hello"));
    }

    #[test]
    fn engine_truncated_result_is_never_pasteable() {
        let eng = SimpleEngine::new(Box::new(|s| s.to_string())).maybe_truncate(true);
        let r = eng.run(&req("hello"));
        assert!(!r.is_pasteable());
        assert_eq!(r.finish_reason.as_deref(), Some("length"));
    }
}