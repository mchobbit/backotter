//! `bko-holt report-bug`: everything a BackOtter bug report needs, in one
//! text file to attach to a GitHub issue, redacted by default (`redact.rs`).
//!
//! It lives in bko-holt rather than in a script on the stick because
//! bko-holt is the one BackOtter program on every platform the stick runs
//! (Linux, the BSDs, illumos, Haiku), it already reads the dump the
//! redaction has to understand, and the redaction's proof (that `load`'s
//! plan of a redacted dump is the plan of the original) needs load's own
//! code.  It covers restic-bko and rustic-bko too: their versions, the
//! archiver's output that load logged, and any error output saved to a file
//! (`--stderr FILE`).

use crate::json::{Obj, Value};
use crate::load::{self, Options, Parsed, RUN_DIR};
use crate::redact::{Collector, Redactor};
use crate::sys::{FakeRunner, Sys};
use std::process::ExitCode;

/// Where issues go: in the help, its last line and the report hint
/// (main.rs) alike.
pub const ISSUES: &str = "https://github.com/mchobbit/backotter/issues";

/// Where `load` records its last run, for this command to find.
pub const LAST_RUN: &str = "last-load.json";

/// Lines kept from each log.
const LOG_LINES: usize = 3000;

pub const USAGE: &str = "Usage: bko-holt report-bug [--output FILE] [--stderr FILE]... [--attach FILE]...
                          [--strip-file FILE]... [--no-redact] [-- LOAD-ARGUMENTS...]

Gathers what a BackOtter bug report needs into one text file to attach to
an issue at https://github.com/mchobbit/backotter/issues :

  - the versions of bko-holt, restic-bko, rustic-bko, the rescue image, the
    operating system and the kernel
  - this system's `bko-holt dump`
  - the last `bko-holt load` run (it records itself in /run/bko-holt, or
    /var/run/bko-holt on systems other than Linux): its command line, exit
    status and error output, the layout it loaded, the plan for it with its
    Problems, and the target disks' status
  - bko-holt's logs: load.log in that directory's log/, and /var/log/bko-holt/
  - error output you saved (--stderr FILE), for example from
    `restic-bko backup ... 2> backup-errors.txt`
  - other files you attach (--attach FILE): bko-rescue attaches its events
    file, the last plan and status documents and its own log

GitHub issues are public, so by default every hostname, machine-id, UUID,
serial number, label, user name (from /home paths and Ubuntu's USERDATA
datasets, in the dump, the logs and the error output alike), key-file path,
MAC address, repository and password-file path gets a pseudonym (host-1,
label2, bc000000-...-0003), the same one everywhere in the file, so
references still resolve.  Sizes, offsets, feature flags, types and the
device topology stay as they are.  It prints what it redacted, what it
kept, and the file's path.  Read the file before you attach it: it is
plain text, and a user name that is in no home directory's path (the
\"by USER\" of restic's lock message) stays as it is.

  --output FILE   write the report here (default: bko-report-DATE.txt in
                  the current directory)
  --stderr FILE   also include this file of error output (repeatable)
  --attach FILE   also include this file, redacted as --stderr's are; a
                  bko-holt plan or status document's disks are learned too
                  (repeatable)
  --strip-file FILE
                  a file of strings, one a line, each replaced wherever it
                  appears, as an other identifier: a password hint, a
                  place's name (repeatable; a line under 3 characters is
                  left as it is)
  --no-redact     leave identifiers as they are (for a private report;
                  --strip-file is ignored then)
  -- LOAD-ARGUMENTS
                  report this load run instead of the last one: the
                  arguments you gave `bko-holt load` (the layout and --map,
                  --erase, --key-file...); only its plan and status are run
  -h, --help      show this help

Exit status: 0 written, 1 it couldn't be written, 2 bad arguments.
";

#[derive(Debug, Default, PartialEq)]
pub struct Args {
    pub output: Option<String>,
    pub stderr: Vec<String>,
    /// --attach FILE: bko-rescue's files (its events, the last plan and
    /// status documents, its log), included as --stderr's are.
    pub attach: Vec<String>,
    /// --strip-file FILE: strings, one a line, to strip wherever they are.
    pub strip_files: Vec<String>,
    pub no_redact: bool,
    pub load_args: Option<Vec<String>>,
    /// For tests: read the system from this tree, running no commands.
    pub sysroot: Option<String>,
}

/// The flags parse_args takes a value after, for `--json`'s first pass
/// (crate::load::jsonout::find_json_flag); parse_args asserts it.
pub const VALUE_FLAGS: &[&str] = &["--output", "-o", "--stderr", "--attach", "--strip-file", "--sysroot"];

pub fn parse_args(args: &[String]) -> Result<Option<Args>, String> {
    let mut a = Args::default();
    let mut it = args.iter();
    while let Some(x) = it.next() {
        let mut value = |flag: &str| {
            debug_assert!(VALUE_FLAGS.contains(&flag), "{flag} takes a value: add it to VALUE_FLAGS");
            it.next().cloned().ok_or_else(|| format!("{flag} needs a value"))
        };
        match x.as_str() {
            "-h" | "--help" => return Ok(None),
            "--output" | "-o" => a.output = Some(value(x)?),
            "--stderr" => a.stderr.push(value(x)?),
            "--attach" => a.attach.push(value(x)?),
            "--strip-file" => a.strip_files.push(value(x)?),
            "--no-redact" => a.no_redact = true,
            "--sysroot" => a.sysroot = Some(value(x)?),
            "--" => {
                a.load_args = Some(it.cloned().collect());
                break;
            }
            other => return Err(format!("unknown argument {other:?} (load's arguments go after --)")),
        }
    }
    Ok(Some(a))
}

pub fn main(args: &[String]) -> ExitCode {
    // The report is text for people: --json is refused, as a document
    // (holt-json.md, "Commands and flags"); beside --help, the help.
    if let Some(flag) = load::jsonout::find_json_flag(args, VALUE_FLAGS) {
        if load::jsonout::asks_for_help(args, VALUE_FLAGS) {
            print!("{USAGE}");
            return ExitCode::SUCCESS;
        }
        let line = format!("bko-holt report-bug: {flag}: the report is text for people; run it without --json");
        load::jsonout::write_stdout(&load::jsonout::error_doc(2, load::jsonout::ErrorCode::JsonNotHere, &line));
        eprintln!("{line}");
        return ExitCode::from(2);
    }
    let a = match parse_args(args) {
        Ok(Some(a)) => a,
        Ok(None) => {
            print!("{USAGE}");
            return ExitCode::SUCCESS;
        }
        Err(e) => {
            eprintln!("bko-holt report-bug: {e}\n\n{USAGE}");
            return ExitCode::from(2);
        }
    };
    let sys = match &a.sysroot {
        Some(d) if !std::path::Path::new(d).is_dir() => {
            eprintln!("bko-holt report-bug: --sysroot {d}: not a directory");
            return ExitCode::from(2);
        }
        Some(d) => Sys::new(d, Box::new(FakeRunner::default()), crate::sys::euid() == 0),
        None => Sys::host(),
    };
    let now = crate::timestamp();
    let path = a.output.clone().unwrap_or_else(|| format!("bko-report-{}.txt", now.replace(['-', ':'], "").replace('T', "-").trim_end_matches('Z')));
    // A dry run writes only a new file, so a device (or anything else that
    // is there) can't be written over.
    let new_only = load::jsonout::no_commit().is_some();
    if new_only && std::fs::symlink_metadata(&path).is_ok() {
        eprintln!("{}", load::jsonout::exists_refusal("report-bug", &path));
        return ExitCode::from(2);
    }
    let b = build(&sys, &a, &now);
    // 0600, and under sudo the invoking user's: they can attach it, no one
    // else can read it (a --no-redact one holds what redaction would hide).
    if let Err(e) = write_report(&path, &b.text, new_only) {
        eprintln!("bko-holt report-bug: {path}: {e}");
        return ExitCode::from(1);
    }
    for n in &b.notes {
        eprintln!("bko-holt report-bug: {n}");
    }
    print!("{}", b.terminal);
    let shown = std::fs::canonicalize(&path).map(|p| p.display().to_string()).unwrap_or(path);
    println!("\nWrote {shown} ({} KiB).", b.text.len().div_ceil(1024));
    println!("Read it, then attach it to a new issue at {ISSUES}");
    ExitCode::SUCCESS
}

/// The report, and what to print on the terminal.
pub struct Bundle {
    pub text: String,
    /// What was redacted (with originals: the terminal only) and kept.
    pub terminal: String,
    /// Things that went wrong gathering it.
    pub notes: Vec<String>,
}

