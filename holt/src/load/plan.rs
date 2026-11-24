//! `bko-holt load` with no stage: every check, and every command the writing
//! stages would run, without writing anything.

use super::codes::{Notes, Problem, ProblemCode, Problems};
use super::commands::{self, Built};
use super::layout::Layout;
use super::target::{self, Mapping};
use super::{Options, Stage, EXIT_OK, EXIT_PLAN_PROBLEMS};
use crate::sys::Sys;

/// Where `plan` looks for the programs the stages run.  Haiku's own come
/// first there: its rescue image installs rustic-bko and bko-holt in
/// /boot/system/non-packaged/bin (stick/haiku/build.sh), and /bin is
/// /boot/system/bin.
const PATH: &[&str] = if cfg!(target_os = "haiku") {
    &["/boot/home/config/non-packaged/bin", "/boot/home/config/bin", "/boot/system/non-packaged/bin", "/boot/system/bin", "/bin"]
} else {
    &["/usr/local/sbin", "/usr/local/bin", "/usr/sbin", "/usr/bin", "/sbin", "/bin"]
};

/// Where PROGRAM (a name or a path) is installed on the system load runs on.
pub fn find(sys: &Sys, prog: &str) -> Option<String> {
    if prog.starts_with('/') {
        return sys.is_file(prog).then(|| prog.to_string());
    }
    PATH.iter().map(|d| format!("{d}/{prog}")).find(|p| sys.is_file(p))
}

pub fn on_path(sys: &Sys, prog: &str) -> bool {
    find(sys, prog).is_some()
}

/// The programs each stage runs here (not inside the restored system's
/// chroot, whose own tools are restored with it).
fn missing_tools(sys: &Sys, built: &Built) -> Vec<Problem> {
    let mut out: Vec<Problem> = Vec::new();
    for (stage, cmds) in &built.stages {
        for c in cmds.iter().filter(|c| !c.argv.is_empty()) {
            let prog = &c.argv[0];
            if !on_path(sys, prog) && !out.iter().any(|m| m.starts_with(&format!("{prog} "))) {
                out.push(Problem::new(ProblemCode::ToolMissing, format!("{prog} isn't installed here (the {} stage runs it)", stage.name())).with("program", prog).with("stage", stage.name()));
            }
        }
    }
    out
}

/// load's LVM commands take --devices and --devicesfile, which lvm2 has had
/// since 2.03.12: an older one is a problem for a layout with volume groups.
/// (An lvm that won't say its version is left to missing_tools.)
fn lvm_problem(sys: &Sys, layout: &Layout) -> Option<Problem> {
    if layout.v.at(&["storage", "lvm", "volume_groups"]).and_then(crate::json::Value::as_array).is_none_or(|a| a.is_empty()) {
        return None;
    }
    let out = sys.run("lvm", &["version"])?;
    let v = lvm_version(&out)?;
    let version = format!("{}.{:02}.{:02}", v.0, v.1, v.2);
    (v < (2, 3, 12)).then(|| Problem::new(ProblemCode::LvmTooOld, format!("lvm2 {version} is older than 2.03.12, which load's LVM commands need (--devices)")).with("version", version.as_str()))
}

/// A layout with a thin pool (commands.rs makes it with lvcreate) needs an
/// lvm2 with thin support (`lvm segtypes` lists thin-pool), thin_check
/// (thin-provisioning-tools: lvm2 runs it on a pool's metadata when it
/// activates the pool) and the kernel's dm-thin-pool.  What can't be asked
/// (no lvm, no kernel release) is left to missing_tools.
fn thin_problems(sys: &Sys, layout: &Layout) -> Vec<Problem> {
    use crate::json::Value;
    let thin = layout.arr(&["storage", "lvm", "volume_groups"]).iter().any(|vg| {
        vg.get("logical_volumes").and_then(Value::as_array).into_iter().flatten().any(|lv| {
            lv.get("segments").and_then(Value::as_array).into_iter().flatten().any(|g| g.get("type").and_then(Value::as_str) == Some("thin-pool"))
        })
    });
    let mut out = Vec::new();
    if !thin {
        return out;
    }
    if let Some(t) = sys.run("lvm", &["segtypes"]) {
        if !t.lines().any(|l| l.trim() == "thin-pool") {
            out.push(Problem::new(ProblemCode::ThinUnsupported, "this system's lvm2 has no thin-pool segment type (lvm segtypes), which the layout's thin pools need").with("why", "lvm_segtype"));
        }
    }
    if !on_path(sys, "thin_check") {
        out.push(Problem::new(ProblemCode::ThinUnsupported, "thin_check isn't installed here (thin-provisioning-tools): lvm2 checks a thin pool's metadata with it when it activates the pool").with("why", "thin_check"));
    }
    if let Some(k) = sys.read_trim("/proc/sys/kernel/osrelease") {
        let builtin = ["/lib", "/usr/lib"].iter().any(|d| sys.read(&format!("{d}/modules/{k}/modules.builtin")).is_some_and(|b| b.contains("/dm-thin-pool.ko")));
        let module = ["/lib", "/usr/lib"]
            .iter()
            .any(|d| ["", ".zst", ".xz", ".gz"].iter().any(|z| sys.is_file(&format!("{d}/modules/{k}/kernel/drivers/md/dm-thin-pool.ko{z}"))));
        if !builtin && !module && !sys.exists("/sys/module/dm_thin_pool") {
            out.push(Problem::new(ProblemCode::ThinUnsupported, format!("the kernel here ({k}) has no dm-thin-pool module, which the layout's thin pools need")).with("why", "dm_thin_pool"));
        }
    }
    out
}

/// "  LVM version:     2.03.31(2) (2025-02-27)" -> (2, 3, 31).
fn lvm_version(out: &str) -> Option<(u32, u32, u32)> {
    let l = out.lines().find(|l| l.trim_start().starts_with("LVM version:"))?;
    let v = l.split(':').nth(1)?.split_whitespace().next()?;
    let mut n = v.split(|c: char| !c.is_ascii_digit()).filter(|s| !s.is_empty()).map(|s| s.parse::<u32>().ok());
    Some((n.next()??, n.next()??, n.next()??))
}

/// Where a pool's `compatibility=` names its feature-set files: OpenZFS's own
/// two, and the FreeBSD port's third.
const COMPAT_DIRS: [&str; 3] = ["/etc/zfs/compatibility.d", "/usr/share/zfs/compatibility.d", "/usr/local/share/zfs/compatibility.d"];

