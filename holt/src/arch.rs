//! The machine's architecture (docs/design/arm64.md, A3): what `dump`
//! records of it, what `load` holds a dump to, and the one table of the
//! names a UEFI boot's files, GRUB's platforms and the boot packages have on
//! each architecture.
//!
//! `dump` records `os.machine` (`uname -m`: `x86_64`, aarch64) and, on UEFI,
//! `firmware.efi_arch` (UEFI's own name for the firmware: x64, ia32, aa64).
//! A dump without `os.machine` (one made before A3) is of `os.architecture`,
//! the dumping binary's own (`x86_64` in every such dump), or else `x86_64`.
//!
//! `load` refuses a dump of another architecture than the rescue system it
//! runs on (`check_load`), before it plans, reads or writes any disk.
//!
//! Every boot path reads its names from `Names`, one row an architecture,
//! instead of naming x64 files itself: `x86_64`'s row is the names bko-holt
//! used before A3, unchanged, so nothing an `x86_64` system dumps or loads
//! changes.  ARM64 boots by UEFI only: its row has no BIOS platform and no
//! BIOS packages.

use crate::json::Value;
use crate::sys::Sys;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) enum Arch {
    X86_64,
    Aarch64,
}

/// One architecture's names.
#[derive(Debug)]
pub(crate) struct Names {
    /// Which it is: the tests hold the names to `Arch::parse` with it.
    #[cfg(test)]
    pub arch: Arch,
    /// `uname -m`'s name.
    pub machine: &'static str,
    /// Debian's name (and the rescue image's: backotter-live-linux-VERSION-arm64.img).
    pub debian: &'static str,
    /// How the messages name it.
    pub label: &'static str,
    /// UEFI's name, in every file name below: x64, aa64.
    pub efi: &'static str,
    /// The removable-media fallback path, on the ESP.
    pub fallback: &'static str,
    /// Its file name, in EFI/BOOT.
    pub fallback_file: &'static str,
    /// GRUB's EFI image, as grub-install names it.
    pub grub: &'static str,
    /// shim, as shim's packages name it.  (`MokManager`, mmx64.efi or
    /// mmaa64.efi, and shim's fallback, fbx64.efi or fbaa64.efi, come back
    /// with the ESP; nothing reads them by name.)
    pub shim: &'static str,
    /// systemd-boot's EFI binary, as bootctl installs it.
    pub systemd_boot: &'static str,
    /// rEFInd's EFI binary.  (Limine's, BOOTX64.EFI or BOOTAA64.EFI, has
    /// the fallback path's name, at EFI/BOOT or in EFI/limine.)
    pub refind: &'static str,
    /// GRUB's UEFI platform (`grub-install --target`, /boot/grub/PLATFORM).
    pub grub_efi_target: &'static str,
    /// GRUB's BIOS platform: none on ARM64.
    pub grub_bios_target: Option<&'static str>,
    /// UEFI GRUB's packages, most specific first (the one `detect_loader` names).
    pub grub_efi_packages: &'static [&'static str],
    /// BIOS GRUB's packages.
    pub grub_bios_packages: &'static [&'static str],
    /// shim's packages.
    pub shim_packages: &'static [&'static str],
    /// The loader an installed package means when nothing else says (package, loader).
    pub last_resort_packages: &'static [(&'static str, &'static str)],
}

/// `x86_64`: the names bko-holt has always used (grub-efi-arm64 is in its
/// UEFI GRUB list as it was before A3).
pub(crate) const X86_64: Names = Names {
    #[cfg(test)]
    arch: Arch::X86_64,
    machine: "x86_64",
    debian: "amd64",
    label: "x86_64",
    efi: "x64",
    fallback: "EFI/BOOT/BOOTX64.EFI",
    fallback_file: "BOOTX64.EFI",
    grub: "grubx64.efi",
    shim: "shimx64.efi",
    systemd_boot: "systemd-bootx64.efi",
    refind: "refind_x64.efi",
    grub_efi_target: "x86_64-efi",
    grub_bios_target: Some("i386-pc"),
    grub_efi_packages: &[
        "anduinos-grub",
        "grub-efi-amd64-signed",
        "grub-efi-amd64",
        "grub-efi-ia32",
        "grub-efi-arm64",
        "grub-x86_64-efi",
        "grub2-efi-x64",
    ],
    grub_bios_packages: &["grub-pc", "grub-i386-pc"],
    shim_packages: &["shim-signed", "shim-x64", "anduinos-shim-signed"],
    last_resort_packages: &[
        ("grub-efi-amd64", "grub"),
        ("grub-pc", "grub"),
        ("systemd-boot", "systemd-boot"),
        ("extlinux", "syslinux"),
    ],
};

/// ARM64 (aarch64): Debian's and Ubuntu's grub-efi-arm64(-signed) and
/// shim-signed, Fedora's grub2-efi-aa64 and shim-aa64, Chimera's and Void's
/// grub-arm64-efi.
pub(crate) const AARCH64: Names = Names {
    #[cfg(test)]
    arch: Arch::Aarch64,
    machine: "aarch64",
    debian: "arm64",
    label: "ARM64",
    efi: "aa64",
    fallback: "EFI/BOOT/BOOTAA64.EFI",
    fallback_file: "BOOTAA64.EFI",
    grub: "grubaa64.efi",
    shim: "shimaa64.efi",
    systemd_boot: "systemd-bootaa64.efi",
    refind: "refind_aa64.efi",
    grub_efi_target: "arm64-efi",
    grub_bios_target: None,
    grub_efi_packages: &[
        "grub-efi-arm64-signed",
        "grub-efi-arm64",
        "grub-arm64-efi",
        "grub2-efi-aa64",
    ],
    grub_bios_packages: &[],
    shim_packages: &["shim-signed", "shim-aa64"],
    last_resort_packages: &[("grub-efi-arm64", "grub"), ("systemd-boot", "systemd-boot")],
};

/// Every row, for what lists them all.
pub(crate) const ALL: [&Names; 2] = [&X86_64, &AARCH64];

