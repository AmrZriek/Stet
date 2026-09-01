//! Unified contract types for target/capture/paste transactions (Phase 1a).
//!
//! Mirror of the Python Phase 1a dataclasses. Pure, testable, no Windows deps.

use serde::{Deserialize, Serialize};

/// Successful adapter code. Only OK/SUCCESS are successful; internal codes map
/// at the wire boundary to the documented aborted_* taxonomy.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum InputCode {
    Ok,
    Success,
    UnsupportedPlatform,
    AccessibilityRequired,
    InputMonitoringRequired,
    PostEventRequired,
    HotkeyConflict,
    NoSelection,
    TargetAppChanged,
    PasteboardChangedExternally,
    PermissionDenied,
    NotAvailable,
    Error,
}

impl InputCode {
    pub fn is_ok(&self) -> bool {
        matches!(self, InputCode::Ok | InputCode::Success)
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum SelectionSource {
    Accessibility,
    Clipboard,
}

/// Platform-neutral target identity (Phase 0b/1a).
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct CompoundIdentity {
    pub hwnd: u64,
    pub pid: u64,
    pub process_creation_time: u64,
    pub session_id: u32,
    pub window_class: String,
    pub title_hash: String,
}

/// TargetToken replaces AppIdentity at the capture/paste boundary (Phase 1a).
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct TargetToken {
    pub pid: u64,
    pub process_creation_time: u64,
    pub session_id: u32,
    pub window_handle: u64,
    pub control_identity: String,
    pub title_hash: String,
    pub capture_source: SelectionSource,
    pub session_mode: String,
    pub selection_fingerprint: String,
    pub raw_selection_fingerprint: String,
    pub fingerprint_policy: String,
    pub newline_policy: String,
    pub captured_at_monotonic_ms: u64,
}

impl TargetToken {
    /// Canonical (line-endings-only) selection fingerprint.
    pub fn selection_fingerprint(&self) -> &str {
        &self.selection_fingerprint
    }
}

/// SelectionCapture: exact text, never stripped (Phase 1a).
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct SelectionCapture {
    pub text: String,
    pub source: SelectionSource,
    pub selection_range_count: u32,
    pub document_range_match: Option<bool>,
    pub selection_fingerprint: String,
    pub raw_selection_fingerprint: String,
    pub fingerprint_policy: String,
    pub truncated: bool,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct SelectionResult {
    pub code: InputCode,
    pub text: Option<String>,
    pub source: Option<SelectionSource>,
    pub target_token: Option<TargetToken>,
    pub capture: Option<SelectionCapture>,
    pub truncated: bool,
    pub message: String,
    // Internal Python transition fields retained only during Phase 1-3:
    pub original_clipboard: Option<serde_json::Value>,
    pub clipboard_change_count: Option<u64>,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct UndoToken {
    pub target_token: TargetToken,
    pub replacement_fingerprint: String,
    pub created_at_monotonic: f64,
}

/// PasteResult wire status; "pasted" is the only success status.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct PasteResult {
    pub code: InputCode,
    pub status: String,
    pub transaction_id: String,
    pub undo_token: Option<UndoToken>,
    pub message: String,
}

impl PasteResult {
    pub fn ok(&self) -> bool {
        self.code.is_ok() && self.status == "pasted"
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn sample_token() -> TargetToken {
        TargetToken {
            pid: 14208,
            process_creation_time: 133702345678901234,
            session_id: 1,
            window_handle: 140392,
            control_identity: "Chrome_RenderWidgetHostHWND".into(),
            title_hash: "a9f84c2e71".into(),
            capture_source: SelectionSource::Accessibility,
            session_mode: "local".into(),
            selection_fingerprint: "sha256canonical".into(),
            raw_selection_fingerprint: "sha256exact".into(),
            fingerprint_policy: "line_endings_only_v1".into(),
            newline_policy: "target_default".into(),
            captured_at_monotonic_ms: 987654321,
        }
    }

    #[test]
    fn input_code_ok_alias() {
        assert!(InputCode::Ok.is_ok());
        assert!(InputCode::Success.is_ok());
        assert!(!InputCode::Error.is_ok());
        assert!(!InputCode::NoSelection.is_ok());
    }

    #[test]
    fn target_token_roundtrip_json() {
        let tok = sample_token();
        let json = serde_json::to_string(&tok).unwrap();
        let back: TargetToken = serde_json::from_str(&json).unwrap();
        assert_eq!(back.pid, 14208);
        assert_eq!(back.control_identity, "Chrome_RenderWidgetHostHWND");
        assert_eq!(back.selection_fingerprint(), "sha256canonical");
    }

    #[test]
    fn selection_capture_preserves_exact_text_and_truncation_flag() {
        let cap = SelectionCapture {
            text: "  leading\n  trailing  ".into(),
            source: SelectionSource::Clipboard,
            selection_range_count: 1,
            document_range_match: Some(true),
            selection_fingerprint: "f".into(),
            raw_selection_fingerprint: "rf".into(),
            fingerprint_policy: "line_endings_only_v1".into(),
            truncated: false,
        };
        assert_eq!(cap.text, "  leading\n  trailing  ");
        assert!(!cap.truncated);
    }

    #[test]
    fn paste_result_only_success_status_is_pasted() {
        let ok = PasteResult {
            code: InputCode::Ok,
            status: "pasted".into(),
            transaction_id: "t1".into(),
            undo_token: None,
            message: "".into(),
        };
        assert!(ok.ok());
        let unverified = PasteResult {
            code: InputCode::Error,
            status: "unverified".into(),
            transaction_id: "t2".into(),
            undo_token: None,
            message: "".into(),
        };
        assert!(!unverified.ok());
    }
}
