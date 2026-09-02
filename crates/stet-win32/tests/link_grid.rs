//! Link-grid checklist.
//!
//! This asserts the link-probe surface is non-empty and internally consistent. The real
//! proof is a clean `cargo test` link: because the probe references every wrapper that
//! transitively calls a raw extern, the MSVC linker must resolve each symbol against the
//! real kernel32/user32/advapi32/ole32 import libs, or linking fails.

#![allow(non_snake_case, non_camel_case_types)]

#[test]
fn link_surface_is_non_empty() {
    assert!(!ffi_skel::linkprobe::LINK_POINTS.is_empty());
}

#[test]
fn link_points_cover_every_subsystem() {
    let all = ffi_skel::linkprobe::LINK_POINTS.concat();
    for subsystem in ["pipe", "security", "input", "hook", "window", "launcher", "clipboard"] {
        assert!(all.contains(subsystem), "missing subsystem links for {subsystem}");
        assert!(ffi_skel::linkprobe::LINK_POINTS.iter().any(|s| s.starts_with(subsystem)));
    }
}

#[test]
fn pipe_surface_links_read_and_write() {
    // The two primitives the earlier windows-crate dead-end blocked (ReadFile/WriteFile)
    // must be reachable via wrappers, proving the byte-stream pipe I/O can be built.
    let names = ffi_skel::linkprobe::LINK_POINTS;
    assert!(names.iter().any(|s| s.contains("read_bytes")));
    assert!(names.iter().any(|s| s.contains("write_bytes")));
}

#[test]
fn launcher_surface_links_job_object_and_process() {
    let names = ffi_skel::linkprobe::LINK_POINTS;
    // §3.1 rule 1 needs CreateJobObject + CreateProcessW + AssignProcessToJobObject.
    assert!(names.iter().any(|s| s.contains("create_job_object")));
    assert!(names.iter().any(|s| s.contains("assign_to_job")));
    assert!(names.iter().any(|s| s.contains("open_process")));
}

#[test]
fn security_surface_links_dacl_builders() {
    let names = ffi_skel::linkprobe::LINK_POINTS;
    // §3.1 rules 3-6 need the DACL/SID builders (no Everyone/anonymous).
    assert!(names.iter().any(|s| s.contains("build_acl_allow_only")));
    assert!(names.iter().any(|s| s.contains("apply_restrictive_dacl")));
    assert!(names.iter().any(|s| s.contains("token_user_sid")));
}

#[test]
fn clipboard_surface_links_ole_and_sequence() {
    let names = ffi_skel::linkprobe::LINK_POINTS;
    // §2d needs OleGetClipboard + the sequence-number gate.
    assert!(names.iter().any(|s| s.contains("ole_get_clipboard")));
    assert!(names.iter().any(|s| s.contains("clipboard_sequence")));
}

#[test]
fn hook_surface_links_keyboard_and_winevent() {
    let names = ffi_skel::linkprobe::LINK_POINTS;
    // §2b/e needs WH_KEYBOARD_LL; §2f needs SetWinEventHook.
    assert!(names.iter().any(|s| s.contains("install_keyboard_hook")));
    assert!(names.iter().any(|s| s.contains("install_winevent_hook")));
}

#[test]
fn window_surface_links_hotkey_message_pump() {
    let names = ffi_skel::linkprobe::LINK_POINTS;
    // §2a needs a message-only HWND pump + RegisterHotKey.
    assert!(names.iter().any(|s| s.contains("register_hotkey")));
    assert!(names.iter().any(|s| s.contains("message_pump")));
    assert!(names.iter().any(|s| s.contains("create_message_only_window")));
}

#[test]
fn input_surface_links_sendinput_and_self_tag() {
    let names = ffi_skel::linkprobe::LINK_POINTS;
    // §2e needs SendInput + dwExtraInfo self-tag.
    assert!(names.iter().any(|s| s.contains("send_key_event")));
    assert!(names.iter().any(|s| s.contains("is_stet_self_event")));
}

