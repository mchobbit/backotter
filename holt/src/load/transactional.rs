//! `load` for a transactional-update system (openSUSE MicroOS and Aeon:
//! docs/design/aeon.md, "The transactional arm").  The tree is fstab's, as
//! on any btrfs system: the booted slot at `/` (named explicitly, since
//! fstab's `/` names no subvolume and a pending update's default isn't the
//! slot the backup's `/` holds), `@/.snapshots`, `@/var` and the rest; each
//! slot's `/etc` is a subvolume nested in it, made after its slot (shared.rs's
//! children, or format's).  Its boot stage regenerates nothing in a
//! snapshot: no grub2-mkconfig, grub2-install, shim-install, dracut,
//! sdbootutil or relabel of the root, and no chroot; the snapshots stay what
//! transactional-update made, and their SELinux labels whole.  It checks
//! that the restored GRUB configs (or BLS entries) lead to kernels and
//! subvolumes that exist and name only filesystems load made with their
//! UUIDs; rewrites health-checker's LAST_WORKING_BTRFS_ID from
//! LAST_WORKING_SNAPSHOT's path, since a restore can give that subvolume
//! another ID; makes the UEFI entries; writes its log with /var/log's
//! SELinux label; and ends as every Linux boot stage does, the read-only
//! flags last.

use super::commands::{atimes_cmd, Builder, Cmd};
use super::{shell_quote, RUN_DIR};
use crate::json::Value;

/// The layout's boot.transactional, when the source is a transactional-update
/// system (crate::transactional).
pub fn of(layout: &Value) -> Option<&Value> {
    layout.at(&["boot", "transactional"]).filter(|v| v.as_object().is_some())
}

/// The slot the source had booted at `/`: what the backup's `/` holds.
pub fn booted_root(layout: &Value) -> Option<String> {
    of(layout)?.at(&["booted", "path"]).and_then(Value::as_str).map(|p| p.trim_matches('/').to_string()).filter(|p| !p.is_empty())
}

/// The shell function both scripts use: whether a path is a btrfs
/// subvolume's root (`btrfs subvolume show` refuses anything else), and its
/// ID.
const SUBVOL_FNS: &str = r#"subid() { btrfs subvolume show "$1" 2>/dev/null | awk '$1 == "Subvolume" && $2 == "ID:" { print $3; exit }'; }
issub() { [ -n "$(subid "$1")" ]; }
"#;

/// The consistency check of a restored tree at T, its btrfs's top level
/// mounted at TOP, as a shell script.  MODE is the dump's loader_mode.
/// KEPT are the UUIDs load makes its filesystems and LUKS containers with:
/// every `search --fs-uuid`, `root=UUID=` and `rd.luks.uuid=` in what is
/// checked must be one of them.
///
/// GRUB: each ESP's stub grub.cfg (EFI/*/grub.cfg), the booted slot's
/// /boot/grub2/grub.cfg (which must be there), and each
/// /.snapshots/N/grub-snapshot.cfg's entries: the slot directory it names
/// has boot/grub2/grub.cfg (checked the same way) and its subvolume path
/// (`/@/.snapshots/N/snapshot`) is a subvolume.  BLS: each
/// loader/entries/*.conf on the ESP has a linux line, its linux and initrd
/// files are on the ESP, and its rootflags=subvol= is a subvolume.  Every
/// mismatch is said and fails the stage (the errors last, with a count, so
/// the last lines of a failure name what failed): nothing is regenerated
/// to cover it.
pub fn check_script(t: &str, top: &str, mode: &str, kept: &[String]) -> String {
    let kept: Vec<String> = kept.iter().map(|u| u.to_ascii_lowercase()).collect();
    format!(
        r#"t=$(readlink -f {t}) || exit 1; top={top}; kept=' {kept} '; errs=; nbad=0; n=0
err() {{ errs="${{errs}}transactional: $*
"; nbad=$((nbad + 1)); }}
{SUBVOL_FNS}uuids() {{ awk '
  $1 == "search" {{ for (i = 2; i < NF; i++) if ($i == "--fs-uuid" || $i == "-u") {{ print $NF; break }} }}
  {{ for (i = 1; i <= NF; i++) {{ w = $i; if (w ~ /^root=UUID=/) {{ sub(/^root=UUID=/, "", w); print w }} else if (w ~ /^rd\.luks\.uuid=/) {{ sub(/^rd\.luks\.uuid=/, "", w); sub(/^luks-/, "", w); print w }} }} }}' "$1" | sort -u; }}
chk_uuids() {{
  for u in $(uuids "$1"); do
    l=$(printf %s "$u" | tr A-Z a-z)
    case $kept in *" $l "*) ;; *) err "${{1#$t/}} names the filesystem $u, which load doesn't make" ;; esac
  done
}}
if [ {mode} = bls ]; then
  e=$t/boot/efi/loader/entries
  [ -d "$e" ] || err "boot/efi/loader/entries isn't on the restored ESP"
  for f in "$e"/*.conf; do
    [ -f "$f" ] || continue
    n=$((n + 1)); b=${{f##*/}}
    awk '$1 == "linux" {{ k = 1 }} END {{ exit !k }}' "$f" || err "$b: no linux line"
    for p in $(awk '$1 == "linux" || $1 == "initrd" {{ print $2 }}' "$f"); do
      [ -f "$t/boot/efi$p" ] || err "$b: $p isn't on the restored ESP"
    done
    for s in $(awk '$1 == "options" {{ for (i = 2; i <= NF; i++) if ($i ~ /^rootflags=/) {{ r = $i; sub(/^rootflags=/, "", r); m = split(r, o, ","); for (j = 1; j <= m; j++) if (o[j] ~ /^subvol=/) {{ v = o[j]; sub(/^subvol=/, "", v); print v }} }} }}' "$f"); do
      issub "$top/${{s#/}}" || err "$b: rootflags=subvol=$s isn't a subvolume of the restored filesystem"
    done
    chk_uuids "$f"
  done
  [ "$n" -gt 0 ] || err "no BLS entry in boot/efi/loader/entries"
else
  for s in "$t"/boot/efi/EFI/*/grub.cfg; do [ -f "$s" ] && chk_uuids "$s"; done
  g=$t/boot/grub2/grub.cfg
  if [ -f "$g" ]; then chk_uuids "$g"; else err "boot/grub2/grub.cfg isn't in the restored root"; fi
  for c in "$t"/.snapshots/*/grub-snapshot.cfg; do
    [ -f "$c" ] || continue
    awk -F'"' '$1 ~ /^[[:space:]]*menuentry[[:space:]]/ && NF >= 7 {{ print $4; print $6 }}' "$c" > {RUN_DIR}/grub-snapshot.list || exit 1
    while read -r dir && read -r sub; do
      n=$((n + 1))
      if [ -f "$t$dir/boot/grub2/grub.cfg" ]; then chk_uuids "$t$dir/boot/grub2/grub.cfg"; else err "${{c#$t/}}: $dir/boot/grub2/grub.cfg isn't in the restored tree"; fi
      issub "$top$sub" || err "${{c#$t/}}: $sub isn't a subvolume of the restored filesystem"
    done < {RUN_DIR}/grub-snapshot.list
  done
fi
[ -z "$errs" ] || {{ printf %s "$errs" >&2; echo "transactional: $nbad mismatches in the restored boot configuration: nothing is regenerated in a snapshot, so the backup's own files must lead to the restored kernels and subvolumes" >&2; exit 1; }}
echo "transactional: {mode}: $n snapshot entries lead to restored kernels and subvolumes; every filesystem UUID named is one load made"
"#,
        t = shell_quote(t),
        top = shell_quote(top),
        kept = kept.join(" "),
        mode = shell_quote(mode),
    )
}

