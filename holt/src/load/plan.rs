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
}

/// Every check `plan` makes; the writing stages make them again first.
pub fn check(layout: &Layout, opts: &Options, sys: &Sys) -> Planned {
    let system = target::scan(sys, opts, &target::layout_pools(layout));
    let mapping = target::map(layout, opts, &system, sys);
    let built = commands::build(layout, opts, &mapping);
    let mut problems = mapping.problems.clone();
    problems.extend(built.problems.iter().cloned());
    problems.extend(archiver_problem(sys, opts));
    problems.extend(missing_tools(sys, &built));
    if super::has_omitted(&layout.v) {
        problems.push("this layout comes from a bug report (`bko-holt report-bug`), which leaves boot-area and ESP images out: restore from the dump itself".into());
    }
    if !sys.privileged {
        problems.push("not running as root: the target disks' tables and signatures can't be read, so blank disks can't be told from used ones".into());
    }
    let mut notes = mapping.notes.clone();
    notes.extend(built.notes.iter().cloned());
    Planned { mapping, built, problems, notes }
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

    const TOOLS: [&str; 8] = ["wipefs", "sfdisk", "udevadm", "mke2fs", "tune2fs", "mkswap", "mkfs.btrfs", "btrfs"];

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
