//! Suspended-start + Job Object handle handoff (§3.1 rule 1, §4.1).
//!
//! The UI sends the fresh secret over an anonymous/stdin pipe; the launcher creates a
//! private bootstrap handle containing it, starts `stet-core` suspended with an explicit
//! inherited-handle list, assigns it to a JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE job before
//! ResumeThread, and keeps a supervisor alive. The pure payload framing lives in
//! `crates/stet-core/src/launcher.rs` (verified). This module is the FFI surface.

#![allow(non_snake_case, non_camel_case_types, non_upper_case_globals, dead_code)]

use crate::types::*;

#[link(name = "kernel32")]
extern "system" {
        fn CreateJobObjectW(lpJobAttributes: *const SECURITY_ATTRIBUTES, lpName: *const WCHAR) -> HANDLE;

        fn SetInformationJobObject(
        hJob: HANDLE,
        JobObjectInformationClass: u32,
        lpJobObjectInformation: *mut core::ffi::c_void,
        cbJobObjectInformationLength: DWORD,
    ) -> BOOL;

        fn AssignProcessToJobObject(hJob: HANDLE, hProcess: HANDLE) -> BOOL;

        fn TerminateJobObject(hJob: HANDLE, uExitCode: u32) -> BOOL;

        fn OpenProcess(
        dwDesiredAccess: DWORD,
        bInheritHandle: BOOL,
        dwProcessId: DWORD,
    ) -> HANDLE;

        fn GetCurrentProcessId() -> DWORD;

        fn InitializeProcThreadAttributeList(
        lpAttributeList: *mut core::ffi::c_void,
        dwAttributeCount: DWORD,
        dwFlags: DWORD,
        lpSize: *mut usize,
    ) -> BOOL;

        fn UpdateProcThreadAttribute(
        lpAttributeList: *mut core::ffi::c_void,
        dwFlags: DWORD,
        Attribute: ULONG_PTR,
        lpValue: *mut core::ffi::c_void,
        cbSize: usize,
        lpPreviousValue: *mut core::ffi::c_void,
        lpReturnSize: *mut usize,
    ) -> BOOL;

        fn DeleteProcThreadAttributeList(lpAttributeList: *mut core::ffi::c_void) -> VOID;

        fn CreateProcessW(
        lpApplicationName: *const WCHAR,
        lpCommandLine: *mut WCHAR,
        lpProcessAttributes: *const SECURITY_ATTRIBUTES,
        lpThreadAttributes: *const SECURITY_ATTRIBUTES,
        bInheritHandles: BOOL,
        dwCreationFlags: DWORD,
        lpEnvironment: *mut core::ffi::c_void,
        lpCurrentDirectory: *const WCHAR,
        lpStartupInfo: *mut STARTUPINFOEXW,
        lpProcessInformation: *mut PROCESS_INFORMATION,
    ) -> BOOL;

        fn ResumeThread(hThread: HANDLE) -> DWORD;

        fn SuspendThread(hThread: HANDLE) -> DWORD;

        fn WaitForSingleObject(hHandle: HANDLE, dwMilliseconds: DWORD) -> DWORD;
}

/// Void type.
pub type VOID = ();

/// Process creation flags.
pub const CREATE_SUSPENDED: DWORD = 0x0000_0004;
pub const CREATE_NEW_PROCESS_GROUP: DWORD = 0x0000_0200;
pub const CREATE_UNICODE_ENVIRONMENT: DWORD = 0x0000_0400;
pub const EXTENDED_STARTUPINFO_PRESENT: DWORD = 0x0008_0000;

/// Job object info classes.
pub const JobObjectExtendedLimitInformation: u32 = 9;

/// PROC_THREAD_ATTRIBUTE_HANDLE_LIST (the explicit inherited-handle list).
pub const PROC_THREAD_ATTRIBUTE_HANDLE_LIST: usize = 0x0002_0000;

/// Desired access for OpenProcess on the target.
pub const PROCESS_ALL_ACCESS: DWORD = 0x001F_0FFF;

/// Create a kill-on-close job object.
pub fn create_job_object() -> Result<HANDLE, DWORD> {
    let job = unsafe { CreateJobObjectW(core::ptr::null(), core::ptr::null()) };
    if job.is_null() {
        Err(crate::pipe::last_error())
    } else {
        Ok(job)
    }
}

/// Set the job to kill-on-close.
pub unsafe fn set_kill_on_close(job: HANDLE) -> Result<(), DWORD> {
    let mut info: JOBOBJECT_EXTENDED_LIMIT_INFORMATION = unsafe { core::mem::zeroed() };
    info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
    let ok = SetInformationJobObject(
        job,
        JobObjectExtendedLimitInformation,
        &mut info as *mut _ as *mut core::ffi::c_void,
        core::mem::size_of::<JOBOBJECT_EXTENDED_LIMIT_INFORMATION>() as DWORD,
    );
    if ok == 0 {
        Err(crate::pipe::last_error())
    } else {
        Ok(())
    }
}

/// Assign a process to a job (must happen before ResumeThread).
pub unsafe fn assign_to_job(job: HANDLE, process: HANDLE) -> Result<(), DWORD> {
    let ok = AssignProcessToJobObject(job, process);
    if ok == 0 {
        Err(crate::pipe::last_error())
    } else {
        Ok(())
    }
}

/// Terminate the job (kills all attached processes).
pub unsafe fn terminate_job(job: HANDLE, code: u32) -> bool {
    TerminateJobObject(job, code) != 0
}

/// Initialize a proc-thread attribute list. Returns the required size as a pointer
/// for the first call (can be null to query size).
/// SAFETY: first call with null to get size, second call fills.
pub unsafe fn init_attribute_list(buf: *mut core::ffi::c_void, count: u32, size: &mut usize) -> Result<(), DWORD> {
    let ok = InitializeProcThreadAttributeList(buf, count, 0, size as *mut usize);
    if ok == 0 {
        Err(crate::pipe::last_error())
    } else {
        Ok(())
    }
}

/// Update the attributed list with an explicit handle list (the inherited handles).
/// SAFETY: list must be initialized; handles must be valid and inheritable.
pub unsafe fn update_handle_list(
    list: *mut core::ffi::c_void,
    handles: &[HANDLE],
) -> Result<(), DWORD> {
    let ok = UpdateProcThreadAttribute(
        list,
        0,
        PROC_THREAD_ATTRIBUTE_HANDLE_LIST,
        handles.as_ptr() as *mut core::ffi::c_void,
        handles.len() * core::mem::size_of::<HANDLE>(),
        core::ptr::null_mut(),
        core::ptr::null_mut(),
    );
    if ok == 0 {
        Err(crate::pipe::last_error())
    } else {
        Ok(())
    }
}

/// Delete the attribute list.
pub unsafe fn delete_attribute_list(list: *mut core::ffi::c_void) {
    DeleteProcThreadAttributeList(list);
}

/// Suspend/Resume.
pub unsafe fn resume_thread(thread: HANDLE) -> u32 {
    ResumeThread(thread)
}
pub unsafe fn suspend_thread(thread: HANDLE) -> u32 {
    SuspendThread(thread)
}

/// Get the current process id.
pub fn current_process_id() -> u32 {
    unsafe { GetCurrentProcessId() }
}

/// Open a process by id.
pub fn open_process(pid: u32) -> Result<HANDLE, DWORD> {
    let h = unsafe { OpenProcess(PROCESS_ALL_ACCESS, 0, pid) };
    if h.is_null() {
        Err(crate::pipe::last_error())
    } else {
        Ok(h)
    }
}