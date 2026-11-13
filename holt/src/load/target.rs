//! The machine `load` runs on: its disks, what's on them and what's in use,
//! and which of them each source disk goes to (layload.md, "Safety" and
//! "Mapping disks").

use super::fit::{self, Fit};
use super::layout::{Disk, Layout};
use super::{Options, Stage, RUN_DIR};
use crate::blockdev::{self, BlockDev, Kind};
use crate::mounts::{self, Mount};
use crate::parttable::{self, Table};
use crate::sys::Sys;
use std::borrow::Cow;

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
    /// Why the disk is busy: mounts, swap, holders, pool vdevs.
    pub in_use: Vec<String>,
    /// The part of it --erase can't let go of: mounts and swap (holders and
    /// pools, release.rs works out).
    pub hard_use: Vec<String>,
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
    /// Every block device (Linux; empty elsewhere), for release.rs.
    pub devs: Vec<BlockDev>,
    /// The kernel names of the devices in use as swap.
    pub swaps: Vec<String>,
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
    let mut out = Vec::new();
    // Not optical, floppy, zram or RAM disks: no PV there, and opening an
    // empty drive is slow and warns.
    for b in all.iter().filter(|b| !matches!(b.kind, Kind::Rom | Kind::Floppy | Kind::Zram | Kind::Ram)) {
        let Some(src) = sys.open_dev(&b.dev_path()) else { continue };
        for c in crate::probe::probe_all(&src).into_iter().filter(|c| c.kind == "LVM2_member") {
            let vg = |k: &str| c.extra.as_ref().and_then(|v| v.get(k)).and_then(crate::json::Value::as_str).map(str::to_string);
            if let Some(name) = vg("name") {
                let disks = super::release::bases(all, &b.name);
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
        let mut hard_use = Vec::new();
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
                    hard_use.push(format!("{} is mounted at {}", d.name, m.target));
                }
            }
            if swaps.contains(&d.name) {
                hard_use.push(format!("{} is in use as swap", d.name));
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
        in_use.splice(0..0, hard_use.iter().cloned());
        disks.push(TargetDisk { b: b.clone(), parts, table, signatures, uuids, in_use, hard_use, boot_medium, holds_root });
    }
    let pvs = host_pvs(sys, &all);
    System { disks, mounts, pools, pvs, devs: all, swaps }
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
    /// Source disks whose table ends where the target does, not where the
    /// source's did, so a GPT's backup header and last usable LBA go there:
    /// a bigger target, or one --fit-largest sized the table to.
    pub bigger: Vec<String>,
    pub problems: Vec<String>,
    pub notes: Vec<String>,
    /// (the layout's pool name, its in-core name here) where a pool of the
    /// system load runs on has the layout's name.
    pub pool_names: Vec<(String, String)>,
    /// Targets named with --erase that are busy only with holders and
    /// imported pools: what holds them, let go of before the partition
    /// stage's wipefs (release.rs), top of each stack first.
    pub held: Vec<String>,
    pub release: Vec<super::release::Step>,
    /// Targets named with --erase that are busy and hold this layout's own
    /// identifiers (TargetDisk::is_ours): what's there may be an earlier
    /// stage's, or the old system's on its own disk, started at boot.
    /// release_holders tells them apart once the layout the stages build
    /// from is known.
    pub ours_busy: Vec<String>,
    /// (path, text) of each target's table record (table_record), which the
    /// partition stage writes once its commands have run: what lets a later
    /// run with --erase tell the table load wrote from the source's own
    /// disk as found.
    pub records: Vec<(String, String)>,
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
    let mut m = map_disks(layout, opts, system, sys);
    release_holders(layout, opts, system, sys, &mut m);
    m.records = records(layout, system, &m, sys);
    m
}

