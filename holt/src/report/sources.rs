//! `storage.backup_sources`: the filesystems a whole-system backup reads,
//! and where each is mounted for it.

use crate::json::{Obj, Value};
use crate::mounts::{self, Mount};
use std::collections::BTreeMap;

/// One filesystem a whole-system backup reads: the tree it's mounted in
/// (`path`, fstab's mount point), where the running system has it (`from`),
/// and what it is.  A filesystem the running system doesn't mount (`ABRoot`'s
/// `init` and other root, `crate::abroot`; an inactive ZFS boot environment's
/// datasets, `crate::zfs_be`) has no `from`: its `mount` says
/// how a backup mounts it at `path` (read-only) for the backup, and `note`
/// says so in words.  `mount` and `note` are in the JSON only when set.
#[derive(Debug, Clone, PartialEq)]
pub struct BackupSource {
    pub path: String,
    pub from: Option<String>,
    pub device: Option<String>,
    pub fstype: String,
    pub source: String,
    pub subvol: Option<String>,
    pub mount: Option<MountHint>,
    pub note: Option<String>,
}

/// The mount a backup makes for a `BackupSource` without a `from`:
/// `mount -t FSTYPE -o OPTIONS DEVICE PATH`.
#[derive(Debug, Clone, PartialEq)]
pub struct MountHint {
    pub device: String,
    pub fstype: String,
    pub options: String,
}

impl BackupSource {
    pub fn to_json(&self) -> Value {
        Obj::new()
            .set("path", self.path.as_str())
            .set("from", self.from.clone())
            .set("device", self.device.clone())
            .set("fstype", self.fstype.as_str())
            .set("source", self.source.as_str())
            .set("subvol", self.subvol.clone())
            .set_some(
                "mount",
                self.mount.as_ref().map(|m| {
                    Obj::new()
                        .set("device", m.device.as_str())
                        .set("fstype", m.fstype.as_str())
                        .set("options", m.options.as_str())
                        .build()
                }),
            )
            .set_some("note", self.note.clone())
            .build()
    }
}

/// `storage.backup_sources`: the filesystems a whole-system backup reads,
/// laid out as fstab mounts them, one per path, sorted by path (parents
/// first).  Aliases (`mounts::alias_of`) are left out: their files are in the
/// mount they're a directory of.  On an ostree system each mount takes the
/// mount point of the fstab entry it is, matched by (device, subvol=) since
/// the targets differ (fstab's `/` is the running /sysroot, `/home` is
/// /var/home), so the tree is the one `load` mounts from fstab
/// (docs/design/bazzite.md, "One tree convention").  Elsewhere the path is
/// the mount's own target.  When two mounts share a path, the later one
/// (the one on top) wins.  `fstab` is each entry with the device it
/// resolves to.
pub fn backup_sources(
    mounts: &[&Mount],
    device: &dyn Fn(&Mount) -> Option<String>,
    fstab: &[(&mounts::FstabEntry, Option<String>)],
    ostree: bool,
) -> Vec<BackupSource> {
    let norm = |s: &str| s.trim_matches('/').to_string();
    let mut out: BTreeMap<String, BackupSource> = BTreeMap::new();
    for m in mounts {
        if mounts::alias_of(m, mounts).is_some() {
            continue;
        }
        let dev = device(m);
        let subvol = (m.fstype == "btrfs").then(|| m.btrfs_subvol()).flatten();
        let path = if !ostree {
            m.target.clone()
        } else {
            let found = fstab.iter().find(|(e, r)| {
                if e.vfstype == "swap" || !e.file.starts_with('/') || dev.is_none() || *r != dev {
                    return false;
                }
                if m.fstype != "btrfs" {
                    return m.root == "/";
                }
                let opt = |k: &str| {
                    e.options
                        .split(',')
                        .find_map(|o| o.strip_prefix(k))
                        .map(str::to_string)
                };
                match (opt("subvol="), opt("subvolid=")) {
                    (Some(sv), _) => Some(norm(&sv)) == subvol.as_deref().map(norm),
                    (None, Some(id)) => m
                        .super_options
                        .split(',')
                        .any(|o| o.strip_prefix("subvolid=") == Some(id.as_str())),
                    // The default subvolume, the top level unless set.
                    (None, None) => subvol.as_deref().map(norm).as_deref() == Some(""),
                }
            });
            found
                .map(|(e, _)| e.file.clone())
                .unwrap_or_else(|| m.target.clone())
        };
        let path = if path.len() > 1 {
            path.trim_end_matches('/').to_string()
        } else {
            path
        };
        out.insert(
            path.clone(),
            BackupSource {
                path,
                from: Some(m.target.clone()),
                device: dev,
                fstype: m.fstype.clone(),
                source: m.source.clone(),
                subvol,
                mount: None,
                note: None,
            },
        );
    }
    out.into_values().collect()
}

