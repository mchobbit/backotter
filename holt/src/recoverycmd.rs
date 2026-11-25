//! `bko-holt recovery-words OUT --json`: the recovery key screen's check
//! (holt-json.md, "bko-holt-recovery"; J21).  The words come on standard
//! input, a pipe from bko-rescue; recovery.rs's `check` reads them and
//! `password` makes the repository key's password from them, which goes to
//! OUT, a new 0600 file in a directory of our own, when they are a recovery
//! key.  The document says what's wrong by the words' positions, never the
//! words themselves nor the password.  It writes no disk, so the dry-run
//! lock (BKO_HOLT_NO_COMMIT) allows it.

use crate::json::{Obj, Value};
use crate::load::jsonout;
use crate::recovery::{self, Problem};
use std::io::{IsTerminal, Read, Write};

pub const USAGE: &str = "Usage: bko-holt recovery-words OUT --json

Checks a BackOtter recovery key, its 12 words typed on standard input (a
pipe, at most 1 KiB: any case and spacing, each word in full or its first
4 letters), and writes one JSON document (`bko-holt-recovery`,
docs/design/holt-json.md) on standard output: whether they are a recovery
key, how many words there were, and which aren't on the list, by their
place.  When they are, the repository key's password they stand for goes
to OUT, a new file (mode 0600) in a directory of bko-holt's own user that
no one else may enter.  For bko-rescue; neither the words nor the password
are ever in what it prints.

  --json          the document (the only form there is)
  -h, --help      show this help

Exit status: 0 when the words are a recovery key and OUT was written, 1
when they aren't (nothing written) or OUT couldn't be written, 2 for bad
arguments or an OUT that isn't a new file in a private directory.
";

/// The most standard input read: the words as typed.
pub const STDIN_MAX: usize = 1024;

/// The most suggestions a word gets in the document.
pub const SUGGEST_MAX: usize = 3;

/// `bko-holt recovery-words ARGS`: the exit status.
pub fn main(args: &[String]) -> u8 {
    if jsonout::asks_for_help(args, &[]) {
        print!("{USAGE}");
        return 0;
    }
    let refuse = |code: jsonout::ErrorCode, line: String, json: bool| -> u8 {
        if json {
            jsonout::write_stdout(&jsonout::error_doc(2, code, &line));
        }
        eprintln!("{line}");
        2
    };
    let json = jsonout::find_json_flag(args, &[]).is_some();
    if let Some(e) = jsonout::schema_error(args, &[]) {
        return refuse(jsonout::ErrorCode::SchemaUnknown, format!("bko-holt recovery-words: {e}"), true);
    }
    let rest: Vec<&String> = args.iter().filter(|a| !jsonout::is_json_flag(a)).collect();
    // Not echoed: a word given as an argument would be in what this prints.
    let out = match rest.as_slice() {
        [o] if !o.starts_with('-') => o.as_str(),
        _ => return refuse(jsonout::ErrorCode::BadArgs, "bko-holt recovery-words: give the file to write, then --json; the words go on standard input".into(), json),
    };
    if !json {
        eprint!("bko-holt recovery-words: say --json: it writes its verdict as JSON, for bko-rescue\n\n{USAGE}");
        return 2;
    }
    // OUT is echoed only once it's absolute: a word typed in its place
    // (`recovery-words legal --json`) would be in what this prints.
    if !std::path::Path::new(out).is_absolute() {
        return refuse(jsonout::ErrorCode::OutUnsafe, "bko-holt recovery-words: OUT isn't an absolute path (the words go on standard input, never as an argument)".into(), true);
    }
    if let Some(why) = out_unsafe(out) {
        return refuse(jsonout::ErrorCode::OutUnsafe, format!("bko-holt recovery-words: {out}: {why}"), true);
    }
    // No core dumps: this process holds the words and the password they
    // stand for (Secrets, item 10), whatever limit its parent set.
    crate::sys::no_core_dumps();
    if std::io::stdin().is_terminal() {
        return refuse(jsonout::ErrorCode::BadArgs, "bko-holt recovery-words: the words go on standard input, a pipe: it asks nothing".into(), true);
    }
    // Room for all of it at once: a buffer that grows would leave unwiped
    // copies of the words behind in the memory it freed.  Read through a
    // copy of descriptor 0, not Stdin, whose buffer would keep them.
    let mut raw = Vec::with_capacity(STDIN_MAX + 1);
    let read = {
        use std::os::fd::AsFd;
        std::io::stdin().as_fd().try_clone_to_owned().map(std::fs::File::from).and_then(|f| f.take(STDIN_MAX as u64 + 1).read_to_end(&mut raw))
    };
    if read.is_err() || raw.len() > STDIN_MAX {
        recovery::zeroize_bytes(&mut raw);
        let why = if read.is_err() { "standard input can't be read".to_string() } else { format!("more than {STDIN_MAX} bytes on standard input") };
        return refuse(jsonout::ErrorCode::BadArgs, format!("bko-holt recovery-words: {why}"), true);
    }
    let mut text = String::from_utf8_lossy(&raw).into_owned();
    recovery::zeroize_bytes(&mut raw);
    let (doc, code) = verdict(&text, std::path::Path::new(out));
    recovery::zeroize_string(&mut text);
    jsonout::write_stdout(&jsonout::line(&doc));
    code
}

