//! bko-holt's JSON protocol for bko-rescue (docs/design/holt-json.md): the
//! documents `--json` writes, and the dry-run lock (`BKO_HOLT_NO_COMMIT`).
//!
//! Built so far: finding `--json` in an argument list by each command's
//! own table of the flags that take a value, the `bko-holt-error` document,
//! `bko-holt --version --json`'s `bko-holt-api` document, the dry-run lock,
//! and writing a document on standard output without ever panicking
//! (RT10a); the plan document and its hash (RT10c), with the size verdicts
//! of each candidate (RT10d).  The status document is status.rs's
//! (RT10e), made with header and line here.  Text mode never comes here:
//! everything is behind `--json` or the variable.

use super::commands::{Cmd, KeyAsk};
use super::layout::{Disk, Layout};
use super::plan::Planned;
use super::target::{self, Mapping, System, TargetDisk};
use super::Options;
use crate::json::{Obj, Value};
use crate::sys::Sys;
use std::io::Write;

/// The one schema of each document this bko-holt writes (J2).
pub const SCHEMA: u64 = 1;

/// The dry-run lock (rescue-tui.md's D14, the second of its two locks).
pub const NO_COMMIT_VAR: &str = "BKO_HOLT_NO_COMMIT";

/// What `features` in the api document names: the parts of the protocol
/// this bko-holt has, so a screen needing a later one can say "not on this
/// stick".  Grows with RT10's steps.
pub const FEATURES: &[&str] = &["no_commit_env", "candidates", "sizes"];

/// The documents this bko-holt writes, each with the schemas it writes.
/// Grows with RT10's steps (plan: RT10c, status: RT10e, events: RT10f).
pub const DOCUMENTS: &[(&str, &[u64])] = &[("plan", &[SCHEMA]), ("status", &[SCHEMA]), ("error", &[SCHEMA])];

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
    in_flag_place(args, value_flags).into_iter().find(|a| is_json_flag(a))
}

/// The first `--json=N` in a flag's place (as find_json_flag finds them)
/// that asks for a schema this bko-holt doesn't write, as check_schema
/// says it: wherever it stands, beside a good `--json` too.
pub fn schema_error(args: &[String], value_flags: &[&str]) -> Option<String> {
    in_flag_place(args, value_flags).into_iter().filter(|a| is_json_flag(a)).find_map(|a| check_schema(a).err())
}

/// Whether ARGS ask for the help (`-h` or `--help` in a flag's place): it
/// is for people, so `--json` beside it, wherever, prints the text help.
pub fn asks_for_help(args: &[String], value_flags: &[&str]) -> bool {
    in_flag_place(args, value_flags).into_iter().any(|a| a == "-h" || a == "--help")
}

/// Every argument of ARGS in a flag's place, up to `--`: an argument that
/// is the value of one of VALUE_FLAGS isn't one.
fn in_flag_place<'a>(args: &'a [String], value_flags: &[&str]) -> Vec<&'a str> {
    let mut out = Vec::new();
    let mut it = args.iter();
    while let Some(a) = it.next() {
        let a = a.as_str();
        if a == "--" {
            break;
        }
        if value_flags.contains(&a) {
            it.next();
        }
        out.push(a);
    }
    out
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

// ---- the plan document (RT10c, RT10d) ------------------------------------

/// A step's kind in the documents: run, write (a file load writes), check
/// (a read-back load makes in-process), note.
fn kind(c: &Cmd) -> &'static str {
    if c.check.is_some() {
        "check"
    } else if c.write.is_some() {
        "write"
    } else if c.is_note() {
        "note"
    } else {
        "run"
    }
}

/// A command's environment as it runs: LC_ALL=C first, which run_cmd adds
/// to every command, then the step's own.  Nothing for a step load makes
/// itself.
fn env_of(c: &Cmd) -> Value {
    if kind(c) != "run" {
        return Value::Arr(Vec::new());
    }
    let mut v = vec![Value::from(vec!["LC_ALL".to_string(), "C".to_string()])];
    v.extend(c.env.iter().map(|(k, x)| Value::from(vec![k.clone(), x.clone()])));
    Value::Arr(v)
}

/// A step as the plan document's `stages` carry it.  A private write's
/// text is never in it: its size and SHA-256 stand for it.
fn cmd_json(c: &Cmd) -> Value {
    let stdin = c.stdin.as_deref();
    Obj::new()
        .set("kind", kind(c))
        .set("argv", c.argv.clone())
        .set("env", env_of(c))
        .set("why", c.why.clone())
        .set("path", c.write.clone())
        .set("dev", c.check.as_ref().map(|(d, _)| d.clone()))
        .set("stdin", if c.private { None } else { stdin })
        .set("stdin_bytes", stdin.map(str::len))
        .set("stdin_sha256", stdin.map(|t| crate::bytes::sha256_hex(t.as_bytes())))
        .set("private", c.private)
        .set("archiver", c.archiver)
        .build()
}

/// What the kernel says of target T, as the partition record names it
/// (target::table_record_text): its serial and WWID from sysfs, empty when
/// its driver gives none.  Off Linux, the scan's.
fn kernel_identity(sys: &Sys, t: &TargetDisk) -> (String, String) {
    if crate::other::Family::current().is_some() {
        return (t.b.serial.clone().unwrap_or_default(), t.b.wwid.clone().unwrap_or_default());
    }
    let attr = |a: &str| sys.read_trim(&format!("/sys/block/{}/device/{a}", t.b.name)).unwrap_or_default();
    (attr("serial"), attr("wwid"))
}

/// The plan's hash (J20): SHA-256 over a canonical form of what the run
/// would do and to what: each stage's steps (kind, argv, environment, path,
/// device, and their input's SHA-256), and each target's node, by-id link,
/// size, kernel serial and WWID.  Not over the time, the text, the notes or
/// the candidates.
pub fn plan_sha256(built: &super::commands::Built, mapping: &Mapping, system: &System, sys: &Sys) -> String {
    let stages: Vec<Value> = built
        .stages
        .iter()
        .map(|(st, cmds)| {
            let cmds: Vec<Value> = cmds
                .iter()
                .map(|c| {
                    Obj::new()
                        .set("kind", kind(c))
                        .set("argv", c.argv.clone())
                        .set("env", env_of(c))
                        .set("path", c.write.clone())
                        .set("dev", c.check.as_ref().map(|(d, _)| d.clone()))
                        .set("stdin_sha256", c.stdin.as_deref().map(|t| crate::bytes::sha256_hex(t.as_bytes())))
                        .build()
                })
                .collect();
            Obj::new().set("stage", st.name()).set("commands", cmds).build()
        })
        .collect();
    let targets: Vec<Value> = mapping
        .pairs
        .iter()
        .filter_map(|(src, tgt)| {
            let t = system.disk(tgt)?;
            let (serial, wwid) = kernel_identity(sys, t);
            Some(Obj::new().set("source", src).set("node", tgt).set("by_id", target::by_id(t)).set("size_bytes", t.b.size_bytes).set("serial", serial).set("wwid", wwid).build())
        })
        .collect();
    crate::bytes::sha256_hex(Obj::new().set("stages", stages).set("targets", targets).build().compact().as_bytes())
}

