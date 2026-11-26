//! The boot setup above the firmware: which loader boots the system and how
//! it's configured (GRUB, systemd-boot, shim), what's on the EFI system
//! partitions, the kernels and initramfs images in /boot, the kernel command
//! line, and the Debian packages behind all of it.

use crate::json::{Obj, Value};
use crate::sys::Sys;

// --- Package databases (boot/packages.rs) ---------------------------------------

mod packages;
pub(crate) use packages::*;

// --- Shell-ish text -------------------------------------------------------------

/// Splits one line into words the way sh would for plain quoting: '...',
/// "..." (with \\ \" \$ escapes), and backslash escapes outside quotes.
/// Words glued to quotes ('a'\''b') come out as one word.
pub(crate) fn shell_words(line: &str) -> Vec<String> {
    let mut words = Vec::new();
    let mut cur = String::new();
    let mut in_word = false;
    let mut it = line.chars().peekable();
    while let Some(c) = it.next() {
        match c {
            ' ' | '\t' => {
                if in_word {
                    words.push(std::mem::take(&mut cur));
                    in_word = false;
                }
            }
            '\'' => {
                in_word = true;
                for d in it.by_ref() {
                    if d == '\'' {
                        break;
                    }
                    cur.push(d);
                }
            }
            '"' => {
                in_word = true;
                while let Some(d) = it.next() {
                    match d {
                        '"' => break,
                        '\\' if matches!(it.peek(), Some('"' | '\\' | '$' | '`')) => {
                            if let Some(esc) = it.next() {
                                cur.push(esc);
                            }
                        }
                        d => cur.push(d),
                    }
                }
            }
            '\\' => {
                in_word = true;
                if let Some(d) = it.next() {
                    cur.push(d);
                }
            }
            '#' if !in_word => break,
            c => {
                in_word = true;
                cur.push(c);
            }
        }
    }
    if in_word {
        words.push(cur);
    }
    words
}

/// KEY=value assignments from a shell file like /etc/default/grub, values
/// unquoted; later assignments win.  Command substitutions are kept as
/// written.
pub(crate) fn parse_shell_assignments(text: &str) -> Vec<(String, String)> {
    let mut out: Vec<(String, String)> = Vec::new();
    for line in text.lines() {
        let line = line.trim();
        let line = line.strip_prefix("export ").unwrap_or(line);
        let Some((k, v)) = line.split_once('=') else {
            continue;
        };
        if k.is_empty()
            || !k.chars().all(|c| c.is_ascii_alphanumeric() || c == '_')
            || k.starts_with('#')
        {
            continue;
        }
        let value = if v.starts_with('`') || v.starts_with("$(") {
            v.to_string()
        } else {
            shell_words(v).join(" ")
        };
        out.retain(|(key, _)| key != k);
        out.push((k.to_string(), value));
    }
    out
}

fn pairs_obj(pairs: &[(String, String)]) -> Value {
    let mut o = Obj::new();
    for (k, v) in pairs {
        o.put(k, v.as_str());
    }
    o.build()
}

// --- GRUB -------------------------------------------------------------------------

#[derive(Debug, Default, Clone, PartialEq)]
pub(crate) struct GrubCfg {
    /// Titles, submenu entries as "Submenu>Entry".
    pub entries: Vec<String>,
    pub search_uuids: Vec<String>,
    pub modules: Vec<String>,
    /// The first entry's `linux` line: kernel path and arguments.
    pub default_linux: Option<String>,
    pub default_initrd: Option<String>,
    pub prefix: Option<String>,
    pub configfile: Option<String>,
    /// `set lang=...`: grub-mkconfig writes it (with `insmod gettext`) when
    /// it ran with LANG set to something other than C.
    pub lang: Option<String>,
}

pub(crate) fn parse_grub_cfg(text: &str) -> GrubCfg {
    let mut g = GrubCfg::default();
    // Open blocks: Some(title) for submenus, None for other braces.
    let mut stack: Vec<Option<String>> = Vec::new();
    let mut in_entry = 0usize;
    let mut first_entry_done = false;
    for raw in text.lines() {
        let w = shell_words(raw.trim());
        let Some(cmd) = w.first().map(String::as_str) else {
            continue;
        };
        let opens = raw.trim_end().ends_with('{');
        match cmd {
            "menuentry" | "submenu" => {
                let title = w.get(1).cloned().unwrap_or_default();
                if cmd == "menuentry" {
                    let mut path: Vec<&str> = stack.iter().flatten().map(String::as_str).collect();
                    path.push(&title);
                    g.entries.push(path.join(">"));
                    if opens {
                        in_entry = stack.len() + 1;
                    }
                }
                if opens {
                    stack.push(if cmd == "submenu" { Some(title) } else { None });
                }
                continue;
            }
            "}" => {
                if stack.len() == in_entry && in_entry > 0 {
                    in_entry = 0;
                    if !g.entries.is_empty() {
                        first_entry_done = true;
                    }
                }
                stack.pop();
                continue;
            }
            "insmod" => {
                for m in &w[1..] {
                    if !g.modules.contains(m) {
                        g.modules.push(m.clone());
                    }
                }
            }
            "search" | "search.fs_uuid" => {
                // "search ... --fs-uuid ... UUID" or "search.fs_uuid UUID var".
                let uuid = if cmd == "search.fs_uuid" {
                    w.get(1).cloned()
                } else if w.iter().any(|x| x == "--fs-uuid" || x == "-u") {
                    w.iter().skip(1).rfind(|x| !x.starts_with('-')).cloned()
                } else {
                    None
                };
                if let Some(u) = uuid {
                    if !g.search_uuids.contains(&u) {
                        g.search_uuids.push(u);
                    }
                }
            }
            "linux" | "linuxefi"
                if in_entry > 0 && !first_entry_done && g.default_linux.is_none() =>
            {
                g.default_linux = Some(w[1..].join(" "));
            }
            "initrd" | "initrdefi"
                if in_entry > 0 && !first_entry_done && g.default_initrd.is_none() =>
            {
                g.default_initrd = Some(w[1..].join(" "));
            }
            "set" => {
                if let Some(p) = w.get(1).and_then(|x| x.strip_prefix("prefix=")) {
                    g.prefix = Some(p.to_string());
                }
                if let (Some(l), true) = (
                    w.get(1).and_then(|x| x.strip_prefix("lang=")),
                    stack.is_empty(),
                ) {
                    g.lang = Some(l.to_string());
                }
            }
            "configfile" => g.configfile = w.get(1).cloned(),
            _ => {}
        }
        if opens && !matches!(cmd, "menuentry" | "submenu") {
            stack.push(None);
        }
    }
    g
}

/// grub-editenv's block: "# GRUB Environment Block" then key=value lines,
/// padded with '#'.
pub(crate) fn parse_grubenv(text: &str) -> Vec<(String, String)> {
    text.lines()
        .filter(|l| !l.starts_with('#'))
        .filter_map(|l| {
            l.split_once('=')
                .map(|(k, v)| (k.to_string(), v.to_string()))
        })
        .collect()
}

fn grub_json(sys: &Sys) -> Value {
    let mut o = Obj::new();
    // Fedora's GRUB lives in /boot/grub2.
    let dir = if !sys.is_dir("/boot/grub") && sys.is_dir("/boot/grub2") {
        "/boot/grub2"
    } else {
        "/boot/grub"
    };
    o.put("dir", dir);
    // Ubuntu's signed images' directory (x86_64-efi-signed), not a platform.
    let signed = format!("{}-signed", crate::arch::names_of(sys).grub_efi_target);
    let platforms: Vec<String> = sys
        .list(dir)
        .into_iter()
        .filter(|d| sys.is_dir(&format!("{dir}/{d}")) && d.contains('-') && *d != signed)
        .filter(|d| {
            sys.is_file(&format!("{dir}/{d}/normal.mod"))
                || sys.is_file(&format!("{dir}/{d}/core.img"))
                || sys.is_file(&format!("{dir}/{d}/core.efi"))
        })
        .collect();
    o.put("installed_platforms", platforms);
    match sys.read(&format!("{dir}/grub.cfg")) {
        Some(t) => {
            let g = parse_grub_cfg(&t);
            o.put(
                "grub_cfg",
                Obj::new()
                    .set("entries", g.entries)
                    .set("search_uuids", g.search_uuids)
                    .set("modules", g.modules)
                    .set("default_linux", g.default_linux)
                    .set("default_initrd", g.default_initrd)
                    .set("lang", g.lang),
            );
        }
        None => {
            if sys.exists(&format!("{dir}/grub.cfg")) {
                sys.warn(format!("{dir}/grub.cfg: not readable (run as root)"));
                o.put("grub_cfg", "unreadable");
            } else {
                o.put("grub_cfg", Value::Null);
            }
        }
    }
    o.put(
        "grubenv",
        sys.read(&format!("{dir}/grubenv"))
            .map(|t| pairs_obj(&parse_grubenv(&t))),
    );
    let mut defaults = sys
        .read("/etc/default/grub")
        .map(|t| parse_shell_assignments(&t))
        .unwrap_or_default();
    for f in sys.list("/etc/default/grub.d") {
        if f.ends_with(".cfg") {
            for (k, v) in sys
                .read(&format!("/etc/default/grub.d/{f}"))
                .map(|t| parse_shell_assignments(&t))
                .unwrap_or_default()
            {
                defaults.retain(|(key, _)| *key != k);
                defaults.push((k, v));
            }
        }
    }
    o.put("default_grub", pairs_obj(&defaults));
    o.put(
        "debconf",
        sys.read("/var/cache/debconf/config.dat")
            .map(|t| pairs_obj(&parse_debconf(&t, &["grub-pc/", "grub2/", "grub-efi/"]))),
    );
    o.build()
}

// --- EFI system partitions --------------------------------------------------------

/// Files under an ESP's EFI/ and loader/ directories (relative paths, sizes),
/// noting which ones are byte-identical copies (the removable-media fallback
/// EFI/BOOT/BOOTX64.EFI usually is one); then the kernels and initrds its
/// loader/entries name in other directories (sdbootutil's
/// /aeon/KVER/linux-HASH, kernel-install's /MACHINE-ID/KVER/linux), which
/// load's plan checks a read-only system's entries against
/// (load/transactional.rs).  Not those at its top: a /boot that is the ESP
/// keeps every kernel there, and boot.kernels lists them.
pub(crate) fn esp_inventory(sys: &Sys, mount: &str) -> Value {
    let mut files: Vec<(String, u64)> = Vec::new();
    fn walk(sys: &Sys, base: &str, rel: &str, depth: usize, out: &mut Vec<(String, u64)>) {
        if depth > 4 || out.len() >= 500 {
            return;
        }
        let dir = if rel.is_empty() {
            base.to_string()
        } else {
            format!("{base}/{rel}")
        };
        for e in sys.list(&dir) {
            let r = if rel.is_empty() {
                e.clone()
            } else {
                format!("{rel}/{e}")
            };
            let p = format!("{base}/{r}");
            if sys.is_dir(&p) {
                walk(sys, base, &r, depth + 1, out);
            } else if let Some(sz) = sys.file_size(&p) {
                out.push((r, sz));
            }
        }
    }
    for top in sys.list(mount) {
        if top.eq_ignore_ascii_case("EFI") || top.eq_ignore_ascii_case("loader") {
            walk(sys, mount, &top, 0, &mut files);
        }
    }
    let entries = format!("{mount}/loader/entries");
    for f in sys
        .list(&entries)
        .into_iter()
        .filter(|f| f.ends_with(".conf"))
    {
        for l in sys
            .read(&format!("{entries}/{f}"))
            .unwrap_or_default()
            .lines()
        {
            let mut w = l.split_whitespace();
            let (Some("linux" | "initrd"), Some(p)) = (w.next(), w.next()) else {
                continue;
            };
            let r = p.trim_start_matches('/').to_string();
            if !r.contains('/')
                || r.split('/').any(|c| c.is_empty() || c == "..")
                || files.iter().any(|(x, _)| x.eq_ignore_ascii_case(&r))
                || files.len() >= 500
            {
                continue;
            }
            if let Some(sz) = sys.file_size(&format!("{mount}/{r}")) {
                files.push((r, sz));
            }
        }
    }
    let efi: Vec<&(String, u64)> = files
        .iter()
        .filter(|(p, _)| p.to_ascii_lowercase().ends_with(".efi"))
        .collect();
    let mut list = Vec::new();
    for (path, size) in &files {
        let mut o = Obj::new().set("path", path.as_str()).set("size", *size);
        if path.to_ascii_lowercase().ends_with(".efi") && *size <= 64 << 20 {
            let others = efi
                .iter()
                .filter(|(p, _)| p != path)
                .map(|(p, s)| (p.as_str(), *s));
            o.put_some(
                "same_as",
                byte_copy_of(sys, &format!("{mount}/{path}"), *size, others, mount),
            );
        }
        list.push(o.build());
    }
    let find = |want: &str| {
        files
            .iter()
            .find(|(p, _)| p.eq_ignore_ascii_case(want))
            .map(|(p, _)| p.clone())
    };
    let stub = find("EFI/debian/grub.cfg")
        .and_then(|p| sys.read(&format!("{mount}/{p}")))
        .map(|t| parse_grub_cfg(&t));
    Obj::new()
        .set("files", list)
        .set(
            "debian_grub_stub",
            stub.map(|g| {
                Obj::new()
                    .set("search_uuids", g.search_uuids)
                    .set("prefix", g.prefix)
                    .set("configfile", g.configfile)
            }),
        )
        .build()
}

/// The first of `candidates` (a path under `under`, or an absolute path when
/// `under` is empty, and its size) that the file at `path`, of `size` bytes,
/// is a byte copy of.  Only a candidate of the same size is compared, a MiB
/// at a time (`Sys::same_bytes`) and stopping at the first MiB that differs,
/// so neither file is ever held in memory whole and a different one is
/// seldom read past its first MiB.
fn byte_copy_of<'a>(
    sys: &Sys,
    path: &str,
    size: u64,
    candidates: impl IntoIterator<Item = (&'a str, u64)>,
    under: &str,
) -> Option<&'a str> {
    candidates
        .into_iter()
        .filter(|&(_, n)| n == size)
        .map(|(p, _)| p)
        .find(|p| {
            let full = if under.is_empty() {
                (*p).to_string()
            } else {
                format!("{under}/{p}")
            };
            sys.same_bytes(path, &full) == Some(true)
        })
}

