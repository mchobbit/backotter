//! `ZFSBootMenu` (docs/design/zfsbootmenu.md): how dump tells an ESP's EFI
//! image is `ZFSBootMenu`'s, and what it records of it and of the boot
//! environments it chooses among (boot.zfsbootmenu).
//!
//! A `ZFSBootMenu` image is a Linux unified kernel image: a PE file built on
//! systemd's EFI stub, with the sections `.osrel`, `.cmdline`, `.initrd`
//! and `.linux` (the v3.1.0 release EFI: also `.sbat`, `.sdmagic`
//! "#### `LoaderInfo`: systemd-stub 256.6 ####", and an embedded command
//! line "quiet loglevel=0 nomodeset").  Nothing in it says `ZFSBootMenu` in
//! plain bytes: its `.osrel` is the build system's os-release (the release
//! image's says Void Linux, as generate-zbm copies the host's
//! /etc/os-release), and its menu's scripts are in the zstd-compressed
//! initramfs.  So a unified kernel image on an ESP is taken for `ZFSBootMenu`
//! when one of these holds (the first that does is its `why`):
//!
//! - its `.osrel` names `ZFSBootMenu` (a build that says so);
//! - its path on the ESP is in a directory `ZBM` or `zfsbootmenu` (the
//!   guides' `EFI/ZBM/VMLINUZ.EFI`, generate-zbm's `EFI/zbm`), or its file
//!   name starts with `zfsbootmenu` (the release's file name);
//! - its embedded command line has a `zbm.` argument;
//! - the running system's / is a ZFS boot environment (`crate::zfs_be`'s
//!   `ZFSBootMenu` rule) and the embedded command line names no `root=`: a
//!   distribution's own unified image must say where its root is;
//! - it is a byte copy (`esp_inventory`'s `same_as`) of one of the above.
//!
//! Its version is recorded only when its `.osrel` names `ZFSBootMenu`; the
//! release image's identity is its SHA-256, with its kernel's version (the
//! bzImage's own string) and stub beside it.
//!
//! The boot environments are `crate::zfs_be::list`'s, `ZFSBootMenu`'s rule;
//! for each, the kernels and initramfs files in its own /boot, which
//! `ZFSBootMenu` boots from: the booted one's from the running tree, an
//! inactive one's through a private read-only mount under /run/bko-holt,
//! unmounted at once (as `crate::boot::read_unmounted_esp` reads an ESP).  A
//! BE whose key isn't loaded can't be read: `crate::zfs_be::backup_sources`
//! already warns of it, and its entry says `read: false`.

use crate::json::{Obj, Value};
use crate::sys::{RUN_DIR, RoMountWords, Sys, safe_name};
use crate::zfs_be::{BootEnv, Rule};
use std::io::{Read, Seek, SeekFrom};

/// `ZFSBootMenu`'s per-BE kernel command line (user property).
pub(crate) const COMMANDLINE: &str = "org.zfsbootmenu:commandline";

/// One section of a PE image: its name, where its bytes are in the file
/// and how many there are (the smaller of its virtual and raw sizes).
#[derive(Debug, Clone, PartialEq)]
pub(crate) struct Section {
    pub name: String,
    pub offset: u64,
    pub size: u64,
}

fn le16(b: &[u8], at: usize) -> Option<u16> {
    Some(u16::from_le_bytes(b.get(at..at + 2)?.try_into().ok()?))
}

fn le32(b: &[u8], at: usize) -> Option<u32> {
    Some(u32::from_le_bytes(b.get(at..at + 4)?.try_into().ok()?))
}