/// What a disk here is, as the plan's targets and candidates both say it.
fn disk_json(t: &TargetDisk) -> Obj {
    Obj::new()
        .set("node", t.b.name.as_str())
        .set("path", t.path())
        .set("by_id", target::by_id(t))
        .set("size_bytes", t.b.size_bytes)
        .set("model", t.b.model.clone())
        .set("serial", t.b.serial.clone())
        .set("wwid", t.b.wwid.clone())
        .set("sector", t.b.logical_block_size.max(512))
        .set("transport", t.b.transport())
        .set("removable", t.b.removable)
}

/// This machine (`here`): its DMI UUID as the dump reads it, how it booted,
/// whether UEFI variables can be written here.
fn here_json(layout: &Layout, sys: &Sys) -> Value {
    use crate::other::Family;
    let fam = Family::current();
    let dmi = if fam.is_none() { sys.read_trim("/sys/class/dmi/id/product_uuid") } else { None };
    let bootmethod = |out: Option<String>| match out.map(|s| s.trim().to_ascii_lowercase()).as_deref() {
        Some("uefi") => Some("uefi"),
        Some("bios") => Some("bios"),
        _ => None,
    };
    let boot_mode: Option<&str> = match fam {
        None => Some(if sys.exists("/sys/firmware/efi") { "uefi" } else { "bios" }),
        Some(Family::FreeBsd | Family::NetBsd) => bootmethod(sys.run("sysctl", &["-n", "machdep.bootmethod"])),
        Some(Family::OpenBsd) => sys.run("dmesg", &[]).map(|t| if t.contains("efi0 at ") { "uefi" } else { "bios" }),
        Some(Family::Illumos) => sys.run("prtconf", &["-v"]).map(|t| if t.contains("efi-systab") { "uefi" } else { "bios" }),
        _ => None,
    };
    let efivars: Option<bool> = match fam {
        None => {
            let mounts = sys.read("/proc/self/mountinfo").map(|t| crate::mounts::parse_mountinfo(&t)).unwrap_or_default();
            match mounts.iter().find(|m| m.fstype == "efivarfs") {
                Some(m) => Some(m.options.split(',').any(|o| o == "rw")),
                None => Some(boot_mode == Some("uefi") && !sys.list("/sys/firmware/efi/efivars").is_empty()),
            }
        }
        // FreeBSD's efibootmgr writes them through the kernel's EFI runtime.
        Some(Family::FreeBsd) => boot_mode.map(|m| m == "uefi"),
        // load writes none elsewhere.
        Some(_) => Some(false),
    };
    let src = layout.str_at(&["host", "dmi", "product_uuid"]);
    let same = matches!((src, dmi.as_deref()), (Some(a), Some(b)) if a.eq_ignore_ascii_case(b));
    Obj::new()
        .set("dmi_uuid", dmi.clone())
        .set("boot_mode", boot_mode)
        .set("efivars", efivars)
        .set("arch", std::env::consts::ARCH)
        .set("privileged", sys.privileged)
        .set("same_machine", same)
        .build()
}

/// The source, from the dump (`source`).
fn source_json(layout: &Layout) -> Value {
    let v = &layout.v;
    let family = crate::other::Family::of_layout(v).map_or("linux", |f| f.name());
    let boot_mode = match layout.str_at(&["firmware", "mode"]).or(layout.str_at(&["boot", "loader", "firmware"])) {
        Some("unknown") => "unknown",
        _ if layout.uefi() => "uefi",
        _ => "bios",
    };
    let arch = layout.str_at(&["os", "machine"]).or(layout.str_at(&["os", "architecture"]));
    Obj::new()
        .set("hostname", layout.str_at(&["host", "hostname"]))
        .set("os", Obj::new().set("id", layout.str_at(&["os", "id"])).set("version_id", layout.str_at(&["os", "version_id"])).set("name", layout.os_name()))
        .set("family", family)
        .set("arch", arch)
        .set("boot_mode", boot_mode)
        .set("loader", layout.str_at(&["boot", "loader", "name"]))
        .set("generated", layout.str_at(&["generated"]))
        .set("dmi_uuid", layout.str_at(&["host", "dmi", "product_uuid"]))
        .set("reformat_possible", reformat_possible(v))
        .build()
}

/// Whether --reformat is built for this source at all: not off Linux, not
/// for ostree, ABRoot, NixOS or Guix (their own tools write what names
/// their filesystems).  A layout --reformat edited already is one it is.
fn reformat_possible(v: &Value) -> bool {
    if v.get("load_reformat").is_some() {
        return true;
    }
    let trial = super::reformat::work_out(v, &[("/".to_string(), "ext4".to_string())]);
    !trial.problems.iter().any(|p| matches!(p.str_field("reason"), Some("not_linux" | "own_tools")))
}

/// The mount points of the source's fstab (non-swap, absolute) that sit on
/// any of DEVS (source names) or on what's stacked under them.
fn mounts_on(layout: &Layout, devs: &[String]) -> Vec<String> {
    let below = |start: &str| -> Vec<String> {
        let mut all = vec![start.to_string()];
        let mut i = 0;
        while i < all.len() && i < 64 {
            for s in layout.slaves(&all[i]) {
                if !all.contains(&s) {
                    all.push(s);
                }
            }
            i += 1;
        }
        all
    };
    let mut out = Vec::new();
    for e in layout.arr(&["storage", "fstab"]) {
        let (Some(file), Some(r)) = (e.get_str("file"), e.get_str("resolves_to")) else { continue };
        if !file.starts_with('/') || e.get_str("type") == Some("swap") || out.iter().any(|m| m == file) {
            continue;
        }
        if below(r).iter().any(|x| devs.contains(x)) {
            out.push(file.to_string());
        }
    }
    out
}

