//! `bko-holt efi-dedupe DISK PART LOADER` (not in the usage: run by load's
//! boot stage just before each `efibootmgr --create`).
//!
//! efibootmgr --create never looks at what NVRAM already has, so every
//! restore onto the same machine would add another copy of each entry.
//! This deletes (`efibootmgr -b NNNN -B`) each Boot#### that points at the
//! same partition (its GPT partition GUID, read off DISK now: NVRAM and the
//! restored table are only known on the target) and the same loader file,
//! compared without regard to case, `/` against `\`, or a leading slash.
//! Entries on other disks or partitions, and other loaders on the same
//! partition, are left alone.
//!
//! The entries are read from efivarfs (the load options themselves, with
//! dump's own parser, efi.rs), or, when it lists none, from `efibootmgr -v`,
//! whose text differs between versions: 17's `HD(...)/File(\EFI\x.efi)`
//! with optional data straight after, 18's `HD(...)/\EFI\x.efi` with it as
//! hex straight after the path.
//!
//! It never fails the stage: anything it can't read or delete is a
//! `warning:` line, and the entry is created all the same (a duplicate is
//! better than no boot entry).

use crate::bytes::{ByteSource, FileSource};
use crate::efi::{self, Node};
use crate::parttable;
use std::path::Path;
use std::process::Command;

const EFIVARS: &str = "/sys/firmware/efi/efivars";

/// What an entry's hard-drive node names.
#[derive(Debug, Clone, PartialEq)]
pub enum Disk {
    /// A GPT partition, by its partition GUID (lower case).
    Gpt(String),
    /// An MBR partition: the disk's signature and the partition's number.
    Mbr(u32, u32),
}

/// One Boot#### entry, as far as matching needs it.
#[derive(Debug, Clone, PartialEq)]
pub struct Entry {
    /// Four hex digits, as efibootmgr -b takes it ("0003").
    pub number: String,
    pub label: String,
    pub disk: Option<Disk>,
    pub file: Option<String>,
}

/// A loader path for comparing: `\`-separated, no leading separator, lower
/// case (FAT ignores case).
pub fn norm_path(p: &str) -> String {
    p.trim().replace('/', "\\").trim_start_matches('\\').to_ascii_lowercase()
}

/// HD(...)'s arguments (`1,GPT,0b1c...,0x800,0x100000`) as a Disk.
fn hd_disk(args: &str) -> Option<Disk> {
    let a: Vec<&str> = args.split(',').map(str::trim).collect();
    let part: u32 = a.first()?.parse().ok()?;
    let sig = a.get(2)?;
    match a.get(1)?.to_ascii_uppercase().as_str() {
        "GPT" => Some(Disk::Gpt(sig.to_ascii_lowercase())),
        "MBR" => u32::from_str_radix(sig.trim_start_matches("0x").trim_start_matches("0X"), 16).ok().map(|s| Disk::Mbr(s, part)),
        _ => None,
    }
}

/// The loader path just after a hard-drive node, from what follows its
/// `)`: `/File(\EFI\x.efi)...` or `/\EFI\x.efi...`.  The optional data
/// efibootmgr prints straight after an unwrapped path (18's hex) is cut off
/// at the end of its first `.efi`; a path without one ends at white space.
fn path_after(rest: &str) -> Option<String> {
    let rest = rest.strip_prefix('/')?;
    if let Some(r) = rest.strip_prefix("File(") {
        return r.find(')').map(|i| r[..i].to_string()).filter(|p| !p.is_empty());
    }
    if !rest.starts_with(['\\', '/']) {
        return None;
    }
    let tok = rest.split(char::is_whitespace).next().unwrap_or("");
    let low = tok.to_ascii_lowercase();
    let tok = match low.find(".efi") {
        Some(i) => &tok[..i + 4],
        None => tok,
    };
    (!tok.is_empty()).then(|| tok.to_string())
}

