//! transactional-update systems (openSUSE MicroOS and Aeon; SL Micro, Leap
//! Micro and Leap 16.1's immutable mode would match too): how to tell one,
//! and what the dump records of it (boot.transactional).
//! docs/design/aeon.md has the layout: a read-only btrfs root that changes
//! only through transactional-update, which writes each change into a new
//! snapper snapshot (`@/.snapshots/N/snapshot`, the btrfs `ro` property
//! set) and makes it the btrfs default; the next boot boots it, and
//! health-checker sets the default back after a failed first boot.  Each
//! slot has its own writable `/etc` subvolume nested in it (since
//! transactional-update 5.0; before, an overlay on /etc).
//!
//! It is one when fstab mounts `/` read-only (`ro`, or util-linux's
//! `ro=vfs`, MicroOS's), the running root is a snapper slot whose subvolume
//! is read-only, and tukit or transactional-update is installed.  It
//! doesn't depend on os-release's ID.  load (load/transactional.rs) takes
//! from here the booted slot and the default (they differ while an update
//! is pending), health-checker's state (the subvolume ID it would roll back
//! to, by path), transactional-update's state and the loader's mode.

use crate::json::{Obj, Value};
use crate::sys::Sys;

/// transactional-update's own state: snapshot numbers, kept across runs.
pub const TU_STATE: &str = "/var/lib/misc/transactional-update.state";
/// health-checker's state on GRUB: the last good slot, by btrfs subvolume
/// ID (LAST_WORKING_BTRFS_ID, what its rollback sets as the default) and
/// by path (LAST_WORKING_SNAPSHOT).
pub const HC_STATE: &str = "/var/lib/misc/health-check.state";
/// health-checker's own test for the BLS path (systemd-boot with
/// sdbootutil's per-snapshot entries) over GRUB's.
pub const BLS_ENTRIES: &str = "/boot/efi/loader/entries";
pub const TOOLS: [&str; 2] = ["/usr/sbin/tukit", "/usr/sbin/transactional-update"];
/// fde-tools' and sdbootutil's settings on an encrypted system (Aeon): the
/// PCRs the TPM2 key is sealed to (FDE_SEAL_PCR_LIST), recorded as
/// boot.transactional.fde_seal_pcr_list when the file is there.
pub const FDE_TOOLS: &str = "/etc/sysconfig/fde-tools";

/// fstab's options make the mount read-only: `ro`, or `ro=vfs` / `ro=fs`.
pub fn ro_in_fstab(options: &str) -> bool {
    options.split(',').any(|o| o == "ro" || o.starts_with("ro="))
}

/// KEY=VALUE lines of a shell-sourced state file, quotes taken off, in
/// order; anything else (comments, blank lines) left out.
pub fn parse_state(text: &str) -> Vec<(String, String)> {
    text.lines()
        .filter_map(|l| {
            let l = l.trim();
            let (k, v) = l.split_once('=')?;
            let ok = !k.is_empty() && !l.starts_with('#') && k.bytes().all(|b| b.is_ascii_alphanumeric() || matches!(b, b'_' | b'[' | b']'));
            ok.then(|| (k.to_string(), crate::osrel::unquote(v)))
        })
        .collect()
}

/// A subvolume path as the layout's other paths are: from the top level,
/// without `<FS_TREE>/` or slashes around it.
fn norm(p: &str) -> String {
    p.trim_start_matches("<FS_TREE>/").trim_matches('/').to_string()
}

/// A slot as boot.transactional records it: its path, ID and number.
fn slot(path: &str, id: Option<u64>) -> Value {
    Obj::new().set("path", path).set("id", id).set("snapshot", crate::load::shared::snapper_slot(path).map(|(_, n)| n)).build()
}

