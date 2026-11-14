//! `bko-holt load` with no stage: every check, and every command the writing
//! stages would run, without writing anything.

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
fn missing_tools(sys: &Sys, built: &Built) -> Vec<String> {
    let mut out: Vec<String> = Vec::new();
    for (stage, cmds) in &built.stages {
        for c in cmds.iter().filter(|c| !c.argv.is_empty()) {
            let prog = &c.argv[0];
            if !on_path(sys, prog) && !out.iter().any(|m| m.starts_with(&format!("{prog} "))) {
                out.push(format!("{prog} isn't installed here (the {} stage runs it)", stage.name()));
            }
        }
    }
    out
}

/// load's LVM commands take --devices and --devicesfile, which lvm2 has had
/// since 2.03.12: an older one is a problem for a layout with volume groups.
/// (An lvm that won't say its version is left to missing_tools.)
fn lvm_problem(sys: &Sys, layout: &Layout) -> Option<String> {
    if layout.v.at(&["storage", "lvm", "volume_groups"]).and_then(crate::json::Value::as_array).is_none_or(|a| a.is_empty()) {
        return None;
    }
    let out = sys.run("lvm", &["version"])?;
    let v = lvm_version(&out)?;
    (v < (2, 3, 12)).then(|| format!("lvm2 {}.{:02}.{:02} is older than 2.03.12, which load's LVM commands need (--devices)", v.0, v.1, v.2))
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
fn zfs_problems(sys: &Sys, layout: &Layout) -> Vec<String> {
    let mut out: Vec<String> = Vec::new();
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
                out.push(format!(
                    "ZFS pool {name}: this system's ZFS doesn't know the source's feature(s) {}, which zpool create would be asked to enable; the rescue image's ZFS is older than the source's, so a newer image is needed",
                    unknown.join(", ")
                ));
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
                    out.push(format!("ZFS pool {name}: its compatibility={value} names {f}, which isn't on this system; a rescue image that has it is needed"));
                }
            } else if !COMPAT_DIRS.iter().any(|d| sys.is_file(&format!("{d}/{f}"))) {
                out.push(format!("ZFS pool {name}: its compatibility={value} names {f}, which is in none of {}; a rescue image that has it is needed", COMPAT_DIRS.join(", ")));
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
fn archiver_problem(sys: &Sys, opts: &Options) -> Option<String> {
    opts.repo.as_ref()?;
    let Some(exe) = find(sys, &opts.archiver) else {
        return Some(format!("{} isn't installed here; restore can't run it", opts.archiver));
    };
    match sys.run_file(&exe, &["--version"]) {
        Some(v) if v.contains("backotter") => None,
        Some(v) => Some(format!("{} isn't BackOtter's rustic ({}); restore needs rustic-bko", opts.archiver, v.trim())),
        None => Some(format!("{exe} --version failed; restore can't run it")),
    }
}

pub struct Planned {
    pub mapping: Mapping,
    pub built: Built,
    pub problems: Vec<String>,
    pub notes: Vec<String>,
    /// What --fit-largest changed, if anything (fit.rs).
    pub fit: super::fit::Fit,
}

/// Every check `plan` makes; the writing stages make them again first.
pub fn check(layout: &Layout, opts: &Options, sys: &Sys) -> Planned {
    let target::Prepared { mapping, layout, fit, .. } = target::prepare(layout, opts, sys);
    let layout = &*layout;
    let built = commands::build(layout, opts, &mapping);
    let mut problems = mapping.problems.clone();
    problems.extend(built.problems.iter().cloned());
    problems.extend(archiver_problem(sys, opts));
    problems.extend(missing_tools(sys, &built));
    problems.extend(lvm_problem(sys, layout));
    problems.extend(zfs_problems(sys, layout));
    if super::has_omitted(&layout.v) {
        problems.push("this layout comes from a bug report (`bko-holt report-bug`), which leaves boot-area and ESP images out: restore from the dump itself".into());
    }
    if !sys.privileged {
        problems.push("not running as root: the target disks' tables and signatures can't be read, so blank disks can't be told from used ones".into());
    }
    let mut notes = mapping.notes.clone();
    notes.extend(built.notes.iter().cloned());
    Planned { mapping, built, problems, notes, fit }
}

pub fn run(layout: &Layout, opts: &Options, sys: &Sys) -> (u8, String, String) {
    let p = check(layout, opts, sys);
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
            out += &c.shell();
        }
    }
    out += "\n# Nothing was written.";
    if p.problems.is_empty() {
        let through = if opts.repo.is_some() { Stage::Close } else { Stage::Mount };
        out += &format!("  To run it: bko-holt load {} ... --through {} --commit\n", opts.layout, through.name());
    } else {
        out.push('\n');
    }
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
}
