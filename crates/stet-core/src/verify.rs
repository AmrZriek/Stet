//! Selection-fingerprint verification (Phase 4b/§4.4).
//!
//! The exact-selection precondition: before a target-specific paste chord, re-read
//! the current selection and verify it matches the recorded fingerprint. A mismatch
//! returns aborted_selection_changed BEFORE any Ctrl+V. The canonical fingerprint is
//! a line-endings-only SHA-256; the raw fingerprint is an exact UTF-8 SHA-256.

use sha2::{Digest, Sha256};

pub fn canonicalize_line_endings(s: &str) -> String {
    s.replace("\r\n", "\n").replace('\r', "\n")
}

pub fn sha256_hex(data: &[u8]) -> String {
    let mut hasher = Sha256::new();
    hasher.update(data);
    let digest = hasher.finalize();
    digest.iter().map(|b| format!("{:02x}", b)).collect()
}

pub fn raw_selection_fingerprint(text: &str) -> String {
    sha256_hex(text.as_bytes())
}

pub fn canonical_selection_fingerprint(text: &str) -> String {
    sha256_hex(canonicalize_line_endings(text).as_bytes())
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum SelectionVerdict {
    Matched,
    Changed,
    Unverifiable,
}

pub fn verify_selection(
    current_text: Option<&str>,
    expected_raw: &str,
    expected_canonical: &str,
    use_raw: bool,
) -> SelectionVerdict {
    match current_text {
        None => SelectionVerdict::Unverifiable,
        Some(text) => {
            if use_raw {
                if raw_selection_fingerprint(text) == expected_raw {
                    SelectionVerdict::Matched
                } else {
                    SelectionVerdict::Changed
                }
            } else if canonical_selection_fingerprint(text) == expected_canonical {
                SelectionVerdict::Matched
            } else {
                SelectionVerdict::Changed
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn canonicalize_only_folds_newlines() {
        assert_eq!(canonicalize_line_endings("a\r\nb\rc\nd"), "a\nb\nc\nd");
    }

    #[test]
    fn raw_fingerprint_is_exact() {
        assert_ne!(raw_selection_fingerprint("a\r\nb"), raw_selection_fingerprint("a\nb"));
    }

    #[test]
    fn canonical_fingerprint_folds_newlines() {
        assert_eq!(canonical_selection_fingerprint("a\r\nb"), canonical_selection_fingerprint("a\nb"));
    }

    #[test]
    fn matched_when_raw_fingerprint_equals() {
        let s = "the exact\r\ntext";
        let raw = raw_selection_fingerprint(s);
        assert_eq!(verify_selection(Some("the exact\r\ntext"), &raw, "", true), SelectionVerdict::Matched);
    }

    #[test]
    fn changed_when_selection_differs() {
        let s = "original text";
        let raw = raw_selection_fingerprint(s);
        assert_eq!(verify_selection(Some("different text"), &raw, "", true), SelectionVerdict::Changed);
    }

    #[test]
    fn unverifiable_when_cannot_reread() {
        assert_eq!(verify_selection(None, "x", "y", true), SelectionVerdict::Unverifiable);
    }

    #[test]
    fn canonical_match_ignores_line_ending_difference() {
        let text = "line1\r\nline2";
        let canonical = canonical_selection_fingerprint(text);
        assert_eq!(verify_selection(Some("line1\nline2"), "", &canonical, false), SelectionVerdict::Matched);
    }
}