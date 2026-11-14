//! ABRoot (Vanilla OS 2 and 3): how to tell a system runs it, and what the
//! dump records of its two roots and their boot setup (boot.abroot).
//! docs/design/vanillaos.md has the layout: two btrfs roots on LVM thin
//! LVs (labels `vos-a` and `vos-b`, partLabelA and partLabelB in
//! abroot.json), a linear `init` LV (label thinInitVolume, `vos-init`)
//! with both roots' kernels, initramfs and GRUB configs, an ext4 /boot with
//! a static master grub.cfg whose literal `set default=0|1` picks the
//! root, and /var on its own VG.  The running `/` is the present root,
//! read-only, with an overlay on /etc; the other root ("future", ABRoot's
//! word) and `init` aren't mounted at all.  dump mounts each of those two
//! read-only in its run directory to read it, and unmounts it at once (as
//! ABRoot itself mounts them only while it works), and lists both in
//! storage.backup_sources at ABRoot's own mount points (`/part-future`,
//! `/boot/init`) with the read-only mount a backup must make there.
//!
//! ABRoot finds every filesystem by its label (first match in lsblk), so
//! the dump does too.

use crate::json::{Obj, Value};
use crate::report::{BackupSource, MountHint};
use crate::sys::Sys;

/// abroot.json: the image's, then /etc's, merged (viper's MergeInConfig:
/// a key in the later file wins).
pub const CONFIGS: [&str; 2] = ["/usr/share/abroot/abroot.json", "/etc/abroot/abroot.json"];
/// The master GRUB config on `vos-boot`, installed once and swapped with
/// grub.cfg.future by each upgrade.
pub const GRUB_CFG: &str = "/boot/grub/grub.cfg";
pub const KARGS: &str = "/etc/abroot/kargs";
/// There while `abroot upgrade` (or `abroot pkg`) runs.
pub const LOCK: &str = "/run/abroot/operation.lock";
/// Where ABRoot mounts `init` and the future root itself, and where the
/// backup tree has them.
pub const INIT_AT: &str = "/boot/init";
pub const FUTURE_AT: &str = "/part-future";
/// dump's own read-only mounts: `/run/bko-holt/abroot-LABEL`.
const READ_AT: &str = "/run/bko-holt";
/// The options of dump's read-only mounts of a filesystem of type TY, and
/// of those backup_sources asks a backup to make: `ro` alone still replays
/// a dirty ext4 journal or a btrfs log tree (a write to the source disk
/// after an unclean shutdown), so ext3/ext4 get `noload` and btrfs
/// `rescue=nologreplay` (the bare `nologreplay` is its deprecated alias;
/// both need `ro`; Linux 5.9 and later).  Other types: plain ro.
pub fn ro_options(ty: &str) -> String {
    let extra = match ty {
        "ext4" | "ext3" => "noload,",
        "btrfs" => "rescue=nologreplay,",
        _ => "",
    };
    format!("ro,{extra}nodev,nosuid,noexec")
}

/// The merged abroot.json, keys as written, the later file's last.
pub struct Config {
    keys: Vec<(String, Value)>,
    pub files: Vec<&'static str>,
}

impl Config {
    /// A key's value: viper matches keys whatever their case, and the last
    /// one read wins (the installer's /etc copy repeats the image's keys,
    /// `thinProvisioning: false` first and `true` after).
    pub fn get(&self, key: &str) -> Option<&Value> {
        self.keys.iter().rev().find(|(k, _)| k.eq_ignore_ascii_case(key)).map(|(_, v)| v)
    }

    /// A string key, None when missing or empty.
    pub fn str(&self, key: &str) -> Option<String> {
        self.get(key).and_then(Value::as_str).filter(|s| !s.is_empty()).map(str::to_string)
    }

    /// thinProvisioning, as viper's GetBool reads it.
    pub fn thin(&self) -> bool {
        match self.get("thinProvisioning") {
            Some(Value::Bool(b)) => *b,
            Some(Value::Str(s)) => matches!(s.to_ascii_lowercase().as_str(), "true" | "1" | "t"),
            _ => false,
        }
    }
}

/// abroot.json on the running root, when there is one: the marker of an
/// ABRoot system (os-release says `vanilla`, but ABRoot is what matters).
pub fn config(sys: &Sys) -> Option<Config> {
    let mut keys = Vec::new();
    let mut files = Vec::new();
    for f in CONFIGS {
        let Some(t) = sys.read(f) else { continue };
        files.push(f);
        match crate::json::parse(&t) {
            Ok(Value::Obj(kv)) => keys.extend(kv),
            _ => sys.warn(format!("ABRoot: {f} isn't a JSON object; its keys are left out of boot.abroot.config")),
        }
    }
    (!files.is_empty()).then_some(Config { keys, files })
}

