//! H1's golden plans: `bko-holt load`'s plan of every dump in the corpus
//! (holt/tests/corpus/GUEST/src.json, its README), against the plan kept
//! beside it (GUEST/plan.txt; GUEST/plan+bigger.txt for the variants in
//! corpus/variants.txt), and five invariants each plan must keep.  They
//! match operations, not words: a command is a plan line, or one command of
//! its `sh -c` script, with its redirections left out (commands()).
//!
//!   / mounted              the mount stage mounts the target's root: a mount
//!                          (mount, mount_*, or redoxfs) whose last operand is
//!                          /mnt/bko-target, or `zfs mount` of a dataset whose
//!                          mountpoint is / in a pool made with
//!                          `-R /mnt/bko-target`
//!   every fstab mount      each fstab entry on a dumped device or filesystem
//!     planned              (UUID=, LABEL=, PARTUUID=, PARTLABEL=, NAME=,
//!                          /dev/..., a ZFS dataset, or one the dump resolved),
//!                          with an absolute mount point and no noauto, is
//!                          mounted at /mnt/bko-target plus its mount point (the
//!                          same way) or named in a Note; swap, pseudo, network
//!                          and bind entries are skipped (diff.json's
//!                          invariants_skipped)
//!   the archiver found     no Problem about the archiver, and the restore
//!                          stage runs `rustic-bko ... restore ...
//!                          /mnt/bko-target`
//!   pool disks in use      every dumped disk holding a member of a ZFS pool,
//!                          md array, LVM volume group or multi-device btrfs
//!                          maps to a target the command making it names: the
//!                          array's `mdadm --create --uuid`, the pool's `zpool
//!                          create` or `import`, the filesystem's `mkfs.btrfs
//!                          -U`, the VG's `vgcreate NAME` or the `pvcreate` of
//!                          its metadata file NAME.vg (the PV's own, by its
//!                          --uuid, where there is one)
//!   disk IDs round-tripped an MBR's label-id is the dump's disk signature
//!                          (0x form); a GPT's label-id its disk GUID, and
//!                          every partition's uuid is carried; a table load
//!                          writes itself (write-table: the BSDs, illumos,
//!                          Haiku, Redox, Sculpt) goes onto a node of the
//!                          disk's own target and is load::writetable::plan's
//!                          bytes, read back by parttable with the dump's IDs
//!
//! The corpus must hold together, in every mode: each directory holds its
//! src.json (a plan kept without its dump fails), and variants.txt lists
//! every plan+VARIANT.txt.  Where testvm/test2/classes.json is beside holt/
//! (a checkout or a private copy; T6.4's archived holt has none), the corpus
//! is held to HoltTest2's cells: a source (holt.families, holt.no_family)
//! without a dump, or a +bigger cell (holt.variants.bigger) without a golden
//! plan, is a difference, `"unplanned"` in diff.json, so that HoltTest2 runs
//! it on a plan-visible change instead of carrying it.  A plan whose raw dump
//! failed check_plan when the corpus was built (index.tsv's check_plan_raw
//! isn't `ok:...`) is `"unverified"` in diff.json's differ, even when it's
//! the same: nothing shows it is its guest's own plan.  Check mode only logs
//! it.
//!
//! BKO_HOLT_GOLDEN picks what a difference does:
//!   check (default)  any difference fails, and so does a violation
//!   report           differences are written to BKO_HOLT_PLANS_OUT as
//!                    NAME.plan, with diff.json; only violations fail
//!   bless            plan.txt (plan+VARIANT.txt) is rewritten from the plan,
//!                    except where an invariant fails (that fails the test)
//! Every entry is planned before anything is written, and each file is
//! written whole (common::write_atomic).  The plans are Linux's (load's run
//! directory is /run/bko-holt there, /var/run/bko-holt elsewhere): report
//! and bless refuse to run on another system.
//!
//! Every test here is named golden_*, and no other holt test's name holds
//! "golden_": GateTest2 runs `cargo test -- --skip golden_` and this file
//! on its own.  It uses only holt API that is as it was before 3671e96, so it
//! runs on older trees too (the replay of #16): common/'s, plus
//! load::writetable::plan, parttable::read_table and bytes::ByteSource for
//! the tables load writes itself, and testutil::TempDir.

mod common;

use bko_holt::bytes::ByteSource;
use bko_holt::json::{self, Value};
use common::{Planned, BIGGER};
use std::collections::BTreeMap;
use std::path::{Path, PathBuf};

/// Where load's mount stage builds the restored tree (load::DEFAULT_TARGET).
const TARGET: &str = "/mnt/bko-target";

#[derive(Clone, Copy, Debug, PartialEq)]
enum Mode {
    Check,
    Report,
    Bless,
}

fn mode_from_env() -> Mode {
    match std::env::var("BKO_HOLT_GOLDEN").as_deref() {
        Err(_) | Ok("") | Ok("check") => Mode::Check,
        Ok("report") => Mode::Report,
        Ok("bless") => Mode::Bless,
        Ok(other) => panic!("BKO_HOLT_GOLDEN={other:?}: check, report or bless"),
    }
}

fn corpus_dir() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR")).join("tests").join("corpus")
}

/// testvm/test2/classes.json, beside holt/.
fn classes_path() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR")).join("..").join("testvm").join("test2").join("classes.json")
}

/// What HoltTest2 holds the corpus to (classes.json's holt section): its
/// sources, and the guests whose +bigger cell has a golden plan of its own.
#[derive(Debug, Clone, Default, PartialEq)]
struct Expected {
    sources: Vec<String>,
    bigger: Vec<String>,
}

/// Expected, from the classes.json at PATH; None when there's none (holt
/// on its own).
fn expected_from(path: &Path) -> Result<Option<Expected>, String> {
    let text = match std::fs::read_to_string(path) {
        Ok(t) => t,
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => return Ok(None),
        Err(e) => return Err(format!("{}: {e}", path.display())),
    };
    let at = path.display();
    let v = json::parse(&text).map_err(|e| format!("{at}: not JSON: {}", e.message))?;
    let names = |x: &Value, what: &str| -> Result<Vec<String>, String> {
        x.as_array().ok_or_else(|| format!("{at}: {what} isn't a list"))?.iter().map(|g| g.as_str().map(str::to_string).ok_or_else(|| format!("{at}: {what} holds a non-name"))).collect()
    };
    let fams = v.at(&["holt", "families"]).and_then(Value::as_object).ok_or_else(|| format!("{at}: no holt.families"))?;
    let mut sources = Vec::new();
    for (f, gs) in fams {
        sources.extend(names(gs, &format!("holt.families.{f}"))?);
    }
    if let Some(nf) = v.at(&["holt", "no_family"]) {
        sources.extend(names(nf, "holt.no_family")?);
    }
    sources.sort();
    sources.dedup();
    let mut bigger = match v.at(&["holt", "variants", "bigger"]) {
        Some(b) => names(b, "holt.variants.bigger")?,
        None => Vec::new(),
    };
    bigger.sort();
    bigger.dedup();
    Ok(Some(Expected { sources, bigger }))
}

/// One golden plan: GUEST, or GUEST+VARIANT.
#[derive(Debug, Clone, PartialEq)]
struct Entry {
    name: String,
    dump: PathBuf,
    plan: PathBuf,
    grow: u64,
}

/// The corpus's plans: each GUEST/src.json, then variants.txt's lines.  A
/// corpus that doesn't hold together is an error: a directory without its
/// src.json, a plan file that isn't plan.txt or plan+VARIANT.txt, a
/// plan+VARIANT.txt that variants.txt doesn't list (or with no variants.txt),
/// a variants.txt that can't be read or names what the corpus can't plan.
fn entries(corpus: &Path) -> Result<Vec<Entry>, String> {
    let rd = std::fs::read_dir(corpus).map_err(|e| format!("{}: {e}", corpus.display()))?;
    let (mut guests, mut kept, mut bad) = (Vec::new(), Vec::new(), Vec::new());
    for e in rd {
        let e = e.map_err(|x| format!("{}: {x}", corpus.display()))?;
        let Some(name) = e.file_name().to_str().map(str::to_string) else {
            bad.push(format!("{:?}: not a UTF-8 name", e.file_name()));
            continue;
        };
        let d = e.path();
        if name.starts_with('.') || !d.is_dir() {
            continue;
        }
        let mut files: Vec<String> = std::fs::read_dir(&d).map_err(|x| format!("{}: {x}", d.display()))?.filter_map(|x| x.ok()?.file_name().to_str().map(str::to_string)).collect();
        files.sort();
        let plans: Vec<&String> = files.iter().filter(|f| f.starts_with("plan") && f.ends_with(".txt")).collect();
        if !d.join("src.json").is_file() {
            bad.push(if plans.is_empty() {
                format!("{name}/: no src.json")
            } else {
                format!("{name}/: no src.json beside its {} (a golden plan of a dump that isn't here)", plans.iter().map(|p| p.as_str()).collect::<Vec<_>>().join(", "))
            });
            continue;
        }
        for p in plans {
            if p == "plan.txt" {
                continue;
            }
            match p.strip_prefix("plan+").and_then(|x| x.strip_suffix(".txt")) {
                Some(v) if !v.is_empty() => kept.push(format!("{name}+{v}")),
                _ => bad.push(format!("{name}/{p}: neither plan.txt nor plan+VARIANT.txt")),
            }
        }
        guests.push(name);
    }
    guests.sort();
    let mut out: Vec<Entry> = guests.iter().map(|g| Entry { name: g.clone(), dump: corpus.join(g).join("src.json"), plan: corpus.join(g).join("plan.txt"), grow: 0 }).collect();
    let listed = match std::fs::read_to_string(corpus.join("variants.txt")) {
        Ok(text) => text.lines().map(|l| l.split('#').next().unwrap_or("").trim().to_string()).filter(|l| !l.is_empty()).collect(),
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => {
            if !kept.is_empty() {
                bad.push(format!("no variants.txt, but variant plans are kept: {}", kept.join(", ")));
            }
            Vec::new()
        }
        Err(e) => return Err(format!("{}: {e}", corpus.join("variants.txt").display())),
    };
    for l in &listed {
        let Some((g, v)) = l.split_once('+') else {
            bad.push(format!("variants.txt: {l:?} isn't GUEST+VARIANT"));
            continue;
        };
        let grow = match v {
            "bigger" => BIGGER,
            _ => {
                bad.push(format!("variants.txt: {l}: only +bigger has a golden plan (+oneshot plans as its guest does)"));
                continue;
            }
        };
        if !guests.iter().any(|x| x == g) {
            bad.push(format!("variants.txt: {l}: no {g}/src.json"));
            continue;
        }
        let d = corpus.join(g);
        out.push(Entry { name: l.clone(), dump: d.join("src.json"), plan: d.join(format!("plan+{v}.txt")), grow });
    }
    for k in kept.iter().filter(|k| !listed.contains(k)) {
        let (g, v) = k.split_once('+').unwrap_or((k, ""));
        bad.push(format!("{g}/plan+{v}.txt: variants.txt doesn't list {k}"));
    }
    if !bad.is_empty() {
        return Err(format!("the corpus {} doesn't hold together:\n  {}", corpus.display(), bad.join("\n  ")));
    }
    out.sort_by(|a, b| a.name.cmp(&b.name));
    Ok(out)
}

/// What the invariants found: violations, and what they left out on purpose.
#[derive(Debug, Default, Clone, PartialEq)]
struct Check {
    violations: Vec<String>,
    skipped: Vec<String>,
    /// What each invariant looked at, for the log.
    checked: Vec<String>,
}

// ---- reading a plan ----

/// One command of a stage, with its here-document's lines.
#[derive(Debug, Clone)]
struct Step {
    cmd: String,
    body: Vec<String>,
}

impl Step {
    fn text(&self) -> String {
        std::iter::once(self.cmd.clone()).chain(self.body.iter().cloned()).collect::<Vec<_>>().join("\n")
    }
}

/// The plan's sections by name (common::sections).
struct Sections<'a>(Vec<(String, Vec<&'a str>)>);

