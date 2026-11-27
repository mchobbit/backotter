//! `SteamOS` 3 (docs/design/steamos.md): how to tell a system runs it, and
//! what the dump records of its two slots and their boot setup
//! (boot.steamos).
//!
//! Every install has eight GPT partitions found by partlabel: the shared
//! `esp` (the chainloader steamcl and each slot's boot record, `SteamOS/conf/
//! A.conf` and `B.conf`), `efi-A`/`efi-B` (vfat, each slot's GRUB and the
//! partsets that map partition names to PARTUUIDs), `rootfs-A`/`rootfs-B`
//! (btrfs, read-only by the `ro` property on the top level), `var-A`/`var-B`
//! (ext4, each slot's /var with the /etc overlay's upper) and `home`.  The
//! running system mounts the booted ("self") slot's three at `/`, `/var` and
//! `/efi` (an automount), the ESP at `/esp` (an automount) and home at
//! `/home`; the other slot's three are in no mount and no fstab line.
//!
//! The backup tree is post-install's shape: the booted slot at `/`, `/var`
//! and `/efi`, the ESP at `/esp`, home at `/home`, and the other slot as a
//! chroot at `/mnt`, `/mnt/var` and `/mnt/efi`.  dump lists all eight in
//! `storage.backup_sources` (replacing the mounts' list), the other slot's
//! three with the read-only mount a backup makes for them.  dump reads them
//! through read-only mounts of its own at `/run/bko-holt/steamos-PARTLABEL`
//! (by partlabel: an installer install's filesystem labels repeat), removed
//! at once, as `crate::abroot` does.

use crate::json::{Obj, Value};
use crate::report::{BackupSource, MountHint};
use crate::sys::{RUN_DIR, RoMountWords, Sys, safe_name};

/// os-release's ID.
pub(crate) const OS_ID: &str = "steamos";

/// The two slots.
pub(crate) const SLOTS: [&str; 2] = ["A", "B"];

/// The eight partlabels of an install, in the disk's order.
pub(crate) const LABELS: [&str; 8] = [
    "esp", "efi-A", "efi-B", "rootfs-A", "rootfs-B", "var-A", "var-B", "home",
];

/// The automounts the running system has (`/efi` is in the generic list,
/// `crate::sysupdate::AUTOMOUNT_POINTS`): dump lists them before it reads
/// the mounts, which mounts them.
pub(crate) const AUTOMOUNTS: [&str; 1] = ["/esp"];

/// On the ESP: steamcl, its fallback copy, its version, its flag files, the
/// boot records, the factory-reset requests.
pub(crate) const STEAMCL: &str = "efi/steamos/steamcl.efi";
pub(crate) const BOOTX64: &str = "efi/boot/bootx64.efi";
pub(crate) const STEAMCL_VERSION: &str = "efi/steamos/steamcl-version";
pub(crate) const FLAGS: [&str; 4] = [
    "steamcl-restricted",
    "steamcl-verbose",
    "steamcl-menu",
    "steamcl-nvram-debug",
];
pub(crate) const CONF_DIR: &str = "SteamOS/conf";
pub(crate) const FACTORY_RESET: &str = "efi/steamos/factory-reset";
/// On each efi partition: GRUB, its config, the partsets, dm-verity's hash.
pub(crate) const GRUBX64: &str = "EFI/steamos/grubx64.efi";
pub(crate) const GRUB_CFG: &str = "EFI/steamos/grub.cfg";
pub(crate) const PARTSETS: &str = "SteamOS/partsets";
pub(crate) const PARTSET_FILES: [&str; 7] = ["all", "self", "other", "shared", "A", "B", "dev"];
pub(crate) const ROOTHASH: &str = "SteamOS/roothash";
/// On each rootfs: the image's identity, and `holo-readonly disable`'s mark.
pub(crate) const MANIFEST: &str = "etc/steamos-atomupd/manifest.json";
pub(crate) const RW_MARKER: &str = ".ROOTFS_RW";
/// On each var: the slot's kernel modules, dirlock's (fscrypt) policies.
pub(crate) const MODULES: &str = "lib/modules";
pub(crate) const DIRLOCK: &str = "lib/dirlock/policies";
/// An update's marks: written, waiting for its reboot; and the booted slot's
/// letter, which makes the shutdown change the new slot's var once more.
pub(crate) const REBOOT_FOR_UPDATE: &str = "/run/steamos-atomupd/reboot_for_update";
pub(crate) const SYNC_TRIGGER: &str = "/run/steamos-atomupd/holo-booted-slot-sync-trigger";

/// The other letter.
pub(crate) fn other_slot(s: &str) -> &'static str {
    if s == "A" { "B" } else { "A" }
}

