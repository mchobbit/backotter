//! ext2/3/4 superblocks, with feature names exactly as dumpe2fs prints them.

use super::Content;
use crate::bytes::{le16, le32, sub, u8_at, uuid, ByteSource};
use crate::json::Obj;

const SB: u64 = 1024;
const MAGIC: u16 = 0xef53;

// (bit, name) in e2fsprogs' lib/e2p/feature.c spelling.
const COMPAT: &[(u32, &str)] = &[
    (0x0001, "dir_prealloc"),
    (0x0002, "imagic_inodes"),
    (0x0004, "has_journal"),
    (0x0008, "ext_attr"),
    (0x0010, "resize_inode"),
    (0x0020, "dir_index"),
    (0x0040, "lazy_bg"),
    (0x0100, "snapshot_bitmap"),
    (0x0200, "sparse_super2"),
    (0x0400, "fast_commit"),
    (0x0800, "stable_inodes"),
    (0x1000, "orphan_file"),
];
const INCOMPAT: &[(u32, &str)] = &[
    (0x0001, "compression"),
    (0x0002, "filetype"),
    (0x0004, "needs_recovery"),
    (0x0008, "journal_dev"),
    (0x0010, "meta_bg"),
    (0x0040, "extent"),
    (0x0080, "64bit"),
    (0x0100, "mmp"),
    (0x0200, "flex_bg"),
    (0x0400, "ea_inode"),
    (0x1000, "dirdata"),
    (0x2000, "metadata_csum_seed"),
    (0x4000, "large_dir"),
    (0x8000, "inline_data"),
    (0x10000, "encrypt"),
    (0x20000, "casefold"),
];
const RO_COMPAT: &[(u32, &str)] = &[
    (0x0001, "sparse_super"),
    (0x0002, "large_file"),
    (0x0008, "huge_file"),
    (0x0010, "uninit_bg"),
    (0x0020, "dir_nlink"),
    (0x0040, "extra_isize"),
    (0x0080, "snapshot"),
    (0x0100, "quota"),
    (0x0200, "bigalloc"),
    (0x0400, "metadata_csum"),
    (0x0800, "replica"),
    (0x1000, "read-only"),
    (0x2000, "project"),
    (0x4000, "shared_blocks"),
    (0x8000, "verity"),
    (0x10000, "orphan_present"),
];

fn names(mask: u32, table: &[(u32, &str)], unknown_prefix: char, out: &mut Vec<String>) {
    for bit in 0..32 {
        let b = 1u32 << bit;
        if mask & b == 0 {
            continue;
        }
        match table.iter().find(|(v, _)| *v == b) {
            Some((_, n)) => out.push(n.to_string()),
            None => out.push(format!("FEATURE_{unknown_prefix}{bit}")),
        }
    }
}

/// The features in dumpe2fs's order: compat, incompat, ro_compat.
pub fn feature_names(compat: u32, incompat: u32, ro_compat: u32) -> Vec<String> {
    let mut v = Vec::new();
    names(compat, COMPAT, 'C', &mut v);
    names(incompat, INCOMPAT, 'I', &mut v);
    names(ro_compat, RO_COMPAT, 'R', &mut v);
    v
}

/// ext2, ext3 or ext4 the way blkid decides it.
fn version(compat: u32, incompat: u32, ro_compat: u32) -> &'static str {
    const EXT3_INCOMPAT_SUPP: u32 = 0x0002 | 0x0004 | 0x0010; // filetype, recover, meta_bg
    const EXT3_RO_SUPP: u32 = 0x0001 | 0x0002 | 0x0004; // sparse_super, large_file, btree_dir
    if incompat & 0x0008 != 0 {
        return "jbd";
    }
    if incompat & !EXT3_INCOMPAT_SUPP != 0 || ro_compat & !EXT3_RO_SUPP != 0 {
        return "ext4";
    }
    if compat & 0x0004 != 0 {
        "ext3"
    } else {
        "ext2"
    }
}

