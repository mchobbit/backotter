//! `load` for Ubuntu Core 24 (docs/design/ubuntu-core.md).  The partition
//! stage makes the pc gadget's table as the dump has it (GPT names, type
//! GUIDs, PARTUUIDs, sizes, attributes: only ubuntu-data may grow, under
//! --fit-largest); format keeps every label and UUID and the seed's vfat
//! volume ID; an encrypted save and data get their kept LUKS2 headers back
//! (commands.rs's keep_header arm, crate::luksheader).  The mount stage
//! mounts the four partitions at snap-bootstrap's run-mode mount points,
//! the backup's tree: there is no `/` and no fstab.
//!
//! The boot stage regenerates nothing: no grub-install, no update-grub, no
//! initramfs (each kernel's kernel.efi is restored on ubuntu-boot), and no
//! NVRAM entry: shim's fallback in the restored seed makes `ubuntu` on the
//! first boot (U1).  It checks that the restored tree boots (modeenv's
//! kernels and recovery systems are there), writes the gadget's MBR code
//! and BIOS Boot core.img back, and with --new-machine points the seed's
//! grubenv at recover mode (Decision 2b).

use super::commands::{Builder, Cmd};
use super::RUN_DIR;
use crate::json::Value;
use crate::snapcore::ROLES;

/// What a restored Ubuntu Core tree has once the restore is done.
pub const MARKER: &str = "run/mnt/data/system-data/var/lib/snapd/modeenv";
/// Where the boot stage's log goes in the restored tree (/var/log).
pub const LOG: &str = "run/mnt/data/system-data/var/log/bko-holt";

/// The layout's boot.snapcore, when the source is Ubuntu Core.
pub fn of(layout: &Value) -> Option<&Value> {
    layout.at(&["boot", "snapcore"]).filter(|v| v.as_object().is_some())
}

fn s(v: &Value, k: &str) -> Option<String> {
    v.get(k).and_then(Value::as_str).map(str::to_string)
}

fn strs(v: Option<&Value>) -> Vec<String> {
    v.and_then(Value::as_array).into_iter().flatten().filter_map(Value::as_str).map(str::to_string).collect()
}

/// boot.snapcore's partition of ROLE.
pub fn part<'a>(sc: &'a Value, r: &str) -> Option<&'a Value> {
    sc.get("partitions").and_then(Value::as_array)?.iter().find(|p| p.get("role").and_then(Value::as_str) == Some(r))
}

/// The recovery system --new-machine boots recover mode of, and why.
///
/// U1 found that recover mode of the image's system (`20240603`, its kernel
/// pc-kernel_1803) asks for the recovery key on a new TPM and unlocks the
/// data, and that of a system made later by snapd 2.78 (`bko-20261114`,
/// pc-kernel_3908) doesn't.  U3 found why (docs/design/ubuntu-core.md,
/// "What U2/U3 found"): recover mode runs the recovery system's own kernel,
/// whose initramfs holds snap-bootstrap.  The newer one (secboot's
/// ActivateContext) tries a recovery key only against keyslots its LUKS2
/// tokens type as recovery keyslots, and a header made by the install's
/// older snapd has no tokens: it boots on with ubuntu-data locked.  The
/// install's own system's kernel made those keyslots and opens them.  So
/// the system chosen is the first good one (modeenv's good_recovery_systems,
/// in the order snapd made them, with its model on the seed): the one the
/// device was installed from.
pub fn recover_system(sc: &Value) -> Result<(String, String), String> {
    let good = strs(sc.at(&["modeenv", "good_recovery_systems"]));
    let systems: Vec<&Value> = sc.at(&["seed", "systems"]).and_then(Value::as_array).into_iter().flatten().collect();
    let sys_of = |l: &str| systems.iter().find(|x| x.get("label").and_then(Value::as_str) == Some(l)).copied();
    let usable: Vec<(&String, &Value)> = good.iter().filter_map(|l| sys_of(l).filter(|x| x.get("model") == Some(&Value::Bool(true))).map(|x| (l, x))).collect();
    let Some((first, fx)) = usable.first() else {
        return Err("Ubuntu Core: --new-machine needs a good recovery system on the seed (modeenv's good_recovery_systems, with its model), and the dump has none".into());
    };
    let why = format!(
        "{first}, the first good recovery system{}: its kernel's initramfs is the one that made the LUKS2 keyslots, and asks for the recovery key in recover mode on a new TPM (a system made later by a newer snapd boots recover mode with ubuntu-data locked: its snap-bootstrap tries the recovery key only on keyslots with LUKS2 tokens)",
        s(fx, "kernel").map(|k| format!(" (kernel {})", k.trim_start_matches("/snaps/"))).unwrap_or_default()
    );
    Ok(((*first).clone(), why))
}

