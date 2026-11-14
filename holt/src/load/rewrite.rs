//! `bko-holt rewrite LAYOUT TARGET [--check] --reformat MOUNT=TYPE...` (not
//! in the usage: run by load's boot stage for --reformat, as
//! docs/design/reformat.md says).
//!
//! Without --check: the restored system under TARGET has its files that
//! name the old filesystems rewritten, each a small, exact edit, logged as
//! the lines it removes and adds: fstab, the kernel command lines
//! (/etc/kernel/cmdline, /etc/cmdline.d, BLS entries, grubenv,
//! /etc/default/grub, limine.conf and /etc/default/limine, rEFInd's
//! refind_linux.conf and refind.conf), systemd mount units, and
//! mkinitcpio's btrfs.  A file that
//! isn't as expected stops it, rather than being edited by guesswork.
//!
//! With --check, after the boot stage made the initramfs and the loader:
//! each initramfs image is listed with its generator's tool and must hold
//! the new type's module (or its kernel has it built in); a kernel command
//! line still asking for a subvolume fails too.  What else is left of the
//! old type (its UUID next to its name, its tools' configuration) is listed
//! as warnings.
//!
//! Both work the change out again from LAYOUT and the --reformats
//! (reformat::work_out), exactly as load did.

use super::reformat::{self, Change};
use crate::json::Value;
use std::path::{Path, PathBuf};

fn rootflags_word(w: &str, c: &Change) -> Option<String> {
    if let Some(v) = w.strip_prefix("rootflags=") {
        let keep: Vec<&str> = v.split(',').filter(|x| !x.is_empty() && *x != "defaults" && reformat::generic_options(x).1.is_empty()).collect();
        return if keep.is_empty() { None } else { Some(format!("rootflags={}", keep.join(","))) };
    }
    if w.strip_prefix("rootfstype=") == Some(c.from.as_str()) {
        return Some(format!("rootfstype={}", c.to));
    }
    Some(w.to_string())
}

/// A kernel command line with the root's old-type options taken out:
/// rootflags= cut to its generic options (gone when none are left),
/// rootfstype= set to the new type.  Unchanged, it's returned as it was.
pub fn cmdline(line: &str, c: &Change) -> String {
    let words: Vec<&str> = line.split_whitespace().collect();
    let new: Vec<String> = words.iter().filter_map(|w| rootflags_word(w, c)).collect();
    if new.iter().map(String::as_str).eq(words.iter().copied()) {
        return line.to_string();
    }
    // Keep the line's own leading and trailing space.
    let lead = &line[..line.len() - line.trim_start().len()];
    let trail = &line[line.trim_end().len()..];
    format!("{lead}{}{trail}", new.join(" "))
}

/// fstab with the reformatted filesystem's entries rewritten: its kept
/// mount gets the new type, options and pass; its folded mounts go.  Every
/// other line stays byte for byte.
pub fn fstab(text: &str, c: &Change) -> Result<String, String> {
    let mut out = String::new();
    let (mut kept, mut gone) = (false, Vec::new());
    for line in text.split_inclusive('\n') {
        let body = line.trim_end_matches('\n');
        let f: Vec<&str> = body.split_whitespace().collect();
        if f.len() >= 3 && !f[0].starts_with('#') && f[2] == c.from {
            let file = f[1].replace("\\040", " ");
            if file == c.mount {
                let dump = f.get(4).copied().unwrap_or("0");
                out += &format!("{}\t{}\t{}\t{}\t{}\t{}", f[0], f[1], c.to, c.options, dump, c.passno);
                out += if line.ends_with('\n') { "\n" } else { "" };
                kept = true;
                continue;
            }
            if c.folded.contains(&file) {
                gone.push(file);
                continue;
            }
        }
        out += line;
    }
    if !kept {
        // The boot stage run again (--only boot, or --through close after
        // it stopped): this fstab was rewritten the first time, so its
        // mount has the new type's line and none of the old type's is left.
        let done = text.lines().any(|l| {
            let f: Vec<&str> = l.split_whitespace().collect();
            f.len() >= 3 && !f[0].starts_with('#') && f[1].replace("\\040", " ") == c.mount && f[2] == c.to
        });
        if done && gone.is_empty() {
            return Ok(text.to_string());
        }
        return Err(format!("no {} line for {} in it", c.from, c.mount));
    }
    if let Some(f) = c.folded.iter().find(|f| !gone.contains(f)) {
        return Err(format!("no {} line for {f} in it", c.from));
    }
    Ok(out)
}