impl<'a> Sections<'a> {
    fn of(text: &'a str) -> Sections<'a> {
        Sections(common::sections(text))
    }

    fn lines(&self, name: &str) -> Vec<&'a str> {
        self.0.iter().filter(|(n, _)| n == name).flat_map(|(_, l)| l.iter().copied()).collect()
    }

    /// A stage's commands: its lines less the header, comments outside a
    /// here-document, and blank lines.
    fn steps(&self, stage: &str) -> Vec<Step> {
        let mut out: Vec<Step> = Vec::new();
        let mut in_doc: Option<String> = None;
        for l in self.lines(stage).into_iter().skip(1) {
            if let Some(end) = &in_doc {
                if l == end {
                    in_doc = None;
                } else if let Some(s) = out.last_mut() {
                    s.body.push(l.to_string());
                }
                continue;
            }
            if l.starts_with('#') || l.trim().is_empty() {
                continue;
            }
            if let Some(i) = l.rfind("<<") {
                let tag = l[i + 2..].trim().trim_matches(|c| c == '\'' || c == '"');
                if !tag.is_empty() && tag.bytes().all(|c| c.is_ascii_alphanumeric() || c == b'_') {
                    in_doc = Some(tag.to_string());
                }
            }
            out.push(Step { cmd: l.to_string(), body: Vec::new() });
        }
        out
    }

    /// The header's list under TITLE ("# Problems", "# Notes:"): its
    /// "#   - " lines.
    fn list(&self, title: &str) -> Vec<String> {
        let head = self.lines("header");
        let Some(i) = head.iter().position(|l| l.starts_with(title)) else { return vec![] };
        head[i + 1..].iter().take_while(|l| l.starts_with("#   - ") || l.starts_with("#     ")).filter_map(|l| l.strip_prefix("#   - ")).map(str::to_string).collect()
    }
}

/// A command line's words, quotes and separators trimmed: good enough to
/// find paths and names in it.
fn words(s: &str) -> Vec<String> {
    s.split_whitespace().map(|w| w.trim_matches(|c| c == '\'' || c == '"' || c == ';' || c == '(' || c == ')').to_string()).filter(|w| !w.is_empty()).collect()
}

/// A program's name: a word's last path part (/sbin/mount is mount).
fn prog(w: &str) -> &str {
    w.rsplit('/').next().unwrap_or(w)
}

/// Shell words that open or close a compound command, or run the command
/// after them, without being a command themselves.
const LEADERS: [&str; 18] = ["if", "then", "else", "elif", "fi", "do", "done", "while", "until", "esac", "!", "{", "}", "(", ")", "exec", "time", "command"];

/// The simple commands of a plan line, each as its words with quotes
/// trimmed and redirections (`>FILE`, `2>&1`, `> FILE`) left out: the line
/// itself, or, for `sh -c 'SCRIPT'`, each command of SCRIPT.  Commands are
/// split at ;, &&, ||, | and newlines, and lose a leading if, do, {, done and
/// the like, and VAR=value assignments.
fn commands(line: &str) -> Vec<Vec<String>> {
    let t = line.trim();
    let script = match t.strip_prefix("sh -c ") {
        Some(rest) => rest.trim().replace("'\\''", "'"),
        None => t.to_string(),
    };
    let script = script.replace("&&", ";").replace("||", ";");
    let mut out = Vec::new();
    for piece in script.split([';', '|', '\n']) {
        let mut w: Vec<String> = Vec::new();
        let mut file_next = false;
        for x in piece.split_whitespace() {
            if file_next {
                file_next = false;
                continue;
            }
            let x = x.trim_matches(|c| c == '\'' || c == '"');
            if x.is_empty() {
                continue;
            }
            if x.contains('>') || x.contains('<') {
                // A redirection; `>` (`2>`, `&>`, `>>`, `<`) on its own takes the next word.
                file_next = x.trim_start_matches(|c: char| c.is_ascii_digit() || c == '&').chars().all(|c| c == '>' || c == '<');
                continue;
            }
            w.push(x.to_string());
        }
        while w.first().is_some_and(|x| LEADERS.contains(&x.as_str()) || (x.contains('=') && !x.starts_with('-') && !x.starts_with('/'))) {
            w.remove(0);
        }
        if !w.is_empty() {
            out.push(w);
        }
    }
    out
}

/// Whether STEP mounts something onto PATH: a `mount` (or mount_*, or
/// Redox's redoxfs) whose last operand is PATH.
fn mounts_at(s: &Step, path: &str) -> bool {
    commands(&s.cmd).iter().any(|c| {
        let p = prog(&c[0]);
        (p == "mount" || p.starts_with("mount_") || p == "redoxfs") && c.len() >= 3 && c.last().is_some_and(|l| l == path)
    })
}

/// The datasets STEP mounts with `zfs mount DATASET`.
fn zfs_mounted(s: &Step) -> Vec<String> {
    commands(&s.cmd).into_iter().filter(|c| c.len() >= 3 && prog(&c[0]) == "zfs" && c[1] == "mount").filter_map(|c| c.last().cloned()).collect()
}

/// Whether STEP restores the repository into PATH: `rustic-bko ... restore
/// ... PATH`.
fn restores_into(s: &Step, path: &str) -> bool {
    commands(&s.cmd).iter().any(|c| prog(&c[0]) == "rustic-bko" && c.iter().skip(1).any(|w| w == "restore") && c.last().is_some_and(|l| l == path))
}

/// Some(rooted) when STEP is a `zpool create` or `zpool import` of POOL;
/// rooted when it roots the pool at the target (-R, altroot=).
fn zpool_makes(s: &Step, pool: &str) -> Option<bool> {
    commands(&s.cmd).into_iter().find(|c| c.len() >= 3 && prog(&c[0]) == "zpool" && (c[1] == "create" || c[1] == "import") && c.iter().skip(2).any(|w| w == pool)).map(|c| c.windows(2).any(|p| p[0] == "-R" && p[1] == TARGET) || c.iter().any(|x| *x == format!("altroot={TARGET}")))
}

fn str_at<'a>(v: &'a Value, k: &str) -> Option<&'a str> {
    v.get(k).and_then(Value::as_str)
}

fn arr<'a>(v: &'a Value, path: &[&str]) -> &'a [Value] {
    v.at(path).and_then(Value::as_array).map(Vec::as_slice).unwrap_or(&[])
}

// ---- the invariants ----

/// The ZFS datasets the dump lists: (name, mountpoint).
fn datasets(dump: &Value) -> Vec<(String, String)> {
    arr(dump, &["storage", "zfs", "tools", "datasets"])
        .iter()
        .filter_map(|d| Some((str_at(d, "name")?.to_string(), str_at(d, "mountpoint").unwrap_or("").to_string())))
        .collect()
}

/// Whether a pool-making step (zpool create or import) of POOL roots it at
/// the target.
fn pool_rooted(assemble: &[Step], pool: &str) -> bool {
    assemble.iter().any(|s| zpool_makes(s, pool) == Some(true))
}

fn root_mounted(dump: &Value, sec: &Sections) -> Option<String> {
    root_mounted_by(dump, sec).err()
}

/// How the mount stage mounts / ("mount", "zfs mount"), or the violation.
fn root_mounted_by(dump: &Value, sec: &Sections) -> Result<&'static str, String> {
    let mount = sec.steps("mount");
    // A mount, or redoxfs, onto the target itself.
    if mount.iter().any(|s| mounts_at(s, TARGET)) {
        return Ok("mount");
    }
    // zfs mount of the / dataset, in a pool under -R TARGET.
    let ds = datasets(dump);
    let assemble = sec.steps("assemble");
    for s in &mount {
        for name in zfs_mounted(s) {
            if ds.iter().any(|(n, mp)| *n == name && mp == "/") && pool_rooted(&assemble, name.split('/').next().unwrap_or("")) {
                return Ok("zfs mount");
            }
        }
    }
    Err(format!("/ mounted: the mount stage mounts nothing on {TARGET} (no mount whose last operand it is, no zfs mount of a / dataset in a pool made with -R {TARGET})"))
}

const PSEUDO: [&str; 30] = [
    "proc", "procfs", "sysfs", "tmpfs", "devtmpfs", "devpts", "devfs", "fdescfs", "linprocfs", "linsysfs", "kernfs", "ptyfs", "mfs", "ramfs", "securityfs", "debugfs",
    "efivarfs", "hugetlbfs", "mqueue", "pstore", "bpf", "tracefs", "configfs", "fusectl", "binfmt_misc", "autofs", "cgroup", "cgroup2", "overlay", "swap",
];
const NETWORK: [&str; 9] = ["nfs", "nfs4", "cifs", "smbfs", "smb3", "sshfs", "fuse.sshfs", "9p", "virtiofs"];
const BIND: [&str; 2] = ["nullfs", "lofs"];

/// fstab's octal escapes (\040 for a space).
fn unescape(s: &str) -> String {
    let b = s.as_bytes();
    let mut out = Vec::new();
    let mut i = 0;
    while i < b.len() {
        if b[i] == b'\\' && i + 4 <= b.len() && b[i + 1..i + 4].iter().all(|c| (b'0'..=b'7').contains(c)) {
            out.push((b[i + 1] - b'0') * 64 + (b[i + 2] - b'0') * 8 + (b[i + 3] - b'0'));
            i += 4;
        } else {
            out.push(b[i]);
            i += 1;
        }
    }
    String::from_utf8_lossy(&out).into_owned()
}

/// The fstab invariant: violations, skipped entries, entries checked.
fn fstab_planned_n(dump: &Value, sec: &Sections) -> (Vec<String>, Vec<String>, usize) {
    let (mut bad, mut skipped, mut n) = (Vec::new(), Vec::new(), 0);
    let mount = sec.steps("mount");
    let notes = sec.list("# Notes:");
    let ds = datasets(dump);
    for e in arr(dump, &["storage", "fstab"]) {
        let spec = str_at(e, "spec").unwrap_or("");
        let file = unescape(str_at(e, "file").unwrap_or(""));
        let ty = str_at(e, "type").unwrap_or("");
        let opts: Vec<&str> = str_at(e, "options").unwrap_or("").split(',').collect();
        let what = format!("{file} ({spec}, {ty})");
        let skip = if ty == "swap" {
            Some("swap")
        } else if !file.starts_with('/') {
            Some("no absolute mount point")
        } else if opts.contains(&"noauto") {
            Some("noauto")
        } else if ty.split(',').any(|t| PSEUDO.contains(&t)) {
            Some("a pseudo filesystem")
        } else if ty.split(',').any(|t| NETWORK.contains(&t)) || spec.starts_with("//") || (spec.contains(':') && !spec.starts_with('/') && !spec.contains('=')) {
            Some("a network filesystem")
        } else if opts.iter().any(|o| *o == "bind" || *o == "rbind") || ty.split(',').any(|t| BIND.contains(&t)) {
            Some("a bind mount")
        } else {
            let device = ["UUID=", "LABEL=", "PARTUUID=", "PARTLABEL=", "NAME=", "/dev/"].iter().any(|p| spec.starts_with(p))
                || ty == "zfs"
                || ds.iter().any(|(n, _)| n == spec)
                || e.get("resolves_to").is_some_and(|r| !r.is_null());
            (!device).then_some("not on a dumped device or filesystem")
        };
        if let Some(why) = skip {
            skipped.push(format!("fstab {what}: {why}"));
            continue;
        }
        n += 1;
        let at = if file == "/" { TARGET.to_string() } else { format!("{TARGET}{}", file.trim_end_matches('/')) };
        let mounted = mount.iter().any(|s| mounts_at(s, &at)) || mount.iter().any(|s| zfs_mounted(s).iter().any(|d| ds.iter().any(|(n, mp)| n == d && *mp == file)));
        let noted = notes.iter().any(|n| n.starts_with(&format!("fstab's {file} ")) || n.starts_with(&format!("fstab's {file}:")));
        if !mounted && !noted {
            bad.push(format!("every fstab mount planned: fstab's {what} is neither mounted at {at} by the mount stage nor named in a Note"));
        }
    }
    (bad, skipped, n)
}

