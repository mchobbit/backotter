//! bko-holt's JSON protocol as a program (docs/design/holt-json.md, RT10a):
//! `--version --json`, `bko-holt-error` documents for every refusal under
//! `--json`, `--json` found only in a flag's place, the dry-run lock
//! (`BKO_HOLT_NO_COMMIT`) on load, the hidden helpers and the two commands
//! that write a file where they're told, and text mode left as it was.

use bko_holt::json::{self, Value};
use bko_holt::testutil::{TempDir, Tree};
use std::io::Seek;
use std::process::{Command, Output, Stdio};

fn exe() -> String {
    std::env::var("BKO_HOLT_BIN").unwrap_or_else(|_| env!("CARGO_BIN_EXE_bko-holt").to_string())
}

/// bko-holt ARGS with ENV added and `BKO_HOLT_NO_COMMIT` removed unless ENV
/// sets it; STDIN on its standard input when given, else none.  A load run
/// names --sysroot (or asks for the help), as `load_cli.rs`'s do: without
/// it, load records RUN_DIR/last-load.json on the test host.
fn run(args: &[&str], env: &[(&str, &str)], stdin: Option<&str>) -> Output {
    run_in(args, env, stdin).0
}

/// As run, and how many bytes of STDIN the program read.  STDIN is a file
/// written before the program starts, not a pipe written after: a refusal
/// that exits before reading it then races nothing (a pipe's write met a
/// broken pipe or not, by timing), and the file's offset, which the
/// program's descriptor shares, says how far it read.
fn run_in(args: &[&str], env: &[(&str, &str)], stdin: Option<&str>) -> (Output, u64) {
    assert!(
        args.first() != Some(&"load")
            || args
                .iter()
                .any(|a| matches!(*a, "--sysroot" | "-h" | "--help")),
        "{args:?}: give --sysroot"
    );
    let mut c = Command::new(exe());
    c.args(args)
        .env_remove("BKO_HOLT_NO_COMMIT")
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());
    for (k, v) in env {
        c.env(k, v);
    }
    let input = stdin.map(|s| {
        let d = TempDir::new("json-cli-stdin");
        let p = d.path().join("stdin");
        std::fs::write(&p, s).unwrap();
        let f = std::fs::File::open(&p).unwrap();
        (d, f)
    });
    c.stdin(match &input {
        Some((_, f)) => Stdio::from(f.try_clone().unwrap()),
        None => Stdio::null(),
    });
    let o = c.spawn().unwrap().wait_with_output().unwrap();
    let read = input.map_or(0, |(_d, mut f)| f.stream_position().unwrap());
    (o, read)
}

fn out(o: &Output) -> String {
    String::from_utf8_lossy(&o.stdout).into_owned()
}

fn err(o: &Output) -> String {
    String::from_utf8_lossy(&o.stderr).into_owned()
}

/// Standard output as exactly one JSON document on one line.
fn doc(o: &Output) -> Value {
    let s = out(o);
    assert!(
        s.ends_with('\n') && s.matches('\n').count() == 1,
        "not one line: {s:?} (stderr {})",
        err(o)
    );
    json::parse(&s).unwrap_or_else(|e| panic!("not JSON ({}): {s}", e.message))
}

/// A bko-holt-error document with EXIT and CODE, the program's exit EXIT,
/// and its text the first line of standard error.
fn assert_error(o: &Output, exit: u64, code: &str) -> Value {
    assert_eq!(o.status.code(), Some(exit as i32), "{}{}", out(o), err(o));
    let v = doc(o);
    assert_eq!(v.get_str("format"), Some("bko-holt-error"), "{}", out(o));
    assert_eq!(v.get_u64("schema"), Some(1));
    assert_eq!(v.get_str("version"), Some(env!("CARGO_PKG_VERSION")));
    assert!(v.get_str("generated").is_some_and(|g| g.ends_with('Z')));
    assert_eq!(v.get_u64("exit"), Some(exit));
    assert_eq!(v.get_str("code"), Some(code), "{}", out(o));
    let text = v.get_str("text").unwrap().to_string();
    assert!(!text.contains('\n') && !text.contains("Usage:"), "{text}");
    assert_eq!(err(o).lines().next(), Some(text.as_str()), "{}", err(o));
    v
}

/// A Debian 13 dump with one BIOS + MBR disk holding a single ext4 (as
/// `load_cli.rs`'s).
const LAYOUT: &str = r#"{
  "format": "bko-holt", "schema": 1, "privileged": true,
  "host": {"hostname": "t"}, "os": {"id": "debian", "version_id": "13"},
  "storage": {
    "disks": [{"name": "vda", "kind": "disk", "size_bytes": 1073741824, "logical_sector_size": 512,
      "partition_table": {"type": "dos", "disk_signature": "0x00c0ffee"},
      "partitions": [{"name": "vda1", "number": 1, "start_lba": 2048, "sectors": 2095104, "type": "0x83", "bootable": true,
        "content": {"type": "ext4", "uuid": "11111111-2222-4333-8444-555555555555", "features": ["has_journal", "extent"]}}],
      "content": null, "boot_code": {"kind": "grub2"}}],
    "fstab": [{"spec": "UUID=11111111-2222-4333-8444-555555555555", "file": "/", "type": "ext4", "options": "errors=remount-ro", "resolves_to": "vda1"}]
  },
  "boot": {"loader": {"name": "grub", "firmware": "bios"}}
}"#;

fn setup() -> (Tree, String, String) {
    let t = Tree::new("json-cli");
    t.file("/layout.json", LAYOUT);
    t.disk("vdb", "253:16", &[], 512)
        .file("/sys/block/vdb/size", "2097152\n");
    t.file("/proc/self/mountinfo", "")
        .file("/proc/swaps", "Filename\tType\tSize\tUsed\tPriority\n");
    let layout = t.p("/layout.json").to_string_lossy().into_owned();
    let root = t.root().to_string_lossy().into_owned();
    (t, layout, root)
}

#[test]
fn version_json_is_the_api_document() {
    for args in [
        ["--version", "--json"],
        ["-V", "--json"],
        ["-V", "--json=1"],
    ] {
        let o = run(&args, &[], None);
        assert_eq!(o.status.code(), Some(0), "{}", err(&o));
        assert!(err(&o).is_empty());
        let v = doc(&o);
        assert_eq!(v.get_str("format"), Some("bko-holt-api"));
        assert_eq!(v.get_u64("schema"), Some(1));
        assert_eq!(v.get_str("version"), Some(env!("CARGO_PKG_VERSION")));
        assert_eq!(v.get_bool("no_commit"), Some(false));
        assert_eq!(v.get_bool("dry_run"), Some(false));
        for d in [
            "plan",
            "status",
            "events",
            "stick",
            "recovery",
            "stick_copy",
            "luks_headers",
            "snapshot_layout",
            "error",
        ] {
            assert!(v.get("documents").and_then(|x| x.get(d)).is_some(), "{d}");
        }
        assert!(
            v.get_arr("features")
                .unwrap()
                .iter()
                .any(|f| f.as_str() == Some("no_commit_env"))
        );
        assert!(
            v.get_arr("features")
                .unwrap()
                .iter()
                .any(|f| f.as_str() == Some("stick_copy"))
        );
        assert!(
            v.get_arr("features")
                .unwrap()
                .iter()
                .any(|f| f.as_str() == Some("luks_headers"))
        );
        assert!(
            v.get_arr("features")
                .unwrap()
                .iter()
                .any(|f| f.as_str() == Some("snapshot_layout"))
        );
        // What an image build copies into the stick's marker (open question 11).
        assert_eq!(
            v.get_arr("dump_schemas")
                .map(|a| a.iter().filter_map(Value::as_u64).collect::<Vec<_>>()),
            Some(bko_holt::load::layout::DUMP_SCHEMAS.to_vec())
        );
        assert!(
            v.str_at(&["platform", "os"]).is_some() && v.str_at(&["platform", "arch"]).is_some()
        );
        assert!(v.get_str("run_dir").unwrap().ends_with("/bko-holt"));
    }
    // The lock shows, so bko-rescue can check it took before its banner.
    for (val, on) in [("1", true), ("yes", true), ("0", false), ("", false)] {
        let v = doc(&run(
            &["--version", "--json"],
            &[("BKO_HOLT_NO_COMMIT", val)],
            None,
        ));
        assert_eq!(v.get_bool("no_commit"), Some(on), "{val:?}");
        assert_eq!(v.get_bool("dry_run"), Some(on), "{val:?}");
    }
    // A schema this bko-holt doesn't write.
    let o = run(&["-V", "--json=2"], &[], None);
    let v = assert_error(&o, 2, "schema_unknown");
    assert_eq!(
        v.get_str("text"),
        Some("bko-holt: --json=2: this bko-holt writes schema 1 of its JSON documents only")
    );
    // Text mode as ever.
    for a in ["-V", "--version"] {
        let o = run(&[a], &[], None);
        assert_eq!(out(&o), format!("bko-holt {}\n", env!("CARGO_PKG_VERSION")));
        let o = run(&[a, "--frob"], &[], None);
        assert_eq!(out(&o), format!("bko-holt {}\n", env!("CARGO_PKG_VERSION")));
    }
}

