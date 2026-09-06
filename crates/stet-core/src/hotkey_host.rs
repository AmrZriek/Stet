//! Live global-hotkey host: Rust owns RegisterHotKey by default.
//!
//! Python (`stet/core/app.py` ctypes + `WinHotkeyFilter`) is fallback only.
//! Rule: where a Rust implementation exists, it is the default; Python paths
//! are fallback/fallback-metrics, never primary.
//!
//! Layout: pure parts (shortcut parsing, spec decode, event-frame shape) are
//! platform-independent and unit-tested. The HWND + pump thread + pipe writes
//! compile on Windows only; elsewhere (and under `cfg(test)`) the host
//! reports `hosted: false` so callers fall back without side effects.

use serde::{Deserialize, Serialize};
use serde_json::{json, Value};

/// Win32 modifier bits (mirror `stet/core/app.py` MOD_*; NOREPEAT included).
pub const MOD_ALT: u16 = 0x0001;
pub const MOD_CONTROL: u16 = 0x0002;
pub const MOD_SHIFT: u16 = 0x0004;
pub const MOD_WIN: u16 = 0x0008;
pub const MOD_NOREPEAT: u16 = 0x4000;

/// One hosted hotkey: OS identity + the payload replayed on fire.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct HostSpec {
    /// Virtual-key code (e.g. 0x78 = F9).
    pub vk: u16,
    /// MOD_* bitmask including NOREPEAT.
    pub modifiers: u16,
    /// Human label, e.g. "Shift+F9".
    pub label: String,
    /// Correction mode replayed in `event.hotkey_fired` params.
    pub mode: String,
    /// Correction strength replayed in params.
    pub strength: String,
    /// Custom prompt replayed in params (empty when none).
    pub custom_prompt: String,
}

/// Parse the key (non-modifier) half of a shortcut into a VK code.
fn vk_for_key(key: &str) -> Option<u16> {
    let k = key.trim().to_lowercase();
    // F1..F24
    if k.len() > 1 && k.starts_with('f') {
        if let Ok(n) = k[1..].parse::<u16>() {
            if (1..=24).contains(&n) {
                return Some(0x70 + (n - 1));
            }
        }
        return None;
    }
    // a..z, 0..9
    if k.len() == 1 {
        let c = k.chars().next().unwrap();
        if ('a'..='z').contains(&c) {
            return Some(0x41 + (c as u16 - 'a' as u16));
        }
        if ('0'..='9').contains(&c) {
            return Some(0x30 + (c as u16 - '0' as u16));
        }
    }
    match k.as_str() {
        "space" => Some(0x20),
        "tab" => Some(0x09),
        "esc" | "escape" => Some(0x1B),
        "enter" | "return" => Some(0x0D),
        "backspace" => Some(0x08),
        "delete" | "del" => Some(0x2E),
        "insert" | "ins" => Some(0x2D),
        "home" => Some(0x24),
        "end" => Some(0x23),
        "pageup" | "pgup" => Some(0x21),
        "pagedown" | "pgdn" => Some(0x22),
        "up" => Some(0x26),
        "down" => Some(0x28),
        "left" => Some(0x25),
        "right" => Some(0x27),
        _ => None,
    }
}

/// Parse "ctrl+shift+f9" into `(vk, mods-including-NOREPEAT)`.
/// Mirrors `parse_hotkey_string` in `stet/core/app.py`.
pub fn parse_shortcut(shortcut: &str) -> Option<(u16, u16)> {
    let mut mods: u16 = MOD_NOREPEAT;
    let mut vk: Option<u16> = None;
    let mut any = false;
    for raw in shortcut.split('+') {
        let part = raw.trim().to_lowercase();
        if part.is_empty() {
            return None;
        }
        any = true;
        match part.as_str() {
            "ctrl" | "control" => mods |= MOD_CONTROL,
            "alt" => mods |= MOD_ALT,
            "shift" => mods |= MOD_SHIFT,
            "win" | "windows" | "super" => mods |= MOD_WIN,
            _ => {
                if vk.is_some() {
                    return None; // two keys: not a hotkey
                }
                vk = vk_for_key(&part);
                if vk.is_none() {
                    return None;
                }
            }
        }
    }
    if !any {
        return None;
    }
    vk.map(|v| (v, mods))
}