struct Part {
    title: String,
    body: Body,
}

enum Body {
    Text(String),
    Json(Value),
}

/// The last load run, from load's record or from `--`.
struct Run {
    source: String,
    args: Vec<String>,
    cwd: Option<String>,
    exit: Option<u64>,
    stderr: Option<String>,
    time: Option<String>,
}

/// The layout at PATH: under SYS's root when it's absolute, else from here.
fn read_any(sys: &Sys, path: &str) -> std::io::Result<String> {
    let p = if path.starts_with('/') { sys.p(path) } else { std::path::PathBuf::from(path) };
    crate::bytes::read_bounded(&p, crate::LAYOUT_MAX).map(|b| String::from_utf8_lossy(&b).into_owned())
}

/// How much of the end of a --stderr file is read: far more than its last
/// LOG_LINES lines, which are what the report keeps.
const STDERR_TAIL_MAX: u64 = 16 << 20;

fn tail(text: &str, n: usize) -> String {
    let lines: Vec<&str> = text.lines().collect();
    if lines.len() <= n {
        return text.to_string();
    }
    format!("[... {} earlier lines left out ...]\n{}\n", lines.len() - n, lines[lines.len() - n..].join("\n"))
}

fn last_run(sys: &Sys, a: &Args) -> Option<Run> {
    if let Some(args) = &a.load_args {
        return Some(Run { source: "given after --".into(), args: args.clone(), cwd: None, exit: None, stderr: None, time: None });
    }
    let path = format!("{RUN_DIR}/{LAST_RUN}");
    let v = crate::json::parse(&sys.read(&path)?).ok()?;
    let args = v.get("args")?.as_array()?.iter().filter_map(|x| x.as_str().map(str::to_string)).collect();
    Some(Run {
        source: path,
        args,
        cwd: v.get("cwd").and_then(Value::as_str).map(str::to_string),
        exit: v.get("exit").and_then(Value::as_u64),
        stderr: v.get("stderr").and_then(Value::as_str).map(str::to_string),
        time: v.get("time").and_then(Value::as_str).map(str::to_string),
    })
}

/// The last MAX bytes of TEXT at most, starting at a character boundary
/// (error output may hold multibyte UTF-8).
fn tail_bytes(text: &str, max: usize) -> &str {
    let mut i = text.len().saturating_sub(max);
    while !text.is_char_boundary(i) {
        i += 1;
    }
    &text[i..]
}

/// `bko-holt load` records each run (but not --sysroot ones or --help)
/// for `report-bug`.  It's in RAM, and failing to write it changes nothing.
pub fn record_load_run(args: &[String], exit: u8, stderr: &str) {
    if args.iter().any(|a| a == "--sysroot" || a == "-h" || a == "--help") {
        return;
    }
    let cwd = std::env::current_dir().map(|p| p.display().to_string()).unwrap_or_default();
    let err = tail_bytes(stderr, 65536);
    let v = Obj::new()
        .set("format", "bko-holt-last-load")
        .set("time", crate::timestamp())
        .set("version", env!("CARGO_PKG_VERSION"))
        .set("cwd", cwd)
        .set("args", Value::Arr(args.iter().map(|a| Value::Str(a.clone())).collect()))
        .set("exit", u64::from(exit))
        .set("stderr", err)
        .build();
    {
        use std::os::unix::fs::{DirBuilderExt, PermissionsExt};
        let _ = std::fs::DirBuilder::new().recursive(true).mode(0o700).create(RUN_DIR);
        let _ = std::fs::set_permissions(RUN_DIR, std::fs::Permissions::from_mode(0o700));
    }
    let _ = write_private_file(&format!("{RUN_DIR}/{LAST_RUN}"), &v.pretty());
}

/// TEXT in the file at PATH, 0600; a PATH that isn't a regular file (a
/// device) keeps its mode.
fn write_private_file(path: &str, text: &str) -> std::io::Result<()> {
    use std::io::Write;
    use std::os::unix::fs::{OpenOptionsExt, PermissionsExt};
    let mut f = std::fs::OpenOptions::new().write(true).create(true).truncate(true).mode(0o600).open(path)?;
    if f.metadata()?.is_file() {
        let _ = f.set_permissions(std::fs::Permissions::from_mode(0o600));
    }
    f.write_all(text.as_bytes())
}

/// The report's TEXT in the file at PATH.  A file this makes is 0600 and,
/// when root runs it under sudo, the invoking user's (SUDO_UID and
/// SUDO_GID), so they can attach it and no one else can read it.  A
/// regular file already there is made 0600 but keeps its owner: handing
/// root's existing files to the sudo user (--output /etc/passwd) would be
/// a way past sudo's rules.  A PATH that isn't a regular file (a device,
/// a FIFO) is written to as it is.
/// NEW_ONLY (a dry run): only a file this run makes is written, never one
/// that was there.
fn write_report(path: &str, text: &str, new_only: bool) -> std::io::Result<()> {
    use std::io::Write;
    use std::os::unix::fs::{OpenOptionsExt, PermissionsExt};
    // O_EXCL first, which also refuses a symlink in PATH's last place:
    // whether this run made the file decides whether it may be chowned.
    let (mut f, created) = match std::fs::OpenOptions::new().write(true).create_new(true).mode(0o600).open(path) {
        Ok(f) => (f, true),
        Err(e) if e.kind() == std::io::ErrorKind::AlreadyExists && !new_only => (std::fs::OpenOptions::new().write(true).truncate(true).open(path)?, false),
        Err(e) => return Err(e),
    };
    if f.metadata()?.is_file() {
        // Someone else's file, writable by us: it keeps its mode.
        let _ = f.set_permissions(std::fs::Permissions::from_mode(0o600));
        let (uid, gid) = (std::env::var("SUDO_UID").ok(), std::env::var("SUDO_GID").ok());
        if let Some((u, g)) = report_owner(created, crate::sys::euid(), uid.as_deref(), gid.as_deref()) {
            if let Err(e) = std::os::unix::fs::fchown(&f, Some(u), g) {
                eprintln!("bko-holt report-bug: {path}: can't give it to uid {u} ({e}): it stays root's");
            }
        }
    }
    f.write_all(text.as_bytes())
}

/// Who a report file is handed to: under sudo (root, SUDO_UID a user's
/// uid), that user and SUDO_GID's group, for a file this run made only.
/// None: it stays whose it is.
fn report_owner(created: bool, euid: u32, sudo_uid: Option<&str>, sudo_gid: Option<&str>) -> Option<(u32, Option<u32>)> {
    if !created || euid != 0 {
        return None;
    }
    let uid: u32 = sudo_uid?.trim().parse().ok().filter(|&u| u != 0)?;
    Some((uid, sudo_gid.and_then(|g| g.trim().parse().ok())))
}

/// Above this many base64 characters, a dump's boot-area or ESP image is
/// left out of the report (its size and CRC-32 stay): such images are
/// almost all of a report's bytes, and the plan and status sections already
/// ran on the whole layout.
const BLOB_MAX: usize = 4096;

/// V with every base64 image longer than BLOB_MAX replaced: KEY becomes
/// KEY_omitted, {"bytes", "crc32"}.  `load` refuses a layout that has one.
fn slim(v: &Value) -> Value {
    match v {
        Value::Obj(kv) => Value::Obj(
            kv.iter()
                .map(|(k, x)| match x {
                    Value::Str(b) if crate::redact::is_b64_key(k) && b.len() > BLOB_MAX => {
                        let raw = crate::bytes::unbase64(b).unwrap_or_default();
                        let crc = format!("{:08x}", crate::bytes::crc32(&raw));
                        (format!("{k}{}", load::OMITTED), Obj::new().set("bytes", raw.len() as u64).set("crc32", crc).build())
                    }
                    _ => (k.clone(), slim(x)),
                })
                .collect(),
        ),
        Value::Arr(a) => Value::Arr(a.iter().map(slim).collect()),
        _ => v.clone(),
    }
}

/// Where a BackOtter rescue image keeps its marker (stick/marker.py).
const MARKERS: [&str; 3] = ["/.disk/backotter.json", "/boot/.disk/backotter.json", "/cdrom/.disk/backotter.json"];

