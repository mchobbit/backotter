use bko_laydump::sys::{FakeRunner, Sys};
use std::io::Write;
use std::process::ExitCode;

const USAGE: &str = "Usage: bko-laydump [--compact] [--sysroot DIR]

Writes a JSON description of this Debian 13 system's disk layout and boot
setup to standard output: disks and partition tables, boot code, md RAID,
device-mapper, LVM, LUKS, ZFS and btrfs, filesystems and their features,
mounts, fstab and crypttab, UEFI or BIOS boot entries, the boot loader and
its configuration, kernels and initramfs.

Run it as root: partition tables, superblocks and LVM/ZFS labels are read
from the devices themselves.  As another user it falls back to udev's
records and says so under \"warnings\".

  --compact       one line of JSON instead of an indented document
  --sysroot DIR   describe the tree under DIR (its own sys/, proc/, dev/,
                  etc/, run/ and boot/) instead of this system, running no
                  commands; for tests
  -h, --help      show this help
  -V, --version   show the version

Exit status: 0 on success, 2 for bad arguments, 3 when the system isn't
Debian 13.  SOURCE_DATE_EPOCH, when set, fixes the report's timestamp.
";

fn main() -> ExitCode {
    let mut compact = false;
    let mut sysroot: Option<String> = None;
    let mut args = std::env::args().skip(1);
    while let Some(a) = args.next() {
        match a.as_str() {
            "--compact" => compact = true,
            "--sysroot" => match args.next() {
                Some(d) => sysroot = Some(d),
                None => {
                    eprintln!("bko-laydump: --sysroot needs a directory\n\n{USAGE}");
                    return ExitCode::from(2);
                }
            },
            "-h" | "--help" => {
                print!("{USAGE}");
                return ExitCode::SUCCESS;
            }
            "-V" | "--version" => {
                println!("bko-laydump {}", env!("CARGO_PKG_VERSION"));
                return ExitCode::SUCCESS;
            }
            other => {
                eprintln!("bko-laydump: unknown argument {other:?}\n\n{USAGE}");
                return ExitCode::from(2);
            }
        }
    }
    let sys = match sysroot {
        Some(d) => {
            if !std::path::Path::new(&d).is_dir() {
                eprintln!("bko-laydump: --sysroot {d}: not a directory");
                return ExitCode::from(2);
            }
            Sys::new(d, Box::new(FakeRunner::default()), bko_laydump::sys::euid() == 0)
        }
        None => Sys::host(),
    };
    let report = match bko_laydump::dump(&sys) {
        Ok(r) => r,
        Err(e) => {
            eprintln!("bko-laydump: {e}");
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
