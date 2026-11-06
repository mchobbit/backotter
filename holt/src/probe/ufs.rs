//! UFS (FreeBSD, DragonFly's /boot) and FFS (NetBSD, OpenBSD): the
//! superblock, at 64 KiB (UFS2), 8 KiB (UFS1), or 0 / 256 KiB, as blkid
//! looks for it.  newfs's parameters themselves come from the system's own
//! dumpfs (`other`); this names the filesystem and tells its version.

use super::Content;
use crate::bytes::{label, le32, sub, ByteSource};
use crate::json::Obj;

const UFS1_MAGIC: u32 = 0x0001_1954;
const UFS2_MAGIC: u32 = 0x1954_0119;
/// NetBSD's FFSv2 with extended attributes (FS_UFS2EA_MAGIC).
const UFS2EA_MAGIC: u32 = 0x1901_2038;
/// Where struct fs keeps its magic number.
const MAGIC_AT: usize = 1372;

pub fn probe(src: &dyn ByteSource) -> Option<Content> {
    for off in [65536u64, 8192, 0, 262144] {
        let Some(b) = src.read_exact_at(off, 1536) else { continue };
        let magic = le32(&b, MAGIC_AT);
        let version = match magic {
            UFS2_MAGIC | UFS2EA_MAGIC => 2,
            UFS1_MAGIC => 1,
            _ => continue,
        };
        // UFS2 lives at 64 KiB; a UFS1 magic there is a stale backup.
        if (version == 2) != (off == 65536 || off == 262144) {
            continue;
        }
        let bsize = le32(&b, 48);
        let fsize = le32(&b, 52);
        if !(4096..=65536).contains(&bsize) || fsize == 0 || bsize % fsize != 0 {
            continue;
        }
        let d = Obj::new()
            .set("superblock_offset", off)
            .set("block_size", bsize)
            .set("fragment_size", fsize)
            .set("cylinder_groups", le32(&b, 44))
            .set("inodes_per_group", le32(&b, 184))
            .set("minfree", le32(&b, 60))
            .set("optimization", if le32(&b, 128) == 1 { "space" } else { "time" })
            .set("extended_attributes", magic == UFS2EA_MAGIC)
            .set("last_mounted_on", crate::bytes::cstr(sub(&b, 212, 468)));
        return Some(Content {
            kind: "ufs".into(),
            usage: "filesystem",
            // blkid's UUID for UFS: fs_id, as two 32-bit words in hex.
            uuid: Some(format!("{:08x}{:08x}", le32(&b, 144), le32(&b, 148))),
            label: if version == 2 { label(sub(&b, 680, 32)) } else { None },
            version: Some(if version == 2 { "2" } else { "1" }.into()),
            details: d,
            extra: None,
        });
    }
    None
}

#[cfg(test)]
pub mod sample {
    /// A UFS2 superblock at 64 KiB in a 128 KiB image: 32 KiB blocks, 4 KiB
    /// fragments.
    pub fn image(volname: &str) -> Vec<u8> {
        let mut img = vec![0u8; 131072];
        let s = &mut img[65536..65536 + 1536];
        s[44..48].copy_from_slice(&4u32.to_le_bytes());
        s[48..52].copy_from_slice(&32768u32.to_le_bytes());
        s[52..56].copy_from_slice(&4096u32.to_le_bytes());
        s[144..148].copy_from_slice(&0x6a1b2c3du32.to_le_bytes());
        s[148..152].copy_from_slice(&0x01020304u32.to_le_bytes());
        s[184..188].copy_from_slice(&1024u32.to_le_bytes());
        s[212..213].copy_from_slice(b"/");
        s[680..680 + volname.len()].copy_from_slice(volname.as_bytes());
        s[super::MAGIC_AT..super::MAGIC_AT + 4].copy_from_slice(&super::UFS2_MAGIC.to_le_bytes());
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
        let c = probe(&MemSource(sample::image("rootfs"))).unwrap();
        assert_eq!(c.kind, "ufs");
        assert_eq!(c.version.as_deref(), Some("2"));
        assert_eq!(c.label.as_deref(), Some("rootfs"));
        assert_eq!(c.uuid.as_deref(), Some("6a1b2c3d01020304"));
        let d = c.details.build();
        assert_eq!(d.get("block_size").and_then(Value::as_u64), Some(32768));
        assert_eq!(d.get("last_mounted_on").and_then(Value::as_str), Some("/"));
        assert!(probe(&MemSource(sample::image(""))).unwrap().label.is_none());
        assert!(probe(&MemSource(vec![0u8; 131072])).is_none());
    }
}
