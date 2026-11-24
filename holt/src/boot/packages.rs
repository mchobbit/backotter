//! Installed packages from each system's package database (dpkg, pacman,
//! apk, rpm, xbps, eopkg, Portage, pkgtools), the packages boot.packages
//! lists, and debconf's answers: crate::boot's package state, which it
//! re-exports.

use crate::sys::Sys;

/// Installed packages from /var/lib/dpkg/status: (name, version, arch).
pub fn parse_dpkg_status(text: &str) -> Vec<(String, String, String)> {
    let mut out = Vec::new();
    for stanza in text.split("\n\n") {
        let (mut name, mut ver, mut arch, mut installed) = (None, None, None, false);
        for line in stanza.lines() {
            if let Some(v) = line.strip_prefix("Package: ") {
                name = Some(v.trim().to_string());
            } else if let Some(v) = line.strip_prefix("Version: ") {
                ver = Some(v.trim().to_string());
            } else if let Some(v) = line.strip_prefix("Architecture: ") {
                arch = Some(v.trim().to_string());
            } else if let Some(v) = line.strip_prefix("Status: ") {
                installed = v.split_whitespace().nth(2) == Some("installed");
            }
        }
        if let (Some(n), Some(v), true) = (name, ver, installed) {
            out.push((n, v, arch.unwrap_or_default()));
        }
    }
    out
}

/// Installed packages from pacman's local database (Arch): each
/// /var/lib/pacman/local/NAME-VERSION/desc holds %NAME%, %VERSION%, %ARCH%.
pub fn pacman_packages(sys: &Sys) -> Vec<(String, String, String)> {
    let mut out = Vec::new();
    for d in sys.list("/var/lib/pacman/local") {
        let Some(t) = sys.read(&format!("/var/lib/pacman/local/{d}/desc")) else { continue };
        let field = |k: &str| {
            let mut it = t.lines();
            it.by_ref().find(|l| l.trim() == k)?;
            it.next().map(|v| v.trim().to_string())
        };
        if let (Some(n), Some(v)) = (field("%NAME%"), field("%VERSION%")) {
            out.push((n, v, field("%ARCH%").unwrap_or_default()));
        }
    }
    out.sort();
    out
}

/// Installed packages from apk's database (Alpine): blocks of `K:value`
/// lines, P the name, V the version, A the architecture.
pub fn parse_apk_installed(text: &str) -> Vec<(String, String, String)> {
    let mut out = Vec::new();
    for block in text.split("\n\n") {
        let f = |k: &str| block.lines().find_map(|l| l.strip_prefix(k)).map(|v| v.trim().to_string());
        if let (Some(n), Some(v)) = (f("P:"), f("V:")) {
            out.push((n, v, f("A:").unwrap_or_default()));
        }
    }
    out.sort();
    out
}

/// Installed packages from rpm (Fedora): its database is SQLite, so rpm
/// itself reads it.
pub fn rpm_packages(sys: &Sys) -> Vec<(String, String, String)> {
    let Some(t) = sys.run("rpm", &["-qa", "--qf", "%{NAME}\t%{VERSION}-%{RELEASE}\t%{ARCH}\n"]) else { return Vec::new() };
    let mut out: Vec<(String, String, String)> = t
        .lines()
        .filter_map(|l| {
            let mut f = l.split('\t');
            Some((f.next()?.to_string(), f.next()?.to_string(), f.next().unwrap_or("").to_string()))
        })
        .collect();
    out.sort();
    out
}

/// Installed packages from xbps's database (Void): a property list whose
/// top-level dict has a dict per package, with `pkgver` ("NAME-VERSION_REV")
/// and `architecture`.
pub fn parse_xbps_pkgdb(text: &str) -> Vec<(String, String, String)> {
    let mut out = Vec::new();
    let mut depth = 0usize;
    let (mut name, mut key) = (String::new(), String::new());
    let (mut ver, mut arch) = (String::new(), String::new());
    let mut rest = text;
    while let Some(i) = rest.find('<') {
        let Some(j) = rest[i..].find('>') else { break };
        let tag = &rest[i + 1..i + j];
        let after = &rest[i + j + 1..];
        let body = || after.split('<').next().unwrap_or("").replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">");
        match tag {
            "dict" => depth += 1,
            "/dict" => {
                if depth == 2 {
                    if !ver.is_empty() {
                        let v = ver.strip_prefix(&format!("{name}-")).unwrap_or(&ver).to_string();
                        out.push((name.clone(), v, arch.clone()));
                    }
                    name.clear();
                    ver.clear();
                    arch.clear();
                }
                depth = depth.saturating_sub(1);
            }
            "key" if depth == 1 => name = body(),
            "key" => key = body(),
            "string" if depth == 2 && key == "pkgver" => ver = body(),
            "string" if depth == 2 && key == "architecture" => arch = body(),
            _ => {}
        }
        rest = after;
    }
    out.sort();
    out
}

