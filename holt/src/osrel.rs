//! /etc/os-release, and the check that this is a system bko-holt supports.

use crate::json::{Obj, Value};
use crate::sys::Sys;

#[derive(Debug, Default, Clone, PartialEq)]
pub struct OsRelease(pub Vec<(String, String)>);

impl OsRelease {
    pub fn get(&self, key: &str) -> Option<&str> {
        self.0.iter().rev().find(|(k, _)| k == key).map(|(_, v)| v.as_str())
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
        let Some((k, v)) = line.split_once('=') else { continue };
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

/// /etc/os-release, else /usr/lib/os-release.
pub fn read(sys: &Sys) -> Option<OsRelease> {
    sys.read("/etc/os-release").or_else(|| sys.read("/usr/lib/os-release")).map(|t| parse(&t))
}

/// The systems bko-holt supports, as os-release's ID and the major part of
/// VERSION_ID ("*": any, for a rolling release that numbers its snapshots),
/// with how people name them.  Each has a whole-machine test in HoltTest1All
/// (testvm/holttest1d/README.md says which guests pass).  The Ubuntu-based ones use Debian's
/// boot tools (update-initramfs, update-grub).  KDE neon (Ubuntu 24.04 with
/// KDE's rolling Plasma) uses Ubuntu's; its test is `neon24` (UEFI, GRUB
/// through shim in EFI/neon) in HoltTest1U, docs/design/kde-neon.md.  Arch
/// and Artix have mkinitcpio and grub-mkconfig, Alpine mkinitfs and
/// grub-mkconfig, Fedora dracut and grub2-mkconfig; AerynOS's initramfs
/// comes prebuilt with its kernel, and systemd-boot boots it.  Chimera
/// Linux (musl, dinit, apk, FreeBSD's userland) has Debian's
/// initramfs-tools (update-initramfs) but no debconf,
/// and grub-mkconfig, or systemd-boot with gen-systemd-boot; its tests are
/// `chimera`, `chimera-uefi` and `chimera-zfs` in HoltTest1Obscure, and
/// BackShot1 on the Thinkpad (root on ZFS, the ESP as /boot).  Bazzite, and
/// Fedora's atomic desktops under the Fedora row, are ostree systems
/// (crate::ostree): the initramfs comes with the image and bootupd's GRUB
/// config is static, so load regenerates nothing there (load/ostree.rs).
/// Their whole-machine test is `kinoite44`, Bazzite's Kinoite base, in
/// HoltTest1F; BackShot1 runs Bazzite itself on real hardware.  Gentoo
/// Linux (OpenRC or systemd, Portage) boots the way its owner put it
/// together: installkernel's install.conf names the initramfs generator,
/// and only dracut's (or none) is supported, with GRUB and grub-mkconfig;
/// its tests are `gentoo` (OpenRC, the Thinkpad's setup in small) and
/// `gentoo-systemd` (LVM on LUKS) in HoltTest1G, and BackShot1 on the
/// Thinkpad itself (docs/design/gentoo.md).  PikaOS 4 (Debian sid with its
/// own kernel) boots by rEFInd, whose files are kept as restored, with
/// booster's initramfs (docs/design/pikaos.md); its tests are `pikaos4` and
/// `pikaos4-luks` in HoltTest1Obscure.  AnduinOS 2 (Ubuntu 26.04
/// underneath, ID_LIKE=debian) has dracut for its initramfs and its own
/// GRUB, signed on the machine with a MOK behind shim, which load keeps as
/// the ESP brings it back (docs/design/anduinos.md); its test is
/// `anduinos2` in HoltTest1U.  Kali Linux (Debian testing with its own
/// archive) boots as Debian 13 does on UEFI without shim: initramfs-tools,
/// GRUB in EFI/kali and update-grub (docs/design/kali.md); its test is
/// `kali` in HoltTest1Obscure.  GNU Guix System is a store system, as
/// NixOS is (crate::store): each generation's kernel and initrd are store
/// items that GRUB reads from /, so nothing is rebuilt, and on BIOS the
/// source's own boot.img and core.img are written to the new disk by the
/// rescue's grub-bios-setup (docs/design/guix.md); its tests
/// are `guix15` (UEFI) and `guix15-bios` in HoltTest1Obscure.  Vanilla OS 3
/// (Debian sid) runs ABRoot v2 (crate::abroot): two roots on LVM thin LVs
/// and an init LV with their kernels, all restored as they were, with no
/// initramfs, grub-install or update-grub (docs/design/vanillaos.md); its
/// test is `vanilla3` in HoltTest1Obscure.
pub const SUPPORTED: &[(&str, &str, &str)] = &[
    ("debian", "13", "Debian 13"),
    ("linuxmint", "22", "Linux Mint 22"),
    ("ubuntu", "24", "Ubuntu 24.04"),
    ("ubuntu", "26", "Ubuntu 26.04"),
    ("zorin", "18", "Zorin OS 18"),
    ("neon", "24", "KDE neon"),
    // Rolling: Arch's os-release has no VERSION_ID.
    ("arch", "", "Arch Linux"),
    ("fedora", "44", "Fedora 44"),
    ("alpine", "3", "Alpine Linux 3"),
    // Rolling, like Arch.
    ("artix", "", "Artix Linux"),
    // Rolling: VERSION_ID is the snapshot's year and month (2026.11).
    ("aerynos", "*", "AerynOS"),
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
    ("rocky", "10", "Rocky Linux 10"),
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
    // Rolling: Chimera's os-release has no VERSION_ID at all (only its image
    // names carry the snapshot's date).  "*" rather than "", as for
    // AerynOS: a snapshot that starts writing one is still Chimera.
    ("chimera", "*", "Chimera Linux"),
    // Rolling: VERSION_ID is baselayout's version (2.18), not a release.
    // Funtoo, Calculate and the other ID_LIKE=gentoo systems aren't it.
    ("gentoo", "*", "Gentoo Linux"),
    // ostree-based, on Fedora's atomic desktops (Kinoite): ID=bazzite,
    // VERSION_ID the Fedora release.  Silverblue and Kinoite themselves say
    // fedora 44, so the Fedora row takes them; ostree is told by its own
    // markers (crate::ostree::detect).
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
    // Read off their disks (dump --disk): no VERSION_ID.
    ("redox", "", "Redox"),
    ("sculpt", "", "Sculpt OS"),
];

/// Whether ID and VERSION_ID name a supported system: the same major version
/// ("22.3" is Mint 22).  Derivatives don't count by their ID_LIKE.
pub fn supported(id: &str, version_id: &str) -> bool {
    let major = version_id.split('.').next().unwrap_or("");
    SUPPORTED.iter().any(|(i, v, _)| *i == id && (*v == major || *v == "*"))
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
        return Err("no /etc/os-release or /usr/lib/os-release: can't tell which system this is".into());
    };
    let id = os.get("ID").unwrap_or("");
    let ver = os.get("VERSION_ID").unwrap_or("");
    if supported(id, ver) {
        return Ok(());
    }
    let name = os.get("PRETTY_NAME").map(str::to_string).unwrap_or_else(|| format!("{id} {ver}"));
    Err(format!("this is {} (ID={id}, VERSION_ID={ver}), and bko-holt dump supports only {} for now", name.trim(), supported_names()))
}

pub fn to_json(sys: &Sys, os: Option<&OsRelease>) -> Value {
    let g = |k: &str| os.and_then(|o| o.get(k)).map(str::to_string);
    Obj::new()
        .set("id", g("ID"))
        .set("version_id", g("VERSION_ID"))
        .set("version_codename", g("VERSION_CODENAME"))
        .set("pretty_name", g("PRETTY_NAME"))
        .set("debian_version", sys.read_trim("/etc/debian_version"))
        .set("kernel_release", sys.read_trim("/proc/sys/kernel/osrelease"))
        .set("kernel_version", sys.read_trim("/proc/sys/kernel/version"))
        .set("architecture", std::env::consts::ARCH)
        .build()
}

#[cfg(test)]
mod tests {
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
        let o = parse("# comment\n\nA='single \"q\"'\nB=\"a\\\"b\\\\c\\$d\\`e\\x\"\n  C = bare \nnot a line\n=nokey\nBAD-KEY=1\nA=override\n");
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
        let buster = parse("ID=debian\nVERSION_ID=\"10\"\nPRETTY_NAME=\"Debian GNU/Linux 10 (buster)\"\n");
        let e = check_supported(Some(&buster)).unwrap_err();
        assert!(e.contains("Debian GNU/Linux 10 (buster)"), "{e}");
        assert!(check_supported(Some(&parse("ID=debian\nVERSION_ID=\"12\"\n"))).is_ok());
        let ubuntu = parse("ID=ubuntu\nID_LIKE=debian\nVERSION_ID=\"26.04\"\n");
        assert!(check_supported(Some(&ubuntu)).is_ok());
        let ubuntu22 = parse("ID=ubuntu\nID_LIKE=debian\nVERSION_ID=\"22.04\"\n");
        assert!(check_supported(Some(&ubuntu22)).unwrap_err().contains("ubuntu 22.04"));
        assert!(check_supported(Some(&parse("ID=zorin\nID_LIKE=\"ubuntu debian\"\nVERSION_ID=\"18\"\n"))).is_ok());
        // KDE neon User Edition's /etc/os-release (neon's base-files); not
        // the neon that was on Ubuntu 22.04, nor one on 26.04 yet.
        let neon = "PRETTY_NAME=\"KDE neon User Edition\"\nNAME=\"KDE neon\"\nVERSION_ID=\"24.04\"\nVERSION=\"User Edition\"\nVERSION_CODENAME=noble\nID=neon\nID_LIKE=\"ubuntu debian\"\nUBUNTU_CODENAME=noble\nLOGO=start-here-kde-neon\n";
        assert!(check_supported(Some(&parse(neon))).is_ok());
        assert!(check_supported(Some(&parse(&neon.replace("24.04", "22.04")))).is_err());
        assert!(check_supported(Some(&parse(&neon.replace("24.04", "26.04")))).is_err());
        assert!(e.contains("Debian 13, Linux Mint 22, Ubuntu 24.04, Ubuntu 26.04, Zorin OS 18, KDE neon, Arch Linux, Fedora 44, Alpine Linux 3, Artix Linux, AerynOS, Debian 12, Devuan 6, Pop!_OS 24.04, Vanilla OS 3, CachyOS, Void Linux, NixOS 26.05, Guix System, openSUSE Tumbleweed, Rocky Linux 10, Omarchy, EndeavourOS, Manjaro, BigLinux, Garuda Linux, Chimera Linux, Gentoo Linux, Bazzite 44, PikaOS 4, AnduinOS 2, Kali Linux, FreeBSD 14, FreeBSD 15, HardenedBSD 15, GhostBSD 26, NetBSD 11, AlpineBSD 1, OpenBSD 8, DragonFly 6, Haiku, OmniOS, Redox or Sculpt OS"), "{e}");
        for t in ["ID=devuan\nVERSION_ID=6\n", "ID=pop\nVERSION_ID=\"24.04\"\n", "ID=cachyos\nBUILD_ID=rolling\n", "ID=void\n", "ID=nixos\nVERSION_ID=\"26.05\"\n", "ID=\"opensuse-tumbleweed\"\nVERSION_ID=\"20261101\"\n", "ID=rocky\nVERSION_ID=\"10.2\"\n"] {
            assert!(check_supported(Some(&parse(t))).is_ok(), "{t}");
        }
        assert!(check_supported(Some(&parse("ID=alpine\nVERSION_ID=3.24.3\n"))).is_ok());
        assert!(check_supported(Some(&parse("ID=artix\nNAME=\"Artix Linux\"\n"))).is_ok());
        assert!(check_supported(Some(&parse("NAME=\"EndeavourOS\"\nID=\"endeavouros\"\nID_LIKE=\"arch\"\nBUILD_ID=rolling\n"))).is_ok());
        assert!(check_supported(Some(&parse("NAME=\"Manjaro Linux\"\nID=manjaro\nID_LIKE=arch\nBUILD_ID=rolling\n"))).is_ok());
        // Garuda's real os-release (read 2026-11-14 from its ISO).
        assert!(check_supported(Some(&parse("NAME=\"Garuda Linux\"\nPRETTY_NAME=\"Garuda Linux\"\nID=garuda\nID_LIKE=arch\nBUILD_ID=rolling\nANSI_COLOR=\"38;2;23;147;209\"\nHOME_URL=\"https://garudalinux.org/\"\nLOGO=garudalinux\n"))).is_ok());
        // BigLinux's real os-release, as biglinux-name writes it.
        assert!(check_supported(Some(&parse("NAME=\"BigLinux based on Manjaro Linux\"\nPRETTY_NAME=\"BigLinux based on Manjaro Linux\"\nID=\"biglinux\"\nID_LIKE=arch\nBUILD_ID=rolling\n"))).is_ok());
        assert!(check_supported(Some(&parse("ID=\"aerynos\"\nVERSION_ID=\"2026.11\"\nID_LIKE=\"linux\"\n"))).is_ok());
        assert!(check_supported(Some(&parse("ID=\"aerynos\"\nVERSION_ID=\"2027.01\"\n"))).is_ok());
        // Linux Mint 22.x; not 21, and not Mint's Debian edition (LMDE).
        let mint = parse("ID=linuxmint\nID_LIKE=\"ubuntu debian\"\nVERSION_ID=\"22.3\"\n");
        assert!(check_supported(Some(&mint)).is_ok());
        assert!(check_supported(Some(&parse("ID=linuxmint\nVERSION_ID=\"21.3\"\n"))).is_err());
        assert!(check_supported(Some(&parse("ID=lmde\nID_LIKE=debian\nVERSION_ID=\"6\"\n"))).is_err());
        assert!(!supported("debian", "130"));
        assert!(check_supported(Some(&parse("ID=arch\nBUILD_ID=rolling\n"))).is_ok());
        assert!(check_supported(Some(&parse("ID=fedora\nVERSION_ID=44\n"))).is_ok());
        assert!(check_supported(Some(&parse("ID=fedora\nVERSION_ID=43\n"))).is_err());
        // Debian testing/sid has no VERSION_ID.
        let sid = parse("ID=debian\nPRETTY_NAME=\"Debian GNU/Linux forky/sid\"\n");
        assert!(check_supported(Some(&sid)).is_err());
        // A derivative that claims VERSION_ID 13 isn't Debian.
        let devuan = parse("ID=devuan\nID_LIKE=debian\nVERSION_ID=13\n");
        assert!(check_supported(Some(&devuan)).is_err());
        assert!(check_supported(None).unwrap_err().contains("os-release"));
    }

