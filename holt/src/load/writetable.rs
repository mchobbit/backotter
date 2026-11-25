//! `bko-holt write-table LAYOUT DISK DEVICE SIZE` (not in the usage: run by
//! load's partition stage on systems without sfdisk): writes the source
//! disk's partition table onto DEVICE exactly, GUIDs, names and attributes
//! included, the way parttable.rs reads it back: a GPT (protective MBR,
//! primary and backup headers and entries) or an MBR with up to four
//! primary partitions.  The source's MBR boot code (`boot_code.code_b64`)
//! and the boot areas its partitions held (`boot_code.image_b64`: FreeBSD's
//! freebsd-boot) go back too.  SIZE is DEVICE's size in bytes: a BSD's raw
//! disk device doesn't seek to its end.

use crate::bytes::{crc32, unbase64};
use crate::json::Value;

fn s(v: &Value, k: &str) -> Option<String> {
    v.get(k).and_then(Value::as_str).map(str::to_string)
}

/// A dump's number in any spelling dump writes (a number, decimal text,
/// 0x-hex text): Value::get_u64_lenient, load's one rule.
fn u(v: &Value, k: &str) -> Option<u64> {
    v.get_u64_lenient(k)
}

/// "c12a7328-f81f-11d2-ba4b-00a0c93ec93b" to GPT's on-disk bytes (the first
/// three fields little-endian).  None for anything but 32 hex digits and
/// dashes: the digits are checked before the string is cut in pairs, so a
/// multibyte character (32 bytes, fewer chars) or a '+' (which
/// from_str_radix takes as a sign) is no GUID rather than a panic or a
/// wrong byte.
pub fn guid_bytes(g: &str) -> Option<[u8; 16]> {
    let h: String = g.chars().filter(|c| *c != '-').collect();
    if h.len() != 32 || !h.bytes().all(|b| b.is_ascii_hexdigit()) {
        return None;
    }
    let mut b = [0u8; 16];
    for (i, x) in b.iter_mut().enumerate() {
        *x = u8::from_str_radix(&h[2 * i..2 * i + 2], 16).ok()?;
    }
    b[0..4].reverse();
    b[4..6].reverse();
    b[6..8].reverse();
    Some(b)
}

/// CHS for an MBR entry (255 heads, 63 sectors; 1023/254/63 past the end
/// of CHS), as fdisk and sfdisk write it.
fn chs(lba: u64) -> [u8; 3] {
    let (c, h, s) = if lba >= 1024 * 255 * 63 { (1023, 254, 63) } else { (lba / (255 * 63), (lba / 63) % 255, lba % 63 + 1) };
    [h as u8, ((s as u8) & 0x3f) | (((c >> 8) as u8 & 3) << 6), c as u8]
}

fn mbr_entry(e: &mut [u8], boot: bool, ptype: u8, start: u64, sectors: u64) {
    e[0] = if boot { 0x80 } else { 0 };
    e[1..4].copy_from_slice(&chs(start));
    e[4] = ptype;
    e[5..8].copy_from_slice(&chs(start.saturating_add(sectors.saturating_sub(1))));
    e[8..12].copy_from_slice(&(start.min(u64::from(u32::MAX)) as u32).to_le_bytes());
    e[12..16].copy_from_slice(&(sectors.min(u64::from(u32::MAX)) as u32).to_le_bytes());
}

fn mbr_type(t: &str) -> Option<u8> {
    u8::from_str_radix(t.trim_start_matches("0x"), 16).ok()
}