fn versions(sys: &Sys) -> (String, Vec<String>) {
    let mut s = format!("bko-holt      {}\n", env!("CARGO_PKG_VERSION"));
    let mut notes = Vec::new();
    for (prog, arg) in [("restic-bko", "version"), ("rustic-bko", "--version")] {
        let v = match crate::load::plan::find(sys, prog) {
            Some(exe) => sys.run_file(&exe, &[arg]).map(|o| format!("{} ({exe})", o.trim())).unwrap_or_else(|| format!("{exe} {arg} failed")),
            None => "not installed here".to_string(),
        };
        s += &format!("{prog:<13} {v}\n");
    }
    // The image's root on the BSDs and illumos; Haiku's boot volume; the
    // Linux image's medium, which casper mounts on /cdrom.
    let marker = MARKERS.iter().find_map(|p| sys.read(p).map(|t| (p, t)));
    match marker {
        Some((p, t)) => match crate::json::parse(&t) {
            Ok(m) => {
                let pick = |k: &str| m.get(k).cloned().unwrap_or(Value::Null);
                let zfs = m.at(&["zfs", "version"]).cloned().unwrap_or(Value::Null);
                let short = Obj::new()
                    .set("image_id", pick("image_id"))
                    .set("built", pick("built"))
                    .set("platform", pick("platform"))
                    .set("base", pick("base"))
                    .set("kernel", pick("kernel"))
                    .set("zfs", zfs)
                    .build();
                s += &format!("image         {} ({p})\n", short.compact());
            }
            Err(_) => s += &format!("image         {p} isn't JSON\n"),
        },
        None => s += "image         none: not running from a BackOtter rescue image (no .disk/backotter.json)\n",
    }
    // As dump reads it: NetBSD, OpenBSD and Haiku have no os-release.
    let os = if crate::other::Family::current().is_some() { crate::other::os_release(sys) } else { crate::osrel::read(sys) };
    let osline = os.as_ref().map(|o| format!("{} ({} {})", o.get("PRETTY_NAME").unwrap_or("?"), o.get("ID").unwrap_or("?"), o.get("VERSION_ID").unwrap_or("?")));
    s += &format!("os            {}\n", osline.unwrap_or_else(|| "no os-release".into()));
    let un = sys.run("uname", &["-s", "-r", "-v", "-m"]).map(|o| o.trim().to_string());
    if un.is_none() {
        notes.push("uname -srvm failed; the kernel isn't in the report".into());
    }
    s += &format!("kernel        {}\n", un.unwrap_or_else(|| "?".into()));
    (s, notes)
}

/// The plan stage of a load run's arguments (whatever stage it ran), in
/// text mode: without `--commit`, `--through`/`--only` and their stage,
/// and a --json run's `--json` and `--expect-plan` (holt-json.md, "Bug
/// reports"), so the report's plan and status read as they do for a text
/// run.  A flag's value is kept as it is (`--snapshot --json` names a
/// snapshot).
fn plan_args(args: &[String]) -> Vec<String> {
    let mut out = Vec::new();
    let mut it = args.iter();
    while let Some(a) = it.next() {
        match a.as_str() {
            "--commit" => {}
            j if load::jsonout::is_json_flag(j) => {}
            "--through" | "--only" | "--expect-plan" => {
                it.next();
            }
            f if load::VALUE_FLAGS.contains(&f) => {
                out.push(a.clone());
                if let Some(v) = it.next() {
                    out.push(v.clone());
                }
            }
            _ => out.push(a.clone()),
        }
    }
    out
}

/// The most of a --strip-file read: far more than the strings a person
/// types (bko-rescue's hint and names).
const STRIP_FILE_MAX: u64 = 1 << 20;

/// The strings of a --strip-file, one a line (a trailing CR dropped),
/// empty lines skipped.
fn strip_lines(text: &str) -> Vec<String> {
    text.lines().map(|l| l.strip_suffix('\r').unwrap_or(l)).filter(|l| !l.trim().is_empty()).map(str::to_string).collect()
}

/// What an attached file teaches the Collector beyond its text: a
/// bko-holt document's disks (a plan's candidates and targets, a status's
/// targets: serials, WWIDs, by-id links and labels of this machine's disks,
/// which no dump may hold), and each event of an events file.
fn attached_values(text: &str) -> Vec<Value> {
    let ours = |v: &Value| v.get("format").and_then(Value::as_str).is_some_and(|f| f.starts_with("bko-holt-")) || v.get("event").is_some();
    if let Ok(v) = crate::json::parse(text) {
        return if ours(&v) { vec![v] } else { Vec::new() };
    }
    // Progress events hold figures only, and a long copy has thousands.
    text.lines()
        .filter(|l| l.trim_start().starts_with('{'))
        .filter_map(|l| crate::json::parse(l).ok())
        .filter(|v| ours(v) && v.get("event").and_then(Value::as_str) != Some("progress"))
        .collect()
}

/// The flags whose value is a repository or a password file: load's own
/// (a plan document's `args`) and the archiver's (its argv in a plan's
/// stages, in `cmd` and `failed` events, in a log's command lines).
const SECRET_FLAGS: [&str; 3] = ["--repo", "--repository", "--password-file"];

/// What FLAG's VALUE teaches the Collector: a repository or a password
/// file (Kind::Secret), or --key-file NAME=FILE's FILE.  A value that is
/// itself a flag is a flag with its value missing.
fn learn_flag(c: &mut Collector, flag: &str, value: &str) {
    if value.starts_with("--") {
        return;
    }
    if SECRET_FLAGS.contains(&flag) {
        c.learn_secret(value);
    } else if flag == "--key-file" {
        c.learn_key_file(value.split_once('=').map_or(value, |(_, f)| f));
    }
}

/// The repositories, password files and key files named in a document's
/// or an event's argument lists, at any depth.  Neither the dump nor this
/// machine holds them, and the recorded run (last-load.json) may be none
/// or another's: under --json, plan and status runs record nothing.
fn learn_argv_values(c: &mut Collector, v: &Value) {
    match v {
        Value::Arr(a) => {
            for (i, x) in a.iter().enumerate() {
                match (x.as_str(), a.get(i + 1).and_then(Value::as_str)) {
                    (Some(f), Some(val)) => learn_flag(c, f, val),
                    (None, _) => learn_argv_values(c, x),
                    _ => {}
                }
            }
        }
        Value::Obj(kv) => kv.iter().for_each(|(_, x)| learn_argv_values(c, x)),
        _ => {}
    }
}

/// The same in free text (a log's or error output's command lines): each
/// flag's next word, shell quotes and a JSON string's escapes taken off.
/// A word in capitals is help's placeholder ("--password-file FILE",
/// "--key-file NAME=FILE"), not a value.
fn learn_text_flags(c: &mut Collector, text: &str) {
    let placeholder = |v: &str| v.bytes().all(|b| b.is_ascii_uppercase() || b.is_ascii_digit() || b"_-.=".contains(&b));
    let words: Vec<&str> = text.split_whitespace().collect();
    for w in words.windows(2) {
        let f = w[0].trim_start_matches(['\'', '"']);
        if !(SECRET_FLAGS.contains(&f) || f == "--key-file") {
            continue;
        }
        let v = w[1].split('\\').next().unwrap_or("").trim_matches(['\'', '"', ',', ';', ')', '`']);
        if !placeholder(v) && !placeholder(v.split_once('=').map_or(v, |(_, x)| x)) {
            learn_flag(c, f, v);
        }
    }
}

/// What report-bug learns from an attached file's TEXT (`--attach`): the
/// text, the flags' values in it, and each bko-holt document or event in
/// it, its argument lists' too.
pub fn learn_attachment(c: &mut Collector, text: &str) {
    learn_text_flags(c, text);
    c.learn_text(text);
    for v in attached_values(text) {
        learn_argv_values(c, &v);
        c.learn(&v);
    }
}

/// The `--no-redact` report's first line: every Kind the redaction would
/// have replaced, from Kind::plural (its first words, without what a
/// parenthesis adds), so it can't drift.
fn not_redacted_line() -> String {
    let kinds: Vec<&str> = crate::redact::Kind::ALL.iter().map(|k| k.plural().split(" (").next().unwrap_or("")).collect();
    format!("NOT redacted: it holds this machine's {}, as they are.\n", kinds.join(", "))
}

/// Options with the names in --key-file NAME=FILE redacted, to run against
/// the redacted layout (a crypttab name such as luks-UUID changes with it).
fn redacted_opts(o: &Options, r: &Redactor) -> Options {
    let mut o = o.clone();
    o.key_files = o.key_files.iter().map(|(n, f)| (r.text(n), f.clone())).collect();
    o
}