/// Where the backup tree has the filesystem of partlabel LABEL, SELF the
/// booted slot (None: a label that isn't one of the eight).
pub(crate) fn tree_path(label: &str, slf: &str) -> Option<&'static str> {
    let o = other_slot(slf);
    let (role, slot) = match label.split_once('-') {
        Some((r, s)) if s == "A" || s == "B" => (r, Some(s)),
        _ => (label, None),
    };
    Some(match (role, slot) {
        ("esp", None) => "/esp",
        ("home", None) => "/home",
        ("rootfs", Some(s)) if s == slf => "/",
        ("var", Some(s)) if s == slf => "/var",
        ("efi", Some(s)) if s == slf => "/efi",
        ("rootfs", Some(s)) if s == o => "/mnt",
        ("var", Some(s)) if s == o => "/mnt/var",
        ("efi", Some(s)) if s == o => "/mnt/efi",
        _ => return None,
    })
}

/// One of the eight partitions, as the dump's disks list it.
#[derive(Debug, Clone, PartialEq)]
pub(crate) struct Role {
    pub partlabel: String,
    /// The kernel name (vda4).
    pub dev: String,
    pub path: String,
    pub partuuid: Option<String>,
    pub type_guid: Option<String>,
    pub fstype: Option<String>,
    pub uuid: Option<String>,
    pub label: Option<String>,
    /// Where the running system mounts it (the tree's path first).
    pub mounted_at: Option<String>,
}

impl Role {
    fn from_part(p: &Value, tree_at: Option<&str>) -> Self {
        let s = |path: &[&str]| p.at(path).and_then(Value::as_str).map(str::to_string);
        let at: Vec<String> = p
            .get("mounted_at")
            .and_then(Value::as_array)
            .into_iter()
            .flatten()
            .filter_map(Value::as_str)
            .map(str::to_string)
            .collect();
        let mounted_at = at
            .iter()
            .find(|m| Some(m.as_str()) == tree_at)
            .or(at.first())
            .cloned();
        Self {
            partlabel: s(&["partlabel"]).unwrap_or_default(),
            dev: s(&["name"]).unwrap_or_default(),
            path: s(&["path"]).unwrap_or_default(),
            partuuid: s(&["uuid"]),
            type_guid: s(&["type"]),
            fstype: s(&["content", "type"]),
            uuid: s(&["content", "uuid"]),
            label: s(&["content", "label"]),
            mounted_at,
        }
    }

    fn json(&self) -> Value {
        Obj::new()
            .set("partlabel", self.partlabel.as_str())
            .set("device", self.dev.as_str())
            .set("partuuid", self.partuuid.clone())
            .set("type", self.type_guid.clone())
            .set("fstype", self.fstype.clone())
            .set("uuid", self.uuid.clone())
            .set("label", self.label.clone())
            .set("mounted_at", self.mounted_at.clone())
            .build()
    }

    /// The node dump mounts it by.
    fn node(&self) -> String {
        if self.path.is_empty() {
            format!("/dev/{}", self.dev)
        } else {
            self.path.clone()
        }
    }
}

/// A boot record (`SteamOS/conf/X.conf`): `key: value` lines as
/// holo-bootconf writes them (`key=value` read too), in order.
pub(crate) fn parse_conf(text: &str) -> Vec<(String, String)> {
    text.lines()
        .map(str::trim)
        .filter(|l| !l.is_empty() && !l.starts_with('#'))
        .filter_map(|l| {
            let i = l.find([':', '='])?;
            Some((l[..i].trim().to_string(), l[i + 1..].trim().to_string()))
        })
        .collect()
}

fn conf_get<'a>(c: &'a [(String, String)], k: &str) -> Option<&'a str> {
    c.iter()
        .rev()
        .find(|(x, _)| x == k)
        .map(|(_, v)| v.as_str())
}

/// A conf's flag: set when it is a number other than 0.
fn conf_flag(c: &[(String, String)], k: &str) -> bool {
    conf_get(c, k)
        .and_then(|v| v.parse::<u64>().ok())
        .is_some_and(|n| n != 0)
}

/// The slot steamcl boots, by its rule (steamos-efi `bootload.c`): the
/// confs from the newest `boot-requested-at` to the oldest, those with
/// `image-invalid` after all the rest, skipping any with `boot-other` set.
/// Ties keep A before B.  When every conf is skipped, the first in that
/// order, and true (a fallback steamcl's own source doesn't spell out).
/// None when there is no conf.
pub(crate) fn selected(confs: &[(&str, Vec<(String, String)>)]) -> Option<(String, bool)> {
    let mut order: Vec<&(&str, Vec<(String, String)>)> = confs.iter().collect();
    let stamp = |c: &[(String, String)]| {
        conf_get(c, "boot-requested-at")
            .and_then(|v| v.parse::<u64>().ok())
            .unwrap_or(0)
    };
    order.sort_by(|a, b| {
        conf_flag(&a.1, "image-invalid")
            .cmp(&conf_flag(&b.1, "image-invalid"))
            .then(stamp(&b.1).cmp(&stamp(&a.1)))
    });
    if let Some(c) = order.iter().find(|c| !conf_flag(&c.1, "boot-other")) {
        return Some((c.0.to_string(), false));
    }
    order.first().map(|c| (c.0.to_string(), true))
}

