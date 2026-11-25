// As in lib.rs: each binary is a crate of its own, which lib.rs's attribute
// does not reach.
#![cfg_attr(not(test), warn(clippy::unwrap_used, clippy::expect_used))]

use bko_holt::load::jsonout;
use bko_holt::sys::{FakeRunner, Sys};
use std::io::Write;
use std::process::ExitCode;

const USAGE: &str = "Usage: bko-holt dump [--compact] [--sysroot DIR]
       bko-holt load LAYOUT.json [--through STAGE | --only STAGE] [--commit] ...
       bko-holt report-bug [--output FILE] [--stderr FILE]... [-- LOAD-ARGS...]
       bko-holt recovery-key-file OUT
       bko-holt stick --json
       bko-holt recovery-words OUT --json
       bko-holt -h | --help | -V | --version | --license

  dump        writes a JSON description of this system's disk layout
              and boot setup to standard output
  load        rebuilds disks from such a description, in stages
  report-bug  gathers what a BackOtter bug report needs (versions, dump,
              the last load run, logs) into one redacted file to attach
  recovery-key-file
              turns Ubuntu Core's recovery key (on standard input) into the
              key file cryptsetup and `load --key-file` take
  stick       reads the rescue stick's config partition (its machines),
              as JSON, for bko-rescue
  recovery-words
              checks a BackOtter recovery key (its words on standard input)
              and writes the repository password it stands for, for
              bko-rescue

`bko-holt dump --help`, `bko-holt load --help` and `bko-holt report-bug --help`
say more.

bko-holt is free software under the GNU GPL, version 3 or later, and comes
with NO WARRANTY.  `bko-holt --license` prints the license.
Source: https://github.com/mchobbit/backotter
";

/// The GPL's text, carried in the binary so every image that ships bko-holt
/// ships its license too.
const LICENSE: &str = include_str!("../LICENSE");

const DUMP_USAGE: &str = "Usage: bko-holt dump [--compact] [--only-disk NAME...] [--sysroot DIR | --disk DEV...]
                     [--keep-luks-header DEV]... [--luks-header-dir DIR]

Writes a JSON description of this system's disk layout and boot
setup to standard output: disks and partition tables, boot code, md RAID,
device-mapper, LVM, LUKS, ZFS and btrfs, filesystems and their features,
mounts, fstab and crypttab, UEFI or BIOS boot entries, the boot loader and
its configuration, kernels and initramfs.

Run it as root: partition tables, superblocks and LVM/ZFS labels are read
from the devices themselves.  As another user it falls back to udev's
records and says so under \"warnings\".

  --compact       one line of JSON instead of an indented document
  --only-disk NAME
                  leave out every other whole disk and its partitions (nvme0n1;
                  repeat for more), so that a rescue stick or a backup drive
                  plugged in stays out of the layout
  --disk DEV      describe the system on these disks instead (Linux only):
                  one that can't run bko-holt itself (Redox, Sculpt OS),
                  read off its disks attached here
  --sysroot DIR   describe the tree under DIR (its own sys/, proc/, dev/,
                  etc/, run/ and boot/) instead of this system, running no
                  commands; for tests
  --keep-luks-header DEV
                  save the LUKS2 header on DEV (vda5) byte for byte, for load
                  to write back instead of making a new one (its volume key,
                  keyslots and tokens kept); always done for Ubuntu Core's
                  encrypted partitions
  --luks-header-dir DIR
                  where those header files go (default /run/bko-holt/luks-headers);
                  keep them beside the dump: load looks for them there
  -h, --help      show this help

Exit status: 0 on success, 2 for bad arguments, 3 when the system isn't
one bko-holt supports (the message then lists those it does).
SOURCE_DATE_EPOCH, when set, fixes the report's timestamp.
";

/// The hidden helpers load's stages run that write to a disk or a tree:
/// under BKO_HOLT_NO_COMMIT they refuse to run at all (load never runs them
/// without --commit, which the variable refuses, so only a bug or a hand
/// would).
const WRITING_HELPERS: [&str; 11] = ["snapcore-boot-areas", "md-lbs", "umount-tree", "efi-dedupe", "grub-prefix", "luks1-digest", "raw-write", "write-table", "rewrite", "fat-set", "clear-restricting-flags"];

/// The flags dump takes a value after, for `--json`'s first pass.
const DUMP_VALUE_FLAGS: &[&str] = &["--disk", "--only-disk", "--keep-luks-header", "--luks-header-dir", "--sysroot"];

