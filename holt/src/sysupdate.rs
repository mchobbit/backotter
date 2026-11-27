//! systemd-sysupdate systems (docs/design/kde-linux.md and
//! docs/design/gnome-os.md, "Shared lines" and R11): what dump records of
//! their transfers, the versions they hold, and the unified kernel images
//! (UKIs) that boot each version (`boot.sysupdate`).  Built once for both:
//! the generic part reads sysupdate.d(5)'s `*.transfer` files, finds each
//! transfer's instances (regular files and directories by `MatchPattern=`,
//! GPT partitions by `MatchPartitionType=` and their partition label), and
//! reads every UKI an ESP-relative transfer installs (`.osrel`, every
//! profile's `.cmdline` with its `usrhash=`, the boot counter in the file
//! name, the addons in `<uki>.extra.d/` and `loader/addons/`).  KDE Linux
//! (`kdelinux`) sits on top: its erofs image files on the btrfs top level,
//! its etc-mstack `/etc` layers, the half versions, and the refusals of a
//! layout bko-holt hasn't seen.  GNOME OS's own part (its raw verity
//! partitions, TPM) is not here.
//!
//! Everything the design marks *check* (settled on the K1 guest, not yet
//! seen) is in `checks` below, in one place.

use crate::json::{Obj, Value};
use crate::mounts::Mount;
use crate::osrel::OsRelease;
use crate::report::BackupSource;
use crate::sys::Sys;
use crate::zfsbootmenu::{Pe, read_uki, sdmagic};

/// Where sysupdate reads transfers, the first a file name is found in
/// winning (sysupdate.d(5)).
pub(crate) const DIRS: [&str; 4] = [
    "/etc/sysupdate.d",
    "/run/sysupdate.d",
    "/usr/local/lib/sysupdate.d",
    "/usr/lib/sysupdate.d",
];

/// The choices that rest on what the design marks *check*, in one place
/// (docs/design/kde-linux.md, K1 settles them).
pub(crate) mod checks {
    /// The directory a per-UKI addon directory is named after: the UKI's
    /// file name, or that name without its boot counter (KDE Linux's
    /// factory tree has `kde-linux_V.efi.extra.d` beside `kde-linux_V+3.efi`,
    /// [src] build.sh:145).  Which one systemd-stub reads is *check*: both
    /// are looked for, the counted name first.
    pub(crate) const EXTRA_D_BOTH_NAMES: bool = true;
    /// An image file is taken as read-only when its mode has no write bit.
    /// Whether sysupdate's `ReadOnly=yes` sets the mode or the immutable
    /// attribute is *check*; the attribute isn't read here.
    pub(crate) const READ_ONLY_BY_MODE: bool = true;
    /// The ESP's mount points tried by the automount stat
    /// (`report::trigger_automounts`): gpt-auto's `/boot` (KDE Linux) and
    /// `/efi` (GNOME OS), and `/boot/efi`.
    pub(crate) const AUTOMOUNT_POINTS: [&str; 3] = ["/boot", "/efi", "/boot/efi"];
    /// The one variant bko-holt v1 supports (Decision 1).
    pub(crate) const KDE_VARIANTS: [&str; 1] = ["testing"];
}

// --- transfer files ----------------------------------------------------------

/// One `*.transfer` file: its sections' assignments in file order.
#[derive(Debug, Clone, Default, PartialEq)]
pub(crate) struct Transfer {
    pub file: String,
    pub path: String,
    pub sections: Vec<(String, Vec<(String, String)>)>,
}

/// An INI file as systemd's parser reads it: `[Section]`, `Key=Value`,
/// `#` and `;` comments, a trailing backslash joining the next line.
pub(crate) fn parse_ini(text: &str) -> Vec<(String, Vec<(String, String)>)> {
    let mut out: Vec<(String, Vec<(String, String)>)> = Vec::new();
    let mut logical: Vec<String> = Vec::new();
    let mut cur = String::new();
    let mut cont = false;
    for raw in text.lines() {
        let l = raw.trim();
        if !cont && (l.starts_with('#') || l.starts_with(';')) {
            continue;
        }
        if cont && (l.starts_with('#') || l.starts_with(';')) {
            // A comment inside a continuation is skipped, the value goes on.
            continue;
        }
        let (body, more) = match l.strip_suffix('\\') {
            Some(b) => (b.trim_end(), true),
            None => (l, false),
        };
        if cont {
            cur.push(' ');
            cur.push_str(body);
        } else {
            cur = body.to_string();
        }
        cont = more;
        if !cont {
            logical.push(std::mem::take(&mut cur));
        }
    }
    if cont {
        logical.push(cur);
    }
    for l in logical {
        let l = l.trim();
        if l.is_empty() {
            continue;
        }
        if let Some(name) = l.strip_prefix('[').and_then(|r| r.strip_suffix(']')) {
            out.push((name.trim().to_string(), Vec::new()));
            continue;
        }
        let Some((k, v)) = l.split_once('=') else {
            continue;
        };
        if out.is_empty() {
            out.push((String::new(), Vec::new()));
        }
        if let Some((_, kv)) = out.last_mut() {
            kv.push((k.trim().to_string(), v.trim().to_string()));
        }
    }
    out
}

impl Transfer {
    pub(crate) fn parse(file: &str, path: &str, text: &str) -> Self {
        Self {
            file: file.to_string(),
            path: path.to_string(),
            sections: parse_ini(text),
        }
    }

    /// The last assignment of KEY in SECTION (systemd's: the last wins).
    pub(crate) fn get(&self, section: &str, key: &str) -> Option<&str> {
        self.sections
            .iter()
            .filter(|(s, _)| s == section)
            .flat_map(|(_, kv)| kv)
            .filter(|(k, _)| k == key)
            .map(|(_, v)| v.as_str())
            .next_back()
    }

    /// A list setting (`MatchPattern=`): each assignment's words appended,
    /// an empty assignment clearing the list.
    pub(crate) fn list(&self, section: &str, key: &str) -> Vec<String> {
        let mut out = Vec::new();
        for (_, v) in self
            .sections
            .iter()
            .filter(|(s, _)| s == section)
            .flat_map(|(_, kv)| kv)
            .filter(|(k, _)| k == key)
        {
            if v.is_empty() {
                out.clear();
            }
            out.extend(v.split_whitespace().map(str::to_string));
        }
        out
    }

    fn bool(&self, section: &str, key: &str) -> Option<bool> {
        self.get(section, key).map(|v| {
            matches!(
                v.to_ascii_lowercase().as_str(),
                "1" | "yes" | "y" | "true" | "t" | "on"
            )
        })
    }

    fn num(&self, section: &str, key: &str) -> Option<u64> {
        self.get(section, key).and_then(|v| v.parse().ok())
    }

