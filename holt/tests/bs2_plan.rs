//! BackShot2's tier 0 planner (docs/design/backshot2.md, "Tiers"): `load
//! plan` of each drifted layout, onto blank targets of its case's sizes with
//! its case's load options, held to golden_plans.rs's five invariants
//! (common/invariants.rs).  It plans as golden_plans.rs does (common::plan:
//! a fake rescue system with every tool installed), so it needs no disks,
//! no root and no VM, and thousands of cases run in one process.
//!
//! It does nothing unless BS2_PLAN_JOBS names a file of jobs, one JSON
//! object a line:
//!
//!   {"id": "CASE", "layout": "PATH.json", "targets": {"vda": BYTES}, "options": ["--fit-largest", "10"], "names": {"vda": "vdb"}}
//!
//! "names" gives each dumped disk's target its kernel name, as tier 1's
//! rescuer has it (virtio: vdb, vdc, ... after its own vda); a disk it
//! doesn't name, or a job without it, gets golden_plans.rs's sdq, sdr, ...
//! by its place (common::sd_target).  Load's Slackware check (Decision 5
//! (d), docs/design/slackware.md) judges a target by its name's driver
//! class and place, so a target named otherwise than tier 1's would be
//! refused at tier 0 and not at tier 1.
//!
//! and writes one line per job to BS2_PLAN_OUT (default: JOBS.out):
//!
//!   {"id": "CASE", "code": 0, "out": PLAN, "err": STDERR, "violations": [...], "checked": [...]}
//!
//! testvm/backshot2/bs2lib/tier0.py writes the jobs and reads the results.
//! The test is named bs2_*, so GateTest2's `--skip golden_` still runs it,
//! as a no-op without BS2_PLAN_JOBS.

mod common;

use bko_holt::json::{self, Value};
use std::collections::BTreeMap;

fn s(v: &str) -> Value {
    Value::Str(v.to_string())
}

fn list(v: &[String]) -> Value {
    Value::Arr(v.iter().map(|x| s(x)).collect())
}

fn one(line: &str) -> Value {
    let job = match json::parse(line) {
        Ok(j) => j,
        Err(e) => return Value::Obj(vec![("id".into(), s("?")), ("error".into(), s(&format!("job isn't JSON: {}", e.message)))]),
    };
    let id = job.get("id").and_then(Value::as_str).unwrap_or("?").to_string();
    let fail = |why: String| Value::Obj(vec![("id".into(), s(&id)), ("error".into(), s(&why))]);
    let Some(path) = job.get("layout").and_then(Value::as_str) else { return fail("no layout".into()) };
    let text = match std::fs::read_to_string(path) {
        Ok(t) => t,
        Err(e) => return fail(format!("{path}: {e}")),
    };
    let dump = match json::parse(&text) {
        Ok(d) => d,
        Err(e) => return fail(format!("{path}: not JSON: {}", e.message)),
    };
    let mut targets = BTreeMap::new();
    for (k, v) in job.get("targets").and_then(Value::as_object).into_iter().flatten() {
        if let Some(n) = v.as_u64() {
            targets.insert(k.clone(), n);
        }
    }
    let options: Vec<String> = job.get("options").and_then(Value::as_array).into_iter().flatten().filter_map(|x| x.as_str().map(str::to_string)).collect();
    let size_of = |name: &str, size: u64| targets.get(name).copied().unwrap_or(size);
    let mut names = BTreeMap::new();
    for (k, v) in job.get("names").and_then(Value::as_object).into_iter().flatten() {
        let Some(n) = v.as_str() else { return fail(format!("names: {k}'s target isn't a string")) };
        names.insert(k.clone(), n.to_string());
    }
    let name_of = |i: usize, disk: &str| names.get(disk).cloned().map_or_else(|| common::sd_target(i, disk), Ok);
    let p = match common::plan_onto_named(&text, &size_of, &options, &name_of) {
        Ok(p) => p,
        Err(e) => return fail(e),
    };
    let c = common::invariants::invariants(&dump, &p);
    Value::Obj(vec![
        ("id".into(), s(&id)),
        ("code".into(), Value::UInt(u64::from(p.code))),
        ("out".into(), s(&p.out)),
        ("err".into(), s(&p.err)),
        ("violations".into(), list(&c.violations)),
        ("checked".into(), list(&c.checked)),
        ("maps".into(), Value::Arr(p.maps.iter().map(|(a, b, n)| Value::Arr(vec![s(a), s(b), Value::UInt(*n)])).collect())),
    ])
}

