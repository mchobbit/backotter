//! ostree systems (Fedora's atomic desktops, Bazzite): how to tell one is
//! running, and what the dump records of its deployments and boot setup
//! (boot.ostree).  docs/design/bazzite.md has the layout: `/` is a composefs
//! overlay rebuilt at each boot, the physical root (fstab's `/`, mounted at
//! /sysroot) holds the repository and the deployments, each with its own
//! etc, and /boot holds the Boot Loader Specification entries that name them.
//!
//! Paths into the physical root are recorded as fstab has them: from its
//! top (`/ostree/deploy/default/deploy/CSUM.1`), not through /sysroot, so
//! `load` can check them against the restored tree.

use crate::json::{Obj, Value};
use crate::mounts::Mount;
use crate::sys::Sys;

/// The physical root of a running ostree system when fstab and mountinfo
/// don't say otherwise.
pub const SYSROOT: &str = "/sysroot";

/// Whether this is a running ostree system, by ostree's own markers (not
/// by os-release: Silverblue and Kinoite say `fedora`): the file
/// ostree-prepare-root leaves, `/` as a composefs overlay, or an `ostree=`
/// on the kernel command line.
pub fn detect(sys: &Sys, mounts: &[Mount]) -> bool {
    sys.exists("/run/ostree-booted")
        || mounts.iter().any(|m| m.target == "/" && m.fstype == "overlay" && m.source == "composefs")
        || sys.read("/proc/cmdline").is_some_and(|c| karg(&c).is_some())
}

/// The `ostree=` word of a kernel command line.
pub fn karg(cmdline: &str) -> Option<String> {
    crate::boot::shell_words(cmdline).into_iter().find_map(|w| w.strip_prefix("ostree=").map(str::to_string))
}

/// A GLib key file (ostree's repository config, prepare-root.conf, an
/// .origin): (section, key, value), comments and blank lines left out.
pub fn parse_ini(text: &str) -> Vec<(String, String, String)> {
    let mut out = Vec::new();
    let mut section = String::new();
    for line in text.lines() {
        let l = line.trim();
        if l.is_empty() || l.starts_with('#') || l.starts_with(';') {
            continue;
        }
        if let Some(s) = l.strip_prefix('[').and_then(|s| s.strip_suffix(']')) {
            section = s.trim().to_string();
            continue;
        }
        if let Some((k, v)) = l.split_once('=') {
            out.push((section.clone(), k.trim().to_string(), v.trim().to_string()));
        }
    }
    out
}

fn ini_get<'a>(ini: &'a [(String, String, String)], section: &str, key: &str) -> Option<&'a str> {
    ini.iter().rev().find(|(s, k, _)| s == section && k == key).map(|(_, _, v)| v.as_str())
}

/// PATH (absolute, from the top of BASE) with every symlink in it followed,
/// an absolute link target taken from BASE's top as well: where an
/// `ostree=` path or a `boot.N` link leads in the physical root.  None when
/// it leads nowhere that exists, or loops.
pub fn resolve(sys: &Sys, base: &str, path: &str) -> Option<String> {
    let base = base.trim_end_matches('/');
    let mut todo: std::collections::VecDeque<String> = path.split('/').filter(|c| !c.is_empty()).map(str::to_string).collect();
    let mut cur: Vec<String> = Vec::new();
    let mut links = 0;
    while let Some(c) = todo.pop_front() {
        match c.as_str() {
            "." => continue,
            ".." => {
                cur.pop();
                continue;
            }
            _ => {}
        }
        let here = format!("{base}/{}", cur.iter().chain(std::iter::once(&c)).cloned().collect::<Vec<_>>().join("/"));
        match sys.readlink(&here) {
            Some(t) => {
                links += 1;
                if links > 40 {
                    return None;
                }
                if t.starts_with('/') {
                    cur.clear();
                }
                for (i, p) in t.split('/').filter(|p| !p.is_empty()).enumerate() {
                    todo.insert(i, p.to_string());
                }
            }
            None => cur.push(c),
        }
    }
    let out = format!("/{}", cur.join("/"));
    sys.exists(&format!("{base}{}", if out == "/" { "" } else { &out })).then_some(out)
}

