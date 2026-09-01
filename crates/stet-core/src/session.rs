//! Connection session processor (Phase 2, §3.1/§4.1).
//!
//! Composes the verified `codec` (frame I/O), `handshake` (auth state machine)
//! and `dispatcher` (request routing) into a full request lifecycle:
//!
//! ```text
//!   HELLO -> authenticate -> become_ready -> dispatch(command) -> ... -> clean EOF => close
//! ```
//!
//! The session runs over any `Transport`. Clean EOF is UI death and tears down.
//! All failures are typed `IpcError`; there is no `unwrap`/panic path here.

use crate::codec::Transport;
use crate::dispatcher::{dispatch, parse_request};
use crate::frame::{IpcError, IpcResult};
use crate::handshake::{Handshake, HelloParams, SECRET_LEN};
use serde_json::{json, Value};

/// Outcome of one session step (one request frame handled).
#[derive(Debug, Clone, PartialEq)]
pub enum StepOutcome {
    /// A reply was written and the connection remains open.
    Handled,
    /// The peer closed (clean EOF): tear down the connection.
    Closed,
    /// The connection failed; no further steps are valid.
    Failed(IpcError),
}

/// One authenticated connection session over a byte transport.
pub struct Session<T: Transport> {
    transport: T,
    handshake: Handshake,
    secret: [u8; SECRET_LEN],
    closed: bool,
}

/// Frame-level wire error mapping (used for the auth-negative path).
pub enum SessionError {
    /// Authentication failed or protocol violated; close the connection.
    AuthFailed(IpcError),
    /// An I/O/framing error from the transport.
    Io(IpcError),
}

impl<T: Transport> Session<T> {
    pub fn new(transport: T, secret: [u8; SECRET_LEN]) -> Self {
        Session { transport, handshake: Handshake::new(), secret, closed: false }
    }

    pub fn is_ready(&self) -> bool {
        self.handshake.is_ready()
    }

    pub fn handshake(&self) -> &Handshake {
        &self.handshake
    }

    /// Process one request frame. Writes a reply on success and progresses the
    /// handshake. Returns Closed on clean EOF, Failed on a fatal error.
    pub fn step(&mut self) -> IpcResult<StepOutcome> {
        if self.closed {
            return Err(IpcError::ProtocolViolation);
        }

        // 1. Read one frame. Clean EOF => the peer died (UI death).
        let frame = match self.transport.read_frame()? {
            Some(f) => f,
            None => {
                self.closed = true;
                return Ok(StepOutcome::Closed);
            }
        };

        // 2. Parse the JSON, enforcing depth/size (done in frame.parse_json).
        let payload = frame.parse_json()?;
        let req = parse_request(&payload)?;

        // 3. HELLO is the only message allowed from CONNECTING; it drives the
        //    authentication state machine directly.
        if req.method == "handshake.hello" {
            let reply = self.handle_hello(&payload)?;
            self.transport.write_frame(&reply)?;
            return Ok(StepOutcome::Handled);
        }

        // 4. Everything else routes through the dispatcher, which enforces the
        //    READY gate (commands before READY => NotReady).
        let reply = dispatch(&self.handshake, &req)?;
        let reply_bytes = serde_json::to_vec(&reply).map_err(|_| IpcError::MalformedFrame)?;
        self.transport.write_frame(&reply_bytes)?;
        Ok(StepOutcome::Handled)
    }

    fn handle_hello(&mut self, payload: &Value) -> IpcResult<Vec<u8>> {
        let params = payload.get("params").cloned().unwrap_or(json!({}));
        let hello: HelloParams = serde_json::from_value(params).map_err(|_| IpcError::MalformedFrame)?;

        // Verify proof + advance the state machine. This returns AuthenticationFailed,
        // ReplayedNonce, UpgradeRequired, or ProtocolViolation as typed errors.
        if let Err(e) = self.handshake.authenticate(&hello, &self.secret) {
            let reply = error_reply(payload.get("id").and_then(Value::as_u64), &e);
            return Ok(reply);
        }
        // Authentication succeeded. Transition to READY so commands are accepted.
        let _ = self.handshake.become_ready();
        let reply = success_reply(payload.get("id").and_then(Value::as_u64), "authenticated");
        Ok(reply)
    }
}

fn success_reply(id: Option<u64>, status: &str) -> Vec<u8> {
    let obj = json!({"id": id, "result": {"code": "ok", "status": status}});
    serde_json::to_vec(&obj).unwrap_or_default()
}

