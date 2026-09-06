//! Daemon-side capture/paste orchestration (native Rust core).
//!
//! Ports the Win32 clipboard fallback from `stet/core/app.py::_capture_selection`
//! and the paste discipline from `StetApp::_paste_text` / the silent worker:
//!
//! * Terminal guard: same class/process keyword lists and the same fail-closed
//!   rule as `_is_terminal_or_ide` (null hwnd / PID 0 is safe; access-denied or
//!   unreadable identity of a live process is a terminal; only a
//!   positively-identified non-terminal takes the plain-Ctrl path).
//! * Capture: snapshot clipboard text+sequence, send the copy chord
//!   (Ctrl+Shift+C on terminals — the terminal branch NEVER sends plain
//!   Ctrl+C, which would SIGINT the foreground job), poll for a change until
//!   the timeout, restore the original text, return the text ("" = no sel.).
//! * Paste: optionally verify a live eligible foreground target, write the
//!   clipboard, send Ctrl+V (Ctrl+Shift+V on terminals), observe consumption,
//!   restore the original clipboard only when it still holds our text.
//!
//! Timing mirrors the Python tunables (`_CLIPBOARD_INITIAL_GRACE`,
//! `_CLIPBOARD_MAX_POLLS`, `_CLIPBOARD_POLL_INTERVAL`).
//!
//! The OS boundary is the [`CaptureEnv`] trait. Real Win32 I/O lives in the
//! `cfg(windows)` `WinEnv`; unit tests drive the pure orchestration
//! ([`capture_with_env`], [`paste_with_env`]) with fakes — no Windows needed.
//! There is no `unwrap`/`expect`/panic path outside tests.

use crate::clipboard::ClipboardRestoreGate;
use crate::focus_target::{classify_foreground, TargetEligibility};
use crate::frame::IpcError;
use crate::paste_observe::{ConsumptionVerdict, PasteObserver};

// ── timing (mirrors stet/core/app.py) ──────────────────────────────────────

/// Grace after injecting the copy chord before the first poll (50 ms).
pub const CLIPBOARD_INITIAL_GRACE_MS: u64 = 50;
/// Interval between clipboard polls (15 ms).
pub const CLIPBOARD_POLL_INTERVAL_MS: u64 = 15;
/// Maximum clipboard polls per capture (12 attempts).
pub const CLIPBOARD_MAX_POLLS: u32 = 12;
/// Settle after writing the clipboard before injecting the paste chord.
pub const PASTE_WRITE_SETTLE_MS: u64 = 150;
/// Settle after the paste chord before observing consumption.
pub const PASTE_POST_CHORD_MS: u64 = 100;
/// Settle after clearing the clipboard before verifying the clear.
pub const CAPTURE_CLEAR_SETTLE_MS: u64 = 50;
/// Default capture timeout when the caller passes none (mirrors the 1500 ms
/// the Python client sends).
pub const DEFAULT_CAPTURE_TIMEOUT_MS: u64 = 1500;

// ── terminal guard (pure; mirrors _is_terminal_or_ide) ─────────────────────

/// Window-class substrings (lowercase) identifying terminal emulators.
/// Mirrors `terminal_class_keywords` in `stet/core/app.py`.
pub const TERMINAL_CLASS_KEYWORDS: &[&str] = &[
    "consolewindowclass",
    "cascadiahostingwindowclass",
    "cascadia",
    "vte",
    "mintty",
    "conhost",
    "putty",
];

/// Process image-file substrings (lowercase) identifying terminal emulators
/// and shells. IDEs, editors and runtimes are intentionally NOT listed —
/// Ctrl+C is a safe copy there. Mirrors `terminal_process_keywords`.
pub const TERMINAL_PROCESS_KEYWORDS: &[&str] = &[
    "cmd.exe",
    "powershell.exe",
    "pwsh.exe",
    "conhost.exe",
    "wt.exe",
    "windowsterminal.exe",
    "wsl.exe",
    "bash.exe",
    "sh.exe",
    "zsh.exe",
    "fish.exe",
    "git-bash.exe",
    "wezterm.exe",
    "wezterm-gui.exe",
    "alacritty.exe",
    "hyper.exe",
    "tabby.exe",
    "cmder.exe",
    "conemu.exe",
    "conemu64.exe",
    "kitty.exe",
    "mintty.exe",
];

