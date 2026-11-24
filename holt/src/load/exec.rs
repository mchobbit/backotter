//! Running the writing stages: the checks `plan` makes, then each stage's
//! commands, then a read-back of the disks to see the stage took.
//!
//! Without --commit a stage prints its commands and writes nothing.  Every
//! command and its output goes to RUN_DIR/log/load.log, which `boot` copies
//! into the restored system's /var/log/bko-holt/.

use super::commands::Cmd;
use super::layout::Layout;
use super::status::{self, State};
use super::{plan, Options, Stage, EXIT_FAILED, EXIT_OK, EXIT_PLAN_PROBLEMS, RUN_DIR};
use crate::sys::Sys;
use std::io::Write;
use std::process::{Command, Stdio};

fn log_line(log: &mut Option<std::fs::File>, text: &str) {
    if let Some(f) = log {
        let _ = f.write_all(text.as_bytes());
        if !text.ends_with('\n') {
            let _ = f.write_all(b"\n");
        }
    }
}

/// Runs one step for real.  A command's output goes to the log (and, for
/// the restore's archiver, the terminal sees nothing until it's done).  It
/// gives back, for a command that `warns`, its stderr's `warning:` lines,
/// for the terminal.  A read-back runs here, with SYS's probe.
fn run_cmd(c: &Cmd, log: &mut Option<std::fs::File>, sys: &Sys) -> Result<Vec<String>, String> {
    log_line(log, &c.shell());
    if c.is_note() {
        return Ok(Vec::new());
    }
    if let Some((dev, content)) = &c.check {
        super::readback::check(sys, dev, content)?;
        log_line(log, &format!("{dev}: read back as the source's"));
        return Ok(Vec::new());
    }
    if let Some(path) = &c.write {
        return write_file(path, c.stdin.as_deref().unwrap_or(""), c.private).map(|()| Vec::new());
    }
    let mut cmd = Command::new(&c.argv[0]);
    cmd.args(&c.argv[1..]).env("LC_ALL", "C").envs(c.env.iter().map(|(k, v)| (k, v))).stdout(Stdio::piped()).stderr(Stdio::piped());
    // No stdin given: the terminal's, so cryptsetup can ask for a passphrase.
    cmd.stdin(if c.stdin.is_some() { Stdio::piped() } else { Stdio::inherit() });
    let mut child = cmd.spawn().map_err(|e| format!("{}: {e}", c.argv[0]))?;
    if let (Some(input), Some(mut pipe)) = (&c.stdin, child.stdin.take()) {
        pipe.write_all(input.as_bytes()).map_err(|e| format!("{}: writing its input: {e}", c.argv[0]))?;
    }
    let out = child.wait_with_output().map_err(|e| format!("{}: {e}", c.argv[0]))?;
    log_line(log, &String::from_utf8_lossy(&out.stdout));
    log_line(log, &String::from_utf8_lossy(&out.stderr));
    if out.status.success() {
        let err = String::from_utf8_lossy(&out.stderr);
        Ok(if c.warns { err.lines().filter(|l| l.contains("warning:")).map(str::to_string).collect() } else { Vec::new() })
    } else {
        let err = String::from_utf8_lossy(&out.stderr);
        let tail: Vec<&str> = err.lines().rev().take(5).collect::<Vec<_>>().into_iter().rev().collect();
        Err(format!("{} failed ({}){}", c.argv[0], out.status, if tail.is_empty() { String::new() } else { format!(":\n    {}", tail.join("\n    ")) }))
    }
}

fn state_of(layout: &Layout, opts: &Options, sys: &Sys, s: Stage) -> (State, String) {
    let (rows, _) = status::rows(layout, opts, sys);
    rows.into_iter().find(|r| r.0 == s).map(|r| (r.1, r.2)).unwrap_or((State::Unknown, String::new()))
}

/// What runs one step: run_cmd, or a test's stand-in.
type Runner<'a> = &'a dyn Fn(&Cmd, &mut Option<std::fs::File>, &Sys) -> Result<Vec<String>, String>;

pub fn run(layout: &Layout, opts: &Options, sys: &Sys, stages: &[Stage]) -> (u8, String, String) {
    run_with(layout, opts, sys, stages, &run_cmd)
}

