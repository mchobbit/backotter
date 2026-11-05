//! Puts the report together: every block device with what's on it, how the
//! devices stack (md, device-mapper, LVM, LUKS, ZFS, btrfs), what's mounted
//! where and what fstab/crypttab expect, and the boot setup.

use crate::blockdev::{self, BlockDev, Kind, Udev};
use crate::bootcode;
use crate::bytes::ByteSource;
use crate::json::{Obj, Value};
use crate::mounts::{self, Mount};
use crate::osrel::OsRelease;
use crate::parttable::{self, Table};
use crate::probe::{self, Content};
use crate::sys::Sys;
use std::collections::BTreeMap;

pub const FORMAT: &str = "bko-holt";
/// Earlier dumps, made before bko-laydump became bko-holt dump: the same
/// schema under the old name.
pub const FORMAT_OLD: &str = "bko-laydump";
pub const SCHEMA: u32 = 1;

/// One block device and what we learned about it.
struct Dev {
    b: BlockDev,
    table: Option<Table>,
    contents: Vec<Content>,
    boot_code: Option<bootcode::BootCode>,
    partition_boot_code: Option<&'static str>,
    first_partition_lba: Option<u64>,
    read_ok: bool,
}

impl Dev {
    fn ss(&self) -> u64 {
        self.b.logical_block_size.max(512)
    }
    // What's on the device: from its superblocks when we read it, from
    // udev's records only when we couldn't (udev's blkid can be wrong: it
    // calls a disk whose last partition is a ZFS vdev a zfs_member).
    fn fs_uuid(&self) -> Option<String> {
        if self.read_ok {
            return self.contents.first().and_then(|c| c.uuid.clone());
        }
        self.b.udev.get("ID_FS_UUID").map(str::to_string)
    }
    fn fs_label(&self) -> Option<String> {
        if self.read_ok {
            return self.contents.first().and_then(|c| c.label.clone());
        }
        self.b.udev.get("ID_FS_LABEL_ENC").map(udev_decode)
    }
    fn fs_type(&self) -> Option<String> {
        if self.read_ok {
            return self.contents.first().map(|c| c.kind.clone());
        }
        self.b.udev.get("ID_FS_TYPE").map(str::to_string)
    }
}

/// udev's \xNN escapes (ID_FS_LABEL_ENC, ID_PART_ENTRY_NAME).
pub fn udev_decode(s: &str) -> String {
    let b = s.as_bytes();
    let mut out = Vec::new();
    let mut i = 0;
    while i < b.len() {
        if b[i] == b'\\' && b.get(i + 1) == Some(&b'x') && i + 3 < b.len() {
            if let Ok(v) = u8::from_str_radix(&s[i + 2..i + 4], 16) {
                out.push(v);
                i += 4;
                continue;
            }
        }
        out.push(b[i]);
        i += 1;
    }
    String::from_utf8_lossy(&out).into_owned()
}

/// 1536 -> "1.5 KiB".
pub fn human(bytes: u64) -> String {
    const U: [&str; 7] = ["B", "KiB", "MiB", "GiB", "TiB", "PiB", "EiB"];
    let mut v = bytes as f64;
    let mut i = 0;
    while v >= 1024.0 && i < U.len() - 1 {
        v /= 1024.0;
        i += 1;
    }
    if i == 0 {
        format!("{bytes} B")
    } else if v >= 100.0 || (v - v.round()).abs() < 0.05 {
        format!("{} {}", v.round() as u64, U[i])
    } else {
        format!("{v:.1} {}", U[i])
    }
}

/// Seconds since the epoch as RFC 3339 UTC.
pub fn rfc3339(secs: u64) -> String {
    let days = (secs / 86400) as i64;
    let rem = secs % 86400;
    // Howard Hinnant's civil_from_days.
    let z = days + 719_468;
    let era = z.div_euclid(146_097);
    let doe = z - era * 146_097;
    let yoe = (doe - doe / 1460 + doe / 36524 - doe / 146_096) / 365;
    let y = yoe + era * 400;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let d = doy - (153 * mp + 2) / 5 + 1;
    let m = if mp < 10 { mp + 3 } else { mp - 9 };
    let y = if m <= 2 { y + 1 } else { y };
    format!("{y:04}-{m:02}-{d:02}T{:02}:{:02}:{:02}Z", rem / 3600, rem % 3600 / 60, rem % 60)
}

fn examine(sys: &Sys, b: BlockDev) -> Dev {
    let mut d = Dev { b, table: None, contents: vec![], boot_code: None, partition_boot_code: None, first_partition_lba: None, read_ok: false };
    // Removable-media drives: often empty (reads fail with ENXIO), and
    // never part of the system's layout.
    if matches!(d.b.kind, Kind::Rom | Kind::Floppy) {
        return d;
    }
    let Some(src) = sys.open_dev(&d.b.dev_path()) else { return d };
    d.read_ok = true;
    let whole = d.b.kind != Kind::Partition;
    if whole {
        d.table = parttable::read_table(&src, d.ss());
        if let Some(t) = &d.table {
            d.first_partition_lba = t.extents().iter().map(|e| e.1).filter(|&s| s > 0).min();
        }
        if d.table.is_some() || d.b.kind == Kind::Disk {
            d.boot_code = Some(bootcode::identify(&src.read_at(0, 512)));
        }
    } else {
        d.partition_boot_code = bootcode::partition_boot_code(&src);
    }
    // A GPT disk's sector 0 is its protective MBR, not a filesystem: only
    // probe a whole device's content when it has no partition table (or the
    // table is an isohybrid's MBR on top of an ISO).
    if !whole || d.table.is_none() || matches!(d.table, Some(Table::Mbr(_))) {
        d.contents = probe::probe_all(&src);
    }
    d
}

fn udev_content(u: &Udev) -> Value {
    let Some(t) = u.get("ID_FS_TYPE") else { return Value::Null };
    Obj::new()
        .set("type", t)
        .set("usage", u.get("ID_FS_USAGE"))
        .set("uuid", u.get("ID_FS_UUID"))
        .set("label", u.get("ID_FS_LABEL_ENC").map(udev_decode))
        .set("version", u.get("ID_FS_VERSION"))
        .set("source", "udev")
        .build()
}

fn content_json(d: &Dev) -> Value {
    if d.read_ok {
        probe::content_json(&d.contents)
    } else {
        udev_content(&d.b.udev)
    }
}

/// Partition table fields udev recorded, for when the disk can't be read.
fn udev_partition(u: &Udev) -> Obj {
    let mut o = Obj::new();
    o.put_some("scheme", u.get("ID_PART_ENTRY_SCHEME"));
    o.put_some("type", u.get("ID_PART_ENTRY_TYPE"));
    if let Some(t) = u.get("ID_PART_ENTRY_TYPE") {
        let name = if let Some(hex) = t.strip_prefix("0x") {
            u8::from_str_radix(hex, 16).ok().and_then(parttable::mbr_type_name)
        } else {
            parttable::gpt_type_name(t)
        };
        o.put("type_name", name);
    }
    o.put_some("uuid", u.get("ID_PART_ENTRY_UUID"));
    o.put_some("partlabel", u.get("ID_PART_ENTRY_NAME").map(udev_decode));
    o.put_some("flags", u.get("ID_PART_ENTRY_FLAGS"));
    o.put("source", "udev");
    o
}

struct Ctx<'a> {
    devs: &'a [Dev],
    mounts: &'a [Mount],
}

