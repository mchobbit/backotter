//! illumos' report (OmniOS, OpenIndiana): disks from diskinfo, the EFI
//! label (GPT) or MBR read off each disk's p0 node, slices named cXtYdZsN,
//! ZFS (the root pool and its boot environments), the mounts from
//! /etc/mnttab and the boot setup.

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

/// `iostat -En`: per device a "cXtYdZ  Soft Errors: ..." line, then its
/// "Vendor: V Product: P Revision: ..." (or "Model: M Revision: ...") and
/// "Size: 17.18GB <17179869184 bytes>".  Devices without media (a CD
/// drive's 0 bytes) are left out.
pub fn parse_iostat_en(text: &str) -> Vec<(String, u64, String)> {
    let mut out: Vec<(String, u64, String)> = Vec::new();
    let mut cur: Option<(String, u64, String)> = None;
    let between = |l: &str, from: &str, to: &[&str]| -> Option<String> {
        let rest = &l[l.find(from)? + from.len()..];
        let end = to.iter().filter_map(|t| rest.find(t)).min().unwrap_or(rest.len());
        Some(rest[..end].trim().to_string())
    };
    for l in text.lines() {
        if !l.starts_with(char::is_whitespace) && l.contains("Soft Errors:") {
            out.extend(cur.take().filter(|d| d.1 > 0));
            cur = l.split_whitespace().next().map(|n| (n.to_string(), 0, String::new()));
        } else if let Some(d) = cur.as_mut() {
            if let Some(v) = between(l, "Vendor:", &["Product:"]) {
                let p = between(l, "Product:", &["Revision:", "Serial No:"]).unwrap_or_default();
                d.2 = format!("{v} {p}").trim().to_string();
            } else if let Some(m) = between(l, "Model:", &["Revision:", "Serial No:"]) {
                d.2 = m;
            }
            if let Some(n) = between(l, "Size:", &[]).and_then(|s| s.split_once('<')?.1.split_whitespace().next()?.parse().ok()) {
                d.1 = n;
            }
        }
    }
    out.extend(cur.filter(|d| d.1 > 0));
    out
}

pub fn disks(sys: &Sys) -> Vec<RawDisk> {
    // diskinfo, killed when it runs out of time (sys.rs), can still hang
    // in the kernel and leave nothing (the rescuer, cinqcent 2026-11-21:
    // `diskinfo -Hp` stuck for two hours just after a restore, while
    // `format` listed every disk): asked once more, then the disks are
    // iostat -En's, from the kernel's statistics without opening any disk.
    let mut text = sys.run("diskinfo", &["-Hp"]);
    if text.is_none() {
        let _ = sys.run("devfsadm", &["-c", "disk"]);
        text = sys.run("diskinfo", &["-Hp"]);
    }
    let Some(mut text) = text else {
        let found = sys.run("iostat", &["-En"]).map(|t| parse_iostat_en(&t)).unwrap_or_default();
        sys.warn(if found.is_empty() {
            "no disk listing: `diskinfo -Hp` failed or ran out of time twice, and `iostat -En` listed no disk".to_string()
        } else {
            "`diskinfo -Hp` failed or ran out of time twice: the disks are `iostat -En`'s".to_string()
        });
        return raw_disks(found);
    };
    // Just after a disk's label changed, its links are being remade and
    // diskinfo may leave it out for a moment: asked again then.
    for _ in 0..5 {
        let _ = sys.run("devfsadm", &["-c", "disk"]);
        let again = sys.run("diskinfo", &["-Hp"]).unwrap_or_default();
        if again.lines().count() <= text.lines().count() {
            break;
        }
        text = again;
    }
    raw_disks(parse_diskinfo(&text))
}

fn raw_disks(found: Vec<(String, u64, String)>) -> Vec<RawDisk> {
    found.into_iter().map(|(n, size, model)| RawDisk { path: format!("/dev/rdsk/{n}p0"), name: n, size, sector: 512, model: Some(model), serial: None }).collect()
}

/// A slice's name: an EFI label's entry N is slice N-1.
pub fn slice_name(disk: &str, n: u32) -> String {
    format!("{disk}s{}", n.saturating_sub(1))
}