/// systemd-boot's loader.conf and its Boot Loader Specification entries,
/// each as an object of its `key value` lines in file order.
///
/// A key the file repeats keeps its first line's place and, mostly, its
/// last line's value; the Boot Loader Specification lets an entry repeat
/// two keys, and those say all their lines.  Several `options` lines are
/// one command line, their values joined with spaces in file order, as
/// systemd-boot joins them.  Several `initrd` lines are all loaded, in
/// order: `initrd` stays the last line's (what an older dump said, the
/// main initramfs after a microcode image), and `initrds`, after it, has
/// every one: an entry with `initrd /intel-ucode.img` then `initrd
/// /initramfs-linux.img` dumps initrd "/initramfs-linux.img" and initrds
/// ["/intel-ucode.img", "/initramfs-linux.img"].  An entry with one initrd
/// line has no `initrds`.  load's ESP check reads both
/// (load/transactional.rs, `bls_problems`).
pub(crate) fn systemd_boot_json(sys: &Sys, mount: &str) -> Option<Value> {
    let conf = sys.read(&format!("{mount}/loader/loader.conf"));
    let entries_dir = format!("{mount}/loader/entries");
    let entry_files: Vec<String> = sys
        .list(&entries_dir)
        .into_iter()
        .filter(|f| f.ends_with(".conf"))
        .collect();
    if conf.is_none() && entry_files.is_empty() {
        return None;
    }
    // BLS: an entry (not loader.conf) whose options and initrd lines repeat.
    let kv = |t: &str, bls: bool| {
        let mut o = Obj::new();
        let (mut options, mut initrds): (Vec<&str>, Vec<&str>) = (Vec::new(), Vec::new());
        for l in t.lines() {
            let l = l.trim();
            if l.is_empty() || l.starts_with('#') {
                continue;
            }
            let (k, v) = l.split_once(char::is_whitespace).unwrap_or((l, ""));
            let v = v.trim();
            match k {
                "options" if bls => {
                    options.extend((!v.is_empty()).then_some(v));
                    o.put(k, options.join(" "));
                }
                "initrd" if bls => {
                    initrds.extend((!v.is_empty()).then_some(v));
                    o.put(k, v);
                }
                _ => o.put(k, v),
            }
        }
        if initrds.len() > 1 {
            let at =
                o.0.iter()
                    .position(|(k, _)| k == "initrd")
                    .map_or(o.0.len(), |i| i + 1);
            o.0.insert(at, ("initrds".into(), Value::from(initrds)));
        }
        o.build()
    };
    let entries: Vec<Value> = entry_files
        .iter()
        .filter_map(|f| sys.read(&format!("{entries_dir}/{f}")).map(|t| (f, t)))
        .map(|(f, t)| {
            let mut v = kv(&t, true);
            if let Value::Obj(o) = &mut v {
                o.insert(0, ("file".into(), Value::from(f.as_str())));
            }
            v
        })
        .collect();
    Some(
        Obj::new()
            .set("loader_conf", conf.map(|t| kv(&t, false)))
            .set("entries", entries)
            .build(),
    )
}

// --- Proxmox VE's proxmox-boot-tool ------------------------------------------------

/// proxmox-boot-tool's list of the ESPs it keeps, one vfat volume ID a line.
pub(crate) const PROXMOX_BOOT_UUIDS: &str = "/etc/kernel/proxmox-boot-uuids";
/// Where dump mounts each of those ESPs, read-only, while it reads it.
pub(crate) const UNMOUNTED_ESP_MOUNTS: &str = crate::sys::RUN_DIR;

/// One ESP proxmox-boot-tool keeps: its volume ID, the partition it is
/// (None: nothing has that ID), and what a read of it found (`mode` is
/// proxmox-boot-tool's: "systemd-boot", "grub-efi" or "grub-bios").
pub(crate) struct ProxmoxEsp {
    pub uuid: String,
    pub device: Option<String>,
    pub mode: Option<&'static str>,
    pub contents: Value,
    pub systemd_boot: Option<Value>,
    /// Where it was already mounted, when it was (then boot.esps has it).
    pub mounted_at: Option<String>,
}

/// `boot.proxmox_boot`, and its ESPs for boot.esps.
pub(crate) struct ProxmoxBoot {
    pub json: Value,
    pub esps: Vec<ProxmoxEsp>,
}

/// "Proxmox VE 9.2" from pve-manager's version (9.2.3), when it is installed.
pub(crate) fn proxmox_product(packages: &[Package]) -> Option<String> {
    let v = &find_package(packages, "pve-manager")?.version;
    let nums: Vec<&str> = v
        .split(|c: char| !c.is_ascii_digit())
        .filter(|n| !n.is_empty())
        .take(2)
        .collect();
    (!nums.is_empty()).then(|| format!("Proxmox VE {}", nums.join(".")))
}

/// The proxmox-boot-tool mode an ESP's files say: systemd-boot's loader,
/// GRUB's EFI image as proxmox-boot-tool names it, or GRUB's BIOS modules.
fn proxmox_mode(sys: &Sys, mount: &str, contents: &Value) -> Option<&'static str> {
    let has = |want: &str| {
        contents
            .get("files")
            .and_then(Value::as_array)
            .into_iter()
            .flatten()
            .any(|f| {
                f.get("path")
                    .and_then(Value::as_str)
                    .is_some_and(|p| p.eq_ignore_ascii_case(want))
            })
    };
    // The machine's names (crate::arch): systemd-bootx64.efi, grubx64.efi.
    let n = crate::arch::names_of(sys);
    if has(&format!("EFI/systemd/{}", n.systemd_boot)) {
        Some("systemd-boot")
    } else if has(&format!("EFI/proxmox/{}", n.grub)) {
        Some("grub-efi")
    } else if sys.is_dir(&format!("{mount}/grub/i386-pc")) {
        Some("grub-bios")
    } else {
        None
    }
}

/// F(AT) with the ESP DEV (a kernel name) mounted read-only at AT, a
/// directory dump makes under its run directory and removes after: the
/// way dump reads an ESP the running system doesn't mount (Proxmox's,
/// `TrueNAS`'s), as their own tools mount them only while they work.  None
/// when it can't be mounted, said with WHO (how the warnings name the ESP)
/// and WHAT (what the dump then lacks).
pub(crate) fn read_unmounted_esp<R>(
    sys: &Sys,
    who: &str,
    dev: &str,
    at: &str,
    what: &str,
    f: impl FnOnce(&str) -> R,
) -> Option<R> {
    let words = crate::sys::RoMountWords {
        cant_make: format!("ESP {who}: can't make {at} to read it"),
        mount_failed: format!(
            "ESP {who}: a read-only mount at {at} failed, so {what} aren't in this dump"
        ),
        still_mounted: format!(
            "ESP {who}: still mounted read-only at {at}; `umount {at}` when this dump is done"
        ),
    };
    sys.with_ro_mount(
        "vfat",
        "ro,noexec,nodev,nosuid",
        &format!("/dev/{dev}"),
        at,
        &words,
        f,
    )
}

/// `boot.proxmox_boot` (docs/design/proxmox.md): on Proxmox VE with
/// proxmox-boot-tool (ZFS root), the ESPs aren't mounted, only listed by
/// volume ID in /etc/kernel/proxmox-boot-uuids.  Each is found by that ID
/// (`resolve`, "UUID=ID" to a kernel name) and read as a mounted ESP is,
/// from a private read-only mount under /run/bko-holt, unmounted at once
/// (as proxmox-boot-tool itself mounts them); one already mounted
/// (`mounted`, device and mount point) is read where it is.  None when the
/// list or proxmox-kernel-helper isn't there.
pub(crate) fn proxmox_boot_json(
    sys: &Sys,
    packages: &[Package],
    resolve: &dyn Fn(&str) -> Option<String>,
    mounted: &[(String, String)],
) -> Option<ProxmoxBoot> {
    if !has_package(packages, "proxmox-kernel-helper") {
        return None;
    }
    let text = sys.read(PROXMOX_BOOT_UUIDS)?;
    let uuids: Vec<String> = text
        .lines()
        .map(str::trim)
        .filter(|l| !l.is_empty() && !l.starts_with('#'))
        .map(str::to_string)
        .collect();
    let mut esps = Vec::new();
    for uuid in &uuids {
        let device = resolve(&format!("UUID={uuid}"));
        let mut e = ProxmoxEsp {
            uuid: uuid.clone(),
            device: device.clone(),
            mode: None,
            contents: Value::Null,
            systemd_boot: None,
            mounted_at: None,
        };
        let Some(dev) = device else {
            sys.warn(format!("{PROXMOX_BOOT_UUIDS} lists ESP {uuid}, but no file system here has that volume ID (proxmox-boot-tool status says the same)"));
            esps.push(e);
            continue;
        };
        if let Some((_, m)) = mounted.iter().find(|(d, _)| *d == dev) {
            e.contents = esp_inventory(sys, m);
            e.mode = proxmox_mode(sys, m, &e.contents);
            e.systemd_boot = systemd_boot_json(sys, m);
            e.mounted_at = Some(m.clone());
            esps.push(e);
            continue;
        }
        // The ID names the mount point: only a volume ID's characters.
        if !sys.privileged
            || uuid.is_empty()
            || !uuid.chars().all(|c| c.is_ascii_hexdigit() || c == '-')
        {
            esps.push(e);
            continue;
        }
        let at = format!("{UNMOUNTED_ESP_MOUNTS}/esp-{uuid}");
        let read = read_unmounted_esp(
            sys,
            &format!("{uuid} ({dev})"),
            &dev,
            &at,
            "its files and proxmox-boot-tool mode",
            |at| {
                let contents = esp_inventory(sys, at);
                let mode = proxmox_mode(sys, at, &contents);
                (contents, mode, systemd_boot_json(sys, at))
            },
        );
        if let Some((contents, mode, sd)) = read {
            e.contents = contents;
            e.mode = mode;
            e.systemd_boot = sd;
        }
        esps.push(e);
    }
    let manual: Option<Vec<String>> =
        sys.read("/etc/kernel/proxmox-boot-manual-kernels")
            .map(|t| {
                t.lines()
                    .map(str::trim)
                    .filter(|l| !l.is_empty())
                    .map(str::to_string)
                    .collect()
            });
    let json = Obj::new()
        .set("uuids", uuids.clone())
        .set(
            "esps",
            esps.iter()
                .map(|e| {
                    Obj::new()
                        .set("uuid", e.uuid.as_str())
                        .set("device", e.device.clone())
                        .set("mode", e.mode)
                        .set("contents", e.contents.clone())
                        .build()
                })
                .collect::<Vec<_>>(),
        )
        .set("cmdline", sys.read_trim("/etc/kernel/cmdline"))
        .set("pin", sys.read_trim("/etc/kernel/proxmox-boot-pin"))
        .set("manual_kernels", manual)
        .build();
    Some(ProxmoxBoot { json, esps })
}

// --- Kernels -----------------------------------------------------------------------

/// One kernel of boot.kernels (or of Solus's): its version, the directory
/// it is in when that isn't /boot, its image's and initrd's sizes, whether
/// it has a config, whether it is the running one and whether its modules
/// are installed (/lib/modules/V or /usr/lib/modules/V), in that order.
fn kernel_obj(
    sys: &Sys,
    v: &str,
    dir: Option<&str>,
    vmlinuz: Option<u64>,
    initrd: Option<u64>,
    config: bool,
    running: Option<&str>,
) -> Value {
    Obj::new()
        .set("version", v)
        .set_some("dir", dir)
        .set("vmlinuz_bytes", vmlinuz)
        .set("initrd_bytes", initrd)
        .set("config", config)
        .set("running", running == Some(v))
        .set(
            "modules_installed",
            sys.is_dir(&format!("/lib/modules/{v}"))
                || sys.is_dir(&format!("/usr/lib/modules/{v}")),
        )
        .build()
}

pub(crate) fn kernels_json(sys: &Sys, running: Option<&str>) -> Value {
    let mut versions: Vec<String> = Vec::new();
    let files = sys.list("/boot");
    // Gentoo's installkernel keeps the kernel's files it replaced as
    // vmlinuz-V.old, initramfs-V.img.old, config-V.old and
    // System.map-V.old: no kernel of their own, listed apart.  Under
    // systemd's kernel-install, its plugin (90-compat.install, layout grub)
    // names the kernel kernel-V.  PikaOS's booster keeps the image it
    // replaced as booster.img-V.dpkg-bak or .bak: set aside the same way.
    let mut old_copies: Vec<&str> = Vec::new();
    for f in &files {
        if (f.ends_with(".old")
            && [
                "vmlinuz-",
                "kernel-",
                "initrd.img-",
                "config-",
                "System.map-",
                "initramfs-",
            ]
            .iter()
            .any(|p| f.starts_with(p)))
            || (f.starts_with("booster.img-") && (f.ends_with(".dpkg-bak") || f.ends_with(".bak")))
        {
            old_copies.push(f);
            continue;
        }
        // Debian's initrd.img-V, Arch's and Fedora's initramfs-V.img.
        // Alpine's are vmlinuz-FLAVOR and initramfs-FLAVOR, with no .img;
        // booster's (PikaOS) booster.img-V.
        let initramfs = f
            .strip_prefix("initramfs-")
            .map(|v| v.strip_suffix(".img").unwrap_or(v))
            .filter(|v| !v.ends_with("-fallback") && !v.ends_with("kdump"));
        for p in [
            "vmlinuz-",
            "kernel-",
            "initrd.img-",
            "config-",
            "System.map-",
            "initramfs-",
            "booster.img-",
        ] {
            let v = if p == "initramfs-" {
                initramfs
            } else {
                f.strip_prefix(p)
            };
            if let Some(v) = v {
                if !versions.iter().any(|x| x == v) {
                    versions.push(v.to_string());
                }
            }
        }
    }
    versions.sort_by(|a, b| crate::sys::natural_cmp(a, b));
    let mut list: Vec<Value> = versions
        .iter()
        .map(|v| {
            let vmlinuz = sys
                .file_size(&format!("/boot/vmlinuz-{v}"))
                .or_else(|| sys.file_size(&format!("/boot/kernel-{v}")));
            let initrd = sys
                .file_size(&format!("/boot/initrd.img-{v}"))
                .or_else(|| sys.file_size(&format!("/boot/initramfs-{v}.img")))
                .or_else(|| sys.file_size(&format!("/boot/initramfs-{v}")))
                .or_else(|| sys.file_size(&format!("/boot/booster.img-{v}")));
            kernel_obj(
                sys,
                v,
                None,
                vmlinuz,
                initrd,
                sys.is_file(&format!("/boot/config-{v}")),
                running,
            )
        })
        .collect();
    // ostree's: /boot/ostree/STATEROOT-BOOTCSUM/vmlinuz-V and
    // initramfs-V.img, one directory per kernel and initramfs pair.
    for d in sys.list("/boot/ostree") {
        let dir = format!("/boot/ostree/{d}");
        if !sys.is_dir(&dir) {
            continue;
        }
        for f in sys.list(&dir) {
            let Some(v) = f.strip_prefix("vmlinuz-") else {
                continue;
            };
            let (vmlinuz, initrd) = (
                sys.file_size(&format!("{dir}/{f}")),
                sys.file_size(&format!("{dir}/initramfs-{v}.img")),
            );
            list.push(kernel_obj(
                sys,
                v,
                Some(&dir),
                vmlinuz,
                initrd,
                sys.is_file(&format!("{dir}/config-{v}")),
                running,
            ));
        }
    }
    let mut links = Obj::new();
    for l in [
        "/vmlinuz",
        "/vmlinuz.old",
        "/initrd.img",
        "/initrd.img.old",
        "/boot/vmlinuz",
        "/boot/initrd.img",
    ] {
        if let Some(t) = sys.readlink(l) {
            links.put(l, t);
        }
    }
    let mut o = Obj::new().set("installed", list).set("symlinks", links);
    if !old_copies.is_empty() {
        o.put("old_copies", old_copies);
    }
    o.build()
}

/// /proc/cmdline split into words, with the storage-related ones picked out.
pub(crate) fn cmdline_json(cmdline: &str) -> Value {
    let words = shell_words(cmdline);
    let mut picked = Obj::new();
    for w in &words {
        let (k, v) = w.split_once('=').unwrap_or((w.as_str(), ""));
        if matches!(
            k,
            "root"
                | "rootflags"
                | "rootfstype"
                | "ro"
                | "rw"
                | "resume"
                | "resume_offset"
                | "cryptdevice"
                | "rd.luks.uuid"
                | "rd.luks.name"
                | "rd.lvm.vg"
                | "rd.lvm.lv"
                | "rd.md.uuid"
                | "rd.auto"
                | "zfs"
                | "zfsforce"
                | "boot"
                | "BOOT_IMAGE"
                | "init"
                | "systemd.mount-extra"
                | "rootdelay"
        ) {
            picked.put(k, v);
        }
    }
    Obj::new()
        .set("raw", cmdline.trim())
        .set("words", words)
        .set("storage", picked)
        .build()
}

