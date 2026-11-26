//! `bko-holt grub-prefix PART FILE...` (not in the usage: run by load's
//! boot stage on Guix System on UEFI, store.rs `efi_prefix_by_number`).
//!
//! grub-install embeds GRUB's prefix, where it finds /boot/grub and
//! normal.mod, in the image it writes; when the ESP is on /boot's own disk
//! and nothing between (no LUKS, LVM or RAID) needs a search, it names
//! /boot's partition by its number: Guix 1.5.0's grubx64.efi has
//! "(,gpt3)/boot/grub" (`BackShot2`, docs/design/guix.md).  grub.cfg's
//! `search --fs-uuid` runs only after that prefix has loaded normal.mod.
//! load doesn't reinstall a store system's GRUB (its store has none), so
//! the restored image is the source's: on a layout where /boot's
//! partition has another number it would stop in grub rescue ("no such
//! partition").  This sets the prefix's partition to PART ("gpt5",
//! "msdos2"), the restored /boot's, in each FILE that is a GRUB EFI image
//! whose prefix names a partition by number, in place: the same length of
//! file, only the prefix's bytes (NUL-padded in its module) changed, and
//! the file's access and modification times put back.  A prefix that
//! already names PART, or names no partition by number (`($root)` after a
//! search), a FILE that is no GRUB image, or isn't there, is left as it
//! is: a restore onto the source's own numbers changes nothing.  A prefix
//! the new number doesn't fit in fails, naming the file, as a GRUB image
//! whose module info is broken does (cut short, its modules past the mods
//! section, its modules' offset past their end, a module header past it):
//! `bko-holt grub-prefix: FILE: WHY` and exit status 1, the file unchanged.
//!
//! The image: a PE file whose "mods" section begins with GRUB's module
//! info (magic "mimg"; 64-bit: magic, padding, offset and size as u64s;
//! 32-bit: magic, offset, size), then module headers (type, size: u32s,
//! size counting the header), the prefix being type 3 (`OBJ_TYPE_PREFIX`).

use std::fs::{File, FileTimes, OpenOptions};
use std::io::{Read, Seek, SeekFrom, Write};

const MAGIC: u32 = 0x676d_696d; // "mimg"
const OBJ_TYPE_PREFIX: u32 = 3;
const MAX_IMAGE: u64 = 64 << 20;

fn u16_at(b: &[u8], o: usize) -> Option<u16> {
    Some(u16::from_le_bytes(
        b.get(o..o.checked_add(2)?)?.try_into().ok()?,
    ))
}

fn u32_at(b: &[u8], o: usize) -> Option<u32> {
    Some(u32::from_le_bytes(
        b.get(o..o.checked_add(4)?)?.try_into().ok()?,
    ))
}

fn u64_at(b: &[u8], o: usize) -> Option<u64> {
    Some(u64::from_le_bytes(
        b.get(o..o.checked_add(8)?)?.try_into().ok()?,
    ))
}

