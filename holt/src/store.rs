//! Store systems: NixOS and GNU Guix System, whose whole system is a store
//! of immutable items (/nix/store, /gnu/store), with generations as
//! profiles, the current one at /run/current-system, and the tools only in
//! that profile (docs/design/guix.md, decision 8).  load treats them alike:
//! nothing is rebuilt (each generation's kernel and initrd are store items
//! or copies the restore brings back), nothing runs in the chroot on UEFI
//! (its tools are on PATH only through /run/current-system, which the
//! chroot hasn't), and BIOS GRUB's boot code comes from the store's own
//! GRUB.  A third such system is a row in `SYSTEMS`.
//!
//! The dump side here is Guix's: its packages from the current
//! generation's profile manifest (there is no package database), and
//! boot.store, with each generation's boot parameters and grub.cfg's
//! searches, for load's check that the restored grub.cfg still finds its
//! file systems.  Both are S-expressions, read by `parse_sexps`.  NixOS's
//! dump records neither (its golden plan stays as it is).

use crate::json::{Obj, Value};
use crate::sys::Sys;

/// One store system, as load names and boots it.
#[derive(Debug)]
pub struct StoreSystem {
    /// os-release's ID.
    pub id: &'static str,
    /// How the plan's notes and problems name it.
    pub name: &'static str,
    /// The store.
    pub store: &'static str,
    /// What the plan calls the store.
    pub store_name: &'static str,
    /// The current system's tool directories (sys.rs's SYSTEM_PATH holds
    /// them, so the dump runs btrfs, cryptsetup and efibootmgr from there).
    pub profile_bins: &'static [&'static str],
    /// BIOS GRUB: grub-install's path in a GRUB store item (NixOS's store
    /// keeps the GRUB it installed with), or None, where the store has no
    /// GRUB and the source's own boot.img and core.img in
    /// /boot/grub/i386-pc are written by the rescue's grub-bios-setup
    /// (Guix: `guix system init` ran the installer's grub-install, and a
    /// reconfigure's GRUB is garbage-collectable).
    pub grub_install: Option<&'static str>,
    /// The initramfs step's note, on UEFI and on BIOS.
    pub initramfs_uefi: &'static str,
    pub initramfs_bios: &'static str,
    /// UEFI GRUB's note: nothing installed, only the boot entry made.
    pub uefi_grub: &'static str,
    /// BIOS GRUB's note (NixOS's plan has none).
    pub bios_grub: Option<&'static str>,
    /// systemd-boot's note, for a system that can boot by it.
    pub systemd_boot: Option<&'static str>,
}

pub const SYSTEMS: &[StoreSystem] = &[
    StoreSystem {
        id: "nixos",
        name: "NixOS",
        store: "/nix/store",
        store_name: "the Nix store",
        profile_bins: &["/run/current-system/sw/sbin", "/run/current-system/sw/bin"],
        grub_install: Some("bin/grub-install"),
        initramfs_uefi: "NixOS: each generation's kernel and initrd came back with the restore, where its boot loader's entries name them; nothing to rebuild",
        initramfs_bios: "NixOS: each generation's kernel and initrd are in /boot/kernels, restored with /boot; nothing to rebuild",
        uefi_grub: "NixOS: UEFI GRUB's EFI image and grub.cfg came back with the ESP and /boot; only the boot entry is made",
        bios_grub: None,
        systemd_boot: Some("NixOS: systemd-boot, its loader entries and every generation's kernel and initrd came back with the ESP; only the boot entry is made"),
    },
    StoreSystem {
        id: "guix",
        name: "Guix System",
        store: "/gnu/store",
        store_name: "the Guix store",
        profile_bins: &["/run/current-system/profile/sbin", "/run/current-system/profile/bin"],
        grub_install: None,
        initramfs_uefi: GUIX_INITRAMFS,
        initramfs_bios: GUIX_INITRAMFS,
        uefi_grub: "Guix System: UEFI GRUB's EFI image came back with the ESP, and grub.cfg (which names each generation's kernel and initrd in the store) with /; only the boot entry is made",
        bios_grub: Some("Guix System: its store holds no GRUB to install with (guix system init ran the installer's); the source's own boot.img and core.img came back in /boot/grub/i386-pc, and the rescue system's grub-bios-setup writes them to each disk; grub.cfg, which names each generation's kernel and initrd in the store, stays as restored"),
        systemd_boot: None,
    },
];

const GUIX_INITRAMFS: &str = "Guix System: each generation's kernel and initrd are store items, restored with /, where grub.cfg names them; nothing to rebuild";

/// The store system os-release's ID names, if it is one.
pub fn system(id: Option<&str>) -> Option<&'static StoreSystem> {
    SYSTEMS.iter().find(|s| Some(s.id) == id)
}

