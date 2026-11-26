//! The plan document's goldens (docs/design/holt-json.md, "Testing";
//! `RT10c`, RT10d): beside each tests/corpus/GUEST/plan.txt, a plan.json,
//! `bko-holt load`'s `--json` document of the same dump with the same
//! arguments plus `--json` (`common::plan_onto`), kept pretty-printed with its
//! `generated` and `version` replaced, and its `plan_sha256`: a hash over
//! the commands as they run, which name the test's own binary and tree
//! (common names them `bko-holt` and HEADERS in the document only).  Each
//! must hold:
//!
//!   - the document as kept (`BKO_HOLT_JSON_GOLDEN=bless` writes it again);
//!   - its `text` is the golden plan.txt's load output byte for byte, but
//!     for a private write's text (J18), which it leaves out;
//!   - the schema below: every field the plan document has, of its type;
//!   - under the 8 MiB a reader takes;
//!   - its size verdicts are fit's own: a `smaller` at `fit_pct` P fits with
//!     --fit-largest P and not P - 1, and where the figures are given,
//!     `data_fits` is `needed_bytes` <= `most_bytes`.
//!
//! The corpus's plans give --repo without --password-file and no --key-file
//! for LUKS, so most plan.json's are ok: false with `password_needed` and
//! `key_needed`: blessed as they are, since that is what a --json run of
//! those arguments is.  The documents are Linux's (load's run directory is
//! /run/bko-holt there): bless refuses to run on another system.

mod common;

use bko_holt::json::{self, Value};
use std::path::{Path, PathBuf};

fn corpus() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("tests")
        .join("corpus")
}

/// The corpus's guests with a golden plan.txt, in name order.
fn guests() -> Vec<String> {
    let mut out: Vec<String> = std::fs::read_dir(corpus())
        .unwrap()
        .filter_map(Result::ok)
        .filter(|e| e.path().join("src.json").is_file() && e.path().join("plan.txt").is_file())
        .map(|e| e.file_name().to_string_lossy().into_owned())
        .collect();
    out.sort();
    out
}

/// The plan document of GUEST's dump, as plan.json keeps it: pretty, its
/// `generated` and `version` replaced.  And the document's compact size.
fn document(guest: &str) -> (String, Value, usize) {
    let src = std::fs::read_to_string(corpus().join(guest).join("src.json")).unwrap();
    let p = common::plan_onto(&src, &|_, size| size, &["--json".to_string()])
        .unwrap_or_else(|e| panic!("{guest}: {e}"));
    // load's document is its first line; common's status lines follow.
    let line = p.out.lines().next().unwrap_or_default().to_string();
    let mut v = json::parse(&line)
        .unwrap_or_else(|e| panic!("{guest}: not one JSON document ({}): {}", e.message, p.out));
    if let Value::Obj(kv) = &mut v {
        for (k, x) in kv.iter_mut() {
            match k.as_str() {
                "generated" => *x = Value::from("GENERATED"),
                "version" => *x = Value::from("VERSION"),
                // Over the commands as run, whose paths are the test's own
                // (its binary for write-table, its tree for kept headers).
                "plan_sha256" => {
                    assert!(
                        x.as_str().is_some_and(
                            |h| h.len() == 64 && h.bytes().all(|b| b.is_ascii_hexdigit())
                        ),
                        "{guest}: plan_sha256 {}",
                        x.compact()
                    );
                    *x = Value::from("PLAN_SHA256");
                }
                _ => {}
            }
        }
    }
    (v.pretty() + "\n", v, line.len())
}

/// plan.txt's load output: the golden text up to `golden_plans.rs`'s own
/// lines.
fn load_output(plan_txt: &str) -> String {
    let mut out = String::new();
    for l in plan_txt.split_inclusive('\n') {
        if l.starts_with(common::GOLDEN_MARK) {
            break;
        }
        out += l;
    }
    out
}

