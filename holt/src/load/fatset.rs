//! `bko-holt fat-set DEV [--id XXXX-XXXX] [--media 0xNN] [--oem NAME]
//! [--hidden N]` (not in the usage: run by load's format stage on illumos
//! and Haiku, other/load.rs `other_newfs_msdos`).
//!
//! A FAT formatter that can't be told the source's volume ID, media byte,
//! OEM name or hidden-sector count (illumos' mkfs -F pcfs writes volume ID
//! 0, media 0xF8 and "MSWIN4.1"; Haiku's fat add-on a volume ID from the
//! clock, media 0xF8, "Haiku   " and the partition's size as its hidden
//! sectors) leaves a FAT that differs from the source's only in those
//! boot-sector fields.  This sets them in the FAT just made on DEV, in
//! place: in the boot sector (the volume ID at 39 or, for FAT32, 67; the
//! media byte at 21; the OEM name at 3..11; the hidden sectors at 28..32),
//! in FAT32's backup boot sector (`BPB_BkBootSec`) the same, and, for the
//! media byte, in the first byte of each FAT's entry 0, which repeats it.
//! Nothing else is written.  Every read and write is a whole sector at a
//! sector's offset, as illumos' raw (character) disk nodes take them.
//! First DEV must hold a FAT with an extended boot record (the volume ID's
//! place); after writing, it is read back and probed, and the probe must
//! show what was set.

use crate::bytes::{le16, le32};
use std::fs::{File, OpenOptions};
use std::os::unix::fs::FileExt;

/// What to set; None leaves a field as the formatter made it.
#[derive(Debug, Default, Clone, PartialEq)]
pub struct Set {
    pub id: Option<u32>,
    pub media: Option<u8>,
    pub oem: Option<[u8; 8]>,
    pub hidden: Option<u32>,
}

/// A FAT volume ID as bko-holt's probe prints it (XXXX-XXXX, hex), as the
/// boot sector's 32-bit number.
pub fn parse_id(s: &str) -> Option<u32> {
    let (hi, lo) = s.split_once('-')?;
    if hi.len() != 4
        || lo.len() != 4
        || !hi.bytes().chain(lo.bytes()).all(|c| c.is_ascii_hexdigit())
    {
        return None;
    }
    Some((u32::from_str_radix(hi, 16).ok()? << 16) | u32::from_str_radix(lo, 16).ok()?)
}

/// An OEM name as the probe prints it (its trailing spaces dropped): 1 to 8
/// printable ASCII characters, space-padded.
pub fn parse_oem(s: &str) -> Option<[u8; 8]> {
    if s.is_empty() || s.len() > 8 || !s.bytes().all(|c| (0x20..0x7f).contains(&c)) {
        return None;
    }
    let mut o = [b' '; 8];
    o[..s.len()].copy_from_slice(s.as_bytes());
    Some(o)
}

/// The boot sector's geometry, checked: bytes per sector, reserved sectors,
/// FAT count, sectors per FAT, where the extended boot record is, and the
/// backup boot sector (FAT32's, when it has one inside the reserved area).
#[derive(Debug, Clone, PartialEq)]
struct Geometry {
    bps: u64,
    reserved: u64,
    fats: u64,
    fatsz: u64,
    ebr: usize,
    backup: Option<u64>,
}

fn geometry(b: &[u8]) -> Result<Geometry, String> {
    if b.len() < 512 || b[510] != 0x55 || b[511] != 0xaa || !(b[0] == 0xeb || b[0] == 0xe9) {
        return Err("no FAT boot sector".into());
    }
    let bps = u64::from(le16(b, 11));
    let reserved = u64::from(le16(b, 14));
    let fats = u64::from(b[16]);
    let fatsz16 = u64::from(le16(b, 22));
    if !matches!(bps, 512 | 1024 | 2048 | 4096) || reserved == 0 || fats == 0 || fats > 4 {
        return Err("no FAT boot sector (its BPB isn't a FAT's)".into());
    }
    let fat32 = fatsz16 == 0;
    let fatsz = if fat32 {
        u64::from(le32(b, 36))
    } else {
        fatsz16
    };
    let ebr = if fat32 { 64 } else { 36 };
    if fatsz == 0 {
        return Err("no FAT boot sector (no FAT size)".into());
    }
    if b[ebr + 2] != 0x29 {
        return Err("the FAT has no extended boot record (signature 0x29), so no volume ID".into());
    }
    let bk = if fat32 { u64::from(le16(b, 50)) } else { 0 };
    Ok(Geometry {
        bps,
        reserved,
        fats,
        fatsz,
        ebr,
        backup: (bk != 0 && bk != 0xffff && bk < reserved).then_some(bk),
    })
}

