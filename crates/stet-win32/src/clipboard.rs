//! OLE IDataObject clipboard snapshot/restore (§2d) + sequence-number gate.
//!
//! The pure restore-vs-paste race logic lives in `crates/stet-core/src/clipboard.rs`
//! (already verified). This module is the FFI surface: OleGetClipboard (full-fidelity
//! snapshot on the STA clipboard thread), sequence numbers, and the suppression flags.

#![allow(non_snake_case, non_camel_case_types, non_upper_case_globals, dead_code)]

use crate::types::*;

// ── ole32 ───────────────────────────────────────────────────────────────────
#[link(name = "ole32")]
extern "system" {
    fn OleGetClipboard(ppDataObj: *mut *mut core::ffi::c_void) -> u32;
    fn OleFlushClipboard() -> u32;
    fn OleInitialize(pvReserved: *mut core::ffi::c_void) -> u32;
    fn OleUninitialize();
}

// ── user32 ──────────────────────────────────────────────────────────────────
#[link(name = "user32")]
extern "system" {
    fn OpenClipboard(hWndNewOwner: HWND) -> BOOL;
    fn CloseClipboard() -> BOOL;
    fn EmptyClipboard() -> BOOL;
    fn GetClipboardSequenceNumber() -> DWORD;
    fn SetClipboardData(uFormat: u32, hMem: HANDLE) -> HANDLE;
    fn CountClipboardFormats() -> u32;
}

// ── kernel32 ────────────────────────────────────────────────────────────────
#[link(name = "kernel32")]
extern "system" {
    fn GlobalAlloc(uFlags: u32, dwBytes: usize) -> HANDLE;
    fn GlobalLock(hMem: HANDLE) -> *mut core::ffi::c_void;
    fn GlobalUnlock(hMem: HANDLE) -> BOOL;
    fn GlobalFree(hMem: HANDLE) -> HANDLE;
}

pub const GMEM_MOVEABLE: u32 = 0x0002;
pub const GMEM_ZEROINIT: u32 = 0x0040;

/// Initialize OLE on the current thread (STA).
pub fn ole_initialize() -> u32 {
    unsafe { OleInitialize(core::ptr::null_mut()) }
}

/// Uninitialize OLE.
pub fn ole_uninitialize() {
    unsafe { OleUninitialize() }
}

/// Take a full-fidelity snapshot via OleGetClipboard into an IDataObject pointer.
/// Returns the pointer (caller releases). NULL on failure.
pub fn ole_get_clipboard() -> *mut core::ffi::c_void {
    let mut p: *mut core::ffi::c_void = core::ptr::null_mut();
    let hr = unsafe { OleGetClipboard(&mut p as *mut *mut core::ffi::c_void) };
    if hr == 0 {
        p
    } else {
        core::ptr::null_mut()
    }
}

/// Flush the clipboard (materialize delayed-rendered formats into owned memory).
/// Returns the HRESULT.
pub fn ole_flush_clipboard() -> u32 {
    unsafe { OleFlushClipboard() }
}

/// Query the current clipboard sequence number.
pub fn clipboard_sequence() -> u32 {
    unsafe { GetClipboardSequenceNumber() }
}

/// Open the clipboard (must pair with close_clipboard()).
pub fn open_clipboard() -> bool {
    unsafe { OpenClipboard(core::ptr::null_mut()) != 0 }
}

/// Close the clipboard.
pub fn close_clipboard() -> bool {
    unsafe { CloseClipboard() != 0 }
}

/// Empty the clipboard.
pub fn empty_clipboard() -> bool {
    unsafe { EmptyClipboard() != 0 }
}

/// Store text as CF_UNICODETEXT (caller passes a UTF-16 buffer).
/// SAFETY: text must be valid for `len` UTF-16 code units. Caller owns the GMEM buffer.
/// Requires OpenClipboard + EmptyClipboard to have succeeded on this thread.
pub unsafe fn set_clipboard_unicode(text: *const u16, len: usize) -> bool {
    let bytes = match len.checked_add(1).and_then(|n| n.checked_mul(2)) {
        Some(n) => n,
        None => return false,
    };
    let hmem = GlobalAlloc(GMEM_MOVEABLE | GMEM_ZEROINIT, bytes);
    if hmem.is_null() {
        return false;
    }
    let ptr = GlobalLock(hmem);
    if ptr.is_null() {
        GlobalFree(hmem);
        return false;
    }
    core::ptr::copy_nonoverlapping(text, ptr as *mut u16, len);
    (ptr as *mut u16).add(len).write(0);
    GlobalUnlock(hmem);
    let rc = SetClipboardData(CF_UNICODETEXT, hmem);
    if rc.is_null() {
        // Ownership stays with caller on failure — free to avoid leak.
        GlobalFree(hmem);
        return false;
    }
    // Success: OS owns hmem now.
    true
}

/// Count the formats currently on the clipboard.
pub fn count_clipboard_formats() -> u32 {
    unsafe { CountClipboardFormats() }
}

/// The suppression flags set per §0f / §2d.
pub fn suppression_flag_can_upload_cloud() -> u32 {
    CLIPBRD_EJECT_ALLOW
}