//! `load` for KDE Linux (docs/design/kde-linux.md, K3).  The backup's tree
//! is the btrfs top level at `/` and the ESP at `/@system/boot` (dump's
//! `storage.backup_sources`, `crate::sysupdate`); there is no fstab, so the
//! mount stage builds that tree from the layout: the top level (`subvol=/`)
//! at the target, the ESP at `@system/boot` in it.  Format makes every
//! subvolume (each `etc_V.mstack`, `@system`, `@system/etc`,
//! `@system/.etc.<epoch>`, each home and snapper's snapshots) as on the
//! source; simple quotas come from `mkfs -O squota` alone
//! (`Builder::btrfs_quota`).
//!
//! The boot stage regenerates nothing: the ESP, the UKIs, `loader.conf`,
//! the image files and the `/etc` layers come back as they were; no
//! initramfs, no `bootctl install`, no chroot (the restored `@system/usr` is
//! an empty mount point).  It checks that every UKI has its image file
//! with the dump's size, that the booted version's UKI and image are both
//! there, that the `/etc` layers and snapshots the dump had are back, and
//! that systemd-boot is on the ESP; makes the UEFI entries; writes its log;
//! and ends as every Linux boot stage does.

use super::RUN_DIR;
use super::commands::{Builder, Cmd, atimes_cmd};
use super::shared::{Mount, Sub};
use crate::json::Value;

/// What a restored KDE Linux tree has once the restore is done
/// (`checks::RESTORED_MARKER`): a regular file only the restore writes.
/// Not `etc.mstack.v`, which format makes (`mkdir -p` before the
/// `etc_V.mstack` subvolumes it creates), so it was there before any
/// restore.  `restored_marker` returns a `&'static str`, so it can't name
/// the booted version's image file; the boot stage's check tests that.
/// Dump records whether the source has it (`restored_marker_present`);
/// a source without it is a plan problem.
pub const MARKER: &str = checks::RESTORED_MARKER;
/// Where the boot stage's log goes in the restored tree (`@system`'s /var).
pub const LOG: &str = "@system/var/log/bko-holt";
/// Where the ESP is in the backup's tree.
pub const ESP_AT: &str = "/@system/boot";
/// The one layout version 1 restores (`boot.sysupdate.kdelinux.layout`).
pub const LAYOUT_V1: &str = "rootfsv3-etc-mstack";
/// The `/etc` overlay's lower layer, `etc_V.mstack/layer@0`'s target.
pub const LAYER0: &str = "/sysusr/usr/share/factory/etc";

/// The choices that rest on facts K1 hasn't settled yet (the spec's
/// *check*s), in one place.
pub mod checks {
    /// The restore gate's file (`super::MARKER`): `@system/etc/machine-id`,
    /// the installer's /etc subvolume's machine ID (Calamares' `machineid`
    /// module, install.sh's `systemd-machine-id-setup --root`), kept after
    /// etc-mstack's first migration.  A regular file, never made by format,
    /// which makes only subvolumes and their parent directories.  Whether
    /// every install has it there is *check* (K1); dump records its
    /// presence, so a source without it is refused at plan time.
    pub const RESTORED_MARKER: &str = "@system/etc/machine-id";
    /// gpt-auto opens the root's LUKS volume as `root`
    /// (`systemd-gpt-auto-generator`), and nothing else names it: no
    /// crypttab.  The restore opens it under the dump's own mapper name,
    /// which is this on a source booted by gpt-auto; another is said in a
    /// note.
    pub const GPT_AUTO_LUKS_NAME: &str = "root";
    /// An active systemd-homed home is recognised by its mount:
    /// homed's LUKS image mapped as `/dev/mapper/home-USER` and mounted at
    /// /home/USER (homed is enabled by preset; whether plasma-setup makes
    /// homed users is a *check*).
    pub const HOMED_MAPPER_PREFIX: &str = "/dev/mapper/home-";
    /// The ESP is mounted inside a subvolume's path (`@system/boot` under
    /// the top level): the mount stage mounts it there once the top level
    /// is up (*check* on K1's rehearsal; `ABRoot`'s `part-future` was the
    /// same shape).
    pub const ESP_INSIDE_TOP: bool = true;
}

