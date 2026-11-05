//! Haiku's report: disks under /dev/disk (each a directory with "raw", and
//! a node per partition, numbered from 0), the MBR read off the raw disk,
//! BFS, and the volume Haiku boots from, which it mounts at /boot: the
//! restored system's root.  Haiku has no fstab: the report makes up one
//! line, the boot volume's, for load to mount it.

use super::{found_json, read_disk, Family, Found, RawDisk};
use crate::bytes::FileSource;
use crate::json::{Obj, Value};

use crate::sys::Sys;
use std::os::unix::fs::FileExt;

/// A raw device's size, where it reports none (Haiku's raw disks stat as
/// empty): the end of what reads, found by halving.
pub fn probe_size(path: &std::path::Path) -> Option<u64> {
    let f = std::fs::File::open(path).ok()?;
    let reads = |sector: u64| {
        let mut b = [0u8; 512];
        f.read_at(&mut b, sector * 512).map(|n| n == 512).unwrap_or(false)
    };
    if !reads(0) {
        return None;
    }
    let (mut lo, mut hi) = (0u64, 1u64 << 40);
    while lo + 1 < hi {
        let mid = lo + (hi - lo) / 2;
        if reads(mid) {
            lo = mid;
        } else {
            hi = mid;
        }
    }
    Some((lo + 1) * 512)
}

/// Every whole disk: /dev/disk/BUS/.../raw, but RAM disks.
pub fn disks(sys: &Sys) -> Vec<RawDisk> {
    fn walk(sys: &Sys, dir: &str, depth: usize, out: &mut Vec<String>) {
        if depth > 6 {
            return;
        }
        for e in sys.list(dir) {
            let p = format!("{dir}/{e}");
            if e == "raw" {
                out.push(dir.to_string());
            } else if sys.is_dir(&p) && e != "virtual" {
                walk(sys, &p, depth + 1, out);
            }
        }
    }
    let mut dirs = Vec::new();
    walk(sys, "/dev/disk", 0, &mut dirs);
    dirs.sort_by(|a, b| crate::sys::natural_cmp(a, b));
    dirs.into_iter()
        .filter_map(|dir| {
            let path = format!("{dir}/raw");
            let size = probe_size(&sys.p(&path))?;
            Some(RawDisk { name: name_of(&dir), path, size, sector: 512, model: None, serial: None })
        })
        .collect()
}

/// "scsi_2_0_0" for /dev/disk/scsi/2/0/0.
pub fn name_of(dir: &str) -> String {
    dir.trim_start_matches("/dev/disk/").replace('/', "_")
}

/// /dev/disk/scsi/2/0/0 for "scsi_2_0_0".
pub fn dir_of(name: &str) -> String {
    format!("/dev/disk/{}", name.replace('_', "/"))
}

/// Partition `n` (1-based, as the table numbers it) of a disk: Haiku numbers
/// them from 0.
pub fn part_path(disk: &str, n: u32) -> String {
    format!("{}/{}", dir_of(disk), n.saturating_sub(1))
}

/// df's volumes: (mount point, file system type, device).
pub fn parse_df(text: &str) -> Vec<(String, String, String)> {
    let strip = |s: &str| {
        let mut o = String::new();
        let mut esc = false;
        for c in s.chars() {
            if c == '\u{1b}' {
                esc = true;
            } else if esc {
                if c.is_ascii_alphabetic() {
                    esc = false;
                }
            } else {
                o.push(c);
            }
        }
        o
    };
    let lines: Vec<String> = text.lines().map(strip).collect();
    let mut out = Vec::new();
    for w in lines.windows(2) {
        let mp = w[0].trim();
        if !mp.starts_with('/') || w[0].starts_with(' ') {
            continue;
        }
        let f: Vec<&str> = w[1].split_whitespace().collect();
        if let (Some(ty), Some(dev)) = (f.first(), f.last().filter(|d| d.starts_with("/dev/"))) {
            out.push((mp.to_string(), ty.to_string(), dev.to_string()));
        }
    }
    out
}