/// Liveness of the foreground process behind a window, as established by the
/// OpenProcess / QueryFullProcessImageNameW probe.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum PidState {
    /// No foreground window (null hwnd): nothing to disturb, never terminal.
    NoWindow,
    /// PID 0, stale handle, or the process is already gone: nothing to harm.
    Dead,
    /// OpenProcess denied (ERROR_ACCESS_DENIED): a live process we may not
    /// inspect — typically an elevated terminal. Fail closed.
    AccessDenied,
    /// The process is alive (open handle) but its image is unreadable.
    /// Fail closed.
    LiveUnreadable,
    /// Positively identified live process; keyword lists decide.
    Known(u32),
}

/// Pure terminal-guard decision over a lowercased-agnostic class name, a
/// process image file name, and the process liveness state.
///
/// * `NoWindow` / `Dead` → false (no process to harm).
/// * `AccessDenied` / `LiveUnreadable` → true (fail closed).
/// * `Known` → true only when a class or process keyword matches.
pub fn is_terminal_window(class: &str, proc_name: &str, pid: PidState) -> bool {
    match pid {
        PidState::NoWindow | PidState::Dead => false,
        PidState::AccessDenied | PidState::LiveUnreadable => true,
        PidState::Known(_) => {
            let lower_class = class.to_lowercase();
            if TERMINAL_CLASS_KEYWORDS.iter().any(|kw| lower_class.contains(kw)) {
                return true;
            }
            let lower_proc = proc_name.to_lowercase();
            if TERMINAL_PROCESS_KEYWORDS.iter().any(|kw| lower_proc.contains(kw)) {
                return true;
            }
            false
        }
    }
}

/// Extract the image file name from a full process image path
/// (`C:\Windows\System32\cmd.exe` → `cmd.exe`). Never fails.
pub fn proc_file_name(image_path: &str) -> &str {
    match image_path.rsplit(['\\', '/']).next() {
        Some(name) => name,
        None => "",
    }
}

// ── foreground-target verification (pure) ──────────────────────────────────

/// Pure paste-target check: the foreground window must be a live, eligible
/// external target — never Stet-owned, never an implausible shell surface.
pub fn verify_foreground_target(class: &str, image_path: &str, pid: u32, own_pid: u32) -> bool {
    matches!(
        classify_foreground(image_path, class, pid, own_pid),
        TargetEligibility::EligibleExternal
    )
}

// ── OS boundary ────────────────────────────────────────────────────────────

/// The Win32 surface the capture/paste orchestration needs. Implemented by
/// the real `WinEnv` on Windows and by fakes in tests.
pub trait CaptureEnv {
    /// Current clipboard Unicode text ("" when empty or unreadable).
    fn clipboard_text(&mut self) -> String;
    /// Current clipboard sequence number.
    fn clipboard_seq(&mut self) -> u64;
    /// Replace the clipboard text. False when the clipboard is locked.
    fn write_clipboard(&mut self, text: &str) -> bool;
    /// True when the foreground window needs the terminal-safe chord.
    fn is_terminal(&mut self) -> bool;
    /// Inject the copy chord. `terminal == true` MUST send Ctrl+Shift+C;
    /// `false` sends plain Ctrl+C (with held-modifier release).
    fn send_copy_chord(&mut self, terminal: bool);
    /// Inject the paste chord (Ctrl+Shift+V on terminals, Ctrl+V elsewhere).
    fn send_paste_chord(&mut self, terminal: bool);
    /// True when a live, eligible external window owns the foreground now.
    fn foreground_eligible(&mut self) -> bool;
    /// Sleep (drives the poll timing; fakes record instead of sleeping).
    fn sleep_ms(&mut self, ms: u64);
    /// Attempt direct text capture via UI Automation (bypassing clipboard & chords).
    fn capture_uia(&mut self) -> Option<String> {
        None
    }
}

