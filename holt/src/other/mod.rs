//! bko-holt on systems other than Linux: FreeBSD (and HardenedBSD and
//! GhostBSD, which are FreeBSD underneath), NetBSD, OpenBSD, DragonFly,
//! illumos and Haiku.  None has /sys, /proc or udev: the disks come from the
//! system's own tools (kern.disks, hw.disknames, diskinfo, ...), and the
//! partition tables and superblocks are read off the raw disk devices with
//! the same parsers as on Linux.  What only the system's own tools know
//! (newfs's parameters, disklabels, the boot blocks' names) is kept as they
//! print it, for `load` to give back to the same tools.
//!
//! The report has the Linux report's shape where the two overlap (os,
//! storage.disks with partition_table and partitions, mounts, fstab, swaps,
//! zfs; boot.loader), and `os.family` says which system it's from.

pub mod bsdlabel;
pub mod dfly;
pub mod freebsd;
pub mod geli;
pub mod haiku;
pub mod illumos;
pub mod labelled;
pub mod load;
pub mod offline;
pub mod softraid;

use crate::bytes::{ByteSource, FileSource, OffsetSource};
use crate::json::{Obj, Value};
use crate::mounts;
use crate::parttable::{self, Table};
use crate::probe::{self, Content};
use crate::sys::Sys;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Family {
    FreeBsd,
    NetBsd,
    OpenBsd,
    DragonFly,
    Illumos,
    Haiku,
    /// Read off the disks from Linux (offline.rs), and rebuilt from Linux.
    Redox,
    Genode,
}

impl Family {
    /// The system this binary was built for, unless it's Linux.
    pub fn current() -> Option<Family> {
        Family::from_name(std::env::consts::OS).filter(|f| !f.on_linux())
    }

    /// From Rust's name for the system (std::env::consts::OS), which is
    /// also what a report's os.family says.
    pub fn from_name(os: &str) -> Option<Family> {
        Some(match os {
            "freebsd" => Family::FreeBsd,
            "netbsd" => Family::NetBsd,
            "openbsd" => Family::OpenBsd,
            "dragonfly" => Family::DragonFly,
            "illumos" | "solaris" => Family::Illumos,
            "haiku" => Family::Haiku,
            "redox" => Family::Redox,
            "genode" => Family::Genode,
            _ => return None,
        })
    }

    pub fn name(self) -> &'static str {
        match self {
            Family::FreeBsd => "freebsd",
            Family::NetBsd => "netbsd",
            Family::OpenBsd => "openbsd",
            Family::DragonFly => "dragonfly",
            Family::Illumos => "illumos",
            Family::Haiku => "haiku",
            Family::Redox => "redox",
            Family::Genode => "genode",
        }
    }

    /// Whether load rebuilds it from Linux (its layout was read off the
    /// disks there): Linux's tools then, but this module's stages.
    pub fn on_linux(self) -> bool {
        matches!(self, Family::Redox | Family::Genode)
    }

    /// A report's family: os.family, absent from Linux's.
    pub fn of_layout(v: &Value) -> Option<Family> {
        v.at(&["os", "family"]).and_then(Value::as_str).and_then(Family::from_name)
    }
}

/// A whole disk as the system names it, and the device to read it through.
#[derive(Debug, Clone, PartialEq)]
pub struct RawDisk {
    pub name: String,
    /// The device that reads the whole disk (/dev/vtbd0, /dev/rsd0c).
    pub path: String,
    pub size: u64,
    pub sector: u64,
    pub model: Option<String>,
    pub serial: Option<String>,
}

/// A partition (GPT entry, MBR slice) found on a disk, with what's on it.
#[derive(Debug, Clone)]
pub struct Found {
    /// Its device name on this system (vtbd0p2, vtbd0s1).
    pub name: String,
    pub number: u32,
    pub start: u64,
    pub size: u64,
    pub json: Obj,
    pub contents: Vec<Content>,
}

/// "VALUE # description" lines, as FreeBSD's diskinfo -v prints them.
pub fn parse_diskinfo_v(text: &str) -> Vec<(String, String)> {
    text.lines()
        .filter_map(|l| {
            let (v, d) = l.split_once('#')?;
            Some((d.trim().to_string(), v.trim().to_string()))
        })
        .collect()
}

/// "pairs of name and value" from a `NAME: value` listing.
pub fn colon_pairs(text: &str) -> Vec<(String, String)> {
    text.lines().filter_map(|l| l.split_once(':')).map(|(k, v)| (k.trim().to_string(), v.trim().to_string())).collect()
}