pub fn build(sys: &Sys, a: &Args, now: &str) -> Bundle {
    let mut notes = Vec::new();
    let mut parts: Vec<Part> = Vec::new();
    let mut c = Collector::new();
    let (vers, n) = versions(sys);
    notes.extend(n);
    parts.push(Part { title: "versions".into(), body: Body::Text(vers) });

    // The last load run, its layout, plan and status.
    let mut layout: Option<(String, Value, String)> = None;
    let mut run_opts: Option<Options> = None;
    // The run's free text, learned after the dumps so that what they hold
    // keeps the pseudonyms it gets without it (RustAudit1 H5-01).
    let mut run_text: Vec<String> = Vec::new();
    let run = last_run(sys, a);
    match &run {
        None => parts.push(Part {
            title: "last bko-holt load run".into(),
            body: Body::Text(format!("none recorded ({RUN_DIR}/{LAST_RUN} isn't there); give load's arguments after -- to report one\n")),
        }),
        Some(r) => {
            let mut t = format!("from {}{}\n", r.source, r.time.as_ref().map(|t| format!(", {t}")).unwrap_or_default());
            let command = r.args.iter().map(|x| load::shell_quote(x)).collect::<Vec<_>>().join(" ");
            t += &format!("command: bko-holt load {command}\n");
            run_text.push(command);
            if let Some(cwd) = &r.cwd {
                t += &format!("in: {cwd}\n");
                run_text.push(cwd.clone());
            }
            if let Some(e) = r.exit {
                t += &format!("exit status: {e}\n");
            }
            if let Some(err) = &r.stderr {
                t += &format!("---- its error output\n{}", if err.is_empty() { "(none)\n".to_string() } else { err.clone() });
                run_text.push(err.clone());
            }
            parts.push(Part { title: "last bko-holt load run".into(), body: Body::Text(t) });
            match load::parse_args(&plan_args(&r.args)) {
                Ok(Parsed::Run(mut o)) => {
                    // The report's plan and status read as text mode's,
                    // whatever the run was.
                    o.json = false;
                    if !o.layout.starts_with('/') {
                        if let Some(cwd) = &r.cwd {
                            o.layout = format!("{}/{}", cwd.trim_end_matches('/'), o.layout);
                        }
                    }
                    run_text.push(o.layout.clone());
                    match read_any(sys, &o.layout) {
                        Ok(text) => match crate::json::parse(&text) {
                            Ok(v) => layout = Some((o.layout.clone(), v, text)),
                            Err(e) => notes.push(format!("{}: not JSON ({}); its plan isn't in the report", o.layout, e.message)),
                        },
                        Err(e) if e.kind() == std::io::ErrorKind::InvalidData => notes.push(format!("{}: bigger than load reads ({} bytes); its plan isn't in the report", o.layout, crate::LAYOUT_MAX)),
                        Err(_) => notes.push(format!("{}: can't read it; its plan isn't in the report", o.layout)),
                    }
                    for (_, f) in &o.key_files {
                        c.learn_key_file(f);
                    }
                    if let Some(r) = &o.repo {
                        c.learn_secret(r);
                    }
                    if let Some(p) = &o.password_file {
                        c.learn_secret(p);
                    }
                    run_opts = Some(o);
                }
                Ok(Parsed::Help) => {}
                Err(e) => notes.push(format!("the load run's arguments don't parse ({e}); its plan isn't in the report")),
            }
        }
    }

    // This system's dump.
    let own = crate::dump(sys);
    match &own {
        Ok(v) => c.learn(v),
        Err(e) => notes.push(format!("bko-holt dump of this system failed: {e}")),
    }
    if let Some((_, v, _)) = &layout {
        c.learn(v);
    }
    for t in &run_text {
        c.learn_text(t);
    }

    // Logs.
    let mut logs: Vec<(String, String)> = Vec::new();
    let mut log_paths = vec![format!("{RUN_DIR}/log/load.log")];
    for d in ["/var/log/bko-holt", "/system/var/log/bko-holt"] {
        let mut names = sys.list(d);
        names.sort();
        log_paths.extend(names.into_iter().map(|n| format!("{d}/{n}")));
    }
    for p in log_paths {
        if let Some(t) = sys.read(&p) {
            learn_text_flags(&mut c, &t);
            c.learn_text(&t);
            logs.push((p, tail(&t, LOG_LINES)));
        }
    }
    let mut saved: Vec<(String, String)> = Vec::new();
    for p in &a.stderr {
        match crate::bytes::read_tail(std::path::Path::new(p), STDERR_TAIL_MAX) {
            Ok(b) => {
                let t = String::from_utf8_lossy(&b).into_owned();
                c.learn_text(p);
                learn_text_flags(&mut c, &t);
                c.learn_text(&t);
                saved.push((p.clone(), tail(&t, LOG_LINES)));
            }
            Err(e) => notes.push(format!("--stderr {p}: {e}")),
        }
    }
    let mut attached: Vec<(String, String)> = Vec::new();
    for p in &a.attach {
        match crate::bytes::read_tail(std::path::Path::new(p), STDERR_TAIL_MAX) {
            Ok(b) => {
                let t = String::from_utf8_lossy(&b).into_owned();
                c.learn_text(p);
                learn_attachment(&mut c, &t);
                attached.push((p.clone(), tail(&t, LOG_LINES)));
            }
            Err(e) => notes.push(format!("--attach {p}: {e}")),
        }
    }
    if a.no_redact && !a.strip_files.is_empty() {
        notes.push("--strip-file is ignored with --no-redact: nothing is replaced".into());
    }
    for p in a.strip_files.iter().filter(|_| !a.no_redact) {
        match crate::bytes::read_bounded(std::path::Path::new(p), STRIP_FILE_MAX) {
            Ok(b) => {
                let short = strip_lines(&String::from_utf8_lossy(&b)).iter().filter(|l| !c.learn_strip(l)).count();
                if short > 0 {
                    notes.push(format!("--strip-file {p}: {short} line(s) under {} characters left as they are", crate::redact::STRIP_MIN));
                }
            }
            // Never the file's text: only that it couldn't be read.
            Err(e) => notes.push(format!("--strip-file {p}: {e}")),
        }
    }

    let r = if a.no_redact { Collector::new().seal() } else { c.seal() };

    // The plan and status, run against the redacted layout: what a
    // developer gets from the bundle's own layout.  The plan of the
    // original, mapped through the pseudonyms, must be the same.
    if let (Some((path, v, text)), Some(o)) = (&layout, &run_opts) {
        let red = r.value(v);
        let red_text = red.pretty();
        let ro = redacted_opts(o, &r);
        let (code, out, err) = load::run(&ro, &red_text, sys);
        let shown_args = run.as_ref().map(|x| plan_args(&x.args)).unwrap_or_default();
        let mut body = format!("bko-holt load {} -- exit {code}\n", shown_args.iter().map(|x| load::shell_quote(x)).collect::<Vec<_>>().join(" "));
        let (code0, out0, err0) = if a.no_redact { (code, out.clone(), err.clone()) } else { load::run(o, text, sys) };
        if a.no_redact {
            body += &(out + &err);
        } else if code0 == code && r.unredact(&out) == out0 && r.unredact(&err) == err0 {
            body += "(checked: the plan of the redacted layout is the original's, pseudonyms aside)\n";
            body += &(out + &err);
        } else if code0 == code && sorted_lines(&r.unredact(&(out.clone() + &err))) == sorted_lines(&(out0.clone() + &err0)) {
            // load sorts datasets and mount points by name, and pseudonyms
            // sort differently.
            body += "(checked: the plan of the redacted layout runs the same commands, in another order because pseudonyms sort\ndifferently; this is the original's, redacted)\n";
            body += &(out0 + &err0);
        } else {
            // The original's plan, redacted as text: what a developer
            // reruns from the bundle won't match it exactly.
            body += "(warning: the plan of the redacted layout differs from the original's beyond the pseudonyms; this is the original's)\n";
            notes.push("the redacted layout's plan differs from the original's: please say so in the issue".into());
            body += &(out0 + &err0);
        }
        parts.push(Part { title: format!("plan for that run, of {path}"), body: Body::Text(body) });
        let mut so = ro.clone();
        so.only = Some(load::Stage::Status);
        so.through = None;
        // With no --map, load matched the disks by WWID or serial, which the
        // redacted layout's pseudonyms match nowhere: the original's pairs
        // as --map instead.
        let mut how = String::new();
        if so.maps.is_empty() && !a.no_redact {
            so.maps = load::disk_pairs(o, text, sys).into_iter().map(|(src, t)| (r.text(&src), t)).collect();
            if !so.maps.is_empty() {
                how = format!("(the run matched its disks by WWID or serial; given here as {})\n", so.maps.iter().map(|(s, t)| format!("--map {s}={t}")).collect::<Vec<_>>().join(" "));
            }
        }
        let (code, out, err) = load::run(&so, &red_text, sys);
        parts.push(Part { title: "status of the target disks (--only status)".into(), body: Body::Text(format!("exit {code}\n{how}{out}{err}")) });
        parts.push(Part { title: format!("layout: {path} (the dump that run loaded)"), body: Body::Json(v.clone()) });
    }
    match own {
        Ok(v) => parts.push(Part { title: "dump of this system (bko-holt dump)".into(), body: Body::Json(v) }),
        Err(e) => parts.push(Part { title: "dump of this system (bko-holt dump)".into(), body: Body::Text(format!("failed: {e}\n")) }),
    }
    if logs.is_empty() {
        parts.push(Part { title: "logs".into(), body: Body::Text(format!("none: no {RUN_DIR}/log/load.log or /var/log/bko-holt/\n")) });
    }
    for (p, t) in logs {
        parts.push(Part { title: format!("log: {p}"), body: Body::Text(t) });
    }
    for (p, t) in saved {
        parts.push(Part { title: format!("saved error output: {p}"), body: Body::Text(t) });
    }
    for (p, t) in attached {
        parts.push(Part { title: format!("attached: {p}"), body: Body::Text(t) });
    }

    let mut text = format!("BackOtter bug report, made by bko-holt report-bug {} at {now}\n", env!("CARGO_PKG_VERSION"));
    if a.no_redact {
        text += &not_redacted_line();
    } else {
        text += "Redacted: identifiers are replaced by pseudonyms (host-1, label2, bc000000-0000-4000-8000-000000000003),\nthe same one everywhere in this file; the last section says what was and wasn't.\n";
    }
    text += "Sections start with \"==== \"; a JSON section is one document, for `bko-holt load` to plan (boot-area and\nESP images over 4 KiB are left out, as KEY_omitted with their size and CRC-32, so it won't write them).\n";
    for p in &parts {
        text += &format!("\n==== {}\n", r.text(&p.title));
        match &p.body {
            Body::Text(t) => {
                let t = r.text(t);
                text += &t;
                if !t.ends_with('\n') {
                    text.push('\n');
                }
            }
            Body::Json(v) => text += &slim(&r.value(v)).pretty(),
        }
    }
    let summary_file = r.summary(false);
    if !a.no_redact {
        text += &format!("\n==== redaction\n{summary_file}");
    }
    text += "\n==== end\n";
    let terminal = if a.no_redact { "Not redacted (--no-redact): don't post this file publicly.\n".to_string() } else { r.summary(true) };
    Bundle { text, terminal, notes }
}

