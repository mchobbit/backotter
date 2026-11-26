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

pub mod abroot;
pub mod codes;
pub mod commands;
pub mod efidedupe;
pub mod exec;
pub mod fatset;
pub mod fit;
pub mod grubprefix;
pub mod jsonout;
pub mod layout;
pub mod luks1digest;
pub mod ostree;
pub mod plan;
pub mod qubes;
pub mod rawvol;
pub mod readback;
pub mod reformat;
pub mod release;
pub mod rewrite;
pub mod shared;
pub mod sizes;
pub mod snapcore;
pub mod status;
pub mod target;
pub mod transactional;
pub mod truenas;
pub mod umounttree;
pub mod unflag;
pub mod writetable;
pub mod zfs_be;
pub mod zfsbootmenu;

use crate::sys::{FakeRunner, Sys};

/// Where `mount` builds the restored system's tree, unless --target says.
pub const DEFAULT_TARGET: &str = "/mnt/bko-target";
/// Stage state, logs and temporary keys, in RAM: nothing is written to the
/// target disks before there's a filesystem on them.  /run is Linux's;
/// elsewhere it's /var/run, which the BSD rescue images keep in memory under
/// a read-only / with no /run (Haiku's / is virtual: its /var is the boot
/// volume's).
pub const RUN_DIR: &str = if cfg!(target_os = "linux") {
    "/run/bko-holt"
} else {
    "/var/run/bko-holt"
};
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
/// Under --json only: a writing run stopped on request (SIGTERM, J7).
pub const EXIT_STOPPED: u8 = 5;

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
    pub const ALL: [Self; 9] = [
        Self::Plan,
        Self::Status,
        Self::Partition,
        Self::Assemble,
        Self::Format,
        Self::Mount,
        Self::Restore,
        Self::Boot,
        Self::Close,
    ];
    /// The stages that change something, in the order they run.
    pub const WRITING: [Self; 7] = [
        Self::Partition,
        Self::Assemble,
        Self::Format,
        Self::Mount,
        Self::Restore,
        Self::Boot,
        Self::Close,
    ];

    pub fn name(self) -> &'static str {
        match self {
            Self::Plan => "plan",
            Self::Status => "status",
            Self::Partition => "partition",
            Self::Assemble => "assemble",
            Self::Format => "format",
            Self::Mount => "mount",
            Self::Restore => "restore",
            Self::Boot => "boot",
            Self::Close => "close",
        }
    }

    pub fn parse(s: &str) -> Option<Self> {
        Self::ALL.into_iter().find(|st| st.name() == s)
    }

    pub fn writes(self) -> bool {
        Self::WRITING.contains(&self)
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
    /// --reformat MOUNT=TYPE: the filesystem mounted at MOUNT is made as
    /// TYPE instead of its own (reformat.rs).
    pub reformats: Vec<(String, String)>,
    /// --key-file NAME=FILE: a LUKS container (by its crypttab name), a
    /// ZFS encryption root (by dataset) or a GELI provider (by partition or
    /// GPT label path; NAME:1 for its key slot 1).
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
    /// --pools NAME[,NAME]: only these ZFS pools and their disks are
    /// planned; the layout's other pools are left untouched
    /// (layout.rs `only_pools`).  Empty: every pool.
    pub pools: Vec<String>,
    /// --new-machine: an encrypted Ubuntu Core restored to another TPM (or
    /// other Secure Boot variables) boots recover mode first
    /// (load/snapcore.rs).
    pub new_machine: bool,
    /// --luks-header-dir DIR: where the LUKS2 headers the dump kept are
    /// (`crate::luksheader`); else beside the layout.
    pub luks_header_dir: Option<String>,
    /// For tests: read the system load runs on from this tree, running no
    /// commands.
    pub sysroot: Option<String>,
    /// --json: the run writes bko-rescue's documents on standard output
    /// instead of text (docs/design/holt-json.md; jsonout.rs).
    pub json: bool,
    /// --expect-plan SHA256 (--json only): the plan document's `plan_sha256`
    /// this run must have, or it's a plan problem (J20).
    pub expect_plan: Option<String>,
    /// load's arguments as given, for the plan document's `args`.
    pub argv: Vec<String>,
}