fn archiver_found(sec: &Sections) -> Vec<String> {
    let mut bad = Vec::new();
    for p in sec.list("# Problems (") {
        if p.contains("rustic-bko") || p.contains("archiver") || p.contains("restore can't run") || p.contains("restore needs rustic-bko") {
            bad.push(format!("the archiver found: Problem {p:?}"));
        }
    }
    if !sec.steps("restore").iter().any(|s| restores_into(s, TARGET)) {
        bad.push(format!("the archiver found: the restore stage runs no `rustic-bko ... restore ... {TARGET}`"));
    }
    bad
}

/// The dumped disk DEV is, or is a partition (or BSD label partition, or
/// illumos slice) of.
fn disk_of(dump: &Value, dev: &str) -> Option<String> {
    let dev = dev.trim_start_matches("/dev/");
    for d in arr(dump, &["storage", "disks"]) {
        let name = str_at(d, "name")?;
        if name == dev {
            return Some(name.to_string());
        }
        for k in ["partitions", "label_partitions"] {
            for p in d.get(k).and_then(Value::as_array).into_iter().flatten() {
                if str_at(p, "name") == Some(dev) {
                    return Some(name.to_string());
                }
                for q in p.get("label_partitions").and_then(Value::as_array).into_iter().flatten() {
                    if str_at(q, "name") == Some(dev) {
                        return Some(name.to_string());
                    }
                }
            }
        }
    }
    None
}

fn target_of<'a>(p: &'a Planned, disk: &str) -> Option<&'a str> {
    p.maps.iter().find(|(s, _, _)| s == disk).map(|(_, t, _)| t.as_str())
}

/// Whether TEXT names a node of target disk TGT (Linux's /dev/sdq1, the
/// BSDs' /dev/sdqp3 and raw /dev/rsdqc, illumos's /dev/dsk/sdqs1, Haiku's
/// /dev/disk/sdq/0).
fn names_target(text: &str, tgt: &str) -> bool {
    [format!("/dev/{tgt}"), format!("/dev/r{tgt}"), format!("/dev/dsk/{tgt}"), format!("/dev/rdsk/{tgt}"), format!("/dev/disk/{tgt}/")].iter().any(|f| text.contains(f.as_str()))
}

fn pool_disks(dump: &Value, sec: &Sections, p: &Planned) -> Vec<String> {
    pool_disks_n(dump, sec, p).0
}

/// pool_disks, and how many containers it checked.
fn pool_disks_n(dump: &Value, sec: &Sections, p: &Planned) -> (Vec<String>, usize) {
    let mut bad = Vec::new();
    let mut n = 0;
    let (assemble, format) = (sec.steps("assemble"), sec.steps("format"));
    let text = |steps: &[&Step]| steps.iter().map(|s| s.text()).collect::<Vec<_>>().join("\n");
    // WHAT's members, each with the text of the commands that must name its
    // disk's target; NONE when no command makes WHAT.
    let mut check = |what: String, members: Vec<(String, String)>, none: bool| {
        n += 1;
        if none {
            bad.push(format!("pool disks in use: {what}: no command makes it"));
            return;
        }
        for (m, t) in members {
            let Some(disk) = disk_of(dump, &m) else { continue };
            match target_of(p, &disk) {
                None => bad.push(format!("pool disks in use: {what}: member {m}'s disk {disk} has no target")),
                Some(tg) if !names_target(&t, tg) => bad.push(format!("pool disks in use: {what}: member {m} is on {disk} -> /dev/{tg}, which the command making it doesn't name")),
                Some(_) => {}
            }
        }
    };
    for a in arr(dump, &["storage", "md_arrays"]) {
        let name = str_at(a, "name").unwrap_or("?");
        let members: Vec<String> = arr(a, &["md", "members"]).iter().filter_map(|m| str_at(m, "device").map(str::to_string)).collect();
        let uuid = a.at(&["md", "uuid"]).and_then(Value::as_str);
        let makers: Vec<&Step> = assemble
            .iter()
            .filter(|s| commands(&s.cmd).iter().any(|c| prog(&c[0]) == "mdadm" && c.get(1).is_some_and(|x| x == "--create") && uuid.is_none_or(|u| c.iter().any(|w| *w == format!("--uuid={u}")) || c.windows(2).any(|w| w[0] == "--uuid" && w[1] == u))))
            .collect();
        let t = text(&makers);
        check(format!("md array {name}"), members.into_iter().map(|m| (m, t.clone())).collect(), makers.is_empty());
    }
    for pool in arr(dump, &["storage", "zfs", "pools_from_labels"]) {
        let name = str_at(pool, "name").unwrap_or("?");
        let members: Vec<String> = arr(pool, &["member_devices"]).iter().filter_map(|m| m.as_str().map(str::to_string)).collect();
        let makers: Vec<&Step> = assemble.iter().filter(|s| zpool_makes(s, name).is_some()).collect();
        let t = text(&makers);
        check(format!("ZFS pool {name}"), members.into_iter().map(|m| (m, t.clone())).collect(), makers.is_empty());
    }
    for vg in arr(dump, &["storage", "lvm", "volume_groups"]) {
        let name = str_at(vg, "name").unwrap_or("?");
        let pvs: Vec<(String, Option<String>)> = arr(vg, &["physical_volumes"]).iter().filter_map(|pv| Some((str_at(pv, "device")?.to_string(), str_at(pv, "id").map(str::to_string)))).collect();
        // PVs on md arrays or LUKS are their containers' to check.
        if pvs.iter().all(|(m, _)| disk_of(dump, m).is_none()) {
            continue;
        }
        // vgcreate NAME, or a pvcreate of the VG's metadata file (load's /run/bko-holt/lvm/NAME.vg).
        let file = format!("/{name}.vg");
        let makers: Vec<&Step> = assemble
            .iter()
            .filter(|s| {
                commands(&s.cmd).iter().any(|c| match prog(&c[0]) {
                    "vgcreate" => c.iter().skip(1).any(|w| w == name),
                    "pvcreate" => c.iter().any(|w| w.ends_with(&file)),
                    _ => false,
                })
            })
            .collect();
        let members = pvs
            .iter()
            .map(|(m, id)| {
                // The PV's own pvcreate, by its --uuid, when the plan has one.
                let own: Vec<&Step> = match id {
                    Some(id) => makers.iter().copied().filter(|s| commands(&s.cmd).iter().any(|c| prog(&c[0]) == "pvcreate" && (c.windows(2).any(|w| w[0] == "--uuid" && w[1] == *id) || c.iter().any(|w| *w == format!("--uuid={id}"))))).collect(),
                    None => Vec::new(),
                };
                (m.clone(), text(if own.is_empty() { &makers } else { &own }))
            })
            .collect();
        check(format!("LVM volume group {name}"), members, makers.is_empty());
    }
    for fs in arr(dump, &["storage", "btrfs"]) {
        let devices: Vec<String> = arr(fs, &["devices"]).iter().filter_map(|d| d.as_str().map(str::to_string)).collect();
        if devices.len() < 2 {
            continue;
        }
        let uuid = str_at(fs, "uuid").unwrap_or("?");
        let makers: Vec<&Step> = format.iter().filter(|s| commands(&s.cmd).iter().any(|c| prog(&c[0]) == "mkfs.btrfs" && c.iter().any(|w| w.eq_ignore_ascii_case(uuid)))).collect();
        let t = text(&makers);
        check(format!("btrfs {uuid}"), devices.into_iter().map(|m| (m, t.clone())).collect(), makers.is_empty());
    }
    (bad, n)
}

/// Writes laid over zeros: what a device holds after load::writetable's.
struct Sparse<'a> {
    size: u64,
    writes: &'a [(u64, Vec<u8>)],
}

impl ByteSource for Sparse<'_> {
    fn size(&self) -> u64 {
        self.size
    }
    fn read_at(&self, offset: u64, len: usize) -> Vec<u8> {
        if offset >= self.size {
            return Vec::new();
        }
        let len = len.min((self.size - offset) as usize);
        let mut out = vec![0u8; len];
        let end = offset + len as u64;
        for (at, b) in self.writes {
            let (s, e) = (*at, *at + b.len() as u64);
            if e <= offset || s >= end {
                continue;
            }
            let from = s.max(offset);
            let to = e.min(end);
            out[(from - offset) as usize..(to - offset) as usize].copy_from_slice(&b[(from - s) as usize..(to - s) as usize]);
        }
        out
    }
}

fn mbr_signature(t: &Value) -> Option<u32> {
    match t.get("disk_signature")? {
        Value::Str(s) => u32::from_str_radix(s.trim_start_matches("0x"), 16).ok(),
        x => x.as_u64().map(|n| n as u32),
    }
}

/// DISK's table as WRITES put it on a device of SIZE bytes, read back: the
/// dump's IDs, or what differs.
fn table_ids(disk: &Value, writes: &[(u64, Vec<u8>)], size: u64) -> Vec<String> {
    let name = str_at(disk, "name").unwrap_or("?");
    let t = disk.get("partition_table").cloned().unwrap_or(Value::Null);
    let ss = t.get("sector_size").and_then(Value::as_u64).or_else(|| disk.get("logical_sector_size").and_then(Value::as_u64)).unwrap_or(512);
    let src = Sparse { size, writes };
    let mut bad = Vec::new();
    match (str_at(&t, "type"), bko_holt::parttable::read_table(&src, ss)) {
        (Some("gpt"), Some(bko_holt::parttable::Table::Gpt { gpt, .. })) => {
            let want = str_at(&t, "disk_guid").unwrap_or("");
            if !gpt.disk_guid.eq_ignore_ascii_case(want) {
                bad.push(format!("disk IDs round-tripped: {name}: write-table's GPT reads back with disk GUID {} for {want}", gpt.disk_guid));
            }
            for p in arr(disk, &["partitions"]) {
                let (Some(n), Some(u)) = (p.get("number").and_then(Value::as_u64), str_at(p, "uuid")) else { continue };
                match gpt.parts.iter().find(|q| u64::from(q.number) == n) {
                    Some(q) if q.uuid.eq_ignore_ascii_case(u) => {}
                    Some(q) => bad.push(format!("disk IDs round-tripped: {name}: write-table's partition {n} reads back as {} for {u}", q.uuid)),
                    None => bad.push(format!("disk IDs round-tripped: {name}: write-table's GPT has no partition {n}")),
                }
            }
        }
        (Some("dos" | "mbr"), Some(bko_holt::parttable::Table::Mbr(m))) => {
            if let Some(want) = mbr_signature(&t) {
                if m.disk_signature != want {
                    bad.push(format!("disk IDs round-tripped: {name}: write-table's MBR reads back with signature 0x{:08x} for 0x{want:08x}", m.disk_signature));
                }
            }
        }
        (ty, got) => bad.push(format!(
            "disk IDs round-tripped: {name}: write-table's {} table reads back as {}",
            ty.unwrap_or("?"),
            match got {
                Some(bko_holt::parttable::Table::Gpt { .. }) => "a GPT",
                Some(bko_holt::parttable::Table::Mbr(_)) => "an MBR",
                None => "nothing",
            }
        )),
    }
    bad
}

fn disk_ids(dump: &Value, sec: &Sections, p: &Planned) -> Vec<String> {
    disk_ids_n(dump, sec, p).0
}

