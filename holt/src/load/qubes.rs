//! Qubes OS in load's boot stage (docs/design/qubes.md, "The design"):
//! GRUB 2 on UEFI without shim, booting Xen and dom0 through 20_linux_xen.
//! GRUB's EFI image (\EFI\qubes\grubx64.efi, and the fallback
//! EFI/BOOT/BOOTX64.EFI) comes back with the ESP; only grub.cfg is remade,
//! by grub2-mkconfig in the chroot, with /etc/default/grub's
//! GRUB_CMDLINE_XEN_DEFAULT and GRUB_CMDLINE_LINUX as restored (the LUKS
//! UUID that rd.luks.uuid= names is kept by load, so nothing is rewritten);
//! dracut remakes the initramfs (the generic step); the "Qubes OS" NVRAM
//! entry is made when the source's NVRAM had none.

use super::commands::{efi_dedupe, Builder, Cmd};
use crate::json::Value;

fn s(v: &Value, k: &str) -> Option<String> {
    v.get(k).and_then(Value::as_str).map(str::to_string)
}

/// The loader Qubes's installer puts on the ESP, and its NVRAM entry's name.
pub const LOADER: &str = "EFI/qubes/grubx64.efi";
pub const ENTRY: &str = "Qubes OS";

/// The Qubes releases load restores: 4.3 and later.
fn supported_version(v: &str) -> bool {
    let mut it = v.split('.').map(|x| x.parse::<u64>().unwrap_or(0));
    let (a, b) = (it.next().unwrap_or(0), it.next().unwrap_or(0));
    (a, b) >= (4, 3)
}