/// Whether TEXT is PLAN but for private writes' text, each heredoc body
/// there replaced by one `# (N bytes, private: not shown)` line of its N
/// bytes.
fn same_but_private(text: &str, plan: &str) -> Result<(), String> {
    let (a, b): (Vec<&str>, Vec<&str>) = (
        text.split_inclusive('\n').collect(),
        plan.split_inclusive('\n').collect(),
    );
    let (mut i, mut j) = (0, 0);
    while i < a.len() && j < b.len() {
        if a[i] == b[j] {
            i += 1;
            j += 1;
            continue;
        }
        let n: Option<usize> = a[i]
            .strip_prefix("# (")
            .and_then(|r| r.strip_suffix(" bytes, private: not shown)\n"))
            .and_then(|n| n.parse().ok());
        let (Some(n), Some(eof)) = (n, a.get(i + 1)) else {
            return Err(format!(
                "line {}: {:?} where plan.txt has {:?}",
                i + 1,
                a[i],
                b[j]
            ));
        };
        let mut bytes = 0;
        while j < b.len() && b[j] != *eof {
            bytes += b[j].len();
            j += 1;
        }
        if bytes != n {
            return Err(format!(
                "line {}: a private write of {n} bytes, and plan.txt's is {bytes}",
                i + 1
            ));
        }
        i += 1;
    }
    if i < a.len() || j < b.len() {
        return Err(format!(
            "the texts end apart: line {} of {}, line {} of {}",
            i,
            a.len(),
            j,
            b.len()
        ));
    }
    Ok(())
}

/// What each field of the plan document is.
#[derive(Clone, Copy)]
enum T {
    Str,
    OptStr,
    U64,
    OptU64,
    Bool,
    OptBool,
    Num,
    Arr,
    Obj,
    OptObj,
    Any,
}

fn is(t: T, v: &Value) -> bool {
    match t {
        T::Str => v.as_str().is_some(),
        T::OptStr => v.is_null() || v.as_str().is_some(),
        T::U64 => v.as_u64().is_some(),
        T::OptU64 => v.is_null() || v.as_u64().is_some(),
        T::Bool => v.as_bool().is_some(),
        T::OptBool => v.is_null() || v.as_bool().is_some(),
        T::Num => matches!(
            v,
            Value::Null | Value::Int(_) | Value::UInt(_) | Value::Float(_)
        ),
        T::Arr => v.as_array().is_some(),
        T::Obj => v.as_object().is_some(),
        T::OptObj => v.is_null() || v.as_object().is_some(),
        T::Any => true,
    }
}

/// Every field of FIELDS in V, of its type; where it is.
fn fields(at: &str, v: &Value, fields: &[(&str, T)], errs: &mut Vec<String>) {
    for (k, t) in fields {
        match v.get(k) {
            None => errs.push(format!("{at}: no {k}")),
            Some(x) if !is(*t, x) => {
                errs.push(format!("{at}.{k}: {} isn't of its type", x.compact()));
            }
            _ => {}
        }
    }
}

const DISK: [(&str, T); 10] = [
    ("node", T::Str),
    ("path", T::Str),
    ("by_id", T::OptStr),
    ("size_bytes", T::U64),
    ("model", T::OptStr),
    ("serial", T::OptStr),
    ("wwid", T::OptStr),
    ("sector", T::U64),
    ("transport", T::OptStr),
    ("removable", T::OptBool),
];

const VERDICT: [(&str, T); 16] = [
    ("source", T::Str),
    ("verdict", T::Str),
    ("fit_pct", T::OptU64),
    ("change_pct", T::Num),
    ("partition", T::OptStr),
    ("mount", T::OptStr),
    ("old_bytes", T::OptU64),
    ("new_bytes", T::OptU64),
    ("used_bytes", T::OptU64),
    ("margin_bytes", T::OptU64),
    ("needed_bytes", T::OptU64),
    ("most_bytes", T::OptU64),
    ("data_fits", T::OptBool),
    ("reason", T::OptStr),
    ("grow", T::Any),
    ("why", T::OptStr),
];

