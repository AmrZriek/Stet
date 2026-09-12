//! Stet native core daemon: stdin bootstrap → restrictive pipe → IPC sessions.
//!
//! The Python parent generates 32 random bytes, writes exactly [`BOOTSTRAP_LEN`]
//! bytes to our stdin (`STET` + version + secret + client PID), and keeps the
//! secret in memory. The daemon never takes the secret from env/argv/logs.
//! Bootstrap failure exits(2) with a stderr message and never serves.
//! The pipe carries a current-user-only DACL; DACL failure fails closed.

#[cfg(windows)]
use ffi_skel::pipe::{
    accept_client, create_pipe_server, disconnect, peek_available, pipe_name, read_bytes,
    write_bytes,
};
#[cfg(windows)]
use ffi_skel::security::{
    build_acl_allow_only, init_security_descriptor, open_current_token, token_user_sid,
    ACL, PIPE_ALL_ACCESS, SECURITY_DESCRIPTOR,
};
#[cfg(windows)]
use ffi_skel::types::{
    DWORD, ERROR_BROKEN_PIPE, ERROR_NO_DATA, ERROR_PIPE_NOT_CONNECTED, HANDLE, PIPE_BASE_NAME,
    SECURITY_ATTRIBUTES,
};
#[cfg(windows)]
use stet_core::codec::Transport;
#[cfg(windows)]
use stet_core::frame::{Frame, IpcError, IpcResult, MAX_FRAME_BYTES};
#[cfg(windows)]
use stet_core::launcher::{decode_bootstrap, BootstrapPayload};
#[cfg(windows)]
use stet_core::pipe_policy::LAUNCH_SECRET_LEN;
#[cfg(windows)]
use stet_core::session::{Session, StepOutcome};

/// Exact stdin bootstrap size: magic(4) + version(1) + len(4) + secret(32) + pid(4).
#[cfg(windows)]
const BOOTSTRAP_LEN: usize = 4 + 1 + 4 + LAUNCH_SECRET_LEN + 4;

#[cfg(windows)]
fn main() {
    let payload = match read_stdin_bootstrap() {
        Ok(payload) => payload,
        Err(kind) => {
            eprintln!("stet-core: invalid stdin bootstrap ({kind}); refusing to serve");
            std::process::exit(2);
        }
    };
    // These buffers must outlive the pipe handle: the security descriptor
    // borrows the ACL for the lifetime of the server handle.
    let mut sid_buf = [0u64; 128];
    let mut acl_buf = [0u64; 256];
    // SAFETY: fully initialized by init_security_descriptor before use.
    let mut sd: SECURITY_DESCRIPTOR = unsafe { core::mem::zeroed() };
    let security = match build_pipe_security(&mut sid_buf, &mut acl_buf, &mut sd) {
        Ok(security) => security,
        Err(code) => {
            eprintln!("stet-core: cannot build restrictive DACL (err {code}); refusing to serve");
            std::process::exit(2);
        }
    };
    let name = pipe_name(PIPE_BASE_NAME);
    let server = match create_pipe_server(&name, &security as *const SECURITY_ATTRIBUTES) {
        Ok(handle) => handle,
        Err(code) => {
            eprintln!("stet-core: cannot create pipe (err {code})");
            std::process::exit(2);
        }
    };
    let secret = payload.secret;
    loop {
        match accept_client(server) {
            Ok(()) => {}
            Err(code) => {
                eprintln!("stet-core: accept failed (err {code})");
                continue;
            }
        }
        serve_one(server, secret);
        disconnect(server);
    }
}

#[cfg(not(windows))]
fn main() {
    eprintln!("stet-core: Windows-only daemon; unsupported on this platform");
    std::process::exit(2);
}

/// Read exactly [`BOOTSTRAP_LEN`] stdin bytes and decode the bootstrap.
#[cfg(windows)]
fn read_stdin_bootstrap() -> Result<BootstrapPayload, &'static str> {
    let stdin = std::io::stdin();
    let mut handle = stdin.lock();
    read_bootstrap(&mut handle)
}

