//! `bko-holt snapshot-layout OUT --repo REPO ...`: the disk layout a
//! snapshot's own system.json carries, read from the backup and written to
//! OUT for `load` (holt-json.md, "bko-holt-snapshot-layout"; its open
//! question 15, approved 2026-11-26).
//!
//! The backup tool puts the machine's system.json in each snapshot, at
//! `SYSTEM_PATH`, beside the LUKS2 headers' `luksfetch::REPO_DIR`
//! (stick-config.md, "Which copy wins").  The stick's copy is the newest
//! backup's (or older, or none: open question 16), so a restore of any
//! snapshot plans from that snapshot's own layout when it has one, and
//! fetches its headers from the same snapshot.  This reads that one file
//! with the archiver's `dump`, read-only toward the repository and every
//! disk, and checks it as `stick --copy` checks the stick's: a
//! backotter-system document of a schema this bko-holt reads, of the
//! machine asked for (`--install-id`), its `layout` one `load` takes, and
//! its kept headers a list `luks-headers` can fetch.  OUT is that `layout`,
//! printed as dump prints it, in a new 0600 file.  No checksum file: the
//! repository authenticates what it holds.
//!
//! A snapshot without the file (a backup from before the backup tool kept
//! it) is `no_system_json`, told from every other failure by the
//! archiver's own words for a path the snapshot doesn't hold, so a caller
//! may fall back to the stick's copy for it only; any other failure
//! (`system_fetch_failed`: a wrong password, a repository not there) is
//! never taken for it.

use crate::json::Value;
use crate::load::jsonout::{self, ErrorCode as E};
use crate::luksfetch::{DumpFailed, Fetch};
use std::path::Path;

/// Where each snapshot holds the machine's system.json (the backup tool
/// puts it there, stick-config.md's "Which copy wins").
pub const SYSTEM_PATH: &str = "/.bko-holt/system.json";

/// The biggest system.json read from a snapshot: a whole dump (up to
/// `LAYOUT_MAX`) and the envelope.  The backup's copy has no stick to fit,
/// so it may be bigger than the stick's `SYSTEM_MAX`.
pub const SNAPSHOT_SYSTEM_MAX: u64 = crate::LAYOUT_MAX + crate::stick::SYSTEM_MAX;

/// The archiver load runs unless --archiver says.
const DEFAULT_ARCHIVER: &str = "rustic-bko";

pub const USAGE: &str = "Usage: bko-holt snapshot-layout OUT --repo REPO [--password-file FILE]
                             [--snapshot ID] [--install-id ID] [--archiver PROGRAM] [--json]

