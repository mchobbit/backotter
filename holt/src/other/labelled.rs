//! NetBSD's and OpenBSD's report: disks from hw.disknames, MBR or GPT read
//! off the raw disk, the BSD disklabel inside (disklabel(8)'s text, kept for
//! load's disklabel -R), NetBSD's wedges for a GPT's partitions, OpenBSD's
//! disklabel UIDs, FFS with the parameters dumpfs prints, and the boot code.

use super::bsdlabel::{self, Label};
use super::{found_json, open, read_disk, Family, Found, RawDisk};
use crate::bytes::OffsetSource;
use crate::json::{Obj, Value};
use crate::mounts;
use crate::parttable::Table;
use crate::probe::{self, Content};
use crate::sys::Sys;

/// The whole-disk ("raw") partition's letter: NetBSD/amd64's d, OpenBSD's c.
pub fn raw_letter(fam: Family) -> char {
    if fam == Family::NetBsd {
        'd'
    } else {
        'c'
    }
}

/// hw.disknames: NetBSD's "ld0 dk0 cd0", OpenBSD's "sd0:duid,cd0:".  Whole
/// disks only (no wedges, CD drives, floppies, RAM or vnode disks), with
/// OpenBSD's DUIDs.
pub fn disk_names(fam: Family, text: &str) -> Vec<(String, Option<String>)> {
    let mut out: Vec<(String, Option<String>)> = text
        .split(|c: char| c == ',' || c.is_whitespace())
        .filter(|w| !w.is_empty())
        .map(|w| match w.split_once(':') {
            Some((n, d)) => (n.to_string(), (!d.is_empty()).then(|| d.to_string())),
            None => (w.to_string(), None),
        })
        .filter(|(n, _)| !["dk", "cd", "fd", "md", "rd", "vnd", "cgd", "ccd", "raid"].iter().any(|p| n.starts_with(p) && n[p.len()..].chars().all(|c| c.is_ascii_digit())))
        .collect();
    if fam == Family::OpenBsd {
        out.retain(|(n, _)| !n.starts_with("vnd") && !n.starts_with("rd"));
    }
    out.sort_by(|a, b| crate::sys::natural_cmp(&a.0, &b.0));
    out
}

/// NetBSD's wedges of a disk: "dk0: EFI, 2097152 blocks at 34, type: msdos"
/// as (dk0, name, start sector).
pub fn parse_wedges(text: &str) -> Vec<(String, String, u64)> {
    text.lines()
        .filter_map(|l| {
            let (dk, rest) = l.split_once(": ")?;
            if !dk.starts_with("dk") {
                return None;
            }
            let (name, rest) = rest.split_once(", ")?;
            let at = rest.split(" at ").nth(1)?.split(',').next()?.trim().parse().ok()?;
            Some((dk.to_string(), name.to_string(), at))
        })
        .collect()
}

/// dumpfs's superblock fields, "name value" pairs, NetBSD's and OpenBSD's.
pub fn dumpfs_fields(text: &str) -> Vec<(String, String)> {
    let mut out = Vec::new();
    for line in text.lines().take(40) {
        let w: Vec<&str> = line.split_whitespace().collect();
        let mut i = 0;
        while i + 1 < w.len() {
            if w[i].chars().all(|c| c.is_ascii_alphabetic() || c == '_') && !w[i + 1].starts_with('(') {
                out.push((w[i].to_string(), w[i + 1].to_string()));
                i += 2;
            } else {
                i += 1;
            }
        }
    }
    out
}

/// newfs's options for the FFS dumpfs describes: version, block and
/// fragment sizes, inodes per group (as bytes per inode), free space and
/// optimisation, and the size, in sectors.
pub fn newfs_from_dumpfs(fam: Family, text: &str) -> Option<String> {
    let f = dumpfs_fields(text);
    let get = |k: &str| f.iter().find(|(n, _)| n == k).map(|(_, v)| v.trim_end_matches('%').to_string());
    let v2 = text.contains("FFSv2") || text.contains("(FFS2)") || get("magic").as_deref() == Some("19540119");
    let bsize: u64 = get("bsize")?.parse().ok()?;
    let fsize: u64 = get("fsize")?.parse().ok()?;
    let fpg: u64 = get("fpg")?.parse().ok()?;
    let ipg: u64 = get("ipg")?.parse().ok()?;
    let size: u64 = get("size")?.parse().ok()?;
    let mut a = vec!["newfs".to_string(), "-O".into(), if v2 { "2" } else { "1" }.into()];
    a.extend(["-b".into(), bsize.to_string(), "-f".into(), fsize.to_string()]);
    // NetBSD's newfs takes the inode count itself (-n), which gives the
    // source's inodes per group exactly; OpenBSD's only bytes per inode.
    let ncg: u64 = get("ncg").and_then(|n| n.parse().ok()).unwrap_or(0);
    if fam == Family::NetBsd && ipg > 0 && ncg > 0 {
        a.extend(["-n".into(), (ipg * ncg).to_string()]);
    } else if ipg > 0 {
        a.extend(["-i".into(), (fpg * fsize / ipg).to_string()]);
    }
    if let Some(m) = get("minfree") {
        a.extend(["-m".into(), m]);
    }
    if let Some(o) = get("optim") {
        a.extend(["-o".into(), o]);
    }
    if fam == Family::NetBsd {
        if let Some(n) = get("maxbpg") {
            a.extend(["-e".into(), n]);
        }
    }
    // size is in fragments; newfs's -s is in sectors.
    a.extend(["-s".into(), (size * fsize / 512).to_string(), "DEVICE".into()]);
    Some(a.join(" "))
}