/// The writes, as (byte offset, bytes), that put `disk`'s table (its JSON
/// in a dump) on a device of `size` bytes.  The GPT's backup goes at the
/// device's end, and its last usable LBA follows it there when the device
/// is bigger.
pub fn plan(disk: &Value, size: u64) -> Result<Vec<(u64, Vec<u8>)>, String> {
    let t = disk.get("partition_table").filter(|t| !t.is_null()).ok_or("the disk has no partition table")?;
    let ss = u(t, "sector_size").unwrap_or(512);
    if !(512..=65536).contains(&ss) || !ss.is_power_of_two() {
        return Err(format!("sector size {ss} isn't a power of two in 512..=65536"));
    }
    let total = size / ss;
    if total == 0 {
        return Err(format!("the device ({size} bytes) holds no {ss}-byte sectors"));
    }
    let src_total = u(disk, "size_bytes").unwrap_or(size) / ss;
    let parts: Vec<&Value> = disk.get("partitions").and_then(Value::as_array).map(|a| a.iter().collect()).unwrap_or_default();
    let code = disk.at(&["boot_code", "code_b64"]).and_then(Value::as_str).and_then(unbase64).unwrap_or_default();
    let mut mbr = vec![0u8; ss as usize];
    mbr[..code.len().min(440)].copy_from_slice(&code[..code.len().min(440)]);
    mbr[510] = 0x55;
    mbr[511] = 0xaa;
    let mut out = Vec::new();
    match s(t, "type").as_deref() {
        Some("gpt") => {
            let disk_guid = guid_bytes(&s(t, "disk_guid").ok_or("no disk GUID")?).ok_or("a bad disk GUID")?;
            let n_u64 = u(t, "entry_count").unwrap_or(128);
            let esz_u64 = u(t, "entry_size").unwrap_or(128);
            if !(1..=4096).contains(&n_u64) {
                return Err(format!("GPT entry_count {n_u64} is out of range (1..=4096)"));
            }
            if !(128..=4096).contains(&esz_u64) || !esz_u64.is_power_of_two() {
                return Err(format!("GPT entry_size {esz_u64} isn't a power of two in 128..=4096"));
            }
            let (n, esz) = (n_u64 as usize, esz_u64 as usize);
            let table_bytes = n.checked_mul(esz).filter(|&b| b <= (16 << 20)).ok_or("GPT entry table is too large")?;
            let entry_sectors = table_bytes.div_ceil(ss as usize) as u64;
            let min_sectors = entry_sectors.checked_mul(2).and_then(|x| x.checked_add(3)).ok_or("GPT entry table overflows LBA")?;
            if total < src_total {
                return Err(format!("the device ({total} sectors) is smaller than the source disk ({src_total})"));
            }
            if src_total < min_sectors || total < min_sectors {
                return Err(format!("the device ({total} sectors) is too small for a GPT with {n} entries (needs at least {min_sectors} sectors)"));
            }
            let max_last = total - 2 - entry_sectors;
            let first = u(t, "first_usable_lba").unwrap_or(2 + entry_sectors);
            // Same size: the source's last usable LBA; bigger: as far as
            // the new backup allows.
            let src_last = u(t, "last_usable_lba").unwrap_or(src_total - 2 - entry_sectors);
            let last = if total > src_total { max_last } else { src_last };
            if last > max_last {
                return Err(format!("the device ({total} sectors) is smaller than the source disk ({src_total})"));
            }
            if first < 2 + entry_sectors || first > last {
                return Err(format!("GPT usable LBA range {first}..={last} is invalid"));
            }
            let mut entries = vec![0u8; table_bytes];
            for p in &parts {
                let num_u64 = u(p, "number").ok_or("a partition without a number")?;
                let Ok(num) = usize::try_from(num_u64) else {
                    return Err(format!("partition {num_u64} is past the table's {n} entries"));
                };
                if num == 0 || num > n {
                    return Err(format!("partition {num} is past the table's {n} entries"));
                }
                let e = &mut entries[(num - 1) * esz..num * esz];
                e[0..16].copy_from_slice(&guid_bytes(&s(p, "type").unwrap_or_default()).ok_or(format!("partition {num}: a bad type GUID"))?);
                e[16..32].copy_from_slice(&guid_bytes(&s(p, "uuid").unwrap_or_default()).ok_or(format!("partition {num}: a bad GUID"))?);
                let start = u(p, "start_lba").ok_or("no start_lba")?;
                let end = match u(p, "end_lba") {
                    Some(end) => end,
                    None => {
                        let secs = u(p, "sectors").unwrap_or(1);
                        if secs == 0 {
                            return Err(format!("partition {num}: zero sectors"));
                        }
                        start.checked_add(secs - 1).ok_or_else(|| format!("partition {num}: end LBA overflows"))?
                    }
                };
                if start < 2 + entry_sectors || end < start || end > max_last {
                    return Err(format!("partition {num} ({start}..={end}) overlaps GPT metadata or exceeds device"));
                }
                e[32..40].copy_from_slice(&start.to_le_bytes());
                e[40..48].copy_from_slice(&end.to_le_bytes());
                e[48..56].copy_from_slice(&u(p, "attributes").unwrap_or(0).to_le_bytes());
                for (i, c) in s(p, "partlabel").unwrap_or_default().encode_utf16().take(36).enumerate() {
                    e[56 + 2 * i..58 + 2 * i].copy_from_slice(&c.to_le_bytes());
                }
            }
            let ecrc = crc32(&entries);
            let header = |my: u64, alt: u64, elba: u64| {
                let mut h = vec![0u8; ss as usize];
                h[0..8].copy_from_slice(b"EFI PART");
                h[8..12].copy_from_slice(&0x0001_0000u32.to_le_bytes());
                h[12..16].copy_from_slice(&92u32.to_le_bytes());
                h[24..32].copy_from_slice(&my.to_le_bytes());
                h[32..40].copy_from_slice(&alt.to_le_bytes());
                h[40..48].copy_from_slice(&first.to_le_bytes());
                h[48..56].copy_from_slice(&last.to_le_bytes());
                h[56..72].copy_from_slice(&disk_guid);
                h[72..80].copy_from_slice(&elba.to_le_bytes());
                h[80..84].copy_from_slice(&(n as u32).to_le_bytes());
                h[84..88].copy_from_slice(&(esz as u32).to_le_bytes());
                h[88..92].copy_from_slice(&ecrc.to_le_bytes());
                let c = crc32(&h[..92]);
                h[16..20].copy_from_slice(&c.to_le_bytes());
                h
            };
            // The protective MBR: the source's entries (a hybrid MBR's
            // too), the 0xee one reaching the new end.
            let pm = t.at(&["protective_mbr", "entries"]).and_then(Value::as_array).cloned().unwrap_or_default();
            let sig = pm.first().and_then(|e| s(e, "uuid")).and_then(|u| u32::from_str_radix(u.split('-').next().unwrap_or(""), 16).ok()).unwrap_or(0);
            mbr[440..444].copy_from_slice(&sig.to_le_bytes());
            if pm.is_empty() {
                mbr_entry(&mut mbr[446..462], false, 0xee, 1, total - 1);
            }
            for (i, e) in pm.iter().take(4).enumerate() {
                let ty = s(e, "type").and_then(|t| mbr_type(&t)).unwrap_or(0xee);
                let start = u(e, "start_lba").unwrap_or(1);
                let mut len = u(e, "sectors").unwrap_or(total - 1);
                if ty == 0xee && start.saturating_add(len) >= src_total.min(1 << 32) {
                    len = total.saturating_sub(start).min(u64::from(u32::MAX));
                }
                mbr_entry(&mut mbr[446 + 16 * i..462 + 16 * i], e.get("bootable").and_then(Value::as_bool) == Some(true), ty, start, len);
            }
            // Whole sectors (illumos' EFI label has 9 entries, 1152 bytes, and
            // a raw disk device takes only whole sectors).
            entries.resize(entry_sectors as usize * ss as usize, 0);
            let primary_entries_off = 2u64.checked_mul(ss).ok_or("primary GPT offset overflows")?;
            let backup_entries_off = (total - 1 - entry_sectors).checked_mul(ss).ok_or("backup GPT entries offset overflows")?;
            let backup_hdr_off = (total - 1).checked_mul(ss).ok_or("backup GPT header offset overflows")?;
            out.push((0, mbr));
            out.push((ss, header(1, total - 1, 2)));
            out.push((primary_entries_off, entries.clone()));
            out.push((backup_entries_off, entries));
            out.push((backup_hdr_off, header(total - 1, 1, total - 1 - entry_sectors)));
        }
        Some("dos" | "mbr") => {
            if total < src_total {
                return Err(format!("the device ({total} sectors) is smaller than the source disk ({src_total})"));
            }
            let sig = s(t, "disk_signature").and_then(|x| u32::from_str_radix(x.trim_start_matches("0x"), 16).ok()).unwrap_or(0);
            mbr[440..444].copy_from_slice(&sig.to_le_bytes());
            // Bug: MBR write-table never checked start_lba == 0, zero sectors,
            // duplicate/overlapping primary slots, target disk bounds, or >32-bit
            // LBA values that mbr_entry silently clamped to u32::MAX, writing
            // corrupt or truncated partition tables. Validate every MBR entry.
            let mut spans: Vec<(usize, u64, u64)> = Vec::new();
            for p in &parts {
                if p.get("logical").and_then(Value::as_bool) == Some(true) || p.get("extended").and_then(Value::as_bool) == Some(true) {
                    return Err("extended and logical partitions aren't written by write-table".into());
                }
                let num_u64 = u(p, "number").ok_or("a partition without a number")?;
                let Ok(num) = usize::try_from(num_u64) else {
                    return Err(format!("MBR partition {num_u64} isn't a primary one"));
                };
                if !(1..=4).contains(&num) {
                    return Err(format!("MBR partition {num} isn't a primary one"));
                }
                if spans.iter().any(|(n, _, _)| *n == num) {
                    return Err(format!("MBR partition {num} is listed twice"));
                }
                let ty = s(p, "type").and_then(|t| mbr_type(&t)).ok_or(format!("partition {num}: a bad type"))?;
                let (start, secs) = (u(p, "start_lba").unwrap_or(0), u(p, "sectors").unwrap_or(0));
                let end = start.checked_add(secs).ok_or_else(|| format!("partition {num}: end LBA overflows"))?;
                if start == 0 || secs == 0 || start > u64::from(u32::MAX) || secs > u64::from(u32::MAX) || end > total {
                    return Err(format!("partition {num} ({start}+{secs} sectors) overlaps MBR, overflows 32-bit LBA, or exceeds device"));
                }
                if let Some((other, _, _)) = spans.iter().find(|(_, s, e)| start < *e && *s < end) {
                    return Err(format!("MBR partitions {other} and {num} overlap"));
                }
                spans.push((num, start, end));
                mbr_entry(&mut mbr[446 + 16 * (num - 1)..462 + 16 * (num - 1)], p.get("bootable").and_then(Value::as_bool) == Some(true), ty, start, secs);
            }
            out.push((0, mbr));
        }
        other => return Err(format!("partition table type {other:?} isn't one write-table writes")),
    }
    // GRUB's core.img in an MBR disk's gap (offline.rs's gap_b64).
    if let Some(g) = disk.at(&["boot_code", "gap_b64"]).and_then(Value::as_str).and_then(unbase64).filter(|b| !b.is_empty()) {
        let gap_end = ss.checked_add(g.len() as u64).ok_or("MBR boot gap overflows offset")?;
        if gap_end > size {
            return Err("MBR boot gap exceeds device size".into());
        }
        if let Some(min_start) = parts.iter().filter_map(|p| u(p, "start_lba")).filter(|&l| l > 0).min() {
            if let Some(part_byte) = min_start.checked_mul(ss) {
                if gap_end > part_byte {
                    return Err(format!("MBR boot gap ({} bytes) overlaps first partition at LBA {min_start}", g.len()));
                }
            }
        }
        out.push((ss, g));
    }
    // An unmounted ESP's bytes (other/'s esp_image), where they were.
    for p in &parts {
        let start = u(p, "start_lba").unwrap_or(0).checked_mul(ss).ok_or("ESP start_lba overflows byte offset")?;
        let part_bytes = u(p, "size_bytes").or_else(|| u(p, "sectors").and_then(|n| n.checked_mul(ss)));
        for c in p.get("esp_image").and_then(Value::as_array).into_iter().flatten() {
            let (Some(off), Some(b)) = (u(c, "offset"), c.get("b64").and_then(Value::as_str).and_then(unbase64)) else { continue };
            let end_in_part = off.checked_add(b.len() as u64).ok_or("ESP image chunk overflows offset")?;
            if let Some(plen) = part_bytes {
                if end_in_part > plen {
                    return Err(format!("partition {}'s esp_image exceeds partition size", u(p, "number").unwrap_or(0)));
                }
            }
            let abs_off = start.checked_add(off).ok_or("ESP image offset overflows")?;
            if abs_off.checked_add(b.len() as u64).is_none_or(|end| end > size) {
                return Err(format!("partition {}'s esp_image exceeds device size", u(p, "number").unwrap_or(0)));
            }
            out.push((abs_off, b));
        }
    }
    // Boot areas the partitions held, back where they were.
    for p in &parts {
        if let Some(img) = p.at(&["boot_code", "image_b64"]).and_then(Value::as_str).and_then(unbase64).filter(|b| !b.is_empty()) {
            let start = u(p, "start_lba").unwrap_or(0).checked_mul(ss).ok_or("boot_code start_lba overflows")?;
            let len = u(p, "size_bytes").or_else(|| u(p, "sectors").and_then(|n| n.checked_mul(ss))).unwrap_or(0);
            let padded_u64 = (img.len() as u64).div_ceil(ss).checked_mul(ss).ok_or("boot_code padded size overflows")?;
            if img.len() as u64 > len || padded_u64 > len {
                return Err(format!("partition {}'s boot code is bigger than the partition", u(p, "number").unwrap_or(0)));
            }
            if start.checked_add(padded_u64).is_none_or(|end| end > size) {
                return Err(format!("partition {}'s boot code exceeds device size", u(p, "number").unwrap_or(0)));
            }
            let mut b = img;
            b.resize(padded_u64 as usize, 0);
            out.push((start, b));
        }
    }
    Ok(out)
}

