//! HAMMER2 (DragonFly): the volume header at the start of the volume.
//! Its PFS names (ROOT, LOCAL) come from hammer2 pfs-list (other/).

use super::Content;
use crate::bytes::{guid, le32, le64, sub, ByteSource};
use crate::json::Obj;

const MAGIC: u64 = 0x4841_4d32_0517_2011;

pub fn probe(src: &dyn ByteSource) -> Option<Content> {
    let b = src.read_exact_at(0, 512)?;
    if le64(&b, 0) != MAGIC {
        return None;
    }
    let d = Obj::new().set("volume_size", le64(&b, 0x28)).set("volumes", u64::from(crate::bytes::u8_at(&b, 0x3c)));
    Some(Content {
        kind: "hammer2".into(),
        usage: "filesystem",
        uuid: Some(guid(sub(&b, 0x40, 16))),
        label: None,
        version: Some(le32(&b, 0x30).to_string()),
        details: d,
        extra: None,
    })
}

#[cfg(test)]
pub mod sample {
    pub fn image() -> Vec<u8> {
        let mut img = vec![0u8; 65536];
        img[0..8].copy_from_slice(&super::MAGIC.to_le_bytes());
        img[0x28..0x30].copy_from_slice(&(13u64 << 30).to_le_bytes());
        img[0x30..0x34].copy_from_slice(&2u32.to_le_bytes());
        img[0x3c] = 1;
        img[0x40..0x50].copy_from_slice(&[0x1d, 0x27, 0x1a, 0x42, 0x87, 0xd6, 0xf1, 0x11, 0xa4, 0x93, 1, 0, 0, 0, 0, 0]);
        img
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::bytes::MemSource;

    #[test]
    fn reads_a_volume_header() {
        let c = probe(&MemSource(sample::image())).unwrap();
        assert_eq!(c.kind, "hammer2");
        assert_eq!(c.uuid.as_deref(), Some("421a271d-d687-11f1-a493-010000000000"));
        assert_eq!(c.version.as_deref(), Some("2"));
        assert!(probe(&MemSource(vec![0u8; 65536])).is_none());
    }
}
