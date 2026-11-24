//! Ubuntu Core 24 (docs/design/ubuntu-core.md): how to tell a system is
//! one, and what the dump records of it (boot.snapcore).
//!
//! Ubuntu Core has no root filesystem of its own: `/` is the base snap
//! (core24, a squashfs loop mount), and the system is the files on four
//! partitions the pc gadget fixes, which snap-bootstrap in the initramfs
//! finds by label and mounts at `/run/mnt/ubuntu-seed` (vfat: shim, the
//! seed GRUB and its grubenv, the recovery systems and their snaps),
//! `/run/mnt/ubuntu-boot` (ext4: the run GRUB, each kernel's kernel.efi,
//! the model), `/run/mnt/ubuntu-save` (ext4: the device's keys) and
//! `/run/mnt/data` (ext4 ubuntu-data: snapd's state and modeenv, every
//! .snap, the writable paths, the homes).  With FDE, save and data are
//! LUKS2 volumes (`ubuntu-save-enc`, `ubuntu-data-enc`) whose keys are
//! sealed to the TPM; their headers are kept byte for byte
//! (crate::luksheader), since a new volume key would open nothing.
//!
//! The backup's tree is those four roots at snap-bootstrap's mount points,
//! and nothing under `/`, `/snap` or the bind mounts, so storage.
//! backup_sources is replaced by them.  Nothing on them is regenerated at
//! load (load/snapcore.rs): every snap revision and recovery system comes
//! back as a file.  The dump runs in run mode, where snap-bootstrap has
//! unlocked and mounted everything.

use crate::json::{Obj, Value};
use crate::report::{BackupSource, MountHint};
use crate::sys::Sys;

/// os-release's ID (the base snap's).
pub const OS_ID: &str = "ubuntu-core";

/// One of the pc gadget's four partitions.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct Role {
    pub name: &'static str,
    /// Its GPT partition name.
    pub partlabel: &'static str,
    /// Its filesystem's label (inside the LUKS2 volume, with FDE).
    pub fs_label: &'static str,
    /// The LUKS2 header's label, for the roles FDE encrypts.
    pub luks_label: Option<&'static str>,
    /// snap-bootstrap's run-mode mount point: the backup tree's path.
    pub at: &'static str,
}

pub const ROLES: [Role; 4] = [
    Role { name: "seed", partlabel: "ubuntu-seed", fs_label: "ubuntu-seed", luks_label: None, at: "/run/mnt/ubuntu-seed" },
    Role { name: "boot", partlabel: "ubuntu-boot", fs_label: "ubuntu-boot", luks_label: None, at: "/run/mnt/ubuntu-boot" },
    Role { name: "save", partlabel: "ubuntu-save", fs_label: "ubuntu-save", luks_label: Some("ubuntu-save-enc"), at: "/run/mnt/ubuntu-save" },
    Role { name: "data", partlabel: "ubuntu-data", fs_label: "ubuntu-data", luks_label: Some("ubuntu-data-enc"), at: "/run/mnt/data" },
];

/// The role named NAME.
pub fn role(name: &str) -> Option<&'static Role> {
    ROLES.iter().find(|r| r.name == name)
}

/// GPT's BIOS boot partition type: the gadget's `BIOS Boot` holds GRUB's
/// core.img (pc-core.img), which the MBR's boot.img (pc-boot.img) loads.
pub const BIOS_BOOT: &str = crate::parttable::BIOS_BOOT;

/// In ubuntu-data: snapd's mode environment, its state, the snaps.
pub const MODEENV: &str = "system-data/var/lib/snapd/modeenv";
pub const STATE: &str = "system-data/var/lib/snapd/state.json";
pub const SNAPS: &str = "system-data/var/lib/snapd/snaps";
/// On ubuntu-seed and ubuntu-boot: GRUB's environment (the seed's picks the
/// mode and recovery system; the run one has kernel_status).
pub const GRUBENV: &str = "EFI/ubuntu/grubenv";
/// On ubuntu-boot: the run-mode kernel (a symlink to
/// `pc-kernel_N.snap/kernel.efi`) and, during a kernel refresh, the one
/// being tried; the model assertion the device was installed with.
pub const RUN_KERNEL: &str = "EFI/ubuntu/kernel.efi";
pub const TRY_KERNEL: &str = "EFI/ubuntu/try-kernel.efi";
pub const MODEL: &str = "device/model";

/// The modeenv's statuses of `snap changes` that mean a change is under way.
const IN_PROGRESS: [&str; 5] = ["Do", "Doing", "Undo", "Undoing", "Wait"];

/// `key=value` lines (modeenv, os-release-like files): in order, the last of
/// a key winning in `get`.
#[derive(Debug, Clone, Default, PartialEq)]
pub struct Env(pub Vec<(String, String)>);

impl Env {
    pub fn parse(text: &str) -> Env {
        Env(text
            .lines()
            .map(str::trim)
            .filter(|l| !l.is_empty() && !l.starts_with('#'))
            .filter_map(|l| l.split_once('=').map(|(k, v)| (k.trim().to_string(), v.to_string())))
            .collect())
    }

    pub fn get(&self, k: &str) -> Option<&str> {
        self.0.iter().rev().find(|(x, _)| x == k).map(|(_, v)| v.as_str())
    }

    /// A key's value, None when empty.
    pub fn some(&self, k: &str) -> Option<String> {
        self.get(k).filter(|v| !v.is_empty()).map(str::to_string)
    }

    /// A comma-separated list.
    pub fn list(&self, k: &str) -> Vec<String> {
        self.get(k).unwrap_or("").split(',').map(str::trim).filter(|s| !s.is_empty()).map(str::to_string).collect()
    }
}

/// GRUB's environment block (1024 bytes: a `# GRUB Environment Block` line,
/// `key=value` lines, then `#` padding).
pub fn parse_grubenv(b: &[u8]) -> Env {
    Env::parse(&String::from_utf8_lossy(b))
}

/// A model assertion's headers and its snaps (name and type), as `snap
/// model --assertion` prints it: `key: value` lines, the `snaps:` list of
/// `-` items, a blank line, then the signature.
#[derive(Debug, Clone, Default, PartialEq)]
pub struct Model {
    pub headers: Vec<(String, String)>,
    pub snaps: Vec<(String, String)>,
}