/// S's fields written into boot sector B (the primary or its backup).
fn patch_boot(b: &mut [u8], g: &Geometry, s: &Set) {
    if let Some(id) = s.id {
        b[g.ebr + 3..g.ebr + 7].copy_from_slice(&id.to_le_bytes());
    }
    if let Some(m) = s.media {
        b[21] = m;
    }
    if let Some(o) = &s.oem {
        b[3..11].copy_from_slice(o);
    }
    if let Some(h) = s.hidden {
        b[28..32].copy_from_slice(&h.to_le_bytes());
    }
}

fn read(f: &File, off: u64, len: u64) -> Result<Vec<u8>, String> {
    let mut b = vec![0u8; len as usize];
    f.read_exact_at(&mut b, off)
        .map_err(|e| format!("reading {len} bytes at {off}: {e}"))?;
    Ok(b)
}

fn write(f: &File, off: u64, b: &[u8]) -> Result<(), String> {
    f.write_all_at(b, off)
        .map_err(|e| format!("writing {} bytes at {off}: {e}", b.len()))
}

/// S set in the FAT on F: what changed, as words for the log.
pub fn set(f: &File, s: &Set) -> Result<Vec<String>, String> {
    let first = read(f, 0, 512)?;
    let g = geometry(&first)?;
    let mut boot = if g.bps == 512 {
        first
    } else {
        read(f, 0, g.bps)?
    };
    let mut said = Vec::new();
    // Entry 0 of each FAT repeats the media byte: those first, the backup
    // boot sector next, the boot sector last, so a FAT whose boot sector
    // says the new media byte has it everywhere.
    if let Some(m) = s.media {
        for i in 0..g.fats {
            let off = (g.reserved + i * g.fatsz) * g.bps;
            let mut sec = read(f, off, g.bps)?;
            sec[0] = m;
            write(f, off, &sec)?;
        }
        said.push(format!("media 0x{m:02x} (boot sector and {} FATs)", g.fats));
    }
    if let Some(bk) = g.backup {
        let off = bk * g.bps;
        let mut sec = read(f, off, g.bps)?;
        // Only a backup that is the boot sector's copy (its signature and
        // BPB geometry) is one to keep in step.
        if geometry(&sec).is_ok_and(|x| x == g) {
            patch_boot(&mut sec, &g, s);
            write(f, off, &sec)?;
            said.push(format!("backup boot sector {bk}"));
        }
    }
    patch_boot(&mut boot, &g, s);
    write(f, 0, &boot)?;
    f.sync_all().map_err(|e| format!("sync: {e}"))?;
    if let Some(id) = s.id {
        said.insert(0, format!("volume ID {:04X}-{:04X}", id >> 16, id & 0xffff));
    }
    if let Some(o) = &s.oem {
        said.push(format!(
            "OEM name {:?}",
            String::from_utf8_lossy(o).trim_end()
        ));
    }
    if let Some(h) = s.hidden {
        said.push(format!("hidden sectors {h}"));
    }
    // Read back: the boot sector as written, and the probe's view of it.
    let back = read(f, 0, g.bps)?;
    if back != boot {
        return Err("the boot sector read back isn't what was written".into());
    }
    let mut probe_bytes = back;
    probe_bytes.resize(512.max(g.bps as usize), 0);
    let c = crate::probe::fat::probe(&crate::bytes::MemSource(probe_bytes))
        .ok_or("the FAT doesn't probe as one after writing")?;
    if let Some(id) = s.id {
        let want = format!("{:04X}-{:04X}", id >> 16, id & 0xffff);
        if c.uuid.as_deref() != Some(want.as_str()) {
            return Err(format!(
                "its volume ID reads back as {:?}, not {want}",
                c.uuid
            ));
        }
    }
    Ok(said)
}

