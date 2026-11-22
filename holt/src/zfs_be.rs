//! ZFS boot environments (BEs): a pool's other roots, which the running
//! system doesn't mount, and how a whole-system backup reaches them
//! (docs/design/truenas.md, decision 2; docs/design/zfsbootmenu.md).
//!
//! Two rules find them, from the dump's `storage.zfs.tools` (what
//! `zfs list` said, so the same list is worked out again at load):
//!
//! - ZFSBootMenu's: any filesystem whose mountpoint is `/` (not
//!   necessarily `POOL/ROOT/NAME`), unless its user property
//!   `org.zfsbootmenu:active` is `off`; and one with mountpoint `legacy`
//!   whose `org.zfsbootmenu:active` is `on`.  A container (canmount=off)
//!   isn't one.
//! - TrueNAS's: every direct child of `boot-pool/ROOT` (each a legacy,
//!   read-only root with `truenas:kernel_version` and `zectl:keep`).
//!
//! A BE's descendant filesystems come with it.  The booted BE (mounted; on
//! TrueNAS also the pool's bootfs) is never an inactive one: it is backed
//! up where it is mounted.  Each inactive BE is listed in
//! storage.backup_sources at `/run/bko-be/NAME` (NAME its last component;
//! `/run/bko-be/POOL/NAME` when two pools have a BE of that name), each
//! descendant at that path plus its own mountpoint, with no `from` and the
//! read-only mount a backup makes there (ABRoot's mechanism,
//! crate::abroot).  `/run` is a tmpfs on the running system, so a backup's
//! mount points are never written to the source's datasets; load mounts
//! the restored datasets at the same paths under its target, restores, and
//! unmounts them (crate::load::commands).
//!
//! A BE whose datasets' key isn't loaded at dump time can't be read: dump
//! says so, and load makes it empty, as before.

use crate::json::Value;
use crate::report::{BackupSource, MountHint};

/// Where a backup mounts the inactive BEs, and load the restored ones
/// (under its target).
pub const BE_DIR: &str = "/run/bko-be";
/// TrueNAS's boot pool: its name is fixed (its GRUB script names it).
pub const TRUENAS_POOL: &str = "boot-pool";
/// ZFSBootMenu's user property: `off` hides a BE from its menu, `on`
/// shows a legacy-mountpoint one.
pub const ZBM_ACTIVE: &str = "org.zfsbootmenu:active";

/// Which rule found a BE.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Rule {
    ZfsBootMenu,
    TrueNas,
}

/// A descendant filesystem of a BE.
#[derive(Debug, Clone, PartialEq)]
pub struct BeDataset {
    pub dataset: String,
    /// Its mountpoint property as the dump has it (`/usr`, `legacy`).
    pub mountpoint: String,
    pub origin: Option<String>,
    /// Where a backup mounts it (None: it holds no files of its own, a
    /// canmount=off container or mountpoint=none, so it's made empty).
    pub path: Option<String>,
}

impl BeDataset {
    pub fn legacy(&self) -> bool {
        self.mountpoint == "legacy"
    }
}

/// One boot environment.
#[derive(Debug, Clone, PartialEq)]
pub struct BootEnv {
    pub pool: String,
    /// The dataset's last component (`25.10.6`, `void-2026`).
    pub name: String,
    pub dataset: String,
    pub mountpoint: String,
    pub origin: Option<String>,
    /// The pool's bootfs.
    pub bootfs: bool,
    pub mounted: bool,
    pub rule: Rule,
    /// Its own or a descendant's encryption key wasn't loaded at dump time
    /// (keystatus unavailable): nothing of it can be read.
    pub locked: bool,
    /// Where a backup mounts its root.
    pub path: String,
    /// Its descendant filesystems, parents first.
    pub children: Vec<BeDataset>,
}

