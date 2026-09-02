//! Win32 base types, constants, and struct layouts for x86_64-pc-windows-msvc.
//!
//! All struct layouts are asserted against `size_of`/`align_of` in tests/layout.rs.
//! These follow the Windows SDK's x64 ABI (the most commonly-correct layout for the
//! 64-bit off_t / pointer-sized handles used throughout Win32).

#![allow(non_snake_case, non_camel_case_types, non_upper_case_globals, dead_code)]

// ─────────────────────────────── base types ───────────────────────────────

/// A pointer-sized signed integer.
pub type LONG_PTR = isize;
/// A pointer-sized unsigned integer.
pub type ULONG_PTR = usize;
/// A 32-bit signed integer.
pub type LONG = i32;
/// A 32-bit unsigned integer.
pub type ULONG = u32;
/// A Boolean (Win32 BOOL is a 32-bit int, not Rust's bool).
pub type BOOL = i32;
/// A byte.
pub type BYTE = u8;
/// A 16-bit unsigned integer.
pub type WORD = u16;
/// A 32-bit unsigned integer (DWORD).
pub type DWORD = u32;
/// A 64-bit unsigned integer.
pub type ULONGLONG = u64;
/// A character (wchar_t on Windows).
pub type WCHAR = u16;
/// A handle to an object.
pub type HANDLE = *mut core::ffi::c_void;
/// A window handle.
pub type HWND = *mut core::ffi::c_void;
/// A device context handle.
pub type HDC = *mut core::ffi::c_void;
/// A module handle.
pub type HMODULE = *mut core::ffi::c_void;
/// An icon handle.
pub type HICON = *mut core::ffi::c_void;
/// An accelerator handle.
pub type HACCEL = *mut core::ffi::c_void;
/// A menu handle.
pub type HMENU = *mut core::ffi::c_void;
/// A cursor handle.
pub type HCURSOR = *mut core::ffi::c_void;
/// A brush handle.
pub type HBRUSH = *mut core::ffi::c_void;
/// A logical font handle.
pub type HFONT = *mut core::ffi::c_void;
/// A bitmap handle.
pub type HBITMAP = *mut core::ffi::c_void;
/// A palette handle.
pub type HPALETTE = *mut core::ffi::c_void;

/// A 16-byte .NET-style IID / CLSID / GUID.
#[repr(C)]
#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash)]
pub struct GUID {
    pub data1: u32,
    pub data2: u16,
    pub data3: u16,
    pub data4: [u8; 8],
}

/// A 64-bit FILETIME in 100-ns intervals since 1601-01-01.
#[repr(C)]
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct FILETIME {
    pub dwLowDateTime: DWORD,
    pub dwHighDateTime: DWORD,
}

/// A monotonic-ish SYSTEMTIME.
#[repr(C)]
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct SYSTEMTIME {
    pub wYear: WORD,
    pub wMonth: WORD,
    pub wDayOfWeek: WORD,
    pub wDay: WORD,
    pub wHour: WORD,
    pub wMinute: WORD,
    pub wSecond: WORD,
    pub wMilliseconds: WORD,
}

// ─────────────────────────────── handshake constants ───────────────────────────────

/// Stet's private SendInput self-tag (dwExtraInfo low 32 bits). Mirrors
/// stet-core's `STET_DW_EXTRA_INFO`. WAS: 0x7374_6574 ("stet" little-endian).
pub const STET_DW_EXTRA_INFO: ULONG_PTR = 0x7374_6574;

/// IPC: 4 MiB maximum serialized UTF-8 frame.
pub const MAX_FRAME_BYTES: usize = 4 * 1024 * 1024;

/// IPC: handshake deadline in ms.
pub const HANDSHAKE_DEADLINE_MS: u32 = 2000;

/// IPC: per-operation / commit deadline ceilings (§4.1).
pub const COMMIT_DEADLINE_MS: u32 = 5000;
pub const COMMIT_ABSOLUTE_CEILING_MS: u32 = 10000;

/// IPC: named pipe name. Production uses the SID-suffixed randomized form where
/// the `with_sid` helper composes it; this is the fixed v2 base name.
pub const PIPE_BASE_NAME: &str = r"\\.\pipe\stet_ipc_v2";

// ─────────────────────────────── types.rs: constants (window / pipe / input) ───────────────────────────────

/// RegisterHotKey modifiers.
pub const MOD_ALT: u32 = 0x0001;
pub const MOD_CONTROL: u32 = 0x0002;
pub const MOD_SHIFT: u32 = 0x0004;
pub const MOD_WIN: u32 = 0x0008;
pub const MOD_NOREPEAT: u32 = 0x4000;

