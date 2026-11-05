//! DragonFly's report: disks from kern.disks and diskinfo, the MBR, each
//! DragonFly slice's disklabel64 (disklabel64 -r's text, kept for load's
//! disklabel64 -R), UFS (/boot) with dumpfs -m's newfs, HAMMER2 with its
//! PFS names, and the boot code.

use super::{found_json, open, read_disk, Family, Found, RawDisk};
use crate::bytes::OffsetSource;
use crate::json::{Obj, Value};
use crate::mounts;
use crate::probe::{self, Content};
use crate::sys::Sys;

/// DragonFly's diskinfo: "/dev/vbd0  blksize=512  offset=0x0  size=0x400000000 ...":
/// (sector size, bytes).
pub fn parse_diskinfo(text: &str) -> Option<(u64, u64)> {
    let mut bs = None;
    let mut size = None;
    for w in text.split_whitespace() {
        if let Some(v) = w.strip_prefix("blksize=") {
            bs = v.parse().ok();
        } else if let Some(v) = w.strip_prefix("size=0x") {
            size = u64::from_str_radix(v, 16).ok();
        }
    }
    Some((bs?, size?))
}

/// A disklabel64 partition: letter, KiB offset from the data base, KiB
/// size, fstype.
#[derive(Debug, Clone, PartialEq)]
pub struct L64Part {
    pub letter: char,
    pub size_kib: u64,
    pub offset_kib: u64,
    pub fstype: String,
}

/// disklabel64 -r's text: the partitions' data base (bytes from the slice's
/// start) and the partitions (sizes and offsets in its display blocks of
/// 1 KiB).
pub fn parse_disklabel64(text: &str) -> (u64, Vec<L64Part>) {
    let mut base = 0;
    let mut block = 1024;
    let mut parts = Vec::new();
    for line in text.lines() {
        let t = line.trim();
        if let Some(v) = t.strip_prefix("partitions data base:") {
            base = v.split_whitespace().next().and_then(|h| u64::from_str_radix(h.trim_start_matches("0x"), 16).ok()).unwrap_or(0);
        } else if let Some(v) = t.strip_prefix("display block size:") {
            block = v.split_whitespace().next().and_then(|n| n.parse().ok()).unwrap_or(1024);
        } else if let Some((l, rest)) = t.split_once(':') {
            let mut c = l.chars();
            let (Some(letter), None) = (c.next(), c.next()) else { continue };
            let f: Vec<&str> = rest.split_whitespace().collect();
            if !letter.is_ascii_lowercase() || f.len() < 3 {
                continue;
            }
            let (Ok(size), Ok(offset)) = (f[0].parse::<u64>(), f[1].parse::<u64>()) else { continue };
            parts.push(L64Part { letter, size_kib: size * block / 1024, offset_kib: offset * block / 1024, fstype: f[2].to_string() });
        }
    }
    (base, parts)
}

/// The partition a spec names: /dev/vbd0s1a, vbd0s1d@ROOT (a HAMMER2 PFS).
fn resolve(spec: &str, names: &[String]) -> Option<String> {
    let s = spec.strip_prefix("/dev/").unwrap_or(spec);
    let s = s.split('@').next()?;
    names.iter().any(|n| n == s).then(|| s.to_string())
}