impl StoreSystem {
    /// The shell that sets `g` to the GRUB (a store path, as the chroot
    /// sees it) whose grub-install writes BIOS boot code, or fails saying
    /// there is none: the newest GRUB in the store with an i386-pc
    /// platform, for a system whose store keeps its GRUB (`grub_install`).
    /// NixOS's, kept as its golden plan has it (it sorts by what follows
    /// the first '-' of the whole path, which is in the target's own name:
    /// harmless with one GRUB).
    pub fn bios_grub_pick(&self, t: &str) -> String {
        let (st, name) = (self.store, self.store_name);
        format!(
            "g=$(for d in {t}{st}/*-grub-2.*/lib/grub/i386-pc; do d=${{d%/lib/grub/i386-pc}}; echo \"${{d#*-}} ${{d#{t}}}\"; done | sort -V | tail -n 1 | cut -d' ' -f2); [ -n \"$g\" ] || {{ echo 'no BIOS GRUB in {name}' >&2; exit 1; }}"
        )
    }
}

// --- S-expressions -------------------------------------------------------------------

/// A datum as Guile writes it, as far as Guix's manifest and boot
/// parameters use them.
#[derive(Clone, Debug, PartialEq)]
pub enum Sx {
    Str(String),
    Sym(String),
    Num(String),
    Bool(bool),
    List(Vec<Sx>),
    /// `#vu8(1 2 3)`: a bytevector (a UUID in boot parameters).
    Bytes(Vec<u8>),
}

impl Sx {
    pub fn list(&self) -> Option<&[Sx]> {
        match self {
            Sx::List(l) => Some(l),
            _ => None,
        }
    }
    pub fn str(&self) -> Option<&str> {
        match self {
            Sx::Str(s) => Some(s),
            _ => None,
        }
    }
    pub fn sym(&self) -> Option<&str> {
        match self {
            Sx::Sym(s) => Some(s),
            _ => None,
        }
    }
    /// In a list of `(KEY VALUE...)` fields, the first field named `key`'s
    /// values.
    pub fn field(&self, key: &str) -> Option<&[Sx]> {
        self.list()?.iter().find_map(|f| match f.list() {
            Some([Sx::Sym(k), rest @ ..]) if k == key => Some(rest),
            _ => None,
        })
    }
    /// The text a string, symbol or number stands for, or a
    /// `(string-append ...)` of such (older boot parameters).
    pub fn text(&self) -> Option<String> {
        match self {
            Sx::Str(s) | Sx::Sym(s) | Sx::Num(s) => Some(s.clone()),
            Sx::List(l) if l.first().and_then(Sx::sym) == Some("string-append") => l[1..].iter().map(Sx::text).collect(),
            _ => None,
        }
    }
}

/// Every datum in `text`, until the first that doesn't read (what came
/// before it is kept).  `;` and `#|...|#` comments and `#;DATUM` are
/// skipped; `'X` reads as (quote X); `#(...)` as a list.
pub fn parse_sexps(text: &str) -> Vec<Sx> {
    let mut r = Reader { b: text.as_bytes(), i: 0 };
    let mut out = Vec::new();
    while let Some(d) = r.datum() {
        out.push(d);
    }
    out
}

struct Reader<'a> {
    b: &'a [u8],
    i: usize,
}