impl Ctx<'_> {
    fn dev(&self, name: &str) -> Option<&Dev> {
        self.devs.iter().find(|d| d.b.name == name)
    }
    fn by_devno(&self, devno: &str) -> Option<&Dev> {
        self.devs.iter().find(|d| d.b.devno == devno)
    }
    fn partition_uuid(&self, d: &Dev) -> Option<String> {
        if let (Some(parent), Some(n)) = (&d.b.parent, d.b.partition_number) {
            if let Some(t) = self.dev(parent).and_then(|p| p.table.as_ref()) {
                return t.partition_json(n, 512).and_then(|o| o.build().get("uuid").and_then(Value::as_str).map(str::to_string));
            }
        }
        d.b.udev.get("ID_PART_ENTRY_UUID").map(str::to_string)
    }
    fn partition_label(&self, d: &Dev) -> Option<String> {
        if let (Some(parent), Some(n)) = (&d.b.parent, d.b.partition_number) {
            if let Some(Table::Gpt { gpt, .. }) = self.dev(parent).and_then(|p| p.table.as_ref()) {
                return gpt.parts.iter().find(|p| p.number == n).map(|p| p.name.clone());
            }
        }
        d.b.udev.get("ID_PART_ENTRY_NAME").map(udev_decode)
    }

    /// The kernel name of the device an fstab/crypttab spec or a mount
    /// source names.
    fn resolve(&self, sys: &Sys, spec: &str) -> Option<String> {
        let (key, val) = spec.split_once('=').unwrap_or(("", spec));
        let val = val.trim_matches('"');
        let found = match key {
            "UUID" => self.devs.iter().find(|d| d.fs_uuid().is_some_and(|u| u.eq_ignore_ascii_case(val))),
            "LABEL" => self.devs.iter().find(|d| d.fs_label().as_deref() == Some(val)),
            "PARTUUID" => self.devs.iter().find(|d| self.partition_uuid(d).is_some_and(|u| u.eq_ignore_ascii_case(val))),
            "PARTLABEL" => self.devs.iter().find(|d| self.partition_label(d).as_deref() == Some(val)),
            "" if val.starts_with("/dev/") => {
                // Follow /dev/mapper/x, /dev/disk/by-*/x, /dev/vg/lv links.
                let canon = std::fs::canonicalize(sys.p(val)).ok();
                let name = canon.as_ref().and_then(|p| p.file_name()).map(|n| n.to_string_lossy().into_owned());
                let name = name.unwrap_or_else(|| val.trim_start_matches("/dev/").to_string());
                return self.dev(&name).map(|d| d.b.name.clone()).or_else(|| {
                    // A link udev knows of, though not on this root's /dev.
                    self.devs.iter().find(|d| d.b.udev.links.iter().any(|l| l == val)).map(|d| d.b.name.clone())
                });
            }
            _ => None,
        };
        found.map(|d| d.b.name.clone())
    }

    fn mounts_of(&self, sys: &Sys, d: &Dev) -> Vec<&Mount> {
        self.mounts
            .iter()
            .filter(|m| mounts::is_layout_mount(m))
            .filter(|m| m.devno == d.b.devno || (m.source.starts_with("/dev/") && self.resolve(sys, &m.source).as_deref() == Some(d.b.name.as_str())))
            .collect()
    }
}

fn partition_json(ctx: &Ctx, sys: &Sys, disk: &Dev, p: &Dev) -> Value {
    let mut o = Obj::new().set("name", p.b.name.as_str()).set("path", p.b.dev_path()).set("devno", p.b.devno.as_str());
    let n = p.b.partition_number.unwrap_or(0);
    let from_table = disk.table.as_ref().and_then(|t| t.partition_json(n, disk.ss()));
    match from_table {
        Some(t) => {
            let start = t.0.iter().find(|(k, _)| k == "start_bytes").and_then(|(_, v)| v.as_u64());
            let size = t.0.iter().find(|(k, _)| k == "size_bytes").and_then(|(_, v)| v.as_u64());
            for (k, v) in t.0 {
                o.put(&k, v);
            }
            let kstart = p.b.start_sectors.map(|s| s * 512);
            // The kernel's view of an extended partition is a 1 KiB stub.
            let is_ext = o.0.iter().any(|(k, v)| k == "extended" && *v == Value::Bool(true));
            o.put("matches_kernel", kstart == start && (is_ext || size == Some(p.b.size_bytes)));
        }
        None => {
            o.put("number", n);
            o.put("start_bytes", p.b.start_sectors.map(|s| s * 512));
            o.put("size_bytes", p.b.size_bytes);
            for (k, v) in udev_partition(&p.b.udev).0 {
                o.put(&k, v);
            }
        }
    }
    o.put("links", p.b.udev.links.clone());
    o.put("holders", p.b.holders.clone());
    o.put("content", content_json(p));
    o.put_some("boot_code", p.partition_boot_code);
    o.put("mounted_at", ctx.mounts_of(sys, p).iter().map(|m| m.target.clone()).collect::<Vec<_>>());
    o.build()
}

fn whole_json(ctx: &Ctx, sys: &Sys, d: &Dev, src: Option<&dyn ByteSource>) -> Obj {
    let b = &d.b;
    let mut o = Obj::new()
        .set("name", b.name.as_str())
        .set("path", b.dev_path())
        .set("kind", b.kind.name())
        .set("devno", b.devno.as_str())
        .set("size_bytes", b.size_bytes)
        .set("size", human(b.size_bytes))
        .set("logical_sector_size", b.logical_block_size)
        .set("physical_sector_size", b.physical_block_size);
    if b.kind == Kind::Disk {
        o.put("model", b.model.clone());
        o.put("vendor", b.vendor.clone());
        o.put("serial", b.serial.clone());
        o.put("wwid", b.wwid.clone());
        o.put("firmware_revision", b.firmware.clone());
        o.put("transport", b.transport());
        o.put("rotational", b.rotational);
        o.put("removable", b.removable);
    }
    o.put("read_only", b.read_only);
    o.put("discard", b.discard_max_bytes.map(|n| n > 0));
    o.put("links", b.udev.links.clone());
    o.put("holders", b.holders.clone());
    o.put("slaves", b.slaves.clone());
    let total_sectors = b.size_bytes / d.ss();
    let table = match &d.table {
        Some(t) => t.header_json(d.ss(), total_sectors),
        None if !d.read_ok => match b.udev.get("ID_PART_TABLE_TYPE") {
            Some(t) => Obj::new().set("type", t).set("uuid", b.udev.get("ID_PART_TABLE_UUID")).set("source", "udev").build(),
            None => Value::Null,
        },
        None => Value::Null,
    };
    o.put("partition_table", table);
    let parts: Vec<Value> = ctx
        .devs
        .iter()
        .filter(|p| p.b.parent.as_deref() == Some(b.name.as_str()))
        .map(|p| partition_json(ctx, sys, d, p))
        .collect();
    if let Some(Table::Gpt { gpt, .. }) = &d.table {
        // Entries the kernel didn't create a device for.
        let known: Vec<u32> = ctx.devs.iter().filter(|p| p.b.parent.as_deref() == Some(b.name.as_str())).filter_map(|p| p.b.partition_number).collect();
        let missing: Vec<u32> = gpt.parts.iter().map(|p| p.number).filter(|n| !known.contains(n)).collect();
        if !missing.is_empty() {
            o.put("partitions_without_device", missing);
        }
    }
    o.put("partitions", parts);
    if let Some(bc) = &d.boot_code {
        o.put("boot_code", bc.to_json(src, d.ss(), d.first_partition_lba));
    }
    o.put("content", content_json(d));
    o.put("mounted_at", ctx.mounts_of(sys, d).iter().map(|m| m.target.clone()).collect::<Vec<_>>());
    o
}