#[test]
fn load_refusals_are_error_documents() {
    let (t, layout, root) = setup();
    // Bad arguments: the document, and on standard error text mode's line
    // and usage.
    let o = run(&["load", "--json", "--sysroot", &root], &[], None);
    let v = assert_error(&o, 2, "bad_args");
    assert_eq!(
        v.get_str("text"),
        Some("bko-holt load: which layout? Give the JSON file bko-holt dump wrote")
    );
    assert!(
        err(&o).contains("\n\nUsage: bko-holt load LAYOUT.json"),
        "{}",
        err(&o)
    );
    let v = assert_error(
        &run(&["load", "--frob", "--json", "--sysroot", &root], &[], None),
        2,
        "bad_args",
    );
    assert_eq!(
        v.get_str("text"),
        Some("bko-holt load: unknown argument \"--frob\"")
    );
    assert_error(
        &run(
            &[
                "load",
                "x.json",
                "--through",
                "boot",
                "--json",
                "--sysroot",
                &root,
            ],
            &[],
            None,
        ),
        2,
        "bad_args",
    );
    assert_error(
        &run(
            &["load", &layout, "--json", "--sysroot", "/nonexistent/tree"],
            &[],
            None,
        ),
        2,
        "bad_args",
    );
    // The layout.
    assert_error(
        &run(
            &[
                "load",
                "/nonexistent/layout.json",
                "--json",
                "--sysroot",
                &root,
            ],
            &[],
            None,
        ),
        2,
        "layout_unreadable",
    );
    std::fs::write(t.p("/not.json"), "{not json").unwrap();
    let nj = t.p("/not.json").to_string_lossy().into_owned();
    assert_error(
        &run(&["load", &nj, "--sysroot", &root, "--json"], &[], None),
        3,
        "layout_not_json",
    );
    std::fs::write(t.p("/other.json"), "{\"format\": \"something-else\"}").unwrap();
    let other = t.p("/other.json").to_string_lossy().into_owned();
    let o = run(&["load", &other, "--sysroot", &root, "--json"], &[], None);
    assert_error(&o, 3, "layout_refused");
    // The report-bug hint stays on standard error, as in text mode.
    assert!(err(&o).contains("report-bug"), "{}", err(&o));
    let riscv = LAYOUT.replace(
        r#""version_id": "13"}"#,
        r#""version_id": "13", "machine": "riscv64"}"#,
    );
    std::fs::write(t.p("/riscv.json"), riscv).unwrap();
    let rv = t.p("/riscv.json").to_string_lossy().into_owned();
    assert_error(
        &run(&["load", &rv, "--sysroot", &root, "--json"], &[], None),
        3,
        "arch_mismatch",
    );
    // A schema this bko-holt doesn't write, before any other argument's
    // complaint.
    assert_error(
        &run(
            &["load", "--frob", "--json=2", "--sysroot", &root],
            &[],
            None,
        ),
        2,
        "schema_unknown",
    );
    // Beside a good --json, after it too.
    assert_error(
        &run(
            &["load", &layout, "--json", "--json=2", "--sysroot", &root],
            &[],
            None,
        ),
        2,
        "schema_unknown",
    );
    // The writing stages (RT10f): an event stream, start to end; this
    // tree has none of the tools, so plan problems: nothing runs, exit 4,
    // as text mode's.
    for extra in [&["--through", "mount"][..], &["--only", "boot"]] {
        let mut a = vec![
            "load",
            layout.as_str(),
            "--sysroot",
            &root,
            "--map",
            "vda=vdb",
        ];
        a.extend_from_slice(extra);
        let text = run(&a, &[], None);
        a.push("--json");
        let o = run(&a, &[], None);
        assert_eq!(
            (o.status.code(), err(&o)),
            (text.status.code(), err(&text)),
            "{a:?}"
        );
        assert_eq!(o.status.code(), Some(4));
        let evs: Vec<Value> = out(&o)
            .lines()
            .map(|l| json::parse(l).unwrap_or_else(|e| panic!("{}: {l}", e.message)))
            .collect();
        let names: Vec<&str> = evs.iter().filter_map(|e| e.get_str("event")).collect();
        assert_eq!(names, ["start", "problems", "end"], "{}", out(&o));
        assert_eq!(
            (
                evs[0].get_str("format"),
                evs[0].get_u64("schema"),
                evs[0].get_bool("commit")
            ),
            (Some("bko-holt-events"), Some(1), Some(false))
        );
        assert_eq!(evs[2].get_u64("exit"), Some(4));
    }
    // --help is for people, --json or not.
    for args in [
        &["load", "--json", "--help"][..],
        &["load", "--json=2", "--help"],
        &["load", "-h", "--json=2"],
    ] {
        let o = run(args, &[], None);
        assert_eq!(o.status.code(), Some(0), "{args:?}");
        assert!(
            out(&o).starts_with("Usage: bko-holt load LAYOUT.json"),
            "{args:?}: {}",
            out(&o)
        );
    }
}

/// The plan under --json (RT10c): one bko-holt-plan document, its text
/// what text mode prints, its problems coded; the exit as the plan's.
#[test]
fn plan_is_a_document() {
    let (_t, layout, root) = setup();
    let o = run(
        &[
            "load",
            &layout,
            "--sysroot",
            &root,
            "--map",
            "vda=vdb",
            "--json",
        ],
        &[],
        None,
    );
    let text = run(
        &["load", &layout, "--sysroot", &root, "--map", "vda=vdb"],
        &[],
        None,
    );
    assert_eq!(o.status.code(), text.status.code(), "{}", err(&o));
    assert_eq!(o.status.code(), Some(4));
    let v = doc(&o);
    assert_eq!(v.get_str("format"), Some("bko-holt-plan"));
    assert_eq!(
        (v.get_bool("ok"), v.get_u64("exit")),
        (Some(false), Some(4))
    );
    assert_eq!(v.get_str("text"), Some(out(&text).as_str()));
    assert!(
        v.get_arr("problems")
            .unwrap()
            .iter()
            .all(|p| p.get_str("code").is_some() && p.get_str("text").is_some())
    );
    assert_eq!(v.str_at(&["disks", "0", "target", "node"]), Some("vdb"));
    assert!(
        v.get_arr("candidates")
            .unwrap()
            .iter()
            .any(|c| c.get_str("node") == Some("vdb"))
    );
    assert!(
        err(&o).starts_with("bko-holt load: plan found "),
        "{}",
        err(&o)
    );
    // --expect-plan wants --json, and a SHA-256.
    let o = run(
        &[
            "load",
            &layout,
            "--sysroot",
            &root,
            "--expect-plan",
            &"0".repeat(64),
        ],
        &[],
        None,
    );
    assert_eq!(o.status.code(), Some(2));
    assert!(
        err(&o).contains("--expect-plan is for --json runs"),
        "{}",
        err(&o)
    );
    assert_error(
        &run(
            &[
                "load",
                &layout,
                "--sysroot",
                &root,
                "--json",
                "--expect-plan",
                "xyz",
            ],
            &[],
            None,
        ),
        2,
        "bad_args",
    );
}

