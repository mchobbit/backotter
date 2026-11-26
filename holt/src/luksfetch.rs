//! `bko-holt luks-headers LAYOUT DIR --repo REPO ...`: the LUKS2 headers a
//! dump kept (`crate::luksheader`), fetched from the backup into DIR for
//! `load --luks-header-dir DIR` (holt-json.md, "bko-holt-luks-headers";
//! its open question 12, approved 2026-11-26).
//!
//! dump keeps each header as a file and its SHA-256 and size in
//! `storage.luks_headers`.  The backup tool keeps those files in the backup
//! itself, in the same snapshot as the files of the system, at
//! `REPO_DIR/FILE` (stick-config.md, "LUKS2 headers in the backup"): the
//! repository is encrypted, and a header backup (16 MiB or more) would
//! fill the stick's config partition.  So the stick carries the layout
//! only, and this reads each header back with the archiver's `dump` of
//! that one path, read-only toward the repository and every disk.  Each
//! is checked against the layout's size and SHA-256 before it is kept, and
//! written to a new 0600 file in DIR, a new 0700 directory: a header is key
//! material (with an old passphrase it opens the volume).  Any failure
//! leaves nothing: what was written is zeroed and removed, and DIR too.
//!
//! The backup tool runs the same command on the snapshot it just made, as
//! its read-back: the headers it holds are the ones its layout names.

use crate::json::{Obj, Value};
use crate::load::jsonout::{self, ErrorCode as E};
use crate::luksheader::HEADER_MAX;
use std::io::Read;
use std::path::{Path, PathBuf};

/// Where the backup keeps the kept headers, inside the snapshot of the
/// system (beside Qubes's raw volumes, `crate::qubes::RAW_DIR`).  load's
/// restore leaves the directory out of the tree it restores.
pub const REPO_DIR: &str = "/.bko-holt/luks-headers";

/// The directory `REPO_DIR` is in: the backup tool's own, not the system's
/// (Qubes's raw volumes are in it too).  load leaves it out of the restore
/// with the headers, unless raw volumes are under it.
pub const BKO_DIR: &str = "/.bko-holt";

/// The archiver load runs unless --archiver says.
const DEFAULT_ARCHIVER: &str = "rustic-bko";

pub const USAGE: &str = "Usage: bko-holt luks-headers LAYOUT DIR --repo REPO [--password-file FILE]
                             [--snapshot ID] [--archiver PROGRAM] [--json]