impl Reader<'_> {
    fn peek(&self) -> Option<u8> {
        self.b.get(self.i).copied()
    }

    fn skip_space(&mut self) {
        loop {
            match self.peek() {
                Some(c) if c.is_ascii_whitespace() => self.i += 1,
                Some(b';') => {
                    while self.peek().is_some_and(|c| c != b'\n') {
                        self.i += 1;
                    }
                }
                Some(b'#') if self.b.get(self.i + 1) == Some(&b'|') => {
                    self.i += 2;
                    while self.i < self.b.len() && !self.b[self.i..].starts_with(b"|#") {
                        self.i += 1;
                    }
                    self.i = (self.i + 2).min(self.b.len());
                }
                Some(b'#') if self.b.get(self.i + 1) == Some(&b';') => {
                    self.i += 2;
                    self.datum();
                }
                _ => return,
            }
        }
    }

    fn delimiter(c: u8) -> bool {
        c.is_ascii_whitespace() || matches!(c, b'(' | b')' | b'[' | b']' | b'"' | b';')
    }

    fn atom(&mut self) -> String {
        let start = self.i;
        while self.peek().is_some_and(|c| !Self::delimiter(c)) {
            self.i += 1;
        }
        String::from_utf8_lossy(&self.b[start..self.i]).into_owned()
    }

    /// The items up to the closing bracket, which is consumed.
    fn items(&mut self) -> Option<Vec<Sx>> {
        let mut l = Vec::new();
        loop {
            self.skip_space();
            match self.peek()? {
                b')' | b']' => {
                    self.i += 1;
                    return Some(l);
                }
                _ => l.push(self.datum()?),
            }
        }
    }

    fn datum(&mut self) -> Option<Sx> {
        self.skip_space();
        let c = self.peek()?;
        match c {
            b'(' | b'[' => {
                self.i += 1;
                self.items().map(Sx::List)
            }
            b')' | b']' => None,
            b'"' => {
                self.i += 1;
                let mut s: Vec<u8> = Vec::new();
                loop {
                    let c = self.peek()?;
                    self.i += 1;
                    match c {
                        b'"' => break,
                        b'\\' => {
                            let e = self.peek()?;
                            self.i += 1;
                            match e {
                                b'n' => s.push(b'\n'),
                                b't' => s.push(b'\t'),
                                b'r' => s.push(b'\r'),
                                b'a' => s.push(7),
                                b'0' => s.push(0),
                                b'x' => {
                                    // \xHH; (R6RS, Guile's write).
                                    let start = self.i;
                                    while self.peek().is_some_and(|c| c.is_ascii_hexdigit()) {
                                        self.i += 1;
                                    }
                                    let hex = std::str::from_utf8(&self.b[start..self.i]).ok()?;
                                    if self.peek() == Some(b';') {
                                        self.i += 1;
                                    }
                                    let ch = u32::from_str_radix(hex, 16).ok().and_then(char::from_u32)?;
                                    s.extend(ch.to_string().as_bytes());
                                }
                                b'\n' => {
                                    while self.peek().is_some_and(|c| c == b' ' || c == b'\t') {
                                        self.i += 1;
                                    }
                                }
                                e => s.push(e),
                            }
                        }
                        c => s.push(c),
                    }
                }
                Some(Sx::Str(String::from_utf8_lossy(&s).into_owned()))
            }
            b'\'' | b'`' | b',' => {
                self.i += 1;
                if c == b',' && self.peek() == Some(b'@') {
                    self.i += 1;
                }
                let q = match c {
                    b'\'' => "quote",
                    b'`' => "quasiquote",
                    _ => "unquote",
                };
                Some(Sx::List(vec![Sx::Sym(q.into()), self.datum()?]))
            }
            b'#' => match self.b.get(self.i + 1) {
                Some(b'(') => {
                    self.i += 2;
                    self.items().map(Sx::List)
                }
                Some(b'v') if self.b[self.i..].starts_with(b"#vu8(") => {
                    self.i += 5;
                    let items = self.items()?;
                    items.iter().map(|x| if let Sx::Num(n) = x { n.parse::<u8>().ok() } else { None }).collect::<Option<Vec<u8>>>().map(Sx::Bytes)
                }
                _ => match self.atom().as_str() {
                    "#t" | "#true" => Some(Sx::Bool(true)),
                    "#f" | "#false" => Some(Sx::Bool(false)),
                    other => Some(Sx::Sym(other.to_string())),
                },
            },
            _ => {
                let a = self.atom();
                if a.is_empty() {
                    return None;
                }
                let digits = a.strip_prefix(['-', '+']).unwrap_or(&a);
                Some(if digits.starts_with(|c: char| c.is_ascii_digit()) { Sx::Num(a) } else { Sx::Sym(a) })
            }
        }
    }
}

// --- Guix: the profile manifest ---------------------------------------------------------

/// A manifest's packages (name, version, output, store path), from
/// `(manifest (version 3|4) (packages ((NAME VERSION OUTPUT PATH ...) ...)))`:
/// entries are positional, and whatever follows the path (propagated
/// inputs, search paths, properties) is left alone.  Unknown versions are
/// read the same way.
pub fn parse_manifest(text: &str) -> Vec<(String, String, String, String)> {
    let mut out = Vec::new();
    let Some(m) = parse_sexps(text).into_iter().find(|d| d.list().and_then(|l| l.first()).and_then(Sx::sym) == Some("manifest")) else { return out };
    let Some([Sx::List(pkgs), ..]) = m.field("packages") else { return out };
    for p in pkgs {
        if let Some([Sx::Str(n), Sx::Str(v), Sx::Str(o), Sx::Str(path), ..]) = p.list() {
            out.push((n.clone(), v.clone(), o.clone(), path.clone()));
        }
    }
    out
}

/// Whether the dump's root is a Guix System: a store, Guix's state
/// directory, and a system profile.
pub fn is_guix(sys: &Sys) -> bool {
    sys.is_dir("/gnu/store") && sys.is_dir("/var/guix") && sys.exists(GUIX_SYSTEM_PROFILE)
}

/// The system profile: a link to system-N-link, itself a link into the store.
pub const GUIX_SYSTEM_PROFILE: &str = "/var/guix/profiles/system";

/// `abs` with every link followed under the dump's root (a link into the
/// store is absolute, and the dump may run on another system's root).
fn resolve(sys: &Sys, abs: &str) -> Option<String> {
    crate::ostree::resolve(sys, "", abs)
}

/// The current generation's packages, from its profile's manifest
/// (/var/guix/profiles/system/profile/manifest, resolved under the dump's
/// root, never through /run).  (name, version, ""): the profile has no
/// architecture; an output other than "out" is NAME:OUTPUT, as `guix
/// package -I` prints it.
pub fn guix_packages(sys: &Sys) -> Vec<(String, String, String)> {
    let Some(m) = resolve(sys, &format!("{GUIX_SYSTEM_PROFILE}/profile/manifest")).and_then(|p| sys.read(&p)) else { return Vec::new() };
    let mut out: Vec<(String, String, String)> =
        parse_manifest(&m).into_iter().map(|(n, v, o, _)| (if o == "out" { n } else { format!("{n}:{o}") }, v, String::new())).collect();
    out.sort();
    out.dedup();
    out
}

