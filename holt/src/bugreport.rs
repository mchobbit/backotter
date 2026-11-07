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

pub const ISSUES: &str = "https://github.com/mchobbit/backotter/issues/new/choose";

/// Where `load` records its last run, for this command to find.
pub const LAST_RUN: &str = "last-load.json";

/// Lines kept from each log.
const LOG_LINES: usize = 3000;

pub const USAGE: &str = "Usage: bko-holt report-bug [--output FILE] [--stderr FILE]... [--no-redact]
                          [-- LOAD-ARGUMENTS...]

Gathers what a BackOtter bug report needs into one text file to attach to
an issue at https://github.com/mchobbit/backotter/issues/new/choose :

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

GitHub issues are public, so by default every hostname, machine-id, UUID,
serial number, label, user name, key-file path, MAC address, repository and
password-file path gets a pseudonym (host-1, label2, bc000000-...-0003),
the same one everywhere in the file, so references still resolve.  Sizes,
offsets, feature flags, types and the device topology stay as they are.
It prints what it redacted, what it kept, and the file's path.  Read the
file before you attach it: it is plain text.

  --output FILE   write the report here (default: bko-report-DATE.txt in
                  the current directory)
  --stderr FILE   also include this file of error output (repeatable)
  --no-redact     leave identifiers as they are (for a private report)
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
    pub no_redact: bool,
    pub load_args: Option<Vec<String>>,
    /// For tests: read the system from this tree, running no commands.
    pub sysroot: Option<String>,
}

pub fn parse_args(args: &[String]) -> Result<Option<Args>, String> {
    let mut a = Args::default();
    let mut it = args.iter();
    while let Some(x) = it.next() {
        let mut value = |flag: &str| it.next().cloned().ok_or_else(|| format!("{flag} needs a value"));
        match x.as_str() {
            "-h" | "--help" => return Ok(None),
            "--output" | "-o" => a.output = Some(value(x)?),
            "--stderr" => a.stderr.push(value(x)?),
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
    let b = build(&sys, &a, &now);
    let path = a.output.clone().unwrap_or_else(|| format!("bko-report-{}.txt", now.replace(['-', ':'], "").replace('T', "-").trim_end_matches('Z')));
    if let Err(e) = std::fs::write(&path, &b.text) {
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

fn read_any(sys: &Sys, path: &str) -> Option<String> {
    if path.starts_with('/') {
        sys.read(path)
    } else {
        std::fs::read_to_string(path).ok()
    }
}

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
    let _ = std::fs::create_dir_all(RUN_DIR);
    let _ = std::fs::write(format!("{RUN_DIR}/{LAST_RUN}"), v.pretty());
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

/// The plan stage of a load run's arguments (whatever stage it ran).
fn plan_args(args: &[String]) -> Vec<String> {
    let mut out = Vec::new();
    let mut it = args.iter();
    while let Some(a) = it.next() {
        match a.as_str() {
            "--commit" => {}
            "--through" | "--only" => {
                it.next();
            }
            _ => out.push(a.clone()),
        }
    }
    out
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
    let run = last_run(sys, a);
    match &run {
        None => parts.push(Part {
            title: "last bko-holt load run".into(),
            body: Body::Text(format!("none recorded ({RUN_DIR}/{LAST_RUN} isn't there); give load's arguments after -- to report one\n")),
        }),
        Some(r) => {
            let mut t = format!("from {}{}\n", r.source, r.time.as_ref().map(|t| format!(", {t}")).unwrap_or_default());
            t += &format!("command: bko-holt load {}\n", r.args.iter().map(|x| load::shell_quote(x)).collect::<Vec<_>>().join(" "));
            if let Some(cwd) = &r.cwd {
                t += &format!("in: {cwd}\n");
            }
            if let Some(e) = r.exit {
                t += &format!("exit status: {e}\n");
            }
            if let Some(err) = &r.stderr {
                t += &format!("---- its error output\n{}", if err.is_empty() { "(none)\n".to_string() } else { err.clone() });
                c.see_text(err);
            }
            parts.push(Part { title: "last bko-holt load run".into(), body: Body::Text(t) });
            match load::parse_args(&plan_args(&r.args)) {
                Ok(Parsed::Run(mut o)) => {
                    if !o.layout.starts_with('/') {
                        if let Some(cwd) = &r.cwd {
                            o.layout = format!("{}/{}", cwd.trim_end_matches('/'), o.layout);
                        }
                    }
                    match read_any(sys, &o.layout) {
                        Some(text) => match crate::json::parse(&text) {
                            Ok(v) => layout = Some((o.layout.clone(), v, text)),
                            Err(e) => notes.push(format!("{}: not JSON ({}); its plan isn't in the report", o.layout, e.message)),
                        },
                        None => notes.push(format!("{}: can't read it; its plan isn't in the report", o.layout)),
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
            c.see_text(&t);
            logs.push((p, tail(&t, LOG_LINES)));
        }
    }
    let mut saved: Vec<(String, String)> = Vec::new();
    for p in &a.stderr {
        match std::fs::read(p) {
            Ok(b) => {
                let t = String::from_utf8_lossy(&b).into_owned();
                c.see_text(&t);
                saved.push((p.clone(), tail(&t, LOG_LINES)));
            }
            Err(e) => notes.push(format!("--stderr {p}: {e}")),
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
        let mut body = format!("bko-holt load {} -- exit {code}\n", plan_args(&run.as_ref().unwrap().args).iter().map(|x| load::shell_quote(x)).collect::<Vec<_>>().join(" "));
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

    let mut text = format!("BackOtter bug report, made by bko-holt report-bug {} at {now}\n", env!("CARGO_PKG_VERSION"));
    text += if a.no_redact {
        "NOT redacted: it holds this machine's hostnames, UUIDs, serials and labels.\n"
    } else {
        "Redacted: identifiers are replaced by pseudonyms (host-1, label2, bc000000-0000-4000-8000-000000000003),\nthe same one everywhere in this file; the last section says what was and wasn't.\n"
    };
    text += "Sections start with \"==== \"; a JSON section is one document, for `bko-holt load`.\n";
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
            Body::Json(v) => text += &r.value(v).pretty(),
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
        for sect in [
            "==== versions", "==== last bko-holt load run", "==== plan for that run, of /srv/alice/layout.json", "==== status of the target disks",
            "==== layout: /srv/alice/layout.json", "==== dump of this system", "==== log: /run/bko-holt/log/load.log", "==== redaction", "==== end",
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
}