/// Daemon command surface used by the dispatcher.
pub trait CaptureAgent {
    /// Copy the current selection; "" means no selection. Never fakes success.
    fn capture_selection(&self, timeout_ms: u64) -> Result<String, IpcError>;
    /// Paste `text`; Ok holds the pasted char count. Never fakes success.
    fn paste_text(&self, text: &str, verify_target: bool) -> Result<usize, IpcError>;
}

// ── orchestration (platform-independent) ───────────────────────────────────

/// Capture orchestration over any [`CaptureEnv`]. Restores the original
/// clipboard (gated on no external change since the last read) on every path
/// and returns the captured text, or "" when no selection appeared.
pub fn capture_with_env(env: &mut impl CaptureEnv, timeout_ms: u64) -> Result<String, IpcError> {
    if timeout_ms == 0 {
        return Ok(String::new());
    }
    // 1. Direct UIA capture first — completely bypasses clipboard and synthetic keys.
    if let Some(uia_text) = env.capture_uia() {
        if !uia_text.is_empty() {
            return Ok(uia_text);
        }
    }
    let terminal = env.is_terminal();
    let old = env.clipboard_text();

    if terminal {
        // Terminal path — never clear the clipboard first: clearing deselects
        // text in terminal apps, so the copy chord would capture nothing.
        // The terminal branch MUST NEVER send plain Ctrl+C.
        env.send_copy_chord(true);
    } else {
        // Non-terminal path — clear and verify the clear (retry once), so a
        // stuck clipboard lock cannot surface stale content as "selection".
        env.write_clipboard("");
        env.sleep_ms(CAPTURE_CLEAR_SETTLE_MS);
        if !env.clipboard_text().is_empty() {
            env.sleep_ms(CAPTURE_CLEAR_SETTLE_MS);
            env.write_clipboard("");
            env.sleep_ms(CAPTURE_CLEAR_SETTLE_MS);
        }
        env.send_copy_chord(false);
    }

    env.sleep_ms(CLIPBOARD_INITIAL_GRACE_MS);
    let mut spent = CLIPBOARD_INITIAL_GRACE_MS;
    let mut polls: u32 = 0;
    let mut found: Option<String> = None;
    let mut last_seq = env.clipboard_seq();
    while polls < CLIPBOARD_MAX_POLLS && spent < timeout_ms {
        env.sleep_ms(CLIPBOARD_POLL_INTERVAL_MS);
        spent = spent.saturating_add(CLIPBOARD_POLL_INTERVAL_MS);
        polls = polls.saturating_add(1);
        let current = env.clipboard_text();
        last_seq = env.clipboard_seq();
        let hit = if terminal {
            !current.is_empty() && current != old
        } else {
            !current.is_empty()
        };
        if hit {
            found = Some(current);
            break;
        }
    }

    restore_when_unchanged(env, &old, last_seq);
    match found {
        Some(text) => Ok(text),
        None => Ok(String::new()),
    }
}

/// Paste orchestration over any [`CaptureEnv`]. Returns the pasted char count.
/// The original clipboard is restored only when it still holds our text (and
/// the restore gate sees no external change); an externally-changed clipboard
/// is left untouched and the paste still reports success.
pub fn paste_with_env(
    env: &mut impl CaptureEnv,
    text: &str,
    verify_target: bool,
) -> Result<usize, IpcError> {
    if text.is_empty() {
        return Ok(0);
    }
    if verify_target && !env.foreground_eligible() {
        return Err(IpcError::AbortedWrongTarget);
    }
    let old = env.clipboard_text();
    if !env.write_clipboard(text) {
        return Err(IpcError::Internal);
    }
    let post_write_seq = env.clipboard_seq();
    env.sleep_ms(PASTE_WRITE_SETTLE_MS);
    let terminal = env.is_terminal();
    env.send_paste_chord(terminal);
    env.sleep_ms(PASTE_POST_CHORD_MS);

    let current = env.clipboard_text();
    let current_seq = env.clipboard_seq();
    let elapsed = PASTE_WRITE_SETTLE_MS.saturating_add(PASTE_POST_CHORD_MS);
    let verdict = PasteObserver::new(5000, 10000).observe(current == text, elapsed);
    if verdict == ConsumptionVerdict::Verified {
        let mut gate = ClipboardRestoreGate::new();
        gate.record_post_write(post_write_seq);
        if gate.decide(current_seq) == crate::clipboard::RestoreDecision::Restore && old != text {
            env.write_clipboard(&old);
        }
    }
    // Unverified (clipboard changed externally): leave it untouched, exactly
    // like the Python `_restore_if_unchanged` discipline.
    Ok(text.chars().count())
}

