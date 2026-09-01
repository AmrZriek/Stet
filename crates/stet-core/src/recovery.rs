//! Generation-truncation recovery (Phase 6b, §3a).
//!
//! A `finish_reason == "length"` result is GENERATION_TRUNCATED, NEVER pasteable.
//! At most ONE retry is allowed, with a LARGER reservation, and only if the closed
//! budget still fits. If it remains truncated after the retry, the correction is
//! aborted (or degraded to the windowed plan), never exposed as a partial result.

/// Outcome of a generation-truncation recovery decision.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum RecoveryDecision {
    Acceptable,
    RetryOnce(usize),
    Abort,
}

/// Decides the recovery path for a truncated generation.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Recovery {
    retry_used: bool,
    max_retries: usize,
}

impl Recovery {
    pub fn new(max_retries: usize) -> Self {
        Recovery { retry_used: false, max_retries }
    }

    pub fn decide(
        &mut self,
        finish_reason: Option<&str>,
        budget_fits: bool,
        larger_reservation: usize,
    ) -> RecoveryDecision {
        if finish_reason != Some("length") {
            return RecoveryDecision::Acceptable;
        }
        if self.retry_used || !budget_fits || self.max_retries == 0 {
            return RecoveryDecision::Abort;
        }
        self.retry_used = true;
        RecoveryDecision::RetryOnce(larger_reservation)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn not_truncated_is_acceptable() {
        let mut r = Recovery::new(1);
        assert_eq!(r.decide(None, true, 0), RecoveryDecision::Acceptable);
    }

    #[test]
    fn truncated_allows_one_retry_when_budget_fits() {
        let mut r = Recovery::new(1);
        assert_eq!(r.decide(Some("length"), true, 512), RecoveryDecision::RetryOnce(512));
    }

    #[test]
    fn second_truncation_aborts_after_retry_used() {
        let mut r = Recovery::new(1);
        let _ = r.decide(Some("length"), true, 512);
        assert_eq!(r.decide(Some("length"), true, 512), RecoveryDecision::Abort);
    }

    #[test]
    fn truncated_aborts_when_budget_does_not_fit() {
        let mut r = Recovery::new(1);
        assert_eq!(r.decide(Some("length"), false, 512), RecoveryDecision::Abort);
    }

    #[test]
    fn zero_retries_aborts_immediately() {
        let mut r = Recovery::new(0);
        assert_eq!(r.decide(Some("length"), true, 512), RecoveryDecision::Abort);
    }

    #[test]
    fn truncated_never_pasteable() {
        // The decision Abort/RetryOnce both mean the result is NOT pasteable as-is.
        let mut r = Recovery::new(1);
        assert_ne!(r.decide(Some("length"), true, 512), RecoveryDecision::Acceptable);
    }
}