/// eopkg's database (Solus): a directory a package.
pub const EOPKG_PACKAGES: &str = "/var/lib/eopkg/package";

/// An eopkg database directory's name, NAME-VERSION-RELEASE, split into the
/// name (which may hold dashes) and VERSION-RELEASE (VERSION holds none,
/// RELEASE is digits), as testvm/update/pkgset.sh lists it:
/// clr-boot-manager-3.6.0-39 is (clr-boot-manager, 3.6.0-39).
pub fn split_eopkg(dir: &str) -> Option<(&str, &str)> {
    let r = dir.rfind('-')?;
    let v = dir[..r].rfind('-')?;
    let (name, ver, rel) = (&dir[..v], &dir[v + 1..r], &dir[r + 1..]);
    (!name.is_empty() && !ver.is_empty() && !rel.is_empty() && rel.bytes().all(|c| c.is_ascii_digit())).then(|| (name, &dir[v + 1..]))
}

/// The text of `<tag>` in `xml`'s first `<tag>...</tag>` after `after`
/// (the whole text when `after` is empty), entities left as they are.
fn xml_text<'a>(xml: &'a str, after: &str, tag: &str) -> Option<&'a str> {
    let from = if after.is_empty() { 0 } else { xml.find(after)? + after.len() };
    let open = format!("<{tag}>");
    let i = from + xml[from..].find(&open)? + open.len();
    let j = i + xml[i..].find(&format!("</{tag}>"))?;
    Some(xml[i..j].trim())
}

/// Installed packages from eopkg's database (Solus): each
/// /var/lib/eopkg/package/NAME-VERSION-RELEASE holds metadata.xml (and
/// files.xml).  The name is metadata.xml's <Package><Name> (its first
/// <Name> is the <Source>'s, which a subpackage doesn't share) when the
/// directory's name starts with it, the version the rest of the directory's
/// name (split_eopkg, as vm-update lists it), the arch <Architecture>.
pub fn eopkg_packages(sys: &Sys) -> Vec<(String, String, String)> {
    let mut out = Vec::new();
    for d in sys.list(EOPKG_PACKAGES) {
        if d.starts_with('.') {
            continue;
        }
        let meta = sys.read(&format!("{EOPKG_PACKAGES}/{d}/metadata.xml")).unwrap_or_default();
        let by_meta = xml_text(&meta, "<Package>", "Name").and_then(|n| d.strip_prefix(n)?.strip_prefix('-').filter(|v| split_eopkg(&format!("x-{v}")).is_some()).map(|v| (n, v)));
        let Some((n, v)) = by_meta.or_else(|| split_eopkg(&d)) else { continue };
        out.push((n.to_string(), v.to_string(), xml_text(&meta, "<Package>", "Architecture").unwrap_or("").to_string()));
    }
    out.sort();
    out
}

/// Whether this is Gentoo: baselayout's /etc/gentoo-release.
pub fn is_gentoo(sys: &Sys) -> bool {
    sys.exists("/etc/gentoo-release")
}

/// Whether `v` is a Portage version without its revision: `N(.N)*[a-z]?`
/// then any of `_alpha`, `_beta`, `_pre`, `_rc` and `_p`, each with an
/// optional number ("1.0.4", "2.03.39", "1.2.3b_p20250101", "9999").
/// Read from the end, as the suffixes come last.
pub fn is_portage_version(v: &str) -> bool {
    let mut core = v;
    while let Some(i) = core.rfind('_') {
        let word = core[i + 1..].trim_end_matches(|c: char| c.is_ascii_digit());
        if !matches!(word, "alpha" | "beta" | "pre" | "rc" | "p") {
            return false;
        }
        core = &core[..i];
    }
    let core = core.strip_suffix(|c: char| c.is_ascii_lowercase()).unwrap_or(core);
    !core.is_empty() && core.split('.').all(|p| !p.is_empty() && p.bytes().all(|c| c.is_ascii_digit()))
}

