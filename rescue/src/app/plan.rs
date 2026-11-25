//! bko-holt's plan and status documents (holt-json.md, `bko-holt-plan`,
//! `bko-holt-status`), read into what the Disks, Passphrases, Resume and
//! Review screens show, and the screens' size rule (J5: the 10 % line is
//! bko-rescue's; every figure is bko-holt's).
//!
//! Unknown values fail closed (Principle 6): a `verdict` this doesn't
//! know can't be taken, a `refused` code refuses the disk, an `unlock`
//! kind blocks, a problem code blocks with its text, a `state` counts as
//! not done.  Strings are kept as they came; the screens clean them.

use crate::json::Value;

/// The schemas these read.
pub const PLAN_SCHEMA: u64 = 1;
pub const STATUS_SCHEMA: u64 = 1;

/// The most of a plan document read (holt-json.md, "Common rules"), and
/// of a status document.
pub const PLAN_MAX: usize = 8 * 1024 * 1024;
pub const STATUS_MAX: usize = 64 * 1024;

/// How far a disk may differ before the screens stop offering a change by
/// themselves (rescue-screens.md, open question 3, answered 2026-11-24):
/// up to this, an offer; beyond it, the user's decision (smaller) or the
/// keep-or-grow screen (bigger).  holt-json.md's J5 puts it here.
pub const OFFER_PCT: f64 = 10.0;

/// The verdicts this knows (holt-json.md, "Disks of another size").
pub const VERDICTS: &[&str] = &["same", "smaller_slack", "smaller", "bigger", "data_wont_fit", "cant_resize", "beyond_limit"];

/// The plan problems that are the Disks screen's to settle: a mapping, a
/// target, a size (holt-json.md, "Plan problems").
pub const DISK_CODES: &[&str] = &[
    "map_unknown_source",
    "no_such_disk",
    "not_mapped",
    "target_twice",
    "boot_disk",
    "running_root",
    "target_too_small",
    "sector_size",
    "read_only",
    "in_use",
    "held_unreleasable",
    "ours_busy",
    "fitted_other_pct",
    "erase_not_target",
    "duplicate_vg",
    "pool_left_out",
    "wedge_clash",
    "fit_refused",
    "fit_over_pct",
    "data_wont_fit",
    "data_unknown",
];

/// Problems the typed serials answer: the Review's `--erase` (holt-json.md,
/// J19).  They never stop the Disks screen.
pub const ERASE_CODES: &[&str] = &["held", "not_blank", "erase_required"];

/// The keys' problems, the Passphrases screen's.
pub const KEY_CODES: &[&str] = &["key_needed", "kept_header_key_needed", "key_file_unrestorable"];

fn st(v: &Value, k: &str) -> Option<String> {
    v.get_str(k).map(str::to_string)
}

fn strs(v: &Value, k: &str) -> Vec<String> {
    v.arr_at(&[k]).iter().filter_map(Value::as_str).map(str::to_string).collect()
}

fn f64_of(v: &Value, k: &str) -> Option<f64> {
    match v.get(k)? {
        Value::Float(f) => Some(*f),
        Value::UInt(u) => Some(*u as f64),
        Value::Int(i) => Some(*i as f64),
        _ => None,
    }
}

#[derive(Clone, Debug, Default)]
pub struct Largest {
    pub partition: Option<String>,
    pub size_bytes: Option<u64>,
    pub mount: Option<String>,
    pub used_bytes: Option<u64>,
}

/// An old disk, as the backup recorded it.
#[derive(Clone, Debug, Default)]
pub struct Source {
    pub name: String,
    pub size_bytes: u64,
    pub model: Option<String>,
    pub serial: Option<String>,
    pub mounts: Vec<String>,
    pub largest: Option<Largest>,
}

#[derive(Clone, Debug, Default)]
pub struct Content {
    pub dev: String,
    pub kind: Option<String>,
    pub label: Option<String>,
    pub used_bytes: Option<u64>,
}

