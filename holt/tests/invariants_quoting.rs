//! common/invariants.rs reads a plan's words as sh does (`RustAudit1`
//! H10-01): load quotes an operand with `shell_quote`, so a mount point with
//! a space or a quote is one word, and the fstab invariant takes the dump's
//! `file` as dump decoded it, not decoded again.

mod common;

use bko_holt::json::{self, Value};
use common::Planned;
use common::invariants::*;

fn w(v: &[&str]) -> Vec<String> {
    v.iter().map(ToString::to_string).collect()
}

#[test]
fn a_quoted_operand_is_one_word() {
    assert_eq!(
        commands("mount -t ext4 /dev/sdq2 '/mnt/bko-target/mnt/my data'"),
        [w(&[
            "mount",
            "-t",
            "ext4",
            "/dev/sdq2",
            "/mnt/bko-target/mnt/my data"
        ])]
    );
    // shell_quote's '\'' is a quote inside the word.
    assert_eq!(
        commands("mount /dev/sdq2 '/mnt/bko-target/it'\\''s'"),
        [w(&["mount", "/dev/sdq2", "/mnt/bko-target/it's"])]
    );
    // A backslash inside '...' is itself.
    assert_eq!(
        commands("mount /dev/sdq2 '/mnt/bko-target/lit\\040'"),
        [w(&["mount", "/dev/sdq2", "/mnt/bko-target/lit\\040"])]
    );
    // In sh -c's script too, where the operand's quotes are '\''.
    let line = "sh -c 'mkdir -p '\\''/mnt/bko-target/my data'\\'' && mount /dev/sdq2 '\\''/mnt/bko-target/my data'\\'''";
    assert_eq!(
        commands(line),
        [
            w(&["mkdir", "-p", "/mnt/bko-target/my data"]),
            w(&["mount", "/dev/sdq2", "/mnt/bko-target/my data"])
        ]
    );
    let s = Step {
        cmd: line.to_string(),
        body: vec![],
    };
    assert!(mounts_at(&s, "/mnt/bko-target/my data"));
    assert!(!mounts_at(&s, "data"));
    assert_eq!(
        words("sfdisk --no-reread '/dev/my disk'; true"),
        w(&["sfdisk", "--no-reread", "/dev/my disk", "true"])
    );
}

#[test]
fn separators_and_redirections_inside_quotes_are_text() {
    assert_eq!(
        commands("sh -c 'echo \"a; b\" | cat'"),
        [w(&["echo", "a; b"]), w(&["cat"])]
    );
    assert_eq!(
        commands("echo 'a > b' c >/run/x 2>&1"),
        [w(&["echo", "a > b", "c"])]
    );
    assert_eq!(
        commands("printf \"%s\\\\n\" \"x \\\"y\\\" \\$z\""),
        [w(&["printf", "%s\\n", "x \"y\" $z"])]
    );
    // The script a chroot runs: its commands too.
    let c = commands("chroot /mnt/bko-target sh -c 'mkdir -p /x && mount --bind / /x'");
    assert_eq!(c[0][..4], w(&["chroot", "/mnt/bko-target", "sh", "-c"]));
    assert_eq!(
        c[1..],
        [
            w(&["mkdir", "-p", "/x"]),
            w(&["mount", "--bind", "/", "/x"])
        ]
    );
    // An unclosed quote runs to the end.
    assert_eq!(commands("mount a 'b c"), [w(&["mount", "a", "b c"])]);
}

#[test]
fn unescape_leaves_what_isnt_a_byte() {
    assert_eq!(unescape("/srv/my\\040disk"), "/srv/my disk");
    // \400 is past \377: as it is written, and no overflow.
    assert_eq!(unescape("/x\\400"), "/x\\400");
    assert_eq!(unescape("/x\\777y"), "/x\\777y");
    assert_eq!(unescape("/x\\377"), "/x\u{fffd}");
}

/// gentoo's dump with its fstab's /home at FILE (its only `"file":
/// "/home"`), the dump, its plan, and the invariants.
fn gentoo_home_at(file: &str) -> (Value, Planned, Check) {
    let text = std::fs::read_to_string(
        std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("tests/corpus/gentoo/src.json"),
    )
    .unwrap();
    let from = "\"file\": \"/home\"";
    assert_eq!(
        text.matches(from).count(),
        1,
        "gentoo's fstab has one /home"
    );
    let text = text.replace(
        from,
        &format!("\"file\": {}", Value::Str(file.into()).compact()),
    );
    let v = json::parse(&text).unwrap();
    let p = common::plan(&text, 0).unwrap();
    let c = invariants(&v, &p);
    (v, p, c)
}

#[test]
fn a_mount_point_load_quotes_is_planned() {
    // A space (fstab's \040, which dump decoded), a literal backslash
    // (fstab's \134040, dump's "\040"), and a \400 that isn't a byte.
    for file in ["/my home", "/lit\\040", "/x\\400"] {
        let (_, p, c) = gentoo_home_at(file);
        assert_eq!(p.code, 0, "{file}: {}\n{}", p.out, p.err);
        // shell_quote's form: each needs quotes.
        let at = format!("'{TARGET}{file}'");
        assert!(
            p.out
                .lines()
                .any(|l| l.starts_with("mount ") && l.ends_with(&at)),
            "{file}: no mount at {at}:\n{}",
            p.out
        );
        assert!(c.violations.is_empty(), "{file}: {:?}", c.violations);
        assert!(
            c.checked.iter().any(|x| x == "3 fstab"),
            "{file}: {:?}",
            c.checked
        );
    }
    // And one load doesn't mount is still a violation.
    let (v, p, _) = gentoo_home_at("/my home");
    let at = format!("'{TARGET}/my home'");
    let line = p
        .out
        .lines()
        .find(|l| l.starts_with("mount ") && l.ends_with(&at))
        .unwrap()
        .to_string();
    let bad = Planned {
        out: p.out.replacen(&format!("{line}\n"), "", 1),
        ..p.clone()
    };
    let c = invariants(&v, &bad);
    assert!(
        c.violations
            .iter()
            .any(|x| x.starts_with("every fstab mount planned: fstab's /my home")),
        "{:?}",
        c.violations
    );
}