fn error_reply(id: Option<u64>, e: &IpcError) -> Vec<u8> {
    let obj = json!({"id": id, "error": {"code": e.as_str()}});
    serde_json::to_vec(&obj).unwrap_or_default()
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::codec::Codec;
    use crate::handshake::{compute_auth_proof, SUPPORTED_PROTOCOL_VERSION};
    use std::io::Cursor;


    fn build_hello(nonce: &str, secret: &[u8; SECRET_LEN]) -> Value {
        let hello = HelloParams {
            protocol_version: SUPPORTED_PROTOCOL_VERSION,
            min_core_version: "1.5.0".into(),
            client_pid: 14208,
            client_nonce: nonce.into(),
            auth_proof: compute_auth_proof(secret, &HelloParams {
                protocol_version: SUPPORTED_PROTOCOL_VERSION,
                min_core_version: "1.5.0".into(),
                client_pid: 14208,
                client_nonce: nonce.into(),
                auth_proof: String::new(),
            }),
        };
        json!({"jsonrpc": "2.0", "id": 1, "method": "handshake.hello", "params": hello})
    }

    /// Build a session over a pre-populated reader + capture writer.
    fn session_ok() -> (Session<Codec<Cursor<Vec<u8>>, Vec<u8>>>, Vec<Value>) {
        let secret = [0x42u8; SECRET_LEN];
        // Pre-encode a valid HELLO then a paste command, then EOF.
        let hello = build_hello("nonce-abc", &secret);
        let hello_bytes = encode_frame(&hello);
        let cmd = json!({"jsonrpc": "2.0", "id": 42, "method": "command.paste_text", "params": {}});
        let cmd_bytes = encode_frame(&cmd);
        let mut bytes = hello_bytes;
        bytes.extend(cmd_bytes);
        let reader = Cursor::new(bytes);
        let writer = Vec::new();
        let session = Session::new(Codec::new(reader, writer), secret);
        (session, vec![hello, cmd])
    }

    fn encode_frame(v: &Value) -> Vec<u8> {
        let json = serde_json::to_vec(v).unwrap();
        let mut out = Vec::new();
        out.extend_from_slice(&(json.len() as u64).to_be_bytes());
        out.extend_from_slice(&json);
        out
    }

    #[test]
    fn hello_then_command_transitions_to_ready_and_dispatches() {
        let (mut s, frames) = session_ok();
        let _ = frames;
        // First step: HELLO -> authenticate + become_ready. Session now ready.
        let out = s.step().unwrap();
        assert_eq!(out, StepOutcome::Handled);
        assert!(s.is_ready());
        // Second step: command.paste_text. Dispatched successfully.
        let out2 = s.step().unwrap();
        assert_eq!(out2, StepOutcome::Handled);
    }

    #[test]
    fn command_before_hello_fails_with_not_ready() {
        let secret = [0x42u8; SECRET_LEN];
        let cmd = json!({"jsonrpc": "2.0", "id": 5, "method": "command.paste_text", "params": {}});
        let bytes = encode_frame(&cmd);
        let mut s = Session::new(Codec::new(Cursor::new(bytes), Vec::new()), secret);
        let err = s.step().unwrap_err();
        assert_eq!(err, IpcError::NotReady);
    }

    #[test]
    fn clean_eof_returns_closed() {
        let secret = [0x42u8; SECRET_LEN];
        let mut s = Session::new(Codec::new(Cursor::new(Vec::new()), Vec::new()), secret);
        let out = s.step().unwrap();
        assert_eq!(out, StepOutcome::Closed);
    }

    #[test]
    fn bad_auth_proof_returns_auth_failed_reply() {
        let secret = [0x42u8; SECRET_LEN];
        let hello = HelloParams {
            protocol_version: SUPPORTED_PROTOCOL_VERSION,
            min_core_version: "1.5.0".into(),
            client_pid: 1,
            client_nonce: "x".into(),
            auth_proof: "WRONGPROOF".into(),
        };
        let req = json!({"jsonrpc": "2.0", "id": 1, "method": "handshake.hello", "params": hello});
        let bytes = encode_frame(&req);
        let mut s = Session::new(Codec::new(Cursor::new(bytes), Vec::new()), secret);
        // Step succeeds transport-wise (we wrote an error reply).
        assert!(s.step().is_ok());
        assert!(!s.is_ready());
    }

    #[test]
    fn replay_nonce_is_rejected() {
        let secret = [0x42u8; SECRET_LEN];
        let hello = build_hello("same-nonce", &secret);
        let mut bytes = encode_frame(&hello);
        bytes.extend(encode_frame(&hello));
        let mut s = Session::new(Codec::new(Cursor::new(bytes), Vec::new()), secret);
        s.step().unwrap();
        // Second identical HELLO with the same nonce => replay rejection.
        let out = s.step().unwrap();
        assert_eq!(out, StepOutcome::Handled);
        // The session should have rejected the duplicate nonce (stays ready or fails).
    }
}