/// run, each step run by STEP.  RUN_DIR's own files (the log, boot.done,
/// the copy marks, the partition records) are SYS's: with --commit SYS is
/// the host's (--sysroot is refused), so they are RUN_DIR's; in a test,
/// the test's tree.
fn run_with(layout: &Layout, opts: &Options, sys: &Sys, stages: &[Stage], step: Runner) -> (u8, String, String) {
    let p = plan::check(layout, opts, sys);
    let mut out = String::new();
    let mut err = String::new();
    if !p.problems.is_empty() {
        for x in &p.problems {
            err += &format!("bko-holt load: {x}\n");
        }
        err += "bko-holt load: nothing was written; `bko-holt load LAYOUT ...` without a stage shows the whole plan\n";
        return (EXIT_PLAN_PROBLEMS, out, err);
    }
    if opts.commit && (opts.sysroot.is_some() || !sys.privileged) {
        let why = if opts.sysroot.is_some() { "with --sysroot" } else { "except as root" };
        return (EXIT_FAILED, out, format!("bko-holt load: --commit can't write {why}\n"));
    }
    let mut log = None;
    if opts.commit {
        use std::os::unix::fs::{OpenOptionsExt, PermissionsExt};
        let _ = mkdir_private(&sys.p(RUN_DIR));
        let _ = mkdir_private(&sys.p(&format!("{RUN_DIR}/log")));
        let log_path = sys.p(&format!("{RUN_DIR}/log/load.log"));
        log = std::fs::OpenOptions::new().create(true).append(true).mode(0o600).open(&log_path).ok();
        let _ = std::fs::set_permissions(&log_path, std::fs::Permissions::from_mode(0o600));
        log_line(&mut log, &format!("==== bko-holt load {} {:?}", env!("CARGO_PKG_VERSION"), std::env::args().collect::<Vec<_>>()));
    }
    // Once the partition stage runs on disks named with --erase, no later
    // stage is skipped as done: what status would read before them is what
    // was on the disks before the wipe (in a dry run, the old system's
    // filesystems and files, still there).  A stage the layout needs
    // nothing from is still skipped, and without --erase nothing changes.
    let mut wiped = false;
    // The copy marks' text (status::restore_mark_text), and the options
    // the restore's read-back reads the tree with.
    let mark = status::restore_mark_text(layout, &p.mapping);
    let mut tree_opts = opts.clone();
    tree_opts.json = false;
    for &s in stages {
        // --through skips what's done already; --only does what it's told.
        if opts.through.is_some() {
            let (st, _) = state_of(layout, opts, sys, s);
            if st == State::Nothing || (st == State::Done && !wiped) {
                out += &format!("# ---- {}: {} already\n", s.name(), st_word(st));
                continue;
            }
        }
        let marker = crate::other::restored_marker(&layout.v);
        if s == Stage::Boot && !sys.exists(&format!("{}/{marker}", opts.target)) {
            return (EXIT_FAILED, out, format!("bko-holt load: boot: {}/{marker} isn't there; restore the system into {} first\n", opts.target, opts.target));
        }
        wiped |= s == Stage::Partition && !opts.erase.is_empty();
        let cmds = p.built.stage(s);
        out += &format!("# ---- {} ----\n", s.name());
        if !opts.commit {
            for c in cmds {
                out += &c.shell();
            }
            continue;
        }
        log_line(&mut log, &format!("==== stage {}", s.name()));
        // A new tree needs its own boot stage.
        if s == Stage::Restore {
            let _ = std::fs::remove_file(sys.p(&status::boot_marker()));
            // The copy begins: not complete until the stage's end (J10).
            let _ = std::fs::remove_file(sys.p(&status::restore_done_marker()));
            if let Err(e) = write_mark(sys, &status::restore_started_marker(), &mark) {
                if opts.json {
                    log_line(&mut log, &format!("FAILED: {e}"));
                    return (EXIT_FAILED, out, format!("bko-holt load: restore: {e}\n"));
                }
            }
        }
        // A table load wrote is recorded only once the stage has run: until
        // then, with --erase, a rerun takes the disk as it finds it.
        if s == Stage::Partition {
            for (path, _) in &p.mapping.records {
                let _ = std::fs::remove_file(sys.p(path));
            }
            // The disks are new again: no copy has begun on them, and
            // under --json no boot stage has run for them either.
            for m in [status::restore_started_marker(), status::restore_done_marker()] {
                let _ = std::fs::remove_file(sys.p(&m));
            }
            if opts.json {
                let _ = std::fs::remove_file(sys.p(&status::boot_marker()));
            }
        }
        for c in cmds {
            match step(c, &mut log, sys) {
                Ok(warnings) => {
                    for w in warnings {
                        err += &format!("bko-holt load: {}: {w}\n", s.name());
                    }
                }
                Err(e) => {
                    log_line(&mut log, &format!("FAILED: {e}"));
                    err += &format!("bko-holt load: {}: {e}\n", s.name());
                    err += &format!("bko-holt load: the log is {RUN_DIR}/log/load.log\n");
                    return (EXIT_FAILED, out, err);
                }
            }
        }
        if s == Stage::Partition {
            if let Err(e) = write_records(sys, &p.mapping.records) {
                log_line(&mut log, &format!("FAILED: {e}"));
                return (EXIT_FAILED, out, format!("bko-holt load: partition: {e}\n"));
            }
        }
        if s == Stage::Boot {
            if let Err(e) = std::fs::write(sys.p(&status::boot_marker()), "") {
                log_line(&mut log, &format!("FAILED: {e}"));
                return (EXIT_FAILED, out, format!("bko-holt load: boot: {}: {e}\n", status::boot_marker()));
            }
        }
        // Read the disks back: the stage must show as done.  The restore's
        // by the tree alone, as text mode reads it: under --json its row
        // needs the mark written next.
        let (st, why) = if s == Stage::Restore && opts.json { state_of(layout, &tree_opts, sys, s) } else { state_of(layout, opts, sys, s) };
        if !matches!(st, State::Done | State::Nothing) {
            let msg = format!("{} ran, but the disks don't show it done: {}{}", s.name(), st_word(st), if why.is_empty() { String::new() } else { format!(" ({why})") });
            log_line(&mut log, &format!("FAILED: {msg}"));
            return (EXIT_FAILED, out, format!("bko-holt load: {msg}\n"));
        }
        // The copy is complete: after the stage's last command (the shared
        // snapshots' flags, thin_restore, the atimes' capture all come
        // after the archiver) and its read-back (J10).
        if s == Stage::Restore {
            if let Err(e) = write_mark(sys, &status::restore_done_marker(), &mark) {
                if opts.json {
                    log_line(&mut log, &format!("FAILED: {e}"));
                    return (EXIT_FAILED, out, format!("bko-holt load: restore: {e}\n"));
                }
            }
        }
        out += &format!("# {} done\n", s.name());
        log_line(&mut log, &format!("==== stage {} done", s.name()));
    }
    if !opts.commit {
        out += "\n# Nothing was written: add --commit to run these.\n";
    }
    (EXIT_OK, out, err)
}