/// The keys a person answers, grouped (holt-json.md, the plan's `keys`):
/// the builders' (LUKS, kept headers, GELI, softraid), then each ZFS
/// encryption root the plan makes.
fn keys_json(layout: &Layout, opts: &Options, p: &Planned) -> Value {
    let mut asks: Vec<KeyAsk> = p.built.keys.clone();
    for ds in layout.arr(&["storage", "zfs", "tools", "datasets"]) {
        let Some(name) = ds.get_str("name") else { continue };
        if ds.get_str("encryptionroot") != Some(name) {
            continue;
        }
        let pool = name.split('/').next().unwrap_or(name);
        let here = |n: &str| p.built.datasets.iter().any(|d| d == n);
        let renamed = format!("{}{}", target::temp_pool_name(pool), &name[pool.len()..]);
        if !here(name) && !here(&renamed) {
            continue;
        }
        let fmt = ds.get_str("keyformat").unwrap_or_default();
        let refused = p.problems.iter().any(|x| x.code == super::codes::ProblemCode::KeyNeeded && x.str_field("name") == Some(name));
        let unlock = match fmt {
            "passphrase" => "passphrase",
            _ if refused => "key_file_elsewhere",
            _ => "temporary",
        };
        let (min, max) = match fmt {
            "raw" => (32, 32),
            "hex" => (64, 64),
            _ => (8, 512),
        };
        let mut devices = Vec::new();
        for d in &layout.disks {
            for (dev, c) in std::iter::once((&d.name, &d.content)).chain(d.parts.iter().map(|x| (&x.name, &x.content))) {
                if c.get_str("type") == Some("zfs_member") && c.get_str("label") == Some(pool) {
                    devices.push(dev.clone());
                }
            }
        }
        asks.push(KeyAsk { kind: "zfs", group: name.to_string(), names: vec![name.to_string()], devices, provides: Vec::new(), unlock, given: opts.key_file(name).is_some(), min_bytes: min, max_bytes: max });
    }
    let mut groups: Vec<(String, Vec<&KeyAsk>)> = Vec::new();
    for a in &asks {
        match groups.iter_mut().find(|(g, members)| *g == a.group && members.first().is_some_and(|m| m.kind == a.kind)) {
            Some((_, members)) => members.push(a),
            None => groups.push((a.group.clone(), vec![a])),
        }
    }
    let datasets = layout.arr(&["storage", "zfs", "tools", "datasets"]);
    let out: Vec<Value> = groups
        .iter()
        .map(|(g, members)| {
            let first = members[0];
            let mut names: Vec<String> = Vec::new();
            let mut devices: Vec<String> = Vec::new();
            for m in members {
                for n in &m.names {
                    if !names.contains(n) {
                        names.push(n.clone());
                    }
                }
                for d in &m.devices {
                    if !devices.contains(d) {
                        devices.push(d.clone());
                    }
                }
            }
            let provides: Vec<String> = members.iter().flat_map(|m| m.provides.iter().cloned()).collect();
            let holds: Vec<String> = if first.kind == "zfs" {
                datasets
                    .iter()
                    .filter(|d| d.get_str("name").is_some_and(|n| n == g || n.starts_with(&format!("{g}/"))))
                    .filter_map(|d| d.get_str("mountpoint").filter(|m| m.starts_with('/')).map(str::to_string))
                    .collect()
            } else {
                mounts_on(layout, &provides)
            };
            let unlock = if members.iter().any(|m| m.unlock == "passphrase") { "passphrase" } else { first.unlock };
            Obj::new()
                .set("group", g.as_str())
                .set("kind", first.kind)
                .set("names", names)
                .set("devices", devices)
                .set("holds", holds)
                .set("unlock", unlock)
                .set("given", members.iter().all(|m| m.given))
                .set("new_ok", first.kind != "luks_kept_header")
                .set("min_bytes", members.iter().map(|m| m.min_bytes).max().unwrap_or(1))
                .set("max_bytes", members.iter().map(|m| m.max_bytes).min().unwrap_or(512))
                .set("shared_at_boot", members.len() > 1)
                .build()
        })
        .collect();
    Value::Arr(out)
}

/// The LUKS2 tokens the restore doesn't bring back (TPM2, FIDO2, a
/// recovery key's): each to enrol again.  A kept header brings its own.
fn tokens_json(layout: &Layout) -> Value {
    let v = &layout.v;
    let mut out = Vec::new();
    let mut devs: Vec<(String, Value)> = Vec::new();
    for sect in ["disks", "md_arrays", "dm_devices"] {
        for d in layout.arr(&["storage", sect]) {
            for x in std::iter::once(d).chain(d.get("partitions").and_then(Value::as_array).into_iter().flatten()) {
                if let (Some(n), Some(c)) = (x.get_str("name"), x.get("content")) {
                    devs.push((n.to_string(), c.clone()));
                }
            }
        }
    }
    for (name, c) in devs {
        if c.get_str("type") != Some("crypto_LUKS") || crate::luksheader::kept(v, &name).is_some() {
            continue;
        }
        let container = layout
            .arr(&["storage", "dm_devices"])
            .iter()
            .find(|d| d.at(&["dm", "target"]).and_then(Value::as_str) == Some("crypt") && d.get("slaves").and_then(Value::as_array).is_some_and(|s| s.iter().any(|x| x.as_str() == Some(name.as_str()))))
            .and_then(|d| d.at(&["dm", "dm_name"]).and_then(Value::as_str).map(str::to_string))
            .unwrap_or(name);
        for t in c.get("tokens").and_then(Value::as_array).into_iter().flatten() {
            out.push(Obj::new().set("container", container.as_str()).set("kind", t.get_str("type")).build());
        }
    }
    Value::Arr(out)
}

/// For each mount point --reformat could name, the types it could become
/// and, per type, null or the refusal it would get: reformat::work_out as a
/// trial.  With --reformat given, the trials are the dump's (LAYOUT_TEXT,
/// as read), each with the run's other --reformat choices beside it, and
/// `type` the dump's.  Empty where --reformat isn't built for the source,
/// and for a layout that was edited by --reformat before it was given.
fn reformat_json(layout: &Layout, layout_text: &str, opts: &Options) -> Value {
    let dump;
    let layout = if layout.v.get("load_reformat").is_some() && !opts.reformats.is_empty() {
        match crate::json::parse(layout_text).ok().and_then(|j| Layout::from_json(j).ok()) {
            Some(l) => {
                dump = l;
                &dump
            }
            None => return Value::Arr(Vec::new()),
        }
    } else {
        layout
    };
    let v = &layout.v;
    if v.get("load_reformat").is_some() || !reformat_possible(v) {
        return Value::Arr(Vec::new());
    }
    let mut out = Vec::new();
    let mut seen: Vec<String> = Vec::new();
    for e in layout.arr(&["storage", "fstab"]) {
        let (Some(file), Some(r)) = (e.get_str("file"), e.get_str("resolves_to")) else { continue };
        if !file.starts_with('/') || e.get_str("type") == Some("swap") || seen.iter().any(|m| m == file) {
            continue;
        }
        let Some(ty) = layout.content(r).and_then(|c| c.get_str("type")).filter(|t| super::reformat::TYPES.contains(t)) else { continue };
        seen.push(file.to_string());
        let mut to = Obj::new();
        for other in super::reformat::TYPES.iter().filter(|t| **t != ty) {
            let mut choices: Vec<(String, String)> = opts.reformats.iter().filter(|(m, _)| m != file).cloned().collect();
            choices.push((file.to_string(), other.to_string()));
            let trial = super::reformat::work_out(v, &choices);
            // This mount's refusal (or one of the whole layout's), not
            // another choice's.
            let refusal = trial
                .problems
                .iter()
                .find(|p| p.str_field("mount").is_none_or(|m| m == file))
                .map(|p| Obj::new().set("code", p.code.as_str()).set("reason", p.str_field("reason").unwrap_or("other")).set("text", p.text.as_str()).build());
            to.put(other, refusal);
        }
        out.push(Obj::new().set("mount", file).set("type", ty).set("to", to).build());
    }
    Value::Arr(out)
}

/// What --fit-largest makes of each disk (Fit::report's figures).
fn fit_json(opts: &Options, fit: &super::fit::Fit) -> Value {
    let Some(pct) = opts.fit_largest else { return Value::Arr(Vec::new()) };
    let disks: Vec<Value> = fit
        .disks
        .iter()
        .map(|d| {
            let part = d.part.as_ref().map(|p| {
                Obj::new().set("number", p.number).set("name", p.name.as_str()).set("what", p.what.as_str()).set("old_sectors", p.old_sectors).set("new_sectors", p.new_sectors).build()
            });
            let moved: Vec<Value> = d.moved.iter().map(|(n, by)| Obj::new().set("number", *n).set("sectors", *by).build()).collect();
            let fits: Vec<Value> = d
                .fits
                .iter()
                .map(|f| Obj::new().set("dev", f.dev.as_str()).set("kind", f.kind.as_str()).set("size_bytes", f.size).set("shrink_bytes", f.shrink).set("used_bytes", f.used).set("free_bytes", f.free).set("need_bytes", f.need).build())
                .collect();
            Obj::new()
                .set("disk", d.disk.as_str())
                .set("target", d.target.as_str())
                .set("pct", pct)
                .set("sector", d.sector)
                .set("old_bytes", d.old_size)
                .set("new_bytes", d.new_size)
                .set("old_last_sector", d.old_last)
                .set("new_last_sector", d.new_last)
                .set("partition", part)
                .set("moved", moved)
                .set("data", fits)
                .set("notes", d.notes.clone())
                .build()
        })
        .collect();
    Value::Arr(disks)
}