/// The Boot#### entries in `efibootmgr -v`'s output.  BootCurrent,
/// BootOrder, Timeout and the like, and 18's indented `dp:`/`data:` lines,
/// aren't entries.
pub fn parse_verbose(out: &str) -> Vec<Entry> {
    let mut v = Vec::new();
    for line in out.lines() {
        let Some(r) = line.strip_prefix("Boot") else { continue };
        let Some(num) = r.get(..4).filter(|n| n.chars().all(|c| c.is_ascii_hexdigit())) else { continue };
        let r = &r[4..];
        let r = r.strip_prefix('*').unwrap_or(r);
        if !r.is_empty() && !r.starts_with([' ', '\t']) {
            continue;
        }
        // The label and the device path are separated by a tab; without
        // one, the path starts where its first node does.
        let (label, path) = match r.split_once('\t') {
            Some((l, p)) => (l.trim().to_string(), p),
            None => match r.find("HD(") {
                Some(i) => (r[..i].trim().to_string(), &r[i..]),
                None => (r.trim().to_string(), ""),
            },
        };
        let (mut disk, mut file) = (None, None);
        if let Some(i) = path.find("HD(") {
            let hd = &path[i + 3..];
            if let Some(j) = hd.find(')') {
                disk = hd_disk(&hd[..j]);
                file = path_after(&hd[j + 1..]);
            }
        }
        v.push(Entry { number: num.to_ascii_uppercase(), label, disk, file });
    }
    v
}

/// The entry in the load option RAW (a Boot#### variable's data, without
/// its attributes), numbered NUMBER.
pub fn from_load_option(number: &str, raw: &[u8]) -> Option<Entry> {
    let lo = efi::parse_load_option(raw)?;
    let disk = match lo.hard_drive() {
        Some(Node::HardDrive { signature, format: 2, .. }) if !signature.is_empty() => Some(Disk::Gpt(signature.to_ascii_lowercase())),
        Some(Node::HardDrive { partition, signature, format: 1, .. }) => u32::from_str_radix(signature, 16).ok().map(|s| Disk::Mbr(s, *partition)),
        _ => None,
    };
    Some(Entry { number: number.to_ascii_uppercase(), label: lo.description.clone(), disk, file: lo.file().map(str::to_string) })
}

/// The entries that point at DISK's partition and LOADER.
pub fn matching<'a>(entries: &'a [Entry], disk: &Disk, loader: &str) -> Vec<&'a Entry> {
    let want = norm_path(loader);
    entries.iter().filter(|e| e.disk.as_ref() == Some(disk) && e.file.as_deref().is_some_and(|f| norm_path(f) == want)).collect()
}

/// The Boot#### entries in efivarfs under DIR; empty when there are none
/// or it can't be read.
fn efivars_entries(dir: &Path) -> Vec<Entry> {
    let suffix = format!("-{}", efi::GLOBAL);
    let Ok(rd) = std::fs::read_dir(dir) else { return Vec::new() };
    let mut names: Vec<String> = rd
        .filter_map(|e| e.ok()?.file_name().into_string().ok())
        .filter_map(|n| n.strip_suffix(&suffix).and_then(|n| n.strip_prefix("Boot")).map(str::to_string))
        .filter(|n| n.len() == 4 && n.chars().all(|c| c.is_ascii_hexdigit()))
        .collect();
    names.sort();
    names
        .iter()
        .filter_map(|n| {
            let b = std::fs::read(dir.join(format!("Boot{n}{suffix}"))).ok()?;
            from_load_option(n, b.get(4..)?)
        })
        .collect()
}

/// DISK's partition PART as an entry's hard-drive node names it, from the
/// table on the device now.
fn target_disk(dev: &str, part: u32) -> Result<Disk, String> {
    let src = FileSource::open(Path::new(dev)).map_err(|e| format!("{dev}: {e}"))?;
    let name = dev.rsplit('/').next().unwrap_or(dev);
    let lbs = std::fs::read_to_string(format!("/sys/class/block/{name}/queue/logical_block_size")).ok().and_then(|s| s.trim().parse::<u64>().ok());
    let sizes: Vec<u64> = match lbs {
        Some(n) if n >= 512 => vec![n],
        _ => vec![512, 4096],
    };
    for &ss in &sizes {
        if let Some(g) = parttable::read_gpt(&src as &dyn ByteSource, ss) {
            return g.parts.iter().find(|p| p.number == part).map(|p| Disk::Gpt(p.uuid.to_ascii_lowercase())).ok_or_else(|| format!("{dev}'s GPT has no partition {part}"));
        }
    }
    match parttable::read_mbr(&src as &dyn ByteSource, sizes[0]) {
        Some(m) if m.parts.iter().any(|p| p.number == part) => Ok(Disk::Mbr(m.disk_signature, part)),
        _ => Err(format!("{dev} has no partition table with a partition {part}")),
    }
}

fn efibootmgr(args: &[&str]) -> Result<String, String> {
    let o = Command::new("efibootmgr").args(args).output().map_err(|e| format!("efibootmgr: {e}"))?;
    if !o.status.success() {
        return Err(format!("efibootmgr {}: {} {}", args.join(" "), o.status, String::from_utf8_lossy(&o.stderr).trim()));
    }
    Ok(String::from_utf8_lossy(&o.stdout).into_owned())
}

