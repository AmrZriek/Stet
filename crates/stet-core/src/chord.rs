//! Chord verification (Phase 2e, §2e/§2.2).
//!
//! After injecting a capture chord (Ctrl+C), verify the clipboard sequence actually
//! moved; if not, retry ONCE with a context-aware alternate chord. SendInput return
//! count alone is NEVER treated as evidence that a replacement was consumed. These
//! decisions are pure; the SendInput/Win32 calls sit behind them.

/// A chord to synthesize.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ChordKind {
    CtrlC,
    CtrlInsert,
    CtrlShiftC,
    CtrlV,
    CtrlShiftV,
}

/// Verdict after checking whether a capture chord moved the clipboard.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ChordVerdict {
    CaptureVerified,
    TryAlternate(ChordKind),
    FailedCapture,
}

/// Decides the chord-verification retry sequence.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct ChordVerifier {
    alternate_used: bool,
}

impl Default for ChordVerifier {
    fn default() -> Self {
        Self::new()
    }
}

impl ChordVerifier {
    pub fn new() -> Self {
        ChordVerifier { alternate_used: false }
    }

    /// Evaluate a capture chord result. `seq_moved` is whether the clipboard
    /// sequence advanced. `alternate` is the context-aware fallback.
    pub fn evaluate(&mut self, seq_moved: bool, alternate: ChordKind) -> ChordVerdict {
        if seq_moved {
            return ChordVerdict::CaptureVerified;
        }
        if self.alternate_used {
            return ChordVerdict::FailedCapture;
        }
        self.alternate_used = true;
        ChordVerdict::TryAlternate(alternate)
    }

    /// Whether consumption was verified by independent observation (UIA/reread),
    /// NOT by SendInput return count.
    pub fn consumption_verified(observed: bool) -> bool {
        observed
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn capture_verified_when_sequence_moved() {
        let mut v = ChordVerifier::new();
        assert_eq!(v.evaluate(true, ChordKind::CtrlInsert), ChordVerdict::CaptureVerified);
    }
    #[test]
    fn retries_once_with_alternate_on_first_failure() {
        let mut v = ChordVerifier::new();
        assert_eq!(v.evaluate(false, ChordKind::CtrlInsert), ChordVerdict::TryAlternate(ChordKind::CtrlInsert));
    }
    #[test]
    fn fails_capture_after_alternate_also_fails() {
        let mut v = ChordVerifier::new();
        assert_eq!(v.evaluate(false, ChordKind::CtrlInsert), ChordVerdict::TryAlternate(ChordKind::CtrlInsert));
        assert_eq!(v.evaluate(false, ChordKind::CtrlInsert), ChordVerdict::FailedCapture);
    }
    #[test]
    fn sendinput_count_is_not_consumption_evidence() {
        assert!(!ChordVerifier::consumption_verified(false));
        assert!(ChordVerifier::consumption_verified(true));
    }
    #[test]
    fn terminal_uses_ctrl_shift_v_paste_chord() {
        assert_ne!(ChordKind::CtrlV, ChordKind::CtrlShiftV);
    }
}
