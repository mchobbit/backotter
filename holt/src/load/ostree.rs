//! `load` for an ostree system (Fedora's atomic desktops, Bazzite:
//! docs/design/bazzite.md).  The mount stage builds fstab's tree as for any
//! system: the physical root (fstab's `/`, which holds ostree/repo and the
//! deployments, each with its own etc), /boot, the ESP, and the other
//! subvolumes.  Its boot stage regenerates nothing: /boot (the Boot Loader
//! Specification entries, the image's kernels and initramfs, bootupd's
//! static grub.cfg), the ESP and /ostree come back as they were, and a
//! chroot couldn't run (the physical root has no /bin/sh) and mustn't (a
//! deployment's /usr is hardlinks into the repository's objects, whose
//! checksums cover their content, mode, owner and SELinux label).  It
//! checks the restored loader entries against the deployments, makes the
//! UEFI entries, writes its log, and ends as every Linux boot stage does.

use super::commands::{atimes_cmd, Builder, Cmd};
use super::layout::Layout;
use super::RUN_DIR;
use crate::json::Value;

/// What a restored ostree tree has once the restore is done: the
/// repository's config, at the top of the physical root (which has no
/// etc/fstab of its own).
pub const MARKER: &str = "ostree/repo/config";

/// The layout's boot.ostree, when the source is an ostree system.
pub fn of(layout: &Value) -> Option<&Value> {
    layout.at(&["boot", "ostree"]).filter(|v| v.as_object().is_some())
}

