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
use std::collections::BTreeMap;

/// A closer for a device just let go of (an LV, a LUKS mapping, an md
/// array): udev probes a device when the last writer closes it, and while
/// its worker has it open the device can't be stopped.  So udev is waited
/// for first, and the command is tried up to five times, a second apart.
fn settled(argv: &[&str]) -> Cmd {
    let cmd = argv.iter().map(|a| shell_quote(a)).collect::<Vec<_>>().join(" ");
    Cmd::new(&["sh", "-c", &format!("i=0; until udevadm settle; {cmd}; do i=$((i + 1)); [ $i -lt 5 ] || exit 1; sleep 1; done")])
}

/// One step: a command, or a file to write (`write` set), or only a note.
#[derive(Debug, Clone, PartialEq, Default)]
pub struct Cmd {
    pub argv: Vec<String>,
    pub stdin: Option<String>,
    /// For a file to write: its path; the contents are `stdin`.
    pub write: Option<String>,
    pub why: Option<String>,
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

    pub fn why(mut self, w: impl Into<String>) -> Cmd {
        self.why = Some(w.into());
        self
    }

    pub fn input(mut self, s: String) -> Cmd {
        self.stdin = Some(s);
        self
    }

    pub fn is_note(&self) -> bool {
        self.argv.is_empty() && self.write.is_none()
    }