impl BootEnv {
    /// Not the booted one: not mounted, and on TrueNAS not the bootfs.
    pub fn inactive(&self) -> bool {
        let booted = self.mounted || (self.rule == Rule::TrueNas && self.bootfs);
        !booted
    }

    pub fn legacy(&self) -> bool {
        self.mountpoint == "legacy"
    }

    /// Whether DS is the BE's root or one of its descendants.
    pub fn holds(&self, ds: &str) -> bool {
        ds == self.dataset || ds.starts_with(&format!("{}/", self.dataset))
    }
}

/// The options of a backup's read-only mount of a BE's dataset: mount.zfs
/// wants `zfsutil` for a dataset whose mountpoint isn't `legacy` (the
/// children's `/usr`, ZFSBootMenu's `/`), and refuses it for a legacy one
/// (TrueNAS's roots).
pub fn ro_options(legacy: bool) -> String {
    if legacy { "ro,nodev,nosuid,noexec".to_string() } else { "ro,zfsutil,nodev,nosuid,noexec".to_string() }
}

/// The note each inactive BE's backup sources carry.
pub fn note(name: &str) -> String {
    format!("ZFS boot environment {name} (not booted): a backup mounts it read-only here")
}

fn s(v: &Value, k: &str) -> Option<String> {
    v.get(k).and_then(Value::as_str).map(str::to_string)
}

/// A dataset's local or received user property K, or else its nearest
/// ancestor's (inheritance, which the dump's `zfs get -s local,received`
/// doesn't list on the child).
fn prop(by_name: &[(String, &Value)], ds: &str, k: &str) -> Option<String> {
    let mut name = ds;
    loop {
        if let Some((_, d)) = by_name.iter().find(|(n, _)| n == name) {
            if let Some(v) = d.at(&["properties", k, "value"]).and_then(Value::as_str) {
                return Some(v.to_string());
            }
        }
        name = name.rsplit_once('/')?.0;
    }
}

