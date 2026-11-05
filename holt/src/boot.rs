//! The boot setup above the firmware: which loader boots the system and how
//! it's configured (GRUB, systemd-boot, shim), what's on the EFI system
//! partitions, the kernels and initramfs images in /boot, the kernel command
//! line, and the Debian packages behind all of it.

use crate::json::{Obj, Value};
use crate::sys::Sys;

// --- Debian package state -------------------------------------------------------

/// Installed packages from /var/lib/dpkg/status: (name, version, arch).
pub fn parse_dpkg_status(text: &str) -> Vec<(String, String, String)> {
    let mut out = Vec::new();
    for stanza in text.split("\n\n") {
        let (mut name, mut ver, mut arch, mut installed) = (None, None, None, false);
        for line in stanza.lines() {
            if let Some(v) = line.strip_prefix("Package: ") {
                name = Some(v.trim().to_string());
            } else if let Some(v) = line.strip_prefix("Version: ") {
                ver = Some(v.trim().to_string());
            } else if let Some(v) = line.strip_prefix("Architecture: ") {
                arch = Some(v.trim().to_string());
            } else if let Some(v) = line.strip_prefix("Status: ") {
                installed = v.split_whitespace().nth(2) == Some("installed");
            }
        }
        if let (Some(n), Some(v), true) = (name, ver, installed) {
            out.push((n, v, arch.unwrap_or_default()));
        }
    }
    out
}

/// Installed packages from pacman's local database (Arch): each
/// /var/lib/pacman/local/NAME-VERSION/desc holds %NAME%, %VERSION%, %ARCH%.
pub fn pacman_packages(sys: &Sys) -> Vec<(String, String, String)> {
    let mut out = Vec::new();
    for d in sys.list("/var/lib/pacman/local") {
        let Some(t) = sys.read(&format!("/var/lib/pacman/local/{d}/desc")) else { continue };
        let field = |k: &str| {
            let mut it = t.lines();
            it.by_ref().find(|l| l.trim() == k)?;
            it.next().map(|v| v.trim().to_string())
        };
        if let (Some(n), Some(v)) = (field("%NAME%"), field("%VERSION%")) {
            out.push((n, v, field("%ARCH%").unwrap_or_default()));
        }
    }
    out.sort();
    out
}

/// Installed packages from apk's database (Alpine): blocks of `K:value`
/// lines, P the name, V the version, A the architecture.
pub fn parse_apk_installed(text: &str) -> Vec<(String, String, String)> {
    let mut out = Vec::new();
    for block in text.split("\n\n") {
        let f = |k: &str| block.lines().find_map(|l| l.strip_prefix(k)).map(|v| v.trim().to_string());
        if let (Some(n), Some(v)) = (f("P:"), f("V:")) {
            out.push((n, v, f("A:").unwrap_or_default()));
        }
    }
    out.sort();
    out
}

/// Installed packages from rpm (Fedora): its database is SQLite, so rpm
/// itself reads it.
pub fn rpm_packages(sys: &Sys) -> Vec<(String, String, String)> {
    let Some(t) = sys.run("rpm", &["-qa", "--qf", "%{NAME}\t%{VERSION}-%{RELEASE}\t%{ARCH}\n"]) else { return Vec::new() };
    let mut out: Vec<(String, String, String)> = t
        .lines()
        .filter_map(|l| {
            let mut f = l.split('\t');
            Some((f.next()?.to_string(), f.next()?.to_string(), f.next().unwrap_or("").to_string()))
        })
        .collect();
    out.sort();
    out
}

/// Installed packages from xbps's database (Void): a property list whose
/// top-level dict has a dict per package, with `pkgver` ("NAME-VERSION_REV")
/// and `architecture`.
pub fn parse_xbps_pkgdb(text: &str) -> Vec<(String, String, String)> {
    let mut out = Vec::new();
    let mut depth = 0usize;
    let (mut name, mut key) = (String::new(), String::new());
    let (mut ver, mut arch) = (String::new(), String::new());
    let mut rest = text;
    while let Some(i) = rest.find('<') {
        let Some(j) = rest[i..].find('>') else { break };
        let tag = &rest[i + 1..i + j];
        let after = &rest[i + j + 1..];
        let body = || after.split('<').next().unwrap_or("").replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">");
        match tag {
            "dict" => depth += 1,
            "/dict" => {
                if depth == 2 {
                    if !ver.is_empty() {
                        let v = ver.strip_prefix(&format!("{name}-")).unwrap_or(&ver).to_string();
                        out.push((name.clone(), v, arch.clone()));
                    }
                    name.clear();
                    ver.clear();
                    arch.clear();
                }
                depth = depth.saturating_sub(1);
            }
            "key" if depth == 1 => name = body(),
            "key" => key = body(),
            "string" if depth == 2 && key == "pkgver" => ver = body(),
            "string" if depth == 2 && key == "architecture" => arch = body(),
            _ => {}
        }
        rest = after;
    }
    out.sort();
    out
}