fn main() -> ExitCode {
    let args: Vec<String> = std::env::args().skip(1).collect();
    if let Some(h) = args.first().filter(|a| WRITING_HELPERS.contains(&a.as_str())) {
        if jsonout::no_commit().is_some() {
            eprintln!("{}", jsonout::helper_refusal(h));
            return ExitCode::from(2);
        }
    }
    match args.first().map(String::as_str) {
        Some("dump") => dump(&args[1..]),
        Some("load") => {
            let code = bko_holt::load::main(&args[1..]);
            if matches!(code, bko_holt::load::EXIT_FAILED | bko_holt::load::EXIT_UNSUPPORTED) {
                eprint!("{}", report_hint());
            }
            ExitCode::from(code)
        }
        Some("report-bug") => bko_holt::bugreport::main(&args[1..]),
        Some("recovery-key-file") => ExitCode::from(bko_holt::snapcore::recovery_key_main(&args[1..])),
        Some("stick") => ExitCode::from(bko_holt::stick::main(&args[1..])),
        Some("recovery-words") => ExitCode::from(bko_holt::recoverycmd::main(&args[1..])),
        // Not in the usage: run by load's close, partition and boot stages.
        Some("snapcore-boot-areas") => ExitCode::from(bko_holt::load::snapcore::boot_areas_main(&args[1..])),
        Some("md-lbs") => ExitCode::from(bko_holt::mdsb::main(&args[1..])),
        Some("umount-tree") => ExitCode::from(bko_holt::load::umounttree::main(&args[1..])),
        Some("efi-dedupe") => ExitCode::from(bko_holt::load::efidedupe::main(&args[1..])),
        Some("grub-prefix") => ExitCode::from(bko_holt::load::grubprefix::main(&args[1..])),
        Some("luks1-digest") => ExitCode::from(bko_holt::load::luks1digest::main(&args[1..])),
        Some("raw-write") => ExitCode::from(bko_holt::load::rawvol::main(&args[1..])),
        Some("write-table") => ExitCode::from(bko_holt::load::writetable::main(&args[1..])),
        Some("rewrite") => ExitCode::from(bko_holt::load::rewrite::main(&args[1..])),
        Some("fat-set") => ExitCode::from(bko_holt::load::fatset::main(&args[1..])),
        Some("clear-restricting-flags") => ExitCode::from(bko_holt::load::unflag::main(&args[1..])),
        Some("-h" | "--help") => {
            print!("{USAGE}");
            ExitCode::SUCCESS
        }
        // `--version --json` (or -V --json): which documents and schemas
        // this bko-holt speaks (J1).  An older one prints its text line to
        // this too, which bko-rescue reads as "too old".
        Some("-V" | "--version") if args.get(1).is_some_and(|a| jsonout::is_json_flag(a)) => {
            let flag = &args[1];
            match jsonout::check_schema(flag) {
                Ok(()) => {
                    jsonout::write_stdout(&jsonout::api_doc());
                    ExitCode::SUCCESS
                }
                Err(e) => {
                    let line = format!("bko-holt: {e}");
                    jsonout::write_stdout(&jsonout::error_doc(2, jsonout::ErrorCode::SchemaUnknown, &line));
                    eprintln!("{line}");
                    ExitCode::from(2)
                }
            }
        }
        Some("-V" | "--version") => {
            println!("bko-holt {}", env!("CARGO_PKG_VERSION"));
            ExitCode::SUCCESS
        }
        Some("--license") => {
            print!("{LICENSE}");
            ExitCode::SUCCESS
        }
        Some(other) => {
            eprintln!("bko-holt: unknown argument {other:?}\n\n{USAGE}");
            ExitCode::from(2)
        }
        None => {
            eprintln!("bko-holt: say dump or load\n\n{USAGE}");
            ExitCode::from(2)
        }
    }
}

/// After a fatal error: how to report it, if it's a bug.
fn report_hint() -> String {
    format!(
        "bko-holt: if this looks like a bug in BackOtter, `bko-holt report-bug` gathers what a report\nneeds into one redacted file; attach it to an issue at {}\n",
        bko_holt::bugreport::ISSUES
    )
}