/// installkernel's configuration on Gentoo: the first of these that
/// exists, as installkernel itself reads them.
pub(crate) const INSTALL_CONF: &[&str] =
    &["/etc/kernel/install.conf", "/usr/lib/kernel/install.conf"];

/// Gentoo's initramfs generator is the owner's choice, not the
/// distribution's (docs/design/gentoo.md): installkernel's install.conf
/// names it (`initrd_generator`: dracut, ugrd or none; `uki_generator`; layout,
/// where the images go).  Without one: none if /boot has no initramfs-*,
/// genkernel if it's installed, else dracut.  The generator, then the
/// boot.initramfs fields only Gentoo's dumps have: installkernel (the file
/// and its three keys, or null), evidence, and `lib_modules_initrd` (the
/// kernels with an image at /lib/modules/K/initrd, which load refuses).
fn gentoo_initramfs(
    sys: &Sys,
    packages: &[Package],
) -> (Option<String>, Vec<(&'static str, Value)>) {
    let has = |p: &str| has_package(packages, p);
    let mut why: Vec<String> = Vec::new();
    let conf = INSTALL_CONF.iter().find(|p| sys.is_file(p)).map(|p| {
        (
            *p,
            sys.read(p)
                .map(|t| parse_shell_assignments(&t))
                .unwrap_or_default(),
        )
    });
    let key = |k: &str| {
        conf.as_ref()
            .and_then(|(_, a)| a.iter().find(|(key, _)| key == k).map(|(_, v)| v.clone()))
    };
    let installkernel = conf.as_ref().map(|(p, _)| {
        Obj::new()
            .set("conf", *p)
            .set("layout", key("layout"))
            .set("initrd_generator", key("initrd_generator"))
            .set("uki_generator", key("uki_generator"))
            .build()
    });
    let in_boot = sys
        .list("/boot")
        .iter()
        .any(|f| f.starts_with("initramfs-") && !f.ends_with(".old"));
    let generator = match (&conf, key("initrd_generator").filter(|g| !g.is_empty())) {
        (Some((p, _)), Some(g)) => {
            why.push(format!("{p} says initrd_generator={g}"));
            g
        }
        (c, _) => {
            match c {
                Some((p, _)) => why.push(format!("{p} names no initrd_generator")),
                None => why.push(format!(
                    "no installkernel configuration ({})",
                    INSTALL_CONF.join(", ")
                )),
            }
            if !in_boot {
                why.push("/boot has no initramfs-*".into());
                "none".into()
            } else if has("genkernel") {
                why.push("genkernel is installed".into());
                "genkernel".into()
            } else {
                why.push("/boot has initramfs-* and genkernel isn't installed".into());
                "dracut".into()
            }
        }
    };
    // The distribution kernels' USE=initramfs: a second clue, said beside
    // the first.
    for cat_pf in sys.list("/var/db/pkg/sys-kernel") {
        let Some((n, _)) = split_pf(&cat_pf) else {
            continue;
        };
        if !matches!(n, "gentoo-kernel-bin" | "gentoo-kernel") {
            continue;
        }
        let use_ = sys
            .read(&format!("/var/db/pkg/sys-kernel/{cat_pf}/USE"))
            .unwrap_or_default();
        let on = use_.split_whitespace().any(|w| w == "initramfs");
        why.push(format!(
            "{cat_pf} is built {} USE=initramfs",
            if on { "with" } else { "without" }
        ));
    }
    let lib_initrd: Vec<String> = sys
        .list("/lib/modules")
        .into_iter()
        .filter(|k| sys.is_file(&format!("/lib/modules/{k}/initrd")))
        .collect();
    (
        Some(generator),
        vec![
            ("installkernel", installkernel.into()),
            ("evidence", why.into()),
            ("lib_modules_initrd", lib_initrd.into()),
        ],
    )
}

/// `initramfs_json` with no ESPs mounted, for the tests.
#[cfg(test)]
fn initramfs_json(sys: &Sys, packages: &[Package], ostree: bool) -> Value {
    initramfs_json_esps(sys, packages, ostree, &[])
}

/// boot.initramfs; `esps` (device, mount point) for Slackware's
/// elilo.conf initrd= lines.
fn initramfs_json_esps(
    sys: &Sys,
    packages: &[Package],
    ostree: bool,
    esps: &[(String, String)],
) -> Value {
    let has = |p: &str| has_package(packages, p);
    // Gentoo's and Slackware's generator is read from their own files,
    // with fields of their own beside it.
    let (gentoo_generator, gentoo) = if is_gentoo(sys) && !ostree {
        gentoo_initramfs(sys, packages)
    } else if is_slackware(sys) {
        slackware_initramfs(sys, esps)
    } else {
        (None, Vec::new())
    };
    // AerynOS: each kernel package ships its initramfs (/usr/lib/kernel's
    // *.initrd), which blsforme copies to the ESP; nothing is generated.
    // ostree: the image's own, in /boot/ostree (its directory is named after
    // the kernel's and initramfs's checksum), though dracut is installed.
    // Solus: each kernel package ships its dracut initrd, built when the
    // package was (/usr/lib64/kernel/initrd-com.solus-project.FLAVOUR.V-R,
    // with /usr/lib a link to lib64), which clr-boot-manager copies to the
    // ESP; dracut is installed, but nothing on the machine runs it.
    let prebuilt = ostree
        || sys
            .list("/usr/lib/kernel")
            .iter()
            .any(|f| f.ends_with(".initrd"))
        || ["/usr/lib64/kernel", "/usr/lib/kernel"]
            .iter()
            .any(|d| sys.list(d).iter().any(|f| f.starts_with(SOLUS_INITRD)));
    let generator = if let Some(g) = gentoo_generator.as_deref() {
        Some(g)
    } else if prebuilt {
        Some("prebuilt")
    } else if has("mkinitcpio") {
        Some("mkinitcpio")
    } else if has("booster") {
        // PikaOS: booster's package puts its own update-initramfs over
        // Debian's, and initramfs-tools or dracut may be installed beside it.
        Some("booster")
    } else if has("dracut") {
        Some("dracut")
    } else if has("initramfs-tools") {
        Some("initramfs-tools")
    } else if has("tiny-initramfs") {
        Some("tiny-initramfs")
    } else if has("mkinitfs") {
        Some("mkinitfs")
    } else {
        None
    };
    let conf = sys
        .read("/etc/initramfs-tools/initramfs.conf")
        .map(|t| parse_shell_assignments(&t))
        .unwrap_or_default();
    let resume = sys
        .read("/etc/initramfs-tools/conf.d/resume")
        .and_then(|t| {
            parse_shell_assignments(&t)
                .into_iter()
                .find(|(k, _)| k == "RESUME")
                .map(|(_, v)| v)
        });
    let pick = |k: &str| {
        conf.iter()
            .find(|(key, _)| key == k)
            .map(|(_, v)| v.clone())
    };
    let mut o = Obj::new()
        .set("generator", generator)
        .set("modules", pick("MODULES"))
        .set("compress", pick("COMPRESS"))
        .set("resume", resume)
        .set("crypttab_in_initramfs", sys.is_file("/etc/crypttab"));
    for (k, v) in gentoo {
        o.put(k, v);
    }
    if generator == Some("booster") {
        o.put("booster", booster_json(sys));
    }
    o.build()
}

/// booster's configuration file.
pub(crate) const BOOSTER_YAML: &str = "/etc/booster.yaml";

/// The keys of /etc/booster.yaml that decide what booster puts in an image,
/// each null when the file (or the key) is absent: universal (all modules,
/// or only this machine's) and `enable_lvm` as booleans, the module lists and
/// the compression as written.
fn booster_json(sys: &Sys) -> Value {
    let text = sys.read(BOOSTER_YAML);
    let keys = text.as_deref().map(parse_flat_yaml).unwrap_or_default();
    let key = |k: &str| {
        keys.iter()
            .find(|(key, _)| key == k)
            .map(|(_, v)| v.clone())
    };
    let flag = |k: &str| {
        key(k).map(|v| match v.to_ascii_lowercase().as_str() {
            "true" | "yes" | "on" => Value::from(true),
            "false" | "no" | "off" => Value::from(false),
            _ => Value::from(v),
        })
    };
    Obj::new()
        .set("conf", text.is_some().then_some(BOOSTER_YAML))
        .set("universal", flag("universal"))
        .set("enable_lvm", flag("enable_lvm"))
        .set("modules", key("modules"))
        .set("modules_force_load", key("modules_force_load"))
        .set("compression", key("compression"))
        .build()
}

/// A YAML file's top-level `key: value` lines (booster.yaml's are flat):
/// indented lines, list items and comments skipped, a trailing ` #`
/// comment dropped, a quoted value unquoted.  A key with no value on its
/// line (a nested map) is left out.
pub(crate) fn parse_flat_yaml(text: &str) -> Vec<(String, String)> {
    let mut out = Vec::new();
    for line in text.lines() {
        if line.starts_with([' ', '\t', '#', '-']) {
            continue;
        }
        let Some((k, v)) = line.split_once(':') else {
            continue;
        };
        let v = v.split(" #").next().unwrap_or("").trim();
        if k.is_empty() || v.is_empty() {
            continue;
        }
        let v = if v.len() >= 2
            && ((v.starts_with('"') && v.ends_with('"'))
                || (v.starts_with('\'') && v.ends_with('\'')))
        {
            &v[1..v.len() - 1]
        } else {
            v
        };
        out.push((k.trim().to_string(), v.to_string()));
    }
    out
}

// --- Which loader ------------------------------------------------------------------

/// What the report already knows that decides the loader.
pub(crate) struct Evidence<'a> {
    pub uefi: bool,
    /// The file of the entry the firmware booted (`BootCurrent`).
    pub current_file: Option<String>,
    pub loader_info: Option<String>,
    pub shim_variables: bool,
    /// (disk, boot code kind) for every disk.
    pub mbr_codes: Vec<(String, String)>,
    pub packages: &'a [Package],
    pub esp_files: Vec<String>,
    /// The ESP file the fallback path (EFI/BOOT/BOOTX64.EFI, or ARM64's
    /// BOOTAA64.EFI) is a byte copy of, if any
    /// (`esp_inventory`'s `same_as)`: shim's, when the fallback path is shim.
    pub fallback_copy_of: Option<String>,
    /// The ESP paths of the `ZFSBootMenu` images on mounted ESPs
    /// (`crate::zfsbootmenu::find_images`), `EFI/ZBM/VMLINUZ.EFI`.
    pub zbm_images: Vec<String>,
}

/// The loader on `x86_64` (`crate::arch::X86_64`'s names), for the tests.
#[cfg(test)]
pub(crate) fn detect_loader(e: &Evidence<'_>) -> Value {
    detect_loader_on(e, &crate::arch::X86_64)
}