/// load's `zpool create` passes every feature the source pool had enabled or
/// active as `-o feature@F=enabled`, and the pool's local properties
/// (`compatibility=` among them) as `-o` (commands.rs).  A rescue system whose
/// ZFS doesn't know one of those features, or which hasn't the file a
/// `compatibility=` names, fails the create partway through the run: said here
/// instead, before anything is written.
fn zfs_problems(sys: &Sys, layout: &Layout) -> Vec<Problem> {
    let mut out: Vec<Problem> = Vec::new();
    // Every imported pool: commands.rs makes one `zpool create` for each
    // (its items(), Item::Pool).
    let pools = layout.arr(&["storage", "zfs", "tools", "imported_pools"]);
    if pools.is_empty() {
        return out;
    }
    // No zpool here, or one that won't list its features: nothing can be
    // said about them.  A missing zpool is missing_tools' to report, and a
    // `zpool upgrade -v` we can't parse is no reason to refuse a layout.
    let known = sys.run("zpool", &["upgrade", "-v"]).map(|o| zpool_features(&o)).filter(|f| !f.is_empty());
    for p in pools {
        let name = p.get("name").and_then(crate::json::Value::as_str).unwrap_or("(unnamed)");
        if let Some(known) = &known {
            let unknown: Vec<&str> = p
                .get("features")
                .and_then(crate::json::Value::as_object)
                .into_iter()
                .flatten()
                .filter(|(_, state)| matches!(state.as_str(), Some("enabled" | "active")))
                .map(|(f, _)| f.as_str())
                .filter(|f| !known.iter().any(|k| k == f))
                .collect();
            if !unknown.is_empty() {
                let text = format!(
                    "ZFS pool {name}: this system's ZFS doesn't know the source's feature(s) {}, which zpool create would be asked to enable; the rescue image's ZFS is older than the source's, so a newer image is needed",
                    unknown.join(", ")
                );
                out.push(Problem::new(ProblemCode::ZfsFeatureUnknown, text).with("pool", name).with("features", unknown.clone()));
            }
        }
        // off and legacy name no file; anything else is a comma-separated
        // list of file names, or of absolute paths.
        let value = p.at(&["local_properties", "compatibility"]).and_then(crate::json::Value::as_str).unwrap_or("off");
        if value == "off" || value == "legacy" {
            continue;
        }
        for f in value.split(',').map(str::trim).filter(|f| !f.is_empty()) {
            if f.starts_with('/') {
                if !sys.is_file(f) {
                    out.push(Problem::new(ProblemCode::ZfsCompatMissing, format!("ZFS pool {name}: its compatibility={value} names {f}, which isn't on this system; a rescue image that has it is needed")).with("pool", name).with("file", f));
                }
            } else if !COMPAT_DIRS.iter().any(|d| sys.is_file(&format!("{d}/{f}"))) {
                out.push(
                    Problem::new(ProblemCode::ZfsCompatMissing, format!("ZFS pool {name}: its compatibility={value} names {f}, which is in none of {}; a rescue image that has it is needed", COMPAT_DIRS.join(", ")))
                        .with("pool", name)
                        .with("file", f),
                );
            }
        }
    }
    out
}

/// The feature short names `zpool upgrade -v` lists: its table, up to the
/// legacy versions printed after it (the parse stick/illumos/build.sh's
/// `info` uses; OpenZFS and illumos print the same kind of table, a dashed
/// rule then a line per feature whose first word is the name, its
/// description indented under it).
fn zpool_features(out: &str) -> Vec<String> {
    let mut names = Vec::new();
    let mut in_table = false;
    for l in out.lines() {
        if l.starts_with("The following legacy") {
            break;
        }
        if !l.is_empty() && l.chars().all(|c| c == '-') {
            in_table = true;
        } else if in_table && l.starts_with(|c: char| c.is_ascii_lowercase()) {
            if let Some(w) = l.split_whitespace().next() {
                names.push(w.to_string());
            }
        }
    }
    names
}

/// The archiver must be BackOtter's, as the rescue stick's build checks.
fn archiver_problem(sys: &Sys, opts: &Options) -> Option<Problem> {
    opts.repo.as_ref()?;
    let Some(exe) = find(sys, &opts.archiver) else {
        return Some(Problem::new(ProblemCode::ArchiverMissing, format!("{} isn't installed here; restore can't run it", opts.archiver)).with("program", &opts.archiver));
    };
    match sys.run_file(&exe, &["--version"]) {
        Some(v) if v.contains("backotter") => None,
        Some(v) => Some(
            Problem::new(ProblemCode::ArchiverNotBko, format!("{} isn't BackOtter's rustic ({}); restore needs rustic-bko", opts.archiver, v.trim()))
                .with("program", &opts.archiver)
                .with("version", v.trim()),
        ),
        // It answers nothing: no version to show.
        None => Some(Problem::new(ProblemCode::ArchiverNotBko, format!("{exe} --version failed; restore can't run it")).with("program", &opts.archiver).with("version", crate::json::Value::Null)),
    }
}

pub struct Planned {
    pub mapping: Mapping,
    pub built: Built,
    pub problems: Problems,
    pub notes: Notes,
    /// What --fit-largest changed, if anything (fit.rs).
    pub fit: super::fit::Fit,
    /// The disks here, as target::prepare read them.
    pub system: target::System,
    /// Under --json, what the plan document says of the run that only the
    /// layout the stages build from can say (JsonFacts).
    pub json: Option<JsonFacts>,
}

