//! /etc/os-release, and the check that this is a system bko-holt supports.

use crate::json::{Obj, Value};
use crate::sys::Sys;

#[derive(Debug, Default, Clone, PartialEq)]
pub struct OsRelease(pub Vec<(String, String)>);

impl OsRelease {
    pub fn get(&self, key: &str) -> Option<&str> {
        self.0
            .iter()
            .rev()
            .find(|(k, _)| k == key)
            .map(|(_, v)| v.as_str())
    }
}

/// os-release(5): KEY=VALUE lines, values optionally quoted the shell way.
pub fn parse(text: &str) -> OsRelease {
    let mut out = Vec::new();
    for line in text.lines() {
        let line = line.trim();
        if line.is_empty() || line.starts_with('#') {
            continue;
        }
        let Some((k, v)) = line.split_once('=') else {
            continue;
        };
        let k = k.trim();
        if k.is_empty() || !k.bytes().all(|c| c.is_ascii_alphanumeric() || c == b'_') {
            continue;
        }
        out.push((k.to_string(), unquote(v.trim())));
    }
    OsRelease(out)
}

/// A shell word: "double quoted" (with \\ \" \$ \` escapes), 'single
/// quoted', or bare.
pub fn unquote(v: &str) -> String {
    let b = v.as_bytes();
    if b.len() >= 2 && b[0] == b'"' && b[b.len() - 1] == b'"' {
        let inner = &v[1..v.len() - 1];
        let mut out = String::new();
        let mut it = inner.chars().peekable();
        while let Some(c) = it.next() {
            if c == '\\' {
                match it.peek() {
                    Some(&n @ ('\\' | '"' | '$' | '`')) => {
                        out.push(n);
                        it.next();
                    }
                    _ => out.push('\\'),
                }
            } else {
                out.push(c);
            }
        }
        out
    } else if b.len() >= 2 && b[0] == b'\'' && b[b.len() - 1] == b'\'' {
        v[1..v.len() - 1].to_string()
    } else {
        v.to_string()
    }
}

/// /etc/os-release, else /usr/lib/os-release; else, on `OpenIndiana`, which
/// has neither, one made from /etc/release.
pub fn read(sys: &Sys) -> Option<OsRelease> {
    sys.read("/etc/os-release")
        .or_else(|| sys.read("/usr/lib/os-release"))
        .map(|t| parse(&t))
        .or_else(|| {
            sys.read("/etc/release")
                .and_then(|t| openindiana_release(&t))
        })
}

/// `OpenIndiana` Hipster's /etc/release, whose first line (indented) is
/// "`OpenIndiana` Hipster 2026.10 (powered by illumos)": ID=openindiana,
/// `VERSION_ID` the snapshot.  Another system's /etc/release (Oracle
/// Solaris', `OpenIndiana`'s before Hipster) makes none.
pub fn openindiana_release(text: &str) -> Option<OsRelease> {
    let line = text.lines().map(str::trim).find(|l| !l.is_empty())?;
    let mut w = line.split_whitespace();
    if (w.next(), w.next()) != (Some("OpenIndiana"), Some("Hipster")) {
        return None;
    }
    let ver = w
        .next()
        .filter(|v| v.starts_with(|c: char| c.is_ascii_digit()));
    let pretty = ver.map_or_else(
        || "OpenIndiana Hipster".to_string(),
        |v| format!("OpenIndiana Hipster {v}"),
    );
    let mut out = vec![
        ("ID".to_string(), "openindiana".to_string()),
        ("NAME".into(), "OpenIndiana Hipster".into()),
    ];
    if let Some(v) = ver {
        out.push(("VERSION_ID".into(), v.to_string()));
    }
    out.push(("PRETTY_NAME".into(), pretty));
    Some(OsRelease(out))
}

