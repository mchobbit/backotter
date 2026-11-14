//! `bko-holt load`: rebuilds a machine's disks from a `bko-holt dump`, in
//! stages that each do only what they're told (docs/design/layload.md).
//!
//! `plan` runs every check and prints every command the writing stages will
//! run, and `status` reports what's on the target disks and which stage
//! comes next; neither writes anything.  The writing stages (partition,
//! assemble, format, mount, restore, boot, close) make the same checks, run
//! those commands with --commit (else print them), and read the disks back
//! after each stage (exec.rs).
//!
//! `restore` runs the archiver (rustic-bko) between `mount` and `boot`;
//! bko-holt never copies file data itself.  Without --repo it's left to the
//! administrator: `--through mount`, restore by hand, then `--only boot`.

pub mod commands;
pub mod exec;
pub mod fit;
pub mod layout;
pub mod ostree;
pub mod plan;
pub mod readback;
pub mod release;
pub mod shared;
pub mod status;
pub mod target;
pub mod writetable;

use crate::sys::{FakeRunner, Sys};

/// Where `mount` builds the restored system's tree, unless --target says.
pub const DEFAULT_TARGET: &str = "/mnt/bko-target";
/// Stage state, logs and temporary keys, in RAM: nothing is written to the
/// target disks before there's a filesystem on them.  /run is Linux's;
/// elsewhere it's /var/run, which the BSD rescue images keep in memory under
/// a read-only / with no /run (Haiku's / is virtual: its /var is the boot
/// volume's).
pub const RUN_DIR: &str = if cfg!(target_os = "linux") { "/run/bko-holt" } else { "/var/run/bko-holt" };
/// restore's archiver, and the snapshot it restores unless --snapshot says.
pub const DEFAULT_ARCHIVER: &str = "rustic-bko";
pub const DEFAULT_SNAPSHOT: &str = "latest";

/// The most --fit-largest takes: half a partition is as far as version 1
/// goes either way.
pub const FIT_MAX: u64 = 50;

pub const EXIT_OK: u8 = 0;
pub const EXIT_FAILED: u8 = 1;
pub const EXIT_BAD_ARGS: u8 = 2;
pub const EXIT_UNSUPPORTED: u8 = 3;
pub const EXIT_PLAN_PROBLEMS: u8 = 4;

#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord)]
pub enum Stage {
    Plan,
    Status,
    Partition,
    Assemble,
    Format,
    Mount,
    Restore,
    Boot,
    Close,
}

impl Stage {
    pub const ALL: [Stage; 9] = [Stage::Plan, Stage::Status, Stage::Partition, Stage::Assemble, Stage::Format, Stage::Mount, Stage::Restore, Stage::Boot, Stage::Close];
    /// The stages that change something, in the order they run.
    pub const WRITING: [Stage; 7] = [Stage::Partition, Stage::Assemble, Stage::Format, Stage::Mount, Stage::Restore, Stage::Boot, Stage::Close];

    pub fn name(self) -> &'static str {
        match self {
            Stage::Plan => "plan",
            Stage::Status => "status",
            Stage::Partition => "partition",
            Stage::Assemble => "assemble",
            Stage::Format => "format",
            Stage::Mount => "mount",
            Stage::Restore => "restore",
            Stage::Boot => "boot",
            Stage::Close => "close",
        }
    }

    pub fn parse(s: &str) -> Option<Stage> {
        Stage::ALL.into_iter().find(|st| st.name() == s)
    }

    pub fn writes(self) -> bool {
        Stage::WRITING.contains(&self)
    }
}