/// The consistency check of a restored tree at T, as a shell script.  For
/// every loader entry ostree wrote (boot/loader/entries/ostree-*.conf): its
/// `linux` and `initrd` files are on the restored /boot, its `ostree=` path
/// leads to a deployment directory (ostree/deploy/STATEROOT/deploy/NAME)
/// of the restored physical root, and its boot.N is /boot/loader's
/// loader.N.  Each mismatch is said, and fails the stage: a live backup can
/// catch /boot and the root on either side of a deploy.  A deployment no
/// entry leads to (an orphan checkout, from a backup taken while one was
/// staged) is only a warning: it boots nothing, and goes at the next
/// `ostree admin cleanup`.  So is a /boot/loader other than the dump's
/// (WANT_LOADER, when it had one): the backup was taken after a deploy the
/// dump didn't see, and the restored tree is still whole.
pub fn check_script(t: &str, want_loader: Option<&str>) -> String {
    let t = super::shell_quote(t);
    let want = want_loader.map(super::shell_quote).unwrap_or_default();
    format!(
        r#"t=$(readlink -f {t}) || exit 1; want={want}; bad=0; seen=
err() {{ echo "ostree: $*" >&2; bad=1; }}
l=$(readlink "$t/boot/loader") || {{ echo "ostree: $t/boot/loader isn't a symlink" >&2; exit 1; }}
case $l in loader.[0-9]*) n=${{l#loader.}} ;; *) echo "ostree: $t/boot/loader leads to $l, not loader.N" >&2; exit 1 ;; esac
[ -z "$want" ] || [ "$l" = "$want" ] || echo "ostree: warning: /boot/loader is $l here, $want in the dump: the backup was taken after a deploy the dump didn't see" >&2
found=0
for e in "$t"/boot/loader/entries/ostree-*.conf; do
  [ -f "$e" ] || continue
  found=$((found + 1)); f=${{e##*/}}
  awk '$1 == "linux" {{ k = 1 }} END {{ exit !k }}' "$e" || err "$f: no linux line"
  for p in $(awk '$1 == "linux" || $1 == "initrd" {{ print $2 }}' "$e"); do
    [ -f "$t/boot$p" ] || err "$f: $p isn't on the restored /boot"
  done
  o=$(awk '$1 == "options" {{ for (i = 2; i <= NF; i++) if ($i ~ /^ostree=/) {{ sub(/^ostree=/, "", $i); print $i; exit }} }}' "$e")
  [ -n "$o" ] || {{ err "$f: no ostree= in its options"; continue; }}
  case $o in /ostree/boot.$n/*) ;; *) err "$f: ostree=$o, but /boot/loader is $l (boot.$n)" ;; esac
  d=$(readlink -e "$t$o") || {{ err "$f: ostree=$o leads nowhere in the restored tree"; continue; }}
  r=${{d#"$t"/ostree/deploy/}}
  case $r in "$d") err "$f: ostree=$o leads to $d, outside $t/ostree/deploy" ;;
    */deploy/*/*) err "$f: ostree=$o leads to ${{d#"$t"}}, inside a deployment, not to one" ;;
    */*/deploy/*) err "$f: ostree=$o leads to ${{d#"$t"}}, not to a deployment" ;;
    ?*/deploy/?*) [ -d "$d" ] || err "$f: ostree=$o leads to ${{d#"$t"}}, not a directory"; seen="$seen $d" ;;
    *) err "$f: ostree=$o leads to ${{d#"$t"}}, not to a deployment (ostree/deploy/STATEROOT/deploy/NAME)" ;;
  esac
done
[ "$found" -gt 0 ] || err "no loader entries (boot/loader/entries/ostree-*.conf) on the restored /boot"
for d in "$t"/ostree/deploy/*/deploy/*; do
  [ -d "$d" ] && [ ! -L "$d" ] || continue
  case "$seen " in *" $d "*) ;; *) echo "ostree: warning: no loader entry leads to ${{d#"$t"}} (an orphan checkout: it boots nothing, and goes at the next ostree admin cleanup)" >&2 ;; esac
done
[ "$bad" = 0 ] || exit 1
echo "ostree: $found loader entries, each with its kernel, initramfs and deployment"
"#
    )
}

/// `python3 -c LABEL_PY NAME SRC TOP`: the extended attribute NAME
/// (security.selinux) of SRC (the restored /var/log) copied onto TOP (the
/// log directory) and everything below it.  SRC without it (SELinux
/// unused): nothing to do.  The rescue system has python3, but not attr's
/// setfattr.
pub const LABEL_PY: &str = "import os, sys
name, src, top = sys.argv[1:4]
try:
    v = os.getxattr(src, name, follow_symlinks=False)
except OSError:
    sys.exit(0)
for d, _, files in os.walk(top):
    for p in [d] + [os.path.join(d, f) for f in files]:
        os.setxattr(p, name, v, follow_symlinks=False)
";

/// Whether any device of the layout holds a CONTENT_TYPE signature.
fn any_content(layout: &Layout, ty: &str) -> bool {
    for sect in ["disks", "md_arrays", "dm_devices"] {
        for d in layout.arr(&["storage", sect]) {
            let mut all = vec![d];
            all.extend(d.get("partitions").and_then(Value::as_array).into_iter().flatten());
            if all.iter().any(|x| x.at(&["content", "type"]).and_then(Value::as_str) == Some(ty)) {
                return true;
            }
        }
    }
    false
}

impl Builder<'_> {
    /// What version 1 doesn't restore on ostree (plan problems), and what
    /// it restores untested (notes).
    fn ostree_problems(&mut self, o: &Value) {
        let get = |p: &[&str]| o.at(p).and_then(Value::as_str).map(str::to_string);
        match get(&["prepare_root", "composefs"]).as_deref() {
            Some(m @ ("verity" | "signed")) => self.problems.push(format!(
                "ostree: prepare-root.conf has composefs enabled = {m}, which needs fs-verity on the repository's objects; the archiver doesn't keep fs-verity, so the restored system wouldn't boot"
            )),
            Some("maybe") | None => self.notes.push(
                "ostree: composefs is enabled = maybe (or left to ostree's default): the restored objects have no fs-verity, so composefs mounts without it".into(),
            ),
            _ => {}
        }
        match o.at(&["repo", "ex_fsverity"]) {
            Some(v) if v.get("required").and_then(Value::as_str) == Some("true") => self.problems.push(
                "ostree: the repository's config has [ex-fsverity] required=true; the archiver doesn't keep fs-verity, so ostree would refuse the restored objects".into(),
            ),
            Some(v) if v.as_object().is_some() => self.notes.push("ostree: the repository's config has an [ex-fsverity] section; the restored objects have no fs-verity".into()),
            _ => {}
        }
        if !self.layout.uefi() {
            self.problems.push("ostree with BIOS boot isn't restored yet: bootupd's core.img is in the BIOS boot partition, which isn't copied".into());
        }
        let crypttab = self.layout.arr(&["storage", "crypttab"]).iter().any(|e| e.get("name").is_some());
        if any_content(self.layout, "crypto_LUKS") || crypttab {
            self.problems.push("ostree with LUKS isn't restored yet: its crypttab and keys are in a deployment's etc, and the unlocking (rd.luks.uuid=, TPM2 or FIDO2 tokens) is untested".into());
        }
        if any_content(self.layout, "LVM2_member") || !self.layout.arr(&["storage", "lvm", "volume_groups"]).is_empty() {
            self.problems.push("ostree with LVM isn't restored yet: its devices file is in a deployment's etc".into());
        }
        let fstab = |f: &str| self.layout.arr(&["storage", "fstab"]).iter().any(|e| e.get("file").and_then(Value::as_str) == Some(f));
        if !fstab("/") {
            self.problems.push("ostree without `/` in fstab (bootc install to-disk, Fedora CoreOS) isn't restored: the physical root can't be told".into());
        }
        if !fstab("/boot") {
            self.problems.push("ostree without a separate /boot in fstab isn't restored yet: the loader entries' paths are then inside the physical root".into());
        }
        if o.get("bootupd_state") != Some(&Value::Bool(true)) {
            self.notes.push("ostree: no /boot/bootupd-state.json, so this /boot and ESP weren't written by bootupd; they're restored as they were, but only bootupd's layout is tested".into());
        }
    }

    /// Where the restored system's /var is: fstab's /var, or else the
    /// stateroot's own var, which ostree binds there at boot.
    fn ostree_var(&self, o: &Value) -> Option<String> {
        let t = &self.opts.target;
        if self.layout.arr(&["storage", "fstab"]).iter().any(|e| e.get("file").and_then(Value::as_str) == Some("/var")) {
            return Some(format!("{t}/var"));
        }
        let stateroot = o.get("stateroot").and_then(Value::as_str).map(str::to_string).or_else(|| {
            let deps = o.get("deployments").and_then(Value::as_array).cloned().unwrap_or_default();
            deps.iter().find(|d| d.get("booted") == Some(&Value::Bool(true))).or(deps.first()).and_then(|d| d.get("stateroot")).and_then(Value::as_str).map(str::to_string)
        })?;
        Some(format!("{t}/ostree/deploy/{stateroot}/var"))
    }

    /// The boot stage on ostree (docs/design/bazzite.md, "load").
    pub(crate) fn ostree_boot(&mut self, o: &Value) -> Vec<Cmd> {
        self.ostree_problems(o);
        let t = self.opts.target.clone();
        let mut out = vec![Cmd::note(format!("refuses to start unless {t}/{MARKER} is there: the restored physical root, with the repository and the deployments"))];
        out.push(Cmd::new(&["sh", "-c", &atimes_cmd(&t, "if-missing", &self.ro_shared())]).why("the restored atimes (the restore stage lists them), to put back after this stage's reads"));
        let what = "ostree: /boot, the ESP and /ostree are restored verbatim; no initramfs, no grub2-mkconfig, no grub2-install, no chroot";
        self.notes.push(what.into());
        out.push(Cmd::note(what));
        out.push(
            Cmd::new(&["sh", "-c", &check_script(&t, o.get("boot_loader").and_then(Value::as_str))])
                .why("each loader entry's kernel, initramfs and deployment are in the restored tree, by its boot.N"),
        );
        if self.layout.uefi() && !self.opts.no_nvram {
            out.extend(self.efi_entries());
        }
        match self.ostree_var(o) {
            Some(var) => {
                let log = format!("{var}/log/bko-holt");
                out.push(Cmd::new(&["mkdir", "-p", &log]));
                out.push(Cmd::new(&["cp", "-a", &format!("{RUN_DIR}/log/."), &format!("{log}/")]).why("this restore's log, on the restored system"));
                out.push(Cmd::new(&["python3", "-c", LABEL_PY, "security.selinux", &format!("{var}/log"), &log]).why("the log's SELinux label: /var/log's (every other file kept its own from the backup; no relabel)"));
            }
            None => self.problems.push("ostree: neither fstab's /var nor a stateroot in boot.ostree: nowhere to write the restore's log".into()),
        }
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

    pub(crate) fn kinoite() -> Value {
        crate::scenarios::dumps().into_iter().find(|(n, _)| *n == "kinoite_ostree").unwrap().1
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

    fn with(v: &Value, path: &[&str], x: Value) -> Value {
        let mut v = v.clone();
        set_at(&mut v, path, x);
        v
    }

    #[test]
    fn kinoite_plan() {
        // The scenario's Boot0001 names Chopin's ESP: here, the guest's.
        let v = with(&kinoite(), &["boot", "efi", "entries", "0", "partition"], crate::json::parse(r#"{"device": "vda1"}"#).unwrap());
        assert!(of(&v).is_some());
        assert_eq!(crate::other::restored_marker(&v), MARKER);
        let b = plan(&v, &["--repo", "/srv/repo"]);
        assert!(b.problems.is_empty(), "{:?}", b.problems);
        assert!(b.notes.iter().any(|n| n.contains("restored verbatim")), "{:?}", b.notes);
        assert!(!b.notes.iter().any(|n| n.contains("bootupd")), "{:?}", b.notes);
        // fstab's tree, / writable though fstab's has ro.
        let m = text(b.stage(Stage::Mount));
        let root = m.find("mount -t btrfs -o subvol=root,compress=zstd:1 /dev/vdb3 /mnt/bko-target\n").expect(&m);
        let boot = m.find("mount -t ext4 -o defaults /dev/vdb2 /mnt/bko-target/boot\n").expect(&m);
        let home = m.find("mount -t btrfs -o subvol=home,compress=zstd:1 /dev/vdb3 /mnt/bko-target/home\n").expect(&m);
        let var = m.find("mount -t btrfs -o subvol=var,compress=zstd:1 /dev/vdb3 /mnt/bko-target/var\n").expect(&m);
        assert!(m.contains("/dev/vdb1 /mnt/bko-target/boot/efi\n"), "{m}");
        assert!(root < boot && boot < home && home < var, "{m}");
        assert!(!m.contains(",ro"), "{m}");
        // Boot: nothing regenerated, nothing chrooted.
        let bt = text(b.stage(Stage::Boot));
        let run: String = bt.lines().filter(|l| !l.starts_with('#')).collect::<Vec<_>>().join("\n");
        for no in ["chroot", "dracut", "grub2-mkconfig", "grub2-install", "update-grub", "setfiles", "--rbind", "lvm-devices", "os-prober"] {
            assert!(!run.contains(no), "{no}: {bt}");
        }
        let pos = |s: &str| bt.find(s).unwrap_or_else(|| panic!("{s}: {bt}"));
        let listed = pos("atimes.before");
        let check = pos("/boot/loader/entries/ostree-*.conf");
        assert!(bt.contains("t=$(readlink -f /mnt/bko-target) || exit 1; want=loader.1;"), "{bt}");
        let nvram = pos("efibootmgr --quiet --create --disk /dev/vdb --part 1 --label");
        assert!(bt.contains("--label Fedora --loader '\\EFI\\fedora\\SHIMX64.EFI'\n"), "{bt}");
        let log = pos("mkdir -p /mnt/bko-target/var/log/bko-holt\n");
        let label = pos("security.selinux /mnt/bko-target/var/log /mnt/bko-target/var/log/bko-holt\n");
        let back = pos("atimes.after");
        assert!(listed < check && check < nvram && nvram < log && log < label && label < back, "{bt}");
        assert!(bt.contains("# ostree: /boot, the ESP and /ostree are restored verbatim"), "{bt}");
        // --no-nvram: no UEFI entries.
        assert!(!text(plan(&v, &["--no-nvram"]).stage(Stage::Boot)).contains("efibootmgr"));
    }

    /// Without fstab's /var, the log goes into the stateroot's var.
    #[test]
    fn log_in_the_stateroots_var() {
        let v = kinoite();
        let fstab: Vec<Value> = v.at(&["storage", "fstab"]).and_then(Value::as_array).unwrap().iter().filter(|e| e.get("file").and_then(Value::as_str) != Some("/var")).cloned().collect();
        let v = with(&v, &["storage", "fstab"], Value::Arr(fstab));
        let b = plan(&v, &[]);
        assert!(b.problems.is_empty(), "{:?}", b.problems);
        let bt = text(b.stage(Stage::Boot));
        assert!(bt.contains("mkdir -p /mnt/bko-target/ostree/deploy/default/var/log/bko-holt\n"), "{bt}");
        assert!(bt.contains("security.selinux /mnt/bko-target/ostree/deploy/default/var/log /mnt/bko-target/ostree/deploy/default/var/log/bko-holt\n"), "{bt}");
        let v = with(&v, &["boot", "ostree", "stateroot"], Value::Null);
        let v = with(&v, &["boot", "ostree", "deployments"], Value::Arr(vec![]));
        let b = plan(&v, &[]);
        assert!(b.problems.iter().any(|p| p.contains("nowhere to write the restore's log")), "{:?}", b.problems);
    }

    #[test]
    fn version_1_problems() {
        let v = kinoite();
        let problems = |v: &Value| plan(v, &[]).problems;
        let has = |v: &Value, s: &str| {
            let p = problems(v);
            assert!(p.iter().any(|x| x.contains(s)), "{s}: {p:?}");
        };
        let s = |x: &str| Value::Str(x.into());
        for m in ["verity", "signed"] {
            has(&with(&v, &["boot", "ostree", "prepare_root", "composefs"], s(m)), &format!("composefs enabled = {m}"));
        }
        for m in [Value::Str("maybe".into()), Value::Null] {
            let b = plan(&with(&v, &["boot", "ostree", "prepare_root", "composefs"], m), &[]);
            assert!(b.problems.is_empty() && b.notes.iter().any(|n| n.contains("enabled = maybe")), "{:?} {:?}", b.problems, b.notes);
        }
        let fsv = crate::json::parse(r#"{"required": "true"}"#).unwrap();
        has(&with(&v, &["boot", "ostree", "repo", "ex_fsverity"], fsv), "[ex-fsverity] required=true");
        let fsv = crate::json::parse(r#"{"required": "false"}"#).unwrap();
        let b = plan(&with(&v, &["boot", "ostree", "repo", "ex_fsverity"], fsv), &[]);
        assert!(b.problems.is_empty() && b.notes.iter().any(|n| n.contains("[ex-fsverity] section")), "{:?}", b.notes);
        let bios = with(&with(&v, &["firmware", "mode"], s("bios")), &["boot", "loader", "firmware"], s("bios"));
        has(&bios, "BIOS boot");
        has(&with(&v, &["storage", "crypttab"], crate::json::parse(r#"[{"name": "luks-x", "device": "UUID=x"}]"#).unwrap()), "LUKS");
        has(&with(&v, &["storage", "disks", "0", "partitions", "2", "content", "type"], s("crypto_LUKS")), "LUKS");
        has(&with(&v, &["storage", "disks", "0", "partitions", "2", "content", "type"], s("LVM2_member")), "LVM");
        let only = |keep: &dyn Fn(&str) -> bool| {
            let f: Vec<Value> = v.at(&["storage", "fstab"]).and_then(Value::as_array).unwrap().iter().filter(|e| keep(e.get("file").and_then(Value::as_str).unwrap())).cloned().collect();
            with(&v, &["storage", "fstab"], Value::Arr(f))
        };
        has(&only(&|f| f != "/boot"), "without a separate /boot");
        has(&only(&|f| f != "/"), "without `/` in fstab");
        let b = plan(&with(&v, &["boot", "ostree", "bootupd_state"], Value::Bool(false)), &[]);
        assert!(b.problems.is_empty() && b.notes.iter().any(|n| n.contains("bootupd-state.json")), "{:?}", b.notes);
        // --new-uuids stays the global problem it is.
        assert!(plan(&v, &["--new-uuids"]).problems.iter().any(|p| p.contains("--new-uuids")));
    }

    /// status: a restored ostree tree shows by its repository's config, as
    /// the physical root has no etc/fstab; the boot stage's gate is the same.
    #[test]
    fn restored_shows_by_the_repository() {
        use crate::load::status::{rows, State};
        use crate::sys::{FakeRunner, Sys};
        let v = kinoite();
        let l = Layout::from_json(v.clone()).unwrap();
        let t = crate::testutil::Tree::new("ostree-status");
        t.disk("vdb", "253:16", &[], 512).file("/sys/block/vdb/size", "201326592\n");
        t.file(
            "/proc/self/mountinfo",
            "36 35 253:19 /root /mnt/bko-target rw,relatime - btrfs /dev/vdb3 rw,subvol=/root\n37 36 253:18 / /mnt/bko-target/boot rw,relatime - ext4 /dev/vdb2 rw\n38 37 253:17 / /mnt/bko-target/boot/efi rw,relatime - vfat /dev/vdb1 rw\n39 36 253:19 /home /mnt/bko-target/home rw,relatime - btrfs /dev/vdb3 rw,subvol=/home\n40 36 253:19 /var /mnt/bko-target/var rw,relatime - btrfs /dev/vdb3 rw,subvol=/var\n",
        )
        .file("/proc/swaps", "Filename\tType\tSize\tUsed\tPriority\n");
        let s = Sys::new(t.root(), Box::new(FakeRunner::default()), true);
        let o = match crate::load::parse_args(&["l.json".into(), "--map".into(), "vda=vdb".into()]).unwrap() {
            crate::load::Parsed::Run(o) => o,
            _ => unreachable!(),
        };
        let restore = || rows(&l, &o, &s).0.into_iter().find(|r| r.0 == Stage::Restore).unwrap();
        let r = restore();
        assert_eq!(r.1, State::NotDone, "{r:?}");
        // An etc/fstab alone (the physical root has none) says nothing.
        t.file("/mnt/bko-target/etc/fstab", "").file("/mnt/bko-target/etc/os-release", "ID=fedora\n");
        assert_eq!(restore().1, State::NotDone);
        t.file(&format!("/mnt/bko-target/{MARKER}"), "[core]\nmode=bare\n");
        assert_eq!(restore().1, State::Done);
    }

    /// A restored kinoite44 tree under T, as the restore leaves it:
    /// /boot/loader -> loader.1 with ostree-1.conf (.0) and ostree-2.conf
    /// (.1, the default), the kernel and initramfs, the boot.1 chain.
    fn restored(t: &crate::testutil::TempDir) -> String {
        let root = t.path().to_str().unwrap().to_string();
        let c = "10e6a37fe91449ab0256dc707f50023505f76d29b6d34844b63843d0950f7de6";
        let b = "1cc099369334fad8952283b3221e6d78fd79263e6dedc35ce40302458681d413";
        let mk = |p: &str| std::fs::create_dir_all(format!("{root}/{p}")).unwrap();
        let file = |p: &str, x: &str| {
            let p = format!("{root}/{p}");
            std::fs::create_dir_all(std::path::Path::new(&p).parent().unwrap()).unwrap();
            std::fs::write(p, x).unwrap();
        };
        let link = |p: &str, to: &str| {
            let p = format!("{root}/{p}");
            std::fs::create_dir_all(std::path::Path::new(&p).parent().unwrap()).unwrap();
            std::os::unix::fs::symlink(to, p).unwrap();
        };
        for n in [0, 1] {
            mk(&format!("ostree/deploy/default/deploy/{c}.{n}/etc"));
            file(&format!("ostree/deploy/default/deploy/{c}.{n}.origin"), "[origin]\n");
        }
        file(MARKER, "[core]\nmode=bare\n");
        link("ostree/boot.1", "boot.1.1");
        link(&format!("ostree/boot.1.1/default/{b}/0"), &format!("../../../deploy/default/deploy/{c}.1"));
        link(&format!("ostree/boot.1.1/default/{b}/1"), &format!("../../../deploy/default/deploy/{c}.0"));
        link("boot/loader", "loader.1");
        let v = "7.3.3-200.fc44.x86_64";
        let bls = |ser: u32| format!("title Fedora\nversion {}\noptions ostree=/ostree/boot.1/default/{b}/{ser} root=UUID=x rw\nlinux /ostree/default-{b}/vmlinuz-{v}\ninitrd /ostree/default-{b}/initramfs-{v}.img\n", 2 - ser);
        file("boot/loader.1/entries/ostree-1.conf", &bls(1));
        file("boot/loader.1/entries/ostree-2.conf", &bls(0));
        file(&format!("boot/ostree/default-{b}/vmlinuz-{v}"), "k");
        file(&format!("boot/ostree/default-{b}/initramfs-{v}.img"), "i");
        root
    }

    fn sh(script: &str) -> (bool, String, String) {
        let out = std::process::Command::new("sh").args(["-c", script]).env("LC_ALL", "C").output().unwrap();
        (out.status.success(), String::from_utf8_lossy(&out.stdout).into_owned(), String::from_utf8_lossy(&out.stderr).into_owned())
    }

    /// The consistency check, run by sh on restored trees: whole, and with
    /// each thing it looks for broken.
    #[test]
    fn consistency_check_runs() {
        let c = "10e6a37fe91449ab0256dc707f50023505f76d29b6d34844b63843d0950f7de6";
        let b = "1cc099369334fad8952283b3221e6d78fd79263e6dedc35ce40302458681d413";
        let fresh = |name: &str| {
            let d = crate::testutil::TempDir::new(name);
            let r = restored(&d);
            (d, r)
        };
        let (_d, r) = fresh("ostree-check-ok");
        let (ok, out, err) = sh(&check_script(&r, Some("loader.1")));
        assert!(ok, "{out}{err}");
        assert_eq!(out, "ostree: 2 loader entries, each with its kernel, initramfs and deployment\n");
        assert!(err.is_empty(), "{err}");
        // The dump saw loader.0: a warning, not a failure.
        let (ok, _, err) = sh(&check_script(&r, Some("loader.0")));
        assert!(ok && err.contains("warning: /boot/loader is loader.1 here, loader.0 in the dump"), "{err}");
        // An orphan checkout: a warning.
        std::fs::create_dir_all(format!("{r}/ostree/deploy/default/deploy/{c}.2/etc")).unwrap();
        let (ok, _, err) = sh(&check_script(&r, None));
        assert!(ok && err.contains(&format!("warning: no loader entry leads to /ostree/deploy/default/deploy/{c}.2 (an orphan checkout")), "{err}");

        // A missing initramfs; a dangling ostree=; boot.N against loader.N.
        let (_d, r) = fresh("ostree-check-initrd");
        std::fs::remove_file(format!("{r}/boot/ostree/default-{b}/initramfs-7.3.3-200.fc44.x86_64.img")).unwrap();
        let (ok, _, err) = sh(&check_script(&r, None));
        assert!(!ok && err.contains(&format!("ostree-1.conf: /ostree/default-{b}/initramfs-7.3.3-200.fc44.x86_64.img isn't on the restored /boot")), "{err}");
        assert!(err.contains("ostree-2.conf: /ostree/default-"), "{err}");
        let (_d, r) = fresh("ostree-check-dangling");
        std::fs::remove_dir_all(format!("{r}/ostree/deploy/default/deploy/{c}.0")).unwrap();
        let (ok, _, err) = sh(&check_script(&r, None));
        assert!(!ok && err.contains(&format!("ostree-1.conf: ostree=/ostree/boot.1/default/{b}/1 leads nowhere in the restored tree")), "{err}");
        assert!(!err.contains("ostree-2.conf"), "{err}");
        let (_d, r) = fresh("ostree-check-version");
        std::fs::remove_file(format!("{r}/boot/loader")).unwrap();
        std::os::unix::fs::symlink("loader.0", format!("{r}/boot/loader")).unwrap();
        std::fs::rename(format!("{r}/boot/loader.1"), format!("{r}/boot/loader.0")).unwrap();
        let (ok, _, err) = sh(&check_script(&r, None));
        assert!(!ok && err.contains(&format!("ostree-2.conf: ostree=/ostree/boot.1/default/{b}/0, but /boot/loader is loader.0 (boot.0)")), "{err}");
        // An ostree= into a deployment, not to one; none at all; no entries.
        let (_d, r) = fresh("ostree-check-inside");
        let e = format!("{r}/boot/loader.1/entries/ostree-2.conf");
        let text = std::fs::read_to_string(&e).unwrap();
        std::fs::write(&e, text.replace(&format!("/{b}/0 "), &format!("/{b}/0/etc "))).unwrap();
        let (ok, _, err) = sh(&check_script(&r, None));
        assert!(!ok && err.contains(&format!("leads to /ostree/deploy/default/deploy/{c}.1/etc, inside a deployment")), "{err}");
        std::fs::write(&e, text.replace("options ostree=", "options x=")).unwrap();
        let (ok, _, err) = sh(&check_script(&r, None));
        assert!(!ok && err.contains("ostree-2.conf: no ostree= in its options"), "{err}");
        let (_d, r) = fresh("ostree-check-none");
        std::fs::remove_dir_all(format!("{r}/boot/loader.1/entries")).unwrap();
        let (ok, _, err) = sh(&check_script(&r, None));
        assert!(!ok && err.contains("no loader entries"), "{err}");
        assert!(err.contains("warning: no loader entry leads to"), "{err}");
        std::fs::remove_file(format!("{r}/boot/loader")).unwrap();
        let (ok, _, err) = sh(&check_script(&r, None));
        assert!(!ok && err.contains("/boot/loader isn't a symlink"), "{err}");
    }

    /// The labeller, with a user attribute in place of security.selinux
    /// (which needs root): copied onto the directory and all below it.
    #[test]
    fn label_copies_the_attribute() {
        let d = crate::testutil::TempDir::new("ostree-label");
        let r = d.path().to_str().unwrap();
        std::fs::create_dir_all(format!("{r}/var/log/bko-holt/sub")).unwrap();
        std::fs::write(format!("{r}/var/log/bko-holt/load.log"), "x").unwrap();
        std::fs::write(format!("{r}/var/log/bko-holt/sub/f"), "x").unwrap();
        let top = format!("{r}/var/log/bko-holt");
        let log = format!("{r}/var/log");
        let py = |src: &str| std::process::Command::new("python3").args(["-c", LABEL_PY, "user.bko_test", src, top.as_str()]).output().unwrap();
        // No attribute on the source: nothing done, and no failure.
        let out = py(&log);
        assert!(out.status.success(), "{}", String::from_utf8_lossy(&out.stderr));
        let set = std::process::Command::new("python3").args(["-c", "import os, sys; os.setxattr(sys.argv[1], 'user.bko_test', b'system_u:object_r:var_log_t:s0')", log.as_str()]).output().unwrap();
        if !set.status.success() {
            eprintln!("SKIP: no user xattrs here: {}", String::from_utf8_lossy(&set.stderr));
            return;
        }
        let out = py(&log);
        assert!(out.status.success(), "{}", String::from_utf8_lossy(&out.stderr));
        let paths: Vec<String> = ["", "/load.log", "/sub", "/sub/f"].iter().map(|x| format!("{top}{x}")).collect();
        let get = std::process::Command::new("python3")
            .arg("-c")
            .arg("import os, sys\nfor p in sys.argv[1:]: print(os.getxattr(p, 'user.bko_test').decode())")
            .args(&paths)
            .output()
            .unwrap();
        assert_eq!(String::from_utf8_lossy(&get.stdout), "system_u:object_r:var_log_t:s0\n".repeat(4), "{}", String::from_utf8_lossy(&get.stderr));
    }
}