/// disk_ids, and how many tables it checked: (sfdisk, write-table).
fn disk_ids_n(dump: &Value, sec: &Sections, p: &Planned) -> (Vec<String>, (usize, usize)) {
    let mut bad = Vec::new();
    let (mut nsf, mut nwt) = (0, 0);
    let part = sec.steps("partition");
    for d in arr(dump, &["storage", "disks"]) {
        let Some(name) = str_at(d, "name") else { continue };
        let t = match d.get("partition_table") {
            Some(t) if !t.is_null() => t,
            _ => continue,
        };
        let Some((_, tgt, bytes)) = p.maps.iter().find(|(s, _, _)| s == name) else { continue };
        let kind = str_at(t, "type").unwrap_or("?");
        // A table load writes itself (other/'s platforms):
        // `bko-holt write-table layout.json NAME DEVICE ...`.
        let devices: Vec<String> = part
            .iter()
            .flat_map(|s| commands(&s.cmd))
            .filter_map(|c| {
                let i = c.iter().position(|w| w == "write-table")?;
                (c.get(i + 1).map(String::as_str) == Some(common::LAYOUT) && c.get(i + 2).map(String::as_str) == Some(name)).then(|| c.get(i + 3).cloned().unwrap_or_default())
            })
            .collect();
        if !devices.is_empty() {
            nwt += 1;
            if let Some(dev) = devices.iter().find(|dev| !names_target(dev, tgt)) {
                bad.push(format!("disk IDs round-tripped: {name}: write-table writes its table onto {dev:?}, not a node of its target /dev/{tgt}"));
                continue;
            }
            match bko_holt::load::writetable::plan(d, *bytes) {
                Ok(w) => bad.extend(table_ids(d, &w, *bytes)),
                Err(e) => bad.push(format!("disk IDs round-tripped: {name}: write-table's plan fails: {e}")),
            }
            continue;
        }
        let Some(sf) = part.iter().find(|s| s.cmd.starts_with("sfdisk") && words(&s.cmd).iter().any(|w| *w == format!("/dev/{tgt}"))) else {
            bad.push(format!("disk IDs round-tripped: {name} ({kind}) -> /dev/{tgt}: the partition stage writes no table on it"));
            continue;
        };
        nsf += 1;
        let label_id = sf.body.iter().find_map(|l| l.strip_prefix("label-id: ")).unwrap_or("");
        match kind {
            "dos" | "mbr" => {
                if let Some(sig) = mbr_signature(t) {
                    let want = format!("0x{sig:08x}");
                    if !label_id.eq_ignore_ascii_case(&want) {
                        bad.push(format!("disk IDs round-tripped: {name}: label-id {label_id:?} for the disk signature {want}"));
                    }
                }
            }
            "gpt" => {
                let want = str_at(t, "disk_guid").unwrap_or("");
                if !label_id.eq_ignore_ascii_case(want) {
                    bad.push(format!("disk IDs round-tripped: {name}: label-id {label_id:?} for the disk GUID {want}"));
                }
                let body = sf.body.join("\n").to_ascii_lowercase();
                for q in arr(d, &["partitions"]) {
                    if let Some(u) = str_at(q, "uuid") {
                        if !body.contains(&format!("uuid={}", u.to_ascii_lowercase())) {
                            bad.push(format!("disk IDs round-tripped: {name}: partition {} ({u}) isn't given its uuid", q.get("number").and_then(Value::as_u64).unwrap_or(0)));
                        }
                    }
                }
            }
            other => bad.push(format!("disk IDs round-tripped: {name}: a {other} table")),
        }
    }
    (bad, (nsf, nwt))
}

/// The five invariants of one plan of DUMP.
fn invariants(dump: &Value, p: &Planned) -> Check {
    let mut c = Check::default();
    if p.out.is_empty() {
        c.violations.push(format!("load made no plan (exit {}): {}", p.code, p.err.lines().next().unwrap_or("")));
        return c;
    }
    let sec = Sections::of(&p.out);
    match root_mounted_by(dump, &sec) {
        Ok(how) => c.checked.push(format!("/ by {how}")),
        Err(e) => c.violations.push(e),
    }
    let (bad, skipped, n) = fstab_planned_n(dump, &sec);
    c.violations.extend(bad);
    c.skipped.extend(skipped);
    c.checked.push(format!("{n} fstab"));
    c.violations.extend(archiver_found(&sec));
    let (bad, n) = pool_disks_n(dump, &sec, p);
    c.violations.extend(bad);
    c.checked.push(format!("{n} pools"));
    let (bad, (nsf, nwt)) = disk_ids_n(dump, &sec, p);
    c.violations.extend(bad);
    c.checked.push(format!("{} tables ({nsf} sfdisk, {nwt} write-table)", nsf + nwt));
    c
}

// ---- the run ----

/// The plan of one entry, as plan.txt holds it, and its invariants.
fn plan_entry(e: &Entry) -> Result<(String, Check), String> {
    let text = std::fs::read_to_string(&e.dump).map_err(|x| format!("{}: {x}", e.dump.display()))?;
    let dump = json::parse(&text).map_err(|x| format!("{}: not JSON: {}", e.dump.display(), x.message))?;
    let p = common::plan(&text, e.grow)?;
    Ok((common::golden_text(&p), invariants(&dump, &p)))
}

/// How a plan differs from the one kept.
#[derive(Debug, Default, Clone, PartialEq)]
struct Differ {
    stages: Vec<String>,
    lines: usize,
    /// No plan kept yet.
    new: bool,
    /// HoltTest2 has the cell and the corpus has no golden plan of it: why.
    unplanned: Option<String>,
}

#[derive(Debug, Default)]
struct Summary {
    /// Dumps (GUEST/src.json) and plans (variants too).
    dumps: usize,
    plans: usize,
    /// The names planned, in order.
    planned: Vec<String>,
    /// How many HoltTest2 sources the corpus was held to (None: classes.json
    /// isn't beside holt/).
    sources: Option<usize>,
    differ: BTreeMap<String, Differ>,
    /// The plans whose raw dump failed check_plan when the corpus was built
    /// (index.tsv's check_plan_raw): name -> that value.
    unverified: BTreeMap<String, String>,
    invariants: BTreeMap<String, Vec<String>>,
    skipped: BTreeMap<String, Vec<String>>,
    written: Vec<String>,
    log: Vec<String>,
}

fn json_list(v: &[String]) -> Value {
    Value::Arr(v.iter().map(|s| Value::Str(s.clone())).collect())
}

impl Summary {
    /// diff.json: {"v":1, "corpus":N, "plans":M, "planned":[NAME...],
    /// "sources":S or null, "differ":{NAME:{"stages":[...], "lines_changed":n
    /// [, "new":true][, "unplanned":WHY][, "unverified":CHECK_PLAN_RAW]}},
    /// "invariants":{NAME:[msg...]}, "invariants_skipped":{NAME:[msg...]}}.
    /// An unverified plan is listed in differ even when it's the same (stages
    /// [], 0 lines): nothing shows it is its guest's own plan, so HoltTest2
    /// runs it on any plan-visible change instead of carrying it.
    fn diff_json(&self) -> String {
        let mut names: Vec<&String> = self.differ.keys().chain(self.unverified.keys()).collect();
        names.sort();
        names.dedup();
        let differ = Value::Obj(
            names
                .into_iter()
                .map(|n| {
                    let d = self.differ.get(n).cloned().unwrap_or_default();
                    let mut kv = vec![("stages".to_string(), json_list(&d.stages)), ("lines_changed".to_string(), Value::UInt(d.lines as u64))];
                    if d.new {
                        kv.push(("new".to_string(), Value::Bool(true)));
                    }
                    if let Some(why) = &d.unplanned {
                        kv.push(("unplanned".to_string(), Value::Str(why.clone())));
                    }
                    if let Some(v) = self.unverified.get(n) {
                        kv.push(("unverified".to_string(), Value::Str(v.clone())));
                    }
                    (n.clone(), Value::Obj(kv))
                })
                .collect(),
        );
        let map = |m: &BTreeMap<String, Vec<String>>| Value::Obj(m.iter().map(|(n, l)| (n.clone(), json_list(l))).collect());
        Value::Obj(vec![
            ("v".into(), Value::UInt(1)),
            ("corpus".into(), Value::UInt(self.dumps as u64)),
            ("plans".into(), Value::UInt(self.plans as u64)),
            ("planned".into(), json_list(&self.planned)),
            ("sources".into(), self.sources.map_or(Value::Null, |n| Value::UInt(n as u64))),
            ("differ".into(), differ),
            ("invariants".into(), map(&self.invariants)),
            ("invariants_skipped".into(), map(&self.skipped)),
        ])
        .pretty()
    }
}

/// index.tsv's guests whose raw dump failed check_plan (check_plan_raw
/// isn't `ok:...`), with that value; none without an index.tsv.
fn unverified_in(corpus: &Path) -> Result<BTreeMap<String, String>, String> {
    let p = corpus.join("index.tsv");
    let text = match std::fs::read_to_string(&p) {
        Ok(t) => t,
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => return Ok(BTreeMap::new()),
        Err(e) => return Err(format!("{}: {e}", p.display())),
    };
    let mut lines = text.lines();
    let cols: Vec<&str> = lines.next().unwrap_or("").split('\t').collect();
    let (Some(gi), Some(ci)) = (cols.iter().position(|c| *c == "guest"), cols.iter().position(|c| *c == "check_plan_raw")) else {
        return Err(format!("{}: no guest or check_plan_raw column", p.display()));
    };
    let mut out = BTreeMap::new();
    for l in lines.filter(|l| !l.is_empty()) {
        let row: Vec<&str> = l.split('\t').collect();
        let (g, c) = (row.get(gi).copied().unwrap_or(""), row.get(ci).copied().unwrap_or(""));
        if !g.is_empty() && !c.starts_with("ok:") {
            out.insert(g.to_string(), if c.is_empty() { "-".to_string() } else { c.to_string() });
        }
    }
    Ok(out)
}

/// What EXPECT has and the corpus doesn't plan, as differences: HoltTest2's
/// sources without a dump, its +bigger cells without a golden plan.  The
/// corpus's own extras are only logged.
fn unplanned(s: &mut Summary, list: &[Entry], x: &Expected) {
    let have = |n: &str| list.iter().any(|e| e.name == n);
    for g in &x.sources {
        if !have(g) {
            s.differ.insert(g.clone(), Differ { new: true, unplanned: Some("no corpus dump".into()), ..Differ::default() });
            s.log.push(format!("NO DUMP  {g}: HoltTest2's source, with no dump in the corpus (its README: Rebuilding it)"));
        }
    }
    for g in &x.bigger {
        let n = format!("{g}+bigger");
        if !have(&n) {
            let why = if have(g) { "not in variants.txt" } else { "no corpus dump" };
            s.differ.insert(n.clone(), Differ { new: true, unplanned: Some(why.into()), ..Differ::default() });
            s.log.push(format!("NO PLAN  {n}: HoltTest2's +bigger cell, with no golden plan ({why})"));
        }
    }
    for e in list {
        if e.grow == 0 && !x.sources.contains(&e.name) {
            s.log.push(format!("note  {}: in the corpus, but not one of HoltTest2's sources in classes.json", e.name));
        }
        if let Some(g) = e.name.strip_suffix("+bigger") {
            if !x.bigger.iter().any(|b| b == g) {
                s.log.push(format!("note  {}: a golden plan, but classes.json has no such cell", e.name));
            }
        }
    }
    s.sources = Some(x.sources.len());
}