fn dm_json(d: &Dev) -> Value {
    let uuid = d.b.dm_uuid.clone().unwrap_or_default();
    let name = d.b.dm_name.clone().unwrap_or_default();
    let mut o = Obj::new().set("dm_name", name.as_str()).set("dm_uuid", uuid.as_str()).set("mapper_path", format!("/dev/mapper/{name}"));
    if let Some(rest) = uuid.strip_prefix("LVM-") {
        o.put("target", "lvm");
        if let Some((vg, lv)) = blockdev::split_lvm_dm_name(&name) {
            o.put("vg", vg);
            o.put("lv", lv);
        }
        if rest.len() >= 64 {
            o.put("vg_uuid", crate::probe::lvm::format_uuid(&rest[..32]));
            o.put("lv_uuid", crate::probe::lvm::format_uuid(&rest[32..64]));
            o.put_some("lv_layer", rest.get(64..).filter(|s| !s.is_empty()).map(|s| s.trim_start_matches('-').to_string()));
        }
    } else if let Some(rest) = uuid.strip_prefix("CRYPT-") {
        o.put("target", "crypt");
        let (kind, tail) = rest.split_once('-').unwrap_or((rest, ""));
        o.put("crypt_type", kind);
        if kind.starts_with("LUKS") && tail.len() >= 32 {
            let h = &tail[..32];
            o.put("luks_uuid", format!("{}-{}-{}-{}-{}", &h[0..8], &h[8..12], &h[12..16], &h[16..20], &h[20..32]));
        }
    } else if uuid.starts_with("mpath-") {
        o.put("target", "multipath");
    } else if uuid.starts_with("part") && uuid.contains('-') {
        o.put("target", "partition");
    } else if uuid.starts_with("INTEGRITY-") {
        o.put("target", "integrity");
    } else {
        o.put("target", if uuid.is_empty() { "unknown" } else { "other" });
    }
    o.build()
}

fn md_json(sys: &Sys, d: &Dev) -> Value {
    let base = format!("/sys/block/{}/md", d.b.name);
    let r = |f: &str| sys.read_trim(&format!("{base}/{f}"));
    let members: Vec<Value> = sys
        .list(&base)
        .into_iter()
        .filter_map(|e| e.strip_prefix("dev-").map(str::to_string))
        .map(|m| {
            Obj::new()
                .set("device", m.as_str())
                .set("slot", sys.read_trim(&format!("{base}/dev-{m}/slot")))
                .set("state", sys.read_trim(&format!("{base}/dev-{m}/state")))
                .build()
        })
        .collect();
    Obj::new()
        .set("level", r("level"))
        .set("raid_disks", r("raid_disks").and_then(|v| v.parse::<u32>().ok()))
        .set("metadata_version", r("metadata_version"))
        .set("chunk_size", r("chunk_size").and_then(|v| v.parse::<u64>().ok()))
        .set("layout", r("layout"))
        .set("array_state", r("array_state"))
        .set("degraded", r("degraded").and_then(|v| v.parse::<u32>().ok()))
        .set("sync_action", r("sync_action"))
        .set("uuid", d.b.udev.get("MD_UUID"))
        .set("name", d.b.udev.get("MD_NAME"))
        .set("devname", d.b.udev.get("MD_DEVNAME"))
        .set("members", members)
        .build()
}

fn lvm_json(ctx: &Ctx) -> Value {
    // Each VG from the PV with the newest metadata.
    let mut vgs: BTreeMap<String, (u64, Value)> = BTreeMap::new();
    let mut pv_dev: BTreeMap<String, String> = BTreeMap::new();
    for d in ctx.devs {
        for c in &d.contents {
            if c.kind != "LVM2_member" {
                continue;
            }
            if let Some(u) = &c.uuid {
                pv_dev.insert(u.clone(), d.b.name.clone());
            }
            if let Some(vg) = &c.extra {
                let id = vg.get("id").and_then(Value::as_str).unwrap_or("").to_string();
                let seq = vg.get("seqno").and_then(Value::as_u64).unwrap_or(0);
                if vgs.get(&id).is_none_or(|(s, _)| *s < seq) {
                    vgs.insert(id, (seq, vg.clone()));
                }
            }
        }
    }
    let mut out = Vec::new();
    for (_, (_, mut vg)) in vgs {
        // Tie PVs to devices and LVs to their dm devices.
        if let Value::Obj(o) = &mut vg {
            for (k, v) in o.iter_mut() {
                if k == "physical_volumes" {
                    if let Value::Arr(pvs) = v {
                        for pv in pvs.iter_mut() {
                            let dev = pv.get("id").and_then(Value::as_str).and_then(|id| pv_dev.get(id)).cloned();
                            if let Value::Obj(p) = pv {
                                p.push(("device".into(), Value::from(dev)));
                            }
                        }
                    }
                }
            }
            let vgid: String = o.iter().find(|(k, _)| k == "id").and_then(|(_, v)| v.as_str()).unwrap_or("").replace('-', "");
            for (k, v) in o.iter_mut() {
                if k == "logical_volumes" {
                    if let Value::Arr(lvs) = v {
                        for lv in lvs.iter_mut() {
                            let lvid = lv.get("id").and_then(Value::as_str).unwrap_or("").replace('-', "");
                            let want = format!("LVM-{vgid}{lvid}");
                            let dev = ctx.devs.iter().find(|d| d.b.dm_uuid.as_deref() == Some(want.as_str())).map(|d| d.b.name.clone());
                            if let Value::Obj(l) = lv {
                                l.push(("active_device".into(), Value::from(dev)));
                            }
                        }
                    }
                }
            }
        }
        out.push(vg);
    }
    Obj::new().set("volume_groups", out).build()
}

/// Imported pools' properties and datasets, from the zfs tools.
fn zfs_tools_json(sys: &Sys) -> Value {
    let Some(list) = sys.run("zpool", &["list", "-H", "-o", "name"]) else { return Value::Null };
    let mut pools = Vec::new();
    for name in list.lines().map(str::trim).filter(|l| !l.is_empty()) {
        let mut props = Obj::new();
        let mut local = Obj::new();
        let mut features = Obj::new();
        if let Some(out) = sys.run("zpool", &["get", "-H", "-p", "-o", "property,value,source", "all", name]) {
            for l in out.lines() {
                let f: Vec<&str> = l.split('\t').collect();
                if f.len() < 3 {
                    continue;
                }
                if let Some(feat) = f[0].strip_prefix("feature@") {
                    features.put(feat, f[1]);
                } else if !matches!(f[0], "free" | "allocated" | "fragmentation" | "capacity" | "freeing" | "leaked" | "expandsize" | "checkpoint" | "dedupratio" | "load_guid" | "bcloneused" | "bclonesaved" | "bcloneratio" | "dedup_table_size" | "dedupcached" | "dedupsaved" | "dedupused") {
                    props.put(f[0], f[1]);
                    // What `zpool create -o` must set again: the properties
                    // set on this pool rather than defaulted.
                    if f[2] == "local" {
                        local.put(f[0], f[1]);
                    }
                }
            }
        }
        pools.push(
            Obj::new()
                .set("name", name)
                .set("properties", props)
                .set("local_properties", local)
                .set("features", features)
                .set("vdevs", zpool_vdevs(sys, name))
                .build(),
        );
    }
    let cols = "name,type,used,available,referenced,mountpoint,canmount,mounted,encryption,encryptionroot,keyformat,keylocation,keystatus,origin,compression,recordsize,volsize,volblocksize,acltype,xattr,dnodesize,normalization,utf8only,casesensitivity,atime,relatime,copies,com.sun:auto-snapshot";
    let datasets: Vec<Value> = sys
        .run("zfs", &["list", "-H", "-p", "-t", "filesystem,volume", "-o", cols])
        .map(|out| {
            let names: Vec<&str> = cols.split(',').collect();
            out.lines()
                .map(|l| {
                    let mut o = Obj::new();
                    for (k, v) in names.iter().zip(l.split('\t')) {
                        let v = if v == "-" { Value::Null } else { v.parse::<u64>().map(Value::UInt).unwrap_or_else(|_| Value::from(v)) };
                        o.put(k, v);
                    }
                    o.build()
                })
                .collect()
        })
        .unwrap_or_default();
    // Every property set on a dataset or received with it, user properties
    // included: the fixed columns above are for people to read, these are
    // what `zfs create -o` needs to make the dataset again.
    let mut props: BTreeMap<String, Obj> = BTreeMap::new();
    if let Some(out) = sys.run("zfs", &["get", "-H", "-p", "-t", "filesystem,volume", "-s", "local,received", "-o", "name,property,value,source", "all"]) {
        for (ds, prop, value, source) in parse_zfs_get(&out) {
            props.entry(ds).or_default().put(&prop, Obj::new().set("value", value).set("source", source));
        }
    }
    let datasets: Vec<Value> = datasets
        .into_iter()
        .map(|mut d| {
            let name = d.get("name").and_then(Value::as_str).unwrap_or("").to_string();
            if let Value::Obj(o) = &mut d {
                o.push(("properties".into(), props.remove(&name).map(Obj::build).unwrap_or_else(|| Obj::new().build())));
            }
            d
        })
        .collect();
    Obj::new().set("imported_pools", pools).set("datasets", datasets).build()
}