/// Why OUT can't be written: not absolute, there already, or in a directory
/// that isn't a real one of our own with no group or other bits.  None when
/// it can.
pub fn out_unsafe(out: &str) -> Option<String> {
    use std::os::unix::fs::MetadataExt;
    let p = std::path::Path::new(out);
    if !p.is_absolute() {
        return Some("not an absolute path".into());
    }
    if std::fs::symlink_metadata(p).is_ok() {
        return Some("is there already: OUT must be a new file".into());
    }
    let Some(dir) = p.parent() else { return Some("has no directory".into()) };
    let me = crate::sys::euid();
    match std::fs::symlink_metadata(dir) {
        Ok(d) if !d.file_type().is_dir() => Some(format!("{} isn't a directory (a symbolic link is refused)", dir.display())),
        Ok(d) if d.uid() != me => Some(format!("{} is owned by uid {}, not bko-holt's {me}", dir.display(), d.uid())),
        Ok(d) if d.mode() & 0o077 != 0 => Some(format!("{} has mode {:o}: group or others may reach it", dir.display(), d.mode() & 0o7777)),
        Ok(_) => None,
        Err(e) => Some(format!("{}: {e}", dir.display())),
    }
}

/// The document for the words TEXT, writing their password to OUT when
/// they are a recovery key; and the exit status.
pub fn verdict(text: &str, out: &std::path::Path) -> (Value, u8) {
    let count = recovery::typed_count(text);
    let doc = |ok: bool, problem: Option<&str>, unknown: Vec<Value>, checksum_ok: Option<bool>, written: bool, error: Option<String>| {
        jsonout::header("bko-holt-recovery")
            .set("ok", ok)
            .set("words", count)
            .set("expected", recovery::COUNT)
            .set("problem", problem)
            .set("unknown", unknown)
            .set("checksum_ok", checksum_ok)
            .set("written", written)
            .set("error", error)
            .build()
    };
    match recovery::check(text) {
        Ok(w) => {
            let mut pw = recovery::password(&w);
            let r = write_new(out, pw.as_bytes());
            recovery::zeroize_string(&mut pw);
            match r {
                Ok(()) => (doc(true, None, Vec::new(), Some(true), true, None), 0),
                Err(e) => (doc(true, None, Vec::new(), Some(true), false, Some(format!("{}: {e}", out.display()))), 1),
            }
        }
        Err(Problem::Unknown { mut words, .. }) => {
            let unknown = words
                .iter()
                .map(|u| Obj::new().set("position", u.position).set("short", u.short).set("suggest", u.suggestions.iter().take(SUGGEST_MAX).map(|s| s.to_string()).collect::<Vec<_>>()).build())
                .collect();
            // What was typed is part of the key: wiped, never shown.
            for u in &mut words {
                recovery::zeroize_string(&mut u.typed);
            }
            (doc(false, Some("unknown_words"), unknown, None, false, None), 1)
        }
        Err(Problem::Count(_)) => (doc(false, Some("count"), Vec::new(), None, false, None), 1),
        Err(Problem::Checksum) => (doc(false, Some("checksum"), Vec::new(), Some(false), false, None), 1),
    }
}