/// boot.transactional for this system, or None when it isn't one.  FSTAB
/// and BTRFS are the dump's storage.fstab and storage.btrfs.
pub fn read(sys: &Sys, fstab: &Value, btrfs: &Value) -> Option<Value> {
    let root = fstab.as_array()?.iter().find(|e| e.get("file").and_then(Value::as_str) == Some("/"))?;
    let root_opts = root.get("options").and_then(Value::as_str).unwrap_or("").to_string();
    if root.get("type").and_then(Value::as_str) != Some("btrfs") || !ro_in_fstab(&root_opts) {
        return None;
    }
    let tools: Vec<&str> = TOOLS.into_iter().filter(|t| sys.is_file(t)).collect();
    if tools.is_empty() {
        return None;
    }
    // The btrfs mounted at /, and the subvolume there.
    let (fs, booted) = btrfs.as_array()?.iter().find_map(|fs| {
        let m = fs.get("mounted_subvolumes")?.as_array()?.iter().find(|m| m.get("target").and_then(Value::as_str) == Some("/"))?;
        Some((fs, norm(m.get("subvol").and_then(Value::as_str)?)))
    })?;
    crate::load::shared::snapper_slot(&booted)?;
    let subs: Vec<&Value> = fs.get("subvolumes").and_then(Value::as_array).into_iter().flatten().collect();
    let sub = |p: &str| subs.iter().find(|v| v.get("path").and_then(Value::as_str).map(norm).as_deref() == Some(p)).copied();
    let booted_sub = sub(&booted)?;
    if booted_sub.get("readonly").and_then(Value::as_bool) != Some(true) {
        return None;
    }
    let default_path = fs.at(&["default_subvolume", "path"]).and_then(Value::as_str).map(norm);
    let default_id = fs.at(&["default_subvolume", "id"]).and_then(Value::as_u64);
    // /etc: a subvolume nested in the slot (transactional-update 5.0 and
    // later), or the old overlay, which fstab mounts.
    let overlay = fstab.as_array().into_iter().flatten().any(|e| e.get("file").and_then(Value::as_str) == Some("/etc") && e.get("type").and_then(Value::as_str) == Some("overlay"));
    let etc = if overlay {
        "overlay"
    } else if sub(&format!("{booted}/etc")).is_some() {
        "subvolume"
    } else {
        "directory"
    };
    if etc == "overlay" {
        sys.warn("transactional-update: /etc is an overlay (a system from before transactional-update 5.0): bko-holt load restores only systems whose /etc is a subvolume in each snapshot; `transactional-update migrate-etc` or a fresh install moves it");
    }
    let tu = sys.read(TU_STATE).map(|t| {
        let mut o = Obj::new();
        for (k, v) in parse_state(&t) {
            o.put(&k, v);
        }
        o.build()
    });
    let hc = sys.read(HC_STATE).map(|t| {
        let kv = parse_state(&t);
        let get = |k: &str| kv.iter().rev().find(|(x, _)| x == k).map(|(_, v)| v.clone());
        let id = get("LAST_WORKING_BTRFS_ID");
        Obj::new()
        .set("last_working_btrfs_id", id.as_deref().and_then(|i| i.parse::<u64>().ok()))
        .set("last_working_btrfs_id_raw", id)
        .set("last_working_snapshot", get("LAST_WORKING_SNAPSHOT").map(|p| norm(&p)))
            .build()
    });
    let pending = default_path.as_deref().is_some_and(|d| d != booted);
    // Only when fde-tools' file is there (Aeon's LUKS2 with TPM2): any other
    // system's dump is as it was, without the key.
    let pcrs = sys.read(FDE_TOOLS).and_then(|t| parse_state(&t).into_iter().rev().find(|(k, _)| k == "FDE_SEAL_PCR_LIST").map(|(_, v)| v));
    let mut o = Obj::new()
        .set("filesystem", fs.get("uuid").cloned().unwrap_or(Value::Null))
        .set("fstab_root_options", root_opts)
        .set("tools", tools)
        .set("booted", slot(&booted, booted_sub.get("id").and_then(Value::as_u64)))
        .set("default", default_path.as_deref().map(|d| slot(d, default_id)))
        .set("pending_update", pending)
        .set("etc", etc)
        .set("loader_mode", if sys.is_dir(BLS_ENTRIES) { "bls" } else { "grub" })
        .set("transactional_update_state", tu)
        .set("health_check_state", hc);
    o.put_some("fde_seal_pcr_list", pcrs);
    Some(o.build())
}

/// BOOT (the dump's boot section) with boot.transactional in it.
pub fn add_to_boot(boot: Value, t: Option<Value>) -> Value {
    match (t, boot) {
        (Some(t), Value::Obj(mut o)) => {
            o.push(("transactional".into(), t));
            Value::Obj(o)
        }
        (_, b) => b,
    }
}