    /// The target's type (sysupdate's default: `regular-file`).
    pub(crate) fn target_type(&self) -> &str {
        self.get("Target", "Type").unwrap_or("regular-file")
    }

    pub(crate) fn target_patterns(&self) -> Vec<String> {
        self.list("Target", "MatchPattern")
    }
}

/// Every transfer file, by name, the first directory of `DIRS` having it
/// winning; in name order.
pub(crate) fn read_transfers(sys: &Sys) -> Vec<Transfer> {
    let mut seen: std::collections::BTreeMap<String, Transfer> = std::collections::BTreeMap::new();
    for d in DIRS {
        for f in sys.list(d).into_iter().filter(|f| f.ends_with(".transfer")) {
            if seen.contains_key(&f) {
                continue;
            }
            let path = format!("{d}/{f}");
            // An empty file or a link to /dev/null masks the name.
            let text = sys.read(&path).unwrap_or_default();
            seen.insert(f.clone(), Transfer::parse(&f, &path, &text));
        }
    }
    seen.into_values()
        .filter(|t| !t.sections.is_empty())
        .collect()
}

/// sysupdate's specifiers in a transfer's values (systemd.unit(5)'s and
/// sysupdate.d(5)'s): from the running os-release and machine.
pub(crate) fn resolve_specifiers(s: &str, os: Option<&OsRelease>, arch: &str) -> String {
    let g = |k: &str| os.and_then(|o| o.get(k)).unwrap_or("").to_string();
    let mut out = String::new();
    let mut it = s.chars();
    while let Some(c) = it.next() {
        if c != '%' {
            out.push(c);
            continue;
        }
        match it.next() {
            Some('%') | None => out.push('%'),
            Some('A') => out.push_str(&g("IMAGE_VERSION")),
            Some('M') => out.push_str(&g("IMAGE_ID")),
            Some('w') => out.push_str(&g("VERSION_ID")),
            Some('W') => out.push_str(&g("VARIANT_ID")),
            Some('B') => out.push_str(&g("BUILD_ID")),
            Some('o') => out.push_str(&g("ID")),
            Some('a') => out.push_str(arch),
            Some(o) => {
                out.push('%');
                out.push(o);
            }
        }
    }
    out
}

// --- MatchPattern ------------------------------------------------------------

/// What a name matched by a `MatchPattern=` says.
#[derive(Debug, Clone, Default, PartialEq)]
pub(crate) struct Matched {
    pub version: String,
    pub tries_left: Option<u64>,
    pub tries_done: Option<u64>,
}

#[derive(Debug, PartialEq)]
enum Tok {
    Lit(String),
    Field(char),
}

fn tokens(pat: &str) -> Vec<Tok> {
    let mut out = Vec::new();
    let mut lit = String::new();
    let mut it = pat.chars().peekable();
    while let Some(c) = it.next() {
        if c != '@' {
            lit.push(c);
            continue;
        }
        match it.next() {
            Some('@') | None => lit.push('@'),
            Some(f) => {
                if !lit.is_empty() {
                    out.push(Tok::Lit(std::mem::take(&mut lit)));
                }
                out.push(Tok::Field(f));
            }
        }
    }
    if !lit.is_empty() {
        out.push(Tok::Lit(lit));
    }
    out
}

/// Whether V is a version sysupdate takes: letters, digits and `.,_-+`.
fn valid_version(v: &str) -> bool {
    !v.is_empty()
        && v
            .bytes()
            .all(|c| c.is_ascii_alphanumeric() || b".,_-+".contains(&c))
}

/// NAME matched against one `MatchPattern=` as sysupdate matches it: each
/// field takes the text up to the first occurrence of the literal after it
/// (all the rest when it's last), and must be valid for its kind; the whole
/// name must be taken.  None when it doesn't match, or a pattern has no
/// `@v`.
pub(crate) fn match_pattern(pat: &str, name: &str) -> Option<Matched> {
    let toks = tokens(pat);
    let mut rest = name;
    let mut found = Matched::default();
    let mut has_v = false;
    for (i, t) in toks.iter().enumerate() {
        match t {
            Tok::Lit(l) => rest = rest.strip_prefix(l.as_str())?,
            Tok::Field(kind) => {
                let n = match toks.get(i + 1) {
                    Some(Tok::Lit(l)) => rest.find(l.as_str())?,
                    Some(Tok::Field(_)) => return None,
                    None => rest.len(),
                };
                let (val, r) = rest.split_at(n);
                rest = r;
                if val.is_empty() || val.contains('/') {
                    return None;
                }
                let digits = || val.bytes().all(|c| c.is_ascii_digit());
                match kind {
                    'v' => {
                        if !valid_version(val) {
                            return None;
                        }
                        found.version = val.to_string();
                        has_v = true;
                    }
                    'l' | 'd' => {
                        if !digits() {
                            return None;
                        }
                        let x = val.parse().ok()?;
                        if *kind == 'l' {
                            found.tries_left = Some(x);
                        } else {
                            found.tries_done = Some(x);
                        }
                    }
                    't' | 's' if !digits() => return None,
                    'm' if !val.bytes().all(|c| (b'0'..=b'7').contains(&c)) => return None,
                    'g' | 'r' | 'a' if val != "0" && val != "1" => return None,
                    'h' if !(val.len() == 64 && val.bytes().all(|c| c.is_ascii_hexdigit())) => {
                        return None;
                    }
                    'u' if !val.bytes().all(|c| c.is_ascii_hexdigit() || c == b'-') => {
                        return None;
                    }
                    _ => {}
                }
            }
        }
    }
    (rest.is_empty() && has_v).then_some(found)
}

/// NAME against PATTERNS, the first that matches.
pub(crate) fn match_any(patterns: &[String], name: &str) -> Option<Matched> {
    patterns.iter().find_map(|p| match_pattern(p, name))
}

/// A UKI's (or BLS entry's) file name split as systemd-boot's boot counting
/// has it: `ID+LEFT-DONE.efi` or `ID+LEFT.efi` is `ID.efi` with LEFT tries
/// left and DONE done.  For names no transfer pattern explains.
pub(crate) fn counter(name: &str) -> (String, Option<(u64, u64)>) {
    let (stem, ext) = name
        .rfind('.')
        .map_or((name, ""), |i| (&name[..i], &name[i..]));
    let parsed = stem.rsplit_once('+').and_then(|(id, c)| {
        let (left, done) = c.split_once('-').unwrap_or((c, "0"));
        let n = |x: &str| {
            (!x.is_empty() && x.bytes().all(|b| b.is_ascii_digit()))
                .then(|| x.parse::<u64>().ok())
                .flatten()
        };
        Some((id, (n(left)?, n(done)?)))
    });
    match parsed {
        Some((id, c)) if !id.is_empty() => (format!("{id}{ext}"), Some(c)),
        _ => (name.to_string(), None),
    }
}