/// Virtual-key codes used by Stet.
pub const VK_CONTROL: u32 = 0x11;
pub const VK_SHIFT: u32 = 0x10;
pub const VK_MENU: u32 = 0x12;
pub const VK_INSERT: u32 = 0x2D;
pub const VK_F9: u32 = 0x78;
pub const VK_C: u32 = 0x43;
pub const VK_V: u32 = 0x56;

/// Key event flags for SendInput / KBDLLHOOKSTRUCT.
pub const KEYEVENTF_EXTENDEDKEY: u32 = 0x0001;
pub const KEYEVENTF_KEYUP: u32 = 0x0002;
pub const KEYEVENTF_UNICODE: u32 = 0x0004;
pub const KEYEVENTF_SCANCODE: u32 = 0x0008;
/// KBDLLHOOKSTRUCT.injected — an injected event. NEVER treated as Stet-owned.
pub const LLKHF_INJECTED: u32 = 0x10;
pub const LLKHF_ALTDOWN: u32 = 0x20;
pub const LLKHF_UP: u32 = 0x80;

/// SetWindowsHookEx hook types.
pub const WH_KEYBOARD_LL: i32 = 13;
pub const WH_KEYBOARD: i32 = 2;

/// WinEvent hook events (§2f).
pub const EVENT_SYSTEM_FOREGROUND: u32 = 0x0003;
pub const EVENT_OBJECT_FOCUS: u32 = 0x8005;

/// WINEVENT (SetWinEventHook) flags/dll.
pub const WINEVENT_OUTOFCONTEXT: u32 = 0x0000;
pub const WINEVENT_SKIPOWNPROCESS: u32 = 0x0002;
pub const WINEVENT_SKIPOWNTHREAD: u32 = 0x0004;

/// Clipboard formats (§2d).
pub const CF_UNICODETEXT: u32 = 13;
pub const CF_HDROP: u32 = 15;
pub const CF_TEXT: u32 = 1;
pub const CF_BITMAP: u32 = 2;
pub const CF_ENHMETAFILE: u32 = 14;

/// Clipboard manager suppression flags (§2d from plan §0f).
pub const CLIPBRD_EJECT_ALLOW: u32 = 0;

/// Pipe directions & flags (§3.1).
pub const PIPE_ACCESS_INBOUND: u32 = 0x00000001;
pub const PIPE_ACCESS_DUPLEX: u32 = 0x00000003;
pub const PIPE_TYPE_BYTE: u32 = 0x00000000;
pub const PIPE_READMODE_BYTE: u32 = 0x00000000;
pub const PIPE_WAIT: u32 = 0x00000000;
pub const PIPE_REJECT_REMOTE_CLIENTS: u32 = 0x00000008;
pub const FILE_FLAG_FIRST_PIPE_INSTANCE: u32 = 0x00080000;
pub const FILE_FLAG_OVERLAPPED: u32 = 0x40000000;

/// Pipe generic access (for open-side).
pub const GENERIC_READ: u32 = 0x80000000;
pub const GENERIC_WRITE: u32 = 0x40000000;

/// Wait / open constants.
pub const INFINITE: u32 = 0xFFFFFFFF;
pub const INVALID_HANDLE_VALUE: HANDLE = usize::MAX as *mut core::ffi::c_void;
pub const WAIT_OBJECT_0: u32 = 0;
pub const WAIT_ABANDONED: u32 = 0x80;
pub const WAIT_TIMEOUT: u32 = 0x102;
pub const WAIT_FAILED: u32 = 0xFFFFFFFF;
pub const ERROR_ACCESS_DENIED: u32 = 5;
pub const ERROR_BROKEN_PIPE: u32 = 109;
pub const ERROR_PIPE_BUSY: u32 = 231;
pub const ERROR_NO_DATA: u32 = 232;
pub const ERROR_PIPE_NOT_CONNECTED: u32 = 233;
pub const ERROR_SUCCESS: u32 = 0;

/// Security descriptor / ACL control bits.
pub const DACL_SECURITY_INFORMATION: u32 = 0x00000004;
pub const SE_OBJECT_TYPE: u32 = 0;
pub const SECURITY_ATTRIBUTES_FLAG: u32 = 1;

// ─────────────────────────────── structures ───────────────────────────────

/// x64 INPUT record (a union of MOUSEINPUT/KEYBDINPUT/HARDWAREINPUT).
/// KEYBDINPUT is the only one Stet uses, but the union must be sized to the largest
/// member so the array stride is correct.
#[repr(C)]
#[derive(Clone, Copy, Debug)]
pub struct INPUT {
    pub r#type: DWORD,
    pub u: INPUT_UNION,
}

