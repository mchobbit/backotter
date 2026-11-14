//! LUKS2 headers kept byte for byte (docs/design/ubuntu-core.md, Decision
//! 2, option A).  load otherwise makes every LUKS container anew with
//! luksFormat, which makes a new volume key: keys sealed to a TPM (Ubuntu
//! Core's FDE) and a recovery key would then open nothing.  So dump saves
//! the header with `cryptsetup luksHeaderBackup` (no key needed) into a
//! file beside the layout, its SHA-256 in storage.luks_headers, and load
//! writes it back with luksHeaderRestore and opens it with a key it already
//! has: the volume key, every keyslot and token come back.
//!
//! On by default for an Ubuntu Core's encrypted partitions; elsewhere asked
//! for with `bko-holt dump --keep-luks-header DEV`.

use crate::json::{Obj, Value};
use crate::sys::Sys;

/// Where dump writes the header files unless --luks-header-dir says: in
/// RAM, never in a backup's tree.
pub const DEFAULT_DIR: &str = "/run/bko-holt/luks-headers";

/// dump's options for them.
#[derive(Debug, Clone, Default, PartialEq)]
pub struct Options {
    /// --keep-luks-header DEV: devices (kernel names, or /dev paths).
    pub keep: Vec<String>,
    /// --luks-header-dir DIR.
    pub dir: Option<String>,
}

/// A header file's name: by the LUKS UUID, else by the device.
pub fn file_name(uuid: Option<&str>, dev: &str) -> String {
    let safe = |s: &str| !s.is_empty() && s.bytes().all(|b| b.is_ascii_alphanumeric() || matches!(b, b'-' | b'_' | b'.'));
    match uuid.filter(|u| safe(u)) {
        Some(u) => format!("luks2-header-{u}.img"),
        None => format!("luks2-header-{}.img", dev.replace(|c: char| !(c.is_ascii_alphanumeric() || c == '-' || c == '_'), "_")),
    }
}

/// Every device in the dump that holds LUKS: (name, node, content).
fn luks_devices(sections: &[&[Value]]) -> Vec<(String, String, Value)> {
    let mut out = Vec::new();
    let mut add = |x: &Value| {
        if x.at(&["content", "type"]).and_then(Value::as_str) == Some("crypto_LUKS") {
            let name = x.get("name").and_then(Value::as_str).unwrap_or("").to_string();
            let node = x.get("path").and_then(Value::as_str).map(str::to_string).unwrap_or_else(|| format!("/dev/{name}"));
            out.push((name, node, x.get("content").cloned().unwrap_or(Value::Null)));
        }
    };
    for sect in sections {
        for d in sect.iter() {
            add(d);
            for p in d.get("partitions").and_then(Value::as_array).into_iter().flatten() {
                add(p);
            }
        }
    }
    out
}

/// The minimum size of a LUKS2 header backup: both binary headers with
/// their JSON areas, and the keyslots area (cryptsetup's
/// LUKS2_hdr_and_areas_size).
fn hdr_and_areas(c: &Value) -> Option<u64> {
    let n = |k: &str| c.get(k).and_then(|v| v.as_u64().or_else(|| v.as_str().and_then(|s| s.parse().ok())));
    Some(2 * n("header_size")? + n("keyslots_size")?)
}