/// `zfs get -H -o name,property,value,source` lines.
pub fn parse_zfs_get(out: &str) -> Vec<(String, String, String, String)> {
    out.lines()
        .filter_map(|l| {
            let f: Vec<&str> = l.splitn(4, '\t').collect();
            (f.len() == 4).then(|| (f[0].to_string(), f[1].to_string(), f[2].to_string(), f[3].to_string()))
        })
        .collect()
}

/// An imported pool's whole vdev tree (data, log, special, dedup, cache and
/// spare vdevs), from `zpool status -j` (OpenZFS 2.3 and later): the labels
/// carry only the data and log vdevs, and each only its own top-level one.
fn zpool_vdevs(sys: &Sys, name: &str) -> Value {
    let Some(out) = sys.run("zpool", &["status", "-j", "--json-int", "-P", name]) else {
        // OpenZFS before 2.3 (Ubuntu 24.04's 2.2) has no -j: read the text.
        if let Some(t) = sys.run("zpool", &["status", "-P", name]) {
            if let Some(v) = parse_zpool_status_text(&t, name) {
                return v;
            }
        }
        sys.warn(format!("zpool status {name} failed: the pool's vdev tree (log, cache, spare vdevs) isn't recorded"));
        return Value::Null;
    };
    match crate::json::parse(&out) {
        Ok(v) => {
            let pool = v.at(&["pools", name]);
            let pick = |k: &str| pool.and_then(|p| p.get(k)).cloned().unwrap_or(Value::Null);
            Obj::new()
                .set("vdevs", pick("vdevs"))
                .set("logs", pick("logs"))
                .set("l2cache", pick("l2cache"))
                .set("spares", pick("spares"))
                .set("special", pick("special"))
                .set("dedup", pick("dedup"))
                .build()
        }
        Err(e) => {
            sys.warn(format!("zpool status -j {name}: unreadable JSON ({} at {})", e.message, e.offset));
            Value::Null
        }
    }
}

/// `zpool status -P POOL`'s config section, shaped as `zpool status -j`'s
/// vdev tree: {"vdevs": {POOL: {"vdev_type": "root", "vdevs": {...}}},
/// "logs": {...}, "l2cache", "spares", "special", "dedup"}.  Each level of
/// the tree is indented two spaces more than its parent.
pub fn parse_zpool_status_text(text: &str, pool: &str) -> Option<Value> {
    fn vtype(name: &str) -> &'static str {
        let word = name.rsplit_once('-').map(|(w, _)| w).unwrap_or(name);
        match word {
            "mirror" => "mirror",
            "replacing" => "replacing",
            "spare" => "spare",
            w if w.starts_with("raidz") => "raidz",
            w if w.starts_with("draid") => "draid",
            _ if name.starts_with('/') => "disk",
            _ => "unknown",
        }
    }
    fn vdev(name: &str, kids: Vec<(String, Value)>) -> Value {
        let ty = vtype(name);
        let mut o = Obj::new().set("name", name).set("vdev_type", ty);
        if ty == "disk" {
            o.put("path", name);
        }
        if !kids.is_empty() {
            let mut k = Obj::new();
            for (n, v) in kids {
                k.put(&n, v);
            }
            o.put("vdevs", k.build());
        }
        o.build()
    }
    let mut lines = text.lines().skip_while(|l| l.trim() != "config:").skip(1);
    // Sections: (key, top-level vdevs as (name, children)).
    // (section, its top-level vdevs as (name, their children as (name, grandchildren))).
    type Kids = Vec<(String, Vec<(String, Value)>)>;
    let mut sections: Vec<(&str, Vec<(String, Kids)>)> = Vec::new();
    let mut seen_header = false;
    for l in lines.by_ref() {
        let body = l.trim_start_matches('\t');
        let name = body.split_whitespace().next().unwrap_or("");
        if !seen_header {
            seen_header = name == "NAME";
            continue;
        }
        if name.is_empty() || l.starts_with("errors:") {
            break;
        }
        let indent = body.len() - body.trim_start().len();
        match indent {
            0 => {
                let key = match name {
                    n if n == pool => "vdevs",
                    "logs" => "logs",
                    "cache" => "l2cache",
                    "spares" => "spares",
                    "special" => "special",
                    "dedup" => "dedup",
                    _ => return None,
                };
                sections.push((key, Vec::new()));
            }
            2 => sections.last_mut()?.1.push((name.to_string(), Vec::new())),
            4 => sections.last_mut()?.1.last_mut()?.1.push((name.to_string(), Vec::new())),
            // A replacing or spare vdev inside a mirror or raidz.
            6 => sections.last_mut()?.1.last_mut()?.1.last_mut()?.1.push((name.to_string(), vdev(name, vec![]))),
            _ => return None,
        }
    }
    if !sections.iter().any(|(k, _)| *k == "vdevs") {
        return None;
    }
    let mut out = Obj::new();
    for key in ["vdevs", "logs", "l2cache", "spares", "special", "dedup"] {
        let Some((_, tops)) = sections.iter().find(|(k, _)| *k == key) else {
            out.put(key, Value::Null);
            continue;
        };
        let mut t = Obj::new();
        for (n, kids) in tops {
            let kids: Vec<(String, Value)> = kids.iter().map(|(cn, gk)| (cn.clone(), vdev(cn, gk.clone()))).collect();
            t.put(n, vdev(n, kids));
        }
        let t = t.build();
        if key == "vdevs" {
            out.put(key, Obj::new().set(pool, Obj::new().set("name", pool).set("vdev_type", "root").set("vdevs", t).build()).build());
        } else {
            out.put(key, t);
        }
    }
    Some(out.build())
}

