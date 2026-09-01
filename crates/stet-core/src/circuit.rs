//! Per-target UIA circuit breaker (Phase 2c).
//!
//! §2c/§4.4: After one timeout on a frozen PID, skip UIA for that PID for 60s.
//! The breaker uses a monotonic clock; a frozen PID is not repeatedly re-probed.
//! Pure + testable; the UIA broker FFI calls sit behind this decision.

use crate::timeout::Monotonic;
use std::collections::HashMap;
use std::time::Duration;

/// Circuit breaker entry for one target PID.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct CircuitEntry {
    pub open_at: Monotonic,
    pub timeout_count: u32,
}

/// Per-target circuit breaker. Tracks how recently a PID timed out.
#[derive(Debug, Clone, PartialEq, Eq, Default)]
pub struct CircuitBreaker {
    entries: HashMap<u32, CircuitEntry>,
    pub open_duration: Duration,
}

impl CircuitBreaker {
    pub fn new(open_duration: Duration) -> Self {
        CircuitBreaker { entries: Default::default(), open_duration }
    }

    pub fn record_timeout(&mut self, pid: u32, now: Monotonic) {
        let entry = self.entries.entry(pid).or_insert(CircuitEntry { open_at: now, timeout_count: 0 });
        entry.open_at = now;
        entry.timeout_count += 1;
    }

    pub fn is_open(&self, pid: u32, now: Monotonic) -> bool {
        match self.entries.get(&pid) {
            Some(e) => now.saturating_duration_since(e.open_at) < self.open_duration,
            None => false,
        }
    }

    pub fn reset(&mut self, pid: u32) {
        self.entries.remove(&pid);
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::time::Duration;
    fn t(ms: u64) -> Monotonic { Monotonic::from_ms(ms) }
    #[test]
    fn fresh_breaker_is_closed() {
        let cb = CircuitBreaker::new(Duration::from_secs(60));
        assert!(!cb.is_open(1234, t(0)));
    }
    #[test]
    fn timeout_opens_breaker_for_that_pid() {
        let mut cb = CircuitBreaker::new(Duration::from_secs(60));
        cb.record_timeout(1234, t(1000));
        assert!(cb.is_open(1234, t(1001)));
        assert!(!cb.is_open(5678, t(1001)));
    }
    #[test]
    fn breaker_closes_after_open_duration() {
        let mut cb = CircuitBreaker::new(Duration::from_secs(60));
        cb.record_timeout(1234, t(1000));
        assert!(!cb.is_open(1234, t(1000 + 60_000)));
    }
    #[test]
    fn reset_clears_breaker() {
        let mut cb = CircuitBreaker::new(Duration::from_secs(60));
        cb.record_timeout(1234, t(1000));
        cb.reset(1234);
        assert!(!cb.is_open(1234, t(1001)));
    }
    #[test]
    fn repeated_timeouts_increment_count() {
        let mut cb = CircuitBreaker::new(Duration::from_secs(60));
        cb.record_timeout(1234, t(1000));
        cb.record_timeout(1234, t(1001));
        assert_eq!(cb.entries.get(&1234).unwrap().timeout_count, 2);
    }
}