/// Status under --json (RT10e): one bko-holt-status document, its rows
/// text mode's, in snake case; the exit as text mode's.
#[test]
fn status_is_a_document() {
    let (_t, layout, root) = setup();
    let a = [
        "load",
        layout.as_str(),
        "--sysroot",
        &root,
        "--map",
        "vda=vdb",
        "--only",
        "status",
    ];
    let text = run(&a, &[], None);
    let mut j = a.to_vec();
    j.push("--json");
    let o = run(&j, &[], None);
    assert_eq!((o.status.code(), err(&o)), (text.status.code(), err(&text)));
    let v = doc(&o);
    assert_eq!(v.get_str("format"), Some("bko-holt-status"));
    assert_eq!(v.get_u64("exit"), text.status.code().map(|c| c as u64));
    // Each text row is "NAME (10) STATE (14) (WHY)".
    let snake = |w: &str| match w {
        "not done" => "not_done".to_string(),
        "nothing to do" => "nothing".to_string(),
        w => w.to_string(),
    };
    let rows: Vec<(String, String)> = out(&text)
        .lines()
        .filter(|l| !l.starts_with('#') && !l.starts_with("next:"))
        .map(|l| {
            (
                l[..10].trim().to_string(),
                snake(l[11..].get(..14).unwrap_or(&l[11..]).trim()),
            )
        })
        .collect();
    let got: Vec<(String, String)> = v
        .get_arr("stages")
        .unwrap()
        .iter()
        .map(|s| {
            (
                s.get_str("name").unwrap().to_string(),
                s.get_str("state").unwrap().to_string(),
            )
        })
        .collect();
    assert_eq!(got, rows, "{}", out(&o));
    let next = out(&text)
        .lines()
        .find_map(|l| l.strip_prefix("next: "))
        .map(str::to_string);
    assert_eq!(v.get_str("next").map(str::to_string), next);
    assert_eq!(v.bool_at(&["resume", "go_on"]), Some(false));
    // Allowed under the dry-run lock, which it reports.
    let o = run(&j, &[("BKO_HOLT_NO_COMMIT", "1")], None);
    assert_eq!(doc(&o).get_bool("dry_run"), Some(true));
}

#[test]
fn json_counts_only_in_a_flags_place() {
    // --snapshot --json names a snapshot called --json: a text-mode run,
    // whose unreadable layout is text on standard error and nothing on
    // standard output.
    let o = run(
        &[
            "load",
            "/nonexistent/layout.json",
            "--repo",
            "/r",
            "--snapshot",
            "--json",
            "--sysroot",
            "/nonexistent/sysroot",
        ],
        &[],
        None,
    );
    assert_eq!(o.status.code(), Some(2));
    assert!(out(&o).is_empty(), "{}", out(&o));
    assert!(
        err(&o).starts_with("bko-holt load: /nonexistent/layout.json: "),
        "{}",
        err(&o)
    );
    // --through --json: no stage of that name, a text-mode error.
    let o = run(
        &[
            "load",
            "x.json",
            "--through",
            "--json",
            "--sysroot",
            "/nonexistent/sysroot",
        ],
        &[],
        None,
    );
    assert_eq!(o.status.code(), Some(2));
    assert!(out(&o).is_empty());
    assert!(
        err(&o).starts_with("bko-holt load: --through: no stage called \"--json\""),
        "{}",
        err(&o)
    );
}

#[test]
fn text_mode_is_unchanged() {
    let (_t, layout, root) = setup();
    // Plan and status print as load_cli.rs checks; nothing of JSON's.
    let o = run(
        &["load", &layout, "--sysroot", &root, "--map", "vda=vdb"],
        &[],
        None,
    );
    assert_eq!(o.status.code(), Some(4));
    assert!(out(&o).starts_with("# bko-holt load plan"), "{}", out(&o));
    let o = run(&["load", "--frob", "--sysroot", &root], &[], None);
    assert_eq!(o.status.code(), Some(2));
    assert!(out(&o).is_empty());
    assert!(
        err(&o).starts_with("bko-holt load: unknown argument \"--frob\"\n\nUsage: bko-holt load"),
        "{}",
        err(&o)
    );
}

#[test]
fn no_commit_refuses_commit() {
    let (_t, layout, root) = setup();
    let write = [
        "load",
        layout.as_str(),
        "--sysroot",
        &root,
        "--map",
        "vda=vdb",
        "--through",
        "mount",
        "--commit",
    ];
    for val in ["1", "yes", "00", "true", "no"] {
        let o = run(&write, &[("BKO_HOLT_NO_COMMIT", val)], None);
        assert_eq!(o.status.code(), Some(2), "{val:?}: {}", err(&o));
        assert!(out(&o).is_empty());
        assert_eq!(
            err(&o),
            format!(
                "bko-holt load: BKO_HOLT_NO_COMMIT={val} is set (a dry run), so --commit is refused; nothing was written to any disk\n"
            )
        );
        let mut a = write.to_vec();
        a.push("--json");
        let v = assert_error(
            &run(&a, &[("BKO_HOLT_NO_COMMIT", val)], None),
            2,
            "no_commit",
        );
        assert_eq!(v.get_bool("dry_run"), Some(true));
    }
    // Before the layout is read.
    let o = run(
        &[
            "load",
            "/nonexistent/layout.json",
            "--commit",
            "--sysroot",
            &root,
        ],
        &[("BKO_HOLT_NO_COMMIT", "1")],
        None,
    );
    assert_eq!(o.status.code(), Some(2));
    assert!(err(&o).contains("so --commit is refused"), "{}", err(&o));
    // Not in force: the run goes on to its checks (no tools in the fake
    // tree, so plan's problems: exit 4, nothing written).
    for val in ["", "0"] {
        let o = run(&write, &[("BKO_HOLT_NO_COMMIT", val)], None);
        assert_eq!(o.status.code(), Some(4), "{val:?}: {}", err(&o));
        assert!(err(&o).contains("nothing was written"), "{}", err(&o));
    }
    // In force, without --commit: plan, status and the printing stages run
    // as ever, --erase included.
    let o = run(
        &[
            "load",
            &layout,
            "--sysroot",
            &root,
            "--map",
            "vda=vdb",
            "--erase",
            "/dev/vdb",
        ],
        &[("BKO_HOLT_NO_COMMIT", "1")],
        None,
    );
    assert_eq!(o.status.code(), Some(4), "{}", err(&o));
    assert!(out(&o).contains("# ---- partition ----"), "{}", out(&o));
    let o = run(
        &[
            "load",
            &layout,
            "--sysroot",
            &root,
            "--map",
            "vda=vdb",
            "--only",
            "status",
        ],
        &[("BKO_HOLT_NO_COMMIT", "1")],
        None,
    );
    assert_eq!(o.status.code(), Some(0), "{}", err(&o));
}

#[test]
fn no_commit_stops_the_writing_helpers() {
    for h in [
        "snapcore-boot-areas",
        "md-lbs",
        "umount-tree",
        "efi-dedupe",
        "grub-prefix",
        "luks1-digest",
        "raw-write",
        "write-table",
        "rewrite",
        "fat-set",
        "clear-restricting-flags",
    ] {
        for args in [vec![h], vec![h, "--help"], vec![h, "/dev/null", "x"]] {
            let o = run(&args, &[("BKO_HOLT_NO_COMMIT", "1")], None);
            assert_eq!(o.status.code(), Some(2), "{args:?}");
            assert!(out(&o).is_empty(), "{args:?}: {}", out(&o));
            assert_eq!(
                err(&o),
                format!(
                    "bko-holt {h}: BKO_HOLT_NO_COMMIT is set (a dry run), so nothing is written\n"
                )
            );
        }
    }
}

