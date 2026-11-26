//! The layout file every `bko-holt load` of this session reads: the disk
//! layout the chosen snapshot's own system.json carries, else the one the
//! chosen machine's system.json on the stick carries, written once a
//! snapshot into the session's directory (0700).
//!
//! First the snapshot's own (holt-json.md's open question 15, approved
//! 2026-11-26; stick-config.md's "Which copy wins", step 2): once the
//! password is in and the snapshot chosen, `bko-holt snapshot-layout OUT
//! --repo URL --password-file FILE --snapshot ID [--install-id ID] --json`
//! reads `/.bko-holt/system.json` from that snapshot, checks it as `stick
//! --copy` checks the stick's (and that it is the chosen machine's), and
//! writes its `layout` to OUT.  The stick's copy is some backup's (the
//! newest one the stick was plugged in for), so only the snapshot's own is
//! the layout of the backup restored.  A snapshot that holds none (a backup
//! from before the backup tool kept one, bko-holt's `no_system_json`) falls
//! back to the stick's copy, as before; any other failure is a problem
//! screen, never that fallback.  A bko-holt whose api document doesn't list
//! `snapshot_layout` isn't asked: the stick's copy, as before.
//!
//! The stick's copy: `bko-holt stick --copy INSTALL_ID OUT --json`
//! (holt-json.md's open question 8, approved 2026-11-26).
//!
//! bko-holt finds the config partition as `stick --json` did, checks the
//! machine's system.json against its `.sha256`, and writes the dump it
//! carries (its `layout`) to OUT, a new 0600 file: parsed from the bytes it
//! checked, with no second read, and printed as dump prints it.  So
//! bko-rescue mounts nothing to have it and reads no FAT (D11), and what
//! load reads is the JSON whose checksum was checked.  bko-holt's
//! refusal (no layout in system.json, a checksum gone bad since, a layout
//! load won't take) is shown in its words on the Disks screen.  A typed
//! location has no system.json on the stick: only the snapshot's own then,
//! asked with no install ID, and without it a screen says there is none.
//!
//! A layout that kept LUKS2 headers (`dump --keep-luks-header`, Ubuntu
//! Core's encryption always: the copy document's `luks_headers` counts
//! them) needs their files, which the backup holds (holt-json.md's open
//! question 12, approved 2026-11-26).  Once the password is in and the
//! snapshot chosen, before the first plan, `bko-holt luks-headers LAYOUT
//! DIR --repo URL --password-file FILE --snapshot ID --json` fetches them
//! into DIR, `h` in the session's directory: bko-holt makes it 0700, each
//! header 0600, checked against the dump's size and SHA-256 (the layout's:
//! the snapshot's own, so its headers match).  Every load then gets
//! `--luks-header-dir DIR`.  Another snapshot finds its layout and fetches
//! them again; the session's end wipes them with the rest (key material).

use std::path::{Path, PathBuf};

use super::holt_args::HoltCmd;
use super::strings as s;
use super::sys;
use super::{document, raw_lines, same_version, wait, App, Go, Problem};
use crate::text::{cap, Clean};
use crate::ui::Ui;

/// Its name in the session's directory.
pub const NAME: &str = "layout.json";

/// The `bko-holt-stick-copy` schema read.
pub const COPY_SCHEMA: u64 = 1;

/// The headers' directory's name in the session's directory.
pub const HEADERS_DIR: &str = "h";

/// The `bko-holt-luks-headers` schema read.
pub const HEADERS_SCHEMA: u64 = 1;

/// The `bko-holt-snapshot-layout` schema read.
pub const SNAP_SCHEMA: u64 = 1;

/// How long reading a snapshot's system.json may take: one file, a dump
/// inside, over the network.
pub const SNAP_MS: u64 = 300_000;

/// How long a fetch may take: two Ubuntu Core headers are 13 MiB, a big
/// keyslots area 128 MiB each, over the network.
pub const HEADERS_MS: u64 = 600_000;

/// Whether the document V names COUNT headers, each a plain file in DIR.
fn headers_there(v: &crate::json::Value, dir: &Path, count: u64) -> bool {
    let rows = v.get_arr("headers").map(Vec::as_slice).unwrap_or(&[]);
    rows.len() as u64 == count
        && rows.iter().all(|r| {
            let file = r.get_str("file").unwrap_or("");
            let plain = !file.is_empty() && !file.starts_with('.') && !file.contains('/');
            let p = dir.join(file);
            plain && r.get_str("path") == p.to_str() && std::fs::symlink_metadata(&p).is_ok_and(|m| m.is_file() && Some(m.len()) == r.get_u64("bytes"))
        })
}