// --- the partitions and the mounts -------------------------------------------

/// A GPT partition, from the dump's storage.disks.
#[derive(Debug, Clone, Default, PartialEq)]
pub(crate) struct Part {
    pub device: String,
    pub partuuid: Option<String>,
    pub partlabel: String,
    pub type_guid: String,
    pub size_bytes: Option<u64>,
    pub read_only: bool,
}

/// Every GPT partition of DISKS (storage.disks).
pub(crate) fn partitions(disks: &[Value]) -> Vec<Part> {
    let s = |v: &Value, k: &str| v.get(k).and_then(Value::as_str).map(str::to_string);
    disks
        .iter()
        .filter_map(|d| d.get("partitions").and_then(Value::as_array))
        .flatten()
        .filter_map(|p| {
            Some(Part {
                device: s(p, "name")?,
                partuuid: s(p, "uuid"),
                partlabel: s(p, "partlabel")?,
                type_guid: s(p, "type")?.to_ascii_lowercase(),
                size_bytes: p.get("size_bytes").and_then(Value::as_u64),
                read_only: p
                    .get("attribute_flags")
                    .and_then(Value::as_array)
                    .is_some_and(|a| a.iter().any(|f| f.as_str() == Some("read_only"))),
            })
        })
        .collect()
}

/// The GPT type GUID of a Discoverable Partitions Specification name
/// (`MatchPartitionType=`: `usr`, `usr-verity`, `root-x86-64`, ...; the
/// names without an architecture are the machine's), or the GUID itself.
/// Checked against `systemd-id128 show` on Debian 13's systemd.
pub(crate) fn dps_type(name: &str, arch: &str) -> Option<String> {
    const TYPES: [(&str, &str); 17] = [
        ("root-x86-64", "4f68bce3-e8cd-4db1-96e7-fbcaf984b709"),
        ("usr-x86-64", "8484680c-9521-48c6-9c11-b0720656f69e"),
        ("root-x86-64-verity", "2c7357ed-ebd2-46d9-aec1-23d437ec2bf5"),
        ("usr-x86-64-verity", "77ff5f63-e7b6-4633-acf4-1565b864c0e6"),
        ("root-x86-64-verity-sig", "41092b05-9fc8-4523-994f-2def0408b176"),
        ("usr-x86-64-verity-sig", "e7bb33fb-06cf-4e81-8273-e543b413e2e2"),
        ("root-arm64", "b921b045-1df0-41c3-af44-4c6f280d3fae"),
        ("usr-arm64", "b0e01050-ee5f-4390-949a-9101b17104e9"),
        ("root-arm64-verity", "df3300ce-d69f-4c92-978c-9bfb0f38d820"),
        ("usr-arm64-verity", "6e11a4e7-fbca-4ded-b9e9-e1a512bb664e"),
        ("root-arm64-verity-sig", "6db69de6-29f4-4758-a7a5-962190f00ce3"),
        ("usr-arm64-verity-sig", "c23ce4ff-44bd-4b00-b2d4-b41b3419e02a"),
        ("esp", "c12a7328-f81f-11d2-ba4b-00a0c93ec93b"),
        ("xbootldr", "bc13c2ff-59e6-4262-a352-b275fd6f7172"),
        ("linux-generic", "0fc63daf-8483-4772-8e79-3d69d8477de4"),
        ("home", "933ac7e1-2eb4-4f13-b844-0e14e2aef915"),
        ("var", "4d21b016-b534-45c2-a9fb-5c16e091fd2d"),
    ];
    let l = name.to_ascii_lowercase();
    if l.len() == 36 && l.bytes().all(|c| c.is_ascii_hexdigit() || c == b'-') {
        return Some(l);
    }
    let dps_arch = match arch {
        "aarch64" | "arm64" => "arm64",
        _ => "x86-64",
    };
    let full = match l.as_str() {
        "root" | "usr" => format!("{l}-{dps_arch}"),
        _ => match l.split_once('-') {
            Some((base @ ("root" | "usr"), rest @ ("verity" | "verity-sig"))) => {
                format!("{base}-{dps_arch}-{rest}")
            }
            _ => l,
        },
    };
    TYPES
        .iter()
        .find(|(n, _)| *n == full)
        .map(|(_, g)| (*g).to_string())
}

/// What dump knows of the system that sysupdate reads relative to.
pub(crate) struct Input<'a> {
    pub os: Option<&'a OsRelease>,
    /// The machine (`uname -m`).
    pub arch: &'a str,
    /// The whole mount table, pseudo filesystems included.
    pub mounts: &'a [Mount],
    /// The mounted ESPs, (device, mount point).
    pub esps: &'a [(String, String)],
    /// Where an XBOOTLDR partition is mounted, if one is.
    pub xbootldr: Option<String>,
    /// storage.disks' GPT partitions.
    pub parts: Vec<Part>,
    /// The device a mount is of (report's `mount_dev`).
    pub device: &'a dyn Fn(&Mount) -> Option<String>,
    /// A loop device's backing file, by the mount of it.
    pub loop_backing: &'a dyn Fn(&Mount) -> Option<String>,
    /// `LoaderEntrySelected`, the entry systemd-boot booted.
    pub entry_selected: Option<String>,
}

impl Input<'_> {
    /// The directory a file transfer's target `Path=` is in, by its
    /// `PathRelativeTo=`; None when that partition isn't mounted.
    fn target_dir(&self, t: &Transfer) -> Option<String> {
        let path = resolve_specifiers(t.get("Target", "Path")?, self.os, self.arch);
        let base = match t.get("Target", "PathRelativeTo").unwrap_or("root") {
            "root" => String::new(),
            "esp" => self.esps.first()?.1.clone(),
            "xbootldr" => self.xbootldr.clone()?,
            "boot" => self
                .xbootldr
                .clone()
                .or_else(|| self.esps.first().map(|e| e.1.clone()))?,
            _ => return None,
        };
        let joined = format!("{}/{}", base.trim_end_matches('/'), path.trim_start_matches('/'));
        let joined = joined.trim_end_matches('/').to_string();
        Some(if joined.is_empty() { "/".into() } else { joined })
    }

    /// Whether DIR is on an ESP or the XBOOTLDR (where UKIs are).
    fn on_boot_partition(&self, dir: &str) -> Option<String> {
        self.esps
            .iter()
            .map(|e| e.1.clone())
            .chain(self.xbootldr.clone())
            .filter(|m| dir == m || dir.starts_with(&format!("{}/", m.trim_end_matches('/'))))
            .max_by_key(String::len)
    }
}

// --- instances ---------------------------------------------------------------