#[test]
fn json_is_refused_on_dump_and_report_bug() {
    for args in [
        vec!["dump", "--json"],
        vec!["dump", "--compact", "--json=1"],
    ] {
        let v = assert_error(&run(&args, &[], None), 2, "json_not_here");
        assert!(
            v.get_str("text")
                .unwrap()
                .starts_with("bko-holt dump: --json"),
            "{args:?}"
        );
    }
    // As a flag's value it is that value.
    for f in [
        "--disk",
        "--only-disk",
        "--keep-luks-header",
        "--luks-header-dir",
        "--sysroot",
    ] {
        let o = run(&["dump", f, "--json", "--frob"], &[], None);
        assert_eq!(o.status.code(), Some(2), "{f}");
        assert!(out(&o).is_empty(), "{f}: {}", out(&o));
    }
    let v = assert_error(
        &run(&["report-bug", "--json"], &[], None),
        2,
        "json_not_here",
    );
    assert!(
        v.get_str("text")
            .unwrap()
            .starts_with("bko-holt report-bug: --json")
    );
    // --help is for people, --json or not, wherever it stands.
    for (args, usage) in [
        (&["dump", "--json", "--help"][..], "Usage: bko-holt dump "),
        (&["dump", "-h", "--json=2"], "Usage: bko-holt dump "),
        (
            &["report-bug", "--json", "--help"],
            "Usage: bko-holt report-bug ",
        ),
        (
            &["report-bug", "-h", "--json"],
            "Usage: bko-holt report-bug ",
        ),
    ] {
        let o = run(args, &[], None);
        assert_eq!(o.status.code(), Some(0), "{args:?}: {}", err(&o));
        assert!(out(&o).starts_with(usage), "{args:?}: {}", out(&o));
    }
    // A --help that is a flag's value isn't the help.
    assert_error(
        &run(&["report-bug", "--stderr", "--help", "--json"], &[], None),
        2,
        "json_not_here",
    );
    for f in ["--output", "-o", "--stderr", "--sysroot"] {
        let o = run(&["report-bug", f, "--json", "--frob"], &[], None);
        assert_eq!(o.status.code(), Some(2), "{f}");
        assert!(out(&o).is_empty(), "{f}: {}", out(&o));
        assert!(
            err(&o).contains("unknown argument \"--frob\""),
            "{f}: {}",
            err(&o)
        );
    }
}

#[test]
fn no_commit_writes_only_new_files() {
    let t = Tree::new("json-cli-files");
    let key = "00001-00002-65535-00256-12345-00000-54321-00010\n";
    let old = t.p("/old.key").to_string_lossy().into_owned();
    std::fs::write(&old, "keep me").unwrap();
    let dry = [("BKO_HOLT_NO_COMMIT", "1")];
    // recovery-key-file: a file that is there (a device too) is refused
    // before the key is read.
    for o_path in [old.as_str(), "/dev/null"] {
        let (o, read) = run_in(&["recovery-key-file", o_path], &dry, Some(key));
        assert_eq!(o.status.code(), Some(2), "{o_path}: {}", err(&o));
        assert_eq!(read, 0, "{o_path}");
        assert_eq!(
            err(&o),
            format!(
                "bko-holt recovery-key-file: {o_path}: BKO_HOLT_NO_COMMIT is set (a dry run), so only a new file is written, and this one is there already\n"
            )
        );
    }
    assert_eq!(std::fs::read_to_string(&old).unwrap(), "keep me");
    let new = t.p("/new.key").to_string_lossy().into_owned();
    let (o, read) = run_in(&["recovery-key-file", &new], &dry, Some(key));
    assert_eq!(o.status.code(), Some(0), "{}", err(&o));
    assert_eq!(read, key.len() as u64);
    assert_eq!(std::fs::read(&new).unwrap().len(), 16);
    // Without the lock, as today: the old file is written over.
    let o = run(&["recovery-key-file", &old], &[], Some(key));
    assert_eq!(o.status.code(), Some(0), "{}", err(&o));
    assert_eq!(std::fs::read(&old).unwrap().len(), 16);
    // report-bug -o: the same, refused before anything is gathered.
    let rep = t.p("/report.txt").to_string_lossy().into_owned();
    std::fs::write(&rep, "an old report").unwrap();
    for o_path in [rep.as_str(), "/dev/null"] {
        let o = run(&["report-bug", "-o", o_path], &dry, None);
        assert_eq!(o.status.code(), Some(2), "{o_path}: {}", err(&o));
        assert_eq!(
            err(&o),
            format!(
                "bko-holt report-bug: {o_path}: BKO_HOLT_NO_COMMIT is set (a dry run), so only a new file is written, and this one is there already\n"
            )
        );
        assert!(out(&o).is_empty());
    }
    assert_eq!(std::fs::read_to_string(&rep).unwrap(), "an old report");
}

/// `bko-holt stick --json` (`RT10k`) on a Linux tree booted from a stick
/// (`bko_holt::stick::sample`), as a program: one document, its exit.
#[test]
fn stick_is_a_document() {
    use bko_holt::stick::sample;
    let t = sample::linux_tree(
        &sample::config(sample::three()),
        Some("4c4c4544-0000-1111-2222-333344445555"),
    );
    let root = t.root().to_string_lossy().into_owned();
    for env in [&[][..], &[("BKO_HOLT_NO_COMMIT", "1")][..]] {
        let o = run(&["stick", "--json", "--sysroot", &root], env, None);
        assert_eq!(o.status.code(), Some(0), "{}", err(&o));
        assert!(err(&o).is_empty(), "{}", err(&o));
        let v = doc(&o);
        assert_eq!(v.get_str("format"), Some("bko-holt-stick"));
        assert_eq!(v.get_bool("dry_run"), Some(!env.is_empty()));
        assert_eq!(v.str_at(&["config", "state"]), Some("ok"));
        let hosts: Vec<&str> = v
            .arr_at(&["machines"])
            .iter()
            .filter_map(|m| m.get_str("hostname"))
            .collect();
        assert_eq!(hosts, ["lovelace", "babbage", "hopper"]);
        assert_eq!(
            v.arr_at(&["machines"])[0].get_bool("this_computer"),
            Some(true)
        );
    }
    // Missing: exit 1, still the document.
    std::fs::remove_file(t.p("/cdrom/.disk/backotter.json")).unwrap();
    let o = run(&["stick", "--json", "--sysroot", &root], &[], None);
    assert_eq!(o.status.code(), Some(1));
    assert_eq!(doc(&o).str_at(&["config", "state"]), Some("missing"));
    // Bad arguments: an error document under --json, the help with --help.
    let v = assert_error(
        &run(&["stick", "--json", "--frob"], &[], None),
        2,
        "bad_args",
    );
    assert_eq!(
        v.get_str("text"),
        Some("bko-holt stick: unknown argument \"--frob\"")
    );
    assert_error(&run(&["stick", "--json=2"], &[], None), 2, "schema_unknown");
    assert_error(
        &run(
            &["stick", "--json", "--sysroot", "/nonexistent-bko"],
            &[],
            None,
        ),
        2,
        "bad_args",
    );
    let o = run(&["stick"], &[], None);
    assert_eq!(o.status.code(), Some(2));
    assert!(out(&o).is_empty() && err(&o).contains("say --json"));
    let o = run(&["stick", "--json", "--help"], &[], None);
    assert_eq!(o.status.code(), Some(0));
    assert!(out(&o).starts_with("Usage: bko-holt stick --json"));
}

