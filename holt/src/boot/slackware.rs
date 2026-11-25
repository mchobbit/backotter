//! Slackware's boot setup: LILO, ELILO, mkinitrd and geninitrd, rc.d;
//! part of crate::boot, which re-exports it.

use super::{byte_copy_of, has_package, is_slackware, parse_shell_assignments, Package};
use crate::json::{Obj, Value};
use crate::sys::Sys;

/// LILO's configuration, as liloconfig writes it.
pub const LILO_CONF: &str = "/etc/lilo.conf";
/// mkinitrd's configuration, when the owner wrote one (`mkinitrd -F`).
pub const MKINITRD_CONF: &str = "/etc/mkinitrd.conf";
/// mkinitrd's source tree (its -s default), which `mkinitrd -c` builds.
pub const INITRD_TREE: &str = "/boot/initrd-tree";
/// The command mkinitrd last ran as (its `$0 $*`), kept in its tree.
pub const MKINITRD_COMMAND_LINE: &str = "/boot/initrd-tree/command_line";
/// mkinitrd's image when nothing names another.
pub const MKINITRD_OUTPUT: &str = "/boot/initrd.gz";
/// -current's initrd maker (mkinitrd's package since 1.4.11-58 or so):
/// runs setup.01.mkinitrd, which makes /boot/initrd-VERSION.img.
pub const GENINITRD: &str = "/usr/sbin/geninitrd";
/// geninitrd's settings (GENERATOR, KERNEL, the symlinks, ...).
pub const GENINITRD_DEFAULT: &str = "/etc/default/geninitrd";
/// The script geninitrd runs unless GENINITRD_OVERRIDE_SCRIPT names another.
pub const GENINITRD_SETUP: &str = "/var/lib/pkgtools/setup/setup.01.mkinitrd";
/// mkinitrd's command generator, which prints a mkinitrd command for the
/// running system's root and storage drivers.
pub const MKINITRD_GENERATOR: &str = "/usr/share/mkinitrd/mkinitrd_command_generator.sh";
/// ELILO's EFI binary as the elilo package installs it, which eliloconfig
/// copies to the ESP's EFI/Slackware/elilo.efi.
pub const ELILO_EFI: &str = "/boot/elilo-x86_64.efi";

/// A lilo.conf item: its key, and its value if it has one.
pub type LiloItem = (String, Option<String>);

/// lilo.conf(5)'s items, which ELILO's elilo.conf shares: `key`, or
/// `key=value` with or without spaces around the `=`, a value in double
/// quotes when it has spaces, several on a line (`disk = /dev/vda
/// bios=0x80`), and `#` to the end of the line a comment.
pub fn lilo_items(text: &str) -> Vec<LiloItem> {
    let mut out = Vec::new();
    for line in text.lines() {
        // (word, whether it's the `=` between a key and its value)
        let mut toks: Vec<(String, bool)> = Vec::new();
        let mut cur = String::new();
        let mut in_word = false;
        let mut it = line.chars();
        while let Some(c) = it.next() {
            match c {
                '#' => break,
                '"' => {
                    in_word = true;
                    for d in it.by_ref() {
                        if d == '"' {
                            break;
                        }
                        cur.push(d);
                    }
                }
                '=' => {
                    if in_word {
                        toks.push((std::mem::take(&mut cur), false));
                        in_word = false;
                    }
                    toks.push(("=".into(), true));
                }
                c if c.is_whitespace() => {
                    if in_word {
                        toks.push((std::mem::take(&mut cur), false));
                        in_word = false;
                    }
                }
                c => {
                    in_word = true;
                    cur.push(c);
                }
            }
        }
        if in_word {
            toks.push((cur, false));
        }
        let mut i = 0;
        while i < toks.len() {
            if toks[i].1 {
                i += 1;
                continue;
            }
            let key = toks[i].0.clone();
            if toks.get(i + 1).is_some_and(|t| t.1) {
                match toks.get(i + 2).filter(|t| !t.1) {
                    Some(v) => {
                        out.push((key, Some(v.0.clone())));
                        i += 3;
                    }
                    None => {
                        out.push((key, Some(String::new())));
                        i += 2;
                    }
                }
            } else {
                out.push((key, None));
                i += 1;
            }
        }
    }
    out
}

/// A lilo.conf (or elilo.conf) in its sections: the global items, then
/// each `image=` (or LILO's `other=`) with the items after it.
#[derive(Debug, Default, Clone, PartialEq)]
pub struct LiloConf {
    pub global: Vec<LiloItem>,
    /// (image or other, its path, its items)
    pub sections: Vec<(String, String, Vec<LiloItem>)>,
}

impl LiloConf {
    pub fn parse(text: &str) -> LiloConf {
        let mut c = LiloConf::default();
        for (k, v) in lilo_items(text) {
            match (k.as_str(), v) {
                ("image" | "other", Some(p)) => c.sections.push((k, p, Vec::new())),
                (_, v) => match c.sections.last_mut() {
                    Some(s) => s.2.push((k, v)),
                    None => c.global.push((k, v)),
                },
            }
        }
        c
    }
}

/// The last value `key` has among `items`.
pub fn lilo_get<'a>(items: &'a [LiloItem], key: &str) -> Option<&'a str> {
    items.iter().rev().find(|(k, _)| k == key).and_then(|(_, v)| v.as_deref())
}

/// The items as written, `key` or `key=value`.
fn lilo_list(items: &[LiloItem]) -> Vec<String> {
    items.iter().map(|(k, v)| v.as_ref().map_or_else(|| k.clone(), |v| format!("{k}={v}"))).collect()
}