pub fn probe(src: &dyn ByteSource) -> Option<Content> {
    let b = src.read_exact_at(SB, 1024)?;
    if le16(&b, 56) != MAGIC {
        return None;
    }
    let log_block = le32(&b, 24);
    if log_block > 6 {
        return None; // block size beyond 64 KiB: not a real superblock
    }
    let block_size = 1024u64 << log_block;
    let (compat, incompat, ro) = (le32(&b, 92), le32(&b, 96), le32(&b, 100));
    let is64 = incompat & 0x0080 != 0;
    let hi = |o: usize| if is64 { (le32(&b, o) as u64) << 32 } else { 0 };
    let blocks = le32(&b, 4) as u64 | hi(336);
    let reserved = le32(&b, 8) as u64 | hi(340);
    let free = le32(&b, 12) as u64 | hi(344);
    let kind = version(compat, incompat, ro);
    let state = le16(&b, 58);
    let errors = match le16(&b, 60) {
        1 => "continue",
        2 => "remount-ro",
        3 => "panic",
        _ => "unknown",
    };
    let creator = match le32(&b, 72) {
        0 => "Linux",
        1 => "Hurd",
        2 => "Masix",
        3 => "FreeBSD",
        4 => "Lites",
        _ => "unknown",
    };
    let hash_version = match u8_at(&b, 252) {
        0 => "legacy",
        1 => "half_md4",
        2 => "tea",
        3 => "legacy_unsigned",
        4 => "half_md4_unsigned",
        5 => "tea_unsigned",
        6 => "siphash",
        _ => "unknown",
    };
    let journal_uuid = sub(&b, 208, 16);
    let mut d = Obj::new()
        .set("block_size", block_size)
        .set("blocks", blocks)
        .set("size_bytes", blocks * block_size)
        .set("free_blocks", free)
        .set("reserved_blocks", reserved)
        .set("inodes", le32(&b, 0))
        .set("free_inodes", le32(&b, 16))
        .set("inode_size", le16(&b, 88))
        .set("inodes_per_group", le32(&b, 40))
        .set("blocks_per_group", le32(&b, 32))
        .set("first_data_block", le32(&b, 20))
        .set("revision", le32(&b, 76))
        .set("features", feature_names(compat, incompat, ro))
        .set("state", if state & 1 != 0 { "clean" } else { "not clean" })
        .set("has_errors", state & 2 != 0)
        .set("errors_behavior", errors)
        .set("creator_os", creator)
        .set("reserved_gdt_blocks", le16(&b, 206))
        .set("default_hash", hash_version)
        .set("hash_seed", uuid(sub(&b, 236, 16)))
        .set("mkfs_time", le32(&b, 264))
        .set("last_mounted_on", crate::bytes::label(sub(&b, 136, 64)));
    if incompat & 0x0200 != 0 {
        d.put("flex_bg_size", 1u64 << u8_at(&b, 372).min(31));
    }
    if is64 {
        d.put("descriptor_size", le16(&b, 254));
    }
    if ro & 0x0200 != 0 {
        d.put("cluster_size", 1024u64 << le32(&b, 28).min(30));
    }
    if ro & 0x0040 != 0 {
        d.put("min_extra_isize", le16(&b, 348));
        d.put("want_extra_isize", le16(&b, 350));
    }
    if incompat & 0x20000 != 0 {
        d.put("encoding", if le16(&b, 0x27c) == 1 { "utf8" } else { "unknown" });
    }
    if journal_uuid.iter().any(|&x| x != 0) {
        d.put("journal_uuid", uuid(journal_uuid));
    }
    if compat & 0x0004 != 0 {
        d.put("journal_inode", le32(&b, 224));
    }
    let journal_dev = le32(&b, 228);
    if journal_dev != 0 {
        d.put("journal_device", format!("{}:{}", (journal_dev >> 8) & 0xfff, journal_dev & 0xff));
    }
    Some(Content {
        kind: kind.to_string(),
        usage: if kind == "jbd" { "other" } else { "filesystem" },
        uuid: Some(uuid(sub(&b, 104, 16))),
        label: crate::bytes::label(sub(&b, 120, 16)),
        version: Some(format!("1.{}", le16(&b, 62))).filter(|_| le32(&b, 76) >= 1),
        details: d,
        extra: None,
    })
}

