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
use std::io::Write;

fn s(v: &Value, k: &str) -> Option<String> {
    v.get(k).and_then(Value::as_str).map(str::to_string)
}

fn u(v: &Value, k: &str) -> Option<u64> {
    match v.get(k)? {
        Value::Str(t) => t.strip_prefix("0x").map_or_else(|| t.parse().ok(), |h| u64::from_str_radix(h, 16).ok()),
        x => x.as_u64(),
    }
}

/// "c12a7328-f81f-11d2-ba4b-00a0c93ec93b" to GPT's on-disk bytes (the first
/// three fields little-endian).
pub fn guid_bytes(g: &str) -> Option<[u8; 16]> {
    let h: String = g.chars().filter(|c| *c != '-').collect();
    if h.len() != 32 {
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
    e[5..8].copy_from_slice(&chs(start + sectors.saturating_sub(1)));
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
    let total = size / ss;
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
            let n = u(t, "entry_count").unwrap_or(128) as usize;
            let esz = u(t, "entry_size").unwrap_or(128) as usize;
            let entry_sectors = (n * esz).div_ceil(ss as usize) as u64;
            let first = u(t, "first_usable_lba").unwrap_or(2 + entry_sectors);
            // Same size: the source's last usable LBA; bigger: as far as
            // the new backup allows.
            let src_last = u(t, "last_usable_lba").unwrap_or(src_total - 2 - entry_sectors);
            let last = if total > src_total { total - 2 - entry_sectors } else { src_last };
            if total < src_total || last > total - 2 - entry_sectors {
                return Err(format!("the device ({total} sectors) is smaller than the source disk ({src_total})"));
            }
            let mut entries = vec![0u8; n * esz];
            for p in &parts {
                let num = u(p, "number").ok_or("a partition without a number")? as usize;
                if num == 0 || num > n {
                    return Err(format!("partition {num} is past the table's {n} entries"));
                }
                let e = &mut entries[(num - 1) * esz..num * esz];
                e[0..16].copy_from_slice(&guid_bytes(&s(p, "type").unwrap_or_default()).ok_or(format!("partition {num}: a bad type GUID"))?);
                e[16..32].copy_from_slice(&guid_bytes(&s(p, "uuid").unwrap_or_default()).ok_or(format!("partition {num}: a bad GUID"))?);
                let start = u(p, "start_lba").ok_or("no start_lba")?;
                let end = u(p, "end_lba").unwrap_or(start + u(p, "sectors").unwrap_or(1) - 1);
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
                if ty == 0xee && start + len >= src_total.min(1 << 32) {
                    len = (total - start).min(u64::from(u32::MAX));
                }
                mbr_entry(&mut mbr[446 + 16 * i..462 + 16 * i], e.get("bootable").and_then(Value::as_bool) == Some(true), ty, start, len);
            }
            out.push((0, mbr));
            out.push((ss, header(1, total - 1, 2)));
            out.push((2 * ss, entries.clone()));
            out.push(((total - 1 - entry_sectors) * ss, entries));
            out.push(((total - 1) * ss, header(total - 1, 1, total - 1 - entry_sectors)));
        }
        Some("dos" | "mbr") => {
            let sig = s(t, "disk_signature").and_then(|x| u32::from_str_radix(x.trim_start_matches("0x"), 16).ok()).unwrap_or(0);
            mbr[440..444].copy_from_slice(&sig.to_le_bytes());
            for p in &parts {
                if p.get("logical").and_then(Value::as_bool) == Some(true) || p.get("extended").and_then(Value::as_bool) == Some(true) {
                    return Err("extended and logical partitions aren't written by write-table".into());
                }
                let num = u(p, "number").ok_or("a partition without a number")? as usize;
                if !(1..=4).contains(&num) {
                    return Err(format!("MBR partition {num} isn't a primary one"));
                }
                let ty = s(p, "type").and_then(|t| mbr_type(&t)).ok_or(format!("partition {num}: a bad type"))?;
                mbr_entry(&mut mbr[446 + 16 * (num - 1)..462 + 16 * (num - 1)], p.get("bootable").and_then(Value::as_bool) == Some(true), ty, u(p, "start_lba").unwrap_or(0), u(p, "sectors").unwrap_or(0));
            }
            out.push((0, mbr));
        }
        other => return Err(format!("partition table type {other:?} isn't one write-table writes")),
    }
    // GRUB's core.img in an MBR disk's gap (offline.rs's gap_b64).
    if let Some(g) = disk.at(&["boot_code", "gap_b64"]).and_then(Value::as_str).and_then(unbase64).filter(|b| !b.is_empty()) {
        out.push((ss, g));
    }
    // An unmounted ESP's bytes (other/'s esp_image), where they were.
    for p in &parts {
        let start = u(p, "start_lba").unwrap_or(0) * ss;
        for c in p.get("esp_image").and_then(Value::as_array).into_iter().flatten() {
            let (Some(off), Some(b)) = (u(c, "offset"), c.get("b64").and_then(Value::as_str).and_then(unbase64)) else { continue };
            out.push((start + off, b));
        }
    }
    // Boot areas the partitions held, back where they were.
    for p in &parts {
        if let Some(img) = p.at(&["boot_code", "image_b64"]).and_then(Value::as_str).and_then(unbase64).filter(|b| !b.is_empty()) {
            let start = u(p, "start_lba").unwrap_or(0) * ss;
            let len = u(p, "size_bytes").unwrap_or(0);
            if img.len() as u64 > len {
                return Err(format!("partition {}'s boot code is bigger than the partition", u(p, "number").unwrap_or(0)));
            }
            let mut b = img;
            b.resize(b.len().div_ceil(ss as usize) * ss as usize, 0);
            out.push((start, b));
        }
    }
    Ok(out)
}