/// The `bko-holt-plan` document (holt-json.md): P, the --json run's checks
/// of LAYOUT (LAYOUT_TEXT as read), with TEXT, what text mode prints for
/// these arguments (J18), and CODE, the run's exit status.
pub fn plan_doc(layout: &Layout, opts: &Options, sys: &Sys, layout_text: &str, p: &Planned, code: u8, text: &str) -> String {
    let v = &layout.v;
    let facts = p.json.as_ref();
    let uuids = target::layout_uuids(layout);
    let used: Vec<&Disk> = layout.disks.iter().filter(|d| d.used).collect();
    let recorded = |node: &str| facts.is_some_and(|f| f.recorded.iter().any(|r| r == node));
    let sizes = |t: &TargetDisk| -> Vec<Value> { used.iter().map(|d| super::fit::verdict(v, &d.name, &t.b.name, t.b.size_bytes).to_json()).collect() };
    let disks: Vec<Value> = used
        .iter()
        .map(|d| {
            let largest = super::fit::largest(v, &d.name).map(|(name, _, bytes)| {
                let at = super::fit::mount_of(v, &name);
                let used_bytes = layout.arr(&["storage", "mounts"]).iter().find(|m| at.is_some() && m.get_str("target") == at.as_deref()).and_then(|m| m.get_u64("used_bytes"));
                Obj::new().set("partition", name.as_str()).set("size_bytes", bytes).set("holds", super::fit::holds(v, &name)).set("mount", at).set("used_bytes", used_bytes).build()
            });
            let table = d.table.as_ref().map(|t| match t.kind {
                super::layout::TableKind::Gpt => "gpt",
                super::layout::TableKind::Mbr => "dos",
            });
            let mut devs: Vec<String> = vec![d.name.clone()];
            devs.extend(d.parts.iter().map(|x| x.name.clone()));
            let source = Obj::new()
                .set("name", d.name.as_str())
                .set("size_bytes", d.size_bytes)
                .set("model", d.model.clone())
                .set("serial", d.serial.clone())
                .set("wwid", d.wwid.clone())
                .set("sector", d.sector_size)
                .set("table", table)
                .set("mounts", mounts_on(layout, &devs))
                .set("largest", largest);
            let tgt = p.mapping.target_of(&d.name).and_then(|n| p.system.disk(n)).map(|t| {
                let node = t.b.name.as_str();
                let matched_by = if opts.maps.iter().any(|(s, _)| *s == d.name) {
                    "map"
                } else if d.wwid.as_deref().is_some_and(|w| !w.is_empty() && t.b.wwid.as_deref() == Some(w)) {
                    "wwid"
                } else {
                    "serial"
                };
                let verdict = super::fit::verdict(v, &d.name, node, t.b.size_bytes);
                let writes: Vec<&str> = facts.and_then(|f| f.writes.iter().find(|(n, _)| n == node)).map(|(_, w)| w.clone()).unwrap_or_default();
                disk_json(t)
                    .set("matched_by", matched_by)
                    .set("same_identity", target::same_identity(d, &t.b))
                    .set("blank", t.blank())
                    .set("contents", target::contents(sys, t))
                    .set("ours", t.is_ours(d, &uuids))
                    .set("recorded", recorded(node))
                    .set("erase", target::is_erased(opts, t))
                    .set("will_erase", facts.is_some_and(|f| f.partition_runs))
                    .set("writes", writes)
                    .set("size", (verdict.verdict != "same").then(|| verdict.to_json()))
                    .build()
            });
            Obj::new().set("source", source).set("target", tgt).build()
        })
        .collect();
    let candidates: Vec<Value> = p
        .system
        .disks
        .iter()
        .map(|t| {
            let node = t.b.name.as_str();
            let target_of = p.mapping.pairs.iter().find(|(_, x)| x == node).map(|(s, _)| s.clone());
            let ours = match target_of.as_deref().and_then(|s| layout.disk(s)) {
                Some(d) => t.is_ours(d, &uuids),
                None => used.iter().any(|d| t.is_ours(d, &uuids)),
            };
            let rec = if target_of.is_some() { recorded(node) } else { target::recorded_for_any(layout, t, sys) };
            let shared = t.b.serial.as_deref().filter(|s| !s.is_empty()).is_some_and(|sn| p.system.disks.iter().any(|o| o.b.name != t.b.name && o.b.serial.as_deref() == Some(sn)));
            let refused = target::refusal(layout, opts, &p.system, sys, t).map(|(c, text)| Obj::new().set("code", c.as_str()).set("text", text).build());
            disk_json(t)
                .set("serial_shared", shared)
                .set("target_of", target_of)
                .set("ours", ours)
                .set("blank", t.blank())
                .set("contents", target::contents(sys, t))
                .set("refused", refused)
                .set("erase_needed", !t.blank() && !rec)
                .set("sizes", sizes(t))
                .build()
        })
        .collect();
    let stages: Vec<Value> =
        p.built.stages.iter().map(|(st, cmds)| Obj::new().set("name", st.name()).set("commands", cmds.iter().map(cmd_json).collect::<Vec<_>>()).build()).collect();
    let mut args = vec!["load".to_string()];
    args.extend(opts.argv.iter().cloned());
    let doc = header("bko-holt-plan")
        .set("ok", p.problems.is_empty())
        .set("exit", code)
        .set("plan_sha256", facts.map(|f| f.plan_sha256.clone()))
        .set("layout", Obj::new().set("path", opts.layout.as_str()).set("sha256", crate::bytes::sha256_hex(layout_text.as_bytes())))
        .set("args", args)
        .set("source", source_json(layout))
        .set("here", here_json(layout, sys))
        .set("disks", disks)
        .set("candidates", candidates)
        .set("erase", opts.erase.clone())
        .set("keys", keys_json(layout, opts, p))
        .set("tokens", tokens_json(layout))
        .set("reformat", reformat_json(layout, layout_text, opts))
        .set("fit", fit_json(opts, &p.fit))
        .set("problems", p.problems.to_json())
        .set("notes", p.notes.to_json())
        .set("stages", stages)
        .set("text", text)
        .build();
    line(&doc)
}

