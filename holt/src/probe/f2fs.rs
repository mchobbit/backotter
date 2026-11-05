//! F2FS: its superblock, at 1 KiB (and a backup copy one block later).

use super::Content;
use crate::bytes::{le16, le32, le64, sub, uuid, ByteSource};
use crate::json::Obj;

const MAGIC: u32 = 0xF2F5_2010;

/// The superblock's feature bits, by the names mkfs.f2fs -O takes.
pub const FEATURES: &[(u32, &str)] = &[
    (0x0001, "encrypt"),
    (0x0002, "blkzoned"),
    (0x0004, "atomic_write"),
    (0x0008, "extra_attr"),
    (0x0010, "project_quota"),
    (0x0020, "inode_checksum"),
    (0x0040, "flexible_inline_xattr"),
    (0x0080, "quota"),
    (0x0100, "inode_crtime"),
    (0x0200, "lost_found"),
    (0x0400, "verity"),
    (0x0800, "sb_checksum"),
    (0x1000, "casefold"),
    (0x2000, "compression"),
    (0x4000, "ro"),
];

/// The volume name: up to 512 UTF-16LE units, zero-padded.
fn volume_name(b: &[u8]) -> Option<String> {
    let units: Vec<u16> = b.chunks_exact(2).map(|c| u16::from_le_bytes([c[0], c[1]])).take_while(|&u| u != 0).collect();
    let s = String::from_utf16_lossy(&units);
    (!s.is_empty()).then_some(s)
}

pub fn probe(src: &dyn ByteSource) -> Option<Content> {
    let b = src.read_exact_at(1024, 3072)?;
    if le32(&b, 0) != MAGIC {
        return None;
    }
    let log_block = le32(&b, 16);
    if !(9..=16).contains(&log_block) {
        return None;
    }
    let block = 1u64 << log_block;
    let blocks = le64(&b, 36);
    let feature = le32(&b, 2180);
    let mut names = Vec::new();
    for bit in 0..32 {
        if feature & (1 << bit) != 0 {
            match FEATURES.iter().find(|(v, _)| *v == 1 << bit) {
                Some((_, n)) => names.push(n.to_string()),
                None => names.push(format!("feature_bit{bit}")),
            }
        }
    }
    let mut d = Obj::new()
        .set("block_size", block)
        .set("blocks", blocks)
        .set("size_bytes", blocks * block)
        .set("sector_size", 1u64 << le32(&b, 8).min(16))
        .set("blocks_per_segment", 1u64 << le32(&b, 20).min(16))
        .set("segments_per_section", le32(&b, 24))
        .set("sections_per_zone", le32(&b, 28))
        .set("segment_count", le32(&b, 48))
        .set("features", names);
    if feature & 0x1000 != 0 {
        // s_encoding: 1 is UTF-8 (12.1).
        d.put("encoding", if le16(&b, 2758) == 1 { "utf8" } else { "unknown" });
    }
    let version = format!("{}.{}", le16(&b, 4), le16(&b, 6));
    Some(Content {
        kind: "f2fs".into(),
        usage: "filesystem",
        uuid: Some(uuid(sub(&b, 108, 16))),
        label: volume_name(sub(&b, 124, 1024)),
        version: Some(version),
        details: d,
        extra: None,
    })
}

#[cfg(test)]
pub mod sample {
    /// An F2FS superblock at 1 KiB in a 64 KiB image.
    pub fn image(label: &str, feature: u32) -> Vec<u8> {
        let mut img = vec![0u8; 65536];
        let s = &mut img[1024..4096];
        s[0..4].copy_from_slice(&super::MAGIC.to_le_bytes());
        s[4..6].copy_from_slice(&1u16.to_le_bytes());
        s[6..8].copy_from_slice(&16u16.to_le_bytes());
        s[8..12].copy_from_slice(&9u32.to_le_bytes());
        s[12..16].copy_from_slice(&3u32.to_le_bytes());
        s[16..20].copy_from_slice(&12u32.to_le_bytes());
        s[20..24].copy_from_slice(&9u32.to_le_bytes());
        s[24..28].copy_from_slice(&1u32.to_le_bytes());
        s[28..32].copy_from_slice(&1u32.to_le_bytes());
        s[36..44].copy_from_slice(&3407872u64.to_le_bytes());
        s[48..52].copy_from_slice(&6655u32.to_le_bytes());
        for (i, b) in (0..16u8).enumerate() {
            s[108 + i] = 0x80 + b;
        }
        for (i, u) in label.encode_utf16().enumerate() {
            s[124 + 2 * i..126 + 2 * i].copy_from_slice(&u.to_le_bytes());
        }
        s[2180..2184].copy_from_slice(&feature.to_le_bytes());
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
        let c = probe(&MemSource(sample::image("mintroot", 0x0008 | 0x0020 | 0x0800))).unwrap();
        assert_eq!(c.kind, "f2fs");
        assert_eq!(c.uuid.as_deref(), Some("80818283-8485-8687-8889-8a8b8c8d8e8f"));
        assert_eq!(c.label.as_deref(), Some("mintroot"));
        assert_eq!(c.version.as_deref(), Some("1.16"));
        let d = c.details.build();
        assert_eq!(d.get("block_size").and_then(Value::as_u64), Some(4096));
        assert_eq!(d.get("size_bytes").and_then(Value::as_u64), Some(3407872 * 4096));
        assert_eq!(d.get("blocks_per_segment").and_then(Value::as_u64), Some(512));
        assert_eq!(d.get("features"), Some(&Value::from(vec!["extra_attr", "inode_checksum", "sb_checksum"])));
        assert!(probe(&MemSource(sample::image("", 0))).unwrap().label.is_none());
        assert!(probe(&MemSource(vec![0u8; 65536])).is_none());
    }
}