/// A BLS entry: its `options` lines through cmdline().
pub fn bls_entry(text: &str, c: &Change) -> String {
    text.split_inclusive('\n')
        .map(|l| {
            let t = l.trim_start();
            match t.strip_prefix("options") {
                Some(rest) if rest.starts_with([' ', '\t']) => format!("{}options{}", &l[..l.len() - t.len()], cmdline(rest, c)),
                _ => l.to_string(),
            }
        })
        .collect()
}

/// GRUB's environment block: kernelopts= through cmdline(), padded with #
/// back to the block's size (1024 bytes) so grub-editenv still takes it.
pub fn grubenv(text: &str, c: &Change) -> Result<String, String> {
    let body = text.trim_end_matches('#');
    let mut out = String::new();
    for l in body.split_inclusive('\n') {
        match l.strip_prefix("kernelopts=") {
            Some(v) => out += &format!("kernelopts={}", cmdline(v, c)),
            None => out += l,
        }
    }
    if out.len() > text.len() {
        return Err("the rewritten block is longer than the old one".into());
    }
    out += &"#".repeat(text.len() - out.len());
    Ok(out)
}

/// A shell assignment's value through cmdline(), inside its quotes: the
/// lines whose variable NAMED says, `NAME=` or `NAME+=`.
fn assignments(text: &str, c: &Change, named: impl Fn(&str) -> bool) -> String {
    text.split_inclusive('\n')
        .map(|l| {
            let Some(eq) = l.find('=') else { return l.to_string() };
            let (key, v) = (&l[..=eq], &l[eq + 1..]);
            if !named(key.trim_end_matches('=').trim_end_matches('+')) {
                return l.to_string();
            }
            let (v, nl) = v.strip_suffix('\n').map(|x| (x, "\n")).unwrap_or((v, ""));
            let q = v.chars().next().filter(|q| *q == '"' || *q == '\'');
            match q {
                Some(q) if v.len() >= 2 && v.ends_with(q) => format!("{key}{q}{}{q}{nl}", cmdline(&v[1..v.len() - 1], c)),
                _ => format!("{key}{}{nl}", cmdline(v, c)),
            }
        })
        .collect()
}

/// /etc/default/grub: GRUB_CMDLINE_LINUX and GRUB_CMDLINE_LINUX_DEFAULT
/// through cmdline(), inside their quotes.
pub fn default_grub(text: &str, c: &Change) -> String {
    assignments(text, c, |k| matches!(k, "GRUB_CMDLINE_LINUX" | "GRUB_CMDLINE_LINUX_DEFAULT"))
}

/// /etc/default/limine (limine-entry-tool, CachyOS): KERNEL_CMDLINE[...]=
/// and +=, from which limine-mkinitcpio-hook writes limine.conf again when
/// the boot stage runs mkinitcpio.
pub fn default_limine(text: &str, c: &Change) -> String {
    assignments(text, c, |k| k == "KERNEL_CMDLINE" || (k.starts_with("KERNEL_CMDLINE[") && k.ends_with(']')))
}

/// rEFInd's refind_linux.conf (`"TITLE" "OPTIONS"` a line) and refind.conf
/// (a stanza's `options "..."`): each line's last quoted string, or an
/// options line's value, through cmdline().
pub fn refind(text: &str, c: &Change) -> String {
    text.split_inclusive('\n')
        .map(|l| {
            let t = l.trim_start();
            if t.starts_with('#') {
                return l.to_string();
            }
            let opts = t.strip_prefix("options").is_some_and(|r| r.starts_with([' ', '\t']));
            let body = l.trim_end_matches('\n');
            let nl = &l[body.len()..];
            if let (Some(end), true) = (body.rfind('"'), body.matches('"').count() >= 2) {
                let start = body[..end].rfind('"').unwrap_or(end);
                return format!("{}{}{}{nl}", &body[..=start], cmdline(&body[start + 1..end], c), &body[end..]);
            }
            if opts {
                let cut = l.len() - t.len() + "options".len();
                return format!("{}{}", &l[..cut], cmdline(&l[cut..], c));
            }
            l.to_string()
        })
        .collect()
}

