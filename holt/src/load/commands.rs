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

/// A closer for a device just let go of (an LV, a LUKS mapping, an md
/// array): udev probes a device when the last writer closes it, and while
/// its worker has it open the device can't be stopped.  So udev is waited
/// for first, and the command is tried up to five times, a second apart.
fn settled(argv: &[&str]) -> Cmd {
    let cmd = argv.iter().map(|a| shell_quote(a)).collect::<Vec<_>>().join(" ");
    Cmd::new(&["sh", "-c", &format!("i=0; until udevadm settle; {cmd}; do i=$((i + 1)); [ $i -lt 5 ] || exit 1; sleep 1; done")])
}

/// An LVM command that sees DEVICES only (comma-separated), and no devices
/// file: `CMD --devicesfile "" --devices DEVICES REST...`.
fn lvm_argv<'a>(cmd: &'a str, devices: &'a str, rest: &[&'a str]) -> Vec<&'a str> {
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
            None => self
                .env
                .iter()
                .map(|(k, v)| format!("{k}={}", shell_quote(v)))
                .chain(self.argv.iter().map(|a| shell_quote(a)))
                .collect::<Vec<_>>()
                .join(" "),
        };
        match &self.stdin {
            Some(s) => {
                out += &format!("{line} <<'EOF'\n{s}");
                if !s.ends_with('\n') {
                    out.push('\n');
                }
                out += "EOF\n";
            }
            None => out += &format!("{line}\n"),
        }
        out
    }
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
    };
    b.btrfs_plans();
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
}

// ---- partition ----------------------------------------------------------

/// GPT attribute bits as sfdisk names them.
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
            let pmbr_active = self.layout.device(&src).and_then(|v| v.at(&["partition_table", "protective_mbr", "entries", "0", "bootable"])).and_then(Value::as_bool) == Some(true);
            if pmbr_active {
                out.push(Cmd::new(&["sh", "-c", &format!("printf '\\200' | dd of={dev} bs=1 seek=446 count=1 conv=notrunc status=none")]).why("the protective MBR's entry active, as the source's"));
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
    /// Its thin LVs in the source's device_id order.
    pub thins: Vec<ThinLv>,
}