/// The layout's `boot.sysupdate.kdelinux`, when the source is KDE Linux.
pub fn of(layout: &Value) -> Option<&Value> {
    layout
        .at(&["boot", "sysupdate", "kdelinux"])
        .filter(|v| v.as_object().is_some())
}

fn s(v: &Value, k: &str) -> Option<String> {
    v.get(k).and_then(Value::as_str).map(str::to_string)
}

fn strs(v: Option<&Value>) -> Vec<String> {
    v.and_then(Value::as_array)
        .into_iter()
        .flatten()
        .filter_map(Value::as_str)
        .map(str::to_string)
        .collect()
}

fn objs(v: Option<&Value>) -> Vec<&Value> {
    v.and_then(Value::as_array).into_iter().flatten().collect()
}

/// A UKI's version, from its file name: `kde-linux_V+L-D.efi`,
/// `kde-linux_V+L.efi` or `kde-linux_V.efi` (the counter as
/// `transactional::bls_counter` splits it).
pub fn uki_version(name: &str) -> Option<String> {
    let base = name.rsplit('/').next().unwrap_or(name);
    if !base.ends_with(".efi") {
        return None;
    }
    let (id, _) = super::transactional::bls_counter(base);
    id.strip_prefix("kde-linux_")
        .filter(|v| !v.is_empty())
        .map(str::to_string)
}

/// What the boot stage's check wants of the restored tree, from the dump.
#[derive(Debug, Default, Clone, PartialEq)]
pub struct Want {
    /// (version, image file, its size): each image file the dump had.
    pub images: Vec<(String, String, Option<u64>)>,
    /// The booted version.
    pub booted: String,
    /// Versions that were half there (a UKI with no image, or the reverse)
    /// and not the booted one: kept as they were, a warning.
    pub halves: Vec<String>,
    /// (`etc_V.mstack`, whether the dump found its `rw/data`).
    pub mstacks: Vec<(String, bool)>,
    /// Each `@system/.etc.<epoch>` the dump had.
    pub etc_snapshots: Vec<String>,
    /// `etc_<booted>.mstack/layer@0`'s target, when the dump read it.
    pub layer0: Option<String>,
}

