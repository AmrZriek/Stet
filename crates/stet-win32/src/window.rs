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

        fn GetForegroundWindow() -> HWND;

        fn GetClassNameW(hWnd: HWND, lpClassName: *mut WCHAR, nMaxCount: i32) -> i32;

        fn GetWindowThreadProcessId(hWnd: HWND, lpdwProcessId: *mut DWORD) -> DWORD;
}

#[link(name = "kernel32")]
extern "system" {
        fn OpenProcess(dwDesiredAccess: DWORD, bInheritHandle: BOOL, dwProcessId: DWORD) -> HANDLE;

        fn QueryFullProcessImageNameW(
        hProcess: HANDLE,
        dwFlags: DWORD,
        lpExeName: *mut WCHAR,
        lpdwSize: *mut DWORD,
    ) -> BOOL;

        fn CloseHandle(hObject: HANDLE) -> BOOL;

        fn GetLastError() -> DWORD;
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

/// PROCESS_QUERY_LIMITED_INFORMATION: enough to read the image name, never
/// enough to disturb the target process.
pub const PROCESS_QUERY_LIMITED_INFORMATION: DWORD = 0x1000;
/// Maximum image-path length queried (matches MAX_PATH).
pub const MAX_IMAGE_PATH: usize = 260;
/// Maximum window-class length queried (matches the Python guard's buffer).
pub const MAX_CLASS_NAME: usize = 256;
/// Sentinel error from [`process_image_name`] when a live process handle was
/// opened but its image name could not be read. Never a real GetLastError
/// code; lets the caller fail closed (treat as terminal) while still mapping
/// a plain OpenProcess failure (e.g. stale PID) to not-terminal.
pub const IDENTITY_UNREADABLE: DWORD = u32::MAX;

/// The current foreground window as an integer handle. 0 means no foreground
/// window (nothing to disturb, never a terminal).
pub fn foreground_window() -> isize {
    unsafe { GetForegroundWindow() as isize }
}

/// The window class of `hwnd` (empty when unreadable).
pub fn window_class(hwnd: isize) -> String {
    let mut buf = [0u16; MAX_CLASS_NAME];
    let len = unsafe { GetClassNameW(hwnd as HWND, buf.as_mut_ptr(), MAX_CLASS_NAME as i32) };
    if len <= 0 {
        return String::new();
    }
    let units = (len as usize).min(MAX_CLASS_NAME);
    String::from_utf16_lossy(&buf[..units])
}

/// The PID owning `hwnd`. None when the handle is stale (PID 0).
pub fn window_pid(hwnd: isize) -> Option<u32> {
    let mut pid: DWORD = 0;
    unsafe {
        GetWindowThreadProcessId(hwnd as HWND, &mut pid);
    }
    if pid == 0 {
        None
    } else {
        Some(pid)
    }
}

/// The full image path of `pid`. Err is the GetLastError code, except
/// [`IDENTITY_UNREADABLE`] when a live handle's image could not be read.
pub fn process_image_name(pid: u32) -> Result<String, DWORD> {
    unsafe {
        let handle = OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, 0, pid);
        if handle.is_null() {
            return Err(GetLastError());
        }
        let mut buf = [0u16; MAX_IMAGE_PATH];
        let mut size: DWORD = MAX_IMAGE_PATH as DWORD;
        let ok = QueryFullProcessImageNameW(handle, 0, buf.as_mut_ptr(), &mut size);
        CloseHandle(handle);
        if ok == 0 {
            return Err(IDENTITY_UNREADABLE);
        }
        let units = (size as usize).min(MAX_IMAGE_PATH);
        Ok(String::from_utf16_lossy(&buf[..units]))
    }
}