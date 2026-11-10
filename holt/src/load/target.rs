//! The machine `load` runs on: its disks, what's on them and what's in use,
//! and which of them each source disk goes to (layload.md, "Safety" and
//! "Mapping disks").

use super::layout::{Disk, Layout};
use super::Options;
use crate::blockdev::{self, BlockDev, Kind};
use crate::mounts::{self, Mount};
use crate::parttable::{self, Table};
use crate::sys::Sys;

/// Where a live system mounts the medium it booted from.
const MEDIUM_MOUNTS: [&str; 4] = ["/cdrom", "/run/live/medium", "/lib/live/mount/medium", "/run/initramfs/live"];
/// The rescue stick's config partition (stick-config.md).
const STICK_CONFIG_LABEL: &str = "BACKOTTER";

pub struct TargetDisk {
    pub b: BlockDev,
    pub parts: Vec<BlockDev>,
    pub table: Option<Table>,
    /// Signatures found on the disk or its partitions (filesystems,
    /// containers, pool labels), as "vdb1: ext4 UUID=...".
    pub signatures: Vec<String>,
    /// Filesystem and container UUIDs on the disk or its partitions.
    pub uuids: Vec<String>,
    /// Why the disk is busy: mounts, swap, holders.
    pub in_use: Vec<String>,
    pub boot_medium: bool,
    pub holds_root: bool,
}

/// A partition table's identifier as the dump writes it: the disk GUID, or
/// the MBR signature as "0x1234abcd".
pub fn table_id(t: &Table) -> String {
    match t {
        Table::Gpt { gpt, .. } => gpt.disk_guid.to_ascii_lowercase(),
        Table::Mbr(m) => format!("0x{:08x}", m.disk_signature),
    }
}

impl TargetDisk {
    /// Whether an earlier stage of this layout wrote what's here: the
    /// source's partition table identifier, or only the source's own
    /// filesystem and container UUIDs.
    pub fn is_ours(&self, src: &Disk, layout_uuids: &[String]) -> bool {
        // An MBR's signature of 0 is no identity: a FAT boot sector has one.
        let table = match (&self.table, src.table.as_ref().and_then(|t| t.id.as_ref()).filter(|id| id.as_str() != "0x00000000")) {
            (Some(t), Some(id)) => table_id(t).eq_ignore_ascii_case(id),
            _ => false,
        };
        table || (!self.uuids.is_empty() && self.uuids.iter().all(|u| layout_uuids.contains(u)))
    }

    pub fn path(&self) -> String {
        self.b.dev_path()
    }

    pub fn blank(&self) -> bool {
        self.table.is_none() && self.signatures.is_empty() && self.in_use.is_empty() && self.parts.is_empty()
    }

    pub fn describe(&self) -> String {
        let mut s = format!("{} ({}", self.path(), crate::report::human(self.b.size_bytes));
        if let Some(m) = &self.b.model {
            s += &format!(", {m}");
        }
        if let Some(n) = &self.b.serial {
            s += &format!(", serial {n}");
        }
        s + ")"
    }
}

pub struct System {
    pub disks: Vec<TargetDisk>,
    pub mounts: Vec<Mount>,
    pub pools: Vec<ImportedPool>,
    /// The LVM physical volumes this system's block devices hold.
    pub pvs: Vec<HostPv>,
}

/// An LVM physical volume on the system load runs on: its device's kernel
/// name, its own UUID, its volume group's name and UUID, and the whole disks
/// it sits on (through partitions, md arrays and device-mapper).
#[derive(Debug, Clone, PartialEq)]
pub struct HostPv {
    pub dev: String,
    pub uuid: String,
    pub vg: String,
    pub vg_uuid: String,
    pub disks: Vec<String>,
}

