//! Named-pipe server/client primitives (§3.1 Windows transport).
//!
//! Raw `extern "system"` bindings declared directly with `#[link]`. This avoids the
//! `windows` crate feature-gate dead-end on `ReadFile`/`WriteFile`. All 6 pipe
//! verbs used by Stet (create, connect as client, connect as server, read, write,
//! disconnect+close) are represented. Behavior is gated behind the `lab` feature.

#![allow(non_snake_case, non_camel_case_types, non_upper_case_globals, dead_code)]

use crate::types::*;

#[link(name = "kernel32")]
extern "system" {
        fn CreateNamedPipeW(
        lpName: *const WCHAR,
        dwOpenMode: DWORD,
        dwPipeMode: DWORD,
        nMaxInstances: DWORD,
        nOutBufferSize: DWORD,
        nInBufferSize: DWORD,
        nDefaultTimeOut: DWORD,
        lpSecurityAttributes: *const SECURITY_ATTRIBUTES,
    ) -> HANDLE;

        fn ConnectNamedPipe(hNamedPipe: HANDLE, lpOverlapped: *mut core::ffi::c_void) -> BOOL;

        fn DisconnectNamedPipe(hNamedPipe: HANDLE) -> BOOL;

        fn ReadFile(
        hFile: HANDLE,
        lpBuffer: *mut core::ffi::c_void,
        nNumberOfBytesToRead: DWORD,
        lpNumberOfBytesRead: *mut DWORD,
        lpOverlapped: *mut core::ffi::c_void,
    ) -> BOOL;

        fn WriteFile(
        hFile: HANDLE,
        lpBuffer: *const core::ffi::c_void,
        nNumberOfBytesToWrite: DWORD,
        lpNumberOfBytesWritten: *mut DWORD,
        lpOverlapped: *mut core::ffi::c_void,
    ) -> BOOL;

        fn CloseHandle(hObject: HANDLE) -> BOOL;

        fn GetLastError() -> DWORD;

        fn SetLastError(dwErrCode: DWORD);

        fn CreateFileW(
        lpFileName: *const WCHAR,
        dwDesiredAccess: DWORD,
        dwShareMode: DWORD,
        lpSecurityAttributes: *const SECURITY_ATTRIBUTES,
        dwCreationDisposition: DWORD,
        dwFlagsAndAttributes: DWORD,
        hTemplateFile: HANDLE,
    ) -> HANDLE;

        fn GetNamedPipeClientProcessId(
        Pipe: HANDLE,
        ClientProcessId: *mut DWORD,
    ) -> BOOL;

        fn PeekNamedPipe(
        hNamedPipe: HANDLE,
        lpBuffer: *mut core::ffi::c_void,
        nBufferSize: DWORD,
        lpBytesRead: *mut DWORD,
        lpTotalBytesAvail: *mut DWORD,
        lpBytesLeftThisMessage: *mut DWORD,
    ) -> BOOL;
}

/// Open-mode flags for CreateFileW on a pipe.
pub const OPEN_EXISTING: u32 = 3;
pub const FILE_SHARE_READ: u32 = 0x00000001;
pub const FILE_SHARE_WRITE: u32 = 0x00000002;

/// Round-trip unit for a small buffer write/read. Returns the number of bytes.
/// SAFETY: caller guarantees the pointers are valid for `len` bytes.
pub unsafe fn write_bytes(handle: HANDLE, buf: *const u8, len: usize) -> Result<usize, DWORD> {
    let mut written: DWORD = 0;
    let ok = WriteFile(handle, buf as *const core::ffi::c_void, len as DWORD, &mut written, core::ptr::null_mut());
    if ok == 0 {
        Err(last_error())
    } else {
        Ok(written as usize)
    }
}

/// Round-trip unit for a bounded read.
/// SAFETY: caller guarantees the buffer points to at least `cap` bytes of writable memory.
pub unsafe fn read_bytes(handle: HANDLE, buf: *mut u8, cap: usize) -> Result<usize, DWORD> {
    let mut read: DWORD = 0;
    let ok = ReadFile(handle, buf as *mut core::ffi::c_void, cap as DWORD, &mut read, core::ptr::null_mut());
    if ok == 0 {
        Err(last_error())
    } else {
        Ok(read as usize)
    }
}