/// Decode a bootstrap from any byte reader (testable without a real stdin).
#[cfg(windows)]
fn read_bootstrap(reader: &mut impl std::io::Read) -> Result<BootstrapPayload, &'static str> {
    let mut buf = [0u8; BOOTSTRAP_LEN];
    reader.read_exact(&mut buf).map_err(|_| "bootstrap_read_failed")?;
    decode_bootstrap(&buf)
}

/// Build the current-user-only pipe security attributes. Fail-closed: any OS
/// failure is an Err and the caller refuses to serve.
#[cfg(windows)]
fn build_pipe_security(
    sid_buf: &mut [u64; 128],
    acl_buf: &mut [u64; 256],
    sd: &mut SECURITY_DESCRIPTOR,
) -> Result<SECURITY_ATTRIBUTES, DWORD> {
    unsafe {
        let token = open_current_token()?;
        let sid = token_user_sid(token, sid_buf)?;
        ffi_skel::pipe::close_handle(token);
        build_acl_allow_only(
            acl_buf.as_mut_ptr() as *mut ACL,
            core::mem::size_of_val(acl_buf),
            sid,
            PIPE_ALL_ACCESS,
        )?;
        init_security_descriptor(sd, acl_buf.as_ptr() as *const ACL)?;
        Ok(SECURITY_ATTRIBUTES {
            nLength: core::mem::size_of::<SECURITY_ATTRIBUTES>() as DWORD,
            lpSecurityDescriptor: sd as *mut SECURITY_DESCRIPTOR as *mut core::ffi::c_void,
            bInheritHandle: 0,
        })
    }
}

/// Run one client session to Closed/Failed.
#[cfg(windows)]
fn serve_one(server: HANDLE, secret: [u8; LAUNCH_SECRET_LEN]) {
    stet_core::hotkey_host::set_session_handle(Some(server as isize));
    let mut session = Session::new(PipeTransport { handle: server }, secret);
    loop {
        match session.step() {
            Ok(StepOutcome::Handled) => {}
            Ok(StepOutcome::Closed) => break,
            Ok(StepOutcome::Failed(e)) => {
                eprintln!("stet-core: session failed ({})", e.as_str());
                break;
            }
            Err(e) => {
                eprintln!("stet-core: session ended ({})", e.as_str());
                break;
            }
        }
    }
    stet_core::hotkey_host::set_session_handle(None);
}

/// Byte transport over a connected named-pipe server handle.
#[cfg(windows)]
struct PipeTransport {
    handle: HANDLE,
}

#[cfg(windows)]
impl Transport for PipeTransport {
    fn read_frame(&mut self) -> IpcResult<Option<Frame>> {
        let mut prefix = [0u8; 8];
        if pipe_read_exact(self.handle, &mut prefix)?.is_none() {
            return Ok(None);
        }
        let len = u64::from_be_bytes(prefix) as usize;
        if len > MAX_FRAME_BYTES {
            return Err(IpcError::FrameTooLarge);
        }
        let mut payload = vec![0u8; len];
        if !payload.is_empty() && pipe_read_exact(self.handle, &mut payload)?.is_none() {
            return Err(IpcError::MalformedFrame);
        }
        Ok(Some(Frame { payload }))
    }

    fn write_frame(&mut self, payload: &[u8]) -> IpcResult<()> {
        if payload.len() > MAX_FRAME_BYTES {
            return Err(IpcError::FrameTooLarge);
        }
        let _guard = stet_core::hotkey_host::write_lock().lock().unwrap();
        pipe_write_all(self.handle, &(payload.len() as u64).to_be_bytes())?;
        pipe_write_all(self.handle, payload)?;
        Ok(())
    }
}