/// Restore `original` only when the clipboard sequence still equals the last
/// sequence we read (no external change since). Best-effort, never errors.
fn restore_when_unchanged(env: &mut impl CaptureEnv, original: &str, last_seq: u64) {
    let current_seq = env.clipboard_seq();
    let gate = ClipboardRestoreGate::new();
    if gate.decide_capture_fallback(current_seq, last_seq)
        == crate::clipboard::RestoreDecision::Restore
    {
        env.write_clipboard(original);
    }
}

// ── real agent ─────────────────────────────────────────────────────────────

/// Production agent: real Win32 I/O on Windows; typed refusal elsewhere.
pub struct RealAgent;

impl RealAgent {
    pub fn new() -> Self {
        RealAgent
    }
}

impl Default for RealAgent {
    fn default() -> Self {
        RealAgent
    }
}

impl CaptureAgent for RealAgent {
    #[cfg(windows)]
    fn capture_selection(&self, timeout_ms: u64) -> Result<String, IpcError> {
        capture_with_env(&mut WinEnv, timeout_ms)
    }

    #[cfg(not(windows))]
    fn capture_selection(&self, _timeout_ms: u64) -> Result<String, IpcError> {
        Err(IpcError::Internal)
    }

    #[cfg(windows)]
    fn paste_text(&self, text: &str, verify_target: bool) -> Result<usize, IpcError> {
        paste_with_env(&mut WinEnv, text, verify_target)
    }

    #[cfg(not(windows))]
    fn paste_text(&self, _text: &str, _verify_target: bool) -> Result<usize, IpcError> {
        Err(IpcError::Internal)
    }
}

// ── real Win32 environment (Windows only) ──────────────────────────────────

#[cfg(windows)]
struct WinEnv;

#[cfg(windows)]
mod win_impl {
    use super::{proc_file_name, verify_foreground_target, PidState};
    use super::{is_terminal_window, CaptureEnv};

    pub(super) const VK_LWIN: u32 = 0x5B;
    pub(super) const VK_RWIN: u32 = 0x5C;
    const MOD_WAIT_TIMEOUT_MS: u64 = 200;
    const MOD_WAIT_STEP_MS: u64 = 10;

    pub(super) fn wait_mods_released(mods: &[u32]) {
        let mut waited: u64 = 0;
        while waited < MOD_WAIT_TIMEOUT_MS {
            let held = mods.iter().any(|m| ffi_skel::input::is_async_key_down(*m));
            if !held {
                return;
            }
            std::thread::sleep(std::time::Duration::from_millis(MOD_WAIT_STEP_MS));
            waited = waited.saturating_add(MOD_WAIT_STEP_MS);
        }
    }

    /// Build + inject one Ctrl(+Shift)+vk chord, all Stet-tagged, as a single
    /// SendInput batch. `terminal` selects Ctrl+Shift+vk; otherwise any
    /// physically-held Shift/Alt/Win is released first (mirror of
    /// `_send_ctrl_chord` / `_send_ctrl_shift_chord` in stet/core/clipboard.py).
    pub(super) fn send_chord(terminal: bool, vk: u32) {
        use ffi_skel::input::{is_async_key_down, make_keyboard_input, send_events};
        use ffi_skel::types::{STET_DW_EXTRA_INFO, VK_CONTROL, VK_MENU, VK_SHIFT};
        let tag = STET_DW_EXTRA_INFO;
        unsafe {
            let mut inputs = Vec::with_capacity(8);
            if terminal {
                wait_mods_released(&[VK_MENU, VK_LWIN, VK_RWIN]);
                for m in [VK_MENU, VK_LWIN, VK_RWIN] {
                    if is_async_key_down(m) {
                        inputs.push(make_keyboard_input(m, 0, true, false, tag));
                    }
                }
                inputs.push(make_keyboard_input(VK_CONTROL, 0, false, false, tag));
                inputs.push(make_keyboard_input(VK_SHIFT, 0, false, false, tag));
                inputs.push(make_keyboard_input(vk, 0, false, false, tag));
                inputs.push(make_keyboard_input(vk, 0, true, false, tag));
                inputs.push(make_keyboard_input(VK_SHIFT, 0, true, false, tag));
                inputs.push(make_keyboard_input(VK_CONTROL, 0, true, false, tag));
            } else {
                wait_mods_released(&[VK_SHIFT, VK_MENU, VK_LWIN, VK_RWIN]);
                for m in [VK_SHIFT, VK_MENU, VK_LWIN, VK_RWIN] {
                    if is_async_key_down(m) {
                        inputs.push(make_keyboard_input(m, 0, true, false, tag));
                    }
                }
                inputs.push(make_keyboard_input(VK_CONTROL, 0, false, false, tag));
                inputs.push(make_keyboard_input(vk, 0, false, false, tag));
                inputs.push(make_keyboard_input(vk, 0, true, false, tag));
                inputs.push(make_keyboard_input(VK_CONTROL, 0, true, false, tag));
            }
            send_events(&inputs);
        }
    }

