//! UIA broker protocol (Phase 2c). State-transition reporting.

#[derive(Debug, Clone, Copy, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
#[serde(rename_all = "SCREAMING_SNAKE_CASE")]
pub enum BrokerState {
    RequestReceived,
    FocusedElementResolved,
    TextPatternResolved,
    BlockingGetSelection,
    ResultSerialized,
}