/// The PVs among ALL, each with the whole disks it sits on.
fn host_pvs(sys: &Sys, all: &[BlockDev]) -> Vec<HostPv> {
    fn base(all: &[BlockDev], name: &str, depth: usize, out: &mut Vec<String>) {
        let Some(b) = all.iter().find(|b| b.name == name) else { return };
        if let Some(p) = &b.parent {
            base(all, p, depth + 1, out);
        } else if b.slaves.is_empty() || depth > 16 {
            if !out.contains(&b.name) {
                out.push(b.name.clone());
            }
        } else {
            for s in &b.slaves {
                base(all, s, depth + 1, out);
            }
        }
    }
    let mut out = Vec::new();
    // Not optical, floppy, zram or RAM disks: no PV there, and opening an
    // empty drive is slow and warns.
    for b in all.iter().filter(|b| !matches!(b.kind, Kind::Rom | Kind::Floppy | Kind::Zram | Kind::Ram)) {
        let Some(src) = sys.open_dev(&b.dev_path()) else { continue };
        for c in crate::probe::probe_all(&src).into_iter().filter(|c| c.kind == "LVM2_member") {
            let vg = |k: &str| c.extra.as_ref().and_then(|v| v.get(k)).and_then(crate::json::Value::as_str).map(str::to_string);
            if let Some(name) = vg("name") {
                let mut disks = Vec::new();
                base(all, &b.name, 0, &mut disks);
                out.push(HostPv { dev: b.name.clone(), uuid: c.uuid.clone().unwrap_or_default(), vg: name, vg_uuid: vg("id").unwrap_or_default(), disks });
            }
        }
    }
    out
}

/// A ZFS pool imported on the system load runs on.
#[derive(Debug, Clone, PartialEq)]
pub struct ImportedPool {
    pub name: String,
    /// "-" when none: the pool is mounted where its datasets say.
    pub altroot: String,
    /// Kernel names of its vdevs' devices.
    pub devices: Vec<String>,
}

impl ImportedPool {
    /// A pool assemble made: one of the layout's, imported under --target.
    pub fn is_ours(&self, layout_pools: &[String], target: &str) -> bool {
        layout_pools.contains(&self.name) && self.altroot == target
    }
}

/// The device paths in `zpool status -P` output, per pool.
pub fn parse_zpool_status(text: &str) -> Vec<(String, Vec<String>)> {
    let mut out: Vec<(String, Vec<String>)> = Vec::new();
    for l in text.lines() {
        let t = l.trim();
        if let Some(n) = t.strip_prefix("pool:") {
            out.push((n.trim().to_string(), vec![]));
        } else if let (Some(first), Some(cur)) = (t.split_whitespace().next(), out.last_mut()) {
            // illumos prints a vdev's device bare (c3t0d0s1) even with -P.
            let bare = first.len() > 3 && first.starts_with('c') && first[1..].starts_with(|c: char| c.is_ascii_digit()) && first.contains('d') && !first.contains('-');
            if first.starts_with("/dev/") || bare {
                cur.1.push(first.to_string());
            }
        }
    }
    out
}

pub fn imported_pools(sys: &Sys) -> Vec<ImportedPool> {
    let roots: Vec<(String, String)> = sys
        .run("zpool", &["list", "-H", "-o", "name,altroot"])
        .map(|o| o.lines().filter_map(|l| l.split_once('\t')).map(|(a, b)| (a.to_string(), b.trim().to_string())).collect())
        .unwrap_or_default();
    let status = sys.run("zpool", &["status", "-P"]).unwrap_or_default();
    parse_zpool_status(&status)
        .into_iter()
        .map(|(name, paths)| ImportedPool {
            altroot: roots.iter().find(|(n, _)| *n == name).map(|(_, a)| a.clone()).unwrap_or_else(|| "-".into()),
            devices: paths.iter().map(|p| resolve_name(sys, p)).collect(),
            name,
        })
        .collect()
}

/// The names of the pools a layout makes.
pub fn layout_pools(layout: &Layout) -> Vec<String> {
    let names = layout_pool_names(layout);
    names.iter().cloned().chain(names.iter().map(|n| temp_pool_name(n))).collect()
}

fn layout_pool_names(layout: &Layout) -> Vec<String> {
    layout
        .v
        .at(&["storage", "zfs", "tools", "imported_pools"])
        .and_then(crate::json::Value::as_array)
        .into_iter()
        .flatten()
        .filter_map(|p| p.get("name").and_then(crate::json::Value::as_str).map(str::to_string))
        .collect()
}