/// limine.conf: each `cmdline:` / `kernel_cmdline:` through cmdline().
pub fn limine(text: &str, c: &Change) -> String {
    text.split_inclusive('\n')
        .map(|l| {
            let t = l.trim_start();
            for key in ["cmdline:", "kernel_cmdline:", "CMDLINE=", "KERNEL_CMDLINE="] {
                if t.len() >= key.len() && t[..key.len()].eq_ignore_ascii_case(key) {
                    let cut = l.len() - t.len() + key.len();
                    return format!("{}{}", &l[..cut], cmdline(&l[cut..], c));
                }
            }
            l.to_string()
        })
        .collect()
}

/// mkinitcpio.conf: btrfs out of MODULES=(), BINARIES=() and HOOKS=().
pub fn mkinitcpio(text: &str) -> String {
    text.split_inclusive('\n')
        .map(|l| {
            for key in ["MODULES=(", "BINARIES=(", "HOOKS=("] {
                if let Some(rest) = l.strip_prefix(key) {
                    if let Some((inside, after)) = rest.split_once(')') {
                        let words: Vec<&str> = inside.split_whitespace().filter(|w| !matches!(w.trim_matches(['"', '\'']), "btrfs" | "/usr/bin/btrfs" | "/bin/btrfs" | "/sbin/btrfs" | "/usr/sbin/btrfs")).collect();
                        if words.len() != inside.split_whitespace().count() {
                            return format!("{key}{}){after}", words.join(" "));
                        }
                    }
                }
            }
            l.to_string()
        })
        .collect()
}

/// A systemd mount unit for the kept mount: Type= and Options= as fstab's.
pub fn mount_unit(text: &str, c: &Change) -> String {
    let wher = text.lines().find_map(|l| l.trim().strip_prefix("Where=")).map(str::trim);
    let ty = text.lines().find_map(|l| l.trim().strip_prefix("Type=")).map(str::trim);
    if wher != Some(c.mount.as_str()) || ty != Some(c.from.as_str()) {
        return text.to_string();
    }
    text.split_inclusive('\n')
        .map(|l| {
            let nl = if l.ends_with('\n') { "\n" } else { "" };
            if l.trim().starts_with("Type=") {
                format!("Type={}{nl}", c.to)
            } else if let Some(o) = l.trim().strip_prefix("Options=") {
                format!("Options={}{nl}", reformat::generic_options(o.trim()).0)
            } else {
                l.to_string()
            }
        })
        .collect()
}

// ---- running it ------------------------------------------------------------

fn read_dir(p: &Path) -> Vec<PathBuf> {
    let mut v: Vec<PathBuf> = std::fs::read_dir(p).into_iter().flatten().filter_map(|e| e.ok().map(|e| e.path())).collect();
    v.sort();
    v
}

fn is_file(p: &Path) -> bool {
    std::fs::symlink_metadata(p).is_ok_and(|m| m.is_file())
}

fn is_dir(p: &Path) -> bool {
    std::fs::symlink_metadata(p).is_ok_and(|m| m.is_dir())
}

/// The lines OLD has that NEW doesn't, then the reverse, for the log.
fn changed_lines(old: &str, new: &str) -> String {
    let (o, n): (Vec<&str>, Vec<&str>) = (old.lines().collect(), new.lines().collect());
    let mut out = String::new();
    for l in o.iter().filter(|l| !n.contains(l)) {
        out += &format!("    - {l}\n");
    }
    for l in n.iter().filter(|l| !o.contains(l)) {
        out += &format!("    + {l}\n");
    }
    out
}

/// The ESPs' mount points the layout records, and /boot.
fn boot_dirs(layout: &Value) -> Vec<String> {
    let mut v: Vec<String> = layout.at(&["boot", "esps"]).and_then(Value::as_array).into_iter().flatten().filter_map(|e| e.get("mounted_at").and_then(Value::as_str).map(str::to_string)).collect();
    for d in ["/boot", "/boot/efi", "/efi"] {
        if !v.iter().any(|x| x == d) {
            v.push(d.to_string());
        }
    }
    v
}

struct Run<'a> {
    t: &'a str,
    log: String,
    errors: Vec<String>,
}