/// `bko-holt stick --copy INSTALL_ID OUT --json` (open question 8): the
/// layout the machine's system.json carries, written to a new private OUT,
/// which load then takes; under the dry-run lock too.
#[test]
fn stick_copy_writes_a_layout_load_takes() {
    use bko_holt::stick::sample;
    use std::os::unix::fs::PermissionsExt;
    let t = sample::linux_tree(&sample::config(sample::two_with_layouts()), None);
    let root = t.root().to_string_lossy().into_owned();
    let k = t.p("/k");
    std::fs::create_dir(&k).unwrap();
    std::fs::set_permissions(&k, std::fs::Permissions::from_mode(0o700)).unwrap();
    let path = |n: &str| k.join(n).to_string_lossy().into_owned();
    for (n, env) in [
        ("a.json", &[][..]),
        ("b.json", &[("BKO_HOLT_NO_COMMIT", "1")][..]),
    ] {
        let o = run(
            &[
                "stick",
                "--copy",
                sample::LOVELACE,
                &path(n),
                "--json",
                "--sysroot",
                &root,
            ],
            env,
            None,
        );
        assert_eq!(o.status.code(), Some(0), "{}{}", out(&o), err(&o));
        assert!(err(&o).is_empty(), "{}", err(&o));
        let v = doc(&o);
        assert_eq!(
            (v.get_str("format"), v.get_bool("dry_run")),
            (Some("bko-holt-stick-copy"), Some(!env.is_empty()))
        );
        assert_eq!(v.get_str("out"), Some(path(n).as_str()));
        let text = std::fs::read_to_string(path(n)).unwrap();
        assert_eq!(
            json::parse(&text).unwrap(),
            json::parse(sample::DUMP).unwrap()
        );
        assert_eq!(v.get_u64("bytes"), Some(text.len() as u64));
        assert_eq!(
            std::fs::metadata(path(n)).unwrap().permissions().mode() & 0o7777,
            0o600
        );
    }
    // load takes OUT as the dump it is: the same plan as the dump's own.
    let (_lt, layout, lroot) = setup();
    let plan = |file: &str| {
        // Its problems are this test host's (no target mapped, no tools).
        let o = run(
            &[
                "load",
                file,
                "--json",
                "--through",
                "plan",
                "--sysroot",
                &lroot,
            ],
            &[],
            None,
        );
        let mut v = doc(&o);
        assert_eq!(v.get_str("format"), Some("bko-holt-plan"), "{}", err(&o));
        for k in ["generated", "layout", "args", "text", "plan_sha256"] {
            v.put(k, Value::Null);
        }
        (o.status.code(), v)
    };
    assert_eq!(plan(&path("a.json")), plan(&layout));
    // Text mode's plan the same too, but for the layout's name.
    let text = |file: &str| {
        out(&run(
            &["load", file, "--only", "plan", "--sysroot", &lroot],
            &[],
            None,
        ))
        .replace(file, "LAYOUT")
    };
    assert_eq!(text(&path("a.json")), text(&layout));
    // A system.json given to load: refused, saying what takes its layout.
    let sj = t.p("/sj.json");
    std::fs::write(
        &sj,
        sample::system(
            sample::LOVELACE,
            "lovelace",
            "",
            &sample::with_layout(sample::DUMP),
        ),
    )
    .unwrap();
    let v = assert_error(
        &run(
            &[
                "load",
                sj.to_str().unwrap(),
                "--json",
                "--through",
                "plan",
                "--sysroot",
                &lroot,
            ],
            &[],
            None,
        ),
        3,
        "layout_refused",
    );
    assert!(
        v.get_str("text").unwrap().contains("stick --copy"),
        "{}",
        v.pretty()
    );
    // Refusals, each a document: OUT there already, not absolute, in a
    // directory others may enter or through a link; a bad ID; no machine.
    let v = assert_error(
        &run(
            &[
                "stick",
                "--copy",
                sample::LOVELACE,
                &path("a.json"),
                "--json",
                "--sysroot",
                &root,
            ],
            &[],
            None,
        ),
        2,
        "out_unsafe",
    );
    assert!(v.get_str("text").unwrap().contains("there already"));
    assert_error(
        &run(
            &[
                "stick",
                "--copy",
                sample::LOVELACE,
                "layout.json",
                "--json",
                "--sysroot",
                &root,
            ],
            &[],
            None,
        ),
        2,
        "out_unsafe",
    );
    std::fs::set_permissions(&k, std::fs::Permissions::from_mode(0o755)).unwrap();
    let v = assert_error(
        &run(
            &[
                "stick",
                "--copy",
                sample::LOVELACE,
                &path("c.json"),
                "--json",
                "--sysroot",
                &root,
            ],
            &[],
            None,
        ),
        2,
        "out_unsafe",
    );
    assert!(v.get_str("text").unwrap().contains("mode 755"));
    std::fs::set_permissions(&k, std::fs::Permissions::from_mode(0o700)).unwrap();
    std::os::unix::fs::symlink(&k, t.p("/link")).unwrap();
    let linked = t.p("/link/c.json").to_string_lossy().into_owned();
    assert_error(
        &run(
            &[
                "stick",
                "--copy",
                sample::LOVELACE,
                &linked,
                "--json",
                "--sysroot",
                &root,
            ],
            &[],
            None,
        ),
        2,
        "out_unsafe",
    );
    let v = assert_error(
        &run(
            &[
                "stick",
                "--copy",
                "../../etc",
                &path("c.json"),
                "--json",
                "--sysroot",
                &root,
            ],
            &[],
            None,
        ),
        2,
        "bad_args",
    );
    assert!(!v.get_str("text").unwrap().contains("etc"));
    assert_error(
        &run(&["stick", "--copy", sample::LOVELACE, "--json"], &[], None),
        2,
        "bad_args",
    );
    // A help flag as --copy's OUT or ID is a value, not the help.
    assert_error(
        &run(
            &["stick", "--copy", sample::LOVELACE, "-h", "--json"],
            &[],
            None,
        ),
        2,
        "bad_args",
    );
    assert_error(
        &run(
            &["stick", "--copy", "--help", &path("c.json"), "--json"],
            &[],
            None,
        ),
        2,
        "bad_args",
    );
    let v = assert_error(
        &run(
            &[
                "stick",
                "--copy",
                "2d3e4f5a-6b7c-4d8e-9fa0-b1c2d3e4f5a6",
                &path("c.json"),
                "--json",
                "--sysroot",
                &root,
            ],
            &[],
            None,
        ),
        1,
        "no_such_machine",
    );
    assert!(v.get_str("text").unwrap().contains("it has 2"));
    assert!(!k.join("c.json").exists());
    // Text mode: none.
    let o = run(
        &[
            "stick",
            "--copy",
            sample::LOVELACE,
            &path("c.json"),
            "--sysroot",
            &root,
        ],
        &[],
        None,
    );
    assert_eq!(o.status.code(), Some(2));
    assert!(out(&o).is_empty() && err(&o).contains("say --json") && !k.join("c.json").exists());
    // Not a stick: the config's state, exit 1.
    std::fs::remove_file(t.p("/cdrom/.disk/backotter.json")).unwrap();
    assert_error(
        &run(
            &[
                "stick",
                "--copy",
                sample::LOVELACE,
                &path("c.json"),
                "--json",
                "--sysroot",
                &root,
            ],
            &[],
            None,
        ),
        1,
        "config_not_ok",
    );
    assert!(!k.join("c.json").exists());
}