#[derive(Debug, Clone, PartialEq)]
pub struct Options {
    pub layout: String,
    pub through: Option<Stage>,
    pub only: Option<Stage>,
    pub commit: bool,
    /// --map SRC=DST: a source disk's kernel name, and the target disk as
    /// given (a /dev path, a /dev/disk/by-* link or a kernel name).
    pub maps: Vec<(String, String)>,
    pub erase: Vec<String>,
    pub target: String,
    /// --fit-largest PCT: a target a little smaller or bigger may be used by
    /// contracting or expanding each disk's largest partition by at most
    /// this many percent (fit.rs).
    pub fit_largest: Option<u64>,
    pub new_uuids: bool,
    /// --key-file NAME=FILE: a LUKS container (by its crypttab name) or a
    /// ZFS encryption root (by dataset).
    pub key_files: Vec<(String, String)>,
    pub no_nvram: bool,
    pub removable: bool,
    /// restore: the archiver's repository; without it, restore is by hand.
    pub repo: Option<String>,
    pub password_file: Option<String>,
    pub snapshot: String,
    pub archiver: String,
    /// --btrfs-snapshots copy: btrfs snapshots restored as full copies, as
    /// before shared snapshots (docs/design/btrfs-snapshots.md).
    pub btrfs_copy: bool,
    /// For tests: read the system load runs on from this tree, running no
    /// commands.
    pub sysroot: Option<String>,
}

impl Options {
    pub fn new(layout: &str) -> Options {
        Options {
            layout: layout.to_string(),
            through: None,
            only: None,
            commit: false,
            maps: vec![],
            erase: vec![],
            target: DEFAULT_TARGET.to_string(),
            fit_largest: None,
            new_uuids: false,
            key_files: vec![],
            no_nvram: false,
            removable: false,
            repo: None,
            password_file: None,
            snapshot: DEFAULT_SNAPSHOT.to_string(),
            archiver: DEFAULT_ARCHIVER.to_string(),
            btrfs_copy: false,
            sysroot: None,
        }
    }

    /// The stages this run asks for, in order.  Neither --through nor
    /// --only: plan.
    pub fn stages(&self) -> Vec<Stage> {
        match (self.only, self.through) {
            (Some(s), _) => vec![s],
            (None, Some(s)) if s.writes() => Stage::WRITING.into_iter().filter(|w| *w <= s).collect(),
            (None, Some(s)) => vec![s],
            (None, None) => vec![Stage::Plan],
        }
    }

    pub fn key_file(&self, name: &str) -> Option<&str> {
        self.key_files.iter().find(|(n, _)| n == name).map(|(_, f)| f.as_str())
    }
}

pub const USAGE: &str = "Usage: bko-holt load LAYOUT.json [--through STAGE | --only STAGE] [--commit]
                     [--map SRC=DST]... [--erase DEV]... [--fit-largest PCT]
                     [--target DIR] [--new-uuids] [--key-file NAME=FILE]...
                     [--no-nvram] [--removable]
                     [--repo REPO [--password-file FILE] [--snapshot ID]
                      [--archiver PROGRAM]] [--btrfs-snapshots shared|copy]