/// What a --json plan works out with the layout the stages build from
/// (--fit-largest's, --pools'), for the plan document.
#[derive(Debug, Default)]
pub struct JsonFacts {
    /// The run bound to what was reviewed (J20): jsonout::plan_sha256.
    pub plan_sha256: String,
    /// The targets this session's partition stage recorded
    /// (target::load_wrote).
    pub recorded: Vec<String>,
    /// Whether this run's partition stage runs, wiping every target
    /// (target::partition_runs).
    pub partition_runs: bool,
    /// Per target, the stages that would make something anew on it, as
    /// exec would run them (with --through close when the run names no
    /// stage).
    pub writes: Vec<(String, Vec<&'static str>)>,
}

/// Whether the argument ARG names the device /dev/NODE or one of its
/// partitions or slices (/dev/sdb2, /dev/nvme0n1p3, /dev/vtbd0p3,
/// /dev/dsk/c1t0d0s0, OpenBSD's /dev/sd0a), and not another disk
/// (/dev/sdbb1, /dev/sdaa, /dev/nvme0n10p1).  Under /dev/dsk and
/// /dev/rdsk (illumos) a slice or partition is s or p and digits.  Else a
/// node ending in a letter (sda) has digits after it; one ending in a
/// digit (nvme0n1, ada0, sd0) a p or s and digits, or one partition
/// letter, never more digits.
fn names_node(arg: &str, node: &str) -> bool {
    let digit = |r: &str| r.starts_with(|c: char| c.is_ascii_digit());
    ["/dev/", "/dev/dsk/", "/dev/rdsk/", "/dev/r"].iter().any(|pre| {
        let Some(rest) = arg.strip_prefix(pre).and_then(|r| r.strip_prefix(node)) else { return false };
        if rest.is_empty() {
            return true;
        }
        if pre.ends_with("dsk/") {
            return rest.strip_prefix(['p', 's']).is_some_and(digit);
        }
        if !node.ends_with(|c: char| c.is_ascii_digit()) {
            return digit(rest);
        }
        (rest.len() == 1 && rest.as_bytes()[0].is_ascii_lowercase()) || rest.strip_prefix(['p', 's']).is_some_and(digit)
    })
}

/// Whether the commands CMDS make something on target NODE: one names it
/// (names_node), or this layout has that one target only.
fn touches(cmds: &[commands::Cmd], node: &str, only_target: bool) -> bool {
    let run = |c: &&commands::Cmd| !c.is_note() && c.check.is_none();
    if only_target {
        return cmds.iter().any(|c| run(&c));
    }
    cmds.iter().filter(run).any(|c| c.argv.iter().chain(c.write.iter()).any(|a| a.split(['=', ',']).any(|w| names_node(w, node))))
}

/// The --json run's checks of its own arguments (holt-json.md, "Secrets"):
/// no prompt, so a repository without --password-file is a problem; a
/// password in the repository's URL never reaches argv or the log; and
/// every key file and the password file are bko-rescue's own, private.
fn json_problems(layout: &Layout, opts: &Options, sys: &Sys, built: &Built) -> Vec<Problem> {
    let mut out = Vec::new();
    if let Some(repo) = &opts.repo {
        if repo_has_password(repo) {
            out.push(Problem::new(ProblemCode::RepoHasPassword, "--repo: the repository's URL holds a password; under --json it goes in --password-file FILE, never on the command line"));
        }
        if opts.password_file.is_none() {
            out.push(Problem::new(ProblemCode::PasswordNeeded, "--repo: give the repository's password with --password-file FILE (under --json nothing asks for it)"));
        }
    }
    // The keys whose files hold a passphrase as typed: none may end in a
    // newline, which cryptsetup and geli would take as part of it.
    let mut passphrase: Vec<String> = built.keys.iter().filter(|k| k.unlock == "passphrase").flat_map(|k| k.names.iter().cloned()).collect();
    for ds in layout.arr(&["storage", "zfs", "tools", "datasets"]) {
        let name = ds.get("name").and_then(crate::json::Value::as_str).unwrap_or_default();
        if ds.get("encryptionroot").and_then(crate::json::Value::as_str) == Some(name) && ds.get("keyformat").and_then(crate::json::Value::as_str) == Some("passphrase") {
            passphrase.push(name.to_string());
        }
    }
    let files = opts.key_files.iter().map(|(n, f)| (Some(n.as_str()), f.as_str())).chain(opts.password_file.iter().map(|f| (None, f.as_str())));
    for (name, path) in files {
        let typed = name.is_some_and(|n| passphrase.iter().any(|p| p == n));
        if let Some((check, why)) = key_file_unsafe(sys, path, typed) {
            let flag = match name {
                Some(n) => format!("--key-file {n}={path}"),
                None => format!("--password-file {path}"),
            };
            out.push(Problem::new(ProblemCode::KeyFileUnsafe, format!("{flag}: {why}; under --json a key file must be a private file of bko-holt's own user")).with("name", name).with("path", path).with("check", check));
        }
    }
    out
}

/// Whether a repository's URL holds a password: `SCHEME://USER:PASS@...`,
/// rest:'s too (`rest:https://user:pass@host/`).
pub fn repo_has_password(repo: &str) -> bool {
    let Some((_, rest)) = repo.split_once("://") else { return false };
    let authority = rest.split('/').next().unwrap_or(rest);
    authority.rsplit_once('@').is_some_and(|(userinfo, _)| userinfo.contains(':'))
}

/// REPO with the password in its URL (repo_has_password) as `REDACTED`,
/// so it is still refused: what a --json run's document, its commands and
/// its record carry in its place (holt-json.md, Principle 4).  Any other
/// REPO as it is.
pub fn repo_redacted(repo: &str) -> String {
    if !repo_has_password(repo) {
        return repo.to_string();
    }
    let Some((scheme, rest)) = repo.split_once("://") else { return repo.to_string() };
    let end = rest.find('/').unwrap_or(rest.len());
    let (authority, path) = rest.split_at(end);
    let Some((userinfo, host)) = authority.rsplit_once('@') else { return repo.to_string() };
    let user = userinfo.split_once(':').map_or(userinfo, |(u, _)| u);
    format!("{scheme}://{user}:REDACTED@{host}{path}")
}

/// ARGS with each that holds a repository URL's password redacted
/// (repo_redacted): a --json run's `args` and its last-load.json.
pub fn args_redacted(args: &[String]) -> Vec<String> {
    args.iter().map(|a| repo_redacted(a)).collect()
}

/// The first of the key-file checks PATH fails (holt-json.md, "Secrets"
/// 4): its name and why, or None.  Never reads the key, but for the last
/// byte of one a passphrase is TYPED into (a newline there).
fn key_file_unsafe(sys: &Sys, path: &str, typed: bool) -> Option<(&'static str, String)> {
    use std::os::unix::fs::{FileExt, MetadataExt};
    if !path.starts_with('/') {
        return Some(("absolute", "not an absolute path".into()));
    }
    let real = sys.p(path);
    let Ok(m) = std::fs::symlink_metadata(&real) else { return Some(("missing", "isn't there".into())) };
    if !m.file_type().is_file() {
        return Some(("regular", "isn't a regular file (a symbolic link is refused)".into()));
    }
    let me = crate::sys::euid();
    if m.uid() != me {
        return Some(("owner", format!("is owned by uid {}, not bko-holt's {me}", m.uid())));
    }
    if m.mode() & 0o077 != 0 {
        return Some(("mode", format!("has mode {:o}: group or others may read it", m.mode() & 0o7777)));
    }
    let dir = real.parent().map(std::fs::symlink_metadata);
    match dir {
        Some(Ok(d)) if d.uid() != me => return Some(("dir_owner", format!("is in a directory owned by uid {}, not bko-holt's {me}", d.uid()))),
        Some(Ok(d)) if d.mode() & 0o077 != 0 => return Some(("dir_mode", format!("is in a directory of mode {:o}: group or others may reach it", d.mode() & 0o7777))),
        Some(Ok(_)) => {}
        _ => return Some(("dir_owner", "its directory can't be read".into())),
    }
    if m.len() == 0 {
        return Some(("empty", "is empty".into()));
    }
    if m.len() > 8 << 20 {
        return Some(("too_big", format!("is {} bytes, more than 8 MiB", m.len())));
    }
    if typed {
        let mut last = [0u8];
        let read = std::fs::File::open(&real).and_then(|f| f.read_exact_at(&mut last, m.len() - 1));
        if read.is_err() {
            return Some(("missing", "can't be read".into()));
        }
        if last[0] == b'\n' {
            return Some(("newline", "ends in a newline, which would be part of the passphrase".into()));
        }
    }
    None
}

/// Every check `plan` makes; the writing stages make them again first.
pub fn check(layout: &Layout, opts: &Options, sys: &Sys) -> Planned {
    // A layout not yet edited for --reformat (a caller other than load::run).
    let edited = super::reformat::edited(layout, &opts.reformats);
    let layout = edited.as_ref().unwrap_or(layout);
    let given = layout;
    let target::Prepared { mapping, layout, fit, system } = target::prepare(layout, opts, sys);
    let layout = &*layout;
    let built = commands::build(layout, opts, &mapping);
    let mut problems = mapping.problems.clone();
    problems.extend(built.coded_problems());
    problems.extend(archiver_problem(sys, opts));
    problems.extend(missing_tools(sys, &built));
    problems.extend(lvm_problem(sys, layout));
    problems.extend(thin_problems(sys, layout));
    problems.extend(zfs_problems(sys, layout));
    if super::has_omitted(&layout.v) {
        problems.push(Problem::new(
            ProblemCode::FromBugReport,
            "this layout comes from a bug report (`bko-holt report-bug`), which leaves boot-area and ESP images out: restore from the dump itself",
        ));
    }
    if !sys.privileged {
        problems.push(Problem::new(ProblemCode::NotRoot, "not running as root: the target disks' tables and signatures can't be read, so blank disks can't be told from used ones"));
    }
    let mut notes = mapping.notes.clone();
    notes.extend(built.coded_notes());
    // Under --json (holt-json.md, J19, J20 and "Secrets"): the erase gate,
    // the run's own arguments, and the plan's hash.  Text mode never comes
    // here.
    let json = opts.json.then(|| {
        problems.extend(target::erase_gate(layout, opts, &system, sys, &mapping));
        problems.extend(json_problems(layout, opts, sys, &built));
        let plan_sha256 = super::jsonout::plan_sha256(&built, &mapping, &system, sys);
        if let Some(want) = opts.expect_plan.as_deref().filter(|w| **w != plan_sha256) {
            problems.push(
                Problem::new(ProblemCode::PlanChanged, format!("--expect-plan {want}: this run's plan is {plan_sha256}: a command or a disk's identity changed since the plan was reviewed"))
                    .with("expected", want)
                    .with("got", plan_sha256.as_str())
                    // Which disk moved can't be read from a hash alone.
                    .with("targets", crate::json::Value::Null),
            );
        }
        let recorded = mapping
            .pairs
            .iter()
            .filter(|(s, t)| matches!((layout.disk(s), system.disk(t)), (Some(d), Some(t)) if target::load_wrote(layout, d, t, sys)))
            .map(|(_, t)| t.clone())
            .collect();
        let would = would_run(layout, opts, sys, &system, &mapping, &built, || super::status::rows(given, opts, sys).0);
        // Nothing made anew over this session's restore (J19).
        problems.extend(super::status::remake_problems(&mapping, &system, &would, sys));
        JsonFacts { plan_sha256, recorded, partition_runs: would.partition, writes: would.writes }
    });
    Planned { mapping, built, problems, notes, fit, system, json }
}

/// What a writing run with OPTS would run, as exec runs it: --only its one
/// stage; else every writing stage up to --through's (the plan's: up to
/// close), each skipped as status's rows (ROWS, read only then) show it.
#[derive(Debug, Default)]
pub struct WouldRun {
    /// Whether its partition stage runs, wiping every target
    /// (target::partition_runs).
    pub partition: bool,
    /// Of assemble and format, the ones that run, with a command that
    /// makes something.
    pub remaking: Vec<Stage>,
    /// Per target, the stages that would make something anew on it.
    pub writes: Vec<(String, Vec<&'static str>)>,
}

pub(crate) fn would_run(
    layout: &Layout,
    opts: &Options,
    sys: &Sys,
    system: &target::System,
    mapping: &Mapping,
    built: &Built,
    rows: impl FnOnce() -> Vec<(Stage, super::status::State, String)>,
) -> WouldRun {
    let only = opts.only.filter(|s| s.writes());
    let through = opts.through.filter(|s| s.writes());
    let runs_stage = |s: Stage| only.is_none_or(|o| o == s) && through.is_none_or(|t| s <= t);
    let partition = runs_stage(Stage::Partition) && (only.is_some() || target::partition_runs(layout, opts, system, sys, mapping));
    let wiped = partition && !opts.erase.is_empty();
    let rows = if only.is_none() { rows() } else { Vec::new() };
    let skipped = |s: Stage| {
        only.is_none() && rows.iter().find(|r| r.0 == s).is_some_and(|r| r.1 == super::status::State::Nothing || (r.1 == super::status::State::Done && !wiped))
    };
    let makes = |c: &commands::Cmd| !c.is_note() && c.check.is_none();
    let remaking = [Stage::Assemble, Stage::Format].into_iter().filter(|&s| runs_stage(s) && !skipped(s) && built.stage(s).iter().any(makes)).collect::<Vec<_>>();
    let one = mapping.pairs.len() == 1;
    let writes = mapping
        .pairs
        .iter()
        .map(|(_, t)| {
            let mut w = Vec::new();
            if partition {
                w.push("partition");
            }
            for &s in &remaking {
                if touches(built.stage(s), t, one) {
                    w.push(s.name());
                }
            }
            (t.clone(), w)
        })
        .collect();
    WouldRun { partition, remaking, writes }
}

pub fn run(layout: &Layout, opts: &Options, sys: &Sys) -> (u8, String, String) {
    let p = check(layout, opts, sys);
    render(layout, opts, &p, false)
}

/// `load LAYOUT --json` (holt-json.md, "bko-holt-plan"): the plan document,
/// made of the --json run's checks, with in its `text` what text mode
/// prints for the same arguments (J18), a private write's text left out.
/// Standard error is what text mode prints there for the same arguments.
/// A password in --repo's URL is refused (repo_has_password) and never
/// shown: the whole document is planned with it redacted (repo_redacted).
pub fn json_run(layout: &Layout, opts: &Options, sys: &Sys, layout_text: &str) -> (u8, String, String) {
    let redacted;
    let opts = if opts.repo.as_deref().is_some_and(repo_has_password) {
        let mut o = opts.clone();
        o.repo = o.repo.as_deref().map(repo_redacted);
        o.argv = args_redacted(&o.argv);
        redacted = o;
        &redacted
    } else {
        opts
    };
    let p = check(layout, opts, sys);
    let mut text_opts = opts.clone();
    text_opts.json = false;
    text_opts.expect_plan = None;
    let tp = check(layout, &text_opts, sys);
    let (_, text, err) = render(layout, &text_opts, &tp, true);
    let code = if p.problems.is_empty() { EXIT_OK } else { EXIT_PLAN_PROBLEMS };
    let doc = super::jsonout::plan_doc(layout, opts, sys, layout_text, &p, code, &text);
    (code, doc, err)
}

/// The text plan of P: what `bko-holt load LAYOUT` prints, with ELIDE a
/// private write's text left out (Cmd::shell_elided).
fn render(layout: &Layout, opts: &Options, p: &Planned, elide: bool) -> (u8, String, String) {
    let mut out = String::new();
    out += &format!("# bko-holt load plan for {}\n", opts.layout);
    out += &format!(
        "# Source: {}, {}, {} boot with {}, dumped {}\n",
        layout.hostname(),
        layout.os_name(),
        if layout.uefi() { "UEFI" } else { "BIOS" },
        layout.str_at(&["boot", "loader", "name"]).unwrap_or("an unknown loader"),
        layout.str_at(&["generated"]).unwrap_or("at an unknown time"),
    );
    out += "#\n# Disks:\n";
    for d in layout.disks.iter().filter(|d| d.used) {
        match p.mapping.target_of(&d.name) {
            Some(t) => out += &format!("#   {} -> /dev/{t}\n", d.describe()),
            None => out += &format!("#   {} -> (no target)\n", d.describe()),
        }
    }
    // What --fit-largest made of each disk: the plan says so plainly, since
    // the result isn't the source's own geometry.
    if let (Some(pct), false) = (opts.fit_largest, p.fit.disks.is_empty()) {
        out += "#\n# Fit: this is not the source's geometry\n";
        for l in p.fit.report(pct) {
            out += &format!("#   {l}\n");
        }
    }
    if !p.problems.is_empty() {
        out += &format!("#\n# Problems ({}): nothing can be written until they're fixed\n", p.problems.len());
        for x in &p.problems {
            out += &format!("#   - {x}\n");
        }
    }
    if !p.notes.is_empty() {
        out += "#\n# Notes:\n";
        for x in &p.notes {
            out += &format!("#   - {x}\n");
        }
    }
    for (stage, cmds) in &p.built.stages {
        out += &format!("\n# ---- {} ----\n", stage.name());
        if cmds.is_empty() {
            out += "# (nothing to do)\n";
        }
        for c in cmds {
            out += &if elide { c.shell_elided() } else { c.shell() };
        }
    }
    out += "\n# Nothing was written.";
    if p.problems.is_empty() {
        let through = if opts.repo.is_some() { Stage::Close } else { Stage::Mount };
        out += &format!("  To run it: bko-holt load {} ... --through {} --commit\n", opts.layout, through.name());
    } else {
        out.push('\n');
    }
    // Never a recovery key (crate::redact), whatever the layout held.
    let out = crate::redact::scrub_recovery_keys(&out).into_owned();
    let code = if p.problems.is_empty() { EXIT_OK } else { EXIT_PLAN_PROBLEMS };
    let err = if p.problems.is_empty() { String::new() } else { format!("bko-holt load: plan found {} problem(s); see above\n", p.problems.len()) };
    (code, out, err)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::load::layout::sample;
    use crate::load::{parse_args, Parsed};
    use crate::sys::FakeRunner;
    use crate::testutil::Tree;

    const TOOLS: [&str; 9] = ["wipefs", "dd", "sfdisk", "udevadm", "mke2fs", "tune2fs", "mkswap", "mkfs.btrfs", "btrfs"];

    #[test]
    fn lvm_versions() {
        let out = "  LVM version:     2.03.31(2) (2025-02-27)\n  Library version: 1.02.205 (2025-02-27)\n";
        assert_eq!(lvm_version(out), Some((2, 3, 31)));
        assert_eq!(lvm_version("  LVM version:     2.03.11(2) (2021-01-08)\n"), Some((2, 3, 11)));
        assert!(lvm_version("  LVM version:     2.03.11(2) (2021-01-08)\n").unwrap() < (2, 3, 12));
        assert_eq!(lvm_version("nothing"), None);
    }

    /// A thin pool needs lvm2's thin-pool, thin_check and dm-thin-pool; a
    /// layout without one asks for none of them.
    #[test]
    fn thin_tools() {
        let vgs = format!(r#""lvm": {{"volume_groups": [{}]}}"#, crate::probe::lvm::sample::thin_vg().compact());
        let thin = Layout::from_json(crate::json::parse(&sample::bios_mbr().replace(r#""lvm": {"volume_groups": []}"#, &vgs)).unwrap()).unwrap();
        let t = Tree::new("load-plan-thin");
        t.file("/proc/sys/kernel/osrelease", "7.0.0-43-generic\n");
        let lacking = Sys::new(t.root(), Box::new(FakeRunner::default().with("lvm segtypes", "striped\nlinear\n")), true);
        assert_eq!(
            thin_problems(&lacking, &thin),
            vec![
                "this system's lvm2 has no thin-pool segment type (lvm segtypes), which the layout's thin pools need",
                "thin_check isn't installed here (thin-provisioning-tools): lvm2 checks a thin pool's metadata with it when it activates the pool",
                "the kernel here (7.0.0-43-generic) has no dm-thin-pool module, which the layout's thin pools need",
            ]
        );
        assert!(thin_problems(&lacking, &layout()).is_empty());
        t.file("/usr/sbin/thin_check", "").file("/usr/lib/modules/7.0.0-43-generic/kernel/drivers/md/dm-thin-pool.ko.zst", "");
        let ok = Sys::new(t.root(), Box::new(FakeRunner::default().with("lvm segtypes", "striped\nthin-pool\nthin\n")), true);
        assert!(thin_problems(&ok, &thin).is_empty());
    }

    fn opts(a: &[&str]) -> Options {
        let mut v = vec!["l.json".to_string()];
        v.extend(a.iter().map(|s| s.to_string()));
        match parse_args(&v).unwrap() {
            Parsed::Run(o) => o,
            Parsed::Help => unreachable!(),
        }
    }

    /// A rescue system with a blank 16 GiB vdb and every tool installed.
    fn tree() -> Tree {
        let t = Tree::new("load-plan");
        t.disk("vdb", "253:16", &[], 512).file("/sys/block/vdb/size", "33554432\n");
        t.file("/proc/self/mountinfo", "").file("/proc/swaps", "Filename\tType\tSize\tUsed\tPriority\n");
        for p in TOOLS.iter().chain(["mkdir", "mount", "umount", "chroot", "cp", "sh", "bash", "rustic-bko"].iter()) {
            t.file(&format!("/usr/sbin/{p}"), "");
        }
        t
    }

    fn layout() -> Layout {
        Layout::from_json(crate::json::parse(&sample::bios_mbr()).unwrap()).unwrap()
    }

    #[test]
    fn clean_plan() {
        let t = tree();
        let runner = FakeRunner::default().with("/usr/sbin/rustic-bko --version", "rustic 0.11.4+backotter.4\n");
        let s = Sys::new(t.root(), Box::new(runner), true);
        let (code, out, err) = run(&layout(), &opts(&["--map", "vda=vdb", "--repo", "/srv/r"]), &s);
        assert_eq!(code, EXIT_OK, "{out}{err}");
        assert!(out.contains("#   vda (16 GiB) -> /dev/vdb\n"), "{out}");
        assert!(out.contains("# Source: deb13, debian 13, BIOS boot with grub,"), "{out}");
        let named = Layout::from_json(crate::json::parse(&sample::bios_mbr().replace("\"version_id\": \"13\"", "\"version_id\": \"13\", \"pretty_name\": \"Debian GNU/Linux 13 (trixie)\"")).unwrap()).unwrap();
        let (_, out, _) = run(&named, &opts(&["--map", "vda=vdb", "--repo", "/srv/r"]), &s);
        assert!(out.contains("# Source: deb13, Debian GNU/Linux 13 (trixie), BIOS"), "{out}");
        for st in ["partition", "assemble", "format", "mount", "restore", "boot", "close"] {
            assert!(out.contains(&format!("# ---- {st} ----\n")), "{st}");
        }
        assert!(out.contains("--through close --commit"), "{out}");
    }

    #[test]
    fn fit_largest_plans_a_smaller_target() {
        let t = tree();
        // vdb a GiB smaller than the source's vda.
        t.file("/sys/block/vdb/size", format!("{}\n", 33554432 - 2097152));
        let runner = FakeRunner::default().with("/usr/sbin/rustic-bko --version", "rustic 0.11.4+backotter.4\n");
        let s = Sys::new(t.root(), Box::new(runner), true);
        let l = Layout::from_json(crate::json::parse(&sample::fittable()).unwrap()).unwrap();
        // Without the flag a smaller target is refused, and says there is one.
        let (code, out, _) = run(&l, &opts(&["--map", "vda=vdb", "--repo", "/srv/r"]), &s);
        assert_eq!(code, EXIT_PLAN_PROBLEMS);
        assert!(out.contains("the target is smaller (15 GiB < 16 GiB); version 1 needs the same size or bigger, or --fit-largest PCT"), "{out}");
        // With it, the root gives up the GiB and the plan says what it did.
        let (code, out, err) = run(&l, &opts(&["--map", "vda=vdb", "--repo", "/srv/r", "--fit-largest", "10"]), &s);
        assert_eq!(code, EXIT_OK, "{out}{err}");
        assert!(out.contains("# Fit: this is not the source's geometry"), "{out}");
        assert!(out.contains("vda -> /dev/vdb: fitted with --fit-largest 10%: 16 GiB -> 15 GiB, last usable sector 33554431 -> 31457279"), "{out}");
        assert!(out.contains("vda3 (btrfs) shrinks by 1 GiB: 27693056 -> 25595904 sectors"), "{out}");
        assert!(out.contains("vda3 (btrfs): 2 GiB used, 11.2 GiB free; the shrink of 1 GiB needs 2 GiB free"), "{out}");
        // The script the partition stage runs carries the new size.
        assert!(out.contains("/dev/vdb3 : start=5861376, size=25595904, type=83"), "{out}");
        // 2% of the root isn't the GiB it would have to give up.
        let (code, out, _) = run(&l, &opts(&["--map", "vda=vdb", "--repo", "/srv/r", "--fit-largest", "2"]), &s);
        assert_eq!(code, EXIT_PLAN_PROBLEMS);
        assert!(out.contains("more than 2%"), "{out}");
    }

    /// What `zpool upgrade -v` prints: the feature table, a rule, then the
    /// legacy versions.  Only async_destroy, empty_bpobj and encryption are
    /// known here.
    const UPGRADE_V: &str = "This system supports ZFS pool feature flags.\n\nThe following features are supported:\n\nFEAT DESCRIPTION\n\
         -------------------------------------------------------------\n\
         async_destroy                         (read-only compatible)\n     Destroy filesystems asynchronously.\n\n\
         empty_bpobj                           (read-only compatible)\n     Snapshots use less space.\n\n\
         encryption\n     Support for dataset level encryption\n\n\
         -------------------------------------------------------------\n\n\
         The following legacy versions are also supported:\n\nVER  DESCRIPTION\n---  -----------------------------------------------------\n \
         1   Initial ZFS version\n 2   Ditto blocks (replicated metadata)\n";

    #[test]
    fn zpool_feature_table() {
        assert_eq!(zpool_features(UPGRADE_V), vec!["async_destroy", "empty_bpobj", "encryption"]);
        // Nothing to go on: no features, so zfs_problems says nothing.
        assert!(zpool_features("zpool: command not found\n").is_empty());
        assert!(zpool_features("").is_empty());
    }

    /// A dump with one pool, rpool, carrying FEATURES and LOCAL: all
    /// zfs_problems looks at.
    fn zfs_dump(features: &str, local: &str) -> Layout {
        let pool = format!("{{\"name\": \"rpool\", \"features\": {features}, \"local_properties\": {local}, \"vdevs\": {{}}}}");
        let text = sample::bios_mbr().replace(
            "\"zfs\": {\"pools_from_labels\": [], \"tools\": null}",
            &format!("\"zfs\": {{\"pools_from_labels\": [], \"tools\": {{\"imported_pools\": [{pool}], \"datasets\": []}}}}"),
        );
        Layout::from_json(crate::json::parse(&text).unwrap()).unwrap()
    }

    /// A rescue system whose `zpool upgrade -v` answers, and which has the
    /// grub2 compatibility file but no mycustom.
    fn zfs_sys(t: &Tree) -> Sys {
        t.file("/usr/share/zfs/compatibility.d/grub2", "async_destroy\nempty_bpobj\n");
        Sys::new(t.root(), Box::new(FakeRunner::default().with("zpool upgrade -v", UPGRADE_V)), true)
    }

    #[test]
    fn zfs_features_the_rescue_system_lacks() {
        let t = tree();
        let s = zfs_sys(&t);
        // Every enabled or active feature known here: nothing to say.
        let known = zfs_dump("{\"async_destroy\": \"enabled\", \"empty_bpobj\": \"active\", \"encryption\": \"disabled\"}", "{}");
        assert!(zfs_problems(&s, &known).is_empty());
        // One unknown active and one unknown enabled: both in one problem.
        let older = zfs_dump("{\"blake3\": \"active\", \"async_destroy\": \"enabled\", \"fast_dedup\": \"enabled\", \"large_dnode\": \"disabled\"}", "{}");
        let p = zfs_problems(&s, &older);
        assert_eq!(p.len(), 1, "{p:?}");
        assert!(p[0].starts_with("ZFS pool rpool: this system's ZFS doesn't know the source's feature(s) blake3, fast_dedup,"), "{}", p[0]);
        assert!(p[0].contains("older than the source's, so a newer image is needed"), "{}", p[0]);
        // A feature the source had disabled is never passed to zpool create.
        let off = zfs_dump("{\"blake3\": \"disabled\", \"fast_dedup\": \"disabled\"}", "{}");
        assert!(zfs_problems(&s, &off).is_empty());
        // No answer from zpool (none installed, or a table we can't read):
        // missing_tools has the first, and neither is a reason to refuse.
        let mute = Sys::new(t.root(), Box::new(FakeRunner::default()), true);
        assert!(zfs_problems(&mute, &older).is_empty());
        // A layout without pools doesn't even run zpool.
        let runner = FakeRunner::default().with("zpool upgrade -v", UPGRADE_V);
        let plain = Sys::new(t.root(), Box::new(runner), true);
        assert!(zfs_problems(&plain, &layout()).is_empty());
    }

    #[test]
    fn zfs_compatibility_files() {
        let t = tree();
        let s = zfs_sys(&t);
        let dump = |local: &str| zfs_dump("{}", local);
        // off and legacy name no file, and a file that's there is fine.
        for local in ["{}", "{\"compatibility\": \"off\"}", "{\"compatibility\": \"legacy\"}", "{\"compatibility\": \"grub2\"}"] {
            assert!(zfs_problems(&s, &dump(local)).is_empty(), "{local}");
        }
        let p = zfs_problems(&s, &dump("{\"compatibility\": \"mycustom\"}"));
        assert_eq!(p.len(), 1, "{p:?}");
        assert!(p[0].starts_with("ZFS pool rpool: its compatibility=mycustom names mycustom, which is in none of /etc/zfs/compatibility.d,"), "{}", p[0]);
        // A list: only the missing one is named.
        let p = zfs_problems(&s, &dump("{\"compatibility\": \"grub2,mycustom\"}"));
        assert_eq!(p.len(), 1, "{p:?}");
        assert!(p[0].contains("names mycustom"), "{}", p[0]);
        // An absolute path is looked for where it says.
        let p = zfs_problems(&s, &dump("{\"compatibility\": \"/etc/zfs/mine\"}"));
        assert_eq!(p.len(), 1, "{p:?}");
        assert!(p[0].contains("names /etc/zfs/mine, which isn't on this system"), "{}", p[0]);
        t.file("/etc/zfs/mine", "async_destroy\n");
        assert!(zfs_problems(&s, &dump("{\"compatibility\": \"/etc/zfs/mine\"}")).is_empty());
    }

    /// The whole plan says so, as check() makes it.
    #[test]
    fn zfs_problems_reach_the_plan() {
        let t = tree();
        let s = zfs_sys(&t);
        let l = zfs_dump("{\"blake3\": \"active\"}", "{\"compatibility\": \"mycustom\"}");
        let (code, out, _) = run(&l, &opts(&["--map", "vda=vdb", "--repo", "/srv/r"]), &s);
        assert_eq!(code, EXIT_PLAN_PROBLEMS);
        assert!(out.contains("doesn't know the source's feature(s) blake3"), "{out}");
        assert!(out.contains("names mycustom"), "{out}");
    }

    /// A pool shaped like Ubuntu's ZFS install boot pool: most features
    /// disabled, a few enabled or active, and `compatibility` set locally
    /// (the ZFS-on-root guides set it for the boot pool so GRUB can read
    /// it).  Modelled on tests/corpus/ubuntu26-zfs/src.json's bpool.
    fn bpool_dump() -> String {
        r#"{
  "format": "bko-holt", "schema": 1, "privileged": true,
  "host": {"hostname": "ubu26zfs"},
  "os": {"id": "debian", "version_id": "13"},
  "firmware": {"mode": "bios"},
  "storage": {
    "disks": [{
      "name": "vda", "kind": "disk", "size_bytes": 17179869184, "logical_sector_size": 512,
      "model": null, "serial": null, "wwid": null,
      "partition_table": {"type": "dos", "sector_size": 512, "disk_signature": "1f2e3d4c"},
      "partitions": [
        {"name": "vda1", "number": 1, "start_lba": 2048, "sectors": 3407872, "type": "0xbf", "bootable": false,
         "content": {"type": "zfs_member", "usage": "other", "label": "bpool"}}
      ],
      "content": null, "boot_code": {"kind": "grub2"}
    }],
    "md_arrays": [], "dm_devices": [],
    "lvm": {"volume_groups": []},
    "zfs": {"pools_from_labels": [], "tools": {
      "imported_pools": [{
        "name": "bpool",
        "properties": {"ashift": "12", "health": "ONLINE", "compatibility": "grub2"},
        "local_properties": {"ashift": "12", "autotrim": "on", "compatibility": "grub2", "cachefile": "/etc/zfs/zpool.cache"},
        "features": {"async_destroy": "enabled", "empty_bpobj": "active", "lz4_compress": "active",
                     "multi_vdev_crash_dump": "disabled", "extensible_dataset": "enabled",
                     "large_dnode": "disabled", "encryption": "disabled"},
        "vdevs": {"vdevs": {"bpool": {"name": "bpool", "vdev_type": "root",
                   "vdevs": {"/dev/vda1": {"name": "/dev/vda1", "vdev_type": "disk", "path": "/dev/vda1"}}}},
                  "logs": null, "l2cache": null, "spares": null, "special": null, "dedup": null}
      }],
      "datasets": []
    }},
    "btrfs": [], "xfs_external_logs": [],
    "fstab": [], "crypttab": null, "mounts": [], "swaps": []
  },
  "boot": {
    "loader": {"name": "grub", "firmware": "bios", "via_shim": false},
    "efi": null, "grub": null,
    "kernels": {"installed": []}
  }
}"#
        .to_string()
    }

    /// What load's `zpool create` passes for such a pool: the source's
    /// feature flags only (-d), each enabled or active one enabled, no
    /// disabled one, and the pool's local properties.
    #[test]
    fn zpool_create_carries_the_sources_features() {
        let l = Layout::from_json(crate::json::parse(&bpool_dump()).unwrap()).unwrap();
        let b = commands::build(&l, &opts(&["--map", "vda=vdb"]), &target::Mapping { pairs: vec![("vda".into(), "vdb".into())], ..target::Mapping::default() });
        let create = b
            .stage(Stage::Assemble)
            .iter()
            .map(commands::Cmd::shell)
            .find(|c| c.contains("zpool create"))
            .unwrap_or_else(|| panic!("no zpool create in {:?}", b.stage(Stage::Assemble).iter().map(commands::Cmd::shell).collect::<Vec<_>>()));
        assert!(create.contains(" -d "), "{create}");
        for f in ["async_destroy", "empty_bpobj", "lz4_compress", "extensible_dataset"] {
            assert!(create.contains(&format!("-o feature@{f}=enabled")), "{f}: {create}");
        }
        for f in ["multi_vdev_crash_dump", "large_dnode", "encryption"] {
            assert!(!create.contains(&format!("feature@{f}")), "{f}: {create}");
        }
        assert!(create.contains("-o compatibility=grub2"), "{create}");
        assert!(create.contains("-o ashift=12") && create.contains("-o autotrim=on"), "{create}");
        // cachefile is load's own (POOL_PROPS_SKIP), not the source's.
        assert!(create.contains("-o cachefile=none") && !create.contains("/etc/zfs/zpool.cache"), "{create}");
        assert!(create.contains("bpool /dev/vdb1"), "{create}");
    }

    /// A filesystem bigger than its partition is refused, exit 4, by plan
    /// and by the writing stages, with --fit-largest too: no mke2fs of a
    /// size that can't fit.
    #[test]
    fn a_filesystem_bigger_than_its_partition_is_refused() {
        let t = tree();
        let runner = FakeRunner::default().with("/usr/sbin/rustic-bko --version", "rustic 0.11.4+backotter.4\n");
        let s = Sys::new(t.root(), Box::new(runner), true);
        // vda1 is 244224 4 KiB blocks: its ext4 one block more.
        let over = Layout::from_json(crate::json::parse(&sample::bios_mbr().replace("\"blocks\": 244224", "\"blocks\": 244225")).unwrap()).unwrap();
        let why = "vda1: the dump's ext4 is 954 MiB (1000345600 bytes), bigger than vda1 itself, 954 MiB (1000341504 bytes): the dump is inconsistent";
        for a in [&["--map", "vda=vdb", "--repo", "/srv/r"][..], &["--map", "vda=vdb", "--repo", "/srv/r", "--fit-largest", "10"]] {
            let (code, out, err) = run(&over, &opts(a), &s);
            assert_eq!(code, EXIT_PLAN_PROBLEMS, "{a:?}: {out}");
            assert!(out.contains(why), "{a:?}: {out}");
            assert!(err.contains("problem"), "{err}");
        }
        let (code, _, err) = crate::load::exec::run(&over, &opts(&["--map", "vda=vdb", "--through", "format", "--commit"]), &s, &[Stage::Partition, Stage::Assemble, Stage::Format]);
        assert_eq!(code, EXIT_PLAN_PROBLEMS);
        assert!(err.contains(why) && err.contains("nothing was written"), "{err}");
        // The same filesystem exactly its partition's size is planned.
        let (code, out, err) = run(&layout(), &opts(&["--map", "vda=vdb", "--repo", "/srv/r"]), &s);
        assert_eq!(code, EXIT_OK, "{out}{err}");
    }

    /// The codes check() gives its own problems, with their fields, and the
    /// mapping's and the builders' carried through in order.
    #[test]
    fn problems_carry_their_codes() {
        use crate::load::codes::ProblemCode as C;
        let t = tree();
        let field = |p: &Problem, k: &str| p.field(k).map(crate::json::Value::compact).unwrap_or_default();
        // Not root; the archiver answers nothing; no target.
        let s = Sys::new(t.root(), Box::new(FakeRunner::default()), false);
        let p = check(&layout(), &opts(&["--repo", "/srv/r"]), &s);
        let find = |c: C| p.problems.iter().position(|x| x.code == c).unwrap_or_else(|| panic!("no {c:?} in {:?}", p.problems));
        // In check()'s order: the mapping's, the archiver's, then not root.
        assert!(find(C::NotMapped) < find(C::ArchiverNotBko) && find(C::ArchiverNotBko) < find(C::NotRoot));
        assert_eq!(find(C::NotRoot), p.problems.len() - 1);
        let a = &p.problems[find(C::ArchiverNotBko)];
        assert_eq!(a.text, "/usr/sbin/rustic-bko --version failed; restore can't run it");
        assert_eq!((field(a, "program"), field(a, "version")), ("\"rustic-bko\"".to_string(), "null".to_string()));
        assert!(p.problems[find(C::NotRoot)].starts_with("not running as root:"));
        // Another archiver's, or none at all.
        let runner = FakeRunner::default().with("/usr/sbin/rustic-bko --version", "rustic 0.11.4\n");
        let s = Sys::new(t.root(), Box::new(runner), true);
        let p = check(&layout(), &opts(&["--map", "vda=vdb", "--repo", "/srv/r"]), &s);
        let a = p.problems.iter().find(|x| x.code == C::ArchiverNotBko).unwrap();
        assert_eq!(a.text, "rustic-bko isn't BackOtter's rustic (rustic 0.11.4); restore needs rustic-bko");
        assert_eq!(field(a, "version"), "\"rustic 0.11.4\"");
        let p = check(&layout(), &opts(&["--map", "vda=vdb", "--repo", "/srv/r", "--archiver", "restic-x"]), &s);
        let a = p.problems.iter().find(|x| x.code == C::ArchiverMissing).unwrap();
        assert_eq!((a.text.as_str(), a.str_field("program")), ("restic-x isn't installed here; restore can't run it", Some("restic-x")));
        // A tool missing.
        std::fs::remove_file(t.p("/usr/sbin/sfdisk")).unwrap();
        let p = check(&layout(), &opts(&["--map", "vda=vdb"]), &s);
        let m = p.problems.iter().find(|x| x.code == C::ToolMissing).unwrap();
        assert_eq!((m.text.as_str(), m.str_field("program"), m.str_field("stage")), ("sfdisk isn't installed here (the partition stage runs it)", Some("sfdisk"), Some("partition")));
        t.file("/usr/sbin/sfdisk", "");
        // A bug report's layout.
        let omitted = Layout::from_json(crate::json::parse(&sample::bios_mbr().replacen("\"storage\": {", "\"storage\": {\"esp_image_omitted\": 1, ", 1)).unwrap()).unwrap();
        let p = check(&omitted, &opts(&["--map", "vda=vdb"]), &s);
        assert!(p.problems.iter().any(|x| x.code == C::FromBugReport && x.starts_with("this layout comes from a bug report")), "{:?}", p.problems);
        // Fields as the documents carry them.
        let j = p.problems.iter().find(|x| x.code == C::FromBugReport).unwrap().to_json();
        assert_eq!(j.get_str("code"), Some("from_bug_report"));
    }

    #[test]
    fn lvm_thin_and_zfs_codes() {
        use crate::load::codes::ProblemCode as C;
        let t = tree();
        // lvm2 older than 2.03.12, for a layout with a volume group.
        let vg = Layout::from_json(crate::json::parse(&sample::bios_mbr().replace(r#""lvm": {"volume_groups": []}"#, r#""lvm": {"volume_groups": [{"name": "vg0"}]}"#)).unwrap()).unwrap();
        let s = Sys::new(t.root(), Box::new(FakeRunner::default().with("lvm version", "  LVM version:     2.03.11(2) (2021-01-08)\n")), true);
        let p = lvm_problem(&s, &vg).unwrap();
        assert_eq!((p.code, p.text.as_str(), p.str_field("version")), (C::LvmTooOld, "lvm2 2.03.11 is older than 2.03.12, which load's LVM commands need (--devices)", Some("2.03.11")));
        // Thin pools: the three reasons.
        let vgs = format!(r#""lvm": {{"volume_groups": [{}]}}"#, crate::probe::lvm::sample::thin_vg().compact());
        let thin = Layout::from_json(crate::json::parse(&sample::bios_mbr().replace(r#""lvm": {"volume_groups": []}"#, &vgs)).unwrap()).unwrap();
        t.file("/proc/sys/kernel/osrelease", "7.0.0-43-generic\n");
        let s = Sys::new(t.root(), Box::new(FakeRunner::default().with("lvm segtypes", "striped\n")), true);
        let p = thin_problems(&s, &thin);
        assert_eq!(p.iter().map(|x| (x.code, x.str_field("why").unwrap_or(""))).collect::<Vec<_>>(), [(C::ThinUnsupported, "lvm_segtype"), (C::ThinUnsupported, "thin_check"), (C::ThinUnsupported, "dm_thin_pool")]);
        // ZFS: a feature unknown here, a compatibility file missing.
        let s = zfs_sys(&t);
        let p = zfs_problems(&s, &zfs_dump("{\"blake3\": \"active\"}", "{\"compatibility\": \"mycustom\"}"));
        assert_eq!(p.iter().map(|x| x.code).collect::<Vec<_>>(), [C::ZfsFeatureUnknown, C::ZfsCompatMissing], "{p:?}");
        assert_eq!((p[0].str_field("pool"), p[0].field("features").map(crate::json::Value::compact).as_deref()), (Some("rpool"), Some(r#"["blake3"]"#)));
        assert_eq!((p[1].str_field("pool"), p[1].str_field("file")), (Some("rpool"), Some("mycustom")));
        let p = zfs_problems(&s, &zfs_dump("{}", "{\"compatibility\": \"/etc/zfs/theirs\"}"));
        assert_eq!((p[0].code, p[0].str_field("file")), (C::ZfsCompatMissing, Some("/etc/zfs/theirs")));
    }

    #[test]
    fn problems_give_exit_4() {
        let t = tree();
        let s = Sys::new(t.root(), Box::new(FakeRunner::default()), true);
        let (code, out, err) = run(&layout(), &opts(&["--repo", "/srv/r"]), &s);
        assert_eq!(code, EXIT_PLAN_PROBLEMS);
        assert!(out.contains("has no target"), "{out}");
        assert!(out.contains("/usr/sbin/rustic-bko --version failed"), "{out}");
        assert!(err.contains("problem"));
        std::fs::remove_file(t.p("/usr/sbin/sfdisk")).unwrap();
        let (_, out, _) = run(&layout(), &opts(&["--map", "vda=vdb"]), &s);
        assert!(out.contains("sfdisk isn't installed here (the partition stage runs it)"), "{out}");
        assert!(out.contains("no --repo"), "{out}");
    }

    /// The `writes` heuristic's names: a disk's own device and its
    /// partitions or slices, never another disk's whose name starts alike.
    #[test]
    fn names_node_partitions_only() {
        for (arg, node) in [
            ("/dev/sda", "sda"),
            ("/dev/sda1", "sda"),
            ("/dev/nvme0n1", "nvme0n1"),
            ("/dev/nvme0n1p3", "nvme0n1"),
            ("/dev/vtbd0p3", "vtbd0"),
            ("/dev/vtbd0p3.eli", "vtbd0"),
            ("/dev/ada0s1a", "ada0"),
            ("/dev/sd0a", "sd0"),
            ("/dev/sd0p", "sd0"),
            ("/dev/rsd0c", "sd0"),
            ("/dev/dsk/c1t0d0s0", "c1t0d0"),
            ("/dev/rdsk/c1t0d0p0", "c1t0d0"),
            ("/dev/dsk/sdqs1", "sdq"),
            ("/dev/md127p1", "md127"),
        ] {
            assert!(names_node(arg, node), "{arg} is {node}'s");
        }
        for (arg, node) in [
            ("/dev/sdaa", "sda"),
            ("/dev/sdaa1", "sda"),
            ("/dev/sdbb1", "sdb"),
            ("/dev/nvme0n10", "nvme0n1"),
            ("/dev/nvme0n10p1", "nvme0n1"),
            ("/dev/sd10a", "sd1"),
            ("/dev/mmcblk0boot0", "mmcblk0"),
            ("/dev/sdb1", "sda"),
            ("/dev/dsk/c1t0d01s0", "c1t0d0"),
            ("/dev/dsk/c1t0d0", "c1t0"),
            ("sda1", "sda"),
        ] {
            assert!(!names_node(arg, node), "{arg} isn't {node}'s");
        }
    }

    /// A password in --repo's URL: refused, and replaced wherever the
    /// --json run would show or record it.
    #[test]
    fn repo_password_redacted() {
        for (r, want) in [
            ("rest:https://me:pw@nas:8000/r", "rest:https://me:REDACTED@nas:8000/r"),
            ("sftp://u:p:q@h/x", "sftp://u:REDACTED@h/x"),
            ("sftp://u:p@ss@h", "sftp://u:REDACTED@h"),
            ("rest:https://u@h/", "rest:https://u@h/"),
            ("/srv/r", "/srv/r"),
            ("sftp:u@h:/x", "sftp:u@h:/x"),
        ] {
            assert_eq!(repo_redacted(r), want, "{r}");
            assert_eq!(repo_has_password(&repo_redacted(r)), repo_has_password(r), "{r}: still refused");
        }
        let a: Vec<String> = ["l", "--repo", "rest:https://me:pw@nas/r", "--json"].iter().map(|x| x.to_string()).collect();
        assert_eq!(args_redacted(&a), ["l", "--repo", "rest:https://me:REDACTED@nas/r", "--json"]);
    }
}