/// Every BE of the dump's `storage.zfs.tools` (`tools`), the booted ones
/// too, by pool then dataset name.  `.inactive()` tells the ones a backup
/// mounts itself.
pub fn list(tools: &Value) -> Vec<BootEnv> {
    let datasets: Vec<&Value> = tools.get("datasets").and_then(Value::as_array).into_iter().flatten().filter(|d| s(d, "type").as_deref() != Some("volume")).collect();
    let by_name: Vec<(String, &Value)> = datasets.iter().filter_map(|d| s(d, "name").map(|n| (n, *d))).collect();
    let bootfs: Vec<String> = tools
        .get("imported_pools")
        .and_then(Value::as_array)
        .into_iter()
        .flatten()
        .filter_map(|p| p.at(&["properties", "bootfs"]).and_then(Value::as_str).filter(|b| *b != "-" && !b.is_empty()).map(str::to_string))
        .collect();
    let mut roots: Vec<(String, Rule)> = Vec::new();
    for (name, d) in &by_name {
        let mp = s(d, "mountpoint").unwrap_or_default();
        let canmount = s(d, "canmount").unwrap_or_else(|| "on".into());
        let parts: Vec<&str> = name.split('/').collect();
        let rule = if parts.len() == 3 && parts[0] == TRUENAS_POOL && parts[1] == "ROOT" {
            Some(Rule::TrueNas)
        } else if parts[0] == TRUENAS_POOL && parts.get(1) == Some(&"ROOT") {
            // A BE's own descendant on TrueNAS: never a BE itself.
            None
        } else {
            let active = prop(&by_name, name, ZBM_ACTIVE);
            match mp.as_str() {
                "/" if canmount != "off" && active.as_deref() != Some("off") => Some(Rule::ZfsBootMenu),
                "legacy" if active.as_deref() == Some("on") => Some(Rule::ZfsBootMenu),
                _ => None,
            }
        };
        if let Some(r) = rule {
            roots.push((name.clone(), r));
        }
    }
    // A candidate inside another (a child with mountpoint=/) is the other's.
    let all: Vec<String> = roots.iter().map(|(n, _)| n.clone()).collect();
    roots.retain(|(n, _)| !all.iter().any(|o| o != n && n.starts_with(&format!("{o}/"))));
    roots.sort_by(|a, b| a.0.cmp(&b.0));
    // Hinted paths: /run/bko-be/NAME, or POOL/NAME where two pools share a
    // NAME, or the whole dataset name where even that clashes.
    let last = |n: &str| n.rsplit('/').next().unwrap_or(n).to_string();
    let pool_of = |n: &str| n.split('/').next().unwrap_or(n).to_string();
    let count = |f: &dyn Fn(&str) -> String, x: &str| roots.iter().filter(|(n, _)| f(n) == f(x)).count();
    let mut out = Vec::new();
    for (name, rule) in &roots {
        let Some(d) = by_name.iter().find(|(n, _)| n == name).map(|(_, d)| *d) else {
            continue;
        };
        let pool = pool_of(name);
        let be = last(name);
        let pooled = |n: &str| format!("{}/{}", pool_of(n), last(n));
        let path = if count(&last, name) == 1 {
            format!("{BE_DIR}/{be}")
        } else if count(&pooled, name) == 1 {
            format!("{BE_DIR}/{pool}/{be}")
        } else {
            format!("{BE_DIR}/{name}")
        };
        let mut locked = s(d, "keystatus").as_deref() == Some("unavailable");
        let mut children = Vec::new();
        let mut kids: Vec<&(String, &Value)> = by_name.iter().filter(|(n, _)| n.starts_with(&format!("{name}/"))).collect();
        kids.sort_by(|a, b| a.0.cmp(&b.0));
        for (kn, kd) in kids {
            locked |= s(kd, "keystatus").as_deref() == Some("unavailable");
            let mp = s(kd, "mountpoint").unwrap_or_default();
            let canmount = s(kd, "canmount").unwrap_or_else(|| "on".into());
            let rel = &kn[name.len() + 1..];
            let at = if canmount == "off" || matches!(mp.as_str(), "none" | "-" | "") {
                None
            } else if mp.starts_with('/') && mp != "/" {
                Some(format!("{path}{}", mp.trim_end_matches('/')))
            } else {
                Some(format!("{path}/{rel}"))
            };
            children.push(BeDataset { dataset: kn.clone(), mountpoint: mp, origin: s(kd, "origin"), path: at });
        }
        out.push(BootEnv {
            pool,
            name: be,
            dataset: name.clone(),
            mountpoint: s(d, "mountpoint").unwrap_or_default(),
            origin: s(d, "origin"),
            bootfs: bootfs.contains(name),
            mounted: s(d, "mounted").as_deref() == Some("yes"),
            rule: *rule,
            locked,
            path,
            children,
        });
    }
    out
}

/// The inactive BEs of `tools` (list, less the booted ones).
#[cfg(test)]
pub fn inactive(tools: &Value) -> Vec<BootEnv> {
    list(tools).into_iter().filter(BootEnv::inactive).collect()
}

/// Each TrueNAS pool whose bootfs BE isn't the one mounted (booted): (the
/// bootfs's dataset, the mounted one's).  Its bootfs gets no backup source
/// (it isn't inactive), and load would mount it as `/` with the running
/// system's files: the dump warns and load refuses it.
pub fn bootfs_not_booted(bes: &[BootEnv]) -> Vec<(String, String)> {
    let mut out = Vec::new();
    for b in bes.iter().filter(|b| b.rule == Rule::TrueNas && b.bootfs && !b.mounted) {
        if let Some(m) = bes.iter().find(|m| m.rule == Rule::TrueNas && m.pool == b.pool && m.mounted) {
            out.push((b.dataset.clone(), m.dataset.clone()));
        }
    }
    out
}

