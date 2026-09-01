//! Input-context classifier (Phase 4, §4.4).
//!
//! Encodes the §4.4 failure-mode decisions for the input path: session mode, IME
//! composition, elevated/UIPI target. These are pure decision logic the Win32
//! inspectors (WTSGetActiveConsoleSessionId, WM_IME_ENDCOMPOSITION, UIPI) feed into.

/// Session mode: whether input is local or a remote/RDP session (§4.4).
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub enum SessionMode {
    #[default]
    Local,
    RemoteInput,
}

/// Whether IME composition is active (defer chord until WM_IME_ENDCOMPOSITION).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct ImeState {
    pub composing: bool,
}

/// Whether the target is elevated (UIPI blocks SendInput/UIA from non-elevated core).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Elevation {
    NonElevated,
    Elevated,
}

/// Capture-safety decision for the current input context.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum CaptureVerdict {
    Proceed,
    DeferIme,
    AbortIntegrity,
}

/// Classify the capture context for a hotkey trigger.
pub fn classify(mode: SessionMode, ime: ImeState, elevation: Elevation) -> CaptureVerdict {
    let _ = mode;
    if elevation == Elevation::Elevated {
        return CaptureVerdict::AbortIntegrity;
    }
    if ime.composing {
        return CaptureVerdict::DeferIme;
    }
    CaptureVerdict::Proceed
}

/// In remote_input mode, injected-flag events are remote input (not Stet-self).
pub fn stet_self_event(dw_extra_info: usize, injected: bool, mode: SessionMode) -> bool {
    let _ = injected;
    let _ = mode;
    dw_extra_info == crate::input_class::STET_DW_EXTRA_INFO
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn local_non_composing_non_elevated_proceeds() {
        assert_eq!(classify(SessionMode::Local, ImeState { composing: false }, Elevation::NonElevated), CaptureVerdict::Proceed);
    }

    #[test]
    fn ime_composition_defers_chord() {
        assert_eq!(classify(SessionMode::Local, ImeState { composing: true }, Elevation::NonElevated), CaptureVerdict::DeferIme);
    }

    #[test]
    fn elevated_target_aborts_integrity() {
        assert_eq!(classify(SessionMode::Local, ImeState { composing: false }, Elevation::Elevated), CaptureVerdict::AbortIntegrity);
    }

    #[test]
    fn remote_injected_event_is_not_stet_self() {
        assert!(!stet_self_event(0, true, SessionMode::RemoteInput));
    }

    #[test]
    fn stet_tag_is_self_even_in_remote_mode() {
        let tag = crate::input_class::STET_DW_EXTRA_INFO;
        assert!(stet_self_event(tag, true, SessionMode::RemoteInput));
    }
}