    pub(super) fn live_is_terminal() -> bool {
        let hwnd = ffi_skel::window::foreground_window();
        if hwnd == 0 {
            return false;
        }
        let class = ffi_skel::window::window_class(hwnd);
        let (state, name) = match ffi_skel::window::window_pid(hwnd) {
            None => (PidState::Dead, String::new()),
            Some(pid) => match ffi_skel::window::process_image_name(pid) {
                Ok(path) => (PidState::Known(pid), proc_file_name(&path).to_string()),
                Err(code) if code == ffi_skel::types::ERROR_ACCESS_DENIED => {
                    (PidState::AccessDenied, String::new())
                }
                Err(code) if code == ffi_skel::window::IDENTITY_UNREADABLE => {
                    (PidState::LiveUnreadable, String::new())
                }
                Err(_) => (PidState::Dead, String::new()),
            },
        };
        is_terminal_window(&class, &name, state)
    }

    pub(super) fn live_foreground_eligible() -> bool {
        let hwnd = ffi_skel::window::foreground_window();
        if hwnd == 0 {
            return false;
        }
        let class = ffi_skel::window::window_class(hwnd);
        let pid = match ffi_skel::window::window_pid(hwnd) {
            Some(p) => p,
            None => return false,
        };
        // Unreadable identity of a live process fails closed: not eligible.
        let image = match ffi_skel::window::process_image_name(pid) {
            Ok(path) => path,
            Err(_) => return false,
        };
        verify_foreground_target(&class, &image, pid, std::process::id())
    }

    impl CaptureEnv for super::WinEnv {
        fn clipboard_text(&mut self) -> String {
            match ffi_skel::clipboard::get_clipboard_unicode() {
                Some(units) => String::from_utf16_lossy(&units),
                None => String::new(),
            }
        }

        fn clipboard_seq(&mut self) -> u64 {
            ffi_skel::clipboard::clipboard_sequence() as u64
        }

        fn write_clipboard(&mut self, text: &str) -> bool {
            let units: Vec<u16> = text.encode_utf16().collect();
            unsafe {
                if !ffi_skel::clipboard::open_clipboard() {
                    return false;
                }
                let ok = ffi_skel::clipboard::empty_clipboard()
                    && ffi_skel::clipboard::set_clipboard_unicode(
                        units.as_ptr(),
                        units.len(),
                    );
                // Privacy (§0f/§2d): suppress Win+V history and cloud sync on
                // every daemon clipboard write. Best-effort, like the Python
                // path — a suppression failure never fails the text write.
                if ok {
                    ffi_skel::clipboard::set_privacy_suppression();
                }
                ffi_skel::clipboard::close_clipboard();
                ok
            }
        }

        fn is_terminal(&mut self) -> bool {
            live_is_terminal()
        }

        fn send_copy_chord(&mut self, terminal: bool) {
            send_chord(
                terminal,
                ffi_skel::types::VK_C,
            );
        }

        fn send_paste_chord(&mut self, terminal: bool) {
            send_chord(
                terminal,
                ffi_skel::types::VK_V,
            );
        }