fn run_golden(corpus: &Path, mode: Mode, out: Option<&Path>, expect: Option<&Expected>) -> Result<Summary, String> {
    let list = entries(corpus)?;
    if list.is_empty() {
        return Err(format!("no GUEST/src.json in {}", corpus.display()));
    }
    let o = match mode {
        Mode::Report => Some(out.ok_or("BKO_HOLT_GOLDEN=report needs BKO_HOLT_PLANS_OUT, the directory for the plans that differ and diff.json")?),
        _ => None,
    };
    let mut s = Summary { dumps: list.iter().filter(|e| e.grow == 0).count(), plans: list.len(), ..Summary::default() };
    // Everything is planned before anything is written: an entry that can't
    // be planned stops the run with the files as they were.
    let mut todo: Vec<(&Entry, String)> = Vec::new();
    for e in &list {
        let (text, check) = plan_entry(e).map_err(|x| format!("{}: {x}", e.name))?;
        let old = match std::fs::read_to_string(&e.plan) {
            Ok(t) => Some(t),
            Err(x) if x.kind() == std::io::ErrorKind::NotFound => None,
            Err(x) => return Err(format!("{}: {x}", e.plan.display())),
        };
        s.planned.push(e.name.clone());
        if !check.violations.is_empty() {
            s.invariants.insert(e.name.clone(), check.violations.clone());
        }
        if !check.skipped.is_empty() {
            s.skipped.insert(e.name.clone(), check.skipped.clone());
        }
        let same = old.as_deref() == Some(text.as_str());
        if !same {
            let o = old.clone().unwrap_or_default();
            let stages = if old.is_some() { common::stages_changed(&o, &text) } else { common::sections(&text).into_iter().map(|(n, _)| n).collect() };
            s.differ.insert(e.name.clone(), Differ { stages, lines: common::lines_changed(&o, &text), new: old.is_none(), unplanned: None });
        }
        let state = match (&old, same) {
            (None, _) => "NEW ",
            (_, true) => "ok  ",
            (_, false) => "DIFF",
        };
        let mut line = format!("{state}  {}: {} lines; checked {}", e.name, text.lines().count(), check.checked.join(", "));
        if let Some(d) = s.differ.get(&e.name) {
            line += &format!(", {} changed in {}", d.lines, d.stages.join(", "));
        }
        if !check.violations.is_empty() {
            line += &format!("; INVARIANTS: {}", check.violations.join("; "));
        }
        s.log.push(line);
        let write = match mode {
            Mode::Bless => check.violations.is_empty() && !same,
            Mode::Report => !same,
            Mode::Check => false,
        };
        if write {
            todo.push((e, text));
        }
    }
    if let Some(x) = expect {
        unplanned(&mut s, &list, x);
    }
    let raw_failed = unverified_in(corpus)?;
    for e in &list {
        let g = e.name.split('+').next().unwrap_or(&e.name);
        if let Some(v) = raw_failed.get(g) {
            s.unverified.insert(e.name.clone(), v.clone());
            s.log.push(format!("UNVERIFIED  {}: check_plan on {g}'s raw dump gave {v} (index.tsv), so nothing shows this is its guest's own plan; diff.json lists it in differ", e.name));
        }
    }
    match (mode, o) {
        (Mode::Bless, _) => {
            for (e, text) in &todo {
                common::write_atomic(&e.plan, text.as_bytes())?;
                s.written.push(e.name.clone());
            }
        }
        (Mode::Report, Some(o)) => {
            std::fs::create_dir_all(o).map_err(|e| format!("{}: {e}", o.display()))?;
            for (e, text) in &todo {
                common::write_atomic(&o.join(format!("{}.plan", e.name)), text.as_bytes())?;
                s.written.push(e.name.clone());
            }
            common::write_atomic(&o.join("diff.json"), s.diff_json().as_bytes())?;
        }
        _ => {}
    }
    Ok(s)
}

/// A difference, as check mode names it.
fn differ_line(n: &str, d: &Differ) -> String {
    match (&d.unplanned, d.new) {
        (Some(why), _) => format!("{n}: no golden plan ({why}), but HoltTest2 has the cell"),
        (None, true) => format!("{n}: no plan kept yet"),
        (None, false) => format!("{n}: {} lines in {}", d.lines, d.stages.join(", ")),
    }
}

#[test]
fn golden_plans() {
    let mode = mode_from_env();
    if mode != Mode::Check && !cfg!(target_os = "linux") {
        panic!("BKO_HOLT_GOLDEN={mode:?}: the golden plans are Linux's (load's run directory is /run/bko-holt there and /var/run/bko-holt here): report and bless them on a Linux host");
    }
    let corpus = corpus_dir();
    let out = std::env::var_os("BKO_HOLT_PLANS_OUT").map(PathBuf::from);
    let expect = expected_from(&classes_path()).unwrap_or_else(|e| panic!("{e}"));
    let s = run_golden(&corpus, mode, out.as_deref(), expect.as_ref()).unwrap_or_else(|e| panic!("{e}"));
    for l in &s.log {
        println!("{l}");
    }
    let inv: Vec<String> = s.invariants.iter().map(|(n, v)| format!("{n}: {}", v.join("; "))).collect();
    println!(
        "{} plans of {} dumps: {} differ, {} break an invariant, {} unverified{}; {}",
        s.plans,
        s.dumps,
        s.differ.len(),
        s.invariants.len(),
        s.unverified.len(),
        match mode {
            Mode::Bless => format!("; blessed {}", s.written.len()),
            Mode::Report => format!("; {} written to {}", s.written.len() + 1, out.as_ref().unwrap().display()),
            Mode::Check => String::new(),
        },
        match (&expect, s.differ.values().filter(|d| d.unplanned.is_some()).count()) {
            (None, _) => "no testvm/test2/classes.json beside holt/: HoltTest2's cells aren't checked".to_string(),
            (Some(x), 0) => format!("HoltTest2's {} sources and {} +bigger cells all have golden plans", x.sources.len(), x.bigger.len()),
            (Some(x), k) => format!("{k} of HoltTest2's {} sources and {} +bigger cells have no golden plan", x.sources.len(), x.bigger.len()),
        }
    );
    assert!(inv.is_empty(), "invariants broken (nothing of theirs blessed):\n{}", inv.join("\n"));
    if mode == Mode::Check {
        let d: Vec<String> = s.differ.iter().map(|(n, d)| differ_line(n, d)).collect();
        assert!(d.is_empty(), "plans differ from the corpus's (BKO_HOLT_GOLDEN=report shows them, =bless takes them):\n{}", d.join("\n"));
    }
}

// ---- the invariants' own tests: synthetic layouts and plans ----

/// A Debian 13 layout: one 16 GiB BIOS disk, MBR: /boot, swap, /.
const SYNTH: &str = r#"{
  "format": "bko-holt", "schema": 1, "privileged": true, "generated": "2026-11-08T00:00:00Z",
  "host": {"hostname": "synth"},
  "os": {"id": "debian", "version_id": "13", "pretty_name": "Debian GNU/Linux 13 (trixie)"},
  "firmware": {"mode": "bios"},
  "storage": {
    "disks": [{
      "name": "vda", "kind": "disk", "size_bytes": 17179869184, "logical_sector_size": 512, "model": null, "serial": null, "wwid": null,
      "partition_table": {"type": "dos", "sector_size": 512, "disk_signature": "bc000001"},
      "partitions": [
        {"name": "vda1", "number": 1, "start_lba": 2048, "sectors": 1953792, "type": "0x83", "bootable": true,
         "content": {"type": "ext4", "usage": "filesystem", "uuid": "bc000000-0000-4000-8000-000000000001", "label": null, "block_size": 4096, "inode_size": 256,
                     "inodes": 61056, "blocks_per_group": 32768, "features": ["has_journal", "ext_attr", "dir_index", "filetype", "extent", "64bit", "flex_bg", "sparse_super", "large_file", "huge_file", "dir_nlink", "extra_isize", "metadata_csum"],
                     "hash_seed": "bc000000-0000-4000-8000-000000000002", "flex_bg_size": 16, "reserved_blocks": 12211, "blocks": 244224}},
        {"name": "vda2", "number": 2, "start_lba": 1955840, "sectors": 3905536, "type": "0x82", "bootable": false,
         "content": {"type": "swap", "usage": "other", "uuid": "bc000000-0000-4000-8000-000000000003", "label": null, "page_size": 4096}},
        {"name": "vda3", "number": 3, "start_lba": 5861376, "sectors": 27693056, "type": "0x83", "bootable": false,
         "content": {"type": "ext4", "usage": "filesystem", "uuid": "bc000000-0000-4000-8000-000000000004", "label": null, "block_size": 4096, "inode_size": 256,
                     "inodes": 865280, "blocks_per_group": 32768, "features": ["has_journal", "ext_attr", "dir_index", "filetype", "extent", "64bit", "flex_bg", "sparse_super", "large_file", "huge_file", "dir_nlink", "extra_isize", "metadata_csum"],
                     "hash_seed": "bc000000-0000-4000-8000-000000000005", "flex_bg_size": 16, "reserved_blocks": 173081, "blocks": 3461632}}
      ],
      "content": null, "boot_code": {"kind": "grub2"}
    }],
    "md_arrays": [], "dm_devices": [], "lvm": {"volume_groups": []}, "zfs": {"pools_from_labels": [], "tools": null}, "btrfs": [], "xfs_external_logs": [],
    "fstab": [
      {"spec": "UUID=bc000000-0000-4000-8000-000000000004", "file": "/", "type": "ext4", "options": "errors=remount-ro", "freq": 0, "passno": 1, "resolves_to": "vda3"},
      {"spec": "UUID=bc000000-0000-4000-8000-000000000001", "file": "/boot", "type": "ext4", "options": "defaults", "freq": 0, "passno": 2, "resolves_to": "vda1"},
      {"spec": "UUID=bc000000-0000-4000-8000-000000000003", "file": "none", "type": "swap", "options": "sw", "freq": 0, "passno": 0, "resolves_to": "vda2"},
      {"spec": "tmpfs", "file": "/tmp", "type": "tmpfs", "options": "defaults", "freq": 0, "passno": 0, "resolves_to": null},
      {"spec": "/dev/sr0", "file": "/media/cdrom0", "type": "udf,iso9660", "options": "user,noauto", "freq": 0, "passno": 0, "resolves_to": null}
    ],
    "crypttab": null, "mounts": [], "swaps": []
  },
  "boot": {"loader": {"name": "grub", "firmware": "bios", "via_shim": false}, "efi": null, "grub": {"debconf": {}}, "kernels": {"installed": [{"version": "6.12.48+deb13-amd64", "running": true}]}}
}"#;

fn synth() -> (Value, Planned) {
    let p = common::plan(SYNTH, 0).unwrap();
    (json::parse(SYNTH).unwrap(), p)
}

/// P with its standard output's line FROM replaced by TO (FROM must be there).
fn edit(p: &Planned, from: &str, to: &str) -> Planned {
    assert!(p.out.contains(from), "{from:?} isn't in the plan:\n{}", p.out);
    Planned { out: p.out.replacen(from, to, 1), ..p.clone() }
}

fn has(c: &Check, what: &str) -> bool {
    c.violations.iter().any(|v| v.starts_with(what))
}

#[test]
fn golden_text_holds_what_status_looks_for() {
    // What load builds and never prints (a change to it moves no command).
    let (_, p) = synth();
    let text = common::golden_text(&p);
    for what in ["status filesystem ", "status mount /"] {
        assert!(text.contains(&format!("{} {what}", common::GOLDEN_MARK)), "no {what:?} in\n{text}");
    }
    let moved = text.replacen(" status mount /", " status mount /x", 1);
    assert_eq!(common::stages_changed(&text, &moved), ["trailer"]);
}

#[test]
fn golden_invariants_hold_on_a_synthetic_plan() {
    let (v, p) = synth();
    assert_eq!(p.code, 0, "{}{}", p.out, p.err);
    let c = invariants(&v, &p);
    assert!(c.violations.is_empty(), "{:?}\n{}", c.violations, p.out);
    // tmpfs, the noauto CD and swap are skipped, with their reasons.
    assert_eq!(c.skipped.len(), 3, "{:?}", c.skipped);
    assert!(c.skipped.iter().any(|s| s.contains("/tmp") && s.contains("pseudo")), "{:?}", c.skipped);
    assert!(c.skipped.iter().any(|s| s.contains("/media/cdrom0") && s.contains("noauto")), "{:?}", c.skipped);
    assert!(c.skipped.iter().any(|s| s.starts_with("fstab none ") && s.ends_with(": swap")), "{:?}", c.skipped);
    // The +bigger plan keeps them too, on targets 1 GiB bigger.
    let b = common::plan(SYNTH, BIGGER).unwrap();
    assert_eq!(b.maps[0].2, 17179869184 + BIGGER);
    assert!(invariants(&v, &b).violations.is_empty());
}