/// The systems bko-holt supports, as os-release's ID and the major part of
/// `VERSION_ID` ("*": any, for a rolling release that numbers its snapshots),
/// with how people name them.  Each has a whole-machine test in `HoltTest1All`
/// (testvm/holttest1d/README.md says which guests pass).  The Ubuntu-based ones use Debian's
/// boot tools (update-initramfs, update-grub).  KDE neon (Ubuntu 24.04 with
/// KDE's rolling Plasma) uses Ubuntu's; its test is `neon24` (UEFI, GRUB
/// through shim in EFI/neon) in `HoltTest1U`, docs/design/kde-neon.md.
/// elementary OS 8 (Ubuntu 24.04 underneath, distinst's installer) does too:
/// LVM VG `data` (LUKS `cryptdata` when encrypted), GRUB through shim in
/// EFI/ubuntu with its grub.cfg on the ESP; its tests are `elementary8` and
/// `elementary8-luks` in `HoltTest1U`, docs/design/elementary.md.  Arch
/// and Artix have mkinitcpio and grub-mkconfig, Alpine mkinitfs and
/// grub-mkconfig, Fedora dracut and grub2-mkconfig; `AerynOS`'s initramfs
/// comes prebuilt with its kernel, and systemd-boot boots it.  Chimera
/// Linux (musl, dinit, apk, FreeBSD's userland) has Debian's
/// initramfs-tools (update-initramfs) but no debconf,
/// and grub-mkconfig, or systemd-boot with gen-systemd-boot; its tests are
/// `chimera`, `chimera-uefi` and `chimera-zfs` in `HoltTest1Obscure`, and
/// `BackShot1` on the Thinkpad (root on ZFS, the ESP as /boot).  Bazzite, and
/// Fedora's atomic desktops under the Fedora row, are ostree systems
/// (`crate::ostree)`: the initramfs comes with the image and bootupd's GRUB
/// config is static, so load regenerates nothing there (load/ostree.rs).
/// Their whole-machine test is `kinoite44`, Bazzite's Kinoite base, in
/// `HoltTest1F`; `BackShot1` runs Bazzite itself on real hardware.  Gentoo
/// Linux (`OpenRC` or systemd, Portage) boots the way its owner put it
/// together: installkernel's install.conf names the initramfs generator,
/// and only dracut's (or none) is supported, with GRUB and grub-mkconfig;
/// its tests are `gentoo` (`OpenRC`, the Thinkpad's setup in small) and
/// `gentoo-systemd` (LVM on LUKS) in `HoltTest1G`, and `BackShot1` on the
/// Thinkpad itself (docs/design/gentoo.md).  `PikaOS` 4 (Debian sid with its
/// own kernel) boots by rEFInd, whose files are kept as restored, with
/// booster's initramfs (docs/design/pikaos.md); its tests are `pikaos4` and
/// `pikaos4-luks` in `HoltTest1Obscure`.  `AnduinOS` 2 (Ubuntu 26.04
/// underneath, `ID_LIKE=debian`) has dracut for its initramfs and its own
/// GRUB, signed on the machine with a MOK behind shim, which load keeps as
/// the ESP brings it back (docs/design/anduinos.md); its test is
/// `anduinos2` in `HoltTest1U`.  Kali Linux (Debian testing with its own
/// archive) boots as Debian 13 does on UEFI without shim: initramfs-tools,
/// GRUB in EFI/kali and update-grub (docs/design/kali.md); its test is
/// `kali` in `HoltTest1Obscure`.  GNU Guix System is a store system, as
/// NixOS is (`crate::store)`: each generation's kernel and initrd are store
/// items that GRUB reads from /, so nothing is rebuilt, and on BIOS the
/// source's own boot.img and core.img are written to the new disk by the
/// rescue's grub-bios-setup (docs/design/guix.md); its tests
/// are `guix15` (UEFI) and `guix15-bios` in `HoltTest1Obscure`.  Vanilla OS 3
/// (Debian sid) runs `ABRoot` v2 (`crate::abroot)`: two roots on LVM thin LVs
/// and an init LV with their kernels, all restored as they were, with no
/// initramfs, grub-install or update-grub (docs/design/vanillaos.md); its
/// test is `vanilla3` in `HoltTest1Obscure`.  Ubuntu Core 24 (all-snap, its
/// `/` the core24 base snap) is the files on the pc gadget's four
/// partitions (`crate::snapcore`), restored as they were with every LUKS2
/// header kept (`crate::luksheader`), and nothing regenerated
/// (docs/design/ubuntu-core.md); its tests are `uc24` and `uc24-fde` in
/// `HoltTest1U`.  Qubes OS 4.3's dom0 (Fedora 41 underneath, no `ID_LIKE`)
/// boots Xen through GRUB 2 without shim, with dracut and grub2-mkconfig's
/// `20_linux_xen`; its qubes' volumes are thin LVs and their snapshots, taken
/// as raw images (`crate::qubes`, docs/design/qubes.md); its test is `qubes43`
/// in `HoltTest1Obscure`.
pub const SUPPORTED: &[(&str, &str, &str)] = &[
    ("debian", "13", "Debian 13"),
    ("linuxmint", "22", "Linux Mint 22"),
    ("ubuntu", "24", "Ubuntu 24.04"),
    ("ubuntu", "26", "Ubuntu 26.04"),
    ("zorin", "18", "Zorin OS 18"),
    ("neon", "24", "KDE neon"),
    // Ubuntu 24.04 underneath (ID_LIKE="ubuntu debian"): VERSION_ID is "8"
    // (VERSION="8.1"), so every 8.x point release; OS 9 when a guest shows it.
    ("elementary", "8", "elementary OS 8"),
    // Rolling: Arch's os-release has no VERSION_ID.
    ("arch", "", "Arch Linux"),
    // Each release by its own row, as Debian 12/13 and FreeBSD 14/15 are:
    // a Fedora 46 is checked before it's claimed.  45 (the Thinkpad's
    // Workstation, BackShot1) boots as 44 does: shim, grub2-mkconfig with
    // BLS entries, dracut.  Its atomic desktops aren't claimed by this row
    // (check_supported).
    ("fedora", "44", "Fedora 44"),
    ("fedora", "45", "Fedora 45"),
    ("alpine", "3", "Alpine Linux 3"),
    // Rolling, like Arch.  Older installs have no VERSION_ID; ISOs from
    // late 2026 write the snapshot's date (VERSION_ID=20261102, with
    // BUILD_ID=rolling), so "*" takes either.
    ("artix", "*", "Artix Linux"),
    // Rolling: VERSION_ID is the snapshot's year and month (2026.11).
    ("aerynos", "*", "AerynOS"),
    // Solus (no ID_LIKE): eopkg, clr-boot-manager with shim and systemd-boot,
    // the ESP at /boot out of fstab (docs/design/solus.md).  A curated
    // rolling release whose VERSION_ID changes only with an ISO (4.9): by
    // the major, so a 5 is checked before it's claimed.
    ("solus", "4", "Solus 4"),
    ("debian", "12", "Debian 12"),
    ("devuan", "6", "Devuan 6"),
    ("pop", "24", "Pop!_OS 24.04"),
    // Debian sid underneath, ABRoot v2 (crate::abroot): VERSION_ID="3.0",
    // taken by its major.  Not 2 (Orchid) until a guest shows it.
    ("vanilla", "3", "Vanilla OS 3"),
    // Rolling, Arch-based.
    ("cachyos", "", "CachyOS"),
    ("void", "", "Void Linux"),
    ("nixos", "26", "NixOS 26.05"),
    // A store system as NixOS is (crate::store), rolling between releases
    // (guix pull): os-release has no VERSION_ID (docs/design/guix.md,
    // decision 5), so one that starts writing it is checked before it's claimed.
    ("guix", "", "Guix System"),
    // Rolling: VERSION_ID is the snapshot's date.
    ("opensuse-tumbleweed", "*", "openSUSE Tumbleweed"),
    // Tumbleweed's packages on a read-only root that changes only through
    // transactional-update (crate::transactional): MicroOS (GRUB with
    // grub2-snapper-plugin) and Aeon (its GNOME desktop: systemd-boot with
    // sdbootutil's per-snapshot entries, LUKS2 unlocked by TPM2).  Both
    // rolling, VERSION_ID the snapshot's date, as Tumbleweed's is
    // (docs/design/aeon.md).  Aeon says ID=aeon (opensuse-microos and
    // opensuse-aeon only in its ID_LIKE), so each by its own row.
    ("opensuse-microos", "*", "openSUSE MicroOS"),
    ("aeon", "*", "Aeon"),
    // Leap 16 (Agama's installer): Tumbleweed's tools (zypper, dracut,
    // grub2, snapper on Btrfs, SELinux), released by point (16.0, 16.1), so
    // by the major; not 15.x, whose installer and tools differ
    // (docs/design/leap16.md, decision 2).
    ("opensuse-leap", "16", "openSUSE Leap 16"),
    ("rocky", "10", "Rocky Linux 10"),
    // dom0 on Fedora 41 (ID=qubes, no ID_LIKE), booting Xen through GRUB 2
    // without shim, its qubes' volumes thin LVs taken as raw images
    // (crate::qubes).  VERSION_ID="4.3": by the major here, and 4.2 or older
    // refused by its minor in check_supported (docs/design/qubes.md).
    ("qubes", "4", "Qubes OS 4.3"),
    // Arch-based, rolling.
    ("omarchy", "*", "Omarchy"),
    // Arch-based, rolling; none has a VERSION_ID.  EndeavourOS boots by
    // systemd-boot with dracut, Manjaro by GRUB with mkinitcpio.  BigLinux
    // is Manjaro underneath (biglinux-name rewrites its os-release): GRUB,
    // mkinitcpio, btrfs with Timeshift.
    ("endeavouros", "", "EndeavourOS"),
    ("manjaro", "", "Manjaro"),
    ("biglinux", "", "BigLinux"),
    // Arch-based, rolling, no VERSION_ID: GRUB on btrfs, with dracut through
    // garuda-dracut-support, and snapper's snapshots nested in @.
    ("garuda", "", "Garuda Linux"),
    // Slackware 15.0 (VERSION_ID=15.0, unquoted; no ID_LIKE): LILO or
    // ELILO, the huge kernel or generic with mkinitrd, pkgtools
    // (docs/design/slackware.md).  By the major, so a 15.1 would pass: it's
    // checked before it's claimed.  -current (VERSION_CODENAME=current) is
    // taken by check_supported, not by this row: the Thinkpad runs a pinned
    // -current snapshot (docs/design/slackware.md, "Slackware -current").
    ("slackware", "15", "Slackware 15"),
    // Rolling: Chimera's os-release has no VERSION_ID at all (only its image
    // names carry the snapshot's date).  "*" rather than "", as for
    // AerynOS: a snapshot that starts writing one is still Chimera.
    ("chimera", "*", "Chimera Linux"),
    // Rolling: VERSION_ID is baselayout's version (2.18), not a release.
    // Funtoo, Calculate and the other ID_LIKE=gentoo systems aren't it.
    ("gentoo", "*", "Gentoo Linux"),
    // ostree-based, on Fedora's atomic desktops (Kinoite): ID=bazzite,
    // VERSION_ID the Fedora release.  Silverblue and Kinoite themselves say
    // fedora 44, so the Fedora row takes them (44 only, check_supported);
    // ostree is told by its own markers (crate::ostree::detect).
    ("bazzite", "44", "Bazzite 44"),
    // Debian sid underneath (ID_LIKE=debian), rolling, but VERSION_ID is
    // its release ("4", Nest): a PikaOS 5 is checked before it's claimed.
    ("pika", "4", "PikaOS 4"),
    // Ubuntu 26.04 underneath (ID_LIKE=debian only), with dracut, and
    // AnduinOS's own MOK-signed GRUB behind shim, kept from the ESP.  Not
    // 1.x: Ubuntu 24.04's casper, initramfs-tools and signed GRUB.
    ("anduinos", "2", "AnduinOS 2"),
    // Debian testing underneath (ID_LIKE=debian), rolling, with its own
    // archive: VERSION_ID is the snapshot's year.release (2026.3), as
    // AerynOS's is, so every snapshot (docs/design/kali.md, decision 2).
    ("kali", "*", "Kali Linux"),
    // Rolling, Alpha: VERSION_ID is the daily build (202611270255), as
    // AerynOS's is a snapshot.  systemd-sysupdate with erofs /usr files on
    // a btrfs top level (crate::sysupdate): its markers, not this row, keep
    // a layout bko-holt hasn't seen from being taken (RootFSv3 with
    // etc-mstack, the testing edition only; docs/design/kde-linux.md).
    ("kde-linux", "*", "KDE Linux"),
    // Arch underneath (ID_LIKE=arch), an image system of two A/B slots on
    // GPT partlabels (crate::steamos): VERSION_ID is the release (3.9.5),
    // taken by its major, so 3.8, 3.9 and 3.10 alike (docs/design/steamos.md,
    // decision 1).  Not by ID_LIKE=arch: Arch's row doesn't take it.
    ("steamos", "3", "SteamOS 3"),
    // Not Linux (other/): os-release, or uname for those without one.
    ("freebsd", "14", "FreeBSD 14"),
    ("freebsd", "15", "FreeBSD 15"),
    ("hardenedbsd", "15", "HardenedBSD 15"),
    ("ghostbsd", "26", "GhostBSD 26"),
    ("netbsd", "11", "NetBSD 11"),
    ("alpinebsd", "1", "AlpineBSD 1"),
    ("openbsd", "8", "OpenBSD 8"),
    ("dragonfly", "6", "DragonFly 6"),
    ("haiku", "*", "Haiku"),
    // illumos: VERSION_ID is the release, r151058y.
    ("omnios", "*", "OmniOS"),
    // Rolling (Hipster): VERSION_ID is the snapshot (2026.10), from
    // /etc/release, as it has no os-release (openindiana_release).
    ("openindiana", "*", "OpenIndiana"),
    // Read off their disks (dump --disk): no VERSION_ID.
    ("redox", "", "Redox"),
    ("sculpt", "", "Sculpt OS"),
    // The base snap's os-release (core24): VERSION_ID="24".  Not 20 or 22,
    // whose FDE keys live otherwise, nor 26 until a guest shows it.
    ("ubuntu-core", "24", "Ubuntu Core 24"),
];

/// The `VERSION_ID` majors Slackware -current's os-release has carried
/// (`VERSION_CODENAME=current)`: 15 before `aaa_base` 16.0, 16 since.
pub const SLACKWARE_CURRENT_MAJORS: [&str; 2] = ["15", "16"];

/// Whether ID, `VERSION_ID` and `VERSION_CODENAME` are Slackware -current at a
/// `VERSION_ID` it has carried (`check_supported`; load's layout check too).
pub fn slackware_current(id: &str, version_id: &str, codename: Option<&str>) -> bool {
    id == "slackware"
        && codename == Some("current")
        && SLACKWARE_CURRENT_MAJORS.contains(&version_id.split('.').next().unwrap_or(""))
}

/// Ok unless this is Slackware -current on a MACHINE (`uname -m`) other
/// than `x86_64`: -current's support was built and checked on
/// Slackware64-current (ELILO and LILO, `x86_64` kernels) only, and Slackware
/// `AArch64`, -current only, says it is -current the same way.
pub fn check_slackware_current_machine(
    os: Option<&OsRelease>,
    machine: &str,
) -> Result<(), String> {
    let Some(os) = os else { return Ok(()) };
    let (id, ver) = (
        os.get("ID").unwrap_or(""),
        os.get("VERSION_ID").unwrap_or(""),
    );
    if slackware_current(id, ver, os.get("VERSION_CODENAME")) && machine != "x86_64" {
        return Err(format!(
            "this is Slackware -current on {machine}, and bko-holt supports -current on x86_64 (Slackware64-current) only"
        ));
    }
    Ok(())
}

/// Whether ID and `VERSION_ID` name a supported system: the same major version
/// ("22.3" is Mint 22).  Derivatives don't count by their `ID_LIKE`.
pub fn supported(id: &str, version_id: &str) -> bool {
    let major = version_id.split('.').next().unwrap_or("");
    SUPPORTED
        .iter()
        .any(|(i, v, _)| *i == id && (*v == major || *v == "*"))
}

/// "Debian 13, Linux Mint 22, ... or Zorin OS 18", for messages.
pub fn supported_names() -> String {
    let n: Vec<&str> = SUPPORTED.iter().map(|s| s.2).collect();
    match n.split_last() {
        Some((last, rest)) if !rest.is_empty() => format!("{} or {last}", rest.join(", ")),
        _ => n.join(""),
    }
}

