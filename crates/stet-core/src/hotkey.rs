//! Diff-based transactional hotkey registration (Phase 2a, §2a).
//!
//! Mirrors Phase 0g: compute `to_unregister = old - new`, `to_register = new - old`,
//! `unchanged = old ∩ new`. Registration is transactional: on any failure, roll back
//! newly-added combos and re-register the ones just removed. `unchanged` combos suffer
//! zero churn (avoids the 1409 re-registration collision). Pure + testable; the actual
//! `RegisterHotKey`/`UnregisterHotKey` FFI lives in the Win32 module.

use serde::{Deserialize, Serialize};
use std::collections::BTreeSet;

/// A hotkey combination. Modeled by the virtual-key code and modifiers.
#[derive(Debug, Clone, PartialEq, Eq, PartialOrd, Ord, Hash, Serialize, Deserialize)]
pub struct Hotkey {
    /// Virtual-key code (e.g. 0x78 = F9).
    pub vk: u16,
    /// MOD_CONTROL=0x2, MOD_SHIFT=0x4, MOD_ALT=0x1, MOD_WIN=0x8 bitmask.
    pub modifiers: u16,
    /// Human-readable shortcut (e.g. "Shift+F9"), for logging/OSD.
    pub label: String,
}

/// The computed registration diff. `unchanged` entries are never touched.
#[derive(Debug, Clone, PartialEq, Eq, Default)]
pub struct HotkeyDiff {
    pub to_register: Vec<Hotkey>,
    pub to_unregister: Vec<Hotkey>,
    pub unchanged: Vec<Hotkey>,
}

/// Set algebra: `to_register = new - old`, `to_unregister = old - new`,
/// `unchanged = old ∩ new`. Membership is by `(vk, modifiers)`, ignoring label.
pub fn diff_hotkeys(old: &[Hotkey], new: &[Hotkey]) -> HotkeyDiff {
    let old_ids: BTreeSet<(u16, u16)> = old.iter().map(|h| (h.vk, h.modifiers)).collect();
    let new_ids: BTreeSet<(u16, u16)> = new.iter().map(|h| (h.vk, h.modifiers)).collect();

    let to_register = new.iter().filter(|h| !old_ids.contains(&(h.vk, h.modifiers))).cloned().collect();
    let to_unregister = old.iter().filter(|h| !new_ids.contains(&(h.vk, h.modifiers))).cloned().collect();
    let unchanged = old.iter().filter(|h| new_ids.contains(&(h.vk, h.modifiers))).cloned().collect();

    HotkeyDiff { to_register, to_unregister, unchanged }
}

/// A registration step that is already executed. Transactional semantics:
/// the caller runs `to_unregister`, then `to_register`; if any step fails, it
/// rolls back the newly-added and re-registers the unregistered ones.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct RegistrationState {
    /// Currently registered (post-successful transaction).
    pub registered: Vec<Hotkey>,
}

impl RegistrationState {
    pub fn new() -> Self {
        RegistrationState { registered: Vec::new() }
    }

    /// Apply a diff transactionally. Returns Err on the first failed step;
    /// on error the state is rolled back to the pre-diff registrations.
    pub fn apply(&mut self, diff: &HotkeyDiff) -> Result<(), HotkeyError> {
        // Record what we unregister so we can re-register on rollback.
        let unregistered: Vec<Hotkey> = diff.to_unregister.clone();

        // 1. Unregister `to_unregister`. Register these only if absent first.
        for h in &diff.to_unregister {
            if let Err(e) = self.unregister_one(h) {
                self.rollback(&unregistered, &[]);
                return Err(e);
            }
        }

        // 2. Register `to_register`. If one fails, roll back newly-added and
        //    re-register the ones we just unregistered.
        let mut added: Vec<Hotkey> = Vec::new();
        for h in &diff.to_register {
            match self.register_one(h) {
                Ok(()) => added.push(h.clone()),
                Err(e) => {
                    // Roll back newly added, then re-register unregistered.
                    for a in &added {
                        let _ = self.unregister_one(a);
                    }
                    self.rollback(&unregistered, &[]);
                    return Err(e);
                }
            }
        }
        Ok(())
    }

    fn register_one(&mut self, h: &Hotkey) -> Result<(), HotkeyError> {
        // The real Win32 call resolves here; this is the logic stub.
        self.registered.push(h.clone());
        Ok(())
    }