impl Model {
    pub fn get(&self, k: &str) -> Option<&str> {
        self.headers.iter().find(|(x, _)| x == k).map(|(_, v)| v.as_str())
    }

    /// The snap of TYPE (gadget, kernel, base).
    pub fn snap_of(&self, ty: &str) -> Option<&str> {
        self.snaps.iter().find(|(_, t)| t == ty).map(|(n, _)| n.as_str())
    }
}

pub fn parse_model(text: &str) -> Model {
    let mut m = Model::default();
    let mut in_snaps = false;
    let mut cur: Option<(String, String)> = None;
    for line in text.lines() {
        if line.trim().is_empty() {
            break;
        }
        if !line.starts_with(' ') && !line.starts_with('-') {
            in_snaps = false;
            if let Some((k, v)) = line.split_once(':') {
                let v = v.trim().to_string();
                in_snaps = k == "snaps" && v.is_empty();
                m.headers.push((k.trim().to_string(), v));
            }
            continue;
        }
        if !in_snaps {
            continue;
        }
        let l = line.trim();
        if l == "-" || l.starts_with("- ") {
            if let Some(c) = cur.take() {
                m.snaps.push(c);
            }
            cur = Some((String::new(), String::new()));
        }
        let kv = l.trim_start_matches('-').trim();
        if let (Some(c), Some((k, v))) = (cur.as_mut(), kv.split_once(':')) {
            match k.trim() {
                "name" => c.0 = v.trim().to_string(),
                "type" => c.1 = v.trim().to_string(),
                _ => {}
            }
        }
    }
    if let Some(c) = cur.take() {
        m.snaps.push(c);
    }
    m
}

/// A `.snap` (or `.comp`) file's name and revision: `core24_2307.snap`.
pub fn snap_file(f: &str) -> Option<(String, String)> {
    let stem = f.strip_suffix(".snap")?;
    let (name, rev) = stem.rsplit_once('_')?;
    (!name.is_empty() && !rev.is_empty()).then(|| (name.to_string(), rev.to_string()))
}

/// The installed snaps from snapd's state.json: name, current revision,
/// every revision in its sequence.  snapd 2.62 and later keep the sequence
/// as `{"revisions": [{"snap": {...}}]}` (for components), older ones as a
/// list of side-infos; both are read.
pub fn state_snaps(state: &Value) -> Vec<(String, Option<String>, Vec<String>)> {
    let rev = |v: &Value| v.get("revision").and_then(|r| r.as_str().map(str::to_string).or_else(|| r.as_i64().map(|n| n.to_string())));
    let mut out = Vec::new();
    for (name, st) in state.at(&["data", "snaps"]).and_then(Value::as_object).into_iter().flatten() {
        let seq = st.get("sequence");
        let revs: Vec<String> = match seq {
            Some(Value::Arr(a)) => a.iter().filter_map(rev).collect(),
            Some(Value::Obj(_)) => seq.and_then(|s| s.get("revisions")).and_then(Value::as_array).into_iter().flatten().filter_map(|r| r.get("snap").and_then(rev)).collect(),
            _ => vec![],
        };
        let current = st.get("current").and_then(|r| r.as_str().map(str::to_string).or_else(|| r.as_i64().map(|n| n.to_string())));
        out.push((name.clone(), current, revs));
    }
    out.sort();
    out
}

/// The in-progress changes `snap changes` lists (its Status column).
pub fn changes_in_progress(out: &str) -> Vec<String> {
    out.lines().skip(1).filter(|l| l.split_whitespace().nth(1).is_some_and(|s| IN_PROGRESS.contains(&s))).map(|l| l.trim().to_string()).collect()
}

/// What the dump found for one role: the partition, and the filesystem
/// (on it, or in the LUKS2 volume opened on it).
#[derive(Debug, Clone, PartialEq)]
pub struct Found {
    pub disk: String,
    pub part: Value,
    /// The opened LUKS2 volume's dm device, for an encrypted role.
    pub opened: Option<Value>,
}

impl Found {
    fn s<'a>(v: &'a Value, path: &[&str]) -> Option<&'a str> {
        v.at(path).and_then(Value::as_str)
    }

    pub fn device(&self) -> String {
        Self::s(&self.part, &["name"]).unwrap_or("").to_string()
    }

    pub fn encrypted(&self) -> bool {
        Self::s(&self.part, &["content", "type"]) == Some("crypto_LUKS")
    }

    /// The device holding the filesystem, and its JSON (the dm device's when
    /// encrypted; None while locked).
    fn fs(&self) -> Option<&Value> {
        if self.encrypted() {
            self.opened.as_ref()
        } else {
            Some(&self.part)
        }
    }

    fn fs_s(&self, k: &str) -> Option<String> {
        self.fs().and_then(|v| Self::s(v, &["content", k])).map(str::to_string)
    }

    fn mounted_at(&self) -> Vec<String> {
        self.fs().and_then(|v| v.get("mounted_at")).and_then(Value::as_array).into_iter().flatten().filter_map(Value::as_str).map(str::to_string).collect()
    }

    /// The node a backup or dump reaches the filesystem by.
    fn node(&self) -> Option<String> {
        let v = self.fs()?;
        if self.encrypted() {
            return Self::s(v, &["dm", "dm_name"]).map(|n| format!("/dev/mapper/{n}"));
        }
        Self::s(v, &["path"]).map(str::to_string)
    }
}

/// boot.snapcore and what goes with it in the rest of the dump.
pub struct Dump {
    pub json: Value,
    /// storage.backup_sources: the four roots, in place of the mounts'.
    pub sources: Vec<BackupSource>,
    /// The LUKS2 devices whose headers the dump keeps (crate::luksheader).
    pub luks: Vec<String>,
    /// Why the dump can't be used: dump refuses it (report::refusal).
    pub refusals: Vec<String>,
}

const SOURCE_NOTE: &str = "Ubuntu Core: snap-bootstrap's run-mode mount point, the backup tree's path; back up one file system (the snaps loop-mounted inside ubuntu-data are its .snap files). Offline, mount it read-only here as `mount` says";
const LUKS_NOTE: &str = " (its LUKS2 volume first: `bko-holt recovery-key-file` makes the recovery key's key file for `cryptsetup open --key-file`)";

