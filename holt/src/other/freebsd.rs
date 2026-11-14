//! FreeBSD's report (HardenedBSD and GhostBSD too): disks from kern.disks
//! and diskinfo, GPT or MBR read off them, GEOM labels, UFS with the newfs
//! command dumpfs -m gives for it, ZFS, and the boot blocks.

use super::{found_json, glabel, open, read_disk, Family, Found, RawDisk};
use crate::bytes::{contains, ByteSource, OffsetSource};
use crate::json::{Obj, Value};
use crate::mounts;
use crate::parttable::Table;
use crate::probe::{self, Content};
use crate::sys::Sys;

/// GPT type of the BIOS boot partition gpart bootcode -p fills.
pub const FREEBSD_BOOT: &str = "83bd6b9d-7f41-11dc-be0b-001560b84f0f";

/// The disks kern.disks lists, but CD drives and memory disks.
pub fn disks(sys: &Sys) -> Vec<RawDisk> {
    let list = sys.run("sysctl", &["-n", "kern.disks"]).unwrap_or_default();
    let mut names: Vec<&str> = list.split_whitespace().filter(|n| !n.starts_with("cd") && !n.starts_with("md")).collect();
    names.sort_by(|a, b| crate::sys::natural_cmp(a, b));
    names
        .into_iter()
        .filter_map(|n| {
            let info = super::parse_diskinfo_v(&sys.run("diskinfo", &["-v", n])?);
            let get = |k: &str| info.iter().find(|(d, _)| d.starts_with(k)).map(|(_, v)| v.clone()).filter(|v| !v.is_empty());
            Some(RawDisk {
                name: n.to_string(),
                path: format!("/dev/{n}"),
                size: get("mediasize in bytes")?.parse().ok()?,
                sector: get("sectorsize").and_then(|s| s.parse().ok()).unwrap_or(512),
                model: get("Disk descr."),
                serial: get("Disk ident."),
            })
        })
        .collect()
}

/// vtbd0p2 for a GPT entry, vtbd0s1 for an MBR slice.
pub fn part_name(disk: &str, n: u32, t: &Table) -> String {
    match t {
        Table::Gpt { .. } => format!("{disk}p{n}"),
        Table::Mbr(_) => format!("{disk}s{n}"),
    }
}

/// What's in a freebsd-boot partition: gptzfsboot (which reads ZFS) or
/// gptboot (UFS).
pub fn boot_partition_kind(src: &dyn ByteSource) -> &'static str {
    let b = src.read_at(0, src.size().min(1 << 20) as usize);
    if crate::bytes::is_zero(&b) {
        "empty"
    } else if contains(&b, b"zfs") || contains(&b, b"ZFS") {
        "gptzfsboot"
    } else if contains(&b, b"BTX") {
        "gptboot"
    } else {
        "unknown"
    }
}

/// FreeBSD's MBR boot code: pmbr (in front of a GPT) or boot0 (MBR).
pub fn mbr_code_kind(code: &[u8]) -> &'static str {
    if contains(code, b"Boot loader too large") || contains(code, b"Missing boot loader") {
        "freebsd-pmbr"
    } else if contains(code, b"F1") && contains(code, b"Default:") {
        "freebsd-boot0"
    } else {
        crate::bootcode::identify(code).kind
    }
}

/// `tunefs -p`'s lines, "tunefs: POSIX.1e ACLs: (-a)   disabled": flag
/// and value.
pub fn parse_tunefs_p(text: &str) -> Vec<(String, String)> {
    text.lines()
        .filter_map(|l| {
            let i = l.find("(-")?;
            let flag = l[i + 1..].split(')').next()?.to_string();
            let v = l[i..].split_once(')')?.1.trim().to_string();
            Some((flag, v))
        })
        .collect()
}

/// dumpfs -m's newfs command, with the device as DEVICE.
pub fn newfs_of(dumpfs_m: &str, dev: &str) -> Option<String> {
    let l = dumpfs_m.lines().find(|l| l.starts_with("newfs "))?;
    Some(l.trim().strip_suffix(dev).map(|s| format!("{}DEVICE", s)).unwrap_or_else(|| l.trim().to_string()))
}