/// The loader, from E, on a machine whose boot files have N's names
/// (`crate::arch`: grubx64.efi or grubaa64.efi, and so on).
pub(crate) fn detect_loader_on(e: &Evidence<'_>, n: &crate::arch::Names) -> Value {
    let has = |p: &str| has_package(e.packages, p);
    let mut why: Vec<String> = Vec::new();
    let mut name: Option<&str> = None;
    let mut shim = false;
    if e.uefi {
        if let Some(f) = &e.current_file {
            let u = f.to_ascii_uppercase().replace('/', "\\");
            why.push(format!("firmware booted {f}"));
            let zbm = e
                .zbm_images
                .iter()
                .find(|p| u.trim_start_matches('\\') == p.to_ascii_uppercase().replace('/', "\\"));
            if let Some(p) = zbm {
                // ZFSBootMenu (crate::zfsbootmenu): a unified kernel image
                // whose name says nothing a loader's would.
                name = Some("zfsbootmenu");
                why.push(format!("that is a ZFSBootMenu image ({p})"));
            } else if has(CBM)
                && u.trim_start_matches('\\')
                    .eq_ignore_ascii_case(&CBM_SHIM.replace('/', "\\"))
            {
                // clr-boot-manager's names (Solus): shim as bootloaderx64.efi,
                // and systemd-boot as the grubx64.efi it loads.
                shim = true;
                name = Some("systemd-boot");
                why.push(format!("that is clr-boot-manager's shim ({CBM_SHIM}), which loads systemd-boot as {CBM_SD_BOOT}"));
            } else if u.contains("SHIM") {
                shim = true;
                // Shim in another loader's own directory loads that loader
                // (refind-install --shim: rEFInd as grubx64.efi beside it in
                // EFI/refind); elsewhere it loads GRUB.
                name = Some(if u.contains("\\REFIND\\") {
                    "refind"
                } else if u.contains("\\LIMINE\\") {
                    "limine"
                } else {
                    "grub"
                });
                if let Some(l) = name.filter(|&l| l != "grub") {
                    why.push(format!(
                        "that is shim in {l}'s own directory, which loads {l}"
                    ));
                }
            } else if u.contains("GRUB") {
                name = Some("grub");
            } else if u.contains("SYSTEMD-BOOT") || u.contains("\\EFI\\SYSTEMD\\") {
                name = Some("systemd-boot");
            } else if u.contains("REFIND") {
                name = Some("refind");
            } else if u.contains("LIMINE") {
                name = Some("limine");
            } else if u.ends_with("\\ELILO.EFI") {
                // Slackware's eliloconfig: \EFI\Slackware\elilo.efi.
                name = Some("elilo");
            } else if u.ends_with(&n.fallback_dp()) {
                why.push("that is the removable-media fallback path".into());
                // AnduinOS's installer copies shim there, with GRUB and
                // MokManager beside it; with Secure Boot off, shim may set no
                // MOK variables to tell by.
                if let Some(of) = e
                    .fallback_copy_of
                    .as_deref()
                    .filter(|p| p.to_ascii_lowercase().ends_with(&format!("/{}", n.shim)))
                {
                    shim = true;
                    why.push(format!("{} is a copy of {of}", n.fallback));
                } else if let Some(of) = e
                    .fallback_copy_of
                    .as_deref()
                    .filter(|p| has(CBM) && p.eq_ignore_ascii_case(CBM_SHIM))
                {
                    // clr-boot-manager copies its shim there too (Solus).
                    shim = true;
                    name = Some("systemd-boot");
                    why.push(format!("{} is a copy of {of}, clr-boot-manager's shim, which loads systemd-boot as {CBM_SD_BOOT}", n.fallback));
                }
            }
        }
        if let Some(info) = &e.loader_info {
            why.push(format!("LoaderInfo says {info:?}"));
            if info.starts_with("GRUB") {
                name = name.or(Some("grub"));
            } else if info.starts_with("systemd-boot") {
                name = Some("systemd-boot");
            } else if info.starts_with("Limine") {
                // Limine sets LoaderInfo ("Limine 12.9.6") too: the only word
                // left when the firmware booted its copy at the fallback
                // path, as with no NVRAM entry (cachyos-zfs under vm-run).
                name = Some("limine");
            }
        }
        if e.shim_variables {
            why.push("shim's MOK variables are set".into());
            shim = true;
        }
        if name.is_none() {
            let lc: Vec<String> = e.esp_files.iter().map(|f| f.to_ascii_lowercase()).collect();
            if let Some(p) = e.zbm_images.first() {
                name = Some("zfsbootmenu");
                why.push(format!("an ESP holds a ZFSBootMenu image ({p})"));
            } else if has(CBM) && lc.iter().any(|f| f.eq_ignore_ascii_case(CBM_SD_BOOT)) {
                // Before grubx64.efi's rule: clr-boot-manager's is systemd-boot.
                name = Some("systemd-boot");
                why.push(format!(
                    "an ESP holds clr-boot-manager's {CBM_SD_BOOT}, which is systemd-boot"
                ));
                if lc.iter().any(|f| f.eq_ignore_ascii_case(CBM_SHIM)) {
                    shim = true;
                    why.push(format!("and its shim, {CBM_SHIM}"));
                }
            } else if lc.iter().any(|f| f.ends_with(&format!("/{}", n.grub))) {
                name = Some("grub");
                why.push(format!("an ESP holds {}", n.grub));
            } else if lc
                .iter()
                .any(|f| f.ends_with(&format!("/{}", n.systemd_boot)))
            {
                name = Some("systemd-boot");
                why.push(format!("an ESP holds {}", n.systemd_boot));
            } else if lc.iter().any(|f| {
                f == "limine.conf" || f.ends_with("/limine.conf") || f.starts_with("efi/limine/")
            }) {
                // esp_inventory's paths are relative to the ESP: EFI/limine/.
                name = Some("limine");
                why.push("an ESP holds Limine's EFI/limine/ or limine.conf".into());
            } else if lc
                .iter()
                .any(|f| f.ends_with(&format!("/{}", n.refind)) || f.ends_with("/refind.conf"))
            {
                // PikaOS's installer copies rEFInd to EFI/BOOT too, and its
                // "BOOTX64 mode" makes no boot entry: the firmware boots the
                // fallback path, which names no loader.
                name = Some("refind");
                why.push(format!("an ESP holds rEFInd's {} or refind.conf", n.refind));
            } else if lc.iter().any(|f| {
                f.ends_with("/elilo.efi")
                    || f.ends_with("/elilo.conf")
                    || f.ends_with("/elilo-x86_64.conf")
            }) {
                // Slackware's test guests boot ELILO's copy at the fallback
                // path (EFI/BOOT, with its elilo.conf) under blank NVRAM.
                name = Some("elilo");
                why.push("an ESP holds ELILO's elilo.efi or elilo.conf".into());
            }
        }
    } else {
        for (disk, kind) in &e.mbr_codes {
            match kind.as_str() {
                "grub2" => {
                    why.push(format!("{disk}'s MBR holds GRUB 2's boot.img"));
                    name = Some("grub");
                }
                "syslinux" => {
                    why.push(format!("{disk}'s MBR holds syslinux's mbr.bin"));
                    name = name.or(Some("syslinux"));
                }
                "grub-legacy" => {
                    why.push(format!("{disk}'s MBR holds GRUB Legacy's stage1"));
                    name = name.or(Some("grub-legacy"));
                }
                "lilo" => {
                    why.push(format!("{disk}'s MBR holds LILO's first stage"));
                    name = name.or(Some("lilo"));
                }
                _ => {}
            }
        }
    }
    let package = match (name, e.uefi) {
        // Chimera's (and Void's) GRUB for each platform is grub-PLATFORM;
        // Fedora's (and its atomic desktops') is grub2-efi-x64.
        // AnduinOS's own GRUB, first: when it's installed, it's the one on
        // the ESP.
        // The names are crate::arch's, the machine's own.
        (Some("grub"), true) => n.grub_efi_packages.iter().copied().find(|p| has(p)),
        (Some("grub"), false) => n.grub_bios_packages.iter().copied().find(|p| has(p)),
        (Some("systemd-boot"), _) => ["systemd-boot"].into_iter().find(|p| has(p)),
        (Some("refind"), _) => ["refind"].into_iter().find(|p| has(p)),
        (Some("limine"), _) => ["limine"].into_iter().find(|p| has(p)),
        (Some("syslinux"), _) => ["extlinux", "syslinux"].into_iter().find(|p| has(p)),
        (Some("lilo"), false) => ["lilo"].into_iter().find(|p| has(p)),
        (Some("elilo"), true) => ["elilo"].into_iter().find(|p| has(p)),
        (Some("zfsbootmenu"), _) => ["zfsbootmenu"].into_iter().find(|p| has(p)),
        _ => None,
    };
    if let Some(p) = package {
        why.push(format!("package {p} is installed"));
    } else if name.is_none() {
        // Last resort: what's installed.
        for &(p, l) in n.last_resort_packages {
            if has(p) {
                name = Some(l);
                why.push(format!("only evidence: package {p} is installed"));
                break;
            }
        }
    }
    // Debian's signed shim, Fedora's, AnduinOS's.
    let shim_package = (e.uefi && shim)
        .then(|| n.shim_packages.iter().copied().find(|p| has(p)))
        .flatten();
    let mut o = Obj::new()
        .set("name", name.unwrap_or("unknown"))
        .set("firmware", if e.uefi { "uefi" } else { "bios" })
        .set("via_shim", e.uefi && shim)
        .set("package", package);
    o.put_some("shim_package", shim_package);
    // What writes the loader and the kernels to the ESP, when it isn't the
    // loader's own tool: clr-boot-manager (Solus), on either firmware.
    o.put_some("manager", has(CBM).then_some(CBM));
    o.set("evidence", why).build()
}

/// clr-boot-manager's package (Solus, docs/design/solus.md).
pub(crate) const CBM: &str = "clr-boot-manager";
/// clr-boot-manager's shim on the ESP (shim-signed's shimx64.efi, renamed).
pub(crate) const CBM_SHIM: &str = "EFI/com.solus-project/bootloaderx64.efi";
/// The systemd-boot it copies beside its shim, by the name shim loads.
pub(crate) const CBM_SD_BOOT: &str = "EFI/com.solus-project/grubx64.efi";
/// The NVRAM entry clr-boot-manager makes for its shim.
pub(crate) const CBM_ENTRY: &str = "Solus Linux Bootloader";
/// The start of a Solus kernel package's prebuilt initrd's name.
pub(crate) const SOLUS_INITRD: &str = "initrd-com.solus-project.";
/// The start of a Solus kernel's name in its package's directory.
pub(crate) const SOLUS_KERNEL: &str = "com.solus-project.";

/// A Solus kernel's file name, com.solus-project.FLAVOUR.VER-REL, as
/// `uname -r` says it: VER-REL.FLAVOUR (com.solus-project.current.7.3.4-373
/// is 7.3.4-373.current).
pub(crate) fn solus_kernel_version(file: &str) -> Option<String> {
    let (flavour, vr) = file.strip_prefix(SOLUS_KERNEL)?.split_once('.')?;
    let (ver, rel) = vr.rsplit_once('-')?;
    (!flavour.is_empty()
        && !ver.is_empty()
        && !rel.is_empty()
        && rel.bytes().all(|c| c.is_ascii_digit()))
    .then(|| format!("{vr}.{flavour}"))
}

/// Solus's kernels (clr-boot-manager's sources, which it copies to the
/// ESP): each package's com.solus-project.FLAVOUR.VER-REL in
/// /usr/lib64/kernel (or /usr/lib/kernel, the same directory), with its
/// initrd-com.solus-project.FLAVOUR.VER-REL and config-VER-REL.FLAVOUR
/// beside it, `dir` saying where; and the default-FLAVOUR links (the
/// kernel each flavour boots by default), as (link, target).
pub(crate) fn solus_kernels(
    sys: &Sys,
    running: Option<&str>,
) -> (Vec<Value>, Vec<(String, String)>) {
    let Some(dir) = ["/usr/lib64/kernel", "/usr/lib/kernel"]
        .into_iter()
        .find(|d| sys.is_dir(d))
    else {
        return (Vec::new(), Vec::new());
    };
    let files = sys.list(dir);
    let mut kernels: Vec<(String, &String)> = files
        .iter()
        .filter_map(|f| solus_kernel_version(f).map(|v| (v, f)))
        .collect();
    kernels.sort_by(|a, b| crate::sys::natural_cmp(&a.0, &b.0));
    let list = kernels
        .iter()
        .map(|(v, f)| {
            let (vmlinuz, initrd) = (
                sys.file_size(&format!("{dir}/{f}")),
                sys.file_size(&format!("{dir}/initrd-{f}")),
            );
            kernel_obj(
                sys,
                v,
                Some(dir),
                vmlinuz,
                initrd,
                sys.is_file(&format!("{dir}/config-{v}")),
                running,
            )
        })
        .collect();
    let links = files
        .iter()
        .filter(|f| f.starts_with("default-"))
        .filter_map(|f| {
            sys.readlink(&format!("{dir}/{f}"))
                .map(|t| (format!("{dir}/{f}"), t))
        })
        .collect();
    (list, links)
}

/// boot.kernels with Solus's kernels (`solus_kernels`) added, when
/// clr-boot-manager is installed: /boot is the ESP there, with no
/// vmlinuz-V of its own.
fn with_solus_kernels(
    mut kernels: Value,
    sys: &Sys,
    packages: &[Package],
    running: Option<&str>,
) -> Value {
    if !has_package(packages, CBM) {
        return kernels;
    }
    let (list, links) = solus_kernels(sys, running);
    if let Value::Obj(o) = &mut kernels {
        for (k, v) in o.iter_mut() {
            match (k.as_str(), v) {
                ("installed", Value::Arr(a)) => a.extend(list.iter().cloned()),
                ("symlinks", Value::Obj(l)) => l.extend(
                    links
                        .iter()
                        .map(|(p, t)| (p.clone(), Value::from(t.as_str()))),
                ),
                _ => {}
            }
        }
    }
    kernels
}

/// Everything in this module, for the report's "boot" section.  `esps` are
/// the mounted ESPs (device, mount point); `ostree` is boot.ostree on an
/// ostree system (`crate::ostree::json`); `proxmox` is `boot.proxmox_boot`
/// (`proxmox_boot_json`), whose ESPs that aren't mounted join boot.esps with
/// `mounted_at` null; `unmounted` are other ESPs nothing mounts, read
/// elsewhere (device, contents: `TrueNAS`'s, `crate::truenas`), which join it
/// the same way after them; `zfs_tools` is storage.zfs.tools, whose boot
/// environments boot.zfsbootmenu lists when a mounted ESP holds a
/// `ZFSBootMenu` image (`crate::zfsbootmenu`).
#[allow(clippy::too_many_arguments)]
pub(crate) fn boot_json(
    sys: &Sys,
    packages: &[Package],
    esps: &[(String, String)],
    efi_boot: Value,
    mbr_codes: Vec<(String, String)>,
    ostree: Option<Value>,
    proxmox: Option<ProxmoxBoot>,
    unmounted: &[(String, Value)],
    zfs_tools: &Value,
) -> Value {
    let running = sys.read_trim("/proc/sys/kernel/osrelease");
    let mut esp_list = Vec::new();
    let mut esp_files = Vec::new();
    let mut fallback_copy_of = None;
    // The machine's boot names (crate::arch): BOOTX64.EFI or BOOTAA64.EFI.
    let names = crate::arch::names_of(sys);
    let mut sd_boot = None;
    // (device, mount point, contents, systemd-boot's files)
    let mut read: Vec<(String, Option<String>, Value, Option<Value>)> = esps
        .iter()
        .map(|(dev, mnt)| {
            (
                dev.clone(),
                Some(mnt.clone()),
                esp_inventory(sys, mnt),
                systemd_boot_json(sys, mnt),
            )
        })
        .collect();
    for e in proxmox
        .iter()
        .flat_map(|p| &p.esps)
        .filter(|e| e.mounted_at.is_none())
    {
        if let Some(dev) = &e.device {
            read.push((
                dev.clone(),
                None,
                e.contents.clone(),
                e.systemd_boot.clone(),
            ));
        }
    }
    read.extend(
        unmounted
            .iter()
            .map(|(dev, inv)| (dev.clone(), None, inv.clone(), None)),
    );
    // ZFSBootMenu's images, on the mounted ESPs.
    let mounted_esps: Vec<(String, String, Value)> = read
        .iter()
        .filter_map(|(d, m, inv, _)| m.as_ref().map(|m| (d.clone(), m.clone(), inv.clone())))
        .collect();
    let zbm_images = crate::zfsbootmenu::find_images(
        sys,
        &mounted_esps,
        crate::zfsbootmenu::root_is_be(&crate::zfs_be::list(zfs_tools)),
    );
    for (dev, mnt, inv, sd) in read {
        if let Some(files) = inv.get("files").and_then(Value::as_array) {
            esp_files.extend(
                files
                    .iter()
                    .filter_map(|f| f.get("path").and_then(Value::as_str))
                    .map(str::to_string),
            );
            fallback_copy_of = fallback_copy_of.or_else(|| {
                files
                    .iter()
                    .find(|f| {
                        f.get("path")
                            .and_then(Value::as_str)
                            .is_some_and(|p| p.eq_ignore_ascii_case(names.fallback))
                    })
                    .and_then(|f| f.get("same_as"))
                    .and_then(Value::as_str)
                    .map(str::to_string)
            });
        }
        if sd_boot.is_none() {
            sd_boot = sd;
        }
        esp_list.push(
            Obj::new()
                .set("device", dev)
                .set("mounted_at", mnt)
                .set("contents", inv)
                .build(),
        );
    }
    if sd_boot.is_none() && ostree.is_none() {
        // systemd-boot can also keep its entries on an XBOOTLDR at /boot.
        // Not on ostree: those are ostree's own entries, which GRUB's
        // blscfg reads (boot.ostree.entries).
        sd_boot = systemd_boot_json(sys, "/boot");
    }
    let current_file = efi_boot
        .get("boot_current")
        .and_then(Value::as_str)
        .and_then(|cur| {
            efi_boot
                .get("entries")
                .and_then(Value::as_array)?
                .iter()
                .find(|e| e.get("number").and_then(Value::as_str) == Some(cur))
                .and_then(|e| e.get("file").and_then(Value::as_str).map(str::to_string))
        });
    let ev = Evidence {
        uefi: crate::efi::is_uefi(sys),
        current_file,
        loader_info: efi_boot
            .at(&["loader_interface", "loader_info"])
            .and_then(Value::as_str)
            .map(str::to_string),
        shim_variables: efi_boot
            .get("shim_variables")
            .and_then(Value::as_array)
            .is_some_and(|a| {
                a.iter()
                    .any(|v| v.as_str().is_some_and(|s| s.starts_with("Mok")))
            }),
        mbr_codes,
        packages,
        esp_files,
        fallback_copy_of,
        zbm_images: zbm_images.iter().map(|i| i.path.clone()).collect(),
    };
    let mut loader = detect_loader_on(&ev, names);
    if let (Some(m), Value::Obj(o)) = (mok_json(sys, packages), &mut loader) {
        o.push(("mok".into(), m));
    }
    let mut pk = Obj::new();
    for p in PACKAGES {
        if let Some(found) = find_package(packages, p) {
            pk.put(p, found.version.as_str());
        }
    }
    let is_ostree = ostree.is_some();
    let mut o = Obj::new()
        .set("loader", loader)
        .set("efi", efi_boot)
        .set("esps", esp_list)
        .set("grub", grub_json(sys))
        .set("systemd_boot", sd_boot)
        .set(
            "kernels",
            with_solus_kernels(
                kernels_json(sys, running.as_deref()),
                sys,
                packages,
                running.as_deref(),
            ),
        )
        .set(
            "cmdline",
            sys.read("/proc/cmdline").map(|c| cmdline_json(&c)),
        )
        .set("kernel_cmdline_file", sys.read_trim("/etc/kernel/cmdline"))
        .set(
            "initramfs",
            initramfs_json_esps(sys, packages, is_ostree, esps),
        )
        .set("packages", pk);
    o.put_some("ostree", ostree);
    o.put_some("refind", refind_json(sys));
    o.put_some("store", crate::store::guix_json(sys));
    o.put_some("proxmox_boot", proxmox.map(|p| p.json));
    if !zbm_images.is_empty() {
        o.put(
            "zfsbootmenu",
            crate::zfsbootmenu::json(sys, &zbm_images, zfs_tools),
        );
    }
    // Slackware's LILO, ELILO and rc.d: on Slackware only, so no other
    // system's dump changes.
    if is_slackware(sys) {
        o.put_some("lilo", lilo_json(sys));
        o.put_some("elilo", elilo_json(sys, esps));
        o.put_some("slackware", slackware_json(sys, packages));
    }
    o.build()
}

