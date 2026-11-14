//! Redaction for bug reports (`bko-holt report-bug`).  GitHub issues are
//! public, and a dump names the machine: its hostname, machine-id, every
//! UUID and GUID, disk serials and WWIDs, labels, user names (in /home
//! paths), key-file paths and MAC addresses.  Each of those gets a
//! pseudonym, the same one everywhere it appears, in every form it takes
//! (upper and lower case, with and without dashes, md's colon form, the
//! device-mapper escaping, GPT's mixed-endian bytes in an EFI load option),
//! so a reference from fstab to a partition, or from a dm name to its LUKS
//! header, still resolves.  Sizes, offsets, feature flags, partition and
//! filesystem types, kernel device names and the topology stay as they are,
//! and so do names that identify no one (an installer's default hostname,
//! labels such as EFI or swap0).
//!
//! A `Collector` learns the identifiers from one or more dumps (and any
//! extra values, such as a repository URL), then `seal` gives the
//! `Redactor` that rewrites JSON and text with them.  `unredact` maps the
//! pseudonyms back, which is how the tests prove that `load`'s plan of a
//! redacted dump is the plan of the original.

use crate::json::Value;
use std::collections::{BTreeMap, BTreeSet, HashMap};

#[derive(Clone, Copy, Debug, PartialEq, Eq, PartialOrd, Ord, Hash)]
pub enum Kind {
    Hostname,
    MachineId,
    Uuid,
    Serial,
    Label,
    User,
    KeyFile,
    Mac,
    /// ZFS GUIDs, hostids, MBR disk signatures, FAT and NTFS serials, DUIDs.
    Id,
    /// A repository, a password file: given on load's command line.
    Secret,
}

impl Kind {
    pub fn plural(self) -> &'static str {
        match self {
            Kind::Hostname => "hostnames",
            Kind::MachineId => "machine-ids",
            Kind::Uuid => "UUIDs and GUIDs",
            Kind::Serial => "serial numbers and WWIDs",
            Kind::Label => "labels",
            Kind::User => "user names",
            Kind::KeyFile => "key-file paths",
            Kind::Mac => "MAC addresses",
            Kind::Id => "other identifiers (ZFS GUIDs, hostids, disk signatures, FAT/NTFS serials, DUIDs)",
            Kind::Secret => "repositories and password-file paths",
        }
    }

    /// Whether the summary may print the originals (on the terminal only).
    pub fn shown(self) -> bool {
        matches!(self, Kind::Hostname | Kind::Label | Kind::User | Kind::KeyFile | Kind::Serial | Kind::Secret)
    }
}

/// One identifier: what it was, and what the bundle calls it.
#[derive(Clone, Debug, PartialEq)]
pub struct Entry {
    pub kind: Kind,
    pub original: String,
    pub pseudonym: String,
}

/// Where a substitution may match.
#[derive(Clone, Copy, Debug, PartialEq)]
enum Edge {
    /// Anywhere (long, random-looking forms).
    Any,
    /// Not inside a longer run of letters and digits.
    Word,
    /// Not inside a longer run of hex digits ("0x02290173" and
    /// "02290173-01" hold the signature 02290173; "1102290173" doesn't).
    Hex,
    /// Not followed by a letter or digit ("/system.key" at the end of any
    /// path, not in "/system.keys").
    Right,
    /// Not preceded by one (a serial in FreeBSD's DISK-SERIALp2).
    Left,
}

impl Edge {
    fn fits(self, b: &[u8], i: usize, len: usize) -> bool {
        let edge = |c: u8| match self {
            Edge::Any => false,
            Edge::Word | Edge::Right | Edge::Left => is_alnum(c),
            Edge::Hex => is_hex(c),
        };
        (self == Edge::Right || i == 0 || !edge(b[i - 1])) && (self == Edge::Left || b.get(i + len).is_none_or(|&c| !edge(c)))
    }
}

#[derive(Clone, Debug)]
struct Sub {
    from: String,
    to: String,
    edge: Edge,
}

#[derive(Clone, Debug, PartialEq)]
enum Ident {
    /// 16 bytes in the order the canonical text form prints them.
    Uuid([u8; 16], Kind),
    /// LVM's 32 letters and digits (printed 6-4-4-4-4-4-6).
    Lvm(String),
    /// Hex digits, lower case, without separators; the form first seen.
    Hex(String, String, Kind),
    /// A decimal number and the width of its hex form (16 for ZFS GUIDs as
    /// GRUB prints them, 8 for a hostid).
    Dec(u64, usize),
    Name(Kind, String),
    Path(Kind, String),
}

const HOST_KEYS: [&str; 4] = ["hostname", "creation_host", "homehost", "nodename"];
const SERIAL_KEYS: [&str; 4] = ["serial", "wwid", "serial_number", "wwn"];
const LABEL_KEYS: [&str; 5] = ["label", "partlabel", "boot_sector_label", "volume_label", "fs_label"];
const GUID_KEYS: [&str; 6] = ["guid", "pool_guid", "top_guid", "vdev_guid", "load_guid", "vdev_root_guid"];
const HEX_KEYS: [&str; 5] = ["disk_signature", "duid", "hostid", "ufsid", "volume_id"];
/// Keys whose UUID values are well known, not identifiers.
const TYPE_KEYS: [&str; 5] = ["type", "type_guid", "parttype", "partition_type", "vendor_guid"];
/// EFI vendors and the like: the same on every machine.
const WELL_KNOWN: [&str; 7] = [
    crate::efi::GLOBAL,
    crate::efi::LOADER,
    crate::efi::SHIM,
    "77fa9abd-0359-4d32-bd60-28f4e78f784b", // Microsoft
    "d719b2cb-3d3a-4596-a3bc-dad00e67656f", // EFI image security database
    "00000000-0000-0000-0000-000000000000",
    "ffffffff-ffff-ffff-ffff-ffffffffffff",
];
/// Hostnames installers give and live systems use: they identify no one.
const GENERIC_HOSTS: [&str; 35] = [
    "", "(none)", "localhost", "localhost.localdomain", "localdomain", "<system>", "any", "debian", "ubuntu", "ubuntu-server", "fedora",
    "archlinux", "archiso", "alpine", "nixos", "freebsd", "openbsd", "netbsd", "dragonfly", "omnios", "openindiana", "haiku", "void",
    "voidlinux", "mint", "zorin", "pop-os", "rocky", "backotter", "rescue",
    // PikaOS's installer copies the live system's hostname into every install.
    "pikaos",
    // Kali's installer's default hostname.
    "kali",
    // Proxmox VE's installer's default hostname.
    "pve",
    // Guix System: its installer's and `guix system image`'s hostnames.
    "gnu", "guix",
];
/// Labels (lower case, trailing digits and '*' dropped) that installers
/// and partitioners write: kept, because they help and identify no one.
const GENERIC_LABELS: [&str; 70] = [
    "", "efi", "esp", "efi system partition", "efi system", "efi-system", "efiboot", "bios", "biosboot", "bios boot partition",
    // Hybrid ISO images (xorriso: EFISYS, Gap1, Appended2...) and BackOtter's
    // and Haiku's own images.
    "efisys", "iso", "iso9660", "gap", "appended", "haiku esp", "backotter root", "backotter config",
    "bios boot", "boot", "root", "rootfs", "swap", "home", "var", "tmp", "usr", "srv", "data", "system", "grub", "grub2", "loader",
    "zfs", "zroot", "rpool", "bpool", "gptboot", "gptzfs", "gptswap", "freebsd", "freebsd-boot", "freebsd-zfs", "freebsd-swap",
    "freebsd-ufs", "haiku", "redox", "genode", "noname", "no name", "linux", "linux filesystem", "linux swap", "primary", "logical",
    "microsoft reserved partition", "basic data partition", "keystore-rpool", "nixboot", "nixos", "backotter", "zram",
    // PikaOS's installers: GPT names, and pikainstall's file system labels
    // (the 2.0.5 GTK installer's auto_xfs labels /boot's ext4 "PikaOS").
    "linux-efi", "linux-boot", "linux-root", "linux-home", "pikaboot", "pikaroot", "pikahome", "pikaos",
];
/// Ubuntu names the datasets for /home and /root USERDATA/home_ID and
/// USERDATA/root_ID.
const GENERIC_USERS: [&str; 3] = ["root", "home", "lost+found"];

/// Learns what to redact.
#[derive(Default)]
pub struct Collector {
    idents: Vec<Ident>,
    /// UUID-shaped values that name a type (partition types, EFI vendors).
    types: BTreeSet<String>,
    /// Lower-case words the OS calls itself: never pseudonyms.
    os_words: BTreeSet<String>,
    /// Every object key and its '_'/'-' parts: a name that is also one of
    /// these can't be replaced as a word.
    vocab: BTreeSet<String>,
    /// zfs_member labels: pool names, part of the topology.
    pool_labels: BTreeSet<String>,
    /// md's array names, "host:name": the labels whose colon is md's.
    md_labels: BTreeSet<String>,
    corpus: String,
}

/// A release medium's volume label: a system's name, its version and an
/// architecture ("Ubuntu-Server 26.04.1 LTS amd64", "Fedora-S-dvd-x86_64-44").
fn release_label(low: &str) -> bool {
    const SYSTEMS: [&str; 16] = ["ubuntu", "debian", "fedora", "rocky", "alma", "arch", "linuxmint", "mint", "pop", "zorin", "opensuse", "nixos", "pikaos", "kali", "proxmox", "guix"];
    const ARCHES: [&str; 5] = ["amd64", "x86_64", "x86-64", "arm64", "aarch64"];
    let first = low.split(|c: char| !c.is_ascii_alphanumeric()).next().unwrap_or("");
    SYSTEMS.contains(&first) && ARCHES.iter().any(|a| low.contains(a)) && low.bytes().any(|c| c.is_ascii_digit())
}

fn is_hex(c: u8) -> bool {
    c.is_ascii_hexdigit()
}

fn is_alnum(c: u8) -> bool {
    c.is_ascii_alphanumeric()
}

fn hex_val(c: u8) -> u8 {
    (c as char).to_digit(16).unwrap_or(0) as u8
}

fn hex_bytes(h: &str) -> Vec<u8> {
    h.as_bytes().chunks(2).map(|p| (hex_val(p[0]) << 4) | hex_val(*p.get(1).unwrap_or(&b'0'))).collect()
}

/// The UUID-shaped runs in `s`: 8-4-4-4-12 hex digits, not inside a longer
/// run of letters and digits.
fn uuid_spans(s: &str) -> Vec<(usize, usize)> {
    pattern_spans(s, &[8, 4, 4, 4, 12], b'-', is_hex)
}

fn lvm_spans(s: &str) -> Vec<(usize, usize)> {
    pattern_spans(s, &[6, 4, 4, 4, 4, 4, 6], b'-', is_alnum)
        .into_iter()
        .filter(|&(a, b)| {
            let t = &s.as_bytes()[a..b];
            t.iter().any(u8::is_ascii_digit) && t.iter().any(u8::is_ascii_alphabetic) && !t.iter().all(|&c| is_hex(c) || c == b'-')
        })
        .collect()
}

fn md_spans(s: &str) -> Vec<(usize, usize)> {
    pattern_spans(s, &[8, 8, 8, 8], b':', is_hex)
}