/// The prefix of a GRUB EFI image B: (the offset of its string, its room
/// in bytes with the NUL padding, the string).  Ok(None): B is no GRUB EFI
/// image (no PE, no mods section, no module info), or has no prefix.
pub fn efi_prefix(b: &[u8]) -> Result<Option<(usize, usize, String)>, String> {
    if b.get(0..2) != Some(b"MZ") {
        return Ok(None);
    }
    let Some(pe) = u32_at(b, 0x3c).map(|x| x as usize) else {
        return Ok(None);
    };
    if b.get(pe..pe.saturating_add(4)) != Some(b"PE\0\0") {
        return Ok(None);
    }
    let (Some(nsec), Some(optsz), Some(opt_magic)) =
        (u16_at(b, pe + 6), u16_at(b, pe + 20), u16_at(b, pe + 24))
    else {
        return Ok(None);
    };
    let wide = match opt_magic {
        0x20b => true,
        0x10b => false,
        _ => return Ok(None),
    };
    let table = pe + 24 + optsz as usize;
    let mods = (0..nsec as usize).find_map(|k| {
        let at = table + 40 * k;
        let name = b.get(at..at + 8)?;
        (name.split(|c| *c == 0).next() == Some(b"mods".as_slice()))
            .then(|| (u32_at(b, at + 16), u32_at(b, at + 20)))
    });
    let Some((Some(raw_size), Some(raw))) = mods else {
        return Ok(None);
    };
    let raw = raw as usize;
    // A section past the end of the address space is past the file's too.
    let Some(raw_end) = raw.checked_add(raw_size as usize) else {
        return Ok(None);
    };
    if raw_end > b.len() || u32_at(b, raw) != Some(MAGIC) {
        return Ok(None);
    }
    let (off, size) = if wide {
        (u64_at(b, raw + 8), u64_at(b, raw + 16))
    } else {
        (
            u32_at(b, raw + 4).map(u64::from),
            u32_at(b, raw + 8).map(u64::from),
        )
    };
    let (Some(off), Some(size)) = (off, size) else {
        return Err("the module info is cut short".into());
    };
    let end = raw
        .checked_add(usize::try_from(size).map_err(|_| "the modules' size is past this machine's")?)
        .ok_or("the modules' size overflows")?;
    if end > raw_end {
        return Err(format!(
            "the modules ({size} bytes) run past the mods section ({raw_size} bytes)"
        ));
    }
    let p = raw
        .checked_add(
            usize::try_from(off).map_err(|_| "the modules' offset is past this machine's")?,
        )
        .ok_or("the modules' offset overflows")?;
    // At END: no modules.  Past it: the module info is broken, as a module
    // header past the modules' end is (RustAudit1 HD1-04).
    if p > end {
        return Err(format!(
            "the modules' offset ({off:#x}) is past their end ({size} bytes from the module info)"
        ));
    }
    module_prefix(b, p, end)
}

/// The prefix module among the modules of B from P to END (module headers:
/// type, size, both u32s, the size counting the header): (the offset of
/// its string, its room with the NUL padding, the string), or None.  A P
/// at END has no modules: None.  `efi_prefix` never gives a P past END (that
/// is its error), but this checks its sums anyway, so a P near `usize::MAX`
/// is None too and never an add overflow.
fn module_prefix(
    b: &[u8],
    mut p: usize,
    end: usize,
) -> Result<Option<(usize, usize, String)>, String> {
    while p.checked_add(8).is_some_and(|q| q <= end) {
        let (Some(t), Some(sz)) = (u32_at(b, p), u32_at(b, p + 4)) else {
            break;
        };
        let sz = sz as usize;
        if sz < 8 || p.checked_add(sz).is_none_or(|e| e > end) {
            return Err(format!(
                "a module header at {p:#x} has size {sz}, past the modules' end"
            ));
        }
        if t == OBJ_TYPE_PREFIX {
            let body = &b[p + 8..p + sz];
            let len = body
                .iter()
                .position(|c| *c == 0)
                .ok_or("the prefix has no NUL in its module")?;
            let text = std::str::from_utf8(&body[..len]).map_err(|_| "the prefix isn't UTF-8")?;
            return Ok(Some((p + 8, sz - 8, text.to_string())));
        }
        p += sz;
    }
    Ok(None)
}

// GRUB's i386-pc core.img (include/grub/offsets.h): diskboot.img's sector,
// then lzma_decompress.img, whose header has these fields; the kernel and
// modules after it, LZMA-compressed; then the Reed-Solomon redundancy.
const CORE_DISKBOOT: usize = 512;
const CORE_COMPRESSED_SIZE: usize = 0x08;
const CORE_UNCOMPRESSED_SIZE: usize = 0x0c;
const CORE_RS_REDUNDANCY: usize = 0x10;
const MAX_CORE: usize = 16 << 20;