const VERDICTS: [&str; 7] = [
    "same",
    "smaller_slack",
    "smaller",
    "bigger",
    "data_wont_fit",
    "cant_resize",
    "beyond_limit",
];

/// The plan document's schema (holt-json.md, "bko-holt-plan"): what bko-
/// rescue reads.  `RT10h`'s `testvm/rescue/holt_schema.py` is the shared one.
fn schema(v: &Value) -> Vec<String> {
    let mut e = Vec::new();
    fields(
        "plan",
        v,
        &[
            ("format", T::Str),
            ("schema", T::U64),
            ("version", T::Str),
            ("generated", T::Str),
            ("dry_run", T::Bool),
            ("ok", T::Bool),
            ("exit", T::U64),
            ("plan_sha256", T::Str),
            ("layout", T::Obj),
            ("args", T::Arr),
            ("source", T::Obj),
            ("here", T::Obj),
            ("disks", T::Arr),
            ("candidates", T::Arr),
            ("erase", T::Arr),
            ("keys", T::Arr),
            ("tokens", T::Arr),
            ("reformat", T::Arr),
            ("fit", T::Arr),
            ("problems", T::Arr),
            ("notes", T::Arr),
            ("stages", T::Arr),
            ("text", T::Str),
        ],
        &mut e,
    );
    if v.get_str("format") != Some("bko-holt-plan") || v.get_u64("schema") != Some(1) {
        e.push("plan: not bko-holt-plan schema 1".into());
    }
    if v.get_bool("ok") != Some(v.get_u64("exit") == Some(0))
        || v.get_bool("ok") != v.get_arr("problems").map(Vec::is_empty)
    {
        e.push("plan: ok, exit and problems disagree".into());
    }
    if let Some(l) = v.get("layout") {
        fields("layout", l, &[("path", T::Str), ("sha256", T::Str)], &mut e);
    }
    if let Some(s) = v.get("source") {
        fields(
            "source",
            s,
            &[
                ("hostname", T::OptStr),
                ("os", T::Obj),
                ("family", T::Str),
                ("arch", T::OptStr),
                ("boot_mode", T::Str),
                ("loader", T::OptStr),
                ("generated", T::OptStr),
                ("dmi_uuid", T::OptStr),
                ("reformat_possible", T::Bool),
            ],
            &mut e,
        );
    }
    if let Some(h) = v.get("here") {
        fields(
            "here",
            h,
            &[
                ("dmi_uuid", T::OptStr),
                ("boot_mode", T::OptStr),
                ("efivars", T::OptBool),
                ("arch", T::Str),
                ("privileged", T::Bool),
                ("same_machine", T::Bool),
            ],
            &mut e,
        );
    }
    let verdicts = |at: &str, x: &Value, e: &mut Vec<String>| {
        fields(at, x, &VERDICT, e);
        if !x.get_str("verdict").is_some_and(|w| VERDICTS.contains(&w)) {
            e.push(format!("{at}: verdict {:?}", x.get("verdict")));
        }
    };
    for (i, d) in v.arr_at(&["disks"]).iter().enumerate() {
        let at = format!("disks[{i}]");
        fields(&at, d, &[("source", T::Obj), ("target", T::OptObj)], &mut e);
        if let Some(s) = d.get("source") {
            fields(
                &format!("{at}.source"),
                s,
                &[
                    ("name", T::Str),
                    ("size_bytes", T::U64),
                    ("model", T::OptStr),
                    ("serial", T::OptStr),
                    ("wwid", T::OptStr),
                    ("sector", T::U64),
                    ("table", T::OptStr),
                    ("mounts", T::Arr),
                    ("largest", T::OptObj),
                ],
                &mut e,
            );
        }
        if let Some(t) = d.get("target").filter(|t| !t.is_null()) {
            let at = format!("{at}.target");
            fields(&at, t, &DISK, &mut e);
            fields(
                &at,
                t,
                &[
                    ("matched_by", T::Str),
                    ("same_identity", T::Bool),
                    ("blank", T::Bool),
                    ("contents", T::Arr),
                    ("ours", T::Bool),
                    ("recorded", T::Bool),
                    ("erase", T::Bool),
                    ("will_erase", T::Bool),
                    ("writes", T::Arr),
                    ("size", T::OptObj),
                ],
                &mut e,
            );
            if let Some(z) = t.get("size").filter(|z| !z.is_null()) {
                verdicts(&format!("{at}.size"), z, &mut e);
            }
        }
    }
    for (i, c) in v.arr_at(&["candidates"]).iter().enumerate() {
        let at = format!("candidates[{i}]");
        fields(&at, c, &DISK, &mut e);
        fields(
            &at,
            c,
            &[
                ("serial_shared", T::Bool),
                ("target_of", T::OptStr),
                ("ours", T::Bool),
                ("blank", T::Bool),
                ("contents", T::Arr),
                ("refused", T::OptObj),
                ("erase_needed", T::Bool),
                ("sizes", T::Arr),
            ],
            &mut e,
        );
        for (j, ct) in c.arr_at(&["contents"]).iter().enumerate() {
            fields(
                &format!("{at}.contents[{j}]"),
                ct,
                &[
                    ("dev", T::Str),
                    ("type", T::Str),
                    ("label", T::OptStr),
                    ("size_bytes", T::U64),
                    ("used_bytes", T::OptU64),
                ],
                &mut e,
            );
        }
        for (j, z) in c.arr_at(&["sizes"]).iter().enumerate() {
            verdicts(&format!("{at}.sizes[{j}]"), z, &mut e);
        }
        if let Some(r) = c.get("refused").filter(|r| !r.is_null()) {
            fields(
                &format!("{at}.refused"),
                r,
                &[("code", T::Str), ("text", T::Str)],
                &mut e,
            );
        }
    }
    for (i, k) in v.arr_at(&["keys"]).iter().enumerate() {
        fields(
            &format!("keys[{i}]"),
            k,
            &[
                ("group", T::Str),
                ("kind", T::Str),
                ("names", T::Arr),
                ("devices", T::Arr),
                ("holds", T::Arr),
                ("unlock", T::Str),
                ("given", T::Bool),
                ("new_ok", T::Bool),
                ("min_bytes", T::U64),
                ("max_bytes", T::U64),
                ("shared_at_boot", T::Bool),
            ],
            &mut e,
        );
    }
    for (i, p) in v
        .arr_at(&["problems"])
        .iter()
        .chain(v.arr_at(&["notes"]))
        .enumerate()
    {
        fields(
            &format!("problems/notes[{i}]"),
            p,
            &[("code", T::Str), ("text", T::Str)],
            &mut e,
        );
    }
    for (i, s) in v.arr_at(&["stages"]).iter().enumerate() {
        fields(
            &format!("stages[{i}]"),
            s,
            &[("name", T::Str), ("commands", T::Arr)],
            &mut e,
        );
        for (j, c) in s.arr_at(&["commands"]).iter().enumerate() {
            fields(
                &format!("stages[{i}].commands[{j}]"),
                c,
                &[
                    ("kind", T::Str),
                    ("argv", T::Arr),
                    ("env", T::Arr),
                    ("why", T::OptStr),
                    ("path", T::OptStr),
                    ("dev", T::OptStr),
                    ("stdin", T::OptStr),
                    ("stdin_bytes", T::OptU64),
                    ("stdin_sha256", T::OptStr),
                    ("private", T::Bool),
                    ("archiver", T::Bool),
                ],
                &mut e,
            );
            if c.get_bool("private") == Some(true) && !c.get("stdin").is_some_and(Value::is_null) {
                e.push(format!("stages[{i}].commands[{j}]: a private write's text"));
            }
        }
    }
    e
}