/// The four roles' partitions among the dump's disks (and the dm devices
/// their LUKS2 volumes are opened as), with the disk that holds them.
pub fn find(disks: &[Value], dm: &[Value]) -> (Vec<(Role, Found)>, Vec<String>) {
    let mut out = Vec::new();
    let mut problems = Vec::new();
    for r in ROLES {
        let mut hits = Vec::new();
        for d in disks {
            for p in d.get("partitions").and_then(Value::as_array).into_iter().flatten() {
                if p.get("partlabel").and_then(Value::as_str) == Some(r.partlabel) {
                    hits.push((d.get("name").and_then(Value::as_str).unwrap_or("").to_string(), p.clone()));
                }
            }
        }
        if hits.len() > 1 {
            let names: Vec<String> = hits.iter().map(|(_, p)| p.get("name").and_then(Value::as_str).unwrap_or("?").to_string()).collect();
            problems.push(format!("{} partitions are named {} ({}): another Ubuntu Core disk is attached; dump with --only-disk naming the system's own", hits.len(), r.partlabel, names.join(", ")));
            continue;
        }
        let Some((disk, part)) = hits.pop() else { continue };
        let holders: Vec<&str> = part.get("holders").and_then(Value::as_array).into_iter().flatten().filter_map(Value::as_str).collect();
        let opened = dm.iter().find(|x| x.get("name").and_then(Value::as_str).is_some_and(|n| holders.contains(&n)) && x.at(&["dm", "target"]).and_then(Value::as_str) == Some("crypt")).cloned();
        out.push((r, Found { disk, part, opened }));
    }
    (out, problems)
}

fn list_of(v: Vec<String>) -> Value {
    Value::from(v)
}

/// What the dump reads of one recovery system on ubuntu-seed.
fn recovery_system(sys: &Sys, seed: &str, label: &str) -> Value {
    let dir = format!("{seed}/systems/{label}");
    let env = sys.read_bytes(&format!("{dir}/grubenv")).map(|b| parse_grubenv(&b)).unwrap_or_default();
    let model = sys.read(&format!("{dir}/model")).map(|t| parse_model(&t));
    Obj::new()
        .set("label", label)
        .set("model", sys.is_file(&format!("{dir}/model")))
        .set("grade", model.as_ref().and_then(|m| m.get("grade")).map(str::to_string))
        .set("kernel", env.some("snapd_recovery_kernel"))
        .set("extra_cmdline_args", env.get("snapd_extra_cmdline_args").map(str::to_string))
        .set("full_cmdline_args", env.get("snapd_full_cmdline_args").map(str::to_string))
        .build()
}

/// The files under DIR (names only: the FDE key files are never read).
fn names(sys: &Sys, dir: &str) -> Vec<String> {
    sys.list(dir)
}

/// os-release from the base snap (`unsquashfs -cat`), else the running
/// system's (whose `/` is that base in run mode).
fn base_os_release(sys: &Sys, base_snap: Option<&str>) -> Value {
    let from_base = base_snap.and_then(|p| {
        ["usr/lib/os-release", "etc/os-release"].iter().find_map(|f| sys.run("unsquashfs", &["-cat", p, f]).filter(|t| t.contains("ID=")).map(|t| (p.to_string(), t)))
    });
    let (source, text) = match from_base {
        Some((p, t)) => (format!("base snap {p}"), Some(t)),
        None => ("running system".to_string(), sys.read("/etc/os-release").or_else(|| sys.read("/usr/lib/os-release"))),
    };
    let o = text.map(|t| crate::osrel::parse(&t));
    let g = |k: &str| o.as_ref().and_then(|o| o.get(k)).map(str::to_string);
    Obj::new().set("source", source).set("id", g("ID")).set("version_id", g("VERSION_ID")).set("pretty_name", g("PRETTY_NAME")).build()
}

/// The MBR's boot code and the BIOS Boot partition's bytes (pc-boot.img and
/// pc-core.img, installed by the gadget), for load to write back where they
/// were: the restored table puts every partition where it was, so core.img's
/// block list still points at itself.
fn boot_areas(sys: &Sys, disk: &Value) -> Value {
    let name = disk.get("name").and_then(Value::as_str).unwrap_or("");
    let path = disk.get("path").and_then(Value::as_str).map(str::to_string).unwrap_or_else(|| format!("/dev/{name}"));
    let Some(src) = sys.open_dev(&path) else {
        sys.warn(format!("Ubuntu Core: {path} couldn't be read, so the dump has neither the MBR's boot code nor the BIOS Boot partition's core.img (BIOS boot of the restored disk needs them; UEFI doesn't)"));
        return Value::Null;
    };
    use crate::bytes::ByteSource;
    let mbr = src.read_at(0, 440);
    let code = (!crate::bytes::is_zero(&mbr)).then(|| mbr.clone());
    let bios = disk.get("partitions").and_then(Value::as_array).into_iter().flatten().find(|p| p.get("type").and_then(Value::as_str).is_some_and(|t| t.eq_ignore_ascii_case(BIOS_BOOT)));
    let bios_json = bios.map(|p| {
        let start = p.get("start_bytes").and_then(Value::as_u64).unwrap_or(0);
        let size = p.get("size_bytes").and_then(Value::as_u64).unwrap_or(0);
        let img = crate::other::image_b64(&crate::bytes::OffsetSource { inner: &src, start, len: size });
        let bytes = crate::bytes::unbase64(&img).unwrap_or_default();
        Obj::new()
            .set("partition", p.get("name").cloned().unwrap_or(Value::Null))
            .set("number", p.get("number").cloned().unwrap_or(Value::Null))
            .set("start_bytes", start)
            .set("size_bytes", size)
            .set("image_bytes", bytes.len())
            .set("sha256", crate::bytes::sha256_hex(&bytes))
            .set("image_b64", img)
            .build()
    });
    Obj::new()
        .set("disk", name)
        .set("mbr_code_kind", crate::bootcode::identify(&src.read_at(0, 512)).kind)
        .set("mbr_code_sha256", code.as_ref().map(|c| crate::bytes::sha256_hex(c)))
        .set("mbr_code_b64", code.as_ref().map(|c| crate::bytes::base64(c)))
        .set("bios_boot", bios_json)
        .build()
}