/// One instance a transfer's target has: a file or directory, or a
/// partition (then `part`).
#[derive(Debug, Clone, PartialEq)]
pub(crate) struct Instance {
    pub name: String,
    /// None for an empty partition slot (`_empty`, or a label no pattern
    /// takes).
    pub m: Option<Matched>,
    pub size: Option<u64>,
    pub mode: Option<u32>,
    pub part: Option<Part>,
}

impl Instance {
    fn json(&self) -> Value {
        let m = self.m.as_ref();
        let mut o = Obj::new()
            .set("name", self.name.as_str())
            .set("version", m.map(|m| m.version.clone()))
            .set("tries_left", m.and_then(|m| m.tries_left))
            .set("tries_done", m.and_then(|m| m.tries_done));
        if let Some(p) = &self.part {
            o.put("device", p.device.as_str());
            o.put("partuuid", p.partuuid.clone());
            o.put("partlabel", p.partlabel.as_str());
            o.put("type", p.type_guid.as_str());
            o.put("empty", self.m.is_none());
            o.put("size_bytes", p.size_bytes);
            o.put("read_only", p.read_only);
        } else {
            o.put("size", self.size);
            o.put("mode", self.mode.map(|m| format!("{:04o}", m & 0o7777)));
        }
        o.build()
    }
}

fn mode_of(sys: &Sys, abs: &str) -> Option<u32> {
    use std::os::unix::fs::PermissionsExt;
    std::fs::metadata(sys.p(abs))
        .ok()
        .map(|m| m.permissions().mode())
}

/// TRANSFER's target instances: files (or directories) in its directory
/// whose names a pattern matches; partitions of its type whose labels a
/// pattern matches, and the empty slots of that type (`_empty`).
fn instances(sys: &Sys, inp: &Input<'_>, t: &Transfer, dir: Option<&str>) -> Vec<Instance> {
    let pats: Vec<String> = t
        .target_patterns()
        .iter()
        .map(|p| resolve_specifiers(p, inp.os, inp.arch))
        .collect();
    match t.target_type() {
        "partition" => {
            let want = t
                .get("Target", "MatchPartitionType")
                .and_then(|n| dps_type(n, inp.arch));
            inp.parts
                .iter()
                .filter(|p| want.as_ref().is_none_or(|w| *w == p.type_guid))
                .filter_map(|p| {
                    let m = match_any(&pats, &p.partlabel);
                    // Only the partitions of the type: without a type, only
                    // those a pattern takes, and sysupdate's empty label.
                    if m.is_none() && want.is_none() && p.partlabel != "_empty" {
                        return None;
                    }
                    Some(Instance {
                        name: p.partlabel.clone(),
                        m,
                        size: p.size_bytes,
                        mode: None,
                        part: Some(p.clone()),
                    })
                })
                .collect()
        }
        ty => {
            let Some(dir) = dir else { return Vec::new() };
            sys.list(dir)
                .into_iter()
                .filter_map(|n| {
                    let abs = format!("{}/{n}", dir.trim_end_matches('/'));
                    let is_dir = sys.is_dir(&abs);
                    let right_kind = if ty == "regular-file" {
                        sys.is_file(&abs)
                    } else {
                        is_dir
                    };
                    if !right_kind {
                        return None;
                    }
                    let m = match_any(&pats, &n)?;
                    Some(Instance {
                        size: sys.file_size(&abs),
                        mode: mode_of(sys, &abs),
                        name: n,
                        m: Some(m),
                        part: None,
                    })
                })
                .collect()
        }
    }
}

// --- UKIs and addons -----------------------------------------------------------

/// The `usrhash=` (and `roothash=`) of a command line, the last one given.
pub(crate) fn cmdline_arg(cmdline: &str, key: &str) -> Option<String> {
    let k = format!("{key}=");
    cmdline
        .split_whitespace()
        .filter_map(|w| w.strip_prefix(k.as_str()))
        .next_back()
        .map(str::to_string)
}

/// One profile of a multi-profile UKI: its `.profile` text and the
/// `.cmdline` after it (systemd-stub: a profile's sections follow its
/// `.profile`; the sections before the first are the base's).
#[derive(Debug, Clone, PartialEq)]
pub(crate) struct Profile {
    pub profile: String,
    pub cmdline: Option<String>,
}

/// An addon (or any small PE the stub reads): its `.cmdline` and stub.
#[derive(Debug, Clone, PartialEq)]
pub(crate) struct Addon {
    pub path: String,
    pub size: Option<u64>,
    pub sha256: Option<String>,
    pub cmdline: Option<String>,
    pub stub: Option<String>,
    /// Not a PE file (a credential, a sysext image in `.extra.d`).
    pub pe: bool,
}

impl Addon {
    fn json(&self) -> Value {
        Obj::new()
            .set("path", self.path.as_str())
            .set("size", self.size)
            .set("sha256", self.sha256.clone())
            .set("pe", self.pe)
            .set("cmdline", self.cmdline.clone())
            .set(
                "usrhash",
                self.cmdline.as_deref().and_then(|c| cmdline_arg(c, "usrhash")),
            )
            .set("stub", self.stub.clone())
            .build()
    }
}

fn sha256_of(sys: &Sys, abs: &str) -> Option<String> {
    crate::bytes::sha256_file(&sys.p(abs)).ok()
}

/// The PE file at ABS as an addon: None when it isn't a file.  The PE
/// section reader, not `read_uki`, which wants `.linux` and `.initrd`.
pub(crate) fn read_addon(sys: &Sys, abs: &str, rel: &str) -> Option<Addon> {
    let size = sys.file_size(abs)?;
    let mut a = Addon {
        path: rel.to_string(),
        size: Some(size),
        sha256: sha256_of(sys, abs),
        cmdline: None,
        stub: None,
        pe: false,
    };
    if let Some(mut pe) = Pe::open(sys, abs) {
        a.pe = true;
        let (c, m) = (pe.find(".cmdline").cloned(), pe.find(".sdmagic").cloned());
        a.cmdline = c.and_then(|s| pe.text(&s)).filter(|c| !c.is_empty());
        a.stub = m.and_then(|s| pe.text(&s)).and_then(|m| sdmagic(&m));
    }
    Some(a)
}

/// The profiles of the PE at ABS (empty for a single-profile UKI).
fn profiles(sys: &Sys, abs: &str) -> Vec<Profile> {
    let Some(mut pe) = Pe::open(sys, abs) else {
        return Vec::new();
    };
    let secs = pe.sections.clone();
    let mut out: Vec<Profile> = Vec::new();
    for s in secs {
        match s.name.as_str() {
            ".profile" => out.push(Profile {
                profile: pe.text(&s).unwrap_or_default(),
                cmdline: None,
            }),
            ".cmdline" => {
                if let Some(p) = out.last_mut() {
                    p.cmdline = pe.text(&s).filter(|c| !c.is_empty());
                }
            }
            _ => {}
        }
    }
    out
}

