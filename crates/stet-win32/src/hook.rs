//! WH_KEYBOARD_LL hook (primary input source, §2b) + SetWinEventHook (§2f).
//!
//! §2b: WH_KEYBOARD_LL is the primary input source because KBDLLHOOKSTRUCT carries
//! dwExtraInfo which distinguishes Stet's own SendInput events. `WM_INPUT` has no Stet
//! self-tag and is never the primary self-event filter. The callback must be constant-time,
//! immediately call the next hook, and hand state to a worker; the hook is re-armed by the
//! monitor because Windows silently removes it after LowLevelHooksTimeout.

#![allow(non_snake_case, non_camel_case_types, non_upper_case_globals, dead_code)]

use crate::types::*;

// ── user32 ──────────────────────────────────────────────────────────────────
#[link(name = "user32")]
extern "system" {
    fn SetWindowsHookExW(idHook: i32, lpfn: HOOKPROC, hMod: HINSTANCE, dwThreadId: DWORD) -> HHOOK;
    fn CallNextHookEx(hhk: HHOOK, nCode: i32, wParam: usize, lParam: isize) -> isize;
    fn UnhookWindowsHookEx(hhk: HHOOK) -> BOOL;
    fn SetWinEventHook(
        eventMin: u32,
        eventMax: u32,
        hmodWinEventProc: HINSTANCE,
        pfnWinEventProc: WINEVENTPROC,
        idProcess: DWORD,
        idThread: DWORD,
        dwFlags: u32,
    ) -> HWINEVENTHOOK;
    fn UnhookWinEvent(hWinEventHook: HWINEVENTHOOK) -> BOOL;
}

// ── kernel32 ────────────────────────────────────────────────────────────────
#[link(name = "kernel32")]
extern "system" {
    fn GetCurrentThreadId() -> DWORD;
}

pub type HHOOK = *mut core::ffi::c_void;
pub type HWINEVENTHOOK = *mut core::ffi::c_void;
pub type HOOKPROC = unsafe extern "system" fn(i32, usize, isize) -> isize;
pub type WINEVENTPROC = unsafe extern "system" fn(HWINEVENTHOOK, u32, HWND, LONG, LONG, DWORD, DWORD);

/// Install the low-level keyboard hook on the current thread.
/// SAFETY: the hook proc must be a valid function pointer that lives for the hook's lifetime.
pub unsafe fn install_keyboard_hook(proc: HOOKPROC) -> Result<HHOOK, DWORD> {
    let hook = SetWindowsHookExW(WH_KEYBOARD_LL, proc, core::ptr::null_mut(), 0);
    if hook.is_null() {
        Err(crate::pipe::last_error())
    } else {
        Ok(hook)
    }
}

/// Remove the keyboard hook.
pub fn remove_keyboard_hook(hook: HHOOK) -> bool {
    unsafe { UnhookWindowsHookEx(hook) != 0 }
}

/// Install a WinEvent foreground/focus hook (thread=0 => global).
/// SAFETY: the proc must live for the hook's lifetime.
pub unsafe fn install_winevent_hook(proc: WINEVENTPROC) -> Result<HWINEVENTHOOK, DWORD> {
    let hook = SetWinEventHook(
        EVENT_SYSTEM_FOREGROUND,
        EVENT_OBJECT_FOCUS,
        core::ptr::null_mut(),
        proc,
        0,
        0,
        WINEVENT_OUTOFCONTEXT | WINEVENT_SKIPOWNPROCESS,
    );
    if hook.is_null() {
        Err(crate::pipe::last_error())
    } else {
        Ok(hook)
    }
}

/// Remove the WinEvent hook.
pub fn remove_winevent_hook(hook: HWINEVENTHOOK) -> bool {
    unsafe { UnhookWinEvent(hook) != 0 }
}

/// Get the current thread id for a hook installed on a specific thread.
pub fn current_thread_id() -> u32 {
    unsafe { GetCurrentThreadId() }
}

/// A re-usable constant-time callback that forwards to the next hook and does NO work.
/// Stet's real hook hands state to a worker; the skeleton only shows the seam.
pub unsafe extern "system" fn passthrough_hook(n_code: i32, w_param: usize, l_param: isize) -> isize {
    CallNextHookEx(core::ptr::null_mut(), n_code, w_param, l_param)
}

/// The hook thread's message pump WinEvent callback skeleton.
pub unsafe extern "system" fn winevent_passthrough(
    _hook: HWINEVENTHOOK,
    _event: u32,
    _hwnd: HWND,
    _id_object: LONG,
    _id_child: LONG,
    _id_thread: DWORD,
    _time: DWORD,
) {
    // No-op skeleton: a real impl filters Stet-own windows and updates last_external_target.
}