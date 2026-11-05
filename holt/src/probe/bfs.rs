//! BFS (Haiku): the superblock, 512 bytes into the volume.

use super::Content;
use crate::bytes::{label, le32, le64, sub, ByteSource};
use crate::json::Obj;

const MAGIC1: u32 = 0x4246_5331;
const MAGIC2: u32 = 0xdd12_1031;

pub fn probe(src: &dyn ByteSource) -> Option<Content> {
    let b = src.read_exact_at(512, 512)?;
    if le32(&b, 32) != MAGIC1 || le32(&b, 68) != MAGIC2 {
        return None;
    }
    let block = u64::from(le32(&b, 40));
    let d = Obj::new()
        .set("block_size", block)
        .set("blocks", le64(&b, 48))
        .set("size_bytes", le64(&b, 48) * block)
        .set("inode_size", le32(&b, 64))
        .set("blocks_per_ag", le32(&b, 72))
        .set("allocation_groups", le32(&b, 80));
    Some(Content { kind: "befs".into(), usage: "filesystem", uuid: None, label: label(sub(&b, 0, 32)), version: None, details: d, extra: None })
}

#[cfg(test)]
pub mod sample {
    pub fn image(name: &str) -> Vec<u8> {
        let mut img = vec![0u8; 65536];
        let s = &mut img[512..1024];
        s[..name.len()].copy_from_slice(name.as_bytes());
        s[32..36].copy_from_slice(&super::MAGIC1.to_le_bytes());
        s[40..44].copy_from_slice(&2048u32.to_le_bytes());
        s[48..56].copy_from_slice(&8388096u64.to_le_bytes());
        s[64..68].copy_from_slice(&2048u32.to_le_bytes());
        s[68..72].copy_from_slice(&super::MAGIC2.to_le_bytes());
        s[72..76].copy_from_slice(&4u32.to_le_bytes());
        s[80..84].copy_from_slice(&128u32.to_le_bytes());
        img
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::bytes::MemSource;
    use crate::json::Value;

    #[test]
    fn reads_a_superblock() {
        let c = probe(&MemSource(sample::image("Haiku"))).unwrap();
        assert_eq!(c.kind, "befs");
        assert_eq!(c.label.as_deref(), Some("Haiku"));
        assert_eq!(c.details.build().get("block_size").and_then(Value::as_u64), Some(2048));
        assert!(probe(&MemSource(vec![0u8; 65536])).is_none());
    }
}