#[test]
fn golden_commands_of_a_line() {
    assert_eq!(commands("mount -t ext4 -o defaults /dev/sdq3 /mnt/bko-target"), [["mount", "-t", "ext4", "-o", "defaults", "/dev/sdq3", "/mnt/bko-target"]]);
    let redox = "sh -c 'redoxfs /dev/sdq3 /mnt/bko-target >/run/bko-holt/redoxfs.log 2>&1 </dev/null; i=0; until mountpoint -q /mnt/bko-target; do i=$((i + 1)); [ $i -lt 60 ] || { cat /run/bko-holt/redoxfs.log; exit 1; }; sleep 1; done'";
    let c = commands(redox);
    assert_eq!(c[0], ["redoxfs", "/dev/sdq3", "/mnt/bko-target"]);
    assert!(c.iter().any(|w| w == &["mountpoint", "-q", "/mnt/bko-target"]), "{c:?}");
    assert_eq!(commands("sh -c 'x > /run/f; LC_ALL=C sort -z && if mount a b; then :; fi'"), vec![vec!["x"], vec!["sort", "-z"], vec!["mount", "a", "b"], vec![":"]]);
    let wt = "sh -c 'bko-holt write-table layout.json ld0 /dev/rsdqd $(disklabel sdq | awk '\\''/^bytes.sector:/ { b = $2 } END { print b }'\\'')'";
    assert_eq!(commands(wt)[0][..5], ["bko-holt", "write-table", "layout.json", "ld0", "/dev/rsdqd"]);
}

#[test]
fn golden_invariant_root_mounted() {
    let (v, p) = synth();
    let line = p.out.lines().find(|l| l.starts_with("mount ") && l.ends_with(&format!(" {TARGET}"))).unwrap().to_string();
    let bad = edit(&p, &format!("{line}\n"), "");
    assert!(has(&invariants(&v, &bad), "/ mounted"), "{:?}", invariants(&v, &bad));
    // Commands that name the target without mounting it, and a mount elsewhere.
    for near in [format!("chmod 0755 {TARGET}"), format!("umount {TARGET}"), format!("mountpoint -q {TARGET}"), format!("{line}/srv"), format!("mount --make-private {TARGET} /srv")] {
        let bad = edit(&p, &format!("{line}\n"), &format!("{near}\n"));
        assert!(has(&invariants(&v, &bad), "/ mounted"), "{near}: {:?}", invariants(&v, &bad));
    }
    // ZFS: a / dataset mounted in a pool made under -R TARGET, and not without it.
    let zfs = |rooted: bool| {
        let out = format!(
            "# bko-holt load plan for layout.json\n\n# ---- assemble ----\nzpool create -f -d {}rpool /dev/sdq3\n\n# ---- mount ----\nzfs mount rpool/ROOT/debian\n\n# ---- restore ----\nrustic-bko --repository /srv/bko-repo restore latest {TARGET}\n\n# Nothing was written.\n",
            if rooted { format!("-R {TARGET} ") } else { String::new() }
        );
        Planned { code: 0, out, err: String::new(), maps: vec![] }
    };
    let dump = json::parse(r#"{"storage": {"zfs": {"tools": {"datasets": [{"name": "rpool", "mountpoint": "/"}, {"name": "rpool/ROOT/debian", "mountpoint": "/"}]}}}}"#).unwrap();
    let sec = |p: &Planned| root_mounted(&dump, &Sections::of(&p.out));
    assert_eq!(sec(&zfs(true)), None);
    assert!(sec(&zfs(false)).is_some());
    // An echo of the pool's command makes no pool.
    let echo = Planned { out: zfs(true).out.replace("zpool create -f", "echo zpool create -f"), ..zfs(true) };
    assert!(sec(&echo).is_some());
    // illumos's legacy boot dataset, mounted with the system's mount.
    let legacy = Planned { out: format!("# ---- mount ----\nmount -F zfs rpool/ROOT/omnios-r151058 {TARGET}\n"), ..zfs(false) };
    assert_eq!(sec(&legacy), None);
    // Redox's redoxfs, in its sh -c; the wait for it alone isn't a mount.
    let redox = Planned { out: format!("# ---- mount ----\nsh -c 'redoxfs /dev/sdq3 {TARGET} >/run/bko-holt/redoxfs.log 2>&1 </dev/null; until mountpoint -q {TARGET}; do sleep 1; done'\n"), ..zfs(false) };
    assert_eq!(sec(&redox), None);
    let wait = Planned { out: format!("# ---- mount ----\nsh -c 'until mountpoint -q {TARGET}; do sleep 1; done'\n"), ..zfs(false) };
    assert!(sec(&wait).is_some());
    // A mkdir of the target isn't a mount.
    let mk = Planned { out: format!("# ---- mount ----\nmkdir -p {TARGET}\n"), ..zfs(false) };
    assert!(sec(&mk).is_some());
}

/// P with NOTE added to its header's Notes.
fn add_note(p: &Planned, note: &str) -> Planned {
    let out = if p.out.contains("# Notes:\n") {
        p.out.replacen("# Notes:\n", &format!("# Notes:\n#   - {note}\n"), 1)
    } else {
        p.out.replacen("\n# ---- ", &format!("#\n# Notes:\n#   - {note}\n\n# ---- "), 1)
    };
    Planned { out, ..p.clone() }
}

/// DUMP with ENTRIES (JSON objects) added to its fstab.
fn with_fstab(dump: &str, entries: &[&str]) -> Value {
    let mut d = json::parse(dump).unwrap();
    let Value::Obj(kv) = &mut d else { unreachable!() };
    let Some((_, Value::Obj(st))) = kv.iter_mut().find(|(k, _)| k == "storage") else { unreachable!() };
    let Some((_, Value::Arr(f))) = st.iter_mut().find(|(k, _)| k == "fstab") else { unreachable!() };
    f.extend(entries.iter().map(|e| json::parse(e).unwrap()));
    d
}

#[test]
fn golden_invariant_every_fstab_mount_planned() {
    let (v, p) = synth();
    let line = p.out.lines().find(|l| l.starts_with("mount ") && l.ends_with(&format!(" {TARGET}/boot"))).unwrap().to_string();
    let bad = edit(&p, &format!("{line}\n"), "");
    let c = invariants(&v, &bad);
    assert!(has(&c, "every fstab mount planned: fstab's /boot"), "{:?}", c);
    // A command that names the mount point without mounting it there.
    for near in [format!("chmod 0700 {TARGET}/boot"), format!("umount {TARGET}/boot"), format!("mount -t ext4 /dev/sdq1 {TARGET}/boot2")] {
        let c = invariants(&v, &edit(&p, &format!("{line}\n"), &format!("{near}\n")));
        assert!(has(&c, "every fstab mount planned: fstab's /boot"), "{near}: {:?}", c);
    }
    // Named in a Note instead: planned.
    let noted = add_note(&bad, "fstab's /boot (ext4) isn't on the layout's disks; not mounted");
    assert!(!has(&invariants(&v, &noted), "every fstab mount planned"), "{:?}\n{}", invariants(&v, &noted), noted.out);
    // A noauto entry, a network one and a bind mount are skipped; an entry
    // on a dumped device that nothing mounts isn't.
    let d = with_fstab(
        SYNTH,
        &[
            r#"{"spec": "UUID=bc000000-0000-4000-8000-000000000009", "file": "/srv", "type": "ext4", "options": "noauto", "resolves_to": "vda3"}"#,
            r#"{"spec": "nas:/export", "file": "/nas", "type": "nfs", "options": "defaults", "resolves_to": null}"#,
            r#"{"spec": "/srv/x", "file": "/x", "type": "none", "options": "bind", "resolves_to": null}"#,
            r#"{"spec": "UUID=bc000000-0000-4000-8000-00000000000a", "file": "/data", "type": "xfs", "options": "defaults", "resolves_to": "vdb1"}"#,
        ],
    );
    let c = invariants(&d, &p);
    assert!(c.skipped.iter().any(|s| s.contains("/srv") && s.contains("noauto")), "{:?}", c.skipped);
    assert!(c.skipped.iter().any(|s| s.contains("/nas") && s.contains("network")), "{:?}", c.skipped);
    assert!(c.skipped.iter().any(|s| s.contains("/x ") && s.contains("bind")), "{:?}", c.skipped);
    assert!(has(&c, "every fstab mount planned: fstab's /data"), "{:?}", c);
    assert_eq!(unescape("/srv/my\\040disk"), "/srv/my disk");
}

#[test]
fn golden_invariant_archiver_found() {
    let (v, p) = synth();
    let restore = p.out.lines().find(|l| l.contains("rustic-bko --repository")).unwrap().to_string();
    let bad = edit(&p, &format!("{restore}\n"), "");
    assert!(has(&invariants(&v, &bad), "the archiver found: the restore stage runs no"));
    // rustic-bko run, but no restore into the target.
    for near in ["rustic-bko --version".to_string(), "rustic-bko --repository /srv/bko-repo snapshots".to_string(), restore.replace(TARGET, "/tmp/elsewhere"), format!("echo {restore}")] {
        let bad = edit(&p, &format!("{restore}\n"), &format!("{near}\n"));
        assert!(has(&invariants(&v, &bad), "the archiver found: the restore stage runs no"), "{near}: {:?}", invariants(&v, &bad));
    }
    let problem = Planned {
        out: p.out.replacen("#\n# Disks:", "#\n# Problems (1): nothing can be written until they're fixed\n#   - rustic-bko isn't BackOtter's rustic (rustic 0.11.4); restore needs rustic-bko\n#\n# Disks:", 1),
        code: 4,
        ..p.clone()
    };
    assert!(problem.out.contains("# Problems (1)"), "{}", problem.out);
    assert!(has(&invariants(&v, &problem), "the archiver found: Problem"));
}

#[test]
fn golden_invariant_pool_disks_in_use() {
    let dump = json::parse(
        r#"{"storage": {"disks": [{"name": "vda", "partitions": [{"name": "vda1"}, {"name": "vda2"}]}, {"name": "vdb", "partitions": [{"name": "vdb1"}, {"name": "vdb2"}]}],
            "md_arrays": [{"name": "md0", "md": {"uuid": "bc000000:00004000:80000000:00000001", "members": [{"device": "vda1"}, {"device": "vdb1"}]}}],
            "zfs": {"pools_from_labels": [{"name": "rpool", "member_devices": ["vda2", "vdb2"]}]},
            "btrfs": [{"uuid": "bc000000-0000-4000-8000-000000000007", "devices": ["vda2", "vdb2"]}]}}"#,
    )
    .unwrap();
    let maps = vec![("vda".to_string(), "sdq".to_string(), 1u64 << 34), ("vdb".to_string(), "sdr".to_string(), 1u64 << 34)];
    let plan = |md: &str, zp: &str, bt: &str| Planned {
        code: 0,
        out: format!("# ---- assemble ----\nmdadm --create /dev/md/0 --uuid=bc000000:00004000:80000000:00000001 {md}\nzpool create -f -R {TARGET} rpool mirror {zp}\n\n# ---- format ----\nmkfs.btrfs -f -U bc000000-0000-4000-8000-000000000007 -d raid1 {bt}\n"),
        err: String::new(),
        maps: maps.clone(),
    };
    let both = "/dev/sdq1 /dev/sdr1";
    let ok = plan(both, "/dev/sdq2 /dev/sdr2", "/dev/sdq2 /dev/sdr2");
    assert!(pool_disks(&dump, &Sections::of(&ok.out), &ok).is_empty(), "{:?}", pool_disks(&dump, &Sections::of(&ok.out), &ok));
    for (bad, what) in [(plan("/dev/sdq1", "/dev/sdq2 /dev/sdr2", "/dev/sdq2 /dev/sdr2"), "md array md0: member vdb1"), (plan(both, "/dev/sdq2", "/dev/sdq2 /dev/sdr2"), "ZFS pool rpool: member vdb2"), (plan(both, "/dev/sdq2 /dev/sdr2", "/dev/sdr2"), "btrfs bc000000-0000-4000-8000-000000000007: member vda2")] {
        let v = pool_disks(&dump, &Sections::of(&bad.out), &bad);
        assert!(v.iter().any(|x| x.contains(what)), "{what}: {v:?}");
    }
    // An array nothing makes, and a pool whose only "maker" names it without making it.
    let none = Planned { out: "# ---- assemble ----\n# (nothing to do)\n".into(), ..ok.clone() };
    assert!(pool_disks(&dump, &Sections::of(&none.out), &none).iter().any(|x| x.contains("md array md0: no command makes it")));
    let echo = Planned { out: ok.out.replace("zpool create", "echo zpool create"), ..ok.clone() };
    assert!(pool_disks(&dump, &Sections::of(&echo.out), &echo).iter().any(|x| x.contains("ZFS pool rpool: no command makes it")));
    // LVM: two volume groups on the disks.  vg0's PV on vdb is its own
    // pvcreate's to make; vg1's on vdb2 doesn't stand in for it.
    let lvm = json::parse(
        r#"{"storage": {"disks": [{"name": "vda", "partitions": [{"name": "vda2"}]}, {"name": "vdb", "partitions": [{"name": "vdb2"}, {"name": "vdb3"}]}],
            "lvm": {"volume_groups": [
              {"name": "vg0", "physical_volumes": [{"name": "pv0", "id": "bkoLVM-0000-0000-0000-0000-0000-000001", "device": "vda2"}, {"name": "pv1", "id": "bkoLVM-0000-0000-0000-0000-0000-000002", "device": "vdb3"}]},
              {"name": "vg1", "physical_volumes": [{"name": "pv0", "id": "bkoLVM-0000-0000-0000-0000-0000-000003", "device": "vdb2"}]}]}}}"#,
    )
    .unwrap();
    let pv = |id: u8, vg: &str, dev: &str| format!("pvcreate --uuid bkoLVM-0000-0000-0000-0000-0000-00000{id} --restorefile /run/bko-holt/lvm/{vg}.vg {dev}\n");
    let lplan = |pvs: &str| Planned { out: format!("# ---- assemble ----\nmkdir -p /run/bko-holt/lvm\n{pvs}vgcfgrestore --file /run/bko-holt/lvm/vg0.vg vg0\nvgcfgrestore --file /run/bko-holt/lvm/vg1.vg vg1\n"), ..ok.clone() };
    let good = lplan(&(pv(1, "vg0", "/dev/sdq2") + &pv(2, "vg0", "/dev/sdr3") + &pv(3, "vg1", "/dev/sdr2")));
    assert!(pool_disks(&lvm, &Sections::of(&good.out), &good).is_empty(), "{:?}", pool_disks(&lvm, &Sections::of(&good.out), &good));
    let dropped = lplan(&(pv(1, "vg0", "/dev/sdq2") + &pv(3, "vg1", "/dev/sdr2")));
    let v = pool_disks(&lvm, &Sections::of(&dropped.out), &dropped);
    assert!(v.iter().any(|x| x.contains("LVM volume group vg0: member vdb3")), "{v:?}");
    assert!(!v.iter().any(|x| x.contains("vg1")), "{v:?}");
    let swapped = lplan(&(pv(1, "vg0", "/dev/sdq2") + &pv(2, "vg0", "/dev/sdq3") + &pv(3, "vg1", "/dev/sdr2")));
    assert!(pool_disks(&lvm, &Sections::of(&swapped.out), &swapped).iter().any(|x| x.contains("vg0: member vdb3")));
    let nothing = lplan(&pv(3, "vg1", "/dev/sdr2"));
    assert!(pool_disks(&lvm, &Sections::of(&nothing.out), &nothing).iter().any(|x| x.contains("LVM volume group vg0: no command makes it")));
}