fn zfs_json(sys: &Sys, ctx: &Ctx) -> Value {
    // Pools from the labels of their vdevs: each label has the pool's
    // config with its own top-level vdev; merge top-level vdevs by id.
    // pool GUID -> (first label's config, top-level vdevs by id, member devices)
    type Pool = (Value, BTreeMap<u64, Value>, Vec<String>);
    let mut pools: BTreeMap<String, Pool> = BTreeMap::new();
    for d in ctx.devs {
        for c in &d.contents {
            let (Some(cfg), "zfs_member") = (&c.extra, c.kind.as_str()) else { continue };
            let guid = cfg.get("pool_guid").and_then(Value::as_u64).map(|g| g.to_string()).unwrap_or_default();
            let e = pools.entry(guid).or_insert_with(|| (cfg.clone(), BTreeMap::new(), Vec::new()));
            e.2.push(d.b.name.clone());
            if let Some(t) = cfg.get("vdev_tree") {
                let id = t.get("id").and_then(Value::as_u64).unwrap_or(0);
                e.1.entry(id).or_insert_with(|| t.clone());
            }
        }
    }
    let label_pools: Vec<Value> = pools
        .into_iter()
        .map(|(guid, (cfg, tops, devs))| {
            let g = |k: &str| cfg.get(k).cloned().unwrap_or(Value::Null);
            Obj::new()
                .set("name", g("name"))
                .set("pool_guid", guid)
                .set("state", g("state"))
                .set("txg", g("txg"))
                .set("version", g("version"))
                .set("hostname", g("hostname"))
                .set("hostid", g("hostid"))
                .set("vdev_children", g("vdev_children"))
                .set("top_level_vdevs", tops.into_values().collect::<Vec<_>>())
                .set("features_for_read", g("features_for_read"))
                .set("member_devices", devs)
                .build()
        })
        .collect();
    let cache = sys.read_bytes("/etc/zfs/zpool.cache").map(|b| match crate::probe::zfs::unpack(&b) {
        Ok(v) => v,
        Err(e) => Obj::new().set("error", e).build(),
    });
    Obj::new().set("pools_from_labels", label_pools).set("zpool_cache", cache).set("tools", zfs_tools_json(sys)).build()
}

fn btrfs_json(sys: &Sys, ctx: &Ctx) -> Value {
    let mut fss: BTreeMap<String, Vec<&Dev>> = BTreeMap::new();
    for d in ctx.devs {
        if d.fs_type().as_deref() == Some("btrfs") {
            if let Some(u) = d.fs_uuid() {
                fss.entry(u).or_default().push(d);
            }
        }
    }
    let mut out = Vec::new();
    for (uuid, devs) in fss {
        let sysfs = format!("/sys/fs/btrfs/{uuid}");
        let mut alloc = Obj::new();
        for kind in ["data", "metadata", "system"] {
            let profiles: Vec<String> = sys
                .list(&format!("{sysfs}/allocation/{kind}"))
                .into_iter()
                .filter(|p| sys.is_dir(&format!("{sysfs}/allocation/{kind}/{p}")))
                .collect();
            alloc.put(kind, profiles);
        }
        let mounts: Vec<&Mount> = ctx.mounts.iter().filter(|m| m.fstype == "btrfs" && devs.iter().any(|d| ctx.resolve(sys, &m.source).as_deref() == Some(d.b.name.as_str()))).collect();
        let mut subvols = Value::Null;
        let mut default = Value::Null;
        let mut qgroups = Value::Null;
        if let Some(m) = mounts.first() {
            if let Some(out) = sys.run("btrfs", &["subvolume", "list", "-a", "-p", "-u", "-q", &m.target]) {
                let ro: Vec<u64> = sys
                    .run("btrfs", &["subvolume", "list", "-a", "-r", &m.target])
                    .map(|o| o.lines().filter_map(parse_btrfs_subvol_line).filter_map(|v| v.get("id").and_then(Value::as_u64)).collect())
                    .unwrap_or_default();
                let list = out
                    .lines()
                    .filter_map(parse_btrfs_subvol_line)
                    .map(|mut v| {
                        let id = v.get("id").and_then(Value::as_u64).unwrap_or(0);
                        let path = v.get("path").and_then(Value::as_str).map(str::to_string);
                        let props = path.and_then(|p| btrfs_reachable_path(&mounts, &p)).map(|p| btrfs_properties(sys, &p));
                        if let Value::Obj(o) = &mut v {
                            o.push(("readonly".into(), Value::Bool(ro.contains(&id))));
                            o.push(("properties".into(), props.map(Obj::build).unwrap_or(Value::Null)));
                        }
                        v
                    })
                    .collect();
                subvols = Value::Arr(list);
            }
            if let Some(out) = sys.run("btrfs", &["subvolume", "get-default", &m.target]) {
                default = parse_btrfs_subvol_line(out.trim()).unwrap_or(Value::from(out.trim()));
            }
            // Quotas: sysfs has a qgroups directory while they're enabled.
            if sys.is_dir(&format!("{sysfs}/qgroups")) {
                qgroups = match sys.run("btrfs", &["qgroup", "show", "-p", "-r", "-e", "--raw", &m.target]) {
                    Some(out) => Value::Arr(parse_btrfs_qgroups(&out)),
                    None => Obj::new().set("error", "btrfs qgroup show failed").build(),
                };
            }
        }
        out.push(
            Obj::new()
                .set("uuid", uuid.as_str())
                .set("label", devs.first().and_then(|d| d.fs_label()))
                .set("devices", devs.iter().map(|d| d.b.name.clone()).collect::<Vec<_>>())
                .set("mounted", !mounts.is_empty())
                .set("features", sys.is_dir(&sysfs).then(|| sys.list(&format!("{sysfs}/features"))))
                .set("allocation_profiles", if sys.is_dir(&sysfs) { alloc.build() } else { Value::Null })
                .set(
                    "mounted_subvolumes",
                    mounts.iter().map(|m| Obj::new().set("target", m.target.as_str()).set("subvol", m.btrfs_subvol()).build()).collect::<Vec<_>>(),
                )
                .set("subvolumes", subvols)
                .set("default_subvolume", default)
                .set("quota_enabled", sys.is_dir(&sysfs).then(|| sys.is_dir(&format!("{sysfs}/qgroups"))))
                .set("qgroups", qgroups)
                .build(),
        );
    }
    Value::Arr(out)
}

/// Where a subvolume (its path from the top level, as `btrfs subvolume list
/// -a` prints it) can be reached through one of the filesystem's mounts.
pub fn btrfs_reachable_path(mounts: &[&Mount], path: &str) -> Option<String> {
    let path = path.strip_prefix("<FS_TREE>/").unwrap_or(path).trim_matches('/');
    for m in mounts {
        let sub = m.btrfs_subvol().unwrap_or_default();
        let sub = sub.trim_matches('/');
        let rest = if sub.is_empty() {
            Some(path)
        } else if path == sub {
            Some("")
        } else {
            path.strip_prefix(sub).and_then(|r| r.strip_prefix('/'))
        };
        if let Some(r) = rest {
            let t = m.target.trim_end_matches('/');
            return Some(if r.is_empty() { m.target.clone() } else { format!("{t}/{r}") });
        }
    }
    None
}

/// A subvolume's properties: `btrfs property get` for the subvolume (ro)
/// and for its root directory's inode (compression).
fn btrfs_properties(sys: &Sys, path: &str) -> Obj {
    let mut o = Obj::new();
    for t in ["s", "i"] {
        if let Some(out) = sys.run("btrfs", &["property", "get", "-t", t, path]) {
            for (k, v) in out.lines().filter_map(|l| l.trim().split_once('=')) {
                o.put(k, v);
            }
        }
    }
    o
}

/// `btrfs qgroup show -p -r -e --raw`: qgroupid, referenced, exclusive,
/// max referenced, max exclusive, parents, and (btrfs-progs 6.x) the path.
pub fn parse_btrfs_qgroups(out: &str) -> Vec<Value> {
    let num = |s: &str| match s {
        "none" | "-" => Value::Null,
        _ => s.parse::<u64>().map(Value::UInt).unwrap_or_else(|_| Value::from(s)),
    };
    out.lines()
        .skip_while(|l| !l.trim_start().starts_with("--"))
        .skip(1)
        .filter_map(|l| {
            let w: Vec<&str> = l.split_whitespace().collect();
            if w.len() < 6 || !w[0].contains('/') {
                return None;
            }
            // "-" (btrfs-progs 6) or "---" (older) when there's no parent.
            let parents: Vec<String> = if w[5].chars().all(|c| c == '-') { vec![] } else { w[5].split(',').map(str::to_string).collect() };
            Some(
                Obj::new()
                    .set("qgroupid", w[0])
                    .set("referenced", num(w[1]))
                    .set("exclusive", num(w[2]))
                    .set("max_referenced", num(w[3]))
                    .set("max_exclusive", num(w[4]))
                    .set("parents", parents)
                    .set("path", (w.len() > 6).then(|| w[6..].join(" ")))
                    .build(),
            )
        })
        .collect()
}