/// The partitions of a BSD disklabel (bsdlabel) in a FreeBSD MBR slice, read
/// off the slice: (letter, start, length), in bytes from the slice's start,
/// the whole-slice partition c left out.  The label is the slice's second
/// sector; its offsets are the slice's own when c starts at 0 (mkimg and
/// gpart write them so), else the disk's, made the slice's here.  FreeBSD's
/// rescue image keeps its root in such a label ("a", 16 sectors in), where a
/// probe of the slice itself doesn't reach its superblock.
pub fn bsdlabel_parts(slice: &dyn ByteSource) -> Vec<(char, u64, u64)> {
    const DISKMAGIC: u32 = 0x8256_4557;
    let Some(b) = slice.read_exact_at(512, 512) else { return Vec::new() };
    if crate::bytes::le32(&b, 0) != DISKMAGIC || crate::bytes::le32(&b, 132) != DISKMAGIC {
        return Vec::new();
    }
    // (The label's own fields: a sector size that's none (0, 512) or a
    // power of two up to 64 KiB, and sums checked, so a corrupt or crafted
    // label on any disk gives nothing rather than wrapping.)
    let secsize = match crate::bytes::le32(&b, 40) {
        0 => 512,
        n if (512..=65536).contains(&n) && n.is_power_of_two() => n as u64,
        _ => return Vec::new(),
    };
    let n = (crate::bytes::le16(&b, 138) as usize).min(20);
    let entry = |i: usize| (crate::bytes::le32(&b, 148 + 16 * i) as u64, crate::bytes::le32(&b, 148 + 16 * i + 4) as u64);
    let base = if n > 2 { entry(2).1 } else { 0 };
    (0..n)
        .filter(|&i| i != 2)
        .filter_map(|i| {
            let (size, off) = entry(i);
            if size == 0 || off < base {
                return None;
            }
            let (start, len) = ((off - base).checked_mul(secsize)?, size.checked_mul(secsize)?);
            (start.checked_add(len)? <= slice.size()).then_some(((b'a' + i as u8) as char, start, len))
        })
        .collect()
}

/// The partition a device spec names: /dev/vtbd0p2, /dev/gpt/LABEL,
/// /dev/gptid/UUID, /dev/label/NAME (glabel), /dev/ufsid/ID, /dev/ufs/LABEL;
/// and DragonFly's /dev/part-by-label/NAME.LETTER, the partition LETTER of
/// the slice whose disklabel64 is named NAME.  Each disk's list of names
/// holds its GEOM labels (FreeBSD) or its slices' disklabel64 names
/// (DragonFly), as (partition, name).
pub fn resolve(spec: &str, disks: &[(RawDisk, Vec<Found>, Vec<(String, String)>)]) -> Option<String> {
    // A GELI provider (/dev/X.eli, onetime swap's too): X's partition.
    if let Some(x) = spec.strip_suffix(".eli") {
        return resolve(x, disks);
    }
    let s = spec.strip_prefix("/dev/")?;
    let parts = || disks.iter().flat_map(|(_, f, g)| f.iter().map(move |p| (p, g)));
    let pj = |p: &Found, k: &str| p.json.0.iter().find(|(key, _)| key == k).and_then(|(_, v)| v.as_str().map(str::to_string));
    if let Some(l) = s.strip_prefix("part-by-label/") {
        let (name, letter) = l.rsplit_once('.')?;
        if letter.len() != 1 || !letter.bytes().all(|c| c.is_ascii_lowercase()) {
            return None;
        }
        return parts().find(|(p, g)| g.iter().any(|(n, gl)| *n == p.name && gl == name)).map(|(p, _)| format!("{}{letter}", p.name));
    }
    if let Some(l) = s.strip_prefix("gpt/") {
        return parts().find(|(p, _)| pj(p, "partlabel").as_deref() == Some(l)).map(|(p, _)| p.name.clone());
    }
    if let Some(u) = s.strip_prefix("gptid/") {
        return parts().find(|(p, _)| pj(p, "uuid").is_some_and(|x| x.eq_ignore_ascii_case(u))).map(|(p, _)| p.name.clone());
    }
    if let Some(l) = s.strip_prefix("label/") {
        return parts().find(|(p, g)| g.iter().any(|(n, gl)| *n == p.name && gl == l)).map(|(p, _)| p.name.clone());
    }
    if let Some(u) = s.strip_prefix("ufsid/") {
        return parts().find(|(p, _)| p.contents.first().and_then(|c| c.uuid.as_deref()) == Some(u)).map(|(p, _)| p.name.clone());
    }
    if let Some(l) = s.strip_prefix("ufs/") {
        return parts().find(|(p, _)| p.contents.first().and_then(|c| c.label.as_deref()) == Some(l)).map(|(p, _)| p.name.clone());
    }
    if disks.iter().any(|(d, _, _)| d.name == s) || parts().any(|(p, _)| p.name == s) {
        return Some(s.to_string());
    }
    None
}