/// A Portage PF ("font-adobe-100dpi-1.0.4", "grub-2.16-r1") split into the
/// package's name and its version with any `-rN` revision ("grub",
/// "2.16-r1").  The name can hold dashes and digits; the version is what
/// follows its last dash once a revision is set aside.
pub fn split_pf(pf: &str) -> Option<(&str, &str)> {
    let base = match pf.rfind("-r") {
        Some(i) if i + 2 < pf.len() && pf[i + 2..].bytes().all(|c| c.is_ascii_digit()) => &pf[..i],
        _ => pf,
    };
    let j = base.rfind('-')?;
    (j > 0 && is_portage_version(&base[j + 1..])).then(|| (&pf[..j], &pf[j + 1..]))
}

/// Installed packages from Portage's database (Gentoo): a directory per
/// package, /var/db/pkg/CATEGORY/PF, whose files PF and CHOST hold its name
/// and version and the system it was built for (the vdb has no
/// architecture).  Names are bare, without the category, as everywhere
/// here (docs/design/gentoo.md, decision 4).  A merge under way
/// (`-MERGING-PF`) isn't installed yet.
pub fn portage_packages(sys: &Sys) -> Vec<(String, String, String)> {
    let mut out = Vec::new();
    for cat in sys.list("/var/db/pkg") {
        let cdir = format!("/var/db/pkg/{cat}");
        if cat.starts_with('.') || !sys.is_dir(&cdir) {
            continue;
        }
        for d in sys.list(&cdir) {
            let dir = format!("{cdir}/{d}");
            if d.starts_with(['-', '.']) || !sys.is_dir(&dir) {
                continue;
            }
            let pf = sys.read_trim(&format!("{dir}/PF")).unwrap_or(d);
            let Some((n, v)) = split_pf(&pf) else { continue };
            out.push((n.to_string(), v.to_string(), sys.read_trim(&format!("{dir}/CHOST")).unwrap_or_default()));
        }
    }
    out.sort();
    out
}

/// Whether this is Slackware: aaa_base's /etc/slackware-version.
pub fn is_slackware(sys: &Sys) -> bool {
    sys.is_file("/etc/slackware-version")
}

/// pkgtools' database (Slackware): installpkg's ADM_DIR, where
/// /var/log/packages is a link to it since 15.0 (a directory before).
pub fn pkgtools_dir(sys: &Sys) -> Option<&'static str> {
    ["/var/lib/pkgtools/packages", "/var/log/packages"].into_iter().find(|d| sys.is_dir(d))
}

/// A pkgtools package's file name, NAME-VERSION-ARCH-BUILD, split at its
/// last three dashes (none of the three holds one) into the name, VERSION-BUILD
/// as the version (as dpkg's and rpm's versions carry the packaging
/// revision, and as testvm/update/pkgset.sh lists it: a rebuild is a new
/// version), and the arch: kernel-huge-5.15.161-x86_64-1 is (kernel-huge,
/// 5.15.161-1, x86_64).
pub fn split_pkgtools(file: &str) -> Option<(&str, String, &str)> {
    let mut it = file.rsplitn(4, '-');
    let (build, arch, ver, name) = (it.next()?, it.next()?, it.next()?, it.next()?);
    (!name.is_empty() && !ver.is_empty() && !arch.is_empty() && !build.is_empty()).then(|| (name, format!("{ver}-{build}"), arch))
}

/// Installed packages from pkgtools' database (Slackware): a file a
/// package, named NAME-VERSION-ARCH-BUILD (split_pkgtools).
pub fn pkgtools_packages(sys: &Sys) -> Vec<(String, String, String)> {
    let Some(dir) = pkgtools_dir(sys) else { return Vec::new() };
    let mut out: Vec<(String, String, String)> = sys
        .list(dir)
        .iter()
        .filter(|f| !f.starts_with('.'))
        .filter_map(|f| split_pkgtools(f).map(|(n, v, a)| (n.to_string(), v, a.to_string())))
        .collect();
    out.sort();
    out
}

