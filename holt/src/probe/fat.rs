//! Boot-sector filesystems: FAT12/16/32 (ESPs), exFAT and NTFS.

use super::Content;
use crate::bytes::{le16, le32, le64, sub, u8_at, ByteSource};
use crate::json::Obj;

pub fn probe(src: &dyn ByteSource) -> Option<Content> {
    let b = src.read_exact_at(0, 512)?;
    match sub(&b, 3, 8) {
        b"NTFS    " => ntfs(&b),
        b"EXFAT   " => exfat(&b),
        _ => vfat(src, &b),
    }
}

fn vfat(src: &dyn ByteSource, b: &[u8]) -> Option<Content> {
    if b[510] != 0x55 || b[511] != 0xaa {
        // mkfs.fat always writes the signature; very old DOS disks may not,
        // but those don't hold ESPs.
        return None;
    }
    if !(b[0] == 0xeb || b[0] == 0xe9) {
        return None;
    }
    let bps = le16(b, 11) as u64;
    let spc = u8_at(b, 13) as u64;
    let reserved = le16(b, 14) as u64;
    let fats = u8_at(b, 16) as u64;
    let root_entries = le16(b, 17) as u64;
    let total16 = le16(b, 19) as u64;
    let media = u8_at(b, 21);
    let fatsz16 = le16(b, 22) as u64;
    let total32 = le32(b, 32) as u64;
    if !matches!(bps, 512 | 1024 | 2048 | 4096) || spc == 0 || !spc.is_power_of_two() || reserved == 0 || fats == 0 || fats > 4 {
        return None;
    }
    if !(media == 0xf0 || media >= 0xf8) {
        return None;
    }
    let fatsz = if fatsz16 != 0 { fatsz16 } else { le32(b, 36) as u64 };
    let total = if total16 != 0 { total16 } else { total32 };
    if fatsz == 0 || total == 0 {
        return None;
    }
    let root_dir_sectors = (root_entries * 32).div_ceil(bps);
    let meta = reserved + fats * fatsz + root_dir_sectors;
    let clusters = total.checked_sub(meta)? / spc;
    // As blkid decides: a zero 16-bit FAT size means the FAT32 layout
    // (however few clusters); otherwise the cluster count picks 12 or 16.
    let bits = if fatsz16 == 0 {
        32
    } else if clusters < 4085 {
        12
    } else {
        16
    };
    // The extended boot record sits at 36 (FAT12/16) or 64 (FAT32).
    let ebr = if bits == 32 { 64 } else { 36 };
    let has_ebr = u8_at(b, ebr + 2) == 0x29;
    let serial = has_ebr.then(|| le32(b, ebr + 3));
    let bs_label = if has_ebr { crate::bytes::label(sub(b, ebr + 7, 11)).filter(|l| l != "NO NAME") } else { None };
    // The label people see is the root directory's volume-label entry; the
    // boot sector's copy is often stale.  FAT32's root directory is a
    // cluster chain: read only its first cluster, where mkfs.fat and fatlabel
    // put the label.
    let root_dir_offset = if bits == 32 {
        let root_cluster = le32(b, 44) as u64;
        (reserved + fats * fatsz + root_cluster.saturating_sub(2) * spc) * bps
    } else {
        (reserved + fats * fatsz) * bps
    };
    let root_len = if bits == 32 { spc * bps } else { root_dir_sectors * bps };
    let dir_label = root_dir_label(&src.read_at(root_dir_offset, root_len.min(65536) as usize));
    let mut d = Obj::new()
        .set("fat_bits", bits)
        .set("bytes_per_sector", bps)
        .set("sectors_per_cluster", spc)
        .set("cluster_size", bps * spc)
        .set("reserved_sectors", reserved)
        .set("fats", fats)
        .set("fat_sectors", fatsz)
        .set("total_sectors", total)
        .set("size_bytes", total * bps)
        .set("clusters", clusters)
        .set("hidden_sectors", le32(b, 28))
        .set("media", format!("0x{media:02x}"))
        .set("oem_name", crate::bytes::label(sub(b, 3, 8)));
    if bits != 32 {
        d.put("root_entries", root_entries);
    } else {
        d.put("root_cluster", le32(b, 44));
        d.put("fsinfo_sector", le16(b, 48));
        d.put("backup_boot_sector", le16(b, 50));
    }
    d.put("boot_sector_label", bs_label.clone());
    Some(Content {
        kind: "vfat".into(),
        usage: "filesystem",
        uuid: serial.map(|s| format!("{:04X}-{:04X}", s >> 16, s & 0xffff)),
        label: dir_label.or(bs_label),
        version: Some(format!("FAT{bits}")),
        details: d,
        extra: None,
    })
}

