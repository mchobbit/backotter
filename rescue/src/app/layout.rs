//! The layout file every `bko-holt load` of this session reads: the disk
//! layout the chosen machine's system.json carries, written once into the
//! session's directory (0700) by `bko-holt stick --copy INSTALL_ID OUT
//! --json` (holt-json.md's open question 8, approved 2026-11-26).
//!
//! bko-holt finds the config partition as `stick --json` did, checks the
//! machine's system.json against its `.sha256`, and writes the dump it
//! carries (its `layout`) to OUT, a new 0600 file: parsed from the bytes it
//! checked, with no second read, and printed as dump prints it.  So
//! bko-rescue mounts nothing to have it and reads no FAT (D11), and what
//! load reads is the JSON whose checksum was checked.  bko-holt's
//! refusal (no layout in system.json, a checksum gone bad since, a layout
//! load won't take) is shown in its words on the Disks screen.  A typed
//! location has no system.json on the stick: the backup's own copy waits
//! on the backup tool (rescue-screens.md, "4. Snapshot"), so then there is
//! no layout, and a screen says so.
//!
//! A layout that kept LUKS2 headers (`dump --keep-luks-header`, Ubuntu
//! Core's encryption always: the copy document's `luks_headers` counts
//! them) needs their files, which the backup holds (holt-json.md's open
//! question 12, approved 2026-11-26).  Once the password is in and the
//! snapshot chosen, before the first plan, `bko-holt luks-headers LAYOUT
//! DIR --repo URL --password-file FILE --snapshot ID --json` fetches them
//! into DIR, `h` in the session's directory: bko-holt makes it 0700, each
//! header 0600, checked against the dump's size and SHA-256.  Every load
//! then gets `--luks-header-dir DIR`.  Another snapshot fetches them
//! again; the session's end wipes them with the rest (key material).

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
        if !install_id_ok(&m.install_id) {
            return Err(super::strings::LAYOUT_NO_ID.to_string());
        }
        let out = self.session.path(NAME);
        sys::wipe_file(&out);
        let Some(cmd) = self.holt_cmd(&HoltCmd::StickCopy { install_id: &m.install_id, out: &out }) else {
            return Err(super::strings::LAYOUT_NO_HOLT.to_string());
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
                Ok(out)
            }
            Ok(_) => {
                sys::wipe_file(&out);
                Err(super::strings::LAYOUT_NOT_WRITTEN.to_string())
            }
            Err(why) => {
                sys::wipe_file(&out);
                // bko-holt's line names itself; the words before it do too.
                let why = why.strip_prefix("bko-holt stick --copy: ").unwrap_or(&why);
                Err(super::strings::layout_refused(why))
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
        let x = self.run_job(ui, s::SCR_DISKS, s::HEADERS_TITLE, Clean::ours(s::HEADERS_TEXT), cmd)?;
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
                Err(self.headers_failed(ui, &why))
            }
        }
    }

    /// The headers couldn't be fetched: why, Try again, Back, Report.
    fn headers_failed(&mut self, ui: &mut dyn Ui, why: &str) -> Go {
        let p = Problem {
            code: "headers_failed",
            screen: s::SCR_DISKS,
            title: s::HEADERS_FAILED_TITLE.to_string(),
            what: s::headers_failed(Clean::line(why, cap::PROBLEM).as_str()),
            why: s::HEADERS_FAILED_WHY.to_string(),
            todo: s::HEADERS_FAILED_TODO.to_string(),
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