/// `AnduinOS`'s Machine Owner Key: anduinos-secureboot-toolkit makes it at
/// install and signs `AnduinOS`'s GRUB with it (MOK.priv beside it stays
/// root's alone).
pub(crate) const ANDUINOS_MOK: &str = "/var/lib/shim-signed/mok/MOK.der";

/// boot.loader.mok: on `AnduinOS`'s own GRUB (anduinos-grub installed and
/// its MOK certificate there), the certificate and its SHA-256 fingerprint
/// (of the DER file, as `openssl x509 -fingerprint -sha256` prints it), so
/// a restore can say which key the firmware must hold.  None otherwise.
pub(crate) fn mok_json(sys: &Sys, packages: &[Package]) -> Option<Value> {
    if !has_package(packages, "anduinos-grub") {
        return None;
    }
    let der = sys.read_bytes(ANDUINOS_MOK)?;
    Some(
        Obj::new()
            .set("cert", ANDUINOS_MOK)
            .set("sha256", crate::bytes::sha256_hex(&der))
            .build(),
    )
}

/// rEFInd's kernel options file, which it reads beside the kernels it
/// finds (/boot here): each line a quoted title and the quoted options.
pub(crate) const REFIND_LINUX_CONF: &str = "/boot/refind_linux.conf";

/// boot.refind: `refind_linux.conf`'s path and its lines, each {title,
/// options}; None when there is no such file.
pub(crate) fn refind_json(sys: &Sys) -> Option<Value> {
    let text = sys.read(REFIND_LINUX_CONF)?;
    let lines: Vec<Value> = text
        .lines()
        .map(str::trim)
        .filter(|l| !l.is_empty() && !l.starts_with('#'))
        .map(|l| {
            let mut w = shell_words(l).into_iter();
            let title = w.next().unwrap_or_default();
            let options = w.collect::<Vec<_>>().join(" ");
            Obj::new()
                .set("title", title)
                .set("options", options)
                .build()
        })
        .collect();
    Some(
        Obj::new()
            .set(
                "linux_conf",
                Obj::new()
                    .set("path", REFIND_LINUX_CONF)
                    .set("lines", lines),
            )
            .build(),
    )
}

// --- Slackware: LILO, ELILO, mkinitrd, rc.d (boot/slackware.rs) -------------------

mod slackware;
pub(crate) use slackware::*;

#[cfg(test)]
mod tests {
    use super::*;
    use crate::sys::FakeRunner;
    use crate::testutil::Tree;

    #[test]
    fn shell_word_splitting() {
        assert_eq!(
            shell_words("a 'b c' \"d \\\" e\" f\\ g"),
            vec!["a", "b c", "d \" e", "f g"]
        );
        assert_eq!(shell_words("'it'\\''s' x"), vec!["it's", "x"]);
        assert_eq!(shell_words("x # comment"), vec!["x"]);
        assert_eq!(shell_words("a#b"), vec!["a#b"]);
        assert_eq!(shell_words("''"), vec![""]);
        assert!(shell_words("   ").is_empty());
        assert_eq!(shell_words("'unterminated"), vec!["unterminated"]);
        assert_eq!(shell_words("trailing\\"), vec!["trailing"]);
    }

    #[test]
    fn default_grub() {
        let a = parse_shell_assignments(
            "# If you change this file...\nGRUB_DEFAULT=0\nGRUB_TIMEOUT=5\nGRUB_DISTRIBUTOR=`lsb_release -i -s 2> /dev/null || echo Debian`\nGRUB_CMDLINE_LINUX_DEFAULT=\"quiet\"\nGRUB_CMDLINE_LINUX=\"\"\n#GRUB_DISABLE_OS_PROBER=false\nexport GRUB_ENABLE_CRYPTODISK=y\nGRUB_TIMEOUT=1\nnot an assignment\n=x\n",
        );
        let get = |k: &str| a.iter().find(|(key, _)| key == k).map(|(_, v)| v.as_str());
        assert_eq!(get("GRUB_DEFAULT"), Some("0"));
        assert_eq!(get("GRUB_TIMEOUT"), Some("1"));
        assert_eq!(
            get("GRUB_DISTRIBUTOR"),
            Some("`lsb_release -i -s 2> /dev/null || echo Debian`")
        );
        assert_eq!(get("GRUB_CMDLINE_LINUX_DEFAULT"), Some("quiet"));
        assert_eq!(get("GRUB_CMDLINE_LINUX"), Some(""));
        assert_eq!(get("GRUB_ENABLE_CRYPTODISK"), Some("y"));
        assert_eq!(get("GRUB_DISABLE_OS_PROBER"), None);
        assert_eq!(a.iter().filter(|(k, _)| k == "GRUB_TIMEOUT").count(), 1);
    }

    /// Shaped like Chopin's real /boot/grub/grub.cfg (Debian 13, UEFI, ext4).
    const GRUB_CFG: &str = "#
# DO NOT EDIT THIS FILE
#
if [ -s $prefix/grubenv ]; then
  set have_grubenv=true
  load_env
fi
function load_video {
  if [ x$feature_all_video_module = xy ]; then
    insmod all_video
  else
    insmod efi_gop
  fi
}
insmod part_gpt
insmod ext2
search --no-floppy --fs-uuid --set=root f2438bf3-6107-49a3-a57c-ba1bee4aebcb
menuentry 'Debian GNU/Linux' --class debian --class gnu-linux --class gnu --class os $menuentry_id_option 'gnulinux-simple-f2438bf3' {
\tload_video
\tinsmod gzio
\tif [ x$grub_platform = xxen ]; then insmod xzio; insmod lzopio; fi
\tinsmod part_gpt
\tinsmod ext2
\tsearch --no-floppy --fs-uuid --set=root f2438bf3-6107-49a3-a57c-ba1bee4aebcb
\techo\t'Loading Linux 6.12.115+deb13.1-amd64 ...'
\tlinux\t/boot/vmlinuz-6.12.115+deb13.1-amd64 root=UUID=f2438bf3-6107-49a3-a57c-ba1bee4aebcb ro  quiet
\techo\t'Loading initial ramdisk ...'
\tinitrd\t/boot/initrd.img-6.12.115+deb13.1-amd64
}
submenu 'Advanced options for Debian GNU/Linux' $menuentry_id_option 'gnulinux-advanced-f2438bf3' {
\tmenuentry 'Debian GNU/Linux, with Linux 6.12.115+deb13.1-amd64' --class debian $menuentry_id_option 'x' {
\t\tlinux\t/boot/vmlinuz-6.12.115+deb13.1-amd64 root=UUID=f2438bf3 ro quiet
\t}
\tmenuentry 'Debian GNU/Linux, with Linux 6.12.115+deb13.1-amd64 (recovery mode)' --class debian $menuentry_id_option 'y' {
\t\tlinux\t/boot/vmlinuz-6.12.115+deb13.1-amd64 root=UUID=f2438bf3 ro single dis_ucode_ldr
\t}
}
menuentry 'UEFI Firmware Settings' $menuentry_id_option 'uefi-firmware' {
\tfwsetup
}
search --fs-uuid --set=boot --hint hd0,gpt2 1111-2222
";

    #[test]
    fn grub_cfg_parse() {
        let g = parse_grub_cfg(GRUB_CFG);
        assert_eq!(
            g.entries,
            vec![
                "Debian GNU/Linux",
                "Advanced options for Debian GNU/Linux>Debian GNU/Linux, with Linux 6.12.115+deb13.1-amd64",
                "Advanced options for Debian GNU/Linux>Debian GNU/Linux, with Linux 6.12.115+deb13.1-amd64 (recovery mode)",
                "UEFI Firmware Settings"
            ]
        );
        assert_eq!(
            g.search_uuids,
            vec!["f2438bf3-6107-49a3-a57c-ba1bee4aebcb", "1111-2222"]
        );
        // Only commands at the start of a line count: "if ...; then insmod xzio" is skipped.
        assert_eq!(
            g.modules,
            vec!["all_video", "efi_gop", "part_gpt", "ext2", "gzio"]
        );
        assert_eq!(
            g.default_linux.as_deref(),
            Some(
                "/boot/vmlinuz-6.12.115+deb13.1-amd64 root=UUID=f2438bf3-6107-49a3-a57c-ba1bee4aebcb ro quiet"
            )
        );
        assert_eq!(
            g.default_initrd.as_deref(),
            Some("/boot/initrd.img-6.12.115+deb13.1-amd64")
        );
        assert_eq!(g.lang, None);
        assert_eq!(
            parse_grub_cfg("set locale_dir=$prefix/locale\nset lang=en_US\ninsmod gettext\n")
                .lang
                .as_deref(),
            Some("en_US")
        );
    }

    #[test]
    fn grub_stub_and_env() {
        let g = parse_grub_cfg(
            "search.fs_uuid f2438bf3-6107-49a3-a57c-ba1bee4aebcb root \nset prefix=($root)'/boot/grub'\nconfigfile $prefix/grub.cfg\n",
        );
        assert_eq!(g.search_uuids, vec!["f2438bf3-6107-49a3-a57c-ba1bee4aebcb"]);
        assert_eq!(g.prefix.as_deref(), Some("($root)/boot/grub"));
        assert_eq!(g.configfile.as_deref(), Some("$prefix/grub.cfg"));
        let env = parse_grubenv(
            "# GRUB Environment Block\n# WARNING: Do not edit\nsaved_entry=gnulinux-x\nrecordfail=1\n##########",
        );
        assert_eq!(
            env,
            vec![
                ("saved_entry".into(), "gnulinux-x".into()),
                ("recordfail".into(), "1".into())
            ]
        );
    }

    #[test]
    fn cmdline() {
        let c = cmdline_json(
            "BOOT_IMAGE=/boot/vmlinuz-6.12 root=UUID=abc ro quiet rootflags=subvol=@rootfs resume=UUID=def\n",
        );
        assert_eq!(
            c.at(&["storage", "root"]).and_then(Value::as_str),
            Some("UUID=abc")
        );
        assert_eq!(
            c.at(&["storage", "rootflags"]).and_then(Value::as_str),
            Some("subvol=@rootfs")
        );
        assert_eq!(c.at(&["storage", "ro"]).and_then(Value::as_str), Some(""));
        assert!(c.at(&["storage", "quiet"]).is_none());
        assert_eq!(
            c.get("words").and_then(Value::as_array).map(Vec::len),
            Some(6)
        );
        let z = cmdline_json("root=ZFS=rpool/ROOT/debian boot=zfs");
        assert_eq!(
            z.at(&["storage", "root"]).and_then(Value::as_str),
            Some("ZFS=rpool/ROOT/debian")
        );
    }

    fn pk(names: &[&str]) -> Vec<Package> {
        names
            .iter()
            .map(|n| Package::new(*n, "1", "amd64"))
            .collect()
    }

    #[test]
    fn loader_uefi_shim_grub() {
        let p = pk(&["grub-efi-amd64-signed", "shim-signed"]);
        let v = detect_loader(&Evidence {
            uefi: true,
            current_file: Some("\\EFI\\DEBIAN\\SHIMX64.EFI".into()),
            loader_info: Some("GRUB 2.12-9+deb13u2".into()),
            shim_variables: true,
            mbr_codes: vec![],
            packages: &p,
            esp_files: vec![],
            fallback_copy_of: None,
            zbm_images: vec![],
        });
        assert_eq!(v.get("name").and_then(Value::as_str), Some("grub"));
        assert_eq!(v.get("via_shim"), Some(&Value::Bool(true)));
        assert_eq!(
            v.get("package").and_then(Value::as_str),
            Some("grub-efi-amd64-signed")
        );
        assert_eq!(
            v.get("evidence").and_then(Value::as_array).map(Vec::len),
            Some(4)
        );
    }

    #[test]
    fn loader_other_cases() {
        let none: Vec<Package> = vec![];
        let base = |uefi: bool| Evidence {
            uefi,
            current_file: None,
            loader_info: None,
            shim_variables: false,
            mbr_codes: vec![],
            packages: &none,
            esp_files: vec![],
            fallback_copy_of: None,
            zbm_images: vec![],
        };
        // systemd-boot by its path, then by LoaderInfo.
        let mut e = base(true);
        e.current_file = Some("\\EFI\\systemd\\systemd-bootx64.efi".into());
        assert_eq!(
            detect_loader(&e).get("name").and_then(Value::as_str),
            Some("systemd-boot")
        );
        let mut e = base(true);
        e.loader_info = Some("systemd-boot 257.8-1~deb13u2".into());
        assert_eq!(
            detect_loader(&e).get("name").and_then(Value::as_str),
            Some("systemd-boot")
        );
        // The removable path alone says nothing; the ESP's files decide.
        let mut e = base(true);
        e.current_file = Some("\\EFI\\BOOT\\BOOTX64.EFI".into());
        e.esp_files = vec!["EFI/debian/grubx64.efi".into()];
        let v = detect_loader(&e);
        assert_eq!(v.get("name").and_then(Value::as_str), Some("grub"));
        assert!(v.get("evidence").unwrap().compact().contains("fallback"));
        assert_eq!(v.get("via_shim"), Some(&Value::Bool(false)));
        // Limine booted at the fallback path (no NVRAM entry): LoaderInfo
        // names it, and so do the ESP's files without it (cachyos-zfs).
        let mut e = base(true);
        e.current_file = Some("\\EFI\\BOOT\\BOOTX64.EFI".into());
        e.loader_info = Some("Limine 12.9.6".into());
        e.esp_files = vec![
            "EFI/BOOT/BOOTX64.EFI".into(),
            "EFI/limine/limine_x64.efi".into(),
        ];
        let p = pk(&["limine"]);
        e.packages = &p;
        let v = detect_loader(&e);
        assert_eq!(v.get("name").and_then(Value::as_str), Some("limine"));
        assert_eq!(v.get("package").and_then(Value::as_str), Some("limine"));
        assert!(
            v.get("evidence")
                .unwrap()
                .compact()
                .contains("Limine 12.9.6")
        );
        e.loader_info = None;
        let v = detect_loader(&e);
        assert_eq!(v.get("name").and_then(Value::as_str), Some("limine"));
        assert!(
            v.get("evidence")
                .unwrap()
                .compact()
                .contains("an ESP holds Limine's")
        );
        // Through its own NVRAM entry, the path decides.
        let mut e = base(true);
        e.current_file = Some("\\EFI\\limine\\limine_x64.efi".into());
        assert_eq!(
            detect_loader(&e).get("name").and_then(Value::as_str),
            Some("limine")
        );
        let mut e = base(true);
        e.current_file = Some("\\EFI\\refind\\refind_x64.efi".into());
        assert_eq!(
            detect_loader(&e).get("name").and_then(Value::as_str),
            Some("refind")
        );
        // rEFInd through shim (refind-install --shim: shim in EFI/refind,
        // rEFInd as grubx64.efi beside it), with GRUB's signed package
        // installed too: rEFInd, via shim, not GRUB (RustAudit1 H4-01).
        let mut e = base(true);
        e.current_file = Some("\\EFI\\refind\\shimx64.efi".into());
        e.esp_files = vec![
            "EFI/refind/shimx64.efi".into(),
            "EFI/refind/grubx64.efi".into(),
            "EFI/refind/refind.conf".into(),
        ];
        let p = pk(&["refind", "shim-signed", "grub-efi-amd64-signed"]);
        e.packages = &p;
        let v = detect_loader(&e);
        assert_eq!(v.get("name").and_then(Value::as_str), Some("refind"));
        assert_eq!(v.get("via_shim"), Some(&Value::Bool(true)));
        assert_eq!(v.get("package").and_then(Value::as_str), Some("refind"));
        assert_eq!(
            v.get("shim_package").and_then(Value::as_str),
            Some("shim-signed")
        );
        assert!(
            v.get("evidence")
                .unwrap()
                .compact()
                .contains("shim in refind's own directory")
        );
        // Limine's the same; shim anywhere else still loads GRUB.
        e.current_file = Some("\\EFI\\limine\\shimx64.efi".into());
        assert_eq!(
            detect_loader(&e).get("name").and_then(Value::as_str),
            Some("limine")
        );
        e.current_file = Some("\\EFI\\ubuntu\\shimx64.efi".into());
        let v = detect_loader(&e);
        assert_eq!(v.get("name").and_then(Value::as_str), Some("grub"));
        assert!(
            !v.get("evidence")
                .unwrap()
                .compact()
                .contains("own directory")
        );
        // BIOS: the MBR decides.
        let mut e = base(false);
        e.mbr_codes = vec![
            ("vda".into(), "grub2".into()),
            ("vdb".into(), "none".into()),
        ];
        let p = pk(&["grub-pc"]);
        e.packages = &p;
        let v = detect_loader(&e);
        assert_eq!(v.get("name").and_then(Value::as_str), Some("grub"));
        assert_eq!(v.get("firmware").and_then(Value::as_str), Some("bios"));
        assert_eq!(v.get("package").and_then(Value::as_str), Some("grub-pc"));
        let mut e = base(false);
        e.mbr_codes = vec![("sda".into(), "syslinux".into())];
        assert_eq!(
            detect_loader(&e).get("name").and_then(Value::as_str),
            Some("syslinux")
        );
        let mut e = base(false);
        e.mbr_codes = vec![("sda".into(), "grub-legacy".into())];
        assert_eq!(
            detect_loader(&e).get("name").and_then(Value::as_str),
            Some("grub-legacy")
        );
        // Nothing at all but a package.
        let mut e = base(false);
        let p = pk(&["grub-pc"]);
        e.packages = &p;
        let v = detect_loader(&e);
        assert_eq!(v.get("name").and_then(Value::as_str), Some("grub"));
        assert!(
            v.get("evidence")
                .unwrap()
                .compact()
                .contains("only evidence")
        );
        assert_eq!(
            detect_loader(&base(false))
                .get("name")
                .and_then(Value::as_str),
            Some("unknown")
        );
    }