/// The os section: os-release where the system has one (FreeBSD, GhostBSD,
/// HardenedBSD, illumos), else uname's name and release.
pub fn os_json(sys: &Sys, fam: Family, os: Option<&crate::osrel::OsRelease>) -> Value {
    let g = |k: &str| os.and_then(|o| o.get(k)).map(str::to_string);
    let uname = |a: &str| sys.run("uname", &[a]).map(|s| s.trim().to_string());
    Obj::new()
        .set("family", fam.name())
        .set("id", g("ID"))
        .set("version_id", g("VERSION_ID"))
        .set("pretty_name", g("PRETTY_NAME"))
        .set("kernel_name", uname("-s"))
        .set("kernel_release", uname("-r"))
        .set("kernel_version", uname("-v"))
        .set("architecture", std::env::consts::ARCH)
        .build()
}

/// os-release, or one made from uname for a system without the file:
/// ID is the lower-case system name, VERSION_ID its release's leading
/// number ("11.0" of NetBSD's "11.0", "7.8" of OpenBSD's).
pub fn os_release(sys: &Sys) -> Option<crate::osrel::OsRelease> {
    if let Some(o) = crate::osrel::read(sys) {
        return Some(o);
    }
    let name = sys.run("uname", &["-s"])?.trim().to_string();
    let rel = sys.run("uname", &["-r"]).unwrap_or_default().trim().to_string();
    let ver: String = rel.chars().take_while(|c| c.is_ascii_digit() || *c == '.').collect();
    let id = name.to_ascii_lowercase();
    Some(crate::osrel::parse(&format!("ID={id}\nVERSION_ID=\"{ver}\"\nPRETTY_NAME=\"{name} {rel}\"\n")))
}

/// A disk's or partition's size in bytes, from the system's own tools: its
/// raw device doesn't seek to its end.
pub fn dev_size(sys: &Sys, path: &str) -> Option<u64> {
    match Family::current()? {
        Family::FreeBsd => sys.run("diskinfo", &[path])?.split_whitespace().nth(2)?.parse().ok(),
        Family::DragonFly => dfly::parse_diskinfo(&sys.run("diskinfo", &[path])?).map(|(_, n)| n),
        // A disklabel partition's or the whole disk's size, from disklabel.
        Family::NetBsd | Family::OpenBsd => {
            let name = path.trim_start_matches("/dev/").trim_start_matches('r');
            let (disk, letter) = name.split_at(name.len().checked_sub(1)?);
            let l = bsdlabel::parse(&sys.run_any("disklabel", &[disk])?);
            let p = l.parts.iter().find(|p| p.letter.to_string() == letter)?;
            Some(p.size * l.bytes_per_sector.max(512))
        }
        Family::Haiku | Family::Illumos => haiku::probe_size(&sys.p(path)),
        Family::Redox | Family::Genode => None,
    }
}

/// A node load names as NAME=WEDGE (NetBSD's GPT partitions) as the device
/// that is that wedge now (/dev/dkN), or by DUID (OpenBSD's softraid
/// volumes' partitions) as the disk with that DUID now; any other as it is.
pub fn node_path(sys: &Sys, node: &str) -> String {
    if let Some(p) = softraid::duid_path(sys, node) {
        return p;
    }
    let Some(name) = node.strip_prefix("NAME=") else { return node.to_string() };
    for (d, _) in labelled::disk_names(Family::NetBsd, &sys.run("sysctl", &["-n", "hw.disknames"]).unwrap_or_default()) {
        for (dk, n, _) in labelled::parse_wedges(&sys.run("dkctl", &[&d, "listwedges"]).unwrap_or_default()) {
            if n == name {
                return format!("/dev/{dk}");
            }
        }
    }
    node.to_string()
}

/// What a restored tree has once the restore is done: /etc/fstab, or
/// Haiku's system directory (Haiku has no fstab), illumos' vfstab, or on
/// ostree the repository's config (the physical root has no etc), or on
/// ABRoot the present root's abimage.abr (load/abroot.rs).
pub fn restored_marker(layout: &Value) -> &'static str {
    if crate::load::ostree::of(layout).is_some() {
        return crate::load::ostree::MARKER;
    }
    if crate::load::abroot::of(layout).is_some() {
        return crate::load::abroot::marker(layout);
    }
    match Family::of_layout(layout) {
        Some(Family::Haiku) => "system",
        Some(Family::Illumos) => "etc/vfstab",
        Some(Family::Redox) => "etc/hostname",
        Some(Family::Genode) => "boot",
        _ => "etc/fstab",
    }
}