/// Fill `buf` from the pipe. `Ok(None)` is clean EOF at a frame boundary
/// (client disconnect); anything short of that mid-frame is fatal.
#[cfg(windows)]
fn pipe_read_exact(handle: HANDLE, buf: &mut [u8]) -> IpcResult<Option<()>> {
    let mut done: usize = 0;
    while done < buf.len() {
        let remain = buf.len() - done;
        // Non-blocking peek: never enter ReadFile when 0 bytes are available.
        // Calling ReadFile on a synchronous pipe handle when idle locks the
        // kernel file object and wedges concurrent writes (e.g. event frames
        // from the hotkey pump thread).
        match peek_available(handle) {
            Ok((avail, _)) => {
                if avail == 0 {
                    std::thread::sleep(std::time::Duration::from_millis(5));
                    continue;
                }
                let to_read = remain.min(avail as usize);
                let chunk = match unsafe { read_bytes(handle, buf.as_mut_ptr().add(done), to_read) } {
                    Ok(0) => {
                        if done == 0 {
                            return Ok(None);
                        }
                        return Err(IpcError::MalformedFrame);
                    }
                    Ok(n) => n.min(to_read),
                    Err(code) => {
                        if done == 0 && is_clean_disconnect(code) {
                            return Ok(None);
                        }
                        return Err(IpcError::MalformedFrame);
                    }
                };
                done = done.saturating_add(chunk);
            }
            Err(code) => {
                if done == 0 && is_clean_disconnect(code) {
                    return Ok(None);
                }
                return Err(IpcError::MalformedFrame);
            }
        }
    }
    Ok(Some(()))
}
/// True for disconnect codes that mean clean EOF at a frame boundary.
#[cfg(windows)]
fn is_clean_disconnect(code: DWORD) -> bool {
    code == ERROR_BROKEN_PIPE || code == ERROR_PIPE_NOT_CONNECTED || code == ERROR_NO_DATA
}

/// Write a whole buffer to the pipe.
#[cfg(windows)]
fn pipe_write_all(handle: HANDLE, mut buf: &[u8]) -> IpcResult<()> {
    while !buf.is_empty() {
        let n = match unsafe { write_bytes(handle, buf.as_ptr(), buf.len()) } {
            Ok(0) => return Err(IpcError::MalformedFrame),
            Ok(n) => n.min(buf.len()),
            Err(_) => return Err(IpcError::MalformedFrame),
        };
        if n == 0 {
            return Err(IpcError::MalformedFrame);
        }
        buf = &buf[n..];
    }
    Ok(())
}

#[cfg(all(test, windows))]
mod tests {
    use super::*;
    use stet_core::launcher::encode_bootstrap;
    use std::io::Cursor;

    #[test]
    fn bootstrap_stdin_framing_roundtrips() {
        let secret = [0xABu8; LAUNCH_SECRET_LEN];
        let bytes = encode_bootstrap(&secret, 4242);
        assert_eq!(bytes.len(), BOOTSTRAP_LEN);
        assert_eq!(BOOTSTRAP_LEN, 45);
        let mut cursor = Cursor::new(bytes);
        let payload = read_bootstrap(&mut cursor).unwrap();
        assert_eq!(payload.secret, secret);
        assert_eq!(payload.client_pid, 4242);
    }

    #[test]
    fn bootstrap_stdin_short_read_is_rejected() {
        let mut cursor = Cursor::new(vec![0u8; 10]);
        assert_eq!(read_bootstrap(&mut cursor), Err("bootstrap_read_failed"));
    }

    #[test]
    fn bootstrap_stdin_bad_magic_is_rejected() {
        let mut bytes = encode_bootstrap(&[0xABu8; LAUNCH_SECRET_LEN], 1);
        bytes[0] = 0;
        let mut cursor = Cursor::new(bytes);
        assert_eq!(read_bootstrap(&mut cursor), Err("bootstrap_magic_mismatch"));
    }
}
