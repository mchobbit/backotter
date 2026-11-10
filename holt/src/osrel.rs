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
/// boot tools (update-initramfs, update-grub); Arch and Artix have mkinitcpio
/// and grub-mkconfig, Alpine mkinitfs and grub-mkconfig, Fedora dracut and
/// grub2-mkconfig; AerynOS's initramfs comes prebuilt with its kernel, and
/// systemd-boot boots it.
pub const SUPPORTED: &[(&str, &str, &str)] = &[
    ("debian", "13", "Debian 13"),
    ("linuxmint", "22", "Linux Mint 22"),
    ("ubuntu", "24", "Ubuntu 24.04"),
    ("ubuntu", "26", "Ubuntu 26.04"),
    ("zorin", "18", "Zorin OS 18"),
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
    // Rolling, Arch-based.
    ("cachyos", "", "CachyOS"),
    ("void", "", "Void Linux"),
    ("nixos", "26", "NixOS 26.05"),
    // Rolling: VERSION_ID is the snapshot's date.
    ("opensuse-tumbleweed", "*", "openSUSE Tumbleweed"),
    ("rocky", "10", "Rocky Linux 10"),
    // Arch-based, rolling.
    ("omarchy", "*", "Omarchy"),
    // Arch-based, rolling; neither has a VERSION_ID.  EndeavourOS boots by
    // systemd-boot with dracut, Manjaro by GRUB with mkinitcpio.
    ("endeavouros", "", "EndeavourOS"),
    ("manjaro", "", "Manjaro"),
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
        assert!(e.contains("Debian 13, Linux Mint 22, Ubuntu 24.04, Ubuntu 26.04, Zorin OS 18, Arch Linux, Fedora 44, Alpine Linux 3, Artix Linux, AerynOS, Debian 12, Devuan 6, Pop!_OS 24.04, CachyOS, Void Linux, NixOS 26.05, openSUSE Tumbleweed, Rocky Linux 10, Omarchy, EndeavourOS, Manjaro, FreeBSD 14, FreeBSD 15, HardenedBSD 15, GhostBSD 26, NetBSD 11, AlpineBSD 1, OpenBSD 8, DragonFly 6, Haiku, OmniOS, Redox or Sculpt OS"), "{e}");
        for t in ["ID=devuan\nVERSION_ID=6\n", "ID=pop\nVERSION_ID=\"24.04\"\n", "ID=cachyos\nBUILD_ID=rolling\n", "ID=void\n", "ID=nixos\nVERSION_ID=\"26.05\"\n", "ID=\"opensuse-tumbleweed\"\nVERSION_ID=\"20261101\"\n", "ID=rocky\nVERSION_ID=\"10.2\"\n"] {
            assert!(check_supported(Some(&parse(t))).is_ok(), "{t}");
        }
        assert!(check_supported(Some(&parse("ID=alpine\nVERSION_ID=3.24.3\n"))).is_ok());
        assert!(check_supported(Some(&parse("ID=artix\nNAME=\"Artix Linux\"\n"))).is_ok());
        assert!(check_supported(Some(&parse("NAME=\"EndeavourOS\"\nID=\"endeavouros\"\nID_LIKE=\"arch\"\nBUILD_ID=rolling\n"))).is_ok());
        assert!(check_supported(Some(&parse("NAME=\"Manjaro Linux\"\nID=manjaro\nID_LIKE=arch\nBUILD_ID=rolling\n"))).is_ok());
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
