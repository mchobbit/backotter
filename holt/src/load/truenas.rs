//! `load`'s boot stage for TrueNAS Community Edition 25.10
//! (docs/design/truenas.md, decision 5): what `truenas_install` does on a
//! fresh install, less what only matters when unpacking an image.  In the
//! active boot environment's chroot, with boot-pool/grub at /boot/grub
//! (load/zfs_be.rs's legacy mounts):
//!
//! 1. bootfs is the source's (format, as for any pool);
//! 2. no initramfs is made: each BE's initrd came back with it, and none
//!    names a disk (cachefile=none, the pool imported by name);
//! 3. `grub-install --target=i386-pc` on every disk holding a boot-pool
//!    vdev, whatever this machine booted with, as TrueNAS installs both;
//! 4. on each of those disks' ESPs (format made each again with the
//!    source's volume ID and label): mounted at /boot/efi,
//!    `grub-install --target=x86_64-efi --bootloader-id=debian`, its image
//!    copied to the removable-media path EFI/boot/bootx64.efi, unmounted;
//! 5. `update-grub` once: TrueNAS's 10_truenas_linux lists every BE under
//!    boot-pool/ROOT, the active one first;
//! 6. the source's NVRAM entries (efi_entries, unless --no-nvram);
//! 7. then the held-back readonly=on (load/zfs_be.rs).
//!
//! The pool must keep the name boot-pool (the GRUB script names it), and
//! only TrueNAS 25.10 is supported.

use super::commands::{part_node, Builder, Cmd};
use crate::json::Value;
use crate::zfs_be::TRUENAS_POOL;

/// The ESP's partition type (GPT).
const ESP: &str = "c12a7328-f81f-11d2-ba4b-00a0c93ec93b";

fn s(v: &Value, k: &str) -> Option<String> {
    v.get(k).and_then(Value::as_str).map(str::to_string)
}