fn signatures_of(sys: &Sys, d: &BlockDev, out: &mut Vec<String>, uuids: &mut Vec<String>) {
    if let Some(src) = sys.open_dev(&d.dev_path()) {
        for c in crate::probe::probe_all(&src) {
            let mut s = format!("{}: {}", d.name, c.kind);
            if let Some(u) = &c.uuid {
                s += &format!(" UUID={u}");
                uuids.push(u.to_ascii_lowercase());
            }
            if let Some(l) = &c.label {
                s += &format!(" LABEL={l}");
            }
            out.push(s);
        }
    } else if let Some(t) = d.udev.get("ID_FS_TYPE") {
        out.push(format!("{}: {t} (from udev)", d.name));
        if let Some(u) = d.udev.get("ID_FS_UUID") {
            uuids.push(u.to_ascii_lowercase());
        }
    }
}

/// The kernel name a /dev path (or a by-id/by-path link) stands for.
pub fn resolve_name(sys: &Sys, spec: &str) -> String {
    if !spec.starts_with('/') {
        return spec.to_string();
    }
    // illumos' /dev/dsk names are links into /devices: the name is the one.
    if let Some(n) = spec.strip_prefix("/dev/dsk/").or_else(|| spec.strip_prefix("/dev/rdsk/")) {
        return n.to_string();
    }
    std::fs::canonicalize(sys.p(spec))
        .ok()
        .and_then(|p| p.file_name().map(|n| n.to_string_lossy().into_owned()))
        .unwrap_or_else(|| spec.trim_start_matches("/dev/").to_string())
}

/// Reads every whole disk on the system `load` runs on.  `ours` are the
/// pools the layout makes, which don't make their disks busy once imported
/// under --target.
pub fn scan(sys: &Sys, opts: &Options, ours: &[String]) -> System {
    if let Some(fam) = crate::other::Family::current() {
        return crate::other::load::scan(sys, fam, opts, ours);
    }
    let all = blockdev::scan(sys);
    let mounts = sys.read("/proc/self/mountinfo").map(|t| mounts::parse_mountinfo(&t)).unwrap_or_default();
    let swaps: Vec<String> =
        sys.read("/proc/swaps").map(|t| mounts::parse_swaps(&t)).unwrap_or_default().into_iter().map(|s| resolve_name(sys, &s.file)).collect();
    let pools = imported_pools(sys);
    let mut disks = Vec::new();
    for b in all.iter().filter(|b| matches!(b.kind, Kind::Disk | Kind::Nbd)) {
        let parts: Vec<BlockDev> = all.iter().filter(|p| p.parent.as_deref() == Some(b.name.as_str())).cloned().collect();
        let table = sys.open_dev(&b.dev_path()).and_then(|src| parttable::read_table(&src, b.logical_block_size.max(512)));
        let mut signatures = Vec::new();
        let mut uuids = Vec::new();
        if table.is_none() {
            signatures_of(sys, b, &mut signatures, &mut uuids);
        }
        for p in &parts {
            signatures_of(sys, p, &mut signatures, &mut uuids);
        }
        let mut in_use = Vec::new();
        let mut boot_medium = false;
        let mut holds_root = false;
        for d in std::iter::once(b).chain(parts.iter()) {
            for m in mounts.iter().filter(|m| m.devno == d.devno || resolve_name(sys, &m.source) == d.name) {
                if m.target == "/" {
                    holds_root = true;
                }
                if MEDIUM_MOUNTS.contains(&m.target.as_str()) {
                    boot_medium = true;
                }
                // Mounts under the target tree are load's own (mount stage).
                if !(m.target == opts.target || m.target.starts_with(&format!("{}/", opts.target))) {
                    in_use.push(format!("{} is mounted at {}", d.name, m.target));
                }
            }
            if swaps.contains(&d.name) {
                in_use.push(format!("{} is in use as swap", d.name));
            }
            if !d.holders.is_empty() {
                in_use.push(format!("{} is held by {}", d.name, d.holders.join(", ")));
            }
            // ZFS takes no holders, and its mounts name datasets, not devices.
            for p in pools.iter().filter(|p| p.devices.contains(&d.name)) {
                if !p.is_ours(ours, &opts.target) {
                    in_use.push(format!("{} is a vdev of the imported pool {}", d.name, p.name));
                }
                if mounts.iter().any(|m| m.target == "/" && m.fstype == "zfs" && m.source.split('/').next() == Some(p.name.as_str())) {
                    holds_root = true;
                }
            }
            if d.udev.get("ID_FS_LABEL") == Some(STICK_CONFIG_LABEL) {
                boot_medium = true;
            }
        }
        if signatures.iter().any(|s| s.contains(&format!("LABEL={STICK_CONFIG_LABEL}"))) {
            boot_medium = true;
        }
        disks.push(TargetDisk { b: b.clone(), parts, table, signatures, uuids, in_use, boot_medium, holds_root });
    }
    let pvs = host_pvs(sys, &all);
    System { disks, mounts, pools, pvs }
}