impl Run<'_> {
    fn at(&self, p: &str) -> PathBuf {
        PathBuf::from(format!("{}{p}", self.t))
    }

    fn edit(&mut self, path: &Path, f: impl Fn(&str) -> Result<String, String>) {
        let Ok(old) = std::fs::read_to_string(path) else { return };
        match f(&old) {
            Ok(new) if new != old => match std::fs::write(path, &new) {
                Ok(()) => self.log += &format!("bko-holt rewrite: {}:\n{}", path.display(), changed_lines(&old, &new)),
                Err(e) => self.errors.push(format!("{}: {e}", path.display())),
            },
            Ok(_) => {}
            Err(e) => self.errors.push(format!("{}: {e}", path.display())),
        }
    }

    fn rewrite(&mut self, c: &Change, layout: &Value, btrfs_left: bool) {
        let fstab_path = self.at("/etc/fstab");
        if !is_file(&fstab_path) {
            self.errors.push(format!("{}: not there", fstab_path.display()));
            return;
        }
        self.edit(&fstab_path, |t| fstab(t, c));
        if c.mount == "/" {
            let (k, g, lim) = (self.at("/etc/kernel/cmdline"), self.at("/etc/default/grub"), self.at("/etc/default/limine"));
            self.edit(&k, |t| Ok(cmdline(t, c)));
            self.edit(&g, |t| Ok(default_grub(t, c)));
            self.edit(&lim, |t| Ok(default_limine(t, c)));
            // mkinitcpio's and kernel-install's command line drop-ins.
            for p in read_dir(&self.at("/etc/cmdline.d")).into_iter().filter(|p| p.extension().is_some_and(|x| x == "conf")) {
                self.edit(&p, |t| Ok(t.split_inclusive('\n').map(|l| if l.trim_start().starts_with('#') { l.to_string() } else { cmdline(l, c) }).collect()));
            }
            for d in boot_dirs(layout) {
                for e in read_dir(&self.at(&format!("{d}/loader/entries"))).into_iter().filter(|p| p.extension().is_some_and(|x| x == "conf")) {
                    self.edit(&e, |t| Ok(bls_entry(t, c)));
                }
                let mut envs = vec![self.at(&format!("{d}/grub2/grubenv")), self.at(&format!("{d}/grub/grubenv"))];
                envs.extend(read_dir(&self.at(&format!("{d}/EFI"))).into_iter().map(|p| p.join("grubenv")));
                for l in ["limine.conf", "limine/limine.conf", "EFI/BOOT/limine.conf", "EFI/limine/limine.conf"] {
                    let p = self.at(&format!("{d}/{l}"));
                    self.edit(&p, |t| Ok(limine(t, c)));
                }
                for e in envs {
                    self.edit(&e, |t| grubenv(t, c));
                }
                // rEFInd (PikaOS): refind_linux.conf beside the kernels, and
                // refind.conf's manual stanzas on the ESP.
                let mut refind_files = vec![self.at(&format!("{d}/refind_linux.conf"))];
                refind_files.extend(read_dir(&self.at(&format!("{d}/EFI"))).into_iter().map(|p| p.join("refind.conf")));
                for p in refind_files {
                    self.edit(&p, |t| Ok(refind(t, c)));
                }
            }
        }
        for u in read_dir(&self.at("/etc/systemd/system")).into_iter().filter(|p| p.extension().is_some_and(|x| x == "mount")) {
            self.edit(&u, |t| Ok(mount_unit(t, c)));
        }
        if c.from == "btrfs" && !btrfs_left {
            let mut confs = vec![self.at("/etc/mkinitcpio.conf")];
            confs.extend(read_dir(&self.at("/etc/mkinitcpio.conf.d")).into_iter().filter(|p| p.extension().is_some_and(|x| x == "conf")));
            for p in confs {
                self.edit(&p, |t| Ok(mkinitcpio(t)));
            }
        }
    }

    /// Each initramfs image, by the generator's own lister, must have the
    /// new type's module or a kernel with it built in.
    fn check_initramfs(&mut self, c: &Change, layout: &Value) {
        let lister = match layout.at(&["boot", "initramfs", "generator"]).and_then(Value::as_str) {
            Some("dracut") => "lsinitrd",
            Some("mkinitcpio") => "lsinitcpio",
            Some("initramfs-tools") => "lsinitramfs",
            Some("prebuilt") => {
                self.log += "bko-holt rewrite: the initramfs came with its kernel; not checked\n";
                return;
            }
            // mkinitfs (Alpine), booster (PikaOS), tiny-initramfs: no lister.
            g => {
                self.log += &format!("bko-holt rewrite: {}'s images aren't listed here; not checked\n", g.unwrap_or("an unknown generator"));
                return;
            }
        };
        let mut images: Vec<PathBuf> = Vec::new();
        for d in boot_dirs(layout) {
            let dir = self.at(&d);
            for p in read_dir(&dir) {
                let n = p.file_name().map(|n| n.to_string_lossy().into_owned()).unwrap_or_default();
                if is_file(&p) && (n.starts_with("initramfs-") || n.starts_with("initrd.img-") || n.starts_with("initrd-")) {
                    images.push(p);
                } else if is_dir(&p) {
                    // kernel-install's ENTRY-TOKEN/KVER/initrd.
                    for k in read_dir(&p).into_iter().filter(|k| is_dir(k)) {
                        // limine-mkinitcpio-hook's MACHINE-ID/KERNEL/initramfs.
                        images.extend(read_dir(&k).into_iter().filter(|f| is_file(f) && f.file_name().is_some_and(|n| n.to_string_lossy().starts_with("initrd") || n.to_string_lossy() == "initramfs")));
                    }
                }
            }
        }
        images.sort();
        images.dedup();
        if images.is_empty() {
            self.errors.push("no initramfs image found under /boot or the ESP to check".into());
            return;
        }
        let module = format!("/kernel/fs/{0}/{0}.ko", c.to);
        for img in images {
            let inside = img.to_string_lossy().trim_start_matches(self.t).to_string();
            let out = std::process::Command::new("chroot").args([self.t, lister, &inside]).output();
            let text = match out {
                Ok(o) if o.status.success() => String::from_utf8_lossy(&o.stdout).into_owned(),
                Ok(o) => {
                    self.errors.push(format!("{lister} {inside}: {}", String::from_utf8_lossy(&o.stderr).trim()));
                    continue;
                }
                Err(e) => {
                    self.errors.push(format!("chroot {} {lister}: {e}", self.t));
                    continue;
                }
            };
            if text.lines().any(|l| l.contains(&module)) {
                self.log += &format!("bko-holt rewrite: {inside} has {}\n", module.trim_start_matches('/'));
                continue;
            }
            let kver = text.lines().find_map(|l| l.split_once("lib/modules/").and_then(|(_, r)| r.split('/').next()).filter(|k| !k.is_empty()).map(str::to_string));
            let builtin = kver.as_ref().is_some_and(|k| {
                ["/usr/lib/modules", "/lib/modules"].iter().any(|m| std::fs::read_to_string(self.at(&format!("{m}/{k}/modules.builtin"))).is_ok_and(|b| b.lines().any(|l| format!("/{l}").starts_with(&module))))
            });
            if builtin {
                self.log += &format!("bko-holt rewrite: {inside}: {} is built into {}\n", c.to, kver.unwrap_or_default());
            } else {
                self.errors.push(format!("{inside} has no {} module, and its kernel{} doesn't have it built in", c.to, kver.map(|k| format!(" ({k})")).unwrap_or_default()));
            }
        }
    }

    /// What's left of the old type: a command line still asking for a
    /// subvolume fails (the root wouldn't mount); the old UUID beside the
    /// old type's name, and the old type's tools, are warnings.
    fn leftovers(&mut self, c: &Change, layout: &Value) {
        let mut dirs = vec!["/etc".to_string()];
        dirs.extend(boot_dirs(layout));
        let mut seen = std::collections::BTreeSet::new();
        let uuid = c.uuid.to_ascii_lowercase();
        for d in dirs {
            let mut stack = vec![self.at(&d)];
            while let Some(p) = stack.pop() {
                if is_dir(&p) {
                    stack.extend(read_dir(&p));
                    continue;
                }
                if !is_file(&p) || seen.contains(&p) || std::fs::metadata(&p).map(|m| m.len()).unwrap_or(0) > 1 << 20 {
                    continue;
                }
                seen.insert(p.clone());
                let Ok(bytes) = std::fs::read(&p) else { continue };
                if bytes.iter().take(8192).any(|b| *b == 0) {
                    continue;
                }
                let text = String::from_utf8_lossy(&bytes);
                let name = p.to_string_lossy().trim_start_matches(self.t).to_string();
                for (i, l) in text.lines().enumerate() {
                    let t = l.trim_start();
                    if t.starts_with('#') {
                        continue;
                    }
                    let low = t.to_ascii_lowercase();
                    if c.mount == "/" && c.from == "btrfs" && low.split_whitespace().any(|w| w.starts_with("rootflags=") && w.contains("subvol")) {
                        self.errors.push(format!("{name}:{}: a kernel command line still asks for a subvolume: {}", i + 1, t.trim()));
                    } else if low.contains(&uuid) && (low.split(|ch: char| !ch.is_ascii_alphanumeric()).any(|w| w == c.from) || low.contains("subvol")) {
                        self.log += &format!("bko-holt rewrite: left over: {name}:{}: {}\n", i + 1, t.trim());
                    }
                }
            }
        }
        if c.from == "btrfs" {
            let mut tools = Vec::new();
            if !read_dir(&self.at("/etc/snapper/configs")).is_empty() {
                tools.push("snapper's configs (/etc/snapper/configs)".to_string());
            }
            if is_file(&self.at("/etc/grub.d/41_snapshots-btrfs")) {
                tools.push("grub-btrfs (/etc/grub.d/41_snapshots-btrfs)".into());
            }
            for w in read_dir(&self.at("/etc/systemd/system")).into_iter().filter(|p| p.to_string_lossy().ends_with(".wants")) {
                for u in read_dir(&w) {
                    let n = u.file_name().map(|n| n.to_string_lossy().into_owned()).unwrap_or_default();
                    if n.starts_with("btrfs") || n.starts_with("grub-btrfsd") || n.starts_with("snapper") {
                        tools.push(format!("{n}, enabled"));
                    }
                }
            }
            if std::fs::read_to_string(self.at("/etc/timeshift/timeshift.json")).is_ok_and(|t| t.replace(' ', "").contains("\"btrfs_mode\":\"true\"")) {
                tools.push("Timeshift in btrfs mode".into());
            }
            for x in tools {
                self.log += &format!("bko-holt rewrite: made for btrfs, still there: {x}\n");
            }
        }
    }
}

