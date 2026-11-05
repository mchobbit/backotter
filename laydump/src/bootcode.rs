//! What boot code sits in a disk's first sector (the MBR's 440 bytes of
//! code), and, for GRUB, where its core image is and whether it's there.

use crate::bytes::{contains, hex, is_zero, le64, sub, u8_at, ByteSource};
use crate::json::{Obj, Value};

#[derive(Debug, Clone, PartialEq)]
pub struct BootCode {
    /// "none", "grub2", "grub-legacy", "syslinux", "isohybrid", "windows",
    /// "unknown".
    pub kind: &'static str,
    /// GRUB 2: the LBA boot.img loads core.img's first sector from.
    pub grub_core_lba: Option<u64>,
    /// GRUB 2: the BIOS drive number it boots from (0xff: whatever the BIOS
    /// passed in).
    pub grub_boot_drive: Option<u8>,
    pub first_bytes: String,
}

/// Identifies the code in the first 440 bytes of a boot sector.
pub fn identify(sector: &[u8]) -> BootCode {
    let code = sub(sector, 0, 440);
    let first_bytes = hex(sub(code, 0, 16));
    let none = |kind| BootCode { kind, grub_core_lba: None, grub_boot_drive: None, first_bytes: first_bytes.clone() };
    if code.is_empty() || is_zero(code) {
        return none("none");
    }
    if contains(code, b"GRUB ") && contains(code, b"Geom") {
        // GRUB Legacy's stage1 keeps its version (3.2) right after the BPB;
        // GRUB 2's boot.img jumps further, past its own data at 0x5a.
        if u8_at(code, 0) == 0xeb && u8_at(code, 1) == 0x48 && u8_at(code, 0x3e) == 3 && u8_at(code, 0x3f) == 2 {
            return none("grub-legacy");
        }
        return BootCode {
            kind: "grub2",
            grub_core_lba: Some(le64(code, 0x5c)),
            grub_boot_drive: Some(u8_at(code, 0x64)),
            first_bytes,
        };
    }
    if contains(code, b"isolinux.bin missing or corrupt") {
        return none("isohybrid");
    }
    if contains(code, b"Missing operating system.") || contains(code, b"Operating system load error") {
        return none("syslinux");
    }
    if contains(code, b"Invalid partition table") && contains(code, b"Missing operating system") {
        return none("windows");
    }
    none("unknown")
}

/// Whether the sector at `lba` looks like GRUB 2's diskboot.img, the first
/// sector of core.img.
pub fn grub_core_present(src: &dyn ByteSource, lba: u64, ss: u64) -> bool {
    lba.checked_mul(ss).map(|o| src.read_at(o, 512)).is_some_and(|s| contains(&s, b"loading") && contains(&s, b"Geom"))
}

impl BootCode {
    /// JSON for a disk's boot code; `gap` is the room before the first
    /// partition, in sectors, where GRUB embeds core.img on MBR disks.
    pub fn to_json(&self, src: Option<&dyn ByteSource>, ss: u64, first_partition_lba: Option<u64>) -> Value {
        let mut o = Obj::new().set("kind", self.kind).set("first_bytes", &self.first_bytes);
        if let Some(lba) = self.grub_core_lba {
            o.put("grub_core_lba", lba);
            o.put("grub_boot_drive", self.grub_boot_drive.map(|d| format!("0x{d:02x}")));
            o.put("grub_core_present", src.map(|s| grub_core_present(s, lba, ss)));
            o.put(
                "grub_core_in",
                match first_partition_lba {
                    Some(first) if lba > 0 && lba < first => "post-mbr-gap",
                    _ => "partition",
                },
            );
        }
        if let (Some(src), Some(first)) = (src, first_partition_lba) {
            // How much of the gap after the MBR holds anything (GRUB's core,
            // or another loader's), capped at the first MiB.
            let gap = first.saturating_sub(1).min((1 << 20) / ss);
            if gap > 0 {
                let b = src.read_at(ss, (gap * ss) as usize);
                let used = b.chunks(ss as usize).filter(|c| !is_zero(c)).count();
                o.put("post_mbr_gap", Obj::new().set("sectors", first - 1).set("nonzero_sectors_in_first_mib", used));
            }
        }
        o.build()
    }
}

/// GRUB's boot sector in a partition's first sector ("grub-install --force"
/// onto a partition, or a chainloaded setup).  None unless it's GRUB or
/// syslinux: everything else's first sector is filesystem data or a FAT/NTFS
/// boot stub.
pub fn partition_boot_code(src: &dyn ByteSource) -> Option<&'static str> {
    let s = src.read_at(0, 512);
    match identify(&s).kind {
        k @ ("grub2" | "grub-legacy" | "syslinux") => Some(k),
        _ => None,
    }
}

#[cfg(test)]
pub mod samples {
    /// A sector shaped like GRUB 2's boot.img: jump to 0x65, kernel sector
    /// at 0x5c, boot drive at 0x64, and its message strings.
    pub fn grub2_boot_img(core_lba: u64) -> Vec<u8> {
        let mut s = vec![0u8; 512];
        s[0] = 0xeb;
        s[1] = 0x63;
        s[2] = 0x90;
        s[0x5c..0x64].copy_from_slice(&core_lba.to_le_bytes());
        s[0x64] = 0xff;
        s[0x65..0x70].copy_from_slice(&[0xfa, 0x90, 0x90, 0xf6, 0xc2, 0x80, 0x74, 0x05, 0xf6, 0xc2, 0x70]);
        let msg = b"GRUB \0Geom\0Hard Disk\0Read\0 Error\r\n\0";
        s[0x180..0x180 + msg.len()].copy_from_slice(msg);
        s[510] = 0x55;
        s[511] = 0xaa;
        s
    }

