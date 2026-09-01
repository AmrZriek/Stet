//! Message-only HWND + diff-based RegisterHotKey (Phase 0g / §2a).
//!
//! §2a: hidden message-only `HWND` with a dedicated Win32 message pump on a background
//! OS thread. The message-only window + message pump is the host shell. The diff-based
//! hotkey registration is the same algorithm already verified in
//! `crates/stet-core/src/hotkey.rs`; here we expose the raw RegisterHotKey/UnregisterHotKey
//! + the message constants needed to drive it.

#![allow(non_snake_case, non_camel_case_types, non_upper_case_globals, dead_code)]

use crate::types::*;

#[link(name = "user32")]
extern "system" {
        fn RegisterHotKey(hWnd: HWND, id: i32, fsModifiers: u32, vk: u32) -> BOOL;

        fn UnregisterHotKey(hWnd: HWND, id: i32) -> BOOL;

        fn RegisterClassExW(lpWndClass: *const WNDCLASSEXW) -> WORD;

        fn UnregisterClassW(lpClassName: *const WCHAR, hInstance: HINSTANCE) -> BOOL;

        fn CreateWindowExW(
        dwExStyle: DWORD,
        lpClassName: *const WCHAR,
        lpWindowName: *const WCHAR,
        dwStyle: DWORD,
        x: i32,
        y: i32,
        nWidth: i32,
        nHeight: i32,
        hWndParent: HWND,
        hMenu: HMENU,
        hInstance: HINSTANCE,
        lpParam: *mut core::ffi::c_void,
    ) -> HWND;

        fn DestroyWindow(hWnd: HWND) -> BOOL;

        fn GetMessageW(lpMsg: *mut MSG, hWnd: HWND, wMsgFilterMin: u32, wMsgFilterMax: u32) -> BOOL;

        fn TranslateMessage(lpMsg: *const MSG) -> BOOL;

        fn DispatchMessageW(lpMsg: *const MSG) -> usize;

        fn PostQuitMessage(nExitCode: i32);

        fn GetModuleHandleW(lpModuleName: *const WCHAR) -> HINSTANCE;
}

/// Register a hotkey against the message-only window.
pub fn register_hotkey(hwnd: HWND, id: i32, modifiers: u32, vk: u32) -> bool {
    unsafe { RegisterHotKey(hwnd, id, modifiers, vk) != 0 }
}

/// Unregister a hotkey.
pub fn unregister_hotkey(hwnd: HWND, id: i32) -> bool {
    unsafe { UnregisterHotKey(hwnd, id) != 0 }
}

/// Create a message-only window (HWND_MESSAGE = -3, parent sentinel).
/// SAFETY: class must be registered, proc valid, instance valid.
pub unsafe fn create_message_only_window(
    class_name: *const WCHAR,
    title: *const WCHAR,
    instance: HINSTANCE,
) -> HWND {
    const HWND_MESSAGE_PARENT: HWND = -3isize as HWND;
    CreateWindowExW(
        0,
        class_name,
        title,
        0,
        0,
        0,
        0,
        0,
        HWND_MESSAGE_PARENT,
        core::ptr::null_mut(),
        instance,
        core::ptr::null_mut(),
    )
}

/// Register the window class. Returns 0 on failure (GetLastError gives the cause).
pub unsafe fn register_window_class(class: *const WNDCLASSEXW) -> u16 {
    RegisterClassExW(class)
}

/// The message pump: blocks on GetMessageW, dispatches.
pub unsafe fn message_pump() {
    loop {
        let mut msg = MSG {
            hwnd: core::ptr::null_mut(),
            message: 0,
            wParam: 0,
            lParam: 0,
            time: 0,
            pt: POINT { x: 0, y: 0 },
        };
        let ret = GetMessageW(&mut msg, core::ptr::null_mut(), 0, 0);
        if ret == 0 || ret == -1 {
            break;
        }
        TranslateMessage(&msg);
        DispatchMessageW(&msg);
    }
}

/// Get the module handle for the current module.
pub fn current_module() -> HINSTANCE {
    unsafe { GetModuleHandleW(core::ptr::null()) }
}

/// WM_HOTKEY message constant (the message the message-only HWND receives).
pub const WM_HOTKEY: u32 = 0x0312;
/// HWND_MESSAGE (message-only window parent).
pub const HWND_MESSAGE: isize = -3;
/// WS styles for the message-only window (none needed).
pub const WS_OVERLAPPED: DWORD = 0;