/// The union member. Repr(C) unions are stable since Rust 1.49.
#[repr(C)]
#[derive(Clone, Copy)]
pub union INPUT_UNION {
    pub mi: MOUSEINPUT,
    pub ki: KEYBDINPUT,
    pub hi: HARDWAREINPUT,
}

#[repr(C)]
#[derive(Clone, Copy, Debug)]
pub struct MOUSEINPUT {
    pub dx: LONG,
    pub dy: LONG,
    pub mouseData: DWORD,
    pub dwFlags: DWORD,
    pub time: DWORD,
    pub dwExtraInfo: ULONG_PTR,
}

#[repr(C)]
#[derive(Clone, Copy, Debug)]
pub struct KEYBDINPUT {
    pub wVk: WORD,
    pub wScan: WORD,
    pub dwFlags: DWORD,
    pub time: DWORD,
    pub dwExtraInfo: ULONG_PTR,
}

#[repr(C)]
#[derive(Clone, Copy, Debug)]
pub struct HARDWAREINPUT {
    pub uMsg: DWORD,
    pub wParamL: WORD,
    pub wParamH: WORD,
}

/// INPUT type constants.
pub const INPUT_KEYBOARD: u32 = 1;

/// KBDLLHOOKSTRUCT (low-level keyboard hook).
#[repr(C)]
#[derive(Clone, Copy, Debug)]
pub struct KBDLLHOOKSTRUCT {
    pub vkCode: DWORD,
    pub scanCode: DWORD,
    pub flags: DWORD,
    pub time: DWORD,
    pub dwExtraInfo: ULONG_PTR,
}

/// MSG (message structure) — minimal for the message-only window pump.
#[repr(C)]
#[derive(Clone, Copy, Debug)]
pub struct MSG {
    pub hwnd: HWND,
    pub message: u32,
    pub wParam: usize,
    pub lParam: isize,
    pub time: DWORD,
    pub pt: POINT,
}

#[repr(C)]
#[derive(Clone, Copy, Debug)]
pub struct POINT {
    pub x: LONG,
    pub y: LONG,
}

/// WNDCLASSEXW — the wide-char window class we register for the message-only window.
/// Note: fields are laid out per the x64 SDK order (cbSize first, then styles, etc.).
#[repr(C)]
#[derive(Clone, Copy)]
pub struct WNDCLASSEXW {
    pub cbSize: u32,
    pub style: u32,
    pub lpfnWndProc: WNDPROC,
    pub cbClsExtra: i32,
    pub cbWndExtra: i32,
    pub hInstance: HINSTANCE,
    pub hIcon: HICON,
    pub hCursor: HCURSOR,
    pub hbrBackground: HBRUSH,
    pub lpszMenuName: *const WCHAR,
    pub lpszClassName: *const WCHAR,
    pub hIconSm: HICON,
}

/// Instance handle.
pub type HINSTANCE = *mut core::ffi::c_void;

/// Window procedure callback signature.
pub type WNDPROC = unsafe extern "system" fn(HWND, u32, usize, isize) -> isize;

/// SECURITY_ATTRIBUTES for named-pipe descriptors.
#[repr(C)]
#[derive(Clone, Copy, Debug)]
pub struct SECURITY_ATTRIBUTES {
    pub nLength: DWORD,
    pub lpSecurityDescriptor: *mut core::ffi::c_void,
    pub bInheritHandle: BOOL,
}

/// STARTUPINFOEXW for the launcher's handle-list handoff (§3.1 rule 1).
#[repr(C)]
#[derive(Clone, Copy)]
pub struct STARTUPINFOEXW {
    pub StartupInfo: STARTUPINFOW,
    pub lpAttributeList: *mut core::ffi::c_void,
}

#[repr(C)]
#[derive(Clone, Copy)]
pub struct STARTUPINFOW {
    pub cb: DWORD,
    pub lpReserved: *mut WCHAR,
    pub lpDesktop: *mut WCHAR,
    pub lpTitle: *mut WCHAR,
    pub dwX: DWORD,
    pub dwY: DWORD,
    pub dwXSize: DWORD,
    pub dwYSize: DWORD,
    pub dwXCountChars: DWORD,
    pub dwYCountChars: DWORD,
    pub dwFillAttribute: DWORD,
    pub dwFlags: DWORD,
    pub wShowWindow: WORD,
    pub cbReserved2: WORD,
    pub lpReserved2: *mut BYTE,
    pub hStdInput: HANDLE,
    pub hStdOutput: HANDLE,
    pub hStdError: HANDLE,
}

/// PROCESS_INFORMATION.
#[repr(C)]
#[derive(Clone, Copy, Debug)]
pub struct PROCESS_INFORMATION {
    pub hProcess: HANDLE,
    pub hThread: HANDLE,
    pub dwProcessId: DWORD,
    pub dwThreadId: DWORD,
}

