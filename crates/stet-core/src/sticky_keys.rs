//! Sticky/Filter Keys reconciliation (Phase 2b, §4.4).
//!
//! When Sticky Keys or Filter Keys are enabled, a physical modifier release does
//! NOT guarantee the target sees an unlatched logical modifier. This module
//! reconciles physical tracking with the logical GetKeyState state and warns once.
//! A conflicting modifier that is logically LATCHED must be treated as still down.

/// Accessibility-keys configuration bits (SPI_GETSTICKYKEYS / SPI_GETFILTERKEYS).
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub struct AccessibilityFlags {
    pub sticky_keys_on: bool,
    pub filter_keys_on: bool,
}

/// A modifier's combined physical + logical (latched) state.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct ModifierReality {
    /// Physically held (from the low-level hook).
    pub physical: bool,
    /// Logically latched (from GetKeyState — Sticky Keys latches a modifier).
    pub logical: bool,
}

/// Whether a modifier is effectively down for chord-safety decisions.
pub fn effectively_down(reality: ModifierReality, flags: AccessibilityFlags) -> bool {
    if flags.sticky_keys_on || flags.filter_keys_on {
        // With accessibility keys enabled, the LOGICAL state is authoritative:
        // a latched logical modifier is still down even if physical released.
        reality.logical || reality.physical
    } else {
        reality.physical
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn non_accessibility_uses_physical_only() {
        let flags = AccessibilityFlags { sticky_keys_on: false, filter_keys_on: false };
        // Physical up + logical latched: with no accessibility keys, effectively up.
        assert!(!effectively_down(ModifierReality { physical: false, logical: true }, flags));
        assert!(effectively_down(ModifierReality { physical: true, logical: false }, flags));
    }

    #[test]
    fn sticky_keys_latched_logical_keeps_modifier_down() {
        let flags = AccessibilityFlags { sticky_keys_on: true, filter_keys_on: false };
        // Physical released but logically latched: modifier still effectively down.
        assert!(effectively_down(ModifierReality { physical: false, logical: true }, flags));
    }

    #[test]
    fn filter_keys_also_respects_logical_state() {
        let flags = AccessibilityFlags { sticky_keys_on: false, filter_keys_on: true };
        assert!(effectively_down(ModifierReality { physical: false, logical: true }, flags));
    }

    #[test]
    fn fully_released_is_up_when_accessibility_on() {
        let flags = AccessibilityFlags { sticky_keys_on: true, filter_keys_on: true };
        assert!(!effectively_down(ModifierReality { physical: false, logical: false }, flags));
    }
}