    /// The step as a person would type it in a shell.
    pub fn shell(&self) -> String {
        let mut out = String::new();
        if let Some(w) = &self.why {
            for l in w.lines() {
                out += &format!("# {l}\n");
            }
        }
        if self.is_note() {
            return out;
        }
        let line = match &self.write {
            Some(p) => format!("cat > {}", shell_quote(p)),
            None => self.argv.iter().map(|a| shell_quote(a)).collect::<Vec<_>>().join(" "),
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
    };
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

impl Builder<'_> {
    fn partition(&mut self) -> Vec<Cmd> {
        if let Some(fam) = self.fam {
            return self.other_partition(fam);
        }
        let mut out = Vec::new();
        for (src, tgt) in self.names.mapping.pairs.clone() {
            let Some(d) = self.layout.disk(&src) else { continue };
            let dev = format!("/dev/{tgt}");
            out.push(Cmd::new(&["wipefs", "--all", &dev]).why(format!("{src} -> {dev}: clear the old table and signatures")));
            if d.table.is_none() {
                out.push(Cmd::note(format!("{src} has no partition table: its content goes on {dev} itself")));
                continue;
            }
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

/// The volume group as `vgcfgrestore` reads it, from the dump's view of it.
pub fn vg_metadata(vg: &Value) -> Result<String, String> {
    use crate::json::Obj;
    let name = s(vg, "name").ok_or("a volume group without a name")?;
    let ext = num(vg, "extent_size_bytes").ok_or("no extent size")? / 512;
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
        let mut out = Vec::new();
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
        for pv in vg.get("physical_volumes").and_then(Value::as_array).into_iter().flatten() {
            let src = s(pv, "device").unwrap_or_default();
            let dev = self.names.dev(&src).unwrap_or_else(|| format!("<{src}>"));
            let id = s(pv, "id").unwrap_or_default();
            out.push(Cmd::new(&["pvcreate", "--uuid", &id, "--restorefile", &file, &dev]));
        }
        out.push(Cmd::new(&["vgcfgrestore", "--file", &file, &name]));
        out.push(Cmd::new(&["vgchange", "--activate", "y", &name]));
        self.closers.push(settled(&["vgchange", "--activate", "n", &name]));
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
        for (f, state) in p.get("features").and_then(Value::as_object).into_iter().flatten() {
            if matches!(state.as_str(), Some("enabled" | "active")) {
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
        let why = format!("ZFS pool {name}, with the source's feature flags only");
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
const EXT_SETTABLE: [&str; 30] = [
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
    // Newer features: only named when on, so an older mkfs.xfs isn't asked
    // about options it doesn't know.
    if f.contains(&"metadir") {
        m.push("metadir=1".into());
    }
    let mut i = vec![format!("sparse={}", on("sparse")), format!("nrext64={}", on("nrext64"))];
    if let Some(sz) = num(c, "inode_size") {
        i.push(format!("size={sz}"));
    }
    if f.contains(&"exchange") {
        i.push("exchange=1".into());
    }
    let mut n = vec![format!("ftype={}", on("ftype"))];
    if let Some(d) = num(c, "dir_block_size") {
        n.push(format!("size={d}"));
    }
    if f.contains(&"parent") {
        n.push("parent=1".into());
    }
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
const BTRFS_FEATURES: [(&str, &str); 9] = [
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
const F2FS_SETTABLE: [&str; 13] = [
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

/// bcachefs format with the source's UUID, label and options.  The tools'
/// release decides the on-disk version: the rescuer's must make one the
/// source's kernel reads.
pub fn mkfs_bcachefs(dev: &str, c: &Value) -> Vec<String> {
    let mut a = vec!["bcachefs".to_string(), "format".into(), "--force".into()];
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
const MADE_BY_ASSEMBLE: [&str; 5] = ["linux_raid_member", "crypto_LUKS", "LVM2_member", "zfs_member", "xfs_external_log"];

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
            }
            for p in &d.parts {
                if !p.content.is_null() {
                    out.push((p.name.clone(), p.content.clone()));
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
                if let (Some(n), Some(c)) = (s(x, "name"), x.get("content").filter(|c| !c.is_null())) {
                    out.push((n, c.clone()));
                }
            }
        }
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
            match ty.as_str() {
                "ext2" | "ext3" | "ext4" => {
                    out.push(Cmd { argv: mke2fs(&dev, &c), why: Some(why), ..Cmd::default() });
                    // The exact count: mke2fs takes only a percentage, and
                    // rounds it down.
                    if let Some(r) = num(&c, "reserved_blocks") {
                        out.push(Cmd::new(&["tune2fs", "-r", &r.to_string(), &dev]));
                    }
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
                    self.btrfs_inside(&uuid, &devs[0], &fs, &mut out);
                }
                "vfat" | "msdos" if self.fam.is_none_or(|f| f.on_linux()) && self.layout.device(&src).is_none_or(|v| v.get("esp_image").is_none()) => {
                    out.push(Cmd { argv: mkfs_fat(&dev, &c), why: Some(why), ..Cmd::default() })
                }
                "f2fs" => out.push(Cmd { argv: mkfs_f2fs(&dev, &c), why: Some(why), ..Cmd::default() }),
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
                }
                "nilfs2" => {
                    out.push(Cmd { argv: mkfs_nilfs2(&dev, &c), why: Some(why), ..Cmd::default() });
                    if let Some(t) = nilfs_tune(&dev, &c) {
                        out.push(Cmd { argv: t, why: Some("the source's UUID (mkfs.nilfs2 can't set one)".into()), ..Cmd::default() });
                    }
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
                }
                "swap" if self.fam.is_some_and(|f| !f.on_linux()) => self.notes.push(format!("{src}: swap needs no format here")),
                "swap" => {
                    if c.get("hibernation_image").is_some_and(|h| !h.is_null()) {
                        self.notes.push(format!("{src}: the source's swap held a hibernation image; the new swap doesn't"));
                    }
                    out.push(Cmd { argv: mkswap(&dev, &c), why: Some(why), ..Cmd::default() });
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

    fn btrfs_inside(&mut self, uuid: &str, dev: &str, fs: &Value, out: &mut Vec<Cmd>) {
        let subs = fs.get("subvolumes").and_then(Value::as_array).cloned().unwrap_or_default();
        let quota = fs.get("quota_enabled").and_then(Value::as_bool).unwrap_or(false);
        let default_id = fs.at(&["default_subvolume", "id"]).and_then(Value::as_u64).unwrap_or(5);
        if subs.is_empty() && !quota && default_id == 5 {
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
        for (path, v) in &list {
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
            if origin.is_none() && v.get("parent_uuid").is_some_and(|p| !p.is_null()) {
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
                Some((p, _)) => out.push(Cmd::new(&["btrfs", "subvolume", "set-default", &format!("{top}/{p}")])),
                None => self.problems.push(format!("btrfs {uuid}: the default subvolume (ID {default_id}) isn't in its subvolume list")),
            }
        }
        if quota {
            out.push(Cmd::new(&["btrfs", "quota", "enable", &top]));
            out.push(Cmd::new(&["btrfs", "quota", "rescan", "-w", &top]));
            let qgroups = fs.get("qgroups").and_then(Value::as_array).cloned().unwrap_or_default();
            // Higher-level qgroups (snapper's 1/0), their members, and limits.
            for q in &qgroups {
                let Some(id) = s(q, "qgroupid") else { continue };
                if !id.starts_with("0/") {
                    out.push(Cmd::new(&["btrfs", "qgroup", "create", &id, &top]));
                }
            }
            for q in &qgroups {
                let Some(id) = s(q, "qgroupid") else { continue };
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
        out.push(Cmd::new(&["umount", &top]));
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
                self.notes.push(format!("ZFS volume {name}: made empty; archivers back up files, not zvols"));
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

/// fstab options that only matter at the restored system's own boot.
fn mount_options(o: &str) -> String {
    let keep: Vec<&str> = o
        .split(',')
        .filter(|x| !x.is_empty())
        .filter(|x| !matches!(*x, "defaults" | "noauto" | "auto" | "nofail" | "_netdev" | "user" | "users" | "owner" | "sw"))
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
        a.extend(["restore".into(), "--numeric-id".into(), self.opts.snapshot.clone(), self.opts.target.clone()]);
        if self.fam.is_some() {
            // The boot stage there reads nothing of the restored tree.
            return vec![Cmd { argv: a, why: Some(format!("snapshot {} of {repo}, with BackOtter's restore flags", self.opts.snapshot)), ..Cmd::default() }];
        }
        vec![
            Cmd { argv: a, why: Some(format!("snapshot {} of {repo}, with BackOtter's restore flags", self.opts.snapshot)), ..Cmd::default() },
            Cmd::new(&["sh", "-c", &atimes_cmd(&self.opts.target, "before")]).why("the atimes the restore set, for boot to put back after its reads"),
        ]
    }
}

// ---- boot, close --------------------------------------------------------

/// Every path's atime under the target (not the bind-mounted /dev, /proc,
/// /sys, /run): "before" lists them to RUN_DIR/atimes.before (the restore
/// stage, right after the archiver: nothing has read the tree yet);
/// "if-missing" does so unless it's done (boot, after a restore made some
/// other way); "restore" lists them again and puts back each that changed
/// (testing a symlink with -L first: -e would follow it, and reading a
/// symlink bumps its atime, perhaps after it was put back).
fn atimes_cmd(t: &str, what: &str) -> String {
    let list = format!(
        "find {t} \\( -path {t}/dev -o -path {t}/proc -o -path {t}/sys -o -path {t}/run \\) -prune -o -printf '%A@\\t%p\\0' | LC_ALL=C sort -z"
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
        out.push(Cmd::new(&["sh", "-c", &atimes_cmd(&t, "if-missing")]).why("the restored atimes (the restore stage lists them), to put back after this stage's reads"));
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
        let nixos = self.layout.str_at(&["os", "id"]) == Some("nixos");
        match self.initramfs(&t) {
            _ if nixos => self.notes.push("NixOS: each generation's kernel and initrd are in /boot/kernels, restored with /boot; nothing to rebuild".into()),
            Some(c) => out.push(c),
            None => self.notes.push("the initramfs came prebuilt with the kernel and was restored with it; nothing to rebuild".into()),
        }
        let loader = self.layout.str_at(&["boot", "loader", "name"]).unwrap_or("unknown").to_string();
        let uefi = self.layout.uefi();
        match (loader.as_str(), uefi) {
            // NixOS: GRUB is in the Nix store, and its grub.cfg is written
            // by switch-to-configuration, which would install GRUB on the
            // source's device names (here, the rescue system's disks).  The
            // restored grub.cfg names the filesystems by UUID, which the
            // new ones keep: only the boot code is installed, by the newest
            // GRUB in the store.
            ("grub", false) if nixos => {
                let devs = self.grub_bios_devices();
                if devs.is_empty() {
                    self.problems.push("BIOS GRUB: no disk's MBR holds GRUB".into());
                }
                let pick = format!(
                    "g=$(for d in {t}/nix/store/*-grub-2.*/lib/grub/i386-pc; do d=${{d%/lib/grub/i386-pc}}; echo \"${{d#*-}} ${{d#{t}}}\"; done | sort -V | tail -n 1 | cut -d' ' -f2); [ -n \"$g\" ] || {{ echo 'no BIOS GRUB in the Nix store' >&2; exit 1; }}"
                );
                for d in devs {
                    out.push(Cmd::new(&["sh", "-c", &format!("{pick}; chroot {t} \"$g/bin/grub-install\" --target=i386-pc --boot-directory=/boot {d}")]).why("the Nix store's GRUB; the restored grub.cfg stays"));
                }
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
            ("grub", true) if self.grub_dir() == "/boot/grub2" => {
                // Fedora: grub2-install refuses EFI (it can't keep Secure
                // Boot); shim and the signed GRUB come back with the ESP.
                self.notes.push("UEFI GRUB on Fedora: the ESP's shim and GRUB are the restored ones; only grub.cfg is remade".into());
                self.remake_grub_cfg(&t, &mut out);
            }
            ("grub", true) => {
                let esp = self.layout.str_at(&["boot", "esps", "0", "mounted_at"]).unwrap_or("/boot/efi").to_string();
                let id = self.bootloader_id();
                let mut a = vec!["chroot".to_string(), t.clone(), "grub-install".into(), "--target=x86_64-efi".into(), format!("--efi-directory={esp}"), format!("--bootloader-id={id}"), "--no-nvram".into()];
                if self.opts.removable {
                    a.push("--force-extra-removable".into());
                }
                out.push(Cmd { argv: a, ..Cmd::default() });
                self.remake_grub_cfg(&t, &mut out);
            }
            ("systemd-boot", true) => {
                out.push(Cmd::new(&["chroot", &t, "bootctl", "install", "--no-variables"]));
            }
            // Limine on UEFI is files on the ESP (its EFI binary,
            // limine.conf), restored with it; only the boot entries are made.
            ("limine", true) => self.notes.push("Limine: its EFI binary and limine.conf came back with the ESP; nothing to install".into()),
            (other, _) => self.problems.push(format!("boot loader {other} ({}) isn't one bko-holt load can install", if uefi { "UEFI" } else { "BIOS" })),
        }
        if uefi && !self.opts.no_nvram {
            out.extend(self.efi_entries());
        }
        out.push(Cmd::new(&["mkdir", "-p", &format!("{t}/var/log/bko-holt")]));
        out.push(Cmd::new(&["cp", "-a", &format!("{RUN_DIR}/log/."), &format!("{t}/var/log/bko-holt/")]).why("this restore's log, on the restored system"));
        if self.layout.str_at(&["os", "id"]) != Some("nixos") {
            // The restore kept every file's SELinux label; what this stage
            // wrote (initramfs images, grub.cfg, the log) gets the policy's.
            out.push(
                Cmd::new(&[
                    "chroot",
                    &t,
                    "sh",
                    "-c",
                    "[ -f /etc/selinux/config ] || exit 0; . /etc/selinux/config; [ \"${SELINUX:-disabled}\" != disabled ] || exit 0; f=/etc/selinux/${SELINUXTYPE:-targeted}/contexts/files/file_contexts; [ ! -f \"$f\" ] || ! command -v setfiles >/dev/null || setfiles \"$f\" /boot /var/log/bko-holt",
                ])
                .why("SELinux labels for the files the boot stage wrote"),
            );
        }
        for d in ["run", "sys", "proc", "dev"] {
            out.push(Cmd::new(&["umount", "--recursive", &format!("{t}/{d}")]));
        }
        out.push(lvm_devices_back);
        // Last but for read-only flags: nothing may read the tree after this.
        out.push(Cmd::new(&["bash", "-c", &atimes_cmd(&t, "restore")]).why("the atimes this stage's reads changed, put back"));
        // Read-only btrfs subvolumes: set now that the restore filled them,
        // and after the atimes are put back (touch can't on a read-only one).
        for fs in self.layout.arr(&["storage", "btrfs"]).to_vec() {
            let uuid = s(&fs, "uuid").unwrap_or_default();
            let ro: Vec<String> = fs
                .get("subvolumes")
                .and_then(Value::as_array)
                .into_iter()
                .flatten()
                .filter(|v| v.get("readonly").and_then(Value::as_bool) == Some(true))
                .filter_map(|v| s(v, "path").map(|p| p.trim_start_matches("<FS_TREE>/").to_string()))
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
        if matches!(self.layout.str_at(&["os", "id"]), Some("debian" | "ubuntu" | "linuxmint" | "zorin")) {
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
            // Not --regenerate-all: it takes every directory in /lib/modules, and
            // a removed kernel can leave one behind with only depmod's indexes.
            Some("dracut") => Cmd::new(&["chroot", t, "sh", "-c", "n=0; for k in /lib/modules/*; do [ -d \"$k/kernel\" ] || continue; dracut --force --kver \"${k##*/}\" || exit; n=$((n + 1)); done; [ \"$n\" -gt 0 ]"]),
            // Alpine: mkinitfs KVER writes /boot/initramfs-FLAVOR.
            Some("mkinitfs") => Cmd::new(&["chroot", t, "sh", "-c", "for k in /lib/modules/*; do mkinitfs \"${k##*/}\" || exit; done"]),
            _ => Cmd::new(&["chroot", t, "update-initramfs", "-u", "-k", "all"]),
        })
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
            (parts.len() >= 3 && parts[0].eq_ignore_ascii_case("EFI") && !parts[1].eq_ignore_ascii_case("BOOT")).then(|| parts[1].to_ascii_lowercase())
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
    fn efi_entries(&mut self) -> Vec<Cmd> {
        let entries = self.layout.arr(&["boot", "efi", "entries"]).to_vec();
        let order: Vec<String> = self.layout.v.at(&["boot", "efi", "boot_order"]).and_then(Value::as_array).into_iter().flatten().filter_map(|x| x.as_str().map(str::to_string)).collect();
        let mut picked: Vec<&Value> = Vec::new();
        for n in &order {
            if let Some(e) = entries.iter().find(|e| s(e, "number").as_deref() == Some(n.as_str())) {
                picked.push(e);
            }
        }
        let mut out = Vec::new();
        for e in picked.into_iter().rev() {
            let desc = s(e, "description").unwrap_or_default();
            let (Some(file), Some(part)) = (s(e, "file"), e.at(&["partition", "device"]).and_then(Value::as_str)) else { continue };
            let Some((disk, num)) = self.layout.disks.iter().find_map(|d| d.parts.iter().find(|p| p.name == part).map(|p| (d.name.clone(), p.number))) else {
                self.notes.push(format!("UEFI entry {desc:?} isn't on the layout's disks; not recreated"));
                continue;
            };
            let Some(t) = self.names.mapping.target_of(&disk) else { continue };
            out.push(Cmd::new(&["efibootmgr", "--quiet", "--create", "--disk", &format!("/dev/{t}"), "--part", &num.to_string(), "--label", &desc, "--loader", &file]));
        }
        out
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
        out.push(
            Cmd::new(&[
                "sh",
                "-c",
                &format!(
                    "me=$(readlink /proc/self/ns/mnt); seen=; for f in /proc/[0-9]*/mountinfo; do grep -q ' {t}[ /]' \"$f\" 2>/dev/null || continue; \
                     p=${{f#/proc/}}; p=${{p%/mountinfo}}; ns=$(readlink /proc/$p/ns/mnt) || continue; \
                     case \"$me $seen \" in *\"$ns \"*) continue;; esac; seen=\"$seen $ns\"; \
                     echo \"unmounting {t} in $ns (pid $p, $(cat /proc/$p/comm))\"; nsenter -t \"$p\" -m umount --recursive {t} || true; done"
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

    #[test]
    fn mount_restore_boot_close() {
        let b = built(&["--repo", "/srv/repo", "--password-file", "/run/pw"]);
        let m = text(b.stage(Stage::Mount));
        let root = m.find("mount -t btrfs -o subvol=@rootfs /dev/vdb3 /mnt/bko-target\n").expect(&m);
        let boot = m.find("mount -t ext4 -o defaults /dev/vdb1 /mnt/bko-target/boot\n").expect(&m);
        assert!(root < boot);
        assert!(!m.contains("swap"));
        let r = text(b.stage(Stage::Restore));
        assert!(r.contains("rustic-bko --repository /srv/repo --password-file /run/pw --no-progress restore --numeric-id latest /mnt/bko-target\n"), "{r}");
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
        let b = crate::json::parse(r#"{"type": "bcachefs", "uuid": "c9cbba71-4054-4849-a19c-e7d40fb5b1e4", "label": null, "block_size": 4096, "btree_node_size": 262144, "metadata_checksum": "crc32c", "data_checksum": "crc32c", "compression": "none", "str_hash": "siphash", "metadata_replicas": 1, "data_replicas": 1}"#).unwrap();
        assert_eq!(
            mkfs_bcachefs("/dev/vdb3", &b).join(" "),
            "bcachefs format --force --uuid=c9cbba71-4054-4849-a19c-e7d40fb5b1e4 --block_size=4096 --btree_node_size=262144 --metadata_checksum=crc32c --data_checksum=crc32c --str_hash=siphash /dev/vdb3"
        );
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
}