/// JOBOBJECT_BASIC_LIMIT_INFORMATION — sets the kill-on-close limit.
#[repr(C)]
#[derive(Clone, Copy)]
pub struct JOBOBJECT_BASIC_LIMIT_INFORMATION {
    pub PerProcessUserTimeLimit: i64,
    pub PerJobUserTimeLimit: i64,
    pub LimitFlags: DWORD,
    pub MinimumWorkingSetSize: usize,
    pub MaximumWorkingSetSize: usize,
    pub ActiveProcessLimit: DWORD,
    pub Affinity: ULONG_PTR,
    pub PriorityClass: DWORD,
    pub SchedulingClass: DWORD,
}

/// JOBOBJECT_EXTENDED_LIMIT_INFORMATION (we set BasicLimitInformation.LimitFlags).
#[repr(C)]
#[derive(Clone, Copy)]
pub struct JOBOBJECT_EXTENDED_LIMIT_INFORMATION {
    pub BasicLimitInformation: JOBOBJECT_BASIC_LIMIT_INFORMATION,
    pub IoInfo: IO_COUNTERS,
    pub ProcessMemoryLimit: usize,
    pub JobMemoryLimit: usize,
    pub PeakProcessMemoryUsed: usize,
    pub PeakJobMemoryUsed: usize,
}

#[repr(C)]
#[derive(Clone, Copy)]
pub struct IO_COUNTERS {
    pub ReadOperationCount: ULONGLONG,
    pub WriteOperationCount: ULONGLONG,
    pub OtherOperationCount: ULONGLONG,
    pub ReadTransferCount: ULONGLONG,
    pub WriteTransferCount: ULONGLONG,
    pub OtherTransferCount: ULONGLONG,
}

/// Job object limits.
pub const JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE: DWORD = 0x2000;
pub const JOB_OBJECT_LIMIT_SILENT_BREAKAWAY_OK: DWORD = 0x1000;

/// Serialized INPUT — gives us a storable seed for tests / logging.
impl core::fmt::Debug for INPUT_UNION {
    fn fmt(&self, f: &mut core::fmt::Formatter<'_>) -> core::fmt::Result {
        unsafe { write!(f, "INPUT_UNION(ki.vk={:#x})", self.ki.wVk) }
    }
}
impl core::fmt::Debug for WNDCLASSEXW {
    fn fmt(&self, f: &mut core::fmt::Formatter<'_>) -> core::fmt::Result {
        f.debug_struct("WNDCLASSEXW")
            .field("cbSize", &self.cbSize)
            .field("style", &self.style)
            .field("cbClsExtra", &self.cbClsExtra)
            .field("cbWndExtra", &self.cbWndExtra)
            .finish()
    }
}
impl core::fmt::Debug for STARTUPINFOEXW {
    fn fmt(&self, f: &mut core::fmt::Formatter<'_>) -> core::fmt::Result {
        f.debug_struct("STARTUPINFOEXW")
            .field("cb", &self.StartupInfo.cb)
            .field("lpAttributeList", &self.lpAttributeList)
            .finish()
    }
}
impl core::fmt::Debug for STARTUPINFOW {
    fn fmt(&self, f: &mut core::fmt::Formatter<'_>) -> core::fmt::Result {
        f.debug_struct("STARTUPINFOW")
            .field("cb", &self.cb)
            .field("dwFlags", &self.dwFlags)
            .finish()
    }
}
impl core::fmt::Debug for JOBOBJECT_EXTENDED_LIMIT_INFORMATION {
    fn fmt(&self, f: &mut core::fmt::Formatter<'_>) -> core::fmt::Result {
        f.debug_struct("JOBOBJECT_EXTENDED_LIMIT_INFORMATION")
            .field("BasicLimitInformation", &self.BasicLimitInformation)
            .field("ProcessMemoryLimit", &self.ProcessMemoryLimit)
            .field("JobMemoryLimit", &self.JobMemoryLimit)
            .finish()
    }
}
impl core::fmt::Debug for JOBOBJECT_BASIC_LIMIT_INFORMATION {
    fn fmt(&self, f: &mut core::fmt::Formatter<'_>) -> core::fmt::Result {
        f.debug_struct("JOBOBJECT_BASIC_LIMIT_INFORMATION")
            .field("LimitFlags", &self.LimitFlags)
            .field("ActiveProcessLimit", &self.ActiveProcessLimit)
            .field("PriorityClass", &self.PriorityClass)
            .finish()
    }
}
impl core::fmt::Debug for IO_COUNTERS {
    fn fmt(&self, f: &mut core::fmt::Formatter<'_>) -> core::fmt::Result {
        f.debug_struct("IO_COUNTERS").finish()
    }
}