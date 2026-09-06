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
    fn GetClipboardData(uFormat: u32) -> HANDLE;
    fn RegisterClipboardFormatW(lpszFormat: *const u16) -> u32;
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

/// Registered clipboard formats used to suppress cloud/history sync (§0f/§2d).
pub fn suppress_history_fmt() -> u32 {
    static FMT: std::sync::LazyLock<u32> =
        std::sync::LazyLock::new(|| registered_format("ExcludeClipboardContentFromClipboardHistory"));
    *FMT
}

pub fn can_include_history_fmt() -> u32 {
    static FMT: std::sync::LazyLock<u32> =
        std::sync::LazyLock::new(|| registered_format("CanIncludeInClipboardHistory"));
    *FMT
}

pub fn can_upload_cloud_fmt() -> u32 {
    static FMT: std::sync::LazyLock<u32> =
        std::sync::LazyLock::new(|| registered_format("CanUploadToCloudClipboard"));
    *FMT
}

fn registered_format(name: &str) -> u32 {
    // ASCII-only format names; encode as UTF-16 with NUL terminator.
    let mut wide: Vec<u16> = name.encode_utf16().collect();
    wide.push(0);
    unsafe { RegisterClipboardFormatW(wide.as_ptr()) }
}

/// Set the privacy suppression formats on the currently-open clipboard:
/// exclude from Win+V history, exclude from clipboard-history sync, and
/// forbid cloud-clipboard upload (DWORD 0). Mirrors `_clipboard_write_text`
/// in stet/core/clipboard.py (§0f / Decision 38).
///
/// SAFETY: caller must hold the clipboard open on this thread. Failures are
/// best-effort — a missing suppression format never blocks the text write,
/// matching the Python implementation.
pub unsafe fn set_privacy_suppression() {
    let history = suppress_history_fmt();
    if history != 0 {
        SetClipboardData(history, core::ptr::null_mut());
    }
    for fmt in [can_include_history_fmt(), can_upload_cloud_fmt()] {
        if fmt == 0 {
            continue;
        }
        let h = GlobalAlloc(GMEM_MOVEABLE | GMEM_ZEROINIT, 4);
        if h.is_null() {
            continue;
        }
        // GMEM_ZEROINIT already zeroed the DWORD; just hand it over.
        if SetClipboardData(fmt, h).is_null() {
            GlobalFree(h);
        }
    }
}

/// Upper bound (UTF-16 units) scanned for the CF_UNICODETEXT NUL terminator.
/// A well-formed clipboard string is NUL-terminated; the cap keeps a corrupt
/// (unterminated) block from turning the scan into an unbounded read.
pub const MAX_CLIPBOARD_TEXT_UNITS: usize = 8 * 1024 * 1024;

/// Read the current CF_UNICODETEXT clipboard content as UTF-16 units (no NUL).
/// Returns None when the clipboard is not openable, holds no Unicode text,
/// or the text cannot be locked. Always closes the clipboard if it was opened
/// and never leaks the global lock on any path.
pub fn get_clipboard_unicode() -> Option<Vec<u16>> {
    unsafe {
        if OpenClipboard(core::ptr::null_mut()) == 0 {
            return None;
        }
        let result = get_clipboard_unicode_locked();
        CloseClipboard();
        result
    }
}

/// Inner reader: clipboard is already open on entry. Locks the shared
/// CF_UNICODETEXT block, copies it out, then unlocks before returning.
/// SAFETY: caller must hold the clipboard open on this thread.
unsafe fn get_clipboard_unicode_locked() -> Option<Vec<u16>> {
    let hmem = GetClipboardData(CF_UNICODETEXT);
    if hmem.is_null() {
        return None;
    }
    let ptr = GlobalLock(hmem) as *const u16;
    if ptr.is_null() {
        return None;
    }
    let mut len: usize = 0;
    while len < MAX_CLIPBOARD_TEXT_UNITS && *ptr.add(len) != 0 {
        len += 1;
    }
    let mut out = Vec::with_capacity(len);
    if len > 0 {
        core::ptr::copy_nonoverlapping(ptr, out.as_mut_ptr(), len);
        out.set_len(len);
    }
    GlobalUnlock(hmem);
    Some(out)
}