/// A size verdict for one (old disk, candidate) pair.
#[derive(Clone, Debug, Default)]
pub struct Size {
    pub source: String,
    pub verdict: String,
    pub fit_pct: Option<u64>,
    pub change_pct: Option<f64>,
    pub partition: Option<String>,
    pub mount: Option<String>,
    pub old_bytes: Option<u64>,
    pub new_bytes: Option<u64>,
    pub used_bytes: Option<u64>,
    pub needed_bytes: Option<u64>,
    pub most_bytes: Option<u64>,
    pub data_fits: Option<bool>,
    pub reason: Option<String>,
    pub why: Option<String>,
    /// `grow.possible`, with RT10n's `--fit SRC=fill`: null today.
    pub grow_to: Option<u64>,
}

/// A whole disk of this computer.
#[derive(Clone, Debug, Default)]
pub struct Disk {
    pub node: String,
    pub path: String,
    pub by_id: Option<String>,
    pub size_bytes: u64,
    pub model: Option<String>,
    pub serial: Option<String>,
    pub serial_shared: bool,
    pub target_of: Option<String>,
    pub blank: bool,
    pub contents: Vec<Content>,
    /// The code and text, when it is never a target.
    pub refused: Option<(String, String)>,
    pub sizes: Vec<Size>,
}

impl Disk {
    /// What `--map` and `--erase` name it by: its most stable link, else
    /// its path (holt-json.md, `by_id`).
    pub fn arg(&self) -> String {
        self.by_id.clone().unwrap_or_else(|| self.path.clone())
    }

    pub fn size_for(&self, source: &str) -> Option<&Size> {
        self.sizes.iter().find(|s| s.source == source)
    }
}

/// An old disk and where it goes in this plan.
#[derive(Clone, Debug, Default)]
pub struct Pair {
    pub source: Source,
    pub target: Option<Target>,
}

#[derive(Clone, Debug, Default)]
pub struct Target {
    pub node: String,
    pub matched_by: String,
    pub same_identity: bool,
    pub will_erase: bool,
    pub writes: Vec<String>,
}

#[derive(Clone, Debug, Default)]
pub struct Key {
    pub group: String,
    pub kind: String,
    pub names: Vec<String>,
    pub holds: Vec<String>,
    pub unlock: String,
    pub new_ok: bool,
    pub min_bytes: u64,
    pub max_bytes: u64,
}

#[derive(Clone, Debug, Default)]
pub struct Reformat {
    pub mount: String,
    pub kind: String,
    /// Each type it could become: None (allowed) or bko-holt's refusal.
    pub to: Vec<(String, Option<String>)>,
}

#[derive(Clone, Debug, Default)]
pub struct Fitted {
    pub disk: String,
    pub target: String,
    pub sector: u64,
    pub old_sectors: Option<u64>,
    pub new_sectors: Option<u64>,
}

#[derive(Clone, Debug, Default)]
pub struct Problem {
    pub code: String,
    pub text: String,
    pub disk: Option<String>,
}

#[derive(Clone, Debug, Default)]
pub struct Plan {
    pub ok: bool,
    pub plan_sha256: String,
    pub hostname: Option<String>,
    pub os_name: Option<String>,
    pub family: Option<String>,
    pub reformat_possible: bool,
    pub same_machine: bool,
    pub disks: Vec<Pair>,
    pub candidates: Vec<Disk>,
    pub keys: Vec<Key>,
    pub tokens: usize,
    pub reformat: Vec<Reformat>,
    pub fit: Vec<Fitted>,
    pub problems: Vec<Problem>,
    pub notes: Vec<Problem>,
    pub text: String,
}

impl Plan {
    pub fn candidate(&self, node: &str) -> Option<&Disk> {
        self.candidates.iter().find(|c| c.node == node)
    }

    /// The disk this plan puts old disk `source` on.
    pub fn target_of(&self, source: &str) -> Option<&Disk> {
        let node = self.disks.iter().find(|p| p.source.name == source)?.target.as_ref()?.node.clone();
        self.candidate(&node)
    }

