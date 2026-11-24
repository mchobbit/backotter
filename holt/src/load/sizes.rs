//! A dump whose filesystem is bigger than the device it's on: its
//! superblock says more blocks than the partition, LV, md array or LUKS
//! mapping under it has.  No source system made that; the dump is
//! inconsistent (a device truncated since, a superblock misread, or a
//! filesystem grown past its partition), and load would run a mkfs that
//! can't fit (mke2fs ... 65536k on 28 MiB) or make something else than the
//! source had.  The kernel refuses such a filesystem too: ext4's "block
//! count exceeds size of device", XFS's "filesystem size mismatch", btrfs's
//! device total_bytes check, swapon's "swap area shorter than signature
//! indicates", md's "device too small".
//!
//! Each device's size is its own entry's (bytes; a partition's sectors are
//! the disk's logical sectors), and a filesystem is held to the device whose
//! entry holds it: inside LUKS, LVM or md that's the mapping's dm or md
//! device, smaller than the partition under it, and a zvol's its volsize.
//! Smaller than its device is normal and never said.  What the dump gives
//! no size for (UFS, ZFS, bcachefs, an LVM PV) isn't checked.

use super::codes::{Problem, ProblemCode};
use crate::json::Value;
use crate::report::human;

/// A number given as a number or as a decimal string (LUKS2 keeps its
/// offsets and sizes as strings).
fn u(v: &Value, k: &str) -> Option<u64> {
    match v.get(k)? {
        Value::Str(t) => t.parse().ok(),
        x => x.as_u64(),
    }
}

/// What CONTENT needs of the device it's on, in bytes, and what that is:
/// None where the dump doesn't say.
fn needs(c: &Value) -> Option<(u64, String)> {
    let kind = c.get("type").and_then(Value::as_str).unwrap_or("");
    match kind {
        // A btrfs's own total_bytes is every device's: the device's
        // (dev_item's) is this one's.
        "btrfs" => {
            let one = u(c, "num_devices").is_none_or(|n| n <= 1);
            let b = c.at(&["device"]).and_then(|d| u(d, "total_bytes")).or_else(|| u(c, "total_bytes").filter(|_| one))?;
            Some((b, "btrfs (its device item's total_bytes)".into()))
        }
        // The payload's offset, and its end where its size is fixed; a
        // dynamic one needs at least a sector past the offset.
        "crypto_LUKS" => {
            let seg = c.at(&["segments", "0"]);
            let off = seg.and_then(|s| u(s, "offset")).or_else(|| u(c, "data_offset_bytes"))?;
            match seg.and_then(|s| u(s, "size")) {
                Some(n) => Some((off + n, format!("LUKS container (payload {} at {})", human(n), human(off)))),
                None => Some((off + 512, format!("LUKS container (payload at {})", human(off)))),
            }
        }
        // md's v1 superblock: the data area (kernel super_1_load refuses a
        // device smaller); 0.90's: the size each member gives the array.
        "linux_raid_member" => match (u(c, "data_offset_bytes"), u(c, "data_size_bytes")) {
            (Some(off), Some(n)) if n > 0 => Some((off + n, format!("md member (data {} at {})", human(n), human(off)))),
            _ => u(c, "component_size_bytes").filter(|&n| n > 0).map(|n| (n, "md member (component size)".into())),
        },
        // v0 swap has no last_page: its header is a bitmap.
        "swap" if c.get("version").and_then(Value::as_str) == Some("0") => None,
        // An ISO 9660 volume (a hybrid image) may start in one partition
        // and run past it; load doesn't make one.
        "iso9660" => None,
        "" => None,
        k => {
            let b = u(c, "size_bytes").or_else(|| Some(u(c, "blocks")? * u(c, "block_size")?))?;
            Some((b, k.to_string()))
        }
    }
}

/// The device's name as the plan gives it: a dm device with its mapping's.
fn named(d: &Value) -> String {
    let name = d.get("name").and_then(Value::as_str).unwrap_or("?");
    match d.at(&["dm", "dm_name"]).and_then(Value::as_str) {
        Some(m) if m != name => format!("{name} ({m})"),
        _ => name.to_string(),
    }
}