    fn unregister_one(&mut self, h: &Hotkey) -> Result<(), HotkeyError> {
        self.registered.retain(|x| !(x.vk == h.vk && x.modifiers == h.modifiers));
        Ok(())
    }

    /// Roll back by re-registering `to_restore` entries (best-effort).
    fn rollback(&mut self, to_restore: &[Hotkey], _extra: &[Hotkey]) {
        for h in to_restore {
            if !self.registered.iter().any(|x| x.vk == h.vk && x.modifiers == h.modifiers) {
                self.registered.push(h.clone());
            }
        }
    }
}

/// Error for a failed registration step.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct HotkeyError {
    pub hotkey: Hotkey,
    pub message: String,
}

#[cfg(test)]
mod tests {
    use super::*;

    const MOD_SHIFT: u16 = 0x4;
    const MOD_CONTROL: u16 = 0x2;

    fn shift_f9() -> Hotkey { Hotkey { vk: 0x78, modifiers: MOD_SHIFT, label: "Shift+F9".into() } }
    fn ctrl_v() -> Hotkey { Hotkey { vk: 0x56, modifiers: MOD_CONTROL, label: "Ctrl+V".into() } }
    fn ctrl_shift_c() -> Hotkey { Hotkey { vk: 0x43, modifiers: MOD_CONTROL | MOD_SHIFT, label: "Ctrl+Shift+C".into() } }

    #[test]
    fn empty_to_empty_is_no_op() {
        let d = diff_hotkeys(&[], &[]);
        assert!(d.to_register.is_empty());
        assert!(d.to_unregister.is_empty());
        assert!(d.unchanged.is_empty());
    }

    #[test]
    fn adding_a_hotkey_only_registers_it() {
        let d = diff_hotkeys(&[], &[shift_f9()]);
        assert_eq!(d.to_register, vec![shift_f9()]);
        assert!(d.to_unregister.is_empty());
        assert!(d.unchanged.is_empty());
    }

    #[test]
    fn removing_a_hotkey_only_unregisters_it() {
        let d = diff_hotkeys(&[shift_f9()], &[]);
        assert!(d.to_register.is_empty());
        assert_eq!(d.to_unregister, vec![shift_f9()]);
        assert!(d.unchanged.is_empty());
    }

    #[test]
    fn unchanged_hotkey_is_zero_churn() {
        let d = diff_hotkeys(&[shift_f9()], &[shift_f9()]);
        assert!(d.to_register.is_empty());
        assert!(d.to_unregister.is_empty());
        assert_eq!(d.unchanged, vec![shift_f9()]);
    }

    #[test]
    fn mixed_diff_partitions_correctly() {
        let old = vec![shift_f9(), ctrl_v()];
        let new = vec![shift_f9(), ctrl_shift_c()];
        let d = diff_hotkeys(&old, &new);
        assert_eq!(d.to_register, vec![ctrl_shift_c()]);
        assert_eq!(d.to_unregister, vec![ctrl_v()]);
        assert_eq!(d.unchanged, vec![shift_f9()]);
    }

    #[test]
    fn diff_membership_ignores_label() {
        let old = vec![Hotkey { vk: 0x78, modifiers: MOD_SHIFT, label: "old label".into() }];
        let new = vec![shift_f9()];
        let d = diff_hotkeys(&old, &new);
        assert!(d.unchanged.iter().any(|h| h.vk == 0x78 && h.modifiers == MOD_SHIFT));
    }

    #[test]
    fn transactional_state_applies_cleanly() {
        let mut st = RegistrationState::new();
        let d = diff_hotkeys(&[], &[shift_f9(), ctrl_v()]);
        st.apply(&d).unwrap();
        assert_eq!(st.registered.len(), 2);
        // Now remove ctrl_v and add ctrl_shift_c (shift_f9 unchanged).
        let d2 = diff_hotkeys(&[shift_f9(), ctrl_v()], &[shift_f9(), ctrl_shift_c()]);
        st.apply(&d2).unwrap();
        assert_eq!(st.registered.len(), 2);
        assert!(st.registered.iter().any(|h| h.vk == 0x78));
        assert!(st.registered.iter().any(|h| h.vk == ctrl_shift_c().vk));
    }

    #[test]
    fn zero_churn_same_set() {
        let mut st = RegistrationState::new();
        st.apply(&diff_hotkeys(&[], &[shift_f9()])).unwrap();
        st.apply(&diff_hotkeys(&[shift_f9()], &[shift_f9()])).unwrap();
        assert_eq!(st.registered.len(), 1);
    }
}