/// The volume label entry in a FAT directory, skipping deleted entries and
/// long-name pieces.
fn root_dir_label(dir: &[u8]) -> Option<String> {
    for e in dir.chunks_exact(32) {
        match e[0] {
            0x00 => return None,
            0xe5 => continue,
            _ => {}
        }
        let attr = e[11];
        if attr & 0x0f == 0x0f {
            continue; // long file name entry
        }
        if attr & 0x08 != 0 && attr & 0x10 == 0 {
            return crate::bytes::label(&e[0..11]).filter(|l| l != "NO NAME");
        }
    }
    None
}

fn exfat(b: &[u8]) -> Option<Content> {
    let bps_shift = u8_at(b, 108);
    let spc_shift = u8_at(b, 109);
    if !(9..=12).contains(&bps_shift) || spc_shift > 25 {
        return None;
    }
    let serial = le32(b, 100);
    let d = Obj::new()
        .set("bytes_per_sector", 1u64 << bps_shift)
        .set("cluster_size", 1u64 << (bps_shift + spc_shift))
        .set("size_bytes", le64(b, 72) << bps_shift)
        .set("revision", format!("{}.{}", u8_at(b, 105), u8_at(b, 104)));
    Some(Content {
        kind: "exfat".into(),
        usage: "filesystem",
        uuid: Some(format!("{:04X}-{:04X}", serial >> 16, serial & 0xffff)),
        label: None,
        version: None,
        details: d,
        extra: None,
    })
}

fn ntfs(b: &[u8]) -> Option<Content> {
    let bps = le16(b, 11) as u64;
    let spc_raw = u8_at(b, 13);
    if !matches!(bps, 256..=4096) || !bps.is_power_of_two() {
        return None;
    }
    // Values above 0x80 encode a cluster size of 2^(256 - n) bytes.
    let cluster = if spc_raw > 0x80 { 1u64 << (256 - spc_raw as u32).min(31) } else { spc_raw as u64 * bps };
    if cluster == 0 {
        return None;
    }
    let sectors = le64(b, 0x28);
    let d = Obj::new()
        .set("bytes_per_sector", bps)
        .set("cluster_size", cluster)
        .set("size_bytes", sectors.saturating_mul(bps))
        .set("mft_cluster", le64(b, 0x30));
    Some(Content {
        kind: "ntfs".into(),
        usage: "filesystem",
        uuid: Some(format!("{:016X}", le64(b, 0x48))),
        label: None,
        version: None,
        details: d,
        extra: None,
    })
}

#[cfg(test)]
pub mod sample {
    /// A FAT32 filesystem's boot sector and root directory, as `mkfs.fat -F
    /// 32 -n LABEL` lays them out: 512-byte sectors, 1 sector/cluster.
    /// Only the start of the volume is materialised (through the first
    /// root directory cluster), so big volumes stay cheap.
    pub fn fat32(total_sectors: u32, serial: u32, label: &str) -> Vec<u8> {
        let fatsz = (total_sectors / 128) + 1;
        let mut img = vec![0u8; (32 + 2 * fatsz as usize + 8) * 512];
        let b = &mut img[..512];
        b[0..3].copy_from_slice(&[0xeb, 0x58, 0x90]);
        b[3..11].copy_from_slice(b"mkfs.fat");
        b[11..13].copy_from_slice(&512u16.to_le_bytes());
        b[13] = 1;
        b[14..16].copy_from_slice(&32u16.to_le_bytes());
        b[16] = 2;
        b[21] = 0xf8;
        b[28..32].copy_from_slice(&2048u32.to_le_bytes());
        b[32..36].copy_from_slice(&total_sectors.to_le_bytes());
        b[36..40].copy_from_slice(&fatsz.to_le_bytes());
        b[44..48].copy_from_slice(&2u32.to_le_bytes());
        b[48..50].copy_from_slice(&1u16.to_le_bytes());
        b[50..52].copy_from_slice(&6u16.to_le_bytes());
        b[66] = 0x29;
        b[67..71].copy_from_slice(&serial.to_le_bytes());
        let mut l = [b' '; 11];
        l[..label.len()].copy_from_slice(label.as_bytes());
        b[71..82].copy_from_slice(&l);
        b[82..90].copy_from_slice(b"FAT32   ");
        b[510] = 0x55;
        b[511] = 0xaa;
        let root = (32 + 2 * fatsz as usize) * 512;
        img[root..root + 11].copy_from_slice(&l);
        img[root + 11] = 0x08;
        img
    }