impl System {
    pub fn disk(&self, name: &str) -> Option<&TargetDisk> {
        self.disks.iter().find(|d| d.b.name == name)
    }
}

/// Which target disk each used source disk goes to, and what's wrong.
#[derive(Debug, Default)]
pub struct Mapping {
    /// (source disk name, target disk kernel name)
    pub pairs: Vec<(String, String)>,
    /// Source disks whose target is bigger (GPT's backup header moves).
    pub bigger: Vec<String>,
    pub problems: Vec<String>,
    pub notes: Vec<String>,
    /// (the layout's pool name, its in-core name here) where a pool of the
    /// system load runs on has the layout's name.
    pub pool_names: Vec<(String, String)>,
}

/// The in-core name a layout's pool takes when the name is taken here.
pub fn temp_pool_name(name: &str) -> String {
    format!("{name}_bko")
}

impl Mapping {
    pub fn target_of(&self, src_disk: &str) -> Option<&str> {
        self.pairs.iter().find(|(s, _)| s == src_disk).map(|(_, t)| t.as_str())
    }
}

fn same_identity(src: &Disk, t: &BlockDev) -> bool {
    match (&src.wwid, &t.wwid) {
        (Some(a), Some(b)) if !a.is_empty() => return a == b,
        _ => {}
    }
    matches!((&src.serial, &t.serial, &src.model, &t.model), (Some(a), Some(b), Some(m), Some(n)) if !a.is_empty() && a == b && m == n)
}