impl Options {
    pub fn new(layout: &str) -> Self {
        Self {
            layout: layout.to_string(),
            through: None,
            only: None,
            commit: false,
            maps: vec![],
            erase: vec![],
            target: DEFAULT_TARGET.to_string(),
            fit_largest: None,
            new_uuids: false,
            reformats: vec![],
            key_files: vec![],
            no_nvram: false,
            removable: false,
            repo: None,
            password_file: None,
            snapshot: DEFAULT_SNAPSHOT.to_string(),
            archiver: DEFAULT_ARCHIVER.to_string(),
            btrfs_copy: false,
            pools: vec![],
            new_machine: false,
            luks_header_dir: None,
            sysroot: None,
            json: false,
            expect_plan: None,
            argv: vec![],
        }
    }

    /// The stages this run asks for, in order.  Neither --through nor
    /// --only: plan.
    pub fn stages(&self) -> Vec<Stage> {
        match (self.only, self.through) {
            (Some(s), _) => vec![s],
            (None, Some(s)) if s.writes() => {
                Stage::WRITING.into_iter().filter(|w| *w <= s).collect()
            }
            (None, Some(s)) => vec![s],
            (None, None) => vec![Stage::Plan],
        }
    }

    pub fn key_file(&self, name: &str) -> Option<&str> {
        self.key_files
            .iter()
            .find(|(n, _)| n == name)
            .map(|(_, f)| f.as_str())
    }
}