/// An install ID as bko-holt names a machine: a UUID's shape, so nothing
/// else ever reaches its argument list.
pub fn install_id_ok(id: &str) -> bool {
    let b = id.as_bytes();
    b.len() == 36 && b.iter().enumerate().all(|(i, &c)| if matches!(i, 8 | 13 | 18 | 23) { c == b'-' } else { c.is_ascii_hexdigit() })
}

/// Why there is no layout file: the words, and whether bko-holt said the
/// machine's system.json carries none (`no_layout`: none written, or left
/// out by the backup tool for the stick's room, holt-json.md's open
/// question 16), which the Disks screen answers with its own words.
#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) struct NoLayout {
    pub what: String,
    pub none_on_stick: bool,
    /// The snapshot was asked for its own, and holds none (`no_system_json`).
    pub none_in_backup: bool,
}

impl NoLayout {
    fn said(what: &str) -> NoLayout {
        NoLayout { what: what.to_string(), none_on_stick: false, none_in_backup: false }
    }
}

/// What the snapshot said of its own layout.
enum Own {
    /// Written: the layout file.
    Written(PathBuf),
    /// It holds none (`no_system_json`).
    NoneThere,
    /// Not asked: a bko-holt that can't, or nothing to ask with.
    NotAsked,
}

/// Why the layout file isn't there: no layout (a screen of its own), or
/// where to go, its problem screen shown.
#[derive(Debug)]
pub(crate) enum LayoutErr {
    None(NoLayout),
    Go(Go),
}

/// Whether a run's standard output is bko-holt's refusal CODE.
fn is_refusal(x: &sys::Output, code: &str) -> bool {
    let v = std::str::from_utf8(&x.stdout).ok().and_then(|t| crate::json::parse(t.trim()).ok());
    v.is_some_and(|v| v.get_str("format") == Some("bko-holt-error") && v.get_str("code") == Some(code))
}

impl App {
    /// The layout file and its headers' count gone, the file zeroed first.
    pub(crate) fn forget_layout(&mut self) {
        if let Some(p) = self.layout.take() {
            sys::wipe_file(&p);
        }
        self.layout_headers = 0;
        self.layout_for = None;
        self.layout_own = false;
    }

    /// The layout file for the chosen snapshot: found the first time, then
    /// the same file.  Err is why there is none.
    pub(crate) fn layout_file(&mut self, ui: &mut dyn Ui) -> Result<PathBuf, LayoutErr> {
        if let Some(p) = &self.layout {
            if p.is_file() && self.layout_for == self.snap {
                return Ok(p.clone());
            }
        }
        self.forget_headers();
        self.forget_layout();
        // The snapshot's own first, then the stick's copy.
        let asked = match self.snapshot_layout(ui).map_err(LayoutErr::Go)? {
            Own::Written(p) => return Ok(p),
            Own::NoneThere => true,
            Own::NotAsked => false,
        };
        self.stick_layout(asked).map_err(LayoutErr::None)
    }

    /// The chosen snapshot's own layout (open question 15).  Err is where
    /// to go, its problem screen shown.
    fn snapshot_layout(&mut self, ui: &mut dyn Ui) -> Result<Own, Go> {
        let (Some(t), Some(pw), Some(snap)) = (self.target(), self.password.clone(), self.snap.clone()) else { return Ok(Own::NotAsked) };
        if !self.snapshot_layouts {
            return Ok(Own::NotAsked);
        }
        let id = self.chosen().map(|m| m.install_id.clone());
        if id.as_deref().is_some_and(|i| !install_id_ok(i)) {
            return Err(self.snapshot_layout_failed(ui, s::LAYOUT_NO_ID));
        }
        let out = self.session.path(NAME);
        sys::wipe_file(&out);
        let Some(mut cmd) = self.holt_cmd(&HoltCmd::SnapshotLayout { out: &out, repo: &t.place.url, password_file: &pw, snapshot: &snap, install_id: id.as_deref() }) else {
            return Ok(Own::NotAsked);
        };
        cmd.timeout_ms = SNAP_MS;
        let x = match self.run_job(ui, s::SCR_DISKS, s::SNAP_LAYOUT_TITLE, Clean::ours(s::SNAP_LAYOUT_TEXT), cmd) {
            Ok(x) => x,
            Err(g) => {
                sys::wipe_file(&out);
                return Err(g);
            }
        };
        let version = self.version.clone();
        let doc = document(&x, "bko-holt-snapshot-layout").and_then(|v| same_version(v, SNAP_SCHEMA, version.as_deref()));
        let size = std::fs::symlink_metadata(&out).ok().filter(|f| f.is_file()).map(|f| f.len());
        match doc {
            Ok(v) if x.code == Some(0)
                && v.get_str("snapshot") == Some(snap.as_str())
                && id.as_deref().is_none_or(|i| v.get_str("install_id").is_some_and(|g| g.eq_ignore_ascii_case(i)))
                && size.is_some()
                && v.get_u64("bytes") == size =>
            {
                self.layout = Some(out.clone());
                self.layout_headers = v.get_u64("luks_headers").unwrap_or(0);
                self.layout_for = Some(snap);
                self.layout_own = true;
                Ok(Own::Written(out))
            }
            Ok(_) => {
                sys::wipe_file(&out);
                Err(self.snapshot_layout_failed(ui, s::SNAP_LAYOUT_NOT_WRITTEN))
            }
            Err(_) if is_refusal(&x, "no_system_json") => {
                // A backup from before the backup tool kept one: the
                // stick's copy, as before.
                sys::wipe_file(&out);
                Ok(Own::NoneThere)
            }
            Err(why) => {
                sys::wipe_file(&out);
                let why = why.strip_prefix("bko-holt snapshot-layout: ").unwrap_or(&why).to_string();
                Err(self.snapshot_layout_failed(ui, &why))
            }
        }
    }