/// boot.snapcore of a running Ubuntu Core.  DISKS and DM are the dump's
/// storage.disks and storage.dm_devices.
pub fn read(sys: &Sys, disks: &[Value], dm: &[Value]) -> Dump {
    let (found, mut refusals) = find(disks, dm);
    let get = |name: &str| found.iter().find(|(r, _)| r.name == name).map(|(_, f)| f);
    for r in ROLES {
        if get(r.name).is_none() {
            refusals.push(format!("Ubuntu Core: no partition is named {} (the pc gadget's four are ubuntu-seed, ubuntu-boot, ubuntu-save and ubuntu-data)", r.partlabel));
        }
    }
    // Each role's filesystem at its run-mode mount point.
    let mut parts = Vec::new();
    let mut sources = Vec::new();
    let mut luks = Vec::new();
    for (r, f) in &found {
        let locked = f.encrypted() && f.opened.is_none();
        if f.encrypted() {
            luks.push(f.device());
            if locked {
                refusals.push(format!(
                    "Ubuntu Core: {} ({}, {}) is encrypted and not open: dump runs in run mode, where snap-bootstrap has unlocked it (offline: `bko-holt recovery-key-file KEY` from the recovery key, then `cryptsetup open --key-file KEY /dev/{} NAME`)",
                    r.partlabel,
                    f.device(),
                    r.luks_label.unwrap_or("LUKS2"),
                    f.device()
                ));
            }
        }
        let at = f.mounted_at();
        let there = at.iter().any(|m| m == r.at);
        if !there && !locked {
            refusals.push(format!("Ubuntu Core: {} ({}) isn't mounted at {}: dump runs in Ubuntu Core's run mode, where snap-bootstrap mounts it there", r.partlabel, f.device(), r.at));
        }
        let fstype = f.fs_s("type").unwrap_or_default();
        let uuid = f.fs_s("uuid");
        if let Some(node) = f.node() {
            let hint_dev = uuid.as_ref().map(|u| format!("/dev/disk/by-uuid/{u}")).unwrap_or_else(|| node.clone());
            sources.push(BackupSource {
                path: r.at.to_string(),
                from: there.then(|| r.at.to_string()),
                device: f.fs().and_then(|v| v.get("name")).and_then(Value::as_str).map(str::to_string),
                fstype: fstype.clone(),
                source: node,
                subvol: None,
                mount: Some(MountHint { device: hint_dev, fstype: fstype.clone(), options: crate::abroot::ro_options(&fstype) }),
                note: Some(format!("{SOURCE_NOTE}{}", if f.encrypted() { LUKS_NOTE } else { "" })),
            });
        }
        let p = &f.part;
        let ps = |k: &str| p.get(k).cloned().unwrap_or(Value::Null);
        parts.push(
            Obj::new()
                .set("role", r.name)
                .set("device", f.device())
                .set("disk", f.disk.as_str())
                .set("number", ps("number"))
                .set("partlabel", ps("partlabel"))
                .set("partuuid", ps("uuid"))
                .set("type", ps("type"))
                .set("attributes", ps("attributes"))
                .set("start_bytes", ps("start_bytes"))
                .set("size_bytes", ps("size_bytes"))
                .set("encrypted", f.encrypted())
                .set(
                    "luks",
                    f.encrypted().then(|| {
                        Obj::new()
                            .set("uuid", p.at(&["content", "uuid"]).cloned().unwrap_or(Value::Null))
                            .set("label", p.at(&["content", "label"]).cloned().unwrap_or(Value::Null))
                            .set("version", p.at(&["content", "version"]).cloned().unwrap_or(Value::Null))
                            .set("opened_as", f.opened.as_ref().and_then(|o| o.at(&["dm", "dm_name"])).cloned().unwrap_or(Value::Null))
                            .set("dm_device", f.opened.as_ref().and_then(|o| o.get("name")).cloned().unwrap_or(Value::Null))
                            .build()
                    }),
                )
                .set("fstype", fstype.as_str())
                .set("fs_label", f.fs_s("label"))
                .set("fs_uuid", uuid)
                .set("at", r.at)
                .set("mounted_there", there)
                .build(),
        );
    }
    let base = |name: &str| get(name).filter(|f| f.mounted_at().iter().any(|m| m == role(name).map_or("", |r| r.at))).map(|_| role(name).map_or("", |r| r.at).to_string());
    let (seed, boot, data, save) = (base("seed"), base("boot"), base("data"), base("save"));

    // modeenv: what run mode boots, and from what.
    let modeenv = data.as_ref().and_then(|d| sys.read(&format!("{d}/{MODEENV}"))).map(|t| Env::parse(&t));
    if data.is_some() && modeenv.is_none() {
        refusals.push(format!("Ubuntu Core: ubuntu-data has no {MODEENV}"));
    }
    let me = modeenv.clone().unwrap_or_default();
    let mode = me.some("mode");
    if modeenv.is_some() && mode.as_deref() != Some("run") {
        refusals.push(format!("Ubuntu Core: modeenv says mode={}: dump runs in run mode (`snap reboot --run`, then dump again)", mode.as_deref().unwrap_or("")));
    }
    let modeenv_json = Obj::new()
        .set("mode", mode.clone())
        .set("recovery_system", me.some("recovery_system"))
        .set("current_recovery_systems", list_of(me.list("current_recovery_systems")))
        .set("good_recovery_systems", list_of(me.list("good_recovery_systems")))
        .set("base", me.some("base"))
        .set("gadget", me.some("gadget"))
        .set("current_kernels", list_of(me.list("current_kernels")))
        .set("model", me.some("model"))
        .set("grade", me.some("grade"))
        .set("model_sign_key_id", me.some("model_sign_key_id"))
        .set("current_kernel_command_lines", me.some("current_kernel_command_lines"))
        .build();

    // The model the device was installed with (ubuntu-boot's copy).
    let model = boot.as_ref().and_then(|b| sys.read(&format!("{b}/{MODEL}"))).map(|t| parse_model(&t));
    let gadget = model.as_ref().and_then(|m| m.snap_of("gadget")).map(str::to_string);
    let arch = model.as_ref().and_then(|m| m.get("architecture")).map(str::to_string);
    let model_base = model.as_ref().and_then(|m| m.get("base")).map(str::to_string);
    match &model {
        None if boot.is_some() => refusals.push(format!("Ubuntu Core: ubuntu-boot has no {MODEL} (the model assertion)")),
        Some(_) if gadget.as_deref() != Some("pc") => {
            refusals.push(format!("Ubuntu Core: the model's gadget is {}, not pc: version 1 restores the pc gadget's layout only", gadget.as_deref().unwrap_or("missing")))
        }
        Some(_) if arch.as_deref() != Some("amd64") => refusals.push(format!("Ubuntu Core: the model is for {}, not amd64: version 1 restores amd64 only", arch.as_deref().unwrap_or("no architecture"))),
        Some(_) if model_base.as_deref() != Some("core24") => {
            refusals.push(format!("Ubuntu Core: the model's base is {}, not core24: version 1 restores Ubuntu Core 24 only (UC20 and UC22 keep their keys otherwise)", model_base.as_deref().unwrap_or("missing")))
        }
        _ => {}
    }
    let model_json = model.as_ref().map(|m| {
        Obj::new()
            .set("brand_id", m.get("brand-id").map(str::to_string))
            .set("model", m.get("model").map(str::to_string))
            .set("grade", m.get("grade").map(str::to_string))
            .set("storage_safety", m.get("storage-safety").map(str::to_string))
            .set("base", m.get("base").map(str::to_string))
            .set("architecture", m.get("architecture").map(str::to_string))
            .set("gadget", gadget.clone())
            .set("kernel", m.snap_of("kernel").map(str::to_string))
            .build()
    });

    // ubuntu-boot: the run GRUB's environment, the run-mode kernel.
    let boot_env = boot.as_ref().and_then(|b| sys.read_bytes(&format!("{b}/{GRUBENV}"))).map(|b| parse_grubenv(&b)).unwrap_or_default();
    let kernel_status = boot_env.some("kernel_status");
    if let Some(ks) = kernel_status.as_deref().filter(|k| matches!(*k, "try" | "trying")) {
        refusals.push(format!("Ubuntu Core: a kernel refresh is being tried (the run grubenv's kernel_status={ks}): wait until it has finished (`snap changes`), then dump again"));
    }
    let run_kernel = boot.as_ref().and_then(|b| sys.readlink(&format!("{b}/{RUN_KERNEL}")));
    let try_kernel = boot.as_ref().and_then(|b| sys.readlink(&format!("{b}/{TRY_KERNEL}")));
    let kernels_on_boot: Vec<String> = boot
        .as_ref()
        .map(|b| sys.list(&format!("{b}/EFI/ubuntu")).into_iter().filter(|k| k.ends_with(".snap") && sys.is_file(&format!("{b}/EFI/ubuntu/{k}/kernel.efi"))).collect())
        .unwrap_or_default();
    let boot_json = Obj::new()
        .set("kernel_status", kernel_status.clone())
        .set("snap_kernel", boot_env.some("snap_kernel"))
        .set("snap_try_kernel", boot_env.some("snap_try_kernel"))
        .set("run_kernel", run_kernel)
        .set("try_kernel", try_kernel)
        .set("kernels", list_of(kernels_on_boot))
        .set("fde_files", boot.as_ref().map(|b| names(sys, &format!("{b}/device/fde"))))
        .build();

    // ubuntu-seed: the seed GRUB's environment and the recovery systems.
    let seed_env = seed.as_ref().and_then(|s| sys.read_bytes(&format!("{s}/{GRUBENV}"))).map(|b| parse_grubenv(&b)).unwrap_or_default();
    let labels: Vec<String> = seed.as_ref().map(|s| sys.list(&format!("{s}/systems"))).unwrap_or_default();
    let systems: Vec<Value> = labels.iter().map(|l| recovery_system(sys, seed.as_deref().unwrap_or(""), l)).collect();
    let seed_json = Obj::new()
        .set("snapd_recovery_mode", seed_env.some("snapd_recovery_mode"))
        .set("snapd_recovery_system", seed_env.some("snapd_recovery_system"))
        .set("snapd_good_recovery_systems", list_of(seed_env.list("snapd_good_recovery_systems")))
        .set("try_recovery_system", seed_env.some("try_recovery_system"))
        .set("recovery_system_status", seed_env.some("recovery_system_status"))
        .set("systems", systems)
        .set("snaps", seed.as_ref().map(|s| sys.list(&format!("{s}/snaps"))))
        .set("efi", seed.as_ref().map(|s| (sys.list(&format!("{s}/EFI/boot")), sys.list(&format!("{s}/EFI/ubuntu")))).map(|(b, u)| Obj::new().set("boot", b).set("ubuntu", u).build()))
        .set("fde_files", seed.as_ref().map(|s| names(sys, &format!("{s}/device/fde"))))
        .build();

    // ubuntu-data: every snap and revision.
    let snap_files: Vec<(String, String)> = data.as_ref().map(|d| sys.list(&format!("{d}/{SNAPS}")).iter().filter_map(|f| snap_file(f)).collect()).unwrap_or_default();
    let state = data.as_ref().and_then(|d| sys.read(&format!("{d}/{STATE}"))).and_then(|t| crate::json::parse(&t).ok());
    let from_state = state.as_ref().map(state_snaps).unwrap_or_default();
    let mut snap_names: Vec<String> = snap_files.iter().map(|(n, _)| n.clone()).chain(from_state.iter().map(|(n, _, _)| n.clone())).collect();
    snap_names.sort();
    snap_names.dedup();
    let snaps: Vec<Value> = snap_names
        .iter()
        .map(|n| {
            let st = from_state.iter().find(|(x, _, _)| x == n);
            let files: Vec<String> = snap_files.iter().filter(|(x, _)| x == n).map(|(_, r)| r.clone()).collect();
            Obj::new()
                .set("name", n.as_str())
                .set("current", st.and_then(|s| s.1.clone()))
                .set("revisions", st.map(|s| s.2.clone()))
                .set("files", files)
                .build()
        })
        .collect();
    let base_snap = data.as_ref().zip(me.some("base")).map(|(d, b)| format!("{d}/{SNAPS}/{b}"));
    let os = base_os_release(sys, base_snap.as_deref());

    // snapd's changes under way (a refresh mid-way): the live backup would
    // be of a system half-refreshed.
    let changes = sys.run("snap", &["changes"]).map(|o| changes_in_progress(&o));
    if let Some(c) = changes.as_ref().filter(|c| !c.is_empty()) {
        refusals.push(format!(
            "Ubuntu Core: `snap changes` shows {} change(s) in progress ({}): a refresh may be mid-way; wait until they're done (`snap refresh --hold` keeps new ones away during a backup), then dump again",
            c.len(),
            c.iter().map(|l| l.split_whitespace().take(2).collect::<Vec<_>>().join(" ")).collect::<Vec<_>>().join("; ")
        ));
    }
    let save_json = Obj::new().set("fde_files", save.as_ref().map(|s| names(sys, &format!("{s}/device/fde")))).build();
    let disk = found.first().map(|(_, f)| f.disk.clone());
    let areas = disk.as_ref().and_then(|n| disks.iter().find(|d| d.get("name").and_then(Value::as_str) == Some(n.as_str()))).map(|d| boot_areas(sys, d));
    let encrypted = found.iter().any(|(_, f)| f.encrypted());
    let json = Obj::new()
        .set("encrypted", encrypted)
        .set("disk", disk)
        .set("partitions", parts)
        .set("modeenv", modeenv_json)
        .set("model", model_json)
        .set("os_release", os)
        .set("boot", boot_json)
        .set("seed", seed_json)
        .set("save", save_json)
        .set("snaps", snaps)
        .set("changes_in_progress", changes.map(|c| c.len()))
        .set("boot_areas", areas)
        .set("refusals", refusals.clone())
        .build();
    Dump { json, sources, luks, refusals }
}