pub fn main(args: &[String]) -> u8 {
    let usage = "usage: bko-holt rewrite LAYOUT TARGET [--check] --reformat MOUNT=TYPE...";
    let (mut pos, mut check, mut reformats) = (Vec::new(), false, Vec::new());
    let mut it = args.iter();
    while let Some(a) = it.next() {
        match a.as_str() {
            "--check" => check = true,
            "--reformat" => match it.next().and_then(|v| v.split_once('=')) {
                Some((m, t)) => reformats.push((m.to_string(), t.to_string())),
                None => {
                    eprintln!("{usage}");
                    return 2;
                }
            },
            s => pos.push(s.to_string()),
        }
    }
    let [layout_path, target] = pos.as_slice() else {
        eprintln!("{usage}");
        return 2;
    };
    let layout = match std::fs::read_to_string(layout_path).map_err(|e| e.to_string()).and_then(|t| crate::json::parse(&t).map_err(|e| e.message.to_string())) {
        Ok(v) => v,
        Err(e) => {
            eprintln!("bko-holt rewrite: {layout_path}: {e}");
            return 2;
        }
    };
    let r = reformat::work_out(&layout, &reformats);
    if !r.problems.is_empty() {
        for p in &r.problems {
            eprintln!("bko-holt rewrite: {p}");
        }
        return 2;
    }
    let edited = reformat::apply(layout.clone(), &r);
    let btrfs_left = edited.at(&["storage", "fstab"]).and_then(Value::as_array).into_iter().flatten().any(|e| e.get("type").and_then(Value::as_str) == Some("btrfs"));
    let mut run = Run { t: target.trim_end_matches('/'), log: String::new(), errors: Vec::new() };
    for c in &r.changes {
        if check {
            run.check_initramfs(c, &layout);
            run.leftovers(c, &layout);
        } else {
            run.rewrite(c, &layout, btrfs_left);
        }
    }
    print!("{}", run.log);
    for e in &run.errors {
        eprintln!("bko-holt rewrite: {e}");
    }
    u8::from(!run.errors.is_empty())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn change() -> Change {
        Change {
            mount: "/".into(),
            src: "nvme0n1p2".into(),
            uuid: "1b64a017-659b-4484-bf36-b97f639c3c35".into(),
            from: "btrfs".into(),
            to: "xfs".into(),
            label: None,
            folded: vec!["/home".into(), "/var/cache".into(), "/var/log".into()],
            unmounted: vec![],
            nested: vec![],
            snapshots: vec![],
            aliases: vec![],
            features: vec![],
            compat_ro: vec![],
            features_from: String::new(),
            options: "noatime".into(),
            dropped: vec![],
            passno: 0,
        }
    }

    // EndeavourOS's, as its installer wrote it.
    const EOS_FSTAB: &str = "# <file system>             <mount point>  <type>  <options>  <dump>  <pass>\n\
UUID=B9BB-8C26                            /efi           vfat    fmask=0137,dmask=0027 0 2\n\
UUID=1b64a017-659b-4484-bf36-b97f639c3c35 /              btrfs   subvol=/@,noatime,compress=zstd 0 0\n\
UUID=1b64a017-659b-4484-bf36-b97f639c3c35 /home          btrfs   subvol=/@home,noatime,compress=zstd 0 0\n\
UUID=1b64a017-659b-4484-bf36-b97f639c3c35 /var/cache     btrfs   subvol=/@cache,noatime,compress=zstd 0 0\n\
UUID=1b64a017-659b-4484-bf36-b97f639c3c35 /var/log       btrfs   subvol=/@log,noatime,compress=zstd 0 0\n\
tmpfs                                     /tmp           tmpfs   defaults,noatime,mode=1777 0 0\n";

    #[test]
    fn fstab_of_endeavouros() {
        let out = fstab(EOS_FSTAB, &change()).unwrap();
        assert_eq!(
            out,
            "# <file system>             <mount point>  <type>  <options>  <dump>  <pass>\n\
UUID=B9BB-8C26                            /efi           vfat    fmask=0137,dmask=0027 0 2\n\
UUID=1b64a017-659b-4484-bf36-b97f639c3c35\t/\txfs\tnoatime\t0\t0\n\
tmpfs                                     /tmp           tmpfs   defaults,noatime,mode=1777 0 0\n"
        );
        // Already rewritten (the boot stage run again): left as it is.
        assert_eq!(fstab(&out, &change()).unwrap(), out);
        // Neither type's line for /: not the fstab the layout describes.
        let none = EOS_FSTAB.lines().filter(|l| !l.contains(" /              ")).map(|l| format!("{l}\n")).collect::<String>();
        assert!(fstab(&none, &change()).unwrap_err().contains("no btrfs line for /"));
    }

    #[test]
    fn command_lines() {
        let c = change();
        assert_eq!(cmdline("nvme_load=YES nowatchdog rw rootflags=subvol=/@ root=UUID=1b64", &c), "nvme_load=YES nowatchdog rw root=UUID=1b64");
        assert_eq!(cmdline(" root=UUID=x rootflags=subvol=@,noatime rootfstype=btrfs quiet\n", &c), " root=UUID=x rootflags=noatime rootfstype=xfs quiet\n");
        assert_eq!(cmdline("root=UUID=x ro  quiet", &c), "root=UUID=x ro  quiet");
        let entry = "title   EndeavourOS\nlinux   /0bb6/6.18.60-1-lts/linux\noptions nvme_load=YES rw rootflags=subvol=/@ root=UUID=1b64 systemd.machine_id=0bb6\n";
        assert_eq!(bls_entry(entry, &c), "title   EndeavourOS\nlinux   /0bb6/6.18.60-1-lts/linux\noptions nvme_load=YES rw root=UUID=1b64 systemd.machine_id=0bb6\n");
        let g = "GRUB_TIMEOUT=5\nGRUB_CMDLINE_LINUX_DEFAULT=\"quiet splash\"\nGRUB_CMDLINE_LINUX=\"rootflags=subvol=root rhgb\"\n";
        assert_eq!(default_grub(g, &c), "GRUB_TIMEOUT=5\nGRUB_CMDLINE_LINUX_DEFAULT=\"quiet splash\"\nGRUB_CMDLINE_LINUX=\"rhgb\"\n");
        let lim = "TARGET_OS_NAME=\"CachyOS\"\nKERNEL_CMDLINE[default]+=\"quiet nowatchdog splash rw rootflags=subvol=/@ root=UUID=x\"\n";
        assert_eq!(default_limine(lim, &c), "TARGET_OS_NAME=\"CachyOS\"\nKERNEL_CMDLINE[default]+=\"quiet nowatchdog splash rw root=UUID=x\"\n");
        let rl = "\"Boot with standard options\"  \"quiet splash rootflags=subvol=@ root=UUID=x\"\n# \"x\" \"rootflags=subvol=@\"\n";
        assert_eq!(refind(rl, &c), "\"Boot with standard options\"  \"quiet splash root=UUID=x\"\n# \"x\" \"rootflags=subvol=@\"\n");
        assert_eq!(refind("menuentry x {\n    options \"rw rootflags=subvol=@\"\n}\n", &c), "menuentry x {\n    options \"rw\"\n}\n");
        let l = "/Omarchy\n    protocol: linux\n    kernel_cmdline: cryptdevice=PARTUUID=x:root root=/dev/mapper/root zswap.enabled=0 rootflags=subvol=@ rw\n";
        assert_eq!(limine(l, &c), "/Omarchy\n    protocol: linux\n    kernel_cmdline: cryptdevice=PARTUUID=x:root root=/dev/mapper/root zswap.enabled=0 rw\n");
    }

    #[test]
    fn grubenv_keeps_its_size() {
        let mut env = "# GRUB Environment Block\nsaved_entry=x\nkernelopts=root=UUID=x ro rootflags=subvol=root rhgb quiet\n".to_string();
        env += &"#".repeat(1024 - env.len());
        let out = grubenv(&env, &change()).unwrap();
        assert_eq!(out.len(), 1024);
        assert!(out.starts_with("# GRUB Environment Block\nsaved_entry=x\nkernelopts=root=UUID=x ro rhgb quiet\n#"));
    }

    #[test]
    fn mkinitcpio_and_mount_units() {
        assert_eq!(mkinitcpio("MODULES=(btrfs i915)\nBINARIES=(/usr/bin/btrfs)\nHOOKS=(base udev autodetect btrfs filesystems fsck)\nFILES=()\n"), "MODULES=(i915)\nBINARIES=()\nHOOKS=(base udev autodetect filesystems fsck)\nFILES=()\n");
        assert_eq!(mkinitcpio("MODULES=()\n"), "MODULES=()\n");
        let c = change();
        let unit = "[Mount]\nWhat=/dev/disk/by-uuid/1b64\nWhere=/\nType=btrfs\nOptions=subvol=@,noatime\n";
        assert_eq!(mount_unit(unit, &c), "[Mount]\nWhat=/dev/disk/by-uuid/1b64\nWhere=/\nType=xfs\nOptions=noatime\n");
        assert_eq!(mount_unit("[Mount]\nWhere=/srv\nType=btrfs\n", &c), "[Mount]\nWhere=/srv\nType=btrfs\n");
    }

    #[test]
    fn a_target_tree() {
        let t = crate::testutil::TempDir::new("rewrite");
        let root = t.path().to_string_lossy().into_owned();
        let w = |p: &str, s: &str| {
            let f = format!("{root}{p}");
            std::fs::create_dir_all(Path::new(&f).parent().unwrap()).unwrap();
            std::fs::write(f, s).unwrap();
        };
        w("/etc/fstab", EOS_FSTAB);
        w("/etc/kernel/cmdline", "nvme_load=YES rw rootflags=subvol=/@ root=UUID=1b64a017-659b-4484-bf36-b97f639c3c35\n");
        w("/efi/loader/entries/0bb6-6.18.60-1-lts.conf", "title EndeavourOS\noptions rw rootflags=subvol=/@ root=UUID=1b64a017-659b-4484-bf36-b97f639c3c35\n");
        w("/etc/grub.d/41_snapshots-btrfs", "#!/bin/sh\n");
        let layout = crate::json::parse(r#"{"boot": {"esps": [{"mounted_at": "/efi"}]}}"#).unwrap();
        let mut run = Run { t: &root, log: String::new(), errors: vec![] };
        run.rewrite(&change(), &layout, false);
        assert!(run.errors.is_empty(), "{:?}", run.errors);
        assert_eq!(std::fs::read_to_string(format!("{root}/etc/kernel/cmdline")).unwrap(), "nvme_load=YES rw root=UUID=1b64a017-659b-4484-bf36-b97f639c3c35\n");
        assert!(std::fs::read_to_string(format!("{root}/efi/loader/entries/0bb6-6.18.60-1-lts.conf")).unwrap().contains("options rw root=UUID="));
        assert!(run.log.contains("    - UUID=1b64a017-659b-4484-bf36-b97f639c3c35 /home"), "{}", run.log);
        // The boot stage run again: nothing left to change, and no error.
        run.log.clear();
        run.rewrite(&change(), &layout, false);
        assert!(run.errors.is_empty() && run.log.is_empty(), "{:?} {}", run.errors, run.log);
        run.log.clear();
        run.leftovers(&change(), &layout);
        assert!(run.errors.is_empty(), "{:?}", run.errors);
        assert!(run.log.contains("made for btrfs, still there: grub-btrfs"), "{}", run.log);
        // An entry the rewrite missed fails the check.
        w("/efi/loader/entries/other.conf", "options root=UUID=x rootflags=subvol=@\n");
        run.leftovers(&change(), &layout);
        assert!(run.errors.iter().any(|e| e.contains("other.conf:1: a kernel command line still asks for a subvolume")), "{:?}", run.errors);
    }
}