/// A deployment directory's name, CSUM.SERIAL.
pub fn parse_deployment(name: &str) -> Option<(String, u32)> {
    let (c, n) = name.rsplit_once('.')?;
    (c.len() >= 32 && c.bytes().all(|b| b.is_ascii_hexdigit())).then_some(())?;
    Some((c.to_string(), n.parse().ok()?))
}

/// `/ostree/deploy/STATEROOT/deploy/CSUM.SERIAL` -> (stateroot, CSUM.SERIAL).
fn deployment_of(path: &str) -> Option<(String, String)> {
    let rest = path.strip_prefix("/ostree/deploy/")?;
    let (stateroot, rest) = rest.split_once('/')?;
    let name = rest.strip_prefix("deploy/")?;
    (!name.contains('/') && parse_deployment(name).is_some()).then(|| (stateroot.to_string(), name.to_string()))
}

/// A BLS entry: KEY VALUE lines.
fn bls_kv(text: &str) -> Vec<(String, String)> {
    text.lines()
        .map(str::trim)
        .filter(|l| !l.is_empty() && !l.starts_with('#'))
        .map(|l| {
            let (k, v) = l.split_once(char::is_whitespace).unwrap_or((l, ""));
            (k.to_string(), v.trim().to_string())
        })
        .collect()
}