/// Each XFS filesystem with an external log, and the device holding that
/// log: by the filesystem UUID in the log's record headers, or the logdev=
/// mount option when the log device couldn't be read.
fn xfs_external_logs(sys: &Sys, ctx: &Ctx) -> Value {
    let mut out = Vec::new();
    for d in ctx.devs {
        let Some(c) = d.contents.first().filter(|c| c.kind == "xfs") else { continue };
        let internal = c.details.0.iter().find(|(k, _)| k == "log_internal").and_then(|(_, v)| v.as_bool());
        if internal != Some(false) {
            continue;
        }
        let uuid = c.uuid.clone().unwrap_or_default();
        let by_uuid = ctx.devs.iter().find(|l| {
            l.contents.iter().any(|lc| {
                lc.kind == "xfs_external_log"
                    && lc.details.0.iter().any(|(k, v)| k == "filesystem_uuid" && v.as_str().is_some_and(|u| u.eq_ignore_ascii_case(&uuid)))
            })
        });
        let option = ctx
            .mounts_of(sys, d)
            .iter()
            .flat_map(|m| m.options.split(',').chain(m.super_options.split(',')))
            .find_map(|o| o.strip_prefix("logdev="))
            .map(str::to_string);
        let by_option = option.as_deref().and_then(|o| ctx.resolve(sys, o));
        out.push(
            Obj::new()
                .set("filesystem_uuid", uuid.as_str())
                .set("filesystem_device", d.b.name.as_str())
                .set("log_device", by_uuid.map(|l| l.b.name.clone()).or(by_option))
                .set("logdev_option", option)
                .build(),
        );
    }
    Value::Arr(out)
}

/// "ID 256 gen 10 parent 5 top level 5 parent_uuid - received_uuid - uuid ... path @rootfs"
pub fn parse_btrfs_subvol_line(l: &str) -> Option<Value> {
    let w: Vec<&str> = l.split_whitespace().collect();
    if w.first() != Some(&"ID") {
        return None;
    }
    let mut o = Obj::new();
    let mut i = 0;
    while i + 1 < w.len() {
        let (key, n) = match (w[i], w.get(i + 1)) {
            ("top", Some(&"level")) => ("top_level", 2),
            ("path", _) => {
                o.put("path", w[i + 1..].join(" "));
                break;
            }
            (k, _) => (k, 1),
        };
        let v = w.get(i + n).copied().unwrap_or("");
        let v = match v.parse::<u64>() {
            Ok(n) => Value::UInt(n),
            Err(_) if v == "-" => Value::Null,
            Err(_) => Value::from(v),
        };
        o.put(&key.to_ascii_lowercase(), v);
        i += n + 1;
    }
    Some(o.build())
}

/// The stacking of devices as a tree, for a person to read: keys describe a
/// device, values hold what sits on it.
fn topology(ctx: &Ctx, sys: &Sys) -> Value {
    fn describe(ctx: &Ctx, sys: &Sys, d: &Dev) -> String {
        let mut s = format!("{} ({}, {}", d.b.name, d.b.kind.name(), human(d.b.size_bytes));
        if let Some(t) = &d.table {
            s.push_str(match t {
                Table::Gpt { .. } => ", gpt",
                Table::Mbr(_) => ", dos",
            });
        }
        if let Some(n) = &d.b.dm_name {
            s.push_str(&format!(", {n}"));
        }
        s.push(')');
        if let Some(t) = d.fs_type() {
            s.push_str(&format!(" {t}"));
            if let Some(l) = d.fs_label() {
                s.push_str(&format!(" \"{l}\""));
            }
        }
        let m: Vec<String> = ctx.mounts_of(sys, d).iter().map(|m| m.target.clone()).collect();
        if !m.is_empty() {
            s.push_str(&format!(" on {}", m.join(", ")));
        }
        s
    }
    fn node(ctx: &Ctx, sys: &Sys, d: &Dev, depth: usize) -> Value {
        let mut o = Obj::new();
        if depth > 16 {
            return o.build();
        }
        let mut kids: Vec<&Dev> = ctx.devs.iter().filter(|p| p.b.parent.as_deref() == Some(d.b.name.as_str())).collect();
        kids.extend(d.b.holders.iter().filter_map(|h| ctx.dev(h)));
        for k in kids {
            o.put(&describe(ctx, sys, k), node(ctx, sys, k, depth + 1));
        }
        o.build()
    }
    let mut o = Obj::new();
    for d in ctx.devs.iter().filter(|d| d.b.slaves.is_empty() && !matches!(d.b.kind, Kind::Partition | Kind::Zram | Kind::Rom | Kind::Floppy)) {
        o.put(&describe(ctx, sys, d), node(ctx, sys, d, 0));
    }
    o.build()
}

fn host_json(sys: &Sys) -> Value {
    let dmi = |f: &str| sys.read_trim(&format!("/sys/class/dmi/id/{f}"));
    Obj::new()
        .set("hostname", sys.read_trim("/proc/sys/kernel/hostname"))
        .set("machine_id", sys.read_trim("/etc/machine-id"))
        .set(
            "dmi",
            Obj::new()
                .set("sys_vendor", dmi("sys_vendor"))
                .set("product_name", dmi("product_name"))
                .set("product_version", dmi("product_version"))
                .set("board_vendor", dmi("board_vendor"))
                .set("board_name", dmi("board_name"))
                .set("bios_vendor", dmi("bios_vendor"))
                .set("bios_version", dmi("bios_version"))
                .set("bios_date", dmi("bios_date"))
                .set("product_uuid", dmi("product_uuid"))
                .set("chassis_type", dmi("chassis_type")),
        )
        .set("virtualization", sys.run("systemd-detect-virt", &[]).map(|s| s.trim().to_string()))
        .build()
}