    /// FAT16 like `mkfs.fat -F 16`: 4 sectors/cluster, 512 root entries.
    pub fn fat16(total_sectors: u16, serial: u32, label: &str) -> Vec<u8> {
        let mut img = vec![0u8; total_sectors as usize * 512];
        let b = &mut img[..512];
        b[0..3].copy_from_slice(&[0xeb, 0x3c, 0x90]);
        b[3..11].copy_from_slice(b"mkfs.fat");
        b[11..13].copy_from_slice(&512u16.to_le_bytes());
        b[13] = 4;
        b[14..16].copy_from_slice(&4u16.to_le_bytes());
        b[16] = 2;
        b[17..19].copy_from_slice(&512u16.to_le_bytes());
        b[19..21].copy_from_slice(&total_sectors.to_le_bytes());
        b[21] = 0xf8;
        b[22..24].copy_from_slice(&64u16.to_le_bytes());
        b[38] = 0x29;
        b[39..43].copy_from_slice(&serial.to_le_bytes());
        let mut l = *b"NO NAME    ";
        if !label.is_empty() {
            l = [b' '; 11];
            l[..label.len()].copy_from_slice(label.as_bytes());
        }
        b[43..54].copy_from_slice(&l);
        b[54..62].copy_from_slice(b"FAT16   ");
        b[510] = 0x55;
        b[511] = 0xaa;
        img
    }
}

#[cfg(test)]
mod tests {
    use super::sample::*;
    use super::*;
    use crate::bytes::MemSource;
    use crate::json::Value;

    #[test]
    fn fat32_esp() {
        let c = probe(&MemSource(fat32(1_048_576, 0x1a2b_3c4d, "EFI"))).unwrap();
        assert_eq!(c.kind, "vfat");
        assert_eq!(c.version.as_deref(), Some("FAT32"));
        assert_eq!(c.uuid.as_deref(), Some("1A2B-3C4D"));
        assert_eq!(c.label.as_deref(), Some("EFI"));
        let d = c.details.build();
        assert_eq!(d.get("fat_bits").and_then(Value::as_u64), Some(32));
        assert_eq!(d.get("size_bytes").and_then(Value::as_u64), Some(512 << 20));
        assert_eq!(d.get("root_cluster").and_then(Value::as_u64), Some(2));
        assert_eq!(d.get("boot_sector_label").and_then(Value::as_str), Some("EFI"));
        assert_eq!(d.get("oem_name").and_then(Value::as_str), Some("mkfs.fat"));
    }

    #[test]
    fn root_label_wins_over_boot_sector() {
        let mut img = fat32(1_048_576, 1, "OLD");
        let fatsz = 1_048_576 / 128 + 1;
        let root = (32 + 2 * fatsz) * 512;
        // A deleted entry and a long-name entry come first; then the label.
        img[root..root + 32].fill(0);
        img[root] = 0xe5;
        img[root + 11] = 0x08;
        img[root + 32] = b'A';
        img[root + 32 + 11] = 0x0f;
        img[root + 64..root + 75].copy_from_slice(b"NEWLABEL   ");
        img[root + 64 + 11] = 0x08;
        let c = probe(&MemSource(img)).unwrap();
        assert_eq!(c.label.as_deref(), Some("NEWLABEL"));
        assert_eq!(c.details.build().get("boot_sector_label").and_then(Value::as_str), Some("OLD"));
    }

    #[test]
    fn fat16_no_name() {
        let c = probe(&MemSource(fat16(65000, 0xdead_beef, ""))).unwrap();
        assert_eq!(c.version.as_deref(), Some("FAT16"));
        assert_eq!(c.uuid.as_deref(), Some("DEAD-BEEF"));
        assert_eq!(c.label, None);
        let d = c.details.build();
        assert_eq!(d.get("root_entries").and_then(Value::as_u64), Some(512));
        assert!(d.get("boot_sector_label").unwrap().is_null());
        let c = probe(&MemSource(fat16(65000, 1, "BACKOTTER"))).unwrap();
        assert_eq!(c.label.as_deref(), Some("BACKOTTER"));
    }