/// boot.ostree, for the running system whose physical root is mounted at
/// PHYS (/sysroot).
pub fn json(sys: &Sys, phys: &str) -> Value {
    let phys = phys.trim_end_matches('/');
    let cmdline_karg = sys.read("/proc/cmdline").and_then(|c| karg(&c));
    let booted_path = cmdline_karg.as_deref().and_then(|k| resolve(sys, phys, k));
    let booted = booted_path.as_deref().and_then(deployment_of);

    // Every deployment of every stateroot, with its origin.
    let mut deployments = Vec::new();
    for stateroot in sys.list(&format!("{phys}/ostree/deploy")) {
        let dir = format!("{phys}/ostree/deploy/{stateroot}/deploy");
        for name in sys.list(&dir) {
            let Some((csum, serial)) = parse_deployment(&name) else { continue };
            if !sys.is_dir(&format!("{dir}/{name}")) || sys.readlink(&format!("{dir}/{name}")).is_some() {
                continue;
            }
            let origin = sys.read(&format!("{dir}/{name}.origin")).map(|t| parse_ini(&t));
            let origin = origin.as_deref().and_then(|o| ini_get(o, "origin", "container-image-reference").or_else(|| ini_get(o, "origin", "refspec"))).map(str::to_string);
            let is_booted = booted.as_ref().is_some_and(|(s, n)| *s == stateroot && *n == name);
            deployments.push(
                Obj::new()
                    .set("stateroot", stateroot.as_str())
                    .set("checksum", csum)
                    .set("serial", serial)
                    .set("path", format!("/ostree/deploy/{stateroot}/deploy/{name}"))
                    .set("booted", is_booted)
                    .set("origin", origin)
                    .set("has_etc", sys.is_dir(&format!("{dir}/{name}/etc")))
                    .build(),
            );
        }
    }

    // The loader entries, each with the deployment its ostree= leads to.
    let entries_dir = "/boot/loader/entries";
    let entries: Vec<Value> = sys
        .list(entries_dir)
        .into_iter()
        .filter(|f| f.ends_with(".conf"))
        .filter_map(|f| sys.read(&format!("{entries_dir}/{f}")).map(|t| (f, bls_kv(&t))))
        .map(|(f, kv)| {
            let get = |k: &str| kv.iter().find(|(key, _)| key == k).map(|(_, v)| v.clone());
            let options = get("options");
            let target = options.as_deref().and_then(karg);
            let leads = target.as_deref().and_then(|k| resolve(sys, phys, k));
            let dep = leads.as_deref().and_then(deployment_of);
            Obj::new()
                .set("file", f.as_str())
                .set("title", get("title"))
                .set("version", get("version"))
                .set("linux", get("linux"))
                .set("initrd", get("initrd"))
                .set("options", options)
                .set("ostree", target)
                .set("deployment", dep.map(|(s, n)| format!("/ostree/deploy/{s}/deploy/{n}")))
                .build()
        })
        .collect();

    // ostree/boot.N -> boot.N.M, which the ostree= paths go through.
    let mut boot_links = Obj::new();
    for e in sys.list(&format!("{phys}/ostree")) {
        if e.starts_with("boot.") {
            if let Some(t) = sys.readlink(&format!("{phys}/ostree/{e}")) {
                boot_links.put(&e, t);
            }
        }
    }

    // composefs and the read-only sysroot: /etc's file overrides /usr/lib's.
    let mut prepare = Obj::new();
    let (mut composefs, mut readonly) = (None, None);
    let mut files = Vec::new();
    for f in ["/usr/lib/ostree/prepare-root.conf", "/etc/ostree/prepare-root.conf"] {
        let Some(t) = sys.read(f) else { continue };
        let ini = parse_ini(&t);
        files.push(f);
        if let Some(v) = ini_get(&ini, "composefs", "enabled") {
            composefs = Some(v.to_string());
        }
        if let Some(v) = ini_get(&ini, "sysroot", "readonly") {
            readonly = Some(v.to_string());
        }
    }
    prepare.put("composefs", composefs);
    prepare.put("readonly", readonly);
    prepare.put("files", files);

    let repo = sys.read(&format!("{phys}/ostree/repo/config")).map(|t| {
        let ini = parse_ini(&t);
        let mut fsv = Obj::new();
        for (s, k, v) in &ini {
            if s == "ex-fsverity" {
                fsv.put(k, v.as_str());
            }
        }
        let has_fsv = ini.iter().any(|(s, _, _)| s == "ex-fsverity");
        Obj::new()
            .set("mode", ini_get(&ini, "core", "mode"))
            .set("sysroot_readonly", ini_get(&ini, "sysroot", "readonly"))
            .set("sysroot_bootloader", ini_get(&ini, "sysroot", "bootloader"))
            .set("ex_fsverity", if has_fsv { fsv.build() } else { Value::Null })
            .build()
    });

    let grubenv = ["/boot/grub2/grubenv", "/boot/grub/grubenv"].iter().find_map(|f| sys.read(f)).map(|t| {
        let env = crate::boot::parse_grubenv(&t);
        let get = |k: &str| env.iter().find(|(key, _)| key == k).map(|(_, v)| v.clone());
        Obj::new().set("boot_success", get("boot_success")).set("boot_counter", get("boot_counter")).set("next_entry", get("next_entry")).build()
    });

    Obj::new()
        .set("booted", sys.exists("/run/ostree-booted"))
        .set("karg", cmdline_karg)
        .set("physical_root", phys)
        .set("stateroot", booted.as_ref().map(|(s, _)| s.clone()))
        .set("booted_deployment", booted.map(|(s, n)| format!("/ostree/deploy/{s}/deploy/{n}")))
        .set("deployments", deployments)
        .set("boot_loader", sys.readlink("/boot/loader"))
        .set("boot_links", boot_links)
        .set("entries", entries)
        .set("prepare_root", prepare)
        .set("repo", repo)
        .set("bootupd_state", sys.is_file("/boot/bootupd-state.json"))
        .set("grubenv", grubenv)
        .set("image", bootc_image(sys))
        .build()
}

