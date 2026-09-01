//! Paste-consumption verification (Phase 2e, §4.4 slow-target rule).
//!
//! After a paste chord, use a bounded UIA/target observation to distinguish queued
//! input from consumed replacement. If consumption cannot be verified within the
//! commit ceiling, return paste_unverified and DO NOT restore over possibly
//! user-owned clipboard data (the restore gate in clipboard.rs backs this).

/// Outcome of paste-consumption observation.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ConsumptionVerdict {
    /// The replacement was observed as consumed by the target.
    Verified,
    /// Consumption could not be verified within the ceiling; do NOT restore.
    PasteUnverified,
}

/// Determines paste-consumption verification against a bounded commit ceiling.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub struct PasteObserver {
    /// Normal commit deadline (ms) and absolute ceiling (ms).
    pub normal_deadline_ms: u64,
    pub absolute_ceiling_ms: u64,
}

impl PasteObserver {
    pub fn new(normal_deadline_ms: u64, absolute_ceiling_ms: u64) -> Self {
        PasteObserver { normal_deadline_ms, absolute_ceiling_ms }
    }

    /// Decide consumption when the observation cost is known.
    /// Returns Verified when observed within the ceiling; PasteUnverified otherwise.
    pub fn observe(&self, observed: bool, elapsed_ms: u64) -> ConsumptionVerdict {
        if observed && elapsed_ms <= self.absolute_ceiling_ms {
            ConsumptionVerdict::Verified
        } else {
            ConsumptionVerdict::PasteUnverified
        }
    }

    /// Whether a slow target is allowed to extend observation within the ceiling.
    pub fn allow_slow_extension(&self, elapsed_ms: u64) -> bool {
        elapsed_ms <= self.absolute_ceiling_ms
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn verified_when_observed_within_ceiling() {
        let o = PasteObserver::new(5000, 10000);
        assert_eq!(o.observe(true, 3000), ConsumptionVerdict::Verified);
    }

    #[test]
    fn unverified_when_not_observed() {
        let o = PasteObserver::new(5000, 10000);
        assert_eq!(o.observe(false, 3000), ConsumptionVerdict::PasteUnverified);
    }

    #[test]
    fn unverified_when_observation_exceeds_ceiling() {
        let o = PasteObserver::new(5000, 10000);
        assert_eq!(o.observe(true, 15000), ConsumptionVerdict::PasteUnverified);
    }

    #[test]
    fn slow_target_extension_allowed_within_ceiling() {
        let o = PasteObserver::new(5000, 10000);
        assert!(o.allow_slow_extension(9000));
        assert!(!o.allow_slow_extension(15000));
    }
}