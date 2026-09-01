//! Keyboard input classification (Phase 2b, §2b).
//!
//! The `WH_KEYBOARD_LL` hook must filter ONLY Stet's exact private `dwExtraInfo` tag.
//! It must NEVER discard every event with `LLKHF_INJECTED`: RDP user input is injected too.
//! This module is the pure classifier; the hook FFI feeds it tag + flags.

/// A Stet-synthesized `SendInput` event is tagged with this exact extra-info value.
pub const STET_DW_EXTRA_INFO: usize = 0x7374_6574; // "stet"

/// Input source classification.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum InputSource {
    /// Stet's own synthetic event (matching the exact dwExtraInfo tag).
    StetInjected,
    /// A user or remote-RDP physical input (may have LLKHF_INJECTED but is not Stet's).
    PhysicalOrRemote,
}

/// Classify a low-level keyboard event by its `dwExtraInfo` and injected flag.
/// We check ONLY the exact Stet tag; a generic injected flag does NOT make an event
/// `StetInjected` (RDP user input is injected too).
pub fn classify_input(dw_extra_info: usize, _injected: bool) -> InputSource {
    if dw_extra_info == STET_DW_EXTRA_INFO {
        InputSource::StetInjected
    } else {
        InputSource::PhysicalOrRemote
    }
}

/// Whether a modifier snapshot (physical/remote state) should update the tracker.
/// Stet's own synthetic modifier events are excluded from physical-state tracking.
pub fn should_track_modifier(source: InputSource) -> bool {
    source == InputSource::PhysicalOrRemote
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn exact_stet_tag_is_classified_stet_injected() {
        assert_eq!(classify_input(STET_DW_EXTRA_INFO, true), InputSource::StetInjected);
    }
    #[test]
    fn injected_flag_alone_is_not_stet_injected() {
        // RDP user input is injected (LLKHF_INJECTED) but is NOT Stet's.
        assert_eq!(classify_input(0, true), InputSource::PhysicalOrRemote);
    }
    #[test]
    fn physical_event_is_physical_or_remote() {
        assert_eq!(classify_input(0, false), InputSource::PhysicalOrRemote);
    }
    #[test]
    fn tracks_modifiers_only_for_physical_remote() {
        assert!(should_track_modifier(InputSource::PhysicalOrRemote));
        assert!(!should_track_modifier(InputSource::StetInjected));
    }
    #[test]
    fn stet_tag_is_distinct_from_zero() {
        assert_ne!(STET_DW_EXTRA_INFO, 0);
    }
}