/// `bko-holt efi-dedupe DISK PART LOADER`.
pub fn main(args: &[String]) -> u8 {
    let [dev, part, loader] = args else {
        eprintln!("usage: bko-holt efi-dedupe DISK PART LOADER");
        return 2;
    };
    let Ok(part) = part.parse::<u32>() else {
        eprintln!("bko-holt efi-dedupe: PART is a number");
        return 2;
    };
    let disk = match target_disk(dev, part) {
        Ok(d) => d,
        Err(e) => {
            eprintln!("warning: bko-holt efi-dedupe: {e}; NVRAM's entries for {loader} not checked for duplicates");
            return 0;
        }
    };
    let mut entries = efivars_entries(Path::new(EFIVARS));
    if entries.is_empty() {
        match efibootmgr(&["-v"]) {
            Ok(out) => entries = parse_verbose(&out),
            Err(e) => {
                eprintln!("warning: bko-holt efi-dedupe: {e}; NVRAM's entries for {loader} not checked for duplicates");
                return 0;
            }
        }
    }
    for e in matching(&entries, &disk, loader) {
        match efibootmgr(&["--quiet", "--bootnum", &e.number, "--delete-bootnum"]) {
            Ok(_) => println!("Boot{} ({:?}): the same partition and loader ({loader}) as the entry made next; deleted", e.number, e.label),
            Err(err) => eprintln!("warning: bko-holt efi-dedupe: Boot{} ({:?}) not deleted: {err}", e.number, e.label),
        }
    }
    0
}

#[cfg(test)]
mod tests {
    use super::*;

    const ESP: &str = "0b1c2d3e-4f50-6172-8394-a5b6c7d8e9f0";
    const OTHER: &str = "9a8b7c6d-5e4f-3a2b-1c0d-ffeeddccbbaa";
    const WIN: &str = "4d3c2b1a-0f9e-8d7c-6b5a-493827161504";

    fn nums(v: Vec<&Entry>) -> Vec<String> {
        v.into_iter().map(|e| e.number.clone()).collect()
    }

    /// Debian 12, efibootmgr 17: File(...) around the path, optional data
    /// straight after it, OVMF's own entries without an HD node.
    #[test]
    fn debian12_efibootmgr17() {
        let out = format!(
            "BootCurrent: 0003\nTimeout: 0 seconds\nBootOrder: 0003,0005,0001,0000,0002\n\
             Boot0000* UiApp\tFvVol(7cb8bdc9-f8eb-4f34-aaea-3ee4af6516a1)/FvFile(462caa21-7614-4503-836e-8ab6f4662331)\n\
             Boot0001* UEFI QEMU DVD-ROM QM00003 \tPciRoot(0x0)/Pci(0x1f,0x2)/Sata(1,65535,0)N.....YM....R,Y.\n\
             Boot0002* EFI Internal Shell\tFvVol(7cb8bdc9-f8eb-4f34-aaea-3ee4af6516a1)/FvFile(7c04a583-9e3e-4f1c-ad65-e05268d0b4d1)\n\
             Boot0003* debian\tHD(1,GPT,{},0x800,0x100000)/File(\\EFI\\debian\\shimx64.efi)\n\
             Boot0004* debian\tHD(1,GPT,{},0x800,0x100000)/File(\\EFI\\DEBIAN\\SHIMX64.EFI)\n\
             Boot0005* debian\tHD(1,GPT,{},0x800,0x100000)/File(\\EFI\\debian\\shimx64.efi)\n\
             Boot0006* grub\tHD(1,GPT,{},0x800,0x100000)/File(\\EFI\\debian\\grubx64.efi)\n\
             Boot0007  Linux\tHD(1,GPT,{},0x800,0x100000)/File(\\EFI\\debian\\shimx64.efi)\\.r.o.o.t.=./.d.e.v./.s.d.a.2.\n",
            ESP.to_ascii_uppercase(),
            ESP,
            OTHER,
            ESP,
            ESP
        );
        let v = parse_verbose(&out);
        assert_eq!(v.len(), 8, "{v:?}");
        assert!(v.iter().all(|e| e.number.len() == 4));
        assert_eq!(v[0].disk, None);
        assert_eq!(v[3], Entry { number: "0003".into(), label: "debian".into(), disk: Some(Disk::Gpt(ESP.into())), file: Some("\\EFI\\debian\\shimx64.efi".into()) });
        assert_eq!(v[7].file.as_deref(), Some("\\EFI\\debian\\shimx64.efi"));
        let d = Disk::Gpt(ESP.into());
        // An upper-case GUID and an upper-case path match; another disk's
        // GUID and another loader don't.
        assert_eq!(nums(matching(&v, &d, "\\EFI\\debian\\shimx64.efi")), ["0003", "0004", "0007"]);
        assert_eq!(nums(matching(&v, &d, "/EFI/debian/shimx64.efi")), ["0003", "0004", "0007"]);
        assert_eq!(nums(matching(&v, &d, "EFI\\Debian\\shimx64.efi")), ["0003", "0004", "0007"]);
        assert_eq!(nums(matching(&v, &d, "\\EFI\\debian\\grubx64.efi")), ["0006"]);
        assert!(matching(&v, &Disk::Gpt(OTHER.into()), "\\EFI\\debian\\grubx64.efi").is_empty());
        assert!(matching(&v, &d, "\\EFI\\debian\\mmx64.efi").is_empty());
    }