/// Whether E, from fsync on the device, says only that the device takes no
/// fsync: POSIX's EINVAL ("not a file on which this operation is
/// possible") or ENOSYS, which a raw disk device on some systems may give
/// (std's InvalidInput and Unsupported).  ENOTSUP/EOPNOTSUPP is Unsupported
/// too from std 1.94.1, but Uncategorized from 1.85.1 (the floor), so a
/// floor build fails on it.  Its writes are done (write_all_at returned),
/// so that one is let go; any other error (EIO) is the table not reaching
/// the disk.
fn sync_unsupported(e: &std::io::Error) -> bool {
    matches!(e.kind(), std::io::ErrorKind::InvalidInput | std::io::ErrorKind::Unsupported)
}

pub fn main(args: &[String]) -> u8 {
    let [layout, disk, dev, size] = args else {
        eprintln!("usage: bko-holt write-table LAYOUT DISK DEVICE SIZE_BYTES");
        return 2;
    };
    let run = || -> Result<(), String> {
        let text = crate::bytes::read_to_string_bounded(std::path::Path::new(layout), crate::LAYOUT_MAX).map_err(|e| format!("{layout}: {e}"))?;
        let v = crate::json::parse(&text).map_err(|e| format!("{layout}: {e:?}"))?;
        let d = v.at(&["storage", "disks"]).and_then(Value::as_array).and_then(|a| a.iter().find(|x| s(x, "name").as_deref() == Some(disk.as_str()))).ok_or(format!("the layout has no disk {disk}"))?;
        // auto: found by reading (Haiku's raw disks report no size).
        let size: u64 = if size == "auto" {
            crate::other::haiku::probe_size(std::path::Path::new(dev)).ok_or(format!("{dev}: can't tell its size"))?
        } else {
            size.parse().map_err(|_| format!("SIZE_BYTES {size:?} isn't a number"))?
        };
        let writes = plan(d, size)?;
        let f = std::fs::OpenOptions::new().write(true).open(dev).map_err(|e| format!("{dev}: {e}"))?;
        for (off, b) in writes {
            use std::os::unix::fs::FileExt;
            f.write_all_at(&b, off).map_err(|e| format!("{dev}: writing {} bytes at {off}: {e}", b.len()))?;
        }
        // File's flush does nothing: sync_all is what puts the table on
        // the disk, so its error is the write's (the cache took the writes,
        // the disk failed them), and the partition stage mustn't go on.
        match f.sync_all() {
            Err(e) if !sync_unsupported(&e) => Err(format!("{dev}: sync: {e}")),
            _ => Ok(()),
        }
    };
    match run() {
        Ok(()) => 0,
        Err(e) => {
            eprintln!("bko-holt write-table: {e}");
            1
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::bytes::MemSource;
    use crate::parttable::{read_table, Table};

    fn apply(writes: &[(u64, Vec<u8>)], size: usize) -> Vec<u8> {
        let mut img = vec![0u8; size];
        for (off, b) in writes {
            img[*off as usize..*off as usize + b.len()].copy_from_slice(b);
        }
        img
    }

    const GPT: &str = r#"{"name": "vtbd0", "size_bytes": 16777216, "partition_table": {"type": "gpt", "sector_size": 512, "disk_guid": "d837c5f1-d686-11f1-83e1-03e680c1a8f0", "first_usable_lba": 40, "last_usable_lba": 32727, "entry_count": 128, "entry_size": 128,
      "protective_mbr": {"entries": [{"number": 1, "start_lba": 1, "sectors": 32767, "type": "0xee", "bootable": true, "uuid": "00000000-01"}]}},
      "boot_code": {"kind": "freebsd-pmbr", "code_b64": "/DHAjsCO2I7Q"},
      "partitions": [
        {"number": 1, "start_lba": 40, "end_lba": 1063, "sectors": 1024, "size_bytes": 524288, "type": "83bd6b9d-7f41-11dc-be0b-001560b84f0f", "uuid": "d837c62b-d686-11f1-83e1-03e680c1a8f0", "partlabel": "gptboot0", "attributes": "0x0000000000000000", "boot_code": {"image_b64": "QlRY"}},
        {"number": 3, "start_lba": 2048, "end_lba": 32727, "sectors": 30680, "size_bytes": 15708160, "type": "516e7cba-6ecf-11d6-8ff8-00022d09712b", "uuid": "d8380a2b-d686-11f1-83e1-03e680c1a8f0", "partlabel": "zfs0", "attributes": "0x0000000000000004"}]}"#;

    #[test]
    fn gpt_round_trip() {
        let d = crate::json::parse(GPT).unwrap();
        for size in [16777216usize, 16777216 + (1 << 20)] {
            let img = apply(&plan(&d, size as u64).unwrap(), size);
            let Some(Table::Gpt { pmbr, gpt }) = read_table(&MemSource(img.clone()), 512) else { panic!("no GPT") };
            assert!(gpt.primary_ok);
            assert_eq!(gpt.backup, crate::parttable::GptBackup::Ok);
            assert_eq!(gpt.disk_guid, "d837c5f1-d686-11f1-83e1-03e680c1a8f0");
            assert_eq!(gpt.first_usable_lba, 40);
            assert_eq!(gpt.last_usable_lba, if size == 16777216 { 32727 } else { (size as u64 / 512) - 34 });
            assert_eq!(gpt.backup_lba, size as u64 / 512 - 1);
            assert_eq!(gpt.parts.len(), 2);
            let p = &gpt.parts[0];
            assert_eq!((p.number, p.first_lba, p.last_lba, p.name.as_str()), (1, 40, 1063, "gptboot0"));
            assert_eq!(p.type_guid, "83bd6b9d-7f41-11dc-be0b-001560b84f0f");
            assert_eq!(p.uuid, "d837c62b-d686-11f1-83e1-03e680c1a8f0");
            assert_eq!(gpt.parts[1].attributes, 4);
            assert_eq!(pmbr.parts[0].ptype, 0xee);
            assert_eq!(pmbr.parts[0].boot_flag, 0x80);
            assert_eq!(u64::from(pmbr.parts[0].sectors), size as u64 / 512 - 1);
            assert_eq!(&img[0..9], &[0xfc, 0x31, 0xc0, 0x8e, 0xc0, 0x8e, 0xd8, 0x8e, 0xd0]);
            assert_eq!(&img[40 * 512..40 * 512 + 3], b"BTX");
        }
        assert!(plan(&d, 8 << 20).unwrap_err().contains("smaller"));
    }

    #[test]
    fn mbr_round_trip() {
        let d = crate::json::parse(r#"{"name": "wd0", "size_bytes": 8392704, "partition_table": {"type": "dos", "sector_size": 512, "disk_signature": "1234abcd"},
          "partitions": [{"number": 1, "start_lba": 63, "sectors": 16321, "type": "0xa6", "bootable": true}, {"number": 4, "start_lba": 16384, "sectors": 8, "type": "0x83"}]}"#).unwrap();
        let img = apply(&plan(&d, 8392704).unwrap(), 8392704);
        let Some(Table::Mbr(m)) = read_table(&MemSource(img), 512) else { panic!("no MBR") };
        assert_eq!(m.disk_signature, 0x1234abcd);
        assert_eq!(m.parts.iter().map(|p| (p.number, p.ptype, p.start, p.sectors, p.boot_flag)).collect::<Vec<_>>(), vec![(1, 0xa6, 63, 16321, 0x80), (4, 0x83, 16384, 8, 0)]);
        assert_eq!(guid_bytes("c12a7328-f81f-11d2-ba4b-00a0c93ec93b").unwrap()[..4], [0x28, 0x73, 0x2a, 0xc1]);
    }

    #[test]
    fn hostile_and_malformed_tables_return_err_without_panic() {
        for (js, sz) in [
            (r#"{"partition_table": {"type": "gpt", "sector_size": 0, "disk_guid": "d837c5f1-d686-11f1-83e1-03e680c1a8f0"}}"#, 1 << 20),
            (r#"{"partition_table": {"type": "gpt", "sector_size": 513, "disk_guid": "d837c5f1-d686-11f1-83e1-03e680c1a8f0"}}"#, 1 << 20),
            (r#"{"partition_table": {"type": "gpt", "sector_size": 512, "disk_guid": "d837c5f1-d686-11f1-83e1-03e680c1a8f0", "entry_count": 1000000000}}"#, 1 << 20),
            (r#"{"partition_table": {"type": "gpt", "sector_size": 512, "disk_guid": "d837c5f1-d686-11f1-83e1-03e680c1a8f0", "entry_size": 24}}"#, 1 << 20),
            (r#"{"size_bytes": 1024, "partition_table": {"type": "gpt", "sector_size": 512, "disk_guid": "d837c5f1-d686-11f1-83e1-03e680c1a8f0"}}"#, 1024),
            (r#"{"size_bytes": 1048576, "partition_table": {"type": "gpt", "sector_size": 512, "disk_guid": "d837c5f1-d686-11f1-83e1-03e680c1a8f0"}, "partitions": [{"number": 1, "start_lba": 0, "sectors": 0, "type": "83bd6b9d-7f41-11dc-be0b-001560b84f0f", "uuid": "d837c62b-d686-11f1-83e1-03e680c1a8f0"}]}"#, 1048576),
            (r#"{"size_bytes": 8192, "partition_table": {"type": "dos", "sector_size": 512}, "boot_code": {"gap_b64": "QlRY"}, "partitions": [{"number": 1, "start_lba": 1, "sectors": 8, "type": "0x83"}]}"#, 8192),
            (r#"{"size_bytes": 8192, "partition_table": {"type": "dos", "sector_size": 512}, "partitions": [{"number": 1, "start_lba": 1, "sectors": 2, "size_bytes": 1024, "type": "0xef", "esp_image": [{"offset": 2048, "b64": "QlRY"}]}]}"#, 8192),
        ] {
            let v = crate::json::parse(js).unwrap();
            assert!(plan(&v, sz).is_err(), "accepted: {js}");
        }
    }

    /// RustAudit1 H7-11: an fsync error is write-table's, except one that
    /// says the device takes no fsync.
    #[test]
    fn only_an_unsupported_sync_is_let_go() {
        use std::io::{Error, ErrorKind};
        assert!(sync_unsupported(&Error::from(ErrorKind::InvalidInput)));
        assert!(sync_unsupported(&Error::from(ErrorKind::Unsupported)));
        assert!(!sync_unsupported(&Error::other("EIO")));
        assert!(!sync_unsupported(&Error::from(ErrorKind::PermissionDenied)));
        #[cfg(target_os = "linux")]
        {
            assert!(!sync_unsupported(&Error::from_raw_os_error(5)), "EIO");
            assert!(sync_unsupported(&Error::from_raw_os_error(22)), "EINVAL");
            assert!(sync_unsupported(&Error::from_raw_os_error(38)), "ENOSYS");
        }
    }

    /// RustAudit1 H7-10: a GUID of 32 bytes that aren't 32 hex digits (a
    /// multibyte character cut mid-way, a sign) is None, and plan() says
    /// which partition's GUID is bad instead of panicking.
    #[test]
    fn a_guid_with_multibyte_or_sign_characters_is_rejected() {
        let wide = format!("a{}a", "\u{e9}".repeat(15));
        assert_eq!(wide.len(), 32);
        assert_eq!(guid_bytes(&wide), None);
        assert_eq!(guid_bytes("+c12a7328f81f11d2ba4b00a0c93ec93"), None);
        assert_eq!(guid_bytes("c12a7328-f81f-11d2-ba4b-00a0c93ec93g"), None);
        assert!(guid_bytes("C12A7328-F81F-11D2-BA4B-00A0C93EC93B").is_some());
        let js = GPT.replace("83bd6b9d-7f41-11dc-be0b-001560b84f0f", &wide);
        assert_eq!(plan(&crate::json::parse(&js).unwrap(), 16777216).unwrap_err(), "partition 1: a bad type GUID");
        let js = GPT.replace("d837c62b-d686-11f1-83e1-03e680c1a8f0", &wide);
        assert_eq!(plan(&crate::json::parse(&js).unwrap(), 16777216).unwrap_err(), "partition 1: a bad GUID");
    }
}