/// health-checker's state in the restored tree at T (its btrfs's top level
/// at TOP), as a shell script: LAST_WORKING_BTRFS_ID set to the ID that
/// LAST_WORKING_SNAPSHOT's path has here, read back with `btrfs subvolume
/// show`; every other byte of the file kept, and its inode, label and
/// times.  A path that isn't a subvolume here fails the stage: rollback()
/// would set a wrong default.
pub fn hc_script(t: &str, top: &str) -> String {
    let f = shell_quote(&format!("{t}{}", crate::transactional::HC_STATE));
    format!(
        r#"f={f}; top={top}
{SUBVOL_FNS}[ -f "$f" ] || {{ echo "transactional: {hc} isn't in the restored /var" >&2; exit 1; }}
p=$(sed -n 's/^LAST_WORKING_SNAPSHOT=//p' "$f" | tail -n 1); p=${{p#\"}}; p=${{p%\"}}; p=${{p#\'}}; p=${{p%\'}}
[ -n "$p" ] || {{ echo "transactional: {hc} has no LAST_WORKING_SNAPSHOT" >&2; exit 1; }}
old=$(sed -n 's/^LAST_WORKING_BTRFS_ID=//p' "$f" | tail -n 1)
new=$(subid "$top/${{p#/}}")
[ -n "$new" ] || {{ echo "transactional: {hc}'s LAST_WORKING_SNAPSHOT=$p isn't a subvolume of the restored filesystem: health-checker's rollback would set a default that isn't there" >&2; exit 1; }}
if [ "$old" = "$new" ]; then echo "transactional: {hc}: LAST_WORKING_BTRFS_ID=$new is $p's ID here too; kept"; exit 0; fi
touch -r "$f" {RUN_DIR}/hc.ref && sed "s/^LAST_WORKING_BTRFS_ID=.*/LAST_WORKING_BTRFS_ID=$new/" "$f" > {RUN_DIR}/hc.new && cat {RUN_DIR}/hc.new > "$f" && touch -r {RUN_DIR}/hc.ref "$f" || exit 1
echo "transactional: {hc}: LAST_WORKING_BTRFS_ID $old -> $new ($p's ID here)"
"#,
        top = shell_quote(top),
        hc = crate::transactional::HC_STATE,
    )
}

/// `python3 -c MP_LABEL_PY SLOT TARGET MOUNT_POINT...`: the SELinux label
/// of each fstab mount point in the booted slot SLOT (its directory under
/// the btrfs top level, where nothing is mounted on it), when it has none.
/// The backup holds that slot through `/`, where the mounts hide those
/// directories: load made them, unlabelled, and a boot of the slot from
/// GRUB's snapshot menu then hit AVC denials on them (systemd-sysctl's
/// getattr of /var, run aeon1c).  The label is the same directory's in
/// another slot (restored through /.snapshots, with its own), else the
/// mounted file system's root's at TARGET.  Nothing when the slot's root
/// itself has none (no SELinux).
pub const MP_LABEL_PY: &str = r#"import glob, os, re, sys
slot, t, mps = sys.argv[1], sys.argv[2], sys.argv[3:]
K = "security.selinux"
def get(p):
    try:
        return os.getxattr(p, K, follow_symlinks=False)
    except OSError:
        return None
if get(slot) is None:
    print("transactional: the booted slot's root has no SELinux label: its mount points are left as they are")
    sys.exit(0)
others = re.sub(r"/[0-9]+/snapshot$", "/*/snapshot", slot)
n = 0
for m in mps:
    d = slot + m
    if not os.path.isdir(d) or os.path.islink(d) or get(d) is not None:
        continue
    src = next((o for o in sorted(glob.glob(others + m)) if o != d and os.path.isdir(o) and get(o) is not None), None)
    if src is None and get(t + m) is not None:
        src = t + m
    if src is None:
        print(f"transactional: {m} in the booted slot has no label, and no other slot or mount has one for it")
        continue
    os.setxattr(d, K, get(src), follow_symlinks=False)
    n += 1
    print(f"transactional: {m} in the booted slot (a mount point load made) labelled {get(src).decode(errors='replace').rstrip(chr(0))}, as {src}")
print(f"transactional: {n} of the booted slot's mount points labelled")
"#;

fn s(v: &Value, k: &str) -> Option<String> {
    v.get(k).and_then(Value::as_str).map(str::to_string)
}

impl Builder<'_> {
    /// The UUIDs load makes filesystems and LUKS containers with: every
    /// content's, and each btrfs's.
    fn kept_uuids(&self) -> Vec<String> {
        let mut have: Vec<String> = Vec::new();
        for sect in ["disks", "md_arrays", "dm_devices"] {
            for d in self.layout.arr(&["storage", sect]) {
                let mut all = vec![d];
                all.extend(d.get("partitions").and_then(Value::as_array).into_iter().flatten());
                have.extend(all.iter().filter_map(|x| x.at(&["content", "uuid"]).and_then(Value::as_str)).map(str::to_ascii_lowercase));
            }
        }
        have.extend(self.layout.arr(&["storage", "btrfs"]).iter().filter_map(|f| s(f, "uuid")).map(|u| u.to_ascii_lowercase()));
        have.sort();
        have.dedup();
        have
    }

    /// The ID the subvolume PATH (from the top level) of the btrfs UUID
    /// gets here: a shared snapshot's (or its child's) planned one, else
    /// the source's, which format keeps.  None when it isn't in the layout.
    fn planned_id(&self, uuid: &str, path: &str) -> Option<u64> {
        let path = path.trim_matches('/');
        if let Some(n) = self.btrfs.get(uuid).and_then(|p| p.new_id_of_path(path)) {
            return Some(n);
        }
        let fs = self.layout.arr(&["storage", "btrfs"]).iter().find(|f| s(f, "uuid").as_deref() == Some(uuid))?.clone();
        fs.get("subvolumes")
            .and_then(Value::as_array)?
            .iter()
            .find(|v| s(v, "path").is_some_and(|p| p.trim_start_matches("<FS_TREE>/").trim_matches('/') == path))
            .and_then(|v| v.get("id").and_then(Value::as_u64))
    }

    /// What load doesn't restore on a transactional system (problems), and
    /// what the plan should say (notes).
    fn transactional_problems(&mut self, x: &Value) {
        let uuid = s(x, "filesystem").unwrap_or_default();
        let slot = |k: &str| x.at(&[k, "path"]).and_then(Value::as_str).map(str::to_string);
        let num = |k: &str| x.at(&[k, "snapshot"]).and_then(Value::as_u64).map_or_else(|| "?".to_string(), |n| n.to_string());
        match s(x, "etc").as_deref() {
            Some("subvolume") => {}
            Some("overlay") => self.problems.push("transactional-update: /etc is an overlay (a system from before transactional-update 5.0, with /var/lib/overlay): bko-holt load restores only systems whose /etc is a subvolume in each snapshot (docs/design/aeon.md, decision 6)".into()),
            other => self.problems.push(format!("transactional-update: the booted slot has no /etc subvolume nested in it (/etc: {}), which bko-holt load doesn't know how to restore", other.unwrap_or("unknown"))),
        }
        if !self.opts.reformats.is_empty() {
            self.problems.push("transactional-update: --reformat would rewrite /etc/fstab and the kernel command lines inside read-only snapshots, and drop the snapshots that are the system; not supported".into());
        }
        if self.layout.arr(&["storage", "btrfs"]).iter().all(|f| s(f, "uuid").as_deref() != Some(uuid.as_str())) {
            self.problems.push(format!("transactional-update: the root filesystem {uuid} isn't one of the layout's btrfs"));
        }
        let mode = s(x, "loader_mode").unwrap_or_default();
        if !matches!(mode.as_str(), "grub" | "bls") {
            self.problems.push(format!("transactional-update: loader mode {mode:?}, not grub or bls"));
        }
        // The dump's own GRUB config: its searches name filesystems load
        // keeps (the boot stage checks the restored files again).
        let kept = self.kept_uuids();
        if mode == "grub" {
            for u in self.layout.arr(&["boot", "grub", "grub_cfg", "search_uuids"]).iter().filter_map(Value::as_str) {
                if !kept.contains(&u.to_ascii_lowercase()) {
                    self.problems.push(format!("transactional-update: grub.cfg searches for the filesystem {u}, which no filesystem in the layout has; nothing is regenerated, so the restored GRUB couldn't find it"));
                }
            }
        }
        let what = "transactional-update: nothing is regenerated in a snapshot (no grub2-mkconfig, grub2-install, shim-install, dracut, sdbootutil, relabel of the root, or chroot): the ESP, every slot's /boot and GRUB's or the BLS entries' files are the restored ones, checked instead";
        self.notes.push(what.into());
        // A pending update: the default isn't the booted slot.
        if let (Some(b), Some(d)) = (slot("booted"), slot("default")) {
            if b != d {
                self.notes.push(format!(
                    "transactional-update: an update was pending on the source: its default is snapshot {} ({d}), not the booted {} ({b}); the backup's / is {b}, and the restored machine boots {d}, as the source's next boot would have, and health-checker judges it",
                    num("default"),
                    num("booted")
                ));
            }
        }
        if let Some(hc) = x.get("health_check_state").filter(|v| !v.is_null()) {
            let path = s(hc, "last_working_snapshot");
            let old = hc.get("last_working_btrfs_id").and_then(Value::as_u64).map_or_else(|| s(hc, "last_working_btrfs_id_raw").unwrap_or_else(|| "none".into()), |n| n.to_string());
            match path.as_deref().map(|p| (p, self.planned_id(&uuid, p))) {
                Some((p, Some(new))) if new.to_string() == old => self.notes.push(format!(
                    "health-checker: {}'s LAST_WORKING_BTRFS_ID={old} names {p} (LAST_WORKING_SNAPSHOT); that subvolume is ID {new} here too, as planned: the boot stage reads it back with btrfs subvolume show and keeps the file as it is unless the ID differs",
                    crate::transactional::HC_STATE
                )),
                Some((p, Some(new))) => self.notes.push(format!(
                    "health-checker: {}'s LAST_WORKING_BTRFS_ID={old} names {p} (LAST_WORKING_SNAPSHOT), which is ID {new} here, as planned: the boot stage rewrites the ID from the path, read back with btrfs subvolume show ({old} -> {new}), and keeps the rest of the file byte for byte",
                    crate::transactional::HC_STATE
                )),
                Some((p, None)) => self.problems.push(format!(
                    "health-checker: {}'s LAST_WORKING_SNAPSHOT={p} isn't a subvolume in the layout: its LAST_WORKING_BTRFS_ID={old} can't be made to name it, and its rollback would set a wrong default",
                    crate::transactional::HC_STATE
                )),
                None => self.problems.push(format!("health-checker: {} has no LAST_WORKING_SNAPSHOT to rewrite its LAST_WORKING_BTRFS_ID={old} from", crate::transactional::HC_STATE)),
            }
        } else if mode == "grub" {
            self.notes.push(format!("health-checker: the source has no {}, so there is no subvolume ID to rewrite", crate::transactional::HC_STATE));
        }
        let mut written = vec!["/var/log/bko-holt (this restore's log)".to_string()];
        if x.get("health_check_state").is_some_and(|v| !v.is_null()) {
            written.insert(0, format!("{} (its ID, if it changed)", crate::transactional::HC_STATE));
        }
        self.notes.push(format!(
            "transactional-update: what the boot stage writes into the restored root: {}, both in @/var; nothing in a snapshot or its /etc, whose fstab is the restored one (the target's layout is the source's)",
            written.join(" and ")
        ));
    }

    /// The boot stage on a transactional-update system (docs/design/aeon.md,
    /// "The transactional arm").
    pub(crate) fn transactional_boot(&mut self, x: &Value) -> Vec<Cmd> {
        self.transactional_problems(x);
        let t = self.opts.target.clone();
        let uuid = s(x, "filesystem").unwrap_or_default();
        let mode = s(x, "loader_mode").unwrap_or_else(|| "grub".into());
        let mut out = vec![Cmd::note(format!("refuses to start unless {t}/etc/fstab is the restored system's (its / matches the layout's)"))];
        out.extend(self.restored_key_files(&t));
        out.push(Cmd::new(&["sh", "-c", &atimes_cmd(&t, "if-missing", &self.ro_shared())]).why("the restored atimes (the restore stage lists them), to put back after this stage's reads"));
        out.push(Cmd::note("transactional-update: no initramfs, grub2-mkconfig, grub2-install, shim-install, sdbootutil or relabel of the root, and no chroot"));
        let fs = self.layout.arr(&["storage", "btrfs"]).iter().find(|f| s(f, "uuid").as_deref() == Some(uuid.as_str())).cloned();
        let dev = fs.as_ref().and_then(|f| f.at(&["devices", "0"]).and_then(Value::as_str).map(str::to_string)).and_then(|d| self.names.dev(&d)).unwrap_or_default();
        let top = format!("{RUN_DIR}/btrfs/{uuid}");
        out.push(Cmd::new(&["mkdir", "-p", &top]));
        out.push(Cmd::new(&["mount", "-t", "btrfs", "-o", "subvolid=5,noatime", &dev, &top]).why(format!("btrfs {uuid}: its subvolumes, by path, for the checks and health-checker's ID")));
        let kept = self.kept_uuids();
        let why = if mode == "bls" {
            "each BLS entry's kernel, initrd and rootflags=subvol= are in the restored tree, and every filesystem UUID it names is one load made"
        } else {
            "the ESP's stub, the booted slot's grub.cfg and each grub-snapshot.cfg lead to restored kernels and subvolumes, and every filesystem UUID they name is one load made"
        };
        out.push(Cmd::new(&["sh", "-c", &check_script(&t, &top, &mode, &kept)]).why(why).warns());
        if x.get("health_check_state").is_some_and(|v| !v.is_null()) {
            out.push(Cmd::new(&["sh", "-c", &hc_script(&t, &top)]).why("health-checker's LAST_WORKING_BTRFS_ID, from LAST_WORKING_SNAPSHOT's path: the ID its rollback sets as the default"));
        }
        // The booted slot's fstab mount points, under its mounts (MP_LABEL_PY).
        if let Some(b) = booted_root(&self.layout.v) {
            let mut mps: Vec<String> = self
                .layout
                .arr(&["storage", "fstab"])
                .iter()
                .filter(|e| s(e, "type").is_some_and(|t| t != "swap" && t != "none") && !s(e, "options").unwrap_or_default().split(',').any(|o| o == "bind"))
                .filter_map(|e| s(e, "file"))
                .filter(|f| f.starts_with('/') && f != "/")
                .collect();
            mps.sort();
            mps.dedup();
            if !mps.is_empty() {
                let mut args = vec!["python3".to_string(), "-c".into(), MP_LABEL_PY.into(), format!("{top}/{b}"), t.clone()];
                args.extend(mps);
                let refs: Vec<&str> = args.iter().map(String::as_str).collect();
                out.push(Cmd::new(&refs).why("the SELinux label of each mount point in the booted slot, which the backup's / hides under its mounts and load made bare: another slot's, else the mounted file system's root's"));
            }
        }
        out.push(Cmd::new(&["umount", &top]));
        // A disk that boots by BIOS too (MicroOS's kvm-and-xen image: GRUB's
        // core in the BIOS boot partition, boot.img in the MBR), which the
        // archive doesn't hold.  The source's own images are in the restored
        // /boot/grub2/i386-pc (the writable @/boot/grub2/i386-pc, not a
        // snapshot), as grub2-install left them, and grub2-bios-setup writes
        // them to the new disk, patching their sector addresses, as for Guix
        // System: the source's own grub2-bios-setup (its GRUB's version) run
        // from the restored tree by its own dynamic loader and libraries,
        // without a chroot, else the rescue system's.  It reads the images
        // and writes the disk's boot areas only; nothing in the target.
        let dir = shell_quote(&format!("{t}{}/i386-pc", self.grub_dir()));
        let tq = shell_quote(&t);
        for d in self.grub_core_partition_devices() {
            out.push(
                Cmd::new(&[
                    "sh",
                    "-c",
                    &format!(
                        "d={dir}; t={tq}; [ -f \"$d/boot.img\" ] && [ -f \"$d/core.img\" ] || {{ echo \"transactional: $d has no boot.img or core.img (the source's, restored with @/boot/grub2/i386-pc) to write to {d}\" >&2; exit 1; }}
l=; for x in \"$t/usr/lib64/ld-linux-x86-64.so.2\" \"$t/lib64/ld-linux-x86-64.so.2\"; do [ -x \"$x\" ] && l=$x && break; done
if [ -n \"$l\" ] && [ -x \"$t/usr/sbin/grub2-bios-setup\" ]; then echo \"the source's grub2-bios-setup ($($l --library-path \"$t/usr/lib64:$t/lib64\" \"$t/usr/sbin/grub2-bios-setup\" --version 2>&1))\"; \"$l\" --library-path \"$t/usr/lib64:$t/lib64\" \"$t/usr/sbin/grub2-bios-setup\" --directory=\"$d\" {d}
elif command -v grub-bios-setup >/dev/null; then echo \"the rescue system's grub-bios-setup\"; grub-bios-setup --directory=\"$d\" {d}
else echo \"transactional: neither the source nor the rescue system has a grub-bios-setup, to write the source's BIOS GRUB to {d}\" >&2; exit 1; fi"
                    ),
                ])
                .why(format!("the source's own BIOS GRUB (boot.img and core.img in {}/i386-pc) to the MBR and BIOS boot partition of {d}, by grub2-bios-setup", self.grub_dir())),
            );
        }
        if self.layout.uefi() && !self.opts.no_nvram {
            out.extend(self.efi_entries());
        }
        let log = format!("{t}/var/log/bko-holt");
        out.push(Cmd::new(&["mkdir", "-p", &log]));
        out.push(Cmd::new(&["cp", "-a", &format!("{RUN_DIR}/log/."), &format!("{log}/")]).why("this restore's log, on the restored system (in @/var)"));
        out.push(
            Cmd::new(&["python3", "-c", super::ostree::LABEL_PY, "security.selinux", &format!("{t}/var/log"), &log])
                .why("the log's SELinux label: /var/log's (every other file kept its own from the backup; nothing in a snapshot is relabelled)"),
        );
        out.extend(self.boot_tail(&t));
        out
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::load::commands::build;
    use crate::load::layout::Layout;
    use crate::load::target::Mapping;
    use crate::load::Stage;

    /// The microos guest's dump (bko-holt dump of the fresh guest under
    /// `vm-run --snapshot` on chopin, 2026-11-14, redacted): slots 1 and 2,
    /// 2 booted and the default, each with its /etc nested in it, GRUB with
    /// grub2-snapper-plugin's menu, health-checker's state naming 2.
    pub(crate) fn microos() -> Value {
        crate::json::parse(include_str!("../../tests/fixtures/microos-fresh.json")).unwrap()
    }

    const UUID: &str = "bc000000-0000-4000-8000-000000000005";

    fn top() -> String {
        format!("{RUN_DIR}/btrfs/{UUID}")
    }

    fn plan(v: &Value, extra: &[&str]) -> crate::load::commands::Built {
        let l = Layout::from_json(v.clone()).unwrap();
        let mut a = vec!["l.json".to_string()];
        a.extend(extra.iter().map(|s| s.to_string()));
        let o = match crate::load::parse_args(&a).unwrap() {
            crate::load::Parsed::Run(o) => o,
            _ => unreachable!(),
        };
        let m = Mapping { pairs: vec![("vda".into(), "vdb".into())], ..Mapping::default() };
        build(&l, &o, &m)
    }

    fn text(cmds: &[Cmd]) -> String {
        cmds.iter().map(Cmd::shell).collect()
    }

    fn set_at(v: &mut Value, path: &[&str], x: Value) {
        let (k, rest) = path.split_first().unwrap();
        let child = match v {
            Value::Obj(kv) => match kv.iter().position(|(key, _)| key == k) {
                Some(i) => &mut kv[i].1,
                None => {
                    kv.push((k.to_string(), Value::Null));
                    &mut kv.last_mut().unwrap().1
                }
            },
            Value::Arr(a) => &mut a[k.parse::<usize>().unwrap()],
            _ => panic!("{k}: not an object or array"),
        };
        if rest.is_empty() {
            *child = x;
        } else {
            set_at(child, rest, x);
        }
    }

    fn j(s: &str) -> Value {
        crate::json::parse(s).unwrap()
    }

    fn pos(hay: &str, s: &str) -> usize {
        hay.find(s).unwrap_or_else(|| panic!("{s}: {hay}"))
    }

    /// The source's subvolume list, its default and boot.transactional's
    /// slots, with SUBS added (a pending update's slot 3).
    fn with_subvolumes(v: &mut Value, subs: &[&str]) {
        let mut list = v.at(&["storage", "btrfs", "0", "subvolumes"]).and_then(Value::as_array).cloned().unwrap();
        list.extend(subs.iter().map(|s| j(s)));
        set_at(v, &["storage", "btrfs", "0", "subvolumes"], Value::Arr(list));
    }

    fn uuid_of(v: &Value, path: &str) -> String {
        v.at(&["storage", "btrfs", "0", "subvolumes"]).and_then(Value::as_array).unwrap().iter().find(|s| s.get("path").and_then(Value::as_str).map(|p| p.trim_start_matches("<FS_TREE>/")) == Some(path)).and_then(|s| s.get("uuid")).and_then(Value::as_str).unwrap().to_string()
    }

    #[test]
    fn microos_plan() {
        let v = microos();
        assert!(of(&v).is_some());
        assert_eq!(booted_root(&v).as_deref(), Some("@/.snapshots/2/snapshot"));
        let b = plan(&v, &["--repo", "/srv/repo"]);
        assert!(b.problems.is_empty(), "{:?}", b.problems);
        let has = |n: &str| b.notes.iter().any(|x| x.contains(n));
        assert!(has("transactional-update: nothing is regenerated in a snapshot"), "{:?}", b.notes);
        assert!(has("health-checker: /var/lib/misc/health-check.state's LAST_WORKING_BTRFS_ID=269 names @/.snapshots/2/snapshot (LAST_WORKING_SNAPSHOT); that subvolume is ID 269 here too"), "{:?}", b.notes);
        assert!(has("what the boot stage writes into the restored root: /var/lib/misc/health-check.state (its ID, if it changed) and /var/log/bko-holt"), "{:?}", b.notes);
        assert!(!has("an update was pending"), "{:?}", b.notes);
        // / is the booted slot, named; fstab's ro=vfs left out.
        let m = text(b.stage(Stage::Mount));
        assert!(m.contains("mount -t btrfs -o subvol=@/.snapshots/2/snapshot /dev/vdb3 /mnt/bko-target\n"), "{m}");
        assert!(!m.contains("ro=vfs") && !m.contains(" /mnt/bko-target/etc\n"), "{m}");
        // Boot: nothing regenerated, nothing chrooted, nothing relabelled
        // but the log.
        let bt = text(b.stage(Stage::Boot));
        let run: String = bt.lines().filter(|l| !l.starts_with('#')).collect::<Vec<_>>().join("\n");
        for no in ["chroot", "dracut", "mkinitrd", "grub2-mkconfig", "grub2-install", "grub-install", "shim-install", "sdbootutil", "bootctl", "setfiles", "restorecon", "--rbind", "os-prober", "update-grub"] {
            assert!(!run.contains(no), "{no}: {bt}");
        }
        let t = top();
        let listed = pos(&bt, "atimes.before");
        let mount = pos(&bt, &format!("mount -t btrfs -o subvolid=5,noatime /dev/vdb3 {t}\n"));
        let check = pos(&bt, "/.snapshots/*/grub-snapshot.cfg");
        assert!(bt.contains(&format!("kept='\\'' bc00-0001 {UUID} '\\''")), "{bt}");
        let hc = pos(&bt, "f=/mnt/bko-target/var/lib/misc/health-check.state");
        let label_mps = pos(&bt, &format!("{t}/@/.snapshots/2/snapshot /mnt/bko-target /.snapshots /boot/efi /boot/grub2/i386-pc /boot/grub2/x86_64-efi /boot/writable /home /opt /root /srv /usr/local /var\n"));
        let umount = pos(&bt, &format!("umount {t}\n"));
        assert!(hc < label_mps && label_mps < umount, "{bt}");
        let log = pos(&bt, "cp -a /run/bko-holt/log/. /mnt/bko-target/var/log/bko-holt/\n");
        let label = pos(&bt, "security.selinux /mnt/bko-target/var/log /mnt/bko-target/var/log/bko-holt\n");
        let back = pos(&bt, "atimes.after");
        let ro = pos(&bt, &format!("btrfs property set -ts {t}/@/.snapshots/2/snapshot ro true\n"));
        assert!(listed < mount && mount < check && check < hc && hc < umount && umount < log && log < label && label < back && back < ro, "{bt}");
        // The image boots by BIOS too: its own boot.img and core.img,
        // restored in the writable @/boot/grub2/i386-pc, written to the MBR
        // and BIOS boot partition by its own grub2-bios-setup (run by its
        // own loader, no chroot) or else the rescue's, after the checks.
        let bios = pos(&bt, "d=/mnt/bko-target/boot/grub2/i386-pc; t=/mnt/bko-target; [ -f \"$d/boot.img\" ] && [ -f \"$d/core.img\" ]");
        assert!(bt[bios..].contains("\"$t/usr/sbin/grub2-bios-setup\" --directory=\"$d\" /dev/vdb\n"), "{bt}");
        assert!(bt[bios..].contains("grub-bios-setup --directory=\"$d\" /dev/vdb\nelse"), "{bt}");
        assert!(umount < bios && bios < log, "{bt}");
        assert_eq!(bt.matches("t=/mnt/bko-target; [ -f").count(), 1, "{bt}");
        // A disk without GRUB's core in its BIOS boot partition: none, a note.
        let mut v3 = v.clone();
        set_at(&mut v3, &["storage", "disks", "0", "boot_code"], j(r#"{"kind": "none"}"#));
        let b3 = plan(&v3, &["--repo", "/srv/repo"]);
        assert!(!text(b3.stage(Stage::Boot)).contains("grub-bios-setup"));
        assert!(b3.notes.iter().any(|n| n.starts_with("vda has a BIOS boot partition, but the dump records no GRUB core in it")), "{:?}", b3.notes);
        // Its only NVRAM entry has no file (the firmware's own for the
        // disk): the restored disk boots EFI/BOOT's shim, as the source.
        assert!(!bt.contains("efibootmgr"), "{bt}");
        // Slot 1, shared, is made read-only in the restore stage; 2 last.
        assert!(!bt.contains("@/.snapshots/1/snapshot ro true"), "{bt}");
        // No health-check.state: no rewrite, a note.
        let mut v2 = v.clone();
        set_at(&mut v2, &["boot", "transactional", "health_check_state"], Value::Null);
        let b2 = plan(&v2, &["--repo", "/srv/repo"]);
        assert!(!text(b2.stage(Stage::Boot)).contains("health-check.state"));
        assert!(b2.notes.iter().any(|n| n.starts_with("health-checker: the source has no /var/lib/misc/health-check.state")), "{:?}", b2.notes);
    }

    /// The image's protective MBR has a disk signature (KIWI's), which
    /// sfdisk leaves zero: written after it, as the source's.  A zero one
    /// (sfdisk's, parted's) writes nothing.
    #[test]
    fn microos_pmbr_signature() {
        let p = text(plan(&microos(), &["--repo", "/srv/repo"]).stage(Stage::Partition));
        let sfdisk = pos(&p, "sfdisk --wipe always");
        let sig = pos(&p, "\\330\\234\\053\\222");
        assert!(p[sig..].starts_with("\\330\\234\\053\\222'\\'' | dd of=/dev/vdb bs=1 seek=440 count=4 conv=notrunc status=none"), "{p}");
        assert!(sfdisk < sig, "{p}");
        assert!(p.contains("# the protective MBR's disk signature, 922b9cd8, as the source's"), "{p}");
        let mut v = microos();
        set_at(&mut v, &["storage", "disks", "0", "partition_table", "protective_mbr", "entries", "0", "uuid"], Value::from("00000000-01"));
        assert!(!text(plan(&v, &["--repo", "/srv/repo"]).stage(Stage::Partition)).contains("seek=440"));
    }

    /// Slot 1 and its /etc are made after the main restore: 1 a snapshot
    /// of the live root, its /etc (an empty directory there) replaced by a
    /// snapshot of the live /etc, then both filled by one exact restore,
    /// and read-only set last.  Format makes 2, then 2's /etc in it.
    #[test]
    fn microos_nested_etc_order() {
        let b = plan(&microos(), &["--repo", "/srv/repo"]);
        let t = top();
        let f = text(b.stage(Stage::Format));
        assert!(!f.contains("@/.snapshots/1/snapshot"), "{f}");
        let two = pos(&f, &format!("btrfs subvolume create {t}/@/.snapshots/2/snapshot\n"));
        let etc = pos(&f, &format!("btrfs subvolume create {t}/@/.snapshots/2/snapshot/etc\n"));
        assert!(two < etc && !f.contains("rmdir") && !f.contains("ro true"), "{f}");
        assert!(b.notes.iter().any(|n| n.ends_with("@/.snapshots/2/snapshot/etc was a snapshot of @/.snapshots/1/snapshot/etc, which is made after the restore; it's made as a plain subvolume")), "{:?}", b.notes);
        assert!(b.notes.iter().any(|n| n.ends_with("@/.snapshots/1/snapshot/etc: ID 268 on the source, 274 here (a subvolume made after it on the source is made first here)")), "{:?}", b.notes);
        let r = text(b.stage(Stage::Restore));
        assert!(r.contains("!/.snapshots/2/snapshot/**\n!/.snapshots/1/snapshot/**\n"), "{r}");
        let main = pos(&r, "--glob-file /run/bko-holt/restore.globs latest /mnt/bko-target\n");
        let slot = pos(&r, &format!("btrfs subvolume snapshot {t}/@/.snapshots/2/snapshot \"$d\" && "));
        let gone = pos(&r, "rm -rf -- \"$d\"/etc && mkdir -p \"$(dirname \"$d\"/etc)\" && ");
        let child = pos(&r, &format!("btrfs subvolume snapshot {t}/@/.snapshots/2/snapshot/etc \"$d\"/etc && "));
        let exact = pos(&r, &format!("restore --numeric-id --exact --clone-from /={t}/@/.snapshots/2/snapshot latest:/.snapshots/1/snapshot {t}/@/.snapshots/1/snapshot"));
        let ro = pos(&r, &format!("btrfs property set -ts {t}/@/.snapshots/1/snapshot ro true\n"));
        assert!(main < slot && slot < gone && gone < child && child < exact && exact < ro, "{r}");
        // Not in the backup: both made empty, the child in its slot.
        assert!(r.contains("else btrfs subvolume create \"$d\" && mkdir -p \"$(dirname \"$d\"/etc)\" && btrfs subvolume create \"$d\"/etc && echo"), "{r}");
        assert!(r.contains("cid=$(btrfs inspect-internal rootid \"$d\"/etc) && [ \"$cid\" = 274 ] || echo \"@/.snapshots/1/snapshot/etc: ID $cid here, 274 in the plan\""), "{r}");
        assert!(r.contains("; etc nested in it before the restore (a snapshot of @/.snapshots/2/snapshot/etc, ID 274; 268 on the source)"), "{r}");
        // /etc stays writable: no ro for either /etc.
        assert!(!r.contains("/etc ro true") && !text(b.stage(Stage::Boot)).contains("/etc ro true"));
    }

    /// --btrfs-snapshots copy: format makes every slot, 2 as a snapshot of
    /// 1; the empty directory that snapshot has where 1's /etc is goes
    /// before 2's /etc (a snapshot of 1's) is made there.
    #[test]
    fn microos_copy_rmdirs_the_empty_etc() {
        let b = plan(&microos(), &["--repo", "/srv/repo", "--btrfs-snapshots", "copy"]);
        let t = top();
        let f = text(b.stage(Stage::Format));
        let one = pos(&f, &format!("btrfs subvolume create {t}/@/.snapshots/1/snapshot\n"));
        let one_etc = pos(&f, &format!("btrfs subvolume create {t}/@/.snapshots/1/snapshot/etc\n"));
        let two = pos(&f, &format!("btrfs subvolume snapshot {t}/@/.snapshots/1/snapshot {t}/@/.snapshots/2/snapshot\n"));
        let rm = pos(&f, &format!("rmdir {t}/@/.snapshots/2/snapshot/etc\n"));
        let two_etc = pos(&f, &format!("btrfs subvolume snapshot {t}/@/.snapshots/1/snapshot/etc {t}/@/.snapshots/2/snapshot/etc\n"));
        assert!(one < one_etc && one_etc < two && two < rm && rm < two_etc, "{f}");
        assert_eq!(f.matches("rmdir").count(), 1, "{f}");
        assert!(f.contains("# @/.snapshots/2/snapshot, a snapshot of @/.snapshots/1/snapshot, has an empty directory where @/.snapshots/1/snapshot/etc is nested: @/.snapshots/2/snapshot/etc is made in its place\n"), "{f}");
        // Read-only on both slots, last, in boot.
        let bt = text(b.stage(Stage::Boot));
        let r1 = pos(&bt, &format!("{t}/@/.snapshots/1/snapshot ro true"));
        assert!(pos(&bt, "atimes.after") < r1, "{bt}");
    }

    /// A pending update: slot 3 the default, 2 booted.  / is still 2 (what
    /// the backup's / holds); 3 is made after the restore, from 1 (its
    /// family's earlier member), and made the default then; the plan says
    /// the restored machine boots 3.
    #[test]
    fn microos_pending_update() {
        let mut v = microos();
        let (u2, u2e) = (uuid_of(&v, "@/.snapshots/2/snapshot"), uuid_of(&v, "@/.snapshots/2/snapshot/etc"));
        with_subvolumes(
            &mut v,
            &[
                &format!(r#"{{"id": 273, "gen": 95, "parent": 257, "top_level": 257, "parent_uuid": "{u2}", "uuid": "bc000000-0000-4000-8000-0000000000f3", "path": "<FS_TREE>/@/.snapshots/3/snapshot", "readonly": true, "properties": {{"ro": "true"}}}}"#),
                &format!(r#"{{"id": 274, "gen": 96, "parent": 273, "top_level": 273, "parent_uuid": "{u2e}", "uuid": "bc000000-0000-4000-8000-0000000000f4", "path": "<FS_TREE>/@/.snapshots/3/snapshot/etc", "readonly": false, "properties": {{"ro": "false"}}}}"#),
            ],
        );
        set_at(&mut v, &["storage", "btrfs", "0", "default_subvolume"], j(r#"{"id": 273, "gen": 95, "top_level": 257, "path": "@/.snapshots/3/snapshot"}"#));
        set_at(&mut v, &["boot", "transactional", "default"], j(r#"{"path": "@/.snapshots/3/snapshot", "id": 273, "snapshot": 3}"#));
        set_at(&mut v, &["boot", "transactional", "pending_update"], Value::Bool(true));
        let b = plan(&v, &["--repo", "/srv/repo"]);
        assert!(b.problems.is_empty(), "{:?}", b.problems);
        assert!(b.notes.iter().any(|n| n.starts_with("transactional-update: an update was pending on the source: its default is snapshot 3 (@/.snapshots/3/snapshot), not the booted 2 (@/.snapshots/2/snapshot); the backup's / is @/.snapshots/2/snapshot, and the restored machine boots @/.snapshots/3/snapshot")), "{:?}", b.notes);
        let t = top();
        let m = text(b.stage(Stage::Mount));
        assert!(m.contains("mount -t btrfs -o subvol=@/.snapshots/2/snapshot /dev/vdb3 /mnt/bko-target\n"), "{m}");
        let f = text(b.stage(Stage::Format));
        assert!(!f.contains("set-default") && !f.contains("@/.snapshots/3/snapshot"), "{f}");
        let r = text(b.stage(Stage::Restore));
        assert!(r.contains("!/.snapshots/2/snapshot/**\n!/.snapshots/1/snapshot/**\n!/.snapshots/3/snapshot/**\n"), "{r}");
        let three = pos(&r, &format!("btrfs subvolume snapshot {t}/@/.snapshots/1/snapshot \"$d\" && rm -rf -- \"$d\"/etc && mkdir -p \"$(dirname \"$d\"/etc)\" && btrfs subvolume snapshot {t}/@/.snapshots/1/snapshot/etc \"$d\"/etc"));
        let ro3 = pos(&r, &format!("btrfs property set -ts {t}/@/.snapshots/3/snapshot ro true\n"));
        let default = pos(&r, &format!("btrfs subvolume set-default {t}/@/.snapshots/3/snapshot\n"));
        assert!(three < ro3 && ro3 < default, "{r}");
    }

    /// LAST_WORKING_SNAPSHOT names a slot that gets a new ID (1, shared):
    /// the note gives both IDs; one that isn't in the layout is a problem.
    #[test]
    fn health_check_ids() {
        let mut v = microos();
        set_at(&mut v, &["boot", "transactional", "health_check_state"], j(r#"{"last_working_btrfs_id": 258, "last_working_btrfs_id_raw": "258", "last_working_snapshot": "@/.snapshots/1/snapshot"}"#));
        let b = plan(&v, &["--repo", "/srv/repo"]);
        assert!(b.notes.iter().any(|n| n.starts_with("health-checker: /var/lib/misc/health-check.state's LAST_WORKING_BTRFS_ID=258 names @/.snapshots/1/snapshot (LAST_WORKING_SNAPSHOT), which is ID 273 here, as planned: the boot stage rewrites the ID from the path, read back with btrfs subvolume show (258 -> 273)")), "{:?}", b.notes);
        set_at(&mut v, &["boot", "transactional", "health_check_state", "last_working_snapshot"], Value::from("@/.snapshots/9/snapshot"));
        let b = plan(&v, &["--repo", "/srv/repo"]);
        assert!(b.problems.iter().any(|p| p.starts_with("health-checker: /var/lib/misc/health-check.state's LAST_WORKING_SNAPSHOT=@/.snapshots/9/snapshot isn't a subvolume in the layout")), "{:?}", b.problems);
    }

    #[test]
    fn problems() {
        let mut v = microos();
        set_at(&mut v, &["boot", "transactional", "etc"], Value::from("overlay"));
        let b = plan(&v, &["--repo", "/srv/repo"]);
        assert!(b.problems.iter().any(|p| p.starts_with("transactional-update: /etc is an overlay")), "{:?}", b.problems);
        let b = plan(&microos(), &["--repo", "/srv/repo", "--reformat", "/=xfs"]);
        assert!(b.problems.iter().any(|p| p.starts_with("transactional-update: --reformat would rewrite /etc/fstab")), "{:?}", b.problems);
        // grub.cfg searching for a filesystem the layout doesn't have.
        let mut v = microos();
        set_at(&mut v, &["boot", "grub", "grub_cfg", "search_uuids"], j(r#"["11111111-2222-3333-4444-555555555555"]"#));
        let b = plan(&v, &["--repo", "/srv/repo"]);
        assert!(b.problems.iter().any(|p| p.contains("grub.cfg searches for the filesystem 11111111-2222-3333-4444-555555555555")), "{:?}", b.problems);
    }

    /// Without boot.transactional (Tumbleweed's and Leap's writable root),
    /// the same disk takes the old arms: Fedora's /boot/grub2 GRUB (dracut
    /// and grub2-mkconfig in the chroot, a relabel), or systemd-boot through
    /// shim; and / is fstab's, the default subvolume.
    #[test]
    fn tumbleweed_and_leap_keep_their_arms() {
        let mut v = microos();
        set_at(&mut v, &["boot", "transactional"], Value::Null);
        set_at(&mut v, &["storage", "fstab", "0", "options"], Value::from("defaults"));
        set_at(&mut v, &["boot", "loader", "name"], Value::from("grub"));
        let b = plan(&v, &["--repo", "/srv/repo"]);
        let bt = text(b.stage(Stage::Boot));
        for want in ["dracut --force", "grub2-mkconfig -o /boot/grub2/grub.cfg", "setfiles", "mount --rbind"] {
            assert!(bt.contains(want), "{want}: {bt}");
        }
        assert!(!bt.contains("grub-snapshot.cfg") && !bt.contains("health-check.state"), "{bt}");
        assert!(b.notes.iter().any(|n| n.starts_with("UEFI GRUB 2 through shim")), "{:?}", b.notes);
        assert!(text(b.stage(Stage::Mount)).contains("mount -t btrfs -o defaults /dev/vdb3 /mnt/bko-target\n"));
        // Tumbleweed's systemd-boot through shim (sdbootutil).
        set_at(&mut v, &["boot", "loader", "name"], Value::from("systemd-boot"));
        let b = plan(&v, &["--repo", "/srv/repo"]);
        assert!(b.notes.iter().any(|n| n.starts_with("systemd-boot through shim")), "{:?}", b.notes);
        assert!(text(b.stage(Stage::Boot)).contains("dracut --force"));
    }

    fn sh(script: &str, path: &str) -> (bool, String, String) {
        let o = std::process::Command::new("sh").arg("-c").arg(script).env("PATH", path).output().unwrap();
        (o.status.success(), String::from_utf8_lossy(&o.stdout).into_owned(), String::from_utf8_lossy(&o.stderr).into_owned())
    }

    /// A fake `btrfs subvolume show`: a directory with a .subvol file
    /// holding its ID is a subvolume.
    fn fake_btrfs(t: &crate::testutil::Tree) -> String {
        let bin = t.p("/fakebin");
        std::fs::create_dir_all(&bin).unwrap();
        let f = bin.join("btrfs");
        std::fs::write(&f, "#!/bin/sh\n[ \"$1 $2\" = \"subvolume show\" ] && [ -f \"$3/.subvol\" ] || { echo \"ERROR: Not a Btrfs subvolume: $3\" >&2; exit 1; }\nprintf '@\\n\\tName: \\t\\t\\tx\\n\\tSubvolume ID: \\t\\t%s\\n' \"$(cat \"$3/.subvol\")\"\n").unwrap();
        use std::os::unix::fs::PermissionsExt;
        std::fs::set_permissions(&f, std::fs::Permissions::from_mode(0o755)).unwrap();
        format!("{}:{}", bin.display(), std::env::var("PATH").unwrap_or_default())
    }

    const RU: &str = "a534a4d5-21d8-64fa-7aa4-eb6434980056";

    /// A restored tree as the guest's: the ESP's stub, slot 2's grub.cfg,
    /// both slots' grub-snapshot.cfg (grub2-snapper-plugin's) and grub.cfg.
    fn grub_tree() -> (crate::testutil::Tree, String, String) {
        let t = crate::testutil::Tree::new("tu-check");
        let cfg = format!("set btrfs_relative_path=\"y\"\nmenuentry 'openSUSE MicroOS' {{\n\tinsmod btrfs\n\tsearch --no-floppy --fs-uuid --set=root {RU}\n\tlinux\t/boot/vmlinuz-7.3.3-1-default root=UUID={RU}  ${{extra_cmdline}} quiet\n\tinitrd\t/boot/initrd-7.3.3-1-default\n}}\n");
        t.file("/t/boot/efi/EFI/BOOT/grub.cfg", format!("set btrfs_relative_path=\"yes\"\nsearch --no-floppy --fs-uuid --set=root {RU}\nset prefix=(${{root}})/boot/grub2\nsource \"${{prefix}}/grub.cfg\"\n"));
        t.file("/t/boot/grub2/grub.cfg", &cfg);
        for n in [1, 2] {
            t.file(&format!("/t/.snapshots/{n}/snapshot/boot/grub2/grub.cfg"), &cfg);
            t.file(&format!("/t/.snapshots/{n}/grub-snapshot.cfg"), format!("\n  if [ -f \"/.snapshots/{n}/snapshot/boot/grub2/grub.cfg\" ]; then\n    snapshot_found=true\n    menuentry  \" openSUSE MicroOS  (7.3.3-1,2026-11-14T11:34,Snapshot Update of #1)\" \"/.snapshots/{n}/snapshot\" \"/@/.snapshots/{n}/snapshot\" {{\n        btrfs_subvol=\"$3\"\n        configfile \"$2/boot/grub2/grub.cfg\"\n      }}\n  fi\n"));
            t.file(&format!("/top/@/.snapshots/{n}/snapshot/.subvol"), (267 + n).to_string());
        }
        t.file("/t/.snapshots/grub-snapshot.cfg", "if [ -z \"$extra_cmdline\" ]; then\n  submenu  \"Start bootloader from a read-only snapshot\" {\n    source \"/.snapshots/2/grub-snapshot.cfg\"\n  }\nfi\n");
        let (tt, top) = (t.p("/t").display().to_string(), t.p("/top").display().to_string());
        (t, tt, top)
    }

    #[test]
    fn grub_check_runs() {
        let (t, tt, top) = grub_tree();
        let path = fake_btrfs(&t);
        let kept = vec!["BC00-0001".to_string(), RU.to_uppercase()];
        let script = check_script(&tt, &top, "grub", &kept).replace(&format!("{RUN_DIR}/"), &format!("{}/", t.p("/").display()));
        let (ok, out, err) = sh(&script, &path);
        assert!(ok, "{out}{err}");
        assert!(out.contains("transactional: grub: 2 snapshot entries lead to restored kernels and subvolumes") && err.is_empty(), "{out}{err}");
        // Another UUID (once per file), a slot's grub.cfg gone, a slot not
        // a subvolume, no grub.cfg at all.
        let (ok, _, err) = sh(&check_script(&tt, &top, "grub", &["bc00-0001".to_string()]).replace(&format!("{RUN_DIR}/"), &format!("{}/", t.p("/").display())), &path);
        assert!(!ok && err.matches("boot/grub2/grub.cfg names the filesystem").count() == 3 && err.contains(&format!("boot/efi/EFI/BOOT/grub.cfg names the filesystem {RU}, which load doesn't make")), "{err}");
        std::fs::remove_file(t.p("/t/.snapshots/1/snapshot/boot/grub2/grub.cfg")).unwrap();
        std::fs::remove_file(t.p("/top/@/.snapshots/2/snapshot/.subvol")).unwrap();
        std::fs::remove_file(t.p("/t/boot/grub2/grub.cfg")).unwrap();
        let (ok, _, err) = sh(&script, &path);
        assert!(!ok, "{err}");
        for want in [
            "transactional: boot/grub2/grub.cfg isn't in the restored root",
            "transactional: .snapshots/1/grub-snapshot.cfg: /.snapshots/1/snapshot/boot/grub2/grub.cfg isn't in the restored tree",
            "transactional: .snapshots/2/grub-snapshot.cfg: /@/.snapshots/2/snapshot isn't a subvolume of the restored filesystem",
            "transactional: 3 mismatches in the restored boot configuration",
        ] {
            assert!(err.contains(want), "{want}: {err}");
        }
    }

    #[test]
    fn bls_check_runs() {
        let t = crate::testutil::Tree::new("tu-bls");
        let lu = "c0000000-1111-2222-3333-444444444444";
        let e = |n: u32| format!("title openSUSE Aeon\nversion {n}@7.3.3-1-default\nsort-key opensuse-aeon\noptions root=UUID={RU} rd.luks.uuid=luks-{lu} rootflags=subvol=@/.snapshots/{n}/snapshot quiet\nlinux /opensuse-aeon/7.3.3-1-default/linux-abc\ninitrd /opensuse-aeon/7.3.3-1-default/initrd-{n}\n");
        t.file("/t/boot/efi/loader/entries/system-7.3.3-1-default-1.conf", e(1)).file("/t/boot/efi/loader/entries/system-7.3.3-1-default-2+3.conf", e(2));
        t.file("/t/boot/efi/opensuse-aeon/7.3.3-1-default/linux-abc", "k").file("/t/boot/efi/opensuse-aeon/7.3.3-1-default/initrd-1", "i").file("/t/boot/efi/opensuse-aeon/7.3.3-1-default/initrd-2", "i");
        t.file("/top/@/.snapshots/1/snapshot/.subvol", "258").file("/top/@/.snapshots/2/snapshot/.subvol", "269");
        let path = fake_btrfs(&t);
        let (tt, top) = (t.p("/t").display().to_string(), t.p("/top").display().to_string());
        let (ok, out, err) = sh(&check_script(&tt, &top, "bls", &[RU.into(), lu.into()]), &path);
        assert!(ok && out.contains("transactional: bls: 2 snapshot entries") && err.is_empty(), "{out}{err}");
        // An initrd gone, a slot gone, the LUKS UUID not kept.
        std::fs::remove_file(t.p("/t/boot/efi/opensuse-aeon/7.3.3-1-default/initrd-2")).unwrap();
        std::fs::remove_file(t.p("/top/@/.snapshots/1/snapshot/.subvol")).unwrap();
        let (ok, _, err) = sh(&check_script(&tt, &top, "bls", &[RU.into()]), &path);
        assert!(!ok, "{err}");
        for want in [
            "system-7.3.3-1-default-2+3.conf: /opensuse-aeon/7.3.3-1-default/initrd-2 isn't on the restored ESP",
            "system-7.3.3-1-default-1.conf: rootflags=subvol=@/.snapshots/1/snapshot isn't a subvolume of the restored filesystem",
            &format!("boot/efi/loader/entries/system-7.3.3-1-default-1.conf names the filesystem {lu}, which load doesn't make"),
            "transactional: 4 mismatches",
        ] {
            assert!(err.contains(want), "{want}: {err}");
        }
        // No entries at all.
        let empty = crate::testutil::Tree::new("tu-bls-none");
        let (ok, _, err) = sh(&check_script(&empty.p("/").display().to_string(), &top, "bls", &[]), &path);
        assert!(!ok && err.contains("boot/efi/loader/entries isn't on the restored ESP") && err.contains("no BLS entry"), "{err}");
    }

    /// The ID rewritten from the path, the rest of the file byte for byte
    /// (no final newline added), its times kept; kept when it's the same;
    /// a path that isn't a subvolume fails.
    #[test]
    fn health_check_rewrite_runs() {
        let t = crate::testutil::Tree::new("tu-hc");
        let f = crate::transactional::HC_STATE;
        t.file(&format!("/t{f}"), "LAST_WORKING_BTRFS_ID=269\nLAST_WORKING_SNAPSHOT=@/.snapshots/2/snapshot\nX=y");
        t.file("/top/@/.snapshots/2/snapshot/.subvol", "281");
        std::fs::create_dir_all(t.p("/run")).unwrap();
        let path = fake_btrfs(&t);
        let (tt, top) = (t.p("/t").display().to_string(), t.p("/top").display().to_string());
        let script = hc_script(&tt, &top).replace(&format!("{RUN_DIR}/"), &format!("{}/", t.p("/run").display()));
        let file = t.p(&format!("/t{f}"));
        let mtime = |p: &std::path::Path| std::fs::metadata(p).unwrap().modified().unwrap();
        let before = mtime(&file);
        std::thread::sleep(std::time::Duration::from_millis(20));
        let (ok, out, err) = sh(&script, &path);
        assert!(ok, "{out}{err}");
        assert!(out.contains("LAST_WORKING_BTRFS_ID 269 -> 281 (@/.snapshots/2/snapshot's ID here)"), "{out}");
        assert_eq!(std::fs::read_to_string(&file).unwrap(), "LAST_WORKING_BTRFS_ID=281\nLAST_WORKING_SNAPSHOT=@/.snapshots/2/snapshot\nX=y");
        assert_eq!(mtime(&file), before);
        let (ok, out, _) = sh(&script, &path);
        assert!(ok && out.contains("LAST_WORKING_BTRFS_ID=281 is @/.snapshots/2/snapshot's ID here too; kept"), "{out}");
        std::fs::remove_file(t.p("/top/@/.snapshots/2/snapshot/.subvol")).unwrap();
        let (ok, _, err) = sh(&script, &path);
        assert!(!ok && err.contains("LAST_WORKING_SNAPSHOT=@/.snapshots/2/snapshot isn't a subvolume of the restored filesystem"), "{err}");
        std::fs::remove_file(&file).unwrap();
        let (ok, _, err) = sh(&script, &path);
        assert!(!ok && err.contains("health-check.state isn't in the restored /var"), "{err}");
    }
}
