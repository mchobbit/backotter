//! Letting go of what holds a target disk named with --erase (layload.md,
//! "Safety"): the LVM volume groups, LUKS mappings, md arrays, other
//! device-mapper devices and ZFS pools a rescue system started on it at
//! boot, stopped from the top of their stack down, before the partition
//! stage's wipefs.  Worked out from sysfs (holders and slaves), the mount
//! table, the swaps, the PVs and the imported pools; refused when any of it
//! is mounted, swap, open, or also on a disk that isn't named with --erase.

use super::target::{resolve_name, System};
use crate::blockdev::{BlockDev, Kind};
use crate::sys::Sys;

/// One thing to stop.
#[derive(Debug, Clone, PartialEq)]
pub enum Action {
    /// `zpool export NAME`.
    Pool(String),
    /// `cryptsetup close NAME` (the dm name).
    Crypt(String),
    /// `vgchange --activate n VG`, LVM seeing PVS only (/dev paths).
    Lvm { vg: String, pvs: Vec<String> },
    /// `mdadm --stop /dev/NAME`.
    Md(String),
    /// `dmsetup remove NAME` (the dm name): device-mapper that isn't LVM's or
    /// LUKS's.
    Dm(String),
}

/// An action, what it stops, and which held target disks it's for.
#[derive(Debug, Clone, PartialEq)]
pub struct Step {
    pub action: Action,
    /// The devices it stops, described ("dm-0 (vg0-root)").
    pub stops: Vec<String>,
    /// The target disks (kernel names) whose holders these are.
    pub disks: Vec<String>,
}

impl Step {
    pub fn what(&self) -> String {
        match &self.action {
            Action::Pool(n) => format!("ZFS pool {n} exported"),
            Action::Crypt(n) => format!("LUKS mapping {n} closed"),
            Action::Lvm { vg, .. } => format!("volume group {vg} deactivated"),
            Action::Md(n) => format!("md array {n} stopped"),
            Action::Dm(n) => format!("device-mapper device {n} removed"),
        }
    }
}

#[derive(Debug, Default)]
pub struct Release {
    /// Top of the stack first.
    pub steps: Vec<Step>,
    /// (held target disk, why its holders can't be let go).
    pub problems: Vec<(String, String)>,
}

/// The whole disks NAME sits on, through partitions, md arrays and
/// device-mapper (its slaves, all the way down).
pub fn bases(all: &[BlockDev], name: &str) -> Vec<String> {
    fn walk(all: &[BlockDev], name: &str, depth: usize, out: &mut Vec<String>) {
        let Some(b) = all.iter().find(|b| b.name == name) else {
            if !out.iter().any(|o| o == name) {
                out.push(name.to_string());
            }
            return;
        };
        if let Some(p) = &b.parent {
            walk(all, p, depth + 1, out);
        } else if b.slaves.is_empty() || depth > 16 {
            if !out.contains(&b.name) {
                out.push(b.name.clone());
            }
        } else {
            for s in &b.slaves {
                walk(all, s, depth + 1, out);
            }
        }
    }
    let mut out = Vec::new();
    walk(all, name, 0, &mut out);
    out
}

fn describe(b: &BlockDev) -> String {
    match &b.dm_name {
        Some(n) => format!("{} ({n})", b.name),
        None => b.name.clone(),
    }
}

/// The VG UUID (no dashes) of an LVM dm device's uuid, "LVM-" + 32 + 32.
fn lvm_vg_uuid(dm_uuid: &str) -> Option<String> {
    let rest = dm_uuid.strip_prefix("LVM-")?;
    (rest.len() >= 32).then(|| rest[..32].to_string())
}

/// `dmsetup info -c --noheadings --separator : -o name,open`: each mapping's
/// open count.  None when dmsetup isn't there to ask.
fn dm_open_counts(sys: &Sys) -> Option<Vec<(String, u32)>> {
    let out = sys.run("dmsetup", &["info", "-c", "--noheadings", "--separator", ":", "-o", "name,open"])?;
    Some(out.lines().filter_map(|l| l.trim().rsplit_once(':')).filter_map(|(n, c)| Some((n.to_string(), c.trim().parse().ok()?))).collect())
}

