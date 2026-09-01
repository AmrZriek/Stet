//! Frame log redaction policy (Phase 2, §3.2).
//!
//! Debug frame dumps must redact `selected_text` / `params.text` by default, showing only
//! the length and a SHA-256 prefix. Full frames require an explicit `--dump-ipc-frames` flag.
//! Shadow-mode comparison retains only lengths, hashes, and outcome metadata. Nothing is ever
//! uploaded (Stet is strictly air-gapped). Pure + testable.

use serde_json::{json, Value};
use sha2::{Digest, Sha256};

/// Maximum plaintext length to report in a redacted frame (a full SHA-256 hex is 64 chars
/// plus a small length prefix; we keep the redacted value bounded).
pub const MAX_REDACTED_FRAGMENT: usize = 64;

/// Whether full frame dumps are enabled (the `--dump-ipc-frames` developer flag).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum RedactionMode {
    /// Redact selected_text / params.text to length + SHA-256 prefix (default).
    Redacted,
    /// Full frames allowed (explicit developer flag, with a startup warning).
    FullDump,
}

/// Return a shortened SHA-256 prefix of `s` (hex), truncated to `max_chars`.
pub fn sha256_prefix(s: &str, max_chars: usize) -> String {
    let mut hasher = Sha256::new();
    hasher.update(s.as_bytes());
    let digest = hasher.finalize();
    let hex: String = digest.iter().map(|b| format!("{:02x}", b)).collect();
    hex.chars().take(max_chars).collect()
}

/// Redact a frame payload per the configured mode. Replaces the values of the top-level
/// `selected_text` and `params.text` keys with `{len, sha256_prefix}` summaries.
pub fn redact_frame(payload: &Value, mode: RedactionMode) -> Value {
    if mode == RedactionMode::FullDump {
        return payload.clone();
    }
    let mut out = payload.clone();
    if let Some(obj) = out.as_object_mut() {
        if let Some(sel) = obj.get("selected_text").and_then(Value::as_str) {
            obj.insert("selected_text".to_string(), redact_text(sel));
        }
        if let Some(params) = obj.get_mut("params").and_then(Value::as_object_mut) {
            if let Some(text) = params.get("text").and_then(Value::as_str) {
                params.insert("text".to_string(), redact_text(text));
            }
        }
    }
    out
}

/// Build the redaction summary value: {len, sha256_prefix}.
pub fn redact_text(s: &str) -> Value {
    json!({
        "len": s.len(),
        "sha256_prefix": sha256_prefix(s, 16),
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn redacted_frame_replaces_selected_text_with_summary() {
        let payload = json!({"selected_text": "hello world", "id": 1});
        let r = redact_frame(&payload, RedactionMode::Redacted);
        assert!(r["selected_text"]["len"].as_u64().unwrap() > 0);
        assert!(r["selected_text"]["sha256_prefix"].as_str().unwrap().len() > 0);
        assert_eq!(r["id"], 1);
    }
    #[test]
    fn redacted_frame_replaces_params_text() {
        let payload = json!({"params": {"text": "secret"}, "method": "command.paste_text"});
        let r = redact_frame(&payload, RedactionMode::Redacted);
        assert!(r["params"]["text"]["len"].as_u64().unwrap() > 0);
    }
    #[test]
    fn full_dump_mode_preserves_text() {
        let payload = json!({"selected_text": "hello world"});
        let r = redact_frame(&payload, RedactionMode::FullDump);
        assert_eq!(r["selected_text"], "hello world");
    }
    #[test]
    fn sha256_prefix_is_deterministic_and_short() {
        let a = sha256_prefix("hello", MAX_REDACTED_FRAGMENT);
        let b = sha256_prefix("hello", MAX_REDACTED_FRAGMENT);
        assert_eq!(a, b);
        assert!(a.len() <= MAX_REDACTED_FRAGMENT);
        assert!(!a.contains("hello"));
    }
    #[test]
    fn redaction_never_contains_plaintext() {
        let payload = json!({"selected_text": "TOP-SECRET-CONTENT"});
        let r = redact_frame(&payload, RedactionMode::Redacted);
        let serialized = serde_json::to_string(&r).unwrap();
        assert!(!serialized.contains("TOP-SECRET-CONTENT"));
    }
}
