//! Swap areas (and hibernation images in them), ISO9660 and JFS.

use super::Content;
use crate::bytes::{le32, le64, sub, uuid, ByteSource};
use crate::json::Obj;

/// Page sizes a Linux swap header may be built for.
const PAGE_SIZES: &[u64] = &[4096, 8192, 16384, 65536];

pub fn swap(src: &dyn ByteSource) -> Option<Content> {
    for &ps in PAGE_SIZES {
        let Some(magic) = src.read_exact_at(ps - 10, 10) else { continue };
        // v1 headers ("SWAPSPACE2", or a hibernation image's signature in
        // its place) have a UUID and label; the ancient v0 has neither.
        let (v1, hibernation) = match &magic[..] {
            b"SWAPSPACE2" => (true, None),
            b"SWAP-SPACE" => (false, None),
            m if m.starts_with(b"S1SUSPEND") => (true, Some("S1SUSPEND")),
            m if m.starts_with(b"S2SUSPEND") => (true, Some("S2SUSPEND")),
            m if m.starts_with(b"ULSUSPEND") => (true, Some("ULSUSPEND")),
            m if m.starts_with(b"LINHIB0001") => (true, Some("LINHIB0001")),
            _ => continue,
        };
        let h = src.read_at(1024, 512);
        // blkid's VERSION is the header's own version field (1), not the
        // "2" in the magic.
        let version = if v1 { le32(&h, 0).to_string() } else { "0".to_string() };
        let last_page = le32(&h, 4) as u64;
        let mut d = Obj::new().set("page_size", ps).set("last_page", last_page).set("size_bytes", (last_page + 1) * ps);
        d.put("bad_pages", le32(&h, 8));
        d.put_some("hibernation_image", hibernation);
        let uuid_bytes = sub(&h, 12, 16);
        return Some(Content {
            kind: "swap".into(),
            usage: "other",
            uuid: (v1 && uuid_bytes.iter().any(|&b| b != 0)).then(|| uuid(uuid_bytes)),
            label: if v1 { crate::bytes::label(sub(&h, 28, 16)) } else { None },
            version: Some(version),
            details: d,
            extra: None,
        });
    }
    None
}

pub fn iso9660(src: &dyn ByteSource) -> Option<Content> {
    let b = src.read_exact_at(0x8000, 2048)?;
    if sub(&b, 1, 5) != b"CD001" || b[0] != 1 {
        return None;
    }
    let blocks = le32(&b, 80) as u64;
    let block_size = crate::bytes::le16(&b, 128) as u64;
    let d = Obj::new()
        .set("system_id", crate::bytes::label(sub(&b, 8, 32)))
        .set("publisher", crate::bytes::label(sub(&b, 318, 128)))
        .set("size_bytes", blocks * block_size)
        .set("creation_time", crate::bytes::label(sub(&b, 813, 16)));
    // blkid's UUID for ISO9660 is the creation time, digits only.
    let created = String::from_utf8_lossy(sub(&b, 813, 16)).to_string();
    let uuid = (created.len() == 16 && created.bytes().all(|c| c.is_ascii_digit()) && created != "0000000000000000")
        .then(|| format!("{}-{}-{}-{}-{}-{}-{}", &created[0..4], &created[4..6], &created[6..8], &created[8..10], &created[10..12], &created[12..14], &created[14..16]));
    Some(Content {
        kind: "iso9660".into(),
        usage: "filesystem",
        uuid,
        label: crate::bytes::label(sub(&b, 40, 32)),
        version: None,
        details: d,
        extra: None,
    })
}

pub fn jfs(src: &dyn ByteSource) -> Option<Content> {
    let b = src.read_exact_at(0x8000, 256)?;
    if sub(&b, 0, 4) != b"JFS1" {
        return None;
    }
    let version = le32(&b, 4);
    let bsize = le32(&b, 16) as u64;
    let label = if version == 1 { crate::bytes::label(sub(&b, 101, 11)) } else { crate::bytes::label(sub(&b, 152, 16)) };
    let d = Obj::new()
        .set("block_size", bsize)
        .set("size_bytes", le64(&b, 8) * le32(&b, 24) as u64)
        .set("external_log", le32(&b, 36) & 0x0000_0800 == 0 && le32(&b, 64) != 0);
    Some(Content {
        kind: "jfs".into(),
        usage: "filesystem",
        uuid: Some(uuid(sub(&b, 136, 16))),
        label,
        version: Some(version.to_string()),
        details: d,
        extra: None,
    })
}