impl Dump {
    /// Every encrypted role's LUKS2 header must be among HEADERS (the
    /// dump's storage.luks_headers): one that isn't is refused, since load
    /// can't restore the volume without it.  The header files are in DIR,
    /// which by default is in RAM: said, so they're kept with the dump.
    pub fn require_headers(&mut self, sys: &Sys, headers: &[Value], dir: &str) {
        let kept = |d: &str| headers.iter().any(|h| h.get("device").and_then(Value::as_str) == Some(d));
        let missing: Vec<String> = self.luks.iter().filter(|d| !kept(d)).cloned().collect();
        for d in &missing {
            self.refusals.push(format!("Ubuntu Core: the LUKS2 header of {d} wasn't kept (the warnings say why): load can't restore it without its header; dump again as root, with cryptsetup and room in the header directory"));
        }
        if !self.luks.is_empty() && missing.len() < self.luks.len() {
            sys.warn(format!("Ubuntu Core: the LUKS2 headers are files in {dir}{}: keep them with this dump, in the same directory as the layout (load looks there, or in its --luks-header-dir)", if dir == crate::luksheader::DEFAULT_DIR { " (RAM, gone at the next boot)" } else { "" }));
        }
        if let Value::Obj(o) = &mut self.json {
            for (k, v) in o.iter_mut() {
                if k == "refusals" {
                    *v = Value::from(self.refusals.clone());
                }
            }
        }
    }
}

