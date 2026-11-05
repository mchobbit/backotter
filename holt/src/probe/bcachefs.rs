//! bcachefs: its superblock, at 4 KiB (sector 8).  NixOS can be installed on
//! it.  The options that `bcachefs format` takes are bit fields in the
//! superblock's flags words (bcachefs_format.h's LE64_BITMASKs).

use super::Content;
use crate::bytes::{label, le16, le32, le64, sub, u8_at, uuid, ByteSource};
use crate::json::Obj;

/// BCACHE_MAGIC (bcache's, which bcachefs's first versions kept) and
/// BCHFS_MAGIC.
const MAGICS: [[u8; 16]; 2] = [
    [0xc6, 0x85, 0x73, 0xf6, 0x4e, 0x1a, 0x45, 0xca, 0x82, 0x65, 0xf5, 0x7f, 0x48, 0xba, 0x6d, 0x81],
    [0xc6, 0x85, 0x73, 0xf6, 0x66, 0xce, 0x90, 0xa9, 0xd9, 0x6a, 0x60, 0xcf, 0x80, 0x3d, 0xf7, 0xef],
];

pub const CSUM: &[&str] = &["none", "crc32c", "crc64", "xxhash"];
pub const COMPRESSION: &[&str] = &["none", "lz4", "gzip", "zstd"];
pub const STR_HASH: &[&str] = &["crc32c", "crc64", "siphash"];

fn bits(w: u64, lo: u32, hi: u32) -> u64 {
    (w >> lo) & ((1u64 << (hi - lo)) - 1)
}

fn name(table: &[&str], v: u64) -> String {
    table.get(v as usize).map(|s| s.to_string()).unwrap_or_else(|| v.to_string())
}

/// "1.13" from BCH_VERSION(major, minor) = major << 10 | minor.
pub fn version_name(v: u16) -> String {
    format!("{}.{}", v >> 10, v & 0x3ff)
}

pub fn probe(src: &dyn ByteSource) -> Option<Content> {
    let b = src.read_exact_at(4096, 512)?;
    if !MAGICS.iter().any(|m| sub(&b, 24, 16) == m) {
        return None;
    }
    let f0 = le64(&b, 144);
    let f1 = le64(&b, 152);
    let block = u64::from(le16(&b, 120)) * 512;
    let d = Obj::new()
        .set("version_min", version_name(le16(&b, 18)))
        .set("internal_uuid", uuid(sub(&b, 40, 16)))
        .set("block_size", block)
        .set("btree_node_size", bits(f0, 12, 28) * 512)
        .set("nr_devices", u64::from(u8_at(&b, 123)))
        .set("dev_idx", u64::from(u8_at(&b, 122)))
        .set("u64s", le32(&b, 124))
        .set("metadata_checksum", name(CSUM, bits(f0, 40, 44)))
        .set("data_checksum", name(CSUM, bits(f0, 44, 48)))
        .set("metadata_replicas", bits(f0, 48, 52))
        .set("data_replicas", bits(f0, 52, 56))
        .set("acl", bits(f0, 56, 57) == 1)
        .set("str_hash", name(STR_HASH, bits(f1, 0, 4)))
        .set("compression", name(COMPRESSION, bits(f1, 4, 8)))
        .set("encrypted", bits(f1, 10, 14) != 0);
    Some(Content {
        kind: "bcachefs".into(),
        usage: "filesystem",
        uuid: Some(uuid(sub(&b, 56, 16))),
        label: label(sub(&b, 72, 32)),
        version: Some(version_name(le16(&b, 16))),
        details: d,
        extra: None,
    })
}

#[cfg(test)]
pub mod sample {
    /// A bcachefs superblock at 4 KiB in a 64 KiB image: version 1.13,
    /// 4 KiB blocks, 256 KiB btree nodes, crc32c, siphash, ACLs.
    pub fn image(label: &str, compression: u64) -> Vec<u8> {
        let mut img = vec![0u8; 65536];
        let s = &mut img[4096..4608];
        s[16..18].copy_from_slice(&((1u16 << 10) | 13).to_le_bytes());
        s[18..20].copy_from_slice(&((1u16 << 10) | 13).to_le_bytes());
        s[24..40].copy_from_slice(&super::MAGICS[1]);
        for i in 0..16u8 {
            s[40 + i as usize] = 0x40 + i;
            s[56 + i as usize] = 0x80 + i;
        }
        s[72..72 + label.len()].copy_from_slice(label.as_bytes());
        s[120..122].copy_from_slice(&8u16.to_le_bytes());
        s[123] = 1;
        let f0: u64 = 1 | (512 << 12) | (1 << 40) | (1 << 44) | (1 << 48) | (1 << 52) | (1 << 56);
        let f1: u64 = 2 | (compression << 4);
        s[144..152].copy_from_slice(&f0.to_le_bytes());
        s[152..160].copy_from_slice(&f1.to_le_bytes());
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
        let c = probe(&MemSource(sample::image("nixroot", 3))).unwrap();
        assert_eq!(c.kind, "bcachefs");
        assert_eq!(c.uuid.as_deref(), Some("80818283-8485-8687-8889-8a8b8c8d8e8f"));
        assert_eq!(c.label.as_deref(), Some("nixroot"));
        assert_eq!(c.version.as_deref(), Some("1.13"));
        let d = c.details.build();
        assert_eq!(d.get("block_size").and_then(Value::as_u64), Some(4096));
        assert_eq!(d.get("btree_node_size").and_then(Value::as_u64), Some(262144));
        assert_eq!(d.get("data_checksum").and_then(Value::as_str), Some("crc32c"));
        assert_eq!(d.get("str_hash").and_then(Value::as_str), Some("siphash"));
        assert_eq!(d.get("compression").and_then(Value::as_str), Some("zstd"));
        assert_eq!(d.get("acl").and_then(Value::as_bool), Some(true));
        assert!(probe(&MemSource(sample::image("", 0))).unwrap().label.is_none());
        assert!(probe(&MemSource(vec![0u8; 65536])).is_none());
    }
}
