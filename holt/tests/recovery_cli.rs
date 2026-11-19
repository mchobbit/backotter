//! bko-recovery-words, the program to try recovery keys with
//! (src/recovery.rs has the module's own tests).

use bko_holt::recovery;
use std::io::Write;
use std::process::{Command, Output, Stdio};

fn run(args: &[&str], stdin: Option<&str>) -> Output {
    let mut c = Command::new(env!("CARGO_BIN_EXE_bko-recovery-words"));
    c.args(args).stdin(Stdio::piped()).stdout(Stdio::piped()).stderr(Stdio::piped());
    let mut child = c.spawn().unwrap();
    {
        let mut i = child.stdin.take().unwrap();
        if let Some(s) = stdin {
            i.write_all(s.as_bytes()).unwrap();
        }
    }
    child.wait_with_output().unwrap()
}

fn out(o: &Output) -> String {
    String::from_utf8(o.stdout.clone()).unwrap()
}

fn err(o: &Output) -> String {
    String::from_utf8(o.stderr.clone()).unwrap()
}

/// `new`'s words: the "Recovery key:" line's, after checking that the
/// numbered rows say the same.
fn new_words() -> String {
    let o = run(&["new"], None);
    assert_eq!(o.status.code(), Some(0), "{}", err(&o));
    assert!(err(&o).is_empty());
    let s = out(&o);
    let line = s.lines().next().unwrap();
    let words = line.strip_prefix("Recovery key: ").unwrap().to_string();
    assert_eq!(words.split(' ').count(), 12);
    let numbered: Vec<&str> = s.lines().skip(2).take(3).flat_map(|l| l.split_whitespace()).collect();
    assert_eq!(numbered.len(), 24, "{s}");
    for (i, pair) in numbered.chunks(2).enumerate() {
        assert_eq!(pair[0], (i + 1).to_string());
        assert_eq!(pair[1], words.split(' ').nth(i).unwrap());
    }
    words
}

#[test]
fn new_prints_twelve_good_words() {
    let a = new_words();
    let b = new_words();
    assert_ne!(a, b);
    for w in [&a, &b] {
        assert_eq!(recovery::password(&recovery::check(w).unwrap()), *w);
    }
}

#[test]
fn check_from_arguments_and_stdin() {
    let full = "legal winner thank year wave sausage worth useful legal winner thank yellow";
    for args in [
        vec!["check", "lega winn than year wave saus wort usef lega winn than yell"],
        vec!["check", "LEGAL", "Winner", "thank", "year", "wave", "sausage", "worth", "useful", "legal", "winner", "thank", "yellow"],
    ] {
        let o = run(&args, None);
        assert_eq!(o.status.code(), Some(0), "{}", err(&o));
        assert_eq!(out(&o), format!("OK: {full}\n"));
    }
    let o = run(&["check"], Some("1. lega 2. winn 3. than 4. year\n5. wave 6. saus 7. wort 8. usef\n9. lega 10. winn 11. than 12. yell\n"));
    assert_eq!(o.status.code(), Some(0), "{}", err(&o));
    assert_eq!(out(&o), format!("OK: {full}\n"));
    // A new key checks out, from standard input.
    let w = new_words();
    let o = run(&["check"], Some(&w));
    assert_eq!(out(&o), format!("OK: {w}\n"));
}

#[test]
fn check_says_what_is_wrong() {
    let o = run(&["check", "legal winner thnak year wave sausage worth useful legal winner thank yellow"], None);
    assert_eq!(o.status.code(), Some(1));
    assert!(out(&o).is_empty());
    assert_eq!(err(&o), "bko-recovery-words: word 3, \"thnak\", isn't one of the recovery key's words (did you mean thank?)\n");
    let o = run(&["check", "legal winner thank year wave sausage worth useful legal winner thank"], None);
    assert_eq!(o.status.code(), Some(1));
    assert_eq!(err(&o), "bko-recovery-words: 11 words: a recovery key has 12\n");
    let o = run(&["check", "winner legal thank year wave sausage worth useful legal winner thank yellow"], None);
    assert_eq!(o.status.code(), Some(1));
    assert!(err(&o).ends_with("one of the words is wrong or the order is swapped\n"), "{}", err(&o));
    let o = run(&["check"], Some(""));
    assert_eq!(o.status.code(), Some(1));
    assert_eq!(err(&o), "bko-recovery-words: no words: a recovery key has 12\n");
    // Two problems: a line each.
    let o = run(&["check", "lega xq thank year"], None);
    assert_eq!(
        err(&o),
        "bko-recovery-words: word 2, \"xq\", isn't one of the recovery key's words\nbko-recovery-words: and there are 4 words: a recovery key has 12\n"
    );
}

#[test]
fn arguments() {
    for a in [&["-h"][..], &["--help"], &["check", "--help"], &["new", "-h"]] {
        let o = run(a, None);
        assert_eq!(o.status.code(), Some(0));
        assert!(out(&o).starts_with("Usage: bko-recovery-words new\n"));
    }
    let o = run(&["-V"], None);
    assert_eq!(out(&o), format!("bko-recovery-words (bko-holt {})\n", env!("CARGO_PKG_VERSION")));
    for (a, why) in [
        (&[][..], "say new or check"),
        (&["new", "now"], "new takes no arguments"),
        (&["abandon", "ability"], "unknown arguments: say new or check"),
    ] {
        let o = run(a, None);
        assert_eq!(o.status.code(), Some(2), "{a:?}");
        assert!(err(&o).starts_with(&format!("bko-recovery-words: {why}\n\nUsage:")), "{}", err(&o));
        // Words given without "check" aren't said back.
        assert!(!err(&o).contains("ability"));
    }
}