/// A filesystem the dump found, with its label: ABRoot's way of finding
/// its roots, `init` and /var.
#[derive(Debug, Clone, PartialEq)]
pub struct Fs {
    /// The kernel name (dm-4).
    pub dev: String,
    pub label: String,
    pub uuid: Option<String>,
    pub fstype: Option<String>,
    pub dm_name: Option<String>,
    /// Where the running system mounts it, `/` first.
    pub mounted_at: Option<String>,
}

impl Fs {
    /// The node to mount it by.
    pub fn node(&self) -> String {
        match &self.dm_name {
            Some(n) => format!("/dev/mapper/{n}"),
            None => format!("/dev/{}", self.dev),
        }
    }

    fn json(&self) -> Obj {
        Obj::new()
            .set("label", self.label.as_str())
            .set("device", self.dev.as_str())
            .set("uuid", self.uuid.clone())
            .set("fstype", self.fstype.clone())
            .set("dm_name", self.dm_name.clone())
            .set("mounted_at", self.mounted_at.clone())
    }
}

/// One menuentry of the master grub.cfg.
#[derive(Debug, Clone, PartialEq, Default)]
pub struct Entry {
    pub title: String,
    pub class: Option<String>,
    /// Its `set root=` (`(lvm/vos--root-init)`).
    pub root: Option<String>,
    /// Its `configfile` path (`/vos-a/abroot.cfg`).
    pub configfile: Option<String>,
}

impl Entry {
    /// The root label the entry leads to: the first directory of its
    /// configfile, in `init`.
    pub fn label(&self) -> Option<String> {
        let p = self.configfile.as_deref()?;
        let p = strip_device(p).trim_start_matches('/');
        let (dir, rest) = p.split_once('/')?;
        (!dir.is_empty() && !rest.is_empty()).then(|| dir.to_string())
    }
}

/// A GRUB path without its `(device)` prefix.
pub fn strip_device(p: &str) -> &str {
    match p.strip_prefix('(').and_then(|r| r.split_once(')')) {
        Some((_, rest)) => rest,
        None => p,
    }
}

fn unquote(s: &str) -> String {
    s.trim().trim_matches('"').trim_matches('\'').to_string()
}

/// The master grub.cfg: its `set default=` (the first) and its menuentries.
pub fn parse_master(text: &str) -> (Option<String>, Vec<Entry>) {
    let mut default = None;
    let mut entries: Vec<Entry> = Vec::new();
    let mut inside = false;
    for line in text.lines() {
        let l = line.trim();
        if l.starts_with('#') {
            continue;
        }
        if let Some(rest) = l.strip_prefix("menuentry ") {
            let title = match rest.trim_start().strip_prefix('"').and_then(|r| r.split_once('"')) {
                Some((t, _)) => t.to_string(),
                None => rest.split_whitespace().next().map(unquote).unwrap_or_default(),
            };
            let words: Vec<&str> = rest.split_whitespace().collect();
            let class = words.iter().position(|w| *w == "--class").and_then(|i| words.get(i + 1)).map(|w| unquote(w));
            entries.push(Entry { title, class, ..Entry::default() });
            inside = true;
            continue;
        }
        if l.starts_with('}') {
            inside = false;
            continue;
        }
        if !inside {
            if default.is_none() {
                if let Some(v) = l.strip_prefix("set default=") {
                    default = Some(unquote(v));
                }
            }
            continue;
        }
        let e = entries.last_mut().expect("inside an entry");
        if let Some(v) = l.strip_prefix("set root=") {
            e.root = Some(unquote(v));
        } else if let Some(v) = l.strip_prefix("configfile ") {
            e.configfile = Some(unquote(v));
        }
    }
    (default, entries)
}

/// What a root's `init/LABEL/abroot.cfg` says about LABEL's boot: the
/// first `linux` line whose kernel is in /LABEL/ (`/vos-b/vmlinuz-K`, or
/// `(lvm/vos--root-init)/vos-a/vmlinuz-K`), its `root=`, the first `initrd`
/// line after it, and the UUID of the last `search --fs-uuid` before it in
/// the same block (an upgrade's abroot.cfg is grub-mkconfig's whole output,
/// whose 00_header can `search --fs-uuid` for its font first; the
/// installer's names `(lvm/vos--root-init)` and has no search).
#[derive(Debug, Clone, PartialEq, Default)]
pub struct RootCfg {
    pub linux: Option<String>,
    pub kernel: Option<String>,
    pub initrd: Option<String>,
    pub root: Option<String>,
    pub search_uuid: Option<String>,
}