/// The section table of a PE image from its first bytes (`head`, which
/// must reach the end of the table): None when it isn't one.
pub(crate) fn pe_sections(head: &[u8]) -> Option<Vec<Section>> {
    if head.get(0..2)? != b"MZ" {
        return None;
    }
    let pe = le32(head, 0x3c)? as usize;
    if head.get(pe..pe + 4)? != b"PE\0\0" {
        return None;
    }
    let n = le16(head, pe + 6)? as usize;
    let opt = le16(head, pe + 20)? as usize;
    let table = pe + 24 + opt;
    let mut out = Vec::with_capacity(n);
    for i in 0..n {
        let e = head.get(table + 40 * i..table + 40 * (i + 1))?;
        let name = String::from_utf8_lossy(&e[..8])
            .trim_end_matches('\0')
            .to_string();
        let vsize = le32(e, 8)? as u64;
        let raw_size = le32(e, 16)? as u64;
        let raw_at = le32(e, 20)? as u64;
        let size = if vsize == 0 {
            raw_size
        } else {
            vsize.min(raw_size)
        };
        out.push(Section {
            name,
            offset: raw_at,
            size,
        });
    }
    Some(out)
}

/// What a unified kernel image's sections say.
#[derive(Debug, Clone, Default, PartialEq)]
pub(crate) struct Uki {
    /// `.osrel`, as KEY=value pairs.
    pub osrel: Vec<(String, String)>,
    /// `.cmdline`, trimmed.
    pub cmdline: Option<String>,
    /// The kernel's version string, from `.linux`'s bzImage setup header.
    pub kernel: Option<String>,
    /// The stub, from `.sdmagic` ("systemd-stub 256.6").
    pub stub: Option<String>,
}

impl Uki {
    fn osrel(&self, k: &str) -> Option<&str> {
        self.osrel
            .iter()
            .find(|(x, _)| x == k)
            .map(|(_, v)| v.as_str())
    }

    /// Whether its .osrel says `ZFSBootMenu`.
    pub(crate) fn names_zbm(&self) -> bool {
        ["NAME", "ID", "PRETTY_NAME"].iter().any(|k| {
            self.osrel(k)
                .is_some_and(|v| v.to_ascii_lowercase().contains("zfsbootmenu"))
        })
    }

    /// Its .osrel's `PRETTY_NAME`, or NAME.
    pub(crate) fn osrel_name(&self) -> Option<String> {
        self.osrel("PRETTY_NAME")
            .or_else(|| self.osrel("NAME"))
            .map(str::to_string)
    }

    /// `ZFSBootMenu`'s version, when its .osrel is `ZFSBootMenu`'s own.
    pub(crate) fn version(&self) -> Option<String> {
        self.names_zbm()
            .then(|| {
                self.osrel("VERSION")
                    .or_else(|| self.osrel("VERSION_ID"))
                    .map(str::to_string)
            })
            .flatten()
    }
}

/// Reads LEN bytes at AT of an open file (fewer at its end).
fn read_at(f: &mut std::fs::File, at: u64, len: u64) -> Option<Vec<u8>> {
    f.seek(SeekFrom::Start(at)).ok()?;
    let mut b = Vec::new();
    f.take(len).read_to_end(&mut b).ok()?;
    Some(b)
}

/// The text of a small section: NUL-terminated, trimmed.
fn text(b: &[u8]) -> String {
    let end = b.iter().position(|&c| c == 0).unwrap_or(b.len());
    String::from_utf8_lossy(&b[..end]).trim().to_string()
}