// --- Guix: generations and their boot parameters ------------------------------------------

/// A device as boot parameters write it: a string (a device path or, in
/// --root=, a UUID or label), `(uuid TYPE #vu8(...))`, `(file-system-label
/// "L")`, or #f.  (kind, value): kind "uuid", "label", "device" (a path)
/// or "name" (another string: --root='s UUID or label).
pub fn device_of(d: &Sx) -> Option<(&'static str, String)> {
    match d {
        Sx::Str(s) => Some((if s.starts_with('/') { "device" } else { "name" }, s.clone())),
        Sx::List(l) => match l.first().and_then(Sx::sym) {
            Some("uuid") => {
                let ty = l.get(1).and_then(Sx::sym).unwrap_or("");
                let v = l.iter().skip(1).find_map(|x| match x {
                    Sx::Bytes(b) => Some(uuid_text(b, ty)),
                    Sx::Str(s) => Some(s.clone()),
                    _ => None,
                })?;
                Some(("uuid", v))
            }
            Some("file-system-label") => Some(("label", l.get(1)?.text()?)),
            _ => None,
        },
        _ => None,
    }
}

/// A UUID's bytes as Guix's uuid->string prints them: an iso9660 one (16
/// ASCII digits, its creation time) as 1970-01-01-19-11-33-83, as the
/// ISO's fstab has it; other 16 bytes in DCE form (lower case); 4 bytes, FAT's serial, as fat-uuid->string does (two
/// little-endian halves, high first, upper case: #vu8(171 205 18 52) is
/// 3412-CDAB); anything else as hex.
fn uuid_text(b: &[u8], ty: &str) -> String {
    let h = crate::bytes::hex(b);
    match b.len() {
        16 if ty == "iso9660" && b.iter().all(u8::is_ascii_digit) => {
            let d = String::from_utf8_lossy(b);
            format!("{}-{}-{}-{}-{}-{}-{}", &d[..4], &d[4..6], &d[6..8], &d[8..10], &d[10..12], &d[12..14], &d[14..])
        }
        16 => format!("{}-{}-{}-{}-{}", &h[..8], &h[8..12], &h[12..16], &h[16..20], &h[20..]),
        4 if matches!(ty, "fat" | "fat32" | "fat16" | "vfat" | "") => format!("{:02X}{:02X}-{:02X}{:02X}", b[3], b[2], b[1], b[0]),
        _ => h,
    }
}

fn device_json(d: Option<&Sx>) -> Value {
    match d.and_then(device_of) {
        Some((k, v)) => Obj::new().set("kind", k).set("value", v).build(),
        None => Value::Null,
    }
}

/// One generation's boot parameters (its `parameters` file):
/// `(boot-parameters (version 1) (label L) (root-device D) (kernel K)
/// (kernel-arguments (A ...)) (initrd I) (bootloader-name B) (store (device
/// D) (mount-point M) (directory-prefix P) ...) ...)`, version 0 or 1 (or
/// any: the fields are read by name).  In 1.5.0's (version 1) the kernel
/// arguments are only the configuration's own: the root is root-device,
/// and grub.cfg adds root=, gnu.system= and gnu.load=; older ones held
/// --root=, --system= and --load= here.  Fields it lacks are
/// null; the label is `title` here (redaction takes `label` keys for file
/// system labels).
pub fn parameters_json(text: &str) -> Option<Obj> {
    let p = parse_sexps(text).into_iter().find(|d| d.list().and_then(|l| l.first()).and_then(Sx::sym) == Some("boot-parameters"))?;
    let one = |k: &str| p.field(k).and_then(|v| v.first());
    let text_of = |k: &str| one(k).and_then(Sx::text);
    let args: Vec<String> = one("kernel-arguments").and_then(Sx::list).map(|l| l.iter().filter_map(Sx::text).collect()).unwrap_or_default();
    let store = Sx::List(p.field("store").map(<[Sx]>::to_vec).unwrap_or_default());
    let store_one = |k: &str| store.field(k).and_then(|v| v.first());
    Some(
        Obj::new()
            .set("version", one("version").and_then(Sx::text))
            .set("title", text_of("label"))
            .set("root_device", device_json(one("root-device")))
            .set("kernel", text_of("kernel"))
            .set("kernel_arguments", args)
            .set("initrd", text_of("initrd"))
            .set("bootloader_name", text_of("bootloader-name"))
            .set("store_device", device_json(store_one("device")))
            .set("store_mount_point", store_one("mount-point").and_then(Sx::text))
            .set("store_directory_prefix", store_one("directory-prefix").and_then(Sx::text)),
    )
}

/// N in `system-N-link`.
fn generation_number(name: &str) -> Option<u64> {
    name.strip_prefix("system-")?.strip_suffix("-link")?.parse().ok()
}

