//! NILFS2: its superblock, at 1 KiB (a second copy sits near the end of the
//! device).  Offsets checked against Zorin OS 18's root, made by its
//! installer's mkfs.nilfs2.

use super::Content;
use crate::bytes::{le16, le32, le64, sub, uuid, ByteSource};
use crate::json::Obj;

const MAGIC: u16 = 0x3434;

/// Feature bits, by the names mkfs.nilfs2 -O takes: (word offset in the
/// superblock, bit, name).  Only compat_ro's block_count exists so far.
pub const FEATURES: &[(usize, u64, &str)] = &[(264, 0x1, "block_count")];

/// The volume name: up to 80 bytes, NUL-padded.
fn volume_name(b: &[u8]) -> Option<String> {
    let end = b.iter().position(|&c| c == 0).unwrap_or(b.len());
    let s = String::from_utf8_lossy(&b[..end]).into_owned();
    (!s.is_empty()).then_some(s)
}

pub fn probe(src: &dyn ByteSource) -> Option<Content> {
    let b = src.read_exact_at(1024, 1024)?;
    if le16(&b, 6) != MAGIC {
        return None;
    }
    let log_block = le32(&b, 20);
    if log_block > 6 {
        return None;
    }
    let block = 1024u64 << log_block;
    let mut names = Vec::new();
    for (off, label) in [(256usize, "compat"), (264, "compat_ro"), (272, "incompat")] {
        let word = le64(&b, off);
        for bit in 0..64 {
            if word & (1 << bit) != 0 {
                match FEATURES.iter().find(|(o, v, _)| *o == off && *v == 1 << bit) {
                    Some((_, _, n)) => names.push(n.to_string()),
                    None => names.push(format!("{label}_bit{bit}")),
                }
            }
        }
    }
    let d = Obj::new()
        .set("block_size", block)
        .set("size_bytes", le64(&b, 32))
        .set("segments", le64(&b, 24))
        .set("blocks_per_segment", le32(&b, 48))
        .set("reserved_segments_percentage", le32(&b, 52))
        .set("inode_size", le16(&b, 144))
        .set("features", names);
    Some(Content {
        kind: "nilfs2".into(),
        usage: "filesystem",
        uuid: Some(uuid(sub(&b, 152, 16))),
        label: volume_name(sub(&b, 168, 80)),
        version: Some(format!("{}.{}", le32(&b, 0), le16(&b, 4))),
        details: d,
        extra: None,
    })
}

#[cfg(test)]
pub mod sample {
    /// A NILFS2 superblock at 1 KiB in a 64 KiB image, as Zorin's.
    pub fn image(label: &str, compat_ro: u64) -> Vec<u8> {
        let mut img = vec![0u8; 65536];
        let s = &mut img[1024..2048];
        s[0..4].copy_from_slice(&2u32.to_le_bytes());
        s[6..8].copy_from_slice(&super::MAGIC.to_le_bytes());
        s[20..24].copy_from_slice(&2u32.to_le_bytes());
        s[24..32].copy_from_slice(&1663u64.to_le_bytes());
        s[32..40].copy_from_slice(&13957595136u64.to_le_bytes());
        s[48..52].copy_from_slice(&2048u32.to_le_bytes());
        s[52..56].copy_from_slice(&5u32.to_le_bytes());
        s[144..146].copy_from_slice(&128u16.to_le_bytes());
        for (i, b) in (0..16u8).enumerate() {
            s[152 + i] = 0x90 + b;
        }
        s[168..168 + label.len()].copy_from_slice(label.as_bytes());
        s[264..272].copy_from_slice(&compat_ro.to_le_bytes());
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
        let c = probe(&MemSource(sample::image("zorinroot", 1))).unwrap();
        assert_eq!(c.kind, "nilfs2");
        assert_eq!(c.uuid.as_deref(), Some("90919293-9495-9697-9899-9a9b9c9d9e9f"));
        assert_eq!(c.label.as_deref(), Some("zorinroot"));
        assert_eq!(c.version.as_deref(), Some("2.0"));
        let d = c.details.build();
        assert_eq!(d.get("block_size").and_then(Value::as_u64), Some(4096));
        assert_eq!(d.get("blocks_per_segment").and_then(Value::as_u64), Some(2048));
        assert_eq!(d.get("reserved_segments_percentage").and_then(Value::as_u64), Some(5));
        assert_eq!(d.get("features"), Some(&Value::from(vec!["block_count"])));
        assert!(probe(&MemSource(sample::image("", 0))).unwrap().label.is_none());
        assert!(probe(&MemSource(vec![0u8; 65536])).is_none());
    }
}