    /// `PikaOS`'s rEFInd booted at the fallback path: the ESP's files name it,
    /// in EFI/refind or only beside EFI/BOOT/bootx64.efi.
    #[test]
    fn refind_from_the_esp() {
        let p = pk(&["refind"]);
        let e = |files: &[&str]| Evidence {
            uefi: true,
            current_file: Some("\\EFI\\BOOT\\BOOTX64.EFI".into()),
            loader_info: None,
            shim_variables: false,
            mbr_codes: vec![],
            packages: &p,
            esp_files: files.iter().map(|f| f.to_string()).collect(),
            fallback_copy_of: None,
            zbm_images: vec![],
        };
        let v = detect_loader(&e(&[
            "EFI/BOOT/bootx64.efi",
            "EFI/refind/refind_x64.efi",
            "EFI/refind/drivers_x64/ext4_x64.efi",
        ]));
        assert_eq!(v.get("name").and_then(Value::as_str), Some("refind"));
        assert_eq!(v.get("package").and_then(Value::as_str), Some("refind"));
        assert!(
            v.get("evidence")
                .unwrap()
                .compact()
                .contains("an ESP holds rEFInd's"),
            "{}",
            v.compact()
        );
        assert_eq!(
            detect_loader(&e(&["EFI/BOOT/bootx64.efi", "EFI/BOOT/refind.conf"]))
                .get("name")
                .and_then(Value::as_str),
            Some("refind")
        );
        assert_eq!(
            detect_loader(&e(&["EFI/BOOT/bootx64.efi"]))
                .get("name")
                .and_then(Value::as_str),
            Some("unknown")
        );
    }

