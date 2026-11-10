//! What golden_plans.rs and corpus_build.rs share: how a corpus dump is
//! planned, the golden text of a plan, and how two plans differ by stage.
//!
//! The planning is `bugreport::check_plan`'s, with the public API only: a
//! fake rescue system (testutil::Tree) with a blank disk sd{q+i} of each
//! dumped disk's size (GROW bytes more for the `+bigger` variant, as
//! ht1.sh's bigger rebuilds onto disks 1 GiB bigger), `--map NAME=TARGET`,
//! `--repo /srv/bko-repo`, a rustic-bko whose `--version` says it's
//! BackOtter's, and every tool the plan asks for installed.  Three things
//! differ from check_plan, for plans that read the same on every build:
//! RAM disks and ZFS volumes get no target (load leaves them out, and a
//! --map for one is a Problem), load's own binary (the test's) is installed
//! where it runs from, and the plan names it `bko-holt`.  It uses only
//! `bko_holt::{testutil::Tree, sys::{Sys, FakeRunner}, load::{parse_args,
//! run, Parsed, layout::Layout, plan::check}, json}`, which are as they were
//! before 3671e96, so the test runs on older trees too (the replay of #16).

#![allow(dead_code)]

pub mod invariants;

use bko_holt::json::{self, Value};
use bko_holt::load::{parse_args, run, Parsed};
use bko_holt::sys::{FakeRunner, Sys};
use bko_holt::testutil::Tree;

/// The fake archiver's answer to `--version`.
pub const RUSTIC_VERSION: &str = "rustic 0.11.4+backotter.4\n";
/// The repository every golden plan restores from.
pub const REPO: &str = "/srv/bko-repo";
/// The layout's name in the plan's first line and its "To run it" line.
pub const LAYOUT: &str = "layout.json";
/// The +bigger variant's targets: this much bigger than the source's disks.
pub const BIGGER: u64 = 1 << 30;

/// One plan: load's exit status, standard output and standard error, and
/// the disk mapping it ran with (source disk, target, target bytes).
#[derive(Debug, Clone, PartialEq)]
pub struct Planned {
    pub code: u8,
    pub out: String,
    pub err: String,
    pub maps: Vec<(String, String, u64)>,
}

/// The dumped disks load maps: (name, size in bytes, logical sector size).
/// Like load's Layout::from_json, and unlike check_plan, it leaves out RAM
/// disks and ZFS volumes: a --map for one is only a Problem ("the layout has
/// no disk ram0"), and alpine324 has 16 of them.
pub fn dumped_disks(v: &Value) -> Vec<(String, u64, u32)> {
    let mut out = Vec::new();
    for d in v.at(&["storage", "disks"]).and_then(Value::as_array).into_iter().flatten() {
        let Some(name) = d.get("name").and_then(Value::as_str) else { continue };
        if d.get("kind").and_then(Value::as_str) == Some("ram") || d.get("links").and_then(Value::as_array).into_iter().flatten().filter_map(Value::as_str).any(|l| l.starts_with("/dev/zvol/")) {
            continue;
        }
        let size = d.get("size_bytes").and_then(Value::as_u64).unwrap_or(1 << 34);
        let ss = d.get("logical_sector_size").and_then(Value::as_u64).unwrap_or(512) as u32;
        out.push((name.to_string(), size, ss));
    }
    out
}

/// Plans LAYOUT_TEXT onto blank targets GROW bytes bigger than its disks.
pub fn plan(layout_text: &str, grow: u64) -> Result<Planned, String> {
    plan_onto(layout_text, &|_, size| size + grow, &[])
}