/// Saves the headers of WANT (device names; and those --keep-luks-header
/// names) as files, and returns storage.luks_headers.  SECTIONS are the
/// dump's disks, md and dm devices.
pub fn capture(sys: &Sys, sections: &[&[Value]], want: &[String], opts: &Options) -> Vec<Value> {
    let all = luks_devices(sections);
    let mut names: Vec<String> = want.to_vec();
    for k in &opts.keep {
        let n = k.trim_start_matches("/dev/").to_string();
        if !all.iter().any(|(name, _, _)| *name == n) {
            sys.warn(format!("--keep-luks-header {k}: no LUKS container is on {n} in this dump"));
            continue;
        }
        if !names.contains(&n) {
            names.push(n);
        }
    }
    if names.is_empty() {
        return vec![];
    }
    let dir = opts.dir.clone().unwrap_or_else(|| DEFAULT_DIR.to_string());
    let mut out = Vec::new();
    for n in names {
        let Some((_, node, c)) = all.iter().find(|(x, _, _)| *x == n) else { continue };
        let uuid = c.get("uuid").and_then(Value::as_str);
        if c.get("version").and_then(Value::as_str) != Some("2") {
            sys.warn(format!("{n}: its LUKS header is LUKS{}, not LUKS2: not kept (load keeps LUKS2 headers only)", c.get("version").and_then(Value::as_str).unwrap_or("?")));
            continue;
        }
        if !sys.privileged {
            sys.warn(format!("{n}: not running as root, so its LUKS2 header wasn't saved: load can't keep it, and can't restore this layout"));
            continue;
        }
        let file = file_name(uuid, &n);
        let path = format!("{}/{file}", dir.trim_end_matches('/'));
        let made = std::fs::create_dir_all(sys.p(&dir)).and_then(|_| {
            use std::os::unix::fs::PermissionsExt;
            std::fs::set_permissions(sys.p(&dir), std::fs::Permissions::from_mode(0o700))
        });
        if let Err(e) = made {
            sys.warn(format!("{n}: can't make {dir} for its LUKS2 header ({e}): load can't keep it"));
            continue;
        }
        // cryptsetup won't write over a file.
        let _ = std::fs::remove_file(sys.p(&path));
        let ran = sys.run("cryptsetup", &["luksHeaderBackup", node, "--header-backup-file", &path]);
        let bytes = sys.read_bytes(&path);
        let ok = |b: &Vec<u8>| b.starts_with(b"LUKS\xba\xbe") && hdr_and_areas(c).is_none_or(|need| b.len() as u64 >= need);
        match (ran, bytes) {
            (Some(_), Some(b)) if ok(&b) => out.push(
                Obj::new()
                    .set("device", n.as_str())
                    .set("luks_uuid", uuid.map(str::to_string))
                    .set("label", c.get("label").cloned().unwrap_or(Value::Null))
                    .set("file", file.as_str())
                    .set("written_to", path.as_str())
                    .set("bytes", b.len())
                    .set("sha256", crate::bytes::sha256_hex(&b))
                    .build(),
            ),
            (Some(_), Some(b)) => {
                let _ = std::fs::remove_file(sys.p(&path));
                sys.warn(format!("{n}: `cryptsetup luksHeaderBackup` wrote {} bytes that aren't a whole LUKS2 header: not kept, so load can't restore this layout", b.len()));
            }
            _ => sys.warn(format!("{n}: `cryptsetup luksHeaderBackup {node}` failed: its LUKS2 header isn't kept, so load can't restore this layout")),
        }
    }
    out
}

/// storage.luks_headers' entry for the device DEV, when its header is kept.
pub fn kept<'a>(layout: &'a Value, dev: &str) -> Option<&'a Value> {
    layout.at(&["storage", "luks_headers"]).and_then(Value::as_array)?.iter().find(|h| h.get("device").and_then(Value::as_str) == Some(dev))
}

/// Where load finds a kept header's FILE: in DIR (load's
/// --luks-header-dir), else beside the layout.
pub fn path_at_load(dir: Option<&str>, layout_path: &str, file: &str) -> String {
    let base = match dir {
        Some(d) => d.trim_end_matches('/').to_string(),
        None => match std::path::Path::new(layout_path).parent().map(|p| p.to_string_lossy().into_owned()) {
            Some(p) if !p.is_empty() => p,
            _ => ".".to_string(),
        },
    };
    format!("{base}/{file}")
}