pub fn parse_abroot_cfg(text: &str, label: &str) -> RootCfg {
    let mut c = RootCfg::default();
    let mut search: Option<String> = None;
    let prefix = format!("/{label}/");
    for line in text.lines() {
        let w: Vec<&str> = line.split_whitespace().collect();
        match w.first() {
            Some(&("menuentry" | "submenu" | "}")) => search = None,
            Some(&"search") => {
                search = w.iter().any(|x| *x == "--fs-uuid" || *x == "-u").then(|| w.last().filter(|x| !x.starts_with('-')).map(|s| s.to_string())).flatten();
            }
            Some(&"linux") if c.linux.is_none() && w.get(1).is_some_and(|k| strip_device(k).starts_with(&prefix)) => {
                c.linux = Some(line.trim().to_string());
                c.kernel = w.get(1).map(|s| s.to_string());
                c.root = w.iter().skip(2).find_map(|x| x.strip_prefix("root=")).map(str::to_string);
                c.search_uuid = search.clone();
            }
            Some(&"initrd") if c.linux.is_some() && c.initrd.is_none() => c.initrd = w.get(1).map(|s| s.to_string()),
            _ => {}
        }
    }
    c
}

/// boot.abroot and what goes with it in the rest of the dump.
pub struct Dump {
    pub json: Value,
    /// The entries storage.backup_sources gets: `/boot/init` and
    /// `/part-future`, unless something already mounts them there.
    pub sources: Vec<BackupSource>,
    /// The note the `/` entry gets.
    pub root_note: String,
    /// boot.kernels.installed's entries for the kernels in `init`.
    pub kernels: Vec<Value>,
    /// (fs UUID, allocation profiles) of each btrfs root dump mounted
    /// itself (the future root): sysfs has them only while it is mounted,
    /// so storage.btrfs takes them from here (crate::report::btrfs_json),
    /// and load's mkfs.btrfs gets -d/-m as for any btrfs.
    pub btrfs_profiles: Vec<(String, Value)>,
}

/// The note for the `/` entry of backup_sources.
const ROOT_NOTE: &str = "ABRoot's present root: its /etc is an overlay (upper /var/lib/abroot/etc/LABEL), so read / through a non-recursive bind mount (mount --bind / DIR) to back up the raw lower /etc, the tree load restores";
const HINT_NOTE: &str = "not mounted on the running system: a backup mounts it read-only here as `mount` says, reads it, and unmounts it";

/// A label safe in a path of dump's run directory.
fn safe(label: &str) -> bool {
    !label.is_empty() && label.bytes().all(|b| b.is_ascii_alphanumeric() || matches!(b, b'-' | b'_' | b'.'))
}

/// F(BASE) with FS readable at BASE: where the running system mounts it,
/// else a read-only mount of dump's own (unless MAY_MOUNT is false: an
/// upgrade is running), removed after.  None when it can't be read (not
/// root, or the mount failed: said).
fn with_mounted<R>(sys: &Sys, fs: &Fs, may_mount: bool, f: impl FnOnce(&str) -> R) -> Option<R> {
    if let Some(m) = &fs.mounted_at {
        return Some(f(m));
    }
    let ty = fs.fstype.clone()?;
    if !may_mount || !sys.privileged || !safe(&fs.label) {
        return None;
    }
    let at = format!("{READ_AT}/abroot-{}", fs.label);
    if let Err(e) = std::fs::create_dir_all(sys.p(&at)) {
        sys.warn(format!("ABRoot: can't make {at} to read {} ({}): {e}", fs.label, fs.dev));
        return None;
    }
    if sys.run("mount", &["-t", &ty, "-o", &ro_options(&ty), &fs.node(), &at]).is_none() {
        sys.warn(format!("ABRoot: a read-only mount of {} ({}) at {at} failed, so boot.abroot doesn't say what it holds", fs.label, fs.node()));
        let _ = std::fs::remove_dir(sys.p(&at));
        return None;
    }
    let r = f(&at);
    if sys.run("umount", &[&at]).is_some() {
        let _ = std::fs::remove_dir(sys.p(&at));
    } else {
        sys.warn(format!("ABRoot: {} is still mounted read-only at {at}; `umount {at}` when this dump is done", fs.label));
    }
    Some(r)
}

/// What `init/LABEL/` holds, read at BASE (init's mount): its files, the
/// kernel versions, abroot.cfg.
fn init_dir(sys: &Sys, base: &str, label: &str) -> (Value, Vec<(String, Option<u64>, Option<u64>, bool)>) {
    let dir = format!("{base}/{label}");
    let files = sys.list(&dir);
    let mut kernels = Vec::new();
    for f in &files {
        if let Some(k) = f.strip_prefix("vmlinuz-") {
            kernels.push((k.to_string(), sys.file_size(&format!("{dir}/{f}")), sys.file_size(&format!("{dir}/initrd.img-{k}")), sys.is_file(&format!("{dir}/config-{k}"))));
        }
    }
    let cfg = sys.read(&format!("{dir}/abroot.cfg")).map(|t| {
        let c = parse_abroot_cfg(&t, label);
        Obj::new().set("linux", c.linux).set("kernel", c.kernel).set("initrd", c.initrd).set("root", c.root).set("search_uuid", c.search_uuid).build()
    });
    let v = Obj::new()
        .set("exists", sys.is_dir(&dir))
        .set("files", files.clone())
        .set("kernels", kernels.iter().map(|k| k.0.clone()).collect::<Vec<_>>())
        .set("abroot_cfg", cfg)
        .build();
    (v, kernels)
}