/// The installed packages, from whichever package manager the system has.
/// Portage first: a Gentoo can have dpkg or rpm emerged as tools.
pub fn installed_packages(sys: &Sys) -> Vec<(String, String, String)> {
    if is_gentoo(sys) {
        return portage_packages(sys);
    }
    // Slackware next: a full install has rpm (series ap).
    if is_slackware(sys) {
        return pkgtools_packages(sys);
    }
    if let Some(t) = sys.read("/var/lib/dpkg/status") {
        return parse_dpkg_status(&t);
    }
    if sys.is_dir("/var/lib/pacman/local") {
        return pacman_packages(sys);
    }
    if sys.is_dir("/var/lib/rpm") || sys.is_dir("/usr/lib/sysimage/rpm") {
        return rpm_packages(sys);
    }
    if let Some(t) = sys.read("/lib/apk/db/installed") {
        return parse_apk_installed(&t);
    }
    if let Some(f) = sys.list("/var/db/xbps").into_iter().filter(|f| f.starts_with("pkgdb-") && f.ends_with(".plist")).max() {
        if let Some(t) = sys.read(&format!("/var/db/xbps/{f}")) {
            return parse_xbps_pkgdb(&t);
        }
    }
    if sys.is_dir(EOPKG_PACKAGES) {
        return eopkg_packages(sys);
    }
    // Guix System has no database: the current generation's profile
    // manifest lists its packages (last: Guix on another system is that
    // system's packages).
    if crate::store::is_guix(sys) {
        return crate::store::guix_packages(sys);
    }
    Vec::new()
}