    pub fn grub2_diskboot_img() -> Vec<u8> {
        let mut s = vec![0u8; 512];
        s[0] = 0x52;
        let msg = b"loading\0.\0\r\n\0Geom\0Read\0 Error\0";
        s[0x100..0x100 + msg.len()].copy_from_slice(msg);
        s
    }
}

#[cfg(test)]
mod tests {
    use super::samples::*;
    use super::*;
    use crate::bytes::MemSource;

    #[test]
    fn empty_and_unknown() {
        assert_eq!(identify(&[0u8; 512]).kind, "none");
        assert_eq!(identify(&[]).kind, "none");
        let mut s = vec![0u8; 512];
        s[0] = 0x33;
        s[1] = 0xc0;
        let b = identify(&s);
        assert_eq!(b.kind, "unknown");
        assert_eq!(b.first_bytes, "33c00000000000000000000000000000");
        // The partition table and signature don't count as code.
        let mut t = vec![0u8; 512];
        t[446..].fill(0x11);
        assert_eq!(identify(&t).kind, "none");
    }

    #[test]
    fn grub2() {
        let b = identify(&grub2_boot_img(1));
        assert_eq!(b.kind, "grub2");
        assert_eq!(b.grub_core_lba, Some(1));
        assert_eq!(b.grub_boot_drive, Some(0xff));
        assert!(b.first_bytes.starts_with("eb6390"));
    }

    #[test]
    fn grub_legacy() {
        let mut s = vec![0u8; 512];
        s[0] = 0xeb;
        s[1] = 0x48;
        s[2] = 0x90;
        s[0x3e] = 3;
        s[0x3f] = 2;
        let msg = b"GRUB \0Geom\0Hard Disk\0Read\0 Error\0";
        s[0x17e..0x17e + msg.len()].copy_from_slice(msg);
        assert_eq!(identify(&s).kind, "grub-legacy");
    }

    #[test]
    fn other_loaders() {
        let with = |m: &[u8]| {
            let mut s = vec![0u8; 512];
            s[0] = 0xfa;
            s[0x100..0x100 + m.len()].copy_from_slice(m);
            identify(&s).kind
        };
        assert_eq!(with(b"Missing operating system.\r\n\0Operating system load error.\r\n"), "syslinux");
        assert_eq!(with(b"isolinux.bin missing or corrupt.\r\n"), "isohybrid");
        assert_eq!(with(b"Invalid partition table\0Error loading operating system\0Missing operating system"), "windows");
        // Strings past the 440 bytes of code are not code.
        let mut s = vec![0u8; 512];
        s[0] = 1;
        s[446..446 + 5].copy_from_slice(b"GRUB ");
        assert_eq!(identify(&s).kind, "unknown");
    }

    #[test]
    fn core_present_and_gap() {
        let mut img = vec![0u8; 512 * 4096];
        img[..512].copy_from_slice(&grub2_boot_img(1));
        img[512..1024].copy_from_slice(&grub2_diskboot_img());
        img[1024..1024 + 100].fill(0x5a);
        let src = MemSource(img);
        assert!(grub_core_present(&src, 1, 512));
        assert!(!grub_core_present(&src, 5, 512));
        assert!(!grub_core_present(&src, u64::MAX, 512));
        let b = identify(&src.read_at(0, 512));
        let j = b.to_json(Some(&src), 512, Some(2048));
        assert_eq!(j.get("kind").and_then(Value::as_str), Some("grub2"));
        assert_eq!(j.get("grub_core_lba").and_then(Value::as_u64), Some(1));
        assert_eq!(j.get("grub_boot_drive").and_then(Value::as_str), Some("0xff"));
        assert_eq!(j.get("grub_core_present"), Some(&Value::Bool(true)));
        assert_eq!(j.get("grub_core_in").and_then(Value::as_str), Some("post-mbr-gap"));
        assert_eq!(j.at(&["post_mbr_gap", "sectors"]).and_then(Value::as_u64), Some(2047));
        assert_eq!(j.at(&["post_mbr_gap", "nonzero_sectors_in_first_mib"]).and_then(Value::as_u64), Some(2));
        // GPT with a BIOS boot partition: core lives in a partition.
        let b = identify(&grub2_boot_img(34));
        let j = b.to_json(None, 512, Some(34));
        assert_eq!(j.get("grub_core_in").and_then(Value::as_str), Some("partition"));
        assert!(j.get("grub_core_present").unwrap().is_null());
        assert!(j.get("post_mbr_gap").is_none());
    }

    #[test]
    fn partition_sectors() {
        assert_eq!(partition_boot_code(&MemSource(grub2_boot_img(100))), Some("grub2"));
        assert_eq!(partition_boot_code(&MemSource(vec![0u8; 512])), None);
        let mut fat = vec![0u8; 512];
        fat[0] = 0xeb;
        fat[1] = 0x3c;
        fat[3..11].copy_from_slice(b"mkfs.fat");
        fat[0x80..0x80 + 26].copy_from_slice(b"This is not a bootable dis");
        assert_eq!(partition_boot_code(&MemSource(fat)), None);
    }
}
