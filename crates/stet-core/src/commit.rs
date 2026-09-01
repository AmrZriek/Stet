//! Commit idempotency (Phase 2, §4.1).
//!
//! Every PASTE_TEXT carries a unique `transaction_id`. The core permits ONE in-flight commit
//! per transaction ID, caches its terminal result until the UI acknowledges it, and returns the
//! cached result for duplicate requests WITHOUT injecting a second paste. After a core restart
//! the UI never replays an uncertain transaction automatically. Pure + testable.

/// The result of trying to start a commit for a transaction.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum CommitDecision {
    /// No prior commit for this transaction: proceed with a fresh commit.
    New,
    /// This transaction already has a terminal result cached: reuse it.
    Cached,
    /// This transaction is already in flight: drop the duplicate.
    InFlight,
}

/// Tracks commit state per transaction ID. Guarantees at-most-once paste.
#[derive(Debug, Clone, PartialEq, Eq, Default)]
pub struct CommitTracker {
    in_flight: std::collections::HashSet<String>,
    cached: std::collections::HashMap<String, CommitCacheEntry>,
}

/// Cached terminal result for a transaction.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct CommitCacheEntry {
    pub outcome: CommitOutcome,
}

/// Terminal outcome of a paste commit.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum CommitOutcome {
    Pasted,
    Aborted,
    Unverified,
}

impl CommitTracker {
    pub fn new() -> Self {
        CommitTracker::default()
    }

    /// Decide whether to begin a commit for `txn`. Returns Cached (terminal already
    /// known) or InFlight (duplicate) without starting a new paste.
    pub fn begin(&mut self, txn: &str) -> CommitDecision {
        if self.cached.contains_key(txn) {
            return CommitDecision::Cached;
        }
        if self.in_flight.contains(txn) {
            return CommitDecision::InFlight;
        }
        self.in_flight.insert(txn.to_string());
        CommitDecision::New
    }

    /// Record a terminal outcome for `txn`. Clears in-flight and caches the result.
    pub fn finish(&mut self, txn: &str, outcome: CommitOutcome) {
        self.in_flight.remove(txn);
        self.cached.insert(txn.to_string(), CommitCacheEntry { outcome });
    }

    /// Fetch the cached terminal outcome for a duplicate request.
    pub fn cached_outcome(&self, txn: &str) -> Option<CommitOutcome> {
        self.cached.get(txn).map(|e| e.outcome)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn first_begin_is_new_and_tracks_in_flight() {
        let mut t = CommitTracker::new();
        assert_eq!(t.begin("7c1a"), CommitDecision::New);
        // A duplicate begin while in flight is dropped (no second paste).
        assert_eq!(t.begin("7c1a"), CommitDecision::InFlight);
    }
    #[test]
    fn finish_caches_terminal_result_for_duplicate() {
        let mut t = CommitTracker::new();
        t.begin("7c1a");
        t.finish("7c1a", CommitOutcome::Pasted);
        // A duplicate request after the terminal result is Cached (no re-paste).
        assert_eq!(t.begin("7c1a"), CommitDecision::Cached);
        assert_eq!(t.cached_outcome("7c1a"), Some(CommitOutcome::Pasted));
    }
    #[test]
    fn distinct_transactions_are_independent() {
        let mut t = CommitTracker::new();
        t.begin("txn-a");
        assert_eq!(t.begin("txn-b"), CommitDecision::New);
        t.finish("txn-b", CommitOutcome::Unverified);
        // txn-a still in flight, txn-b cached.
        assert_eq!(t.begin("txn-a"), CommitDecision::InFlight);
        assert_eq!(t.cached_outcome("txn-b"), Some(CommitOutcome::Unverified));
    }
    #[test]
    fn after_restart_tracker_is_empty_and_requires_fresh_action() {
        // A fresh tracker (post-restart) has no memory of any transaction.
        let mut t = CommitTracker::new();
        assert_eq!(t.cached_outcome("7c1a"), None);
        assert_eq!(t.begin("7c1a"), CommitDecision::New);
    }
}
