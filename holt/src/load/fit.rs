//! `--fit-largest PCT`: the dump rewritten for a target disk a little
//! smaller or bigger than the source's (layload.md, "Fitting a disk of
//! another size").  Per disk, one partition, the largest, loses or gains the
//! difference; every other partition keeps its size, and every identifier
//! stays the source's.
//!
//! Nothing here shrinks a live filesystem: `load` remakes every filesystem
//! with mkfs and the archiver refills it, so the fit only chooses other
//! numbers before anything is erased.  It's one pure function of the dump,
//! the targets' sizes and PCT, applied once in `target::prepare`, so
//! partition, assemble, format, mount, boot, close and status all work from
//! the same table.

use crate::json::Value;
use crate::report::human;

/// Every start and size the fit chooses is a whole one of these.
const MIB: u64 = 1 << 20;
/// What a shrinking filesystem must have free beyond the shrink itself.
const SPARE_BYTES: u64 = 1 << 30;
const SPARE_PCT: u64 = 5;
/// xfsprogs' XFS_AG_MIN_BYTES: mkfs.xfs refuses a size whose last allocation
/// group is a stub smaller than this, so the fit leaves that stub out.
const XFS_AG_MIN_BYTES: u64 = 16 << 20;
/// The last sector an MBR partition entry can reach: both its start and its
/// sector count are 32-bit.
const MBR_LAST: u64 = (1 << 32) - 1;

/// Filesystems whose mkfs is given the size the dump records.
const FS_SIZED: [&str; 4] = ["ext2", "ext3", "ext4", "xfs"];
/// Filesystems whose mkfs fills whatever device it's given.
const FS_FILLS: [&str; 4] = ["btrfs", "f2fs", "jfs", "nilfs2"];

/// A mapped target disk: the source disk's name in the dump, the target's
/// path (for the messages) and the target's size.
#[derive(Debug, Clone, PartialEq)]
pub struct Target {
    pub disk: String,
    pub path: String,
    pub size_bytes: u64,
}

/// What `--fit-largest` makes of a dump and its targets.
#[derive(Debug, Clone, Default, PartialEq)]
pub struct Fit {
    /// The dump the stages work from, where the fit changed anything.
    pub v: Option<Value>,
    pub problems: Vec<String>,
    pub notes: Vec<String>,
    /// Source disks whose table now ends where the target does, so the GPT
    /// backup header and last usable LBA go there (commands' `same_size`).
    pub new_end: Vec<String>,
    pub disks: Vec<DiskFit>,
}

/// One disk's fit.
#[derive(Debug, Clone, PartialEq)]
pub struct DiskFit {
    pub disk: String,
    pub target: String,
    pub sector: u64,
    pub old_size: u64,
    pub new_size: u64,
    /// The last sector a partition may end on, there and here.
    pub old_last: u64,
    pub new_last: u64,
    /// The partition that changes size, where one does.
    pub part: Option<PartFit>,
    /// The partitions that move, with the sectors each moves (down: below 0).
    pub moved: Vec<(u32, i64)>,
    /// Every filesystem that shrinks, with the figures the fit checked.
    pub fits: Vec<DataFit>,
    pub notes: Vec<String>,
    /// What the fit changes in the dump: a path and its new value.
    pub edits: Vec<(Vec<String>, Value)>,
}

#[derive(Debug, Clone, PartialEq)]
pub struct PartFit {
    pub number: u32,
    pub name: String,
    /// What it holds, as the report names it ("ext4", "LVM on LUKS").
    pub what: String,
    pub old_sectors: u64,
    pub new_sectors: u64,
}

/// A shrinking filesystem's room: what the dump says it holds, and what the
/// shrink needs.
#[derive(Debug, Clone, PartialEq)]
pub struct DataFit {
    /// The device as the source named it (vda3, dm-1).
    pub dev: String,
    pub kind: String,
    pub shrink: u64,
    pub used: u64,
    pub free: u64,
    pub need: u64,
}

impl Fit {
    pub fn changed(&self) -> bool {
        self.v.is_some()
    }

    /// plan's report: what each disk's geometry was and is, what shrank or
    /// grew, what moved, and the data-fit figures.
    pub fn report(&self, pct: u64) -> Vec<String> {
        let mut out = Vec::new();
        for d in &self.disks {
            out.push(format!(
                "{} -> {}: fitted with --fit-largest {pct}%: {} -> {}, last usable sector {} -> {}",
                d.disk,
                d.target,
                human(d.old_size),
                human(d.new_size),
                d.old_last,
                d.new_last
            ));
            if let Some(p) = &d.part {
                let (word, by) = if p.new_sectors < p.old_sectors {
                    ("shrinks", (p.old_sectors - p.new_sectors) * d.sector)
                } else {
                    ("grows", (p.new_sectors - p.old_sectors) * d.sector)
                };
                out.push(format!(
                    "  {} ({}) {word} by {}: {} -> {} sectors",
                    p.name, p.what, human(by), p.old_sectors, p.new_sectors
                ));
            }
            for (n, by) in &d.moved {
                out.push(format!("  partition {n} moves {} by {}", if *by < 0 { "down" } else { "up" }, human(by.unsigned_abs() * d.sector)));
            }
            for f in &d.fits {
                out.push(format!(
                    "  {} ({}): {} used, {} free; the shrink of {} needs {} free",
                    f.dev,
                    f.kind,
                    human(f.used),
                    human(f.free),
                    human(f.shrink),
                    human(f.need)
                ));
            }
            for n in &d.notes {
                out.push(format!("  {n}"));
            }
        }
        out
    }
}

fn s(v: &Value, k: &str) -> Option<String> {
    v.get(k).and_then(Value::as_str).map(str::to_string)
}

/// A number given as a number or as a decimal string (LUKS2 keeps its
/// offsets and sizes as strings).
fn u(v: &Value, k: &str) -> Option<u64> {
    match v.get(k)? {
        Value::Str(t) => t.parse().ok(),
        x => x.as_u64(),
    }
}

fn path(keys: &[&str]) -> Vec<String> {
    keys.iter().map(|k| k.to_string()).collect()
}

fn under(p: &[String], keys: &[&str]) -> Vec<String> {
    let mut out = p.to_vec();
    out.extend(keys.iter().map(|k| k.to_string()));
    out
}

/// Puts X at PATH in V (object keys and array indexes, as `Value::at` reads
/// them).  A path the dump hasn't got is an error: the fit only ever writes
/// keys it has read.
fn set_path(v: &mut Value, p: &[String], x: Value) -> Result<(), String> {
    let Some((key, rest)) = p.split_first() else {
        *v = x;
        return Ok(());
    };
    match v {
        Value::Arr(a) => {
            let i: usize = key.parse().map_err(|_| format!("{key} isn't an array index"))?;
            let e = a.get_mut(i).ok_or_else(|| format!("no element {key}"))?;
            set_path(e, rest, x)
        }
        Value::Obj(kv) => {
            let i = kv.iter().position(|(k, _)| k == key).ok_or_else(|| format!("no key {key}"))?;
            set_path(&mut kv[i].1, rest, x)
        }
        _ => Err(format!("{key}: not an object or an array")),
    }
}

/// The dump rewritten for TARGETS, each partition's largest contracted or
/// expanded by at most PCT percent.
pub fn fit(v: &Value, targets: &[Target], pct: u64) -> Fit {
    let mut f = Fit::default();
    let mut out = v.clone();
    for t in targets {
        match one(v, t, pct) {
            Err(problems) => f.problems.extend(problems),
            Ok(Outcome::Same) => {}
            Ok(Outcome::Unused(note)) => {
                f.new_end.push(t.disk.clone());
                f.notes.push(note);
            }
            Ok(Outcome::Fitted(d)) => {
                for (p, x) in &d.edits {
                    if let Err(e) = set_path(&mut out, p, x.clone()) {
                        f.problems.push(format!("{} -> {}: --fit-largest can't rewrite {}: {e}", d.disk, d.target, p.join(".")));
                    }
                }
                f.new_end.push(t.disk.clone());
                f.disks.push(d);
            }
        }
    }
    if !f.disks.is_empty() && f.problems.is_empty() {
        f.v = Some(out);
    }
    f
}

/// Whether the table HAVE, found on a target of T's size, is one a fit of
/// this dump wrote: the geometry `fit` comes to for some PCT and no other.
/// A disk someone resized by hand, or an older restore of the same machine,
/// is nothing of ours, so it's left to the usual checks.  Gives the
/// partition that changed size and the sectors it changed by.
pub fn wrote(v: &Value, t: &Target, have: &[(u32, u64, u64)]) -> Option<(u32, i64)> {
    for pct in 1..=super::FIT_MAX {
        let f = fit(v, std::slice::from_ref(t), pct);
        let (Some(out), Some(d)) = (&f.v, f.disks.first()) else { continue };
        let Some(p) = &d.part else { continue };
        let Some(g) = geometry(out, &t.disk) else { continue };
        if g.len() == have.len() && g.iter().all(|x| have.contains(x)) {
            return Some((p.number, p.new_sectors as i64 - p.old_sectors as i64));
        }
    }
    None
}

/// Every partition of DISK in V, as (number, start, sectors).
fn geometry(v: &Value, disk: &str) -> Option<Vec<(u32, u64, u64)>> {
    let d = v.at(&["storage", "disks"]).and_then(Value::as_array)?.iter().find(|x| s(x, "name").as_deref() == Some(disk))?;
    let ss = u(d, "logical_sector_size").unwrap_or(512).max(512);
    let mut out = Vec::new();
    for p in d.get("partitions").and_then(Value::as_array)? {
        out.push((
            u(p, "number")? as u32,
            u(p, "start_lba").or_else(|| u(p, "start_bytes").map(|b| b / ss))?,
            u(p, "sectors").or_else(|| u(p, "size_bytes").map(|b| b / ss))?,
        ));
    }
    Some(out)
}

enum Outcome {
    /// The target is the same size: nothing to do, and nothing to say.
    Same,
    /// Nothing changes but where the table ends.
    Unused(String),
    Fitted(DiskFit),
}

/// A partition of the source disk, as the fit needs it.
struct P {
    i: usize,
    number: u32,
    name: String,
    start: u64,
    sectors: u64,
    extended: bool,
    logical: bool,
}

impl P {
    fn end(&self) -> u64 {
        self.start + self.sectors - 1
    }
}

/// One disk's fit, worked out on the dump as it is.
struct One<'a> {
    v: &'a Value,
    t: &'a Target,
    disk: &'a Value,
    /// The path of the disk in the dump.
    at: Vec<String>,
    sector: u64,
    /// The disk's size, and the last sector a partition may end on, as the
    /// source had them and as the target has them.
    old_size: u64,
    new_size: u64,
    old_last: u64,
    new_last: u64,
    edits: Vec<(Vec<String>, Value)>,
    notes: Vec<String>,
    fits: Vec<DataFit>,
}