/// The check of a restored tree at T, as a shell script: modeenv says
/// mode=run; each of its current_kernels has EFI/ubuntu/K/kernel.efi on
/// ubuntu-boot; EFI/ubuntu/kernel.efi (the run-mode kernel, a symlink
/// since UC20) points at one of them, and the run grubenv's snap_kernel,
/// when set, names one; no kernel is being tried; each of
/// current_recovery_systems has systems/LABEL/model on ubuntu-seed;
/// modeenv's base and gadget are in system-data/var/lib/snapd/snaps; the
/// model is on ubuntu-boot.  A mismatch fails the stage.  WANT_KERNELS and
/// WANT_SYSTEMS are the dump's: a restored tree that differs (a backup
/// taken across a refresh) is a warning, since the tree's own files are
/// what boots.
pub fn check_script(t: &str, want_kernels: &str, want_systems: &str) -> String {
    let q = super::shell_quote;
    format!(
        r#"t=$(readlink -f {t}) || exit 1; wk={wk}; ws={ws}; errs=; nbad=0
err() {{ errs="${{errs}}snapcore: $*
"; nbad=$((nbad + 1)); }}
seed=$t/run/mnt/ubuntu-seed; boot=$t/run/mnt/ubuntu-boot; data=$t/run/mnt/data
m=$data/system-data/var/lib/snapd/modeenv
[ -f "$m" ] || {{ echo "snapcore: system-data/var/lib/snapd/modeenv isn't on the restored ubuntu-data" >&2; exit 1; }}
v() {{ sed -n "s/^$1=//p" "$m" | tail -n 1; }}
mode=$(v mode); [ "$mode" = run ] || err "modeenv says mode=$mode, not run"
ks=$(v current_kernels | tr , ' '); rs=$(v current_recovery_systems | tr , ' ')
[ -n "$ks" ] || err "modeenv names no current_kernels"
for k in $ks; do [ -f "$boot/EFI/ubuntu/$k/kernel.efi" ] || err "modeenv's kernel $k has no EFI/ubuntu/$k/kernel.efi on the restored ubuntu-boot"; done
l=$(readlink "$boot/EFI/ubuntu/kernel.efi" 2>/dev/null); rk=${{l%%/*}}
if [ -z "$l" ]; then
  err "ubuntu-boot has no EFI/ubuntu/kernel.efi symlink (the run-mode kernel)"
else
  case " $ks " in *" $rk "*) [ -f "$boot/EFI/ubuntu/$l" ] || err "EFI/ubuntu/kernel.efi points at $l, which isn't there" ;; *) err "EFI/ubuntu/kernel.efi points at $l, not at one of modeenv's current_kernels ($ks)" ;; esac
fi
g=$boot/EFI/ubuntu/grubenv
sk=$(sed -n 's/^snap_kernel=//p' "$g" 2>/dev/null | tail -n 1)
[ -z "$sk" ] || case " $ks " in *" $sk "*) ;; *) err "the run grubenv's snap_kernel=$sk isn't one of modeenv's current_kernels ($ks)" ;; esac
st=$(sed -n 's/^kernel_status=//p' "$g" 2>/dev/null | tail -n 1)
[ -z "$st" ] || err "the run grubenv says kernel_status=$st: a kernel refresh was being tried"
[ -n "$rs" ] || err "modeenv names no current_recovery_systems"
for r in $rs; do [ -f "$seed/systems/$r/model" ] || err "recovery system $r has no systems/$r/model on the restored ubuntu-seed"; done
for k in base gadget; do x=$(v $k); [ -z "$x" ] || [ -f "$data/system-data/var/lib/snapd/snaps/$x" ] || err "modeenv's $k $x isn't in system-data/var/lib/snapd/snaps on the restored ubuntu-data"; done
[ -f "$boot/device/model" ] || err "ubuntu-boot has no device/model"
[ "$ks" = "$wk" ] || echo "snapcore: warning: the restored modeenv's kernels ($ks) aren't the dump's ($wk): the backup was taken after a refresh; the restored tree is what boots" >&2
[ "$rs" = "$ws" ] || echo "snapcore: warning: the restored recovery systems ($rs) aren't the dump's ($ws)" >&2
[ -z "$errs" ] || {{ printf %s "$errs" >&2; echo "snapcore: $nbad mismatches between modeenv and the restored ubuntu-boot, ubuntu-seed and ubuntu-data" >&2; exit 1; }}
echo "snapcore: run mode boots $rk; recovery systems $rs"
"#,
        t = q(t),
        wk = q(want_kernels),
        ws = q(want_systems),
    )
}

impl Builder<'_> {
    /// The four partitions at snap-bootstrap's mount points: the restored
    /// system's tree, which no fstab line mounts.
    pub(crate) fn snapcore_mounts(&mut self, sc: &Value) -> Vec<(String, Cmd)> {
        let t = self.opts.target.clone();
        let mut out = Vec::new();
        for r in ROLES {
            let Some(p) = part(sc, r.name) else {
                self.problems.push(format!("Ubuntu Core: the dump has no {} partition", r.partlabel));
                continue;
            };
            let src = if p.get("encrypted") == Some(&Value::Bool(true)) { p.at(&["luks", "dm_device"]).and_then(Value::as_str).map(str::to_string) } else { s(p, "device") };
            let (Some(src), Some(ty)) = (src, s(p, "fstype").filter(|x| !x.is_empty())) else {
                self.problems.push(format!("Ubuntu Core: the dump doesn't say which filesystem {} holds", r.partlabel));
                continue;
            };
            let Some(node) = self.names.dev(&src) else {
                self.problems.push(format!("Ubuntu Core: {} is on {src}, which isn't restored", r.partlabel));
                continue;
            };
            out.push((r.at.to_string(), Cmd::new(&["mount", "-t", &ty, "-o", "defaults", &node, &format!("{t}{}", r.at)]).why(format!("Ubuntu Core: {}, at snap-bootstrap's mount point", r.partlabel))));
        }
        out
    }

    /// What version 1 doesn't restore on Ubuntu Core (plan problems), and
    /// what the plan should say (notes).
    fn snapcore_problems(&mut self, sc: &Value) {
        for r in strs(sc.get("refusals")) {
            self.problems.push(format!("the dump refused this system: {r}"));
        }
        if self.opts.new_uuids {
            self.problems.push("Ubuntu Core with --new-uuids isn't restored: snap-bootstrap finds the partitions by label and name, the sealed-key objects and the kept LUKS2 headers name their volumes".into());
        }
        let m = |k: &str| sc.at(&["model", k]).and_then(Value::as_str).map(str::to_string);
        if m("gadget").as_deref() != Some("pc") || m("architecture").as_deref() != Some("amd64") || m("base").as_deref() != Some("core24") {
            self.problems.push(format!(
                "Ubuntu Core: version 1 restores Ubuntu Core 24 on the amd64 pc gadget only; this model has gadget {}, architecture {}, base {}",
                m("gadget").unwrap_or("?".into()),
                m("architecture").unwrap_or("?".into()),
                m("base").unwrap_or("?".into())
            ));
        }
        if sc.at(&["modeenv", "mode"]).and_then(Value::as_str) != Some("run") {
            self.problems.push(format!("Ubuntu Core: the dump was made in {} mode, not run mode", sc.at(&["modeenv", "mode"]).and_then(Value::as_str).unwrap_or("an unknown")));
        }
        if let Some(k) = sc.at(&["boot", "kernel_status"]).and_then(Value::as_str).filter(|k| matches!(*k, "try" | "trying")) {
            self.problems.push(format!("Ubuntu Core: a kernel refresh was being tried when the dump was made (kernel_status={k}): dump and back up again once it has finished"));
        }
        // The gadget's geometry: only ubuntu-data may change size (the fit).
        for r in ROLES {
            let Some(p) = part(sc, r.name) else {
                self.problems.push(format!("Ubuntu Core: the dump has no {} partition", r.partlabel));
                continue;
            };
            let dev = s(p, "device").unwrap_or_default();
            let was = p.get("size_bytes").and_then(Value::as_u64);
            let now = self.layout.device(&dev).and_then(|v| v.get("size_bytes")).and_then(Value::as_u64);
            if r.name != "data" && was.is_some() && now != was {
                self.problems.push(format!("Ubuntu Core: {} ({dev}) would be {} instead of the gadget's {}: only ubuntu-data may change size (--fit-largest)", r.partlabel, now.map(crate::report::human).unwrap_or("?".into()), was.map(crate::report::human).unwrap_or_default()));
            }
            if p.get("encrypted") == Some(&Value::Bool(true)) && crate::luksheader::kept(&self.layout.v, &dev).is_none() {
                self.problems.push(format!(
                    "Ubuntu Core: the dump kept no LUKS2 header of {} ({dev}): a new one would have a new volume key, which neither the TPM-sealed keys nor the recovery key open; dump again as root with cryptsetup at hand",
                    r.partlabel
                ));
            }
        }
        let encrypted = sc.get("encrypted") == Some(&Value::Bool(true));
        if encrypted {
            self.notes.push("Ubuntu Core: the LUKS2 header files the dump kept (by default in /run/bko-holt/luks-headers, RAM) must be kept with the dump: load reads them beside the layout, or in --luks-header-dir".into());
        }
        if self.opts.new_machine && !encrypted {
            self.problems.push("--new-machine is for an encrypted Ubuntu Core: this one isn't, and boots on any machine as it is".into());
        }
        if encrypted && self.opts.new_machine {
            match recover_system(sc) {
                Ok((_, why)) => self.notes.push(format!(
                    "Ubuntu Core, --new-machine: the restored seed's grubenv boots recover mode of {why}. On the first boot type the recovery key at the prompt on the screen; the data is then readable. A normal (run mode) boot on this machine needs a reinstall of Ubuntu Core: Ubuntu Core 24 doesn't re-provision onto another TPM, and bko-holt doesn't reseal (docs/design/ubuntu-core.md, Decision 2b)"
                )),
                Err(e) => self.problems.push(e),
            }
        } else if encrypted {
            self.notes.push("Ubuntu Core with TPM-sealed FDE: both LUKS2 headers come back byte for byte. On the same machine (the same TPM and Secure Boot variables, dbx included) it boots unattended only if nothing was resealed since the backup: a kernel, gadget or snapd refresh or a new recovery system after it reseals the keys and advances the TPM's policy counter, which revokes the restored sealed keys. On another TPM, after a firmware or dbx reset, or after such a reseal, run mode can't unlock and reboots in a loop: load with --new-machine, whose first boot is recover mode".into());
        } else {
            self.notes.push("Ubuntu Core (unencrypted): restored file for file; it boots unattended on any machine by UEFI".into());
        }
        if sc.at(&["boot_areas", "bios_boot", "image_bytes"]).and_then(Value::as_u64) == Some(0) {
            self.notes.push("Ubuntu Core: the gadget's BIOS Boot partition is empty (the pc gadget of UC24 boots by UEFI only; its MBR code says \"Please boot in EFI mode\"), so the restored disk boots by UEFI only, as the source did".into());
        }
        if self.opts.no_nvram || self.opts.removable {
            self.notes.push("Ubuntu Core: --no-nvram and --removable change nothing: load makes no UEFI entry (shim's fallback on the restored seed makes it at the first boot), and the seed's EFI/boot/bootx64.efi is the removable path already".into());
        }
        self.notes.push("Ubuntu Core finds its partitions by label: boot the restored disk with the source's disk (or any other Ubuntu Core disk) detached".into());
    }

    /// The boot stage on Ubuntu Core (docs/design/ubuntu-core.md, "load").
    pub(crate) fn snapcore_boot(&mut self, sc: &Value) -> Vec<Cmd> {
        self.snapcore_problems(sc);
        let t = self.opts.target.clone();
        let mut out = vec![Cmd::note(format!("refuses to start unless {t}/{MARKER} is there: the restored ubuntu-data"))];
        out.push(Cmd::new(&["sh", "-c", &self.atimes(&t, "if-missing")]).why("the restored atimes (the restore stage lists them), to put back after this stage's reads"));
        let what = "Ubuntu Core: ubuntu-seed, ubuntu-boot, ubuntu-save and ubuntu-data are restored as they were; no grub-install, no update-grub, no initramfs, no chroot, and no UEFI entry (shim's fallback on the restored seed makes it at the first boot)";
        self.notes.push(what.into());
        out.push(Cmd::note(what));
        let wk = strs(sc.at(&["modeenv", "current_kernels"])).join(" ");
        let ws = strs(sc.at(&["modeenv", "current_recovery_systems"])).join(" ");
        out.push(Cmd::new(&["sh", "-c", &check_script(&t, &wk, &ws)]).why("modeenv's kernels and recovery systems are on the restored ubuntu-boot and ubuntu-seed, and the run-mode kernel is one of them").warns());
        // The gadget's MBR code and BIOS Boot core.img, byte for byte.
        if let Some(a) = sc.get("boot_areas").filter(|a| !a.is_null()) {
            let disk = s(a, "disk").unwrap_or_default();
            match self.names.mapping.target_of(&disk) {
                Some(tgt) => {
                    let exe = std::env::current_exe().map(|p| p.to_string_lossy().into_owned()).unwrap_or_else(|_| "bko-holt".into());
                    out.push(Cmd::new(&["sh", "-c", &format!("{exe} snapcore-boot-areas {} {disk} /dev/{tgt}", super::shell_quote(&self.opts.layout))]).why(format!(
                        "{disk} -> /dev/{tgt}: the gadget's MBR code, and BIOS Boot's core.img when it has one, byte for byte (UEFI boots shim from the seed)"
                    )));
                }
                None => self.problems.push(format!("Ubuntu Core: {disk}, the disk with the gadget's partitions, isn't mapped to a target")),
            }
        } else {
            self.notes.push("Ubuntu Core: the dump has neither the MBR's boot code nor BIOS Boot's core.img, so the restored disk boots by UEFI only".into());
        }
        if self.opts.new_machine && sc.get("encrypted") == Some(&Value::Bool(true)) {
            if let Ok((label, _)) = recover_system(sc) {
                out.push(
                    Cmd::new(&["grub-editenv", &format!("{t}/run/mnt/ubuntu-seed/EFI/ubuntu/grubenv"), "set", "snapd_recovery_mode=recover", &format!("snapd_recovery_system={label}")])
                        .why(format!("--new-machine: the first boot is recover mode of {label}, which asks for the recovery key (what `snap reboot --recover {label}` writes)")),
                );
            }
        }
        let log = format!("{t}/{LOG}");
        out.push(Cmd::new(&["mkdir", "-p", &log]));
        out.push(Cmd::new(&["cp", "-a", &format!("{RUN_DIR}/log/."), &format!("{log}/")]).why("this restore's log, on the restored system (/var/log is ubuntu-data's system-data/var/log)"));
        out.extend(self.boot_tail(&t));
        out
    }
}

/// `bko-holt snapcore-boot-areas LAYOUT DISK DEVICE` (not in the usage: run
/// by load's boot stage): writes boot.snapcore.boot_areas back on DEVICE,
/// the target of DISK: the MBR's first 440 bytes (GRUB's boot.img; the
/// partition table after them is left alone), and the BIOS Boot
/// partition's core.img at that partition's start, each checked against
/// its SHA-256 in the dump first.
pub fn boot_areas_main(args: &[String]) -> u8 {
    let [layout, disk, dev] = args else {
        eprintln!("usage: bko-holt snapcore-boot-areas LAYOUT DISK DEVICE");
        return 2;
    };
    let run = || -> Result<String, String> {
        let text = std::fs::read_to_string(layout).map_err(|e| format!("{layout}: {e}"))?;
        let v = crate::json::parse(&text).map_err(|e| format!("{layout}: {e:?}"))?;
        let writes = boot_area_writes(&v, disk)?;
        let f = std::fs::OpenOptions::new().write(true).open(dev).map_err(|e| format!("{dev}: {e}"))?;
        let mut said = Vec::new();
        for (off, b, what) in &writes {
            use std::os::unix::fs::FileExt;
            f.write_all_at(b, *off).map_err(|e| format!("{dev}: writing {} bytes at {off}: {e}", b.len()))?;
            said.push(format!("{what} ({} bytes at {off})", b.len()));
        }
        f.sync_all().map_err(|e| format!("{dev}: {e}"))?;
        Ok(said.join(", "))
    };
    match run() {
        Ok(s) if s.is_empty() => {
            println!("snapcore-boot-areas: nothing to write");
            0
        }
        Ok(s) => {
            println!("snapcore-boot-areas: wrote {s} to {dev}");
            0
        }
        Err(e) => {
            eprintln!("bko-holt snapcore-boot-areas: {e}");
            1
        }
    }
}

/// One write snapcore-boot-areas makes: (offset, bytes, what).
pub type Write = (u64, Vec<u8>, &'static str);

/// The writes snapcore-boot-areas makes.  An empty BIOS Boot (the UC24 pc
/// gadget's: it is UEFI only, and its MBR code says "Please boot in EFI
/// mode") writes nothing there.
pub fn boot_area_writes(v: &Value, disk: &str) -> Result<Vec<Write>, String> {
    let a = of(v).and_then(|sc| sc.get("boot_areas")).filter(|a| !a.is_null()).ok_or("the layout has no boot.snapcore.boot_areas")?;
    if a.get("disk").and_then(Value::as_str) != Some(disk) {
        return Err(format!("the layout's boot areas are {}'s, not {disk}'s", a.get("disk").and_then(Value::as_str).unwrap_or("no disk")));
    }
    let bytes = |b64: Option<&Value>, sha: Option<&Value>, what: &str| -> Result<Option<Vec<u8>>, String> {
        let Some(t) = b64.and_then(Value::as_str) else { return Ok(None) };
        let b = crate::bytes::unbase64(t).ok_or(format!("{what}: not base64"))?;
        if sha.and_then(Value::as_str) != Some(crate::bytes::sha256_hex(&b).as_str()) {
            return Err(format!("{what}: its bytes aren't the dump's (SHA-256)"));
        }
        Ok(Some(b))
    };
    let mut out = Vec::new();
    if let Some(code) = bytes(a.get("mbr_code_b64"), a.get("mbr_code_sha256"), "the MBR's boot code")? {
        if code.len() > 440 {
            return Err("the MBR's boot code is over 440 bytes".into());
        }
        out.push((0, code, "the MBR's boot code"));
    }
    if let Some(b) = a.get("bios_boot").filter(|b| !b.is_null()) {
        if let Some(img) = bytes(b.get("image_b64"), b.get("sha256"), "BIOS Boot's core.img")?.filter(|i| !i.is_empty()) {
            let start = b.get("start_bytes").and_then(Value::as_u64).ok_or("BIOS Boot has no start")?;
            let size = b.get("size_bytes").and_then(Value::as_u64).unwrap_or(0);
            if img.len() as u64 > size || start < 512 {
                return Err("BIOS Boot's core.img doesn't fit where it was".into());
            }
            out.push((start, img, "BIOS Boot's core.img"));
        }
    }
    Ok(out)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::testutil::Tree;

    fn run(script: &str) -> (bool, String, String) {
        let o = std::process::Command::new("sh").arg("-c").arg(script).output().unwrap();
        (o.status.success(), String::from_utf8_lossy(&o.stdout).into_owned(), String::from_utf8_lossy(&o.stderr).into_owned())
    }

    /// A restored tree as uc24-fde's: one kernel, two recovery systems.
    fn tree() -> Tree {
        let t = Tree::new("snapcore-check");
        let m = "/run/mnt/data/system-data/var/lib/snapd";
        t.file(&format!("{m}/modeenv"), crate::snapcore::tests::MODEENV_TEXT)
            .file(&format!("{m}/snaps/core24_2307.snap"), "s")
            .file(&format!("{m}/snaps/pc_196.snap"), "s")
            .file("/run/mnt/ubuntu-boot/EFI/ubuntu/pc-kernel_3908.snap/kernel.efi", "k")
            .file("/run/mnt/ubuntu-boot/EFI/ubuntu/pc-kernel_1803.snap/kernel.efi", "k")
            .symlink("/run/mnt/ubuntu-boot/EFI/ubuntu/kernel.efi", "pc-kernel_3908.snap/kernel.efi")
            .file("/run/mnt/ubuntu-boot/EFI/ubuntu/grubenv", crate::snapcore::tests::grubenv(&["kernel_status=", "snapd_extra_cmdline_args="]))
            .file("/run/mnt/ubuntu-boot/device/model", crate::snapcore::tests::MODEL_TEXT)
            .file("/run/mnt/ubuntu-seed/systems/20240603/model", "m")
            .file("/run/mnt/ubuntu-seed/systems/bko-20261114/model", "m");
        t
    }

    #[test]
    fn check_passes_and_fails() {
        let root = |t: &Tree| t.root().to_string_lossy().into_owned();
        let t = tree();
        let (ok, out, err) = run(&check_script(&root(&t), "pc-kernel_3908.snap", "20240603 bko-20261114"));
        assert!(ok && err.is_empty(), "{out}{err}");
        assert!(out.contains("run mode boots pc-kernel_3908.snap; recovery systems 20240603 bko-20261114"), "{out}");
        // A backup after a refresh: warned, not failed.
        let (ok, _, err) = run(&check_script(&root(&t), "pc-kernel_1803.snap", "20240603"));
        assert!(ok && err.contains("warning: the restored modeenv's kernels") && err.contains("warning: the restored recovery systems"), "{err}");
        // Five things wrong at once.
        std::fs::remove_file(t.p("/run/mnt/ubuntu-seed/systems/bko-20261114/model")).unwrap();
        std::fs::remove_file(t.p("/run/mnt/data/system-data/var/lib/snapd/snaps/core24_2307.snap")).unwrap();
        t.symlink("/run/mnt/ubuntu-boot/EFI/ubuntu/kernel.efi", "pc-kernel_1803.snap/kernel.efi");
        t.file("/run/mnt/ubuntu-boot/EFI/ubuntu/grubenv", crate::snapcore::tests::grubenv(&["kernel_status=try", "snap_kernel=pc-kernel_1.snap"]));
        let (ok, _, err) = run(&check_script(&root(&t), "pc-kernel_3908.snap", "20240603 bko-20261114"));
        assert!(!ok, "{err}");
        for want in [
            "recovery system bko-20261114 has no systems/bko-20261114/model on the restored ubuntu-seed",
            "modeenv's base core24_2307.snap isn't in system-data/var/lib/snapd/snaps",
            "EFI/ubuntu/kernel.efi points at pc-kernel_1803.snap/kernel.efi, not at one of modeenv's current_kernels (pc-kernel_3908.snap)",
            "the run grubenv's snap_kernel=pc-kernel_1.snap isn't one of modeenv's current_kernels",
            "the run grubenv says kernel_status=try",
        ] {
            assert!(err.contains(want), "{want}: {err}");
        }
        assert!(err.trim_end().ends_with("snapcore: 5 mismatches between modeenv and the restored ubuntu-boot, ubuntu-seed and ubuntu-data"), "{err}");
        // No kernel.efi at all, no modeenv at all.
        std::fs::remove_file(t.p("/run/mnt/ubuntu-boot/EFI/ubuntu/kernel.efi")).unwrap();
        let (ok, _, err) = run(&check_script(&root(&t), "", ""));
        assert!(!ok && err.contains("ubuntu-boot has no EFI/ubuntu/kernel.efi symlink"), "{err}");
        std::fs::remove_file(t.p("/run/mnt/data/system-data/var/lib/snapd/modeenv")).unwrap();
        let (ok, _, err) = run(&check_script(&root(&t), "", ""));
        assert!(!ok && err.contains("modeenv isn't on the restored ubuntu-data"), "{err}");
    }

    /// Ubuntu Core's atimes list covers run/mnt (its whole tree), and
    /// still leaves the rest of run/ out.
    #[test]
    fn atimes_cover_run_mnt() {
        let t = Tree::new("snapcore-atimes");
        t.file("/run/mnt/data/system-data/var/lib/snapd/modeenv", "m").file("/run/other/x", "x").file("/etc/x", "x");
        let root = t.root().to_string_lossy().into_owned();
        let list = |run_mnt: bool| {
            let cmd = super::super::commands::atimes_cmd_in(&root, "before", &[], run_mnt).replace("/run/bko-holt/atimes.before", &format!("{root}/list")).replace("mkdir -p /run/bko-holt && ", "");
            assert!(run(&cmd).0, "{cmd}");
            std::fs::read(t.p("/list")).unwrap().split(|&b| b == 0).map(|e| String::from_utf8_lossy(e).into_owned()).collect::<Vec<_>>().join("\n")
        };
        let l = list(true);
        assert!(l.contains("/run/mnt/data/system-data/var/lib/snapd/modeenv") && l.contains("/etc/x") && !l.contains("/run/other"), "{l}");
        let l = list(false);
        assert!(!l.contains("/run/mnt") && l.contains("/etc/x"), "{l}");
    }

    fn sc(json: &str) -> Value {
        crate::json::parse(json).unwrap()
    }

    #[test]
    fn recover_system_choice() {
        let v = sc(r#"{"modeenv": {"good_recovery_systems": ["20240603", "bko-20261114"]}, "seed": {"systems": [
            {"label": "20240603", "model": true, "kernel": "/snaps/pc-kernel_1803.snap"},
            {"label": "bko-20261114", "model": true, "kernel": "/snaps/pc-kernel_3908.snap"}]}}"#);
        let (l, why) = recover_system(&v).unwrap();
        assert_eq!(l, "20240603");
        assert!(why.contains("(kernel pc-kernel_1803.snap)"), "{why}");
        // The first good one without its model is passed over.
        let v = sc(r#"{"modeenv": {"good_recovery_systems": ["20240603", "bko-20261114"]}, "seed": {"systems": [
            {"label": "20240603", "model": false, "kernel": "/snaps/pc-kernel_1803.snap"},
            {"label": "bko-20261114", "model": true, "kernel": "/snaps/pc-kernel_3908.snap"}]}}"#);
        assert_eq!(recover_system(&v).unwrap().0, "bko-20261114");
        assert!(recover_system(&sc(r#"{"modeenv": {"good_recovery_systems": []}, "seed": {"systems": []}}"#)).unwrap_err().contains("no"));
    }

    #[test]
    fn boot_areas() {
        let code = vec![0xebu8; 440];
        let core = vec![0x52u8; 1024];
        let v = sc(&format!(
            r#"{{"boot": {{"snapcore": {{"boot_areas": {{"disk": "vda", "mbr_code_b64": "{}", "mbr_code_sha256": "{}", "bios_boot": {{"partition": "vda1", "start_bytes": 1048576, "size_bytes": 1048576, "image_b64": "{}", "sha256": "{}"}}}}}}}}}}"#,
            crate::bytes::base64(&code),
            crate::bytes::sha256_hex(&code),
            crate::bytes::base64(&core),
            crate::bytes::sha256_hex(&core)
        ));
        let w = boot_area_writes(&v, "vda").unwrap();
        assert_eq!(w.iter().map(|(o, b, _)| (*o, b.len())).collect::<Vec<_>>(), vec![(0, 440), (1048576, 1024)]);
        assert!(boot_area_writes(&v, "vdb").unwrap_err().contains("vda's, not vdb's"));
        // UC24's empty BIOS Boot: the MBR code only.
        let empty = crate::json::parse(&v.compact().replace(&crate::bytes::base64(&core), "").replace(&crate::bytes::sha256_hex(&core), &crate::bytes::sha256_hex(&[]))).unwrap();
        assert_eq!(boot_area_writes(&empty, "vda").unwrap().len(), 1);
        let bad = crate::json::parse(&v.compact().replace(&crate::bytes::sha256_hex(&core), &"0".repeat(64))).unwrap();
        assert!(boot_area_writes(&bad, "vda").unwrap_err().contains("core.img: its bytes aren't the dump's"));
        // Written to a file standing in for the target disk.
        let t = Tree::new("snapcore-areas");
        t.file("/layout.json", v.compact()).file("/disk", vec![0u8; 3 << 20]);
        let p = |x: &str| t.p(x).to_string_lossy().into_owned();
        assert_eq!(boot_areas_main(&[p("/layout.json"), "vda".into(), p("/disk")]), 0);
        let d = std::fs::read(t.p("/disk")).unwrap();
        assert!(d[..440] == code[..] && d[440..1048576].iter().all(|&b| b == 0) && d[1048576..1048576 + 1024] == core[..] && d[1048576 + 1024] == 0);
    }
}
