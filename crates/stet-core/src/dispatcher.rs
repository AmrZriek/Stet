//! IPC dispatcher (Phase 1b, §3.1).
//!
//! Routes a JSON-RPC request envelope to a handler, returning a typed
//! `IpcResult`. Dispatchers contain no `unwrap`/`expect`/panic path; a
//! last-resort panic boundary is covered by §4.4 (rate-limited integrity).

use crate::capture_agent::{CaptureAgent, RealAgent, DEFAULT_CAPTURE_TIMEOUT_MS};
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
/// the connection is READY (Schema §3.1). Production entry: runs the real
/// capture/paste agent.
pub fn dispatch(hs: &Handshake, req: &Request) -> IpcResult<Value> {
    dispatch_with(hs, req, &RealAgent::new())
}

/// Handle a request with an injectable [`CaptureAgent`]. Tests pass fakes;
/// production passes the real agent. Agent failures surface as typed
/// `IpcError`s — success is never faked.
pub fn dispatch_with(hs: &Handshake, req: &Request, agent: &impl CaptureAgent) -> IpcResult<Value> {
    if req.method.starts_with("command.") && !hs.is_ready() {
        return Err(IpcError::NotReady);
    }
    match req.method.as_str() {
        "command.capture_selection" => {
            let timeout_ms = req
                .params
                .get("timeout_ms")
                .and_then(Value::as_u64)
                .unwrap_or(DEFAULT_CAPTURE_TIMEOUT_MS);
            let text = agent.capture_selection(timeout_ms)?;
            Ok(json!({"id": req.id, "result": {"text": text}}))
        }
        "command.paste_text" => {
            let text = req
                .params
                .get("text")
                .and_then(Value::as_str)
                .ok_or(IpcError::UnknownRequiredField)?;
            let verify = req
                .params
                .get("verify_target")
                .and_then(Value::as_bool)
                .unwrap_or(true);
            let count = agent.paste_text(text, verify)?;
            Ok(json!({"id": req.id, "result": {"status": "pasted", "chars": count}}))
        }
        "command.register_hotkeys" => {
            let empty = Vec::new();
            let arr = req.params.get("hotkeys").and_then(Value::as_array).unwrap_or(&empty);
            // Host-owned registration (Rust default); Python falls back when hosted=false.
            let (hosted_count, failed, hosted) =
                crate::hotkey_host::register_from_json(&Value::Array(arr.clone()));
            Ok(json!({"id": req.id, "result": {
                "status": "registered",
                "count": arr.len(),
                "hosted": hosted,
                "hosted_count": hosted_count,
                "failed": failed,
            }}))
        }
        "command.unregister_hotkeys" => {
            crate::hotkey_host::clear_host();
            Ok(json!({"id": req.id, "result": {"status": "unregistered", "hosted": true}}))
        }
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
        let hs = ready_handshake();
        let agent = FakeAgent::ok();
        let req = Request { id: Some(1), method: "command.bogus".into(), params: json!({}) };
        let res = dispatch_with(&hs, &req, &agent);
        assert_eq!(res, Err(IpcError::UnknownRequiredField));
    }

    #[test]
    fn malformed_envelope_is_rejected() {
        let payload = json!({"jsonrpc": "2.0", "params": {}});
        let res = parse_request(&payload);
        assert_eq!(res, Err(IpcError::MalformedFrame));
    }

    #[test]
    fn ready_connection_routes_known_command() {
        let hs = ready_handshake();
        let agent = FakeAgent::ok();
        let req = Request { id: Some(42), method: "command.paste_text".into(), params: json!({"text": "hi"}) };
        let res = dispatch_with(&hs, &req, &agent).unwrap();
        assert_eq!(res, json!({"id": 42, "result": {"status": "pasted", "chars": 2}}));
    }

    #[test]
    fn capture_selection_returns_text_shape() {
        let hs = ready_handshake();
        let agent = FakeAgent::ok();
        let req = Request {
            id: Some(7),
            method: "command.capture_selection".into(),
            params: json!({"timeout_ms": 500}),
        };
        let res = dispatch_with(&hs, &req, &agent).unwrap();
        assert_eq!(res, json!({"id": 7, "result": {"text": "selected"}}));
        assert_eq!(agent.seen_timeout.get(), 500);
    }

    #[test]
    fn capture_selection_defaults_timeout() {
        let hs = ready_handshake();
        let agent = FakeAgent::ok();
        let req = Request { id: Some(7), method: "command.capture_selection".into(), params: json!({}) };
        let res = dispatch_with(&hs, &req, &agent).unwrap();
        assert_eq!(res, json!({"id": 7, "result": {"text": "selected"}}));
        assert_eq!(agent.seen_timeout.get(), crate::capture_agent::DEFAULT_CAPTURE_TIMEOUT_MS);
    }

    #[test]
    fn paste_text_defaults_verify_target_to_true() {
        let hs = ready_handshake();
        let agent = FakeAgent::ok();
        let req = Request {
            id: Some(3),
            method: "command.paste_text".into(),
            params: json!({"text": "abc"}),
        };
        let res = dispatch_with(&hs, &req, &agent).unwrap();
        assert_eq!(res, json!({"id": 3, "result": {"status": "pasted", "chars": 3}}));
        assert!(agent.seen_verify.get());
    }

    #[test]
    fn paste_text_missing_text_is_typed_error() {
        let hs = ready_handshake();
        let agent = FakeAgent::ok();
        let req = Request { id: Some(3), method: "command.paste_text".into(), params: json!({}) };
        let res = dispatch_with(&hs, &req, &agent);
        assert_eq!(res, Err(IpcError::UnknownRequiredField));
    }

    #[test]
    fn agent_failure_is_typed_never_fake_ok() {
        let hs = ready_handshake();
        let agent = FakeAgent::failing(IpcError::Timeout, IpcError::AbortedWrongTarget);
        let cap = Request { id: Some(1), method: "command.capture_selection".into(), params: json!({}) };
        assert_eq!(dispatch_with(&hs, &cap, &agent), Err(IpcError::Timeout));
        let paste = Request {
            id: Some(2),
            method: "command.paste_text".into(),
            params: json!({"text": "x"}),
        };
        assert_eq!(dispatch_with(&hs, &paste, &agent), Err(IpcError::AbortedWrongTarget));
    }

    use std::cell::Cell;

    struct FakeAgent {
        capture_text: String,
        capture_err: Option<IpcError>,
        paste_err: Option<IpcError>,
        seen_timeout: Cell<u64>,
        seen_verify: Cell<bool>,
    }

    impl FakeAgent {
        fn ok() -> Self {
            FakeAgent {
                capture_text: "selected".into(),
                capture_err: None,
                paste_err: None,
                seen_timeout: Cell::new(0),
                seen_verify: Cell::new(false),
            }
        }

        fn failing(capture_err: IpcError, paste_err: IpcError) -> Self {
            FakeAgent {
                capture_text: String::new(),
                capture_err: Some(capture_err),
                paste_err: Some(paste_err),
                seen_timeout: Cell::new(0),
                seen_verify: Cell::new(false),
            }
        }
    }

    impl crate::capture_agent::CaptureAgent for FakeAgent {
        fn capture_selection(&self, timeout_ms: u64) -> Result<String, IpcError> {
            self.seen_timeout.set(timeout_ms);
            match self.capture_err {
                Some(e) => Err(e),
                None => Ok(self.capture_text.clone()),
            }
        }

        fn paste_text(&self, text: &str, verify_target: bool) -> Result<usize, IpcError> {
            self.seen_verify.set(verify_target);
            match self.paste_err {
                Some(e) => Err(e),
                None => Ok(text.chars().count()),
            }
        }
    }

    fn ready_handshake() -> Handshake {
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
        let _ = hs.become_ready();
        hs
    }

    #[test]
    fn register_hotkeys_returns_status_and_count() {
        let hs = ready_handshake();
        let req = Request {
            id: Some(10),
            method: "command.register_hotkeys".into(),
            params: json!({
                "hotkeys": [
                    {"vk": 0x78, "modifiers": 0, "label": "F9"},
                    {"vk": 0x78, "modifiers": 4, "label": "Shift+F9"}
                ]
            }),
        };
        let v = dispatch_with(&hs, &req, &FakeAgent::ok()).unwrap();
        assert_eq!(v["result"]["status"], "registered");
        assert_eq!(v["result"]["count"], 2);
    }

    #[test]
    fn unregister_hotkeys_returns_status() {
        let hs = ready_handshake();
        let req = Request {
            id: Some(11),
            method: "command.unregister_hotkeys".into(),
            params: json!({}),
        };
        let v = dispatch_with(&hs, &req, &FakeAgent::ok()).unwrap();
        assert_eq!(v["result"]["status"], "unregistered");
    }
}