/// The packages that make up the storage and boot stack.
pub const PACKAGES: &[&str] = &[
    "grub-pc", "grub-pc-bin", "grub-efi-amd64", "grub-efi-amd64-bin", "grub-efi-amd64-signed", "grub-efi-ia32",
    "grub-efi-ia32-bin", "grub-efi-arm64", "grub-efi-arm64-signed", "grub2-common", "grub-common", "shim-signed",
    "shim-unsigned", "shim-helpers-amd64-signed", "mokutil", "efibootmgr", "systemd-boot", "systemd-boot-efi",
    "systemd-ukify", "refind", "syslinux", "extlinux", "syslinux-efi", "os-prober", "linux-image-amd64",
    "linux-image-cloud-amd64", "linux-image-rt-amd64", "initramfs-tools", "dracut", "tiny-initramfs", "mdadm",
    "lvm2", "thin-provisioning-tools", "cryptsetup", "cryptsetup-initramfs", "cryptsetup-bin", "clevis-luks",
    "systemd-cryptsetup", "tpm2-tools", "zfsutils-linux", "zfs-initramfs", "zfs-dracut", "zfs-dkms", "btrfs-progs",
    "xfsprogs", "e2fsprogs", "dosfstools", "exfatprogs", "ntfs-3g", "jfsutils", "f2fs-tools", "nilfs-tools", "bcachefs-tools",
    // Arch's and Fedora's names for the same.
    "grub", "grub2-pc", "grub2-efi-x64", "grub2-tools", "shim-x64", "mkinitcpio", "linux", "linux-lts", "kernel-core",
    "zfs-utils", "selinux-policy-targeted",
    // ARM64's (crate::arch): Debian's and Ubuntu's, Fedora's, Chimera's;
    // names no amd64 system has, so no fingerprint changes.
    "grub-efi-arm64-bin", "shim-helpers-arm64-signed", "linux-image-arm64", "linux-image-cloud-arm64", "grub2-efi-aa64", "shim-aa64", "grub-arm64-efi",
    // Alpine's.
    "grub-bios", "grub-efi", "mkinitfs", "linux-virt",
    // EndeavourOS's and Manjaro's.
    "kernel-install-for-dracut", "eos-dracut", "mhwd",
    // BigLinux's: grub-btrfs's snapshot menu (with Timeshift's hook) and the
    // one-preset mkinitcpio; only names no other guest has, so no other
    // fingerprint changes.
    "grub-btrfs", "grub-btrfs-timeshift", "faster-mkinitcpio",
    // Garuda's ("grub-btrfs", "snapper" and "dracut" are above): the dracut
    // hook load keys on, its kernel, and how snapshots reach the boot menu.
    "garuda-dracut-support", "linux-garuda", "snap-pac", "snapper-support",
    // Omarchy's, openSUSE's and Void's.
    "limine", "limine-mkinitcpio-hook", "limine-snapper-sync", "grub2", "grub2-i386-pc", "grub2-x86_64-efi", "kernel-default", "snapper", "linux-base",
    "multipath-tools", "dmraid", "nvme-cli", "open-iscsi",
    // Chimera's ("grub", "linux-lts", "initramfs-tools", "systemd-boot" and
    // the rest are above): its ZFS userland is "zfs", built for the kernel
    // by ckms (zfs-ckms) or prebuilt (linux-*-zfs-bin).
    "grub-i386-pc", "grub-x86_64-efi", "linux-stable", "linux-lts-zfs-bin", "linux-stable-zfs-bin", "zfs", "zfs-ckms", "ckms",
    "cryptsetup-scripts",
    // Gentoo's ("grub", "dracut", "lvm2" and the rest are above, by bare
    // name): its kernels, installkernel and the initramfs generators it
    // can call, systemd-utils (udev, and systemd-boot without systemd).
    "gentoo-kernel-bin", "gentoo-kernel", "gentoo-sources", "installkernel", "genkernel", "ugrd", "systemd-utils", "zfs-kmod",
    // PikaOS's: booster, its kernel by metapackage (linux-image-K-pikaos is
    // versioned, as Debian's linux-image-K are, and boot.kernels lists K),
    // rEFInd's theme and btrfs snapshot menu, zram swap.
    "booster", "kernel-pika", "kernel-pika-runtime", "pika-refind-theme", "refind-btrfs", "zram-tools",
    // AnduinOS's: its own GRUB (signed on the machine with a MOK), Ubuntu's
    // shim repackaged, the toolkit that signs and deploys them, and its
    // Btrfs snapshot manager; names no other guest has.
    "anduinos-grub", "anduinos-shim-signed", "anduinos-secureboot-toolkit", "anduinos-btrfs-snapshots-manager",
    // Proxmox VE's ("zfs-initramfs" is above): proxmox-boot-tool, its kernel
    // by metapackage, and the product (pve-manager's version is the PVE
    // release, os.product).
    "proxmox-kernel-helper", "proxmox-default-kernel", "proxmox-ve", "pve-manager",
    // Slackware's ("grub" and "efibootmgr" are above): its loaders, its
    // initrd tool, its kernels (huge, or generic with an initrd) and their
    // modules, and its updater.
    "lilo", "elilo", "mkinitrd", "kernel-huge", "kernel-generic", "kernel-modules", "slackpkg",
    // Guix System's, from its system profile's manifest ("grub", "grub-efi",
    // "cryptsetup", "btrfs-progs", "e2fsprogs" and "efibootmgr" are above):
    // its kernel and its init.
    "linux-libre", "shepherd",
    // TrueNAS's ("grub-pc", "grub-efi-amd64-bin" and "zfs-initramfs" are
    // above): its middleware, by the names its packages are guessed to have
    // (docs/design/truenas.md; none on another system, so no fingerprint
    // changes).
    "truenas", "middlewared",
    // ZFSBootMenu's ("zfs", "dracut" and "linux" are above): its package
    // (generate-zbm's images; the release EFI has none), and Void's
    // mainline kernel series (docs/design/zfsbootmenu.md).
    "zfsbootmenu", "linux-mainline",
    // Solus's ("linux-lts", "shim-signed", "dracut" and "efibootmgr" are
    // above): clr-boot-manager, which writes shim, systemd-boot and the
    // kernels to the ESP, its kernel, and usysconf, which runs it after
    // each eopkg transaction (docs/design/solus.md).  systemd-boot is
    // systemd's own file there, and "systemd" every guest has.
    "clr-boot-manager", "linux-current", "usysconf",
    // Qubes OS's dom0 ("grub2-efi-x64", "dracut", "lvm2" and the rest are
    // above): the Xen hypervisor GRUB boots and its tools, dom0's newer
    // kernel series, and Qubes's own core and release (docs/design/qubes.md;
    // names no other guest has, so no fingerprint changes).
    "xen", "xen-hypervisor", "xen-runtime", "kernel-latest", "qubes-core-dom0", "qubes-release",
];