/// What grub.cfg's `search` commands look for ({kind: fs-uuid, label or
/// file; value}), and the root every `linux` line names (Guix 1.5.0's
/// `root=`, older Guix's `--root=`; a `UUID=` or `LABEL=` before it is
/// dropped), each once.  Comment lines (`# search --file ...` for the
/// locales) are skipped.
pub fn grub_cfg_searches(text: &str) -> (Vec<(String, String)>, Vec<String>) {
    let mut searches: Vec<(String, String)> = Vec::new();
    let mut roots: Vec<String> = Vec::new();
    for raw in text.lines() {
        let w = crate::boot::shell_words(raw.trim());
        let Some(cmd) = w.first().map(String::as_str) else { continue };
        let found = match cmd {
            "search.fs_uuid" => w.get(1).map(|v| ("fs-uuid", v.clone())),
            "search.fs_label" => w.get(1).map(|v| ("label", v.clone())),
            "search.file" => w.get(1).map(|v| ("file", v.clone())),
            "search" => {
                let has = |a: &[&str]| w.iter().any(|x| a.contains(&x.as_str()));
                let kind = if has(&["--fs-uuid", "-u"]) {
                    "fs-uuid"
                } else if has(&["--label", "-l"]) {
                    "label"
                } else {
                    "file"
                };
                w.iter().skip(1).rfind(|x| !x.starts_with('-')).map(|v| (kind, v.clone()))
            }
            "linux" | "linuxefi" => {
                for a in &w[1..] {
                    if let Some(r) = root_arg(a) {
                        if !roots.iter().any(|x| x == r) {
                            roots.push(r.to_string());
                        }
                    }
                }
                None
            }
            _ => None,
        };
        if let Some((k, v)) = found {
            if !searches.iter().any(|(a, b)| a == k && *b == v) {
                searches.push((k.to_string(), v));
            }
        }
    }
    (searches, roots)
}

/// The root a kernel argument names: `root=R` (Guix 1.5.0) or `--root=R`
/// (older), R without a `UUID=` or `LABEL=` prefix.
pub fn root_arg(a: &str) -> Option<&str> {
    let r = a.strip_prefix("--root=").or_else(|| a.strip_prefix("root="))?;
    let r = r.strip_prefix("UUID=").or_else(|| r.strip_prefix("LABEL=")).unwrap_or(r);
    (!r.is_empty()).then_some(r)
}