#[cfg(test)]
pub mod sample {
    pub fn swap(pages: u32, label: &str) -> Vec<u8> {
        let mut img = vec![0u8; pages as usize * 4096];
        img[1024..1028].copy_from_slice(&1u32.to_le_bytes());
        img[1028..1032].copy_from_slice(&(pages - 1).to_le_bytes());
        for (i, b) in img[1036..1052].iter_mut().enumerate() {
            *b = 0xc0 + i as u8;
        }
        img[1052..1052 + label.len()].copy_from_slice(label.as_bytes());
        img[4086..4096].copy_from_slice(b"SWAPSPACE2");
        img
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::bytes::MemSource;
    use crate::json::Value;

    #[test]
    fn swap_v2() {
        let c = swap(&MemSource(sample::swap(256, "swap0"))).unwrap();
        assert_eq!(c.kind, "swap");
        assert_eq!(c.version.as_deref(), Some("1"));
        assert_eq!(c.uuid.as_deref(), Some("c0c1c2c3-c4c5-c6c7-c8c9-cacbcccdcecf"));
        assert_eq!(c.label.as_deref(), Some("swap0"));
        let d = c.details.build();
        assert_eq!(d.get("size_bytes").and_then(Value::as_u64), Some(256 * 4096));
        assert!(d.get("hibernation_image").is_none());
    }

    #[test]
    fn swap_hibernated_and_big_pages() {
        let mut img = sample::swap(256, "");
        img[4086..4096].copy_from_slice(b"S1SUSPEND\0");
        let c = swap(&MemSource(img)).unwrap();
        assert_eq!(c.details.build().get("hibernation_image").and_then(Value::as_str), Some("S1SUSPEND"));
        let mut img = vec![0u8; 65536 * 4];
        img[65536 - 10..65536].copy_from_slice(b"SWAPSPACE2");
        img[1028..1032].copy_from_slice(&3u32.to_le_bytes());
        let c = swap(&MemSource(img)).unwrap();
        let d = c.details.build();
        assert_eq!(d.get("page_size").and_then(Value::as_u64), Some(65536));
        assert_eq!(c.uuid, None);
        assert_eq!(c.label, None);
    }

    #[test]
    fn swap_v1_and_none() {
        let mut img = vec![0u8; 8192];
        img[4086..4096].copy_from_slice(b"SWAP-SPACE");
        img[1052..1056].copy_from_slice(b"junk");
        let c = swap(&MemSource(img)).unwrap();
        assert_eq!(c.version.as_deref(), Some("0"));
        assert_eq!(c.label, None);
        assert!(swap(&MemSource(vec![0u8; 8192])).is_none());
        assert!(swap(&MemSource(vec![0u8; 10])).is_none());
    }

    #[test]
    fn iso() {
        let mut img = vec![0u8; 0x9000];
        let b = &mut img[0x8000..0x8800];
        b[0] = 1;
        b[1..6].copy_from_slice(b"CD001");
        b[8..13].copy_from_slice(b"LINUX");
        b[40..40 + 11].copy_from_slice(b"BKO_RESCUE ");
        b[80..84].copy_from_slice(&1000u32.to_le_bytes());
        b[128..130].copy_from_slice(&2048u16.to_le_bytes());
        b[813..829].copy_from_slice(b"2026110412000000");
        let c = iso9660(&MemSource(img.clone())).unwrap();
        assert_eq!(c.label.as_deref(), Some("BKO_RESCUE"));
        assert_eq!(c.uuid.as_deref(), Some("2026-11-04-12-00-00-00"));
        assert_eq!(c.details.build().get("size_bytes").and_then(Value::as_u64), Some(2048000));
        img[0x8000 + 813] = b' ';
        assert_eq!(iso9660(&MemSource(img.clone())).unwrap().uuid, None);
        img[0x8000] = 2;
        assert!(iso9660(&MemSource(img)).is_none());
    }

    #[test]
    fn jfs_labels_by_version() {
        let mut img = vec![0u8; 0x9000];
        let b = &mut img[0x8000..0x8100];
        b[0..4].copy_from_slice(b"JFS1");
        b[4..8].copy_from_slice(&2u32.to_le_bytes());
        b[8..16].copy_from_slice(&1000u64.to_le_bytes());
        b[16..20].copy_from_slice(&4096u32.to_le_bytes());
        b[24..28].copy_from_slice(&512u32.to_le_bytes());
        b[101..104].copy_from_slice(b"old");
        b[136] = 0x42;
        b[152..155].copy_from_slice(b"new");
        let c = jfs(&MemSource(img.clone())).unwrap();
        assert_eq!(c.label.as_deref(), Some("new"));
        assert!(c.uuid.unwrap().starts_with("42"));
        assert_eq!(c.details.build().get("size_bytes").and_then(Value::as_u64), Some(512000));
        img[0x8004] = 1;
        assert_eq!(jfs(&MemSource(img)).unwrap().label.as_deref(), Some("old"));
        assert!(jfs(&MemSource(vec![0u8; 0x9000])).is_none());
    }
}