/// The prefix of GRUB's i386-pc core.img B (what grub-install wrote to
/// /boot/grub/i386-pc/core.img and the BIOS boot partition): its kernel and
/// modules are LZMA-compressed (lc=3 lp=0 pb=2, grub-mkimage's), so they
/// are decoded (lzma.rs) and the module info sought in them ("mimg" with
/// the 32-bit layout, ending within them: the kernel's code holds the magic
/// too).  The compressed part starts at the file's end less its size and
/// the redundancy, so B must be the whole file.  Ok(None): decoded, and no
/// prefix module.  Err: B isn't such a core.img.
pub fn core_prefix(b: &[u8]) -> Result<Option<String>, String> {
    let h = CORE_DISKBOOT;
    let (Some(comp), Some(unc), Some(red)) = (
        u32_at(b, h + CORE_COMPRESSED_SIZE),
        u32_at(b, h + CORE_UNCOMPRESSED_SIZE),
        u32_at(b, h + CORE_RS_REDUNDANCY),
    ) else {
        return Err(format!(
            "{} bytes: too short for a GRUB i386-pc core.img",
            b.len()
        ));
    };
    let (comp, unc, red) = (comp as usize, unc as usize, red as usize);
    let start = b
        .len()
        .checked_sub(comp)
        .and_then(|x| x.checked_sub(red))
        .filter(|s| *s >= h + 0x20 && comp > 0 && unc > 0 && unc <= MAX_CORE);
    let Some(start) = start else {
        return Err(format!(
            "not a GRUB i386-pc core.img: its header's sizes (compressed {comp}, uncompressed {unc}, redundancy {red}) don't fit its {} bytes",
            b.len()
        ));
    };
    let out = crate::lzma::decode(&b[start..start + comp], 3, 0, 2, unc)
        .map_err(|e| format!("its compressed kernel and modules: {e}"))?;
    let mut i = 0;
    while let Some(k) = out
        .get(i..)
        .and_then(|r| r.windows(4).position(|w| w == MAGIC.to_le_bytes()))
    {
        let at = i + k;
        i = at + 1;
        let (Some(off), Some(size)) = (u32_at(&out, at + 4), u32_at(&out, at + 8)) else {
            continue;
        };
        let (off, size) = (off as usize, size as usize);
        let Some(end) = at
            .checked_add(size)
            .filter(|e| *e <= out.len() && off >= 12 && off <= size)
        else {
            continue;
        };
        if let Ok(Some((_, _, p))) = module_prefix(&out, at + off, end) {
            return Ok(Some(p));
        }
    }
    Ok(None)
}

/// The partition PREFIX names by number ("gpt3" of "(,gpt3)/@/boot/grub"),
/// as `with_partition` reads it; None for "($root)/...", nested partitions
/// and the like.
pub fn numbered_partition(prefix: &str) -> Option<String> {
    let inner = prefix.strip_prefix('(')?;
    let drive = &inner[..inner.find(')')?];
    let (_, p) = drive.split_once(',')?;
    with_partition(prefix, "gpt1").map(|_| p.to_string())
}

/// PREFIX with its drive's partition set to PART ("gpt5"): Some when its
/// drive names one partition by number ("(,gpt3)", "(hd0,msdos1)"), else
/// None ("($root)", no drive, nested partitions).
pub fn with_partition(prefix: &str, part: &str) -> Option<String> {
    let inner = prefix.strip_prefix('(')?;
    let close = inner.find(')')?;
    let (drive, rest) = (&inner[..close], &inner[close + 1..]);
    let (disk, p) = drive.split_once(',')?;
    if disk.starts_with('$') || p.contains(',') {
        return None;
    }
    let num = p.strip_prefix("gpt").or_else(|| p.strip_prefix("msdos"))?;
    if num.is_empty() || !num.bytes().all(|c| c.is_ascii_digit()) {
        return None;
    }
    Some(format!("({disk},{part}){rest}"))
}

/// A valid PART: "gpt" or "msdos", then a partition number.
pub fn valid_part(part: &str) -> bool {
    let num = part
        .strip_prefix("gpt")
        .or_else(|| part.strip_prefix("msdos"));
    num.is_some_and(|n| {
        !n.is_empty() && !n.starts_with('0') && n.bytes().all(|c| c.is_ascii_digit())
    })
}

#[derive(Debug, PartialEq)]
pub enum Outcome {
    /// Not a GRUB EFI image, or one without a prefix.
    NoPrefix,
    /// Its prefix names no partition by number.
    NotNumbered(String),
    /// Its prefix names PART already.
    Same(String),
    /// Rewritten: (the offset of the prefix's room, its new bytes).
    Rewrite {
        from: String,
        to: String,
        at: usize,
        bytes: Vec<u8>,
    },
}

