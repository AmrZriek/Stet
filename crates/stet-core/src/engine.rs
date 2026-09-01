//! Correction engine protocol (Phase 1c).
//!
//! `TaskSpec` + single entry point `CorrectionEngine::run(request) -> CorrectionResult`.
//! A `finish_reason == "length"` result is `GENERATION_TRUNCATED`, never a pasteable
//! success. Pure protocol types; no engine implementation yet.

use crate::index_types::InputCode;
use serde::{Deserialize, Serialize};

/// Task type (Phase 1c).
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum TaskType {
    Correct,
    Rewrite,
    SavedAction,
}

/// TaskSpec: the resolved per-correction contract (Phase 1c).
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct TaskSpec {
    pub task_type: TaskType,
    /// Target-boundary encoding policy, resolved from target metadata.
    pub newline_policy: String,
    pub budget_policy: String,
    pub guard_set: Vec<String>,
}

/// The request passed to the single engine entry point.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct CorrectionRequest {
    pub task: TaskSpec,
    pub selected_text: String,
    pub transaction_id: String,
}

/// The engine result. `code` is the wire-visible outcome.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct CorrectionResult {
    pub code: InputCode,
    pub corrected_text: Option<String>,
    pub transaction_id: String,
    pub finish_reason: Option<String>,
    pub message: String,
}

impl CorrectionResult {
    /// A correction is pasteable only when it is a full success and the
    /// generation was not truncated.
    pub fn is_pasteable(&self) -> bool {
        self.code.is_ok() && self.finish_reason.as_deref() != Some("length")
    }
}

/// Single engine entry point. Phase 3 provides the implementation.
pub trait CorrectionEngine {
    fn run(&self, request: &CorrectionRequest) -> CorrectionResult;
}

#[cfg(test)]
mod tests {
    use super::*;

    fn base_task() -> TaskSpec {
        TaskSpec {
            task_type: TaskType::Correct,
            newline_policy: "target_default".into(),
            budget_policy: "balanced".into(),
            guard_set: vec!["hard_target".into(), "selection_verify".into()],
        }
    }

    #[test]
    fn task_spec_serializes_with_snake_case_task_type() {
        let spec = base_task();
        let json = serde_json::to_string(&spec).unwrap();
        assert!(json.contains("\"correct\""));
        let back: TaskSpec = serde_json::from_str(&json).unwrap();
        assert_eq!(back.task_type, TaskType::Correct);
    }

    #[test]
    fn finish_reason_length_result_is_not_pasteable() {
        let r = CorrectionResult {
            code: InputCode::Ok,
            corrected_text: Some("partial".into()),
            transaction_id: "t".into(),
            finish_reason: Some("length".into()),
            message: String::new(),
        };
        assert!(!r.is_pasteable());
    }

    #[test]
    fn full_success_without_length_finish_is_pasteable() {
        let r = CorrectionResult {
            code: InputCode::Ok,
            corrected_text: Some("full".into()),
            transaction_id: "t".into(),
            finish_reason: None,
            message: String::new(),
        };
        assert!(r.is_pasteable());
    }

    #[test]
    fn abort_code_result_is_not_pasteable() {
        let r = CorrectionResult {
            code: InputCode::GenerationTruncated,
            corrected_text: None,
            transaction_id: "t".into(),
            finish_reason: Some("length".into()),
            message: String::new(),
        };
        assert!(!r.is_pasteable());
    }

    #[test]
    fn request_roundtrips_json() {
        let req = CorrectionRequest {
            task: base_task(),
            selected_text: "The quick brown fox.".into(),
            transaction_id: "7c1a7f23".into(),
        };
        let json = serde_json::to_string(&req).unwrap();
        let back: CorrectionRequest = serde_json::from_str(&json).unwrap();
        assert_eq!(back.selected_text, "The quick brown fox.");
        assert_eq!(back.task.guard_set.len(), 2);
    }

    #[test]
    fn task_type_variants_are_distinct() {
        assert_ne!(TaskType::Correct, TaskType::Rewrite);
        assert_ne!(TaskType::Rewrite, TaskType::SavedAction);
    }
}