pub fn report(sys: &Sys, fam: Family, os: Option<&crate::osrel::OsRelease>, generated: &str) -> Value {
    let hostname = sys.run("hostname", &[]).map(|s| s.trim().to_string());
    let df = parse_df(&sys.run("df", &[]).unwrap_or_default());
    let mut all: Vec<(RawDisk, Option<crate::parttable::Table>, Vec<Found>)> = Vec::new();
    for d in disks(sys) {
        let Ok(src) = FileSource::open_sized(&sys.p(&d.path), d.size) else { continue };
        let name = d.name.clone();
        let (t, f) = read_disk(&src, &d, &|n, _| format!("{name}_{}", n.saturating_sub(1)));
        all.push((d, t, f));
    }
    let dev_name = |dev: &str| -> Option<String> {
        let (dir, idx) = dev.rsplit_once('/')?;
        Some(format!("{}_{idx}", name_of(dir)))
    };
    let mounted_at = |name: &str| -> Vec<String> { df.iter().filter(|m| dev_name(&m.2).as_deref() == Some(name)).map(|m| m.0.clone()).collect() };
    let mut disks_json = Vec::new();
    for (d, t, found) in &all {
        let parts: Vec<Value> = found.iter().map(|f| found_json(f, mounted_at(&f.name), Obj::new())).collect();
        let mut o = Obj::new()
            .set("name", d.name.as_str())
            .set("path", d.path.as_str())
            .set("kind", "disk")
            .set("size_bytes", d.size)
            .set("size", crate::report::human(d.size))
            .set("logical_sector_size", d.sector)
            .set("partition_table", t.as_ref().map(|t| t.header_json(d.sector, d.size / d.sector)).unwrap_or(Value::Null))
            .set("partitions", parts);
        if let Some(t) = t {
            let code = t.boot_code();
            o.put(
                "boot_code",
                Obj::new()
                    .set("kind", crate::bootcode::identify(code).kind)
                    .set("first_bytes", crate::bytes::hex(crate::bytes::sub(code, 0, 16)))
                    .set("code_b64", (!crate::bytes::is_zero(code)).then(|| crate::bytes::base64(crate::bytes::sub(code, 0, 440)))),
            );
        }
        o.put("mounted_at", mounted_at(&d.name));
        disks_json.push(o.build());
    }
    let mounts_json: Vec<Value> = df.iter().map(|m| Obj::new().set("target", m.0.as_str()).set("source", m.2.as_str()).set("fstype", m.1.as_str()).set("device", dev_name(&m.2)).build()).collect();
    // The boot volume, mounted at /boot, is the restored system's /.
    let fstab: Vec<Value> = df
        .iter()
        .filter(|m| m.0 == "/boot" && m.1 == "bfs")
        .map(|m| Obj::new().set("spec", m.2.as_str()).set("file", "/").set("type", "bfs").set("options", "").set("freq", 0).set("passno", 0).set("resolves_to", dev_name(&m.2)).set("made_up", true).build())
        .collect();
    let storage = Obj::new().set("disks", disks_json).set("mounts", mounts_json).set("swaps", Vec::<Value>::new()).set("fstab", fstab);
    let boot = Obj::new().set("loader", Obj::new().set("name", "haiku").set("firmware", "bios").set("evidence", vec!["Haiku's MBR and BFS boot block (makebootable)".to_string()]));
    super::frame(sys, fam, os, generated, hostname, "bios", storage, boot)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn names_and_df() {
        assert_eq!(name_of("/dev/disk/scsi/2/0/0"), "scsi_2_0_0");
        assert_eq!(dir_of("scsi_2_0_0"), "/dev/disk/scsi/2/0/0");
        assert_eq!(part_path("scsi_3_0_0", 1), "/dev/disk/scsi/3/0/0/0");
        let t = "\u{1b}[1mVolume\u{1b}[0m\n Type        Total      Free      Flags   Used%  Device\n----------- ---------- --------- ------- ------ -------------------\n\u{1b}[1m/boot             \u{1b}[0m\n        bfs   16.0 GiB  13.4 GiB QAM-P-W    16% /dev/disk/scsi/2/0/0/0  \n\u{1b}[1m/boot/system      \u{1b}[0m\n  packagefs          0         0 QAM-P--   100%                         \n";
        assert_eq!(parse_df(t), vec![("/boot".into(), "bfs".into(), "/dev/disk/scsi/2/0/0/0".into())]);
    }
}