    /// The snapshot's layout couldn't be had: why, Try again, Back, Report.
    fn snapshot_layout_failed(&mut self, ui: &mut dyn Ui, why: &str) -> Go {
        let p = Problem {
            code: "snapshot_layout_failed",
            screen: s::SCR_DISKS,
            title: s::SNAP_LAYOUT_FAILED_TITLE.to_string(),
            what: s::snap_layout_failed(Clean::line(why, cap::PROBLEM).as_str()),
            why: s::SNAP_LAYOUT_FAILED_WHY.to_string(),
            todo: s::SNAP_LAYOUT_FAILED_TODO.to_string(),
            fixes: Vec::new(),
            again: true,
            back: Some(Go::Back),
        };
        self.problem(ui, p)
    }

    /// The stick's copy of the chosen machine's layout; ASKED: the
    /// snapshot was asked for its own first, and holds none.  Err is why
    /// there is none.
    fn stick_layout(&mut self, asked: bool) -> Result<PathBuf, NoLayout> {
        let r = self.stick_copy(asked);
        r.map_err(|mut n| {
            n.none_in_backup = asked;
            n
        })
    }

    fn stick_copy(&mut self, asked: bool) -> Result<PathBuf, NoLayout> {
        let Some(m) = self.chosen().cloned() else {
            return Err(NoLayout::said(if asked { s::LAYOUT_NONE_TYPED_IN_BACKUP } else { s::LAYOUT_NONE_TYPED }));
        };
        if !install_id_ok(&m.install_id) {
            return Err(NoLayout::said(super::strings::LAYOUT_NO_ID));
        }
        let out = self.session.path(NAME);
        sys::wipe_file(&out);
        let Some(cmd) = self.holt_cmd(&HoltCmd::StickCopy { install_id: &m.install_id, out: &out }) else {
            return Err(NoLayout::said(super::strings::LAYOUT_NO_HOLT));
        };
        let x = wait(self.sys.start(cmd), &mut *self.sys);
        self.raw = raw_lines(&x);
        let version = self.version.clone();
        let doc = document(&x, "bko-holt-stick-copy").and_then(|v| same_version(v, COPY_SCHEMA, version.as_deref()));
        let size = std::fs::symlink_metadata(&out).ok().filter(|f| f.is_file()).map(|f| f.len());
        match doc {
            // bko-holt's word for it, and the file it says it wrote.
            Ok(v) if x.code == Some(0) && v.get_str("install_id").is_some_and(|i| i.eq_ignore_ascii_case(&m.install_id)) && size.is_some() && v.get_u64("bytes") == size => {
                self.layout = Some(out.clone());
                // A bko-holt from before open question 12 says none: it
                // refused such a layout.
                self.layout_headers = v.get_u64("luks_headers").unwrap_or(0);
                self.layout_for = self.snap.clone();
                self.layout_own = false;
                Ok(out)
            }
            Ok(_) => {
                sys::wipe_file(&out);
                Err(NoLayout::said(super::strings::LAYOUT_NOT_WRITTEN))
            }
            Err(why) => {
                sys::wipe_file(&out);
                // bko-holt's line names itself; the words before it do too.
                let why = why.strip_prefix("bko-holt stick --copy: ").unwrap_or(&why);
                Err(NoLayout { what: super::strings::layout_refused(why), none_on_stick: is_refusal(&x, "no_layout"), none_in_backup: false })
            }
        }
    }
}

