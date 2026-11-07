use bko_holt::sys::{FakeRunner, Sys};
use std::io::Write;
use std::process::ExitCode;

const USAGE: &str = "Usage: bko-holt dump [--compact] [--sysroot DIR]
       bko-holt load LAYOUT.json [--through STAGE | --only STAGE] [--commit] ...
       bko-holt -h | --help | -V | --version

  dump   writes a JSON description of this system's disk layout
         and boot setup to standard output
  load   rebuilds disks from such a description, in stages

`bko-holt dump --help` and `bko-holt load --help` say more.
";

const DUMP_USAGE: &str = "Usage: bko-holt dump [--compact] [--sysroot DIR | --disk DEV...]

Writes a JSON description of this system's disk layout and boot
setup to standard output: disks and partition tables, boot code, md RAID,
device-mapper, LVM, LUKS, ZFS and btrfs, filesystems and their features,
mounts, fstab and crypttab, UEFI or BIOS boot entries, the boot loader and
its configuration, kernels and initramfs.

Run it as root: partition tables, superblocks and LVM/ZFS labels are read
from the devices themselves.  As another user it falls back to udev's
records and says so under \"warnings\".

  --compact       one line of JSON instead of an indented document
  --disk DEV      describe the system on these disks instead (Linux only):
                  one that can't run bko-holt itself (Redox, Sculpt OS),
                  read off its disks attached here
  --sysroot DIR   describe the tree under DIR (its own sys/, proc/, dev/,
                  etc/, run/ and boot/) instead of this system, running no
                  commands; for tests
  -h, --help      show this help

Exit status: 0 on success, 2 for bad arguments, 3 when the system isn't
one bko-holt supports (the message then lists those it does).
SOURCE_DATE_EPOCH, when set, fixes the report's timestamp.
";

fn main() -> ExitCode {
    let args: Vec<String> = std::env::args().skip(1).collect();
    match args.first().map(String::as_str) {
        Some("dump") => dump(&args[1..]),
        Some("load") => ExitCode::from(bko_holt::load::main(&args[1..])),
        // Not in the usage: run by load's close stage.
        Some("md-lbs") => ExitCode::from(bko_holt::mdsb::main(&args[1..])),
        Some("write-table") => ExitCode::from(bko_holt::load::writetable::main(&args[1..])),
        Some("-h" | "--help") => {
            print!("{USAGE}");
            ExitCode::SUCCESS
        }
        Some("-V" | "--version") => {
            println!("bko-holt {}", env!("CARGO_PKG_VERSION"));
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

fn dump(args: &[String]) -> ExitCode {
    let mut compact = false;
    let mut sysroot: Option<String> = None;
    let mut disks: Vec<String> = Vec::new();
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
    let result = if disks.is_empty() { bko_holt::dump(&sys) } else { bko_holt::other::offline::report(&sys, &disks, &bko_holt::timestamp()) };
    let report = match result {
        Ok(r) => r,
        Err(e) => {
            eprintln!("bko-holt dump: {e}");
            return ExitCode::from(3);
        }
    };
    let text = if compact { report.compact() + "\n" } else { report.pretty() };
    let mut out = std::io::stdout().lock();
    if out.write_all(text.as_bytes()).and_then(|_| out.flush()).is_err() {
        return ExitCode::from(1);
    }
    ExitCode::SUCCESS
}