/// A unified kernel image's sections (`.linux` and `.initrd` both there),
/// read from the file at ABS: None for anything else.
pub(crate) fn read_uki(sys: &Sys, abs: &str) -> Option<Uki> {
    let mut f = std::fs::File::open(sys.p(abs)).ok()?;
    let mut head = read_at(&mut f, 0, 4096)?;
    let pe = le32(&head, 0x3c)? as u64;
    if pe > 1 << 20 {
        return None;
    }
    // The section table can lie beyond the first 4 KiB.
    let n = le16(&read_at(&mut f, pe, 24)?, 6)? as u64;
    let opt = le16(&read_at(&mut f, pe, 24)?, 20)? as u64;
    let end = pe + 24 + opt + 40 * n.min(96);
    if end > head.len() as u64 {
        head = read_at(&mut f, 0, end)?;
    }
    let sections = pe_sections(&head)?;
    let find = |n: &str| sections.iter().find(|s| s.name == n);
    let linux = find(".linux")?;
    find(".initrd")?;
    let mut small = |s: Option<&Section>| {
        s.and_then(|s| read_at(&mut f, s.offset, s.size.min(64 << 10)))
            .map(|b| text(&b))
    };
    let osrel = small(find(".osrel"))
        .map(|t| crate::boot::parse_shell_assignments(&t))
        .unwrap_or_default();
    let cmdline = small(find(".cmdline")).filter(|c| !c.is_empty());
    let stub = small(find(".sdmagic"))
        .map(|m| {
            m.trim_matches('#')
                .trim()
                .trim_start_matches("LoaderInfo:")
                .trim()
                .to_string()
        })
        .filter(|m| !m.is_empty());
    // The bzImage's setup header: "HdrS" at 0x202, then at 0x20e the
    // offset (less 0x200) of its version string.
    let hdr = read_at(&mut f, linux.offset, 0x210)?;
    let kernel = if hdr.get(0x202..0x206) == Some(b"HdrS") {
        let at = le16(&hdr, 0x20e)? as u64 + 0x200;
        (at < linux.size)
            .then(|| read_at(&mut f, linux.offset + at, 256))
            .flatten()
            .map(|b| text(&b))
            .and_then(|t| t.split_whitespace().next().map(str::to_string))
    } else {
        None
    };
    Some(Uki {
        osrel,
        cmdline,
        kernel,
        stub,
    })
}

/// One `ZFSBootMenu` image on a mounted ESP.
#[derive(Debug, Clone, PartialEq)]
pub(crate) struct Image {
    pub esp_device: String,
    pub esp_mount: String,
    /// Its path on the ESP, as `esp_inventory` lists it (`EFI/ZBM/VMLINUZ.EFI`).
    pub path: String,
    pub size: u64,
    pub sha256: String,
    pub uki: Uki,
    pub why: String,
}

impl Image {
    pub(crate) fn json(&self) -> Value {
        Obj::new()
            .set("esp_device", self.esp_device.as_str())
            .set("esp_mount", self.esp_mount.as_str())
            .set("path", self.path.as_str())
            .set("size", self.size)
            .set("sha256", self.sha256.as_str())
            .set("version", self.uki.version())
            .set("osrel", self.uki.osrel_name())
            .set("kernel", self.uki.kernel.clone())
            .set("stub", self.uki.stub.clone())
            .set("cmdline", self.uki.cmdline.clone())
            .set("why", self.why.as_str())
            .build()
    }
}

/// Why the unified image at PATH (on an ESP) is `ZFSBootMenu`'s, if it is
/// (the module's rules, less the copies).
fn zbm_why(path: &str, uki: &Uki, root_is_be: bool) -> Option<String> {
    if uki.names_zbm() {
        return Some("its .osrel names ZFSBootMenu".into());
    }
    let parts: Vec<String> = path.split('/').map(str::to_ascii_lowercase).collect();
    if let Some(d) = parts[..parts.len().saturating_sub(1)]
        .iter()
        .find(|p| *p == "zbm" || *p == "zfsbootmenu")
    {
        return Some(format!("it is in a {d} directory"));
    }
    if parts.last().is_some_and(|f| f.starts_with("zfsbootmenu")) {
        return Some("its file name is ZFSBootMenu's".into());
    }
    let words: Vec<&str> = uki
        .cmdline
        .as_deref()
        .unwrap_or("")
        .split_whitespace()
        .collect();
    if words.iter().any(|w| w.starts_with("zbm.")) {
        return Some("its embedded command line has a zbm. argument".into());
    }
    if root_is_be && !words.iter().any(|w| w.starts_with("root=")) {
        return Some("a unified kernel image whose embedded command line names no root=, on a system whose / is a ZFS boot environment".into());
    }
    None
}