/// What a slot's grub.cfg says: its first `search` (kind: `fs-uuid`,
/// `part-uuid`, `label` or `file`; and its value), and the first `linux`
/// line's kernel and the `initrd` files after it (`steamenv_boot linux
/// ...` counts as `linux`).
#[derive(Debug, Clone, PartialEq, Default)]
pub(crate) struct GrubCfg {
    pub search_kind: Option<String>,
    pub search_value: Option<String>,
    pub linux: Option<String>,
    pub cmdline: Option<String>,
    pub initrd: Vec<String>,
}

/// `search` words: the kind and the value.
fn search_of(w: &[&str]) -> Option<(String, String)> {
    let kind = w.iter().find_map(|x| match *x {
        "--fs-uuid" | "-u" => Some("fs-uuid"),
        "--part-uuid" => Some("part-uuid"),
        "--label" | "-l" => Some("label"),
        "--file" | "-f" => Some("file"),
        _ => None,
    })?;
    let value = w
        .iter()
        .skip(1)
        .rev()
        .find(|x| !x.starts_with('-'))?
        .to_string();
    Some((kind.to_string(), value))
}

pub(crate) fn parse_grub_cfg(text: &str) -> GrubCfg {
    let mut c = GrubCfg::default();
    for line in text.lines() {
        let all: Vec<&str> = line.split_whitespace().collect();
        if all.first().is_some_and(|w| w.starts_with('#')) {
            continue;
        }
        let w: &[&str] = match all.first() {
            Some(&"steamenv_boot") => &all[1..],
            _ => &all,
        };
        match w.first() {
            Some(&"search") if c.search_kind.is_none() => {
                if let Some((k, v)) = search_of(w) {
                    c.search_kind = Some(k);
                    c.search_value = Some(v);
                }
            }
            Some(&"linux") if c.linux.is_none() => {
                c.linux = w.get(1).map(ToString::to_string);
                c.cmdline = Some(w.iter().skip(2).copied().collect::<Vec<_>>().join(" "));
            }
            Some(&"initrd") if c.linux.is_some() && c.initrd.is_empty() => {
                c.initrd = w.iter().skip(1).map(ToString::to_string).collect();
            }
            _ => {}
        }
    }
    c
}

/// The `search` GRUB's image has built in (grub-mkimage's early config,
/// `holo-grub-mkearlyconfig`): an EFI image keeps its embedded config and
/// memdisk uncompressed, so its text is read straight off the bytes, the
/// first line that names `search` and one of its kinds.
pub(crate) fn embedded_search(bytes: &[u8]) -> Option<(String, String)> {
    let mut at = 0;
    while let Some(i) = find_bytes(&bytes[at..], b"search") {
        let start = at + i;
        let end = bytes[start..]
            .iter()
            .position(|b| matches!(b, b'\n' | b'\r' | 0))
            .map_or(bytes.len(), |e| start + e);
        if let Ok(line) = std::str::from_utf8(&bytes[start..end]) {
            let w: Vec<&str> = line.split_whitespace().collect();
            if w.first() == Some(&"search") {
                if let Some(s) = search_of(&w) {
                    return Some(s);
                }
            }
        }
        at = start + 6;
    }
    None
}

fn find_bytes(hay: &[u8], needle: &[u8]) -> Option<usize> {
    hay.windows(needle.len()).position(|w| w == needle)
}

/// A partsets file: its `NAME PARTUUID` lines.
pub(crate) fn parse_partsets(text: &str) -> Vec<(String, String)> {
    text.lines()
        .filter_map(|l| {
            let mut w = l.split_whitespace();
            let (n, u) = (w.next()?, w.next()?);
            (!n.starts_with('#')).then(|| (n.to_string(), u.to_string()))
        })
        .collect()
}

/// `compsize -b -x`'s TOTAL line: (percent, disk, uncompressed, referenced).
pub(crate) fn parse_compsize(out: &str) -> Option<(u64, u64, u64, u64)> {
    let l = out.lines().find(|l| l.trim_start().starts_with("TOTAL"))?;
    let w: Vec<&str> = l.split_whitespace().collect();
    let n = |i: usize| w.get(i)?.trim_end_matches('%').parse::<u64>().ok();
    Some((n(1)?, n(2)?, n(3)?, n(4)?))
}

/// boot.steamos and what goes with it in the rest of the dump.
pub(crate) struct Dump {
    pub json: Value,
    /// `storage.backup_sources`: the eight, in place of the mounts'.
    pub sources: Vec<BackupSource>,
    /// The devices of the eight (the others' sources aren't in the tree).
    pub devices: Vec<String>,
    /// boot.kernels.installed's entries for the other slot's kernels.
    pub kernels: Vec<Value>,
    /// (fs UUID, allocation profiles) and (fs UUID, `top_level`) of the
    /// other rootfs, read during dump's own mount of it (sysfs has them only
    /// while it's mounted, and no mount reaches its top level after): to
    /// `crate::report::btrfs_json`, so load's mkfs.btrfs gets its -d/-m and
    /// its ro comes back.
    pub btrfs_profiles: Vec<(String, Value)>,
    pub btrfs_top_levels: Vec<(String, Value)>,
}

