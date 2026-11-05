//! Linux md RAID member superblocks: 1.x (at the start, 4 KiB in, or the
//! end) and 0.90 (at the end).

use super::Content;
use crate::bytes::{hex, le16, le32, le64, sub, uuid, ByteSource};
use crate::json::Obj;

const MAGIC: u32 = 0xa92b_4efc;

pub fn level_name(level: i32) -> String {
    match level {
        -4 => "multipath".into(),
        -1 => "linear".into(),
        -5 => "faulty".into(),
        n => format!("raid{n}"),
    }
}

fn mdadm_uuid(b: &[u8]) -> String {
    let h = hex(b);
    format!("{}:{}:{}:{}", &h[0..8], &h[8..16], &h[16..24], &h[24..32])
}

/// Where each superblock format puts itself on a device of `size` bytes:
/// (version, offset).
fn locations(size: u64) -> Vec<(&'static str, u64)> {
    let sectors = size / 512;
    let mut v = vec![("1.1", 0), ("1.2", 4096)];
    if sectors >= 16 {
        v.push(("1.0", ((sectors - 16) & !7) * 512));
    }
    if sectors >= 128 {
        v.push(("0.90", ((sectors & !127) - 128) * 512));
    }
    v
}

pub fn probe(src: &dyn ByteSource) -> Option<Content> {
    let size = src.size();
    for (version, off) in locations(size) {
        if off >= size {
            continue;
        }
        let Some(b) = src.read_exact_at(off, 4096.min((size - off) as usize)) else { continue };
        if le32(&b, 0) != MAGIC {
            continue;
        }
        if version == "0.90" {
            if le32(&b, 4) == 0 {
                return Some(v090(&b));
            }
            continue;
        }
        // A 1.x superblock records its own offset (in sectors); one found
        // at the wrong place is a stale copy or another format's data.
        if le32(&b, 4) != 1 || le64(&b, 144) * 512 != off {
            continue;
        }
        return Some(v1(&b, version));
    }
    None
}

fn v1(b: &[u8], version: &str) -> Content {
    let level = le32(b, 72) as i32;
    let dev_number = le32(b, 160);
    let max_dev = le32(b, 220);
    let role = if dev_number < max_dev.min(1920) { Some(le16(b, 256 + 2 * dev_number as usize)) } else { None };
    let role_text = match role {
        Some(0xffff) => "spare".to_string(),
        Some(0xfffe) => "faulty".to_string(),
        Some(0xfffd) => "journal".to_string(),
        Some(n) => format!("active {n}"),
        None => "unknown".to_string(),
    };
    let feature_map = le32(b, 8);
    let mut d = Obj::new()
        .set("metadata", version)
        .set("level", level_name(level))
        .set("raid_disks", le32(b, 92))
        .set("layout", le32(b, 76))
        .set("chunk_size", le32(b, 88) as u64 * 512)
        .set("component_size_bytes", le64(b, 80) * 512)
        .set("data_offset_bytes", le64(b, 128) * 512)
        .set("data_size_bytes", le64(b, 136) * 512)
        .set("super_offset_bytes", le64(b, 144) * 512)
        .set("mdadm_uuid", mdadm_uuid(sub(b, 16, 16)))
        .set("device_uuid", uuid(sub(b, 168, 16)))
        .set("device_number", dev_number)
        .set("device_role", role_text)
        .set("events", le64(b, 200))
        .set("creation_time", le64(b, 64) & 0xff_ffff_ffff)
        .set("update_time", le64(b, 192) & 0xff_ffff_ffff)
        .set("feature_map", format!("0x{feature_map:x}"))
        .set("write_intent_bitmap", feature_map & 1 != 0)
        .set("ppl", feature_map & 0x400 != 0)
        // Newer kernels (Linux 6.18 on) keep the array's logical block size
        // here, where older ones have padding that must be zero.
        .set("logical_block_size", le32(b, 224));
    if feature_map & 1 != 0 {
        d.put("bitmap_offset_sectors", le32(b, 96) as i32);
    }
    Content {
        kind: "linux_raid_member".into(),
        usage: "raid",
        uuid: Some(uuid(sub(b, 16, 16))),
        // "homehost:name"; mdadm shows the part after the colon as the name.
        label: crate::bytes::label(sub(b, 32, 32)),
        version: Some(version.into()),
        details: d,
        extra: None,
    }
}