/// The `ZFSBootMenu` images on the mounted ESPs (device, mount point,
/// `esp_inventory`'s contents), in the ESPs' order and their files'.
/// `ROOT_IS_BE`: the running / is a ZFS boot environment.
pub(crate) fn find_images(
    sys: &Sys,
    esps: &[(String, String, Value)],
    root_is_be: bool,
) -> Vec<Image> {
    let mut out: Vec<Image> = Vec::new();
    for (dev, mnt, inv) in esps {
        let files: Vec<&Value> = inv
            .get("files")
            .and_then(Value::as_array)
            .into_iter()
            .flatten()
            .collect();
        let path_of = |f: &Value| f.get("path").and_then(Value::as_str).map(str::to_string);
        let mut found: Vec<(String, u64, Uki, String)> = Vec::new();
        let efi = |p: &str| {
            p.to_ascii_lowercase().ends_with(".efi") && p.to_ascii_lowercase().starts_with("efi/")
        };
        for f in &files {
            let Some(p) = path_of(f).filter(|p| efi(p)) else {
                continue;
            };
            let Some(uki) = read_uki(sys, &format!("{mnt}/{p}")) else {
                continue;
            };
            if let Some(why) = zbm_why(&p, &uki, root_is_be) {
                found.push((
                    p,
                    f.get("size").and_then(Value::as_u64).unwrap_or(0),
                    uki,
                    why,
                ));
            }
        }
        // A byte copy of one (the fallback path's).
        for f in &files {
            let (Some(p), Some(of)) = (path_of(f), f.get("same_as").and_then(Value::as_str)) else {
                continue;
            };
            if found.iter().any(|(x, ..)| *x == p) {
                continue;
            }
            if let Some(src) = found.iter().find(|(x, ..)| x == of).cloned() {
                found.push((p, src.1, src.2, format!("a copy of {of}")));
            }
        }
        let order = |p: &str| {
            files
                .iter()
                .position(|f| path_of(f).as_deref() == Some(p))
                .unwrap_or(usize::MAX)
        };
        found.sort_by_key(|(p, ..)| order(p));
        for (path, size, uki, why) in found {
            let sha256 = sys
                .read_bytes(&format!("{mnt}/{path}"))
                .map(|b| crate::bytes::sha256_hex(&b))
                .unwrap_or_default();
            out.push(Image {
                esp_device: dev.clone(),
                esp_mount: mnt.clone(),
                path,
                size,
                sha256,
                uki,
                why,
            });
        }
    }
    out
}

/// The files in a /boot directory `ZFSBootMenu` takes for kernels and
/// initramfs images (its own patterns: vmlinuz*, vmlinux*, linux*,
/// kernel*; initramfs*, initrd*).
fn boot_files(sys: &Sys, dir: &str) -> (Vec<String>, Vec<String>) {
    let mut kernels = Vec::new();
    let mut initramfs = Vec::new();
    for f in sys.list(dir) {
        if !sys.is_file(&format!("{dir}/{f}")) {
            continue;
        }
        if ["vmlinuz", "vmlinux", "linux", "kernel"]
            .iter()
            .any(|p| f.starts_with(p))
        {
            kernels.push(f);
        } else if f.starts_with("initramfs") || f.starts_with("initrd") {
            initramfs.push(f);
        }
    }
    (kernels, initramfs)
}

/// An inactive BE's /boot listing: its dataset (or the child mounted at
/// /boot) mounted read-only under dump's run directory, read, unmounted at
/// once.  None (said) when it can't be.
fn inactive_boot(sys: &Sys, be: &BootEnv) -> Option<(Vec<String>, Vec<String>)> {
    if !sys.privileged || !safe_name(&be.name, b"-_.+") {
        return None;
    }
    let (ds, legacy, rel) = match be.children.iter().find(|c| c.mountpoint == "/boot") {
        Some(c) => (c.dataset.as_str(), c.legacy(), ""),
        None => (be.dataset.as_str(), be.legacy(), "/boot"),
    };
    // Where dump mounts the inactive BE, read-only, to list its /boot:
    // `/run/bko-holt/zbm-be-NAME`.
    let at = format!("{RUN_DIR}/zbm-be-{}", be.name);
    let words = RoMountWords {
        cant_make: format!(
            "ZFSBootMenu: can't make {at} to read boot environment {}'s /boot",
            be.name
        ),
        mount_failed: format!(
            "ZFSBootMenu: a read-only mount of {ds} at {at} failed, so boot.zfsbootmenu doesn't list boot environment {}'s kernels",
            be.name
        ),
        still_mounted: format!(
            "ZFSBootMenu: {ds} is still mounted read-only at {at}; `umount {at}` when this dump is done"
        ),
    };
    sys.with_ro_mount(
        "zfs",
        &crate::zfs_be::ro_options(legacy),
        ds,
        &at,
        &words,
        |at| boot_files(sys, &format!("{at}{rel}")),
    )
}