/// The proof the redaction keeps `load` working: on a fake rescue system
/// with a blank disk of the same size for each of the dump's, `load`'s plan
/// of the redacted dump, with the pseudonyms mapped back, is the plan of the
/// original, byte for byte, Problems and exit status too; the redacted dump
/// maps back to the original; and no identifier is left in the redacted
/// dump or its plan.  Returns the number of identifiers and plan lines.
#[doc(hidden)]
pub fn check_plan(layout_text: &str) -> Result<(usize, usize), String> {
    let v = crate::json::parse(layout_text).map_err(|e| format!("not JSON: {}", e.message))?;
    let (rv, r) = crate::redact::redact(&v);
    let back = r.unredact_value(&rv);
    if back.compact() != v.compact() {
        return Err(format!("the redacted dump doesn't map back to the original: {}", value_diff(&back, &v, "").unwrap_or_default()));
    }
    let rtext = rv.pretty();
    let leaks = r.leaks(&rtext);
    if !leaks.is_empty() {
        return Err(format!("left in the redacted dump: {leaks:?}"));
    }
    let t = crate::testutil::Tree::new("redact-plan");
    t.file("/proc/self/mountinfo", "").file("/proc/swaps", "Filename\tType\tSize\tUsed\tPriority\n");
    // A rescue system of the dump's own architecture (crate::arch), which
    // load holds it to.
    t.file("/proc/sys/kernel/arch", format!("{}\n", crate::arch::dump_machine(&v)));
    let mut args = vec!["layout.json".to_string()];
    let disks = v.at(&["storage", "disks"]).and_then(Value::as_array).cloned().unwrap_or_default();
    for (i, d) in disks.iter().enumerate() {
        let Some(name) = d.get("name").and_then(Value::as_str) else { continue };
        let tgt = format!("sd{}", (b'q' + i as u8) as char);
        let size = d.get("size_bytes").and_then(Value::as_u64).unwrap_or(1 << 34);
        let ss = d.get("logical_sector_size").and_then(Value::as_u64).unwrap_or(512) as u32;
        t.disk(&tgt, &format!("8:{}", 16 * i), &[], ss).file(&format!("/sys/block/{tgt}/size"), format!("{}\n", size / 512));
        args.push("--map".into());
        args.push(format!("{name}={tgt}"));
    }
    args.extend(["--repo".to_string(), "/srv/bko-repo".to_string()]);
    let opts = match load::parse_args(&args) {
        Ok(Parsed::Run(o)) => o,
        other => return Err(format!("load's arguments {args:?}: {other:?}")),
    };
    let sys = || Sys::new(t.root(), Box::new(FakeRunner::default().with("/usr/sbin/rustic-bko --version", "rustic 0.11.4+backotter.4\n")), true);
    t.file("/usr/sbin/rustic-bko", "");
    // Install every tool the plan asks for, as a rescue system would have.
    let mut orig = load::run(&opts, layout_text, &sys());
    for _ in 0..4 {
        let missing: Vec<String> = orig.1.lines().filter_map(|l| l.trim_start_matches("#   - ").split_once(" isn't installed here").map(|(p, _)| p.to_string())).collect();
        if missing.is_empty() {
            break;
        }
        for m in missing {
            t.file(&format!("/usr/sbin/{}", m.trim_start_matches('/').rsplit('/').next().unwrap_or(&m)), "");
        }
        orig = load::run(&opts, layout_text, &sys());
    }
    let ropts = redacted_opts(&opts, &r);
    let red = load::run(&ropts, &rtext, &sys());
    let back = (red.0, r.unredact(&red.1), r.unredact(&red.2));
    if back != orig {
        let what = if back.0 != orig.0 { format!("exit {} vs {}", back.0, orig.0) } else if back.1 != orig.1 { first_diff(&back.1, &orig.1) } else { first_diff(&back.2, &orig.2) };
        return Err(format!("the plan of the redacted dump differs: {what}"));
    }
    let leaks = r.leaks(&(red.1.clone() + &red.2));
    if !leaks.is_empty() {
        return Err(format!("left in the redacted dump's plan: {leaks:?}"));
    }
    Ok((r.entries.len(), orig.1.lines().count()))
}

/// Where two JSON values first differ; for a blob, the first byte.
fn value_diff(a: &Value, b: &Value, path: &str) -> Option<String> {
    match (a, b) {
        (Value::Obj(x), Value::Obj(y)) if x.len() == y.len() => x.iter().zip(y).find_map(|((ka, va), (kb, vb))| {
            if ka != kb {
                Some(format!("{path}: key {ka:?} vs {kb:?}"))
            } else {
                value_diff(va, vb, &format!("{path}/{ka}"))
            }
        }),
        (Value::Arr(x), Value::Arr(y)) if x.len() == y.len() => x.iter().zip(y).enumerate().find_map(|(i, (p, q))| value_diff(p, q, &format!("{path}/{i}"))),
        (Value::Str(x), Value::Str(y)) if x != y => {
            if let (Some(p), Some(q)) = (crate::bytes::unbase64(x), crate::bytes::unbase64(y)) {
                if p.len() == q.len() && crate::redact::is_blob_key(path.rsplit('/').next().unwrap_or("")) {
                    let i = p.iter().zip(&q).position(|(m, n)| m != n).unwrap_or(0);
                    let w = |b: &[u8]| crate::bytes::hex(crate::bytes::sub(b, i.saturating_sub(8), 32));
                    return Some(format!("{path}: byte {i} of {}: {} vs {}", p.len(), w(&p), w(&q)));
                }
            }
            Some(format!("{path}: {:?} vs {:?}", &x[..x.len().min(200)], &y[..y.len().min(200)]))
        }
        _ if a != b => Some(format!("{path}: {} vs {}", a.compact().chars().take(200).collect::<String>(), b.compact().chars().take(200).collect::<String>())),
        _ => None,
    }
}

