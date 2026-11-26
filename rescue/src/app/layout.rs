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

use std::path::PathBuf;

use super::holt_args::HoltCmd;
use super::sys;
use super::{document, raw_lines, same_version, wait, App};

/// Its name in the session's directory.
pub const NAME: &str = "layout.json";

/// The `bko-holt-stick-copy` schema read.
pub const COPY_SCHEMA: u64 = 1;

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