    /// The problems of these codes.
    pub fn problems_in<'a>(&'a self, codes: &'a [&str]) -> impl Iterator<Item = &'a Problem> + 'a {
        self.problems.iter().filter(move |p| codes.contains(&p.code.as_str()))
    }
}

/// The plan document, once it is one this reads.
pub fn read_plan(v: &Value) -> Result<Plan, String> {
    if v.get_u64("schema") != Some(PLAN_SCHEMA) {
        return Err(format!("a plan of schema {:?}", v.get("schema").map(Value::compact)));
    }
    let size = |x: &Value| -> Size {
        Size {
            source: st(x, "source").unwrap_or_default(),
            verdict: st(x, "verdict").unwrap_or_default(),
            fit_pct: x.get_u64("fit_pct"),
            change_pct: f64_of(x, "change_pct"),
            partition: st(x, "partition"),
            mount: st(x, "mount"),
            old_bytes: x.get_u64("old_bytes"),
            new_bytes: x.get_u64("new_bytes"),
            used_bytes: x.get_u64("used_bytes"),
            needed_bytes: x.get_u64("needed_bytes"),
            most_bytes: x.get_u64("most_bytes"),
            data_fits: x.get_bool("data_fits"),
            reason: st(x, "reason"),
            why: st(x, "why"),
            grow_to: x.get("grow").filter(|g| g.get_bool("possible") == Some(true)).and_then(|g| g.get_u64("to_bytes")),
        }
    };
    let disk = |x: &Value| -> Result<Disk, String> {
        let node = st(x, "node").filter(|n| !n.is_empty()).ok_or("a disk with no node")?;
        Ok(Disk {
            path: st(x, "path").unwrap_or_else(|| format!("/dev/{node}")),
            node,
            by_id: st(x, "by_id"),
            size_bytes: x.get_u64("size_bytes").unwrap_or(0),
            model: st(x, "model"),
            serial: st(x, "serial").filter(|s| !s.trim().is_empty()),
            serial_shared: x.get_bool("serial_shared") == Some(true),
            target_of: st(x, "target_of"),
            blank: x.get_bool("blank") == Some(true),
            contents: x
                .arr_at(&["contents"])
                .iter()
                .map(|c| Content { dev: st(c, "dev").unwrap_or_default(), kind: st(c, "type"), label: st(c, "label"), used_bytes: c.get_u64("used_bytes") })
                .collect(),
            // A refusal whose shape isn't known refuses all the same.
            refused: match x.get("refused") {
                None | Some(Value::Null) => None,
                Some(r) => Some((st(r, "code").unwrap_or_else(|| "unknown".into()), st(r, "text").unwrap_or_default())),
            },
            sizes: x.arr_at(&["sizes"]).iter().map(size).collect(),
        })
    };
    let mut disks = Vec::new();
    for p in v.arr_at(&["disks"]) {
        let s = p.get("source").ok_or("a disk with no source")?;
        let largest = s.get("largest").filter(|l| !matches!(l, Value::Null)).map(|l| Largest {
            partition: st(l, "partition"),
            size_bytes: l.get_u64("size_bytes"),
            mount: st(l, "mount"),
            used_bytes: l.get_u64("used_bytes"),
        });
        let source = Source {
            name: st(s, "name").filter(|n| !n.is_empty()).ok_or("an old disk with no name")?,
            size_bytes: s.get_u64("size_bytes").unwrap_or(0),
            model: st(s, "model"),
            serial: st(s, "serial").filter(|x| !x.trim().is_empty()),
            mounts: strs(s, "mounts"),
            largest,
        };
        let target = match p.get("target") {
            None | Some(Value::Null) => None,
            Some(t) => Some(Target {
                node: st(t, "node").ok_or("a target with no node")?,
                matched_by: st(t, "matched_by").unwrap_or_default(),
                same_identity: t.get_bool("same_identity") == Some(true),
                // Missing is written: the serials are asked for it.
                will_erase: t.get_bool("will_erase") != Some(false),
                writes: strs(t, "writes"),
            }),
        };
        disks.push(Pair { source, target });
    }
    let candidates = v.arr_at(&["candidates"]).iter().map(disk).collect::<Result<Vec<_>, _>>()?;
    let keys = v
        .arr_at(&["keys"])
        .iter()
        .map(|k| Key {
            group: st(k, "group").unwrap_or_default(),
            kind: st(k, "kind").unwrap_or_default(),
            names: strs(k, "names"),
            holds: strs(k, "holds"),
            unlock: st(k, "unlock").unwrap_or_default(),
            new_ok: k.get_bool("new_ok") == Some(true),
            min_bytes: k.get_u64("min_bytes").unwrap_or(1),
            max_bytes: k.get_u64("max_bytes").unwrap_or(512),
        })
        .collect();
    let reformat = v
        .arr_at(&["reformat"])
        .iter()
        .map(|r| Reformat {
            mount: st(r, "mount").unwrap_or_default(),
            kind: st(r, "type").unwrap_or_default(),
            to: match r.get("to") {
                Some(Value::Obj(kv)) => kv
                    .iter()
                    .map(|(t, why)| match why {
                        Value::Null => (t.clone(), None),
                        w => (t.clone(), Some(st(w, "text").unwrap_or_else(|| "bko-holt refuses it".into()))),
                    })
                    .collect(),
                _ => Vec::new(),
            },
        })
        .collect();
    let fit = v
        .arr_at(&["fit"])
        .iter()
        .map(|f| {
            let part = f.get("partition").filter(|p| !matches!(p, Value::Null));
            Fitted {
                disk: st(f, "disk").unwrap_or_default(),
                target: st(f, "target").unwrap_or_default(),
                sector: f.get_u64("sector").unwrap_or(512),
                old_sectors: part.and_then(|p| p.get_u64("old_sectors")),
                new_sectors: part.and_then(|p| p.get_u64("new_sectors")),
            }
        })
        .collect();
    let coded = |k: &str| -> Vec<Problem> {
        v.arr_at(&[k]).iter().map(|p| Problem { code: st(p, "code").unwrap_or_else(|| "other".into()), text: st(p, "text").unwrap_or_default(), disk: st(p, "disk") }).collect()
    };
    let source = v.get("source");
    Ok(Plan {
        ok: v.get_bool("ok") == Some(true),
        plan_sha256: st(v, "plan_sha256").filter(|h| h.len() == 64 && h.bytes().all(|b| b.is_ascii_hexdigit())).ok_or("no plan_sha256")?,
        hostname: source.and_then(|s| st(s, "hostname")),
        os_name: source.and_then(|s| s.at(&["os", "name"]).and_then(Value::as_str).map(str::to_string)),
        family: source.and_then(|s| st(s, "family")),
        reformat_possible: source.and_then(|s| s.get_bool("reformat_possible")) == Some(true),
        same_machine: v.at(&["here", "same_machine"]).and_then(Value::as_bool) == Some(true),
        disks,
        candidates,
        keys,
        tokens: v.arr_at(&["tokens"]).len(),
        reformat,
        fit,
        problems: coded("problems"),
        notes: coded("notes"),
        text: st(v, "text").unwrap_or_default(),
    })
}