/// The consistency check of a restored tree at T, as a shell script (spec,
/// "load", boot stage step 3): each `@system/boot/EFI/Linux/kde-linux_*.efi`
/// names a version, whose `kde-linux_V.erofs` is at the top with the
/// dump's size; the booted version's UKI and image are both there; each
/// `etc_V.mstack` is, with its `rw/data` where the dump had one; each
/// `@system/.etc.<epoch>` is a subvolume (its root's inode is 256); the
/// booted version's `layer@0` is the symlink to the image's factory /etc;
/// systemd-boot is on the ESP.  A mismatch fails the stage; a half version
/// the dump had (not the booted one) is a warning.  Sizes and inodes by
/// `ls`, which reads no 8 GB image.
pub fn check_script(t: &str, w: &Want) -> String {
    let q = super::shell_quote;
    let mut imgs = String::new();
    for (v, f, size) in &w.images {
        imgs += &format!(
            "img {} {} {}\n",
            q(v),
            q(f),
            size.map_or_else(|| "-".to_string(), |n| n.to_string())
        );
    }
    let mut ms = String::new();
    for (m, rw) in &w.mstacks {
        ms += &format!("ms {} {}\n", q(m), u8::from(*rw));
    }
    let mut snaps = String::new();
    for e in &w.etc_snapshots {
        snaps += &format!("snap {}\n", q(e));
    }
    let halves = w.halves.join(" ");
    format!(
        r#"t=$(readlink -f {t}) || exit 1; booted={booted}; halves={halves}; layer0={layer0}; errs=; nbad=0; known=
sv=256
err() {{ errs="${{errs}}kdelinux: $*
"; nbad=$((nbad + 1)); }}
# ls, not stat -c or wc: POSIX, and no 8 GB image is read.
size() {{ set -- $(ls -ln "$1" 2>/dev/null); echo "${{5:-}}"; }}
ino() {{ set -- $(ls -di "$1" 2>/dev/null); echo "${{1:-}}"; }}
esp=$t/@system/boot
[ -d "$t/etc.mstack.v" ] || {{ echo "kdelinux: etc.mstack.v isn't at the restored btrfs top level" >&2; exit 1; }}
[ -d "$esp/EFI" ] || {{ echo "kdelinux: the restored ESP (@system/boot) has no EFI directory" >&2; exit 1; }}
# The dump's image files: there, with their sizes.
img() {{
  known="$known $1 "
  f=$t/$2
  if [ ! -f "$f" ]; then err "$2 (version $1) isn't at the restored top level"; return 0; fi
  [ "$3" = - ] || {{ n=$(size "$f"); [ "$n" = "$3" ] || err "$2 is ${{n:-?}} bytes, the dump's $3"; }}
}}
{imgs}# Each UKI's version has its image file.
nuki=0; buki=
for u in "$esp"/EFI/Linux/kde-linux_*.efi; do
  [ -f "$u" ] || continue
  nuki=$((nuki + 1))
  n=${{u##*/}}; n=${{n%.efi}}; n=${{n#kde-linux_}}; v=${{n%%+*}}
  [ "$v" != "$booted" ] || buki=$u
  if [ -f "$t/kde-linux_$v.erofs" ]; then
    case $known in *" $v "*) ;; *) err "${{u##*/}}: version $v's image is here, but the dump hadn't it" ;; esac
  else
    case " $halves " in
      *" $v "*) echo "kdelinux: warning: ${{u##*/}}: version $v has no kde-linux_$v.erofs, as on the source (an interrupted update): it boots nothing" >&2 ;;
      *) err "${{u##*/}}: kde-linux_$v.erofs isn't at the restored top level: version $v boots nothing" ;;
    esac
  fi
done
[ "$nuki" -gt 0 ] || err "no EFI/Linux/kde-linux_*.efi on the restored ESP"
[ -n "$buki" ] || err "the booted version $booted has no UKI on the restored ESP"
[ -f "$t/kde-linux_$booted.erofs" ] || err "the booted version's kde-linux_$booted.erofs isn't at the restored top level"
# The /etc layers.
ms() {{
  d=$t/etc.mstack.v/$1
  if [ ! -d "$d" ]; then err "etc.mstack.v/$1 isn't restored"; return 0; fi
  [ "$2" = 0 ] || [ -d "$d/rw/data" ] || err "etc.mstack.v/$1 has no rw/data (the /etc upper)"
}}
{ms}snap() {{
  d=$t/@system/$1
  [ -d "$d" ] || {{ err "@system/$1 isn't restored"; return 0; }}
  [ "$(ino "$d")" = "$sv" ] || err "@system/$1 isn't a subvolume (its root's inode isn't $sv)"
}}
{snaps}if [ -n "$layer0" ]; then
  l=$(readlink "$t/etc.mstack.v/etc_$booted.mstack/layer@0") || l=
  [ "$l" = "$layer0" ] || err "etc.mstack.v/etc_$booted.mstack/layer@0 is ${{l:-not a symlink}}, not $layer0"
fi
for f in EFI/BOOT/BOOTX64.EFI EFI/systemd/systemd-bootx64.efi; do
  [ -f "$esp/$f" ] || err "$f isn't on the restored ESP"
done
[ -z "$errs" ] || {{ printf %s "$errs" >&2; echo "kdelinux: $nbad mismatches between the UKIs, the image files and the /etc layers in the restored tree" >&2; exit 1; }}
echo "kdelinux: $nuki UKIs, each with its image file; the booted version $booted's UKI, image and /etc layers are there"
"#,
        t = q(t),
        booted = q(&w.booted),
        halves = q(&halves),
        layer0 = q(w.layer0.as_deref().unwrap_or("")),
    )
}

