//! illumos' report (OmniOS): disks from diskinfo, the EFI label (GPT) or
//! MBR read off each disk's p0 node, slices named cXtYdZsN, ZFS (the root
//! pool and its boot environments), the mounts from /etc/mnttab and the
//! boot setup.

use super::{found_json, open, read_disk, Family, Found, RawDisk};
use crate::json::{Obj, Value};
use crate::probe::Content;
use crate::sys::Sys;

/// `diskinfo -Hp`: "TYPE DISK VID PID SIZE RMV SSD", tab-separated.
pub fn parse_diskinfo(text: &str) -> Vec<(String, u64, String)> {
    text.lines()
        .filter_map(|l| {
            let f: Vec<&str> = l.split('\t').collect();
            if f.len() < 5 {
                return None;
            }
            Some((f[1].to_string(), f[4].trim().parse().ok()?, format!("{} {}", f[2].trim(), f[3].trim())))
        })
        .collect()
}

/// /etc/mnttab: special, mount point, type, options, time.
pub fn parse_mnttab(text: &str) -> Vec<(String, String, String, String)> {
    text.lines()
        .filter_map(|l| {
            let f: Vec<&str> = l.split('\t').collect();
            (f.len() >= 4).then(|| (f[0].to_string(), f[1].to_string(), f[2].to_string(), f[3].to_string()))
        })
        .collect()
}

pub fn disks(sys: &Sys) -> Vec<RawDisk> {
    // Just after a disk's label changed, its links are being remade and
    // diskinfo may leave it out for a moment: asked again then.
    let mut text = sys.run("diskinfo", &["-Hp"]).unwrap_or_default();
    for _ in 0..5 {
        let _ = sys.run("devfsadm", &["-c", "disk"]);
        let again = sys.run("diskinfo", &["-Hp"]).unwrap_or_default();
        if again.lines().count() <= text.lines().count() {
            break;
        }
        text = again;
    }
    parse_diskinfo(&text)
        .into_iter()
        .map(|(n, size, model)| RawDisk { path: format!("/dev/rdsk/{n}p0"), name: n, size, sector: 512, model: Some(model), serial: None })
        .collect()
}

/// A slice's name: an EFI label's entry N is slice N-1.
pub fn slice_name(disk: &str, n: u32) -> String {
    format!("{disk}s{}", n.saturating_sub(1))
}

pub fn report(sys: &Sys, fam: Family, os: Option<&crate::osrel::OsRelease>, generated: &str) -> Value {
    let hostname = sys.run("hostname", &[]).map(|s| s.trim().to_string());
    let firmware = if sys.run("prtconf", &["-v"]).is_some_and(|t| t.contains("efi-systab")) { "uefi" } else { "bios" };
    let mnttab = parse_mnttab(&sys.read("/etc/mnttab").unwrap_or_default());
    let resolve = |spec: &str| -> Option<String> { spec.strip_prefix("/dev/dsk/").map(str::to_string) };
    let mounted_at = |name: &str| -> Vec<String> {
        let mut t: Vec<String> = mnttab.iter().filter(|m| resolve(&m.0).as_deref() == Some(name)).map(|m| m.1.clone()).collect();
        t.sort();
        t
    };
    let mut disks_json = Vec::new();
    let mut members: Vec<(String, Content)> = Vec::new();
    for d in disks(sys) {
        let Some(src) = open(sys, &d) else {
            sys.warn(format!("{}: can't read the disk", d.path));
            continue;
        };
        let name = d.name.clone();
        let (table, found): (_, Vec<Found>) = read_disk(&src, &d, &|n, _| slice_name(&name, n));
        let mut parts = Vec::new();
        for f in &found {
            let mut extra = Obj::new();
            super::esp_image(&src, f, &mounted_at(&f.name), &mut extra);
            for c in &f.contents {
                members.push((f.name.clone(), c.clone()));
            }
            parts.push(found_json(f, mounted_at(&f.name), extra));
        }
        let mut o = Obj::new()
            .set("name", d.name.as_str())
            .set("path", d.path.as_str())
            .set("kind", "disk")
            .set("size_bytes", d.size)
            .set("size", crate::report::human(d.size))
            .set("logical_sector_size", d.sector)
            .set("model", d.model.clone())
            .set("partition_table", table.as_ref().map(|t| t.header_json(d.sector, d.size / d.sector)).unwrap_or(Value::Null))
            .set("partitions", parts);
        if let Some(t) = &table {
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
    let mounts_json: Vec<Value> = mnttab
        .iter()
        .filter(|m| !matches!(m.2.as_str(), "proc" | "mntfs" | "devfs" | "dev" | "ctfs" | "objfs" | "sharefs" | "fd" | "bootfs" | "lofs"))
        .map(|m| Obj::new().set("target", m.1.as_str()).set("source", m.0.as_str()).set("fstype", m.2.as_str()).set("options", m.3.as_str()).set("device", resolve(&m.0)).build())
        .collect();
    let member_refs: Vec<(String, &Content)> = members.iter().map(|(n, c)| (n.clone(), c)).collect();
    let storage = Obj::new()
        .set("disks", disks_json)
        .set("mounts", mounts_json)
        .set("swaps", Vec::<Value>::new())
        // vfstab names no filesystem of these disks (ZFS mounts the root
        // pool's datasets): load mounts the pool's.
        .set("fstab", Vec::<Value>::new())
        .set("vfstab", sys.read("/etc/vfstab"))
        .set("zfs", crate::report::zfs_section(sys, &member_refs, &["/etc/zfs/zpool.cache"]));
    let boot = Obj::new()
        .set("loader", Obj::new().set("name", "illumos").set("firmware", firmware).set("evidence", vec!["illumos' loader (installboot)".to_string()]))
        .set("boot_environments", sys.run("beadm", &["list", "-H"]))
        .set("menu", sys.run("bootadm", &["list-menu"]));
    super::frame(sys, fam, os, generated, hostname, firmware, storage, boot)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parsers() {
        assert_eq!(parse_diskinfo("-\tc3t0d0\tVirtio\tBlock Device\t17179869184\tno\tno\n"), vec![("c3t0d0".into(), 17179869184, "Virtio Block Device".into())]);
        let m = parse_mnttab("rpool/ROOT/omnios-r151058y\t/\tzfs\tdev=4310002\t0\n/devices\t/devices\tdevfs\tdev=8980000\t1793913084\n");
        assert_eq!(m[0], ("rpool/ROOT/omnios-r151058y".into(), "/".into(), "zfs".into(), "dev=4310002".into()));
        assert_eq!(slice_name("c3t0d0", 2), "c3t0d0s1");
    }
}