fn str_field(v: &Value, key: &str) -> String {
    v.get(key).and_then(Value::as_str).unwrap_or("").to_string()
}

/// Decode one RPC spec entry. Accepts the live shape
/// `{shortcut, mode, strength[, custom_prompt]}` and the legacy numeric
/// shape `{vk, modifiers, label[, mode, strength, custom_prompt]}`.
fn decode_entry(entry: &Value) -> Option<HostSpec> {
    if let Some(shortcut) = entry.get("shortcut").and_then(Value::as_str) {
        let (vk, mods) = parse_shortcut(shortcut)?;
        if vk == 0 {
            return None;
        }
        let mode = str_field(entry, "mode");
        let strength = str_field(entry, "strength");
        return Some(HostSpec {
            vk,
            modifiers: mods,
            label: shortcut.to_string(),
            mode: if mode.is_empty() { "panel".to_string() } else { mode },
            strength: if strength.is_empty() {
                "full_correction".to_string()
            } else {
                strength
            },
            custom_prompt: str_field(entry, "custom_prompt"),
        });
    }
    let vk = entry.get("vk").and_then(Value::as_u64)? as u16;
    let mut mods = entry.get("modifiers").and_then(Value::as_u64).unwrap_or(0) as u16;
    if vk == 0 {
        return None;
    }
    mods |= MOD_NOREPEAT;
    Some(HostSpec {
        vk,
        modifiers: mods,
        label: str_field(entry, "label"),
        mode: {
            let m = str_field(entry, "mode");
            if m.is_empty() { "panel".to_string() } else { m }
        },
        strength: {
            let s = str_field(entry, "strength");
            if s.is_empty() { "full_correction".to_string() } else { s }
        },
        custom_prompt: str_field(entry, "custom_prompt"),
    })
}

/// Split the `hotkeys` RPC array into `(hostable, failed_labels)`.
pub fn specs_from_json(hotkeys: &Value) -> (Vec<HostSpec>, Vec<String>) {
    let mut ok = Vec::new();
    let mut failed = Vec::new();
    let arr = match hotkeys.as_array() {
        Some(a) => a,
        None => return (ok, vec!["<non-array hotkeys>".to_string()]),
    };
    for entry in arr {
        match decode_entry(entry) {
            Some(spec) => ok.push(spec),
            None => failed.push(
                entry
                    .get("shortcut")
                    .and_then(Value::as_str)
                    .or_else(|| entry.get("label").and_then(Value::as_str))
                    .unwrap_or("?")
                    .to_string(),
            ),
        }
    }
    (ok, failed)
}

/// Build the server-push frame for a fired hotkey (no `id`: the Python
/// client routes id-less `method` frames to event handlers).
pub fn event_frame(spec: &HostSpec) -> Value {
    json!({
        "jsonrpc": "2.0",
        "method": "event.hotkey_fired",
        "params": {
            "mode": spec.mode,
            "strength": spec.strength,
            "custom_prompt": spec.custom_prompt,
            "shortcut": spec.label,
        }
    })
}

// ── live OS host (Windows daemon binary only) ──────────────────────────────
// The pump thread owns the message-only HWND: creation, registration and the
// pump all run there (RegisterHotKey delivers WM_HOTKEY to the creating
// thread). Set changes arriving on the serve thread update shared state and
// wake the pump with WM_NULL; reconcile itself runs in the wnd_proc, i.e. on
// the pump thread. Pipe writes from the pump share a process-wide mutex with
// the serve loop so reply and event frames never interleave mid-frame.

#[cfg(all(windows, not(test)))]
mod live {
    use super::HostSpec;
    use std::collections::{HashMap, HashSet};
    use std::sync::{
        atomic::{AtomicBool, Ordering},
        Mutex,
    };

    struct HostState {
        specs: Vec<HostSpec>,
        /// Message-only HWND as an integer (0 = none yet).
        hwnd: isize,
        pump_started: bool,
        /// Current client pipe handle as an integer (None = no session).
        session_handle: Option<isize>,
        /// (vk, modifiers) -> RegisterHotKey id.
        id_by_key: HashMap<(u16, u16), i32>,
        next_id: i32,
    }