#[cfg(test)]
pub mod sample {
    /// A minimal ext superblock at 1024 in a 64 KiB buffer.
    pub fn image(compat: u32, incompat: u32, ro: u32, label: &str) -> Vec<u8> {
        let mut img = vec![0u8; 65536];
        let s = &mut img[1024..2048];
        s[0..4].copy_from_slice(&8192u32.to_le_bytes()); // inodes
        s[4..8].copy_from_slice(&16384u32.to_le_bytes()); // blocks lo
        s[24..28].copy_from_slice(&2u32.to_le_bytes()); // 4 KiB blocks
        s[32..36].copy_from_slice(&32768u32.to_le_bytes());
        s[40..44].copy_from_slice(&8192u32.to_le_bytes());
        s[56..58].copy_from_slice(&0xef53u16.to_le_bytes());
        s[58..60].copy_from_slice(&1u16.to_le_bytes());
        s[60..62].copy_from_slice(&1u16.to_le_bytes());
        s[76..80].copy_from_slice(&1u32.to_le_bytes());
        s[88..90].copy_from_slice(&256u16.to_le_bytes());
        s[92..96].copy_from_slice(&compat.to_le_bytes());
        s[96..100].copy_from_slice(&incompat.to_le_bytes());
        s[100..104].copy_from_slice(&ro.to_le_bytes());
        for (i, b) in s[104..120].iter_mut().enumerate() {
            *b = 0x10 + i as u8;
        }
        s[120..120 + label.len()].copy_from_slice(label.as_bytes());
        s[136..137].copy_from_slice(b"/");
        img
    }
}

#[cfg(test)]
mod tests {
    use super::sample::image;
    use super::*;
    use crate::bytes::MemSource;
    use crate::json::Value;

    // What Debian 13's mke2fs.conf gives ext4.
    const T_COMPAT: u32 = 0x0004 | 0x0008 | 0x0010 | 0x0020 | 0x1000;
    const T_INCOMPAT: u32 = 0x0002 | 0x0040 | 0x0080 | 0x0200 | 0x2000;
    const T_RO: u32 = 0x0001 | 0x0002 | 0x0008 | 0x0020 | 0x0040 | 0x0400;

    #[test]
    fn trixie_ext4() {
        let c = probe(&MemSource(image(T_COMPAT, T_INCOMPAT, T_RO, "root"))).unwrap();
        assert_eq!(c.kind, "ext4");
        assert_eq!(c.usage, "filesystem");
        assert_eq!(c.label.as_deref(), Some("root"));
        assert_eq!(c.uuid.as_deref(), Some("10111213-1415-1617-1819-1a1b1c1d1e1f"));
        assert_eq!(c.version.as_deref(), Some("1.0"));
        let d = c.details.build();
        assert_eq!(
            d.get("features"),
            Some(&Value::from(vec![
                "has_journal", "ext_attr", "resize_inode", "dir_index", "orphan_file", "filetype", "extent", "64bit",
                "flex_bg", "metadata_csum_seed", "sparse_super", "large_file", "huge_file", "dir_nlink", "extra_isize",
                "metadata_csum"
            ]))
        );
        assert_eq!(d.get("block_size").and_then(Value::as_u64), Some(4096));
        assert_eq!(d.get("size_bytes").and_then(Value::as_u64), Some(16384 * 4096));
        assert_eq!(d.get("inode_size").and_then(Value::as_u64), Some(256));
        assert_eq!(d.get("state").and_then(Value::as_str), Some("clean"));
        assert_eq!(d.get("errors_behavior").and_then(Value::as_str), Some("continue"));
        assert_eq!(d.get("last_mounted_on").and_then(Value::as_str), Some("/"));
        assert!(d.get("descriptor_size").is_some());
        assert!(d.get("flex_bg_size").is_some());
        assert!(d.get("journal_inode").is_some());
        assert!(d.get("journal_uuid").is_none());
    }