const ROOT_NOTE: &str = "SteamOS's booted rootfs, the btrfs top level: its /etc is an overlay (upper /var/lib/overlays/etc/upper), so read / through a non-recursive bind mount (mount --bind / DIR) to back up the raw lower /etc, the tree load restores";
const AUTOMOUNT_NOTE: &str = "SteamOS: an automount that unmounts after a minute idle; a backup holds it mounted while it reads it (a read-only bind of it into the backup tree keeps it busy), or mounts it read-only here as `mount` says";
const HINT_NOTE: &str = "SteamOS's other slot, not mounted on the running system: a backup mounts it read-only here as `mount` says, reads it, and unmounts it";
const PLAIN_NOTE: &str = "SteamOS: one of the eight partitions, at its path in the backup tree; mount it read-only here as `mount` says where nothing mounts it";

/// A boot record's lines, by slot.
type Confs = Vec<(&'static str, Vec<(String, String)>)>;

/// What dump reads of one slot.
struct SlotRead {
    json: Obj,
    kernels: Vec<Value>,
    profiles: Option<(String, Value)>,
    top_level: Option<(String, Value)>,
}

/// The eight partitions on the disk holding `/`, from the dump's disks:
/// (the roles found, by partlabel; the disk's other partlabels).  None when
/// `/` isn't on a partition named rootfs-A or rootfs-B.
pub(crate) fn find(disks: &[Value]) -> Option<(String, Vec<Role>, Vec<String>)> {
    let parts = |d: &Value| -> Vec<Value> {
        d.get("partitions")
            .and_then(Value::as_array)
            .cloned()
            .unwrap_or_default()
    };
    for d in disks {
        let ps = parts(d);
        let root = ps.iter().find(|p| {
            p.get("mounted_at")
                .and_then(Value::as_array)
                .is_some_and(|a| a.iter().any(|m| m.as_str() == Some("/")))
        });
        let Some(slf) = root
            .and_then(|p| p.get("partlabel").and_then(Value::as_str))
            .and_then(|l| l.strip_prefix("rootfs-"))
            .filter(|s| SLOTS.contains(s))
        else {
            continue;
        };
        let mut roles = Vec::new();
        let mut extra = Vec::new();
        for p in &ps {
            let Some(l) = p.get("partlabel").and_then(Value::as_str) else {
                continue;
            };
            if LABELS.contains(&l) {
                if !roles.iter().any(|r: &Role| r.partlabel == l) {
                    roles.push(Role::from_part(p, tree_path(l, slf)));
                }
            } else if !l.is_empty() {
                extra.push(l.to_string());
            }
        }
        return Some((slf.to_string(), roles, extra));
    }
    None
}

/// F(BASE) with ROLE readable at BASE: where the running system mounts it,
/// else a read-only mount of dump's own (unless `MAY_MOUNT` is false: an
/// update is running), removed after.  None when it can't be read.
fn with_mounted<R>(sys: &Sys, r: &Role, may_mount: bool, f: impl FnOnce(&str) -> R) -> Option<R> {
    if let Some(m) = &r.mounted_at {
        return Some(f(m));
    }
    let ty = r.fstype.clone()?;
    if !may_mount || !sys.privileged || !safe_name(&r.partlabel, b"-") {
        return None;
    }
    let at = format!("{RUN_DIR}/steamos-{}", r.partlabel);
    let words = RoMountWords {
        cant_make: format!(
            "SteamOS: can't make {at} to read {} ({})",
            r.partlabel, r.dev
        ),
        mount_failed: format!(
            "SteamOS: a read-only mount of {} ({}) at {at} failed, so boot.steamos doesn't say what it holds",
            r.partlabel,
            r.node()
        ),
        still_mounted: format!(
            "SteamOS: {} is still mounted read-only at {at}; `umount {at}` when this dump is done",
            r.partlabel
        ),
    };
    sys.with_ro_mount(
        &ty,
        &crate::abroot::ro_options(&ty),
        &r.node(),
        &at,
        &words,
        f,
    )
}

fn join(base: &str, rel: &str) -> String {
    format!("{}/{rel}", base.trim_end_matches('/'))
}

/// The ESP's part of boot.steamos, read at BASE.
fn esp_json(sys: &Sys, base: &str) -> (Obj, Confs) {
    let mut flags = Obj::new();
    for f in FLAGS {
        flags.put(f, sys.exists(&join(base, &format!("efi/steamos/{f}"))));
    }
    let resets: Vec<String> = sys
        .list(&join(base, FACTORY_RESET))
        .into_iter()
        .filter(|f| f.to_ascii_lowercase().ends_with(".cfg"))
        .collect();
    let mut confs = Vec::new();
    let mut cj = Obj::new();
    for s in SLOTS {
        match sys.read(&join(base, &format!("{CONF_DIR}/{s}.conf"))) {
            Some(t) => {
                let c = parse_conf(&t);
                let mut o = Obj::new();
                for (k, v) in &c {
                    o.put(k, v.as_str());
                }
                cj.put(s, o);
                confs.push((s, c));
            }
            None => cj.put(s, Value::Null),
        }
    }
    let o = Obj::new()
        .set("steamcl", sys.is_file(&join(base, STEAMCL)))
        .set("bootx64", sys.is_file(&join(base, BOOTX64)))
        .set(
            "steamcl_version",
            sys.read(&join(base, STEAMCL_VERSION))
                .and_then(|t| t.lines().next().map(|l| l.trim().to_string())),
        )
        .set("flags", flags)
        .set("factory_reset", !resets.is_empty())
        .set("factory_reset_files", resets)
        .set("confs", cj);
    (o, confs)
}

/// One slot's efi partition, read at BASE.
fn efi_json(sys: &Sys, base: &str, o: &mut Obj) {
    let cfg = sys.read(&join(base, GRUB_CFG)).map(|t| {
        let c = parse_grub_cfg(&t);
        Obj::new()
            .set("search_kind", c.search_kind)
            .set("search_value", c.search_value)
            .set("linux", c.linux)
            .set("cmdline", c.cmdline)
            .set("initrd", c.initrd)
            .build()
    });
    let grub = sys.read_bytes(&join(base, GRUBX64)).map(|b| {
        let e = embedded_search(&b);
        Obj::new()
            .set("bytes", b.len() as u64)
            .set("sha256", crate::bytes::sha256_hex(&b))
            .set("embedded_search_kind", e.as_ref().map(|x| x.0.clone()))
            .set("embedded_search_value", e.map(|x| x.1))
            .build()
    });
    let mut partsets = Obj::new();
    for f in PARTSET_FILES {
        if let Some(t) = sys.read(&join(base, &format!("{PARTSETS}/{f}"))) {
            let lines: Vec<Value> = parse_partsets(&t)
                .into_iter()
                .map(|(n, u)| Obj::new().set("name", n).set("partuuid", u).build())
                .collect();
            partsets.put(f, lines);
        }
    }
    o.put("grub_cfg", cfg);
    o.put("grubx64_efi", grub);
    o.put("partsets", partsets);
    o.put("roothash", sys.exists(&join(base, ROOTHASH)));
}

/// One slot's rootfs, read at BASE; its kernels (`slot` S, the tree's DIR).
fn rootfs_json(sys: &Sys, base: &str, slot: &str, dir: &str, o: &mut Obj) -> Vec<Value> {
    let manifest = sys
        .read(&join(base, MANIFEST))
        .map(|t| match crate::json::parse(&t) {
            Ok(Value::Obj(kv)) => {
                let mut m = Obj::new();
                for (k, v) in kv {
                    if matches!(
                        v,
                        Value::Str(_) | Value::UInt(_) | Value::Int(_) | Value::Bool(_)
                    ) {
                        m.put(&k, v);
                    }
                }
                m.build()
            }
            _ => Obj::new().set("unparsed", true).build(),
        });
    let ro = sys
        .run("btrfs", &["property", "get", "-ts", base, "ro"])
        .and_then(|t| {
            t.lines()
                .find_map(|l| l.trim().strip_prefix("ro=").map(|v| v == "true"))
        });
    let boot = join(base, "boot");
    let files = sys.list(&boot);
    let vmlinuz: Vec<String> = files
        .iter()
        .filter(|f| f.starts_with("vmlinuz-"))
        .cloned()
        .collect();
    let initramfs: Vec<String> = files
        .iter()
        .filter(|f| f.starts_with("initramfs-"))
        .cloned()
        .collect();
    let kernels = vmlinuz
        .iter()
        .map(|f| {
            let v = f.trim_start_matches("vmlinuz-");
            Obj::new()
                .set("version", v)
                .set("dir", dir)
                .set("slot", slot)
                .set("vmlinuz_bytes", sys.file_size(&format!("{boot}/{f}")))
                .set(
                    "initrd_bytes",
                    sys.file_size(&format!("{boot}/initramfs-{v}.img")),
                )
                .set("config", false)
                .set("running", false)
                .set("modules_installed", Value::Null)
                .build()
        })
        .collect();
    let compsize = sys
        .run("compsize", &["-b", "-x", base])
        .and_then(|t| parse_compsize(&t))
        .map(|(p, d, u, r)| {
            Obj::new()
                .set("percent", p)
                .set("disk_bytes", d)
                .set("uncompressed_bytes", u)
                .set("referenced_bytes", r)
                .build()
        });
    o.put("manifest", manifest);
    o.put("ro", ro);
    o.put("rootfs_rw_marker", sys.exists(&join(base, RW_MARKER)));
    o.put("vmlinuz", vmlinuz);
    o.put("initramfs", initramfs);
    o.put("compsize", compsize);
    kernels
}

/// One slot's var, read at BASE.
fn var_json(sys: &Sys, base: &str, o: &mut Obj) {
    o.put("modules", sys.list(&join(base, MODULES)));
    o.put("dirlock", !sys.list(&join(base, DIRLOCK)).is_empty());
}

/// Whether systemd says UNIT is active.
fn active(sys: &Sys, unit: &str) -> bool {
    sys.run_any("systemctl", &["is-active", unit])
        .is_some_and(|o| o.trim() == "active")
}

/// boot.steamos for a running `SteamOS` system: OS is os-release's ID, DISKS
/// the dump's.  None when this isn't `SteamOS`, or `/` isn't on rootfs-A or
/// rootfs-B (said).
pub(crate) fn read(sys: &Sys, os: Option<&str>, disks: &[Value]) -> Option<Dump> {
    if os != Some(OS_ID) {
        return None;
    }
    let Some((slf, roles, extra)) = find(disks) else {
        sys.warn("SteamOS: / is not on a GPT partition named rootfs-A or rootfs-B, so the dump has no boot.steamos and treats this as an ordinary system");
        return None;
    };
    let oth = other_slot(&slf);
    let role = |l: &str| roles.iter().find(|r| r.partlabel == l);
    let missing: Vec<&str> = LABELS
        .iter()
        .copied()
        .filter(|l| role(l).is_none())
        .collect();
    if !missing.is_empty() {
        sys.warn(format!(
            "SteamOS: the disk holding / has no partition named {}: load refuses this layout",
            missing.join(", ")
        ));
    }
    // An update running: dump makes no mounts of its own (RAUC writes the
    // other slot's partitions raw); the plan refuses such a dump anyway.
    let (rauc, atomupd) = (active(sys, "rauc.service"), active(sys, "atomupd.service"));
    let skip = rauc;
    if skip {
        sys.warn("SteamOS: rauc.service is active (an update is being written): the other slot's partitions weren't mounted to read them; dump again once it has finished");
    }

    // The ESP.
    let mut confs = Vec::new();
    let esp = role("esp").and_then(|r| {
        with_mounted(sys, r, !skip, |base| {
            let (o, c) = esp_json(sys, base);
            confs = c;
            o
        })
    });
    let sel = selected(&confs);
    let boot_other = {
        let mut o = Obj::new();
        for (s, c) in &confs {
            o.put(s, conf_flag(c, "boot-other"));
        }
        o.build()
    };
    let esp_json = esp.map(|mut o| {
        o.put("selected", sel.as_ref().map(|x| x.0.clone()));
        o.put("selected_fallback", sel.as_ref().map(|x| x.1));
        o.put("boot_other", boot_other.clone());
        o.build()
    });

    // Each slot: its efi, rootfs and var.
    let mut slots = Vec::new();
    let mut kernels = Vec::new();
    let mut profiles = Vec::new();
    let mut tops = Vec::new();
    for s in SLOTS {
        let r = read_slot(sys, s, s == slf, &role, !skip);
        if let Some(p) = r.profiles {
            profiles.push(p);
        }
        if let Some(t) = r.top_level {
            tops.push(t);
        }
        if s != slf {
            kernels.extend(r.kernels);
        }
        slots.push(r.json.build());
    }
    let sync = sys.read_trim(SYNC_TRIGGER);
    let pending = Obj::new()
        .set("reboot_for_update", sys.exists(REBOOT_FOR_UPDATE))
        .set("sync_trigger", sys.exists(SYNC_TRIGGER))
        .set("sync_trigger_slot", sync.filter(|t| !t.is_empty()))
        .set("rauc_active", rauc)
        .set("atomupd_active", atomupd);
    let json = Obj::new()
        .set("self", slf.as_str())
        .set("other", oth)
        .set("roles", roles.iter().map(Role::json).collect::<Vec<_>>())
        .set("missing_roles", missing.clone())
        .set("other_partitions", extra)
        .set("esp", esp_json)
        .set("slots", slots)
        .set("pending", pending)
        .set("mounts_skipped", skip)
        .build();

    // backup_sources: the eight at the tree's paths.
    let mut sources: Vec<BackupSource> = roles
        .iter()
        .filter_map(|r| {
            let path = tree_path(&r.partlabel, &slf)?;
            let fstype = r.fstype.clone().unwrap_or_default();
            let from = r.mounted_at.clone().filter(|m| m == path);
            let hint = MountHint {
                device: format!("/dev/disk/by-partlabel/{}", r.partlabel),
                fstype: fstype.clone(),
                options: crate::abroot::ro_options(&fstype),
            };
            let note = match path {
                "/" => ROOT_NOTE,
                "/efi" | "/esp" => AUTOMOUNT_NOTE,
                "/mnt" | "/mnt/var" | "/mnt/efi" => HINT_NOTE,
                _ => PLAIN_NOTE,
            };
            Some(BackupSource {
                path: path.to_string(),
                subvol: (fstype == "btrfs").then(|| "/".to_string()),
                from: from.clone(),
                device: Some(r.dev.clone()),
                fstype,
                source: r.node(),
                // Every entry nothing has mounted at its path says how.
                mount: from.is_none().then_some(hint),
                note: Some(note.to_string()),
            })
        })
        .collect();
    sources.sort_by(|a, b| a.path.cmp(&b.path));
    Some(Dump {
        json,
        sources,
        devices: roles.iter().map(|r| r.dev.clone()).collect(),
        kernels,
        btrfs_profiles: profiles,
        btrfs_top_levels: tops,
    })
}

fn read_slot<'a>(
    sys: &Sys,
    s: &str,
    booted: bool,
    role: &dyn Fn(&str) -> Option<&'a Role>,
    may_mount: bool,
) -> SlotRead {
    let mut o = Obj::new().set("slot", s).set("booted", booted);
    let mut read = Obj::new();
    let efi = role(&format!("efi-{s}"));
    read.put(
        "efi",
        efi.and_then(|r| with_mounted(sys, r, may_mount, |b| efi_json(sys, b, &mut o)))
            .is_some(),
    );
    let dir = if booted { "/boot" } else { "/mnt/boot" };
    let mut kernels = Vec::new();
    let mut profiles = None;
    let mut top_level = None;
    let rootfs = role(&format!("rootfs-{s}"));
    let rootfs_read = rootfs.and_then(|r| {
        with_mounted(sys, r, may_mount, |b| {
            kernels = rootfs_json(sys, b, s, dir, &mut o);
            // The other rootfs, mounted by dump itself: its allocation
            // profiles and top level while sysfs and the mount are there.
            if r.mounted_at.is_none() && r.fstype.as_deref() == Some("btrfs") {
                if let Some(u) = r.uuid.as_deref() {
                    profiles = crate::report::btrfs_allocation(sys, u).map(|v| (u.to_string(), v));
                    let sysfs = format!("/sys/fs/btrfs/{u}/features");
                    let features = sys.is_dir(&sysfs).then(|| sys.list(&sysfs));
                    top_level = Some((
                        u.to_string(),
                        crate::report::top_level_json(sys, b, features.as_deref()),
                    ));
                }
            }
        })
    });
    read.put("rootfs", rootfs_read.is_some());
    let var = role(&format!("var-{s}"));
    read.put(
        "var",
        var.and_then(|r| with_mounted(sys, r, may_mount, |b| var_json(sys, b, &mut o)))
            .is_some(),
    );
    o.put("read", read);
    SlotRead {
        json: o,
        kernels,
        profiles,
        top_level,
    }
}