struct DiskSeen {
    d: RawDisk,
    duid: Option<String>,
    label: Option<(String, Label)>,
    found: Vec<Found>,
    /// The disklabel's partitions: (name, LabelPart, contents).
    lparts: Vec<(String, bsdlabel::LabelPart, Vec<Content>)>,
    wedges: Vec<(String, String, u64)>,
    whole: Vec<Content>,
    table: Option<Table>,
}

/// The device a spec names: /dev/ld0a, /dev/rld0a, /dev/dk2, NAME=wedge,
/// OpenBSD's DUID.a.
fn resolve(spec: &str, seen: &[DiskSeen]) -> Option<String> {
    if let Some(n) = spec.strip_prefix("NAME=") {
        return seen.iter().flat_map(|s| s.wedges.iter()).find(|(_, name, _)| name == n).map(|(dk, _, _)| dk.clone());
    }
    if let Some((duid, letter)) = spec.split_once('.') {
        if duid.len() == 16 && duid.chars().all(|c| c.is_ascii_hexdigit()) {
            let s = seen.iter().find(|s| s.duid.as_deref() == Some(duid))?;
            return Some(format!("{}{letter}", s.d.name));
        }
    }
    let s = spec.strip_prefix("/dev/")?;
    let s = s.strip_prefix('r').filter(|x| seen.iter().any(|d| x.starts_with(&d.d.name)) || x.starts_with("dk")).unwrap_or(s);
    for d in seen {
        if d.d.name == s || d.lparts.iter().any(|(n, _, _)| n == s) || d.wedges.iter().any(|(dk, _, _)| dk == s) {
            return Some(s.to_string());
        }
    }
    None
}

