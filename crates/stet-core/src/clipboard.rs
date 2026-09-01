//! Clipboard restore-vs-paste race logic (Phase 2d, §4.1).
//!
//! The paste snapshot lifecycle: immediately before writing corrected text, record
//! the current clipboard sequence. After writing, record the post-write sequence.
//! Restore the fresh snapshot ONLY if the current sequence still equals the
//! recorded post-write value; any external change returns `aborted_clipboard_conflict`
//! and leaves the clipboard untouched. Pure + testable.


/// Outcome of deciding whether to restore the pre-paste clipboard snapshot.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum RestoreDecision {
    Restore,
    SkipRestore,
}

/// Decides restore-vs-keep based on clipboard sequence numbers.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub struct ClipboardRestoreGate {
    post_write_seq: Option<u64>,
}

impl ClipboardRestoreGate {
    pub fn new() -> Self {
        ClipboardRestoreGate { post_write_seq: None }
    }

    /// Record the sequence number that represents Stet's corrected text.
    pub fn record_post_write(&mut self, seq: u64) {
        self.post_write_seq = Some(seq);
    }

    /// Given the current clipboard sequence, decide if the snapshot can be
    /// restored. Returns SkipRestore if the clipboard was changed externally.
    pub fn decide(&self, current_seq: u64) -> RestoreDecision {
        match self.post_write_seq {
            Some(written) if current_seq == written => RestoreDecision::Restore,
            _ => RestoreDecision::SkipRestore,
        }
    }

    /// Capture-fallback variant: restore immediately after a chord capture.
    pub fn decide_capture_fallback(&self, current_seq: u64, post_capture_seq: u64) -> RestoreDecision {
        if current_seq == post_capture_seq {
            RestoreDecision::Restore
        } else {
            RestoreDecision::SkipRestore
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn restores_when_sequence_unchanged_since_stet_write() {
        let mut g = ClipboardRestoreGate::new();
        g.record_post_write(100);
        assert_eq!(g.decide(100), RestoreDecision::Restore);
    }
    #[test]
    fn skips_restore_when_clipboard_changed_externally() {
        let mut g = ClipboardRestoreGate::new();
        g.record_post_write(100);
        assert_eq!(g.decide(101), RestoreDecision::SkipRestore);
    }
    #[test]
    fn skips_restore_when_no_post_write_recorded() {
        let g = ClipboardRestoreGate::new();
        assert_eq!(g.decide(100), RestoreDecision::SkipRestore);
    }
    #[test]
    fn capture_fallback_restores_only_on_matching_sequence() {
        let g = ClipboardRestoreGate::new();
        assert_eq!(g.decide_capture_fallback(55, 55), RestoreDecision::Restore);
        assert_eq!(g.decide_capture_fallback(56, 55), RestoreDecision::SkipRestore);
    }
    #[test]
    fn external_change_leaves_clipboard_untouched_by_contract() {
        let mut g = ClipboardRestoreGate::new();
        g.record_post_write(100);
        assert_eq!(g.decide(999), RestoreDecision::SkipRestore);
    }
}