fn v090(b: &[u8]) -> Content {
    let mut raw = Vec::with_capacity(16);
    raw.extend_from_slice(sub(b, 20, 4));
    raw.extend_from_slice(sub(b, 52, 12));
    let d = Obj::new()
        .set("metadata", "0.90")
        .set("level", level_name(le32(b, 28) as i32))
        .set("raid_disks", le32(b, 40))
        .set("component_size_bytes", le32(b, 32) as u64 * 1024)
        .set("md_minor", le32(b, 44))
        .set("layout", le32(b, 256))
        .set("chunk_size", le32(b, 260))
        .set("mdadm_uuid", format!("{:08x}:{:08x}:{:08x}:{:08x}", le32(b, 20), le32(b, 52), le32(b, 56), le32(b, 60)))
        .set("creation_time", le32(b, 24))
        .set("update_time", le32(b, 128));
    Content {
        kind: "linux_raid_member".into(),
        usage: "raid",
        uuid: Some(uuid(&raw)),
        label: None,
        version: Some("0.90".into()),
        details: d,
        extra: None,
    }
}

#[cfg(test)]
pub mod sample {
    /// A 1.x member superblock at `off` of a `size`-byte device.
    pub fn v1(size: usize, off: usize, name: &str, level: i32, dev_number: u32, role: u16) -> Vec<u8> {
        let mut img = vec![0u8; size];
        let b = &mut img[off..off + 4096];
        b[0..4].copy_from_slice(&0xa92b_4efcu32.to_le_bytes());
        b[4..8].copy_from_slice(&1u32.to_le_bytes());
        b[8..12].copy_from_slice(&1u32.to_le_bytes()); // bitmap
        for (i, x) in b[16..32].iter_mut().enumerate() {
            *x = 0x30 + i as u8;
        }
        b[32..32 + name.len()].copy_from_slice(name.as_bytes());
        b[72..76].copy_from_slice(&level.to_le_bytes());
        b[80..88].copy_from_slice(&((size as u64 / 512).saturating_sub(4096)).to_le_bytes());
        b[88..92].copy_from_slice(&1024u32.to_le_bytes());
        b[92..96].copy_from_slice(&2u32.to_le_bytes());
        b[96..100].copy_from_slice(&8u32.to_le_bytes());
        b[128..136].copy_from_slice(&2048u64.to_le_bytes());
        b[144..152].copy_from_slice(&((off / 512) as u64).to_le_bytes());
        b[160..164].copy_from_slice(&dev_number.to_le_bytes());
        b[168] = 0x99;
        b[200..208].copy_from_slice(&17u64.to_le_bytes());
        b[220..224].copy_from_slice(&2u32.to_le_bytes());
        let r = 256 + 2 * dev_number as usize;
        b[r..r + 2].copy_from_slice(&role.to_le_bytes());
        img
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::bytes::MemSource;
    use crate::json::Value;

    #[test]
    fn v12_member() {
        let c = probe(&MemSource(sample::v1(1 << 20, 4096, "cinqcent:0", 1, 1, 1))).unwrap();
        assert_eq!(c.kind, "linux_raid_member");
        assert_eq!(c.version.as_deref(), Some("1.2"));
        assert_eq!(c.uuid.as_deref(), Some("30313233-3435-3637-3839-3a3b3c3d3e3f"));
        assert_eq!(c.label.as_deref(), Some("cinqcent:0"));
        let d = c.details.build();
        assert_eq!(d.get("level").and_then(Value::as_str), Some("raid1"));
        assert_eq!(d.get("mdadm_uuid").and_then(Value::as_str), Some("30313233:34353637:38393a3b:3c3d3e3f"));
        assert_eq!(d.get("device_role").and_then(Value::as_str), Some("active 1"));
        assert_eq!(d.get("chunk_size").and_then(Value::as_u64), Some(512 * 1024));
        assert_eq!(d.get("data_offset_bytes").and_then(Value::as_u64), Some(1 << 20));
        assert_eq!(d.get("write_intent_bitmap"), Some(&Value::Bool(true)));
        assert_eq!(d.get("bitmap_offset_sectors").and_then(Value::as_i64), Some(8));
        assert_eq!(d.get("events").and_then(Value::as_u64), Some(17));
    }

    #[test]
    fn v11_and_v10() {
        let c = probe(&MemSource(sample::v1(1 << 20, 0, "x", 0, 0, 0xffff))).unwrap();
        assert_eq!(c.version.as_deref(), Some("1.1"));
        assert_eq!(c.details.build().get("device_role").and_then(Value::as_str), Some("spare"));
        let size = (1 << 20) + 3 * 512;
        let off = ((size / 512 - 16) & !7) * 512;
        let c = probe(&MemSource(sample::v1(size, off, "esp", 1, 0, 0xfffe))).unwrap();
        assert_eq!(c.version.as_deref(), Some("1.0"));
        assert_eq!(c.details.build().get("device_role").and_then(Value::as_str), Some("faulty"));
    }

    #[test]
    fn stale_superblock_at_wrong_offset_is_ignored() {
        // A 1.2 superblock that says it lives at sector 0 isn't a 1.2 one.
        let mut img = sample::v1(1 << 20, 4096, "x", 1, 0, 0);
        img[4096 + 144..4096 + 152].copy_from_slice(&0u64.to_le_bytes());
        assert!(probe(&MemSource(img)).is_none());
        let mut img = sample::v1(1 << 20, 4096, "x", 1, 0, 0);
        img[4096 + 4] = 2; // major version 2 doesn't exist
        assert!(probe(&MemSource(img)).is_none());
        assert!(probe(&MemSource(vec![0u8; 1 << 16])).is_none());
        assert!(probe(&MemSource(vec![0u8; 100])).is_none());
    }

    #[test]
    fn v090_member() {
        let size = 1 << 20;
        let off = (((size / 512) & !127) - 128) * 512;
        let mut img = vec![0u8; size];
        let b = &mut img[off..off + 4096];
        b[0..4].copy_from_slice(&MAGIC.to_le_bytes());
        b[8..12].copy_from_slice(&90u32.to_le_bytes());
        b[20..24].copy_from_slice(&0x0102_0304u32.to_le_bytes());
        b[28..32].copy_from_slice(&5u32.to_le_bytes());
        b[32..36].copy_from_slice(&960u32.to_le_bytes());
        b[40..44].copy_from_slice(&3u32.to_le_bytes());
        b[52..56].copy_from_slice(&0x0506_0708u32.to_le_bytes());
        b[256..260].copy_from_slice(&2u32.to_le_bytes());
        b[260..264].copy_from_slice(&65536u32.to_le_bytes());
        let c = probe(&MemSource(img)).unwrap();
        assert_eq!(c.version.as_deref(), Some("0.90"));
        assert_eq!(c.uuid.as_deref(), Some("04030201-0807-0605-0000-000000000000"));
        let d = c.details.build();
        assert_eq!(d.get("level").and_then(Value::as_str), Some("raid5"));
        assert_eq!(d.get("mdadm_uuid").and_then(Value::as_str), Some("01020304:05060708:00000000:00000000"));
        assert_eq!(d.get("component_size_bytes").and_then(Value::as_u64), Some(960 * 1024));
        assert_eq!(d.get("chunk_size").and_then(Value::as_u64), Some(65536));
    }

    #[test]
    fn level_names() {
        assert_eq!(level_name(-1), "linear");
        assert_eq!(level_name(-4), "multipath");
        assert_eq!(level_name(-5), "faulty");
        assert_eq!(level_name(10), "raid10");
        assert_eq!(level_name(0), "raid0");
    }

    #[test]
    fn location_math() {
        let l = locations(1 << 20);
        assert_eq!(l[2], ("1.0", ((2048 - 16) & !7) * 512));
        assert_eq!(l[3], ("0.90", (2048 - 128) * 512));
        assert_eq!(locations(8192).len(), 3); // 16 sectors: room for 1.0
        assert_eq!(locations(4096).len(), 2);
        assert_eq!(locations(0).len(), 2);
    }
}