/// What each of HELD (target disks named with --erase, busy only because
/// something holds them or a pool is imported from them) needs stopped, and
/// in what order.  ERASED are every target disk named with --erase: no step
/// may reach a device that also sits on any other disk.
pub fn plan(sys: &Sys, system: &System, held: &[String], erased: &[String]) -> Release {
    let all = &system.devs;
    let mut r = Release::default();
    // (key, action, what it stops) in the order first met, and "a before b".
    let mut actions: Vec<(String, Action, Vec<String>, Vec<String>)> = Vec::new();
    let mut before: Vec<(String, String)> = Vec::new();
    let mut open: Option<Option<Vec<(String, u32)>>> = None;
    let not_erased = |names: &[String]| names.iter().filter(|d| !erased.contains(d)).cloned().collect::<Vec<_>>();
    for disk in held {
        let mut why: Vec<String> = Vec::new();
        let mut mine: Vec<(String, Action, Vec<String>)> = Vec::new();
        let mut edges: Vec<(String, String)> = Vec::new();
        // The disk, its partitions, and everything stacked on them.
        let mut queue: Vec<String> = std::iter::once(disk.clone()).chain(all.iter().filter(|b| b.parent.as_deref() == Some(disk.as_str())).map(|b| b.name.clone())).collect();
        let roots = queue.len();
        let mut i = 0;
        let mut key_of: Vec<(String, String)> = Vec::new();
        while i < queue.len() {
            let name = queue[i].clone();
            let is_root = i < roots;
            i += 1;
            let Some(b) = all.iter().find(|b| b.name == name) else {
                why.push(format!("{name} is in the stack, but not in /sys/block"));
                continue;
            };
            for h in &b.holders {
                if !queue.contains(h) {
                    queue.push(h.clone());
                }
            }
            // Pools take no holders: each one with a vdev here is on top.
            for p in system.pools.iter().filter(|p| p.devices.contains(&name)) {
                let key = format!("pool:{}", p.name);
                if !mine.iter().any(|(k, ..)| *k == key) {
                    let mut ok = true;
                    for m in system.mounts.iter().filter(|m| m.fstype == "zfs" && (m.source == p.name || m.source.starts_with(&format!("{}/", p.name)))) {
                        why.push(format!("ZFS pool {}'s dataset {} is mounted at {}", p.name, m.source, m.target));
                        ok = false;
                    }
                    for d in &p.devices {
                        let other = not_erased(&bases(all, d));
                        if !other.is_empty() {
                            why.push(format!("ZFS pool {} also has {d}, on {}, which isn't named with --erase", p.name, other.join(", ")));
                            ok = false;
                        }
                    }
                    if ok {
                        mine.push((key.clone(), Action::Pool(p.name.clone()), vec![format!("pool {}", p.name)]));
                    }
                }
                edges.push((key.clone(), format!("dev:{name}")));
            }
            if is_root {
                continue;
            }
            // A holder: never mounted, swap or open, and only on erased disks.
            let d = describe(b);
            for m in system.mounts.iter().filter(|m| m.devno == b.devno || resolve_name(sys, &m.source) == b.name) {
                why.push(format!("{d} is mounted at {}", m.target));
            }
            if system.swaps.contains(&b.name) {
                why.push(format!("{d} is in use as swap"));
            }
            let other = not_erased(&bases(all, &b.name));
            if !other.is_empty() {
                why.push(format!("{d} also sits on {}, which isn't named with --erase", other.join(", ")));
            }
            if b.kind == Kind::Dm {
                let counts = open.get_or_insert_with(|| dm_open_counts(sys));
                if let (Some(counts), Some(n)) = (counts.as_ref(), b.dm_name.as_ref()) {
                    let pools = system.pools.iter().filter(|p| p.devices.contains(&b.name)).count();
                    if let Some((_, c)) = counts.iter().find(|(x, _)| x == n) {
                        if *c as usize > b.holders.len() + pools {
                            why.push(format!("{d} is open (open count {c}, of which what's stacked on it accounts for {})", b.holders.len() + pools));
                        }
                    }
                }
            }
            let (key, action) = match (b.kind, b.dm_uuid.as_deref()) {
                (Kind::Md, _) => (format!("md:{}", b.name), Some(Action::Md(b.name.clone()))),
                (Kind::Dm, Some(u)) if u.starts_with("LVM-") => {
                    let Some(vgu) = lvm_vg_uuid(u) else {
                        why.push(format!("{d}: an LVM device whose UUID ({u}) names no volume group"));
                        continue;
                    };
                    let key = format!("lvm:{vgu}");
                    let mut act = None;
                    if !mine.iter().any(|(k, ..)| *k == key) {
                        // Every LV of the group goes; each is a holder to check.
                        for lv in all.iter().filter(|x| x.kind == Kind::Dm && x.dm_uuid.as_deref().and_then(lvm_vg_uuid).as_deref() == Some(vgu.as_str())) {
                            if !queue.contains(&lv.name) {
                                queue.push(lv.name.clone());
                            }
                        }
                        let vg = b.dm_name.as_deref().and_then(crate::blockdev::split_lvm_dm_name).map(|(v, _)| v).unwrap_or_default();
                        let pvs: Vec<_> = system.pvs.iter().filter(|p| p.vg_uuid.replace('-', "") == vgu).collect();
                        if pvs.is_empty() || vg.is_empty() {
                            why.push(format!("{d}: which PVs its volume group {} has wasn't read", if vg.is_empty() { "?" } else { &vg }));
                        }
                        for pv in &pvs {
                            let other = not_erased(&pv.disks);
                            if !other.is_empty() {
                                why.push(format!("volume group {vg} (of {d}) has the PV {}, on {}, which isn't named with --erase", pv.dev, other.join(", ")));
                            }
                        }
                        let paths = pvs
                            .iter()
                            .map(|p| match all.iter().find(|x| x.name == p.dev).and_then(|x| x.dm_name.as_ref()) {
                                Some(n) => format!("/dev/mapper/{n}"),
                                None => format!("/dev/{}", p.dev),
                            })
                            .collect();
                        act = Some(Action::Lvm { vg, pvs: paths });
                    }
                    (key, act)
                }
                (Kind::Dm, Some(u)) if u.starts_with("CRYPT-") => match &b.dm_name {
                    Some(n) => (format!("crypt:{n}"), Some(Action::Crypt(n.clone()))),
                    None => {
                        why.push(format!("{d}: a LUKS mapping with no dm name"));
                        continue;
                    }
                },
                (Kind::Dm, _) => match &b.dm_name {
                    Some(n) => (format!("dm:{n}"), Some(Action::Dm(n.clone()))),
                    None => {
                        why.push(format!("{d}: a device-mapper device with no dm name"));
                        continue;
                    }
                },
                _ => {
                    why.push(format!("{d} holds it, and load knows no way to let go of a {}", b.kind.name()));
                    continue;
                }
            };
            key_of.push((b.name.clone(), key.clone()));
            match mine.iter_mut().find(|(k, ..)| *k == key) {
                Some((_, _, stops)) => {
                    if !stops.contains(&d) {
                        stops.push(d);
                    }
                }
                None => {
                    if let Some(a) = action {
                        mine.push((key.clone(), a, vec![d]));
                    }
                }
            }
            // What sits on it goes first.
            for h in &b.holders {
                edges.push((format!("dev:{h}"), key.clone()));
            }
        }
        // Device names in edges, to their actions' keys.
        let resolve = |k: &str| -> Option<String> {
            match k.strip_prefix("dev:") {
                Some(n) => key_of.iter().find(|(x, _)| x == n).map(|(_, k)| k.clone()),
                None => Some(k.to_string()),
            }
        };
        if !why.is_empty() {
            why.dedup();
            r.problems.push((disk.clone(), why.join("; ")));
            continue;
        }
        for (a, b) in edges {
            if let (Some(a), Some(b)) = (resolve(&a), resolve(&b)) {
                if a != b && !before.contains(&(a.clone(), b.clone())) {
                    before.push((a, b));
                }
            }
        }
        for (k, a, stops) in mine {
            match actions.iter_mut().find(|(x, ..)| *x == k) {
                Some((_, _, s, disks)) => {
                    for d in stops {
                        if !s.contains(&d) {
                            s.push(d);
                        }
                    }
                    if !disks.contains(disk) {
                        disks.push(disk.clone());
                    }
                }
                None => actions.push((k, a, stops, vec![disk.clone()])),
            }
        }
    }
    // A disk refused drops its own steps (and its problem refuses the whole
    // plan, so nothing runs).  Top-down: an action goes once everything that must precede it has.
    let mut done: Vec<String> = Vec::new();
    while done.len() < actions.len() {
        let next = actions.iter().find(|(k, ..)| !done.contains(k) && before.iter().filter(|(_, b)| b == k).all(|(a, _)| done.contains(a) || !actions.iter().any(|(x, ..)| x == a)));
        let Some((k, a, stops, disks)) = next else {
            r.problems.push((held.first().cloned().unwrap_or_default(), "what holds it stacks in a loop; nothing is let go".into()));
            r.steps.clear();
            return r;
        };
        done.push(k.clone());
        r.steps.push(Step { action: a.clone(), stops: stops.clone(), disks: disks.clone() });
    }
    r
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::load::target::{HostPv, ImportedPool};
    use crate::mounts::Mount;

    fn dev(name: &str, kind: Kind, parent: Option<&str>, holders: &[&str], slaves: &[&str]) -> BlockDev {
        BlockDev {
            name: name.into(),
            kind,
            devno: format!("9:{name}"),
            parent: parent.map(str::to_string),
            holders: holders.iter().map(|s| s.to_string()).collect(),
            slaves: slaves.iter().map(|s| s.to_string()).collect(),
            ..BlockDev::default()
        }
    }

    fn dm(name: &str, dm_name: &str, uuid: &str, holders: &[&str], slaves: &[&str]) -> BlockDev {
        BlockDev { dm_name: Some(dm_name.into()), dm_uuid: Some(uuid.into()), ..dev(name, Kind::Dm, None, holders, slaves) }
    }

    const VG: &str = "Yb3kFa0Pz1AbCdEfGhIjKlMnOpQrStUv";
    fn lv(n: u8) -> String {
        format!("LVM-{VG}{}", format!("{n}").repeat(32))
    }
    fn pv(dev: &str, disks: &[&str]) -> HostPv {
        HostPv { dev: dev.into(), uuid: "x".into(), vg: "vg0".into(), vg_uuid: "Yb3kFa-0Pz1-AbCd-EfGh-IjKl-MnOp-QrStUv".into(), disks: disks.iter().map(|s| s.to_string()).collect() }
    }

    fn sys() -> (crate::testutil::Tree, Sys) {
        let t = crate::testutil::Tree::new("release");
        let s = Sys::new(t.root(), Box::new(crate::sys::FakeRunner::default()), true);
        (t, s)
    }

    /// vdb2 a PV of vg0, with root and swap LVs active (BackShot2's L3).
    fn lvm_system() -> System {
        System {
            disks: vec![],
            mounts: vec![],
            pools: vec![],
            pvs: vec![pv("vdb2", &["vdb"])],
            devs: vec![
                dev("vdb", Kind::Disk, None, &[], &[]),
                dev("vdb1", Kind::Partition, Some("vdb"), &[], &[]),
                dev("vdb2", Kind::Partition, Some("vdb"), &["dm-0", "dm-1"], &[]),
                dev("vdc", Kind::Disk, None, &[], &[]),
                dm("dm-0", "vg0-root", &lv(1), &[], &["vdb2"]),
                dm("dm-1", "vg0-swap", &lv(2), &[], &["vdb2"]),
            ],
            swaps: vec![],
        }
    }

    fn names(r: &Release) -> Vec<String> {
        r.steps.iter().map(Step::what).collect()
    }

    #[test]
    fn lvm_on_an_erased_disk_is_deactivated() {
        let (_t, s) = sys();
        let r = plan(&s, &lvm_system(), &["vdb".into()], &["vdb".into()]);
        assert!(r.problems.is_empty(), "{:?}", r.problems);
        assert_eq!(r.steps.len(), 1);
        assert_eq!(r.steps[0].action, Action::Lvm { vg: "vg0".into(), pvs: vec!["/dev/vdb2".into()] });
        assert_eq!(r.steps[0].stops, vec!["dm-0 (vg0-root)", "dm-1 (vg0-swap)"]);
        assert_eq!(r.steps[0].disks, vec!["vdb"]);
    }

    #[test]
    fn a_volume_group_on_another_disk_too_is_refused() {
        let (_t, s) = sys();
        let mut sy = lvm_system();
        sy.pvs.push(pv("vdc1", &["vdc"]));
        let r = plan(&s, &sy, &["vdb".into()], &["vdb".into()]);
        assert!(r.steps.is_empty());
        assert_eq!(r.problems.len(), 1);
        assert!(r.problems[0].1.contains("volume group vg0 (of dm-0 (vg0-root)) has the PV vdc1, on vdc, which isn't named with --erase"), "{:?}", r.problems);
        // Both disks named: the group goes, PVs on both.
        let r = plan(&s, &sy, &["vdb".into()], &["vdb".into(), "vdc".into()]);
        assert!(r.problems.is_empty(), "{:?}", r.problems);
        assert_eq!(r.steps[0].action, Action::Lvm { vg: "vg0".into(), pvs: vec!["/dev/vdb2".into(), "/dev/vdc1".into()] });
        // An LV of the group that sits on the other disk is checked too.
        let mut sy2 = lvm_system();
        sy2.pvs.push(pv("vdc1", &["vdc"]));
        sy2.devs.push(dm("dm-2", "vg0-home", &lv(3), &[], &["vdc1"]));
        sy2.mounts.push(Mount { devno: sy2.devs.last().unwrap().devno.clone(), source: "/dev/mapper/vg0-home".into(), target: "/home".into(), fstype: "ext4".into(), ..Mount::default() });
        let r = plan(&s, &sy2, &["vdb".into()], &["vdb".into(), "vdc".into()]);
        assert!(r.steps.is_empty() && r.problems[0].1.contains("dm-2 (vg0-home) is mounted at /home"), "{:?}", r.problems);
    }

    #[test]
    fn luks_on_lvm_closes_top_down() {
        let (_t, s) = sys();
        let mut sy = lvm_system();
        // dm-0 holds a LUKS mapping, dm-3, with a pool on it.
        sy.devs[4].holders = vec!["dm-3".into()];
        sy.devs.push(dm("dm-3", "cryptroot", "CRYPT-LUKS2-abc-cryptroot", &[], &["dm-0"]));
        sy.pools.push(ImportedPool { name: "tank".into(), altroot: "-".into(), devices: vec!["dm-3".into()] });
        let r = plan(&s, &sy, &["vdb".into()], &["vdb".into()]);
        assert!(r.problems.is_empty(), "{:?}", r.problems);
        assert_eq!(names(&r), vec!["ZFS pool tank exported", "LUKS mapping cryptroot closed", "volume group vg0 deactivated"]);
        // Open by a process beyond what's stacked on it: refused.
        let t = crate::testutil::Tree::new("release-open");
        let s2 = Sys::new(
            t.root(),
            Box::new(crate::sys::FakeRunner::default().with("dmsetup info -c --noheadings --separator : -o name,open", "vg0-root:1\nvg0-swap:0\ncryptroot:2\n")),
            true,
        );
        let r = plan(&s2, &sy, &["vdb".into()], &["vdb".into()]);
        assert!(r.steps.is_empty() && r.problems[0].1.contains("dm-3 (cryptroot) is open (open count 2, of which what's stacked on it accounts for 1)"), "{:?}", r.problems);
        // A dataset mounted: refused.
        sy.mounts.push(Mount { source: "tank/home".into(), target: "/home".into(), fstype: "zfs".into(), devno: "0:99".into(), ..Mount::default() });
        let r = plan(&s, &sy, &["vdb".into()], &["vdb".into()]);
        assert!(r.steps.is_empty() && r.problems[0].1.contains("ZFS pool tank's dataset tank/home is mounted at /home"), "{:?}", r.problems);
    }

    #[test]
    fn md_with_a_member_elsewhere_is_refused() {
        let (_t, s) = sys();
        let sy = System {
            disks: vec![],
            mounts: vec![],
            pools: vec![],
            pvs: vec![],
            devs: vec![
                dev("vdb", Kind::Disk, None, &[], &[]),
                dev("vdb1", Kind::Partition, Some("vdb"), &["md127"], &[]),
                dev("vdc", Kind::Disk, None, &[], &[]),
                dev("vdc1", Kind::Partition, Some("vdc"), &["md127"], &[]),
                dev("md127", Kind::Md, None, &[], &["vdb1", "vdc1"]),
            ],
            swaps: vec![],
        };
        let r = plan(&s, &sy, &["vdb".into()], &["vdb".into()]);
        assert!(r.steps.is_empty() && r.problems[0].1.contains("md127 also sits on vdc, which isn't named with --erase"), "{:?}", r.problems);
        let r = plan(&s, &sy, &["vdb".into(), "vdc".into()], &["vdb".into(), "vdc".into()]);
        assert!(r.problems.is_empty(), "{:?}", r.problems);
        assert_eq!(r.steps, vec![Step { action: Action::Md("md127".into()), stops: vec!["md127".into()], disks: vec!["vdb".into(), "vdc".into()] }]);
    }

    #[test]
    fn mounted_or_swap_lv_is_refused() {
        let (_t, s) = sys();
        let mut sy = lvm_system();
        sy.swaps.push("dm-1".into());
        sy.mounts.push(Mount { devno: sy.devs[4].devno.clone(), source: "/dev/mapper/vg0-root".into(), target: "/mnt/old".into(), fstype: "ext4".into(), ..Mount::default() });
        let r = plan(&s, &sy, &["vdb".into()], &["vdb".into()]);
        assert!(r.steps.is_empty());
        let p = &r.problems[0].1;
        assert!(p.contains("dm-0 (vg0-root) is mounted at /mnt/old") && p.contains("dm-1 (vg0-swap) is in use as swap"), "{p}");
    }

    #[test]
    fn md_under_lvm_stops_after_the_group() {
        let (_t, s) = sys();
        let sy = System {
            disks: vec![],
            mounts: vec![],
            pools: vec![],
            pvs: vec![pv("md127", &["vdb", "vdc"])],
            devs: vec![
                dev("vdb", Kind::Disk, None, &[], &[]),
                dev("vdb1", Kind::Partition, Some("vdb"), &["md127"], &[]),
                dev("vdc", Kind::Disk, None, &[], &[]),
                dev("vdc1", Kind::Partition, Some("vdc"), &["md127"], &[]),
                dev("md127", Kind::Md, None, &["dm-0"], &["vdb1", "vdc1"]),
                dm("dm-0", "vg0-root", &lv(1), &[], &["md127"]),
            ],
            swaps: vec![],
        };
        let r = plan(&s, &sy, &["vdb".into(), "vdc".into()], &["vdb".into(), "vdc".into()]);
        assert!(r.problems.is_empty(), "{:?}", r.problems);
        assert_eq!(names(&r), vec!["volume group vg0 deactivated", "md array md127 stopped"]);
        assert_eq!(r.steps[0].action, Action::Lvm { vg: "vg0".into(), pvs: vec!["/dev/md127".into()] });
    }
}