/// A dataset's local or inherited user property K, from the dump's
/// tools.datasets (the nearest ancestor that sets it), with where it was
/// set.
fn user_prop(tools: &Value, ds: &str, k: &str) -> Option<(String, String)> {
    let datasets = tools.get("datasets").and_then(Value::as_array)?;
    let mut name = ds;
    loop {
        if let Some(v) = datasets
            .iter()
            .find(|d| d.get("name").and_then(Value::as_str) == Some(name))
            .and_then(|d| d.at(&["properties", k, "value"]))
            .and_then(Value::as_str)
        {
            return Some((v.to_string(), name.to_string()));
        }
        name = name.rsplit_once('/')?.0;
    }
}

/// Whether the running / is a ZFS boot environment (`ZFSBootMenu`'s rule).
pub(crate) fn root_is_be(bes: &[BootEnv]) -> bool {
    bes.iter().any(|b| b.rule == Rule::ZfsBootMenu && b.mounted)
}

/// boot.zfsbootmenu: the images, the pools holding BEs (with their
/// bootfs), and the BEs (`crate::zfs_be::list` of the dump's `tools`).
pub(crate) fn json(sys: &Sys, images: &[Image], tools: &Value) -> Value {
    let bes: Vec<BootEnv> = crate::zfs_be::list(tools)
        .into_iter()
        .filter(|b| b.rule == Rule::ZfsBootMenu)
        .collect();
    let mut pools: Vec<String> = Vec::new();
    for b in &bes {
        if !pools.contains(&b.pool) {
            pools.push(b.pool.clone());
        }
    }
    let bootfs = |pool: &str| {
        tools
            .get("imported_pools")
            .and_then(Value::as_array)
            .into_iter()
            .flatten()
            .find(|p| p.get("name").and_then(Value::as_str) == Some(pool))
            .and_then(|p| p.at(&["properties", "bootfs"]))
            .and_then(Value::as_str)
            .filter(|b| *b != "-")
            .map(str::to_string)
    };
    let pools_json: Vec<Value> = pools
        .iter()
        .map(|p| {
            Obj::new()
                .set("name", p.as_str())
                .set("bootfs", bootfs(p))
                .build()
        })
        .collect();
    let mut list = Vec::new();
    for be in &bes {
        let files = if be.locked {
            None
        } else if !be.inactive() {
            Some(boot_files(sys, "/boot"))
        } else {
            inactive_boot(sys, be)
        };
        let cl = user_prop(tools, &be.dataset, COMMANDLINE);
        list.push(
            Obj::new()
                .set("name", be.name.as_str())
                .set("dataset", be.dataset.as_str())
                .set("pool", be.pool.as_str())
                .set("bootfs", be.bootfs)
                .set("mounted", be.mounted)
                .set("origin", be.origin.clone())
                .set("commandline", cl.as_ref().map(|(v, _)| v.clone()))
                .set("commandline_from", cl.map(|(_, d)| d))
                .set("locked", be.locked)
                .set("read", files.is_some())
                .set(
                    "kernels",
                    files.as_ref().map(|f| f.0.clone()).unwrap_or_default(),
                )
                .set("initramfs", files.map(|f| f.1).unwrap_or_default())
                .build(),
        );
    }
    Obj::new()
        .set("images", images.iter().map(Image::json).collect::<Vec<_>>())
        .set("pools", pools_json)
        .set("bes", list)
        .build()
}