    #[test]
    fn small_fat32_is_still_fat32() {
        // mkfs.fat -F 32 -S 4096 on 100 MiB: far fewer than 65525 clusters.
        let c = probe(&MemSource(fat32(20000, 0x0bad_cafe, "TINY"))).unwrap();
        assert_eq!(c.version.as_deref(), Some("FAT32"));
        assert_eq!(c.uuid.as_deref(), Some("0BAD-CAFE"));
        assert_eq!(c.label.as_deref(), Some("TINY"));
    }

    #[test]
    fn fat12_small() {
        let c = probe(&MemSource(fat16(2880, 5, "FLOPPY"))).unwrap();
        assert_eq!(c.version.as_deref(), Some("FAT12"));
    }

    #[test]
    fn vfat_rejects() {
        let good = fat32(65536 * 2, 1, "X");
        for (off, val) in [(510usize, 0u8), (0, 0x00), (11, 0x03), (13, 3), (13, 0), (14, 0), (16, 0), (16, 9), (21, 0x12)] {
            let mut b = good.clone();
            b[off] = val;
            if off == 14 {
                b[15] = 0;
            }
            assert!(probe(&MemSource(b)).is_none(), "byte {off}={val:#x} accepted");
        }
        // An MBR (GRUB, no BPB) isn't FAT.
        assert!(probe(&MemSource(crate::bootcode::samples::grub2_boot_img(1))).is_none());
        assert!(probe(&MemSource(vec![0u8; 100])).is_none());
        // Metadata bigger than the volume.
        let mut b = fat16(100, 1, "");
        b[19..21].copy_from_slice(&10u16.to_le_bytes());
        assert!(probe(&MemSource(b)).is_none());
    }

    #[test]
    fn exfat_and_ntfs() {
        let mut b = vec![0u8; 512];
        b[3..11].copy_from_slice(b"EXFAT   ");
        b[72..80].copy_from_slice(&(1u64 << 21).to_le_bytes());
        b[100..104].copy_from_slice(&0x1234_5678u32.to_le_bytes());
        b[104] = 0;
        b[105] = 1;
        b[108] = 9;
        b[109] = 8;
        let c = probe(&MemSource(b.clone())).unwrap();
        assert_eq!(c.kind, "exfat");
        assert_eq!(c.uuid.as_deref(), Some("1234-5678"));
        let d = c.details.build();
        assert_eq!(d.get("cluster_size").and_then(Value::as_u64), Some(1 << 17));
        assert_eq!(d.get("size_bytes").and_then(Value::as_u64), Some(1 << 30));
        assert_eq!(d.get("revision").and_then(Value::as_str), Some("1.0"));
        b[108] = 3;
        assert!(probe(&MemSource(b)).is_none());

        let mut n = vec![0u8; 512];
        n[3..11].copy_from_slice(b"NTFS    ");
        n[11..13].copy_from_slice(&512u16.to_le_bytes());
        n[13] = 8;
        n[0x28..0x30].copy_from_slice(&2_097_151u64.to_le_bytes());
        n[0x48..0x50].copy_from_slice(&0x0123_4567_89ab_cdefu64.to_le_bytes());
        let c = probe(&MemSource(n.clone())).unwrap();
        assert_eq!(c.kind, "ntfs");
        assert_eq!(c.uuid.as_deref(), Some("0123456789ABCDEF"));
        assert_eq!(c.details.clone().build().get("cluster_size").and_then(Value::as_u64), Some(4096));
        n[13] = 0xf4; // 2^12
        assert_eq!(probe(&MemSource(n.clone())).unwrap().details.build().get("cluster_size").and_then(Value::as_u64), Some(4096));
        n[11..13].copy_from_slice(&300u16.to_le_bytes());
        assert!(probe(&MemSource(n.clone())).is_none());
        n[11..13].copy_from_slice(&512u16.to_le_bytes());
        n[13] = 0;
        assert!(probe(&MemSource(n)).is_none());
    }

    #[test]
    fn dir_label_edge_cases() {
        assert_eq!(root_dir_label(&[]), None);
        let mut d = vec![0u8; 64];
        d[0..11].copy_from_slice(b"SUBDIR     ");
        d[11] = 0x18; // label + directory bits: a directory, not a label
        assert_eq!(root_dir_label(&d), None);
        d[32..43].copy_from_slice(b"NO NAME    ");
        d[43] = 0x08;
        assert_eq!(root_dir_label(&d), None);
    }
}
