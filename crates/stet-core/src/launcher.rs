//! Launcher handoff protocol (Phase 2g, §3.1 rule 1).
//!
//! The launcher receives the 256-bit secret through an anonymous/stdin pipe, creates a
//! private bootstrap handle containing it, and passes only that handle to `stet-core` via
//! STARTUPINFOEXW / PROC_THREAD_ATTRIBUTE_HANDLE_LIST. The secret is NEVER exposed in a
//! user-readable file or an ordinary environment variable. This module defines the pure
//! handoff framing + security invariants; the Win32 handle plumbing is the FFI layer.

use crate::pipe_policy::LAUNCH_SECRET_LEN;

/// Bootstrap-handle framing magic (identifies a Stet bootstrap secret handle).
pub const BOOTSTRAP_MAGIC: &[u8; 4] = b"STET";

/// Version of the bootstrap handoff framing.
pub const BOOTSTRAP_VERSION: u8 = 1;

/// A validated bootstrap payload: the secret plus its framing metadata.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct BootstrapPayload {
    pub secret: [u8; LAUNCH_SECRET_LEN],
    pub client_pid: u32,
}

/// Encode the bootstrap payload into handoff bytes (magic + version + len + secret + pid).
pub fn encode_bootstrap(secret: &[u8; LAUNCH_SECRET_LEN], client_pid: u32) -> Vec<u8> {
    let mut out = Vec::with_capacity(4 + 1 + 4 + LAUNCH_SECRET_LEN + 4);
    out.extend_from_slice(BOOTSTRAP_MAGIC);
    out.push(BOOTSTRAP_VERSION);
    out.extend_from_slice(&(LAUNCH_SECRET_LEN as u32).to_le_bytes());
    out.extend_from_slice(secret);
    out.extend_from_slice(&client_pid.to_le_bytes());
    out
}

/// Decode and validate a bootstrap payload. Returns Err on malformed framing.
pub fn decode_bootstrap(bytes: &[u8]) -> Result<BootstrapPayload, &'static str> {
    if bytes.len() != 4 + 1 + 4 + LAUNCH_SECRET_LEN + 4 {
        return Err("bootstrap_len_mismatch");
    }
    if &bytes[0..4] != BOOTSTRAP_MAGIC {
        return Err("bootstrap_magic_mismatch");
    }
    if bytes[4] != BOOTSTRAP_VERSION {
        return Err("bootstrap_version_mismatch");
    }
    let len = u32::from_le_bytes(bytes[5..9].try_into().unwrap());
    if len as usize != LAUNCH_SECRET_LEN {
        return Err("bootstrap_secret_len_mismatch");
    }
    let mut secret = [0u8; LAUNCH_SECRET_LEN];
    secret.copy_from_slice(&bytes[9..9 + LAUNCH_SECRET_LEN]);
    let client_pid = u32::from_le_bytes(bytes[9 + LAUNCH_SECRET_LEN..].try_into().unwrap());
    Ok(BootstrapPayload { secret, client_pid })
}

/// The secret must never be exposed via an environment variable. Used by a startup check.
pub fn secret_not_in_env(env: &[(String, String)]) -> bool {
    !env.iter().any(|(k, v)| k.to_lowercase().contains("stet") && !v.is_empty() && v.len() >= LAUNCH_SECRET_LEN)
}

#[cfg(test)]
mod tests {
    use super::*;
    fn secret() -> [u8; LAUNCH_SECRET_LEN] { [0x42; LAUNCH_SECRET_LEN] }
    #[test]
    fn bootstrap_roundtrips_secret_and_pid() {
        let bytes = encode_bootstrap(&secret(), 14208);
        let p = decode_bootstrap(&bytes).unwrap();
        assert_eq!(p.secret, secret());
        assert_eq!(p.client_pid, 14208);
    }
    #[test]
    fn wrong_magic_is_rejected() {
        let mut bytes = encode_bootstrap(&secret(), 1);
        bytes[0] = 0x00;
        assert_eq!(decode_bootstrap(&bytes), Err("bootstrap_magic_mismatch"));
    }
    #[test]
    fn wrong_version_is_rejected() {
        let mut bytes = encode_bootstrap(&secret(), 1);
        bytes[4] = 99;
        assert_eq!(decode_bootstrap(&bytes), Err("bootstrap_version_mismatch"));
    }
    #[test]
    fn truncated_bootstrap_is_rejected() {
        assert_eq!(decode_bootstrap(&[0u8; 10]), Err("bootstrap_len_mismatch"));
    }
    #[test]
    fn secret_not_carried_in_env() {
        let env = vec![("PATH".to_string(), "C:\\bin".to_string())];
        assert!(secret_not_in_env(&env));
        let bad = vec![("STET_SECRET".to_string(), "0123456789abcdef0123456789abcdef".to_string())];
        assert!(!secret_not_in_env(&bad));
    }
}