    #[test]
    fn versions() {
        assert_eq!(probe(&MemSource(image(0x0004, 0x0002, 0x0001, ""))).unwrap().kind, "ext3");
        assert_eq!(probe(&MemSource(image(0, 0x0002, 0x0003, ""))).unwrap().kind, "ext2");
        // ext3 features plus one ext4-only one makes ext4.
        assert_eq!(probe(&MemSource(image(0x0004, 0x0002 | 0x0040, 0, ""))).unwrap().kind, "ext4");
        assert_eq!(probe(&MemSource(image(0x0004, 0x0002, 0x0008, ""))).unwrap().kind, "ext4");
        let j = probe(&MemSource(image(0, 0x0008, 0, ""))).unwrap();
        assert_eq!(j.kind, "jbd");
        assert_eq!(j.usage, "other");
        assert_eq!(j.label, None);
    }

    #[test]
    fn unknown_feature_bits_named_like_e2fsprogs() {
        let v = feature_names(1 << 13, 1 << 5, 1 << 20);
        assert_eq!(v, vec!["FEATURE_C13", "FEATURE_I5", "FEATURE_R20"]);
        // Every table entry is a single bit.
        for t in [COMPAT, INCOMPAT, RO_COMPAT] {
            for (b, _) in t {
                assert_eq!(b.count_ones(), 1);
            }
        }
    }

    #[test]
    fn optional_fields() {
        let mut img = image(0x0004, 0x0002 | 0x20000, 0x0200 | 0x0040, "x");
        img[1024 + 0x27c] = 1; // utf8
        img[1024 + 28] = 4; // cluster 16 KiB
        img[1024 + 208] = 0xaa; // external journal uuid
        img[1024 + 228..1024 + 232].copy_from_slice(&0x0811u32.to_le_bytes());
        img[1024 + 58] = 0; // not clean
        img[1024 + 60] = 2;
        let d = probe(&MemSource(img)).unwrap().details.build();
        assert_eq!(d.get("encoding").and_then(Value::as_str), Some("utf8"));
        assert_eq!(d.get("cluster_size").and_then(Value::as_u64), Some(16384));
        assert!(d.get("journal_uuid").unwrap().as_str().unwrap().starts_with("aa00"));
        assert_eq!(d.get("journal_device").and_then(Value::as_str), Some("8:17"));
        assert_eq!(d.get("state").and_then(Value::as_str), Some("not clean"));
        assert_eq!(d.get("errors_behavior").and_then(Value::as_str), Some("remount-ro"));
        assert!(d.get("min_extra_isize").is_some());
        assert!(d.get("descriptor_size").is_none());
    }

    #[test]
    fn sixty_four_bit_counts() {
        let mut img = image(0, 0x0080, 0, "");
        img[1024 + 336..1024 + 340].copy_from_slice(&1u32.to_le_bytes());
        let d = probe(&MemSource(img)).unwrap().details.build();
        assert_eq!(d.get("blocks").and_then(Value::as_u64), Some((1u64 << 32) + 16384));
        // Without 64bit the high word is ignored.
        let mut img = image(0, 0, 0, "");
        img[1024 + 336..1024 + 340].copy_from_slice(&1u32.to_le_bytes());
        let d = probe(&MemSource(img)).unwrap().details.build();
        assert_eq!(d.get("blocks").and_then(Value::as_u64), Some(16384));
    }

    #[test]
    fn rejects() {
        assert!(probe(&MemSource(vec![0u8; 65536])).is_none());
        assert!(probe(&MemSource(vec![0u8; 1500])).is_none());
        let mut img = image(0, 0, 0, "");
        img[1024 + 24] = 9; // absurd block size
        assert!(probe(&MemSource(img)).is_none());
    }
}