#[derive(Debug, Clone, PartialEq)]
pub struct ThinLv {
    pub name: String,
    /// Its virtual size.
    pub bytes: u64,
    pub device_id: u64,
    pub attrs: LvAttrs,
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

/// The volume group's thin pools and thin LVs.  Refused in version 1: a
/// thin LV that is a snapshot (origin, external_origin), a pool whose data
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
        if let Some(o) = s(seg, "origin") {
            return Err(format!("thin LV {name}/{ln} is a thin snapshot of {o}; version 1 doesn't make thin snapshots"));
        }
        if let Some(o) = s(seg, "external_origin") {
            return Err(format!("thin LV {name}/{ln} has the external origin {o}; version 1 doesn't make thin LVs with one"));
        }
        let pool = s(seg, "thin_pool").unwrap_or_default();
        let Some(p) = t.pools.iter_mut().find(|p| p.name == pool) else {
            return Err(format!("thin LV {name}/{ln}'s pool {pool} isn't a thin pool in the group"));
        };
        p.thins.push(ThinLv { name: ln.clone(), bytes: num(seg, "extent_count").unwrap_or(0) * ext, device_id: num(seg, "device_id").unwrap_or(0), attrs: LvAttrs::of(lv) });
        t.dropped.push(ln);
    }
    for p in &mut t.pools {
        p.thins.sort_by_key(|l| l.device_id);
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

/// A size for an LVM command, exact: MiB where it is a whole number of
/// them, else KiB, else 512-byte sectors.
fn lvm_size(bytes: u64) -> String {
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
        let mut o = Obj::new()
            .set("id", lv.get("id").cloned().unwrap_or(Value::Null))
            .set("status", lv.get("status").cloned().unwrap_or(Value::Arr(vec![])))
            .set("flags", lv.get("flags").cloned().unwrap_or(Value::Arr(vec![])))
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
            let outs = self.zvols_of(&s(&p, "name").unwrap_or_default()).into_iter().map(|(src, ds)| (src, format!("/dev/zvol/{ds}"))).collect();
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
            let ks = c.at(&["keyslots", "0"]).cloned().unwrap_or(Value::Null);
            if let Some(e) = s(&seg, "encryption") {
                a.push(format!("--cipher={e}"));
            }
            if let Some(k) = num(&ks, "key_bits") {
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
        // prompt for a new passphrase.
        let tab_key = self.layout.crypttab_entry(&open_as, s(&c, "uuid").as_deref()).and_then(|e| s(e, "key_file")).filter(|k| !matches!(k.as_str(), "none" | "-" | ""));
        let key = match (self.opts.key_file(&open_as), &tab_key) {
            (Some(f), _) => Some(f.to_string()),
            (None, Some(k)) => {
                let tmp = format!("{RUN_DIR}/keys/{open_as}");
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
        out.push(Cmd::new(&["mkdir", "-p", &format!("{RUN_DIR}/lvm")]));
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
            for l in &p.thins {
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
        out.push(Cmd::new(&["sh", "-c", &format!("[ \"$(zfs get -H -o value mounted {here})\" != yes ] || zfs unmount {here}")]).why("mount mounts every dataset in order"));
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
                if let Some(ds) = self.dataset(&d).filter(|_| d != name && !self.datasets.contains(&d)) {
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
        let key = format!("{RUN_DIR}/keys/{}", name.replace('/', "_"));
        out.push(Cmd::new(&["mkdir", "-p", "-m", "700", &format!("{RUN_DIR}/keys")]));
        let gen = if fmt == "raw" { format!("head -c 32 /dev/urandom > {key}") } else { format!("od -An -tx1 -N32 /dev/urandom | tr -d ' \\n' > {key}") };
        out.push(Cmd::new(&["sh", "-c", &format!("umask 077; {gen}")]).why(format!("ZFS encryption root {name}: a new {fmt} key (the data is all rewritten by the restore)")));
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
                        self.problems.push(format!("ZFS encryption root {name}: give its new key with --key-file {name}=FILE"));
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
    let has = |f: &str| c.get("features").and_then(Value::as_array).is_some_and(|a| a.iter().any(|x| x.as_str() == Some(f)));
    if let (true, Some(r), Some(bs), Some(n), Some(bpg)) = (has("resize_inode"), num(c, "reserved_gdt_blocks"), num(c, "block_size"), num(c, "blocks"), num(c, "blocks_per_group")) {
        let desc = num(c, "descriptor_size").filter(|&d| d >= 32).unwrap_or(32);
        let first = u64::from(bs == 1024);
        let gdpb = bs / desc;
        if gdpb > 0 && bpg > 0 {
            let groups = (n - first).div_ceil(bpg);
            let d = groups.div_ceil(gdpb);
            ext.push(format!("resize={}", (d + r) * gdpb * bpg + first));
        }
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
pub fn mkfs_bcachefs(dev: &str, c: &Value) -> Vec<String> {
    let mut a = vec!["bcachefs".to_string(), "format".into(), "--force".into()];
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
    let w = |off: u32| format!("printf '\\001' | dd of={dev} bs=1 seek={off} count=1 conv=notrunc status=none");
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
            if !imaged && matches!(ty.as_str(), "ext2" | "ext3" | "ext4" | "xfs" | "btrfs" | "f2fs" | "nilfs2" | "jfs" | "vfat" | "msdos" | "swap") {
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
                    let mut members: Vec<(u64, String)> = vec![];
                    for m in fs.get("devices").and_then(Value::as_array).into_iter().flatten().filter_map(|x| x.as_str()) {
                        let devid = self.layout.content(m).and_then(|c| num(c, "devid")).unwrap_or(0);
                        members.push((devid, self.names.dev(m).unwrap_or_else(|| format!("<{m}>"))));
                    }
                    if members.is_empty() {
                        members.push((1, dev.clone()));
                    }
                    members.sort();
                    let devs: Vec<String> = members.into_iter().map(|m| m.1).collect();
                    out.push(Cmd { argv: mkfs_btrfs(&devs, &c, &fs), why: Some(why), ..Cmd::default() });
                    out.push(Cmd::read_back(&dev, &c));
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
    /// system finds it where the source's was.
    fn keystores(&mut self, out: &mut Vec<Cmd>) {
        let mounts = self.layout.arr(&["storage", "mounts"]).to_vec();
        for ds in self.layout.v.at(&["storage", "zfs", "tools", "datasets"]).and_then(Value::as_array).cloned().unwrap_or_default() {
            let name = s(&ds, "name").unwrap_or_default();
            let Some(path) = s(&ds, "keylocation").and_then(|l| l.strip_prefix("file://").map(str::to_string)) else { continue };
            if s(&ds, "encryptionroot").as_deref() != Some(name.as_str()) {
                continue;
            }
            // The mount holding the key file, deepest first.
            let Some(m) = mounts
                .iter()
                .filter_map(|m| Some((s(m, "target")?, s(m, "source")?)))
                .filter(|(t, _)| path.starts_with(&format!("{t}/")))
                .max_by_key(|(t, _)| t.len())
            else {
                continue;
            };
            // Its device, as this layout makes it.
            let src_dev = self.layout.arr(&["storage", "dm_devices"]).iter().chain(self.layout.arr(&["storage", "disks"])).find_map(|d| {
                let n = s(d, "name")?;
                let mapper = d.at(&["dm", "mapper_path"]).and_then(Value::as_str);
                (m.1 == format!("/dev/{n}") || Some(m.1.as_str()) == mapper).then_some(n)
            });
            let Some(dev) = src_dev.and_then(|n| self.names.dev(&n)) else { continue };
            let key = self.opts.key_file(&name).map(str::to_string).unwrap_or_else(|| format!("{RUN_DIR}/keys/{}", name.replace('/', "_")));
            let mnt = format!("{RUN_DIR}/keystore");
            let rel = &path[m.0.len()..];
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
            // A snapshot is made as one of its origin (still empty: the
            // restore fills both), so that it stays a snapshot.
            let origin = v.get("parent_uuid").and_then(Value::as_str).and_then(|pu| made.iter().find(|(u, _)| u == pu)).map(|(_, p)| p.clone());
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
                .and_then(|n| plan.shared.iter().find(|x| x.id == Some(n)))
                .map_or_else(|| id.to_string(), |x| format!("0/{}", x.new_id))
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
                        gen: v.get("gen").and_then(Value::as_u64),
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
                    (None, None) => by_id(default),
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
        self.btrfs.values().flat_map(|p| p.shared.iter().filter(|x| x.readonly).map(|x| format!("{t}{}", x.backup))).collect()
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
            out.push(Cmd { argv: a, ..Cmd::default() });
            // illumos' zfs create has no -u: what it mounted, unmounted
            // (mount mounts every dataset in order).
            if self.fam == Some(crate::other::Family::Illumos) && s(&ds, "type").as_deref() != Some("volume") {
                out.push(Cmd::new(&["sh", "-c", &format!("[ \"$(zfs get -H -o value mounted {here})\" != yes ] || zfs unmount {here}")]));
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

// ---- mount, restore -----------------------------------------------------

/// fstab's options for the mount stage's own mounts: without those that
/// only matter at the restored system's own boot, and without `ro`, since
/// the restore writes into every mount (ostree's fstab mounts / ro).
fn mount_options(o: &str) -> String {
    let keep: Vec<&str> = o
        .split(',')
        .filter(|x| !x.is_empty())
        .filter(|x| !matches!(*x, "defaults" | "noauto" | "auto" | "nofail" | "_netdev" | "user" | "users" | "owner" | "sw" | "ro"))
        .filter(|x| !x.starts_with("x-") && !x.starts_with("comment="))
        .collect();
    if keep.is_empty() { "defaults".to_string() } else { keep.join(",") }
}

impl Builder<'_> {
    fn mount(&mut self) -> Vec<Cmd> {
        let target = self.opts.target.clone();
        // (mount point in the restored system, command)
        let mut ms: Vec<(String, Cmd)> = Vec::new();
        let mut out_mkdir: Vec<String> = Vec::new();
        for e in self.layout.arr(&["storage", "fstab"]) {
            let file = s(e, "file").unwrap_or_default();
            let ty = s(e, "type").unwrap_or_default();
            if ty == "swap" || !file.starts_with('/') {
                continue;
            }
            let Some(src) = s(e, "resolves_to") else {
                self.notes.push(format!("fstab's {file} ({ty}) isn't on the layout's disks; not mounted"));
                continue;
            };
            let src = crate::other::geli::fstab_src(self.layout, e, src);
            let Some(dev) = self.names.dev(&src) else {
                self.notes.push(format!("fstab's {file} is on {src}, which isn't restored"));
                continue;
            };
            let opts = mount_options(&s(e, "options").unwrap_or_default());
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
                let script = format!("redoxfs {dev} {dir} >{RUN_DIR}/redoxfs.log 2>&1 </dev/null; i=0; until mountpoint -q {dir}; do i=$((i + 1)); [ $i -lt 60 ] || {{ cat {RUN_DIR}/redoxfs.log; exit 1; }}; sleep 1; done");
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
        // ABRoot (load/abroot.rs): the init LV, the other root and /var,
        // which no fstab line mounts.
        if let Some(a) = super::abroot::of(&self.layout.v) {
            let have: Vec<String> = ms.iter().map(|(p, _)| p.clone()).collect();
            let extra = self.abroot_mounts(&a.clone(), &have);
            ms.extend(extra);
        }
        ms.sort_by(|a, b| {
            let depth = |p: &str| if p == "/" { 0 } else { p.matches('/').count() };
            depth(&a.0).cmp(&depth(&b.0)).then(a.0.cmp(&b.0))
        });
        if !ms.iter().any(|(p, _)| p == "/") {
            self.problems.push("nothing in fstab or the ZFS datasets mounts /".into());
        }
        let mut out = Vec::new();
        for (p, c) in ms {
            let dir = if p == "/" { target.clone() } else { format!("{target}{p}") };
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
            return vec![Cmd::note(format!(
                "no --repo: restore the backup into {} yourself, then run `bko-holt load ... --only boot`",
                self.opts.target
            ))];
        };
        let mut a = vec![self.opts.archiver.clone(), "--repository".into(), repo.clone()];
        if let Some(p) = &self.opts.password_file {
            a.extend(["--password-file".into(), p.clone()]);
        }
        a.push("--no-progress".into());
        let prefix = a.clone();
        a.extend(["restore".into(), "--numeric-id".into()]);
        // Aliases and shared snapshots left out of the main restore; with
        // --reformat, a reformatted btrfs's aliases and snapshots too.
        let mut globs: Vec<String> = self.btrfs.values().flat_map(|p| p.globs()).collect();
        let reformat_globs: Vec<String> = self.layout.arr(&["load_reformat", "restore_globs"]).iter().filter_map(Value::as_str).map(str::to_string).collect();
        let only_reformat = globs.is_empty();
        globs.extend(reformat_globs);
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
            pre.push(Cmd::new(&["mkdir", "-p", RUN_DIR]));
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
                pre.push(Cmd::new(&["mkdir", "-p", RUN_DIR]));
            }
            let why = if only_reformat {
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
            // The boot stage there reads nothing of the restored tree.
            return self.msdosfs_roots_spared(Cmd { argv: a, why, env, ..Cmd::default() });
        }
        let mut out = pre;
        out.push(Cmd { argv: a, why, env, ..Cmd::default() });
        self.shared_snapshots(&prefix, &mut out);
        out.push(Cmd::new(&["sh", "-c", &atimes_cmd(&self.opts.target, "before", &self.ro_shared())]).why("the atimes the restore set, for boot to put back after its reads"));
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
        let script = format!(
            "\"$@\" 2>{err}; s=$?; cat {err} >&2; [ $s -eq 1 ] || exit $s; e=$(printf '\\033'); sed -e \"s/$e\\[[0-9;]*m//g\" -e 's/[[:space:]]*$//' {err} > {err}.plain; \
             n=$(sed -n 's/^error: There were \\([0-9][0-9]*\\) errors.*/\\1/p' {err}.plain); k=$(grep -cxF -f {allowed} {err}.plain); \
             [ -n \"$n\" ] && [ \"$n\" = \"$k\" ] || exit 1; echo 'bko-holt: the restore set no owner, mode or times on the root of {}: msdosfs keeps none there' >&2",
            roots.iter().map(|r| format!("/{r}")).collect::<Vec<_>>().join(", ")
        );
        let mut argv = vec!["sh".to_string(), "-c".into(), script, "sh".into()];
        argv.extend(restore.argv);
        vec![
            Cmd::new(&["mkdir", "-p", RUN_DIR]),
            Cmd::write_file(&allowed, lines).why("the restore errors msdosfs's mount roots give, and only those, let pass"),
            Cmd { argv, ..restore },
        ]
    }

    /// Each btrfs's shared snapshots, once the main restore is done: in the
    /// source's ID order, each a snapshot of its base, then made exact from
    /// the backup (rustic-bko restore --exact); then the read-only flags,
    /// the default subvolume if it's one, and quotas.
    fn shared_snapshots(&mut self, prefix: &[String], out: &mut Vec<Cmd>) {
        for fs in self.layout.arr(&["storage", "btrfs"]).to_vec() {
            let uuid = s(&fs, "uuid").unwrap_or_default();
            let Some(plan) = self.btrfs.get(&uuid).cloned().filter(|p| !p.shared.is_empty()) else { continue };
            let dev = fs.at(&["devices", "0"]).and_then(Value::as_str).and_then(|d| self.names.dev(d)).unwrap_or_default();
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
                let (qd, qb, qp, qpath) = (shell_quote(&d), shell_quote(&format!("{top}/{}", x.base)), shell_quote(&parent), x.path.replace('"', ""));
                let id = x.new_id;
                // The directory it's in keeps its times (snapper's N/, with
                // info.xml), which rmdir and the snapshot would change.
                let script = format!(
                    "d={qd}; p={qp}; mkdir -p \"$p\" && touch -r \"$p\" {RUN_DIR}/times.ref || exit; \
                     if [ -d \"$d\" ] && [ \"$(stat -c %i \"$d\")\" != 256 ] && [ -z \"$(ls -A \"$d\")\" ]; then rmdir \"$d\" && btrfs subvolume snapshot {qb} \"$d\" && RUSTIC_STRICT_EXIT=true {r}; \
                     else btrfs subvolume create \"$d\" && echo \"{qpath}: not in the backup; made empty\"; fi || exit; \
                     touch -r {RUN_DIR}/times.ref \"$p\" || exit; \
                     id=$(btrfs inspect-internal rootid \"$d\") && [ \"$id\" = {id} ] || echo \"{qpath}: ID $id here, {id} in the plan\""
                );
                let why = match x.id {
                    Some(src) if src == id => format!("{}: a snapshot of {}, then made exact from the backup's {} (ID {id}, as on the source)", x.path, x.base, x.backup),
                    Some(src) => format!("{}: a snapshot of {}, then made exact from the backup's {} (ID {id}; {src} on the source)", x.path, x.base, x.backup),
                    None => format!("{}: a snapshot of {}, then made exact from the backup's {}", x.path, x.base, x.backup),
                };
                out.push(Cmd::new(&["sh", "-c", &script]).why(why));
            }
            if !placeholders.is_empty() {
                let mut a = vec!["btrfs".to_string(), "subvolume".into(), "delete".into(), "--commit-after".into()];
                a.extend(placeholders);
                out.push(Cmd { argv: a, ..Cmd::default() });
                out.push(Cmd::new(&["btrfs", "subvolume", "sync", &top]).why("the placeholders gone before quotas are on (no qgroups for them)"));
            }
            for x in plan.shared.iter().filter(|x| x.readonly) {
                out.push(Cmd::new(&["btrfs", "property", "set", "-ts", &format!("{top}/{}", x.path), "ro", "true"]));
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
    let more: String = prune.iter().map(|p| format!(" -o -path {}", shell_quote(p))).collect();
    let list = format!(
        "find {t} \\( -path {t}/dev -o -path {t}/proc -o -path {t}/sys -o -path {t}/run{more} \\) -prune -o -printf '%A@\\t%p\\0' | LC_ALL=C sort -z"
    );
    let b = format!("{RUN_DIR}/atimes.before");
    if what == "before" {
        format!("mkdir -p {RUN_DIR} && {list} > {b}")
    } else if what == "if-missing" {
        format!("[ -s {b} ] || {{ mkdir -p {RUN_DIR} && {list} > {b}; }}")
    } else {
        format!(
            "{list} > {RUN_DIR}/atimes.after && LC_ALL=C comm -z -23 {b} {RUN_DIR}/atimes.after | \\
             while IFS=$'\\t' read -r -d '' a p; do [ -L \"$p\" ] || [ -e \"$p\" ] || continue; touch -a -h -d \"@$a\" -- \"$p\" 2>/dev/null || \
             {{ [ ! -L \"$p\" ] && lsattr -d -- \"$p\" 2>/dev/null | cut -d' ' -f1 | grep -q i && {{ chattr -i -- \"$p\"; touch -a -h -d \"@$a\" -- \"$p\"; chattr +i -- \"$p\"; }}; }}; done"
        )
    }
}

impl Builder<'_> {
    fn boot(&mut self) -> Vec<Cmd> {
        if let Some(fam) = self.fam {
            return self.other_boot(fam);
        }
        if let Some(o) = super::ostree::of(&self.layout.v) {
            return self.ostree_boot(&o.clone());
        }
        if let Some(a) = super::abroot::of(&self.layout.v) {
            return self.abroot_boot(&a.clone());
        }
        let t = self.opts.target.clone();
        let mut out = Vec::new();
        out.push(Cmd::note(format!("refuses to start unless {t}/etc/fstab is the restored system's (its / matches the layout's)")));
        // Keys: the restored key files replace the temporary ones.
        for dm in self.layout.arr(&["storage", "dm_devices"]).to_vec() {
            if dm.at(&["dm", "target"]).and_then(Value::as_str) != Some("crypt") {
                continue;
            }
            let name = s(&dm, "name").unwrap_or_default();
            let dm_name = dm.at(&["dm", "dm_name"]).and_then(Value::as_str).unwrap_or(&name).to_string();
            let Some(e) = self.layout.crypttab_entry(&dm_name, dm.at(&["dm", "luks_uuid"]).and_then(Value::as_str)).cloned() else { continue };
            let open_as = s(&e, "name").unwrap_or(dm_name);
            let Some(k) = s(&e, "key_file").filter(|k| !matches!(k.as_str(), "none" | "-" | "")) else { continue };
            if self.opts.key_file(&open_as).is_some() {
                continue;
            }
            let under = self.layout.slaves(&name).first().and_then(|u| self.names.dev(u)).unwrap_or_default();
            let tmp = format!("{RUN_DIR}/keys/{open_as}");
            out.push(Cmd::new(&["cryptsetup", "luksAddKey", &format!("--key-file={tmp}"), &under, &format!("{t}{k}")]).why(format!("{open_as}: the restored key file {k}")));
            out.push(Cmd::new(&["cryptsetup", "luksRemoveKey", &under, &tmp]));
        }
        for ds in self.layout.v.at(&["storage", "zfs", "tools", "datasets"]).and_then(Value::as_array).cloned().unwrap_or_default() {
            let name = s(&ds, "name").unwrap_or_default();
            if s(&ds, "encryptionroot").as_deref() != Some(name.as_str()) {
                continue;
            }
            let loc = s(&ds, "keylocation").unwrap_or("prompt".into());
            out.push(Cmd::new(&["zfs", "set", &format!("keylocation={loc}"), &name]).why(format!("{name}: unlocks as the source did")));
        }
        for p in self.layout.v.at(&["storage", "zfs", "tools", "imported_pools"]).and_then(Value::as_array).into_iter().flatten() {
            let n = s(p, "name").unwrap_or_default();
            if !self.layout.v.at(&["storage", "zfs", "zpool_cache"]).is_none_or(Value::is_null) {
                out.push(Cmd::new(&["zpool", "set", &format!("cachefile={t}/etc/zfs/zpool.cache"), &n]).why("the restored system's zpool.cache names the new pool"));
            }
        }
        // The target is mounted with the source's own options (dracut
        // records the root's in the initramfs it makes), so what this stage
        // reads gets its atime bumped: each file's atime, as the restore
        // set it, is kept here and put back at the end.
        out.push(Cmd::new(&["sh", "-c", &atimes_cmd(&t, "if-missing", &self.ro_shared())]).why("the restored atimes (the restore stage lists them), to put back after this stage's reads"));
        out.extend(self.rewrite_cmd(false));
        // LVM's devices file (RHEL, Fedora): LVM run in the chroot (by dracut)
        // would record the rescue system's names for the PVs in it; it's
        // put back as restored when the chroot's work is done.
        let devdir = format!("{t}/etc/lvm/devices");
        out.push(Cmd::new(&["sh", "-c", &format!("rm -rf {RUN_DIR}/lvm-devices; [ ! -d {devdir} ] || {{ cp -a {devdir} {RUN_DIR}/lvm-devices && touch -r {t}/etc/lvm {RUN_DIR}/lvm-devices.parent; }}")]).why("the restored LVM devices file, kept"));
        let lvm_devices_back =
            Cmd::new(&["sh", "-c", &format!("[ ! -d {RUN_DIR}/lvm-devices ] || {{ rm -rf {devdir} && cp -a {RUN_DIR}/lvm-devices {devdir} && touch -r {RUN_DIR}/lvm-devices.parent {t}/etc/lvm; }}")]).why("the restored LVM devices file, put back");
        for d in ["dev", "proc", "sys", "run"] {
            out.push(Cmd::new(&["mount", "--rbind", "--make-rslave", &format!("/{d}"), &format!("{t}/{d}")]));
        }
        // NixOS and Guix System (crate::store): their notes and paths.
        let store = crate::store::system(self.layout.str_at(&["os", "id"]));
        let gentoo = self.layout.str_at(&["os", "id"]) == Some("gentoo");
        if gentoo {
            let cmds = self.gentoo_initramfs(&t);
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
            _ if self.proxmox_boot().is_some() => {
                let cmds = self.proxmox_boot_cmds(&t, via_shim);
                out.extend(cmds);
            }
            // Gentoo through shim: grub-install would put an unsigned GRUB
            // where the signed one is.
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
            ("grub", false) if store.is_some() => {
                let st = store.expect("a store system");
                let devs = self.grub_bios_devices();
                if devs.is_empty() {
                    self.problems.push("BIOS GRUB: no disk's MBR holds GRUB".into());
                }
                match st.grub_install {
                    Some(install) => {
                        let pick = st.bios_grub_pick(&t);
                        for d in devs {
                            out.push(Cmd::new(&["sh", "-c", &format!("{pick}; chroot {t} \"$g/{install}\" --target=i386-pc --boot-directory=/boot {d}")]).why(format!("{}'s GRUB; the restored grub.cfg stays", st.store_name)));
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
                            out.push(
                                Cmd::new(&[
                                    "sh",
                                    "-c",
                                    &format!("d={q}; [ -f \"$d/boot.img\" ] && [ -f \"$d/core.img\" ] || {{ echo \"$d: no boot.img or core.img (the source's, restored with /boot/grub) to write to {d}\" >&2; exit 1; }}; grub-bios-setup --directory=\"$d\" {d}"),
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
                    out.push(Cmd::new(&["chroot", &t, self.grub_tool("install"), "--target=i386-pc", &d]));
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
            ("grub", true) if self.grub_dir() == "/boot/grub2" => {
                // Fedora: grub2-install refuses EFI (it can't keep Secure
                // Boot); shim and the signed GRUB come back with the ESP.
                self.notes.push("UEFI GRUB on Fedora: the ESP's shim and GRUB are the restored ones; only grub.cfg is remade".into());
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
            ("grub", true) if store.is_some() => {
                let st = store.expect("a store system");
                self.notes.push(st.uefi_grub.into());
                let img = self.esp_file("/grubx64.efi").unwrap_or_else(|| format!("EFI/{}/grubx64.efi", self.bootloader_id()));
                out.extend(self.store_removable(&img));
                self.check_store_grub_cfg(st);
            }
            ("grub", true) => {
                let esp = self.layout.str_at(&["boot", "esps", "0", "mounted_at"]).unwrap_or("/boot/efi").to_string();
                let id = self.bootloader_id();
                let mut a = vec!["chroot".to_string(), t.clone(), "grub-install".into(), "--target=x86_64-efi".into(), format!("--efi-directory={esp}"), format!("--bootloader-id={id}"), "--no-nvram".into()];
                if self.opts.removable {
                    a.push("--force-extra-removable".into());
                }
                // --reformat: a fallback loader that's a copy of this GRUB
                // (Calamares's installEFIFallback) reads only the old type,
                // so firmware that falls back to it would stop in grub rescue.
                // It's renewed with GRUB.  (FAT finds EFI/BOOT in any case.)
                let (fallback, loader) = (format!("{t}{esp}/EFI/BOOT/BOOTX64.EFI"), format!("{t}{esp}/EFI/{id}/grubx64.efi"));
                let renew = !self.opts.reformats.is_empty() && !self.opts.removable;
                if renew {
                    out.push(Cmd::new(&["sh", "-c", &format!("rm -f {RUN_DIR}/fallback-is-grub; [ ! -f {fallback} ] || ! cmp -s {fallback} {loader} || touch {RUN_DIR}/fallback-is-grub")]).why("--reformat: is the fallback loader a copy of GRUB?"));
                }
                out.push(Cmd { argv: a, ..Cmd::default() });
                if renew {
                    out.push(Cmd::new(&["sh", "-c", &format!("[ ! -e {RUN_DIR}/fallback-is-grub ] || cp {loader} {fallback}")]).why("--reformat: the fallback loader, a copy of GRUB, renewed with it"));
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
                let img = self.esp_file("/systemd-bootx64.efi").unwrap_or_else(|| "EFI/systemd/systemd-bootx64.efi".into());
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
        }
        for d in ["run", "sys", "proc", "dev"] {
            out.push(Cmd::new(&["umount", "--recursive", &format!("{t}/{d}")]));
        }
        out.push(lvm_devices_back);
        out.extend(self.boot_tail(&t));
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

    /// How every Linux boot stage ends (ostree's too, load/ostree.rs): the
    /// atimes put back, then the read-only btrfs subvolumes made so.
    pub(crate) fn boot_tail(&mut self, t: &str) -> Vec<Cmd> {
        let mut out = Vec::new();
        // Last but for read-only flags: nothing may read the tree after this.
        out.push(Cmd::new(&["bash", "-c", &atimes_cmd(t, "restore", &self.ro_shared())]).why("the atimes this stage's reads changed, put back"));
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
            let dev = fs.at(&["devices", "0"]).and_then(Value::as_str).and_then(|d| self.names.dev(d)).unwrap_or_default();
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
        if matches!(self.layout.str_at(&["os", "id"]), Some("debian" | "ubuntu" | "linuxmint" | "zorin" | "neon" | "anduinos" | "kali")) {
            return Cmd::new(&["chroot", t, "env", &lang, "update-grub"]);
        }
        let cfg = format!("{}/grub.cfg", self.grub_dir());
        Cmd::new(&["chroot", t, "env", &lang, self.grub_tool("mkconfig"), "-o", &cfg])
    }

    /// grub.cfg remade with os-prober out of the way: in the rescue system
    /// it would find the rescue system's own disks and list them.
    fn remake_grub_cfg(&self, t: &str, out: &mut Vec<Cmd>) {
        let p = format!("{t}/usr/bin/os-prober");
        out.push(Cmd::new(&["sh", "-c", &format!("[ ! -e {p} ] || mount --bind {t}/usr/bin/true {p}")]).why("os-prober off: it would list the rescue system's disks"));
        out.push(self.update_grub(t));
        out.push(Cmd::new(&["sh", "-c", &format!("! grep -q ' {p} ' /proc/self/mountinfo || umount {p}")]));
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
        format!(
            "AnduinOS: shim, MokManager and AnduinOS's GRUB, signed with {key}, are the restored ESP's; grub-install --target=x86_64-efi isn't run (AnduinOS's is plain GRUB and would write an unsigned image), only grub.cfg is remade.  On the same machine its firmware holds the key; on other hardware with Secure Boot on, enroll it again (`mokutil --import {cert}`, then confirm it at MokManager on the next boot) or turn Secure Boot off"
        )
    }

    /// /boot/grub, or Fedora's /boot/grub2.
    fn grub_dir(&self) -> &str {
        self.layout.str_at(&["boot", "grub", "dir"]).unwrap_or("/boot/grub")
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
            Some("dracut") if ["eos-dracut", "garuda-dracut-support"].iter().any(|p| self.layout.str_at(&["boot", "packages", *p]).is_some()) => Cmd::new(&["chroot", t, "dracut-rebuild"]),
            // Not --regenerate-all: it takes every directory in /lib/modules, and
            // a removed kernel can leave one behind with only depmod's indexes.
            Some("dracut") => Cmd::new(&["chroot", t, "sh", "-c", "n=0; for k in /lib/modules/*; do [ -d \"$k/kernel\" ] || continue; dracut --force --kver \"${k##*/}\" || exit; n=$((n + 1)); done; [ \"$n\" -gt 0 ]"]),
            // Alpine: mkinitfs KVER writes /boot/initramfs-FLAVOR.
            Some("mkinitfs") => Cmd::new(&["chroot", t, "sh", "-c", "for k in /lib/modules/*; do mkinitfs \"${k##*/}\" || exit; done"]),
            // PikaOS: what booster's own update-initramfs runs for each
            // kernel, without its dpkg-trigger and backup paths; host-only
            // as booster.yaml says, from the chroot's /sys (this machine's).
            // The same guard as dracut's loop.
            Some("booster") => Cmd::new(&["chroot", t, "sh", "-c", "n=0; for k in /lib/modules/*; do [ -d \"$k/kernel\" ] || continue; k=${k##*/}; booster build --force --kernel-version \"$k\" \"/boot/booster.img-$k\" || exit; n=$((n + 1)); done; [ \"$n\" -gt 0 ]"]),
            _ => Cmd::new(&["chroot", t, "update-initramfs", "-u", "-k", "all"]),
        })
    }

    /// Gentoo's initramfs step (docs/design/gentoo.md, decisions 2, 3 and
    /// 6): installkernel's generator as the dump read it, with bko-holt's
    /// own commands and no Portage or Python in the chroot.  dracut makes
    /// each kernel's image under the name installkernel gives it
    /// (/boot/initramfs-K.img), named on the command line: left to itself,
    /// dracut 112 could pick /efi/MACHINE-ID/K/initrd or
    /// /lib/modules/K/initrd.  `none` makes nothing.  Everything else is
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
                    out.push(Cmd::new(&["chroot", t, "dracut", "--force", &format!("/boot/initramfs-{v}.img"), &v]).why(format!("kernel {v}'s initramfs, by the name installkernel gives it")));
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

    /// The path on the first ESP, as the dump listed it, of the file whose
    /// name ends in `suffix` (any case), outside EFI/BOOT.
    fn esp_file(&self, suffix: &str) -> Option<String> {
        let files = self.layout.arr(&["boot", "esps", "0", "contents", "files"]).to_vec();
        files
            .iter()
            .filter_map(|f| s(f, "path"))
            .find(|p| p.to_ascii_lowercase().ends_with(suffix) && !p.to_ascii_lowercase().starts_with("efi/boot/"))
    }

    /// A store system (NixOS, Guix System) with --removable: the ESP's
    /// loader image `img` (a path on the ESP) is copied to
    /// EFI/BOOT/BOOTX64.EFI, unless the restored ESP has one (bootctl
    /// install, which NixOS runs, writes it).  Nothing runs in the chroot.
    fn store_removable(&self, img: &str) -> Vec<Cmd> {
        if !self.opts.removable {
            return Vec::new();
        }
        let esp = format!("{}{}", self.opts.target, self.layout.str_at(&["boot", "esps", "0", "mounted_at"]).unwrap_or("/boot").trim_end_matches('/'));
        let (src, dir) = (shell_quote(&format!("{esp}/{img}")), shell_quote(&format!("{esp}/EFI/BOOT")));
        vec![Cmd::new(&["sh", "-c", &format!("[ -e {dir}/BOOTX64.EFI ] || {{ mkdir -p {dir} && cp {src} {dir}/BOOTX64.EFI; }}")]).why("--removable: the ESP's loader at the fallback path too")]
    }

    /// The target disks whose source has GRUB's BIOS core in its BIOS boot
    /// partition, as the dump's boot_code found it (a UEFI install that is
    /// a BIOS install too).  A disk with a BIOS boot partition and no core
    /// recorded gets a note: it boots by UEFI only.
    fn grub_core_partition_devices(&mut self) -> Vec<String> {
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
        for esp in self.layout.arr(&["boot", "esps"]) {
            let files = esp.at(&["contents", "files"]).and_then(Value::as_array).into_iter().flatten();
            for path in files.filter_map(|f| f.get("path").and_then(Value::as_str)) {
                let low = path.to_ascii_lowercase();
                if low.ends_with("/shimx64.efi") || low.ends_with("/grubx64.efi") {
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
    /// one at the front).
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
            if proxmox_sd.iter().any(|d| d == part) && file.replace('/', "\\").trim_start_matches('\\').eq_ignore_ascii_case("EFI\\systemd\\systemd-bootx64.efi") {
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
            out.push(Cmd::new(&["efibootmgr", "--quiet", "--create", "--disk", &format!("/dev/{t}"), "--part", &num.to_string(), "--label", &desc, "--loader", &file]));
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
        let mut out = vec![Cmd::new(&["umount", "--recursive", t])];
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
        out.push(
            Cmd::new(&[
                "sh",
                "-c",
                &format!(
                    "me=$(readlink /proc/self/ns/mnt); seen=\" $me \"; \
                     at() {{ awk -v t={t} '{{ m = $5; if (m == t || length(m) > length(t) && substr(m, length(m) - length(t) + 1) == t) print m }}'; }}; \
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
                ),
            ])
            .why("other mount namespaces' copies of the target's mounts"),
        );
        out.extend(self.closers.iter().rev().cloned());
        // Stopped, each md member gets back the source's logical block size
        // field: the rescue system's kernel may have written one the
        // source's kernel takes for non-zero padding, and refuses.
        let exe = std::env::current_exe().map(|p| p.to_string_lossy().into_owned()).unwrap_or_else(|_| "bko-holt".into());
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

#[cfg(test)]
mod tests {
    use super::*;
    use crate::load::layout::{sample, Layout};

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
        assert!(r.contains("--clone-from /mnt/src=/run/bko-holt/btrfs/"), "{r}");
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
        assert!(t.contains("btrfs subvolume create /run/bko-holt/btrfs/b7b7b7b7-0000-4000-8000-000000000003/@rootfs"), "{t}");
        assert!(t.contains("btrfs property set /run/bko-holt/btrfs/b7b7b7b7-0000-4000-8000-000000000003/@rootfs compression zstd"), "{t}");
        assert!(b.problems.is_empty(), "{:?}", b.problems);
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
        let top = "/run/bko-holt/btrfs/b7b7b7b7-0000-4000-8000-000000000003";
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
        let top = "/run/bko-holt/btrfs/b7b7b7b7-0000-4000-8000-000000000003";
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
        let top = "/run/bko-holt/btrfs/b7b7b7b7-0000-4000-8000-000000000003";
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
        let top = "/run/bko-holt/btrfs/b7b7b7b7-0000-4000-8000-000000000003";
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
        let top = "/run/bko-holt/btrfs/b7b7b7b7-0000-4000-8000-000000000003";
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
        let dracut = "chroot /mnt/bko-target sh -c 'n=0; for k in /lib/modules/*; do [ -d \"$k/kernel\" ] || continue; dracut --force --kver \"${k##*/}\" || exit; n=$((n + 1)); done; [ \"$n\" -gt 0 ]'\n";
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
        assert!(bt.contains("chroot /mnt/bko-target dracut-rebuild\n") && !bt.contains("--regenerate-all"), "{bt}");
        let garuda = sample::bios_mbr()
            .replace(r#""id": "debian", "version_id": "13""#, r#""id": "garuda""#)
            .replace(r#""efi": null,"#, r#""efi": null, "initramfs": {"generator": "dracut"}, "packages": {"garuda-dracut-support": "1.6.0-2"},"#);
        let bt = text(built_from(&garuda, &[]).stage(Stage::Boot));
        assert!(bt.contains("chroot /mnt/bko-target dracut-rebuild\n") && !bt.contains("--regenerate-all") && !bt.contains("--kver"), "{bt}");
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

    /// Chimera: Debian's initramfs-tools, without debconf or update-grub's
    /// Debian side; GRUB by grub-PLATFORM packages and grub-mkconfig.  What
    /// runs in the chroot runs on FreeBSD's userland (chimerautils).
    #[test]
    fn chimera_boot_tools() {
        let bios = sample::bios_mbr()
            .replace(r#""id": "debian", "version_id": "13""#, r#""id": "chimera""#)
            .replace(r#""efi": null,"#, r#""efi": null, "initramfs": {"generator": "initramfs-tools"}, "packages": {"grub": "2.14-r1", "grub-i386-pc": "2.14-r1", "initramfs-tools": "0.148.3-r2"},"#)
            .replace(r#""grub": {"debconf": {"grub-pc/install_devices": "/dev/disk/by-id/virtio-x"}}"#, r#""grub": {"dir": "/boot/grub", "debconf": null}"#);
        let b = built_from(&bios, &[]);
        let bt = text(b.stage(Stage::Boot));
        assert!(b.problems.is_empty(), "{:?}", b.problems);
        assert!(bt.contains("chroot /mnt/bko-target update-initramfs -u -k all\n"), "{bt}");
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
        assert!(ut.contains("chroot /mnt/bko-target update-initramfs -u -k all\n"), "{ut}");
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
        let gen = "chroot /mnt/bko-target sh -c 'gen-systemd-boot; r=$?; [ \"$r\" -ne 1 ] || { echo '\\''gen-systemd-boot: warning: it made no entries (disabled, or bootctl says systemd-boot is not installed); the restored ones stay'\\'' >&2; r=0; }; exit \"$r\"'\n";
        let initramfs = st.find("chroot /mnt/bko-target update-initramfs -u -k all\n").expect(&st);
        let bootctl = st.find("chroot /mnt/bko-target bootctl install --no-variables\n").expect(&st);
        let made = st.find(gen).expect(&st);
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
        assert!(props.contains(&("keylocation".into(), "file:///run/bko-holt/keys/rpool".into())), "{props:?}");
        assert!(b.problems.is_empty(), "{:?}", b.problems);
        let keystore = crate::json::parse(r#"{"name": "rpool/keystore", "type": "volume", "encryption": "off", "volsize": 20971520, "properties": {}}"#).unwrap();
        let props = b.ds_props(&keystore, false);
        assert!(props.contains(&("encryption".into(), "off".into())), "{props:?}");
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
            "bcachefs format --force --version=1037 --uuid=c9cbba71-4054-4849-a19c-e7d40fb5b1e4 --block_size=4096 --btree_node_size=262144 --metadata_checksum=crc32c --data_checksum=crc32c --str_hash=siphash /dev/vdb3"
        );
        // version_min, not the version a newer kernel raised it to (nixos2605's).
        let b2 = crate::json::parse(r#"{"type": "bcachefs", "version": "1.39", "version_min": "1.13"}"#).unwrap();
        assert_eq!(mkfs_bcachefs("/dev/vdb3", &b2).join(" "), "bcachefs format --force --version=1037 /dev/vdb3");
        // No version, or one that doesn't parse: no flag.
        for v in [r#"{"type": "bcachefs"}"#, r#"{"type": "bcachefs", "version": "1.x", "version_min": "junk"}"#] {
            let x = crate::json::parse(v).unwrap();
            assert_eq!(mkfs_bcachefs("/dev/vdb3", &x).join(" "), "bcachefs format --force /dev/vdb3");
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
                    ThinLv { name: "root-a".into(), bytes: 19 << 30, device_id: 1, attrs: LvAttrs::default() },
                    ThinLv { name: "root-b".into(), bytes: 19 << 30, device_id: 2, attrs: LvAttrs::default() }
                ],
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

    /// Refused in version 1, each by name: thin snapshots, external
    /// origins, a pool on RAID or cached, a thin LV of no pool.
    #[test]
    fn thin_refusals() {
        let vg = crate::probe::lvm::sample::thin_vg().compact();
        let err = |from: &str, to: &str| {
            let changed = vg.replacen(from, to, 1);
            assert_ne!(changed, vg, "{from}");
            vg_metadata(&crate::json::parse(&changed).unwrap()).unwrap_err()
        };
        let snap = err("\"device_id\":2", "\"device_id\":2,\"origin\":\"root-a\"");
        assert_eq!(snap, "thin LV vos-root/root-b is a thin snapshot of root-a; version 1 doesn't make thin snapshots");
        let ext = err("\"device_id\":2", "\"device_id\":2,\"external_origin\":\"init\"");
        assert_eq!(ext, "thin LV vos-root/root-b has the external origin init; version 1 doesn't make thin LVs with one");
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
}