fn check(out: &mut Vec<Problem>, dev: &str, size: Option<u64>, c: Option<&Value>) {
    let (Some(size), Some(c)) = (size.filter(|&s| s > 0), c.filter(|c| !c.is_null())) else { return };
    let Some((need, what)) = needs(c) else { return };
    if need > size {
        let text = format!(
            "{dev}: the dump's {what} is {} ({need} bytes), bigger than {dev} itself, {} ({size} bytes): the dump is inconsistent (a device truncated since, a superblock misread, or a filesystem grown past its partition), and load won't make what can't fit; check the source and dump it again",
            human(need),
            human(size)
        );
        out.push(Problem::new(ProblemCode::LayoutInconsistent, text).with("dev", dev));
    }
}

/// Every device of the dump holding a filesystem (or swap, a LUKS container
/// or an md member) bigger than itself.
pub fn problems(v: &Value) -> Vec<Problem> {
    let mut out = Vec::new();
    for sect in ["disks", "md_arrays", "dm_devices", "softraid_volumes"] {
        for d in v.at(&["storage", sect]).and_then(Value::as_array).into_iter().flatten() {
            if d.get("kind").and_then(Value::as_str) == Some("ram") {
                continue;
            }
            let sector = u(d, "logical_sector_size").unwrap_or(512).max(512);
            let name = named(d);
            check(&mut out, &name, u(d, "size_bytes"), d.get("content"));
            // A GELI provider (FreeBSD's): its content against its provsize.
            let geli = |out: &mut Vec<Problem>, p: &Value| {
                if let Some(g) = p.get("geli").filter(|g| !g.is_null()) {
                    let prov = g.get("provider").and_then(Value::as_str).map(str::to_string).unwrap_or_else(|| format!("{}.eli", p.get("name").and_then(Value::as_str).unwrap_or("?")));
                    check(out, &prov, u(g, "provsize"), g.get("content"));
                }
            };
            geli(&mut out, d);
            for k in ["partitions", "label_partitions"] {
                for p in d.get(k).and_then(Value::as_array).into_iter().flatten() {
                    let size = u(p, "size_bytes").or_else(|| u(p, "sectors").map(|n| n * sector));
                    check(&mut out, p.get("name").and_then(Value::as_str).unwrap_or("?"), size, p.get("content"));
                    geli(&mut out, p);
                }
            }
        }
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::json::parse;

    const M: u64 = 1 << 20;

    /// A dump with one 32 MiB disk vda whose partition vda1 is PART bytes
    /// and holds CONTENT, and DM (dm_devices entries).
    fn dump(part: u64, content: &str, dm: &str) -> Value {
        parse(&format!(
            r#"{{"storage": {{"disks": [{{"name": "vda", "kind": "disk", "size_bytes": {}, "logical_sector_size": 512, "content": null,
                "partitions": [{{"name": "vda1", "number": 1, "start_lba": 2048, "sectors": {}, "size_bytes": {part}, "content": {content}}}]}}],
              "md_arrays": [], "dm_devices": [{dm}]}}}}"#,
            32 * M,
            part / 512
        ))
        .unwrap()
    }

    fn ext4(blocks: u64) -> String {
        format!(r#"{{"type": "ext4", "block_size": 4096, "blocks": {blocks}, "size_bytes": {}}}"#, blocks * 4096)
    }

    /// The motivating dump: a full 64 MiB ext4 on a 28 MiB vda2.
    #[test]
    fn an_ext4_bigger_than_its_partition() {
        let p = problems(&dump(28 * M, &ext4(16384), ""));
        assert_eq!(p.len(), 1, "{p:?}");
        assert_eq!((p[0].code, p[0].str_field("dev")), (crate::load::codes::ProblemCode::LayoutInconsistent, Some("vda1")));
        assert!(p[0].starts_with("vda1: the dump's ext4 is 64 MiB (67108864 bytes), bigger than vda1 itself, 28 MiB (29360128 bytes): the dump is inconsistent"), "{}", p[0]);
        // Exactly the partition, and a little smaller: nothing to say.
        assert!(problems(&dump(28 * M, &ext4(7168), "")).is_empty());
        assert!(problems(&dump(28 * M, &ext4(7163), "")).is_empty());
        // One block over is over.
        assert_eq!(problems(&dump(28 * M, &ext4(7169), "")).len(), 1);
        // blocks and block_size alone are enough.
        assert_eq!(problems(&dump(28 * M, r#"{"type": "ext2", "block_size": 1024, "blocks": 30000}"#, "")).len(), 1);
    }

    /// Bytes, not sectors: a partition with sectors only is the disk's
    /// logical sectors, 4 KiB on a 4Kn disk.
    #[test]
    fn a_partitions_sectors_are_the_disks_logical_sectors() {
        let v = |ss: u64, sectors: u64| {
            parse(&format!(
                r#"{{"storage": {{"disks": [{{"name": "nvme0n1", "size_bytes": {}, "logical_sector_size": {ss}, "content": null,
                    "partitions": [{{"name": "nvme0n1p1", "sectors": {sectors}, "content": {}}}]}}]}}}}"#,
                64 * M,
                ext4(4096)
            ))
            .unwrap()
        };
        // 4096 4 KiB sectors are 16 MiB: the 16 MiB ext4 fits.
        assert!(problems(&v(4096, 4096)).is_empty());
        // 4096 512-byte sectors are 2 MiB: it doesn't.
        assert_eq!(problems(&v(512, 4096)).len(), 1);
    }

    /// Every sized kind, each bigger than its 28 MiB partition.
    #[test]
    fn every_kind_with_a_size() {
        let big = 30 * M;
        for (c, what) in [
            (format!(r#"{{"type": "xfs", "block_size": 4096, "blocks": {}, "size_bytes": {big}}}"#, big / 4096), "xfs is 30 MiB"),
            (format!(r#"{{"type": "f2fs", "block_size": 4096, "blocks": {}, "size_bytes": {big}}}"#, big / 4096), "f2fs is 30 MiB"),
            (format!(r#"{{"type": "vfat", "total_sectors": {}, "size_bytes": {big}}}"#, big / 512), "vfat is 30 MiB"),
            (format!(r#"{{"type": "swap", "version": "1", "page_size": 4096, "last_page": {}, "size_bytes": {big}}}"#, big / 4096 - 1), "swap is 30 MiB"),
            (format!(r#"{{"type": "jfs", "size_bytes": {big}}}"#), "jfs is 30 MiB"),
            (format!(r#"{{"type": "nilfs2", "size_bytes": {big}}}"#), "nilfs2 is 30 MiB"),
            (format!(r#"{{"type": "ntfs", "size_bytes": {big}}}"#), "ntfs is 30 MiB"),
            (format!(r#"{{"type": "exfat", "size_bytes": {big}}}"#), "exfat is 30 MiB"),
            (format!(r#"{{"type": "btrfs", "total_bytes": {big}, "num_devices": 1, "device": {{"devid": 1, "total_bytes": {big}}}}}"#), "btrfs (its device item's total_bytes) is 30 MiB"),
            (format!(r#"{{"type": "crypto_LUKS", "segments": [{{"offset": "{big}", "size": "dynamic"}}]}}"#), "LUKS container (payload at 30 MiB) is 30 MiB"),
            (format!(r#"{{"type": "crypto_LUKS", "segments": [{{"offset": "16777216", "size": "{}"}}]}}"#, 14 * M), "LUKS container (payload 14 MiB at 16 MiB) is 30 MiB"),
            (format!(r#"{{"type": "linux_raid_member", "metadata": "1.2", "data_offset_bytes": {}, "data_size_bytes": {}}}"#, 2 * M, 28 * M), "md member (data 28 MiB at 2 MiB) is 30 MiB"),
            (format!(r#"{{"type": "linux_raid_member", "metadata": "0.90", "component_size_bytes": {big}}}"#), "md member (component size) is 30 MiB"),
        ] {
            let p = problems(&dump(28 * M, &c, ""));
            assert_eq!(p.len(), 1, "{c}: {p:?}");
            assert!(p[0].starts_with(&format!("vda1: the dump's {what} ")), "{c}: {}", p[0]);
            // The same on a partition big enough for it is fine.
            assert!(problems(&dump(31 * M, &c, "")).is_empty(), "{c}");
        }
    }

    /// One device of a two-device btrfs: the filesystem's total_bytes is
    /// both, its device item's this one.
    #[test]
    fn a_btrfs_of_two_devices_is_held_to_its_own_device_item() {
        let c = format!(r#"{{"type": "btrfs", "total_bytes": {}, "num_devices": 2, "device": {{"devid": 1, "total_bytes": {}}}}}"#, 56 * M, 28 * M);
        assert!(problems(&dump(28 * M, &c, "")).is_empty());
        // Without a device item, the total says nothing about one device.
        let c = format!(r#"{{"type": "btrfs", "total_bytes": {}, "num_devices": 2}}"#, 56 * M);
        assert!(problems(&dump(28 * M, &c, "")).is_empty());
    }

    /// Inside LUKS or LVM the filesystem is held to the mapping, smaller
    /// than the partition: one that fits the partition but not the mapping
    /// is over, and the partition's own content (LUKS) is fine.
    #[test]
    fn a_filesystem_on_a_mapping_is_held_to_the_mapping() {
        let luks = r#"{"type": "crypto_LUKS", "segments": [{"offset": "16777216", "size": "dynamic"}]}"#;
        let dm = |fs: &str| format!(r#"{{"name": "dm-0", "kind": "dm", "size_bytes": {}, "slaves": ["vda1"], "content": {fs}, "dm": {{"dm_name": "cryptroot", "target": "crypt"}}}}"#, 12 * M);
        assert!(problems(&dump(28 * M, luks, &dm(&ext4(3072)))).is_empty());
        let p = problems(&dump(28 * M, luks, &dm(&ext4(4096))));
        assert_eq!(p.len(), 1, "{p:?}");
        assert!(p[0].starts_with("dm-0 (cryptroot): the dump's ext4 is 16 MiB (16777216 bytes), bigger than dm-0 (cryptroot) itself, 12 MiB"), "{}", p[0]);
        // An LV: the same.
        let lv = format!(r#"{{"name": "dm-1", "kind": "dm", "size_bytes": {}, "slaves": ["vda1"], "content": {}, "dm": {{"dm_name": "vg0-root", "target": "linear"}}}}"#, 8 * M, ext4(4096));
        let pv = r#"{"type": "LVM2_member", "device_size": 29360128}"#;
        let p = problems(&dump(28 * M, pv, &lv));
        assert_eq!(p.len(), 1, "{p:?}");
        assert!(p[0].starts_with("dm-1 (vg0-root): the dump's ext4 is 16 MiB"), "{}", p[0]);
    }

    /// md arrays and zvols are held to their own size; a GELI provider's
    /// content to its provsize.
    #[test]
    fn md_zvol_and_geli() {
        let v = parse(&format!(
            r#"{{"storage": {{
                "disks": [{{"name": "zd0", "size_bytes": {}, "links": ["/dev/zvol/rpool/swap"], "content": {{"type": "swap", "version": "1", "size_bytes": {}}}, "partitions": []}},
                          {{"name": "vtbd0", "size_bytes": {}, "logical_sector_size": 512, "content": null, "partitions": [
                             {{"name": "vtbd0p2", "size_bytes": {}, "content": null, "geli": {{"provider": "vtbd0p2.eli", "provsize": {}, "content": {}}}}}]}}],
                "md_arrays": [{{"name": "md0", "kind": "md", "size_bytes": {}, "content": {}, "partitions": []}}]}}}}"#,
            8 * M,
            9 * M,
            64 * M,
            16 * M,
            16 * M - 512,
            ext4(4096),
            10 * M,
            ext4(2560)
        ))
        .unwrap();
        let p = problems(&v);
        assert_eq!(p.len(), 2, "{p:?}");
        assert!(p[0].starts_with("zd0: the dump's swap is 9 MiB"), "{}", p[0]);
        assert!(p[1].starts_with("vtbd0p2.eli: the dump's ext4 is 16 MiB (16777216 bytes), bigger than vtbd0p2.eli itself, 16 MiB (16776704 bytes)"), "{}", p[1]);
    }

    /// What the dump gives no size for, and what isn't load's, isn't
    /// checked; nor is a device of unknown size.
    #[test]
    fn what_isnt_checked() {
        for c in [
            r#"{"type": "ufs"}"#,
            r#"{"type": "zfs_member"}"#,
            r#"{"type": "bcachefs"}"#,
            r#"{"type": "LVM2_member", "device_size": 999999999999}"#,
            r#"{"type": "swap", "version": "0", "size_bytes": 999999999999}"#,
            r#"{"type": "iso9660", "size_bytes": 999999999999}"#,
            r#"{"type": "linux_raid_member", "metadata": "1.2", "data_offset_bytes": 1048576, "data_size_bytes": 0}"#,
            "null",
        ] {
            assert!(problems(&dump(28 * M, c, "")).is_empty(), "{c}");
        }
        let v = parse(&format!(r#"{{"storage": {{"disks": [{{"name": "sr0", "size_bytes": 0, "content": {}}}]}}}}"#, ext4(4096))).unwrap();
        assert!(problems(&v).is_empty());
        let v = parse(&format!(r#"{{"storage": {{"disks": [{{"name": "ram0", "kind": "ram", "size_bytes": 4096, "content": {}}}]}}}}"#, ext4(4096))).unwrap();
        assert!(problems(&v).is_empty());
    }
}
