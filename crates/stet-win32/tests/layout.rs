//! Struct-layout assertions on the host x86_64-pc-windows-msvc target.
//!
//! These are the x64 Windows SDK `repr(C)` layouts. If a future refactor changes a shape,
//! this test fails loudly so the ABI break is caught before the fragment hits the lab.

#![allow(non_snake_case, non_camel_case_types, dead_code)]

use ffi_skel::types::*;

fn size_of<T>() -> usize { core::mem::size_of::<T>() }
fn align_of<T>() -> usize { core::mem::align_of::<T>() }

#[test]
fn guid_layout() {
    // 4 + 2 + 2 + 8 = 16, align 4.
    assert_eq!(size_of::<GUID>(), 16);
    assert_eq!(align_of::<GUID>(), 4);
}

#[test]
fn systemtime_layout() {
    // 8 x u16 = 16, align 2.
    assert_eq!(size_of::<SYSTEMTIME>(), 16);
    assert_eq!(align_of::<SYSTEMTIME>(), 2);
}

#[test]
fn filetime_layout() {
    assert_eq!(size_of::<FILETIME>(), 8);
    assert_eq!(align_of::<FILETIME>(), 4);
}

#[test]
fn keybdinput_layout() {
    // wVk(2)+wScan(2)+dwFlags(4)+time(4) = 12, pad to align 8, +dwExtraInfo(8) = 24.
    assert_eq!(size_of::<KEYBDINPUT>(), 24);
    assert_eq!(align_of::<KEYBDINPUT>(), 8);
}

#[test]
fn mouseinput_layout() {
    // 4*4 + 4 + 8 = 28, pad to 32.
    assert_eq!(size_of::<MOUSEINPUT>(), 32);
    assert_eq!(align_of::<MOUSEINPUT>(), 8);
}

#[test]
fn hardwareinput_layout() {
    // 4 + 2 + 2 = 8.
    assert_eq!(size_of::<HARDWAREINPUT>(), 8);
    assert_eq!(align_of::<HARDWAREINPUT>(), 4);
}

#[test]
fn input_union_is_max_member() {
    // Union size must be >= KEYBDINPUT (24) and MOUSEINPUT (32), so 32.
    assert!(size_of::<INPUT_UNION>() >= 32);
}

#[test]
fn input_stride() {
    // INPUT = type(4) + union(32), pad => 40.
    assert_eq!(size_of::<INPUT>(), 40);
    assert_eq!(align_of::<INPUT>(), 8);
}

#[test]
fn kbdllhookstruct_layout() {
    // 4+4+4+4 + 8 = 24.
    assert_eq!(size_of::<KBDLLHOOKSTRUCT>(), 24);
    assert_eq!(align_of::<KBDLLHOOKSTRUCT>(), 8);
}

#[test]
fn msg_layout() {
    // hwnd(8)+message(4)+pad(4)+wParam(8)+lParam(8)+time(4)+pad(4)+pt(8) = 48.
    assert_eq!(size_of::<MSG>(), 48);
    assert_eq!(align_of::<MSG>(), 8);
}

#[test]
fn security_attributes_layout() {
    // nLength(4)+pad(4)+lp(8)+bInherit(4)+pad(4) = 24.
    assert_eq!(size_of::<SECURITY_ATTRIBUTES>(), 24);
    assert_eq!(align_of::<SECURITY_ATTRIBUTES>(), 8);
}

#[test]
fn started_process_info_layout() {
    // two handles (16) + two DWORDs (8) = 24.
    assert_eq!(size_of::<PROCESS_INFORMATION>(), 24);
    assert_eq!(align_of::<PROCESS_INFORMATION>(), 8);
}

#[test]
fn jobobject_basic_limit_layout() {
    // Conservative: just assert it is at least the sum of its scalar fields.
    assert!(size_of::<JOBOBJECT_BASIC_LIMIT_INFORMATION>() >= 8 + 8 + 4 + 8 * 3 + 4 + 4 + 4);
}

#[test]
fn jobobject_extended_limit_layout() {
    assert!(size_of::<JOBOBJECT_EXTENDED_LIMIT_INFORMATION>() >=
            size_of::<JOBOBJECT_BASIC_LIMIT_INFORMATION>() + size_of::<IO_COUNTERS>() + 8 * 4);
}