/// Ok on a supported system, else why not.
pub fn check_supported(os: Option<&OsRelease>) -> Result<(), String> {
    let Some(os) = os else {
        return Err(
            "no /etc/os-release or /usr/lib/os-release: can't tell which system this is".into(),
        );
    };
    let id = os.get("ID").unwrap_or("");
    let ver = os.get("VERSION_ID").unwrap_or("");
    // Slackware -current says what it is by VERSION_CODENAME=current, with
    // VERSION_ID the release it leads to: 15.0 (VERSION="15.0+") until
    // aaa_base 16.0, 16.0 since (VERSION="16.0", PRETTY_NAME "... (post 15.0
    // -current)").  Supported by that codename (docs/design/slackware.md,
    // "Slackware -current"), at those two only; a VERSION ending in "+"
    // under any other codename, or -current at another VERSION_ID, isn't
    // one bko-holt knows.
    if id == "slackware" {
        let codename = os.get("VERSION_CODENAME").unwrap_or("");
        if codename == "current" {
            if slackware_current(id, ver, Some(codename)) {
                return Ok(());
            }
            return Err(format!(
                "this is Slackware -current (ID=slackware, VERSION_ID={ver}, VERSION_CODENAME=current), and bko-holt dump supports -current only at VERSION_ID 15.0 or 16.0, which -current's os-release has carried so far"
            ));
        }
        if os.get("VERSION").is_some_and(|v| v.ends_with('+')) {
            return Err(format!(
                "this is a Slackware development tree (ID=slackware, VERSION_ID={ver}, VERSION={}, VERSION_CODENAME={codename}), and bko-holt dump supports only the Slackware 15.0 release and -current (VERSION_CODENAME=current)",
                os.get("VERSION").unwrap_or("")
            ));
        }
    }
    // Qubes OS: 4.3 and later (its row takes the major); 4.2's support
    // ended 2026-06-21, and its volumes and boot differ.
    if id == "qubes"
        && ver
            .split('.')
            .nth(1)
            .and_then(|m| m.parse::<u64>().ok())
            .is_some_and(|m| ver.starts_with("4.") && m < 3)
    {
        return Err(format!(
            "this is Qubes OS {ver} (ID=qubes, VERSION_ID={ver}), and bko-holt dump supports Qubes OS 4.3 and later only"
        ));
    }
    // Fedora's atomic desktops (Silverblue, Kinoite: ID=fedora with an
    // OSTREE_VERSION) are claimed at 44 only, kinoite44's, as Bazzite is by
    // its own row: the Fedora 45 row is Workstation's.
    if id == "fedora" && os.get("OSTREE_VERSION").is_some() && ver.split('.').next() != Some("44") {
        return Err(format!(
            "this is a Fedora {ver} atomic desktop (ID=fedora, VERSION_ID={ver}, OSTREE_VERSION={}), and bko-holt dump supports Fedora's atomic desktops (Silverblue, Kinoite) at 44 only",
            os.get("OSTREE_VERSION").unwrap_or("")
        ));
    }
    if supported(id, ver) {
        return Ok(());
    }
    let name = os
        .get("PRETTY_NAME")
        .map_or_else(|| format!("{id} {ver}"), str::to_string);
    Err(format!(
        "this is {} (ID={id}, VERSION_ID={ver}), and bko-holt dump supports only {} for now",
        name.trim(),
        supported_names()
    ))
}

pub fn to_json(sys: &Sys, os: Option<&OsRelease>) -> Value {
    let g = |k: &str| os.and_then(|o| o.get(k)).map(str::to_string);
    Obj::new()
        .set("id", g("ID"))
        .set("version_id", g("VERSION_ID"))
        .set("version_codename", g("VERSION_CODENAME"))
        .set("pretty_name", g("PRETTY_NAME"))
        .set("debian_version", sys.read_trim("/etc/debian_version"))
        .set(
            "kernel_release",
            sys.read_trim("/proc/sys/kernel/osrelease"),
        )
        .set("kernel_version", sys.read_trim("/proc/sys/kernel/version"))
        .set("architecture", std::env::consts::ARCH)
        // The machine's, as `uname -m` says (crate::arch): what load holds
        // the dump to.  "architecture" is the dumping binary's own.
        .set("machine", crate::arch::machine(sys))
        .build()
}

#[cfg(test)]
pub(crate) mod tests {
    use super::*;
    use crate::sys::FakeRunner;
    use crate::testutil::Tree;

    const TRIXIE: &str = "PRETTY_NAME=\"Debian GNU/Linux 13 (trixie)\"\nNAME=\"Debian GNU/Linux\"\nVERSION_ID=\"13\"\nVERSION=\"13 (trixie)\"\nVERSION_CODENAME=trixie\nDEBIAN_VERSION_FULL=13.1\nID=debian\nHOME_URL=\"https://www.debian.org/\"\nSUPPORT_URL=\"https://www.debian.org/support\"\nBUG_REPORT_URL=\"https://bugs.debian.org/\"\n";

    #[test]
    fn parses_trixie() {
        let o = parse(TRIXIE);
        assert_eq!(o.get("ID"), Some("debian"));
        assert_eq!(o.get("VERSION_ID"), Some("13"));
        assert_eq!(o.get("VERSION_CODENAME"), Some("trixie"));
        assert_eq!(o.get("PRETTY_NAME"), Some("Debian GNU/Linux 13 (trixie)"));
        assert_eq!(o.get("NOPE"), None);
        assert!(check_supported(Some(&o)).is_ok());
    }

    #[test]
    fn quoting_and_junk() {
        let o = parse(
            "# comment\n\nA='single \"q\"'\nB=\"a\\\"b\\\\c\\$d\\`e\\x\"\n  C = bare \nnot a line\n=nokey\nBAD-KEY=1\nA=override\n",
        );
        assert_eq!(o.get("A"), Some("override"));
        assert_eq!(o.get("B"), Some("a\"b\\c$d`e\\x"));
        assert_eq!(o.get("C"), Some("bare"));
        assert_eq!(o.get("BAD-KEY"), None);
        assert_eq!(unquote("\""), "\"");
        assert_eq!(unquote("''"), "");
        assert_eq!(unquote("\"\""), "");
    }