/// BOOT (the dump's boot section) with boot.steamos in it and the other
/// slot's kernels in boot.kernels.installed (the booted slot's are /boot's,
/// marked with their slot).
pub(crate) fn add_to_boot(mut boot: Value, d: Option<&Dump>) -> Value {
    let Some(d) = d else { return boot };
    let slf = d
        .json
        .get("self")
        .and_then(Value::as_str)
        .map(str::to_string);
    if let Some(a) = boot
        .at_mut(&["kernels", "installed"])
        .and_then(Value::as_array_mut)
    {
        for k in a.iter_mut() {
            if let (Value::Obj(o), Some(s)) = (k, &slf) {
                if !o.iter().any(|(x, _)| x == "slot") {
                    o.push(("slot".into(), Value::from(s.as_str())));
                }
            }
        }
        a.extend(d.kernels.iter().cloned());
    }
    boot.put("steamos", d.json.clone());
    boot
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn tree_paths() {
        assert_eq!(tree_path("rootfs-A", "A"), Some("/"));
        assert_eq!(tree_path("rootfs-B", "A"), Some("/mnt"));
        assert_eq!(tree_path("var-B", "B"), Some("/var"));
        assert_eq!(tree_path("var-A", "B"), Some("/mnt/var"));
        assert_eq!(tree_path("efi-A", "B"), Some("/mnt/efi"));
        assert_eq!(tree_path("efi-B", "B"), Some("/efi"));
        assert_eq!(tree_path("esp", "B"), Some("/esp"));
        assert_eq!(tree_path("home", "A"), Some("/home"));
        assert_eq!(tree_path("verity-A", "A"), None);
        assert_eq!(tree_path("rootfs-C", "A"), None);
    }

    fn conf(t: &str) -> Vec<(String, String)> {
        parse_conf(t)
    }

    #[test]
    fn confs_and_selection() {
        let a = conf(
            "boot-requested-at: 20261101120000\nboot-other: 0\nboot-attempts: 0\nimage-invalid: 0\ntitle: A\ncomment:\n",
        );
        assert_eq!(conf_get(&a, "title"), Some("A"));
        assert_eq!(conf_get(&a, "comment"), Some(""));
        assert_eq!(
            conf("boot-other=1\n"),
            vec![("boot-other".to_string(), "1".to_string())]
        );
        let b = conf("boot-requested-at: 20261120080000\nboot-other: 0\nimage-invalid: 0\n");
        // The newest wins.
        assert_eq!(
            selected(&[("A", a.clone()), ("B", b.clone())]),
            Some(("B".into(), false))
        );
        // boot-other on B (reboot-other from B): A.
        let b_other = conf("boot-requested-at: 20261127080000\nboot-other: 1\nimage-invalid: 0\n");
        assert_eq!(
            selected(&[("A", a.clone()), ("B", b_other.clone())]),
            Some(("A".into(), false))
        );
        // An invalid image counts older than the rest.
        let b_bad = conf("boot-requested-at: 20261127080000\nimage-invalid: 1\n");
        assert_eq!(
            selected(&[("A", a.clone()), ("B", b_bad)]),
            Some(("A".into(), false))
        );
        // Both skipped: the first in order, flagged.
        let a_other = conf("boot-requested-at: 20261101120000\nboot-other: 1\n");
        assert_eq!(
            selected(&[("A", a_other), ("B", b_other)]),
            Some(("B".into(), true))
        );
        // Ties keep A first; one conf alone; none.
        let t = conf("boot-requested-at: 5\n");
        assert_eq!(
            selected(&[("A", t.clone()), ("B", t.clone())]),
            Some(("A".into(), false))
        );
        assert_eq!(selected(&[("B", t)]), Some(("B".into(), false)));
        assert_eq!(selected(&[]), None);
    }

    /// 3.8.14's grub.cfg (the recovery image's, cut short), and the 3.9
    /// form that searches by PARTUUID.
    #[test]
    fn grub_cfgs() {
        let c = parse_grub_cfg(
            "# generated\nset timeout=0\ninsmod btrfs\nsearch --no-floppy --fs-uuid --set=root 651004c4-7509-4853-b71c-cacc991d8c8d\nmenuentry 'SteamOS' {\n  steamenv_boot linux /boot/vmlinuz-linux-neptune-612 console=tty1 rd.luks=0 rd.lvm=0 rd.md=0 rd.dm=0 loglevel=3 quiet\n  initrd /boot/amd-ucode.img /boot/initramfs-linux-neptune-612.img\n}\n",
        );
        assert_eq!(c.search_kind.as_deref(), Some("fs-uuid"));
        assert_eq!(
            c.search_value.as_deref(),
            Some("651004c4-7509-4853-b71c-cacc991d8c8d")
        );
        assert_eq!(c.linux.as_deref(), Some("/boot/vmlinuz-linux-neptune-612"));
        assert!(
            c.cmdline
                .as_deref()
                .unwrap()
                .starts_with("console=tty1 rd.luks=0")
        );
        assert_eq!(
            c.initrd,
            vec![
                "/boot/amd-ucode.img".to_string(),
                "/boot/initramfs-linux-neptune-612.img".into()
            ]
        );
        let c = parse_grub_cfg(
            "search --no-floppy --part-uuid --set=root 1d1b5c2e-0000-4000-8000-000000000004\nlinux /boot/vmlinuz-linux-neptune-72 x\n",
        );
        assert_eq!(
            (c.search_kind.as_deref(), c.linux.as_deref()),
            (Some("part-uuid"), Some("/boot/vmlinuz-linux-neptune-72"))
        );
        assert!(c.initrd.is_empty());
        assert_eq!(parse_grub_cfg(""), GrubCfg::default());
    }

    #[test]
    fn embedded_config() {
        let mut img = vec![0u8; 64];
        img.extend_from_slice(b"searching for nothing\0");
        img.extend_from_slice(b"search --no-floppy --fs-uuid --set=root 651004c4-7509-4853-b71c-cacc991d8c8d\nset prefix=($root)/boot/grub\n");
        img.extend(vec![0xffu8; 32]);
        assert_eq!(
            embedded_search(&img),
            Some((
                "fs-uuid".into(),
                "651004c4-7509-4853-b71c-cacc991d8c8d".into()
            ))
        );
        assert_eq!(embedded_search(b"no such line"), None);
    }

    #[test]
    fn partsets_and_compsize() {
        assert_eq!(
            parse_partsets("efi 1111\nrootfs 2222\n\nvar 3333\n"),
            vec![
                ("efi".into(), "1111".into()),
                ("rootfs".into(), "2222".into()),
                ("var".into(), "3333".into())
            ]
        );
        let out = "Processed 41234 files, 39012 regular extents (39500 refs), 18000 inline.\nType       Perc     Disk Usage   Uncompressed Referenced  \nTOTAL       46%      1649267441   3584327680   3600000000  \nnone       100%       400000000    400000000    400000000  \nzstd        39%      1249267441   3184327680   3200000000  \n";
        assert_eq!(
            parse_compsize(out),
            Some((46, 1_649_267_441, 3_584_327_680, 3_600_000_000))
        );
        assert_eq!(parse_compsize("Type Perc\n"), None);
    }

    #[test]
    fn add_to_boot_marks_slots() {
        let boot = crate::json::parse(
            r#"{"loader": {}, "kernels": {"installed": [{"version": "linux-neptune-72"}], "symlinks": {}}}"#,
        )
        .unwrap();
        let d = Dump {
            json: Obj::new().set("self", "B").build(),
            sources: vec![],
            devices: vec![],
            kernels: vec![
                Obj::new()
                    .set("version", "linux-neptune-612")
                    .set("slot", "A")
                    .build(),
            ],
            btrfs_profiles: vec![],
            btrfs_top_levels: vec![],
        };
        let b = add_to_boot(boot.clone(), Some(&d));
        assert_eq!(
            b.at(&["kernels", "installed", "0", "slot"])
                .and_then(Value::as_str),
            Some("B")
        );
        assert_eq!(
            b.at(&["kernels", "installed", "1", "slot"])
                .and_then(Value::as_str),
            Some("A")
        );
        assert_eq!(
            b.at(&["steamos", "self"]).and_then(Value::as_str),
            Some("B")
        );
        assert_eq!(add_to_boot(boot.clone(), None), boot);
    }
}