pub fn report(sys: &Sys, fam: Family, os: Option<&crate::osrel::OsRelease>, generated: &str) -> Value {
    let hostname = sys.run("hostname", &[]).map(|s| s.trim().to_string());
    let firmware = match fam {
        Family::NetBsd => match sys.run("sysctl", &["-n", "machdep.bootmethod"]).map(|s| s.trim().to_ascii_lowercase()) {
            Some(m) if m == "uefi" => "uefi",
            _ => "bios",
        },
        _ => {
            if sys.run("dmesg", &[]).is_some_and(|t| t.contains("efi0 at ")) {
                "uefi"
            } else {
                "bios"
            }
        }
    };
    let names = disk_names(fam, &sys.run("sysctl", &["-n", "hw.disknames"]).unwrap_or_default());
    let mut seen: Vec<DiskSeen> = Vec::new();
    for (name, duid) in names {
        let text = sys.run_any("disklabel", &[&name]).unwrap_or_default();
        let lab = bsdlabel::parse(&text);
        let sector = if lab.bytes_per_sector > 0 { lab.bytes_per_sector } else { 512 };
        let d = RawDisk { name: name.clone(), path: format!("/dev/r{name}{}", raw_letter(fam)), size: lab.total_sectors * sector, sector, model: None, serial: duid.clone() };
        let wedges = if fam == Family::NetBsd { parse_wedges(&sys.run("dkctl", &[&name, "listwedges"]).unwrap_or_default()) } else { Vec::new() };
        let Some(src) = open(sys, &d) else {
            sys.warn(format!("{}: can't read the disk", d.path));
            seen.push(DiskSeen { d, duid, label: None, found: vec![], lparts: vec![], wedges, whole: vec![], table: None });
            continue;
        };
        let nm = name.clone();
        let wd = wedges.clone();
        let (table, found) = read_disk(&src, &d, &|n, t| match t {
            Table::Gpt { gpt, .. } => {
                let start = gpt.parts.iter().find(|p| p.number == n).map(|p| p.first_lba).unwrap_or(0);
                wd.iter().find(|(_, _, s)| *s == start).map(|(dk, _, _)| dk.clone()).unwrap_or_else(|| format!("{nm}gpt{n}"))
            }
            Table::Mbr(_) => format!("{nm}mbr{n}"),
        });
        // The disklabel's own partitions: NetBSD's only on an MBR disk (on a
        // GPT it's made up from the wedges); OpenBSD's always.
        let real_label = fam == Family::OpenBsd || matches!(table, Some(Table::Mbr(_)));
        let mut lparts = Vec::new();
        if real_label {
            for p in lab.parts.iter().filter(|p| p.letter != raw_letter(fam) && p.fstype != "unused" && p.size > 0) {
                let c = probe::probe_all(&OffsetSource { inner: &src, start: p.offset * sector, len: p.size * sector });
                lparts.push((format!("{name}{}", p.letter), p.clone(), c));
            }
        }
        let whole = if table.is_none() && lparts.is_empty() { probe::probe_all(&src) } else { Vec::new() };
        // The MBR partition the disklabel is in starts where its first
        // partition does: what's found there is that partition's.
        let mut found = found;
        if real_label {
            for f in found.iter_mut().filter(|f| f.json.0.iter().any(|(k, v)| k == "type" && matches!(v.as_str(), Some("0xa6" | "0xa9" | "0xa5")))) {
                f.contents.clear();
            }
        }
        seen.push(DiskSeen { d, duid, label: real_label.then(|| (text.clone(), lab)), found, lparts, wedges, whole, table });
    }
    // mount(8)'s list.
    let mounted: Vec<(String, String, String, String)> = sys.run("mount", &[]).unwrap_or_default().lines().filter_map(bsdlabel::parse_mount_line).collect();
    let mounted_at = |name: &str| -> Vec<String> {
        let mut t: Vec<String> = mounted.iter().filter(|m| resolve(&m.0, &seen).as_deref() == Some(name)).map(|m| m.1.clone()).collect();
        t.sort();
        t
    };
    let ffs_extra = |name: &str, c: &[Content]| -> Obj {
        let mut x = Obj::new();
        if c.first().is_some_and(|c| c.kind == "ufs") {
            let raw = format!("/dev/r{name}");
            let t = match fam {
                Family::NetBsd => sys.run("dumpfs", &["-s", &raw]),
                _ => sys.run("dumpfs", &[&raw]),
            };
            if let Some(t) = t {
                x.put("newfs", newfs_from_dumpfs(fam, &t));
                x.put("ffs_flags", dumpfs_fields(&t).into_iter().find(|(k, _)| k == "flags").map(|(_, v)| v));
            }
        }
        x
    };
    let mut disks_json = Vec::new();
    let mut members: Vec<(String, Content)> = Vec::new();
    for s in &seen {
        let mut parts = Vec::new();
        let disk_src = open(sys, &s.d);
        for f in &s.found {
            let mut extra = ffs_extra(&f.name, &f.contents);
            if let Some(ds) = &disk_src {
                super::esp_image(ds, f, &mounted_at(&f.name), &mut extra);
            }
            if let Some((_, wname, _)) = s.wedges.iter().find(|(dk, _, _)| *dk == f.name) {
                extra.put("wedge_name", wname.as_str());
            }
            for c in &f.contents {
                members.push((f.name.clone(), c.clone()));
            }
            parts.push(found_json(f, mounted_at(&f.name), extra));
        }
        let lparts: Vec<Value> = s
            .lparts
            .iter()
            .map(|(n, p, c)| {
                let mut o = Obj::new()
                    .set("name", n.as_str())
                    .set("letter", p.letter.to_string())
                    .set("start_lba", p.offset)
                    .set("sectors", p.size)
                    .set("start_bytes", p.offset * s.d.sector)
                    .set("size_bytes", p.size * s.d.sector)
                    .set("fstype", p.fstype.as_str())
                    .set("content", probe::content_json(c));
                for (k, v) in ffs_extra(n, c).0 {
                    o.put(&k, v);
                }
                o.put("mounted_at", mounted_at(n));
                o.build()
            })
            .collect();
        let mut o = Obj::new()
            .set("name", s.d.name.as_str())
            .set("path", s.d.path.as_str())
            .set("kind", "disk")
            .set("size_bytes", s.d.size)
            .set("size", crate::report::human(s.d.size))
            .set("logical_sector_size", s.d.sector)
            .set("duid", s.duid.clone())
            .set("partition_table", s.table.as_ref().map(|t| t.header_json(s.d.sector, s.d.size / s.d.sector.max(1))).unwrap_or(Value::Null))
            .set("partitions", parts)
            .set("label_partitions", lparts)
            .set("disklabel", s.label.as_ref().map(|(t, _)| t.clone()));
        if let Some(t) = &s.table {
            let code = t.boot_code();
            o.put(
                "boot_code",
                Obj::new()
                    .set("kind", crate::bootcode::identify(code).kind)
                    .set("first_bytes", crate::bytes::hex(crate::bytes::sub(code, 0, 16)))
                    .set("code_b64", (!crate::bytes::is_zero(code)).then(|| crate::bytes::base64(crate::bytes::sub(code, 0, 440)))),
            );
        }
        o.put("content", probe::content_json(&s.whole));
        o.put("mounted_at", mounted_at(&s.d.name));
        disks_json.push(o.build());
    }
    let mounts_json: Vec<Value> = mounted
        .iter()
        .map(|m| Obj::new().set("target", m.1.as_str()).set("source", m.0.as_str()).set("fstype", m.2.as_str()).set("options", m.3.as_str()).set("device", resolve(&m.0, &seen)).build())
        .collect();
    let fstab = match sys.read("/etc/fstab") {
        Some(t) => Value::Arr(mounts::parse_fstab(&t).iter().map(|e| e.to_json(resolve(&e.spec, &seen).as_deref())).collect()),
        None => Value::Null,
    };
    let swap_text = sys.run("swapctl", &["-l"]).unwrap_or_default();
    let unit: u64 = swap_text.lines().next().and_then(|h| h.split_whitespace().nth(1)).and_then(|w| w.split('-').next()).and_then(|n| n.parse().ok()).unwrap_or(512);
    let swaps: Vec<Value> = swap_text
        .lines()
        .skip(1)
        .filter_map(|l| {
            let f: Vec<&str> = l.split_whitespace().collect();
            let n: u64 = f.get(1)?.parse().ok()?;
            Some(Obj::new().set("file", f[0]).set("size_bytes", n * unit).set("device", resolve(f[0], &seen)).build())
        })
        .collect();
    let member_refs: Vec<(String, &Content)> = members.iter().map(|(n, c)| (n.clone(), c)).collect();
    let storage = Obj::new()
        .set("disks", disks_json)
        .set("mounts", mounts_json)
        .set("swaps", swaps)
        .set("fstab", fstab)
        .set("zfs", crate::report::zfs_section(sys, &member_refs, &["/etc/zfs/zpool.cache"]));
    let loader = if fam == Family::NetBsd { "netbsd" } else { "openbsd" };
    let boot = Obj::new()
        .set("loader", Obj::new().set("name", loader).set("firmware", firmware).set("evidence", vec![format!("{loader}'s own boot blocks ({firmware})")]))
        .set("boot_cfg", if fam == Family::NetBsd { sys.read("/boot.cfg") } else { None })
        .set("secondary_boot", sys.is_file("/boot"));
    super::frame(sys, fam, os, generated, hostname, firmware, storage, boot)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn names_and_wedges() {
        assert_eq!(disk_names(Family::NetBsd, "ld0 dk0 dk1 cd0 wd1"), vec![("ld0".into(), None), ("wd1".into(), None)]);
        assert_eq!(disk_names(Family::OpenBsd, "cd0:,sd0:1a4a1728e6a303a2,fd0:"), vec![("sd0".into(), Some("1a4a1728e6a303a2".into()))]);
        let w = parse_wedges("/dev/rld0: 3 wedges:\ndk0: EFI, 2097152 blocks at 34, type: msdos\ndk2: AlpineBSD, 27262909 blocks at 6291490, type: ffs\n");
        assert_eq!(w, vec![("dk0".into(), "EFI".into(), 34), ("dk2".into(), "AlpineBSD".into(), 6291490)]);
    }

    #[test]
    fn newfs_args() {
        let nb = "file system: /dev/rld0a\nformat\tFFSv2\nendian\tlittle-endian\nmagic\t19540119\ttime\tThu Nov  5 21:10:45 2026\nncg\t78\tsize\t7339520\tblocks\t7115463\nbsize\t16384\tshift\t14\tmask\t0xffffc000\nfsize\t2048\tshift\t11\tmask\t0xfffff800\nbpg\t11763\tfpg\t94104\tipg\t22848\nminfree\t5%\toptim\ttime\tmaxcontig 4\tmaxbpg\t2048\n";
        assert_eq!(newfs_from_dumpfs(Family::NetBsd, nb).unwrap(), "newfs -O 2 -b 16384 -f 2048 -n 1782144 -m 5 -o time -e 2048 -s 29358080 DEVICE");
        let ob = "magic\t19540119 (FFS2)\ttime\tThu Nov  5 21:11:54 2026\nncg\t71\tsize\t7340016\tblocks\t7108799\nbsize\t16384\tshift\t14\tmask\t0xffffc000\nfsize\t2048\tshift\t11\tmask\t0xfffff800\nminfree\t5%\toptim\ttime\tsymlinklen 120\nbpg\t12960\tfpg\t103680\tipg\t25920\n";
        assert_eq!(newfs_from_dumpfs(Family::OpenBsd, ob).unwrap(), "newfs -O 2 -b 16384 -f 2048 -i 8192 -m 5 -o time -s 29360064 DEVICE");
    }
}