    #[test]
    fn refuses_other_systems() {
        let buster =
            parse("ID=debian\nVERSION_ID=\"10\"\nPRETTY_NAME=\"Debian GNU/Linux 10 (buster)\"\n");
        let e = check_supported(Some(&buster)).unwrap_err();
        assert!(e.contains("Debian GNU/Linux 10 (buster)"), "{e}");
        assert!(check_supported(Some(&parse("ID=debian\nVERSION_ID=\"12\"\n"))).is_ok());
        let ubuntu = parse("ID=ubuntu\nID_LIKE=debian\nVERSION_ID=\"26.04\"\n");
        assert!(check_supported(Some(&ubuntu)).is_ok());
        let ubuntu22 = parse("ID=ubuntu\nID_LIKE=debian\nVERSION_ID=\"22.04\"\n");
        assert!(
            check_supported(Some(&ubuntu22))
                .unwrap_err()
                .contains("ubuntu 22.04")
        );
        assert!(
            check_supported(Some(&parse(
                "ID=zorin\nID_LIKE=\"ubuntu debian\"\nVERSION_ID=\"18\"\n"
            )))
            .is_ok()
        );
        // KDE neon User Edition's /etc/os-release (neon's base-files); not
        // the neon that was on Ubuntu 22.04, nor one on 26.04 yet.
        let neon = "PRETTY_NAME=\"KDE neon User Edition\"\nNAME=\"KDE neon\"\nVERSION_ID=\"24.04\"\nVERSION=\"User Edition\"\nVERSION_CODENAME=noble\nID=neon\nID_LIKE=\"ubuntu debian\"\nUBUNTU_CODENAME=noble\nLOGO=start-here-kde-neon\n";
        assert!(check_supported(Some(&parse(neon))).is_ok());
        assert!(check_supported(Some(&parse(&neon.replace("24.04", "22.04")))).is_err());
        assert!(check_supported(Some(&parse(&neon.replace("24.04", "26.04")))).is_err());
        assert!(e.contains("Debian 13, Linux Mint 22, Ubuntu 24.04, Ubuntu 26.04, Zorin OS 18, KDE neon, elementary OS 8, Arch Linux, Fedora 44, Fedora 45, Alpine Linux 3, Artix Linux, AerynOS, Solus 4, Debian 12, Devuan 6, Pop!_OS 24.04, Vanilla OS 3, CachyOS, Void Linux, NixOS 26.05, Guix System, openSUSE Tumbleweed, openSUSE MicroOS, Aeon, openSUSE Leap 16, Rocky Linux 10, Qubes OS 4.3, Omarchy, EndeavourOS, Manjaro, BigLinux, Garuda Linux, Slackware 15, Chimera Linux, Gentoo Linux, Bazzite 44, PikaOS 4, AnduinOS 2, Kali Linux, KDE Linux, SteamOS 3, FreeBSD 14, FreeBSD 15, HardenedBSD 15, GhostBSD 26, NetBSD 11, AlpineBSD 1, OpenBSD 8, DragonFly 6, Haiku, OmniOS, OpenIndiana, Redox, Sculpt OS or Ubuntu Core 24"), "{e}");
        for t in [
            "ID=devuan\nVERSION_ID=6\n",
            "ID=pop\nVERSION_ID=\"24.04\"\n",
            "ID=cachyos\nBUILD_ID=rolling\n",
            "ID=void\n",
            "ID=nixos\nVERSION_ID=\"26.05\"\n",
            "ID=\"opensuse-tumbleweed\"\nVERSION_ID=\"20261101\"\n",
            "ID=rocky\nVERSION_ID=\"10.2\"\n",
        ] {
            assert!(check_supported(Some(&parse(t))).is_ok(), "{t}");
        }
        assert!(check_supported(Some(&parse("ID=alpine\nVERSION_ID=3.24.3\n"))).is_ok());
        assert!(check_supported(Some(&parse("ID=artix\nNAME=\"Artix Linux\"\n"))).is_ok());
        // A real xfce-dinit install from artix-xfce-dinit-20261102.
        assert!(check_supported(Some(&parse("NAME=\"Artix Linux\"\nID=artix\nBUILD_ID=rolling\nVERSION_ID=20261102\nVARIANT=xfce-dinit\n"))).is_ok());
        assert!(
            check_supported(Some(&parse(
                "NAME=\"EndeavourOS\"\nID=\"endeavouros\"\nID_LIKE=\"arch\"\nBUILD_ID=rolling\n"
            )))
            .is_ok()
        );
        assert!(
            check_supported(Some(&parse(
                "NAME=\"Manjaro Linux\"\nID=manjaro\nID_LIKE=arch\nBUILD_ID=rolling\n"
            )))
            .is_ok()
        );
        // Garuda's real os-release (read 2026-11-14 from its ISO).
        assert!(check_supported(Some(&parse("NAME=\"Garuda Linux\"\nPRETTY_NAME=\"Garuda Linux\"\nID=garuda\nID_LIKE=arch\nBUILD_ID=rolling\nANSI_COLOR=\"38;2;23;147;209\"\nHOME_URL=\"https://garudalinux.org/\"\nLOGO=garudalinux\n"))).is_ok());
        // BigLinux's real os-release, as biglinux-name writes it.
        assert!(check_supported(Some(&parse("NAME=\"BigLinux based on Manjaro Linux\"\nPRETTY_NAME=\"BigLinux based on Manjaro Linux\"\nID=\"biglinux\"\nID_LIKE=arch\nBUILD_ID=rolling\n"))).is_ok());
        assert!(
            check_supported(Some(&parse(
                "ID=\"aerynos\"\nVERSION_ID=\"2026.11\"\nID_LIKE=\"linux\"\n"
            )))
            .is_ok()
        );
        assert!(check_supported(Some(&parse("ID=\"aerynos\"\nVERSION_ID=\"2027.01\"\n"))).is_ok());
        // KDE Linux's os-release as its 202611270255 image has it ([seen],
        // docs/design/kde-linux.md): every daily build, by "*".
        let kde = "NAME=\"KDE Linux\"\nPRETTY_NAME=\"KDE Linux\"\nID=kde-linux\nDEFAULT_HOSTNAME=\"$-????\"\nVERSION=\"2026-11-27 02:55\"\nVERSION_ID=202611270255\nBUILD_ID=unknownSHA\nIMAGE_ID=\"kde-linux\"\nIMAGE_VERSION=\"202611270255\"\nVARIANT=\"testing\"\nVARIANT_ID=\"testing\"\nKDE_LINUX_SYSUPDATE_ENDPOINT=sysupdate/v2\n";
        assert!(check_supported(Some(&parse(kde))).is_ok());
        assert!(check_supported(Some(&parse("ID=kde-linux\nVERSION_ID=202701010255\n"))).is_ok());
        // Not KDE neon, nor a derivative by its ID_LIKE.
        assert!(check_supported(Some(&parse("ID=kdeos\nID_LIKE=kde-linux\nVERSION_ID=1\n"))).is_err());
        // Linux Mint 22.x; not 21, and not Mint's Debian edition (LMDE).
        let mint = parse("ID=linuxmint\nID_LIKE=\"ubuntu debian\"\nVERSION_ID=\"22.3\"\n");
        assert!(check_supported(Some(&mint)).is_ok());
        assert!(check_supported(Some(&parse("ID=linuxmint\nVERSION_ID=\"21.3\"\n"))).is_err());
        assert!(
            check_supported(Some(&parse("ID=lmde\nID_LIKE=debian\nVERSION_ID=\"6\"\n"))).is_err()
        );
        assert!(!supported("debian", "130"));
        assert!(check_supported(Some(&parse("ID=arch\nBUILD_ID=rolling\n"))).is_ok());
        assert!(check_supported(Some(&parse("ID=fedora\nVERSION_ID=44\n"))).is_ok());
        assert!(check_supported(Some(&parse("ID=fedora\nVERSION_ID=43\n"))).is_err());
        assert!(check_supported(Some(&parse("ID=fedora\nVERSION_ID=46\n"))).is_err());
        // Debian testing/sid has no VERSION_ID.
        let sid = parse("ID=debian\nPRETTY_NAME=\"Debian GNU/Linux forky/sid\"\n");
        assert!(check_supported(Some(&sid)).is_err());
        // A derivative that claims VERSION_ID 13 isn't Debian.
        let devuan = parse("ID=devuan\nID_LIKE=debian\nVERSION_ID=13\n");
        assert!(check_supported(Some(&devuan)).is_err());
        assert!(check_supported(None).unwrap_err().contains("os-release"));
    }

    /// The chimera guest's /etc/os-release (snapshot 20261103): ID quoted,
    /// and no `VERSION_ID` line.
    const CHIMERA: &str = "NAME=\"Chimera\"\nID=\"chimera\"\nPRETTY_NAME=\"Chimera Linux\"\n";

    #[test]
    fn chimera() {
        let o = parse(CHIMERA);
        assert_eq!(o.get("ID"), Some("chimera"));
        assert_eq!(o.get("NAME"), Some("Chimera"));
        assert_eq!(o.get("PRETTY_NAME"), Some("Chimera Linux"));
        assert_eq!(o.get("VERSION_ID"), None);
        assert!(check_supported(Some(&o)).is_ok());
        assert!(supported("chimera", ""));
        assert!(supported("chimera", "20261103"));
        assert!(supported_names().contains("Chimera Linux"));
        // Not by its likeness: another musl/apk system isn't Chimera.
        assert!(
            check_supported(Some(&parse("ID=\"chimera-ish\"\nID_LIKE=\"chimera\"\n"))).is_err()
        );
    }

    /// The gentoo guest's /etc/os-release (baselayout 2.18), the same as the
    /// Thinkpad's: single quotes, `VERSION_ID` baselayout's, no `ID_LIKE`.
    const GENTOO: &str = "NAME='Gentoo'\nID='gentoo'\nPRETTY_NAME='Gentoo Linux'\nVERSION='2.18'\nVERSION_ID='2.18'\nHOME_URL='https://www.gentoo.org/'\nSUPPORT_URL='https://www.gentoo.org/support/'\nBUG_REPORT_URL='https://bugs.gentoo.org/'\nANSI_COLOR='1;32'\n";

    #[test]
    fn gentoo() {
        let o = parse(GENTOO);
        assert_eq!(o.get("ID"), Some("gentoo"));
        assert_eq!(o.get("NAME"), Some("Gentoo"));
        assert_eq!(o.get("PRETTY_NAME"), Some("Gentoo Linux"));
        assert_eq!(o.get("VERSION_ID"), Some("2.18"));
        assert_eq!(o.get("ANSI_COLOR"), Some("1;32"));
        assert_eq!(o.get("ID_LIKE"), None);
        assert!(check_supported(Some(&o)).is_ok());
        // Any baselayout: it isn't a release.
        assert!(supported("gentoo", "2.17"));
        assert!(supported("gentoo", ""));
        assert!(supported_names().contains("Chimera Linux, Gentoo Linux, Bazzite 44"));
        // Not by its likeness: Funtoo and Calculate say ID_LIKE=gentoo.
        for t in [
            "ID=funtoo\nID_LIKE=gentoo\nVERSION_ID=2.4\n",
            "ID=calculate\nID_LIKE=gentoo\nVERSION_ID=\"25\"\nPRETTY_NAME=\"Calculate Linux 25\"\n",
        ] {
            let e = check_supported(Some(&parse(t))).unwrap_err();
            assert!(e.contains("Gentoo Linux"), "{e}");
        }
    }

    /// The Thinkpad's /usr/lib/os-release (Bazzite 44.20261012), and
    /// kinoite44's (Fedora Kinoite 44.20261111.0).
    #[test]
    fn bazzite_and_kinoite() {
        let b = parse(
            "NAME=\"Bazzite\"\nVERSION=\"44.20261012.0 (Kinoite)\"\nID=bazzite\nID_LIKE=\"fedora\"\nVERSION_ID=44\nVARIANT_ID=bazzite\nPRETTY_NAME=\"Bazzite 44 (FROM Fedora Kinoite)\"\nOSTREE_VERSION='44.20261012.0'\n",
        );
        assert_eq!(b.get("ID"), Some("bazzite"));
        assert_eq!(b.get("OSTREE_VERSION"), Some("44.20261012.0"));
        assert!(check_supported(Some(&b)).is_ok());
        assert!(supported_names().contains("Bazzite 44"));
        // Not the next Fedora major until a guest or a run shows it.
        assert!(
            check_supported(Some(&parse("ID=bazzite\nID_LIKE=fedora\nVERSION_ID=45\n"))).is_err()
        );
        // Bluefin and Aurora aren't Bazzite, and ID_LIKE=fedora doesn't let them in.
        assert!(
            check_supported(Some(&parse("ID=bluefin\nID_LIKE=fedora\nVERSION_ID=44\n"))).is_err()
        );
        let k = parse(
            "NAME=\"Fedora Linux\"\nVERSION=\"44.20261111.0 (Kinoite)\"\nID=fedora\nVERSION_ID=44\nVARIANT_ID=kinoite\nOSTREE_VERSION='44.20261111.0'\n",
        );
        assert!(check_supported(Some(&k)).is_ok());
        // Kinoite 45 isn't claimed by Workstation's Fedora 45 row.
        let k45 = parse(
            "NAME=\"Fedora Linux\"\nVERSION=\"45.20261118.0 (Kinoite)\"\nID=fedora\nVERSION_ID=45\nVARIANT_ID=kinoite\nOSTREE_VERSION='45.20261118.0'\n",
        );
        assert!(
            check_supported(Some(&k45))
                .unwrap_err()
                .contains("atomic desktops (Silverblue, Kinoite) at 44 only")
        );
    }