#[test]
fn golden_invariant_disk_ids_round_tripped() {
    let (v, p) = synth();
    // MBR: the label-id is the disk signature.
    let bad = edit(&p, "label-id: 0xbc000001", "label-id: 0xbc000002");
    assert!(has(&invariants(&v, &bad), "disk IDs round-tripped: vda: label-id"), "{:?}", invariants(&v, &bad));
    // GPT: the disk GUID and each partition's uuid.
    let gdump = json::parse(
        r#"{"storage": {"disks": [{"name": "vda", "partition_table": {"type": "gpt", "disk_guid": "BC000000-0000-4000-8000-000000000001"},
            "partitions": [{"number": 1, "uuid": "BC000000-0000-4000-8000-000000000002"}, {"number": 2, "uuid": "BC000000-0000-4000-8000-000000000003"}]}]}}"#,
    )
    .unwrap();
    let gplan = |ids: &str, u2: &str| Planned {
        code: 0,
        out: format!("# ---- partition ----\nsfdisk --wipe always /dev/sdq <<'EOF'\nlabel: gpt\nlabel-id: {ids}\n\n/dev/sdq1 : start=2048, size=2048, type=C12A7328-F81F-11D2-BA4B-00A0C93EC93B, uuid=BC000000-0000-4000-8000-000000000002\n/dev/sdq2 : start=4096, size=2048, type=0FC63DAF-8483-4772-8E79-3D69D8477DE4, uuid={u2}\nEOF\n"),
        err: String::new(),
        maps: vec![("vda".to_string(), "sdq".to_string(), 1 << 30)],
    };
    let ok = gplan("bc000000-0000-4000-8000-000000000001", "BC000000-0000-4000-8000-000000000003");
    assert!(disk_ids(&gdump, &Sections::of(&ok.out), &ok).is_empty(), "{:?}", disk_ids(&gdump, &Sections::of(&ok.out), &ok));
    let b1 = gplan("bc000000-0000-4000-8000-000000000009", "BC000000-0000-4000-8000-000000000003");
    assert!(disk_ids(&gdump, &Sections::of(&b1.out), &b1).iter().any(|x| x.contains("label-id")));
    let b2 = gplan("bc000000-0000-4000-8000-000000000001", "BC000000-0000-4000-8000-000000000008");
    assert!(disk_ids(&gdump, &Sections::of(&b2.out), &b2).iter().any(|x| x.contains("partition 2")));
    // No table written at all.
    let none = Planned { out: "# ---- partition ----\n# (nothing to do)\n".into(), ..ok.clone() };
    assert!(disk_ids(&gdump, &Sections::of(&none.out), &none).iter().any(|x| x.contains("writes no table")));
}

/// A FreeBSD-style GPT disk VTBD0 of 1 GiB, with disk GUID and partition uuid.
fn bsd_disk(guid: &str, part: &str) -> Value {
    json::parse(&format!(
        r#"{{"name": "vtbd0", "size_bytes": 1073741824, "logical_sector_size": 512,
            "partition_table": {{"type": "gpt", "sector_size": 512, "disk_guid": "{guid}", "first_usable_lba": 40, "last_usable_lba": 2097118, "entry_count": 128, "entry_size": 128}},
            "partitions": [{{"number": 1, "start_lba": 40, "end_lba": 1063, "sectors": 1024, "size_bytes": 524288, "type": "83bd6b9d-7f41-11dc-be0b-001560b84f0f", "uuid": "{part}", "partlabel": "gptboot0", "attributes": "0x0000000000000000"}}]}}"#
    ))
    .unwrap()
}

#[test]
fn golden_invariant_write_table_ids() {
    // The disk written by load::writetable and read back.
    let good = bsd_disk("bc000000-0000-4000-8000-000000000001", "bc000000-0000-4000-8000-000000000002");
    let w = bko_holt::load::writetable::plan(&good, 1 << 30).unwrap();
    assert!(table_ids(&good, &w, 1 << 30).is_empty(), "{:?}", table_ids(&good, &w, 1 << 30));
    // Bytes that carry other IDs than the dump's.
    let other = bsd_disk("bc000000-0000-4000-8000-000000000003", "bc000000-0000-4000-8000-000000000004");
    let w2 = bko_holt::load::writetable::plan(&other, 1 << 30).unwrap();
    let v = table_ids(&good, &w2, 1 << 30);
    assert!(v.iter().any(|x| x.contains("disk GUID")) && v.iter().any(|x| x.contains("partition 1")), "{v:?}");
    // Nothing written: nothing reads back.
    assert!(table_ids(&good, &[], 1 << 30).iter().any(|x| x.contains("reads back as nothing")));
    // An MBR's signature.
    let mbr = json::parse(r#"{"name": "wd0", "size_bytes": 1073741824, "partition_table": {"type": "dos", "sector_size": 512, "disk_signature": "bc000001"},
        "partitions": [{"number": 1, "start_lba": 2048, "sectors": 4096, "type": "0xa9", "bootable": true}]}"#).unwrap();
    let w3 = bko_holt::load::writetable::plan(&mbr, 1 << 30).unwrap();
    assert!(table_ids(&mbr, &w3, 1 << 30).is_empty(), "{:?}", table_ids(&mbr, &w3, 1 << 30));
    let mbr2 = json::parse(&mbr.compact().replace("bc000001", "bc000002"))
        .unwrap();
    assert!(table_ids(&mbr2, &w3, 1 << 30).iter().any(|x| x.contains("signature")));
    // In a plan: write-table must write the disk's table onto its own target.
    let dump = Value::Obj(vec![("storage".into(), Value::Obj(vec![("disks".into(), Value::Arr(vec![good.clone()]))]))]);
    let wplan = |dev: &str| Planned {
        code: 0,
        out: format!("# ---- partition ----\nsh -c 'bko-holt write-table layout.json vtbd0 {dev} $(diskinfo {dev} | awk '\\''{{ print $3 }}'\\'')'\n"),
        err: String::new(),
        maps: vec![("vtbd0".to_string(), "sdq".to_string(), 1 << 30), ("vtbd1".to_string(), "sdr".to_string(), 1 << 30)],
    };
    let ok = wplan("/dev/sdq");
    assert_eq!(disk_ids_n(&dump, &Sections::of(&ok.out), &ok), (vec![], (0, 1)));
    let wrong = wplan("/dev/sdr");
    assert!(disk_ids(&dump, &Sections::of(&wrong.out), &wrong).iter().any(|x| x.contains("write-table writes its table onto \"/dev/sdr\"")), "{:?}", disk_ids(&dump, &Sections::of(&wrong.out), &wrong));
    // Another disk's write-table doesn't count for this one.
    let others = Planned { out: ok.out.replace("vtbd0", "vtbd1"), ..ok.clone() };
    assert!(disk_ids(&dump, &Sections::of(&others.out), &others).iter().any(|x| x.contains("writes no table")));
}

#[test]
fn golden_sections_and_stages() {
    let old = "# bko-holt load plan for layout.json\n# Source: x\n\n# ---- partition ----\nwipefs --all /dev/sdq\n\n# ---- format ----\nmkfs.ext4 /dev/sdq1\n\n# ---- close ----\numount /mnt/bko-target\n\n# Nothing was written.  To run it: ...\n# golden_plans.rs: exit 0, nothing on stderr\n";
    let names: Vec<String> = common::sections(old).into_iter().map(|(n, _)| n).collect();
    assert_eq!(names, ["header", "partition", "format", "close", "trailer"]);
    assert_eq!(common::stages_changed(old, old), Vec::<String>::new());
    assert_eq!(common::stages_changed(old, &old.replace("mkfs.ext4", "mkfs.xfs")), ["format"]);
    assert_eq!(common::stages_changed(old, &old.replace("exit 0", "exit 4")), ["trailer"]);
    assert_eq!(common::stages_changed(old, &old.replace("# Source: x", "# Source: y")), ["header"]);
    let added = old.replace("\n\n# ---- format", "\n\n# ---- assemble ----\nmdadm --create /dev/md/0\n\n# ---- format");
    assert_eq!(common::stages_changed(old, &added), ["assemble"]);
    assert_eq!(common::lines_changed(old, &old.replace("mkfs.ext4", "mkfs.xfs")), 2);
    assert_eq!(common::lines_changed(old, &added), 3);
    assert_eq!(common::lines_changed("", "a\nb\n"), 2);
    // No stages at all (load refused the layout): the golden lines are the trailer.
    let refused = "# golden_plans.rs: exit 3; stderr:\n#   bko-holt load: layout.json: not a bko-holt dump\n";
    assert_eq!(common::sections(refused).into_iter().map(|(n, _)| n).collect::<Vec<_>>(), ["header", "trailer"]);
    assert!(common::stage_header("# ---- mount ----") == Some("mount") && common::stage_header("# ---- a b ----").is_none());
}