/// Every corpus plan.json: as kept, its text the plan.txt's, its schema,
/// its size.
#[test]
fn corpus_plan_documents() {
    let bless = match std::env::var("BKO_HOLT_JSON_GOLDEN").as_deref() {
        Ok("bless") => true,
        Ok("check") | Err(_) => false,
        Ok(other) => panic!("BKO_HOLT_JSON_GOLDEN={other:?}: check or bless"),
    };
    assert!(
        !bless || cfg!(target_os = "linux"),
        "the plan documents are Linux's: bless them on a Linux host"
    );
    let mut errs = Vec::new();
    let mut largest = 0;
    let list = guests();
    assert!(list.len() > 50, "{} guests with a plan.txt", list.len());
    for g in &list {
        let (pretty, v, size) = document(g);
        largest = largest.max(size);
        if size >= 8 << 20 {
            errs.push(format!("{g}: {size} bytes, over the 8 MiB a reader takes"));
        }
        let plan_txt = std::fs::read_to_string(corpus().join(g).join("plan.txt")).unwrap();
        if let Err(e) = same_but_private(
            v.get_str("text").unwrap_or_default(),
            &load_output(&plan_txt),
        ) {
            errs.push(format!("{g}: text isn't plan.txt's: {e}"));
        }
        for e in schema(&v) {
            errs.push(format!("{g}: {e}"));
        }
        let path = corpus().join(g).join("plan.json");
        if bless {
            common::write_atomic(&path, pretty.as_bytes()).unwrap();
            continue;
        }
        match std::fs::read_to_string(&path) {
            Ok(kept) if kept == pretty => {}
            Ok(kept) => errs.push(format!(
                "{g}: plan.json differs ({} lines changed; BKO_HOLT_JSON_GOLDEN=bless takes it)",
                common::lines_changed(&kept, &pretty)
            )),
            Err(_) => errs.push(format!(
                "{g}: no plan.json (BKO_HOLT_JSON_GOLDEN=bless writes it)"
            )),
        }
    }
    eprintln!("{} plan documents, the largest {largest} bytes", list.len());
    assert!(errs.is_empty(), "{}", errs.join("\n"));
}