    /// The Thinkpad's /etc/os-release, Fedora 45 Workstation (`BackShot1`,
    /// 2026-11-20).
    const FEDORA45: &str = "NAME=\"Fedora Linux\"\nVERSION=\"45 (Workstation Edition)\"\nRELEASE_TYPE=stable\nID=fedora\nVERSION_ID=45\nVERSION_CODENAME=\"\"\nPRETTY_NAME=\"Fedora Linux 45 (Workstation Edition)\"\nANSI_COLOR=\"0;38;2;60;110;180\"\nLOGO=fedora-logo-icon\nCPE_NAME=\"cpe:/o:fedoraproject:fedora:45\"\nDEFAULT_HOSTNAME=\"fedora\"\nHOME_URL=\"https://fedoraproject.org/\"\nDOCUMENTATION_URL=\"https://docs.fedoraproject.org/en-US/fedora/f45/\"\nSUPPORT_URL=\"https://ask.fedoraproject.org/\"\nBUG_REPORT_URL=\"https://bugzilla.redhat.com/\"\nREDHAT_BUGZILLA_PRODUCT=\"Fedora\"\nREDHAT_BUGZILLA_PRODUCT_VERSION=45\nREDHAT_SUPPORT_PRODUCT=\"Fedora\"\nREDHAT_SUPPORT_PRODUCT_VERSION=45\nSUPPORT_END=2027-11-24\nVARIANT=\"Workstation Edition\"\nVARIANT_ID=workstation\n";

    #[test]
    fn fedora45() {
        let o = parse(FEDORA45);
        assert_eq!(o.get("ID"), Some("fedora"));
        assert_eq!(o.get("VERSION_ID"), Some("45"));
        assert_eq!(o.get("VERSION_CODENAME"), Some(""));
        assert_eq!(o.get("VARIANT_ID"), Some("workstation"));
        assert!(check_supported(Some(&o)).is_ok());
        assert!(supported("fedora", "45"));
        assert!(supported_names().contains("Fedora 44, Fedora 45, Alpine Linux 3"));
    }

    /// `PikaOS` 4's /etc/os-release (its base-files' source).
    const PIKA: &str = "PRETTY_NAME=\"PikaOS 4\"\nNAME=\"PikaOS\"\nVERSION_ID=\"4\"\nVERSION=\"4\"\nVERSION_CODENAME=nest\nID=pika\nID_LIKE=debian\nDEBIAN_CODENAME=sid\n";

    #[test]
    fn pikaos() {
        let o = parse(PIKA);
        assert_eq!(o.get("ID"), Some("pika"));
        assert_eq!(o.get("ID_LIKE"), Some("debian"));
        assert_eq!(o.get("VERSION_CODENAME"), Some("nest"));
        assert_eq!(o.get("DEBIAN_CODENAME"), Some("sid"));
        assert!(check_supported(Some(&o)).is_ok());
        assert!(supported_names().contains("Bazzite 44, PikaOS 4"));
        // Not PikaOS 5 until a guest shows it, nor PikaOS 3 (Ubuntu-based).
        let e = check_supported(Some(&parse(&PIKA.replace("\"4\"", "\"5\"")))).unwrap_err();
        assert!(
            e.contains("(ID=pika, VERSION_ID=5)") && e.contains("Bazzite 44, PikaOS 4,"),
            "{e}"
        );
        assert!(check_supported(Some(&parse(&PIKA.replace("\"4\"", "\"3\"")))).is_err());
        // Another ID_LIKE=debian system on sid isn't PikaOS, nor is a
        // derivative that claims its VERSION_ID.
        assert!(
            check_supported(Some(&parse(
                "PRETTY_NAME=\"Siduction\"\nID=siduction\nID_LIKE=debian\nVERSION_ID=\"4\"\n"
            )))
            .is_err()
        );
        assert!(
            check_supported(Some(&parse(
                "ID=pika-ish\nID_LIKE=\"pika debian\"\nVERSION_ID=\"4\"\n"
            )))
            .is_err()
        );
    }

    /// elementary OS 8.1's /etc/os-release (the elementary8 guest, built from
    /// elementaryos-8.1-stable-amd64.20260219.iso; `UBUNTU_CODENAME` is there
    /// twice).
    const ELEMENTARY: &str = "PRETTY_NAME=\"elementary OS 8\"\nNAME=\"elementary OS\"\nVERSION_ID=\"8\"\nVERSION=\"8.1\"\nVERSION_CODENAME=circe\nID=elementary\nID_LIKE=\"ubuntu debian\"\nUBUNTU_CODENAME=noble\nUBUNTU_VERSION_ID=\"24.04\"\nUBUNTU_CODENAME=noble\n";

    #[test]
    fn elementary() {
        let o = parse(ELEMENTARY);
        assert_eq!(o.get("ID"), Some("elementary"));
        assert_eq!(o.get("ID_LIKE"), Some("ubuntu debian"));
        assert_eq!(o.get("VERSION_ID"), Some("8"));
        assert_eq!(o.get("VERSION"), Some("8.1"));
        assert_eq!(o.get("VERSION_CODENAME"), Some("circe"));
        assert_eq!(o.get("UBUNTU_CODENAME"), Some("noble"));
        assert_eq!(o.get("PRETTY_NAME"), Some("elementary OS 8"));
        assert!(check_supported(Some(&o)).is_ok());
        assert!(supported("elementary", "8.1"));
        assert!(supported_names().contains("KDE neon, elementary OS 8, Arch Linux"));
        // Not 7 (Ubuntu 22.04's stack), nor a 9 until a guest shows it.
        let e = check_supported(Some(&parse(
            &ELEMENTARY
                .replace("VERSION_ID=\"8\"", "VERSION_ID=\"7\"")
                .replace("elementary OS 8\"", "elementary OS 7\""),
        )))
        .unwrap_err();
        assert!(
            e.contains("this is elementary OS 7 (ID=elementary, VERSION_ID=7)")
                && e.contains("elementary OS 8,"),
            "{e}"
        );
        assert!(
            check_supported(Some(&parse(
                &ELEMENTARY.replace("VERSION_ID=\"8\"", "VERSION_ID=\"9\"")
            )))
            .is_err()
        );
        // Not by its likeness: Ubuntu 24.04 is supported as ubuntu, but
        // another ID_LIKE="ubuntu debian" system claiming VERSION_ID 8 isn't.
        assert!(check_supported(Some(&parse("ID=elementary-remix\nID_LIKE=\"elementary ubuntu debian\"\nVERSION_ID=\"8\"\nUBUNTU_CODENAME=noble\n"))).is_err());
    }

    /// `AnduinOS` 2.0.5's /usr/lib/os-release (the anduinos2 guest's ISO,
    /// docs/design/anduinos.md).
    const ANDUINOS: &str = "PRETTY_NAME=\"AnduinOS 2.0.5\"\nNAME=\"AnduinOS\"\nVERSION_ID=\"2.0.5\"\nVERSION=\"2.0.5 (Resolute Ringtail)\"\nVERSION_CODENAME=resolute\nID=anduinos\nID_LIKE=debian\nUBUNTU_CODENAME=resolute\n";

    #[test]
    fn anduinos() {
        let o = parse(ANDUINOS);
        assert_eq!(o.get("ID"), Some("anduinos"));
        assert_eq!(o.get("ID_LIKE"), Some("debian"));
        assert_eq!(o.get("VERSION_ID"), Some("2.0.5"));
        assert_eq!(o.get("UBUNTU_CODENAME"), Some("resolute"));
        assert!(check_supported(Some(&o)).is_ok());
        assert!(supported("anduinos", "2.0.0"));
        assert!(supported_names().contains("PikaOS 4, AnduinOS 2, "));
        // Not 1.x (Ubuntu 24.04's stack), nor a 3 until a guest shows it.
        let e = check_supported(Some(&parse(&ANDUINOS.replace("2.0.5", "1.1.2")))).unwrap_err();
        assert!(
            e.contains("this is AnduinOS 1.1.2 (ID=anduinos, VERSION_ID=1.1.2)")
                && e.contains("AnduinOS 2,"),
            "{e}"
        );
        assert!(check_supported(Some(&parse(&ANDUINOS.replace("\"2.0.5\"", "\"3.0\"")))).is_err());
        // Not by its likeness: another ID_LIKE=debian system on resolute,
        // claiming AnduinOS's VERSION_ID, isn't AnduinOS.
        let lookalike = ANDUINOS
            .replace("ID=anduinos", "ID=anduinos-remix")
            .replace("NAME=\"AnduinOS\"", "NAME=\"Remix\"");
        assert!(check_supported(Some(&parse(&lookalike))).is_err());
        assert!(check_supported(Some(&parse("ID=resolute-desktop\nID_LIKE=debian\nVERSION_ID=\"2.0.5\"\nUBUNTU_CODENAME=resolute\n"))).is_err());
    }

    /// Kali 2026.3's /usr/lib/os-release (base-files 2026.3.1, the kali
    /// guest's netinst ISO, docs/design/kali.md "What K1 found").
    const KALI: &str = "PRETTY_NAME=\"Kali GNU/Linux Rolling\"\nNAME=\"Kali GNU/Linux\"\nVERSION_ID=\"2026.3\"\nVERSION=\"2026.3\"\nVERSION_CODENAME=kali-rolling\nID=kali\nID_LIKE=debian\nHOME_URL=\"https://www.kali.org/\"\nSUPPORT_URL=\"https://forums.kali.org/\"\nBUG_REPORT_URL=\"https://bugs.kali.org/\"\nANSI_COLOR=\"1;31\"\n";