/// The booted image as `bootc status --json` names it, when bootc is there.
fn bootc_image(sys: &Sys) -> Value {
    let Some(out) = sys.run("bootc", &["status", "--json"]) else { return Value::Null };
    let Ok(v) = crate::json::parse(&out) else { return Value::Null };
    let img = v.at(&["status", "booted", "image"]);
    let s = |p: &[&str]| img.and_then(|i| i.at(p)).and_then(Value::as_str).map(str::to_string);
    if img.is_none() {
        return Value::Null;
    }
    Obj::new()
        .set("image", s(&["image", "image"]))
        .set("transport", s(&["image", "transport"]))
        .set("digest", s(&["imageDigest"]))
        .set("version", s(&["version"]))
        .build()
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::sys::FakeRunner;
    use crate::testutil::Tree;

    #[test]
    fn kargs_and_ini() {
        assert_eq!(karg("BOOT_IMAGE=(hd0,gpt2)/ostree/x/vmlinuz ostree=/ostree/boot.1/default/abc/0 rw\n").as_deref(), Some("/ostree/boot.1/default/abc/0"));
        assert_eq!(karg("root=UUID=x ro"), None);
        let i = parse_ini("[core]\nrepo_version=1\nmode=bare\n\n# c\n[sysroot]\nreadonly=true\nbootloader=none\n[remote \"default\"]\nurl=quay.io/x@sha256:1\n");
        assert_eq!(ini_get(&i, "core", "mode"), Some("bare"));
        assert_eq!(ini_get(&i, "sysroot", "bootloader"), Some("none"));
        assert_eq!(ini_get(&i, "remote \"default\"", "url"), Some("quay.io/x@sha256:1"));
        assert_eq!(ini_get(&i, "core", "nope"), None);
        let p = parse_ini("[composefs]\nenabled = yes\n[sysroot]\nreadonly = true\n");
        assert_eq!(ini_get(&p, "composefs", "enabled"), Some("yes"));
        let c = "10e6a37fe91449ab0256dc707f50023505f76d29b6d34844b63843d0950f7de6";
        assert_eq!(parse_deployment(&format!("{c}.1")), Some((c.to_string(), 1)));
        assert_eq!(parse_deployment(&format!("{c}.1.origin")), None);
        assert_eq!(parse_deployment("short.1"), None);
        assert_eq!(deployment_of(&format!("/ostree/deploy/default/deploy/{c}.0")), Some(("default".into(), format!("{c}.0"))));
        assert_eq!(deployment_of(&format!("/ostree/deploy/default/deploy/{c}.0/etc")), None);
    }

    #[test]
    fn resolves_the_boot_chain() {
        let t = Tree::new("ostree-resolve");
        let c = "10e6a37fe91449ab0256dc707f50023505f76d29b6d34844b63843d0950f7de6";
        t.dir(&format!("/sysroot/ostree/deploy/default/deploy/{c}.1/etc"))
            .dir(&format!("/sysroot/ostree/deploy/default/deploy/{c}.0"))
            .symlink("/sysroot/ostree/boot.1", "boot.1.1")
            .symlink("/sysroot/ostree/boot.1.1/default/bb/0", &format!("../../../deploy/default/deploy/{c}.1"))
            .symlink("/sysroot/ostree/boot.1.1/default/bb/1", &format!("../../../deploy/default/deploy/{c}.0"))
            .symlink("/sysroot/abs", "/ostree/boot.1")
            .symlink("/sysroot/loop", "loop");
        let s = Sys::new(t.root(), Box::new(FakeRunner::default()), false);
        assert_eq!(resolve(&s, "/sysroot", "/ostree/boot.1/default/bb/0").as_deref(), Some(format!("/ostree/deploy/default/deploy/{c}.1").as_str()));
        assert_eq!(resolve(&s, "/sysroot/", "/ostree/boot.1/default/bb/1/etc/..").as_deref(), Some(format!("/ostree/deploy/default/deploy/{c}.0").as_str()));
        // An absolute link is taken from the physical root's top, not the host's.
        assert_eq!(resolve(&s, "/sysroot", "/abs/default/bb/0/etc").as_deref(), Some(format!("/ostree/deploy/default/deploy/{c}.1/etc").as_str()));
        assert_eq!(resolve(&s, "/sysroot", "/ostree/boot.1/default/bb/2"), None);
        assert_eq!(resolve(&s, "/sysroot", "/loop/x"), None);
        assert_eq!(resolve(&s, "/sysroot", "/").as_deref(), Some("/"));
    }
}