impl Builder<'_> {
    /// The dump's KDE Linux btrfs: the one on the top level's device.
    fn kdelinux_fs(&self, k: &Value) -> Option<Value> {
        let dev = self.kname(&s(k, "top_device")?);
        self.layout
            .arr(&["storage", "btrfs"])
            .iter()
            .find(|fs| strs(fs.get("devices")).iter().any(|d| self.kname(d) == dev))
            .cloned()
    }

    /// A device's kernel name, however the dump spells it: `dm-0` for
    /// `dm-0`, `/dev/dm-0`, `/dev/mapper/root` or `mapper/root` (a
    /// device-mapper device by its name), so the top level's device and the
    /// btrfs's devices compare equal (a mismatch would drop the shared
    /// snapshots silently: full copies, no error).
    pub(crate) fn kname(&self, d: &str) -> String {
        let d = d.strip_prefix("/dev/").unwrap_or(d);
        if let Some(name) = d.strip_prefix("mapper/") {
            if let Some(n) = self
                .layout
                .arr(&["storage", "dm_devices"])
                .iter()
                .find(|x| x.at(&["dm", "dm_name"]).and_then(Value::as_str) == Some(name))
                .and_then(|x| s(x, "name"))
            {
                return n;
            }
        }
        d.to_string()
    }

    /// The mounts `btrfs_plans` plans shared snapshots by, for the btrfs
    /// whose devices are DEVS and subvolumes SUBS: on KDE Linux's, which no
    /// fstab names, the backup's tree, the top level at `/`, with each
    /// subvolume that isn't a snapshot of another listed one (nor a snapper
    /// slot) where the top level holds it.  So `@system/.etc.<epoch>`, each
    /// `etc_V.mstack` made from its origin's, and snapper's snapshots of a
    /// home are made after the restore as snapshots sharing their extents
    /// (Decision 6), from those restored before them.
    pub(crate) fn kdelinux_btrfs_mounts(&self, devs: &[String], subs: &[Sub]) -> Vec<Mount> {
        let Some(k) = of(&self.layout.v) else {
            return Vec::new();
        };
        let Some(top) = s(k, "top_device").map(|d| self.kname(&d)) else {
            return Vec::new();
        };
        if !devs.iter().any(|d| self.kname(d) == top) {
            return Vec::new();
        }
        let uuids: Vec<&str> = subs.iter().filter_map(|x| x.uuid.as_deref()).collect();
        let mut out = vec![Mount {
            target: "/".into(),
            subvol: String::new(),
        }];
        out.extend(
            subs.iter()
                .filter(|x| !x.parent_uuid.as_deref().is_some_and(|p| uuids.contains(&p)))
                .filter(|x| {
                    super::shared::snapper_slot(&x.path).is_none()
                        && super::shared::slot_child(&x.path).is_none()
                })
                .map(|x| Mount {
                    target: format!("/{}", x.path),
                    subvol: x.path.clone(),
                }),
        );
        out
    }

    /// Where the source had mounted what the tree has at AT: the ESP's
    /// `/boot` for `/@system/boot` (`fat_time`'s options are the source
    /// mount's).
    pub(crate) fn kdelinux_source_at(&self, at: &str) -> Option<String> {
        let k = of(&self.layout.v)?;
        if at == ESP_AT {
            return k.at(&["esp", "mounted_at"]).and_then(Value::as_str).map(str::to_string);
        }
        if at == "/" {
            return s(k, "top");
        }
        None
    }

    /// The mount stage's tree on KDE Linux: the btrfs top level at the
    /// target (`subvol=/`, compressed as the source's was), then the ESP
    /// at `@system/boot`.
    pub(crate) fn kdelinux_mounts(&mut self, k: &Value) -> Vec<(String, Cmd)> {
        let t = self.opts.target.clone();
        let mut out = Vec::new();
        let top = s(k, "top").unwrap_or_else(|| "/system".into());
        let src_opts = self
            .layout
            .arr(&["storage", "mounts"])
            .iter()
            .rev()
            .find(|m| s(m, "target").as_deref() == Some(top.as_str()) && s(m, "fstype").as_deref() == Some("btrfs"))
            .map(|m| format!("{},{}", s(m, "options").unwrap_or_default(), s(m, "super_options").unwrap_or_default()))
            .unwrap_or_default();
        let compress: Vec<&str> = src_opts
            .split(',')
            .filter(|o| o.starts_with("compress=") || o.starts_with("compress-force="))
            .take(1)
            .collect();
        let mut opts = vec!["subvol=/"];
        opts.extend(compress.iter().copied());
        match s(k, "top_device").map(|d| self.kname(&d)).and_then(|d| self.names.dev(&d).map(|n| (d, n))) {
            Some((d, node)) => out.push((
                "/".to_string(),
                Cmd::new(&["mount", "-t", "btrfs", "-o", &opts.join(","), &node, &t]).why(format!(
                    "KDE Linux: the btrfs top level ({d}, the source's {top}), / of the backup's tree: the image files, etc.mstack.v and @system"
                )),
            )),
            None => self.problems.push(format!(
                "KDE Linux: the btrfs top level is on {}, which isn't restored",
                s(k, "top_device").unwrap_or_else(|| "a device the dump doesn't name".into())
            )),
        }
        let esp = k.at(&["esp", "device"]).and_then(Value::as_str).map(str::to_string);
        let ty = esp
            .as_deref()
            .and_then(|d| self.layout.content(d))
            .and_then(|c| s(c, "type"))
            .unwrap_or_else(|| "vfat".into());
        match esp.and_then(|d| self.names.dev(&d).map(|n| (d, n))) {
            Some((d, node)) if checks::ESP_INSIDE_TOP => out.push((
                ESP_AT.to_string(),
                Cmd::new(&["mount", "-t", &ty, "-o", "defaults", &node, &format!("{t}{ESP_AT}")])
                    .why(format!("KDE Linux: the ESP ({d}), @system/boot in the backup's tree (gpt-auto mounts it at /boot)")),
            )),
            _ => self.problems.push("KDE Linux: the dump names no ESP that's restored, and the UKIs are on it".into()),
        }
        out
    }

    /// What version 1 doesn't restore on KDE Linux (plan problems), and
    /// what the plan should say (notes).
    fn kdelinux_problems(&mut self, k: &Value) {
        for r in strs(k.get("refusals")) {
            self.problems.push(format!("the dump refused this system: {r}"));
        }
        if k.get("restored_marker_present") == Some(&Value::Bool(false)) {
            self.problems.push(format!(
                "KDE Linux: the source has no {MARKER}, the file the boot stage's gate and `load status` take to mean the restore is done: a layout bko-holt hasn't seen"
            ));
        }
        if s(k, "layout").as_deref() != Some(LAYOUT_V1) {
            self.problems.push(format!(
                "KDE Linux: version 1 restores the RootFSv3 layout with etc-mstack only (a btrfs top level holding @system, etc.mstack.v and the image files); the dump's layout is {}",
                s(k, "layout").unwrap_or_else(|| "not one it recognised".into())
            ));
        }
        let variant = s(k, "variant_id").unwrap_or_default();
        if !crate::sysupdate::checks::KDE_VARIANTS.contains(&variant.as_str()) {
            self.problems.push(format!(
                "KDE Linux: version 1 restores the {} edition only; this is {variant:?} (VARIANT_ID)",
                crate::sysupdate::checks::KDE_VARIANTS.join(", ")
            ));
        }
        for h in objs(k.get("half_versions")) {
            let v = s(h, "version").unwrap_or_default();
            let missing = s(h, "missing").unwrap_or_default();
            if h.get("booted") == Some(&Value::Bool(true)) {
                self.problems.push(format!("KDE Linux: the booted version {v} was half there on the source (no {missing}): the restored system would boot nothing; back up again once it boots whole"));
            } else {
                self.notes.push(format!("KDE Linux: version {v} was half there on the source (no {missing}: an interrupted update), restored as it was; the next `updatectl update` finishes or replaces it"));
            }
        }
        if !self.layout.uefi() {
            self.problems.push("KDE Linux with BIOS boot isn't restored: it boots UEFI only".into());
        }
        if self.opts.new_uuids {
            self.problems.push("KDE Linux with --new-uuids isn't restored in version 1: nothing names a UUID (gpt-auto finds the root and the ESP by partition type), but no test has tried it yet".into());
        }
        if self.opts.fit_largest.is_some() {
            self.problems.push("KDE Linux with --fit-largest isn't restored in version 1: no test has tried a grown btrfs top level yet".into());
        }
        let homed: Vec<String> = self
            .layout
            .arr(&["storage", "mounts"])
            .iter()
            .filter(|m| s(m, "source").is_some_and(|x| x.starts_with(checks::HOMED_MAPPER_PREFIX)))
            .filter_map(|m| s(m, "target"))
            .collect();
        if !homed.is_empty() {
            self.problems.push(format!("KDE Linux: {} {} an active systemd-homed home (its LUKS image mapped and mounted), which version 1 doesn't restore", homed.join(", "), if homed.len() == 1 { "is" } else { "are" }));
        }
        self.kdelinux_fit(k);
        if let Some(fs) = self.kdelinux_fs(k) {
            if !self.btrfs_squota(&fs) && fs.get("quota_enabled").and_then(Value::as_bool) == Some(true) {
                self.notes.push("KDE Linux: the source's btrfs has full qgroups, not simple quotas (kde-linux-btrfs.service turns simple quotas on at every boot): restored as the source had them".into());
            }
        }
        // LUKS without crypttab: gpt-auto names the volume; the restore
        // opens it by the dump's name (luks(), the dm device's own).
        if self.layout.arr(&["storage", "crypttab"]).is_empty() {
            for d in self.layout.arr(&["storage", "dm_devices"]) {
                if d.at(&["dm", "target"]).and_then(Value::as_str) != Some("crypt") {
                    continue;
                }
                let name = d.at(&["dm", "dm_name"]).and_then(Value::as_str).unwrap_or_default();
                let g = checks::GPT_AUTO_LUKS_NAME;
                if name == g {
                    self.notes.push(format!("KDE Linux: the LUKS volume (gpt-auto's {g}, no crypttab) gets a new header with the source's UUID, label and parameters, and is opened as {g} for the restore; its passphrase is asked for anew (no TPM2 or FIDO2 token is lost: the installer enrols none)"));
                } else {
                    self.notes.push(format!("KDE Linux: the LUKS volume was open as {name}, not gpt-auto's {g}: the restore opens it as {name}; the restored system's gpt-auto opens it as {g}"));
                }
            }
        }
        // The loader's variables load doesn't restore (efi.rs records them).
        let li = self.layout.v.at(&["boot", "efi", "loader_interface"]).cloned().unwrap_or(Value::Null);
        if let Some(d) = s(&li, "loader_entry_default").filter(|d| !d.is_empty()) {
            self.notes.push(format!("KDE Linux: the source's systemd-boot default was set to {d} (LoaderEntryDefault, `bootctl set-default`), which isn't restored: the restored machine boots the newest version until it's set again"));
        }
        if s(&li, "loader_config_timeout").is_some() {
            self.notes.push("KDE Linux: LoaderConfigTimeout (the hidden boot menu) isn't restored: the restored machine shows the menu for loader.conf's timeout until its first clean shutdown after a blessed boot hides it again".into());
        }
        if li.get("loader_system_token") == Some(&Value::Bool(true)) {
            self.notes.push("KDE Linux: LoaderSystemToken isn't restored: systemd-boot doesn't credit the restored loader/random-seed until the restored system sets a token of its own (harmless)".into());
        }
    }

    /// The image files against the btrfs's partition: an image that
    /// can't fit is a problem (the generic fit check counts bytes used).
    fn kdelinux_fit(&mut self, k: &Value) {
        let total: u64 = objs(k.get("images"))
            .iter()
            .filter_map(|i| i.get("size").and_then(Value::as_u64))
            .sum();
        let Some(mut dev) = s(k, "top_device").map(|d| self.kname(&d)) else { return };
        for _ in 0..4 {
            if self.layout.disks.iter().any(|d| d.parts.iter().any(|p| p.name == dev)) {
                break;
            }
            match self.layout.slaves(&dev).first() {
                Some(u) => dev.clone_from(u),
                None => return,
            }
        }
        let part = self.layout.disks.iter().find_map(|d| {
            d.parts
                .iter()
                .find(|p| p.name == dev)
                .map(|p| p.sectors * d.sector_size.max(512))
        });
        if let Some(p) = part.filter(|&p| total > p) {
            self.problems.push(format!(
                "KDE Linux: the image files take {} ({total} bytes), more than the btrfs's partition {dev} holds here ({})",
                crate::report::human(total),
                crate::report::human(p)
            ));
        }
    }

    /// The check's expectations, from boot.sysupdate.kdelinux.
    fn kdelinux_want(k: &Value) -> Want {
        let images = objs(k.get("images"))
            .iter()
            .map(|i| {
                (
                    s(i, "version").unwrap_or_default(),
                    s(i, "file").unwrap_or_default(),
                    i.get("size").and_then(Value::as_u64),
                )
            })
            .collect();
        let rw: Vec<String> = objs(k.get("images"))
            .iter()
            .filter(|i| i.get("etc_mstack_rw_data") == Some(&Value::Bool(true)))
            .filter_map(|i| s(i, "version"))
            .map(|v| format!("etc_{v}.mstack"))
            .collect();
        Want {
            images,
            booted: s(k, "booted_version").unwrap_or_default(),
            halves: objs(k.get("half_versions"))
                .iter()
                .filter(|h| h.get("booted") != Some(&Value::Bool(true)))
                .filter_map(|h| s(h, "version"))
                .collect(),
            mstacks: strs(k.get("mstacks"))
                .into_iter()
                .map(|m| {
                    let r = rw.contains(&m);
                    (m, r)
                })
                .collect(),
            etc_snapshots: strs(k.get("etc_snapshots")),
            layer0: s(k, "layer0"),
        }
    }

    /// The boot stage on KDE Linux (docs/design/kde-linux.md, "load").
    pub(crate) fn kdelinux_boot(&mut self, k: &Value) -> Vec<Cmd> {
        self.kdelinux_problems(k);
        let t = self.opts.target.clone();
        let mut out = vec![Cmd::note(format!(
            "refuses to start unless {t}/{MARKER} is there: the restored btrfs top level (a file only the restore writes)"
        ))];
        out.push(Cmd::new(&["sh", "-c", &atimes_cmd(&t, "if-missing", &self.ro_shared())]).why("the restored atimes (the restore stage lists them), to put back after this stage's reads"));
        let what = "KDE Linux: the ESP, the UKIs, loader.conf, the image files and the /etc layers are restored as they were; no initramfs, no bootctl install, no chroot";
        self.notes.push(what.into());
        out.push(Cmd::note(what));
        let want = Self::kdelinux_want(k);
        // Boot counting: names kept as they were.
        let booted_counted: Vec<String> = self
            .layout
            .arr(&["boot", "sysupdate", "ukis"])
            .iter()
            .filter(|u| s(u, "version").as_deref() == Some(want.booted.as_str()))
            .filter(|u| u.get("tries_left").and_then(Value::as_u64).is_some())
            .filter_map(|u| s(u, "name"))
            .collect();
        for n in booted_counted {
            self.notes.push(format!("KDE Linux: the booted version's UKI is still {n}, with a boot counter (backed up before its boot was blessed): kept as it was, so the restored machine's first boot counts it down again"));
        }
        out.push(
            Cmd::new(&["sh", "-c", &check_script(&t, &want)])
                .why("each UKI has its image file with the dump's size, the booted version's UKI, image and /etc layers are there, and systemd-boot is on the ESP, in the restored tree")
                .warns(),
        );
        if self.layout.uefi() && !self.opts.no_nvram {
            out.extend(self.efi_entries());
        }
        let log = format!("{t}/{LOG}");
        out.push(Cmd::new(&["mkdir", "-p", &log]));
        out.push(
            Cmd::new(&["cp", "-a", &format!("{RUN_DIR}/log/."), &format!("{log}/")])
                .why("this restore's log, on the restored system (@system's /var/log)"),
        );
        out.extend(self.boot_tail(&t));
        out
    }
}

#[cfg(test)]
mod tests;