    #[test]
    fn kali() {
        let o = parse(KALI);
        assert_eq!(o.get("ID"), Some("kali"));
        assert_eq!(o.get("ID_LIKE"), Some("debian"));
        assert_eq!(o.get("VERSION_ID"), Some("2026.3"));
        assert_eq!(o.get("VERSION_CODENAME"), Some("kali-rolling"));
        assert_eq!(o.get("PRETTY_NAME"), Some("Kali GNU/Linux Rolling"));
        assert_eq!(o.get("ANSI_COLOR"), Some("1;31"));
        assert!(check_supported(Some(&o)).is_ok());
        assert!(
            supported_names().contains("AnduinOS 2, Kali Linux, KDE Linux, SteamOS 3, FreeBSD 14")
        );
        // Rolling (decision 2): every snapshot, the next year's too, and
        // one with no VERSION_ID at all.
        for v in ["2026.4", "2027.1", "2025.4"] {
            assert!(
                check_supported(Some(&parse(&KALI.replace("2026.3", v)))).is_ok(),
                "{v}"
            );
        }
        assert!(
            check_supported(Some(&parse(&KALI.replace("VERSION_ID=\"2026.3\"\n", "")))).is_ok()
        );
        // Not by its likeness: a remix with its own ID, or a derivative that
        // names Kali in ID_LIKE and claims its VERSION_ID, isn't Kali.
        let remix = KALI
            .replace("ID=kali\n", "ID=kali-remix\n")
            .replace("NAME=\"Kali GNU/Linux\"", "NAME=\"Remix\"");
        let e = check_supported(Some(&parse(&remix))).unwrap_err();
        assert!(
            e.contains("(ID=kali-remix, VERSION_ID=2026.3)")
                && e.contains("AnduinOS 2, Kali Linux,"),
            "{e}"
        );
        assert!(check_supported(Some(&parse("ID=pentestos\nID_LIKE=\"kali debian\"\nVERSION_ID=\"2026.3\"\nVERSION_CODENAME=kali-rolling\n"))).is_err());
        // Debian testing itself (no VERSION_ID), even on Kali's codename.
        assert!(check_supported(Some(&parse("ID=debian\nVERSION_CODENAME=kali-rolling\nPRETTY_NAME=\"Debian GNU/Linux forky/sid\"\n"))).is_err());
    }

    /// Guix System's /etc/os-release, as gnu/system.scm writes it (a link
    /// into the store): ID=guix, no `VERSION_ID` (docs/design/guix.md; GX1
    /// checks it on the guest).
    const GUIX: &str = "NAME=\"Guix System\"\nID=guix\nPRETTY_NAME=\"Guix System\"\nLOGO=guix-icon\nHOME_URL=\"https://guix.gnu.org\"\nDOCUMENTATION_URL=\"https://guix.gnu.org/en/manual\"\nSUPPORT_URL=\"https://guix.gnu.org/en/help\"\nBUG_REPORT_URL=\"https://lists.gnu.org/mailman/listinfo/bug-guix\"\n";

    #[test]
    fn guix() {
        let o = parse(GUIX);
        assert_eq!(o.get("ID"), Some("guix"));
        assert_eq!(o.get("VERSION_ID"), None);
        assert_eq!(o.get("PRETTY_NAME"), Some("Guix System"));
        assert!(check_supported(Some(&o)).is_ok());
        assert!(check_supported(Some(&parse("ID=guix\n"))).is_ok());
        assert!(supported_names().contains("NixOS 26.05, Guix System, "));
        // A VERSION_ID (a release that starts writing one) is checked first.
        let e = check_supported(Some(&parse(&format!("{GUIX}VERSION_ID=1.5.0\n")))).unwrap_err();
        assert!(e.contains("(ID=guix, VERSION_ID=1.5.0)"), "{e}");
        // Guix the package manager on another system is that system.
        assert!(check_supported(Some(&parse("ID=guixish\n"))).is_err());
    }

    /// Slackware 15.0's /etc/os-release (aaa_base-15.0): `VERSION_ID` bare,
    /// no `ID_LIKE`, `VERSION_CODENAME=stable`.
    const SLACKWARE: &str = "NAME=Slackware\nVERSION=\"15.0\"\nID=slackware\nVERSION_ID=15.0\nPRETTY_NAME=\"Slackware 15.0 x86_64\"\nANSI_COLOR=\"0;34\"\nCPE_NAME=\"cpe:/o:slackware:slackware_linux:15.0\"\nHOME_URL=\"http://slackware.com/\"\nSUPPORT_URL=\"http://www.linuxquestions.org/questions/slackware-14/\"\nBUG_REPORT_URL=\"http://www.linuxquestions.org/questions/slackware-14/\"\nVERSION_CODENAME=stable\n";

    /// Slackware64-current's /etc/os-release since `aaa_base` 16.0, as the
    /// Thinkpad's pinned current-20261121 has it.
    const SLACKWARE_CURRENT: &str = "NAME=Slackware\nVERSION=\"16.0\"\nID=slackware\nVERSION_ID=16.0\nPRETTY_NAME=\"Slackware 15.0 x86_64 (post 15.0 -current)\"\nANSI_COLOR=\"0;34\"\nCPE_NAME=\"cpe:/o:slackware:slackware_linux:15.0\"\nHOME_URL=\"http://slackware.com/\"\nSUPPORT_URL=\"http://www.linuxquestions.org/questions/slackware-14/\"\nBUG_REPORT_URL=\"http://www.linuxquestions.org/questions/slackware-14/\"\nVERSION_CODENAME=current\n";

    #[test]
    fn slackware() {
        let o = parse(SLACKWARE);
        assert_eq!(o.get("ID"), Some("slackware"));
        assert_eq!(o.get("VERSION_ID"), Some("15.0"));
        assert_eq!(o.get("VERSION_CODENAME"), Some("stable"));
        assert_eq!(o.get("PRETTY_NAME"), Some("Slackware 15.0 x86_64"));
        assert_eq!(o.get("ID_LIKE"), None);
        assert!(check_supported(Some(&o)).is_ok());
        assert!(supported_names().contains("Garuda Linux, Slackware 15, Chimera Linux"));
        // 14.2 isn't 15.
        let e = check_supported(Some(&parse(&SLACKWARE.replace("15.0", "14.2")))).unwrap_err();
        assert!(
            e.contains("(ID=slackware, VERSION_ID=14.2)") && e.contains("Slackware 15,"),
            "{e}"
        );
        // -current before aaa_base 16.0: VERSION="15.0+", the codename.
        let current = SLACKWARE
            .replace("VERSION=\"15.0\"", "VERSION=\"15.0+\"")
            .replace("VERSION_CODENAME=stable", "VERSION_CODENAME=current");
        assert!(check_supported(Some(&parse(&current))).is_ok());
        assert!(
            check_supported(Some(&parse(
                &SLACKWARE.replace("VERSION_CODENAME=stable", "VERSION_CODENAME=current")
            )))
            .is_ok()
        );
        // VERSION's "+" under any other codename is no tree bko-holt knows.
        let e = check_supported(Some(&parse(
            &SLACKWARE.replace("VERSION=\"15.0\"", "VERSION=\"15.0+\""),
        )))
        .unwrap_err();
        assert!(
            e.contains("Slackware development tree")
                && e.contains("VERSION=15.0+")
                && e.contains("VERSION_CODENAME=stable"),
            "{e}"
        );
        assert!(
            check_supported(Some(&parse(
                &SLACKWARE
                    .replace("VERSION=\"15.0\"", "VERSION=\"15.0+\"")
                    .replace("VERSION_CODENAME=stable\n", "")
            )))
            .is_err()
        );
        // -current since aaa_base 16.0 (the Thinkpad's, 2026-11-22).
        let o = parse(SLACKWARE_CURRENT);
        assert_eq!(
            (o.get("VERSION_ID"), o.get("VERSION_CODENAME")),
            (Some("16.0"), Some("current"))
        );
        assert!(check_supported(Some(&o)).is_ok());
        // The 16.0 release, or a -current at a VERSION_ID it hasn't had, isn't.
        let e = check_supported(Some(&parse(
            &SLACKWARE_CURRENT.replace("VERSION_CODENAME=current", "VERSION_CODENAME=stable"),
        )))
        .unwrap_err();
        assert!(
            e.contains("(ID=slackware, VERSION_ID=16.0)") && e.contains("Slackware 15,"),
            "{e}"
        );
        assert!(
            check_supported(Some(&parse(
                &SLACKWARE_CURRENT.replace("VERSION_CODENAME=current\n", "")
            )))
            .is_err()
        );
        let e =
            check_supported(Some(&parse(&SLACKWARE_CURRENT.replace("16.0", "17.0")))).unwrap_err();
        assert!(
            e.contains(
                "Slackware -current (ID=slackware, VERSION_ID=17.0, VERSION_CODENAME=current)"
            ) && e.contains("only at VERSION_ID 15.0 or 16.0"),
            "{e}"
        );
        assert!(
            check_supported(Some(&parse(
                &SLACKWARE
                    .replace("15.0", "14.2")
                    .replace("VERSION_CODENAME=stable", "VERSION_CODENAME=current")
            )))
            .is_err()
        );
        assert!(
            slackware_current("slackware", "16.0", Some("current"))
                && slackware_current("slackware", "15.0", Some("current"))
        );
        // -current on x86_64 only (Slackware AArch64 is -current too).
        assert!(check_slackware_current_machine(Some(&o), "x86_64").is_ok());
        let e = check_slackware_current_machine(Some(&o), "aarch64").unwrap_err();
        assert!(
            e.contains("Slackware -current on aarch64")
                && e.contains("x86_64 (Slackware64-current) only"),
            "{e}"
        );
        assert!(
            check_slackware_current_machine(Some(&parse(SLACKWARE)), "aarch64").is_ok()
                && check_slackware_current_machine(None, "aarch64").is_ok()
        );
        assert!(
            !slackware_current("slackware", "16.0", Some("stable"))
                && !slackware_current("slackware", "16.0", None)
                && !slackware_current("slackware", "17.0", Some("current"))
                && !slackware_current("salix", "16.0", Some("current"))
        );
        // A derivative's -current is still not Slackware's.
        assert!(
            check_supported(Some(&parse(
                "ID=salix\nID_LIKE=slackware\nVERSION_ID=16.0\nVERSION_CODENAME=current\n"
            )))
            .is_err()
        );
        // A 15.1 passes by the major (the row's comment).
        assert!(supported("slackware", "15.1"));
        // Not by its likeness: a derivative claiming 15.0 isn't Slackware.
        assert!(
            check_supported(Some(&parse(
                "ID=salix\nID_LIKE=slackware\nVERSION_ID=15.0\n"
            )))
            .is_err()
        );
    }