/// Test helpers: a tiny unified kernel image.
#[cfg(test)]
pub(crate) mod sample {
    /// A PE image with systemd-stub's sections: `.sdmagic`, `.osrel`
    /// (OSREL), `.cmdline` (CMDLINE), `.initrd` and a `.linux` whose
    /// bzImage header gives KERNEL as its version.  `uki` false leaves out
    /// `.linux` and `.initrd` (a plain EFI program).
    pub(crate) fn uki(osrel: &str, cmdline: &str, kernel: &str, uki: bool) -> Vec<u8> {
        let mut linux = vec![0u8; 0x400];
        linux[0x202..0x206].copy_from_slice(b"HdrS");
        linux[0x20e..0x210].copy_from_slice(&0x100u16.to_le_bytes());
        let v = format!("{kernel} (builder@host) #1 SMP\0");
        linux[0x300..0x300 + v.len()].copy_from_slice(v.as_bytes());
        let mut secs: Vec<(&str, Vec<u8>)> = vec![
            (".text", vec![0xc3; 16]),
            (
                ".sdmagic",
                b"#### LoaderInfo: systemd-stub 256.6 ####\0".to_vec(),
            ),
            (".osrel", osrel.as_bytes().to_vec()),
            (".cmdline", cmdline.as_bytes().to_vec()),
        ];
        if uki {
            secs.push((".initrd", vec![0x28, 0xb5, 0x2f, 0xfd, 0, 0, 0, 0]));
            secs.push((".linux", linux));
        }
        let pe = 0x80usize;
        let opt = 240usize;
        let table = pe + 24 + opt;
        let mut at = (table + 40 * secs.len()).div_ceil(0x200) * 0x200;
        let mut img = vec![0u8; at];
        img[0..2].copy_from_slice(b"MZ");
        img[0x3c..0x40].copy_from_slice(&(pe as u32).to_le_bytes());
        img[pe..pe + 4].copy_from_slice(b"PE\0\0");
        img[pe + 4..pe + 6].copy_from_slice(&0x8664u16.to_le_bytes());
        img[pe + 6..pe + 8].copy_from_slice(&(secs.len() as u16).to_le_bytes());
        img[pe + 20..pe + 22].copy_from_slice(&(opt as u16).to_le_bytes());
        for (i, (name, data)) in secs.iter().enumerate() {
            let e = table + 40 * i;
            img[e..e + name.len()].copy_from_slice(name.as_bytes());
            img[e + 8..e + 12].copy_from_slice(&(data.len() as u32).to_le_bytes());
            let raw = data.len().div_ceil(0x200) * 0x200;
            img[e + 16..e + 20].copy_from_slice(&(raw as u32).to_le_bytes());
            img[e + 20..e + 24].copy_from_slice(&(at as u32).to_le_bytes());
            img.resize(at + raw, 0);
            img[at..at + data.len()].copy_from_slice(data);
            at += raw;
        }
        img
    }

    /// The v3.1.0 release EFI's .osrel and .cmdline (Void's os-release, as
    /// the release is built in a Void container), with zbm-kcl's serial
    /// console added as the void-zbm guest has it.
    pub(crate) const RELEASE_OSREL: &str = "NAME=\"Void\"\nID=\"void\"\nPRETTY_NAME=\"Void Linux\"\nHOME_URL=\"https://voidlinux.org/\"\nDOCUMENTATION_URL=\"https://docs.voidlinux.org/\"\nLOGO=\"void-logo\"\nANSI_COLOR=\"0;38;2;71;128;97\"\n\nDISTRIB_ID=\"void\"\n";
    pub(crate) const RELEASE_CMDLINE: &str =
        "quiet loglevel=0 nomodeset console=ttyS0,115200n8 zbm.timeout=5";
    pub(crate) const RELEASE_KERNEL: &str = "6.12.63_1";
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::testutil::Tree;