impl Arch {
    /// From `uname -m`'s name (the BSDs' amd64 and arm64; illumos's i86pc,
    /// which is 64-bit only), or Debian's.
    pub(crate) fn parse(name: &str) -> Option<Self> {
        match name.trim() {
            "x86_64" | "amd64" | "i86pc" => Some(Self::X86_64),
            "aarch64" | "arm64" => Some(Self::Aarch64),
            _ => None,
        }
    }

    pub(crate) fn names(self) -> &'static Names {
        match self {
            Self::X86_64 => &X86_64,
            Self::Aarch64 => &AARCH64,
        }
    }
}

impl Names {
    /// The fallback path as a UEFI device path's file names it, upper case
    /// (`\EFI\BOOT\BOOTX64.EFI`).
    pub(crate) fn fallback_dp(&self) -> String {
        format!(
            "\\{}",
            self.fallback.replace('/', "\\").to_ascii_uppercase()
        )
    }
}

/// The machine SYS runs on, as `uname -m` names it: the kernel's
/// /proc/sys/kernel/arch, else `uname -m`.  A fake system (a tree other
/// than /) that has neither is `x86_64`, as every one before A3; the running
/// system, this binary's own architecture.
pub(crate) fn machine(sys: &Sys) -> String {
    sys.read_trim("/proc/sys/kernel/arch")
        .filter(|m| !m.is_empty())
        .or_else(|| {
            sys.run("uname", &["-m"])
                .map(|o| o.trim().to_string())
                .filter(|m| !m.is_empty())
        })
        .unwrap_or_else(|| {
            if sys.root() == std::path::Path::new("/") {
                std::env::consts::ARCH
            } else {
                "x86_64"
            }
            .to_string()
        })
}

/// The names for the machine SYS runs on (`x86_64`'s when it is neither).
pub(crate) fn names_of(sys: &Sys) -> &'static Names {
    Arch::parse(&machine(sys)).unwrap_or(Arch::X86_64).names()
}

/// UEFI's name for the firmware of a MACHINE whose
/// /`sys/firmware/efi/fw_platform_size` is `PLATFORM_SIZE`: an `x86_64` kernel
/// on 32-bit firmware (mixed mode) has ia32's.
pub(crate) fn efi_arch(machine: &str, platform_size: Option<u64>) -> Option<&'static str> {
    match (Arch::parse(machine), platform_size) {
        (Some(Arch::X86_64), Some(32)) => Some("ia32"),
        (Some(a), _) => Some(a.names().efi),
        (None, _) => None,
    }
}

/// The machine a dump V is of: its `os.machine`, else (a dump made before
/// A3) `os.architecture`, else `x86_64`.
pub(crate) fn dump_machine(v: &Value) -> String {
    let at = |k: &str| {
        v.at(&["os", k])
            .and_then(Value::as_str)
            .map(str::trim)
            .filter(|m| !m.is_empty())
    };
    at("machine")
        .or_else(|| at("architecture"))
        .unwrap_or("x86_64")
        .to_string()
}

/// The names for the machine a dump V is of (`x86_64`'s for one load
/// refuses, which never gets as far as the names).
pub(crate) fn names_of_dump(v: &Value) -> &'static Names {
    Arch::parse(&dump_machine(v))
        .unwrap_or(Arch::X86_64)
        .names()
}