        fn foreground_eligible(&mut self) -> bool {
            live_foreground_eligible()
        }

        fn sleep_ms(&mut self, ms: u64) {
            std::thread::sleep(std::time::Duration::from_millis(ms));
        }

        fn capture_uia(&mut self) -> Option<String> {
            let hwnd = ffi_skel::window::foreground_window();
            let pid = ffi_skel::window::window_pid(hwnd).unwrap_or(0);
            let req = stet_uia_broker::protocol::BrokerRequest {
                pid,
                window_handle: hwnd as u64,
                request_id: 1,
                deadline_ms: 250,
            };
            let resp = stet_uia_broker::handle_broker_request(&req);
            if resp.outcome.is_success() {
                resp.text
            } else {
                None
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::cell::{Cell, RefCell};

    // ── guard truth table ──────────────────────────────────────────────

    #[test]
    fn null_window_is_never_terminal() {
        assert!(!is_terminal_window("ConsoleWindowClass", "cmd.exe", PidState::NoWindow));
    }

    #[test]
    fn dead_pid_is_never_terminal() {
        assert!(!is_terminal_window("ConsoleWindowClass", "cmd.exe", PidState::Dead));
    }

    #[test]
    fn access_denied_fails_closed() {
        assert!(is_terminal_window("", "", PidState::AccessDenied));
        assert!(is_terminal_window("Notepad", "notepad.exe", PidState::AccessDenied));
    }

    #[test]
    fn unreadable_live_identity_fails_closed() {
        assert!(is_terminal_window("", "", PidState::LiveUnreadable));
        assert!(is_terminal_window("Chrome_WidgetWin_1", "chrome.exe", PidState::LiveUnreadable));
    }

    #[test]
    fn terminal_class_matches() {
        assert!(is_terminal_window(
            "ConsoleWindowClass",
            "notepad.exe",
            PidState::Known(1)
        ));
        assert!(is_terminal_window(
            "CASCADIAHOSTINGWINDOWCLASS",
            "wt.exe",
            PidState::Known(1)
        ));
        assert!(is_terminal_window("mintty", "bash.exe", PidState::Known(1)));
    }

    #[test]
    fn terminal_process_matches() {
        for proc in ["cmd.exe", "powershell.exe", "pwsh.exe", "conhost.exe", "wt.exe"] {
            assert!(
                is_terminal_window("SomeClass", proc, PidState::Known(1)),
                "{proc} should be terminal"
            );
        }
        for proc in ["bash.exe", "zsh.exe", "wezterm-gui.exe", "alacritty.exe", "kitty.exe"] {
            assert!(
                is_terminal_window("SomeClass", proc, PidState::Known(1)),
                "{proc} should be terminal"
            );
        }
    }

    #[test]
    fn ides_and_runtimes_are_not_terminals() {
        for proc in [
            "code.exe",
            "notepad.exe",
            "chrome.exe",
            "python.exe",
            "node.exe",
            "antigravity.exe",
        ] {
            assert!(
                !is_terminal_window("Chrome_WidgetWin_1", proc, PidState::Known(1)),
                "{proc} should NOT be terminal"
            );
        }
    }

    #[test]
    fn matching_is_case_insensitive() {
        assert!(is_terminal_window(
            "consolewindowclass",
            "C:\\Windows\\System32\\CMD.EXE",
            PidState::Known(1)
        ));
    }

    #[test]
    fn proc_file_name_extracts_basename() {
        assert_eq!(proc_file_name("C:\\Windows\\System32\\cmd.exe"), "cmd.exe");
        assert_eq!(proc_file_name("/usr/bin/bash.exe"), "bash.exe");
        assert_eq!(proc_file_name("wt.exe"), "wt.exe");
        assert_eq!(proc_file_name(""), "");
    }

    #[test]
    fn foreground_target_accepts_external_rejects_owned() {
        assert!(verify_foreground_target(
            "Chrome_WidgetWin_1",
            "C:\\App\\Code.exe",
            4000,
            12345
        ));
        assert!(!verify_foreground_target(
            "Window",
            "C:\\stet-core.exe",
            12345,
            12345
        ));
        assert!(!verify_foreground_target(
            "Progman",
            "C:\\Windows\\explorer.exe",
            2000,
            12345
        ));
    }

    // ── fake env ───────────────────────────────────────────────────────

    struct FakeEnv {
        texts: RefCell<Vec<String>>,
        seq: Cell<u64>,
        terminal: bool,
        eligible: bool,
        copy_chords: RefCell<Vec<bool>>,
        paste_chords: RefCell<Vec<bool>>,
        writes: RefCell<Vec<String>>,
        sleeps: RefCell<Vec<u64>>,
        uia_text: RefCell<Option<String>>,
    }

    impl FakeEnv {
        fn new(texts: Vec<&str>, terminal: bool) -> Self {
            FakeEnv {
                texts: RefCell::new(texts.into_iter().map(str::to_string).collect()),
                seq: Cell::new(7),
                terminal,
                eligible: true,
                copy_chords: RefCell::new(Vec::new()),
                paste_chords: RefCell::new(Vec::new()),
                writes: RefCell::new(Vec::new()),
                sleeps: RefCell::new(Vec::new()),
                uia_text: RefCell::new(None),
            }
        }

        fn pop_text(&self) -> String {
            let mut t = self.texts.borrow_mut();
            if t.len() > 1 {
                t.remove(0)
            } else {
                t.first().cloned().unwrap_or_default()
            }
        }
    }

    impl CaptureEnv for FakeEnv {
        fn clipboard_text(&mut self) -> String {
            self.pop_text()
        }
        fn clipboard_seq(&mut self) -> u64 {
            let s = self.seq.get();
            self.seq.set(s.saturating_add(1));
            s
        }
        fn write_clipboard(&mut self, text: &str) -> bool {
            self.writes.borrow_mut().push(text.to_string());
            true
        }
        fn is_terminal(&mut self) -> bool {
            self.terminal
        }
        fn send_copy_chord(&mut self, terminal: bool) {
            self.copy_chords.borrow_mut().push(terminal);
        }
        fn send_paste_chord(&mut self, terminal: bool) {
            self.paste_chords.borrow_mut().push(terminal);
        }
        fn foreground_eligible(&mut self) -> bool {
            self.eligible
        }
        fn sleep_ms(&mut self, ms: u64) {
            self.sleeps.borrow_mut().push(ms);
        }
        fn capture_uia(&mut self) -> Option<String> {
            self.uia_text.borrow_mut().take()
        }
    }

    // The fake bumps the sequence on every read, so pinning restores requires
    // a steady-sequence fake.
    struct SteadyEnv {
        inner: FakeEnv,
    }

    impl SteadyEnv {
        fn new(texts: Vec<&str>, terminal: bool) -> Self {
            SteadyEnv { inner: FakeEnv::new(texts, terminal) }
        }
    }

    impl CaptureEnv for SteadyEnv {
        fn clipboard_text(&mut self) -> String {
            self.inner.pop_text()
        }
        fn clipboard_seq(&mut self) -> u64 {
            7
        }
        fn write_clipboard(&mut self, text: &str) -> bool {
            self.inner.write_clipboard(text)
        }
        fn is_terminal(&mut self) -> bool {
            self.inner.terminal
        }
        fn send_copy_chord(&mut self, terminal: bool) {
            self.inner.send_copy_chord(terminal);
        }
        fn send_paste_chord(&mut self, terminal: bool) {
            self.inner.send_paste_chord(terminal);
        }
        fn foreground_eligible(&mut self) -> bool {
            self.inner.eligible
        }
        fn sleep_ms(&mut self, ms: u64) {
            self.inner.sleep_ms(ms);
        }
        fn capture_uia(&mut self) -> Option<String> {
            self.inner.capture_uia()
        }
    }
    #[test]
    fn uia_capture_succeeds_without_sending_chords() {
        let mut env = FakeEnv::new(vec!["old text"], false);
        *env.uia_text.borrow_mut() = Some("uia direct captured text".to_string());
        let text = capture_with_env(&mut env, 1500).unwrap();
        assert_eq!(text, "uia direct captured text");
        assert!(env.copy_chords.borrow().is_empty());
        assert!(env.writes.borrow().is_empty());
    }

    #[test]
    fn capture_nonterminal_sends_plain_chord_and_returns_text() {
        // old="" then clear-verify reads "" then poll yields "sel".
        let mut env = SteadyEnv::new(vec!["", "", "", "sel"], false);
        let out = capture_with_env(&mut env, 1500).unwrap();
        assert_eq!(out, "sel");
        assert_eq!(*env.inner.copy_chords.borrow(), vec![false]);
        // Original restored after success.
        assert!(env.inner.writes.borrow().contains(&String::new()));
    }
    #[test]
    fn capture_terminal_never_sends_plain_ctrl_c() {
        // old="old", polls: "old" (unchanged) then "new".
        let mut env = SteadyEnv::new(vec!["old", "old", "new"], true);
        let out = capture_with_env(&mut env, 1500).unwrap();
        assert_eq!(out, "new");
        assert_eq!(*env.inner.copy_chords.borrow(), vec![true]);
        // Terminal path never clears first: no empty write before the chord.
        assert!(!env.inner.writes.borrow().iter().any(|w| w.is_empty()));
    }

    #[test]
    fn capture_terminal_ignores_unchanged_clipboard() {
        let mut env = SteadyEnv::new(vec!["same", "same", "same"], true);
        let out = capture_with_env(&mut env, 60).unwrap();
        assert_eq!(out, "");
        assert_eq!(*env.inner.copy_chords.borrow(), vec![true]);
    }

    #[test]
    fn capture_empty_when_no_selection() {
        let mut env = SteadyEnv::new(vec!["", ""], false);
        let out = capture_with_env(&mut env, 60).unwrap();
        assert_eq!(out, "");
    }

    #[test]
    fn capture_zero_timeout_touches_nothing() {
        let mut env = SteadyEnv::new(vec!["x"], false);
        let out = capture_with_env(&mut env, 0).unwrap();
        assert_eq!(out, "");
        assert!(env.inner.copy_chords.borrow().is_empty());
    }

    #[test]
    fn paste_reports_chars_and_restores() {
        // clipboard reads: old="orig", post-chord still holds pasted text.
        let mut env = SteadyEnv::new(vec!["orig", "new text"], false);
        let n = paste_with_env(&mut env, "new text", true).unwrap();
        assert_eq!(n, 8);
        assert_eq!(*env.inner.paste_chords.borrow(), vec![false]);
        assert!(env.inner.writes.borrow().contains(&"orig".to_string()));
    }

    #[test]
    fn paste_terminal_uses_shift_chord() {
        let mut env = SteadyEnv::new(vec!["orig", "t"], true);
        let n = paste_with_env(&mut env, "t", true).unwrap();
        assert_eq!(n, 1);
        assert_eq!(*env.inner.paste_chords.borrow(), vec![true]);
    }

    #[test]
    fn paste_aborts_on_wrong_target() {
        let mut env = SteadyEnv::new(vec!["orig", "t"], false);
        env.inner.eligible = false;
        let err = paste_with_env(&mut env, "t", true).unwrap_err();
        assert_eq!(err, IpcError::AbortedWrongTarget);
        assert!(env.inner.paste_chords.borrow().is_empty());
    }

    #[test]
    fn paste_skips_verify_when_disabled() {
        let mut env = SteadyEnv::new(vec!["orig", "t"], false);
        env.inner.eligible = false;
        let n = paste_with_env(&mut env, "t", false).unwrap();
        assert_eq!(n, 1);
    }

    #[test]
    fn paste_leaves_externally_changed_clipboard_alone() {
        // Post-chord read differs from pasted text: unverified, no restore.
        let mut env = SteadyEnv::new(vec!["orig", "someone-else"], false);
        let n = paste_with_env(&mut env, "new text", true).unwrap();
        assert_eq!(n, 8);
        assert!(!env.inner.writes.borrow().contains(&"orig".to_string()));
    }

    #[test]
    fn paste_empty_is_noop() {
        let mut env = SteadyEnv::new(vec!["orig"], false);
        let n = paste_with_env(&mut env, "", true).unwrap();
        assert_eq!(n, 0);
        assert!(env.inner.writes.borrow().is_empty());
    }
}