/// A UKI one of the transfers installs.
#[derive(Debug, Clone, PartialEq)]
pub(crate) struct UkiFile {
    pub transfer: String,
    /// The ESP (or XBOOTLDR) mount point it's on.
    pub mount: String,
    /// Its path on that partition (`EFI/Linux/kde-linux_V+3.efi`).
    pub path: String,
    pub name: String,
    pub m: Matched,
    pub size: Option<u64>,
    pub sha256: Option<String>,
    pub uki: Option<crate::zfsbootmenu::Uki>,
    pub profiles: Vec<Profile>,
    /// `<name>.extra.d`, as found (`checks::EXTRA_D_BOTH_NAMES`), and its files.
    pub extra_d: Option<(String, Vec<Addon>)>,
    /// Its name less the counter is `LoaderEntrySelected`.
    pub selected: bool,
}

impl UkiFile {
    /// The counter-less name (`kde-linux_V.efi`), systemd-boot's entry ID.
    pub(crate) fn entry_id(&self) -> String {
        counter(&self.name).0
    }

    pub(crate) fn cmdline(&self) -> Option<&str> {
        self.uki.as_ref().and_then(|u| u.cmdline.as_deref())
    }

    fn json(&self) -> Value {
        let u = self.uki.as_ref();
        let os = |k: &str| {
            u.and_then(|u| u.osrel.iter().find(|(x, _)| x == k))
                .map(|(_, v)| v.clone())
        };
        Obj::new()
            .set("transfer", self.transfer.as_str())
            .set("mount", self.mount.as_str())
            .set("path", self.path.as_str())
            .set("name", self.name.as_str())
            .set("entry_id", self.entry_id())
            .set("version", self.m.version.as_str())
            .set("tries_left", self.m.tries_left)
            .set("tries_done", self.m.tries_done)
            .set("size", self.size)
            .set("sha256", self.sha256.clone())
            .set("uki", u.is_some())
            .set(
                "osrel",
                Obj::new()
                    .set("id", os("ID"))
                    .set("image_id", os("IMAGE_ID"))
                    .set("image_version", os("IMAGE_VERSION"))
                    .set("version_id", os("VERSION_ID"))
                    .set("variant_id", os("VARIANT_ID")),
            )
            .set("cmdline", self.cmdline())
            .set("usrhash", self.cmdline().and_then(|c| cmdline_arg(c, "usrhash")))
            .set("roothash", self.cmdline().and_then(|c| cmdline_arg(c, "roothash")))
            .set("kernel", u.and_then(|u| u.kernel.clone()))
            .set("stub", u.and_then(|u| u.stub.clone()))
            .set(
                "profiles",
                self.profiles
                    .iter()
                    .map(|p| {
                        Obj::new()
                            .set("profile", p.profile.as_str())
                            .set("cmdline", p.cmdline.clone())
                            .set(
                                "usrhash",
                                p.cmdline.as_deref().and_then(|c| cmdline_arg(c, "usrhash")),
                            )
                            .build()
                    })
                    .collect::<Vec<_>>(),
            )
            .set(
                "extra_d",
                self.extra_d.as_ref().map(|(d, files)| {
                    Obj::new()
                        .set("path", d.as_str())
                        .set("files", files.iter().map(Addon::json).collect::<Vec<_>>())
                        .build()
                }),
            )
            .set("selected", self.selected)
            .build()
    }
}

/// The files of the directory REL on the partition mounted at MOUNT, each
/// read as an addon.
fn addon_dir(sys: &Sys, mount: &str, rel: &str) -> Vec<Addon> {
    let dir = format!("{mount}/{rel}");
    sys.list(&dir)
        .into_iter()
        .filter_map(|f| read_addon(sys, &format!("{dir}/{f}"), &format!("{rel}/{f}")))
        .collect()
}

fn read_uki_file(
    sys: &Sys,
    inp: &Input<'_>,
    t: &Transfer,
    dir: &str,
    mount: &str,
    i: &Instance,
) -> Option<UkiFile> {
    let m = i.m.clone()?;
    let abs = format!("{dir}/{}", i.name);
    let path = abs
        .strip_prefix(&format!("{}/", mount.trim_end_matches('/')))
        .unwrap_or(&abs)
        .to_string();
    let rel_dir = path.rsplit_once('/').map_or("", |(d, _)| d).to_string();
    let mut names = vec![format!("{}.extra.d", i.name)];
    let bare = counter(&i.name).0;
    if checks::EXTRA_D_BOTH_NAMES && bare != i.name {
        names.push(format!("{bare}.extra.d"));
    }
    let extra_d = names.into_iter().find_map(|n| {
        let rel = if rel_dir.is_empty() {
            n
        } else {
            format!("{rel_dir}/{n}")
        };
        sys.is_dir(&format!("{mount}/{rel}"))
            .then(|| (rel.clone(), addon_dir(sys, mount, &rel)))
    });
    let selected = inp
        .entry_selected
        .as_deref()
        .is_some_and(|s| s.eq_ignore_ascii_case(&bare));
    Some(UkiFile {
        transfer: t.file.clone(),
        mount: mount.to_string(),
        name: i.name.clone(),
        path,
        m,
        size: i.size,
        sha256: sha256_of(sys, &abs),
        uki: read_uki(sys, &abs),
        profiles: profiles(sys, &abs),
        extra_d,
        selected,
    })
}

// --- the dump ----------------------------------------------------------------

/// What dump found: `boot.sysupdate`, and on KDE Linux the backup tree that
/// replaces the generic `backup_sources`.
pub(crate) struct Dump {
    pub json: Value,
    pub ukis: Vec<UkiFile>,
    pub kdelinux: Option<KdeLinux>,
}

/// One transfer as dump read it.
struct Read {
    t: Transfer,
    dir: Option<String>,
    inst: Vec<Instance>,
}

fn transfer_json(r: &Read, os: Option<&OsRelease>, arch: &str) -> Value {
    let t = &r.t;
    let pv = t.get("Transfer", "ProtectVersion");
    Obj::new()
        .set("file", t.file.as_str())
        .set("path", t.path.as_str())
        .set(
            "source",
            Obj::new()
                .set("type", t.get("Source", "Type"))
                .set("path", t.get("Source", "Path"))
                .set("match_pattern", t.list("Source", "MatchPattern")),
        )
        .set(
            "target",
            Obj::new()
                .set("type", t.target_type())
                .set("path", t.get("Target", "Path"))
                .set("path_relative_to", t.get("Target", "PathRelativeTo"))
                .set("dir", r.dir.clone())
                .set("match_pattern", t.target_patterns())
                .set("match_partition_type", t.get("Target", "MatchPartitionType"))
                .set(
                    "partition_type_guid",
                    t.get("Target", "MatchPartitionType")
                        .and_then(|n| dps_type(n, arch)),
                )
                .set("instances_max", t.num("Target", "InstancesMax"))
                .set("read_only", t.bool("Target", "ReadOnly"))
                .set("mode", t.get("Target", "Mode"))
                .set("tries_left", t.num("Target", "TriesLeft"))
                .set("tries_done", t.num("Target", "TriesDone")),
        )
        .set("protect_version", pv)
        .set(
            "protect_version_resolved",
            pv.map(|p| resolve_specifiers(p, os, arch)),
        )
        .set("verify", t.bool("Transfer", "Verify"))
        .set(
            "instances",
            r.inst.iter().map(Instance::json).collect::<Vec<_>>(),
        )
        .build()
}

