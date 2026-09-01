//! Target-class paste chord selection (Phase 2e, §2e).
//!
//! The paste chord table is part of target metadata, not an afterthought of the capture
//! path. Ordinary editors use Ctrl+V; terminal targets use Ctrl+Shift+V. Pure + testable;
//! the SendInput synthesis sits behind this decision by target class.

use crate::chord::ChordKind;

/// The target class category, derived from the control/window identity.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum TargetClass {
    /// Ordinary Win32 edit / RichEdit / Chromium text control.
    Editor,
    /// A terminal emulator / console host (Windows Terminal, conhost).
    Terminal,
    /// Unknown; default to the safe editor chord.
    Unknown,
}

/// Resolve a target class from a control identity (window class).
pub fn classify_target(control_identity: &str) -> TargetClass {
    let lower = control_identity.to_lowercase();
    if lower.contains("terminal") || lower.contains("console") || lower.contains("conhost") || lower.contains("wt_") {
        TargetClass::Terminal
    } else if lower.contains("edit") || lower.contains("renderwidget") || lower.contains("richedit") || lower.contains("chrome") || lower.is_empty() {
        TargetClass::Editor
    } else {
        TargetClass::Unknown
    }
}

/// The paste chord for a given target class (§2e table).
pub fn paste_chord_for(class: TargetClass) -> ChordKind {
    match class {
        TargetClass::Terminal => ChordKind::CtrlShiftV,
        TargetClass::Editor | TargetClass::Unknown => ChordKind::CtrlV,
    }
}

/// The capture chord for a given target class (for selection capture fallback).
pub fn capture_chord_for(class: TargetClass) -> ChordKind {
    match class {
        TargetClass::Terminal => ChordKind::CtrlShiftC,
        TargetClass::Editor | TargetClass::Unknown => ChordKind::CtrlC,
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn terminal_classifies_and_uses_ctrl_shift_v() {
        assert_eq!(classify_target("WindowsTerminal"), TargetClass::Terminal);
        assert_eq!(paste_chord_for(TargetClass::Terminal), ChordKind::CtrlShiftV);
    }
    #[test]
    fn editor_classifies_and_uses_ctrl_v() {
        assert_eq!(classify_target("Chrome_RenderWidgetHostHWND"), TargetClass::Editor);
        assert_eq!(paste_chord_for(TargetClass::Editor), ChordKind::CtrlV);
    }
    #[test]
    fn conhost_is_terminal() {
        assert_eq!(classify_target("ConsoleWindowClass"), TargetClass::Terminal);
        assert_eq!(paste_chord_for(TargetClass::Terminal), ChordKind::CtrlShiftV);
    }
    #[test]
    fn unknown_defaults_to_editor_chord() {
        assert_eq!(paste_chord_for(TargetClass::Unknown), ChordKind::CtrlV);
    }
    #[test]
    fn capture_chord_is_target_aware() {
        assert_eq!(capture_chord_for(TargetClass::Terminal), ChordKind::CtrlShiftC);
        assert_eq!(capture_chord_for(TargetClass::Editor), ChordKind::CtrlC);
    }
}