    /// Debian 12 backports and Chimera, efibootmgr 18 (efivar 38's text): no
    /// File(...), optional data as hex straight after the path, and 18's
    /// `dp:` and `data:` lines under each entry.
    #[test]
    fn efibootmgr18() {
        let out = format!(
            "BootCurrent: 0001\nTimeout: 3 seconds\nBootOrder: 0001,0004,0000\n\
             Boot0000* UiApp\tFvVol(7cb8bdc9-f8eb-4f34-aaea-3ee4af6516a1)/FvFile(462caa21-7614-4503-836e-8ab6f4662331)\n\
             \x20     dp: 04 07 14 00 c9 bd b8 7c eb f8 34 4f aa ea 3e e4 af 65 16 a1 / 04 06 14 00 21 aa 2c 46 14 76 03 45 83 6e 8a b6 f4 66 23 31 / 7f ff 04 00\n\
             Boot0001* Linux Boot Manager\tHD(1,GPT,{},0x800,0x100000)/\\EFI\\systemd\\systemd-bootx64.efi\n\
             \x20     dp: 04 01 2a 00 01 00 00 00 00 08 00 00 00 00 00 00 00 00 10 00 00 00 00 00 / 7f ff 04 00\n\
             Boot0002* Linux Boot Manager\tHD(1,GPT,{},0x800,0x100000)/\\EFI\\systemd\\systemd-bootx64.efi\n\
             Boot0003* Linux Boot Manager\tHD(1,GPT,{},0x800,0x100000)/\\EFI\\SYSTEMD\\SYSTEMD-BOOTX64.EFI6b0065007200\n\
             \x20   data: 6b 00 65 00 72 00\n\
             Boot0004* chimera\tHD(1,GPT,{},0x800,0x100000)/\\EFI\\chimera\\grubx64.efi\n\
             Boot0005* Windows Boot Manager\tHD(2,GPT,{},0x100800,0x32000)/\\EFI\\Microsoft\\Boot\\bootmgfw.efi57494e444f5753000100000088000000\n",
            ESP, OTHER, ESP.to_ascii_uppercase(), ESP, WIN
        );
        let v = parse_verbose(&out);
        assert_eq!(v.iter().map(|e| e.number.as_str()).collect::<Vec<_>>(), ["0000", "0001", "0002", "0003", "0004", "0005"]);
        assert_eq!(v[3].file.as_deref(), Some("\\EFI\\SYSTEMD\\SYSTEMD-BOOTX64.EFI"));
        assert_eq!(v[5].file.as_deref(), Some("\\EFI\\Microsoft\\Boot\\bootmgfw.efi"));
        let d = Disk::Gpt(ESP.into());
        assert_eq!(nums(matching(&v, &d, "\\EFI\\systemd\\systemd-bootx64.efi")), ["0001", "0003"]);
        assert_eq!(nums(matching(&v, &d, "\\EFI\\chimera\\grubx64.efi")), ["0004"]);
        assert!(matching(&v, &Disk::Gpt(OTHER.into()), "\\EFI\\chimera\\grubx64.efi").is_empty());
        assert_eq!(v[5].disk, Some(Disk::Gpt(WIN.into())));
        assert!(matching(&v, &d, "\\EFI\\Microsoft\\Boot\\bootmgfw.efi").is_empty());
        assert_eq!(nums(matching(&v, &Disk::Gpt(WIN.into()), "\\EFI\\Microsoft\\Boot\\bootmgfw.efi")), ["0005"]);
    }