pub fn main(args: &[String]) -> u8 {
    let [layout, disk, dev, size] = args else {
        eprintln!("usage: bko-holt write-table LAYOUT DISK DEVICE SIZE_BYTES");
        return 2;
    };
    let run = || -> Result<(), String> {
        let text = std::fs::read_to_string(layout).map_err(|e| format!("{layout}: {e}"))?;
        let v = crate::json::parse(&text).map_err(|e| format!("{layout}: {e:?}"))?;
        let d = v.at(&["storage", "disks"]).and_then(Value::as_array).and_then(|a| a.iter().find(|x| s(x, "name").as_deref() == Some(disk.as_str()))).ok_or(format!("the layout has no disk {disk}"))?;
        // auto: found by reading (Haiku's raw disks report no size).
        let size: u64 = if size == "auto" {
            crate::other::haiku::probe_size(std::path::Path::new(dev)).ok_or(format!("{dev}: can't tell its size"))?
        } else {
            size.parse().map_err(|_| format!("SIZE_BYTES {size:?} isn't a number"))?
        };
        let writes = plan(d, size)?;
        let mut f = std::fs::OpenOptions::new().write(true).open(dev).map_err(|e| format!("{dev}: {e}"))?;
        for (off, b) in writes {
            use std::os::unix::fs::FileExt;
            f.write_all_at(&b, off).map_err(|e| format!("{dev}: writing {} bytes at {off}: {e}", b.len()))?;
        }
        f.flush().map_err(|e| format!("{dev}: {e}"))?;
        f.sync_all().ok();
        Ok(())
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
            assert_eq!(gpt.backup, "ok");
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
        let d = crate::json::parse(r#"{"name": "wd0", "size_bytes": 8388608, "partition_table": {"type": "dos", "sector_size": 512, "disk_signature": "1234abcd"},
          "partitions": [{"number": 1, "start_lba": 63, "sectors": 16321, "type": "0xa6", "bootable": true}, {"number": 4, "start_lba": 16384, "sectors": 8, "type": "0x83"}]}"#).unwrap();
        let img = apply(&plan(&d, 8388608).unwrap(), 8388608);
        let Some(Table::Mbr(m)) = read_table(&MemSource(img), 512) else { panic!("no MBR") };
        assert_eq!(m.disk_signature, 0x1234abcd);
        assert_eq!(m.parts.iter().map(|p| (p.number, p.ptype, p.start, p.sectors, p.boot_flag)).collect::<Vec<_>>(), vec![(1, 0xa6, 63, 16321, 0x80), (4, 0x83, 16384, 8, 0)]);
        assert_eq!(guid_bytes("c12a7328-f81f-11d2-ba4b-00a0c93ec93b").unwrap()[..4], [0x28, 0x73, 0x2a, 0xc1]);
    }
}