pub fn build(sys: &Sys, os: Option<&OsRelease>, generated: &str) -> Value {
    if !sys.privileged {
        sys.warn("not running as root: partition tables, superblocks and LVM/ZFS labels were not read; device contents come from udev's database");
    }
    let devs: Vec<Dev> = blockdev::scan(sys).into_iter().map(|b| examine(sys, b)).collect();
    let mountinfo = sys.read("/proc/self/mountinfo").map(|t| mounts::parse_mountinfo(&t)).unwrap_or_default();
    let ctx = Ctx { devs: &devs, mounts: &mountinfo };

    let section = |kinds: &[Kind], extra: &dyn Fn(&Dev) -> Option<(&'static str, Value)>| -> Vec<Value> {
        devs.iter()
            .filter(|d| kinds.contains(&d.b.kind))
            .map(|d| {
                let src = if d.read_ok { sys.open_dev(&d.b.dev_path()) } else { None };
                let mut o = whole_json(&ctx, sys, d, src.as_ref().map(|s| s as &dyn ByteSource));
                if let Some((k, v)) = extra(d) {
                    o.put(k, v);
                }
                o.build()
            })
            .collect()
    };
    let disks = section(&[Kind::Disk, Kind::Nbd, Kind::Ram], &|_| None);
    let md = section(&[Kind::Md], &|d| Some(("md", md_json(sys, d))));
    let dm = section(&[Kind::Dm], &|d| Some(("dm", dm_json(d))));
    let loops = section(&[Kind::Loop], &|d| Some(("backing_file", Value::from(d.b.loop_backing_file.clone()))));
    let other = section(&[Kind::Zram, Kind::Rom, Kind::Floppy], &|_| None);

    let mounts_json: Vec<Value> = mountinfo
        .iter()
        .filter(|m| mounts::is_layout_mount(m))
        .map(|m| {
            let dev = ctx.by_devno(&m.devno).map(|d| d.b.name.clone()).or_else(|| ctx.resolve(sys, &m.source));
            m.to_json(dev.as_deref(), sys.statvfs(&m.target))
        })
        .collect();
    let fstab: Value = match sys.read("/etc/fstab") {
        Some(t) => Value::Arr(mounts::parse_fstab(&t).iter().map(|e| e.to_json(ctx.resolve(sys, &e.spec).as_deref())).collect()),
        None => Value::Null,
    };
    let crypttab: Value = match sys.read("/etc/crypttab") {
        Some(t) => Value::Arr(mounts::parse_crypttab(&t).iter().map(|e| e.to_json(ctx.resolve(sys, &e.device).as_deref())).collect()),
        None => Value::Null,
    };
    let swaps: Vec<Value> = sys
        .read("/proc/swaps")
        .map(|t| mounts::parse_swaps(&t))
        .unwrap_or_default()
        .iter()
        .map(|s| {
            Obj::new()
                .set("file", s.file.as_str())
                .set("type", s.kind.as_str())
                .set("size_bytes", s.size_kib * 1024)
                .set("used_bytes", s.used_kib * 1024)
                .set("priority", s.priority)
                .set("device", if s.kind == "partition" { ctx.resolve(sys, &s.file) } else { None })
                .build()
        })
        .collect();
    let mdadm_conf = sys.read("/etc/mdadm/mdadm.conf").or_else(|| sys.read("/etc/mdadm.conf")).map(|t| mounts::parse_mdadm_conf(&t));

    let packages = crate::boot::installed_packages(sys);
    let partuuid_to_dev = |u: &str| ctx.resolve(sys, &format!("PARTUUID={u}"));
    let efi_boot = crate::efi::boot_json(sys, &partuuid_to_dev);
    let esps: Vec<(String, String)> = mountinfo
        .iter()
        .filter(|m| m.fstype == "vfat")
        .filter_map(|m| {
            let name = ctx.by_devno(&m.devno).map(|d| d.b.name.clone())?;
            let d = ctx.dev(&name)?;
            let is_esp_type = d.b.parent.as_ref().and_then(|p| ctx.dev(p)).and_then(|p| p.table.as_ref()).is_some_and(|t| {
                let n = d.b.partition_number.unwrap_or(0);
                match t {
                    Table::Gpt { gpt, .. } => gpt.parts.iter().any(|p| p.number == n && p.type_guid == parttable::ESP),
                    Table::Mbr(mb) => mb.parts.iter().any(|p| p.number == n && p.ptype == 0xef),
                }
            }) || d.b.udev.get("ID_PART_ENTRY_TYPE").is_some_and(|t| t == parttable::ESP || t == "0xef");
            (is_esp_type || matches!(m.target.as_str(), "/boot/efi" | "/efi")).then(|| (name, m.target.clone()))
        })
        .collect();
    let mbr_codes: Vec<(String, String)> =
        devs.iter().filter_map(|d| d.boot_code.as_ref().map(|bc| (d.b.name.clone(), bc.kind.to_string()))).collect();

    let storage = Obj::new()
        .set("topology", topology(&ctx, sys))
        .set("disks", disks)
        .set("md_arrays", md)
        .set("dm_devices", dm)
        .set("loop_devices", loops)
        .set("other_devices", other)
        .set("lvm", lvm_json(&ctx))
        .set("zfs", zfs_json(sys, &ctx))
        .set("btrfs", btrfs_json(sys, &ctx))
        .set("xfs_external_logs", xfs_external_logs(sys, &ctx))
        .set("mounts", mounts_json)
        .set("swaps", swaps)
        .set("fstab", fstab)
        .set("crypttab", crypttab)
        .set("mdadm_conf", mdadm_conf);
    let boot = crate::boot::boot_json(sys, &packages, &esps, efi_boot, mbr_codes);

    let mut out = Obj::new()
        .set("format", FORMAT)
        .set("schema", SCHEMA)
        .set("tool", Obj::new().set("name", "bko-holt").set("version", env!("CARGO_PKG_VERSION")))
        .set("generated", generated)
        .set("privileged", sys.privileged)
        .set("host", host_json(sys))
        .set("os", crate::osrel::to_json(sys, os))
        .set("firmware", crate::efi::firmware_json(sys))
        .set("storage", storage)
        .set("boot", boot);
    out.put("warnings", sys.warnings());
    out.build()
}

#[cfg(test)]
mod tests {

    #[test]
    fn zpool_status_text() {
        let t = "  pool: rpool\n state: ONLINE\nconfig:\n\n\tNAME                     STATE     READ WRITE CKSUM\n\trpool                    ONLINE       0     0     0\n\t  mirror-0               ONLINE       0     0     0\n\t    /dev/vda4            ONLINE       0     0     0\n\t    /dev/vdb4            ONLINE       0     0     0\n\tlogs\t\n\t  /dev/vdc1              ONLINE       0     0     0\n\ncache\n\nerrors: No known data errors\n";
        let v = parse_zpool_status_text(t, "rpool").unwrap();
        let m = v.at(&["vdevs", "rpool", "vdevs", "mirror-0"]).unwrap();
        assert_eq!(m.get("vdev_type").and_then(Value::as_str), Some("mirror"));
        assert_eq!(m.at(&["vdevs", "/dev/vdb4", "path"]).and_then(Value::as_str), Some("/dev/vdb4"));
        assert_eq!(v.at(&["logs", "/dev/vdc1", "vdev_type"]).and_then(Value::as_str), Some("disk"));
        assert!(v.get("l2cache").is_some_and(Value::is_null));
        let single = "config:\n\n\tNAME        STATE     READ WRITE CKSUM\n\trpool       ONLINE       0     0     0\n\t  /dev/vda3  ONLINE       0     0     0\n";
        let v = parse_zpool_status_text(single, "rpool").unwrap();
        assert_eq!(v.at(&["vdevs", "rpool", "vdevs", "/dev/vda3", "path"]).and_then(Value::as_str), Some("/dev/vda3"));
        assert!(parse_zpool_status_text("no config here", "rpool").is_none());
    }

    use super::*;

    #[test]
    fn human_sizes() {
        assert_eq!(human(0), "0 B");
        assert_eq!(human(1023), "1023 B");
        assert_eq!(human(1024), "1 KiB");
        assert_eq!(human(1536), "1.5 KiB");
        assert_eq!(human(16 << 30), "16 GiB");
        assert_eq!(human(2_000_398_934_016), "1.8 TiB");
        assert_eq!(human(500 << 20), "500 MiB");
        assert_eq!(human(u64::MAX), "16 EiB");
    }

    #[test]
    fn timestamps() {
        assert_eq!(rfc3339(0), "1970-01-01T00:00:00Z");
        assert_eq!(rfc3339(951_782_400), "2000-02-29T00:00:00Z");
        assert_eq!(rfc3339(1_793_828_645), "2026-11-04T21:44:05Z");
        assert_eq!(rfc3339(4_107_542_399), "2100-02-28T23:59:59Z");
    }

    #[test]
    fn udev_escapes() {
        assert_eq!(udev_decode("EFI\\x20System\\x20Partition"), "EFI System Partition");
        assert_eq!(udev_decode("a\\x2fb"), "a/b");
        assert_eq!(udev_decode("bad\\xzz"), "bad\\xzz");
        assert_eq!(udev_decode("end\\x2"), "end\\x2");
    }

