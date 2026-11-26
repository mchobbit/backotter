//! The machine `load` runs on: its disks, what's on them and what's in use,
//! and which of them each source disk goes to (layload.md, "Safety" and
//! "Mapping disks").

use super::codes::{Note, NoteCode, Notes, Problem, ProblemCode, Problems};
use super::fit::{self, Fit};
use super::layout::{Disk, Layout};
use super::{Options, RUN_DIR, Stage};
use crate::blockdev::{self, BlockDev, Kind};
use crate::mounts::{self, Mount};
use crate::parttable::{self, Table};
use crate::sys::Sys;
use std::borrow::Cow;

/// Where a live system mounts the medium it booted from.
const MEDIUM_MOUNTS: [&str; 4] = [
    "/cdrom",
    "/run/live/medium",
    "/lib/live/mount/medium",
    "/run/initramfs/live",
];
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
        let table = match (
            &self.table,
            src.table
                .as_ref()
                .and_then(|t| t.id.as_ref())
                .filter(|id| id.as_str() != "0x00000000"),
        ) {
            (Some(t), Some(id)) => table_id(t).eq_ignore_ascii_case(id),
            _ => false,
        };
        table || (!self.uuids.is_empty() && self.uuids.iter().all(|u| layout_uuids.contains(u)))
    }

    pub fn path(&self) -> String {
        self.b.dev_path()
    }

    pub fn blank(&self) -> bool {
        self.table.is_none()
            && self.signatures.is_empty()
            && self.in_use.is_empty()
            && self.parts.is_empty()
    }

    pub fn describe(&self) -> String {
        let mut s = format!(
            "{} ({}",
            self.path(),
            crate::report::human(self.b.size_bytes)
        );
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
    for b in all
        .iter()
        .filter(|b| !matches!(b.kind, Kind::Rom | Kind::Floppy | Kind::Zram | Kind::Ram))
    {
        let Some(src) = sys.open_dev(&b.dev_path()) else {
            continue;
        };
        for c in crate::probe::probe_all(&src)
            .into_iter()
            .filter(|c| c.kind == "LVM2_member")
        {
            let vg = |k: &str| {
                c.extra
                    .as_ref()
                    .and_then(|v| v.get(k))
                    .and_then(crate::json::Value::as_str)
                    .map(str::to_string)
            };
            if let Some(name) = vg("name") {
                let disks = super::release::bases(all, &b.name);
                out.push(HostPv {
                    dev: b.name.clone(),
                    uuid: c.uuid.clone().unwrap_or_default(),
                    vg: name,
                    vg_uuid: vg("id").unwrap_or_default(),
                    disks,
                });
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
            let bare = first.len() > 3
                && first.starts_with('c')
                && first[1..].starts_with(|c: char| c.is_ascii_digit())
                && first.contains('d')
                && !first.contains('-');
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
        .map(|o| {
            o.lines()
                .filter_map(|l| l.split_once('\t'))
                .map(|(a, b)| (a.to_string(), b.trim().to_string()))
                .collect()
        })
        .unwrap_or_default();
    let status = sys.run("zpool", &["status", "-P"]).unwrap_or_default();
    parse_zpool_status(&status)
        .into_iter()
        .map(|(name, paths)| ImportedPool {
            altroot: roots
                .iter()
                .find(|(n, _)| *n == name)
                .map(|(_, a)| a.clone())
                .unwrap_or_else(|| "-".into()),
            devices: paths.iter().map(|p| resolve_name(sys, p)).collect(),
            name,
        })
        .collect()
}

/// The names of the pools a layout makes.
pub fn layout_pools(layout: &Layout) -> Vec<String> {
    let names = layout_pool_names(layout);
    names
        .iter()
        .cloned()
        .chain(names.iter().map(|n| temp_pool_name(n)))
        .collect()
}

fn layout_pool_names(layout: &Layout) -> Vec<String> {
    layout
        .v
        .at(&["storage", "zfs", "tools", "imported_pools"])
        .and_then(crate::json::Value::as_array)
        .into_iter()
        .flatten()
        .filter_map(|p| {
            p.get("name")
                .and_then(crate::json::Value::as_str)
                .map(str::to_string)
        })
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
    if let Some(n) = spec
        .strip_prefix("/dev/dsk/")
        .or_else(|| spec.strip_prefix("/dev/rdsk/"))
    {
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
    let mounts = sys
        .read("/proc/self/mountinfo")
        .map(|t| mounts::parse_mountinfo(&t))
        .unwrap_or_default();
    let swaps: Vec<String> = sys
        .read("/proc/swaps")
        .map(|t| mounts::parse_swaps(&t))
        .unwrap_or_default()
        .into_iter()
        .map(|s| resolve_name(sys, &s.file))
        .collect();
    let pools = imported_pools(sys);
    let mut disks = Vec::new();
    for b in all
        .iter()
        .filter(|b| matches!(b.kind, Kind::Disk | Kind::Nbd))
    {
        let parts: Vec<BlockDev> = all
            .iter()
            .filter(|p| p.parent.as_deref() == Some(b.name.as_str()))
            .cloned()
            .collect();
        let table = sys
            .open_dev(&b.dev_path())
            .and_then(|src| parttable::read_table(&src, b.logical_block_size.max(512)));
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
            for m in mounts
                .iter()
                .filter(|m| m.devno == d.devno || resolve_name(sys, &m.source) == d.name)
            {
                if m.target == "/" {
                    holds_root = true;
                }
                if MEDIUM_MOUNTS.contains(&m.target.as_str()) {
                    boot_medium = true;
                }
                // Mounts under the target tree are load's own (mount stage).
                if !(m.target == opts.target || m.target.starts_with(&format!("{}/", opts.target)))
                {
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
                    in_use.push(format!(
                        "{} is a vdev of the imported pool {}",
                        d.name, p.name
                    ));
                }
                if mounts.iter().any(|m| {
                    m.target == "/"
                        && m.fstype == "zfs"
                        && m.source.split('/').next() == Some(p.name.as_str())
                }) {
                    holds_root = true;
                }
            }
            if d.udev.get("ID_FS_LABEL") == Some(STICK_CONFIG_LABEL) {
                boot_medium = true;
            }
        }
        // Bug: scan checked mounts, boot-medium labels, and ZFS root pools only
        // on the raw disk and its partitions, missing md, LUKS, and LVM holders
        // above them (plus mdev /dev/mapper/<dm_name> nodes). A disk backing /
        // or the live medium through LVM, LUKS, md, or ZFS-on-LUKS left
        // holds_root and boot_medium false instead of refusing the disk.
        for name in super::release::stack(&all, &b.name) {
            let Some(d) = all.iter().find(|x| x.name == name) else {
                continue;
            };
            let matches_dev = |x: &str| x == d.name || d.dm_name.as_deref() == Some(x);
            for m in mounts
                .iter()
                .filter(|m| m.devno == d.devno || matches_dev(&resolve_name(sys, &m.source)))
            {
                holds_root |= m.target == "/";
                boot_medium |= MEDIUM_MOUNTS.contains(&m.target.as_str());
            }
            if d.udev.get("ID_FS_LABEL") == Some(STICK_CONFIG_LABEL) {
                boot_medium = true;
            }
            for p in pools
                .iter()
                .filter(|p| p.devices.iter().any(|x| matches_dev(x)))
            {
                if mounts.iter().any(|m| {
                    m.target == "/"
                        && m.fstype == "zfs"
                        && m.source.split('/').next() == Some(p.name.as_str())
                }) {
                    holds_root = true;
                }
            }
        }
        if signatures
            .iter()
            .any(|s| s.contains(&format!("LABEL={STICK_CONFIG_LABEL}")))
        {
            boot_medium = true;
        }
        in_use.splice(0..0, hard_use.iter().cloned());
        disks.push(TargetDisk {
            b: b.clone(),
            parts,
            table,
            signatures,
            uuids,
            in_use,
            hard_use,
            boot_medium,
            holds_root,
        });
    }
    let pvs = host_pvs(sys, &all);
    System {
        disks,
        mounts,
        pools,
        pvs,
        devs: all,
        swaps,
    }
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
    pub problems: Problems,
    pub notes: Notes,
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
        self.pairs
            .iter()
            .find(|(s, _)| s == src_disk)
            .map(|(_, t)| t.as_str())
    }
}

pub(crate) fn same_identity(src: &Disk, t: &BlockDev) -> bool {
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
            m.problems.push(
                Problem::new(
                    ProblemCode::MapUnknownSource,
                    format!(
                        "--map {src}=...: the layout has no disk {src} (it has {})",
                        names(layout)
                    ),
                )
                .with("disk", src),
            );
        }
    }
    let ours = layout_uuids(layout);
    for d in layout.disks.iter() {
        if !d.used {
            m.notes.push(
                Note::new(
                    NoteCode::SourceEmpty,
                    format!(
                        "source disk {} holds nothing; it needs no target",
                        d.describe()
                    ),
                )
                .with("disk", &d.name),
            );
            continue;
        }
        let target = match opts.maps.iter().find(|(s, _)| *s == d.name) {
            Some((_, spec)) => {
                let name = resolve_name(sys, spec);
                match system.disk(&name) {
                    Some(t) => Some(t),
                    None => {
                        m.problems.push(
                            Problem::new(
                                ProblemCode::NoSuchDisk,
                                format!("--map {}={spec}: no whole disk {name} here", d.name),
                            )
                            .with("disk", &d.name)
                            .with("target", &name),
                        );
                        continue;
                    }
                }
            }
            None => system.disks.iter().find(|t| same_identity(d, &t.b)),
        };
        let Some(t) = target else {
            m.problems.push(
                Problem::new(
                    ProblemCode::NotMapped,
                    format!(
                        "source disk {} has no target: give --map {}=DISK (disks here: {})",
                        d.describe(),
                        d.name,
                        system
                            .disks
                            .iter()
                            .map(TargetDisk::describe)
                            .collect::<Vec<_>>()
                            .join("; ")
                    ),
                )
                .with("disk", &d.name),
            );
            continue;
        };
        if let Some((other, _)) = m.pairs.iter().find(|(_, tn)| *tn == t.b.name) {
            m.problems.push(
                Problem::new(
                    ProblemCode::TargetTwice,
                    format!("{} is the target of both {other} and {}", t.path(), d.name),
                )
                .with("disk", &d.name)
                .with("target", &t.b.name),
            );
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
    let s = |v: &crate::json::Value, k: &str| {
        v.get(k)
            .and_then(crate::json::Value::as_str)
            .unwrap_or_default()
            .to_string()
    };
    for vg in layout
        .v
        .at(&["storage", "lvm", "volume_groups"])
        .and_then(crate::json::Value::as_array)
        .into_iter()
        .flatten()
    {
        let (n, id) = (s(vg, "name"), s(vg, "id"));
        let pv_ids: Vec<String> = vg
            .get("physical_volumes")
            .and_then(crate::json::Value::as_array)
            .into_iter()
            .flatten()
            .map(|p| s(p, "id"))
            .filter(|u| !u.is_empty())
            .collect();
        for pv in system
            .pvs
            .iter()
            .filter(|pv| !pv.disks.iter().all(|d| targets.contains(d)))
        {
            let why = if !n.is_empty() && pv.vg == n {
                format!("a volume group named {n}")
            } else if !id.is_empty() && pv.vg_uuid == id {
                format!("volume group {n}'s UUID (as {})", pv.vg)
            } else if pv_ids.contains(&pv.uuid) {
                format!("the UUID of one of {n}'s PVs (in {})", pv.vg)
            } else {
                continue;
            };
            m.problems.push(
                Problem::new(
                    ProblemCode::DuplicateVg,
                    format!(
                        "{why} is on {} ({}), which isn't a target: the restored system's boot tools and its first boot would find it twice; disconnect that disk, or give its group a new name and UUIDs (vgimportclone) first",
                        pv.dev,
                        pv.disks.join(", ")
                    ),
                )
                .with("vg", &n)
                .with("dev", &pv.dev)
                .with("disks", pv.disks.clone()),
            );
        }
    }
    // A pool with no vdev devices read (zpool status unparsed) can't be
    // shown to be on the targets, so it's renamed too: before, an empty
    // list passed (all() of nothing) and the layout's pool kept its name.
    let target_refs: Vec<&str> = targets.iter().map(String::as_str).collect();
    for n in layout
        .v
        .at(&["storage", "zfs", "tools", "imported_pools"])
        .and_then(crate::json::Value::as_array)
        .into_iter()
        .flatten()
        .filter_map(|p| p.get("name").and_then(crate::json::Value::as_str))
    {
        if let Some(ip) = system.pools.iter().find(|ip| ip.name == n) {
            if ip.devices.is_empty()
                || !ip
                    .devices
                    .iter()
                    .all(|d| vdev_on_targets(system, &target_refs, d))
            {
                m.pool_names.push((n.to_string(), temp_pool_name(n)));
                m.notes.push(
                    Note::new(NoteCode::PoolRenamed, format!("ZFS pool {n}: a pool of that name is imported here, so it's {} here until close exports it", temp_pool_name(n)))
                        .with("pool", n)
                        .with("as", temp_pool_name(n)),
                );
            }
        }
    }
    for e in &opts.erase {
        let name = resolve_name(sys, e);
        if !m.pairs.iter().any(|(_, t)| *t == name) {
            m.problems.push(
                Problem::new(
                    ProblemCode::EraseNotTarget,
                    format!("--erase {e}: that isn't a target of this layout, so it isn't erased"),
                )
                .with("target", &name),
            );
        }
    }
    m
}

/// Whether a pool's vdev device `dev` sits exclusively on `targets`: a target
/// whole disk, one of its partitions (or FreeBSD `.eli` provider on one), or a
/// Linux md/dm stack whose underlying whole disks are all in `targets`.
/// Names match exactly: sdaa1 isn't on sda.  illumos' slices (c3t0d0s1)
/// are its disks' parts (other/load.rs names EFI label entries so).
pub fn vdev_on_targets(system: &System, targets: &[&str], dev: &str) -> bool {
    let raw = dev.strip_suffix(".eli").unwrap_or(dev);
    if system.disks.iter().any(|d| {
        targets.contains(&d.b.name.as_str())
            && (d.b.name == raw || d.parts.iter().any(|p| p.name == raw))
    }) {
        return true;
    }
    if system.devs.is_empty() {
        return false;
    }
    // Under mdev (Alpine) /dev/mapper/NAME is a node of its own, not a link
    // to dm-N, so resolve_name gives the mapper name: its kernel name is the
    // device whose dm/name that is.
    let raw = if system.devs.iter().any(|b| b.name == raw) {
        raw
    } else {
        system
            .devs
            .iter()
            .find(|b| b.dm_name.as_deref() == Some(raw))
            .map_or(raw, |b| b.name.as_str())
    };
    let bs = super::release::bases(&system.devs, raw);
    !bs.is_empty()
        && bs.iter().all(|b| {
            system
                .disks
                .iter()
                .any(|d| d.b.name == *b && targets.contains(&d.b.name.as_str()))
        })
}

/// Whether T holds D's partition table as the partition stage writes it, by
/// status's check: the source's identifier (when it has one) and each
/// partition at its sectors.  A source disk with no table gives the stage
/// nothing to check.
pub fn partition_done(d: &Disk, t: &TargetDisk) -> bool {
    let Some(want) = &d.table else { return true };
    let Some(have) = &t.table else { return false };
    let ext = have.extents();
    want.id
        .as_ref()
        .is_none_or(|id| table_id(have).eq_ignore_ascii_case(id))
        && d.parts.iter().all(|p| {
            ext.iter()
                .any(|&(n, s, len)| n == p.number && s == p.start_lba && len == p.sectors)
        })
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
/// Off Linux, which has no sysfs, the serial and WWID are the scan's
/// (kernel_identity), so a disk swapped in under the same node and size
/// there is told apart by them too.
pub fn table_record_text(layout: &Layout, d: &Disk, t: &TargetDisk, sys: &Sys) -> String {
    record_text(layout, d, t, &kernel_identity(sys, t))
}

/// What target T's kernel identity is, as its partition record
/// (table_record_text) names it: (serial, WWID).  On Linux, what its
/// driver gives in sysfs (device/serial, device/wwid), empty when it gives
/// none; off Linux, the scan's (other/load.rs), empty when it found none.
pub fn kernel_identity(sys: &Sys, t: &TargetDisk) -> (String, String) {
    identity_on(crate::other::Family::current(), sys, t)
}

/// kernel_identity as it is on FAM (None: Linux).  On OpenBSD and NetBSD
/// the scan's serial is the disklabel's DUID (other/labelled.rs, from
/// hw.disknames), which the partition stage itself writes: the record
/// written for the target as it was would never be the one read back after
/// it (RT10j review: openbsd8-crypt's partition stage "not done"), so there
/// the record leaves it out, as before RustAudit1 HD2-04.
fn identity_on(fam: Option<crate::other::Family>, sys: &Sys, t: &TargetDisk) -> (String, String) {
    use crate::other::Family;
    if let Some(f) = fam {
        let serial = match f {
            Family::OpenBsd | Family::NetBsd => String::new(),
            _ => t.b.serial.clone().unwrap_or_default(),
        };
        return (serial, t.b.wwid.clone().unwrap_or_default());
    }
    let attr = |a: &str| {
        sys.read_trim(&format!("/sys/block/{}/device/{a}", t.b.name))
            .unwrap_or_default()
    };
    (attr("serial"), attr("wwid"))
}

/// The record of D on T, T's kernel identity being (SERIAL, WWID).
fn record_text(
    layout: &Layout,
    d: &Disk,
    t: &TargetDisk,
    (serial, wwid): &(String, String),
) -> String {
    let mut s = format!(
        "layout {} {}\nsource {}\ntarget {} size={} serial={serial} wwid={wwid}\n",
        layout.hostname(),
        layout.str_at(&["generated"]).unwrap_or("-"),
        d.name,
        t.b.name,
        t.b.size_bytes,
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
        .map(|(d, t)| {
            (
                table_record(&t.b.name),
                table_record_text(layout, d, t, sys),
            )
        })
        .collect()
}

/// Whether this rescue session's partition stage wrote D's table on T (or,
/// with no table, wiped T for D's content).
pub fn load_wrote(layout: &Layout, d: &Disk, t: &TargetDisk, sys: &Sys) -> bool {
    sys.read(&table_record(&t.b.name))
        .is_some_and(|r| r == table_record_text(layout, d, t, sys))
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
pub fn partition_counts_done(
    layout: &Layout,
    d: &Disk,
    t: &TargetDisk,
    opts: &Options,
    sys: &Sys,
) -> bool {
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
pub(crate) fn partition_runs(
    layout: &Layout,
    opts: &Options,
    system: &System,
    sys: &Sys,
    m: &Mapping,
) -> bool {
    if let Some(s) = opts.only {
        return s == Stage::Partition;
    }
    let done = m
        .pairs
        .iter()
        .all(|(s, t)| match (layout.disk(s), system.disk(t)) {
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
    // Bug: under mdev (Alpine), /dev/mapper/<dm_name> is a standalone block node
    // rather than a symlink to /dev/dm-N, while `stack` stores kernel names
    // (dm-N) and btrfs mounts report anonymous 0:N devnos. loads_own missed
    // active mounts and ZFS-on-LUKS pools under --target, allowing --erase to
    // wipe open target stacks without requiring `--only close` first.
    let stack = super::release::stack(&system.devs, disk);
    let in_stack = |x: &str| {
        stack.iter().any(|n| {
            n == x
                || system
                    .devs
                    .iter()
                    .any(|b| b.name == *n && b.dm_name.as_deref() == Some(x))
        })
    };
    let under = |t: &str| {
        t == opts.target || t.starts_with(&format!("{}/", opts.target.trim_end_matches('/')))
    };
    let devno = |n: &str| {
        system
            .devs
            .iter()
            .find(|b| b.name == n)
            .map(|b| b.devno.clone())
    };
    let mounted = system.mounts.iter().filter(|m| under(&m.target)).any(|m| {
        stack
            .iter()
            .any(|n| devno(n).as_deref() == Some(m.devno.as_str()))
            || in_stack(&resolve_name(sys, &m.source))
    });
    mounted
        || system
            .pools
            .iter()
            .any(|p| p.is_ours(pools, &opts.target) && p.devices.iter().any(|d| in_stack(d)))
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
            let (Some(d), Some(t)) = (layout.disk(src), system.disk(tgt)) else {
                continue;
            };
            if unrecorded(layout, d, t, opts, sys) {
                let what = if d.table.is_some() {
                    "wrote this table (the source's table, as on the source's own disk as found"
                } else {
                    "wiped this disk (its source has no table, its content goes on the disk itself"
                };
                m.notes.push(
                    Note::new(NoteCode::PartitionRuns, format!("{src} -> {}: the partition stage runs: --erase and no record that load {what}; {}), so what's on it is let go of and wiped", t.path(), table_record(tgt)))
                        .with("disk", src)
                        .with("target", tgt),
                );
            }
        }
    }
    if !m.ours_busy.is_empty() && runs {
        let pools = layout_pools(layout);
        for name in m.ours_busy.clone() {
            let Some(t) = system.disk(&name) else {
                continue;
            };
            let src = m
                .pairs
                .iter()
                .find(|(_, x)| *x == name)
                .map(|(s, _)| s.clone())
                .unwrap_or_default();
            if loads_own(sys, system, opts, &pools, &name) {
                m.problems.push(
                    Problem::new(
                        ProblemCode::OursBusy,
                        format!(
                            "{src} -> {}: the partition stage would wipe it, but load's own is on it, from an earlier stage (mounted under {}, or a pool of the layout's imported there); `--only close` first lets go of it",
                            t.path(),
                            opts.target
                        ),
                    )
                    .with("disk", &src)
                    .with("target", &name),
                );
            } else if !t.hard_use.is_empty() {
                m.problems.push(
                    Problem::new(
                        ProblemCode::InUse,
                        format!(
                            "{src} -> {}: the target is in use ({}); refused even with --erase",
                            t.path(),
                            t.in_use.join("; ")
                        ),
                    )
                    .with("disk", &src)
                    .with("target", &name)
                    .with("uses", t.in_use.clone()),
                );
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
            let src = m
                .pairs
                .iter()
                .find(|(_, t)| *t == disk)
                .map(|(s, _)| s.clone())
                .unwrap_or_default();
            m.problems.push(
                Problem::new(ProblemCode::HeldUnreleasable, format!("{src} -> /dev/{disk}: the target is held, and what holds it can't be let go: {why}; refused even with --erase"))
                    .with("disk", &src)
                    .with("target", &disk)
                    .with("why", &why),
            );
        }
        for st in &r.steps {
            m.notes.push(
                Note::new(
                    NoteCode::Release,
                    format!(
                        "--erase {}: {} ({}) before it's wiped",
                        st.disks
                            .iter()
                            .map(|d| format!("/dev/{d}"))
                            .collect::<Vec<_>>()
                            .join(", "),
                        st.what(),
                        st.stops.join(", ")
                    ),
                )
                .with("targets", st.disks.clone()),
            );
        }
        m.release = r.steps;
    }
}

pub fn is_erased(opts: &Options, t: &TargetDisk) -> bool {
    opts.erase
        .iter()
        .any(|e| e == &t.path() || e == &t.b.name || resolve_tail(e) == t.b.name)
}

/// A disk's most stable /dev/disk/by-id link (holt-json.md, the plan's
/// `by_id`): its WWN's first, then one its serial names, then any other
/// by-id link of the whole disk.  Linux only (udev's); None elsewhere, or
/// where udev gives none.
pub fn by_id(t: &TargetDisk) -> Option<String> {
    let links: Vec<&String> =
        t.b.udev
            .links
            .iter()
            .filter(|l| l.starts_with("/dev/disk/by-id/") && !l.contains("-part"))
            .collect();
    let tail = |l: &str| l.trim_start_matches("/dev/disk/by-id/").to_string();
    if let Some(l) = links.iter().find(|l| tail(l).starts_with("wwn-")) {
        return Some(l.to_string());
    }
    if let Some(sn) = t.b.serial.as_deref().filter(|s| !s.is_empty()) {
        if let Some(l) = links.iter().find(|l| tail(l).contains(sn)) {
            return Some(l.to_string());
        }
    }
    links.first().map(|l| l.to_string())
}

/// What the probes find on T (holt-json.md, the plan's `contents`): per
/// signature its device, type and label, the device's size, and the space
/// used where the superblock says it without mounting (ext's and XFS's free
/// blocks, btrfs's bytes used), else null.  Read as scan read them: the
/// whole disk when it has no table, and each partition.  A device that
/// can't be opened here (off Linux, not root) has what scan found of it.
pub fn contents(sys: &Sys, t: &TargetDisk) -> Vec<crate::json::Value> {
    use crate::json::{Obj, Value};
    let mut out = Vec::new();
    let devs: Vec<&BlockDev> = if t.table.is_none() {
        std::iter::once(&t.b).chain(t.parts.iter()).collect()
    } else {
        t.parts.iter().collect()
    };
    for d in devs {
        let Some(src) = sys.open_dev(&d.dev_path()) else {
            // scan's own words for it: "vdb1: ext4 UUID=... LABEL=...".
            for sig in t
                .signatures
                .iter()
                .filter_map(|x| x.strip_prefix(&format!("{}: ", d.name)))
            {
                let sig = sig.strip_suffix(" (from udev)").unwrap_or(sig);
                let (rest, label) = match sig.split_once(" LABEL=") {
                    Some((r, l)) => (r, Some(l.to_string())),
                    None => (sig, None),
                };
                let kind = rest.split(' ').next().unwrap_or(rest);
                out.push(
                    Obj::new()
                        .set("dev", d.name.as_str())
                        .set("type", kind)
                        .set("label", label)
                        .set("size_bytes", d.size_bytes)
                        .set("used_bytes", Value::Null)
                        .build(),
                );
            }
            continue;
        };
        for c in crate::probe::probe_all(&src) {
            let used = used_bytes(&c.kind, &c.to_json());
            out.push(
                Obj::new()
                    .set("dev", d.name.as_str())
                    .set("type", c.kind.as_str())
                    .set("label", c.label.clone())
                    .set("size_bytes", d.size_bytes)
                    .set("used_bytes", used)
                    .build(),
            );
        }
    }
    out
}

/// The bytes a filesystem of KIND uses, from its probe's JSON J: ext's
/// and XFS's blocks less free blocks, times the block size; btrfs's
/// bytes_used.  None for another type, or a superblock whose figures
/// overflow a u64 (a damaged one: no real filesystem holds 2^64 bytes).
fn used_bytes(kind: &str, j: &crate::json::Value) -> Option<u64> {
    let u = |k: &str| j.get(k).and_then(crate::json::Value::as_u64);
    match kind {
        "ext2" | "ext3" | "ext4" | "xfs" => u("blocks")?
            .saturating_sub(u("free_blocks")?)
            .checked_mul(u("block_size")?),
        "btrfs" => u("bytes_used"),
        _ => None,
    }
}

/// Whether the mount at MOUNT_POINT is of something on T: one of its
/// devices or what's stacked on them (LUKS, LVM, md), as scan reads mounts.
fn holds_mount(sys: &Sys, system: &System, t: &TargetDisk, mount_point: &str) -> bool {
    let Some(m) = system.mounts.iter().find(|m| m.target == mount_point) else {
        return false;
    };
    let mut names: Vec<String> = std::iter::once(t.b.name.clone())
        .chain(t.parts.iter().map(|p| p.name.clone()))
        .collect();
    names.extend(super::release::stack(&system.devs, &t.b.name));
    let src = resolve_name(sys, &m.source);
    names.iter().any(|n| {
        let b = system.devs.iter().find(|b| b.name == *n);
        *n == src
            || b.is_some_and(|b| b.devno == m.devno || b.dm_name.as_deref() == Some(src.as_str()))
    }) || std::iter::once(&t.b)
        .chain(t.parts.iter())
        .any(|b| b.devno == m.devno)
}

/// The mount point a local --repo is under: the longest mount here that
/// holds it.  None for a repository that isn't a local path.
fn repo_mount(system: &System, opts: &Options) -> Option<String> {
    let repo = opts.repo.as_deref().filter(|r| r.starts_with('/'))?;
    system
        .mounts
        .iter()
        .map(|m| m.target.as_str())
        .filter(|t| {
            *t == "/" || repo == *t || repo.starts_with(&format!("{}/", t.trim_end_matches('/')))
        })
        .max_by_key(|t| t.len())
        .map(str::to_string)
}

/// Why T can never be a target of LAYOUT here, or None (holt-json.md, the
/// plan's `candidates[].refused`): the stick, the running system's disk,
/// the disk the local --repo is on, one mounted or in use as swap, a
/// read-only one, one whose sector size no source disk has.  A held disk,
/// one that isn't blank and one of another size aren't refused.
pub fn refusal(
    layout: &Layout,
    opts: &Options,
    system: &System,
    sys: &Sys,
    t: &TargetDisk,
) -> Option<(super::codes::RefusalCode, String)> {
    use super::codes::RefusalCode as R;
    let p = t.path();
    if t.boot_medium {
        return Some((
            R::BootDisk,
            format!(
                "{} is the disk this system booted from; never a target",
                t.b.name
            ),
        ));
    }
    if t.holds_root {
        return Some((
            R::RunningRoot,
            format!("{} holds the running system's /; never a target", t.b.name),
        ));
    }
    if let Some(mp) =
        repo_mount(system, opts).filter(|mp| mp != "/" && holds_mount(sys, system, t, mp))
    {
        return Some((
            R::BackupDrive,
            format!(
                "{p} holds {mp}, where the repository {} is; never a target",
                opts.repo.as_deref().unwrap_or_default()
            ),
        ));
    }
    if !t.hard_use.is_empty() {
        return Some((
            R::InUse,
            format!(
                "{p} is in use ({}); refused even with --erase",
                t.in_use.join("; ")
            ),
        ));
    }
    if t.b.read_only == Some(true) {
        return Some((R::ReadOnly, format!("{p} is read-only")));
    }
    let here = t.b.logical_block_size.max(512);
    let used: Vec<&Disk> = layout.disks.iter().filter(|d| d.used).collect();
    if !used.is_empty() && used.iter().all(|d| d.sector_size != here) {
        return Some((
            R::SectorSize,
            format!("{p}: logical sector size {here} here, and no source disk has it"),
        ));
    }
    None
}

/// Whether this session's partition stage wrote T for one of LAYOUT's used
/// disks (load_wrote, for any of them): a candidate that isn't a target
/// has no source of its own.
pub fn recorded_for_any(layout: &Layout, t: &TargetDisk, sys: &Sys) -> bool {
    layout
        .disks
        .iter()
        .filter(|d| d.used)
        .any(|d| load_wrote(layout, d, t, sys))
}

/// Never over the old system (holt-json.md, J19; under --json only): every
/// target that isn't blank must be named with --erase, or have this
/// session's partition record.  TargetDisk::is_ours is no exception: the
/// source machine's own disk as found carries the layout's identifiers
/// too.  A target the mapping refused already (not_blank, held, in_use)
/// isn't refused twice.
pub fn erase_gate(
    layout: &Layout,
    opts: &Options,
    system: &System,
    sys: &Sys,
    m: &Mapping,
) -> Vec<Problem> {
    let ours = layout_uuids(layout);
    let mut out = Vec::new();
    for (src, tgt) in &m.pairs {
        let (Some(d), Some(t)) = (layout.disk(src), system.disk(tgt)) else {
            continue;
        };
        if t.blank() || is_erased(opts, t) || load_wrote(layout, d, t, sys) {
            continue;
        }
        let refused = m.problems.iter().any(|p| {
            p.str_field("target") == Some(tgt.as_str())
                && matches!(
                    p.code,
                    ProblemCode::NotBlank | ProblemCode::Held | ProblemCode::InUse
                )
        });
        if refused {
            continue;
        }
        let same = same_identity(d, &t.b);
        let is_ours = t.is_ours(d, &ours);
        let what = if is_ours {
            "it carries this layout's own identifiers (an earlier restore's, or the source's own disk as found), and this session has no record that load wrote it"
        } else {
            "it isn't blank, and this session has no record that load wrote it"
        };
        out.push(
            Problem::new(
                ProblemCode::EraseRequired,
                format!(
                    "{src} -> {}: {what}; give --erase {} to wipe it",
                    t.path(),
                    t.path()
                ),
            )
            .with("disk", src)
            .with("target", tgt)
            .with("ours", is_ours)
            .with("same_identity", same),
        );
    }
    out
}

fn names(layout: &Layout) -> String {
    layout
        .disks
        .iter()
        .map(|d| d.name.as_str())
        .collect::<Vec<_>>()
        .join(", ")
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
    // A problem about this pair: its source disk and target node.
    let about = |code: ProblemCode, text: String| {
        Problem::new(code, text)
            .with("disk", &src.name)
            .with("target", &t.b.name)
    };
    if t.boot_medium {
        m.problems.push(about(
            ProblemCode::BootDisk,
            format!(
                "{who}: {} is the disk this system booted from; never a target",
                t.path()
            ),
        ));
        return;
    }
    if t.holds_root {
        m.problems.push(about(
            ProblemCode::RunningRoot,
            format!(
                "{who}: {} holds the running system's /; never a target",
                t.path()
            ),
        ));
        return;
    }
    // With --fit-largest the sizes are the fit's business (fit.rs), which
    // says what it changes and refuses what it can't fit.
    if t.b.size_bytes < src.size_bytes && opts.fit_largest.is_none() {
        m.problems.push(
            about(
                ProblemCode::TargetTooSmall,
                format!(
                    "{who}: the target is smaller ({} < {}); version 1 needs the same size or bigger, or --fit-largest PCT to fit the layout to it",
                    crate::report::human(t.b.size_bytes),
                    crate::report::human(src.size_bytes)
                ),
            )
            .with("target_bytes", t.b.size_bytes)
            .with("source_bytes", src.size_bytes),
        );
    }
    if t.b.logical_block_size.max(512) != src.sector_size {
        m.problems.push(
            about(ProblemCode::SectorSize, format!("{who}: logical sector size {} here, {} on the source; version 1 needs them equal", t.b.logical_block_size, src.sector_size))
                .with("here", t.b.logical_block_size)
                .with("source", src.sector_size),
        );
    }
    if t.b.read_only == Some(true) {
        m.problems.push(about(
            ProblemCode::ReadOnly,
            format!("{who}: the target is read-only"),
        ));
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
            m.problems.push(
                about(
                    ProblemCode::InUse,
                    format!(
                        "{who}: the target is in use ({}); refused even with --erase",
                        t.in_use.join("; ")
                    ),
                )
                .with("uses", t.in_use.clone()),
            );
            return;
        }
        if !erase {
            m.problems.push(
                about(ProblemCode::Held, format!("{who}: the target is held ({}); give --erase {} to let go of what holds it and wipe it", t.in_use.join("; "), t.path())).with("uses", t.in_use.clone()),
            );
            return;
        }
        // Held only: release.rs says whether it can be let go of, once every
        // target is known (a stack may span several).
        m.held.push(t.b.name.clone());
    }
    if !t.blank() && !is_ours && !erase {
        let what = if t.signatures.is_empty() {
            "a partition table".to_string()
        } else {
            t.signatures.join("; ")
        };
        m.problems.push(
            about(
                ProblemCode::NotBlank,
                format!(
                    "{who}: the target isn't blank ({what}); give --erase {} to wipe it",
                    t.path()
                ),
            )
            .with("contents", t.signatures.clone()),
        );
    }
    if t.b.size_bytes > src.size_bytes && opts.fit_largest.is_none() {
        m.notes.push(
            Note::new(
                NoteCode::TargetBigger,
                format!(
                    "{who}: the target is bigger; the extra {} stays unused",
                    crate::report::human(t.b.size_bytes - src.size_bytes)
                ),
            )
            .with("disk", &src.name)
            .with("target", &t.b.name)
            .with("bytes", t.b.size_bytes - src.size_bytes),
        );
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
    // --pools: the layout without the pools it leaves untouched, and their
    // disks; a --map of one of those, or a target holding one, refused.
    let only = (!opts.pools.is_empty()).then(|| layout.only_pools(&opts.pools));
    let src: &Layout = only.as_ref().map_or(layout, |o| &o.layout);
    let mut o2 = opts.clone();
    if let Some(o) = &only {
        o2.maps.retain(|(s, _)| !o.disks.contains(s));
    }
    let system = scan(sys, &o2, &layout_pools(src));
    let mut mapping = map_disks(src, &o2, &system, sys);
    // NetBSD: a disk that isn't a target with a target partition's wedge name.
    let clashes = crate::other::load::wedge_clashes(src, &mapping.pairs, sys);
    mapping.problems.extend(clashes);
    // A filesystem bigger than its device: the dump is inconsistent, and
    // nothing is planned to fit it (sizes.rs).  Checked on the source's
    // sizes, before --fit-largest changes them.
    mapping.problems.extend(super::sizes::problems(&src.v));
    if let Some(o) = &only {
        mapping.problems.extend(o.problems.iter().cloned());
        mapping.notes.extend(o.notes.iter().cloned());
        mapping
            .problems
            .extend(left_out_pool_problems(opts, o, &mapping, &system));
    }
    let mut fit = Fit::default();
    if let Some(pct) = opts.fit_largest {
        let targets: Vec<fit::Target> = mapping
            .pairs
            .iter()
            .filter_map(|(src, t)| {
                system.disk(t).map(|t| fit::Target {
                    disk: src.clone(),
                    path: t.path(),
                    size_bytes: t.b.size_bytes,
                })
            })
            .collect();
        fit = fit::fit(&src.v, &targets, pct);
        mapping.problems.extend(fit.problems.iter().cloned());
        mapping.notes.extend(fit.notes.iter().cloned());
        for d in &fit.new_end {
            if !mapping.bigger.contains(d) {
                mapping.bigger.push(d.clone());
            }
        }
    }
    let mut out: Cow<'a, Layout> = match &only {
        Some(o) => Cow::Owned(o.layout.clone()),
        None => Cow::Borrowed(layout),
    };
    if let Some(v) = fit.v.clone() {
        match Layout::from_json(v) {
            Ok(l) => out = Cow::Owned(l),
            Err(e) => mapping.problems.push(format!(
                "--fit-largest: the layout it fits isn't one load can read back: {e}"
            )),
        }
    }
    for p in fitted_already(src, &out, &system, &o2, &mapping) {
        mapping.problems.push(p);
    }
    release_holders(&out, &o2, &system, sys, &mut mapping);
    mapping.records = records(&out, &system, &mapping, sys);
    Prepared {
        system,
        mapping,
        layout: out,
        fit,
    }
}

/// --pools' refusals: a --map of a disk of a pool it leaves untouched, and
/// a target disk here that holds one of those pools (its vdev's label has
/// the pool's GUID): load would write over a pool it was told to leave.
pub fn left_out_pool_problems(
    opts: &Options,
    only: &crate::load::layout::OnlyPools,
    m: &Mapping,
    system: &System,
) -> Vec<Problem> {
    let mut out = Vec::new();
    for (src, dst) in &opts.maps {
        if only.disks.contains(src) {
            // Which of the left-out pools isn't known here: null.
            out.push(Problem::new(ProblemCode::PoolLeftOut, format!("--map {src}={dst}: {src} holds a pool --pools leaves untouched, so it has no target")).with("disk", src).with("pool", crate::json::Value::Null));
        }
    }
    for (src, tgt) in &m.pairs {
        let Some(t) = system.disk(tgt) else { continue };
        for (pool, guid) in &only.guids {
            if t.uuids.iter().any(|u| u == guid) {
                out.push(
                    Problem::new(ProblemCode::PoolLeftOut, format!("{src} -> {}: the target holds pool {pool}, which --pools leaves untouched; refused", t.path()))
                        .with("disk", src)
                        .with("target", tgt)
                        .with("pool", pool),
                );
            }
        }
    }
    out
}

/// A table an earlier stage wrote with another --fit-largest: without the
/// same one again, every later stage would read the disk as the source's and
/// write over what's on it.  `planned` is the layout this run would write,
/// fitted or not; a target that already holds it has nothing wrong with it.
fn fitted_already(
    src: &Layout,
    planned: &Layout,
    system: &System,
    opts: &Options,
    m: &Mapping,
) -> Vec<Problem> {
    let mut out = Vec::new();
    for (name, tgt) in &m.pairs {
        let (Some(d), Some(plan), Some(t)) = (src.disk(name), planned.disk(name), system.disk(tgt))
        else {
            continue;
        };
        // --erase wipes the table, which is what the message below asks for.
        if is_erased(opts, t) {
            continue;
        }
        let (Some(want), Some(have)) = (
            d.table.as_ref().and_then(|x| x.id.as_ref()),
            t.table.as_ref(),
        ) else {
            continue;
        };
        if !table_id(have).eq_ignore_ascii_case(want) {
            continue;
        }
        // What this run plans is already there, by the same check status
        // makes of the partition stage: the stages go on from it.
        let ext = have.extents();
        if plan.parts.iter().all(|q| {
            ext.iter()
                .any(|&(n, s, len)| n == q.number && s == q.start_lba && len == q.sectors)
        }) {
            continue;
        }
        let target = fit::Target {
            disk: name.clone(),
            path: t.path(),
            size_bytes: t.b.size_bytes,
        };
        let Some((n, by)) = fit::wrote(&src.v, &target, &ext) else {
            continue;
        };
        let part = d
            .parts
            .iter()
            .find(|q| q.number == n)
            .map(|q| q.name.clone())
            .unwrap_or_else(|| format!("partition {n}"));
        let text = format!(
            "{name} -> {}: the table there is this layout's, fitted to the disk ({part} is {} {} there); run the stages with the --fit-largest PCT that wrote it, or --erase {} to start again",
            t.path(),
            crate::report::human(by.unsigned_abs() * d.sector_size),
            if by < 0 { "smaller" } else { "bigger" },
            t.path()
        );
        // bytes: how much the partition differs, below 0 smaller.
        out.push(
            Problem::new(ProblemCode::FittedOtherPct, text)
                .with("disk", name)
                .with("target", tgt)
                .with("partition", &part)
                .with("bytes", by * d.sector_size as i64),
        );
    }
    out
}

fn resolve_tail(spec: &str) -> &str {
    spec.rsplit('/').next().unwrap_or(spec)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::load::layout::{Layout, sample};
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
        t.disk("vdb", "253:16", &[], 512)
            .file("/sys/block/vdb/size", format!("{}\n", 33554432));
        t.disk("vdc", "253:32", &[], 512)
            .file("/sys/block/vdc/size", format!("{}\n", 16777216));
        t.disk("sda", "8:0", &[], 512)
            .file("/sys/block/sda/size", "30000000\n");
        t.partition("sda", "sda3", "8:3", 3, 2048, 65536, &[]);
        t.file(
            "/run/udev/data/b8:3",
            "E:ID_FS_TYPE=vfat\nE:ID_FS_LABEL=BACKOTTER\n",
        );
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

    /// RustAudit1 HD2-07: a damaged superblock's figures (blocks about
    /// 2^60, 64 KiB blocks) give no used bytes, not a panic or a wrapped
    /// number.
    #[test]
    fn used_bytes_overflow_is_null() {
        let j = |b: u64, f: u64, bs: u64| {
            crate::json::parse(&format!(
                r#"{{"blocks": {b}, "free_blocks": {f}, "block_size": {bs}}}"#
            ))
            .unwrap()
        };
        assert_eq!(
            used_bytes("ext4", &j(1 << 20, 1 << 18, 4096)),
            Some(((1 << 20) - (1 << 18)) * 4096)
        );
        assert_eq!(used_bytes("xfs", &j(10, 20, 4096)), Some(0));
        assert_eq!(used_bytes("ext4", &j(1 << 60, 0, 65536)), None);
        assert_eq!(used_bytes("xfs", &j(u64::MAX, 1, 4096)), None);
        assert_eq!(
            used_bytes(
                "btrfs",
                &crate::json::parse(r#"{"bytes_used": 12345}"#).unwrap()
            ),
            Some(12345)
        );
        assert_eq!(used_bytes("vfat", &j(1, 0, 512)), None);
        assert_eq!(
            used_bytes(
                "ext4",
                &crate::json::parse(r#"{"blocks": 10, "block_size": 4096}"#).unwrap()
            ),
            None
        );
    }

    /// RustAudit1 HD2-04: off Linux the partition record names the target
    /// by the scan's serial and WWID (there is no sysfs), so a disk swapped
    /// in under the same node and size has another record and load_wrote
    /// doesn't take it for the one load wrote; on Linux, sysfs's, as ever.
    #[test]
    fn the_record_names_the_scans_serial_off_linux() {
        use crate::blockdev::BlockDev;
        let t = tree();
        t.file("/sys/block/vdb/device/serial", "SYSFS-1\n");
        let s = sys(&t);
        let l = layout();
        let d = l.disk("vda").unwrap();
        let base = scan(&s, &opts(&["--map", "vda=vdb"]), &[])
            .disks
            .into_iter()
            .find(|d| d.b.name == "vdb")
            .unwrap();
        let with = |serial: &str, wwid: Option<&str>| TargetDisk {
            b: BlockDev {
                serial: Some(serial.into()),
                wwid: wwid.map(str::to_string),
                ..base.b.clone()
            },
            parts: base.parts.clone(),
            table: None,
            signatures: vec![],
            uuids: vec![],
            in_use: vec![],
            hard_use: vec![],
            boot_medium: false,
            holds_root: false,
        };
        let (old, new) = (
            with("OLD-1", None),
            with("NEW-2", Some("naa.5000c500a1b2c3d4")),
        );
        let bsd = Some(crate::other::Family::FreeBsd);
        assert_eq!(
            identity_on(bsd, &s, &old),
            ("OLD-1".to_string(), String::new())
        );
        assert_eq!(
            identity_on(bsd, &s, &new),
            ("NEW-2".to_string(), "naa.5000c500a1b2c3d4".to_string())
        );
        let rec = |x: &TargetDisk| record_text(&l, d, x, &identity_on(bsd, &s, x));
        assert!(
            rec(&old).contains(&format!(
                "\ntarget vdb size={} serial=OLD-1 wwid=\n",
                base.b.size_bytes
            )),
            "{}",
            rec(&old)
        );
        assert_ne!(rec(&old), rec(&new));
        // Linux: sysfs's, whatever the scan found.
        assert_eq!(
            identity_on(None, &s, &old),
            ("SYSFS-1".to_string(), String::new())
        );
        assert_eq!(
            table_record_text(&l, d, &old, &s),
            record_text(&l, d, &old, &kernel_identity(&s, &old))
        );
        if crate::other::Family::current().is_none() {
            assert_eq!(
                table_record_text(&l, d, &old, &s),
                table_record_text(&l, d, &new, &s)
            );
        }
        // OpenBSD and NetBSD: the scan's serial is the disklabel's DUID,
        // which partition writes; the record leaves it out (RT10j review),
        // so the target read back after partition has the record written
        // for it before.
        for fam in [crate::other::Family::OpenBsd, crate::other::Family::NetBsd] {
            let duid = |x: &str| with(x, None);
            assert_eq!(
                identity_on(Some(fam), &s, &duid("52fcb0b7cd1d05e5")),
                (String::new(), String::new()),
                "{fam:?}"
            );
            assert_eq!(
                identity_on(Some(fam), &s, &new),
                (String::new(), "naa.5000c500a1b2c3d4".to_string()),
                "{fam:?}"
            );
            let rec = |x: &TargetDisk| record_text(&l, d, x, &identity_on(Some(fam), &s, x));
            assert_eq!(
                rec(&duid("0000000000000000")),
                rec(&duid("52fcb0b7cd1d05e5")),
                "{fam:?}"
            );
            assert!(
                rec(&old).contains(&format!(
                    "\ntarget vdb size={} serial= wwid=\n",
                    base.b.size_bytes
                )),
                "{}",
                rec(&old)
            );
        }
    }

    /// A pool of the layout's name imported here keeps it only when every
    /// vdev is on the targets: exactly (sdaa1 isn't sda's), or through the
    /// dm/md devices over them.
    #[test]
    fn a_same_named_pool_off_the_targets_is_renamed() {
        use crate::blockdev::{BlockDev, Kind};
        let t = tree();
        let s = sys(&t);
        let base = scan(&s, &opts(&["--map", "vda=vdb"]), &[])
            .disks
            .into_iter()
            .find(|d| d.b.name == "vdb")
            .unwrap();
        let part = |n: &str, d: &str| BlockDev {
            name: n.into(),
            kind: Kind::Partition,
            parent: Some(d.into()),
            ..BlockDev::default()
        };
        let disk = |n: &str, p: &str| TargetDisk {
            b: BlockDev {
                name: n.into(),
                ..base.b.clone()
            },
            parts: vec![part(p, n)],
            table: None,
            signatures: vec![],
            uuids: vec![],
            in_use: vec![],
            hard_use: vec![],
            boot_medium: false,
            holds_root: false,
        };
        let (sda, sdaa) = (disk("sda", "sda2"), disk("sdaa", "sdaa1"));
        let dm = |n: &str, dm: &str, slaves: &[&str]| BlockDev {
            name: n.into(),
            kind: Kind::Dm,
            dm_name: Some(dm.into()),
            slaves: slaves.iter().map(|x| x.to_string()).collect(),
            ..BlockDev::default()
        };
        let devs = vec![
            sda.b.clone(),
            sda.parts[0].clone(),
            sdaa.b.clone(),
            sdaa.parts[0].clone(),
            dm("dm-0", "luks-a", &["sda2"]),
            dm("dm-1", "luks-b", &["sda2", "sdaa1"]),
        ];
        let j = sample::bios_mbr().replace(
            "\"zfs\": {\"pools_from_labels\": [], \"tools\": null}",
            "\"zfs\": {\"pools_from_labels\": [], \"tools\": {\"imported_pools\": [{\"name\": \"rpool\", \"vdevs\": {}}], \"datasets\": []}}",
        );
        let l = Layout::from_json(crate::json::parse(&j).unwrap()).unwrap();
        let o = opts(&["--map", "vda=sda"]);
        let renamed = |devices: &[&str]| {
            let pools = vec![ImportedPool {
                name: "rpool".into(),
                altroot: "-".into(),
                devices: devices.iter().map(|x| x.to_string()).collect(),
            }];
            let system = System {
                disks: vec![disk("sda", "sda2"), disk("sdaa", "sdaa1")],
                mounts: vec![],
                pools,
                pvs: vec![],
                devs: devs.clone(),
                swaps: vec![],
            };
            let m = map_disks(&l, &o, &system, &s);
            assert_eq!(m.pairs, vec![("vda".to_string(), "sda".to_string())]);
            let r = m.pool_names == vec![("rpool".to_string(), temp_pool_name("rpool"))];
            assert_eq!(
                r,
                m.notes
                    .iter()
                    .any(|n| n.contains("a pool of that name is imported here")),
                "{:?}",
                m.notes
            );
            assert!(r || m.pool_names.is_empty(), "{:?}", m.pool_names);
            r
        };
        assert!(renamed(&["sdaa1"]), "sdaa1 isn't on sda");
        assert!(!renamed(&["sda2"]));
        assert!(!renamed(&["dm-0"]), "dm-0 is over sda2");
        assert!(
            !renamed(&["luks-a"]),
            "mdev: /dev/mapper/luks-a resolves to its dm name"
        );
        assert!(renamed(&["dm-1"]), "dm-1 is over sdaa1 too");
        assert!(
            renamed(&[]),
            "no vdevs read: not shown to be on the targets"
        );
    }

    /// illumos' zpool names a vdev by slice (c3t0d0s1); scan names its
    /// disks' EFI label entries so (omnios58's dump: c3t0d0s0, s1, s8).
    #[test]
    fn an_illumos_slice_vdev_is_on_its_disk() {
        use crate::blockdev::{BlockDev, Kind};
        let disk = |n: &str| TargetDisk {
            b: BlockDev {
                name: n.into(),
                kind: Kind::Disk,
                ..BlockDev::default()
            },
            parts: [1, 2, 9]
                .iter()
                .map(|&i| BlockDev {
                    name: crate::other::illumos::slice_name(n, i),
                    kind: Kind::Partition,
                    parent: Some(n.into()),
                    ..BlockDev::default()
                })
                .collect(),
            table: None,
            signatures: vec![],
            uuids: vec![],
            in_use: vec![],
            hard_use: vec![],
            boot_medium: false,
            holds_root: false,
        };
        let system = System {
            disks: vec![disk("c3t0d0"), disk("c3t0d01")],
            mounts: vec![],
            pools: vec![],
            pvs: vec![],
            devs: vec![],
            swaps: vec![],
        };
        for (dev, want) in [
            ("c3t0d0s1", true),
            ("c3t0d0s8", true),
            ("c3t0d0", true),
            ("c3t0d0s3", false),
            ("c3t0d01s1", false),
            ("c3t0d0s10", false),
        ] {
            assert_eq!(vdev_on_targets(&system, &["c3t0d0"], dev), want, "{dev}");
        }
    }

    #[test]
    fn zpool_status_devices() {
        let text = "  pool: rpool\n state: ONLINE\nconfig:\n\n\tNAME         STATE     READ WRITE CKSUM\n\trpool        ONLINE       0     0     0\n\t  /dev/vda3  ONLINE       0     0     0\n\nerrors: No known data errors\n\n  pool: tank\nconfig:\n\ttank  ONLINE\n\t  mirror-0  ONLINE\n\t    /dev/disk/by-id/ata-A-part1  ONLINE\n\t    /dev/sdb1  ONLINE\n";
        assert_eq!(
            parse_zpool_status(text),
            vec![
                ("rpool".to_string(), vec!["/dev/vda3".to_string()]),
                (
                    "tank".to_string(),
                    vec![
                        "/dev/disk/by-id/ata-A-part1".to_string(),
                        "/dev/sdb1".to_string()
                    ]
                )
            ]
        );
        let p = ImportedPool {
            name: "rpool".into(),
            altroot: "/mnt/bko-target".into(),
            devices: vec![],
        };
        assert!(p.is_ours(&["rpool".into()], "/mnt/bko-target"));
        assert!(!p.is_ours(&["rpool".into()], "/mnt/elsewhere"));
        assert!(
            !ImportedPool {
                altroot: "-".into(),
                ..p
            }
            .is_ours(&["rpool".into()], "/mnt/bko-target")
        );
    }

    #[test]
    fn fit_largest_takes_a_smaller_target() {
        let t = tree();
        let s = sys(&t);
        let l = layout();
        // vdc is 8 GiB, half of the source's 16: too small to fit.
        let o = opts(&["--map", "vda=vdc", "--fit-largest", "50"]);
        let p = prepare(&l, &o, &s);
        assert_eq!(
            p.mapping.pairs,
            vec![("vda".to_string(), "vdc".to_string())]
        );
        assert!(
            p.mapping
                .problems
                .iter()
                .any(|x| x.contains("more than 50%")),
            "{:?}",
            p.mapping.problems
        );
        // Without the flag it's the old refusal, which names the flag.
        let o = opts(&["--map", "vda=vdc"]);
        let p = prepare(&l, &o, &s);
        assert!(
            p.mapping
                .problems
                .iter()
                .any(|x| x.contains("the target is smaller") && x.contains("--fit-largest PCT")),
            "{:?}",
            p.mapping.problems
        );
        // The same size: the flag changes nothing, and says nothing.
        let o = opts(&["--map", "vda=vdb", "--fit-largest", "10"]);
        let p = prepare(&l, &o, &s);
        assert!(
            p.mapping.problems.is_empty() && p.mapping.notes.is_empty(),
            "{:?} {:?}",
            p.mapping.problems,
            p.mapping.notes
        );
        assert!(!p.fit.changed() && p.fit.disks.is_empty() && p.mapping.bigger.is_empty());
        assert!(matches!(p.layout, Cow::Borrowed(_)));
    }

    #[test]
    fn refusals() {
        let t = tree();
        let s = sys(&t);
        let p = |a: &[&str]| {
            let o = opts(a);
            map(&layout(), &o, &scan(&s, &o, &[]), &s)
                .problems
                .join("\n")
        };
        assert!(p(&[]).contains("has no target"));
        assert!(p(&["--map", "vda=vdc"]).contains("smaller"));
        assert!(p(&["--map", "vda=sda", "--erase", "sda"]).contains("booted from"));
        assert!(p(&["--map", "vdz=vdb"]).contains("no disk vdz"));
        assert!(p(&["--map", "vda=vdq"]).contains("no whole disk vdq"));
        assert!(p(&["--map", "vda=vdb", "--erase", "vdc"]).contains("isn't a target"));
    }

    /// Every problem and note map_disks, check_target, release_holders and
    /// prepare code: the code, the subject fields, and the text as before.
    #[test]
    fn problems_carry_their_codes() {
        use crate::load::codes::{NoteCode, ProblemCode as C};
        let t = tree();
        let s = sys(&t);
        let m = |a: &[&str]| {
            let o = opts(a);
            map(&layout(), &o, &scan(&s, &o, &[]), &s)
        };
        let one = |m: &Mapping, code: C| -> Problem {
            let p: Vec<&Problem> = m.problems.iter().filter(|p| p.code == code).collect();
            assert_eq!(
                p.len(),
                1,
                "{code:?} in {:?}",
                m.problems
                    .iter()
                    .map(|p| (p.code, p.text.clone()))
                    .collect::<Vec<_>>()
            );
            p[0].clone()
        };
        let f = |p: &Problem, k: &str| {
            p.field(k)
                .map(crate::json::Value::compact)
                .unwrap_or_default()
        };
        let p = one(&m(&[]), C::NotMapped);
        assert!(
            p.starts_with("source disk vda (16 GiB) has no target: give --map vda=DISK"),
            "{p}"
        );
        assert_eq!(p.str_field("disk"), Some("vda"));
        let p = one(&m(&["--map", "vda=vdc"]), C::TargetTooSmall);
        assert_eq!(
            p.text,
            "vda -> /dev/vdc: the target is smaller (8 GiB < 16 GiB); version 1 needs the same size or bigger, or --fit-largest PCT to fit the layout to it"
        );
        assert_eq!(
            (p.str_field("disk"), p.str_field("target")),
            (Some("vda"), Some("vdc"))
        );
        assert_eq!(
            (f(&p, "target_bytes"), f(&p, "source_bytes")),
            ((8u64 << 30).to_string(), (16u64 << 30).to_string())
        );
        let p = one(&m(&["--map", "vda=sda", "--erase", "sda"]), C::BootDisk);
        assert_eq!(
            p.text,
            "vda -> /dev/sda: /dev/sda is the disk this system booted from; never a target"
        );
        let p = one(&m(&["--map", "vdz=vdb"]), C::MapUnknownSource);
        assert_eq!(
            (p.text.as_str(), p.str_field("disk")),
            (
                "--map vdz=...: the layout has no disk vdz (it has vda)",
                Some("vdz")
            )
        );
        let p = one(&m(&["--map", "vda=vdq"]), C::NoSuchDisk);
        assert_eq!(
            (p.text.as_str(), p.str_field("disk"), p.str_field("target")),
            (
                "--map vda=vdq: no whole disk vdq here",
                Some("vda"),
                Some("vdq")
            )
        );
        let p = one(
            &m(&["--map", "vda=vdb", "--erase", "vdc"]),
            C::EraseNotTarget,
        );
        assert_eq!(
            (p.text.as_str(), p.str_field("target")),
            (
                "--erase vdc: that isn't a target of this layout, so it isn't erased",
                Some("vdc")
            )
        );
        // A path: the text as given, the target its disk's name.
        let p = one(
            &m(&["--map", "vda=vdb", "--erase", "/dev/vdc"]),
            C::EraseNotTarget,
        );
        assert_eq!(
            (p.text.as_str(), p.str_field("target")),
            (
                "--erase /dev/vdc: that isn't a target of this layout, so it isn't erased",
                Some("vdc")
            )
        );
        // The rescue system's /, a 4 KiB-sector disk, a read-only one, a
        // bigger one and one with someone else's table.
        t.disk("vde", "253:64", &[], 4096).file(
            "/sys/block/vde/size",
            format!(
                "{}
",
                33554432
            ),
        );
        t.disk("vdf", "253:80", &[], 512)
            .file(
                "/sys/block/vdf/size",
                format!(
                    "{}
",
                    33554432
                ),
            )
            .file(
                "/sys/block/vdf/ro",
                "1
",
            );
        t.disk("vdg", "253:96", &[], 512).file(
            "/sys/block/vdg/size",
            format!(
                "{}
",
                2 * 33554432
            ),
        );
        let mut other = vec![0u8; 1 << 20];
        other[440..444].copy_from_slice(&0xdeadbeefu32.to_le_bytes());
        other[446 + 4] = 0x83;
        other[446 + 8..446 + 12].copy_from_slice(&2048u32.to_le_bytes());
        other[446 + 12..446 + 16].copy_from_slice(&1024u32.to_le_bytes());
        other[510] = 0x55;
        other[511] = 0xaa;
        t.disk("vdh", "253:112", &other, 512).file(
            "/sys/block/vdh/size",
            format!(
                "{}
",
                33554432
            ),
        );
        t.disk("vdi", "253:128", &[], 512).file(
            "/sys/block/vdi/size",
            format!(
                "{}
",
                33554432
            ),
        );
        t.file(
            "/proc/self/mountinfo",
            "30 1 253:128 / / rw - ext4 /dev/vdi rw
",
        );
        let s = Sys::new(t.root(), Box::new(FakeRunner::default()), true);
        let m = |a: &[&str]| {
            let o = opts(a);
            map(&layout(), &o, &scan(&s, &o, &[]), &s)
        };
        let p = one(&m(&["--map", "vda=vde"]), C::SectorSize);
        assert_eq!(
            p.text,
            "vda -> /dev/vde: logical sector size 4096 here, 512 on the source; version 1 needs them equal"
        );
        assert_eq!(
            (f(&p, "here"), f(&p, "source")),
            ("4096".to_string(), "512".to_string())
        );
        assert_eq!(
            one(&m(&["--map", "vda=vdf"]), C::ReadOnly).text,
            "vda -> /dev/vdf: the target is read-only"
        );
        let p = one(&m(&["--map", "vda=vdh"]), C::NotBlank);
        assert!(
            p.text
                .starts_with("vda -> /dev/vdh: the target isn't blank ("),
            "{p}"
        );
        assert!(
            p.text.ends_with("); give --erase /dev/vdh to wipe it"),
            "{p}"
        );
        assert_eq!(p.str_field("target"), Some("vdh"));
        assert!(
            p.field("contents")
                .and_then(crate::json::Value::as_array)
                .is_some()
        );
        assert!(
            m(&["--map", "vda=vdh", "--erase", "vdh"])
                .problems
                .iter()
                .all(|p| p.code != C::NotBlank)
        );
        assert_eq!(
            one(&m(&["--map", "vda=vdi", "--erase", "vdi"]), C::RunningRoot).text,
            "vda -> /dev/vdi: /dev/vdi holds the running system's /; never a target"
        );
        let big = m(&["--map", "vda=vdg"]);
        assert!(big.problems.is_empty(), "{:?}", big.problems);
        let n = big
            .notes
            .iter()
            .find(|n| n.code == NoteCode::TargetBigger)
            .unwrap();
        assert_eq!(
            n.text,
            "vda -> /dev/vdg: the target is bigger; the extra 16 GiB stays unused"
        );
        assert_eq!(
            (
                n.str_field("disk"),
                n.str_field("target"),
                n.field("bytes").and_then(crate::json::Value::as_u64)
            ),
            (Some("vda"), Some("vdg"), Some(16 << 30))
        );
        // The source empty, a note of its own.
        let empty = sample::bios_mbr().replacen(r#""disks": ["#, r#""disks": [{"name": "vdy", "kind": "disk", "size_bytes": 1048576, "logical_sector_size": 512, "partition_table": null, "partitions": [], "content": null},"#, 1);
        let el = Layout::from_json(crate::json::parse(&empty).unwrap()).unwrap();
        let o = opts(&["--map", "vda=vdb"]);
        let em = map(&el, &o, &scan(&s, &o, &[]), &s);
        let n = em
            .notes
            .iter()
            .find(|n| n.contains("holds nothing"))
            .unwrap_or_else(|| panic!("{:?}", em.notes));
        assert_eq!(
            (n.code, n.str_field("disk")),
            (NoteCode::SourceEmpty, Some("vdy"))
        );
        // --fit-largest past its percentage: fit's own code, through prepare.
        let o = opts(&["--map", "vda=vdc", "--fit-largest", "50"]);
        let l = layout();
        let pr = prepare(&l, &o, &s);
        let p = pr
            .mapping
            .problems
            .iter()
            .find(|p| p.code == C::FitOverPct)
            .unwrap_or_else(|| panic!("{:?}", pr.mapping.problems));
        assert!(
            p.text.contains("more than 50%")
                && p.text.starts_with("vda -> /dev/vdc: --fit-largest 50%: "),
            "{p}"
        );
        assert_eq!(
            (
                p.str_field("disk"),
                p.str_field("target"),
                p.field("given_pct").and_then(crate::json::Value::as_u64)
            ),
            (Some("vda"), Some("vdc"), Some(50))
        );
        assert!(
            p.field("needed_pct")
                .and_then(crate::json::Value::as_u64)
                .is_some_and(|n| n > 50)
        );
    }

    /// The held, unreleasable, in-use and release codes, on the old vg0.
    #[test]
    fn held_targets_carry_their_codes() {
        use crate::load::codes::{NoteCode, ProblemCode as C};
        let t = tree();
        old_vg0(&t);
        let s = Sys::new(t.root(), Box::new(FakeRunner::default()), true);
        let m = |a: &[&str]| {
            let o = opts(a);
            map(&layout(), &o, &scan(&s, &o, &[]), &s)
        };
        let held = m(&["--map", "vda=vdb"]);
        let p = held.problems.iter().find(|p| p.code == C::Held).unwrap();
        assert_eq!(
            p.text,
            "vda -> /dev/vdb: the target is held (vdb2 is held by dm-0, dm-1); give --erase /dev/vdb to let go of what holds it and wipe it"
        );
        assert_eq!(
            p.field("uses").map(crate::json::Value::compact).as_deref(),
            Some(r#"["vdb2 is held by dm-0, dm-1"]"#)
        );
        let m2 = m(&["--map", "vda=vdb", "--erase", "/dev/vdb"]);
        let n = m2
            .notes
            .iter()
            .find(|n| n.code == NoteCode::Release)
            .unwrap();
        assert_eq!(
            n.text,
            "--erase /dev/vdb: volume group vg0 deactivated (dm-0 (vg0-root), dm-1 (vg0-swap)) before it's wiped"
        );
        assert_eq!(
            n.field("targets")
                .map(crate::json::Value::compact)
                .as_deref(),
            Some(r#"["vdb"]"#)
        );
        t.file(
            "/proc/swaps",
            "Filename	Type	Size	Used	Priority
/dev/dm-1                               partition	1048572	0	-2
",
        );
        let m3 = m(&["--map", "vda=vdb", "--erase", "/dev/vdb"]);
        let p = m3
            .problems
            .iter()
            .find(|p| p.code == C::HeldUnreleasable)
            .unwrap_or_else(|| panic!("{:?}", m3.problems));
        assert_eq!(
            p.text,
            "vda -> /dev/vdb: the target is held, and what holds it can't be let go: dm-1 (vg0-swap) is in use as swap; refused even with --erase"
        );
        assert_eq!(
            (
                p.str_field("disk"),
                p.str_field("target"),
                p.str_field("why")
            ),
            (
                Some("vda"),
                Some("vdb"),
                Some("dm-1 (vg0-swap) is in use as swap")
            )
        );
        t.file(
            "/proc/swaps",
            "Filename	Type	Size	Used	Priority
",
        );
        t.file(
            "/proc/self/mountinfo",
            "30 1 253:18 / /mnt/old rw - ext4 /dev/vdb2 rw
",
        );
        let m4 = m(&["--map", "vda=vdb", "--erase", "/dev/vdb"]);
        let p = m4
            .problems
            .iter()
            .find(|p| p.code == C::InUse)
            .unwrap_or_else(|| panic!("{:?}", m4.problems));
        assert!(p.text.ends_with("refused even with --erase"), "{p}");
        assert!(p.field("uses").is_some());
    }

    #[test]
    fn a_same_named_volume_group_elsewhere_is_refused() {
        let t = tree();
        // vdd: a whole-disk PV of the volume group vg0.
        t.disk(
            "vdd",
            "253:48",
            &crate::probe::lvm::sample::pv(512, 1 << 20),
            512,
        );
        // Privileged: scan reads the devices.
        let s = Sys::new(t.root(), Box::new(FakeRunner::default()), true);
        let with = |vg: &str| {
            let j = sample::bios_mbr().replace(
                r#""lvm": {"volume_groups": []}"#,
                &format!(r#""lvm": {{"volume_groups": [{{"name": "{vg}"}}]}}"#),
            );
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
        let vm = map(&with("vg0"), &o, &system, &s);
        let dup = vm
            .problems
            .iter()
            .find(|p| p.code == ProblemCode::DuplicateVg)
            .unwrap();
        assert_eq!(
            (dup.str_field("vg"), dup.str_field("dev")),
            (Some("vg0"), Some("vdd"))
        );
        let p = vm.problems.join("\n");
        assert!(
            p.contains("a volume group named vg0 is on vdd (vdd), which isn't a target"),
            "{p}"
        );
        assert!(map(&with("ubuntu-vg"), &o, &system, &s).problems.is_empty());
        // Renamed, but with the same UUIDs (vgrename keeps them).
        let ids = |vg: &str, id: &str, pv: &str| {
            let j = sample::bios_mbr().replace(
                r#""lvm": {"volume_groups": []}"#,
                &format!(r#""lvm": {{"volume_groups": [{{"name": "{vg}", "id": "{id}", "physical_volumes": [{{"id": "{pv}"}}]}}]}}"#),
            );
            Layout::from_json(crate::json::parse(&j).unwrap()).unwrap()
        };
        let p = map(
            &ids("ubuntu-vg", "Yb3kFa-0Pz1-AbCd-EfGh-IjKl-MnOp-QrStUv", "x"),
            &o,
            &system,
            &s,
        )
        .problems
        .join("\n");
        assert!(
            p.contains("volume group ubuntu-vg's UUID (as vg0) is on vdd"),
            "{p}"
        );
        let p = map(
            &ids("ubuntu-vg", "y", "Hc7n2m-aaaa-bbbb-cccc-dddd-eeee-ffffff"),
            &o,
            &system,
            &s,
        )
        .problems
        .join("\n");
        assert!(
            p.contains("the UUID of one of ubuntu-vg's PVs (in vg0) is on vdd"),
            "{p}"
        );
        assert!(
            map(&ids("ubuntu-vg", "y", "z"), &o, &system, &s)
                .problems
                .is_empty()
        );
        // On a target, it's the old system's, which load replaces.
        let o = opts(&["--map", "vda=vdd", "--erase", "vdd"]);
        let p = map(&with("vg0"), &o, &scan(&s, &o, &[]), &s)
            .problems
            .join("\n");
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
                .file(
                    &format!("/sys/block/{dm}/dm/uuid"),
                    format!("LVM-{vg}{}\n", i.to_string().repeat(32)),
                )
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
        assert_eq!(
            m.release[0].action,
            super::super::release::Action::Lvm {
                vg: "vg0".into(),
                pvs: vec!["/dev/vdb2".into()]
            }
        );
        assert!(m.notes.iter().any(|n| n == "--erase /dev/vdb: volume group vg0 deactivated (dm-0 (vg0-root), dm-1 (vg0-swap)) before it's wiped"), "{:?}", m.notes);
        let b = super::super::commands::build(&layout(), &o, &m);
        let text: String = b
            .stage(Stage::Partition)
            .iter()
            .map(|c| c.shell())
            .collect();
        let at = |x: &str| {
            text.find(x)
                .unwrap_or_else(|| panic!("no {x:?} in\n{text}"))
        };
        assert!(
            at("# volume group vg0 deactivated (dm-0 (vg0-root), dm-1 (vg0-swap))\n")
                < at("vgchange --devicesfile '' --devices /dev/vdb2 --activate n vg0"),
            "{text}"
        );
        assert!(
            text.starts_with("# --erase /dev/vdb: what this system started on it"),
            "{text}"
        );
        assert!(
            at("vgchange --devicesfile") < at("O_EXCL\nudevadm settle\n"),
            "{text}"
        );
        assert!(
            at("O_EXCL\nudevadm settle\n") < at("wipefs --all /dev/vdb\n"),
            "{text}"
        );

        // The swap LV in use: refused even with --erase, and nothing let go.
        t.file("/proc/swaps", "Filename\tType\tSize\tUsed\tPriority\n/dev/dm-1                               partition\t1048572\t0\t-2\n");
        let m = map(&layout(), &o, &scan(&s, &o, &[]), &s);
        let p = m.problems.join("\n");
        assert!(p.contains("vda -> /dev/vdb: the target is held, and what holds it can't be let go: dm-1 (vg0-swap) is in use as swap; refused even with --erase"), "{p}");
        assert!(m.release.is_empty());
        // A partition mounted: refused as before, --erase or not.
        t.file("/proc/swaps", "Filename\tType\tSize\tUsed\tPriority\n");
        t.file(
            "/proc/self/mountinfo",
            "30 1 253:18 / /mnt/old rw - ext4 /dev/vdb2 rw\n",
        );
        let p = map(&layout(), &o, &scan(&s, &o, &[]), &s)
            .problems
            .join("\n");
        assert!(p.contains("the target is in use (vdb2 is mounted at /mnt/old; vdb2 is held by dm-0, dm-1); refused even with --erase"), "{p}");
    }

    /// An MBR with the sample layout's signature and PARTS (slot, start,
    /// sectors).
    fn mbr(parts: &[(usize, u32, u32)]) -> Vec<u8> {
        let mut s = vec![0u8; 512];
        s[440..444].copy_from_slice(&0x1f2e_3d4cu32.to_le_bytes());
        for &(slot, start, n) in parts {
            parttable::build::mbr_entry(&mut s, slot, 0, 0x83, start, n);
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
        let lvm = super::super::release::Action::Lvm {
            vg: "vg0".into(),
            pvs: vec!["/dev/vdb2".into()],
        };
        let o = opts(&["--map", "vda=vdb"]);
        let system = scan(&s, &o, &[]);
        assert!(
            system
                .disk("vdb")
                .unwrap()
                .is_ours(l.disk("vda").unwrap(), &layout_uuids(&l))
        );
        assert!(!partition_done(
            l.disk("vda").unwrap(),
            system.disk("vdb").unwrap()
        ));

        // Without --erase: as before, neither refused nor let go of.
        let m = run(&["--map", "vda=vdb"]);
        assert!(
            m.problems.is_empty() && m.release.is_empty(),
            "{:?} {:?}",
            m.problems,
            m.release
        );

        // With it, the plan and a --through run let go of vg0, then wipe.
        for a in [
            &["--map", "vda=vdb", "--erase", "/dev/vdb"][..],
            &[
                "--map",
                "vda=vdb",
                "--erase",
                "/dev/vdb",
                "--through",
                "mount",
            ],
        ] {
            let m = run(a);
            assert!(m.problems.is_empty(), "{a:?}: {:?}", m.problems);
            assert_eq!(
                m.release.iter().map(|x| &x.action).collect::<Vec<_>>(),
                vec![&lvm],
                "{a:?}"
            );
            let b = super::super::commands::build(&l, &opts(a), &m);
            let text: String = b
                .stage(Stage::Partition)
                .iter()
                .map(|c| c.shell())
                .collect();
            let at = |x: &str| {
                text.find(x)
                    .unwrap_or_else(|| panic!("no {x:?} in\n{text}"))
            };
            assert!(
                at("vgchange --devicesfile '' --devices /dev/vdb2 --activate n vg0")
                    < at("wipefs --all /dev/vdb\n"),
                "{text}"
            );
        }
        // The same refusals: an LV mounted outside --target.
        t.file(
            "/proc/self/mountinfo",
            "30 1 252:0 / /mnt/old rw - ext4 /dev/mapper/vg0-root rw\n",
        );
        let p = run(&["--map", "vda=vdb", "--erase", "/dev/vdb"])
            .problems
            .join("\n");
        assert!(p.contains("vda -> /dev/vdb: the target is held, and what holds it can't be let go: dm-0 (vg0-root) is mounted at /mnt/old; refused even with --erase"), "{p}");
        // A partition mounted: refused with --erase, as before without.
        t.file(
            "/proc/self/mountinfo",
            "30 1 253:18 / /mnt/old rw - ext4 /dev/vdb2 rw\n",
        );
        let m = run(&["--map", "vda=vdb", "--erase", "/dev/vdb"]);
        assert!(m.problems.join("\n").contains("vda -> /dev/vdb: the target is in use (vdb2 is mounted at /mnt/old; vdb2 is held by dm-0, dm-1); refused even with --erase"), "{:?}", m.problems);
        let m = run(&["--map", "vda=vdb"]);
        assert!(
            m.problems.is_empty() && m.release.is_empty(),
            "{:?}",
            m.problems
        );

        // An LV mounted under --target, from an earlier partial run: load's
        // own, so nothing is let go of; the partition stage runs (the table
        // isn't the layout's), and wipefs couldn't open the disk under it,
        // so with --erase the plan refuses it.  Without, taken as it is.
        t.file(
            "/proc/self/mountinfo",
            "30 1 252:0 / /mnt/bko-target rw - ext4 /dev/mapper/vg0-root rw\n",
        );
        for a in [
            &["--map", "vda=vdb", "--erase", "/dev/vdb"][..],
            &[
                "--map",
                "vda=vdb",
                "--erase",
                "/dev/vdb",
                "--through",
                "mount",
            ],
        ] {
            let m = run(a);
            assert!(m.release.is_empty(), "{a:?}: {:?}", m.release);
            let p = m.problems.join("\n");
            assert!(p.contains("vda -> /dev/vdb: the partition stage would wipe it, but load's own is on it, from an earlier stage (mounted under /mnt/bko-target, or a pool of the layout's imported there); `--only close` first lets go of it"), "{a:?}: {p}");
        }
        let m = run(&["--map", "vda=vdb"]);
        assert!(
            m.problems.is_empty() && m.release.is_empty(),
            "{:?} {:?}",
            m.problems,
            m.release
        );
        t.file("/proc/self/mountinfo", "");

        // Another stage alone never reaches the partition stage.
        let m = run(&[
            "--map", "vda=vdb", "--erase", "/dev/vdb", "--only", "status",
        ]);
        assert!(
            m.problems.is_empty() && m.release.is_empty(),
            "{:?}",
            m.problems
        );
    }

    /// The sample layout's own table, every partition at its sectors.
    fn own_table() -> Vec<u8> {
        mbr(&[
            (0, 2048, 1953792),
            (1, 1955840, 3905536),
            (2, 5861376, 27693056),
        ])
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
        let lvm = super::super::release::Action::Lvm {
            vg: "vg0".into(),
            pvs: vec!["/dev/vdb2".into()],
        };
        let o = opts(&["--map", "vda=vdb"]);
        let (d, system) = (l.disk("vda").unwrap(), scan(&s, &o, &[]));
        let vdb = system.disk("vdb").unwrap();
        assert!(partition_done(d, vdb));
        let partition = |a: &[&str]| {
            super::super::status::rows(&l, &opts(a), &s)
                .0
                .into_iter()
                .find(|r| r.0 == Stage::Partition)
                .map(|r| (r.1, r.2))
                .unwrap()
        };
        use super::super::status::State;
        let erase = ["--map", "vda=vdb", "--erase", "/dev/vdb"];
        let through = [
            "--map",
            "vda=vdb",
            "--erase",
            "/dev/vdb",
            "--through",
            "mount",
        ];
        let note = "vda -> /dev/vdb: the partition stage runs: --erase and no record that load wrote this table";

        // Without --erase: as before, the stage done and nothing let go of.
        for a in [
            &["--map", "vda=vdb"][..],
            &["--map", "vda=vdb", "--through", "mount"],
        ] {
            let m = run(a);
            assert!(
                m.problems.is_empty() && m.release.is_empty(),
                "{a:?}: {:?} {:?}",
                m.problems,
                m.release
            );
            assert!(
                !m.notes.iter().any(|n| n.contains(note)),
                "{a:?}: {:?}",
                m.notes
            );
        }
        assert_eq!(partition(&["--map", "vda=vdb"]).0, State::Done);

        // With it and no record: the stage runs, vg0 let go of, then wiped.
        for a in [&erase[..], &through] {
            let m = run(a);
            assert!(m.problems.is_empty(), "{a:?}: {:?}", m.problems);
            assert_eq!(
                m.release.iter().map(|x| &x.action).collect::<Vec<_>>(),
                vec![&lvm],
                "{a:?}"
            );
            assert!(
                m.notes
                    .iter()
                    .any(|n| n.starts_with(note)
                        && n.contains(&format!("{RUN_DIR}/partitioned/vdb"))),
                "{a:?}: {:?}",
                m.notes
            );
            let b = super::super::commands::build(&l, &opts(a), &m);
            let text: String = b
                .stage(Stage::Partition)
                .iter()
                .map(|c| c.shell())
                .collect();
            let at = |x: &str| {
                text.find(x)
                    .unwrap_or_else(|| panic!("no {x:?} in\n{text}"))
            };
            assert!(
                at("vgchange --devicesfile '' --devices /dev/vdb2 --activate n vg0")
                    < at("wipefs --all /dev/vdb\n"),
                "{text}"
            );
            assert_eq!(
                m.records,
                vec![(table_record("vdb"), table_record_text(&l, d, vdb, &s))]
            );
        }
        assert_eq!(partition(&erase), (State::NotDone, "/dev/vdb: the source's table, but no record that load wrote it, and --erase wipes it".to_string()));
        // The same refusals: an LV mounted outside --target.
        t.file(
            "/proc/self/mountinfo",
            "30 1 252:0 / /mnt/old rw - ext4 /dev/mapper/vg0-root rw\n",
        );
        for a in [&erase[..], &through] {
            let p = run(a).problems.join("\n");
            assert!(p.contains("vda -> /dev/vdb: the target is held, and what holds it can't be let go: dm-0 (vg0-root) is mounted at /mnt/old; refused even with --erase"), "{a:?}: {p}");
        }
        assert!(run(&["--map", "vda=vdb"]).problems.is_empty());
        t.file("/proc/self/mountinfo", "");

        // A record of another layout's table (another dump) is no record.
        t.file(
            &table_record("vdb"),
            table_record_text(&l, d, vdb, &s).replace("layout deb13 ", "layout other "),
        );
        assert_eq!(run(&erase).release.len(), 1);
        assert_eq!(partition(&erase).0, State::NotDone);

        // With this session's record: load wrote the table, so --through
        // (and the plan) skip the stage and let go of nothing; --only
        // partition runs it, and lets go first.
        t.file(&table_record("vdb"), table_record_text(&l, d, vdb, &s));
        for a in [&erase[..], &through] {
            let m = run(a);
            assert!(
                m.problems.is_empty() && m.release.is_empty(),
                "{a:?}: {:?} {:?}",
                m.problems,
                m.release
            );
            assert!(
                !m.notes.iter().any(|n| n.contains(note)),
                "{a:?}: {:?}",
                m.notes
            );
        }
        assert_eq!(partition(&erase).0, State::Done);
        // udev's serial and WWID coming (or going) between runs leave the
        // record standing: it names the disk by sysfs only.  The kernel's
        // own serial changing is another disk.
        t.file(
            "/run/udev/data/b253:16",
            "E:ID_SERIAL_SHORT=SER123\nE:ID_WWN=0x5000c500a1b2c3d4\n",
        );
        assert_eq!(
            scan(&s, &o, &[]).disk("vdb").unwrap().b.serial.as_deref(),
            Some("SER123")
        );
        assert_eq!(partition(&erase).0, State::Done);
        assert!(run(&through).release.is_empty());
        t.file("/sys/block/vdb/device/serial", "OTHER\n");
        assert_eq!(partition(&erase).0, State::NotDone);
        std::fs::remove_file(t.p("/sys/block/vdb/device/serial")).unwrap();
        assert_eq!(partition(&erase).0, State::Done);
        let m = run(&[
            "--map",
            "vda=vdb",
            "--erase",
            "/dev/vdb",
            "--only",
            "partition",
        ]);
        assert!(m.problems.is_empty(), "{:?}", m.problems);
        assert_eq!(
            m.release.iter().map(|x| &x.action).collect::<Vec<_>>(),
            vec![&lvm]
        );

        // Load's own under --target (the mount stage's, of this session):
        // a rerun goes on from it, but --only partition would wipe the disk
        // under it, so the plan refuses it rather than wipefs failing.
        t.file(
            "/proc/self/mountinfo",
            "30 1 252:0 / /mnt/bko-target rw - ext4 /dev/mapper/vg0-root rw\n",
        );
        for a in [&erase[..], &through] {
            let m = run(a);
            assert!(
                m.problems.is_empty() && m.release.is_empty(),
                "{a:?}: {:?} {:?}",
                m.problems,
                m.release
            );
        }
        let m = run(&[
            "--map",
            "vda=vdb",
            "--erase",
            "/dev/vdb",
            "--only",
            "partition",
        ]);
        assert!(m.release.is_empty(), "{:?}", m.release);
        let p = m.problems.join("\n");
        assert!(p.contains("vda -> /dev/vdb: the partition stage would wipe it, but load's own is on it, from an earlier stage (mounted under /mnt/bko-target"), "{p}");
        assert!(p.contains("`--only close` first lets go of it"), "{p}");
        // A partition mounted there, too: the same refusal, not wipefs's.
        t.file(
            "/proc/self/mountinfo",
            "30 1 253:18 / /mnt/bko-target rw - ext4 /dev/vdb2 rw\n",
        );
        let p = run(&[
            "--map",
            "vda=vdb",
            "--erase",
            "/dev/vdb",
            "--only",
            "partition",
        ])
        .problems
        .join("\n");
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
            format!(
                "{a}\"disks\": [{VDX}],\n    {}",
                &rest[rest.find("\"md_arrays\"").unwrap()..]
            )
        } else {
            j.replace(
                "\"content\": null, \"boot_code\": {\"kind\": \"grub2\"}\n    }]",
                &format!(
                    "\"content\": null, \"boot_code\": {{\"kind\": \"grub2\"}}\n    }}, {VDX}]"
                ),
            )
        };
        let l = Layout::from_json(crate::json::parse(&j).unwrap()).unwrap();
        assert_eq!(
            l.disks.iter().map(|d| d.name.as_str()).collect::<Vec<_>>(),
            if alone {
                vec!["vdx"]
            } else {
                vec!["vda", "vdx"]
            }
        );
        l
    }

    fn row(l: &Layout, a: &[&str], s: &Sys, st: Stage) -> (super::super::status::State, String) {
        super::super::status::rows(l, &opts(a), s)
            .0
            .into_iter()
            .find(|r| r.0 == st)
            .map(|r| (r.1, r.2))
            .unwrap()
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
        t.disk("vdc", "253:32", &crate::probe::xfs::sample::image(""), 512)
            .file("/sys/block/vdc/size", "16777216\n");
        let s = Sys::new(t.root(), Box::new(FakeRunner::default()), true);
        let l = with_vdx(true);
        let plain = ["--map", "vdx=vdc"];
        let erase = ["--map", "vdx=vdc", "--erase", "/dev/vdc"];
        let through = [
            "--map",
            "vdx=vdc",
            "--erase",
            "/dev/vdc",
            "--through",
            "mount",
        ];
        assert_eq!(row(&l, &plain, &s, Stage::Partition).0, State::Nothing);
        assert_eq!(
            row(&l, &plain, &s, Stage::Format).0,
            State::Done,
            "the old XFS passes format's check"
        );
        let m = prepare(&l, &opts(&plain), &s).mapping;
        assert!(
            m.problems.is_empty() && !m.notes.iter().any(|n| n.contains("no record")),
            "{:?} {:?}",
            m.problems,
            m.notes
        );

        assert_eq!(
            row(&l, &erase, &s, Stage::Partition),
            (
                State::NotDone,
                "/dev/vdc: no record that load wiped it, and --erase wipes it".to_string()
            )
        );
        for a in [&erase[..], &through] {
            let m = prepare(&l, &opts(a), &s).mapping;
            assert!(m.problems.is_empty(), "{a:?}: {:?}", m.problems);
            assert!(m.notes.iter().any(|n| n.starts_with("vdx -> /dev/vdc: the partition stage runs: --erase and no record that load wiped this disk (its source has no table")), "{a:?}: {:?}", m.notes);
            let vdc = scan(&s, &opts(a), &[]);
            assert_eq!(
                m.records,
                vec![(
                    table_record("vdc"),
                    table_record_text(&l, l.disk("vdx").unwrap(), vdc.disk("vdc").unwrap(), &s)
                )]
            );
            assert!(
                m.records[0].1.contains("no table: wiped\n"),
                "{:?}",
                m.records
            );
        }
        let (path, text) = prepare(&l, &opts(&erase), &s).mapping.records.remove(0);
        t.file(&path, text);
        assert_eq!(row(&l, &erase, &s, Stage::Partition).0, State::Done);
        let m = prepare(&l, &opts(&through), &s).mapping;
        assert!(
            m.problems.is_empty() && !m.notes.iter().any(|n| n.contains("no record")),
            "{:?} {:?}",
            m.problems,
            m.notes
        );
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
        let erase = [
            "--map", "vda=vdb", "--map", "vdx=vdc", "--erase", "/dev/vdb", "--erase", "/dev/vdc",
        ];
        let through = [
            "--map",
            "vda=vdb",
            "--map",
            "vdx=vdc",
            "--erase",
            "/dev/vdb",
            "--erase",
            "/dev/vdc",
            "--through",
            "mount",
        ];
        let lvm = super::super::release::Action::Lvm {
            vg: "vg0".into(),
            pvs: vec!["/dev/vdb2".into()],
        };
        let runs = |a: &[&str]| {
            let m = prepare(&l, &opts(a), &s).mapping;
            assert!(m.problems.is_empty(), "{a:?}: {:?}", m.problems);
            m.release
                .iter()
                .map(|x| x.action.clone())
                .collect::<Vec<_>>()
        };
        // No records: both disks unrecorded, the stage runs.
        assert_eq!(row(&l, &erase, &s, Stage::Partition).0, State::NotDone);
        assert_eq!(runs(&through), vec![lvm.clone()]);
        let records = prepare(&l, &opts(&erase), &s).mapping.records;
        assert_eq!(
            records.iter().map(|r| r.0.clone()).collect::<Vec<_>>(),
            vec![
                format!("{RUN_DIR}/partitioned/vdb"),
                format!("{RUN_DIR}/partitioned/vdc")
            ]
        );
        // vdb's alone: vdc still unrecorded, so the stage still runs.
        t.file(&records[0].0, records[0].1.as_str());
        let (st, why) = row(&l, &erase, &s, Stage::Partition);
        assert_eq!(
            (st, why.as_str()),
            (
                State::Partly,
                "/dev/vdc: no record that load wiped it, and --erase wipes it"
            )
        );
        assert_eq!(runs(&through), vec![lvm.clone()]);
        // Both: done, by status and by the plan alike.
        t.file(&records[1].0, records[1].1.as_str());
        assert_eq!(row(&l, &erase, &s, Stage::Partition).0, State::Done);
        assert!(runs(&through).is_empty() && runs(&erase).is_empty());
        t.file(
            "/proc/self/mountinfo",
            "30 1 252:0 / /mnt/bko-target rw - ext4 /dev/mapper/vg0-root rw\n",
        );
        assert_eq!(row(&l, &through, &s, Stage::Partition).0, State::Done);
        assert!(runs(&through).is_empty() && runs(&erase).is_empty());
    }
}
