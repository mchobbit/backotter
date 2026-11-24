//! bko-holt's JSON protocol for bko-rescue (docs/design/holt-json.md): the
//! documents `--json` writes, and the dry-run lock (`BKO_HOLT_NO_COMMIT`).
//!
//! Built so far (RT10a): finding `--json` in an argument list by each
//! command's own table of the flags that take a value, the `bko-holt-error`
//! document, `bko-holt --version --json`'s `bko-holt-api` document, the
//! dry-run lock, and writing a document on standard output without ever
//! panicking.  Text mode never comes here: everything is behind `--json` or
//! the variable.

use crate::json::{Obj, Value};
use std::io::Write;

/// The one schema of each document this bko-holt writes (J2).
pub const SCHEMA: u64 = 1;

/// The dry-run lock (rescue-tui.md's D14, the second of its two locks).
pub const NO_COMMIT_VAR: &str = "BKO_HOLT_NO_COMMIT";

/// What `features` in the api document names: the parts of the protocol
/// this bko-holt has, so a screen needing a later one can say "not on this
/// stick".  Grows with RT10's steps.
pub const FEATURES: &[&str] = &["no_commit_env"];

/// The documents this bko-holt writes, each with the schemas it writes.
/// Grows with RT10's steps (plan: RT10c, status: RT10e, events: RT10f).
pub const DOCUMENTS: &[(&str, &[u64])] = &[("error", &[SCHEMA])];

/// `bko-holt-error`'s codes (holt-json.md, "bko-holt-error").
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ErrorCode {
    BadArgs,
    NoCommit,
    LayoutUnreadable,
    LayoutNotJson,
    LayoutRefused,
    ArchMismatch,
    SchemaUnknown,
    JsonNotHere,
}

impl ErrorCode {
    pub const ALL: [ErrorCode; 8] = [
        ErrorCode::BadArgs,
        ErrorCode::NoCommit,
        ErrorCode::LayoutUnreadable,
        ErrorCode::LayoutNotJson,
        ErrorCode::LayoutRefused,
        ErrorCode::ArchMismatch,
        ErrorCode::SchemaUnknown,
        ErrorCode::JsonNotHere,
    ];

    pub fn as_str(self) -> &'static str {
        match self {
            ErrorCode::BadArgs => "bad_args",
            ErrorCode::NoCommit => "no_commit",
            ErrorCode::LayoutUnreadable => "layout_unreadable",
            ErrorCode::LayoutNotJson => "layout_not_json",
            ErrorCode::LayoutRefused => "layout_refused",
            ErrorCode::ArchMismatch => "arch_mismatch",
            ErrorCode::SchemaUnknown => "schema_unknown",
            ErrorCode::JsonNotHere => "json_not_here",
        }
    }
}

/// BKO_HOLT_NO_COMMIT's value when it is in force: set, not empty and not
/// `0`; any other value counts, so a typo can't turn the lock off (J12).
/// A value that isn't UTF-8 counts too, shown lossily.
pub fn no_commit_value(v: Option<&std::ffi::OsStr>) -> Option<String> {
    let v = v?;
    if v.is_empty() || v == "0" {
        return None;
    }
    Some(v.to_string_lossy().into_owned())
}

/// BKO_HOLT_NO_COMMIT's value, when it is in force for this process.
pub fn no_commit() -> Option<String> {
    no_commit_value(std::env::var_os(NO_COMMIT_VAR).as_deref())
}

/// Whether this is a dry run: every document's `dry_run`.
pub fn dry_run() -> bool {
    no_commit().is_some()
}

/// The line load prints on standard error when the lock refuses --commit.
pub fn no_commit_refusal(value: &str) -> String {
    format!("bko-holt load: {NO_COMMIT_VAR}={value} is set (a dry run), so --commit is refused; nothing was written to any disk")
}

/// The line a hidden helper that writes prints when the lock is in force:
/// load never runs one without --commit, so only a bug or a hand would.
pub fn helper_refusal(helper: &str) -> String {
    format!("bko-holt {helper}: {NO_COMMIT_VAR} is set (a dry run), so nothing is written")
}

/// The line a command that writes a file where it's told prints when the
/// lock is in force and that file is already there (a device included).
pub fn exists_refusal(command: &str, path: &str) -> String {
    format!("bko-holt {command}: {path}: {NO_COMMIT_VAR} is set (a dry run), so only a new file is written, and this one is there already")
}

