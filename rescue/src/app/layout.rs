//! The layout file every `bko-holt load` of this session reads: the chosen
//! machine's system.json, copied off the stick's config partition into the
//! session's directory (0600), once.
//!
//! holt-json.md's open question 8 is unanswered (how bko-rescue gets the
//! layout, and how system.json carries the dump): this is its second
//! option, the one that needs nothing new in bko-holt.  The partition is
//! mounted read-only, the file copied (at most `LAYOUT_MAX`, the cap
//! `bko-holt stick` reads it with), the partition unmounted.  bko-holt
//! checked its checksum when `stick --json` ran; nothing here checks it
//! again (no SHA-256 in bko-rescue, D2).  A system.json that isn't a
//! layout load takes is bko-holt's to refuse (`layout_refused`), and the
//! Disks screen says so.  A typed location has no system.json on the
//! stick: the backup's own copy waits on the backup tool (rescue-screens.md,
//! "4. Snapshot"), so then there is no layout, and a screen says so.

use std::ffi::OsString;
use std::fs;
use std::path::{Path, PathBuf};

use crate::term::profile::Os;
use crate::text::{cap, Clean};

use super::sys::{self, Cmd};
use super::{wait, write_new, App};

/// How the layout is had (open question 8): `ConfigReadOnly` is the
/// stick's copy through a read-only mount; `HoltCopy` would be `bko-holt
/// stick --copy ID OUT --json`, which isn't built.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum From {
    ConfigReadOnly,
}

pub const FROM: From = From::ConfigReadOnly;

/// The most of a system.json read (holt's `stick.rs` reads 64 MiB).
pub const LAYOUT_MAX: usize = 64 * 1024 * 1024;

/// Its name in the session's directory.
pub const NAME: &str = "layout.json";

/// Each platform's read-only mount of a FAT partition, and its unmount
/// (*Check* on each image at RT13, as `report::mount_argv`).
pub fn mount_ro_argv(os: Os, dev: &str, dir: &Path) -> (Vec<String>, Vec<String>) {
    let d = dir.display().to_string();
    let mount: Vec<&str> = match os {
        Os::Linux => vec!["mount", "-t", "vfat", "-o", "ro,nosuid,nodev,noexec", dev, &d],
        Os::FreeBsd | Os::DragonFly => vec!["mount_msdosfs", "-o", "ro", dev, &d],
        Os::NetBsd | Os::OpenBsd => vec!["mount_msdos", "-o", "ro", dev, &d],
        Os::Illumos => vec!["mount", "-F", "pcfs", "-o", "ro", dev, &d],
        Os::Haiku => vec!["mount", "-ro", "-t", "fat", dev, &d],
    };
    let umount = match os {
        Os::Haiku => vec!["unmount", d.as_str()],
        _ => vec!["umount", d.as_str()],
    };
    (mount.into_iter().map(str::to_string).collect(), umount.into_iter().map(str::to_string).collect())
}

/// A machine's path as bko-holt named it: `backotter/machines/UUID/system.json`
/// and nothing else, so no `..` or link can lead elsewhere.
pub fn safe_path(p: &str) -> bool {
    let parts: Vec<&str> = p.split('/').collect();
    parts.len() == 4
        && parts[0] == "backotter"
        && parts[1] == "machines"
        && parts[3] == "system.json"
        && parts[2].len() == 36
        && parts[2].bytes().all(|b| b.is_ascii_hexdigit() || b == b'-')
}

/// A device path bko-holt named, as a mount takes it.
fn plain_dev(d: &str) -> bool {
    d.starts_with("/dev/") && !d.contains("..") && d.bytes().all(|b| b.is_ascii_alphanumeric() || matches!(b, b'/' | b'_' | b'-' | b'.' | b','))
}

impl App {
    /// The layout file: copied the first time, then the same file.  Err
    /// is why there is none, in words.
    pub(crate) fn layout_file(&mut self) -> Result<PathBuf, String> {
        if let Some(p) = &self.layout {
            if p.is_file() {
                return Ok(p.clone());
            }
        }
        let Some(m) = self.chosen().cloned() else { return Err(super::strings::LAYOUT_NONE_TYPED.to_string()) };
        let path = m.path.clone().filter(|p| safe_path(p)).ok_or_else(|| super::strings::LAYOUT_NO_PATH.to_string())?;
        let dev = self.stick.as_ref().and_then(|st| st.dev.clone()).filter(|d| plain_dev(d)).ok_or_else(|| super::strings::LAYOUT_NO_DEV.to_string())?;
        match FROM {
            From::ConfigReadOnly => {}
        }
        let mnt = self.session.path("config");
        sys::own_dir(&mnt, None).map_err(|e| e.to_string())?;
        let (mount, umount) = mount_ro_argv(self.wh.os, &dev, &mnt);
        let run = |app: &mut App, argv: &[String]| -> Result<(), String> {
            let Some(prog) = app.sys.which(&argv[0]) else { return Err(format!("{} isn't here", argv[0])) };
            let mut c = Cmd::new(&prog, argv[1..].iter().map(OsString::from).collect());
            c.timeout_ms = 60_000;
            let out = wait(app.sys.start(c), &mut *app.sys);
            app.raw = super::raw_lines(&out);
            if out.code == Some(0) {
                Ok(())
            } else {
                Err(Clean::line(out.stderr_text().lines().next().unwrap_or("it failed"), cap::LOG_LINE).as_str().to_string())
            }
        };
        if let Err(e) = run(self, &mount) {
            let _ = fs::remove_dir(&mnt);
            return Err(super::strings::layout_mount_failed(&e));
        }
        let bytes = self.sys.read(&mnt.join(&path), LAYOUT_MAX);
        let un = run(self, &umount);
        let _ = fs::remove_dir(&mnt);
        let bytes = bytes.ok_or_else(|| super::strings::LAYOUT_UNREADABLE.to_string())?;
        un.map_err(|e| super::strings::layout_mount_failed(&e))?;
        let out = self.session.path(NAME);
        sys::wipe_file(&out);
        write_new(&out, &bytes).map_err(|e| e.to_string())?;
        self.layout = Some(out.clone());
        Ok(out)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn only_bko_holts_own_path_is_taken() {
        assert!(safe_path("backotter/machines/0b7e3c52-8d1f-4a6e-9c3b-2f5d7a1e4b90/system.json"));
        for bad in [
            "backotter/machines/../../etc/system.json",
            "/backotter/machines/0b7e3c52-8d1f-4a6e-9c3b-2f5d7a1e4b90/system.json",
            "backotter/machines/0b7e3c52-8d1f-4a6e-9c3b-2f5d7a1e4b90/../system.json",
            "backotter/machines/x/system.json",
            "backotter/machines/0b7e3c52-8d1f-4a6e-9c3b-2f5d7a1e4b90/other.json",
        ] {
            assert!(!safe_path(bad), "{bad}");
        }
        let (m, u) = mount_ro_argv(Os::Linux, "/dev/sdc3", Path::new("/run/x"));
        assert!(m.join(" ").contains("-o ro,"), "read-only: {m:?}");
        assert_eq!(u, vec!["umount", "/run/x"]);
        for os in [Os::FreeBsd, Os::NetBsd, Os::OpenBsd, Os::DragonFly, Os::Illumos, Os::Haiku] {
            assert!(mount_ro_argv(os, "/dev/x", Path::new("/m")).0.iter().any(|a| a.contains("ro")), "{os:?}");
        }
    }
}
