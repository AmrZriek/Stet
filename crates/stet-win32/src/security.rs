//! SID / DACL construction and pipe security-descriptor helpers (§3.1 rules 2-6).
//!
//! The Stet pipe must carry an explicit DACL granting the authenticated user's SID only,
//! with NO Everyone / anonymous access. The pure `dacl_allows` decision logic lives in
//! `crates/stet-core/src/pipe_policy.rs` (already verified). This module is the FFI
//! surface that *builds* the descriptor.

#![allow(non_snake_case, non_camel_case_types, non_upper_case_globals, dead_code)]

use crate::types::*;

// ── advapi32 ────────────────────────────────────────────────────────────────
#[link(name = "advapi32")]
extern "system" {
    fn AllocateAndInitializeSid(
        pIdentifierAuthority: *const SID_IDENTIFIER_AUTHORITY,
        nSubAuthorityCount: BYTE,
        nSubAuthority0: DWORD,
        nSubAuthority1: DWORD,
        nSubAuthority2: DWORD,
        nSubAuthority3: DWORD,
        nSubAuthority4: DWORD,
        nSubAuthority5: DWORD,
        nSubAuthority6: DWORD,
        nSubAuthority7: DWORD,
        pSid: *mut PSID,
    ) -> BOOL;
    fn GetTokenInformation(
        TokenHandle: HANDLE,
        TokenInformationClass: u32,
        TokenInformation: *mut core::ffi::c_void,
        TokenInformationLength: DWORD,
        ReturnLength: *mut DWORD,
    ) -> BOOL;
    fn ConvertSidToStringSidW(Sid: PSID, StringSid: *mut *mut WCHAR) -> BOOL;
    fn GetTokenUser(TokenHandle: HANDLE, UserInfo: *mut TOKEN_USER) -> BOOL;
    fn SetSecurityInfo(
        handle: HANDLE,
        objectType: u32,
        securityInformation: u32,
        psidOwner: PSID,
        psidGroup: PSID,
        pDacl: *mut ACL,
        pSacl: *mut ACL,
    ) -> u32;
    fn InitializeSecurityDescriptor(
        pSecurityDescriptor: *mut SECURITY_DESCRIPTOR,
        dwRevision: u32,
    ) -> BOOL;
    fn SetSecurityDescriptorDacl(
        pSecurityDescriptor: *mut SECURITY_DESCRIPTOR,
        bDaclPresent: BOOL,
        pDacl: *const ACL,
        bDaclDefaulted: BOOL,
    ) -> BOOL;
    fn InitializeAcl(pAcl: *mut ACL, nAclLength: DWORD, dwAclRevision: u32) -> BOOL;
    fn AddAccessAllowedAce(pAcl: *mut ACL, dwAceRevision: u32, AccessMask: DWORD, pSid: PSID) -> BOOL;
    fn AddAccessDeniedAce(pAcl: *mut ACL, dwAceRevision: u32, AccessMask: DWORD, pSid: PSID) -> BOOL;
    fn CreateWellKnownSid(
        WellKnownSidType: u32,
        DomainSid: PSID,
        pSid: *mut BYTE,
        cbSid: *mut DWORD,
    ) -> BOOL;
    fn LocalFree(hMem: HANDLE) -> HANDLE;
}

// ── kernel32 ────────────────────────────────────────────────────────────────
#[link(name = "kernel32")]
extern "system" {
    fn OpenProcessToken(ProcessHandle: HANDLE, DesiredAccess: DWORD, TokenHandle: *mut HANDLE) -> BOOL;
    fn GetCurrentProcess() -> HANDLE;
}

// ── types ───────────────────────────────────────────────────────────────────

pub type PSID = *mut core::ffi::c_void;

#[repr(C)]
#[derive(Clone, Copy)]
pub struct SID_IDENTIFIER_AUTHORITY {
    pub Value: [BYTE; 6],
}

#[repr(C, align(8))]
#[derive(Clone, Copy)]
pub struct SECURITY_DESCRIPTOR {
    pub revision: BYTE,
    pub sbz1: BYTE,
    pub control: WORD,
    pub owner: *mut core::ffi::c_void,
    pub group: *mut core::ffi::c_void,
    pub sacl: *mut core::ffi::c_void,
    pub dacl: *mut core::ffi::c_void,
}

#[repr(C, align(8))]
#[derive(Clone, Copy)]
pub struct ACL {
    pub acl_revision: BYTE,
    pub sbz1: BYTE,
    pub acl_size: WORD,
    pub ace_count: WORD,
    pub sbz2: WORD,
}

#[repr(C)]
#[derive(Clone, Copy)]
pub struct TOKEN_USER {
    pub User: SID_AND_ATTRIBUTES,
}

#[repr(C)]
#[derive(Clone, Copy)]
pub struct SID_AND_ATTRIBUTES {
    pub Sid: PSID,
    pub Attributes: DWORD,
}

// ── constants ───────────────────────────────────────────────────────────────

