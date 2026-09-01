//! Monotonic deadline tracking (Phase 1b, §3.1).
//!
//! Safety decisions use a monotonic clock, never wall-clock. Handshake and
//! per-operation deadlines are expressed as `Instant`-based absolute windows.

use crate::frame::{IpcError, IpcResult};
use std::time::{Duration, Instant};

/// Deadline helper. `Instant` is monotonic on this platform.
#[derive(Debug, Clone, Copy)]
pub struct Deadline {
    deadline: Instant,
}

impl Deadline {
    /// Create a deadline `window` from now.
    pub fn after(window: Duration) -> Self {
        Deadline { deadline: Instant::now() + window }
    }

    /// True once the deadline has passed.
    pub fn expired(&self) -> bool {
        Instant::now() >= self.deadline
    }

    /// Time remaining. 0 when expired.
    pub fn remaining(&self) -> Duration {
        self.deadline.saturating_duration_since(Instant::now())
    }

    /// Check remaining against a budget; returns a typed timeout error
    /// when the remaining time is zero/already expired.
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
}