    /// SteamOS 3.9.5's os-release as Valve's image writes it (S1 checks the
    /// installed one: docs/design/steamos.md).
    pub(crate) const STEAMOS: &str = "NAME=\"SteamOS\"\nPRETTY_NAME=\"SteamOS\"\nVERSION_CODENAME=holo\nID=steamos\nID_LIKE=arch\nANSI_COLOR=\"1;35\"\nHOME_URL=\"https://www.steampowered.com/\"\nDOCUMENTATION_URL=\"https://support.steampowered.com/\"\nSUPPORT_URL=\"https://support.steampowered.com/\"\nBUG_REPORT_URL=\"https://support.steampowered.com/\"\nLOGO=steamos\nVERSION_ID=3.9.5\nVARIANT_ID=steamdeck\nBUILD_ID=20261023.100\n";

    #[test]
    fn steamos() {
        let o = parse(STEAMOS);
        assert_eq!(o.get("ID"), Some("steamos"));
        assert!(check_supported(Some(&o)).is_ok());
        // 3.8 (the install images) and 3.10 (preview) by the major.
        assert!(supported("steamos", "3.8.14"));
        assert!(supported("steamos", "3.10.1"));
        assert!(supported_names().contains("KDE Linux, SteamOS 3, FreeBSD 14"));
        // Not SteamOS 2 (Debian) or a 4 until a guest shows it.
        for v in ["2.195", "4.0"] {
            let e = check_supported(Some(&parse(&STEAMOS.replace("3.9.5", v)))).unwrap_err();
            assert!(
                e.contains(&format!("(ID=steamos, VERSION_ID={v})")) && e.contains("SteamOS 3,"),
                "{e}"
            );
        }
        // Not by its likeness: ID_LIKE=arch alone isn't Arch.
        assert!(
            check_supported(Some(&parse("ID=holoish\nID_LIKE=arch\nVERSION_ID=3.9\n"))).is_err()
        );
    }

    /// Vanilla OS 3's os-release (base-files v1.1.0: docs/design/vanillaos.md;
    /// V1 checks the installed one).
    pub(crate) const VANILLA: &str = "PRETTY_NAME=\"Vanilla OS 3.0\"\nNAME=\"Vanilla OS\"\nVERSION_ID=\"3.0\"\nVERSION=\"3.0 Reunion\"\nVERSION_CODENAME=reunion\nID=vanilla\nID_LIKE=debian\nHOME_URL=\"https://vanillaos.org/\"\n";

    #[test]
    fn vanilla() {
        let o = parse(VANILLA);
        assert_eq!(o.get("ID"), Some("vanilla"));
        assert_eq!(o.get("VERSION_ID"), Some("3.0"));
        assert!(check_supported(Some(&o)).is_ok());
        // 3.0 is taken by its major, as Alpine's 3.22 is; 3.1 too.
        assert!(supported("vanilla", "3.1"));
        assert!(supported_names().contains("Pop!_OS 24.04, Vanilla OS 3, "));
        // Not Orchid (2.0) until a guest shows it, nor a 4.
        for v in ["2.0", "4.0"] {
            let e = check_supported(Some(&parse(&VANILLA.replace("3.0", v)))).unwrap_err();
            assert!(
                e.contains(&format!("(ID=vanilla, VERSION_ID={v})")) && e.contains("Vanilla OS 3,"),
                "{e}"
            );
        }
        // Not by its likeness: ID_LIKE=debian alone isn't Debian 13.
        assert!(
            check_supported(Some(&parse(
                "ID=vanillish\nID_LIKE=debian\nVERSION_ID=\"3.0\"\n"
            )))
            .is_err()
        );
    }

    /// The microos guest's /usr/lib/os-release (openSUSE's
    /// kvm-and-xen image of Snapshot20261111: docs/design/aeon.md, "What M1
    /// found").
    pub(crate) const MICROOS: &str = "NAME=\"openSUSE MicroOS\"\n# VERSION=\"20261111\"\nID=\"opensuse-microos\"\nID_LIKE=\"suse opensuse opensuse-tumbleweed microos sl-micro\"\nVERSION_ID=\"20261111\"\nPRETTY_NAME=\"openSUSE MicroOS\"\nSYSEXT_LEVEL=\"glibc-2.44\"\nANSI_COLOR=\"0;32\"\nCPE_NAME=\"cpe:/o:opensuse:microos:20261111\"\nBUG_REPORT_URL=\"https://bugzilla.opensuse.org\"\nSUPPORT_URL=\"https://bugs.opensuse.org\"\nHOME_URL=\"https://www.opensuse.org/\"\nDOCUMENTATION_URL=\"https://en.opensuse.org/Portal:MicroOS\"\nLOGO=\"distributor-logo-MicroOS\"\n";

    #[test]
    fn microos() {
        let o = parse(MICROOS);
        assert_eq!(
            (o.get("ID"), o.get("VERSION_ID")),
            (Some("opensuse-microos"), Some("20261111"))
        );
        assert!(check_supported(Some(&o)).is_ok());
        // Rolling: any snapshot.
        assert!(supported("opensuse-microos", "20270101"));
        // Tumbleweed stays Tumbleweed: its own row, not MicroOS's by
        // ID_LIKE; and MicroOS isn't Tumbleweed by its ID_LIKE either.
        assert!(
            check_supported(Some(&parse(
                "ID=\"opensuse-tumbleweed\"\nID_LIKE=\"opensuse suse\"\nVERSION_ID=\"20261101\"\n"
            )))
            .is_ok()
        );
        assert!(supported_names().contains("openSUSE Tumbleweed, openSUSE MicroOS, "));
        // Aeon (and SL Micro, Leap Micro) aren't claimed by MicroOS's row.
        for id in ["opensuse-aeon", "sl-micro", "opensuse-leap-micro"] {
            let e = check_supported(Some(&parse(
                &MICROOS.replace("\"opensuse-microos\"", &format!("\"{id}\"")),
            )))
            .unwrap_err();
            assert!(
                e.contains(&format!("(ID={id}, VERSION_ID=20261111)")),
                "{e}"
            );
        }
    }

    /// The aeon guest's /usr/lib/os-release (Aeon-Installer Snapshot20261111,
    /// self-deployed by tik: docs/design/aeon.md, "What A1 found so far").
    pub(crate) const AEON: &str = "NAME=\"Aeon\"\n# VERSION=\"20261111\"\nID=\"aeon\"\nID_LIKE=\"suse opensuse opensuse-tumbleweed opensuse-microos opensuse-aeon microos\"\nVERSION_ID=\"20261111\"\nPRETTY_NAME=\"Aeon\"\nANSI_COLOR=\"0;32\"\nCPE_NAME=\"cpe:/o:opensuse:aeon:20261111\"\nBUG_REPORT_URL=\"https://aeondesktop.org/reportbug\"\nSUPPORT_URL=\"https://aeondesktop.org/bugs\"\nHOME_URL=\"https://aeondesktop.org/\"\nDOCUMENTATION_URL=\"https://aeondesktop.org/docs\"\n";

    #[test]
    fn aeon() {
        let o = parse(AEON);
        assert_eq!(
            (o.get("ID"), o.get("VERSION_ID"), o.get("VERSION")),
            (Some("aeon"), Some("20261111"), None)
        );
        assert!(check_supported(Some(&o)).is_ok());
        // Rolling: any snapshot, later or earlier.
        for v in ["20270301", "20260101"] {
            assert!(
                check_supported(Some(&parse(&AEON.replace("20261111", v)))).is_ok(),
                "{v}"
            );
        }
        assert!(supported_names().contains("openSUSE MicroOS, Aeon, "));
        // By its own ID only: a system that names Aeon (or MicroOS) in its
        // ID_LIKE isn't taken by either row, nor is Aeon by MicroOS's.
        assert!(supported("aeon", "20261111") && !supported("opensuse-aeon", "20261111"));
        for id in ["opensuse-aeon", "aeon-ish", "kalpa"] {
            let e = check_supported(Some(&parse(
                &AEON.replace("ID=\"aeon\"", &format!("ID=\"{id}\"")),
            )))
            .unwrap_err();
            assert!(
                e.contains(&format!("(ID={id}, VERSION_ID=20261111)")),
                "{e}"
            );
        }
        assert!(
            check_supported(Some(&parse(
                &MICROOS.replace("ID=\"opensuse-microos\"", "ID=\"aeon\"")
            )))
            .is_ok()
        );
        assert!(
            check_supported(Some(&parse(
                "ID=\"opensuse-microos\"\nID_LIKE=\"aeon\"\nVERSION_ID=\"20261111\"\n"
            )))
            .is_ok()
        );
    }

    /// Leap 16.1's /etc/os-release (the Leap-16.1 offline ISO, Build60.2:
    /// docs/design/leap16.md).
    const LEAP16: &str = "NAME=\"openSUSE Leap\"\nVERSION=\"16.1\"\nID=\"opensuse-leap\"\nID_LIKE=\"suse opensuse\"\nVERSION_ID=\"16.1\"\nPRETTY_NAME=\"openSUSE Leap 16.1\"\n";

    #[test]
    fn leap16() {
        let o = parse(LEAP16);
        assert_eq!(
            (o.get("ID"), o.get("VERSION_ID")),
            (Some("opensuse-leap"), Some("16.1"))
        );
        assert!(check_supported(Some(&o)).is_ok());
        // Every 16.x point release, 16.0 too.
        assert!(check_supported(Some(&parse(&LEAP16.replace("16.1", "16.0")))).is_ok());
        assert!(supported("opensuse-leap", "16.2"));
        assert!(
            supported_names().contains("openSUSE MicroOS, Aeon, openSUSE Leap 16, Rocky Linux 10")
        );
        // Not Leap 15.x (YaST, its older tools), nor a 17 until a guest shows it.
        for v in ["15.6", "17.0"] {
            let e = check_supported(Some(&parse(&LEAP16.replace("16.1", v)))).unwrap_err();
            assert!(
                e.contains(&format!(
                    "this is openSUSE Leap {v} (ID=opensuse-leap, VERSION_ID={v})"
                )) && e.contains("openSUSE Leap 16,"),
                "{e}"
            );
        }
        // Tumbleweed stays Tumbleweed (its own row, any snapshot), and
        // neither is the other by ID_LIKE.
        let tw = parse(
            "NAME=\"openSUSE Tumbleweed\"\nID=\"opensuse-tumbleweed\"\nID_LIKE=\"opensuse suse\"\nVERSION_ID=\"20261101\"\nPRETTY_NAME=\"openSUSE Tumbleweed\"\n",
        );
        assert_eq!(tw.get("ID"), Some("opensuse-tumbleweed"));
        assert!(check_supported(Some(&tw)).is_ok());
        assert!(!supported("opensuse-leap", "20261101"));
        assert!(
            check_supported(Some(&parse(
                "ID=opensuse-leap-micro\nID_LIKE=\"suse opensuse\"\nVERSION_ID=\"16.1\"\n"
            )))
            .is_err()
        );
    }