/// What setting B's prefix to PART would do (B unchanged).
pub fn plan(b: &[u8], part: &str) -> Result<Outcome, String> {
    let Some((at, room, old)) = efi_prefix(b)? else {
        return Ok(Outcome::NoPrefix);
    };
    let Some(new) = with_partition(&old, part) else {
        return Ok(Outcome::NotNumbered(old));
    };
    if new == old {
        return Ok(Outcome::Same(old));
    }
    if new.len() + 1 > room {
        return Err(format!(
            "the prefix {old} has room for {} bytes, and {new} needs {}",
            room - 1,
            new.len()
        ));
    }
    let mut bytes = new.clone().into_bytes();
    bytes.resize(room, 0);
    Ok(Outcome::Rewrite {
        from: old,
        to: new,
        at,
        bytes,
    })
}

fn one(path: &str, part: &str) -> Result<String, String> {
    let mut f = match File::open(path) {
        Ok(f) => f,
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => {
            return Ok(format!("{path}: not there; nothing to do"));
        }
        Err(e) => return Err(format!("{path}: {e}")),
    };
    let meta = f.metadata().map_err(|e| format!("{path}: {e}"))?;
    if !meta.is_file() || meta.len() > MAX_IMAGE {
        return Ok(format!(
            "{path}: not a GRUB image (not a file, or over {} MiB); left as it is",
            MAX_IMAGE >> 20
        ));
    }
    let mut b = Vec::with_capacity(meta.len() as usize);
    f.read_to_end(&mut b).map_err(|e| format!("{path}: {e}"))?;
    drop(f);
    match plan(&b, part).map_err(|e| format!("{path}: {e}"))? {
        Outcome::NoPrefix => Ok(format!("{path}: no GRUB prefix in it; left as it is")),
        Outcome::NotNumbered(p) => Ok(format!(
            "{path}: its prefix {p} names no partition by number; left as it is"
        )),
        Outcome::Same(p) => Ok(format!(
            "{path}: its prefix {p} already names {part}; left as it is"
        )),
        Outcome::Rewrite {
            from,
            to,
            at,
            bytes,
        } => {
            let (atime, mtime) = (
                meta.accessed().map_err(|e| format!("{path}: {e}"))?,
                meta.modified().map_err(|e| format!("{path}: {e}"))?,
            );
            let mut w = OpenOptions::new()
                .write(true)
                .open(path)
                .map_err(|e| format!("{path}: {e}"))?;
            w.seek(SeekFrom::Start(at as u64))
                .and_then(|_| w.write_all(&bytes))
                .and_then(|_| w.sync_all())
                .map_err(|e| format!("{path}: writing its prefix: {e}"))?;
            w.set_times(FileTimes::new().set_accessed(atime).set_modified(mtime))
                .map_err(|e| format!("{path}: putting its times back: {e}"))?;
            Ok(format!("{path}: prefix {from} -> {to}"))
        }
    }
}

/// `bko-holt grub-prefix PART FILE...`.
pub fn main(args: &[String]) -> u8 {
    let Some((part, files)) = args
        .split_first()
        .filter(|(p, f)| valid_part(p) && !f.is_empty())
    else {
        eprintln!("usage: bko-holt grub-prefix gptN|msdosN FILE...");
        return 2;
    };
    let mut rc = 0;
    for f in files {
        match one(f, part) {
            Ok(m) => println!("grub-prefix: {m}"),
            Err(e) => {
                eprintln!("bko-holt grub-prefix: {e}");
                rc = 1;
            }
        }
    }
    rc
}

#[cfg(test)]
mod tests {
    use super::*;