fn one(v: &Value, t: &Target, pct: u64) -> Result<Outcome, Vec<String>> {
    let who = format!("{} -> {}", t.disk, t.path);
    let no = |m: String| -> Result<Outcome, Vec<String>> { Err(vec![format!("{who}: {m}")]) };
    let disks = v.at(&["storage", "disks"]).and_then(Value::as_array);
    let Some((di, disk)) = disks.and_then(|a| a.iter().position(|x| s(x, "name").as_deref() == Some(t.disk.as_str())).map(|i| (i, &a[i]))) else {
        return no(format!("--fit-largest: the dump has no disk {}", t.disk));
    };
    let sector = u(disk, "logical_sector_size").unwrap_or(512).max(512);
    let old_size = u(disk, "size_bytes").unwrap_or(0);
    let new_size = t.size_bytes - t.size_bytes % sector;
    if old_size == 0 {
        return no("--fit-largest: the dump doesn't give the source disk's size".into());
    }
    if new_size == old_size {
        return Ok(Outcome::Same);
    }
    let bigger = new_size > old_size;
    let extra = || human(new_size.saturating_sub(old_size));
    // Another system's layout: its table goes on with write-table, not with
    // the sfdisk script the fit rewrites.
    if let Some(fam) = crate::other::Family::of_layout(v) {
        if !bigger {
            return no(format!("--fit-largest changes the partition tables sfdisk writes; this dump is {}'s, whose table load writes itself, so a smaller target is still refused", fam.name()));
        }
        return Ok(Outcome::Unused(format!("{who}: the target is bigger; --fit-largest doesn't grow a {} layout, so the extra {} stays unused", fam.name(), extra())));
    }
    let Some(table) = disk.get("partition_table").filter(|x| !x.is_null()) else {
        if !bigger {
            return no("the source disk has no partition table, so --fit-largest has no partition to fit; version 1 needs the same size or bigger".into());
        }
        return Ok(Outcome::Unused(format!("{who}: the target is bigger; {} holds no partition table, so the extra {} stays unused", t.disk, extra())));
    };
    let gpt = s(table, "type").as_deref() == Some("gpt");
    let total = new_size / sector;
    // Where the last partition may end: a GPT keeps the source's table
    // length, so its backup header and entries take the same room at the
    // new end; an MBR's last sector is the disk's.
    let entry_sectors = (u(table, "entry_count").unwrap_or(128) * u(table, "entry_size").unwrap_or(128)).div_ceil(sector);
    let tail = if gpt { 2 + entry_sectors } else { 1 };
    if total <= tail {
        return no(format!("--fit-largest: a target of {} is too small for a partition table", human(new_size)));
    }
    let new_last = total - tail;
    let old_last = if gpt { u(table, "last_usable_lba").unwrap_or(old_size / sector - tail) } else { old_size / sector - 1 };
    let mut parts = Vec::new();
    for (i, p) in disk.get("partitions").and_then(Value::as_array).into_iter().flatten().enumerate() {
        let (Some(number), Some(start), Some(sectors)) = (
            u(p, "number"),
            u(p, "start_lba").or_else(|| u(p, "start_bytes").map(|b| b / sector)),
            u(p, "sectors").or_else(|| u(p, "size_bytes").map(|b| b / sector)),
        ) else {
            return no("--fit-largest: a partition without a number, a start or a size".into());
        };
        parts.push(P {
            i,
            number: number as u32,
            name: s(p, "name").unwrap_or_default(),
            start,
            sectors,
            extended: p.get("extended").and_then(Value::as_bool) == Some(true),
            logical: p.get("logical").and_then(Value::as_bool) == Some(true),
        });
    }
    parts.sort_by_key(|p| p.start);
    if parts.is_empty() {
        if !bigger {
            return no("the source disk's table holds no partition, so --fit-largest has none to fit; version 1 needs the same size or bigger".into());
        }
        return Ok(Outcome::Unused(format!("{who}: the target is bigger; {} has no partition to grow, so the extra {} stays unused", t.disk, extra())));
    }
    let mut o = One {
        v,
        t,
        disk,
        at: path(&["storage", "disks", &di.to_string()]),
        sector,
        old_size,
        new_size,
        old_last,
        new_last,
        edits: vec![],
        notes: vec![],
        fits: vec![],
    };
    let mib = (MIB / sector).max(1);
    let last_end = parts.iter().map(P::end).max().unwrap_or(0);

    // A smaller target that still holds every partition: only the source's
    // own slack was lost, so no partition changes.  What's gone is the
    // difference between the disks: a source whose table ends short of its
    // own end (a disk grown without moving the table) has a last usable
    // sector that says nothing about it.
    if !bigger && last_end <= new_last {
        o.disk_edits(table, gpt, total);
        o.notes.push(format!(
            "no partition changes: the last one ends at sector {last_end}, and the target holds partitions through sector {new_last}; only {} of the source's unused space is gone",
            human(old_size - new_size)
        ));
        return Ok(o.done(None, vec![]));
    }

    // The largest partition, which an MBR's extended container isn't.
    let mut cands: Vec<&P> = parts.iter().filter(|p| !p.extended).collect();
    cands.sort_by(|a, b| b.sectors.cmp(&a.sectors).then(a.number.cmp(&b.number)));
    let Some(big) = cands.first().copied() else {
        return no("--fit-largest: the disk holds nothing but an extended partition".into());
    };
    let tied: Vec<String> = cands.iter().filter(|p| p.sectors == big.sectors).map(|p| format!("{} ({})", p.name, human(p.sectors * sector))).collect();
    if tied.len() > 1 {
        return no(format!("--fit-largest: {} are the same size, so there's no one largest partition", tied.join(" and ")));
    }
    if big.logical {
        return no(format!("--fit-largest: the largest partition {} is an MBR logical partition, whose start the extended partition's chain records", big.name));
    }
    // Only what starts after the largest moves, so the first partition
    // never does: the gap after the MBR, where GRUB's core image sits, is
    // where it was, and so is anything else before the largest.
    let moved: Vec<&P> = parts.iter().filter(|p| p.start > big.start).collect();
    for m in &moved {
        if m.logical || m.extended {
            return no(format!(
                "--fit-largest: {} would have to move, and it's an MBR {} partition, whose start the extended partition's chain records",
                m.name,
                if m.logical { "logical" } else { "extended" }
            ));
        }
        let pv = &disk.at(&["partitions", &m.i.to_string()]).cloned().unwrap_or(Value::Null);
        if pv.get("esp_image").is_some() || pv.at(&["boot_code", "image_b64"]).is_some_and(|b| !b.is_null()) {
            return no(format!("--fit-largest: {} would have to move, and the dump holds its bytes to put back where they were", m.name));
        }
        // A FAT's BPB records the sector its partition starts at, which
        // mkfs.fat is given as -h: a moved one is told the new start below,
        // and one recording anything else isn't a start the fit can mend.
        let mc = pv.get("content").cloned().unwrap_or(Value::Null);
        if let Some(h) = u(&mc, "hidden_sectors").filter(|&h| h != 0 && h != m.start) {
            return no(format!(
                "--fit-largest: {} would have to move, and the filesystem on it records sector {h} as its first, not its own start {}",
                m.name, m.start
            ));
        }
    }

    // How much the largest gives up or takes on, in whole MiB.
    let room = new_last.saturating_sub(last_end) / mib * mib;
    let cap = big.sectors * pct / 100 / mib * mib;
    // An MBR entry holds a 32-bit start and a 32-bit sector count, so
    // nothing may grow or move past the last sector one can reach; sfdisk
    // refuses such a table outright.  A GPT's are 64-bit.
    let entry_room = if gpt {
        u64::MAX
    } else {
        let furthest = moved.iter().map(|m| m.end()).max().unwrap_or(0).max(big.end());
        MBR_LAST.saturating_sub(furthest) / mib * mib
    };
    let (shrink, grow) = if bigger { (0, room.min(cap).min(entry_room)) } else { ((last_end - new_last).div_ceil(mib) * mib, 0) };
    // Growing, whatever is left past the last partition stays unused, as it
    // does without the flag (check_target's own note).  Under a MiB there
    // was never anything the fit could have done with it.
    let unused = match (bigger, new_last.saturating_sub(last_end + grow) / mib * mib * sector) {
        (true, n) if n > 0 => Some(format!("the target is bigger; the extra {} stays unused", human(n))),
        _ => None,
    };
    if bigger && grow == 0 {
        o.disk_edits(table, gpt, total);
        let why = if entry_room < mib {
            format!("an MBR partition entry reaches no further than sector {MBR_LAST}")
        } else if cap < mib {
            format!("{pct}% of {} is less than 1 MiB", big.name)
        } else {
            "there's less than 1 MiB to grow into".to_string()
        };
        o.notes.push(format!("{} doesn't grow: {why}", big.name));
        o.notes.extend(unused);
        return Ok(o.done(None, vec![]));
    }
    o.notes.extend(unused);
    if shrink > 0 && shrink * 100 > pct * big.sectors {
        return no(format!(
            "--fit-largest {pct}%: {} would have to shrink by {} of its {}, more than {pct}%",
            big.name,
            human(shrink * sector),
            human(big.sectors * sector)
        ));
    }
    if shrink >= big.sectors {
        return no(format!("--fit-largest: {} is only {}, and the target is {} too small", big.name, human(big.sectors * sector), human(shrink * sector)));
    }
    let delta = shrink as i64 - grow as i64;
    let new_sectors = (big.sectors as i64 - delta) as u64;

    // What the largest holds, and the filesystems that go with its size.
    let what = o.resize_inside(big, big.sectors * sector, new_sectors * sector, pct).map_err(|e| vec![format!("{who}: {e}")])?;

    o.disk_edits(table, gpt, total);
    let bp = under(&o.at, &["partitions", &big.i.to_string()]);
    let bv = o.part_json(big);
    o.put(&bv, &bp, "sectors", new_sectors);
    o.put(&bv, &bp, "size_bytes", new_sectors * sector);
    o.put(&bv, &bp, "end_lba", big.start + new_sectors - 1);
    o.put(&bv, &bp, "size", human(new_sectors * sector));
    let mut shifted = Vec::new();
    for m in &moved {
        let start = (m.start as i64 - delta) as u64;
        let mp = under(&o.at, &["partitions", &m.i.to_string()]);
        let mv = o.part_json(m);
        o.put(&mv, &mp, "start_lba", start);
        o.put(&mv, &mp, "start_bytes", start * sector);
        o.put(&mv, &mp, "end_lba", start + m.sectors - 1);
        // A FAT that moves is told where its partition now starts (mkfs.fat
        // -h writes that into the BPB); one recording anything else was
        // refused above.
        let mc = mv.get("content").cloned().unwrap_or(Value::Null);
        if u(&mc, "hidden_sectors") == Some(m.start) {
            o.put(&mc, &under(&mp, &["content"]), "hidden_sectors", start);
        }
        shifted.push((m.number, -delta));
    }
    let part = PartFit { number: big.number, name: big.name.clone(), what, old_sectors: big.sectors, new_sectors };
    Ok(o.done(Some(part), shifted))
}