/// BYTES in OUT, a new file of mode 0600 (O_CREAT|O_EXCL, so neither a
/// file there nor a symbolic link is followed), synced; on a failure after
/// it was made, overwritten with zeros and removed.
fn write_new(out: &std::path::Path, bytes: &[u8]) -> std::io::Result<()> {
    use std::os::unix::fs::{FileExt, OpenOptionsExt};
    let mut f = std::fs::OpenOptions::new().write(true).create_new(true).mode(0o600).open(out)?;
    let r = f.write_all(bytes).and_then(|_| f.sync_all());
    if r.is_err() {
        let _ = f.write_all_at(&vec![0u8; bytes.len()], 0);
        let _ = f.sync_all();
        drop(f);
        let _ = std::fs::remove_file(out);
    }
    r
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::os::unix::fs::PermissionsExt;

    /// BIP39's first 12-word test vector (entropy 7f7f...7f).
    const GOOD: &str = "legal winner thank year wave sausage worth useful legal winner thank yellow";

    fn private_dir() -> crate::testutil::TempDir {
        let d = crate::testutil::TempDir::new("recovery");
        std::fs::set_permissions(d.path(), std::fs::Permissions::from_mode(0o700)).unwrap();
        d
    }

    #[test]
    fn good_words_write_the_password_and_say_nothing_of_it() {
        let d = private_dir();
        let out = d.path().join("pw");
        assert_eq!(out_unsafe(out.to_str().unwrap()), None);
        let (v, code) = verdict("1. LEGAL winner, than year wave sausage worth useful legal winner thank yell", &out);
        assert_eq!(code, 0, "{}", v.pretty());
        assert_eq!((v.get_bool("ok"), v.get_bool("written"), v.get_bool("checksum_ok")), (Some(true), Some(true), Some(true)));
        assert_eq!((v.get_u64("words"), v.get_u64("expected")), (Some(12), Some(12)));
        assert!(v.get("problem").unwrap().is_null() && v.get_arr("unknown").unwrap().is_empty());
        assert_eq!(std::fs::read_to_string(&out).unwrap(), GOOD);
        let m = std::fs::metadata(&out).unwrap();
        assert_eq!(m.permissions().mode() & 0o7777, 0o600);
        let line = jsonout::line(&v);
        for w in GOOD.split(' ') {
            assert!(!line.contains(&format!("\"{w}\"")) && !line.contains(w), "{w} in {line}");
        }
        let keys: Vec<&str> = v.as_object().unwrap().iter().map(|(k, _)| k.as_str()).collect();
        assert_eq!(keys, ["format", "schema", "version", "generated", "dry_run", "ok", "words", "expected", "problem", "unknown", "checksum_ok", "written", "error"]);
        crate::testutil::golden_json("recovery-json", "ok", &v);
        // A second run onto the same OUT: refused before it reads a word.
        assert!(out_unsafe(out.to_str().unwrap()).unwrap().contains("there already"));
        let (v, code) = verdict(GOOD, &out);
        assert_eq!((code, v.get_bool("ok"), v.get_bool("written")), (1, Some(true), Some(false)));
        assert!(v.get_str("error").unwrap().contains("pw"));
    }

    #[test]
    fn wrong_words_write_nothing_and_say_where() {
        let d = private_dir();
        let out = d.path().join("pw");
        let (v, code) = verdict("legal winner thank year wave sausage ribbx useful legal winner thank yellow", &out);
        assert_eq!(code, 1);
        assert_eq!((v.get_bool("ok"), v.get_str("problem"), v.get_bool("written")), (Some(false), Some("unknown_words"), Some(false)));
        assert!(v.get("checksum_ok").unwrap().is_null());
        let u = v.get_arr("unknown").unwrap();
        assert_eq!(u.len(), 1);
        assert_eq!((u[0].get_u64("position"), u[0].get_bool("short")), (Some(7), Some(false)));
        let s = u[0].get_arr("suggest").unwrap();
        assert!(!s.is_empty() && s.len() <= SUGGEST_MAX && s.iter().any(|x| x.as_str() == Some("ribbon")), "{s:?}");
        assert!(!out.exists());
        crate::testutil::golden_json("recovery-json", "unknown", &v);
        // Swapped: all on the list, the checksum wrong.
        let swapped = "winner legal thank year wave sausage worth useful legal winner thank yellow";
        let (v, code) = verdict(swapped, &out);
        assert_eq!((code, v.get_str("problem"), v.get_bool("checksum_ok")), (1, Some("checksum"), Some(false)));
        crate::testutil::golden_json("recovery-json", "checksum", &v);
        // Eleven.
        let (v, _) = verdict("legal winner thank year wave sausage worth useful legal winner thank", &out);
        assert_eq!((v.get_str("problem"), v.get_u64("words")), (Some("count"), Some(11)));
        crate::testutil::golden_json("recovery-json", "count", &v);
        assert!(v.get("checksum_ok").unwrap().is_null());
        // Too short to tell.
        let (v, _) = verdict("aba winner thank year wave sausage worth useful legal winner thank yellow", &out);
        let u = v.get_arr("unknown").unwrap();
        assert_eq!((u[0].get_u64("position"), u[0].get_bool("short")), (Some(1), Some(true)));
        assert!(!out.exists());
    }

    #[test]
    fn out_must_be_new_in_a_private_directory() {
        assert!(out_unsafe("pw").unwrap().contains("absolute"));
        let d = crate::testutil::TempDir::new("recovery");
        std::fs::set_permissions(d.path(), std::fs::Permissions::from_mode(0o755)).unwrap();
        assert!(out_unsafe(d.path().join("pw").to_str().unwrap()).unwrap().contains("mode 755"));
        std::fs::set_permissions(d.path(), std::fs::Permissions::from_mode(0o700)).unwrap();
        let real = d.path().join("real");
        std::fs::create_dir(&real).unwrap();
        std::fs::set_permissions(&real, std::fs::Permissions::from_mode(0o700)).unwrap();
        std::os::unix::fs::symlink(&real, d.path().join("link")).unwrap();
        assert!(out_unsafe(d.path().join("link/pw").to_str().unwrap()).unwrap().contains("symbolic link"));
        std::os::unix::fs::symlink("/dev/null", d.path().join("dev")).unwrap();
        assert!(out_unsafe(d.path().join("dev").to_str().unwrap()).unwrap().contains("there already"));
        assert!(out_unsafe("/nonexistent-bko-dir/pw").is_some());
    }
}
