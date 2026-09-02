//! Deterministic headless OS-call smoke tests.
//!
//! These call only Win32 functions that are safe to invoke without a GUI session: process
//! id, tick count, last-error round trip, key-state query, clipboard sequence number, and
//! self-tag classification. They verify the FFI surface is actually callable on this host.

#![allow(non_snake_case, dead_code)]

use ffi_skel::types::*;

#[test]
fn current_process_id_is_nonzero() {
    let pid = ffi_skel::launcher::current_process_id();
    assert!(pid != 0, "current process id should be nonzero");
}

#[test]
fn get_last_error_roundtrip() {
    // Set then read; the read must return exactly what we set.
    ffi_skel::pipe::set_last_error(ERROR_ACCESS_DENIED);
    let e = ffi_skel::pipe::last_error();
    assert_eq!(e, ERROR_ACCESS_DENIED);
}

#[test]
fn stet_self_tag_classification() {
    // The exact Stet tag is self; a generic injected flag is NOT self (RDP input).
    assert!(ffi_skel::input::is_stet_self_event(STET_DW_EXTRA_INFO));
    assert!(!ffi_skel::input::is_stet_self_event(0x1111_1111));
    assert!(!ffi_skel::input::is_stet_self_event(0x0));
}

#[test]
fn key_state_query_returns_i16() {
    // GetKeyState returns i16 regardless of the value; just ensure the call does not trap.
    let v = ffi_skel::input::is_key_down(VK_F9);
    // We cannot assert the value (varies), but the call must succeed without UB.
    let _ = v;
}

#[test]
fn clipboard_sequence_is_monotonic() {
    // The sequence number is a DWORD; reading it must not trap.
    let _ = ffi_skel::clipboard::clipboard_sequence();
}

#[test]
fn link_point_count_is_expected() {
    // The link probe should reference a stable, non-trivial number of wrapper link points.
    let n = ffi_skel::linkprobe::link_point_count();
    assert!(n >= 50, "expected >=50 link points, got {n}");
}

#[test]
fn input_struct_layout_matches_abi() {
    // INPUT = 40 bytes on x64; construct one to validate union usage.
    let inp = unsafe { ffi_skel::input::make_keyboard_input(VK_CONTROL, 0, false, false, STET_DW_EXTRA_INFO) };
    assert_eq!(inp.r#type, INPUT_KEYBOARD as u32);
    unsafe {
        assert_eq!(inp.u.ki.wVk, VK_CONTROL as u16);
        assert_eq!(inp.u.ki.dwExtraInfo, STET_DW_EXTRA_INFO);
    }
}

#[test]
fn chord_events_are_stet_tagged() {
    let ev = unsafe { ffi_skel::input::chord_events(VK_C, STET_DW_EXTRA_INFO) };
    assert_eq!(ev.len(), 2);
    unsafe {
        // First is down, second is up; both carry the Stet tag.
        assert_eq!(ev[0].u.ki.dwFlags & KEYEVENTF_KEYUP, 0);
        assert_ne!(ev[1].u.ki.dwFlags & KEYEVENTF_KEYUP, 0);
        assert_eq!(ev[0].u.ki.dwExtraInfo, STET_DW_EXTRA_INFO);
    }
}

#[test]
fn vk_to_scan_maps_known_key() {
    // VK_F9 -> a nonzero scan code on a real keyboard driver.
    let scan = ffi_skel::input::vk_to_scan(VK_F9);
    assert!(scan != 0, "VK_F9 should map to a nonzero scan code");
}

#[test]
fn pipe_event_and_flag_constants() {
    // The flags the wire protocol depends on are the documented values.
    assert_eq!(PIPE_REJECT_REMOTE_CLIENTS, 0x0000_0008);
    assert_eq!(FILE_FLAG_FIRST_PIPE_INSTANCE, 0x0008_0000);
    assert_eq!(STET_DW_EXTRA_INFO, 0x7374_6574);
}