    /// booster's images and configuration (PikaOS): booster.img-K is K's
    /// initramfs, its .dpkg-bak and .bak copies are set aside; booster wins
    /// over initramfs-tools; booster.yaml's keys, null when absent; and
    /// `refind_linux.conf`'s lines.
    #[test]
    fn booster_and_refind_linux_conf() {
        let t = Tree::new("boot-booster");
        let k = "7.3.3-pikaos";
        t.file(&format!("/boot/vmlinuz-{k}"), "kk")
            .file(&format!("/boot/booster.img-{k}"), "iii")
            .file(&format!("/boot/booster.img-{k}.dpkg-bak"), "o")
            .file(&format!("/boot/booster.img-{k}.bak"), "o")
            .dir(&format!("/lib/modules/{k}/kernel"));
        let s = Sys::new(t.root(), Box::new(FakeRunner::default()), false);
        let v = kernels_json(&s, Some(k));
        let inst = v.get("installed").and_then(Value::as_array).unwrap();
        assert_eq!(inst.len(), 1, "{}", v.compact());
        assert_eq!(inst[0].get("initrd_bytes").and_then(Value::as_u64), Some(3));
        assert_eq!(
            v.get("old_copies").unwrap().compact(),
            format!(r#"["booster.img-{k}.bak","booster.img-{k}.dpkg-bak"]"#)
        );
        let p = pk(&["booster", "initramfs-tools", "dracut"]);
        let i = initramfs_json(&s, &p, false);
        assert_eq!(i.get("generator").and_then(Value::as_str), Some("booster"));
        assert_eq!(
            i.get("booster").unwrap().compact(),
            r#"{"conf":null,"universal":null,"enable_lvm":null,"modules":null,"modules_force_load":null,"compression":null}"#
        );
        t.file("/etc/booster.yaml", "universal: true  # all modules\nenable_lvm: false\nmodules: 'usbhid,hid_generic'\nnetwork:\n  dhcp: on\n- stray\ncompression: \"zstd\"\n");
        let i = initramfs_json(&s, &p, false);
        assert_eq!(
            i.get("booster").unwrap().compact(),
            r#"{"conf":"/etc/booster.yaml","universal":true,"enable_lvm":false,"modules":"usbhid,hid_generic","modules_force_load":null,"compression":"zstd"}"#
        );
        // Without booster, no booster object.
        let i = initramfs_json(&s, &pk(&["initramfs-tools"]), false);
        assert_eq!(
            i.get("generator").and_then(Value::as_str),
            Some("initramfs-tools")
        );
        assert!(i.get("booster").is_none());
        assert!(refind_json(&s).is_none());
        t.file("/boot/refind_linux.conf", "# rEFInd\n\"Boot with standard options\"  \"rd.luks.name=1111aaaa-2222-4333-8444-555566667777=crypt_root rd.luks.options=discard nowatchdog root=UUID=a0a1 quiet splash ---\"\n\n\"Boot with logging\" \"root=UUID=a0a1 ---\"\n");
        assert_eq!(
            refind_json(&s).unwrap().compact(),
            r#"{"linux_conf":{"path":"/boot/refind_linux.conf","lines":[{"title":"Boot with standard options","options":"rd.luks.name=1111aaaa-2222-4333-8444-555566667777=crypt_root rd.luks.options=discard nowatchdog root=UUID=a0a1 quiet splash ---"},{"title":"Boot with logging","options":"root=UUID=a0a1 ---"}]}}"#
        );
    }

    #[test]
    fn kernel_record_keys() {
        let t = Tree::new("kobj");
        t.dir("/usr/lib/modules/6.12");
        let s = Sys::new(t.root(), Box::new(FakeRunner::default()), false);
        let keys = |v: &Value| match v {
            Value::Obj(o) => o.iter().map(|(k, _)| k.clone()).collect::<Vec<_>>(),
            _ => Vec::new(),
        };
        let boot = kernel_obj(&s, "6.12", None, Some(9), None, true, Some("6.12"));
        assert_eq!(
            keys(&boot),
            [
                "version",
                "vmlinuz_bytes",
                "initrd_bytes",
                "config",
                "running",
                "modules_installed"
            ]
        );
        assert_eq!(boot.get("initrd_bytes"), Some(&Value::Null));
        assert_eq!(boot.get("running"), Some(&Value::Bool(true)));
        assert_eq!(boot.get("modules_installed"), Some(&Value::Bool(true)));
        let ostree = kernel_obj(
            &s,
            "6.13",
            Some("/boot/ostree/x"),
            None,
            Some(3),
            false,
            Some("6.12"),
        );
        assert_eq!(
            keys(&ostree),
            [
                "version",
                "dir",
                "vmlinuz_bytes",
                "initrd_bytes",
                "config",
                "running",
                "modules_installed"
            ]
        );
        assert_eq!(ostree.get("running"), Some(&Value::Bool(false)));
        assert_eq!(ostree.get("modules_installed"), Some(&Value::Bool(false)));
    }

    #[test]
    fn byte_copies() {
        let t = Tree::new("bytecopy");
        t.file("/esp/a.efi", "same")
            .file("/esp/b.efi", "diff")
            .file("/esp/c.efi", "same")
            .file("/esp/d.efi", "same")
            .file("/esp/big.efi", "same!");
        let s = Sys::new(t.root(), Box::new(FakeRunner::default()), false);
        // Same size first: d.efi's bytes match but its (claimed) size
        // doesn't, so it is never compared; then the first match wins.
        let c = [
            ("d.efi", 5),
            ("gone.efi", 4),
            ("b.efi", 4),
            ("c.efi", 4),
            ("a.efi", 4),
        ];
        assert_eq!(byte_copy_of(&s, "/esp/a.efi", 4, c, "/esp"), Some("c.efi"));
        assert_eq!(
            byte_copy_of(&s, "/esp/a.efi", 4, [("d.efi", 5), ("b.efi", 4)], "/esp"),
            None
        );
        assert_eq!(
            byte_copy_of(
                &s,
                "/esp/big.efi",
                5,
                [("/esp/a.efi", 5), ("/esp/big.efi", 5)],
                ""
            ),
            Some("/esp/big.efi")
        );
        assert_eq!(
            byte_copy_of(&s, "/esp/gone.efi", 4, [("a.efi", 4)], "/esp"),
            None
        );
        assert!(s.warnings().is_empty());
    }

    #[test]
    fn esp_and_systemd_boot() {
        let t = Tree::new("esp");
        let shim = vec![7u8; 1000];
        t.file("/boot/efi/EFI/debian/shimx64.efi", &shim)
            .file("/boot/efi/EFI/debian/grubx64.efi", vec![8u8; 1000])
            .file("/boot/efi/EFI/debian/grub.cfg", "search.fs_uuid f2438bf3 root \nset prefix=($root)'/boot/grub'\nconfigfile $prefix/grub.cfg\n")
            .file("/boot/efi/EFI/BOOT/BOOTX64.EFI", &shim)
            .file("/boot/efi/loader/loader.conf", "#timeout 3\ntimeout 5\ndefault debian-*\n")
            .file("/boot/efi/loader/entries/debian-6.12.conf", "title Debian\nversion 6.12\nlinux /vmlinuz\ninitrd /initrd\noptions root=UUID=x ro\n")
            .file("/boot/efi/loader/entries/readme.txt", "x")
            .file("/boot/efi/System Volume Information/x", "y");
        let s = Sys::new(t.root(), Box::new(FakeRunner::default()), false);
        let inv = esp_inventory(&s, "/boot/efi");
        let files = inv.get("files").unwrap().as_array().unwrap();
        let paths: Vec<&str> = files
            .iter()
            .filter_map(|f| f.get("path").and_then(Value::as_str))
            .collect();
        assert_eq!(
            paths,
            vec![
                "EFI/BOOT/BOOTX64.EFI",
                "EFI/debian/grub.cfg",
                "EFI/debian/grubx64.efi",
                "EFI/debian/shimx64.efi",
                "loader/entries/debian-6.12.conf",
                "loader/entries/readme.txt",
                "loader/loader.conf"
            ]
        );
        assert_eq!(
            files[0].get("same_as").and_then(Value::as_str),
            Some("EFI/debian/shimx64.efi")
        );
        assert!(files[2].get("same_as").is_none());
        assert_eq!(
            inv.at(&["debian_grub_stub", "search_uuids", "0"])
                .and_then(Value::as_str),
            Some("f2438bf3")
        );
        let sd = systemd_boot_json(&s, "/boot/efi").unwrap();
        assert_eq!(
            sd.at(&["loader_conf", "timeout"]).and_then(Value::as_str),
            Some("5")
        );
        assert_eq!(
            sd.at(&["entries", "0", "file"]).and_then(Value::as_str),
            Some("debian-6.12.conf")
        );
        assert_eq!(
            sd.at(&["entries", "0", "options"]).and_then(Value::as_str),
            Some("root=UUID=x ro")
        );
        assert_eq!(
            sd.get("entries").and_then(Value::as_array).map(Vec::len),
            Some(1)
        );
        assert!(systemd_boot_json(&s, "/nowhere").is_none());
        // RustAudit1 H4-03: an entry's repeated initrd lines are all in
        // initrds (initrd stays the last one's), its repeated options lines
        // one line joined with spaces; any other repeated key keeps its
        // first place and its last value.
        t.file("/boot/efi/loader/entries/arch.conf", "title Arch\nlinux /vmlinuz-linux\ninitrd /intel-ucode.img\ninitrd  /initramfs-linux.img \noptions root=x\noptions\noptions  rw quiet\ntitle Arch Linux\n");
        let sd = systemd_boot_json(&s, "/boot/efi").unwrap();
        let arch = sd.at(&["entries", "0"]).unwrap();
        assert_eq!(arch.get("file").and_then(Value::as_str), Some("arch.conf"));
        assert_eq!(
            arch.get("title").and_then(Value::as_str),
            Some("Arch Linux")
        );
        assert_eq!(
            arch.get("initrd").and_then(Value::as_str),
            Some("/initramfs-linux.img")
        );
        assert_eq!(
            arch.get("initrds"),
            Some(&Value::from(vec![
                "/intel-ucode.img",
                "/initramfs-linux.img"
            ]))
        );
        assert_eq!(
            arch.get("options").and_then(Value::as_str),
            Some("root=x rw quiet")
        );
        let Value::Obj(keys) = arch else {
            panic!("entry not an object")
        };
        assert_eq!(
            keys.iter().map(|(k, _)| k.as_str()).collect::<Vec<_>>(),
            vec!["file", "title", "linux", "initrd", "initrds", "options"]
        );
        // One initrd line: no initrds; debian's entry is as it was.
        let debian = sd.at(&["entries", "1"]).unwrap();
        assert_eq!(
            debian.get("initrd").and_then(Value::as_str),
            Some("/initrd")
        );
        assert!(debian.get("initrds").is_none());
        assert_eq!(
            debian.get("options").and_then(Value::as_str),
            Some("root=UUID=x ro")
        );
        // loader.conf isn't an entry: its keys are only ever the last line's.
        t.file(
            "/boot/efi/loader/loader.conf",
            "options a\noptions b\ninitrd c\ninitrd d\n",
        );
        let sd = systemd_boot_json(&s, "/boot/efi").unwrap();
        let lc = sd.get("loader_conf").unwrap();
        assert_eq!(
            (
                lc.get_str("options"),
                lc.get_str("initrd"),
                lc.get("initrds")
            ),
            (Some("b"), Some("d"), None)
        );
        t.file(
            "/boot/efi/loader/loader.conf",
            "#timeout 3\ntimeout 5\ndefault debian-*\n",
        );
        std::fs::remove_file(t.p("/boot/efi/loader/entries/arch.conf")).unwrap();
        // The kernel and initrd an entry names in another directory
        // (sdbootutil's, on Aeon), once each, after the rest; nothing for
        // a name that isn't there, one outside the ESP or at its top
        // (debian-6.12.conf's /vmlinuz).
        t.file("/boot/efi/aeon/7.3.3-1-default/linux-1fe2", vec![1u8; 30])
            .file("/boot/efi/aeon/7.3.3-1-default/initrd-7d97", vec![2u8; 20])
            .file("/boot/efi/aeon/other", "x")
            .file("/boot/efi/vmlinuz", "k");
        let e = "title Aeon\nlinux /aeon/7.3.3-1-default/linux-1fe2\ninitrd /aeon/7.3.3-1-default/initrd-7d97\ninitrd /aeon/gone\noptions x\n";
        t.file("/boot/efi/loader/entries/aeon-7.3.3-1-default-1+3.conf", e)
            .file(
                "/boot/efi/loader/entries/aeon-7.3.3-1-default-2.conf",
                e.replace("linux /aeon", "linux /../aeon"),
            );
        let inv = esp_inventory(&s, "/boot/efi");
        let files = inv.get("files").unwrap().as_array().unwrap();
        let tail: Vec<(&str, u64)> = files
            .iter()
            .skip(9)
            .filter_map(|f| Some((f.get("path")?.as_str()?, f.get("size")?.as_u64()?)))
            .collect();
        assert_eq!(
            tail,
            vec![
                ("aeon/7.3.3-1-default/linux-1fe2", 30),
                ("aeon/7.3.3-1-default/initrd-7d97", 20)
            ],
            "{}",
            inv.pretty()
        );
        assert_eq!(files.len(), 11);
    }

    #[test]
    fn kernels_and_initramfs() {
        let t = Tree::new("kernels");
        t.file("/boot/vmlinuz-6.12.74+deb13+1-amd64", vec![0u8; 10])
            .file("/boot/vmlinuz-6.12.115+deb13.1-amd64", vec![0u8; 20])
            .file("/boot/initrd.img-6.12.115+deb13.1-amd64", vec![0u8; 30])
            .file("/boot/config-6.12.115+deb13.1-amd64", "")
            .file("/boot/System.map-6.1.0-44-amd64", "")
            .dir("/usr/lib/modules/6.12.115+deb13.1-amd64")
            .symlink("/vmlinuz", "boot/vmlinuz-6.12.115+deb13.1-amd64")
            .file(
                "/etc/initramfs-tools/initramfs.conf",
                "MODULES=most\nBUSYBOX=auto\nCOMPRESS=zstd\n",
            )
            .file("/etc/initramfs-tools/conf.d/resume", "RESUME=UUID=abc\n");
        let s = Sys::new(t.root(), Box::new(FakeRunner::default()), false);
        let k = kernels_json(&s, Some("6.12.115+deb13.1-amd64"));
        let list = k.get("installed").unwrap().as_array().unwrap();
        let vers: Vec<&str> = list
            .iter()
            .filter_map(|x| x.get("version").and_then(Value::as_str))
            .collect();
        assert_eq!(
            vers,
            vec![
                "6.1.0-44-amd64",
                "6.12.74+deb13+1-amd64",
                "6.12.115+deb13.1-amd64"
            ]
        );
        assert_eq!(list[2].get("running"), Some(&Value::Bool(true)));
        assert_eq!(
            list[2].get("initrd_bytes").and_then(Value::as_u64),
            Some(30)
        );
        assert_eq!(list[2].get("modules_installed"), Some(&Value::Bool(true)));
        assert!(list[1].get("initrd_bytes").unwrap().is_null());
        assert!(list[0].get("vmlinuz_bytes").unwrap().is_null());
        assert_eq!(
            k.at(&["symlinks", "/vmlinuz"]).and_then(Value::as_str),
            Some("boot/vmlinuz-6.12.115+deb13.1-amd64")
        );
        let i = initramfs_json(&s, &pk(&["initramfs-tools"]), false);
        assert_eq!(
            i.get("generator").and_then(Value::as_str),
            Some("initramfs-tools")
        );
        assert_eq!(i.get("compress").and_then(Value::as_str), Some("zstd"));
        assert_eq!(i.get("resume").and_then(Value::as_str), Some("UUID=abc"));
        assert_eq!(
            initramfs_json(&s, &pk(&["dracut", "initramfs-tools"]), false)
                .get("generator")
                .and_then(Value::as_str),
            Some("dracut")
        );
        assert_eq!(
            initramfs_json(&s, &pk(&["mkinitfs"]), false)
                .get("generator")
                .and_then(Value::as_str),
            Some("mkinitfs")
        );
    }

    #[test]
    fn alpine_and_aerynos() {
        let apk = "C:Q1abc=\nP:mkinitfs\nV:3.12.0-r0\nA:x86_64\nS:1\n\nC:Q2\nP:grub-bios\nV:2.12-r9\nA:x86_64\n\n";
        assert_eq!(
            parse_apk_installed(apk),
            vec![
                Package::new("grub-bios", "2.12-r9", "x86_64"),
                Package::new("mkinitfs", "3.12.0-r0", "x86_64")
            ]
        );
        let t = Tree::new("alpine-kernels");
        t.file("/boot/vmlinuz-lts", vec![0u8; 10])
            .file("/boot/initramfs-lts", vec![0u8; 7])
            .file("/lib/apk/db/installed", apk);
        let s = Sys::new(t.root(), Box::new(FakeRunner::default()), false);
        assert_eq!(installed_packages(&s).len(), 2);
        let k = kernels_json(&s, None);
        let list = k.get("installed").unwrap().as_array().unwrap();
        assert_eq!(list.len(), 1);
        assert_eq!(list[0].get("version").and_then(Value::as_str), Some("lts"));
        assert_eq!(list[0].get("initrd_bytes").and_then(Value::as_u64), Some(7));
        let a = Tree::new("aerynos-kernels");
        a.file("/usr/lib/kernel/current.stable.initrd", "")
            .file("/usr/lib/kernel/current.stable.kernel", "");
        let s = Sys::new(a.root(), Box::new(FakeRunner::default()), false);
        assert_eq!(
            initramfs_json(&s, &pk(&["dracut"]), false)
                .get("generator")
                .and_then(Value::as_str),
            Some("prebuilt")
        );
    }

    /// Fedora through shim (fedora44-uefi, kinoite44): GRUB's package is
    /// grub2-efi-x64 and shim's shim-x64.  On ostree the kernels are in
    /// /boot/ostree, the initramfs comes with the image though dracut is
    /// installed, and /boot's loader entries aren't systemd-boot's.
    #[test]
    fn fedora_shim_and_ostree_kernels() {
        let p = pk(&["grub2-efi-x64", "shim-x64", "dracut", "grub2-tools"]);
        let l = detect_loader(&Evidence {
            uefi: true,
            current_file: Some("\\EFI\\fedora\\shimx64.efi".into()),
            loader_info: None,
            shim_variables: false,
            mbr_codes: vec![],
            packages: &p,
            esp_files: vec![],
            fallback_copy_of: None,
            zbm_images: vec![],
        });
        assert_eq!(l.get("name").and_then(Value::as_str), Some("grub"));
        assert_eq!(l.get("via_shim"), Some(&Value::Bool(true)));
        assert_eq!(
            l.get("package").and_then(Value::as_str),
            Some("grub2-efi-x64")
        );
        assert_eq!(
            l.get("shim_package").and_then(Value::as_str),
            Some("shim-x64")
        );
        // No shim, no shim_package.
        let l = detect_loader(&Evidence {
            uefi: true,
            current_file: Some("\\EFI\\fedora\\grubx64.efi".into()),
            loader_info: None,
            shim_variables: false,
            mbr_codes: vec![],
            packages: &p,
            esp_files: vec![],
            fallback_copy_of: None,
            zbm_images: vec![],
        });
        assert!(l.get("shim_package").is_none());
        let v = "7.3.3-200.fc44.x86_64";
        let d =
            "/boot/ostree/default-1cc099369334fad8952283b3221e6d78fd79263e6dedc35ce40302458681d413";
        let t = Tree::new("ostree-kernels");
        t.file(&format!("{d}/vmlinuz-{v}"), vec![0u8; 10])
            .file(&format!("{d}/initramfs-{v}.img"), vec![0u8; 7])
            .file(&format!("{d}/.vmlinuz-{v}.hmac"), "x")
            .file("/boot/ostree/stray", "x")
            .dir(&format!("/usr/lib/modules/{v}/kernel"))
            .file(
                "/boot/loader.1/entries/ostree-1.conf",
                "title Fedora\nversion 1\noptions ostree=/ostree/boot.1/default/x/1 rw\n",
            )
            .symlink("/boot/loader", "loader.1")
            .file("/proc/sys/kernel/osrelease", format!("{v}\n"));
        let s = Sys::new(t.root(), Box::new(FakeRunner::default()), false);
        let k = kernels_json(&s, Some(v));
        let list = k.get("installed").and_then(Value::as_array).unwrap();
        assert_eq!(list.len(), 1, "{}", k.compact());
        assert_eq!(list[0].get("version").and_then(Value::as_str), Some(v));
        assert_eq!(list[0].get("dir").and_then(Value::as_str), Some(d));
        assert_eq!(
            list[0].get("vmlinuz_bytes").and_then(Value::as_u64),
            Some(10)
        );
        assert_eq!(list[0].get("initrd_bytes").and_then(Value::as_u64), Some(7));
        assert_eq!(list[0].get("running"), Some(&Value::Bool(true)));
        assert_eq!(list[0].get("modules_installed"), Some(&Value::Bool(true)));
        assert_eq!(
            initramfs_json(&s, &p, true)
                .get("generator")
                .and_then(Value::as_str),
            Some("prebuilt")
        );
        assert_eq!(
            initramfs_json(&s, &p, false)
                .get("generator")
                .and_then(Value::as_str),
            Some("dracut")
        );
        let b = boot_json(
            &s,
            &p,
            &[],
            Value::Null,
            vec![],
            Some(Obj::new().set("booted", true).build()),
            None,
            &[],
            &Value::Null,
        );
        assert!(b.get("systemd_boot").unwrap().is_null(), "{}", b.compact());
        assert_eq!(b.at(&["ostree", "booted"]), Some(&Value::Bool(true)));
        // Without ostree, the same entries read as systemd-boot's on an XBOOTLDR.
        let b = boot_json(
            &s,
            &p,
            &[],
            Value::Null,
            vec![],
            None,
            None,
            &[],
            &Value::Null,
        );
        assert_eq!(
            b.at(&["systemd_boot", "entries", "0", "file"])
                .and_then(Value::as_str),
            Some("ostree-1.conf")
        );
        assert!(b.get("ostree").is_none());
    }

    /// Portage's PF: the name keeps its dashes and digits; the version is
    /// read from the end (revision, suffixes, then N(.N)*[a-z]?).
    /// Shaped like the chimera guest (BIOS, GRUB in the MBR): apk-tools 3
    /// still writes v2's installed database, reached through /lib ->
    /// usr/lib; initramfs-tools names the kernel's files as Debian's does.
    #[test]
    fn chimera() {
        let apk = "C:Q1aaa=\nP:linux-lts\nV:6.18.62-r0\nA:x86_64\no:linux-lts\n\nC:Q1bbb=\nP:grub\nV:2.14-r1\nA:x86_64\n\nC:Q1ccc=\nP:grub-i386-pc\nV:2.14-r1\nA:x86_64\n\nC:Q1ddd=\nP:initramfs-tools\nV:0.148.3-r2\nA:noarch\n\n";
        let v = "6.18.62-0-generic";
        let t = Tree::new("chimera-boot");
        t.symlink("/lib", "usr/lib")
            .file("/usr/lib/apk/db/installed", apk)
            .file(&format!("/boot/vmlinuz-{v}"), vec![0u8; 10])
            .file(&format!("/boot/initrd.img-{v}"), vec![0u8; 7])
            .file(&format!("/boot/config-{v}"), "")
            .dir(&format!("/usr/lib/modules/{v}/kernel"))
            .file("/boot/grub/i386-pc/normal.mod", "x")
            .file("/boot/grub/i386-pc/core.img", "x")
            .file("/etc/default/grub", "GRUB_DEFAULT=0\nGRUB_TIMEOUT=5\n")
            .file(
                "/etc/initramfs-tools/initramfs.conf",
                "MODULES=most\nBUSYBOX=auto\nCOMPRESS=zstd\n",
            )
            .file("/proc/sys/kernel/osrelease", format!("{v}\n"));
        let s = Sys::new(t.root(), Box::new(FakeRunner::default()), false);
        let p = installed_packages(&s);
        let names: Vec<&str> = p.iter().map(|p| p.name.as_str()).collect();
        assert_eq!(
            names,
            vec!["grub", "grub-i386-pc", "initramfs-tools", "linux-lts"]
        );
        let b = boot_json(
            &s,
            &p,
            &[],
            Value::Null,
            vec![("vda".into(), "grub2".into())],
            None,
            None,
            &[],
            &Value::Null,
        );
        assert_eq!(
            b.at(&["loader", "name"]).and_then(Value::as_str),
            Some("grub")
        );
        assert_eq!(
            b.at(&["loader", "firmware"]).and_then(Value::as_str),
            Some("bios")
        );
        assert_eq!(
            b.at(&["loader", "package"]).and_then(Value::as_str),
            Some("grub-i386-pc")
        );
        assert_eq!(
            b.at(&["grub", "dir"]).and_then(Value::as_str),
            Some("/boot/grub")
        );
        assert_eq!(
            b.at(&["grub", "installed_platforms", "0"])
                .and_then(Value::as_str),
            Some("i386-pc")
        );
        assert!(b.at(&["grub", "debconf"]).unwrap().is_null());
        assert_eq!(
            b.at(&["initramfs", "generator"]).and_then(Value::as_str),
            Some("initramfs-tools")
        );
        assert_eq!(
            b.at(&["initramfs", "modules"]).and_then(Value::as_str),
            Some("most")
        );
        assert_eq!(
            b.at(&["initramfs", "compress"]).and_then(Value::as_str),
            Some("zstd")
        );
        let k = b
            .at(&["kernels", "installed"])
            .and_then(Value::as_array)
            .unwrap();
        assert_eq!(k.len(), 1, "{}", b.get("kernels").unwrap().compact());
        assert_eq!(k[0].get("version").and_then(Value::as_str), Some(v));
        assert_eq!(k[0].get("vmlinuz_bytes").and_then(Value::as_u64), Some(10));
        assert_eq!(k[0].get("initrd_bytes").and_then(Value::as_u64), Some(7));
        assert_eq!(k[0].get("config"), Some(&Value::Bool(true)));
        assert_eq!(k[0].get("running"), Some(&Value::Bool(true)));
        assert_eq!(k[0].get("modules_installed"), Some(&Value::Bool(true)));
        assert_eq!(
            b.at(&["packages", "grub-i386-pc"]).and_then(Value::as_str),
            Some("2.14-r1")
        );
        assert_eq!(
            b.at(&["packages", "linux-lts"]).and_then(Value::as_str),
            Some("6.18.62-r0")
        );
        // chimera-zfs: UEFI GRUB from grub-x86_64-efi, found on the ESP.
        let p = pk(&[
            "grub",
            "grub-x86_64-efi",
            "initramfs-tools",
            "linux-lts-zfs-bin",
            "zfs",
        ]);
        let l = detect_loader(&Evidence {
            uefi: true,
            current_file: Some("\\EFI\\chimera\\grubx64.efi".into()),
            loader_info: None,
            shim_variables: false,
            mbr_codes: vec![],
            packages: &p,
            esp_files: vec!["EFI/chimera/grubx64.efi".into()],
            fallback_copy_of: None,
            zbm_images: vec![],
        });
        assert_eq!(l.get("name").and_then(Value::as_str), Some("grub"));
        assert_eq!(
            l.get("package").and_then(Value::as_str),
            Some("grub-x86_64-efi")
        );
        assert_eq!(l.get("via_shim"), Some(&Value::Bool(false)));
    }

    /// `AnduinOS` (docs/design/anduinos.md): shim from `BootCurrent` in
    /// EFI/AnduinOS, or from the fallback path when BOOTX64.EFI is a copy of
    /// shim; anduinos-grub and anduinos-shim-signed as the packages; and
    /// boot.loader.mok from MOK.der, only beside anduinos-grub.
    #[test]
    fn anduinos_loader_and_mok() {
        let p = pk(&["anduinos-grub", "anduinos-shim-signed", "mokutil", "dracut"]);
        let files = || {
            vec![
                "EFI/AnduinOS/grubx64.efi".to_string(),
                "EFI/AnduinOS/mmx64.efi".into(),
                "EFI/AnduinOS/shimx64.efi".into(),
                "EFI/BOOT/BOOTX64.EFI".into(),
                "EFI/BOOT/grubx64.efi".into(),
            ]
        };
        let ev = |current: &str, copy: Option<&str>| Evidence {
            uefi: true,
            current_file: Some(current.into()),
            loader_info: None,
            shim_variables: false,
            mbr_codes: vec![],
            packages: &p,
            esp_files: files(),
            fallback_copy_of: copy.map(str::to_string),
            zbm_images: vec![],
        };
        let l = detect_loader(&ev("\\EFI\\AnduinOS\\shimx64.efi", None));
        assert_eq!(l.get("name").and_then(Value::as_str), Some("grub"));
        assert_eq!(l.get("via_shim"), Some(&Value::Bool(true)));
        assert_eq!(
            l.get("package").and_then(Value::as_str),
            Some("anduinos-grub")
        );
        assert_eq!(
            l.get("shim_package").and_then(Value::as_str),
            Some("anduinos-shim-signed")
        );
        // The fallback path, a copy of shim: through shim, said why.
        let l = detect_loader(&ev(
            "\\EFI\\BOOT\\BOOTX64.EFI",
            Some("EFI/AnduinOS/shimx64.efi"),
        ));
        assert_eq!(l.get("name").and_then(Value::as_str), Some("grub"));
        assert_eq!(l.get("via_shim"), Some(&Value::Bool(true)));
        assert!(
            l.get("evidence")
                .unwrap()
                .compact()
                .contains("EFI/BOOT/BOOTX64.EFI is a copy of EFI/AnduinOS/shimx64.efi"),
            "{}",
            l.compact()
        );
        // A fallback that's a copy of GRUB itself, or of nothing: not shim.
        for copy in [Some("EFI/AnduinOS/grubx64.efi"), None] {
            let l = detect_loader(&ev("\\EFI\\BOOT\\BOOTX64.EFI", copy));
            assert_eq!(l.get("via_shim"), Some(&Value::Bool(false)), "{copy:?}");
            assert!(l.get("shim_package").is_none(), "{copy:?}");
        }

        // The MOK: its certificate's SHA-256, read through Sys.
        let t = Tree::new("mok");
        let der = b"0\x82\x03\x1fnot really DER";
        t.file(ANDUINOS_MOK, &der[..])
            .file("/var/lib/shim-signed/mok/MOK.priv", "secret");
        let s = Sys::new(t.root(), Box::new(FakeRunner::default()), false);
        let m = mok_json(&s, &p).unwrap();
        assert_eq!(
            m.compact(),
            format!(
                r#"{{"cert":"/var/lib/shim-signed/mok/MOK.der","sha256":"{}"}}"#,
                crate::bytes::sha256_hex(der)
            )
        );
        assert_eq!(
            m.get("sha256").and_then(Value::as_str).map(str::len),
            Some(64)
        );
        // Not without anduinos-grub (a MOK of DKMS's, Ubuntu's signed GRUB),
        // nor without the certificate.
        assert!(
            mok_json(
                &s,
                &pk(&["grub-efi-amd64-signed", "shim-signed", "mokutil"])
            )
            .is_none()
        );
        let none = Tree::new("mok-none");
        assert!(
            mok_json(
                &Sys::new(none.root(), Box::new(FakeRunner::default()), false),
                &p
            )
            .is_none()
        );
    }

    #[test]
    fn loader_lilo_and_elilo() {
        let p = pk(&["lilo", "elilo"]);
        let base = |uefi: bool| Evidence {
            uefi,
            current_file: None,
            loader_info: None,
            shim_variables: false,
            mbr_codes: vec![],
            packages: &p,
            esp_files: vec![],
            fallback_copy_of: None,
            zbm_images: vec![],
        };
        let mut e = base(false);
        e.mbr_codes = vec![("vda".into(), "lilo".into())];
        let v = detect_loader(&e);
        assert_eq!(v.get("name").and_then(Value::as_str), Some("lilo"));
        assert_eq!(v.get("package").and_then(Value::as_str), Some("lilo"));
        assert!(
            v.get("evidence")
                .unwrap()
                .compact()
                .contains("vda's MBR holds LILO's first stage"),
            "{}",
            v.compact()
        );
        // GRUB in the MBR wins over LILO installed.
        e.mbr_codes = vec![("vda".into(), "grub2".into())];
        assert_eq!(
            detect_loader(&e).get("name").and_then(Value::as_str),
            Some("grub")
        );
        // ELILO from NVRAM's entry, or from the ESP's files at the fallback path.
        let mut e = base(true);
        e.current_file = Some("\\EFI\\Slackware\\elilo.efi".into());
        let v = detect_loader(&e);
        assert_eq!(v.get("name").and_then(Value::as_str), Some("elilo"));
        assert_eq!(v.get("package").and_then(Value::as_str), Some("elilo"));
        let mut e = base(true);
        e.current_file = Some("\\EFI\\BOOT\\BOOTX64.EFI".into());
        e.esp_files = vec!["EFI/BOOT/BOOTX64.EFI".into(), "EFI/BOOT/elilo.conf".into()];
        assert_eq!(
            detect_loader(&e).get("name").and_then(Value::as_str),
            Some("elilo")
        );
        // LILO's package alone (every Slackware has it) says nothing on UEFI.
        assert_eq!(
            detect_loader(&base(true))
                .get("name")
                .and_then(Value::as_str),
            Some("unknown")
        );
    }

    /// Solus's initrds ship in the kernel package (/usr/lib64/kernel, which
    /// /usr/lib/kernel is the same directory as): prebuilt, though dracut
    /// is installed.  The kernel alone isn't.
    #[test]
    fn solus_initrd_prebuilt() {
        let t = Tree::new("solus-kernels");
        t.file(
            "/usr/lib64/kernel/com.solus-project.current.6.18.21-333",
            "k",
        )
        .file("/usr/lib64/kernel/cmdline-6.18.21-333.current", "");
        let s = Sys::new(t.root(), Box::new(FakeRunner::default()), false);
        assert_eq!(
            initramfs_json(&s, &pk(&["dracut"]), false)
                .get("generator")
                .and_then(Value::as_str),
            Some("dracut")
        );
        t.file(
            "/usr/lib64/kernel/initrd-com.solus-project.current.6.18.21-333",
            "i",
        );
        assert_eq!(
            initramfs_json(&s, &pk(&["dracut"]), false)
                .get("generator")
                .and_then(Value::as_str),
            Some("prebuilt")
        );
        let u = Tree::new("solus-kernels-usr-lib");
        u.file(
            "/usr/lib/kernel/initrd-com.solus-project.lts.6.12.80-300",
            "i",
        );
        let s = Sys::new(u.root(), Box::new(FakeRunner::default()), false);
        assert_eq!(
            initramfs_json(&s, &pk(&["dracut"]), false)
                .get("generator")
                .and_then(Value::as_str),
            Some("prebuilt")
        );
    }

    /// Solus's kernels from /usr/lib64/kernel, named as uname -r names
    /// them (the solus49 guest's two), the default-current link beside
    /// them; only with clr-boot-manager installed.
    #[test]
    fn solus_kernel_list() {
        assert_eq!(
            solus_kernel_version("com.solus-project.current.7.3.4-373").as_deref(),
            Some("7.3.4-373.current")
        );
        assert_eq!(
            solus_kernel_version("com.solus-project.lts.6.18.63-322").as_deref(),
            Some("6.18.63-322.lts")
        );
        for f in [
            "initrd-com.solus-project.current.7.3.4-373",
            "com.solus-project.current",
            "com.solus-project.current.7.3.4",
            "com.solus-project.current.7.3.4-r1",
            "cmdline-7.3.4-373.current",
        ] {
            assert_eq!(solus_kernel_version(f), None, "{f}");
        }
        let t = Tree::new("solus-kernel-list");
        for v in ["6.18.21-333", "7.3.4-373"] {
            t.file(
                &format!("/usr/lib64/kernel/com.solus-project.current.{v}"),
                vec![0u8; 10],
            )
            .file(
                &format!("/usr/lib64/kernel/initrd-com.solus-project.current.{v}"),
                vec![0u8; 20],
            )
            .file(&format!("/usr/lib64/kernel/config-{v}.current"), "")
            .file(&format!("/usr/lib64/kernel/System.map-{v}.current"), "");
        }
        t.symlink(
            "/usr/lib64/kernel/default-current",
            "com.solus-project.current.7.3.4-373",
        )
        .symlink(
            "/usr/lib64/kernel/current-initrd",
            "initrd-com.solus-project.current.7.3.4-373",
        )
        .dir("/usr/lib64/kernel/cmdline.d")
        .dir("/usr/lib/modules/7.3.4-373.current/kernel");
        let s = Sys::new(t.root(), Box::new(FakeRunner::default()), false);
        let (list, links) = solus_kernels(&s, Some("7.3.4-373.current"));
        let got: Vec<(&str, bool, bool, Option<u64>)> = list
            .iter()
            .map(|k| {
                (
                    k.get("version").and_then(Value::as_str).unwrap(),
                    k.get("running") == Some(&Value::Bool(true)),
                    k.get("modules_installed") == Some(&Value::Bool(true)),
                    k.get("initrd_bytes").and_then(Value::as_u64),
                )
            })
            .collect();
        assert_eq!(
            got,
            vec![
                ("6.18.21-333.current", false, false, Some(20)),
                ("7.3.4-373.current", true, true, Some(20))
            ]
        );
        assert!(
            list.iter()
                .all(|k| k.get("config") == Some(&Value::Bool(true))
                    && k.get("dir").and_then(Value::as_str) == Some("/usr/lib64/kernel"))
        );
        assert_eq!(
            links,
            vec![(
                "/usr/lib64/kernel/default-current".to_string(),
                "com.solus-project.current.7.3.4-373".to_string()
            )]
        );
        let empty = kernels_json(&s, Some("7.3.4-373.current"));
        let with = with_solus_kernels(
            empty.clone(),
            &s,
            &pk(&["clr-boot-manager"]),
            Some("7.3.4-373.current"),
        );
        assert_eq!(
            with.get("installed")
                .and_then(Value::as_array)
                .map(Vec::len),
            Some(2)
        );
        assert_eq!(
            with.at(&["symlinks", "/usr/lib64/kernel/default-current"])
                .and_then(Value::as_str),
            Some("com.solus-project.current.7.3.4-373")
        );
        assert_eq!(
            with_solus_kernels(empty.clone(), &s, &pk(&["dracut"]), None).compact(),
            empty.compact()
        );
    }

    /// clr-boot-manager's shim and systemd-boot by their fixed names
    /// (Solus): from NVRAM's entry, from the fallback copy of the shim,
    /// from `LoaderInfo`, or from the ESP's files alone; not without
    /// clr-boot-manager installed.
    #[test]
    fn loader_clr_boot_manager() {
        fn base(p: &[Package]) -> Evidence<'_> {
            let esp = [
                "EFI/BOOT/BOOTX64.EFI",
                "EFI/BOOT/fbx64.efi",
                "EFI/com.solus-project/bootloaderx64.efi",
                "EFI/com.solus-project/grubx64.efi",
                "EFI/com.solus-project/mmx64.efi",
                "loader/loader.conf",
            ];
            Evidence {
                uefi: true,
                current_file: None,
                loader_info: None,
                shim_variables: false,
                mbr_codes: vec![],
                packages: p,
                esp_files: esp.iter().map(|f| f.to_string()).collect(),
                fallback_copy_of: None,
                zbm_images: vec![],
            }
        }
        let p = &pk(&["clr-boot-manager", "shim-signed", "dracut"]);
        let check = |e: &Evidence<'_>, why: &str| {
            let v = detect_loader(e);
            assert_eq!(
                v.get("name").and_then(Value::as_str),
                Some("systemd-boot"),
                "{}",
                v.compact()
            );
            assert_eq!(
                v.get("via_shim"),
                Some(&Value::Bool(true)),
                "{}",
                v.compact()
            );
            assert_eq!(
                v.get("manager").and_then(Value::as_str),
                Some("clr-boot-manager")
            );
            assert_eq!(
                v.get("shim_package").and_then(Value::as_str),
                Some("shim-signed")
            );
            assert!(
                v.get("evidence").unwrap().compact().contains(why),
                "{}",
                v.compact()
            );
        };
        let mut e = base(p);
        e.current_file = Some("\\EFI\\com.solus-project\\bootloaderx64.efi".into());
        check(
            &e,
            "that is clr-boot-manager's shim (EFI/com.solus-project/bootloaderx64.efi), which loads systemd-boot as EFI/com.solus-project/grubx64.efi",
        );
        // A fresh OVMF store: the removable path, a copy of the shim.
        let mut e = base(p);
        e.current_file = Some("\\EFI\\BOOT\\BOOTX64.EFI".into());
        e.fallback_copy_of = Some("EFI/com.solus-project/bootloaderx64.efi".into());
        check(
            &e,
            "EFI/BOOT/BOOTX64.EFI is a copy of EFI/com.solus-project/bootloaderx64.efi, clr-boot-manager's shim",
        );
        e.loader_info = Some("systemd-boot 257.10".into());
        check(&e, "LoaderInfo says \\\"systemd-boot 257.10\\\"");
        // No BootCurrent: the ESP's files.
        check(
            &base(p),
            "an ESP holds clr-boot-manager's EFI/com.solus-project/grubx64.efi, which is systemd-boot",
        );
        // Without clr-boot-manager, grubx64.efi is GRUB's, and no manager.
        let v = detect_loader(&base(&pk(&["shim-signed"])));
        assert_eq!(v.get("name").and_then(Value::as_str), Some("grub"));
        assert!(v.get("manager").is_none());
    }
}