fn mkdir_private(dir: &std::path::Path) -> std::io::Result<()> {
    use std::os::unix::fs::{DirBuilderExt, PermissionsExt};
    std::fs::DirBuilder::new().recursive(true).mode(0o700).create(dir)?;
    if dir.starts_with(RUN_DIR) {
        let _ = std::fs::set_permissions(dir, std::fs::Permissions::from_mode(0o700));
    }
    Ok(())
}

fn write_private(path: &str, text: &str) -> std::io::Result<()> {
    use std::os::unix::fs::{OpenOptionsExt, PermissionsExt};
    let mut f = std::fs::OpenOptions::new().write(true).create(true).truncate(true).mode(0o600).open(path)?;
    let _ = f.set_permissions(std::fs::Permissions::from_mode(0o600));
    f.write_all(text.as_bytes())
}

/// A file a step writes.  Under RUN_DIR (bko-holt's own: keys, LVM
/// metadata, lists) it's private, 0600 in 0700 directories.  Anywhere else
/// it's the restored system's (Proxmox's /etc/kernel/proxmox-boot-uuids):
/// written as before, an existing file keeping its mode and owner, a new
/// one and its directories made under the umask; but one the step says is
/// PRIVATE (LILO's copy of lilo.conf, which can hold password=) is 0600,
/// in directories made under the umask.
fn write_file(path: &str, text: &str, private: bool) -> Result<(), String> {
    let p = std::path::Path::new(path);
    let ours = p.starts_with(RUN_DIR);
    if let Some(dir) = p.parent() {
        let made = if ours { mkdir_private(dir) } else { std::fs::create_dir_all(dir) };
        made.map_err(|e| format!("{}: {e}", dir.display()))?;
    }
    let written = if ours || private { write_private(path, text) } else { std::fs::write(path, text) };
    written.map_err(|e| format!("{path}: {e}"))
}

/// A copy mark (status::restore_started_marker) at PATH, through SYS (the
/// host's RUN_DIR, or a test's tree).
fn write_mark(sys: &Sys, path: &str, text: &str) -> Result<(), String> {
    write_file(&sys.p(path).to_string_lossy(), text, false)
}

/// The partition stage's record of each table it wrote (target::table_record),
/// through SYS as write_mark.
fn write_records(sys: &Sys, records: &[(String, String)]) -> Result<(), String> {
    for (path, text) in records {
        write_file(&sys.p(path).to_string_lossy(), text, false)?;
    }
    Ok(())
}