/// Maps source disks to target disks: by --map, else by identity
/// (WWID, or serial and model); never by size or order.  `ours` says
/// whether the layout's own identifiers on a target (from an earlier stage)
/// explain why it's busy or not blank.
pub fn map(layout: &Layout, opts: &Options, system: &System, sys: &Sys) -> Mapping {
    let mut m = Mapping::default();
    for (src, _) in &opts.maps {
        if layout.disk(src).is_none() {
            m.problems.push(format!("--map {src}=...: the layout has no disk {src} (it has {})", names(layout)));
        }
    }
    let ours = layout_uuids(layout);
    for d in layout.disks.iter() {
        if !d.used {
            m.notes.push(format!("source disk {} holds nothing; it needs no target", d.describe()));
            continue;
        }
        let target = match opts.maps.iter().find(|(s, _)| *s == d.name) {
            Some((_, spec)) => {
                let name = resolve_name(sys, spec);
                match system.disk(&name) {
                    Some(t) => Some(t),
                    None => {
                        m.problems.push(format!("--map {}={spec}: no whole disk {name} here", d.name));
                        continue;
                    }
                }
            }
            None => system.disks.iter().find(|t| same_identity(d, &t.b)),
        };
        let Some(t) = target else {
            m.problems.push(format!(
                "source disk {} has no target: give --map {}=DISK (disks here: {})",
                d.describe(),
                d.name,
                system.disks.iter().map(TargetDisk::describe).collect::<Vec<_>>().join("; ")
            ));
            continue;
        };
        if let Some((other, _)) = m.pairs.iter().find(|(_, tn)| *tn == t.b.name) {
            m.problems.push(format!("{} is the target of both {other} and {}", t.path(), d.name));
            continue;
        }
        check_target(d, t, opts, &ours, &mut m);
        m.pairs.push((d.name.clone(), t.b.name.clone()));
    }
    // A pool of the layout's name imported here, but not from the targets
    // (the rescue system's own): the layout's goes by another name here.
    let targets: Vec<String> = m.pairs.iter().map(|(_, t)| t.clone()).collect();
    // A volume group of the layout's name, or with its UUID or one of its
    // PVs' UUIDs, on a disk that isn't a target (the source's own disk,
    // still attached, most often).  load's own LVM commands see the targets
    // only (--devices), but the restored system's tools in the boot stage
    // (its initramfs's, GRUB's, which finds LVs by UUID) see every disk, and
    // so would its first boot: two groups of one name, or duplicate PVs.
    // LVM has no in-core name to give the layout's, as ZFS has.
    let s = |v: &crate::json::Value, k: &str| v.get(k).and_then(crate::json::Value::as_str).unwrap_or_default().to_string();
    for vg in layout.v.at(&["storage", "lvm", "volume_groups"]).and_then(crate::json::Value::as_array).into_iter().flatten() {
        let (n, id) = (s(vg, "name"), s(vg, "id"));
        let pv_ids: Vec<String> = vg.get("physical_volumes").and_then(crate::json::Value::as_array).into_iter().flatten().map(|p| s(p, "id")).filter(|u| !u.is_empty()).collect();
        for pv in system.pvs.iter().filter(|pv| !pv.disks.iter().all(|d| targets.contains(d))) {
            let why = if !n.is_empty() && pv.vg == n {
                format!("a volume group named {n}")
            } else if !id.is_empty() && pv.vg_uuid == id {
                format!("volume group {n}'s UUID (as {})", pv.vg)
            } else if pv_ids.contains(&pv.uuid) {
                format!("the UUID of one of {n}'s PVs (in {})", pv.vg)
            } else {
                continue;
            };
            m.problems.push(format!(
                "{why} is on {} ({}), which isn't a target: the restored system's boot tools and its first boot would find it twice; disconnect that disk, or give its group a new name and UUIDs (vgimportclone) first",
                pv.dev,
                pv.disks.join(", ")
            ));
        }
    }
    for n in layout.v.at(&["storage", "zfs", "tools", "imported_pools"]).and_then(crate::json::Value::as_array).into_iter().flatten().filter_map(|p| p.get("name").and_then(crate::json::Value::as_str)) {
        if let Some(ip) = system.pools.iter().find(|ip| ip.name == n) {
            if !ip.devices.iter().all(|d| targets.iter().any(|t| d.starts_with(t.as_str()))) {
                m.pool_names.push((n.to_string(), temp_pool_name(n)));
                m.notes.push(format!("ZFS pool {n}: a pool of that name is imported here, so it's {} here until close exports it", temp_pool_name(n)));
            }
        }
    }
    for e in &opts.erase {
        let name = resolve_name(sys, e);
        if !m.pairs.iter().any(|(_, t)| *t == name) {
            m.problems.push(format!("--erase {e}: that isn't a target of this layout, so it isn't erased"));
        }
    }
    m
}

fn names(layout: &Layout) -> String {
    layout.disks.iter().map(|d| d.name.as_str()).collect::<Vec<_>>().join(", ")
}

/// Every identifier the layout gives a filesystem, container or member.
pub fn layout_uuids(layout: &Layout) -> Vec<String> {
    let mut out = Vec::new();
    let mut add = |c: &crate::json::Value| {
        if let Some(u) = c.get("uuid").and_then(crate::json::Value::as_str) {
            out.push(u.to_ascii_lowercase());
        }
    };
    for d in &layout.disks {
        add(&d.content);
        for p in &d.parts {
            add(&p.content);
        }
    }
    out
}

fn check_target(src: &Disk, t: &TargetDisk, opts: &Options, ours: &[String], m: &mut Mapping) {
    let who = format!("{} -> {}", src.name, t.path());
    if t.boot_medium {
        m.problems.push(format!("{who}: {} is the disk this system booted from; never a target", t.path()));
        return;
    }
    if t.holds_root {
        m.problems.push(format!("{who}: {} holds the running system's /; never a target", t.path()));
        return;
    }
    if t.b.size_bytes < src.size_bytes {
        m.problems.push(format!(
            "{who}: the target is smaller ({} < {}); version 1 needs the same size or bigger",
            crate::report::human(t.b.size_bytes),
            crate::report::human(src.size_bytes)
        ));
    }
    if t.b.logical_block_size.max(512) != src.sector_size {
        m.problems.push(format!("{who}: logical sector size {} here, {} on the source; version 1 needs them equal", t.b.logical_block_size, src.sector_size));
    }
    if t.b.read_only == Some(true) {
        m.problems.push(format!("{who}: the target is read-only"));
    }
    // Busy or written on by an earlier stage of this same layout is fine:
    // that's what status and the later stages expect to find.
    let is_ours = t.is_ours(src, ours);
    if !t.in_use.is_empty() && !is_ours {
        m.problems.push(format!("{who}: the target is in use ({}); refused even with --erase", t.in_use.join("; ")));
        return;
    }
    let erase = opts.erase.iter().any(|e| e == &t.path() || e == &t.b.name || resolve_tail(e) == t.b.name);
    if !t.blank() && !is_ours && !erase {
        let what = if t.signatures.is_empty() { "a partition table".to_string() } else { t.signatures.join("; ") };
        m.problems.push(format!("{who}: the target isn't blank ({what}); give --erase {} to wipe it", t.path()));
    }
    if t.b.size_bytes > src.size_bytes {
        m.notes.push(format!("{who}: the target is bigger; the extra {} stays unused", crate::report::human(t.b.size_bytes - src.size_bytes)));
        m.bigger.push(src.name.clone());
    }
}