/// What a candidate is for one old disk, by the screens' rule.
#[derive(Clone, Debug, PartialEq)]
pub enum Fits {
    /// As it is: the same size.
    Same,
    /// Up to OFFER_PCT smaller (or only free space lost), the data
    /// fitting: the offer, with `--fit-largest` PCT.
    LittleSmaller(u64),
    /// More than OFFER_PCT smaller, the data fitting: the user decides.
    MuchSmaller(u64),
    /// The data fits, but bko-holt can't shrink this far yet.
    BeyondLimit,
    /// Up to OFFER_PCT bigger: the grow offer, with PCT.
    LittleBigger(u64),
    /// More bigger, or no percentage fills it: keep the old sizes (grow
    /// to fill waits for RT10n).
    MuchBigger,
    /// Can't be taken, and why: the figures, or bko-holt's reason.
    Refused,
}

pub fn fits(s: Option<&Size>) -> Fits {
    let Some(s) = s else { return Fits::Same };
    let pct = s.change_pct.unwrap_or(f64::MAX);
    match s.verdict.as_str() {
        "same" => Fits::Same,
        "smaller_slack" => s.fit_pct.map_or(Fits::Refused, Fits::LittleSmaller),
        "smaller" if s.data_fits == Some(true) => match s.fit_pct {
            Some(p) if pct <= OFFER_PCT => Fits::LittleSmaller(p),
            Some(p) => Fits::MuchSmaller(p),
            None => Fits::Refused,
        },
        "beyond_limit" if s.data_fits == Some(true) => Fits::BeyondLimit,
        "bigger" => match s.fit_pct {
            Some(p) if pct <= OFFER_PCT => Fits::LittleBigger(p),
            _ => Fits::MuchBigger,
        },
        // data_wont_fit, cant_resize, and any verdict this doesn't know.
        _ => Fits::Refused,
    }
}