/// A root's /abimage.abr (ABRoot's record of the image extracted into
/// it), and whether the root is empty (no usr/: a fresh install's other
/// root), read at BASE.
fn root_state(sys: &Sys, base: &str) -> (Value, bool) {
    let base = base.trim_end_matches('/');
    let abimage = sys.read(&format!("{base}/abimage.abr")).map(|t| match crate::json::parse(&t) {
        Ok(v) => {
            let g = |k: &str| v.get(k).and_then(Value::as_str).map(str::to_string);
            Obj::new().set("digest", g("digest")).set("timestamp", g("timestamp")).set("image", g("image")).build()
        }
        Err(_) => Obj::new().set("unparsed", true).build(),
    });
    (abimage.unwrap_or(Value::Null), !sys.exists(&format!("{base}/usr")))
}

/// boot.abroot for a running ABRoot system.  FSS are the filesystems the
/// dump found, with their labels and mounts.
pub fn read(sys: &Sys, cfg: &Config, fss: &[Fs]) -> Dump {
    let by_label = |l: &Option<String>| l.as_deref().and_then(|l| fss.iter().find(|f| f.label == l));
    let (la, lb) = (cfg.str("partLabelA"), cfg.str("partLabelB"));
    let labels: Vec<String> = [la.clone(), lb.clone()].into_iter().flatten().collect();
    let init_label = cfg.str("thinInitVolume");
    let init = by_label(&init_label);
    let var = by_label(&cfg.str("partLabelVar"));
    let present = labels.iter().find(|l| by_label(&Some(l.to_string())).is_some_and(|f| f.mounted_at.as_deref() == Some("/"))).cloned();
    let future = present.as_ref().and_then(|p| labels.iter().find(|l| *l != p).cloned());
    let running = sys.read_trim("/proc/sys/kernel/osrelease");
    // An upgrade running (its lock, looked at before any mount): dump makes
    // no mounts of its own, so that `abroot upgrade` doesn't find the init
    // LV or the other root busy; the plan refuses such a dump anyway.
    let locked = sys.exists(LOCK);
    if locked {
        sys.warn(format!("ABRoot: {LOCK} is there (an abroot upgrade or pkg is running): the init LV and the other root weren't mounted to read them; dump again once it has finished"));
    }

    // The master grub.cfg.
    let grub = sys.read(GRUB_CFG).map(|t| {
        let (default, entries) = parse_master(&t);
        let default_label = default.as_deref().and_then(|d| d.parse::<usize>().ok()).and_then(|i| entries.get(i)).and_then(Entry::label);
        let ev: Vec<Value> = entries
            .iter()
            .map(|e| Obj::new().set("title", e.title.as_str()).set("class", e.class.clone()).set("root", e.root.clone()).set("configfile", e.configfile.clone()).set("label", e.label()).build())
            .collect();
        Obj::new().set("file", GRUB_CFG).set("default", default).set("default_label", default_label).set("entries", ev).set("future_cfg", sys.is_file(&format!("{GRUB_CFG}.future"))).build()
    });

    // init: both roots' directories, from one read.
    let mut init_dirs: Vec<(String, Value, Vec<(String, Option<u64>, Option<u64>, bool)>)> = Vec::new();
    let init_read = init.and_then(|f| {
        with_mounted(sys, f, !locked, |base| {
            for l in &labels {
                let (v, k) = init_dir(sys, base, l);
                init_dirs.push((l.clone(), v, k));
            }
        })
    });

    // Each root: its state, read where it's mounted (`/`) or by dump.
    let mut roots = Vec::new();
    let mut kernels = Vec::new();
    let mut btrfs_profiles = Vec::new();
    for l in &labels {
        let fs = by_label(&Some(l.clone()));
        // (A btrfs root mounted by dump itself: its allocation profiles too,
        // read from sysfs while the mount is there.)
        let state = fs.and_then(|f| {
            with_mounted(sys, f, !locked, |base| {
                let alloc = if f.mounted_at.is_none() && f.fstype.as_deref() == Some("btrfs") { f.uuid.as_deref().and_then(|u| crate::report::btrfs_allocation(sys, u).map(|v| (u.to_string(), v))) } else { None };
                (root_state(sys, base), base == "/", alloc)
            })
        });
        if let Some((_, _, Some(p))) = &state {
            btrfs_profiles.push(p.clone());
        }
        let modules = |base: &str, k: &str| sys.is_dir(&format!("{}/usr/lib/modules/{k}", base.trim_end_matches('/'))) || sys.is_dir(&format!("{}/lib/modules/{k}", base.trim_end_matches('/')));
        let is_present = present.as_deref() == Some(l.as_str());
        let dir = init_dirs.iter().find(|(x, _, _)| x == l);
        // A kernel's modules are in its own root: `/` for the present one,
        // the read-only mount (gone by now) for the other; read again only
        // for the present root, else left null.
        for (k, vb, ib, config) in dir.map(|d| d.2.clone()).unwrap_or_default() {
            let modules_installed = if is_present { Value::Bool(modules("/", &k)) } else { Value::Null };
            kernels.push(
                Obj::new()
                    .set("version", k.as_str())
                    .set("dir", format!("{INIT_AT}/{l}"))
                    .set("root_label", l.as_str())
                    .set("vmlinuz_bytes", vb)
                    .set("initrd_bytes", ib)
                    .set("config", config)
                    .set("running", is_present && running.as_deref() == Some(k.as_str()))
                    .set("modules_installed", modules_installed)
                    .build(),
            );
        }
        let mut o = fs.map(Fs::json).unwrap_or_else(|| Obj::new().set("label", l.as_str()).set("device", Value::Null));
        o.put("present", is_present);
        o.put("read", state.is_some());
        o.put("abimage", state.as_ref().map(|((a, _), _, _)| a.clone()).unwrap_or(Value::Null));
        o.put("empty", state.as_ref().map(|((_, e), _, _)| *e));
        o.put("init", dir.map(|d| d.1.clone()).unwrap_or(Value::Null));
        roots.push(o.build());
    }

    let mut config = Obj::new();
    for k in ["partLabelA", "partLabelB", "partLabelVar", "partLabelBoot", "partLabelEfi", "PartCryptVar", "thinInitVolume"] {
        config.put(k, cfg.str(k));
    }
    config.put("thinProvisioning", cfg.thin());
    config.put("files", cfg.files.clone());
    let thin_two_roots = cfg.thin() && init.is_some() && labels.len() == 2 && labels.iter().all(|l| by_label(&Some(l.clone())).is_some()) && la != lb;
    let json = Obj::new()
        .set("config", config)
        .set("thin_two_roots", thin_two_roots)
        .set("v1_system", sys.is_dir("/.system"))
        .set("present", present.clone())
        .set("future", future.clone())
        .set("grub", grub)
        .set("init", init.map(|f| f.json().set("read", init_read.is_some()).build()))
        .set("var", var.map(|f| f.json().build()))
        .set("roots", roots)
        .set("kargs", sys.read_trim(KARGS))
        .set("operation_lock", locked)
        // Whether dump left its own read-only mounts out (the lock).
        .set("mounts_skipped", locked)
        .build();

    // backup_sources: init and the future root where ABRoot mounts them,
    // with the read-only mount a backup makes there.
    let mut sources = Vec::new();
    let hinted = |path: &str, f: &Fs| BackupSource {
        path: path.to_string(),
        from: None,
        device: Some(f.dev.clone()),
        fstype: f.fstype.clone().unwrap_or_default(),
        source: f.node(),
        subvol: None,
        mount: Some(MountHint { device: f.node(), fstype: f.fstype.clone().unwrap_or_default(), options: ro_options(f.fstype.as_deref().unwrap_or("")) }),
        note: Some(HINT_NOTE.to_string()),
    };
    if let Some(f) = init.filter(|f| f.mounted_at.is_none()) {
        sources.push(hinted(INIT_AT, f));
    }
    if let Some(f) = by_label(&future).filter(|f| f.mounted_at.is_none()) {
        sources.push(hinted(FUTURE_AT, f));
    }
    Dump { json, sources, root_note: ROOT_NOTE.to_string(), kernels, btrfs_profiles }
}