fn mac_spans(s: &str) -> Vec<(usize, usize)> {
    let mut out = pattern_spans(s, &[2, 2, 2, 2, 2, 2], b':', is_hex);
    // EFI's "MAC:525400123456" (a PXE entry's description) and "MAC(5254...".
    for pre in ["MAC:", "MAC("] {
        let b = s.as_bytes();
        let mut from = 0;
        while let Some(i) = s[from..].find(pre) {
            let st = from + i + pre.len();
            if b.len() >= st + 12 && b[st..st + 12].iter().all(|&c| is_hex(c)) && b.get(st + 12).is_none_or(|&c| !is_hex(c)) {
                out.push((st, st + 12));
            }
            from = st;
        }
    }
    out
}

fn pattern_spans(s: &str, groups: &[usize], sep: u8, ok: fn(u8) -> bool) -> Vec<(usize, usize)> {
    let b = s.as_bytes();
    let len: usize = groups.iter().sum::<usize>() + groups.len() - 1;
    let mut out = Vec::new();
    let mut i = 0;
    while i + len <= b.len() {
        let before_ok = i == 0 || !(is_alnum(b[i - 1]) || (sep == b':' && b[i - 1] == b':'));
        let after_ok = b.get(i + len).is_none_or(|&c| !(is_alnum(c) || (sep == b':' && c == b':')));
        if before_ok && after_ok && fits(&b[i..i + len], groups, sep, ok) {
            out.push((i, i + len));
            i += len;
        } else {
            i += 1;
        }
    }
    out
}

fn fits(t: &[u8], groups: &[usize], sep: u8, ok: fn(u8) -> bool) -> bool {
    let mut p = 0;
    for (n, g) in groups.iter().enumerate() {
        if n > 0 {
            if t[p] != sep {
                return false;
            }
            p += 1;
        }
        if !t[p..p + g].iter().all(|&c| ok(c)) {
            return false;
        }
        p += g;
    }
    true
}

/// A user name as /etc/passwd allows it.
fn user_like(u: &str) -> bool {
    let b = u.as_bytes();
    !b.is_empty() && b.len() <= 32 && (b[0].is_ascii_alphabetic() || b[0] == b'_') && b.iter().all(|&c| c.is_ascii_alphanumeric() || b"_.-".contains(&c))
}

impl Collector {
    pub fn new() -> Collector {
        Collector::default()
    }

    fn add(&mut self, id: Ident) {
        if !self.idents.contains(&id) {
            self.idents.push(id);
        }
    }

    /// Everything in `v` that identifies the machine.
    pub fn learn(&mut self, v: &Value) {
        self.corpus.push_str(&v.compact());
        self.corpus.push('\n');
        if let Some(os) = v.get("os") {
            for k in ["id", "id_like", "name", "version_codename", "variant_id"] {
                if let Some(s) = os.get(k).and_then(Value::as_str) {
                    for w in s.split(|c: char| !c.is_ascii_alphanumeric() && c != '-') {
                        self.os_words.insert(w.to_ascii_lowercase());
                        for part in w.split('-') {
                            self.os_words.insert(part.to_ascii_lowercase());
                        }
                    }
                }
            }
        }
        self.walk(v, "", "");
    }

    /// A value given on the command line or found outside a dump (a
    /// repository URL, a password file): replaced wherever it appears.
    pub fn learn_secret(&mut self, s: &str) {
        if s.len() >= 3 {
            self.corpus.push_str(s);
            self.corpus.push('\n');
            self.add(Ident::Path(Kind::Secret, s.to_string()));
        }
    }

    /// A key file named on the command line (--key-file NAME=FILE).
    pub fn learn_key_file(&mut self, path: &str) {
        self.key_path(path);
    }

    /// Text that will be redacted too: pseudonyms must not collide with it.
    pub fn see_text(&mut self, s: &str) {
        self.corpus.push_str(s);
        self.corpus.push('\n');
    }

    fn walk(&mut self, v: &Value, key: &str, parent: &str) {
        match v {
            Value::Obj(kv) => {
                if v.get("type").and_then(Value::as_str) == Some("zfs_member") {
                    if let Some(l) = v.get("label").and_then(Value::as_str) {
                        self.pool_labels.insert(l.to_string());
                    }
                }
                if key == "md_arrays" || v.get("type").and_then(Value::as_str) == Some("linux_raid_member") {
                    if let Some(l) = v.get("label").and_then(Value::as_str) {
                        self.md_labels.insert(l.to_string());
                    }
                }
                for (k, x) in kv {
                    // Not the topology's keys ("vda2 (partition, 1 GiB) ext4
                    // \"photos\""): those are text, holding names.
                    if !k.contains([' ', '"']) {
                        self.vocab.insert(k.clone());
                        for part in k.split(['_', '-', '.', ':', '/']) {
                            self.vocab.insert(part.to_string());
                        }
                    }
                    self.scan(k);
                    self.walk(x, k, key);
                }
            }
            Value::Arr(a) => {
                for x in a {
                    self.walk(x, key, parent);
                }
            }
            Value::Str(s) => self.string(s, key, parent),
            Value::UInt(n) => self.number(*n, key, parent),
            Value::Int(n) if *n > 0 => self.number(*n as u64, key, parent),
            _ => {}
        }
    }

    fn number(&mut self, n: u64, key: &str, parent: &str) {
        if key == "hostid" || (key == "value" && parent == "hostid") {
            if n != 0 {
                self.add(Ident::Dec(n, 8));
            }
        } else if (GUID_KEYS.contains(&key) || (key == "value" && GUID_KEYS.contains(&parent))) && n >= 10_000_000 {
            self.add(Ident::Dec(n, 16));
        }
    }

    fn string(&mut self, s: &str, key: &str, parent: &str) {
        if is_blob_key(key) {
            return;
        }
        if TYPE_KEYS.contains(&key) {
            for (a, b) in uuid_spans(s) {
                self.types.insert(s[a..b].to_ascii_lowercase());
            }
            return;
        }
        self.scan(s);
        let t = s.trim();
        if HOST_KEYS.contains(&key) && !t.is_empty() {
            self.add(Ident::Name(Kind::Hostname, t.to_string()));
            if let Some((short, _)) = t.split_once('.') {
                self.add(Ident::Name(Kind::Hostname, short.to_string()));
            }
        } else if key == "machine_id" {
            if t.len() == 32 && t.bytes().all(is_hex) {
                let b = hex_bytes(&t.to_ascii_lowercase());
                self.add(Ident::Uuid(b.try_into().unwrap(), Kind::MachineId));
            }
        } else if SERIAL_KEYS.contains(&key) && !t.is_empty() {
            self.add(Ident::Name(Kind::Serial, t.to_string()));
            // naa.5002538e..., eui.0025...: the hex also names wwn-0x... links.
            if let Some((_, h)) = t.split_once('.') {
                if h.len() >= 8 && h.bytes().all(is_hex) {
                    self.hex(h, Kind::Id);
                }
            }
        } else if LABEL_KEYS.contains(&key) && !t.is_empty() {
            // md names its arrays "host:N" after the host that made them.
            if let Some((h, n)) = t.rsplit_once(':') {
                if !h.is_empty() && !n.is_empty() && n.bytes().all(|c| c.is_ascii_digit()) && !h.contains(['/', ' ']) {
                    self.add(Ident::Name(Kind::Hostname, h.to_string()));
                }
            }
            self.add(Ident::Name(Kind::Label, s.to_string()));
        } else if ["key_file", "keyfile", "key", "keyfiles"].contains(&key) {
            self.key_path(t);
        } else if key == "keylocation" || (key == "value" && parent == "keylocation") {
            if let Some(p) = t.strip_prefix("file://") {
                self.key_path(p);
            }
        } else if HEX_KEYS.contains(&key) || (key == "value" && HEX_KEYS.contains(&parent)) {
            let h = t.trim_start_matches("0x");
            if h.len() >= 8 && h.bytes().all(|c| is_hex(c) || c == b'-') {
                self.hex(h, Kind::Id);
            }
        } else if GUID_KEYS.contains(&key) || (key == "value" && GUID_KEYS.contains(&parent)) {
            if let Ok(n) = t.parse::<u64>() {
                self.number(n, key, parent);
            }
        } else if key.ends_with("uuid") || key.ends_with("guid") || key == "uuid" {
            // What isn't canonical (scan has those): FAT's 3051-B739, NTFS's
            // 16 digits, ZFS's decimal pool GUID.  Not an MBR PARTUUID
            // (signature-NN): the signature itself is learned.
            if t.len() >= 8 && t.bytes().all(|c| c.is_ascii_digit()) {
                if let Ok(n) = t.parse::<u64>() {
                    self.add(Ident::Dec(n, 16));
                }
            } else if t.bytes().all(|c| is_hex(c) || c == b'-') && t.bytes().filter(|&c| is_hex(c)).count() >= 8 && uuid_spans(t).is_empty() && !is_mbr_partuuid(t) {
                self.hex(t, Kind::Id);
            }
        }
    }

    fn hex(&mut self, form: &str, kind: Kind) {
        let digits: String = form.chars().filter(char::is_ascii_hexdigit).collect::<String>().to_ascii_lowercase();
        if digits.len() >= 8 && digits.bytes().any(|c| c != b'0') {
            if !self.idents.iter().any(|i| matches!(i, Ident::Hex(d, _, _) if *d == digits)) {
                self.add(Ident::Hex(digits, form.to_string(), kind));
            }
        }
    }

    fn key_path(&mut self, t: &str) {
        // crypttab's "path:device" form; a device isn't a key file.
        let p = t.split(':').next().unwrap_or("");
        if p.starts_with('/') && !p.starts_with("/dev/") && p.len() > 1 && !p.ends_with('/') {
            self.add(Ident::Path(Kind::KeyFile, p.to_string()));
        }
    }

    /// Identifiers inside any text: UUIDs, LVM UUIDs, md UUIDs, MACs, and
    /// user names in home directories.
    fn scan(&mut self, s: &str) {
        if !s.bytes().any(|c| c == b'-' || c == b':' || c == b'/') {
            return;
        }
        for (a, b) in uuid_spans(s) {
            let u = s[a..b].to_ascii_lowercase();
            let bytes: Vec<u8> = hex_bytes(&u.replace('-', ""));
            self.add(Ident::Uuid(bytes.try_into().unwrap(), Kind::Uuid));
        }
        for (a, b) in md_spans(s) {
            let u = s[a..b].to_ascii_lowercase().replace(':', "");
            self.add(Ident::Uuid(hex_bytes(&u).try_into().unwrap(), Kind::Uuid));
        }
        for (a, b) in lvm_spans(s) {
            self.add(Ident::Lvm(s[a..b].replace('-', "")));
        }
        for (a, b) in mac_spans(s) {
            let d = s[a..b].replace(':', "").to_ascii_lowercase();
            if d.bytes().any(|c| c != b'0') && d != "ffffffffffff" {
                if !self.idents.iter().any(|i| matches!(i, Ident::Hex(x, _, _) if *x == d)) {
                    self.add(Ident::Hex(d, s[a..b].to_string(), Kind::Mac));
                }
            }
        }
        for ser in link_serials(s) {
            self.add(Ident::Name(Kind::Serial, ser));
        }
        for pre in ["/home/", "USERDATA/"] {
            let mut from = 0;
            while let Some(i) = s[from..].find(pre) {
                let st = from + i + pre.len();
                // A path that starts there, not one inside another
                // (DragonFly's kernel names its build's /usr/obj/home/USER/).
                let before = s[..from + i].chars().next_back();
                if pre == "/home/" && before.is_some_and(|c| c.is_alphanumeric() || "/_-.".contains(c)) {
                    from = st;
                    continue;
                }
                let rest = &s[st..];
                let end = rest.find(|c: char| "/\"' \t\n:,;)]}".contains(c) || (pre == "USERDATA/" && c == '_')).unwrap_or(rest.len());
                let u = &rest[..end];
                if user_like(u) && !GENERIC_USERS.contains(&u) {
                    self.add(Ident::Name(Kind::User, u.to_string()));
                }
                from = st;
            }
        }
    }