/// The size verdicts are fit's own, on every corpus dump's used disks onto
/// disks 2, 10, 30 and 60 % smaller and 10 % bigger: a `smaller` at P fits
/// with --fit-largest P and not P - 1, a `smaller_slack` at 1; a `bigger`
/// at P takes the whole extra there and not at P - 1; a `beyond_limit` is
/// refused at --fit-largest's most, its data fitting; and `data_fits` is
/// `needed_bytes` <= `most_bytes` wherever both are given.
#[test]
fn corpus_size_verdicts_are_fits() {
    use bko_holt::load::fit::{Target, fit, verdict};
    let mut errs = Vec::new();
    let mut seen = std::collections::BTreeMap::<&str, usize>::new();
    for g in guests() {
        let src = std::fs::read_to_string(corpus().join(&g).join("src.json")).unwrap();
        let v = json::parse(&src).unwrap();
        for (name, size, ss) in common::dumped_disks(&v) {
            if v.at(&["storage", "disks"])
                .and_then(Value::as_array)
                .into_iter()
                .flatten()
                .all(|d| {
                    d.get_str("name") != Some(&name)
                        || (d.get("partition_table").is_none_or(Value::is_null)
                            && d.get("content").is_none_or(Value::is_null))
                })
            {
                continue;
            }
            for (num, den) in [(98u64, 100u64), (90, 100), (70, 100), (40, 100), (110, 100)] {
                let bytes = size / den * num / u64::from(ss) * u64::from(ss);
                let z = verdict(&v, &name, "sdq", bytes);
                let at = format!("{g} {name} at {num}%");
                *seen.entry(z.verdict).or_default() += 1;
                let t = Target {
                    disk: name.clone(),
                    path: "/dev/sdq".into(),
                    size_bytes: bytes,
                };
                match (z.verdict, z.fit_pct) {
                    ("smaller" | "smaller_slack", Some(p)) => {
                        if !fit(&v, std::slice::from_ref(&t), p).problems.is_empty() {
                            errs.push(format!("{at}: {} at {p}%, and fit refuses {p}%", z.verdict));
                        }
                        if p > 1 && fit(&v, std::slice::from_ref(&t), p - 1).problems.is_empty() {
                            errs.push(format!(
                                "{at}: {} at {p}%, and fit takes {}%",
                                z.verdict,
                                p - 1
                            ));
                        }
                    }
                    ("smaller" | "smaller_slack", None) => {
                        errs.push(format!("{at}: {} without a percentage", z.verdict));
                    }
                    ("bigger", Some(p)) => {
                        let f = fit(&v, std::slice::from_ref(&t), p);
                        if !f.problems.is_empty()
                            || f.disks.first().is_none_or(|d| d.unused_bytes != 0)
                        {
                            errs.push(format!(
                                "{at}: bigger at {p}%, and fit's doesn't take the whole extra there"
                            ));
                        }
                        // The least such percentage.
                        if p > 1 {
                            let f = fit(&v, std::slice::from_ref(&t), p - 1);
                            if f.problems.is_empty()
                                && f.disks.first().is_some_and(|d| d.unused_bytes == 0)
                            {
                                errs.push(format!(
                                    "{at}: bigger at {p}%, and fit's takes the whole extra at {}%",
                                    p - 1
                                ));
                            }
                        }
                    }
                    ("beyond_limit", p) => {
                        // More than --fit-largest takes: fit refuses its most.
                        if fit(&v, std::slice::from_ref(&t), bko_holt::load::FIT_MAX)
                            .problems
                            .is_empty()
                        {
                            errs.push(format!(
                                "{at}: beyond_limit, and fit takes it at {}%",
                                bko_holt::load::FIT_MAX
                            ));
                        }
                        if p.is_some_and(|p| p <= bko_holt::load::FIT_MAX)
                            || z.data_fits != Some(true)
                        {
                            errs.push(format!(
                                "{at}: beyond_limit at {p:?}, data_fits {:?}",
                                z.data_fits
                            ));
                        }
                    }
                    ("data_wont_fit" | "cant_resize", _) => {
                        if fit(&v, std::slice::from_ref(&t), bko_holt::load::FIT_MAX)
                            .problems
                            .is_empty()
                        {
                            errs.push(format!(
                                "{at}: {}, and fit takes it at {}%",
                                z.verdict,
                                bko_holt::load::FIT_MAX
                            ));
                        }
                    }
                    _ => {}
                }
                if let (Some(n), Some(m)) = (z.needed_bytes, z.most_bytes) {
                    if z.data_fits != Some(n <= m) {
                        errs.push(format!(
                            "{at}: data_fits {:?}, needed {n} and most {m}",
                            z.data_fits
                        ));
                    }
                }
            }
        }
    }
    eprintln!("verdicts: {seen:?}");
    assert!(
        seen.get("smaller").copied().unwrap_or(0) > 5
            && seen.get("bigger").copied().unwrap_or(0) > 5,
        "{seen:?}"
    );
    assert!(errs.is_empty(), "{}", errs.join("\n"));
}

#[test]
fn private_text_comparison() {
    let plan = "a\ncat > /x <<'EOF'\npassword=1\nimage=/v\nEOF\nb\n";
    let text = "a\ncat > /x <<'EOF'\n# (20 bytes, private: not shown)\nEOF\nb\n";
    assert_eq!(same_but_private(text, plan), Ok(()));
    assert!(same_but_private(&text.replace("20", "19"), plan).is_err());
    assert!(same_but_private("a\n", plan).is_err());
    assert_eq!(same_but_private(plan, plan), Ok(()));
}
