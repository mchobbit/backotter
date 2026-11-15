//! `load`'s boot stage for a system booted by ZFSBootMenu
//! (docs/design/zfsbootmenu.md, decisions 3, 5-7; boot.zfsbootmenu,
//! crate::zfsbootmenu).  ZFSBootMenu is a standalone EFI image the OS
//! doesn't manage; it imports the pool, lists every boot environment (BE)
//! and kexecs the newest kernel in the chosen BE's own /boot.  So:
//!
//! 1. no loader is installed: the images came back with the ESP (fstab's
//!    /boot/efi), and a note names each with its version and SHA-256 as the
//!    dump read them;
//! 2. the booted BE's initramfs is remade by the boot stage's generator
//!    step (dracut on Void, for each kernel in its /lib/modules), with the
//!    restored /etc/hostid and key file in it;
//! 3. the inactive BEs, restored through their own backup sources
//!    (load/zfs_be.rs) and unmounted when the stage starts, keep their
//!    /boot as restored (decision 6): their initramfs hold the same hostid,
//!    and ZFSBootMenu imports the pool by name;
//! 4. efi_entries makes the source's NVRAM entries again, in its order.
//!
//! Refused in the plan: a BE pool missing from the layout, a pool whose
//! bootfs isn't among its datasets, and no image on an ESP that fstab
//! mounts and the layout makes.  Without NVRAM entries (--no-nvram,
//! --removable) a copy finds ZFSBootMenu only at EFI/BOOT/BOOTX64.EFI.
//!
//! Also here, for any ZFS encryption root, not ZFSBootMenu's only: a key
//! file inside the restored tree (keylocation=file:///etc/zfs/zroot.key on
//! a dataset of the pool), whose content --key-file gave; after the restore
//! the restored file must be that key, or the initramfs would carry one
//! that doesn't open the pool.

use super::commands::{Builder, Cmd};
use super::shell_quote;
use crate::json::Value;

fn s(v: &Value, k: &str) -> Option<String> {
    v.get(k).and_then(Value::as_str).map(str::to_string)
}