/// A parsed lilo.conf or elilo.conf as the dump keeps it: the global
/// boot=, map=, root=, append=, initrd= and default=, every global item as
/// written (options), and each section (images) with its label, root=,
/// initrd=, append=, addappend=, read-only (true, read-write false, else
/// null) and items, and what `extra` adds for it (its path, its items).
fn lilo_conf_json(c: &LiloConf, extra: &dyn Fn(&str, &[LiloItem]) -> Vec<(&'static str, Value)>) -> Obj {
    let g = |k: &str| lilo_get(&c.global, k);
    let images: Vec<Value> = c
        .sections
        .iter()
        .map(|(kind, path, items)| {
            let i = |k: &str| lilo_get(items, k);
            let has = |k: &str| items.iter().any(|(key, _)| key == k);
            let read_only = if has("read-only") {
                Some(true)
            } else if has("read-write") {
                Some(false)
            } else {
                None
            };
            let mut o = Obj::new()
                .set("kind", kind.as_str())
                .set("path", path.as_str())
                .set("label", i("label"))
                .set("root", i("root"))
                .set("initrd", i("initrd"))
                .set("append", i("append"))
                .set("addappend", i("addappend"))
                .set("read_only", read_only)
                .set("options", lilo_list(items));
            for (k, v) in extra(path, items) {
                o.put(k, v);
            }
            o.build()
        })
        .collect();
    Obj::new()
        .set("boot", g("boot"))
        .set("map", g("map"))
        .set("root", g("root"))
        .set("append", g("append"))
        .set("initrd", g("initrd"))
        .set("default", g("default"))
        .set("options", lilo_list(&c.global))
        .set("images", images)
}

/// boot.lilo: /etc/lilo.conf parsed (lilo_conf_json), each image with
/// whether its file exists, and whether the map file lilo writes
/// (/boot/map unless map= says) is there.  None without a lilo.conf.
pub fn lilo_json(sys: &Sys) -> Option<Value> {
    let c = LiloConf::parse(&sys.read(LILO_CONF)?);
    let map = lilo_get(&c.global, "map").unwrap_or("/boot/map").to_string();
    let mut o = Obj::new().set("conf", LILO_CONF);
    for (k, v) in lilo_conf_json(&c, &|path, _| vec![("exists", Value::from(sys.exists(path)))]).0 {
        o.put(&k, v);
    }
    Some(o.set("map_exists", sys.exists(&map)).build())
}

/// /boot's regular files (not links) whose names `pick` takes, with sizes.
fn boot_files(sys: &Sys, pick: &dyn Fn(&str) -> bool) -> Vec<(String, u64)> {
    let mut out: Vec<(String, u64)> = sys
        .list("/boot")
        .into_iter()
        .filter(|f| pick(f))
        .map(|f| format!("/boot/{f}"))
        .filter(|p| sys.readlink(p).is_none())
        .filter_map(|p| sys.file_size(&p).map(|n| (p, n)))
        .collect();
    out.sort();
    out
}

/// The first of `candidates` that `path` is a byte copy of.
fn same_file(sys: &Sys, path: &str, candidates: &[(String, u64)]) -> Option<String> {
    let size = sys.file_size(path)?;
    byte_copy_of(sys, path, size, candidates.iter().map(|(p, n)| (p.as_str(), *n)), "").map(str::to_string)
}

/// An elilo.conf on a mounted ESP.
pub struct EliloConf {
    pub device: String,
    pub mount: String,
    /// Its directory on the ESP (EFI/Slackware, EFI/BOOT).
    pub dir: String,
    /// Its file name: elilo-x86_64.conf when there is one (ELILO reads that
    /// first), else elilo.conf.
    pub name: String,
    pub conf: LiloConf,
}

impl EliloConf {
    /// A path elilo.conf names, as a path on the ESP: relative to its own
    /// directory, or from the ESP's root when it starts with / or \.
    pub fn on_esp(&self, p: &str) -> String {
        if p.starts_with(['/', '\\']) {
            p.trim_start_matches(['/', '\\']).replace('\\', "/")
        } else {
            format!("{}/{p}", self.dir)
        }
    }
}

/// Every EFI/*/elilo.conf (or elilo-x86_64.conf) on the mounted ESPs:
/// ELILO reads the one in the directory it was loaded from, whatever its
/// own file's name (so EFI/BOOT/BOOTX64.EFI reads EFI/BOOT/elilo.conf).
pub fn elilo_confs(sys: &Sys, esps: &[(String, String)]) -> Vec<EliloConf> {
    let mut out = Vec::new();
    for (dev, mnt) in esps {
        let Some(efi) = sys.list(mnt).into_iter().find(|e| e.eq_ignore_ascii_case("EFI")) else { continue };
        let mut dirs = sys.list(&format!("{mnt}/{efi}"));
        dirs.sort();
        for d in dirs {
            let dir = format!("{efi}/{d}");
            let files = sys.list(&format!("{mnt}/{dir}"));
            let Some(name) = ["elilo-x86_64.conf", "elilo.conf"].iter().find_map(|n| files.iter().find(|f| f.eq_ignore_ascii_case(n))).cloned() else { continue };
            let Some(text) = sys.read(&format!("{mnt}/{dir}/{name}")) else { continue };
            out.push(EliloConf { device: dev.clone(), mount: mnt.clone(), dir, name, conf: LiloConf::parse(&text) });
        }
    }
    out
}

/// boot.elilo: each elilo.conf on the ESPs (elilo_confs), parsed as
/// lilo.conf is (lilo_conf_json), with its ESP (device, mounted_at), its
/// directory and file, its loader (the .efi beside it that is a byte copy
/// of /boot/elilo-x86_64.efi, else its elilo.efi) and whether it is that
/// copy; for each image, its file on the ESP and the /boot kernel it is a
/// byte copy of (same_as: what the machine really boots), and the same for
/// its initrd.  None when there is none.
pub fn elilo_json(sys: &Sys, esps: &[(String, String)]) -> Option<Value> {
    let confs = elilo_confs(sys, esps);
    if confs.is_empty() {
        return None;
    }
    let kernels = boot_files(sys, &|f| f.starts_with("vmlinuz"));
    // mkinitrd's initrd.gz, and geninitrd's initrd-VERSION.img (-current).
    let initrds = boot_files(sys, &|f| f.starts_with("initrd") && (f.ends_with(".gz") || f.ends_with(".img")));
    let elilo = sys.file_size(ELILO_EFI);
    let mut list = Vec::new();
    for e in &confs {
        let full = format!("{}/{}", e.mount, e.dir);
        let efis: Vec<String> = sys.list(&full).into_iter().filter(|f| f.to_ascii_lowercase().ends_with(".efi")).collect();
        // The first .efi here (in name order) that is a byte copy of /boot's
        // ELILO_EFI: only one of its size is read, and a piece at a time, so
        // a file over READ_MAX is compared too, with no warning in the dump
        // (RustAudit1 H4-06).
        let sized = efis.iter().filter_map(|f| sys.file_size(&format!("{full}/{f}")).map(|n| (f.as_str(), n)));
        let copy = elilo.and_then(|n| byte_copy_of(sys, ELILO_EFI, n, sized, &full));
        let loader = copy.or_else(|| efis.iter().map(String::as_str).find(|f| f.eq_ignore_ascii_case("elilo.efi"))).map(|f| format!("{}/{f}", e.dir));
        let extra = |path: &str, items: &[LiloItem]| {
            let file = e.on_esp(path);
            let same = same_file(sys, &format!("{}/{file}", e.mount), &kernels);
            let mut x = vec![("file", Value::from(file)), ("same_as", Value::from(same))];
            if let Some(i) = lilo_get(items, "initrd") {
                let f = e.on_esp(i);
                let same = same_file(sys, &format!("{}/{f}", e.mount), &initrds);
                x.extend([("initrd_file", Value::from(f)), ("initrd_same_as", Value::from(same))]);
            }
            x
        };
        let mut o = Obj::new()
            .set("device", e.device.as_str())
            .set("mounted_at", e.mount.as_str())
            .set("dir", e.dir.as_str())
            .set("conf", format!("{}/{}", e.dir, e.name))
            .set("loader", loader)
            .set("loader_is_boot_elilo", copy.is_some());
        for (k, v) in lilo_conf_json(&e.conf, &extra).0 {
            o.put(&k, v);
        }
        list.push(o.build());
    }
    Some(Obj::new().set("configs", list).build())
}

/// Whether `abs` is a file with an execute bit (an rc.d script that's on).
fn executable(sys: &Sys, abs: &str) -> bool {
    use std::os::unix::fs::PermissionsExt;
    std::fs::metadata(sys.p(abs)).is_ok_and(|m| m.is_file() && m.permissions().mode() & 0o111 != 0)
}

/// Slackware's kernels in /boot: vmlinuz-FLAVOR-VERSION files (15.0's huge,
/// generic), or vmlinuz-VERSION (-current's, its flavor the vmlinuz-FLAVOR
/// link to it: vmlinuz-generic, else ""), not the links to them (vmlinuz,
/// vmlinuz-huge).
pub fn slackware_kernels(sys: &Sys) -> Vec<(String, String, String)> {
    let digit = |s: &str| s.starts_with(|c: char| c.is_ascii_digit());
    let links: Vec<(String, String)> = sys
        .list("/boot")
        .into_iter()
        .filter_map(|f| {
            let flavor = f.strip_prefix("vmlinuz-").filter(|fl| !fl.is_empty() && !digit(fl))?.to_string();
            sys.readlink(&format!("/boot/{f}")).map(|t| (flavor, t.trim_start_matches("/boot/").to_string()))
        })
        .collect();
    let mut out: Vec<(String, String, String)> = boot_files(sys, &|f| f.starts_with("vmlinuz-"))
        .into_iter()
        .filter_map(|(p, _)| {
            let f = p.trim_start_matches("/boot/").to_string();
            let rest = f.strip_prefix("vmlinuz-")?;
            if digit(rest) {
                let flavor = links.iter().find(|(_, t)| *t == f).map(|(fl, _)| fl.clone()).unwrap_or_default();
                return Some((f.clone(), flavor, rest.to_string()));
            }
            let (flavor, ver) = rest.split_once('-')?;
            digit(ver).then(|| (f.clone(), flavor.to_string(), ver.to_string()))
        })
        .collect();
    out.sort();
    out
}

/// boot.slackware, on Slackware only: /etc/slackware-version; init,
/// "slackware-rc" when /etc/inittab and /etc/rc.d/rc.M are there and
/// /sbin/init is sysvinit's (its package, and a file, not a link); the rc.d
/// scripts that are on (rc_enabled: executable /etc/rc.d/rc.*); the
/// kernels (slackware_kernels), each with its flavor, version and whether
/// /lib/modules has it; and the /boot links that name the kernel.
pub fn slackware_json(sys: &Sys, packages: &[Package]) -> Option<Value> {
    if !is_slackware(sys) {
        return None;
    }
    let sysvinit = has_package(packages, "sysvinit") && sys.is_file("/sbin/init") && sys.readlink("/sbin/init").is_none();
    let init = (sys.is_file("/etc/rc.d/rc.M") && sys.is_file("/etc/inittab") && sysvinit).then_some("slackware-rc");
    let mut rc: Vec<String> = sys.list("/etc/rc.d").into_iter().filter(|f| f.starts_with("rc.") && executable(sys, &format!("/etc/rc.d/{f}"))).collect();
    rc.sort();
    let kernels: Vec<Value> = slackware_kernels(sys)
        .into_iter()
        .map(|(f, fl, v)| Obj::new().set("file", f).set("flavor", fl).set("modules_installed", sys.is_dir(&format!("/lib/modules/{v}"))).set("version", v).build())
        .collect();
    let mut links = Obj::new();
    for l in ["/boot/vmlinuz", "/boot/vmlinuz-huge", "/boot/vmlinuz-generic", "/boot/System.map", "/boot/config"] {
        if let Some(t) = sys.readlink(l) {
            links.put(l, t);
        }
    }
    Some(Obj::new().set("version", sys.read_trim("/etc/slackware-version")).set("init", init).set("rc_enabled", rc).set("kernels", kernels).set("links", links).build())
}

/// mkinitrd's word for an option in a command line: the word after the
/// last `flag`.
fn word_after<'a>(words: &[&'a str], flag: &str) -> Option<&'a str> {
    words.windows(2).rev().find(|p| p[0] == flag).map(|p| p[1])
}