fn sorted_lines(s: &str) -> Vec<&str> {
    let mut v: Vec<&str> = s.lines().collect();
    v.sort_unstable();
    v
}

fn first_diff(a: &str, b: &str) -> String {
    for (i, (x, y)) in a.lines().zip(b.lines()).enumerate() {
        if x != y {
            return format!("line {}: {x:?} (redacted, mapped back) vs {y:?} (original)", i + 1);
        }
    }
    format!("{} lines vs {}", a.lines().count(), b.lines().count())
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::testutil::Tree;

    fn s(a: &[&str]) -> Vec<String> {
        a.iter().map(|x| x.to_string()).collect()
    }

    #[test]
    fn arguments() {
        assert_eq!(parse_args(&s(&["--help"])).unwrap(), None);
        let a = parse_args(&s(&["-o", "r.txt", "--stderr", "e1", "--stderr", "e2", "--no-redact", "--", "l.json", "--map", "vda=sdb"])).unwrap().unwrap();
        assert_eq!(a.output.as_deref(), Some("r.txt"));
        assert_eq!(a.stderr, s(&["e1", "e2"]));
        assert!(a.no_redact);
        assert_eq!(a.load_args, Some(s(&["l.json", "--map", "vda=sdb"])));
        assert!(parse_args(&s(&["l.json"])).unwrap_err().contains("after --"));
        assert!(parse_args(&s(&["--output"])).unwrap_err().contains("needs a value"));
        assert_eq!(plan_args(&s(&["l", "--through", "format", "--commit", "--map", "a=b"])), s(&["l", "--map", "a=b"]));
    }

    #[test]
    fn the_sample_layouts_plan_the_same_redacted() {
        check_plan(&crate::load::layout::sample::bios_mbr()).unwrap();
    }

    /// A rescue system that ran `load` on a layout and failed: the bundle
    /// has every section, redacted, and finds the run by itself.
    #[test]
    fn report_owner_only_for_sudo_and_new_files() {
        assert_eq!(report_owner(true, 0, Some("1000"), Some("1000")), Some((1000, Some(1000))));
        assert_eq!(report_owner(true, 0, Some("1000"), None), Some((1000, None)));
        assert_eq!(report_owner(true, 0, Some("1000"), Some("x")), Some((1000, None)));
        // Not root, not under sudo, root's own sudo, or a file already there.
        assert_eq!(report_owner(true, 1000, Some("1000"), Some("1000")), None);
        assert_eq!(report_owner(true, 0, None, Some("1000")), None);
        assert_eq!(report_owner(true, 0, Some("0"), Some("0")), None);
        assert_eq!(report_owner(true, 0, Some("alice"), None), None);
        assert_eq!(report_owner(false, 0, Some("1000"), Some("1000")), None);
    }

    #[test]
    fn write_report_is_private() {
        use std::os::unix::fs::PermissionsExt;
        let t = Tree::new("bugreport-write");
        let new = t.root().join("new.txt");
        write_report(new.to_str().unwrap(), "one", false).unwrap();
        assert_eq!(std::fs::read_to_string(&new).unwrap(), "one");
        assert_eq!(std::fs::metadata(&new).unwrap().permissions().mode() & 0o777, 0o600);
        // A regular file already there: rewritten and made 0600.
        let old = t.root().join("old.txt");
        std::fs::write(&old, "a much longer old text").unwrap();
        std::fs::set_permissions(&old, std::fs::Permissions::from_mode(0o644)).unwrap();
        write_report(old.to_str().unwrap(), "two", false).unwrap();
        assert_eq!(std::fs::read_to_string(&old).unwrap(), "two");
        assert_eq!(std::fs::metadata(&old).unwrap().permissions().mode() & 0o777, 0o600);
        // Not a regular file: written to, its mode left alone.
        let before = std::fs::metadata("/dev/null").unwrap().permissions().mode();
        write_report("/dev/null", "three", false).unwrap();
        assert_eq!(std::fs::metadata("/dev/null").unwrap().permissions().mode(), before);
        // A dry run's (BKO_HOLT_NO_COMMIT): a new file only.
        let dry = t.root().join("dry.txt");
        write_report(dry.to_str().unwrap(), "four", true).unwrap();
        assert_eq!(std::fs::read_to_string(&dry).unwrap(), "four");
        let e = write_report(dry.to_str().unwrap(), "five", true).unwrap_err();
        assert_eq!(e.kind(), std::io::ErrorKind::AlreadyExists);
        assert_eq!(std::fs::read_to_string(&dry).unwrap(), "four");
        assert_eq!(write_report("/dev/null", "six", true).unwrap_err().kind(), std::io::ErrorKind::AlreadyExists);
    }

    #[test]
    fn value_flags_table() {
        // Every flag in the table takes a value, and parse_args's own
        // debug_assert holds the other way.
        for f in VALUE_FLAGS {
            assert!(parse_args(&[f.to_string()]).unwrap_err().contains("needs a value"), "{f}");
        }
    }

    #[test]
    fn bundle_from_a_failed_run() {
        let t = Tree::new("report-bug");
        t.debian13();
        t.file("/proc/self/mountinfo", "").file("/proc/swaps", "Filename\tType\tSize\tUsed\tPriority\n");
        t.disk("vdb", "253:16", &[], 512).file("/sys/block/vdb/size", "33554432\n");
        let layout = crate::load::layout::sample::bios_mbr().replace("\"deb13\"", "\"alice-pc\"");
        t.file("/srv/alice/layout.json", &layout);
        t.file("/.disk/backotter.json", r#"{"format": "backotter-image", "schema": 1, "image_id": "6f1c2c1e-5b0e-4a51-9d8e-2a3c8f4e7b10", "built": "2026-10-29T17:12:39Z", "platform": "linux", "base": {"os": "ubuntu", "release": "26.04.1"}, "kernel": "7.0.0-30-generic", "zfs": {"version": "2.4.0", "features": ["a", "b"]}}"#);
        let last = Obj::new()
            .set("time", "2026-11-07T03:40:00Z")
            .set("cwd", "/srv/alice")
            .set("args", Value::Arr(s(&["layout.json", "--map", "vda=vdb", "--through", "format", "--commit"]).into_iter().map(Value::Str).collect()))
            .set("exit", 1u64)
            .set("stderr", "bko-holt load: format: mkfs.btrfs failed (exit status: 1):\n    ERROR: b7b7b7b7-0000-4000-8000-000000000003 on alice-pc\n")
            .build();
        t.file(&format!("{RUN_DIR}/{LAST_RUN}"), last.pretty());
        t.file(&format!("{RUN_DIR}/log/load.log"), "==== stage format\nmkfs.btrfs -U b7b7b7b7-0000-4000-8000-000000000003 /dev/vdb3\nFAILED\n");
        let sys = Sys::new(t.root(), Box::new(FakeRunner::default().with("uname -s -r -v -m", "Linux 7.0.0-30-generic #30 SMP x86_64\n")), true);
        let a = Args::default();
        let b = build(&sys, &a, "2026-11-07T03:42:00Z");
        let txt = &b.text;
        let log_hdr = format!("==== log: {RUN_DIR}/log/load.log");
        for sect in [
            "==== versions", "==== last bko-holt load run", "==== plan for that run, of /srv/alice/layout.json", "==== status of the target disks",
            "==== layout: /srv/alice/layout.json", "==== dump of this system", log_hdr.as_str(), "==== redaction", "==== end",
        ] {
            assert!(txt.contains(sect), "no {sect:?} in:\n{txt}");
        }
        assert!(txt.contains("image         {\"image_id\":\"6f1c2c1e-5b0e-4a51-9d8e-2a3c8f4e7b10\""), "{txt}");
        assert!(!txt.lines().find(|l| l.starts_with("image ")).unwrap().contains("features"), "{txt}");
        assert!(txt.contains("exit status: 1"));
        assert!(txt.contains("command: bko-holt load layout.json --map vda=vdb --through format --commit"), "{txt}");
        assert!(txt.contains("(checked: the plan of the redacted layout is the original's"), "{txt}");
        assert!(txt.contains("#   vda (16 GiB) -> /dev/vdb"), "{txt}");
        assert!(txt.contains("# Source: host-1,"), "{txt}");
        for gone in ["alice-pc", "b7b7b7b7", "0b0e2c6e", "1f2e3d4c"] {
            assert!(!txt.contains(gone), "{gone} in:\n{txt}");
        }
        assert!(txt.contains("Linux 7.0.0-30-generic"));
        assert!(b.terminal.contains("alice-pc -> host-1"), "{}", b.terminal);
        assert!(!txt.contains("alice-pc -> host-1"));
        // Not redacted on request.
        let a = Args { no_redact: true, ..Args::default() };
        let b = build(&sys, &a, "2026-11-07T03:42:00Z");
        assert!(b.text.contains("alice-pc") && b.text.contains("NOT redacted"));
        assert!(!b.text.contains("==== redaction"));
    }

    /// Free text is scanned as the dump is: a user name, a UUID and a MAC
    /// that are only in load's error output, its log, a --stderr file or
    /// the run's cwd get pseudonyms too (RustAudit1 H5-01).
    #[test]
    fn identifiers_only_in_free_text() {
        let t = Tree::new("report-bug-text");
        t.debian13();
        t.file("/proc/self/mountinfo", "").file("/proc/swaps", "Filename\tType\tSize\tUsed\tPriority\n");
        t.disk("vdb", "253:16", &[], 512).file("/sys/block/vdb/size", "33554432\n");
        t.file("/home/bob/layout.json", crate::load::layout::sample::bios_mbr());
        let last = Obj::new()
            .set("cwd", "/home/bob")
            .set("args", Value::Arr(s(&["layout.json", "--map", "vda=vdb"]).into_iter().map(Value::Str).collect()))
            .set("exit", 1u64)
            .set("stderr", "bko-holt load: 5d1c0e9a-7b3f-4c2e-9a1d-0e4f3b2a1c9d is busy\n")
            .build();
        t.file(&format!("{RUN_DIR}/{LAST_RUN}"), last.pretty());
        t.file(&format!("{RUN_DIR}/log/load.log"), "link up on 52:54:00:9c:4e:17\n");
        let errs = t.root().join("backup-errors.txt");
        std::fs::write(&errs, "error: lstat /home/bob/.cache/x: permission denied\n").unwrap();
        let sys = Sys::new(t.root(), Box::new(FakeRunner::default().with("uname -s -r -v -m", "Linux 7.0.0-30-generic #30 SMP x86_64\n")), true);
        let a = Args { stderr: vec![errs.display().to_string()], ..Args::default() };
        let b = build(&sys, &a, "2026-11-07T03:42:00Z");
        let txt = &b.text;
        for gone in ["/home/bob", "5d1c0e9a", "52:54:00:9c:4e:17"] {
            assert!(!txt.contains(gone), "{gone} in:\n{txt}");
        }
        assert!(txt.contains("in: /home/user1\n"), "{txt}");
        assert!(txt.contains("==== plan for that run, of /home/user1/layout.json"), "{txt}");
        assert!(txt.contains("error: lstat /home/user1/.cache/x: permission denied"), "{txt}");
        assert!(txt.contains("(checked: the plan of the redacted layout is the original's"), "{txt}");
        assert!(b.terminal.contains("bob -> user1"), "{}", b.terminal);
    }

    /// RT10i: `--attach`, `--strip-file`, the plan document's disks learned,
    /// a recorded --json run re-run in text mode, `ISSUES` and the
    /// `--no-redact` line.
    #[test]
    fn rescue_attachments_and_stripped_strings() {
        let a = parse_args(&s(&["--attach", "ev", "--attach", "plan.json", "--strip-file", "k/strip", "--strip-file", "k/more"])).unwrap().unwrap();
        assert_eq!((a.attach.clone(), a.strip_files.clone()), (s(&["ev", "plan.json"]), s(&["k/strip", "k/more"])));
        let t = Tree::new("report-bug-attach");
        t.debian13();
        t.file("/proc/self/mountinfo", "").file("/proc/swaps", "Filename\tType\tSize\tUsed\tPriority\n");
        t.disk("vdb", "253:16", &[], 512).file("/sys/block/vdb/size", "33554432\n");
        t.file("/srv/alice/layout.json", crate::load::layout::sample::bios_mbr());
        // A --json writing run, recorded as load records one.
        let last = Obj::new()
            .set("cwd", "/srv/alice")
            .set("args", Value::Arr(s(&["layout.json", "--map", "vda=vdb", "--through", "close", "--commit", "--json", "--expect-plan", &"a".repeat(64)]).into_iter().map(Value::Str).collect()))
            .set("exit", 1u64)
            .build();
        t.file(&format!("{RUN_DIR}/{LAST_RUN}"), last.pretty());
        // This machine's disks as a plan document has them, in no dump.
        let plan = r##"{"format":"bko-holt-plan","schema":1,"candidates":[{"node":"sdc","by_id":"/dev/disk/by-id/usb-SanDisk_Ultra_4C530001150612110384-0:0","serial":"4C530001150612110384","wwid":null,"model":"SanDisk Ultra","contents":[{"dev":"sdc1","type":"ext4","label":"holiday-pics","size_bytes":1,"used_bytes":null}]},{"node":"sdd","by_id":"/dev/disk/by-id/wwn-0x5000c500a1b2c3d4","serial":"ZA1B2C3D","wwid":"naa.5000c500a1b2c3d4","model":"ST4000DM004"}],"text":"# sdd is ZA1B2C3D\n"}"##;
        let plan_path = t.root().join("plan.json");
        std::fs::write(&plan_path, format!("{plan}\n")).unwrap();
        let ev_path = t.root().join("events");
        std::fs::write(&ev_path, "{\"event\":\"start\",\"layout\":{\"hostname\":\"bobs-nas\"}}\n{\"event\":\"progress\",\"bytes\":1}\n{\"event\":\"warning\",\"text\":\"restore /srv/ZA1B2C3D.img: no xattrs\"}\n").unwrap();
        let log_path = t.root().join("rescue.log");
        std::fs::write(&log_path, "hint shown: Mittens the cat\nlocation: Grandma's attic\n").unwrap();
        let strip = t.root().join("strip");
        std::fs::write(&strip, "Mittens the cat\r\n\nGrandma's attic\nxy\n").unwrap();
        let sys = Sys::new(t.root(), Box::new(FakeRunner::default().with("uname -s -r -v -m", "Linux 7.0.0-30-generic #30 SMP x86_64\n")), true);
        let path = |p: &std::path::Path| p.display().to_string();
        let a = Args { attach: vec![path(&plan_path), path(&ev_path), path(&log_path)], strip_files: vec![path(&strip)], ..Args::default() };
        let b = build(&sys, &a, "2026-11-24T12:00:00Z");
        let txt = &b.text;
        for p in [&plan_path, &ev_path, &log_path] {
            assert!(txt.contains(&format!("==== attached: {}", path(p))), "{txt}");
        }
        for gone in ["4C530001150612110384", "holiday-pics", "ZA1B2C3D", "5000c500a1b2c3d4", "bobs-nas", "Mittens", "Grandma"] {
            assert!(!txt.contains(gone), "{gone} in:\n{txt}");
            assert!(!b.terminal.contains(gone) || !["Mittens", "Grandma"].contains(&gone), "{gone} on the terminal:\n{}", b.terminal);
        }
        assert!(txt.contains("hint shown: stripped1\n") && txt.contains("location: stripped2\n"), "{txt}");
        assert!(txt.contains("model\":\"SanDisk Ultra"), "models are kept: {txt}");
        assert!(b.notes.iter().any(|n| n.contains("1 line(s) under 3 characters")), "{:?}", b.notes);
        // The --json run's plan is a text plan: no --json, no --expect-plan.
        assert!(txt.contains("command: bko-holt load layout.json --map vda=vdb --through close --commit --json --expect-plan"), "{txt}");
        assert!(txt.contains("bko-holt load layout.json --map vda=vdb -- exit "), "{txt}");
        assert!(txt.contains("#   vda (16 GiB) -> /dev/vdb"), "{txt}");
        // --no-redact: the line names every kind; --strip-file is ignored.
        let a = Args { no_redact: true, strip_files: vec![path(&strip)], ..Args::default() };
        let b = build(&sys, &a, "2026-11-24T12:00:00Z");
        let first = b.text.lines().nth(1).unwrap();
        assert!(first.starts_with("NOT redacted: it holds this machine's hostnames, machine-ids, UUIDs and GUIDs, "), "{first}");
        for k in crate::redact::Kind::ALL {
            assert!(first.contains(k.plural().split(" (").next().unwrap()), "{k:?} not in {first:?}");
        }
        assert!(first.ends_with("other identifiers, repositories and password-file paths, as they are."), "{first}");
        assert!(b.notes.iter().any(|n| n.contains("--strip-file is ignored with --no-redact")), "{:?}", b.notes);
    }

    /// RT10i review: the repository, the password file and the key files
    /// an attached document names are learned from it, with no
    /// last-load.json (a --json plan run records none): a plan's `args`
    /// and its archiver argv, a `cmd` and a `failed` event's argv, a log's
    /// command line.  Help's placeholders ("--password-file FILE") stay.
    #[test]
    fn attached_documents_repository_learned() {
        let t = Tree::new("report-bug-attach-repo");
        t.debian13();
        t.file("/proc/self/mountinfo", "").file("/proc/swaps", "Filename\tType\tSize\tUsed\tPriority\n");
        let repo = "sftp://zelda@zeldas-nas.example.org/srv/bko/zelda";
        let (pw, key) = ("/run/bko-rescue/k/1", "/run/bko-rescue/k/0");
        let args: Vec<Value> = ["load", "layout.json", "--map", "vda=vdb", "--repo", repo, "--password-file", pw, "--key-file", &format!("cryptroot={key}"), "--json"].iter().map(|x| Value::Str(x.to_string())).collect();
        let argv = |r: &str| -> Value { Value::Arr(["rustic-bko", "--repository", r, "--password-file", pw, "restore", "latest", "/mnt/bko-target"].iter().map(|x| Value::Str(x.to_string())).collect()) };
        let cmd = Obj::new().set("kind", "run").set("argv", argv(repo)).set("archiver", true).build();
        let plan = Obj::new()
            .set("format", "bko-holt-plan")
            .set("schema", 1u64)
            .set("args", Value::Arr(args))
            .set("stages", vec![Obj::new().set("name", "restore").set("commands", vec![cmd]).build()])
            .set("problems", vec![Obj::new().set("code", "password_needed").set("text", "--repo: give the repository's password with --password-file FILE").build()])
            .set("text", "# --password-file FILE\n")
            .build();
        // Each document on its own: the events name another repository.
        let other = "rest:https://nas.zelda.example.org:8000/zelda";
        let ev = [Obj::new().set("event", "cmd").set("stage", "restore").set("argv", argv(other)).build(), Obj::new().set("event", "failed").set("stage", "restore").set("argv", argv("/media/zelda/bko-repo")).build()];
        let plan_path = t.root().join("plan.json");
        std::fs::write(&plan_path, plan.pretty()).unwrap();
        let ev_path = t.root().join("events");
        std::fs::write(&ev_path, ev.iter().map(|v| v.compact() + "\n").collect::<String>()).unwrap();
        let log_path = t.root().join("rescue.log");
        std::fs::write(&log_path, "ran: bko-holt load /run/bko-rescue/layout.json --repo 's3:https://s3.zelda.example.org/bucket' --password-file FILE\n").unwrap();
        let sys = Sys::new(t.root(), Box::new(FakeRunner::default().with("uname -s -r -v -m", "Linux 7.0.0-30-generic #30 SMP x86_64\n")), true);
        let path = |p: &std::path::Path| p.display().to_string();
        let a = Args { attach: vec![path(&plan_path), path(&ev_path), path(&log_path)], ..Args::default() };
        let b = build(&sys, &a, "2026-11-24T12:00:00Z");
        let txt = &b.text;
        assert!(txt.contains("none recorded"), "{txt}");
        for gone in [repo, pw, key, other, "/media/zelda/bko-repo", "s3:https://s3.zelda.example.org/bucket", "zelda"] {
            assert!(!txt.contains(gone), "{gone} in:\n{txt}");
        }
        assert!(txt.contains("/run/bko-rescue/k/keyfile1"), "{txt}");
        assert!(txt.contains("--password-file FILE"), "help's placeholder is kept: {txt}");
        assert!(txt.contains("REDACTED1") && txt.contains("REDACTED5"), "{txt}");
    }

    #[test]
    fn plan_args_are_a_text_runs() {
        let sha = "b".repeat(64);
        assert_eq!(plan_args(&s(&["l", "--json", "--through", "close", "--commit", "--expect-plan", &sha, "--map", "a=b"])), s(&["l", "--map", "a=b"]));
        assert_eq!(plan_args(&s(&["l", "--json=1", "--only", "status"])), s(&["l"]));
        // A value is kept whatever it reads.
        assert_eq!(plan_args(&s(&["l", "--repo", "/r", "--snapshot", "--json", "--key-file", "--commit"])), s(&["l", "--repo", "/r", "--snapshot", "--json", "--key-file", "--commit"]));
    }

    #[test]
    fn issues_and_the_help() {
        assert_eq!(ISSUES, "https://github.com/mchobbit/backotter/issues");
        assert!(USAGE.contains(&format!("an issue at {ISSUES} :")) && !USAGE.contains("/new/choose"), "{USAGE}");
        for f in ["--attach FILE", "--strip-file FILE"] {
            assert!(USAGE.contains(f), "{f} not in the help");
        }
    }

    #[test]
    fn stderr_tail_cuts_at_a_character() {
        let t = "é".repeat(40);
        let cut = tail_bytes(&t, 11);
        assert_eq!(cut, "é".repeat(5));
        assert_eq!(tail_bytes("abc", 10), "abc");
    }

    #[test]
    fn linux_image_marker_on_cdrom() {
        let t = Tree::new("report-bug-cdrom");
        t.debian13();
        t.file("/cdrom/.disk/backotter.json", r#"{"image_id": "6f1c2c1e-5b0e-4a51-9d8e-2a3c8f4e7b10", "platform": "linux"}"#);
        let sys = Sys::new(t.root(), Box::new(FakeRunner::default().with("uname -s -r -v -m", "Linux 7.0.0-43-generic #43 SMP x86_64\n")), true);
        let (v, _) = versions(&sys);
        assert!(v.contains("\"platform\":\"linux\"") && v.contains("(/cdrom/.disk/backotter.json)"), "{v}");
    }

    #[test]
    fn status_of_disks_matched_by_serial() {
        let t = Tree::new("report-bug-serial");
        t.debian13();
        t.file("/proc/self/mountinfo", "").file("/proc/swaps", "Filename\tType\tSize\tUsed\tPriority\n");
        t.disk("vdb", "253:16", &[], 512).file("/sys/block/vdb/size", "33554432\n");
        t.file("/sys/block/vdb/device/model", "QEMU HARDDISK\n").file("/sys/block/vdb/device/serial", "ALICE-SER-77\n");
        let layout = crate::load::layout::sample::bios_mbr()
            .replace("\"deb13\"", "\"alice-pc\"")
            .replacen("\"model\": null, \"serial\": null", "\"model\": \"QEMU HARDDISK\", \"serial\": \"ALICE-SER-77\"", 1);
        assert!(layout.contains("ALICE-SER-77"));
        t.file("/srv/alice/layout.json", &layout);
        let last = Obj::new()
            .set("cwd", "/srv/alice")
            .set("args", Value::Arr(s(&["layout.json", "--through", "format", "--commit"]).into_iter().map(Value::Str).collect()))
            .set("exit", 1u64)
            .build();
        t.file(&format!("{RUN_DIR}/{LAST_RUN}"), last.pretty());
        let sys = Sys::new(t.root(), Box::new(FakeRunner::default().with("uname -s -r -v -m", "Linux 7.0.0-30-generic #30 SMP x86_64\n")), true);
        let b = build(&sys, &Args::default(), "2026-11-07T03:42:00Z");
        let st = b.text.split("==== status of the target disks").nth(1).unwrap().split("\n==== ").next().unwrap();
        assert!(st.contains("given here as --map vda=vdb"), "{st}");
        assert!(!st.contains("has no target"), "{st}");
        assert!(!b.text.contains("ALICE-SER-77"), "{}", b.text);
    }

    #[test]
    fn big_images_are_left_out() {
        let big = crate::bytes::base64(&vec![7u8; 6000]);
        let v = crate::json::parse(&format!(r#"{{"p": {{"esp_image": [{{"offset": 0, "b64": "{big}"}}], "boot_code": {{"code_b64": "QlRY"}}}}}}"#)).unwrap();
        let s = slim(&v);
        let c = s.at(&["p", "esp_image", "0", "b64_omitted"]).unwrap();
        assert_eq!(c.get("bytes").and_then(Value::as_u64), Some(6000));
        assert!(s.at(&["p", "esp_image", "0", "b64"]).is_none());
        assert_eq!(s.at(&["p", "boot_code", "code_b64"]).and_then(Value::as_str), Some("QlRY"));
        assert!(load::has_omitted(&s) && !load::has_omitted(&v));
    }
}
