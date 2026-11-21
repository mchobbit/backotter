//! `bko-holt rewrite LAYOUT TARGET [--check] --reformat MOUNT=TYPE...` (not
//! in the usage: run by load's boot stage for --reformat, as
//! docs/design/reformat.md says).
//!
//! Without --check: the restored system under TARGET has its files that
//! name the old filesystems rewritten, each a small, exact edit, logged as
//! the lines it removes and adds: fstab, the kernel command lines
//! (/etc/kernel/cmdline, /etc/cmdline.d, BLS entries, grubenv,
//! /etc/default/grub, limine.conf and /etc/default/limine, rEFInd's
//! refind_linux.conf and refind.conf), systemd mount units,
//! mkinitcpio's btrfs, and the ESPs' GRUB stubs (EFI/*/grub.cfg, whose
//! prefix named a path inside a btrfs subvolume).  A file that
//! isn't as expected stops it, rather than being edited by guesswork.
//!
//! With --check, after the boot stage made the initramfs and the loader:
//! each initramfs image is listed with its generator's tool and must hold
//! the new type's module (or its kernel has it built in); a kernel command
//! line still asking for a subvolume fails too (grub-btrfs's old
//! grub-btrfs.cfg only while a grub.cfg reads it).  What else is left of the
//! old type (its UUID next to its name, its tools' configuration) is listed
//! as warnings.
//!
//! Both work the change out again from LAYOUT and the --reformats
//! (reformat::work_out), exactly as load did.

use super::reformat::{self, Change};
use crate::json::Value;
use crate::mounts;
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
            // fstab(5)'s octal escapes (\040, \011, \012, \134, any \NNN)
            // decoded for the match; the line keeps its own spelling.
            let file = mounts::unescape(f[1]);
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
            f.len() >= 3 && !f[0].starts_with('#') && mounts::unescape(f[1]) == c.mount && f[2] == c.to
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