    /// Solus 4.9's /usr/lib/os-release (the Solus-Xfce-Release-2026-04-18
    /// ISO's root, docs/design/solus.md).
    const SOLUS: &str = "NAME=\"Solus\"\nVERSION=\"4.9\"\nID=\"solus\"\nVERSION_CODENAME=serenity\nVERSION_ID=\"4.9\"\nPRETTY_NAME=\"Solus 4.9 Serenity\"\nANSI_COLOR=\"1;34\"\nHOME_URL=\"https://getsol.us\"\nSUPPORT_URL=\"https://help.getsol.us/docs/user/contributing/getting-involved\"\nBUG_REPORT_URL=\"https://issues.getsol.us/\"\nLOGO=\"distributor-logo-solus\"\n";

    #[test]
    fn solus() {
        let o = parse(SOLUS);
        assert_eq!(
            (o.get("ID"), o.get("VERSION_ID"), o.get("ID_LIKE")),
            (Some("solus"), Some("4.9"), None)
        );
        assert_eq!(o.get("PRETTY_NAME"), Some("Solus 4.9 Serenity"));
        assert!(check_supported(Some(&o)).is_ok());
        // Every 4.x ISO; not a 5 until a guest shows it, nor the 3.x before.
        assert!(supported("solus", "4.10"));
        assert!(supported_names().contains("AerynOS, Solus 4, Debian 12"));
        for v in ["5.0", "3.9999"] {
            let e = check_supported(Some(&parse(&SOLUS.replace("\"4.9\"", &format!("\"{v}\"")))))
                .unwrap_err();
            assert!(
                e.contains(&format!("(ID=solus, VERSION_ID={v})")) && e.contains("Solus 4,"),
                "{e}"
            );
        }
        // Not by its likeness: a remix with its own ID claiming 4.9 isn't Solus.
        let remix = SOLUS
            .replace("ID=\"solus\"", "ID=\"solus-remix\"")
            .replace("NAME=\"Solus\"", "NAME=\"Remix\"");
        assert!(check_supported(Some(&parse(&remix))).is_err());
        assert!(check_supported(Some(&parse("ID=solusos\nVERSION_ID=\"4.9\"\n"))).is_err());
    }

    #[test]
    fn reads_usr_lib_fallback() {
        let t = Tree::new("osrel");
        t.file("/usr/lib/os-release", TRIXIE);
        let s = Sys::new(t.root(), Box::new(FakeRunner::default()), false);
        assert_eq!(read(&s).unwrap().get("ID"), Some("debian"));
        t.file("/etc/os-release", "ID=other\n");
        assert_eq!(read(&s).unwrap().get("ID"), Some("other"));
        let empty = Tree::new("osrel-none");
        let s = Sys::new(empty.root(), Box::new(FakeRunner::default()), false);
        assert!(read(&s).is_none());
    }

    /// The oi2610 guest's /etc/release (`OpenIndiana` 2026.10, which has no
    /// os-release anywhere).
    const OI_RELEASE: &str = "             OpenIndiana Hipster 2026.10 (powered by illumos)\n        OpenIndiana Project, part of The Illumos Foundation (C) 2010-2026\n                        Use is subject to license terms.\n                           Assembled 30 October 2026\n";

    #[test]
    fn openindiana_from_etc_release() {
        let t = Tree::new("osrel-oi");
        t.file("/etc/release", OI_RELEASE);
        let s = Sys::new(t.root(), Box::new(FakeRunner::default()), false);
        let o = read(&s).unwrap();
        assert_eq!(o.get("ID"), Some("openindiana"));
        assert_eq!(o.get("NAME"), Some("OpenIndiana Hipster"));
        assert_eq!(o.get("VERSION_ID"), Some("2026.10"));
        assert_eq!(o.get("PRETTY_NAME"), Some("OpenIndiana Hipster 2026.10"));
        assert!(check_supported(Some(&o)).is_ok());
        assert!(supported("openindiana", "2027.04"));
        // A leading blank line, and no snapshot after Hipster.
        let o = openindiana_release("\n\n  OpenIndiana Hipster\n").unwrap();
        assert_eq!(
            (o.get("VERSION_ID"), o.get("PRETTY_NAME")),
            (None, Some("OpenIndiana Hipster"))
        );
        assert!(check_supported(Some(&o)).is_ok());
        // os-release wins where there is one (OmniOS has both).
        t.file("/etc/os-release", "ID=omnios\nVERSION_ID=r151058y\n");
        assert_eq!(read(&s).unwrap().get("ID"), Some("omnios"));
    }

    #[test]
    fn other_etc_release_isnt_openindiana() {
        for rel in [
            "                             Oracle Solaris 11.4 X86\n  Copyright (c) 1983, 2024, Oracle and/or its affiliates.\n",
            "             OpenIndiana Development oi_151a X86\n",
            "OpenIndianaX Hipster 2026.10\n",
            "",
        ] {
            assert!(openindiana_release(rel).is_none(), "{rel}");
            let t = Tree::new("osrel-sunos");
            t.file("/etc/release", rel);
            let s = Sys::new(t.root(), Box::new(FakeRunner::default()), false);
            assert!(read(&s).is_none(), "{rel}");
        }
        // Without an os-release, other::os_release makes one from uname
        // (ID=sunos), which is refused.
        let e = check_supported(Some(&parse(
            "ID=sunos\nVERSION_ID=\"5.11\"\nPRETTY_NAME=\"SunOS 5.11\"\n",
        )))
        .unwrap_err();
        assert!(
            e.contains("this is SunOS 5.11 (ID=sunos, VERSION_ID=5.11)")
                && e.contains("OmniOS, OpenIndiana,"),
            "{e}"
        );
        assert!(check_supported(Some(&parse("ID=solaris\nVERSION_ID=\"11.4\"\n"))).is_err());
    }

    /// The core24 base snap's os-release (docs/design/ubuntu-core.md, "What
    /// U1 found": core24 423 and 2307 alike).
    pub(crate) const UBUNTU_CORE: &str = "NAME=\"Ubuntu Core\"\nVERSION=\"24\"\nID=ubuntu-core\nPRETTY_NAME=\"Ubuntu Core 24\"\nVERSION_ID=\"24\"\nHOME_URL=\"https://snapcraft.io/\"\nBUG_REPORT_URL=\"https://bugs.launchpad.net/snappy/\"\n";

    #[test]
    fn ubuntu_core() {
        let o = parse(UBUNTU_CORE);
        assert_eq!(
            (o.get("ID"), o.get("VERSION_ID")),
            (Some("ubuntu-core"), Some("24"))
        );
        assert!(check_supported(Some(&o)).is_ok());
        assert!(supported_names().ends_with(", Sculpt OS or Ubuntu Core 24"));
        // UC20 and UC22 keep their FDE keys otherwise; UC26 isn't tested.
        for v in ["20", "22", "26"] {
            let e = check_supported(Some(&parse(
                &UBUNTU_CORE.replace("\"24\"", &format!("\"{v}\"")),
            )))
            .unwrap_err();
            assert!(
                e.contains(&format!("(ID=ubuntu-core, VERSION_ID={v})"))
                    && e.contains("Ubuntu Core 24"),
                "{e}"
            );
        }
        // Ubuntu's own row is another.
        assert!(check_supported(Some(&parse("ID=ubuntu\nVERSION_ID=\"24\"\n"))).is_ok());
    }

    /// Qubes OS 4.3.2's dom0 (docs/design/qubes.md, "What Q0 found").
    #[test]
    fn qubes() {
        let q = "NAME=Qubes\nVERSION=\"4.3.2 (R4.3)\"\nID=qubes\nVERSION_ID=4.3\nPRETTY_NAME=\"Qubes OS 4.3.2 (R4.3)\"\n";
        let o = parse(q);
        assert_eq!(
            (o.get("ID"), o.get("VERSION_ID")),
            (Some("qubes"), Some("4.3"))
        );
        assert!(check_supported(Some(&o)).is_ok());
        let e = check_supported(Some(&parse(&q.replace("VERSION_ID=4.3", "VERSION_ID=4.2"))))
            .unwrap_err();
        assert!(e.contains("Qubes OS 4.3 and later only"), "{e}");
        assert!(
            check_supported(Some(&parse(&q.replace("VERSION_ID=4.3", "VERSION_ID=3.2")))).is_err()
        );
    }

    #[test]
    fn json_fields() {
        let t = Tree::new("osrel-json");
        t.debian13()
            .file("/proc/sys/kernel/osrelease", "6.12.48+deb13-amd64\n")
            .file(
                "/proc/sys/kernel/version",
                "#1 SMP PREEMPT_DYNAMIC Debian 6.12.48-1 (2025-09-20)\n",
            );
        let s = Sys::new(t.root(), Box::new(FakeRunner::default()), false);
        let o = read(&s);
        let j = to_json(&s, o.as_ref());
        assert_eq!(j.get("id").and_then(Value::as_str), Some("debian"));
        assert_eq!(
            j.get("version_codename").and_then(Value::as_str),
            Some("trixie")
        );
        assert_eq!(
            j.get("debian_version").and_then(Value::as_str),
            Some("13.1")
        );
        assert_eq!(
            j.get("kernel_release").and_then(Value::as_str),
            Some("6.12.48+deb13-amd64")
        );
        assert!(
            j.get("kernel_version")
                .and_then(Value::as_str)
                .unwrap()
                .contains("Debian")
        );
        assert_eq!(
            j.get("architecture").and_then(Value::as_str),
            Some(std::env::consts::ARCH)
        );
    }
}