const USAGE: &str =
    "usage: bko-holt fat-set DEV [--id XXXX-XXXX] [--media 0xNN] [--oem NAME] [--hidden N]";

/// The arguments: DEV and what to set, or None when they aren't a usage.
pub fn parse(args: &[String]) -> Option<(String, Set)> {
    let (dev, rest) = args.split_first()?;
    if dev.starts_with('-') || rest.len() % 2 != 0 {
        return None;
    }
    let mut s = Set::default();
    for p in rest.chunks(2) {
        let v = p[1].as_str();
        match p[0].as_str() {
            "--id" if s.id.is_none() => s.id = Some(parse_id(v)?),
            "--media" if s.media.is_none() => {
                let m = u8::from_str_radix(v.strip_prefix("0x")?, 16).ok()?;
                // A FAT's media byte: 0xF0, or 0xF8 to 0xFF.
                if !(m == 0xf0 || m >= 0xf8) {
                    return None;
                }
                s.media = Some(m);
            }
            "--oem" if s.oem.is_none() => s.oem = Some(parse_oem(v)?),
            "--hidden" if s.hidden.is_none() => s.hidden = Some(v.parse().ok()?),
            _ => return None,
        }
    }
    (s != Set::default()).then_some((dev.clone(), s))
}

pub fn main(args: &[String]) -> u8 {
    let Some((dev, s)) = parse(args) else {
        eprintln!("{USAGE}");
        return 2;
    };
    let r = OpenOptions::new()
        .read(true)
        .write(true)
        .open(&dev)
        .map_err(|e| format!("{dev}: {e}"))
        .and_then(|f| set(&f, &s).map_err(|e| format!("{dev}: {e}")));
    match r {
        Ok(said) => {
            println!("fat-set: {dev}: {}", said.join(", "));
            0
        }
        Err(e) => {
            eprintln!("bko-holt fat-set: {e}");
            1
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::probe::fat::sample::{fat16, fat32};

    fn file_with(tag: &str, img: &[u8]) -> (crate::testutil::TempDir, std::path::PathBuf) {
        let t = crate::testutil::TempDir::new(tag);
        let p = t.path().join("fat.img");
        std::fs::write(&p, img).unwrap();
        (t, p)
    }

    /// A FAT32 as mkfs -F pcfs leaves one (volume ID 0, "MSWIN4.1", media
    /// 0xF8, a backup boot sector at 6, FAT entry 0 0x0FFFFFF8 in both
    /// FATs): the source's ID, media, OEM name and hidden sectors set in
    /// the boot sector, its backup and both FATs, and nothing else.
    #[test]
    fn sets_a_fat32s_fields_everywhere_they_are() {
        let mut img = fat32(70000, 0, "NONAME");
        img[3..11].copy_from_slice(b"MSWIN4.1");
        img[28..32].copy_from_slice(&0u32.to_le_bytes());
        let boot: Vec<u8> = img[..512].to_vec();
        img[6 * 512..7 * 512].copy_from_slice(&boot);
        let fatsz = le32(&img, 36) as usize;
        for i in 0..2 {
            let o = (32 + i * fatsz) * 512;
            img[o..o + 8].copy_from_slice(&[0xf8, 0xff, 0xff, 0x0f, 0xff, 0xff, 0xff, 0x0f]);
        }
        let (_t, p) = file_with("fatset-32", &img);
        let f = OpenOptions::new().read(true).write(true).open(&p).unwrap();
        let s = Set {
            id: Some(0xbc00_0001),
            media: Some(0xf0),
            oem: parse_oem("BSD4.4"),
            hidden: Some(40),
        };
        let said = set(&f, &s).unwrap();
        assert_eq!(
            said,
            [
                "volume ID BC00-0001",
                "media 0xf0 (boot sector and 2 FATs)",
                "backup boot sector 6",
                "OEM name \"BSD4.4\"",
                "hidden sectors 40"
            ]
        );
        let out = std::fs::read(&p).unwrap();
        for sec in [0, 6] {
            let b = &out[sec * 512..(sec + 1) * 512];
            assert_eq!(
                (le32(b, 67), b[21], &b[3..11], le32(b, 28)),
                (0xbc00_0001, 0xf0, &b"BSD4.4  "[..], 40),
                "sector {sec}"
            );
        }
        for i in 0..2 {
            let o = (32 + i * fatsz) * 512;
            assert_eq!(
                &out[o..o + 8],
                &[0xf0, 0xff, 0xff, 0x0f, 0xff, 0xff, 0xff, 0x0f]
            );
        }
        // Every other byte is as it was.
        let mut want = img.clone();
        for sec in [0, 6] {
            let b = &mut want[sec * 512..(sec + 1) * 512];
            b[67..71].copy_from_slice(&0xbc00_0001u32.to_le_bytes());
            b[21] = 0xf0;
            b[3..11].copy_from_slice(b"BSD4.4  ");
            b[28..32].copy_from_slice(&40u32.to_le_bytes());
        }
        for i in 0..2 {
            want[(32 + i * fatsz) * 512] = 0xf0;
        }
        assert!(out == want);
        let c = crate::probe::fat::probe(&crate::bytes::MemSource(out)).unwrap();
        assert_eq!(c.uuid.as_deref(), Some("BC00-0001"));
    }

    /// FAT16: the volume ID at 39, no backup boot sector; the volume ID
    /// alone leaves the FATs as they are.
    #[test]
    fn sets_a_fat16s_volume_id() {
        let img = fat16(8192, 0x1234_5678, "ESP");
        let (_t, p) = file_with("fatset-16", &img);
        let f = OpenOptions::new().read(true).write(true).open(&p).unwrap();
        assert_eq!(
            set(
                &f,
                &Set {
                    id: parse_id("0A0B-0C0D"),
                    ..Set::default()
                }
            )
            .unwrap(),
            ["volume ID 0A0B-0C0D"]
        );
        let out = std::fs::read(&p).unwrap();
        assert_eq!(le32(&out, 39), 0x0a0b_0c0d);
        let mut want = img.clone();
        want[39..43].copy_from_slice(&0x0a0b_0c0du32.to_le_bytes());
        assert!(out == want);
    }

    /// Not a FAT, or one without an extended boot record: refused, nothing
    /// written.
    #[test]
    fn refuses_what_isnt_a_fat_with_a_volume_id() {
        let (_t, p) = file_with("fatset-none", &vec![0u8; 8192]);
        let f = OpenOptions::new().read(true).write(true).open(&p).unwrap();
        assert_eq!(
            set(
                &f,
                &Set {
                    id: Some(1),
                    ..Set::default()
                }
            )
            .unwrap_err(),
            "no FAT boot sector"
        );
        let mut img = fat16(8192, 1, "X");
        img[38] = 0;
        let (_t2, p2) = file_with("fatset-noebr", &img);
        let f = OpenOptions::new().read(true).write(true).open(&p2).unwrap();
        assert!(
            set(
                &f,
                &Set {
                    id: Some(1),
                    ..Set::default()
                }
            )
            .unwrap_err()
            .contains("no extended boot record")
        );
        assert!(std::fs::read(&p2).unwrap() == img);
    }

    #[test]
    fn arguments() {
        let a = |s: &str| s.split_whitespace().map(str::to_string).collect::<Vec<_>>();
        assert_eq!(
            parse(&a(
                "/dev/rdsk/c1t0d0s0 --id BC00-0001 --media 0xf0 --oem BSD4.4 --hidden 40"
            )),
            Some((
                "/dev/rdsk/c1t0d0s0".into(),
                Set {
                    id: Some(0xbc00_0001),
                    media: Some(0xf0),
                    oem: parse_oem("BSD4.4"),
                    hidden: Some(40)
                }
            ))
        );
        for bad in [
            "",
            "/dev/x",
            "--id BC00-0001",
            "/dev/x --id BC000001",
            "/dev/x --media f8",
            "/dev/x --media 0x12",
            "/dev/x --id BC00-0001 --id BC00-0002",
            "/dev/x --oem 123456789",
            "/dev/x --hidden -1",
            "/dev/x --size 1",
        ] {
            assert_eq!(parse(&a(bad)), None, "{bad:?}");
        }
        assert_eq!(parse_id("0000-0000"), Some(0));
        assert_eq!(parse_oem("MSWIN4.1"), Some(*b"MSWIN4.1"));
    }
}