impl Builder<'_> {
    /// boot.zfsbootmenu, when the source booted (or holds) ZFSBootMenu.
    pub(crate) fn zbm(&self) -> Option<&Value> {
        self.layout.v.at(&["boot", "zfsbootmenu"]).filter(|v| v.as_object().is_some())
    }

    /// The path of an encryption root's key file when it lies inside the
    /// restored tree: keylocation=file://PATH and the deepest of the
    /// source's mounts above PATH is a ZFS dataset (not a keystore on
    /// another filesystem, which keystores() fills).
    pub(crate) fn key_file_in_tree(&self, ds: &Value) -> Option<String> {
        let path = s(ds, "keylocation")?.strip_prefix("file://")?.to_string();
        let mount = self
            .layout
            .arr(&["storage", "mounts"])
            .iter()
            .filter_map(|m| Some((s(m, "target")?, s(m, "fstype")?)))
            .filter(|(t, _)| t == "/" || path.starts_with(&format!("{}/", t.trim_end_matches('/'))))
            .max_by_key(|(t, _)| t.len())?;
        (mount.1 == "zfs").then_some(path)
    }

    /// The boot stage's check of each encryption root whose key file is in
    /// the restored tree and whose key --key-file gave: the restored file
    /// is that key, byte for byte, under the target T.
    pub(crate) fn zfs_key_file_checks(&self, t: &str) -> Vec<Cmd> {
        let mut out = Vec::new();
        for ds in self.layout.arr(&["storage", "zfs", "tools", "datasets"]) {
            let name = s(ds, "name").unwrap_or_default();
            if s(ds, "encryptionroot").as_deref() != Some(name.as_str()) {
                continue;
            }
            let (Some(path), Some(key)) = (self.key_file_in_tree(ds), self.opts.key_file(&name)) else { continue };
            let (k, r) = (shell_quote(key), shell_quote(&format!("{t}{path}")));
            let msg = format!("ZFS encryption root {name}: the restored {path} is not the key --key-file {name}={key} gave, so the initramfs would carry a key that does not open the pool; give the key the source kept there");
            out.push(Cmd::new(&["sh", "-c", &format!("cmp -s {k} {r} || {{ echo {} >&2; exit 1; }}", shell_quote(&msg))]).why(format!("{name}: the restored key file {path} must be the key it was made with")));
        }
        out
    }

    /// The zfsbootmenu boot arm's notes and refusals (it runs nothing: the
    /// initramfs step and efi_entries are the stage's own).
    pub(crate) fn zfsbootmenu_boot_cmds(&mut self) -> Vec<Cmd> {
        let zbm = self.zbm().cloned().unwrap_or(Value::Null);
        let images: Vec<Value> = zbm.get("images").and_then(Value::as_array).cloned().unwrap_or_default();
        // 1. The images, as the dump read them.
        for i in &images {
            let path = s(i, "path").unwrap_or_default();
            let esp = s(i, "esp_mount").unwrap_or_default();
            let version = match (s(i, "version"), s(i, "osrel")) {
                (Some(v), _) => format!("version {v}"),
                (None, Some(o)) => format!("version not recorded (its .osrel is its build system's, {o})"),
                (None, None) => "version not recorded".to_string(),
            };
            let kernel = s(i, "kernel").map(|k| format!(", kernel {k}")).unwrap_or_default();
            let sha = s(i, "sha256").unwrap_or_else(|| "not recorded".into());
            self.notes.push(format!("ZFSBootMenu: its image {path} on the ESP at {esp} ({version}{kernel}, SHA-256 {sha}) comes back with the ESP; nothing is installed"));
        }
        // The refusals.
        let pools: Vec<String> = self.layout.arr(&["storage", "zfs", "tools", "imported_pools"]).iter().filter_map(|p| s(p, "name")).collect();
        let datasets: Vec<String> = self.layout.arr(&["storage", "zfs", "tools", "datasets"]).iter().filter_map(|d| s(d, "name")).collect();
        for p in zbm.get("pools").and_then(Value::as_array).into_iter().flatten() {
            let name = s(p, "name").unwrap_or_default();
            if !pools.contains(&name) {
                self.problems.push(format!("ZFSBootMenu: its boot environments' pool {name} isn't in the layout, so nothing it boots would be restored"));
                continue;
            }
            if let Some(b) = s(p, "bootfs").filter(|b| !datasets.contains(b)) {
                self.problems.push(format!("ZFSBootMenu: pool {name}'s bootfs {b} isn't among the layout's datasets, so ZFSBootMenu would have no default boot environment"));
            }
        }
        let made = |esp: &str| {
            self.layout.arr(&["storage", "fstab"]).iter().any(|e| s(e, "file").as_deref() == Some(esp) && s(e, "resolves_to").is_some_and(|d| self.names.dev(&d).is_some()))
        };
        if !images.iter().any(|i| s(i, "esp_mount").is_some_and(|m| made(&m))) {
            let at: Vec<String> = images.iter().map(|i| format!("{} on {}", s(i, "path").unwrap_or_default(), s(i, "esp_mount").unwrap_or_default())).collect();
            self.problems.push(format!(
                "ZFSBootMenu: no image of it is on an ESP that fstab mounts and this layout makes ({}), so the restore wouldn't bring it back",
                if at.is_empty() { "the dump lists none".to_string() } else { at.join(", ") }
            ));
        }
        // 3. The inactive BEs' /boot.
        let restored: Vec<String> = self.restored_bes().iter().map(|b| b.name.clone()).collect();
        if !restored.is_empty() {
            self.notes.push(format!(
                "ZFSBootMenu: only the booted boot environment's initramfs is remade; {}'s /boot stay as restored (each holds the same /etc/hostid, and ZFSBootMenu imports the pool by name)",
                restored.join(", ")
            ));
        }
        // Without NVRAM entries (decision 7: nothing is written beyond what
        // the ESP holds).
        // The fallback path is the source machine's (crate::arch).
        let fb = crate::arch::names_of_dump(&self.layout.v).fallback;
        let fallback = images.iter().any(|i| s(i, "path").is_some_and(|p| p.eq_ignore_ascii_case(fb)));
        if (self.opts.no_nvram || self.opts.removable) && !fallback {
            let what = if self.opts.no_nvram { "--no-nvram: no NVRAM entry is made".to_string() } else { format!("--removable: nothing is copied to {fb}") };
            self.notes.push(format!(
                "ZFSBootMenu: {what}, and the ESP has no ZFSBootMenu image at {fb} (the source had none), so firmware without the source's NVRAM entries won't boot the copy by itself; add an entry for its image (efibootmgr --create --loader) or copy it to that path"
            ));
        }
        Vec::new()
    }
}
