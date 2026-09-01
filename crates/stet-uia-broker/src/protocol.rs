//! UIA broker protocol (Phase 2c, §2c).
//!
//! The broker is a lightweight child process hosting COM IUIAutomationTextPattern. The
//! core owns a monotonic ~250ms total request deadline and terminates/recreates the broker
//! on expiry. The broker reports state-transition progress; transitions are telemetry,
//! the external total deadline is the correctness boundary.

use serde::{Deserialize, Serialize};

/// Broker lifecycle states, in the order a request progresses.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "SCREAMING_SNAKE_CASE")]
pub enum BrokerState {
    RequestReceived,
    FocusedElementResolved,
    TextPatternResolved,
    BlockingGetSelection,
    ResultSerialized,
}

/// A broker request to capture selection text for a target PID.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct BrokerRequest {
    pub pid: u32,
    pub window_handle: u64,
    pub request_id: u64,
    /// Monotonic ms budget for the whole capture (usually 250).
    pub deadline_ms: u64,
}

/// A broker response (success or typed abort).
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct BrokerResponse {
    pub request_id: u64,
    pub outcome: BrokerOutcome,
    pub text: Option<String>,
    pub truncated: bool,
    pub states_seen: Vec<BrokerState>,
}

/// Capture outcome. `ok` is the only success; others map to the wire taxonomy.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum BrokerOutcome {
    Ok,
    // The core terminated us; the reported states are telemetry only.
    TimedOut,
    FrozenTarget,
    SelectionUnavailable,
    InternalError,
}

impl BrokerOutcome {
    pub fn is_success(&self) -> bool {
        *self == BrokerOutcome::Ok
    }
}

/// A capture deadline snapshot: states reached + elapsed monotonic ms.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct BrokerTelemetry {
    pub states: Vec<BrokerState>,
    pub elapsed_ms: u64,
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn broker_states_serialize_screaming_snake_case() {
        assert_eq!(serde_json::to_string(&BrokerState::RequestReceived).unwrap(), "\"REQUEST_RECEIVED\"");
        assert_eq!(serde_json::to_string(&BrokerState::BlockingGetSelection).unwrap(), "\"BLOCKING_GET_SELECTION\"");
    }
    #[test]
    fn request_roundtrips_json() {
        let r = BrokerRequest { pid: 1234, window_handle: 5678, request_id: 1, deadline_ms: 250 };
        let j = serde_json::to_string(&r).unwrap();
        let back: BrokerRequest = serde_json::from_str(&j).unwrap();
        assert_eq!(back.pid, 1234);
        assert_eq!(back.deadline_ms, 250);
    }
    #[test]
    fn only_ok_outcome_is_success() {
        assert!(BrokerOutcome::Ok.is_success());
        assert!(!BrokerOutcome::TimedOut.is_success());
        assert!(!BrokerOutcome::FrozenTarget.is_success());
    }
    #[test]
    fn response_states_are_telemetry_on_timeout() {
        let resp = BrokerResponse {
            request_id: 1,
            outcome: BrokerOutcome::TimedOut,
            text: None,
            truncated: false,
            states_seen: vec![BrokerState::RequestReceived, BrokerState::FocusedElementResolved],
        };
        // A timed-out capture still reports how far it got (telemetry only).
        assert_eq!(resp.states_seen.len(), 2);
        assert!(!resp.outcome.is_success());
    }
    #[test]
    fn telemetry_serializes_states_and_elapsed() {
        let t = BrokerTelemetry { states: vec![BrokerState::TextPatternResolved], elapsed_ms: 42 };
        let j = serde_json::to_string(&t).unwrap();
        let back: BrokerTelemetry = serde_json::from_str(&j).unwrap();
        assert_eq!(back.elapsed_ms, 42);
    }
}