    /// The chimera guest's /etc/os-release (snapshot 20261103): ID quoted,
    /// and no VERSION_ID line.
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
        assert!(check_supported(Some(&parse("ID=\"chimera-ish\"\nID_LIKE=\"chimera\"\n"))).is_err());
    }

    /// The gentoo guest's /etc/os-release (baselayout 2.18), the same as the
    /// Thinkpad's: single quotes, VERSION_ID baselayout's, no ID_LIKE.
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
        for t in ["ID=funtoo\nID_LIKE=gentoo\nVERSION_ID=2.4\n", "ID=calculate\nID_LIKE=gentoo\nVERSION_ID=\"25\"\nPRETTY_NAME=\"Calculate Linux 25\"\n"] {
            let e = check_supported(Some(&parse(t))).unwrap_err();
            assert!(e.contains("Gentoo Linux"), "{e}");
        }
    }

    /// The Thinkpad's /usr/lib/os-release (Bazzite 44.20261012), and
    /// kinoite44's (Fedora Kinoite 44.20261111.0).
    #[test]
    fn bazzite_and_kinoite() {
        let b = parse("NAME=\"Bazzite\"\nVERSION=\"44.20261012.0 (Kinoite)\"\nID=bazzite\nID_LIKE=\"fedora\"\nVERSION_ID=44\nVARIANT_ID=bazzite\nPRETTY_NAME=\"Bazzite 44 (FROM Fedora Kinoite)\"\nOSTREE_VERSION='44.20261012.0'\n");
        assert_eq!(b.get("ID"), Some("bazzite"));
        assert_eq!(b.get("OSTREE_VERSION"), Some("44.20261012.0"));
        assert!(check_supported(Some(&b)).is_ok());
        assert!(supported_names().contains("Bazzite 44"));
        // Not the next Fedora major until a guest or a run shows it.
        assert!(check_supported(Some(&parse("ID=bazzite\nID_LIKE=fedora\nVERSION_ID=45\n"))).is_err());
        // Bluefin and Aurora aren't Bazzite, and ID_LIKE=fedora doesn't let them in.
        assert!(check_supported(Some(&parse("ID=bluefin\nID_LIKE=fedora\nVERSION_ID=44\n"))).is_err());
        let k = parse("NAME=\"Fedora Linux\"\nVERSION=\"44.20261111.0 (Kinoite)\"\nID=fedora\nVERSION_ID=44\nVARIANT_ID=kinoite\nOSTREE_VERSION='44.20261111.0'\n");
        assert!(check_supported(Some(&k)).is_ok());
    }

    /// PikaOS 4's /etc/os-release (its base-files' source).
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
        assert!(e.contains("(ID=pika, VERSION_ID=5)") && e.contains("Bazzite 44, PikaOS 4,"), "{e}");
        assert!(check_supported(Some(&parse(&PIKA.replace("\"4\"", "\"3\"")))).is_err());
        // Another ID_LIKE=debian system on sid isn't PikaOS, nor is a
        // derivative that claims its VERSION_ID.
        assert!(check_supported(Some(&parse("PRETTY_NAME=\"Siduction\"\nID=siduction\nID_LIKE=debian\nVERSION_ID=\"4\"\n"))).is_err());
        assert!(check_supported(Some(&parse("ID=pika-ish\nID_LIKE=\"pika debian\"\nVERSION_ID=\"4\"\n"))).is_err());
    }

    /// AnduinOS 2.0.5's /usr/lib/os-release (the anduinos2 guest's ISO,
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
        assert!(e.contains("this is AnduinOS 1.1.2 (ID=anduinos, VERSION_ID=1.1.2)") && e.contains("AnduinOS 2,"), "{e}");
        assert!(check_supported(Some(&parse(&ANDUINOS.replace("\"2.0.5\"", "\"3.0\"")))).is_err());
        // Not by its likeness: another ID_LIKE=debian system on resolute,
        // claiming AnduinOS's VERSION_ID, isn't AnduinOS.
        let lookalike = ANDUINOS.replace("ID=anduinos", "ID=anduinos-remix").replace("NAME=\"AnduinOS\"", "NAME=\"Remix\"");
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
        assert!(supported_names().contains("AnduinOS 2, Kali Linux, FreeBSD 14"));
        // Rolling (decision 2): every snapshot, the next year's too, and
        // one with no VERSION_ID at all.
        for v in ["2026.4", "2027.1", "2025.4"] {
            assert!(check_supported(Some(&parse(&KALI.replace("2026.3", v)))).is_ok(), "{v}");
        }
        assert!(check_supported(Some(&parse(&KALI.replace("VERSION_ID=\"2026.3\"\n", "")))).is_ok());
        // Not by its likeness: a remix with its own ID, or a derivative that
        // names Kali in ID_LIKE and claims its VERSION_ID, isn't Kali.
        let remix = KALI.replace("ID=kali\n", "ID=kali-remix\n").replace("NAME=\"Kali GNU/Linux\"", "NAME=\"Remix\"");
        let e = check_supported(Some(&parse(&remix))).unwrap_err();
        assert!(e.contains("(ID=kali-remix, VERSION_ID=2026.3)") && e.contains("AnduinOS 2, Kali Linux,"), "{e}");
        assert!(check_supported(Some(&parse("ID=pentestos\nID_LIKE=\"kali debian\"\nVERSION_ID=\"2026.3\"\nVERSION_CODENAME=kali-rolling\n"))).is_err());
        // Debian testing itself (no VERSION_ID), even on Kali's codename.
        assert!(check_supported(Some(&parse("ID=debian\nVERSION_CODENAME=kali-rolling\nPRETTY_NAME=\"Debian GNU/Linux forky/sid\"\n"))).is_err());
    }

    /// Guix System's /etc/os-release, as gnu/system.scm writes it (a link
    /// into the store): ID=guix, no VERSION_ID (docs/design/guix.md; GX1
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
            assert!(e.contains(&format!("(ID=vanilla, VERSION_ID={v})")) && e.contains("Vanilla OS 3,"), "{e}");
        }
        // Not by its likeness: ID_LIKE=debian alone isn't Debian 13.
        assert!(check_supported(Some(&parse("ID=vanillish\nID_LIKE=debian\nVERSION_ID=\"3.0\"\n"))).is_err());
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

    #[test]
    fn json_fields() {
        let t = Tree::new("osrel-json");
        t.debian13().file("/proc/sys/kernel/osrelease", "6.12.48+deb13-amd64\n").file(
            "/proc/sys/kernel/version",
            "#1 SMP PREEMPT_DYNAMIC Debian 6.12.48-1 (2025-09-20)\n",
        );
        let s = Sys::new(t.root(), Box::new(FakeRunner::default()), false);
        let o = read(&s);
        let j = to_json(&s, o.as_ref());
        assert_eq!(j.get("id").and_then(Value::as_str), Some("debian"));
        assert_eq!(j.get("version_codename").and_then(Value::as_str), Some("trixie"));
        assert_eq!(j.get("debian_version").and_then(Value::as_str), Some("13.1"));
        assert_eq!(j.get("kernel_release").and_then(Value::as_str), Some("6.12.48+deb13-amd64"));
        assert!(j.get("kernel_version").and_then(Value::as_str).unwrap().contains("Debian"));
        assert_eq!(j.get("architecture").and_then(Value::as_str), Some(std::env::consts::ARCH));
    }
}