/// The first `--json` or `--json=N` in ARGS that stands in a flag's place:
/// an argument that is the value of one of VALUE_FLAGS (`--snapshot
/// --json` names a snapshot) isn't one.  The walk ends at `--`, after
/// which the arguments are another command's (report-bug's load
/// arguments).
pub fn find_json_flag<'a>(args: &'a [String], value_flags: &[&str]) -> Option<&'a str> {
    let mut it = args.iter();
    while let Some(a) = it.next() {
        let a = a.as_str();
        if a == "--" {
            return None;
        }
        if is_json_flag(a) {
            return Some(a);
        }
        if value_flags.contains(&a) {
            it.next();
        }
    }
    None
}

/// Whether A is `--json` or `--json=N`.
pub fn is_json_flag(a: &str) -> bool {
    a == "--json" || a.starts_with("--json=")
}

/// Whether FLAG (`--json` or `--json=N`) asks for a schema this bko-holt
/// writes: plain `--json` means the oldest still written (J2), and only
/// schema 1 is.  The error is text mode's message without the program's
/// name.
pub fn check_schema(flag: &str) -> Result<(), String> {
    match flag.strip_prefix("--json=") {
        None => Ok(()),
        Some(n) if n.parse::<u64>().ok() == Some(SCHEMA) => Ok(()),
        Some(_) => Err(format!("{flag}: this bko-holt writes schema {SCHEMA} of its JSON documents only")),
    }
}

/// The fields every top-level document starts with.
pub fn header(format: &str) -> Obj {
    Obj::new()
        .set("format", format)
        .set("schema", SCHEMA)
        .set("version", env!("CARGO_PKG_VERSION"))
        .set("generated", crate::timestamp())
        .set("dry_run", dry_run())
}

/// One document as standard output carries it: one line, scrubbed of
/// recovery keys as text plans are.
pub fn line(v: &Value) -> String {
    crate::redact::scrub_recovery_keys(&v.compact()).into_owned() + "\n"
}

/// The `bko-holt-error` document: the whole of standard output when a run
/// can't produce its own.  TEXT is the line text mode prints on standard
/// error, without the usage after it (a trailing newline is dropped).
pub fn error_doc(exit: u8, code: ErrorCode, text: &str) -> String {
    let v = header("bko-holt-error").set("exit", exit).set("code", code.as_str()).set("text", text.trim_end_matches('\n')).build();
    line(&v)
}

/// The `bko-holt-api` document, `bko-holt --version --json`'s (J1).
pub fn api_doc() -> String {
    let mut docs = Obj::new();
    for (name, schemas) in DOCUMENTS {
        docs.put(name, schemas.to_vec());
    }
    let platform = Obj::new().set("os", std::env::consts::OS).set("arch", std::env::consts::ARCH);
    let v = header("bko-holt-api")
        .set("documents", docs)
        .set("features", FEATURES.to_vec())
        .set("platform", platform)
        .set("run_dir", super::RUN_DIR)
        .set("no_commit", dry_run())
        .build();
    line(&v)
}