/// BOOT (the dump's boot section) with boot.snapcore in it.
pub fn add_to_boot(boot: Value, d: Option<&Dump>) -> Value {
    let (Some(d), Value::Obj(mut o)) = (d, boot.clone()) else { return boot };
    o.push(("snapcore".into(), d.json.clone()));
    Value::Obj(o)
}

/// Why a dump can't be used, when it's Ubuntu Core's and refused.
pub fn refusal(v: &Value) -> Option<String> {
    let r: Vec<&str> = v.at(&["boot", "snapcore", "refusals"]).and_then(Value::as_array)?.iter().filter_map(Value::as_str).collect();
    (!r.is_empty()).then(|| r.join("; "))
}

// ---- the recovery key -------------------------------------------------

/// Ubuntu Core's recovery key (`snap recovery --show-keys`: eight groups of
/// five digits) as the 16 bytes its LUKS2 keyslot holds: each group a
/// little-endian uint16 (docs/design/ubuntu-core.md, "What U1 found": these
/// bytes open keyslot 1; the dashed text doesn't).  The error never repeats
/// what it was given.
pub fn recovery_key_bytes(text: &str) -> Result<[u8; 16], String> {
    let t = text.trim();
    let groups: Vec<&str> = t.split('-').collect();
    if groups.len() != 8 {
        return Err(format!("a recovery key is eight groups of five digits joined by '-'; this has {} group(s)", groups.len()));
    }
    let mut out = [0u8; 16];
    for (i, g) in groups.iter().enumerate() {
        if g.len() != 5 || !g.bytes().all(|b| b.is_ascii_digit()) {
            return Err(format!("group {} of the recovery key isn't five digits", i + 1));
        }
        let n: u32 = g.parse().map_err(|_| format!("group {} of the recovery key isn't a number", i + 1))?;
        let n = u16::try_from(n).map_err(|_| format!("group {} of the recovery key is over 65535, so it isn't one", i + 1))?;
        out[2 * i..2 * i + 2].copy_from_slice(&n.to_le_bytes());
    }
    Ok(out)
}

pub const RECOVERY_KEY_USAGE: &str = "Usage: bko-holt recovery-key-file OUT