/// `bko-holt recovery-words OUT --json` (RT10p): the words on standard
/// input, the password to a new private OUT, the dry-run lock allowing it.
#[test]
fn recovery_words_is_a_document() {
    use std::os::unix::fs::PermissionsExt;
    let words = "legal winner thank year wave sausage worth useful legal winner thank yellow";
    let t = Tree::new("json-cli-recovery");
    let k = t.p("/k");
    std::fs::create_dir(&k).unwrap();
    std::fs::set_permissions(&k, std::fs::Permissions::from_mode(0o700)).unwrap();
    let pw = k.join("0").to_string_lossy().into_owned();
    let typed = format!("{}\n", words.to_uppercase());
    let (o, read) = run_in(
        &["recovery-words", &pw, "--json"],
        &[("BKO_HOLT_NO_COMMIT", "1")],
        Some(&typed),
    );
    assert_eq!(o.status.code(), Some(0), "{}", err(&o));
    assert_eq!(read, typed.len() as u64);
    assert!(err(&o).is_empty());
    let v = doc(&o);
    assert_eq!(
        (
            v.get_str("format"),
            v.get_bool("ok"),
            v.get_bool("written"),
            v.get_bool("dry_run")
        ),
        (
            Some("bko-holt-recovery"),
            Some(true),
            Some(true),
            Some(true)
        )
    );
    assert!(!out(&o).contains("legal") && !out(&o).contains("yellow"));
    assert_eq!(std::fs::read_to_string(&pw).unwrap(), words);
    assert_eq!(
        std::fs::metadata(&pw).unwrap().permissions().mode() & 0o777,
        0o600
    );
    // The same OUT again: refused before a word is read.
    let (o, read) = run_in(&["recovery-words", &pw, "--json"], &[], Some(words));
    assert_eq!(read, 0);
    let v = assert_error(&o, 2, "out_unsafe");
    assert!(v.get_str("text").unwrap().contains("there already"));
    // Wrong words: exit 1, nothing written.
    let pw1 = k.join("1").to_string_lossy().into_owned();
    let o = run(
        &["recovery-words", &pw1, "--json"],
        &[],
        Some("legal winner thank year wave sausage worth useful legal winner thank"),
    );
    assert_eq!(o.status.code(), Some(1));
    let v = doc(&o);
    assert_eq!(
        (v.get_bool("ok"), v.get_str("problem"), v.get_u64("words")),
        (Some(false), Some("count"), Some(11))
    );
    assert!(!std::path::Path::new(&pw1).exists());
    // A directory others may enter, more than 1 KiB, no --json, a word as OUT.
    std::fs::set_permissions(&k, std::fs::Permissions::from_mode(0o755)).unwrap();
    let (o, read) = run_in(&["recovery-words", &pw1, "--json"], &[], Some(words));
    assert_eq!(read, 0);
    assert_error(&o, 2, "out_unsafe");
    std::fs::set_permissions(&k, std::fs::Permissions::from_mode(0o700)).unwrap();
    let (o, read) = run_in(
        &["recovery-words", &pw1, "--json"],
        &[],
        Some(&"a ".repeat(600)),
    );
    assert_eq!(read, bko_holt::recoverycmd::STDIN_MAX as u64 + 1);
    assert_error(&o, 2, "bad_args");
    let (o, read) = run_in(&["recovery-words", &pw1], &[], Some(words));
    assert_eq!((o.status.code(), read), (Some(2), 0));
    assert!(out(&o).is_empty() && err(&o).contains("say --json"));
    let o = run(&["recovery-words", "legal", "winner", "--json"], &[], None);
    let v = assert_error(&o, 2, "bad_args");
    assert!(!v.get_str("text").unwrap().contains("legal") && !err(&o).contains("legal"));
    // One word in OUT's place: refused as OUT, the word not echoed.
    let (o, read) = run_in(&["recovery-words", "legal", "--json"], &[], Some(words));
    assert_eq!(read, 0);
    let v = assert_error(&o, 2, "out_unsafe");
    assert!(v.get_str("text").unwrap().contains("absolute"));
    assert!(
        !out(&o).contains("legal") && !err(&o).contains("legal"),
        "{}",
        err(&o)
    );
    assert!(!std::path::Path::new(&pw1).exists());
}