    /// A GRUB EFI image as grub-mkimage lays one out: a PE32+ with .text and
    /// mods, the module info, an ELF module and the prefix (padded to ROOM).
    pub(super) fn image(prefix: Option<&str>, room: usize, wide: bool) -> Vec<u8> {
        let mut b = vec![0u8; 0x400];
        b[0..2].copy_from_slice(b"MZ");
        let pe = 0x80usize;
        b[0x3c..0x40].copy_from_slice(&(pe as u32).to_le_bytes());
        b[pe..pe + 4].copy_from_slice(b"PE\0\0");
        b[pe + 6..pe + 8].copy_from_slice(&2u16.to_le_bytes());
        let optsz: u16 = if wide { 240 } else { 224 };
        b[pe + 20..pe + 22].copy_from_slice(&optsz.to_le_bytes());
        b[pe + 24..pe + 26].copy_from_slice(&(if wide { 0x20bu16 } else { 0x10b }).to_le_bytes());
        let table = pe + 24 + optsz as usize;
        let mut mods = Vec::new();
        mods.extend_from_slice(&MAGIC.to_le_bytes());
        let hdr = if wide { 24 } else { 12 };
        let mut body = Vec::new();
        body.extend_from_slice(&0u32.to_le_bytes());
        body.extend_from_slice(&24u32.to_le_bytes());
        body.extend_from_slice(&[0x7f, b'E', b'L', b'F'].repeat(4));
        if let Some(p) = prefix {
            body.extend_from_slice(&OBJ_TYPE_PREFIX.to_le_bytes());
            body.extend_from_slice(&((8 + room) as u32).to_le_bytes());
            let mut s = p.as_bytes().to_vec();
            s.resize(room, 0);
            body.extend_from_slice(&s);
        }
        let size = (hdr + body.len()) as u64;
        if wide {
            mods.extend_from_slice(&0u32.to_le_bytes());
            mods.extend_from_slice(&(hdr as u64).to_le_bytes());
            mods.extend_from_slice(&size.to_le_bytes());
        } else {
            mods.extend_from_slice(&(hdr as u32).to_le_bytes());
            mods.extend_from_slice(&(size as u32).to_le_bytes());
        }
        mods.extend_from_slice(&body);
        let raw = b.len();
        let sect = |name: &[u8], raw: usize, len: usize| {
            let mut s = [0u8; 40];
            s[..name.len()].copy_from_slice(name);
            s[16..20].copy_from_slice(&(len as u32).to_le_bytes());
            s[20..24].copy_from_slice(&(raw as u32).to_le_bytes());
            s
        };
        b[table..table + 40].copy_from_slice(&sect(b".text", 0x200, 0x100));
        b[table + 40..table + 80].copy_from_slice(&sect(b"mods", raw, mods.len()));
        b.extend_from_slice(&mods);
        b.extend_from_slice(&[0xcc; 64]);
        b
    }

    #[test]
    fn finds_the_prefix() {
        for wide in [true, false] {
            let b = image(Some("(,gpt3)/boot/grub"), 24, wide);
            let (at, room, p) = efi_prefix(&b).unwrap().unwrap();
            assert_eq!((room, p.as_str()), (24, "(,gpt3)/boot/grub"));
            assert_eq!(&b[at..at + 7], b"(,gpt3)");
        }
        assert_eq!(efi_prefix(&image(None, 0, true)).unwrap(), None);
        assert_eq!(efi_prefix(b"not a PE file at all").unwrap(), None);
        assert_eq!(efi_prefix(&[]).unwrap(), None);
    }

    #[test]
    fn a_broken_module_list_is_an_error() {
        let mut b = image(Some("(,gpt3)/boot/grub"), 24, true);
        let (at, _, _) = efi_prefix(&b).unwrap().unwrap();
        b[at - 4..at].copy_from_slice(&4000u32.to_le_bytes());
        assert!(
            efi_prefix(&b)
                .unwrap_err()
                .contains("past the modules' end")
        );
    }