/// The installed packages, from whichever package manager the system has.
pub fn installed_packages(sys: &Sys) -> Vec<(String, String, String)> {
    if let Some(t) = sys.read("/var/lib/dpkg/status") {
        return parse_dpkg_status(&t);
    }
    if sys.is_dir("/var/lib/pacman/local") {
        return pacman_packages(sys);
    }
    if sys.is_dir("/var/lib/rpm") || sys.is_dir("/usr/lib/sysimage/rpm") {
        return rpm_packages(sys);
    }
    if let Some(t) = sys.read("/lib/apk/db/installed") {
        return parse_apk_installed(&t);
    }
    if let Some(f) = sys.list("/var/db/xbps").into_iter().filter(|f| f.starts_with("pkgdb-") && f.ends_with(".plist")).max() {
        if let Some(t) = sys.read(&format!("/var/db/xbps/{f}")) {
            return parse_xbps_pkgdb(&t);
        }
    }
    Vec::new()
}

/// The packages that make up the storage and boot stack.
pub const PACKAGES: &[&str] = &[
    "grub-pc", "grub-pc-bin", "grub-efi-amd64", "grub-efi-amd64-bin", "grub-efi-amd64-signed", "grub-efi-ia32",
    "grub-efi-ia32-bin", "grub-efi-arm64", "grub-efi-arm64-signed", "grub2-common", "grub-common", "shim-signed",
    "shim-unsigned", "shim-helpers-amd64-signed", "mokutil", "efibootmgr", "systemd-boot", "systemd-boot-efi",
    "systemd-ukify", "refind", "syslinux", "extlinux", "syslinux-efi", "os-prober", "linux-image-amd64",
    "linux-image-cloud-amd64", "linux-image-rt-amd64", "initramfs-tools", "dracut", "tiny-initramfs", "mdadm",
    "lvm2", "thin-provisioning-tools", "cryptsetup", "cryptsetup-initramfs", "cryptsetup-bin", "clevis-luks",
    "systemd-cryptsetup", "tpm2-tools", "zfsutils-linux", "zfs-initramfs", "zfs-dracut", "zfs-dkms", "btrfs-progs",
    "xfsprogs", "e2fsprogs", "dosfstools", "exfatprogs", "ntfs-3g", "jfsutils", "f2fs-tools", "nilfs-tools", "bcachefs-tools",
    // Arch's and Fedora's names for the same.
    "grub", "grub2-pc", "grub2-efi-x64", "grub2-tools", "shim-x64", "mkinitcpio", "linux", "linux-lts", "kernel-core",
    "zfs-utils", "selinux-policy-targeted",
    // Alpine's.
    "grub-bios", "grub-efi", "mkinitfs", "linux-virt",
    // Omarchy's, openSUSE's and Void's.
    "limine", "limine-mkinitcpio-hook", "limine-snapper-sync", "grub2", "grub2-i386-pc", "grub2-x86_64-efi", "kernel-default", "snapper", "linux-base",
    "multipath-tools", "dmraid", "nvme-cli", "open-iscsi",
];

/// debconf answers from /var/cache/debconf/config.dat whose names start with
/// one of `prefixes`.
pub fn parse_debconf(text: &str, prefixes: &[&str]) -> Vec<(String, String)> {
    let mut out = Vec::new();
    for rec in text.split("\n\n") {
        let mut name = None;
        let mut value = None;
        for line in rec.lines() {
            if let Some(v) = line.strip_prefix("Name: ") {
                name = Some(v.trim().to_string());
            } else if let Some(v) = line.strip_prefix("Value:") {
                value = Some(v.trim().to_string());
            }
        }
        if let (Some(n), Some(v)) = (name, value) {
            if prefixes.iter().any(|p| n.starts_with(p)) {
                out.push((n, v));
            }
        }
    }
    out
}

// --- Shell-ish text -------------------------------------------------------------

