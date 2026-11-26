//! `load`'s side of the ZFS boot environments a backup reaches through
//! their own backup sources (crate::zfs_be; docs/design/truenas.md,
//! decisions 2-4).  Every BE's datasets are made in format as any dataset
//! is.  The mount stage mounts each inactive one the dump lists a source
//! for at the source's path under the target (`TARGET/run/bko-be/NAME`, its
//! descendants below), so the archiver restores it from that source; the
//! boot stage unmounts them first (deepest first) and removes the mount
//! points it made (rmdir), so the restored root's /run is as it was.  A BE
//! the dump has no source for (its key wasn't loaded, or an older dump)
//! comes back empty, as before.  Encrypted datasets are made with their key
//! loaded (keylocation=file://, Builder::ds_props), so they mount here as
//! any other.
//!
//! Also here: legacy-mountpoint datasets the source had mounted at a path
//! that is a backup source (TrueNAS's boot-pool/grub at /boot/grub, its
//! .system at /var/db/system), mounted there with the system's mount; the
//! note for datasets that were clones (they come back as full copies, and
//! no snapshot is remade); and `readonly=on`, held back when datasets are
//! made (nothing could be restored into one) and set again at the end of
//! the boot stage.

use super::commands::{Builder, Cmd};
use super::shell_quote;
use crate::json::Value;
use crate::zfs_be::{self, BootEnv};

fn s(v: &Value, k: &str) -> Option<String> {
    v.get(k).and_then(Value::as_str).map(str::to_string)
}

/// One dataset of an inactive BE that the dump lists as a backup source.
#[derive(Debug, Clone, PartialEq)]
pub struct BeMount {
    /// The source's path (`/run/bko-be/25.10.6/usr`).
    pub path: String,
    pub dataset: String,
    pub legacy: bool,
}