Fetches the LUKS2 headers the dump LAYOUT kept (storage.luks_headers: dump
--keep-luks-header, and Ubuntu Core's encrypted partitions always) from the
backup, where the backup tool keeps them (/.bko-holt/luks-headers/FILE in
the snapshot), into DIR, for `bko-holt load LAYOUT --luks-header-dir DIR`.
Each is read with the archiver's dump of that one file and checked against
the size and SHA-256 the dump recorded.  DIR must be a new directory in one
of bko-holt's own user that no one else may enter; it is made with mode
0700, and each header goes in it with mode 0600.  If any header can't be
fetched or isn't the dump's, nothing is left: DIR is removed.

  --repo REPO          the repository (as load's --repo)
  --password-file FILE its password (under --json, required: nothing asks;
                       without it the archiver asks on the terminal)
  --snapshot ID        the snapshot load restores (default latest)
  --archiver PROGRAM   rustic-bko (the default) or restic-bko
  --json               one JSON document on standard output
                       (`bko-holt-luks-headers`, docs/design/holt-json.md)
  -h, --help           show this help

It reads the repository only and writes no disk, so the dry-run lock
(BKO_HOLT_NO_COMMIT) allows it.

Exit status: 0 when every header is in DIR (none kept: DIR made, empty), 1
when the layout can't be read or a header can't be fetched or isn't the
dump's (nothing left), 2 for bad arguments or a DIR that isn't a new
directory in a private one.
";

/// One header `storage.luks_headers` names.
#[derive(Debug, Clone, PartialEq)]
pub struct Entry {
    pub device: String,
    pub label: Option<String>,
    pub file: String,
    /// The size dump recorded (absent in a dump from before it did).
    pub bytes: Option<u64>,
    pub sha256: String,
}

/// Whether FILE is a header file's name as dump makes them
/// (`luksheader::file_name)`: one name, no directory, nothing hidden.
pub fn file_ok(file: &str) -> bool {
    !file.is_empty()
        && file.len() <= 200
        && !file.starts_with('.')
        && file
            .bytes()
            .all(|b| b.is_ascii_alphanumeric() || matches!(b, b'-' | b'_' | b'.'))
}

/// The headers LAYOUT (a dump) kept, each checked to be one this can
/// fetch: a device, a file name of dump's kind (no two the same), a
/// SHA-256, a size no bigger than a header can be.  Else why not.
pub fn entries(layout: &Value) -> Result<Vec<Entry>, String> {
    let list = match layout.at(&["storage", "luks_headers"]) {
        None | Some(Value::Null) => return Ok(Vec::new()),
        Some(Value::Arr(a)) => a,
        Some(_) => return Err("storage.luks_headers isn't a list".into()),
    };
    let mut out: Vec<Entry> = Vec::new();
    for (i, h) in list.iter().enumerate() {
        let at = format!("storage.luks_headers[{i}]");
        let device = h
            .get_str("device")
            .filter(|d| !d.is_empty())
            .ok_or_else(|| format!("{at} names no device"))?;
        let file = h
            .get_str("file")
            .ok_or_else(|| format!("{at} ({device}) names no file"))?;
        if !file_ok(file) {
            return Err(format!(
                "{at} ({device}): {file:?} isn't a header file's name as dump makes them"
            ));
        }
        if out.iter().any(|e| e.file == file) {
            return Err(format!("{at} ({device}): {file} is named twice"));
        }
        let sha256 = h.get_str("sha256").unwrap_or("");
        if sha256.len() != 64 || !sha256.bytes().all(|b| b.is_ascii_hexdigit()) {
            return Err(format!(
                "{at} ({device}) has no SHA-256 to check its header by"
            ));
        }
        let bytes = match h.get("bytes") {
            None | Some(Value::Null) => None,
            Some(v) => match v.as_u64() {
                Some(n) if n > 0 && n <= HEADER_MAX => Some(n),
                _ => {
                    return Err(format!(
                        "{at} ({device}): its size isn't one a LUKS2 header can have (1 to {HEADER_MAX} bytes)"
                    ));
                }
            },
        };
        out.push(Entry {
            device: device.to_string(),
            label: h.get_str("label").map(str::to_string),
            file: file.to_string(),
            bytes,
            sha256: sha256.to_ascii_lowercase(),
        });
    }
    Ok(out)
}

/// The shared check: whether B is the header E names (its size, when the
/// dump recorded one, and its SHA-256).  None when it is, else why not.
pub fn check_bytes(e: &Entry, b: &[u8]) -> Option<String> {
    if let Some(n) = e.bytes.filter(|&n| n != b.len() as u64) {
        return Some(format!("{} bytes, and the dump kept {n}", b.len()));
    }
    if crate::bytes::sha256_hex(b) != e.sha256 {
        return Some("its SHA-256 isn't the one the dump recorded".into());
    }
    None
}

/// Where E is in the snapshot.
pub fn repo_path(file: &str) -> String {
    format!("{REPO_DIR}/{file}")
}

/// What to fetch, and from where.
#[derive(Debug, Clone)]
pub struct Fetch {
    pub repo: String,
    pub password_file: Option<String>,
    pub snapshot: String,
    pub archiver: String,
    /// Standard input for the archiver: none under --json (nothing may
    /// ask), the terminal's in text mode.
    pub stdin_null: bool,
}

impl Fetch {
    /// Whether the archiver may ask for the password: text mode with no
    /// --password-file.  Its standard error is then the terminal's, not
    /// read, since restic-bko writes its prompt there (rustic-bko's goes
    /// to /dev/tty).
    pub fn asks(&self) -> bool {
        !self.stdin_null && self.password_file.is_none()
    }
}

impl Fetch {
    /// The archiver's argv for one header: `ARCHIVER --repository REPO
    /// [--password-file FILE] --no-progress dump SNAPSHOT:PATH` (restic's
    /// takes SNAPSHOT and PATH), as load's raw volumes are read.
    pub fn argv(&self, file: &str) -> Vec<String> {
        self.argv_path(&repo_path(file))
    }

    /// The archiver's argv for PATH in the snapshot (a `SNAPSHOT:SUBPATH`
    /// snapshot puts SUBPATH first): `snapshot-layout`'s system.json too.
    pub fn argv_path(&self, path: &str) -> Vec<String> {
        let mut pre = vec![
            self.archiver.clone(),
            "--repository".to_string(),
            self.repo.clone(),
        ];
        if let Some(p) = &self.password_file {
            pre.extend(["--password-file".to_string(), p.clone()]);
        }
        pre.push("--no-progress".into());
        crate::load::rawvol::dump_argv(&pre, &self.archiver, &self.snapshot, path)
    }
}

/// What the archiver said on standard error, in one short line: the line
/// after rustic's `Message:`, else its last `error` line, else the last
/// line that isn't its backtrace's (restic's `Fatal: ...`).
fn last_line(err: &[u8]) -> String {
    let t = String::from_utf8_lossy(err);
    let lines: Vec<&str> = t.lines().map(str::trim).collect();
    let said = lines
        .iter()
        .position(|l| *l == "Message:")
        .and_then(|i| lines[i + 1..].iter().find(|l| !l.is_empty()))
        .or_else(|| lines.iter().rev().find(|l| l.starts_with("error")))
        .or_else(|| {
            lines
                .iter()
                .rev()
                .find(|l| !l.is_empty() && !l.to_ascii_lowercase().contains("backtrace"))
        });
    let l = said.copied().unwrap_or("").to_string();
    let l = crate::load::plan::repo_redacted(&l);
    if l.chars().count() > 300 {
        l.chars().take(300).collect::<String>() + "…"
    } else {
        l
    }
}

/// The buffer's first size: the header's, or this much when the dump
/// recorded none (it then grows, each old copy zeroed).
const FIRST_READ: usize = 1 << 20;

/// At most MAX bytes and one more of R, in a buffer that never leaves a
/// copy of what it held: each regrowth copies into a new buffer and zeroes
/// the old one.  An allocation that fails is an error, not an abort.
fn read_capped(r: &mut impl Read, max: u64) -> Result<Vec<u8>, (Vec<u8>, String)> {
    let cap = max.saturating_add(1);
    let mut body: Vec<u8> = Vec::new();
    if body
        .try_reserve_exact(usize::try_from(cap).unwrap_or(usize::MAX).min(FIRST_READ))
        .is_err()
    {
        return Err((body, "no memory for what it wrote".into()));
    }
    let mut chunk = [0u8; 65536];
    loop {
        let left = cap - body.len() as u64;
        if left == 0 {
            break;
        }
        let want = chunk.len().min(usize::try_from(left).unwrap_or(usize::MAX));
        let n = match r.read(&mut chunk[..want]) {
            Ok(0) => break,
            Ok(n) => n,
            Err(e) if e.kind() == std::io::ErrorKind::Interrupted => continue,
            Err(e) => {
                crate::recovery::zeroize_bytes(&mut chunk);
                return Err((body, format!("its output couldn't be read: {e}")));
            }
        };
        if body.len() + n > body.capacity() {
            let mut bigger: Vec<u8> = Vec::new();
            let to = (body.capacity().saturating_mul(2))
                .max(body.len() + n)
                .min(usize::try_from(cap).unwrap_or(usize::MAX));
            if bigger.try_reserve_exact(to).is_err() {
                crate::recovery::zeroize_bytes(&mut chunk);
                return Err((body, "no memory for what it wrote".into()));
            }
            bigger.extend_from_slice(&body);
            crate::recovery::zeroize_bytes(&mut body);
            body = bigger;
        }
        body.extend_from_slice(&chunk[..n]);
    }
    crate::recovery::zeroize_bytes(&mut chunk);
    Ok(body)
}

/// Why a dump wasn't the file: the archiver failed (or couldn't be read:
/// WHY, and SAID, the archiver's own last line, empty when it said none
/// or wasn't read), or it wrote more than MAX.
#[derive(Debug, PartialEq)]
pub(crate) enum DumpFailed {
    Fetch { why: String, said: String },
    TooBig(String),
}

impl DumpFailed {
    fn fetch(why: String) -> DumpFailed {
        DumpFailed::Fetch {
            why,
            said: String::new(),
        }
    }
}

/// Runs ARGV and reads at most MAX bytes of its standard output: the
/// bytes, or why not.  ASKS: its standard error is the terminal's (it may
/// ask for the password there), not read.
pub(crate) fn run_dump(
    argv: &[String],
    max: u64,
    stdin_null: bool,
    asks: bool,
) -> Result<Vec<u8>, DumpFailed> {
    use std::process::{Command, Stdio};
    let prog = argv
        .first()
        .ok_or_else(|| DumpFailed::fetch("no archiver".into()))?;
    let mut c = Command::new(prog);
    c.args(&argv[1..]).stdout(Stdio::piped()).stderr(if asks {
        Stdio::inherit()
    } else {
        Stdio::piped()
    });
    if stdin_null {
        c.stdin(Stdio::null());
    }
    let mut child = c
        .spawn()
        .map_err(|e| DumpFailed::fetch(format!("{prog} couldn't be started: {e}")))?;
    let mut err_pipe = child.stderr.take();
    // Standard error in a thread of its own, so a full pipe there can't
    // stop the dump (its last 16 KiB kept).
    let errs = std::thread::spawn(move || {
        let mut all = Vec::new();
        if let Some(p) = err_pipe.as_mut() {
            let mut buf = [0u8; 4096];
            while let Ok(n) = p.read(&mut buf) {
                if n == 0 {
                    break;
                }
                all.extend_from_slice(&buf[..n]);
                if all.len() > 16384 {
                    all.drain(..all.len() - 16384);
                }
            }
        }
        all
    });
    let read = match child.stdout.take() {
        Some(mut o) => read_capped(&mut o, max),
        None => Ok(Vec::new()),
    };
    // Stopped before its end (more than the dump kept, or a read that
    // failed): killed, so the wait can't block on an archiver still
    // writing.
    let over = read.as_ref().map_or(true, |b| b.len() as u64 > max);
    if over {
        let _ = child.kill();
    }
    let status = child.wait();
    let err = errs.join().unwrap_or_default();
    let fail = |body: &mut Vec<u8>, why: DumpFailed| {
        crate::recovery::zeroize_bytes(body);
        Err(why)
    };
    let mut body = match read {
        Ok(b) => b,
        Err((mut b, why)) => return fail(&mut b, DumpFailed::fetch(format!("{prog}: {why}"))),
    };
    if over {
        return fail(
            &mut body,
            DumpFailed::TooBig(format!("{prog} wrote more than {max} bytes")),
        );
    }
    match status {
        Ok(s) if s.success() => Ok(body),
        Ok(s) => {
            let said = last_line(&err);
            let how = s.code().map_or_else(
                || "was killed by a signal".to_string(),
                |c| format!("exited {c}"),
            );
            let why = if said.is_empty() {
                format!("{prog} {how}")
            } else {
                format!("{prog} {how}: {said}")
            };
            fail(&mut body, DumpFailed::Fetch { why, said })
        }
        Err(e) => fail(&mut body, DumpFailed::fetch(format!("{prog}: {e}"))),
    }
}

/// Every file written so far, zeroed and removed, then DIR.
fn undo(dir: &Path, written: &[(PathBuf, usize)]) {
    use std::io::Write;
    for (p, n) in written {
        if let Ok(mut f) = std::fs::OpenOptions::new().write(true).open(p) {
            let zeros = vec![0u8; (*n).min(1 << 20)];
            let mut left = *n;
            while left > 0 {
                let k = left.min(zeros.len());
                if f.write_all(&zeros[..k]).is_err() {
                    break;
                }
                left -= k;
            }
            let _ = f.sync_all();
        }
        let _ = std::fs::remove_file(p);
    }
    let _ = std::fs::remove_dir(dir);
}

/// Fetches the headers ENTRIES name into DIR (checked by the caller to be
/// a new path in a private directory): each file's row for the document,
/// else the refusal's code and why (nothing left in DIR, DIR gone).
pub fn fetch(entries: &[Entry], dir: &Path, f: &Fetch) -> Result<Vec<Value>, (E, String)> {
    use std::os::unix::fs::{DirBuilderExt, PermissionsExt};
    std::fs::DirBuilder::new()
        .mode(0o700)
        .create(dir)
        .and_then(|()| std::fs::set_permissions(dir, std::fs::Permissions::from_mode(0o700)))
        .map_err(|e| (E::CopyFailed, format!("{}: {e}", dir.display())))?;
    let mut written: Vec<(PathBuf, usize)> = Vec::new();
    let mut rows = Vec::new();
    for e in entries {
        let argv = f.argv(&e.file);
        // What the archiver's dump was given: SNAPSHOT:PATH (rustic's),
        // or SNAPSHOT PATH (restic's).
        let from = argv
            .iter()
            .rposition(|a| a == "dump")
            .map(|i| argv[i + 1..].join(" "))
            .unwrap_or_default();
        let what = format!("{}'s LUKS2 header ({from})", e.device);
        let mut body = match run_dump(&argv, e.bytes.unwrap_or(HEADER_MAX), f.stdin_null, f.asks())
        {
            Ok(b) => b,
            Err(failed) => {
                undo(dir, &written);
                let (code, why) = match failed {
                    DumpFailed::TooBig(why) => (E::HeaderMismatch, why),
                    DumpFailed::Fetch { why, .. } => (E::HeaderFetchFailed, why),
                };
                return Err((code, format!("{what}: {why}")));
            }
        };
        if let Some(why) = check_bytes(e, &body) {
            crate::recovery::zeroize_bytes(&mut body);
            undo(dir, &written);
            return Err((
                E::HeaderMismatch,
                format!(
                    "{what} isn't the header the dump kept: {why} (a snapshot other than the one the layout was dumped with?)"
                ),
            ));
        }
        let path = dir.join(&e.file);
        let wrote = crate::recoverycmd::write_new(&path, &body);
        let n = body.len();
        crate::recovery::zeroize_bytes(&mut body);
        if let Err(err) = wrote {
            undo(dir, &written);
            return Err((E::CopyFailed, format!("{what}: {}: {err}", path.display())));
        }
        written.push((path.clone(), n));
        rows.push(
            Obj::new()
                .set("device", e.device.as_str())
                .set("label", e.label.clone())
                .set("file", e.file.as_str())
                .set("path", path.display().to_string())
                .set("from", from)
                .set("bytes", n)
                .set("sha256", e.sha256.as_str())
                .build(),
        );
    }
    Ok(rows)
}

/// `bko-holt luks-headers ARGS`: the exit status.
pub fn main(args: &[String]) -> u8 {
    const VALUE_FLAGS: &[&str] = &["--repo", "--password-file", "--snapshot", "--archiver"];
    if jsonout::asks_for_help(args, VALUE_FLAGS) {
        print!("{USAGE}");
        return 0;
    }
    let json = jsonout::find_json_flag(args, VALUE_FLAGS).is_some();
    let refuse = |exit: u8, code: E, line: String| -> u8 {
        if json {
            jsonout::write_stdout(&jsonout::error_doc(exit, code, &line));
        }
        if exit == 2 && code == E::BadArgs {
            eprintln!("{line}\n\n{USAGE}");
        } else {
            eprintln!("{line}");
        }
        exit
    };
    if let Some(e) = jsonout::schema_error(args, VALUE_FLAGS) {
        return refuse(2, E::SchemaUnknown, format!("bko-holt luks-headers: {e}"));
    }
    let mut pos: Vec<String> = Vec::new();
    let mut repo = None;
    let mut password_file = None;
    let mut snapshot = None;
    let mut archiver = None;
    let mut it = args.iter();
    while let Some(a) = it.next() {
        let slot = match a.as_str() {
            a if jsonout::is_json_flag(a) => continue,
            "--repo" => &mut repo,
            "--password-file" => &mut password_file,
            "--snapshot" => &mut snapshot,
            "--archiver" => &mut archiver,
            a if a.starts_with('-') => {
                return refuse(
                    2,
                    E::BadArgs,
                    format!("bko-holt luks-headers: unknown argument {a:?}"),
                );
            }
            _ => {
                pos.push(a.clone());
                continue;
            }
        };
        match it.next() {
            Some(v) if slot.is_none() => *slot = Some(v.clone()),
            Some(_) => {
                return refuse(
                    2,
                    E::BadArgs,
                    format!("bko-holt luks-headers: {a} is given twice"),
                );
            }
            None => {
                return refuse(
                    2,
                    E::BadArgs,
                    format!("bko-holt luks-headers: {a} needs a value"),
                );
            }
        }
    }
    let [layout_path, dir] = pos.as_slice() else {
        return refuse(
            2,
            E::BadArgs,
            "bko-holt luks-headers: give the layout and the directory to fetch its headers into"
                .into(),
        );
    };
    let Some(repo) = repo else {
        return refuse(
            2,
            E::BadArgs,
            "bko-holt luks-headers: --repo REPO: the repository the headers are fetched from"
                .into(),
        );
    };
    if crate::load::plan::repo_has_password(&repo) {
        return refuse(2, E::BadArgs, "bko-holt luks-headers: --repo: the repository's URL holds a password; it goes in --password-file FILE, never on the command line".into());
    }
    if json {
        // Nothing asks under --json, and the password file is checked as
        // load checks it (holt-json.md, "Secrets" 4).
        let Some(p) = &password_file else {
            return refuse(2, E::BadArgs, "bko-holt luks-headers: --repo: give the repository's password with --password-file FILE (under --json nothing asks for it)".into());
        };
        if let Some((_, why)) =
            crate::load::plan::key_file_unsafe(&crate::sys::Sys::host(), p, false)
        {
            return refuse(
                2,
                E::BadArgs,
                format!(
                    "bko-holt luks-headers: --password-file {p}: {why}; under --json it must be a private file of bko-holt's own user"
                ),
            );
        }
    }
    if !Path::new(dir).is_absolute() {
        return refuse(
            2,
            E::OutUnsafe,
            "bko-holt luks-headers: DIR isn't an absolute path".into(),
        );
    }
    if let Some(why) = crate::recoverycmd::out_unsafe(dir) {
        let why = why.replace("OUT must be a new file", "DIR must be a new directory");
        return refuse(
            2,
            E::OutUnsafe,
            format!("bko-holt luks-headers: {dir}: {why}"),
        );
    }
    // No core dumps: the archiver holds the repository's key, and this
    // process each header (Secrets, item 10).
    crate::sys::no_core_dumps();
    let say = |what: String| format!("bko-holt luks-headers: {what}");
    let text = match crate::bytes::read_to_string_bounded(Path::new(layout_path), crate::LAYOUT_MAX)
    {
        Ok(t) => t,
        Err(e) => return refuse(1, E::LayoutUnreadable, say(format!("{layout_path}: {e}"))),
    };
    let v = match crate::json::parse(&text) {
        Ok(v) => v,
        Err(e) => {
            return refuse(
                1,
                E::LayoutNotJson,
                say(format!("{layout_path}: not JSON ({e})")),
            );
        }
    };
    if let Err(e) = crate::load::layout::Layout::from_json(v.clone()) {
        return refuse(1, E::LayoutRefused, say(format!("{layout_path}: {e}")));
    }
    let list = match entries(&v) {
        Ok(l) => l,
        Err(e) => return refuse(1, E::LayoutRefused, say(format!("{layout_path}: {e}"))),
    };
    let snapshot = snapshot.unwrap_or_else(|| "latest".to_string());
    let f = Fetch {
        repo,
        password_file,
        snapshot: snapshot.clone(),
        archiver: archiver.unwrap_or_else(|| DEFAULT_ARCHIVER.to_string()),
        stdin_null: json,
    };
    match fetch(&list, Path::new(dir), &f) {
        Ok(rows) => {
            if json {
                let doc = jsonout::header("bko-holt-luks-headers")
                    .set("layout", layout_path.as_str())
                    .set("dir", dir.as_str())
                    .set("snapshot", snapshot.as_str())
                    .set("repo_dir", REPO_DIR)
                    .set("headers", rows)
                    .build();
                jsonout::write_stdout(&jsonout::line(&doc));
            } else if rows.is_empty() {
                println!("{layout_path} kept no LUKS2 header: {dir} is empty");
            } else {
                for r in &rows {
                    println!(
                        "{}: {}'s LUKS2 header, {} bytes, the dump's SHA-256",
                        r.get_str("path").unwrap_or(""),
                        r.get_str("device").unwrap_or(""),
                        r.get_u64("bytes").unwrap_or(0)
                    );
                }
            }
            0
        }
        Err((code, line)) => refuse(1, code, say(line)),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::testutil::TempDir;
    use std::os::unix::fs::PermissionsExt;

    fn h(file: &str, body: &[u8]) -> Value {
        Obj::new()
            .set("device", "vda5")
            .set("luks_uuid", "u")
            .set("label", "ubuntu-data-enc")
            .set("file", file)
            .set("written_to", format!("/run/bko-holt/luks-headers/{file}"))
            .set("bytes", body.len())
            .set("sha256", crate::bytes::sha256_hex(body))
            .build()
    }

    fn layout(hs: Vec<Value>) -> Value {
        let mut v =
            crate::json::parse(r#"{"format": "bko-holt", "schema": 1, "storage": {}}"#).unwrap();
        v.at_mut(&["storage"]).unwrap().put("luks_headers", hs);
        v
    }

    /// A private directory, and in it an archiver: a shell script that
    /// logs its argv, then writes the file named by the dump argument's
    /// basename from FILES, or fails as rustic does.
    fn rig() -> (TempDir, PathBuf, PathBuf) {
        let d = TempDir::new("luksfetch");
        std::fs::set_permissions(d.path(), std::fs::Permissions::from_mode(0o700)).unwrap();
        let files = d.path().join("files");
        std::fs::create_dir(&files).unwrap();
        let arch = d.path().join("rustic-bko");
        std::fs::write(
            &arch,
            format!(
                "#!/bin/sh\nprintf '%s\\n' \"$@\" >> '{log}'\nfor a; do last=$a; done\nf='{files}'/\"${{last##*/}}\"\n[ -f \"$f\" ] || {{ echo 'error: path not found in snapshot' >&2; exit 1; }}\ncat \"$f\"\n",
                log = d.path().join("argv").display(),
                files = files.display()
            ),
        )
        .unwrap();
        std::fs::set_permissions(&arch, std::fs::Permissions::from_mode(0o755)).unwrap();
        (d, files, arch)
    }

    fn fetcher(arch: &Path, snapshot: &str) -> Fetch {
        Fetch {
            repo: "sftp://nas/bko".into(),
            password_file: Some("/k/pw".into()),
            snapshot: snapshot.into(),
            archiver: arch.display().to_string(),
            stdin_null: true,
        }
    }

    #[test]
    fn entries_are_checked() {
        let a = h("luks2-header-a.img", b"A");
        assert_eq!(entries(&layout(vec![])).unwrap(), vec![]);
        assert_eq!(
            entries(&crate::json::parse(r#"{"storage": {}}"#).unwrap()).unwrap(),
            vec![]
        );
        let e = entries(&layout(vec![a.clone()])).unwrap();
        assert_eq!(
            (
                e[0].device.as_str(),
                e[0].file.as_str(),
                e[0].bytes,
                e[0].label.as_deref()
            ),
            (
                "vda5",
                "luks2-header-a.img",
                Some(1),
                Some("ubuntu-data-enc")
            )
        );
        for bad in ["../x.img", "a/b.img", ".hidden", "", "x y.img"] {
            let err = entries(&layout(vec![h(bad, b"A")])).unwrap_err();
            assert!(
                err.contains("name") || err.contains("no file"),
                "{bad}: {err}"
            );
        }
        assert!(
            entries(&layout(vec![a.clone(), a.clone()]))
                .unwrap_err()
                .contains("named twice")
        );
        let mut no_sum = a.clone();
        no_sum.put("sha256", "00");
        assert!(
            entries(&layout(vec![no_sum]))
                .unwrap_err()
                .contains("SHA-256")
        );
        let mut huge = a.clone();
        huge.put("bytes", HEADER_MAX + 1);
        assert!(entries(&layout(vec![huge])).unwrap_err().contains("size"));
        let mut old = a;
        old.put("bytes", Value::Null);
        assert_eq!(entries(&layout(vec![old])).unwrap()[0].bytes, None);
        assert!(
            entries(&crate::json::parse(r#"{"storage": {"luks_headers": {}}}"#).unwrap()).is_err()
        );
    }

    #[test]
    fn the_check_is_size_then_sum() {
        let e = &entries(&layout(vec![h(
            "luks2-header-a.img",
            b"LUKS\xba\xbe header",
        )]))
        .unwrap()[0];
        assert_eq!(check_bytes(e, b"LUKS\xba\xbe header"), None);
        assert!(
            check_bytes(e, b"LUKS\xba\xbe heade")
                .unwrap()
                .contains("bytes")
        );
        assert!(
            check_bytes(e, b"LUKS\xba\xbe HEADER")
                .unwrap()
                .contains("SHA-256")
        );
    }

    #[test]
    fn argv_reads_one_file_of_the_snapshot() {
        let f = fetcher(Path::new("rustic-bko"), "latest");
        assert_eq!(
            f.argv("h.img"),
            [
                "rustic-bko",
                "--repository",
                "sftp://nas/bko",
                "--password-file",
                "/k/pw",
                "--no-progress",
                "dump",
                "latest:/.bko-holt/luks-headers/h.img"
            ]
        );
        let sub = fetcher(Path::new("/usr/bin/rustic-bko"), "1a2b3c4d:/mnt/src");
        assert_eq!(
            sub.argv("h.img").last().unwrap(),
            "1a2b3c4d:/mnt/src/.bko-holt/luks-headers/h.img"
        );
        let restic = Fetch {
            archiver: "restic-bko".into(),
            password_file: None,
            ..fetcher(Path::new("x"), "1a2b")
        };
        assert_eq!(
            restic.argv("h.img"),
            [
                "restic-bko",
                "--repository",
                "sftp://nas/bko",
                "--no-progress",
                "dump",
                "1a2b",
                "/.bko-holt/luks-headers/h.img"
            ]
        );
    }

    #[test]
    fn fetched_headers_are_private_and_checked() {
        let (d, files, arch) = rig();
        let (a, b) = (
            b"LUKS\xba\xbe first header".to_vec(),
            b"LUKS\xba\xbe second".to_vec(),
        );
        std::fs::write(files.join("luks2-header-a.img"), &a).unwrap();
        std::fs::write(files.join("luks2-header-b.img"), &b).unwrap();
        let list = entries(&layout(vec![
            h("luks2-header-a.img", &a),
            h("luks2-header-b.img", &b),
        ]))
        .unwrap();
        let out = d.path().join("h");
        let rows = fetch(&list, &out, &fetcher(&arch, "1a2b3c4d")).unwrap();
        assert_eq!(
            std::fs::metadata(&out).unwrap().permissions().mode() & 0o7777,
            0o700
        );
        for (r, body) in rows.iter().zip([&a, &b]) {
            let p = r.get_str("path").unwrap();
            assert_eq!(&std::fs::read(p).unwrap(), body);
            assert_eq!(
                std::fs::metadata(p).unwrap().permissions().mode() & 0o7777,
                0o600
            );
            assert_eq!(r.get_u64("bytes"), Some(body.len() as u64));
            assert!(
                r.get_str("from")
                    .unwrap()
                    .starts_with("1a2b3c4d:/.bko-holt/luks-headers/luks2-header-")
            );
        }
        let argv = std::fs::read_to_string(d.path().join("argv")).unwrap();
        assert!(
            argv.contains("dump\n1a2b3c4d:/.bko-holt/luks-headers/luks2-header-b.img\n"),
            "{argv}"
        );
        // DIR there already: mkdir refuses (the caller checks it first).
        assert_eq!(
            fetch(&list, &out, &fetcher(&arch, "x")).unwrap_err().0,
            E::CopyFailed
        );
    }

    #[test]
    fn any_failure_leaves_nothing() {
        let (d, files, arch) = rig();
        let a = b"LUKS\xba\xbe first header".to_vec();
        std::fs::write(files.join("luks2-header-a.img"), &a).unwrap();
        // The second isn't in the snapshot: the first is wiped too.
        let list = entries(&layout(vec![
            h("luks2-header-a.img", &a),
            h("luks2-header-b.img", b"other"),
        ]))
        .unwrap();
        let out = d.path().join("h1");
        let (code, why) = fetch(&list, &out, &fetcher(&arch, "latest")).unwrap_err();
        assert_eq!(code, E::HeaderFetchFailed);
        assert!(
            why.contains("vda5's LUKS2 header (latest:/.bko-holt/luks-headers/luks2-header-b.img)")
                && why.contains("exited 1: error: path not found in snapshot"),
            "{why}"
        );
        assert!(!out.exists());
        // Another header than the dump's (another snapshot's): refused.
        std::fs::write(files.join("luks2-header-b.img"), b"LUKS\xba\xbe not it").unwrap();
        let list = entries(&layout(vec![h(
            "luks2-header-b.img",
            b"LUKS\xba\xbe is it!",
        )]))
        .unwrap();
        let (code, why) =
            fetch(&list, &d.path().join("h2"), &fetcher(&arch, "latest")).unwrap_err();
        assert_eq!(code, E::HeaderMismatch);
        assert!(
            why.contains("its SHA-256 isn't") && why.contains("other than the one"),
            "{why}"
        );
        assert!(!d.path().join("h2").exists());
        // More than the dump kept: stopped at its size and one byte.
        std::fs::write(files.join("luks2-header-c.img"), vec![7u8; 4096]).unwrap();
        let mut c = h("luks2-header-c.img", &[7u8; 4096]);
        c.put("bytes", 100u64);
        let (code, why) = fetch(
            &entries(&layout(vec![c])).unwrap(),
            &d.path().join("h3"),
            &fetcher(&arch, "latest"),
        )
        .unwrap_err();
        assert_eq!(code, E::HeaderMismatch, "{why}");
        assert!(why.contains("more than 100 bytes"), "{why}");
        // No archiver at all.
        let none = Fetch {
            archiver: d.path().join("nope").display().to_string(),
            ..fetcher(&arch, "latest")
        };
        let (code, why) = fetch(
            &entries(&layout(vec![h("luks2-header-a.img", &a)])).unwrap(),
            &d.path().join("h4"),
            &none,
        )
        .unwrap_err();
        assert_eq!(code, E::HeaderFetchFailed);
        assert!(why.contains("couldn't be started"), "{why}");
        assert!(!d.path().join("h4").exists());
    }

    #[test]
    fn an_old_dump_without_sizes_reads_a_big_header() {
        // No `bytes`: the buffer starts at FIRST_READ and grows (each old
        // copy zeroed), never HEADER_MAX at once (oq12 review).
        let (d, files, arch) = rig();
        let big: Vec<u8> = (0..(3 * FIRST_READ + 5)).map(|i| (i % 251) as u8).collect();
        std::fs::write(files.join("luks2-header-a.img"), &big).unwrap();
        let mut e = h("luks2-header-a.img", &big);
        e.put("bytes", Value::Null);
        let list = entries(&layout(vec![e])).unwrap();
        let out = d.path().join("h");
        let rows = fetch(&list, &out, &fetcher(&arch, "latest")).unwrap();
        assert_eq!(
            std::fs::read(rows[0].get_str("path").unwrap()).unwrap(),
            big
        );
        // The cap holds: at most MAX and one more byte is read.
        let mut r: &[u8] = &[1, 2, 3, 4, 5, 6, 7, 8, 9, 10];
        assert_eq!(read_capped(&mut r, 4).unwrap(), [1, 2, 3, 4, 5]);
        let mut r: &[u8] = &[1, 2, 3];
        assert_eq!(read_capped(&mut r, 4).unwrap(), [1, 2, 3]);
    }

    #[test]
    fn the_code_is_the_failures_kind_not_its_words() {
        // An archiver that fails with "wrote more than" in its own words:
        // still header_fetch_failed (oq12 review).
        let (d, _, _) = rig();
        let arch = d.path().join("liar");
        std::fs::write(
            &arch,
            "#!/bin/sh\necho 'error: it wrote more than it should' >&2\nexit 1\n",
        )
        .unwrap();
        std::fs::set_permissions(&arch, std::fs::Permissions::from_mode(0o755)).unwrap();
        let list = entries(&layout(vec![h("luks2-header-a.img", b"A")])).unwrap();
        let (code, why) = fetch(&list, &d.path().join("h"), &fetcher(&arch, "latest")).unwrap_err();
        assert_eq!(code, E::HeaderFetchFailed, "{why}");
        assert!(
            why.contains("exited 1: error: it wrote more than it should"),
            "{why}"
        );
    }

    #[test]
    fn text_mode_without_a_password_file_lets_the_archiver_ask() {
        // restic-bko asks on standard error: it is the terminal's then, not
        // read (oq12 review), so the refusal has no line of it.
        let (d, _, arch) = rig();
        let asks = Fetch {
            password_file: None,
            stdin_null: false,
            ..fetcher(&arch, "latest")
        };
        assert!(asks.asks());
        assert!(
            !fetcher(&arch, "latest").asks(),
            "a password file: nothing asks"
        );
        assert!(
            !Fetch {
                password_file: None,
                ..fetcher(&arch, "latest")
            }
            .asks(),
            "under --json nothing asks"
        );
        let list = entries(&layout(vec![h("luks2-header-a.img", b"A")])).unwrap();
        let (code, why) = fetch(&list, &d.path().join("h"), &asks).unwrap_err();
        assert_eq!(code, E::HeaderFetchFailed);
        assert!(
            why.ends_with("exited 1") && !why.contains("path not found"),
            "{why}"
        );
    }

    #[test]
    fn none_kept_is_an_empty_directory() {
        let (d, _, arch) = rig();
        let out = d.path().join("h");
        assert_eq!(fetch(&[], &out, &fetcher(&arch, "latest")).unwrap(), vec![]);
        assert!(out.is_dir() && std::fs::read_dir(&out).unwrap().next().is_none());
        assert!(!d.path().join("argv").exists(), "the archiver isn't run");
    }

    #[test]
    fn stderr_lines_are_short_and_redacted() {
        assert_eq!(last_line(b"one\nerror: bad\n\n"), "error: bad");
        // rustic-bko's own (a wrong password), and restic's.
        let rustic = b"[INFO] using no config file\nerror: `rustic_core` experienced an error related to `credentials handling`.\n\nMessage:\nThe password that has been entered, seems to be incorrect.\n\nSome additional details ...\n\nBacktrace:\ndisabled backtrace (set 'RUST_BACKTRACE=\"1\"' environment variable to enable)\n";
        assert_eq!(
            last_line(rustic),
            "The password that has been entered, seems to be incorrect."
        );
        assert_eq!(
            last_line(b"Fatal: wrong password or no key found\n"),
            "Fatal: wrong password or no key found"
        );
        assert_eq!(
            last_line(b"x sftp://u:secret@h/r y"),
            "x sftp://u:REDACTED@h/r y"
        );
        assert!(last_line(&[b'a'; 400]).chars().count() == 301);
    }
}