#[cfg(test)]
pub(crate) mod tests {
    use super::*;
    use crate::sys::FakeRunner;
    use crate::testutil::Tree;

    /// The microos guest's /etc/fstab (docs/design/aeon.md, "What M1
    /// found"), as the dump has it, and its storage.btrfs, trimmed.
    pub(crate) const FSTAB: &str = r#"[
 {"spec": "UUID=a534a4d5-21d8-64fa-7aa4-eb6434980056", "file": "/", "type": "btrfs", "options": "ro=vfs", "resolves_to": "vda3"},
 {"spec": "UUID=a534a4d5-21d8-64fa-7aa4-eb6434980056", "file": "/.snapshots", "type": "btrfs", "options": "defaults,subvol=@/.snapshots", "resolves_to": "vda3"},
 {"spec": "UUID=a534a4d5-21d8-64fa-7aa4-eb6434980056", "file": "/var", "type": "btrfs", "options": "defaults,subvol=@/var,x-initrd.mount", "resolves_to": "vda3"},
 {"spec": "UUID=16DB-949C", "file": "/boot/efi", "type": "vfat", "options": "defaults,umask=0077", "resolves_to": "vda2"},
 {"spec": "/etc", "file": "/etc", "type": "none", "options": "bind,x-initrd.mount", "resolves_to": null}
]"#;

    pub(crate) fn btrfs(default: (u64, &str)) -> String {
        format!(
            r#"[{{"uuid": "a534a4d5-21d8-64fa-7aa4-eb6434980056", "devices": ["vda3"],
 "mounted_subvolumes": [{{"target": "/", "subvol": "/@/.snapshots/2/snapshot"}}, {{"target": "/.snapshots", "subvol": "/@/.snapshots"}}, {{"target": "/etc", "subvol": "/@/.snapshots/2/snapshot/etc"}}, {{"target": "/var", "subvol": "/@/var"}}],
 "subvolumes": [
  {{"id": 256, "path": "<FS_TREE>/@", "readonly": false}},
  {{"id": 257, "path": "<FS_TREE>/@/.snapshots", "readonly": false}},
  {{"id": 258, "path": "<FS_TREE>/@/.snapshots/1/snapshot", "readonly": true}},
  {{"id": 263, "path": "<FS_TREE>/@/var", "readonly": false}},
  {{"id": 268, "path": "<FS_TREE>/@/.snapshots/1/snapshot/etc", "readonly": false}},
  {{"id": 269, "path": "<FS_TREE>/@/.snapshots/2/snapshot", "readonly": true}},
  {{"id": 270, "path": "@/.snapshots/2/snapshot/etc", "readonly": false}},
  {{"id": 271, "path": "<FS_TREE>/@/.snapshots/3/snapshot", "readonly": true}}
 ],
 "default_subvolume": {{"id": {}, "gen": 47, "top_level": 257, "path": "{}"}}}}]"#,
            default.0, default.1
        )
    }

    /// The guest's state files, as M1's check boot left them.
    pub(crate) const TU: &str = "LAST_WORKING_SNAPSHOTS=\"1 \"\nUNUSED_SNAPSHOTS=\"2 \"\nLAST_BOOTED=\"1\"\nREBOOT_ASSOCS[2]=reboot\nFORCE_REFRESH=\"0\"\n";
    pub(crate) const HC: &str = "LAST_WORKING_BTRFS_ID=269\nLAST_WORKING_SNAPSHOT=@/.snapshots/2/snapshot\n";

    fn guest() -> Tree {
        let t = Tree::new("transactional");
        t.file("/usr/sbin/tukit", "").file("/usr/sbin/transactional-update", "").file(TU_STATE, TU).file(HC_STATE, HC);
        t
    }

    fn sys(t: &Tree) -> Sys {
        Sys::new(t.root(), Box::new(FakeRunner::default()), true)
    }

    fn j(s: &str) -> Value {
        crate::json::parse(s).unwrap()
    }

    #[test]
    fn microos_is_transactional() {
        let t = guest();
        let s = sys(&t);
        let v = read(&s, &j(FSTAB), &j(&btrfs((269, "@/.snapshots/2/snapshot")))).expect("transactional");
        let g = |p: &[&str]| v.at(p).cloned().unwrap_or(Value::Null);
        assert_eq!(g(&["booted", "path"]).as_str(), Some("@/.snapshots/2/snapshot"));
        assert_eq!((g(&["booted", "id"]).as_u64(), g(&["booted", "snapshot"]).as_u64()), (Some(269), Some(2)));
        assert_eq!(g(&["default", "path"]).as_str(), Some("@/.snapshots/2/snapshot"));
        assert_eq!(g(&["pending_update"]), Value::Bool(false));
        assert_eq!(g(&["etc"]).as_str(), Some("subvolume"));
        assert_eq!(g(&["loader_mode"]).as_str(), Some("grub"));
        assert_eq!(g(&["fstab_root_options"]).as_str(), Some("ro=vfs"));
        assert_eq!(g(&["filesystem"]).as_str(), Some("a534a4d5-21d8-64fa-7aa4-eb6434980056"));
        assert_eq!(g(&["health_check_state", "last_working_btrfs_id"]).as_u64(), Some(269));
        assert_eq!(g(&["health_check_state", "last_working_snapshot"]).as_str(), Some("@/.snapshots/2/snapshot"));
        assert_eq!(g(&["transactional_update_state", "LAST_WORKING_SNAPSHOTS"]).as_str(), Some("1 "));
        assert_eq!(g(&["transactional_update_state", "REBOOT_ASSOCS[2]"]).as_str(), Some("reboot"));
        assert_eq!(g(&["tools"]).as_array().map(Vec::len), Some(2));
        assert!(s.warnings().is_empty(), "{:?}", s.warnings());
        // A pending update: the default is 3, not the booted 2.  BLS: the
        // ESP has loader/entries.
        t.file(&format!("{BLS_ENTRIES}/x.conf"), "");
        let v = read(&s, &j(FSTAB), &j(&btrfs((271, "@/.snapshots/3/snapshot")))).unwrap();
        assert_eq!((v.at(&["default", "snapshot"]).and_then(Value::as_u64), v.get("pending_update")), (Some(3), Some(&Value::Bool(true))));
        assert_eq!(v.get("loader_mode").and_then(Value::as_str), Some("bls"));
        // No state files: null.
        std::fs::remove_file(t.p(HC_STATE)).unwrap();
        std::fs::remove_file(t.p(TU_STATE)).unwrap();
        let v = read(&s, &j(FSTAB), &j(&btrfs((269, "@/.snapshots/2/snapshot")))).unwrap();
        assert!(v.get("health_check_state").is_some_and(Value::is_null) && v.get("transactional_update_state").is_some_and(Value::is_null));
    }

    /// Tumbleweed's and Leap's: / writable in fstab (or a writable root
    /// slot, or no tukit): not transactional.
    #[test]
    fn tumbleweed_and_leap_arent() {
        let t = guest();
        let s = sys(&t);
        let tw = FSTAB.replace("\"options\": \"ro=vfs\"", "\"options\": \"defaults\"");
        assert!(read(&s, &j(&tw), &j(&btrfs((269, "@/.snapshots/2/snapshot")))).is_none());
        // ro in fstab, but the root slot writable (a snapper rollback's).
        let rw = btrfs((269, "@/.snapshots/2/snapshot")).replace("\"<FS_TREE>/@/.snapshots/2/snapshot\", \"readonly\": true", "\"<FS_TREE>/@/.snapshots/2/snapshot\", \"readonly\": false");
        assert!(read(&s, &j(FSTAB), &j(&rw)).is_none());
        // A root that isn't a snapper slot.
        let plain = btrfs((269, "@/.snapshots/2/snapshot")).replace("{\"target\": \"/\", \"subvol\": \"/@/.snapshots/2/snapshot\"}", "{\"target\": \"/\", \"subvol\": \"/@\"}");
        assert!(read(&s, &j(FSTAB), &j(&plain)).is_none());
        // No transactional-update.
        let bare = Tree::new("transactional-bare");
        assert!(read(&sys(&bare), &j(FSTAB), &j(&btrfs((269, "@/.snapshots/2/snapshot")))).is_none());
        // ro, but tukit alone counts.
        bare.file("/usr/sbin/tukit", "");
        assert!(read(&sys(&bare), &j(FSTAB), &j(&btrfs((269, "@/.snapshots/2/snapshot")))).is_some());
    }

    #[test]
    fn etc_overlay_warns() {
        let t = guest();
        let s = sys(&t);
        let f = FSTAB.replace("{\"spec\": \"/etc\", \"file\": \"/etc\", \"type\": \"none\"", "{\"spec\": \"overlay\", \"file\": \"/etc\", \"type\": \"overlay\"");
        let v = read(&s, &j(&f), &j(&btrfs((269, "@/.snapshots/2/snapshot")))).unwrap();
        assert_eq!(v.get("etc").and_then(Value::as_str), Some("overlay"));
        assert!(s.warnings()[0].starts_with("transactional-update: /etc is an overlay"), "{:?}", s.warnings());
    }

    /// The guest's own dump (load/transactional.rs's fixture, redacted):
    /// its storage gives back its boot.transactional.
    #[test]
    fn the_guests_dump() {
        let v = crate::json::parse(include_str!("../tests/fixtures/microos-fresh.json")).unwrap();
        let t = guest();
        let got = read(&sys(&t), v.at(&["storage", "fstab"]).unwrap(), v.at(&["storage", "btrfs"]).unwrap()).unwrap();
        let want = v.at(&["boot", "transactional"]).unwrap();
        for k in ["filesystem", "fstab_root_options", "booted", "default", "pending_update", "etc", "loader_mode", "health_check_state"] {
            assert_eq!(got.get(k), want.get(k), "{k}");
        }
        assert_eq!(want.at(&["booted", "path"]).and_then(Value::as_str), Some("@/.snapshots/2/snapshot"));
    }

    /// Aeon's layout (the synthetic fixture: load/transactional.rs's
    /// aeon(), from the guest's disk read by vm-mount, until a real dump
    /// replaces it): its fstab (/ compress=zstd:1,ro=vfs on the LUKS2
    /// container's btrfs) and btrfs (slot 1 booted and the default) read as
    /// transactional, BLS (sdbootutil's entries on the ESP), no
    /// health-check.state (the BLS path writes none), fde-tools' PCRs.
    #[test]
    fn aeon_is_transactional() {
        let v = crate::json::parse(include_str!("../tests/fixtures/aeon-synthetic.json")).unwrap();
        let t = Tree::new("transactional-aeon");
        t.file("/usr/sbin/tukit", "").file("/usr/sbin/transactional-update", "").file(TU_STATE, "LAST_WORKING_SNAPSHOTS=\"1 \"\n");
        t.file(&format!("{BLS_ENTRIES}/aeon-7.3.3-1-default-1.conf"), "title Aeon\n");
        t.file(FDE_TOOLS, "# fde-tools\nFDE_SEAL_PCR_LIST=4,5,7,9\n");
        let s = sys(&t);
        let got = read(&s, v.at(&["storage", "fstab"]).unwrap(), v.at(&["storage", "btrfs"]).unwrap()).expect("transactional");
        assert_eq!(&got, v.at(&["boot", "transactional"]).unwrap(), "{}", got.pretty());
        assert_eq!(got.get("loader_mode").and_then(Value::as_str), Some("bls"));
        assert!(got.get("health_check_state").is_some_and(Value::is_null));
        assert_eq!(got.at(&["booted", "path"]), got.at(&["default", "path"]));
        assert!(s.warnings().is_empty(), "{:?}", s.warnings());
        // Without fde-tools' file (MicroOS's): no key at all.
        std::fs::remove_file(t.p(FDE_TOOLS)).unwrap();
        assert!(read(&s, v.at(&["storage", "fstab"]).unwrap(), v.at(&["storage", "btrfs"]).unwrap()).unwrap().get("fde_seal_pcr_list").is_none());
    }

    #[test]
    fn helpers() {
        for (o, ro) in [("ro=vfs", true), ("ro", true), ("defaults,ro", true), ("defaults", false), ("rw,subvol=@/x", false), ("errors=remount-ro", false)] {
            assert_eq!(ro_in_fstab(o), ro, "{o}");
        }
        assert_eq!(parse_state("# x\nA=\"1 2\"\nB=3\n\nC[2]=r\n=x\n"), vec![("A".into(), "1 2".into()), ("B".into(), "3".into()), ("C[2]".into(), "r".into())]);
        let b = Obj::new().set("loader", 1u64).build();
        assert_eq!(add_to_boot(b.clone(), None), b);
        assert!(add_to_boot(b, Some(Value::Bool(true))).get("transactional").is_some());
    }
}
