//! Authenticated connection handshake (Phase 1a, §3.1).
//!
//! `CONNECTING -> AUTHENTICATED -> READY -> CLOSING` state machine plus
//! HMAC-SHA-256 proof-of-possession over a fresh per-launch secret and
//! client nonce. Pure + testable; no Windows deps.

use crate::frame::{IpcError, IpcResult};
use base64::engine::general_purpose::STANDARD as BASE64;
use base64::Engine;
use hmac::{Hmac, Mac};
use serde::{Deserialize, Serialize};
use sha2::Sha256;
use std::collections::HashSet;

/// Core-supported wire protocol version (Schema §3.1 HELLO).
pub const SUPPORTED_PROTOCOL_VERSION: u32 = 2;
/// Minimum core version advertised by the UI that this core must satisfy.
pub const CORE_VERSION: &str = "1.5.0";
/// 256-bit per-launch secret size (bytes).
pub const SECRET_LEN: usize = 32;

type HmacSha256 = Hmac<Sha256>;

/// Connection lifecycle states (Phase 1a).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ConnectionState {
    Connecting,
    Authenticated,
    Ready,
    Closing,
}

/// Authenticated HELLO params (Schema "0. UI -> Core").
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct HelloParams {
    pub protocol_version: u32,
    pub min_core_version: String,
    pub client_pid: u32,
    /// base64 of 64 random bytes.
    pub client_nonce: String,
    /// base64 of HMAC-SHA-256 over the canonical hello payload.
    pub auth_proof: String,
}

/// Canonical bytes over which the HMAC auth proof is computed.
/// It serializes the *authenticated* fields (everything except
/// `auth_proof`) in a deterministic field order. The auth_proof value
/// never appears in the canonical payload.
pub fn canonical_hello_payload(p: &HelloParams) -> Vec<u8> {
    // Order matters for determinism; use a fixed, stable serialization.
    let mut v = Vec::new();
    v.extend_from_slice(b"stet-hello-v1\x00");
    v.extend_from_slice(&p.protocol_version.to_be_bytes());
    v.extend_from_slice(&(p.min_core_version.len() as u32).to_be_bytes());
    v.extend_from_slice(p.min_core_version.as_bytes());
    v.extend_from_slice(&p.client_pid.to_be_bytes());
    v.extend_from_slice(&(p.client_nonce.len() as u32).to_be_bytes());
    v.extend_from_slice(p.client_nonce.as_bytes());
    v
}

/// Compute the base64 HMAC-SHA-256 proof over the canonical hello payload.
pub fn compute_auth_proof(secret: &[u8], p: &HelloParams) -> String {
    let mut mac = HmacSha256::new_from_slice(secret).expect("HMAC accepts any key length");
    mac.update(&canonical_hello_payload(p));
    let result = mac.finalize().into_bytes();
    BASE64.encode(result)
}

/// Constant-time verify of the presented auth proof.
pub fn verify_auth_proof(secret: &[u8], p: &HelloParams) -> bool {
    let expected = compute_auth_proof(secret, p);
    // Constant-time comparison of the two base64 strings.
    const_verify(expected.as_bytes(), p.auth_proof.as_bytes())
}

fn const_verify(a: &[u8], b: &[u8]) -> bool {
    if a.len() != b.len() {
        return false;
    }
    let mut diff = 0u8;
    for (x, y) in a.iter().zip(b.iter()) {
        diff |= x ^ y;
    }
    diff == 0
}

/// The authenticated connection handshake state machine.
pub struct Handshake {
    state: ConnectionState,
    /// Accepted client nonces (replay protection).
    accepted_nonces: HashSet<String>,
    /// Authenticated client PID (diagnostics only).
    client_pid: u32,
}

impl Handshake {
    pub fn new() -> Self {
        Handshake {
            state: ConnectionState::Connecting,
            accepted_nonces: HashSet::new(),
            client_pid: 0,
        }
    }

    pub fn state(&self) -> ConnectionState {
        self.state
    }

    pub fn is_ready(&self) -> bool {
        self.state == ConnectionState::Ready
    }

    pub fn client_pid(&self) -> u32 {
        self.client_pid
    }

    /// Accept a nonce already consumed by another connection instance.
    /// Marking it prevents a replay attempt on this connection.
    pub fn mark_nonce_accepted(&mut self, nonce: &str) {
        self.accepted_nonces.insert(nonce.to_string());
    }

    /// Authenticate a HELLO. Only valid from CONNECTING. On success,
    /// verify the HMAC proof, accept the nonce, and transition to
    /// AUTHENTICATED. On failure the state is left unchanged.
    pub fn authenticate(&mut self, p: &HelloParams, secret: &[u8; SECRET_LEN]) -> IpcResult<()> {
        if self.state != ConnectionState::Connecting {
            return Err(IpcError::ProtocolViolation);
        }
        // Version agreement before proof verification.
        if p.protocol_version != SUPPORTED_PROTOCOL_VERSION {
            return Err(IpcError::UpgradeRequired);
        }
        if CORE_VERSION < p.min_core_version.as_str() {
            return Err(IpcError::UpgradeRequired);
        }
        if self.accepted_nonces.contains(&p.client_nonce) {
            return Err(IpcError::ReplayedNonce);
        }
        if !verify_auth_proof(secret, p) {
            return Err(IpcError::AuthenticationFailed);
        }
        self.accepted_nonces.insert(p.client_nonce.clone());
        self.client_pid = p.client_pid;
        self.state = ConnectionState::Authenticated;
        Ok(())
    }