#[derive(Clone, Debug, Default)]
pub struct StageRow {
    pub name: String,
    pub state: String,
    pub why: String,
}

#[derive(Clone, Debug, Default)]
pub struct Status {
    pub stages: Vec<StageRow>,
    pub next: Option<String>,
    pub all_done: bool,
    pub partitioned: Vec<String>,
    pub restore_started: bool,
    pub recorded: Vec<String>,
    pub go_on: bool,
    pub why_not: Vec<String>,
    pub problems: Vec<Problem>,
}

impl Status {
    /// bko-holt's work is on these disks (rescue-screens.md, "7. Resume
    /// offer"): what this session recorded.  The source's own disk is
    /// "ours" too, so `ours` alone is no evidence (J19).
    pub fn work_found(&self) -> bool {
        !self.recorded.is_empty() || !self.partitioned.is_empty() || self.restore_started
    }

    /// A stage is done only when status says `done` (any other word,
    /// known or not, isn't).
    pub fn done(&self, stage: &str) -> bool {
        self.stages.iter().any(|r| r.name == stage && r.state == "done")
    }
}

pub fn read_status(v: &Value) -> Result<Status, String> {
    if v.get_u64("schema") != Some(STATUS_SCHEMA) {
        return Err(format!("a status of schema {:?}", v.get("schema").map(Value::compact)));
    }
    Ok(Status {
        stages: v.arr_at(&["stages"]).iter().map(|r| StageRow { name: st(r, "name").unwrap_or_default(), state: st(r, "state").unwrap_or_default(), why: st(r, "why").unwrap_or_default() }).collect(),
        next: st(v, "next"),
        all_done: v.get_bool("all_done") == Some(true),
        partitioned: v.at(&["marks", "partitioned"]).and_then(Value::as_array).map(|a| a.iter().filter_map(Value::as_str).map(str::to_string).collect()).unwrap_or_default(),
        restore_started: v.at(&["marks", "restore_started"]).and_then(Value::as_bool) == Some(true),
        recorded: v.arr_at(&["targets"]).iter().filter(|t| t.get_bool("recorded") == Some(true)).filter_map(|t| st(t, "node")).collect(),
        // Fail closed: Go on only when bko-holt says it is safe.
        go_on: v.at(&["resume", "go_on"]).and_then(Value::as_bool) == Some(true),
        why_not: v.at(&["resume", "why"]).and_then(Value::as_array).map(|a| a.iter().filter_map(Value::as_str).map(str::to_string).collect()).unwrap_or_default(),
        problems: v.arr_at(&["problems"]).iter().map(|p| Problem { code: st(p, "code").unwrap_or_else(|| "other".into()), text: st(p, "text").unwrap_or_default(), disk: st(p, "disk") }).collect(),
    })
}