/// Plans LAYOUT_TEXT onto blank targets of SIZE_OF(disk, its size) bytes,
/// with EXTRA arguments after load's own (BackShot2's tier 0: a drifted
/// layout, its case's targets and load options).
pub fn plan_onto(layout_text: &str, size_of: &dyn Fn(&str, u64) -> u64, extra: &[String]) -> Result<Planned, String> {
    let v = json::parse(layout_text).map_err(|e| format!("not JSON: {}", e.message))?;
    let t = Tree::new("golden-plan");
    t.file("/proc/self/mountinfo", "").file("/proc/swaps", "Filename\tType\tSize\tUsed\tPriority\n");
    let mut args = vec![LAYOUT.to_string()];
    let mut maps = Vec::new();
    for (i, (name, size, ss)) in dumped_disks(&v).into_iter().enumerate() {
        if i >= 10 {
            return Err(format!("{} disks: the targets sdq..sdz hold 10", i + 1));
        }
        let tgt = format!("sd{}", (b'q' + i as u8) as char);
        let bytes = size_of(&name, size);
        t.disk(&tgt, &format!("8:{}", 16 * i), &[], ss).file(&format!("/sys/block/{tgt}/size"), format!("{}\n", bytes / 512));
        args.push("--map".into());
        args.push(format!("{name}={tgt}"));
        maps.push((name, tgt, bytes));
    }
    args.extend(["--repo".to_string(), REPO.to_string()]);
    args.extend(extra.iter().cloned());
    let opts = match parse_args(&args) {
        Ok(Parsed::Run(o)) => o,
        Ok(Parsed::Help) => return Err(format!("load's arguments {args:?} asked for help")),
        Err(e) => return Err(format!("load's arguments {args:?}: {e}")),
    };
    t.file("/usr/sbin/rustic-bko", "");
    // load runs itself (write-table, md-lbs) as std::env::current_exe(),
    // which is this test's binary: installed where it is, as a rescue
    // system has bko-holt, and named `bko-holt` in the plan, which would
    // otherwise hold the build's own path.
    let exe = std::env::current_exe().map(|p| p.to_string_lossy().into_owned()).unwrap_or_default();
    if exe.starts_with('/') {
        t.file(&exe, "");
    }
    let sys = || Sys::new(t.root(), Box::new(FakeRunner::default().with("/usr/sbin/rustic-bko --version", RUSTIC_VERSION)), true);
    // Every tool the plan asks for, installed as a rescue system has it: a
    // path where it says, a name in /usr/sbin.
    let mut res = run(&opts, layout_text, &sys());
    for _ in 0..4 {
        let missing: Vec<String> = res.1.lines().filter_map(|l| l.trim_start_matches("#   - ").split_once(" isn't installed here").map(|(p, _)| p.to_string())).collect();
        if missing.is_empty() {
            break;
        }
        for m in missing {
            if m.starts_with('/') {
                t.file(&m, "");
            } else {
                t.file(&format!("/usr/sbin/{}", m.rsplit('/').next().unwrap_or(&m)), "");
            }
        }
        res = run(&opts, layout_text, &sys());
    }
    let named = |s: String| if exe.len() > 1 { s.replace(&exe, "bko-holt") } else { s };
    let mut out = named(res.1);
    // What `status` looks for after each stage: load builds it with the
    // commands but never prints it, and a change to it moves no command.
    if let Ok(l) = bko_holt::load::layout::Layout::from_json(v.clone()) {
        let b = bko_holt::load::plan::check(&l, &opts, &sys()).built;
        if !out.is_empty() && !out.ends_with('\n') {
            out.push('\n');
        }
        for (src, node) in &b.made {
            out += &format!("{GOLDEN_MARK} status made {src} {node}\n");
        }
        for p in &b.pools {
            out += &format!("{GOLDEN_MARK} status pool {p}\n");
        }
        for (node, ty, uuid) in &b.filesystems {
            out += &format!("{GOLDEN_MARK} status filesystem {node} {ty} {}\n", uuid.as_deref().unwrap_or("-"));
        }
        for d in &b.datasets {
            out += &format!("{GOLDEN_MARK} status dataset {d}\n");
        }
        for m in &b.mount_points {
            out += &format!("{GOLDEN_MARK} status mount {m}\n");
        }
    }
    Ok(Planned { code: res.0, out, err: named(res.2), maps })
}

/// Writes TEXT to PATH whole or not at all: a `.NAME.part` beside it, then a
/// rename, so a killed run leaves the old file or the new one, never half.
pub fn write_atomic(path: &std::path::Path, text: &[u8]) -> Result<(), String> {
    let name = path.file_name().map(|n| n.to_string_lossy().into_owned()).unwrap_or_default();
    let tmp = path.with_file_name(format!(".{name}.part"));
    std::fs::write(&tmp, text).map_err(|e| format!("{}: {e}", tmp.display()))?;
    std::fs::rename(&tmp, path).map_err(|e| format!("{}: {e}", path.display()))
}

/// The marker of golden_plans.rs's own lines after load's output.
pub const GOLDEN_MARK: &str = "# golden_plans.rs:";