    /// The pseudonyms, and what's left as it is.
    pub fn seal(self) -> Redactor {
        Sealer::new(self).run()
    }
}

/// The serial numbers in /dev/disk/by-id links (udev's BUS-MODEL_SERIAL,
/// with spaces as '_') and FreeBSD's diskid/DISK-SERIAL: a device whose
/// serial is nowhere else in the dump (a CD drive, a USB stick) still has
/// it there.
fn link_serials(s: &str) -> Vec<String> {
    let mut out = Vec::new();
    for (pre, buses) in [("by-id/", &["ata-", "scsi-", "usb-", "nvme-", "mmc-", "ieee1394-", "memstick-", "virtio-", "cciss-"][..]), ("diskid/", &["DISK-"][..])] {
        let mut from = 0;
        while let Some(i) = s[from..].find(pre) {
            let st = from + i + pre.len();
            from = st;
            let rest = &s[st..];
            let name = &rest[..rest.find(|c: char| c == '"' || c == '/' || c.is_whitespace() || c == ',').unwrap_or(rest.len())];
            let Some(bus) = buses.iter().find(|b| name.starts_with(**b)) else { continue };
            let mut id = &name[bus.len()..];
            // -part3 (udev), -0:0 (a USB LUN), p3 / s1a (FreeBSD's slices).
            if let Some(p) = id.rfind("-part").filter(|&p| id[p + 5..].bytes().all(|c| c.is_ascii_digit()) && p + 5 < id.len()) {
                id = &id[..p];
            }
            if let Some(p) = id.rfind('-').filter(|&p| id[p + 1..].contains(':') && id[p + 1..].bytes().all(|c| c.is_ascii_digit() || c == b':')) {
                id = &id[..p];
            }
            let mut segs: Vec<&str> = id.split('_').collect();
            // nvme-MODEL_SERIAL_1: the namespace.
            if segs.len() > 2 && segs.last().is_some_and(|l| l.len() <= 2 && l.bytes().all(|c| c.is_ascii_digit())) {
                segs.pop();
            }
            let ser = if *bus == "DISK-" { strip_slice(id) } else if segs.len() >= 2 { segs.last().copied().unwrap_or("") } else { "" };
            if ser.len() >= 4 && !out.iter().any(|x| x == ser) {
                out.push(ser.to_string());
            }
        }
    }
    out
}

/// FreeBSD's partition suffix: p2, s1, s1a.
fn strip_slice(id: &str) -> &str {
    let b = id.as_bytes();
    let mut e = b.len();
    if e > 0 && (b'a'..=b'h').contains(&b[e - 1]) && e > 1 && b[e - 2].is_ascii_digit() {
        e -= 1;
    }
    let digits_end = e;
    while e > 0 && b[e - 1].is_ascii_digit() {
        e -= 1;
    }
    if e < digits_end && e > 1 && (b[e - 1] == b'p' || b[e - 1] == b's') {
        &id[..e - 1]
    } else {
        id
    }
}

fn is_mbr_partuuid(t: &str) -> bool {
    let b = t.as_bytes();
    b.len() == 11 && b[8] == b'-' && b[..8].iter().all(|&c| is_hex(c)) && b[9..].iter().all(|&c| is_hex(c))
}

pub fn is_blob_key(key: &str) -> bool {
    is_hex_blob_key(key) || is_b64_key(key)
}

fn is_hex_blob_key(key: &str) -> bool {
    key.ends_with("_hex") || key == "first_bytes"
}

pub fn is_b64_key(key: &str) -> bool {
    key == "b64" || key.ends_with("_b64")
}

struct Sealer {
    c: Collector,
    corpus_lc: String,
    r: Redactor,
    counters: HashMap<&'static str, u64>,
}

impl Sealer {
    fn new(c: Collector) -> Sealer {
        let corpus_lc = c.corpus.to_ascii_lowercase();
        Sealer { c, corpus_lc, r: Redactor::default(), counters: HashMap::new() }
    }

    /// The next free number for pseudonyms made by `f`: one that appears
    /// nowhere in the input, so `unredact` can't mistake input for a
    /// pseudonym.
    fn next(&mut self, tag: &'static str, f: impl Fn(u64) -> Vec<String>) -> Vec<String> {
        loop {
            let n = self.counters.entry(tag).or_insert(0);
            *n += 1;
            let forms = f(*n);
            if forms.iter().all(|x| x.len() < 3 || !self.corpus_lc.contains(&x.to_ascii_lowercase())) {
                return forms;
            }
        }
    }

    fn sub(&mut self, from: impl Into<String>, to: impl Into<String>, edge: Edge) {
        let (from, to) = (from.into(), to.into());
        if from.is_empty() || from == to || self.r.subs.iter().any(|s| s.from == from && s.to == to && s.edge == edge) {
            return;
        }
        self.r.subs.push(Sub { from, to, edge });
    }

    /// Only as the whole value of one of `kind`'s keys (a short label, a
    /// hostname that is also a word the dump uses).
    fn whole(&mut self, kind: Kind, from: &str, to: &str) {
        if !from.is_empty() && from != to && !self.r.whole_subs.iter().any(|(k, f, _)| *k == kind && f == from) {
            self.r.whole_subs.push((kind, from.to_string(), to.to_string()));
        }
    }

    fn word_sub(&self, from: &str) -> bool {
        self.r.subs.iter().any(|s| s.from == from && s.edge == Edge::Word)
    }

    fn bin(&mut self, from: Vec<u8>, to: Vec<u8>, short: bool) {
        if from != to && !self.r.bin.iter().any(|(f, _, _)| *f == from) {
            self.r.bin.push((from, to, short));
        }
    }

    fn ascii_bin(&mut self, from: &str, to: &str) {
        if from.len() == to.len() && from.len() >= 8 {
            self.bin(from.as_bytes().to_vec(), to.as_bytes().to_vec(), false);
            let u16 = |s: &str| s.bytes().flat_map(|c| [c, 0]).collect::<Vec<u8>>();
            self.bin(u16(from), u16(to), false);
        }
    }

    fn entry(&mut self, kind: Kind, original: impl Into<String>, pseudonym: impl Into<String>) {
        self.r.entries.push(Entry { kind, original: original.into(), pseudonym: pseudonym.into() });
    }

    fn kept(&mut self, kind: Kind, what: &str, why: &str) {
        if !self.r.kept.iter().any(|(k, w, _)| *k == kind && w == what) {
            self.r.kept.push((kind, what.to_string(), why.to_string()));
        }
    }

    fn run(mut self) -> Redactor {
        let idents = std::mem::take(&mut self.c.idents);
        // Hostnames first: a label or VG name that holds one (md's
        // "host:0", Rocky's "rl_host") is redacted through it.
        let mut hosts: Vec<String> = Vec::new();
        for id in &idents {
            if let Ident::Name(Kind::Hostname, h) = id {
                if self.generic_host(h) {
                    self.kept(Kind::Hostname, h, "an installer's or the system's default");
                } else if !hosts.contains(h) {
                    hosts.push(h.clone());
                }
            }
        }
        // Longest first, so "deb13.lan" is named before "deb13".
        hosts.sort_by_key(|h| std::cmp::Reverse(h.len()));
        for h in &hosts {
            self.name(Kind::Hostname, h, "host");
        }
        // Decimal GUIDs and hostids before hex ids: a hostid's hex form is
        // the same identifier.
        for id in &idents {
            if let Ident::Dec(n, w) = id {
                self.dec(*n, *w);
            }
        }
        for id in idents {
            match id {
                Ident::Name(Kind::Hostname, _) | Ident::Dec(..) => {}
                Ident::Uuid(b, kind) => self.uuid(b, kind),
                Ident::Lvm(s) => self.lvm(&s),
                Ident::Hex(d, form, kind) => self.hex(&d, &form, kind),
                Ident::Name(Kind::Label, l) => self.label(&l, &hosts),
                Ident::Name(Kind::User, u) => {
                    if self.c.os_words.contains(&u.to_ascii_lowercase()) {
                        self.kept(Kind::User, &u, "the system's own name");
                    } else {
                        self.name(Kind::User, &u, "user");
                    }
                }
                Ident::Name(Kind::Serial, s) => self.name(Kind::Serial, &s, "serial"),
                Ident::Name(k, s) => self.name(k, &s, "name"),
                Ident::Path(k, p) => self.path(k, &p),
            }
        }
        // Longest first; ties in a stable order.
        self.r.subs.sort_by(|a, b| b.from.len().cmp(&a.from.len()).then_with(|| a.from.cmp(&b.from)));
        self.r.bin.sort_by(|a, b| b.0.len().cmp(&a.0.len()));
        self.r.index();
        self.r
    }

    fn generic_host(&self, h: &str) -> bool {
        let l = h.to_ascii_lowercase();
        GENERIC_HOSTS.contains(&l.as_str()) || self.c.os_words.contains(&l) || l.starts_with("localhost")
    }

    fn label(&mut self, l: &str, hosts: &[String]) {
        let low = l.to_ascii_lowercase();
        let stem = low.trim_end_matches(|c: char| c.is_ascii_digit() || c == '*');
        // md's array name, "host:0": named through its host.
        let host_part = l.rsplit_once(':').filter(|(_, n)| n.bytes().all(|c| c.is_ascii_digit())).map(|(h, _)| h);
        if let Some(h) = host_part {
            if hosts.iter().any(|x| x == h) && self.word_sub(h) {
                return;
            }
            if self.generic_host(h) {
                self.kept(Kind::Label, l, "an md array named after a default hostname");
                return;
            }
        }
        // md's array name, "host:name" with a name that isn't a number
        // (Anaconda's localhost.localdomain:pv00): load gives md the two
        // parts apart (--homehost, --name), so the pseudonym keeps them
        // apart.  The name after the host is a label like any other.
        let parts = l.split_once(':').filter(|_| self.c.md_labels.contains(l)).filter(|(h, n)| {
            let host_like = h.bytes().next().is_some_and(|c| c.is_ascii_alphabetic()) && h.bytes().all(|c| c.is_ascii_alphanumeric() || matches!(c, b'.' | b'-'));
            let name_like = !n.is_empty() && n.bytes().all(|c| c.is_ascii_alphanumeric() || matches!(c, b'_' | b'.' | b'-'));
            host_like && name_like && !n.bytes().all(|c| c.is_ascii_digit()) && !hosts.iter().any(|x| contains_word(n, x))
        });
        if let Some((h, n)) = parts {
            if !self.generic_host(h) {
                self.name(Kind::Hostname, h, "host");
            }
            self.label(n, hosts);
            let pseudo = |r: &Redactor, k: Kind, o: &str| r.entries.iter().find(|e| e.kind == k && e.original == o).map_or(o.to_string(), |e| e.pseudonym.clone());
            let to = format!("{}:{}", pseudo(&self.r, Kind::Hostname, h), pseudo(&self.r, Kind::Label, n));
            self.whole(Kind::Label, l, &to);
            return;
        }
        if hosts.iter().any(|h| contains_word(l, h) && self.word_sub(h)) {
            return;
        }
        if GENERIC_LABELS.contains(&stem) || GENERIC_LABELS.contains(&low.as_str()) || self.c.os_words.contains(stem) || release_label(&low) {
            self.kept(Kind::Label, l, "a default name");
            return;
        }
        if self.c.pool_labels.contains(l) {
            self.kept(Kind::Label, l, "a ZFS pool's name, part of the layout");
            return;
        }
        self.name(Kind::Label, l, "label");
    }