pub fn report(sys: &Sys, fam: Family, os: Option<&crate::osrel::OsRelease>, generated: &str) -> Value {
    let hostname = sys.run("hostname", &[]).map(|s| s.trim().to_string());
    let list = sys.run("sysctl", &["-n", "kern.disks"]).unwrap_or_default();
    let mut names: Vec<&str> = list.split_whitespace().filter(|n| !["cd", "acd", "vn", "md"].iter().any(|p| n.starts_with(p) && n[p.len()..].chars().all(|c| c.is_ascii_digit()))).collect();
    names.sort_by(|a, b| crate::sys::natural_cmp(a, b));
    let mount_list = sys.run("mount", &["-p"]).map(|t| mounts::parse_fstab(&t)).unwrap_or_default();
    struct Seen {
        d: RawDisk,
        found: Vec<Found>,
        labels: Vec<(u32, String, u64, Vec<L64Part>)>,
        lcontents: Vec<(String, u64, u64, L64Part, u32, Vec<Content>)>,
        table: Option<crate::parttable::Table>,
    }
    let mut seen: Vec<Seen> = Vec::new();
    for n in names {
        let Some((sector, size)) = sys.run("diskinfo", &[&format!("/dev/{n}")]).and_then(|t| parse_diskinfo(&t)) else { continue };
        let d = RawDisk { name: n.to_string(), path: format!("/dev/{n}"), size, sector, model: None, serial: None };
        let Some(src) = open(sys, &d) else {
            sys.warn(format!("{}: can't read the disk", d.path));
            continue;
        };
        let (table, found) = read_disk(&src, &d, &|k, _| format!("{n}s{k}"));
        let mut labels = Vec::new();
        let mut lcontents = Vec::new();
        for f in &found {
            let is_dfly = f.json.0.iter().any(|(k, v)| k == "type" && v.as_str() == Some("0x6c"));
            if !is_dfly {
                continue;
            }
            let Some(text) = sys.run("disklabel64", &["-r", &f.name]) else { continue };
            let (base, parts) = parse_disklabel64(&text);
            for p in &parts {
                let start = f.start + base + p.offset_kib * 1024;
                let len = p.size_kib * 1024;
                let c = probe::probe_all(&OffsetSource { inner: &src, start, len });
                lcontents.push((format!("{}{}", f.name, p.letter), start, len, p.clone(), f.number, c));
            }
            labels.push((f.number, text, base, parts));
        }
        seen.push(Seen { d, found, labels, lcontents, table });
    }
    let all_names: Vec<String> = seen.iter().flat_map(|s| std::iter::once(s.d.name.clone()).chain(s.found.iter().map(|f| f.name.clone())).chain(s.lcontents.iter().map(|l| l.0.clone()))).collect();
    let mounted_at = |name: &str| -> Vec<String> {
        let mut t: Vec<String> = mount_list.iter().filter(|m| resolve(&m.spec, &all_names).as_deref() == Some(name)).map(|m| m.file.clone()).collect();
        t.sort();
        t
    };
    let mut disks_json = Vec::new();
    for s in &seen {
        let parts: Vec<Value> = s.found.iter().map(|f| found_json(f, mounted_at(&f.name), Obj::new())).collect();
        let lparts: Vec<Value> = s
            .lcontents
            .iter()
            .map(|(name, start, len, p, slice, c)| {
                let mut o = Obj::new()
                    .set("name", name.as_str())
                    .set("letter", p.letter.to_string())
                    .set("slice", *slice)
                    .set("start_bytes", *start)
                    .set("size_bytes", *len)
                    .set("fstype", p.fstype.as_str())
                    .set("content", probe::content_json(c));
                if c.first().is_some_and(|c| c.kind == "ufs") {
                    let dev = format!("/dev/{name}");
                    if let Some(t) = sys.run("dumpfs", &["-m", &dev]) {
                        o.put("newfs", super::freebsd::newfs_of(&t, &dev));
                    }
                }
                if c.first().is_some_and(|c| c.kind == "hammer2") {
                    // Its PFSs, from where it's mounted.
                    if let Some(mp) = mounted_at(name).first() {
                        let pfs: Vec<String> = sys
                            .run("hammer2", &["pfs-list", mp])
                            .unwrap_or_default()
                            .lines()
                            .skip(1)
                            .filter_map(|l| l.split_whitespace().nth(2).map(str::to_string))
                            .collect();
                        o.put("pfs", pfs);
                    }
                }
                o.put("mounted_at", mounted_at(name));
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
            .set("partition_table", s.table.as_ref().map(|t| t.header_json(s.d.sector, s.d.size / s.d.sector.max(1))).unwrap_or(Value::Null))
            .set("partitions", parts)
            .set("label_partitions", lparts)
            .set("disklabels64", s.labels.iter().map(|(n, t, _, _)| Obj::new().set("slice", *n).set("text", t.as_str()).build()).collect::<Vec<_>>());
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
        o.put("mounted_at", mounted_at(&s.d.name));
        disks_json.push(o.build());
    }
    let mounts_json: Vec<Value> = mount_list
        .iter()
        .map(|m| Obj::new().set("target", m.file.as_str()).set("source", m.spec.as_str()).set("fstype", m.vfstype.as_str()).set("options", m.options.as_str()).set("device", resolve(&m.spec, &all_names)).build())
        .collect();
    let fstab = match sys.read("/etc/fstab") {
        Some(t) => Value::Arr(mounts::parse_fstab(&t).iter().map(|e| e.to_json(resolve(&e.spec, &all_names).as_deref())).collect()),
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
            Some(Obj::new().set("file", f[0]).set("size_bytes", n * unit).set("device", resolve(f[0], &all_names)).build())
        })
        .collect();
    let storage = Obj::new().set("disks", disks_json).set("mounts", mounts_json).set("swaps", swaps).set("fstab", fstab);
    let boot = Obj::new()
        .set("loader", Obj::new().set("name", "dragonfly").set("firmware", "bios").set("evidence", vec!["DragonFly's boot0/boot1/boot2".to_string()]))
        .set("loader_conf", sys.read("/boot/loader.conf"));
    super::frame(sys, fam, os, generated, hostname, "bios", storage, boot)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn diskinfo_and_label64() {
        assert_eq!(parse_diskinfo("/dev/vbd0        blksize=512  offset=0x000000000000 size=0x000400000000 16384.00 MB\n"), Some((512, 1 << 34)));
        let t = "# /dev/vbd0s1:\ndiskid: 4214edb6-d687-11f1-a493-010000000000\nboot2 data base:      0x000000001000\npartitions data base: 0x0000000f8200\ndisplay block size: 1024\t# for partition display and edit only\n\n16 partitions:\n#          size     offset    fstype   fsuuid\n  a:    1048576          0    4.2BSD\t#    1024.000MB\n  b:    2097152    1048576      swap\t#    2048.000MB\n  d:   13629440    3145728   HAMMER2\t#   13310.000MB\n  a-stor_uuid: 42158474-d687-11f1-a493-010000000000\n";
        let (base, parts) = parse_disklabel64(t);
        assert_eq!(base, 0xf8200);
        assert_eq!(parts.len(), 3);
        assert_eq!(parts[2], L64Part { letter: 'd', size_kib: 13629440, offset_kib: 3145728, fstype: "HAMMER2".into() });
        assert_eq!(resolve("vbd0s1d@ROOT", &["vbd0s1d".to_string()]).as_deref(), Some("vbd0s1d"));
    }
}