Rebuilds the disks LAYOUT.json (from `bko-holt dump`) describes, in stages:

  plan       checks everything and prints every command the stages below
             would run; the default, and it writes nothing
  status     what's on the target disks now, and which stage comes next
  partition  partition tables
  assemble   md arrays, LUKS containers, LVM, ZFS pools
  format     filesystems, swap, btrfs subvolumes, ZFS datasets
  mount      the target tree under --target, for the archiver to fill
  restore    the archiver restores the backup into it (needs --repo;
             without it, restore by hand and go on with --only boot)
  boot       after the restore: initramfs, boot loader, UEFI entries
  close      unmount, export pools, close containers, stop arrays

  --through STAGE   run the writing stages up to and including STAGE
  --only STAGE      run just STAGE
  --commit          let the writing stages write; without it they print
                    their commands
  --map SRC=DST     the source disk SRC (its name in the JSON, e.g. vda)
                    goes to DST (/dev/sdb, /dev/disk/by-id/...)
  --erase DEV       a target disk that isn't blank may be wiped
  --fit-largest PCT a target disk a little smaller or bigger than its
                    source may be used: each disk's largest partition
                    contracts or expands by at most PCT percent (1 to 50),
                    and everything else keeps its size.  Linux layouts
                    only; plan prints what changes
  --target DIR      where mount builds the tree (default /mnt/bko-target)
  --new-uuids       new identifiers everywhere instead of the original ones
  --key-file NAME=FILE
                    a LUKS container's (crypttab name) or ZFS encryption
                    root's (dataset) key or passphrase, instead of a prompt;
                    cryptsetup takes the file's bytes as they are, so a
                    passphrase's file must not end in a newline
  --no-nvram        boot writes no UEFI variables
  --removable       boot also installs the loader to EFI/BOOT/BOOTX64.EFI
  --repo REPO       restore from this repository (rustic-bko's -r)
  --password-file FILE
                    the repository's password; without it rustic-bko asks
  --snapshot ID     the snapshot to restore (default latest)
  --archiver PROGRAM
                    the archiver to run (default rustic-bko)
  --btrfs-snapshots shared|copy
                    shared (the default): btrfs snapshots in the backup
                    (snapper's) are made as snapshots of the one before
                    them, then only their changes restored, so they share
                    their extents; needs rustic-bko's restore --exact.
                    copy: each is restored in full.  Give every stage
                    the same (format and restore must agree); to restore
                    by hand, give copy
  -h, --help        show this help

Exit status: 0 done, 1 a stage failed, 2 bad arguments, 3 the layout isn't
one bko-holt can load, 4 plan found problems.
";

// Parsed once per run, so Options' size doesn't matter.
#[allow(clippy::large_enum_variant)]
#[derive(Debug, PartialEq)]
pub enum Parsed {
    Run(Options),
    Help,
}

fn pair(flag: &str, v: &str) -> Result<(String, String), String> {
    match v.split_once('=') {
        Some((a, b)) if !a.is_empty() && !b.is_empty() => Ok((a.to_string(), b.to_string())),
        _ => Err(format!("{flag} wants NAME=VALUE, not {v:?}")),
    }
}

pub fn parse_args(args: &[String]) -> Result<Parsed, String> {
    let mut layout: Option<String> = None;
    let mut o = Options::new("");
    let mut it = args.iter();
    let value = |it: &mut std::slice::Iter<String>, flag: &str| it.next().cloned().ok_or_else(|| format!("{flag} needs a value"));
    while let Some(a) = it.next() {
        match a.as_str() {
            "-h" | "--help" => return Ok(Parsed::Help),
            "--through" | "--only" => {
                let v = value(&mut it, a)?;
                let st = Stage::parse(&v).ok_or_else(|| format!("{a}: no stage called {v:?}"))?;
                if a == "--through" {
                    o.through = Some(st);
                } else {
                    o.only = Some(st);
                }
            }
            "--commit" => o.commit = true,
            "--map" => {
                let v = value(&mut it, a)?;
                let (src, dst) = pair(a, &v)?;
                if o.maps.iter().any(|(s, _)| *s == src) {
                    return Err(format!("--map {src}=...: {src} is mapped twice"));
                }
                o.maps.push((src, dst));
            }
            "--erase" => o.erase.push(value(&mut it, a)?),
            "--target" => {
                let v = value(&mut it, a)?;
                if !v.starts_with('/') || v == "/" {
                    return Err(format!("--target {v}: give an absolute directory other than /"));
                }
                o.target = v.trim_end_matches('/').to_string();
            }
            "--fit-largest" => {
                let v = value(&mut it, a)?;
                let n: u64 = v.strip_suffix('%').unwrap_or(&v).parse().map_err(|_| format!("--fit-largest {v}: give a whole percentage, 1 to {FIT_MAX}"))?;
                if !(1..=FIT_MAX).contains(&n) {
                    return Err(format!("--fit-largest {v}: give a whole percentage, 1 to {FIT_MAX}"));
                }
                o.fit_largest = Some(n);
            }
            "--new-uuids" => o.new_uuids = true,
            "--key-file" => {
                let v = value(&mut it, a)?;
                o.key_files.push(pair(a, &v)?);
            }
            "--no-nvram" => o.no_nvram = true,
            "--removable" => o.removable = true,
            "--repo" => o.repo = Some(value(&mut it, a)?),
            "--password-file" => o.password_file = Some(value(&mut it, a)?),
            "--snapshot" => o.snapshot = value(&mut it, a)?,
            "--archiver" => o.archiver = value(&mut it, a)?,
            "--btrfs-snapshots" => {
                o.btrfs_copy = match value(&mut it, a)?.as_str() {
                    "shared" => false,
                    "copy" => true,
                    v => return Err(format!("--btrfs-snapshots {v}: give shared or copy")),
                }
            }
            "--sysroot" => o.sysroot = Some(value(&mut it, a)?),
            s if s.starts_with('-') => return Err(format!("unknown argument {s:?}")),
            s => {
                if layout.is_some() {
                    return Err(format!("one layout file only, not also {s:?}"));
                }
                layout = Some(s.to_string());
            }
        }
    }
    if o.through.is_some() && o.only.is_some() {
        return Err("--through and --only don't go together".into());
    }
    if o.repo.is_none() && (o.password_file.is_some() || o.snapshot != DEFAULT_SNAPSHOT || o.archiver != DEFAULT_ARCHIVER) {
        return Err("--password-file, --snapshot and --archiver are for restore; give --repo too".into());
    }
    if o.repo.is_none() && o.stages().contains(&Stage::Restore) {
        return Err(format!(
            "restore needs --repo; to restore by hand, run --through mount, restore into {}, then --only boot",
            o.target
        ));
    }
    o.layout = layout.ok_or("which layout? Give the JSON file bko-holt dump wrote")?;
    Ok(Parsed::Run(o))
}

/// `bko-holt load ARGS`: prints the result and returns the exit status.
/// Each run is recorded in RUN_DIR for `bko-holt report-bug`.
pub fn main(args: &[String]) -> u8 {
    let (code, err) = main_inner(args);
    eprint!("{err}");
    if !matches!(parse_args(args), Ok(Parsed::Help)) {
        crate::bugreport::record_load_run(args, code, &err);
    }
    code
}

/// The exit status and standard error; standard output is printed.
fn main_inner(args: &[String]) -> (u8, String) {
    let opts = match parse_args(args) {
        Ok(Parsed::Help) => {
            print!("{USAGE}");
            return (EXIT_OK, String::new());
        }
        Ok(Parsed::Run(o)) => o,
        Err(e) => return (EXIT_BAD_ARGS, format!("bko-holt load: {e}\n\n{USAGE}")),
    };
    let text = match std::fs::read_to_string(&opts.layout) {
        Ok(t) => t,
        Err(e) => return (EXIT_BAD_ARGS, format!("bko-holt load: {}: {e}\n", opts.layout)),
    };
    let sys = match &opts.sysroot {
        Some(d) if !std::path::Path::new(d).is_dir() => return (EXIT_BAD_ARGS, format!("bko-holt load: --sysroot {d}: not a directory\n")),
        Some(d) => Sys::new(d, Box::new(FakeRunner::default()), crate::sys::euid() == 0),
        None => Sys::host(),
    };
    let (code, out, err) = run(&opts, &text, &sys);
    print!("{out}");
    (code, err)
}

/// Runs what `opts` asks for on `sys` (the system load runs on, whose disks
/// are the targets): the exit status, standard output and standard error.
pub fn run(opts: &Options, layout_text: &str, sys: &Sys) -> (u8, String, String) {
    let json = match crate::json::parse(layout_text) {
        Ok(v) => v,
        Err(e) => return (EXIT_UNSUPPORTED, String::new(), format!("bko-holt load: {}: not JSON ({} at byte {})\n", opts.layout, e.message, e.offset)),
    };
    let layout = match layout::Layout::from_json(json) {
        Ok(l) => l,
        Err(e) => return (EXIT_UNSUPPORTED, String::new(), format!("bko-holt load: {}: {e}\n", opts.layout)),
    };
    let stages = opts.stages();
    match stages.as_slice() {
        [Stage::Plan] => plan::run(&layout, opts, sys),
        [Stage::Status] => status::run(&layout, opts, sys),
        _ => exec::run(&layout, opts, sys, &stages),
    }
}

/// The source-to-target disk pairs `plan` makes of LAYOUT_TEXT here, with
/// OPTS's --map or else by WWID or serial (report-bug gives them as --map to
/// a run on the redacted layout, whose serials match no disk).
pub fn disk_pairs(opts: &Options, layout_text: &str, sys: &Sys) -> Vec<(String, String)> {
    let Some(layout) = crate::json::parse(layout_text).ok().and_then(|j| layout::Layout::from_json(j).ok()) else {
        return Vec::new();
    };
    let system = target::scan(sys, opts, &target::layout_pools(&layout));
    target::map(&layout, opts, &system, sys).pairs
}

/// The suffix `report-bug` gives a boot-area or ESP image's key when it
/// leaves the image out of a report (KEY_omitted: its size and CRC-32).
pub const OMITTED: &str = "_omitted";

/// Whether V has an image `report-bug` left out: such a layout can be
/// planned, but not written.
pub fn has_omitted(v: &crate::json::Value) -> bool {
    use crate::json::Value;
    match v {
        Value::Obj(kv) => kv.iter().any(|(k, x)| k.ends_with(OMITTED) || has_omitted(x)),
        Value::Arr(a) => a.iter().any(has_omitted),
        _ => false,
    }
}

/// A word for a shell command line: quoted when it needs to be.
pub fn shell_quote(s: &str) -> String {
    if !s.is_empty() && s.bytes().all(|b| b.is_ascii_alphanumeric() || b"-_./:=,+@%^".contains(&b)) {
        s.to_string()
    } else {
        format!("'{}'", s.replace('\'', "'\\''"))
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn args(a: &[&str]) -> Vec<String> {
        a.iter().map(|s| s.to_string()).collect()
    }

    fn opts(a: &[&str]) -> Options {
        match parse_args(&args(a)).unwrap() {
            Parsed::Run(o) => o,
            Parsed::Help => panic!("help"),
        }
    }

    #[test]
    fn stage_names_and_order() {
        for s in Stage::ALL {
            assert_eq!(Stage::parse(s.name()), Some(s));
        }
        assert_eq!(Stage::parse("restore"), Some(Stage::Restore));
        assert_eq!(Stage::parse("backup"), None);
        assert!(Stage::Mount < Stage::Restore && Stage::Restore < Stage::Boot);
        assert!(Stage::Partition < Stage::Close);
        assert!(!Stage::Plan.writes() && !Stage::Status.writes() && Stage::Boot.writes());
    }

    #[test]
    fn defaults_and_stage_selection() {
        let o = opts(&["layout.json"]);
        assert_eq!(o.layout, "layout.json");
        assert_eq!(o.target, DEFAULT_TARGET);
        assert!(!o.commit && !o.new_uuids && !o.no_nvram && !o.removable);
        assert_eq!(o.stages(), vec![Stage::Plan]);
        assert_eq!(opts(&["l", "--through", "mount"]).stages(), vec![Stage::Partition, Stage::Assemble, Stage::Format, Stage::Mount]);
        assert_eq!(opts(&["l", "--through", "close", "--repo", "/r"]).stages(), Stage::WRITING.to_vec());
        assert_eq!(opts(&["l", "--through", "mount"]).repo, None);
        assert_eq!(opts(&["l", "--only", "boot"]).stages(), vec![Stage::Boot]);
        assert_eq!(opts(&["l", "--only", "status"]).stages(), vec![Stage::Status]);
        assert_eq!(opts(&["l", "--through", "plan"]).stages(), vec![Stage::Plan]);
    }

    #[test]
    fn every_flag() {
        let o = opts(&[
            "--map", "vda=/dev/sdb", "x.json", "--map", "vdb=/dev/disk/by-id/ata-X", "--erase", "/dev/sdb", "--target", "/mnt/t/", "--new-uuids",
            "--fit-largest", "15%",
            "--key-file", "root_crypt=/tmp/k", "--no-nvram", "--removable", "--commit", "--only", "partition", "--sysroot", "/tmp/fake",
            "--repo", "sftp:nas:/bko", "--password-file", "/run/pw", "--snapshot", "1a2b3c", "--archiver", "/usr/local/bin/rustic-bko",
        ]);
        assert_eq!(o.repo.as_deref(), Some("sftp:nas:/bko"));
        assert_eq!(o.password_file.as_deref(), Some("/run/pw"));
        assert_eq!(o.snapshot, "1a2b3c");
        assert_eq!(o.archiver, "/usr/local/bin/rustic-bko");
        assert_eq!(o.layout, "x.json");
        assert_eq!(o.maps, vec![("vda".to_string(), "/dev/sdb".to_string()), ("vdb".to_string(), "/dev/disk/by-id/ata-X".to_string())]);
        assert_eq!(o.erase, vec!["/dev/sdb".to_string()]);
        assert_eq!(o.target, "/mnt/t");
        assert!(o.new_uuids && o.no_nvram && o.removable && o.commit);
        assert_eq!(o.fit_largest, Some(15));
        assert_eq!(opts(&["l", "--fit-largest", "1"]).fit_largest, Some(1));
        assert_eq!(opts(&["l", "--fit-largest", "50"]).fit_largest, Some(50));
        assert_eq!(opts(&["l"]).fit_largest, None);
        assert_eq!(o.key_file("root_crypt"), Some("/tmp/k"));
        assert_eq!(o.key_file("other"), None);
        assert_eq!(o.only, Some(Stage::Partition));
        assert_eq!(o.sysroot.as_deref(), Some("/tmp/fake"));
    }

    #[test]
    fn bad_arguments() {
        let err = |a: &[&str]| parse_args(&args(a)).unwrap_err();
        assert!(err(&[]).contains("which layout"));
        assert!(err(&["a.json", "b.json"]).contains("one layout file"));
        assert!(err(&["a", "--frob"]).contains("unknown argument"));
        assert!(err(&["a", "--through", "backup"]).contains("no stage called"));
        assert!(err(&["a", "--through", "boot"]).contains("restore needs --repo"));
        assert!(err(&["a", "--only", "restore"]).contains("restore needs --repo"));
        assert!(err(&["a", "--snapshot", "x"]).contains("give --repo too"));
        assert!(err(&["a", "--through", "mount", "--only", "boot"]).contains("don't go together"));
        assert!(err(&["a", "--map", "vda"]).contains("NAME=VALUE"));
        assert!(err(&["a", "--map", "=x"]).contains("NAME=VALUE"));
        assert!(err(&["a", "--map", "vda=x", "--map", "vda=y"]).contains("mapped twice"));
        assert!(err(&["a", "--target", "relative"]).contains("absolute"));
        assert!(err(&["a", "--target", "/"]).contains("absolute"));
        assert!(err(&["a", "--erase"]).contains("needs a value"));
        assert!(err(&["a", "--fit-largest"]).contains("needs a value"));
        for v in ["0", "51", "100", "lots", "", "5%%", "-5"] {
            assert!(err(&["a", "--fit-largest", v]).contains("whole percentage, 1 to 50"), "{v:?}");
        }
        assert_eq!(parse_args(&args(&["--help"])).unwrap(), Parsed::Help);
    }

    #[test]
    fn writing_stages_check_first() {
        let t = crate::testutil::Tree::new("load-run");
        let s = Sys::new(t.root(), Box::new(FakeRunner::default()), true);
        let layout = r#"{"format": "bko-holt", "schema": 1, "os": {"id": "debian", "version_id": "13"}, "storage": {"disks": []}}"#;
        // The same checks as plan's: this layout mounts no /.
        let (code, out, err) = run(&opts(&["l.json", "--through", "mount", "--commit"]), layout, &s);
        assert_eq!(code, EXIT_PLAN_PROBLEMS);
        assert!(out.is_empty());
        assert!(err.contains("nothing in fstab or the ZFS datasets mounts /"), "{err}");
        assert!(err.contains("nothing was written"), "{err}");
        let (code, _, err) = run(&opts(&["l.json"]), "{not json", &s);
        assert_eq!(code, EXIT_UNSUPPORTED);
        assert!(err.contains("not JSON"));
    }

    #[test]
    fn quoting() {
        assert_eq!(shell_quote("/dev/sda1"), "/dev/sda1");
        assert_eq!(shell_quote("feature@encryption=enabled"), "feature@encryption=enabled");
        assert_eq!(shell_quote("EFI System"), "'EFI System'");
        assert_eq!(shell_quote("it's"), "'it'\\''s'");
        assert_eq!(shell_quote(""), "''");
        assert_eq!(shell_quote("\\EFI\\DEBIAN\\SHIMX64.EFI"), "'\\EFI\\DEBIAN\\SHIMX64.EFI'");
    }
}