    /// Alpine (busybox userland, efibootmgr 18 from apk): the same text,
    /// sometimes printed without the tab, and an MBR disk's entry.
    #[test]
    fn alpine() {
        let out = format!(
            "BootCurrent: 0002\nBootOrder: 0002,0003\n\
             Boot0002* alpine HD(1,GPT,{},0x800,0x80000)/\\EFI\\alpine\\grubx64.efi\n\
             Boot0003* alpine\tHD(1,GPT,{},0x800,0x80000)/\\EFI\\alpine\\grubx64.efi\n\
             Boot0004* old\tHD(1,MBR,0x1a2b3c4d,0x800,0x80000)/\\EFI\\alpine\\grubx64.efi\n\
             Boot0005  Hidden\n",
            ESP, OTHER
        );
        let v = parse_verbose(&out);
        assert_eq!(v.len(), 4, "{v:?}");
        assert_eq!(v[0].label, "alpine");
        assert_eq!(v[3], Entry { number: "0005".into(), label: "Hidden".into(), disk: None, file: None });
        assert_eq!(nums(matching(&v, &Disk::Gpt(ESP.into()), "\\EFI\\alpine\\grubx64.efi")), ["0002"]);
        assert_eq!(nums(matching(&v, &Disk::Mbr(0x1a2b3c4d, 1), "/efi/alpine/grubx64.efi")), ["0004"]);
        assert!(matching(&v, &Disk::Mbr(0x1a2b3c4d, 2), "/efi/alpine/grubx64.efi").is_empty());
    }

    /// No entries, and lines that only look like them.
    #[test]
    fn nothing_to_match() {
        assert!(parse_verbose("").is_empty());
        assert!(parse_verbose("BootCurrent: 0001\nBootOrder: 0001\nBootNext: 0002\nTimeout: 1 seconds\nMirrored percentage above 4G: 0.00\n").is_empty());
        assert!(matching(&[], &Disk::Gpt(ESP.into()), "\\EFI\\debian\\shimx64.efi").is_empty());
    }

    /// From efivarfs: a real load option (ubuntu26-uefi's dump, redacted).
    #[test]
    fn load_option() {
        let hex = "0100000062005500620075006e0074007500000004012a0001000000000800000000000000d8170000000000000000bc0000004080000000000000020202040434005c004500460049005c007500620075006e00740075005c007300680069006d007800360034002e0065006600690000007fff0400";
        let raw: Vec<u8> = (0..hex.len() / 2).map(|i| u8::from_str_radix(&hex[2 * i..2 * i + 2], 16).unwrap()).collect();
        let e = from_load_option("0006", &raw).unwrap();
        assert_eq!(e, Entry { number: "0006".into(), label: "Ubuntu".into(), disk: Some(Disk::Gpt("bc000000-0000-4000-8000-000000000002".into())), file: Some("\\EFI\\ubuntu\\shimx64.efi".into()) });
        let v = [e];
        assert_eq!(matching(&v, &Disk::Gpt("BC000000-0000-4000-8000-000000000002".to_ascii_lowercase()), "/EFI/Ubuntu/shimx64.efi").len(), 1);
        assert!(matching(&v, &Disk::Gpt("bc000000-0000-4000-8000-000000000003".into()), "\\EFI\\ubuntu\\shimx64.efi").is_empty());
    }

    /// efivarfs read from a directory: each Boot#### file is the 4-byte
    /// attributes and the load option.
    #[test]
    fn efivars_dir() {
        let dir = std::env::temp_dir().join(format!("bko-holt-efidedupe-{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        let hex = "0100000062005500620075006e0074007500000004012a0001000000000800000000000000d8170000000000000000bc0000004080000000000000020202040434005c004500460049005c007500620075006e00740075005c007300680069006d007800360034002e0065006600690000007fff0400";
        let mut raw = vec![7u8, 0, 0, 0];
        raw.extend((0..hex.len() / 2).map(|i| u8::from_str_radix(&hex[2 * i..2 * i + 2], 16).unwrap()));
        std::fs::write(dir.join(format!("Boot000A-{}", efi::GLOBAL)), &raw).unwrap();
        std::fs::write(dir.join(format!("BootOrder-{}", efi::GLOBAL)), [7u8, 0, 0, 0, 10, 0]).unwrap();
        std::fs::write(dir.join(format!("Boot0001-{}", efi::LOADER)), &raw).unwrap();
        let v = efivars_entries(&dir);
        std::fs::remove_dir_all(&dir).ok();
        assert_eq!(v.len(), 1, "{v:?}");
        assert_eq!(v[0].number, "000A");
        assert_eq!(v[0].file.as_deref(), Some("\\EFI\\ubuntu\\shimx64.efi"));
    }
}
