//! `load` for an ABRoot system (Vanilla OS: docs/design/vanillaos.md).
//! The mount stage builds ABRoot's own tree: fstab's (the present root at
//! `/`, /boot, the ESP), then the init LV at `boot/init`, the other root at
//! `part-future`, and /var, which ABRoot's mount-sys mounts by label and
//! drops from fstab.  Its boot stage regenerates nothing: the master
//! grub.cfg on /boot, each root's abroot.cfg, kernels and initramfs in the
//! init LV, shim and GRUB on the ESP all come back as they were, every
//! UUID, label and LV name they name kept; Vanilla's update-grub is a stub
//! that fails, its initramfs is ABRoot's (made in a chroot of the root
//! during an upgrade), and the restored roots are read-only images.  It
//! checks that the restored GRUB configs lead to kernels and roots that
//! exist, makes the UEFI entries, writes its log, and ends as every Linux
//! boot stage does.

use super::commands::{atimes_cmd, Builder, Cmd};
use super::RUN_DIR;
use crate::json::Value;

/// What a restored ABRoot tree has once the restore is done: the present
/// root's record of its image.
pub const MARKER: &str = "abimage.abr";
/// The marker when the dump's present root had no /abimage.abr: ABRoot's
/// state directory in /var.
pub const MARKER_NO_IMAGE: &str = "var/lib/abroot";

/// The layout's boot.abroot, when the source is an ABRoot system.
pub fn of(layout: &Value) -> Option<&Value> {
    layout.at(&["boot", "abroot"]).filter(|v| v.as_object().is_some())
}

fn s(v: &Value, k: &str) -> Option<String> {
    v.get(k).and_then(Value::as_str).map(str::to_string)
}

/// boot.abroot's root of label L.
fn root<'a>(a: &'a Value, l: &str) -> Option<&'a Value> {
    a.get("roots").and_then(Value::as_array)?.iter().find(|r| r.get("label").and_then(Value::as_str) == Some(l))
}

fn present(a: &Value) -> Option<&Value> {
    a.get("roots").and_then(Value::as_array)?.iter().find(|r| r.get("present") == Some(&Value::Bool(true)))
}

/// The restore gate's file (crate::other::restored_marker).
pub fn marker(layout: &Value) -> &'static str {
    match of(layout).and_then(present) {
        Some(r) if r.get("abimage").is_some_and(Value::is_null) => MARKER_NO_IMAGE,
        _ => MARKER,
    }
}

/// One root as the consistency check wants it: its label, whether the dump
/// found it empty, and the names its abroot.cfg's root= may use.
pub struct RootWant {
    pub label: String,
    pub empty: bool,
    pub uuid: String,
    pub mapper: String,
    pub vg_lv: String,
}