/// boot.store on Guix System (None elsewhere): the store, the system
/// profile, the current generation's number, each generation's boot
/// parameters, the current generation's GRUB (its manifest's `grub-pc`,
/// `grub` or `grub-efi`, when that has an i386-pc platform; an installed
/// system's profile may list none, nor its store hold one), whether
/// /boot/grub/i386-pc holds the boot.img and core.img grub-install wrote
/// there (what load's grub-bios-setup writes to the new disk), and
/// /boot/grub/grub.cfg's searches and the roots its `linux` lines name.
pub fn guix_json(sys: &Sys) -> Option<Value> {
    if !is_guix(sys) {
        return None;
    }
    let current = sys.readlink(GUIX_SYSTEM_PROFILE).and_then(|l| generation_number(l.rsplit('/').next().unwrap_or(&l)));
    let mut gens: Vec<(u64, String)> = sys.list("/var/guix/profiles").into_iter().filter_map(|n| generation_number(&n).map(|g| (g, n))).collect();
    gens.sort();
    let list: Vec<Value> = gens
        .iter()
        .map(|(n, name)| {
            let link = format!("/var/guix/profiles/{name}");
            let dir = resolve(sys, &link);
            let mut o = Obj::new().set("number", *n).set("system", sys.readlink(&link));
            match dir.as_deref().and_then(|d| sys.read(&format!("{d}/parameters"))).and_then(|t| parameters_json(&t)) {
                Some(p) => o.0.extend(p.0),
                None => {
                    sys.warn(format!("Guix: {link}'s boot parameters can't be read"));
                    o.put("parameters", Value::Null);
                }
            }
            o.build()
        })
        .collect();
    let grub = resolve(sys, &format!("{GUIX_SYSTEM_PROFILE}/profile/manifest"))
        .and_then(|p| sys.read(&p))
        .map(|m| parse_manifest(&m))
        .unwrap_or_default()
        .into_iter()
        .find(|(n, _, o, path)| matches!(n.as_str(), "grub-pc" | "grub" | "grub-efi") && o == "out" && sys.is_dir(&format!("{path}/lib/grub/i386-pc")))
        .map(|(_, _, _, path)| path);
    let cfg = "/boot/grub/grub.cfg";
    let grub_cfg = resolve(sys, cfg).and_then(|p| sys.read(&p)).map(|t| {
        let (searches, roots) = grub_cfg_searches(&t);
        Obj::new().set("path", cfg).set("searches", searches.into_iter().map(|(k, v)| Obj::new().set("kind", k).set("value", v).build()).collect::<Vec<_>>()).set("roots", roots).build()
    });
    Some(
        Obj::new()
            .set("system", "guix")
            .set("store", "/gnu/store")
            .set("profile", GUIX_SYSTEM_PROFILE)
            .set("current", current)
            .set("generations", list)
            .set("grub", grub)
            .set("boot_img", sys.is_file("/boot/grub/i386-pc/boot.img"))
            .set("core_img", sys.is_file("/boot/grub/i386-pc/core.img"))
            .set("grub_cfg", grub_cfg)
            .build(),
    )
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::sys::FakeRunner;
    use crate::testutil::Tree;

    #[test]
    fn reader() {
        let d = parse_sexps("; a comment\n(a \"b\\\"c\\x41;\" 12 -3 #t #f #vu8(1 255) #(x y) 'q [z]) #| block |# #;(skipped) last");
        assert_eq!(
            d,
            vec![
                Sx::List(vec![
                    Sx::Sym("a".into()),
                    Sx::Str("b\"cA".into()),
                    Sx::Num("12".into()),
                    Sx::Num("-3".into()),
                    Sx::Bool(true),
                    Sx::Bool(false),
                    Sx::Bytes(vec![1, 255]),
                    Sx::List(vec![Sx::Sym("x".into()), Sx::Sym("y".into())]),
                    Sx::List(vec![Sx::Sym("quote".into()), Sx::Sym("q".into())]),
                    Sx::List(vec![Sx::Sym("z".into())]),
                ]),
                Sx::Sym("last".into()),
            ]
        );
        // What doesn't read ends the list; what came before stays.
        assert_eq!(parse_sexps("(a) (b"), vec![Sx::List(vec![Sx::Sym("a".into())])]);
        assert_eq!(parse_sexps("(a) )"), vec![Sx::List(vec![Sx::Sym("a".into())])]);
        assert_eq!(parse_sexps("#vu8(1 300)"), vec![]);
        assert!(parse_sexps("").is_empty());
    }

    pub(crate) const MANIFEST: &str = r#";; This file was automatically generated.
(manifest
  (version 4)
  (packages
    (("grub" "2.12" "out" "/gnu/store/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-grub-2.12"
      (propagated-inputs
        (("gettext-minimal" "0.23" "out" "/gnu/store/bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb-gettext-minimal-0.23" (propagated-inputs ()) (search-paths ()))))
      (search-paths ())
      (properties (provenance . "x")))
     ("btrfs-progs" "6.17" "out" "/gnu/store/cccccccccccccccccccccccccccccccc-btrfs-progs-6.17" (propagated-inputs ()) (search-paths ()))
     ("shepherd" "1.0.9" "out" "/gnu/store/dddddddddddddddddddddddddddddddd-shepherd-1.0.9" (propagated-inputs ()) (search-paths ()))
     ("glibc" "2.41" "debug" "/gnu/store/eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee-glibc-2.41-debug" (propagated-inputs ()) (search-paths ())))))
"#;

    #[test]
    fn manifest() {
        let m = parse_manifest(MANIFEST);
        assert_eq!(m.len(), 4, "{m:?}");
        assert_eq!(m[0], ("grub".into(), "2.12".into(), "out".into(), "/gnu/store/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-grub-2.12".into()));
        assert_eq!(m[3].2, "debug");
        // Version 3 is positional too; an entry that isn't is skipped.
        let v3 = "(manifest (version 3) (packages ((\"e2fsprogs\" \"1.47.2\" \"out\" \"/gnu/store/x-e2fsprogs-1.47.2\" (propagated-inputs ()) (search-paths ())) (broken))))";
        assert_eq!(parse_manifest(v3), vec![("e2fsprogs".into(), "1.47.2".into(), "out".into(), "/gnu/store/x-e2fsprogs-1.47.2".into())]);
        assert!(parse_manifest("(not-a-manifest)").is_empty());
        assert!(parse_manifest("(manifest (version 4))").is_empty());
    }

    pub(crate) const PARAMETERS: &str = r#"(boot-parameters
  (version 0)
  (label "GNU with Linux-Libre 6.17.7")
  (root-device (uuid ext4 #vu8(16 17 18 19 20 21 22 23 24 25 26 27 28 29 30 31)))
  (kernel "/gnu/store/kkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkk-linux-libre-6.17.7/bzImage")
  (kernel-arguments ("--root=10111213-1415-1617-1819-1a1b1c1d1e1f" "--system=/gnu/store/ssssssssssssssssssssssssssssssss-system" "--load=/gnu/store/ssssssssssssssssssssssssssssssss-system/boot" "console=ttyS0,115200n8"))
  (initrd "/gnu/store/iiiiiiiiiiiiiiiiiiiiiiiiiiiiiiii-raw-initrd/initrd.cpio.gz")
  (multiboot-modules ())
  (locale "en_US.utf8")
  (store (device #f) (mount-point "/") (directory-prefix #f) (crypto-devices ()))
  (bootloader-name grub-efi)
  (bootloader-menu-entries ()))
"#;

    #[test]
    fn parameters() {
        let p = parameters_json(PARAMETERS).unwrap().build();
        assert_eq!(p.at(&["title"]).and_then(Value::as_str), Some("GNU with Linux-Libre 6.17.7"));
        assert_eq!(p.at(&["root_device", "kind"]).and_then(Value::as_str), Some("uuid"));
        assert_eq!(p.at(&["root_device", "value"]).and_then(Value::as_str), Some("10111213-1415-1617-1819-1a1b1c1d1e1f"));
        assert!(p.at(&["kernel"]).and_then(Value::as_str).unwrap().ends_with("-linux-libre-6.17.7/bzImage"));
        assert_eq!(p.at(&["kernel_arguments"]).and_then(Value::as_array).unwrap().len(), 4);
        assert_eq!(p.at(&["bootloader_name"]).and_then(Value::as_str), Some("grub-efi"));
        assert!(p.at(&["store_device"]).unwrap().is_null());
        assert_eq!(p.at(&["store_mount_point"]).and_then(Value::as_str), Some("/"));
        assert!(p.at(&["store_directory_prefix"]).unwrap().is_null());
        // A label, an older initrd spelled with string-append, a FAT UUID.
        let old = "(boot-parameters (label \"x\") (root-device (file-system-label \"my-root\")) (initrd (string-append \"/gnu/store/i-initrd\" \"/initrd.cpio.gz\")) (store (device (uuid fat #vu8(171 205 18 52)))))";
        let p = parameters_json(old).unwrap().build();
        assert_eq!(p.at(&["root_device", "kind"]).and_then(Value::as_str), Some("label"));
        assert_eq!(p.at(&["root_device", "value"]).and_then(Value::as_str), Some("my-root"));
        assert_eq!(p.at(&["initrd"]).and_then(Value::as_str), Some("/gnu/store/i-initrd/initrd.cpio.gz"));
        assert_eq!(p.at(&["store_device", "value"]).and_then(Value::as_str), Some("3412-CDAB"));
        assert!(p.at(&["kernel"]).unwrap().is_null());
        assert_eq!(device_of(&Sx::Str("/dev/vda2".into())), Some(("device", "/dev/vda2".into())));
        assert!(parameters_json("(manifest)").is_none());
    }

    /// Guix 1.5.0's (the installation ISO's, read on Chopin): version 1,
    /// the configuration's own kernel arguments only, an iso9660 UUID.
    #[test]
    fn parameters_1_5() {
        let iso = "#vu8(49 57 55 48 48 49 48 49 49 57 49 49 51 51 56 51)";
        let t = format!("(boot-parameters (version 1) (label \"GNU with Linux-Libre 6.17.12\") (root-device (uuid iso9660 {iso})) (kernel \"/gnu/store/p-linux-libre-6.17.12/bzImage\") (kernel-arguments (\"quiet\" \"modprobe.blacklist=radeon,amdgpu\")) (initrd \"/gnu/store/i-raw-initrd/initrd.cpio.gz\") (bootloader-name grub-efi) (bootloader-menu-entries ()) (locale \"en_US.utf8\") (store (device (uuid iso9660 {iso})) (mount-point \"/\") (directory-prefix #f) (crypto-devices ())))");
        let p = parameters_json(&t).unwrap().build();
        assert_eq!(p.at(&["version"]).and_then(Value::as_str), Some("1"));
        assert_eq!(p.at(&["root_device", "kind"]).and_then(Value::as_str), Some("uuid"));
        assert_eq!(p.at(&["root_device", "value"]).and_then(Value::as_str), Some("1970-01-01-19-11-33-83"));
        assert_eq!(p.at(&["store_device", "value"]).and_then(Value::as_str), Some("1970-01-01-19-11-33-83"));
        let a: Vec<&str> = p.at(&["kernel_arguments"]).and_then(Value::as_array).unwrap().iter().filter_map(Value::as_str).collect();
        assert_eq!(a, vec!["quiet", "modprobe.blacklist=radeon,amdgpu"]);
        assert_eq!(p.at(&["bootloader_name"]).and_then(Value::as_str), Some("grub-efi"));
        // An iso9660 UUID that isn't digits is DCE hex: unknown, not a failure.
        assert_eq!(uuid_text(&[0xffu8; 16], "iso9660"), "ffffffff-ffff-ffff-ffff-ffffffffffff");
    }

    /// The ISO's grub.cfg, in small: a top-level search outside any entry,
    /// a commented-out search for the locales, then 1.5.0's root=,
    /// gnu.system= and gnu.load=.
    #[test]
    fn grub_cfg_1_5() {
        let u = "31393730-3031-3031-3139-313133333833";
        let cfg = format!("# This file was generated from your Guix configuration.\nsearch --fs-uuid --set {u}\n# search --file --set /gnu/store/l-grub-locales/en@quot.mo\n  # search --label --set commented\nmenuentry \"GNU with Linux-Libre 6.17.12\" {{\n  search --fs-uuid --set {u}\n  linux /gnu/store/p-linux-libre-6.17.12/bzImage root={u} gnu.system=/gnu/store/s-system gnu.load=/gnu/store/s-system/boot quiet modprobe.blacklist=radeon,amdgpu\n  initrd /gnu/store/i/initrd.cpio.gz\n}}\n");
        let (s, r) = grub_cfg_searches(&cfg);
        assert_eq!(s, vec![("fs-uuid".to_string(), u.to_string())]);
        assert_eq!(r, vec![u.to_string()]);
        assert_eq!(root_arg("root=UUID=abc"), Some("abc"));
        assert_eq!(root_arg("--root=LABEL=x"), Some("x"));
        assert_eq!(root_arg("root="), None);
        assert_eq!(root_arg("rootflags=subvol=@"), None);
        assert_eq!(root_arg("gnu.system=/gnu/store/s"), None);
    }

    /// Older Guix's --root=, --system= and --load=.
    #[test]
    fn grub_cfg() {
        let cfg = "# Generated\nsearch --fs-uuid --set 10111213-1415-1617-1819-1a1b1c1d1e1f\nmenuentry \"GNU with Linux-Libre 6.17.7\" {\n  search --fs-uuid --set 10111213-1415-1617-1819-1a1b1c1d1e1f\n  linux /gnu/store/k-linux-libre-6.17.7/bzImage --root=10111213-1415-1617-1819-1a1b1c1d1e1f --system=/gnu/store/s-system console=ttyS0\n  initrd /gnu/store/i/initrd.cpio.gz\n}\nsubmenu \"GNU system, old configurations...\" {\n  menuentry \"x\" {\n    search --label --set my-root\n    search --file --set /gnu/store/k/bzImage\n    linux /@/gnu/store/k/bzImage --root=my-root\n  }\n}\nsearch.fs_uuid ABCD-1234 root\n";
        let (s, r) = grub_cfg_searches(cfg);
        let s: Vec<(&str, &str)> = s.iter().map(|(a, b)| (a.as_str(), b.as_str())).collect();
        assert_eq!(s, vec![("fs-uuid", "10111213-1415-1617-1819-1a1b1c1d1e1f"), ("label", "my-root"), ("file", "/gnu/store/k/bzImage"), ("fs-uuid", "ABCD-1234")]);
        assert_eq!(r, vec!["10111213-1415-1617-1819-1a1b1c1d1e1f", "my-root"]);
    }

    #[test]
    fn table() {
        assert_eq!(system(Some("nixos")).unwrap().store, "/nix/store");
        assert!(system(Some("guix")).unwrap().grub_install.is_none());
        assert_eq!(system(Some("nixos")).unwrap().grub_install, Some("bin/grub-install"));
        assert!(system(Some("debian")).is_none() && system(None).is_none());
        for s in SYSTEMS {
            for d in s.profile_bins {
                assert!(crate::sys::SYSTEM_PATH.split(':').any(|p| p == *d), "{d} isn't on the dump's PATH");
            }
        }
    }

    /// The profile chain resolved under the dump's root (absolute links
    /// into the store), the manifest's packages, and boot.store.
    #[test]
    fn guix_tree() {
        let t = Tree::new("guix-store");
        let sys1 = "/gnu/store/ssssssssssssssssssssssssssssssss-system";
        let sys2 = "/gnu/store/tttttttttttttttttttttttttttttttt-system";
        let prof = "/gnu/store/pppppppppppppppppppppppppppppppp-profile";
        t.file(&format!("{prof}/manifest"), MANIFEST)
            .dir("/gnu/store/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-grub-2.12/lib/grub/i386-pc")
            .file(&format!("{sys1}/parameters"), PARAMETERS)
            .symlink(&format!("{sys1}/profile"), prof)
            .file(&format!("{sys2}/parameters"), PARAMETERS.replace("6.17.7", "6.17.9"))
            .symlink(&format!("{sys2}/profile"), prof)
            .symlink("/var/guix/profiles/system-1-link", sys1)
            .symlink("/var/guix/profiles/system-2-link", sys2)
            .symlink("/var/guix/profiles/system", "system-2-link")
            .file("/boot/grub/grub.cfg", "search --fs-uuid --set 10111213-1415-1617-1819-1a1b1c1d1e1f\n");
        let sys = Sys::new(t.root(), Box::new(FakeRunner::default()), true);
        assert!(is_guix(&sys));
        let p = guix_packages(&sys);
        assert_eq!(p.len(), 4);
        assert!(p.contains(&("glibc:debug".into(), "2.41".into(), String::new())));
        assert!(p.contains(&("grub".into(), "2.12".into(), String::new())));
        let v = guix_json(&sys).unwrap();
        assert_eq!(v.at(&["current"]).and_then(Value::as_u64), Some(2));
        let g = v.at(&["generations"]).and_then(Value::as_array).unwrap();
        assert_eq!(g.len(), 2);
        assert_eq!(g[1].at(&["system"]).and_then(Value::as_str), Some(sys2));
        assert_eq!(g[1].at(&["title"]).and_then(Value::as_str), Some("GNU with Linux-Libre 6.17.9"));
        assert_eq!(v.at(&["grub"]).and_then(Value::as_str), Some("/gnu/store/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-grub-2.12"));
        assert_eq!(v.at(&["grub_cfg", "searches", "0", "kind"]).and_then(Value::as_str), Some("fs-uuid"));
        assert!(sys.warnings().is_empty(), "{:?}", sys.warnings());
        // Not Guix: no store, no packages from it.
        let t = Tree::new("guix-none");
        t.dir("/gnu/store").dir("/var/guix");
        let sys = Sys::new(t.root(), Box::new(FakeRunner::default()), true);
        assert!(!is_guix(&sys) && guix_json(&sys).is_none() && guix_packages(&sys).is_empty());
    }
}