/// Writes TEXT on standard output, ignoring errors: a reader gone or a
/// full RAM disk must not panic the run (`print!` would), as the log's
/// writes ignore theirs.
pub fn write_stdout(text: &str) {
    let mut out = std::io::stdout().lock();
    let _ = out.write_all(text.as_bytes());
    let _ = out.flush();
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::ffi::OsStr;

    fn s(a: &[&str]) -> Vec<String> {
        a.iter().map(|x| x.to_string()).collect()
    }

    #[test]
    fn no_commit_values() {
        assert_eq!(no_commit_value(None), None);
        assert_eq!(no_commit_value(Some(OsStr::new(""))), None);
        assert_eq!(no_commit_value(Some(OsStr::new("0"))), None);
        for v in ["1", "yes", "true", "00", " 0", "0 ", "no", "false"] {
            assert_eq!(no_commit_value(Some(OsStr::new(v))).as_deref(), Some(v), "{v:?}");
        }
        use std::os::unix::ffi::OsStrExt;
        assert!(no_commit_value(Some(OsStr::from_bytes(b"\xff"))).is_some());
        assert_eq!(
            no_commit_refusal("1"),
            "bko-holt load: BKO_HOLT_NO_COMMIT=1 is set (a dry run), so --commit is refused; nothing was written to any disk"
        );
        assert_eq!(helper_refusal("raw-write"), "bko-holt raw-write: BKO_HOLT_NO_COMMIT is set (a dry run), so nothing is written");
    }

    #[test]
    fn json_in_a_flags_place_only() {
        let vf = &["--snapshot", "--map"];
        assert_eq!(find_json_flag(&s(&["l.json", "--json"]), vf), Some("--json"));
        assert_eq!(find_json_flag(&s(&["--json=1", "l.json"]), vf), Some("--json=1"));
        assert_eq!(find_json_flag(&s(&["l.json", "--snapshot", "--json"]), vf), None);
        assert_eq!(find_json_flag(&s(&["l.json", "--snapshot", "--json", "--json"]), vf), Some("--json"));
        assert_eq!(find_json_flag(&s(&["l.json", "--map", "--json", "--frob"]), vf), None);
        assert_eq!(find_json_flag(&s(&["--", "--json"]), vf), None);
        assert_eq!(find_json_flag(&s(&["--jsonx", "--JSON"]), vf), None);
        assert_eq!(find_json_flag(&s(&["l.json", "--snapshot"]), vf), None);
        assert_eq!(find_json_flag(&s(&[]), vf), None);
    }

    #[test]
    fn schemas() {
        assert!(check_schema("--json").is_ok());
        assert!(check_schema("--json=1").is_ok());
        for f in ["--json=2", "--json=0", "--json=", "--json=one", "--json=01x"] {
            assert_eq!(check_schema(f).unwrap_err(), format!("{f}: this bko-holt writes schema 1 of its JSON documents only"));
        }
    }

    #[test]
    fn error_document() {
        let codes: Vec<&str> = ErrorCode::ALL.iter().map(|c| c.as_str()).collect();
        assert_eq!(codes, ["bad_args", "no_commit", "layout_unreadable", "layout_not_json", "layout_refused", "arch_mismatch", "schema_unknown", "json_not_here"]);
        let d = error_doc(2, ErrorCode::BadArgs, "bko-holt load: unknown argument \"--frob\"\n");
        assert!(d.ends_with('\n') && d.matches('\n').count() == 1, "{d}");
        let v = crate::json::parse(&d).unwrap();
        assert_eq!(v.get_str("format"), Some("bko-holt-error"));
        assert_eq!(v.get_u64("schema"), Some(1));
        assert_eq!(v.get_str("version"), Some(env!("CARGO_PKG_VERSION")));
        assert!(v.get_str("generated").unwrap().ends_with('Z'));
        assert!(v.get_bool("dry_run").is_some());
        assert_eq!(v.get_u64("exit"), Some(2));
        assert_eq!(v.get_str("code"), Some("bad_args"));
        assert_eq!(v.get_str("text"), Some("bko-holt load: unknown argument \"--frob\""));
        let keys: Vec<&str> = v.as_object().unwrap().iter().map(|(k, _)| k.as_str()).collect();
        assert_eq!(keys, ["format", "schema", "version", "generated", "dry_run", "exit", "code", "text"]);
    }

    #[test]
    fn api_document() {
        let d = api_doc();
        assert_eq!(d.matches('\n').count(), 1);
        let v = crate::json::parse(&d).unwrap();
        assert_eq!(v.get_str("format"), Some("bko-holt-api"));
        assert_eq!(v.get_u64("schema"), Some(1));
        assert_eq!(v.get_str("version"), Some(env!("CARGO_PKG_VERSION")));
        let schemas = |doc: &str| v.at(&["documents", doc]).and_then(Value::as_array).map(|a| a.iter().filter_map(Value::as_u64).collect::<Vec<_>>());
        assert_eq!(schemas("error"), Some(vec![1]));
        // Only what this bko-holt writes is listed: a reader that needs a
        // plan stops before its first plan (J2).
        assert_eq!(v.get("documents").and_then(Value::as_object).map(|o| o.len()), Some(DOCUMENTS.len()));
        assert!(v.get_arr("features").unwrap().iter().any(|f| f.as_str() == Some("no_commit_env")));
        assert_eq!(v.str_at(&["platform", "os"]), Some(std::env::consts::OS));
        assert_eq!(v.str_at(&["platform", "arch"]), Some(std::env::consts::ARCH));
        assert_eq!(v.get_str("run_dir"), Some(super::super::RUN_DIR));
        assert_eq!(v.get_bool("no_commit"), v.get_bool("dry_run"));
    }
}