/// Splits one line into words the way sh would for plain quoting: '...',
/// "..." (with \\ \" \$ escapes), and backslash escapes outside quotes.
/// Words glued to quotes ('a'\''b') come out as one word.
pub fn shell_words(line: &str) -> Vec<String> {
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
                        '\\' if matches!(it.peek(), Some('"' | '\\' | '$' | '`')) => cur.push(it.next().unwrap()),
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
pub fn parse_shell_assignments(text: &str) -> Vec<(String, String)> {
    let mut out: Vec<(String, String)> = Vec::new();
    for line in text.lines() {
        let line = line.trim();
        let line = line.strip_prefix("export ").unwrap_or(line);
        let Some((k, v)) = line.split_once('=') else { continue };
        if k.is_empty() || !k.chars().all(|c| c.is_ascii_alphanumeric() || c == '_') || k.starts_with('#') {
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
pub struct GrubCfg {
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

pub fn parse_grub_cfg(text: &str) -> GrubCfg {
    let mut g = GrubCfg::default();
    // Open blocks: Some(title) for submenus, None for other braces.
    let mut stack: Vec<Option<String>> = Vec::new();
    let mut in_entry = 0usize;
    let mut first_entry_done = false;
    for raw in text.lines() {
        let w = shell_words(raw.trim());
        let Some(cmd) = w.first().map(String::as_str) else { continue };
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
            "linux" | "linuxefi" if in_entry > 0 && !first_entry_done && g.default_linux.is_none() => {
                g.default_linux = Some(w[1..].join(" "));
            }
            "initrd" | "initrdefi" if in_entry > 0 && !first_entry_done && g.default_initrd.is_none() => {
                g.default_initrd = Some(w[1..].join(" "));
            }
            "set" => {
                if let Some(p) = w.get(1).and_then(|x| x.strip_prefix("prefix=")) {
                    g.prefix = Some(p.to_string());
                }
                if let (Some(l), true) = (w.get(1).and_then(|x| x.strip_prefix("lang=")), stack.is_empty()) {
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
pub fn parse_grubenv(text: &str) -> Vec<(String, String)> {
    text.lines()
        .filter(|l| !l.starts_with('#'))
        .filter_map(|l| l.split_once('=').map(|(k, v)| (k.to_string(), v.to_string())))
        .collect()
}

fn grub_json(sys: &Sys) -> Value {
    let mut o = Obj::new();
    // Fedora's GRUB lives in /boot/grub2.
    let dir = if !sys.is_dir("/boot/grub") && sys.is_dir("/boot/grub2") { "/boot/grub2" } else { "/boot/grub" };
    o.put("dir", dir);
    let platforms: Vec<String> = sys
        .list(dir)
        .into_iter()
        .filter(|d| sys.is_dir(&format!("{dir}/{d}")) && d.contains('-') && d != "x86_64-efi-signed")
        .filter(|d| sys.is_file(&format!("{dir}/{d}/normal.mod")) || sys.is_file(&format!("{dir}/{d}/core.img")) || sys.is_file(&format!("{dir}/{d}/core.efi")))
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
    o.put("grubenv", sys.read(&format!("{dir}/grubenv")).map(|t| pairs_obj(&parse_grubenv(&t))));
    let mut defaults = sys.read("/etc/default/grub").map(|t| parse_shell_assignments(&t)).unwrap_or_default();
    for f in sys.list("/etc/default/grub.d") {
        if f.ends_with(".cfg") {
            for (k, v) in sys.read(&format!("/etc/default/grub.d/{f}")).map(|t| parse_shell_assignments(&t)).unwrap_or_default() {
                defaults.retain(|(key, _)| *key != k);
                defaults.push((k, v));
            }
        }
    }
    o.put("default_grub", pairs_obj(&defaults));
    o.put(
        "debconf",
        sys.read("/var/cache/debconf/config.dat").map(|t| pairs_obj(&parse_debconf(&t, &["grub-pc/", "grub2/", "grub-efi/"]))),
    );
    o.build()
}

// --- EFI system partitions --------------------------------------------------------

/// Files under an ESP's EFI/ and loader/ directories (relative paths, sizes),
/// noting which ones are byte-identical copies (the removable-media fallback
/// EFI/BOOT/BOOTX64.EFI usually is one).
pub fn esp_inventory(sys: &Sys, mount: &str) -> Value {
    let mut files: Vec<(String, u64)> = Vec::new();
    fn walk(sys: &Sys, base: &str, rel: &str, depth: usize, out: &mut Vec<(String, u64)>) {
        if depth > 4 || out.len() >= 500 {
            return;
        }
        let dir = if rel.is_empty() { base.to_string() } else { format!("{base}/{rel}") };
        for e in sys.list(&dir) {
            let r = if rel.is_empty() { e.clone() } else { format!("{rel}/{e}") };
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
    let efi: Vec<&(String, u64)> = files.iter().filter(|(p, _)| p.to_ascii_lowercase().ends_with(".efi")).collect();
    let mut list = Vec::new();
    for (path, size) in &files {
        let mut o = Obj::new().set("path", path.as_str()).set("size", *size);
        if path.to_ascii_lowercase().ends_with(".efi") && *size <= 64 << 20 {
            let twin = efi.iter().find(|(p, s)| p != path && s == size && {
                let a = sys.read_bytes(&format!("{mount}/{path}"));
                a.is_some() && a == sys.read_bytes(&format!("{mount}/{p}"))
            });
            if let Some((p, _)) = twin {
                o.put("same_as", p.as_str());
            }
        }
        list.push(o.build());
    }
    let find = |want: &str| files.iter().find(|(p, _)| p.eq_ignore_ascii_case(want)).map(|(p, _)| p.clone());
    let stub = find("EFI/debian/grub.cfg").and_then(|p| sys.read(&format!("{mount}/{p}"))).map(|t| parse_grub_cfg(&t));
    Obj::new()
        .set("files", list)
        .set(
            "debian_grub_stub",
            stub.map(|g| Obj::new().set("search_uuids", g.search_uuids).set("prefix", g.prefix).set("configfile", g.configfile)),
        )
        .build()
}

/// systemd-boot's loader.conf and its Boot Loader Specification entries.
pub fn systemd_boot_json(sys: &Sys, mount: &str) -> Option<Value> {
    let conf = sys.read(&format!("{mount}/loader/loader.conf"));
    let entries_dir = format!("{mount}/loader/entries");
    let entry_files: Vec<String> = sys.list(&entries_dir).into_iter().filter(|f| f.ends_with(".conf")).collect();
    if conf.is_none() && entry_files.is_empty() {
        return None;
    }
    let kv = |t: &str| {
        let mut o = Obj::new();
        for l in t.lines() {
            let l = l.trim();
            if l.is_empty() || l.starts_with('#') {
                continue;
            }
            let (k, v) = l.split_once(char::is_whitespace).unwrap_or((l, ""));
            o.put(k, v.trim());
        }
        o.build()
    };
    let entries: Vec<Value> = entry_files
        .iter()
        .filter_map(|f| sys.read(&format!("{entries_dir}/{f}")).map(|t| (f, t)))
        .map(|(f, t)| {
            let mut v = kv(&t);
            if let Value::Obj(o) = &mut v {
                o.insert(0, ("file".into(), Value::from(f.as_str())));
            }
            v
        })
        .collect();
    Some(Obj::new().set("loader_conf", conf.map(|t| kv(&t))).set("entries", entries).build())
}

// --- Kernels -----------------------------------------------------------------------

pub fn kernels_json(sys: &Sys, running: Option<&str>) -> Value {
    let mut versions: Vec<String> = Vec::new();
    let files = sys.list("/boot");
    for f in &files {
        // Debian's initrd.img-V, Arch's and Fedora's initramfs-V.img.
        // Alpine's are vmlinuz-FLAVOR and initramfs-FLAVOR, with no .img.
        let initramfs = f.strip_prefix("initramfs-").map(|v| v.strip_suffix(".img").unwrap_or(v)).filter(|v| !v.ends_with("-fallback") && !v.ends_with("kdump"));
        for p in ["vmlinuz-", "initrd.img-", "config-", "System.map-", "initramfs-"] {
            let v = if p == "initramfs-" { initramfs } else { f.strip_prefix(p) };
            if let Some(v) = v {
                if !versions.iter().any(|x| x == v) {
                    versions.push(v.to_string());
                }
            }
        }
    }
    versions.sort_by(|a, b| crate::sys::natural_cmp(a, b));
    let list: Vec<Value> = versions
        .iter()
        .map(|v| {
            Obj::new()
                .set("version", v.as_str())
                .set("vmlinuz_bytes", sys.file_size(&format!("/boot/vmlinuz-{v}")))
                .set(
                    "initrd_bytes",
                    sys.file_size(&format!("/boot/initrd.img-{v}"))
                        .or_else(|| sys.file_size(&format!("/boot/initramfs-{v}.img")))
                        .or_else(|| sys.file_size(&format!("/boot/initramfs-{v}"))),
                )
                .set("config", sys.is_file(&format!("/boot/config-{v}")))
                .set("running", running == Some(v.as_str()))
                .set("modules_installed", sys.is_dir(&format!("/lib/modules/{v}")) || sys.is_dir(&format!("/usr/lib/modules/{v}")))
                .build()
        })
        .collect();
    let mut links = Obj::new();
    for l in ["/vmlinuz", "/vmlinuz.old", "/initrd.img", "/initrd.img.old", "/boot/vmlinuz", "/boot/initrd.img"] {
        if let Some(t) = sys.readlink(l) {
            links.put(l, t);
        }
    }
    Obj::new().set("installed", list).set("symlinks", links).build()
}

/// /proc/cmdline split into words, with the storage-related ones picked out.
pub fn cmdline_json(cmdline: &str) -> Value {
    let words = shell_words(cmdline);
    let mut picked = Obj::new();
    for w in &words {
        let (k, v) = w.split_once('=').unwrap_or((w.as_str(), ""));
        if matches!(
            k,
            "root" | "rootflags" | "rootfstype" | "ro" | "rw" | "resume" | "resume_offset" | "cryptdevice" | "rd.luks.uuid"
                | "rd.luks.name" | "rd.lvm.vg" | "rd.lvm.lv" | "rd.md.uuid" | "rd.auto" | "zfs" | "zfsforce" | "boot"
                | "BOOT_IMAGE" | "init" | "systemd.mount-extra" | "rootdelay"
        ) {
            picked.put(k, v);
        }
    }
    Obj::new().set("raw", cmdline.trim()).set("words", words).set("storage", picked).build()
}

fn initramfs_json(sys: &Sys, packages: &[(String, String, String)]) -> Value {
    let has = |p: &str| packages.iter().any(|(n, _, _)| n == p);
    // AerynOS: each kernel package ships its initramfs (/usr/lib/kernel's
    // *.initrd), which blsforme copies to the ESP; nothing is generated.
    let prebuilt = sys.list("/usr/lib/kernel").iter().any(|f| f.ends_with(".initrd"));
    let generator = if prebuilt {
        Some("prebuilt")
    } else if has("mkinitcpio") {
        Some("mkinitcpio")
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
    let conf = sys.read("/etc/initramfs-tools/initramfs.conf").map(|t| parse_shell_assignments(&t)).unwrap_or_default();
    let resume = sys
        .read("/etc/initramfs-tools/conf.d/resume")
        .and_then(|t| parse_shell_assignments(&t).into_iter().find(|(k, _)| k == "RESUME").map(|(_, v)| v));
    let pick = |k: &str| conf.iter().find(|(key, _)| key == k).map(|(_, v)| v.clone());
    Obj::new()
        .set("generator", generator)
        .set("modules", pick("MODULES"))
        .set("compress", pick("COMPRESS"))
        .set("resume", resume)
        .set("crypttab_in_initramfs", sys.is_file("/etc/crypttab"))
        .build()
}

// --- Which loader ------------------------------------------------------------------

/// What the report already knows that decides the loader.
pub struct Evidence<'a> {
    pub uefi: bool,
    /// The file of the entry the firmware booted (BootCurrent).
    pub current_file: Option<String>,
    pub loader_info: Option<String>,
    pub shim_variables: bool,
    /// (disk, boot code kind) for every disk.
    pub mbr_codes: Vec<(String, String)>,
    pub packages: &'a [(String, String, String)],
    pub esp_files: Vec<String>,
}

pub fn detect_loader(e: &Evidence) -> Value {
    let has = |p: &str| e.packages.iter().any(|(n, _, _)| n == p);
    let mut why: Vec<String> = Vec::new();
    let mut name: Option<&str> = None;
    let mut shim = false;
    if e.uefi {
        if let Some(f) = &e.current_file {
            let u = f.to_ascii_uppercase().replace('/', "\\");
            why.push(format!("firmware booted {f}"));
            if u.contains("SHIM") {
                shim = true;
                name = Some("grub");
            } else if u.contains("GRUB") {
                name = Some("grub");
            } else if u.contains("SYSTEMD-BOOT") || u.contains("\\EFI\\SYSTEMD\\") {
                name = Some("systemd-boot");
            } else if u.contains("REFIND") {
                name = Some("refind");
            } else if u.contains("LIMINE") {
                name = Some("limine");
            } else if u.ends_with("\\EFI\\BOOT\\BOOTX64.EFI") {
                why.push("that is the removable-media fallback path".into());
            }
        }
        if let Some(info) = &e.loader_info {
            why.push(format!("LoaderInfo says {info:?}"));
            if info.starts_with("GRUB") {
                name = name.or(Some("grub"));
            } else if info.starts_with("systemd-boot") {
                name = Some("systemd-boot");
            }
        }
        if e.shim_variables {
            why.push("shim's MOK variables are set".into());
            shim = true;
        }
        if name.is_none() {
            let lc: Vec<String> = e.esp_files.iter().map(|f| f.to_ascii_lowercase()).collect();
            if lc.iter().any(|f| f.ends_with("/grubx64.efi")) {
                name = Some("grub");
                why.push("an ESP holds grubx64.efi".into());
            } else if lc.iter().any(|f| f.ends_with("/systemd-bootx64.efi")) {
                name = Some("systemd-boot");
                why.push("an ESP holds systemd-bootx64.efi".into());
            } else if lc.iter().any(|f| f.ends_with("/limine.conf") || f.contains("/efi/limine/")) {
                name = Some("limine");
                why.push("an ESP holds Limine's limine.conf".into());
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
                _ => {}
            }
        }
    }
    let package = match (name, e.uefi) {
        (Some("grub"), true) => ["grub-efi-amd64-signed", "grub-efi-amd64", "grub-efi-ia32", "grub-efi-arm64"].into_iter().find(|p| has(p)),
        (Some("grub"), false) => ["grub-pc"].into_iter().find(|p| has(p)),
        (Some("systemd-boot"), _) => ["systemd-boot"].into_iter().find(|p| has(p)),
        (Some("refind"), _) => ["refind"].into_iter().find(|p| has(p)),
        (Some("limine"), _) => ["limine"].into_iter().find(|p| has(p)),
        (Some("syslinux"), _) => ["extlinux", "syslinux"].into_iter().find(|p| has(p)),
        _ => None,
    };
    if let Some(p) = package {
        why.push(format!("package {p} is installed"));
    } else if name.is_none() {
        // Last resort: what's installed.
        for (p, n) in [("grub-efi-amd64", "grub"), ("grub-pc", "grub"), ("systemd-boot", "systemd-boot"), ("extlinux", "syslinux")] {
            if has(p) {
                name = Some(n);
                why.push(format!("only evidence: package {p} is installed"));
                break;
            }
        }
    }
    Obj::new()
        .set("name", name.unwrap_or("unknown"))
        .set("firmware", if e.uefi { "uefi" } else { "bios" })
        .set("via_shim", e.uefi && shim)
        .set("package", package)
        .set("evidence", why)
        .build()
}

/// Everything in this module, for the report's "boot" section.  `esps` are
/// the mounted ESPs (device, mount point).
pub fn boot_json(sys: &Sys, packages: &[(String, String, String)], esps: &[(String, String)], efi_boot: Value, mbr_codes: Vec<(String, String)>) -> Value {
    let running = sys.read_trim("/proc/sys/kernel/osrelease");
    let mut esp_list = Vec::new();
    let mut esp_files = Vec::new();
    let mut sd_boot = None;
    for (dev, mnt) in esps {
        let inv = esp_inventory(sys, mnt);
        if let Some(files) = inv.get("files").and_then(Value::as_array) {
            esp_files.extend(files.iter().filter_map(|f| f.get("path").and_then(Value::as_str)).map(str::to_string));
        }
        if sd_boot.is_none() {
            sd_boot = systemd_boot_json(sys, mnt);
        }
        esp_list.push(Obj::new().set("device", dev.as_str()).set("mounted_at", mnt.as_str()).set("contents", inv).build());
    }
    if sd_boot.is_none() {
        // systemd-boot can also keep its entries on an XBOOTLDR at /boot.
        sd_boot = systemd_boot_json(sys, "/boot");
    }
    let current_file = efi_boot.get("boot_current").and_then(Value::as_str).and_then(|cur| {
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
        loader_info: efi_boot.at(&["loader_interface", "loader_info"]).and_then(Value::as_str).map(str::to_string),
        shim_variables: efi_boot.get("shim_variables").and_then(Value::as_array).is_some_and(|a| a.iter().any(|v| v.as_str().is_some_and(|s| s.starts_with("Mok")))),
        mbr_codes,
        packages,
        esp_files,
    };
    let mut pk = Obj::new();
    for p in PACKAGES {
        if let Some((_, v, _)) = packages.iter().find(|(n, _, _)| n == p) {
            pk.put(p, v.as_str());
        }
    }
    Obj::new()
        .set("loader", detect_loader(&ev))
        .set("efi", efi_boot)
        .set("esps", esp_list)
        .set("grub", grub_json(sys))
        .set("systemd_boot", sd_boot)
        .set("kernels", kernels_json(sys, running.as_deref()))
        .set("cmdline", sys.read("/proc/cmdline").map(|c| cmdline_json(&c)))
        .set("kernel_cmdline_file", sys.read_trim("/etc/kernel/cmdline"))
        .set("initramfs", initramfs_json(sys, packages))
        .set("packages", pk)
        .build()
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::sys::FakeRunner;
    use crate::testutil::Tree;

    const DPKG: &str = "Package: grub-efi-amd64-signed
Status: install ok installed
Priority: optional
Architecture: amd64
Version: 1+2.12+9+deb13u2
Description: GRand Unified Bootloader, version 2 (EFI-AMD64 version)
 multi-line
 description

Package: grub-pc
Status: deinstall ok config-files
Architecture: amd64
Version: 2.12-9

Package: lvm2
Status: install ok installed
Architecture: amd64
Version: 2.03.31-2

Package: half
Status: install ok half-installed
Version: 1
";

    #[test]
    fn dpkg_status() {
        let p = parse_dpkg_status(DPKG);
        assert_eq!(
            p,
            vec![
                ("grub-efi-amd64-signed".to_string(), "1+2.12+9+deb13u2".to_string(), "amd64".to_string()),
                ("lvm2".to_string(), "2.03.31-2".to_string(), "amd64".to_string())
            ]
        );
    }

    #[test]
    fn debconf() {
        let t = "Name: grub-pc/install_devices\nTemplate: grub-pc/install_devices\nValue: /dev/disk/by-id/ata-QEMU_HARDDISK_QM00001\nOwners: grub-pc\nFlags: seen\n\nName: grub2/update_nvram\nTemplate: grub2/update_nvram\nValue: true\nOwners: grub-efi-amd64\n\nName: grub2/kfreebsd_cmdline\nTemplate: grub2/kfreebsd_cmdline\nOwners: grub-efi-amd64\n\nName: tzdata/Areas\nValue: Europe\n";
        let d = parse_debconf(t, &["grub-pc/", "grub2/"]);
        assert_eq!(
            d,
            vec![
                ("grub-pc/install_devices".into(), "/dev/disk/by-id/ata-QEMU_HARDDISK_QM00001".into()),
                ("grub2/update_nvram".into(), "true".into())
            ]
        );
    }

    #[test]
    fn shell_word_splitting() {
        assert_eq!(shell_words("a 'b c' \"d \\\" e\" f\\ g"), vec!["a", "b c", "d \" e", "f g"]);
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
        assert_eq!(get("GRUB_DISTRIBUTOR"), Some("`lsb_release -i -s 2> /dev/null || echo Debian`"));
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
        assert_eq!(g.search_uuids, vec!["f2438bf3-6107-49a3-a57c-ba1bee4aebcb", "1111-2222"]);
        // Only commands at the start of a line count: "if ...; then insmod xzio" is skipped.
        assert_eq!(g.modules, vec!["all_video", "efi_gop", "part_gpt", "ext2", "gzio"]);
        assert_eq!(
            g.default_linux.as_deref(),
            Some("/boot/vmlinuz-6.12.115+deb13.1-amd64 root=UUID=f2438bf3-6107-49a3-a57c-ba1bee4aebcb ro quiet")
        );
        assert_eq!(g.default_initrd.as_deref(), Some("/boot/initrd.img-6.12.115+deb13.1-amd64"));
        assert_eq!(g.lang, None);
        assert_eq!(parse_grub_cfg("set locale_dir=$prefix/locale\nset lang=en_US\ninsmod gettext\n").lang.as_deref(), Some("en_US"));
    }

    #[test]
    fn grub_stub_and_env() {
        let g = parse_grub_cfg("search.fs_uuid f2438bf3-6107-49a3-a57c-ba1bee4aebcb root \nset prefix=($root)'/boot/grub'\nconfigfile $prefix/grub.cfg\n");
        assert_eq!(g.search_uuids, vec!["f2438bf3-6107-49a3-a57c-ba1bee4aebcb"]);
        assert_eq!(g.prefix.as_deref(), Some("($root)/boot/grub"));
        assert_eq!(g.configfile.as_deref(), Some("$prefix/grub.cfg"));
        let env = parse_grubenv("# GRUB Environment Block\n# WARNING: Do not edit\nsaved_entry=gnulinux-x\nrecordfail=1\n##########");
        assert_eq!(env, vec![("saved_entry".into(), "gnulinux-x".into()), ("recordfail".into(), "1".into())]);
    }

    #[test]
    fn cmdline() {
        let c = cmdline_json("BOOT_IMAGE=/boot/vmlinuz-6.12 root=UUID=abc ro quiet rootflags=subvol=@rootfs resume=UUID=def\n");
        assert_eq!(c.at(&["storage", "root"]).and_then(Value::as_str), Some("UUID=abc"));
        assert_eq!(c.at(&["storage", "rootflags"]).and_then(Value::as_str), Some("subvol=@rootfs"));
        assert_eq!(c.at(&["storage", "ro"]).and_then(Value::as_str), Some(""));
        assert!(c.at(&["storage", "quiet"]).is_none());
        assert_eq!(c.get("words").and_then(Value::as_array).map(Vec::len), Some(6));
        let z = cmdline_json("root=ZFS=rpool/ROOT/debian boot=zfs");
        assert_eq!(z.at(&["storage", "root"]).and_then(Value::as_str), Some("ZFS=rpool/ROOT/debian"));
    }

    fn pk(names: &[&str]) -> Vec<(String, String, String)> {
        names.iter().map(|n| (n.to_string(), "1".to_string(), "amd64".to_string())).collect()
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
        });
        assert_eq!(v.get("name").and_then(Value::as_str), Some("grub"));
        assert_eq!(v.get("via_shim"), Some(&Value::Bool(true)));
        assert_eq!(v.get("package").and_then(Value::as_str), Some("grub-efi-amd64-signed"));
        assert_eq!(v.get("evidence").and_then(Value::as_array).map(Vec::len), Some(4));
    }

    #[test]
    fn loader_other_cases() {
        let none: Vec<(String, String, String)> = vec![];
        let base = |uefi: bool| Evidence { uefi, current_file: None, loader_info: None, shim_variables: false, mbr_codes: vec![], packages: &none, esp_files: vec![] };
        // systemd-boot by its path, then by LoaderInfo.
        let mut e = base(true);
        e.current_file = Some("\\EFI\\systemd\\systemd-bootx64.efi".into());
        assert_eq!(detect_loader(&e).get("name").and_then(Value::as_str), Some("systemd-boot"));
        let mut e = base(true);
        e.loader_info = Some("systemd-boot 257.8-1~deb13u2".into());
        assert_eq!(detect_loader(&e).get("name").and_then(Value::as_str), Some("systemd-boot"));
        // The removable path alone says nothing; the ESP's files decide.
        let mut e = base(true);
        e.current_file = Some("\\EFI\\BOOT\\BOOTX64.EFI".into());
        e.esp_files = vec!["EFI/debian/grubx64.efi".into()];
        let v = detect_loader(&e);
        assert_eq!(v.get("name").and_then(Value::as_str), Some("grub"));
        assert!(v.get("evidence").unwrap().compact().contains("fallback"));
        assert_eq!(v.get("via_shim"), Some(&Value::Bool(false)));
        let mut e = base(true);
        e.current_file = Some("\\EFI\\refind\\refind_x64.efi".into());
        assert_eq!(detect_loader(&e).get("name").and_then(Value::as_str), Some("refind"));
        // BIOS: the MBR decides.
        let mut e = base(false);
        e.mbr_codes = vec![("vda".into(), "grub2".into()), ("vdb".into(), "none".into())];
        let p = pk(&["grub-pc"]);
        e.packages = &p;
        let v = detect_loader(&e);
        assert_eq!(v.get("name").and_then(Value::as_str), Some("grub"));
        assert_eq!(v.get("firmware").and_then(Value::as_str), Some("bios"));
        assert_eq!(v.get("package").and_then(Value::as_str), Some("grub-pc"));
        let mut e = base(false);
        e.mbr_codes = vec![("sda".into(), "syslinux".into())];
        assert_eq!(detect_loader(&e).get("name").and_then(Value::as_str), Some("syslinux"));
        let mut e = base(false);
        e.mbr_codes = vec![("sda".into(), "grub-legacy".into())];
        assert_eq!(detect_loader(&e).get("name").and_then(Value::as_str), Some("grub-legacy"));
        // Nothing at all but a package.
        let mut e = base(false);
        let p = pk(&["grub-pc"]);
        e.packages = &p;
        let v = detect_loader(&e);
        assert_eq!(v.get("name").and_then(Value::as_str), Some("grub"));
        assert!(v.get("evidence").unwrap().compact().contains("only evidence"));
        assert_eq!(detect_loader(&base(false)).get("name").and_then(Value::as_str), Some("unknown"));
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
        let paths: Vec<&str> = files.iter().filter_map(|f| f.get("path").and_then(Value::as_str)).collect();
        assert_eq!(
            paths,
            vec![
                "EFI/BOOT/BOOTX64.EFI", "EFI/debian/grub.cfg", "EFI/debian/grubx64.efi", "EFI/debian/shimx64.efi",
                "loader/entries/debian-6.12.conf", "loader/entries/readme.txt", "loader/loader.conf"
            ]
        );
        assert_eq!(files[0].get("same_as").and_then(Value::as_str), Some("EFI/debian/shimx64.efi"));
        assert!(files[2].get("same_as").is_none());
        assert_eq!(inv.at(&["debian_grub_stub", "search_uuids", "0"]).and_then(Value::as_str), Some("f2438bf3"));
        let sd = systemd_boot_json(&s, "/boot/efi").unwrap();
        assert_eq!(sd.at(&["loader_conf", "timeout"]).and_then(Value::as_str), Some("5"));
        assert_eq!(sd.at(&["entries", "0", "file"]).and_then(Value::as_str), Some("debian-6.12.conf"));
        assert_eq!(sd.at(&["entries", "0", "options"]).and_then(Value::as_str), Some("root=UUID=x ro"));
        assert_eq!(sd.get("entries").and_then(Value::as_array).map(Vec::len), Some(1));
        assert!(systemd_boot_json(&s, "/nowhere").is_none());
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
            .file("/etc/initramfs-tools/initramfs.conf", "MODULES=most\nBUSYBOX=auto\nCOMPRESS=zstd\n")
            .file("/etc/initramfs-tools/conf.d/resume", "RESUME=UUID=abc\n");
        let s = Sys::new(t.root(), Box::new(FakeRunner::default()), false);
        let k = kernels_json(&s, Some("6.12.115+deb13.1-amd64"));
        let list = k.get("installed").unwrap().as_array().unwrap();
        let vers: Vec<&str> = list.iter().filter_map(|x| x.get("version").and_then(Value::as_str)).collect();
        assert_eq!(vers, vec!["6.1.0-44-amd64", "6.12.74+deb13+1-amd64", "6.12.115+deb13.1-amd64"]);
        assert_eq!(list[2].get("running"), Some(&Value::Bool(true)));
        assert_eq!(list[2].get("initrd_bytes").and_then(Value::as_u64), Some(30));
        assert_eq!(list[2].get("modules_installed"), Some(&Value::Bool(true)));
        assert!(list[1].get("initrd_bytes").unwrap().is_null());
        assert!(list[0].get("vmlinuz_bytes").unwrap().is_null());
        assert_eq!(k.at(&["symlinks", "/vmlinuz"]).and_then(Value::as_str), Some("boot/vmlinuz-6.12.115+deb13.1-amd64"));
        let i = initramfs_json(&s, &pk(&["initramfs-tools"]));
        assert_eq!(i.get("generator").and_then(Value::as_str), Some("initramfs-tools"));
        assert_eq!(i.get("compress").and_then(Value::as_str), Some("zstd"));
        assert_eq!(i.get("resume").and_then(Value::as_str), Some("UUID=abc"));
        assert_eq!(initramfs_json(&s, &pk(&["dracut", "initramfs-tools"])).get("generator").and_then(Value::as_str), Some("dracut"));
        assert_eq!(initramfs_json(&s, &pk(&["mkinitfs"])).get("generator").and_then(Value::as_str), Some("mkinitfs"));
    }

    #[test]
    fn xbps() {
        let db = "<?xml version=\"1.0\"?>\n<plist version=\"1.0\">\n<dict>\n\t<key>dracut</key>\n\t<dict>\n\t\t<key>architecture</key>\n\t\t<string>x86_64-musl</string>\n\t\t<key>pkgver</key>\n\t\t<string>dracut-105_1</string>\n\t\t<key>run_depends</key>\n\t\t<array>\n\t\t\t<string>bash&gt;=0</string>\n\t\t</array>\n\t\t<key>alternatives</key>\n\t\t<dict>\n\t\t\t<key>x</key>\n\t\t\t<array><string>a:b</string></array>\n\t\t</dict>\n\t</dict>\n\t<key>grub</key>\n\t<dict>\n\t\t<key>pkgver</key>\n\t\t<string>grub-2.12_3</string>\n\t\t<key>architecture</key>\n\t\t<string>x86_64-musl</string>\n\t</dict>\n\t<key>_XBPS_ALTERNATIVES_</key>\n\t<dict>\n\t\t<key>x</key>\n\t\t<array><string>dracut</string></array>\n\t</dict>\n</dict>\n</plist>\n";
        assert_eq!(
            parse_xbps_pkgdb(db),
            vec![("dracut".into(), "105_1".into(), "x86_64-musl".into()), ("grub".into(), "2.12_3".into(), "x86_64-musl".into())]
        );
    }

    #[test]
    fn alpine_and_aerynos() {
        let apk = "C:Q1abc=\nP:mkinitfs\nV:3.12.0-r0\nA:x86_64\nS:1\n\nC:Q2\nP:grub-bios\nV:2.12-r9\nA:x86_64\n\n";
        assert_eq!(
            parse_apk_installed(apk),
            vec![("grub-bios".into(), "2.12-r9".into(), "x86_64".into()), ("mkinitfs".into(), "3.12.0-r0".into(), "x86_64".into())]
        );
        let t = Tree::new("alpine-kernels");
        t.file("/boot/vmlinuz-lts", vec![0u8; 10]).file("/boot/initramfs-lts", vec![0u8; 7]).file("/lib/apk/db/installed", apk);
        let s = Sys::new(t.root(), Box::new(FakeRunner::default()), false);
        assert_eq!(installed_packages(&s).len(), 2);
        let k = kernels_json(&s, None);
        let list = k.get("installed").unwrap().as_array().unwrap();
        assert_eq!(list.len(), 1);
        assert_eq!(list[0].get("version").and_then(Value::as_str), Some("lts"));
        assert_eq!(list[0].get("initrd_bytes").and_then(Value::as_u64), Some(7));
        let a = Tree::new("aerynos-kernels");
        a.file("/usr/lib/kernel/current.stable.initrd", "").file("/usr/lib/kernel/current.stable.kernel", "");
        let s = Sys::new(a.root(), Box::new(FakeRunner::default()), false);
        assert_eq!(initramfs_json(&s, &pk(&["dracut"])).get("generator").and_then(Value::as_str), Some("prebuilt"));
    }
}