/// Whether the file at PATH is the header the dump kept (its SHA-256):
/// None when it is, else why not.
pub fn check_file(path: &str, h: &Value) -> Option<String> {
    let want = h.get("sha256").and_then(Value::as_str).unwrap_or("");
    match std::fs::read(path) {
        Err(e) => Some(format!("{path}: {e}")),
        Ok(b) if crate::bytes::sha256_hex(&b) != want => Some(format!("{path} isn't the header the dump kept (its SHA-256 differs)")),
        Ok(_) => None,
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::sys::FakeRunner;
    use crate::testutil::Tree;

    #[test]
    fn names_and_paths() {
        assert_eq!(file_name(Some("b93c93b3-cf55-40e1-9972-c5b49656a810"), "vda5"), "luks2-header-b93c93b3-cf55-40e1-9972-c5b49656a810.img");
        assert_eq!(file_name(None, "vda5"), "luks2-header-vda5.img");
        assert_eq!(file_name(Some("../x"), "md/x"), "luks2-header-md_x.img");
        assert_eq!(path_at_load(None, "/run/x/layout.json", "h.img"), "/run/x/h.img");
        assert_eq!(path_at_load(None, "layout.json", "h.img"), "./h.img");
        assert_eq!(path_at_load(Some("/h/"), "/run/x/layout.json", "h.img"), "/h/h.img");
    }

    /// A runner that writes what `cryptsetup luksHeaderBackup` would: the
    /// device's first BYTES bytes.
    struct Backup {
        root: std::path::PathBuf,
    }
    impl crate::sys::Runner for Backup {
        fn run(&self, prog: &str, args: &[&str]) -> Option<String> {
            if prog != "cryptsetup" || args.first() != Some(&"luksHeaderBackup") {
                return None;
            }
            let dev = std::fs::read(self.root.join(args[1].trim_start_matches('/'))).ok()?;
            std::fs::write(self.root.join(args[3].trim_start_matches('/')), &dev[..dev.len().min(2 * 16384 + 16744448).min(dev.len())]).ok()?;
            Some(String::new())
        }
        fn installed(&self, _: &str) -> bool {
            true
        }
    }

    #[test]
    fn captures_luks2_only() {
        let t = Tree::new("luksheader");
        let img = crate::probe::luks::sample::luks2("6c3e5f0e-6b0a-4d6c-9a2e-8f7e1d2c3b4a", "ubuntu-data-enc");
        t.file("/dev/vda5", &img);
        let c = crate::probe::probe_all(&crate::bytes::MemSource(img.clone()))[0].to_json();
        let disks = vec![Obj::new().set("name", "vda").set("partitions", vec![Obj::new().set("name", "vda5").set("path", "/dev/vda5").set("content", c.clone()).build()]).build()];
        // The sample is 1 MiB, short of its keyslots area: refused as not whole.
        let sys = Sys::new(t.root(), Box::new(Backup { root: t.root().to_path_buf() }), true);
        assert!(capture(&sys, &[&disks], &["vda5".into()], &Options::default()).is_empty());
        assert!(sys.warnings()[0].contains("aren't a whole LUKS2 header"), "{:?}", sys.warnings());
        // A header whose keyslots area fits.
        let mut c2 = c.clone();
        if let Value::Obj(o) = &mut c2 {
            for (k, v) in o.iter_mut() {
                if k == "keyslots_size" {
                    *v = Value::from("4096");
                }
            }
        }
        let disks = vec![Obj::new().set("name", "vda").set("partitions", vec![Obj::new().set("name", "vda5").set("path", "/dev/vda5").set("content", c2).build()]).build()];
        let sys = Sys::new(t.root(), Box::new(Backup { root: t.root().to_path_buf() }), true);
        let h = capture(&sys, &[&disks], &[], &Options { keep: vec!["/dev/vda5".into(), "vdb1".into()], dir: Some("/hdr".into()) });
        assert_eq!(h.len(), 1, "{:?}", sys.warnings());
        assert_eq!(h[0].get("file").and_then(Value::as_str), Some("luks2-header-6c3e5f0e-6b0a-4d6c-9a2e-8f7e1d2c3b4a.img"));
        assert_eq!(h[0].get("written_to").and_then(Value::as_str), Some("/hdr/luks2-header-6c3e5f0e-6b0a-4d6c-9a2e-8f7e1d2c3b4a.img"));
        let b = std::fs::read(t.p("/hdr/luks2-header-6c3e5f0e-6b0a-4d6c-9a2e-8f7e1d2c3b4a.img")).unwrap();
        assert_eq!(h[0].get("sha256").and_then(Value::as_str), Some(crate::bytes::sha256_hex(&b).as_str()));
        assert!(sys.warnings().iter().any(|w| w.contains("--keep-luks-header vdb1: no LUKS container")), "{:?}", sys.warnings());
        let path = t.p("/hdr/luks2-header-6c3e5f0e-6b0a-4d6c-9a2e-8f7e1d2c3b4a.img").to_string_lossy().into_owned();
        assert_eq!(check_file(&path, &h[0]), None);
        std::fs::write(&path, b"x").unwrap();
        assert!(check_file(&path, &h[0]).unwrap().contains("SHA-256 differs"));
        // Unprivileged, or a failing cryptsetup: said, nothing kept.
        let sys = Sys::new(t.root(), Box::new(FakeRunner::default()), true);
        assert!(capture(&sys, &[&disks], &["vda5".into()], &Options::default()).is_empty());
        assert!(sys.warnings()[0].contains("luksHeaderBackup /dev/vda5` failed"), "{:?}", sys.warnings());
        let sys = Sys::new(t.root(), Box::new(FakeRunner::default()), false);
        assert!(capture(&sys, &[&disks], &["vda5".into()], &Options::default()).is_empty());
        assert!(sys.warnings()[0].contains("not running as root"), "{:?}", sys.warnings());
    }
}