/// Bytes as disks are sold: decimal GB or TB, three significant figures
/// (rescue-screens.md, "Conventions").
pub fn gb(n: u64) -> String {
    if n < 1_000_000_000 {
        let mb = n as f64 / 1e6;
        return if mb >= 100.0 { format!("{mb:.0} MB") } else if mb >= 10.0 { format!("{mb:.1} MB") } else { format!("{mb:.2} MB") };
    }
    crate::demo::size(n)
}

/// A percentage to one decimal, as bko-holt gives it: `4.3`.
pub fn pct(p: f64) -> String {
    format!("{p:.1}")
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::json;

    fn size(verdict: &str, fit: Option<u64>, change: Option<f64>, data: Option<bool>) -> Size {
        Size { verdict: verdict.into(), fit_pct: fit, change_pct: change, data_fits: data, ..Size::default() }
    }

    #[test]
    fn the_size_rule_is_the_screens() {
        assert_eq!(fits(None), Fits::Same);
        assert_eq!(fits(Some(&size("smaller", Some(5), Some(4.3), Some(true)))), Fits::LittleSmaller(5));
        assert_eq!(fits(Some(&size("smaller", Some(10), Some(10.0), Some(true)))), Fits::LittleSmaller(10), "10 % is still the offer");
        assert_eq!(fits(Some(&size("smaller", Some(39), Some(38.7), Some(true)))), Fits::MuchSmaller(39));
        assert_eq!(fits(Some(&size("smaller", Some(39), Some(38.7), None))), Fits::Refused, "no word on the data: not taken");
        assert_eq!(fits(Some(&size("smaller_slack", Some(1), Some(0.0), Some(true)))), Fits::LittleSmaller(1));
        assert_eq!(fits(Some(&size("beyond_limit", None, Some(60.2), Some(true)))), Fits::BeyondLimit);
        assert_eq!(fits(Some(&size("data_wont_fit", None, None, Some(false)))), Fits::Refused);
        assert_eq!(fits(Some(&size("cant_resize", None, None, None))), Fits::Refused);
        assert_eq!(fits(Some(&size("bigger", Some(5), Some(4.9), Some(true)))), Fits::LittleBigger(5));
        assert_eq!(fits(Some(&size("bigger", None, None, Some(true)))), Fits::MuchBigger);
        assert_eq!(fits(Some(&size("bigger", Some(50), Some(50.0), Some(true)))), Fits::MuchBigger);
        assert_eq!(fits(Some(&size("smaller_by_magic", Some(1), Some(0.1), Some(true)))), Fits::Refused, "a verdict this doesn't know");
    }

    #[test]
    fn sizes_as_disks_are_sold() {
        assert_eq!(gb(500_107_862_016), "500 GB");
        assert_eq!(gb(1_000_204_886_016), "1.00 TB");
        assert_eq!(gb(41_700_000_000), "41.7 GB");
        assert_eq!(gb(12_884_901_888), "12.9 GB");
        assert_eq!(gb(691_236_864), "691 MB");
    }

    #[test]
    fn a_recorded_plan_reads() {
        let p = concat!(env!("CARGO_MANIFEST_DIR"), "/../testvm/rescue/scenarios/rec-debian13-stack/04-plan/stdout");
        let v = json::parse(&std::fs::read_to_string(p).unwrap()).unwrap();
        let plan = read_plan(&v).unwrap();
        assert_eq!(plan.disks.len(), 2);
        assert_eq!(plan.candidates.len(), 4);
        assert_eq!(plan.candidates[0].refused.as_ref().map(|r| r.0.as_str()), Some("running_root"));
        assert_eq!(plan.keys[0].unlock, "passphrase");
        assert!(plan.reformat_possible && plan.ok);
        assert!(plan.text.starts_with("# bko-holt load plan"));
        let s = concat!(env!("CARGO_MANIFEST_DIR"), "/../testvm/rescue/scenarios/rec-debian13-stack/08-status-after-partition/stdout");
        let st = read_status(&json::parse(&std::fs::read_to_string(s).unwrap()).unwrap()).unwrap();
        assert!(st.done("partition") && !st.done("assemble"));
        assert!(st.work_found());
    }
}