/// Whether `load` may restore the dump V from the rescue system SYS: the
/// same architecture, one load knows, and on ARM64 a source that booted by
/// UEFI.  The message names both machines.
pub(crate) fn check_load(v: &Value, sys: &Sys) -> Result<(), String> {
    let (dm, rm) = (dump_machine(v), machine(sys));
    let known = ALL
        .iter()
        .map(|n| n.machine)
        .collect::<Vec<_>>()
        .join(" and ");
    let Some(da) = Arch::parse(&dm) else {
        return Err(format!(
            "the source system's architecture is {dm}; bko-holt load restores {known} systems only"
        ));
    };
    let Some(ra) = Arch::parse(&rm) else {
        return Err(format!(
            "this rescue system's architecture is {rm}; bko-holt load runs on {known} only"
        ));
    };
    let (dn, rn) = (da.names(), ra.names());
    if da != ra {
        return Err(format!(
            "the dump is of an {} system ({dm}), and this rescue system is {} ({rm}): load restores a system only from a rescue system of its own architecture; boot BackOtter's {} image (backotter-live-linux-VERSION-{}.img) on the target machine",
            dn.label, rn.label, dn.debian, dn.debian
        ));
    }
    let mode = v
        .at(&["firmware", "mode"])
        .and_then(Value::as_str)
        .unwrap_or("unknown");
    if dn.grub_bios_target.is_none() && mode != "uefi" {
        return Err(format!(
            "the dump is of an {} system ({dm}) that didn't boot by UEFI (firmware mode {mode}): bko-holt load restores {} systems that boot by UEFI only (no U-Boot or devicetree boot)",
            dn.label, dn.label
        ));
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::json::parse;
    use crate::sys::FakeRunner;
    use crate::testutil::Tree;

    fn sys_of(t: &Tree, runner: FakeRunner) -> Sys {
        Sys::new(t.root(), Box::new(runner), true)
    }

    #[test]
    fn every_row_names_its_own_architecture() {
        for n in ALL {
            assert_eq!(Arch::parse(n.machine), Some(n.arch));
            assert_eq!(Arch::parse(n.debian), Some(n.arch));
            assert_eq!(n.arch.names().machine, n.machine);
            assert_eq!(n.fallback, format!("EFI/BOOT/{}", n.fallback_file));
            assert_eq!(
                n.fallback_file,
                format!("BOOT{}.EFI", n.efi.to_ascii_uppercase())
            );
            for f in [n.grub, n.shim, n.systemd_boot] {
                assert!(f.ends_with(&format!("{}.efi", n.efi)), "{f}");
            }
            assert!(n.refind.ends_with(&format!("_{}.efi", n.efi)));
        }
        // ARM64 has no BIOS path at all.
        assert_eq!(AARCH64.grub_bios_target, None);
        assert!(AARCH64.grub_bios_packages.is_empty());
        assert_eq!(X86_64.fallback_dp(), "\\EFI\\BOOT\\BOOTX64.EFI");
        assert_eq!(AARCH64.fallback_dp(), "\\EFI\\BOOT\\BOOTAA64.EFI");
        assert_eq!(Arch::parse("riscv64"), None);
        // The BSDs' and illumos's uname -m.
        assert_eq!(Arch::parse("amd64"), Some(Arch::X86_64));
        assert_eq!(Arch::parse("i86pc"), Some(Arch::X86_64));
        assert_eq!(Arch::parse("arm64"), Some(Arch::Aarch64));
    }

    #[test]
    fn the_machine_from_the_kernel_then_uname() {
        let t = Tree::new("arch-machine");
        // A fake system that says nothing is x86_64, wherever the tests run.
        assert_eq!(machine(&sys_of(&t, FakeRunner::default())), "x86_64");
        assert_eq!(
            machine(&sys_of(
                &t,
                FakeRunner::default().with("uname -m", "aarch64\n")
            )),
            "aarch64"
        );
        t.file("/proc/sys/kernel/arch", "aarch64\n");
        assert_eq!(
            machine(&sys_of(
                &t,
                FakeRunner::default().with("uname -m", "x86_64\n")
            )),
            "aarch64"
        );
        assert_eq!(names_of(&sys_of(&t, FakeRunner::default())).efi, "aa64");
        // The running system is its own.
        assert_eq!(
            Arch::parse(&machine(&Sys::host())),
            Arch::parse(std::env::consts::ARCH)
        );
    }

    #[test]
    fn efi_arch_from_machine_and_platform_size() {
        assert_eq!(efi_arch("x86_64", Some(64)), Some("x64"));
        assert_eq!(efi_arch("x86_64", None), Some("x64"));
        assert_eq!(efi_arch("x86_64", Some(32)), Some("ia32"));
        assert_eq!(efi_arch("aarch64", Some(64)), Some("aa64"));
        assert_eq!(efi_arch("riscv64", Some(64)), None);
    }

    /// A dump made before A3 has no os.machine: it is os.architecture's
    /// (`x86_64` in every one), or `x86_64` with neither.
    #[test]
    fn a_dump_before_a3_is_x86_64() {
        assert_eq!(
            dump_machine(&parse(r#"{"os": {"id": "debian", "architecture": "x86_64"}}"#).unwrap()),
            "x86_64"
        );
        assert_eq!(
            dump_machine(&parse(r#"{"os": {"id": "debian"}}"#).unwrap()),
            "x86_64"
        );
        assert_eq!(dump_machine(&parse(r"{}").unwrap()), "x86_64");
        assert_eq!(
            dump_machine(
                &parse(r#"{"os": {"architecture": "x86_64", "machine": "aarch64"}}"#).unwrap()
            ),
            "aarch64"
        );
        assert_eq!(
            names_of_dump(&parse(r#"{"os": {"id": "debian"}}"#).unwrap()).efi,
            "x64"
        );
        // And loads on an x86_64 rescue system, as before.
        let t = Tree::new("arch-old-dump");
        t.file("/proc/sys/kernel/arch", "x86_64\n");
        for d in [
            r#"{"os": {"id": "debian", "architecture": "x86_64"}, "firmware": {"mode": "bios"}}"#,
            r#"{"firmware": {"mode": "uefi"}}"#,
            "{}",
        ] {
            assert_eq!(
                check_load(&parse(d).unwrap(), &sys_of(&t, FakeRunner::default())),
                Ok(()),
                "{d}"
            );
        }
        // But not on an ARM64 one.
        t.file("/proc/sys/kernel/arch", "aarch64\n");
        let e = check_load(
            &parse(r#"{"os": {"id": "debian"}, "firmware": {"mode": "uefi"}}"#).unwrap(),
            &sys_of(&t, FakeRunner::default()),
        )
        .unwrap_err();
        assert!(
            e.contains("an x86_64 system (x86_64)")
                && e.contains("this rescue system is ARM64 (aarch64)"),
            "{e}"
        );
    }

    #[test]
    fn load_refuses_another_architecture_both_ways() {
        let t = Tree::new("arch-refusal");
        let amd64 = parse(r#"{"os": {"id": "debian", "architecture": "x86_64", "machine": "x86_64"}, "firmware": {"mode": "uefi", "efi_arch": "x64"}}"#).unwrap();
        let arm64 = parse(r#"{"os": {"id": "debian", "architecture": "aarch64", "machine": "aarch64"}, "firmware": {"mode": "uefi", "efi_arch": "aa64"}}"#).unwrap();
        t.file("/proc/sys/kernel/arch", "aarch64\n");
        let s = sys_of(&t, FakeRunner::default());
        let e = check_load(&amd64, &s).unwrap_err();
        assert_eq!(
            e,
            "the dump is of an x86_64 system (x86_64), and this rescue system is ARM64 (aarch64): load restores a system only from a rescue system of its own architecture; boot BackOtter's amd64 image (backotter-live-linux-VERSION-amd64.img) on the target machine"
        );
        assert_eq!(check_load(&arm64, &s), Ok(()));
        t.file("/proc/sys/kernel/arch", "x86_64\n");
        let s = sys_of(&t, FakeRunner::default());
        let e = check_load(&arm64, &s).unwrap_err();
        assert_eq!(
            e,
            "the dump is of an ARM64 system (aarch64), and this rescue system is x86_64 (x86_64): load restores a system only from a rescue system of its own architecture; boot BackOtter's arm64 image (backotter-live-linux-VERSION-arm64.img) on the target machine"
        );
        assert_eq!(check_load(&amd64, &s), Ok(()));
        // Architectures load doesn't know, either side.
        let rv = parse(r#"{"os": {"machine": "riscv64"}, "firmware": {"mode": "uefi"}}"#).unwrap();
        assert!(check_load(&rv, &s).unwrap_err().contains(
            "architecture is riscv64; bko-holt load restores x86_64 and aarch64 systems only"
        ));
        t.file("/proc/sys/kernel/arch", "ppc64le\n");
        assert!(
            check_load(&amd64, &sys_of(&t, FakeRunner::default()))
                .unwrap_err()
                .contains("this rescue system's architecture is ppc64le")
        );
        // ARM64 boots by UEFI only.
        t.file("/proc/sys/kernel/arch", "aarch64\n");
        let nouefi =
            parse(r#"{"os": {"machine": "aarch64"}, "firmware": {"mode": "bios"}}"#).unwrap();
        assert!(
            check_load(&nouefi, &sys_of(&t, FakeRunner::default()))
                .unwrap_err()
                .contains("didn't boot by UEFI (firmware mode bios)")
        );
    }

    // --- Whole fake ARM64 systems: dump, then load's plan -----------------

    use crate::efi::{GLOBAL, LOADER, SHIM};
    use crate::parttable::ESP;
    use crate::parttable::build::{GptSpec, write_gpt};
    use crate::probe::{ext, fat};
    use std::cell::RefCell;
    use std::rc::Rc;

    const MIB: usize = 1 << 20;
    const LINUX: &str = "0fc63daf-8483-4772-8e79-3d69d8477de4";
    const ESP_UUID: &str = "0b5f04ed-e731-4ac6-933e-83ff25657722";

    /// A UEFI Boot#### variable (with efivarfs's 4 attribute bytes): DESC,
    /// on the ESP (partition 1 of the disk, its GUID `ESP_UUID`), FILE.
    fn load_option(desc: &str, file: &str) -> Vec<u8> {
        let mut hd = vec![4u8, 1, 42, 0];
        hd.extend(1u32.to_le_bytes());
        hd.extend(2048u64.to_le_bytes());
        hd.extend(71680u64.to_le_bytes());
        hd.extend([
            0xed, 0x04, 0x5f, 0x0b, 0x31, 0xe7, 0xc6, 0x4a, 0x93, 0x3e, 0x83, 0xff, 0x25, 0x65,
            0x77, 0x22, 2, 2,
        ]);
        let name: Vec<u8> = file
            .encode_utf16()
            .chain([0])
            .flat_map(u16::to_le_bytes)
            .collect();
        let mut fp = vec![4u8, 4];
        fp.extend(((4 + name.len()) as u16).to_le_bytes());
        fp.extend(name);
        let mut v = vec![7u8, 0, 0, 0];
        v.extend(1u32.to_le_bytes());
        v.extend(((hd.len() + fp.len() + 4) as u16).to_le_bytes());
        v.extend(desc.encode_utf16().chain([0]).flat_map(u16::to_le_bytes));
        v.extend(hd);
        v.extend(fp);
        v.extend([0x7f, 0xff, 4, 0]);
        v
    }

    fn utf16_var(text: &str) -> Vec<u8> {
        let mut v = vec![6u8, 0, 0, 0];
        v.extend(text.encode_utf16().chain([0]).flat_map(u16::to_le_bytes));
        v
    }

    /// Debian 13 arm64 on UEFI as its installer (and its cloud image) lays
    /// it out: GPT with an ESP at `ESP_AT` and an ext4 root, the kernel's
    /// machine aarch64.  Without files on the ESP: the callers add the
    /// loader's.
    fn arm64_debian(tag: &str, esp_at: &str) -> Tree {
        let t = Tree::new(tag);
        t.debian13();
        let mut img = vec![0u8; 64 * MIB];
        write_gpt(
            &mut img,
            512,
            &GptSpec {
                disk_guid: "6f1c2c3d-0000-4000-8000-0000000aa064",
                parts: vec![
                    (ESP, ESP_UUID, 2048, 73727, 0, ""),
                    (
                        LINUX,
                        "11112222-3333-4444-5555-666677778888",
                        73728,
                        131_038,
                        0,
                        "",
                    ),
                ],
            },
        );
        // ext::sample's superblock is a full 64 MiB filesystem: this one is
        // what mke2fs makes on vda2 (57311 sectors, so 7163 4 KiB blocks),
        // holding a small root (a dump of an ext bigger than its partition,
        // or with no free block or inode, is no source a restore can fit).
        let mut root = ext::sample::image(0x4 | 0x1000, 0x2 | 0x40 | 0x80 | 0x200, 0x1 | 0x400, "");
        for (o, v) in [(4, 7163u32), (12, 5500), (16, 8150)] {
            root[1024 + o..1024 + o + 4].copy_from_slice(&v.to_le_bytes());
        }
        for (at, data) in [
            (2048, fat::sample::fat32(71680, 0x1234_abcd, "")),
            (73728, root),
        ] {
            img[at * 512..at * 512 + data.len()].copy_from_slice(&data);
        }
        let k = "6.12.57+deb13-arm64";
        t.disk("vda", "254:0", &img, 512)
            .partition("vda", "vda1", "254:1", 1, 2048, 71680, &img)
            .partition("vda", "vda2", "254:2", 2, 73728, 57311, &img)
            .file("/proc/sys/kernel/arch", "aarch64\n")
            .file("/proc/sys/kernel/osrelease", format!("{k}\n"))
            .file("/sys/firmware/efi/fw_platform_size", "64\n")
            .file(&format!("/sys/firmware/efi/efivars/SecureBoot-{GLOBAL}"), [6u8, 0, 0, 0, 0])
            .symlink(&format!("/dev/disk/by-partuuid/{ESP_UUID}"), "../../vda1")
            .file("/proc/self/mountinfo", format!("26 1 254:2 / / rw,relatime shared:1 - ext4 /dev/vda2 rw,errors=remount-ro\n27 26 254:1 / {esp_at} rw,relatime shared:2 - vfat /dev/vda1 rw,fmask=0077\n"))
            .file("/etc/fstab", format!("UUID=10111213-1415-1617-1819-1a1b1c1d1e1f / ext4 errors=remount-ro 0 1\nPARTUUID={ESP_UUID} {esp_at} vfat umask=0077 0 1\n"))
            .file(&format!("/boot/vmlinuz-{k}"), "k")
            .file(&format!("/boot/initrd.img-{k}"), "i")
            .dir(&format!("/usr/lib/modules/{k}/kernel"))
            .file("/proc/cmdline", format!("BOOT_IMAGE=/boot/vmlinuz-{k} root=UUID=10111213-1415-1617-1819-1a1b1c1d1e1f ro\n"));
        t
    }

    /// GRUB through shim, Debian's arm64 packages; the firmware booted
    /// shim's NVRAM entry.
    fn arm64_grub_shim() -> Tree {
        let t = arm64_debian("arch-arm64-grub", "/boot/efi");
        let shim = vec![1u8; 300];
        t.file(&format!("/sys/firmware/efi/efivars/Boot0001-{GLOBAL}"), load_option("debian", "\\EFI\\debian\\shimaa64.efi"))
            .file(&format!("/sys/firmware/efi/efivars/BootOrder-{GLOBAL}"), [7u8, 0, 0, 0, 1, 0])
            .file(&format!("/sys/firmware/efi/efivars/BootCurrent-{GLOBAL}"), [6u8, 0, 0, 0, 1, 0])
            .file(&format!("/sys/firmware/efi/efivars/LoaderInfo-{LOADER}"), utf16_var("GRUB 2.12-9"))
            .file(&format!("/sys/firmware/efi/efivars/MokListRT-{SHIM}"), [0u8; 8])
            .file("/boot/efi/EFI/debian/shimaa64.efi", &shim)
            .file("/boot/efi/EFI/debian/grubaa64.efi", vec![2u8; 400])
            .file("/boot/efi/EFI/debian/mmaa64.efi", vec![3u8; 200])
            .file("/boot/efi/EFI/debian/BOOTAA64.CSV", "shimaa64.efi,debian,,This is the boot entry for debian\n")
            .file("/boot/efi/EFI/debian/grub.cfg", "search.fs_uuid 10111213-1415-1617-1819-1a1b1c1d1e1f root \nset prefix=($root)'/boot/grub'\nconfigfile $prefix/grub.cfg\n")
            .file("/boot/efi/EFI/BOOT/BOOTAA64.EFI", &shim)
            .file("/boot/efi/EFI/BOOT/fbaa64.efi", vec![4u8; 100])
            .file("/boot/efi/EFI/BOOT/mmaa64.efi", vec![3u8; 200])
            .file("/boot/grub/arm64-efi/normal.mod", "x")
            .file("/boot/grub/grub.cfg", "menuentry 'Debian GNU/Linux' {\n\tlinux /boot/vmlinuz-6.12.57+deb13-arm64 root=UUID=10111213-1415-1617-1819-1a1b1c1d1e1f ro\n}\n")
            .file(
                "/var/lib/dpkg/status",
                "Package: grub-efi-arm64-signed\nStatus: install ok installed\nArchitecture: arm64\nVersion: 1+2.12+9+deb13u2\n\nPackage: grub-efi-arm64-bin\nStatus: install ok installed\nArchitecture: arm64\nVersion: 2.12-9+deb13u2\n\nPackage: grub-efi-arm64\nStatus: install ok installed\nArchitecture: arm64\nVersion: 2.12-9+deb13u2\n\nPackage: shim-signed\nStatus: install ok installed\nArchitecture: arm64\nVersion: 1.47+15.8-1\n\nPackage: linux-image-arm64\nStatus: install ok installed\nArchitecture: arm64\nVersion: 6.12.57-1\n\nPackage: initramfs-tools\nStatus: install ok installed\nArchitecture: all\nVersion: 0.148.3\n",
            );
        t
    }

    /// systemd-boot with the ESP as /boot (the Fedora and Arch layout):
    /// bootctl's systemd-bootaa64.efi and its fallback copy, the firmware
    /// booted its NVRAM entry.
    fn arm64_systemd_boot() -> Tree {
        let t = arm64_debian("arch-arm64-sdboot", "/boot");
        let k = "6.12.57+deb13-arm64";
        let sd = vec![5u8; 300];
        t.file(&format!("/sys/firmware/efi/efivars/Boot0001-{GLOBAL}"), load_option("Linux Boot Manager", "\\EFI\\systemd\\systemd-bootaa64.efi"))
            .file(&format!("/sys/firmware/efi/efivars/BootOrder-{GLOBAL}"), [7u8, 0, 0, 0, 1, 0])
            .file(&format!("/sys/firmware/efi/efivars/BootCurrent-{GLOBAL}"), [6u8, 0, 0, 0, 1, 0])
            .file(&format!("/sys/firmware/efi/efivars/LoaderInfo-{LOADER}"), utf16_var("systemd-boot 257.9-1~deb13u1"))
            .file("/boot/EFI/systemd/systemd-bootaa64.efi", &sd)
            .file("/boot/EFI/BOOT/BOOTAA64.EFI", &sd)
            .file("/boot/loader/loader.conf", "timeout 3\n")
            .file("/boot/loader/entries/debian-6.12.57.conf", format!("title Debian GNU/Linux 13 (trixie)\nlinux /vmlinuz-{k}\ninitrd /initrd.img-{k}\noptions root=UUID=10111213-1415-1617-1819-1a1b1c1d1e1f ro\n"))
            .file(
                "/var/lib/dpkg/status",
                "Package: systemd-boot\nStatus: install ok installed\nArchitecture: arm64\nVersion: 257.9-1~deb13u1\n\nPackage: systemd-boot-efi\nStatus: install ok installed\nArchitecture: arm64\nVersion: 257.9-1~deb13u1\n\nPackage: linux-image-arm64\nStatus: install ok installed\nArchitecture: arm64\nVersion: 6.12.57-1\n\nPackage: initramfs-tools\nStatus: install ok installed\nArchitecture: all\nVersion: 0.148.3\n",
            );
        t
    }

    fn dump(t: &Tree) -> Value {
        let sys = Sys::new(t.root(), Box::new(FakeRunner::default()), true);
        let os = crate::osrel::read(&sys);
        crate::osrel::check_supported(os.as_ref()).unwrap();
        let v = crate::report::build(&sys, os.as_ref(), "2026-11-14T00:00:00Z");
        // Redacted for a bug report it plans the same (on a rescue system
        // of its own architecture, as check_plan's is).
        if let Err(e) = crate::bugreport::check_plan(&v.pretty()) {
            panic!("redaction: {e}");
        }
        v
    }

    fn str_at<'a>(v: &'a Value, path: &[&str]) -> &'a str {
        v.at(path)
            .and_then(Value::as_str)
            .unwrap_or_else(|| panic!("no string at {path:?}: {}", v.compact()))
    }

    /// The boot stage onto vdb, with EXTRA load arguments.
    fn boot_stage(v: &Value, extra: &[&str]) -> (String, crate::load::commands::Built) {
        use crate::load::{Parsed, Stage, commands, layout::Layout, target::Mapping};
        let l = Layout::from_json(v.clone()).unwrap();
        let args: Vec<String> = std::iter::once("l.json")
            .chain(extra.iter().copied())
            .map(str::to_string)
            .collect();
        let Ok(Parsed::Run(o)) = crate::load::parse_args(&args) else {
            unreachable!()
        };
        let b = commands::build(
            &l,
            &o,
            &Mapping {
                pairs: vec![("vda".into(), "vdb".into())],
                ..Mapping::default()
            },
        );
        (
            b.stage(Stage::Boot)
                .iter()
                .map(commands::Cmd::shell)
                .collect(),
            b,
        )
    }

    /// SCRIPT with bko-holt's own path (this test binary's, quoted as a plan
    /// quotes it, or bare inside a quoted command) put as BKO-HOLT: under
    /// `cargo test --target x86_64-unknown-linux-gnu` it lies in
    /// target/x86_64-unknown-linux-gnu/, an x86 name that isn't the plan's.
    fn without_exe(script: &str) -> String {
        let exe = std::env::current_exe()
            .map_or_else(|_| "bko-holt".into(), |p| p.to_string_lossy().into_owned());
        script
            .replace(&crate::load::shell_quote(&exe), "BKO-HOLT")
            .replace(&exe, "BKO-HOLT")
    }

    #[test]
    fn arm64_grub_shim_dump_and_plan() {
        let v = dump(&arm64_grub_shim());
        assert_eq!(str_at(&v, &["os", "machine"]), "aarch64");
        assert_eq!(str_at(&v, &["firmware", "efi_arch"]), "aa64");
        let l = v.at(&["boot", "loader"]).unwrap();
        assert_eq!(str_at(l, &["name"]), "grub");
        assert_eq!(l.get("via_shim"), Some(&Value::Bool(true)));
        assert_eq!(str_at(l, &["package"]), "grub-efi-arm64-signed");
        assert_eq!(str_at(l, &["shim_package"]), "shim-signed");
        assert_eq!(
            str_at(&v, &["boot", "efi", "entries", "0", "file"]),
            "\\EFI\\debian\\shimaa64.efi"
        );
        assert_eq!(
            str_at(&v, &["boot", "grub", "installed_platforms", "0"]),
            "arm64-efi"
        );
        assert_eq!(
            str_at(&v, &["boot", "packages", "grub-efi-arm64-bin"]),
            "2.12-9+deb13u2"
        );
        assert_eq!(
            str_at(&v, &["boot", "packages", "linux-image-arm64"]),
            "6.12.57-1"
        );
        let files: Vec<&str> = v
            .at(&["boot", "esps", "0", "contents", "files"])
            .and_then(Value::as_array)
            .unwrap()
            .iter()
            .filter_map(|f| f.get("path").and_then(Value::as_str))
            .collect();
        assert!(
            files.contains(&"EFI/BOOT/BOOTAA64.EFI")
                && files.contains(&"EFI/BOOT/fbaa64.efi")
                && files.contains(&"EFI/debian/mmaa64.efi"),
            "{files:?}"
        );
        let fb = v
            .at(&["boot", "esps", "0", "contents", "files"])
            .and_then(Value::as_array)
            .unwrap()
            .iter()
            .find(|f| f.get("path").and_then(Value::as_str) == Some("EFI/BOOT/BOOTAA64.EFI"))
            .unwrap();
        assert_eq!(str_at(fb, &["same_as"]), "EFI/debian/shimaa64.efi");
        // load: GRUB's arm64-efi platform, the bootloader id from shim's
        // entry, NVRAM's entry for shimaa64.efi; no x86 name, no BIOS.
        let (bt, b) = boot_stage(&v, &[]);
        assert!(b.problems.is_empty(), "{:?}", b.problems);
        assert!(bt.contains("chroot /mnt/bko-target grub-install --target=arm64-efi --efi-directory=/boot/efi --bootloader-id=debian --no-nvram\n"), "{bt}");
        assert!(bt.contains("efibootmgr --quiet --create --disk /dev/vdb --part 1 --label debian --loader '\\EFI\\debian\\shimaa64.efi'\n"), "{bt}");
        let plan = without_exe(&bt);
        for x86 in ["x86_64", "x64", "i386-pc", "BOOTX64"] {
            assert!(!plan.contains(x86), "{x86}: {plan}");
        }
        // --removable: grub-install's own copy at BOOTAA64.EFI.
        let (bt, _) = boot_stage(&v, &["--removable"]);
        assert!(bt.contains("grub-install --target=arm64-efi --efi-directory=/boot/efi --bootloader-id=debian --no-nvram --force-extra-removable ;; ") && bt.contains("cmp -s /mnt/bko-target/boot/efi/EFI/BOOT/BOOTAA64.EFI /mnt/bko-target/boot/efi/EFI/debian/shimaa64.efi"), "{bt}");
        // --reformat: the fallback, a copy of GRUB, renewed with it.
        let (bt, _) = boot_stage(&v, &["--reformat", "/=xfs"]);
        assert!(bt.contains("[ ! -f /mnt/bko-target/boot/efi/EFI/BOOT/BOOTAA64.EFI ] || ! cmp -s /mnt/bko-target/boot/efi/EFI/BOOT/BOOTAA64.EFI /mnt/bko-target/boot/efi/EFI/debian/grubaa64.efi"), "{bt}");
    }

    /// An ARM64 Debian whose ESP (at /boot/efi) holds FILES (a file named
    /// in SAME is a copy of the first), the firmware having booted CURRENT
    /// (an NVRAM entry's file) if any, with dpkg's PACKAGES; the kernel's
    /// machine MACHINE.
    fn esp_case(
        tag: &str,
        machine: &str,
        files: &[&str],
        same: &[&str],
        current: Option<&str>,
        packages: &[&str],
    ) -> Value {
        let t = arm64_debian(tag, "/boot/efi");
        t.file("/proc/sys/kernel/arch", format!("{machine}\n"));
        for (i, f) in files.iter().enumerate() {
            let body = if i == 0 || same.contains(f) {
                vec![1u8; 300]
            } else {
                vec![i as u8 + 1; 100 + i]
            };
            t.file(&format!("/boot/efi/{f}"), body);
        }
        if let Some(c) = current {
            t.file(
                &format!("/sys/firmware/efi/efivars/Boot0002-{GLOBAL}"),
                load_option("UEFI Misc Device", c),
            )
            .file(
                &format!("/sys/firmware/efi/efivars/BootOrder-{GLOBAL}"),
                [7u8, 0, 0, 0, 2, 0],
            )
            .file(
                &format!("/sys/firmware/efi/efivars/BootCurrent-{GLOBAL}"),
                [6u8, 0, 0, 0, 2, 0],
            );
        }
        let status: String = packages.iter().map(|p| format!("Package: {p}\nStatus: install ok installed\nArchitecture: arm64\nVersion: 1\n\n")).collect();
        t.file("/var/lib/dpkg/status", status);
        dump(&t)
    }

    /// The firmware booted the fallback path, a copy of shim (no NVRAM
    /// entry of its own, no MOK variables), or nothing names the loader
    /// but the ESP's files or the packages: the names are ARM64's.
    #[test]
    fn arm64_detection_from_the_fallback_and_the_esp() {
        let pk = [
            "grub-efi-arm64-signed",
            "shim-signed",
            "grub-efi-amd64-signed",
        ];
        let files = [
            "EFI/debian/shimaa64.efi",
            "EFI/BOOT/BOOTAA64.EFI",
            "EFI/debian/grubaa64.efi",
        ];
        let v = esp_case(
            "arch-fb-shim",
            "aarch64",
            &files,
            &["EFI/BOOT/BOOTAA64.EFI"],
            Some("\\EFI\\BOOT\\BOOTAA64.EFI"),
            &pk,
        );
        let l = v.at(&["boot", "loader"]).unwrap();
        assert_eq!(str_at(l, &["name"]), "grub", "{}", l.compact());
        assert_eq!(l.get("via_shim"), Some(&Value::Bool(true)));
        // ARM64's own GRUB package, though x86_64's is listed too.
        assert_eq!(str_at(l, &["package"]), "grub-efi-arm64-signed");
        let why = l.get("evidence").unwrap().compact();
        assert!(
            why.contains("that is the removable-media fallback path")
                && why.contains("EFI/BOOT/BOOTAA64.EFI is a copy of EFI/debian/shimaa64.efi")
                && why.contains("an ESP holds grubaa64.efi"),
            "{why}"
        );
        // The same files on x86_64 (its names) say nothing of shim or GRUB.
        let v = esp_case(
            "arch-fb-shim-x86",
            "x86_64",
            &files,
            &["EFI/BOOT/BOOTAA64.EFI"],
            Some("\\EFI\\BOOT\\BOOTAA64.EFI"),
            &pk,
        );
        assert_eq!(
            v.at(&["boot", "loader", "via_shim"]),
            Some(&Value::Bool(false)),
            "{}",
            v.at(&["boot", "loader"]).unwrap().compact()
        );
        assert_eq!(str_at(&v, &["boot", "loader", "name"]), "unknown");
        // No entry, the ESP alone: each loader's ARM64 binary.
        let name = |tag: &str, files: &[&str], pk: &[&str]| {
            str_at(
                &esp_case(tag, "aarch64", files, &[], None, pk),
                &["boot", "loader", "name"],
            )
            .to_string()
        };
        assert_eq!(
            name("arch-esp-grub", &["EFI/debian/grubaa64.efi"], &[]),
            "grub"
        );
        assert_eq!(
            name("arch-esp-sd", &["EFI/systemd/systemd-bootaa64.efi"], &[]),
            "systemd-boot"
        );
        assert_eq!(
            name("arch-esp-refind", &["EFI/refind/refind_aa64.efi"], &[]),
            "refind"
        );
        // Limine's BOOTAA64.EFI at the fallback path, its limine.conf beside it, or in EFI/limine.
        assert_eq!(
            name(
                "arch-esp-limine",
                &["EFI/BOOT/BOOTAA64.EFI", "EFI/BOOT/limine.conf"],
                &[]
            ),
            "limine"
        );
        assert_eq!(
            name("arch-esp-limine2", &["EFI/limine/BOOTAA64.EFI"], &[]),
            "limine"
        );
        assert_eq!(
            name("arch-esp-x64", &["EFI/debian/grubx64.efi"], &[]),
            "unknown"
        );
        // Only a package: ARM64's GRUB; BIOS GRUB's means nothing there.
        assert_eq!(name("arch-pk-grub", &[], &["grub-efi-arm64"]), "grub");
        assert_eq!(name("arch-pk-pc", &[], &["grub-pc"]), "unknown");
    }

    #[test]
    fn arm64_systemd_boot_dump_and_plan() {
        let v = dump(&arm64_systemd_boot());
        assert_eq!(str_at(&v, &["os", "machine"]), "aarch64");
        assert_eq!(str_at(&v, &["firmware", "efi_arch"]), "aa64");
        let l = v.at(&["boot", "loader"]).unwrap();
        assert_eq!(str_at(l, &["name"]), "systemd-boot");
        assert_eq!(str_at(l, &["package"]), "systemd-boot");
        assert_eq!(l.get("via_shim"), Some(&Value::Bool(false)));
        assert_eq!(str_at(&v, &["boot", "esps", "0", "mounted_at"]), "/boot");
        let (bt, b) = boot_stage(&v, &[]);
        assert!(b.problems.is_empty(), "{:?}", b.problems);
        assert!(
            bt.contains("chroot /mnt/bko-target bootctl install --no-variables\n"),
            "{bt}"
        );
        assert!(bt.contains("efibootmgr --quiet --create --disk /dev/vdb --part 1 --label 'Linux Boot Manager' --loader '\\EFI\\systemd\\systemd-bootaa64.efi'\n"), "{bt}");
        let plan = without_exe(&bt);
        assert!(!plan.contains("grub") && !plan.contains("x64"), "{plan}");
    }

    /// The runner a refused load must never call.
    struct Spy(Rc<RefCell<Vec<String>>>);

    impl crate::sys::Runner for Spy {
        fn run(&self, prog: &str, args: &[&str]) -> Option<String> {
            self.0.borrow_mut().push(
                std::iter::once(prog)
                    .chain(args.iter().copied())
                    .collect::<Vec<_>>()
                    .join(" "),
            );
            None
        }
    }

    /// load refuses another architecture's dump in every mode (plan,
    /// status, a committed whole restore), before it runs anything, reads
    /// a target disk or writes one: both directions.
    #[test]
    fn load_refuses_another_architecture_before_any_disk() {
        let arm64 = dump(&arm64_grub_shim()).pretty();
        let amd64 = crate::load::layout::sample::bios_mbr();
        for (layout, rescue, want) in [
            (
                &arm64,
                "x86_64",
                "the dump is of an ARM64 system (aarch64), and this rescue system is x86_64 (x86_64)",
            ),
            (
                &amd64,
                "aarch64",
                "the dump is of an x86_64 system (x86_64), and this rescue system is ARM64 (aarch64)",
            ),
        ] {
            for extra in [
                &[][..],
                &["--only", "status"],
                &[
                    "--through",
                    "close",
                    "--commit",
                    "--erase",
                    "/dev/vdb",
                    "--repo",
                    "/srv/r",
                ],
            ] {
                let t = Tree::new("arch-refused");
                t.file("/proc/sys/kernel/arch", format!("{rescue}\n")).disk(
                    "vdb",
                    "254:16",
                    &vec![0u8; 64 * MIB],
                    512,
                );
                let calls = Rc::new(RefCell::new(Vec::new()));
                let sys = Sys::new(t.root(), Box::new(Spy(calls.clone())), true);
                let args: Vec<String> = ["layout.json", "--map", "vda=vdb"]
                    .iter()
                    .chain(extra.iter())
                    .map(ToString::to_string)
                    .collect();
                let Ok(crate::load::Parsed::Run(o)) = crate::load::parse_args(&args) else {
                    panic!("{args:?}")
                };
                let (code, out, err) = crate::load::run(&o, layout, &sys);
                assert_eq!(code, crate::load::EXIT_UNSUPPORTED, "{args:?}: {out}{err}");
                assert!(out.is_empty(), "{out}");
                assert!(err.contains(want), "{err}");
                assert!(
                    calls.borrow().is_empty(),
                    "{args:?} ran {:?}",
                    calls.borrow()
                );
                assert_eq!(std::fs::read(t.p("/dev/vdb")).unwrap(), vec![0u8; 64 * MIB]);
            }
        }
        // Each on a rescue system of its own architecture gets past it.
        for (layout, rescue) in [(&arm64, "aarch64"), (&amd64, "x86_64")] {
            let t = Tree::new("arch-allowed");
            t.file("/proc/sys/kernel/arch", format!("{rescue}\n"));
            let sys = Sys::new(t.root(), Box::new(FakeRunner::default()), true);
            let Ok(crate::load::Parsed::Run(o)) =
                crate::load::parse_args(&["layout.json".to_string()])
            else {
                unreachable!()
            };
            let (_, _, err) = crate::load::run(&o, layout, &sys);
            assert!(
                !err.contains("rescue system of its own architecture"),
                "{err}"
            );
        }
    }

    /// The corpus's two ARM64 fixtures (holt/tests/corpus/arm64-grub-shim
    /// and arm64-systemd-boot, its README) are these trees' dumps, which
    /// `golden_plans.rs` plans as it plans every guest's.  The tool's version
    /// aside, they must be the same; `BKO_HOLT_GOLDEN=bless` writes them.
    #[test]
    fn arm64_corpus_fixtures_are_these_dumps() {
        let dir = std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
            .join("tests")
            .join("corpus");
        let bless = std::env::var("BKO_HOLT_GOLDEN").as_deref() == Ok("bless");
        // The first "version" line is tool.version.
        let norm = |s: &str| {
            let mut seen = false;
            s.lines()
                .map(|l| {
                    if !seen && l.trim_start().starts_with("\"version\": ") {
                        seen = true;
                        "    \"version\": \"-\","
                    } else {
                        l
                    }
                })
                .collect::<Vec<_>>()
                .join("\n")
        };
        for (name, t) in [
            ("arm64-grub-shim", arm64_grub_shim()),
            ("arm64-systemd-boot", arm64_systemd_boot()),
        ] {
            // os.architecture is the dumping binary's own: on the machine,
            // the aarch64 bko-holt's, wherever these tests run.
            let own = format!("\"architecture\": \"{}\",", std::env::consts::ARCH);
            // A mount's usage is statvfs's of the directory the tree is in:
            // the test host's, so the fixtures have none.
            fn drop_usage(v: &mut Value) {
                match v {
                    Value::Arr(a) => a.iter_mut().for_each(drop_usage),
                    Value::Obj(o) => {
                        if o.iter().any(|(k, _)| k == "super_options") {
                            o.retain(|(k, _)| {
                                !matches!(
                                    k.as_str(),
                                    "size_bytes" | "used_bytes" | "available_bytes"
                                )
                            });
                        }
                        o.iter_mut().for_each(|(_, x)| drop_usage(x));
                    }
                    _ => {}
                }
            }
            let mut v = dump(&t);
            drop_usage(&mut v);
            let text = v
                .pretty()
                .replacen(&own, "\"architecture\": \"aarch64\",", 1);
            assert!(
                text.contains("\"architecture\": \"aarch64\",\n    \"machine\": \"aarch64\"\n"),
                "{name}: {text}"
            );
            assert!(
                !text.contains(&t.root().display().to_string()),
                "{name}: the dump names its tree's path"
            );
            let p = dir.join(name).join("src.json");
            if bless {
                std::fs::create_dir_all(p.parent().unwrap()).unwrap();
                std::fs::write(&p, &text).unwrap();
            }
            let kept = std::fs::read_to_string(&p).unwrap_or_else(|e| {
                panic!("{}: {e} (BKO_HOLT_GOLDEN=bless writes it)", p.display())
            });
            assert!(
                norm(&kept) == norm(&text),
                "{}: not this tree's dump (BKO_HOLT_GOLDEN=bless rewrites it)",
                p.display()
            );
            // index.tsv's check_plan_raw and check_plan.
            let (ids, lines) = crate::bugreport::check_plan(&kept).unwrap();
            println!("{name}\tok:{ids}:{lines}");
        }
    }
}