    #[test]
    fn btrfs_subvol_lines() {
        let v = parse_btrfs_subvol_line("ID 256 gen 1234 parent 5 top level 5 parent_uuid - uuid 6c8a1b2c-0000-4000-8000-000000000001 path @rootfs").unwrap();
        assert_eq!(v.get("id").and_then(Value::as_u64), Some(256));
        assert_eq!(v.get("gen").and_then(Value::as_u64), Some(1234));
        assert_eq!(v.get("top_level").and_then(Value::as_u64), Some(5));
        assert!(v.get("parent_uuid").unwrap().is_null());
        assert_eq!(v.get("path").and_then(Value::as_str), Some("@rootfs"));
        let v = parse_btrfs_subvol_line("ID 5 (FS_TREE)").unwrap();
        assert_eq!(v.get("id").and_then(Value::as_u64), Some(5));
        let v = parse_btrfs_subvol_line("ID 300 gen 9 top level 256 path <FS_TREE>/@/with space").unwrap();
        assert_eq!(v.get("path").and_then(Value::as_str), Some("<FS_TREE>/@/with space"));
        assert!(parse_btrfs_subvol_line("garbage").is_none());
    }

    #[test]
    fn zfs_get_lines() {
        let v = parse_zfs_get("rpool\tcompression\tlz4\tlocal\nrpool/home\tcom.sun:auto-snapshot\ttrue\treceived\nshort\tline\nrpool\tdesc\ta\tb c\tlocal\n");
        assert_eq!(v.len(), 3);
        assert_eq!(v[0], ("rpool".into(), "compression".into(), "lz4".into(), "local".into()));
        assert_eq!(v[1].1, "com.sun:auto-snapshot");
        assert_eq!(v[1].3, "received");
        // A tab in the value isn't possible; one in the source is kept whole.
        assert_eq!(v[2].3, "b c\tlocal");
    }

    #[test]
    fn btrfs_qgroup_table() {
        // btrfs-progs 6.x, with the path column.
        let out = "Qgroupid    Referenced    Exclusive Max referenced Max exclusive   Parent     Path \n--------    ----------    --------- -------------- -------------   ------     ---- \n0/5              16384        16384           none          none   -          <toplevel>\n0/256        1073741824     4096      2147483648          none   1/100      @home\n1/100        1073741824     4096            none          none   -          <0 member qgroups>\n";
        let q = parse_btrfs_qgroups(out);
        assert_eq!(q.len(), 3);
        assert_eq!(q[0].get("qgroupid").and_then(Value::as_str), Some("0/5"));
        assert!(q[0].get("max_referenced").unwrap().is_null());
        assert_eq!(q[1].get("max_referenced").and_then(Value::as_u64), Some(2147483648));
        assert_eq!(q[1].get("parents"), Some(&Value::from(vec!["1/100"])));
        assert_eq!(q[1].get("path").and_then(Value::as_str), Some("@home"));
        assert_eq!(q[2].get("path").and_then(Value::as_str), Some("<0 member qgroups>"));
        // Older btrfs-progs: no path column.
        let q = parse_btrfs_qgroups("qgroupid rfer excl max_rfer max_excl parent\n-------- ---- ---- -------- -------- ------\n0/5 16384 16384 none none ---\n");
        assert_eq!(q.len(), 1);
        assert!(q[0].get("path").unwrap().is_null());
        assert_eq!(q[0].get("parents"), Some(&Value::Arr(vec![])));
        assert!(parse_btrfs_qgroups("ERROR: can't list qgroups: quotas not enabled\n").is_empty());
    }

    #[test]
    fn btrfs_paths_through_mounts() {
        let m = |target: &str, subvol: &str| Mount {
            id: 1,
            parent: 0,
            devno: "0:30".into(),
            root: subvol.into(),
            target: target.into(),
            options: "rw".into(),
            fstype: "btrfs".into(),
            source: "/dev/vda2".into(),
            super_options: format!("rw,subvolid=256,subvol={subvol}"),
        };
        let (root, home, top) = (m("/", "/@"), m("/home", "/@home"), m("/mnt/top", "/"));
        let only_root = [&root, &home];
        assert_eq!(btrfs_reachable_path(&only_root, "@").as_deref(), Some("/"));
        assert_eq!(btrfs_reachable_path(&only_root, "<FS_TREE>/@home").as_deref(), Some("/home"));
        assert_eq!(btrfs_reachable_path(&only_root, "@/var/lib/machines").as_deref(), Some("/var/lib/machines"));
        assert_eq!(btrfs_reachable_path(&only_root, "@home/user/snap").as_deref(), Some("/home/user/snap"));
        assert_eq!(btrfs_reachable_path(&only_root, "@snapshots"), None);
        // "@home" isn't under "@" just because it starts with it.
        assert_eq!(btrfs_reachable_path(&[&root], "@home"), None);
        assert_eq!(btrfs_reachable_path(&[&top], "@snapshots/1").as_deref(), Some("/mnt/top/@snapshots/1"));
    }

    #[test]
    fn dm_classification() {
        let mk = |name: &str, uuid: &str| {
            let t = crate::testutil::Tree::new("dmcls");
            t.disk("dm-0", "253:0", &[0u8; 1024], 512).file("/sys/block/dm-0/dm/name", name).file("/sys/block/dm-0/dm/uuid", uuid);
            let s = Sys::new(t.root(), Box::new(crate::sys::FakeRunner::default()), false);
            let mut b = crate::blockdev::scan(&s);
            dm_json(&Dev { b: b.remove(0), table: None, contents: vec![], boot_code: None, partition_boot_code: None, first_partition_lba: None, read_ok: false })
        };
        let v = mk("vg0-root", &format!("LVM-{}{}", "a".repeat(32), "b".repeat(32)));
        assert_eq!(v.get("target").and_then(Value::as_str), Some("lvm"));
        assert_eq!(v.get("vg").and_then(Value::as_str), Some("vg0"));
        assert_eq!(v.get("lv").and_then(Value::as_str), Some("root"));
        assert_eq!(v.get("vg_uuid").and_then(Value::as_str), Some("aaaaaa-aaaa-aaaa-aaaa-aaaa-aaaa-aaaaaa"));
        assert!(v.get("lv_layer").is_none());
        let v = mk("vg0-pool_tdata", &format!("LVM-{}{}-tdata", "a".repeat(32), "b".repeat(32)));
        assert_eq!(v.get("lv_layer").and_then(Value::as_str), Some("tdata"));
        let v = mk("vda5_crypt", "CRYPT-LUKS2-0b5f04ede7314ac6933e83ff25657722-vda5_crypt");
        assert_eq!(v.get("target").and_then(Value::as_str), Some("crypt"));
        assert_eq!(v.get("crypt_type").and_then(Value::as_str), Some("LUKS2"));
        assert_eq!(v.get("luks_uuid").and_then(Value::as_str), Some("0b5f04ed-e731-4ac6-933e-83ff25657722"));
        assert_eq!(v.get("mapper_path").and_then(Value::as_str), Some("/dev/mapper/vda5_crypt"));
        let v = mk("swap", "CRYPT-PLAIN-swap");
        assert_eq!(v.get("crypt_type").and_then(Value::as_str), Some("PLAIN"));
        assert!(v.get("luks_uuid").is_none());
        assert_eq!(mk("mpatha", "mpath-3600").get("target").and_then(Value::as_str), Some("multipath"));
        assert_eq!(mk("mpatha1", "part1-mpath-3600").get("target").and_then(Value::as_str), Some("partition"));
        assert_eq!(mk("x", "").get("target").and_then(Value::as_str), Some("unknown"));
        assert_eq!(mk("x", "SOMETHING").get("target").and_then(Value::as_str), Some("other"));
    }
}
