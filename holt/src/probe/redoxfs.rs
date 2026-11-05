//! RedoxFS (Redox): its header block, "RedoxFS\0", the version and the UUID.

use super::Content;
use crate::bytes::{le64, sub, uuid, ByteSource};
use crate::json::Obj;

pub fn probe(src: &dyn ByteSource) -> Option<Content> {
    let b = src.read_exact_at(0, 512)?;
    if !b.starts_with(b"RedoxFS\0") {
        return None;
    }
    Some(Content {
        kind: "redoxfs".into(),
        usage: "filesystem",
        uuid: Some(uuid(sub(&b, 16, 16))),
        label: None,
        version: Some(le64(&b, 8).to_string()),
        details: Obj::new().set("size_bytes", le64(&b, 32)),
        extra: None,
    })
}

#[cfg(test)]
pub mod sample {
    pub fn image() -> Vec<u8> {
        let mut img = vec![0u8; 65536];
        img[0..8].copy_from_slice(b"RedoxFS\0");
        img[8..16].copy_from_slice(&8u64.to_le_bytes());
        for i in 0..16u8 {
            img[16 + i as usize] = 0x10 + i;
        }
        img[32..40].copy_from_slice(&(1u64 << 30).to_le_bytes());
        img
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::bytes::MemSource;

    #[test]
    fn reads_the_header() {
        let c = probe(&MemSource(sample::image())).unwrap();
        assert_eq!(c.kind, "redoxfs");
        assert_eq!(c.version.as_deref(), Some("8"));
        assert_eq!(c.uuid.as_deref(), Some("10111213-1415-1617-1819-1a1b1c1d1e1f"));
        assert!(probe(&MemSource(vec![0u8; 65536])).is_none());
    }
}