    /// `RustAudit1` HD1-04: a module-info offset past the modules' end (up to
    /// the top of the address space, or a 32-bit image's `u32::MAX`) is an
    /// error, not "no prefix", and never an add overflow; one past usize is
    /// the overflow error it was; one at the end is no modules, no prefix.
    #[test]
    fn a_huge_module_offset_is_an_error() {
        let raw = 0x400usize;
        let mut b = image(Some("(,gpt3)/boot/grub"), 24, true);
        assert_eq!(u32_at(&b, raw), Some(MAGIC));
        let size = u64_at(&b, raw + 16).unwrap();
        for p in [
            raw + size as usize + 1,
            usize::MAX - 15,
            usize::MAX - 7,
            usize::MAX,
        ] {
            let off = (p - raw) as u64;
            b[raw + 8..raw + 16].copy_from_slice(&off.to_le_bytes());
            assert_eq!(
                efi_prefix(&b),
                Err(format!(
                    "the modules' offset ({off:#x}) is past their end ({size} bytes from the module info)"
                )),
                "{p:#x}"
            );
            assert!(matches!(plan(&b, "gpt5"), Err(e) if e.contains("is past their end")));
        }
        b[raw + 8..raw + 16].copy_from_slice(&size.to_le_bytes());
        assert_eq!(efi_prefix(&b), Ok(None));
        b[raw + 8..raw + 16].copy_from_slice(&(u64::MAX - 8).to_le_bytes());
        assert!(efi_prefix(&b).unwrap_err().contains("offset overflows"));
        let mut b = image(Some("(,gpt3)/boot/grub"), 24, false);
        let size = u32_at(&b, raw + 8).unwrap();
        b[raw + 4..raw + 8].copy_from_slice(&u32::MAX.to_le_bytes());
        assert_eq!(
            efi_prefix(&b),
            Err(format!(
                "the modules' offset (0xffffffff) is past their end ({size} bytes from the module info)"
            ))
        );
        assert_eq!(module_prefix(&b, usize::MAX - 3, usize::MAX), Ok(None));
        assert_eq!(module_prefix(&b, usize::MAX - 15, usize::MAX - 8), Ok(None));
    }

    const CORE_GPT3: &[u8] = include_bytes!("../../tests/fixtures/lzma/core-gpt3.img");
    const CORE_ROOT: &[u8] = include_bytes!("../../tests/fixtures/lzma/core-root.img");

    #[test]
    fn reads_a_core_img_prefix() {
        assert_eq!(
            core_prefix(CORE_GPT3).unwrap().as_deref(),
            Some("(,gpt3)/@/boot/grub")
        );
        assert_eq!(
            core_prefix(CORE_ROOT).unwrap().as_deref(),
            Some("($root)/boot/grub")
        );
        // Not the whole file (the compressed part is found from its end), or not a core.img.
        assert!(core_prefix(&CORE_GPT3[..CORE_GPT3.len() - 1]).is_err());
        assert!(core_prefix(b"short").unwrap_err().contains("too short"));
        assert!(core_prefix(&[0u8; 4096]).is_err());
        let mut b = CORE_GPT3.to_vec();
        b[CORE_DISKBOOT + CORE_UNCOMPRESSED_SIZE..CORE_DISKBOOT + CORE_UNCOMPRESSED_SIZE + 4]
            .copy_from_slice(&u32::MAX.to_le_bytes());
        assert!(core_prefix(&b).is_err());
        assert_eq!(
            numbered_partition("(,gpt3)/@/boot/grub").as_deref(),
            Some("gpt3")
        );
        assert_eq!(
            numbered_partition("(hd0,msdos12)/boot/grub").as_deref(),
            Some("msdos12")
        );
        assert_eq!(numbered_partition("($root)/boot/grub"), None);
        assert_eq!(numbered_partition("(,msdos1,bsd1)/boot/grub"), None);
        assert_eq!(numbered_partition("/boot/grub"), None);
    }

    /// guix15-bios's real core.img (GRUB 2.12 from Guix 1.5.0), not kept in
    /// the tree: `BKO_CORE_IMG=PATH cargo test real_core_img -- --ignored`.
    #[test]
    #[ignore]
    fn real_core_img() {
        let p = std::env::var("BKO_CORE_IMG").expect("BKO_CORE_IMG");
        let b = std::fs::read(&p).unwrap();
        let want =
            std::env::var("BKO_CORE_PREFIX").unwrap_or_else(|_| "(,gpt3)/@/boot/grub".into());
        assert_eq!(core_prefix(&b).unwrap().as_deref(), Some(want.as_str()));
    }

    #[test]
    fn the_partition_is_set() {
        assert_eq!(
            with_partition("(,gpt3)/boot/grub", "gpt5").as_deref(),
            Some("(,gpt5)/boot/grub")
        );
        assert_eq!(
            with_partition("(,gpt3)/@/boot/grub", "gpt12").as_deref(),
            Some("(,gpt12)/@/boot/grub")
        );
        assert_eq!(
            with_partition("(hd0,msdos1)/boot/grub", "msdos2").as_deref(),
            Some("(hd0,msdos2)/boot/grub")
        );
        assert_eq!(with_partition("($root)/boot/grub", "gpt5"), None);
        assert_eq!(with_partition("/boot/grub", "gpt5"), None);
        assert_eq!(with_partition("(,msdos1,bsd1)/boot/grub", "gpt5"), None);
        assert_eq!(with_partition("(lvm/vg-root)/boot/grub", "gpt5"), None);
        assert_eq!(with_partition("(,gptx)/boot/grub", "gpt5"), None);
        assert!(valid_part("gpt5") && valid_part("msdos12"));
        assert!(
            !valid_part("gpt")
                && !valid_part("gpt05")
                && !valid_part("hd0")
                && !valid_part("gpt5;rm")
        );
    }