/// The fstab entries `backup_sources` has no path for: each one a running
/// system mounts (a filesystem, not swap, `noauto`, a bind or a pseudo
/// filesystem) whose mount point no source took, as `FILE (SPEC -> DEVICE)`.
/// On an ostree system each is a mount that stays at its running path (or a
/// filesystem that isn't mounted), so the backup's tree isn't fstab's.
pub fn unmatched_fstab(
    sources: &[BackupSource],
    fstab: &[(&mounts::FstabEntry, Option<String>)],
) -> Vec<String> {
    let trim = |p: &str| {
        if p.len() > 1 {
            p.trim_end_matches('/').to_string()
        } else {
            p.to_string()
        }
    };
    fstab
        .iter()
        .filter(|(e, _)| {
            let opts: Vec<&str> = e.options.split(',').collect();
            e.file.starts_with('/')
                && e.vfstype != "swap"
                && !mounts::PSEUDO.contains(&e.vfstype.as_str())
                && !opts
                    .iter()
                    .any(|o| matches!(*o, "noauto" | "bind" | "rbind"))
                && !sources.iter().any(|s| s.path == trim(&e.file))
        })
        .map(|(e, r)| {
            format!(
                "{} ({} -> {})",
                e.file,
                e.spec,
                r.as_deref().unwrap_or("unresolved")
            )
        })
        .collect()
}

/// The mounts that aren't backup sources after all:
///   - with --only-disk (`ONLY_DISK`), a block device's mount that isn't on a
///     kept disk (its device isn't in the dump: the backup drive, a USB
///     stick), as `BackShot1`'s own tree (bs1-common `internal_mounts`) leaves
///     them out;
///   - a runtime store of ZFS raw/hex key files under /run that fstab
///     doesn't mount (Ubuntu's keystore, LUKS on the zvol POOL/keystore,
///     which the initramfs mounts at /run/keystore/POOL): it is the deepest
///     mount above an encryption root's file:// keylocation, its device is in
///     the dump, and load writes a new key there (load/commands.rs
///     `keystores()`, which writes keys only where this leaves the mount
///     out, and otherwise asks for --key-file), so the old key isn't backed
///     up, nor restored into the root's /run.  Such a mount anywhere else
///     (a .mount unit at /keys) is backed up.
pub fn not_backed_up(
    sources: &mut Vec<BackupSource>,
    only_disk: bool,
    zfs_tools: &Value,
    fstab: &[(&mounts::FstabEntry, Option<String>)],
) {
    fn trim_slash(p: &str) -> &str {
        let t = p.trim_end_matches('/');
        if t.is_empty() && p.starts_with('/') {
            "/"
        } else {
            t
        }
    }
    let all_paths: Vec<String> = sources
        .iter()
        .map(|s| trim_slash(&s.path).to_string())
        .collect();
    if only_disk {
        sources.retain(|s| s.device.is_some() || !s.source.starts_with("/dev/"));
    }
    let keys: Vec<String> = zfs_tools
        .get("datasets")
        .and_then(Value::as_array)
        .into_iter()
        .flatten()
        .filter(|d| {
            d.get("encryptionroot").is_some()
                && d.get("encryptionroot") == d.get("name")
                && matches!(
                    d.get("keyformat").and_then(Value::as_str),
                    Some("raw" | "hex") | None
                )
        })
        .filter_map(|d| d.get("keylocation")?.as_str()?.strip_prefix("file://"))
        .filter(|k| k.starts_with('/') && !k.contains('\0') && !k.split('/').any(|c| c == ".."))
        .map(str::to_string)
        .collect();
    let keystore_mounts: Vec<&str> = keys
        .iter()
        .filter_map(|k| {
            all_paths
                .iter()
                .map(String::as_str)
                .filter(|&p| p == "/" || k.starts_with(&format!("{p}/")))
                .max_by_key(|p| p.len())
        })
        .collect();
    let in_fstab = |p: &str| {
        fstab
            .iter()
            .any(|(e, _)| trim_slash(&e.file) == trim_slash(p))
    };
    sources.retain(|s| {
        let p = trim_slash(&s.path);
        let is_runtime_keystore = s.device.is_some()
            && (p == "/run" || p.starts_with("/run/"))
            && keystore_mounts.iter().any(|&m| m == p);
        s.fstype == "zfs" || p == "/" || in_fstab(p) || !is_runtime_keystore
    });
}

/// A ZFS dataset mounted at more than one path, its whole tree each time
/// (`TrueNAS`'s boot-pool/.system/cores, at /var/db/system/cores and
/// /var/lib/systemd/coredump): backed up once, at its first path, whose
/// note names the others; load mounts it there only (the restored system
/// mounts it again where it wants it).
pub fn zfs_mounted_twice(sources: &mut Vec<BackupSource>) {
    let mut i = 0;
    while i < sources.len() {
        let s = &sources[i];
        if s.fstype != "zfs" || s.from.is_none() {
            i += 1;
            continue;
        }
        let ds = s.source.clone();
        let others: Vec<String> = sources[i + 1..]
            .iter()
            .filter(|o| o.fstype == "zfs" && o.from.is_some() && o.source == ds)
            .map(|o| o.path.clone())
            .collect();
        if !others.is_empty() {
            sources.retain(|o| !(o.fstype == "zfs" && o.source == ds && others.contains(&o.path)));
            sources[i].note = Some(format!(
                "ZFS dataset {ds} is also mounted at {}: backed up here only, once",
                others.join(", ")
            ));
        }
        i += 1;
    }
}
