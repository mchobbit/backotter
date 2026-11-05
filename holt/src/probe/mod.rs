//! What a block device holds, read from its superblocks: the signatures
//! blkid knows, for the formats a Debian 13 system can be installed on or
//! commonly carries.

pub mod bcachefs;
pub mod bfs;
pub mod btrfs;
pub mod ext;
pub mod f2fs;
pub mod nilfs2;
pub mod redoxfs;
pub mod ufs;
pub mod fat;
pub mod hammer2;
pub mod luks;
pub mod lvm;
pub mod md;
pub mod misc;
pub mod xfs;
pub mod zfs;

use crate::bytes::ByteSource;
use crate::json::{Obj, Value};

#[derive(Debug, Clone)]
pub struct Content {
    /// blkid's TYPE: ext4, xfs, vfat, swap, crypto_LUKS, LVM2_member, ...
    pub kind: String,
    /// blkid's USAGE: filesystem, raid, crypto, other.
    pub usage: &'static str,
    pub uuid: Option<String>,
    pub label: Option<String>,
    pub version: Option<String>,
    pub details: Obj,
    /// Data for the report's own sections rather than this device's entry:
    /// an LVM PV's volume group, a ZFS vdev's pool configuration.
    pub extra: Option<Value>,
}

impl Content {
    pub fn to_json(&self) -> Value {
        let mut o = Obj::new()
            .set("type", self.kind.as_str())
            .set("usage", self.usage)
            .set("uuid", self.uuid.clone())
            .set("label", self.label.clone())
            .set("version", self.version.clone());
        for (k, v) in &self.details.0 {
            o.put(k, v.clone());
        }
        o.build()
    }
}

type Prober = fn(&dyn ByteSource) -> Option<Content>;

/// In blkid's spirit: RAID and container signatures first (an md 1.0
/// member also shows the filesystem inside it at its start), then
/// crypto, volume managers, filesystems, swap.
const PROBERS: &[Prober] = &[
    md::probe,
    luks::probe,
    lvm::probe,
    zfs::probe,
    xfs::probe,
    ext::probe,
    btrfs::probe,
    bcachefs::probe,
    f2fs::probe,
    nilfs2::probe,
    misc::jfs,
    ufs::probe,
    hammer2::probe,
    bfs::probe,
    redoxfs::probe,
    fat::probe,
    misc::iso9660,
    misc::swap,
    xfs::external_log,
];

/// Every signature on the device, most significant first.
pub fn probe_all(src: &dyn ByteSource) -> Vec<Content> {
    if src.size() == 0 {
        return Vec::new();
    }
    PROBERS.iter().filter_map(|p| p(src)).collect()
}

/// The device's content for the report: its main signature, and the names
/// of any others found (stale signatures, or an md 1.0 member's filesystem).
pub fn content_json(found: &[Content]) -> Value {
    match found.split_first() {
        None => Value::Null,
        Some((first, rest)) => {
            let mut v = first.to_json();
            if !rest.is_empty() {
                if let Value::Obj(o) = &mut v {
                    o.push(("other_signatures".into(), Value::from(rest.iter().map(|c| c.kind.clone()).collect::<Vec<_>>())));
                }
            }
            v
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::bytes::MemSource;

    #[test]
    fn nothing_on_blank_or_empty() {
        assert!(probe_all(&MemSource(vec![0u8; 1 << 20])).is_empty());
        assert!(probe_all(&MemSource(vec![])).is_empty());
        assert_eq!(content_json(&[]), Value::Null);
    }

    #[test]
    fn each_sample_is_found_alone() {
        let cases: Vec<(&str, Vec<u8>)> = vec![
            ("ext4", ext::sample::image(0x4, 0x40, 0, "r")),
            ("xfs", xfs::sample::image("x")),
            ("btrfs", btrfs::sample::image("b", 0)),
            ("f2fs", f2fs::sample::image("f", 0)),
            ("bcachefs", bcachefs::sample::image("c", 0)),
            ("ufs", ufs::sample::image("u")),
            ("hammer2", hammer2::sample::image()),
            ("befs", bfs::sample::image("Haiku")),
            ("redoxfs", redoxfs::sample::image()),
            ("nilfs2", nilfs2::sample::image("n", 0)),
            ("vfat", fat::sample::fat32(1 << 20, 1, "EFI")),
            ("swap", misc::sample::swap(64, "")),
            ("crypto_LUKS", luks::sample::luks2("u", "")),
            ("LVM2_member", lvm::sample::pv(512, 1 << 20)),
            ("linux_raid_member", md::sample::v1(1 << 20, 4096, "h:0", 1, 0, 0)),
        ];
        for (want, img) in cases {
            let found = probe_all(&MemSource(img));
            let kinds: Vec<&str> = found.iter().map(|c| c.kind.as_str()).collect();
            assert_eq!(kinds, vec![want]);
            let j = content_json(&found);
            assert_eq!(j.get("type").and_then(Value::as_str), Some(want));
            assert!(j.get("other_signatures").is_none());
        }
    }

    #[test]
    fn md10_member_shows_its_filesystem_too() {
        // An ESP mirrored with md metadata 1.0: FAT at the start, md at the end.
        let mut img = fat::sample::fat32(65536 * 2, 7, "EFI");
        let size = img.len();
        let off = ((size / 512 - 16) & !7) * 512;
        let sb = md::sample::v1(size, off, "h:esp", 1, 0, 0);
        img[off..off + 4096].copy_from_slice(&sb[off..off + 4096]);
        let found = probe_all(&MemSource(img));
        assert_eq!(found.iter().map(|c| c.kind.as_str()).collect::<Vec<_>>(), vec!["linux_raid_member", "vfat"]);
        let j = content_json(&found);
        assert_eq!(j.get("version").and_then(Value::as_str), Some("1.0"));
        assert_eq!(j.get("other_signatures"), Some(&Value::from(vec!["vfat"])));
    }

    #[test]
    fn content_json_fields() {
        let found = probe_all(&MemSource(ext::sample::image(0x4, 0x40, 0, "root")));
        let j = content_json(&found);
        assert_eq!(j.get("usage").and_then(Value::as_str), Some("filesystem"));
        assert_eq!(j.get("label").and_then(Value::as_str), Some("root"));
        assert!(j.get("features").is_some());
        let keys: Vec<&str> = j.as_object().unwrap().iter().map(|(k, _)| k.as_str()).take(5).collect();
        assert_eq!(keys, vec!["type", "usage", "uuid", "label", "version"]);
    }
}