/// A plan as plan.txt holds it: load's standard output as it is, then the
/// exit status and standard error.
pub fn golden_text(p: &Planned) -> String {
    let mut s = p.out.clone();
    if !s.is_empty() && !s.ends_with('\n') {
        s.push('\n');
    }
    if p.err.is_empty() {
        s += &format!("{GOLDEN_MARK} exit {}, nothing on stderr\n", p.code);
    } else {
        s += &format!("{GOLDEN_MARK} exit {}; stderr:\n", p.code);
        for l in p.err.lines() {
            s += &format!("#   {l}\n");
        }
    }
    s
}

/// load's stages, in the order plans print them.
pub const STAGES: [&str; 7] = ["partition", "assemble", "format", "mount", "restore", "boot", "close"];

/// The name in a stage header line, `# ---- STAGE ----`.
pub fn stage_header(line: &str) -> Option<&str> {
    let name = line.strip_prefix("# ---- ")?.strip_suffix(" ----")?;
    (!name.is_empty() && name.bytes().all(|c| c.is_ascii_lowercase() || c.is_ascii_digit() || c == b'_' || c == b'-')).then_some(name)
}

/// TEXT split into its sections: `header` (everything before the first
/// stage header), each stage (from its header line), and `trailer`: from the
/// first line after the last stage header (anywhere, without one) that
/// starts "# Nothing was written" (plan.rs's last line) or is
/// golden_text's own, to the end.  testvm/test2/t2lib/plandiff.py splits the
/// same way.
pub fn sections(text: &str) -> Vec<(String, Vec<&str>)> {
    let lines: Vec<&str> = text.lines().collect();
    let last_stage = lines.iter().rposition(|l| stage_header(l).is_some());
    let from = last_stage.map(|i| i + 1).unwrap_or(0);
    let trailer = (from..lines.len()).find(|&i| lines[i].starts_with("# Nothing was written") || lines[i].starts_with(GOLDEN_MARK));
    let mut out: Vec<(String, Vec<&str>)> = vec![("header".to_string(), Vec::new())];
    for (i, l) in lines.iter().enumerate() {
        if Some(i) == trailer {
            out.push(("trailer".to_string(), Vec::new()));
        } else if let Some(name) = stage_header(l).filter(|_| trailer.is_none_or(|t| i < t)) {
            out.push((name.to_string(), Vec::new()));
        }
        out.last_mut().unwrap().1.push(l);
    }
    out
}

/// The sections whose lines differ between OLD and NEW (one only in either
/// counts), in the order: header, the stages as STAGES orders them, any
/// other stage by first appearance, trailer.
pub fn stages_changed(old: &str, new: &str) -> Vec<String> {
    let (a, b) = (sections(old), sections(new));
    let mut names: Vec<String> = vec!["header".into()];
    names.extend(STAGES.iter().map(|s| s.to_string()));
    for (n, _) in a.iter().chain(b.iter()) {
        if !names.contains(n) && n != "trailer" {
            names.push(n.clone());
        }
    }
    names.push("trailer".into());
    let get = |s: &[(String, Vec<&str>)], n: &str| -> Option<Vec<String>> {
        let parts: Vec<&Vec<&str>> = s.iter().filter(|(m, _)| m == n).map(|(_, l)| l).collect();
        (!parts.is_empty()).then(|| parts.into_iter().flatten().map(|l| l.to_string()).collect())
    };
    names.into_iter().filter(|n| get(&a, n) != get(&b, n)).collect()
}

/// How many lines a line diff of OLD and NEW removes and adds (the longest
/// common subsequence kept).
pub fn lines_changed(old: &str, new: &str) -> usize {
    let a: Vec<&str> = old.lines().collect();
    let b: Vec<&str> = new.lines().collect();
    let pre = a.iter().zip(&b).take_while(|(x, y)| x == y).count();
    let suf = a[pre..].iter().rev().zip(b[pre..].iter().rev()).take_while(|(x, y)| x == y).count();
    let (a, b) = (&a[pre..a.len() - suf], &b[pre..b.len() - suf]);
    if a.is_empty() || b.is_empty() {
        return a.len() + b.len();
    }
    // LCS by rows; plans are a few thousand lines at most.
    let mut prev = vec![0u32; b.len() + 1];
    let mut cur = vec![0u32; b.len() + 1];
    for x in a {
        for (j, y) in b.iter().enumerate() {
            cur[j + 1] = if x == y { prev[j] + 1 } else { prev[j + 1].max(cur[j]) };
        }
        std::mem::swap(&mut prev, &mut cur);
    }
    a.len() + b.len() - 2 * prev[b.len()] as usize
}