impl One<'_> {
    /// The disk's partition entry as the dump holds it.
    fn part_json(&self, p: &P) -> Value {
        self.disk.at(&["partitions", &p.i.to_string()]).cloned().unwrap_or(Value::Null)
    }

    /// Sets KEY of the object AT (whose path in the dump is PATH), where the
    /// dump has that key.
    fn put(&mut self, at: &Value, p: &[String], key: &str, x: impl Into<Value>) {
        if at.get(key).is_some() {
            self.edits.push((under(p, &[key]), x.into()));
        }
    }

    /// The disk's own size, and where its table ends.
    fn disk_edits(&mut self, table: &Value, gpt: bool, total: u64) {
        let (at, d, new_size) = (self.at.clone(), self.disk.clone(), self.new_size);
        self.put(&d, &at, "size_bytes", new_size);
        self.put(&d, &at, "size", human(new_size));
        if !gpt {
            return;
        }
        let tp = under(&at, &["partition_table"]);
        let (tv, new_last) = (table.clone(), self.new_last);
        self.put(&tv, &tp, "last_usable_lba", new_last);
        self.put(&tv, &tp, "backup_lba", total - 1);
        // The protective MBR's own entry reaches the new end too.
        for (i, e) in table.at(&["protective_mbr", "entries"]).and_then(Value::as_array).cloned().unwrap_or_default().iter().enumerate() {
            let start = u(e, "start_lba").unwrap_or(1);
            if s(e, "type").as_deref() != Some("0xee") || start >= total {
                continue;
            }
            let ep = under(&tp, &["protective_mbr", "entries", &i.to_string()]);
            let len = (total - start).min(u64::from(u32::MAX));
            self.put(e, &ep, "sectors", len);
            self.put(e, &ep, "size_bytes", len * self.sector);
        }
    }

    fn done(self, part: Option<PartFit>, moved: Vec<(u32, i64)>) -> Outcome {
        Outcome::Fitted(DiskFit {
            disk: self.t.disk.clone(),
            target: self.t.path.clone(),
            sector: self.sector,
            old_size: self.old_size,
            new_size: self.new_size,
            old_last: self.old_last,
            new_last: self.new_last,
            part,
            moved,
            fits: self.fits,
            notes: self.notes,
            edits: self.edits,
        })
    }

    /// What the largest partition holds, resized with it: a filesystem, a
    /// LUKS container with one inside, or LVM (item 8 of the design).
    /// Gives what it holds, as the report names it.
    fn resize_inside(&mut self, p: &P, old: u64, new: u64, pct: u64) -> Result<String, String> {
        let at = under(&self.at, &["partitions", &p.i.to_string(), "content"]);
        let c = self.part_json(p).get("content").cloned().unwrap_or(Value::Null);
        // LUKS: the header stays where it is, so the payload keeps its
        // offset and the plaintext device gains or loses the difference.
        if s(&c, "type").as_deref() != Some("crypto_LUKS") {
            return self.resize_content(&p.name, &c, &at, old, new, pct);
        }
        let off = u(&c, "data_offset_bytes")
            .or_else(|| c.at(&["segments", "0"]).and_then(|x| u(x, "offset")))
            .ok_or_else(|| format!("--fit-largest: the dump doesn't give the LUKS payload offset on {}", p.name))?;
        let (i, dm) = self.crypt_dm(&p.name).ok_or_else(|| format!("--fit-largest: {} holds LUKS, and the dump doesn't say what was opened on it", p.name))?;
        if old <= off || new <= off {
            return Err(format!("--fit-largest: the LUKS payload on {} starts at {}, past the new size", p.name, human(off)));
        }
        let dp = path(&["storage", "dm_devices", &i.to_string()]);
        self.put(&dm, &dp, "size_bytes", new - off);
        self.put(&dm, &dp, "size", human(new - off));
        let dev = s(&dm, "name").unwrap_or_default();
        let c = dm.get("content").cloned().unwrap_or(Value::Null);
        let what = self.resize_content(&dev, &c, &under(&dp, &["content"]), old - off, new - off, pct)?;
        Ok(format!("{what} on LUKS"))
    }

    /// The dm device a LUKS container on DEV was opened as: its index in the
    /// dump's dm_devices and its entry.
    fn crypt_dm(&self, dev: &str) -> Option<(usize, Value)> {
        let all = self.v.at(&["storage", "dm_devices"]).and_then(Value::as_array)?;
        all.iter()
            .position(|d| {
                d.at(&["dm", "target"]).and_then(Value::as_str) == Some("crypt")
                    && d.get("slaves").and_then(Value::as_array).is_some_and(|sl| sl.iter().any(|x| x.as_str() == Some(dev)))
            })
            .map(|i| (i, all[i].clone()))
    }

    /// A filesystem or a volume group on DEV, resized from OLD to NEW bytes.
    fn resize_content(&mut self, dev: &str, c: &Value, at: &[String], old: u64, new: u64, pct: u64) -> Result<String, String> {
        let kind = s(c, "type").unwrap_or_default();
        match kind.as_str() {
            "LVM2_member" => self.resize_vg(dev, old, new, pct),
            k if FS_SIZED.contains(&k) || FS_FILLS.contains(&k) => {
                if k == "btrfs" && self.btrfs_devices(c) > 1 {
                    return Err(format!("--fit-largest: {dev} is one of {} devices of a btrfs filesystem", self.btrfs_devices(c)));
                }
                self.resize_fs(dev, c, at, old, new)?;
                Ok(kind)
            }
            "" => Err(format!("--fit-largest: the dump says nothing is on {dev}")),
            "swap" => Err(format!("--fit-largest: {dev} holds swap, whose size load takes from the partition; it's never the one to resize")),
            "linux_raid_member" => Err(format!("--fit-largest: {dev} is an md array's member, and version 1 doesn't resize an array")),
            "zfs_member" => Err(format!("--fit-largest: {dev} is a ZFS pool's member, and version 1 doesn't resize a pool")),
            k => Err(format!("--fit-largest: {dev} holds {k}, which isn't one it resizes (ext2/3/4, XFS, btrfs on one device, f2fs, JFS, NILFS2, LUKS with one of those, or LVM)")),
        }
    }

    /// How many devices the btrfs filesystem on this content has.
    fn btrfs_devices(&self, c: &Value) -> usize {
        let uuid = s(c, "uuid").unwrap_or_default();
        self.v
            .at(&["storage", "btrfs"])
            .and_then(Value::as_array)
            .into_iter()
            .flatten()
            .find(|f| s(f, "uuid").as_deref() == Some(uuid.as_str()))
            .and_then(|f| f.get("devices").and_then(Value::as_array).map(Vec::len))
            .unwrap_or(1)
    }

    /// A filesystem resized with the device under it: the source's own slack
    /// is kept, and one that filled its device fills it again.
    fn resize_fs(&mut self, dev: &str, c: &Value, at: &[String], old: u64, new: u64) -> Result<(), String> {
        let kind = s(c, "type").unwrap_or_default();
        let fills = FS_FILLS.contains(&kind.as_str());
        let size = u(c, "size_bytes").or_else(|| Some(u(c, "blocks")? * u(c, "block_size")?)).or_else(|| u(c, "total_bytes")).unwrap_or(0);
        if size == 0 {
            return Err(format!("--fit-largest: the dump doesn't give the size of the {kind} on {dev}"));
        }
        // Whatever room the source left between the filesystem and the
        // device stays: the filesystem loses or gains what the device did.
        // Growing, one that didn't fill its device keeps its size, unless
        // its mkfs takes no size and fills whatever it's given.
        let mut want = if new < old || fills || size == old {
            size.saturating_sub(old.saturating_sub(new)) + new.saturating_sub(old)
        } else {
            self.notes.push(format!("{dev}: the {kind} was {} in a {} device, so it keeps that size and the spare room grows", human(size), human(old)));
            size
        };
        if want == 0 || want > new {
            return Err(format!("--fit-largest: the {kind} on {dev} doesn't fit a device of {}", human(new)));
        }
        match kind.as_str() {
            "ext2" | "ext3" | "ext4" | "f2fs" => {
                let bs = u(c, "block_size").ok_or_else(|| format!("--fit-largest: the dump doesn't give the block size of the {kind} on {dev}"))?;
                let blocks = want / bs;
                want = blocks * bs;
                self.put(c, at, "blocks", blocks);
            }
            "xfs" => {
                let bs = u(c, "block_size").ok_or_else(|| format!("--fit-largest: the dump doesn't give the block size of the XFS on {dev}"))?;
                let mut blocks = want / bs;
                if let Some(ag) = u(c, "ag_blocks").filter(|&a| a > 0) {
                    // mkfs.xfs refuses an allocation group size bigger than
                    // the whole data area, and leaves out a last group that
                    // would be a stub.
                    if ag > blocks {
                        return Err(format!(
                            "--fit-largest: the XFS on {dev} has allocation groups of {}, and {} is all that would be left for one",
                            human(ag * bs),
                            human(blocks * bs)
                        ));
                    }
                    // The least a last group may be and still be made: the
                    // minimum, and the log too where that group is the one
                    // mkfs.xfs puts it in (group agcount/2, which is the
                    // last of two).
                    let least = (XFS_AG_MIN_BYTES / bs).max(1);
                    let least = if blocks.div_ceil(ag) == 2 { least + u(c, "log_blocks").unwrap_or(0) } else { least };
                    let rem = blocks % ag;
                    if blocks > ag && rem != 0 && rem < least {
                        blocks -= rem;
                    }
                    self.put(c, at, "ag_count", blocks.div_ceil(ag));
                }
                want = blocks * bs;
                self.put(c, at, "blocks", blocks);
            }
            _ => {}
        }
        self.put(c, at, "size_bytes", want);
        self.put(c, at, "total_bytes", want);
        if want < size {
            let shrink = size - want;
            self.data_fits(dev, &kind, c, size, shrink)?;
            if let (Some(bs), Some(free)) = (u(c, "block_size"), u(c, "free_blocks")) {
                self.put(c, at, "free_blocks", free.saturating_sub(shrink.div_ceil(bs)));
            }
        }
        Ok(())
    }

    /// Whether what the dump says is on a filesystem still fits once it has
    /// shrunk, with room to spare.
    fn data_fits(&mut self, dev: &str, kind: &str, c: &Value, size: u64, shrink: u64) -> Result<(), String> {
        let Some((used, free)) = self.usage(dev, c) else {
            return Err(format!(
                "--fit-largest: {dev} ({kind}) would shrink by {}, and the dump doesn't say how much of it is used, so there's no telling the data would still fit",
                human(shrink)
            ));
        };
        let need = shrink + SPARE_BYTES.max(used * SPARE_PCT / 100);
        self.fits.push(DataFit { dev: dev.to_string(), kind: kind.to_string(), shrink, used, free, need });
        if free < need {
            return Err(format!(
                "--fit-largest: {dev} ({kind}) holds {} of {} and would shrink by {}; that leaves less than the {} free it needs (the shrink and {} to spare)",
                human(used),
                human(size),
                human(shrink),
                human(need),
                human(need - shrink)
            ));
        }
        Ok(())
    }

    /// What a filesystem holds and what it has free: the mount the dump
    /// recorded, else the superblock's own free blocks.
    fn usage(&self, dev: &str, c: &Value) -> Option<(u64, u64)> {
        for m in self.v.at(&["storage", "mounts"]).and_then(Value::as_array).into_iter().flatten() {
            if s(m, "device").as_deref() == Some(dev) {
                if let (Some(size), Some(used)) = (u(m, "size_bytes"), u(m, "used_bytes")) {
                    return Some((used, size.saturating_sub(used)));
                }
            }
        }
        let (free, bs, blocks) = (u(c, "free_blocks")?, u(c, "block_size")?, u(c, "blocks")?);
        Some(((blocks.saturating_sub(free)) * bs, free * bs))
    }
}

// ---- LVM ----------------------------------------------------------------

/// A run of physical extents.
type Run = (u64, u64);

/// The extents of RUNS that are below X.
fn below(runs: &[Run], x: u64) -> u64 {
    runs.iter().map(|&(s, n)| n.min(x.saturating_sub(s))).sum()
}

/// What's left of [0, COUNT) once the used runs are taken out.
fn free_runs(count: u64, used: &mut Vec<Run>) -> Result<Vec<Run>, String> {
    used.sort();
    let mut out = Vec::new();
    let mut at = 0;
    for &(s, n) in used.iter() {
        if s < at {
            return Err("--fit-largest: the volume group's extents overlap in the dump".into());
        }
        if s > at {
            out.push((at, s - at));
        }
        at = s + n;
    }
    if at > count {
        return Err("--fit-largest: the volume group uses more extents than its physical volume has".into());
    }
    if at < count {
        out.push((at, count - at));
    }
    Ok(out)
}

/// N extents of FREE, taken from the highest ones down.
fn take_from_top(free: &[Run], n: u64) -> Vec<Run> {
    let mut left = n;
    let mut out = Vec::new();
    for &(s, len) in free.iter().rev() {
        if left == 0 {
            break;
        }
        let take = left.min(len);
        out.push((s + len - take, take));
        left -= take;
    }
    out.sort();
    out
}

/// A logical volume as the fit reads it.
struct Lv {
    i: usize,
    name: String,
    /// (segment index, start extent, extent count, first physical extent,
    /// stripe count)
    segs: Vec<(usize, u64, u64, u64, u64)>,
    extents: u64,
    linear: bool,
}