/// `bko-holt luks-headers LAYOUT DIR --repo REPO --password-file FILE
/// --json` (open question 12): the headers uc24-fde's dump kept, read from
/// the backup by the archiver's dump of /.bko-holt/luks-headers/FILE (here
/// a script serving test files, never a real header), each checked and
/// put in a new private DIR, which load's --luks-header-dir then takes.
#[test]
fn luks_headers_fetches_what_load_reads() {
    use std::os::unix::fs::PermissionsExt;
    let t = Tree::new("json-cli-luks");
    let k = t.p("/k");
    std::fs::create_dir(&k).unwrap();
    std::fs::set_permissions(&k, std::fs::Permissions::from_mode(0o700)).unwrap();
    let path = |n: &str| k.join(n).to_string_lossy().into_owned();
    std::fs::write(path("pw"), "repository password").unwrap();
    std::fs::set_permissions(path("pw"), std::fs::Permissions::from_mode(0o600)).unwrap();
    // The snapshot's files, and an archiver that serves them by the dump
    // argument's last part, logging its argv.
    let snap = t.p("/snap");
    std::fs::create_dir(&snap).unwrap();
    let arch = t.p("/rustic-bko");
    std::fs::write(
        &arch,
        format!(
            "#!/bin/sh\nprintf '%s\\n' \"$@\" >> '{}'\nfor a; do last=$a; done\nf='{}'/\"${{last##*/}}\"\n[ -f \"$f\" ] || {{ echo 'error: Path not found' >&2; exit 1; }}\ncat \"$f\"\n",
            t.p("/argv").display(),
            snap.display()
        ),
    )
    .unwrap();
    std::fs::set_permissions(&arch, std::fs::Permissions::from_mode(0o755)).unwrap();
    let arch = arch.to_string_lossy().into_owned();
    // uc24-fde's dump, its two headers' sizes and SHA-256 the test files'.
    let mut v = json::parse(include_str!("corpus/uc24-fde/src.json")).unwrap();
    let mut files = Vec::new();
    for h in v
        .at_mut(&["storage", "luks_headers"])
        .unwrap()
        .as_array_mut()
        .unwrap()
    {
        let file = h.get_str("file").unwrap().to_string();
        let body = format!("LUKS\u{ba}json_cli test header {file}\n").into_bytes();
        h.put("bytes", body.len());
        h.put("sha256", bko_holt::bytes::sha256_hex(&body));
        std::fs::write(snap.join(&file), &body).unwrap();
        files.push((file, body));
    }
    assert_eq!(files.len(), 2);
    let layout = t.p("/layout.json").to_string_lossy().into_owned();
    std::fs::write(&layout, v.pretty()).unwrap();
    let fetch = |dir: &str, extra: &[&str], env: &[(&str, &str)]| {
        let mut a = vec![
            "luks-headers",
            layout.as_str(),
            dir,
            "--repo",
            "sftp://nas.local/bko",
            "--password-file",
            "PW",
            "--archiver",
            arch.as_str(),
            "--json",
        ];
        let pw = path("pw");
        a.iter_mut()
            .filter(|x| **x == "PW")
            .for_each(|x| *x = pw.as_str());
        let mut a: Vec<String> = a.iter().map(ToString::to_string).collect();
        a.extend(extra.iter().map(ToString::to_string));
        let a: Vec<&str> = a.iter().map(String::as_str).collect();
        run(&a, env, None)
    };
    // Under the dry-run lock too: it reads the repository only.
    let o = fetch(
        &path("h"),
        &["--snapshot", "1a2b3c4d"],
        &[("BKO_HOLT_NO_COMMIT", "1")],
    );
    assert_eq!(o.status.code(), Some(0), "{}{}", out(&o), err(&o));
    assert!(err(&o).is_empty(), "{}", err(&o));
    let d = doc(&o);
    assert_eq!(
        (
            d.get_str("format"),
            d.get_u64("schema"),
            d.get_bool("dry_run")
        ),
        (Some("bko-holt-luks-headers"), Some(1), Some(true))
    );
    assert_eq!(
        (
            d.get_str("dir"),
            d.get_str("snapshot"),
            d.get_str("repo_dir")
        ),
        (
            Some(path("h").as_str()),
            Some("1a2b3c4d"),
            Some("/.bko-holt/luks-headers")
        )
    );
    let keys: Vec<&str> = d
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
            "layout",
            "dir",
            "snapshot",
            "repo_dir",
            "headers"
        ]
    );
    assert_eq!(
        std::fs::metadata(path("h")).unwrap().permissions().mode() & 0o7777,
        0o700
    );
    let rows = d.get_arr("headers").unwrap();
    assert_eq!(rows.len(), 2);
    for (r, (file, body)) in rows.iter().zip(&files) {
        assert_eq!(r.get_str("file"), Some(file.as_str()));
        let p = r.get_str("path").unwrap();
        assert_eq!(&std::fs::read(p).unwrap(), body);
        assert_eq!(
            std::fs::metadata(p).unwrap().permissions().mode() & 0o7777,
            0o600
        );
        assert_eq!(
            r.get_str("from"),
            Some(format!("1a2b3c4d:/.bko-holt/luks-headers/{file}").as_str())
        );
        let keys: Vec<&str> = r
            .as_object()
            .unwrap()
            .iter()
            .map(|(k, _)| k.as_str())
            .collect();
        assert_eq!(
            keys,
            ["device", "label", "file", "path", "from", "bytes", "sha256"]
        );
    }
    let argv = std::fs::read_to_string(t.p("/argv")).unwrap();
    assert!(argv.starts_with(&format!("--repository\nsftp://nas.local/bko\n--password-file\n{}\n--no-progress\ndump\n1a2b3c4d:/.bko-holt/luks-headers/", path("pw"))), "{argv}");
    assert!(!argv.contains("repository password") && !out(&o).contains("repository password"));
    // load takes DIR: no problem with the headers there (the missing
    // header's problem, naming this command, is scenarios.rs's, on mapped
    // disks); and the restore leaves the backup's copies out.
    let lt = Tree::new("json-cli-luks-load");
    lt.file("/proc/self/mountinfo", "")
        .file("/proc/swaps", "Filename\tType\tSize\tUsed\tPriority\n")
        .file("/proc/sys/kernel/arch", "x86_64\n");
    let lroot = lt.root().to_string_lossy().into_owned();
    let plan = |extra: &[&str]| {
        let mut a = vec![
            "load",
            layout.as_str(),
            "--json",
            "--through",
            "plan",
            "--sysroot",
            lroot.as_str(),
            "--repo",
            "/srv/r",
        ];
        a.extend(extra);
        let o = run(&a, &[], None);
        let v = doc(&o);
        assert_eq!(
            v.get_str("format"),
            Some("bko-holt-plan"),
            "{}{}",
            out(&o),
            err(&o)
        );
        v
    };
    let header_problems = |v: &Value| {
        v.get_arr("problems")
            .unwrap()
            .iter()
            .filter_map(|p| p.get_str("text"))
            .filter(|t| t.contains("LUKS2 header the dump kept"))
            .map(str::to_string)
            .collect::<Vec<_>>()
    };
    let with = plan(&["--luks-header-dir", &path("h")]);
    assert!(
        header_problems(&with).is_empty(),
        "{:?}",
        header_problems(&with)
    );
    // The headers, their directory, the snapshot's system.json (open
    // question 15) and /.bko-holt (no raw volume under it): nothing of the
    // backup tool's staging lands on the restored root.  Nothing else is
    // left out, so they are --glob arguments, with no glob file.
    assert!(
        with.get_str("text").unwrap().contains(
            "restore --numeric-id --glob '!/.bko-holt/luks-headers/**' --glob '!/.bko-holt/luks-headers' --glob '!/.bko-holt/system.json' --glob '!/.bko-holt' "
        ) && !with.get_str("text").unwrap().contains("restore.globs"),
        "{}",
        with.get_str("text").unwrap()
    );
    // Text mode with no password file: the archiver asks on standard error
    // (restic-bko's prompt), which is the terminal's, not swallowed.
    let asker = t.p("/restic-bko");
    std::fs::write(
        &asker,
        "#!/bin/sh\nprintf 'enter password for repository: ' >&2\nexit 1\n",
    )
    .unwrap();
    std::fs::set_permissions(&asker, std::fs::Permissions::from_mode(0o755)).unwrap();
    let o = run(
        &[
            "luks-headers",
            &layout,
            &path("h5"),
            "--repo",
            "/srv/r",
            "--archiver",
            &asker.to_string_lossy(),
        ],
        &[],
        None,
    );
    assert_eq!(o.status.code(), Some(1), "{}{}", out(&o), err(&o));
    assert!(
        err(&o).starts_with("enter password for repository: "),
        "{}",
        err(&o)
    );
    assert!(!k.join("h5").exists());
    // Refusals: DIR there already; nothing asks for the password under
    // --json; a password in the URL; a header the snapshot doesn't hold, or
    // another one (the layout's and the snapshot's differ): nothing left.
    let v = assert_error(&fetch(&path("h"), &[], &[]), 2, "out_unsafe");
    assert!(
        v.get_str("text")
            .unwrap()
            .contains("DIR must be a new directory"),
        "{}",
        v.pretty()
    );
    assert_error(
        &run(
            &[
                "luks-headers",
                &layout,
                &path("h2"),
                "--repo",
                "/srv/r",
                "--json",
            ],
            &[],
            None,
        ),
        2,
        "bad_args",
    );
    assert_error(
        &run(
            &[
                "luks-headers",
                &layout,
                &path("h2"),
                "--repo",
                "sftp://u:secret@nas/r",
                "--password-file",
                &path("pw"),
                "--json",
            ],
            &[],
            None,
        ),
        2,
        "bad_args",
    );
    assert_error(
        &run(
            &[
                "luks-headers",
                &layout,
                "h2",
                "--repo",
                "/srv/r",
                "--password-file",
                &path("pw"),
                "--json",
            ],
            &[],
            None,
        ),
        2,
        "out_unsafe",
    );
    std::fs::set_permissions(path("pw"), std::fs::Permissions::from_mode(0o644)).unwrap();
    let v = assert_error(&fetch(&path("h2"), &[], &[]), 2, "bad_args");
    assert!(
        v.get_str("text").unwrap().contains("mode 644"),
        "{}",
        v.pretty()
    );
    std::fs::set_permissions(path("pw"), std::fs::Permissions::from_mode(0o600)).unwrap();
    std::fs::write(
        snap.join(&files[1].0),
        b"LUKS\xba another snapshot's header, the same size?",
    )
    .unwrap();
    let v = assert_error(&fetch(&path("h2"), &[], &[]), 1, "header_mismatch");
    assert!(
        v.get_str("text")
            .unwrap()
            .contains("isn't the header the dump kept"),
        "{}",
        v.pretty()
    );
    assert!(!k.join("h2").exists());
    std::fs::remove_file(snap.join(&files[1].0)).unwrap();
    let v = assert_error(&fetch(&path("h2"), &[], &[]), 1, "header_fetch_failed");
    assert!(
        v.get_str("text")
            .unwrap()
            .ends_with("exited 1: error: Path not found"),
        "{}",
        v.pretty()
    );
    assert!(!k.join("h2").exists());
    let v = assert_error(
        &run(
            &[
                "luks-headers",
                &path("nope.json"),
                &path("h2"),
                "--repo",
                "/srv/r",
                "--password-file",
                &path("pw"),
                "--json",
            ],
            &[],
            None,
        ),
        1,
        "layout_unreadable",
    );
    assert!(v.get_str("text").unwrap().contains("nope.json"));
    // Text mode: the same fetch, a line a header; nothing asked here (a
    // password file), and the help.
    std::fs::write(snap.join(&files[1].0), &files[1].1).unwrap();
    let o = run(
        &[
            "luks-headers",
            &layout,
            &path("h3"),
            "--repo",
            "/srv/r",
            "--password-file",
            &path("pw"),
            "--archiver",
            &arch,
        ],
        &[],
        None,
    );
    assert_eq!(o.status.code(), Some(0), "{}", err(&o));
    assert_eq!(out(&o).lines().count(), 2, "{}", out(&o));
    assert!(out(&o).contains("vda5's LUKS2 header"), "{}", out(&o));
    let o = run(&["luks-headers", "--help"], &[], None);
    assert!(o.status.success() && out(&o).starts_with("Usage: bko-holt luks-headers"));
}