/// debconf answers from /var/cache/debconf/config.dat whose names start with
/// one of `prefixes`.
pub fn parse_debconf(text: &str, prefixes: &[&str]) -> Vec<(String, String)> {
    let mut out = Vec::new();
    for rec in text.split("\n\n") {
        let mut name = None;
        let mut value = None;
        for line in rec.lines() {
            if let Some(v) = line.strip_prefix("Name: ") {
                name = Some(v.trim().to_string());
            } else if let Some(v) = line.strip_prefix("Value:") {
                value = Some(v.trim().to_string());
            }
        }
        if let (Some(n), Some(v)) = (name, value) {
            if prefixes.iter().any(|p| n.starts_with(p)) {
                out.push((n, v));
            }
        }
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::sys::FakeRunner;
    use crate::testutil::Tree;

    const DPKG: &str = "Package: grub-efi-amd64-signed
Status: install ok installed
Priority: optional
Architecture: amd64
Version: 1+2.12+9+deb13u2
Description: GRand Unified Bootloader, version 2 (EFI-AMD64 version)
 multi-line
 description

Package: grub-pc
Status: deinstall ok config-files
Architecture: amd64
Version: 2.12-9

Package: lvm2
Status: install ok installed
Architecture: amd64
Version: 2.03.31-2

Package: half
Status: install ok half-installed
Version: 1
";

    #[test]
    fn dpkg_status() {
        let p = parse_dpkg_status(DPKG);
        assert_eq!(
            p,
            vec![
                ("grub-efi-amd64-signed".to_string(), "1+2.12+9+deb13u2".to_string(), "amd64".to_string()),
                ("lvm2".to_string(), "2.03.31-2".to_string(), "amd64".to_string())
            ]
        );
    }

    #[test]
    fn debconf() {
        let t = "Name: grub-pc/install_devices\nTemplate: grub-pc/install_devices\nValue: /dev/disk/by-id/ata-QEMU_HARDDISK_QM00001\nOwners: grub-pc\nFlags: seen\n\nName: grub2/update_nvram\nTemplate: grub2/update_nvram\nValue: true\nOwners: grub-efi-amd64\n\nName: grub2/kfreebsd_cmdline\nTemplate: grub2/kfreebsd_cmdline\nOwners: grub-efi-amd64\n\nName: tzdata/Areas\nValue: Europe\n";
        let d = parse_debconf(t, &["grub-pc/", "grub2/"]);
        assert_eq!(
            d,
            vec![
                ("grub-pc/install_devices".into(), "/dev/disk/by-id/ata-QEMU_HARDDISK_QM00001".into()),
                ("grub2/update_nvram".into(), "true".into())
            ]
        );
    }

    #[test]
    fn xbps() {
        let db = "<?xml version=\"1.0\"?>\n<plist version=\"1.0\">\n<dict>\n\t<key>dracut</key>\n\t<dict>\n\t\t<key>architecture</key>\n\t\t<string>x86_64-musl</string>\n\t\t<key>pkgver</key>\n\t\t<string>dracut-105_1</string>\n\t\t<key>run_depends</key>\n\t\t<array>\n\t\t\t<string>bash&gt;=0</string>\n\t\t</array>\n\t\t<key>alternatives</key>\n\t\t<dict>\n\t\t\t<key>x</key>\n\t\t\t<array><string>a:b</string></array>\n\t\t</dict>\n\t</dict>\n\t<key>grub</key>\n\t<dict>\n\t\t<key>pkgver</key>\n\t\t<string>grub-2.12_3</string>\n\t\t<key>architecture</key>\n\t\t<string>x86_64-musl</string>\n\t</dict>\n\t<key>_XBPS_ALTERNATIVES_</key>\n\t<dict>\n\t\t<key>x</key>\n\t\t<array><string>dracut</string></array>\n\t</dict>\n</dict>\n</plist>\n";
        assert_eq!(
            parse_xbps_pkgdb(db),
            vec![("dracut".into(), "105_1".into(), "x86_64-musl".into()), ("grub".into(), "2.12_3".into(), "x86_64-musl".into())]
        );
    }

    #[test]
    fn portage_pf() {
        for (pf, n, v) in [
            ("font-adobe-100dpi-1.0.4", "font-adobe-100dpi", "1.0.4"),
            ("grub-2.16-r1", "grub", "2.16-r1"),
            ("gentoo-kernel-bin-6.18.63", "gentoo-kernel-bin", "6.18.63"),
            ("installkernel-70-r1", "installkernel", "70-r1"),
            ("dracut-112", "dracut", "112"),
            ("linux-firmware-20261019", "linux-firmware", "20261019"),
            ("openssl-3.5.4_p1", "openssl", "3.5.4_p1"),
            ("python-3.14.0_rc3-r2", "python", "3.14.0_rc3-r2"),
            ("foo-1.2b_beta2_p20250101-r10", "foo", "1.2b_beta2_p20250101-r10"),
            ("xf86-video-r128-6.12.1", "xf86-video-r128", "6.12.1"),
            ("avahi-0-r3", "avahi", "0-r3"),
            ("libsigc++-2.12.1-r1", "libsigc++", "2.12.1-r1"),
            ("vim-9999", "vim", "9999"),
        ] {
            assert_eq!(split_pf(pf), Some((n, v)), "{pf}");
        }
        for pf in ["grub", "grub-", "-1.0", "foo-r1", "foo-1.0_gamma", "foo-1..2", "foo-1.0ab", "foo-v1.0", "foo-1.0-rc"] {
            assert_eq!(split_pf(pf), None, "{pf}");
        }
        assert!(is_portage_version("2.03.39") && is_portage_version("1.0a_pre") && !is_portage_version("") && !is_portage_version("_p1"));
    }

    /// /var/db/pkg read as Portage keeps it, before dpkg's (a Gentoo can
    /// have dpkg emerged): bare names, PF's version, CHOST as the arch;
    /// a merge under way and a stray file left out.
    #[test]
    fn portage_vdb() {
        let t = Tree::new("portage-vdb");
        t.file("/etc/gentoo-release", "Gentoo Base System release 2.18
")
            .file("/var/db/pkg/sys-boot/grub-2.16-r1/PF", "grub-2.16-r1
")
            .file("/var/db/pkg/sys-boot/grub-2.16-r1/CHOST", "x86_64-pc-linux-gnu
")
            .file("/var/db/pkg/sys-kernel/dracut-112/PF", "dracut-112
")
            .file("/var/db/pkg/sys-kernel/dracut-112/CHOST", "x86_64-pc-linux-gnu
")
            .file("/var/db/pkg/sys-kernel/-MERGING-dracut-113/PF", "dracut-113
")
            .dir("/var/db/pkg/virtual/dist-kernel-6.18.63")
            .file("/var/db/pkg/sys-kernel/stray", "x")
            .file("/var/lib/dpkg/status", "Package: dpkg\nStatus: install ok installed\nVersion: 1.22\n");
        let s = Sys::new(t.root(), Box::new(FakeRunner::default()), false);
        assert_eq!(
            installed_packages(&s),
            vec![
                ("dist-kernel".into(), "6.18.63".into(), String::new()),
                ("dracut".into(), "112".into(), "x86_64-pc-linux-gnu".into()),
                ("grub".into(), "2.16-r1".into(), "x86_64-pc-linux-gnu".into())
            ]
        );
        std::fs::remove_file(t.p("/etc/gentoo-release")).unwrap();
        assert_eq!(installed_packages(&s), vec![("dpkg".into(), "1.22".into(), String::new())]);
    }

    #[test]
    fn pkgtools() {
        assert_eq!(split_pkgtools("kernel-huge-5.15.161-x86_64-1"), Some(("kernel-huge", "5.15.161-1".to_string(), "x86_64")));
        assert_eq!(split_pkgtools("glibc-zoneinfo-2025b-noarch-1_slack15.0"), Some(("glibc-zoneinfo", "2025b-1_slack15.0".to_string(), "noarch")));
        assert_eq!(split_pkgtools("lilo-24.2-x86_64-6"), Some(("lilo", "24.2-6".to_string(), "x86_64")));
        assert_eq!(split_pkgtools("a-b-c"), None);
        assert_eq!(split_pkgtools("-1-x86_64-1"), None);
        let t = Tree::new("pkgtools");
        t.file("/etc/slackware-version", "Slackware 15.0\n")
            .file("/var/lib/pkgtools/packages/lilo-24.2-x86_64-6", "PACKAGE NAME: lilo-24.2-x86_64-6\n")
            .file("/var/lib/pkgtools/packages/kernel-huge-5.15.161-x86_64-1", "")
            .file("/var/lib/pkgtools/packages/broken", "")
            .symlink("/var/log/packages", "../lib/pkgtools/packages")
            // A full install's rpm (series ap) has a database too.
            .dir("/var/lib/rpm");
        let s = Sys::new(t.root(), Box::new(FakeRunner::default()), false);
        let p = installed_packages(&s);
        assert_eq!(p, vec![("kernel-huge".into(), "5.15.161-1".into(), "x86_64".into()), ("lilo".into(), "24.2-6".into(), "x86_64".into())]);
        // Before 15.0, /var/log/packages was the directory.
        let t = Tree::new("pkgtools-old");
        t.file("/etc/slackware-version", "Slackware 14.2\n").file("/var/log/packages/elilo-3.16-x86_64-1", "");
        let s = Sys::new(t.root(), Box::new(FakeRunner::default()), false);
        assert_eq!(pkgtools_packages(&s), vec![("elilo".into(), "3.16-1".into(), "x86_64".into())]);
    }

    /// Names from the Solus 4.9 ISO's eopkg database (eopkg 4.4.2): the
    /// name may hold dashes and digits, the version none, the release is
    /// digits.
    #[test]
    fn eopkg_names() {
        for (d, n, v) in [
            ("clr-boot-manager-3.6.0-39", "clr-boot-manager", "3.6.0-39"),
            ("linux-current-6.18.21-333", "linux-current", "6.18.21-333"),
            ("shim-signed-15.8-7", "shim-signed", "15.8-7"),
            ("openssh-10.3_p1-64", "openssh", "10.3_p1-64"),
            ("grub2-2.14-41", "grub2", "2.14-41"),
            ("efibootmgr-18-3", "efibootmgr", "18-3"),
            ("eopkg-4.4.2-35", "eopkg", "4.4.2-35"),
            ("usysconf-0.5.13-49", "usysconf", "0.5.13-49"),
            ("usysconf-epoch-1.0.0-26", "usysconf-epoch", "1.0.0-26"),
            ("aalib-1.4.0_5-9", "aalib", "1.4.0_5-9"),
            ("linux-firmware-amdgpu-20260309-103", "linux-firmware-amdgpu", "20260309-103"),
        ] {
            assert_eq!(split_eopkg(d), Some((n, v)), "{d}");
        }
        for d in ["eopkg", "eopkg-4.4.2", "eopkg-4.4.2-r35", "-1.0-1", "x--1"] {
            assert_eq!(split_eopkg(d), None, "{d}");
        }
    }

    /// eopkg's database as the ISO has it: metadata.xml's first <Name> is
    /// the source's (linux-firmware for linux-firmware-amdgpu), its
    /// <Package><Name> the package's.
    #[test]
    fn eopkg_database() {
        let t = Tree::new("eopkg-db");
        let meta = |src: &str, n: &str, v: &str, r: &str| {
            format!("<PISI>\n    <Source>\n        <Name>{src}</Name>\n        <Packager>\n            <Name>Someone</Name>\n        </Packager>\n    </Source>\n    <Package>\n        <Name>{n}</Name>\n        <History>\n            <Update release=\"{r}\">\n                <Version>{v}</Version>\n                <Name>Someone</Name>\n            </Update>\n        </History>\n        <Architecture>x86_64</Architecture>\n    </Package>\n</PISI>")
        };
        for (src, n, v, r) in [("clr-boot-manager", "clr-boot-manager", "3.6.0", "39"), ("linux-firmware", "linux-firmware-amdgpu", "20260309", "103"), ("shim-signed", "shim-signed", "15.8", "7")] {
            let d = format!("{EOPKG_PACKAGES}/{n}-{v}-{r}");
            t.file(&format!("{d}/metadata.xml"), meta(src, n, v, r)).file(&format!("{d}/files.xml"), "<Files>\n</Files>");
        }
        // No metadata.xml: the directory's name alone.
        t.dir(&format!("{EOPKG_PACKAGES}/usysconf-epoch-1.0.0-26"));
        let s = Sys::new(t.root(), Box::new(FakeRunner::default()), false);
        let x = |n: &str, v: &str, a: &str| (n.to_string(), v.to_string(), a.to_string());
        let want = vec![x("clr-boot-manager", "3.6.0-39", "x86_64"), x("linux-firmware-amdgpu", "20260309-103", "x86_64"), x("shim-signed", "15.8-7", "x86_64"), x("usysconf-epoch", "1.0.0-26", "")];
        assert_eq!(eopkg_packages(&s), want);
        assert_eq!(installed_packages(&s), want);
    }
}