/// Opens a whole disk for reading.
pub fn open(sys: &Sys, d: &RawDisk) -> Option<FileSource> {
    if !sys.privileged {
        return None;
    }
    FileSource::open_sized(&sys.p(&d.path), d.size).ok()
}

/// Everything on a whole disk: its table's JSON, and each partition with
/// its contents, named by `name_of(number, table)`.
pub fn read_disk(src: &dyn ByteSource, d: &RawDisk, name_of: &dyn Fn(u32, &Table) -> String) -> (Option<Table>, Vec<Found>) {
    let Some(table) = parttable::read_table(src, d.sector) else { return (None, Vec::new()) };
    let mut out = Vec::new();
    for (n, start, sectors) in table.extents() {
        let Some(json) = table.partition_json(n, d.sector) else { continue };
        let start_b = start * d.sector;
        let size_b = sectors * d.sector;
        let is_ext = json.0.iter().any(|(k, v)| k == "extended" && *v == Value::Bool(true));
        let contents = if is_ext { Vec::new() } else { probe::probe_all(&OffsetSource { inner: src, start: start_b, len: size_b }) };
        out.push(Found { name: name_of(n, &table), number: n, start: start_b, size: size_b, json, contents });
    }
    (Some(table), out)
}

/// FreeBSD's GEOM label (glabel), in a provider's last sector: its name
/// shows as /dev/label/NAME.
pub fn glabel(src: &dyn ByteSource) -> Option<String> {
    let size = src.size();
    if size < 512 {
        return None;
    }
    let b = src.read_exact_at(size - 512, 512)?;
    if !b.starts_with(b"GEOM::LABEL\0") {
        return None;
    }
    crate::bytes::label(crate::bytes::sub(&b, 20, 16))
}

/// A boot area's bytes up to its last non-zero 512-byte sector, base64:
/// what load writes back where they were.  At most 2 MiB.
pub fn image_b64(src: &dyn ByteSource) -> String {
    let b = src.read_at(0, src.size().min(2 << 20) as usize);
    let end = b.chunks(512).rposition(|c| !crate::bytes::is_zero(c)).map(|i| (i + 1) * 512).unwrap_or(0);
    crate::bytes::base64(&b[..end.min(b.len())])
}

/// An EFI system partition that isn't mounted (so isn't in the backup):
/// its non-zero 512-byte runs, as (offset, base64) chunks, for load to
/// write back as they were.  At most 64 MiB of them.
pub fn esp_image(disk: &dyn ByteSource, f: &Found, mounted_at: &[String], extra: &mut Obj) {
    let ty = f.json.0.iter().find(|(k, _)| k == "type").and_then(|(_, v)| v.as_str()).unwrap_or("").to_ascii_lowercase();
    if !mounted_at.is_empty() || !(ty == crate::parttable::ESP || ty == "0xef") || f.size > (4 << 30) {
        return;
    }
    let src = OffsetSource { inner: disk, start: f.start, len: f.size };
    let mut chunks: Vec<Value> = Vec::new();
    let mut total = 0usize;
    let mut off = 0u64;
    let step = 1u64 << 20;
    while off < f.size {
        let b = src.read_at(off, step.min(f.size - off) as usize);
        if b.is_empty() {
            break;
        }
        let mut i = 0;
        while i < b.len() {
            if crate::bytes::is_zero(&b[i..(i + 512).min(b.len())]) {
                i += 512;
                continue;
            }
            let start = i;
            while i < b.len() && !crate::bytes::is_zero(&b[i..(i + 512).min(b.len())]) {
                i += 512;
            }
            let run = &b[start..i.min(b.len())];
            total += run.len();
            chunks.push(Obj::new().set("offset", off + start as u64).set("b64", crate::bytes::base64(run)).build());
        }
        off += step;
        if total > 64 << 20 {
            extra.put("esp_image_too_big", true);
            return;
        }
    }
    extra.put("esp_image", chunks);
}

/// The fstab-format lines of `mount -p` as mount entries.
pub fn mounts_from_p(text: &str) -> Vec<mounts::FstabEntry> {
    mounts::parse_fstab(text)
}

/// The report's partition JSON: the table's view, the name, the contents.
pub fn found_json(f: &Found, mounted_at: Vec<String>, extra: Obj) -> Value {
    let mut o = Obj::new().set("name", f.name.as_str());
    for (k, v) in f.json.0.iter() {
        o.put(k, v.clone());
    }
    o.put("content", probe::content_json(&f.contents));
    for (k, v) in extra.0 {
        o.put(&k, v);
    }
    o.put("mounted_at", mounted_at);
    o.build()
}