Reads Ubuntu Core's recovery key (the eight groups of five digits `snap
recovery --show-keys` prints) from standard input, and writes the 16 bytes
its LUKS2 keyslot holds to OUT (mode 0600), for `cryptsetup open
--key-file OUT` and `bko-holt load --key-file NAME=OUT`.  The digits are
never taken from the command line, so they don't sit in `ps` or a shell's
history; on a terminal they aren't echoed.  It prints only the first 12
hex digits of the key file's SHA-256, to tell two keys apart.
";

/// `bko-holt recovery-key-file OUT`.
pub fn recovery_key_main(args: &[String]) -> u8 {
    use std::io::{IsTerminal, Read, Write};
    let out = match args {
        [a] if a == "-h" || a == "--help" => {
            print!("{RECOVERY_KEY_USAGE}");
            return 0;
        }
        [o] if !o.starts_with('-') => o.clone(),
        _ => {
            eprint!("bko-holt recovery-key-file: give the file to write, and the key on standard input\n\n{RECOVERY_KEY_USAGE}");
            return 2;
        }
    };
    // A key given as the file's name would be in argv and in what this
    // prints: refused, without saying it again.
    if recovery_key_bytes(&out).is_ok() || crate::redact::scrub_recovery_keys(&out) != out.as_str() {
        eprintln!("bko-holt recovery-key-file: OUT looks like a recovery key: give the file to write, and the key on standard input");
        return 2;
    }
    // A dry run (BKO_HOLT_NO_COMMIT) writes only a new file, so OUT can't
    // be a device or anything else already there; asked before the key is.
    let new_only = crate::load::jsonout::no_commit().is_some();
    if new_only && std::fs::symlink_metadata(&out).is_ok() {
        eprintln!("{}", crate::load::jsonout::exists_refusal("recovery-key-file", &out));
        return 2;
    }
    let tty = std::io::stdin().is_terminal();
    let mut text = String::new();
    if tty {
        eprint!("Recovery key (eight groups of five digits, not echoed): ");
        let _ = std::io::stderr().flush();
        // The terminal's echo comes back however the read ends, Ctrl-C
        // included: the shell's trap restores it.  The key reaches us on a
        // pipe, never in an argument.
        let r = std::process::Command::new("sh")
            .args(["-c", "trap 'stty echo' EXIT; trap 'exit 130' INT TERM HUP; stty -echo; IFS= read -r k; printf '%s' \"$k\""])
            .stdin(std::process::Stdio::inherit())
            .stderr(std::process::Stdio::inherit())
            .output();
        eprintln!();
        match r {
            Ok(mut o) if o.status.success() => {
                text = String::from_utf8_lossy(&o.stdout).into_owned();
                crate::recovery::zeroize_bytes(&mut o.stdout);
            }
            Ok(mut o) => {
                crate::recovery::zeroize_bytes(&mut o.stdout);
                eprintln!("bko-holt recovery-key-file: couldn't read the key");
                return 1;
            }
            Err(_) => {
                eprintln!("bko-holt recovery-key-file: couldn't read the key");
                return 1;
            }
        }
    } else if std::io::stdin().take(4096).read_to_string(&mut text).is_err() {
        crate::recovery::zeroize_string(&mut text);
        eprintln!("bko-holt recovery-key-file: couldn't read the key from standard input");
        return 1;
    }
    let mut key = match recovery_key_bytes(&text) {
        Ok(k) => k,
        Err(e) => {
            crate::recovery::zeroize_string(&mut text);
            eprintln!("bko-holt recovery-key-file: {e}");
            return 1;
        }
    };
    crate::recovery::zeroize_string(&mut text);
    let write = || -> std::io::Result<()> {
        use std::os::unix::fs::{OpenOptionsExt, PermissionsExt};
        let mut o = std::fs::OpenOptions::new();
        o.write(true).mode(0o600);
        if new_only {
            o.create_new(true);
        } else {
            o.create(true).truncate(true);
        }
        let mut f = o.open(&out)?;
        f.set_permissions(std::fs::Permissions::from_mode(0o600))?;
        f.write_all(&key)?;
        f.sync_all()
    };
    if let Err(e) = write() {
        crate::recovery::zeroize_bytes(&mut key);
        eprintln!("bko-holt recovery-key-file: {out}: {e}");
        return 1;
    }
    let digest = crate::bytes::sha256_hex(&key);
    crate::recovery::zeroize_bytes(&mut key);
    println!("wrote {out}: 16 bytes, sha256 {}...", &digest[..12]);
    0
}

#[cfg(test)]
pub(crate) mod tests {
    use super::*;

    /// modeenv of the uc24-fde guest after its build (docs/design/ubuntu-core.md,
    /// "What U1 found"; the asset hashes cut short).
    pub(crate) const MODEENV_TEXT: &str = "mode=run\ncurrent_recovery_systems=20240603,bko-20261114\ngood_recovery_systems=20240603,bko-20261114\nbase=core24_2307.snap\ngadget=pc_196.snap\ncurrent_kernels=pc-kernel_3908.snap\nmodel=canonical/ubuntu-core-24-amd64\ngrade=signed\nmodel_sign_key_id=9tydnLa6MTJ-jaQTFUXEwHl1yRx7ZS4K5cyFDhYDcPzhS7uyEkDxdUjg9g08BtNn\ncurrent_trusted_boot_assets={\"grubx64.efi\":[\"e138\"]}\ncurrent_kernel_command_lines=[\"snapd_recovery_mode=run console=ttyS0,115200n8 console=tty1 panic=-1\"]\n";

    /// The stock model (canonical/ubuntu-core-24-amd64), its signature cut.
    pub(crate) const MODEL_TEXT: &str = "type: model\nauthority-id: canonical\nseries: 16\nbrand-id: canonical\nmodel: ubuntu-core-24-amd64\narchitecture: amd64\nbase: core24\ngrade: signed\nsnaps:\n  -\n    default-channel: 24/stable\n    id: UqFziVZDHLSyO3TqSWgNBoAdHbLI4dAH\n    name: pc\n    type: gadget\n  -\n    default-channel: 24/stable\n    id: pYVQrBcKmBa0mZ4CCN7ExT6jH8rY1hza\n    name: pc-kernel\n    type: kernel\n  -\n    default-channel: latest/stable\n    id: dwTAh7MZZ01zyriOZErqd1JynQLiOGvM\n    name: core24\n    type: base\n  -\n    default-channel: latest/stable\n    id: PMrrV4ml8uWuEUDBT8dSGnKUYbevVhc4\n    name: snapd\n    type: snapd\n  -\n    default-channel: 24/stable\n    id: ASctKBEHzVt3f1pbZLoekCvcigRjtuqw\n    name: console-conf\n    presence: optional\n    type: app\ntimestamp: 2024-04-19T08:42:32+00:00\nsign-key-sha3-384: 9tydnLa6MTJ-jaQTFUXEwHl1yRx7ZS4K5cyFDhYDcPzhS7uyEkDxdUjg9g08BtNn\n\nAcLBXAQAAQoABgUCZiLKhgAKCRDgT5vottzAEmmw\n";

    /// A GRUB environment block as grub-editenv writes it: 1024 bytes.
    pub(crate) fn grubenv(lines: &[&str]) -> Vec<u8> {
        let mut s = String::from("# GRUB Environment Block\n");
        for l in lines {
            s += l;
            s.push('\n');
        }
        let mut b = s.into_bytes();
        b.resize(1024, b'#');
        b
    }

    #[test]
    fn modeenv_lists() {
        let m = Env::parse(MODEENV_TEXT);
        assert_eq!(m.get("mode"), Some("run"));
        assert_eq!(m.list("current_recovery_systems"), vec!["20240603", "bko-20261114"]);
        assert_eq!(m.list("current_kernels"), vec!["pc-kernel_3908.snap"]);
        assert_eq!(m.some("base").as_deref(), Some("core24_2307.snap"));
        assert_eq!(m.some("recovery_system"), None);
        assert!(m.list("nothing").is_empty());
        // A value with '=' in it keeps them.
        assert_eq!(Env::parse("a=b=c\n").get("a"), Some("b=c"));
    }

    #[test]
    fn grubenv_block() {
        let e = parse_grubenv(&grubenv(&["snapd_recovery_mode=run", "snapd_recovery_system=", "snapd_good_recovery_systems=20240603,bko-20261114"]));
        assert_eq!(e.some("snapd_recovery_mode").as_deref(), Some("run"));
        assert_eq!(e.some("snapd_recovery_system"), None);
        assert_eq!(e.list("snapd_good_recovery_systems"), vec!["20240603", "bko-20261114"]);
        assert_eq!(e.0.len(), 3, "{:?}", e.0);
    }

    #[test]
    fn model_assertion() {
        let m = parse_model(MODEL_TEXT);
        assert_eq!(m.get("brand-id"), Some("canonical"));
        assert_eq!(m.get("grade"), Some("signed"));
        assert_eq!(m.get("base"), Some("core24"));
        assert_eq!(m.get("storage-safety"), None);
        assert_eq!(m.snap_of("gadget"), Some("pc"));
        assert_eq!(m.snap_of("kernel"), Some("pc-kernel"));
        assert_eq!(m.snaps.len(), 5);
        assert_eq!(m.snaps[4], ("console-conf".to_string(), "app".to_string()));
        // The signature after the blank line isn't a header.
        assert!(m.get("AcLBXAQAAQoABgUCZiLKhgAKCRDgT5vottzAEmmw").is_none());
        assert_eq!(m.get("timestamp"), Some("2024-04-19T08:42:32+00:00"));
    }

    #[test]
    fn snap_files_and_state() {
        assert_eq!(snap_file("core24_2307.snap"), Some(("core24".into(), "2307".into())));
        assert_eq!(snap_file("pc-kernel_3908.snap"), Some(("pc-kernel".into(), "3908".into())));
        assert_eq!(snap_file("hello_x1.snap"), Some(("hello".into(), "x1".into())));
        assert_eq!(snap_file("pc-kernel+wifi-comp_12.comp"), None);
        assert_eq!(snap_file("partial"), None);
        // Both sequence forms.
        let st = crate::json::parse(r#"{"data": {"snaps": {"hello": {"sequence": [{"name": "hello", "revision": 42}, {"name": "hello", "revision": 29}], "current": 29}, "core24": {"sequence": {"revisions": [{"snap": {"name": "core24", "revision": 423}}, {"snap": {"name": "core24", "revision": 2307}, "components": []}]}, "current": 2307}}}}"#).unwrap();
        assert_eq!(state_snaps(&st), vec![("core24".to_string(), Some("2307".to_string()), vec!["423".to_string(), "2307".to_string()]), ("hello".to_string(), Some("29".to_string()), vec!["42".to_string(), "29".to_string()])]);
        assert!(state_snaps(&crate::json::parse("{}").unwrap()).is_empty());
    }

    #[test]
    fn changes_under_way() {
        let out = "ID   Status  Spawn               Ready               Summary\n12   Done    today at 12:37 UTC  today at 12:40 UTC  Auto-refresh snaps\n13   Doing   today at 13:01 UTC  -                   Refresh \"pc-kernel\" snap\n14   Wait    today at 13:02 UTC  -                   Refresh \"pc\" snap\n15   Error   today at 13:03 UTC  today at 13:03 UTC  Install \"x\" snap\n";
        let c = changes_in_progress(out);
        assert_eq!(c.len(), 2);
        assert!(c[0].starts_with("13   Doing"));
        assert!(changes_in_progress("no changes found\n").is_empty());
    }

    /// The converter's format (U1: these bytes opened keyslot 1).
    #[test]
    fn recovery_key_file_refuses_a_key_as_its_name() {
        for out in ["00001-00002-65535-00256-12345-00000-54321-00010", "/tmp/k-00001-00002-65535-00256-12345-00000-54321-00010.bin"] {
            assert_eq!(recovery_key_main(&[out.to_string()]), 2);
            assert!(!std::path::Path::new(out).exists());
        }
    }

    #[test]
    fn recovery_key_converts() {
        let k = recovery_key_bytes("00001-00002-65535-00256-12345-00000-54321-00010\n").unwrap();
        assert_eq!(k, [1, 0, 2, 0, 0xff, 0xff, 0, 1, 0x39, 0x30, 0, 0, 0x31, 0xd4, 10, 0]);
        for (bad, why) in [
            ("00001-00002", "has 2 group(s)"),
            ("00001-00002-65536-00256-12345-00000-54321-00010", "group 3 of the recovery key is over 65535"),
            ("00001-0002-65535-00256-12345-00000-54321-00010", "group 2 of the recovery key isn't five digits"),
            ("00001-00002-6553a-00256-12345-00000-54321-00010", "group 3 of the recovery key isn't five digits"),
            ("", "has 1 group(s)"),
        ] {
            let e = recovery_key_bytes(bad).unwrap_err();
            assert!(e.contains(why), "{bad}: {e}");
            // Never the key itself.
            for g in bad.split('-').filter(|g| g.len() == 5) {
                assert!(!e.contains(g), "{e}");
            }
        }
    }
}