impl One<'_> {
    /// LVM on the largest partition, or inside its LUKS: the physical volume
    /// grows or shrinks with it, free extents go first, and the largest
    /// logical volume gives up the rest.
    fn resize_vg(&mut self, dev: &str, old: u64, new: u64, pct: u64) -> Result<String, String> {
        let vgs = self.v.at(&["storage", "lvm", "volume_groups"]).and_then(Value::as_array).cloned().unwrap_or_default();
        let Some(vi) = vgs.iter().position(|g| {
            g.get("physical_volumes").and_then(Value::as_array).is_some_and(|pvs| pvs.iter().any(|p| s(p, "device").as_deref() == Some(dev)))
        }) else {
            return Err(format!("--fit-largest: {dev} is an LVM physical volume, and the dump has no volume group on it"));
        };
        let vg = vgs[vi].clone();
        let name = s(&vg, "name").unwrap_or_default();
        let pvs = vg.get("physical_volumes").and_then(Value::as_array).cloned().unwrap_or_default();
        if pvs.len() != 1 {
            return Err(format!("--fit-largest: the volume group {name} has {} physical volumes, and version 1 fits one on its own", pvs.len()));
        }
        let pv = pvs[0].clone();
        let pv_name = s(&pv, "name").unwrap_or_default();
        let extent = u(&vg, "extent_size_bytes").filter(|&e| e > 0).ok_or_else(|| format!("--fit-largest: the volume group {name} has no extent size"))?;
        let pe_start = u(&pv, "pe_start_bytes").ok_or_else(|| format!("--fit-largest: the physical volume {pv_name} has no pe_start"))?;
        let old_count = u(&pv, "pe_count").ok_or_else(|| format!("--fit-largest: the physical volume {pv_name} has no extent count"))?;
        if new <= pe_start {
            return Err(format!("--fit-largest: the physical volume {pv_name} starts its extents at {}, past the new size", human(pe_start)));
        }
        let new_count = (new - pe_start) / extent;
        let vp = path(&["storage", "lvm", "volume_groups", &vi.to_string()]);
        let pp = under(&vp, &["physical_volumes", "0"]);
        self.put(&pv, &pp, "dev_size_bytes", new / 512 * 512);
        // A bigger physical volume holds more extents, which stay free in
        // the group.  A smaller one keeps the extents it has where the room
        // past its last one covers what the disk lost: the group goes on as
        // it is, since the fit never adds free extents to a shrinking disk.
        if new >= old {
            self.put(&pv, &pp, "pe_count", new_count);
            self.notes.push(if new_count > old_count {
                format!(
                    "the volume group {name} grows by {} ({} extents), which stay free in it: no logical volume changes",
                    human((new_count - old_count) * extent),
                    new_count - old_count
                )
            } else {
                format!("the volume group {name} doesn't change: {pv_name} gained less than the {} an extent takes", human(extent))
            });
            return Ok(format!("LVM ({name})"));
        }
        if new_count >= old_count {
            self.notes.push(format!(
                "the volume group {name} keeps its {old_count} extents: {pv_name} had room past them for the {} the disk lost",
                human(old - new)
            ));
            return Ok(format!("LVM ({name})"));
        }
        self.put(&pv, &pp, "pe_count", new_count);

        let lvs = self.read_lvs(&vg, &pv_name)?;
        let mut used: Vec<Run> = lvs.iter().flat_map(|l| l.segs.iter().map(|&(_, _, n, pe, st)| (pe, n / st.max(1)))).collect();
        let free = free_runs(old_count, &mut used)?;
        let freed: u64 = free.iter().map(|&(_, n)| n).sum();
        let want = old_count - new_count;

        // Free extents first; the largest logical volume gives up the rest.
        let mut shrunk: Option<(usize, u64)> = None;
        if want > freed {
            let need = want - freed;
            let big = self.largest_lv(&lvs, &name)?;
            if (need * extent) * 100 > pct * (lvs[big].extents * extent) {
                return Err(format!(
                    "--fit-largest {pct}%: {name}/{} would have to shrink by {} of its {}, more than {pct}%",
                    lvs[big].name,
                    human(need * extent),
                    human(lvs[big].extents * extent)
                ));
            }
            if need >= lvs[big].extents {
                return Err(format!("--fit-largest: {name}/{} is only {}, and {} has to go", lvs[big].name, human(lvs[big].extents * extent), human(need * extent)));
            }
            shrunk = Some((big, need));
        }
        // The extents to drop: every free one, or the highest free ones.
        let mut gone: Vec<Run> = free;
        if let Some((big, need)) = shrunk {
            let mut left = need;
            let mut segs = lvs[big].segs.clone();
            segs.sort_by_key(|&(_, start, _, _, _)| std::cmp::Reverse(start));
            for &(_, _, n, pe, _) in &segs {
                let take = left.min(n);
                if take > 0 {
                    gone.push((pe + n - take, take));
                    left -= take;
                }
            }
            gone.sort();
        } else {
            gone = take_from_top(&gone, want);
        }

        self.rewrite_lvs(&vp, &vg, &lvs, &gone, shrunk, extent)?;
        let dropped: u64 = gone.iter().map(|&(_, n)| n).sum();
        self.notes.push(format!(
            "the volume group {name} loses {} ({dropped} extents), {}",
            human(dropped * extent),
            match shrunk {
                None => "all of them free".to_string(),
                Some((i, n)) => format!("{n} of them from the end of {name}/{}", lvs[i].name),
            }
        ));
        Ok(format!("LVM ({name})"))
    }

    /// The volume group's logical volumes, with the extents each holds on
    /// this physical volume.
    fn read_lvs(&self, vg: &Value, pv: &str) -> Result<Vec<Lv>, String> {
        let mut out = Vec::new();
        for (i, lv) in vg.get("logical_volumes").and_then(Value::as_array).cloned().unwrap_or_default().iter().enumerate() {
            let name = s(lv, "name").unwrap_or_default();
            let mut segs = Vec::new();
            let mut linear = true;
            for (j, seg) in lv.get("segments").and_then(Value::as_array).cloned().unwrap_or_default().iter().enumerate() {
                let ty = s(seg, "type").unwrap_or_default();
                if ty != "striped" {
                    return Err(format!("--fit-largest: the logical volume {name} has a {ty} segment, and version 1 fits linear ones"));
                }
                let stripes = seg.get("stripes").and_then(Value::as_array).cloned().unwrap_or_default();
                let count = u(seg, "stripe_count").unwrap_or(1).max(1);
                if count != 1 {
                    linear = false;
                }
                let (Some(start), Some(n)) = (u(seg, "start_extent"), u(seg, "extent_count")) else {
                    return Err(format!("--fit-largest: a segment of the logical volume {name} has no extents"));
                };
                for k in 0..count as usize {
                    let on = stripes.get(2 * k).and_then(Value::as_str).unwrap_or_default();
                    let at = stripes.get(2 * k + 1).and_then(Value::as_u64);
                    let Some(at) = at else {
                        return Err(format!("--fit-largest: a segment of the logical volume {name} doesn't say where it is"));
                    };
                    if on != pv {
                        return Err(format!("--fit-largest: the logical volume {name} is on {on}, not on {pv}"));
                    }
                    segs.push((j, start, n, at, count));
                }
            }
            let extents = segs.iter().map(|&(_, _, n, _, st)| n / st.max(1)).sum();
            out.push(Lv { i, name, segs, extents, linear });
        }
        Ok(out)
    }

    /// The one logical volume to shrink: the largest, linear, with a
    /// filesystem the fit can resize.
    fn largest_lv(&self, lvs: &[Lv], vg: &str) -> Result<usize, String> {
        let mut by_size: Vec<&Lv> = lvs.iter().collect();
        by_size.sort_by(|a, b| b.extents.cmp(&a.extents).then(a.name.cmp(&b.name)));
        let Some(big) = by_size.first() else {
            return Err(format!("--fit-largest: the volume group {vg} has no logical volume to shrink"));
        };
        if by_size.iter().filter(|l| l.extents == big.extents).count() > 1 {
            let tied: Vec<&str> = by_size.iter().filter(|l| l.extents == big.extents).map(|l| l.name.as_str()).collect();
            return Err(format!("--fit-largest: {} are the same size, so there's no one largest logical volume in {vg}", tied.join(" and ")));
        }
        if !big.linear {
            return Err(format!("--fit-largest: {vg}/{} is striped, and version 1 shrinks a linear one", big.name));
        }
        let Some((_, c)) = self.lv_device(vg, &big.name) else {
            return Err(format!("--fit-largest: the dump doesn't say what's on {vg}/{}", big.name));
        };
        let kind = s(&c, "type").unwrap_or_default();
        if kind == "swap" {
            return Err(format!("--fit-largest: the largest logical volume {vg}/{} is swap, which the fit never shrinks", big.name));
        }
        if !FS_SIZED.contains(&kind.as_str()) && !FS_FILLS.contains(&kind.as_str()) {
            return Err(format!("--fit-largest: {vg}/{} holds {}, which isn't one the fit resizes", big.name, if kind.is_empty() { "nothing the dump knows" } else { &kind }));
        }
        Ok(big.i)
    }

    /// A logical volume's device-mapper entry in the dump: its index and
    /// what's on it.
    fn lv_device(&self, vg: &str, lv: &str) -> Option<(usize, Value)> {
        let all = self.v.at(&["storage", "dm_devices"]).and_then(Value::as_array)?;
        let i = all.iter().position(|d| d.at(&["dm", "vg"]).and_then(Value::as_str) == Some(vg) && d.at(&["dm", "lv"]).and_then(Value::as_str) == Some(lv))?;
        Some((i, all[i].get("content").cloned().unwrap_or(Value::Null)))
    }

    /// Every logical volume's segments once GONE's extents are dropped and
    /// the ones above them renumbered down.
    fn rewrite_lvs(&mut self, vp: &[String], vg: &Value, lvs: &[Lv], gone: &[Run], shrunk: Option<(usize, u64)>, extent: u64) -> Result<(), String> {
        let name = s(vg, "name").unwrap_or_default();
        let all = vg.get("logical_volumes").and_then(Value::as_array).cloned().unwrap_or_default();
        for lv in lvs {
            let lp = under(vp, &["logical_volumes", &lv.i.to_string()]);
            let mut segs = all[lv.i].get("segments").and_then(Value::as_array).cloned().unwrap_or_default();
            // This volume's own shrink comes off its last logical extents.
            let mut left = shrunk.filter(|&(i, _)| i == lv.i).map(|(_, n)| n).unwrap_or(0);
            if left > 0 {
                let mut order: Vec<usize> = (0..segs.len()).collect();
                order.sort_by_key(|&j| std::cmp::Reverse(u(&segs[j], "start_extent").unwrap_or(0)));
                for j in order {
                    let n = u(&segs[j], "extent_count").unwrap_or(0);
                    let take = left.min(n);
                    left -= take;
                    if let Value::Obj(kv) = &mut segs[j] {
                        if let Some(slot) = kv.iter_mut().find(|(k, _)| k == "extent_count") {
                            slot.1 = Value::UInt(n - take);
                        }
                    }
                }
                segs.retain(|x| u(x, "extent_count").unwrap_or(0) > 0);
            }
            // Compaction: an extent moves down by what was dropped below it.
            for seg in segs.iter_mut() {
                let stripes = seg.get("stripes").and_then(Value::as_array).cloned().unwrap_or_default();
                let mut new = stripes.clone();
                for k in (1..stripes.len()).step_by(2) {
                    let at = stripes[k].as_u64().unwrap_or(0);
                    new[k] = Value::UInt(at - below(gone, at));
                }
                if let Value::Obj(kv) = seg {
                    if let Some(slot) = kv.iter_mut().find(|(k, _)| k == "stripes") {
                        slot.1 = Value::Arr(new);
                    }
                }
            }
            // A segment's extent_count is the volume's own extents, however
            // many stripes share them out, so it's the total read_lvs
            // arrived at: a volume nothing took from comes to the same
            // number again and is left alone.
            let extents: u64 = segs.iter().map(|x| u(x, "extent_count").unwrap_or(0)).sum();
            let was = all[lv.i].get("segments").and_then(Value::as_array).cloned().unwrap_or_default();
            if segs != was {
                if !lv.linear {
                    return Err(format!("--fit-largest: {name}/{} is striped, and it would have to move", lv.name));
                }
                self.edits.push((under(&lp, &["segments"]), Value::Arr(segs)));
            }
            if extents == lv.extents {
                continue;
            }
            // The volume that shrank: its device and its filesystem too.
            self.put(&all[lv.i], &lp, "size_bytes", extents * extent);
            let Some((di, c)) = self.lv_device(&name, &lv.name) else { continue };
            let dp = path(&["storage", "dm_devices", &di.to_string()]);
            let d = self.v.at(&["storage", "dm_devices", &di.to_string()]).cloned().unwrap_or(Value::Null);
            self.put(&d, &dp, "size_bytes", extents * extent);
            self.put(&d, &dp, "size", human(extents * extent));
            let dev = s(&d, "name").unwrap_or_default();
            self.resize_fs(&dev, &c, &under(&dp, &["content"]), lv.extents * extent, extents * extent)?;
        }
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// A MiB and a GiB in 512-byte sectors.
    const M: u64 = MIB / 512;
    const G: u64 = GIB / 512;
    const GIB: u64 = 1 << 30;

    /// A dump of one disk, as the fixtures need it.
    struct D {
        table: &'static str,
        /// The disk's size in sectors.
        total: u64,
        parts: Vec<String>,
        dm: Vec<String>,
        vgs: Vec<String>,
        mounts: Vec<String>,
        family: Option<&'static str>,
        /// How far short of the disk's own end the table ends.
        short: u64,
    }

    impl D {
        fn new(table: &'static str, total: u64) -> D {
            D { table, total, parts: vec![], dm: vec![], vgs: vec![], mounts: vec![], family: None, short: 0 }
        }
        fn gpt(total: u64) -> D {
            D::new("gpt", total)
        }
        fn mbr(total: u64) -> D {
            D::new("dos", total)
        }
        /// A partition: number, start, sectors, its content's JSON, and any
        /// more fields of its own ("\"logical\": true").
        fn part(mut self, n: u32, start: u64, sectors: u64, content: &str, more: &str) -> D {
            let comma = if more.is_empty() { "" } else { ", " };
            self.parts.push(format!(
                r#"{{"name": "vda{n}", "number": {n}, "start_lba": {start}, "end_lba": {}, "sectors": {sectors},
                     "start_bytes": {}, "size_bytes": {}, "type": "0fc63daf-8483-4772-8e79-3d69d8477de4",
                     "uuid": "aa000000-0000-4000-8000-00000000000{n}", "partlabel": "p{n}", "attributes": "0x0000000000000000",
                     "matches_kernel": true{comma}{more}, "content": {content}}}"#,
                start + sectors - 1,
                start * 512,
                sectors * 512
            ));
            self
        }
        fn dm(mut self, json: String) -> D {
            self.dm.push(json);
            self
        }
        fn vg(mut self, json: String) -> D {
            self.vgs.push(json);
            self
        }
        /// What the dump says is mounted from DEV: its size and what's used.
        fn mount(mut self, dev: &str, size: u64, used: u64) -> D {
            self.mounts.push(format!(
                r#"{{"target": "/m{}", "source": "/dev/{dev}", "device": "{dev}", "fstype": "x", "size_bytes": {size}, "used_bytes": {used}, "available_bytes": {}}}"#,
                self.mounts.len(),
                size - used
            ));
            self
        }
        fn family(mut self, f: &'static str) -> D {
            self.family = Some(f);
            self
        }
        /// The table ends SECTORS short of the disk's own end, as it does on
        /// a disk grown without moving its GPT.
        fn short(mut self, sectors: u64) -> D {
            self.short = sectors;
            self
        }

        fn build(&self) -> Value {
            let t = match self.table {
                "gpt" => format!(
                    r#"{{"type": "gpt", "sector_size": 512, "disk_guid": "aa000000-0000-4000-8000-0000000000ff",
                         "first_usable_lba": 34, "last_usable_lba": {}, "entries_lba": 2, "entry_count": 128, "entry_size": 128,
                         "primary_header_ok": true, "backup_header": "ok", "backup_lba": {}, "backup_at_end": true,
                         "protective_mbr": {{"present": true, "hybrid": false, "entries": [
                           {{"number": 1, "start_lba": 1, "sectors": {}, "size_bytes": {}, "type": "0xee", "bootable": false}}]}}}}"#,
                    self.total - 34 - self.short,
                    self.total - 1 - self.short,
                    self.total - 1 - self.short,
                    (self.total - 1 - self.short) * 512
                ),
                _ => r#"{"type": "dos", "sector_size": 512, "disk_signature": "1f2e3d4c"}"#.to_string(),
            };
            let fam = self.family.map(|f| format!(", \"family\": \"{f}\"")).unwrap_or_default();
            let text = format!(
                r#"{{"format": "bko-holt", "schema": 1, "privileged": true, "host": {{"hostname": "t"}},
                     "os": {{"id": "debian", "version_id": "13"{fam}}}, "firmware": {{"mode": "bios"}},
                     "storage": {{
                       "disks": [{{"name": "vda", "kind": "disk", "size_bytes": {}, "size": "{}", "logical_sector_size": 512,
                         "model": null, "serial": null, "wwid": null, "partition_table": {t},
                         "partitions": [{}], "content": null}}],
                       "md_arrays": [], "dm_devices": [{}], "lvm": {{"volume_groups": [{}]}},
                       "btrfs": [], "mounts": [{}], "fstab": []}}}}"#,
                self.total * 512,
                human(self.total * 512),
                self.parts.join(", "),
                self.dm.join(", "),
                self.vgs.join(", "),
                self.mounts.join(", ")
            );
            crate::json::parse(&text).unwrap_or_else(|e| panic!("{} at byte {} in {text}", e.message, e.offset))
        }
    }

    fn ext4(blocks: u64, free: Option<u64>) -> String {
        let f = free.map(|n| format!(", \"free_blocks\": {n}")).unwrap_or_default();
        format!(
            r#"{{"type": "ext4", "usage": "filesystem", "uuid": "0b0e2c6e-1111-4222-8333-444455556666", "label": null,
                 "block_size": 4096, "blocks": {blocks}, "size_bytes": {}, "blocks_per_group": 32768, "inode_size": 256,
                 "features": ["has_journal", "extent", "64bit"]{f}}}"#,
            blocks * 4096
        )
    }

    /// An ext4 filling a device of SECTORS, with FREE bytes free.
    fn ext4_on(sectors: u64, free: u64) -> String {
        ext4(sectors / 8, Some(free / 4096))
    }

    fn xfs(blocks: u64, ag: u64) -> String {
        format!(
            r#"{{"type": "xfs", "usage": "filesystem", "uuid": "0b0e2c6e-1111-4222-8333-444455557777", "label": null,
                 "block_size": 4096, "blocks": {blocks}, "size_bytes": {}, "ag_blocks": {ag}, "ag_count": {},
                 "log_blocks": 2560, "free_blocks": {}, "features": ["crc", "finobt"]}}"#,
            blocks * 4096,
            blocks.div_ceil(ag),
            blocks / 2
        )
    }

    fn btrfs(bytes: u64) -> String {
        format!(
            r#"{{"type": "btrfs", "usage": "filesystem", "uuid": "b7b7b7b7-0000-4000-8000-000000000003", "label": null,
                 "node_size": 16384, "sector_size": 4096, "total_bytes": {bytes}, "devid": 1, "features": ["no_holes"]}}"#
        )
    }

    fn swap() -> String {
        r#"{"type": "swap", "usage": "other", "uuid": "5e5e5e5e-0000-4000-8000-000000000002", "label": null, "page_size": 4096}"#.to_string()
    }

    /// A FAT whose BPB records HIDDEN as the sector its partition starts at.
    fn vfat(hidden: u64) -> String {
        format!(
            r#"{{"type": "vfat", "usage": "filesystem", "uuid": "1234-ABCD", "label": "ESP", "fat_bits": 32,
                 "bytes_per_sector": 512, "sectors_per_cluster": 8, "fats": 2, "reserved_sectors": 32,
                 "root_entries": 0, "hidden_sectors": {hidden}, "media": 248}}"#
        )
    }

    fn luks(offset: u64) -> String {
        format!(
            r#"{{"type": "crypto_LUKS", "usage": "crypto", "uuid": "6c3e5f0e-6b0a-4d6c-9a2e-8f7e1d2c3b4a", "version": "2",
                 "segments": {{"0": {{"encryption": "aes-xts-plain64", "offset": "{offset}", "sector_size": 512}}}},
                 "keyslots": {{"0": {{"kdf": "argon2id", "key_bits": 512}}}}}}"#
        )
    }

    fn pv() -> String {
        r#"{"type": "LVM2_member", "usage": "raid", "uuid": "bkoLVM-0000-0000-0000-0000-0000-000001"}"#.to_string()
    }

    /// The dm device a LUKS container on ON was opened as.
    fn crypt_dm(name: &str, on: &str, bytes: u64, content: &str) -> String {
        format!(
            r#"{{"name": "{name}", "kind": "dm", "size_bytes": {bytes}, "size": "{}", "logical_sector_size": 512,
                 "slaves": ["{on}"], "holders": [], "partition_table": null, "partitions": [],
                 "dm": {{"dm_name": "{on}_crypt", "mapper_path": "/dev/mapper/{on}_crypt", "target": "crypt",
                         "crypt_type": "LUKS2", "luks_uuid": "6c3e5f0e-6b0a-4d6c-9a2e-8f7e1d2c3b4a"}},
                 "content": {content}}}"#,
            human(bytes)
        )
    }

    fn lv_dm(name: &str, lv: &str, on: &str, bytes: u64, content: &str) -> String {
        format!(
            r#"{{"name": "{name}", "kind": "dm", "size_bytes": {bytes}, "size": "{}", "logical_sector_size": 512,
                 "slaves": ["{on}"], "holders": [], "partition_table": null, "partitions": [],
                 "dm": {{"dm_name": "vg0-{lv}", "mapper_path": "/dev/mapper/vg0-{lv}", "target": "lvm", "vg": "vg0", "lv": "{lv}"}},
                 "content": {content}}}"#,
            human(bytes)
        )
    }

    /// The extent size of every volume group here.
    const EXT: u64 = 4 * MIB;

    /// A volume group of one PV on DEV, with LVS as (name, extents, the
    /// physical extent each stripe starts at).
    fn vg(dev: &str, dev_size: u64, pe_count: u64, lvs: &[(&str, u64, Vec<u64>)]) -> String {
        let body: Vec<String> = lvs
            .iter()
            .enumerate()
            .map(|(i, (name, extents, at))| {
                let stripes: Vec<String> = at.iter().map(|a| format!("\"pv0\", {a}")).collect();
                format!(
                    r#"{{"name": "{name}", "id": "bkoLVM-0000-0000-0000-0000-0000-00000{}", "status": ["READ", "WRITE", "VISIBLE"], "flags": [],
                         "size_bytes": {}, "segments": [{{"start_extent": 0, "extent_count": {extents}, "type": "striped",
                         "stripe_count": {}, "stripes": [{}]}}]}}"#,
                    i + 3,
                    extents * EXT,
                    at.len(),
                    stripes.join(", ")
                )
            })
            .collect();
        format!(
            r#"{{"name": "vg0", "id": "bkoLVM-0000-0000-0000-0000-0000-000002", "seqno": 4, "format": "lvm2",
                 "status": ["RESIZEABLE", "READ", "WRITE"], "flags": [], "extent_size_bytes": {EXT},
                 "max_lv": 0, "max_pv": 0, "metadata_copies": 0,
                 "physical_volumes": [{{"name": "pv0", "id": "bkoLVM-0000-0000-0000-0000-0000-000001", "device_hint": "/dev/{dev}",
                   "dev_size_bytes": {dev_size}, "pe_start_bytes": {MIB}, "pe_count": {pe_count}, "status": ["ALLOCATABLE"], "device": "{dev}"}}],
                 "logical_volumes": [{}]}}"#,
            body.join(", ")
        )
    }

    fn one_target(size: u64) -> Vec<Target> {
        vec![Target { disk: "vda".into(), path: "/dev/sdb".into(), size_bytes: size }]
    }

    /// The fit of V onto a target of SIZE bytes, which must hold.
    fn fitted(v: &Value, size: u64, pct: u64) -> (Value, Fit) {
        let f = fit(v, &one_target(size), pct);
        assert!(f.problems.is_empty(), "problems: {:?}", f.problems);
        (f.v.clone().expect("a rewritten dump"), f)
    }

    /// Why the fit of V onto a target of SIZE bytes was refused.
    fn refused(v: &Value, size: u64, pct: u64) -> String {
        let f = fit(v, &one_target(size), pct);
        assert!(f.v.is_none(), "it wasn't refused: {:?}", f.disks);
        assert!(!f.problems.is_empty(), "no problem said why");
        f.problems.join("\n")
    }

    /// Every partition of the disk, as (number, start, sectors).
    fn geom(v: &Value) -> Vec<(u64, u64, u64)> {
        v.at(&["storage", "disks", "0", "partitions"])
            .and_then(Value::as_array)
            .unwrap()
            .iter()
            .map(|p| (u(p, "number").unwrap(), u(p, "start_lba").unwrap(), u(p, "sectors").unwrap()))
            .collect()
    }

    fn at(v: &Value, p: &[&str]) -> u64 {
        v.at(p).and_then(Value::as_u64).unwrap_or_else(|| panic!("no number at {p:?}"))
    }

    /// A volume group's field, by the path under it.
    fn vg_at(v: &Value, p: &[&str]) -> u64 {
        let mut path = vec!["storage", "lvm", "volume_groups", "0"];
        path.extend(p);
        at(v, &path)
    }

    /// The motivating layout: /boot 300 MiB, swap 16 GiB, and the rest one
    /// filesystem, on a 64 GiB disk.
    fn boot_swap_root() -> D {
        let total = 64 * G;
        let boot = 300 * M;
        let sw = 16 * G;
        let root_start = 2048 + boot + sw;
        let root = total - 34 - root_start + 1;
        D::gpt(total)
            .part(1, 2048, boot, &ext4_on(boot, 230 * MIB), "")
            .part(2, 2048 + boot, sw, &swap(), "")
            .part(3, root_start, root, &btrfs(root * 512), "")
            .mount("vda3", root * 512, 8 * GIB)
    }

    /// One 20 GiB ext4 partition filling the disk, with FREE bytes free.
    fn one_root(free: u64) -> (u64, D) {
        let root = 20 * G;
        let total = 2048 + root + 34;
        (total, D::gpt(total).part(1, 2048, root, &ext4_on(root, free), "").mount("vda1", 20 * GIB, 20 * GIB - free))
    }

    #[test]
    fn the_motivating_example() {
        let v = boot_swap_root().build();
        let (out, f) = fitted(&v, 63 * GIB, 5);
        let (old, new) = (geom(&v), geom(&out));
        // /boot and swap keep their place and size; the root loses 1 GiB.
        assert_eq!(new[0], old[0]);
        assert_eq!(new[1], old[1]);
        assert_eq!(new[2], (3, old[2].1, old[2].2 - G));
        // The disk, the last usable LBA and the backup header follow.
        assert_eq!(at(&out, &["storage", "disks", "0", "size_bytes"]), 63 * GIB);
        assert_eq!(at(&out, &["storage", "disks", "0", "partition_table", "last_usable_lba"]), 63 * G - 34);
        assert_eq!(at(&out, &["storage", "disks", "0", "partition_table", "backup_lba"]), 63 * G - 1);
        assert_eq!(at(&out, &["storage", "disks", "0", "partition_table", "protective_mbr", "entries", "0", "sectors"]), 63 * G - 1);
        // The btrfs on it fills the partition, as mkfs.btrfs makes it.
        assert_eq!(at(&out, &["storage", "disks", "0", "partitions", "2", "content", "total_bytes"]), new[2].2 * 512);
        // /boot's and the swap's entries, identifiers and all, are untouched.
        for i in ["0", "1"] {
            assert_eq!(v.at(&["storage", "disks", "0", "partitions", i]), out.at(&["storage", "disks", "0", "partitions", i]));
        }
        let p = f.disks[0].part.as_ref().unwrap();
        assert_eq!((p.number, p.what.as_str()), (3, "btrfs"));
        assert!(f.disks[0].moved.is_empty());
        let r = f.report(5).join("\n");
        assert!(r.contains("vda -> /dev/sdb: fitted with --fit-largest 5%: 64 GiB -> 63 GiB"), "{r}");
        assert!(r.contains("vda3 (btrfs) shrinks by 1 GiB"), "{r}");
        assert!(r.contains("8 GiB used"), "{r}");
    }

    #[test]
    fn exactly_pct_passes_and_one_sector_over_refuses() {
        // A 20 GiB root that has to give up exactly 2 GiB: 10% of it.
        let root = 20 * G;
        let total = 2048 + G + root + 34;
        let v = D::gpt(total)
            .part(1, 2048, G, &ext4_on(G, 512 * MIB), "")
            .part(2, 2048 + G, root, &ext4_on(root, 16 * GIB), "")
            .mount("vda2", 20 * GIB, 4 * GIB)
            .build();
        // The largest target whose overrun is exactly 2 GiB: the root ends
        // on the last usable sector, so the table's own 34 come off too.
        let exact = (total - 2 * G - 1) * 512;
        let (out, _) = fitted(&v, exact, 10);
        assert_eq!(geom(&out)[1], (2, 2048 + G, root - 2 * G));
        assert_eq!(at(&out, &["storage", "disks", "0", "partitions", "1", "content", "blocks"]), (root - 2 * G) / 8);
        assert!(refused(&v, exact, 9).contains("more than 9%"));
        // One sector smaller: the shrink rounds up to the next whole MiB,
        // which 10% of the root no longer covers.
        assert!(refused(&v, exact - 512, 10).contains("more than 10%"));
        let (out, _) = fitted(&v, exact - 512, 11);
        assert_eq!(geom(&out)[1], (2, 2048 + G, root - 2 * G - M));
    }

    #[test]
    fn only_the_sources_slack_is_lost() {
        // The root ends 4 GiB before the last usable sector.
        let root = 20 * G;
        let total = 2048 + root + 4 * G + 34;
        let v = D::gpt(total).part(1, 2048, root, &ext4_on(root, GIB), "").build();
        let (out, f) = fitted(&v, (total - 2 * G) * 512, 1);
        assert_eq!(geom(&out), geom(&v));
        assert!(f.disks[0].part.is_none());
        assert_eq!(at(&out, &["storage", "disks", "0", "partition_table", "last_usable_lba"]), total - 2 * G - 34);
        assert!(f.disks[0].notes[0].contains("no partition changes"), "{:?}", f.disks[0].notes);
        // Nothing shrinks, so how full it is doesn't come into it.
        assert!(f.disks[0].fits.is_empty());
    }

    #[test]
    fn a_tie_for_largest_is_refused() {
        let half = 8 * G;
        let total = 2048 + 2 * half + 34;
        let v = D::gpt(total)
            .part(1, 2048, half, &ext4_on(half, GIB), "")
            .part(2, 2048 + half, half, &ext4_on(half, GIB), "")
            .build();
        let why = refused(&v, (total - M) * 512, 50);
        assert!(why.contains("vda1 (8 GiB) and vda2 (8 GiB) are the same size"), "{why}");
    }

    #[test]
    fn the_largest_isnt_last_and_the_rest_move() {
        // /boot, root, swap: the root shrinks and the swap moves down.
        let root = 20 * G;
        let total = 2048 + G + root + 2 * G + 34;
        let v = D::mbr(total)
            .part(1, 2048, G, &ext4_on(G, 512 * MIB), "\"bootable\": true")
            .part(2, 2048 + G, root, &xfs(root / 8, root / 32), "")
            .part(3, 2048 + G + root, 2 * G, &swap(), "")
            .mount("vda2", 20 * GIB, 2 * GIB)
            .build();
        let (out, f) = fitted(&v, (total - M) * 512, 50);
        let (old, new) = (geom(&v), geom(&out));
        assert_eq!(new[0], old[0]);
        assert_eq!(new[1], (2, old[1].1, old[1].2 - M));
        assert_eq!(new[2], (3, old[2].1 - M, old[2].2));
        assert_eq!(f.disks[0].moved, vec![(3, -(M as i64))]);
        // An MBR has no last usable LBA to move, and the swap keeps its UUID.
        assert!(out.at(&["storage", "disks", "0", "partition_table", "last_usable_lba"]).is_none());
        assert_eq!(v.at(&["storage", "disks", "0", "partitions", "2", "content"]), out.at(&["storage", "disks", "0", "partitions", "2", "content"]));
        // The XFS keeps its allocation group size, and counts them again.
        let blocks = at(&out, &["storage", "disks", "0", "partitions", "1", "content", "blocks"]);
        let ag = at(&out, &["storage", "disks", "0", "partitions", "1", "content", "ag_blocks"]);
        assert_eq!(blocks, (root - M) / 8);
        assert_eq!(ag, root / 32);
        assert_eq!(at(&out, &["storage", "disks", "0", "partitions", "1", "content", "ag_count"]), blocks.div_ceil(ag));
    }

    #[test]
    fn the_last_allocation_group_is_never_a_stub() {
        // An XFS of five whole 5 GiB allocation groups on a 25 GiB
        // partition, shrunk by a little less than one of them.
        let root = 25 * G;
        let total = 2048 + root + 34;
        let ag = 5 * GIB / 4096;
        let v = D::gpt(total).part(1, 2048, root, &xfs(root / 8, ag), "").mount("vda1", 25 * GIB, 8 * GIB).build();
        let blocks = |out: &Value| at(out, &["storage", "disks", "0", "partitions", "0", "content", "blocks"]);
        // 8 MiB of a fifth group would be left: mkfs.xfs refuses that, so
        // the fit leaves it out and keeps four whole groups.
        let (out, _) = fitted(&v, (total - 5 * G + 8 * M) * 512, 50);
        assert_eq!(blocks(&out), 4 * ag);
        assert_eq!(at(&out, &["storage", "disks", "0", "partitions", "0", "content", "ag_count"]), 4);
        // 64 MiB of it is group enough to keep.
        let (out, _) = fitted(&v, (total - 5 * G + 64 * M) * 512, 50);
        assert_eq!(blocks(&out), 4 * ag + 64 * MIB / 4096);
        assert_eq!(at(&out, &["storage", "disks", "0", "partitions", "0", "content", "ag_count"]), 5);
    }

    #[test]
    fn growth_is_capped_by_pct_and_by_room() {
        let (total, d) = one_root(16 * GIB);
        let v = d.build();
        let root = 20 * G;
        // 10% of 20 GiB is 2 GiB, and there are 16 GiB to grow into.
        let (out, f) = fitted(&v, (total + 16 * G) * 512, 10);
        assert_eq!(geom(&out)[0], (1, 2048, root + 2 * G));
        assert_eq!(at(&out, &["storage", "disks", "0", "partitions", "0", "content", "blocks"]), (root + 2 * G) / 8);
        assert!(f.disks[0].fits.is_empty(), "nothing shrinks, so there's nothing to check");
        // The 14 GiB PCT held it back from is said to stay unused, as it is
        // without the flag.
        assert!(f.disks[0].notes.iter().any(|n| n == "the target is bigger; the extra 14 GiB stays unused"), "{:?}", f.disks[0].notes);
        assert!(f.report(10).iter().any(|l| l.contains("the extra 14 GiB stays unused")), "{:?}", f.report(10));
        // Room first: one GiB of disk, where 10% would allow two.
        let (out, _) = fitted(&v, (total + G) * 512, 10);
        assert_eq!(geom(&out)[0], (1, 2048, root + G));
        // Less than a MiB to grow into: the extra stays unused.
        let (out, f) = fitted(&v, (total + 1024) * 512, 10);
        assert_eq!(geom(&out), geom(&v));
        assert!(f.disks[0].notes[0].contains("doesn't grow"), "{:?}", f.disks[0].notes);
    }

    #[test]
    fn mbr_growth_stops_where_a_partition_entry_does() {
        // An MBR partition reaching most of the way to the 32-bit limit: a
        // 4 TiB target has room for half of it again, and an MBR entry
        // hasn't, so the fit stops at the last sector one can hold.
        let root = 3_774_873_600;
        let total = 2048 + root;
        let v = D::mbr(total).part(1, 2048, root, &ext4_on(root, 512 * GIB), "").mount("vda1", root * 512, 256 * GIB).build();
        let (out, f) = fitted(&v, 4 << 40, 50);
        let (number, start, sectors) = geom(&out)[0];
        assert_eq!((number, start), (1, 2048));
        assert_eq!(start + sectors - 1, MBR_LAST, "it grows to the last sector an entry reaches");
        assert_eq!(at(&out, &["storage", "disks", "0", "partitions", "0", "content", "blocks"]), sectors / 8);
        // And the 2 TiB it couldn't take is said to stay unused.
        assert!(f.disks[0].notes.iter().any(|n| n.contains("the extra 2 TiB stays unused")), "{:?}", f.disks[0].notes);
        // A partition already at the limit doesn't grow at all.
        let v = D::mbr(MBR_LAST + 1).part(1, 2048, MBR_LAST - 2047, &ext4_on(MBR_LAST - 2047, GIB), "").build();
        let (out, f) = fitted(&v, 4 << 40, 50);
        assert_eq!(geom(&out), geom(&v));
        assert!(f.disks[0].notes.iter().any(|n| n.contains("an MBR partition entry reaches no further")), "{:?}", f.disks[0].notes);
    }

    #[test]
    fn a_table_short_of_the_disks_end() {
        // A disk grown without moving its GPT: its last usable sector says
        // nothing about how much smaller the target is, and on a target
        // bigger by less than the table's own tail there's no room at all.
        let root = 20 * G;
        let total = 2048 + root + 20 * G + 34;
        let v = D::gpt(total).short(20 * G).part(1, 2048, root, &ext4_on(root, GIB), "").build();
        let (out, f) = fitted(&v, (total - 4 * G) * 512, 10);
        assert_eq!(geom(&out), geom(&v));
        let notes = f.disks[0].notes.join("; ");
        assert!(notes.contains("no partition changes"), "{notes}");
        assert!(notes.contains("only 4 GiB of the source's unused space is gone"), "{notes}");
        // The same table with its last partition run into the tail, on a
        // target 16 sectors bigger: nothing past it to grow into.
        let v = D::gpt(total).part(1, 2048, total - 2048, &ext4_on(root, GIB), "").build();
        let (out, f) = fitted(&v, (total + 16) * 512, 10);
        assert_eq!(geom(&out), geom(&v));
        assert!(f.disks[0].notes.iter().any(|n| n.contains("less than 1 MiB to grow into")), "{:?}", f.disks[0].notes);
    }

    #[test]
    fn a_moved_fat_is_told_where_it_now_starts() {
        // mkfs.fat -h writes the partition's first sector into the BPB, so
        // a FAT after the largest partition has to be given the new one.
        let root = 20 * G;
        let esp = 512 * M;
        let total = 2048 + root + esp + 34;
        let start = 2048 + root;
        let d = |hidden: u64| {
            D::gpt(total)
                .part(1, 2048, root, &ext4_on(root, 8 * GIB), "")
                .part(2, start, esp, &vfat(hidden), "")
                .mount("vda1", 20 * GIB, 4 * GIB)
        };
        let src = d(start).build();
        let (out, _) = fitted(&src, (total - G) * 512, 10);
        assert_eq!(geom(&out)[1], (2, start - G, esp));
        assert_eq!(at(&out, &["storage", "disks", "0", "partitions", "1", "content", "hidden_sectors"]), start - G);
        // Nothing else about it changes, identifiers and all.
        for k in ["uuid", "label", "fat_bits", "reserved_sectors", "media"] {
            let p = ["storage", "disks", "0", "partitions", "1", "content", k];
            assert_eq!(src.at(&p), out.at(&p), "{k}");
        }
        // One that records no start keeps that: it says nothing about where
        // the partition is.
        let (out, _) = fitted(&d(0).build(), (total - G) * 512, 10);
        assert_eq!(at(&out, &["storage", "disks", "0", "partitions", "1", "content", "hidden_sectors"]), 0);
        // One recording something else isn't a start the fit can mend.
        assert!(refused(&d(1).build(), (total - G) * 512, 10).contains("records sector 1 as its first, not its own start"));
    }

    #[test]
    fn a_two_group_stub_has_to_hold_the_log() {
        // mkfs.xfs puts the internal log in allocation group agcount/2,
        // which is the last of two: a stub too small for the log as well is
        // left out, as a stub under the minimum already is.
        let ag = 4 * GIB / 4096;
        let root = 8 * G;
        let total = 2048 + root + 34;
        let v = D::gpt(total).part(1, 2048, root, &xfs(2 * ag, ag), "").mount("vda1", 8 * GIB, GIB).build();
        let blocks = |out: &Value| at(out, &["storage", "disks", "0", "partitions", "0", "content", "blocks"]);
        let groups = |out: &Value| at(out, &["storage", "disks", "0", "partitions", "0", "content", "ag_count"]);
        // 20 MiB of a second group: past the 16 MiB minimum, and short of
        // that again plus the 10 MiB log, so the group goes.
        let (out, _) = fitted(&v, (total - 4076 * M) * 512, 50);
        assert_eq!((blocks(&out), groups(&out)), (ag, 1));
        // 40 MiB of it holds the log and its own headers.
        let (out, _) = fitted(&v, (total - 4056 * M) * 512, 50);
        assert_eq!((blocks(&out), groups(&out)), (ag + 10240, 2));
    }

    #[test]
    fn a_filesystem_smaller_than_its_partition_keeps_its_slack() {
        // An installer's XFS a GiB smaller than the partition it was made in.
        let root = 20 * G;
        let total = 2048 + root + 34;
        let blocks = (root - G) / 8;
        let v = D::gpt(total).part(1, 2048, root, &xfs(blocks, blocks / 4), "").mount("vda1", 19 * GIB, 2 * GIB).build();
        let (out, _) = fitted(&v, (total - G) * 512, 20);
        // It loses the GiB the partition lost, and keeps the slack.
        assert_eq!(at(&out, &["storage", "disks", "0", "partitions", "0", "content", "blocks"]), blocks - G / 8);
        // Growing, a filesystem with slack keeps its size.
        let (out, f) = fitted(&v, (total + G) * 512, 20);
        assert_eq!(at(&out, &["storage", "disks", "0", "partitions", "0", "content", "blocks"]), blocks);
        assert!(f.disks[0].notes.iter().any(|n| n.contains("keeps that size")), "{:?}", f.disks[0].notes);
    }

    #[test]
    fn every_other_stack_is_refused() {
        let root = 20 * G;
        let total = 2048 + root + 34;
        let smaller = (total - M) * 512;
        let case = |content: &str| {
            let v = D::gpt(total).part(1, 2048, root, content, "").build();
            refused(&v, smaller, 50)
        };
        assert!(case(&swap()).contains("holds swap"));
        assert!(case(r#"{"type": "linux_raid_member", "uuid": "a"}"#).contains("md array's member"));
        assert!(case(r#"{"type": "zfs_member", "label": "rpool"}"#).contains("ZFS pool's member"));
        assert!(case(r#"{"type": "bcachefs", "uuid": "a"}"#).contains("isn't one it resizes"));
        assert!(case(r#"{"type": "vfat", "uuid": "1234-ABCD"}"#).contains("isn't one it resizes"));
        assert!(case("null").contains("nothing is on"));
        // btrfs over two devices.
        let v = D::gpt(total).part(1, 2048, root, &btrfs(root * 512), "").build();
        let two = crate::json::parse(&v.compact().replace(
            r#""btrfs":[]"#,
            r#""btrfs":[{"uuid":"b7b7b7b7-0000-4000-8000-000000000003","devices":["vda1","vdb1"]}]"#,
        ))
        .unwrap();
        assert!(refused(&two, smaller, 50).contains("one of 2 devices"));
        // An MBR logical partition, and one that would have to move.
        let ext = root - 4 * G;
        let v = D::mbr(total)
            .part(1, 2048, 4 * G, &ext4_on(4 * G, GIB), "")
            .part(2, 2048 + 4 * G, ext, "null", "\"extended\": true")
            .part(5, 2048 + 4 * G + 2, ext - 2, &ext4_on(ext - 2, GIB), "\"logical\": true")
            .mount("vda5", 16 * GIB, GIB)
            .build();
        assert!(refused(&v, smaller, 50).contains("is an MBR logical partition"));
        let v = D::mbr(total)
            .part(1, 2048, root - 4 * G, &ext4_on(root - 4 * G, 8 * GIB), "")
            .part(2, 2048 + root - 4 * G, 4 * G, "null", "\"extended\": true")
            .part(5, 2048 + root - 4 * G + 2, 4 * G - 2, &ext4_on(4 * G - 2, GIB), "\"logical\": true")
            .mount("vda1", 16 * GIB, 8 * GIB)
            .build();
        assert!(refused(&v, smaller, 50).contains("would have to move, and it's an MBR extended partition"));
        // A whole-disk layout with no table at all.
        let v = crate::json::parse(
            r#"{"format": "bko-holt", "schema": 1, "privileged": true, "os": {"id": "debian", "version_id": "13"},
                "storage": {"disks": [{"name": "vda", "size_bytes": 1073741824, "logical_sector_size": 512,
                  "partition_table": null, "partitions": [], "content": {"type": "xfs", "uuid": "a"}}]}}"#,
        )
        .unwrap();
        assert!(refused(&v, 1 << 29, 50).contains("no partition table"));
        // Another system's layout, whose table load writes itself.
        let v = D::gpt(total).part(1, 2048, root, &ext4_on(root, GIB), "").family("freebsd").build();
        assert!(refused(&v, smaller, 50).contains("this dump is freebsd's"));
        let f = fit(&v, &one_target((total + 4 * G) * 512), 50);
        assert!(f.v.is_none() && f.problems.is_empty());
        assert!(f.notes[0].contains("doesn't grow a freebsd layout"), "{:?}", f.notes);
        assert_eq!(f.new_end, vec!["vda".to_string()]);
    }

    #[test]
    fn a_moving_partition_whose_bytes_go_back_is_refused() {
        let root = 20 * G;
        let total = 2048 + root + 2 * M + 34;
        let v = D::gpt(total)
            .part(1, 2048, root, &ext4_on(root, 16 * GIB), "")
            .part(2, 2048 + root, 2 * M, &btrfs(2 * M * 512), r#""esp_image": [{"offset": 0, "b64": "AAA="}]"#)
            .mount("vda1", 20 * GIB, GIB)
            .build();
        assert!(refused(&v, (total - M) * 512, 50).contains("the dump holds its bytes to put back"));
    }

    #[test]
    fn the_data_has_to_fit_with_room_to_spare() {
        // 18 GiB used of 20: 2 GiB free, and a 2 GiB shrink needs 3.
        let (total, d) = one_root(2 * GIB);
        let why = refused(&d.build(), (total - 2 * G) * 512, 50);
        assert!(why.contains("holds 18 GiB of 20 GiB and would shrink by 2 GiB"), "{why}");
        assert!(why.contains("1 GiB to spare"), "{why}");
        // A GiB of it: that fits, with the GiB to spare.
        let (_, f) = fitted(&d.build(), (total - G) * 512, 50);
        assert_eq!(f.disks[0].fits[0], DataFit { dev: "vda1".into(), kind: "ext4".into(), shrink: GIB, used: 18 * GIB, free: 2 * GIB, need: 2 * GIB });
        // 5% of what's used, where that's more than a GiB: 120 GiB used of
        // 200, so a 76 GiB shrink needs 82 GiB free and has 80.
        let big = 200 * G;
        let btotal = 2048 + big + 34;
        let v = D::gpt(btotal).part(1, 2048, big, &ext4_on(big, 80 * GIB), "").mount("vda1", 200 * GIB, 120 * GIB).build();
        let why = refused(&v, (btotal - 76 * G) * 512, 50);
        assert!(why.contains("would shrink by 76 GiB"), "{why}");
        assert!(why.contains("82 GiB free it needs (the shrink and 6 GiB to spare)"), "{why}");
        let (_, f) = fitted(&v, (btotal - 70 * G) * 512, 50);
        assert_eq!(f.disks[0].fits[0].need, 76 * GIB);
        // The superblock's own free blocks, where nothing says it's mounted.
        let sb = D::gpt(total).part(1, 2048, 20 * G, &ext4_on(20 * G, 4 * GIB), "").build();
        let (_, f) = fitted(&sb, (total - 2 * G) * 512, 50);
        assert_eq!((f.disks[0].fits[0].free, f.disks[0].fits[0].used), (4 * GIB, 16 * GIB));
        // Nothing says how full it is: the shrink is refused.
        let blind = D::gpt(total).part(1, 2048, 20 * G, &ext4(20 * G / 8, None), "").build();
        assert!(refused(&blind, (total - M) * 512, 50).contains("doesn't say how much of it is used"));
    }

    #[test]
    fn luks_keeps_its_payload_offset() {
        let root = 20 * G;
        let total = 2048 + root + 34;
        let off = 16 * MIB;
        let inner = root * 512 - off;
        let v = D::gpt(total)
            .part(1, 2048, root, &luks(off), "")
            .dm(crypt_dm("dm-0", "vda1", inner, &ext4(inner / 4096, Some(GIB / 4096))))
            .mount("dm-0", inner, 4 * GIB)
            .build();
        let (out, f) = fitted(&v, (total - G) * 512, 50);
        assert_eq!(geom(&out)[0], (1, 2048, root - G));
        assert_eq!(at(&out, &["storage", "dm_devices", "0", "size_bytes"]), inner - GIB);
        assert_eq!(at(&out, &["storage", "dm_devices", "0", "content", "blocks"]), (inner - GIB) / 4096);
        // The header's offset and the LUKS UUID are still the source's.
        assert_eq!(v.at(&["storage", "disks", "0", "partitions", "0", "content"]), out.at(&["storage", "disks", "0", "partitions", "0", "content"]));
        assert_eq!(f.disks[0].part.as_ref().unwrap().what, "ext4 on LUKS");
        assert_eq!(f.disks[0].fits[0].dev, "dm-0");
    }

    /// A 20 GiB LVM partition: a PV of 5119 extents of 4 MiB, root and
    /// swap_1 on it, and FREE extents left over at the top.
    fn lvm_disk(free: u64) -> D {
        let root = 5119 - 256 - free;
        let part = 20 * G;
        let total = 2048 + part + 34;
        D::gpt(total)
            .part(1, 2048, part, &pv(), "")
            .vg(vg("vda1", part * 512, 5119, &[("root", root, vec![0]), ("swap_1", 256, vec![root])]))
            .dm(lv_dm("dm-0", "root", "vda1", root * EXT, &ext4(root * EXT / 4096, Some(root * MIB / 4096))))
            .dm(lv_dm("dm-1", "swap_1", "vda1", 256 * EXT, &swap()))
            .mount("dm-0", root * EXT, root * 2 * MIB)
    }

    /// The 20 GiB LVM disk's size in sectors.
    const LVM_TOTAL: u64 = 2048 + 20 * G + 34;

    #[test]
    fn lvm_gives_up_free_extents_first() {
        // 512 free extents at the top, of which a GiB's worth has to go.
        let v = lvm_disk(512).build();
        let (out, f) = fitted(&v, (LVM_TOTAL - G) * 512, 10);
        // The PV shrinks; no logical volume changes at all.
        assert_eq!(vg_at(&out, &["physical_volumes", "0", "pe_count"]), 5119 - 256);
        assert_eq!(vg_at(&out, &["physical_volumes", "0", "dev_size_bytes"]), (20 * G - G) * 512);
        assert_eq!(
            v.at(&["storage", "lvm", "volume_groups", "0", "logical_volumes"]),
            out.at(&["storage", "lvm", "volume_groups", "0", "logical_volumes"])
        );
        assert_eq!(v.at(&["storage", "dm_devices"]), out.at(&["storage", "dm_devices"]));
        assert!(f.disks[0].notes.iter().any(|n| n.contains("all of them free")), "{:?}", f.disks[0].notes);
        assert!(f.disks[0].fits.is_empty());
    }

    #[test]
    fn lvm_shrinks_its_largest_volume_and_compacts() {
        // No free extents: the root volume gives up 256 of them, and swap_1,
        // which sits above it, is renumbered down.
        let v = lvm_disk(0).build();
        let root = 5119 - 256;
        let (out, f) = fitted(&v, (LVM_TOTAL - G) * 512, 10);
        assert_eq!(vg_at(&out, &["physical_volumes", "0", "pe_count"]), 5119 - 256);
        assert_eq!(vg_at(&out, &["logical_volumes", "0", "size_bytes"]), (root - 256) * EXT);
        assert_eq!(vg_at(&out, &["logical_volumes", "0", "segments", "0", "extent_count"]), root - 256);
        // root keeps its first extent; swap_1 moves down by the 256 dropped.
        assert_eq!(vg_at(&out, &["logical_volumes", "0", "segments", "0", "stripes", "1"]), 0);
        assert_eq!(vg_at(&out, &["logical_volumes", "1", "segments", "0", "stripes", "1"]), root - 256);
        assert_eq!(vg_at(&out, &["logical_volumes", "1", "size_bytes"]), 256 * EXT);
        assert_eq!(vg_at(&out, &["logical_volumes", "1", "segments", "0", "extent_count"]), 256);
        // The volume's device and its filesystem shrink with it.
        assert_eq!(at(&out, &["storage", "dm_devices", "0", "size_bytes"]), (root - 256) * EXT);
        assert_eq!(at(&out, &["storage", "dm_devices", "0", "content", "blocks"]), (root - 256) * EXT / 4096);
        // swap_1's own device, UUID and all, doesn't change.
        assert_eq!(v.at(&["storage", "dm_devices", "1"]), out.at(&["storage", "dm_devices", "1"]));
        assert_eq!(f.disks[0].fits[0].dev, "dm-0");
        assert!(f.disks[0].notes.iter().any(|n| n.contains("256 of them from the end of vg0/root")), "{:?}", f.disks[0].notes);
        // The volume's own size is held to the same percentage: a GiB is 5%
        // of the 20 GiB partition, and 5.2% of the 19 GiB volume.
        let why = refused(&v, (LVM_TOTAL - G) * 512, 5);
        assert!(why.contains("vg0/root would have to shrink by 1 GiB of its 19 GiB, more than 5%"), "{why}");
    }

    #[test]
    fn a_shrink_the_physical_volumes_own_room_covers() {
        // A PV whose extents stop short of its end: a megabyte off the disk
        // takes none of them, so the group stays exactly as it was.  (It
        // never gains free extents from a disk that got smaller.)
        let part = 20 * G;
        let root = 4700;
        let v = D::gpt(LVM_TOTAL)
            .part(1, 2048, part, &pv(), "")
            .vg(vg("vda1", part * 512, 5000, &[("root", root, vec![0]), ("swap_1", 256, vec![root])]))
            .dm(lv_dm("dm-0", "root", "vda1", root * EXT, &ext4(root * EXT / 4096, Some(root * MIB / 4096))))
            .dm(lv_dm("dm-1", "swap_1", "vda1", 256 * EXT, &swap()))
            .mount("dm-0", root * EXT, root * 2 * MIB)
            .build();
        let (out, f) = fitted(&v, (LVM_TOTAL - M) * 512, 10);
        assert_eq!(vg_at(&out, &["physical_volumes", "0", "pe_count"]), 5000);
        assert_eq!(vg_at(&out, &["physical_volumes", "0", "dev_size_bytes"]), (part - M) * 512);
        assert_eq!(v.at(&["storage", "lvm", "volume_groups", "0", "logical_volumes"]), out.at(&["storage", "lvm", "volume_groups", "0", "logical_volumes"]));
        let notes = f.disks[0].notes.join("; ");
        assert!(notes.contains("keeps its 5000 extents") && notes.contains("1 MiB the disk lost"), "{notes}");
        assert!(!notes.contains("grows"), "a smaller disk doesn't grow the group: {notes}");
    }

    #[test]
    fn an_untouched_striped_volume_keeps_its_size() {
        // Free extents cover the shrink, so no logical volume changes at
        // all: a striped one's extents count as its own, not per stripe.
        let part = 20 * G;
        let total = 2048 + part + 34;
        let a = 4000;
        let v = D::gpt(total)
            .part(1, 2048, part, &pv(), "")
            .vg(vg("vda1", part * 512, 5119, &[("a", a, vec![0, a / 2])]))
            .dm(lv_dm("dm-0", "a", "vda1", a * EXT, &ext4(a * EXT / 4096, Some(15 * GIB / 4096))))
            .mount("dm-0", a * EXT, GIB)
            .build();
        let (out, f) = fitted(&v, (total - G) * 512, 10);
        assert_eq!(vg_at(&out, &["physical_volumes", "0", "pe_count"]), 5119 - 256);
        assert_eq!(
            v.at(&["storage", "lvm", "volume_groups", "0", "logical_volumes"]),
            out.at(&["storage", "lvm", "volume_groups", "0", "logical_volumes"])
        );
        assert_eq!(v.at(&["storage", "dm_devices"]), out.at(&["storage", "dm_devices"]));
        assert!(f.disks[0].fits.is_empty(), "nothing shrinks, so there's nothing to check");
        assert!(f.disks[0].notes.iter().any(|n| n.contains("all of them free")), "{:?}", f.disks[0].notes);
    }

    #[test]
    fn lvm_growth_leaves_the_new_extents_free() {
        let v = lvm_disk(0).build();
        let (out, f) = fitted(&v, (LVM_TOTAL + G) * 512, 10);
        assert_eq!(vg_at(&out, &["physical_volumes", "0", "pe_count"]), 5119 + 256);
        assert_eq!(
            v.at(&["storage", "lvm", "volume_groups", "0", "logical_volumes"]),
            out.at(&["storage", "lvm", "volume_groups", "0", "logical_volumes"])
        );
        assert_eq!(v.at(&["storage", "dm_devices"]), out.at(&["storage", "dm_devices"]));
        assert!(f.disks[0].notes.iter().any(|n| n.contains("stay free in it")), "{:?}", f.disks[0].notes);
    }

    #[test]
    fn lvm_refusals() {
        let part = 20 * G;
        let total = 2048 + part + 34;
        let smaller = (total - G) * 512;
        let root = 5119 - 256;
        // The largest volume is swap.
        let v = D::gpt(total)
            .part(1, 2048, part, &pv(), "")
            .vg(vg("vda1", part * 512, 5119, &[("swap_1", root, vec![0]), ("root", 256, vec![root])]))
            .dm(lv_dm("dm-0", "swap_1", "vda1", root * EXT, &swap()))
            .dm(lv_dm("dm-1", "root", "vda1", 256 * EXT, &ext4(256 * EXT / 4096, Some(M))))
            .build();
        assert!(refused(&v, smaller, 50).contains("is swap, which the fit never shrinks"));
        // Two volumes of the same size, with nothing free between them.
        let v = D::gpt(total)
            .part(1, 2048, part, &pv(), "")
            .vg(vg("vda1", part * 512, 5118, &[("a", 2559, vec![0]), ("b", 2559, vec![2559])]))
            .dm(lv_dm("dm-0", "a", "vda1", 2559 * EXT, &ext4(2559 * EXT / 4096, Some(M))))
            .dm(lv_dm("dm-1", "b", "vda1", 2559 * EXT, &ext4(2559 * EXT / 4096, Some(M))))
            .build();
        assert!(refused(&v, smaller, 50).contains("same size, so there's no one largest logical volume"));
        // The largest volume is striped.
        let v = D::gpt(total)
            .part(1, 2048, part, &pv(), "")
            .vg(vg("vda1", part * 512, 5119, &[("a", 4000, vec![0, 2000]), ("b", 1119, vec![4000])]))
            .dm(lv_dm("dm-0", "a", "vda1", 4000 * EXT, &ext4(4000 * EXT / 4096, Some(M))))
            .dm(lv_dm("dm-1", "b", "vda1", 1119 * EXT, &ext4(1119 * EXT / 4096, Some(M))))
            .build();
        assert!(refused(&v, smaller, 50).contains("vg0/a is striped, and version 1 shrinks a linear one"));
        // A striped volume that would have to move: 97 free extents below
        // it, and the rest off the end of the linear volume below those.
        let v = D::gpt(total)
            .part(1, 2048, part, &pv(), "")
            .vg(vg("vda1", part * 512, 5119, &[("b", 4000, vec![0]), ("a", 1022, vec![4097, 4608])]))
            .dm(lv_dm("dm-0", "b", "vda1", 4000 * EXT, &ext4(4000 * EXT / 4096, Some(8 * GIB / 4096))))
            .dm(lv_dm("dm-1", "a", "vda1", 1022 * EXT, &ext4(1022 * EXT / 4096, Some(M))))
            .mount("dm-0", 4000 * EXT, 2 * GIB)
            .build();
        assert!(refused(&v, smaller, 50).contains("vg0/a is striped, and it would have to move"));
        // A second physical volume somewhere else.
        let two = crate::json::parse(&lvm_disk(0).build().compact().replace(
            r#""device":"vda1"}]"#,
            r#""device":"vda1"},{"name":"pv1","id":"x","dev_size_bytes":1024,"pe_start_bytes":1048576,"pe_count":1,"device":"vdb1"}]"#,
        ))
        .unwrap();
        assert!(refused(&two, smaller, 50).contains("has 2 physical volumes"));
        // A thin pool, which vgcfgrestore's own metadata can't be rebuilt from.
        let thin = crate::json::parse(&lvm_disk(0).build().compact().replace(r#""type":"striped""#, r#""type":"thin-pool""#)).unwrap();
        assert!(refused(&thin, smaller, 50).contains("has a thin-pool segment"));
    }

    #[test]
    fn a_table_fitted_before_is_recognised() {
        let v = boot_swap_root().build();
        let t = |size: u64| Target { disk: "vda".into(), path: "/dev/sdb".into(), size_bytes: size };
        let sectors = |x: &Value| -> Vec<(u32, u64, u64)> { geom(x).into_iter().map(|(n, s, l)| (n as u32, s, l)).collect() };
        let same = sectors(&v);
        // The source's own table, on a target of the source's size: no fit.
        assert_eq!(wrote(&v, &t(64 * GIB), &same), None);
        // The root a GiB smaller, as a fit onto a 63 GiB disk wrote it.
        let (out, _) = fitted(&v, 63 * GIB, 5);
        let after = sectors(&out);
        assert_eq!(wrote(&v, &t(63 * GIB), &after), Some((3, -(G as i64))));
        // On that disk the source's own table is no fit of it either: every
        // percentage takes the GiB out of the root.
        assert_eq!(wrote(&v, &t(63 * GIB), &same), None);
        // Nor is the fitted table on a disk no fit of it leaves that way.
        assert_eq!(wrote(&v, &t(62 * GIB), &after), None);
        // A table of another layout altogether, and a half-written one.
        assert_eq!(wrote(&v, &t(63 * GIB), &[(1, 2048, 1024), (2, 4096, 1024), (3, 8192, 1024)]), None);
        assert_eq!(wrote(&v, &t(63 * GIB), &after[..2]), None);
        // A partition someone resized by hand: a fit would have taken the
        // whole overrun, not four kilobytes of it.
        let mut by_hand = after.clone();
        by_hand[2].2 -= 8;
        assert_eq!(wrote(&v, &t(63 * GIB), &by_hand), None);
    }

    #[test]
    fn the_paths_it_writes_must_be_there() {
        let v = boot_swap_root().build();
        let mut x = v.clone();
        assert!(set_path(&mut x, &path(&["storage", "disks", "0", "size_bytes"]), Value::UInt(7)).is_ok());
        assert_eq!(at(&x, &["storage", "disks", "0", "size_bytes"]), 7);
        assert!(set_path(&mut x, &path(&["storage", "disks", "0", "nope"]), Value::UInt(7)).unwrap_err().contains("no key nope"));
        assert!(set_path(&mut x, &path(&["storage", "disks", "9", "size_bytes"]), Value::UInt(7)).unwrap_err().contains("no element 9"));
    }

    #[test]
    fn free_runs_and_compaction() {
        let mut used = vec![(0, 10), (20, 5)];
        assert_eq!(free_runs(30, &mut used).unwrap(), vec![(10, 10), (25, 5)]);
        let mut over = vec![(0, 10), (5, 5)];
        assert!(free_runs(30, &mut over).is_err());
        let mut past = vec![(0, 40)];
        assert!(free_runs(30, &mut past).is_err());
        let free = vec![(10, 10), (25, 5)];
        assert_eq!(take_from_top(&free, 3), vec![(27, 3)]);
        assert_eq!(take_from_top(&free, 8), vec![(17, 3), (25, 5)]);
        assert_eq!(below(&[(17, 3), (25, 5)], 20), 3);
        assert_eq!(below(&[(17, 3), (25, 5)], 30), 8);
        assert_eq!(below(&[(17, 3), (25, 5)], 0), 0);
    }

    /// Every scenario's dump and every fixture here, fitted to a target of
    /// each size: whatever comes back keeps the table sound.
    #[test]
    fn every_layout_keeps_the_table_sound() {
        let mut dumps: Vec<(String, Value)> = crate::scenarios::dumps().into_iter().map(|(n, v)| (n.to_string(), v)).collect();
        let root = 20 * G;
        let total = 2048 + root + 34;
        dumps.push(("fixture-gpt".into(), boot_swap_root().build()));
        dumps.push(("fixture-lvm".into(), lvm_disk(512).build()));
        dumps.push(("fixture-lvm-lv".into(), lvm_disk(0).build()));
        dumps.push((
            "fixture-mbr".into(),
            D::mbr(total)
                .part(1, 2048, G, &ext4_on(G, 512 * MIB), "")
                .part(2, 2048 + G, root - 3 * G, &xfs((root - 3 * G) / 8, (root - 3 * G) / 32), "")
                .part(3, 2048 + root - 2 * G, 2 * G, &swap(), "")
                .mount("vda2", (root - 3 * G) * 512, GIB)
                .build(),
        ));
        dumps.push((
            "fixture-luks".into(),
            D::gpt(total)
                .part(1, 2048, root, &luks(16 * MIB), "")
                .dm(crypt_dm("dm-0", "vda1", root * 512 - 16 * MIB, &ext4((root * 512 - 16 * MIB) / 4096, Some(GIB / 4096))))
                .mount("dm-0", root * 512 - 16 * MIB, 2 * GIB)
                .build(),
        ));
        let mut fits = 0;
        for (name, v) in dumps {
            for disk in v.at(&["storage", "disks"]).and_then(Value::as_array).cloned().unwrap_or_default() {
                let Some(size) = u(&disk, "size_bytes").filter(|&s| s > 4 * MIB) else { continue };
                let dn = s(&disk, "name").unwrap_or_default();
                let sector = u(&disk, "logical_sector_size").unwrap_or(512).max(512);
                for delta in [-(MIB as i64), -4 * MIB as i64, -(GIB as i64), MIB as i64, GIB as i64] {
                    if size as i64 + delta <= 4 * MIB as i64 {
                        continue;
                    }
                    let want = (size as i64 + delta) as u64;
                    let t = vec![Target { disk: dn.clone(), path: "/dev/sdz".into(), size_bytes: want }];
                    for pct in [1, 50] {
                        let f = fit(&v, &t, pct);
                        let Some(out) = &f.v else { continue };
                        fits += 1;
                        check_sound(&format!("{name} {dn} {delta} {pct}%"), &v, out, &dn, sector, want);
                    }
                }
            }
        }
        assert!(fits > 20, "only {fits} of the layouts were fitted at all");
    }

    /// The fitted disk holds together: inside the disk, no overlaps, whole
    /// MiB moves, one partition resized, every identifier the source's.
    fn check_sound(what: &str, v: &Value, out: &Value, name: &str, sector: u64, size: u64) {
        let find = |x: &Value| {
            x.at(&["storage", "disks"])
                .and_then(Value::as_array)
                .and_then(|a| a.iter().find(|d| s(d, "name").as_deref() == Some(name)))
                .cloned()
                .unwrap()
        };
        let (was, now) = (find(v), find(out));
        assert_eq!(u(&now, "size_bytes"), Some(size - size % sector), "{what}: the disk's size");
        let table = now.at(&["partition_table"]).cloned().unwrap_or(Value::Null);
        let gpt = s(&table, "type").as_deref() == Some("gpt");
        let total = (size - size % sector) / sector;
        let tail = if gpt { 2 + (u(&table, "entry_count").unwrap_or(128) * u(&table, "entry_size").unwrap_or(128)).div_ceil(sector) } else { 1 };
        let last = total - tail;
        if gpt {
            assert_eq!(u(&table, "last_usable_lba"), Some(last), "{what}: the last usable LBA");
            assert_eq!(u(&table, "backup_lba"), Some(total - 1), "{what}: the backup header");
        }
        let parts = |d: &Value| d.get("partitions").and_then(Value::as_array).cloned().unwrap_or_default();
        let (pw, pn) = (parts(&was), parts(&now));
        assert_eq!(pw.len(), pn.len(), "{what}: as many partitions");
        let mut resized = 0;
        let mut spans: Vec<(u64, u64, bool)> = Vec::new();
        for (a, b) in pw.iter().zip(pn.iter()) {
            let (s0, n0) = (u(a, "start_lba").unwrap(), u(a, "sectors").unwrap());
            let (s1, n1) = (u(b, "start_lba").unwrap(), u(b, "sectors").unwrap());
            assert_eq!(u(a, "number"), u(b, "number"), "{what}: the numbers");
            // Identifiers and everything else about it stay the source's.
            for k in ["uuid", "type", "partlabel", "attributes", "bootable", "name"] {
                assert_eq!(a.get(k), b.get(k), "{what}: {k} of partition {:?}", u(a, "number"));
            }
            if n0 != n1 {
                resized += 1;
                assert_eq!(s0, s1, "{what}: a resized partition keeps its start");
                assert_eq!((n1 as i64 - n0 as i64).unsigned_abs() * sector % MIB, 0, "{what}: whole MiB");
            }
            if s0 != s1 {
                assert_eq!(n0, n1, "{what}: a moved partition keeps its size");
                assert_eq!((s1 as i64 - s0 as i64).unsigned_abs() * sector % MIB, 0, "{what}: whole MiB");
            }
            assert!(s1 + n1 - 1 <= last, "{what}: partition {:?} ends past {last}", u(a, "number"));
            if b.get("end_lba").is_some() {
                assert_eq!(u(b, "end_lba"), Some(s1 + n1 - 1), "{what}: end_lba");
            }
            if b.get("size_bytes").is_some() {
                assert_eq!(u(b, "size_bytes"), Some(n1 * sector), "{what}: size_bytes");
            }
            let inside = b.get("logical").and_then(Value::as_bool) == Some(true);
            spans.push((s1, n1, inside));
        }
        assert!(resized <= 1, "{what}: {resized} partitions changed size");
        // Primaries don't overlap each other; logical partitions lie inside
        // their extended one, so they're only held apart from each other.
        for pass in [false, true] {
            let mut these: Vec<(u64, u64)> = spans.iter().filter(|x| x.2 == pass).map(|x| (x.0, x.1)).collect();
            these.sort();
            for w in these.windows(2) {
                assert!(w[0].0 + w[0].1 <= w[1].0, "{what}: {:?} and {:?} overlap", w[0], w[1]);
            }
        }
        // The table's own identity, and every filesystem's, are untouched.
        for k in ["disk_guid", "disk_signature", "first_usable_lba", "entry_count", "entry_size"] {
            assert_eq!(was.at(&["partition_table", k]), now.at(&["partition_table", k]), "{what}: {k}");
        }
        assert_eq!(uuids(v), uuids(out), "{what}: the filesystem and container identifiers");
    }

    /// Every identifier anywhere in the dump.
    fn uuids(v: &Value) -> Vec<String> {
        let mut out = Vec::new();
        walk(v, &mut out);
        out.sort();
        out
    }

    fn walk(v: &Value, out: &mut Vec<String>) {
        match v {
            Value::Obj(kv) => {
                for (k, x) in kv {
                    if k.contains("uuid") || k.contains("label") || k == "id" || k == "name" {
                        out.push(format!("{k}={}", x.compact()));
                    }
                    walk(x, out);
                }
            }
            Value::Arr(a) => a.iter().for_each(|x| walk(x, out)),
            _ => {}
        }
    }
}