pub const USAGE: &str =
    "Usage: bko-holt load LAYOUT.json [--through STAGE | --only STAGE] [--commit]
                     [--map SRC=DST]... [--erase DEV]... [--fit-largest PCT]
                     [--target DIR] [--new-uuids] [--key-file NAME=FILE]...
                     [--reformat MOUNT=TYPE]...
                     [--no-nvram] [--removable] [--pools NAME[,NAME]]
                     [--luks-header-dir DIR] [--new-machine]
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
  --reformat MOUNT=TYPE
                    make the filesystem mounted at MOUNT as TYPE (ext4, xfs
                    or btrfs) instead of its own, on the same partition;
                    boot rewrites what names it (fstab, kernel command
                    lines).  Linux layouts only; plan says what changes
  --key-file NAME=FILE
                    a LUKS container's (crypttab name; for a kept header its
                    LUKS label too, ubuntu-data-enc) or ZFS encryption
                    root's (dataset) key or passphrase, instead of a prompt;
                    cryptsetup takes the file's bytes as they are, so a
                    passphrase's file must not end in a newline.  On
                    FreeBSD, a GELI provider's passphrase (NAME its
                    partition, vtbd0p3, or GPT label path, gpt/zfs0;
                    NAME:1 for key slot 1), or for one a key file alone
                    unlocks, that key file.  On OpenBSD, a softraid CRYPTO
                    volume's new passphrase (NAME its device in the dump,
                    sd1, or its DUID); bioctl takes the file's first line
                    (load adds the final newline bioctl wants, if missing)
  --no-nvram        boot writes no UEFI variables
  --removable       boot also installs the loader to EFI/BOOT/BOOTX64.EFI
  --luks-header-dir DIR
                    where the LUKS2 headers the dump kept are (bko-holt dump
                    --keep-luks-header; Ubuntu Core's always): written back
                    with luksHeaderRestore and opened with --key-file, no new
                    volume key.  Default: the layout's own directory.
                    `bko-holt luks-headers` fetches them from the backup
  --new-machine     Ubuntu Core with FDE onto another machine (another TPM,
                    or other Secure Boot variables): its first boot is recover
                    mode, which asks for the recovery key and unlocks the
                    data; run mode there needs a reinstall
  --pools NAME[,NAME]
                    restore only these ZFS pools (TrueNAS: boot-pool): the
                    layout's other pools and the disks they're on are left
                    untouched, and a target holding one is refused
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

/// The flags `parse_args` takes a value after: the table `--json`'s first pass
/// walks (`jsonout::find_json_flag`), so that `--snapshot --json` names a
/// snapshot and isn't the flag.  `parse_args` asserts every flag it takes a
/// value for is here, and a test that every one here takes one.
pub const VALUE_FLAGS: &[&str] = &[
    "--through",
    "--only",
    "--map",
    "--erase",
    "--target",
    "--fit-largest",
    "--reformat",
    "--key-file",
    "--repo",
    "--password-file",
    "--snapshot",
    "--archiver",
    "--btrfs-snapshots",
    "--pools",
    "--luks-header-dir",
    "--sysroot",
    "--expect-plan",
];

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
    let mut schema: Option<String> = None;
    let mut it = args.iter();
    let value = |it: &mut std::slice::Iter<'_, String>, flag: &str| {
        debug_assert!(
            VALUE_FLAGS.contains(&flag),
            "{flag} takes a value: add it to VALUE_FLAGS"
        );
        it.next()
            .cloned()
            .ok_or_else(|| format!("{flag} needs a value"))
    };
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
                    return Err(format!(
                        "--target {v}: give an absolute directory other than /"
                    ));
                }
                o.target = v.trim_end_matches('/').to_string();
            }
            "--fit-largest" => {
                let v = value(&mut it, a)?;
                let n: u64 = v.strip_suffix('%').unwrap_or(&v).parse().map_err(|_| {
                    format!("--fit-largest {v}: give a whole percentage, 1 to {FIT_MAX}")
                })?;
                if !(1..=FIT_MAX).contains(&n) {
                    return Err(format!(
                        "--fit-largest {v}: give a whole percentage, 1 to {FIT_MAX}"
                    ));
                }
                o.fit_largest = Some(n);
            }
            "--new-uuids" => o.new_uuids = true,
            "--reformat" => {
                let v = value(&mut it, a)?;
                let (m, ty) = pair(a, &v)?;
                if !m.starts_with('/') {
                    return Err(format!("--reformat {v}: MOUNT is a mount point, such as /"));
                }
                if !reformat::TYPES.contains(&ty.as_str()) {
                    return Err(format!(
                        "--reformat {v}: TYPE is one of {}",
                        reformat::TYPES.join(", ")
                    ));
                }
                let m = if m == "/" {
                    m
                } else {
                    m.trim_end_matches('/').to_string()
                };
                if o.reformats.iter().any(|(x, _)| *x == m) {
                    return Err(format!("--reformat {m}=...: {m} is given twice"));
                }
                o.reformats.push((m, ty));
            }
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
            "--pools" => {
                let v = value(&mut it, a)?;
                for n in v.split(',').map(str::trim) {
                    if n.is_empty() {
                        return Err(format!("--pools {v:?}: give pool names, comma-separated"));
                    }
                    if !o.pools.iter().any(|p| p == n) {
                        o.pools.push(n.to_string());
                    }
                }
            }
            "--new-machine" => o.new_machine = true,
            "--luks-header-dir" => o.luks_header_dir = Some(value(&mut it, a)?),
            "--sysroot" => o.sysroot = Some(value(&mut it, a)?),
            "--expect-plan" => {
                let v = value(&mut it, a)?;
                if v.len() != 64
                    || !v
                        .bytes()
                        .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
                {
                    return Err(format!(
                        "--expect-plan {v}: give the plan document's plan_sha256 (64 lower-case hex digits)"
                    ));
                }
                o.expect_plan = Some(v);
            }
            s if jsonout::is_json_flag(s) => {
                // A schema this bko-holt doesn't write: refused once every
                // argument is read, so a --help after it is still the help.
                if let Err(e) = jsonout::check_schema(s) {
                    schema.get_or_insert(e);
                }
                o.json = true;
            }
            s if s.starts_with('-') => return Err(format!("unknown argument {s:?}")),
            s => {
                if layout.is_some() {
                    return Err(format!("one layout file only, not also {s:?}"));
                }
                layout = Some(s.to_string());
            }
        }
    }
    if let Some(e) = schema {
        return Err(e);
    }
    if o.through.is_some() && o.only.is_some() {
        return Err("--through and --only don't go together".into());
    }
    if o.expect_plan.is_some() && !o.json {
        return Err(
            "--expect-plan is for --json runs: it checks the plan document's plan_sha256".into(),
        );
    }
    o.argv = args.to_vec();
    if o.repo.is_none()
        && (o.password_file.is_some()
            || o.snapshot != DEFAULT_SNAPSHOT
            || o.archiver != DEFAULT_ARCHIVER)
    {
        return Err(
            "--password-file, --snapshot and --archiver are for restore; give --repo too".into(),
        );
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
/// Each run is recorded in `RUN_DIR` for `bko-holt report-bug`.
pub fn main(args: &[String]) -> u8 {
    let (code, err) = main_inner(args);
    eprint!("{err}");
    if records_itself(args) {
        // Under --json a password in --repo's URL is refused and never
        // recorded (holt-json.md, "args"); text mode records ARGS as ever.
        if jsonout::find_json_flag(args, VALUE_FLAGS).is_some() {
            crate::bugreport::record_load_run(&plan::args_redacted(args), code, &err);
        } else {
            crate::bugreport::record_load_run(args, code, &err);
        }
    }
    code
}

/// Whether a load run of ARGS records itself in RUN_DIR/last-load.json for
/// report-bug: every run but --help in text mode, as ever; under --json,
/// not a plan or a status run (holt-json.md, "Bug reports": the status
/// bko-rescue reads right after a failed run mustn't take its place).  A
/// --json run whose arguments don't parse records itself: it is no plan
/// or status run that ran.
fn records_itself(args: &[String]) -> bool {
    match parse_args(args) {
        Ok(Parsed::Help) => false,
        Ok(Parsed::Run(o)) if o.json => {
            !matches!(o.stages().as_slice(), [Stage::Plan | Stage::Status])
        }
        _ => true,
    }
}

/// The exit status and standard error; standard output is printed.
fn main_inner(args: &[String]) -> (u8, String) {
    // --json in a flag's place, found before parsing, so that a bad
    // argument is reported as a bko-holt-error document too.
    let json = jsonout::find_json_flag(args, VALUE_FLAGS);
    // Under --json, a refusal's document on standard output and its text
    // line on standard error (with TAIL after it, the usage for a bad
    // argument); in text mode the line and TAIL alone, as ever.
    let refuse = |exit: u8, code: jsonout::ErrorCode, line: String, tail: &str| -> (u8, String) {
        if json.is_some() {
            jsonout::write_stdout(&jsonout::error_doc(exit, code, &line));
        }
        (exit, format!("{line}\n{tail}"))
    };
    let parsed = parse_args(args);
    if let Ok(Parsed::Help) = parsed {
        // For people, --json or not.
        print!("{USAGE}");
        return (EXIT_OK, String::new());
    }
    if let Some(e) = jsonout::schema_error(args, VALUE_FLAGS) {
        return refuse(
            EXIT_BAD_ARGS,
            jsonout::ErrorCode::SchemaUnknown,
            format!("bko-holt load: {e}"),
            &format!("\n{USAGE}"),
        );
    }
    let opts = match parsed {
        Ok(Parsed::Run(o)) => o,
        Ok(Parsed::Help) => return (EXIT_OK, String::new()),
        Err(e) => {
            return refuse(
                EXIT_BAD_ARGS,
                jsonout::ErrorCode::BadArgs,
                format!("bko-holt load: {e}"),
                &format!("\n{USAGE}"),
            );
        }
    };
    // The dry-run lock: after --help, before the layout is read.
    if opts.commit {
        if let Some(v) = jsonout::no_commit() {
            return refuse(
                EXIT_BAD_ARGS,
                jsonout::ErrorCode::NoCommit,
                jsonout::no_commit_refusal(&v),
                "",
            );
        }
    }
    let text = match crate::bytes::read_to_string_bounded(
        std::path::Path::new(&opts.layout),
        crate::LAYOUT_MAX,
    ) {
        Ok(t) => t,
        Err(e) => {
            return refuse(
                EXIT_BAD_ARGS,
                jsonout::ErrorCode::LayoutUnreadable,
                format!("bko-holt load: {}: {e}", opts.layout),
                "",
            );
        }
    };
    let sys = match &opts.sysroot {
        Some(d) if !std::path::Path::new(d).is_dir() => {
            return refuse(
                EXIT_BAD_ARGS,
                jsonout::ErrorCode::BadArgs,
                format!("bko-holt load: --sysroot {d}: not a directory"),
                "",
            );
        }
        Some(d) => Sys::new(d, Box::new(FakeRunner::default()), crate::sys::euid() == 0),
        None => Sys::host(),
    };
    if opts.json {
        // No core dumps (cryptsetup and the archiver hold keys), and for a
        // writing run, SIGTERM's stop and the panic hook (exec::arm).
        crate::sys::no_core_dumps();
        if !matches!(opts.stages().as_slice(), [Stage::Plan | Stage::Status]) {
            exec::arm();
        }
    }
    let (code, out, err) = run(&opts, &text, &sys);
    if opts.json {
        jsonout::write_stdout(&out);
    } else {
        print!("{out}");
    }
    (code, err)
}

/// Runs what `opts` asks for on `sys` (the system load runs on, whose disks
/// are the targets): the exit status, standard output and standard error,
/// neither of them with a password= item's value in it (a dry run's copy
/// of lilo.conf, a Problem that quotes an item; `crate::redact::`
/// `scrub_passwords`, `RustAudit1` HD1-01).
pub fn run(opts: &Options, layout_text: &str, sys: &Sys) -> (u8, String, String) {
    let (code, out, err) = run_raw(opts, layout_text, sys);
    let shown = |t: &str| crate::redact::scrub_passwords(t).into_owned();
    (code, shown(&out), shown(&err))
}

fn run_raw(opts: &Options, layout_text: &str, sys: &Sys) -> (u8, String, String) {
    // A refusal: under --json its document is the whole of standard output.
    let refuse = |exit: u8, code: jsonout::ErrorCode, line: String| -> (u8, String, String) {
        let out = if opts.json {
            jsonout::error_doc(exit, code, &line)
        } else {
            String::new()
        };
        (exit, out, line + "\n")
    };
    let json = match crate::json::parse(layout_text) {
        Ok(v) => v,
        Err(e) => {
            return refuse(
                EXIT_UNSUPPORTED,
                jsonout::ErrorCode::LayoutNotJson,
                format!("bko-holt load: {}: not JSON ({e})", opts.layout),
            );
        }
    };
    // --reformat: every stage works from the edited layout (reformat.rs).
    let json = if opts.reformats.is_empty() {
        json
    } else {
        reformat::apply(json.clone(), &reformat::work_out(&json, &opts.reformats))
    };
    let layout = match layout::Layout::from_json(json) {
        Ok(l) => l,
        Err(e) => {
            return refuse(
                EXIT_UNSUPPORTED,
                jsonout::ErrorCode::LayoutRefused,
                format!("bko-holt load: {}: {e}", opts.layout),
            );
        }
    };
    // A dump of another architecture than this rescue system's is refused
    // here, before any stage (plan and status too) reads or writes a disk
    // (crate::arch).
    if let Err(e) = crate::arch::check_load(&layout.v, sys) {
        return refuse(
            EXIT_UNSUPPORTED,
            jsonout::ErrorCode::ArchMismatch,
            format!("bko-holt load: {}: {e}", opts.layout),
        );
    }
    let stages = opts.stages();
    if opts.json {
        // The writing stages' events go out as they happen; the documents
        // are written whole.
        match stages.as_slice() {
            [Stage::Plan] => return plan::json_run(&layout, opts, sys, layout_text),
            [Stage::Status] => return status::json_run(&layout, opts, sys),
            _ => {
                return exec::json_run(&layout, opts, sys, &stages, &mut |l| {
                    jsonout::write_stdout(l);
                });
            }
        }
    }
    match stages.as_slice() {
        [Stage::Plan] => plan::run(&layout, opts, sys),
        [Stage::Status] => status::run(&layout, opts, sys),
        _ => exec::run(&layout, opts, sys, &stages),
    }
}

/// The source-to-target disk pairs `plan` makes of `LAYOUT_TEXT` here, with
/// OPTS's --map or else by WWID or serial (report-bug gives them as --map to
/// a run on the redacted layout, whose serials match no disk).
pub fn disk_pairs(opts: &Options, layout_text: &str, sys: &Sys) -> Vec<(String, String)> {
    let Some(layout) = crate::json::parse(layout_text)
        .ok()
        .and_then(|j| layout::Layout::from_json(j).ok())
    else {
        return Vec::new();
    };
    let system = target::scan(sys, opts, &target::layout_pools(&layout));
    target::map(&layout, opts, &system, sys).pairs
}

/// The suffix `report-bug` gives a boot-area or ESP image's key when it
/// leaves the image out of a report (`KEY_omitted`: its size and CRC-32).
pub const OMITTED: &str = "_omitted";

/// Whether V has an image `report-bug` left out: such a layout can be
/// planned, but not written.
pub fn has_omitted(v: &crate::json::Value) -> bool {
    use crate::json::Value;
    match v {
        Value::Obj(kv) => kv
            .iter()
            .any(|(k, x)| k.ends_with(OMITTED) || has_omitted(x)),
        Value::Arr(a) => a.iter().any(has_omitted),
        _ => false,
    }
}

/// A word for a shell command line: quoted when it needs to be.
pub fn shell_quote(s: &str) -> String {
    if !s.is_empty()
        && s.bytes()
            .all(|b| b.is_ascii_alphanumeric() || b"-_./:=,+@%^".contains(&b))
    {
        s.to_string()
    } else {
        format!("'{}'", s.replace('\'', "'\\''"))
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn args(a: &[&str]) -> Vec<String> {
        a.iter().map(ToString::to_string).collect()
    }

    fn opts(a: &[&str]) -> Options {
        match parse_args(&args(a)).unwrap() {
            Parsed::Run(o) => o,
            Parsed::Help => panic!("help"),
        }
    }

    /// slackware15's dump (holt/tests/corpus) with a password= in its
    /// lilo.conf's global items and another in its image's: neither is in
    /// what load prints, its plan, a dry run of the boot stage (which
    /// writes the copy of lilo.conf lilo runs on) or the plan document
    /// (`RustAudit1` HD1-01).
    #[test]
    fn lilo_passwords_are_never_shown() {
        use crate::sys::FakeRunner;
        let l = include_str!("../../tests/corpus/slackware15/src.json");
        let l = l
            .replacen(
                "\"disk=/dev/vda\",",
                "\"password=s3cret\", \"disk=/dev/vda\",",
                1,
            )
            .replacen(
                "\"label=Linux\",",
                "\"label=Linux\", \"password=my img pass\",",
                1,
            );
        assert!(l.contains("s3cret") && l.contains("my img pass"));
        let t = crate::testutil::Tree::new("load-lilo-password");
        t.file("/proc/self/mountinfo", "")
            .file("/proc/swaps", "Filename\tType\tSize\tUsed\tPriority\n");
        t.disk("vdb", "253:16", &[], 512)
            .file("/sys/block/vdb/size", "41943040\n");
        let s = Sys::new(t.root(), Box::new(FakeRunner::default()), true);
        let run_of = |a: &[&str]| {
            let mut v = vec!["layout.json".to_string(), "--map".into(), "vda=vdb".into()];
            v.extend(a.iter().map(ToString::to_string));
            let Ok(Parsed::Run(o)) = parse_args(&v) else {
                panic!("{v:?}")
            };
            run(&o, &l, &s)
        };
        let shown = |what: &str, out: &str, err: &str| {
            for gone in ["s3cret", "img pass"] {
                assert!(
                    !out.contains(gone) && !err.contains(gone),
                    "{what}: {gone} in:\n{out}{err}"
                );
            }
        };
        let (_, out, err) = run_of(&[]);
        shown("the plan", &out, &err);
        assert!(
            out.contains("lilo.conf.bko-holt <<'EOF'\n")
                && out.contains("password=\"<password>\"\n")
                && out.contains("  password=\"<password>\"\n"),
            "{out}{err}"
        );
        // The dry run of the boot stage, the restored tree there and the
        // rescue's tools.
        t.file("/mnt/bko-target/etc/fstab", "");
        for p in [
            "wipefs", "dd", "sh", "udevadm", "sfdisk", "mkswap", "mke2fs", "tune2fs", "mkdir",
            "mount", "chroot", "rm", "cp", "umount", "bash",
        ] {
            t.file(&format!("/usr/sbin/{p}"), "");
        }
        let (_, out, err) = run_of(&["--only", "boot"]);
        shown("the dry run", &out, &err);
        assert!(
            out.contains("lilo.conf.bko-holt <<'EOF'\n")
                && out.contains("password=\"<password>\"\n"),
            "{out}{err}"
        );
        let (_, out, err) = run_of(&["--json"]);
        shown("the plan document", &out, &err);
    }

    /// `RT10i`: under --json, plan and status runs leave last-load.json to
    /// the writing run before them; text mode records every run but
    /// --help, as ever.
    #[test]
    fn which_runs_record_themselves() {
        let rec = |a: &[&str]| records_itself(&args(a));
        for a in [
            &["l.json"][..],
            &["l.json", "--only", "status"],
            &["l.json", "--through", "close"],
            &["l.json", "--through", "format", "--commit"],
            &["l.json", "--bogus"],
        ] {
            assert!(rec(a), "text mode: {a:?}");
        }
        assert!(!rec(&["l.json", "--help"]) && !rec(&["--json", "-h"]));
        assert!(!rec(&["l.json", "--json"]), "a --json plan");
        assert!(
            !rec(&["l.json", "--only", "status", "--json"]),
            "a --json status"
        );
        assert!(
            !rec(&["l.json", "--through", "plan", "--json"]),
            "--through plan is a plan"
        );
        assert!(
            rec(&["l.json", "--through", "close", "--json"]),
            "a --json writing run, --commit or not"
        );
        assert!(rec(&["l.json", "--only", "boot", "--json", "--commit"]));
        assert!(
            rec(&["l.json", "--json", "--frob"]),
            "arguments that don't parse: no plan or status ran"
        );
        // `--json` as --snapshot's value is a text run's.
        assert!(rec(&["l.json", "--repo", "/r", "--snapshot", "--json"]));
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
        assert_eq!(
            opts(&["l", "--through", "mount"]).stages(),
            vec![
                Stage::Partition,
                Stage::Assemble,
                Stage::Format,
                Stage::Mount
            ]
        );
        assert_eq!(
            opts(&["l", "--through", "close", "--repo", "/r"]).stages(),
            Stage::WRITING.to_vec()
        );
        assert_eq!(opts(&["l", "--through", "mount"]).repo, None);
        assert_eq!(opts(&["l", "--only", "boot"]).stages(), vec![Stage::Boot]);
        assert_eq!(
            opts(&["l", "--only", "status"]).stages(),
            vec![Stage::Status]
        );
        assert_eq!(
            opts(&["l", "--through", "plan"]).stages(),
            vec![Stage::Plan]
        );
    }

    #[test]
    fn every_flag() {
        let o = opts(&[
            "--map",
            "vda=/dev/sdb",
            "x.json",
            "--map",
            "vdb=/dev/disk/by-id/ata-X",
            "--erase",
            "/dev/sdb",
            "--target",
            "/mnt/t/",
            "--new-uuids",
            "--fit-largest",
            "15%",
            "--key-file",
            "root_crypt=/tmp/k",
            "--no-nvram",
            "--removable",
            "--commit",
            "--only",
            "partition",
            "--sysroot",
            "/tmp/fake",
            "--repo",
            "sftp:nas:/bko",
            "--password-file",
            "/run/pw",
            "--snapshot",
            "1a2b3c",
            "--archiver",
            "/usr/local/bin/rustic-bko",
        ]);
        assert_eq!(o.repo.as_deref(), Some("sftp:nas:/bko"));
        assert_eq!(o.password_file.as_deref(), Some("/run/pw"));
        assert_eq!(o.snapshot, "1a2b3c");
        assert_eq!(o.archiver, "/usr/local/bin/rustic-bko");
        assert_eq!(o.layout, "x.json");
        assert_eq!(
            o.maps,
            vec![
                ("vda".to_string(), "/dev/sdb".to_string()),
                ("vdb".to_string(), "/dev/disk/by-id/ata-X".to_string())
            ]
        );
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
            assert!(
                err(&["a", "--fit-largest", v]).contains("whole percentage, 1 to 50"),
                "{v:?}"
            );
        }
        assert_eq!(parse_args(&args(&["--help"])).unwrap(), Parsed::Help);
    }

    #[test]
    fn json_argument() {
        assert!(!opts(&["l"]).json);
        assert!(opts(&["l", "--json"]).json);
        assert!(opts(&["--json=1", "l"]).json);
        let err = |a: &[&str]| parse_args(&args(a)).unwrap_err();
        assert!(err(&["l", "--json=2"]).contains("writes schema 1"));
        // A flag's value is its value.
        let o = opts(&["l", "--repo", "/r", "--snapshot", "--json"]);
        assert!(!o.json);
        assert_eq!(o.snapshot, "--json");
        assert_eq!(
            jsonout::find_json_flag(
                &args(&["l", "--repo", "/r", "--snapshot", "--json"]),
                VALUE_FLAGS
            ),
            None
        );
        assert_eq!(
            parse_args(&args(&["l", "--json", "--help"])).unwrap(),
            Parsed::Help
        );
        // The help wherever it stands, a bad schema too.
        assert_eq!(
            parse_args(&args(&["l", "--json=2", "--help"])).unwrap(),
            Parsed::Help
        );
        assert!(err(&["l", "--json", "--json=2"]).contains("writes schema 1"));
    }

    #[test]
    fn value_flags_table() {
        // Every flag in the table takes a value (parse_args's debug_assert
        // holds the other way: every flag it takes a value for is listed).
        for f in VALUE_FLAGS {
            let e = parse_args(&args(&["l", f])).unwrap_err();
            assert!(e.contains("needs a value"), "{f}: {e}");
        }
        // And none of the switches does.
        for f in [
            "--commit",
            "--new-uuids",
            "--no-nvram",
            "--removable",
            "--new-machine",
            "--json",
        ] {
            assert_eq!(opts(&[f, "l"]).layout, "l", "{f}");
        }
    }

    #[test]
    fn json_refusals() {
        let t = crate::testutil::Tree::new("load-json");
        let s = Sys::new(t.root(), Box::new(FakeRunner::default()), true);
        let doc = |out: &str| crate::json::parse(out).unwrap();
        // Text mode: nothing on standard output, as ever.
        let (code, out, err) = run(&opts(&["l.json"]), "{not json", &s);
        assert_eq!((code, out.as_str()), (EXIT_UNSUPPORTED, ""));
        let (jcode, jout, jerr) = run(&opts(&["l.json", "--json"]), "{not json", &s);
        assert_eq!((jcode, &jerr), (code, &err));
        assert_eq!(doc(&jout).get_str("code"), Some("layout_not_json"));
        assert_eq!(doc(&jout).get_str("text"), Some(err.trim_end()));
        let e = crate::json::parse("{not json").unwrap_err();
        assert_eq!(
            err,
            format!(
                "bko-holt load: l.json: not JSON ({} at byte {})\n",
                e.message, e.offset
            )
        );
        let (code, out, _) = run(&opts(&["l.json", "--json"]), r#"{"format": "x"}"#, &s);
        assert_eq!(code, EXIT_UNSUPPORTED);
        assert_eq!(doc(&out).get_str("code"), Some("layout_refused"));
        // The writing stages write their events on standard output as they
        // happen (RT10f): json_cli.rs runs them as a program, and exec.rs's
        // tests read them from a sink.
    }

    #[test]
    fn reformat_arguments() {
        let o = opts(&["l", "--reformat", "/=xfs", "--reformat", "/srv/=ext4"]);
        assert_eq!(
            o.reformats,
            vec![
                ("/".to_string(), "xfs".to_string()),
                ("/srv".to_string(), "ext4".to_string())
            ]
        );
        assert!(opts(&["l"]).reformats.is_empty());
        let err = |a: &[&str]| parse_args(&args(a)).unwrap_err();
        assert!(err(&["l", "--reformat", "root=xfs"]).contains("MOUNT is a mount point"));
        assert!(err(&["l", "--reformat", "/=zfs"]).contains("TYPE is one of ext4, xfs, btrfs"));
        assert!(err(&["l", "--reformat", "/="]).contains("NAME=VALUE"));
        assert!(
            err(&["l", "--reformat", "/=xfs", "--reformat", "/=ext4"]).contains("/ is given twice")
        );
    }

    #[test]
    fn writing_stages_check_first() {
        let t = crate::testutil::Tree::new("load-run");
        let s = Sys::new(t.root(), Box::new(FakeRunner::default()), true);
        let layout = r#"{"format": "bko-holt", "schema": 1, "os": {"id": "debian", "version_id": "13"}, "storage": {"disks": []}}"#;
        // The same checks as plan's: this layout mounts no /.
        let (code, out, err) = run(
            &opts(&["l.json", "--through", "mount", "--commit"]),
            layout,
            &s,
        );
        assert_eq!(code, EXIT_PLAN_PROBLEMS);
        assert!(out.is_empty());
        assert!(
            err.contains("nothing in fstab or the ZFS datasets mounts /"),
            "{err}"
        );
        assert!(err.contains("nothing was written"), "{err}");
        let (code, _, err) = run(&opts(&["l.json"]), "{not json", &s);
        assert_eq!(code, EXIT_UNSUPPORTED);
        assert!(err.contains("not JSON"));
    }

    #[test]
    fn quoting() {
        assert_eq!(shell_quote("/dev/sda1"), "/dev/sda1");
        assert_eq!(
            shell_quote("feature@encryption=enabled"),
            "feature@encryption=enabled"
        );
        assert_eq!(shell_quote("EFI System"), "'EFI System'");
        assert_eq!(shell_quote("it's"), "'it'\\''s'");
        assert_eq!(shell_quote(""), "''");
        assert_eq!(
            shell_quote("\\EFI\\DEBIAN\\SHIMX64.EFI"),
            "'\\EFI\\DEBIAN\\SHIMX64.EFI'"
        );
    }
}
