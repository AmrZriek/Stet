//! Re-entrancy / busy-drop semantics (Phase 2, §4.2).
//!
//! The core captures BEFORE the UI sees the event, so Python `_hotkey_busy` cannot guard
//! interleaving. Busy semantics are defined AT THE CORE: default is DROP (no second capture
//! while one is in flight) with a `busy: true` flag and `busy_dropped` error code.

/// Busy-policy mode.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub enum BusyPolicy {
    /// Drop a second capture while one is in flight (default).
    #[default]
    Drop,
    /// Queue mode is a config option for later; not yet implemented.
    Queue,
}

/// Verdict for an incoming trigger while a capture is in flight.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum BusyVerdict {
    /// No capture in flight: proceed normally.
    Proceed,
    /// A capture is in flight and policy is Drop: emit `busy_dropped`.
    Drop
}

/// The busy gate. Tracks whether a capture is currently in flight.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub struct BusyGate {
    in_flight: bool,
    policy: BusyPolicy,
}

impl BusyGate {
    pub fn new(policy: BusyPolicy) -> Self {
        BusyGate { in_flight: false, policy }
    }

    pub fn in_flight(&self) -> bool {
        self.in_flight
    }

    /// Called when a hotkey trigger fires. Returns Proceed (and marks in-flight)
    /// or Drop when a capture is already in flight under the Drop policy.
    pub fn on_trigger(&mut self) -> BusyVerdict {
        if self.in_flight {
            match self.policy {
                BusyPolicy::Drop => return BusyVerdict::Drop,
                BusyPolicy::Queue => return BusyVerdict::Drop,
            }
        }
        self.in_flight = true;
        BusyVerdict::Proceed
    }

    /// Mark the capture complete and release the in-flight slot.
    pub fn release(&mut self) {
        self.in_flight = false;
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn first_trigger_proceeds_and_marks_in_flight() {
        let mut g = BusyGate::new(BusyPolicy::Drop);
        assert_eq!(g.on_trigger(), BusyVerdict::Proceed);
        assert!(g.in_flight());
    }
    #[test]
    fn second_trigger_is_dropped() {
        let mut g = BusyGate::new(BusyPolicy::Drop);
        g.on_trigger();
        assert_eq!(g.on_trigger(), BusyVerdict::Drop);
    }
    #[test]
    fn release_allows_next_trigger() {
        let mut g = BusyGate::new(BusyPolicy::Drop);
        g.on_trigger();
        g.release();
        assert!(!g.in_flight());
        assert_eq!(g.on_trigger(), BusyVerdict::Proceed);
    }
    #[test]
    fn drop_policy_keeps_in_flight_after_drop() {
        let mut g = BusyGate::new(BusyPolicy::Drop);
        g.on_trigger();
        assert_eq!(g.on_trigger(), BusyVerdict::Drop);
        // The in-flight capture is still active; only release() clears it.
        assert!(g.in_flight());
    }
}