pub const TokenUser: u32 = 1;
pub const SECURITY_MAX_SID_SIZE: usize = 68;
pub const SECURITY_DESCRIPTOR_REVISION: u32 = 1;
pub const ACL_REVISION: u32 = 2;
pub const SECURITY_NT_AUTHORITY: SID_IDENTIFIER_AUTHORITY = SID_IDENTIFIER_AUTHORITY { Value: [0, 0, 0, 0, 0, 5] };
pub const WinWorldSid: u32 = 1;
pub const WinLocalSystemSid: u32 = 9;
pub const WinCreatorOwnerSid: u32 = 3;
/// Full access mask for named pipes granting standard rights, synchronize,
/// read/write data, and read/write attributes (0x001F_01FF). Standard Win32
/// file open (`open(pipe, "r+b")`) checks for attribute rights and synchronize;
/// missing bits cause ERROR_ACCESS_DENIED (13 / Permission denied).
pub const PIPE_ALL_ACCESS: DWORD = 0x001F_01FF;
pub const PIPE_ACCESS_READ: DWORD = 0x0000_0101;
pub const GENERIC_ALL: DWORD = 0x10000000;
const TOKEN_QUERY: DWORD = 0x0008;
pub const SE_OBJECT_TYPE: u32 = 1; // SE_FILE_OBJECT for named pipes
pub const DACL_SECURITY_INFORMATION: u32 = 0x00000004;

// ── wrappers ────────────────────────────────────────────────────────────────

/// Build a SID for a well-known account into a caller-provided buffer.
/// SAFETY: sid_buf must point to at least `cb_sid` bytes; the OS writes the SID there.
pub unsafe fn create_well_known_sid(wk: u32, sid_buf: *mut BYTE, cb_sid: *mut DWORD) -> bool {
    CreateWellKnownSid(wk, core::ptr::null_mut(), sid_buf, cb_sid) != 0
}

/// Get the current process' user SID from an OPEN process token into a caller-provided 8-byte aligned buffer.
/// SAFETY: token_handle must be a valid OPEN process token; `buf` must remain alive as long as returned PSID is used.
pub unsafe fn token_user_sid<'buf>(token_handle: HANDLE, buf: &'buf mut [u64; 128]) -> Result<PSID, DWORD> {
    // GetTokenInformation writes a TOKEN_USER whose first field (SID_AND_ATTRIBUTES.Sid)
    // is a pointer -> needs 8-byte alignment. A [u64; N] is 8-aligned; a [u8; N] is not.
    let mut ret: DWORD = 0;
    let ok = GetTokenInformation(
        token_handle,
        TokenUser,
        buf.as_mut_ptr() as *mut core::ffi::c_void,
        (std::mem::size_of_val(buf)) as DWORD,
        &mut ret,
    );
    if ok == 0 {
        return Err(crate::pipe::last_error());
    }
    let user = &*(buf.as_ptr() as *const core::ffi::c_void as *const TOKEN_USER);
    Ok(user.User.Sid)
}

/// Open the current process' token for querying.
pub unsafe fn open_current_token() -> Result<HANDLE, DWORD> {
    let proc_handle = GetCurrentProcess();
    let mut token: HANDLE = core::ptr::null_mut();
    let ok = OpenProcessToken(proc_handle, TOKEN_QUERY, &mut token);
    if ok == 0 {
        Err(crate::pipe::last_error())
    } else {
        Ok(token)
    }
}

/// Initialize an ACL to allow only `allowed_sid` with the given access mask.
/// SAFETY: acl_buf must point to `acl_buf_len` writable bytes.
pub unsafe fn build_acl_allow_only(
    acl_buf: *mut ACL,
    acl_buf_len: usize,
    allowed_sid: PSID,
    access_mask: DWORD,
) -> Result<(), DWORD> {
    let init = InitializeAcl(acl_buf, acl_buf_len as DWORD, ACL_REVISION);
    if init == 0 {
        return Err(crate::pipe::last_error());
    }
    let add = AddAccessAllowedAce(acl_buf, ACL_REVISION, access_mask, allowed_sid);
    if add == 0 {
        return Err(crate::pipe::last_error());
    }
    Ok(())
}

/// Initialize a security descriptor and attach a restrictive DACL.
/// SAFETY: sd must point to at least the size of SECURITY_DESCRIPTOR bytes.
pub unsafe fn init_security_descriptor(
    sd: *mut SECURITY_DESCRIPTOR,
    dacl: *const ACL,
) -> Result<(), DWORD> {
    let init = InitializeSecurityDescriptor(sd, SECURITY_DESCRIPTOR_REVISION);
    if init == 0 {
        return Err(crate::pipe::last_error());
    }
    let set = SetSecurityDescriptorDacl(sd, 1, dacl, 0);
    if set == 0 {
        return Err(crate::pipe::last_error());
    }
    Ok(())
}

/// Apply a restrictive DACL to a pipe handle via SetSecurityInfo.
/// SAFETY: handle must be a valid pipe handle.
pub unsafe fn apply_restrictive_dacl(handle: HANDLE, dacl: *mut ACL) -> Result<(), DWORD> {
    let rc = SetSecurityInfo(
        handle,
        SE_OBJECT_TYPE,
        DACL_SECURITY_INFORMATION,
        core::ptr::null_mut(),
        core::ptr::null_mut(),
        dacl,
        core::ptr::null_mut(),
    );
    if rc != ERROR_SUCCESS {
        Err(rc)
    } else {
        Ok(())
    }
}