    impl HostState {
        fn new() -> Self {
            HostState {
                specs: Vec::new(),
                hwnd: 0,
                pump_started: false,
                session_handle: None,
                id_by_key: HashMap::new(),
                next_id: 1000,
            }
        }
    }
    static STATE: std::sync::LazyLock<Mutex<HostState>> =
        std::sync::LazyLock::new(|| Mutex::new(HostState::new()));
    static WRITE_LOCK: std::sync::LazyLock<Mutex<()>> =
        std::sync::LazyLock::new(|| Mutex::new(()));
    static SESSION_LIVE: AtomicBool = AtomicBool::new(false);

    fn state() -> &'static Mutex<HostState> {
        &STATE
    }

    pub fn write_lock() -> &'static Mutex<()> {
        &WRITE_LOCK
    }
    fn wide(s: &str) -> Vec<u16> {
        s.encode_utf16().chain(std::iter::once(0)).collect()
    }

    fn write_event_frame(frame_bytes: &[u8], handle: isize) -> bool {
        let _guard = match write_lock().lock() {
            Ok(g) => g,
            Err(_) => return false,
        };
        unsafe {
            type HANDLE = *mut core::ffi::c_void;
            type DWORD = u32;
            let mut written: DWORD = 0;
            let mut off = 0usize;
            while off < frame_bytes.len() {
                let chunk = &frame_bytes[off..];
                let ok = WriteFile(
                    handle as HANDLE,
                    chunk.as_ptr() as *const core::ffi::c_void,
                    chunk.len() as DWORD,
                    &mut written,
                    core::ptr::null_mut(),
                );
                if ok == 0 || written == 0 {
                    return false;
                }
                off += written as usize;
            }
            true
        }
    }

    #[link(name = "user32")]
    extern "system" {
        fn PostMessageW(hWnd: isize, Msg: u32, wParam: usize, lParam: isize) -> i32;
    }

    #[link(name = "kernel32")]
    extern "system" {
        fn WriteFile(
            hFile: *mut core::ffi::c_void,
            lpBuffer: *const core::ffi::c_void,
            nNumberOfBytesToWrite: u32,
            lpNumberOfBytesWritten: *mut u32,
            lpOverlapped: *mut u8,
        ) -> i32;
    }

    unsafe extern "system" fn wnd_proc(
        hwnd: ffi_skel::types::HWND,
        msg: u32,
        wparam: usize,
        _lparam: isize,
    ) -> isize {
        use ffi_skel::window as W;
        const WM_NCCREATE: u32 = 0x0081;
        const WM_CREATE: u32 = 0x0001;
        if msg == W::WM_HOTKEY {
            fire_event(wparam as i32);
            return 0;
        }
        // Every other message is a chance to reconcile the registered set
        // with the latest specs (wake-ups arrive as WM_NULL).
        reconcile_locked(hwnd);
        // No DefWindowW in this import set: answer the create handshake
        // explicitly (message-only window needs nothing else dispatched).
        if msg == WM_NCCREATE || msg == WM_CREATE {
            return 1;
        }
        0
    }

    /// Must run on the pump thread (owns the HWND).
    unsafe fn reconcile_locked(hwnd: ffi_skel::types::HWND) {
        use ffi_skel::window as W;
        let specs = { state().lock().unwrap().specs.clone() };
        let want: HashSet<(u16, u16)> = specs.iter().map(|s| (s.vk, s.modifiers)).collect();
        let mut st = state().lock().unwrap();
        let stale: Vec<((u16, u16), i32)> = st
            .id_by_key
            .iter()
            .filter(|(k, _)| !want.contains(*k))
            .map(|(k, v)| (*k, *v))
            .collect();
        for (key, id) in stale {
            W::unregister_hotkey(hwnd, id);
            st.id_by_key.remove(&key);
        }
        for spec in &specs {
            let key = (spec.vk, spec.modifiers);
            if !st.id_by_key.contains_key(&key) {
                let id = st.next_id;
                st.next_id += 1;
                if W::register_hotkey(hwnd, id, spec.modifiers as u32, spec.vk as u32) {
                    st.id_by_key.insert(key, id);
                }
            }
        }
    }

    fn fire_event(id: i32) {
        let live = SESSION_LIVE.load(Ordering::SeqCst);
        if !live {
            eprintln!("stet-core: hotkey id={id} swallowed (no live session)");
            return;
        }
        let (spec, handle) = {
            let st = state().lock().unwrap();
            let handle = match st.session_handle {
                Some(h) => h,
                None => return,
            };
            let found = st
                .id_by_key
                .iter()
                .find(|(_, v)| **v == id)
                .and_then(|(k, _)| st.specs.iter().find(|s| (s.vk, s.modifiers) == *k))
                .cloned();
            match found {
                Some(s) => (s, handle),
                None => {
                    eprintln!("stet-core: hotkey id={id} not found in active specs");
                    return;
                }
            }
        };
        let frame = match crate::frame::Frame::encode(&super::event_frame(&spec)) {
            Ok(b) => b,
            Err(_) => {
                eprintln!("stet-core: hotkey id={id} dropped (encode failed)");
                return;
            }
        };
        if !write_event_frame(&frame, handle) {
            eprintln!("stet-core: hotkey id={id} dropped (pipe write failed)");
        } else {
            eprintln!("stet-core: hotkey id={id} fired ({})", spec.label);
        }
    }

    fn pump_entry() {
        unsafe {
            use ffi_skel::window as W;
            let cls = wide("StetHotkeyHost");
            let title = wide("StetHotkeyHost");
            let inst = W::current_module();
            let class = ffi_skel::types::WNDCLASSEXW {
                cbSize: core::mem::size_of::<ffi_skel::types::WNDCLASSEXW>() as u32,
                style: 0,
                lpfnWndProc: wnd_proc,
                cbClsExtra: 0,
                cbWndExtra: 0,
                hInstance: inst,
                hIcon: core::ptr::null_mut(),
                hCursor: core::ptr::null_mut(),
                hbrBackground: core::ptr::null_mut(),
                lpszMenuName: core::ptr::null(),
                lpszClassName: cls.as_ptr(),
                hIconSm: core::ptr::null_mut(),
            };
            if W::register_window_class(&class as *const _) == 0 {
                return;
            }
            let hwnd = W::create_message_only_window(cls.as_ptr(), title.as_ptr(), inst);
            if hwnd.is_null() {
                return;
            }
            {
                let mut st = state().lock().unwrap();
                st.hwnd = hwnd as isize;
            }
            reconcile_locked(hwnd);
            W::message_pump();
        }
    }

    fn wake_pump() {
        let hwnd = state().lock().unwrap().hwnd;
        if hwnd == 0 {
            return;
        }
        unsafe {
            const WM_NULL: u32 = 0x0000;
            PostMessageW(hwnd, WM_NULL, 0, 0);
        }
    }

    /// Replace the hosted set. Called on the serve thread; OS registration
    /// reconciles on the pump thread. Returns `(spec_count, [])`.
    pub fn apply_specs(specs: Vec<HostSpec>) -> (usize, Vec<String>) {
        let (start_pump, count) = {
            let mut st = state().lock().unwrap();
            st.specs = specs;
            let start = !st.pump_started;
            if start {
                st.pump_started = true;
            }
            (start, st.specs.len())
        };
        if start_pump {
            std::thread::Builder::new()
                .name("stet-hotkey-pump".into())
                .spawn(pump_entry)
                .ok();
        } else {
            wake_pump();
        }
        (count, Vec::new())
    }

    pub fn clear_host() {
        {
            state().lock().unwrap().specs.clear();
        }
        wake_pump();
    }

    pub fn hosted_count() -> usize {
        state().lock().unwrap().id_by_key.len()
    }

    pub fn set_session_handle(handle: Option<isize>) {
        eprintln!("stet-core: session live={}", handle.is_some());
        state().lock().unwrap().session_handle = handle;
        SESSION_LIVE.store(handle.is_some(), Ordering::SeqCst);
    }
}
#[cfg(all(windows, not(test)))]
pub use live::{apply_specs, clear_host, hosted_count, set_session_handle, write_lock};