#[test]
fn golden_modes() {
    let dir = bko_holt::testutil::TempDir::new("golden-modes");
    let corpus = dir.path().join("corpus");
    let out = dir.path().join("out");
    std::fs::create_dir_all(corpus.join("synth")).unwrap();
    std::fs::write(corpus.join("synth").join("src.json"), SYNTH).unwrap();
    std::fs::write(corpus.join("variants.txt"), "# a comment\nsynth+bigger\n").unwrap();
    // check: no plan kept yet fails as a difference.
    let s = run_golden(&corpus, Mode::Check, None, None).unwrap();
    assert_eq!((s.dumps, s.plans), (1, 2));
    assert!(s.differ.get("synth").is_some_and(|d| d.new) && s.differ.get("synth+bigger").is_some_and(|d| d.new), "{:?}", s.differ);
    // bless writes both, then check passes.
    let s = run_golden(&corpus, Mode::Bless, None, None).unwrap();
    assert_eq!(s.written, ["synth", "synth+bigger"]);
    let plan = std::fs::read_to_string(corpus.join("synth").join("plan.txt")).unwrap();
    let bigger = std::fs::read_to_string(corpus.join("synth").join("plan+bigger.txt")).unwrap();
    assert!(plan.contains("#   vda (16 GiB) -> /dev/sdq") && bigger.contains("#   vda (16 GiB) -> /dev/sdq") && plan.ends_with("# golden_plans.rs: exit 0, nothing on stderr\n"), "{plan}");
    assert!(!corpus.join("synth").join(".plan.txt.part").exists());
    let s = run_golden(&corpus, Mode::Check, None, None).unwrap();
    assert!(s.differ.is_empty() && s.invariants.is_empty(), "{:?} {:?}", s.differ, s.invariants);
    // A kept plan that differs: check names it, report writes it and diff.json.
    let changed = plan.replacen("mount -t ext4", "mount -t ext3", 1);
    std::fs::write(corpus.join("synth").join("plan.txt"), &changed).unwrap();
    let s = run_golden(&corpus, Mode::Check, None, None).unwrap();
    assert_eq!(s.differ.get("synth").map(|d| d.stages.clone()), Some(vec!["mount".to_string()]));
    assert!(run_golden(&corpus, Mode::Report, None, None).is_err(), "report without BKO_HOLT_PLANS_OUT");
    let s = run_golden(&corpus, Mode::Report, Some(&out), None).unwrap();
    assert_eq!(s.written, ["synth"]);
    assert_eq!(std::fs::read_to_string(out.join("synth.plan")).unwrap(), plan);
    let d = json::parse(&std::fs::read_to_string(out.join("diff.json")).unwrap()).unwrap();
    assert_eq!(d.get("v").and_then(Value::as_u64), Some(1));
    assert_eq!(d.get("corpus").and_then(Value::as_u64), Some(1));
    assert_eq!(d.at(&["differ", "synth", "stages", "0"]).and_then(Value::as_str), Some("mount"));
    assert_eq!(d.at(&["differ", "synth", "lines_changed"]).and_then(Value::as_u64), Some(2));
    assert!(d.at(&["differ", "synth+bigger"]).is_none());
    assert!(d.at(&["invariants_skipped", "synth", "0"]).is_some());
    assert_eq!(d.get("planned").map(|p| p.compact()), Some(r#"["synth","synth+bigger"]"#.to_string()));
    assert!(d.get("sources").is_some_and(Value::is_null));
    // bless takes it back; a plan that breaks an invariant isn't blessed.
    run_golden(&corpus, Mode::Bless, None, None).unwrap();
    assert_eq!(std::fs::read_to_string(corpus.join("synth").join("plan.txt")).unwrap(), plan);
    let no_root = SYNTH.replace(r#""file": "/", "type": "ext4""#, r#""file": "/srv", "type": "ext4""#);
    std::fs::write(corpus.join("synth").join("src.json"), &no_root).unwrap();
    let s = run_golden(&corpus, Mode::Bless, None, None).unwrap();
    assert!(s.invariants.get("synth").is_some_and(|v| v.iter().any(|x| x.starts_with("/ mounted"))), "{:?}", s.invariants);
    assert!(s.written.is_empty(), "{:?}", s.written);
    assert_eq!(std::fs::read_to_string(corpus.join("synth").join("plan.txt")).unwrap(), plan, "not blessed");
    std::fs::write(corpus.join("synth").join("src.json"), SYNTH).unwrap();
    // A variant of a guest the corpus doesn't have, or one with no golden plan.
    std::fs::write(corpus.join("variants.txt"), "synth+bigger\nnosuch+bigger\n").unwrap();
    assert!(run_golden(&corpus, Mode::Check, None, None).unwrap_err().contains("no nosuch/src.json"));
    std::fs::write(corpus.join("variants.txt"), "synth+bigger\nsynth+oneshot\n").unwrap();
    assert!(run_golden(&corpus, Mode::Check, None, None).unwrap_err().contains("only +bigger"));
    std::fs::write(corpus.join("variants.txt"), "synth+bigger\n").unwrap();
}

#[test]
fn golden_corpus_holds_together() {
    let dir = bko_holt::testutil::TempDir::new("golden-corpus");
    let corpus = dir.path().join("corpus");
    let out = dir.path().join("out");
    let g = corpus.join("synth");
    std::fs::create_dir_all(&g).unwrap();
    std::fs::write(g.join("src.json"), SYNTH).unwrap();
    std::fs::write(corpus.join("variants.txt"), "synth+bigger\n").unwrap();
    run_golden(&corpus, Mode::Bless, None, None).unwrap();
    assert!(run_golden(&corpus, Mode::Check, None, None).unwrap().differ.is_empty());
    let err = |c: &Path| run_golden(c, Mode::Report, Some(&out), None).unwrap_err();
    // A plan kept without its dump fails, in report mode too.
    std::fs::create_dir_all(corpus.join("gone")).unwrap();
    std::fs::write(corpus.join("gone").join("plan.txt"), "x\n").unwrap();
    assert!(err(&corpus).contains("gone/: no src.json beside its plan.txt"), "{}", err(&corpus));
    assert!(!out.join("diff.json").exists(), "no diff.json for a corpus that doesn't hold together");
    std::fs::remove_dir_all(corpus.join("gone")).unwrap();
    // A variant plan that variants.txt doesn't list, or with no variants.txt.
    std::fs::write(g.join("plan+huge.txt"), "x\n").unwrap();
    assert!(err(&corpus).contains("synth/plan+huge.txt: variants.txt doesn't list synth+huge"), "{}", err(&corpus));
    std::fs::remove_file(g.join("plan+huge.txt")).unwrap();
    std::fs::remove_file(corpus.join("variants.txt")).unwrap();
    assert!(err(&corpus).contains("no variants.txt, but variant plans are kept: synth+bigger"), "{}", err(&corpus));
    std::fs::write(g.join("plan-old.txt"), "x\n").unwrap();
    assert!(err(&corpus).contains("synth/plan-old.txt: neither plan.txt nor plan+VARIANT.txt"), "{}", err(&corpus));
    std::fs::remove_file(g.join("plan-old.txt")).unwrap();
    std::fs::write(corpus.join("variants.txt"), "synth+bigger\n").unwrap();
    // HoltTest2's cells: a source without a dump and a +bigger cell without
    // a golden plan are differences, unplanned, in diff.json too.
    let x = Expected { sources: vec!["other".into(), "synth".into()], bigger: vec!["other".into(), "synth".into()] };
    let s = run_golden(&corpus, Mode::Check, None, Some(&x)).unwrap();
    assert_eq!(s.differ.keys().cloned().collect::<Vec<_>>(), ["other", "other+bigger"]);
    assert_eq!(s.differ["other"].unplanned.as_deref(), Some("no corpus dump"));
    assert_eq!(differ_line("other", &s.differ["other"]), "other: no golden plan (no corpus dump), but HoltTest2 has the cell");
    let s = run_golden(&corpus, Mode::Report, Some(&out), Some(&x)).unwrap();
    assert!(s.written.is_empty());
    let d = json::parse(&std::fs::read_to_string(out.join("diff.json")).unwrap()).unwrap();
    assert_eq!(d.at(&["differ", "other", "unplanned"]).and_then(Value::as_str), Some("no corpus dump"));
    assert_eq!(d.at(&["differ", "other", "stages"]).and_then(Value::as_array).map(Vec::len), Some(0));
    assert_eq!(d.at(&["differ", "other+bigger", "unplanned"]).and_then(Value::as_str), Some("no corpus dump"));
    assert_eq!(d.get("sources").and_then(Value::as_u64), Some(2));
    assert_eq!(d.get("planned").map(|p| p.compact()), Some(r#"["synth","synth+bigger"]"#.to_string()));
    // A guest's dump without its +bigger plan listed: "not in variants.txt".
    std::fs::write(corpus.join("variants.txt"), "").unwrap();
    std::fs::remove_file(g.join("plan+bigger.txt")).unwrap();
    let s = run_golden(&corpus, Mode::Check, None, Some(&Expected { sources: vec!["synth".into()], bigger: vec!["synth".into()] })).unwrap();
    assert_eq!(s.differ.get("synth+bigger").and_then(|d| d.unplanned.clone()).as_deref(), Some("not in variants.txt"));
    // A guest whose raw dump failed check_plan (index.tsv): listed in
    // diff.json's differ as unverified, plan unchanged; check mode passes.
    std::fs::write(corpus.join("index.tsv"), "guest\thost\tcheck_plan_raw\nsynth\tchopin\tFAIL:the-plan-of-the-redacted-dump-differs\nother\tchopin\tok:3:40\n").unwrap();
    std::fs::write(corpus.join("variants.txt"), "synth+bigger\n").unwrap();
    run_golden(&corpus, Mode::Bless, None, None).unwrap();
    let s = run_golden(&corpus, Mode::Check, None, None).unwrap();
    assert!(s.differ.is_empty(), "{:?}", s.differ);
    assert_eq!(s.unverified.keys().cloned().collect::<Vec<_>>(), ["synth", "synth+bigger"]);
    run_golden(&corpus, Mode::Report, Some(&out), None).unwrap();
    let d = json::parse(&std::fs::read_to_string(out.join("diff.json")).unwrap()).unwrap();
    assert_eq!(d.at(&["differ", "synth", "unverified"]).and_then(Value::as_str), Some("FAIL:the-plan-of-the-redacted-dump-differs"));
    assert_eq!(d.at(&["differ", "synth+bigger", "lines_changed"]).and_then(Value::as_u64), Some(0));
    assert!(d.at(&["differ", "other"]).is_none());
    std::fs::write(corpus.join("index.tsv"), "guest\thost\n").unwrap();
    assert!(run_golden(&corpus, Mode::Check, None, None).unwrap_err().contains("no guest or check_plan_raw column"));
    std::fs::remove_file(corpus.join("index.tsv")).unwrap();
    // The corpus's own extras are only noted.
    let s = run_golden(&corpus, Mode::Check, None, Some(&Expected::default())).unwrap();
    assert!(s.differ.is_empty() && s.log.iter().any(|l| l.starts_with("note  synth: in the corpus, but not")), "{:?}", s.log);
    // classes.json: its holt section, or None without one.
    std::fs::write(dir.path().join("classes.json"), r#"{"holt": {"families": {"D": ["b", "a"], "M": ["c"]}, "no_family": ["d"], "variants": {"bigger": ["c", "a"], "oneshot": ["b"]}}}"#).unwrap();
    assert_eq!(expected_from(&dir.path().join("classes.json")).unwrap(), Some(Expected { sources: vec!["a".into(), "b".into(), "c".into(), "d".into()], bigger: vec!["a".into(), "c".into()] }));
    assert_eq!(expected_from(&dir.path().join("none.json")).unwrap(), None);
    std::fs::write(dir.path().join("bad.json"), r#"{"holt": {}}"#).unwrap();
    assert!(expected_from(&dir.path().join("bad.json")).unwrap_err().contains("no holt.families"));
}