/// BOOT (the dump's boot section) with boot.abroot in it and the init
/// LV's kernels in boot.kernels.installed.
pub fn add_to_boot(boot: Value, d: Option<&Dump>) -> Value {
    let (Some(d), Value::Obj(mut o)) = (d, boot.clone()) else { return boot };
    for (k, v) in o.iter_mut() {
        if k == "kernels" {
            if let Value::Obj(ko) = v {
                for (kk, kv) in ko.iter_mut() {
                    if kk == "installed" {
                        if let Value::Arr(a) = kv {
                            a.extend(d.kernels.iter().cloned());
                        }
                    }
                }
            }
        }
    }
    o.push(("abroot".into(), d.json.clone()));
    Value::Obj(o)
}

/// The installer's master grub.cfg, for other modules' tests.
#[cfg(test)]
pub(crate) fn tests_master() -> &'static str {
    tests::MASTER
}

/// The 00_header an upgrade's abroot.cfg begins with, for other modules' tests.
#[cfg(test)]
pub(crate) fn tests_upgrade_header() -> &'static str {
    tests::UPGRADE_HEADER
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::sys::FakeRunner;
    use crate::testutil::Tree;

    /// Vanilla's installer's master grub.cfg (vanilla-installer's
    /// processor.py, _BOOT_GRUB_CFG), its header cut short.
    pub(crate) const MASTER: &str = "set default=0\nset timeout=5\n\n### BEGIN /etc/grub.d/00_header ###\nif [ -s $prefix/grubenv ]; then\n  set have_grubenv=true\n  load_env\nfi\nfunction savedefault {\n  if [ -z \"${boot_once}\" ]; then\n    saved_entry=\"${chosen}\"\n    save_env saved_entry\n  fi\n}\nif [ \"${recordfail}\" = 1 ] ; then\n  set timeout=30\nelse\n  set timeout=5\nfi\n### END /etc/grub.d/00_header ###\n\n# AUTO GENERATED BY ABROOT\nmenuentry \"Current State (A)\" --class abroot-a {\n    set root=(lvm/vos--root-init)\n    configfile \"/vos-a/abroot.cfg\"\n}\n\nmenuentry \"Previous State (B)\" --class abroot-b {\n    set root=(lvm/vos--root-init)\n    configfile \"/vos-b/abroot.cfg\"\n}\n# END - AUTO GENERATED BY ABROOT\n";

    #[test]
    fn master_grub_cfg() {
        let (d, e) = parse_master(MASTER);
        assert_eq!(d.as_deref(), Some("0"));
        assert_eq!(e.len(), 2);
        assert_eq!(e[0].title, "Current State (A)");
        assert_eq!(e[0].class.as_deref(), Some("abroot-a"));
        assert_eq!(e[0].root.as_deref(), Some("(lvm/vos--root-init)"));
        assert_eq!(e[0].configfile.as_deref(), Some("/vos-a/abroot.cfg"));
        assert_eq!(e[0].label().as_deref(), Some("vos-a"));
        assert_eq!(e[1].label().as_deref(), Some("vos-b"));
        // An upgrade's swap: default=1, the titles traded.
        let (d, e) = parse_master(&MASTER.replace("default=0", "default=1").replace("Current State (A)", "Previous State (A)").replace("Previous State (B)", "Current State (B)"));
        assert_eq!(d.as_deref(), Some("1"));
        assert_eq!(e[1].title, "Current State (B)");
        // A configfile with a device, or none at all.
        assert_eq!(Entry { configfile: Some("(lvm/vos--root-init)/vos-b/abroot.cfg".into()), ..Entry::default() }.label().as_deref(), Some("vos-b"));
        assert_eq!(Entry { configfile: Some("/abroot.cfg".into()), ..Entry::default() }.label(), None);
        assert_eq!(Entry::default().label(), None);
        assert_eq!(strip_device("(lvm/x)/vos-a/vmlinuz-6"), "/vos-a/vmlinuz-6");
        assert_eq!(strip_device("/vos-a/vmlinuz-6"), "/vos-a/vmlinuz-6");
    }

    #[test]
    fn root_cfgs() {
        // The installer's (processor.py's _ROOT_GRUB_CFG).
        let c = parse_abroot_cfg("insmod gzio\ninsmod part_gpt\ninsmod ext2\nlinux   (lvm/vos--root-init)/vos-a/vmlinuz-6.17.8-amd64 root=UUID=b0b1b2b3-b4b5-b6b7-b8b9-babbbcbdbebf quiet splash bgrt_disable $vt_handoff lsm=integrity\ninitrd  (lvm/vos--root-init)/vos-a/initrd.img-6.17.8-amd64\n", "vos-a");
        assert_eq!(c.kernel.as_deref(), Some("(lvm/vos--root-init)/vos-a/vmlinuz-6.17.8-amd64"));
        assert_eq!(c.initrd.as_deref(), Some("(lvm/vos--root-init)/vos-a/initrd.img-6.17.8-amd64"));
        assert_eq!(c.root.as_deref(), Some("UUID=b0b1b2b3-b4b5-b6b7-b8b9-babbbcbdbebf"));
        assert_eq!(c.search_uuid, None);
        // An upgrade's (core/grub.go's template inside grub-mkconfig's output).
        // Its 00_header searches for the font's filesystem first, and a
        // stray linux line of another label (memtest, a leftover) comes
        // before: neither counts.
        let c = parse_abroot_cfg(&format!("{UPGRADE_HEADER}  linux   /vos-a/vmlinuz-old root=UUID=x\n### BEGIN /etc/grub.d/10_linux ###\n  search --no-floppy --fs-uuid --set=root 20111213-1415-1617-1819-1a1b1c1d1e1f\n  linux   /vos-b/vmlinuz-6.18.1-amd64 root=/dev/mapper/vos--root-root--b quiet splash\n  initrd  /vos-b/initrd.img-6.18.1-amd64\n"), "vos-b");
        assert_eq!(c.kernel.as_deref(), Some("/vos-b/vmlinuz-6.18.1-amd64"));
        assert_eq!(c.initrd.as_deref(), Some("/vos-b/initrd.img-6.18.1-amd64"));
        assert_eq!(c.root.as_deref(), Some("/dev/mapper/vos--root-root--b"));
        assert_eq!(c.search_uuid.as_deref(), Some("20111213-1415-1617-1819-1a1b1c1d1e1f"));
        // A menuentry's own search doesn't reach past it.
        let c = parse_abroot_cfg("menuentry m {\n search --fs-uuid --set=root 1111\n}\nlinux /vos-a/vmlinuz-6 root=UUID=x\n", "vos-a");
        assert_eq!((c.kernel.as_deref(), c.search_uuid), (Some("/vos-a/vmlinuz-6"), None));
        assert_eq!(parse_abroot_cfg("", "vos-a"), RootCfg::default());
        assert_eq!(parse_abroot_cfg("linux /vos-b/vmlinuz-6 root=x\n", "vos-a"), RootCfg::default());
    }

    /// grub-mkconfig's 00_header as an upgrade's abroot.cfg begins: a
    /// font search of / by UUID, which isn't the init LV.
    pub(crate) const UPGRADE_HEADER: &str = "### BEGIN /etc/grub.d/00_header ###\nif [ -s $prefix/grubenv ]; then\n  load_env\nfi\nfunction load_video {\n  insmod all_video\n}\nif [ x$feature_default_font_path = xy ] ; then\n   font=unicode\nelse\ninsmod part_gpt\ninsmod btrfs\nsearch --no-floppy --fs-uuid --set=root c0b1b2b3-b4b5-b6b7-b8b9-babbbcbdbebf\n    font=\"/usr/share/grub/unicode.pf2\"\nfi\n### END /etc/grub.d/00_header ###\n";

    #[test]
    fn ro_options_by_type() {
        assert_eq!(ro_options("ext4"), "ro,noload,nodev,nosuid,noexec");
        assert_eq!(ro_options("btrfs"), "ro,rescue=nologreplay,nodev,nosuid,noexec");
        assert_eq!(ro_options("xfs"), "ro,nodev,nosuid,noexec");
    }

    /// The installer's /etc/abroot/abroot.json is the image's with its last
    /// line cut and keys appended: thinProvisioning twice, the later wins,
    /// as in viper.  Keys match whatever their case.
    #[test]
    fn config_merges_last_wins() {
        let t = Tree::new("abroot-config");
        let base = "{\n  \"partLabelVar\": \"vos-var\",\n  \"partLabelA\": \"vos-a\",\n  \"partLabelB\": \"vos-b\",\n  \"PartCryptVar\": \"/dev/mapper/vos--var-var\",\n  \"thinProvisioning\": false,\n  \"thinInitVolume\": \"\"\n}\n";
        t.file(CONFIGS[0], base);
        let s = Sys::new(t.root(), Box::new(FakeRunner::default()), false);
        let c = config(&s).unwrap();
        assert!(!c.thin());
        assert_eq!(c.str("thinInitVolume"), None);
        let cut: Vec<&str> = base.lines().collect();
        t.file(CONFIGS[1], format!("{},\n  \"PartCryptVar\": \"/dev/mapper/vos--var-var\",\n  \"thinProvisioning\": true,\n    \"thinInitVolume\": \"vos-init\"\n}}\n", cut[..cut.len() - 1].join("\n")));
        let c = config(&s).unwrap();
        assert!(c.thin());
        assert_eq!(c.str("thininitvolume").as_deref(), Some("vos-init"));
        assert_eq!(c.str("partLabelA").as_deref(), Some("vos-a"));
        assert_eq!(c.files, CONFIGS.to_vec());
        // Not JSON: said, and the other file's keys still count.
        t.file(CONFIGS[1], "not json");
        let c = config(&s).unwrap();
        assert!(!c.thin() && c.str("partLabelB").as_deref() == Some("vos-b"));
        assert!(s.warnings()[0].contains("/etc/abroot/abroot.json isn't a JSON object"), "{:?}", s.warnings());
        assert!(config(&Sys::new(Tree::new("abroot-none").root(), Box::new(FakeRunner::default()), false)).is_none());
    }

    /// Unprivileged, nothing is mounted: init and the other root unread,
    /// both still in backup_sources with their mounts; one already mounted
    /// (ABRoot's own, during an upgrade) is read there and not listed.
    #[test]
    fn unread_and_already_mounted() {
        let t = Tree::new("abroot-read");
        t.file(CONFIGS[0], "{\"partLabelA\": \"vos-a\", \"partLabelB\": \"vos-b\", \"partLabelVar\": \"vos-var\", \"thinProvisioning\": true, \"thinInitVolume\": \"vos-init\"}")
            .file("/abimage.abr", "{\"digest\":\"sha256:aa\",\"timestamp\":\"2026-11-10T00:00:00Z\",\"image\":\"ghcr.io/vanilla-os/gnome:main\"}")
            .file("/usr/lib/modules/6.17.8-amd64/modules.dep", "")
            .file("/part-future/abimage.abr", "{\"digest\":\"sha256:bb\"}")
            .file("/part-future/usr/x", "")
            .file(GRUB_CFG, MASTER)
            .file(LOCK, "");
        let fs = |dev: &str, label: &str, ty: &str, dm: &str, at: Option<&str>| Fs { dev: dev.into(), label: label.into(), uuid: Some(format!("{label}-uuid")), fstype: Some(ty.into()), dm_name: Some(dm.into()), mounted_at: at.map(str::to_string) };
        let fss = [fs("dm-0", "vos-init", "ext4", "vos--root-init", None), fs("dm-4", "vos-a", "btrfs", "vos--root-root--a", Some("/")), fs("dm-5", "vos-b", "btrfs", "vos--root-root--b", None), fs("dm-7", "vos-var", "btrfs", "vos--var-var", Some("/var"))];
        // Privileged, but an upgrade is running: no mount of dump's own is
        // tried (the runner answers none: a try would warn that it failed).
        let s = Sys::new(t.root(), Box::new(FakeRunner::default()), true);
        let c = config(&s).unwrap();
        let d = read(&s, &c, &fss);
        let j = &d.json;
        assert_eq!(j.get("present").and_then(Value::as_str), Some("vos-a"));
        assert_eq!(j.get("future").and_then(Value::as_str), Some("vos-b"));
        assert_eq!(j.get("thin_two_roots"), Some(&Value::Bool(true)));
        assert_eq!(j.get("operation_lock"), Some(&Value::Bool(true)));
        assert_eq!(j.get("mounts_skipped"), Some(&Value::Bool(true)));
        assert_eq!(j.at(&["grub", "default_label"]).and_then(Value::as_str), Some("vos-a"));
        assert_eq!(j.at(&["init", "read"]), Some(&Value::Bool(false)));
        assert_eq!(j.at(&["roots", "0", "abimage", "digest"]).and_then(Value::as_str), Some("sha256:aa"));
        assert_eq!(j.at(&["roots", "0", "empty"]), Some(&Value::Bool(false)));
        assert_eq!(j.at(&["roots", "1", "read"]), Some(&Value::Bool(false)));
        assert!(j.at(&["roots", "1", "empty"]).is_some_and(Value::is_null));
        assert_eq!(s.warnings().len(), 1, "{:?}", s.warnings());
        assert!(s.warnings()[0].starts_with("ABRoot: /run/abroot/operation.lock is there"), "{:?}", s.warnings());
        let paths: Vec<(&str, Option<&str>, &str, &str)> = d.sources.iter().map(|b| (b.path.as_str(), b.from.as_deref(), b.mount.as_ref().unwrap().device.as_str(), b.mount.as_ref().unwrap().options.as_str())).collect();
        assert_eq!(paths, vec![("/boot/init", None, "/dev/mapper/vos--root-init", "ro,noload,nodev,nosuid,noexec"), ("/part-future", None, "/dev/mapper/vos--root-root--b", "ro,rescue=nologreplay,nodev,nosuid,noexec")]);
        // Without the lock, dump does mount them (here the mounts fail).
        std::fs::remove_file(t.p(LOCK)).unwrap();
        let s2 = Sys::new(t.root(), Box::new(FakeRunner::default()), true);
        let d2 = read(&s2, &c, &fss);
        assert_eq!(d2.json.get("mounts_skipped"), Some(&Value::Bool(false)));
        assert!(s2.warnings().iter().any(|w| w.contains("a read-only mount of vos-init")) && s2.warnings().iter().any(|w| w.contains("a read-only mount of vos-b")), "{:?}", s2.warnings());
        t.file(LOCK, "");
        assert!(d.kernels.is_empty());
        // The other root mounted by ABRoot at /part-future: read there.
        let mut fss2 = fss.clone();
        fss2[2].mounted_at = Some("/part-future".into());
        let d = read(&s, &c, &fss2);
        assert_eq!(d.json.at(&["roots", "1", "abimage", "digest"]).and_then(Value::as_str), Some("sha256:bb"));
        assert_eq!(d.json.at(&["roots", "1", "empty"]), Some(&Value::Bool(false)));
        assert_eq!(d.sources.len(), 1);
        // Not thin (a manual layout): still boot.abroot, but not the scheme.
        t.file(CONFIGS[1], "{\"thinProvisioning\": false}");
        let d = read(&s, &config(&s).unwrap(), &fss);
        assert_eq!(d.json.get("thin_two_roots"), Some(&Value::Bool(false)));
    }

    /// add_to_boot puts boot.abroot last and the init kernels after /boot's.
    #[test]
    fn adds_to_boot() {
        let boot = crate::json::parse(r#"{"loader": {}, "kernels": {"installed": [{"version": "x"}], "symlinks": {}}}"#).unwrap();
        let d = Dump { json: Obj::new().set("present", "vos-a").build(), sources: vec![], root_note: String::new(), kernels: vec![Obj::new().set("version", "6").build()], btrfs_profiles: vec![] };
        let b = add_to_boot(boot.clone(), Some(&d));
        assert_eq!(b.at(&["kernels", "installed", "1", "version"]).and_then(Value::as_str), Some("6"));
        assert_eq!(b.at(&["abroot", "present"]).and_then(Value::as_str), Some("vos-a"));
        assert_eq!(add_to_boot(boot.clone(), None), boot);
    }
}