/// `boot.sysupdate`: None on a system with no transfer files.
#[allow(clippy::too_many_lines)]
pub(crate) fn read(sys: &Sys, inp: &Input<'_>) -> Option<Dump> {
    let transfers = read_transfers(sys);
    if transfers.is_empty() {
        return None;
    }
    let reads: Vec<Read> = transfers
        .into_iter()
        .map(|t| {
            let dir = (t.target_type() != "partition")
                .then(|| inp.target_dir(&t))
                .flatten();
            let inst = instances(sys, inp, &t, dir.as_deref());
            Read { t, dir, inst }
        })
        .collect();
    let booted = inp
        .os
        .and_then(|o| o.get("IMAGE_VERSION"))
        .map(str::to_string);
    // The UKIs: an ESP- or XBOOTLDR-relative file transfer's .efi instances.
    let mut ukis: Vec<UkiFile> = Vec::new();
    for r in &reads {
        let (Some(dir), "regular-file") = (r.dir.as_deref(), r.t.target_type()) else {
            continue;
        };
        let Some(mount) = inp.on_boot_partition(dir) else {
            continue;
        };
        for i in r
            .inst
            .iter()
            .filter(|i| i.name.to_ascii_lowercase().ends_with(".efi"))
        {
            if let Some(u) = read_uki_file(sys, inp, &r.t, dir, &mount, i) {
                ukis.push(u);
            }
        }
    }
    // Global addons, on each partition holding UKIs.
    let mut addon_mounts: Vec<String> = ukis.iter().map(|u| u.mount.clone()).collect();
    addon_mounts.extend(inp.esps.iter().map(|e| e.1.clone()));
    addon_mounts.dedup();
    let mut global: Vec<(String, Addon)> = Vec::new();
    for m in &addon_mounts {
        if global.iter().any(|(x, _)| x == m) {
            continue;
        }
        for a in addon_dir(sys, m, "loader/addons") {
            global.push((m.clone(), a));
        }
    }
    // The versions: every instance's, newest first.
    let mut versions: Vec<String> = reads
        .iter()
        .flat_map(|r| r.inst.iter().filter_map(|i| i.m.as_ref()))
        .map(|m| m.version.clone())
        .collect();
    versions.sort_by(|a, b| crate::load::transactional::vercmp(b, a));
    versions.dedup();
    let protected: Vec<String> = reads
        .iter()
        .filter_map(|r| r.t.get("Transfer", "ProtectVersion"))
        .map(|p| resolve_specifiers(p, inp.os, inp.arch))
        .filter(|p| !p.is_empty())
        .collect();
    let has = |r: &Read, v: &str| {
        r.inst
            .iter()
            .any(|i| i.m.as_ref().is_some_and(|m| m.version == v))
    };
    let versions_json: Vec<Value> = versions
        .iter()
        .map(|v| {
            let (inn, out): (Vec<&Read>, Vec<&Read>) = reads.iter().partition(|r| has(r, v));
            Obj::new()
                .set("version", v.as_str())
                .set("booted", booted.as_deref() == Some(v.as_str()))
                .set("newest", versions.first() == Some(v))
                .set("protected", protected.iter().any(|p| p == v))
                .set("in", inn.iter().map(|r| r.t.file.as_str()).collect::<Vec<_>>())
                // A transfer that has instances but not this version's.
                .set(
                    "missing_from",
                    out.iter()
                        .filter(|r| r.inst.iter().any(|i| i.m.is_some()))
                        .map(|r| r.t.file.as_str())
                        .collect::<Vec<_>>(),
                )
                .build()
        })
        .collect();
    let mut o = Obj::new()
        .set(
            "transfers",
            reads
                .iter()
                .map(|r| transfer_json(r, inp.os, inp.arch))
                .collect::<Vec<_>>(),
        )
        .set("image_id", inp.os.and_then(|o| o.get("IMAGE_ID")))
        .set("booted_version", booted.clone())
        .set("newest_version", versions.first().cloned())
        .set("versions", versions_json)
        .set("ukis", ukis.iter().map(UkiFile::json).collect::<Vec<_>>())
        .set(
            "global_addons",
            global
                .iter()
                .map(|(m, a)| {
                    let mut v = a.json();
                    v.put("mount", m.as_str());
                    v
                })
                .collect::<Vec<_>>(),
        );
    let kdelinux = kdelinux_read(sys, inp, &reads, &ukis, booted.as_deref());
    if let Some(k) = &kdelinux {
        o.put("kdelinux", k.json.clone());
    }
    Some(Dump {
        json: o.build(),
        ukis,
        kdelinux,
    })
}

// --- KDE Linux -----------------------------------------------------------------

/// The marker transfer's target pattern ([seen]
/// `/usr/lib/sysupdate.d/50-root-x86-64-erofs.transfer`).
pub(crate) const KDE_IMAGE_PATTERN: &str = "kde-linux_@v.erofs";
pub(crate) const KDE_OS_ID: &str = "kde-linux";
/// etc-mstack's directory at the top level.
pub(crate) const MSTACK_DIR: &str = "etc.mstack.v";
/// The subvolume the running `/` is.
pub(crate) const SYSTEM_SUBVOL: &str = "@system";

/// KDE Linux's part of the dump.
pub(crate) struct KdeLinux {
    pub json: Value,
    /// Also in `json` (refusals), which `refusal` reads.
    #[cfg_attr(not(test), allow(dead_code))]
    pub refusals: Vec<String>,
    pub warnings: Vec<String>,
    /// The backup tree (docs/design/kde-linux.md, "One tree convention"),
    /// when the layout is one v1 supports.
    pub sources: Option<Vec<BackupSource>>,
    /// The device of the btrfs top level and of the ESP, for the other
    /// mounts' warning.
    pub tree_devices: Vec<String>,
}

fn kde_image_name(v: &str) -> String {
    format!("kde-linux_{v}.erofs")
}