fn resolve_tail(spec: &str) -> &str {
    spec.rsplit('/').next().unwrap_or(spec)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::load::layout::{sample, Layout};
    use crate::sys::FakeRunner;
    use crate::testutil::Tree;

    fn layout() -> Layout {
        Layout::from_json(crate::json::parse(&sample::bios_mbr()).unwrap()).unwrap()
    }

    fn opts(a: &[&str]) -> Options {
        let mut v = vec!["l.json".to_string()];
        v.extend(a.iter().map(|s| s.to_string()));
        match super::super::parse_args(&v).unwrap() {
            super::super::Parsed::Run(o) => o,
            _ => unreachable!(),
        }
    }

    /// A rescue system: sda the stick (config partition), vdb blank and
    /// 16 GiB, vdc 8 GiB.
    fn tree() -> Tree {
        let t = Tree::new("load-target");
        t.disk("vdb", "253:16", &[], 512).file("/sys/block/vdb/size", format!("{}\n", 33554432));
        t.disk("vdc", "253:32", &[], 512).file("/sys/block/vdc/size", format!("{}\n", 16777216));
        t.disk("sda", "8:0", &[], 512).file("/sys/block/sda/size", "30000000\n");
        t.partition("sda", "sda3", "8:3", 3, 2048, 65536, &[]);
        t.file("/run/udev/data/b8:3", "E:ID_FS_TYPE=vfat\nE:ID_FS_LABEL=BACKOTTER\n");
        t.file("/proc/self/mountinfo", "");
        t.file("/proc/swaps", "Filename\tType\tSize\tUsed\tPriority\n");
        t.symlink("/dev/disk/by-id/virtio-tgt", "../../vdb");
        t
    }

    fn sys(t: &Tree) -> Sys {
        Sys::new(t.root(), Box::new(FakeRunner::default()), false)
    }

    #[test]
    fn maps_by_flag_and_checks() {
        let t = tree();
        let s = sys(&t);
        let o = opts(&["--map", "vda=/dev/disk/by-id/virtio-tgt"]);
        let system = scan(&s, &o, &[]);
        assert!(system.disk("sda").unwrap().boot_medium);
        let m = map(&layout(), &o, &system, &s);
        assert_eq!(m.pairs, vec![("vda".to_string(), "vdb".to_string())]);
        assert!(m.problems.is_empty(), "{:?}", m.problems);
    }

    #[test]
    fn zpool_status_devices() {
        let text = "  pool: rpool\n state: ONLINE\nconfig:\n\n\tNAME         STATE     READ WRITE CKSUM\n\trpool        ONLINE       0     0     0\n\t  /dev/vda3  ONLINE       0     0     0\n\nerrors: No known data errors\n\n  pool: tank\nconfig:\n\ttank  ONLINE\n\t  mirror-0  ONLINE\n\t    /dev/disk/by-id/ata-A-part1  ONLINE\n\t    /dev/sdb1  ONLINE\n";
        assert_eq!(
            parse_zpool_status(text),
            vec![
                ("rpool".to_string(), vec!["/dev/vda3".to_string()]),
                ("tank".to_string(), vec!["/dev/disk/by-id/ata-A-part1".to_string(), "/dev/sdb1".to_string()])
            ]
        );
        let p = ImportedPool { name: "rpool".into(), altroot: "/mnt/bko-target".into(), devices: vec![] };
        assert!(p.is_ours(&["rpool".into()], "/mnt/bko-target"));
        assert!(!p.is_ours(&["rpool".into()], "/mnt/elsewhere"));
        assert!(!ImportedPool { altroot: "-".into(), ..p }.is_ours(&["rpool".into()], "/mnt/bko-target"));
    }

    #[test]
    fn refusals() {
        let t = tree();
        let s = sys(&t);
        let p = |a: &[&str]| {
            let o = opts(a);
            map(&layout(), &o, &scan(&s, &o, &[]), &s).problems.join("\n")
        };
        assert!(p(&[]).contains("has no target"));
        assert!(p(&["--map", "vda=vdc"]).contains("smaller"));
        assert!(p(&["--map", "vda=sda", "--erase", "sda"]).contains("booted from"));
        assert!(p(&["--map", "vdz=vdb"]).contains("no disk vdz"));
        assert!(p(&["--map", "vda=vdq"]).contains("no whole disk vdq"));
        assert!(p(&["--map", "vda=vdb", "--erase", "vdc"]).contains("isn't a target"));
    }

    #[test]
    fn a_same_named_volume_group_elsewhere_is_refused() {
        let t = tree();
        // vdd: a whole-disk PV of the volume group vg0.
        t.disk("vdd", "253:48", &crate::probe::lvm::sample::pv(512, 1 << 20), 512);
        // Privileged: scan reads the devices.
        let s = Sys::new(t.root(), Box::new(FakeRunner::default()), true);
        let with = |vg: &str| {
            let j = sample::bios_mbr().replace(r#""lvm": {"volume_groups": []}"#, &format!(r#""lvm": {{"volume_groups": [{{"name": "{vg}"}}]}}"#));
            assert_ne!(j, sample::bios_mbr());
            Layout::from_json(crate::json::parse(&j).unwrap()).unwrap()
        };
        let o = opts(&["--map", "vda=vdb"]);
        let system = scan(&s, &o, &[]);
        assert_eq!(
            system.pvs,
            vec![HostPv {
                dev: "vdd".into(),
                uuid: "Hc7n2m-aaaa-bbbb-cccc-dddd-eeee-ffffff".into(),
                vg: "vg0".into(),
                vg_uuid: "Yb3kFa-0Pz1-AbCd-EfGh-IjKl-MnOp-QrStUv".into(),
                disks: vec!["vdd".into()]
            }],
            "vdd: {:?}",
            system.disk("vdd").map(|d| &d.signatures)
        );
        let p = map(&with("vg0"), &o, &system, &s).problems.join("\n");
        assert!(p.contains("a volume group named vg0 is on vdd (vdd), which isn't a target"), "{p}");
        assert!(map(&with("ubuntu-vg"), &o, &system, &s).problems.is_empty());
        // Renamed, but with the same UUIDs (vgrename keeps them).
        let ids = |vg: &str, id: &str, pv: &str| {
            let j = sample::bios_mbr().replace(
                r#""lvm": {"volume_groups": []}"#,
                &format!(r#""lvm": {{"volume_groups": [{{"name": "{vg}", "id": "{id}", "physical_volumes": [{{"id": "{pv}"}}]}}]}}"#),
            );
            Layout::from_json(crate::json::parse(&j).unwrap()).unwrap()
        };
        let p = map(&ids("ubuntu-vg", "Yb3kFa-0Pz1-AbCd-EfGh-IjKl-MnOp-QrStUv", "x"), &o, &system, &s).problems.join("\n");
        assert!(p.contains("volume group ubuntu-vg's UUID (as vg0) is on vdd"), "{p}");
        let p = map(&ids("ubuntu-vg", "y", "Hc7n2m-aaaa-bbbb-cccc-dddd-eeee-ffffff"), &o, &system, &s).problems.join("\n");
        assert!(p.contains("the UUID of one of ubuntu-vg's PVs (in vg0) is on vdd"), "{p}");
        assert!(map(&ids("ubuntu-vg", "y", "z"), &o, &system, &s).problems.is_empty());
        // On a target, it's the old system's, which load replaces.
        let o = opts(&["--map", "vda=vdd", "--erase", "vdd"]);
        let p = map(&with("vg0"), &o, &scan(&s, &o, &[]), &s).problems.join("\n");
        assert!(!p.contains("volume group named"), "{p}");
    }
}