/// The consistency check of a restored tree at T, as a shell script: the
/// master grub.cfg has `set default=0` or `1` and two configfile lines, and
/// its default entry leads to DEFAULT_LABEL (the root the dump's grub.cfg
/// booted next); each `set root=(lvm/...)` names the init LV (INIT_LV);
/// each root's init/LABEL/abroot.cfg is there unless the dump found the root
/// empty; in it, the first `linux` line with a kernel in /LABEL/ and the
/// first `initrd` after it name files in init/LABEL/, its root= is that
/// root's own filesystem (UUID=, /dev/mapper/ or /dev/VG/LV), and the last
/// `search --fs-uuid` before it in its block names the init LV's
/// filesystem (INIT_UUID), as crate::abroot::parse_abroot_cfg reads it.  A
/// mismatch is said and fails the stage (a backup taken across an
/// upgrade's grub.cfg swap shows here); an empty root is a warning.  The
/// errors come after the warnings, ended by a count, so the last lines of
/// a failure name what failed.
pub fn check_script(t: &str, default_label: &str, init_lv: &str, init_uuid: &str, roots: &[RootWant]) -> String {
    let q = super::shell_quote;
    let mut chk = String::new();
    for r in roots {
        chk += &format!("chk {} {} {} {} {}\n", q(&r.label), if r.empty { 1 } else { 0 }, q(&r.uuid), q(&r.mapper), q(&r.vg_lv));
    }
    format!(
        r#"t=$(readlink -f {t}) || exit 1; want={want}; ilv={ilv}; iuuid={iuuid}; errs=; nbad=0
err() {{ errs="${{errs}}abroot: $*
"; nbad=$((nbad + 1)); }}
g=$t/boot/grub/grub.cfg
[ -f "$g" ] || {{ echo "abroot: boot/grub/grub.cfg isn't on the restored /boot" >&2; exit 1; }}
d=$(sed -n 's/^[[:space:]]*set[[:space:]][[:space:]]*default=\([0-9][0-9]*\)[[:space:]]*$/\1/p' "$g" | head -n 1)
set -- $(awk '$1 == "configfile" {{ p = $2; gsub(/"/, "", p); sub(/^\([^)]*\)/, "", p); sub(/^\//, "", p); sub(/\/.*$/, "", p); print p }}' "$g")
[ $# -eq 2 ] || err "grub.cfg has $# configfile lines, not 2"
case $d in 0) dl=${{1:-}} ;; 1) dl=${{2:-}} ;; *) dl=; err "grub.cfg has no set default=0 or 1${{d:+ (it has default=$d)}}" ;; esac
[ -z "$dl" ] || [ "$dl" = "$want" ] || err "grub.cfg's default entry ($d) leads to $dl, the dump's to $want (a backup taken across an upgrade's grub.cfg swap?)"
for r in $(awk '$1 == "set" && $2 ~ /^root=\(lvm\// {{ sub(/^root=/, "", $2); print $2 }}' "$g"); do
  [ "$r" = "(lvm/$ilv)" ] || err "grub.cfg sets root=$r, not (lvm/$ilv), the init LV"
done
chk() {{
  L=$1; c=$t/boot/init/$L/abroot.cfg
  if [ ! -f "$c" ]; then
    if [ "$2" = 1 ]; then
      echo "abroot: warning: $L was empty on the source (no usr/, no init/$L/abroot.cfg): restored empty, its grub.cfg entry boots nothing, as on the source" >&2
      [ "$L" != "$dl" ] || err "grub.cfg's default entry leads to $L, which is empty"
    else
      err "$L: init/$L/abroot.cfg isn't on the restored init LV"
    fi
    return 0
  fi
  # The first linux line whose kernel is in /$L/, its root=, the first
  # initrd after it, and the last search --fs-uuid before it in its block
  # (an upgrade's abroot.cfg is a whole generated GRUB config: its
  # 00_header can search for the font's filesystem first).
  v=$(awk -v L="$L" '
    $1 == "menuentry" || $1 == "submenu" || $1 == "}}" {{ s = "" }}
    $1 == "search" {{ s = ""; for (j = 2; j < NF; j++) if ($j == "--fs-uuid" || $j == "-u") s = $NF }}
    k == "" && $1 == "linux" {{ p = $2; sub(/^\([^)]*\)/, "", p); if (index(p, "/" L "/") == 1) {{ k = $2; u = s; for (j = 3; j <= NF; j++) if ($j ~ /^root=/) {{ r = $j; sub(/^root=/, "", r); break }} }}; next }}
    k != "" && i == "" && $1 == "initrd" {{ i = $2 }}
    END {{ print k; print i; print r; print u }}' "$c")
  k=$(printf '%s\n' "$v" | sed -n 1p); i=$(printf '%s\n' "$v" | sed -n 2p); r=$(printf '%s\n' "$v" | sed -n 3p); u=$(printf '%s\n' "$v" | sed -n 4p)
  [ -n "$k" ] || {{ err "$L: abroot.cfg has no linux line with a kernel in init/$L/"; return 0; }}
  [ -n "$i" ] || err "$L: abroot.cfg has no initrd line after its linux line"
  for p in $k $i; do
    f=${{p#\(*\)}}
    case $f in "/$L/"*) [ -f "$t/boot/init$f" ] || err "$L: abroot.cfg's $p isn't on the restored init LV" ;; *) err "$L: abroot.cfg's $p isn't in init/$L/" ;; esac
  done
  case $r in
    "") err "$L: abroot.cfg's linux line has no root=" ;;
    "UUID=$3"|"/dev/mapper/$4"|"$5") ;;
    *) err "$L: abroot.cfg's root=$r isn't $L's filesystem (UUID=$3, /dev/mapper/$4 or $5)" ;;
  esac
  [ -z "$u" ] || [ "$u" = "$iuuid" ] || err "$L: abroot.cfg searches for the filesystem $u, not the init LV's ($iuuid)"
}}
{chk}[ -z "$errs" ] || {{ printf %s "$errs" >&2; echo "abroot: $nbad mismatches between grub.cfg, the init LV and the roots in the restored tree (a backup taken across an upgrade?)" >&2; exit 1; }}
echo "abroot: grub.cfg boots $dl by default; each root's abroot.cfg leads to its kernel, initramfs and filesystem"
"#,
        t = q(t),
        want = q(default_label),
        ilv = q(init_lv),
        iuuid = q(init_uuid),
    )
}

