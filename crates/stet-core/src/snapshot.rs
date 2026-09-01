//! Rich-format clipboard snapshot model (Phase 6b, §2d).
//!
//! The OLE IDataObject snapshot preserves all formats. This models the snapshot as a
//! format -> bytes map with a serialization contract, so the restore logic and the
//! format-copy semantics are testable without the COM calls. The Win32 IDataObject
//! instantiation is the FFI layer (Phase 2h); the contract here is the testable core.

use serde::{Deserialize, Serialize};

/// A clipboard format identifier (canonical or registered).
#[derive(Debug, Clone, PartialEq, Eq, Hash, Serialize, Deserialize)]
pub enum ClipFormat {
    UnicodeText,
    FileList,
    Html,
    Image,
    Registered(String),
}

/// One format payload in the snapshot.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct FormatPayload {
    pub format: ClipFormat,
    pub bytes: Vec<u8>,
}

/// A full clipboard snapshot captured from OLE IDataObject.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, Default)]
pub struct Snapshot {
    pub formats: Vec<FormatPayload>,
    pub sequence: u64,
}

impl Snapshot {
    pub fn new(sequence: u64) -> Self {
        Snapshot { formats: Vec::new(), sequence }
    }

    pub fn add(&mut self, format: ClipFormat, bytes: Vec<u8>) {
        self.formats.push(FormatPayload { format, bytes });
    }

    pub fn get_text(&self) -> Option<&[u8]> {
        self.formats.iter().find(|f| f.format == ClipFormat::UnicodeText).map(|f| f.bytes.as_slice())
    }

    pub fn preserves_all_formats(&self, original: &Snapshot) -> bool {
        original.formats.iter().all(|of| {
            self.formats.iter().any(|sf| sf.format == of.format)
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn snapshot_roundtrips_json() {
        let mut s = Snapshot::new(42);
        s.add(ClipFormat::UnicodeText, b"hello".to_vec());
        s.add(ClipFormat::Html, b"<b>hi</b>".to_vec());
        let j = serde_json::to_string(&s).unwrap();
        let back: Snapshot = serde_json::from_str(&j).unwrap();
        assert_eq!(back.sequence, 42);
        assert_eq!(back.formats.len(), 2);
    }

    #[test]
    fn unicode_text_retrieval() {
        let mut s = Snapshot::new(7);
        s.add(ClipFormat::Html, b"<b>hi</b>".to_vec());
        assert!(s.get_text().is_none());
        s.add(ClipFormat::UnicodeText, "hello".as_bytes().to_vec());
        assert_eq!(s.get_text(), Some(b"hello".as_slice()));
    }

    #[test]
    fn rich_format_preservation_detects_loss() {
        let mut orig = Snapshot::new(1);
        orig.add(ClipFormat::UnicodeText, b"t".to_vec());
        orig.add(ClipFormat::Html, b"<b>t</b>".to_vec());
        let mut reduced = Snapshot::new(1);
        reduced.add(ClipFormat::UnicodeText, b"t".to_vec());
        assert!(!reduced.preserves_all_formats(&orig));
    }

    #[test]
    fn rich_format_preservation_passes_when_all_present() {
        let mut orig = Snapshot::new(1);
        orig.add(ClipFormat::UnicodeText, b"t".to_vec());
        orig.add(ClipFormat::FileList, b"c:\\a.txt".to_vec());
        let mut restored = Snapshot::new(2);
        restored.add(ClipFormat::UnicodeText, b"t".to_vec());
        restored.add(ClipFormat::FileList, b"c:\\a.txt".to_vec());
        assert!(restored.preserves_all_formats(&orig));
    }
}