impl Builder<'_> {
    /// boot.truenas, when the source is TrueNAS.
    pub(crate) fn truenas(&self) -> Option<&Value> {
        self.layout.v.at(&["boot", "truenas"]).filter(|v| v.as_object().is_some())
    }

    /// The pool may not go by another name here on TrueNAS.
    pub(crate) fn truenas_pool_name(&mut self, name: &str, here: &str) {
        if self.truenas().is_some() && name == TRUENAS_POOL && here != name {
            self.problems.push(format!("TrueNAS: {TRUENAS_POOL} can't be renamed (its GRUB script names it), but a pool of that name is imported here; export it (or restore from another rescue system) first"));
        }
    }

    /// The source disks holding a boot-pool vdev, in the layout's order.
    fn truenas_disks(&self) -> Vec<String> {
        let member = |c: &Value| s(c, "type").as_deref() == Some("zfs_member") && (s(c, "pool_name").or_else(|| s(c, "label"))).as_deref() == Some(TRUENAS_POOL);
        self.layout.disks.iter().filter(|d| member(&d.content) || d.parts.iter().any(|p| member(&p.content))).map(|d| d.name.clone()).collect()
    }

    /// The truenas boot arm, steps 3 to 5 (and the notes), run in the
    /// chroot at T.
    pub(crate) fn truenas_boot_cmds(&mut self, t: &str) -> Vec<Cmd> {
        let tn = self.truenas().cloned().unwrap_or(Value::Null);
        match self.layout.str_at(&["os", "product"]) {
            Some(p) if p.strip_prefix("TrueNAS ").is_some_and(|v| v == "25.10" || v.starts_with("25.10.")) => {}
            Some(p) => self.problems.push(format!("TrueNAS: the source is {p}; bko-holt load supports TrueNAS 25.10 only")),
            None => self.problems.push("TrueNAS: the dump names no TrueNAS release (/etc/version wasn't read); bko-holt load supports TrueNAS 25.10 only".into()),
        }
        let mut out = Vec::new();
        let disks = self.truenas_disks();
        if disks.is_empty() {
            self.problems.push(format!("TrueNAS: no disk of the layout holds a {TRUENAS_POOL} vdev, so there's nowhere to install GRUB"));
        }
        let mut targets = Vec::new();
        let mut esps = 0;
        for name in &disks {
            let Some(tgt) = self.names.mapping.target_of(name).map(str::to_string) else {
                self.problems.push(format!("TrueNAS: {name} holds a {TRUENAS_POOL} vdev but has no target, so GRUB can't be installed on it"));
                continue;
            };
            let Some(d) = self.layout.disks.iter().find(|d| d.name == *name).cloned() else {
                self.problems.push(format!("TrueNAS: {name} holds a {TRUENAS_POOL} vdev but isn't one of the layout's disks"));
                continue;
            };
            out.push(Cmd::new(&["chroot", t, "grub-install", "--target=i386-pc", &format!("/dev/{tgt}")]).why(format!("{name}: GRUB's BIOS core in its BIOS boot partition, as TrueNAS installs it on every boot disk")));
            targets.push(tgt.clone());
            for p in d.parts.iter().filter(|p| p.ptype.eq_ignore_ascii_case(ESP)) {
                if s(&p.content, "type").as_deref() != Some("vfat") {
                    self.notes.push(format!("TrueNAS: {} (the ESP of {name}) holds no FAT file system on the source; left as it is", p.name));
                    continue;
                }
                let dev = part_node(self.layout, &d, &tgt, p.number);
                let efi = format!("{t}/boot/efi");
                // TrueNAS's BEs have no /boot/efi: one made for the mount
                // goes again after it (a mark in load's run directory says so).
                let made = format!("{}/truenas-boot-efi-made", super::RUN_DIR);
                let (qe, qm) = (super::shell_quote(&efi), super::shell_quote(&made));
                out.push(Cmd::new(&["sh", "-c", &format!("[ -d {qe} ] || {{ mkdir {qe} && : > {qm}; }}")]).why("its mount point, made only if the BE hasn't one (TrueNAS's haven't)"));
                out.push(Cmd::new(&["mount", "-t", "vfat", &dev, &efi]).why(format!("{}: the ESP, mounted only while GRUB is installed to it (as truenas_install does)", p.name)));
                out.push(Cmd::new(&["chroot", t, "grub-install", "--target=x86_64-efi", "--efi-directory=/boot/efi", "--bootloader-id=debian", "--recheck", "--no-floppy", "--no-nvram"]));
                out.push(Cmd::new(&["mkdir", "-p", &format!("{efi}/EFI/boot")]));
                out.push(Cmd::new(&["cp", &format!("{efi}/EFI/debian/grubx64.efi"), &format!("{efi}/EFI/boot/bootx64.efi")]).why("the removable-media path too, as TrueNAS installs it"));
                out.push(Cmd::new(&["umount", &efi]));
                out.push(Cmd::new(&["sh", "-c", &format!("[ ! -f {qm} ] || {{ rmdir {qe} && rm {qm}; }}")]).why("the mount point taken away again when this stage made it"));
                esps += 1;
            }
        }
        self.remake_grub_cfg(t, &mut out);
        let bes = tn.get("bes").and_then(Value::as_array).cloned().unwrap_or_default();
        let active = bes.iter().find(|b| b.get("active").and_then(Value::as_bool) == Some(true)).and_then(|b| s(b, "version").or_else(|| s(b, "name"))).unwrap_or_else(|| "unknown".into());
        let n = targets.len();
        self.notes.push(format!(
            "TrueNAS: {} boot environment{} restored (active {active}); GRUB reinstalled on {n} disk{}{}, its menu rebuilt",
            bes.len(),
            if bes.len() == 1 { "" } else { "s" },
            if n == 1 { "" } else { "s" },
            if esps > 0 { format!(" and {esps} ESP{}", if esps == 1 { "" } else { "s" }) } else { String::new() }
        ));
        self.notes.push("TrueNAS: each boot environment's initrd came back with it; none is rebuilt".into());
        out
    }
}