fn dump(args: &[String]) -> ExitCode {
    // dump writes JSON already: --json is refused, as a document; beside
    // --help, the help, which is for people.
    if let Some(flag) = jsonout::find_json_flag(args, DUMP_VALUE_FLAGS) {
        if jsonout::asks_for_help(args, DUMP_VALUE_FLAGS) {
            print!("{DUMP_USAGE}");
            return ExitCode::SUCCESS;
        }
        let line = format!("bko-holt dump: {flag}: dump writes JSON already; run it without --json");
        jsonout::write_stdout(&jsonout::error_doc(2, jsonout::ErrorCode::JsonNotHere, &line));
        eprintln!("{line}");
        return ExitCode::from(2);
    }
    let mut compact = false;
    let mut sysroot: Option<String> = None;
    let mut disks: Vec<String> = Vec::new();
    let mut only: Vec<String> = Vec::new();
    let mut luks = bko_holt::luksheader::Options::default();
    let mut args = args.iter();
    while let Some(a) = args.next() {
        match a.as_str() {
            "--compact" => compact = true,
            "--disk" => match args.next() {
                Some(d) => disks.push(d.clone()),
                None => {
                    eprintln!("bko-holt dump: --disk needs a device\n\n{DUMP_USAGE}");
                    return ExitCode::from(2);
                }
            },
            "--only-disk" => match args.next() {
                Some(d) => only.push(d.trim_start_matches("/dev/").to_string()),
                None => {
                    eprintln!("bko-holt dump: --only-disk needs a disk's name\n\n{DUMP_USAGE}");
                    return ExitCode::from(2);
                }
            },
            "--keep-luks-header" => match args.next() {
                Some(d) => luks.keep.push(d.clone()),
                None => {
                    eprintln!("bko-holt dump: --keep-luks-header needs a device\n\n{DUMP_USAGE}");
                    return ExitCode::from(2);
                }
            },
            "--luks-header-dir" => match args.next() {
                Some(d) if d.starts_with('/') => luks.dir = Some(d.clone()),
                _ => {
                    eprintln!("bko-holt dump: --luks-header-dir needs an absolute directory\n\n{DUMP_USAGE}");
                    return ExitCode::from(2);
                }
            },
            "--sysroot" => match args.next() {
                Some(d) => sysroot = Some(d.clone()),
                None => {
                    eprintln!("bko-holt dump: --sysroot needs a directory\n\n{DUMP_USAGE}");
                    return ExitCode::from(2);
                }
            },
            "-h" | "--help" => {
                print!("{DUMP_USAGE}");
                return ExitCode::SUCCESS;
            }
            other => {
                eprintln!("bko-holt dump: unknown argument {other:?}\n\n{DUMP_USAGE}");
                return ExitCode::from(2);
            }
        }
    }
    let sys = match sysroot {
        Some(d) => {
            if !std::path::Path::new(&d).is_dir() {
                eprintln!("bko-holt dump: --sysroot {d}: not a directory");
                return ExitCode::from(2);
            }
            Sys::new(d, Box::new(FakeRunner::default()), bko_holt::sys::euid() == 0)
        }
        None => Sys::host(),
    };
    if !only.is_empty() && !disks.is_empty() {
        eprintln!("bko-holt dump: --only-disk and --disk don't go together\n\n{DUMP_USAGE}");
        return ExitCode::from(2);
    }
    if let Some(n) = bko_holt::report::unknown_disk(&sys, &only) {
        eprintln!("bko-holt dump: --only-disk {n}: no such whole disk here");
        return ExitCode::from(2);
    }
    if !disks.is_empty() && (!luks.keep.is_empty() || luks.dir.is_some()) {
        eprintln!("bko-holt dump: --keep-luks-header and --luks-header-dir don't go with --disk\n\n{DUMP_USAGE}");
        return ExitCode::from(2);
    }
    let result = if disks.is_empty() { bko_holt::dump_with(&sys, &only, &luks) } else { bko_holt::other::offline::report(&sys, &disks, &bko_holt::timestamp()) };
    let report = match result {
        Ok(r) => r,
        Err(e) => {
            eprintln!("bko-holt dump: {e}");
            eprint!("{}", report_hint());
            return ExitCode::from(3);
        }
    };
    let text = if compact { report.compact() + "\n" } else { report.pretty() };
    // Never a recovery key, whatever a file the dump read held.
    let text = bko_holt::redact::scrub_recovery_keys(&text);
    if text.len() as u64 > bko_holt::LAYOUT_MAX {
        eprintln!("bko-holt dump: the layout is {} bytes, more than load reads ({}): not written", text.len(), bko_holt::LAYOUT_MAX);
        eprint!("{}", report_hint());
        return ExitCode::from(3);
    }
    let mut out = std::io::stdout().lock();
    if out.write_all(text.as_bytes()).and_then(|_| out.flush()).is_err() {
        return ExitCode::from(1);
    }
    ExitCode::SUCCESS
}