    /// A name: hostname, label, user or serial.  As a word where it's long
    /// enough to be safe and isn't a word the dump uses for itself; else
    /// only as a whole value and in the places such names go (LABEL=,
    /// by-label/, /home/).
    fn name(&mut self, kind: Kind, orig: &str, tag: &'static str) {
        if self.r.entries.iter().any(|e| e.kind == kind && e.original == orig) {
            return;
        }
        if kind == Kind::Serial && orig.trim().len() < 4 {
            self.kept(kind, orig, "too short to identify anything");
            return;
        }
        let upper = !orig.bytes().any(|c| c.is_ascii_lowercase()) && orig.bytes().any(|c| c.is_ascii_uppercase());
        let dash = orig.contains('-');
        let p = self.next(tag, |n| {
            let base = if dash { format!("{tag}-{n}") } else { format!("{tag}{n}") };
            vec![if upper { base.to_ascii_uppercase() } else { base }]
        });
        let pseudo = p[0].clone();
        self.entry(kind, orig, &pseudo);
        let min = if kind == Kind::Hostname { 3 } else { 4 };
        let wordy = orig.len() >= min && !self.c.vocab.contains(orig) && !self.c.os_words.contains(&orig.to_ascii_lowercase());
        if wordy {
            self.sub(orig, &pseudo, Edge::Word);
        } else {
            self.whole(kind, orig, &pseudo);
        }
        if kind == Kind::Serial && wordy {
            let trimmed = orig.trim();
            self.sub(trimmed, &pseudo, Edge::Word);
            self.sub(trimmed, &pseudo, Edge::Left);
            if orig.contains(' ') {
                self.sub(orig.trim().replace(' ', "_"), pseudo.replace(' ', "_"), Edge::Word);
            }
        }
        if dash && wordy {
            // device-mapper doubles a name's dashes: vg-name--x becomes vg--name--x.
            self.sub(orig.replace('-', "--"), pseudo.replace('-', "--"), Edge::Word);
        }
        match kind {
            Kind::Label => {
                let esc = |s: &str| s.replace('\\', "\\x5c").replace(' ', "\\x20").replace('/', "\\x2f");
                for pre in ["LABEL=", "PARTLABEL=", "by-label/", "by-partlabel/", "label=", "partlabel="] {
                    let (f, t) = if pre.starts_with("by-") { (esc(orig), esc(&pseudo)) } else { (orig.to_string(), pseudo.clone()) };
                    self.sub(format!("{pre}{f}"), format!("{pre}{t}"), Edge::Word);
                    self.sub(format!("{pre}\"{f}\""), format!("{pre}\"{t}\""), Edge::Any);
                }
            }
            Kind::User => {
                self.sub(format!("/home/{orig}"), format!("/home/{pseudo}"), Edge::Word);
                self.sub(format!("USERDATA/{orig}_"), format!("USERDATA/{pseudo}_"), Edge::Any);
            }
            _ => {}
        }
    }

    fn path(&mut self, kind: Kind, orig: &str) {
        if self.r.entries.iter().any(|e| e.kind == kind && e.original == orig) {
            return;
        }
        if kind == Kind::KeyFile && orig.starts_with("/run/keystore/") {
            self.kept(kind, orig, "Ubuntu's ZFS keystore, the same on every install");
            return;
        }
        let pseudo = if kind == Kind::KeyFile {
            // The directory stays (it says where a key lives: in the
            // restored tree, or on a stick); the file's name goes, wherever
            // load puts the file.
            let (dir, base) = orig.rsplit_once('/').unwrap_or(("", orig));
            let ext = base.rsplit_once('.').map(|(_, e)| format!(".{e}")).filter(|e| e.len() <= 5).unwrap_or_default();
            let p = self.next("keyfile", |n| vec![format!("keyfile{n}{ext}")]);
            if base.len() >= 3 {
                self.sub(format!("/{base}"), format!("/{}", p[0]), Edge::Right);
            }
            format!("{dir}/{}", p[0])
        } else {
            self.next("secret", |n| vec![format!("REDACTED{n}")])[0].clone()
        };
        self.entry(kind, orig, &pseudo);
        self.sub(orig, &pseudo, Edge::Any);
    }

    fn uuid(&mut self, b: [u8; 16], kind: Kind) {
        let canon = crate::bytes::uuid(&b);
        if WELL_KNOWN.contains(&canon.as_str()) || self.c.types.contains(&canon) {
            return;
        }
        if self.r.entries.iter().any(|e| e.original == canon || e.original == crate::bytes::hex(&b)) {
            return;
        }
        let tag = if kind == Kind::MachineId { "mid" } else { "uuid" };
        let first = if kind == Kind::MachineId { 0xbd } else { 0xbc };
        let forms = self.next(tag, |n| {
            let mut p = [0u8; 16];
            p[0] = first;
            p[6] = 0x40;
            p[8] = 0x80;
            p[12..].copy_from_slice(&(n as u32).to_be_bytes());
            vec![crate::bytes::hex(&p), crate::bytes::uuid(&p)]
        });
        let p: Vec<u8> = hex_bytes(&forms[0]);
        let pc = crate::bytes::uuid(&p);
        let (oh, ph) = (crate::bytes::hex(&b), crate::bytes::hex(&p));
        self.entry(kind, if kind == Kind::MachineId { oh.clone() } else { canon.clone() }, if kind == Kind::MachineId { ph.clone() } else { pc.clone() });
        let colon = |h: &str| format!("{}:{}:{}:{}", &h[0..8], &h[8..16], &h[16..24], &h[24..32]);
        for (f, t) in [(canon.clone(), pc.clone()), (oh.clone(), ph.clone()), (colon(&oh), colon(&ph))] {
            self.sub(&f, &t, Edge::Any);
            self.sub(f.to_ascii_uppercase(), t.to_ascii_uppercase(), Edge::Any);
            self.ascii_bin(&f, &t);
            self.ascii_bin(&f.to_ascii_uppercase(), &t.to_ascii_uppercase());
        }
        // Raw: in on-disk order, and as EFI and GPT store GUIDs.
        self.bin(b.to_vec(), p.clone(), false);
        let mixed = |x: &[u8]| -> Vec<u8> {
            let mut m = x.to_vec();
            m[0..4].reverse();
            m[4..6].reverse();
            m[6..8].reverse();
            m
        };
        self.bin(mixed(&b), mixed(&p), false);
    }

    fn lvm(&mut self, s: &str) {
        if self.r.entries.iter().any(|e| e.original.replace('-', "") == s) {
            return;
        }
        let forms = self.next("lvm", |n| vec![format!("bkoLVM{n:026}")]);
        let p = forms[0].clone();
        let dashed = |x: &str| format!("{}-{}-{}-{}-{}-{}-{}", &x[0..6], &x[6..10], &x[10..14], &x[14..18], &x[18..22], &x[22..26], &x[26..32]);
        self.entry(Kind::Uuid, dashed(s), dashed(&p));
        self.sub(dashed(s), dashed(&p), Edge::Any);
        self.sub(s, &p, Edge::Any);
        self.ascii_bin(&dashed(s), &dashed(&p));
        self.ascii_bin(s, &p);
    }

    fn hex(&mut self, digits: &str, form: &str, kind: Kind) {
        if self.r.entries.iter().any(|e| e.original.to_ascii_lowercase().replace([':', '-'], "") == digits) || self.r.subs.iter().any(|s| s.from == digits) {
            return;
        }
        let l = digits.len();
        let pd = self.next("hex", |n| {
            let tail = format!("{n:x}");
            vec![format!("bc{}{tail}", "0".repeat(l.saturating_sub(2 + tail.len())))]
        })[0]
            .clone();
        // The separators where the first form had them.
        let shape = |d: &str| -> String {
            let mut out = String::new();
            let mut it = d.chars();
            for c in form.chars() {
                if c.is_ascii_hexdigit() {
                    out.push(it.next().unwrap_or('0'));
                } else {
                    out.push(c);
                }
            }
            out
        };
        let (of, pf) = (shape(digits), shape(&pd));
        let upper_first = form.bytes().any(|c| c.is_ascii_uppercase());
        self.entry(kind, if upper_first { of.to_ascii_uppercase() } else { of.clone() }, if upper_first { pf.to_ascii_uppercase() } else { pf.clone() });
        let mut pairs = vec![(digits.to_string(), pd.clone())];
        if of != digits {
            pairs.push((of.clone(), pf.clone()));
        }
        if kind == Kind::Mac {
            let sep = |d: &str, s: char| d.as_bytes().chunks(2).map(|c| String::from_utf8_lossy(c).into_owned()).collect::<Vec<_>>().join(&s.to_string());
            pairs.push((sep(digits, ':'), sep(&pd, ':')));
            pairs.push((sep(digits, '-'), sep(&pd, '-')));
        }
        for (f, t) in pairs {
            self.sub(&f, &t, Edge::Hex);
            self.sub(f.to_ascii_uppercase(), t.to_ascii_uppercase(), Edge::Hex);
            self.ascii_bin(&f, &t);
            self.ascii_bin(&f.to_ascii_uppercase(), &t.to_ascii_uppercase());
        }
        // Up to 8 bytes only in EFI load options: in boot code, a run of
        // zeros and one byte matches by chance.
        let (ob, pb) = (hex_bytes(digits), hex_bytes(&pd));
        let short = ob.len() <= 8;
        self.bin(ob.clone(), pb.clone(), short);
        if ob.len() == 4 || ob.len() == 8 {
            let rev = |x: &[u8]| x.iter().rev().copied().collect::<Vec<u8>>();
            self.bin(rev(&ob), rev(&pb), short);
        }
    }