pub fn report(sys: &Sys, fam: Family, os: Option<&crate::osrel::OsRelease>, generated: &str) -> Value {
    let hostname = sys.run("sysctl", &["-n", "kern.hostname"]).map(|s| s.trim().to_string());
    let firmware = match sys.run("sysctl", &["-n", "machdep.bootmethod"]).map(|s| s.trim().to_ascii_lowercase()) {
        Some(m) if m == "uefi" => "uefi",
        _ => "bios",
    };
    // Each disk with its partitions and their GEOM labels.
    let mut seen: Vec<(RawDisk, Vec<Found>, Vec<(String, String)>)> = Vec::new();
    let mut tables: Vec<Option<Table>> = Vec::new();
    let mut whole: Vec<Vec<Content>> = Vec::new();
    for d in disks(sys) {
        let Some(src) = open(sys, &d) else {
            sys.warn(format!("{}: can't read the disk", d.path));
            tables.push(None);
            whole.push(Vec::new());
            seen.push((d, Vec::new(), Vec::new()));
            continue;
        };
        let name = d.name.clone();
        let (table, found) = read_disk(&src, &d, &|n, t| part_name(&name, n, t));
        let mut labels = Vec::new();
        for f in &found {
            if let Some(l) = glabel(&OffsetSource { inner: &src, start: f.start, len: f.size }) {
                labels.push((f.name.clone(), l));
            }
        }
        whole.push(if table.is_none() { probe::probe_all(&src) } else { Vec::new() });
        tables.push(table);
        seen.push((d, found, labels));
    }
    let mount_list = sys.run("mount", &["-p"]).map(|t| mounts::parse_fstab(&t)).unwrap_or_default();
    let mounted_at = |name: &str| -> Vec<String> {
        let mut t: Vec<String> = mount_list.iter().filter(|m| resolve(&m.spec, &seen).as_deref() == Some(name)).map(|m| m.file.clone()).collect();
        t.sort();
        t
    };
    let mut disks_json = Vec::new();
    let mut members: Vec<(String, Content)> = Vec::new();
    let mut boot_blocks = Vec::new();
    for (i, (d, found, labels)) in seen.iter().enumerate() {
        let src = open(sys, d);
        let mut parts = Vec::new();
        for f in found {
            let mut extra = Obj::new();
            if let Some((_, l)) = labels.iter().find(|(n, _)| *n == f.name) {
                extra.put("glabel", l.as_str());
            }
            let ptype = f.json.0.iter().find(|(k, _)| k == "type").and_then(|(_, v)| v.as_str()).unwrap_or("").to_ascii_lowercase();
            if ptype == FREEBSD_BOOT {
                if let Some(s) = &src {
                    let part = OffsetSource { inner: s, start: f.start, len: f.size };
                    let kind = boot_partition_kind(&part);
                    // Its bytes, for load to write back (gpart bootcode
                    // would read the restored /boot instead, moving atimes).
                    extra.put("boot_code", Obj::new().set("kind", kind).set("image_b64", super::image_b64(&part)));
                    boot_blocks.push(Obj::new().set("partition", f.name.as_str()).set("kind", kind).build());
                }
            }
            if f.contents.first().is_some_and(|c| c.kind == "ufs") {
                let dev = format!("/dev/{}", f.name);
                // Whether it has /.snap (newfs -U makes one; installers may
                // remove it), where it's mounted.
                if let Some(mp) = mount_list.iter().find(|m| resolve(&m.spec, &seen).as_deref() == Some(f.name.as_str())).map(|m| m.file.clone()) {
                    extra.put("snap_dir", sys.is_dir(&format!("{}/.snap", if mp == "/" { "" } else { mp.as_str() })));
                }
                if let Some(t) = sys.run("dumpfs", &["-m", &dev]) {
                    extra.put("newfs", super::freebsd::newfs_of(&t, &dev));
                }
                if let Some(t) = sys.run("tunefs", &["-p", &dev]) {
                    let mut o = Obj::new();
                    for (k, v) in parse_tunefs_p(&t) {
                        o.put(&k, v);
                    }
                    extra.put("tunefs", o.build());
                }
            }
            let gl = labels.iter().find(|(n, _)| *n == f.name).map(|(_, l)| l.as_str());
            members.extend(super::geli::dump_partition(sys, f, gl, &seen, &mount_list, &mut extra));
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
            .set("serial", d.serial.clone())
            .set("partition_table", tables[i].as_ref().map(|t| t.header_json(d.sector, d.size / d.sector)).unwrap_or(Value::Null))
            .set("partitions", parts);
        if let Some(t) = &tables[i] {
            let code = t.boot_code();
            o.put(
                "boot_code",
                Obj::new()
                    .set("kind", mbr_code_kind(code))
                    .set("first_bytes", crate::bytes::hex(crate::bytes::sub(code, 0, 16)))
                    .set("code_b64", (!crate::bytes::is_zero(code)).then(|| crate::bytes::base64(crate::bytes::sub(code, 0, 440)))),
            );
        }
        for c in &whole[i] {
            members.push((d.name.clone(), c.clone()));
        }
        o.put("content", probe::content_json(&whole[i]));
        o.put("mounted_at", mounted_at(&d.name));
        disks_json.push(o.build());
    }
    let mounts_json: Vec<Value> = mount_list
        .iter()
        .map(|m| super::geli::mark(Obj::new().set("target", m.file.as_str()).set("source", m.spec.as_str()).set("fstype", m.vfstype.as_str()).set("options", m.options.as_str()).set("device", resolve(&m.spec, &seen)).build(), &m.spec))
        .collect();
    let fstab = match sys.read("/etc/fstab") {
        Some(t) => Value::Arr(mounts::parse_fstab(&t).iter().map(|e| super::geli::mark(e.to_json(resolve(&e.spec, &seen).as_deref()), &e.spec)).collect()),
        None => Value::Null,
    };
    let swaps: Vec<Value> = sys
        .run("swapctl", &["-l"])
        .unwrap_or_default()
        .lines()
        .skip(1)
        .filter_map(|l| {
            let f: Vec<&str> = l.split_whitespace().collect();
            let kib: u64 = f.get(1)?.parse().ok()?;
            Some(super::geli::mark_swap(Obj::new().set("file", f[0]).set("size_bytes", kib * 1024).set("device", resolve(f[0], &seen)).build(), f[0], &seen))
        })
        .collect();
    let member_refs: Vec<(String, &Content)> = members.iter().map(|(n, c)| (n.clone(), c)).collect();
    let zfs = crate::report::zfs_section(sys, &member_refs, &["/etc/zfs/zpool.cache", "/boot/zfs/zpool.cache"]);
    let storage = Obj::new()
        .set("disks", disks_json)
        .set("mounts", mounts_json)
        .set("swaps", swaps)
        .set("fstab", fstab)
        .set("zfs", zfs)
        .set("glabels", seen.iter().flat_map(|(_, _, g)| g.iter().map(|(n, l)| Obj::new().set("device", n.as_str()).set("label", l.as_str()).build())).collect::<Vec<_>>());
    let mut evidence = vec![format!("machdep.bootmethod says {}", firmware.to_uppercase())];
    for b in &boot_blocks {
        evidence.push(format!("{} holds {}", b.get("partition").and_then(Value::as_str).unwrap_or("?"), b.get("kind").and_then(Value::as_str).unwrap_or("?")));
    }
    let efi = if firmware == "uefi" { sys.run("efibootmgr", &["-v"]) } else { None };
    let boot = Obj::new()
        .set("loader", Obj::new().set("name", "freebsd").set("firmware", firmware).set("evidence", evidence))
        .set("boot_partitions", boot_blocks)
        .set("loader_conf", sys.read("/boot/loader.conf"))
        .set("efibootmgr", efi)
        .set("kernel", sys.run("sysctl", &["-n", "kern.bootfile"]).map(|s| s.trim().to_string()));
    super::frame(sys, fam, os, generated, hostname, firmware, storage, boot)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn tunefs_and_newfs() {
        let t = "tunefs: POSIX.1e ACLs: (-a)                                disabled\ntunefs: soft update journaling: (-j)                       enabled\ntunefs: minimum percentage of free space: (-m)             8%\ntunefs: volume label: (-L)                                 \n";
        assert_eq!(
            parse_tunefs_p(t),
            vec![("-a".into(), "disabled".into()), ("-j".into(), "enabled".into()), ("-m".into(), "8%".into()), ("-L".into(), "".into())]
        );
        let d = "# newfs command for /dev/vtbd0p3 (/dev/vtbd0p3)\nnewfs -O 2 -U -a 32 -b 32768 -j -s 29359024 -t /dev/vtbd0p3 \n";
        assert_eq!(newfs_of(d, "/dev/vtbd0p3").as_deref(), Some("newfs -O 2 -U -a 32 -b 32768 -j -s 29359024 -t DEVICE"));
    }

    fn found(name: &str, number: u32, contents: Vec<Content>) -> Found {
        Found { name: name.into(), number, start: 0, size: 0, json: Obj::new(), contents }
    }

    fn raw(name: &str) -> RawDisk {
        RawDisk { name: name.into(), path: format!("/dev/{name}"), size: 1 << 30, sector: 512, model: None, serial: None }
    }

    #[test]
    fn dragonfly_part_by_label() {
        // DragonFly's rescue image mounts / from part-by-label/BKO_<id>.a:
        // the "a" of the slice whose disklabel64 has that name.
        let disks = vec![
            (raw("vbd0"), vec![found("vbd0s1", 1, vec![]), found("vbd0s2", 2, vec![]), found("vbd0s3", 3, vec![])], vec![("vbd0s2".to_string(), "BKO_0123abcd".to_string())]),
            (raw("vbd1"), vec![found("vbd1s1", 1, vec![])], vec![("vbd1s1".to_string(), "DragonFly_v6.4.2".to_string())]),
        ];
        assert_eq!(resolve("/dev/part-by-label/BKO_0123abcd.a", &disks).as_deref(), Some("vbd0s2a"));
        assert_eq!(resolve("/dev/part-by-label/DragonFly_v6.4.2.d", &disks).as_deref(), Some("vbd1s1d"));
        assert_eq!(resolve("/dev/part-by-label/BKO_other.a", &disks), None);
        assert_eq!(resolve("/dev/part-by-label/BKO_0123abcd", &disks), None);
        assert_eq!(resolve("/dev/part-by-label/BKO_0123abcd.ab", &disks), None);
        assert_eq!(resolve("/dev/part-by-label/BKO_0123abcd.1", &disks), None);
        // The slice itself is still found by its name.
        assert_eq!(resolve("/dev/vbd0s2", &disks).as_deref(), Some("vbd0s2"));
    }

    /// A slice with a bsdlabel: partitions (letter index, offset, sectors),
    /// c over the slice from `c_off`.
    fn bsd_slice(sectors: u32, c_off: u32, parts: &[(usize, u32, u32)]) -> Vec<u8> {
        let mut img = vec![0u8; sectors as usize * 512];
        let l = &mut img[512..1024];
        l[0..4].copy_from_slice(&0x8256_4557u32.to_le_bytes());
        l[40..44].copy_from_slice(&512u32.to_le_bytes());
        l[132..136].copy_from_slice(&0x8256_4557u32.to_le_bytes());
        l[138..140].copy_from_slice(&8u16.to_le_bytes());
        let mut put = |i: usize, off: u32, size: u32| {
            l[148 + 16 * i..152 + 16 * i].copy_from_slice(&size.to_le_bytes());
            l[152 + 16 * i..156 + 16 * i].copy_from_slice(&off.to_le_bytes());
        };
        put(2, c_off, sectors);
        for &(i, off, size) in parts {
            put(i, off, size);
        }
        img
    }

    #[test]
    fn bsdlabel_in_a_slice() {
        use crate::bytes::MemSource;
        // As mkimg -s bsd lays out FreeBSD's rescue image: "a" 16 sectors in.
        let mut img = bsd_slice(400, 0, &[(0, 16, 384)]);
        let ufs = crate::probe::ufs::sample::image("BACKOTTER-0123abcd");
        img[16 * 512..16 * 512 + ufs.len()].copy_from_slice(&ufs);
        let src = MemSource(img);
        assert_eq!(bsdlabel_parts(&src), vec![('a', 8192, 384 * 512)]);
        // The slice's own probe doesn't see that UFS; the partition's does.
        assert!(crate::probe::probe_all(&src).iter().all(|c| c.kind != "ufs"));
        let a = OffsetSource { inner: &src, start: 8192, len: 384 * 512 };
        assert_eq!(crate::probe::probe_all(&a).first().and_then(|c| c.label.clone()).as_deref(), Some("BACKOTTER-0123abcd"));
        // Offsets from the disk's start (c's offset is the slice's), and a
        // swap partition; one past the slice's end left out.
        let src = MemSource(bsd_slice(400, 1000, &[(0, 1016, 100), (1, 1116, 100), (3, 1300, 200)]));
        assert_eq!(bsdlabel_parts(&src), vec![('a', 8192, 51200), ('b', 116 * 512, 51200)]);
        // No label: nothing.
        assert!(bsdlabel_parts(&MemSource(vec![0u8; 4096])).is_empty());
        // A label whose fields would overflow (a 4 GiB "sector", the
        // largest sizes and offsets) or name a sector size that isn't one:
        // nothing, without a panic.
        for ss in [u32::MAX, 3000, 256] {
            let mut img = bsd_slice(400, 0, &[(0, u32::MAX, u32::MAX)]);
            img[512 + 40..512 + 44].copy_from_slice(&ss.to_le_bytes());
            assert!(bsdlabel_parts(&MemSource(img)).is_empty(), "{ss}");
        }
        let img = bsd_slice(400, 0, &[(0, u32::MAX, u32::MAX), (1, 16, 100)]);
        assert_eq!(bsdlabel_parts(&MemSource(img)), vec![('b', 8192, 51200)]);
    }

    #[test]
    fn ufs_label_of_a_bsdlabel_partition() {
        // FreeBSD's rescue image mounts / from /dev/ufs/BACKOTTER-...: found
        // once the slice's bsdlabel partitions are among the disk's.
        let ufs = Content { kind: "ufs".into(), usage: "filesystem", uuid: None, label: Some("BACKOTTER-0123abcd".into()), version: Some("2".into()), details: Obj::new(), extra: None };
        let disks = vec![(raw("vtbd0"), vec![found("vtbd0s1", 1, vec![]), found("vtbd0s2", 2, vec![]), found("vtbd0s2a", 2, vec![ufs])], vec![])];
        assert_eq!(resolve("/dev/ufs/BACKOTTER-0123abcd", &disks).as_deref(), Some("vtbd0s2a"));
        assert_eq!(resolve("/dev/ufs/BACKOTTER-00000000", &disks), None);
    }

    #[test]
    fn names() {
        let gpt = Table::Mbr(crate::parttable::Mbr { disk_signature: 0, boot_code: vec![], parts: vec![], problems: vec![] });
        assert_eq!(part_name("ada0", 1, &gpt), "ada0s1");
        assert_eq!(mbr_code_kind(&[0u8; 440]), "none");
    }
}