/// Slackware's initramfs generator (docs/design/slackware.md): `none` when
/// no loader configuration names an initrd= and /boot/initrd.gz is
/// missing (the huge kernel, drivers built in), else `mkinitrd`.  The
/// generator, then the boot.initramfs fields only Slackware's dumps have:
/// evidence, and for mkinitrd, `mkinitrd`: conf (/etc/mkinitrd.conf or
/// null), command_line (what mkinitrd last ran as, or null), kernels (the
/// versions its image is for: command_line's -k, else mkinitrd.conf's
/// KERNEL_VERSION, else the running kernel, else every /lib/modules/K;
/// those /lib/modules has), output (-o, else mkinitrd.conf's
/// OUTPUT_IMAGE, else /boot/initrd-tree/initrd-name, else /boot/initrd.gz)
/// rootdev (-r, else mkinitrd.conf's ROOTDEV: the root the initrd
/// mounts, by the name given, or null), luksdev (-C, else LUKSDEV) and
/// resumedev (-h, else RESUMEDEV).  mkinitrd.conf counts only when the
/// command line has -F, or there is none.
pub(super) fn slackware_initramfs(sys: &Sys, esps: &[(String, String)]) -> (Option<String>, Vec<(&'static str, Value)>) {
    let mut why: Vec<String> = Vec::new();
    let mut named = 0;
    let mut note = |file: &str, items: &[LiloItem], why: &mut Vec<String>| {
        if let Some(i) = lilo_get(items, "initrd") {
            why.push(format!("{file} has initrd={i}"));
            named += 1;
        }
    };
    if let Some(t) = sys.read(LILO_CONF) {
        let c = LiloConf::parse(&t);
        note(LILO_CONF, &c.global, &mut why);
        for (_, _, items) in &c.sections {
            note(LILO_CONF, items, &mut why);
        }
    }
    for e in elilo_confs(sys, esps) {
        let f = format!("{}/{}", e.dir, e.name);
        note(&f, &e.conf.global, &mut why);
        for (_, _, items) in &e.conf.sections {
            note(&f, items, &mut why);
        }
    }
    // -current: geninitrd's images, before initrd.gz (which it makes a link
    // to them, or not, as GENINITRD_INITRD_GZ_SYMLINK says).
    if let Some(g) = geninitrd_json(sys, &mut why) {
        return (Some("geninitrd".into()), vec![("evidence", why.into()), ("geninitrd", g)]);
    }
    let gz = sys.is_file(MKINITRD_OUTPUT);
    why.push(if gz { format!("{MKINITRD_OUTPUT} exists") } else { format!("no {MKINITRD_OUTPUT}") });
    if named == 0 && !gz {
        why.push("no loader configuration names an initrd".into());
        return (Some("none".into()), vec![("evidence", why.into())]);
    }
    let conf = sys.read(MKINITRD_CONF).map(|t| parse_shell_assignments(&t));
    let cmd = sys.read(MKINITRD_COMMAND_LINE).map(|t| t.trim().to_string()).filter(|t| !t.is_empty());
    why.push(if conf.is_some() { format!("{MKINITRD_CONF} exists") } else { format!("no {MKINITRD_CONF}") });
    why.push(match &cmd {
        Some(c) => format!("{MKINITRD_COMMAND_LINE} says {c:?}"),
        None => format!("no {MKINITRD_COMMAND_LINE}"),
    });
    let words: Vec<&str> = cmd.as_deref().map(|c| c.split_whitespace().collect()).unwrap_or_default();
    // mkinitrd sources its configuration for -F only (sbin/mkinitrd's
    // first loop over "$@"), and load runs `mkinitrd -F` when there's no
    // command line: read it then, and not otherwise.
    let use_conf = cmd.is_none() || words.contains(&"-F");
    if conf.is_some() && !use_conf {
        why.push(format!("the command line has no -F, so mkinitrd didn't read {MKINITRD_CONF}"));
    }
    let key = |k: &str| conf.as_ref().filter(|_| use_conf).and_then(|c| c.iter().rev().find(|(key, _)| key == k).map(|(_, v)| v.clone())).filter(|v| !v.is_empty() && !v.contains(['$', '`']));
    let modules = |k: &str| sys.is_dir(&format!("/lib/modules/{k}"));
    let mut kernels: Vec<String> = match (word_after(&words, "-k"), key("KERNEL_VERSION")) {
        (Some(k), _) => k.split(':').filter(|k| !k.is_empty()).map(str::to_string).collect(),
        (None, Some(k)) => k.split(':').filter(|k| !k.is_empty()).map(str::to_string).collect(),
        (None, None) => match sys.read_trim("/proc/sys/kernel/osrelease").filter(|k| modules(k)) {
            Some(k) => vec![k],
            None => sys.list("/lib/modules").into_iter().filter(|k| sys.is_dir(&format!("/lib/modules/{k}/kernel"))).collect(),
        },
    };
    kernels.sort_by(|a, b| crate::sys::natural_cmp(a, b));
    kernels.dedup();
    for k in kernels.iter().filter(|k| !modules(k)) {
        why.push(format!("kernel {k} has no /lib/modules/{k}, so mkinitrd can't make its image"));
    }
    kernels.retain(|k| modules(k));
    // mkinitrd writes the image's path, canonical (-o through readlink -m,
    // against its cwd then), to initrd-name; a bare name there is in /boot.
    let in_boot = |n: String| if n.starts_with('/') { n } else { format!("/boot/{n}") };
    let tree_name = sys.read_trim("/boot/initrd-tree/initrd-name").filter(|n| !n.is_empty()).map(in_boot);
    let output = match word_after(&words, "-o") {
        Some(o) if o.starts_with('/') => o.to_string(),
        // Relative: where it went, as initrd-name recorded it, else /boot's.
        Some(o) => tree_name.clone().filter(|n| n.starts_with('/')).unwrap_or_else(|| in_boot(o.to_string())),
        None => key("OUTPUT_IMAGE").map(in_boot).or(tree_name).unwrap_or_else(|| MKINITRD_OUTPUT.into()),
    };
    let rootdev = word_after(&words, "-r").map(str::to_string).or_else(|| key("ROOTDEV"));
    let luksdev = word_after(&words, "-C").map(str::to_string).or_else(|| key("LUKSDEV"));
    let resumedev = word_after(&words, "-h").map(str::to_string).or_else(|| key("RESUMEDEV"));
    let m = Obj::new()
        .set("conf", conf.is_some().then_some(MKINITRD_CONF))
        .set("command_line", cmd)
        .set("kernels", kernels)
        .set("output", output)
        .set("rootdev", rootdev)
        .set("luksdev", luksdev)
        .set("resumedev", resumedev)
        .build();
    (Some("mkinitrd".into()), vec![("evidence", why.into()), ("mkinitrd", m)])
}

/// Whether a kernel version is one geninitrd would name an image after
/// and a restore can give a shell: digits first, then letters, digits and
/// `.`, `_`, `+`, `-`.
pub fn kernel_version_word(v: &str) -> bool {
    v.starts_with(|c: char| c.is_ascii_digit()) && v.chars().all(|c| c.is_ascii_alphanumeric() || matches!(c, '.' | '_' | '+' | '-'))
}

/// Slackware -current's geninitrd (docs/design/slackware.md, "Slackware
/// -current"): None unless /usr/sbin/geninitrd is there and /boot has an
/// image it made (a regular file initrd-VERSION.img).  Else boot.initramfs's
/// `geninitrd`: default (/etc/default/geninitrd or null), its settings as
/// setup.01.mkinitrd reads them (kernel, generator, dracut_opts,
/// override_script, post_install_script, named_symlink, initrd_gz_symlink,
/// each null when unset, or setup.01.mkinitrd's own default for the
/// generator, the two symlinks and auto_remove_initrd_tree), initrd_tree
/// (whether /boot/initrd-tree is there: setup.01.mkinitrd removes the one
/// mkinitrd leaves when AUTO_REMOVE_INITRD_TREE=true), `runs` (what setup.01.mkinitrd would
/// run: `override` for an executable GENINITRD_OVERRIDE_SCRIPT, `mkinitrd`
/// for GENERATOR=mkinitrd with /etc/mkinitrd.conf, `mkinitrd_command_generator.sh`
/// for it without one or by name, `dracut`, else the generator as given),
/// conf (/etc/mkinitrd.conf or null), luksdev, rootdev and resumedev from
/// it when it is read, images (each file, version, kernel: /boot/vmlinuz-VERSION or
/// null, modules_installed) and links (each /boot initrd*.img or
/// initrd.gz link, to its target).  Evidence lines go to `why`.
fn geninitrd_json(sys: &Sys, why: &mut Vec<String>) -> Option<Value> {
    if !sys.is_file(GENINITRD) {
        return None;
    }
    let images: Vec<(String, String)> = boot_files(sys, &|f| f.starts_with("initrd-") && f.ends_with(".img"))
        .into_iter()
        .filter_map(|(p, _)| {
            let v = p.strip_prefix("/boot/initrd-")?.strip_suffix(".img")?.to_string();
            kernel_version_word(&v).then_some((p, v))
        })
        .collect();
    if images.is_empty() {
        why.push(format!("{GENINITRD} exists, but /boot has no initrd-VERSION.img it made"));
        return None;
    }
    why.push(format!("{GENINITRD} exists"));
    let default = sys.read(GENINITRD_DEFAULT).map(|t| parse_shell_assignments(&t));
    let key = |k: &str| default.as_ref().and_then(|c| c.iter().rev().find(|(key, _)| key == k).map(|(_, v)| v.clone()));
    let flag = |k: &str| key(k).map(|v| Value::from(v == "true")).unwrap_or(Value::from(true));
    let generator = key("GENERATOR").filter(|g| !g.is_empty()).unwrap_or_else(|| "mkinitrd".into());
    let conf = sys.read(MKINITRD_CONF).map(|t| parse_shell_assignments(&t));
    let override_script = key("GENINITRD_OVERRIDE_SCRIPT").filter(|o| !o.is_empty());
    let override_runs = override_script.as_deref().is_some_and(|o| o.starts_with('/') && executable(sys, o));
    let runs = if override_runs {
        "override".to_string()
    } else {
        match generator.as_str() {
            "mkinitrd" if conf.is_some() => "mkinitrd".into(),
            "mkinitrd" | "mkinitrd_command_generator.sh" => "mkinitrd_command_generator.sh".into(),
            g => g.to_string(),
        }
    };
    why.push(match &default {
        Some(_) => format!("{GENINITRD_DEFAULT} says GENERATOR={}", key("GENERATOR").unwrap_or_default()),
        None => format!("no {GENINITRD_DEFAULT} (setup.01.mkinitrd's GENERATOR=mkinitrd)"),
    });
    why.push(match runs.as_str() {
        "override" => format!("GENINITRD_OVERRIDE_SCRIPT={} runs instead of {GENINITRD_SETUP}", override_script.as_deref().unwrap_or("")),
        "mkinitrd" => format!("{MKINITRD_CONF} exists, so {GENINITRD_SETUP} runs mkinitrd -F"),
        "mkinitrd_command_generator.sh" if conf.is_none() && generator == "mkinitrd" => format!("no {MKINITRD_CONF}, so {GENINITRD_SETUP} runs {MKINITRD_GENERATOR}'s command"),
        r => format!("{GENINITRD_SETUP} runs {r}"),
    });
    let ckey = |k: &str| conf.as_ref().filter(|_| runs == "mkinitrd").and_then(|c| c.iter().rev().find(|(key, _)| key == k).map(|(_, v)| v.clone())).filter(|v| !v.is_empty());
    let list: Vec<Value> = images
        .iter()
        .map(|(p, v)| {
            let kernel = format!("/boot/vmlinuz-{v}");
            let has_kernel = sys.is_file(&kernel);
            let modules = sys.is_dir(&format!("/lib/modules/{v}"));
            why.push(format!("{p} is geninitrd's image for kernel {v}{}", if has_kernel { "" } else { ", which has no /boot/vmlinuz-VERSION" }));
            Obj::new().set("file", p.as_str()).set("version", v.as_str()).set("kernel", has_kernel.then_some(kernel)).set("modules_installed", modules).build()
        })
        .collect();
    let mut links = Obj::new();
    for f in sys.list("/boot").into_iter().filter(|f| f == "initrd.gz" || (f.starts_with("initrd-") && f.ends_with(".img"))) {
        let l = format!("/boot/{f}");
        if let Some(t) = sys.readlink(&l) {
            why.push(format!("{l} -> {t}"));
            links.put(&l, t);
        }
    }
    Some(
        Obj::new()
            .set("default", default.is_some().then_some(GENINITRD_DEFAULT))
            .set("kernel", key("KERNEL"))
            .set("generator", generator.as_str())
            .set("dracut_opts", key("DRACUT_OPTS"))
            .set("override_script", override_script)
            .set("post_install_script", key("POST_INSTALL_SCRIPT").filter(|o| !o.is_empty()))
            .set("named_symlink", flag("GENINITRD_NAMED_SYMLINK"))
            .set("initrd_gz_symlink", flag("GENINITRD_INITRD_GZ_SYMLINK"))
            .set("auto_remove_initrd_tree", flag("AUTO_REMOVE_INITRD_TREE"))
            .set("initrd_tree", sys.is_dir(INITRD_TREE))
            .set("runs", runs.as_str())
            .set("conf", conf.is_some().then_some(MKINITRD_CONF))
            .set("luksdev", ckey("LUKSDEV"))
            .set("rootdev", ckey("ROOTDEV"))
            .set("resumedev", ckey("RESUMEDEV"))
            .set("images", list)
            .set("links", links.build())
            .build(),
    )
}

/// The source's mkinitrd command line (/boot/initrd-tree/command_line, its
/// `$0 $*`: the words as given, their quoting gone) made again for
/// `kernels`: `mkinitrd`, then its options in the source's order, its -k
/// given as `-k K1:K2` where it was (a later -k dropped), and at the end
/// when it had none (mkinitrd's own default, `uname -r`, would be the
/// rescue system's kernel); an -o is given as `output`, absolute (a
/// relative one would be against the chroot's cwd, /).  mkinitrd writes
/// the words it ran with to command_line again, so only these two change.
/// None when it isn't mkinitrd's or there's no kernel.
pub fn mkinitrd_replay(command_line: &str, kernels: &[String], output: &str) -> Option<Vec<String>> {
    let mut w = command_line.split_whitespace();
    let prog = w.next()?;
    if prog.rsplit('/').next() != Some("mkinitrd") || kernels.is_empty() {
        return None;
    }
    let rest: Vec<&str> = w.collect();
    let mut out = vec!["mkinitrd".to_string()];
    let mut k = false;
    let mut i = 0;
    while i < rest.len() {
        if rest[i] == "-k" {
            if !k {
                out.extend(["-k".to_string(), kernels.join(":")]);
                k = true;
            }
            i += 2;
            continue;
        }
        if rest[i] == "-o" {
            out.extend(["-o".to_string(), output.to_string()]);
            i += 2;
            continue;
        }
        out.push(rest[i].to_string());
        i += 1;
    }
    if !k {
        out.extend(["-k".to_string(), kernels.join(":")]);
    }
    Some(out)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::sys::FakeRunner;
    use crate::testutil::Tree;

    /// The first /boot file of the same size and bytes (byte_copy_of).
    #[test]
    fn same_file_first_copy() {
        let t = Tree::new("samefile");
        t.file("/esp/a.efi", "same").file("/esp/b.efi", "diff").file("/esp/d.efi", "same");
        let s = Sys::new(t.root(), Box::new(FakeRunner::default()), false);
        assert_eq!(same_file(&s, "/esp/a.efi", &[("/esp/b.efi".into(), 4), ("/esp/d.efi".into(), 4)]), Some("/esp/d.efi".to_string()));
        assert_eq!(same_file(&s, "/esp/gone.efi", &[("/esp/d.efi".into(), 4)]), None);
    }

    /// RustAudit1 H4-06: elilo_json's loader is the first .efi beside
    /// elilo.conf (in name order) that is a byte copy of /boot's
    /// ELILO_EFI, by byte_copy_of: a same-size different file isn't it, a
    /// copy larger than READ_MAX is found, and nothing is said in the
    /// dump's warnings; with no copy, elilo.efi by its name.
    #[test]
    fn elilo_loader_copy() {
        let t = Tree::new("elilo-loader");
        let conf = "image=vmlinuz\n  label=vmlinuz\n";
        t.file(ELILO_EFI, "elilo-binary")
            .file("/boot/efi/EFI/Slackware/elilo.conf", conf)
            .file("/boot/efi/EFI/Slackware/a.efi", "elilo-binarZ")
            .file("/boot/efi/EFI/Slackware/b.efi", "short")
            .file("/boot/efi/EFI/Slackware/c.efi", "elilo-binary")
            .file("/boot/efi/EFI/Slackware/elilo.efi", "elilo-binary")
            .file("/boot/efi/EFI/BOOT/elilo.conf", conf)
            .file("/boot/efi/EFI/BOOT/elilo.efi", "not-the-copy");
        let big = t.p("/boot/efi/EFI/BOOT/BOOTX64.EFI");
        std::fs::File::create(&big).unwrap().set_len(crate::sys::READ_MAX + 1).unwrap();
        let s = Sys::new(t.root(), Box::new(FakeRunner::default()), false);
        let esps = [("vda1".to_string(), "/boot/efi".to_string())];
        let j = elilo_json(&s, &esps).unwrap();
        let at = |i: &str, k: &str| j.at(&["configs", i, k]).cloned().unwrap();
        assert_eq!((at("0", "dir"), at("0", "loader"), at("0", "loader_is_boot_elilo")), (Value::from("EFI/BOOT"), Value::from("EFI/BOOT/elilo.efi"), Value::Bool(false)));
        assert_eq!((at("1", "dir"), at("1", "loader"), at("1", "loader_is_boot_elilo")), (Value::from("EFI/Slackware"), Value::from("EFI/Slackware/c.efi"), Value::Bool(true)));
        // /boot's ELILO_EFI as large as the fallback: compared, and found.
        std::fs::File::create(t.p(ELILO_EFI)).unwrap().set_len(crate::sys::READ_MAX + 1).unwrap();
        let j = elilo_json(&s, &esps).unwrap();
        assert_eq!(j.at(&["configs", "0", "loader"]).cloned(), Some(Value::from("EFI/BOOT/BOOTX64.EFI")));
        assert_eq!(j.at(&["configs", "0", "loader_is_boot_elilo"]).cloned(), Some(Value::Bool(true)));
        assert_eq!(j.at(&["configs", "1", "loader"]).cloned(), Some(Value::from("EFI/Slackware/elilo.efi")));
        assert!(s.warnings().is_empty(), "{:?}", s.warnings());
    }

    #[test]
    fn lilo_conf() {
        let text = "# LILO configuration file\nappend=\" vt.default_utf8=0\"\nboot = /dev/vda\ndisk = /dev/vda bios=0x80 max-partitions=7\ncompact\nprompt\ntimeout = 50\nchange-rules\n  reset\nvga = normal\n# Linux bootable partition config begins\nimage = /boot/vmlinuz\n  root = /dev/vda2\n  label = Linux\n  read-only  # a comment\nimage=/boot/vmlinuz-generic-5.15.161\n  initrd=/boot/initrd.gz\n  root=\"/dev/vda2\"\n  label=generic\n  append=\"\"\nother = /dev/vda3\n  label = Windows\n";
        let c = LiloConf::parse(text);
        assert_eq!(lilo_get(&c.global, "boot"), Some("/dev/vda"));
        assert_eq!(lilo_get(&c.global, "append"), Some(" vt.default_utf8=0"));
        assert_eq!(lilo_get(&c.global, "bios"), Some("0x80"));
        assert_eq!(lilo_get(&c.global, "max-partitions"), Some("7"));
        assert!(c.global.contains(&("compact".into(), None)) && c.global.contains(&("reset".into(), None)));
        assert_eq!(c.sections.len(), 3);
        assert_eq!((c.sections[0].0.as_str(), c.sections[0].1.as_str()), ("image", "/boot/vmlinuz"));
        assert_eq!(lilo_get(&c.sections[0].2, "root"), Some("/dev/vda2"));
        assert!(c.sections[0].2.contains(&("read-only".into(), None)));
        assert_eq!(lilo_get(&c.sections[1].2, "initrd"), Some("/boot/initrd.gz"));
        assert_eq!(lilo_get(&c.sections[1].2, "root"), Some("/dev/vda2"));
        assert_eq!(lilo_get(&c.sections[1].2, "append"), Some(""));
        assert_eq!(c.sections[2].0, "other");
        let j = lilo_conf_json(&c, &|_, _| vec![]).build();
        assert_eq!(j.get("boot").and_then(Value::as_str), Some("/dev/vda"));
        assert!(j.get("map").unwrap().is_null());
        assert_eq!(j.at(&["images", "0", "read_only"]), Some(&Value::Bool(true)));
        assert!(j.at(&["images", "1", "read_only"]).unwrap().is_null());
        assert_eq!(j.at(&["images", "1", "label"]).and_then(Value::as_str), Some("generic"));
        assert_eq!(j.at(&["options", "2"]).and_then(Value::as_str), Some("disk=/dev/vda"));
        // elilo.conf as eliloconfig writes it.
        let e = LiloConf::parse("chooser=simple\ndelay=1\ntimeout=1\n#\nimage=vmlinuz\n        label=vmlinuz\n        initrd=initrd.gz\n        read-only\n        append=\"root=/dev/vda3 vga=normal ro\"\n");
        assert_eq!(lilo_get(&e.global, "chooser"), Some("simple"));
        assert_eq!(lilo_get(&e.sections[0].2, "append"), Some("root=/dev/vda3 vga=normal ro"));
    }

    #[test]
    fn mkinitrd_replays() {
        let k = vec!["5.15.161".to_string()];
        assert_eq!(
            mkinitrd_replay("/sbin/mkinitrd -c -k 5.15.19 -f xfs -r /dev/vda3 -m xfs -u -o /boot/initrd.gz", &k, "/boot/initrd.gz").unwrap(),
            ["mkinitrd", "-c", "-k", "5.15.161", "-f", "xfs", "-r", "/dev/vda3", "-m", "xfs", "-u", "-o", "/boot/initrd.gz"]
        );
        // The source's order, word for word, when the kernel is the same
        // (slackware15-uefi's command_line); a second -k dropped.
        let line = "/sbin/mkinitrd -c -k 5.15.19 -f xfs -r /dev/vda3 -m virtio_blk:xfs -u -o /boot/initrd.gz";
        assert_eq!(mkinitrd_replay(line, &["5.15.19".into()], "/boot/initrd.gz").unwrap().join(" "), line.replacen("/sbin/", "", 1));
        assert_eq!(mkinitrd_replay("mkinitrd -k 1 -c -k 2", &k, "/boot/initrd.gz").unwrap(), ["mkinitrd", "-k", "5.15.161", "-c"]);
        assert_eq!(mkinitrd_replay("mkinitrd -F", &k, "/boot/initrd.gz").unwrap(), ["mkinitrd", "-F", "-k", "5.15.161"]);
        assert_eq!(mkinitrd_replay("mkinitrd -c", &["6.1".into(), "6.2".into()], "/boot/initrd.gz").unwrap(), ["mkinitrd", "-c", "-k", "6.1:6.2"]);
        assert!(mkinitrd_replay("dracut -f", &k, "/boot/initrd.gz").is_none());
        assert!(mkinitrd_replay("mkinitrd -c", &[], "/boot/initrd.gz").is_none());
        assert!(mkinitrd_replay("", &k, "/boot/initrd.gz").is_none());
        // A relative -o is given absolute: in the chroot it would be from /.
        assert_eq!(mkinitrd_replay("./mkinitrd -c -o initrd-custom.gz", &k, "/boot/initrd-custom.gz").unwrap(), ["mkinitrd", "-c", "-o", "/boot/initrd-custom.gz", "-k", "5.15.161"]);
    }

    /// -current's kernels are /boot/vmlinuz-VERSION, their flavor the
    /// vmlinuz-FLAVOR link to them (or none); 15.0's vmlinuz-FLAVOR-VERSION
    /// still read as before.  geninitrd's images by their names only.
    #[test]
    fn slackware_current_kernels() {
        let t = Tree::new("slk-current-kernels");
        t.file("/boot/vmlinuz-7.3.5", "k")
            .file("/boot/vmlinuz-7.2.9-rc1", "old")
            .file("/boot/vmlinuz-generic-5.15.161", "15")
            .symlink("/boot/vmlinuz-generic", "vmlinuz-7.3.5")
            .symlink("/boot/vmlinuz", "vmlinuz-7.3.5");
        let s = Sys::new(t.root(), Box::new(FakeRunner::default()), false);
        assert_eq!(
            slackware_kernels(&s),
            vec![
                ("vmlinuz-7.2.9-rc1".into(), String::new(), "7.2.9-rc1".into()),
                ("vmlinuz-7.3.5".into(), "generic".into(), "7.3.5".into()),
                ("vmlinuz-generic-5.15.161".into(), "generic".into(), "5.15.161".into()),
            ]
        );
        assert!(kernel_version_word("7.3.5") && kernel_version_word("6.12.1-rc2+") && kernel_version_word("5.15.161_x"));
        assert!(!kernel_version_word("generic") && !kernel_version_word("") && !kernel_version_word("7.3.5 x") && !kernel_version_word("7.3'5") && !kernel_version_word("7.3.5$x"));
        // No geninitrd, or no image it made: not geninitrd's.
        let mut why = Vec::new();
        assert!(geninitrd_json(&s, &mut why).is_none() && why.is_empty());
        t.file(GENINITRD, "#!/bin/bash\n").file("/boot/initrd.gz", "15.0's");
        assert!(geninitrd_json(&s, &mut why).is_none());
        assert_eq!(why, ["/usr/sbin/geninitrd exists, but /boot has no initrd-VERSION.img it made"]);
        // An image with no /etc/default/geninitrd: setup.01.mkinitrd's defaults.
        t.file("/boot/initrd-7.3.5.img", "i").file("/boot/initrd-x y.img", "not a version");
        let mut why = Vec::new();
        let g = geninitrd_json(&s, &mut why).unwrap();
        assert_eq!(g.get("generator").and_then(Value::as_str), Some("mkinitrd"));
        assert_eq!(g.get("runs").and_then(Value::as_str), Some("mkinitrd_command_generator.sh"));
        assert!(g.get("default").unwrap().is_null() && g.get("kernel").unwrap().is_null());
        assert_eq!(g.get("named_symlink"), Some(&Value::Bool(true)));
        assert_eq!((g.get("auto_remove_initrd_tree"), g.get("initrd_tree")), (Some(&Value::Bool(true)), Some(&Value::Bool(false))));
        assert_eq!(g.at(&["images"]).and_then(Value::as_array).map(Vec::len), Some(1));
        assert_eq!(g.at(&["images", "0", "kernel"]).and_then(Value::as_str), Some("/boot/vmlinuz-7.3.5"));
        assert_eq!(g.at(&["images", "0", "modules_installed"]), Some(&Value::Bool(false)));
        assert!(why.contains(&"no /etc/default/geninitrd (setup.01.mkinitrd's GENERATOR=mkinitrd)".to_string()), "{why:?}");
        // A set but not executable override script isn't run.
        t.file(GENINITRD_DEFAULT, "GENERATOR=mkinitrd_command_generator.sh\nGENINITRD_OVERRIDE_SCRIPT=/usr/local/sbin/x\nGENINITRD_INITRD_GZ_SYMLINK=false\nAUTO_REMOVE_INITRD_TREE=false\n").file("/usr/local/sbin/x", "").dir("/boot/initrd-tree");
        let g = geninitrd_json(&s, &mut Vec::new()).unwrap();
        assert_eq!(g.get("runs").and_then(Value::as_str), Some("mkinitrd_command_generator.sh"));
        assert_eq!(g.get("override_script").and_then(Value::as_str), Some("/usr/local/sbin/x"));
        assert_eq!(g.get("initrd_gz_symlink"), Some(&Value::Bool(false)));
        assert_eq!((g.get("auto_remove_initrd_tree"), g.get("initrd_tree")), (Some(&Value::Bool(false)), Some(&Value::Bool(true))));
        // mkinitrd.conf's names are read only when it is what runs: no
        // RESUMEDEV here, as no ROOTDEV.
        assert!(g.get("resumedev").unwrap().is_null() && g.get("rootdev").unwrap().is_null());
    }

    /// mkinitrd.conf counts for -F only (or with no command line); a
    /// relative -o is where initrd-name says, else in /boot; -C and -h are
    /// recorded.
    #[test]
    fn mkinitrd_fields() {
        let t = Tree::new("mkinitrd-fields");
        t.file("/etc/slackware-version", "Slackware 15.0
")
            .file("/boot/initrd.gz", "i")
            .dir("/lib/modules/5.15.161/kernel")
            .file("/etc/mkinitrd.conf", "KERNEL_VERSION=\"9.9.9\"\nROOTDEV=\"/dev/sdz9\"\nOUTPUT_IMAGE=\"/boot/stale.gz\"\nLUKSDEV=\"/dev/sdz8\"\n")
            .file(MKINITRD_COMMAND_LINE, "/sbin/mkinitrd -c -k 5.15.161 -r /dev/vda3 -h /dev/vda2 -o initrd.gz\n")
            .file("/boot/initrd-tree/initrd-name", "/root/initrd.gz\n");
        let s = Sys::new(t.root(), Box::new(FakeRunner::default()), false);
        let generate = || {
            let (g, f) = slackware_initramfs(&s, &[]);
            (g.unwrap(), f.into_iter().find(|(k, _)| *k == "mkinitrd").map(|(_, v)| v).unwrap())
        };
        let (g, m) = generate();
        assert_eq!(g, "mkinitrd");
        assert_eq!(m.at(&["kernels", "0"]).and_then(Value::as_str), Some("5.15.161"));
        assert_eq!(m.get("rootdev").and_then(Value::as_str), Some("/dev/vda3"));
        assert_eq!(m.get("resumedev").and_then(Value::as_str), Some("/dev/vda2"));
        assert!(m.get("luksdev").unwrap().is_null(), "{}", m.compact());
        assert_eq!(m.get("output").and_then(Value::as_str), Some("/root/initrd.gz"));
        // Without initrd-name, a relative -o is /boot's.
        std::fs::remove_file(t.p("/boot/initrd-tree/initrd-name")).unwrap();
        assert_eq!(generate().1.get("output").and_then(Value::as_str), Some("/boot/initrd.gz"));
        // With -F (and -C), the conf counts where the command line is silent.
        t.file(MKINITRD_COMMAND_LINE, "mkinitrd -F -C /dev/vda3\n");
        let (_, m) = generate();
        assert_eq!(m.get("rootdev").and_then(Value::as_str), Some("/dev/sdz9"));
        assert_eq!(m.get("output").and_then(Value::as_str), Some("/boot/stale.gz"));
        assert_eq!(m.get("luksdev").and_then(Value::as_str), Some("/dev/vda3"));
        // KERNEL_VERSION 9.9.9 has no modules: no kernel.
        assert!(m.get("kernels").and_then(Value::as_array).unwrap().is_empty());
    }
}