/// `bko-holt snapshot-layout OUT --repo REPO --password-file FILE
/// --snapshot ID --install-id ID --json` (open question 15): the layout a
/// snapshot's own system.json carries, read by the archiver's dump of
/// /.bko-holt/system.json (here a script serving a test file), checked as
/// `stick --copy` checks the stick's, and written to a new private OUT
/// that load takes; a snapshot without one is `no_system_json`.
#[test]
fn snapshot_layout_writes_the_snapshots_own_layout() {
    use std::os::unix::fs::PermissionsExt;
    const ID: &str = "0b7e3c52-8d1f-4a6e-9c3b-2f5d7a1e4b90";
    let t = Tree::new("json-cli-snaplayout");
    let k = t.p("/k");
    std::fs::create_dir(&k).unwrap();
    std::fs::set_permissions(&k, std::fs::Permissions::from_mode(0o700)).unwrap();
    let path = |n: &str| k.join(n).to_string_lossy().into_owned();
    std::fs::write(path("pw"), "repository password").unwrap();
    std::fs::set_permissions(path("pw"), std::fs::Permissions::from_mode(0o600)).unwrap();
    let sys = t.p("/system.json");
    let arch = t.p("/rustic-bko");
    std::fs::write(
        &arch,
        format!(
            "#!/bin/sh\nprintf '%s\\n' \"$@\" >> '{}'\n[ -f '{f}' ] || {{ printf 'Message:\\nNode `system.json` not found in tree.\\n' >&2; exit 1; }}\ncat '{f}'\n",
            t.p("/argv").display(),
            f = sys.display()
        ),
    )
    .unwrap();
    std::fs::set_permissions(&arch, std::fs::Permissions::from_mode(0o755)).unwrap();
    let arch = arch.to_string_lossy().into_owned();
    let dump = json::parse(include_str!("corpus/debian13-stack/src.json")).unwrap();
    let system = format!(
        "{{\"format\": \"backotter-system\", \"schema\": 1, \"sequence\": 41, \"machine\": {{\"install_id\": \"{ID}\"}}, \"layout\": {}}}",
        dump.compact()
    );
    std::fs::write(&sys, &system).unwrap();
    let pw = path("pw");
    let get = |o: &str, extra: &[&str], env: &[(&str, &str)]| {
        let mut a = vec![
            "snapshot-layout",
            o,
            "--repo",
            "sftp://nas.local/bko",
            "--password-file",
            pw.as_str(),
            "--archiver",
            arch.as_str(),
            "--json",
        ];
        a.extend(extra);
        run(&a, env, None)
    };
    // Under the dry-run lock too: it reads the repository only.
    let o = get(
        &path("layout.json"),
        &["--snapshot", "1a2b3c4d", "--install-id", ID],
        &[("BKO_HOLT_NO_COMMIT", "1")],
    );
    assert_eq!(o.status.code(), Some(0), "{}{}", out(&o), err(&o));
    assert!(err(&o).is_empty(), "{}", err(&o));
    let d = doc(&o);
    assert_eq!(
        (
            d.get_str("format"),
            d.get_bool("dry_run"),
            d.get_str("snapshot"),
            d.get_str("from"),
            d.get_str("install_id"),
            d.get_u64("sequence"),
            d.get_u64("luks_headers")
        ),
        (
            Some("bko-holt-snapshot-layout"),
            Some(true),
            Some("1a2b3c4d"),
            Some("1a2b3c4d:/.bko-holt/system.json"),
            Some(ID),
            Some(41),
            Some(0)
        )
    );
    let text = std::fs::read_to_string(path("layout.json")).unwrap();
    assert_eq!(text, dump.pretty());
    assert_eq!(
        std::fs::metadata(path("layout.json"))
            .unwrap()
            .permissions()
            .mode()
            & 0o7777,
        0o600
    );
    assert_eq!(d.get_u64("bytes"), Some(text.len() as u64));
    let argv = std::fs::read_to_string(t.p("/argv")).unwrap();
    assert_eq!(
        argv,
        format!(
            "--repository\nsftp://nas.local/bko\n--password-file\n{pw}\n--no-progress\ndump\n1a2b3c4d:/.bko-holt/system.json\n"
        )
    );
    assert!(!out(&o).contains("repository password"));
    // load takes OUT as it takes any dump.
    let lt = Tree::new("json-cli-snaplayout-load");
    lt.file("/proc/self/mountinfo", "")
        .file("/proc/swaps", "Filename\tType\tSize\tUsed\tPriority\n")
        .file("/proc/sys/kernel/arch", "x86_64\n");
    let lroot = lt.root().to_string_lossy().into_owned();
    let lp = path("layout.json");
    let o = run(
        &[
            "load",
            &lp,
            "--json",
            "--through",
            "plan",
            "--sysroot",
            &lroot,
        ],
        &[],
        None,
    );
    assert_eq!(
        doc(&o).get_str("format"),
        Some("bko-holt-plan"),
        "{}",
        err(&o)
    );
    // Another machine's, refused; no install ID asked: any machine's.
    let v = assert_error(
        &get(
            &path("2.json"),
            &["--install-id", "1c2d3e4f-5a6b-4c7d-8e9f-a0b1c2d3e4f5"],
            &[],
        ),
        1,
        "system_refused",
    );
    assert!(
        v.get_str("text")
            .unwrap()
            .contains("a backup of another machine"),
        "{}",
        v.pretty()
    );
    assert!(!k.join("2.json").exists());
    assert_eq!(get(&path("3.json"), &[], &[]).status.code(), Some(0));
    assert!(
        std::fs::read_to_string(t.p("/argv"))
            .unwrap()
            .ends_with("dump\nlatest:/.bko-holt/system.json\n")
    );
    // Refusals before the archiver runs: an install ID that isn't one (not
    // echoed), OUT there already or not absolute, no password file under
    // --json, a password in the URL.
    let o = get(&path("4.json"), &["--install-id", "../../etc/passwd"], &[]);
    let v = assert_error(&o, 2, "bad_args");
    assert!(!v.get_str("text").unwrap().contains("passwd") && !err(&o).contains("passwd"));
    assert_error(&get(&path("layout.json"), &[], &[]), 2, "out_unsafe");
    assert_error(&get("rel.json", &[], &[]), 2, "out_unsafe");
    assert_error(
        &run(
            &[
                "snapshot-layout",
                &path("4.json"),
                "--repo",
                "/srv/r",
                "--json",
            ],
            &[],
            None,
        ),
        2,
        "bad_args",
    );
    assert_error(
        &run(
            &[
                "snapshot-layout",
                &path("4.json"),
                "--repo",
                "sftp://u:secret@nas/r",
                "--password-file",
                &pw,
                "--json",
            ],
            &[],
            None,
        ),
        2,
        "bad_args",
    );
    // A snapshot without one: no_system_json, nothing left.
    std::fs::remove_file(&sys).unwrap();
    let v = assert_error(
        &get(&path("4.json"), &["--snapshot", "9f8e7d6c"], &[]),
        1,
        "no_system_json",
    );
    assert!(
        v.get_str("text")
            .unwrap()
            .contains("snapshot 9f8e7d6c holds no /.bko-holt/system.json"),
        "{}",
        v.pretty()
    );
    assert!(!k.join("4.json").exists());
    // Text mode: one line; and the help.
    std::fs::write(&sys, &system).unwrap();
    let o = run(
        &[
            "snapshot-layout",
            &path("5.json"),
            "--repo",
            "/srv/r",
            "--password-file",
            &pw,
            "--archiver",
            &arch,
        ],
        &[],
        None,
    );
    assert_eq!(o.status.code(), Some(0), "{}", err(&o));
    assert_eq!(out(&o).lines().count(), 1, "{}", out(&o));
    assert!(out(&o).contains(&format!("machine {ID}")), "{}", out(&o));
    let o = run(&["snapshot-layout", "--help"], &[], None);
    assert!(o.status.success() && out(&o).starts_with("Usage: bko-holt snapshot-layout"));
}