Writes the disk layout a snapshot's own system.json carries (the bko-holt
dump in its `layout`) to OUT, for `bko-holt load OUT`.  The backup tool keeps
the machine's system.json in each snapshot, at /.bko-holt/system.json; it is
read with the archiver's dump of that one file and checked as `stick --copy`
checks the stick's: a backotter-system document of a schema this bko-holt
reads, of machine ID when --install-id names one, its layout one load takes.
OUT must be a new file in a directory of bko-holt's own user that no one else
may enter; it is written with mode 0600.

  --repo REPO          the repository (as load's --repo)
  --password-file FILE its password (under --json, required: nothing asks;
                       without it the archiver asks on the terminal)
  --snapshot ID        the snapshot (default latest)
  --install-id ID      the machine it must be (a UUID); refused if another's
  --archiver PROGRAM   rustic-bko (the default) or restic-bko
  --json               one JSON document on standard output
                       (`bko-holt-snapshot-layout`, docs/design/holt-json.md)
  -h, --help           show this help

It reads the repository only and writes no disk, so the dry-run lock
(BKO_HOLT_NO_COMMIT) allows it.

Exit status: 0 when OUT is written, 1 when the snapshot holds no system.json
(code no_system_json: a backup from before the backup tool kept one), it
can't be read, or it isn't one this takes (nothing left in OUT), 2 for bad
arguments or an OUT that isn't a new file in a private directory.
";

/// Whether SAID, the archiver's last line for a dump of PATH that failed,
/// says the snapshot doesn't hold the file: rustic-bko's "Node `X` not
/// found in tree." for one of its last two parts (`.bko-holt` or
/// `system.json`; a SUBPATH that isn't there names another part), or
/// restic-bko's "path \"P\" not found in snapshot" for PATH or its
/// directory.  Both as rustic-bko 0.12.0+backotter.4 and restic-bko
/// 0.20.0+backotter.1 say them (recorded 2026-11-26, open question 15).
pub fn says_missing(said: &str, path: &str) -> bool {
    let p = Path::new(path);
    let names: Vec<&str> = [p.file_name(), p.parent().and_then(Path::file_name)]
        .into_iter()
        .flatten()
        .filter_map(|n| n.to_str())
        .collect();
    let dir = p.parent().and_then(Path::to_str).unwrap_or("");
    let said = said.trim();
    if let Some(node) = said
        .strip_prefix("Node `")
        .and_then(|r| r.strip_suffix("` not found in tree."))
    {
        return names.contains(&node);
    }
    if let Some(rest) = said.strip_suffix("\" not found in snapshot") {
        if let Some(i) = rest.rfind("path \"") {
            let got = &rest[i + 6..];
            return got == path || (!dir.is_empty() && got == dir);
        }
    }
    false
}

/// What was asked: the repository, the snapshot, the machine.
pub struct Ask {
    pub fetch: Fetch,
    pub install_id: Option<String>,
}

/// SNAPSHOT's layout in OUT (checked by the caller to be a new file in a
/// private directory): the document's fields.
///
/// # Errors
///
/// The refusal's code and line: `no_system_json`, `system_fetch_failed`,
/// `system_refused`, `no_layout`, `layout_refused` or `copy_failed`, with
/// nothing left in OUT (or, when a file appeared there after the check,
/// that file left as it is).
pub fn copy(ask: &Ask, out: &Path) -> Result<Value, (E, String)> {
    let f = &ask.fetch;
    let argv = f.argv_path(SYSTEM_PATH);
    // What the archiver's dump was given: SNAPSHOT:PATH, or SNAPSHOT PATH.
    let from = argv
        .iter()
        .rposition(|a| a == "dump")
        .map(|i| argv[i + 1..].join(" "))
        .unwrap_or_default();
    let in_snap = format!("snapshot {}'s {SYSTEM_PATH}", f.snapshot);
    let body = match crate::luksfetch::run_dump(&argv, SNAPSHOT_SYSTEM_MAX, f.stdin_null, f.asks())
    {
        Ok(b) => b,
        Err(DumpFailed::TooBig(_)) => {
            return Err((
                E::SystemRefused,
                format!(
                    "{in_snap} ({from}) is bigger than the {} MiB a system.json may be",
                    SNAPSHOT_SYSTEM_MAX >> 20
                ),
            ));
        }
        Err(DumpFailed::Fetch { why, said }) => {
            // The archiver's path: the SUBPATH's when the snapshot has one.
            let path = from.rsplit([':', ' ']).next().unwrap_or(SYSTEM_PATH);
            if says_missing(&said, path) {
                return Err((
                    E::NoSystemJson,
                    format!(
                        "snapshot {} holds no {SYSTEM_PATH}: a backup made before the backup tool kept the machine's system.json in each snapshot, so its own layout isn't in it (the archiver said: {said})",
                        f.snapshot
                    ),
                ));
            }
            return Err((
                E::SystemFetchFailed,
                format!("{in_snap} couldn't be read ({from}): {why}"),
            ));
        }
    };
    let v = std::str::from_utf8(&body)
        .ok()
        .and_then(|t| crate::json::parse(t).ok())
        .ok_or_else(|| (E::SystemRefused, format!("{in_snap} isn't JSON")))?;
    if v.get_str("format") != Some("backotter-system") {
        return Err((
            E::SystemRefused,
            format!("{in_snap} isn't a backotter-system document"),
        ));
    }
    match v.get_u64("schema") {
        Some(s) if crate::stick::SYSTEM_SCHEMAS.contains(&s) => {}
        Some(s) => {
            return Err((
                E::SystemRefused,
                format!(
                    "{in_snap} is of schema {s}; this bko-holt reads {}: a newer image's bko-holt reads it",
                    schemas()
                ),
            ));
        }
        None => {
            return Err((E::SystemRefused, format!("{in_snap} gives no schema")));
        }
    }
    let id = match v.str_at(&["machine", "install_id"]) {
        Some(x) if crate::stick::is_uuid(x) => x.to_ascii_lowercase(),
        Some(_) => {
            return Err((
                E::SystemRefused,
                format!("{in_snap} names an install ID that isn't a UUID"),
            ));
        }
        None => {
            return Err((
                E::SystemRefused,
                format!("{in_snap} names no install ID (machine.install_id)"),
            ));
        }
    };
    if let Some(want) = &ask.install_id {
        if !want.eq_ignore_ascii_case(&id) {
            return Err((
                E::SystemRefused,
                format!(
                    "{in_snap} is machine {id}'s, not {}'s: a backup of another machine in the same repository",
                    want.to_ascii_lowercase()
                ),
            ));
        }
    }
    let key = crate::stick::LAYOUT_KEY;
    let layout = match v.get(key) {
        Some(l @ Value::Obj(_)) => l,
        Some(_) => {
            return Err((
                E::NoLayout,
                format!(
                    "{in_snap}'s \"{key}\" isn't a bko-holt dump document: it carries no disk layout"
                ),
            ));
        }
        None => {
            return Err((
                E::NoLayout,
                format!(
                    "{in_snap} carries no disk layout: it has no \"{key}\", where the backup tool puts the bko-holt dump"
                ),
            ));
        }
    };
    if let Err(e) = crate::load::layout::Layout::from_json(layout.clone()) {
        return Err((
            E::LayoutRefused,
            format!("{in_snap}'s {key} isn't a layout bko-holt load takes: {e}"),
        ));
    }
    let headers = match crate::luksfetch::entries(layout) {
        Ok(l) => l.len(),
        Err(e) => {
            return Err((
                E::LayoutRefused,
                format!(
                    "{in_snap}'s {key} kept LUKS2 headers bko-holt can't fetch from the backup: {e}"
                ),
            ));
        }
    };
    let text = layout.pretty();
    if text.len() as u64 > crate::LAYOUT_MAX {
        return Err((
            E::LayoutRefused,
            format!(
                "{in_snap}'s {key} is bigger than the {} MiB load reads",
                crate::LAYOUT_MAX >> 20
            ),
        ));
    }
    if let Err(e) = crate::recoverycmd::write_new(out, text.as_bytes()) {
        // O_EXCL: a file there now isn't this one's, and is left as it is.
        let left = if e.kind() == std::io::ErrorKind::AlreadyExists {
            "something is there now, left as it is"
        } else {
            "nothing left there"
        };
        return Err((E::CopyFailed, format!("{}: {e} ({left})", out.display())));
    }
    let short = |k: &str| -> Value {
        match v.get_str(k) {
            Some(t) if t.len() <= crate::stick::FIELD_MAX => Value::from(t),
            _ => Value::Null,
        }
    };
    Ok(jsonout::header("bko-holt-snapshot-layout")
        .set("snapshot", f.snapshot.as_str())
        .set("from", from)
        .set("install_id", id)
        .set("sequence", v.get_u64("sequence"))
        .set("written", short("written"))
        .set("out", out.display().to_string())
        .set("bytes", text.len())
        .set("sha256", crate::bytes::sha256_hex(text.as_bytes()))
        .set("system_sha256", crate::bytes::sha256_hex(&body))
        .set("luks_headers", headers)
        .build())
}

fn schemas() -> String {
    let v: Vec<String> = crate::stick::SYSTEM_SCHEMAS
        .iter()
        .map(u64::to_string)
        .collect();
    format!("schema {}", v.join(", "))
}

/// `bko-holt snapshot-layout ARGS`: the exit status.
pub fn main(args: &[String]) -> u8 {
    const VALUE_FLAGS: &[&str] = &[
        "--repo",
        "--password-file",
        "--snapshot",
        "--install-id",
        "--archiver",
    ];
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
    let say = |what: &str| format!("bko-holt snapshot-layout: {what}");
    if let Some(e) = jsonout::schema_error(args, VALUE_FLAGS) {
        return refuse(2, E::SchemaUnknown, say(&e));
    }
    let mut pos: Vec<String> = Vec::new();
    let mut repo = None;
    let mut password_file = None;
    let mut snapshot = None;
    let mut install_id = None;
    let mut archiver = None;
    let mut it = args.iter();
    while let Some(a) = it.next() {
        let slot = match a.as_str() {
            a if jsonout::is_json_flag(a) => continue,
            "--repo" => &mut repo,
            "--password-file" => &mut password_file,
            "--snapshot" => &mut snapshot,
            "--install-id" => &mut install_id,
            "--archiver" => &mut archiver,
            a if a.starts_with('-') => {
                return refuse(2, E::BadArgs, say(&format!("unknown argument {a:?}")));
            }
            _ => {
                pos.push(a.clone());
                continue;
            }
        };
        match it.next() {
            Some(v) if slot.is_none() => *slot = Some(v.clone()),
            Some(_) => return refuse(2, E::BadArgs, say(&format!("{a} is given twice"))),
            None => return refuse(2, E::BadArgs, say(&format!("{a} needs a value"))),
        }
    }
    let [out] = pos.as_slice() else {
        return refuse(
            2,
            E::BadArgs,
            say("give the one file to write the layout to"),
        );
    };
    // Not echoed: it may be anything typed in its place.
    if install_id
        .as_deref()
        .is_some_and(|i| !crate::stick::is_uuid(i))
    {
        return refuse(
            2,
            E::BadArgs,
            say(
                "--install-id isn't an install ID (a UUID, the name of its directory under backotter/machines/)",
            ),
        );
    }
    let Some(repo) = repo else {
        return refuse(
            2,
            E::BadArgs,
            say("--repo REPO: the repository the snapshot is in"),
        );
    };
    if crate::load::plan::repo_has_password(&repo) {
        return refuse(
            2,
            E::BadArgs,
            say(
                "--repo: the repository's URL holds a password; it goes in --password-file FILE, never on the command line",
            ),
        );
    }
    if json {
        // Nothing asks under --json, and the password file is checked as
        // load checks it (holt-json.md, "Secrets" 4).
        let Some(p) = &password_file else {
            return refuse(
                2,
                E::BadArgs,
                say(
                    "--repo: give the repository's password with --password-file FILE (under --json nothing asks for it)",
                ),
            );
        };
        if let Some((_, why)) =
            crate::load::plan::key_file_unsafe(&crate::sys::Sys::host(), p, false)
        {
            return refuse(
                2,
                E::BadArgs,
                say(&format!(
                    "--password-file {p}: {why}; under --json it must be a private file of bko-holt's own user"
                )),
            );
        }
    }
    if !Path::new(out).is_absolute() {
        return refuse(2, E::OutUnsafe, say("OUT isn't an absolute path"));
    }
    if let Some(why) = crate::recoverycmd::out_unsafe(out) {
        return refuse(2, E::OutUnsafe, say(&format!("{out}: {why}")));
    }
    // No core dumps: the archiver holds the repository's key.
    crate::sys::no_core_dumps();
    let ask = Ask {
        fetch: Fetch {
            repo,
            password_file,
            snapshot: snapshot.unwrap_or_else(|| "latest".to_string()),
            archiver: archiver.unwrap_or_else(|| DEFAULT_ARCHIVER.to_string()),
            stdin_null: json,
        },
        install_id,
    };
    match copy(&ask, Path::new(out)) {
        Ok(doc) => {
            if json {
                jsonout::write_stdout(&jsonout::line(&doc));
            } else {
                println!(
                    "{out}: the layout {} holds, machine {}, {} bytes",
                    doc.get_str("from").unwrap_or(""),
                    doc.get_str("install_id").unwrap_or(""),
                    doc.get_u64("bytes").unwrap_or(0)
                );
            }
            0
        }
        Err((code, line)) => refuse(1, code, say(&line)),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::json::Obj;
    use crate::testutil::TempDir;
    use std::os::unix::fs::PermissionsExt;

    const ID: &str = "0b7e3c52-8d1f-4a6e-9c3b-2f5d7a1e4b90";

    /// rustic-bko's and restic-bko's own lines for a dump of a path the
    /// snapshot doesn't hold, as recorded 2026-11-26 on cinqcent.
    #[test]
    fn the_archivers_words_for_a_missing_file() {
        let p = SYSTEM_PATH;
        assert!(says_missing("Node `.bko-holt` not found in tree.", p));
        assert!(says_missing("Node `system.json` not found in tree.", p));
        assert!(says_missing(
            "Fatal: cannot dump file: path \"/.bko-holt\" not found in snapshot",
            p
        ));
        assert!(says_missing(
            "Fatal: cannot dump file: path \"/.bko-holt/system.json\" not found in snapshot",
            p
        ));
        // A SUBPATH that isn't there, a wrong password, no such snapshot,
        // no repository: not "no system.json".
        let sub = "/mnt/src/.bko-holt/system.json";
        assert!(says_missing("Node `.bko-holt` not found in tree.", sub));
        assert!(!says_missing("Node `mnt` not found in tree.", sub));
        assert!(!says_missing("Node `etc` not found in tree.", p));
        for other in [
            "The password that has been entered, seems to be incorrect. No suitable key found for the given password. Please check your password and try again.",
            "No suitable id found for `deadbeef`.",
            "No repository config file found for `local:/x`. Please check the repository.",
            "Fatal: wrong password or no key found",
            "Fatal: cannot dump file: path \"/etc\" not found in snapshot",
            "",
        ] {
            assert!(!says_missing(other, p), "{other}");
        }
    }

    fn system(id: &str, layout: Option<Value>) -> Value {
        let mut o = Obj::new()
            .set("format", "backotter-system")
            .set("schema", 1u64)
            .set("sequence", 7u64)
            .set("written", "2026-11-20T21:14:03Z")
            .set(
                "machine",
                Obj::new()
                    .set("install_id", id)
                    .set("hostname", "lovelace")
                    .build(),
            )
            .build();
        if let Some(l) = layout {
            o.put("layout", l);
        }
        o
    }

    fn dump() -> Value {
        crate::json::parse(include_str!("../tests/corpus/debian13-stack/src.json")).unwrap()
    }

    /// A private directory, and an archiver that writes its `system.json`
    /// for the dump of `SYSTEM_PATH`, or says what rustic-bko says.
    fn rig(body: Option<&[u8]>) -> (TempDir, Ask) {
        let d = TempDir::new("snaplayout");
        std::fs::set_permissions(d.path(), std::fs::Permissions::from_mode(0o700)).unwrap();
        let file = d.path().join("system.json");
        if let Some(b) = body {
            std::fs::write(&file, b).unwrap();
        }
        let arch = d.path().join("rustic-bko");
        std::fs::write(
            &arch,
            format!(
                "#!/bin/sh\nprintf '%s\\n' \"$@\" >> '{log}'\n[ -f '{f}' ] || {{ printf 'error: internal operations.\\n\\nMessage:\\nNode `.bko-holt` not found in tree.\\n\\nWe believe this is a bug\\n' >&2; exit 1; }}\ncat '{f}'\n",
                log = d.path().join("argv").display(),
                f = file.display()
            ),
        )
        .unwrap();
        std::fs::set_permissions(&arch, std::fs::Permissions::from_mode(0o755)).unwrap();
        let ask = Ask {
            fetch: Fetch {
                repo: "sftp://nas/bko".into(),
                password_file: Some("/k/pw".into()),
                snapshot: "1a2b3c4d".into(),
                archiver: arch.display().to_string(),
                stdin_null: true,
            },
            install_id: Some(ID.to_string()),
        };
        (d, ask)
    }

    #[test]
    fn the_snapshots_own_layout_is_written() {
        let body = system(ID, Some(dump())).pretty();
        let (d, ask) = rig(Some(body.as_bytes()));
        let out = d.path().join("layout.json");
        let v = copy(&ask, &out).unwrap();
        let text = std::fs::read_to_string(&out).unwrap();
        assert_eq!(crate::json::parse(&text).unwrap(), dump());
        assert_eq!(text, dump().pretty());
        assert_eq!(
            std::fs::metadata(&out).unwrap().permissions().mode() & 0o7777,
            0o600
        );
        assert_eq!(v.get_str("from"), Some("1a2b3c4d:/.bko-holt/system.json"));
        assert_eq!(v.get_str("install_id"), Some(ID));
        assert_eq!(v.get_u64("sequence"), Some(7));
        assert_eq!(v.get_u64("bytes"), Some(text.len() as u64));
        assert_eq!(v.get_u64("luks_headers"), Some(0));
        assert_eq!(
            v.get_str("system_sha256"),
            Some(crate::bytes::sha256_hex(body.as_bytes()).as_str())
        );
        let keys: Vec<&str> = v
            .as_object()
            .unwrap()
            .iter()
            .map(|(k, _)| k.as_str())
            .collect();
        assert_eq!(
            keys,
            [
                "format",
                "schema",
                "version",
                "generated",
                "dry_run",
                "snapshot",
                "from",
                "install_id",
                "sequence",
                "written",
                "out",
                "bytes",
                "sha256",
                "system_sha256",
                "luks_headers"
            ]
        );
        let argv = std::fs::read_to_string(d.path().join("argv")).unwrap();
        assert!(
            argv.ends_with("--no-progress\ndump\n1a2b3c4d:/.bko-holt/system.json\n"),
            "{argv}"
        );
        let mut g = v.clone();
        g.put("out", "OUT");
        crate::testutil::golden_json("snapshot-layout-json", "ok", &g);
        // The ID in capitals is the same machine; none asked takes any.
        let mut upper = ask;
        upper.install_id = Some(ID.to_uppercase());
        assert!(copy(&upper, &d.path().join("2.json")).is_ok());
        upper.install_id = None;
        assert!(copy(&upper, &d.path().join("3.json")).is_ok());
        // OUT there now: left as it is.
        let (code, why) = copy(&upper, &out).unwrap_err();
        assert_eq!(code, E::CopyFailed);
        assert!(why.contains("left as it is"), "{why}");
        assert_eq!(std::fs::read_to_string(&out).unwrap(), text);
    }

    #[test]
    fn a_snapshot_without_one_is_told_apart() {
        let (d, ask) = rig(None);
        let out = d.path().join("layout.json");
        let (code, why) = copy(&ask, &out).unwrap_err();
        assert_eq!(code, E::NoSystemJson, "{why}");
        assert!(
            why.contains("snapshot 1a2b3c4d holds no /.bko-holt/system.json"),
            "{why}"
        );
        assert!(!out.exists());
        let v = crate::json::parse(&jsonout::error_doc(
            1,
            code,
            &format!("bko-holt snapshot-layout: {why}"),
        ))
        .unwrap();
        crate::testutil::golden_json("snapshot-layout-json", "no-system-json", &v);
        // Any other failure of the archiver: never taken for it.
        let arch = d.path().join("other");
        std::fs::write(&arch, "#!/bin/sh\nprintf 'Message:\\nThe password that has been entered, seems to be incorrect.\\n' >&2\nexit 1\n").unwrap();
        std::fs::set_permissions(&arch, std::fs::Permissions::from_mode(0o755)).unwrap();
        let mut ask = ask;
        ask.fetch.archiver = arch.display().to_string();
        let (code, why) = copy(&ask, &out).unwrap_err();
        assert_eq!(code, E::SystemFetchFailed, "{why}");
        assert!(
            why.ends_with("exited 1: The password that has been entered, seems to be incorrect."),
            "{why}"
        );
        ask.fetch.archiver = d.path().join("nope").display().to_string();
        assert_eq!(copy(&ask, &out).unwrap_err().0, E::SystemFetchFailed);
        assert!(!out.exists());
    }

    #[test]
    fn what_it_holds_is_checked_as_the_sticks() {
        let other = "1c2d3e4f-5a6b-4c7d-8e9f-a0b1c2d3e4f5";
        let mut schema2 = system(ID, Some(dump()));
        schema2.put("schema", 2u64);
        let mut not_ours = system(ID, Some(dump()));
        not_ours.put("format", "something-else");
        let mut bad_layout = dump();
        bad_layout.put("format", "not-bko-holt");
        let mut heads = dump();
        heads.at_mut(&["storage"]).unwrap().put(
            "luks_headers",
            crate::json::parse(r#"[{"device": "vda5", "file": "../x.img"}]"#).unwrap(),
        );
        let cases: Vec<(String, E, &str)> = vec![
            ("not json".into(), E::SystemRefused, "isn't JSON"),
            (
                not_ours.pretty(),
                E::SystemRefused,
                "isn't a backotter-system document",
            ),
            (
                schema2.pretty(),
                E::SystemRefused,
                "of schema 2; this bko-holt reads schema 1",
            ),
            (
                system(other, Some(dump())).pretty(),
                E::SystemRefused,
                "is machine 1c2d3e4f-5a6b-4c7d-8e9f-a0b1c2d3e4f5's, not 0b7e3c52-8d1f-4a6e-9c3b-2f5d7a1e4b90's",
            ),
            (
                system("../../etc", Some(dump())).pretty(),
                E::SystemRefused,
                "isn't a UUID",
            ),
            (system(ID, None).pretty(), E::NoLayout, "has no \"layout\""),
            (
                system(ID, Some(Value::from("x"))).pretty(),
                E::NoLayout,
                "isn't a bko-holt dump document",
            ),
            (
                system(ID, Some(bad_layout)).pretty(),
                E::LayoutRefused,
                "isn't a layout bko-holt load takes",
            ),
            (
                system(ID, Some(heads)).pretty(),
                E::LayoutRefused,
                "kept LUKS2 headers bko-holt can't fetch",
            ),
        ];
        for (i, (body, code, words)) in cases.into_iter().enumerate() {
            let (d, ask) = rig(Some(body.as_bytes()));
            let out = d.path().join("layout.json");
            let (c, why) = copy(&ask, &out).unwrap_err();
            assert_eq!(c, code, "{i}: {why}");
            assert!(why.contains(words), "{i}: {why}");
            assert!(!out.exists(), "{i}");
        }
        // A machine's system.json without the machine named: still its own.
        let mut no_id = system(ID, Some(dump()));
        no_id.put("machine", Obj::new().build());
        let (d, ask) = rig(Some(no_id.pretty().as_bytes()));
        let (c, why) = copy(&ask, &d.path().join("l.json")).unwrap_err();
        assert_eq!(
            (c, why.contains("names no install ID")),
            (E::SystemRefused, true),
            "{why}"
        );
    }

    #[test]
    fn a_subpath_snapshot_reads_under_it() {
        let body = system(ID, Some(dump())).pretty();
        let (d, mut ask) = rig(Some(body.as_bytes()));
        ask.fetch.snapshot = "1a2b3c4d:/mnt/src".into();
        let v = copy(&ask, &d.path().join("layout.json")).unwrap();
        assert_eq!(
            v.get_str("from"),
            Some("1a2b3c4d:/mnt/src/.bko-holt/system.json")
        );
    }
}