pub fn report(sys: &Sys, fam: Family, os: Option<&crate::osrel::OsRelease>, generated: &str) -> Value {
    let hostname = sys.run("hostname", &[]).map(|s| s.trim().to_string());
    let firmware = match sys.run("prtconf", &["-v"]) {
        Some(t) if t.contains("efi-systab") => "uefi",
        Some(_) => "bios",
        None => super::firmware_unknown(sys, "prtconf", "`prtconf -v`", None),
    };
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
        let mut o = super::disk_obj(&d, "disk", Obj::new())
            .set("model", d.model.clone())
            .set("partition_table", super::table_json(table.as_ref(), &d))
            .set("partitions", parts);
        if let Some(t) = &table {
            o.put("boot_code", super::boot_code_json(t));
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
        .set("zfs", crate::report::zfs_section(sys, &member_refs, &["/etc/zfs/zpool.cache"], mnttab.iter().any(|m| m.2 == "zfs")));
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

    const IOSTAT_EN: &str = "c1t0d0           Soft Errors: 0 Hard Errors: 0 Transport Errors: 0 \n\
Vendor: QEMU     Product: QEMU DVD-ROM     Revision: 2.5+ Serial No:  \n\
Size: 0.00GB <0 bytes>\n\
Media Error: 0 Device Not Ready: 0 No Device: 0 Recoverable: 0 \n\
Illegal Request: 0 Predictive Failure Analysis: 0 \n\
c2t0d0           Soft Errors: 0 Hard Errors: 0 Transport Errors: 0 \n\
Vendor: Virtio   Product: Block Device     Revision: 0000 Serial No: bkodisk-0 \n\
Size: 17.18GB <17179869184 bytes>\n\
Media Error: 0 Device Not Ready: 0 No Device: 0 Recoverable: 0 \n\
Illegal Request: 0 Predictive Failure Analysis: 0 \n\
c3t0d0           Soft Errors: 0 Hard Errors: 0 Transport Errors: 0 \n\
Model: INTEL SSDPE2KX010T8 Revision: VDV10131 Serial No: PHLJ0001 \n\
Size: 1000.20GB <1000204886016 bytes>\n";

    #[test]
    fn iostat_en_lists_disks_with_media() {
        assert_eq!(
            parse_iostat_en(IOSTAT_EN),
            vec![("c2t0d0".into(), 17179869184, "Virtio Block Device".into()), ("c3t0d0".into(), 1000204886016, "INTEL SSDPE2KX010T8".into())]
        );
    }

    /// diskinfo failing (or timed out: RealRunner says None either way)
    /// twice: the disks are iostat -En's, with a warning; nothing from
    /// either: a warning that says so.  A diskinfo that answers: its disks,
    /// and no iostat.
    #[test]
    fn disks_without_diskinfo() {
        use crate::sys::FakeRunner;
        let names = |s: &Sys| disks(s).into_iter().map(|d| (d.name, d.path, d.size)).collect::<Vec<_>>();
        let s = Sys::new("/", Box::new(FakeRunner::default().with("iostat -En", IOSTAT_EN)), true);
        assert_eq!(names(&s), vec![("c2t0d0".into(), "/dev/rdsk/c2t0d0p0".into(), 17179869184), ("c3t0d0".into(), "/dev/rdsk/c3t0d0p0".into(), 1000204886016)]);
        assert_eq!(s.warnings(), vec!["`diskinfo -Hp` failed or ran out of time twice: the disks are `iostat -En`'s".to_string()]);
        let s = Sys::new("/", Box::new(FakeRunner::default()), true);
        assert!(names(&s).is_empty());
        assert!(s.warnings()[0].starts_with("no disk listing: "), "{:?}", s.warnings());
        let s = Sys::new("/", Box::new(FakeRunner::default().with("diskinfo -Hp", "-\tc5t0d0\tVirtio\tBlock Device\t1048576\tno\tno\n").with("iostat -En", IOSTAT_EN)), true);
        assert_eq!(names(&s), vec![("c5t0d0".into(), "/dev/rdsk/c5t0d0p0".into(), 1048576)]);
        assert!(s.warnings().is_empty(), "{:?}", s.warnings());
    }

    /// No prtconf: the firmware is unknown, not bios, with a warning (load
    /// refuses it); the root pool mounted (mnttab) and no zpool: the zfs
    /// section says so.  A prtconf without efi-systab: bios, nothing said.
    #[test]
    fn missing_tools_are_said() {
        use crate::sys::FakeRunner;
        let t = crate::testutil::Tree::new("illumos-notools");
        t.file("/etc/mnttab", "rpool/ROOT/omnios\t/\tzfs\tdev=4310002\t0\n");
        let v = report(&Sys::new(t.root(), Box::new(FakeRunner::default().without("prtconf").without("zpool")), true), Family::Illumos, None, "x");
        assert_eq!((v.at(&["firmware", "mode"]).and_then(Value::as_str), v.at(&["boot", "loader", "firmware"]).and_then(Value::as_str)), (Some("unknown"), Some("unknown")));
        let e = v.at(&["storage", "zfs", "tools", "error"]).and_then(Value::as_str).unwrap();
        assert!(e.starts_with("ZFS file systems are mounted, but zpool isn't installed, "), "{e}");
        let w = v.get("warnings").and_then(Value::as_array).unwrap();
        assert!(w.contains(&Value::from("the firmware mode (UEFI or BIOS) can't be told: prtconf isn't installed; the dump says unknown, and bko-holt load refuses it")), "{w:?}");
        let r = FakeRunner::default().with("prtconf -v", "System Configuration:  illumos  i86pc\n").with("zpool list -H -o name", "");
        let v = report(&Sys::new(t.root(), Box::new(r), true), Family::Illumos, None, "x");
        assert_eq!(v.at(&["firmware", "mode"]).and_then(Value::as_str), Some("bios"));
        assert_eq!(v.at(&["storage", "zfs", "tools", "error"]), None);
        assert!(v.get("warnings").and_then(Value::as_array).unwrap().iter().filter_map(Value::as_str).all(|w| !w.contains("firmware") && !w.contains("zpool")));
    }
}