impl Builder<'_> {
    fn zfs_tools(&self) -> Value {
        self.layout
            .v
            .at(&["storage", "zfs", "tools"])
            .cloned()
            .unwrap_or(Value::Null)
    }

    fn dataset_mountpoint(&self, ds: &str) -> Option<String> {
        self.layout
            .arr(&["storage", "zfs", "tools", "datasets"])
            .iter()
            .find(|d| s(d, "name").as_deref() == Some(ds))
            .and_then(|d| s(d, "mountpoint"))
    }

    /// The inactive BEs' datasets the dump has backup sources for: entries
    /// with no `from`, a zfs mount hint and a path under /run/bko-be, by
    /// path.  Linux only (crate::zfs_be::dump_sources): on another system
    /// the inactive BEs come back empty, as before.  Worked out once, in
    /// commands::build (Builder::be_mounts).
    pub(crate) fn find_be_mounts(&self) -> Vec<BeMount> {
        if self.fam.is_some() {
            return Vec::new();
        }
        let mut out: Vec<BeMount> = self
            .layout
            .arr(&["storage", "backup_sources"])
            .iter()
            .filter(|b| b.get("from").is_none_or(Value::is_null))
            .filter(|b| b.at(&["mount", "fstype"]).and_then(Value::as_str) == Some("zfs"))
            .filter_map(|b| {
                let path = s(b, "path")?;
                let ds = b
                    .at(&["mount", "device"])
                    .and_then(Value::as_str)?
                    .to_string();
                path.starts_with(&format!("{}/", zfs_be::BE_DIR)).then(|| {
                    let legacy = self.dataset_mountpoint(&ds).as_deref() == Some("legacy");
                    BeMount {
                        path,
                        dataset: ds,
                        legacy,
                    }
                })
            })
            .collect();
        out.sort_by(|a, b| a.path.cmp(&b.path));
        out
    }

    /// The BEs restored through their sources (their root has one), once
    /// find_be_mounts has run (Builder::restored_bes).
    pub(crate) fn find_restored_bes(&self) -> Vec<BootEnv> {
        if self.be_mounts.is_empty() {
            return Vec::new();
        }
        zfs_be::list(&self.zfs_tools())
            .into_iter()
            .filter(|b| b.inactive() && self.be_mounts.iter().any(|m| m.dataset == b.dataset))
            .collect()
    }

    /// The BEs restored through their sources (Builder::be_restored).
    pub(crate) fn restored_bes(&self) -> Vec<BootEnv> {
        self.be_restored.clone()
    }

    /// Whether DS is a dataset of a BE restored through its sources: the
    /// mount stage's ZFS loop leaves it to be_mount_cmds.
    pub(crate) fn in_restored_be(&self, ds: &str) -> bool {
        self.be_restored.iter().any(|b| b.holds(ds))
    }

    /// A TrueNAS pool whose bootfs isn't the BE the dump was made on: that
    /// BE has no backup source, and the mount stage would mount it as `/`
    /// with the booted one's files (crate::zfs_be::bootfs_not_booted).
    pub(crate) fn be_problems(&mut self) {
        if self.fam.is_some() {
            return;
        }
        for (bootfs, booted) in zfs_be::bootfs_not_booted(&zfs_be::list(&self.zfs_tools())) {
            self.problems.push(format!(
                "ZFS: the pool's bootfs is {bootfs}, but the dump was made on boot environment {booted}: the backup has no source for {bootfs}, and the restore would put {booted}'s files under {bootfs}'s name; boot {bootfs} (or make {booted} the bootfs) and dump again"
            ));
        }
    }

    /// The mount stage's mounts of the restored BEs: (path in the restored
    /// tree, command).  mount.zfs wants zfsutil for a dataset whose
    /// mountpoint isn't legacy, and refuses it for a legacy one.
    pub(crate) fn be_mount_cmds(&self) -> Vec<(String, Cmd)> {
        let t = self.opts.target.clone();
        // (find_be_mounts lists none off Linux; the system's own mount
        // flag regardless, as legacy_mounts does.)
        let fs_flag = if self.fam == Some(crate::other::Family::Illumos) {
            "-F"
        } else {
            "-t"
        };
        self.be_mounts
            .iter()
            .map(|m| {
                let dir = format!("{t}{}", m.path);
                let ds = self.zname(&m.dataset);
                let cmd = if m.legacy || self.fam.is_some() {
                    Cmd::new(&["mount", fs_flag, "zfs", &ds, &dir])
                } else {
                    Cmd::new(&["mount", fs_flag, "zfs", "-o", "zfsutil", &ds, &dir])
                };
                (
                    m.path.clone(),
                    cmd.why(format!(
                        "{}: an inactive boot environment's, restored from its backup source here",
                        m.dataset
                    )),
                )
            })
            .collect()
    }

    /// The boot stage's first steps: the restored BEs unmounted, deepest
    /// first, then the mount points the mount stage made removed (rmdir:
    /// each is empty once its dataset is gone), up to /run/bko-be.  Both
    /// guarded, so a second run of the stage passes them.
    pub(crate) fn be_unmount_cmds(&self) -> Vec<Cmd> {
        let mounts = &self.be_mounts;
        if mounts.is_empty() {
            return Vec::new();
        }
        let t = self.opts.target.clone();
        let mut out = Vec::new();
        for m in mounts.iter().rev() {
            let d = shell_quote(&format!("{t}{}", m.path));
            out.push(Cmd::new(&[
                "sh",
                "-c",
                &format!("! mountpoint -q {d} || umount {d}"),
            ]));
        }
        // The BEs' roots' mount points and the directories above them.
        let mut dirs: Vec<String> = Vec::new();
        for m in mounts {
            let mut p = m.path.as_str();
            while p.len() > zfs_be::BE_DIR.len() {
                if !mounts
                    .iter()
                    .any(|o| o.path != p && p.starts_with(&format!("{}/", o.path)))
                    && !dirs.iter().any(|x| x == p)
                {
                    dirs.push(p.to_string());
                }
                p = p.rsplit_once('/').map_or("", |(a, _)| a);
            }
        }
        dirs.push(zfs_be::BE_DIR.to_string());
        dirs.sort_by(|a, b| {
            b.matches('/')
                .count()
                .cmp(&a.matches('/').count())
                .then(b.cmp(a))
        });
        for p in dirs {
            let d = shell_quote(&format!("{t}{p}"));
            out.push(Cmd::new(&[
                "sh",
                "-c",
                &format!("[ ! -d {d} ] || rmdir {d}"),
            ]));
        }
        if let Some(c) = out.first_mut() {
            c.why = Some(format!(
                "the inactive boot environments, restored: unmounted, and {t}{} removed",
                zfs_be::BE_DIR
            ));
        }
        out
    }

    /// Legacy-mountpoint datasets the source had mounted at a path that is
    /// a backup source (TrueNAS's boot-pool/grub at /boot/grub), which `zfs
    /// mount` won't mount: with the system's mount, at that path.  HAVE are
    /// the paths something mounts already.
    ///
    /// The path is the mount's, not the dataset's name (TrueNAS's
    /// .system/netdata-UUID at /var/db/system/netdata).  A dataset the
    /// source mounted at several paths (.system/cores, also at
    /// /var/lib/systemd/coredump) is mounted at the first only, with a
    /// note: its files are restored once, and the restored system mounts it
    /// again where it wants it.
    pub(crate) fn legacy_mounts(&mut self, have: &[String]) -> Vec<(String, Cmd)> {
        let t = self.opts.target.clone();
        let fs_flag = if self.fam == Some(crate::other::Family::Illumos) {
            "-F"
        } else {
            "-t"
        };
        let mut out: Vec<(String, Cmd)> = Vec::new();
        let mut done: Vec<(String, String)> = Vec::new();
        for b in self.layout.arr(&["storage", "backup_sources"]).to_vec() {
            let (Some(path), Some(ds)) = (s(&b, "path"), s(&b, "source")) else {
                continue;
            };
            if s(&b, "fstype").as_deref() != Some("zfs")
                || b.get("from").is_none_or(Value::is_null)
                || path == "/"
                || !path.starts_with('/')
            {
                continue;
            }
            if self.dataset_mountpoint(&ds).as_deref() != Some("legacy")
                || have.contains(&path)
                || out.iter().any(|(p, _)| *p == path)
            {
                continue;
            }
            if done.iter().any(|(d, _)| *d == ds) {
                continue; // (an older dump lists it at each path: the second below)
            }
            let dir = format!("{t}{path}");
            out.push((
                path.clone(),
                Cmd::new(&["mount", fs_flag, "zfs", &self.zname(&ds), &dir]).why(format!(
                    "{ds}: mountpoint=legacy, mounted at {path} on the source"
                )),
            ));
            done.push((ds, path));
        }
        // Its other mounts on the source, as storage.mounts has them.
        for (ds, path) in &done {
            let mut also: Vec<String> = Vec::new();
            for m in self.layout.arr(&["storage", "mounts"]) {
                if s(m, "fstype").as_deref() == Some("zfs")
                    && s(m, "source").as_deref() == Some(ds.as_str())
                {
                    if let Some(tg) = s(m, "target").filter(|tg| tg != path && !also.contains(tg)) {
                        also.push(tg);
                    }
                }
            }
            if !also.is_empty() {
                self.notes.push(format!("ZFS dataset {ds} was also mounted at {} on the source: mounted at {path} only here (its files are restored once; the restored system mounts it again)", also.join(", ")));
            }
        }
        out
    }

    /// Whether this layout's BEs come back filled (it has BE sources, or
    /// is TrueNAS's): then the clones among their datasets are noted.
    fn be_restore(&self) -> bool {
        !self.be_mounts.is_empty()
            || self
                .layout
                .v
                .at(&["boot", "truenas"])
                .is_some_and(|v| !v.is_null())
    }

    /// A note for each BE some of whose datasets were clones: they come
    /// back as plain datasets, full copies, and no snapshot is remade.
    pub(crate) fn clone_notes(&mut self) {
        if !self.be_restore() {
            return;
        }
        let bes = zfs_be::list(&self.zfs_tools());
        for be in &bes {
            let mut what: Vec<String> = Vec::new();
            let mut from: Vec<String> = Vec::new();
            let mut add = |rel: String, origin: &str| {
                what.push(rel);
                let ds = origin.split('@').next().unwrap_or(origin);
                let who = bes
                    .iter()
                    .find(|o| o.holds(ds))
                    .map(|o| format!("{}'s snapshots", o.name))
                    .unwrap_or_else(|| origin.to_string());
                if !from.contains(&who) {
                    from.push(who);
                }
            };
            if let Some(o) = &be.origin {
                add("its root".to_string(), o);
            }
            for c in &be.children {
                if let Some(o) = &c.origin {
                    add(c.dataset[be.dataset.len() + 1..].to_string(), o);
                }
            }
            if what == ["its root"] {
                // A BE that is a clone itself (ZFSBootMenu's void-6.12).
                self.notes.push(format!("ZFS: {} ({}) was a clone of {}; it comes back as a full copy (no snapshot is remade)", be.name, be.dataset, from.join(", ")));
            } else if !what.is_empty() {
                self.notes.push(format!("ZFS: {}'s datasets {} were clones of {}; they come back as full copies (no snapshot is remade)", be.name, what.join(", "), from.join(", ")));
            }
        }
    }

    /// The datasets whose readonly=on was held back, made read-only again:
    /// the last of the boot stage, before close exports the pools.
    pub(crate) fn readonly_sets(&self) -> Vec<Cmd> {
        let mut out: Vec<Cmd> = self
            .readonly_later
            .iter()
            .map(|ds| Cmd::new(&["zfs", "set", "readonly=on", ds]))
            .collect();
        if let Some(c) = out.first_mut() {
            c.why = Some("readonly=on as on the source, held back until now so that the restore and this stage could write".into());
        }
        out
    }

    /// Restore globs leaving out the backup sources --pools left untouched
    /// (layout.rs only_pools): the pools' datasets and everything else on
    /// their disks, which aren't made, so their files would land in the
    /// restored tree's own.
    pub(crate) fn left_out_pool_globs(&self) -> Vec<String> {
        self.layout
            .arr(&["storage", "zfs", "left_out_paths"])
            .iter()
            .filter_map(|p| {
                p.as_str().map(|p| {
                    format!(
                        "!{}/**",
                        super::shared::glob_literal(p.trim_end_matches('/'))
                    )
                })
            })
            .collect()
    }
}