#[allow(clippy::too_many_lines)]
fn kdelinux_read(
    sys: &Sys,
    inp: &Input<'_>,
    reads: &[Read],
    ukis: &[UkiFile],
    booted: Option<&str>,
) -> Option<KdeLinux> {
    let id = inp.os.and_then(|o| o.get("ID"));
    let marker = reads.iter().find(|r| {
        r.t.target_type() == "regular-file"
            && r.t.target_patterns().iter().any(|p| p == KDE_IMAGE_PATTERN)
    });
    if id != Some(KDE_OS_ID) && marker.is_none() {
        return None;
    }
    let mut refusals: Vec<String> = Vec::new();
    let mut warnings: Vec<String> = Vec::new();
    if id != Some(KDE_OS_ID) {
        refusals.push(format!(
            "KDE Linux's image transfer ({}) is here but os-release says ID={}: a layout bko-holt hasn't seen",
            marker.map_or("", |r| r.t.path.as_str()),
            id.unwrap_or("")
        ));
    }
    if marker.is_none() {
        refusals.push(format!(
            "this is KDE Linux, but no sysupdate transfer has MatchPattern={KDE_IMAGE_PATTERN}: a layout bko-holt hasn't seen"
        ));
    }
    let variant = inp.os.and_then(|o| o.get("VARIANT_ID")).unwrap_or("");
    if !checks::KDE_VARIANTS.contains(&variant) {
        refusals.push(format!(
            "this is KDE Linux's {variant:?} edition (VARIANT_ID), and bko-holt supports its {} edition only",
            checks::KDE_VARIANTS.join(", ")
        ));
    }
    // /usr: the erofs loop mount of <top>/kde-linux_<IMAGE_VERSION>.erofs.
    let usr = inp
        .mounts
        .iter()
        .rev()
        .find(|m| m.target == "/usr" && m.fstype == "erofs");
    let backing = usr.and_then(|m| (inp.loop_backing)(m));
    let top = backing.as_deref().and_then(|b| {
        let (dir, file) = b.rsplit_once('/')?;
        let want = booted.map(kde_image_name);
        if want.as_deref() != Some(file) {
            refusals.push(format!(
                "KDE Linux's /usr is the loop mount of {b}, not of {} the booted version's image: a layout bko-holt hasn't seen",
                want.unwrap_or_else(|| "kde-linux_<IMAGE_VERSION>.erofs".into())
            ));
        }
        Some(if dir.is_empty() { "/".to_string() } else { dir.to_string() })
    });
    if backing.is_none() {
        refusals.push(match usr {
            None => "this is KDE Linux, but /usr is not an erofs mount: a layout bko-holt hasn't seen".into(),
            Some(m) => format!(
                "this is KDE Linux, but /usr ({}) is not a loop device with a backing file: a layout bko-holt hasn't seen",
                m.source
            ),
        });
    }
    // The btrfs top level, mounted at <top> (subvol=/).
    let top_mount = top.as_deref().and_then(|t| {
        inp.mounts
            .iter()
            .rev()
            .find(|m| m.target == t && m.fstype == "btrfs" && m.root == "/")
    });
    if let (Some(t), None) = (top.as_deref(), top_mount) {
        refusals.push(format!(
            "KDE Linux's image files are in {t}, which isn't the btrfs top level (subvol=/) mounted: a layout bko-holt hasn't seen"
        ));
    }
    let top_s = top.clone().unwrap_or_else(|| "/system".into());
    let at = |rel: &str| format!("{}/{rel}", top_s.trim_end_matches('/'));
    let entries = sys.list(&top_s);
    // RootFSv3 with etc-mstack only (Detection): older layouts refused.
    if top_mount.is_some() {
        let old: Vec<&String> = entries
            .iter()
            .filter(|e| {
                (e.starts_with("@kde-linux_") && !e.starts_with("@kde-linux_debug_"))
                    || *e == "@etc-overlay"
                    || *e == "@home"
            })
            .collect();
        if !old.is_empty() {
            refusals.push(format!(
                "KDE Linux's btrfs top level has {}: an older layout (RootFSv1 or v2), and bko-holt supports RootFSv3 with etc-mstack only",
                old.iter().map(|s| s.as_str()).collect::<Vec<_>>().join(", ")
            ));
        }
        for need in [SYSTEM_SUBVOL, MSTACK_DIR] {
            if !sys.is_dir(&at(need)) {
                refusals.push(format!(
                    "KDE Linux's btrfs top level ({top_s}) has no {need}: bko-holt supports RootFSv3 with etc-mstack only"
                ));
            }
        }
    }
    // The ESP, as dump found it mounted.
    let esp = inp.esps.first().cloned();
    if esp.is_none() {
        refusals.push("this is KDE Linux, and no ESP is mounted (gpt-auto mounts it at /boot): its UKIs would not be backed up; mount it and dump again".into());
    }
    // The images, by the marker transfer's instances.
    let images: Vec<&Instance> = marker
        .map(|r| r.inst.iter().filter(|i| i.m.is_some()).collect())
        .unwrap_or_default();
    let caibx = reads.iter().find(|r| {
        r.t.target_patterns()
            .iter()
            .any(|p| p == "kde-linux_@v.erofs.caibx")
    });
    let version_of = |i: &Instance| i.m.as_ref().map(|m| m.version.clone()).unwrap_or_default();
    let mstacks: Vec<String> = sys
        .list(&at(MSTACK_DIR))
        .into_iter()
        .filter(|n| n.starts_with("etc_") && n.ends_with(".mstack"))
        .collect();
    let images_json: Vec<Value> = images
        .iter()
        .map(|i| {
            let v = version_of(i);
            let ms = format!("etc_{v}.mstack");
            let origin_file = at(&format!("{MSTACK_DIR}/origin-for_{v}.os-release"));
            let origin = sys
                .read(&origin_file)
                .map(|t| crate::osrel::parse(&t))
                .and_then(|o| {
                    o.get("IMAGE_VERSION")
                        .or_else(|| o.get("VERSION_ID"))
                        .map(str::to_string)
                });
            let cx = caibx.and_then(|r| {
                r.inst
                    .iter()
                    .find(|c| c.m.as_ref().is_some_and(|m| m.version == v))
            });
            let uki = ukis.iter().find(|u| u.m.version == v);
            Obj::new()
                .set("version", v.as_str())
                .set("file", i.name.as_str())
                .set("size", i.size)
                .set("mode", i.mode.map(|m| format!("{:04o}", m & 0o7777)))
                .set(
                    "read_only",
                    checks::READ_ONLY_BY_MODE && i.mode.is_some_and(|m| m & 0o222 == 0),
                )
                .set(
                    "caibx",
                    cx.map(|c| {
                        Obj::new()
                            .set("file", c.name.as_str())
                            .set("size", c.size)
                            .build()
                    }),
                )
                .set("uki", uki.map(|u| u.path.clone()))
                .set("etc_mstack", mstacks.contains(&ms))
                .set(
                    "etc_mstack_rw_data",
                    sys.is_dir(&at(&format!("{MSTACK_DIR}/{ms}/rw/data"))),
                )
                .set("origin_file", sys.exists(&origin_file))
                .set("origin", origin)
                .build()
        })
        .collect();
    // Half versions: a UKI with no image file, an image file with no UKI.
    let image_versions: Vec<String> = images.iter().map(|i| version_of(i)).collect();
    let mut halves: Vec<Value> = Vec::new();
    for u in ukis {
        if !image_versions.contains(&u.m.version) && !halves.iter().any(|h| h.get("version").and_then(Value::as_str) == Some(u.m.version.as_str())) {
            halves.push(
                Obj::new()
                    .set("version", u.m.version.as_str())
                    .set("has", "uki")
                    .set("missing", kde_image_name(&u.m.version))
                    .set("booted", booted == Some(u.m.version.as_str()))
                    .build(),
            );
        }
    }
    for v in &image_versions {
        if !ukis.iter().any(|u| &u.m.version == v) {
            halves.push(
                Obj::new()
                    .set("version", v.as_str())
                    .set("has", "image")
                    .set("missing", format!("EFI/Linux/kde-linux_{v}.efi"))
                    .set("booted", booted == Some(v.as_str()))
                    .build(),
            );
        }
    }
    for h in &halves {
        let v = h.get("version").and_then(Value::as_str).unwrap_or("");
        let has = h.get("has").and_then(Value::as_str).unwrap_or("");
        let missing = h.get("missing").and_then(Value::as_str).unwrap_or("");
        warnings.push(format!(
            "KDE Linux version {v} is half there: it has its {}, not {missing}{}",
            if has == "uki" { "UKI" } else { "image file" },
            if h.get("booted") == Some(&Value::Bool(true)) {
                " (the booted version: load refuses this dump)"
            } else {
                " (an interrupted update, kept as is)"
            }
        ));
    }
    let etc_snapshots: Vec<String> = sys
        .list(&at(SYSTEM_SUBVOL))
        .into_iter()
        .filter(|n| {
            n.strip_prefix(".etc.")
                .is_some_and(|e| !e.is_empty() && e.bytes().all(|c| c.is_ascii_digit()))
        })
        .collect();
    let debug: Vec<String> = entries
        .iter()
        .filter(|e| e.starts_with("@kde-linux_debug_"))
        .cloned()
        .collect();
    let layer0 = booted.and_then(|v| sys.readlink(&at(&format!("{MSTACK_DIR}/etc_{v}.mstack/layer@0"))));
    let top_dev = top_mount.and_then(|m| (inp.device)(m));
    let esp_mount = esp.as_ref().and_then(|(_, at_)| {
        inp.mounts
            .iter()
            .rev()
            .find(|m| m.target == *at_ && m.fstype == "vfat")
    });
    let sources = (refusals.is_empty())
        .then(|| {
            let tm = top_mount?;
            let em = esp_mount?;
            let (edev, emnt) = esp.clone()?;
            Some(vec![
                BackupSource {
                    path: "/".into(),
                    from: Some(tm.target.clone()),
                    device: top_dev.clone(),
                    fstype: tm.fstype.clone(),
                    source: tm.source.clone(),
                    subvol: Some("/".into()),
                    mount: None,
                    note: Some(format!(
                        "KDE Linux: / of the backup tree is the btrfs top level, mounted at {}: the image files, etc.mstack.v (the /etc layers) and @system (the running /) with its nested subvolumes; /usr and /etc are views of it, not sources (docs/design/kde-linux.md)",
                        tm.target
                    )),
                },
                BackupSource {
                    path: format!("/{SYSTEM_SUBVOL}/boot"),
                    from: Some(emnt.clone()),
                    device: Some(edev),
                    fstype: em.fstype.clone(),
                    source: em.source.clone(),
                    subvol: None,
                    mount: None,
                    note: Some(format!(
                        "KDE Linux: the ESP, mounted at {emnt} by gpt-auto, is @system/boot in the backup tree: systemd-boot, loader.conf and one UKI per version"
                    )),
                },
            ])
        })
        .flatten();
    let tree_devices: Vec<String> = top_dev
        .iter()
        .cloned()
        .chain(esp.as_ref().map(|e| e.0.clone()))
        .collect();
    let json = Obj::new()
        .set("top", top.clone())
        .set("top_device", top_dev.clone())
        .set("usr_backing_file", backing.clone())
        .set("variant_id", variant)
        .set(
            "layout",
            refusals.is_empty().then_some("rootfsv3-etc-mstack"),
        )
        .set("booted_version", booted)
        .set("images", images_json)
        .set("mstacks", mstacks.clone())
        .set("etc_snapshots", etc_snapshots)
        .set("debug_subvolumes", debug)
        .set("layer0", layer0)
        .set(
            "restored_marker_present",
            sys.is_file(&at(crate::load::kdelinux::MARKER)),
        )
        .set("half_versions", halves)
        .set(
            "esp",
            esp.as_ref().map(|(d, m)| {
                Obj::new()
                    .set("device", d.as_str())
                    .set("mounted_at", m.as_str())
                    .build()
            }),
        )
        .set("refusals", refusals.clone())
        .build();
    Some(KdeLinux {
        json,
        refusals,
        warnings,
        sources,
        tree_devices,
    })
}