/// Create the Stet IPC pipe server with §3.1 security. Returns the server handle.
/// On failure returns the last-error code.
pub fn create_pipe_server(name: &[u16], security: *const SECURITY_ATTRIBUTES) -> Result<HANDLE, DWORD> {
    let handle = unsafe {
        CreateNamedPipeW(
            name.as_ptr(),
            PIPE_ACCESS_DUPLEX | FILE_FLAG_FIRST_PIPE_INSTANCE,
            PIPE_TYPE_BYTE | PIPE_READMODE_BYTE | PIPE_WAIT | PIPE_REJECT_REMOTE_CLIENTS,
            1,                    // single instance
            MAX_FRAME_BYTES as u32,
            MAX_FRAME_BYTES as u32,
            0,                    // default timeout
            security,
        )
    };
    if handle == INVALID_HANDLE_VALUE {
        Err(last_error())
    } else {
        Ok(handle)
    }
}

/// Accept a single client. Blocks until a connection arrives (or fails).
pub fn accept_client(server: HANDLE) -> Result<(), DWORD> {
    let ok = unsafe { ConnectNamedPipe(server, core::ptr::null_mut()) };
    if ok == 0 {
        // ERROR_PIPE_CONNECTED (535) means the client connected between create and connect.
        match last_error() {
            535 => Ok(()),
            e => Err(e),
        }
    } else {
        Ok(())
    }
}

/// Connect as a client to an existing pipe. Returns the client handle.
pub fn connect_client(name: &[u16]) -> Result<HANDLE, DWORD> {
    let handle = unsafe {
        CreateFileW(
            name.as_ptr(),
            GENERIC_READ | GENERIC_WRITE,
            FILE_SHARE_READ | FILE_SHARE_WRITE,
            core::ptr::null(),
            OPEN_EXISTING,
            0,
            core::ptr::null_mut(),
        )
    };
    if handle == INVALID_HANDLE_VALUE {
        Err(last_error())
    } else {
        Ok(handle)
    }
}

/// Get the client PID of a connected pipe (diagnostic only, §3.1 rule 5).
pub fn client_pid(handle: HANDLE) -> Result<u32, DWORD> {
    let mut pid: DWORD = 0;
    let ok = unsafe { GetNamedPipeClientProcessId(handle, &mut pid) };
    if ok == 0 {
        Err(last_error())
    } else {
        Ok(pid)
    }
}

/// Close a pipe handle (server or client).
pub fn close_handle(handle: HANDLE) -> bool {
    unsafe { CloseHandle(handle) != 0 }
}

/// Disconnect a connected server pipe (client-side teardown).
pub fn disconnect(handle: HANDLE) -> bool {
    unsafe { DisconnectNamedPipe(handle) != 0 }
}

/// Peek the number of available bytes (for non-blocking logic). Returns (avail, left_in_msg).
pub fn peek_available(handle: HANDLE) -> Result<(DWORD, DWORD), DWORD> {
    let mut avail: DWORD = 0;
    let mut left: DWORD = 0;
    let ok = unsafe { PeekNamedPipe(handle, core::ptr::null_mut(), 0, core::ptr::null_mut(), &mut avail, &mut left) };
    if ok == 0 {
        Err(last_error())
    } else {
        Ok((avail, left))
    }
}

/// Helper to compose a UTF-16 null-terminated name from `\\\\.\\pipe\\stet_ipc_v2` + suffix.
pub fn pipe_name(name: &str) -> Vec<u16> {
    let mut v: Vec<u16> = name.encode_utf16().collect();
    v.push(0);
    v
}

/// Read the thread's last-error code (headless-safe).
pub fn last_error() -> u32 {
    unsafe { crate::pipe::GetLastError() }
}

/// Set the thread's last-error code (headless-safe).
pub fn set_last_error(e: u32) {
    unsafe { SetLastError(e) }
}