#[test]
fn bs2_plan_jobs() {
    let Ok(jobs) = std::env::var("BS2_PLAN_JOBS") else { return };
    let out_path = std::env::var("BS2_PLAN_OUT").unwrap_or_else(|_| format!("{jobs}.out"));
    let text = std::fs::read_to_string(&jobs).unwrap_or_else(|e| panic!("{jobs}: {e}"));
    let mut out = String::new();
    for line in text.lines().filter(|l| !l.trim().is_empty()) {
        out += &one(line).compact();
        out.push('\n');
    }
    common::write_atomic(std::path::Path::new(&out_path), out.as_bytes()).unwrap();
}

#[test]
fn bs2_plan_a_corpus_dump_onto_a_smaller_target() {
    // alpine324's root (vda3) is the largest partition: a target 1 GiB
    // smaller plans with --fit-largest 10 and keeps the invariants.
    let path = std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("tests/corpus/alpine324/src.json");
    let text = std::fs::read_to_string(&path).unwrap();
    let dump = json::parse(&text).unwrap();
    let small = |_: &str, size: u64| size - (1 << 30);
    let p = common::plan_onto(&text, &small, &["--fit-largest".into(), "10".into()]).unwrap();
    assert_eq!(p.code, 0, "{}{}", p.out, p.err);
    assert_eq!(p.maps[0].2, 17179869184 - (1 << 30));
    let c = common::invariants::invariants(&dump, &p);
    assert!(c.violations.is_empty(), "{:?}", c.violations);
    // And without the flag, load refuses the smaller disk.
    let p = common::plan_onto(&text, &small, &[]).unwrap();
    assert_eq!(p.code, 4, "{}{}", p.out, p.err);
}

#[test]
fn bs2_plan_slackware_onto_the_rescuers_virtio_targets() {
    // slackware15 names its disk /dev/vda in lilo.conf and fstab (Decision 5
    // (d)): onto sdq, an sd disk, load refuses it (the golden plan); onto
    // vdb, tier 1's target after the rescuer's own vda, it plans, since vdb
    // is the first virtio target and boots as vda.
    // slackware15-uefi likewise, by elilo.conf's root=.
    let same = |_: &str, size: u64| size;
    for guest in ["slackware15", "slackware15-uefi"] {
        let path = std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join(format!("tests/corpus/{guest}/src.json"));
        let text = std::fs::read_to_string(&path).unwrap();
        let dump = json::parse(&text).unwrap();
        let sd = common::plan_onto(&text, &same, &[]).unwrap();
        assert_eq!(sd.code, 4, "{guest}: {}{}", sd.out, sd.err);
        assert!(sd.out.contains("on a disk of the sd class, where the source's was of the vd class"), "{guest}: {}", sd.out);
        let vd = common::plan_onto_named(&text, &same, &[], &|_, _| Ok("vdb".to_string())).unwrap();
        assert_eq!(vd.code, 0, "{guest}: {}{}", vd.out, vd.err);
        assert_eq!(vd.maps, vec![("vda".to_string(), "vdb".to_string(), sd.maps[0].2)]);
        assert!(!vd.out.contains("/dev/sdq"), "{guest}: {}", vd.out);
        let c = common::invariants::invariants(&dump, &vd);
        assert!(c.violations.is_empty(), "{guest}: {:?}", c.violations);
    }
    let text = std::fs::read_to_string(std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("tests/corpus/slackware15/src.json")).unwrap();
    // Only the targets count: a lone target vdc boots as vda too.
    let vdc = common::plan_onto_named(&text, &same, &[], &|_, _| Ok("vdc".to_string())).unwrap();
    assert_eq!(vdc.code, 0, "{}{}", vdc.out, vdc.err);
    // A bad name is the harness's mistake, never a plan.
    assert!(common::plan_onto_named(&text, &same, &[], &|_, _| Ok("../x".to_string())).is_err());
}