    #[test]
    fn plans_each_case() {
        let b = image(Some("(,gpt3)/boot/grub"), 24, true);
        assert_eq!(
            plan(&b, "gpt3").unwrap(),
            Outcome::Same("(,gpt3)/boot/grub".into())
        );
        let Outcome::Rewrite {
            from,
            to,
            at,
            bytes,
        } = plan(&b, "gpt128").unwrap()
        else {
            panic!()
        };
        assert_eq!(
            (from.as_str(), to.as_str(), bytes.len()),
            ("(,gpt3)/boot/grub", "(,gpt128)/boot/grub", 24)
        );
        assert_eq!(&bytes[19..], &[0u8; 5]);
        assert_eq!(&b[at..at + 3], b"(,g");
        assert_eq!(
            plan(&image(Some("($root)/boot/grub"), 24, true), "gpt5").unwrap(),
            Outcome::NotNumbered("($root)/boot/grub".into())
        );
        assert_eq!(
            plan(&image(None, 0, true), "gpt5").unwrap(),
            Outcome::NoPrefix
        );
        // "(,gpt3)/boot/grub" in exactly its room: a longer number has none.
        let tight = image(Some("(,gpt3)/boot/grub"), 18, true);
        assert!(
            plan(&tight, "gpt12")
                .unwrap_err()
                .contains("has room for 17 bytes")
        );
        assert!(matches!(
            plan(&tight, "gpt7").unwrap(),
            Outcome::Rewrite { .. }
        ));
    }

    #[test]
    fn rewrites_in_place_and_keeps_the_times() {
        let d = std::env::temp_dir().join(format!("bko-grub-prefix-{}", std::process::id()));
        std::fs::create_dir_all(&d).unwrap();
        let (img, other, gone) = (
            d.join("grubx64.efi"),
            d.join("BOOTX64.EFI"),
            d.join("none.efi"),
        );
        let b = image(Some("(,gpt3)/boot/grub"), 24, true);
        std::fs::write(&img, &b).unwrap();
        std::fs::write(&other, b"MZ but nothing else").unwrap();
        let t = std::time::UNIX_EPOCH + std::time::Duration::from_secs(1_700_000_000);
        File::options()
            .write(true)
            .open(&img)
            .unwrap()
            .set_times(FileTimes::new().set_accessed(t).set_modified(t))
            .unwrap();
        let p = |x: &std::path::Path| x.to_string_lossy().into_owned();
        assert_eq!(main(&["gpt5".into(), p(&img), p(&other), p(&gone)]), 0);
        let after = std::fs::read(&img).unwrap();
        assert_eq!(after.len(), b.len());
        assert_eq!(efi_prefix(&after).unwrap().unwrap().2, "(,gpt5)/boot/grub");
        let diff: Vec<usize> = (0..b.len()).filter(|&i| b[i] != after[i]).collect();
        let (at, _, _) = efi_prefix(&b).unwrap().unwrap();
        assert!(
            diff.iter().all(|&i| i >= at && i < at + 24),
            "only the prefix's bytes change: {diff:?}"
        );
        assert_eq!(std::fs::metadata(&img).unwrap().modified().unwrap(), t);
        assert_eq!(std::fs::read(&other).unwrap(), b"MZ but nothing else");
        // Again: nothing to do, nothing written.
        assert_eq!(main(&["gpt5".into(), p(&img)]), 0);
        assert_eq!(std::fs::read(&img).unwrap(), after);
        assert_eq!(main(&["gpt05".into(), p(&img)]), 2);
        assert_eq!(main(&["gpt5".into()]), 2);
        std::fs::remove_dir_all(&d).unwrap();
    }
}