#[cfg(any(not(windows), test))]
static DUMMY_WRITE_LOCK: std::sync::LazyLock<std::sync::Mutex<()>> =
    std::sync::LazyLock::new(|| std::sync::Mutex::new(()));

#[cfg(any(not(windows), test))]
pub fn write_lock() -> &'static std::sync::Mutex<()> {
    &DUMMY_WRITE_LOCK
}
/// Non-Windows / test builds: parse + report only, never touch the OS.
#[cfg(any(not(windows), test))]
pub fn apply_specs(specs: Vec<HostSpec>) -> (usize, Vec<String>) {
    let _ = specs;
    (0, Vec::new())
}

#[cfg(any(not(windows), test))]
pub fn clear_host() {}

#[cfg(any(not(windows), test))]
pub fn hosted_count() -> usize {
    0
}

#[cfg(any(not(windows), test))]
pub fn set_session_handle(_handle: Option<isize>) {}

/// Apply decoded specs to the live host; returns `(hosted_count, failed, hosted)`.
/// Dispatcher-facing entry: pure decode + host apply.
pub fn register_from_json(hotkeys: &Value) -> (usize, Vec<String>, bool) {
    let (specs, failed) = specs_from_json(hotkeys);
    if specs.is_empty() {
        return (0, failed, false);
    }
    let _ = apply_specs(specs);
    #[cfg(any(not(windows), test))]
    {
        // Parse-only builds never touch the OS: callers must fall back.
        return (0, failed, false);
    }
    #[cfg(all(windows, not(test)))]
    {
        // The pump registers asynchronously after spawn; wait boundedly for
        // proof (zero means the HWND/pump path is broken → fall back rather
        // than claim keys that will never fire).
        for _ in 0..50 {
            if hosted_count() > 0 {
                break;
            }
            std::thread::sleep(std::time::Duration::from_millis(10));
        }
        let n = hosted_count();
        return (n, failed, n > 0);
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn parses_f9_family() {
        assert_eq!(parse_shortcut("f9"), Some((0x78, MOD_NOREPEAT)));
        assert_eq!(
            parse_shortcut("shift+f9"),
            Some((0x78, MOD_SHIFT | MOD_NOREPEAT))
        );
        assert_eq!(
            parse_shortcut("ctrl+shift+f9"),
            Some((0x78, MOD_CONTROL | MOD_SHIFT | MOD_NOREPEAT))
        );
    }

    #[test]
    fn parses_letters_digits_and_aliases() {
        assert_eq!(parse_shortcut("ctrl+c").unwrap().0, 0x43);
        assert_eq!(parse_shortcut("control+alt+delete").unwrap().0, 0x2E);
        assert!(parse_shortcut("cmd+f9").is_none());
        assert!(parse_shortcut("f99").is_none());
        assert!(parse_shortcut("ctrl+shift").is_none());
        assert!(parse_shortcut("").is_none());
        assert!(parse_shortcut("ctrl++f9").is_none());
    }

    #[test]
    fn decodes_live_and_legacy_shapes() {
        let live = json!({"shortcut": "shift+f9", "mode": "panel", "strength": "rewrite_polish"});
        let spec = decode_entry(&live).unwrap();
        assert_eq!((spec.vk, spec.modifiers), (0x78, MOD_SHIFT | MOD_NOREPEAT));
        assert_eq!(spec.strength, "rewrite_polish");
        let legacy = json!({"vk": 120, "modifiers": 0, "label": "F9"});
        let spec = decode_entry(&legacy).unwrap();
        assert_eq!(spec.vk, 0x78);
        assert_eq!(spec.modifiers & MOD_NOREPEAT, MOD_NOREPEAT);
        assert_eq!(spec.mode, "panel");
    }

    #[test]
    fn rejects_garbage_specs() {
        assert!(decode_entry(&json!({"shortcut": "bogus"})).is_none());
        assert!(decode_entry(&json!({"vk": 0, "modifiers": 0})).is_none());
        let (ok, failed) = specs_from_json(&json!([
            {"shortcut": "f9", "mode": "panel", "strength": "full_correction"},
            {"shortcut": "nope"},
        ]));
        assert_eq!(ok.len(), 1);
        assert_eq!(failed, vec!["nope".to_string()]);
    }

    #[test]
    fn event_frame_is_idless_method_frame() {
        let spec = HostSpec {
            vk: 0x78,
            modifiers: MOD_NOREPEAT,
            label: "F9".into(),
            mode: "panel".into(),
            strength: "full_correction".into(),
            custom_prompt: "".into(),
        };
        let f = event_frame(&spec);
        assert_eq!(f["method"], "event.hotkey_fired");
        assert!(f.get("id").is_none());
        assert_eq!(f["params"]["strength"], "full_correction");
    }
}