    #[test]
    fn pe_sections_and_uki() {
        let img = sample::uki(
            sample::RELEASE_OSREL,
            sample::RELEASE_CMDLINE,
            sample::RELEASE_KERNEL,
            true,
        );
        let names: Vec<String> = pe_sections(&img)
            .unwrap()
            .into_iter()
            .map(|s| s.name)
            .collect();
        assert_eq!(
            names,
            [
                ".text", ".sdmagic", ".osrel", ".cmdline", ".initrd", ".linux"
            ]
        );
        assert!(pe_sections(b"MZ not a PE").is_none());
        assert!(pe_sections(&[0u8; 512]).is_none());
        let t = Tree::new("zbm-pe");
        t.file("/esp/EFI/ZBM/VMLINUZ.EFI", &img)
            .file(
                "/esp/EFI/systemd/systemd-bootx64.efi",
                sample::uki("NAME=x\n", "", "", false),
            )
            .file(
                "/esp/EFI/Linux/debian.efi",
                sample::uki(
                    "NAME=\"Debian GNU/Linux\"\n",
                    "root=UUID=1 ro",
                    "6.12.1-amd64",
                    true,
                ),
            )
            .file(
                "/esp/EFI/other/zbm.efi",
                sample::uki(
                    "NAME=\"ZFSBootMenu\"\nVERSION=\"3.2.0\"\n",
                    "",
                    "6.18.1",
                    true,
                ),
            )
            .file("/esp/EFI/junk.efi", "MZ");
        let sys = Sys::new(t.root(), Box::new(crate::sys::FakeRunner::default()), true);
        let u = read_uki(&sys, "/esp/EFI/ZBM/VMLINUZ.EFI").unwrap();
        assert_eq!(u.osrel_name().as_deref(), Some("Void Linux"));
        assert_eq!(u.cmdline.as_deref(), Some(sample::RELEASE_CMDLINE));
        assert_eq!(u.kernel.as_deref(), Some(sample::RELEASE_KERNEL));
        assert_eq!(u.stub.as_deref(), Some("systemd-stub 256.6"));
        assert!(!u.names_zbm() && u.version().is_none());
        assert!(read_uki(&sys, "/esp/EFI/systemd/systemd-bootx64.efi").is_none());
        assert!(read_uki(&sys, "/esp/EFI/junk.efi").is_none());
        assert_eq!(
            read_uki(&sys, "/esp/EFI/other/zbm.efi")
                .unwrap()
                .version()
                .as_deref(),
            Some("3.2.0")
        );
        // The rules: directory, .osrel, zbm. argument, no root= on a BE root.
        let inv = crate::boot::esp_inventory(&sys, "/esp");
        let esps = vec![("vda1".to_string(), "/esp".to_string(), inv)];
        let got: Vec<(String, String)> = find_images(&sys, &esps, false)
            .into_iter()
            .map(|i| (i.path, i.why))
            .collect();
        assert_eq!(
            got,
            vec![
                (
                    "EFI/ZBM/VMLINUZ.EFI".to_string(),
                    "it is in a zbm directory".to_string()
                ),
                (
                    "EFI/other/zbm.efi".to_string(),
                    "its .osrel names ZFSBootMenu".to_string()
                )
            ]
        );
        let img = find_images(&sys, &esps, false).remove(0);
        assert_eq!(
            img.sha256,
            crate::bytes::sha256_hex(&sample::uki(
                sample::RELEASE_OSREL,
                sample::RELEASE_CMDLINE,
                sample::RELEASE_KERNEL,
                true
            ))
        );
        assert_eq!(img.json().get("version"), Some(&Value::Null));
        let u = Uki {
            cmdline: Some("zbm.prefer=zroot".into()),
            ..Uki::default()
        };
        assert_eq!(
            zbm_why("EFI/BOOT/BOOTX64.EFI", &u, false).as_deref(),
            Some("its embedded command line has a zbm. argument")
        );
        let u = Uki {
            cmdline: Some("quiet".into()),
            ..Uki::default()
        };
        assert!(zbm_why("EFI/BOOT/BOOTX64.EFI", &u, false).is_none());
        assert!(zbm_why("EFI/BOOT/BOOTX64.EFI", &u, true).is_some());
        let u = Uki {
            cmdline: Some("root=zfs:zroot/ROOT/void".into()),
            ..Uki::default()
        };
        assert!(zbm_why("EFI/Linux/void.efi", &u, true).is_none());
        assert_eq!(
            zbm_why(
                "EFI/zfsbootmenu-release-x86_64-v3.1.0-linux6.12.EFI",
                &Uki::default(),
                false
            )
            .as_deref(),
            Some("its file name is ZFSBootMenu's")
        );
        assert_eq!(
            zbm_why("EFI/zbm/vmlinuz-backup.EFI", &Uki::default(), false).as_deref(),
            Some("it is in a zbm directory")
        );
    }
}