/// The dump's side, on the system FAM (crate::other::Family, None for
/// Linux): every BE, the backup sources of the inactive ones and the
/// warnings.  Linux only: the sources' read-only mounts are Linux's
/// mount.zfs (`zfsutil`) under its /run; elsewhere (FreeBSD's bectl,
/// illumos' beadm) the inactive BEs come back empty as before
/// (docs/design/openindiana.md, decision 3), and nothing is listed.
pub fn dump_sources(fam: Option<crate::other::Family>, tools: &Value) -> (Vec<BootEnv>, Vec<BackupSource>, Vec<String>) {
    if fam.is_some() {
        return (Vec::new(), Vec::new(), Vec::new());
    }
    let bes = list(tools);
    let (src, warnings) = backup_sources(&bes);
    (bes, src, warnings)
}

/// storage.backup_sources' entries for BES's inactive BEs that can be read,
/// parents first, and a warning for each that can't (its key wasn't
/// loaded), and for a TrueNAS bootfs that isn't the booted BE.
pub fn backup_sources(bes: &[BootEnv]) -> (Vec<BackupSource>, Vec<String>) {
    let mut out = Vec::new();
    let mut warnings = Vec::new();
    for (bootfs, booted) in bootfs_not_booted(bes) {
        let pool = bootfs.split('/').next().unwrap_or("");
        warnings.push(format!(
            "ZFS pool {pool}: its bootfs is {bootfs}, but the booted boot environment is {booted}: a backup has no source for {bootfs}, and bko-holt load refuses this dump; boot {bootfs} (or `zpool set bootfs={booted} {pool}`) and dump again"
        ));
    }
    for be in bes.iter().filter(|b| b.inactive()) {
        if be.locked {
            warnings.push(format!(
                "ZFS boot environment {} ({}) is encrypted and its key isn't loaded, so a backup can't read it; load makes its datasets empty (`zfs load-key -r {}` and dump again to back it up)",
                be.name, be.dataset, be.dataset
            ));
            continue;
        }
        let hinted = |path: &str, ds: &str, legacy: bool| BackupSource {
            path: path.to_string(),
            from: None,
            device: None,
            fstype: "zfs".into(),
            source: ds.to_string(),
            subvol: None,
            mount: Some(MountHint { device: ds.to_string(), fstype: "zfs".into(), options: ro_options(legacy) }),
            note: Some(note(&be.name)),
        };
        out.push(hinted(&be.path, &be.dataset, be.legacy()));
        for c in &be.children {
            if let Some(p) = &c.path {
                out.push(hinted(p, &c.dataset, c.legacy()));
            }
        }
    }
    out.sort_by(|a, b| a.path.cmp(&b.path));
    (out, warnings)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::json::Obj;

    /// A dataset as the dump's tools.datasets lists it.
    fn ds(name: &str, mp: &str, canmount: &str, mounted: &str, props: &[(&str, &str)]) -> Value {
        let mut p = Obj::new();
        for (k, v) in props {
            p.put(k, Obj::new().set("value", *v).set("source", "local"));
        }
        Obj::new()
            .set("name", name)
            .set("type", "filesystem")
            .set("mountpoint", mp)
            .set("canmount", canmount)
            .set("mounted", mounted)
            .set("keystatus", Value::Null)
            .set("origin", Value::Null)
            .set("properties", p)
            .build()
    }

    fn tools(pools: &[(&str, &str)], datasets: Vec<Value>) -> Value {
        let pools: Vec<Value> = pools.iter().map(|(n, b)| Obj::new().set("name", *n).set("properties", Obj::new().set("bootfs", *b)).build()).collect();
        Obj::new().set("imported_pools", pools).set("datasets", datasets).build()
    }

    /// ZFSBootMenu's rule: mountpoint=/ and not mounted, wherever it is;
    /// active=off left out (inherited too); a legacy one with active=on
    /// in; the booted one and containers never; a name two pools share by
    /// POOL/NAME.
    #[test]
    fn zfsbootmenu_rule() {
        let t = tools(
            &[("zroot", "zroot/ROOT/void"), ("tank", "-")],
            vec![
                ds("zroot", "none", "off", "no", &[]),
                ds("zroot/ROOT", "none", "off", "no", &[]),
                ds("zroot/ROOT/void", "/", "noauto", "yes", &[]),
                ds("zroot/ROOT/void-old", "/", "noauto", "no", &[]),
                ds("zroot/ROOT/void-old/var", "/var", "noauto", "no", &[]),
                ds("zroot/ROOT/void-old/var/log", "/var/log", "noauto", "no", &[]),
                ds("zroot/ROOT/void-old/opt", "legacy", "on", "no", &[]),
                ds("zroot/ROOT/void-old/box", "none", "on", "no", &[]),
                ds("zroot/ROOT/hidden", "/", "noauto", "no", &[(ZBM_ACTIVE, "off")]),
                ds("zroot/other", "none", "off", "no", &[(ZBM_ACTIVE, "off")]),
                ds("zroot/other/inherits-off", "/", "noauto", "no", &[]),
                ds("zroot/legacy-be", "legacy", "noauto", "no", &[(ZBM_ACTIVE, "on")]),
                ds("zroot/legacy-not", "legacy", "noauto", "no", &[]),
                ds("zroot/home", "/home", "on", "yes", &[]),
                ds("tank", "/tank", "on", "yes", &[]),
                ds("tank/void-old", "/", "noauto", "no", &[]),
                ds("tank/container", "/", "off", "no", &[]),
            ],
        );
        let all = list(&t);
        let names: Vec<(&str, &str, bool)> = all.iter().map(|b| (b.dataset.as_str(), b.path.as_str(), b.inactive())).collect();
        assert_eq!(
            names,
            vec![
                ("tank/void-old", "/run/bko-be/tank/void-old", true),
                ("zroot/ROOT/void", "/run/bko-be/void", false),
                ("zroot/ROOT/void-old", "/run/bko-be/zroot/void-old", true),
                ("zroot/legacy-be", "/run/bko-be/legacy-be", true),
            ]
        );
        assert!(all.iter().all(|b| b.rule == Rule::ZfsBootMenu));
        let old = &all[2];
        let kids: Vec<(&str, Option<&str>)> = old.children.iter().map(|c| (c.dataset.as_str(), c.path.as_deref())).collect();
        assert_eq!(
            kids,
            vec![
                ("zroot/ROOT/void-old/box", None),
                ("zroot/ROOT/void-old/opt", Some("/run/bko-be/zroot/void-old/opt")),
                ("zroot/ROOT/void-old/var", Some("/run/bko-be/zroot/void-old/var")),
                ("zroot/ROOT/void-old/var/log", Some("/run/bko-be/zroot/void-old/var/log")),
            ]
        );
        assert!(old.holds("zroot/ROOT/void-old/var") && !old.holds("zroot/ROOT/void-older"));
        let (src, warns) = backup_sources(&all);
        assert!(warns.is_empty());
        let got: Vec<(&str, &str, &str)> = src.iter().map(|b| (b.path.as_str(), b.source.as_str(), b.mount.as_ref().unwrap().options.as_str())).collect();
        assert_eq!(
            got,
            vec![
                ("/run/bko-be/legacy-be", "zroot/legacy-be", "ro,nodev,nosuid,noexec"),
                ("/run/bko-be/tank/void-old", "tank/void-old", "ro,zfsutil,nodev,nosuid,noexec"),
                ("/run/bko-be/zroot/void-old", "zroot/ROOT/void-old", "ro,zfsutil,nodev,nosuid,noexec"),
                ("/run/bko-be/zroot/void-old/opt", "zroot/ROOT/void-old/opt", "ro,nodev,nosuid,noexec"),
                ("/run/bko-be/zroot/void-old/var", "zroot/ROOT/void-old/var", "ro,zfsutil,nodev,nosuid,noexec"),
                ("/run/bko-be/zroot/void-old/var/log", "zroot/ROOT/void-old/var/log", "ro,zfsutil,nodev,nosuid,noexec"),
            ]
        );
        assert!(src.iter().all(|b| b.from.is_none() && b.fstype == "zfs" && b.mount.as_ref().unwrap().fstype == "zfs"));
        assert_eq!(src[2].note.as_deref(), Some("ZFS boot environment void-old (not booted): a backup mounts it read-only here"));
        // The pool's bootfs not booted (another BE picked in ZFSBootMenu's
        // menu): still inactive.
        let t2 = tools(&[("zroot", "zroot/ROOT/void-old")], vec![ds("zroot/ROOT/void", "/", "noauto", "yes", &[]), ds("zroot/ROOT/void-old", "/", "noauto", "no", &[])]);
        assert_eq!(inactive(&t2).iter().map(|b| b.dataset.as_str()).collect::<Vec<_>>(), vec!["zroot/ROOT/void-old"]);
    }

    /// TrueNAS's rule: boot-pool/ROOT's children, legacy roots; the bootfs
    /// is the booted one; descendants at the BE's path plus their own
    /// mountpoint; a locked BE warned of and left out.
    #[test]
    fn truenas_rule_and_locked() {
        let mut v = vec![
            ds("boot-pool", "none", "off", "no", &[]),
            ds("boot-pool/ROOT", "none", "off", "no", &[]),
            ds("boot-pool/grub", "legacy", "off", "yes", &[]),
            ds("boot-pool/ROOT/25.10.7", "legacy", "noauto", "no", &[("truenas:kernel_version", "6.6.44-production+truenas")]),
            ds("boot-pool/ROOT/25.10.7/usr", "/usr", "noauto", "no", &[]),
            ds("boot-pool/ROOT/25.10.7/var/ca-certificates", "/var/local/ca-certificates", "noauto", "no", &[]),
            ds("boot-pool/ROOT/25.10.8", "legacy", "noauto", "yes", &[]),
            ds("boot-pool/ROOT/25.10.8/usr", "/usr", "noauto", "yes", &[]),
        ];
        let t = tools(&[("boot-pool", "boot-pool/ROOT/25.10.8")], v.clone());
        let all = list(&t);
        assert_eq!(all.len(), 2);
        assert!(all.iter().all(|b| b.rule == Rule::TrueNas && b.legacy()));
        assert!(all[1].bootfs && !all[1].inactive() && all[0].inactive());
        let paths: Vec<Option<&str>> = all[0].children.iter().map(|c| c.path.as_deref()).collect();
        assert_eq!(paths, vec![Some("/run/bko-be/25.10.7/usr"), Some("/run/bko-be/25.10.7/var/local/ca-certificates")]);
        let (src, _) = backup_sources(&all);
        assert_eq!(src.len(), 3);
        assert_eq!(src[0].mount.as_ref().unwrap().options, "ro,nodev,nosuid,noexec");
        assert_eq!(src[1].mount.as_ref().unwrap().options, "ro,zfsutil,nodev,nosuid,noexec");
        // Its key not loaded: no source, a warning.
        if let Value::Obj(o) = &mut v[4] {
            for (k, x) in o.iter_mut() {
                if k == "keystatus" {
                    *x = Value::from("unavailable");
                }
            }
        }
        let all = list(&tools(&[("boot-pool", "boot-pool/ROOT/25.10.8")], v));
        assert!(all[0].locked);
        let (src, warns) = backup_sources(&all);
        assert!(src.is_empty());
        assert_eq!(warns.len(), 1);
        assert!(warns[0].starts_with("ZFS boot environment 25.10.7 (boot-pool/ROOT/25.10.7) is encrypted"), "{warns:?}");
    }
}