/// grubenv on a filesystem that had GRUB's environment block in btrfs's
/// reserved area and doesn't now (--reformat from btrfs): its env_block=
/// line taken out, so GRUB no longer reads a sector the new type uses, and
/// the block's variables VARS (reformat::grub_env_block, as GRUB wrote
/// them) put in, each over the file's own (GRUB reads the block after the
/// file), padded with # back to the file's size.  A file with no
/// env_block= is left as it is.
pub fn grubenv_unblock(text: &str, vars: &Value) -> Result<String, String> {
    let body = text.trim_end_matches('#');
    if !body.split_inclusive('\n').any(|l| l.starts_with("env_block=")) {
        return Ok(text.to_string());
    }
    let vars: Vec<(&str, &str)> = vars.as_object().into_iter().flatten().map(|(k, v)| (k.as_str(), v.as_str().unwrap_or_default())).collect();
    let mut out = String::new();
    let mut done: Vec<&str> = Vec::new();
    for l in body.split_inclusive('\n') {
        let name = l.split_once('=').map(|(k, _)| k);
        if name == Some("env_block") {
            continue;
        }
        match vars.iter().find(|(k, _)| Some(*k) == name) {
            Some((k, v)) => {
                out += &format!("{k}={v}\n");
                done.push(k);
            }
            None => out += l,
        }
    }
    for (k, v) in vars.iter().filter(|(k, _)| !done.contains(k)) {
        out += &format!("{k}={v}\n");
    }
    if out.len() > text.len() {
        return Err(format!("GRUB's environment block's variables don't fit in it ({} bytes, the file's {})", out.len(), text.len()));
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
                // Bytes, not a str slice: a title like `/Ubuntu 中文版` has
                // no char boundary at every key's length.  A match is ASCII,
                // so `cut` is a boundary.
                if t.as_bytes().get(..key.len()).is_some_and(|p| p.eq_ignore_ascii_case(key.as_bytes())) {
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

/// An ESP's GRUB stub (EFI/ID/grub.cfg: grub-install --uefi-secure-boot's
/// `search.fs_uuid UUID root`, `set prefix=($root)'/@root/boot/grub'`,
/// `configfile $prefix/grub.cfg`; Fedora's `search ... --fs-uuid
/// --set=dev UUID`, `set prefix=($dev)/root/boot/grub2`) that finds the
/// reformatted filesystem by UUID: each path on a GRUB device,
/// `(DEV)/PATH` or `(DEV)'PATH'`, through MAP (reformat::grub_path,
/// whose bool says btrfs_relative_path is set), so a path inside a
/// subvolume names where the new filesystem has it.  The signed GRUB
/// behind shim reads the stub and isn't installed again (AnduinOS's, the
/// grub2 distributions'), and Debian's grub-install remakes only its own
/// EFI/ID one, not the fallback EFI/BOOT copy.  A file not naming UUID
/// is left as it is.
pub fn grub_stub(text: &str, uuid: &str, map: impl Fn(&str, bool) -> Option<String>) -> String {
    if uuid.is_empty() || !text.to_ascii_lowercase().contains(&uuid.to_ascii_lowercase()) {
        return text.to_string();
    }
    let relative = text.lines().any(|l| {
        let t = l.trim();
        let t = t.strip_prefix("set ").map(str::trim_start).unwrap_or(t);
        t.strip_prefix("btrfs_relative_path=").is_some_and(|v| matches!(v.trim().trim_matches(['"', '\'']), "y" | "yes" | "1"))
    });
    text.split_inclusive('\n')
        .map(|l| {
            if l.trim_start().starts_with('#') {
                return l.to_string();
            }
            let mut out = String::new();
            let mut rest = l;
            while let Some(open) = rest.find('(') {
                let Some(close) = rest[open..].find(')').map(|c| open + c) else { break };
                if rest[open + 1..close].contains(char::is_whitespace) {
                    out += &rest[..=open];
                    rest = &rest[open + 1..];
                    continue;
                }
                out += &rest[..=close];
                rest = &rest[close + 1..];
                let quote = rest.chars().next().filter(|q| *q == '\'' || *q == '"');
                let body = &rest[quote.map_or(0, char::len_utf8)..];
                if !body.starts_with('/') {
                    continue;
                }
                let end = match quote {
                    Some(q) => body.find(q).unwrap_or(body.len()),
                    None => body.find(|ch: char| ch.is_whitespace() || matches!(ch, ';' | '\'' | '"' | '}' | ')')).unwrap_or(body.len()),
                };
                let path = &body[..end];
                if let Some(q) = quote {
                    out.push(q);
                }
                out += &map(path, relative).unwrap_or_else(|| path.to_string());
                rest = &body[end..];
            }
            out + rest
        })
        .collect()
}

/// The ESPs' GRUB stubs: each EFI/*/grub.cfg on a boot directory.
fn grub_stubs(run: &Run, layout: &Value) -> Vec<PathBuf> {
    let mut v: Vec<PathBuf> = boot_dirs(layout).iter().flat_map(|d| read_dir(&run.at(&format!("{d}/EFI")))).filter(|p| is_dir(p)).map(|p| p.join("grub.cfg")).filter(|p| is_file(p)).collect();
    v.sort();
    v.dedup();
    v
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

    /// PATH through F, written back and logged when F changed it; whether
    /// it did.  A file that isn't there is left alone.
    fn edit(&mut self, path: &Path, f: impl Fn(&str) -> Result<String, String>) -> bool {
        let Ok(old) = std::fs::read_to_string(path) else { return false };
        match f(&old) {
            Ok(new) if new != old => match std::fs::write(path, &new) {
                Ok(()) => {
                    self.log += &format!("bko-holt rewrite: {}:\n{}", path.display(), changed_lines(&old, &new));
                    return true;
                }
                Err(e) => self.errors.push(format!("{}: {e}", path.display())),
            },
            Ok(_) => {}
            Err(e) => self.errors.push(format!("{}: {e}", path.display())),
        }
        false
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
        // GRUB's environment block, on a btrfs only: the grubenv files on
        // this filesystem stop pointing at it, and keep its variables.  The
        // file decides, not the layout: a dump made before probe/btrfs.rs
        // read the block (79ff930) has no grub_env_block, and its grubenv's
        // env_block= still has to go (RustAudit1 XH1-01).
        if c.from == "btrfs" {
            let block = reformat::grub_env_block(layout, c);
            let vars = block.clone().unwrap_or_else(|| Value::Obj(Vec::new()));
            for d in boot_dirs(layout) {
                for g in ["grub2/grubenv", "grub/grubenv"] {
                    let p = format!("{d}/{g}");
                    if reformat::holds(layout, c, &p) {
                        let at = self.at(&p);
                        if self.edit(&at, |t| grubenv_unblock(t, &vars)) && block.is_none() {
                            self.log += &format!("bko-holt rewrite: {p}: the dump doesn't have the variables of GRUB's environment block (a dump made before bko-holt read it); env_block= taken out without them\n");
                        }
                    }
                }
            }
        }
        // The ESPs' GRUB stubs: their prefix out of the subvolume.
        if c.from == "btrfs" {
            for p in grub_stubs(self, layout) {
                self.edit(&p, |t| Ok(grub_stub(t, &c.uuid, |x, rel| reformat::grub_path(layout, c, x, rel))));
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

    /// An ESP's GRUB stub that still names a path inside the old btrfs's
    /// subvolumes (a form grub_stub doesn't know): its GRUB would stop at
    /// the grub> prompt.
    fn check_grub_stubs(&mut self, c: &Change, layout: &Value) {
        if c.from != "btrfs" {
            return;
        }
        for p in grub_stubs(self, layout) {
            let Ok(t) = std::fs::read_to_string(&p) else { continue };
            let new = grub_stub(&t, &c.uuid, |x, rel| reformat::grub_path(layout, c, x, rel));
            if new != t {
                let name = p.to_string_lossy().trim_start_matches(self.t).to_string();
                let lines: Vec<&str> = t.lines().filter(|l| !new.lines().any(|n| n == *l)).map(str::trim).collect();
                self.errors.push(format!("{name}: GRUB's stub still reads from inside the old btrfs's subvolumes: {}", lines.join("; ")));
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

    /// Whether a grub.cfg (beside FILE, or in a boot directory's grub or
    /// grub2) has an uncommented line naming NAME: GRUB would read FILE.
    fn grub_reads(&self, file: &Path, layout: &Value, name: &str) -> bool {
        let mut cfgs: Vec<PathBuf> = file.parent().map(|d| d.join("grub.cfg")).into_iter().collect();
        for d in boot_dirs(layout) {
            cfgs.push(self.at(&format!("{d}/grub/grub.cfg")));
            cfgs.push(self.at(&format!("{d}/grub2/grub.cfg")));
        }
        cfgs.iter().any(|g| std::fs::read_to_string(g).is_ok_and(|t| t.lines().any(|l| !l.trim_start().starts_with('#') && l.contains(name))))
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
                // grub-btrfs's snapshot menu: its 41_snapshots-btrfs stops on
                // a root that isn't btrfs, so grub-mkconfig wrote no submenu
                // reading it and the old file stays (BigLinux, Garuda).  Its
                // entries boot snapshots that weren't brought over; while no
                // grub.cfg reads it, it's a leftover, not a boot that fails.
                if c.mount == "/" && c.from == "btrfs" && p.file_name().is_some_and(|n| n == "grub-btrfs.cfg") && !self.grub_reads(&p, layout, "grub-btrfs.cfg") {
                    self.log += &format!("bko-holt rewrite: left over: {name}: grub-btrfs's snapshot menu for the old btrfs root, which no grub.cfg reads now\n");
                    continue;
                }
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
            run.check_grub_stubs(c, &layout);
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

    // Mount points and a spec with fstab(5)'s octal escapes: a space, a tab,
    // a newline, a backslash, an escape at the field's end, and a literal
    // "\040" (its backslash escaped).
    const ESCAPED_FSTAB: &str = "LABEL=My\\040Data /mnt/my\\040data btrfs subvol=/@data,noatime 0 0\n\
LABEL=My\\040Data /mnt/tab\\011dir btrfs subvol=/@tab 0 0\n\
LABEL=My\\040Data /mnt/new\\012line btrfs subvol=/@nl 0 0\n\
LABEL=My\\040Data /mnt/back\\134slash btrfs subvol=/@bs 0 0\n\
LABEL=My\\040Data /mnt/trailing\\040 btrfs subvol=/@tr 0 0\n\
LABEL=My\\040Data /mnt/lit\\134040 btrfs subvol=/@lit 0 0\n\
LABEL=My\\040Data /mnt/my\\040dat btrfs subvol=/@other 0 0\n\
/dev/sdb1 /mnt/my\\040data\\040too ext4 defaults 0 2\n";

    fn escaped_change() -> Change {
        Change {
            mount: "/mnt/my data".into(),
            folded: vec!["/mnt/tab\tdir".into(), "/mnt/new\nline".into(), "/mnt/back\\slash".into(), "/mnt/trailing ".into(), "/mnt/lit\\040".into()],
            ..change()
        }
    }

    #[test]
    fn fstab_with_escapes() {
        let c = escaped_change();
        let out = fstab(ESCAPED_FSTAB, &c).unwrap();
        // The kept mount's line and the untouched ones keep their own
        // escaped spelling: written back, each field decodes as it did.
        assert_eq!(
            out,
            "LABEL=My\\040Data\t/mnt/my\\040data\txfs\tnoatime\t0\t0\n\
LABEL=My\\040Data /mnt/my\\040dat btrfs subvol=/@other 0 0\n\
/dev/sdb1 /mnt/my\\040data\\040too ext4 defaults 0 2\n"
        );
        let e = crate::mounts::parse_fstab(&out);
        assert_eq!(e.iter().map(|e| (e.spec.as_str(), e.file.as_str())).collect::<Vec<_>>(), [("LABEL=My Data", "/mnt/my data"), ("LABEL=My Data", "/mnt/my dat"), ("/dev/sdb1", "/mnt/my data too")]);
        // Run again: the new type's line is found by its decoded mount point.
        assert_eq!(fstab(&out, &c).unwrap(), out);
        // A folded mount not in it, by its decoded name, is an error.
        let c2 = Change { folded: vec!["/mnt/lit 040".into()], ..escaped_change() };
        assert!(fstab(ESCAPED_FSTAB, &c2).unwrap_err().contains("no btrfs line for /mnt/lit 040"));
        // The kept mount's escape at the field's end.
        let c3 = Change { mount: "/mnt/trailing ".into(), folded: vec![], ..change() };
        let out3 = fstab("UUID=x /mnt/trailing\\040 btrfs subvol=@ 0 0\n", &c3).unwrap();
        assert_eq!(out3, "UUID=x\t/mnt/trailing\\040\txfs\tnoatime\t0\t0\n");
        assert_eq!(fstab(&out3, &c3).unwrap(), out3);
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
        // RustAudit1 H8-01: multi-byte characters across the keys' lengths
        // (版 at bytes 14..17 of the title, 中 at 7..10 of the comment).
        let l = "/Ubuntu 中文版\n# 12345中文 kernel_cmdline: x\n    protocol: linux\n    kernel_cmdline: quiet rootflags=subvol=@ rw\n";
        assert_eq!(limine(l, &c), "/Ubuntu 中文版\n# 12345中文 kernel_cmdline: x\n    protocol: linux\n    kernel_cmdline: quiet rw\n");
    }

    #[test]
    fn grubenv_keeps_its_size() {
        let mut env = "# GRUB Environment Block\nsaved_entry=x\nkernelopts=root=UUID=x ro rootflags=subvol=root rhgb quiet\n".to_string();
        env += &"#".repeat(1024 - env.len());
        let out = grubenv(&env, &change()).unwrap();
        assert_eq!(out.len(), 1024);
        assert!(out.starts_with("# GRUB Environment Block\nsaved_entry=x\nkernelopts=root=UUID=x ro rhgb quiet\n#"));
    }

    /// --reformat from a btrfs with GRUB's environment block (Leap 16's
    /// env_block=512+1): grubenv loses env_block= and gets the block's
    /// variables, still 1024 bytes; without env_block= it's left alone.
    #[test]
    fn grubenv_unblock_moves_the_block_in() {
        let mut env = "# GRUB Environment Block\nsaved_entry=x\nnext_entry=0\nenv_block=512+1\n".to_string();
        env += &"#".repeat(1024 - env.len());
        let vars = crate::json::parse(r#"{"next_entry": "2", "health": "a=b"}"#).unwrap();
        let out = grubenv_unblock(&env, &vars).unwrap();
        assert_eq!(out.len(), 1024);
        assert!(out.starts_with("# GRUB Environment Block\nsaved_entry=x\nnext_entry=2\nhealth=a=b\n#"), "{out}");
        assert_eq!(grubenv_unblock(&out, &vars).unwrap(), out);
        let empty = grubenv_unblock(&env, &crate::json::parse("{}").unwrap()).unwrap();
        assert!(empty.starts_with("# GRUB Environment Block\nsaved_entry=x\nnext_entry=0\n#"), "{empty}");
        let big = crate::json::parse(&format!(r#"{{"x": "{}"}}"#, "y".repeat(1100))).unwrap();
        assert!(grubenv_unblock(&env, &big).unwrap_err().contains("don't fit"));
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

    const ANDUINOS_UUID: &str = "6952b714-eb5b-4747-a32b-95b6fa1f800a";

    /// The GRUB stubs' forms: grub-install --uefi-secure-boot's (Debian,
    /// Ubuntu, AnduinOS), Fedora's grub2, openSUSE's relative one.
    #[test]
    fn grub_stubs_out_of_the_subvolume() {
        let map = |p: &str, rel: bool| match (p, rel) {
            ("/@root/boot/grub", false) => Some("/boot/grub".to_string()),
            ("/@root/boot/grub/grub.cfg", false) => Some("/boot/grub/grub.cfg".to_string()),
            ("/root/boot/grub2", false) => Some("/boot/grub2".to_string()),
            ("/boot/grub2", true) => Some("/relative".to_string()),
            _ => None,
        };
        let u = ANDUINOS_UUID;
        let anduinos = format!("search.fs_uuid {u} root \nset prefix=($root)'/@root/boot/grub'\nconfigfile $prefix/grub.cfg\n");
        assert_eq!(grub_stub(&anduinos, u, map), format!("search.fs_uuid {u} root \nset prefix=($root)'/boot/grub'\nconfigfile $prefix/grub.cfg\n"));
        // The UUID in capitals, a hint, a direct configfile, a comment.
        let other = format!("search.fs_uuid {} root hd0,gpt4\n# set prefix=($root)'/@root/boot/grub'\nconfigfile (${{root}})/@root/boot/grub/grub.cfg\n", u.to_uppercase());
        assert_eq!(grub_stub(&other, u, map), format!("search.fs_uuid {} root hd0,gpt4\n# set prefix=($root)'/@root/boot/grub'\nconfigfile (${{root}})/boot/grub/grub.cfg\n", u.to_uppercase()));
        let fedora = format!("search --no-floppy --root-dev-only --fs-uuid --set=dev {u}\nset prefix=($dev)/root/boot/grub2\nexport $prefix\nconfigfile $prefix/grub.cfg\n");
        assert_eq!(grub_stub(&fedora, u, map), fedora.replace("($dev)/root/boot/grub2", "($dev)/boot/grub2"));
        let suse = format!("set btrfs_relative_path=\"yes\"\nsearch --fs-uuid --set=root {u}\nset prefix=(${{root}})/boot/grub2\nsource \"${{prefix}}/grub.cfg\"\n");
        assert_eq!(grub_stub(&suse, u, map), suse.replace("(${root})/boot/grub2", "(${root})/relative"));
        // Another filesystem's stub, or the ESP's own paths: as they were.
        assert_eq!(grub_stub(&anduinos.replace(u, "34CD-367E"), u, map), anduinos.replace(u, "34CD-367E"));
        let esp = format!("search.fs_uuid {u} root\nchainloader ($root)/EFI/x.efi (hd0) ( a b )\n");
        assert_eq!(grub_stub(&esp, u, map), esp);
        assert_eq!(grub_stub(&anduinos, "", map), anduinos);
    }

    /// AnduinOS's / onto ext4 (HoltTest1 HT_REFORMAT=/=ext4): both ESP stubs
    /// read GRUB's directory from the old @root, then from the new root.
    #[test]
    fn anduinos_esp_stubs() {
        anduinos_stubs(None);
    }

    /// The same with the top level mounted too (subvolid=5 at /mnt/top,
    /// btrbk's; subvol=/ at /.btrfsroot, snapper-rollback's): a path not
    /// in a subvolume (/boot/grub after the rewrite, the ESP's own) isn't
    /// the old btrfs's, so the check passes and a second rewrite leaves it.
    #[test]
    fn anduinos_esp_stubs_top_level_mounted() {
        anduinos_stubs(Some(("/mnt/top", "subvolid=5,noatime")));
        anduinos_stubs(Some(("/.btrfsroot", "subvol=/,noatime")));
    }

    fn anduinos_stubs(top: Option<(&str, &str)>) {
        let t = crate::testutil::TempDir::new("rewrite-stub");
        let root = t.path().to_string_lossy().into_owned();
        let w = |p: &str, s: &str| {
            let f = format!("{root}{p}");
            std::fs::create_dir_all(Path::new(&f).parent().unwrap()).unwrap();
            std::fs::write(f, s).unwrap();
        };
        let u = ANDUINOS_UUID;
        let mut fstab = format!("UUID={u} / btrfs defaults,subvol=@root,compress=zstd:3,noatime 0 0\nUUID={u} /home btrfs defaults,subvol=@home,compress=zstd:3,noatime 0 0\nUUID=34CD-367E /boot/efi vfat defaults,noatime 0 1\n");
        let mut extra = String::new();
        if let Some((at, o)) = top {
            fstab += &format!("UUID={u} {at} btrfs {o} 0 0\n");
            extra = format!(r#", {{"file": "{at}", "type": "btrfs", "options": "{o}", "resolves_to": "vda4"}}"#);
        }
        let layout = crate::json::parse(&format!(
            r#"{{"os": {{"id": "anduinos"}},
  "boot": {{"esps": [{{"device": "vda2", "mounted_at": "/boot/efi"}}], "kernels": {{"installed": [{{"version": "7.0.0-43-generic"}}]}}, "packages": {{"e2fsprogs": "1.47.2-3"}}}},
  "storage": {{"disks": [{{"name": "vda", "partitions": [{{"name": "vda2", "content": {{"type": "vfat", "uuid": "34CD-367E"}}}}, {{"name": "vda4", "content": {{"type": "btrfs", "uuid": "{u}"}}}}]}}],
    "btrfs": [{{"uuid": "{u}", "devices": ["vda4"], "default_subvolume": {{"id": 5}}, "subvolumes": [{{"id": 256, "path": "<FS_TREE>/@root"}}, {{"id": 257, "path": "<FS_TREE>/@home"}}]}}],
    "fstab": [{{"file": "/", "type": "btrfs", "options": "defaults,subvol=@root,compress=zstd:3,noatime", "resolves_to": "vda4"}},
              {{"file": "/home", "type": "btrfs", "options": "defaults,subvol=@home,compress=zstd:3,noatime", "resolves_to": "vda4"}},
              {{"file": "/boot/efi", "type": "vfat", "options": "defaults,noatime", "resolves_to": "vda2"}}{extra}]}}}}"#
        ))
        .unwrap();
        let r = reformat::work_out(&layout, &[("/".to_string(), "ext4".to_string())]);
        assert!(r.problems.is_empty(), "{:?}", r.problems);
        let c = &r.changes[0];
        let stub = format!("search.fs_uuid {u} root \nset prefix=($root)'/@root/boot/grub'\nconfigfile $prefix/grub.cfg\n");
        w("/etc/fstab", &fstab);
        w("/boot/efi/EFI/AnduinOS/grub.cfg", &stub);
        w("/boot/efi/EFI/BOOT/grub.cfg", &stub);
        w("/boot/efi/EFI/BOOT/BOOTX64.CSV", "shimx64.efi,AnduinOS,,\n");
        let mut run = Run { t: &root, log: String::new(), errors: vec![] };
        // Before the rewrite, the check stops on both.
        run.check_grub_stubs(c, &layout);
        assert_eq!(run.errors.len(), 2, "{:?}", run.errors);
        assert!(run.errors[0].starts_with("/boot/efi/EFI/AnduinOS/grub.cfg: GRUB's stub still reads from inside the old btrfs's subvolumes: set prefix=($root)'/@root/boot/grub'"), "{:?}", run.errors);
        run.errors.clear();
        run.rewrite(c, &layout, false);
        assert!(run.errors.is_empty(), "{:?}", run.errors);
        let want = stub.replace("'/@root/boot/grub'", "'/boot/grub'");
        for d in ["AnduinOS", "BOOT"] {
            assert_eq!(std::fs::read_to_string(format!("{root}/boot/efi/EFI/{d}/grub.cfg")).unwrap(), want);
        }
        assert!(run.log.contains("/boot/efi/EFI/BOOT/grub.cfg:\n    - set prefix=($root)'/@root/boot/grub'\n    + set prefix=($root)'/boot/grub'\n"), "{}", run.log);
        run.check_grub_stubs(c, &layout);
        assert!(run.errors.is_empty(), "{:?}", run.errors);
        // Run again: nothing left to change.
        run.log.clear();
        run.rewrite(c, &layout, false);
        assert!(run.errors.is_empty() && run.log.is_empty(), "{:?} {}", run.errors, run.log);
        for d in ["AnduinOS", "BOOT"] {
            assert_eq!(std::fs::read_to_string(format!("{root}/boot/efi/EFI/{d}/grub.cfg")).unwrap(), want);
        }
        // A stub grub-install wrote fresh (the new filesystem's paths), and
        // one with the ESP's own: the check passes, the rewrite leaves them.
        let fresh = format!("search.fs_uuid {u} root hd0,gpt4\nset prefix=($root)'/boot/grub'\nconfigfile ($root)/boot/grub/grub.cfg\nchainloader ($esp)/EFI/BOOT/fbx64.efi\n");
        w("/boot/efi/EFI/AnduinOS/grub.cfg", &fresh);
        run.check_grub_stubs(c, &layout);
        assert!(run.errors.is_empty(), "{:?}", run.errors);
        run.rewrite(c, &layout, false);
        assert!(run.errors.is_empty() && run.log.is_empty(), "{:?} {}", run.errors, run.log);
        assert_eq!(std::fs::read_to_string(format!("{root}/boot/efi/EFI/AnduinOS/grub.cfg")).unwrap(), fresh);
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
        // GRUB's environment block (a btrfs Leap-style GRUB used): the
        // grubenv on the reformatted / stops pointing at it.
        let mut env = "# GRUB Environment Block\nsaved_entry=x\nenv_block=512+1\n".to_string();
        env += &"#".repeat(1024 - env.len());
        w("/boot/grub2/grubenv", &env);
        let with_block = crate::json::parse(
            r#"{"boot": {"esps": [{"mounted_at": "/efi"}]},
  "storage": {"disks": [{"name": "nvme0n1", "partitions": [{"name": "nvme0n1p2", "content": {"type": "btrfs", "grub_env_block": {"next_entry": "1"}}}]}],
              "fstab": [{"file": "/efi"}, {"file": "/"}, {"file": "/home"}]}}"#,
        )
        .unwrap();
        run.log.clear();
        run.rewrite(&change(), &with_block, false);
        assert!(run.errors.is_empty(), "{:?}", run.errors);
        let g = std::fs::read_to_string(format!("{root}/boot/grub2/grubenv")).unwrap();
        assert!(g.len() == 1024 && g.starts_with("# GRUB Environment Block\nsaved_entry=x\nnext_entry=1\n#"), "{g}");
        assert!(run.log.contains("    - env_block=512+1\n") && run.log.contains("    + next_entry=1\n"), "{}", run.log);
        // RustAudit1 XH1-01: a dump from before the probe read the block
        // has no grub_env_block, and env_block= still goes, with a log line.
        let no_block = crate::json::parse(
            r#"{"boot": {"esps": [{"mounted_at": "/efi"}]},
  "storage": {"disks": [{"name": "nvme0n1", "partitions": [{"name": "nvme0n1p2", "content": {"type": "btrfs"}}]}],
              "fstab": [{"file": "/efi"}, {"file": "/"}, {"file": "/home"}]}}"#,
        )
        .unwrap();
        w("/boot/grub2/grubenv", &env);
        run.log.clear();
        run.rewrite(&change(), &no_block, false);
        assert!(run.errors.is_empty(), "{:?}", run.errors);
        let g = std::fs::read_to_string(format!("{root}/boot/grub2/grubenv")).unwrap();
        assert!(g.len() == 1024 && g.starts_with("# GRUB Environment Block\nsaved_entry=x\n#") && !g.contains("env_block="), "{g}");
        assert!(run.log.contains("    - env_block=512+1\n") && run.log.contains("/boot/grub2/grubenv: the dump doesn't have the variables"), "{}", run.log);
        run.log.clear();
        run.rewrite(&change(), &no_block, false);
        assert!(run.errors.is_empty() && run.log.is_empty(), "{:?} {}", run.errors, run.log);
        // grub-btrfs's old snapshot menu (BigLinux): no grub.cfg reads it
        // after grub-mkconfig on the new root, so it's a leftover; while a
        // grub.cfg still sources it, its subvolume lines fail the check.
        let snap = "submenu 'x' {\n  menuentry 'snap' {\n    linux \"/timeshift-btrfs/snapshots/1/@/boot/vmlinuz\" root=UUID=x rootflags=subvol=\"timeshift-btrfs/snapshots/1/@\"\n  }\n}\n";
        w("/boot/grub/grub-btrfs.cfg", snap);
        w("/boot/grub/grub.cfg", "### BEGIN /etc/grub.d/41_snapshots-btrfs ###\n# configfile ${prefix}/grub-btrfs.cfg\n### END /etc/grub.d/41_snapshots-btrfs ###\n");
        run.log.clear();
        run.leftovers(&change(), &layout);
        assert!(run.errors.is_empty(), "{:?}", run.errors);
        assert!(run.log.contains("left over: /boot/grub/grub-btrfs.cfg: grub-btrfs's snapshot menu"), "{}", run.log);
        w("/boot/grub/grub.cfg", "submenu 'snapshots' {\n    configfile \"${prefix}/grub-btrfs.cfg\"\n}\n");
        run.leftovers(&change(), &layout);
        assert!(run.errors.iter().any(|e| e.contains("grub-btrfs.cfg:3: a kernel command line still asks for a subvolume")), "{:?}", run.errors);
        std::fs::remove_file(format!("{root}/boot/grub/grub-btrfs.cfg")).unwrap();
        run.errors.clear();
        // An entry the rewrite missed fails the check.
        w("/efi/loader/entries/other.conf", "options root=UUID=x rootflags=subvol=@\n");
        run.leftovers(&change(), &layout);
        assert!(run.errors.iter().any(|e| e.contains("other.conf:1: a kernel command line still asks for a subvolume")), "{:?}", run.errors);
    }
}