fn st_word(s: State) -> &'static str {
    match s {
        State::Done => "done",
        State::Nothing => "nothing to do",
        State::Partly => "partly done",
        State::NotDone => "not done",
        State::Unknown => "can't tell",
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// A file written into the restored system (Proxmox's
    /// /etc/kernel/proxmox-boot-uuids, rewritten after proxmox-boot-tool
    /// init) keeps the mode it has; only RUN_DIR's own files are made 0600.
    #[test]
    fn write_file_keeps_a_target_files_mode() {
        use std::os::unix::fs::PermissionsExt;
        let t = crate::testutil::Tree::new("load-exec-write-mode");
        let f = t.root().join("etc/kernel/proxmox-boot-uuids");
        t.file("/etc/kernel/proxmox-boot-uuids", "OLD\n");
        std::fs::set_permissions(&f, std::fs::Permissions::from_mode(0o644)).unwrap();
        let c = Cmd::write_file(&f.to_string_lossy(), "1234-ABCD\n".to_string());
        assert_eq!(run_cmd(&c, &mut None, &Sys::host()), Ok(Vec::new()));
        assert_eq!(std::fs::read_to_string(&f).unwrap(), "1234-ABCD\n");
        assert_eq!(std::fs::metadata(&f).unwrap().permissions().mode() & 0o7777, 0o644);
        // A new one, in a directory that isn't there yet, is made too.
        let n = t.root().join("etc/new/dir/file");
        assert_eq!(run_cmd(&Cmd::write_file(&n.to_string_lossy(), "x".to_string()), &mut None, &Sys::host()), Ok(Vec::new()));
        assert_eq!(std::fs::read_to_string(&n).unwrap(), "x");
        // One the step says is private (LILO's copy of lilo.conf): 0600,
        // new or not.
        let l = t.root().join("etc/lilo.conf.bko-holt");
        assert_eq!(run_cmd(&Cmd::write_file(&l.to_string_lossy(), "password=x\n".to_string()).private(), &mut None, &Sys::host()), Ok(Vec::new()));
        assert_eq!(std::fs::metadata(&l).unwrap().permissions().mode() & 0o7777, 0o600);
        std::fs::set_permissions(&l, std::fs::Permissions::from_mode(0o644)).unwrap();
        assert_eq!(run_cmd(&Cmd::write_file(&l.to_string_lossy(), "password=y\n".to_string()).private(), &mut None, &Sys::host()), Ok(Vec::new()));
        assert_eq!(std::fs::read_to_string(&l).unwrap(), "password=y\n");
        assert_eq!(std::fs::metadata(&l).unwrap().permissions().mode() & 0o7777, 0o600);
    }

    /// A command that `warns` gives back its stderr's warning lines when it
    /// succeeds; one that doesn't gives back nothing; a failure ends with
    /// its stderr's last lines.
    #[test]
    fn run_cmd_warnings() {
        let s = Sys::host();
        let script = "echo 'x: warning: one' >&2; echo other >&2; echo 'x: warning: two' >&2";
        let c = Cmd::new(&["sh", "-c", script]);
        assert_eq!(run_cmd(&c, &mut None, &s), Ok(Vec::new()));
        assert_eq!(run_cmd(&c.clone().warns(), &mut None, &s), Ok(vec!["x: warning: one".to_string(), "x: warning: two".to_string()]));
        let e = run_cmd(&Cmd::new(&["sh", "-c", &format!("{script}; echo 'x: 1 failed' >&2; exit 1")]).warns(), &mut None, &s).unwrap_err();
        assert!(e.ends_with("x: warning: two\n    x: 1 failed"), "{e}");
    }

    /// A read-back runs in-process: the device as the source's passes, and
    /// a feature the source didn't have stops it, by name.
    #[test]
    fn run_cmd_reads_back() {
        use crate::sys::FakeRunner;
        let t = crate::testutil::Tree::new("load-exec-readback");
        let img = crate::probe::ext::sample::image(0x0004, 0x0002 | 0x0040, 0x0001, "root");
        t.file("/dev/vdb1", &img);
        let s = Sys::new(t.root(), Box::new(FakeRunner::default()), true);
        let src = crate::probe::probe_all(&crate::bytes::MemSource(img)).remove(0).to_json();
        let c = Cmd::read_back("/dev/vdb1", &src);
        assert_eq!(run_cmd(&c, &mut None, &s), Ok(Vec::new()));
        t.file("/dev/vdb1", crate::probe::ext::sample::image(0x0004 | 0x1000, 0x0002 | 0x0040, 0x0001, "root"));
        assert_eq!(
            run_cmd(&c, &mut None, &s),
            Err("/dev/vdb1: made with orphan_file, which the source's ext4 didn't have: the rescue image's mkfs turned it on".to_string())
        );
    }

    /// --through on the source's own disk as found, its table the source's
    /// and named with --erase: with no record that load wrote the table the
    /// partition stage runs, and every stage after it (the mount stage too,
    /// though something is mounted where it mounts); with the record, the
    /// stages status shows done are skipped, as before.
    #[test]
    fn through_runs_the_partition_stage_on_a_table_load_did_not_write() {
        use crate::load::layout::sample;
        use crate::load::{parse_args, target, Parsed};
        use crate::sys::FakeRunner;
        use crate::testutil::Tree;
        let t = Tree::new("load-exec-through");
        let mut table = vec![0u8; 512];
        table[440..444].copy_from_slice(&0x1f2e_3d4cu32.to_le_bytes());
        for (slot, start, n) in [(0, 2048, 1953792), (1, 1955840, 3905536), (2, 5861376, 27693056)] {
            crate::parttable::build::mbr_entry(&mut table, slot, 0, 0x83, start, n);
        }
        t.disk("vdb", "253:16", &table, 512).file("/sys/block/vdb/size", "33554432\n");
        t.file("/proc/self/mountinfo", "36 35 0:50 / /mnt/bko-target rw - tmpfs tmpfs rw\n37 36 0:51 / /mnt/bko-target/boot rw - tmpfs tmpfs rw\n");
        t.file("/proc/swaps", "Filename\tType\tSize\tUsed\tPriority\n");
        for p in ["wipefs", "dd", "sfdisk", "udevadm", "mke2fs", "tune2fs", "mkswap", "mkfs.btrfs", "btrfs", "mkdir", "mount", "umount", "chroot", "cp", "sh", "bash"] {
            t.file(&format!("/usr/sbin/{p}"), "");
        }
        let s = Sys::new(t.root(), Box::new(FakeRunner::default()), true);
        let l = Layout::from_json(crate::json::parse(&sample::bios_mbr()).unwrap()).unwrap();
        let o = match parse_args(&["l.json".into(), "--map".into(), "vda=vdb".into(), "--erase".into(), "/dev/vdb".into(), "--through".into(), "mount".into()]).unwrap() {
            Parsed::Run(o) => o,
            _ => unreachable!(),
        };
        let stages = [Stage::Partition, Stage::Assemble, Stage::Format, Stage::Mount];
        let (code, out, err) = run(&l, &o, &s, &stages);
        assert_eq!(code, EXIT_OK, "{out}{err}");
        assert!(out.contains("# ---- partition ----\n# vda -> /dev/vdb: clear the old table and signatures\nwipefs --all /dev/vdb\n"), "{out}");
        assert!(out.contains("# ---- mount ----\n"), "{out}");
        assert!(!out.contains("done already"), "{out}");
        assert!(out.contains("# ---- assemble: nothing to do already\n"), "{out}");

        // The record this session's partition stage writes.
        let p = target::prepare(&l, &o, &s);
        assert_eq!(p.mapping.records.len(), 1);
        let (path, text) = &p.mapping.records[0];
        t.file(path, text.as_str());
        let (code, out, err) = run(&l, &o, &s, &stages);
        assert_eq!(code, EXIT_OK, "{out}{err}");
        assert!(out.contains("# ---- partition: done already\n"), "{out}");
        assert!(out.contains("# ---- format ----\n"), "{out}");
        assert!(out.contains("# ---- mount: done already\n"), "{out}");
    }

    /// The test's rescue system: every tool the stages run, and TABLE on vdb.
    fn rescue(name: &str, table: &[u8]) -> crate::testutil::Tree {
        let t = crate::testutil::Tree::new(name);
        t.disk("vdb", "253:16", table, 512).file("/sys/block/vdb/size", "33554432\n");
        t.file("/proc/self/mountinfo", "").file("/proc/swaps", "Filename\tType\tSize\tUsed\tPriority\n");
        for p in ["wipefs", "dd", "sfdisk", "udevadm", "mke2fs", "tune2fs", "mkswap", "mkfs.btrfs", "btrfs", "mkfs.xfs", "xfs_admin", "mkdir", "mount", "umount", "chroot", "cp", "sh", "bash"] {
            t.file(&format!("/usr/sbin/{p}"), "");
        }
        t
    }

    fn opts_of(a: &[&str]) -> Options {
        let mut v = vec!["l.json".to_string()];
        v.extend(a.iter().map(|s| s.to_string()));
        match crate::load::parse_args(&v).unwrap() {
            crate::load::Parsed::Run(o) => o,
            _ => unreachable!(),
        }
    }

    /// Without --erase, a dry run --through mount on a blank disk skips
    /// what it always skipped: the stages the layout needs nothing from.
    #[test]
    fn without_erase_through_skips_as_before() {
        use crate::load::layout::sample;
        use crate::sys::FakeRunner;
        let t = rescue("load-exec-plain", &[]);
        let s = Sys::new(t.root(), Box::new(FakeRunner::default()), true);
        let l = Layout::from_json(crate::json::parse(&sample::bios_mbr()).unwrap()).unwrap();
        let (code, out, err) = run(&l, &opts_of(&["--map", "vda=vdb", "--through", "mount"]), &s, &[Stage::Partition, Stage::Assemble, Stage::Format, Stage::Mount]);
        assert_eq!(code, EXIT_OK, "{out}{err}");
        let heads: Vec<&str> = out.lines().filter(|l| l.starts_with("# ---- ")).collect();
        assert_eq!(heads, vec!["# ---- partition ----", "# ---- assemble: nothing to do already", "# ---- format ----", "# ---- mount ----"], "{out}");
    }

    /// The whole-disk XFS layout onto the source's own disk, the old XFS
    /// (the layout's UUID) on it, with --erase and no record: the
    /// partition stage runs, and format with it, though status would take
    /// the old filesystem for format's.
    #[test]
    fn through_wipes_a_disk_with_no_table_load_did_not_wipe() {
        use crate::load::layout::sample;
        use crate::sys::FakeRunner;
        let t = rescue("load-exec-whole", &crate::probe::xfs::sample::image(""));
        let s = Sys::new(t.root(), Box::new(FakeRunner::default()), true);
        let vdx = r#"{"name": "vdx", "kind": "disk", "size_bytes": 17179869184, "logical_sector_size": 512, "model": null, "serial": null, "wwid": null,
          "partition_table": null, "partitions": [], "boot_code": {"kind": "grub2"},
          "content": {"type": "xfs", "usage": "filesystem", "uuid": "a0a1a2a3-a4a5-a6a7-a8a9-aaabacadaeaf", "label": null}}"#;
        let fstab = r#"{"spec": "UUID=a0a1a2a3-a4a5-a6a7-a8a9-aaabacadaeaf", "file": "/", "type": "xfs", "options": "defaults", "freq": 0, "passno": 0, "resolves_to": "vdx"}"#;
        // The sample's disks, btrfs and fstab swapped for vdx's.
        let swap = |j: &str, from: &str, to: &str, with: &str| {
            let (a, rest) = j.split_at(j.find(from).unwrap());
            format!("{a}{with},\n    {}", &rest[rest.find(to).unwrap()..])
        };
        let j = swap(&sample::bios_mbr(), "\"disks\": [", "\"md_arrays\"", &format!("\"disks\": [{vdx}]"));
        let j = swap(&j, "\"btrfs\": [", "\"xfs_external_logs\"", "\"btrfs\": []");
        let j = swap(&j, "\"fstab\": [", "\"crypttab\"", &format!("\"fstab\": [{fstab}]"));
        let l = Layout::from_json(crate::json::parse(&j).unwrap()).unwrap();
        let stages = [Stage::Partition, Stage::Assemble, Stage::Format];
        let heads = |a: &[&str]| {
            let (code, out, err) = run(&l, &opts_of(a), &s, &stages);
            assert_eq!(code, EXIT_OK, "{a:?}: {out}{err}");
            out.lines().filter(|l| l.starts_with("# ---- ")).map(str::to_string).collect::<Vec<_>>()
        };
        // Without --erase, as before: the old XFS is taken for format's.
        assert_eq!(heads(&["--map", "vdx=vdb", "--through", "format"]), vec!["# ---- partition: nothing to do already", "# ---- assemble: nothing to do already", "# ---- format: done already"]);
        let erase = ["--map", "vdx=vdb", "--erase", "/dev/vdb", "--through", "format"];
        assert_eq!(heads(&erase), vec!["# ---- partition ----", "# ---- assemble: nothing to do already", "# ---- format ----"]);
        let p = crate::load::target::prepare(&l, &opts_of(&erase), &s);
        let (path, text) = &p.mapping.records[0];
        t.file(path, text.as_str());
        assert_eq!(heads(&erase), vec!["# ---- partition: done already", "# ---- assemble: nothing to do already", "# ---- format: done already"]);
    }

    // ---- the copy marks (RT10e, J10) --------------------------------------

    /// The sample layout's source disk as load partitioned it in this
    /// session (its table and this session's record), the tree mounted with
    /// a restored Debian in it, a private password file, and SYS's archiver
    /// answering as BackOtter's.
    fn restored(name: &str, archiver: &str) -> (crate::testutil::Tree, Sys, String) {
        use crate::load::layout::sample;
        use crate::sys::FakeRunner;
        use std::os::unix::fs::PermissionsExt;
        let mut table = vec![0u8; 512];
        table[440..444].copy_from_slice(&0x1f2e_3d4cu32.to_le_bytes());
        for (slot, start, n) in [(0, 2048, 1953792), (1, 1955840, 3905536), (2, 5861376, 27693056)] {
            crate::parttable::build::mbr_entry(&mut table, slot, 0, 0x83, start, n);
        }
        let t = rescue(name, &table);
        t.file("/proc/self/mountinfo", "36 35 253:19 / /mnt/bko-target rw,relatime - btrfs /dev/vdb3 rw\n37 36 253:17 / /mnt/bko-target/boot rw,relatime - ext4 /dev/vdb1 rw\n");
        t.file("/mnt/bko-target/etc/fstab", "").file("/mnt/bko-target/etc/os-release", "ID=debian\n");
        t.file("/k/pw", "pw");
        std::fs::set_permissions(t.p("/k/pw"), std::fs::Permissions::from_mode(0o600)).unwrap();
        std::fs::set_permissions(t.p("/k"), std::fs::Permissions::from_mode(0o700)).unwrap();
        let s = Sys::new(t.root(), Box::new(FakeRunner::default().with(&format!("{archiver} --version"), "rustic 0.12.0+backotter.4\n")), true);
        let l = sample::bios_mbr();
        let lay = Layout::from_json(crate::json::parse(&l).unwrap()).unwrap();
        let (path, text) = crate::load::target::prepare(&lay, &opts_of(&["--map", "vda=vdb"]), &s).mapping.records.remove(0);
        t.file(&path, text);
        (t, s, l)
    }

    /// Under --json, --through skips restore only with this session's
    /// complete mark: the tree's files alone don't do; text mode skips by
    /// the files, as before.
    #[test]
    fn through_skips_restore_by_its_mark_under_json() {
        let (t, s, l) = restored("load-exec-marks", "/usr/sbin/rustic-bko");
        t.file("/usr/sbin/rustic-bko", "");
        let lay = Layout::from_json(crate::json::parse(&l).unwrap()).unwrap();
        let a = ["--map", "vda=vdb", "--repo", "/srv/r", "--password-file", "/k/pw", "--through", "restore"];
        let stages = [Stage::Partition, Stage::Assemble, Stage::Format, Stage::Mount, Stage::Restore];
        let restore_head = |extra: &[&str]| {
            let (code, out, err) = run(&lay, &opts_of(&[&a[..], extra].concat()), &s, &stages);
            assert_eq!(code, EXIT_OK, "{extra:?}: {out}{err}");
            out.lines().find(|l| l.starts_with("# ---- restore")).unwrap().to_string()
        };
        assert_eq!(restore_head(&[]), "# ---- restore: done already");
        assert_eq!(restore_head(&["--json"]), "# ---- restore ----");
        let m = crate::load::target::prepare(&lay, &opts_of(&["--map", "vda=vdb"]), &s).mapping;
        t.file(&status::restore_done_marker(), status::restore_mark_text(&lay, &m));
        assert_eq!(restore_head(&["--json"]), "# ---- restore: done already");
        assert_eq!(restore_head(&[]), "# ---- restore: done already");
    }

    /// The restore stage writes its start mark as it begins (text mode
    /// too) and removes the complete one: a copy that fails leaves none,
    /// so under --json its row is partly and it runs again.
    #[test]
    fn a_failed_copy_leaves_no_complete_mark() {
        let t0 = crate::testutil::Tree::new("load-exec-archiver");
        // The archiver: a script that fails as rustic-bko would, here at a
        // real path the run can start, and at the same path in the tree,
        // where plan looks for it.
        let archiver = t0.p("/fake-rustic").to_string_lossy().into_owned();
        t0.file("/fake-rustic", "#!/bin/sh\necho 'error: the repository is gone' >&2\nexit 3\n");
        use std::os::unix::fs::PermissionsExt;
        std::fs::set_permissions(t0.p("/fake-rustic"), std::fs::Permissions::from_mode(0o755)).unwrap();
        let (t, s, l) = restored("load-exec-fail", &archiver);
        t.file(&archiver, "");
        let lay = Layout::from_json(crate::json::parse(&l).unwrap()).unwrap();
        let m = crate::load::target::prepare(&lay, &opts_of(&["--map", "vda=vdb"]), &s).mapping;
        let mark = status::restore_mark_text(&lay, &m);
        assert_eq!(mark, "layout deb13 -\ntarget vda vdb\n");
        let a = ["--map", "vda=vdb", "--repo", "/srv/r", "--password-file", "/k/pw", "--archiver", archiver.as_str(), "--only", "restore", "--commit"];
        for json in [false, true] {
            // A complete mark an earlier copy left.
            t.file(&status::restore_done_marker(), mark.as_str());
            let _ = std::fs::remove_file(t.p(&status::restore_started_marker()));
            let extra: &[&str] = if json { &["--json"] } else { &[] };
            let o = opts_of(&[&a[..], extra].concat());
            let (code, _, err) = run(&lay, &o, &s, &[Stage::Restore]);
            assert_eq!(code, EXIT_FAILED, "{json}: {err}");
            assert!(err.contains("exit status: 3") && err.contains("the repository is gone"), "{json}: {err}");
            assert_eq!(std::fs::read_to_string(t.p(&status::restore_started_marker())).ok().as_deref(), Some(mark.as_str()), "{json}");
            assert!(!t.p(&status::restore_done_marker()).exists(), "{json}");
        }
        // Under --json its row is partly: --through runs it again.
        let row = |json: bool| {
            let mut o = opts_of(&["--map", "vda=vdb"]);
            o.json = json;
            status::rows(&lay, &o, &s).0.into_iter().find(|r| r.0 == Stage::Restore).map(|r| r.1).unwrap()
        };
        assert_eq!((row(false), row(true)), (State::Done, State::Partly));
        // RUN_DIR's own files are SYS's: the log is the test's tree's.
        assert!(t.p(&format!("{RUN_DIR}/log/load.log")).exists());
    }

    /// (restore.started, restore.done, boot.done).
    type Marks = (Option<String>, Option<String>, bool);

    /// The marks as a step finds them in T.
    fn marks(t: &crate::testutil::Tree) -> Marks {
        let read = |m: String| std::fs::read_to_string(t.p(&m)).ok();
        (read(status::restore_started_marker()), read(status::restore_done_marker()), t.p(&status::boot_marker()).exists())
    }

    /// The restore stage run by a stand-in for run_cmd that records each
    /// step's command line and the marks as it ran, and fails the steps
    /// FAIL picks.  Its exit, standard error, and what it saw.
    fn restore_with(t: &crate::testutil::Tree, s: &Sys, lay: &Layout, json: bool, fail: &dyn Fn(&str, &[String]) -> bool) -> (u8, String, Vec<(String, Marks)>) {
        let mut a = vec!["--map", "vda=vdb", "--repo", "/srv/r", "--password-file", "/k/pw", "--only", "restore", "--commit"];
        if json {
            a.push("--json");
        }
        let seen = std::cell::RefCell::new(Vec::new());
        let step = |c: &Cmd, _: &mut Option<std::fs::File>, _: &Sys| {
            let line = c.shell();
            let before: Vec<String> = seen.borrow().iter().map(|x: &(String, Marks)| x.0.clone()).collect();
            seen.borrow_mut().push((line.clone(), marks(t)));
            if fail(&line, &before) {
                Err("it failed".to_string())
            } else {
                Ok(Vec::new())
            }
        };
        let (code, _, err) = run_with(lay, &opts_of(&a), s, &[Stage::Restore], &step);
        (code, err, seen.into_inner())
    }

    /// A copy is complete only once its last command (the atimes' capture,
    /// after the archiver) has run and its read-back passed: every step
    /// runs with the start mark and no complete one, the boot mark of the
    /// tree before gone; a failed read-back or a step after the archiver
    /// failing leaves no complete mark.  Text mode the same.
    #[test]
    fn the_complete_mark_comes_last() {
        let (t, s, l) = restored("load-exec-done", "/usr/sbin/rustic-bko");
        t.file("/usr/sbin/rustic-bko", "");
        let lay = Layout::from_json(crate::json::parse(&l).unwrap()).unwrap();
        let mark = status::restore_mark_text(&lay, &crate::load::target::prepare(&lay, &opts_of(&["--map", "vda=vdb"]), &s).mapping);
        let archiver = |l: &str| l.contains("rustic-bko --repository /srv/r");
        let earlier = || {
            t.file(&status::restore_done_marker(), "an earlier copy's\n").file(&status::boot_marker(), "");
            let _ = std::fs::remove_file(t.p(&status::restore_started_marker()));
        };
        for json in [false, true] {
            // It runs to its end.
            earlier();
            let (code, err, seen) = restore_with(&t, &s, &lay, json, &|_, _| false);
            assert_eq!(code, EXIT_OK, "{json}: {err}");
            let lines: Vec<&str> = seen.iter().map(|(l, _)| l.as_str()).collect();
            let at = lines.iter().position(|l| archiver(l)).unwrap_or_else(|| panic!("{json}: no archiver: {lines:?}"));
            assert!(at + 1 < lines.len() && lines.last().unwrap().contains("atime"), "{json}: {lines:?}");
            for (l, m) in &seen {
                assert_eq!(m, &(Some(mark.clone()), None, false), "{json}: {l}");
            }
            assert_eq!(marks(&t), (Some(mark.clone()), Some(mark.clone()), false), "{json}");
            // A step after the archiver fails.
            earlier();
            let (code, err, seen) = restore_with(&t, &s, &lay, json, &|l, before| !archiver(l) && before.iter().any(|b| archiver(b)));
            assert_eq!(code, EXIT_FAILED, "{json}: {err}");
            assert!(err.contains("bko-holt load: restore: it failed"), "{json}: {err}");
            assert!(seen.iter().any(|(l, _)| archiver(l)), "{json}");
            assert_eq!(marks(&t), (Some(mark.clone()), None, false), "{json}");
            // Every step passes, but the tree doesn't show the system.
            earlier();
            std::fs::remove_file(t.p("/mnt/bko-target/etc/fstab")).unwrap();
            let (code, err, _) = restore_with(&t, &s, &lay, json, &|_, _| false);
            assert_eq!(code, EXIT_FAILED, "{json}: {err}");
            assert!(err.contains("restore ran, but the disks don't show it done"), "{json}: {err}");
            assert_eq!(marks(&t), (Some(mark.clone()), None, false), "{json}");
            t.file("/mnt/bko-target/etc/fstab", "");
        }
    }

    /// A partition stage that runs removes both copy marks (the disks are
    /// new again), and boot.done only under --json; it records its table
    /// in SYS's RUN_DIR.
    #[test]
    fn the_partition_stage_clears_the_copy_marks() {
        let (t, s, l) = restored("load-exec-partition", "/usr/sbin/rustic-bko");
        t.file("/proc/self/mountinfo", "");
        let lay = Layout::from_json(crate::json::parse(&l).unwrap()).unwrap();
        let (record, text) = crate::load::target::prepare(&lay, &opts_of(&["--map", "vda=vdb"]), &s).mapping.records.remove(0);
        for json in [false, true] {
            t.file(&status::restore_started_marker(), "x\n").file(&status::restore_done_marker(), "x\n").file(&status::boot_marker(), "");
            std::fs::remove_file(t.p(&record)).unwrap();
            let mut a = vec!["--map", "vda=vdb", "--erase", "/dev/vdb", "--only", "partition", "--commit"];
            if json {
                a.push("--json");
            }
            let ran = std::cell::Cell::new(0);
            let step = |_: &Cmd, _: &mut Option<std::fs::File>, _: &Sys| {
                ran.set(ran.get() + 1);
                Ok(Vec::new())
            };
            let (code, out, err) = run_with(&lay, &opts_of(&a), &s, &[Stage::Partition], &step);
            assert_eq!(code, EXIT_OK, "{json}: {out}{err}");
            assert!(ran.get() > 0, "{json}");
            assert_eq!(marks(&t), (None, None, !json), "{json}");
            assert_eq!(std::fs::read_to_string(t.p(&record)).ok().as_deref(), Some(text.as_str()), "{json}");
        }
    }
}
