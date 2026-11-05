//! btrfs superblocks (the primary copy at 64 KiB).

use super::Content;
use crate::bytes::{le16, le32, le64, sub, uuid, ByteSource};
use crate::json::Obj;

const SB: u64 = 0x10000;

const INCOMPAT: &[(u64, &str)] = &[
    (0x1, "mixed_backref"),
    (0x2, "default_subvol"),
    (0x4, "mixed_groups"),
    (0x8, "compress_lzo"),
    (0x10, "compress_zstd"),
    (0x20, "big_metadata"),
    (0x40, "extended_iref"),
    (0x80, "raid56"),
    (0x100, "skinny_metadata"),
    (0x200, "no_holes"),
    (0x400, "metadata_uuid"),
    (0x800, "raid1c34"),
    (0x1000, "zoned"),
    (0x2000, "extent_tree_v2"),
    (0x4000, "raid_stripe_tree"),
    (0x10000, "simple_quota"),
];
const COMPAT_RO: &[(u64, &str)] = &[
    (0x1, "free_space_tree"),
    (0x2, "free_space_tree_valid"),
    (0x4, "verity"),
    (0x8, "block_group_tree"),
];

fn names(mask: u64, table: &[(u64, &str)], prefix: &str) -> Vec<String> {
    (0..64)
        .filter(|b| mask & (1u64 << b) != 0)
        .map(|b| table.iter().find(|(v, _)| *v == 1u64 << b).map_or_else(|| format!("{prefix}_bit{b}"), |(_, n)| n.to_string()))
        .collect()
}

pub fn probe(src: &dyn ByteSource) -> Option<Content> {
    let b = src.read_exact_at(SB, 4096)?;
    if sub(&b, 0x40, 8) != b"_BHRfS_M" {
        return None;
    }
    let sectorsize = le32(&b, 0x90);
    let nodesize = le32(&b, 0x94);
    if !sectorsize.is_power_of_two() || !nodesize.is_power_of_two() || sectorsize < 512 {
        return None;
    }
    let incompat = le64(&b, 0xbc);
    let compat_ro = le64(&b, 0xb4);
    let csum = match le16(&b, 0xc4) {
        0 => "crc32c",
        1 => "xxhash64",
        2 => "sha256",
        3 => "blake2b",
        _ => "unknown",
    };
    let mut d = Obj::new()
        .set("total_bytes", le64(&b, 0x70))
        .set("bytes_used", le64(&b, 0x78))
        .set("num_devices", le64(&b, 0x88))
        .set("sector_size", sectorsize)
        .set("node_size", nodesize)
        .set("stripe_size", le32(&b, 0x9c))
        .set("checksum", csum)
        .set("generation", le64(&b, 0x48))
        .set("features", names(incompat, INCOMPAT, "incompat"))
        .set("compat_ro_features", names(compat_ro, COMPAT_RO, "compat_ro"))
        .set(
            "device",
            Obj::new()
                .set("devid", le64(&b, 0xc9))
                .set("uuid", uuid(sub(&b, 0x10b, 16)))
                .set("total_bytes", le64(&b, 0xd1))
                .set("bytes_used", le64(&b, 0xd9)),
        );
    if incompat & 0x400 != 0 {
        d.put("metadata_uuid", uuid(sub(&b, 0x23b, 16)));
    }
    Some(Content {
        kind: "btrfs".into(),
        usage: "filesystem",
        uuid: Some(uuid(sub(&b, 0x20, 16))),
        label: crate::bytes::label(sub(&b, 0x12b, 256)),
        version: None,
        details: d,
        extra: None,
    })
}

#[cfg(test)]
pub mod sample {
    pub fn image(label: &str, incompat: u64) -> Vec<u8> {
        let mut img = vec![0u8; 0x20000];
        let s = &mut img[0x10000..0x11000];
        for (i, b) in s[0x20..0x30].iter_mut().enumerate() {
            *b = 0xb0 + i as u8;
        }
        s[0x40..0x48].copy_from_slice(b"_BHRfS_M");
        s[0x48..0x50].copy_from_slice(&9u64.to_le_bytes());
        s[0x70..0x78].copy_from_slice(&(1u64 << 30).to_le_bytes());
        s[0x78..0x80].copy_from_slice(&(5u64 << 20).to_le_bytes());
        s[0x88..0x90].copy_from_slice(&1u64.to_le_bytes());
        s[0x90..0x94].copy_from_slice(&4096u32.to_le_bytes());
        s[0x94..0x98].copy_from_slice(&16384u32.to_le_bytes());
        s[0x9c..0xa0].copy_from_slice(&4096u32.to_le_bytes());
        s[0xb4..0xbc].copy_from_slice(&3u64.to_le_bytes());
        s[0xbc..0xc4].copy_from_slice(&incompat.to_le_bytes());
        s[0xc9..0xd1].copy_from_slice(&1u64.to_le_bytes());
        s[0x10b] = 0xdd;
        s[0x12b..0x12b + label.len()].copy_from_slice(label.as_bytes());
        img
    }
}

#[cfg(test)]
mod tests {
    use super::sample::image;
    use super::*;
    use crate::bytes::MemSource;
    use crate::json::Value;

    #[test]
    fn trixie_defaults() {
        // mkfs.btrfs 6.14 defaults: mixed_backref, big_metadata,
        // extended_iref, skinny_metadata, no_holes; free-space-tree.
        let c = probe(&MemSource(image("debian", 0x1 | 0x20 | 0x40 | 0x100 | 0x200))).unwrap();
        assert_eq!(c.kind, "btrfs");
        assert_eq!(c.label.as_deref(), Some("debian"));
        assert_eq!(c.uuid.as_deref(), Some("b0b1b2b3-b4b5-b6b7-b8b9-babbbcbdbebf"));
        let d = c.details.build();
        assert_eq!(
            d.get("features"),
            Some(&Value::from(vec!["mixed_backref", "big_metadata", "extended_iref", "skinny_metadata", "no_holes"]))
        );
        assert_eq!(d.get("compat_ro_features"), Some(&Value::from(vec!["free_space_tree", "free_space_tree_valid"])));
        assert_eq!(d.get("checksum").and_then(Value::as_str), Some("crc32c"));
        assert_eq!(d.get("node_size").and_then(Value::as_u64), Some(16384));
        assert_eq!(d.at(&["device", "devid"]).and_then(Value::as_u64), Some(1));
        assert!(d.at(&["device", "uuid"]).unwrap().as_str().unwrap().starts_with("dd"));
        assert!(d.get("metadata_uuid").is_none());
    }

    #[test]
    fn metadata_uuid_and_unknown_bits() {
        let mut img = image("", 0x400 | (1 << 40));
        img[0x10000 + 0x23b] = 0xee;
        img[0x10000 + 0xc4] = 1;
        let c = probe(&MemSource(img)).unwrap();
        assert_eq!(c.label, None);
        let d = c.details.build();
        assert!(d.get("metadata_uuid").unwrap().as_str().unwrap().starts_with("ee"));
        assert_eq!(d.get("features"), Some(&Value::from(vec!["metadata_uuid", "incompat_bit40"])));
        assert_eq!(d.get("checksum").and_then(Value::as_str), Some("xxhash64"));
    }

    #[test]
    fn rejects() {
        assert!(probe(&MemSource(vec![0u8; 0x20000])).is_none());
        assert!(probe(&MemSource(vec![0u8; 100])).is_none());
        let mut img = image("", 0);
        img[0x10000 + 0x90..0x10000 + 0x94].copy_from_slice(&3000u32.to_le_bytes());
        assert!(probe(&MemSource(img)).is_none());
    }
}