impl Builder<'_> {
    /// The mounts ABRoot's tree adds to fstab's (HAVE, the mount points
    /// fstab gives): the init LV at /boot/init, the other root at
    /// /part-future, and /var when fstab hasn't it (mount-sys drops it).
    pub(crate) fn abroot_mounts(&mut self, a: &Value, have: &[String]) -> Vec<(String, Cmd)> {
        let t = self.opts.target.clone();
        let future = s(a, "future");
        let mut want: Vec<(&str, Option<Value>, String)> = vec![
            (crate::abroot::INIT_AT, a.get("init").cloned(), "the init LV (both roots' kernels, initramfs and abroot.cfg), where ABRoot mounts it".into()),
            (crate::abroot::FUTURE_AT, future.as_deref().and_then(|l| root(a, l)).cloned(), format!("the other root ({}), where ABRoot mounts it", future.as_deref().unwrap_or("?"))),
        ];
        if !have.iter().any(|p| p == "/var") {
            want.push(("/var", a.get("var").cloned(), "/var, which ABRoot's mount-sys mounts by label".into()));
        }
        let mut out = Vec::new();
        for (path, fs, what) in want {
            let dev = fs.as_ref().and_then(|f| s(f, "device"));
            let ty = fs.as_ref().and_then(|f| s(f, "fstype"));
            let (Some(dev), Some(ty)) = (dev, ty) else {
                self.problems.push(format!("ABRoot: the dump has no device for {what}, so the tree's {path} can't be mounted"));
                continue;
            };
            let Some(node) = self.names.dev(&dev) else {
                self.problems.push(format!("ABRoot: {what} is on {dev}, which isn't restored"));
                continue;
            };
            out.push((path.to_string(), Cmd::new(&["mount", "-t", &ty, "-o", "defaults", &node, &format!("{t}{path}")]).why(format!("ABRoot: {what} (not in fstab)"))));
        }
        out
    }

    /// What version 1 doesn't restore on ABRoot (plan problems), and what
    /// the plan should say (notes).
    fn abroot_problems(&mut self, a: &Value) {
        if a.get("thin_two_roots") != Some(&Value::Bool(true)) || a.get("v1_system") == Some(&Value::Bool(true)) {
            let c = |k: &str| a.at(&["config", k]).and_then(Value::as_str).unwrap_or("").to_string();
            self.problems.push(format!(
                "ABRoot: not the layout version 1 restores (ABRoot v2 with thinProvisioning, the thinInitVolume LV and two roots labelled partLabelA and partLabelB: here thinProvisioning={}, thinInitVolume={:?}, partLabelA={:?}, partLabelB={:?}{}); ABRoot v1's /.system and layouts without thin provisioning aren't restored",
                a.at(&["config", "thinProvisioning"]).and_then(Value::as_bool).unwrap_or(false),
                c("thinInitVolume"),
                c("partLabelA"),
                c("partLabelB"),
                if a.get("v1_system") == Some(&Value::Bool(true)) { ", and / has ABRoot v1's /.system" } else { "" },
            ));
        }
        if a.get("operation_lock") == Some(&Value::Bool(true)) {
            self.problems.push(format!("ABRoot: {} was there when the dump was made: an `abroot upgrade` (or `abroot pkg`) was running, so the other root, the init LV and grub.cfg may have been half-written; dump and back up again once it has finished", crate::abroot::LOCK));
        }
        if !self.layout.uefi() {
            self.problems.push("ABRoot with BIOS boot isn't restored: Vanilla OS's installer makes UEFI systems only".into());
        }
        let default_label = a.at(&["grub", "default_label"]).and_then(Value::as_str);
        if default_label.is_none() {
            self.problems.push(format!("ABRoot: the dump has no default entry of the master {} (its `set default=0|1` and the entry's configfile), so the restored boot can't be checked", crate::abroot::GRUB_CFG));
        }
        let key_files: Vec<String> = self
            .layout
            .arr(&["storage", "crypttab"])
            .iter()
            .filter(|e| s(e, "key_file").is_some_and(|k| !matches!(k.as_str(), "none" | "-" | "")))
            .filter_map(|e| s(e, "name"))
            .collect();
        if !key_files.is_empty() {
            self.problems.push(format!("ABRoot: crypttab opens {} with a key file, which the ABRoot boot stage doesn't put back yet (Vanilla's /var unlocks by passphrase)", key_files.join(", ")));
        }
        let present = s(a, "present");
        if present.is_none() {
            self.problems.push("ABRoot: the dump names no present root (no filesystem labelled partLabelA or partLabelB was mounted at /)".into());
        }
        if let Some(d) = default_label.filter(|d| root(a, d).is_some_and(|r| r.get("empty") == Some(&Value::Bool(true)))) {
            self.problems.push(format!("ABRoot: the source's grub.cfg boots {d} by default, and {d} was empty (no usr/, nothing in init/{d}): the restored system would boot nothing"));
        }
        if let (Some(d), Some(p)) = (default_label, present.as_deref()) {
            if d != p {
                self.notes.push(format!("ABRoot: the source's grub.cfg boots {d} next, not {p}, the root it was running (an upgrade had finished and not been rebooted into, or {p}'s entry was picked at GRUB's menu): the restored system boots {d} first, as the source would have"));
            }
        }
        for r in a.get("roots").and_then(Value::as_array).into_iter().flatten() {
            let l = s(r, "label").unwrap_or_default();
            if r.get("empty") == Some(&Value::Bool(true)) {
                self.notes.push(format!("ABRoot: {l} was empty on the source (a fresh install's other root: no usr/, nothing in init/{l}): restored empty, and its grub.cfg entry boots nothing until the next `abroot upgrade`, as on the source"));
            } else if r.get("read") != Some(&Value::Bool(true)) {
                self.notes.push(format!("ABRoot: the dump couldn't read {l} (not run as root, or its read-only mount failed), so it can't say whether it was empty: the boot stage's check counts it as used"));
            }
        }
        self.notes.push("ABRoot finds its roots, init and /var by label, the first match: boot the restored disk with the source's disk (or any other Vanilla OS disk) detached".into());
    }

    /// The consistency check's expectations, from boot.abroot.
    fn abroot_want(&self, a: &Value) -> (String, String, String, Vec<RootWant>) {
        let default_label = a.at(&["grub", "default_label"]).and_then(Value::as_str).or(a.get("present").and_then(Value::as_str)).unwrap_or("").to_string();
        let init = a.get("init").cloned().unwrap_or(Value::Null);
        let roots = a
            .get("roots")
            .and_then(Value::as_array)
            .into_iter()
            .flatten()
            .map(|r| {
                let mapper = s(r, "dm_name").unwrap_or_default();
                let vg_lv = crate::blockdev::split_lvm_dm_name(&mapper).map(|(vg, lv)| format!("/dev/{vg}/{lv}")).unwrap_or_default();
                RootWant { label: s(r, "label").unwrap_or_default(), empty: r.get("empty") == Some(&Value::Bool(true)), uuid: s(r, "uuid").unwrap_or_default(), mapper, vg_lv }
            })
            .collect();
        (default_label, s(&init, "dm_name").unwrap_or_default(), s(&init, "uuid").unwrap_or_default(), roots)
    }

    /// The boot stage on ABRoot (docs/design/vanillaos.md, "load").
    pub(crate) fn abroot_boot(&mut self, a: &Value) -> Vec<Cmd> {
        self.abroot_problems(a);
        let t = self.opts.target.clone();
        let marker = marker(&self.layout.v);
        let mut out = vec![Cmd::note(format!("refuses to start unless {t}/{marker} is there: the restored present root"))];
        out.push(Cmd::new(&["sh", "-c", &atimes_cmd(&t, "if-missing", &self.ro_shared())]).why("the restored atimes (the restore stage lists them), to put back after this stage's reads"));
        let what = "ABRoot: /boot (the master grub.cfg), the ESP, the init LV and both roots are restored as they were; no initramfs, no grub-install, no update-grub (a failing stub on Vanilla OS), no chroot";
        self.notes.push(what.into());
        out.push(Cmd::note(what));
        let (default_label, init_lv, init_uuid, roots) = self.abroot_want(a);
        out.push(
            Cmd::new(&["sh", "-c", &check_script(&t, &default_label, &init_lv, &init_uuid, &roots)])
                .why("the master grub.cfg's default entry leads to the dump's root, and each root's abroot.cfg to its kernel, initramfs and filesystem, in the restored tree")
                .warns(),
        );
        if self.layout.uefi() && !self.opts.no_nvram {
            out.extend(self.efi_entries());
        }
        let log = format!("{t}/var/log/bko-holt");
        out.push(Cmd::new(&["mkdir", "-p", &log]));
        out.push(Cmd::new(&["cp", "-a", &format!("{RUN_DIR}/log/."), &format!("{log}/")]).why("this restore's log, on the restored system (/var is the vos-var filesystem)"));
        out.extend(self.boot_tail(&t));
        out
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn run(script: &str) -> (bool, String, String) {
        let o = std::process::Command::new("sh").arg("-c").arg(script).output().unwrap();
        (o.status.success(), String::from_utf8_lossy(&o.stdout).into_owned(), String::from_utf8_lossy(&o.stderr).into_owned())
    }

    const UA: &str = "b0b1b2b3-b4b5-b6b7-b8b9-babbbcbdbebf";
    const UB: &str = "c0b1b2b3-b4b5-b6b7-b8b9-babbbcbdbebf";
    const UI: &str = "20111213-1415-1617-1819-1a1b1c1d1e1f";

    fn wants(b_empty: bool) -> Vec<RootWant> {
        vec![
            RootWant { label: "vos-a".into(), empty: false, uuid: UA.into(), mapper: "vos--root-root--a".into(), vg_lv: "/dev/vos-root/root-a".into() },
            RootWant { label: "vos-b".into(), empty: b_empty, uuid: UB.into(), mapper: "vos--root-root--b".into(), vg_lv: "/dev/vos-root/root-b".into() },
        ]
    }

    /// A restored tree: the installer's master grub.cfg, vos-a's abroot.cfg
    /// as the installer writes it, vos-b's as an upgrade does (or none).
    fn tree(b: bool) -> crate::testutil::Tree {
        let t = crate::testutil::Tree::new("abroot-check");
        t.file("/boot/grub/grub.cfg", crate::abroot::tests_master());
        let k = "6.17.8-amd64";
        t.file(&format!("/boot/init/vos-a/vmlinuz-{k}"), "k")
            .file(&format!("/boot/init/vos-a/initrd.img-{k}"), "i")
            .file("/boot/init/vos-a/abroot.cfg", format!("insmod gzio\nlinux   (lvm/vos--root-init)/vos-a/vmlinuz-{k} root=UUID={UA} quiet splash\ninitrd  (lvm/vos--root-init)/vos-a/initrd.img-{k}\n"));
        if b {
            t.file("/boot/init/vos-b/vmlinuz-6.18.1-amd64", "k")
                .file("/boot/init/vos-b/initrd.img-6.18.1-amd64", "i")
                // An upgrade's: grub-mkconfig's 00_header, with its font
                // search of another filesystem, first.
                .file("/boot/init/vos-b/abroot.cfg", format!("{}  search --no-floppy --fs-uuid --set=root {UI}\n  linux   /vos-b/vmlinuz-6.18.1-amd64 root=/dev/mapper/vos--root-root--b quiet\n  initrd  /vos-b/initrd.img-6.18.1-amd64\n", crate::abroot::tests_upgrade_header()));
        }
        t
    }

    #[test]
    fn check_passes_and_fails() {
        let root = |t: &crate::testutil::Tree| t.root().to_string_lossy().into_owned();
        let t = tree(true);
        let (ok, out, err) = run(&check_script(&root(&t), "vos-a", "vos--root-init", UI, &wants(false)));
        assert!(ok, "{out}{err}");
        assert!(out.contains("grub.cfg boots vos-a by default") && err.is_empty(), "{out}{err}");
        // A fresh install: vos-b empty, a warning; but not as the default.
        let t = tree(false);
        let (ok, _, err) = run(&check_script(&root(&t), "vos-a", "vos--root-init", UI, &wants(true)));
        assert!(ok && err.contains("abroot: warning: vos-b was empty on the source"), "{err}");
        let (ok, _, err) = run(&check_script(&root(&t), "vos-a", "vos--root-init", UI, &wants(false)));
        assert!(!ok && err.contains("vos-b: init/vos-b/abroot.cfg isn't on the restored init LV"), "{err}");
        // The default entry leads elsewhere than the dump's.
        let t = tree(true);
        let (ok, _, err) = run(&check_script(&root(&t), "vos-b", "vos--root-init", UI, &wants(false)));
        assert!(!ok && err.contains("grub.cfg's default entry (0) leads to vos-a, the dump's to vos-b"), "{err}");
        assert!(err.trim_end().ends_with("abroot: 1 mismatches between grub.cfg, the init LV and the roots in the restored tree (a backup taken across an upgrade?)"), "{err}");
        // A kernel missing, a root= of another root, a search for another
        // filesystem, a set root= of another LV.
        std::fs::remove_file(t.p("/boot/init/vos-a/initrd.img-6.17.8-amd64")).unwrap();
        t.file("/boot/init/vos-b/abroot.cfg", format!("  search --no-floppy --fs-uuid --set=root {UA}\n  linux   /vos-b/vmlinuz-6.18.1-amd64 root=UUID={UA} quiet\n  initrd  /vos-a/initrd.img-6.18.1-amd64\n"));
        t.file("/boot/grub/grub.cfg", crate::abroot::tests_master().replacen("(lvm/vos--root-init)", "(lvm/other-init)", 1));
        let (ok, _, err) = run(&check_script(&root(&t), "vos-a", "vos--root-init", UI, &wants(false)));
        assert!(!ok, "{err}");
        for want in [
            "grub.cfg sets root=(lvm/other-init), not (lvm/vos--root-init), the init LV",
            "vos-a: abroot.cfg's (lvm/vos--root-init)/vos-a/initrd.img-6.17.8-amd64 isn't on the restored init LV",
            "vos-b: abroot.cfg's /vos-a/initrd.img-6.18.1-amd64 isn't in init/vos-b/",
            &format!("vos-b: abroot.cfg's root=UUID={UA} isn't vos-b's filesystem"),
            &format!("vos-b: abroot.cfg searches for the filesystem {UA}, not the init LV's ({UI})"),
            "abroot: 5 mismatches",
        ] {
            assert!(err.contains(want), "{want}: {err}");
        }
        // No default, a third configfile.
        t.file("/boot/grub/grub.cfg", crate::abroot::tests_master().replace("set default=0", "set default=saved") + "menuentry x {\n configfile /x/abroot.cfg\n}\n");
        let (ok, _, err) = run(&check_script(&root(&t), "vos-a", "vos--root-init", UI, &wants(false)));
        assert!(!ok && err.contains("grub.cfg has 3 configfile lines, not 2") && err.contains("grub.cfg has no set default=0 or 1"), "{err}");
        // /dev/VG/LV as root= counts.
        let t = tree(true);
        t.file("/boot/init/vos-b/abroot.cfg", "linux /vos-b/vmlinuz-6.18.1-amd64 root=/dev/vos-root/root-b\ninitrd /vos-b/initrd.img-6.18.1-amd64\n");
        assert!(run(&check_script(&root(&t), "vos-a", "vos--root-init", UI, &wants(false))).0);
        // Only another root's kernel in vos-b's abroot.cfg.
        t.file("/boot/init/vos-b/abroot.cfg", format!("linux /vos-a/vmlinuz-6.17.8-amd64 root=UUID={UB}\ninitrd /vos-a/initrd.img-6.17.8-amd64\n"));
        let (ok, _, err) = run(&check_script(&root(&t), "vos-a", "vos--root-init", UI, &wants(false)));
        assert!(!ok && err.contains("vos-b: abroot.cfg has no linux line with a kernel in init/vos-b/") && err.contains("abroot: 1 mismatches"), "{err}");
    }

    #[test]
    fn markers() {
        let v = crate::json::parse(r#"{"boot": {"abroot": {"roots": [{"label": "vos-a", "present": true, "abimage": {"digest": "sha256:x"}}, {"label": "vos-b", "present": false, "abimage": null}]}}}"#).unwrap();
        assert!(of(&v).is_some());
        assert_eq!(marker(&v), MARKER);
        assert_eq!(crate::other::restored_marker(&v), MARKER);
        let v = crate::json::parse(r#"{"boot": {"abroot": {"roots": [{"label": "vos-a", "present": true, "abimage": null}]}}}"#).unwrap();
        assert_eq!(crate::other::restored_marker(&v), MARKER_NO_IMAGE);
        assert!(of(&crate::json::parse(r#"{"boot": {"abroot": null}}"#).unwrap()).is_none());
    }
}
