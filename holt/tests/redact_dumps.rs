//! The redaction's proof on real dumps (`bugreport::check_plan`): for every
//! `*.json` under BKO_HOLT_DUMPS (a directory, searched recursively, for
//! example /bkoscratch/holttest1d/r3all with HoltTest1's dumps/src.json), the
//! load plan of the redacted dump is the original's, pseudonyms aside, and
//! no identifier is left in it.  Without BKO_HOLT_DUMPS it checks nothing.

use std::path::{Path, PathBuf};

fn jsons(dir: &Path, out: &mut Vec<PathBuf>) {
    let Ok(rd) = std::fs::read_dir(dir) else { return };
    let mut entries: Vec<PathBuf> = rd.filter_map(|e| e.ok().map(|e| e.path())).collect();
    entries.sort();
    for p in entries {
        if p.is_dir() {
            jsons(&p, out);
        } else if p.extension().is_some_and(|e| e == "json") && p.file_name().is_some_and(|n| n == "src.json" || std::env::var_os("BKO_HOLT_DUMPS_ALL").is_some()) {
            out.push(p);
        }
    }
}

#[test]
fn redacted_dumps_plan_the_same() {
    let Some(dir) = std::env::var_os("BKO_HOLT_DUMPS") else {
        eprintln!("BKO_HOLT_DUMPS isn't set: no real dumps checked");
        return;
    };
    let mut files = Vec::new();
    jsons(Path::new(&dir), &mut files);
    assert!(!files.is_empty(), "no src.json under {dir:?} (BKO_HOLT_DUMPS_ALL=1 takes every *.json)");
    let mut bad = Vec::new();
    for f in &files {
        let text = std::fs::read_to_string(f).unwrap();
        match bko_holt::bugreport::check_plan(&text) {
            Ok((ids, lines)) => println!("ok    {}: {ids} identifiers, plan of {lines} lines", f.display()),
            Err(e) => {
                println!("FAIL  {}: {e}", f.display());
                bad.push(format!("{}: {e}", f.display()));
            }
        }
    }
    assert!(bad.is_empty(), "{} of {} failed:\n{}", bad.len(), files.len(), bad.join("\n"));
}

/// RT10i: report-bug learns a plan document's disks too (`--attach`): for
/// every plan.json beside a src.json under BKO_HOLT_DUMPS (the corpus's
/// golden plan documents), the document redacted as report-bug redacts an
/// attachment keeps none of the identifiers learned from the dump or from
/// it.  Without BKO_HOLT_DUMPS it checks nothing.
#[test]
fn redacted_plan_documents_leave_nothing() {
    let Some(dir) = std::env::var_os("BKO_HOLT_DUMPS") else {
        eprintln!("BKO_HOLT_DUMPS isn't set: no plan documents checked");
        return;
    };
    let mut files = Vec::new();
    jsons(Path::new(&dir), &mut files);
    let mut bad = Vec::new();
    let mut n = 0;
    for f in files.iter().filter(|f| f.file_name().is_some_and(|x| x == "src.json")) {
        let plan = f.with_file_name("plan.json");
        let Ok(doc) = std::fs::read_to_string(&plan) else { continue };
        let src = bko_holt::json::parse(&std::fs::read_to_string(f).unwrap()).unwrap();
        let v = bko_holt::json::parse(&doc).unwrap_or_else(|e| panic!("{}: not JSON ({})", plan.display(), e.message));
        let mut c = bko_holt::redact::Collector::new();
        c.learn(&src);
        c.learn_text(&doc);
        c.learn(&v);
        let r = c.seal();
        let leaks = r.leaks(&r.text(&doc));
        n += 1;
        if leaks.is_empty() {
            println!("ok    {}", plan.display());
        } else {
            println!("FAIL  {}: {leaks:?}", plan.display());
            bad.push(format!("{}: {leaks:?}", plan.display()));
        }
    }
    println!("{n} plan documents checked");
    assert!(bad.is_empty(), "{} of {n} left identifiers:\n{}", bad.len(), bad.join("\n"));
}
