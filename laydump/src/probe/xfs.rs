//! XFS superblocks: geometry and the features mkfs.xfs would need to make the
//! same filesystem again.

use super::Content;
use crate::bytes::{be16, be32, be64, sub, u8_at, uuid, ByteSource};
use crate::json::Obj;

const VERSION_BITS: &[(u16, &str)] = &[
    (0x0010, "attr"),
    (0x0020, "nlink"),
    (0x0040, "quota"),
    (0x0080, "align"),
    (0x0100, "dalign"),
    (0x0200, "shared"),
    (0x0400, "logv2"),
    (0x0800, "sector"),
    (0x1000, "extflg"),
    (0x2000, "dirv2"),
    (0x4000, "asciici"),
    (0x8000, "morebits"),
];
const FEATURES2: &[(u32, &str)] = &[
    (0x0002, "lazysbcount"),
    (0x0008, "attr2"),
    (0x0010, "parent"),
    (0x0080, "projid32bit"),
    (0x0100, "crc"),
    (0x0200, "ftype"),
];
const RO_COMPAT: &[(u32, &str)] = &[(0x1, "finobt"), (0x2, "rmapbt"), (0x4, "reflink"), (0x8, "inobtcount")];
const INCOMPAT: &[(u32, &str)] = &[
    (0x01, "ftype"),
    (0x02, "sparse"),
    (0x04, "meta_uuid"),
    (0x08, "bigtime"),
    (0x10, "needsrepair"),
    (0x20, "nrext64"),
    (0x40, "exchange"),
    (0x80, "parent"),
    (0x100, "metadir"),
];
const LOG_INCOMPAT: &[(u32, &str)] = &[(0x1, "xattrs")];

fn bits<T: Copy + Into<u64>>(mask: T, table: &[(T, &str)], prefix: &str, out: &mut Vec<String>) {
    let m: u64 = mask.into();
    for bit in 0..32 {
        if m & (1 << bit) == 0 {
            continue;
        }
        match table.iter().find(|(v, _)| (*v).into() == 1u64 << bit) {
            Some((_, n)) => out.push(n.to_string()),
            None => out.push(format!("{prefix}_bit{bit}")),
        }
    }
}

pub fn probe(src: &dyn ByteSource) -> Option<Content> {
    let b = src.read_exact_at(0, 512)?;
    if sub(&b, 0, 4) != b"XFSB" {
        return None;
    }
    let block_size = be32(&b, 4) as u64;
    let sect = be16(&b, 102);
    if !(512..=65536).contains(&block_size) || !block_size.is_power_of_two() || !(512..=32768).contains(&sect) {
        return None;
    }
    let versionnum = be16(&b, 100);
    let v5 = versionnum & 0xf == 5;
    let mut version_flags = Vec::new();
    bits(versionnum & 0xfff0, VERSION_BITS, "version", &mut version_flags);
    let f2 = be32(&b, 200);
    let mut features = Vec::new();
    bits(f2, FEATURES2, "features2", &mut features);
    let (ro, inc, logi) = if v5 { (be32(&b, 212), be32(&b, 216), be32(&b, 220)) } else { (0, 0, 0) };
    bits(ro, RO_COMPAT, "ro_compat", &mut features);
    bits(inc, INCOMPAT, "incompat", &mut features);
    let mut log_features = Vec::new();
    bits(logi, LOG_INCOMPAT, "log_incompat", &mut log_features);
    let mut seen = std::collections::HashSet::new();
    features.retain(|f| seen.insert(f.clone()));
    let dblocks = be64(&b, 8);
    let logstart = be64(&b, 48);
    let mut d = Obj::new()
        .set("format_version", versionnum & 0xf)
        .set("block_size", block_size)
        .set("blocks", dblocks)
        .set("size_bytes", dblocks * block_size)
        .set("sector_size", sect)
        .set("inode_size", be16(&b, 104))
        .set("ag_count", be32(&b, 88))
        .set("ag_blocks", be32(&b, 84))
        .set("log_internal", logstart != 0)
        .set("log_blocks", be32(&b, 96))
        .set("log_sector_size", be16(&b, 194))
        .set("log_stripe_unit", be32(&b, 196))
        .set("stripe_unit", be32(&b, 184))
        .set("stripe_width", be32(&b, 188))
        .set("dir_block_size", block_size << u8_at(&b, 192).min(16))
        .set("imax_pct", u8_at(&b, 127))
        .set("realtime_blocks", be64(&b, 16))
        .set("realtime_extent_size", be32(&b, 80))
        .set("inodes", be64(&b, 128))
        .set("free_inodes", be64(&b, 136))
        .set("free_blocks", be64(&b, 144))
        .set("version_flags", version_flags)
        .set("features", features)
        .set("log_incompat_features", log_features);
    if v5 && inc & 0x04 != 0 {
        d.put("meta_uuid", uuid(sub(&b, 248, 16)));
    }
    if be16(&b, 176) != 0 {
        d.put("quota_flags", format!("0x{:04x}", be16(&b, 176)));
    }
    Some(Content {
        kind: "xfs".into(),
        usage: "filesystem",
        uuid: Some(uuid(sub(&b, 32, 16))),
        label: crate::bytes::label(sub(&b, 108, 12)),
        version: Some(format!("{}", versionnum & 0xf)),
        details: d,
        extra: None,
    })
}

