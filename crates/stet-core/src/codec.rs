//! Frame transport (Phase 1b, §3.1).
//!
//! A `codec::Transport` reads/writes length-prefixed JSON frames over a
//! byte stream (`Read`/`Write`). Enforces MAX_FRAME_BYTES before allocation.
//! A clean stream EOF (peer closed) is `Ok(None)` — the supervisor treats it
//! as UI death and tears down (§4.1). A truncated frame or invalid prefix is a
//! typed error. Windows named-pipe and macOS UDS impls are Phase 2c/Phase 4.

use crate::frame::{Frame, IpcError, IpcResult, MAX_FRAME_BYTES, MAX_LENGTH_PREFIX};
use std::io::{Read, Write};

/// A transport that reads optional frames (None == clean EOF) and writes frames.
pub trait Transport {
    fn read_frame(&mut self) -> IpcResult<Option<Frame>>;
    fn write_frame(&mut self, payload: &[u8]) -> IpcResult<()>;
}

/// Stream-oriented frame codec over `Read + Write`.
pub struct Codec<R, W> {
    reader: R,
    writer: W,
}

impl<R, W> Codec<R, W> {
    pub fn new(reader: R, writer: W) -> Self {
        Codec { reader, writer }
    }
}

impl<R: Read, W: Write> Transport for Codec<R, W> {
    fn read_frame(&mut self) -> IpcResult<Option<Frame>> {
        // Read the 8-byte length prefix fully (loop over partial reads).
        let mut len_bytes = [0u8; MAX_LENGTH_PREFIX];
        // Clean EOF before any byte => Ok(None).
        match read_exact_returning_eof(&mut self.reader, &mut len_bytes)? {
            ReadOutcome::Eof => return Ok(None),
            ReadOutcome::Ok => {}
        }
        let len = u64::from_be_bytes(len_bytes) as usize;
        if len > MAX_FRAME_BYTES {
            return Err(IpcError::FrameTooLarge);
        }
        let mut payload = vec![0u8; len];
        match read_exact_returning_eof(&mut self.reader, &mut payload)? {
            ReadOutcome::Eof => return Err(IpcError::MalformedFrame), // truncated frame
            ReadOutcome::Ok => {}
        }
        Ok(Some(Frame { payload }))
    }

    fn write_frame(&mut self, payload: &[u8]) -> IpcResult<()> {
        if payload.len() > MAX_FRAME_BYTES {
            return Err(IpcError::FrameTooLarge);
        }
        let mut out = Vec::with_capacity(MAX_LENGTH_PREFIX + payload.len());
        out.extend_from_slice(&(payload.len() as u64).to_be_bytes());
        out.extend_from_slice(payload);
        self.writer.write_all(&out).map_err(|_| IpcError::MalformedFrame)?;
        self.writer.flush().map_err(|_| IpcError::MalformedFrame)?;
        Ok(())
    }
}

enum ReadOutcome {
    Ok,
    Eof,
}

/// read_exact that distinguishes clean EOF at a frame boundary from a
/// truncated frame. Returns Ok/Eof; underlying I/O errors map to MalformedFrame.
fn read_exact_returning_eof(r: &mut impl Read, buf: &mut [u8]) -> IpcResult<ReadOutcome> {
    let mut offset = 0;
    while offset < buf.len() {
        let n = r.read(&mut buf[offset..]).map_err(|_| IpcError::MalformedFrame)?;
        if n == 0 {
            // Clean EOF before any byte read => peer closed at a boundary.
            if offset == 0 {
                return Ok(ReadOutcome::Eof);
            }
            return Err(IpcError::MalformedFrame); // truncated mid-frame
        }
        offset += n;
    }
    Ok(ReadOutcome::Ok)
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::Cursor;

    fn frame_bytes(v: &serde_json::Value) -> Vec<u8> {
        let json = serde_json::to_vec(v).unwrap();
        let mut out = Vec::new();
        out.extend_from_slice(&(json.len() as u64).to_be_bytes());
        out.extend_from_slice(&json);
        out
    }

    #[test]
    fn codec_roundtrips_a_frame() {
        let bytes = frame_bytes(&serde_json::json!({"method": "command.paste_text", "id": 7}));
        let reader = Cursor::new(bytes);
        let writer = Vec::new();
        let mut codec = Codec::new(reader, writer);
        let frame = codec.read_frame().unwrap().unwrap();
        let parsed = frame.parse_json().unwrap();
        assert_eq!(parsed["id"], 7);
    }

    #[test]
    fn codec_writes_length_prefixed_frame() {
        let reader = Cursor::new(Vec::new());
        let writer = Vec::new();
        let mut codec = Codec::new(reader, writer);
        let payload = b"{\"a\":1}";
        codec.write_frame(payload).unwrap();
        let written = codec.writer;
        let len = u64::from_be_bytes(written[0..8].try_into().unwrap());
        assert_eq!(len, payload.len() as u64);
        assert_eq!(&written[8..], payload);
    }

    #[test]
    fn codec_rejects_length_prefix_over_cap() {
        let bytes = {
            let mut b = Vec::new();
            b.extend_from_slice(&((MAX_FRAME_BYTES + 1) as u64).to_be_bytes());
            b.extend_from_slice(&[0u8; 8]);
            b
        };
        let reader = Cursor::new(bytes);
        let mut codec = Codec::new(reader, Vec::new());
        assert_eq!(codec.read_frame(), Err(IpcError::FrameTooLarge));
    }

    #[test]
    fn codec_reads_multiple_frames() {
        let mut bytes = frame_bytes(&serde_json::json!({"id": 1, "method": "a"}));
        bytes.extend(frame_bytes(&serde_json::json!({"id": 2, "method": "b"})));
        let reader = Cursor::new(bytes);
        let mut codec = Codec::new(reader, Vec::new());
        let f1 = codec.read_frame().unwrap().unwrap();
        let f2 = codec.read_frame().unwrap().unwrap();
        assert_eq!(f1.parse_json().unwrap()["id"], 1);
        assert_eq!(f2.parse_json().unwrap()["id"], 2);
    }

    #[test]
    fn codec_clean_eof_returns_none() {
        let reader = Cursor::new(Vec::new());
        let mut codec = Codec::new(reader, Vec::new());
        assert_eq!(codec.read_frame(), Ok(None));
    }

    #[test]
    fn codec_truncated_frame_is_malformed() {
        // Prefix says 100 bytes but only 5 present.
        let mut bytes = Vec::new();
        bytes.extend_from_slice(&100u64.to_be_bytes());
        bytes.extend_from_slice(&[0u8; 5]);
        let reader = Cursor::new(bytes);
        let mut codec = Codec::new(reader, Vec::new());
        assert_eq!(codec.read_frame(), Err(IpcError::MalformedFrame));
    }
}
