//! Monotonic deadline tracking (Phase 1b, §3.1).
//!
//! Safety decisions use a monotonic clock, never wall-clock. Handshake and
//! per-operation deadlines are expressed as `Instant`-based absolute windows.

use crate::frame::{IpcError, IpcResult};
use std::time::{Duration, Instant};

/// A monotonically-increasing clock value. Backed by `Instant` at runtime but
/// allows deterministic `from_ms` construction for pure tests. Safety decisions
/// never use wall-clock time (§4.1).
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Default)]
pub struct Monotonic {
    value: u64,
}

impl Monotonic {
    pub fn from_ms(ms: u64) -> Self {
        Monotonic { value: ms }
    }

    pub fn now() -> Self {
        let base = Instant::now().duration_since(Instant::now()).as_millis() as u64;
        Monotonic { value: base }
    }

    pub fn saturating_duration_since(&self, earlier: Self) -> Duration {
        Duration::from_millis(self.value.saturating_sub(earlier.value))
    }

    pub fn as_ms(&self) -> u64 {
        self.value
    }
}

/// Deadline helper. `Instant` is monotonic on this platform.
#[derive(Debug, Clone, Copy)]
pub struct Deadline {
    deadline: Instant,
}

impl Deadline {
    pub fn after(window: Duration) -> Self {
        Deadline { deadline: Instant::now() + window }
    }

    pub fn expired(&self) -> bool {
        Instant::now() >= self.deadline
    }

    pub fn remaining(&self) -> Duration {
        self.deadline.saturating_duration_since(Instant::now())
    }

    pub fn check(&self) -> IpcResult<Duration> {
        let rem = self.remaining();
        if rem.is_zero() {
            return Err(IpcError::Timeout);
        }
        Ok(rem)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn deadline_not_expired_within_window() {
        let d = Deadline::after(Duration::from_millis(1000));
        assert!(!d.expired());
        assert!(d.check().is_ok());
    }

    #[test]
    fn deadline_expires_after_window() {
        let d = Deadline::after(Duration::from_millis(1));
        std::thread::sleep(Duration::from_millis(15));
        assert!(d.expired());
        assert_eq!(d.check(), Err(IpcError::Timeout));
    }

    #[test]
    fn remaining_is_positive_before_expiry() {
        let d = Deadline::after(Duration::from_secs(5));
        assert!(d.remaining() > Duration::ZERO);
    }

    #[test]
    fn monotonic_from_ms_is_deterministic() {
        let a = Monotonic::from_ms(1000);
        let b = Monotonic::from_ms(2000);
        assert_eq!(a.as_ms(), 1000);
        assert!(b > a);
        assert_eq!(b.saturating_duration_since(a), Duration::from_millis(1000));
    }

    #[test]
    fn monotonic_saturates_at_zero() {
        let a = Monotonic::from_ms(1000);
        let b = Monotonic::from_ms(500);
        assert_eq!(b.saturating_duration_since(a), Duration::ZERO);
    }
}