    fn dec(&mut self, n: u64, w: usize) {
        if self.r.nums.iter().any(|(a, _)| *a == n) {
            return;
        }
        let digits = n.to_string();
        let l = digits.len();
        let p = if l >= 2 {
            let base = 10u64.pow((l - 1) as u32);
            let forms = self.next("dec", |k| vec![(base + k).to_string(), format!("{:0w$x}", base + k)]);
            forms[0].parse::<u64>().unwrap()
        } else {
            n
        };
        if p == n {
            return;
        }
        self.r.nums.push((n, p));
        self.entry(Kind::Id, &digits, p.to_string());
        if l >= 6 {
            self.sub(&digits, p.to_string(), Edge::Word);
        }
        let (oh, ph) = (format!("{n:0w$x}"), format!("{p:0w$x}"));
        if oh.len() >= 8 {
            self.sub(&oh, &ph, Edge::Hex);
            self.sub(oh.to_ascii_uppercase(), ph.to_ascii_uppercase(), Edge::Hex);
            self.ascii_bin(&oh, &ph);
        }
        self.bin(n.to_le_bytes().to_vec(), p.to_le_bytes().to_vec(), true);
    }
}

fn contains_word(hay: &str, w: &str) -> bool {
    let (h, n) = (hay.as_bytes(), w.as_bytes());
    if n.is_empty() || n.len() > h.len() {
        return false;
    }
    (0..=h.len() - n.len()).any(|i| &h[i..i + n.len()] == n && (i == 0 || !is_alnum(h[i - 1])) && h.get(i + n.len()).is_none_or(|&c| !is_alnum(c)))
}

/// Rewrites JSON and text with the pseudonyms a `Collector` chose.
#[derive(Default, Debug)]
pub struct Redactor {
    subs: Vec<Sub>,
    /// (from, to, short): byte patterns for decoded boot code and EFI load
    /// options; short ones (8 bytes or fewer) only in EFI load options.
    bin: Vec<(Vec<u8>, Vec<u8>, bool)>,
    nums: Vec<(u64, u64)>,
    pub entries: Vec<Entry>,
    /// (kind, value, why it was kept)
    pub kept: Vec<(Kind, String, String)>,
    whole_subs: Vec<(Kind, String, String)>,
    by_first: HashMap<u8, Vec<usize>>,
    bin_first: HashMap<u8, Vec<usize>>,
    whole: HashMap<String, Vec<(Kind, String)>>,
    back: Vec<(String, String)>,
    back_first: HashMap<u8, Vec<usize>>,
}

impl Redactor {
    fn index(&mut self) {
        self.by_first.clear();
        self.whole.clear();
        for (i, s) in self.subs.iter().enumerate() {
            self.by_first.entry(s.from.as_bytes()[0]).or_default().push(i);
        }
        self.bin_first.clear();
        for (i, (f, _, _)) in self.bin.iter().enumerate() {
            self.bin_first.entry(f[0]).or_default().push(i);
        }
        for (k, f, t) in &self.whole_subs {
            self.whole.entry(f.clone()).or_default().push((*k, t.clone()));
        }
        let mut back: BTreeMap<String, String> = BTreeMap::new();
        for s in &self.subs {
            back.entry(s.to.clone()).or_insert_with(|| s.from.clone());
        }
        for (_, f, t) in &self.whole_subs {
            back.entry(t.clone()).or_insert_with(|| f.clone());
        }
        self.back = back.into_iter().collect();
        self.back.sort_by(|a, b| b.0.len().cmp(&a.0.len()).then_with(|| a.0.cmp(&b.0)));
        self.back_first.clear();
        for (i, (to, _)) in self.back.iter().enumerate() {
            self.back_first.entry(to.as_bytes()[0]).or_default().push(i);
        }
    }

    /// Whether there was anything to redact.
    pub fn is_empty(&self) -> bool {
        self.entries.is_empty()
    }

    /// `s` with every identifier replaced.
    pub fn text(&self, s: &str) -> String {
        self.text_with(s, true)
    }

    /// An object key (the topology's are text; EFI variables' hold GUIDs).
    /// Names that are words the dump uses as keys ("data") have no word
    /// substitution, so "data_offset" stays.
    fn key(&self, s: &str) -> String {
        self.text_with(s, true)
    }

    fn text_with(&self, s: &str, words: bool) -> String {
        let b = s.as_bytes();
        let mut out: Vec<u8> = Vec::with_capacity(b.len());
        let mut i = 0;
        'outer: while i < b.len() {
            if let Some(cands) = self.by_first.get(&b[i]) {
                for &k in cands {
                    let sub = &self.subs[k];
                    let f = sub.from.as_bytes();
                    if b[i..].starts_with(f) && (words || sub.edge != Edge::Word) {
                        if sub.edge.fits(b, i, f.len()) {
                            out.extend_from_slice(sub.to.as_bytes());
                            i += f.len();
                            continue 'outer;
                        }
                    }
                }
            }
            out.push(b[i]);
            i += 1;
        }
        String::from_utf8(out).unwrap_or_else(|e| String::from_utf8_lossy(e.as_bytes()).into_owned())
    }

    /// Pseudonyms back to the originals: for checking, never for the bundle.
    pub fn unredact(&self, s: &str) -> String {
        let b = s.as_bytes();
        let mut out: Vec<u8> = Vec::with_capacity(b.len());
        let mut i = 0;
        'outer: while i < b.len() {
            if let Some(cands) = self.back_first.get(&b[i]) {
                for &k in cands {
                    let (to, from) = &self.back[k];
                    if b[i..].starts_with(to.as_bytes()) {
                        out.extend_from_slice(from.as_bytes());
                        i += to.len();
                        continue 'outer;
                    }
                }
            }
            out.push(b[i]);
            i += 1;
        }
        String::from_utf8_lossy(&out).into_owned()
    }

    /// A redacted copy of a dump (or any JSON).  A dump's `os` section
    /// (os-release) stays as it is: it identifies no one, and `load` checks
    /// it.
    pub fn value(&self, v: &Value) -> Value {
        match v {
            Value::Obj(kv) => Value::Obj(kv.iter().map(|(k, x)| (self.key(k), if k == "os" { x.clone() } else { self.val(x, k) })).collect()),
            _ => self.val(v, ""),
        }
    }

    /// `value` undone: for checking that nothing but identifiers changed.
    pub fn unredact_value(&self, v: &Value) -> Value {
        match v {
            Value::Obj(kv) => Value::Obj(kv.iter().map(|(k, x)| (self.unredact(k), if k == "os" { x.clone() } else { self.unval(x, k) })).collect()),
            _ => self.unval(v, ""),
        }
    }

    fn unval(&self, v: &Value, key: &str) -> Value {
        match v {
            Value::Str(s) if is_hex_blob_key(key) && s.len() % 2 == 0 && s.bytes().all(is_hex) => {
                let upper = s.bytes().any(|c| c.is_ascii_uppercase());
                let out = crate::bytes::hex(&self.unbytes(&hex_bytes(&s.to_ascii_lowercase()), true));
                Value::Str(if upper { out.to_ascii_uppercase() } else { out })
            }
            Value::Str(s) if is_b64_key(key) => match crate::bytes::unbase64(s) {
                Some(b) if crate::bytes::base64(&b) == *s => Value::Str(crate::bytes::base64(&self.unbytes(&b, false))),
                _ => Value::Str(s.clone()),
            },
            Value::Str(s) => Value::Str(self.unredact(s)),
            Value::UInt(n) => Value::UInt(self.nums.iter().find(|(_, p)| p == n).map(|(o, _)| *o).unwrap_or(*n)),
            Value::Int(n) if *n > 0 => Value::Int(self.nums.iter().find(|(_, p)| *p == *n as u64).map(|(o, _)| *o as i64).unwrap_or(*n)),
            Value::Arr(a) => Value::Arr(a.iter().map(|x| self.unval(x, key)).collect()),
            Value::Obj(kv) => Value::Obj(kv.iter().map(|(k, x)| (self.unredact(k), self.unval(x, k))).collect()),
            other => other.clone(),
        }
    }

    fn unbytes(&self, b: &[u8], short: bool) -> Vec<u8> {
        let mut out = b.to_vec();
        let mut i = 0;
        'outer: while i < out.len() {
            for (f, t, s) in &self.bin {
                if (!*s || short) && out[i..].starts_with(t) {
                    out[i..i + t.len()].copy_from_slice(f);
                    i += t.len();
                    continue 'outer;
                }
            }
            i += 1;
        }
        if !short {
            self.fat_serials(&mut out, false);
        }
        out
    }

    fn val(&self, v: &Value, key: &str) -> Value {
        match v {
            Value::Str(s) if is_hex_blob_key(key) && s.len() % 2 == 0 && s.bytes().all(is_hex) => {
                let upper = s.bytes().any(|c| c.is_ascii_uppercase());
                let out = crate::bytes::hex(&self.bytes(&hex_bytes(&s.to_ascii_lowercase()), true));
                Value::Str(if upper { out.to_ascii_uppercase() } else { out })
            }
            Value::Str(s) if is_b64_key(key) => match crate::bytes::unbase64(s) {
                Some(b) if crate::bytes::base64(&b) == *s => Value::Str(crate::bytes::base64(&self.bytes(&b, false))),
                _ => Value::Str(s.clone()),
            },
            Value::Str(s) => match self.whole.get(s).and_then(|ts| ts.iter().find(|(k, _)| kind_key(*k, key))) {
                Some((_, t)) => Value::Str(t.clone()),
                None => Value::Str(self.text(s)),
            },
            Value::UInt(n) => Value::UInt(self.num(*n)),
            Value::Int(n) if *n > 0 => Value::Int(self.num(*n as u64) as i64),
            Value::Arr(a) => Value::Arr(a.iter().map(|x| self.val(x, key)).collect()),
            Value::Obj(kv) => Value::Obj(kv.iter().map(|(k, x)| (self.key(k), self.val(x, k))).collect()),
            other => other.clone(),
        }
    }

    fn num(&self, n: u64) -> u64 {
        self.nums.iter().find(|(a, _)| *a == n).map(|(_, p)| *p).filter(|p| *p <= i64::MAX as u64 || n > i64::MAX as u64).unwrap_or(n)
    }

    /// Identifiers in binary: EFI load options (`short`: their 4-byte MBR
    /// signatures and 6-byte MACs too) and boot code.  Same lengths, so
    /// every offset stays.
    fn bytes(&self, b: &[u8], short: bool) -> Vec<u8> {
        let mut out = b.to_vec();
        let mut i = 0;
        'outer: while i < out.len() {
            for &k in self.bin_first.get(&out[i]).map(Vec::as_slice).unwrap_or(&[]) {
                let (f, t, s) = &self.bin[k];
                if (!*s || short) && out[i..].starts_with(f) {
                    out[i..i + f.len()].copy_from_slice(t);
                    i += f.len();
                    continue 'outer;
                }
            }
            i += 1;
        }
        if !short {
            self.fat_serials(&mut out, true);
        }
        out
    }

    /// A FAT boot sector's 4-byte volume serial (an EFI system partition's
    /// "UUID", 122A-10FD), in its place only: at 39 (FAT12, FAT16) or 67
    /// (FAT32) of a sector that is one, which an image of an ESP's sectors
    /// holds twice (the boot sector and its backup).  Elsewhere a 4-byte
    /// run matches by chance, as `bytes` says.  `redacting`: original to
    /// pseudonym, else back (`unbytes`, for the proof in check_plan).
    fn fat_serials(&self, out: &mut [u8], redacting: bool) {
        for i in (0..out.len().saturating_sub(511)).step_by(512) {
            let s = &out[i..i + 512];
            if s[510..] != [0x55, 0xAA] || !matches!(s[0], 0xEB | 0xE9) {
                continue;
            }
            let at = if &s[82..87] == b"FAT32" {
                67
            } else if &s[54..57] == b"FAT" {
                39
            } else {
                continue;
            };
            let id = out[i + at..i + at + 4].to_vec();
            let found = self.bin.iter().find(|(f, t, short)| *short && if redacting { *f == id } else { *t == id });
            if let Some((f, t, _)) = found {
                out[i + at..i + at + 4].copy_from_slice(if redacting { t } else { f });
            }
        }
    }

    /// Originals still in `text` (a check on the redaction itself).
    pub fn leaks(&self, text: &str) -> Vec<String> {
        let mut out = Vec::new();
        for s in &self.subs {
            if s.from.len() < 6 {
                continue;
            }
            let b = text.as_bytes();
            let found = text.match_indices(s.from.as_str()).any(|(i, _)| s.edge.fits(b, i, s.from.len()));
            if found && !out.contains(&s.from) {
                out.push(s.from.clone());
            }
        }
        out
    }

    /// What was redacted, for the terminal: originals only for the kinds
    /// a person would recognise (never UUIDs: there are too many).
    pub fn summary(&self, originals: bool) -> String {
        let mut by: BTreeMap<Kind, Vec<&Entry>> = BTreeMap::new();
        for e in &self.entries {
            by.entry(e.kind).or_default().push(e);
        }
        let mut s = String::new();
        if by.is_empty() {
            s += "Redacted: nothing (no identifiers found)\n";
        } else {
            s += "Redacted (each replaced by the same pseudonym everywhere in the file):\n";
            for (k, es) in &by {
                s += &format!("  {} {}", es.len(), k.plural());
                if originals && k.shown() {
                    let list: Vec<String> = es.iter().take(8).map(|e| format!("{} -> {}", e.original, e.pseudonym)).collect();
                    s += &format!(": {}{}", list.join(", "), if es.len() > 8 { ", ..." } else { "" });
                }
                s.push('\n');
            }
        }
        s += "Kept as they are: sizes, offsets, sector sizes, feature flags, partition and\n";
        s += "  filesystem types, device names (sda1, nvme0n1p2), the device topology, disk\n";
        s += "  models, pool, VG and LV names, mount points, package versions";
        let mut kept: BTreeMap<Kind, Vec<String>> = BTreeMap::new();
        for (k, w, why) in &self.kept {
            kept.entry(*k).or_default().push(format!("{w:?} ({why})"));
        }
        for (k, ws) in &kept {
            s += &format!(";\n  {}: {}", k.plural(), ws.join(", "));
        }
        s += ";\n  boot code (GRUB's core.img and the like) apart from identifiers found in it\n";
        s += "  as plain bytes: compressed parts of it may still hold a filesystem UUID\n";
        s
    }
}

