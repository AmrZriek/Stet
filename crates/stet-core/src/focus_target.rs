//! WinEvent focus-tracker target selection (Phase 2f, §2f).
//!
//! SetWinEventHook (EVENT_SYSTEM_FOREGROUND / EVENT_OBJECT_FOCUS) tracks the
//! last external target. This module decides whether a foreground HWND is a legitimate
//! external target or a Stet-owned window (which must never be re-verified as a paste
//! target, per Phase 0b: never re-verify against a Stet own window). Pure + testable.

/// Whether a foreground window can be a paste/correct target.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum TargetEligibility {
    EligibleExternal,
    StetOwned,
    NotPlausible,
}

/// Classify a foreground window by its image path / window class / PID.
pub fn classify_foreground(image_path: &str, window_class: &str, pid: u32, stet_pid: u32) -> TargetEligibility {
    if pid == stet_pid {
        return TargetEligibility::StetOwned;
    }
    let lower_img = image_path.to_lowercase();
    let lower_cls = window_class.to_lowercase();
    if lower_img.contains("stet") || lower_img.contains("stet-core") || lower_img.contains("stet-launcher") {
        return TargetEligibility::StetOwned;
    }
    if lower_cls.contains("progman") || lower_cls.contains("workerw") || lower_cls.contains("shell_traywnd") {
        return TargetEligibility::NotPlausible;
    }
    TargetEligibility::EligibleExternal
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn stet_pid_window_is_never_a_target() {
        assert_eq!(classify_foreground("C:\\stet-core.exe", "Window", 12345, 12345), TargetEligibility::StetOwned);
    }
    #[test]
    fn stet_pid_with_generic_image_is_still_never_a_target() {
        // Isolates the PID check: same Stet PID but a generic path (no 'stet' substring).
        assert_eq!(classify_foreground("C:\\Tools\\mystery.exe", "Window", 12345, 12345), TargetEligibility::StetOwned);
    }
    #[test]
    fn stet_named_image_is_never_a_target_even_on_other_pid() {
        assert_eq!(classify_foreground("C:\\stet-launcher.exe", "Window", 999, 12345), TargetEligibility::StetOwned);
    }
    #[test]
    fn desktop_class_is_not_plausible() {
        assert_eq!(classify_foreground("C:\\Windows\\explorer.exe", "Progman", 2000, 12345), TargetEligibility::NotPlausible);
    }
    #[test]
    fn external_app_is_eligible() {
        assert_eq!(classify_foreground("C:\\Users\\me\\App\\Code.exe", "Chrome_WidgetWin_1", 4000, 12345), TargetEligibility::EligibleExternal);
    }
}