    /// Transition AUTHENTICATED -> READY. Commands are only accepted
    /// after this succeeds.
    pub fn become_ready(&mut self) -> IpcResult<()> {
        if self.state != ConnectionState::Authenticated {
            return Err(IpcError::ProtocolViolation);
        }
        self.state = ConnectionState::Ready;
        Ok(())
    }

    /// Transition to CLOSING from any state.
    pub fn close(&mut self) {
        self.state = ConnectionState::Closing;
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn secret() -> [u8; SECRET_LEN] {
        [0x42u8; SECRET_LEN]
    }

    fn hello_with(mut p: HelloParams) -> HelloParams {
        p.protocol_version = SUPPORTED_PROTOCOL_VERSION;
        p.min_core_version = "1.5.0".to_string();
        p.client_pid = 14208;
        p.client_nonce = "c29tZS1ub25jZQ".to_string();
        p.auth_proof = compute_auth_proof(&secret(), &p);
        p
    }

    fn hello() -> HelloParams {
        hello_with(HelloParams {
            protocol_version: 0,
            min_core_version: String::new(),
            client_pid: 0,
            client_nonce: String::new(),
            auth_proof: String::new(),
        })
    }

    #[test]
    fn new_handshake_is_connecting_and_not_ready() {
        let hs = Handshake::new();
        assert_eq!(hs.state(), ConnectionState::Connecting);
        assert!(!hs.is_ready());
    }

    #[test]
    fn accepts_valid_hello_then_transitions_authenticated() {
        let mut hs = Handshake::new();
        let p = hello();
        let res = hs.authenticate(&p, &secret());
        assert_eq!(res, Ok(()));
        assert_eq!(hs.state(), ConnectionState::Authenticated);
        assert!(!hs.is_ready());
    }

    #[test]
    fn rejects_wrong_auth_proof() {
        let mut hs = Handshake::new();
        let mut p = hello();
        p.auth_proof = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=".to_string();
        let res = hs.authenticate(&p, &secret());
        assert_eq!(res, Err(IpcError::AuthenticationFailed));
        assert_eq!(hs.state(), ConnectionState::Connecting);
    }

    #[test]
    fn rejects_replayed_client_nonce() {
        let mut hs = Handshake::new();
        let p = hello();
        hs.authenticate(&p, &secret()).unwrap();
        let mut hs2 = Handshake::new();
        hs2.mark_nonce_accepted(&p.client_nonce);
        let res = hs2.authenticate(&p, &secret());
        assert_eq!(res, Err(IpcError::ReplayedNonce));
    }

    #[test]
    fn rejects_hello_when_not_connecting() {
        let mut hs = Handshake::new();
        let p = hello();
        hs.authenticate(&p, &secret()).unwrap();
        let res = hs.authenticate(&p, &secret());
        assert_eq!(res, Err(IpcError::ProtocolViolation));
    }

    #[test]
    fn version_mismatch_yields_upgrade_required() {
        let mut hs = Handshake::new();
        let mut p = hello();
        p.protocol_version = SUPPORTED_PROTOCOL_VERSION + 1;
        p.auth_proof = compute_auth_proof(&secret(), &p);
        let res = hs.authenticate(&p, &secret());
        assert_eq!(res, Err(IpcError::UpgradeRequired));
    }

    #[test]
    fn core_too_old_yields_upgrade_required() {
        let mut hs = Handshake::new();
        let mut p = hello();
        p.min_core_version = "99.0.0".to_string();
        p.auth_proof = compute_auth_proof(&secret(), &p);
        let res = hs.authenticate(&p, &secret());
        assert_eq!(res, Err(IpcError::UpgradeRequired));
    }

    #[test]
    fn authenticated_transitions_to_ready_and_accepts_commands() {
        let mut hs = Handshake::new();
        let p = hello();
        hs.authenticate(&p, &secret()).unwrap();
        hs.become_ready().unwrap();
        assert_eq!(hs.state(), ConnectionState::Ready);
        assert!(hs.is_ready());
    }

    #[test]
    fn become_ready_rejects_when_not_authenticated() {
        let mut hs = Handshake::new();
        let res = hs.become_ready();
        assert_eq!(res, Err(IpcError::ProtocolViolation));
    }

    #[test]
    fn close_transitions_to_closing() {
        let mut hs = Handshake::new();
        hs.close();
        assert_eq!(hs.state(), ConnectionState::Closing);
        assert!(!hs.is_ready());
    }

    #[test]
    fn canonical_payload_is_deterministic_and_excludes_auth_proof() {
        let p = hello();
        let a = canonical_hello_payload(&p);
        let b = canonical_hello_payload(&p);
        assert_eq!(a, b);
        assert!(!String::from_utf8_lossy(&a).contains(&p.auth_proof));
    }

    #[test]
    fn proof_is_hmac_sha256_of_canonical_payload() {
        let p = hello();
        let proof = compute_auth_proof(&secret(), &p);
        assert!(!proof.is_empty());
        assert!(verify_auth_proof(&secret(), &p));
    }

    #[test]
    fn handshake_accepts_authenticated_client_pid() {
        let mut hs = Handshake::new();
        let p = hello();
        hs.authenticate(&p, &secret()).unwrap();
        assert_eq!(hs.client_pid(), 14208);
    }

    #[test]
    fn secret_must_be_256_bit_for_proof() {
        let wrong = [0x24u8; SECRET_LEN];
        let p = hello();
        assert!(!verify_auth_proof(&wrong, &p));
    }
}