/// Whether a whole-value pseudonym of `kind` applies under `key`.
fn kind_key(kind: Kind, key: &str) -> bool {
    match kind {
        Kind::Hostname => HOST_KEYS.contains(&key),
        Kind::Serial => SERIAL_KEYS.contains(&key),
        Kind::Label => LABEL_KEYS.contains(&key),
        _ => false,
    }
}

/// Redacts one dump on its own.
pub fn redact(v: &Value) -> (Value, Redactor) {
    let mut c = Collector::new();
    c.learn(v);
    let r = c.seal();
    (r.value(v), r)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::json::parse;

    fn dump() -> Value {
        parse(
            r#"{
  "format": "bko-holt", "schema": 1,
  "host": {"hostname": "alice-laptop", "machine_id": "0427e0213c4d4e42a9c7f7cd7d18d98e"},
  "os": {"id": "debian", "version_id": "13", "name": "Debian GNU/Linux"},
  "storage": {
    "disks": [{"name": "sda", "size_bytes": 512110190592, "model": "Samsung SSD 870 EVO 500GB", "serial": "S62ANJ0R123456X",
      "wwid": "naa.5002538e40a1b2c3",
      "links": ["/dev/disk/by-id/ata-Samsung_SSD_870_EVO_500GB_S62ANJ0R123456X", "/dev/disk/by-id/wwn-0x5002538e40a1b2c3", "/dev/diskid/DISK-S62ANJ0R123456Xp1"],
      "partition_table": {"type": "gpt", "disk_guid": "DAAF2A80-57D1-4C56-92D5-90B0634A0471"},
      "partitions": [
        {"name": "sda1", "type": "c12a7328-f81f-11d2-ba4b-00a0c93ec93b", "uuid": "56ae978f-0d70-49eb-9dd1-19b174f48d8b", "partlabel": "EFI System Partition",
         "content": {"type": "vfat", "uuid": "3051-B739", "label": "EFI"},
         "links": ["/dev/disk/by-uuid/3051-B739", "/dev/disk/by-partuuid/56ae978f-0d70-49eb-9dd1-19b174f48d8b"]},
        {"name": "sda2", "type": "0fc63daf-8483-4772-8e79-3d69d8477de4", "uuid": "aaaaaaaa-0000-0000-0000-000000000002", "partlabel": "photos",
         "content": {"type": "crypto_LUKS", "uuid": "6c3e5f0e-6b0a-4d6c-9a2e-8f7e1d2c3b4a", "label": "Photos"}}
      ]}],
    "dm_devices": [{"name": "dm-0", "dm_name": "luks-6c3e5f0e-6b0a-4d6c-9a2e-8f7e1d2c3b4a", "dm_uuid": "CRYPT-LUKS2-6c3e5f0e6b0a4d6c9a2e8f7e1d2c3b4a-luks-6c3e5f0e-6b0a-4d6c-9a2e-8f7e1d2c3b4a"},
                   {"name": "dm-1", "dm_name": "alice--laptop--vg-root", "dm_uuid": "LVM-Abc123DEFg4h5IJ6kl7MN8op9QR0st1uVw2XYz3abC4dE5fG6hI7jK8lM9nO0pQ1r"}],
    "lvm": {"volume_groups": [{"name": "alice-laptop-vg", "uuid": "Abc123-DEFg-4h5I-J6kl-7MN8-op9Q-R0st1u", "creation_host": "alice-laptop"}]},
    "md_arrays": [{"name": "md0", "uuid": "75f651e9:d640a5a9:c9fdc179:785e99b1", "label": "alice-laptop:0"}],
    "zfs": {"pools": [{"name": "tank", "pool_guid": 15105800815207446011, "hostid": 545037285, "hostname": "alice-laptop",
                       "datasets": [{"name": "tank/USERDATA/alice_k3j4l2", "mountpoint": "/home/alice"}]}]},
    "fstab": [{"spec": "UUID=3051-B739", "file": "/boot/efi"}, {"spec": "LABEL=Photos", "file": "/home/alice/Photos"},
              {"spec": "/dev/mapper/alice--laptop--vg-root", "file": "/"}],
    "crypttab": [{"name": "luks-6c3e5f0e-6b0a-4d6c-9a2e-8f7e1d2c3b4a", "device": "UUID=6c3e5f0e-6b0a-4d6c-9a2e-8f7e1d2c3b4a", "key_file": "/etc/keys/alice.key:LABEL=KEYS"}]
  },
  "boot": {"grub": {"search_uuids": ["d1a2b3c4d5e6f708"]},
           "efi": {"entries": [{"description": "UEFI PXEv4 (MAC:525400123456)", "load_option_hex": "01000000aabb8f97ae56700deb499dd119b174f48d8b5254001234560000"}]},
           "cmdline": "BOOT_IMAGE=/vmlinuz root=/dev/mapper/alice--laptop--vg-root ro quiet"}
}"#,
        )
        .unwrap()
    }

    fn text(v: &Value) -> String {
        v.pretty()
    }

    #[test]
    fn identifiers_go_everywhere_they_appear() {
        let d = dump();
        let (r, red) = redact(&d);
        let t = text(&r);
        for gone in [
            "alice", "0427e0213c4d4e42a9c7f7cd7d18d98e", "S62ANJ0R123456X", "5002538e40a1b2c3", "daaf2a80", "DAAF2A80", "56ae978f", "3051-B739",
            "6c3e5f0e", "Abc123", "75f651e9", "15105800815207446011", "545037285", "Photos", "photos", "525400123456", "MAC:5254",
        ] {
            assert!(!t.contains(gone), "{gone} is still in:\n{t}");
        }
        assert!(red.leaks(&t).is_empty(), "{:?}", red.leaks(&t));
        let s = |p: &[&str]| r.at(p).and_then(Value::as_str).unwrap().to_string();
        // The same pseudonym in every form.
        let host = s(&["host", "hostname"]);
        assert_eq!(host, "host-1");
        assert_eq!(s(&["storage", "lvm", "volume_groups", "0", "name"]), "host-1-vg");
        assert_eq!(s(&["storage", "dm_devices", "1", "dm_name"]), "host--1--vg-root");
        assert_eq!(s(&["storage", "fstab", "2", "spec"]), "/dev/mapper/host--1--vg-root");
        assert_eq!(s(&["storage", "md_arrays", "0", "label"]), "host-1:0");
        assert_eq!(s(&["storage", "lvm", "volume_groups", "0", "creation_host"]), host);
        let fat = s(&["storage", "disks", "0", "partitions", "0", "content", "uuid"]);
        assert!(fat.starts_with("BC00-000"), "{fat}");
        assert_eq!(s(&["storage", "fstab", "0", "spec"]), format!("UUID={fat}"));
        assert_eq!(s(&["storage", "disks", "0", "partitions", "0", "links", "0"]), format!("/dev/disk/by-uuid/{fat}"));
        let luks = s(&["storage", "disks", "0", "partitions", "1", "content", "uuid"]);
        assert!(luks.starts_with("bc000000-0000-4000-8000-"), "{luks}");
        assert_eq!(s(&["storage", "crypttab", "0", "name"]), format!("luks-{luks}"));
        assert_eq!(s(&["storage", "dm_devices", "0", "dm_uuid"]), format!("CRYPT-LUKS2-{}-luks-{luks}", luks.replace('-', "")));
        let vg = s(&["storage", "lvm", "volume_groups", "0", "uuid"]);
        assert!(s(&["storage", "dm_devices", "1", "dm_uuid"]).starts_with(&format!("LVM-{}", vg.replace('-', ""))));
        // GPT's disk GUID keeps its case.
        assert!(s(&["storage", "disks", "0", "partition_table", "disk_guid"]).starts_with("BC000000-0000-4000-8000-"));
        // Labels: as values, in LABEL=, and in key-file specs; user names in
        // /home and in Ubuntu's USERDATA datasets.
        let photos = s(&["storage", "disks", "0", "partitions", "1", "content", "label"]);
        assert!(photos.starts_with("label"), "{photos}");
        assert_ne!(photos, s(&["storage", "disks", "0", "partitions", "1", "partlabel"]));
        assert_eq!(s(&["storage", "fstab", "1", "spec"]), format!("LABEL={photos}"));
        assert_eq!(s(&["storage", "fstab", "1", "file"]), format!("/home/user1/{photos}"));
        assert_eq!(s(&["storage", "zfs", "pools", "0", "datasets", "0", "name"]), "tank/USERDATA/user1_k3j4l2");
        assert_eq!(s(&["storage", "crypttab", "0", "key_file"]), "/etc/keys/keyfile1.key:LABEL=KEYS");
        // ZFS's GUID as a number, and as GRUB's hex.
        let g = r.at(&["storage", "zfs", "pools", "0", "pool_guid"]).and_then(Value::as_u64).unwrap();
        assert_eq!(g.to_string().len(), "15105800815207446011".len());
        assert_ne!(g, 15105800815207446011);
        assert!(r.at(&["storage", "zfs", "pools", "0", "hostid"]).and_then(Value::as_u64).unwrap() != 545037285);
        // MBR-style ids stay inside load options; the PXE entry's MAC goes.
        let lo = s(&["boot", "efi", "entries", "0", "load_option_hex"]);
        assert!(!lo.contains("8f97ae56700deb499dd119b174f48d8b"), "{lo}");
        assert!(!lo.contains("525400123456"), "{lo}");
        assert_eq!(lo.len(), 60);
        assert!(s(&["boot", "efi", "entries", "0", "description"]).contains("(MAC:bc0000000"), "{t}");
        assert_eq!(s(&["boot", "cmdline"]), "BOOT_IMAGE=/vmlinuz root=/dev/mapper/host--1--vg-root ro quiet");
        let ser = s(&["storage", "disks", "0", "serial"]);
        assert_eq!(s(&["storage", "disks", "0", "links", "2"]), format!("/dev/diskid/DISK-{ser}p1"));
        assert_eq!(s(&["storage", "disks", "0", "links", "0"]), format!("/dev/disk/by-id/ata-Samsung_SSD_870_EVO_500GB_{ser}"));
        // A CD drive's serial is only in its by-id link.
        let cd = parse(r#"{"os": {"id": "debian"}, "other_devices": [{"name": "sr0", "serial": null, "links": ["/dev/disk/by-id/ata-QEMU_DVD-ROM_QM00003"]}]}"#).unwrap();
        let (r, _) = redact(&cd);
        assert_eq!(r.at(&["other_devices", "0", "links", "0"]).and_then(Value::as_str), Some("/dev/disk/by-id/ata-QEMU_DVD-ROM_SERIAL1"));
    }

    #[test]
    fn kept_what_identifies_no_one() {
        let (r, red) = redact(&dump());
        let s = |p: &[&str]| r.at(p).and_then(Value::as_str).unwrap().to_string();
        // Types, sizes, models, device names, generic labels, pool names.
        assert_eq!(s(&["storage", "disks", "0", "partitions", "0", "type"]), "c12a7328-f81f-11d2-ba4b-00a0c93ec93b");
        assert_eq!(s(&["storage", "disks", "0", "partitions", "1", "type"]), "0fc63daf-8483-4772-8e79-3d69d8477de4");
        assert_eq!(r.at(&["storage", "disks", "0", "size_bytes"]).and_then(Value::as_u64), Some(512110190592));
        assert_eq!(s(&["storage", "disks", "0", "model"]), "Samsung SSD 870 EVO 500GB");
        assert_eq!(s(&["storage", "disks", "0", "partitions", "0", "partlabel"]), "EFI System Partition");
        assert_eq!(s(&["storage", "disks", "0", "partitions", "0", "content", "label"]), "EFI");
        assert_eq!(s(&["storage", "zfs", "pools", "0", "name"]), "tank");
        assert_eq!(s(&["os", "id"]), "debian");
        assert_eq!(s(&["storage", "fstab", "0", "file"]), "/boot/efi");
        // Same keys, same shape.
        assert_eq!(keys(&r), keys(&dump()));
        let sum = red.summary(true);
        assert!(!red.summary(false).contains("alice"));
        assert!(sum.contains("alice-laptop -> host-1"), "{sum}");
        assert!(sum.contains("\"EFI\" (a default name)"), "{sum}");
        assert!(!sum.contains("0427e0213c4d4e42"), "{sum}");
    }

    fn keys(v: &Value) -> Vec<String> {
        let mut out = Vec::new();
        fn go(v: &Value, p: &str, out: &mut Vec<String>) {
            match v {
                Value::Obj(kv) => kv.iter().for_each(|(k, x)| {
                    let q = format!("{p}/{k}");
                    out.push(q.clone());
                    go(x, &q, out)
                }),
                Value::Arr(a) => a.iter().enumerate().for_each(|(i, x)| go(x, &format!("{p}/{i}"), out)),
                _ => {}
            }
        }
        go(v, "", &mut out);
        out
    }

    #[test]
    fn unredact_inverts_text() {
        let d = dump();
        let (r, red) = redact(&d);
        assert_eq!(red.unredact_value(&r).pretty(), d.pretty());
        let line = "mkfs.ext4 -L Photos -U 6c3e5f0e-6b0a-4d6c-9a2e-8f7e1d2c3b4a /dev/mapper/alice--laptop--vg-root  # alice-laptop";
        assert_eq!(red.unredact(&red.text(line)), line);
        assert!(!red.text(line).contains("alice"));
    }

    #[test]
    fn stable_and_consistent() {
        let a = redact(&dump()).0.pretty();
        let b = redact(&dump()).0.pretty();
        assert_eq!(a, b);
        // Several dumps through one Collector share the pseudonyms.
        let mut c = Collector::new();
        c.learn(&dump());
        let other = parse(r#"{"host": {"hostname": "bob-desktop"}, "os": {"id": "debian"}, "x": "alice-laptop and bob-desktop"}"#).unwrap();
        c.learn(&other);
        let r = c.seal();
        assert_eq!(r.value(&other).get("x").and_then(Value::as_str), Some("host-1 and host-2"));
        assert_eq!(r.value(&dump()).at(&["host", "hostname"]).and_then(Value::as_str), Some("host-1"));
    }

    #[test]
    fn defaults_and_words_are_left_alone() {
        let d = parse(
            r#"{"host": {"hostname": "debian"}, "os": {"id": "debian"},
                "storage": {"disks": [{"name": "vda", "serial": "1", "partitions": [{"partlabel": "swap0"}, {"partlabel": "gptboot0"}],
                            "content": {"type": "zfs_member", "label": "tank"}}],
                            "md_arrays": [{"label": "ubuntu-server:0"}],
                            "mounts": [{"target": "/home/root", "data_offset": 1}]},
                "boot": {"loader": "debian", "path": "/EFI/debian/shimx64.efi"}}"#,
        )
        .unwrap();
        let (r, red) = redact(&d);
        assert_eq!(r.pretty(), d.pretty(), "{}", red.summary(true));
        assert!(red.entries.is_empty(), "{:?}", red.entries);
        assert!(red.kept.iter().any(|(k, w, _)| *k == Kind::Serial && w == "1"));
        // A hostname that is also a word the dump uses (a key part) is
        // replaced only where it's a hostname's whole value.
        let d = parse(r#"{"host": {"hostname": "data"}, "os": {"id": "debian"}, "x": {"data_offset": "data"}, "y": "/srv/data"}"#).unwrap();
        let (r, red) = redact(&d);
        assert_eq!(r.at(&["host", "hostname"]).and_then(Value::as_str), Some("host1"));
        assert_eq!(r.at(&["x", "data_offset"]).and_then(Value::as_str), Some("data"));
        assert_eq!(r.get("y").and_then(Value::as_str), Some("/srv/data"));
        assert_eq!(red.unredact("host1"), "data");
        // A disk signature of decimal digits stays out of longer numbers.
        let d = parse(r#"{"os": {"id": "debian"}, "t": {"disk_signature": "12345678"}, "p": "start=112345678901 0x12345678 12345678-01"}"#).unwrap();
        let (r, _) = redact(&d);
        assert_eq!(r.get("p").and_then(Value::as_str), Some("start=112345678901 0xbc000001 bc000001-01"));
    }

    #[test]
    fn boot_code_keeps_its_length() {
        let guid = [0x8fu8, 0x97, 0xae, 0x56, 0x70, 0x0d, 0xeb, 0x49, 0x9d, 0xd1, 0x19, 0xb1, 0x74, 0xf4, 0x8d, 0x8b];
        let mut img = vec![0xeb, 0x63, 0x90];
        img.extend_from_slice(&guid);
        img.extend_from_slice(b"search --fs-uuid 56ae978f-0d70-49eb-9dd1-19b174f48d8b");
        let d = parse(&format!(
            r#"{{"os": {{"id": "debian"}}, "p": {{"uuid": "56ae978f-0d70-49eb-9dd1-19b174f48d8b", "boot_code": {{"image_b64": "{}"}}}}}}"#,
            crate::bytes::base64(&img)
        ))
        .unwrap();
        let (r, _) = redact(&d);
        let out = crate::bytes::unbase64(r.at(&["p", "boot_code", "image_b64"]).and_then(Value::as_str).unwrap()).unwrap();
        assert_eq!(out.len(), img.len());
        assert_eq!(&out[..3], &img[..3]);
        assert!(!crate::bytes::contains(&out, &guid));
        assert!(!crate::bytes::contains(&out, b"56ae978f"));
        assert!(crate::bytes::contains(&out, b"search --fs-uuid bc000000-0000-4000-8000-000000000001"), "{:?}", String::from_utf8_lossy(&out));
    }

    #[test]
    fn spans() {
        assert_eq!(uuid_spans("UUID=56ae978f-0d70-49eb-9dd1-19b174f48d8b,x"), vec![(5, 41)]);
        assert!(uuid_spans("x56ae978f-0d70-49eb-9dd1-19b174f48d8b").is_empty());
        assert_eq!(md_spans("md-uuid-75f651e9:d640a5a9:c9fdc179:785e99b1").len(), 1);
        assert_eq!(lvm_spans("Abc123-DEFg-4h5I-J6kl-7MN8-op9Q-R0st1u").len(), 1);
        assert!(lvm_spans("abcdef-abcd-abcd-abcd-abcd-abcd-abcdef").is_empty());
        assert_eq!(mac_spans("52:54:00:12:34:56").len(), 1);
        assert!(mac_spans("75f651e9:d640a5a9").is_empty());
        assert!(is_mbr_partuuid("02290173-01"));
        assert_eq!(link_serials("/dev/disk/by-id/ata-QEMU_DVD-ROM_QM00003"), vec!["QM00003"]);
        assert_eq!(link_serials("/dev/disk/by-id/nvme-Samsung_SSD_970_EVO_Plus_1TB_S4EWNX0N123456_1-part2"), vec!["S4EWNX0N123456"]);
        assert_eq!(link_serials("/dev/disk/by-id/usb-Kingston_DataTraveler_3.0_E0D55EA5741C1661A9A1233D-0:0-part1"), vec!["E0D55EA5741C1661A9A1233D"]);
        assert_eq!(link_serials("/dev/diskid/DISK-S5Y1NJ0R123456Xp2"), vec!["S5Y1NJ0R123456X"]);
        assert_eq!(link_serials("/dev/diskid/DISK-QM00001s1a"), vec!["QM00001"]);
        assert!(link_serials("/dev/disk/by-id/wwn-0x5002538e40a1b2c3 /dev/disk/by-id/dm-name-root /dev/disk/by-id/scsi-35002538e40a1b2c3").is_empty());
        assert!(user_like("alice") && !user_like("lost+found") && !user_like(""));
    }

    #[test]
    fn fat_serial_in_an_esp_image() {
        // A FAT32 boot sector and its backup, serial 122A-10FD (little-endian
        // on disk), and a sector that isn't one holding the same 4 bytes.
        let mut bs = vec![0u8; 512];
        bs[..3].copy_from_slice(&[0xeb, 0x58, 0x90]);
        bs[67..71].copy_from_slice(&[0xfd, 0x10, 0x2a, 0x12]);
        bs[82..90].copy_from_slice(b"FAT32   ");
        bs[510..].copy_from_slice(&[0x55, 0xaa]);
        let mut other = vec![1u8; 512];
        other[67..71].copy_from_slice(&[0xfd, 0x10, 0x2a, 0x12]);
        let img = [bs.clone(), bs.clone(), other.clone()].concat();
        let d = parse(&format!(
            r#"{{"os": {{"id": "netbsd"}}, "p": {{"uuid": "122A-10FD", "type": "vfat", "esp_image": [{{"offset": 0, "b64": "{}"}}]}}}}"#,
            crate::bytes::base64(&img)
        ))
        .unwrap();
        let (r, red) = redact(&d);
        assert_ne!(r.at(&["p", "uuid"]).and_then(Value::as_str), Some("122A-10FD"));
        let out = crate::bytes::unbase64(r.at(&["p", "esp_image", "0", "b64"]).and_then(Value::as_str).unwrap()).unwrap();
        assert_eq!(out.len(), img.len());
        for i in [0, 512] {
            assert_ne!(&out[i + 67..i + 71], &[0xfd, 0x10, 0x2a, 0x12], "boot sector at {i}");
            assert_eq!(&out[i..i + 67], &bs[..67]);
            assert_eq!(&out[i + 71..i + 512], &bs[71..]);
        }
        assert_eq!(&out[1024..], &other[..], "a 4-byte run elsewhere is left alone");
        // And back: check_plan's proof that the redacted dump maps to the original.
        assert_eq!(red.unredact_value(&r).compact(), d.compact());
    }

    #[test]
    fn md_name_with_a_host_and_a_name_keeps_its_parts() {
        // A default host and a name that isn't a number: load splits it at
        // the colon into --homehost and --name, so a pseudonym must too.
        let d = parse(
            r#"{"os": {"id": "fedora"}, "storage": {"md_arrays": [
                {"label": "localhost.localdomain:boot"},
                {"label": "localhost.localdomain:pv00"},
                {"label": "oldbox:media"},
                {"label": "localhost:abc"}]},
              "p": ["/dev/md/pv00", "/dev/disk/by-id/md-name-localhost.localdomain:pv00"]}"#,
        )
        .unwrap();
        let (r, red) = redact(&d);
        let s = |i: &str| r.at(&["storage", "md_arrays", i, "label"]).and_then(Value::as_str).unwrap().to_string();
        assert_eq!(s("0"), "localhost.localdomain:boot", "{}", red.summary(true));
        assert!(s("1").starts_with("localhost.localdomain:label") && !s("1").contains("pv00"), "{}", s("1"));
        assert!(s("2").starts_with("host") && s("2").contains(":label") && !s("2").contains("oldbox") && !s("2").contains("media"), "{}", s("2"));
        assert!(s("3").starts_with("localhost:label") && !s("3").contains("abc"), "{}", s("3"));
        assert!(!r.compact().contains("pv00"), "{}", r.compact());
        assert_eq!(red.unredact_value(&r).compact(), d.compact());
    }

    #[test]
    fn a_colon_outside_md_is_part_of_the_label() {
        let d = parse(
            r#"{"os": {"id": "debian"}, "storage": {"disks": [{"name": "vda", "partitions": [
                  {"name": "vda1", "links": ["/dev/disk/by-label/home:media"], "content": {"type": "ext4", "label": "home:media"}},
                  {"name": "vda2", "partlabel": "ab:xy", "content": {"type": "ext4", "label": "photos:old"}},
                  {"name": "vda3", "content": {"type": "linux_raid_member", "label": "oldbox:media"}}]}],
                "fstab": [{"spec": "LABEL=home:media", "file": "/home"}, {"spec": "PARTLABEL=ab:xy", "file": "/srv/x"}, {"spec": "LABEL=photos:old", "file": "/mnt/p"}]}}"#,
        )
        .unwrap();
        let (r, red) = redact(&d);
        let s = |p: &[&str]| r.at(p).and_then(Value::as_str).unwrap().to_string();
        let home = s(&["storage", "disks", "0", "partitions", "0", "content", "label"]);
        assert!(home.starts_with("label") && !home.contains(':'), "{home}");
        assert_eq!(s(&["storage", "fstab", "0", "spec"]), format!("LABEL={home}"));
        assert_eq!(s(&["storage", "fstab", "0", "file"]), "/home");
        assert_eq!(s(&["storage", "disks", "0", "partitions", "0", "links", "0"]), format!("/dev/disk/by-label/{home}"));
        assert_eq!(s(&["storage", "fstab", "1", "spec"]), format!("PARTLABEL={}", s(&["storage", "disks", "0", "partitions", "1", "partlabel"])));
        assert_eq!(s(&["storage", "fstab", "2", "spec"]), format!("LABEL={}", s(&["storage", "disks", "0", "partitions", "1", "content", "label"])));
        let md = s(&["storage", "disks", "0", "partitions", "2", "content", "label"]);
        assert!(md.starts_with("host") && md.contains(":label"), "{md}");
        assert!(!r.compact().contains("ab:xy") && !r.compact().contains("photos:old"), "{}", r.compact());
        assert!(red.entries.iter().all(|e| e.kind != Kind::Hostname || e.original == "oldbox"), "{}", red.summary(true));
        assert_eq!(red.unredact_value(&r).compact(), d.compact());
    }

    #[test]
    fn image_labels_and_build_paths_are_kept() {
        let d = parse(
            r#"{"host": {"hostname": "backotter"}, "os": {"id": "dragonfly", "kernel_version": "DragonFly v6.4.2-RELEASE #11: root@www.shiningsilence.com:/usr/obj/home/justin/release/6_4/sys/X86_64_GENERIC"},
                "storage": {"disks": [{"name": "da0", "serial": "1", "partitions": [
                    {"partlabel": "EFISYS"}, {"partlabel": "Appended2"}, {"partlabel": "Gap1"}, {"partlabel": "HAIKU ESP"},
                    {"partlabel": "BackOtter root"}, {"partlabel": "BackOtter config"},
                    {"content": {"type": "iso9660", "label": "Ubuntu-Server 26.04.1 LTS amd64"}}, {"content": {"type": "iso9660", "label": "ISO9660"}}]}]}}"#,
        )
        .unwrap();
        let (r, red) = redact(&d);
        assert_eq!(r.pretty(), d.pretty(), "{}", red.summary(true));
        // A home directory is still a user's.
        let d = parse(r#"{"os": {"id": "debian"}, "x": "/home/alice/notes", "y": "HOME=/home/alice"}"#).unwrap();
        let (r, _) = redact(&d);
        assert!(!r.pretty().contains("alice"), "{}", r.pretty());
    }

    /// PikaOS's installers: the GTK one's GPT names, pikainstall's file
    /// system labels (the 2.0.5 GTK installer's "PikaOS" on /boot too), and
    /// its ISO's volume label, all kept.
    #[test]
    fn pikaos_labels_are_kept() {
        let d = parse(
            r#"{"os": {"id": "pika"}, "storage": {"disks": [{"name": "vda", "serial": "1", "partitions": [
                {"partlabel": "linux-efi", "content": {"type": "vfat", "label": "EFI"}}, {"partlabel": "linux-boot", "content": {"type": "ext4", "label": "PikaBoot"}},
                {"partlabel": "linux-root", "content": {"type": "xfs", "label": "PikaRoot"}}, {"partlabel": "linux-home", "content": {"type": "xfs", "label": "PikaHome"}},
                {"partlabel": "linux-boot", "content": {"type": "ext4", "label": "PikaOS"}},
                {"content": {"type": "iso9660", "label": "PikaOS-4-amd64-v3"}}]}]}}"#,
        )
        .unwrap();
        let (r, red) = redact(&d);
        assert_eq!(r.pretty(), d.pretty(), "{}", red.summary(true));
        assert!(release_label("pikaos-nest-gnome-4.0-amd64-v3"));
    }

    /// Kali: its installer's default hostname kept on another system's dump
    /// too (not only as Kali's own os word), and its ISO's names as release
    /// labels.
    #[test]
    fn kali_host_and_release_are_kept() {
        let d = parse(r#"{"os": {"id": "debian"}, "host": {"hostname": "kali"}, "storage": {"disks": [{"name": "vda", "serial": "1", "partitions": [{"content": {"type": "iso9660", "label": "Kali-2026.3-amd64"}}]}]}}"#).unwrap();
        let (r, red) = redact(&d);
        assert_eq!(r.pretty(), d.pretty(), "{}", red.summary(true));
        assert!(release_label("kali-linux-2026.3-installer-netinst-amd64"));
        assert!(!release_label("kali-desktop"));
    }

    /// Proxmox VE: its installer's default hostname kept (os-release says
    /// Debian, so "proxmox" isn't an os word), and its ISO's names as
    /// release labels.
    #[test]
    fn proxmox_host_and_release_are_kept() {
        let d = parse(r#"{"os": {"id": "debian", "product": "Proxmox VE 9.2"}, "host": {"hostname": "pve"}, "storage": {"disks": [{"name": "vda", "serial": "1", "partitions": [{"content": {"type": "iso9660", "label": "proxmox-ve_9.2-1-amd64"}}]}]}}"#).unwrap();
        let (r, red) = redact(&d);
        assert_eq!(r.pretty(), d.pretty(), "{}", red.summary(true));
        assert!(release_label("proxmox-ve_9.2-1-amd64"));
        assert!(!release_label("proxmox-backup"));
    }

    /// Guix System: "gnu" and "guix" kept as hostnames on another system's
    /// dump too, and the installer's names as release labels.  Store paths
    /// (base32 hashes) aren't identifiers.
    #[test]
    fn guix_hosts_and_release_are_kept() {
        for h in ["gnu", "guix"] {
            let d = parse(&format!(r#"{{"os": {{"id": "debian"}}, "host": {{"hostname": "{h}"}}, "boot": {{"store": {{"grub": "/gnu/store/0123456789abcdfghijklmnpqrsvwxyz-grub-2.12"}}}}, "storage": {{"disks": [{{"name": "vda", "serial": "1", "partitions": [{{"content": {{"type": "iso9660", "label": "guix-system-install-1.5.0.x86_64-linux"}}}}]}}]}}}}"#)).unwrap();
            let (r, red) = redact(&d);
            assert_eq!(r.pretty(), d.pretty(), "{}", red.summary(true));
        }
        assert!(release_label("guix-system-install-1.5.0.x86_64-linux"));
        assert!(!release_label("guix-home"));
    }

}