/// map without what holds the targets worked out: prepare does that once
/// --fit-largest has made the layout the stages build from.
fn map_disks(layout: &Layout, opts: &Options, system: &System, sys: &Sys) -> Mapping {
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

/// Whether T holds D's partition table as the partition stage writes it, by
/// status's check: the source's identifier (when it has one) and each
/// partition at its sectors.  A source disk with no table gives the stage
/// nothing to check.
pub fn partition_done(d: &Disk, t: &TargetDisk) -> bool {
    let Some(want) = &d.table else { return true };
    let Some(have) = &t.table else { return false };
    let ext = have.extents();
    want.id.as_ref().is_none_or(|id| table_id(have).eq_ignore_ascii_case(id)) && d.parts.iter().all(|p| ext.iter().any(|&(n, s, len)| n == p.number && s == p.start_lba && len == p.sectors))
}

/// Where the partition stage records what it did to target disk TGT (a
/// kernel name): the table it wrote, or, for a source disk with no table,
/// the wipe its content goes on.  RUN_DIR is the rescue system's tmpfs: a
/// reboot forgets the record along with the session's mounts, imports and
/// activations, so a record always speaks of what this session did.
pub fn table_record(tgt: &str) -> String {
    format!("{RUN_DIR}/partitioned/{tgt}")
}

/// What the record of D on T says: the layout (its host and dump time), the
/// source disk, the target, and the table and partitions written (with
/// --fit-largest, the fitted ones; none for a disk with no table).
///
/// The target is named by what the kernel says of it in sysfs, which holds
/// for as long as the disk stays attached: its name, its size, and the
/// serial and WWID its driver gives (device/serial, device/wwid), empty
/// when it gives none.  Not by udev's ID_SERIAL_SHORT or ID_WWN, which
/// BlockDev falls back to for virtio and NVMe disks: udev rewrites its
/// database on every change event, and a field that came or went between
/// two runs would make the record miss, so a --through rerun would wipe a
/// disk load had already restored.  A disk swapped in under the same name,
/// with the same size and kernel serial and the table load wrote, is the
/// one case the record can't tell; the table check makes it a clone of the
/// target load wrote, and a hot-swap mid-restore isn't one load supports.
pub fn table_record_text(layout: &Layout, d: &Disk, t: &TargetDisk, sys: &Sys) -> String {
    let attr = |a: &str| sys.read_trim(&format!("/sys/block/{}/device/{a}", t.b.name)).unwrap_or_default();
    let mut s = format!(
        "layout {} {}\nsource {}\ntarget {} size={} serial={} wwid={}\n",
        layout.hostname(),
        layout.str_at(&["generated"]).unwrap_or("-"),
        d.name,
        t.b.name,
        t.b.size_bytes,
        attr("serial"),
        attr("wwid")
    );
    match &d.table {
        Some(tb) => s += &format!("table {}\n", tb.id.as_deref().unwrap_or("-")),
        None => s += "no table: wiped\n",
    }
    for p in &d.parts {
        s += &format!("partition {} {} {}\n", p.number, p.start_lba, p.sectors);
    }
    s
}

/// The record of each target, for the partition stage to write.
fn records(layout: &Layout, system: &System, m: &Mapping, sys: &Sys) -> Vec<(String, String)> {
    m.pairs
        .iter()
        .filter_map(|(s, t)| Some((layout.disk(s)?, system.disk(t)?)))
        .map(|(d, t)| (table_record(&t.b.name), table_record_text(layout, d, t, sys)))
        .collect()
}

/// Whether this rescue session's partition stage wrote D's table on T (or,
/// with no table, wiped T for D's content).
pub fn load_wrote(layout: &Layout, d: &Disk, t: &TargetDisk, sys: &Sys) -> bool {
    sys.read(&table_record(&t.b.name)).is_some_and(|r| r == table_record_text(layout, d, t, sys))
}

/// Whether the partition stage counts as done on T: partition_done, and,
/// for a target named with --erase, a record that load wrote that table in
/// this session.  The source's own disk as found carries the source's table
/// too (same disk, same table: the usual bare-metal restore), and what the
/// old system has on it (an active volume group, its filesystems, its
/// /etc) isn't load's; --erase asked for it wiped (layload.md, "Safety",
/// rule 4).  A source disk with no table has nothing for partition_done to
/// check, but the stage is the one that wipes and zeroes its target: named
/// with --erase, it's done only by the record too, or the old filesystem
/// there (the source's own, its UUID the layout's) would pass for format's.
pub fn partition_counts_done(layout: &Layout, d: &Disk, t: &TargetDisk, opts: &Options, sys: &Sys) -> bool {
    partition_done(d, t) && (!is_erased(opts, t) || load_wrote(layout, d, t, sys))
}

/// A target named with --erase that passes partition_done (the source's
/// table there, or the source disk has none), with no record that load
/// wrote it: the partition stage runs.
pub fn unrecorded(layout: &Layout, d: &Disk, t: &TargetDisk, opts: &Options, sys: &Sys) -> bool {
    partition_done(d, t) && is_erased(opts, t) && !load_wrote(layout, d, t, sys)
}

/// Whether this run's partition stage wipes the targets: --only partition
/// does; --through (and the plan, which ends in the --through command to
/// run) skips it once status shows it done on every target, as exec does.
fn partition_runs(layout: &Layout, opts: &Options, system: &System, sys: &Sys, m: &Mapping) -> bool {
    if let Some(s) = opts.only {
        return s == Stage::Partition;
    }
    let done = m.pairs.iter().all(|(s, t)| match (layout.disk(s), system.disk(t)) {
        (Some(d), Some(t)) => partition_counts_done(layout, d, t, opts, sys),
        _ => true,
    });
    !(sys.privileged && done)
}

/// Whether what busies DISK is load's own, from an earlier stage of this
/// layout: anything stacked on it mounted under --target (the mount
/// stage's), or one of the layout's pools imported there (assemble's).
/// Neither let go of nor refused (layload.md, "Safety", rule 4).
fn loads_own(sys: &Sys, system: &System, opts: &Options, pools: &[String], disk: &str) -> bool {
    let stack = super::release::stack(&system.devs, disk);
    let under = |t: &str| t == opts.target || t.starts_with(&format!("{}/", opts.target));
    let devno = |n: &str| system.devs.iter().find(|b| b.name == n).map(|b| b.devno.clone());
    let mounted = system
        .mounts
        .iter()
        .filter(|m| under(&m.target))
        .any(|m| stack.iter().any(|n| devno(n).as_deref() == Some(m.devno.as_str()) || resolve_name(sys, &m.source) == *n));
    mounted || system.pools.iter().any(|p| p.is_ours(pools, &opts.target) && p.devices.iter().any(|d| stack.contains(d)))
}

/// What holds the targets named with --erase, let go of at the start of the
/// partition stage (release.rs).  LAYOUT is the one the stages build from.
/// A target that is_ours (an earlier stage's, or the source's own disk) goes
/// the same way, with the same refusals, when the partition stage will run;
/// when the stage won't run (done already, under --through: the table
/// load's by its record), nothing is let go and nothing refused.  Load's own
/// in its stack, when the stage will run, is refused: wipefs couldn't open
/// the disk under it.
fn release_holders(layout: &Layout, opts: &Options, system: &System, sys: &Sys, m: &mut Mapping) {
    let runs = partition_runs(layout, opts, system, sys, m);
    if runs {
        for (src, tgt) in &m.pairs {
            let (Some(d), Some(t)) = (layout.disk(src), system.disk(tgt)) else { continue };
            if unrecorded(layout, d, t, opts, sys) {
                let what = if d.table.is_some() { "wrote this table (the source's table, as on the source's own disk as found" } else { "wiped this disk (its source has no table, its content goes on the disk itself" };
                m.notes.push(format!("{src} -> {}: the partition stage runs: --erase and no record that load {what}; {}), so what's on it is let go of and wiped", t.path(), table_record(tgt)));
            }
        }
    }
    if !m.ours_busy.is_empty() && runs {
        let pools = layout_pools(layout);
        for name in m.ours_busy.clone() {
            let Some(t) = system.disk(&name) else { continue };
            let src = m.pairs.iter().find(|(_, x)| *x == name).map(|(s, _)| s.clone()).unwrap_or_default();
            if loads_own(sys, system, opts, &pools, &name) {
                m.problems.push(format!(
                    "{src} -> {}: the partition stage would wipe it, but load's own is on it, from an earlier stage (mounted under {}, or a pool of the layout's imported there); `--only close` first lets go of it",
                    t.path(),
                    opts.target
                ));
            } else if !t.hard_use.is_empty() {
                m.problems.push(format!("{src} -> {}: the target is in use ({}); refused even with --erase", t.path(), t.in_use.join("; ")));
            } else if !m.held.contains(&name) {
                m.held.push(name);
            }
        }
    }
    if !m.held.is_empty() {
        // Never the stick's disk or the running system's: check_target
        // refuses those as targets, and they aren't let go of either.
        let erased: Vec<String> = m
            .pairs
            .iter()
            .filter_map(|(_, t)| system.disk(t))
            .filter(|t| is_erased(opts, t) && !t.boot_medium && !t.holds_root)
            .map(|t| t.b.name.clone())
            .collect();
        let r = super::release::plan(sys, system, &m.held, &erased);
        for (disk, why) in r.problems {
            let src = m.pairs.iter().find(|(_, t)| *t == disk).map(|(s, _)| s.clone()).unwrap_or_default();
            m.problems.push(format!("{src} -> /dev/{disk}: the target is held, and what holds it can't be let go: {why}; refused even with --erase"));
        }
        for st in &r.steps {
            m.notes.push(format!("--erase {}: {} ({}) before it's wiped", st.disks.iter().map(|d| format!("/dev/{d}")).collect::<Vec<_>>().join(", "), st.what(), st.stops.join(", ")));
        }
        m.release = r.steps;
    }
}

pub fn is_erased(opts: &Options, t: &TargetDisk) -> bool {
    opts.erase.iter().any(|e| e == &t.path() || e == &t.b.name || resolve_tail(e) == t.b.name)
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
    // With --fit-largest the sizes are the fit's business (fit.rs), which
    // says what it changes and refuses what it can't fit.
    if t.b.size_bytes < src.size_bytes && opts.fit_largest.is_none() {
        m.problems.push(format!(
            "{who}: the target is smaller ({} < {}); version 1 needs the same size or bigger, or --fit-largest PCT to fit the layout to it",
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
    let erase = is_erased(opts, t);
    if !t.in_use.is_empty() && is_ours && erase {
        // An earlier stage's, or the old system's on the source's own disk:
        // release_holders says which.
        m.ours_busy.push(t.b.name.clone());
    }
    if !t.in_use.is_empty() && !is_ours {
        if !t.hard_use.is_empty() {
            m.problems.push(format!("{who}: the target is in use ({}); refused even with --erase", t.in_use.join("; ")));
            return;
        }
        if !erase {
            m.problems.push(format!("{who}: the target is held ({}); give --erase {} to let go of what holds it and wipe it", t.in_use.join("; "), t.path()));
            return;
        }
        // Held only: release.rs says whether it can be let go of, once every
        // target is known (a stack may span several).
        m.held.push(t.b.name.clone());
    }
    if !t.blank() && !is_ours && !erase {
        let what = if t.signatures.is_empty() { "a partition table".to_string() } else { t.signatures.join("; ") };
        m.problems.push(format!("{who}: the target isn't blank ({what}); give --erase {} to wipe it", t.path()));
    }
    if t.b.size_bytes > src.size_bytes && opts.fit_largest.is_none() {
        m.notes.push(format!("{who}: the target is bigger; the extra {} stays unused", crate::report::human(t.b.size_bytes - src.size_bytes)));
        m.bigger.push(src.name.clone());
    }
}

/// What every stage works from: the disks here, which source disk goes to
/// which, and the layout the commands are built from.
pub struct Prepared<'a> {
    pub system: System,
    pub mapping: Mapping,
    /// The dump, or (with --fit-largest) the rewrite of it the stages build
    /// from.  Worked out once here, so every stage sees the same numbers.
    pub layout: Cow<'a, Layout>,
    pub fit: Fit,
}

/// Scans the disks here, maps the source disks onto them and, with
/// --fit-largest, fits the layout to their sizes.
pub fn prepare<'a>(layout: &'a Layout, opts: &Options, sys: &Sys) -> Prepared<'a> {
    let system = scan(sys, opts, &layout_pools(layout));
    let mut mapping = map_disks(layout, opts, &system, sys);
    let mut fit = Fit::default();
    if let Some(pct) = opts.fit_largest {
        let targets: Vec<fit::Target> = mapping
            .pairs
            .iter()
            .filter_map(|(src, t)| system.disk(t).map(|t| fit::Target { disk: src.clone(), path: t.path(), size_bytes: t.b.size_bytes }))
            .collect();
        fit = fit::fit(&layout.v, &targets, pct);
        mapping.problems.extend(fit.problems.iter().cloned());
        mapping.notes.extend(fit.notes.iter().cloned());
        for d in &fit.new_end {
            if !mapping.bigger.contains(d) {
                mapping.bigger.push(d.clone());
            }
        }
    }
    let mut out = Cow::Borrowed(layout);
    if let Some(v) = fit.v.clone() {
        match Layout::from_json(v) {
            Ok(l) => out = Cow::Owned(l),
            Err(e) => mapping.problems.push(format!("--fit-largest: the layout it fits isn't one load can read back: {e}")),
        }
    }
    for p in fitted_already(layout, &out, &system, opts, &mapping) {
        mapping.problems.push(p);
    }
    release_holders(&out, opts, &system, sys, &mut mapping);
    mapping.records = records(&out, &system, &mapping, sys);
    Prepared { system, mapping, layout: out, fit }
}

/// A table an earlier stage wrote with another --fit-largest: without the
/// same one again, every later stage would read the disk as the source's and
/// write over what's on it.  `planned` is the layout this run would write,
/// fitted or not; a target that already holds it has nothing wrong with it.
fn fitted_already(src: &Layout, planned: &Layout, system: &System, opts: &Options, m: &Mapping) -> Vec<String> {
    let mut out = Vec::new();
    for (name, tgt) in &m.pairs {
        let (Some(d), Some(plan), Some(t)) = (src.disk(name), planned.disk(name), system.disk(tgt)) else { continue };
        // --erase wipes the table, which is what the message below asks for.
        if is_erased(opts, t) {
            continue;
        }
        let (Some(want), Some(have)) = (d.table.as_ref().and_then(|x| x.id.as_ref()), t.table.as_ref()) else { continue };
        if !table_id(have).eq_ignore_ascii_case(want) {
            continue;
        }
        // What this run plans is already there, by the same check status
        // makes of the partition stage: the stages go on from it.
        let ext = have.extents();
        if plan.parts.iter().all(|q| ext.iter().any(|&(n, s, len)| n == q.number && s == q.start_lba && len == q.sectors)) {
            continue;
        }
        let target = fit::Target { disk: name.clone(), path: t.path(), size_bytes: t.b.size_bytes };
        let Some((n, by)) = fit::wrote(&src.v, &target, &ext) else { continue };
        let part = d.parts.iter().find(|q| q.number == n).map(|q| q.name.clone()).unwrap_or_else(|| format!("partition {n}"));
        out.push(format!(
            "{name} -> {}: the table there is this layout's, fitted to the disk ({part} is {} {} there); run the stages with the --fit-largest PCT that wrote it, or --erase {} to start again",
            t.path(),
            crate::report::human(by.unsigned_abs() * d.sector_size),
            if by < 0 { "smaller" } else { "bigger" },
            t.path()
        ));
    }
    out
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
    fn fit_largest_takes_a_smaller_target() {
        let t = tree();
        let s = sys(&t);
        let l = layout();
        // vdc is 8 GiB, half of the source's 16: too small to fit.
        let o = opts(&["--map", "vda=vdc", "--fit-largest", "50"]);
        let p = prepare(&l, &o, &s);
        assert_eq!(p.mapping.pairs, vec![("vda".to_string(), "vdc".to_string())]);
        assert!(p.mapping.problems.iter().any(|x| x.contains("more than 50%")), "{:?}", p.mapping.problems);
        // Without the flag it's the old refusal, which names the flag.
        let o = opts(&["--map", "vda=vdc"]);
        let p = prepare(&l, &o, &s);
        assert!(p.mapping.problems.iter().any(|x| x.contains("the target is smaller") && x.contains("--fit-largest PCT")), "{:?}", p.mapping.problems);
        // The same size: the flag changes nothing, and says nothing.
        let o = opts(&["--map", "vda=vdb", "--fit-largest", "10"]);
        let p = prepare(&l, &o, &s);
        assert!(p.mapping.problems.is_empty() && p.mapping.notes.is_empty(), "{:?} {:?}", p.mapping.problems, p.mapping.notes);
        assert!(!p.fit.changed() && p.fit.disks.is_empty() && p.mapping.bigger.is_empty());
        assert!(matches!(p.layout, Cow::Borrowed(_)));
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

    /// The old vg0 on vdb2, its root (dm-0, 252:0) and swap (dm-1) LVs
    /// active: what the rescue system's LVM autoactivated at boot.
    fn old_vg0(t: &Tree) {
        let vg = "Yb3kFa0Pz1AbCdEfGhIjKlMnOpQrStUv";
        t.partition("vdb", "vdb2", "253:18", 2, 4096, 65536, &[]);
        t.file("/dev/vdb2", crate::probe::lvm::sample::pv(512, 1 << 20));
        for (i, lv) in ["root", "swap"].iter().enumerate() {
            let dm = format!("dm-{i}");
            t.file(&format!("/sys/block/vdb/vdb2/holders/{dm}"), "");
            t.disk(&dm, &format!("252:{i}"), &vec![0u8; 65536], 512)
                .file(&format!("/sys/block/{dm}/dm/name"), format!("vg0-{lv}\n"))
                .file(&format!("/sys/block/{dm}/dm/uuid"), format!("LVM-{vg}{}\n", i.to_string().repeat(32)))
                .file(&format!("/sys/block/{dm}/slaves/vdb2"), "");
        }
    }

    /// BackShot2's leftovers L3: the rescue system autoactivated the old
    /// vg0 (root and swap LVs on vdb2) at boot.  Without --erase the target
    /// is refused as before; with it, vg0 is deactivated (LVM seeing vdb2
    /// only) before the partition stage's wipefs; a swap LV refuses it.
    #[test]
    fn a_held_target_is_let_go_of_with_erase_only() {
        let t = tree();
        old_vg0(&t);
        let s = Sys::new(t.root(), Box::new(FakeRunner::default()), true);
        let o = opts(&["--map", "vda=vdb"]);
        let m = map(&layout(), &o, &scan(&s, &o, &[]), &s);
        let p = m.problems.join("\n");
        assert!(p.contains("vda -> /dev/vdb: the target is held (vdb2 is held by dm-0, dm-1); give --erase /dev/vdb to let go of what holds it and wipe it"), "{p}");
        assert!(m.release.is_empty());

        let o = opts(&["--map", "vda=vdb", "--erase", "/dev/vdb"]);
        let m = map(&layout(), &o, &scan(&s, &o, &[]), &s);
        assert!(m.problems.is_empty(), "{:?}", m.problems);
        assert_eq!(m.release.len(), 1);
        assert_eq!(m.release[0].action, super::super::release::Action::Lvm { vg: "vg0".into(), pvs: vec!["/dev/vdb2".into()] });
        assert!(m.notes.iter().any(|n| n == "--erase /dev/vdb: volume group vg0 deactivated (dm-0 (vg0-root), dm-1 (vg0-swap)) before it's wiped"), "{:?}", m.notes);
        let b = super::super::commands::build(&layout(), &o, &m);
        let text: String = b.stage(super::super::Stage::Partition).iter().map(|c| c.shell()).collect();
        let at = |x: &str| text.find(x).unwrap_or_else(|| panic!("no {x:?} in\n{text}"));
        assert!(at("# volume group vg0 deactivated (dm-0 (vg0-root), dm-1 (vg0-swap))\n") < at("vgchange --devicesfile '' --devices /dev/vdb2 --activate n vg0"), "{text}");
        assert!(text.starts_with("# --erase /dev/vdb: what this system started on it"), "{text}");
        assert!(at("vgchange --devicesfile") < at("O_EXCL\nudevadm settle\n"), "{text}");
        assert!(at("O_EXCL\nudevadm settle\n") < at("wipefs --all /dev/vdb\n"), "{text}");

        // The swap LV in use: refused even with --erase, and nothing let go.
        t.file("/proc/swaps", "Filename\tType\tSize\tUsed\tPriority\n/dev/dm-1                               partition\t1048572\t0\t-2\n");
        let m = map(&layout(), &o, &scan(&s, &o, &[]), &s);
        let p = m.problems.join("\n");
        assert!(p.contains("vda -> /dev/vdb: the target is held, and what holds it can't be let go: dm-1 (vg0-swap) is in use as swap; refused even with --erase"), "{p}");
        assert!(m.release.is_empty());
        // A partition mounted: refused as before, --erase or not.
        t.file("/proc/swaps", "Filename\tType\tSize\tUsed\tPriority\n");
        t.file("/proc/self/mountinfo", "30 1 253:18 / /mnt/old rw - ext4 /dev/vdb2 rw\n");
        let p = map(&layout(), &o, &scan(&s, &o, &[]), &s).problems.join("\n");
        assert!(p.contains("the target is in use (vdb2 is mounted at /mnt/old; vdb2 is held by dm-0, dm-1); refused even with --erase"), "{p}");
    }

    /// An MBR with the sample layout's signature and PARTS (slot, start,
    /// sectors).
    fn mbr(parts: &[(usize, u32, u32)]) -> Vec<u8> {
        let mut s = vec![0u8; 512];
        s[440..444].copy_from_slice(&0x1f2e_3d4cu32.to_le_bytes());
        for &(slot, start, n) in parts {
            crate::parttable::build::mbr_entry(&mut s, slot, 0, 0x83, start, n);
        }
        s
    }

    /// The source's own disk (BackShot1's case): its table carries the
    /// layout's identifier, so the target is_ours, and the rescue system
    /// autoactivated the old vg0 on it.  With --erase it's let go of as on
    /// any other disk, when the partition stage will run and what holds it
    /// isn't load's own; otherwise nothing changes.
    #[test]
    fn the_sources_own_disk_is_let_go_of_too() {
        let t = tree();
        old_vg0(&t);
        // The old table: the layout's signature, other partitions.
        t.file("/dev/vdb", mbr(&[(1, 4096, 65536)]));
        let s = Sys::new(t.root(), Box::new(FakeRunner::default()), true);
        let l = layout();
        let run = |a: &[&str]| prepare(&l, &opts(a), &s).mapping;
        let lvm = super::super::release::Action::Lvm { vg: "vg0".into(), pvs: vec!["/dev/vdb2".into()] };
        let o = opts(&["--map", "vda=vdb"]);
        let system = scan(&s, &o, &[]);
        assert!(system.disk("vdb").unwrap().is_ours(l.disk("vda").unwrap(), &layout_uuids(&l)));
        assert!(!partition_done(l.disk("vda").unwrap(), system.disk("vdb").unwrap()));

        // Without --erase: as before, neither refused nor let go of.
        let m = run(&["--map", "vda=vdb"]);
        assert!(m.problems.is_empty() && m.release.is_empty(), "{:?} {:?}", m.problems, m.release);

        // With it, the plan and a --through run let go of vg0, then wipe.
        for a in [&["--map", "vda=vdb", "--erase", "/dev/vdb"][..], &["--map", "vda=vdb", "--erase", "/dev/vdb", "--through", "mount"]] {
            let m = run(a);
            assert!(m.problems.is_empty(), "{a:?}: {:?}", m.problems);
            assert_eq!(m.release.iter().map(|x| &x.action).collect::<Vec<_>>(), vec![&lvm], "{a:?}");
            let b = super::super::commands::build(&l, &opts(a), &m);
            let text: String = b.stage(super::super::Stage::Partition).iter().map(|c| c.shell()).collect();
            let at = |x: &str| text.find(x).unwrap_or_else(|| panic!("no {x:?} in\n{text}"));
            assert!(at("vgchange --devicesfile '' --devices /dev/vdb2 --activate n vg0") < at("wipefs --all /dev/vdb\n"), "{text}");
        }
        // The same refusals: an LV mounted outside --target.
        t.file("/proc/self/mountinfo", "30 1 252:0 / /mnt/old rw - ext4 /dev/mapper/vg0-root rw\n");
        let p = run(&["--map", "vda=vdb", "--erase", "/dev/vdb"]).problems.join("\n");
        assert!(p.contains("vda -> /dev/vdb: the target is held, and what holds it can't be let go: dm-0 (vg0-root) is mounted at /mnt/old; refused even with --erase"), "{p}");
        // A partition mounted: refused with --erase, as before without.
        t.file("/proc/self/mountinfo", "30 1 253:18 / /mnt/old rw - ext4 /dev/vdb2 rw\n");
        let m = run(&["--map", "vda=vdb", "--erase", "/dev/vdb"]);
        assert!(m.problems.join("\n").contains("vda -> /dev/vdb: the target is in use (vdb2 is mounted at /mnt/old; vdb2 is held by dm-0, dm-1); refused even with --erase"), "{:?}", m.problems);
        let m = run(&["--map", "vda=vdb"]);
        assert!(m.problems.is_empty() && m.release.is_empty(), "{:?}", m.problems);

        // An LV mounted under --target, from an earlier partial run: load's
        // own, so nothing is let go of; the partition stage runs (the table
        // isn't the layout's), and wipefs couldn't open the disk under it,
        // so with --erase the plan refuses it.  Without, taken as it is.
        t.file("/proc/self/mountinfo", "30 1 252:0 / /mnt/bko-target rw - ext4 /dev/mapper/vg0-root rw\n");
        for a in [&["--map", "vda=vdb", "--erase", "/dev/vdb"][..], &["--map", "vda=vdb", "--erase", "/dev/vdb", "--through", "mount"]] {
            let m = run(a);
            assert!(m.release.is_empty(), "{a:?}: {:?}", m.release);
            let p = m.problems.join("\n");
            assert!(p.contains("vda -> /dev/vdb: the partition stage would wipe it, but load's own is on it, from an earlier stage (mounted under /mnt/bko-target, or a pool of the layout's imported there); `--only close` first lets go of it"), "{a:?}: {p}");
        }
        let m = run(&["--map", "vda=vdb"]);
        assert!(m.problems.is_empty() && m.release.is_empty(), "{:?} {:?}", m.problems, m.release);
        t.file("/proc/self/mountinfo", "");

        // Another stage alone never reaches the partition stage.
        let m = run(&["--map", "vda=vdb", "--erase", "/dev/vdb", "--only", "status"]);
        assert!(m.problems.is_empty() && m.release.is_empty(), "{:?}", m.problems);
    }

    /// The sample layout's own table, every partition at its sectors.
    fn own_table() -> Vec<u8> {
        mbr(&[(0, 2048, 1953792), (1, 1955840, 3905536), (2, 5861376, 27693056)])
    }

    /// The source's own disk as found, its table unchanged (the usual
    /// bare-metal restore: same disk, same table), the old vg0 active on it.
    /// The table passes status's check, but with --erase that isn't enough:
    /// only this session's record that load wrote it makes the partition
    /// stage done.  Without the record, the stage runs, letting go of vg0
    /// with the usual refusals; with it, a rerun skips the stage, as before.
    #[test]
    fn the_sources_own_table_as_found_is_wiped_with_erase() {
        let t = tree();
        old_vg0(&t);
        t.file("/dev/vdb", own_table());
        let s = Sys::new(t.root(), Box::new(FakeRunner::default()), true);
        let l = layout();
        let run = |a: &[&str]| prepare(&l, &opts(a), &s).mapping;
        let lvm = super::super::release::Action::Lvm { vg: "vg0".into(), pvs: vec!["/dev/vdb2".into()] };
        let o = opts(&["--map", "vda=vdb"]);
        let (d, system) = (l.disk("vda").unwrap(), scan(&s, &o, &[]));
        let vdb = system.disk("vdb").unwrap();
        assert!(partition_done(d, vdb));
        let partition = |a: &[&str]| super::super::status::rows(&l, &opts(a), &s).0.into_iter().find(|r| r.0 == Stage::Partition).map(|r| (r.1, r.2)).unwrap();
        use super::super::status::State;
        let erase = ["--map", "vda=vdb", "--erase", "/dev/vdb"];
        let through = ["--map", "vda=vdb", "--erase", "/dev/vdb", "--through", "mount"];
        let note = "vda -> /dev/vdb: the partition stage runs: --erase and no record that load wrote this table";

        // Without --erase: as before, the stage done and nothing let go of.
        for a in [&["--map", "vda=vdb"][..], &["--map", "vda=vdb", "--through", "mount"]] {
            let m = run(a);
            assert!(m.problems.is_empty() && m.release.is_empty(), "{a:?}: {:?} {:?}", m.problems, m.release);
            assert!(!m.notes.iter().any(|n| n.contains(note)), "{a:?}: {:?}", m.notes);
        }
        assert_eq!(partition(&["--map", "vda=vdb"]).0, State::Done);

        // With it and no record: the stage runs, vg0 let go of, then wiped.
        for a in [&erase[..], &through] {
            let m = run(a);
            assert!(m.problems.is_empty(), "{a:?}: {:?}", m.problems);
            assert_eq!(m.release.iter().map(|x| &x.action).collect::<Vec<_>>(), vec![&lvm], "{a:?}");
            assert!(m.notes.iter().any(|n| n.starts_with(note) && n.contains("/run/bko-holt/partitioned/vdb")), "{a:?}: {:?}", m.notes);
            let b = super::super::commands::build(&l, &opts(a), &m);
            let text: String = b.stage(Stage::Partition).iter().map(|c| c.shell()).collect();
            let at = |x: &str| text.find(x).unwrap_or_else(|| panic!("no {x:?} in\n{text}"));
            assert!(at("vgchange --devicesfile '' --devices /dev/vdb2 --activate n vg0") < at("wipefs --all /dev/vdb\n"), "{text}");
            assert_eq!(m.records, vec![(table_record("vdb"), table_record_text(&l, d, vdb, &s))]);
        }
        assert_eq!(partition(&erase), (State::NotDone, "/dev/vdb: the source's table, but no record that load wrote it, and --erase wipes it".to_string()));
        // The same refusals: an LV mounted outside --target.
        t.file("/proc/self/mountinfo", "30 1 252:0 / /mnt/old rw - ext4 /dev/mapper/vg0-root rw\n");
        for a in [&erase[..], &through] {
            let p = run(a).problems.join("\n");
            assert!(p.contains("vda -> /dev/vdb: the target is held, and what holds it can't be let go: dm-0 (vg0-root) is mounted at /mnt/old; refused even with --erase"), "{a:?}: {p}");
        }
        assert!(run(&["--map", "vda=vdb"]).problems.is_empty());
        t.file("/proc/self/mountinfo", "");

        // A record of another layout's table (another dump) is no record.
        t.file(&table_record("vdb"), table_record_text(&l, d, vdb, &s).replace("layout deb13 ", "layout other "));
        assert_eq!(run(&erase).release.len(), 1);
        assert_eq!(partition(&erase).0, State::NotDone);

        // With this session's record: load wrote the table, so --through
        // (and the plan) skip the stage and let go of nothing; --only
        // partition runs it, and lets go first.
        t.file(&table_record("vdb"), table_record_text(&l, d, vdb, &s));
        for a in [&erase[..], &through] {
            let m = run(a);
            assert!(m.problems.is_empty() && m.release.is_empty(), "{a:?}: {:?} {:?}", m.problems, m.release);
            assert!(!m.notes.iter().any(|n| n.contains(note)), "{a:?}: {:?}", m.notes);
        }
        assert_eq!(partition(&erase).0, State::Done);
        // udev's serial and WWID coming (or going) between runs leave the
        // record standing: it names the disk by sysfs only.  The kernel's
        // own serial changing is another disk.
        t.file("/run/udev/data/b253:16", "E:ID_SERIAL_SHORT=SER123\nE:ID_WWN=0x5000c500a1b2c3d4\n");
        assert_eq!(scan(&s, &o, &[]).disk("vdb").unwrap().b.serial.as_deref(), Some("SER123"));
        assert_eq!(partition(&erase).0, State::Done);
        assert!(run(&through).release.is_empty());
        t.file("/sys/block/vdb/device/serial", "OTHER\n");
        assert_eq!(partition(&erase).0, State::NotDone);
        std::fs::remove_file(t.p("/sys/block/vdb/device/serial")).unwrap();
        assert_eq!(partition(&erase).0, State::Done);
        let m = run(&["--map", "vda=vdb", "--erase", "/dev/vdb", "--only", "partition"]);
        assert!(m.problems.is_empty(), "{:?}", m.problems);
        assert_eq!(m.release.iter().map(|x| &x.action).collect::<Vec<_>>(), vec![&lvm]);

        // Load's own under --target (the mount stage's, of this session):
        // a rerun goes on from it, but --only partition would wipe the disk
        // under it, so the plan refuses it rather than wipefs failing.
        t.file("/proc/self/mountinfo", "30 1 252:0 / /mnt/bko-target rw - ext4 /dev/mapper/vg0-root rw\n");
        for a in [&erase[..], &through] {
            let m = run(a);
            assert!(m.problems.is_empty() && m.release.is_empty(), "{a:?}: {:?} {:?}", m.problems, m.release);
        }
        let m = run(&["--map", "vda=vdb", "--erase", "/dev/vdb", "--only", "partition"]);
        assert!(m.release.is_empty(), "{:?}", m.release);
        let p = m.problems.join("\n");
        assert!(p.contains("vda -> /dev/vdb: the partition stage would wipe it, but load's own is on it, from an earlier stage (mounted under /mnt/bko-target"), "{p}");
        assert!(p.contains("`--only close` first lets go of it"), "{p}");
        // A partition mounted there, too: the same refusal, not wipefs's.
        t.file("/proc/self/mountinfo", "30 1 253:18 / /mnt/bko-target rw - ext4 /dev/vdb2 rw\n");
        let p = run(&["--map", "vda=vdb", "--erase", "/dev/vdb", "--only", "partition"]).problems.join("\n");
        assert!(p.contains("vda -> /dev/vdb: the partition stage would wipe it, but load's own is on it, from an earlier stage (mounted under /mnt/bko-target"), "{p}");
        assert!(!p.contains("refused even with --erase"), "{p}");
    }

    /// A source disk with no table, its XFS on the whole disk (UUID
    /// a0a1a2a3-...: the sample superblock's).
    const VDX: &str = r#"{"name": "vdx", "kind": "disk", "size_bytes": 8589934592, "logical_sector_size": 512, "model": null, "serial": null, "wwid": null,
      "partition_table": null, "partitions": [],
      "content": {"type": "xfs", "usage": "filesystem", "uuid": "a0a1a2a3-a4a5-a6a7-a8a9-aaabacadaeaf", "label": null}}"#;

    /// The sample layout with vdx beside vda, or (ALONE) instead of it.
    fn with_vdx(alone: bool) -> Layout {
        let j = sample::bios_mbr();
        let j = if alone {
            let (a, rest) = j.split_at(j.find("\"disks\": [").unwrap());
            format!("{a}\"disks\": [{VDX}],\n    {}", &rest[rest.find("\"md_arrays\"").unwrap()..])
        } else {
            j.replace("\"content\": null, \"boot_code\": {\"kind\": \"grub2\"}\n    }]", &format!("\"content\": null, \"boot_code\": {{\"kind\": \"grub2\"}}\n    }}, {VDX}]"))
        };
        let l = Layout::from_json(crate::json::parse(&j).unwrap()).unwrap();
        assert_eq!(l.disks.iter().map(|d| d.name.as_str()).collect::<Vec<_>>(), if alone { vec!["vdx"] } else { vec!["vda", "vdx"] });
        l
    }

    fn row(l: &Layout, a: &[&str], s: &Sys, st: Stage) -> (super::super::status::State, String) {
        super::super::status::rows(l, &opts(a), s).0.into_iter().find(|r| r.0 == st).map(|r| (r.1, r.2)).unwrap()
    }

    /// The whole-disk XFS layout onto the source's own disk, the old XFS
    /// (the layout's UUID) still there: with --erase the partition stage,
    /// the one that wipes and zeroes it, runs until this session's record
    /// says it did, though there's no table for status to check; format's
    /// check would take the old filesystem for its own.  Without --erase,
    /// as before: nothing to do.
    #[test]
    fn a_disk_with_no_table_named_with_erase_is_wiped_until_recorded() {
        use super::super::status::State;
        let t = tree();
        t.disk("vdc", "253:32", &crate::probe::xfs::sample::image(""), 512).file("/sys/block/vdc/size", "16777216\n");
        let s = Sys::new(t.root(), Box::new(FakeRunner::default()), true);
        let l = with_vdx(true);
        let plain = ["--map", "vdx=vdc"];
        let erase = ["--map", "vdx=vdc", "--erase", "/dev/vdc"];
        let through = ["--map", "vdx=vdc", "--erase", "/dev/vdc", "--through", "mount"];
        assert_eq!(row(&l, &plain, &s, Stage::Partition).0, State::Nothing);
        assert_eq!(row(&l, &plain, &s, Stage::Format).0, State::Done, "the old XFS passes format's check");
        let m = prepare(&l, &opts(&plain), &s).mapping;
        assert!(m.problems.is_empty() && !m.notes.iter().any(|n| n.contains("no record")), "{:?} {:?}", m.problems, m.notes);

        assert_eq!(row(&l, &erase, &s, Stage::Partition), (State::NotDone, "/dev/vdc: no record that load wiped it, and --erase wipes it".to_string()));
        for a in [&erase[..], &through] {
            let m = prepare(&l, &opts(a), &s).mapping;
            assert!(m.problems.is_empty(), "{a:?}: {:?}", m.problems);
            assert!(m.notes.iter().any(|n| n.starts_with("vdx -> /dev/vdc: the partition stage runs: --erase and no record that load wiped this disk (its source has no table")), "{a:?}: {:?}", m.notes);
            let vdc = scan(&s, &opts(a), &[]);
            assert_eq!(m.records, vec![(table_record("vdc"), table_record_text(&l, l.disk("vdx").unwrap(), vdc.disk("vdc").unwrap(), &s))]);
            assert!(m.records[0].1.contains("no table: wiped\n"), "{:?}", m.records);
        }
        let (path, text) = prepare(&l, &opts(&erase), &s).mapping.records.remove(0);
        t.file(&path, text);
        assert_eq!(row(&l, &erase, &s, Stage::Partition).0, State::Done);
        let m = prepare(&l, &opts(&through), &s).mapping;
        assert!(m.problems.is_empty() && !m.notes.iter().any(|n| n.contains("no record")), "{:?} {:?}", m.problems, m.notes);
    }

    /// A disk with a table (vda -> vdb, the old vg0 on it) and one without
    /// (vdx -> vdc), both named with --erase: plan, status and --through
    /// agree on whether the partition stage runs, both records needed.  A
    /// resume with both recorded and load's own mount under --target goes
    /// on, neither refused nor let go of.
    #[test]
    fn a_resume_over_disks_with_and_without_a_table_agrees() {
        use super::super::status::State;
        let t = tree();
        old_vg0(&t);
        t.file("/dev/vdb", own_table());
        let s = Sys::new(t.root(), Box::new(FakeRunner::default()), true);
        let l = with_vdx(false);
        let erase = ["--map", "vda=vdb", "--map", "vdx=vdc", "--erase", "/dev/vdb", "--erase", "/dev/vdc"];
        let through = ["--map", "vda=vdb", "--map", "vdx=vdc", "--erase", "/dev/vdb", "--erase", "/dev/vdc", "--through", "mount"];
        let lvm = super::super::release::Action::Lvm { vg: "vg0".into(), pvs: vec!["/dev/vdb2".into()] };
        let runs = |a: &[&str]| {
            let m = prepare(&l, &opts(a), &s).mapping;
            assert!(m.problems.is_empty(), "{a:?}: {:?}", m.problems);
            m.release.iter().map(|x| x.action.clone()).collect::<Vec<_>>()
        };
        // No records: both disks unrecorded, the stage runs.
        assert_eq!(row(&l, &erase, &s, Stage::Partition).0, State::NotDone);
        assert_eq!(runs(&through), vec![lvm.clone()]);
        let records = prepare(&l, &opts(&erase), &s).mapping.records;
        assert_eq!(records.iter().map(|r| r.0.as_str()).collect::<Vec<_>>(), vec!["/run/bko-holt/partitioned/vdb", "/run/bko-holt/partitioned/vdc"]);
        // vdb's alone: vdc still unrecorded, so the stage still runs.
        t.file(&records[0].0, records[0].1.as_str());
        let (st, why) = row(&l, &erase, &s, Stage::Partition);
        assert_eq!((st, why.as_str()), (State::Partly, "/dev/vdc: no record that load wiped it, and --erase wipes it"));
        assert_eq!(runs(&through), vec![lvm.clone()]);
        // Both: done, by status and by the plan alike.
        t.file(&records[1].0, records[1].1.as_str());
        assert_eq!(row(&l, &erase, &s, Stage::Partition).0, State::Done);
        assert!(runs(&through).is_empty() && runs(&erase).is_empty());
        t.file("/proc/self/mountinfo", "30 1 252:0 / /mnt/bko-target rw - ext4 /dev/mapper/vg0-root rw\n");
        assert_eq!(row(&l, &through, &s, Stage::Partition).0, State::Done);
        assert!(runs(&through).is_empty() && runs(&erase).is_empty());
    }
}