/// Writes TEXT on standard output, ignoring errors: a reader gone or a/// Writes TEXT on standard output, ignoring errors: a reader gone or a
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
        // A bad schema wherever it stands; the help in a flag's place only.
        assert_eq!(schema_error(&s(&["l.json", "--json", "--json=2"]), vf).as_deref(), Some("--json=2: this bko-holt writes schema 1 of its JSON documents only"));
        assert_eq!(schema_error(&s(&["--json=1", "l.json", "--json"]), vf), None);
        assert_eq!(schema_error(&s(&["--snapshot", "--json=2", "--json"]), vf), None);
        assert_eq!(schema_error(&s(&["--", "--json=2"]), vf), None);
        assert!(asks_for_help(&s(&["--json=2", "--help"]), vf) && asks_for_help(&s(&["-h", "--json"]), vf));
        assert!(!asks_for_help(&s(&["--snapshot", "--help", "--json"]), vf) && !asks_for_help(&s(&["--json", "--", "-h"]), vf));
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

    // ---- the plan document (RT10c, RT10d) --------------------------------

    use crate::load::layout::sample;
    use crate::load::{parse_args, Parsed};
    use crate::sys::{FakeRunner, Sys};
    use crate::testutil::Tree;

    const TOOLS: [&str; 19] = [
        "wipefs", "dd", "sfdisk", "udevadm", "mke2fs", "tune2fs", "mkswap", "mkfs.btrfs", "btrfs", "mkdir", "mount", "umount", "chroot", "cp", "sh", "bash", "cryptsetup", "grub-install", "update-grub",
    ];

    /// A rescue system: vdb blank and 16 GiB (the sample's size), its udev
    /// by-id link and serial; vdc 8 GiB with an ext4 on it; sda the stick;
    /// every tool, and BackOtter's rustic-bko.
    fn rescue(name: &str) -> Tree {
        let t = Tree::new(name);
        t.disk("vdb", "253:16", &[], 512).file("/sys/block/vdb/size", "33554432\n");
        t.file("/run/udev/data/b253:16", "S:disk/by-id/virtio-SER1\nE:ID_SERIAL_SHORT=SER1\n");
        let ext = crate::probe::ext::sample::image(0x0004, 0x0002 | 0x0040, 0x0001, "photos");
        t.disk("vdc", "253:32", &[], 512).file("/sys/block/vdc/size", "16777216\n").file("/dev/vdc", &ext);
        t.disk("sda", "8:0", &[], 512).file("/sys/block/sda/size", "30000000\n");
        t.partition("sda", "sda3", "8:3", 3, 2048, 65536, &[]);
        t.file("/run/udev/data/b8:3", "E:ID_FS_TYPE=vfat\nE:ID_FS_LABEL=BACKOTTER\n");
        t.file("/proc/self/mountinfo", "").file("/proc/swaps", "Filename\tType\tSize\tUsed\tPriority\n");
        for p in TOOLS.iter().chain(["rustic-bko"].iter()) {
            t.file(&format!("/usr/sbin/{p}"), "");
        }
        t
    }

    fn sys_of(t: &Tree) -> Sys {
        Sys::new(t.root(), Box::new(FakeRunner::default().with("/usr/sbin/rustic-bko --version", "rustic 0.12.0+backotter.4\n")), true)
    }

    fn opts(a: &[&str]) -> crate::load::Options {
        let mut v = vec!["l.json".to_string()];
        v.extend(a.iter().map(|s| s.to_string()));
        match parse_args(&v).unwrap() {
            Parsed::Run(o) => o,
            Parsed::Help => unreachable!(),
        }
    }

    /// load::run with A: its exit, and its standard output as a document.
    fn plan(layout: &str, a: &[&str], s: &Sys) -> (u8, Value, String) {
        let (code, out, err) = crate::load::run(&opts(a), layout, s);
        assert!(out.ends_with('\n') && out.matches('\n').count() == 1, "not one line: {out}");
        (code, crate::json::parse(&out).unwrap_or_else(|e| panic!("{}: {out}", e.message)), err)
    }

    fn codes(v: &Value) -> Vec<String> {
        v.arr_at(&["problems"]).iter().filter_map(|p| p.get_str("code").map(str::to_string)).collect()
    }

    /// Every field of the plan document, in the spec's order, and what text
    /// mode prints for the same arguments in its `text`.
    #[test]
    fn plan_document() {
        let t = rescue("json-plan");
        let s = sys_of(&t);
        let l = sample::fittable();
        let a = ["--map", "vda=vdb", "--repo", "/srv/r", "--json"];
        let (code, v, err) = plan(&l, &a, &s);
        let keys: Vec<&str> = v.as_object().unwrap().iter().map(|(k, _)| k.as_str()).collect();
        assert_eq!(
            keys,
            [
                "format", "schema", "version", "generated", "dry_run", "ok", "exit", "plan_sha256", "layout", "args", "source", "here", "disks", "candidates", "erase", "keys", "tokens", "reformat", "fit",
                "problems", "notes", "stages", "text"
            ]
        );
        assert_eq!(v.get_str("format"), Some("bko-holt-plan"));
        // Under --json nothing asks: no --password-file is a problem.
        assert_eq!((code, v.get_bool("ok"), v.get_u64("exit")), (4, Some(false), Some(4)));
        assert_eq!(codes(&v), ["password_needed"]);
        // Standard error is text mode's, which finds no problem here.
        assert_eq!(err, "");
        // The text plan, byte for byte, as text mode prints it.
        let (tcode, text, terr) = crate::load::run(&opts(&a[..4]), &l, &s);
        assert_eq!((tcode, terr.as_str()), (0, err.as_str()));
        assert_eq!(v.get_str("text"), Some(text.as_str()));
        assert!(text.contains("--no-progress restore"), "{text}");
        assert_eq!(v.arr_at(&["args"]).iter().filter_map(Value::as_str).collect::<Vec<_>>(), ["load", "l.json", "--map", "vda=vdb", "--repo", "/srv/r", "--json"]);
        assert_eq!(v.str_at(&["layout", "path"]), Some("l.json"));
        assert_eq!(v.str_at(&["layout", "sha256"]), Some(crate::bytes::sha256_hex(l.as_bytes()).as_str()));
        let sha = v.get_str("plan_sha256").unwrap();
        assert!(sha.len() == 64 && sha.bytes().all(|b| b.is_ascii_hexdigit()), "{sha}");
        // The source and here.
        assert_eq!((v.str_at(&["source", "hostname"]), v.str_at(&["source", "family"]), v.str_at(&["source", "boot_mode"])), (Some("deb13"), Some("linux"), Some("bios")));
        assert_eq!((v.str_at(&["source", "loader"]), v.bool_at(&["source", "reformat_possible"])), (Some("grub"), Some(true)));
        assert_eq!((v.str_at(&["here", "boot_mode"]), v.bool_at(&["here", "efivars"]), v.bool_at(&["here", "same_machine"])), (Some("bios"), Some(false), Some(false)));
        // The one disk: its source, its largest partition and the target.
        let d = &v.arr_at(&["disks"])[0];
        assert_eq!(d.str_at(&["source", "name"]), Some("vda"));
        assert_eq!(d.str_at(&["source", "table"]), Some("dos"));
        assert_eq!(d.arr_at(&["source", "mounts"]).iter().filter_map(Value::as_str).collect::<Vec<_>>(), ["/", "/boot"]);
        assert_eq!((d.str_at(&["source", "largest", "partition"]), d.str_at(&["source", "largest", "holds"]), d.str_at(&["source", "largest", "mount"])), (Some("vda3"), Some("btrfs"), Some("/")));
        assert_eq!(d.u64_at(&["source", "largest", "used_bytes"]), Some(2147483648));
        let tg = d.get("target").unwrap();
        assert_eq!((tg.get_str("node"), tg.get_str("path"), tg.get_str("by_id")), (Some("vdb"), Some("/dev/vdb"), Some("/dev/disk/by-id/virtio-SER1")));
        assert_eq!((tg.get_str("serial"), tg.get_str("matched_by"), tg.get_bool("same_identity"), tg.get_bool("blank")), (Some("SER1"), Some("map"), Some(false), Some(true)));
        assert_eq!((tg.get_bool("ours"), tg.get_bool("recorded"), tg.get_bool("erase"), tg.get_bool("will_erase")), (Some(false), Some(false), Some(false), Some(true)));
        assert_eq!(tg.arr_at(&["writes"]).iter().filter_map(Value::as_str).collect::<Vec<_>>(), ["partition", "format"]);
        assert!(tg.get("size").is_some_and(Value::is_null), "the same size: {tg:?}");
        // The candidates: every disk here, the target among them.
        let c = |n: &str| v.arr_at(&["candidates"]).iter().find(|c| c.get_str("node") == Some(n)).cloned().unwrap_or_else(|| panic!("no {n}"));
        assert_eq!(v.arr_at(&["candidates"]).len(), 3);
        let vdb = c("vdb");
        assert_eq!((vdb.get_str("target_of"), vdb.get_bool("erase_needed"), vdb.get("refused").map(Value::is_null)), (Some("vda"), Some(false), Some(true)));
        assert_eq!(vdb.arr_at(&["sizes"])[0].get_str("verdict"), Some("same"));
        let stick = c("sda");
        assert_eq!(stick.str_at(&["refused", "code"]), Some("boot_disk"));
        let vdc = c("vdc");
        assert!(vdc.get("target_of").is_some_and(Value::is_null));
        assert_eq!((vdc.get_bool("blank"), vdc.get_bool("erase_needed"), vdc.get_u64("size_bytes")), (Some(false), Some(true), Some(8 << 30)));
        let ct = &vdc.arr_at(&["contents"])[0];
        assert_eq!((ct.get_str("dev"), ct.get_str("type"), ct.get_str("label")), (Some("vdc"), Some("ext4"), Some("photos")));
        assert!(ct.get_u64("used_bytes").is_some(), "{ct:?}");
        // Half the size: the 13 GiB root would have to give up 8 GiB of 13,
        // more than --fit-largest takes, and its 2 GiB fit: beyond_limit.
        let z = &vdc.arr_at(&["sizes"])[0];
        assert_eq!((z.get_str("source"), z.get_str("verdict"), z.get_bool("data_fits")), (Some("vda"), Some("beyond_limit"), Some(true)), "{z:?}");
        assert!(z.get_u64("fit_pct").is_some_and(|p| p > 50), "{z:?}");
        // Stages: the --json run's argv, the archiver tagged.
        let stages = v.arr_at(&["stages"]);
        assert_eq!(stages.iter().filter_map(|s| s.get_str("name")).collect::<Vec<_>>(), ["partition", "assemble", "format", "mount", "restore", "boot", "close"]);
        let restore = stages[4].arr_at(&["commands"]).iter().find(|c| c.get_bool("archiver") == Some(true)).cloned().unwrap();
        let argv: Vec<&str> = restore.arr_at(&["argv"]).iter().filter_map(Value::as_str).collect();
        assert_eq!(argv, ["rustic-bko", "--repository", "/srv/r", "--json-progress", "--progress-interval", "1s", "restore", "--numeric-id", "latest", "/mnt/bko-target"]);
        assert_eq!(restore.get("env").unwrap().compact(), r#"[["LC_ALL","C"],["RUSTIC_STRICT_EXIT","true"]]"#);
        assert_eq!(restore.get_str("kind"), Some("run"));
        // No key here, no token, no fit; the reformat options per mount.
        assert!(v.arr_at(&["keys"]).is_empty() && v.arr_at(&["tokens"]).is_empty() && v.arr_at(&["fit"]).is_empty());
        let rf = v.arr_at(&["reformat"]);
        let root = rf.iter().find(|r| r.get_str("mount") == Some("/")).unwrap();
        assert_eq!(root.get_str("type"), Some("btrfs"));
        assert!(root.at(&["to", "ext4"]).is_some() && root.at(&["to", "xfs"]).is_some(), "{root:?}");
        // With a --reformat given, the options are still the dump's: the
        // second plan's for / as the first's, its type the dump's.
        let (_, v3, _) = plan(&l, &["--map", "vda=vdb", "--repo", "/srv/r", "--reformat", "/=ext4", "--json"], &s);
        let root3 = v3.arr_at(&["reformat"]).iter().find(|r| r.get_str("mount") == Some("/")).cloned().unwrap_or_else(|| panic!("{:?}", v3.get("reformat")));
        assert_eq!(root3.compact(), root.compact());
        assert_eq!(v3.arr_at(&["reformat"]).len(), rf.len());
        // A password file that isn't there is unsafe; a private one is
        // what was missing: no problem, exit 0.
        let (code, v2, _) = plan(&l, &["--map", "vda=vdb", "--repo", "/srv/r", "--password-file", "/srv/pw", "--json"], &s);
        assert_eq!((code, codes(&v2)), (4, vec!["key_file_unsafe".to_string()]));
        t.file("/k/pw", "pw");
        use std::os::unix::fs::PermissionsExt;
        std::fs::set_permissions(t.p("/k/pw"), std::fs::Permissions::from_mode(0o600)).unwrap();
        std::fs::set_permissions(t.p("/k"), std::fs::Permissions::from_mode(0o700)).unwrap();
        let (code, v2, _) = plan(&l, &["--map", "vda=vdb", "--repo", "/srv/r", "--password-file", "/k/pw", "--json"], &s);
        assert_eq!((code, codes(&v2)), (0, Vec::<String>::new()));
        assert_eq!(v2.get_bool("ok"), Some(true));
    }

    /// The plan's hash binds the run to what was reviewed (J20): the same
    /// for the same plan; another for another target size or serial, and
    /// then --expect-plan with the old one is plan_changed.
    #[test]
    fn plan_hash() {
        let t = rescue("json-plan-hash");
        let s = sys_of(&t);
        let l = sample::bios_mbr();
        let a = ["--map", "vda=vdb", "--repo", "/srv/r", "--json"];
        let sha = |a: &[&str]| plan(&l, a, &s).1.get_str("plan_sha256").unwrap().to_string();
        let first = sha(&a);
        assert_eq!(sha(&a), first);
        let mut with = a.to_vec();
        with.extend(["--expect-plan", &first]);
        let (_, v, _) = plan(&l, &with, &s);
        assert!(!codes(&v).contains(&"plan_changed".to_string()), "{:?}", codes(&v));
        // The disk swapped for one of another size.
        t.file("/sys/block/vdb/size", "33554440\n");
        let (_, v, _) = plan(&l, &with, &s);
        let p = v.arr_at(&["problems"]).iter().find(|p| p.get_str("code") == Some("plan_changed")).cloned().unwrap_or_else(|| panic!("{:?}", codes(&v)));
        assert_eq!((p.get_str("expected"), p.get_str("got")), (Some(first.as_str()), v.get_str("plan_sha256")));
        t.file("/sys/block/vdb/size", "33554432\n");
        assert_eq!(sha(&a), first);
        // Its kernel serial.
        t.file("/sys/block/vdb/device/serial", "OTHER\n");
        assert_ne!(sha(&a), first);
        // A malformed one, or one without --json, is a bad argument.
        let args = |a: &[&str]| a.iter().map(|x| x.to_string()).collect::<Vec<_>>();
        assert!(parse_args(&args(&["l", "--json", "--expect-plan", "abc"])).unwrap_err().contains("64 lower-case hex digits"));
        assert!(parse_args(&args(&["l", "--expect-plan", &first])).unwrap_err().contains("is for --json runs"));
    }

    /// The erase gate (J19) under --json: the source's own disk as found
    /// (its table the layout's, so is_ours) needs --erase or this
    /// session's record; text mode takes it as before.
    #[test]
    fn erase_gate() {
        let t = rescue("json-plan-gate");
        let mut table = vec![0u8; 512];
        table[440..444].copy_from_slice(&0x1f2e_3d4cu32.to_le_bytes());
        for (slot, start, n) in [(0, 2048, 1953792), (1, 1955840, 3905536), (2, 5861376, 27693056)] {
            crate::parttable::build::mbr_entry(&mut table, slot, 0, 0x83, start, n);
        }
        t.file("/dev/vdb", &table);
        let s = sys_of(&t);
        let l = sample::bios_mbr();
        let a = ["--map", "vda=vdb", "--repo", "/srv/r", "--password-file", "/k/pw", "--json"];
        t.file("/k/pw", "pw");
        use std::os::unix::fs::PermissionsExt;
        std::fs::set_permissions(t.p("/k/pw"), std::fs::Permissions::from_mode(0o600)).unwrap();
        std::fs::set_permissions(t.p("/k"), std::fs::Permissions::from_mode(0o700)).unwrap();
        let (code, v, _) = plan(&l, &a, &s);
        assert_eq!((code, codes(&v)), (4, vec!["erase_required".to_string()]));
        let p = &v.arr_at(&["problems"])[0];
        assert_eq!((p.get_str("disk"), p.get_str("target"), p.get_bool("ours"), p.get_bool("same_identity")), (Some("vda"), Some("vdb"), Some(true), Some(false)));
        assert!(p.get_str("text").unwrap().contains("give --erase /dev/vdb to wipe it"), "{p:?}");
        let tg = v.at(&["disks", "0", "target"]).unwrap();
        assert_eq!((tg.get_bool("ours"), tg.get_bool("blank"), tg.get_bool("recorded")), (Some(true), Some(false), Some(false)));
        assert_eq!(v.at(&["candidates"]).unwrap().as_array().unwrap().iter().find(|c| c.get_str("node") == Some("vdb")).and_then(|c| c.get_bool("erase_needed")), Some(true));
        // Text mode: no gate, as before.
        let (tcode, out, _) = crate::load::run(&opts(&a[..6]), &l, &s);
        assert!(!out.contains("--erase /dev/vdb to wipe it"), "{tcode}: {out}");
        // --erase passes it, and the plan wipes.
        let mut erase = a.to_vec();
        erase.extend(["--erase", "/dev/vdb"]);
        let (code, v, _) = plan(&l, &erase, &s);
        assert_eq!(code, 0, "{:?}", codes(&v));
        assert_eq!(v.at(&["disks", "0", "target"]).and_then(|t| t.get_bool("will_erase")), Some(true));
        assert_eq!(v.arr_at(&["erase"]).iter().filter_map(Value::as_str).collect::<Vec<_>>(), ["/dev/vdb"]);
        // This session's record of the table: passes without --erase, and
        // the partition stage is done.
        let o = opts(&erase);
        let lay = crate::load::layout::Layout::from_json(crate::json::parse(&l).unwrap()).unwrap();
        let m = crate::load::target::prepare(&lay, &o, &s).mapping;
        let (path, text) = &m.records[0];
        t.file(path, text.as_str());
        let (code, v, _) = plan(&l, &a, &s);
        assert_eq!(code, 0, "{:?}", codes(&v));
        let tg = v.at(&["disks", "0", "target"]).unwrap();
        assert_eq!((tg.get_bool("recorded"), tg.get_bool("will_erase")), (Some(true), Some(false)));
        // A blank disk needs nothing.
        std::fs::remove_file(t.p(path)).unwrap();
        t.file("/dev/vdb", "");
        let (code, v, _) = plan(&l, &a, &s);
        assert_eq!(code, 0, "{:?}", codes(&v));
    }

    /// Two LUKS2 containers on the sample's disk, cryptswap (vda2) and
    /// cryptroot (vda3, holding /): crypttab's OPTIONS for both.
    fn two_luks(options: &str) -> String {
        let luks = |uuid: &str| format!(r#""content": {{"type": "crypto_LUKS", "usage": "crypto", "uuid": "{uuid}", "label": null, "version": "2", "tokens": [{{"type": "systemd-tpm2"}}]}}"#);
        sample::bios_mbr()
            .replace(r#""content": {"type": "swap", "usage": "other", "uuid": "5e5e5e5e-0000-4000-8000-000000000002", "label": null, "page_size": 4096}"#, &luks("a1a1a1a1-0000-4000-8000-000000000002"))
            .replacen(
                r#""content": {"type": "btrfs", "usage": "filesystem", "uuid": "b7b7b7b7-0000-4000-8000-000000000003""#,
                &format!(r#"{}, "x": {{"type": "btrfs", "usage": "filesystem", "uuid": "b7b7b7b7-0000-4000-8000-000000000003""#, luks("a3a3a3a3-0000-4000-8000-000000000003")),
                1,
            )
            .replace(
                r#""dm_devices": []"#,
                r#""dm_devices": [
                  {"name": "dm-0", "kind": "dm", "size_bytes": 1980000000, "slaves": ["vda2"], "holders": [],
                   "content": {"type": "swap", "usage": "other", "uuid": "5e5e5e5e-0000-4000-8000-000000000002", "label": null, "page_size": 4096},
                   "dm": {"dm_name": "cryptswap", "mapper_path": "/dev/mapper/cryptswap", "target": "crypt", "crypt_type": "LUKS2"}},
                  {"name": "dm-1", "kind": "dm", "size_bytes": 14178844672, "slaves": ["vda3"], "holders": [],
                   "content": {"type": "btrfs", "usage": "filesystem", "uuid": "b7b7b7b7-0000-4000-8000-000000000003", "label": null, "node_size": 16384, "sector_size": 4096, "checksum": "crc32c", "devid": 1, "features": [], "compat_ro_features": []},
                   "dm": {"dm_name": "cryptroot", "mapper_path": "/dev/mapper/cryptroot", "target": "crypt", "crypt_type": "LUKS2"}}]"#,
            )
            .replace(r#""devices": ["vda3"]"#, r#""devices": ["dm-1"]"#)
            .replace(r#""resolves_to": "vda3""#, r#""resolves_to": "dm-1""#)
            .replace(r#""resolves_to": "vda2""#, r#""resolves_to": "dm-0""#)
            .replace(
                r#""crypttab": null"#,
                &format!(
                    r#""crypttab": [{{"name": "cryptswap", "device": "UUID=a1a1a1a1-0000-4000-8000-000000000002", "key_file": "none", "options": {options}, "resolves_to": "vda2"}},
                                   {{"name": "cryptroot", "device": "UUID=a3a3a3a3-0000-4000-8000-000000000003", "key_file": "none", "options": {options}, "resolves_to": "vda3"}}]"#
                ),
            )
    }

    /// The keys: one group per container, or one for the containers the
    /// source unlocked with one passphrase (decrypt_keyctl); key_needed
    /// under --json for each without --key-file; the key files' checks;
    /// the tokens not restored.
    #[test]
    fn keys_and_their_files() {
        let t = rescue("json-plan-keys");
        let s = sys_of(&t);
        let keys = |v: &Value| v.arr_at(&["keys"]).to_vec();
        let base = ["--map", "vda=vdb", "--json"];
        // Each its own.
        let l = two_luks(r#"["luks"]"#);
        let (_, v, _) = plan(&l, &base, &s);
        let k = keys(&v);
        assert_eq!(k.len(), 2, "{k:?}");
        assert_eq!((k[0].get_str("group"), k[0].get_str("kind"), k[0].get_str("unlock"), k[0].get_bool("given")), (Some("cryptswap"), Some("luks"), Some("passphrase"), Some(false)));
        assert_eq!((k[0].get_bool("new_ok"), k[0].get_u64("min_bytes"), k[0].get_u64("max_bytes"), k[0].get_bool("shared_at_boot")), (Some(true), Some(1), Some(512), Some(false)));
        assert_eq!(k[1].arr_at(&["holds"]).iter().filter_map(Value::as_str).collect::<Vec<_>>(), ["/"]);
        assert_eq!(k[1].arr_at(&["devices"]).iter().filter_map(Value::as_str).collect::<Vec<_>>(), ["vda3"]);
        let needed: Vec<(Option<&str>, Option<&str>)> = v.arr_at(&["problems"]).iter().filter(|p| p.get_str("code") == Some("key_needed")).map(|p| (p.get_str("name"), p.get_str("group"))).collect();
        assert_eq!(needed, [(Some("cryptswap"), Some("cryptswap")), (Some("cryptroot"), Some("cryptroot"))]);
        // Text mode asks on the terminal, as ever: no problem.
        let (code, out, _) = crate::load::run(&opts(&base[..2]), &l, &s);
        assert!(!out.contains("give its new passphrase with --key-file"), "{code}: {out}");
        // The tokens a new LUKS2 header doesn't have.
        assert_eq!(v.get("tokens").unwrap().compact(), r#"[{"container":"cryptswap","kind":"systemd-tpm2"},{"container":"cryptroot","kind":"systemd-tpm2"}]"#);
        // One passphrase for both at boot: one group.
        let l = two_luks(r#"["luks", "keyscript=decrypt_keyctl"]"#);
        let (_, v, _) = plan(&l, &base, &s);
        let k = keys(&v);
        assert_eq!(k.len(), 1, "{k:?}");
        assert_eq!(k[0].arr_at(&["names"]).iter().filter_map(Value::as_str).collect::<Vec<_>>(), ["cryptswap", "cryptroot"]);
        assert_eq!((k[0].get_str("group"), k[0].get_bool("shared_at_boot"), k[0].get_str("unlock")), (Some("cryptswap"), Some(true), Some("passphrase")));
        // The key files: private, of this user, no newline at the end of
        // a passphrase's.
        let l = two_luks(r#"["luks"]"#);
        use std::os::unix::fs::PermissionsExt;
        let mode = |p: &str, m: u32| std::fs::set_permissions(t.p(p), std::fs::Permissions::from_mode(m)).unwrap();
        t.file("/k/0", "secret");
        mode("/k", 0o700);
        mode("/k/0", 0o600);
        let with = |extra: &[&str]| {
            let mut a = base.to_vec();
            a.extend(["--key-file", "cryptswap=/k/0", "--key-file", "cryptroot=/k/0"]);
            a.extend(extra);
            plan(&l, &a, &s).1
        };
        let v = with(&[]);
        assert!(codes(&v).is_empty(), "{:?}", codes(&v));
        assert!(keys(&v).iter().all(|k| k.get_bool("given") == Some(true)));
        let unsafe_check = |v: &Value| v.arr_at(&["problems"]).iter().filter(|p| p.get_str("code") == Some("key_file_unsafe")).filter_map(|p| p.get_str("check").map(str::to_string)).collect::<Vec<_>>();
        t.file("/k/0", "secret\n");
        mode("/k/0", 0o600);
        assert_eq!(unsafe_check(&with(&[])), ["newline", "newline"]);
        t.file("/k/0", "secret");
        mode("/k/0", 0o644);
        assert_eq!(unsafe_check(&with(&[])), ["mode", "mode"]);
        mode("/k/0", 0o600);
        mode("/k", 0o755);
        assert_eq!(unsafe_check(&with(&[])), ["dir_mode", "dir_mode"]);
        mode("/k", 0o700);
        t.symlink("/k/1", "0");
        let mut a = base.to_vec();
        a.extend(["--key-file", "cryptswap=/k/1", "--key-file", "cryptroot=k/0"]);
        assert_eq!(unsafe_check(&plan(&l, &a, &s).1), ["regular", "absolute"]);
        // A password in the repository's URL: refused, and nowhere in the
        // document (args, the commands, their reasons, text), nor on
        // standard error.
        let mut a = base.to_vec();
        a.extend(["--key-file", "cryptswap=/k/0", "--key-file", "cryptroot=/k/0", "--repo", "rest:https://me:hunter2@nas:8000/r", "--password-file", "/k/0"]);
        let (code, v, err) = plan(&l, &a, &s);
        let c = codes(&v);
        assert!(code == 4 && c.contains(&"repo_has_password".to_string()), "{code}: {c:?}");
        assert!(!v.compact().contains("hunter2") && !err.contains("hunter2"), "{}", v.compact());
        assert!(v.arr_at(&["args"]).iter().any(|x| x.as_str() == Some("rest:https://me:REDACTED@nas:8000/r")), "{:?}", v.get("args"));
        assert!(v.get_str("text").unwrap().contains("rest:https://me:REDACTED@nas:8000/r"));
        assert!(v.compact().contains(r#""--repository","rest:https://me:REDACTED@nas:8000/r""#), "{}", v.compact());
        for (r, has) in [("sftp://u:p@h/x", true), ("rest:https://u@h/", false), ("/srv/r", false), ("sftp:u@h:/x", false), ("s3:https://h/b", false)] {
            assert_eq!(crate::load::plan::repo_has_password(r), has, "{r}");
        }
    }

    /// A private write's text is in neither the plan's stages nor its text.
    #[test]
    fn private_writes_are_not_shown() {
        use crate::load::commands::Cmd;
        let c = Cmd::write_file("/mnt/bko-target/etc/lilo.conf.bko-holt", "password=hunter2\nimage=/vmlinuz\n".to_string()).private();
        assert_eq!(c.shell_elided(), "cat > /mnt/bko-target/etc/lilo.conf.bko-holt <<'EOF'\n# (32 bytes, private: not shown)\nEOF\n");
        assert!(c.shell().contains("hunter2"));
        let j = cmd_json(&c);
        assert!(!j.compact().contains("hunter2"), "{}", j.compact());
        assert_eq!((j.get_str("kind"), j.get("stdin").map(Value::is_null), j.get_u64("stdin_bytes")), (Some("write"), Some(true), Some(32)));
        assert_eq!(j.get_str("stdin_sha256"), Some(crate::bytes::sha256_hex(b"password=hunter2\nimage=/vmlinuz\n").as_str()));
        // Any other step is shell()'s.
        let n = Cmd::new(&["mkdir", "-p", "/x"]);
        assert_eq!(n.shell_elided(), n.shell());
    }
}