impl Builder<'_> {
    pub(crate) fn is_qubes(&self) -> bool {
        self.layout.str_at(&["os", "id"]) == Some(crate::qubes::OS_ID)
    }

    /// What load refuses on a Qubes source, whatever the stage: a release
    /// before 4.3, Qubes's btrfs layout (its root on btrfs, or a
    /// file-reflink pool), and a pool qubes.xml names that the dump's LVM
    /// doesn't have.
    pub(crate) fn qubes_problems(&mut self) {
        if !self.is_qubes() {
            return;
        }
        let ver = self.layout.str_at(&["os", "version_id"]).unwrap_or("").to_string();
        if !supported_version(&ver) {
            self.problems.push(format!("Qubes OS {ver}: bko-holt load restores Qubes OS 4.3 and later only"));
        }
        let root_btrfs = self.layout.arr(&["storage", "fstab"]).iter().any(|e| s(e, "file").as_deref() == Some("/") && s(e, "type").as_deref() == Some("btrfs"));
        let pools = self.layout.arr(&["storage", "qubes", "pools"]).to_vec();
        if root_btrfs || pools.iter().any(|p| s(p, "driver").as_deref() == Some("file-reflink")) {
            self.problems.push("Qubes OS on btrfs (the installer's btrfs layout, a file-reflink pool) isn't supported by bko-holt load; only its default LVM thin layout is".into());
        }
        let vgs = self.layout.arr(&["storage", "lvm", "volume_groups"]).to_vec();
        for p in pools.iter().filter(|p| s(p, "driver").as_deref() == Some("lvm_thin")) {
            let (vg, tp) = (s(p, "volume_group").unwrap_or_default(), s(p, "thin_pool").unwrap_or_default());
            let has = vgs.iter().any(|g| {
                s(g, "name").as_deref() == Some(vg.as_str())
                    && g.get("logical_volumes").and_then(Value::as_array).into_iter().flatten().any(|l| s(l, "name").as_deref() == Some(tp.as_str()) && l.at(&["segments", "0", "type"]).and_then(Value::as_str) == Some("thin-pool"))
            });
            if !has {
                self.problems.push(format!("Qubes OS: qubes.xml's pool {} is the thin pool {vg}/{tp}, which isn't in the dump", s(p, "name").unwrap_or_default()));
            }
        }
    }

    /// The boot stage's Qubes arm (UEFI GRUB 2, no shim): grub.cfg remade
    /// with 20_linux_xen and checked for its Xen entries.
    pub(crate) fn qubes_boot_cmds(&mut self, t: &str, via_shim: bool) -> Vec<Cmd> {
        let mut out = Vec::new();
        if via_shim {
            self.problems.push("Qubes OS through shim (Secure Boot): Qubes boots GRUB without shim, with Secure Boot off; bko-holt load doesn't support another setup".into());
            return out;
        }
        let Some(xen) = self.layout.v.at(&["boot", "xen"]).cloned() else {
            self.problems.push("Qubes OS: the dump has no boot.xen (no /boot/xen-*.gz was found), so there is no hypervisor for GRUB to boot".into());
            return out;
        };
        if xen.get("grub_d_20_linux_xen").and_then(Value::as_bool) == Some(false) {
            self.problems.push("Qubes OS: /etc/grub.d/20_linux_xen isn't installed, so grub2-mkconfig would make a menu without Xen".into());
        }
        // rd.luks.uuid= names a LUKS container load makes with its UUID
        // kept; one that names none here would stop dom0's boot.
        let luks: Vec<String> = self.layout.arr(&["storage", "dm_devices"]).iter().filter_map(|d| d.at(&["dm", "luks_uuid"]).and_then(Value::as_str).map(str::to_ascii_lowercase)).collect();
        for w in s(&xen, "cmdline_linux").unwrap_or_default().split_whitespace() {
            if let Some(u) = w.strip_prefix("rd.luks.uuid=") {
                let u = u.trim_start_matches("luks-").to_ascii_lowercase();
                if !luks.contains(&u) {
                    self.problems.push(format!("Qubes OS: GRUB_CMDLINE_LINUX's rd.luks.uuid={u} names no LUKS container of the layout, so dom0's initramfs wouldn't unlock its disk"));
                }
            }
        }
        let cfg = s(&xen, "grub_cfg").unwrap_or_else(|| "/boot/grub2/grub.cfg".into());
        self.notes.push(format!(
            "Qubes OS: GRUB 2 without shim (Secure Boot off): {LOADER} and EFI/BOOT/BOOTX64.EFI are the restored ESP's; only {cfg} is remade, by grub2-mkconfig with 20_linux_xen (Xen {}); GRUB_CMDLINE_XEN_DEFAULT and GRUB_CMDLINE_LINUX stay as restored (rd.luks.uuid= names the LUKS UUID, which load keeps)",
            s(&xen, "version").unwrap_or_else(|| "?".into())
        ));
        let p = format!("{t}/usr/bin/os-prober");
        let lang = match self.layout.str_at(&["boot", "grub", "grub_cfg", "lang"]) {
            Some(l) if !l.is_empty() => format!("LANG={l}.UTF-8"),
            _ => "LANG=C".to_string(),
        };
        out.push(Cmd::new(&["sh", "-c", &format!("[ ! -e {p} ] || mount --bind {t}/usr/bin/true {p}")]).why("os-prober off: it would list the rescue system's disks"));
        out.push(Cmd::new(&["chroot", t, "env", &lang, "grub2-mkconfig", "-o", &cfg]));
        out.push(Cmd::new(&["sh", "-c", &format!("! grep -q ' {p} ' /proc/self/mountinfo || umount {p}")]));
        out.push(
            Cmd::new(&["sh", "-c", &format!("grep -q '^[[:space:]]*multiboot2[[:space:]].*/xen' {t}{cfg} || {{ echo '{cfg}: grub2-mkconfig made no Xen entry (20_linux_xen found no /boot/xen-*.gz?)' >&2; exit 1; }}")])
                .why("Qubes OS: the remade menu boots Xen"),
        );
        out
    }

    /// The "Qubes OS" boot entry for \EFI\qubes\grubx64.efi, when the
    /// source's NVRAM had none for it on the layout's disks (a dump taken
    /// where the NVRAM isn't the source's).  Made last, so it comes first.
    pub(crate) fn qubes_entry(&mut self) -> Vec<Cmd> {
        let loader = format!("\\{}", LOADER.replace('/', "\\"));
        let order: Vec<String> = self.layout.v.at(&["boot", "efi", "boot_order"]).and_then(Value::as_array).into_iter().flatten().filter_map(|x| x.as_str().map(str::to_string)).collect();
        let on_layout = |part: &str| self.layout.disks.iter().any(|d| d.parts.iter().any(|p| p.name == part));
        let made = self.layout.arr(&["boot", "efi", "entries"]).iter().any(|e| {
            s(e, "number").is_some_and(|n| order.contains(&n))
                && s(e, "file").is_some_and(|f| f.replace('/', "\\").trim_start_matches('\\').eq_ignore_ascii_case(&loader[1..]))
                && e.at(&["partition", "device"]).and_then(Value::as_str).is_some_and(on_layout)
        });
        if made {
            return Vec::new();
        }
        let holds = |esp: &Value| esp.at(&["contents", "files"]).and_then(Value::as_array).is_some_and(|f| f.iter().any(|f| f.get("path").and_then(Value::as_str).is_some_and(|p| p.eq_ignore_ascii_case(LOADER))));
        let esps = self.layout.arr(&["boot", "esps"]).to_vec();
        let Some(part) = esps.iter().find(|e| holds(e)).or_else(|| esps.first()).and_then(|e| s(e, "device")) else {
            self.problems.push(format!("Qubes OS: the dump has no ESP, so there's nowhere for {LOADER}"));
            return Vec::new();
        };
        let Some((disk, num)) = self.layout.disks.iter().find_map(|d| d.parts.iter().find(|p| p.name == part).map(|p| (d.name.clone(), p.number))) else {
            self.notes.push(format!("Qubes OS: its ESP {part} isn't on the layout's disks; no boot entry made"));
            return Vec::new();
        };
        let Some(tgt) = self.names.mapping.target_of(&disk) else { return Vec::new() };
        vec![
            efi_dedupe(&format!("/dev/{tgt}"), num, &loader),
            Cmd::new(&["efibootmgr", "--quiet", "--create", "--disk", &format!("/dev/{tgt}"), "--part", &num.to_string(), "--label", ENTRY, "--loader", &loader])
                .why("Qubes OS: the boot entry its installer makes (the dump's NVRAM had none for it)"),
        ]
    }
}

#[cfg(test)]
mod tests {
    #[test]
    fn versions() {
        assert!(super::supported_version("4.3"));
        assert!(super::supported_version("4.3.2"));
        assert!(super::supported_version("5.0"));
        assert!(!super::supported_version("4.2"));
        assert!(!super::supported_version(""));
    }
}
