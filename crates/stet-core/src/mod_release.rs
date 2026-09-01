//! Modifier-release tracker (Phase 2b) — the Shift+F9 regression fix core.
//!
//! §2b: On hotkey trigger with physically-held modifiers, the capture chord
//! (Ctrl+C) must NEVER be injected while a conflicting modifier is down or
//! logically latched. This module is the pure decision brain:
//!
//! ```text
//!  Idle ──(hotkey, mods held)──> WaitingRelease ──(all clear)──> SafeToInject
//!                                  │                              │
//!                                  └──(cap expiry)──> PostCap  ──(still down)──> Abort
//!                                                       └──(clear)──> SafeToInject
//! ```
//! The Win32 `WH_KEYBOARD_LL` hook only feeds physical modifier states in;
//! this state machine decides whether injection is safe.

use serde::{Deserialize, Serialize};

/// Logical modifier set (mirrors MOD_* bits).
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default, Serialize, Deserialize)]
pub struct ModifierSet {
    pub alt: bool,
    pub control: bool,
    pub shift: bool,
    pub win: bool,
}

impl ModifierSet {
    pub fn any(&self) -> bool {
        self.alt || self.control || self.shift || self.win
    }
}

/// Hook-input classification: whether a key event is Stet's own synthetic
/// event (matching the exact private dwExtraInfo tag) or physical/remote.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum EventKind {
    PhysicalOrRemote,
    StetInjected,
}

/// The capture chord to inject once safe.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Chord {
    CtrlC,
    CtrlInsert,
    CtrlShiftC,
}

/// Decision at the moment a hotkey fires, given physically-held modifiers.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ModStateOutcome {
    /// All relevant modifiers clear: inject the chord now (UIA-first tried).
    SafeToInject(Chord),
    /// One or more conflicting modifiers held: wait up to the release cap.
    WaitingRelease,
    /// Cap expired and the conflicting modifier is still down/latched: ABORT.
    Abort,
}

/// The tracker state machine. `idle()` -> on hotkey, feed modifier snapshots.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum TrackerState {
    Idle,
    WaitingRelease,
    PostCap,
    Aborted,
}

/// The core tracker. Advances on modifier snapshots; decides safe-to-inject.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct ModReleaseTracker {
    state: TrackerStateInner,
    /// Which modifiers conflict with the capture chord (e.g. shift on Ctrl+C).
    conflicting: ModifierSet,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum TrackerStateInner {
    Idle,
    WaitingRelease,
    PostCap,
    Aborted,
}

impl ModReleaseTracker {
    /// Tracker for a capture chord. `conflicting` are the modifiers that make
    /// the chord dangerous (e.g. for Ctrl+C, shift/alt/win are conflicting).
    pub fn new(conflicting: ModifierSet) -> Self {
        ModReleaseTracker { state: TrackerStateInner::Idle, conflicting }
    }

    pub fn state(&self) -> TrackerStateInner {
        self.state
    }

    /// A hotkey fired with the given physically-held modifiers. Returns the
    /// immediate decision. UIA-first already failed, so chord is last resort.
    pub fn on_hotkey(&mut self, held: ModifierSet) -> ModStateOutcome {
        if !self.conflicting_overlaps(held) {
            self.state = TrackerStateInner::Idle;
            return ModStateOutcome::SafeToInject(Chord::CtrlC);
        }
        self.state = TrackerStateInner::WaitingRelease;
        ModStateOutcome::WaitingRelease
    }

    /// Cap expired. If a conflicting modifier is still down/latched, we must
    /// abort (never inject). Otherwise safe to inject now.
    pub fn on_cap_expiry(&mut self, held: ModifierSet) -> ModStateOutcome {
        if self.conflicting_overlaps(held) {
            self.state = TrackerStateInner::Aborted;
            return ModStateOutcome::Abort;
        }
        self.state = TrackerStateInner::Idle;
        ModStateOutcome::SafeToInject(Chord::CtrlC)
    }

    /// A physical/remote modifier key-event snapshot. When the conflicting
    /// modifiers clear during WaitingRelease, injection becomes safe.
    pub fn on_modifier_snapshot(&mut self, held: ModifierSet) -> ModStateOutcome {
        if self.state == TrackerStateInner::WaitingRelease && !self.conflicting_overlaps(held) {
            self.state = TrackerStateInner::Idle;
            return ModStateOutcome::SafeToInject(Chord::CtrlC);
        }
        ModStateOutcome::WaitingRelease
    }

    fn conflicting_overlaps(&self, held: ModifierSet) -> bool {
        (self.conflicting.shift && held.shift)
            || (self.conflicting.control && held.control)
            || (self.conflicting.alt && held.alt)
            || (self.conflicting.win && held.win)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn shift_conflict() -> ModifierSet { ModifierSet { shift: true, ..Default::default() } }
    fn none() -> ModifierSet { ModifierSet::default() }

    fn ctrlc_tracker() -> ModReleaseTracker {
        // For Ctrl+C, shift/alt/win are conflicting (they alter the chord).
        ModReleaseTracker::new(ModifierSet { alt: true, shift: true, win: true, control: false })
    }

    #[test]
    fn no_conflicting_modifier_injects_immediately() {
        let mut t = ctrlc_tracker();
        let out = t.on_hotkey(none());
        assert_eq!(out, ModStateOutcome::SafeToInject(Chord::CtrlC));
    }

    #[test]
    fn shift_held_waitsthen_injects_on_release() {
        let mut t = ctrlc_tracker();
        let out = t.on_hotkey(shift_conflict());
        assert_eq!(out, ModStateOutcome::WaitingRelease);
        let out2 = t.on_modifier_snapshot(none());
        assert_eq!(out2, ModStateOutcome::SafeToInject(Chord::CtrlC));
    }

    #[test]
    fn cap_expiry_with_shift_still_down_aborts() {
        let mut t = ctrlc_tracker();
        t.on_hotkey(shift_conflict());
        let out = t.on_cap_expiry(shift_conflict());
        assert_eq!(out, ModStateOutcome::Abort);
        assert_eq!(t.state(), TrackerStateInner::Aborted);
    }

    #[test]
    fn cap_expiry_with_shift_cleared_injects() {
        let mut t = ctrlc_tracker();
        t.on_hotkey(shift_conflict());
        let out = t.on_cap_expiry(none());
        assert_eq!(out, ModStateOutcome::SafeToInject(Chord::CtrlC));
    }

    #[test]
    fn ctrl_shift_c_is_selected_for_terminal_target() {
        let mut t = ModReleaseTracker::new(ModifierSet { alt: true, shift: true, win: true, control: true });
        let out = t.on_hotkey(ModifierSet { control: true, ..Default::default() });
        assert_eq!(out, ModStateOutcome::WaitingRelease);
    }
}
