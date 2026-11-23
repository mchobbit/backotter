//! Every command each writing stage runs, worked out from the layout and
//! the disk mapping before anything is written.  `plan` prints them; the
//! writing stages will run exactly these.
//!
//! Identifiers are the source's (layload.md, "Identity"): partition table
//! and partition GUIDs, md, LUKS, LVM and filesystem UUIDs, labels, and the
//! ZFS pool GUID (vdev GUIDs can't be set).  Features are set on and off as
//! the source had them, never left to the defaults of the tools here.

use super::layout::{Disk, Layout, Part, TableKind};
use super::target::Mapping;
use super::{shell_quote, Options, Stage, RUN_DIR};
use crate::json::Value;
use std::collections::{BTreeMap, BTreeSet};

/// The os-release ids whose GRUB is Debian's (update-grub, and grub-install
/// with Debian's --force-extra-removable).
const DEBIAN_GRUB_IDS: [&str; 8] = ["debian", "ubuntu", "linuxmint", "zorin", "neon", "elementary", "anduinos", "kali"];

/// A closer for a device just let go of (an LV, a LUKS mapping, an md
/// array): udev probes a device when the last writer closes it, and while
/// its worker has it open the device can't be stopped.  So udev is waited
/// for first, and the command is tried up to five times, a second apart.
fn settled(argv: &[&str]) -> Cmd {
    let cmd = argv.iter().map(|a| shell_quote(a)).collect::<Vec<_>>().join(" ");
    Cmd::new(&["sh", "-c", &format!("i=0; until udevadm settle; {cmd}; do i=$((i + 1)); [ $i -lt 5 ] || exit 1; sleep 1; done")])
}

/// A wait for a zvol's /dev/zvol/NAME, which udev's 60-zvol.rules links
/// after `zfs create -V` returns: what's made on it next (Ubuntu's keystore
/// LUKS, a swap) fails without it.  Up to 30 s.
fn zvol_wait(ds: &str) -> Cmd {
    let p = shell_quote(&format!("/dev/zvol/{ds}"));
    Cmd::new(&["sh", "-c", &format!("i=0; until udevadm settle; [ -b {p} ]; do i=$((i + 1)); [ $i -lt 30 ] || {{ echo {p}\": no such block device 30 s after zfs create (udev never linked it)\" >&2; exit 1; }}; sleep 1; done")])
        .why(format!("udev links /dev/zvol/{ds} a moment after zfs create"))
}

/// An LVM command that sees DEVICES only (comma-separated), and no devices
/// file: `CMD --devicesfile "" --devices DEVICES REST...`.
pub(crate) fn lvm_argv<'a>(cmd: &'a str, devices: &'a str, rest: &[&'a str]) -> Vec<&'a str> {
    let mut v = vec![cmd, "--devicesfile", "", "--devices", devices];
    v.extend_from_slice(rest);
    v
}

/// One step: a command, or a file to write (`write` set), or a read-back
/// (`check` set), or only a note.
#[derive(Debug, Clone, PartialEq, Default)]
pub struct Cmd {
    pub argv: Vec<String>,
    pub stdin: Option<String>,
    /// For a file to write: its path; the contents are `stdin`.
    pub write: Option<String>,
    /// For a read-back: the device format just made, and the source's
    /// content it must match (readback.rs).  It runs in-process.
    pub check: Option<(String, Value)>,
    pub why: Option<String>,
    /// Environment variables the command runs with (shown before it).
    pub env: Vec<(String, String)>,
    /// Whether the terminal sees its stderr's `warning:` lines when it
    /// succeeds (the log has all its output either way).
    pub warns: bool,
    /// For a file to write outside RUN_DIR: whether it's made 0600, as
    /// RUN_DIR's own are (a copy of a file that can hold a password).
    pub private: bool,
}

impl Cmd {
    pub fn new<S: AsRef<str>>(argv: &[S]) -> Cmd {
        Cmd { argv: argv.iter().map(|s| s.as_ref().to_string()).collect(), ..Cmd::default() }
    }

    pub fn note(text: impl Into<String>) -> Cmd {
        Cmd { why: Some(text.into()), ..Cmd::default() }
    }

    pub fn write_file(path: &str, contents: String) -> Cmd {
        Cmd { write: Some(path.to_string()), stdin: Some(contents), ..Cmd::default() }
    }

    /// The file it writes is 0600 (`write_file`): a copy of a file that can
    /// hold a password.
    pub fn private(mut self) -> Cmd {
        self.private = true;
        self
    }

    /// The read-back of DEV, just made from the source's CONTENT.
    pub fn read_back(dev: &str, content: &Value) -> Cmd {
        Cmd { check: Some((dev.to_string(), content.clone())), ..Cmd::default() }
    }

    pub fn why(mut self, w: impl Into<String>) -> Cmd {
        self.why = Some(w.into());
        self
    }

    /// Its stderr's lines with `warning:` in them go to the terminal too,
    /// when it succeeds.
    pub fn warns(mut self) -> Cmd {
        self.warns = true;
        self
    }

    pub fn input(mut self, s: String) -> Cmd {
        self.stdin = Some(s);
        self
    }

    pub fn is_note(&self) -> bool {
        self.argv.is_empty() && self.write.is_none() && self.check.is_none()
    }

    /// The step as a person would type it in a shell (a read-back, which
    /// bko-holt makes itself, as a comment).
    pub fn shell(&self) -> String {
        let mut out = String::new();
        if let Some(w) = &self.why {
            for l in w.lines() {
                out += &format!("# {l}\n");
            }
        }
        if let Some((dev, content)) = &self.check {
            return out + &super::readback::line(dev, content);
        }
        if self.is_note() {
            return out;
        }
        let line = match &self.write {
            Some(p) => format!("cat > {}", shell_quote(p)),
            None if self.env.iter().all(|(k, _)| valid_env_key(k)) => self
                .env
                .iter()
                .map(|(k, v)| format!("{k}={}", shell_quote(v)))
                .chain(self.argv.iter().map(|a| shell_quote(a)))
                .collect::<Vec<_>>()
                .join(" "),
            None => std::iter::once("env".to_string())
                .chain(self.env.iter().filter(|(k, _)| !k.is_empty() && !k.contains('=')).map(|(k, v)| shell_quote(&format!("{k}={v}"))))
                .chain(self.argv.iter().map(|a| shell_quote(a)))
                .collect::<Vec<_>>()
                .join(" "),
        };
        match &self.stdin {
            Some(s) => {
                let mut eof = String::from("EOF");
                let mut n = 1u32;
                while s.lines().any(|l| l == eof) {
                    eof = format!("EOF_{n}");
                    n += 1;
                }
                out += &format!("{line} <<'{eof}'\n{s}");
                if !s.ends_with('\n') {
                    out.push('\n');
                }
                out += &format!("{eof}\n");
            }
            None => out += &format!("{line}\n"),
        }
        out
    }
}

pub(crate) fn safe_target_subpath(p: &str) -> bool {
    p.starts_with('/') && !p.contains('\0') && !p.split('/').any(|c| c == "..")
}

/// Whether crypttab's key field names a file the restore brings back: an
/// absolute path in the restored tree, not systemd's path:device form, a
/// raw key device or a USB stick under /dev, or /proc, /sys or /run.
fn restorable_key_file(k: &str) -> bool {
    !k.contains(':') && safe_target_subpath(k) && !["/dev/", "/proc/", "/sys/", "/run/"].iter().any(|d| k.starts_with(d))
}

/// The plan problem for a LUKS container whose key file the restore can't
/// bring back and no --key-file covers.
fn needs_key_file(open_as: &str, k: &str) -> String {
    format!("{open_as}: crypttab's key {k:?} isn't a file the restore brings back (it's on another device, or not a path in the restored tree): give --key-file {open_as}=FILE")
}

/// The file name of a container's temporary key under RUN_DIR/keys, the
/// same in the format and the boot stage; None for a name that can't be one.
fn temp_key_name(open_as: &str) -> Option<String> {
    let n = open_as.replace('/', "_");
    (!n.is_empty() && !matches!(n.as_str(), "." | "..") && !n.contains('\0')).then_some(n)
}

fn valid_env_key(k: &str) -> bool {
    let mut b = k.bytes();
    b.next().is_some_and(|c| c.is_ascii_alphabetic() || c == b'_') && b.all(|c| c.is_ascii_alphanumeric() || c == b'_')
}

fn dq_escape(s: &str) -> String {
    let mut out = String::with_capacity(s.len());
    for c in s.chars() {
        if matches!(c, '\\' | '"' | '$' | '`') {
            out.push('\\');
        }
        out.push(c);
    }
    out
}

pub struct Built {
    pub stages: Vec<(Stage, Vec<Cmd>)>,
    pub problems: Vec<String>,
    pub notes: Vec<String>,
    /// What the stages make, for `status` to look for: assemble's device
    /// nodes and pools, format's filesystems (node, type, UUID) and ZFS
    /// datasets, and mount's mount points.
    pub made: Vec<(String, String)>,
    pub pools: Vec<String>,
    pub filesystems: Vec<(String, String, Option<String>)>,
    pub datasets: Vec<String>,
    pub mount_points: Vec<String>,
}

impl Built {
    pub fn stage(&self, s: Stage) -> &[Cmd] {
        self.stages.iter().find(|(st, _)| *st == s).map(|(_, c)| c.as_slice()).unwrap_or(&[])
    }
}

fn s(v: &Value, k: &str) -> Option<String> {
    v.get(k).and_then(Value::as_str).map(str::to_string)
}

/// A number given as a number or as a decimal string (LUKS2 keeps its
/// offsets and sizes as strings).
fn num(v: &Value, k: &str) -> Option<u64> {
    match v.get(k)? {
        Value::Str(t) => t.parse().ok(),
        x => x.as_u64(),
    }
}

/// The device node of partition `n` of disk `disk` (a kernel name).
pub fn part_path(disk: &str, n: u32) -> String {
    if disk.ends_with(|c: char| c.is_ascii_digit()) {
        format!("/dev/{disk}p{n}")
    } else {
        format!("/dev/{disk}{n}")
    }
}

/// The node of partition `n` of target disk `t`, for source disk `d`: the
/// Linux kernel's name, or on another system its own (crate::other).
pub fn part_node(layout: &Layout, d: &crate::load::layout::Disk, t: &str, n: u32) -> String {
    match crate::other::Family::of_layout(&layout.v) {
        Some(fam) => crate::other::load::part_node(fam, d, t, n),
        None => part_path(t, n),
    }
}

/// Source device names to the device nodes they'll have here.
pub(crate) struct Names<'a> {
    pub(crate) layout: &'a Layout,
    pub(crate) mapping: &'a Mapping,
    pub(crate) made: BTreeMap<String, String>,
}

impl Names<'_> {
    pub(crate) fn dev(&self, src: &str) -> Option<String> {
        if let Some(p) = self.made.get(src) {
            return Some(p.clone());
        }
        for d in &self.layout.disks {
            let Some(t) = self.mapping.target_of(&d.name) else { continue };
            if d.name == src {
                // illumos names a pool's whole-disk vdev by the disk; ZFS
                // is on its slice, which the restored table has.
                if crate::other::Family::of_layout(&self.layout.v) == Some(crate::other::Family::Illumos) {
                    if let Some(p) = d.parts.iter().find(|p| p.content.get("type").and_then(Value::as_str) == Some("zfs_member")) {
                        return Some(part_node(self.layout, d, t, p.number));
                    }
                    return Some(format!("/dev/dsk/{t}"));
                }
                return Some(format!("/dev/{t}"));
            }
            if let Some(n) = crate::other::load::label_node(self.layout, d, t, src) {
                return Some(n);
            }
            if let Some(p) = d.parts.iter().find(|p| p.name == src) {
                return Some(part_node(self.layout, d, t, p.number));
            }
        }
        None
    }

    /// A source device by a path as the source knew it: /dev/vda3, or a
    /// /dev/disk/by-* link the dump recorded.
    fn src_of_path(&self, path: &str) -> Option<String> {
        let tail = path.strip_prefix("/dev/").unwrap_or(path);
        if self.layout.device(tail).is_some() {
            return Some(tail.to_string());
        }
        if let Some(n) = tail.strip_prefix("mapper/") {
            for d in self.layout.arr(&["storage", "dm_devices"]) {
                if d.at(&["dm", "dm_name"]).and_then(Value::as_str) == Some(n) {
                    return s(d, "name");
                }
            }
        }
        for sect in ["disks", "md_arrays", "dm_devices"] {
            for d in self.layout.arr(&["storage", sect]) {
                let mut all = vec![d];
                all.extend(d.get("partitions").and_then(Value::as_array).into_iter().flatten());
                for x in all {
                    if x.get("links").and_then(Value::as_array).is_some_and(|l| l.iter().any(|v| v.as_str() == Some(path))) {
                        return s(x, "name");
                    }
                }
            }
        }
        None
    }
}

/// A Timeshift snapshot's subvolume (BigLinux's; Mint's or Manjaro's with
/// Timeshift in btrfs mode): `timeshift-btrfs/snapshots/NAME/@...` in the
/// top level, mounted nowhere and in no backup path (shared.rs), so the
/// backup has none of it.  Its NAME (Timeshift's date), or None.  Made, it
/// would be an empty restore point: restoring it would empty `/`.
fn timeshift_snapshot<'a>(path: &'a str, mounts: &[crate::load::shared::Mount]) -> Option<&'a str> {
    let (name, sub) = path.strip_prefix("timeshift-btrfs/snapshots/")?.split_once('/')?;
    if name.is_empty() || !sub.starts_with('@') || sub.contains('/') {
        return None;
    }
    let mounted = mounts.iter().any(|m| m.subvol == path);
    (!mounted && crate::load::shared::backup_path(mounts, path).is_none()).then_some(name)
}

pub fn build(layout: &Layout, opts: &Options, mapping: &Mapping) -> Built {
    let mut b = Builder {
        layout,
        opts,
        names: Names { layout, mapping, made: BTreeMap::new() },
        problems: vec![],
        notes: vec![],
        closers: vec![],
        pools: vec![],
        filesystems: vec![],
        datasets: vec![],
        mount_points: vec![],
        fam: crate::other::Family::of_layout(&layout.v),
        btrfs: BTreeMap::new(),
        timeshift: BTreeMap::new(),
        readonly_later: vec![],
        be_mounts: vec![],
        be_restored: vec![],
    };
    b.be_mounts = b.find_be_mounts();
    b.be_restored = b.find_restored_bes();
    b.be_problems();
    // What the dump couldn't find out because a tool it runs was missing or
    // failed (report::zfs_tools_json, other::firmware_unknown): it would
    // have been a wrong answer, so it is said and refused instead.
    if let Some(e) = layout.str_at(&["storage", "zfs", "tools", "error"]) {
        b.problems.push(format!("ZFS: {e}"));
    }
    if [&["firmware", "mode"][..], &["boot", "loader", "firmware"][..]].iter().any(|p| layout.str_at(p) == Some("unknown")) {
        b.problems.push("the dump couldn't tell UEFI from BIOS (its firmware mode is unknown; its warnings say why), so load can't choose the boot code to install: dump again once that tool works".into());
    }
    b.btrfs_plans();
    b.qubes_note();
    b.qubes_problems();
    b.thin_problems();
    // --reformat's findings (reformat.rs), carried in the edited layout.
    for (k, list) in [("problems", &mut b.problems), ("notes", &mut b.notes)] {
        list.extend(layout.arr(&["load_reformat", k]).iter().filter_map(Value::as_str).map(str::to_string));
    }
    if !opts.reformats.is_empty() && opts.fit_largest.is_some() {
        b.problems.push("--reformat and --fit-largest don't go together: a reformatted filesystem fills its partition as it is, and --fit-largest would resize it from figures the new type doesn't have".into());
    }
    if opts.new_uuids {
        b.problems.push("--new-uuids isn't built yet: it also has to rewrite the restored fstab, crypttab and boot configuration".into());
    }
    if opts.new_machine && super::snapcore::of(&layout.v).is_none() {
        b.problems.push("--new-machine is for an encrypted Ubuntu Core; this layout isn't one".into());
    }
    let partition = b.partition();
    let assemble = b.assemble();
    let format = b.format();
    let mount = b.mount();
    let restore = b.restore();
    let boot = b.boot();
    let close = b.close();
    Built {
        stages: vec![
            (Stage::Partition, partition),
            (Stage::Assemble, assemble),
            (Stage::Format, format),
            (Stage::Mount, mount),
            (Stage::Restore, restore),
            (Stage::Boot, boot),
            (Stage::Close, close),
        ],
        problems: b.problems,
        notes: b.notes,
        made: b.names.made.into_iter().collect(),
        pools: b.pools,
        filesystems: b.filesystems,
        datasets: b.datasets,
        mount_points: b.mount_points,
    }
}

pub(crate) struct Builder<'a> {
    pub(crate) layout: &'a Layout,
    pub(crate) opts: &'a Options,
    pub(crate) names: Names<'a>,
    pub(crate) problems: Vec<String>,
    pub(crate) notes: Vec<String>,
    /// What close undoes, in the order assemble made it.
    pub(crate) closers: Vec<Cmd>,
    pub(crate) pools: Vec<String>,
    pub(crate) filesystems: Vec<(String, String, Option<String>)>,
    pub(crate) datasets: Vec<String>,
    pub(crate) mount_points: Vec<String>,
    /// The source's system, when it isn't Linux (crate::other).
    pub(crate) fam: Option<crate::other::Family>,
    /// Each btrfs's shared snapshots and aliases, by UUID (shared.rs).
    pub(crate) btrfs: BTreeMap<String, crate::load::shared::Plan>,
    /// Each btrfs's Timeshift snapshots (their subvolumes' paths), by UUID:
    /// not in the backup, so not made (timeshift_snapshot).
    pub(crate) timeshift: BTreeMap<String, BTreeSet<String>>,
    /// The datasets (names here) whose readonly=on ds_props held back: set
    /// at the end of the boot stage (load/zfs_be.rs).
    pub(crate) readonly_later: Vec<String>,
    /// The inactive ZFS boot environments' datasets the dump has backup
    /// sources for, and those BEs (load/zfs_be.rs), worked out once.
    pub(crate) be_mounts: Vec<super::zfs_be::BeMount>,
    pub(crate) be_restored: Vec<crate::zfs_be::BootEnv>,
}

// ---- partition ----------------------------------------------------------

/// GPT attribute bits as sfdisk names them.
/// The disk signature of a GPT disk's protective MBR (the dump's first
/// entry's uuid, blkid's SIGNATURE-01), when it isn't zero.
pub fn pmbr_signature(disk: Option<&Value>) -> Option<u32> {
    let u = disk?.at(&["partition_table", "protective_mbr", "entries", "0", "uuid"])?.as_str()?;
    u32::from_str_radix(u.split('-').next()?, 16).ok().filter(|&n| n != 0)
}

pub fn gpt_attrs(a: u64) -> Result<String, u32> {
    let mut out = Vec::new();
    for bit in 0..64u32 {
        if a & (1u64 << bit) == 0 {
            continue;
        }
        match bit {
            0 => out.push("RequiredPartition".to_string()),
            1 => out.push("NoBlockIOProtocol".to_string()),
            2 => out.push("LegacyBIOSBootable".to_string()),
            48..=63 => out.push(format!("GUID:{bit}")),
            _ => return Err(bit),
        }
    }
    Ok(out.join(" "))
}

fn sfdisk_quote(s: &str) -> String {
    format!("\"{}\"", s.replace('\\', "\\\\").replace('"', "\\\""))
}

/// The sfdisk script that makes `d`'s table on `target` (a kernel name).
pub fn sfdisk_script(d: &Disk, target: &str, same_size: bool) -> Result<String, String> {
    let t = d.table.as_ref().ok_or("no partition table")?;
    let mut out = String::new();
    match t.kind {
        TableKind::Gpt => {
            out += "label: gpt\n";
            if let Some(id) = &t.id {
                out += &format!("label-id: {id}\n");
            }
        }
        TableKind::Mbr => {
            out += "label: dos\n";
            if let Some(id) = &t.id {
                out += &format!("label-id: {id}\n");
            }
        }
    }
    out += "unit: sectors\n";
    out += &format!("sector-size: {}\n", d.sector_size);
    if t.kind == TableKind::Gpt {
        if let Some(n) = t.entry_count {
            out += &format!("table-length: {n}\n");
        }
        if let Some(f) = t.first_usable_lba {
            out += &format!("first-lba: {f}\n");
        }
        // On a bigger disk the backup header and last usable LBA move to its
        // end (sfdisk's default); on one the same size they stay put.
        if same_size {
            if let Some(l) = t.last_usable_lba {
                out += &format!("last-lba: {l}\n");
            }
        }
    }
    out.push('\n');
    let mut parts: Vec<&Part> = d.parts.iter().collect();
    parts.sort_by_key(|p| p.number);
    for p in parts {
        let mut f = vec![format!("start={}", p.start_lba), format!("size={}", p.sectors)];
        match t.kind {
            TableKind::Gpt => {
                f.push(format!("type={}", p.ptype.to_ascii_uppercase()));
                if let Some(u) = &p.uuid {
                    f.push(format!("uuid={}", u.to_ascii_uppercase()));
                }
                if let Some(l) = &p.label {
                    f.push(format!("name={}", sfdisk_quote(l)));
                }
                let a = gpt_attrs(p.attributes).map_err(|bit| format!("partition {} has GPT attribute bit {bit}, which sfdisk can't set", p.number))?;
                if !a.is_empty() {
                    f.push(format!("attrs={}", sfdisk_quote(&a)));
                }
            }
            TableKind::Mbr => {
                f.push(format!("type={}", p.ptype.trim_start_matches("0x")));
                if p.bootable {
                    f.push("bootable".into());
                }
            }
        }
        out += &format!("{} : {}\n", part_path(target, p.number), f.join(", "));
    }
    Ok(out)
}

/// How much of a disk's (and each new partition's) start and end the
/// partition stage zeroes.  wipefs erases only the magic it recognizes, on
/// the device it's given; these cover what sits around it: a GPT and its
/// backup at the disk's end, the LVM label and metadata area, LUKS2's two
/// headers, md 1.1/1.2 superblocks at the start and 0.90/1.0 ones at the
/// end, ZFS's two labels at each end, filesystem superblocks.
pub const ZERO_HEAD: u64 = 4 << 20;
pub const ZERO_TAIL: u64 = 1 << 20;

/// What DRACUT_EACH_KERNEL and DRACUT_ONE_IMAGE read from the restored
/// image, as sh, one copy for both (RustAudit1 XH3-01): `l` lsinitrd's
/// listing of the image `lsinitrd LS` names, `m` its kernel modules still
/// in the tree, and with dracut's resume module `a=resume` and `f` a
/// temporary copy of its 95resume.conf (`lsinitrd LSF`), for
/// dracut_keep_args! and dracut_keep_done!.
macro_rules! dracut_keep_read {
    ($ls:literal, $lsf:literal) => {
        concat!(
            "l=$(lsinitrd ",
            $ls,
            " 2>/dev/null); m=; ",
            "for p in $(printf '%s\\n' \"$l\" | sed -n 's|^.* \\(\\(usr/\\)*lib/modules/[^ ]*\\.ko[.a-z0-9]*\\)$|\\1|p'); do [ -e \"/$p\" ] && b=${p##*/} && m=\"$m ${b%%.ko*}\"; done; ",
            "a=; f=; if printf '%s\\n' \"$l\" | sed -n '/^dracut modules:$/,/^=/p' | grep -qx resume; then a=resume; r=$(lsinitrd ",
            $lsf,
            " 2>/dev/null); ",
            "[ -z \"$r\" ] || { f=$(mktemp) && printf '%s\\n' \"$r\" > \"$f\"; } || exit; fi; "
        )
    };
}

/// dracut's arguments for what dracut_keep_read! found.
macro_rules! dracut_keep_args {
    () => {
        "${m:+--add-drivers \"$m\"} ${a:+--add \"$a\"} ${f:+--include \"$f\" /etc/cmdline.d/95resume.conf}"
    };
}

/// Right after dracut: `s` its status, and dracut_keep_read!'s temporary
/// file removed.
macro_rules! dracut_keep_done {
    () => {
        "s=$?; [ -z \"$f\" ] || rm -f \"$f\"; "
    };
}

/// The boot stage's dracut, in the restored system's chroot: each installed
/// kernel's initramfs remade (not --regenerate-all: it takes every
/// directory in /lib/modules, and a removed kernel can leave one behind
/// with only depmod's indexes), with every kernel module the restored image
/// for that kernel had (lsinitrd -k, those still in the tree) as
/// --add-drivers.  Host-only dracut (the default on Fedora, openSUSE and
/// others) takes the modules loaded on the machine it runs on, and that is
/// the rescue system: what its kernel has built in, the source's kernel may
/// have as a module, which host-only then leaves out.  efivarfs is one
/// (built into Ubuntu's kernel, a module in openSUSE Leap 16's): without it
/// in the initramfs, systemd mounts no /sys/firmware/efi/efivars, and the
/// restored machine boots with no UEFI variable readable (mokutil, the
/// boot entries, shim's MOK lists; t2h-20261118T2037-e7d1).  The image
/// still gets the drivers this machine needs.
///
/// The list is all the restored image's modules, not only the ones the
/// rescue system hides: which those are (built into its kernel, or not
/// loaded in it) can't be told from the chroot, and naming them by kind
/// (file systems, efivarfs, crypto) would miss the next one.  It is no
/// longer than what the source's own image had: a host-only image's tens
/// of modules, or a generic one's few thousand, which dracut takes anyway
/// (well under the kernel's 128 KiB for one argument).  The remade image is
/// the source's and this machine's together; the next dracut run on the
/// restored machine makes it host-only for that machine alone.
///
/// So with dracut's resume module: host-only takes it only when this
/// machine has swap on (and the rescue system has none), so an image that
/// had it gets it by --add, and the resume= its host-only command line
/// had (etc/cmdline.d/95resume.conf, from the source's swap) by
/// --include.  Without them the restored machine can't resume from
/// hibernation (docs/design/leap16.md).
pub(crate) const DRACUT_EACH_KERNEL: &str = concat!(
    "n=0; for k in /lib/modules/*; do [ -d \"$k/kernel\" ] || continue; ",
    dracut_keep_read!("-k \"${k##*/}\"", "-k \"${k##*/}\" -f etc/cmdline.d/95resume.conf"),
    "dracut --force --kver \"${k##*/}\" ",
    dracut_keep_args!(),
    "; ",
    dracut_keep_done!(),
    "[ \"$s\" -eq 0 ] || exit \"$s\"; n=$((n + 1)); done; [ \"$n\" -gt 0 ]"
);

/// DRACUT_EACH_KERNEL's dracut for one kernel and one image, both named:
/// `sh -c DRACUT_ONE_IMAGE sh KVER IMAGE`.  Gentoo's (gentoo_initramfs):
/// its 01-gentoo.conf is host-only too, so the image IMAGE was (read by
/// lsinitrd before dracut replaces it) gives the remade one its kernel
/// modules still in the tree, its resume module and its 95resume.conf, as
/// DRACUT_EACH_KERNEL's restored images do.  With no image lsinitrd can
/// read, it is plain `dracut --force IMAGE KVER`.
pub(crate) const DRACUT_ONE_IMAGE: &str = concat!(
    "k=$1; i=$2; ",
    dracut_keep_read!("\"$i\"", "-f etc/cmdline.d/95resume.conf \"$i\""),
    "dracut --force ",
    dracut_keep_args!(),
    " \"$i\" \"$k\"; ",
    dracut_keep_done!(),
    "exit \"$s\""
);

/// EndeavourOS's and Garuda's dracut-rebuild (host-only images, then
/// non-host-only fallbacks), with dracut's resume module kept as
/// DRACUT_EACH_KERNEL keeps it: when a restored host-only image had it, a
/// dracut.conf.d drop-in adds it while dracut-rebuild runs, and is removed
/// after, and dracut.conf.d gets back the mtime it had (a file made and
/// removed in it changes it, and the restored tree is the source's).
/// (Their dracut-rebuild passes --no-hostonly-cmdline: resume= is the
/// kernel command line's.)
pub(crate) const DRACUT_REBUILD_RESUME: &str = "c=/etc/dracut.conf.d/zz-bko-holt-resume.conf; a=; w=; for i in /boot/initramfs-*.img; do case $i in *-fallback.img) continue ;; esac; \
if lsinitrd \"$i\" 2>/dev/null | sed -n '/^dracut modules:$/,/^=/p' | grep -qx resume; then a=1; fi; done; \
if [ -n \"$a\" ] && [ ! -e \"$c\" ]; then w=$(mktemp) && touch -r \"${c%/*}\" \"$w\" && printf 'add_dracutmodules+=\" resume \"\\n' > \"$c\" || exit; fi; dracut-rebuild; s=$?; \
[ -z \"$w\" ] || { rm -f \"$c\" && touch -r \"$w\" \"${c%/*}\" && rm -f \"$w\"; } || [ \"$s\" -ne 0 ] || s=1; exit \"$s\"";

/// initramfs-tools' update-initramfs, with the resume device kept.  With
/// RESUME unset (or auto), its resume hook picks the largest swap in
/// /proc/swaps, and writes it into the image (conf/conf.d/zz-resume-auto),
/// but not in a chroot (ischroot), and the rescue system has no swap on:
/// the remade image would have no resume device.  So the one a restored
/// image had (unmkinitramfs) goes in /etc/initramfs-tools/conf.d under
/// that name while update-initramfs runs, which copies it into each image
/// as it was, and is removed after; conf.d then gets back the mtime it had,
/// as the restored tree is the source's (in t2h-20261118T2305-edbb,
/// mint22's and pop24's conf.d mtime differed).  A RESUME in the
/// environment wouldn't do: what the image gets is conf.d's files.
pub(crate) const UPDATE_INITRAMFS_RESUME: &str = "c=/etc/initramfs-tools/conf.d/zz-resume-auto; r=; w=; \
if [ ! -e \"$c\" ] && command -v unmkinitramfs >/dev/null; then for i in /boot/initrd.img-*; do [ -f \"$i\" ] || continue; d=$(mktemp -d) || exit; \
unmkinitramfs \"$i\" \"$d\" 2>/dev/null; r=$(cat \"$d/conf/conf.d/zz-resume-auto\" \"$d/main/conf/conf.d/zz-resume-auto\" 2>/dev/null | sed -n 's/^RESUME=//p' | head -n 1); rm -rf \"$d\"; [ -z \"$r\" ] || break; done; fi; \
[ -z \"$r\" ] || { w=$(mktemp) && touch -r \"${c%/*}\" \"$w\" && printf 'RESUME=%s\\n' \"$r\" > \"$c\"; } || exit; update-initramfs -u -k all; s=$?; \
[ -z \"$w\" ] || { rm -f \"$c\" && touch -r \"$w\" \"${c%/*}\" && rm -f \"$w\"; } || [ \"$s\" -ne 0 ] || s=1; exit \"$s\"";

/// BIOS grub-install onto a disk that may end in a NILFS2 partition: `sh -c
/// GRUB_BIOS_NILFS2 sh DISK COMMAND...` runs COMMAND with the disk's last
/// 4 KiB not looking like a NILFS2 superblock.  grub-bios-setup won't embed
/// core.img in a disk where it finds a filesystem besides the partition
/// table ("multiple partition labels", then "will not proceed with
/// blocklists"), and GRUB's NILFS2 driver finds one on the whole disk when
/// the last partition is NILFS2 and runs to the disk's end (an MBR disk's
/// last partition filled to it, as zorin18's /): with no superblock at the
/// disk's byte 1024, it takes the secondary one, at the device's last 4 KiB,
/// which is then the partition's own.  What it reads next is the
/// partition's blocks at the whole disk's offsets, and whether that makes a
/// directory GRUB can list is down to what the restore wrote, so
/// grub-install failed now and then (t2h-20261119T1959-f82c, zorin18: the
/// first 1 MiB a clean MBR, that superblock in the disk's last 4 KiB).  The
/// source's own grub-install is as lucky.  So the superblock's magic
/// (0x3434, at byte 6) is cleared through the whole disk while COMMAND
/// runs, then put back: NILFS2 reads its superblocks only at mount, and
/// what it writes is its own copy, through the partition; the magic it
/// writes is the same two bytes.  The disk's cache is dropped first, so the
/// check reads the disk and not the zeros the partition stage wrote there
/// through it (which, cached, hid the superblock from GRUB too, until the
/// restore's reads pushed them out).
pub(crate) const GRUB_BIOS_NILFS2: &str = "d=$1; shift; blockdev --flushbufs \"$d\" && n=$(blockdev --getsize64 \"$d\") || exit; o=$(( (n / 4096 - 1) * 4096 + 6 )); \
m=$(dd if=\"$d\" bs=1 skip=\"$o\" count=2 status=none | od -An -tx1 | tr -d ' \\n'); [ \"$m\" = 3434 ] || exec \"$@\"; \
put() { printf \"$1\" | dd of=\"$d\" bs=1 seek=\"$o\" count=2 conv=notrunc,fsync status=none; }; \
echo \"$d: its last 4 KiB is a NILFS2 superblock (the last partition's, which ends at the disk's end): its magic cleared while GRUB looks at the whole disk\"; \
put '\\000\\000' || exit; trap 'put 44; exit 1' HUP INT TERM; \"$@\"; r=$?; trap - HUP INT TERM; put 44 || r=1; exit \"$r\"";

/// `dd` writing COUNT blocks of BS zero bytes to DEV, SEEK blocks in.
fn zero(dev: &str, bs: u64, seek: u64, count: u64) -> Cmd {
    let mut a = vec!["dd".to_string(), "if=/dev/zero".into(), format!("of={dev}"), format!("bs={bs}")];
    if seek > 0 {
        a.push(format!("seek={seek}"));
    }
    a.push(format!("count={count}"));
    a.push("conv=fsync".into());
    Cmd::new(&a)
}

/// A target disk's first 4 MiB and last 1 MiB, its size read when it runs
/// (`blockdev --getsz`, in 512-byte sectors, whatever the disk's own).
fn zero_disk(dev: &str) -> Vec<Cmd> {
    let tail = ZERO_TAIL / 512;
    let q = shell_quote(dev);
    vec![
        Cmd::new(&["dd", "if=/dev/zero", &format!("of={dev}"), "bs=1M", &format!("count={}", ZERO_HEAD >> 20), "conv=fsync"]).why(format!("{dev}: its first {} MiB zeroed, and its last {} MiB (a GPT's backup, md 0.90/1.0 superblocks, ZFS's end labels)", ZERO_HEAD >> 20, ZERO_TAIL >> 20)),
        Cmd::new(&["sh", "-c", &format!("n=$(blockdev --getsz {q}) && dd if=/dev/zero of={q} bs=512 seek=$((n - {tail})) count={tail} conv=fsync")]),
    ]
}

/// Each partition the table will have: its first 4 MiB (all of it when
/// it's smaller) and its last 1 MiB, written through the disk before the
/// table is, so neither udev nor LVM's autoactivation, md's incremental
/// assembly or a ZFS import ever sees an old layer on a new partition (the
/// old partitions' content is all still there; wipefs saw only the disk's).
/// A partition inside the disk's first 4 MiB is zeroed already; the
/// extended partition holds only the EBRs sfdisk writes.
fn zero_parts(d: &Disk, dev: &str) -> Vec<Cmd> {
    let ss = d.sector_size;
    let (head, tail) = (ZERO_HEAD / ss, ZERO_TAIL / ss);
    let mut parts: Vec<&Part> = d.parts.iter().filter(|p| !p.extended && p.sectors > 0 && p.start_lba + p.sectors > head).collect();
    parts.sort_by_key(|p| p.number);
    let mut out = Vec::new();
    if !parts.is_empty() {
        out.push(Cmd::note(format!("the new partitions' old content zeroed through {dev}, before its table names them (blocks of {ss} bytes, at their start= sectors)")));
    }
    for p in parts {
        let h = p.sectors.min(head);
        if h == p.sectors {
            out.push(zero(dev, ss, p.start_lba, h).why(format!("{}: all of it", p.name)));
            continue;
        }
        let t = (p.sectors - h).min(tail);
        out.push(zero(dev, ss, p.start_lba, h).why(format!("{}: its first {} MiB and its last {} MiB", p.name, ZERO_HEAD >> 20, ZERO_TAIL >> 20)));
        out.push(zero(dev, ss, p.start_lba + p.sectors - t, t));
    }
    out
}

/// What holds the targets named with --erase, let go of from the top of
/// each stack down (target.rs and release.rs worked out that none of it is
/// mounted, swap, open, or on a disk that isn't named with --erase), with
/// udev let finish before and after, so wipefs's O_EXCL open finds the
/// disks free.  Each command refuses a device still in use, and the stage
/// stops there: nothing is written.  Plain commands, not `settled`'s retry
/// loop, so the plan reads as what a person would type.
fn release_cmds(steps: &[super::release::Step]) -> Vec<Cmd> {
    use super::release::Action;
    if steps.is_empty() {
        return vec![];
    }
    let mut disks: Vec<String> = Vec::new();
    for d in steps.iter().flat_map(|s| s.disks.iter()).map(|d| format!("/dev/{d}")) {
        if !disks.contains(&d) {
            disks.push(d);
        }
    }
    let mut out = vec![Cmd::new(&["udevadm", "settle"]).why(format!(
        "--erase {}: what this system started on it (at boot, most often) is let go of first, top of the stack down; none of it is mounted, swap, open, or on a disk not named with --erase",
        disks.join(", ")
    ))];
    for st in steps {
        let why = format!("{} ({})", st.what(), st.stops.join(", "));
        let c = match &st.action {
            Action::Pool(n) => Cmd::new(&["zpool", "export", n]),
            Action::Crypt(n) => Cmd::new(&["cryptsetup", "close", n]),
            Action::Lvm { vg, pvs } => {
                let devices = pvs.join(",");
                Cmd::new(&lvm_argv("vgchange", &devices, &["--activate", "n", vg]))
            }
            Action::Md(n) => Cmd::new(&["mdadm", "--stop", &format!("/dev/{n}")]),
            Action::Dm(n) => Cmd::new(&["dmsetup", "remove", n]),
        };
        out.push(c.why(why));
    }
    out.push(Cmd::new(&["udevadm", "settle"]).why("udev done with what was let go of, before wipefs opens the disks O_EXCL"));
    out
}

impl Builder<'_> {
    fn partition(&mut self) -> Vec<Cmd> {
        let mut out = release_cmds(&self.names.mapping.release);
        if let Some(fam) = self.fam {
            out.extend(self.other_partition(fam));
            return out;
        }
        for (src, tgt) in self.names.mapping.pairs.clone() {
            let Some(d) = self.layout.disk(&src) else { continue };
            let dev = format!("/dev/{tgt}");
            // wipefs first: it opens the disk O_EXCL, so a disk with a
            // partition still mounted or held fails here, before dd (which
            // takes no such lock) writes anything.
            out.push(Cmd::new(&["wipefs", "--all", &dev]).why(format!("{src} -> {dev}: clear the old table and signatures")));
            out.extend(zero_disk(&dev));
            if d.table.is_none() {
                out.push(Cmd::note(format!("{src} has no partition table: its content goes on {dev} itself")));
                continue;
            }
            out.extend(zero_parts(d, &dev));
            out.push(Cmd::new(&["udevadm", "settle"]).why(format!("udev done with {dev} before sfdisk takes it")));
            let same_size = !self.names.mapping.bigger.contains(&src);
            match sfdisk_script(d, &tgt, same_size) {
                Ok(script) => out.push(Cmd::new(&["sfdisk", "--wipe", "always", "--wipe-partitions", "always", &dev]).input(script)),
                Err(e) => self.problems.push(format!("{src}: {e}")),
            }
            // A protective MBR marked active (Anaconda does so on BIOS, for
            // firmware that boots only an active partition); sfdisk's isn't.
            let qdev = shell_quote(&dev);
            let pmbr_active = self.layout.device(&src).and_then(|v| v.at(&["partition_table", "protective_mbr", "entries", "0", "bootable"])).and_then(Value::as_bool) == Some(true);
            if pmbr_active {
                out.push(Cmd::new(&["sh", "-c", &format!("printf '\\200' | dd of={qdev} bs=1 seek=446 count=1 conv=notrunc status=none")]).why("the protective MBR's entry active, as the source's"));
            }
            // A protective MBR with a disk signature (KIWI's images, MicroOS's:
            // bytes 440-443); sfdisk writes a GPT's as zero.
            if let Some(sig) = pmbr_signature(self.layout.device(&src)) {
                let oct: String = sig.to_le_bytes().iter().map(|b| format!("\\{b:03o}")).collect();
                out.push(Cmd::new(&["sh", "-c", &format!("printf '{oct}' | dd of={qdev} bs=1 seek=440 count=4 conv=notrunc status=none")]).why(format!("the protective MBR's disk signature, {sig:08x}, as the source's")));
            }
            if d.parts.iter().any(|p| p.extended) {
                self.notes.push(format!("{src}: the extended partition and its EBRs are made by sfdisk; logical partitions keep their starts"));
            }
        }
        if !out.is_empty() {
            out.push(Cmd::new(&["udevadm", "settle"]));
        }
        out
    }
}

// ---- assemble -----------------------------------------------------------

enum Item {
    Md(Value),
    Luks(Value),
    Vg(Value),
    Pool(Value),
}

/// An item to assemble, the source devices it sits on, and the source
/// devices it makes with the node each gets here.
type Planned = (Item, Vec<String>, Vec<(String, String)>);

/// mdadm's name for a layout number sysfs gives.
pub fn md_layout_name(level: &str, n: u64) -> Option<String> {
    match level {
        "raid5" | "raid6" | "raid4" => Some(
            match n {
                0 => "left-asymmetric",
                1 => "right-asymmetric",
                2 => "left-symmetric",
                3 => "right-symmetric",
                4 => "parity-first",
                5 => "parity-last",
                _ => return None,
            }
            .to_string(),
        ),
        "raid10" => {
            let near = n & 0xff;
            let far = (n >> 8) & 0xff;
            let offset = n & 0x10000 != 0;
            Some(if offset {
                format!("o{far}")
            } else if far > 1 {
                format!("f{far}")
            } else {
                format!("n{near}")
            })
        }
        _ => None,
    }
}

/// The devices a pool's vdev tree names, and its `zpool create` vdev words.
fn vdev_words(names: &Names, tree: &Value, class: &str, words: &mut Vec<String>, leaves: &mut Vec<String>, problems: &mut Vec<String>) {
    let Some(kids) = tree.as_object() else { return };
    let mut first = true;
    for (name, v) in kids {
        let ty = v.get("vdev_type").and_then(Value::as_str).unwrap_or("disk");
        if first && !class.is_empty() {
            words.push(class.to_string());
        }
        first = false;
        match ty {
            "disk" | "file" => leaf(names, v, name, words, leaves, problems),
            "mirror" | "raidz" | "replacing" | "spare" => {
                let word = name.split('-').next().unwrap_or(ty);
                words.push(if word == "raidz" { "raidz1".to_string() } else { word.to_string() });
                for (cn, c) in v.get("vdevs").and_then(Value::as_object).into_iter().flatten() {
                    leaf(names, c, cn, words, leaves, problems);
                }
            }
            other => problems.push(format!("ZFS vdev {name} is {other}; version 1 makes disk, mirror and raidz vdevs only")),
        }
    }
}

fn leaf(names: &Names, v: &Value, name: &str, words: &mut Vec<String>, leaves: &mut Vec<String>, problems: &mut Vec<String>) {
    let path = v.get("path").and_then(Value::as_str).unwrap_or(name);
    match names.src_of_path(path) {
        Some(src) => {
            leaves.push(src.clone());
            words.push(names.dev(&src).unwrap_or_else(|| format!("<{src}>")));
        }
        None => problems.push(format!("ZFS vdev {path} isn't a device the layout knows")),
    }
}

/// Pool properties `zpool create -o` can't or mustn't take.
/// (bootsize: illumos' zpool create -B, whose ESP the partition stage
/// writes as the source's instead.)
const POOL_PROPS_SKIP: [&str; 10] = ["cachefile", "altroot", "readonly", "bootfs", "guid", "load_guid", "name", "version", "health", "bootsize"];

/// A legacy pool's version (28, say): its `version` property when that's a
/// number other than 5000.  A feature-flags pool's is 5000, or `-` as
/// `zpool get -p` gives it.
fn legacy_pool_version(p: &Value) -> Option<u64> {
    let v = p.at(&["local_properties", "version"]).or_else(|| p.at(&["properties", "version"]))?;
    let n = v.as_u64().or_else(|| v.as_str().and_then(|s| s.trim().parse().ok()))?;
    (n != 5000).then_some(n)
}
/// Dataset properties `zfs create -o` can't take.
const DS_PROPS_SKIP: [&str; 7] = ["pbkdf2salt", "objsetid", "guid", "createtxg", "keystatus", "encryptionroot", "mounted"];

/// An LVM metadata value in its text format.
fn lvm_text(v: &Value, indent: usize, out: &mut String) {
    let pad = "\t".repeat(indent);
    if let Value::Obj(kv) = v {
        for (k, x) in kv {
            match x {
                Value::Obj(_) => {
                    *out += &format!("{pad}{k} {{\n");
                    lvm_text(x, indent + 1, out);
                    *out += &format!("{pad}}}\n");
                }
                Value::Null => {}
                _ => *out += &format!("{pad}{k} = {}\n", lvm_scalar(x)),
            }
        }
    }
}

fn lvm_scalar(v: &Value) -> String {
    match v {
        Value::Str(s) => format!("\"{}\"", s.replace('\\', "\\\\").replace('"', "\\\"")),
        Value::Arr(a) => format!("[{}]", a.iter().map(lvm_scalar).collect::<Vec<_>>().join(", ")),
        Value::UInt(n) => n.to_string(),
        Value::Int(n) => n.to_string(),
        Value::Bool(b) => (*b as u8).to_string(),
        Value::Float(f) => f.to_string(),
        _ => "\"\"".into(),
    }
}

/// A thin pool of a volume group's, as load makes it again with lvcreate
/// once vgcfgrestore has restored the rest of the group
/// (docs/design/vanillaos.md, "Thin pools in `load`").
#[derive(Debug, Clone, PartialEq)]
pub struct ThinPool {
    pub name: String,
    /// The pool's data and metadata LVs (POOL_tdata, POOL_tmeta), in bytes.
    pub data_bytes: u64,
    pub meta_bytes: u64,
    /// The data LV's stripes (count, stripe size in bytes) when it is
    /// striped across more than one PV.
    pub data_stripes: Option<(u64, u64)>,
    /// The segment's chunk_size and discards, where the dump has them;
    /// zero_new_blocks, which lvm2 writes only when it is on; the pool LV's
    /// ERROR_WHEN_FULL flag.
    pub chunk_bytes: Option<u64>,
    pub discards: Option<String>,
    pub zero: bool,
    pub error_when_full: bool,
    pub attrs: LvAttrs,
    /// Its thin LVs: those without an origin in the source's device_id
    /// order, then the snapshots, each after its origin.
    pub thins: Vec<ThinLv>,
    /// How much of its data and metadata the source had in use (the dump's
    /// data_used_bytes and metadata_used_bytes, from the pool's
    /// `dmsetup status`), where the dump has them.
    pub data_used: Option<u64>,
    pub meta_used: Option<u64>,
}

#[derive(Debug, Clone, PartialEq)]
pub struct ThinLv {
    pub name: String,
    /// Its virtual size.
    pub bytes: u64,
    pub device_id: u64,
    pub attrs: LvAttrs,
    /// The thin LV it is a snapshot of, in the same pool (docs/design/qubes.md,
    /// Gate 2): made by `lvcreate -s` in the restore stage, once its origin's
    /// content is there (load/rawvol.rs).
    pub origin: Option<String>,
}

/// What of an LV's own lvcreate doesn't set from its sizes: its tags, its
/// ACTIVATION_SKIP flag and read-only permission (no WRITE in its status).
#[derive(Debug, Clone, PartialEq, Default)]
pub struct LvAttrs {
    pub tags: Vec<String>,
    pub activation_skip: bool,
    pub read_only: bool,
}

impl LvAttrs {
    fn of(lv: &Value) -> LvAttrs {
        let list = |k: &str| lv.get(k).and_then(Value::as_array).cloned().unwrap_or_default();
        let has = |f: &str| list("status").iter().chain(list("flags").iter()).any(|x| x.as_str() == Some(f));
        LvAttrs {
            tags: list("tags").iter().filter_map(|x| x.as_str().map(str::to_string)).collect(),
            activation_skip: has("ACTIVATION_SKIP"),
            read_only: lv.get("status").is_some() && !has("WRITE"),
        }
    }
}

/// A volume group's thin provisioning: its pools, whether lvcreate makes a
/// pool metadata spare (as the source had an lvolN_pmspare), why not when
/// the source's was too small to make again, and every LV vg_metadata
/// leaves out (the pools, their data and metadata LVs, their thin LVs and
/// the spare).
#[derive(Debug, Clone, PartialEq, Default)]
pub struct Thin {
    pub pools: Vec<ThinPool>,
    pub spare: bool,
    pub spare_note: Option<String>,
    pub dropped: Vec<String>,
}

fn lv_segments(lv: &Value) -> Vec<Value> {
    lv.get("segments").and_then(Value::as_array).cloned().unwrap_or_default()
}

/// The volume group's thin pools and thin LVs.  Refused: a thin LV with an
/// external origin, a thin snapshot whose origin isn't a thin LV of the same
/// pool in the dump, a pool whose data
/// or metadata LV isn't linear or striped (RAID, cache), a striped metadata
/// LV, data stripes that differ between segments, a pool LV on a PV that
/// isn't allocatable, a pool or thin LV that isn't one segment, and a thin
/// LV whose pool isn't in the group.
pub fn thin_of(vg: &Value) -> Result<Thin, String> {
    let name = s(vg, "name").unwrap_or_default();
    let ext = num(vg, "extent_size_bytes").unwrap_or(0);
    let lvs = vg.get("logical_volumes").and_then(Value::as_array).cloned().unwrap_or_default();
    let pvs = vg.get("physical_volumes").and_then(Value::as_array).cloned().unwrap_or_default();
    let by_name = |n: &str| lvs.iter().find(|l| s(l, "name").as_deref() == Some(n));
    let ty_of = |seg: &Value| seg.get("type").and_then(Value::as_str).unwrap_or("?").to_string();
    // The PVs a striped segment sits on that lvcreate can't allocate on.
    let unallocatable = |seg: &Value| -> Vec<String> {
        let on = seg.get("stripes").and_then(Value::as_array).cloned().unwrap_or_default();
        on.iter()
            .step_by(2)
            .filter_map(|x| x.as_str())
            .filter(|pv| !pvs.iter().any(|p| s(p, "name").as_deref() == Some(pv) && p.get("status").and_then(Value::as_array).is_some_and(|a| a.iter().any(|x| x.as_str() == Some("ALLOCATABLE")))))
            .map(str::to_string)
            .collect()
    };
    let mut t = Thin::default();
    for lv in &lvs {
        let ln = s(lv, "name").unwrap_or_default();
        let segs = lv_segments(lv);
        if !segs.iter().any(|g| ty_of(g) == "thin-pool") {
            continue;
        }
        if segs.len() != 1 {
            return Err(format!("thin pool {name}/{ln} has {} segments; version 1 makes a pool of one", segs.len()));
        }
        let seg = &segs[0];
        // A sub-LV's size, and its stripes (count, stripe size) when more
        // than one.
        let mut size = |key: &str, what: &str| -> Result<(u64, Option<(u64, u64)>), String> {
            let sub = s(seg, key).ok_or_else(|| format!("thin pool {name}/{ln} doesn't name its {what} LV"))?;
            let l = by_name(&sub).ok_or_else(|| format!("thin pool {name}/{ln}'s {what} LV {sub} isn't in the group"))?;
            let mut extents = 0;
            let mut stripes: Vec<(u64, u64)> = vec![];
            for g in lv_segments(l) {
                let ty = ty_of(&g);
                if ty != "striped" {
                    return Err(format!("thin pool {name}/{ln}'s {what} LV {sub} has a {ty} segment; version 1 makes thin pools on linear and striped LVs only (not RAID or cache)"));
                }
                if let Some(pv) = unallocatable(&g).first() {
                    return Err(format!("thin pool {name}/{ln}'s {what} LV {sub} is on {pv}, which isn't allocatable (pvchange -x n): lvcreate can't make it there again"));
                }
                extents += num(&g, "extent_count").unwrap_or(0);
                stripes.push((num(&g, "stripe_count").unwrap_or(1).max(1), num(&g, "stripe_size").unwrap_or(0) * 512));
            }
            stripes.dedup();
            let striped = match stripes.as_slice() {
                [] | [(1, _)] => None,
                [one] => Some(*one),
                _ => return Err(format!("thin pool {name}/{ln}'s {what} LV {sub} has segments of different stripes; version 1 makes a pool's data LV with one stripe count and size")),
            };
            t.dropped.push(sub);
            Ok((extents * ext, striped))
        };
        let (data_bytes, data_stripes) = size("pool", "data")?;
        let (meta_bytes, meta_stripes) = size("metadata", "metadata")?;
        if let Some((n, _)) = meta_stripes {
            return Err(format!("thin pool {name}/{ln}'s metadata LV is striped across {n} PVs; version 1 makes a pool's metadata LV linear only"));
        }
        t.dropped.push(ln.clone());
        t.pools.push(ThinPool {
            name: ln,
            data_bytes,
            meta_bytes,
            data_stripes,
            chunk_bytes: num(seg, "chunk_size").map(|c| c * 512),
            discards: s(seg, "discards"),
            zero: num(seg, "zero_new_blocks").is_some_and(|z| z != 0),
            error_when_full: [lv.get("status"), lv.get("flags")].into_iter().flatten().filter_map(Value::as_array).flatten().any(|x| x.as_str() == Some("ERROR_WHEN_FULL")),
            attrs: LvAttrs::of(lv),
            thins: vec![],
            data_used: num(lv, "data_used_bytes"),
            meta_used: num(lv, "metadata_used_bytes"),
        });
    }
    for lv in &lvs {
        let ln = s(lv, "name").unwrap_or_default();
        let segs = lv_segments(lv);
        if !segs.iter().any(|g| ty_of(g) == "thin") {
            continue;
        }
        if segs.len() != 1 {
            return Err(format!("thin LV {name}/{ln} has {} segments; version 1 makes a thin LV of one", segs.len()));
        }
        let seg = &segs[0];
        if let Some(o) = s(seg, "external_origin") {
            return Err(format!("thin LV {name}/{ln} has the external origin {o}; version 1 doesn't make thin LVs with one"));
        }
        let pool = s(seg, "thin_pool").unwrap_or_default();
        let Some(p) = t.pools.iter_mut().find(|p| p.name == pool) else {
            return Err(format!("thin LV {name}/{ln}'s pool {pool} isn't a thin pool in the group"));
        };
        p.thins.push(ThinLv {
            name: ln.clone(),
            bytes: num(seg, "extent_count").unwrap_or(0) * ext,
            device_id: num(seg, "device_id").unwrap_or(0),
            attrs: LvAttrs::of(lv),
            origin: s(seg, "origin"),
        });
        t.dropped.push(ln);
    }
    for p in &mut t.pools {
        p.thins = thin_forest(&name, &p.name, std::mem::take(&mut p.thins))?;
    }
    // The spare: lvcreate makes its own for a pool (with
    // --poolmetadataspare y), sized for the largest pool's metadata.  One
    // the source had smaller than that (the metadata grown since) gave
    // back fewer extents than the new one takes: it is made only where the
    // group's free extents cover the difference.
    if !t.pools.is_empty() {
        let mut spare_bytes = 0;
        for lv in &lvs {
            let ln = s(lv, "name").unwrap_or_default();
            let visible = lv.get("status").and_then(Value::as_array).is_some_and(|a| a.iter().any(|x| x.as_str() == Some("VISIBLE")));
            if ln.ends_with("_pmspare") && !visible {
                for g in lv_segments(lv) {
                    if let Some(pv) = unallocatable(&g).first() {
                        return Err(format!("the pool metadata spare {name}/{ln} is on {pv}, which isn't allocatable (pvchange -x n): lvcreate can't make it there again"));
                    }
                    spare_bytes += num(&g, "extent_count").unwrap_or(0) * ext;
                }
                t.spare = true;
                t.dropped.push(ln);
            }
        }
        let meta = t.pools.iter().map(|p| p.meta_bytes).max().unwrap_or(0);
        if t.spare && spare_bytes < meta {
            let total: u64 = pvs.iter().filter_map(|p| num(p, "pe_count")).sum::<u64>() * ext;
            let used: u64 = lvs.iter().flat_map(lv_segments).filter(|g| ty_of(g) == "striped").filter_map(|g| num(&g, "extent_count")).sum::<u64>() * ext;
            let free = total.saturating_sub(used);
            if free < meta - spare_bytes {
                t.spare = false;
                t.spare_note = Some(format!(
                    "volume group {name}: its pool metadata spare ({}) is smaller than the largest pool's metadata ({}), and the group's {} free can't make up the {} a new one takes: the pools are made without a spare (--poolmetadataspare n); `lvconvert --repair` needs one, which `lvconvert --poolmetadataspare y` makes once there is room",
                    crate::report::human(spare_bytes),
                    crate::report::human(meta),
                    crate::report::human(free),
                    crate::report::human(meta - spare_bytes),
                ));
            }
        }
    }
    Ok(t)
}

/// A pool's thin LVs in the order load makes them: a forest from their
/// origin links, the roots (no origin) in device_id order, then each
/// snapshot after its origin, breadth first (a chain of revisions oldest
/// first), siblings in device_id order.  A snapshot whose origin isn't a
/// thin LV of this pool, or a loop of origins, is refused.
pub fn thin_forest(vg: &str, pool: &str, mut thins: Vec<ThinLv>) -> Result<Vec<ThinLv>, String> {
    thins.sort_by_key(|l| l.device_id);
    for l in &thins {
        if let Some(o) = &l.origin {
            if !thins.iter().any(|x| &x.name == o) {
                return Err(format!("thin LV {vg}/{} is a thin snapshot of {o}, which isn't a thin LV of pool {pool} in the dump: bko-holt load can't make it without its origin", l.name));
            }
        }
    }
    let mut out: Vec<ThinLv> = Vec::new();
    let mut left = thins;
    loop {
        let before = out.len();
        let ready: Vec<usize> = (0..left.len()).filter(|&i| left[i].origin.as_ref().is_none_or(|o| out.iter().any(|x| &x.name == o))).collect();
        for i in ready.into_iter().rev() {
            out.push(left.remove(i));
        }
        // (Each round's newly ready ones, in device_id order.)
        out[before..].sort_by_key(|l| l.device_id);
        if left.is_empty() {
            return Ok(out);
        }
        if out.len() == before {
            return Err(format!("thin LVs {} in {vg}/{pool} are snapshots of each other in a loop", left.iter().map(|l| l.name.as_str()).collect::<Vec<_>>().join(", ")));
        }
    }
}

/// A size for an LVM command, exact: MiB where it is a whole number of
/// them, else KiB, else 512-byte sectors.
pub(crate) fn lvm_size(bytes: u64) -> String {
    if bytes % (1 << 20) == 0 {
        format!("{}m", bytes >> 20)
    } else if bytes % 1024 == 0 {
        format!("{}k", bytes >> 10)
    } else {
        format!("{}s", bytes / 512)
    }
}

/// The volume group as `vgcfgrestore` reads it, from the dump's view of it:
/// without its thin pools and thin LVs (thin_of's `dropped`), which
/// lvcreate makes after it.
pub fn vg_metadata(vg: &Value) -> Result<String, String> {
    use crate::json::Obj;
    let name = s(vg, "name").ok_or("a volume group without a name")?;
    let ext = num(vg, "extent_size_bytes").ok_or("no extent size")? / 512;
    let thin = thin_of(vg)?;
    let mut pvs = Obj::new();
    for pv in vg.get("physical_volumes").and_then(Value::as_array).into_iter().flatten() {
        let pn = s(pv, "name").ok_or("a PV without a name")?;
        pvs.put(
            &pn,
            Obj::new()
                .set("id", pv.get("id").cloned().unwrap_or(Value::Null))
                .set("device", pv.get("device_hint").cloned().unwrap_or(Value::Null))
                .set("status", pv.get("status").cloned().unwrap_or(Value::Arr(vec![])))
                .set("flags", Value::Arr(vec![]))
                .set("dev_size", num(pv, "dev_size_bytes").map(|b| b / 512))
                .set("pe_start", num(pv, "pe_start_bytes").map(|b| b / 512))
                .set("pe_count", num(pv, "pe_count"))
                .build(),
        );
    }
    let mut lvs = Obj::new();
    for lv in vg.get("logical_volumes").and_then(Value::as_array).into_iter().flatten() {
        let ln = s(lv, "name").ok_or("an LV without a name")?;
        if thin.dropped.contains(&ln) {
            continue;
        }
        let segs = lv.get("segments").and_then(Value::as_array).cloned().unwrap_or_default();
        for seg in &segs {
            let ty = seg.get("type").and_then(Value::as_str).unwrap_or("?");
            if ty != "striped" {
                return Err(format!("LV {name}/{ln} has a {ty} segment; version 1 makes linear and striped LVs only"));
            }
        }
        let mut status: Vec<Value> = lv
            .get("status")
            .and_then(Value::as_array)
            .cloned()
            .unwrap_or_default()
            .into_iter()
            .filter(|x| x.as_str() != Some("ACTIVATION_SKIP"))
            .collect();
        if lv.get("status").is_some() && !status.iter().any(|x| x.as_str() == Some("WRITE")) {
            let idx = status.iter().position(|x| x.as_str() == Some("READ")).map_or(0, |i| i + 1);
            status.insert(idx, Value::Str("WRITE".into()));
        }
        let flags: Vec<Value> = lv
            .get("flags")
            .and_then(Value::as_array)
            .cloned()
            .unwrap_or_default()
            .into_iter()
            .filter(|x| x.as_str() != Some("ACTIVATION_SKIP"))
            .collect();
        let mut o = Obj::new()
            .set("id", lv.get("id").cloned().unwrap_or(Value::Null))
            .set("status", Value::Arr(status))
            .set("flags", Value::Arr(flags))
            .set("creation_host", lv.get("creation_host").cloned().unwrap_or(Value::Null))
            .set("creation_time", lv.get("creation_time").cloned().unwrap_or(Value::Null))
            .set("segment_count", segs.len() as u64);
        for (i, seg) in segs.into_iter().enumerate() {
            o.put(&format!("segment{}", i + 1), seg);
        }
        lvs.put(&ln, o.build());
    }
    let body = Obj::new()
        .set("id", vg.get("id").cloned().unwrap_or(Value::Null))
        .set("seqno", num(vg, "seqno").unwrap_or(1))
        .set("format", s(vg, "format").unwrap_or("lvm2".into()))
        .set("status", vg.get("status").cloned().unwrap_or(Value::Arr(vec![])))
        .set("flags", vg.get("flags").cloned().unwrap_or(Value::Arr(vec![])))
        .set("extent_size", ext)
        .set("max_lv", num(vg, "max_lv").unwrap_or(0))
        .set("max_pv", num(vg, "max_pv").unwrap_or(0))
        .set("metadata_copies", num(vg, "metadata_copies").unwrap_or(0))
        .set("physical_volumes", pvs)
        .set("logical_volumes", lvs);
    let doc = Obj::new()
        .set(&name, body)
        .set("contents", "Text Format Volume Group")
        .set("version", 1u64)
        .set("description", "made by bko-holt load from a bko-holt dump")
        .set("creation_host", vg.get("creation_host").cloned().unwrap_or(Value::Null))
        .set("creation_time", vg.get("creation_time").cloned().unwrap_or(Value::Null))
        .build();
    let mut out = String::new();
    lvm_text(&doc, 0, &mut out);
    Ok(out)
}

impl Builder<'_> {
    /// A plan problem, unless it's listed already (two stages that check
    /// the same thing).
    pub(crate) fn problem_once(&mut self, p: String) {
        if !self.problems.contains(&p) {
            self.problems.push(p);
        }
    }

    fn items(&mut self) -> Vec<Planned> {
        let mut items = Vec::new();
        for md in self.layout.arr(&["storage", "md_arrays"]) {
            let name = s(md, "name").unwrap_or_default();
            let inputs = self.layout.slaves(&name);
            let devname = md.at(&["md", "devname"]).and_then(Value::as_str).map(str::to_string);
            let short = devname.or_else(|| md.at(&["md", "name"]).and_then(Value::as_str).map(|n| n.rsplit(':').next().unwrap_or(n).to_string())).unwrap_or_else(|| name.clone());
            items.push((Item::Md(md.clone()), inputs, vec![(name, format!("/dev/md/{short}"))]));
        }
        for dm in self.layout.arr(&["storage", "dm_devices"]) {
            if dm.at(&["dm", "target"]).and_then(Value::as_str) != Some("crypt") {
                continue;
            }
            let name = s(dm, "name").unwrap_or_default();
            // Made at every boot by crypttab with a random key: nothing to
            // make here (format passes over what's on it).
            if let Some((open_as, key, opt)) = self.layout.random_key_crypt(&name) {
                let under = self.layout.slaves(&name).join(", ");
                self.notes.push(format!(
                    "{name} ({open_as}): plain dm-crypt that crypttab makes at every boot with a random key from {key} ({opt}): nothing is made on {under} but the partition (its ends zeroed, as every new partition's); the restored system's crypttab makes it at its first boot"
                ));
                continue;
            }
            let dm_name = dm.at(&["dm", "dm_name"]).and_then(Value::as_str).unwrap_or(&name).to_string();
            let open_as = self.layout.crypttab_entry(&dm_name, dm.at(&["dm", "luks_uuid"]).and_then(Value::as_str)).and_then(|e| s(e, "name")).unwrap_or(dm_name);
            items.push((Item::Luks(dm.clone()), self.layout.slaves(&name), vec![(name, format!("/dev/mapper/{open_as}"))]));
        }
        for vg in self.layout.arr(&["storage", "lvm", "volume_groups"]) {
            let vgname = s(vg, "name").unwrap_or_default();
            let inputs: Vec<String> = vg.get("physical_volumes").and_then(Value::as_array).into_iter().flatten().filter_map(|p| s(p, "device")).collect();
            let outputs: Vec<(String, String)> = self
                .layout
                .arr(&["storage", "dm_devices"])
                .iter()
                .filter(|d| d.at(&["dm", "vg"]).and_then(Value::as_str) == Some(vgname.as_str()))
                .filter_map(|d| Some((s(d, "name")?, format!("/dev/mapper/{}", d.at(&["dm", "dm_name"]).and_then(Value::as_str)?))))
                .collect();
            items.push((Item::Vg(vg.clone()), inputs, outputs));
        }
        let pools = self.layout.v.at(&["storage", "zfs", "tools", "imported_pools"]).and_then(Value::as_array).cloned().unwrap_or_default();
        for p in self.layout.arr(&["storage", "zfs", "pools_from_labels"]) {
            let n = s(p, "name").unwrap_or_default();
            if !pools.iter().any(|q| s(q, "name").as_deref() == Some(n.as_str())) {
                self.problems.push(format!("ZFS pool {n} has labels on disk but wasn't imported when the dump was made, so its properties and datasets weren't recorded"));
            }
        }
        for p in pools {
            let mut words = vec![];
            let mut leaves = vec![];
            let mut probs = vec![];
            vdev_words(&self.names, p.at(&["vdevs", "vdevs"]).and_then(|v| v.as_object()).and_then(|o| o.first()).and_then(|(_, root)| root.get("vdevs")).unwrap_or(&Value::Null), "", &mut words, &mut leaves, &mut probs);
            let outs = self.zvols_of(&s(&p, "name").unwrap_or_default()).into_iter().map(|(src, ds)| (src, format!("/dev/zvol/{}", self.zname(&ds)))).collect();
            items.push((Item::Pool(p), leaves, outs));
        }
        items
    }

    fn assemble(&mut self) -> Vec<Cmd> {
        let items = self.items();
        // Order: an item is ready once every device it sits on exists.
        let mut have: Vec<String> = Vec::new();
        for (src, _) in &self.names.mapping.pairs {
            if let Some(d) = self.layout.disk(src) {
                have.push(d.name.clone());
                have.extend(d.parts.iter().map(|p| p.name.clone()));
            }
        }
        let mut left: Vec<_> = items.into_iter().collect();
        // GELI providers first: what sits on them is made on their .eli nodes.
        let mut out = self.geli_assemble(&mut have);
        // So too OpenBSD's softraid volumes, on their DUIDs.
        out.extend(self.softraid_assemble());
        while let Some(i) = left.iter().position(|(_, ins, _)| ins.iter().all(|x| have.contains(x))) {
            let (item, _, outs) = left.remove(i);
            for (src, node) in &outs {
                self.names.made.insert(src.clone(), node.clone());
                have.push(src.clone());
            }
            match item {
                Item::Md(v) => self.md(&v, &outs, &mut out),
                Item::Luks(v) => self.luks(&v, &outs, &mut out),
                Item::Vg(v) => self.vg(&v, &mut out),
                Item::Pool(v) => self.pool(&v, &mut out),
            }
        }
        for (item, ins, _) in left {
            let what = match item {
                Item::Md(v) | Item::Luks(v) | Item::Vg(v) | Item::Pool(v) => s(&v, "name").unwrap_or_default(),
            };
            let missing: Vec<String> = ins.into_iter().filter(|x| !have.contains(x)).collect();
            self.problems.push(format!("{what} sits on {}, which nothing in this layout makes (an unmapped disk?)", missing.join(", ")));
        }
        out
    }

    fn md(&mut self, v: &Value, outs: &[(String, String)], out: &mut Vec<Cmd>) {
        let name = s(v, "name").unwrap_or_default();
        let md = v.get("md").cloned().unwrap_or(Value::Null);
        let node = outs.first().map(|o| o.1.clone()).unwrap_or_default();
        let level = s(&md, "level").unwrap_or_default();
        let n = num(&md, "raid_disks").unwrap_or(0) as usize;
        let mut slots: Vec<String> = vec!["missing".into(); n];
        let mut first_member = None;
        for m in md.get("members").and_then(Value::as_array).into_iter().flatten() {
            let dev = s(m, "device").unwrap_or_default();
            first_member.get_or_insert(dev.clone());
            let node = self.names.dev(&dev).unwrap_or_else(|| format!("<{dev}>"));
            match s(m, "slot").and_then(|x| x.parse::<usize>().ok()) {
                Some(i) if i < n => slots[i] = node,
                _ => slots.push(node), // a spare
            }
        }
        let sb = first_member.and_then(|m| self.layout.content(&m).cloned()).unwrap_or(Value::Null);
        let meta = s(&md, "metadata_version").or_else(|| s(&sb, "version")).unwrap_or("1.2".into());
        let mut a = vec!["mdadm".to_string(), "--create".into(), node.clone(), "--run".into(), "--assume-clean".into(), format!("--metadata={meta}"), format!("--level={level}"), format!("--raid-devices={n}")];
        if let Some(c) = num(&md, "chunk_size").filter(|&c| c > 0 && level != "raid1") {
            a.push(format!("--chunk={}K", c / 1024));
        }
        if let Some(l) = num(&md, "layout").and_then(|l| md_layout_name(&level, l)) {
            a.push(format!("--layout={l}"));
        }
        if let Some(u) = s(&md, "uuid") {
            a.push(format!("--uuid={u}"));
        }
        if let Some(full) = s(&md, "name") {
            match full.split_once(':') {
                Some((host, short)) => {
                    a.push(format!("--homehost={host}"));
                    a.push(format!("--name={short}"));
                }
                None => a.push(format!("--name={full}")),
            }
        }
        // Metadata 1.0 keeps the data at the start of the member (offset 0),
        // which mdadm's --data-offset refuses: only 1.1 and 1.2 take one.
        if meta.starts_with('1') {
            if let Some(off) = num(&sb, "data_offset_bytes").filter(|&o| o > 0) {
                a.push(format!("--data-offset={}K", off / 1024));
            }
        }
        let bitmap = sb.get("write_intent_bitmap").and_then(Value::as_bool).unwrap_or(false);
        a.push(format!("--bitmap={}", if bitmap { "internal" } else { "none" }));
        a.extend(slots);
        out.push(Cmd { argv: a, why: Some(format!("md array {name} ({level})")), ..Cmd::default() });
        self.closers.push(settled(&["mdadm", "--stop", &node]));
    }

    fn luks(&mut self, v: &Value, outs: &[(String, String)], out: &mut Vec<Cmd>) {
        let name = s(v, "name").unwrap_or_default();
        let Some(under) = self.layout.slaves(&name).first().cloned() else {
            self.problems.push(format!("LUKS device {name}: what it sits on isn't recorded"));
            return;
        };
        let dev = self.names.dev(&under).unwrap_or_else(|| format!("<{under}>"));
        let node = outs.first().map(|o| o.1.clone()).unwrap_or_default();
        let open_as = node.trim_start_matches("/dev/mapper/").to_string();
        let c = self.layout.content(&under).cloned().unwrap_or(Value::Null);
        if c.get("type").and_then(Value::as_str) != Some("crypto_LUKS") {
            self.problems.push(format!("{name}: {under} doesn't hold a LUKS header in the dump (plain dm-crypt isn't supported)"));
            return;
        }
        // The dump kept this header (crate::luksheader): written back, not
        // made anew.
        if let Some(h) = crate::luksheader::kept(&self.layout.v, &under).cloned() {
            self.luks_keep_header(&under, &dev, &open_as, &c, &h, out);
            return;
        }
        let version = s(&c, "version").unwrap_or("2".into());
        let mut a = vec!["cryptsetup".to_string(), "luksFormat".into(), "--batch-mode".into(), format!("--type=luks{version}")];
        if version == "1" {
            if let Some(ci) = s(&c, "cipher") {
                a.push(format!("--cipher={ci}"));
            }
            if let Some(k) = num(&c, "key_bits") {
                a.push(format!("--key-size={k}"));
            }
            if let Some(h) = s(&c, "hash") {
                a.push(format!("--hash={h}"));
            }
            if let Some(o) = num(&c, "data_offset_bytes") {
                a.push(format!("--offset={}", o / 512));
            }
        } else {
            let seg = c.at(&["segments", "0"]).cloned().unwrap_or(Value::Null);
            // The PBKDF the new passphrase's keyslot takes: the first source
            // keyslot no token names.  A token's (systemd-cryptenroll's
            // TPM2, FIDO2 or recovery key: Aeon's two) has its minimal PBKDF
            // (PBKDF2, 1000 iterations), meant for a high-entropy key, which
            // on a typed passphrase would cost an attacker nothing; with no
            // other, cryptsetup's own (benchmarked) instead.
            let ks = luks_passphrase_keyslot(&c);
            if ks.is_null() && c.get("keyslots").and_then(Value::as_array).is_some_and(|k| !k.is_empty()) {
                self.notes.push(format!("LUKS2 on {dev}: every keyslot of the source is a token's (systemd-tpm2, systemd-recovery and the like: high-entropy keys with systemd-cryptenroll's minimal PBKDF), so the new passphrase's keyslot gets cryptsetup's default PBKDF, benchmarked here, not their cost"));
            }
            if let Some(e) = s(&seg, "encryption") {
                a.push(format!("--cipher={e}"));
            }
            if let Some(k) = num(&ks, "key_bits").or_else(|| c.at(&["keyslots", "0"]).and_then(|k| num(k, "key_bits"))) {
                a.push(format!("--key-size={k}"));
            }
            if let Some(ss) = num(&seg, "sector_size") {
                a.push(format!("--sector-size={ss}"));
            }
            if let Some(o) = num(&seg, "offset") {
                a.push(format!("--offset={}", o / 512));
            }
            if let Some(k) = s(&ks, "kdf") {
                a.push(format!("--pbkdf={k}"));
            }
            // The keyslot's cost, as the source's (no benchmark): an
            // unlock takes what it took there.
            if let Some(m) = num(&ks, "kdf_memory") {
                a.push(format!("--pbkdf-memory={m}"));
            }
            if let Some(c) = num(&ks, "kdf_cpus") {
                a.push(format!("--pbkdf-parallel={c}"));
            }
            if let Some(t) = num(&ks, "kdf_time").or_else(|| num(&ks, "kdf_iterations")) {
                a.push(format!("--pbkdf-force-iterations={t}"));
            }
            // PBKDF2's hash, or else (argon2) the volume key digest's: one
            // --hash sets both.
            let digest = c.get("digest_hashes").and_then(Value::as_array).and_then(|d| d.first()).and_then(Value::as_str).map(str::to_string);
            if let Some(h) = s(&ks, "kdf_hash").or(digest) {
                a.push(format!("--hash={h}"));
            }
            if let Some(h) = num(&c, "header_size") {
                a.push(format!("--luks2-metadata-size={h}"));
            }
            if let Some(k) = num(&c, "keyslots_size") {
                a.push(format!("--luks2-keyslots-size={k}"));
            }
            if let Some(x) = s(&c, "subsystem") {
                a.push(format!("--subsystem={x}"));
            }
            if let Some(l) = s(&c, "label") {
                a.push(format!("--label={l}"));
            }
        }
        if let Some(u) = s(&c, "uuid") {
            a.push(format!("--uuid={u}"));
        }
        // The key: --key-file, else a temporary random one when the source
        // unlocked it with a key file of its own (replaced in boot), else a
        // prompt for a new passphrase.  Either way the boot stage adds the
        // restored key file, when it's in the restored tree
        // (restored_key_files).
        let tab_key = self.layout.crypttab_entry(&open_as, s(&c, "uuid").as_deref()).and_then(|e| s(e, "key_file")).filter(|k| !matches!(k.as_str(), "none" | "-" | ""));
        let key = match (self.opts.key_file(&open_as), &tab_key) {
            (Some(f), _) => Some(f.to_string()),
            // A key the restore doesn't bring back (on another device, or
            // not a path in the restored tree): the temporary key would be
            // the container's only one, lost when the rescue system stops.
            (None, Some(k)) if !restorable_key_file(k) => {
                self.problem_once(needs_key_file(&open_as, k));
                None
            }
            (None, Some(_)) if temp_key_name(&open_as).is_none() => {
                self.problem_once(format!("{open_as}: a LUKS name bko-holt can't keep a temporary key under: give --key-file {open_as}=FILE"));
                None
            }
            (None, Some(k)) => {
                let tmp = format!("{RUN_DIR}/keys/{}", temp_key_name(&open_as).unwrap_or_default());
                out.push(Cmd::new(&["mkdir", "-p", "-m", "700", &format!("{RUN_DIR}/keys")]));
                out.push(Cmd::new(&["dd", "if=/dev/urandom", &format!("of={tmp}"), "bs=64", "count=1", "status=none"]).why(format!(
                    "{open_as} unlocks with the key file {k} on the restored system; until that's restored, a temporary key"
                )));
                Some(tmp)
            }
            (None, None) => None,
        };
        let why = if key.is_none() { format!("LUKS{version} on {dev}: asks for the new passphrase") } else { format!("LUKS{version} on {dev}") };
        if let Some(k) = &key {
            a.push(format!("--key-file={k}"));
        }
        a.push(dev.clone());
        out.push(Cmd { argv: a, why: Some(why), ..Cmd::default() });
        let mut o = vec!["cryptsetup".to_string(), "open".into()];
        if let Some(k) = &key {
            o.push(format!("--key-file={k}"));
        }
        o.push(dev);
        o.push(open_as.clone());
        out.push(Cmd { argv: o, ..Cmd::default() });
        self.closers.push(settled(&["cryptsetup", "close", &open_as]));
    }

    /// keep_header (docs/design/ubuntu-core.md, Decision 2): the LUKS2
    /// header the dump kept, written back with luksHeaderRestore and opened
    /// with --key-file (by the container's name, its LUKS label or the
    /// device): the source's volume key, keyslots and tokens, so whatever
    /// opened it there (a TPM-sealed key, a recovery key) opens it here.  No
    /// luksFormat, no luksAddKey, no token change.
    fn luks_keep_header(&mut self, under: &str, dev: &str, open_as: &str, c: &Value, h: &Value, out: &mut Vec<Cmd>) {
        let label = s(c, "label");
        if s(c, "version").as_deref() != Some("2") {
            self.problems.push(format!("{under}: the dump kept its LUKS header, which is LUKS{}: load keeps LUKS2 headers only", s(c, "version").unwrap_or("?".into())));
            return;
        }
        if self.opts.new_uuids {
            self.problems.push(format!("{under}: --new-uuids, and the dump kept its LUKS2 header, which carries the source's UUID"));
        }
        let file = crate::luksheader::path_at_load(self.opts.luks_header_dir.as_deref(), &self.opts.layout, &s(h, "file").unwrap_or_default());
        if let Some(e) = crate::luksheader::check_file(&file, h) {
            self.problems.push(format!("{under}: the LUKS2 header the dump kept: {e} (dump wrote it to {}; give its directory with --luks-header-dir)", s(h, "written_to").unwrap_or_default()));
        }
        // The data segment must fit the partition it's restored to.
        // A segment of a fixed size must fit whole; a `dynamic` one (Ubuntu
        // Core's) takes what follows its offset, so only that is checked.
        let seg = c.at(&["segments", "0"]).cloned().unwrap_or(Value::Null);
        let offset = num(&seg, "offset").unwrap_or(0);
        if let Some(have) = self.layout.device(under).and_then(|v| num(v, "size_bytes")) {
            match num(&seg, "size") {
                Some(size) if have < offset + size => {
                    self.problems.push(format!("{under}: its kept LUKS2 header's data segment needs {} bytes, and the partition here has {have}", offset + size));
                }
                None if have <= offset => {
                    self.problems.push(format!("{under}: its kept LUKS2 header's data segment starts at {offset} bytes, past the partition here ({have} bytes)"));
                }
                _ => {}
            }
        }
        let key = self.opts.key_file(open_as).or_else(|| label.as_deref().and_then(|l| self.opts.key_file(l))).or_else(|| self.opts.key_file(under)).map(str::to_string);
        let Some(key) = key else {
            let name = label.clone().unwrap_or_else(|| open_as.to_string());
            self.problems.push(format!(
                "{under}: its LUKS2 header is kept, so it opens only with a key it already has: give --key-file {name}=FILE (Ubuntu Core: its recovery key, made into FILE by `bko-holt recovery-key-file FILE`)"
            ));
            return;
        };
        out.push(Cmd::new(&["cryptsetup", "luksHeaderRestore", "--batch-mode", dev, "--header-backup-file", &file]).why(format!(
            "LUKS2 on {dev}: the source's header{}, kept byte for byte (its volume key, keyslots and tokens); no luksFormat, no luksAddKey",
            label.map(|l| format!(" ({l})")).unwrap_or_default()
        )));
        out.push(Cmd::new(&["cryptsetup", "open", &format!("--key-file={key}"), dev, open_as]));
        self.closers.push(settled(&["cryptsetup", "close", open_as]));
    }

    fn vg(&mut self, vg: &Value, out: &mut Vec<Cmd>) {
        let name = s(vg, "name").unwrap_or_default();
        let text = match vg_metadata(vg) {
            Ok(t) => t,
            Err(e) => {
                self.problems.push(format!("volume group {name}: {e}"));
                return;
            }
        };
        let file = format!("{RUN_DIR}/lvm/{name}.vg");
        out.push(Cmd::new(&["mkdir", "-p", "-m", "700", &format!("{RUN_DIR}/lvm")]));
        out.push(Cmd::write_file(&file, text).why(format!("volume group {name}: its metadata, for pvcreate and vgcfgrestore")));
        // LVM sees the group's own PVs only (--devices, and no devices file):
        // a group of the same name elsewhere on this system, or a PV of
        // another's with one of this group's UUIDs, is never what these
        // commands act on (target.rs refuses the first, for the boot stage's
        // sake).
        let pvs: Vec<(String, String)> = vg
            .get("physical_volumes")
            .and_then(Value::as_array)
            .into_iter()
            .flatten()
            .map(|pv| {
                let src = s(pv, "device").unwrap_or_default();
                (self.names.dev(&src).unwrap_or_else(|| format!("<{src}>")), s(pv, "id").unwrap_or_default())
            })
            .collect();
        let devices = pvs.iter().map(|(d, _)| d.as_str()).collect::<Vec<_>>().join(",");
        for (dev, id) in &pvs {
            out.push(Cmd::new(&lvm_argv("pvcreate", &devices, &["--uuid", id, "--restorefile", &file, dev])));
        }
        out.push(Cmd::new(&lvm_argv("vgcfgrestore", &devices, &["--file", &file, &name])));
        out.push(Cmd::new(&lvm_argv("vgchange", &devices, &["--activate", "y", &name])));
        // Thin pools and their thin LVs, which vgcfgrestore left out: made
        // empty by lvcreate, at the dump's sizes and names, before the
        // format stage formats the thin LVs.
        let thin = thin_of(vg).unwrap_or_default();
        self.notes.extend(thin.spare_note.clone());
        // Flags set once the stages that write are done (close runs its
        // closers last first, so these before the group's vgchange -an): a
        // read-only LV can't be formatted or restored into, and lvcreate
        // doesn't activate an LV it makes with ACTIVATION_SKIP.
        let mut later: Vec<Cmd> = vec![];
        let attrs = |lv: &str, at: &LvAttrs, a: &mut Vec<String>, later: &mut Vec<Cmd>| {
            for tag in &at.tags {
                a.extend(["--addtag".into(), tag.clone()]);
            }
            let full = format!("{name}/{lv}");
            if at.activation_skip {
                later.push(Cmd::new(&lvm_argv("lvchange", &devices, &["--setactivationskip", "y", &full])).why(format!("{full}: ACTIVATION_SKIP, as on the source")));
            }
            if at.read_only {
                later.push(Cmd::new(&lvm_argv("lvchange", &devices, &["--permission", "r", &full])).why(format!("{full}: read-only, as on the source")));
            }
        };
        for lv in vg.get("logical_volumes").and_then(Value::as_array).into_iter().flatten() {
            let ln = s(lv, "name").unwrap_or_default();
            if ln.is_empty() || thin.dropped.contains(&ln) {
                continue;
            }
            let at = LvAttrs::of(lv);
            let full = format!("{name}/{ln}");
            if at.activation_skip {
                later.push(Cmd::new(&lvm_argv("lvchange", &devices, &["--setactivationskip", "y", &full])).why(format!("{full}: ACTIVATION_SKIP, as on the source")));
            }
            if at.read_only {
                later.push(Cmd::new(&lvm_argv("lvchange", &devices, &["--permission", "r", &full])).why(format!("{full}: read-only, as on the source")));
            }
        }
        for p in &thin.pools {
            let (data, meta) = (lvm_size(p.data_bytes), lvm_size(p.meta_bytes));
            let mut a: Vec<String> = ["--yes", "--type", "thin-pool", "-n", &p.name, "-L", &data, "--poolmetadatasize", &meta].iter().map(|x| x.to_string()).collect();
            if let Some((n, size)) = p.data_stripes {
                a.extend(["-i".into(), n.to_string(), "-I".into(), lvm_size(size)]);
            }
            if let Some(c) = p.chunk_bytes {
                a.extend(["--chunksize".into(), lvm_size(c)]);
            }
            if let Some(d) = &p.discards {
                a.extend(["--discards".into(), d.clone()]);
            }
            a.extend(["--zero".into(), if p.zero { "y" } else { "n" }.into()]);
            // (Not --errorwhenfull: lvm2 2.03's lvcreate refuses it with
            // --type thin-pool, "unsupported with thin pools" (HoltTest1's
            // vanilla3, V7); a new pool queues writes when full, and
            // lvchange sets error-when-full just after, below, where the
            // source's had it.)
            a.extend(["--poolmetadataspare".into(), if thin.spare { "y" } else { "n" }.into()]);
            // A read-only pool stays as lvcreate makes it: its thin LVs are
            // what format and restore write to.
            let pool_attrs = LvAttrs { read_only: false, ..p.attrs.clone() };
            attrs(&p.name, &pool_attrs, &mut a, &mut later);
            a.push(name.clone());
            let rest: Vec<&str> = a.iter().map(String::as_str).collect();
            let thins = p.thins.iter().map(|l| format!("{name}/{}", l.name)).collect::<Vec<_>>().join(", ");
            out.push(Cmd::new(&lvm_argv("lvcreate", &devices, &rest)).why(format!(
                "volume group {name}: thin pool {} (data {}, metadata {}) made again by lvcreate, empty; its LV UUID, its data and metadata LVs', {} and their device ids are new (vgcfgrestore keeps the PVs', the group's and its other LVs' UUIDs); tags, activation skip and read-only are set as on the source, a profile and crop_metadata aren't carried over{}",
                p.name,
                crate::report::human(p.data_bytes),
                crate::report::human(p.meta_bytes),
                if thins.is_empty() { "its thin LVs' (it has none)".to_string() } else { format!("those of its thin LVs ({thins})") },
                if p.attrs.read_only { "; the pool itself was read-only, and is made read-write" } else { "" },
            )));
            // (Only then: lvchange fails when the flag is already as asked,
            // "Error when full is already unset".)
            if p.error_when_full {
                let full = format!("{name}/{}", p.name);
                out.push(Cmd::new(&lvm_argv("lvchange", &devices, &["--errorwhenfull", "y", &full])).why(format!("{full}: errors at once when its data space is full, as on the source")));
            }
            out.extend(super::rawvol::pool_use_note(&name, p, &mut self.problems));
            for l in &p.thins {
                // A thin snapshot waits for its origin's content: the restore
                // stage makes it (load/rawvol.rs).
                if l.origin.is_some() {
                    continue;
                }
                let v = lvm_size(l.bytes);
                let mut a: Vec<String> = ["--type", "thin", "-V", &v, "-n", &l.name, "--thinpool", &p.name].iter().map(|x| x.to_string()).collect();
                attrs(&l.name, &l.attrs, &mut a, &mut later);
                a.push(name.clone());
                let rest: Vec<&str> = a.iter().map(String::as_str).collect();
                out.push(Cmd::new(&lvm_argv("lvcreate", &devices, &rest)).why(format!("thin LV {name}/{}: {} virtual, device id {} on the source", l.name, crate::report::human(l.bytes), l.device_id)));
            }
        }
        self.closers.push(settled(&lvm_argv("vgchange", &devices, &["--activate", "n", &name])));
        self.closers.extend(later);
    }

    fn pool(&mut self, p: &Value, out: &mut Vec<Cmd>) {
        let name = s(p, "name").unwrap_or_default();
        let mut words = vec![];
        let mut leaves = vec![];
        let mut probs = vec![];
        let root = p.at(&["vdevs", "vdevs"]).and_then(Value::as_object).and_then(|o| o.first()).and_then(|(_, r)| r.get("vdevs")).cloned();
        let Some(root) = root else {
            self.problems.push(format!("ZFS pool {name}: its vdev tree isn't in the dump (zpool status -j failed when it was made)"));
            return;
        };
        vdev_words(&self.names, &root, "", &mut words, &mut leaves, &mut probs);
        for (k, class) in [("logs", "log"), ("l2cache", "cache"), ("spares", "spare")] {
            if let Some(t) = p.at(&["vdevs", k]).filter(|v| !v.is_null()) {
                vdev_words(&self.names, t, class, &mut words, &mut leaves, &mut probs);
            }
        }
        for k in ["special", "dedup"] {
            if p.at(&["vdevs", k]).is_some_and(|v| !v.is_null()) {
                probs.push(format!("ZFS pool {name} has {k} vdevs; version 1 doesn't make them"));
            }
        }
        self.problems.extend(probs);
        let mut a = vec!["zpool".to_string(), "create".into(), "-f".into(), "-d".into(), "-o".into(), "cachefile=none".into()];
        // The system load runs on has a pool of this name already (an
        // illumos rescue system's rpool): this one goes by another name
        // here, and keeps its own on the disks.
        let here = self.zname(&name);
        if here != name {
            a.extend(["-t".into(), here.clone()]);
        }
        self.truenas_pool_name(&name, &here);
        for (k, v) in p.get("local_properties").and_then(Value::as_object).into_iter().flatten() {
            if POOL_PROPS_SKIP.contains(&k.as_str()) {
                continue;
            }
            if let Some(v) = v.as_str().map(str::to_string).or_else(|| v.as_u64().map(|n| n.to_string())) {
                a.push("-o".into());
                a.push(format!("{k}={v}"));
            }
        }
        let mut min_ashift: Option<u64> = None;
        // The vdevs' ashift, from their labels, where the pool's property is
        // 0 (automatic): FreeBSD's installer raises the minimum to 12, and a
        // pool made here would take the disk's 512-byte sectors (9).
        let has_ashift = p.get("local_properties").and_then(|l| l.get("ashift")).is_some();
        if !has_ashift && matches!(p.at(&["properties", "ashift"]).and_then(Value::as_str), Some("0") | None) {
            let label_ashift = self.layout.arr(&["storage", "zfs", "pools_from_labels"]).iter().find(|l| s(l, "name").as_deref() == Some(name.as_str())).and_then(|l| {
                l.get("top_level_vdevs").and_then(Value::as_array).into_iter().flatten().filter_map(|v| v.get("ashift").and_then(Value::as_u64)).max()
            });
            // As FreeBSD's installer does it: through the minimum the
            // system picks, so that the pool's own property stays 0.
            min_ashift = label_ashift;
        }
        // A legacy pool (version 28, no feature flags) is made at its own
        // version, which takes no feature@ properties; left alone, zpool
        // create would make it 5000, which the source's older ZFS or boot
        // loader may not read.  (POOL_PROPS_SKIP keeps the local-property
        // loop from naming the version again.)
        let legacy = legacy_pool_version(p);
        if let Some(v) = legacy {
            a.push("-o".into());
            a.push(format!("version={v}"));
        }
        for (f, state) in p.get("features").and_then(Value::as_object).into_iter().flatten() {
            if legacy.is_none() && matches!(state.as_str(), Some("enabled" | "active")) {
                a.push("-o".into());
                a.push(format!("feature@{f}=enabled"));
            }
        }
        if let Some(ds) = self.dataset(&name) {
            self.new_key(&ds, out);
            for (k, v) in self.ds_props(&ds, true) {
                a.push("-O".into());
                a.push(format!("{k}={v}"));
            }
        }
        a.push("-R".into());
        a.push(self.opts.target.clone());
        a.push(name.clone());
        a.extend(words);
        let why = match legacy {
            Some(v) => format!("ZFS pool {name}, at the source's version {v} (no feature flags)"),
            None => format!("ZFS pool {name}, with the source's feature flags only"),
        };
        match min_ashift {
            Some(n) => {
                let create = a.iter().map(|w| crate::load::shell_quote(w)).collect::<Vec<_>>().join(" ");
                let p = "/sys/module/zfs/parameters/zfs_vdev_min_auto_ashift";
                let script = if self.fam == Some(crate::other::Family::FreeBsd) {
                    format!("old=$(sysctl -n vfs.zfs.min_auto_ashift); sysctl vfs.zfs.min_auto_ashift={n} >/dev/null; {create}; s=$?; sysctl vfs.zfs.min_auto_ashift=$old >/dev/null; exit $s")
                } else {
                    format!("old=$(cat {p}); echo {n} > {p}; {create}; s=$?; echo $old > {p}; exit $s")
                };
                out.push(Cmd::new(&["sh", "-c", &script]).why(format!("{why}; its vdevs' ashift {n}, as the source's")));
            }
            None => out.push(Cmd { argv: a, why: Some(why), ..Cmd::default() }),
        }
        let guid = self.layout.arr(&["storage", "zfs", "pools_from_labels"]).iter().find(|l| s(l, "name").as_deref() == Some(name.as_str())).and_then(|l| {
            l.get("pool_guid").and_then(|g| g.as_str().map(str::to_string).or_else(|| g.as_u64().map(|n| n.to_string())))
        });
        match guid {
            // illumos' zpool reguid has no -g.
            Some(_) if self.fam == Some(crate::other::Family::Illumos) => self.notes.push(format!("ZFS pool {name}: illumos can't set a pool's GUID; it gets a new one")),
            Some(g) => out.push(Cmd::new(&["zpool", "reguid", "-g", &g, &here]).why("the source pool's GUID (vdev GUIDs can't be kept)")),
            None => self.notes.push(format!("ZFS pool {name}: no pool GUID in the dump, so it gets a new one")),
        }
        // zpool create mounts the pool's own dataset (FreeBSD's zroot, at
        // /zroot) at once; mount mounts it again in its place, after /.
        let qhere = shell_quote(&here);
        out.push(Cmd::new(&["sh", "-c", &format!("[ \"$(zfs get -H -o value mounted {qhere})\" != yes ] || zfs unmount {qhere}")]).why("mount mounts every dataset in order"));
        // (On Linux, udev and its workers' mount namespaces can keep the
        // pool busy for a moment after the last unmount.)
        let export = ["zpool", "export", here.as_str()];
        self.closers.push(if self.fam.map_or(true, |f| f.on_linux()) { settled(&export) } else { Cmd::new(&export) });
        // Under another name here, the pool's own dataset would mount at
        // /<that name>: at the source's default place while load works,
        // inherited again before the export (closers run last first).
        if here != name && p.get("local_properties").and_then(|l| l.get("mountpoint")).is_none() {
            let local_mp = self.dataset(&name).and_then(|d| d.at(&["properties", "mountpoint", "source"]).and_then(Value::as_str).map(str::to_string));
            if local_mp.as_deref() != Some("local") {
                out.push(Cmd::new(&["zfs", "set", &format!("mountpoint=/{name}"), &here]).why(format!("{here} mounts where {name} does")));
                self.closers.push(Cmd::new(&["zfs", "inherit", "mountpoint", &here]));
            }
        }
        self.pools.push(here.clone());
        // Its zvols, now: what sits on them (LUKS: Ubuntu's keystore) is
        // made next in this stage.  Their parents first.
        for (_, zv) in self.zvols_of(&name) {
            let mut chain: Vec<String> = Vec::new();
            let mut p = zv.as_str();
            while let Some((parent, _)) = p.rsplit_once('/') {
                chain.push(parent.to_string());
                p = parent;
            }
            for d in chain.into_iter().rev().chain([zv.clone()]) {
                if let Some(ds) = self.dataset(&d).filter(|_| d != name && !self.datasets.contains(&self.zname(&d))) {
                    self.make_dataset(&ds, out);
                }
            }
        }
    }

    /// A pool's zvols that are devices in the layout: (zdN, dataset).
    fn zvols_of(&self, pool: &str) -> Vec<(String, String)> {
        let mut out = vec![];
        for d in self.layout.arr(&["storage", "disks"]) {
            let Some(name) = s(d, "name") else { continue };
            for l in d.get("links").and_then(Value::as_array).into_iter().flatten().filter_map(Value::as_str) {
                if let Some(ds) = l.strip_prefix("/dev/zvol/").filter(|ds| ds.split('/').next() == Some(pool)) {
                    out.push((name.clone(), ds.to_string()));
                }
            }
        }
        out
    }

    /// An encryption root whose key is a file (keyformat raw or hex) that
    /// --key-file doesn't give: a new random key, which the restored
    /// system finds where the source kept it (see keystores()).
    fn new_key(&mut self, ds: &Value, out: &mut Vec<Cmd>) {
        let name = s(ds, "name").unwrap_or_default();
        let fmt = s(ds, "keyformat").unwrap_or_default();
        if s(ds, "encryptionroot").as_deref() != Some(name.as_str()) || self.opts.key_file(&name).is_some() || !matches!(fmt.as_str(), "raw" | "hex") {
            return;
        }
        let key_name = name.replace('/', "_");
        if key_name.is_empty() || matches!(key_name.as_str(), "." | "..") || key_name.contains('\0') {
            self.problems.push(format!("ZFS encryption root {name:?}: invalid dataset name"));
            return;
        }
        let key = format!("{RUN_DIR}/keys/{key_name}");
        let qkey = shell_quote(&key);
        out.push(Cmd::new(&["mkdir", "-p", "-m", "700", &format!("{RUN_DIR}/keys")]));
        let make_key = if fmt == "raw" { format!("head -c 32 /dev/urandom > {qkey}") } else { format!("od -An -tx1 -N32 /dev/urandom | tr -d ' \\n' > {qkey}") };
        out.push(Cmd::new(&["sh", "-c", &format!("umask 077; {make_key}")]).why(format!("ZFS encryption root {name}: a new {fmt} key (the data is all rewritten by the restore)")));
    }

    fn dataset(&self, name: &str) -> Option<Value> {
        self.layout.v.at(&["storage", "zfs", "tools", "datasets"]).and_then(Value::as_array)?.iter().find(|d| s(d, "name").as_deref() == Some(name)).cloned()
    }

    /// A dataset's own properties for `zfs create -o` / `zpool create -O`.
    /// Encryption keys come from --key-file or a temporary key file.
    fn ds_props(&mut self, ds: &Value, _root: bool) -> Vec<(String, String)> {
        let name = s(ds, "name").unwrap_or_default();
        let mut out = vec![];
        let is_enc_root = s(ds, "encryptionroot").as_deref() == Some(name.as_str());
        for (k, v) in ds.get("properties").and_then(Value::as_object).into_iter().flatten() {
            if DS_PROPS_SKIP.contains(&k.as_str()) {
                continue;
            }
            let val = v.get("value").and_then(|x| x.as_str().map(str::to_string).or_else(|| x.as_u64().map(|n| n.to_string()))).unwrap_or_default();
            if k == "keylocation" {
                continue;
            }
            if matches!(k.as_str(), "encryption" | "keyformat" | "pbkdf2iters") && !is_enc_root {
                continue;
            }
            // A read-only dataset can't be restored into: made writable,
            // and read-only again at the end of the boot stage.
            if k == "readonly" && val == "on" {
                let here = self.zname(&name);
                if !self.readonly_later.contains(&here) {
                    self.readonly_later.push(here);
                }
                continue;
            }
            out.push((k.clone(), val));
        }
        // Properties set only when a dataset is made, which `zfs get -s
        // local` doesn't list: Debian's and Ubuntu's root pools have
        // normalization=formD and utf8only=on.
        if s(ds, "type").as_deref() == Some("volume") {
            if let Some(b) = num(ds, "volblocksize").filter(|_| !out.iter().any(|(k, _)| k == "volblocksize")) {
                out.push(("volblocksize".into(), b.to_string()));
            }
        } else {
            for (k, default) in [("normalization", "none"), ("utf8only", "off"), ("casesensitivity", "sensitive")] {
                if let Some(v) = s(ds, k).filter(|v| v != default && v != "-") {
                    if !out.iter().any(|(x, _)| x == k) {
                        out.push((k.into(), v));
                    }
                }
            }
        }
        // Not encrypted under an encrypted parent (Ubuntu's keystore zvol,
        // which holds the parent's key): says so, or it would inherit.
        let enc = s(ds, "encryption").unwrap_or_default();
        if enc == "off" && !out.iter().any(|(k, _)| k == "encryption") {
            let mut p = name.as_str();
            while let Some((parent, _)) = p.rsplit_once('/') {
                if self.dataset(parent).and_then(|d| s(&d, "encryption")).is_some_and(|e| e != "off") {
                    out.push(("encryption".into(), "off".into()));
                    break;
                }
                p = parent;
            }
        }
        if is_enc_root {
            // Set at creation, so `zfs get -s local` doesn't list them; the
            // dump has them as the dataset's own fields.
            for k in ["encryption", "keyformat"] {
                if let Some(v) = s(ds, k).filter(|v| !out.iter().any(|(x, _)| x == k) && v != "none" && v != "off") {
                    out.push((k.into(), v));
                }
            }
            let fmt = s(ds, "keyformat").unwrap_or_default();
            let key = match self.opts.key_file(&name) {
                Some(f) => f.to_string(),
                None => {
                    if !matches!(fmt.as_str(), "raw" | "hex") {
                        // A passphrase kept in a file inside the encrypted
                        // datasets (ZFSBootMenu's /etc/zfs/zroot.key) can't
                        // be read before they exist.
                        let kept = self.key_file_in_tree(ds).map(|p| format!(" (the source keeps its passphrase in {p}, inside the encrypted datasets: give that passphrase)")).unwrap_or_default();
                        self.problems.push(format!("ZFS encryption root {name}: give its new key with --key-file {name}=FILE{kept}"));
                    }
                    format!("{RUN_DIR}/keys/{}", name.replace('/', "_"))
                }
            };
            out.push(("keylocation".into(), format!("file://{key}")));
        }
        out
    }
}

// ---- format -------------------------------------------------------------

/// e2fsprogs feature names mke2fs can set; anything else in a superblock
/// (needs_recovery, orphan_present, ...) is state, not a choice.
pub(crate) const EXT_SETTABLE: [&str; 30] = [
    "has_journal", "ext_attr", "resize_inode", "dir_index", "sparse_super2", "fast_commit", "stable_inodes", "orphan_file", "filetype", "meta_bg", "extent", "64bit",
    "mmp", "flex_bg", "ea_inode", "metadata_csum_seed", "large_dir", "inline_data", "encrypt", "casefold", "sparse_super", "large_file", "huge_file",
    "dir_nlink", "extra_isize", "quota", "bigalloc", "metadata_csum", "project", "verity",
];

/// The -E resize=BLOCKS that has mke2fs reserve exactly the content's
/// reserved GDT blocks, worked out as mke2fs works them out from it
/// (e2fsprogs misc/mke2fs.c parse_extended_opts): ceil(ceil(BLOCKS / bpg) /
/// per block) descriptor blocks, less the ceil(ceil(blocks / bpg) / per
/// block) the filesystem itself has, both counting groups from block 0, not
/// from the first data block (1 with 1 KiB blocks).  None without
/// resize_inode, and for 0 reserved: mke2fs takes a resize= that reserves
/// none as no request, and refuses one that isn't past the size.
pub(crate) fn resize_for(c: &Value) -> Option<u64> {
    let has = |f: &str| c.get("features").and_then(Value::as_array).is_some_and(|a| a.iter().any(|x| x.as_str() == Some(f)));
    if !has("resize_inode") {
        return None;
    }
    let (r, bs, n, bpg) = (num(c, "reserved_gdt_blocks")?, num(c, "block_size")?, num(c, "blocks")?, num(c, "blocks_per_group")?);
    let desc = num(c, "descriptor_size").filter(|&d| d >= 32).unwrap_or(32);
    let gdpb = bs / desc;
    if r == 0 || gdpb == 0 || bpg == 0 {
        return None;
    }
    let d = n.div_ceil(bpg).div_ceil(gdpb);
    Some((d + r) * gdpb * bpg)
}

pub fn mke2fs(dev: &str, c: &Value) -> Vec<String> {
    let ty = s(c, "type").unwrap_or("ext4".into());
    let feats: Vec<String> = c.get("features").and_then(Value::as_array).into_iter().flatten().filter_map(|f| f.as_str()).filter(|f| EXT_SETTABLE.contains(f)).map(str::to_string).collect();
    let mut a = vec!["mke2fs".to_string(), "-F".into(), "-q".into(), "-t".into(), ty, "-O".into(), format!("none,{}", feats.join(","))];
    if let Some(u) = s(c, "uuid") {
        a.extend(["-U".into(), u]);
    }
    if let Some(l) = s(c, "label") {
        a.extend(["-L".into(), l]);
    }
    if let Some(b) = num(c, "block_size") {
        a.extend(["-b".into(), b.to_string()]);
    }
    if let Some(i) = num(c, "inode_size") {
        a.extend(["-I".into(), i.to_string()]);
    }
    if let Some(n) = num(c, "inodes") {
        a.extend(["-N".into(), n.to_string()]);
    }
    if let Some(g) = num(c, "blocks_per_group") {
        a.extend(["-g".into(), g.to_string()]);
    }
    if let Some(g) = num(c, "flex_bg_size") {
        a.extend(["-G".into(), g.to_string()]);
    }
    let mut ext = vec![];
    if let Some(h) = s(c, "hash_seed") {
        ext.push(format!("hash_seed={h}"));
    }
    if let Some(x) = num(c, "raid_stride") {
        ext.push(format!("stride={x}"));
    }
    if let Some(x) = num(c, "raid_stripe_width") {
        ext.push(format!("stripe_width={x}"));
    }
    if let Some(x) = num(c, "descriptor_size").filter(|&d| d > 32) {
        ext.push(format!("desc_size={x}"));
    }
    // The reserved GDT blocks (for online growing) as the source's: a
    // filesystem grown since it was made has fewer than mke2fs gives a new
    // one of its size.  mke2fs reserves enough for -E resize=BLOCKS.
    if let Some(r) = resize_for(c) {
        ext.push(format!("resize={r}"));
    }
    if !ext.is_empty() {
        a.extend(["-E".into(), ext.join(",")]);
    }
    if let (Some(b), Some(n)) = (num(c, "block_size"), num(c, "blocks")) {
        a.push(dev.to_string());
        a.push(format!("{}k", b * n / 1024));
    } else {
        a.push(dev.to_string());
    }
    a
}

pub fn mkfs_xfs(dev: &str, c: &Value, logdev: Option<&str>) -> Vec<String> {
    let f: Vec<&str> = c.get("features").and_then(Value::as_array).into_iter().flatten().filter_map(|x| x.as_str()).collect();
    let on = |n: &str| if f.contains(&n) { "1" } else { "0" };
    let mut m = vec![format!("crc={}", on("crc")), format!("finobt={}", on("finobt")), format!("rmapbt={}", on("rmapbt")), format!("reflink={}", on("reflink"))];
    m.push(format!("inobtcount={}", on("inobtcount")));
    m.push(format!("bigtime={}", on("bigtime")));
    if let Some(u) = s(c, "uuid") {
        m.push(format!("uuid={u}"));
    }
    // exchange, parent and metadir are named off too: xfsprogs 6.18's
    // defaults turn exchange and parent on (by the running kernel), and an
    // older distro's kernel or GRUB may not know them.  Every rescuer's
    // mkfs.xfs takes all three, off or on: the Debian 13 one's 6.13.0 and
    // the Linux image's 6.18.0 (both tried, 2026-11-14).  An xfsprogs older
    // than 6.13 would refuse metadir, and one older than 6.10 the other two.
    m.push(format!("metadir={}", on("metadir")));
    let mut i = vec![format!("sparse={}", on("sparse")), format!("nrext64={}", on("nrext64"))];
    if let Some(sz) = num(c, "inode_size") {
        i.push(format!("size={sz}"));
    }
    i.push(format!("exchange={}", on("exchange")));
    // The most of the space inodes may take: mkfs.xfs's default depends on
    // the size, which --fit-largest changes.  It's an inode option (-d
    // maxpct is refused).
    if let Some(p) = num(c, "imax_pct") {
        i.push(format!("maxpct={p}"));
    }
    let mut n = vec![format!("ftype={}", on("ftype"))];
    if let Some(d) = num(c, "dir_block_size") {
        n.push(format!("size={d}"));
    }
    n.push(format!("parent={}", on("parent")));
    let mut a = vec!["mkfs.xfs".to_string(), "-f".into(), "-m".into(), m.join(","), "-i".into(), i.join(","), "-n".into(), n.join(",")];
    if let Some(b) = num(c, "block_size") {
        a.extend(["-b".into(), format!("size={b}")]);
    }
    if let Some(sz) = num(c, "sector_size") {
        a.extend(["-s".into(), format!("size={sz}")]);
    }
    let mut d = vec![];
    // The source's data size and AG size where the dump has them: with
    // agcount alone, another mkfs.xfs version picks its own AG size (it
    // rounds to the stripe unit differently) and fills the device, so a
    // filesystem the source's installer made smaller than its LV changes.
    match (num(c, "blocks"), num(c, "ag_blocks"), num(c, "block_size")) {
        (Some(n), Some(ag), Some(b)) if n > 0 && ag > 0 => {
            d.push(format!("size={}", n * b));
            d.push(format!("agsize={}", ag * b));
        }
        _ => {
            if let Some(ag) = num(c, "ag_count") {
                d.push(format!("agcount={ag}"));
            }
        }
    }
    if let (Some(su), Some(sw)) = (num(c, "stripe_unit"), num(c, "stripe_width")) {
        if su > 0 {
            d.push(format!("sunit={}", su * num(c, "block_size").unwrap_or(4096) / 512));
            d.push(format!("swidth={}", sw * num(c, "block_size").unwrap_or(4096) / 512));
        }
    }
    if !d.is_empty() {
        a.extend(["-d".into(), d.join(",")]);
    }
    let mut l = vec![format!("lazy-count={}", on("lazysbcount"))];
    match logdev {
        Some(ld) => l.push(format!("logdev={ld}")),
        None => {
            if let (Some(lb), Some(b)) = (num(c, "log_blocks"), num(c, "block_size")) {
                l.push(format!("size={}", lb * b));
            }
        }
    }
    if let Some(v) = num(c, "log_version") {
        l.push(format!("version={v}"));
    }
    if let Some(su) = num(c, "log_stripe_unit").filter(|&s| s > 1) {
        l.push(format!("su={su}"));
    }
    a.extend(["-l".into(), l.join(",")]);
    if let Some(lb) = s(c, "label") {
        a.extend(["-L".into(), lb]);
    }
    a.push(dev.to_string());
    a
}

/// btrfs's superblock names to mkfs.btrfs -O names, for the features
/// mkfs.btrfs can choose.
pub(crate) const BTRFS_FEATURES: [(&str, &str); 9] = [
    ("extended_iref", "extref"),
    ("skinny_metadata", "skinny-metadata"),
    ("no_holes", "no-holes"),
    ("raid56", "raid56"),
    ("raid1c34", "raid1c34"),
    ("zoned", "zoned"),
    ("simple_quota", "squota"),
    ("free_space_tree", "free-space-tree"),
    ("block_group_tree", "block-group-tree"),
];

pub fn mkfs_btrfs(devs: &[String], c: &Value, fs: &Value) -> Vec<String> {
    let mut have: Vec<&str> = c.get("features").and_then(Value::as_array).into_iter().flatten().filter_map(|x| x.as_str()).collect();
    have.extend(c.get("compat_ro_features").and_then(Value::as_array).into_iter().flatten().filter_map(|x| x.as_str()));
    let feats: Vec<String> = BTRFS_FEATURES.iter().map(|(sb, opt)| if have.contains(sb) { opt.to_string() } else { format!("^{opt}") }).collect();
    let mut a = vec!["mkfs.btrfs".to_string(), "-f".into(), "-O".into(), feats.join(",")];
    if let Some(u) = s(c, "uuid") {
        a.extend(["-U".into(), u]);
    }
    if let Some(l) = s(c, "label") {
        a.extend(["-L".into(), l]);
    }
    if let Some(cs) = s(c, "checksum") {
        a.extend(["--csum".into(), cs]);
    }
    if let Some(n) = num(c, "node_size") {
        a.extend(["-n".into(), n.to_string()]);
    }
    if let Some(n) = num(c, "sector_size") {
        a.extend(["-s".into(), n.to_string()]);
    }
    if have.contains(&"mixed_groups") {
        a.push("--mixed".into());
    }
    for (kind, flag) in [("data", "-d"), ("metadata", "-m")] {
        if let Some(p) = fs.at(&["allocation_profiles", kind, "0"]).and_then(Value::as_str) {
            a.extend([flag.into(), p.to_string()]);
        }
    }
    // The device's own UUID (dev_item's), which mkfs.btrfs can set for a
    // single device only.
    if let (1, Some(u)) = (devs.len(), c.at(&["device", "uuid"]).and_then(Value::as_str)) {
        a.extend(["--device-uuid".into(), u.to_string()]);
    }
    a.extend(devs.iter().cloned());
    a
}

/// GRUB's environment block in btrfs's reserved area (probe/btrfs.rs),
/// written to DEV as the source's: its signature, VARS (as GRUB wrote
/// them) and '#' to the sector's end.  mkfs.btrfs zeroed it, and GRUB,
/// told by grubenv's `env_block=512+1` to read it, prints "invalid
/// environment block" at every boot (openSUSE Leap 16; docs/design/leap16.md).
pub fn grub_env_block(dev: &str, vars: &Value) -> Result<Cmd, String> {
    use crate::probe::btrfs::{GRUB_ENV_BLOCK, GRUB_ENV_SIGNATURE, GRUB_ENV_SIZE};
    let mut b = String::from_utf8_lossy(GRUB_ENV_SIGNATURE).into_owned();
    for (k, v) in vars.as_object().into_iter().flatten() {
        b += &format!("{k}={}\n", v.as_str().unwrap_or_default());
    }
    if b.len() > GRUB_ENV_SIZE {
        return Err(format!("GRUB's environment block on it holds {} bytes, more than its {GRUB_ENV_SIZE}", b.len()));
    }
    b += &"#".repeat(GRUB_ENV_SIZE - b.len());
    let seek = GRUB_ENV_BLOCK / GRUB_ENV_SIZE as u64;
    Ok(Cmd::new(&["dd".to_string(), format!("of={dev}"), format!("bs={GRUB_ENV_SIZE}"), format!("seek={seek}"), "count=1".into(), "iflag=fullblock".into(), "conv=notrunc,fsync".into(), "status=none".into()])
        .input(b)
        .why(format!("GRUB's environment block in btrfs's reserved area (grubenv's env_block={seek}+1), as the source's: mkfs.btrfs zeroed it")))
}

pub fn mkfs_fat(dev: &str, c: &Value) -> Vec<String> {
    let mut a = vec!["mkfs.fat".to_string()];
    if let Some(b) = num(c, "fat_bits") {
        a.extend(["-F".into(), b.to_string()]);
    }
    if let Some(u) = s(c, "uuid") {
        a.extend(["-i".into(), u.replace('-', "")]);
    }
    if let Some(l) = s(c, "label") {
        a.extend(["-n".into(), l]);
    }
    for (k, f) in [("bytes_per_sector", "-S"), ("sectors_per_cluster", "-s"), ("fats", "-f"), ("reserved_sectors", "-R"), ("root_entries", "-r"), ("hidden_sectors", "-h")] {
        if let Some(n) = num(c, k).filter(|&n| n > 0 || k == "hidden_sectors") {
            a.extend([f.into(), n.to_string()]);
        }
    }
    if let Some(m) = num(c, "media") {
        a.extend(["-M".into(), format!("0x{m:02x}")]);
    }
    a.push(dev.to_string());
    a
}

/// F2FS features mkfs.f2fs -O can set; the rest of the superblock's bits
/// (atomic_write, blkzoned) aren't chosen there.
pub(crate) const F2FS_SETTABLE: [&str; 13] = [
    "encrypt", "extra_attr", "project_quota", "inode_checksum", "flexible_inline_xattr", "quota", "inode_crtime", "lost_found", "verity", "sb_checksum",
    "casefold", "compression", "ro",
];

pub fn mkfs_f2fs(dev: &str, c: &Value) -> Vec<String> {
    let feats: Vec<String> = c.get("features").and_then(Value::as_array).into_iter().flatten().filter_map(|f| f.as_str()).filter(|f| F2FS_SETTABLE.contains(f)).map(str::to_string).collect();
    let mut a = vec!["mkfs.f2fs".to_string(), "-f".into(), "-q".into()];
    if let Some(u) = s(c, "uuid") {
        a.extend(["-U".into(), u]);
    }
    if let Some(l) = s(c, "label") {
        a.extend(["-l".into(), l]);
    }
    if !feats.is_empty() {
        a.extend(["-O".into(), feats.join(",")]);
    }
    if s(c, "encoding").as_deref() == Some("utf8") {
        a.extend(["-C".into(), "utf8".into()]);
    }
    if let Some(n) = num(c, "segments_per_section").filter(|&n| n > 1) {
        a.extend(["-s".into(), n.to_string()]);
    }
    if let Some(n) = num(c, "sections_per_zone").filter(|&n| n > 1) {
        a.extend(["-z".into(), n.to_string()]);
    }
    a.push(dev.to_string());
    a
}

/// BCH_VERSION(major, minor) = major << 10 | minor, from "1.13".
pub(crate) fn bcachefs_version(v: &str) -> Option<u32> {
    let (major, minor) = v.split_once('.')?;
    let (major, minor): (u32, u32) = (major.parse().ok()?, minor.parse().ok()?);
    (major < 64 && minor < 1024).then_some(major << 10 | minor)
}

/// bcachefs format with the source's UUID, label, options and on-disk
/// version: its version_min (the oldest version anything on it is
/// written in), or its version when it has none.
///
/// --no_initialize leaves the journal and the root directory to the
/// kernel's first mount, which is what format does by itself whenever the
/// version asked for isn't the tools' own (the image's 1.39 tools
/// recreating a 1.13 source).  When it is the tools' own, format would
/// open the filesystem in userspace to initialize it, and 1.13's tools
/// (the HoltTest rescuer's) corrupt their heap there in about one run of
/// three, dying of SIGSEGV or SIGABRT (`tcache_thread_shutdown(): unaligned
/// tcache chunk detected`) after the superblock is written.  The kernel
/// initializes it at its first mount instead, load's or the restored
/// system's, as it does for every other version.
pub fn mkfs_bcachefs(dev: &str, c: &Value) -> Vec<String> {
    let mut a = vec!["bcachefs".to_string(), "format".into(), "--force".into(), "--no_initialize".into()];
    // Left to itself, format writes the tools' newest version (1.39 from
    // the Linux image's 1.39 tools), which the source's older kernel may
    // not mount: the one-kernel rescue recreates the source's version
    // instead.  The number is BCH_VERSION as a decimal integer, which
    // both tools generations take the same way: 1.13's C cmd_format.c
    // reads --version with kstrtouint base 10, and 1.39's Rust
    // version_parse reads a bare integer as the minor with major 0, the
    // same number.  "1.13" wouldn't parse in the C tools.
    if let Some(n) = s(c, "version_min").or_else(|| s(c, "version")).as_deref().and_then(bcachefs_version) {
        a.push(format!("--version={n}"));
    }
    if let Some(u) = s(c, "uuid") {
        a.push(format!("--uuid={u}"));
    }
    if let Some(l) = s(c, "label") {
        a.push(format!("--fs_label={l}"));
    }
    for k in ["block_size", "btree_node_size"] {
        if let Some(n) = num(c, k).filter(|&n| n > 0) {
            a.push(format!("--{k}={n}"));
        }
    }
    for k in ["metadata_checksum", "data_checksum", "compression", "str_hash"] {
        // No compression is the default, and isn't a value --compression takes.
        if let Some(v) = s(c, k).filter(|v| !(k == "compression" && v == "none")) {
            a.push(format!("--{k}={v}"));
        }
    }
    if c.get("acl").and_then(Value::as_bool) == Some(true) {
        a.push("--acl".into());
    }
    for k in ["metadata_replicas", "data_replicas"] {
        if let Some(n) = num(c, k).filter(|&n| n > 1) {
            a.push(format!("--{k}={n}"));
        }
    }
    a.push(dev.to_string());
    a
}

/// mkfs.nilfs2 can't set a UUID: `nilfs-tune -U` sets it after (nilfs_tune).
pub fn mkfs_nilfs2(dev: &str, c: &Value) -> Vec<String> {
    let have: Vec<&str> = c.get("features").and_then(Value::as_array).into_iter().flatten().filter_map(|f| f.as_str()).collect();
    let mut a = vec!["mkfs.nilfs2".to_string(), "-f".into(), "-q".into()];
    if let Some(l) = s(c, "label") {
        a.extend(["-L".into(), l]);
    }
    if let Some(n) = num(c, "block_size") {
        a.extend(["-b".into(), n.to_string()]);
    }
    if let Some(n) = num(c, "blocks_per_segment") {
        a.extend(["-B".into(), n.to_string()]);
    }
    if let Some(n) = num(c, "reserved_segments_percentage") {
        a.extend(["-m".into(), n.to_string()]);
    }
    let feats: Vec<String> = crate::probe::nilfs2::FEATURES.iter().map(|(_, _, n)| if have.contains(n) { n.to_string() } else { format!("^{n}") }).collect();
    a.extend(["-O".into(), feats.join(",")]);
    a.push(dev.to_string());
    a
}

pub fn nilfs_tune(dev: &str, c: &Value) -> Option<Vec<String>> {
    s(c, "uuid").map(|u| vec!["nilfs-tune".to_string(), "-U".into(), u, dev.to_string()])
}

/// mkfs.jfs: 4 KiB blocks only, and no UUID option (jfs_tune sets it).
pub fn mkfs_jfs(dev: &str, c: &Value) -> Vec<String> {
    let mut a = vec!["mkfs.jfs".to_string(), "-q".into()];
    if let Some(l) = s(c, "label") {
        a.extend(["-L".into(), l]);
    }
    a.push(dev.to_string());
    a
}

pub fn jfs_tune(dev: &str, c: &Value) -> Option<Vec<String>> {
    s(c, "uuid").map(|u| vec!["jfs_tune".to_string(), "-U".into(), u, dev.to_string()])
}

/// s_version back to 1 in both superblocks (at 32 KiB and 60 KiB; the
/// field is their fifth byte).  A version-1 superblock still has its UUID
/// field: mkfs.jfs writes one there.
pub fn jfs_version1(dev: &str) -> Vec<String> {
    let qdev = shell_quote(dev);
    let w = |off: u32| format!("printf '\\001' | dd of={qdev} bs=1 seek={off} count=1 conv=notrunc status=none");
    vec!["sh".to_string(), "-c".into(), format!("{} && {}", w(0x8000 + 4), w(0xF000 + 4))]
}

pub fn mkswap(dev: &str, c: &Value) -> Vec<String> {
    let mut a = vec!["mkswap".to_string()];
    if let Some(u) = s(c, "uuid") {
        a.extend(["-U".into(), u]);
    }
    if let Some(l) = s(c, "label") {
        a.extend(["-L".into(), l]);
    }
    if let Some(p) = num(c, "page_size") {
        a.extend(["--pagesize".into(), p.to_string()]);
    }
    a.push(dev.to_string());
    a
}

/// Contents assemble makes, not format.
const MADE_BY_ASSEMBLE: [&str; 6] = ["linux_raid_member", "crypto_LUKS", "LVM2_member", "zfs_member", "xfs_external_log", "geli"];

impl Builder<'_> {
    /// Every source device that holds a filesystem or swap, with its content.
    fn leaves(&self) -> Vec<(String, Value)> {
        let mut out = Vec::new();
        for d in &self.layout.disks {
            if self.names.mapping.target_of(&d.name).is_none() {
                continue;
            }
            if !d.content.is_null() {
                out.push((d.name.clone(), d.content.clone()));
                out.extend(crate::other::geli::inner_leaf(self.layout, &d.name));
            }
            for p in &d.parts {
                if !p.content.is_null() {
                    out.push((p.name.clone(), p.content.clone()));
                    out.extend(crate::other::geli::inner_leaf(self.layout, &p.name));
                }
            }
            for p in self.layout.device(&d.name).and_then(|v| v.get("label_partitions")).and_then(Value::as_array).into_iter().flatten() {
                if let (Some(n), Some(c)) = (s(p, "name"), p.get("content").filter(|c| !c.is_null())) {
                    out.push((n, c.clone()));
                }
            }
        }
        for sect in ["md_arrays", "dm_devices"] {
            for x in self.layout.arr(&["storage", sect]) {
                // A thin pool's own devices (its data and metadata LVs, the
                // pool): what a probe found on them is a thin LV's, or
                // nothing.  lvcreate makes them (vg).
                if matches!(x.at(&["dm", "lv_layer"]).and_then(Value::as_str), Some("tdata" | "tmeta" | "tpool" | "pool")) {
                    continue;
                }
                if let (Some(n), Some(c)) = (s(x, "name"), x.get("content").filter(|c| !c.is_null())) {
                    out.push((n, c.clone()));
                }
            }
        }
        // OpenBSD's softraid volumes' disklabel partitions, once assemble
        // has planned the volume (other/softraid.rs).
        out.extend(crate::other::softraid::leaves(self.layout).into_iter().filter(|(n, _)| self.names.made.contains_key(n)));
        out
    }

    fn format(&mut self) -> Vec<Cmd> {
        let mut out = Vec::new();
        let mut btrfs_done: Vec<String> = Vec::new();
        for (src, c) in self.leaves() {
            let ty = s(&c, "type").unwrap_or_default();
            if MADE_BY_ASSEMBLE.contains(&ty.as_str()) {
                continue;
            }
            // A raw volume (load/rawvol.rs) gets its bytes in the restore
            // stage, whatever they hold.
            if self.is_raw_volume(&src) {
                continue;
            }
            // crypttab makes it, and what's on it, at boot (items()).
            if self.layout.random_key_crypt(&src).is_some() {
                continue;
            }
            let Some(dev) = self.names.dev(&src) else {
                if !self.names.made.contains_key(&src) {
                    self.problems.push(format!("{src} holds {ty}, but nothing in this layout makes {src}"));
                }
                continue;
            };
            let why = format!("{src}: {ty}{}", s(&c, "label").map(|l| format!(" \"{l}\"")).unwrap_or_default());
            // An ESP written byte for byte by the partition stage (esp_image)
            // isn't format's.
            let imaged = self.layout.device(&src).is_some_and(|v| v.get("esp_image").is_some());
            // Bug: format() omitted bcachefs from self.filesystems (so status
            // ignored bcachefs and --through skipped formatting BIOS bcachefs
            // roots) and pushed before checking rejected bcachefs/jfs options
            // or unformatted non-Linux swap. Push only for formatted targets.
            let non_linux_swap = ty == "swap" && self.fam.is_some_and(|f| !f.on_linux());
            let bad_fs = (ty == "bcachefs" && (c.get("encrypted").and_then(Value::as_bool) == Some(true) || num(&c, "nr_devices").unwrap_or(1) > 1))
                || (ty == "jfs" && c.get("external_log").and_then(Value::as_bool) == Some(true));
            if !imaged && !non_linux_swap && !bad_fs && matches!(ty.as_str(), "ext2" | "ext3" | "ext4" | "xfs" | "btrfs" | "f2fs" | "bcachefs" | "nilfs2" | "jfs" | "vfat" | "msdos" | "swap") {
                self.filesystems.push((dev.clone(), ty.clone(), s(&c, "uuid")));
            }
            // plan's check 4: a feature the probes can't name is one load
            // can't ask for, nor read back.
            let unnamed = super::readback::unnamed(&c);
            if !imaged && super::readback::reads_back(&ty) && !unnamed.is_empty() {
                self.problems.push(format!("{src}: its {ty} has features bko-holt can't name ({}), so load can't promise to make them again", unnamed.join(", ")));
            }
            match ty.as_str() {
                "ext2" | "ext3" | "ext4" => {
                    out.push(Cmd { argv: mke2fs(&dev, &c), why: Some(why), ..Cmd::default() });
                    // The exact count: mke2fs takes only a percentage, and
                    // rounds it down.
                    if let Some(r) = num(&c, "reserved_blocks") {
                        out.push(Cmd::new(&["tune2fs", "-r", &r.to_string(), &dev]));
                    }
                    out.push(Cmd::read_back(&dev, &c));
                }
                "xfs" => {
                    let uuid = s(&c, "uuid").unwrap_or_default();
                    let logdev = self
                        .layout
                        .arr(&["storage", "xfs_external_logs"])
                        .iter()
                        .find(|l| s(l, "filesystem_uuid").as_deref() == Some(uuid.as_str()))
                        .and_then(|l| s(l, "log_device"))
                        .and_then(|ld| self.names.dev(&ld));
                    out.push(Cmd { argv: mkfs_xfs(&dev, &c, logdev.as_deref()), why: Some(why), ..Cmd::default() });
                    out.push(Cmd::read_back(&dev, &c));
                }
                "btrfs" => {
                    let uuid = s(&c, "uuid").unwrap_or_default();
                    if btrfs_done.contains(&uuid) {
                        continue;
                    }
                    btrfs_done.push(uuid.clone());
                    let fs = self.layout.arr(&["storage", "btrfs"]).iter().find(|f| s(f, "uuid").as_deref() == Some(uuid.as_str())).cloned().unwrap_or(Value::Null);
                    let mut members: Vec<(u64, String, Option<Value>)> = vec![];
                    for m in fs.get("devices").and_then(Value::as_array).into_iter().flatten().filter_map(|x| x.as_str()) {
                        let mc = self.layout.content(m);
                        let devid = mc.and_then(|c| num(c, "devid")).unwrap_or(0);
                        members.push((devid, self.names.dev(m).unwrap_or_else(|| format!("<{m}>")), mc.and_then(|c| c.get("grub_env_block")).cloned()));
                    }
                    if members.is_empty() {
                        members.push((1, dev.clone(), c.get("grub_env_block").cloned()));
                    }
                    members.sort_by(|a, b| (a.0, &a.1).cmp(&(b.0, &b.1)));
                    let devs: Vec<String> = members.iter().map(|m| m.1.clone()).collect();
                    out.push(Cmd { argv: mkfs_btrfs(&devs, &c, &fs), why: Some(why), ..Cmd::default() });
                    out.push(Cmd::read_back(&dev, &c));
                    for (_, d, env) in &members {
                        match env.as_ref().filter(|e| !e.is_null()).map(|e| grub_env_block(d, e)) {
                            Some(Ok(cmd)) => out.push(cmd),
                            Some(Err(e)) => self.problems.push(format!("{src}: {e}")),
                            None => {}
                        }
                    }
                    self.btrfs_inside(&uuid, &devs[0], &fs, &mut out);
                }
                "vfat" | "msdos" if self.fam.is_none_or(|f| f.on_linux()) && self.layout.device(&src).is_none_or(|v| v.get("esp_image").is_none()) => {
                    out.push(Cmd { argv: mkfs_fat(&dev, &c), why: Some(why), ..Cmd::default() });
                    out.push(Cmd::read_back(&dev, &c));
                }
                "f2fs" => {
                    out.push(Cmd { argv: mkfs_f2fs(&dev, &c), why: Some(why), ..Cmd::default() });
                    out.push(Cmd::read_back(&dev, &c));
                }
                "bcachefs" => {
                    if c.get("encrypted").and_then(Value::as_bool) == Some(true) {
                        self.problems.push(format!("{src}: encrypted bcachefs isn't one bko-holt load can make"));
                        continue;
                    }
                    if num(&c, "nr_devices").unwrap_or(1) > 1 {
                        self.problems.push(format!("{src}: bcachefs on more than one device isn't one bko-holt load can make"));
                        continue;
                    }
                    out.push(Cmd { argv: mkfs_bcachefs(&dev, &c), why: Some(why), ..Cmd::default() });
                    out.push(Cmd::read_back(&dev, &c));
                }
                "nilfs2" => {
                    out.push(Cmd { argv: mkfs_nilfs2(&dev, &c), why: Some(why), ..Cmd::default() });
                    if let Some(t) = nilfs_tune(&dev, &c) {
                        out.push(Cmd { argv: t, why: Some("the source's UUID (mkfs.nilfs2 can't set one)".into()), ..Cmd::default() });
                    }
                    out.push(Cmd::read_back(&dev, &c));
                }
                "jfs" => {
                    if c.get("external_log").and_then(Value::as_bool) == Some(true) {
                        self.problems.push(format!("{src}: JFS with an external log isn't one bko-holt load can make"));
                        continue;
                    }
                    out.push(Cmd { argv: mkfs_jfs(&dev, &c), why: Some(why), ..Cmd::default() });
                    if let Some(t) = jfs_tune(&dev, &c) {
                        out.push(Cmd { argv: t, why: Some("the source's UUID (mkfs.jfs can't set one)".into()), ..Cmd::default() });
                        if s(&c, "version").as_deref() == Some("1") {
                            out.push(Cmd { argv: jfs_version1(&dev), why: Some("jfs_tune -U makes the superblock version 2; mkfs.jfs's, and the source's, is 1".into()), ..Cmd::default() });
                        }
                    }
                    out.push(Cmd::read_back(&dev, &c));
                }
                "swap" if self.fam.is_some_and(|f| !f.on_linux()) => self.notes.push(format!("{src}: swap needs no format here")),
                "swap" => {
                    if c.get("hibernation_image").is_some_and(|h| !h.is_null()) {
                        self.notes.push(format!("{src}: the source's swap held a hibernation image; the new swap doesn't"));
                    }
                    out.push(Cmd { argv: mkswap(&dev, &c), why: Some(why), ..Cmd::default() });
                    out.push(Cmd::read_back(&dev, &c));
                }
                "ntfs" | "exfat" => self.notes.push(format!("{src}: {ty} isn't restored by BackOtter; the partition is left empty")),
                "vfat" | "msdos" if self.layout.device(&src).is_some_and(|v| v.get("esp_image").is_some()) => {
                    self.notes.push(format!("{src}: the ESP, written as the source's with the partition table"));
                }
                "vfat" | "msdos" => out.extend(self.other_newfs_msdos(&src, &dev, &c)),
                "redoxfs" => {
                    self.filesystems.push((dev.clone(), ty.clone(), None));
                    out.push(Cmd::new(&["redoxfs-mkfs", &dev]).why(format!("{src}: RedoxFS")));
                }
                "befs" if self.fam.is_some() => {
                    self.filesystems.push((dev.clone(), ty.clone(), None));
                    let c2 = self.other_mkbfs(&src, &dev, &c);
                    out.extend(c2);
                }
                "hammer2" if self.fam.is_some() => {
                    self.filesystems.push((dev.clone(), ty.clone(), None));
                    let c = self.other_newfs_hammer2(&src, &dev);
                    out.extend(c);
                }
                "ufs" if self.fam.is_some() => {
                    self.filesystems.push((dev.clone(), ty.clone(), None));
                    let c = self.other_newfs(&src, &dev);
                    out.extend(c);
                }
                other => self.problems.push(format!("{src}: {other} isn't a filesystem bko-holt load can make")),
            }
        }
        self.datasets(&mut out);
        self.keystores(&mut out);
        if self.fam == Some(crate::other::Family::FreeBsd) {
            out.extend(self.other_glabels());
        }
        out
    }

    /// An encryption root whose key file lives on a filesystem this layout
    /// makes (Ubuntu's keystore: /run/keystore/rpool/system.key on LUKS on
    /// the zvol rpool/keystore): its new key goes there, so the restored
    /// system finds it where the source's was.  Only a mount that the dump
    /// leaves out of the backup is written: the deepest device-backed mount
    /// above the key file, not in fstab, at /run or under it (report.rs
    /// not_backed_up()'s rule).  Any other mount is backed up, so the
    /// restore would write the old key over the new one: a random raw/hex
    /// key then needs --key-file (refuse), and a given key or passphrase
    /// comes back with the restore, as backed up.
    fn keystores(&mut self, out: &mut Vec<Cmd>) {
        // Bug: keystores() missed /run keystores mounted via /dev/vg/lv,
        // /dev/md/NAME, /dev/zvol/..., /dev/disk/by-*, or mounts[].device, did
        // not normalize trailing slashes on fstab/mount targets, and silently
        // skipped installing --key-file onto an unmapped or unformatted block
        // keystore while boot still set keylocation=file:///run/..., leaving the
        // restored pool unbootable. Resolve via mounts[].device and src_of_path,
        // normalize slashes, and require the keystore block device to be formatted.
        fn trim(p: &str) -> &str {
            if p == "/" { "/" } else { p.trim_end_matches('/') }
        }
        let mounts = self.layout.arr(&["storage", "mounts"]).to_vec();
        let fstab_mounts: Vec<String> = self
            .layout
            .arr(&["storage", "fstab"])
            .iter()
            .filter(|e| s(e, "type").as_deref() != Some("swap"))
            .filter_map(|e| s(e, "file").map(|f| trim(&f).to_string()))
            .collect();
        for ds in self.layout.v.at(&["storage", "zfs", "tools", "datasets"]).and_then(Value::as_array).cloned().unwrap_or_default() {
            let name = s(&ds, "name").unwrap_or_default();
            if s(&ds, "encryptionroot").as_deref() != Some(name.as_str()) {
                continue;
            }
            let pool = name.split('/').next().unwrap_or("");
            if !self.pools.contains(&self.zname(pool)) {
                continue;
            }
            let fmt = s(&ds, "keyformat").unwrap_or_default();
            let need_random = self.opts.key_file(&name).is_none() && matches!(fmt.as_str(), "raw" | "hex");
            let refuse = |this: &mut Self| {
                if need_random {
                    let kept = this.key_file_in_tree(&ds).map(|p| format!(" (the source keeps its {fmt} key in {p}, inside the encrypted datasets: give that key)")).unwrap_or_default();
                    this.problem_once(format!("ZFS encryption root {name}: give its new key with --key-file {name}=FILE{kept}"));
                }
            };
            let Some(path) = s(&ds, "keylocation").and_then(|l| l.strip_prefix("file://").map(str::to_string)).filter(|p| safe_target_subpath(p)) else {
                refuse(self);
                continue;
            };
            let Some(m) = mounts
                .iter()
                .filter_map(|m| Some((s(m, "target")?, s(m, "source")?, s(m, "device"))))
                .filter(|(t, _, _)| t == "/" || path.starts_with(&format!("{}/", t.trim_end_matches('/'))))
                .max_by_key(|(t, _, _)| trim(t).len())
            else {
                refuse(self);
                continue;
            };
            let base = trim(&m.0);
            if fstab_mounts.iter().any(|f| f == base) || (base != "/run" && !base.starts_with("/run/")) {
                refuse(self);
                continue;
            }
            let src_dev = m
                .2
                .clone()
                .filter(|n| self.names.dev(n).is_some())
                .or_else(|| self.names.src_of_path(&m.1))
                .or_else(|| {
                    self.layout
                        .arr(&["storage", "dm_devices"])
                        .iter()
                        .chain(self.layout.arr(&["storage", "md_arrays"]))
                        .chain(self.layout.arr(&["storage", "disks"]))
                        .find_map(|d| {
                            let n = s(d, "name")?;
                            let mapper = d.at(&["dm", "mapper_path"]).and_then(Value::as_str);
                            if m.1 == format!("/dev/{n}") || Some(m.1.as_str()) == mapper {
                                return Some(n);
                            }
                            d.get("partitions").and_then(Value::as_array).into_iter().flatten().find_map(|p| {
                                let pn = s(p, "name")?;
                                (m.1 == format!("/dev/{pn}")).then_some(pn)
                            })
                        })
                });
            let Some(dev) = src_dev.and_then(|n| self.names.dev(&n)).filter(|d| self.filesystems.iter().any(|(f, _, _)| f == d)) else {
                if m.1.starts_with("/dev/") || m.2.is_some() {
                    self.problem_once(format!("ZFS encryption root {name}: its key file {path} lives on {}, which this layout doesn't format", m.1));
                } else {
                    refuse(self);
                }
                continue;
            };
            let key = match self.opts.key_file(&name) {
                Some(f) => f.to_string(),
                None => match temp_key_name(&name) {
                    Some(k) => format!("{RUN_DIR}/keys/{k}"),
                    None => {
                        refuse(self);
                        continue;
                    }
                },
            };
            let mnt = format!("{RUN_DIR}/keystore");
            let base = m.0.trim_end_matches('/');
            let rel = &path[base.len()..];
            let dest = format!("{mnt}{rel}");
            let dir = dest.rsplit_once('/').map(|(d, _)| d.to_string()).unwrap_or(mnt.clone());
            out.push(Cmd::new(&["mkdir", "-p", &mnt]));
            out.push(Cmd::new(&["mount", &dev, &mnt]).why(format!("{name}'s key file {path} lives on {}", m.1)));
            out.push(Cmd::new(&["mkdir", "-p", &dir]));
            out.push(Cmd::new(&["install", "-m", "400", &key, &dest]));
            out.push(Cmd::new(&["umount", &mnt]));
        }
    }

    /// The subvolumes a btrfs's mounts name, for a layout that doesn't list
    /// them (a dump that couldn't run btrfs: NixOS's, before its tools were
    /// on the dump's path): fstab's subvol= and those mounted when it was
    /// dumped, without the top level, parents first.
    fn btrfs_named_subvolumes(&self, fs: &Value) -> Vec<String> {
        let devs: Vec<String> = fs.get("devices").and_then(Value::as_array).into_iter().flatten().filter_map(|d| d.as_str().map(str::to_string)).collect();
        let strip = |p: &str| p.trim_start_matches("<FS_TREE>/").trim_matches('/').to_string();
        let mut names: Vec<String> = Vec::new();
        for e in self.layout.arr(&["storage", "fstab"]) {
            if s(e, "type").as_deref() != Some("btrfs") || !s(e, "resolves_to").is_some_and(|r| devs.contains(&r)) {
                continue;
            }
            let o = s(e, "options").unwrap_or_default();
            names.extend(o.split(',').filter_map(|x| x.strip_prefix("subvol=")).map(strip));
        }
        names.extend(fs.get("mounted_subvolumes").and_then(Value::as_array).into_iter().flatten().filter_map(|m| s(m, "subvol")).map(|p| strip(&p)));
        names.retain(|p| !p.is_empty() && !p.split('/').any(|c| c.is_empty() || c == "." || c == ".."));
        names.sort_by(|a, b| a.matches('/').count().cmp(&b.matches('/').count()).then(a.cmp(b)));
        names.dedup();
        names
    }

    fn btrfs_inside(&mut self, uuid: &str, dev: &str, fs: &Value, out: &mut Vec<Cmd>) {
        let mut subs = fs.get("subvolumes").and_then(Value::as_array).cloned().unwrap_or_default();
        if fs.get("subvolumes").and_then(Value::as_array).is_none() {
            let named = self.btrfs_named_subvolumes(fs);
            if !named.is_empty() {
                self.notes.push(format!(
                    "btrfs {uuid}: the layout doesn't list its subvolumes (its dump couldn't run btrfs); made are those its mounts name, {}: any other comes back as a plain directory, without its properties",
                    named.join(", ")
                ));
                subs = named.into_iter().map(|p| crate::json::Obj::new().set("path", p).build()).collect();
            }
        }
        // Timeshift's snapshots aren't made, nor their NAME directories
        // (btrfs_plans noted them).
        let ts_ids = self.timeshift_ids(fs);
        subs.retain(|v| !self.is_timeshift(fs, v));
        let quota = fs.get("quota_enabled").and_then(Value::as_bool).unwrap_or(false);
        let default_id = fs.at(&["default_subvolume", "id"]).and_then(Value::as_u64).unwrap_or(5);
        // `btrfs subvolume set-default` sets the incompat flag DEFAULT_SUBVOL
        // whatever the ID it names, and nothing else sets it: a source on
        // which it was run for ID 5 (BigLinux's big-btrfs-fix-to-timeshift,
        // at every boot) has the flag with the top level as its default.
        let flag_only = default_id == 5 && fs.get("features").and_then(Value::as_array).is_some_and(|f| f.iter().any(|x| x.as_str() == Some("default_subvol")));
        if subs.is_empty() && !quota && default_id == 5 && !flag_only {
            return;
        }
        let top = format!("{RUN_DIR}/btrfs/{uuid}");
        out.push(Cmd::new(&["mkdir", "-p", &top]));
        out.push(Cmd::new(&["mount", "-t", "btrfs", "-o", "subvolid=5", dev, &top]).why(format!("btrfs {uuid}: its subvolumes")));
        let mut list: Vec<(String, Value)> = subs.iter().filter_map(|v| Some((s(v, "path")?.trim_start_matches("<FS_TREE>/").to_string(), v.clone()))).collect();
        // In the source's ID order, so that each gets the source's ID: btrfs
        // numbers subvolumes from 256 up, and a gap (a deleted subvolume)
        // is burnt with a placeholder, deleted once all are made.  A
        // subvolume inside another always has the larger ID.
        list.sort_by_key(|(p, v)| (v.get("id").and_then(Value::as_u64).unwrap_or(u64::MAX), p.clone()));
        let mut next = 256u64;
        let mut placeholders = Vec::new();
        let mut made: Vec<(String, String)> = Vec::new();
        // (path, its origin) of each made as a snapshot, and every path made.
        let mut snapped: Vec<(String, String)> = Vec::new();
        let mut made_paths: Vec<String> = Vec::new();
        let plan = self.btrfs.get(uuid).cloned().unwrap_or_default();
        for (path, v) in &list {
            // A shared snapshot is made after the restore (restore stage).
            if plan.is_shared(path) {
                continue;
            }
            if let Some(id) = v.get("id").and_then(Value::as_u64) {
                while next < id && id - next <= 4096 {
                    let ph = format!("{top}/.bko-holt-placeholder-{next}");
                    out.push(Cmd::new(&["btrfs", "subvolume", "create", &ph]).why(format!("burns ID {next}, unused on the source")));
                    placeholders.push(ph);
                    next += 1;
                }
                next = next.max(id + 1);
            }
            let full = format!("{top}/{path}");
            if let Some((parent, _)) = path.rsplit_once('/') {
                out.push(Cmd::new(&["mkdir", "-p", &format!("{top}/{parent}")]));
            }
            // Inside a subvolume made as a snapshot of one that has a
            // subvolume here too (transactional-update's /etc in each slot):
            // the snapshot has an empty directory in its place, which goes.
            let holder = made_paths.iter().filter(|m| path.starts_with(&format!("{m}/"))).max_by_key(|m| m.len());
            if let Some((h, o)) = holder.and_then(|h| snapped.iter().find(|(p, _)| p == h)) {
                let theirs = format!("{o}/{}", &path[h.len() + 1..]);
                if made_paths.contains(&theirs) {
                    out.push(Cmd::new(&["rmdir", &full]).why(format!("{h}, a snapshot of {o}, has an empty directory where {theirs} is nested: {path} is made in its place")));
                }
            }
            // A snapshot is made as one of its origin (still empty: the
            // restore fills both), so that it stays a snapshot.
            let origin = v.get("parent_uuid").and_then(Value::as_str).and_then(|pu| made.iter().find(|(u, _)| u == pu)).map(|(_, p)| p.clone());
            if let Some(o) = &origin {
                snapped.push((path.clone(), o.clone()));
            }
            made_paths.push(path.clone());
            match &origin {
                Some(o) => out.push(Cmd::new(&["btrfs", "subvolume", "snapshot", &format!("{top}/{o}"), &full]).why(format!("a snapshot of {o}, as on the source"))),
                None => out.push(Cmd::new(&["btrfs", "subvolume", "create", &full])),
            }
            if let Some(u) = s(v, "uuid") {
                made.push((u, path.clone()));
            }
            for (k, pv) in v.get("properties").and_then(Value::as_object).into_iter().flatten() {
                if k == "ro" {
                    continue; // read-only comes after the restore (boot)
                }
                if let Some(val) = pv.as_str() {
                    out.push(Cmd::new(&["btrfs", "property", "set", &full, k, val]));
                }
            }
            let later = v.get("parent_uuid").and_then(Value::as_str).and_then(|pu| list.iter().find(|(p, w)| plan.is_shared(p) && w.get("uuid").and_then(Value::as_str) == Some(pu)));
            if let Some((o, _)) = later {
                self.notes.push(format!("btrfs {uuid}: {path} was a snapshot of {o}, which is made after the restore; it's made as a plain subvolume"));
            } else if origin.is_none() && v.get("parent_uuid").is_some_and(|p| !p.is_null()) {
                self.notes.push(format!("btrfs {uuid}: {path} was a snapshot of a subvolume that's gone; it's made as a plain subvolume"));
            }
        }
        if !placeholders.is_empty() {
            let mut a = vec!["btrfs".to_string(), "subvolume".into(), "delete".into(), "--commit-after".into()];
            a.extend(placeholders);
            out.push(Cmd { argv: a, ..Cmd::default() });
            out.push(Cmd::new(&["btrfs", "subvolume", "sync", &top]).why("the placeholders gone before quotas are on (no qgroups for them)"));
        }
        if default_id != 5 {
            match list.iter().find(|(_, v)| v.get("id").and_then(Value::as_u64) == Some(default_id)) {
                Some((p, _)) if plan.is_shared(p) => {} // set once it's made
                Some((p, _)) => out.push(Cmd::new(&["btrfs", "subvolume", "set-default", &format!("{top}/{p}")])),
                None if ts_ids.contains(&default_id) => self.problems.push(format!("btrfs {uuid}: the default subvolume (ID {default_id}) is a Timeshift snapshot, which isn't in the backup")),
                None => self.problems.push(format!("btrfs {uuid}: the default subvolume (ID {default_id}) isn't in its subvolume list")),
            }
        } else if flag_only {
            out.push(Cmd::new(&["btrfs", "subvolume", "set-default", &top]).why(format!("btrfs {uuid}: the default stays the top level (ID 5), set as on the source, whose superblock has the default_subvol flag that only set-default sets")));
        }
        // With shared snapshots, quotas go on after the restore, which they
        // would slow down, with the IDs those got.
        if quota && plan.shared.is_empty() {
            self.btrfs_quota(fs, &top, &plan, out);
        }
        out.push(Cmd::new(&["umount", &top]));
    }

    /// Quotas, as the source had them: on, the higher-level qgroups
    /// (snapper's 1/0), their members and limits.  A shared snapshot's
    /// level-0 qgroup is by the ID it has here.
    fn btrfs_quota(&self, fs: &Value, top: &str, plan: &crate::load::shared::Plan, out: &mut Vec<Cmd>) {
        let top = top.to_string();
        let here = |id: &str| -> String {
            id.strip_prefix("0/")
                .and_then(|n| n.parse::<u64>().ok())
                .and_then(|n| plan.new_id_of(n))
                .map_or_else(|| id.to_string(), |n| format!("0/{n}"))
        };
        {
            out.push(Cmd::new(&["btrfs", "quota", "enable", &top]));
            out.push(Cmd::new(&["btrfs", "quota", "rescan", "-w", &top]));
            let mut qgroups = fs.get("qgroups").and_then(Value::as_array).cloned().unwrap_or_default();
            // A Timeshift snapshot's level-0 qgroup: its subvolume isn't made.
            let ts: Vec<String> = self.timeshift_ids(fs).iter().map(|i| format!("0/{i}")).collect();
            qgroups.retain(|q| !s(q, "qgroupid").is_some_and(|id| ts.contains(&id)));
            // Higher-level qgroups (snapper's 1/0), their members, and limits.
            for q in &qgroups {
                let Some(id) = s(q, "qgroupid") else { continue };
                if !id.starts_with("0/") {
                    out.push(Cmd::new(&["btrfs", "qgroup", "create", &id, &top]));
                }
            }
            for q in &qgroups {
                let Some(id) = s(q, "qgroupid").map(|i| here(&i)) else { continue };
                for p in q.get("parents").and_then(Value::as_array).into_iter().flatten().filter_map(Value::as_str) {
                    out.push(Cmd::new(&["btrfs", "qgroup", "assign", "--no-rescan", &id, p, &top]));
                }
                for (k, flag) in [("max_referenced", None), ("max_exclusive", Some("-e"))] {
                    if let Some(n) = num(q, k) {
                        let mut a = vec!["btrfs".to_string(), "qgroup".into(), "limit".into()];
                        a.extend(flag.map(str::to_string));
                        a.extend([n.to_string(), id.clone(), top.clone()]);
                        out.push(Cmd { argv: a, ..Cmd::default() });
                    }
                }
            }
            if qgroups.iter().any(|q| q.get("parents").and_then(Value::as_array).is_some_and(|p| !p.is_empty())) {
                out.push(Cmd::new(&["btrfs", "quota", "rescan", "-w", &top]));
            }
        }
    }

    /// Each btrfs's plan of shared snapshots (shared.rs), from its
    /// subvolumes and the mounts the mount stage makes of it; with
    /// --btrfs-snapshots copy, only its aliases.  Not by --repo: each stage
    /// may run on its own (--only), and format's must be restore's.
    fn btrfs_plans(&mut self) {
        use crate::load::shared::{Mount, Sub};
        if self.fam.is_some() {
            return;
        }
        let share = !self.opts.btrfs_copy;
        for fs in self.layout.arr(&["storage", "btrfs"]).to_vec() {
            let Some(uuid) = s(&fs, "uuid") else { continue };
            let strip = |p: &str| p.trim_start_matches("<FS_TREE>/").trim_start_matches('/').to_string();
            let mut subs: Vec<Sub> = fs
                .get("subvolumes")
                .and_then(Value::as_array)
                .into_iter()
                .flatten()
                .filter_map(|v| {
                    Some(Sub {
                        path: strip(&s(v, "path")?),
                        id: v.get("id").and_then(Value::as_u64),
                        generation: v.get("gen").and_then(Value::as_u64),
                        uuid: s(v, "uuid"),
                        parent_uuid: s(v, "parent_uuid"),
                        readonly: v.get("readonly").and_then(Value::as_bool) == Some(true),
                    })
                })
                .collect();
            let devs: Vec<String> = fs.get("devices").and_then(Value::as_array).into_iter().flatten().filter_map(|d| d.as_str().map(str::to_string)).collect();
            let by_id = |id: u64| if id == 5 { Some(String::new()) } else { subs.iter().find(|x| x.id == Some(id)).map(|x| x.path.clone()) };
            let default = fs.at(&["default_subvolume", "id"]).and_then(Value::as_u64).unwrap_or(5);
            let mut mounts = Vec::new();
            for e in self.layout.arr(&["storage", "fstab"]) {
                if s(e, "type").as_deref() != Some("btrfs") || !s(e, "resolves_to").is_some_and(|r| devs.contains(&r)) {
                    continue;
                }
                let Some(target) = s(e, "file").filter(|f| f.starts_with('/')) else { continue };
                let o = s(e, "options").unwrap_or_default();
                let opt = |k: &str| o.split(',').find_map(|x| x.strip_prefix(k).map(str::to_string));
                let subvol = match (opt("subvol="), opt("subvolid=").and_then(|i| i.parse().ok())) {
                    (Some(p), _) => Some(strip(&p)),
                    (None, Some(id)) => by_id(id),
                    // transactional-update's /: the booted slot, which the
                    // mount stage mounts (a pending update's default isn't).
                    (None, None) => match super::transactional::booted_root(&self.layout.v).filter(|_| target == "/") {
                        Some(b) => Some(strip(&b)),
                        None => by_id(default),
                    },
                };
                if let Some(subvol) = subvol {
                    mounts.push(Mount { target, subvol });
                }
            }
            // Timeshift's snapshots: not in the backup, so neither made by
            // format nor planned as shared snapshots.  Their IDs are burnt
            // by format's placeholders when a later subvolume needs its own.
            let ts: Vec<(String, String)> = subs.iter().filter_map(|x| timeshift_snapshot(&x.path, &mounts).map(|n| (x.path.clone(), n.to_string()))).collect();
            if !ts.is_empty() {
                let names: BTreeSet<&str> = ts.iter().map(|(_, n)| n.as_str()).collect();
                let what = if names.len() == 1 { "Timeshift snapshot (NAMES) is" } else { "Timeshift snapshots (NAMES) are" };
                let what = what.replace("NAMES", &names.iter().copied().collect::<Vec<_>>().join(", "));
                self.notes.push(format!("btrfs {uuid}: {} {what} not in the backup; not made", names.len()));
                subs.retain(|x| !ts.iter().any(|(p, _)| *p == x.path));
                self.timeshift.insert(uuid.clone(), ts.into_iter().map(|(p, _)| p).collect());
            }
            let mut plan = crate::load::shared::plan(&subs, &mounts);
            if share {
                self.notes.extend(plan.notes.iter().map(|n| format!("btrfs {uuid}: {n}")));
                if self.opts.repo.is_none() && !plan.shared.is_empty() {
                    let list: Vec<&str> = plan.shared.iter().map(|x| x.path.as_str()).collect();
                    self.notes.push(format!(
                        "btrfs {uuid}: the snapshots {} are made by the restore stage, with --repo; to restore by hand, give --btrfs-snapshots copy (format then makes them, for a restore in full)",
                        list.join(", ")
                    ));
                }
            } else {
                plan.shared.clear();
            }
            if self.opts.repo.is_none() {
                for a in &plan.aliases {
                    self.notes.push(format!("btrfs {uuid}: {a} is a subvolume mounted elsewhere, seen again there: a restore by hand leaves it out"));
                }
            }
            plan.notes.clear();
            self.btrfs.insert(uuid, plan);
        }
    }

    /// The read-only shared snapshots, as paths in the target: made
    /// read-only in the restore stage, which nothing reads after.
    pub(crate) fn ro_shared(&self) -> Vec<String> {
        let t = &self.opts.target;
        self.btrfs
            .values()
            .flat_map(|p| p.shared.iter())
            .flat_map(|x| {
                let me = x.readonly.then(|| format!("{t}{}", x.backup));
                let kids = x.children.iter().filter(|c| c.readonly && !x.readonly).map(move |c| format!("{t}{}/{}", x.backup, c.rest));
                me.into_iter().chain(kids)
            })
            .collect()
    }

    /// `v`, a subvolume of the btrfs `fs`, is one of its Timeshift
    /// snapshots (btrfs_plans), which aren't made.
    fn is_timeshift(&self, fs: &Value, v: &Value) -> bool {
        let Some(ts) = s(fs, "uuid").and_then(|u| self.timeshift.get(&u)) else { return false };
        s(v, "path").is_some_and(|p| ts.contains(p.trim_start_matches("<FS_TREE>/").trim_start_matches('/')))
    }

    /// The source's IDs of the btrfs `fs`'s Timeshift snapshots.
    fn timeshift_ids(&self, fs: &Value) -> Vec<u64> {
        fs.get("subvolumes").and_then(Value::as_array).into_iter().flatten().filter(|v| self.is_timeshift(fs, v)).filter_map(|v| v.get("id").and_then(Value::as_u64)).collect()
    }

    fn datasets(&mut self, out: &mut Vec<Cmd>) {
        let pools: Vec<String> = self.layout.v.at(&["storage", "zfs", "tools", "imported_pools"]).and_then(Value::as_array).into_iter().flatten().filter_map(|p| s(p, "name")).collect();
        let mut list: Vec<Value> = self.layout.v.at(&["storage", "zfs", "tools", "datasets"]).and_then(Value::as_array).cloned().unwrap_or_default();
        list.sort_by_key(|d| s(d, "name").unwrap_or_default());
        for ds in list {
            let name = s(&ds, "name").unwrap_or_default();
            if pools.contains(&name) || self.datasets.contains(&self.zname(&name)) {
                continue; // the root dataset came with zpool create; zvols with assemble
            }
            self.make_dataset(&ds, out);
        }
        for p in self.layout.v.at(&["storage", "zfs", "tools", "imported_pools"]).and_then(Value::as_array).into_iter().flatten() {
            if let (Some(n), Some(b)) = (s(p, "name"), p.at(&["properties", "bootfs"]).and_then(Value::as_str).filter(|b| *b != "-")) {
                out.push(Cmd::new(&["zpool", "set", &format!("bootfs={}", self.zname(b)), &self.zname(&n)]));
            }
        }
        self.clone_notes();
    }

    fn make_dataset(&mut self, ds: &Value, out: &mut Vec<Cmd>) {
        let ds = ds.clone();
        let name = s(&ds, "name").unwrap_or_default();
        self.new_key(&ds, out);
        {
            // -u (don't mount) is for filesystems; zfs refuses it for a volume.
            let mut a = vec!["zfs".to_string(), "create".into()];
            if s(&ds, "type").as_deref() == Some("volume") {
                a.extend(["-V".into(), num(&ds, "volsize").unwrap_or(0).to_string()]);
                let pve = if self.layout.str_at(&["boot", "packages", "pve-manager"]).is_some() { "; Proxmox: VM disks are vzdump's or Proxmox Backup Server's to back up" } else { "" };
                self.notes.push(format!("ZFS volume {name}: made empty; archivers back up files, not zvols{pve}"));
            } else if self.fam != Some(crate::other::Family::Illumos) {
                a.push("-u".into());
            }
            for (k, v) in self.ds_props(&ds, false) {
                if k == "volsize" {
                    continue;
                }
                a.push("-o".into());
                a.push(format!("{k}={v}"));
            }
            let here = self.zname(&name);
            a.push(here.clone());
            // (volmode=none: no device node, nothing to wait for.)
            let node = !a.iter().any(|x| x == "volmode=none");
            out.push(Cmd { argv: a, ..Cmd::default() });
            if s(&ds, "type").as_deref() == Some("volume") && node && self.fam.map_or(true, |f| f.on_linux()) {
                out.push(zvol_wait(&here));
            }
            // illumos' zfs create has no -u: what it mounted, unmounted
            // (mount mounts every dataset in order).
            if self.fam == Some(crate::other::Family::Illumos) && s(&ds, "type").as_deref() != Some("volume") {
                let qhere = shell_quote(&here);
                out.push(Cmd::new(&["sh", "-c", &format!("[ \"$(zfs get -H -o value mounted {qhere})\" != yes ] || zfs unmount {qhere}")]));
            }
            self.datasets.push(here);
        }
    }
}

impl Builder<'_> {
    /// A dataset's name here: its pool's in-core name when that differs
    /// (Mapping::pool_names).
    pub(crate) fn zname(&self, ds: &str) -> String {
        let (pool, rest) = match ds.split_once('/') {
            Some((p, r)) => (p, Some(r)),
            None => (ds, None),
        };
        match self.names.mapping.pool_names.iter().find(|(s, _)| s == pool) {
            Some((_, h)) => rest.map_or_else(|| h.clone(), |r| format!("{h}/{r}")),
            None => ds.to_string(),
        }
    }
}

/// The start of a script run in the restored system's chroot that labels
/// with its SELinux policy: it exits 0 unless SELinux is enabled there, and
/// has setfiles and the policy's file_contexts, which it leaves in $f.
const SELINUX_SETFILES_GATE: &str = "[ -f /etc/selinux/config ] || exit 0; . /etc/selinux/config; [ \"${SELINUX:-disabled}\" != disabled ] || exit 0; f=/etc/selinux/${SELINUXTYPE:-targeted}/contexts/files/file_contexts; [ -f \"$f\" ] && command -v setfiles >/dev/null || exit 0; ";

// ---- mount, restore -----------------------------------------------------

/// fstab's options for the mount stage's own mounts: without those that
/// only matter at the restored system's own boot, and without `ro`, since
/// the restore writes into every mount (ostree's fstab mounts / ro).
fn mount_options(o: &str) -> String {
    let keep: Vec<&str> = o
        .split(',')
        .filter(|x| !x.is_empty())
        .filter(|x| !matches!(*x, "defaults" | "noauto" | "auto" | "nofail" | "_netdev" | "user" | "users" | "owner" | "sw" | "ro"))
        // util-linux's ro=vfs (or ro=fs): read-only too (MicroOS's /).
        .filter(|x| !x.starts_with("ro="))
        .filter(|x| !x.starts_with("x-") && !x.starts_with("comment="))
        .collect();
    if keep.is_empty() { "defaults".to_string() } else { keep.join(",") }
}

/// The option a FAT filesystem's mount for the restore needs so that the
/// files load writes onto it (the restore's, and the boot stage's, which
/// writes through the same mount) get the stamps the source's had on disk.
/// FAT keeps local time, and Linux's vfat converts with the mount's
/// tz=UTC or time_offset= (minutes east of UTC), else with the kernel's
/// sys_tz: on the source `hwclock --systz` may have set that (Artix's
/// dinit does at every boot: Europe/Paris in winter is tz_minuteswest
/// -60, so time_offset=60), where the rescue's is 0.  `opts` is the
/// mount's own option list (it already says, if it names either);
/// `source` the source's running mount's options, whose tz=/time_offset=
/// is reproduced (fstab may not have had it); else the dump's sys_tz
/// (storage.sys_tz_minuteswest): time_offset= when it isn't 0, tz=UTC
/// when it is (so a rescue whose own sys_tz isn't 0, live media that ran
/// hwclock --systz, doesn't shift the stamps).  None: nothing to add (an
/// older dump without sys_tz: the rescue's own, 0 on our images).
pub(crate) fn fat_time_option(opts: &str, source: Option<&str>, sys_tz_minuteswest: Option<i64>) -> Option<String> {
    let named = |o: &str| o.split(',').find(|x| x.starts_with("tz=") || x.starts_with("time_offset=")).map(str::to_string);
    if named(opts).is_some() {
        return None;
    }
    if let Some(o) = source.and_then(named) {
        return Some(o);
    }
    // vfat refuses a time_offset beyond a day.
    sys_tz_minuteswest.filter(|&m| m.abs() <= 24 * 60).map(|m| if m == 0 { "tz=UTC".to_string() } else { format!("time_offset={}", -m) })
}

impl Builder<'_> {
    /// A Linux vfat/msdos mount of the mount stage (`mount -t TYPE -o OPTS
    /// DEV DIR`, at `at` in the restored system; an fstab `auto` one too
    /// when the source had a vfat/msdos filesystem mounted there) with
    /// fat_time_option's option added, and the plan saying why.
    fn fat_time(&self, at: &str, c: &mut Cmd) {
        if self.fam.is_some() || c.argv.len() != 7 || c.argv[0] != "mount" || c.argv[1] != "-t" || c.argv[3] != "-o" || !matches!(c.argv[2].as_str(), "vfat" | "msdos" | "auto") {
            return;
        }
        let source: Option<String> = self
            .layout
            .arr(&["storage", "mounts"])
            .iter()
            .find(|m| s(m, "target").as_deref() == Some(at) && s(m, "fstype").is_some_and(|t| matches!(t.as_str(), "vfat" | "msdos")))
            .map(|m| format!("{},{}", s(m, "options").unwrap_or_default(), s(m, "super_options").unwrap_or_default()));
        if c.argv[2] == "auto" && source.is_none() {
            return;
        }
        let tz = self.layout.v.at(&["storage", "sys_tz_minuteswest"]).and_then(Value::as_i64);
        let Some(add) = fat_time_option(&c.argv[4], source.as_deref(), tz) else {
            return;
        };
        let why = if source.as_deref().is_some_and(|o| o.split(',').any(|x| x == add)) {
            format!("{add}: as the source's {at} was mounted, so FAT's local-time stamps are written as its were")
        } else if add == "tz=UTC" {
            format!("{add}: the source's vfat kept {at}'s FAT local-time stamps as UTC (sys_tz tz_minuteswest 0), whatever the rescue's kernel time zone; the restore and the boot stage write through this mount")
        } else {
            format!("{add}: the source's vfat kept {at}'s FAT local-time stamps by its kernel time zone (sys_tz tz_minuteswest {}), not the rescue's; the restore and the boot stage write through this mount", tz.unwrap_or(0))
        };
        c.argv[4] = if c.argv[4] == "defaults" { add } else { format!("{},{add}", c.argv[4]) };
        c.why = Some(match c.why.take() {
            Some(w) => format!("{w}; {why}"),
            None => why,
        });
    }

    fn mount(&mut self) -> Vec<Cmd> {
        let target = self.opts.target.clone();
        // (mount point in the restored system, command)
        let mut ms: Vec<(String, Cmd)> = Vec::new();
        let mut out_mkdir: Vec<String> = Vec::new();
        // Ubuntu Core's fstab is the base snap's binds and tmpfs, none of
        // them the backup's tree (load/snapcore.rs mounts that).
        let fstab = if super::snapcore::of(&self.layout.v).is_some() { &[][..] } else { self.layout.arr(&["storage", "fstab"]) };
        for e in fstab {
            let file = s(e, "file").unwrap_or_default();
            let ty = s(e, "type").unwrap_or_default();
            if ty == "swap" || !file.starts_with('/') {
                continue;
            }
            if !safe_target_subpath(&file) {
                self.problems.push(format!("fstab's {file:?} isn't a safe mount path under the target"));
                continue;
            }
            let Some(src) = s(e, "resolves_to") else {
                // A legacy ZFS dataset fstab mounts (TrueNAS's boot-pool/grub,
                // perhaps): load/zfs_be.rs's legacy mounts mount it, as the
                // source had it mounted there.
                let legacy_source = ty == "zfs" && self.layout.arr(&["storage", "backup_sources"]).iter().any(|b| s(b, "path").as_deref() == Some(file.as_str()) && s(b, "source") == s(e, "spec"));
                if !legacy_source {
                    self.notes.push(format!("fstab's {file} ({ty}) isn't on the layout's disks; not mounted"));
                }
                continue;
            };
            let src = crate::other::geli::fstab_src(self.layout, e, src);
            let Some(dev) = self.names.dev(&src) else {
                self.notes.push(format!("fstab's {file} is on {src}, which isn't restored"));
                continue;
            };
            let mut opts = mount_options(&s(e, "options").unwrap_or_default());
            // A transactional-update root names no subvolume: the default,
            // which during a pending update isn't the slot the backup's /
            // holds (load/transactional.rs).
            if let Some(b) = (file == "/" && ty == "btrfs").then(|| super::transactional::booted_root(&self.layout.v)).flatten() {
                if !opts.split(',').any(|o| o.starts_with("subvol=") || o.starts_with("subvolid=")) {
                    opts = if opts == "defaults" { format!("subvol={b}") } else { format!("{opts},subvol={b}") };
                }
            }
            // A HAMMER2 PFS (DragonFly's vbd0s1d@ROOT): the same PFS here.
            let dev = match s(e, "spec").and_then(|sp| sp.split_once('@').map(|(_, l)| l.to_string())) {
                Some(l) if ty == "hammer2" => format!("{dev}@{l}"),
                _ => dev,
            };
            let dir = format!("{target}{}", if file == "/" { "" } else { &file });
            // RedoxFS mounts through its FUSE driver (it goes to the
            // background once the tree is up).
            if ty == "redoxfs" {
                // Its daemon would hold load's output pipe open: its own log.
                let (qdev, qdir) = (shell_quote(&dev), shell_quote(&dir));
                let script = format!("redoxfs {qdev} {qdir} >{RUN_DIR}/redoxfs.log 2>&1 </dev/null; i=0; until mountpoint -q {qdir}; do i=$((i + 1)); [ $i -lt 60 ] || {{ cat {RUN_DIR}/redoxfs.log; exit 1; }}; sleep 1; done");
                ms.push((file.clone(), Cmd::new(&["sh", "-c", &script])));
                out_mkdir.push(dir.clone());
                continue;
            }
            // Haiku's mount takes no option list worth giving.
            if self.fam == Some(crate::other::Family::Haiku) {
                ms.push((file.clone(), Cmd::new(&["mount", "-t", &ty, &dev, &dir])));
                continue;
            }
            ms.push((file.clone(), Cmd::new(&["mount", "-t", &ty, "-o", &opts, &dev, &dir])));
        }
        let bootfs: Vec<String> =
            self.layout.v.at(&["storage", "zfs", "tools", "imported_pools"]).and_then(Value::as_array).into_iter().flatten().filter_map(|p| p.at(&["properties", "bootfs"]).and_then(Value::as_str).map(str::to_string)).collect();
        for ds in self.layout.v.at(&["storage", "zfs", "tools", "datasets"]).and_then(Value::as_array).cloned().unwrap_or_default() {
            let name = s(&ds, "name").unwrap_or_default();
            let mp = s(&ds, "mountpoint").unwrap_or_default();
            let canmount = s(&ds, "canmount").unwrap_or("on".into());
            // A root dataset is canmount=noauto, mounted by the initramfs
            // (root=ZFS=...); it was mounted when the dump was made.
            let was_mounted = s(&ds, "mounted").as_deref() == Some("yes");
            // An illumos boot environment (POOL/ROOT/NAME, beadm's) that
            // isn't the pool's bootfs and wasn't mounted: not in the backup,
            // so made but not filled (docs/design/openindiana.md, decision 3).
            if self.fam == Some(crate::other::Family::Illumos) && !bootfs.contains(&name) && !was_mounted && s(&ds, "type").as_deref() != Some("volume") {
                if let [_, "ROOT", be] = name.split('/').collect::<Vec<_>>()[..] {
                    self.notes.push(format!("boot environment {be} ({name}) isn't the booted one: it comes back as an empty plain dataset (only the booted BE is restored)"));
                }
            }
            // An inactive boot environment the dump has backup sources for
            // (crate::zfs_be, Linux only): mounted below, at its sources' paths.
            if self.in_restored_be(&name) {
                continue;
            }
            if !mp.starts_with('/') || canmount == "off" || (canmount == "noauto" && !bootfs.contains(&name) && !was_mounted) {
                continue;
            }
            // illumos won't mount over a directory that isn't empty (the
            // mount points the datasets' creation left): -O, overlaid.
            if self.fam == Some(crate::other::Family::Illumos) {
                ms.push((mp, Cmd::new(&["zfs", "mount", "-O", &self.zname(&name)])));
            } else {
                ms.push((mp, Cmd::new(&["zfs", "mount", &self.zname(&name)])));
            }
        }
        // A pool's boot dataset with mountpoint=legacy (OmniOS's cloud image:
        // the boot loader and the kernel mount it as /, fstab doesn't): mounted
        // as / with the system's mount, since zfs mount refuses legacy ones.
        if !ms.iter().any(|(p, _)| p == "/") {
            let ds = self.layout.v.at(&["storage", "zfs", "tools", "datasets"]).and_then(Value::as_array).cloned().unwrap_or_default();
            if let Some(name) = ds.iter().filter_map(|d| s(d, "name").filter(|n| bootfs.contains(n) && s(d, "mountpoint").as_deref() == Some("legacy"))).next() {
                let fs_flag = if self.fam == Some(crate::other::Family::Illumos) { "-F" } else { "-t" };
                ms.push(("/".into(), Cmd::new(&["mount", fs_flag, "zfs", &self.zname(&name), &target])));
            }
        }
        // Legacy datasets mounted at a backup source's path on the source
        // (TrueNAS's /boot/grub), and the inactive boot environments at
        // their sources' paths (load/zfs_be.rs).
        let have: Vec<String> = ms.iter().map(|(p, _)| p.clone()).collect();
        let extra = self.legacy_mounts(&have);
        ms.extend(extra);
        let extra = self.be_mount_cmds();
        ms.extend(extra);
        // ABRoot (load/abroot.rs): the init LV, the other root and /var,
        // which no fstab line mounts.
        if let Some(a) = super::abroot::of(&self.layout.v) {
            let have: Vec<String> = ms.iter().map(|(p, _)| p.clone()).collect();
            let extra = self.abroot_mounts(&a.clone(), &have);
            ms.extend(extra);
        }
        // Ubuntu Core (load/snapcore.rs): the four partitions at
        // snap-bootstrap's mount points; it has no fstab and no `/`.
        let snapcore = super::snapcore::of(&self.layout.v).cloned();
        if let Some(sc) = &snapcore {
            let extra = self.snapcore_mounts(sc);
            ms.extend(extra);
        }
        // Solus: the ESP at /boot, which fstab leaves out
        // (docs/design/solus.md): the restore puts its files back on it.
        if self.solus_cbm() && !ms.iter().any(|(p, _)| p == "/boot") {
            let extra = self.solus_esp_mount();
            ms.extend(extra);
        }
        ms.sort_by(|a, b| {
            let depth = |p: &str| if p == "/" { 0 } else { p.matches('/').count() };
            depth(&a.0).cmp(&depth(&b.0)).then(a.0.cmp(&b.0))
        });
        if !ms.iter().any(|(p, _)| p == "/") && snapcore.is_none() {
            self.problems.push("nothing in fstab or the ZFS datasets mounts /".into());
        }
        let mut out = Vec::new();
        for (p, mut c) in ms {
            let dir = if p == "/" { target.clone() } else { format!("{target}{p}") };
            self.fat_time(&p, &mut c);
            self.mount_points.push(dir.clone());
            if c.argv[0] == "mount" || out_mkdir.contains(&dir) {
                out.push(Cmd::new(&["mkdir", "-p", &dir]));
            }
            out.push(c);
        }
        out
    }

    fn restore(&mut self) -> Vec<Cmd> {
        let Some(repo) = &self.opts.repo else {
            let mut v = vec![Cmd::note(format!(
                "no --repo: restore the backup into {} yourself, then run `bko-holt load ... --only boot`",
                self.opts.target
            ))];
            v.extend(self.thin_by_hand_note());
            return v;
        };
        let mut a = vec![self.opts.archiver.clone(), "--repository".into(), repo.clone()];
        if let Some(p) = &self.opts.password_file {
            a.extend(["--password-file".into(), p.clone()]);
        }
        a.push("--no-progress".into());
        let prefix = a.clone();
        a.extend(["restore".into(), "--numeric-id".into()]);
        // Aliases and shared snapshots left out of the main restore, the
        // pools --pools leaves untouched, and with --reformat, a reformatted
        // btrfs's aliases and snapshots.
        let mut globs: Vec<String> = self.btrfs.values().flat_map(|p| p.globs()).collect();
        let pool_globs = self.left_out_pool_globs();
        let reformat_globs: Vec<String> = self.layout.arr(&["load_reformat", "restore_globs"]).iter().filter_map(Value::as_str).map(str::to_string).collect();
        let raw_globs = self.raw_globs();
        let only_raw = globs.is_empty() && pool_globs.is_empty() && reformat_globs.is_empty() && !raw_globs.is_empty();
        let only_pools = globs.is_empty() && !pool_globs.is_empty() && reformat_globs.is_empty();
        let only_reformat = globs.is_empty() && pool_globs.is_empty();
        globs.extend(pool_globs);
        globs.extend(reformat_globs);
        globs.extend(raw_globs);
        let aliases = self.btrfs.values().any(|p| !p.aliases.is_empty());
        let mut pre = Vec::new();
        // What the archiver must have: --exact for shared snapshots,
        // --target-metadata where an alias is left out.  The message names
        // 0.11.4+backotter.11: before it, --clone-from into a snapshot inside
        // the subvolume cloned from (CachyOS's and Fedora's @/.snapshots/N)
        // could empty a file there of the snapshot's size.
        let mut need = Vec::new();
        if self.btrfs.values().any(|p| !p.shared.is_empty()) {
            need.push("--exact");
        }
        if aliases {
            need.push("--target-metadata");
        }
        if !need.is_empty() {
            let arch = shell_quote(&self.opts.archiver);
            let checks: Vec<String> = need.iter().map(|f| format!("grep -q -- '{f}' {RUN_DIR}/restore.help")).collect();
            pre.push(Cmd::new(&["mkdir", "-p", "-m", "700", RUN_DIR]));
            pre.push(
                Cmd::new(&[
                    "sh",
                    "-c",
                    &format!(
                        "{arch} restore --help > {RUN_DIR}/restore.help 2>&1; {} || {{ echo \"{arch} has no restore {}, which this layout's btrfs needs: use rustic-bko 0.11.4+backotter.11 or newer\" >&2; exit 1; }}",
                        checks.join(" && "),
                        need.join(" and ")
                    ),
                ])
                .why(format!("the archiver's restore {}, for the btrfs snapshots and aliases", need.join(" and "))),
            );
        }
        if aliases {
            a.push("--target-metadata".into());
        }
        if !globs.is_empty() {
            let file = format!("{RUN_DIR}/restore.globs");
            if need.is_empty() {
                pre.push(Cmd::new(&["mkdir", "-p", "-m", "700", RUN_DIR]));
            }
            let why = if only_raw {
                "left out of the restore: the raw volumes' images, written to their LVs after it"
            } else if only_pools {
                "left out of the restore: the files of the pools --pools leaves untouched"
            } else if only_reformat {
                "left out of the restore (--reformat): btrfs subvolumes mounted elsewhere, seen again (aliases), and btrfs snapshots, which the new type can't hold as snapshots"
            } else {
                "left out of the restore: btrfs subvolumes mounted elsewhere, seen again (aliases), and the contents of the shared snapshots, made after it"
            };
            pre.push(Cmd::write_file(&file, globs.iter().map(|g| format!("{g}\n")).collect()).why(why));
            a.extend(["--glob-file".into(), file]);
        }
        a.extend([self.opts.snapshot.clone(), self.opts.target.clone()]);
        // rustic-bko's --strict-exit, as its environment variable: the
        // restore exits 1 when it couldn't set metadata or make a special
        // file, as restic-bko does, where by default it only warns, and this
        // stage would pass.  As a variable, not the flag, so that a stick
        // whose rustic-bko predates the option (0.11.4+backotter.8) still
        // runs the command: clap reads only the variables it knows.
        let env = vec![("RUSTIC_STRICT_EXIT".to_string(), "true".to_string())];
        let why = Some(format!("snapshot {} of {repo}, with BackOtter's restore flags", self.opts.snapshot));
        if self.fam.is_some() {
            // The boot stage there reads nothing of the restored tree.  The
            // glob file (--pools) is written first there too.
            let mut out = pre;
            out.extend(self.msdosfs_roots_spared(Cmd { argv: a, why, env, ..Cmd::default() }));
            return out;
        }
        let mut out = pre;
        out.push(Cmd { argv: a, why, env, ..Cmd::default() });
        self.shared_snapshots(&prefix, &mut out);
        // Thin snapshots and raw volumes (load/rawvol.rs), once their
        // origins' content is restored.
        self.thin_restore(&prefix, &mut out);
        out.push(Cmd::new(&["sh", "-c", &self.atimes(&self.opts.target, "before")]).why("the atimes the restore set, for boot to put back after its reads"));
        out
    }

    /// The restore on a system whose msdosfs (FreeBSD's FAT: the ESP at
    /// /boot/efi) is mounted in the tree: msdosfs refuses every change to
    /// its root directory's owner, mode and times (EINVAL, even to what it
    /// has), which has no directory entry to keep them in, so the archiver's
    /// strict exit fails on those three alone.  Such a root shows what its
    /// mount options give it, the source's as well.  The restore passes when
    /// its only errors are those, and fails as before otherwise.
    fn msdosfs_roots_spared(&self, restore: Cmd) -> Vec<Cmd> {
        let roots: Vec<String> = self
            .layout
            .arr(&["storage", "fstab"])
            .iter()
            .filter(|e| s(e, "type").as_deref() == Some("msdosfs") && s(e, "resolves_to").is_some())
            .filter_map(|e| s(e, "file"))
            .filter(|f| f.starts_with('/') && f != "/")
            .map(|f| f.trim_matches('/').to_string())
            .collect();
        if roots.is_empty() {
            return vec![restore];
        }
        let allowed = format!("{RUN_DIR}/restore.fatroots");
        let lines: String = roots
            .iter()
            .flat_map(|r| ["setting UID/GID failed", "setting User/Group failed", "chmod failed", "setting file times failed"].map(|w| format!("[WARN] restore {r}: {w}.\n")))
            .collect();
        let err = format!("{RUN_DIR}/restore.err");
        let msg = shell_quote(&format!(
            "bko-holt: the restore set no owner, mode or times on the root of {}: msdosfs keeps none there",
            roots.iter().map(|r| format!("/{r}")).collect::<Vec<_>>().join(", ")
        ));
        let script = format!(
            "\"$@\" 2>{err}; s=$?; cat {err} >&2; [ $s -eq 1 ] || exit $s; e=$(printf '\\033'); sed -e \"s/$e\\[[0-9;]*m//g\" -e 's/[[:space:]]*$//' {err} > {err}.plain; \
             n=$(sed -n 's/^error: There were \\([0-9][0-9]*\\) errors.*/\\1/p' {err}.plain); k=$(grep -cxF -f {allowed} {err}.plain); \
             [ -n \"$n\" ] && [ \"$n\" = \"$k\" ] || exit 1; echo {msg} >&2"
        );
        let mut argv = vec!["sh".to_string(), "-c".into(), script, "sh".into()];
        argv.extend(restore.argv);
        vec![
            Cmd::new(&["mkdir", "-p", "-m", "700", RUN_DIR]),
            Cmd::write_file(&allowed, lines).why("the restore errors msdosfs's mount roots give, and only those, let pass"),
            Cmd { argv, ..restore },
        ]
    }

    /// Each btrfs's shared snapshots, once the main restore is done: in the
    /// source's ID order, each a snapshot of its base, then made exact from
    /// the backup (rustic-bko restore --exact); then the read-only flags,
    /// the default subvolume if it's one, and quotas.
    // Bug: unlike format(), shared_snapshots(), boot_tail(), and
    // transactional_boot() read only storage.btrfs[].devices[0] with
    // unwrap_or_default(). When that array is empty/missing (dumps without btrfs
    // tools) or its disk is unmapped, they emitted `mount -t btrfs ... '' ...`
    // with an empty device argument instead of falling back to btrfs leaves or
    // reporting a layout problem.
    pub(crate) fn btrfs_dev(&mut self, uuid: &str, fs: Option<&Value>) -> Option<String> {
        if let Some(dev) = fs
            .and_then(|f| f.get("devices"))
            .and_then(Value::as_array)
            .into_iter()
            .flatten()
            .filter_map(|d| d.as_str().and_then(|n| self.names.dev(n)))
            .next()
        {
            return Some(dev);
        }
        if let Some(dev) = self
            .leaves()
            .into_iter()
            .find(|(_, c)| s(c, "type").as_deref() == Some("btrfs") && s(c, "uuid").is_some_and(|u| u.eq_ignore_ascii_case(uuid)))
            .and_then(|(src, _)| self.names.dev(&src))
        {
            return Some(dev);
        }
        self.problem_once(format!("btrfs {uuid}: no target device in this layout"));
        None
    }

    fn shared_snapshots(&mut self, prefix: &[String], out: &mut Vec<Cmd>) {
        for fs in self.layout.arr(&["storage", "btrfs"]).to_vec() {
            let uuid = s(&fs, "uuid").unwrap_or_default();
            let Some(plan) = self.btrfs.get(&uuid).cloned().filter(|p| !p.shared.is_empty()) else { continue };
            let Some(dev) = self.btrfs_dev(&uuid, Some(&fs)) else { continue };
            let top = format!("{RUN_DIR}/btrfs/{uuid}");
            out.push(Cmd::new(&["mkdir", "-p", &top]));
            out.push(Cmd::new(&["mount", "-t", "btrfs", "-o", "subvolid=5,noatime", &dev, &top]).why(format!("btrfs {uuid}: its shared snapshots (noatime: reading them changes nothing)")));
            let mut placeholders = Vec::new();
            for x in &plan.shared {
                for n in &x.placeholders {
                    let ph = format!("{top}/.bko-holt-placeholder-{n}");
                    out.push(Cmd::new(&["btrfs", "subvolume", "create", &ph]).why(format!("burns ID {n}, unused on the source")));
                    placeholders.push(ph);
                }
                let d = format!("{top}/{}", x.path);
                let parent = d.rsplit_once('/').map_or(top.clone(), |(p, _)| p.to_string());
                let mut r: Vec<String> = prefix.to_vec();
                r.extend(["restore".into(), "--numeric-id".into(), "--exact".into()]);
                if let Some((b, sub)) = &x.clone_from {
                    r.extend(["--clone-from".into(), format!("{}={top}/{sub}", snapshot_path(&self.opts.snapshot, b).1)]);
                }
                let (id, path) = snapshot_path(&self.opts.snapshot, &x.backup);
                r.extend([format!("{id}:{path}"), d.clone()]);
                let r = r.iter().map(|w| shell_quote(w)).collect::<Vec<_>>().join(" ");
                let (qd, qb, qp, qpath) = (shell_quote(&d), shell_quote(&format!("{top}/{}", x.base)), shell_quote(&parent), dq_escape(&x.path));
                let id = x.new_id;
                // Its children (each slot's /etc on transactional-update):
                // made in it once it's made, where its snapshot has an empty
                // directory (or the base's plain one), before its restore,
                // which fills them too; their placeholders just before each.
                let (mut kids_snap, mut kids_empty, mut kids_ids) = (String::new(), String::new(), String::new());
                for c in &x.children {
                    let mut ph = String::new();
                    for n in &c.placeholders {
                        let p = format!("{top}/.bko-holt-placeholder-{n}");
                        ph += &format!("btrfs subvolume create {} && ", shell_quote(&p));
                        placeholders.push(p);
                    }
                    let qc = format!("\"$d\"/{}", shell_quote(&c.rest));
                    let make = match &c.base {
                        Some(b) => format!("btrfs subvolume snapshot {} {qc}", shell_quote(&format!("{top}/{b}"))),
                        None => format!("btrfs subvolume create {qc}"),
                    };
                    kids_snap += &format!("{ph}rm -rf -- {qc} && mkdir -p \"$(dirname {qc})\" && {make} && ");
                    kids_empty += &format!("{ph}mkdir -p \"$(dirname {qc})\" && btrfs subvolume create {qc} && ");
                    let (cp, cid) = (dq_escape(&c.path), c.new_id);
                    kids_ids += &format!("; cid=$(btrfs inspect-internal rootid {qc}) && [ \"$cid\" = {cid} ] || echo \"{cp}: ID $cid here, {cid} in the plan\"");
                }
                // The directory it's in keeps its times (snapper's N/, with
                // info.xml), which rmdir and the snapshot would change.
                let script = format!(
                    "d={qd}; p={qp}; mkdir -p \"$p\" && touch -r \"$p\" {RUN_DIR}/times.ref || exit; \
                     if [ -d \"$d\" ] && [ \"$(stat -c %i \"$d\")\" != 256 ] && [ -z \"$(ls -A \"$d\")\" ]; then rmdir \"$d\" && btrfs subvolume snapshot {qb} \"$d\" && {kids_snap}RUSTIC_STRICT_EXIT=true {r}; \
                     else btrfs subvolume create \"$d\" && {kids_empty}echo \"{qpath}: not in the backup; made empty\"; fi || exit; \
                     touch -r {RUN_DIR}/times.ref \"$p\" || exit; \
                     id=$(btrfs inspect-internal rootid \"$d\") && [ \"$id\" = {id} ] || echo \"{qpath}: ID $id here, {id} in the plan\"{kids_ids}"
                );
                let mut why = match x.id {
                    Some(src) if src == id => format!("{}: a snapshot of {}, then made exact from the backup's {} (ID {id}, as on the source)", x.path, x.base, x.backup),
                    Some(src) => format!("{}: a snapshot of {}, then made exact from the backup's {} (ID {id}; {src} on the source)", x.path, x.base, x.backup),
                    None => format!("{}: a snapshot of {}, then made exact from the backup's {}", x.path, x.base, x.backup),
                };
                for c in &x.children {
                    let from = c.base.as_ref().map_or_else(|| "made empty".to_string(), |b| format!("a snapshot of {b}"));
                    let ids = match c.id {
                        Some(src) if src == c.new_id => format!("ID {}, as on the source", c.new_id),
                        Some(src) => format!("ID {}; {src} on the source", c.new_id),
                        None => format!("ID {}", c.new_id),
                    };
                    why += &format!("; {} nested in it before the restore ({from}, {ids})", c.rest);
                }
                out.push(Cmd::new(&["sh", "-c", &script]).why(why));
            }
            if !placeholders.is_empty() {
                let mut a = vec!["btrfs".to_string(), "subvolume".into(), "delete".into(), "--commit-after".into()];
                a.extend(placeholders);
                out.push(Cmd { argv: a, ..Cmd::default() });
                out.push(Cmd::new(&["btrfs", "subvolume", "sync", &top]).why("the placeholders gone before quotas are on (no qgroups for them)"));
            }
            // Read-only last: a child (a slot's /etc) is made and filled in
            // its slot first, and a child that is read-only itself comes
            // before its slot.
            for x in &plan.shared {
                for c in x.children.iter().rev().filter(|c| c.readonly) {
                    out.push(Cmd::new(&["btrfs", "property", "set", "-ts", &format!("{top}/{}", c.path), "ro", "true"]));
                }
                if x.readonly {
                    out.push(Cmd::new(&["btrfs", "property", "set", "-ts", &format!("{top}/{}", x.path), "ro", "true"]));
                }
            }
            let default_id = fs.at(&["default_subvolume", "id"]).and_then(Value::as_u64).unwrap_or(5);
            if let Some(x) = plan.shared.iter().find(|x| x.id == Some(default_id)) {
                out.push(Cmd::new(&["btrfs", "subvolume", "set-default", &format!("{top}/{}", x.path)]));
            }
            if fs.get("quota_enabled").and_then(Value::as_bool) == Some(true) {
                self.btrfs_quota(&fs, &top, &plan, out);
            }
            out.push(Cmd::new(&["umount", &top]));
        }
    }
}

// ---- boot, close --------------------------------------------------------

/// The snapshot `--snapshot` names, and `path` (a path of the restored
/// tree) as a path in it: below its own `ID:PATH`'s PATH, if it has one
/// (a backup of a source mounted elsewhere, /mnt/src).
fn snapshot_path(snapshot: &str, path: &str) -> (String, String) {
    match snapshot.split_once(':') {
        Some((id, sub)) if !sub.trim_end_matches('/').is_empty() => {
            let sub = sub.trim_end_matches('/');
            (id.to_string(), if path == "/" { sub.to_string() } else { format!("{sub}{path}") })
        }
        Some((id, _)) => (id.to_string(), path.to_string()),
        None => (snapshot.to_string(), path.to_string()),
    }
}

/// Every path's atime under the target (not the bind-mounted /dev, /proc,
/// /sys, /run): "before" lists them to RUN_DIR/atimes.before (the restore
/// stage, right after the archiver: nothing has read the tree yet);
/// "if-missing" does so unless it's done (boot, after a restore made some
/// other way); "restore" lists them again and puts back each that changed
/// (testing a symlink with -L first: -e would follow it, and reading a
/// symlink bumps its atime, perhaps after it was put back).
pub(crate) fn atimes_cmd(t: &str, what: &str, prune: &[String]) -> String {
    atimes_cmd_in(t, what, prune, false)
}

/// atimes_cmd; with RUN_MNT, T/run/mnt is listed, not pruned with the rest
/// of T/run: Ubuntu Core's whole tree is there (load/snapcore.rs).
pub(crate) fn atimes_cmd_in(t: &str, what: &str, prune: &[String], run_mnt: bool) -> String {
    let more: String = prune.iter().map(|p| format!(" -o -path {}", shell_quote(p))).collect();
    let qt = shell_quote(t);
    let run = if run_mnt {
        format!(
            "\\( -path {} ! -path {} ! -path {} \\)",
            shell_quote(&format!("{t}/run/*")),
            shell_quote(&format!("{t}/run/mnt")),
            shell_quote(&format!("{t}/run/mnt/*"))
        )
    } else {
        format!("-path {}", shell_quote(&format!("{t}/run")))
    };
    let list = format!(
        "find {qt} \\( -path {} -o -path {} -o -path {} -o {run}{more} \\) -prune -o -printf '%A@\\t%p\\0' | LC_ALL=C sort -z",
        shell_quote(&format!("{t}/dev")),
        shell_quote(&format!("{t}/proc")),
        shell_quote(&format!("{t}/sys"))
    );
    let b = format!("{RUN_DIR}/atimes.before");
    if what == "before" {
        format!("mkdir -p -m 700 {RUN_DIR} && {list} > {b}")
    } else if what == "if-missing" {
        format!("[ -s {b} ] || {{ mkdir -p -m 700 {RUN_DIR} && {list} > {b}; }}")
    } else {
        format!(
            "{list} > {RUN_DIR}/atimes.after && LC_ALL=C comm -z -23 {b} {RUN_DIR}/atimes.after | \\
             while IFS=$'\\t' read -r -d '' a p; do [ -L \"$p\" ] || [ -e \"$p\" ] || continue; touch -a -h -d \"@$a\" -- \"$p\" 2>/dev/null || \
             {{ [ ! -L \"$p\" ] && lsattr -d -- \"$p\" 2>/dev/null | cut -d' ' -f1 | grep -q i && {{ chattr -i -- \"$p\"; touch -a -h -d \"@$a\" -- \"$p\"; chattr +i -- \"$p\"; }}; }}; done"
        )
    }
}

impl Builder<'_> {
    /// The boot stage: the inactive boot environments the restore filled
    /// unmounted first (load/zfs_be.rs), and the held-back readonly=on set
    /// last, whatever the system.
    fn boot(&mut self) -> Vec<Cmd> {
        let mut out = self.boot_stage();
        let at = usize::from(out.first().is_some_and(Cmd::is_note));
        let unmounts = self.be_unmount_cmds();
        out.splice(at..at, unmounts);
        out.extend(self.readonly_sets());
        out
    }

    fn boot_stage(&mut self) -> Vec<Cmd> {
        if let Some(fam) = self.fam {
            return self.other_boot(fam);
        }
        if let Some(o) = super::ostree::of(&self.layout.v) {
            return self.ostree_boot(&o.clone());
        }
        if let Some(a) = super::abroot::of(&self.layout.v) {
            return self.abroot_boot(&a.clone());
        }
        if let Some(sc) = super::snapcore::of(&self.layout.v) {
            return self.snapcore_boot(&sc.clone());
        }
        if let Some(x) = super::transactional::of(&self.layout.v) {
            return self.transactional_boot(&x.clone());
        }
        let t = self.opts.target.clone();
        let mut out = Vec::new();
        out.push(Cmd::note(format!("refuses to start unless {t}/etc/fstab is the restored system's (its / matches the layout's)")));
        out.extend(self.restored_key_files(&t));
        // A key file inside the restored tree must be the key the pool was
        // made with (load/zfsbootmenu.rs).
        out.extend(self.zfs_key_file_checks(&t));
        // Bug: when the rescue host already imports a same-named ZFS pool,
        // assemble creates the restored pool under self.zname() (e.g. rpool_bko),
        // yet zvol nodes, zvol parent checks, keylocation, and cachefile still
        // used the untranslated source name. That formatted host zvols, mutated
        // the host pool's keylocation and cachefile, and left the restored pool
        // unable to unlock or import at boot. Translate via self.zname().
        for ds in self.layout.v.at(&["storage", "zfs", "tools", "datasets"]).and_then(Value::as_array).cloned().unwrap_or_default() {
            let name = s(&ds, "name").unwrap_or_default();
            let pool = name.split('/').next().unwrap_or("");
            if s(&ds, "encryptionroot").as_deref() != Some(name.as_str()) || !self.pools.contains(&self.zname(pool)) {
                continue;
            }
            let loc = s(&ds, "keylocation").unwrap_or("prompt".into());
            out.push(Cmd::new(&["zfs", "set", &format!("keylocation={loc}"), &self.zname(&name)]).why(format!("{name}: unlocks as the source did")));
        }
        for p in self.layout.v.at(&["storage", "zfs", "tools", "imported_pools"]).and_then(Value::as_array).into_iter().flatten() {
            let here = self.zname(&s(p, "name").unwrap_or_default());
            if self.pools.contains(&here) && !self.layout.v.at(&["storage", "zfs", "zpool_cache"]).is_none_or(Value::is_null) {
                out.push(Cmd::new(&["zpool", "set", &format!("cachefile={t}/etc/zfs/zpool.cache"), &here]).why("the restored system's zpool.cache names the new pool"));
            }
        }
        // The target is mounted with the source's own options (dracut
        // records the root's in the initramfs it makes), so what this stage
        // reads gets its atime bumped: each file's atime, as the restore
        // set it, is kept here and put back at the end.
        out.extend(self.root_atime_remount(&t));
        out.push(Cmd::new(&["sh", "-c", &atimes_cmd(&t, "if-missing", &self.ro_shared())]).why("the restored atimes (the restore stage lists them), to put back after this stage's reads"));
        out.extend(self.rewrite_cmd(false));
        // LVM's devices file (RHEL, Fedora): LVM run in the chroot (by dracut)
        // would record the rescue system's names for the PVs in it; it's
        // put back as restored when the chroot's work is done.
        let devdir = format!("{t}/etc/lvm/devices");
        let (qdevdir, qlvm) = (shell_quote(&devdir), shell_quote(&format!("{t}/etc/lvm")));
        out.push(Cmd::new(&["sh", "-c", &format!("rm -rf {RUN_DIR}/lvm-devices; [ ! -d {qdevdir} ] || {{ cp -a {qdevdir} {RUN_DIR}/lvm-devices && touch -r {qlvm} {RUN_DIR}/lvm-devices.parent; }}")]).why("the restored LVM devices file, kept"));
        let lvm_devices_back =
            Cmd::new(&["sh", "-c", &format!("[ ! -d {RUN_DIR}/lvm-devices ] || {{ rm -rf {qdevdir} && cp -a {RUN_DIR}/lvm-devices {qdevdir} && touch -r {RUN_DIR}/lvm-devices.parent {qlvm}; }}")]).why("the restored LVM devices file, put back");
        for d in ["dev", "proc", "sys", "run"] {
            out.push(Cmd::new(&["mount", "--rbind", "--make-rslave", &format!("/{d}"), &format!("{t}/{d}")]));
        }
        // NixOS and Guix System (crate::store): their notes and paths.
        let store = crate::store::system(self.layout.str_at(&["os", "id"]));
        let gentoo = self.layout.str_at(&["os", "id"]) == Some("gentoo");
        let slackware = self.layout.str_at(&["os", "id"]) == Some("slackware");
        let solus = self.solus_cbm();
        let truenas = self.truenas().is_some();
        if truenas {
            // Each boot environment's initrd came back with it (the truenas
            // arm's note says so).
        } else if gentoo {
            let cmds = self.gentoo_initramfs(&t);
            out.extend(cmds);
        } else if solus {
            // Solus: each kernel package ships its initrd, and
            // clr-boot-manager's copies came back with the ESP; one rebuilt
            // here would be host-only, and CBM would never copy it.
            self.notes.push("Solus: each kernel's initrd came prebuilt in its package (/usr/lib64/kernel/initrd-com.solus-project.*), and clr-boot-manager's copies of them came back with the ESP; nothing to rebuild".into());
        } else if slackware {
            self.slackware_device_names();
            let cmds = self.slackware_initramfs(&t);
            out.extend(cmds);
        } else {
            match self.initramfs(&t) {
                _ if store.is_some() && self.layout.uefi() => self.notes.push(store.map_or("", |s| s.initramfs_uefi).into()),
                _ if store.is_some() => self.notes.push(store.map_or("", |s| s.initramfs_bios).into()),
                Some(c) => out.push(c),
                None => self.notes.push("the initramfs came prebuilt with the kernel and was restored with it; nothing to rebuild".into()),
            }
        }
        let loader = self.layout.str_at(&["boot", "loader", "name"]).unwrap_or("unknown").to_string();
        let uefi = self.layout.uefi();
        let via_shim = self.layout.v.at(&["boot", "loader", "via_shim"]).and_then(Value::as_bool) == Some(true);
        match (loader.as_str(), uefi) {
            // Proxmox VE on ZFS root: proxmox-boot-tool owns the ESPs, which
            // nothing mounts and the archive doesn't hold; it rebuilds them
            // (docs/design/proxmox.md, decision 3), whatever the loader.
            // TrueNAS (load/truenas.rs): GRUB on every boot-pool disk, both
            // firmwares, each ESP, and the menu of every boot environment.
            _ if truenas => {
                let cmds = self.truenas_boot_cmds(&t);
                out.extend(cmds);
            }
            _ if self.proxmox_boot().is_some() => {
                let cmds = self.proxmox_boot_cmds(&t, via_shim);
                out.extend(cmds);
            }
            // ZFSBootMenu (load/zfsbootmenu.rs): no loader installed, its
            // images came back with the ESP; the initramfs step above and
            // efi_entries below are all it needs.
            ("zfsbootmenu", true) => {
                let cmds = self.zfsbootmenu_boot_cmds();
                out.extend(cmds);
            }
            // Gentoo through shim: grub-install would put an unsigned GRUB
            // where the signed one is.
            // Solus (docs/design/solus.md, decision 4): clr-boot-manager's
            // shim (bootloaderx64.efi), MokManager, systemd-boot (as
            // grubx64.efi), every kernel and initrd it copied, and the
            // entries came back with the ESP, at /boot; they hold no machine
            // identity beyond the UUIDs format keeps.  No bootctl install
            // (an unsigned systemd-boot over the shim), no clr-boot-manager:
            // only the boot entry is made (efi_entries, solus_entry).
            ("systemd-boot", true) if solus => {
                self.notes.push("Solus: clr-boot-manager's shim, MokManager, systemd-boot, kernels, initrds and loader entries are the restored ESP's; only the boot entry is made".into());
                self.notes.push("Solus: clr-boot-manager isn't run: in the rescue system LoaderDevicePartUUID names the rescue stick's ESP, and with --path it runs in image mode, which writes a plain systemd-boot over the shim at EFI/BOOT/BOOTX64.EFI; the restored system's next `eopkg up` runs `clr-boot-manager update` itself".into());
                self.notes.push("Solus: on other hardware with Secure Boot on, Solus's key must be enrolled again: solus-enroll-me.cer, at the ESP's root, from MokManager on the first boot".into());
            }
            (other, true) if solus => {
                self.problems.push(format!("Solus with clr-boot-manager: the dump found boot loader {other}, not its systemd-boot, so bko-holt load can't tell what to keep"));
            }
            // BIOS: clr-boot-manager's GRUB2 backend (docs/design/solus.md,
            // decision 2), which nothing here covers.
            (_, false) if solus => {
                self.problems.push("Solus on BIOS (clr-boot-manager's GRUB2 backend) isn't supported by bko-holt load; only UEFI Solus is".into());
            }
            (_, true) if gentoo && via_shim => {
                self.problems.push(format!("Gentoo through shim (Secure Boot, {loader}): bko-holt load would write an unsigned boot loader over the signed one, and doesn't support it"));
            }
            // NixOS and Guix System: grub.cfg is written by
            // switch-to-configuration (guix system reconfigure), which would
            // install GRUB on the source's device names (here, the rescue
            // system's disks).  The restored grub.cfg names the filesystems
            // by UUID (or label), which the new ones keep: only the boot
            // code is installed.  NixOS's store keeps its GRUB, whose
            // grub-install does it, the newest in the store.  Guix's has
            // none (guix system init ran the installer's, and a
            // reconfigure's is garbage-collectable): the source's own
            // boot.img and core.img, which grub-install left in
            // /boot/grub/i386-pc and the restore brought back, are written
            // to each disk by the rescue system's grub-bios-setup, outside
            // the chroot, which patches their sector addresses for it.
            ("grub", false) if store.is_some() => if let Some(st) = store {
                let devs = self.grub_bios_devices();
                if devs.is_empty() {
                    self.problems.push("BIOS GRUB: no disk's MBR holds GRUB".into());
                }
                let qt = shell_quote(&t);
                match st.grub_install {
                    Some(install) => {
                        let pick = st.bios_grub_pick(&t);
                        for d in devs {
                            let qd = shell_quote(&d);
                            out.push(Cmd::new(&["sh", "-c", &format!("{pick}; chroot {qt} \"$g/{install}\" --target=i386-pc --boot-directory=/boot {qd}")]).why(format!("{}'s GRUB; the restored grub.cfg stays", st.store_name)));
                        }
                    }
                    None => {
                        let dir = format!("{t}{}/i386-pc", self.grub_dir());
                        let missing: Vec<&str> = ["boot_img", "core_img"].into_iter().filter(|k| self.layout.v.at(&["boot", "store", k]).and_then(Value::as_bool) == Some(false)).collect();
                        if !missing.is_empty() {
                            self.problems.push(format!(
                                "{}: the source's {}/i386-pc has no {}, which grub-install leaves there and bko-holt load writes to the new disk's MBR and BIOS boot partition (its store has no GRUB to install with)",
                                st.name,
                                self.grub_dir(),
                                missing.iter().map(|k| k.replace('_', ".")).collect::<Vec<_>>().join(" or ")
                            ));
                        }
                        let q = shell_quote(&dir);
                        for d in devs {
                            let (qd, ed) = (shell_quote(&d), dq_escape(&d));
                            out.push(
                                Cmd::new(&[
                                    "sh",
                                    "-c",
                                    &format!("d={q}; [ -f \"$d/boot.img\" ] && [ -f \"$d/core.img\" ] || {{ echo \"$d: no boot.img or core.img (the source's, restored with /boot/grub) to write to {ed}\" >&2; exit 1; }}; grub-bios-setup --directory=\"$d\" {qd}"),
                                ])
                                .why(format!("{}'s own boot.img and core.img, restored in {}/i386-pc; the rescue's grub-bios-setup writes them to the new disk", st.name, self.grub_dir())),
                            );
                        }
                    }
                }
                if let Some(n) = st.bios_grub {
                    self.notes.push(n.into());
                }
                self.check_store_grub_cfg(st);
            }
            ("grub", false) => {
                let devs = self.grub_bios_devices();
                if devs.is_empty() {
                    self.problems.push("BIOS GRUB: no install device in debconf's grub-pc/install_devices or in any MBR".into());
                }
                for d in devs {
                    out.push(self.grub_bios_install(&t, &d));
                }
                self.notes.push("debconf's grub-pc/install_devices still names the source's disks; `dpkg-reconfigure grub-pc` on the restored system fixes it".into());
                self.remake_grub_cfg(&t, &mut out);
            }
            // AnduinOS's own GRUB, signed on the machine with its MOK, behind
            // shim (docs/design/anduinos.md, decision 3): shim, MokManager
            // and the signed GRUB come back with the ESP.  Its grub-install
            // is plain GRUB's and would write an unsigned image over the
            // signed one, so only grub.cfg is remade.  Without shim or a
            // MOK (BIOS, a GRUB booted directly, Ubuntu's signed GRUB from
            // before 2.0.5) it's Ubuntu's, below.  Its erase-disk install
            // is a BIOS install too (a BIOS boot partition, and
            // grub-install --target=i386-pc): that's made again, since the
            // i386-pc target writes only the MBR, the BIOS boot partition
            // and /boot/grub/i386-pc, never the ESP.
            ("grub", true) if self.anduinos_signed_grub(via_shim) => {
                self.notes.push(self.anduinos_mok_note());
                for d in self.grub_core_partition_devices() {
                    out.push(Cmd::new(&["chroot", &t, "grub-install", "--target=i386-pc", &d]).why("the source's BIOS install beside the UEFI one: GRUB's core in the BIOS boot partition; the ESP isn't touched"));
                }
                self.remake_grub_cfg(&t, &mut out);
            }
            // Qubes OS (load/qubes.rs): GRUB 2 without shim, Xen's menu.
            ("grub", true) if self.is_qubes() => {
                let cmds = self.qubes_boot_cmds(&t, via_shim);
                out.extend(cmds);
            }
            ("grub", true) if self.grub_dir() == "/boot/grub2" => {
                // GRUB 2 through shim (Fedora, Rocky, openSUSE Tumbleweed
                // and Leap): grub2-install refuses EFI on Fedora (it can't
                // keep Secure Boot), and openSUSE's shim-install would
                // rewrite the signed files; shim, the signed GRUB and
                // MokManager come back with the ESP.
                self.notes.push("UEFI GRUB 2 through shim (Fedora, Rocky, openSUSE): the ESP's shim and GRUB are the restored ones; only grub.cfg is remade".into());
                self.remake_grub_cfg(&t, &mut out);
            }
            // NixOS and Guix System on UEFI: nothing runs in the chroot.
            // Their tools are in the store, found by PATH through
            // /run/current-system, which the chroot doesn't have (its /run
            // is the rescue system's).  Nor is anything needed:
            // switch-to-configuration (guix system reconfigure) wrote the
            // loader, its configuration and each generation's files, all
            // files on the ESP, /boot and (Guix's kernels) the store,
            // restored with them.  GRUB's EFI image finds /boot by its
            // filesystem's UUID, which the new one keeps (as BIOS GRUB's
            // grub.cfg does, above).  Only the boot entry is made
            // (efi_entries, below).
            ("grub", true) if store.is_some() => if let Some(st) = store {
                self.notes.push(st.uefi_grub.into());
                let g = self.boot_names().grub;
                let img = self.esp_file(&format!("/{g}")).unwrap_or_else(|| format!("EFI/{}/{g}", self.bootloader_id()));
                out.extend(self.store_removable(&img));
                self.check_store_grub_cfg(st);
            }
            ("grub", true) => {
                let esp = self.layout.str_at(&["boot", "esps", "0", "mounted_at"]).filter(|m| safe_target_subpath(m)).unwrap_or("/boot/efi").to_string();
                let id = self.bootloader_id();
                let n = self.boot_names();
                let mut a = vec!["chroot".to_string(), t.clone(), "grub-install".into(), format!("--target={}", n.grub_efi_target), format!("--efi-directory={esp}"), format!("--bootloader-id={id}"), "--no-nvram".into()];
                // --removable: Debian's grub-install writes the fallback
                // path itself (--force-extra-removable, a Debian patch);
                // upstream GRUB's refuses that option (Alpine's, Arch's,
                // Gentoo's), so its image is copied there after it
                // (BackShot2's fail-003).
                let debian = self.grub_install_is_debians();
                if self.opts.removable && debian {
                    a.push("--force-extra-removable".into());
                }
                // A fallback loader that's a copy of this GRUB (Alpine's
                // setup-disk, Calamares's installEFIFallback) is GRUB as the
                // source's grub-install made it: its embedded prefix names
                // /boot's partition by its old number (BackShot2's
                // fail-002), and with --reformat it reads only the old type,
                // so firmware that falls back to it would stop in grub
                // rescue.  It's renewed with GRUB: when the dump's ESP
                // inventory says it's a copy, always; with --reformat, also
                // when the restored files compare equal.  (FAT finds EFI/BOOT
                // in any case.)
                let (fallback, loader) = (format!("{t}{esp}/{}", n.fallback), format!("{t}{esp}/EFI/{id}/{}", n.grub));
                let (qfb, qld) = (shell_quote(&fallback), shell_quote(&loader));
                let copy = !self.opts.removable && self.fallback_is_grub_copy(&id);
                let renew = !self.opts.reformats.is_empty() && !self.opts.removable && !copy;
                if renew {
                    out.push(Cmd::new(&["sh", "-c", &format!("rm -f {RUN_DIR}/fallback-is-grub; [ ! -f {qfb} ] || ! cmp -s {qfb} {qld} || touch {RUN_DIR}/fallback-is-grub")]).why("--reformat: is the fallback loader a copy of GRUB?"));
                }
                out.push(Cmd { argv: a, ..Cmd::default() });
                if renew {
                    out.push(Cmd::new(&["sh", "-c", &format!("[ ! -e {RUN_DIR}/fallback-is-grub ] || cp {qld} {qfb}")]).why("--reformat: the fallback loader, a copy of GRUB, renewed with it"));
                }
                if copy {
                    out.push(Cmd::new(&["cp", &loader, &fallback]).why(format!("the fallback loader, {}, a copy of GRUB on the source, renewed with it: the old one names /boot's partition as it was", n.fallback)));
                }
                if self.opts.removable && !debian {
                    out.push(self.grub_removable_copy(&t, &esp, &id, via_shim));
                }
                self.remake_grub_cfg(&t, &mut out);
            }
            // Through shim (openSUSE's sdbootutil): the ESP's shim, its
            // signed systemd-boot and the fallback came back with the ESP.
            // bootctl install would put an unsigned systemd-boot where shim
            // is the fallback, EFI/BOOT/BOOTX64.EFI.
            ("systemd-boot", true) if self.layout.v.at(&["boot", "loader", "via_shim"]).and_then(Value::as_bool) == Some(true) => {
                self.notes.push("systemd-boot through shim: shim, systemd-boot and the loader entries are the restored ESP's; only the boot entries are made".into());
            }
            ("systemd-boot", true) if store.is_some_and(|s| s.systemd_boot.is_some()) => {
                self.notes.push(store.and_then(|s| s.systemd_boot).unwrap_or_default().into());
                let sd = self.boot_names().systemd_boot;
                let img = self.esp_file(&format!("/{sd}")).unwrap_or_else(|| format!("EFI/systemd/{sd}"));
                out.extend(self.store_removable(&img));
            }
            ("systemd-boot", true) => {
                out.push(Cmd::new(&["chroot", &t, "bootctl", "install", "--no-variables"]));
                if let Some(c) = self.gen_systemd_boot(&t) {
                    out.push(c);
                }
            }
            // Limine on UEFI is files on the ESP (its EFI binary,
            // limine.conf), restored with it; only the boot entries are made.
            ("limine", true) => self.notes.push("Limine: its EFI binary and limine.conf came back with the ESP; nothing to install".into()),
            // rEFInd through shim: PikaOS doesn't, and nothing here keeps
            // shim's MOK enrolment of rEFInd's key.
            ("refind", true) if via_shim => {
                self.problems.push("rEFInd through shim (Secure Boot): bko-holt load keeps only an unsigned rEFInd's files, and doesn't support it".into());
            }
            // rEFInd (PikaOS) is files too: its binary, drivers, theme and
            // refind.conf in EFI/refind and EFI/BOOT, restored with the ESP,
            // and refind_linux.conf with /boot.  refind-install isn't run: it
            // would rewrite refind.conf and make its own boot entry.
            ("refind", true) => {
                self.notes.push("rEFInd: its EFI files, drivers, theme and refind.conf came back with the ESP, and refind_linux.conf with /boot; nothing to install".into());
                self.check_refind_uuids();
            }
            // Slackware's LILO (docs/design/slackware.md): lilo writes the
            // MBR and its map file of the kernels' sectors, which the
            // restore moved, so it runs again after the initrd step;
            // `lilo -t` first, so a lilo.conf it can't use stops the stage
            // before the MBR is written.
            ("lilo", false) => {
                let cmds = self.lilo_cmds(&t);
                out.extend(cmds);
            }
            // Slackware's ELILO: elilo.efi, elilo.conf, the kernel and the
            // initrd are files on the ESP, restored with it; nothing is
            // installed.  A remade initrd is copied over the ESP's, as
            // eliloconfig copies it.
            ("elilo", true) => {
                let cmds = self.elilo_cmds(&t);
                out.extend(cmds);
            }
            (other, _) => self.problems.push(format!("boot loader {other} ({}) isn't one bko-holt load can install", if uefi { "UEFI" } else { "BIOS" })),
        }
        // BigLinux's installer ends with btrfs-fix: /boot and GRUB's files
        // rewritten uncompressed, which the restore and this stage wrote
        // through compress-force=zstd.  After the initramfs and grub.cfg.
        if self.layout.str_at(&["os", "id"]) == Some("biglinux") && self.fstab_type_of("/boot").as_deref() == Some("btrfs") {
            for d in ["/boot", "/usr/share/grub"].into_iter().filter(|d| self.fstab_type_of(d).as_deref() == Some("btrfs")) {
                out.push(Cmd::new(&["btrfs", "filesystem", "defragment", "--nocomp", "-r", &format!("{t}{d}")]).why(format!("{d} uncompressed, as BigLinux's installer leaves it (its btrfs-fix)")));
            }
        }
        // AnduinOS's Disk Snapshots Manager (decision 6): a note only.
        if self.layout.str_at(&["os", "id"]) == Some("anduinos") && self.layout.str_at(&["boot", "packages", "anduinos-btrfs-snapshots-manager"]).is_some() && !self.layout.arr(&["storage", "btrfs"]).is_empty() {
            self.notes.push("AnduinOS: the Disk Snapshots Manager's records name each snapshot by its Btrfs subvolume UUID, which a restore can't keep (the subvolumes are made anew), so its snapshot list and Factory Reset may need re-pointing at the restored snapshots".into());
        }
        if uefi && !self.opts.no_nvram {
            out.extend(self.efi_entries());
            if slackware && loader == "elilo" {
                out.extend(self.elilo_entry());
            }
            if solus {
                out.extend(self.solus_entry());
            }
            if self.is_qubes() && loader == "grub" {
                out.extend(self.qubes_entry());
            }
        }
        out.extend(self.rewrite_cmd(true));
        out.push(Cmd::new(&["mkdir", "-p", &format!("{t}/var/log/bko-holt")]));
        out.push(Cmd::new(&["cp", "-a", &format!("{RUN_DIR}/log/."), &format!("{t}/var/log/bko-holt/")]).why("this restore's log, on the restored system"));
        if store.is_none() {
            // The restore kept every file's SELinux label; what this stage
            // wrote (initramfs images, grub.cfg, the log, bootctl's
            // /etc/kernel/entry-token) gets the policy's.
            out.push(
                Cmd::new(&[
                    "chroot",
                    &t,
                    "sh",
                    "-c",
                    "[ -f /etc/selinux/config ] || exit 0; . /etc/selinux/config; [ \"${SELINUX:-disabled}\" != disabled ] || exit 0; f=/etc/selinux/${SELINUXTYPE:-targeted}/contexts/files/file_contexts; [ ! -f \"$f\" ] || ! command -v setfiles >/dev/null || { [ ! -d /etc/kernel ] || set -- /etc/kernel; setfiles \"$f\" /boot /var/log/bko-holt \"$@\"; }",
                ])
                .why("SELinux labels for the files the boot stage wrote"),
            );
            out.extend(self.mount_point_labels());
        }
        for d in ["run", "sys", "proc", "dev"] {
            out.push(Cmd::new(&["umount", "--recursive", &format!("{t}/{d}")]));
        }
        out.push(lvm_devices_back);
        out.extend(self.boot_tail(&t));
        out
    }

    /// SELinux labels for the mount points the mount stage made (BackShot1,
    /// Leap 16): the backup reads through the mounts, so the directory
    /// under each one was mkdir'd bare and the restore never reached it,
    /// and early boot (systemd-modules-load, systemd-sysctl) hit AVC
    /// denials on the unlabeled /usr/local and /var before their subvolumes
    /// mounted.  Each filesystem holding mount points is bound, not
    /// recursively, at RUN_DIR/mount-points + its own path, so those directories
    /// show bare at their logical paths, and setfiles -r labels them from
    /// the policy as the installer had them (they're empty: nothing below
    /// them is touched).  Not under a filesystem without xattrs (an ESP),
    /// nor under /dev, /proc, /sys or /run.
    fn mount_point_labels(&self) -> Option<Cmd> {
        let t = self.opts.target.trim_end_matches('/');
        let mut mps: Vec<String> = self
            .mount_points
            .iter()
            .filter_map(|d| d.strip_prefix(t))
            .map(|p| if p.is_empty() { "/".to_string() } else { p.to_string() })
            // Not under the chroot's own /dev, /proc, /sys and /run, which
            // hide the restored ones (and TrueNAS's other boot environments
            // at /run/bko-be: load's mounts, not the system's).
            .filter(|p| !["/dev", "/proc", "/sys", "/run"].iter().any(|d| p == d || p.starts_with(&format!("{d}/"))))
            .collect();
        mps.sort();
        mps.dedup();
        let under = |p: &str, m: &str| p != m && (p == "/" || m.starts_with(&format!("{p}/")));
        // (filesystem's mount point, the mount points directly on it), shallowest first.
        let mut groups: Vec<(String, Vec<String>)> = Vec::new();
        for m in mps.iter().filter(|m| *m != "/") {
            let Some(p) = mps.iter().filter(|p| under(p, m)).max_by_key(|p| p.len()) else { continue };
            if self.fstab_type_of(p).is_some_and(|ty| matches!(ty.as_str(), "vfat" | "msdos" | "exfat" | "ntfs" | "ntfs3" | "fuseblk")) {
                continue;
            }
            match groups.iter_mut().find(|(g, _)| g == p) {
                Some((_, v)) => v.push(m.clone()),
                None => groups.push((p.clone(), vec![m.clone()])),
            }
        }
        if groups.is_empty() {
            return None;
        }
        groups.sort_by_key(|(p, _)| if p == "/" { 0 } else { p.matches('/').count() });
        let mut script = format!("{SELINUX_SETFILES_GATE}r={RUN_DIR}/mount-points");
        for (p, ms) in &groups {
            let at = if p == "/" { "\"$r\"".to_string() } else { format!("\"$r\"{}", shell_quote(p)) };
            let paths: Vec<String> = ms.iter().map(|m| format!("\"$r\"{}", shell_quote(m))).collect();
            script.push_str(&format!("; mkdir -p {at} && mount --bind {} {at} || exit 1; setfiles -r \"$r\" \"$f\" {}; s=$?; umount {at}; [ $s = 0 ] || exit $s", shell_quote(p), paths.join(" ")));
        }
        Some(Cmd::new(&["chroot", &self.opts.target, "sh", "-c", &script]).why("SELinux labels for the mount points load made, which the mounts hide from the restore"))
    }

    /// Keys: the restored key files (crypttab's) replace the temporary ones
    /// LUKS was made with, or join the key --key-file gave.
    pub(crate) fn restored_key_files(&mut self, t: &str) -> Vec<Cmd> {
        let mut out = Vec::new();
        for dm in self.layout.arr(&["storage", "dm_devices"]).to_vec() {
            if dm.at(&["dm", "target"]).and_then(Value::as_str) != Some("crypt") {
                continue;
            }
            let name = s(&dm, "name").unwrap_or_default();
            // Its key is random at every boot: no keyslot, nothing to add.
            if self.layout.random_key_crypt(&name).is_some() {
                continue;
            }
            let dm_name = dm.at(&["dm", "dm_name"]).and_then(Value::as_str).unwrap_or(&name).to_string();
            let Some(e) = self.layout.crypttab_entry(&dm_name, dm.at(&["dm", "luks_uuid"]).and_then(Value::as_str)).cloned() else { continue };
            let open_as = s(&e, "name").unwrap_or(dm_name);
            let Some(k) = s(&e, "key_file").filter(|k| !matches!(k.as_str(), "none" | "-" | "")) else { continue };
            // A kept header (luks_keep_header) has the source's keyslots,
            // its key file's among them: nothing to add.
            if self.layout.slaves(&name).first().is_some_and(|u| crate::luksheader::kept(&self.layout.v, u).is_some()) {
                continue;
            }
            let under = self.layout.slaves(&name).first().and_then(|u| self.names.dev(u)).unwrap_or_default();
            // Made with --key-file's key: that keyslot stays (it's how the
            // restore was opened, and BackShot1's typed passphrase), and the
            // restored key file gets one beside it, as the source had (Manjaro's
            // Calamares: the passphrase, and /crypto_keyfile.bin, which its
            // initramfs carries and opens / with).  Not when it already opens
            // the container (--key-file's bytes are the key file's): no second
            // keyslot of the same key.  A key in systemd's path:device form
            // (BackShot1's drive unlock) is on another device, and one under
            // /dev (a raw key device, a USB stick's by-id name), /proc, /sys
            // or /run isn't in the restored tree: nothing to add, as before.
            // crypttab's keyfile-offset= and keyfile-size= say which bytes of
            // the file are the key: the keyslot is made of those.
            let slice: Vec<(&str, String)> = e
                .get("options")
                .and_then(Value::as_array)
                .into_iter()
                .flatten()
                .filter_map(Value::as_str)
                .filter_map(|o| o.split_once('='))
                .filter(|(n, _)| matches!(*n, "keyfile-offset" | "keyfile-size"))
                .map(|(n, v)| (n, v.to_string()))
                .collect();
            let new_slice: String = slice.iter().map(|(n, v)| format!(" --new-{n}={}", shell_quote(v))).collect();
            if let Some(given) = self.opts.key_file(&open_as) {
                // Not a file in the restored tree (Debian's keyscript=
                // takes a name in the key field, decrypt_derived's or
                // decrypt_keyctl's): --key-file's keyslot is the one.
                if k.contains(':') || !k.starts_with('/') || ["/dev/", "/proc/", "/sys/", "/run/"].iter().any(|d| k.starts_with(d)) {
                    continue;
                }
                if !safe_target_subpath(&k) {
                    self.problem_once(format!("{open_as}: crypttab key file {k:?} isn't a path under the target"));
                    continue;
                }
                let (r, g, u) = (shell_quote(&format!("{t}{k}")), shell_quote(given), shell_quote(&under));
                let test_slice: String = slice.iter().map(|(n, v)| format!(" --{n}={}", shell_quote(v))).collect();
                out.push(
                    Cmd::new(&["sh", "-c", &format!("cryptsetup open --test-passphrase --key-file={r}{test_slice} {u} 2>/dev/null || cryptsetup luksAddKey --key-file={g}{new_slice} {u} {r}")])
                        .why(format!("{open_as}: the restored key file {k}, a keyslot beside --key-file's, unless it opens {under} already")),
                );
                continue;
            }
            // The format stage made the container with a temporary key and
            // refused these already (luks): said again in case its crypttab
            // lookup differs, never skipped with the temporary key the only one.
            if !restorable_key_file(&k) {
                self.problem_once(needs_key_file(&open_as, &k));
                continue;
            }
            let Some(safe_open) = temp_key_name(&open_as) else {
                self.problem_once(format!("{open_as}: a LUKS name bko-holt can't keep a temporary key under: give --key-file {open_as}=FILE"));
                continue;
            };
            let tmp = format!("{RUN_DIR}/keys/{safe_open}");
            let mut add = vec!["cryptsetup".to_string(), "luksAddKey".into(), format!("--key-file={tmp}")];
            add.extend(slice.iter().map(|(n, v)| format!("--new-{n}={v}")));
            add.extend([under.clone(), format!("{t}{k}")]);
            out.push(Cmd { argv: add, why: Some(format!("{open_as}: the restored key file {k}")), ..Cmd::default() });
            out.push(Cmd::new(&["cryptsetup", "luksRemoveKey", &under, &tmp]));
        }
        out
    }

    /// `bko-holt rewrite` for --reformat (rewrite.rs): first the restored
    /// files that name the old filesystems, before the initramfs and the
    /// loader are made from them; with CHECK, at the end, the initramfs read
    /// back for the new type's module, and what's left of the old type listed.
    fn rewrite_cmd(&self, check: bool) -> Option<Cmd> {
        if self.opts.reformats.is_empty() {
            return None;
        }
        let exe = std::env::current_exe().map(|p| p.to_string_lossy().into_owned()).unwrap_or_else(|_| "bko-holt".into());
        let mut a = vec![exe, "rewrite".into(), self.opts.layout.clone(), self.opts.target.clone()];
        if check {
            a.push("--check".into());
        }
        for (m, ty) in &self.opts.reformats {
            a.push("--reformat".into());
            a.push(format!("{m}={ty}"));
        }
        let why = if check { "--reformat: the initramfs has the new type's module; what's left of the old type, listed" } else { "--reformat: fstab and the kernel command lines for the new filesystem" };
        Some(Cmd { argv: a, why: Some(why.into()), ..Cmd::default() })
    }

    /// atimes_cmd for this layout (Ubuntu Core's tree is under T/run/mnt).
    pub(crate) fn atimes(&self, t: &str, what: &str) -> String {
        atimes_cmd_in(t, what, &self.ro_shared(), super::snapcore::of(&self.layout.v).is_some())
    }

    /// How every Linux boot stage ends (ostree's too, load/ostree.rs): the
    /// atimes put back, then the read-only btrfs subvolumes made so.
    pub(crate) fn boot_tail(&mut self, t: &str) -> Vec<Cmd> {
        let mut out = Vec::new();
        // Last but for read-only flags: nothing may read the tree after this.
        out.push(Cmd::new(&["bash", "-c", &self.atimes(t, "restore")]).why("the atimes this stage's reads changed, put back"));
        // Read-only btrfs subvolumes: set now that the restore filled them,
        // and after the atimes are put back (touch can't on a read-only one).
        for fs in self.layout.arr(&["storage", "btrfs"]).to_vec() {
            let uuid = s(&fs, "uuid").unwrap_or_default();
            let plan = self.btrfs.get(&uuid).cloned().unwrap_or_default();
            let ro: Vec<String> = fs
                .get("subvolumes")
                .and_then(Value::as_array)
                .into_iter()
                .flatten()
                .filter(|v| v.get("readonly").and_then(Value::as_bool) == Some(true))
                .filter(|v| !self.is_timeshift(&fs, v)) // not made
                .filter_map(|v| s(v, "path").map(|p| p.trim_start_matches("<FS_TREE>/").to_string()))
                .filter(|p| !plan.is_shared(p)) // the restore stage did those
                .collect();
            if ro.is_empty() {
                continue;
            }
            let Some(dev) = self.btrfs_dev(&uuid, Some(&fs)) else { continue };
            let top = format!("{RUN_DIR}/btrfs/{uuid}");
            out.push(Cmd::new(&["mount", "-t", "btrfs", "-o", "subvolid=5", &dev, &top]));
            for p in ro {
                out.push(Cmd::new(&["btrfs", "property", "set", "-ts", &format!("{top}/{p}"), "ro", "true"]));
            }
            out.push(Cmd::new(&["umount", &top]));
        }
        out
    }

    /// grub.cfg remade, with the LANG the source's was made with: it
    /// decides whether grub.cfg loads gettext and sets lang.  Debian and its
    /// derivatives have update-grub; Arch and Fedora run grub-mkconfig.
    fn update_grub(&self, t: &str) -> Cmd {
        let lang = match self.layout.str_at(&["boot", "grub", "grub_cfg", "lang"]) {
            Some(l) if !l.is_empty() => format!("LANG={l}.UTF-8"),
            _ => "LANG=C".to_string(),
        };
        if matches!(self.layout.str_at(&["os", "id"]), Some(id) if DEBIAN_GRUB_IDS.contains(&id)) {
            return Cmd::new(&["chroot", t, "env", &lang, "update-grub"]);
        }
        let cfg = format!("{}/grub.cfg", self.grub_dir());
        Cmd::new(&["chroot", t, "env", &lang, self.grub_tool("mkconfig"), "-o", &cfg])
    }

    /// grub.cfg remade with os-prober out of the way: in the rescue system
    /// it would find the rescue system's own disks and list them.
    pub(crate) fn remake_grub_cfg(&self, t: &str, out: &mut Vec<Cmd>) {
        let (off, on) = super::qubes::os_prober_off(t);
        out.push(Cmd::new(&["sh", "-c", &off]).why("os-prober off: it would list the rescue system's disks"));
        out.push(self.update_grub(t));
        out.push(Cmd::new(&["sh", "-c", &on]));
    }

    /// The type of the filesystem fstab mounts `dir` (an absolute path) on:
    /// its deepest mount at or above it.
    fn fstab_type_of(&self, dir: &str) -> Option<String> {
        self.layout
            .arr(&["storage", "fstab"])
            .iter()
            .filter_map(|e| Some((s(e, "file")?, s(e, "type")?)))
            .filter(|(f, ty)| ty != "swap" && (f == "/" || dir == f || dir.starts_with(&format!("{}/", f.trim_end_matches('/')))))
            .max_by_key(|(f, _)| f.len())
            .map(|(_, ty)| ty)
    }

    /// AnduinOS with its own GRUB on the ESP, signed with the machine's MOK:
    /// the dump found the MOK beside anduinos-grub (boot.loader.mok), or the
    /// firmware booted shim and anduinos-grub is installed.
    /// Solus's ESP mounted at /boot in the target, as it was when the dump
    /// was made (boot.esps' mounted_at /boot, in_fstab false), with its
    /// mount's options; fstab has no line for it.
    fn solus_esp_mount(&mut self) -> Vec<(String, Cmd)> {
        let target = self.opts.target.clone();
        let esp = self.layout.arr(&["boot", "esps"]).iter().find(|e| s(e, "mounted_at").as_deref() == Some("/boot")).and_then(|e| s(e, "device"));
        let Some(src) = esp else {
            self.problems.push("Solus: no ESP was mounted at /boot when the dump was made, so the restore has nowhere to put the kernels and the loader".into());
            return Vec::new();
        };
        let Some(dev) = self.names.dev(&src) else {
            self.problems.push(format!("Solus: its ESP {src} (at /boot) isn't restored"));
            return Vec::new();
        };
        let opts = self.layout.arr(&["storage", "mounts"]).iter().find(|m| s(m, "target").as_deref() == Some("/boot")).and_then(|m| s(m, "options")).map(|o| mount_options(&o)).unwrap_or_else(|| "defaults".into());
        self.notes.push(format!("Solus: its ESP {src} is mounted at /boot for the restore, though fstab has no line for it (clr-boot-manager mounts it when it needs it)"));
        vec![("/boot".into(), Cmd::new(&["mount", "-t", "vfat", "-o", &opts, &dev, &format!("{target}/boot")]))]
    }

    fn solus_cbm(&self) -> bool {
        self.layout.str_at(&["os", "id"]) == Some("solus") && self.layout.str_at(&["boot", "loader", "manager"]) == Some(crate::boot::CBM)
    }

    /// Solus's boot entry, as clr-boot-manager makes it ("Solus Linux
    /// Bootloader", its shim \EFI\com.solus-project\bootloaderx64.efi on
    /// the ESP that holds it), when efi_entries made none for that shim: a
    /// source booted from the fallback path (blank NVRAM) has none.  Last,
    /// so that it comes first in BootOrder.
    fn solus_entry(&mut self) -> Vec<Cmd> {
        let shim = crate::boot::CBM_SHIM;
        let loader = format!("\\{}", shim.replace('/', "\\"));
        let order: Vec<String> = self.layout.v.at(&["boot", "efi", "boot_order"]).and_then(Value::as_array).into_iter().flatten().filter_map(|x| x.as_str().map(str::to_string)).collect();
        let on_layout = |part: &str| self.layout.disks.iter().any(|d| d.parts.iter().any(|p| p.name == part));
        let made = self.layout.arr(&["boot", "efi", "entries"]).iter().any(|e| {
            s(e, "number").is_some_and(|n| order.contains(&n))
                && s(e, "file").is_some_and(|f| f.replace('/', "\\").trim_start_matches('\\').eq_ignore_ascii_case(&loader[1..]))
                && e.at(&["partition", "device"]).and_then(Value::as_str).is_some_and(on_layout)
        });
        if made {
            return Vec::new();
        }
        let holds = |esp: &Value| esp.at(&["contents", "files"]).and_then(Value::as_array).is_some_and(|f| f.iter().any(|f| f.get("path").and_then(Value::as_str).is_some_and(|p| p.eq_ignore_ascii_case(shim))));
        let Some(part) = self.layout.arr(&["boot", "esps"]).iter().find(|e| holds(e)).and_then(|e| s(e, "device")) else {
            self.problems.push(format!("Solus: no ESP in the dump holds clr-boot-manager's shim, {shim}, so there's nothing to boot"));
            return Vec::new();
        };
        let Some((disk, num)) = self.layout.disks.iter().find_map(|d| d.parts.iter().find(|p| p.name == part).map(|p| (d.name.clone(), p.number))) else {
            self.notes.push(format!("Solus: its ESP {part} isn't on the layout's disks; no boot entry made"));
            return Vec::new();
        };
        let Some(tgt) = self.names.mapping.target_of(&disk) else { return Vec::new() };
        vec![
            efi_dedupe(&format!("/dev/{tgt}"), num, &loader),
            Cmd::new(&["efibootmgr", "--quiet", "--create", "--disk", &format!("/dev/{tgt}"), "--part", &num.to_string(), "--label", crate::boot::CBM_ENTRY, "--loader", &loader])
                .why("Solus: the boot entry clr-boot-manager makes (the source's NVRAM had none for its shim)"),
        ]
    }

    fn anduinos_signed_grub(&self, via_shim: bool) -> bool {
        self.layout.str_at(&["os", "id"]) == Some("anduinos")
            && (self.layout.v.at(&["boot", "loader", "mok"]).is_some_and(|m| !m.is_null()) || (via_shim && self.layout.str_at(&["boot", "packages", "anduinos-grub"]).is_some()))
    }

    /// What the restored machine's firmware needs to boot AnduinOS's
    /// MOK-signed GRUB: the same MOK, which only the source's machine has.
    fn anduinos_mok_note(&self) -> String {
        let cert = self.layout.str_at(&["boot", "loader", "mok", "cert"]).unwrap_or(crate::boot::ANDUINOS_MOK);
        let key = match self.layout.str_at(&["boot", "loader", "mok", "sha256"]) {
            Some(fp) => format!("the MOK {cert} (SHA-256 {fp})"),
            None => format!("the MOK {cert} (its fingerprint not in the dump)"),
        };
        let target = self.boot_names().grub_efi_target;
        format!(
            "AnduinOS: shim, MokManager and AnduinOS's GRUB, signed with {key}, are the restored ESP's; grub-install --target={target} isn't run (AnduinOS's is plain GRUB and would write an unsigned image), only grub.cfg is remade.  On the same machine its firmware holds the key; on other hardware with Secure Boot on, enroll it again (`mokutil --import {cert}`, then confirm it at MokManager on the next boot) or turn Secure Boot off"
        )
    }

    /// /boot/grub, or Fedora's /boot/grub2.
    pub(crate) fn grub_dir(&self) -> &str {
        self.layout.str_at(&["boot", "grub", "dir"]).unwrap_or("/boot/grub")
    }

    /// BIOS GRUB's grub-install onto DISK, in the target T; when the layout
    /// has NILFS2, through GRUB_BIOS_NILFS2, which makes it hide a NILFS2
    /// superblock at the disk's end from GRUB's look at the whole disk.
    fn grub_bios_install(&self, t: &str, disk: &str) -> Cmd {
        let install = ["chroot", t, self.grub_tool("install"), "--target=i386-pc", disk];
        if !self.leaves().iter().any(|(_, c)| s(c, "type").as_deref() == Some("nilfs2")) {
            return Cmd::new(&install);
        }
        let mut a = vec!["sh", "-c", GRUB_BIOS_NILFS2, "sh", disk];
        a.extend(install);
        Cmd::new(&a).why(format!("{disk}: GRUB would take a NILFS2 partition's secondary superblock, when it's the disk's last 4 KiB, for a filesystem on the whole disk, and refuse to embed core.img; while grub-install runs, that superblock's magic is cleared, then put back"))
    }

    /// grub-install / grub-mkconfig, or Fedora's grub2-*.
    fn grub_tool(&self, what: &str) -> &'static str {
        match (self.grub_dir() == "/boot/grub2", what) {
            (true, "install") => "grub2-install",
            (true, _) => "grub2-mkconfig",
            (false, "install") => "grub-install",
            (false, _) => "grub-mkconfig",
        }
    }

    /// The target's / remounted with the atime options the source's / was
    /// mounted with (the dump's storage.mounts), before dracut runs in the
    /// chroot.  Host-only dracut writes the root's mount options as it
    /// finds them (findmnt's OPTIONS for /) into the image's
    /// etc/cmdline.d/20-root-dev.conf as rootflags=, and the restored
    /// machine's initramfs then mounts / with them: whatever changed the
    /// rescue system's mount since the mount stage would last on the
    /// restored machine.  BackShot1's diff remounts the target noatime and
    /// puts it back with remount,relatime, which a ZFS dataset ignores (it
    /// stays noatime, atime=off temporary): the Thinkpad's Ubuntu 26.10 on
    /// ZFS came back with rootflags=...,noatime,... and / noatime (run
    /// ubuntu-26.10-20261119-050205).  So `atime` with relatime or
    /// strictatime, which a ZFS dataset takes too; only the atime options:
    /// the rest of the root's options come from the same fstab line or
    /// dataset properties as the source's.  Only for dracut: no other
    /// generator records the root's options.
    fn root_atime_remount(&self, t: &str) -> Option<Cmd> {
        if self.layout.str_at(&["boot", "initramfs", "generator"]) != Some("dracut") {
            return None;
        }
        let m = self.layout.arr(&["storage", "mounts"]).iter().find(|m| s(m, "target").as_deref() == Some("/"))?;
        let o = s(m, "options")?;
        let has = |x: &str| o.split(',').any(|y| y == x);
        let mut a = if has("noatime") {
            "noatime"
        } else if has("relatime") {
            "atime,relatime"
        } else {
            "atime,strictatime"
        }
        .to_string();
        if has("nodiratime") {
            a.push_str(",nodiratime");
        }
        Some(Cmd::new(&["mount", "-o", &format!("remount,{a}"), t]).why(format!("/ with the source's atime options ({o}), for host-only dracut's rootflags=")))
    }

    /// Every initramfs remade by the source's own generator; None for one
    /// that came prebuilt with its kernel (AerynOS).
    fn initramfs(&self, t: &str) -> Option<Cmd> {
        Some(match self.layout.str_at(&["boot", "initramfs", "generator"]) {
            Some("prebuilt") => return None,
            Some("mkinitcpio") => Cmd::new(&["chroot", t, "mkinitcpio", "-P"]),
            // EndeavourOS with GRUB (eos-dracut) and Garuda
            // (garuda-dracut-support) both name each initramfs after its
            // kernel's package (/boot/initramfs-linux.img,
            // initramfs-linux-garuda.img), which dracut's own choice of path
            // would miss; their dracut-rebuild builds them, with fallbacks.
            // With a swap device, the resume module kept (DRACUT_REBUILD_RESUME).
            Some("dracut") if ["eos-dracut", "garuda-dracut-support"].iter().any(|p| self.layout.str_at(&["boot", "packages", *p]).is_some()) => match self.swap_device() {
                true => Cmd::new(&["chroot", t, "sh", "-c", DRACUT_REBUILD_RESUME]),
                false => Cmd::new(&["chroot", t, "dracut-rebuild"]),
            },
            Some("dracut") => Cmd::new(&["chroot", t, "sh", "-c", DRACUT_EACH_KERNEL]),
            // Alpine: mkinitfs KVER writes /boot/initramfs-FLAVOR.
            Some("mkinitfs") => Cmd::new(&["chroot", t, "sh", "-c", "for k in /lib/modules/*; do mkinitfs \"${k##*/}\" || exit; done"]),
            // PikaOS: what booster's own update-initramfs runs for each
            // kernel, without its dpkg-trigger and backup paths; host-only
            // as booster.yaml says, from the chroot's /sys (this machine's).
            // The same guard as dracut's loop.
            Some("booster") => Cmd::new(&["chroot", t, "sh", "-c", "n=0; for k in /lib/modules/*; do [ -d \"$k/kernel\" ] || continue; k=${k##*/}; booster build --force --kernel-version \"$k\" \"/boot/booster.img-$k\" || exit; n=$((n + 1)); done; [ \"$n\" -gt 0 ]"]),
            // initramfs-tools picked the resume device itself (RESUME unset
            // or auto, a swap device to pick): UPDATE_INITRAMFS_RESUME.
            Some("initramfs-tools") if self.swap_device() && self.layout.str_at(&["boot", "initramfs", "resume"]).is_none_or(|r| r == "auto") => {
                Cmd::new(&["chroot", t, "sh", "-c", UPDATE_INITRAMFS_RESUME])
            }
            _ => Cmd::new(&["chroot", t, "update-initramfs", "-u", "-k", "all"]),
        })
    }

    /// Whether the source's fstab swaps on a device (not a swap file: what
    /// initramfs-tools' resume hook and dracut's resume module look for).
    fn swap_device(&self) -> bool {
        self.layout.arr(&["storage", "fstab"]).iter().any(|e| {
            s(e, "type").as_deref() == Some("swap") && s(e, "spec").is_some_and(|sp| !sp.starts_with('/') || sp.starts_with("/dev/"))
        })
    }

    /// Gentoo's initramfs step (docs/design/gentoo.md, decisions 2, 3 and
    /// 6): installkernel's generator as the dump read it, with bko-holt's
    /// own commands and no Portage or Python in the chroot.  dracut makes
    /// each kernel's image under the name installkernel gives it
    /// (/boot/initramfs-K.img), named on the command line: left to itself,
    /// dracut 112 could pick /efi/MACHINE-ID/K/initrd or
    /// /lib/modules/K/initrd.  Its host-only image keeps what the restored
    /// one had and the rescue system's chroot can't show it (the modules,
    /// the resume module and resume=: DRACUT_ONE_IMAGE).  `none` makes nothing.  Everything else is
    /// refused by name: genkernel, ugrd, a unified kernel image, an
    /// installkernel layout other than grub or compat, an image at
    /// /lib/modules/K/initrd.  installkernel's .old copies come back with
    /// /boot as they were.
    fn gentoo_initramfs(&mut self, t: &str) -> Vec<Cmd> {
        let mut out = Vec::new();
        let ik = self.layout.v.at(&["boot", "initramfs", "installkernel"]).filter(|v| !v.is_null()).cloned();
        if let Some(ik) = &ik {
            let conf = s(ik, "conf").unwrap_or_else(|| "install.conf".into());
            match s(ik, "uki_generator").as_deref() {
                None | Some("" | "none") => {}
                Some(u) => self.problems.push(format!("Gentoo: {conf} says uki_generator={u}; bko-holt load doesn't make unified kernel images")),
            }
            match s(ik, "layout").as_deref() {
                Some("grub" | "compat") => {}
                Some(l) => self.problems.push(format!("Gentoo: {conf} says layout={l}; bko-holt load supports only installkernel's grub and compat layouts (the kernels and their images in /boot)")),
                None => self.problems.push(format!("Gentoo: {conf} names no layout; bko-holt load supports only installkernel's grub and compat layouts (the kernels and their images in /boot)")),
            }
        }
        for k in self.layout.arr(&["boot", "initramfs", "lib_modules_initrd"]).to_vec() {
            let k = k.as_str().unwrap_or_default();
            self.problems.push(format!("Gentoo: kernel {k} has its initramfs at /lib/modules/{k}/initrd, where bko-holt load doesn't make one"));
        }
        let generator = self.layout.str_at(&["boot", "initramfs", "generator"]).unwrap_or("unknown").to_string();
        match generator.as_str() {
            "dracut" => {
                // Kernels with an initramfs in /boot, remade or not: one whose
                // modules are missing keeps its restored image, with a note.
                let mut with_image = 0;
                for k in self.layout.arr(&["boot", "kernels", "installed"]).to_vec() {
                    let Some(v) = s(&k, "version") else { continue };
                    if k.get("dir").is_some_and(|d| !d.is_null()) || k.get("initrd_bytes").is_none_or(Value::is_null) {
                        continue;
                    }
                    with_image += 1;
                    if k.get("modules_installed").and_then(Value::as_bool) != Some(true) {
                        self.notes.push(format!("Gentoo: kernel {v} has no /lib/modules/{v}, so dracut can't remake its initramfs; the restored one stays"));
                        continue;
                    }
                    out.push(Cmd::new(&["chroot", t, "sh", "-c", DRACUT_ONE_IMAGE, "sh", &v, &format!("/boot/initramfs-{v}.img")])
                        .why(format!("kernel {v}'s initramfs, by the name installkernel gives it, with the restored image's modules and resume")));
                }
                if with_image == 0 {
                    self.problems.push("Gentoo: the initramfs generator is dracut, but no kernel in /boot has an initramfs to remake".into());
                }
            }
            "none" => self.notes.push("Gentoo: no initramfs generator (installkernel's initrd_generator=none, or no initramfs in /boot): nothing is made, and the kernels boot without one, as on the source".into()),
            "genkernel" | "ugrd" => self.problems.push(format!("Gentoo: the initramfs is {generator}'s; bko-holt load remakes only dracut's (or none)")),
            other => self.problems.push(format!("Gentoo: initramfs generator {other} isn't one bko-holt load can run (only dracut, or none)")),
        }
        let old: Vec<String> = self.layout.arr(&["boot", "kernels", "old_copies"]).iter().filter_map(|v| v.as_str().map(str::to_string)).collect();
        if !old.is_empty() {
            self.notes.push(format!("Gentoo: installkernel's .old copies in /boot ({}) came back as they were: grub-mkconfig lists them, and this stage doesn't remake them", old.join(", ")));
        }
        out
    }

    /// boot.proxmox_boot, when the source's ESPs are proxmox-boot-tool's.
    fn proxmox_boot(&self) -> Option<&Value> {
        self.layout.v.at(&["boot", "proxmox_boot"]).filter(|v| !v.is_null())
    }

    /// The source devices of the ESPs that proxmox-boot-tool keeps in
    /// systemd-boot mode: `init` runs bootctl install on each, which makes
    /// its own boot entry for it.
    fn proxmox_systemd_boot_esps(&self) -> Vec<String> {
        let Some(pb) = self.proxmox_boot() else { return Vec::new() };
        pb.get("esps").and_then(Value::as_array).into_iter().flatten().filter(|e| s(e, "mode").as_deref() == Some("systemd-boot")).filter_map(|e| s(e, "device")).collect()
    }

    /// Proxmox VE's ESPs, rebuilt by their owner (docs/design/proxmox.md,
    /// decisions 3-5): format made each with its volume ID, so
    /// /etc/kernel/proxmox-boot-uuids is still true; in the chroot,
    /// `proxmox-boot-tool init` on each in the list's order, by the
    /// source's mode (not by this machine's firmware, as `reinit` would),
    /// then one `refresh` (every kernel, initrd and loader entry on every
    /// ESP, the pin and manual kernels honoured), then the list written back
    /// as it was.  Only Proxmox VE 9 and systemd-boot mode are supported:
    /// GRUB's modes (BIOS, or EFI, through shim or not) are written but
    /// refused, not tested.
    fn proxmox_boot_cmds(&mut self, t: &str, via_shim: bool) -> Vec<Cmd> {
        let pb = self.proxmox_boot().cloned().unwrap_or(Value::Null);
        let mut out = Vec::new();
        match self.layout.str_at(&["os", "product"]) {
            Some(p) if p.strip_prefix("Proxmox VE ").and_then(|v| v.split('.').next()) == Some("9") => {}
            Some(p) => self.problems.push(format!("Proxmox: the source is {p}; bko-holt load rebuilds proxmox-boot-tool's ESPs on Proxmox VE 9 only")),
            None => self.problems.push("Proxmox: proxmox-boot-tool keeps the source's ESPs, but the dump names no Proxmox VE release (pve-manager isn't installed); bko-holt load rebuilds them on Proxmox VE 9 only".into()),
        }
        if via_shim {
            self.problems.push("Proxmox through shim (Secure Boot, signed GRUB on each ESP): bko-holt load doesn't support it (not tested)".into());
        }
        let uuids: Vec<String> = pb.get("uuids").and_then(Value::as_array).into_iter().flatten().filter_map(|u| u.as_str().map(str::to_string)).collect();
        if uuids.is_empty() {
            self.problems.push(format!("Proxmox: {} lists no ESP, so proxmox-boot-tool has nothing to rebuild and the restored system wouldn't boot", crate::boot::PROXMOX_BOOT_UUIDS));
        }
        let esps = pb.get("esps").and_then(Value::as_array).cloned().unwrap_or_default();
        let mut modes: Vec<String> = Vec::new();
        for u in &uuids {
            let e = esps.iter().find(|e| s(e, "uuid").as_deref() == Some(u.as_str()));
            let Some(dev) = e.and_then(|e| s(e, "device")) else {
                self.problems.push(format!("Proxmox: {} lists ESP {u}, which no partition on the source has; proxmox-boot-tool couldn't rebuild it", crate::boot::PROXMOX_BOOT_UUIDS));
                continue;
            };
            if self.names.dev(&dev).is_none() {
                self.problems.push(format!("Proxmox: ESP {u} is {dev}, which this layout doesn't make; proxmox-boot-tool couldn't rebuild it"));
                continue;
            }
            let mode = e.and_then(|e| s(e, "mode"));
            let mut a = vec!["proxmox-boot-tool".to_string(), "init".into(), format!("/dev/disk/by-uuid/{u}")];
            match mode.as_deref() {
                Some("systemd-boot") => {}
                Some(m @ ("grub-efi" | "grub-bios")) => {
                    a.push("grub".into());
                    self.problems.push(format!("Proxmox: ESP {u} ({dev}) is in proxmox-boot-tool's GRUB mode ({m}); bko-holt load supports only its systemd-boot mode (GRUB's isn't tested)"));
                }
                _ => {
                    self.problems.push(format!("Proxmox: the dump doesn't say ESP {u} ({dev})'s proxmox-boot-tool mode (its files couldn't be read)"));
                    continue;
                }
            }
            let m = mode.unwrap_or_default();
            if !modes.contains(&m) {
                modes.push(m.clone());
            }
            let why = format!("ESP {u} ({dev}): made again by proxmox-boot-tool, in its {m} mode as on the source");
            // --no-nvram: bootctl install (in init) would make its boot
            // entry.  With efivarfs merely unmounted, systemd 257's bootctl
            // --graceful still fails ("Failed to create EFI Boot variable
            // entry: No such file or directory", HoltTest1 pve6-1..4), so
            // init runs over an empty tmpfs in efivarfs' place, in a mount
            // namespace of its own: its writes land there and are thrown
            // away, the machine's NVRAM untouched.  If the tmpfs can't be
            // mounted, init doesn't run (it would write the real NVRAM).
            out.push(if self.opts.no_nvram {
                let cmd = format!("mount -t tmpfs -o mode=0755 bko-novars {}/sys/firmware/efi/efivars && exec chroot {} {}", shell_quote(t), shell_quote(t), a.iter().map(|x| shell_quote(x)).collect::<Vec<_>>().join(" "));
                Cmd::new(&["unshare", "--mount", "--propagation", "private", "sh", "-c", &cmd]).why(format!("{why}; --no-nvram: over an empty tmpfs in place of the EFI variables, in a mount namespace of its own"))
            } else {
                let mut argv = vec!["chroot".to_string(), t.to_string()];
                argv.extend(a);
                Cmd { argv, why: Some(why), ..Cmd::default() }
            });
        }
        out.push(Cmd::new(&["chroot", t, "proxmox-boot-tool", "refresh"]).why("every kernel, initrd and loader entry onto every ESP, as a kernel update does"));
        let list: String = uuids.iter().map(|u| format!("{u}\n")).collect();
        out.push(Cmd::write_file(&format!("{t}{}", crate::boot::PROXMOX_BOOT_UUIDS), list).why("proxmox-boot-tool's list of ESPs as the source had it (init adds to it)"));
        let n = out.len() - 2;
        self.notes.push(format!(
            "Proxmox: proxmox-boot-tool rebuilt {n} ESP{} ({}); their old content isn't in the archive",
            if n == 1 { "" } else { "s" },
            if modes.is_empty() { "none".to_string() } else { modes.join(", ") }
        ));
        out
    }

    /// Chimera's systemd-boot entries, remade as its kernel hook
    /// (/usr/lib/kernel.d/99-gen-systemd-boot.sh) remakes them: one per
    /// kernel in /boot and a recovery one, named by /etc/kernel/entry-token.
    /// The command line is /etc/default/systemd-boot's and
    /// systemd-boot-cmdline's, restored; root= is theirs when they have one,
    /// else kernel-root-detect's, from what / is mounted from in the chroot
    /// (the target's root, by PARTUUID, /dev/mapper name or UUID), never the
    /// rescue system's /proc/cmdline.  Its exit status 1 is "made nothing"
    /// (disabled, or bootctl says systemd-boot isn't installed), which the
    /// hook doesn't count as failing: the restored ESP's entries stay.
    /// Other systems with systemd-boot write their entries with
    /// kernel-install or their own tools, and they came back with the ESP.
    fn gen_systemd_boot(&self, t: &str) -> Option<Cmd> {
        if self.layout.str_at(&["os", "id"]) != Some("chimera") || self.layout.str_at(&["boot", "packages", "systemd-boot"]).is_none() {
            return None;
        }
        Some(
            Cmd::new(&[
                "chroot",
                t,
                "sh",
                "-c",
                "gen-systemd-boot; r=$?; [ \"$r\" -ne 1 ] || { echo 'gen-systemd-boot: warning: it made no entries (disabled, or bootctl says systemd-boot is not installed); the restored ones stay' >&2; r=0; }; exit \"$r\"",
            ])
            .why("the boot entries, for the restored kernels and root (Chimera's gen-systemd-boot, as its kernel hook runs it)")
            .warns(),
        )
    }

    /// The source machine's boot names (crate::arch): grubx64.efi or
    /// grubaa64.efi, x86_64-efi or arm64-efi, BOOTX64.EFI or BOOTAA64.EFI.
    pub(crate) fn boot_names(&self) -> &'static crate::arch::Names {
        crate::arch::names_of_dump(&self.layout.v)
    }

    /// The path on the first ESP, as the dump listed it, of the file whose
    /// name ends in `suffix` (any case), outside EFI/BOOT.
    fn esp_file(&self, suffix: &str) -> Option<String> {
        let files = self.layout.arr(&["boot", "esps", "0", "contents", "files"]).to_vec();
        files
            .iter()
            .filter_map(|f| s(f, "path"))
            .find(|p| p.to_ascii_lowercase().ends_with(suffix) && !p.to_ascii_lowercase().starts_with("efi/boot/"))
    }

    /// Whether the source's grub-install is Debian's, which has
    /// --force-extra-removable (a Debian patch; upstream GRUB has no such
    /// option): Debian's GRUB comes in grub-common and its UEFI packages
    /// are grub-efi-ARCH(-signed); or the system is one of Debian's family.
    fn grub_install_is_debians(&self) -> bool {
        self.layout.str_at(&["boot", "packages", "grub-common"]).is_some()
            || self.layout.str_at(&["boot", "loader", "package"]).is_some_and(|p| p.starts_with("grub-efi-"))
            || matches!(self.layout.str_at(&["os", "id"]), Some(id) if DEBIAN_GRUB_IDS.contains(&id))
    }

    /// Whether the source's ESP had its fallback loader (EFI/BOOT/BOOTX64.EFI,
    /// BOOTAA64.EFI) as a byte copy of GRUB's image in EFI/ID, as the
    /// dump's ESP inventory says (its same_as, which names the first copy
    /// found: either way round, or both naming a third).
    fn fallback_is_grub_copy(&self, id: &str) -> bool {
        let n = self.boot_names();
        let files = self.layout.arr(&["boot", "esps", "0", "contents", "files"]);
        let find = |p: &str| files.iter().find(|f| s(f, "path").is_some_and(|x| x.eq_ignore_ascii_case(p)));
        let (Some(fb), Some(grub)) = (find(n.fallback), find(&format!("EFI/{id}/{}", n.grub))) else { return false };
        let names = |a: &Value, b: &Value| s(a, "same_as").zip(s(b, "path")).is_some_and(|(x, y)| x.eq_ignore_ascii_case(&y));
        names(fb, grub) || names(grub, fb) || s(fb, "same_as").zip(s(grub, "same_as")).is_some_and(|(x, y)| x.eq_ignore_ascii_case(&y))
    }

    /// --removable for upstream GRUB, whose grub-install has no
    /// --force-extra-removable: the image it just wrote to EFI/ID copied to
    /// the fallback path; through shim, shim there with GRUB beside it, as
    /// Debian's option does (shim loads GRUB from its own directory).
    fn grub_removable_copy(&self, t: &str, esp: &str, id: &str, via_shim: bool) -> Cmd {
        let n = self.boot_names();
        let (from, dir) = (shell_quote(&format!("{t}{esp}/EFI/{id}")), shell_quote(&format!("{t}{esp}/EFI/BOOT")));
        let (f, g) = (n.fallback_file, n.grub);
        if via_shim {
            let sh = n.shim;
            return Cmd::new(&["sh", "-c", &format!("mkdir -p {dir} && cp {from}/{sh} {dir}/{f} && cp {from}/{g} {dir}/{g}")]).why(format!("--removable: shim and GRUB at the fallback path too ({}'s grub-install has no --force-extra-removable, Debian's option)", self.layout.str_at(&["os", "id"]).unwrap_or("this system")));
        }
        Cmd::new(&["sh", "-c", &format!("mkdir -p {dir} && cp {from}/{g} {dir}/{f}")]).why(format!("--removable: GRUB at the fallback path too ({}'s grub-install has no --force-extra-removable, Debian's option)", self.layout.str_at(&["os", "id"]).unwrap_or("this system")))
    }

    /// A store system (NixOS, Guix System) with --removable: the ESP's
    /// loader image `img` (a path on the ESP) is copied to the fallback
    /// path, EFI/BOOT/BOOTX64.EFI (BOOTAA64.EFI on ARM64), unless the
    /// restored ESP has one (bootctl install, which NixOS runs, writes it).
    /// Nothing runs in the chroot.
    fn store_removable(&self, img: &str) -> Vec<Cmd> {
        if !self.opts.removable {
            return Vec::new();
        }
        let esp = format!("{}{}", self.opts.target, self.layout.str_at(&["boot", "esps", "0", "mounted_at"]).filter(|m| safe_target_subpath(m)).unwrap_or("/boot").trim_end_matches('/'));
        let (src, dir) = (shell_quote(&format!("{esp}/{img}")), shell_quote(&format!("{esp}/EFI/BOOT")));
        let f = self.boot_names().fallback_file;
        vec![Cmd::new(&["sh", "-c", &format!("[ -e {dir}/{f} ] || {{ mkdir -p {dir} && cp {src} {dir}/{f}; }}")]).why("--removable: the ESP's loader at the fallback path too")]
    }

    /// The target disks whose source has GRUB's BIOS core in its BIOS boot
    /// partition, as the dump's boot_code found it (a UEFI install that is
    /// a BIOS install too).  A disk with a BIOS boot partition and no core
    /// recorded gets a note: it boots by UEFI only.
    pub(crate) fn grub_core_partition_devices(&mut self) -> Vec<String> {
        const BIOS_BOOT: &str = "21686148-6449-6e6f-744e-656564454649";
        let mut out = Vec::new();
        for d in &self.layout.disks {
            let Some(t) = self.names.mapping.target_of(&d.name) else { continue };
            if !d.parts.iter().any(|p| p.ptype.eq_ignore_ascii_case(BIOS_BOOT)) {
                continue;
            }
            let code = self.layout.device(&d.name).and_then(|v| v.get("boot_code"));
            let at = |k: &str| code.and_then(|c| c.get(k));
            let core = at("kind").and_then(Value::as_str) == Some("grub2") && at("grub_core_in").and_then(Value::as_str) == Some("partition") && at("grub_core_present").and_then(Value::as_bool) == Some(true);
            if core {
                out.push(format!("/dev/{t}"));
            } else {
                self.notes.push(format!("{} has a BIOS boot partition, but the dump records no GRUB core in it: no BIOS boot code is written, and /dev/{t} boots by UEFI only", d.name));
            }
        }
        out
    }

    fn grub_bios_devices(&self) -> Vec<String> {
        let mut out = Vec::new();
        if let Some(list) = self.layout.str_at(&["boot", "grub", "debconf", "grub-pc/install_devices"]) {
            for p in list.split([',', ' ']).map(str::trim).filter(|p| !p.is_empty()) {
                if let Some(d) = self.names.src_of_path(p).and_then(|src| self.names.dev(&src)) {
                    out.push(d);
                }
            }
        }
        if out.is_empty() {
            for d in &self.layout.disks {
                let grub = self.layout.device(&d.name).and_then(|v| v.at(&["boot_code", "kind"])).and_then(Value::as_str) == Some("grub2");
                if let (true, Some(t)) = (grub, self.names.mapping.target_of(&d.name)) {
                    out.push(format!("/dev/{t}"));
                }
            }
        }
        out
    }

    /// The directory under EFI/ the source's loader lives in ("debian").  Only
    /// an entry on the layout's own disks can say: NVRAM outlives reinstalls,
    /// and can list other disks' loaders and long-gone systems'.  The entry
    /// that booted comes first, then BootOrder, then the rest; with none of
    /// those, what's on the ESP.
    fn bootloader_id(&self) -> String {
        let dir_of = |f: &str| {
            let parts: Vec<&str> = f.split(['\\', '/']).filter(|p| !p.is_empty()).collect();
            // As the source spells it (BigLinux's EFI/BigLinux): FAT ignores
            // case, but the plan says what the installer did.
            (parts.len() >= 3 && parts[0].eq_ignore_ascii_case("EFI") && !parts[1].eq_ignore_ascii_case("BOOT")).then(|| parts[1].to_string())
        };
        let mut wanted: Vec<String> = self.layout.str_at(&["boot", "efi", "boot_current"]).map(str::to_string).into_iter().collect();
        wanted.extend(self.layout.v.at(&["boot", "efi", "boot_order"]).and_then(Value::as_array).into_iter().flatten().filter_map(|x| x.as_str().map(str::to_string)));
        let rank = |e: &Value| s(e, "number").and_then(|n| wanted.iter().position(|w| *w == n)).unwrap_or(usize::MAX);
        let on_layout = |e: &Value| e.at(&["partition", "device"]).and_then(Value::as_str).is_some_and(|part| self.layout.disks.iter().any(|d| d.parts.iter().any(|p| p.name == part)));
        let mut entries: Vec<&Value> = self.layout.arr(&["boot", "efi", "entries"]).iter().filter(|e| on_layout(e)).collect();
        entries.sort_by_key(|e| rank(e));
        if let Some(id) = entries.iter().find_map(|e| e.get("file").and_then(Value::as_str).and_then(dir_of)) {
            return id;
        }
        let n = self.boot_names();
        let (shim, grub) = (format!("/{}", n.shim), format!("/{}", n.grub));
        for esp in self.layout.arr(&["boot", "esps"]) {
            let files = esp.at(&["contents", "files"]).and_then(Value::as_array).into_iter().flatten();
            for path in files.filter_map(|f| f.get("path").and_then(Value::as_str)) {
                let low = path.to_ascii_lowercase();
                if low.ends_with(&shim) || low.ends_with(&grub) {
                    if let Some(id) = dir_of(path) {
                        return id;
                    }
                }
            }
        }
        "debian".into()
    }

    /// The source's boot entries on the restored disks, created in reverse
    /// BootOrder so that the first ends up first (efibootmgr puts each new
    /// one at the front), each with its optional data.
    pub(crate) fn efi_entries(&mut self) -> Vec<Cmd> {
        let entries = self.layout.arr(&["boot", "efi", "entries"]).to_vec();
        let order: Vec<String> = self.layout.v.at(&["boot", "efi", "boot_order"]).and_then(Value::as_array).into_iter().flatten().filter_map(|x| x.as_str().map(str::to_string)).collect();
        let mut picked: Vec<&Value> = Vec::new();
        for n in &order {
            if let Some(e) = entries.iter().find(|e| s(e, "number").as_deref() == Some(n.as_str())) {
                picked.push(e);
            }
        }
        let mut out = Vec::new();
        let proxmox_sd = self.proxmox_systemd_boot_esps();
        // The (disk, partition, loader)s NVRAM's old copies were deleted for
        // already: a second source entry for the same loader (another
        // kernel command line) mustn't delete the first one made.
        let mut deduped: Vec<(String, u32, String)> = Vec::new();
        for e in picked.into_iter().rev() {
            let desc = s(e, "description").unwrap_or_default();
            let (Some(file), Some(part)) = (s(e, "file"), e.at(&["partition", "device"]).and_then(Value::as_str)) else { continue };
            // proxmox-boot-tool init's bootctl install makes this one (the
            // same partition and file) itself: not made twice.
            if proxmox_sd.iter().any(|d| d == part) && file.replace('/', "\\").trim_start_matches('\\').eq_ignore_ascii_case(&format!("EFI\\systemd\\{}", self.boot_names().systemd_boot)) {
                self.notes.push(format!("UEFI entry {desc:?} ({part}, {file}): proxmox-boot-tool init's bootctl install makes it again; not recreated"));
                continue;
            }
            let Some((disk, num)) = self.layout.disks.iter().find_map(|d| d.parts.iter().find(|p| p.name == part).map(|p| (d.name.clone(), p.number))) else {
                self.notes.push(format!("UEFI entry {desc:?} isn't on the layout's disks; not recreated"));
                continue;
            };
            let Some(t) = self.names.mapping.target_of(&disk) else { continue };
            let key = (t.to_string(), num, crate::load::efidedupe::norm_path(&file));
            if !deduped.contains(&key) {
                out.push(efi_dedupe(&format!("/dev/{t}"), num, &file));
                deduped.push(key);
            }
            let mut a: Vec<String> = ["efibootmgr", "--quiet", "--create", "--disk", &format!("/dev/{t}"), "--part", &num.to_string(), "--label", &desc, "--loader", &file].iter().map(|x| x.to_string()).collect();
            // The entry's optional data (the loader's arguments), as the
            // source had it: UCS-2 text as efibootmgr's --unicode argument,
            // anything else from a file.
            match efi_optional_data(e) {
                Some(OptionalData::Text(text)) => a.extend(["--unicode".to_string(), text]),
                Some(OptionalData::Bytes(b)) => {
                    let num_tok: String = s(e, "number").unwrap_or_default().chars().filter(char::is_ascii_alphanumeric).collect();
                    let f = format!("{RUN_DIR}/efi-optional-data-{num_tok}");
                    let qf = shell_quote(&f);
                    let octal: String = b.iter().map(|c| format!("\\{c:03o}")).collect();
                    out.push(Cmd::new(&["sh", "-c", &format!("printf '{octal}' > {qf}")]).why(format!("UEFI entry {desc:?}: its optional data, {} bytes, as the source had it", b.len())));
                    a.extend(["--append-binary-args".to_string(), f]);
                }
                None => {}
            }
            out.push(Cmd { argv: a, ..Cmd::default() });
        }
        out
    }

    /// refind_linux.conf (boot.refind.linux_conf) is kept as restored, so
    /// every root=UUID= and rd.luks.name=UUID=NAME (or rd.luks.uuid=) in it
    /// must name a filesystem or LUKS container the layout makes with that
    /// UUID; one that doesn't is a problem now, not a boot that can't find
    /// its root.
    fn check_refind_uuids(&mut self) {
        let mut have: Vec<String> = Vec::new();
        for sect in ["disks", "md_arrays", "dm_devices"] {
            for d in self.layout.arr(&["storage", sect]) {
                let mut all = vec![d];
                all.extend(d.get("partitions").and_then(Value::as_array).into_iter().flatten());
                have.extend(all.iter().filter_map(|x| x.at(&["content", "uuid"]).and_then(Value::as_str)).map(str::to_ascii_lowercase));
            }
        }
        let conf = self.layout.str_at(&["boot", "refind", "linux_conf", "path"]).unwrap_or("/boot/refind_linux.conf").to_string();
        let mut missing: Vec<String> = Vec::new();
        for l in self.layout.arr(&["boot", "refind", "linux_conf", "lines"]) {
            for w in s(l, "options").unwrap_or_default().split_whitespace() {
                let u = if let Some(r) = w.strip_prefix("root=") {
                    r.strip_prefix("UUID=").or_else(|| r.strip_prefix("/dev/disk/by-uuid/"))
                } else if let Some(r) = w.strip_prefix("rd.luks.name=") {
                    // rd.luks.name=UUID=NAME, the UUID bare or with UUID=.
                    let r = r.strip_prefix("UUID=").unwrap_or(r);
                    r.split_once('=').map(|(u, _)| u)
                } else {
                    w.strip_prefix("rd.luks.uuid=").map(|r| r.strip_prefix("luks-").unwrap_or(r))
                };
                let Some(u) = u.filter(|u| !u.is_empty()) else { continue };
                let what = format!("{}={u}", w.split('=').next().unwrap_or(w));
                if !have.contains(&u.to_ascii_lowercase()) && !missing.contains(&what) {
                    missing.push(what);
                }
            }
        }
        for m in missing {
            self.problems.push(format!("rEFInd: {conf} has {m}, which no filesystem or LUKS container in the layout has; the restored system couldn't find it at boot"));
        }
    }

    /// A store system's grub.cfg (Guix's boot.store) is kept as restored,
    /// with every generation's kernel and initrd in the store: each `search
    /// --fs-uuid` and `--label` in it, the root each `linux` line names
    /// (1.5.0's root=, older Guix's --root=), each generation's root device
    /// (its boot parameters' root-device) and any root= or --root= in its
    /// kernel arguments must name a file system (or LUKS container) the
    /// layout makes with that UUID or label.  One that doesn't is a problem
    /// now, not a GRUB or initrd that can't find its root at boot.  `search
    /// --file` and a /dev path are fine.  A dump without boot.store
    /// (NixOS's) has nothing to check.
    fn check_store_grub_cfg(&mut self, st: &crate::store::StoreSystem) {
        let Some(store) = self.layout.v.at(&["boot", "store"]).filter(|v| !v.is_null()).cloned() else { return };
        let (mut uuids, mut labels): (Vec<String>, Vec<String>) = (Vec::new(), Vec::new());
        for sect in ["disks", "md_arrays", "dm_devices"] {
            for d in self.layout.arr(&["storage", sect]) {
                let mut all = vec![d];
                all.extend(d.get("partitions").and_then(Value::as_array).into_iter().flatten());
                for c in all.iter().filter_map(|x| x.get("content")) {
                    uuids.extend(s(c, "uuid").map(|u| u.to_ascii_lowercase()));
                    labels.extend(s(c, "label"));
                }
            }
        }
        let name = st.name;
        let cfg = store.at(&["grub_cfg", "path"]).and_then(Value::as_str).unwrap_or("/boot/grub/grub.cfg").to_string();
        let mut missing: Vec<String> = Vec::new();
        let mut miss = |m: String| {
            if !missing.contains(&m) {
                missing.push(m);
            }
        };
        for x in store.at(&["grub_cfg", "searches"]).and_then(Value::as_array).into_iter().flatten() {
            let (Some(k), Some(v)) = (s(x, "kind"), s(x, "value")) else { continue };
            let found = match k.as_str() {
                "fs-uuid" => uuids.contains(&v.to_ascii_lowercase()),
                "label" => labels.contains(&v),
                _ => true,
            };
            if !found {
                miss(format!("{cfg} searches --{} {v}, which no file system in the layout has; the restored GRUB couldn't find it", if k == "fs-uuid" { "fs-uuid" } else { "label" }));
            }
        }
        let root_ok = |r: &str| r.starts_with('/') || uuids.contains(&r.to_ascii_lowercase()) || labels.iter().any(|l| l == r);
        for r in store.at(&["grub_cfg", "roots"]).and_then(Value::as_array).into_iter().flatten().filter_map(Value::as_str) {
            if !root_ok(r) {
                miss(format!("{cfg} boots with root {r}, which no file system in the layout has as its UUID or label; the restored initrd couldn't find its root"));
            }
        }
        for g in store.get("generations").and_then(Value::as_array).into_iter().flatten() {
            let n = g.get("number").and_then(Value::as_u64).unwrap_or(0);
            for a in g.get("kernel_arguments").and_then(Value::as_array).into_iter().flatten().filter_map(Value::as_str) {
                if let Some(r) = crate::store::root_arg(a).filter(|r| !root_ok(r)) {
                    miss(format!("generation {n} boots with root {r}, which no file system in the layout has as its UUID or label; its initrd couldn't find its root"));
                }
            }
            // Its root device (1.5.0's only word on the root: grub.cfg's
            // root= is made from it).
            let (k, v) = (g.at(&["root_device", "kind"]).and_then(Value::as_str), g.at(&["root_device", "value"]).and_then(Value::as_str));
            if let (Some(k), Some(v)) = (k, v) {
                let ok = match k {
                    "uuid" => uuids.contains(&v.to_ascii_lowercase()),
                    "label" => labels.iter().any(|l| l == v),
                    _ => root_ok(v),
                };
                if !ok {
                    let what = match k {
                        "label" => format!("the label {v}"),
                        "uuid" => format!("UUID {v}"),
                        _ => v.to_string(),
                    };
                    miss(format!("generation {n}'s root device is {what}, which no file system in the layout has; its initrd couldn't find its root"));
                }
            }
        }
        for m in missing {
            self.problems.push(format!("{name}: {m}"));
        }
    }

    fn close(&mut self) -> Vec<Cmd> {
        if let Some(fam) = self.fam {
            return self.other_close(fam);
        }
        let t = &self.opts.target;
        let exe = std::env::current_exe().map(|p| p.to_string_lossy().into_owned()).unwrap_or_else(|_| "bko-holt".into());
        // Where the mount stage mounts nothing at the target itself (Ubuntu
        // Core: the four partitions under run/mnt), `umount --recursive`
        // of it fails, "not mounted": umount-tree (umounttree.rs) takes
        // each mount under it off instead, read from mountinfo.
        // Bug: when nothing is mounted at target t itself (Ubuntu Core mounts
        // partitions under t/run/mnt/...), close() used umount-tree locally, but
        // its cross-namespace sweep matched only mount points equal to or ending
        // in t. Submounts like t/run/mnt/data in udevd or snapd namespaces were
        // skipped, leaving LUKS devices busy so cryptsetup close failed. When
        // !rooted, match submounts under t/ too, sorted deepest first.
        let rooted = self.mount_points.iter().any(|p| p == t);
        let mut out = vec![if rooted {
            Cmd::new(&["umount", "--recursive", t])
        } else {
            Cmd::new(&[exe.as_str(), "umount-tree", t]).why(format!("nothing is mounted at {t} itself: every mount under it, read from /proc/self/mountinfo, each before the one it's on"))
        }];
        // A service that started while the target was mounted (systemd's
        // udevd, logind, timesyncd: PrivateMounts and the like) has its own
        // copy of those mounts, which umount here doesn't always reach, and
        // which keeps pools, LUKS and LVM volumes busy.  Unmount it there.
        // So too in a namespace no process is in, kept by a bind mount of
        // its nsfs file: snapd keeps each snap's at /run/snapd/ns/SNAP.mnt,
        // and a snap refresh's hooks make one while load works.  The boot
        // stage's rbind of /run puts snapd's private /run/snapd/ns under
        // the target there, which umount's propagation can't take away.
        // A snap's namespace has the target under /var/lib/snapd/hostfs as
        // well: every mount point whose path ends in the target's goes.
        // With nothing mounted at the target itself, so too every mount
        // under such a path, deepest first.
        let qt = shell_quote(t);
        let ns_script = if qt == *t && !t.bytes().any(|b| matches!(b, b'.' | b'^')) {
            let at = if rooted {
                format!("awk -v t={t} '{{ m = $5; if (m == t || length(m) > length(t) && substr(m, length(m) - length(t) + 1) == t) print m }}'")
            } else {
                format!("awk -v t={t} '{{ m = $5; i = index(m, t); if (i > 0 && (i + length(t) - 1 == length(m) || substr(m, i + length(t), 1) == \"/\")) print m }}' | LC_ALL=C sort -r")
            };
            format!(
                "me=$(readlink /proc/self/ns/mnt); seen=\" $me \"; \
                 at() {{ {at}; }}; \
                 for f in /proc/[0-9]*/mountinfo; do grep -q '{t}[ /]' \"$f\" 2>/dev/null || continue; \
                 p=${{f#/proc/}}; p=${{p%/mountinfo}}; ns=$(readlink /proc/$p/ns/mnt) || continue; \
                 case $seen in *\" $ns \"*) continue;; esac; seen=\"$seen$ns \"; \
                 echo \"unmounting {t} in $ns (pid $p, $(cat /proc/$p/comm))\"; \
                 for m in $(at <\"$f\"); do nsenter -t \"$p\" -m umount --recursive \"$m\" || true; done; done; \
                 awk '{{ for (i = 7; i < NF; i++) if ($i == \"-\") {{ if ($(i + 1) == \"nsfs\" && $4 ~ /^mnt:\\[/) print $4, $5; break }} }}' /proc/self/mountinfo | \
                 while read -r ns b; do case $seen in *\" $ns \"*) continue;; esac; seen=\"$seen$ns \"; \
                 ms=$(nsenter --mount=\"$b\" cat /proc/self/mountinfo | at); [ -n \"$ms\" ] || continue; \
                 echo \"unmounting {t} in $ns (bound at $b)\"; \
                 for m in $ms; do nsenter --mount=\"$b\" umount --recursive \"$m\" || true; done; done"
            )
        } else {
            let at = if rooted {
                "T=\"$t\" awk 'BEGIN { t = ENVIRON[\"T\"] } { m = $5; if (m == t || length(m) > length(t) && substr(m, length(m) - length(t) + 1) == t) print m }'"
            } else {
                "T=\"$t\" awk 'BEGIN { t = ENVIRON[\"T\"] } { m = $5; i = index(m, t); if (i > 0 && (i + length(t) - 1 == length(m) || substr(m, i + length(t), 1) == \"/\")) print m }' | LC_ALL=C sort -r"
            };
            format!(
                "t={qt}; me=$(readlink /proc/self/ns/mnt); seen=\" $me \"; \
                 at() {{ {at}; }}; \
                 for f in /proc/[0-9]*/mountinfo; do grep -Fq -- \"$t\" \"$f\" 2>/dev/null || continue; \
                 p=${{f#/proc/}}; p=${{p%/mountinfo}}; ns=$(readlink /proc/$p/ns/mnt) || continue; \
                 case $seen in *\" $ns \"*) continue;; esac; seen=\"$seen$ns \"; \
                 echo \"unmounting $t in $ns (pid $p, $(cat /proc/$p/comm))\"; \
                 at <\"$f\" | while IFS= read -r m; do nsenter -t \"$p\" -m umount --recursive \"$m\" || true; done; done; \
                 awk '{{ for (i = 7; i < NF; i++) if ($i == \"-\") {{ if ($(i + 1) == \"nsfs\" && $4 ~ /^mnt:\\[/) print $4, $5; break }} }}' /proc/self/mountinfo | \
                 while read -r ns b; do case $seen in *\" $ns \"*) continue;; esac; seen=\"$seen$ns \"; \
                 ms=$(nsenter --mount=\"$b\" cat /proc/self/mountinfo | at); [ -n \"$ms\" ] || continue; \
                 echo \"unmounting $t in $ns (bound at $b)\"; \
                 printf '%s\\n' \"$ms\" | while IFS= read -r m; do nsenter --mount=\"$b\" umount --recursive \"$m\" || true; done; done"
            )
        };
        out.push(Cmd::new(&["sh", "-c", &ns_script]).why("other mount namespaces' copies of the target's mounts"));
        out.extend(self.closers.iter().rev().cloned());
        // Stopped, each md member gets back the source's logical block size
        // field: the rescue system's kernel may have written one the
        // source's kernel takes for non-zero padding, and refuses.
        for md in self.layout.arr(&["storage", "md_arrays"]).to_vec() {
            for m in md.at(&["md", "members"]).and_then(Value::as_array).into_iter().flatten() {
                let Some(src) = s(m, "device") else { continue };
                let Some(c) = self.layout.content(&src).cloned() else { continue };
                if !s(&c, "metadata").is_some_and(|v| v.starts_with('1')) {
                    continue;
                }
                let (Some(dev), Some(off)) = (self.names.dev(&src), num(&c, "super_offset_bytes")) else { continue };
                let lbs = num(&c, "logical_block_size").unwrap_or(0);
                out.push(Cmd::new(&[exe.as_str(), "md-lbs", dev.as_str(), &off.to_string(), &lbs.to_string()]).why(format!("{src}: the md superblock as the source's kernel reads it")));
            }
        }
        out
    }
}

/// `bko-holt efi-dedupe DEV PART LOADER` (efidedupe.rs), just before
/// `efibootmgr --create` makes that entry: NVRAM's entries for the same
/// partition and loader, which an earlier restore onto this machine made,
/// deleted, so that each restore doesn't add another copy.
pub(crate) fn efi_dedupe(dev: &str, part: u32, loader: &str) -> Cmd {
    let exe = std::env::current_exe().map(|p| p.to_string_lossy().into_owned()).unwrap_or_else(|_| "bko-holt".into());
    Cmd::new(&[exe.as_str(), "efi-dedupe", dev, &part.to_string(), loader]).why("NVRAM's entries for this partition and loader, from an earlier restore here, deleted").warns()
}

/// A UEFI entry's optional data (the bytes after its device path).
#[derive(Debug, Clone, PartialEq)]
pub(crate) enum OptionalData {
    /// UCS-2 text, as `efibootmgr --unicode` writes it (no final NUL).
    Text(String),
    Bytes(Vec<u8>),
}

/// The LUKS2 content C's first keyslot that no token names (a passphrase's
/// or a key file's), or Null when every one is a token's.
pub(crate) fn luks_passphrase_keyslot(c: &Value) -> Value {
    let bound: Vec<String> = c
        .get("tokens")
        .and_then(Value::as_array)
        .into_iter()
        .flatten()
        .flat_map(|t| t.get("keyslots").and_then(Value::as_array).cloned().unwrap_or_default())
        .filter_map(|k| k.as_str().map(str::to_string).or_else(|| k.as_u64().map(|n| n.to_string())))
        .collect();
    c.get("keyslots").and_then(Value::as_array).into_iter().flatten().find(|k| s(k, "id").is_none_or(|id| !bound.contains(&id))).cloned().unwrap_or(Value::Null)
}

/// The dumped entry E's optional data, from the tail of its load option
/// (optional_data_bytes of load_option_hex): None when it has none.
pub(crate) fn efi_optional_data(e: &Value) -> Option<OptionalData> {
    let n = num(e, "optional_data_bytes").filter(|&n| n > 0)? as usize;
    let hex = s(e, "load_option_hex")?;
    let raw: Vec<u8> = (0..hex.len() / 2).filter_map(|i| u8::from_str_radix(hex.get(2 * i..2 * i + 2)?, 16).ok()).collect();
    let b = raw.get(raw.len().checked_sub(n)?..)?.to_vec();
    if b.len() % 2 == 0 {
        let mut units: Vec<u16> = b.chunks_exact(2).map(|c| u16::from_le_bytes([c[0], c[1]])).collect();
        while units.last() == Some(&0) {
            units.pop();
        }
        if let Ok(t) = String::from_utf16(&units) {
            if !t.is_empty() && t.chars().all(|c| !c.is_control()) {
                return Some(OptionalData::Text(t));
            }
        }
    }
    Some(OptionalData::Bytes(b))
}

// ---- boot: Slackware ----------------------------------------------------

/// A plain kernel device path (/dev/vda2, /dev/nvme0n1p2, /dev/md0): what
/// Slackware's lilo.conf, elilo.conf and fstab name, and what changes with
/// the hardware.  Not /dev/mapper/, /dev/disk/by-*/ or /dev/VG/LV.
fn kernel_dev_path(p: &str) -> bool {
    let p = md_name(p);
    p.strip_prefix("/dev/").is_some_and(|t| !t.is_empty() && !t.contains('/'))
}

/// /dev/md/N as /dev/mdN: the same array, by mdadm's name or the
/// kernel's (load assembles it as /dev/md/N; it boots as /dev/mdN).
fn md_name(p: &str) -> String {
    match p.strip_prefix("/dev/md/") {
        Some(n) if !n.is_empty() && n.bytes().all(|c| c.is_ascii_digit()) => format!("/dev/md{n}"),
        _ => p.to_string(),
    }
}

/// The name of the lilo.conf copy lilo_cmds runs lilo on: the config's
/// own name with this added, beside it.
const LILO_COPY_SUFFIX: &str = ".bko-holt";

/// lilo 24.2's longest kernel command line (lilo.h's COMMAND_LINE_SIZE
/// 512, less its NUL): a longer append= it refuses, a longer line it cuts.
const LILO_CMDLINE_MAX: usize = 511;

/// Whether lilo writes this root= as a device number (bsect.c's
/// bsect_common): any but `current`, /dev/mapper/..., LABEL= and UUID=,
/// which go through as text.  Only /dev/ paths are rewritten here.
fn lilo_numbers_root(root: &str) -> bool {
    root.starts_with("/dev/") && !root.starts_with("/dev/mapper/")
}

/// boot.lilo's items as written (lilo_list's `key` or `key=value`) back
/// into lilo.conf items.
fn lilo_dump_items(v: &Value) -> Option<Vec<crate::boot::LiloItem>> {
    let a = v.get("options").and_then(Value::as_array)?;
    Some(
        a.iter()
            .filter_map(Value::as_str)
            .map(|o| match o.split_once('=') {
                Some((k, v)) => (k.to_string(), Some(v.to_string())),
                None => (o.to_string(), None),
            })
            .collect(),
    )
}

/// lilo.conf as boot.lilo recorded it (its global items, then each image=
/// or other= with its items, in order), written out again for lilo -C,
/// with `root=DEV` at the end of the append= of each image whose root=
/// (its own, else the global one) lilo would turn into a device number.
/// lilo writes such a root= as the device's major:minor under the rescue
/// kernel, and virtio_blk's major is dynamic (254 under the rescue kernel,
/// 253 under Slackware 15's huge kernel), so a kernel with no initrd would
/// look for another device; the kernel takes the last root= it sees and
/// finds /dev/NAME by itself.  The image gets its own append= with it: the
/// one it has, else the global one it inherits (an image's append= replaces
/// the global one in lilo), so other images, which may have other roots,
/// keep theirs.  Images with literal=, or a root= already in append= or
/// addappend=, are left as they are.  Ok(None) when no image needs it, else
/// the copy's text and each `LABEL: root=DEV` added; Err when the copy
/// couldn't say what the user's file says, or lilo would refuse it.
fn lilo_root_copy(lilo: &Value) -> Result<Option<(String, Vec<String>)>, String> {
    use crate::boot::lilo_get;
    let conf = s(lilo, "conf").unwrap_or_else(|| crate::boot::LILO_CONF.into());
    let Some(global) = lilo_dump_items(lilo) else {
        return Err(format!("the dump's boot.lilo doesn't keep {conf}'s items (options), so bko-holt can't write the copy of it lilo runs on"));
    };
    let g = |k: &str| lilo_get(&global, k).map(str::to_string);
    let flag = |its: &[crate::boot::LiloItem], k: &str| its.iter().chain(global.iter()).any(|(x, _)| x == k);
    let mut sections: Vec<(String, String, Vec<crate::boot::LiloItem>)> = Vec::new();
    let mut added: Vec<String> = Vec::new();
    for img in lilo.get("images").and_then(Value::as_array).into_iter().flatten() {
        let kind = s(img, "kind").unwrap_or_else(|| "image".into());
        let path = s(img, "path").unwrap_or_default();
        let Some(mut its) = lilo_dump_items(img) else {
            return Err(format!("the dump's boot.lilo doesn't keep the items of {conf}'s {kind}={path} (options), so bko-holt can't write the copy of it lilo runs on"));
        };
        let root = lilo_get(&its, "root").map(str::to_string).or_else(|| g("root"));
        let has_root = |t: Option<&str>| t.unwrap_or("").split_whitespace().any(|w| w.starts_with("root="));
        if let Some(root) = root.filter(|r| kind == "image" && lilo_numbers_root(r) && lilo_get(&its, "literal").is_none()) {
            let own = its.iter().rposition(|(k, _)| k == "append");
            let append = match own {
                Some(i) => its[i].1.clone(),
                None => g("append"),
            };
            if !has_root(append.as_deref()) && !has_root(lilo_get(&its, "addappend")) {
                let dev = md_name(&root);
                let new = match append.as_deref() {
                    Some(a) if !a.trim().is_empty() => format!("{a} root={dev}"),
                    _ => format!("root={dev}"),
                };
                // The line lilo makes (bsect_common): [ro ][rw ]root=HEX
                // [ramdisk=N ]APPEND [ADDAPPEND], less a last space; the
                // device number at its longest, 8 hex digits.
                let mut line = String::new();
                if flag(&its, "read-only") {
                    line += "ro ";
                }
                if flag(&its, "read-write") {
                    line += "rw ";
                }
                line += "root=ffffffff ";
                if let Some(r) = lilo_get(&its, "ramdisk").map(str::to_string).or_else(|| g("ramdisk")) {
                    line += &format!("ramdisk={r} ");
                }
                line += &new;
                line.push(' ');
                line += lilo_get(&its, "addappend").unwrap_or("");
                let n = line.strip_suffix(' ').unwrap_or(&line).len();
                let label = lilo_get(&its, "label").unwrap_or(&path).to_string();
                if n > LILO_CMDLINE_MAX {
                    return Err(format!(
                        "{conf}'s image {label} would have a kernel command line of up to {n} characters with root={dev} added to its append=, over lilo's {LILO_CMDLINE_MAX}; bko-holt adds it because lilo writes root={root} as the device's number under the rescue kernel, which the restored kernel may number otherwise"
                    ));
                }
                match own {
                    Some(i) => its[i].1 = Some(new),
                    None => its.push(("append".into(), Some(new))),
                }
                added.push(format!("{label}: root={dev}"));
            }
        }
        sections.push((kind, path, its));
    }
    if added.is_empty() {
        return Ok(None);
    }
    // lilo_items reads no escapes or variables, so an item with a quote, a
    // backslash or a $ (lilo expands $VAR) may not be what lilo read; and
    // password="" keeps the passwords in CONF.shs, named after the config
    // lilo runs on, which the copy's name would miss.
    let all = global.iter().chain(sections.iter().flat_map(|(_, _, its)| its.iter()));
    for (k, v) in all {
        if k == "password" && v.as_deref() == Some("") {
            return Err(format!("{conf} has password=\"\", whose passwords lilo keeps in {conf}.shs, beside the config it runs on; bko-holt runs lilo on a copy of {conf} (to give the kernel root= by name) and doesn't support that"));
        }
    }
    let odd = |t: &str| t.contains(['"', '\\', '$']);
    let mut bad = global.iter().chain(sections.iter().flat_map(|(_, _, its)| its.iter())).map(|(k, v)| (k.as_str(), v.as_deref())).chain(sections.iter().map(|(k, p, _)| (k.as_str(), Some(p.as_str()))));
    if let Some((k, v)) = bad.find(|(k, v)| odd(k) || v.is_some_and(odd)) {
        return Err(format!(
            "{conf}'s {k}{} has a quote, backslash or $, which bko-holt can't write into the copy of {conf} it runs lilo on (to give the kernel root= by name) as lilo reads it",
            v.map(|v| format!("={v}")).unwrap_or_default()
        ));
    }
    let item = |(k, v): &crate::boot::LiloItem| match v {
        Some(v) => format!("{k}=\"{v}\"\n"),
        None => format!("{k}\n"),
    };
    let mut text = format!("# bko-holt load: {conf} with root= by name added to append= ({}); lilo is run on this copy, then it is removed\n", added.join(", "));
    text.extend(global.iter().map(item));
    for (kind, path, its) in &sections {
        text += &format!("{kind}=\"{path}\"\n");
        text.extend(its.iter().map(|i| format!("  {}", item(i))));
    }
    Ok(Some((text, added)))
}

/// geninitrd_steps' result: (image, the commands remaking it) for each
/// image remade, and notes on those that stay as restored.
type GeninitrdSteps = (Vec<(String, Vec<Cmd>)>, Vec<String>);

impl Builder<'_> {
    /// Slackware's mkinitrd step, as the dump read it
    /// (boot.initramfs.mkinitrd): the source's own command
    /// (/boot/initrd-tree/command_line) with -k for its kernels, else
    /// `mkinitrd -F` from /etc/mkinitrd.conf; Err with why neither can run.
    fn mkinitrd_cmd(&self, t: &str) -> Result<Cmd, String> {
        let m = self.layout.v.at(&["boot", "initramfs", "mkinitrd"]).cloned().unwrap_or(Value::Null);
        let kernels: Vec<String> = m.get("kernels").and_then(Value::as_array).into_iter().flatten().filter_map(|k| k.as_str().map(str::to_string)).collect();
        let output = s(&m, "output").unwrap_or_else(|| crate::boot::MKINITRD_OUTPUT.into());
        if kernels.is_empty() {
            return Err(format!("no kernel in /lib/modules to make it for, so the restored {output} stays"));
        }
        let k = kernels.join(", ");
        let mut argv = vec!["chroot".to_string(), t.to_string()];
        if let Some(r) = s(&m, "command_line").and_then(|c| crate::boot::mkinitrd_replay(&c, &kernels, &output)) {
            argv.extend(r);
            return Ok(Cmd { argv, ..Cmd::default() }.why(format!("the initrd, by the source's own mkinitrd command ({}), for kernel {k}", crate::boot::MKINITRD_COMMAND_LINE)));
        }
        if s(&m, "conf").is_some() {
            argv.extend(["mkinitrd".to_string(), "-F".into(), "-k".into(), kernels.join(":")]);
            return Ok(Cmd { argv, ..Cmd::default() }.why(format!("the initrd, as {} says (mkinitrd -F), for kernel {k}", crate::boot::MKINITRD_CONF)));
        }
        Err(format!("mkinitrd's command can't be found (no {} or {}), so the restored {output} stays", crate::boot::MKINITRD_COMMAND_LINE, crate::boot::MKINITRD_CONF))
    }

    /// -current's geninitrd step, as the dump read it
    /// (boot.initramfs.geninitrd): each image geninitrd made
    /// (/boot/initrd-VERSION.img, for a kernel /boot and /lib/modules still
    /// have) made again by what its setup.01.mkinitrd runs for that kernel,
    /// in the chroot: `mkinitrd -F /etc/mkinitrd.conf -k V -o IMAGE` with a
    /// mkinitrd.conf, else mkinitrd_command_generator.sh's command for this
    /// machine's root and storage drivers, with setup.01.mkinitrd's `-L -R
    /// -o IMAGE`, run.  Only that: not geninitrd itself, whose pre- and
    /// post-install scripts (DKMS builds, lilo), orphan removal and
    /// update-grub aren't the restore's.  The links to the images
    /// (initrd-generic.img, initrd.gz) came back with /boot and still point
    /// at them.  After each, /boot/initrd-tree (which mkinitrd builds) is
    /// removed as setup.01.mkinitrd removes it, when AUTO_REMOVE_INITRD_TREE
    /// is true (its default) and the source had none.  Ok((image, commands)
    /// for each remade, notes on the images that stay), or Err(why) when
    /// every restored image stays.
    fn geninitrd_steps(&self, t: &str) -> Result<GeninitrdSteps, String> {
        let g = self.layout.v.at(&["boot", "initramfs", "geninitrd"]).cloned().unwrap_or(Value::Null);
        let runs = s(&g, "runs").unwrap_or_default();
        let setup = crate::boot::GENINITRD_SETUP;
        match runs.as_str() {
            "mkinitrd" | "mkinitrd_command_generator.sh" => {}
            "override" => {
                return Err(format!(
                    "geninitrd runs GENINITRD_OVERRIDE_SCRIPT={} in place of {setup}, which bko-holt load doesn't run, so the restored initrd images stay",
                    s(&g, "override_script").unwrap_or_default()
                ))
            }
            other => return Err(format!("geninitrd's GENERATOR={} ({setup} runs {other}) isn't one bko-holt load runs (only mkinitrd), so the restored initrd images stay", s(&g, "generator").unwrap_or_default())),
        }
        // A dump before these were read: setup.01.mkinitrd's default, and
        // no tree on the source (geninitrd's default leaves none).
        let remove_tree = g.get("auto_remove_initrd_tree").and_then(Value::as_bool) != Some(false) && g.get("initrd_tree").and_then(Value::as_bool) != Some(true);
        let mut steps = Vec::new();
        let mut notes = Vec::new();
        for img in g.get("images").and_then(Value::as_array).into_iter().flatten() {
            let (Some(file), Some(v)) = (s(img, "file"), s(img, "version")) else { continue };
            if !crate::boot::kernel_version_word(&v) || file != format!("/boot/initrd-{v}.img") {
                notes.push(format!("{file} isn't named as geninitrd names its images (/boot/initrd-VERSION.img), so it stays as restored"));
                continue;
            }
            if s(img, "kernel").is_none() {
                notes.push(format!("{file} is for kernel {v}, which has no /boot/vmlinuz-{v}, so it stays as restored"));
                continue;
            }
            if img.get("modules_installed").and_then(Value::as_bool) != Some(true) {
                notes.push(format!("{file} is for kernel {v}, which has no /lib/modules/{v}, so it stays as restored"));
                continue;
            }
            let cmd = if runs == "mkinitrd" {
                Cmd::new(&["chroot", t, "/sbin/mkinitrd", "-F", crate::boot::MKINITRD_CONF, "-k", &v, "-o", &file])
                    .why(format!("the initrd for kernel {v}, as geninitrd makes it ({setup}: GENERATOR=mkinitrd with {})", crate::boot::MKINITRD_CONF))
            } else {
                let generator = crate::boot::MKINITRD_GENERATOR;
                // `file` is /boot/initrd-V.img with V a kernel_version_word: no
                // quote or $ to escape.
                let line = format!("set -o pipefail; {generator} -k {v} -a \"-L -R -o {file}\" | bash");
                Cmd::new(&["chroot", t, "/bin/bash", "-c", &line]).why(format!(
                    "the initrd for kernel {v}, as geninitrd makes it ({setup}: the mkinitrd command {generator} prints for this machine's root and storage drivers, with -L -R -o {file}, run)"
                ))
            };
            let mut cmds = vec![cmd];
            if remove_tree {
                let tree = crate::boot::INITRD_TREE;
                cmds.push(Cmd::new(&["rm", "-rf", &format!("{t}{tree}")]).why(format!("{tree}, which mkinitrd built, removed as {setup} removes it (AUTO_REMOVE_INITRD_TREE=true; the source had none)")));
            }
            steps.push((file, cmds));
        }
        if steps.is_empty() && notes.is_empty() {
            return Err("geninitrd's dump lists no image, so nothing is made again".into());
        }
        Ok((steps, notes))
    }

    /// Why the source's / is on dm-crypt, or None: a crypt target anywhere
    /// in the stack under it (fstab's / by the device it resolves to, else
    /// the mount's), through LVM, md and other dm layers, which
    /// mkinitrd_command_generator.sh would find and give mkinitrd -C for
    /// (Slackware's README_CRYPT LVM on LUKS, /dev/cryptvg/root, among
    /// them); or, when neither names a device, fstab's / on /dev/mapper.
    fn slackware_root_crypt(&self) -> Option<String> {
        let fstab = self.layout.arr(&["storage", "fstab"]).iter().find(|e| s(e, "file").as_deref() == Some("/"));
        let spec = fstab.and_then(|e| s(e, "spec")).unwrap_or_default();
        let dev = fstab
            .and_then(|e| s(e, "resolves_to"))
            .or_else(|| self.layout.arr(&["storage", "mounts"]).iter().find(|m| s(m, "target").as_deref() == Some("/")).and_then(|m| s(m, "device")));
        let Some(dev) = dev else {
            return spec.starts_with("/dev/mapper/").then(|| format!("fstab's / is {spec}, on /dev/mapper"));
        };
        let stack: Vec<&Value> = self.layout.arr(&["storage", "dm_devices"]).iter().chain(self.layout.arr(&["storage", "md_arrays"])).collect();
        let (mut todo, mut seen) = (vec![dev.clone()], Vec::new());
        while let Some(n) = todo.pop() {
            if seen.contains(&n) {
                continue;
            }
            seen.push(n.clone());
            let Some(d) = stack.iter().find(|d| s(d, "name").as_deref() == Some(n.as_str())) else { continue };
            if d.at(&["dm", "target"]).and_then(Value::as_str) == Some("crypt") {
                let name = d.at(&["dm", "dm_name"]).and_then(Value::as_str).unwrap_or(&n);
                return Some(format!("fstab's / ({spec}) is on dm-crypt {name}"));
            }
            todo.extend(d.get("slaves").and_then(Value::as_array).into_iter().flatten().filter_map(|x| x.as_str().map(str::to_string)));
        }
        None
    }

    /// The initrd images the Slackware initrd step makes again (mkinitrd's
    /// output, or geninitrd's images), for ELILO's copies on the ESP.
    fn slackware_remade(&self, t: &str) -> Vec<String> {
        match self.layout.str_at(&["boot", "initramfs", "generator"]) {
            Some("mkinitrd") if self.mkinitrd_cmd(t).is_ok() => vec![self.layout.str_at(&["boot", "initramfs", "mkinitrd", "output"]).unwrap_or(crate::boot::MKINITRD_OUTPUT).to_string()],
            Some("geninitrd") => self.geninitrd_steps(t).map(|(st, _)| st.into_iter().map(|(f, _)| f).collect()).unwrap_or_default(),
            _ => Vec::new(),
        }
    }

    /// Slackware's initrd step (docs/design/slackware.md): mkinitrd's image
    /// remade (mkinitrd_cmd), -current's geninitrd images remade
    /// (geninitrd_steps), or nothing when there is none (the huge kernel);
    /// any other generator is refused.
    fn slackware_initramfs(&mut self, t: &str) -> Vec<Cmd> {
        let generator = self.layout.str_at(&["boot", "initramfs", "generator"]).unwrap_or("unknown").to_string();
        let root_crypt = self.slackware_root_crypt();
        match generator.as_str() {
            "none" => self.notes.push("Slackware: no initrd (no loader configuration names one, and there is no /boot/initrd.gz): nothing is made, and the kernel boots without one, as on the source".into()),
            // LUKS (mkinitrd -C, or a root on /dev/mapper) is out of
            // scope (docs/design/slackware.md): refused, not replayed.
            "mkinitrd" if self.layout.str_at(&["boot", "initramfs", "mkinitrd", "luksdev"]).is_some() || self.layout.str_at(&["boot", "initramfs", "mkinitrd", "rootdev"]).is_some_and(|r| r.starts_with("/dev/mapper/")) || root_crypt.is_some() => {
                let m = |k: &str| self.layout.str_at(&["boot", "initramfs", "mkinitrd", k]).map(str::to_string);
                let why = match (m("luksdev"), m("rootdev")) {
                    (Some(l), r) => format!("-C {l}, root {}", r.unwrap_or_default()),
                    (None, Some(r)) if r.starts_with("/dev/mapper/") => format!("root {r}"),
                    _ => root_crypt.clone().unwrap_or_default(),
                };
                self.problems.push(format!("Slackware: mkinitrd's initrd unlocks LUKS ({why}); bko-holt load doesn't support Slackware on LUKS in this release"));
            }
            "mkinitrd" => match self.mkinitrd_cmd(t) {
                Ok(c) => return vec![c],
                Err(why) => self.notes.push(format!("Slackware: {why}")),
            },
            // LUKS again: mkinitrd.conf's LUKSDEV, or a root that the
            // command generator would find on dm-crypt.
            "geninitrd" if self.layout.str_at(&["boot", "initramfs", "geninitrd", "luksdev"]).is_some() || root_crypt.is_some() => {
                let luks = self.layout.str_at(&["boot", "initramfs", "geninitrd", "luksdev"]).map(|l| format!("{} has LUKSDEV={l}", crate::boot::MKINITRD_CONF)).or(root_crypt).unwrap_or_default();
                self.problems.push(format!("Slackware: geninitrd's initrd unlocks LUKS ({luks}); bko-holt load doesn't support Slackware on LUKS in this release"));
            }
            "geninitrd" => match self.geninitrd_steps(t) {
                Ok((steps, notes)) => {
                    self.notes.extend(notes.into_iter().map(|n| format!("Slackware: {n}")));
                    return steps.into_iter().flat_map(|(_, c)| c).collect();
                }
                Err(why) => self.notes.push(format!("Slackware: {why}")),
            },
            other => self.problems.push(format!("Slackware: initramfs generator {other} isn't one bko-holt load can run (only mkinitrd, geninitrd, or none)")),
        }
        Vec::new()
    }

    /// Decision 5: Slackware's kernel finds / by the root= its loader gives
    /// it (no initrd to resolve a UUID), and fstab names its filesystems by
    /// device name.  Every /dev/NAME in lilo.conf (boot=, disk=, root=,
    /// root= in append=), in each elilo.conf, in mkinitrd's -r and in fstab
    /// (those on the layout's disks) must be the same name here: a note when it is, a
    /// problem when the target's disk is named otherwise, since load
    /// doesn't rewrite them.
    fn slackware_device_names(&mut self) {
        let mut refs: Vec<(String, String)> = Vec::new();
        let mut push = |what: String, p: &str| {
            if kernel_dev_path(p) && !refs.iter().any(|(w, q)| q == p && *w == what) {
                refs.push((what, p.to_string()));
            }
        };
        let mut confs: Vec<(String, Value)> = Vec::new();
        if let Some(l) = self.layout.v.at(&["boot", "lilo"]).filter(|v| !v.is_null()) {
            confs.push((s(l, "conf").unwrap_or_else(|| crate::boot::LILO_CONF.into()), l.clone()));
        }
        for c in self.layout.arr(&["boot", "elilo", "configs"]) {
            confs.push((s(c, "conf").unwrap_or_default(), c.clone()));
        }
        for (conf, c) in &confs {
            let mut words = |v: &Value| {
                for k in ["boot", "root"] {
                    if let Some(p) = s(v, k) {
                        push(format!("{conf}'s {k}="), &p);
                    }
                }
                for k in ["append", "addappend"] {
                    for w in s(v, k).unwrap_or_default().split_whitespace() {
                        if let Some(p) = w.strip_prefix("root=") {
                            push(format!("{conf}'s root= in {k}="), p);
                        }
                    }
                }
            };
            words(c);
            for img in c.get("images").and_then(Value::as_array).into_iter().flatten() {
                words(img);
            }
            for o in c.get("options").and_then(Value::as_array).into_iter().flatten().filter_map(Value::as_str) {
                if let Some(p) = o.strip_prefix("disk=") {
                    push(format!("{conf}'s disk="), p);
                }
            }
        }
        // The root the initrd mounts (mkinitrd's -r) and the swap it
        // resumes from (-h): made again with them.
        if let Some(p) = self.layout.str_at(&["boot", "initramfs", "mkinitrd", "rootdev"]) {
            push("mkinitrd's -r (the initrd's root)".into(), p);
        }
        if let Some(p) = self.layout.str_at(&["boot", "initramfs", "mkinitrd", "resumedev"]) {
            push("mkinitrd's -h (the initrd's resume device)".into(), p);
        }
        // -current's mkinitrd.conf, when geninitrd runs mkinitrd -F with it.
        if let Some(p) = self.layout.str_at(&["boot", "initramfs", "geninitrd", "rootdev"]) {
            push(format!("{}'s ROOTDEV (the initrd's root)", crate::boot::MKINITRD_CONF), p);
        }
        if let Some(p) = self.layout.str_at(&["boot", "initramfs", "geninitrd", "resumedev"]) {
            push(format!("{}'s RESUMEDEV (the initrd's resume device)", crate::boot::MKINITRD_CONF), p);
        }
        // A noauto line on a device that isn't one of the source's disks
        // or partitions (Slackware's stock /dev/fd0 /mnt/floppy, a CD
        // drive) isn't mounted at boot and isn't restored: the line is kept
        // as it is, and the mount stage notes it.  A nofail line still
        // mounts at boot, so it is checked like any other.
        for e in self.layout.arr(&["storage", "fstab"]) {
            if let (Some(spec), Some(_)) = (s(e, "spec"), s(e, "resolves_to")) {
                let noauto = s(e, "options").unwrap_or_default().split(',').any(|o| o == "noauto");
                if noauto && self.names.src_of_path(&md_name(&spec)).or_else(|| self.names.src_of_path(&spec)).is_none() {
                    continue;
                }
                push(format!("fstab's {}", s(e, "file").unwrap_or_default()), &spec);
            }
        }
        // How the restored system finds / by a name: the huge kernel by
        // root= alone; mkinitrd's initrd (15.0's or geninitrd's) mounts the
        // root it was made with (-r, ROOTDEV) unless root= says another.
        let finds = match self.layout.str_at(&["boot", "initramfs", "generator"]) {
            Some("mkinitrd" | "geninitrd") => "the restored initrd mounts / by that name (mkinitrd's -r, or the kernel's root=)",
            _ => "the restored kernel finds / by that name (it has no initrd to find a UUID)",
        };
        let mut same: Vec<String> = Vec::new();
        for (what, p) in refs {
            let src = self.names.src_of_path(&md_name(&p)).or_else(|| self.names.src_of_path(&p));
            match src.as_deref().and_then(|x| self.names.dev(x)) {
                Some(t) if md_name(&t) == md_name(&p) => {
                    if !same.contains(&p) {
                        same.push(p);
                    }
                }
                Some(t) => self.problems.push(format!(
                    "Slackware: {what} names {p}, which is {t} here; {finds} and fstab mounts by device names, and bko-holt load doesn't rewrite them: restore onto disks that keep the source's names"
                )),
                None => self.problems.push(format!("Slackware: {what} names {p}, which isn't on the layout's disks, so the restored system couldn't find it at boot")),
            }
        }
        if !same.is_empty() {
            self.notes.push(format!(
                "Slackware: lilo.conf, elilo.conf and fstab name devices by their kernel names ({}), which the target's disks keep; on disks named otherwise they would need rewriting",
                same.join(", ")
            ));
        }
    }

    /// LILO (BIOS): `lilo -t -v` (a check that writes nothing), then
    /// `lilo -v`, which writes the MBR of boot= and /boot/map.  Only boot=
    /// a whole disk: LILO in a partition's boot sector is refused by name.
    /// When an image's root= is a /dev/ name lilo would write as a device
    /// number (lilo_root_copy), both run with -C on a copy of lilo.conf
    /// that gives the kernel root= by name too, removed after; the user's
    /// lilo.conf stays as restored.
    fn lilo_cmds(&mut self, t: &str) -> Vec<Cmd> {
        let Some(lilo) = self.layout.v.at(&["boot", "lilo"]).filter(|v| !v.is_null()).cloned() else {
            self.problems.push(format!("LILO: the dump has no {} (boot.lilo), so lilo has nothing to install from", crate::boot::LILO_CONF));
            return Vec::new();
        };
        let conf = s(&lilo, "conf").unwrap_or_else(|| crate::boot::LILO_CONF.into());
        match s(&lilo, "boot") {
            None => self.problems.push(format!("LILO: {conf} has no boot=, so lilo would write to the root partition's boot sector; bko-holt load supports LILO in a disk's MBR only (boot=/dev/DISK)")),
            Some(b) => match self.names.src_of_path(&b) {
                Some(n) if self.layout.disks.iter().any(|d| d.name == n) => {}
                Some(n) => self.problems.push(format!(
                    "LILO: {conf} has boot={b}, partition {n}: LILO in a partition's boot sector (with another loader in the MBR) isn't supported in this release, only boot= a whole disk"
                )),
                None => self.problems.push(format!("LILO: {conf} has boot={b}, which isn't one of the layout's disks")),
            },
        }
        // lilo resolves boot= and disk= on this machine: a link
        // (/dev/disk/by-id/...) could name the source's disk, still
        // attached, and lilo would write its MBR.  Only kernel names, which
        // slackware_device_names checks against the target.
        let mut named: Vec<String> = s(&lilo, "boot").into_iter().map(|b| format!("boot={b}")).collect();
        named.extend(lilo.get("options").and_then(Value::as_array).into_iter().flatten().filter_map(Value::as_str).filter(|o| o.starts_with("disk=")).map(str::to_string));
        for n in named {
            let p = n.split_once('=').map_or("", |(_, p)| p);
            if !kernel_dev_path(p) {
                self.problems.push(format!("LILO: {conf} has {n}, not a kernel device name: lilo would resolve it on this machine, where it may name another disk than the target (the source's, still attached); bko-holt load supports only names like /dev/vda there"));
            }
        }
        match lilo_root_copy(&lilo) {
            Err(p) => {
                self.problems.push(format!("LILO: {p}"));
                Vec::new()
            }
            Ok(None) => vec![
                Cmd::new(&["chroot", t, "lilo", "-t", "-v"]).why(format!("LILO: {conf} checked against the restored kernels; nothing is written (-t)")),
                Cmd::new(&["chroot", t, "lilo", "-v"]).why("LILO: the MBR and /boot/map (the restored kernels' sectors), as lilo writes them"),
            ],
            Ok(Some((text, added))) => {
                let copy = format!("{conf}{LILO_COPY_SUFFIX}");
                vec![
                    // 0600: lilo.conf can hold password=.
                    Cmd::write_file(&format!("{t}{copy}"), text).private().why(format!(
                        "LILO: {conf} copied with root= by name at the end of append= ({}): lilo writes root=/dev/... as the major:minor the rescue kernel gives it, and virtio_blk's major is dynamic, so the restored kernel would look for another device; {conf} stays as restored",
                        added.join(", ")
                    )),
                    Cmd::new(&["chroot", t, "lilo", "-C", &copy, "-t", "-v"]).why(format!("LILO: {copy} checked against the restored kernels; nothing is written (-t)")),
                    Cmd::new(&["chroot", t, "lilo", "-C", &copy, "-v"]).why(format!("LILO: the MBR and /boot/map (the restored kernels' sectors), as lilo writes them from {copy}")),
                    Cmd::new(&["rm", "-f", &format!("{t}{copy}")]).why(format!("LILO: {copy} removed; the map is written")),
                ]
            }
        }
    }

    /// ELILO (UEFI): nothing installed.  After the mkinitrd step, the new
    /// image is copied over each ESP initrd that was a copy of it on the
    /// source (EFI/Slackware's, and EFI/BOOT's), beside a kernel that is a
    /// /boot kernel's copy; an ESP kernel that is no /boot kernel's copy
    /// gets a note.  --removable copies the ESP's ELILO directory to
    /// EFI/BOOT, elilo.efi as BOOTX64.EFI, unless the fallback is there.
    fn elilo_cmds(&mut self, t: &str) -> Vec<Cmd> {
        let confs = self.layout.arr(&["boot", "elilo", "configs"]).to_vec();
        if confs.is_empty() {
            self.problems.push("ELILO: the dump found no elilo.conf on an ESP (boot.elilo), so there is nothing to boot".into());
            return Vec::new();
        }
        self.notes.push("ELILO: elilo.efi, elilo.conf, the kernel and the initrd came back with the ESP; nothing to install".into());
        // The images the initrd step remade, if it did.
        let remade = self.slackware_remade(t);
        let mut out = Vec::new();
        for c in &confs {
            let mnt = s(c, "mounted_at").filter(|m| safe_target_subpath(m)).unwrap_or_else(|| "/boot/efi".into());
            let conf = s(c, "conf").unwrap_or_default();
            for img in c.get("images").and_then(Value::as_array).into_iter().flatten() {
                let file = s(img, "file").unwrap_or_default();
                let kernel = s(img, "same_as");
                if kernel.is_none() {
                    self.notes.push(format!(
                        "ELILO: {conf}'s image {file} is a copy of no /boot kernel (the source was upgraded and eliloconfig not run): the restored machine boots the same old kernel the source did, with the ESP's initrd as restored"
                    ));
                    continue;
                }
                let Some(ifile) = s(img, "initrd_file") else { continue };
                if remade.is_empty() {
                    continue;
                }
                let Some(new) = s(img, "initrd_same_as").filter(|i| remade.contains(i)) else {
                    self.notes.push(format!("ELILO: {ifile} on the ESP wasn't a copy of {} on the source; it stays as restored", remade.join(" or ")));
                    continue;
                };
                let (src, dst) = (format!("{t}{new}"), format!("{t}{}/{ifile}", mnt.trim_end_matches('/')));
                out.push(Cmd::new(&["cp", &src, &dst]).why(format!("ELILO: the remade {new} over the ESP's {ifile}, as eliloconfig copies it")));
                out.push(Cmd::new(&["touch", "-r", &src, &dst]));
            }
        }
        if self.opts.removable {
            let main = confs.iter().find(|c| s(c, "dir").is_some_and(|d| !d.eq_ignore_ascii_case("EFI/BOOT")) && s(c, "loader").is_some());
            match main {
                Some(c) => {
                    let mnt = s(c, "mounted_at").filter(|m| safe_target_subpath(m)).unwrap_or_else(|| "/boot/efi".into());
                    let esp = format!("{t}{}", mnt.trim_end_matches('/'));
                    let dir = s(c, "dir").unwrap_or_default();
                    let loader = s(c, "loader").unwrap_or_default();
                    let name = loader.rsplit('/').next().unwrap_or("elilo.efi").to_string();
                    let (from, to) = (shell_quote(&format!("{esp}/{dir}")), shell_quote(&format!("{esp}/EFI/BOOT")));
                    let fb = self.boot_names().fallback_file;
                    out.push(
                        Cmd::new(&["sh", "-c", &format!("[ -e {to}/{fb} ] || {{ mkdir -p {to} && cp -r {from}/. {to}/ && mv {to}/{} {to}/{fb}; }}", shell_quote(&name))])
                            .why(format!("--removable: {dir} at the fallback path too, {name} as {fb} (ELILO reads the elilo.conf beside itself)")),
                    );
                }
                None => self.notes.push("--removable: ELILO is at the fallback path (EFI/BOOT) only already".into()),
            }
        }
        out
    }

    /// ELILO's boot entry, as eliloconfig makes it (label Slackware, the
    /// loader EFI/Slackware/elilo.efi), when efi_entries made none for an
    /// elilo.efi: the test guests' NVRAM is blank, and they boot the
    /// fallback path.  Last, so that it comes first in BootOrder.
    fn elilo_entry(&mut self) -> Vec<Cmd> {
        let order: Vec<String> = self.layout.v.at(&["boot", "efi", "boot_order"]).and_then(Value::as_array).into_iter().flatten().filter_map(|x| x.as_str().map(str::to_string)).collect();
        let on_layout = |part: &str| self.layout.disks.iter().any(|d| d.parts.iter().any(|p| p.name == part));
        let made = self.layout.arr(&["boot", "efi", "entries"]).iter().any(|e| {
            s(e, "number").is_some_and(|n| order.contains(&n))
                && s(e, "file").is_some_and(|f| f.replace('/', "\\").to_ascii_lowercase().ends_with("\\elilo.efi"))
                && e.at(&["partition", "device"]).and_then(Value::as_str).is_some_and(on_layout)
        });
        if made {
            return Vec::new();
        }
        let confs = self.layout.arr(&["boot", "elilo", "configs"]).to_vec();
        let Some(c) = confs.iter().find(|c| s(c, "dir").is_some_and(|d| !d.eq_ignore_ascii_case("EFI/BOOT")) && s(c, "loader").is_some()) else {
            self.notes.push("ELILO: only at the fallback path (EFI/BOOT), so no boot entry is made: the firmware boots it as removable media".into());
            return Vec::new();
        };
        let part = s(c, "device").unwrap_or_default();
        let Some((disk, num)) = self.layout.disks.iter().find_map(|d| d.parts.iter().find(|p| p.name == part).map(|p| (d.name.clone(), p.number))) else {
            self.notes.push(format!("ELILO: its ESP {part} isn't on the layout's disks; no boot entry made"));
            return Vec::new();
        };
        let Some(tgt) = self.names.mapping.target_of(&disk) else { return Vec::new() };
        let loader = format!("\\{}", s(c, "loader").unwrap_or_default().replace('/', "\\"));
        vec![
            efi_dedupe(&format!("/dev/{tgt}"), num, &loader),
            Cmd::new(&["efibootmgr", "--quiet", "--create", "--disk", &format!("/dev/{tgt}"), "--part", &num.to_string(), "--label", "Slackware", "--loader", &loader])
                .why("ELILO: the boot entry eliloconfig makes (the source's NVRAM had none for it)"),
        ]
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::load::layout::{sample, Layout};

    /// Fake programs for the boot stage's scripts run by sh: a directory of
    /// sh scripts under DIR, for PATH.  Each file is written by a child
    /// process, so that no fd of this one has it open for writing when
    /// another test's thread forks (its exec would fail with ETXTBSY).
    fn fakes(dir: &std::path::Path, fakes: &[(&str, String)]) -> String {
        use std::io::Write;
        static N: std::sync::atomic::AtomicUsize = std::sync::atomic::AtomicUsize::new(0);
        let bin = dir.join(format!("bin{}", N.fetch_add(1, std::sync::atomic::Ordering::SeqCst)));
        std::fs::create_dir_all(&bin).unwrap();
        for (name, body) in fakes {
            let mut c = std::process::Command::new("/bin/sh")
                .args(["-c", "cat > \"$1\" && chmod 755 \"$1\"", "sh"])
                .arg(bin.join(name))
                .stdin(std::process::Stdio::piped())
                .spawn()
                .unwrap();
            c.stdin.take().unwrap().write_all(format!("#!/bin/sh\n{body}\n").as_bytes()).unwrap();
            assert!(c.wait().unwrap().success());
        }
        bin.display().to_string()
    }

    /// SCRIPT run by sh with BIN first on PATH (or PATH alone, when
    /// given), $R for the chroot's / and TMPDIR for mktemp.
    fn run_script(script: &str, bin: &str, root: &std::path::Path, tmp: &std::path::Path, path: Option<&str>) -> std::process::Output {
        std::fs::create_dir_all(tmp).unwrap();
        std::process::Command::new("/bin/sh")
            .args(["-c", script])
            .env("R", root)
            .env("TMPDIR", tmp)
            .env("PATH", path.map_or_else(|| format!("{bin}:/usr/bin:/bin"), str::to_string))
            .output()
            .unwrap()
    }

    fn touch(root: &std::path::Path, files: &[String]) {
        for f in files {
            let p = root.join(f);
            std::fs::create_dir_all(p.parent().unwrap()).unwrap();
            std::fs::write(&p, "").unwrap();
        }
    }

    /// Sets DIR's mtime to a fixed old one (as a restored tree's
    /// directories have the source's), and returns it.
    fn age(dir: &std::path::Path) -> std::time::SystemTime {
        let st = std::process::Command::new("touch").args(["-t", "202001020304.05"]).arg(dir).status().unwrap();
        assert!(st.success());
        mtime(dir)
    }

    fn mtime(p: &std::path::Path) -> std::time::SystemTime {
        std::fs::metadata(p).unwrap().modified().unwrap()
    }

    /// DRACUT_EACH_KERNEL run by sh over a fake tree ($R for the chroot's
    /// /), with a fake lsinitrd (the restored images' listings, as
    /// openSUSE's prints them) and a dracut that prints its arguments and
    /// the files it was given to --include.
    #[test]
    fn dracut_keeps_the_restored_images_modules() {
        let t = crate::testutil::TempDir::new("dracut-mods");
        let (r, k, k2) = (t.path().join("root"), "6.12.0-160100.8-default", "7.0.1-1-default");
        touch(&r, &[format!("usr/lib/modules/{k}/kernel/fs/efivarfs/efivarfs.ko.zst"), format!("lib/modules/{k}/kernel/drivers/block/loop.ko"),
                    format!("lib/modules/{k2}/kernel/fs/btrfs/btrfs.ko.zst"), "lib/modules/6.11.0-gone/modules.dep".into()]);
        let listing = format!(
            "Image: /boot/initrd-{k}: 24M\n\
             ========================================================================\n\
             Version: dracut-059+suse.640.g3a1b2c3d\n\
             \n\
             Arguments:  --force --kver '{k}'\n\
             \n\
             dracut modules:\n\
             systemd\n\
             resume\n\
             btrfs\n\
             ========================================================================\n\
             lrwxrwxrwx   1 root     root            7 Oct 19 06:31 lib -> usr/lib\n\
             drwxr-xr-x   1 root     root            0 Nov 14 10:46 usr/lib/modules/{k}/kernel/fs/efivarfs\n\
             -rw-r--r--   1 root     root        17655 Oct 19 06:31 usr/lib/modules/{k}/kernel/fs/efivarfs/efivarfs.ko.zst\n\
             -rw-r--r--   1 root     root        25282 Oct 19 06:31 lib/modules/{k}/kernel/drivers/block/loop.ko\n\
             -rw-r--r--   1 root     root        25282 Oct 19 06:31 usr/lib/modules/{k}/kernel/drivers/scsi/gone.ko.xz\n\
             -rw-r--r--   1 root     root          512 Oct 19 06:31 usr/lib/dracut/hooks/resume\n\
             -rw-r--r--   1 root     root         9001 Oct 19 06:31 usr/lib/modules/{k}/modules.dep\n\
             ========================================================================\n");
        // k2's image had no resume module: its listing names only files
        // with "resume" in them.
        let listing2 = format!(
            "Image: /boot/initrd-{k2}: 20M\n\
             dracut modules:\n\
             systemd\n\
             btrfs\n\
             ========================================================================\n\
             -rw-r--r--   1 root     root          512 Oct 19 06:31 resume\n\
             ========================================================================\n");
        let lsinitrd = format!(
            "[ \"$1\" = -k ] || exit 2\n\
             case \"$2 $3 $4\" in\n\
             '{k} -f etc/cmdline.d/95resume.conf') echo ' resume=UUID=eee0e84a-5975-4d26-b706-4145f8ebd5db' ;;\n\
             '{k}  ') cat <<'EOF'\n{listing}EOF\n;;\n\
             '{k2}  ') cat <<'EOF'\n{listing2}EOF\n;;\n\
             *) echo \"no initramfs for $2\" >&2; exit 1 ;;\nesac\n");
        let dracut = "printf '[%s]' \"$@\"; echo\nwhile [ $# -gt 0 ]; do [ \"$1\" = --include ] && echo \"$3: $(cat \"$2\")\"; shift; done\n".to_string();
        let bin = fakes(t.path(), &[("lsinitrd", lsinitrd.clone()), ("dracut", dracut)]);
        let script = DRACUT_EACH_KERNEL.replace("/lib/modules/*", "$R/lib/modules/*").replace("\"/$p\"", "\"$R/$p\"");
        assert_eq!(script.matches("$R/").count(), 2, "{script}");
        let tmp = t.path().join("tmp");
        let o = run_script(&script, &bin, &r, &tmp, None);
        assert!(o.status.success(), "{}", String::from_utf8_lossy(&o.stderr));
        let out = String::from_utf8_lossy(&o.stdout).into_owned();
        let mut lines = out.lines();
        // The modules the image had and the tree still has, in its order;
        // the resume module, and the resume= its command line had.
        let first = lines.next().unwrap();
        let inc = first.find("[--include][").map(|i| &first[i + 12..]).and_then(|r| r.split(']').next()).unwrap_or_else(|| panic!("{first}"));
        assert_eq!(first, format!("[--force][--kver][{k}][--add-drivers][ efivarfs loop][--add][resume][--include][{inc}][/etc/cmdline.d/95resume.conf]"));
        assert_eq!(lines.next(), Some("/etc/cmdline.d/95resume.conf:  resume=UUID=eee0e84a-5975-4d26-b706-4145f8ebd5db"));
        // A kernel whose image had no resume module: none added; a
        // directory with no kernel/ (6.11.0-gone) is no kernel.
        assert_eq!(lines.next(), Some(format!("[--force][--kver][{k2}]").as_str()));
        assert_eq!(lines.next(), None);
        // The --include file is removed.
        assert_eq!(std::fs::read_dir(&tmp).unwrap().count(), 0);
        // No kernel at all: it fails, as before.
        let empty = t.path().join("empty");
        std::fs::create_dir_all(empty.join("lib/modules")).unwrap();
        assert!(!run_script(&script, &bin, &empty, &tmp, None).status.success());
        // dracut's failure is the script's, and the file is removed.
        let failing = fakes(t.path(), &[("dracut", "exit 3".into()), ("lsinitrd", lsinitrd)]);
        assert_eq!(run_script(&script, &failing, &r, &tmp, None).status.code(), Some(3));
        assert_eq!(std::fs::read_dir(&tmp).unwrap().count(), 0);
    }

    /// DRACUT_ONE_IMAGE run by sh over a fake tree, as Gentoo's boot stage
    /// runs it (KVER and IMAGE its arguments), with a fake lsinitrd (dracut
    /// 112's listing of a host-only image with the resume module, read by
    /// the image's path) and a dracut that prints its arguments and the
    /// files it was given to --include.
    #[test]
    fn dracut_one_image_keeps_modules_and_resume() {
        let t = crate::testutil::TempDir::new("dracut-one");
        let (r, k) = (t.path().join("root"), "6.18.63-gentoo-dist-bin");
        let (img, img2) = (format!("/boot/initramfs-{k}.img"), "/boot/initramfs-6.18.9-gentoo-dist-bin.img");
        touch(&r, &[format!("lib/modules/{k}/kernel/fs/efivarfs/efivarfs.ko.xz"), format!("lib/modules/{k}/kernel/drivers/md/dm-mod.ko.xz")]);
        let listing = format!(
            "Image: {img}: 18M\n\
             ========================================================================\n\
             Version: dracut-112\n\
             \n\
             Arguments:  --kver '{k}' --force\n\
             \n\
             dracut modules:\n\
             bash\n\
             resume\n\
             lvm\n\
             ========================================================================\n\
             -rw-r--r--   1 root     root        11204 Nov 12 18:02 lib/modules/{k}/kernel/fs/efivarfs/efivarfs.ko.xz\n\
             -rw-r--r--   1 root     root        81116 Nov 12 18:02 lib/modules/{k}/kernel/drivers/md/dm-mod.ko.xz\n\
             -rw-r--r--   1 root     root        30020 Nov 12 18:02 lib/modules/{k}/kernel/drivers/nvme/gone.ko.xz\n\
             ========================================================================\n");
        // The other image's resume is only a file's name; it has no module.
        let listing2 = "Image: x: 9M\ndracut modules:\nbash\nlvm\n====\n-rw-r--r-- 1 root root 1 Nov 12 18:02 usr/lib/dracut/hooks/resume\n====\n";
        let lsinitrd = format!(
            "case \"$*\" in\n\
             '-f etc/cmdline.d/95resume.conf {img}') echo ' resume=UUID=bc000000-0000-4000-8000-000000000004' ;;\n\
             '{img}') cat <<'EOF'\n{listing}EOF\n;;\n\
             '{img2}') cat <<'EOF'\n{listing2}EOF\n;;\n\
             *) echo \"no such image $*\" >&2; exit 1 ;;\nesac\n");
        let dracut = "printf '[%s]' \"$@\"; echo\nwhile [ $# -gt 0 ]; do [ \"$1\" = --include ] && echo \"$3: $(cat \"$2\")\"; shift; done\n".to_string();
        let bin = fakes(t.path(), &[("lsinitrd", lsinitrd.clone()), ("dracut", dracut)]);
        let script = DRACUT_ONE_IMAGE.replace("\"/$p\"", "\"$R/$p\"");
        assert_eq!(script.matches("$R/").count(), 1, "{script}");
        let with = |k: &str, i: &str| format!("set -- {k} {i}; {script}");
        let tmp = t.path().join("tmp");
        let o = run_script(&with(k, &img), &bin, &r, &tmp, None);
        assert!(o.status.success(), "{}", String::from_utf8_lossy(&o.stderr));
        let out = String::from_utf8_lossy(&o.stdout).into_owned();
        let mut lines = out.lines();
        let first = lines.next().unwrap();
        let inc = first.find("[--include][").map(|i| &first[i + 12..]).and_then(|r| r.split(']').next()).unwrap_or_else(|| panic!("{first}"));
        assert_eq!(first, format!("[--force][--add-drivers][ efivarfs dm-mod][--add][resume][--include][{inc}][/etc/cmdline.d/95resume.conf][{img}][{k}]"));
        assert_eq!(lines.next(), Some("/etc/cmdline.d/95resume.conf:  resume=UUID=bc000000-0000-4000-8000-000000000004"));
        assert_eq!(lines.next(), None);
        assert_eq!(std::fs::read_dir(&tmp).unwrap().count(), 0);
        // An image with no resume module (and no module still in the
        // tree): plain dracut.
        let o = run_script(&with("6.18.9-gentoo-dist-bin", img2), &bin, &r, &tmp, None);
        assert_eq!(String::from_utf8_lossy(&o.stdout), format!("[--force][{img2}][6.18.9-gentoo-dist-bin]\n"));
        // No image lsinitrd can read: plain dracut, as before.
        let o = run_script(&with(k, "/boot/missing.img"), &bin, &r, &tmp, None);
        assert!(o.status.success());
        assert_eq!(String::from_utf8_lossy(&o.stdout), format!("[--force][/boot/missing.img][{k}]\n"));
        // dracut's failure is the script's, and the file is removed.
        let failing = fakes(t.path(), &[("dracut", "exit 3".into()), ("lsinitrd", lsinitrd)]);
        assert_eq!(run_script(&with(k, &img), &failing, &r, &tmp, None).status.code(), Some(3));
        assert_eq!(std::fs::read_dir(&tmp).unwrap().count(), 0);
    }

    /// DRACUT_REBUILD_RESUME: a drop-in adds the resume module while
    /// dracut-rebuild runs when a host-only image had it (a fallback's
    /// doesn't count), and is removed after; one already there stays.
    #[test]
    fn dracut_rebuild_keeps_resume() {
        let t = crate::testutil::TempDir::new("dracut-rebuild");
        let r = t.path().join("root");
        touch(&r, &["boot/initramfs-linux.img".into(), "boot/initramfs-linux-fallback.img".into(), "etc/dracut.conf.d/.keep".into()]);
        let lsinitrd = "case $1 in *-fallback.img) printf 'dracut modules:\\nresume\\n====\\n' ;; *) cat \"$1.list\" ;; esac\n".to_string();
        let rebuild = "cat \"$R\"/etc/dracut.conf.d/*.conf 2>/dev/null; echo rebuilt\n".to_string();
        let bin = fakes(t.path(), &[("lsinitrd", lsinitrd.clone()), ("dracut-rebuild", rebuild)]);
        let script = DRACUT_REBUILD_RESUME.replace("/boot/", "$R/boot/").replace("c=/etc/", "c=$R/etc/");
        let tmp = t.path().join("tmp");
        let conf = r.join("etc/dracut.conf.d/zz-bko-holt-resume.conf");
        std::fs::write(r.join("boot/initramfs-linux.img.list"), "dracut modules:\nsystemd\nresume\n=====\n").unwrap();
        let confd = r.join("etc/dracut.conf.d");
        let was = age(&confd);
        let o = run_script(&script, &bin, &r, &tmp, None);
        assert!(o.status.success(), "{}", String::from_utf8_lossy(&o.stderr));
        assert_eq!(String::from_utf8_lossy(&o.stdout), "add_dracutmodules+=\" resume \"\nrebuilt\n");
        assert!(!conf.exists());
        // dracut.conf.d's mtime is the one it had, and the reference file
        // is gone.
        assert_eq!(mtime(&confd), was);
        assert_eq!(std::fs::read_dir(&tmp).unwrap().count(), 0);
        // Only the fallback had it: nothing added.
        std::fs::write(r.join("boot/initramfs-linux.img.list"), "dracut modules:\nsystemd\n=====\n-rw-r--r-- 1 root root 1 Oct 19 06:31 resume\n").unwrap();
        assert_eq!(String::from_utf8_lossy(&run_script(&script, &bin, &r, &tmp, None).stdout), "rebuilt\n");
        // A drop-in of that name already there is the system's: kept.
        std::fs::write(r.join("boot/initramfs-linux.img.list"), "dracut modules:\nresume\n=====\n").unwrap();
        std::fs::write(&conf, "# mine\n").unwrap();
        assert_eq!(String::from_utf8_lossy(&run_script(&script, &bin, &r, &tmp, None).stdout), "# mine\nrebuilt\n");
        assert!(conf.exists());
        // dracut-rebuild's failure is the script's.
        std::fs::remove_file(&conf).unwrap();
        let failing = fakes(t.path(), &[("dracut-rebuild", "exit 4".into()), ("lsinitrd", lsinitrd)]);
        let was = age(&confd);
        assert_eq!(run_script(&script, &failing, &r, &tmp, None).status.code(), Some(4));
        assert!(!conf.exists());
        assert_eq!(mtime(&confd), was);
    }

    /// UPDATE_INITRAMFS_RESUME: the resume device a restored image's
    /// resume hook had picked (conf/conf.d/zz-resume-auto, in an early +
    /// main image or a plain one) goes in conf.d while update-initramfs
    /// runs, and is removed after.
    #[test]
    fn update_initramfs_keeps_the_auto_resume_device() {
        let t = crate::testutil::TempDir::new("initramfs-resume");
        let r = t.path().join("root");
        touch(&r, &["boot/initrd.img-6.12.48+deb13-amd64".into(), "boot/initrd.img-6.12.57+deb13-amd64".into(), "etc/initramfs-tools/conf.d/.keep".into()]);
        // The older image's resume hook found no swap; the newer's did.
        // By the image's own name: the temporary directory's can hold "48".
        let unmk = "case ${1##*/} in *48*) x=$2/conf/conf.d; mkdir -p $x; echo 'RESUME=' > $x/zz-resume-auto ;; \
                    *57*) x=$2/main/conf/conf.d; mkdir -p $x $2/early; echo 'RESUME=UUID=bc000000-0000-4000-8000-000000000003' > $x/zz-resume-auto ;; esac\n".to_string();
        let update = "echo \"update-initramfs $*\"; cat \"$R\"/etc/initramfs-tools/conf.d/* 2>/dev/null; :\n".to_string();
        let bin = fakes(t.path(), &[("unmkinitramfs", unmk.clone()), ("update-initramfs", update.clone())]);
        let script = UPDATE_INITRAMFS_RESUME.replace("/boot/", "$R/boot/").replace("c=/etc/", "c=$R/etc/");
        let tmp = t.path().join("tmp");
        let conf = r.join("etc/initramfs-tools/conf.d/zz-resume-auto");
        let confd = r.join("etc/initramfs-tools/conf.d");
        let was = age(&confd);
        let o = run_script(&script, &bin, &r, &tmp, None);
        assert!(o.status.success(), "{}", String::from_utf8_lossy(&o.stderr));
        assert_eq!(String::from_utf8_lossy(&o.stdout), "update-initramfs -u -k all\nRESUME=UUID=bc000000-0000-4000-8000-000000000003\n");
        assert!(!conf.exists());
        // conf.d's mtime is the one it had (t2h-20261118T2305-edbb).
        assert_eq!(mtime(&confd), was);
        assert_eq!(std::fs::read_dir(&tmp).unwrap().count(), 0, "unmkinitramfs's directories and the reference file are removed");
        // No image picked one: update-initramfs as it is.
        std::fs::remove_file(r.join("boot/initrd.img-6.12.57+deb13-amd64")).unwrap();
        assert_eq!(String::from_utf8_lossy(&run_script(&script, &bin, &r, &tmp, None).stdout), "update-initramfs -u -k all\n");
        // The system's own zz-resume-auto: kept, and not read again.
        std::fs::write(&conf, "RESUME=/dev/vda5\n").unwrap();
        assert_eq!(String::from_utf8_lossy(&run_script(&script, &bin, &r, &tmp, None).stdout), "update-initramfs -u -k all\nRESUME=/dev/vda5\n");
        assert!(conf.exists());
        std::fs::remove_file(&conf).unwrap();
        // No unmkinitramfs (Chimera's initramfs-tools, say): as it is.
        let bare = fakes(t.path(), &[("update-initramfs", update)]);
        let o = run_script(&script, &bare, &r, &tmp, Some(&bare));
        assert!(o.status.success(), "{}", String::from_utf8_lossy(&o.stderr));
        // update-initramfs's failure is the script's, the file removed.
        touch(&r, &["boot/initrd.img-6.12.57+deb13-amd64".into()]);
        let failing = fakes(t.path(), &[("update-initramfs", "exit 5".into()), ("unmkinitramfs", unmk)]);
        let was = age(&confd);
        assert_eq!(run_script(&script, &failing, &r, &tmp, None).status.code(), Some(5));
        assert!(!conf.exists());
        assert_eq!(mtime(&confd), was);
    }

    fn built(extra: &[&str]) -> Built {
        built_from(&sample::bios_mbr(), extra)
    }

    fn built_from(json: &str, extra: &[&str]) -> Built {
        let l = Layout::from_json(crate::json::parse(json).unwrap()).unwrap();
        let mut a = vec!["l.json".to_string()];
        a.extend(extra.iter().map(|s| s.to_string()));
        let o = match crate::load::parse_args(&a).unwrap() {
            crate::load::Parsed::Run(o) => o,
            _ => unreachable!(),
        };
        let m = Mapping { pairs: vec![("vda".into(), "vdb".into())], ..Mapping::default() };
        build(&l, &o, &m)
    }

    fn text(cmds: &[Cmd]) -> String {
        cmds.iter().map(Cmd::shell).collect()
    }

    /// -E resize= gives the source's reserved GDT blocks to the block: each
    /// (block size, blocks, blocks a group, 64bit, reserved) here was made
    /// by mke2fs 1.47.2 with this resize= and read back (cinqcent,
    /// 2026-11-17).  With 1 KiB blocks the old + first data block asked for
    /// one more (BackShot2's fail-004: 127 made 128).
    #[test]
    fn mke2fs_resize_reserves_the_sources_gdt_blocks() {
        let cases: [(u64, u64, u64, bool, u64, u64); 7] = [
            (1024, 1048576, 8192, true, 127, 17694720),
            (1024, 1048576, 8192, false, 127, 34340864),
            (1024, 1048577, 8192, true, 100, 14286848),
            (1024, 1056769, 8192, false, 37, 11010048),
            (1024, 8193, 8192, false, 3, 1048576),
            (4096, 3538432, 32768, true, 261, 551550976),
            (2048, 500001, 16384, true, 50, 26738688),
        ];
        for (bs, n, bpg, b64, r, want) in cases {
            let (feats, desc) = if b64 { (r#""resize_inode", "64bit""#, r#", "descriptor_size": 64"#) } else { (r#""resize_inode""#, "") };
            let c = crate::json::parse(&format!(
                r#"{{"type": "ext4", "block_size": {bs}, "blocks": {n}, "blocks_per_group": {bpg}, "reserved_gdt_blocks": {r}, "features": [{feats}]{desc}}}"#
            ))
            .unwrap();
            assert_eq!(resize_for(&c), Some(want), "{bs} {n} {bpg} {b64} {r}");
            assert!(mke2fs("/dev/x", &c).iter().any(|a| a.ends_with(&format!("resize={want}"))));
        }
        // None reserved, or no resize_inode: no resize= (mke2fs would take
        // the one as no request, or refuse it).
        let none = crate::json::parse(r#"{"block_size": 4096, "blocks": 32768, "blocks_per_group": 32768, "reserved_gdt_blocks": 0, "features": ["resize_inode"]}"#).unwrap();
        assert_eq!(resize_for(&none), None);
        let off = crate::json::parse(r#"{"block_size": 4096, "blocks": 32768, "blocks_per_group": 32768, "reserved_gdt_blocks": 7, "features": []}"#).unwrap();
        assert_eq!(resize_for(&off), None);
    }

    #[test]
    fn snapshot_paths() {
        assert_eq!(snapshot_path("latest", "/.snapshots/2/snapshot"), ("latest".into(), "/.snapshots/2/snapshot".into()));
        assert_eq!(snapshot_path("latest:/mnt/src", "/.snapshots/2/snapshot"), ("latest".into(), "/mnt/src/.snapshots/2/snapshot".into()));
        assert_eq!(snapshot_path("1a2b:/mnt/src/", "/"), ("1a2b".into(), "/mnt/src".into()));
        assert_eq!(snapshot_path("1a2b:/", "/home"), ("1a2b".into(), "/home".into()));
    }

    #[test]
    fn shared_snapshots_whatever_the_stage() {
        // format without --repo plans what restore with it does.
        let j = std::fs::read_to_string(concat!(env!("CARGO_MANIFEST_DIR"), "/tests/corpus/tumbleweed/src.json")).unwrap();
        let fmt = |extra: &[&str]| {
            let b = built_from(&j, extra);
            text(b.stage(Stage::Format))
        };
        let a = fmt(&[]);
        assert_eq!(a, fmt(&["--repo", "/r", "--snapshot", "latest:/mnt/src"]));
        assert!(!a.contains(".snapshots/2/snapshot"), "{a}");
        assert!(fmt(&["--repo", "/r", "--btrfs-snapshots", "copy"]).contains("@/.snapshots/2/snapshot"));
        let r = text(built_from(&j, &["--repo", "/r", "--snapshot", "latest:/mnt/src"]).stage(Stage::Restore));
        assert!(r.contains("latest:/mnt/src/.snapshots/2/snapshot "), "{r}");
        assert!(r.contains(&format!("--clone-from /mnt/src={RUN_DIR}/btrfs/")), "{r}");
    }

    #[test]
    fn mkfs_xfs_keeps_data_and_ag_size() {
        // rocky10-raid5's root: Anaconda's mkfs left it 3 MiB short of its LV.
        let c = crate::json::parse(r#"{"block_size":4096,"blocks":7333888,"ag_blocks":458368,"ag_count":16,"stripe_unit":128,"stripe_width":256}"#).unwrap();
        let a = mkfs_xfs("/dev/x", &c, None).join(" ");
        assert!(a.contains("-d size=30039605248,agsize=1877475328,sunit=1024,swidth=2048"), "{a}");
        assert!(!a.contains("agcount"), "{a}");
        // Without the sizes, the AG count as before.
        let c = crate::json::parse(r#"{"block_size":4096,"ag_count":8}"#).unwrap();
        assert!(mkfs_xfs("/dev/x", &c, None).join(" ").contains("-d agcount=8"));
    }

    /// Every feature xfsprogs 6.18 knows is named, on or off: Rocky 10's XFS
    /// has no exchange or parent, which 6.18 turns on by default; and the
    /// source's imaxpct, as an inode option.
    #[test]
    fn mkfs_xfs_names_every_feature() {
        let rocky = r#"{"type":"xfs","uuid":"u1","block_size":4096,"inode_size":512,"dir_block_size":4096,"imax_pct":25,
            "features":["lazysbcount","attr2","projid32bit","crc","finobt","rmapbt","reflink","inobtcount","ftype","sparse","bigtime","nrext64"]}"#;
        let a = mkfs_xfs("/dev/sdq3", &crate::json::parse(rocky).unwrap(), None).join(" ");
        assert_eq!(
            a,
            "mkfs.xfs -f -m crc=1,finobt=1,rmapbt=1,reflink=1,inobtcount=1,bigtime=1,uuid=u1,metadir=0 -i sparse=1,nrext64=1,size=512,exchange=0,maxpct=25 -n ftype=1,size=4096,parent=0 -b size=4096 -l lazy-count=1 /dev/sdq3"
        );
        // Fedora 44's has both; a metadir one, and no imaxpct recorded.
        let fedora = r#"{"features":["crc","ftype","exchange","parent","metadir"]}"#;
        let a = mkfs_xfs("/dev/x", &crate::json::parse(fedora).unwrap(), None).join(" ");
        assert!(a.contains("bigtime=0,metadir=1 -i sparse=0,nrext64=0,exchange=1 -n ftype=1,parent=1 "), "{a}");
        assert!(!a.contains("maxpct"), "{a}");
    }

    /// A legacy pool (version 28) is made at its version, with no feature@
    /// words; a feature-flags pool's command is as before, its version (`-`
    /// or 5000) never named.
    #[test]
    fn legacy_pool_keeps_its_version() {
        fn set(v: &mut Value, path: &[&str], to: Value) {
            let Some((k, rest)) = path.split_first() else {
                *v = to;
                return;
            };
            match v {
                Value::Arr(a) => set(&mut a[k.parse::<usize>().unwrap()], rest, to),
                Value::Obj(o) => match o.iter_mut().find(|(n, _)| n == k) {
                    Some((_, x)) => set(x, rest, to),
                    None => {
                        o.push((k.to_string(), Value::Null));
                        set(&mut o.last_mut().unwrap().1, rest, to);
                    }
                },
                _ => panic!("no {k}"),
            }
        }
        let j = std::fs::read_to_string(concat!(env!("CARGO_MANIFEST_DIR"), "/tests/corpus/debian13-zfs/src.json")).unwrap();
        let create = |v: &Value| {
            let t = text(built_from(&v.compact(), &[]).stage(Stage::Assemble));
            t.lines().find(|l| l.starts_with("zpool create ")).unwrap_or_else(|| panic!("no zpool create in\n{t}")).to_string()
        };
        let src = crate::json::parse(&j).unwrap();
        let pool = ["storage", "zfs", "tools", "imported_pools", "0"];
        let at = |k: &[&'static str]| -> Vec<&'static str> { pool.iter().chain(k).copied().collect() };
        let before = create(&src);
        assert!(before.contains("-o feature@async_destroy=enabled ") && !before.contains("version="), "{before}");
        let mut v5000 = src.clone();
        set(&mut v5000, &at(&["properties", "version"]), "5000".into());
        assert_eq!(create(&v5000), before);
        // v28, its features listed (as zpool get lists them) and the version
        // set locally too: named once.
        for path in [&["properties", "version"][..], &["local_properties", "version"]] {
            let mut v28 = src.clone();
            set(&mut v28, &at(path), "28".into());
            let a = create(&v28);
            assert_eq!(a.matches("version=").count(), 1, "{a}");
            assert!(a.contains(" -o version=28 ") && !a.contains("feature@"), "{a}");
            assert_eq!(a.replace(" -o version=28", ""), before.split(" -o feature@").next().unwrap().to_string() + &before[before.find(" -O ").unwrap()..]);
        }
        let mut v28 = src.clone();
        set(&mut v28, &at(&["properties", "version"]), 28u64.into());
        assert!(create(&v28).contains(" -o version=28 "));
    }

    #[test]
    fn bootloader_id_comes_from_the_layouts_own_disks() {
        let id_of = |efi: &str, esps: &str| {
            let j = sample::bios_mbr()
                .replace(r#""firmware": {"mode": "bios"}"#, r#""firmware": {"mode": "uefi"}"#)
                .replace(r#""firmware": "bios""#, r#""firmware": "uefi""#)
                .replace(r#""efi": null,"#, &format!(r#""efi": {efi}, "esps": {esps},"#));
            let t = text(built_from(&j, &[]).stage(Stage::Boot));
            let at = t.find("--bootloader-id=").expect(&t) + "--bootloader-id=".len();
            t[at..].split_whitespace().next().unwrap().to_string()
        };
        let win = r#"{"number": "0000", "description": "Windows", "file": "\\EFI\\Microsoft\\Boot\\bootmgfw.efi"}"#;
        let zorin = r#"{"number": "0002", "description": "Zorin", "file": "\\EFI\\zorin\\shimx64.efi", "partition": {"device": "vda1"}}"#;
        let ubuntu = r#"{"number": "0003", "description": "Ubuntu", "file": "\\EFI\\ubuntu\\shimx64.efi", "partition": {"device": "vda1"}}"#;
        // A stale entry listed first, on no disk of the layout.
        assert_eq!(id_of(&format!(r#"{{"entries": [{win}, {ubuntu}], "boot_order": ["0000", "0003"]}}"#), "[]"), "ubuntu");
        // The entry that booted beats BootOrder and the listing.
        assert_eq!(id_of(&format!(r#"{{"entries": [{zorin}, {ubuntu}], "boot_current": "0003", "boot_order": ["0002", "0003"]}}"#), "[]"), "ubuntu");
        // Then BootOrder.
        assert_eq!(id_of(&format!(r#"{{"entries": [{zorin}, {ubuntu}], "boot_order": ["0003", "0002"]}}"#), "[]"), "ubuntu");
        // Nothing on the layout's disks: what's on the ESP.
        let esps = r#"[{"contents": {"files": [{"path": "EFI/BOOT/BOOTX64.EFI"}, {"path": "EFI/ubuntu/shimx64.efi"}]}}]"#;
        assert_eq!(id_of(&format!(r#"{{"entries": [{win}], "boot_order": ["0000"]}}"#), esps), "ubuntu");
        assert_eq!(id_of(&format!(r#"{{"entries": [{win}], "boot_order": ["0000"]}}"#), "[]"), "debian");
        // The source's spelling, from the entry or the ESP (BigLinux's);
        // EFI and BOOT still matched without case.
        let big = r#"{"number": "0004", "description": "BigLinux", "file": "\\EFI\\BigLinux\\grubx64.efi", "partition": {"device": "vda1"}}"#;
        assert_eq!(id_of(&format!(r#"{{"entries": [{big}], "boot_order": ["0004"]}}"#), "[]"), "BigLinux");
        let esps = r#"[{"contents": {"files": [{"path": "efi/Boot/bootx64.efi"}, {"path": "efi/BigLinux/grubx64.efi"}]}}]"#;
        assert_eq!(id_of(&format!(r#"{{"entries": [{win}], "boot_order": ["0000"]}}"#), esps), "BigLinux");
        // elementary OS (docs/design/elementary.md): distinst maps "elementary
        // OS" to ubuntu, so the entry it labels "elementary OS 8" is shim in
        // EFI/ubuntu.
        let elementary = r#"{"number": "0001", "description": "elementary OS 8", "file": "\\EFI\\ubuntu\\shimx64.efi", "partition": {"device": "vda1"}}"#;
        assert_eq!(id_of(&format!(r#"{{"entries": [{win}, {elementary}], "boot_current": "0001", "boot_order": ["0000", "0001"]}}"#), "[]"), "ubuntu");
    }

    #[test]
    fn systemd_boot_through_shim_keeps_the_esp() {
        let boot = |via_shim: bool| {
            let j = sample::bios_mbr()
                .replace(r#""firmware": {"mode": "bios"}"#, r#""firmware": {"mode": "uefi"}"#)
                .replace(r#""loader": {"name": "grub", "firmware": "bios", "via_shim": false}"#, &format!(r#""loader": {{"name": "systemd-boot", "firmware": "uefi", "via_shim": {via_shim}}}"#));
            let b = built_from(&j, &[]);
            (text(b.stage(Stage::Boot)), b.notes.join("\n"))
        };
        let (t, n) = boot(true);
        assert!(!t.contains("bootctl"), "{t}");
        assert!(n.contains("systemd-boot through shim"), "{n}");
        let (t, _) = boot(false);
        assert!(t.contains("chroot /mnt/bko-target bootctl install --no-variables\n"), "{t}");
        // bootctl writes /etc/kernel/entry-token: relabelled, when it's there.
        assert!(t.contains(r#"[ ! -d /etc/kernel ] || set -- /etc/kernel; setfiles "$f" /boot /var/log/bko-holt "$@";"#), "{t}");
    }

    /// The mount points the mount stage made, hidden from the restore under
    /// their mounts (BackShot1, Leap 16: unlabeled_t /usr/local and /var at
    /// early boot): each filesystem holding some is bound alone under
    /// RUN_DIR/mount-points, and setfiles -r labels them at their logical
    /// paths, nested ones (/var/lib/x on /var) from /var's bind; none on a
    /// filesystem without xattrs, nor under the chroot's own /run.
    #[test]
    fn mount_points_under_mounts_are_labelled() {
        let script = |boot_ty: &str| {
            let sub = |file: &str, vol: &str| format!(r#"{{"spec": "UUID=b7b7b7b7-0000-4000-8000-000000000003", "file": "{file}", "type": "btrfs", "options": "subvol={vol}", "freq": 0, "passno": 0, "resolves_to": "vda3"}},"#);
            let extra = [sub("/var/lib/x", "@x"), sub("/usr/local", "@usr_local"), sub("/var", "@var"), sub("/boot/grub", "@grub"), sub("/run/x", "@run_x")].concat();
            let j = sample::bios_mbr()
                .replace(r#""file": "/boot", "type": "ext4""#, &format!(r#""file": "/boot", "type": "{boot_ty}""#))
                .replace(r#""fstab": ["#, &format!(r#""fstab": [{extra}"#));
            let b = built_from(&j, &[]);
            let c = b.stage(Stage::Boot).iter().find(|c| c.argv.last().is_some_and(|a| a.contains("/mount-points"))).cloned();
            let umount = b.stage(Stage::Boot).iter().position(|c| c.argv.starts_with(&["umount".to_string(), "--recursive".to_string()]));
            let at = b.stage(Stage::Boot).iter().position(|c| c.argv.last().is_some_and(|a| a.contains("/mount-points")));
            assert!(at < umount, "labelled while /proc and /run are still bound");
            let c = c.expect("a mount point label command");
            assert_eq!(c.argv[..4], ["chroot", "/mnt/bko-target", "sh", "-c"]);
            c.argv[4].clone()
        };
        let s = script("ext4");
        assert!(s.starts_with(SELINUX_SETFILES_GATE), "{s}");
        let want = format!(
            "r={RUN_DIR}/mount-points; {}",
            concat!(
                r#"mkdir -p "$r" && mount --bind / "$r" || exit 1; setfiles -r "$r" "$f" "$r"/boot "$r"/usr/local "$r"/var; s=$?; umount "$r"; [ $s = 0 ] || exit $s"#,
                r#"; mkdir -p "$r"/boot && mount --bind /boot "$r"/boot || exit 1; setfiles -r "$r" "$f" "$r"/boot/grub; s=$?; umount "$r"/boot; [ $s = 0 ] || exit $s"#,
                r#"; mkdir -p "$r"/var && mount --bind /var "$r"/var || exit 1; setfiles -r "$r" "$f" "$r"/var/lib/x; s=$?; umount "$r"/var; [ $s = 0 ] || exit $s"#,
            )
        );
        assert!(s.ends_with(&want), "{s}");
        assert!(!s.contains("--rbind") && !s.contains(" -R") && !s.contains("/run/x"), "{s}");
        // /boot an ESP (vfat): /boot itself is labelled on /, nothing under it.
        let s = script("vfat");
        assert!(s.contains(r#"setfiles -r "$r" "$f" "$r"/boot "$r"/usr/local "$r"/var;"#) && !s.contains("/boot/grub") && !s.contains("mount --bind /boot"), "{s}");
    }

    /// FAT's local-time stamps (BackShot1 artix-20261123-102328: Artix's
    /// hwclock --systz left sys_tz at -60, and boot/intel-ucode.img came
    /// back an hour early): the dump's sys_tz as time_offset=, or the
    /// source mount's own tz=/time_offset=, never over fstab's own.
    #[test]
    fn fat_time_options() {
        assert_eq!(fat_time_option("defaults", None, None), None);
        assert_eq!(fat_time_option("defaults", None, Some(0)).as_deref(), Some("tz=UTC"));
        assert_eq!(fat_time_option("umask=0077", None, Some(-60)).as_deref(), Some("time_offset=60"));
        assert_eq!(fat_time_option("defaults", Some("rw,relatime,rw,fmask=0022,dmask=0022,codepage=437"), Some(300)).as_deref(), Some("time_offset=-300"));
        assert_eq!(fat_time_option("defaults", Some("rw,relatime,rw,fmask=0022,tz=UTC,errors=remount-ro"), Some(-60)).as_deref(), Some("tz=UTC"));
        assert_eq!(fat_time_option("defaults", Some("rw,relatime,rw,time_offset=-120,errors=remount-ro"), None).as_deref(), Some("time_offset=-120"));
        assert_eq!(fat_time_option("umask=0077,tz=UTC", Some("rw,tz=UTC"), Some(-60)), None);
        assert_eq!(fat_time_option("time_offset=30", None, Some(-60)), None);
        // More than a day: vfat would refuse the mount.
        assert_eq!(fat_time_option("defaults", None, Some(-1500)), None);

        // fstab's type for /boot, the source's running /boot's fstype.
        let plan_ty = |ty: &str, live_ty: &str, sys_tz: Option<i64>, live: &str| {
            let mut j = sample::bios_mbr()
                .replace(r#""file": "/boot", "type": "ext4", "options": "defaults""#, &format!(r#""file": "/boot", "type": "{ty}", "options": "umask=0077""#))
                .replace(r#""mounts": [],"#, &format!(r#""mounts": [{{"target": "/boot", "source": "/dev/vda1", "fstype": "{live_ty}", "root": "/", "options": "rw,relatime", "super_options": "{live}"}}],"#));
            if let Some(m) = sys_tz {
                j = j.replace(r#""crypttab": null,"#, &format!(r#""crypttab": null, "sys_tz_minuteswest": {m},"#));
            }
            let b = built_from(&j, &[]);
            let c = b.stage(Stage::Mount).iter().find(|c| c.argv.first().map(String::as_str) == Some("mount") && c.argv.last().map(String::as_str) == Some("/mnt/bko-target/boot")).cloned().expect("the /boot mount");
            (c.argv[2..5].join(" "), c.why.unwrap_or_default())
        };
        let plan = |sys_tz: Option<i64>, live: &str| plan_ty("vfat", "vfat", sys_tz, live);
        let plain = "rw,fmask=0022,dmask=0022,codepage=437,iocharset=ascii,shortname=mixed,utf8,errors=remount-ro";
        assert_eq!(plan(None, plain).0, "vfat -o umask=0077");
        let (o, why) = plan(Some(0), plain);
        assert_eq!(o, "vfat -o umask=0077,tz=UTC");
        assert!(why.contains("whatever the rescue's kernel time zone"), "{why}");
        let (o, why) = plan(Some(-60), plain);
        assert_eq!(o, "vfat -o umask=0077,time_offset=60");
        assert!(why.contains("sys_tz tz_minuteswest -60"), "{why}");
        assert_eq!(plan(Some(300), plain).0, "vfat -o umask=0077,time_offset=-300");
        let (o, why) = plan(Some(-60), "rw,fmask=0022,tz=UTC");
        assert_eq!(o, "vfat -o umask=0077,tz=UTC");
        assert!(why.contains("as the source's /boot was mounted"), "{why}");
        assert_eq!(plan(None, "rw,time_offset=120").0, "vfat -o umask=0077,time_offset=120");
        // fstab's auto: by what the source had mounted there.
        assert_eq!(plan_ty("auto", "vfat", Some(-60), plain).0, "auto -o umask=0077,time_offset=60");
        assert_eq!(plan_ty("auto", "ext4", Some(-60), "rw").0, "auto -o umask=0077");
        assert_eq!(plan_ty("ext4", "ext4", Some(-60), "rw").0, "ext4 -o umask=0077");
    }

    /// PikaOS (docs/design/pikaos.md): booster's image remade for each
    /// kernel; rEFInd kept as restored, with only its boot entry made, and
    /// refind_linux.conf's UUIDs checked against the layout.
    #[test]
    fn pikaos_booster_and_refind() {
        let plan = |via_shim: bool, options: &str| {
            let j = sample::bios_mbr()
                .replace(r#""id": "debian", "version_id": "13""#, r#""id": "pika", "version_id": "4""#)
                .replace(r#""firmware": {"mode": "bios"}"#, r#""firmware": {"mode": "uefi"}"#)
                .replace(r#""loader": {"name": "grub", "firmware": "bios", "via_shim": false}"#, &format!(r#""loader": {{"name": "refind", "firmware": "uefi", "via_shim": {via_shim}, "package": "refind"}}"#))
                .replace(
                    r#""efi": null,"#,
                    &format!(
                        r#""efi": {{"boot_current": "0001", "boot_order": ["0001"], "entries": [{{"number": "0001", "description": "rEFInd Boot Manager", "file": "\\EFI\\refind\\refind_x64.efi", "partition": {{"device": "vda1"}}}}]}},
                           "esps": [{{"device": "vda1", "mounted_at": "/boot/efi", "contents": {{"files": [{{"path": "EFI/refind/refind_x64.efi"}}, {{"path": "EFI/BOOT/bootx64.efi"}}]}}}}],
                           "initramfs": {{"generator": "booster", "booster": {{"conf": "/etc/booster.yaml", "universal": false}}}}, "packages": {{"booster": "0.12.1", "refind": "0.14.2"}},
                           "refind": {{"linux_conf": {{"path": "/boot/refind_linux.conf", "lines": [{{"title": "Boot with standard options", "options": "{options}"}}]}}}},"#
                    ),
                );
            let b = built_from(&j, &[]);
            (text(b.stage(Stage::Boot)), b.notes.join("\n"), b.problems.clone())
        };
        let good = "nowatchdog rootflags=rw,relatime root=UUID=b7b7b7b7-0000-4000-8000-000000000003 quiet splash ---";
        let (t, n, p) = plan(false, good);
        assert!(p.is_empty(), "{p:?}");
        assert_eq!(t.matches("booster build").count(), 1, "{t}");
        assert!(t.contains(r#"chroot /mnt/bko-target sh -c 'n=0; for k in /lib/modules/*; do [ -d "$k/kernel" ] || continue; k=${k##*/}; booster build --force --kernel-version "$k" "/boot/booster.img-$k" || exit; n=$((n + 1)); done; [ "$n" -gt 0 ]'"#), "{t}");
        assert!(!t.contains("update-initramfs") && !t.contains("refind-install") && !t.contains("grub"), "{t}");
        assert!(t.contains("efibootmgr --quiet --create --disk /dev/vdb --part 1 --label 'rEFInd Boot Manager' --loader '\\EFI\\refind\\refind_x64.efi'\n"), "{t}");
        assert!(n.contains("rEFInd: its EFI files, drivers, theme and refind.conf came back with the ESP, and refind_linux.conf with /boot; nothing to install"), "{n}");
        // A UUID the layout doesn't make, as root= and as the LUKS one.
        let (_, _, p) = plan(false, "rd.luks.name=1111aaaa-2222-4333-8444-555566667777=crypt_root rd.luks.options=discard root=UUID=DEADBEEF-0000-4000-8000-000000000009 quiet");
        assert_eq!(
            p,
            vec![
                "rEFInd: /boot/refind_linux.conf has rd.luks.name=1111aaaa-2222-4333-8444-555566667777, which no filesystem or LUKS container in the layout has; the restored system couldn't find it at boot",
                "rEFInd: /boot/refind_linux.conf has root=DEADBEEF-0000-4000-8000-000000000009, which no filesystem or LUKS container in the layout has; the restored system couldn't find it at boot",
            ]
        );
        // The same UUID in another case is the same UUID; LUKS's by UUID= too.
        let (_, _, p) = plan(false, "rd.luks.name=UUID=5E5E5E5E-0000-4000-8000-000000000002=crypt_root root=UUID=B7B7B7B7-0000-4000-8000-000000000003");
        assert!(p.is_empty(), "{p:?}");
        // Through shim: refused by name, nothing kept quietly.
        let (_, n, p) = plan(true, good);
        assert_eq!(p, vec!["rEFInd through shim (Secure Boot): bko-holt load keeps only an unsigned rEFInd's files, and doesn't support it"]);
        assert!(!n.contains("rEFInd: its EFI files"), "{n}");
    }

    /// NixOS on UEFI (BackShot1's Thinkpad, NixOS 26.05): bootctl and
    /// grub-install are only on PATH through /run/current-system, which the
    /// chroot hasn't; the ESP came back with the loader, its entries and
    /// every generation's files.  Only the NVRAM entry is made.
    #[test]
    fn nixos_uefi_runs_nothing_in_the_chroot() {
        let plan = |loader: &str, file: &str, esp_files: &str, extra: &[&str]| {
            let j = sample::bios_mbr()
                .replace(r#""id": "debian", "version_id": "13""#, r#""id": "nixos", "version_id": "26.05""#)
                .replace(r#""firmware": {"mode": "bios"}"#, r#""firmware": {"mode": "uefi"}"#)
                .replace(r#""loader": {"name": "grub", "firmware": "bios", "via_shim": false}"#, &format!(r#""loader": {{"name": "{loader}", "firmware": "uefi", "via_shim": false}}"#))
                .replace(r#""grub": {"debconf": {"grub-pc/install_devices": "/dev/disk/by-id/virtio-x"}}"#, r#""grub": {"dir": "/boot/grub", "debconf": null}"#)
                .replace(
                    r#""efi": null,"#,
                    &format!(
                        r#""efi": {{"boot_current": "0001", "boot_order": ["0001"], "entries": [{{"number": "0001", "description": "Linux Boot Manager", "file": "{}", "partition": {{"device": "vda1"}}}}]}},
                           "esps": [{{"device": "vda1", "mounted_at": "/boot", "contents": {{"files": [{esp_files}]}}}}],"#,
                        file.replace('\\', "\\\\")
                    ),
                );
            let b = built_from(&j, extra);
            (text(b.stage(Stage::Boot)), b.notes.join("\n"), b.problems.clone())
        };
        let sd_files = r#"{"path": "EFI/systemd/systemd-bootx64.efi"}, {"path": "EFI/BOOT/BOOTX64.EFI"}, {"path": "loader/loader.conf"}, {"path": "loader/entries/nixos-generation-1.conf"}"#;
        let (t, n, p) = plan("systemd-boot", r"\EFI\systemd\systemd-bootx64.efi", sd_files, &[]);
        assert!(p.is_empty(), "{p:?}");
        assert!(!t.contains("bootctl") && !t.contains("gen-systemd-boot") && !t.contains("kernel-install"), "{t}");
        assert!(!t.lines().any(|l| l.starts_with("chroot ") || l.contains("; chroot ")), "{t}");
        assert!(t.contains("efibootmgr --quiet --create --disk /dev/vdb --part 1 --label 'Linux Boot Manager' --loader '\\EFI\\systemd\\systemd-bootx64.efi'\n"), "{t}");
        assert!(n.contains("NixOS: systemd-boot, its loader entries and every generation's kernel and initrd came back with the ESP; only the boot entry is made"), "{n}");
        assert!(!n.contains("/boot/kernels"), "{n}");
        assert!(!t.contains("BOOTX64"), "{t}");
        // --removable: the fallback copy, made outside the chroot unless there.
        let (t, _, _) = plan("systemd-boot", r"\EFI\systemd\systemd-bootx64.efi", sd_files, &["--removable"]);
        assert!(t.contains("[ -e /mnt/bko-target/boot/EFI/BOOT/BOOTX64.EFI ] || { mkdir -p /mnt/bko-target/boot/EFI/BOOT && cp /mnt/bko-target/boot/EFI/systemd/systemd-bootx64.efi /mnt/bko-target/boot/EFI/BOOT/BOOTX64.EFI; }"), "{t}");
        // --no-nvram: no entry either.
        let (t, _, _) = plan("systemd-boot", r"\EFI\systemd\systemd-bootx64.efi", sd_files, &["--no-nvram"]);
        assert!(!t.contains("efibootmgr"), "{t}");
        // UEFI GRUB: neither grub-install nor grub-mkconfig.
        let grub_files = r#"{"path": "EFI/NixOS-boot/grubx64.efi"}"#;
        let (t, n, p) = plan("grub", r"\EFI\NixOS-boot\grubx64.efi", grub_files, &["--removable"]);
        assert!(p.is_empty(), "{p:?}");
        assert!(!t.contains("grub-install") && !t.contains("grub-mkconfig") && !t.contains("update-grub"), "{t}");
        assert!(!t.lines().any(|l| l.starts_with("chroot ") || l.contains("; chroot ")), "{t}");
        assert!(t.contains("--label 'Linux Boot Manager' --loader '\\EFI\\NixOS-boot\\grubx64.efi'\n"), "{t}");
        assert!(t.contains("cp /mnt/bko-target/boot/EFI/NixOS-boot/grubx64.efi /mnt/bko-target/boot/EFI/BOOT/BOOTX64.EFI"), "{t}");
        assert!(n.contains("NixOS: UEFI GRUB's EFI image and grub.cfg came back"), "{n}");
    }

    /// GRUB's environment block in btrfs's reserved area (openSUSE's
    /// env_block=512+1): written again after mkfs.btrfs, the source's
    /// variables in it, to the device that had it; none when the dump has
    /// none.
    #[test]
    fn btrfs_grub_env_block() {
        let j = sample::bios_mbr().replace(r#""content": {"type": "btrfs", "usage": "filesystem","#, r#""content": {"type": "btrfs", "usage": "filesystem", "grub_env_block": {"next_entry": "1"},"#);
        assert_ne!(j, sample::bios_mbr());
        let b = built_from(&j, &[]);
        assert!(b.problems.is_empty(), "{:?}", b.problems);
        let f = text(b.stage(Stage::Format));
        let block = format!("# GRUB Environment Block\nnext_entry=1\n{}", "#".repeat(512 - 38));
        let dd = format!("# GRUB's environment block in btrfs's reserved area (grubenv's env_block=512+1), as the source's: mkfs.btrfs zeroed it\n\
                          dd of=/dev/vdb3 bs=512 seek=512 count=1 iflag=fullblock conv=notrunc,fsync status=none <<'EOF'\n{block}\nEOF\n");
        let at = f.find(&dd).unwrap_or_else(|| panic!("{f}"));
        assert!(f.find("mkfs.btrfs").unwrap() < at && at < f.find("subvolume create").unwrap_or(usize::MAX), "{f}");
        let c = b.stage(Stage::Format).iter().find(|c| c.argv.first().is_some_and(|a| a == "dd") && c.argv.iter().any(|a| a == "seek=512")).unwrap();
        assert_eq!(c.stdin.as_deref().map(str::len), Some(512));
        // An empty one: the signature and padding.
        let e = grub_env_block("/dev/vdb3", &crate::json::parse("{}").unwrap()).unwrap();
        assert_eq!(e.stdin.unwrap(), format!("# GRUB Environment Block\n{}", "#".repeat(512 - 25)));
        // More than a sector: refused by name.
        let big = crate::json::parse(&format!(r#"{{"x": "{}"}}"#, "y".repeat(600))).unwrap();
        assert!(grub_env_block("/dev/vdb3", &big).unwrap_err().contains("more than its 512"));
        // None in the dump: nothing written.
        assert!(!text(built(&[]).stage(Stage::Format)).contains("seek=512"));
    }

    /// The resume device kept where the boot stage remakes the initramfs
    /// with a generator that picks it from this machine's swap: an
    /// initramfs-tools source with RESUME unset or auto and a swap device,
    /// and EndeavourOS's dracut-rebuild with one.
    #[test]
    fn initramfs_resume_kept() {
        let it = |extra: &str| sample::bios_mbr().replace(r#""efi": null,"#, &format!(r#""efi": null, "initramfs": {{"generator": "initramfs-tools"{extra}}},"#));
        let wrapped = format!("chroot /mnt/bko-target sh -c {}\n", shell_quote(UPDATE_INITRAMFS_RESUME));
        let plain = "chroot /mnt/bko-target update-initramfs -u -k all\n";
        for (extra, want) in [("", &wrapped), (r#", "resume": null"#, &wrapped), (r#", "resume": "auto""#, &wrapped), (r#", "resume": "UUID=5e5e5e5e-0000-4000-8000-000000000002""#, &plain.to_string()), (r#", "resume": "none""#, &plain.to_string())] {
            let bt = text(built_from(&it(extra), &[]).stage(Stage::Boot));
            assert!(bt.contains(want.as_str()), "{extra}: {bt}");
        }
        // A swap file, or no swap: nothing for the hook to pick.
        let file = it("").replace(r#""spec": "UUID=5e5e5e5e-0000-4000-8000-000000000002", "file": "none""#, r#""spec": "/swap.img", "file": "none""#);
        assert!(text(built_from(&file, &[]).stage(Stage::Boot)).contains(plain));
        let eos = sample::bios_mbr()
            .replace(r#""id": "debian", "version_id": "13""#, r#""id": "endeavouros""#)
            .replace(r#""efi": null,"#, r#""efi": null, "initramfs": {"generator": "dracut"}, "packages": {"eos-dracut": "1.7-1"},"#);
        assert!(text(built_from(&eos, &[]).stage(Stage::Boot)).contains(&format!("chroot /mnt/bko-target sh -c {}\n", shell_quote(DRACUT_REBUILD_RESUME))));
        let eos = eos.replace(r#""spec": "UUID=5e5e5e5e-0000-4000-8000-000000000002", "file": "none""#, r#""spec": "/swap/swapfile", "file": "none""#);
        assert!(text(built_from(&eos, &[]).stage(Stage::Boot)).contains("chroot /mnt/bko-target dracut-rebuild\n"));
    }

    #[test]
    fn partition_script() {
        let b = built(&[]);
        let t = text(b.stage(Stage::Partition));
        assert!(t.contains("wipefs --all /dev/vdb\n"), "{t}");
        assert!(t.contains("label: dos\nlabel-id: 0x1f2e3d4c\n"), "{t}");
        assert!(t.contains("/dev/vdb1 : start=2048, size=1953792, type=83, bootable\n"), "{t}");
        assert!(t.contains("/dev/vdb3 : start=5861376, size=27693056, type=83\n"), "{t}");
        assert!(t.ends_with("udevadm settle\n"));
    }

    /// The (seek, count) in sectors of each dd of `dev` the commands make.
    fn dds(cmds: &[Cmd], dev: &str) -> Vec<(u64, u64)> {
        let arg = |c: &Cmd, k: &str| c.argv.iter().find_map(|a| a.strip_prefix(k)).map(|v| v.parse::<u64>().unwrap());
        cmds.iter().filter(|c| c.argv.first().map(String::as_str) == Some("dd") && c.argv.contains(&format!("of={dev}"))).map(|c| (arg(c, "seek=").unwrap_or(0), arg(c, "count=").unwrap())).collect()
    }

    /// The disk's first 4 MiB and last 1 MiB, then each new partition's,
    /// all after wipefs (whose O_EXCL open refuses a busy disk) and before
    /// sfdisk writes the table, so nothing old is on a partition when the
    /// kernel first shows it, let alone when assemble or format writes to it.
    #[test]
    fn partition_zeroes_the_disk_then_each_new_partition() {
        let b = built(&[]);
        let cmds = b.stage(Stage::Partition);
        let t = text(cmds);
        let at = |needle: &str| t.find(needle).unwrap_or_else(|| panic!("{needle:?} not in:\n{t}"));
        let order = [
            "wipefs --all /dev/vdb\n",
            "dd if=/dev/zero of=/dev/vdb bs=1M count=4 conv=fsync\n",
            "sh -c 'n=$(blockdev --getsz /dev/vdb) && dd if=/dev/zero of=/dev/vdb bs=512 seek=$((n - 2048)) count=2048 conv=fsync'\n",
            "# vda1: its first 4 MiB and its last 1 MiB\ndd if=/dev/zero of=/dev/vdb bs=512 seek=2048 count=8192 conv=fsync\n",
            "dd if=/dev/zero of=/dev/vdb bs=512 seek=1953792 count=2048 conv=fsync\n",
            "dd if=/dev/zero of=/dev/vdb bs=512 seek=1955840 count=8192 conv=fsync\n",
            "dd if=/dev/zero of=/dev/vdb bs=512 seek=5859328 count=2048 conv=fsync\n",
            "dd if=/dev/zero of=/dev/vdb bs=512 seek=5861376 count=8192 conv=fsync\n",
            "dd if=/dev/zero of=/dev/vdb bs=512 seek=33552384 count=2048 conv=fsync\n",
            "udevadm settle\nsfdisk ",
        ];
        for w in order.windows(2) {
            assert!(at(w[0]) < at(w[1]), "{:?} before {:?}:\n{t}", w[0], w[1]);
        }
        // Each partition's ranges lie inside it, on the disk's node (never
        // a partition's, which the kernel may still have from the old table).
        let l = Layout::from_json(crate::json::parse(&sample::bios_mbr()).unwrap()).unwrap();
        let parts = &l.disks[0].parts;
        let zs = dds(cmds, "/dev/vdb");
        assert_eq!(zs.len(), 1 + 2 * parts.len(), "{zs:?}");
        for &(seek, count) in &zs[1..] {
            assert!(parts.iter().any(|p| seek >= p.start_lba && seek + count <= p.start_lba + p.sectors), "{seek}+{count} isn't inside a partition");
        }
        assert!(!cmds.iter().any(|c| c.argv.iter().any(|a| a.starts_with("of=/dev/vdb") && a != "of=/dev/vdb")), "{t}");
        // Nothing later zeroes: assemble and format write to what's clean.
        for s in [Stage::Assemble, Stage::Format, Stage::Mount, Stage::Boot] {
            assert!(!text(b.stage(s)).contains("if=/dev/zero"), "{}", text(b.stage(s)));
        }
    }

    /// A partition inside the disk's first 4 MiB (a 1 MiB BIOS boot) is
    /// zeroed by the disk's own; one smaller than 5 MiB is zeroed whole; on
    /// 4096-byte sectors the counts are the disk's sectors.
    #[test]
    fn partition_zeroes_small_partitions_whole() {
        let j = std::fs::read_to_string(concat!(env!("CARGO_MANIFEST_DIR"), "/tests/corpus/rocky10-lvm/src.json")).unwrap();
        let b = built_from(&j, &[]);
        let zs = dds(b.stage(Stage::Partition), "/dev/vdb");
        // vda1 is sectors 2048-4095: no dd of its own.
        assert!(!zs.iter().any(|&(s, _)| s == 2048), "{zs:?}");
        assert!(zs.contains(&(4096, 8192)), "{zs:?}");
        let l = Layout::from_json(crate::json::parse(&j).unwrap()).unwrap();
        let d = l.disk("vda").unwrap();
        let mut d4 = d.clone();
        d4.sector_size = 4096;
        d4.parts = vec![Part { start_lba: 256, sectors: 1024, ..d.parts[1].clone() }, Part { start_lba: 2048, sectors: 1280, ..d.parts[1].clone() }];
        let z4 = dds(&zero_parts(&d4, "/dev/vdb"), "/dev/vdb");
        // The first is 4 MiB (from 1 MiB in): all of it.  The second is 5 MiB:
        // its first 4 and its last 1.
        assert_eq!(z4, vec![(256, 1024), (2048, 1024), (3072, 256)]);
    }

    /// Only the disks the plan maps are zeroed: a layout's other disk,
    /// mapped nowhere, is touched by nothing.
    #[test]
    fn partition_zeroes_only_mapped_disks() {
        let j = std::fs::read_to_string(concat!(env!("CARGO_MANIFEST_DIR"), "/tests/corpus/debian13-stack/src.json")).unwrap();
        let b = built_from(&j, &[]);
        let cmds = b.stage(Stage::Partition);
        let zeroed: Vec<&String> = cmds.iter().filter(|c| c.argv.first().map(String::as_str) == Some("dd")).flat_map(|c| c.argv.iter().filter(|a| a.starts_with("of="))).collect();
        assert!(!zeroed.is_empty() && zeroed.iter().all(|a| *a == "of=/dev/vdb"), "{zeroed:?}");
        let t = text(cmds);
        assert_eq!(t.matches("blockdev --getsz").count(), 1, "{t}");
    }

    #[test]
    fn filesystems() {
        let b = built(&[]);
        let t = text(b.stage(Stage::Format));
        assert!(t.contains("mke2fs -F -q -t ext4 -O none,has_journal,ext_attr,resize_inode,dir_index,filetype,extent,64bit,flex_bg,sparse_super,large_file,huge_file,dir_nlink,extra_isize,metadata_csum -U 0b0e2c6e-1111-4222-8333-444455556666"), "{t}");
        assert!(!t.contains("needs_recovery"));
        assert!(t.contains("-E hash_seed=9a6c1c0e-aaaa-4bbb-8ccc-dddddddddddd /dev/vdb1"), "{t}");
        assert!(t.contains("tune2fs -r 12211 /dev/vdb1\n"), "{t}");
        assert!(!t.contains(" -m 5"), "{t}");
        assert!(t.contains("mkswap -U 5e5e5e5e-0000-4000-8000-000000000002 --pagesize 4096 /dev/vdb2"), "{t}");
        assert!(t.contains("mkfs.btrfs -f -O extref,skinny-metadata,no-holes,^raid56,^raid1c34,^zoned,^squota,free-space-tree,block-group-tree -U b7b7b7b7-0000-4000-8000-000000000003 --csum crc32c -n 16384 -s 4096 -d single -m dup /dev/vdb3"), "{t}");
        assert!(t.contains(&format!("btrfs subvolume create {RUN_DIR}/btrfs/b7b7b7b7-0000-4000-8000-000000000003/@rootfs")), "{t}");
        assert!(t.contains(&format!("btrfs property set {RUN_DIR}/btrfs/b7b7b7b7-0000-4000-8000-000000000003/@rootfs compression zstd")), "{t}");
        assert!(b.problems.is_empty(), "{:?}", b.problems);
    }

    /// The sample's swap as Ubuntu's installer encrypts it (26.10, ZFS +
    /// encryption): plain dm-crypt dm-0 on vda2, which holds no header, and
    /// crypttab's `dm_crypt-0 PARTUUID=... /dev/urandom ...,plain,swap`,
    /// fstab naming the mapping.  crypttab makes it at every boot with a
    /// random key and mkswaps it: load makes the partition and nothing on
    /// it, adds no keyslot, and notes it.  With a real key file it stays a
    /// Problem, as does a LUKS one's `swap` option (no PLAIN).
    #[test]
    fn plain_dm_crypt_swap_with_a_random_key_is_left_to_crypttab() {
        let layout = |key: &str, crypt_type: &str| {
            sample::bios_mbr()
                .replace(
                    r#""content": {"type": "swap", "usage": "other", "uuid": "5e5e5e5e-0000-4000-8000-000000000002", "label": null, "page_size": 4096}"#,
                    r#""content": null"#,
                )
                .replace(
                    r#""dm_devices": []"#,
                    &format!(
                        r#""dm_devices": [{{"name": "dm-0", "kind": "dm", "size_bytes": 1999634432, "slaves": ["vda2"], "holders": [],
                            "content": {{"type": "swap", "usage": "other", "uuid": "de0ff632-a412-4f03-bc76-41b6f2566ced", "label": "dm_crypt-0", "version": "1"}},
                            "dm": {{"dm_name": "dm_crypt-0", "dm_uuid": "CRYPT-{crypt_type}-dm_crypt-0", "mapper_path": "/dev/mapper/dm_crypt-0", "target": "crypt", "crypt_type": "{crypt_type}"}}}}]"#
                    ),
                )
                .replace(
                    r#"{"spec": "UUID=5e5e5e5e-0000-4000-8000-000000000002", "file": "none", "type": "swap", "options": "sw", "freq": 0, "passno": 0, "resolves_to": "vda2"}"#,
                    r#"{"spec": "/dev/disk/by-id/dm-uuid-CRYPT-PLAIN-dm_crypt-0", "file": "none", "type": "swap", "options": "sw", "freq": 0, "passno": 0, "resolves_to": "dm-0"}"#,
                )
                .replace(
                    r#""crypttab": null"#,
                    &format!(r#""crypttab": [{{"name": "dm_crypt-0", "device": "/dev/vda2", "key_file": "{key}", "options": ["cipher=aes-cbc-essiv:sha256", "initramfs", "plain", "size=256", "swap"], "resolves_to": "vda2"}}]"#),
                )
        };
        let j = layout("/dev/urandom", "PLAIN");
        assert!(j.contains("CRYPT-PLAIN") && j.contains("\"resolves_to\": \"dm-0\"") && j.contains("\"content\": null"));
        let b = built_from(&j, &[]);
        assert!(b.problems.is_empty(), "{:?}", b.problems);
        let all: String = b.stages.iter().map(|(_, c)| text(c)).collect();
        assert!(!all.contains("cryptsetup") && !all.contains("dm_crypt-0") && !all.contains("mkswap"), "{all}");
        assert!(b.notes.iter().any(|n| n
            == "dm-0 (dm_crypt-0): plain dm-crypt that crypttab makes at every boot with a random key from /dev/urandom (swap): nothing is made on vda2 but the partition (its ends zeroed, as every new partition's); the restored system's crypttab makes it at its first boot"), "{:?}", b.notes);
        let part = text(b.stage(Stage::Partition));
        assert!(part.contains("vda2: its first 4 MiB and its last 1 MiB"), "{part}");
        // A real key: plain dm-crypt load can't make.
        let b = built_from(&layout("/etc/keys/swap.key", "PLAIN"), &[]);
        assert!(b.problems.iter().any(|p| p == "dm-0: vda2 doesn't hold a LUKS header in the dump (plain dm-crypt isn't supported)"), "{:?}", b.problems);
        let b = built_from(&layout("/dev/urandom", "LUKS2"), &[]);
        assert!(b.problems.iter().any(|p| p.contains("doesn't hold a LUKS header")), "{:?}", b.problems);
    }

    /// NixOS's graphical installer (BackShot1's Thinkpad, NixOS 26.05): /
    /// is the btrfs's top level (subvolid=5), /home and /nix subvolumes
    /// under it, and srv, var/lib/portables, var/lib/machines, tmp and
    /// var/tmp subvolumes nested in its tree, mounted nowhere.
    fn nixos_top_level(subvolumes: &str) -> String {
        let fs = "b7b7b7b7-0000-4000-8000-000000000003";
        sample::bios_mbr()
            .replace(r#""id": "debian", "version_id": "13""#, r#""id": "nixos", "version_id": "26.05""#)
            .replace(
                r#""subvolumes": [{"id": 256, "top_level": 5, "path": "<FS_TREE>/@rootfs", "readonly": false, "properties": {"compression": "zstd"}}],"#,
                &format!(r#""mounted_subvolumes": [{{"target": "/", "subvol": "/"}}, {{"target": "/home", "subvol": "/home"}}, {{"target": "/nix", "subvol": "/nix"}}], "subvolumes": {subvolumes},"#),
            )
            .replace(
                &format!(r#"{{"spec": "UUID={fs}", "file": "/", "type": "btrfs", "options": "defaults,subvol=@rootfs", "freq": 0, "passno": 0, "resolves_to": "vda3"}},"#),
                &format!(
                    r#"{{"spec": "/dev/disk/by-uuid/{fs}", "file": "/", "type": "btrfs", "options": "defaults", "freq": 0, "passno": 0, "resolves_to": "vda3"}},
                       {{"spec": "/dev/disk/by-uuid/{fs}", "file": "/home", "type": "btrfs", "options": "subvol=home", "freq": 0, "passno": 0, "resolves_to": "vda3"}},
                       {{"spec": "/dev/disk/by-uuid/{fs}", "file": "/nix", "type": "btrfs", "options": "subvol=nix,noatime", "freq": 0, "passno": 0, "resolves_to": "vda3"}},"#
                ),
            )
    }

    #[test]
    fn btrfs_top_level_root_makes_its_subvolumes() {
        let subs = [(256, "home"), (257, "nix"), (258, "srv"), (259, "var/lib/portables"), (260, "var/lib/machines"), (261, "tmp"), (262, "var/tmp")];
        let list: Vec<String> = subs
            .iter()
            .map(|(id, p)| format!(r#"{{"id": {id}, "gen": 9, "top_level": 5, "path": "{p}", "parent_uuid": null, "uuid": "u{id}", "readonly": false, "properties": {{"ro": "false"}}}}"#))
            .collect();
        let b = built_from(&nixos_top_level(&format!("[{}]", list.join(", "))), &["--repo", "/srv/repo", "--password-file", "/run/pw"]);
        assert!(b.problems.is_empty(), "{:?}", b.problems);
        let f = text(b.stage(Stage::Format));
        let top = format!("{RUN_DIR}/btrfs/b7b7b7b7-0000-4000-8000-000000000003");
        let at = |l: &str| f.find(&format!("{l}\n")).unwrap_or_else(|| panic!("{l} not in\n{f}"));
        let mut prev = at(&format!("mount -t btrfs -o subvolid=5 /dev/vdb3 {top}"));
        assert!(at("mkfs.btrfs -f -O extref,skinny-metadata,no-holes,^raid56,^raid1c34,^zoned,^squota,free-space-tree,block-group-tree -U b7b7b7b7-0000-4000-8000-000000000003 --csum crc32c -n 16384 -s 4096 -d single -m dup /dev/vdb3") < prev);
        // Each after the one before, in ID order, its parent directory made
        // just before it.
        for (_, p) in subs {
            let after = |l: &str, from: usize| f[from..].find(&format!("{l}\n")).map(|i| i + from).unwrap_or_else(|| panic!("{l} not after {from} in\n{f}"));
            if let Some((parent, _)) = p.rsplit_once('/') {
                prev = after(&format!("mkdir -p {top}/{parent}"), prev);
            }
            prev = after(&format!("btrfs subvolume create {top}/{p}"), prev);
        }
        assert!(at(&format!("umount {top}")) > prev);
        assert!(!f.contains("property set"), "{f}");
        // The mount stage: the top level, then /boot, /home and /nix.
        let m = text(b.stage(Stage::Mount));
        let root = m.find("mount -t btrfs -o defaults /dev/vdb3 /mnt/bko-target\n").expect(&m);
        let home = m.find("mount -t btrfs -o subvol=home /dev/vdb3 /mnt/bko-target/home\n").expect(&m);
        let nix = m.find("mount -t btrfs -o subvol=nix,noatime /dev/vdb3 /mnt/bko-target/nix\n").expect(&m);
        assert!(root < home && root < nix, "{m}");
        // The restore fills the top level and both mounts, leaving nothing
        // out: /home and /nix are covered by their own mounts, not aliases.
        let r = text(b.stage(Stage::Restore));
        assert!(r.contains("restore --numeric-id latest /mnt/bko-target\n"), "{r}");
        assert!(!r.contains("--glob-file") && !r.contains("--target-metadata"), "{r}");
    }

    /// The Thinkpad's layout as dumped before NixOS's tools were on the
    /// dump's path: no subvolume list.  The subvolumes its mounts name are
    /// made, parents first; the rest come back as directories.
    #[test]
    fn btrfs_unlisted_subvolumes_from_its_mounts() {
        let b = built_from(&nixos_top_level("null"), &[]);
        assert!(b.problems.is_empty(), "{:?}", b.problems);
        let f = text(b.stage(Stage::Format));
        let top = format!("{RUN_DIR}/btrfs/b7b7b7b7-0000-4000-8000-000000000003");
        let mnt = f.find(&format!("mount -t btrfs -o subvolid=5 /dev/vdb3 {top}\n")).expect(&f);
        let home = f.find(&format!("btrfs subvolume create {top}/home\n")).expect(&f);
        let nix = f.find(&format!("btrfs subvolume create {top}/nix\n")).expect(&f);
        assert!(mnt < home && home < nix && nix < f.find(&format!("umount {top}\n")).unwrap(), "{f}");
        assert_eq!(f.matches("btrfs subvolume create").count(), 2, "{f}");
        let n = b.notes.join("\n");
        assert!(n.contains("doesn't list its subvolumes (its dump couldn't run btrfs); made are those its mounts name, home, nix"), "{n}");
        // A layout that lists none (an empty list) makes none.
        let b = built_from(&nixos_top_level("[]"), &[]);
        assert!(!text(b.stage(Stage::Format)).contains("subvolume create"));
    }

    #[test]
    fn named_subvolumes_parents_first() {
        let j = nixos_top_level("null").replace(r#""options": "subvol=home""#, r#""options": "subvol=/@/home""#).replace(r#""options": "subvol=nix,noatime""#, r#""options": "subvol=<FS_TREE>/@,noatime""#);
        let f = text(built_from(&j, &[]).stage(Stage::Format));
        let top = format!("{RUN_DIR}/btrfs/b7b7b7b7-0000-4000-8000-000000000003");
        // fstab's and those mounted at the dump: a parent before what's in it.
        let order: Vec<usize> = ["@", "@/home", "home", "nix"].iter().map(|p| f.find(&format!("btrfs subvolume create {top}/{p}\n")).expect(&f)).collect();
        assert!(order.windows(2).all(|w| w[0] < w[1]), "{f}");
    }

    #[test]
    fn timeshift_snapshots_are_told_apart() {
        use crate::load::shared::Mount;
        let m = |t: &str, s: &str| Mount { target: t.into(), subvol: s.into() };
        let ms = [m("/", "@"), m("/home", "@home")];
        assert_eq!(timeshift_snapshot("timeshift-btrfs/snapshots/2026-11-14_10-00-01/@", &ms), Some("2026-11-14_10-00-01"));
        assert_eq!(timeshift_snapshot("timeshift-btrfs/snapshots/2026-11-14_10-00-01/@home", &ms), Some("2026-11-14_10-00-01"));
        for p in ["timeshift-btrfs/snapshots/X/home", "timeshift-btrfs/snapshots/X/@/var", "timeshift-btrfs/snapshots//@", "timeshift-btrfs/X/@", "@/timeshift-btrfs/snapshots/X/@", "@snapshots/1/snapshot"] {
            assert_eq!(timeshift_snapshot(p, &ms), None, "{p}");
        }
        // Mounted, or in the backup through a mount of the top level: not left out.
        let p = "timeshift-btrfs/snapshots/X/@";
        assert_eq!(timeshift_snapshot(p, &[m("/", "@"), m("/mnt/old", p)]), None);
        assert_eq!(timeshift_snapshot(p, &[m("/", "@"), m("/mnt/top", "")]), None);
    }

    /// BigLinux's btrfs with two Timeshift snapshots of / (one read-only,
    /// with a qgroup), Arch's @snapshots unmounted, another Timeshift-looking
    /// subvolume mounted at /mnt/old, and @var made after them all.
    fn timeshift_layout(default_id: u64) -> String {
        let fs = "b7b7b7b7-0000-4000-8000-000000000003";
        let sub = |id: u64, p: &str, parent: &str, ro: bool| format!(r#"{{"id": {id}, "gen": {id}, "top_level": 5, "path": "<FS_TREE>/{p}", "uuid": "u{id}", "parent_uuid": {parent}, "readonly": {ro}, "properties": null}}"#);
        let subs = [
            sub(256, "@rootfs", "null", false),
            sub(257, "@snapshots", "null", false),
            sub(258, "timeshift-btrfs/snapshots/2026-11-14_10-00-01/@rootfs", "\"u256\"", true),
            sub(260, "timeshift-btrfs/snapshots/2026-11-15_10-00-01/@rootfs", "\"u256\"", false),
            sub(261, "timeshift-btrfs/snapshots/2026-11-01_00-00-01/@", "null", false),
            sub(262, "@var", "null", false),
        ];
        sample::bios_mbr()
            .replace(
                r#""subvolumes": [{"id": 256, "top_level": 5, "path": "<FS_TREE>/@rootfs", "readonly": false, "properties": {"compression": "zstd"}}],"#,
                &format!(r#""subvolumes": [{}],"#, subs.join(", ")),
            )
            .replace(r#""default_subvolume": {"id": 5}, "quota_enabled": false, "qgroups": null"#, &format!(
                r#""default_subvolume": {{"id": {default_id}}}, "quota_enabled": true, "qgroups": [{{"qgroupid": "0/256", "parents": ["1/0"]}}, {{"qgroupid": "0/258", "parents": ["1/0"], "max_referenced": 1000}}, {{"qgroupid": "1/0", "parents": []}}]"#
            ))
            .replace(
                r#""resolves_to": "vda3"},"#,
                &format!(
                    r#""resolves_to": "vda3"}},
      {{"spec": "UUID={fs}", "file": "/var", "type": "btrfs", "options": "subvol=@var", "freq": 0, "passno": 0, "resolves_to": "vda3"}},
      {{"spec": "UUID={fs}", "file": "/mnt/old", "type": "btrfs", "options": "subvol=timeshift-btrfs/snapshots/2026-11-01_00-00-01/@", "freq": 0, "passno": 0, "resolves_to": "vda3"}},"#
                ),
            )
    }

    /// Timeshift's snapshots (Decision 3 of docs/design/biglinux.md): not
    /// in the backup, so neither their subvolumes nor their NAME
    /// directories are made, nor their qgroups, IDs or read-only flags; one
    /// note names them.  An unmounted subvolume that isn't one, and a
    /// mounted one under timeshift-btrfs, are made as before.
    #[test]
    fn timeshift_snapshots_are_not_made() {
        let j = timeshift_layout(5);
        let b = built_from(&j, &["--repo", "/srv/repo", "--password-file", "/run/pw"]);
        assert!(b.problems.is_empty(), "{:?}", b.problems);
        let f = text(b.stage(Stage::Format));
        let top = format!("{RUN_DIR}/btrfs/b7b7b7b7-0000-4000-8000-000000000003");
        for p in ["@rootfs", "@snapshots", "timeshift-btrfs/snapshots/2026-11-01_00-00-01/@", "@var"] {
            assert!(f.contains(&format!("btrfs subvolume create {top}/{p}\n")), "{p} not made in\n{f}");
        }
        assert!(f.contains(&format!("mkdir -p {top}/timeshift-btrfs/snapshots/2026-11-01_00-00-01\n")), "{f}");
        assert!(!f.contains("2026-11-14") && !f.contains("2026-11-15") && !f.contains("subvolume snapshot"), "{f}");
        // Their IDs burnt, so that the mounted one and @var keep theirs.
        for n in 258..=260 {
            assert!(f.contains(&format!("btrfs subvolume create {top}/.bko-holt-placeholder-{n}\n")), "{n} in\n{f}");
        }
        assert!(!f.contains("placeholder-261") && !f.contains("placeholder-262"), "{f}");
        // Quotas: / 's qgroup in 1/0; the left-out snapshot's not at all.
        assert!(f.contains(&format!("btrfs qgroup assign --no-rescan 0/256 1/0 {top}\n")) && !f.contains("0/258"), "{f}");
        let n = b.notes.join("\n");
        assert!(n.contains("btrfs b7b7b7b7-0000-4000-8000-000000000003: 2 Timeshift snapshots (2026-11-14_10-00-01, 2026-11-15_10-00-01) are not in the backup; not made"), "{n}");
        assert_eq!(b.notes.iter().filter(|x| x.contains("Timeshift")).count(), 1, "{n}");
        assert!(!n.contains("snapshot of a subvolume that's gone"), "{n}");
        // No shared snapshot for them, and no read-only flag.
        let r = text(b.stage(Stage::Restore));
        assert!(!r.contains("--exact") && !r.contains("timeshift"), "{r}");
        let bt = text(b.stage(Stage::Boot));
        assert!(!bt.contains("timeshift") && !bt.contains("ro true"), "{bt}");
        // The default subvolume being one of them is a problem.
        let b = built_from(&timeshift_layout(258), &[]);
        assert_eq!(b.problems, vec!["btrfs b7b7b7b7-0000-4000-8000-000000000003: the default subvolume (ID 258) is a Timeshift snapshot, which isn't in the backup".to_string()]);
    }

    /// BigLinux's btrfs-fix: on a btrfs /boot, /boot and /usr/share/grub
    /// defragmented uncompressed once grub.cfg is written; not with an
    /// ext4 /boot, nor on another system.
    #[test]
    fn biglinux_boot_uncompressed() {
        let big = sample::bios_mbr().replace(r#""id": "debian", "version_id": "13""#, r#""id": "biglinux""#);
        let defrag = "btrfs filesystem defragment --nocomp -r /mnt/bko-target";
        let bt = text(built_from(&big, &[]).stage(Stage::Boot));
        assert!(!bt.contains("defragment"), "{bt}");
        let boot_line = r#"{"spec": "UUID=0b0e2c6e-1111-4222-8333-444455556666", "file": "/boot", "type": "ext4", "options": "defaults", "freq": 0, "passno": 2, "resolves_to": "vda1"},"#;
        assert!(big.contains(boot_line));
        let b = built_from(&big.replace(boot_line, ""), &[]);
        let bt = text(b.stage(Stage::Boot));
        let cfg = bt.find("grub-mkconfig -o /boot/grub/grub.cfg\n").expect(&bt);
        let boot = bt.find(&format!("{defrag}/boot\n")).expect(&bt);
        let grub = bt.find(&format!("{defrag}/usr/share/grub\n")).expect(&bt);
        assert!(cfg < boot && boot < grub, "{bt}");
        assert!(b.stage(Stage::Boot).iter().any(|c| c.why.as_deref() == Some("/boot uncompressed, as BigLinux's installer leaves it (its btrfs-fix)")));
        let deb = sample::bios_mbr().replace(boot_line, "");
        assert!(!text(built_from(&deb, &[]).stage(Stage::Boot)).contains("defragment"));
    }

    /// A btrfs whose superblock has default_subvol with the top level as
    /// its default (BigLinux's set-default to ID 5, run at every boot): one
    /// `set-default` of the top level sets the flag again.  Without the
    /// flag, nothing; with another default, only that subvolume's.
    #[test]
    fn default_subvol_flag_kept() {
        let top = format!("{RUN_DIR}/btrfs/b7b7b7b7-0000-4000-8000-000000000003");
        let plain = r#""default_subvolume": {"id": 5}, "quota_enabled": false"#;
        let flagged = sample::bios_mbr().replace(plain, &format!(r#""features": ["mixed_backref", "default_subvol", "extended_iref"], {plain}"#));
        assert_ne!(flagged, sample::bios_mbr());
        let b = built_from(&flagged, &[]);
        assert!(b.problems.is_empty(), "{:?}", b.problems);
        let f = text(b.stage(Stage::Format));
        assert_eq!(f.matches("btrfs subvolume set-default").count(), 1, "{f}");
        let set = f.find(&format!("btrfs subvolume set-default {top}\n")).expect(&f);
        assert!(f.find(&format!("btrfs subvolume create {top}/@rootfs\n")).expect(&f) < set && set < f.find(&format!("umount {top}\n")).expect(&f), "{f}");
        assert!(!text(built_from(&sample::bios_mbr(), &[]).stage(Stage::Format)).contains("set-default"));
        let other = flagged.replace(r#""default_subvolume": {"id": 5}"#, r#""default_subvolume": {"id": 256}"#);
        let f = text(built_from(&other, &[]).stage(Stage::Format));
        assert_eq!(f.matches("btrfs subvolume set-default").count(), 1, "{f}");
        assert!(f.contains(&format!("btrfs subvolume set-default {top}/@rootfs\n")), "{f}");
    }

    #[test]
    fn mount_restore_boot_close() {
        let b = built(&["--repo", "/srv/repo", "--password-file", "/run/pw"]);
        let m = text(b.stage(Stage::Mount));
        let root = m.find("mount -t btrfs -o subvol=@rootfs /dev/vdb3 /mnt/bko-target\n").expect(&m);
        let boot = m.find("mount -t ext4 -o defaults /dev/vdb1 /mnt/bko-target/boot\n").expect(&m);
        assert!(root < boot);
        assert!(!m.contains("swap"));
        let r = text(b.stage(Stage::Restore));
        assert!(r.contains("RUSTIC_STRICT_EXIT=true rustic-bko --repository /srv/repo --password-file /run/pw --no-progress restore --numeric-id latest /mnt/bko-target\n"), "{r}");
        let bt = text(b.stage(Stage::Boot));
        assert!(bt.contains("chroot /mnt/bko-target grub-install --target=i386-pc /dev/vdb\n"), "{bt}");
        assert!(bt.contains("chroot /mnt/bko-target update-initramfs -u -k all\n"));
        let before = bt.find("atimes.before").expect(&bt);
        assert!(before < bt.find("update-initramfs").unwrap());
        assert!(bt.contains("touch -a -h -d"), "{bt}");
        assert!(bt.contains("chroot /mnt/bko-target env LANG=C update-grub\n"), "{bt}");
        assert!(text(b.stage(Stage::Close)).starts_with("umount --recursive /mnt/bko-target\n"));
        let none = built(&[]);
        assert!(text(none.stage(Stage::Restore)).contains("no --repo"));
    }

    /// Ubuntu Core mounts nothing at the target itself, only under
    /// run/mnt: close takes those off with umount-tree, not `umount
    /// --recursive` of the target (which fails, "not mounted").
    #[test]
    fn close_without_a_root_mount() {
        let j = std::fs::read_to_string(concat!(env!("CARGO_MANIFEST_DIR"), "/tests/corpus/uc24/src.json")).unwrap();
        let b = built_from(&j, &[]);
        assert!(!b.mount_points.iter().any(|p| p == "/mnt/bko-target") && b.mount_points.iter().any(|p| p == "/mnt/bko-target/run/mnt/data"), "{:?}", b.mount_points);
        let c = text(b.stage(Stage::Close));
        let first = c.lines().find(|l| !l.starts_with('#') && !l.is_empty()).unwrap();
        assert!(first.ends_with(" umount-tree /mnt/bko-target"), "{c}");
        assert!(!c.contains("umount --recursive /mnt/bko-target\n"), "{c}");
    }

    #[test]
    fn bios_grub_device_from_the_mbr() {
        // Mint leaves grub-pc/install_devices empty: the disk whose MBR holds
        // GRUB's boot code is the one.
        let j = sample::bios_mbr().replace(r#""grub-pc/install_devices": "/dev/disk/by-id/virtio-x""#, r#""grub-pc/install_devices": """#);
        let b = built_from(&j, &[]);
        let bt = text(b.stage(Stage::Boot));
        assert!(bt.contains("chroot /mnt/bko-target grub-install --target=i386-pc /dev/vdb\n"), "{bt}");
        assert!(b.problems.is_empty(), "{:?}", b.problems);
    }

    /// zorin18's / is NILFS2, the MBR disk's last partition, to the disk's
    /// end: BIOS grub-install runs through GRUB_BIOS_NILFS2
    /// (t2h-20261119T1959-f82c).  A layout without NILFS2 runs it plain.
    #[test]
    fn bios_grub_install_with_nilfs2_at_the_disk_end() {
        let j = std::fs::read_to_string(concat!(env!("CARGO_MANIFEST_DIR"), "/tests/corpus/zorin18/src.json")).unwrap();
        let b = built_from(&j, &[]);
        let c = b.stage(Stage::Boot).iter().find(|c| c.argv.iter().any(|a| a == "grub-install")).expect("no grub-install").clone();
        assert_eq!(c.argv[..2], ["sh", "-c"]);
        assert_eq!(c.argv[2], GRUB_BIOS_NILFS2);
        assert_eq!(c.argv[3..], ["sh", "/dev/vdb", "chroot", "/mnt/bko-target", "grub-install", "--target=i386-pc", "/dev/vdb"]);
        let bt = text(b.stage(Stage::Boot));
        assert_eq!(bt.matches(" grub-install --target=").count(), 1, "{bt}");
        assert!(bt.contains("# /dev/vdb: GRUB would take a NILFS2 partition's secondary superblock"), "{bt}");
        assert!(!text(built(&[]).stage(Stage::Boot)).contains("GRUB would take a NILFS2"));
    }

    /// GRUB_BIOS_NILFS2 run on a file for the disk, with a blockdev that
    /// knows files: the command sees the last 4 KiB's magic cleared, the
    /// disk is as it was after, and the command's exit status is the
    /// script's.  Without the magic, the disk isn't written.
    #[test]
    fn grub_bios_nilfs2_hides_the_superblock_while_grub_runs() {
        let t = crate::testutil::Tree::new("grub-nilfs2");
        t.file("/bin/blockdev", "#!/bin/sh\ncase $1 in --flushbufs) echo \"flush $2\" >> \"${2%/*}/log\" ;; --getsize64) wc -c < \"$2\" | tr -d ' ' ;; *) exit 1 ;; esac\n");
        std::process::Command::new("chmod").arg("755").arg(t.p("/bin/blockdev")).status().unwrap();
        let path = format!("{}:{}", t.p("/bin").display(), std::env::var("PATH").unwrap_or_default());
        // 64 KiB and 1 KiB more: the superblock is at the last whole 4 KiB.
        let size = 65 * 1024;
        let at = (size / 4096 - 1) * 4096;
        let mut sb = vec![0u8; 4096];
        sb[..8].copy_from_slice(&[2, 0, 0, 0, 0, 0, 0x34, 0x34]);
        sb[8..16].copy_from_slice(b"rest of ");
        let disk = t.p("/d/disk");
        let log = t.p("/d/log");
        // What GRUB sees: bytes 6 and 7 of the last 4 KiB, then exit RC.
        let grub = format!("dd if={} bs=1 skip={} count=2 status=none | od -An -tx1 | tr -d ' \\n' >> {}; echo >> {}; exit \"$1\"", disk.display(), at + 6, log.display(), log.display());
        for (magic, rc) in [(true, 0), (true, 1), (false, 0), (false, 3)] {
            let mut img = vec![0u8; size];
            img[size - 1] = 0xaa;
            if magic {
                img[at..at + 4096].copy_from_slice(&sb);
            } else {
                img[at + 6..at + 8].copy_from_slice(&[0x12, 0x34]);
            }
            t.file("/d/disk", &img);
            let _ = std::fs::remove_file(&log);
            let o = std::process::Command::new("sh").args(["-c", GRUB_BIOS_NILFS2, "sh", disk.to_str().unwrap(), "sh", "-c", &grub, "grub", &rc.to_string()]).env("PATH", &path).output().unwrap();
            assert_eq!(o.status.code(), Some(rc), "{magic} {rc}: {}", String::from_utf8_lossy(&o.stderr));
            let seen = std::fs::read_to_string(&log).unwrap();
            assert_eq!(seen, format!("flush {}\n{}\n", disk.display(), if magic { "0000" } else { "1234" }), "{magic} {rc}");
            assert_eq!(std::fs::read(&disk).unwrap(), img, "{magic} {rc}: the disk changed");
            assert_eq!(String::from_utf8_lossy(&o.stdout).contains("NILFS2 superblock"), magic, "{magic} {rc}");
        }
    }

    #[test]
    fn update_grub_lang() {
        let j = sample::bios_mbr().replace(r#""grub": {"debconf""#, r#""grub": {"grub_cfg": {"lang": "en_US"}, "debconf""#);
        let bt = text(built_from(&j, &[]).stage(Stage::Boot));
        assert!(bt.contains("chroot /mnt/bko-target env LANG=en_US.UTF-8 update-grub\n"), "{bt}");
    }

    /// KDE neon: Ubuntu's update-grub, and its GRUB through shim in EFI/neon.
    #[test]
    fn kde_neon_boot_tools() {
        let neon = sample::bios_mbr().replace(r#""id": "debian", "version_id": "13""#, r#""id": "neon", "version_id": "24.04""#);
        let bt = text(built_from(&neon, &[]).stage(Stage::Boot));
        assert!(bt.contains("chroot /mnt/bko-target env LANG=C update-grub\n") && !bt.contains("grub-mkconfig"), "{bt}");
        let entry = r#"{"number": "0001", "description": "neon", "file": "\\EFI\\neon\\shimx64.efi", "partition": {"device": "vda1"}}"#;
        let uefi = neon
            .replace(r#""firmware": {"mode": "bios"}"#, r#""firmware": {"mode": "uefi"}"#)
            .replace(r#""firmware": "bios""#, r#""firmware": "uefi""#)
            .replace(r#""efi": null,"#, &format!(r#""efi": {{"entries": [{entry}], "boot_order": ["0001"]}}, "esps": [],"#));
        let bt = text(built_from(&uefi, &[]).stage(Stage::Boot));
        let at = bt.find("--bootloader-id=").expect(&bt) + "--bootloader-id=".len();
        assert_eq!(bt[at..].split_whitespace().next(), Some("neon"), "{bt}");
        assert!(bt.contains("update-grub\n") && !bt.contains("grub-mkconfig"), "{bt}");
    }

    /// openSUSE Leap 16 on UEFI (docs/design/leap16.md, L2): GRUB 2
    /// through shim in EFI/opensuse, as Fedora's and Tumbleweed's: no
    /// grub2-install or shim-install over the signed files the ESP brings
    /// back, grub2-mkconfig, dracut, and the source's boot entry made again.
    #[test]
    fn leap16_uefi_grub_through_shim() {
        let entry = r#"{"number": "0001", "description": "opensuse-secureboot", "file": "\\EFI\\opensuse\\shim.efi", "partition": {"device": "vda1"}}"#;
        let j = sample::bios_mbr()
            .replace(r#""id": "debian", "version_id": "13""#, r#""id": "opensuse-leap", "version_id": "16.1""#)
            .replace(r#""firmware": {"mode": "bios"}"#, r#""firmware": {"mode": "uefi"}"#)
            .replace(r#""loader": {"name": "grub", "firmware": "bios", "via_shim": false}"#, r#""loader": {"name": "grub", "firmware": "uefi", "via_shim": true}"#)
            .replace(r#""efi": null,"#, &format!(r#""efi": {{"entries": [{entry}], "boot_current": "0001", "boot_order": ["0001"]}}, "esps": [],"#))
            .replace(r#""grub": {"debconf": {"grub-pc/install_devices": "/dev/disk/by-id/virtio-x"}}"#, r#""grub": {"dir": "/boot/grub2"}, "initramfs": {"generator": "dracut"}"#);
        assert!(j.contains("opensuse-leap") && j.contains("/boot/grub2") && j.contains("shim.efi") && j.contains(r#""via_shim": true"#));
        let b = built_from(&j, &[]);
        assert!(b.problems.is_empty(), "{:?}", b.problems);
        let bt = text(b.stage(Stage::Boot));
        for no in ["grub2-install", "grub-install", "shim-install", "update-grub", "grub-mkconfig"] {
            assert!(!bt.contains(no), "{no}: {bt}");
        }
        let cfg = "chroot /mnt/bko-target env LANG=C grub2-mkconfig -o /boot/grub2/grub.cfg\n";
        let nvram = "efibootmgr --quiet --create --disk /dev/vdb --part 1 --label opensuse-secureboot --loader '\\EFI\\opensuse\\shim.efi'\n";
        let (d, c, e) = (bt.find("dracut --force").expect(&bt), bt.find(cfg).expect(&bt), bt.find(nvram).expect(&bt));
        assert!(d < c && c < e, "{bt}");
        assert!(bt.contains("mount --bind /mnt/bko-target/usr/bin/true /mnt/bko-target/usr/bin/os-prober"), "{bt}");
        assert!(b.notes.iter().any(|n| n.starts_with("UEFI GRUB 2 through shim (Fedora, Rocky, openSUSE)")), "{:?}", b.notes);
        // The loader's directory is the entry's, EFI/opensuse.
        let l = Layout::from_json(crate::json::parse(&j).unwrap()).unwrap();
        let o = match crate::load::parse_args(&["l.json".to_string()]).unwrap() {
            crate::load::Parsed::Run(o) => o,
            _ => unreachable!(),
        };
        let m = Mapping { pairs: vec![("vda".into(), "vdb".into())], ..Mapping::default() };
        let b = Builder {
            layout: &l,
            opts: &o,
            names: Names { layout: &l, mapping: &m, made: BTreeMap::new() },
            problems: vec![],
            notes: vec![],
            closers: vec![],
            pools: vec![],
            filesystems: vec![],
            datasets: vec![],
            mount_points: vec![],
            fam: None,
            btrfs: BTreeMap::new(),
            timeshift: BTreeMap::new(),
            readonly_later: vec![],
            be_mounts: vec![],
            be_restored: vec![],
        };
        assert_eq!(b.bootloader_id(), "opensuse");
        assert_eq!((b.grub_tool("install"), b.grub_tool("mkconfig")), ("grub2-install", "grub2-mkconfig"));
    }

    /// openSUSE Leap 16 on BIOS (no guest: docs/design/leap16.md):
    /// Tumbleweed's arm, grub2-install --target=i386-pc and grub2-mkconfig.
    /// The tumbleweed guest's dump, named Leap 16.1, plans the same boot
    /// stage.
    #[test]
    fn leap16_bios_is_tumbleweeds() {
        let tw = std::fs::read_to_string(concat!(env!("CARGO_MANIFEST_DIR"), "/tests/corpus/tumbleweed/src.json")).unwrap();
        let leap = tw
            .replace("\"id\": \"opensuse-tumbleweed\",\n    \"version_id\": \"20261103\"", "\"id\": \"opensuse-leap\",\n    \"version_id\": \"16.1\"")
            .replace(r#""pretty_name": "openSUSE Tumbleweed""#, r#""pretty_name": "openSUSE Leap 16.1""#);
        assert!(leap.contains(r#""id": "opensuse-leap""#) && !leap.contains(r#""id": "opensuse-tumbleweed""#));
        let (a, b) = (built_from(&tw, &[]), built_from(&leap, &[]));
        assert!(b.problems.is_empty(), "{:?}", b.problems);
        let bt = text(b.stage(Stage::Boot));
        assert_eq!(bt, text(a.stage(Stage::Boot)));
        assert!(bt.contains("grub2-install --target=i386-pc /dev/vdb\n") && bt.contains("grub2-mkconfig -o /boot/grub2/grub.cfg\n"), "{bt}");
        assert!(bt.contains("dracut --force") && !bt.contains("grub-install") && !bt.contains("update-grub"), "{bt}");
    }

    /// elementary OS (docs/design/elementary.md, decision 5): Ubuntu's
    /// update-grub by its ID, not grub-mkconfig.
    #[test]
    fn elementary_boot_tools() {
        let e = sample::bios_mbr().replace(r#""id": "debian", "version_id": "13""#, r#""id": "elementary", "version_id": "8""#);
        let bt = text(built_from(&e, &[]).stage(Stage::Boot));
        assert!(bt.contains("chroot /mnt/bko-target env LANG=C update-grub\n") && !bt.contains("grub-mkconfig"), "{bt}");
    }

    /// Kali (docs/design/kali.md): Debian's arms as they are, named EFI/kali.
    /// UEFI without shim (Kali has no shim-signed): grub-install into
    /// EFI/kali with no NVRAM write, update-grub with os-prober off, the
    /// update-initramfs step, and the source's boot entry made again.  The
    /// guest's ESP has the removable copy too (force-efi-extra-removable),
    /// which comes back with the ESP; grub-install writes it again only
    /// with --removable.  Through shim (a Kali that had one) the same arm,
    /// as on Debian; BIOS, Debian's i386-pc arm.
    #[test]
    fn kali_boot_tools() {
        let kali = sample::bios_mbr().replace(r#""id": "debian", "version_id": "13""#, r#""id": "kali", "version_id": "2026.3""#).replace(r#""hostname": "deb13""#, r#""hostname": "kali""#);
        let id_of = |bt: &str| {
            let at = bt.find("--bootloader-id=").expect(bt) + "--bootloader-id=".len();
            bt[at..].split_whitespace().next().unwrap().to_string()
        };
        let esp = r#"[{"device": "vda1", "mounted_at": "/boot/efi", "contents": {"files": [{"path": "EFI/BOOT/BOOTX64.EFI"}, {"path": "EFI/BOOT/grubx64.efi"}, {"path": "EFI/kali/grubx64.efi"}, {"path": "EFI/kali/grub.cfg"}]}}]"#;
        let uefi = |loader: &str, efi: &str| {
            kali.replace(r#""firmware": {"mode": "bios"}"#, r#""firmware": {"mode": "uefi"}"#)
                .replace(r#""loader": {"name": "grub", "firmware": "bios", "via_shim": false}"#, &format!(r#""loader": {loader}"#))
                .replace(r#""efi": null,"#, &format!(r#""efi": {efi}, "esps": {esp},"#))
        };
        let plain = r#"{"name": "grub", "firmware": "uefi", "via_shim": false, "package": "grub-efi-amd64"}"#;
        let install = "chroot /mnt/bko-target grub-install --target=x86_64-efi --efi-directory=/boot/efi --bootloader-id=kali --no-nvram\n";
        let cfg = "chroot /mnt/bko-target env LANG=C update-grub\n";
        let initramfs = "chroot /mnt/bko-target update-initramfs -u -k all\n";

        // The vm-run guest: booted at the fallback path from blank variables,
        // so no entry at all; the ESP's EFI/kali names it.
        let b = built_from(&uefi(plain, r#"{"entries": [], "boot_order": []}"#), &[]);
        assert!(b.problems.is_empty(), "{:?}", b.problems);
        let bt = text(b.stage(Stage::Boot));
        assert_eq!(id_of(&bt), "kali");
        let (i, g, c) = (bt.find(initramfs).expect(&bt), bt.find(install).expect(&bt), bt.find(cfg).expect(&bt));
        let off = bt.find("mount --bind /mnt/bko-target/usr/bin/true /mnt/bko-target/usr/bin/os-prober").expect(&bt);
        assert!(i < g && g < off && off < c, "{bt}");
        assert_eq!(bt.matches("grub-install").count(), 1, "{bt}");
        assert!(!bt.contains("grub-mkconfig") && !bt.contains("--removable") && !bt.contains("efibootmgr") && !bt.contains("--target=i386-pc"), "{bt}");

        // A real PC's install: NVRAM's entry for EFI/kali, made again after
        // update-grub.
        let entry = r#"{"number": "0001", "description": "kali", "file": "\\EFI\\kali\\grubx64.efi", "partition": {"device": "vda1"}}"#;
        let efi = format!(r#"{{"entries": [{entry}], "boot_current": "0001", "boot_order": ["0001"]}}"#);
        let bt = text(built_from(&uefi(plain, &efi), &[]).stage(Stage::Boot));
        assert_eq!(id_of(&bt), "kali");
        let nvram = bt.find("efibootmgr --quiet --create --disk /dev/vdb --part 1 --label kali --loader '\\EFI\\kali\\grubx64.efi'\n").expect(&bt);
        assert!(bt.find(install).expect(&bt) < bt.find(cfg).unwrap() && bt.find(cfg).unwrap() < nvram, "{bt}");
        // --removable: the fallback copy too; --no-nvram: no entry.
        let bt = text(built_from(&uefi(plain, &efi), &["--removable"]).stage(Stage::Boot));
        assert!(bt.contains("chroot /mnt/bko-target grub-install --target=x86_64-efi --efi-directory=/boot/efi --bootloader-id=kali --no-nvram --force-extra-removable\n"), "{bt}");
        let bt = text(built_from(&uefi(plain, &efi), &["--no-nvram"]).stage(Stage::Boot));
        assert!(bt.contains(install) && !bt.contains("efibootmgr"), "{bt}");

        // Through shim: Debian's arm still, shim's entry made again.
        let shim = r#"{"name": "grub", "firmware": "uefi", "via_shim": true, "package": "grub-efi-amd64-signed", "shim_package": "shim-signed"}"#;
        let entry = r#"{"number": "0001", "description": "kali", "file": "\\EFI\\kali\\shimx64.efi", "partition": {"device": "vda1"}}"#;
        let b = built_from(&uefi(shim, &format!(r#"{{"entries": [{entry}], "boot_current": "0001", "boot_order": ["0001"]}}"#)), &[]);
        assert!(b.problems.is_empty(), "{:?}", b.problems);
        let bt = text(b.stage(Stage::Boot));
        assert!(bt.contains(install) && bt.contains(cfg) && bt.contains("--loader '\\EFI\\kali\\shimx64.efi'\n") && !bt.contains("grub-mkconfig"), "{bt}");

        // BIOS: Debian's i386-pc arm, with debconf's note.
        let b = built_from(&kali, &[]);
        assert!(b.problems.is_empty(), "{:?}", b.problems);
        let bt = text(b.stage(Stage::Boot));
        let g = bt.find("chroot /mnt/bko-target grub-install --target=i386-pc /dev/vdb\n").expect(&bt);
        assert!(bt.find(initramfs).expect(&bt) < g && g < bt.find(cfg).expect(&bt), "{bt}");
        assert!(!bt.contains("x86_64-efi") && !bt.contains("grub-mkconfig") && !bt.contains("efibootmgr"), "{bt}");
        assert!(b.notes.iter().any(|n| n.starts_with("debconf's grub-pc/install_devices still names the source's disks")), "{:?}", b.notes);
    }

    /// Upstream GRUB on UEFI, as Alpine's setup-disk installs it: EFI/alpine
    /// and a copy of its image at the fallback path (EFI/boot/bootx64.efi).
    /// BackShot2's fail-002 (testvm/backshot2/cases): grub-install renews
    /// EFI/alpine only, and the fallback the firmware boots kept the old
    /// /boot partition's number, so the copy is renewed too.  fail-003:
    /// --removable gave grub-install Debian's --force-extra-removable,
    /// which upstream GRUB refuses; the image is copied there instead.
    #[test]
    fn upstream_uefi_grub_fallback_copy_and_removable() {
        let alpine = sample::bios_mbr().replace(r#""id": "debian", "version_id": "13""#, r#""id": "alpine", "version_id": "3.24.3""#);
        let esp = |same: &str| format!(r#"[{{"device": "vda1", "mounted_at": "/boot/efi", "contents": {{"files": [{{"path": "EFI/alpine/grubx64.efi"}}, {{"path": "EFI/boot/bootx64.efi"{same}}}]}}}}]"#);
        let uefi = |loader: &str, esp: &str| {
            alpine
                .replace(r#""firmware": {"mode": "bios"}"#, r#""firmware": {"mode": "uefi"}"#)
                .replace(r#""loader": {"name": "grub", "firmware": "bios", "via_shim": false}"#, &format!(r#""loader": {loader}"#))
                .replace(r#""efi": null,"#, &format!(r#""efi": {{"entries": [], "boot_order": []}}, "esps": {esp},"#))
        };
        let plain = r#"{"name": "grub", "firmware": "uefi", "via_shim": false, "package": "grub-efi"}"#;
        let copy = esp(r#", "same_as": "EFI/alpine/grubx64.efi""#);
        let install = "chroot /mnt/bko-target grub-install --target=x86_64-efi --efi-directory=/boot/efi --bootloader-id=alpine --no-nvram\n";
        let renew = "cp /mnt/bko-target/boot/efi/EFI/alpine/grubx64.efi /mnt/bko-target/boot/efi/EFI/BOOT/BOOTX64.EFI\n";

        // fail-002: the copy renewed after grub-install, before grub.cfg.
        let bt = text(built_from(&uefi(plain, &copy), &[]).stage(Stage::Boot));
        let (i, r, c) = (bt.find(install).expect(&bt), bt.find(renew).expect(&bt), bt.find("grub-mkconfig -o /boot/grub/grub.cfg").expect(&bt));
        assert!(i < r && r < c, "{bt}");
        assert!(!bt.contains("fallback-is-grub"), "{bt}");
        // Named either way round by same_as, or both naming a third.
        let twin = r#"[{"device": "vda1", "mounted_at": "/boot/efi", "contents": {"files": [{"path": "EFI/alpine/grubx64.efi", "same_as": "EFI/boot/bootx64.efi"}, {"path": "EFI/boot/bootx64.efi", "same_as": "EFI/alpine/grubx64.efi"}]}}]"#;
        assert!(text(built_from(&uefi(plain, twin), &[]).stage(Stage::Boot)).contains(renew));
        let third = r#"[{"device": "vda1", "mounted_at": "/boot/efi", "contents": {"files": [{"path": "EFI/BOOT/grubx64.efi", "same_as": "EFI/alpine/grubx64.efi"}, {"path": "EFI/alpine/grubx64.efi", "same_as": "EFI/BOOT/grubx64.efi"}, {"path": "EFI/BOOT/BOOTX64.EFI", "same_as": "EFI/BOOT/grubx64.efi"}]}}]"#;
        assert!(text(built_from(&uefi(plain, third), &[]).stage(Stage::Boot)).contains(renew));
        // A fallback that isn't GRUB's copy (another loader) is left.
        let bt = text(built_from(&uefi(plain, &esp("")), &[]).stage(Stage::Boot));
        assert!(bt.contains(install) && !bt.contains(renew) && !bt.contains("BOOTX64"), "{bt}");
        // With --reformat, the dump's word for it is enough.
        let bt = text(built_from(&uefi(plain, &copy), &["--reformat", "/=xfs"]).stage(Stage::Boot));
        assert!(bt.contains(renew) && !bt.contains("fallback-is-grub"), "{bt}");

        // fail-003: --removable copies the image; no Debian option.
        for files in [copy.as_str(), &esp("")] {
            let bt = text(built_from(&uefi(plain, files), &["--removable"]).stage(Stage::Boot));
            assert!(!bt.contains("--no-nvram --force-extra-removable") && bt.contains(install), "{bt}");
            let cp = bt.find("mkdir -p /mnt/bko-target/boot/efi/EFI/BOOT && cp /mnt/bko-target/boot/efi/EFI/alpine/grubx64.efi /mnt/bko-target/boot/efi/EFI/BOOT/BOOTX64.EFI").expect(&bt);
            assert!(bt.find(install).unwrap() < cp && !bt.contains(renew), "{bt}");
        }
        // Through shim: shim at the fallback path, GRUB beside it.
        let shim = r#"{"name": "grub", "firmware": "uefi", "via_shim": true, "package": "grub-efi"}"#;
        let bt = text(built_from(&uefi(shim, &esp("")), &["--removable"]).stage(Stage::Boot));
        assert!(bt.contains("cp /mnt/bko-target/boot/efi/EFI/alpine/shimx64.efi /mnt/bko-target/boot/efi/EFI/BOOT/BOOTX64.EFI && cp /mnt/bko-target/boot/efi/EFI/alpine/grubx64.efi /mnt/bko-target/boot/efi/EFI/BOOT/grubx64.efi"), "{bt}");
        // Debian's GRUB (grub-common, or its family's ids) keeps its option.
        let deb = uefi(plain, &esp("")).replace(r#""id": "alpine", "version_id": "3.24.3""#, r#""id": "devuan", "version_id": "6""#).replace(r#""kernels":"#, r#""packages": {"grub-common": "2.12-9"}, "kernels":"#);
        let bt = text(built_from(&deb, &["--removable"]).stage(Stage::Boot));
        assert!(bt.contains("--bootloader-id=alpine --no-nvram --force-extra-removable\n") && !bt.contains("EFI/BOOT && cp"), "{bt}");
    }

    /// AnduinOS (docs/design/anduinos.md): Ubuntu's update-grub and the
    /// generic dracut loop; through shim (or with the MOK in the dump) no
    /// grub-install, only grub.cfg and the boot entry, and a note naming the
    /// MOK; without shim, Ubuntu's arms, with the ESP directory's case kept.
    #[test]
    fn anduinos_boot_tools() {
        const FP: &str = "5d41402abc4b2a76b9719d911017c592aaaabbbbccccddddeeeeffff00001111";
        let base = sample::bios_mbr()
            .replace(r#""id": "debian", "version_id": "13""#, r#""id": "anduinos", "version_id": "2.0.5""#)
            .replace(r#""efi": null,"#, r#""efi": null, "initramfs": {"generator": "dracut"}, "packages": {PKGS},"#);
        let uefi = |loader: &str, pkgs: &str| {
            let entry = r#"{"number": "0001", "description": "AnduinOS", "file": "\\EFI\\AnduinOS\\shimx64.efi", "partition": {"device": "vda1"}}"#;
            base.replace("{PKGS}", pkgs)
                .replace(r#""firmware": {"mode": "bios"}"#, r#""firmware": {"mode": "uefi"}"#)
                .replace(r#""loader": {"name": "grub", "firmware": "bios", "via_shim": false}"#, &format!(r#""loader": {loader}"#))
                .replace(r#""efi": null,"#, &format!(r#""efi": {{"entries": [{entry}], "boot_current": "0001", "boot_order": ["0001"]}}, "esps": [{{"device": "vda1", "mounted_at": "/boot/efi", "contents": {{"files": [{{"path": "EFI/AnduinOS/shimx64.efi"}}, {{"path": "EFI/AnduinOS/grubx64.efi"}}]}}}}],"#))
        };
        let ours = r#"{"anduinos-grub": "2.0.5+repack1-2", "anduinos-shim-signed": "15.8-1", "anduinos-btrfs-snapshots-manager": "2.0.5-3", "dracut": "110-11ubuntu0.1"}"#;
        let mok = format!(r#""mok": {{"cert": "/var/lib/shim-signed/mok/MOK.der", "sha256": "{FP}"}}"#);
        let shim = format!(r#"{{"name": "grub", "firmware": "uefi", "via_shim": true, "package": "anduinos-grub", {mok}}}"#);
        let dracut = &format!("chroot /mnt/bko-target sh -c {}\n", shell_quote(DRACUT_EACH_KERNEL));
        let snap_note = |n: &[String]| n.iter().any(|n| n.starts_with("AnduinOS: the Disk Snapshots Manager's records name each snapshot by its Btrfs subvolume UUID"));
        let mok_note = |n: &[String]| n.iter().find(|n| n.starts_with("AnduinOS: shim, MokManager and AnduinOS's GRUB")).cloned();

        // Through shim, its MOK in the dump.
        let b = built_from(&uefi(&shim, ours), &[]);
        assert!(b.problems.is_empty(), "{:?}", b.problems);
        let bt = text(b.stage(Stage::Boot));
        assert!(!bt.contains("grub-install") && !bt.contains("grub-mkconfig") && !bt.contains("update-initramfs"), "{bt}");
        let d = bt.find(dracut).expect(&bt);
        let off = bt.find("mount --bind /mnt/bko-target/usr/bin/true /mnt/bko-target/usr/bin/os-prober").expect(&bt);
        let cfg = bt.find("chroot /mnt/bko-target env LANG=C update-grub\n").expect(&bt);
        let nvram = bt.find("efibootmgr --quiet --create --disk /dev/vdb --part 1 --label AnduinOS --loader '\\EFI\\AnduinOS\\shimx64.efi'\n").expect(&bt);
        assert!(d < off && off < cfg && cfg < nvram, "{bt}");
        let n = mok_note(&b.notes).expect(&b.notes.join("\n"));
        assert!(n.contains(&format!("signed with the MOK /var/lib/shim-signed/mok/MOK.der (SHA-256 {FP})")), "{n}");
        assert!(n.contains("grub-install --target=x86_64-efi isn't run") && n.contains("`mokutil --import /var/lib/shim-signed/mok/MOK.der`, then confirm it at MokManager") && n.contains("or turn Secure Boot off"), "{n}");
        assert!(snap_note(&b.notes), "{:?}", b.notes);
        // --no-nvram: still no grub-install, and no entry.
        let bt = text(built_from(&uefi(&shim, ours), &["--no-nvram"]).stage(Stage::Boot));
        assert!(!bt.contains("grub-install") && !bt.contains("efibootmgr") && bt.contains("update-grub\n"), "{bt}");
        // The MOK alone (booted at the fallback path, shim not told), or
        // shim with anduinos-grub but no MOK in the dump: kept too.
        for loader in [format!(r#"{{"name": "grub", "firmware": "uefi", "via_shim": false, {mok}}}"#), r#"{"name": "grub", "firmware": "uefi", "via_shim": true, "mok": null}"#.to_string()] {
            let b = built_from(&uefi(&loader, ours), &[]);
            let bt = text(b.stage(Stage::Boot));
            assert!(!bt.contains("grub-install") && bt.contains("update-grub\n"), "{loader}: {bt}");
            assert!(mok_note(&b.notes).is_some(), "{loader}: {:?}", b.notes);
        }
        let b = built_from(&uefi(r#"{"name": "grub", "firmware": "uefi", "via_shim": true}"#, ours), &[]);
        assert!(mok_note(&b.notes).unwrap().contains("signed with the MOK /var/lib/shim-signed/mok/MOK.der (its fingerprint not in the dump)"), "{:?}", b.notes);

        // UEFI GRUB without shim or a MOK: Ubuntu's arm, as EFI/AnduinOS.
        let b = built_from(&uefi(r#"{"name": "grub", "firmware": "uefi", "via_shim": false}"#, ours), &[]);
        let bt = text(b.stage(Stage::Boot));
        let install = bt.find("chroot /mnt/bko-target grub-install --target=x86_64-efi --efi-directory=/boot/efi --bootloader-id=AnduinOS --no-nvram\n").expect(&bt);
        assert!(bt.find(dracut).unwrap() < install && install < bt.find("env LANG=C update-grub\n").unwrap(), "{bt}");
        assert!(mok_note(&b.notes).is_none(), "{:?}", b.notes);
        // Before 2.0.5: Ubuntu's signed GRUB through shim, no anduinos-grub.
        let ubuntu = r#"{"grub-efi-amd64-signed": "1.218+2.14-2ubuntu1", "shim-signed": "1.61+15.8-0ubuntu1", "dracut": "110-11ubuntu0.1"}"#;
        let b = built_from(&uefi(r#"{"name": "grub", "firmware": "uefi", "via_shim": true, "package": "grub-efi-amd64-signed"}"#, ubuntu), &[]);
        let bt = text(b.stage(Stage::Boot));
        assert!(bt.contains("grub-install --target=x86_64-efi --efi-directory=/boot/efi --bootloader-id=AnduinOS --no-nvram\n") && bt.contains("update-grub\n"), "{bt}");
        assert!(mok_note(&b.notes).is_none() && !snap_note(&b.notes), "{:?}", b.notes);

        // BIOS: Ubuntu's arm, with dracut's loop; the snapshot note on btrfs.
        let b = built_from(&base.replace("{PKGS}", ours), &[]);
        let bt = text(b.stage(Stage::Boot));
        let d = bt.find(dracut).expect(&bt);
        let install = bt.find("chroot /mnt/bko-target grub-install --target=i386-pc /dev/vdb\n").expect(&bt);
        assert!(d < install && install < bt.find("env LANG=C update-grub\n").unwrap(), "{bt}");
        assert!(!bt.contains("update-initramfs") && !bt.contains("grub-mkconfig") && !bt.contains("efibootmgr"), "{bt}");
        assert!(mok_note(&b.notes).is_none() && snap_note(&b.notes), "{:?}", b.notes);
        // Not AnduinOS: a MOK in the dump changes nothing on Ubuntu.
        let u = uefi(&shim, ours).replace(r#""id": "anduinos", "version_id": "2.0.5""#, r#""id": "ubuntu", "version_id": "26.04""#);
        let b = built_from(&u, &[]);
        assert!(text(b.stage(Stage::Boot)).contains("grub-install --target=x86_64-efi"), "{:?}", b.notes);
        assert!(mok_note(&b.notes).is_none() && !snap_note(&b.notes), "{:?}", b.notes);
    }

    /// AnduinOS's erase-disk UEFI install is a BIOS install too (its
    /// golden dump): the i386-pc grub-install comes back, before
    /// update-grub, and still no x86_64-efi one.  A BIOS boot partition
    /// without GRUB's core recorded: neither, and a note.
    #[test]
    fn anduinos_bios_install_too() {
        let j = std::fs::read_to_string(concat!(env!("CARGO_MANIFEST_DIR"), "/tests/corpus/anduinos2/src.json")).unwrap();
        let note = |n: &[String]| n.iter().any(|n| n.starts_with("vda has a BIOS boot partition, but the dump records no GRUB core in it"));
        let b = built_from(&j, &[]);
        assert!(b.problems.is_empty(), "{:?}", b.problems);
        let bt = text(b.stage(Stage::Boot));
        let bios = bt.find("chroot /mnt/bko-target grub-install --target=i386-pc /dev/vdb\n").expect(&bt);
        assert!(bios < bt.find("chroot /mnt/bko-target env LANG=C update-grub\n").expect(&bt), "{bt}");
        assert_eq!(bt.matches("grub-install").count(), 1, "{bt}");
        assert!(!bt.contains("--target=x86_64-efi") && !note(&b.notes), "{bt}\n{:?}", b.notes);
        let none = j.replace(r#""grub_core_present": true"#, r#""grub_core_present": false"#);
        assert_ne!(none, j);
        let b = built_from(&none, &[]);
        let bt = text(b.stage(Stage::Boot));
        assert!(!bt.contains("grub-install") && bt.contains("update-grub\n"), "{bt}");
        assert!(note(&b.notes), "{:?}", b.notes);
    }

    #[test]
    fn btrfs_device_uuid() {
        let c = crate::json::parse(r#"{"type": "btrfs", "uuid": "b7b7b7b7-0000-4000-8000-000000000003", "device": {"uuid": "94a415d0-e252-4362-aa38-8b6d9bb369c8"}}"#).unwrap();
        let fs = crate::json::parse("{}").unwrap();
        let one = mkfs_btrfs(&["/dev/vdb3".into()], &c, &fs).join(" ");
        assert!(one.ends_with("--device-uuid 94a415d0-e252-4362-aa38-8b6d9bb369c8 /dev/vdb3"), "{one}");
        let two = mkfs_btrfs(&["/dev/vdb3".into(), "/dev/vdc3".into()], &c, &fs).join(" ");
        assert!(!two.contains("--device-uuid"), "{two}");
    }

    #[test]
    fn arch_and_fedora_boot_tools() {
        let arch = sample::bios_mbr().replace(r#""id": "debian", "version_id": "13""#, r#""id": "arch""#).replace(r#""efi": null,"#, r#""efi": null, "initramfs": {"generator": "mkinitcpio"},"#);
        let bt = text(built_from(&arch, &[]).stage(Stage::Boot));
        assert!(bt.contains("chroot /mnt/bko-target mkinitcpio -P\n"), "{bt}");
        assert!(bt.contains("chroot /mnt/bko-target grub-install --target=i386-pc /dev/vdb\n"), "{bt}");
        assert!(bt.contains("chroot /mnt/bko-target env LANG=C grub-mkconfig -o /boot/grub/grub.cfg\n"), "{bt}");
        let fedora = sample::bios_mbr()
            .replace(r#""id": "debian", "version_id": "13""#, r#""id": "fedora", "version_id": "44""#)
            .replace(r#""efi": null,"#, r#""efi": null, "initramfs": {"generator": "dracut"},"#)
            .replace(r#""grub": {"debconf""#, r#""grub": {"dir": "/boot/grub2", "debconf""#);
        let bt = text(built_from(&fedora, &[]).stage(Stage::Boot));
        assert!(bt.contains("dracut --force --kver"), "{bt}");
        assert!(!bt.contains("--regenerate-all"), "{bt}");
        assert!(bt.contains("[ -d \"$k/kernel\" ] || continue"), "{bt}");
        assert!(bt.contains("chroot /mnt/bko-target grub2-install --target=i386-pc /dev/vdb\n"), "{bt}");
        assert!(bt.contains("chroot /mnt/bko-target env LANG=C grub2-mkconfig -o /boot/grub2/grub.cfg\n"), "{bt}");
        assert!(bt.contains("setfiles \"$f\" /boot"), "{bt}");
        assert!(bt.contains("mount --bind /mnt/bko-target/usr/bin/true /mnt/bko-target/usr/bin/os-prober"), "{bt}");
        let eos = sample::bios_mbr()
            .replace(r#""id": "debian", "version_id": "13""#, r#""id": "endeavouros""#)
            .replace(r#""efi": null,"#, r#""efi": null, "initramfs": {"generator": "dracut"}, "packages": {"eos-dracut": "1.7-1"},"#);
        let bt = text(built_from(&eos, &[]).stage(Stage::Boot));
        assert!(bt.contains(&format!("chroot /mnt/bko-target sh -c {}\n", shell_quote(DRACUT_REBUILD_RESUME))) && !bt.contains("--regenerate-all"), "{bt}");
        let garuda = sample::bios_mbr()
            .replace(r#""id": "debian", "version_id": "13""#, r#""id": "garuda""#)
            .replace(r#""efi": null,"#, r#""efi": null, "initramfs": {"generator": "dracut"}, "packages": {"garuda-dracut-support": "1.6.0-2"},"#);
        let bt = text(built_from(&garuda, &[]).stage(Stage::Boot));
        assert!(bt.contains(&format!("chroot /mnt/bko-target sh -c {}\n", shell_quote(DRACUT_REBUILD_RESUME))) && !bt.contains("--regenerate-all") && !bt.contains("--kver"), "{bt}");
        let alpine = sample::bios_mbr()
            .replace(r#""id": "debian", "version_id": "13""#, r#""id": "alpine", "version_id": "3.24.3""#)
            .replace(r#""efi": null,"#, r#""efi": null, "initramfs": {"generator": "mkinitfs"},"#);
        let bt = text(built_from(&alpine, &[]).stage(Stage::Boot));
        assert!(bt.contains("mkinitfs"), "{bt}");
        assert!(bt.contains("chroot /mnt/bko-target env LANG=C grub-mkconfig -o /boot/grub/grub.cfg\n"), "{bt}");
        let prebuilt = arch.replace(r#""generator": "mkinitcpio""#, r#""generator": "prebuilt""#);
        let bt = text(built_from(&prebuilt, &[]).stage(Stage::Boot));
        assert!(!bt.contains("mkinitcpio") && !bt.contains("update-initramfs"), "{bt}");
    }

    /// Host-only dracut records the root's mount options (rootflags=), so
    /// the boot stage remounts the target's / with the source's atime
    /// options first: atime with relatime or strictatime (which undoes a
    /// ZFS dataset's noatime too), noatime as it was.  Not for another
    /// generator, nor without the source's / in the dump's mounts.
    #[test]
    fn dracut_root_gets_the_source_atime_options() {
        let with = |opts: &str, generator: &str| {
            sample::bios_mbr()
                .replace(r#""id": "debian", "version_id": "13""#, r#""id": "fedora", "version_id": "44""#)
                .replace(r#""efi": null,"#, &format!(r#""efi": null, "initramfs": {{"generator": "{generator}"}},"#))
                .replace(r#""grub": {"debconf""#, r#""grub": {"dir": "/boot/grub2", "debconf""#)
                .replace(r#""mounts": [],"#, &format!(r#""mounts": [{{"target": "/", "source": "/dev/vda3", "fstype": "btrfs", "root": "/@rootfs", "options": "{opts}", "super_options": "rw,space_cache=v2,subvolid=256,subvol=/@rootfs"}}],"#))
        };
        for (opts, want) in [
            ("rw,nodev,relatime", "remount,atime,relatime"),
            ("rw,noatime", "remount,noatime"),
            ("rw", "remount,atime,strictatime"),
            ("rw,relatime,nodiratime", "remount,atime,relatime,nodiratime"),
        ] {
            let bt = text(built_from(&with(opts, "dracut"), &[]).stage(Stage::Boot));
            let remount = bt.find(&format!("mount -o {want} /mnt/bko-target\n")).unwrap_or_else(|| panic!("{opts}: {bt}"));
            let dracut = bt.find("dracut --force --kver").expect(&bt);
            let rbind = bt.find("mount --rbind").expect(&bt);
            assert!(remount < rbind && rbind < dracut, "{bt}");
        }
        let bt = text(built_from(&with("rw,noatime", "mkinitcpio"), &[]).stage(Stage::Boot));
        assert!(!bt.contains("remount"), "{bt}");
        let bt = text(built_from(&sample::bios_mbr().replace(r#""efi": null,"#, r#""efi": null, "initramfs": {"generator": "dracut"},"#), &[]).stage(Stage::Boot));
        assert!(!bt.contains("remount"), "{bt}");
    }

    /// Chimera: Debian's initramfs-tools, without debconf or update-grub's
    /// Debian side; GRUB by grub-PLATFORM packages and grub-mkconfig.  What
    /// runs in the chroot runs on FreeBSD's userland (chimerautils).
    #[test]
    fn chimera_boot_tools() {
        let initramfs = &format!("chroot /mnt/bko-target sh -c {}\n", shell_quote(UPDATE_INITRAMFS_RESUME));
        let bios = sample::bios_mbr()
            .replace(r#""id": "debian", "version_id": "13""#, r#""id": "chimera""#)
            .replace(r#""efi": null,"#, r#""efi": null, "initramfs": {"generator": "initramfs-tools"}, "packages": {"grub": "2.14-r1", "grub-i386-pc": "2.14-r1", "initramfs-tools": "0.148.3-r2"},"#)
            .replace(r#""grub": {"debconf": {"grub-pc/install_devices": "/dev/disk/by-id/virtio-x"}}"#, r#""grub": {"dir": "/boot/grub", "debconf": null}"#);
        let b = built_from(&bios, &[]);
        let bt = text(b.stage(Stage::Boot));
        assert!(b.problems.is_empty(), "{:?}", b.problems);
        assert!(bt.contains(initramfs.as_str()), "{bt}");
        // The disk whose MBR holds GRUB, with no debconf to name it.
        assert!(bt.contains("chroot /mnt/bko-target grub-install --target=i386-pc /dev/vdb\n"), "{bt}");
        assert!(bt.contains("chroot /mnt/bko-target env LANG=C grub-mkconfig -o /boot/grub/grub.cfg\n"), "{bt}");
        assert!(!bt.contains("update-grub") && !bt.contains("dpkg"), "{bt}");
        let uefi = bios
            .replace(r#""firmware": {"mode": "bios"}"#, r#""firmware": {"mode": "uefi"}"#)
            .replace(r#""loader": {"name": "grub", "firmware": "bios", "via_shim": false}"#, r#""loader": {"name": "grub", "firmware": "uefi", "via_shim": false, "package": "grub-x86_64-efi"}"#)
            .replace(r#""efi": null,"#, r#""efi": {"entries": [], "boot_order": []}, "esps": [{"device": "vda1", "mounted_at": "/boot/efi", "contents": {"files": [{"path": "EFI/chimera/grubx64.efi"}]}}],"#);
        let b = built_from(&uefi, &[]);
        let ut = text(b.stage(Stage::Boot));
        assert!(ut.contains("chroot /mnt/bko-target grub-install --target=x86_64-efi --efi-directory=/boot/efi --bootloader-id=chimera --no-nvram\n"), "{ut}");
        assert!(ut.contains("chroot /mnt/bko-target env LANG=C grub-mkconfig -o /boot/grub/grub.cfg\n"), "{ut}");
        assert!(ut.contains(initramfs.as_str()), "{ut}");
        assert!(!ut.contains("gen-systemd-boot"), "{ut}");
        // UEFI with systemd-boot (chimera-uefi): the ESP as /boot; bootctl
        // reinstalls it, and gen-systemd-boot remakes the entries after the
        // initramfs, as Chimera's kernel hook does.
        let sd = bios
            .replace(r#""firmware": {"mode": "bios"}"#, r#""firmware": {"mode": "uefi"}"#)
            .replace(r#""loader": {"name": "grub", "firmware": "bios", "via_shim": false}"#, r#""loader": {"name": "systemd-boot", "firmware": "uefi", "via_shim": false, "package": "systemd-boot"}"#)
            .replace(r#""grub": "2.14-r1", "grub-i386-pc": "2.14-r1""#, r#""systemd-boot": "256.11-r5", "systemd-boot-efi": "256.11-r5""#)
            .replace(r#""efi": null,"#, r#""efi": {"entries": [], "boot_order": []}, "esps": [{"device": "vda1", "mounted_at": "/boot", "contents": {"files": [{"path": "EFI/systemd/systemd-bootx64.efi"}, {"path": "EFI/BOOT/BOOTX64.EFI"}]}}],"#);
        let b = built_from(&sd, &[]);
        let st = text(b.stage(Stage::Boot));
        assert!(b.problems.is_empty(), "{:?}", b.problems);
        let gen_sd = "chroot /mnt/bko-target sh -c 'gen-systemd-boot; r=$?; [ \"$r\" -ne 1 ] || { echo '\\''gen-systemd-boot: warning: it made no entries (disabled, or bootctl says systemd-boot is not installed); the restored ones stay'\\'' >&2; r=0; }; exit \"$r\"'\n";
        let initramfs = st.find(initramfs.as_str()).expect(&st);
        let bootctl = st.find("chroot /mnt/bko-target bootctl install --no-variables\n").expect(&st);
        let made = st.find(gen_sd).expect(&st);
        assert!(initramfs < made && bootctl < made, "{st}");
        assert_eq!(st.matches("gen-systemd-boot;").count(), 1, "{st}");
        assert!(!st.contains("grub"), "{st}");
        // gen-systemd-boot's exit status: 0 and 1 (made nothing) succeed,
        // with a warning for 1; the rest fail, with their own status.
        let c = b.stage(Stage::Boot).iter().find(|c| c.argv.iter().any(|a| a.starts_with("gen-systemd-boot;"))).unwrap().clone();
        assert!(c.warns);
        let script = c.argv[4].replace("gen-systemd-boot;", "f() { return \"$1\"; }; f \"$0\";");
        for (rc, want) in [(0, 0), (1, 0), (2, 2), (69, 69), (127, 127)] {
            let o = std::process::Command::new("sh").args(["-c", &script, &rc.to_string()]).output().unwrap();
            assert_eq!(o.status.code(), Some(want), "{rc}");
            assert_eq!(String::from_utf8_lossy(&o.stderr).contains("warning: it made no entries"), rc == 1, "{rc}");
        }
        // Only Chimera's: not without its systemd-boot package, and not on
        // another system whose systemd-boot package has no gen-systemd-boot
        // (Pop!_OS's, Debian's).
        let no_pkg = sd.replace(r#""systemd-boot": "256.11-r5", "#, "");
        assert!(!text(built_from(&no_pkg, &[]).stage(Stage::Boot)).contains("gen-systemd-boot"));
        let pop = sd.replace(r#""id": "chimera""#, r#""id": "pop", "version_id": "24.04""#);
        let pt = text(built_from(&pop, &[]).stage(Stage::Boot));
        assert!(pt.contains("chroot /mnt/bko-target bootctl install --no-variables\n") && !pt.contains("gen-systemd-boot"), "{pt}");
        // No GNU-only option in what runs on the target's own tools.
        for l in bt.lines().chain(ut.lines()).chain(st.lines()).filter(|l| l.starts_with("chroot ")) {
            for gnu in ["stat -c", "sed -i", "df --output", "df -B", "date -d", "cp --", "sort -V", "readlink -e", "tac "] {
                assert!(!l.contains(gnu), "{gnu} in {l}");
            }
        }
    }

    #[test]
    fn zfs_create_time_properties() {
        let ds = crate::json::parse(r#"{"name": "rpool/home", "type": "filesystem", "normalization": "formD", "utf8only": "on", "casesensitivity": "sensitive",
            "properties": {"mountpoint": {"value": "/home", "source": "local"}}}"#).unwrap();
        let j = sample::bios_mbr().replace(
            r#""zfs": {"pools_from_labels": [], "tools": null}"#,
            r#""zfs": {"pools_from_labels": [], "tools": {"datasets": [{"name": "rpool", "type": "filesystem", "encryption": "aes-256-gcm"}]}}"#,
        );
        assert!(j.contains("aes-256-gcm"));
        let l = Layout::from_json(crate::json::parse(&j).unwrap()).unwrap();
        let o = match crate::load::parse_args(&["l.json".to_string()]).unwrap() {
            crate::load::Parsed::Run(o) => o,
            _ => unreachable!(),
        };
        let m = Mapping::default();
        let mut b = Builder {
            layout: &l,
            opts: &o,
            names: Names { layout: &l, mapping: &m, made: BTreeMap::new() },
            problems: vec![],
            notes: vec![],
            closers: vec![],
            pools: vec![],
            filesystems: vec![],
            datasets: vec![],
            mount_points: vec![],
            fam: None,
            btrfs: BTreeMap::new(),
            timeshift: BTreeMap::new(),
            readonly_later: vec![],
            be_mounts: vec![],
            be_restored: vec![],
        };
        let props = b.ds_props(&ds, false);
        assert!(props.contains(&("normalization".into(), "formD".into())), "{props:?}");
        assert!(props.contains(&("utf8only".into(), "on".into())), "{props:?}");
        assert!(!props.iter().any(|(k, _)| k == "casesensitivity"), "{props:?}");
        let root = crate::json::parse(r#"{"name": "rpool", "type": "filesystem", "encryption": "aes-256-gcm", "encryptionroot": "rpool", "keyformat": "raw",
            "keylocation": "file:///run/keystore/rpool/system.key", "properties": {}}"#).unwrap();
        let props = b.ds_props(&root, true);
        assert!(props.contains(&("encryption".into(), "aes-256-gcm".into())), "{props:?}");
        assert!(props.contains(&("keyformat".into(), "raw".into())), "{props:?}");
        assert!(props.contains(&("keylocation".into(), format!("file://{RUN_DIR}/keys/rpool"))), "{props:?}");
        assert!(b.problems.is_empty(), "{:?}", b.problems);
        let keystore = crate::json::parse(r#"{"name": "rpool/keystore", "type": "volume", "encryption": "off", "volsize": 20971520, "properties": {}}"#).unwrap();
        let props = b.ds_props(&keystore, false);
        assert!(props.contains(&("encryption".into(), "off".into())), "{props:?}");
    }

    /// keystores() writes a random raw key only into a mount the dump left
    /// out of the backup (report.rs not_backed_up(): not in fstab, at /run
    /// or under it): Ubuntu's /run/keystore/rpool gets rpool's new key.  The
    /// same keystore mounted at /keys (a .mount unit) is backed up, so the
    /// restore would write the old key over the new one: load asks for
    /// --key-file, and given one writes nothing there.
    #[test]
    fn keystore_only_where_the_backup_leaves_it_out() {
        let j = std::fs::read_to_string(concat!(env!("CARGO_MANIFEST_DIR"), "/tests/corpus/bs1-ubuntu2610-zfsenc/src.json")).unwrap();
        let plan = |json: &str, extra: &[&str]| {
            let l = Layout::from_json(crate::json::parse(json).unwrap()).unwrap();
            let mut a = vec!["l.json".to_string()];
            a.extend(extra.iter().map(|s| s.to_string()));
            let Ok(crate::load::Parsed::Run(o)) = crate::load::parse_args(&a) else { unreachable!() };
            build(&l, &o, &Mapping { pairs: vec![("nvme0n1".into(), "sdq".into())], ..Mapping::default() })
        };
        let need = "ZFS encryption root rpool: give its new key with --key-file rpool=FILE".to_string();
        let b = plan(&j, &[]);
        let fmt = text(b.stage(Stage::Format));
        assert!(fmt.contains("mount /dev/mapper/keystore-rpool /run/bko-holt/keystore\n"), "{fmt}");
        assert!(fmt.contains("install -m 400 /run/bko-holt/keys/rpool /run/bko-holt/keystore/system.key\n"), "{fmt}");
        assert!(!b.problems.contains(&need), "{:?}", b.problems);
        let keys = j.replace("/run/keystore/rpool", "/keys");
        let k = plan(&keys, &[]);
        assert!(k.problems.contains(&need), "{:?}", k.problems);
        assert!(!text(k.stage(Stage::Format)).contains("/run/bko-holt/keystore"));
        let k = plan(&keys, &["--key-file", "rpool=/root/rpool.key"]);
        assert!(!text(k.stage(Stage::Format)).contains("/run/bko-holt/keystore"));
        assert_eq!(k.problems, b.problems);
    }

    #[test]
    fn nilfs2() {
        let c = crate::json::parse(r#"{"type": "nilfs2", "uuid": "64962903-fd78-434b-9633-b73fd5eba5e8", "label": null,
            "block_size": 4096, "blocks_per_segment": 2048, "reserved_segments_percentage": 5, "features": []}"#).unwrap();
        assert_eq!(mkfs_nilfs2("/dev/vdb3", &c).join(" "), "mkfs.nilfs2 -f -q -b 4096 -B 2048 -m 5 -O ^block_count /dev/vdb3");
        assert_eq!(nilfs_tune("/dev/vdb3", &c).unwrap().join(" "), "nilfs-tune -U 64962903-fd78-434b-9633-b73fd5eba5e8 /dev/vdb3");
    }

    #[test]
    fn jfs() {
        let c = crate::json::parse(r#"{"type": "jfs", "uuid": "d12ea338-ce14-4c47-90c8-7e72c24a87c6", "label": "root", "block_size": 4096}"#).unwrap();
        assert_eq!(mkfs_jfs("/dev/vdb3", &c).join(" "), "mkfs.jfs -q -L root /dev/vdb3");
        let b = crate::json::parse(r#"{"type": "bcachefs", "uuid": "c9cbba71-4054-4849-a19c-e7d40fb5b1e4", "label": null, "version": "1.13", "block_size": 4096, "btree_node_size": 262144, "metadata_checksum": "crc32c", "data_checksum": "crc32c", "compression": "none", "str_hash": "siphash", "metadata_replicas": 1, "data_replicas": 1}"#).unwrap();
        assert_eq!(
            mkfs_bcachefs("/dev/vdb3", &b).join(" "),
            "bcachefs format --force --no_initialize --version=1037 --uuid=c9cbba71-4054-4849-a19c-e7d40fb5b1e4 --block_size=4096 --btree_node_size=262144 --metadata_checksum=crc32c --data_checksum=crc32c --str_hash=siphash /dev/vdb3"
        );
        // version_min, not the version a newer kernel raised it to (nixos2605's).
        let b2 = crate::json::parse(r#"{"type": "bcachefs", "version": "1.39", "version_min": "1.13"}"#).unwrap();
        assert_eq!(mkfs_bcachefs("/dev/vdb3", &b2).join(" "), "bcachefs format --force --no_initialize --version=1037 /dev/vdb3");
        // No version, or one that doesn't parse: no flag.
        for v in [r#"{"type": "bcachefs"}"#, r#"{"type": "bcachefs", "version": "1.x", "version_min": "junk"}"#] {
            let x = crate::json::parse(v).unwrap();
            assert_eq!(mkfs_bcachefs("/dev/vdb3", &x).join(" "), "bcachefs format --force --no_initialize /dev/vdb3");
        }
        assert_eq!(bcachefs_version("1.39"), Some(1063));
        assert_eq!(bcachefs_version("0.24"), Some(24));
        assert_eq!(jfs_tune("/dev/vdb3", &c).unwrap().join(" "), "jfs_tune -U d12ea338-ce14-4c47-90c8-7e72c24a87c6 /dev/vdb3");
        assert_eq!(
            jfs_version1("/dev/vdb3")[2],
            "printf '\\001' | dd of=/dev/vdb3 bs=1 seek=32772 count=1 conv=notrunc status=none && printf '\\001' | dd of=/dev/vdb3 bs=1 seek=61444 count=1 conv=notrunc status=none"
        );
    }

    #[test]
    fn f2fs() {
        let c = crate::json::parse(r#"{"type": "f2fs", "uuid": "84cf3639-67d8-4237-9980-8dc6392e778c", "label": null,
            "features": ["extra_attr", "inode_checksum", "atomic_write", "sb_checksum"], "segments_per_section": 1, "sections_per_zone": 1}"#).unwrap();
        assert_eq!(
            mkfs_f2fs("/dev/vdb3", &c).join(" "),
            "mkfs.f2fs -f -q -U 84cf3639-67d8-4237-9980-8dc6392e778c -O extra_attr,inode_checksum,sb_checksum /dev/vdb3"
        );
    }

    #[test]
    fn helpers() {
        assert_eq!(part_path("vda", 3), "/dev/vda3");
        assert_eq!(part_path("nvme0n1", 2), "/dev/nvme0n1p2");
        assert_eq!(gpt_attrs(0), Ok(String::new()));
        assert_eq!(gpt_attrs(1 | 4 | (1 << 60)), Ok("RequiredPartition LegacyBIOSBootable GUID:60".into()));
        assert_eq!(gpt_attrs(1 << 10), Err(10));
        assert_eq!(md_layout_name("raid5", 2).as_deref(), Some("left-symmetric"));
        assert_eq!(md_layout_name("raid10", 0x102).as_deref(), Some("n2"));
        assert_eq!(md_layout_name("raid10", 0x201).as_deref(), Some("f2"));
        assert_eq!(md_layout_name("raid1", 0), None);
        assert_eq!(mount_options("defaults,noatime,x-systemd.device-timeout=5,nofail"), "noatime");
        assert_eq!(mount_options("defaults"), "defaults");
        // The stage writes into every mount, ostree's ro `/` too.
        assert_eq!(mount_options("subvol=root,compress=zstd:1,ro"), "subvol=root,compress=zstd:1");
        assert_eq!(mount_options("ro,noatime"), "noatime");
        assert_eq!(mount_options("ro"), "defaults");
        assert_eq!(mount_options("errors=remount-ro"), "errors=remount-ro");
        let c = Cmd::write_file("/run/x", "a\n".into());
        assert_eq!(c.shell(), "cat > /run/x <<'EOF'\na\nEOF\n");
    }

    #[test]
    fn vg_text() {
        let vg = crate::json::parse(
            r#"{"name": "vg0", "id": "AAAA", "seqno": 3, "format": "lvm2", "status": ["READ", "WRITE"], "flags": [], "extent_size_bytes": 4194304,
                "max_lv": 0, "max_pv": 0, "metadata_copies": 0,
                "physical_volumes": [{"name": "pv0", "id": "PPPP", "device_hint": "/dev/md0", "dev_size_bytes": 1073741824, "pe_start_bytes": 1048576, "pe_count": 255, "status": ["ALLOCATABLE"]}],
                "logical_volumes": [{"name": "root", "id": "LLLL", "status": ["READ", "WRITE", "VISIBLE"], "flags": [], "creation_host": "h", "creation_time": 1700000000,
                  "segments": [{"start_extent": 0, "extent_count": 100, "type": "striped", "stripe_count": 1, "stripes": ["pv0", 0]}]}]}"#,
        )
        .unwrap();
        let t = vg_metadata(&vg).unwrap();
        assert!(t.starts_with("vg0 {\n\tid = \"AAAA\"\n\tseqno = 3\n"), "{t}");
        assert!(t.contains("\textent_size = 8192\n"), "{t}");
        assert!(t.contains("\t\tpv0 {\n\t\t\tid = \"PPPP\"\n\t\t\tdevice = \"/dev/md0\"\n"), "{t}");
        assert!(t.contains("\t\t\tsegment_count = 1\n\t\t\tsegment1 {\n\t\t\t\tstart_extent = 0\n"), "{t}");
        assert!(t.contains("stripes = [\"pv0\", 0]"), "{t}");
        assert!(t.contains("contents = \"Text Format Volume Group\"\nversion = 1\n"), "{t}");
        // A read-only LV with ACTIVATION_SKIP (the LV's own status and
        // flags only, not the group's "flags": []): vgcfgrestore makes it
        // writable and active, for format and restore ...
        let c = vg.compact();
        let lv_from = "\"status\":[\"READ\",\"WRITE\",\"VISIBLE\"],\"flags\":[]";
        let changed = c.replacen(lv_from, "\"status\":[\"READ\",\"VISIBLE\"],\"flags\":[\"ACTIVATION_SKIP\"]", 1);
        assert_ne!(changed, c, "{lv_from}");
        let ro_skip = crate::json::parse(&changed).unwrap();
        assert_eq!(LvAttrs::of(&ro_skip.get("logical_volumes").and_then(Value::as_array).unwrap()[0]), LvAttrs { tags: vec![], activation_skip: true, read_only: true });
        let rt = vg_metadata(&ro_skip).unwrap();
        assert!(rt.contains("\t\troot {\n\t\t\tid = \"LLLL\"\n\t\t\tstatus = [\"READ\", \"WRITE\", \"VISIBLE\"]\n\t\t\tflags = []\n"), "{rt}");
        assert!(!rt.contains("ACTIVATION_SKIP"), "{rt}");
        // ... and close sets both again, before the group's vgchange -an
        // (closers run last first).
        let closers = |vg: &Value| {
            let j = sample::bios_mbr();
            let l = Layout::from_json(crate::json::parse(&j).unwrap()).unwrap();
            let o = match crate::load::parse_args(&["l.json".to_string()]).unwrap() {
                crate::load::Parsed::Run(o) => o,
                _ => unreachable!(),
            };
            let m = Mapping::default();
            let mut b = Builder {
                layout: &l,
                opts: &o,
                names: Names { layout: &l, mapping: &m, made: BTreeMap::new() },
                problems: vec![],
                notes: vec![],
                closers: vec![],
                pools: vec![],
                filesystems: vec![],
                datasets: vec![],
                mount_points: vec![],
                fam: None,
                btrfs: BTreeMap::new(),
                timeshift: BTreeMap::new(),
                readonly_later: vec![],
                be_mounts: vec![],
                be_restored: vec![],
            };
            let mut out = vec![];
            b.vg(vg, &mut out);
            assert!(b.problems.is_empty(), "{:?}", b.problems);
            b.closers.iter().map(|c| c.argv.join(" ")).collect::<Vec<_>>()
        };
        let cl = closers(&ro_skip);
        let at = |w: &str| cl.iter().position(|c| c.contains(w)).unwrap_or_else(|| panic!("{w}: {cl:?}"));
        let (an, skip, ro) = (at("vgchange --devicesfile"), at(" --setactivationskip y vg0/root"), at(" --permission r vg0/root"));
        assert!(cl[skip].starts_with("lvchange --devicesfile ") && cl[ro].starts_with("lvchange --devicesfile "), "{cl:?}");
        assert!(cl[an].contains(" --activate n vg0"), "{cl:?}");
        assert!(an < skip && an < ro, "{cl:?}");
        assert_eq!(cl.len(), 3, "{cl:?}");
        // A writable LV without the flag: no lvchange.
        let cl = closers(&vg);
        assert!(!cl.iter().any(|c| c.contains("lvchange")), "{cl:?}");
    }

    /// Vanilla OS's vos-root: vgcfgrestore's file has the PV, the group and
    /// `init` only; thin_of has the pool at the dump's sizes, its thin LVs
    /// in device_id order, and the spare.
    #[test]
    fn thin_pool_left_to_lvcreate() {
        let vg = crate::probe::lvm::sample::thin_vg();
        let t = vg_metadata(&vg).unwrap();
        assert!(t.starts_with("vos-root {\n\tid = \"VoSr00-tAAA-BBBB-CCCC-DDDD-EEEE-FFFFFF\"\n"), "{t}");
        assert!(t.contains("\t\tpv0 {\n\t\t\tid = \"Hc7n2m-aaaa-bbbb-cccc-dddd-eeee-ffffff\"\n"), "{t}");
        assert!(t.contains("\t\tinit {\n\t\t\tid = \"1n1t00-aaaa-bbbb-cccc-dddd-eeee-ffffff\"\n"), "{t}");
        for gone in ["\troot {", "root-a", "root-b", "pmspare", "root_tmeta", "root_tdata", "thin", "p00l00"] {
            assert!(!t.contains(gone), "{gone}: {t}");
        }
        let th = thin_of(&vg).unwrap();
        assert!(th.spare);
        assert_eq!(th.dropped, vec!["root_tdata", "root_tmeta", "root", "root-a", "root-b", "lvol0_pmspare"]);
        assert_eq!(
            th.pools,
            vec![ThinPool {
                name: "root".into(),
                data_bytes: 19 << 30,
                meta_bytes: 1 << 30,
                data_stripes: None,
                chunk_bytes: Some(64 << 10),
                discards: Some("passdown".into()),
                zero: true,
                error_when_full: false,
                attrs: LvAttrs::default(),
                thins: vec![
                    ThinLv { name: "root-a".into(), bytes: 19 << 30, device_id: 1, attrs: LvAttrs::default(), origin: None },
                    ThinLv { name: "root-b".into(), bytes: 19 << 30, device_id: 2, attrs: LvAttrs::default(), origin: None }
                ],
                data_used: None,
                meta_used: None,
            }]
        );
        assert_eq!(th.spare_note, None);
        let c = vg.compact();
        let with = |from: &str, to: &str| {
            let changed = c.replacen(from, to, 1);
            assert_ne!(changed, c, "{from}");
            thin_of(&crate::json::parse(&changed).unwrap()).unwrap()
        };
        // device_id order, not the metadata's.
        assert_eq!(with("\"device_id\":1", "\"device_id\":7").pools[0].thins.iter().map(|t| t.name.as_str()).collect::<Vec<_>>(), vec!["root-b", "root-a"]);
        // lvm2 writes zero_new_blocks only when it is on.
        assert!(!with(",\"zero_new_blocks\":1", "").pools[0].zero);
        // ERROR_WHEN_FULL is the pool LV's flag; ACTIVATION_SKIP, tags and
        // read-only (no WRITE) a thin LV's.
        assert!(with("\"name\":\"root\",\"id\":\"p00l00-aaaa-bbbb-cccc-dddd-eeee-ffffff\",\"status\":[\"READ\",\"WRITE\",\"VISIBLE\"]", "\"name\":\"root\",\"id\":\"p00l00-aaaa-bbbb-cccc-dddd-eeee-ffffff\",\"status\":[\"READ\",\"WRITE\",\"VISIBLE\",\"ERROR_WHEN_FULL\"]").pools[0].error_when_full);
        let b = with("\"status\":[\"READ\",\"WRITE\",\"VISIBLE\"],\"flags\":[],\"size_bytes\":20401094656,\"creation_host\":\"vanilla\",\"creation_time\":1762300003", "\"status\":[\"READ\",\"VISIBLE\"],\"flags\":[\"ACTIVATION_SKIP\"],\"size_bytes\":20401094656,\"creation_host\":\"vanilla\",\"creation_time\":1762300003,\"tags\":[\"keep\"]");
        assert_eq!(b.pools[0].thins[1].attrs, LvAttrs { tags: vec!["keep".into()], activation_skip: true, read_only: true });
        assert_eq!(b.pools[0].thins[0].attrs, LvAttrs::default());
        // A data LV striped over two PVs keeps its stripes.
        let at = c.find("\"name\":\"root_tdata\"").unwrap();
        let striped = format!("{}{}", &c[..at], c[at..].replacen("\"stripe_count\":1", "\"stripe_count\":2,\"stripe_size\":128", 1));
        assert_eq!(thin_of(&crate::json::parse(&striped).unwrap()).unwrap().pools[0].data_stripes, Some((2, 64 << 10)));
        // A spare smaller than the metadata: made again where the free
        // extents cover the difference, else left out with a note.
        let at = c.find("\"name\":\"lvol0_pmspare\"").unwrap();
        let small = format!("{}{}", &c[..at], c[at..].replacen("\"extent_count\":256", "\"extent_count\":128", 1));
        let roomy = thin_of(&crate::json::parse(&small).unwrap()).unwrap();
        assert!(roomy.spare && roomy.spare_note.is_none(), "{roomy:?}");
        let full = thin_of(&crate::json::parse(&small.replacen("\"pe_count\":5657", "\"pe_count\":5600", 1)).unwrap()).unwrap();
        assert!(!full.spare);
        assert_eq!(full.spare_note.as_deref(), Some("volume group vos-root: its pool metadata spare (512 MiB) is smaller than the largest pool's metadata (1 GiB), and the group's 384 MiB free can't make up the 512 MiB a new one takes: the pools are made without a spare (--poolmetadataspare n); `lvconvert --repair` needs one, which `lvconvert --poolmetadataspare y` makes once there is room"));
        // No thin pool: nothing dropped, the spare (a cache pool's) stays.
        let plain = crate::json::parse(&vg.compact().replace("\"thin-pool\"", "\"striped\"").replace("\"thin\"", "\"striped\"")).unwrap();
        assert_eq!(thin_of(&plain).unwrap(), Thin::default());
        assert_eq!(lvm_size(19 << 30), "19456m");
        assert_eq!(lvm_size(64 << 10), "64k");
        assert_eq!(lvm_size(1536), "3s");
    }

    /// Refused, each by name: external origins, a snapshot of an LV not
    /// in the pool, a pool on RAID or cached, a thin LV of no pool.
    #[test]
    fn thin_refusals() {
        let vg = crate::probe::lvm::sample::thin_vg().compact();
        let err = |from: &str, to: &str| {
            let changed = vg.replacen(from, to, 1);
            assert_ne!(changed, vg, "{from}");
            vg_metadata(&crate::json::parse(&changed).unwrap()).unwrap_err()
        };
        // A thin snapshot is made (in the restore stage, load/rawvol.rs):
        // after its origin, whatever their device ids; one of an LV not
        // in the pool, or a loop, is refused.
        let snap = vg.replacen("\"device_id\":1", "\"device_id\":1,\"origin\":\"root-b\"", 1);
        let th = thin_of(&crate::json::parse(&snap).unwrap()).unwrap();
        assert_eq!(th.pools[0].thins.iter().map(|l| (l.name.as_str(), l.origin.as_deref())).collect::<Vec<_>>(), vec![("root-b", None), ("root-a", Some("root-b"))]);
        let gone = err("\"device_id\":2", "\"device_id\":2,\"origin\":\"root-x\"");
        assert_eq!(gone, "thin LV vos-root/root-b is a thin snapshot of root-x, which isn't a thin LV of pool root in the dump: bko-holt load can't make it without its origin");
        let looped = vg.replacen("\"device_id\":1", "\"device_id\":1,\"origin\":\"root-b\"", 1).replacen("\"device_id\":2", "\"device_id\":2,\"origin\":\"root-a\"", 1);
        assert!(vg_metadata(&crate::json::parse(&looped).unwrap()).unwrap_err().contains("are snapshots of each other in a loop"));
        let ext = err("\"device_id\":2", "\"device_id\":2,\"external_origin\":\"init\"");
        assert_eq!(ext, "thin LV vos-root/root-b has the external origin init; version 1 doesn't make thin LVs with one");
        // The pool's use, where the dump has it (crate::qubes::thin_usage).
        let used = vg.replacen("\"zero_new_blocks\":1}]", "\"zero_new_blocks\":1}],\"data_used_bytes\":1048576,\"metadata_used_bytes\":4096", 1);
        let p = &thin_of(&crate::json::parse(&used).unwrap()).unwrap().pools[0];
        assert_eq!((p.data_used, p.meta_used), (Some(1 << 20), Some(4096)));
        // root_tdata as RAID1 (its one segment's type).
        let at = vg.find("\"name\":\"root_tdata\"").unwrap();
        let raid = format!("{}{}", &vg[..at], vg[at..].replacen("\"type\":\"striped\"", "\"type\":\"raid1\"", 1));
        let e = vg_metadata(&crate::json::parse(&raid).unwrap()).unwrap_err();
        assert_eq!(e, "thin pool vos-root/root's data LV root_tdata has a raid1 segment; version 1 makes thin pools on linear and striped LVs only (not RAID or cache)");
        let at = vg.find("\"name\":\"root_tmeta\"").unwrap();
        let cached = format!("{}{}", &vg[..at], vg[at..].replacen("\"type\":\"striped\"", "\"type\":\"cache\"", 1));
        assert!(vg_metadata(&crate::json::parse(&cached).unwrap()).unwrap_err().starts_with("thin pool vos-root/root's metadata LV root_tmeta has a cache segment"));
        let orphan = err("\"thin_pool\":\"root\"", "\"thin_pool\":\"other\"");
        assert_eq!(orphan, "thin LV vos-root/root-a's pool other isn't a thin pool in the group");
        let meta_striped = format!("{}{}", &vg[..at], vg[at..].replacen("\"stripe_count\":1", "\"stripe_count\":2,\"stripe_size\":128", 1));
        assert_eq!(vg_metadata(&crate::json::parse(&meta_striped).unwrap()).unwrap_err(), "thin pool vos-root/root's metadata LV is striped across 2 PVs; version 1 makes a pool's metadata LV linear only");
        let at = vg.find("\"name\":\"root_tdata\"").unwrap();
        let mixed = vg[at..].replacen("\"segments\":[{", "\"segments\":[{\"start_extent\":0,\"extent_count\":1,\"type\":\"striped\",\"stripe_count\":2,\"stripe_size\":128,\"stripes\":[\"pv0\",0,\"pv0\",1]},{", 1);
        let e = vg_metadata(&crate::json::parse(&format!("{}{mixed}", &vg[..at])).unwrap()).unwrap_err();
        assert_eq!(e, "thin pool vos-root/root's data LV root_tdata has segments of different stripes; version 1 makes a pool's data LV with one stripe count and size");
        let e = err("\"status\":[\"ALLOCATABLE\"]", "\"status\":[]");
        assert_eq!(e, "thin pool vos-root/root's data LV root_tdata is on pv0, which isn't allocatable (pvchange -x n): lvcreate can't make it there again");
    }

    /// Slackware's device names: /dev/md/N is /dev/mdN (load assembles
    /// /dev/md/0, the kernel boots it as /dev/md0); links aren't names.
    #[test]
    fn slackware_md_names() {
        assert_eq!(md_name("/dev/md/0"), "/dev/md0");
        assert_eq!(md_name("/dev/md/127"), "/dev/md127");
        assert_eq!(md_name("/dev/md/root"), "/dev/md/root");
        assert_eq!(md_name("/dev/md0"), "/dev/md0");
        assert!(kernel_dev_path("/dev/md0") && kernel_dev_path("/dev/md/0") && kernel_dev_path("/dev/vda2"));
        assert!(!kernel_dev_path("/dev/disk/by-id/virtio-x") && !kernel_dev_path("/dev/mapper/root") && !kernel_dev_path("/dev/md/root") && !kernel_dev_path("UUID=x"));
    }

    /// boot.lilo as the dump keeps a lilo.conf's items (lilo_conf_json's
    /// options, and each section's kind, path and options).
    fn lilo_dump(text: &str) -> Value {
        use crate::json::Obj;
        let c = crate::boot::LiloConf::parse(text);
        let list = |its: &[crate::boot::LiloItem]| -> Vec<String> { its.iter().map(|(k, v)| v.as_ref().map_or_else(|| k.clone(), |v| format!("{k}={v}"))).collect() };
        let images: Vec<Value> = c.sections.iter().map(|(k, p, its)| Obj::new().set("kind", k.as_str()).set("path", p.as_str()).set("options", list(its)).build()).collect();
        Obj::new().set("conf", "/etc/lilo.conf").set("options", list(&c.global)).set("images", images).build()
    }

    /// The copy's sections, parsed as the dump parses lilo.conf.
    fn lilo_copy(text: &str) -> (String, crate::boot::LiloConf) {
        let (copy, _) = lilo_root_copy(&lilo_dump(text)).unwrap().expect(text);
        let c = crate::boot::LiloConf::parse(&copy);
        (copy, c)
    }

    /// lilo_root_copy: root=/dev/NAME by name at the end of the image's
    /// effective append= (the global one it inherits made its own; its own;
    /// a new one), nothing for root=UUID= or a root= already in append=,
    /// /dev/md/N as the kernel's /dev/mdN, and every other item kept.
    #[test]
    fn lilo_root_copies() {
        use crate::boot::lilo_get;
        // The global append=, inherited: the image's own copy of it.  A
        // second image on another root keeps the global one unchanged.
        let conf = "append=\" vt.default_utf8=0\"\nboot = /dev/vda\nprompt\ntimeout = 50\nchange-rules\n  reset\nvga = normal\nimage = /boot/vmlinuz\n  root = /dev/vda2\n  label = Linux\n  read-only\nimage = /boot/vmlinuz-old\n  root = UUID=1234\n  label = Old\nother = /dev/vda3\n  label = Other\n";
        let (copy, c) = lilo_copy(conf);
        assert_eq!(lilo_get(&c.global, "append"), Some(" vt.default_utf8=0"), "{copy}");
        assert_eq!(c.global, crate::boot::LiloConf::parse(conf).global, "{copy}");
        assert_eq!(lilo_get(&c.sections[0].2, "append"), Some(" vt.default_utf8=0 root=/dev/vda2"), "{copy}");
        assert_eq!(lilo_get(&c.sections[0].2, "root"), Some("/dev/vda2"));
        assert!(c.sections[0].2.iter().any(|(k, v)| k == "read-only" && v.is_none()));
        // root=UUID= goes through as text: left alone.
        assert_eq!(lilo_get(&c.sections[1].2, "append"), None, "{copy}");
        assert_eq!((c.sections[2].0.as_str(), c.sections[2].1.as_str()), ("other", "/dev/vda3"));
        assert_eq!(c.sections.len(), 3);

        // The image's own append=: there.
        let (copy, c) = lilo_copy("boot=/dev/vda\nappend=\"quiet\"\nimage=/boot/vmlinuz\n  root=/dev/vda2\n  append=\"vga=normal ro\"\n");
        assert_eq!(lilo_get(&c.global, "append"), Some("quiet"), "{copy}");
        assert_eq!(lilo_get(&c.sections[0].2, "append"), Some("vga=normal ro root=/dev/vda2"), "{copy}");
        assert_eq!(c.sections[0].2.iter().filter(|(k, _)| k == "append").count(), 1);

        // No append= at all, the root= global: a new append= on the image.
        let (copy, c) = lilo_copy("boot=/dev/vda\nroot=/dev/vda2\nimage=/boot/vmlinuz\n  label=Linux\n");
        assert_eq!(lilo_get(&c.sections[0].2, "append"), Some("root=/dev/vda2"), "{copy}");
        assert_eq!(lilo_get(&c.global, "append"), None);
        assert!(copy.starts_with("# bko-holt load: /etc/lilo.conf with root= by name added to append= (Linux: root=/dev/vda2)"), "{copy}");

        // A /dev/md/N root, by the kernel's name.
        let (copy, c) = lilo_copy("boot=/dev/vda\nimage=/boot/vmlinuz\n  root=/dev/md/0\n  label=Linux\n");
        assert_eq!(lilo_get(&c.sections[0].2, "append"), Some("root=/dev/md0"), "{copy}");

        // Nothing to add: root=UUID=, LABEL=, current, /dev/mapper/, no
        // root=, literal=, or a root= already in append= or addappend=.
        for conf in [
            "boot=/dev/vda\nimage=/boot/vmlinuz\n  root=UUID=1234\n",
            "boot=/dev/vda\nroot=LABEL=root\nimage=/boot/vmlinuz\n",
            "boot=/dev/vda\nimage=/boot/vmlinuz\n  root=current\n",
            "boot=/dev/vda\nimage=/boot/vmlinuz\n  root=/dev/mapper/root\n",
            "boot=/dev/vda\nimage=/boot/vmlinuz\n",
            "boot=/dev/vda\nimage=/boot/vmlinuz\n  root=/dev/vda2\n  literal=\"root=/dev/vda2 ro\"\n",
            "boot=/dev/vda\nappend=\"root=/dev/vda2 ro\"\nimage=/boot/vmlinuz\n  root=/dev/vda2\n",
            "boot=/dev/vda\nappend=\"ro\"\nimage=/boot/vmlinuz\n  root=/dev/vda2\n  addappend=\"root=/dev/vda3\"\n",
        ] {
            assert_eq!(lilo_root_copy(&lilo_dump(conf)), Ok(None), "{conf}");
        }
    }

    /// lilo_root_copy refuses what lilo would refuse (a command line over
    /// 511 characters) and what the copy couldn't say as lilo reads it.
    #[test]
    fn lilo_root_copy_refusals() {
        let long = "x".repeat(490);
        let e = lilo_root_copy(&lilo_dump(&format!("boot=/dev/vda\nimage=/boot/vmlinuz\n  root=/dev/vda2\n  label=Linux\n  read-only\n  append=\"{long}\"\n"))).unwrap_err();
        assert!(e.starts_with("/etc/lilo.conf's image Linux would have a kernel command line of up to 522 characters with root=/dev/vda2 added to its append=, over lilo's 511"), "{e}");
        // Just under: ro (3) root=ffffffff (14) the append, less its space.
        let fits = "x".repeat(511 - 3 - 14 - " root=/dev/vda2".len());
        assert!(lilo_root_copy(&lilo_dump(&format!("boot=/dev/vda\nimage=/boot/vmlinuz\n  root=/dev/vda2\n  read-only\n  append=\"{fits}\"\n"))).unwrap().is_some());
        let e = lilo_root_copy(&lilo_dump("boot=/dev/vda\npassword=\"\"\nimage=/boot/vmlinuz\n  root=/dev/vda2\n")).unwrap_err();
        assert!(e.starts_with("/etc/lilo.conf has password=\"\""), "{e}");
        let e = lilo_root_copy(&lilo_dump("boot=/dev/vda\nmessage=$HOME/m\nimage=/boot/vmlinuz\n  root=/dev/vda2\n")).unwrap_err();
        assert!(e.starts_with("/etc/lilo.conf's message=$HOME/m has a quote, backslash or $"), "{e}");
        let e = lilo_root_copy(&crate::json::Obj::new().set("conf", "/etc/lilo.conf").build()).unwrap_err();
        assert!(e.starts_with("the dump's boot.lilo doesn't keep /etc/lilo.conf's items"), "{e}");
    }

    #[test]
    fn shell_heredoc_and_hostile_targets_are_quoted() {
        let mut c = Cmd::new(&["sfdisk", "/dev/vdb"]).input("label: gpt\nEOF\nEOF_1\n".into());
        c.env.push(("BAD;KEY".into(), "val".into()));
        let s = c.shell();
        assert!(s.starts_with("env 'BAD;KEY=val' sfdisk /dev/vdb <<'EOF_2'\n"), "{s}");
        assert!(s.ends_with("\nEOF_2\n"), "{s}");
        assert!(std::process::Command::new("sh").args(["-n", "-c", &s]).status().unwrap().success(), "{s}");

        let hostile = "/mnt/new d'isk\"$HOME`id`\nz";
        let l = Layout::from_json(crate::json::parse(&crate::load::layout::sample::bios_mbr()).unwrap()).unwrap();
        let Ok(crate::load::Parsed::Run(o)) = crate::load::parse_args(&["l.json".into(), "--target".into(), hostile.into()]) else { unreachable!() };
        let b = build(&l, &o, &crate::load::target::Mapping { pairs: vec![("vda".into(), "vdb".into())], ..Default::default() });
        for st in Stage::ALL {
            for cmd in b.stage(st).iter().filter(|c| c.argv.len() > 2 && matches!(c.argv[0].as_str(), "sh" | "bash") && c.argv[1] == "-c") {
                let script = &cmd.argv[2];
                assert!(!script.contains(hostile), "{} unquoted: {script}", st.name());
                assert!(std::process::Command::new("sh").args(["-n", "-c", script]).status().unwrap().success(), "{} syntax: {script}", st.name());
            }
        }
        assert!(!safe_target_subpath("/../../etc"));
        assert!(!safe_target_subpath("relative/path"));
        assert!(safe_target_subpath("/boot/efi"));
    }
}
