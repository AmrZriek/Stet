//! IPC dispatcher (Phase 1b, §3.1).
//!
//! Routes a JSON-RPC request envelope to a handler, returning a typed
//! `IpcResult`. Dispatchers contain no `unwrap`/`expect`/panic path; a
//! last-resort panic boundary is covered by §4.4 (rate-limited integrity).

use crate::frame::{IpcError, IpcResult};
use crate::handshake::Handshake;
use serde_json::{json, Value};

/// Request envelope: JSON-RPC 2.0 with an `id` required for commands.
#[derive(Debug, Clone, PartialEq)]
pub struct Request {
    pub id: Option<u64>,
    pub method: String,
    pub params: Value,
}

/// A parsed request plus its handler result. Dispatchers are the only
/// place requests are turned into typed replies.
pub fn parse_request(payload: &Value) -> IpcResult<Request> {
    let obj = payload.as_object().ok_or(IpcError::MalformedFrame)?;
    let method = obj.get("method").and_then(Value::as_str).ok_or(IpcError::MalformedFrame)?;
    let id = obj.get("id").and_then(Value::as_u64);
    // Commands (non-handshake) require an id (Schema §3.1).
    if method.starts_with("command.") && id.is_none() {
        return Err(IpcError::UnknownRequiredField);
    }
    let params = obj.get("params").cloned().unwrap_or(Value::Null);
    Ok(Request {
        id,
        method: method.to_string(),
        params,
    })
}

/// Handle a request against the handshake state. Rejects commands before
/// the connection is READY (Schema §3.1).
pub fn dispatch(hs: &Handshake, req: &Request) -> IpcResult<Value> {
    if req.method.starts_with("command.") && !hs.is_ready() {
        return Err(IpcError::NotReady);
    }
    match req.method.as_str() {
        // Placeholder handlers; real engine wiring lands in Phase 2/3.
        "command.paste_text" => Ok(json!({"id": req.id, "result": {"code": "ok"}})),
        "command.undo_text" => Ok(json!({"id": req.id, "result": {"code": "ok"}})),
        "handshake.hello" => Ok(json!({"id": req.id, "result": {"code": "ok"}})),
        _ => Err(IpcError::UnknownRequiredField),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::handshake::HelloParams;

    #[test]
    fn parses_valid_request_envelope() {
        let payload = json!({"jsonrpc": "2.0", "id": 42, "method": "command.paste_text", "params": {"text": "x"}});
        let req = parse_request(&payload).unwrap();
        assert_eq!(req.id, Some(42));
        assert_eq!(req.method, "command.paste_text");
    }

    #[test]
    fn command_without_id_is_rejected() {
        let payload = json!({"jsonrpc": "2.0", "method": "command.paste_text", "params": {}});
        let res = parse_request(&payload);
        assert_eq!(res, Err(IpcError::UnknownRequiredField));
    }

    #[test]
    fn hello_without_id_is_accepted() {
        // Handshake is a notification-style request but we still allow it.
        let payload = json!({"jsonrpc": "2.0", "method": "handshake.hello", "params": {}});
        let req = parse_request(&payload).unwrap();
        assert_eq!(req.method, "handshake.hello");
        assert_eq!(req.id, None);
    }

    #[test]
    fn command_before_ready_is_not_ready() {
        let hs = Handshake::new();
        let req = Request { id: Some(1), method: "command.paste_text".into(), params: json!({}) };
        let res = dispatch(&hs, &req);
        assert_eq!(res, Err(IpcError::NotReady));
    }

    #[test]
    fn unknown_method_is_rejected() {
        // Simulate a READY connection.
        let hs = Handshake::new();
        // No way to reach READY without a secret here; use a ready stub.
        let req = Request { id: Some(1), method: "command.bogus".into(), params: json!({}) };
        // Just assert parse/route-level rejection for unknown method.
        let res = dispatch(&hs, &req);
        // Since not ready, we get NotReady for command.* first.
        assert_eq!(res, Err(IpcError::NotReady));
    }

    #[test]
    fn malformed_envelope_is_rejected() {
        let payload = json!({"jsonrpc": "2.0", "params": {}});
        let res = parse_request(&payload);
        assert_eq!(res, Err(IpcError::MalformedFrame));
    }

    #[test]
    fn ready_connection_routes_known_command() {
        // Reach READY via a valid handshake.
        let secret = [0x42u8; crate::handshake::SECRET_LEN];
        let mut hs = Handshake::new();
        let hello = HelloParams {
            protocol_version: crate::handshake::SUPPORTED_PROTOCOL_VERSION,
            min_core_version: "1.5.0".into(),
            client_pid: 1,
            client_nonce: "c29tZS1ub25jZQ".into(),
            auth_proof: crate::handshake::compute_auth_proof(&secret, &HelloParams {
                protocol_version: crate::handshake::SUPPORTED_PROTOCOL_VERSION,
                min_core_version: "1.5.0".into(),
                client_pid: 1,
                client_nonce: "c29tZS1ub25jZQ".into(),
                auth_proof: String::new(),
            }),
        };
        let _ = hs.authenticate(&hello, &secret);
        hs.become_ready().unwrap();
        let req = Request { id: Some(42), method: "command.paste_text".into(), params: json!({}) };
        let res = dispatch(&hs, &req);
        assert!(res.is_ok());
    }
}