/// `bko-holt dump` on this system.
pub fn dump(sys: &Sys, fam: Family, generated: &str) -> Result<Value, String> {
    let os = os_release(sys);
    crate::osrel::check_supported(os.as_ref())?;
    match fam {
        Family::FreeBsd => Ok(freebsd::report(sys, fam, os.as_ref(), generated)),
        Family::NetBsd | Family::OpenBsd => Ok(labelled::report(sys, fam, os.as_ref(), generated)),
        Family::DragonFly => Ok(dfly::report(sys, fam, os.as_ref(), generated)),
        Family::Haiku => Ok(haiku::report(sys, fam, os.as_ref(), generated)),
        Family::Illumos => Ok(illumos::report(sys, fam, os.as_ref(), generated)),
        Family::Redox | Family::Genode => Err("bko-holt dump --disk reads this system, from Linux".into()),
    }
}

/// The firmware mode when the dump can't tell UEFI from BIOS: "unknown",
/// with a warning saying why (WHAT, PROG's command, said ANSWER; None: PROG
/// isn't installed or failed).  Never "bios" for want of an answer: load
/// takes anything but "uefi" for BIOS (Layout::uefi), so it refuses
/// "unknown" (commands::build) rather than install the wrong boot code.
pub fn firmware_unknown(sys: &Sys, prog: &str, what: &str, answer: Option<&str>) -> &'static str {
    let why = match answer {
        Some(a) => format!("{what} says {a:?}"),
        None if !sys.installed(prog) => format!("{prog} isn't installed"),
        None => format!("{what} failed"),
    };
    sys.warn(format!("the firmware mode (UEFI or BIOS) can't be told: {why}; the dump says unknown, and bko-holt load refuses it"));
    "unknown"
}

/// The report's frame around a family's sections.
pub fn frame(sys: &Sys, fam: Family, os: Option<&crate::osrel::OsRelease>, generated: &str, hostname: Option<String>, firmware: &str, storage: Obj, boot: Obj) -> Value {
    let mut out = Obj::new()
        .set("format", crate::report::FORMAT)
        .set("schema", crate::report::SCHEMA)
        .set("tool", Obj::new().set("name", "bko-holt").set("version", env!("CARGO_PKG_VERSION")))
        .set("generated", generated)
        .set("privileged", sys.privileged)
        .set("host", Obj::new().set("hostname", hostname))
        .set("os", os_json(sys, fam, os))
        .set("firmware", Obj::new().set("mode", firmware))
        .set("storage", storage)
        .set("boot", boot);
    out.put("warnings", sys.warnings());
    out.build()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn families() {
        for f in [Family::FreeBsd, Family::NetBsd, Family::OpenBsd, Family::DragonFly, Family::Illumos, Family::Haiku] {
            assert_eq!(Family::from_name(f.name()), Some(f));
        }
        assert_eq!(Family::from_name("linux"), None);
        assert_eq!(Family::from_name("solaris"), Some(Family::Illumos));
        let v = crate::json::parse(r#"{"os": {"family": "openbsd"}}"#).unwrap();
        assert_eq!(Family::of_layout(&v), Some(Family::OpenBsd));
        assert_eq!(Family::of_layout(&crate::json::parse(r#"{"os": {"id": "debian"}}"#).unwrap()), None);
    }

    #[test]
    fn diskinfo() {
        let t = "vtbd0\n\t512         \t# sectorsize\n\t17179869184 \t# mediasize in bytes (16G)\n\t            \t# Disk descr.\n\tBHYVE-1234  \t# Disk ident.\n";
        let p = parse_diskinfo_v(t);
        assert!(p.contains(&("sectorsize".into(), "512".into())));
        assert!(p.contains(&("mediasize in bytes (16G)".into(), "17179869184".into())));
        assert!(p.contains(&("Disk ident.".into(), "BHYVE-1234".into())));
    }

    #[test]
    fn glabels() {
        let mut img = vec![0u8; 8192];
        img[8192 - 512..8192 - 512 + 12].copy_from_slice(b"GEOM::LABEL\0");
        img[8192 - 512 + 20..8192 - 512 + 27].copy_from_slice(b"rootfs0");
        assert_eq!(glabel(&crate::bytes::MemSource(img)).as_deref(), Some("rootfs0"));
        assert_eq!(glabel(&crate::bytes::MemSource(vec![0u8; 8192])), None);
    }
}
