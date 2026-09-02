//! Checked SendInput synthesis + key-state queries (Phase 0b / §2e).
//!
//! UIA-first is primary; the chord path is last-resort. Every injected event carries
//! Stet's private dwExtraInfo tag; the low-level hook filters that EXACT tag and not the
//! generic LLKHF_INJECTED flag (RDP user input is injected too).

#![allow(non_snake_case, non_camel_case_types, non_upper_case_globals, dead_code)]

use crate::types::*;

#[link(name = "user32")]
extern "system" {
        fn SendInput(
        cInputs: u32,
        pInputs: *mut INPUT,
        cbSize: i32,
    ) -> u32;

        fn GetKeyState(nVirtKey: i32) -> i16;

        fn GetAsyncKeyState(vKey: i32) -> i16;

        fn MapVirtualKeyW(uCode: u32, uMapType: u32) -> u32;

        fn GetMessageExtraInfo() -> ULONG_PTR;

        fn SetMessageExtraInfo(lParam: ULONG_PTR) -> ULONG_PTR;
}

/// Construct a keyboard INPUT record with Stet's private tag.
/// `keyup` selects KEYEVENTF_KEYUP; `extended` selects KEYEVENTF_EXTENDEDKEY.
pub unsafe fn make_keyboard_input(vk: u32, scan: u16, keyup: bool, extended: bool, tag: usize) -> INPUT {
    let mut flags: DWORD = 0;
    if keyup {
        flags |= KEYEVENTF_KEYUP;
    }
    if extended {
        flags |= KEYEVENTF_EXTENDEDKEY;
    }
    let ki = KEYBDINPUT {
        wVk: vk as WORD,
        wScan: scan,
        dwFlags: flags,
        time: 0,
        dwExtraInfo: tag,
    };
    INPUT {
        r#type: INPUT_KEYBOARD,
        u: INPUT_UNION { ki },
    }
}

/// Send one keyboard event and return the number of events queued (>=1 is success).
/// SAFETY: not all success means consumed; a queued input is not proof of consumption.
pub unsafe fn send_key_event(input: &INPUT) -> u32 {
    SendInput(1, input as *const INPUT as *mut INPUT, core::mem::size_of::<INPUT>() as i32)
}

/// Send a chord (e.g. Ctrl+C) of a variable number of events, all Stet-tagged.
/// Returns the count of events actually injected. 0 means injected nothing.
pub unsafe fn send_events(inputs: &[INPUT]) -> u32 {
    SendInput(inputs.len() as u32, inputs.as_ptr() as *mut INPUT, core::mem::size_of::<INPUT>() as i32)
}

/// A pair of plain (down, up) chord events for one key stet-tagged.
pub unsafe fn chord_events(vk: u32, tag: usize) -> [INPUT; 2] {
    [
        make_keyboard_input(vk, 0, false, false, tag),
        make_keyboard_input(vk, 0, true, false, tag),
    ]
}

/// Query the physical/async state of a key. Returns true if the high bit (pressed) set.
pub fn is_key_down(vk: u32) -> bool {
    unsafe { GetKeyState(vk as i32) as u16 & 0x8000 != 0 }
}

/// Query the async key state. Returns true if pressed.
pub fn is_async_key_down(vk: u32) -> bool {
    unsafe { GetAsyncKeyState(vk as i32) as u16 & 0x8000 != 0 }
}

/// Map a virtual-key code to a scan code (MapVirtualKeyW with MAPVK_VK_TO_VSC).
pub fn vk_to_scan(vk: u32) -> u32 {
    unsafe { MapVirtualKeyW(vk, 0) }
}

/// Stet's self-tag is stored in the thread message extra info.
pub fn current_message_extra_info() -> usize {
    unsafe { GetMessageExtraInfo() }
}

/// Set the thread message extra info (for the hook thread). Returns the previous value.
pub fn set_message_extra_info(v: usize) -> usize {
    unsafe { SetMessageExtraInfo(v) }
}

/// True if the given input record is a Stet-tagged keyboard event.
pub fn is_stet_self_event(extra_info: usize) -> bool {
    extra_info & 0xFFFF_FFFF == STET_DW_EXTRA_INFO
}