impl App {
    /// The headers' directory gone, its files zeroed first.
    pub(crate) fn forget_headers(&mut self) {
        if let Some((d, _)) = self.headers.take() {
            sys::wipe_dir(&d);
        }
    }

    /// The directory of the LUKS2 headers the layout kept, fetched from
    /// the chosen snapshot the first time (None: it kept none).  Err is
    /// where to go, its problem screen shown.
    pub(crate) fn header_dir(&mut self, ui: &mut dyn Ui) -> Result<Option<PathBuf>, Go> {
        if self.layout_headers == 0 {
            return Ok(None);
        }
        if let Some((d, from)) = &self.headers {
            if *from == self.snap && d.is_dir() {
                return Ok(Some(d.clone()));
            }
        }
        self.forget_headers();
        let (Some(layout), Some(t), Some(pw)) = (self.layout.clone(), self.target(), self.password.clone()) else {
            return Err(self.headers_failed(ui, s::HEADERS_NO_REPO));
        };
        let dir = self.session.path(HEADERS_DIR);
        sys::wipe_dir(&dir);
        let snap = self.snap.clone();
        let Some(mut cmd) = self.holt_cmd(&HoltCmd::LuksHeaders { layout: &layout, dir: &dir, repo: &t.place.url, password_file: &pw, snapshot: snap.as_deref() }) else {
            return Err(Go::Back);
        };
        cmd.timeout_ms = HEADERS_MS;
        // Cancelled or timed out: bko-holt (and the archiver, in its group)
        // was killed before its own undo, so what it wrote goes here.
        let x = match self.run_job(ui, s::SCR_DISKS, s::HEADERS_TITLE, Clean::ours(s::HEADERS_TEXT), cmd) {
            Ok(x) => x,
            Err(g) => {
                sys::wipe_dir(&dir);
                return Err(g);
            }
        };
        let version = self.version.clone();
        let doc = document(&x, "bko-holt-luks-headers").and_then(|v| same_version(v, HEADERS_SCHEMA, version.as_deref()));
        match doc {
            Ok(v) if x.code == Some(0) && v.get_str("dir") == dir.to_str() && headers_there(&v, &dir, self.layout_headers) => {
                self.headers = Some((dir.clone(), snap));
                Ok(Some(dir))
            }
            Ok(_) => {
                sys::wipe_dir(&dir);
                Err(self.headers_failed(ui, s::HEADERS_NOT_WRITTEN))
            }
            Err(why) => {
                sys::wipe_dir(&dir);
                let why = why.strip_prefix("bko-holt luks-headers: ").unwrap_or(&why).to_string();
                // Another snapshot's headers than the layout's: the stick's
                // copy, used for a backup that holds no layout of its own,
                // is another backup's (open question 15).
                let todo = if is_refusal(&x, "header_mismatch") && !self.layout_own { s::HEADERS_MISMATCH_STICK_TODO } else { s::HEADERS_FAILED_TODO };
                Err(self.headers_failed_todo(ui, &why, todo))
            }
        }
    }

    /// The headers couldn't be fetched: why, Try again, Back, Report.
    fn headers_failed(&mut self, ui: &mut dyn Ui, why: &str) -> Go {
        self.headers_failed_todo(ui, why, s::HEADERS_FAILED_TODO)
    }

    /// The same, with TODO's words for what to do.
    fn headers_failed_todo(&mut self, ui: &mut dyn Ui, why: &str, todo: &str) -> Go {
        let p = Problem {
            code: "headers_failed",
            screen: s::SCR_DISKS,
            title: s::HEADERS_FAILED_TITLE.to_string(),
            what: s::headers_failed(Clean::line(why, cap::PROBLEM).as_str()),
            why: s::HEADERS_FAILED_WHY.to_string(),
            todo: todo.to_string(),
            fixes: Vec::new(),
            again: true,
            back: Some(Go::Back),
        };
        self.problem(ui, p)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn only_an_install_id_is_passed_on() {
        assert!(install_id_ok("0b7e3c52-8d1f-4a6e-9c3b-2f5d7a1e4b90"));
        assert!(install_id_ok("0B7E3C52-8D1F-4A6E-9C3B-2F5D7A1E4B90"));
        for bad in ["", "../../etc", "0b7e3c52-8d1f-4a6e-9c3b-2f5d7a1e4b9", "0b7e3c52-8d1f-4a6e-9c3b-2f5d7a1e4b90 ", "--json", "0b7e3c52_8d1f_4a6e_9c3b_2f5d7a1e4b90"] {
            assert!(!install_id_ok(bad), "{bad}");
        }
    }
}