/// Why a dump can't be used, when it's KDE Linux's and refused.
pub(crate) fn refusal(v: &Value) -> Option<String> {
    let r: Vec<&str> = v
        .at(&["boot", "sysupdate", "kdelinux", "refusals"])
        .and_then(Value::as_array)?
        .iter()
        .filter_map(Value::as_str)
        .collect();
    (!r.is_empty()).then(|| r.join("; "))
}

/// boot with `boot.sysupdate` added, and what it says of the rest: the
/// initramfs is prebuilt, inside each UKI; systemd-boot boots Type #2
/// entries (the UKIs).
pub(crate) fn add_to_boot(mut boot: Value, d: Option<&Dump>) -> Value {
    let Some(d) = d else { return boot };
    let paths: Vec<String> = d.ukis.iter().map(|u| u.path.clone()).collect();
    if !paths.is_empty() {
        if let Some(i) = boot.get_mut("initramfs") {
            i.put("generator", "prebuilt");
            i.put("in", "uki");
            i.put("ukis", paths.clone());
        }
        if let Some(l) = boot.get_mut("loader") {
            if l.get("name").and_then(Value::as_str) == Some("systemd-boot") {
                l.put("type2", true);
                l.put("ukis", paths);
            }
        }
    }
    boot.put("sysupdate", d.json.clone());
    boot
}

#[cfg(test)]
pub(crate) mod tests;