#[cfg(test)]
pub mod sample {
    /// A v5 XFS superblock as mkfs.xfs 6.x makes it (crc, finobt, sparse,
    /// rmapbt, reflink, bigtime, inobtcount, nrext64; internal log).
    pub fn image(label: &str) -> Vec<u8> {
        let mut s = vec![0u8; 65536];
        s[0..4].copy_from_slice(b"XFSB");
        s[4..8].copy_from_slice(&4096u32.to_be_bytes());
        s[8..16].copy_from_slice(&262144u64.to_be_bytes());
        for (i, b) in s[32..48].iter_mut().enumerate() {
            *b = 0xa0 + i as u8;
        }
        s[48..56].copy_from_slice(&131076u64.to_be_bytes());
        s[84..88].copy_from_slice(&65536u32.to_be_bytes());
        s[88..92].copy_from_slice(&4u32.to_be_bytes());
        s[96..100].copy_from_slice(&16384u32.to_be_bytes());
        s[100..102].copy_from_slice(&0xb4a5u16.to_be_bytes());
        s[102..104].copy_from_slice(&512u16.to_be_bytes());
        s[104..106].copy_from_slice(&512u16.to_be_bytes());
        s[108..108 + label.len()].copy_from_slice(label.as_bytes());
        s[127] = 25;
        s[194..196].copy_from_slice(&512u16.to_be_bytes());
        s[200..204].copy_from_slice(&0x18au32.to_be_bytes());
        s[212..216].copy_from_slice(&0xfu32.to_be_bytes());
        s[216..220].copy_from_slice(&0x2bu32.to_be_bytes());
        s
    }
}

#[cfg(test)]
mod tests {
    use super::sample::image;
    use super::*;
    use crate::bytes::MemSource;
    use crate::json::Value;

    #[test]
    fn v5_defaults() {
        let c = probe(&MemSource(image("data"))).unwrap();
        assert_eq!(c.kind, "xfs");
        assert_eq!(c.label.as_deref(), Some("data"));
        assert_eq!(c.uuid.as_deref(), Some("a0a1a2a3-a4a5-a6a7-a8a9-aaabacadaeaf"));
        assert_eq!(c.version.as_deref(), Some("5"));
        let d = c.details.build();
        assert_eq!(
            d.get("features"),
            Some(&Value::from(vec![
                "lazysbcount", "attr2", "projid32bit", "crc", "finobt", "rmapbt", "reflink", "inobtcount", "ftype",
                "sparse", "bigtime", "nrext64"
            ]))
        );
        let f: Vec<&str> = d.get("features").unwrap().as_array().unwrap().iter().filter_map(Value::as_str).collect();
        for want in ["crc", "finobt", "sparse", "rmapbt", "reflink", "bigtime", "inobtcount", "nrext64", "ftype"] {
            assert!(f.contains(&want), "{want} missing from {f:?}");
        }
        assert_eq!(d.get("ag_count").and_then(Value::as_u64), Some(4));
        assert_eq!(d.get("size_bytes").and_then(Value::as_u64), Some(262144 * 4096));
        assert_eq!(d.get("log_internal"), Some(&Value::Bool(true)));
        assert_eq!(d.get("imax_pct").and_then(Value::as_u64), Some(25));
        let vf: Vec<&str> = d.get("version_flags").unwrap().as_array().unwrap().iter().filter_map(Value::as_str).collect();
        assert_eq!(vf, vec!["nlink", "align", "logv2", "extflg", "dirv2", "morebits"]);
        assert!(d.get("meta_uuid").is_none());
    }

    #[test]
    fn v4_ignores_v5_fields() {
        let mut img = image("");
        img[100..102].copy_from_slice(&0xb4a4u16.to_be_bytes());
        img[200..204].copy_from_slice(&0x8u32.to_be_bytes());
        let c = probe(&MemSource(img)).unwrap();
        assert_eq!(c.version.as_deref(), Some("4"));
        assert_eq!(c.label, None);
        let d = c.details.build();
        assert_eq!(d.get("features"), Some(&Value::from(vec!["attr2"])));
    }

    #[test]
    fn meta_uuid_unknown_bits_and_quota() {
        let mut img = image("x");
        img[216..220].copy_from_slice(&(0x04u32 | 0x8000).to_be_bytes());
        img[248] = 0x77;
        img[176..178].copy_from_slice(&0x0005u16.to_be_bytes());
        let d = probe(&MemSource(img)).unwrap().details.build();
        assert!(d.get("meta_uuid").unwrap().as_str().unwrap().starts_with("77"));
        let f: Vec<&str> = d.get("features").unwrap().as_array().unwrap().iter().filter_map(Value::as_str).collect();
        assert!(f.contains(&"incompat_bit15"));
        assert_eq!(d.get("quota_flags").and_then(Value::as_str), Some("0x0005"));
    }

    #[test]
    fn rejects() {
        assert!(probe(&MemSource(vec![0u8; 4096])).is_none());
        let mut img = image("");
        img[4..8].copy_from_slice(&3000u32.to_be_bytes());
        assert!(probe(&MemSource(img)).is_none());
        let mut img = image("");
        img[102..104].copy_from_slice(&100u16.to_be_bytes());
        assert!(probe(&MemSource(img)).is_none());
        assert!(probe(&MemSource(b"XFSB".to_vec())).is_none());
    }
}
