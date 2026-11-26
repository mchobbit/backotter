//! `bko-holt load` for the systems in other/: the target disks as the
//! system's own tools see them, and the stages that differ from Linux's.
//! ZFS (pools, datasets, mounts) and the archiver's restore are load's own
//! (commands.rs), with this system's device names.

use super::{Family, Found, RawDisk, freebsd, open, read_disk};
use crate::blockdev::{BlockDev, Kind};
use crate::json::Value;
use crate::load::commands::{Builder, Cmd};
use crate::load::layout::{Disk, Layout, TableKind};
use crate::load::target::{self, System, TargetDisk};
use crate::load::{Options, RUN_DIR, shell_quote as q};
use crate::mounts::{self, Mount};
use crate::sys::Sys;

/// The node of partition `n` of target disk `t` (a source partition of `d`).
/// NetBSD names a GPT's partitions as wedges, dkN in the order it finds
/// them: they're named by their wedge name (NAME=), which mount and newfs
/// take: wedge_names.  An MBR partition that holds a disklabel has no node
/// of its own.
pub fn part_node(fam: Family, d: &Disk, t: &str, n: u32) -> String {
    match fam {
        Family::FreeBsd | Family::DragonFly => match d.table.as_ref().map(|t| t.kind) {
            Some(TableKind::Mbr) => format!("/dev/{t}s{n}"),
            _ => format!("/dev/{t}p{n}"),
        },
        Family::Haiku => super::haiku::part_path(t, n),
        Family::Redox | Family::Genode => crate::load::commands::part_path(t, n),
        Family::Illumos => format!("/dev/dsk/{}", super::illumos::slice_name(t, n)),
        Family::NetBsd => match (
            d.table.as_ref().map(|t| t.kind),
            wedge_names(d)
                .into_iter()
                .find(|w| w.part.number == n)
                .and_then(|w| w.name),
        ) {
            (Some(TableKind::Gpt), Some(w)) => format!("NAME={w}"),
            _ => format!("/dev/{t}mbr{n}"),
        },
        _ => format!("/dev/{t}{n}"),
    }
}

/// A GPT entry and the name NetBSD gives its wedge.
struct Wedge<'a> {
    part: &'a crate::load::layout::Part,
    /// Its GPT name, or its GUID when it has none.
    first: Option<String>,
    /// The name it gets; none when it gets no wedge.
    name: Option<String>,
}

/// The names NetBSD gives a GPT disk's wedges (sys/dev/dkwedge/
/// dkwedge_gpt.c), in table order: an entry's GPT name, or, for an entry
/// with none, its unique GUID as uuid_snprintf prints it (lower case).
/// GPT names needn't be unique, and wedge names must: when an earlier
/// entry of the disk has the name, dk.c's dkwedge_add refuses it (EEXIST)
/// and dkwedge_gpt.c names the wedge by its GUID instead, or makes none
/// when that is taken too (or the dump has no GUID for it).  Another
/// disk's wedges are wedge_clashes'.
fn wedge_names(d: &Disk) -> Vec<Wedge<'_>> {
    let mut parts: Vec<&crate::load::layout::Part> = d.parts.iter().collect();
    parts.sort_by_key(|p| p.number);
    let mut out: Vec<Wedge<'_>> = Vec::new();
    for part in parts {
        let guid = part
            .uuid
            .as_ref()
            .filter(|u| !u.is_empty())
            .map(|u| u.to_ascii_lowercase());
        let first = part.label.clone().or_else(|| guid.clone());
        let taken = |n: &str| out.iter().any(|w| w.name.as_deref() == Some(n));
        let name = match &first {
            Some(n) if taken(n) => guid.filter(|g| g != n && !taken(g)),
            n => n.clone(),
        };
        out.push(Wedge { part, first, name });
    }
    out
}

/// NetBSD's wedge names are the whole system's, not a disk's: dk.c's
/// dkwedge_add refuses (EEXIST) a name any wedge of any disk has, and
/// dkwedge_gpt.c then names the new wedge by its GUID.  So a wedge of a
/// disk that isn't a target (the source's own disk, still attached) with
/// the name a target's partition is to have would take its place: load's
/// newfs and mount of NAME=that would write to that other disk, and status
/// and readback (node_path) read it.  One problem per such wedge.  So is
/// a GPT name two targets' partitions both have: which gets it, and which
/// its GUID, is the order the disks' wedges are made in (and the old
/// wedges a target still has then), which load can't know when it names
/// the nodes; and so is a partition that gets no wedge at all (a
/// duplicate GPT name and no GUID).  Nothing for a layout that isn't
/// NetBSD's.  `pairs` are the mapping's (source disk, target disk).
pub fn wedge_clashes(
    layout: &Layout,
    pairs: &[(String, String)],
    sys: &Sys,
) -> Vec<crate::load::codes::Problem> {
    use crate::load::codes::{Problem, ProblemCode};
    if Family::of_layout(&layout.v) != Some(Family::NetBsd) {
        return Vec::new();
    }
    // Each target's partitions by the wedge names they're to have.
    let mut wanted: Vec<(String, String, String, String)> = Vec::new();
    let mut out = Vec::new();
    for (src, tgt) in pairs {
        let Some(d) = layout
            .disk(src)
            .filter(|d| d.table.as_ref().map(|t| t.kind) == Some(TableKind::Gpt))
        else {
            continue;
        };
        let wedges = wedge_names(d);
        for w in &wedges {
            let part = &w.part.name;
            match (&w.name, &w.first) {
                (Some(name), _) => {
                    if let Some((_, otgt, opart, _)) =
                        wanted.iter().find(|o| o.3 == *name && o.1 != *tgt)
                    {
                        let text = format!(
                            "{src} -> /dev/{tgt}: {part}'s wedge is to be named {name}, as {opart}'s on /dev/{otgt} is: NetBSD's wedge names are the whole system's, and which of the two gets the name (and which its GUID) is the order their wedges are made in, so newfs and mount of NAME={name} could write to the other one; give one of them another GPT name (gpt label) on the source, and dump it again, first"
                        );
                        out.push(
                            Problem::new(ProblemCode::WedgeClash, text)
                                .with("disk", src)
                                .with("target", tgt),
                        );
                    }
                    wanted.push((src.clone(), tgt.clone(), part.clone(), name.clone()));
                }
                (None, Some(first)) => {
                    let other = wedges
                        .iter()
                        .find(|o| o.name.as_deref() == Some(first.as_str()))
                        .map_or("another entry", |o| o.part.name.as_str());
                    let why = if w.part.uuid.as_ref().is_some_and(|u| !u.is_empty()) {
                        "its GUID is another's wedge name too"
                    } else {
                        "it has no GUID"
                    };
                    let text = format!(
                        "{src} -> /dev/{tgt}: {part}'s GPT name is {first}, as {other}'s is, and {why}: NetBSD would make no wedge for it, so there'd be nothing for newfs and mount to write it to; give it another GPT name (gpt label) on the source, and dump it again, first"
                    );
                    out.push(
                        Problem::new(ProblemCode::WedgeClash, text)
                            .with("disk", src)
                            .with("target", tgt),
                    );
                }
                (None, None) => {}
            }
        }
    }
    if wanted.is_empty() {
        return out;
    }
    // Every disk but the wedges themselves (cgd, raid and vnd disks have
    // wedges too), and but the targets.
    let names = sys
        .run("sysctl", &["-n", "hw.disknames"])
        .unwrap_or_default();
    let others = names
        .split(|c: char| c == ',' || c.is_whitespace())
        .map(|w| w.split(':').next().unwrap_or(w))
        .filter(|n| {
            !n.is_empty() && !(n.starts_with("dk") && n[2..].chars().all(|c| c.is_ascii_digit()))
        })
        .filter(|n| !pairs.iter().any(|(_, t)| t == n));
    for disk in others {
        for (dk, name, _) in super::labelled::parse_wedges(
            &sys.run("dkctl", &[disk, "listwedges"]).unwrap_or_default(),
        ) {
            for (src, tgt, part, _) in wanted.iter().filter(|w| w.3 == name) {
                let text = format!(
                    "{src} -> /dev/{tgt}: {part}'s wedge is to be named {name}, and {disk}, which isn't a target, has a wedge of that name ({dk}): NetBSD's wedge names are the whole system's, so newfs and mount of NAME={name} would write to {disk}; disconnect {disk}, or give its partition another GPT name (gpt label), first"
                );
                out.push(
                    Problem::new(ProblemCode::WedgeClash, text)
                        .with("disk", src)
                        .with("target", tgt),
                );
            }
        }
    }
    out
}

/// The node of the disklabel partition `src` of source disk `d` on target
/// disk `t` (NetBSD, OpenBSD: /dev/ld1a, /dev/sd1a).
pub fn label_node(layout: &Layout, d: &Disk, t: &str, src: &str) -> Option<String> {
    let v = layout.device(&d.name)?;
    let p = v
        .get("label_partitions")?
        .as_array()?
        .iter()
        .find(|p| s(p, "name").as_deref() == Some(src))?;
    // DragonFly's disklabel64 is a slice's: vbd1s1d.
    match p.get("slice").and_then(Value::as_u64) {
        Some(n) => Some(format!("/dev/{t}s{n}{}", s(p, "letter")?)),
        None => Some(format!("/dev/{t}{}", s(p, "letter")?)),
    }
}

/// A disk's size here (NetBSD, OpenBSD): disklabel's total sectors.
fn labelled_size(sys: &Sys, disk: &str) -> Option<(u64, u64)> {
    let l = super::bsdlabel::parse(&sys.run_any("disklabel", &[disk])?);
    (l.total_sectors > 0).then_some((
        l.total_sectors * l.bytes_per_sector.max(512),
        l.bytes_per_sector.max(512),
    ))
}

fn s(v: &Value, k: &str) -> Option<String> {
    v.get(k).and_then(Value::as_str).map(str::to_string)
}

/// The rescue stick's config partition's FAT label (stick-config.md), as
/// load/target.rs has it for Linux: a disk with it is the stick.
const STICK_CONFIG_LABEL: &str = "BACKOTTER";

/// A DragonFly slice's disklabel64 name (its d_packname), read off the
/// slice: what devfs shows as /dev/part-by-label/NAME.LETTER.  The label
/// is in the slice's first sectors (the second, as disklabel64 writes it).
fn label64_name(slice: &dyn crate::bytes::ByteSource) -> Option<String> {
    const DISKMAGIC64: u32 = 0xc446_4c59;
    (0..16u64).find_map(|i| {
        let b = slice.read_exact_at(i * 512, 512)?;
        (crate::bytes::le32(&b, 0) == DISKMAGIC64)
            .then(|| crate::bytes::label(crate::bytes::sub(&b, 72, 64)))?
    })
}

/// A partition's MBR type ("0xa5"), when it is an MBR's.
fn mbr_type(f: &Found) -> Option<&str> {
    f.field("type")
}

/// The whole disks here, as the system lists them (no table read yet):
/// scan's, and `bko-holt stick`'s (stick.rs).
pub fn raw_disks(sys: &Sys, fam: Family) -> Vec<RawDisk> {
    match fam {
        Family::FreeBsd => freebsd::disks(sys),
        Family::DragonFly => super::dfly::disks(sys),
        Family::Haiku => super::haiku::disks(sys),
        Family::Illumos => super::illumos::disks(sys),
        // Rebuilt from Linux: scan is Linux's (target.rs).
        Family::Redox | Family::Genode => Vec::new(),
        Family::NetBsd | Family::OpenBsd => super::labelled::disk_names(
            fam,
            &sys.run("sysctl", &["-n", "hw.disknames"])
                .unwrap_or_default(),
        )
        .into_iter()
        .filter_map(|(n, duid)| {
            let (size, sector) = labelled_size(sys, &n)?;
            Some(RawDisk {
                path: format!("/dev/r{n}{}", super::labelled::raw_letter(fam)),
                name: n,
                size,
                sector,
                model: None,
                serial: duid,
            })
        })
        .collect(),
    }
}

/// The disks here, read as `dump` reads them, for load's checks.
pub fn scan(sys: &Sys, fam: Family, opts: &Options, ours: &[String]) -> System {
    let raw = raw_disks(sys, fam);
    let mount_list: Vec<mounts::FstabEntry> = match fam {
        Family::Illumos => {
            super::illumos::parse_mnttab(&sys.read("/etc/mnttab").unwrap_or_default())
                .into_iter()
                .map(|(spec, file, vfstype, options)| mounts::FstabEntry {
                    spec,
                    file,
                    vfstype,
                    options,
                    freq: 0,
                    passno: 0,
                })
                .collect()
        }
        Family::Haiku => super::haiku::parse_df(&sys.run("df", &[]).unwrap_or_default())
            .into_iter()
            .map(|(file, vfstype, spec)| mounts::FstabEntry {
                spec,
                file,
                vfstype,
                options: String::new(),
                freq: 0,
                passno: 0,
            })
            .collect(),
        Family::NetBsd | Family::OpenBsd => sys
            .run("mount", &[])
            .unwrap_or_default()
            .lines()
            .filter_map(super::bsdlabel::parse_mount_line)
            .map(|(spec, file, vfstype, options)| mounts::FstabEntry {
                spec,
                file,
                vfstype,
                options,
                freq: 0,
                passno: 0,
            })
            .collect(),
        _ => sys
            .run("mount", &["-p"])
            .map(|t| mounts::parse_fstab(&t))
            .unwrap_or_default(),
    };
    let mounts: Vec<Mount> = mount_list
        .iter()
        .map(|m| Mount {
            target: m.file.clone(),
            source: m.spec.clone(),
            fstype: m.vfstype.clone(),
            options: m.options.clone(),
            ..Mount::default()
        })
        .collect();
    let swaps: Vec<String> = sys
        .run("swapctl", &["-l"])
        .unwrap_or_default()
        .lines()
        .skip(1)
        .filter_map(|l| l.split_whitespace().next().map(str::to_string))
        .collect();
    let pools = target::imported_pools(sys);
    let mut seen: Vec<(RawDisk, Vec<Found>, Vec<(String, String)>)> = Vec::new();
    let mut tables = Vec::new();
    let mut whole = Vec::new();
    // What a mount's device may name inside a slice, beyond the disk's own
    // partitions: FreeBSD's bsdlabel partitions (vtbd0s2a), for resolve.
    let mut inner: Vec<Vec<Found>> = Vec::new();
    for d in raw {
        let (table, found, labels, w, x) = match open(sys, &d) {
            Some(src) => {
                let name = d.name.clone();
                let (t, f) = read_disk(&src, &d, &|n, t| match fam {
                    Family::FreeBsd => freebsd::part_name(&name, n, t),
                    Family::DragonFly => format!("{name}s{n}"),
                    Family::Haiku => super::haiku::part_name(&name, n),
                    Family::Illumos => super::illumos::slice_name(&name, n),
                    _ => format!("{name}:{n}"),
                });
                let part = |p: &Found| crate::bytes::OffsetSource {
                    inner: &src,
                    start: p.start,
                    len: p.size,
                };
                let mut labels: Vec<(String, String)> = f
                    .iter()
                    .filter_map(|p| super::glabel(&part(p)).map(|l| (p.name.clone(), l)))
                    .collect();
                let mut x = Vec::new();
                for p in &f {
                    match (fam, mbr_type(p)) {
                        // FreeBSD's rescue image: / is the "a" of slice 2's bsdlabel.
                        (Family::FreeBsd, Some("0xa5")) => {
                            for (letter, start, len) in freebsd::bsdlabel_parts(&part(p)) {
                                let contents =
                                    crate::probe::probe_all(&crate::bytes::OffsetSource {
                                        inner: &src,
                                        start: p.start + start,
                                        len,
                                    });
                                x.push(Found {
                                    name: format!("{}{letter}", p.name),
                                    number: p.number,
                                    start: p.start + start,
                                    size: len,
                                    json: crate::json::Obj::new(),
                                    contents,
                                });
                            }
                        }
                        // DragonFly's: / is part-by-label/NAME.a, NAME slice 2's disklabel64's.
                        (Family::DragonFly, Some("0x6c")) => {
                            labels.extend(label64_name(&part(p)).map(|l| (p.name.clone(), l)))
                        }
                        _ => {}
                    }
                }
                let w = if t.is_none() {
                    crate::probe::probe_all(&src)
                } else {
                    Vec::new()
                };
                (t, f, labels, w, x)
            }
            None => (None, Vec::new(), Vec::new(), Vec::new(), Vec::new()),
        };
        tables.push(table);
        whole.push(w);
        inner.push(x);
        seen.push((d, found, labels));
    }
    let rseen: Vec<(RawDisk, Vec<Found>, Vec<(String, String)>)> = seen
        .iter()
        .zip(&inner)
        .map(|((d, f, l), x)| (d.clone(), f.iter().chain(x).cloned().collect(), l.clone()))
        .collect();
    let mut disks = Vec::new();
    for (i, (d, found, _)) in seen.iter().enumerate() {
        let b = BlockDev {
            name: d.name.clone(),
            kind: Kind::Disk,
            size_bytes: d.size,
            logical_block_size: d.sector,
            physical_block_size: d.sector,
            model: d.model.clone(),
            serial: d.serial.clone(),
            ..BlockDev::default()
        };
        let parts: Vec<BlockDev> = found
            .iter()
            .map(|f| BlockDev {
                name: f.name.clone(),
                kind: Kind::Partition,
                size_bytes: f.size,
                parent: Some(d.name.clone()),
                partition_number: Some(f.number),
                start_sectors: Some(f.start / 512),
                ..BlockDev::default()
            })
            .collect();
        let mut signatures = Vec::new();
        let mut uuids = Vec::new();
        let named = found
            .iter()
            .map(|f| (f.name.clone(), &f.contents))
            .chain(std::iter::once((d.name.clone(), &whole[i])));
        for (n, cs) in named {
            for c in cs.iter() {
                signatures.push(format!(
                    "{n}: {}{}{}",
                    c.kind,
                    c.uuid
                        .as_ref()
                        .map(|u| format!(" UUID={u}"))
                        .unwrap_or_default(),
                    c.label
                        .as_ref()
                        .map(|l| format!(" LABEL={l}"))
                        .unwrap_or_default()
                ));
                if let Some(u) = &c.uuid {
                    uuids.push(u.to_ascii_lowercase());
                }
            }
        }
        // Bug: NetBSD GPT partitions attach as dkN wedges not prefixed with the
        // parent disk name, and scan() never ran dkctl listwedges. Consequently
        // mine() missed every GPT wedge, leaving holds_root false and in_use
        // empty on NetBSD disks with mounted GPT filesystems, ZFS vdevs, or swap.
        let wedges: Vec<String> = if fam == Family::NetBsd {
            super::labelled::parse_wedges(
                &sys.run("dkctl", &[&d.name, "listwedges"])
                    .unwrap_or_default(),
            )
            .into_iter()
            .map(|(dk, _, _)| dk)
            .collect()
        } else {
            Vec::new()
        };
        let mine = |n: &str| {
            n == d.name
                || found.iter().any(|f| f.name == n)
                || wedges.iter().any(|w| w == n)
                || (n.starts_with(&d.name)
                    && n[d.name.len()..].starts_with(|c: char| !c.is_ascii_digit()))
        };
        let mut in_use = Vec::new();
        let mut holds_root = false;
        for m in mount_list
            .iter()
            .map(|m| {
                // DragonFly's HAMMER2 PFS (vbd0s1d@ROOT) is its device's; Haiku
                // boots from the volume at /boot.
                let mut m = m.clone();
                if let Some((dev, _)) = m.spec.split_once('@') {
                    m.spec = dev.to_string();
                }
                if fam == Family::Haiku && m.file == "/boot" {
                    m.file = "/".into();
                }
                m
            })
            .collect::<Vec<_>>()
            .iter()
        {
            let haiku = (fam == Family::Haiku)
                .then(|| {
                    m.spec
                        .rsplit_once('/')
                        .map(|(dir, i)| format!("{}_{i}", super::haiku::name_of(dir)))
                })
                .flatten();
            let Some(dev) = haiku
                .or_else(|| freebsd::resolve(&m.spec, &rseen))
                .or_else(|| {
                    m.spec
                        .strip_prefix("/dev/dsk/")
                        .or_else(|| m.spec.strip_prefix("/dev/"))
                        .map(str::to_string)
                })
                .or_else(|| {
                    (!m.spec.contains('/') && m.spec.starts_with(&d.name)).then(|| m.spec.clone())
                })
            else {
                continue;
            };
            if !mine(&dev) {
                continue;
            }
            if m.file == "/" {
                holds_root = true;
            }
            if !(m.file == opts.target || m.file.starts_with(&format!("{}/", opts.target))) {
                in_use.push(format!("{dev} is mounted at {}", m.file));
            }
        }
        for sw in &swaps {
            if let Some(dev) = freebsd::resolve(sw, &rseen)
                .or_else(|| sw.strip_prefix("/dev/").map(str::to_string))
                .filter(|x| mine(x))
            {
                in_use.push(format!("{dev} is in use as swap"));
            }
        }
        for p in pools.iter().filter(|p| p.devices.iter().any(|x| mine(x))) {
            if !p.is_ours(ours, &opts.target) {
                in_use.push(format!(
                    "{} is a vdev of the imported pool {}",
                    d.name, p.name
                ));
            }
            if mount_list.iter().any(|m| {
                m.file == "/"
                    && m.vfstype == "zfs"
                    && m.spec.split('/').next() == Some(p.name.as_str())
            }) {
                holds_root = true;
            }
        }
        // The stick: its config partition's label, on every system (illumos'
        // root is a RAM disk, so nothing mounted names the stick there).
        let boot_medium = found
            .iter()
            .chain(&inner[i])
            .flat_map(|f| f.contents.iter())
            .chain(whole[i].iter())
            .any(|c| c.label.as_deref() == Some(STICK_CONFIG_LABEL));
        // Nothing here is let go of with --erase (release.rs is Linux's).
        let hard_use = in_use.clone();
        disks.push(TargetDisk {
            b,
            parts,
            table: tables[i].clone(),
            signatures,
            uuids,
            in_use,
            hard_use,
            boot_medium,
            holds_root,
        });
    }
    System {
        disks,
        mounts,
        pools,
        pvs: vec![],
        devs: vec![],
        swaps: vec![],
    }
}

/// The sector size the device `src`'s filesystem goes on counts in, `rec`
/// being that device's record (`Layout::device`): a GELI provider's own
/// (`geli init -s`, kept in its record as `sectorsize`), else the logical
/// sector size of the disk whose partition it is.  512 where the dump
/// doesn't say (a disklabel partition), which leaves the source's own
/// numbers as they are.
pub(super) fn newfs_sector(layout: &Layout, src: &str, rec: &Value) -> u64 {
    if let Some(n) = rec.get("sectorsize").and_then(Value::as_u64) {
        return n.max(512);
    }
    layout
        .disks
        .iter()
        .find(|d| d.name == src || d.parts.iter().any(|p| p.name == src))
        .map_or(512, |d| d.sector_size.max(512))
}

/// Validates a `dumpfs -m` / `labelled` `newfs` command from a layout JSON:
/// must invoke `newfs` with known UFS/FFS option flags and end with a single
/// `DEVICE` placeholder, never an empty command, another binary, `-N`, or an
/// extra positional device.
fn valid_newfs_words(raw: &[&str]) -> bool {
    if raw.len() < 2 || raw[0] != "newfs" || raw.last() != Some(&"DEVICE") {
        return false;
    }
    let args = &raw[1..raw.len() - 1];
    let mut i = 0;
    while i < args.len() {
        let w = args[i];
        if w == "DEVICE" || w.contains('\0') {
            return false;
        }
        match w {
            "-E" | "-J" | "-j" | "-l" | "-t" | "-U" | "-X" => i += 1,
            "-n" => {
                if args.get(i + 1).is_some_and(|v| v.parse::<u64>().is_ok()) {
                    i += 2;
                } else {
                    i += 1;
                }
            }
            "-O" => {
                if !args
                    .get(i + 1)
                    .is_some_and(|v| matches!(*v, "0" | "1" | "2" | "2ea"))
                {
                    return false;
                }
                i += 2;
            }
            "-o" => {
                if !args
                    .get(i + 1)
                    .is_some_and(|v| matches!(*v, "time" | "space"))
                {
                    return false;
                }
                i += 2;
            }
            "-L" => {
                if !args
                    .get(i + 1)
                    .is_some_and(|v| !v.is_empty() && *v != "DEVICE" && !v.contains('\0'))
                {
                    return false;
                }
                i += 2;
            }
            "-a" | "-b" | "-c" | "-d" | "-e" | "-f" | "-g" | "-h" | "-i" | "-k" | "-m" | "-p"
            | "-r" | "-s" | "-S" | "-v" => {
                if args.get(i + 1).is_none_or(|v| v.parse::<u64>().is_err()) {
                    return false;
                }
                i += 2;
            }
            _ => return false,
        }
    }
    true
}

/// `dumpfs -m`'s `-s`, which is in DEV_BSIZE (512-byte) units, made the
/// count of `sector`-byte sectors FreeBSD's `newfs` reads it as (newfs asks
/// the device for its sector size and counts in those).  On a device with
/// larger sectors -- a GELI provider made with `geli init -s 4096`, a 4Kn
/// disk -- the source's number names the same bytes but `sector`/512 times
/// too many sectors, and newfs refuses it ("maximum file system size is
/// ...").  A size that isn't a whole number of sectors is rounded down:
/// newfs must not be given more than the device holds.  `words` is the
/// command, changed in place; the answer is what to say in the Cmd's `why`,
/// and None where there was nothing to change.
fn newfs_size_in_sectors(words: &mut [String], sector: u64) -> Option<String> {
    if sector <= 512 {
        return None;
    }
    let i = words.iter().position(|w| w == "-s")?;
    let blocks: u64 = words.get(i + 1)?.parse().ok()?;
    let per = sector / 512;
    let sectors = blocks / per;
    words[i + 1] = sectors.to_string();
    let mut why = format!(
        "dumpfs's -s {blocks} is in 512-byte units, and newfs counts this device's {sector}-byte sectors: -s {sectors}"
    );
    if blocks % per != 0 {
        why += &format!(
            ", rounded down ({blocks} of them isn't a whole number of {sector}-byte sectors, and newfs must not be given more than the device holds)"
        );
    }
    Some(why)
}

impl Builder<'_> {
    /// The table, written by bko-holt itself from the dump (GUIDs, names
    /// and attributes exactly), with the boot code the source's MBR and
    /// boot partitions held; then GEOM labels.
    pub(crate) fn other_partition(&mut self, fam: Family) -> Vec<Cmd> {
        let exe = std::env::current_exe()
            .map(|p| p.to_string_lossy().into_owned())
            .unwrap_or_else(|_| "bko-holt".into());
        let mut out = Vec::new();
        for (src, tgt) in self.names.mapping.pairs.clone() {
            let Some(d) = self.layout.disk(&src).cloned() else {
                continue;
            };
            let dev = format!("/dev/{tgt}");
            // The words the sh -c scripts below take, quoted (a word with
            // nothing to quote stays bare).
            let (qtgt, qdev) = (q(&tgt), q(&dev));
            if d.table.is_none() {
                out.push(Cmd::note(format!(
                    "{src} has no partition table: its content goes on {dev} itself"
                )));
                continue;
            }
            if self.opts.erase.iter().any(|e| e == &dev || e == &tgt) || fam.on_linux() {
                match fam {
                    Family::Redox | Family::Genode => out.push(Cmd::new(&["wipefs", "--all", &dev]).why(format!("{src} -> {dev}: clear the old table and signatures"))),
                    Family::FreeBsd => out.push(Cmd::new(&["sh", "-c", &format!("gpart destroy -F {qtgt} 2>/dev/null; dd if=/dev/zero of={qdev} bs=1m count=1 2>/dev/null; true")]).why(format!("--erase {dev}: its old table"))),
                    Family::DragonFly => out.push(Cmd::new(&["sh", "-c", &format!("dd if=/dev/zero of={qdev} bs=1m count=1 2>/dev/null; true")]).why(format!("--erase {dev}: its old table"))),
                    Family::Illumos => out.push(Cmd::new(&["sh", "-c", &format!("dd if=/dev/zero of={} bs=1048576 count=1 2>/dev/null; true", q(&format!("/dev/rdsk/{tgt}p0")))]).why(format!("--erase {tgt}: its old label"))),
                    Family::Haiku => {
                        let raw = format!("{}/raw", super::haiku::dir_of(&tgt));
                        out.push(Cmd::new(&["sh", "-c", &format!("dd if=/dev/zero of={} bs=1048576 count=1 2>/dev/null; true", q(&raw))]).why(format!("--erase {raw}: its old table")));
                    }
                    Family::NetBsd | Family::OpenBsd => {
                        let raw = format!("/dev/r{tgt}{}", super::labelled::raw_letter(fam));
                        if fam == Family::NetBsd {
                            out.push(Cmd::new(&["sh", "-c", &format!("dkctl {qtgt} listwedges 2>/dev/null | sed -n 's/^\\(dk[0-9]*\\):.*/\\1/p' | while read -r w; do dkctl {qtgt} delwedge $w; done; true")]).why(format!("--erase {dev}: its wedges")));
                        }
                        out.push(Cmd::new(&["sh", "-c", &format!("dd if=/dev/zero of={} bs=1m count=1 2>/dev/null; true", q(&raw))]).why(format!("--erase {dev}: its old table and label")));
                    }
                }
            }
            let (node, size) = match fam {
                Family::Redox | Family::Genode => {
                    (dev.clone(), format!("$(blockdev --getsize64 {qdev})"))
                }
                Family::Haiku => (
                    format!("{}/raw", super::haiku::dir_of(&tgt)),
                    "auto".to_string(),
                ),
                Family::Illumos => (format!("/dev/rdsk/{tgt}p0"), "auto".to_string()),
                Family::DragonFly => (
                    dev.clone(),
                    format!(
                        "$(printf %d 0x$(diskinfo {qdev} | sed -n 's/.*size=0x\\([0-9a-fA-F]*\\).*/\\1/p'))"
                    ),
                ),
                Family::NetBsd | Family::OpenBsd => {
                    let raw = format!("/dev/r{tgt}{}", super::labelled::raw_letter(fam));
                    let size = format!(
                        "$(disklabel {qtgt} | awk '/^bytes.sector:/ {{ b = $2 }} /^total sectors:/ {{ t = $3 }} END {{ print b * t }}')"
                    );
                    (raw, size)
                }
                _ => (dev.clone(), format!("$(diskinfo {qdev} | cut -f3)")),
            };
            out.push(
                Cmd::new(&[
                    "sh",
                    "-c",
                    &format!(
                        "{} write-table {} {} {} {size}",
                        q(&exe),
                        q(&self.opts.layout),
                        q(&src),
                        q(&node)
                    ),
                ])
                .why(format!(
                    "{src} -> {dev}: the source's table, GUIDs and boot code exactly"
                )),
            );
            match fam {
                Family::Haiku => out.push(Cmd::note("Haiku publishes a new table's partitions only at boot: restart this system before the format stage".to_string())),
                Family::Redox | Family::Genode => {
                    out.push(Cmd::new(&["partx", "-u", &dev]).why("the kernel reads the new table"));
                    out.push(Cmd::new(&["udevadm", "settle"]));
                }
                // GEOM tastes the new table when the writer closes the disk.
                Family::FreeBsd => out.push(Cmd::new(&["gpart", "show", &tgt])),
                Family::DragonFly => {
                    // Each slice's disklabel64, and its boot1/boot2 (-B, the
                    // rescue system's, the same release's).
                    for l in self.layout.device(&src).and_then(|v| v.get("disklabels64")).and_then(Value::as_array).cloned().unwrap_or_default() {
                        let (Some(n), Some(text)) = (l.get("slice").and_then(Value::as_u64), s(&l, "text")) else { continue };
                        let proto = format!("{RUN_DIR}/{}s{n}.disklabel64", super::safe_run_token(&src, "disk"));
                        out.push(Cmd::write_file(&proto, text));
                        out.push(Cmd::new(&["disklabel64", "-R", "-B", &format!("{tgt}s{n}"), &proto]).why(format!("{src}s{n}'s disklabel64, and DragonFly's boot1/boot2")));
                    }
                }
                Family::NetBsd | Family::OpenBsd => {
                    let label = self.layout.device(&src).and_then(|v| s(v, "disklabel"));
                    match (d.table.as_ref().map(|t| t.kind), label) {
                        (_, Some(text)) => {
                            // The source's disklabel, as disklabel printed
                            // it (OpenBSD's DUID line included).
                            let proto = format!("{RUN_DIR}/{}.disklabel", super::safe_run_token(&src, "disk"));
                            out.push(Cmd::write_file(&proto, text));
                            out.push(Cmd::new(&["disklabel", "-R", &tgt, &proto]).why(format!("{src}'s disklabel")));
                            // disklabel -R makes a DUID of its own; fstab names the source's.
                            if let Some(duid) = self.layout.device(&src).and_then(|v| s(v, "duid")) {
                                out.push(Cmd::new(&["sh", "-c", &format!("printf 'i\\n%s\\nw\\nq\\n' {} | disklabel -E {qtgt} >/dev/null", q(&duid))]).why(format!("{src}'s disklabel UID, {duid}")));
                            }
                        }
                        (Some(TableKind::Gpt), None) if fam == Family::NetBsd => {
                            out.push(Cmd::new(&["sh", "-c", &format!("dkctl {qtgt} listwedges | grep -q '^dk' || dkctl {qtgt} makewedges")]).why("the GPT's partitions as wedges"));
                        }
                        _ => {}
                    }
                }
                _ => {}
            }
        }
        out
    }

    /// newfs as the source's dumpfs -m printed it, with its size in the
    /// target device's sectors, and the tunefs settings newfs doesn't take.
    pub(crate) fn other_newfs(&mut self, src: &str, dev: &str) -> Vec<Cmd> {
        let p = self.layout.device(src).cloned().unwrap_or(Value::Null);
        let Some(cmd) = s(&p, "newfs") else {
            self.problems.push(format!(
                "{src}: UFS, but the dump has no newfs command for it (dumpfs -m)"
            ));
            return vec![];
        };
        let raw: Vec<&str> = cmd.split_whitespace().collect();
        if !valid_newfs_words(&raw) {
            self.problems.push(format!(
                "{src}: UFS newfs command {cmd:?} isn't a valid dumpfs -m command"
            ));
            return vec![];
        }
        // On the partition itself, -s (the source's size) and all: a GEOM
        // label, labelled after, takes its last sector (GhostBSD's
        // installer makes the filesystem first, so that it spans it).
        // NetBSD's and OpenBSD's newfs take the raw (character) device.
        let node = match self.fam {
            Some(Family::NetBsd | Family::OpenBsd) if dev.starts_with("/dev/") => {
                dev.replacen("/dev/", "/dev/r", 1)
            }
            _ => dev.to_string(),
        };
        let mut words: Vec<String> = raw
            .into_iter()
            .map(|w| {
                if w == "DEVICE" {
                    node.clone()
                } else {
                    w.to_string()
                }
            })
            .collect();
        let mut why = format!("{src}: UFS as the source's (dumpfs -m)");
        // DragonFly's dumpfs -m gives -s in fragments, newfs takes sectors:
        // the whole partition (what made it) instead.
        if self.fam == Some(Family::DragonFly) {
            if let Some(i) = words.iter().position(|w| w == "-s") {
                words.drain(i..(i + 2).min(words.len()));
            }
        } else if self.fam == Some(Family::FreeBsd) {
            if let Some(said) =
                newfs_size_in_sectors(&mut words, newfs_sector(self.layout, src, &p))
            {
                why += &format!("; {said}");
            }
        }
        let mut out = vec![Cmd {
            argv: words,
            why: Some(why),
            ..Cmd::default()
        }];
        let tf = p.get("tunefs").cloned().unwrap_or(Value::Null);
        for (flag, on) in [
            ("-a", "POSIX.1e ACLs"),
            ("-N", "NFSv4 ACLs"),
            ("-l", "MAC multilabel"),
        ] {
            if s(&tf, flag).as_deref() == Some("enabled") {
                out.push(Cmd::new(&["tunefs", flag, "enable", dev]).why(format!("{src}: {on}")));
            }
        }
        if let Some(l) = s(&tf, "-L").filter(|l| !l.is_empty()) {
            out.push(Cmd::new(&["tunefs", "-L", &l, dev]));
        }
        out
    }

    /// HAMMER2 with the source's PFSs (newfs_hammer2 makes LOCAL itself).
    pub(crate) fn other_newfs_hammer2(&mut self, src: &str, dev: &str) -> Vec<Cmd> {
        let pfs: Vec<String> = self
            .layout
            .device(src)
            .and_then(|v| v.get("pfs"))
            .and_then(Value::as_array)
            .into_iter()
            .flatten()
            .filter_map(|x| x.as_str().map(str::to_string))
            .filter(|p| p != "LOCAL")
            .collect();
        let mut a = vec!["newfs_hammer2".to_string()];
        for p in &pfs {
            a.extend(["-L".into(), p.clone()]);
        }
        a.push(dev.to_string());
        vec![Cmd {
            argv: a,
            why: Some(format!("{src}: HAMMER2, with the PFSs {}", pfs.join(", "))),
            ..Cmd::default()
        }]
    }

    /// BFS (Haiku) with the source's block size and volume name.
    pub(crate) fn other_mkbfs(&mut self, src: &str, dev: &str, c: &Value) -> Vec<Cmd> {
        let name = s(c, "label").unwrap_or_else(|| "Haiku".into());
        let mut a = vec!["mkfs".to_string(), "-t".into(), "bfs".into(), "-q".into()];
        if let Some(b) = c.get("block_size").and_then(Value::as_u64) {
            a.extend(["-o".into(), format!("block_size {b}")]);
        }
        a.extend([dev.to_string(), name]);
        vec![Cmd {
            argv: a,
            why: Some(format!("{src}: BFS, as the source's")),
            ..Cmd::default()
        }]
    }

    /// A FAT filesystem (an ESP the dump didn't image) as the source's, by
    /// this system's own formatter: newfs_msdos on the BSDs, mkfs -F pcfs
    /// on illumos, mkfs -t fat on Haiku (neither of whose rescue images has
    /// newfs_msdos).  What illumos' or Haiku's can't be told and the boot
    /// sector holds (volume ID, media byte, OEM name, hidden sectors) is set
    /// after it by `bko-holt fat-set` (load/fatset.rs); what neither can
    /// make as the source's is said in a note.
    pub(crate) fn other_newfs_msdos(&mut self, src: &str, dev: &str, c: &Value) -> Vec<Cmd> {
        let why = format!("{src}: FAT, as the source's");
        let part = self.layout.device(src).cloned().unwrap_or(Value::Null);
        let fat = match self.fam {
            Some(Family::Illumos) => mkfs_pcfs(dev, c, &part),
            Some(Family::Haiku) => mkfs_haiku_fat(dev, c, &part),
            _ => {
                return vec![Cmd {
                    argv: newfs_msdos(dev, c),
                    why: Some(why),
                    ..Cmd::default()
                }];
            }
        };
        let fat = match fat {
            Ok(f) => f,
            Err(e) => {
                self.problems.push(format!("{src}: {e}"));
                return vec![];
            }
        };
        let mut out = vec![fat.mkfs.why(why)];
        if let Some(args) = fat_set_args(&fat.set) {
            let exe = std::env::current_exe()
                .map(|p| p.to_string_lossy().into_owned())
                .unwrap_or_else(|_| "bko-holt".into());
            let mut a = vec![exe, "fat-set".to_string(), fat.node.clone()];
            a.extend(args);
            out.push(Cmd {
                argv: a,
                why: Some(format!(
                    "{src}: the source's {}, which {} can't be told",
                    fat_set_words(&fat.set),
                    fat.tool
                )),
                ..Cmd::default()
            });
        }
        if !fat.lost.is_empty() {
            self.notes.push(format!(
                "{src}: {} makes its own {}, not the source's",
                fat.tool,
                fat.lost.join(", ")
            ));
        }
        self.notes.push(match self.fam {
            Some(Family::Illumos) => format!("{src}: the new FAT holds nothing of the backup (an illumos restore mounts ZFS only); an ESP gets illumos' loader from installboot"),
            _ => format!("{src}: the new FAT holds nothing of the backup (a Haiku restore puts back the boot volume only)"),
        });
        out
    }

    /// GEOM labels (glabel) on the partitions the source had them on,
    /// after their filesystems: a label takes the last sector.
    pub(crate) fn other_glabels(&mut self) -> Vec<Cmd> {
        let mut out = Vec::new();
        for (src, tgt) in self.names.mapping.pairs.clone() {
            let Some(d) = self.layout.disk(&src).cloned() else {
                continue;
            };
            for p in &d.parts {
                if let Some(l) = self.layout.device(&p.name).and_then(|v| s(v, "glabel")) {
                    let node = part_node(Family::FreeBsd, &d, &tgt, p.number);
                    out.push(
                        Cmd::new(&["glabel", "label", &l, &node])
                            .why(format!("{}: GEOM label {l}, as the source's", p.name))
                            .made_on(vec![src.clone()]),
                    );
                }
            }
        }
        out
    }

    /// The boot code went on with the table (partition stage); what's left
    /// is each pool's zpool.cache where the source kept it, and load's log.
    pub(crate) fn other_boot(&mut self, fam: Family) -> Vec<Cmd> {
        let t = self.opts.target.clone();
        let qt = q(&t);
        let mut out = vec![Cmd::note(
            "the boot code (MBR, freebsd-boot) was written with the partition table".to_string(),
        )];
        out.extend(self.geli_boot());
        // newfs -U makes /.snap (for UFS snapshots); a source without it
        // gets none.
        for e in self.layout.arr(&["storage", "fstab"]).to_vec() {
            let (Some(file), Some(src)) = (s(&e, "file"), s(&e, "resolves_to")) else {
                continue;
            };
            if !crate::load::commands::safe_target_subpath(&file) {
                continue;
            }
            if self
                .layout
                .device(&src)
                .and_then(|v| v.get("snap_dir"))
                .and_then(Value::as_bool)
                == Some(false)
            {
                let top = format!("{t}{}", if file == "/" { "" } else { file.as_str() });
                let (snap, top) = (q(&format!("{top}/.snap")), q(&top));
                let r = format!("{RUN_DIR}/snapref");
                out.push(Cmd::new(&["sh", "-c", &format!("[ ! -d {snap} ] || {{ touch -r {top} {r} && rmdir {snap} && touch -r {r} {top}; }}")]).why(format!("{file} had no .snap on the source (its directory's times kept)")));
            }
        }
        let cache = self
            .layout
            .v
            .at(&["storage", "zfs", "zpool_cache"])
            .is_some_and(|c| !c.is_null());
        for p in self.pools.clone() {
            if cache {
                out.push(
                    Cmd::new(&["sh", "-c", &format!("for c in /etc/zfs/zpool.cache /boot/zfs/zpool.cache; do [ -f {qt}$c ] && zpool set cachefile={qt}$c {}; done; true", q(&p))])
                        .why("the restored system's zpool.cache names the new pool"),
                );
            }
        }
        if matches!(fam, Family::NetBsd | Family::OpenBsd) {
            out.extend(self.labelled_boot(fam));
        }
        if fam == Family::Illumos {
            out.extend(self.illumos_boot());
        }
        if fam == Family::Haiku {
            // The BFS boot block (the MBR's code went on with the table).
            for (src, tgt) in self.names.mapping.pairs.clone() {
                let Some(d) = self.layout.disk(&src).cloned() else {
                    continue;
                };
                // makebootable takes the volume by a directory on it (Haiku's
                // partitions are no block devices it can open).
                let _ = (&d, &tgt);
                out.push(
                    Cmd::new(&["makebootable", &t])
                        .why("Haiku's boot block, on the restored volume"),
                );
            }
        }
        // Sculpt's partition is its data: no log goes there.
        if fam == Family::Genode {
            self.notes.push("Sculpt OS: load's log stays in the rescue system (its partition has no place for it)".into());
            return out;
        }
        // Haiku's /var is the boot volume's system/var.
        let logdir = if fam == Family::Haiku {
            format!("{t}/system/var/log/bko-holt")
        } else {
            format!("{t}/var/log/bko-holt")
        };
        out.push(Cmd::new(&["mkdir", "-p", &logdir]));
        out.push(
            Cmd::new(&[
                "cp",
                "-Rp",
                &format!("{RUN_DIR}/log/."),
                &format!("{logdir}/"),
            ])
            .why("this restore's log, on the restored system"),
        );
        out
    }

    /// illumos: the boot archive remade (it holds zpool.cache, now the new
    /// pool's) and the BIOS boot blocks, pmbr and gptzfsboot, from the
    /// restored system's /boot (installboot -m); a UEFI ESP came back with
    /// the partition table, or, one format made, gets illumos' loader from
    /// installboot.
    fn illumos_boot(&mut self) -> Vec<Cmd> {
        let t = self.opts.target.clone();
        // OmniOS' bootadm (the rescue system's; not upstream illumos-gate's)
        // remakes etc/versions/build in update-archive from the
        // etc/versions/build.* fragments (assemble_files), and deletes it
        // when etc/versions holds none, as OpenIndiana's does.  So a build
        // with no fragment gets one, a copy of it, for the run, and the
        // fragment goes again after (etc/versions' times kept).
        // assemble_files puts the old mtime back only when its nanoseconds
        // aren't 0 (a packaged build's are), so such a build gets 1 ns for
        // the run (the second, all bootadm's filestat compares, kept) and
        // the fragment's exact mtime (cp -p) back after.
        let v = q(&format!("{t}/etc/versions"));
        let r = format!("{RUN_DIR}/versref");
        let mut out = vec![
            Cmd::new(&["sh", "-c", &format!("[ -f {v}/build ] && [ ! -h {v}/build ] || exit 0; for f in {v}/build.*; do if [ -e \"$f\" ] || [ -h \"$f\" ]; then exit 0; fi; done; touch -r {v} {r} && cp -p {v}/build {v}/build.bko-holt && s=$(/usr/gnu/bin/stat -c %.9Y {v}/build) && case $s in *.000000000) /usr/gnu/bin/touch -m -d @${{s%.*}}.000000001 {v}/build;; esac")])
                .why("etc/versions/build as a fragment of itself, so OmniOS' bootadm remakes it rather than deleting it"),
            Cmd::new(&["bootadm", "update-archive", "-R", &t]).why("the boot archive, with the new pool's zpool.cache"),
            Cmd::new(&["sh", "-c", &format!("[ ! -f {r} ] || {{ /usr/gnu/bin/touch -m -r {v}/build.bko-holt {v}/build && rm -f {v}/build.bko-holt && touch -r {r} {v} && rm -f {r}; }}")]).why("etc/versions/build's fragment gone again, and its mtime back"),
        ];
        let uefi = self.layout.uefi();
        for (src, tgt) in self.names.mapping.pairs.clone() {
            let Some(d) = self.layout.disk(&src).cloned() else {
                continue;
            };
            // On UEFI, a disk whose ESP format made (not imaged: other_newfs_msdos)
            // gets its loader from installboot too, as illumos' installer put
            // it there: EFI/Boot/bootx64.efi (and bootia32.efi), from the
            // restored /boot, with pmbr and gptzfsboot, which it always writes.
            let formatted_esp = d.parts.iter().any(|p| {
                (p.ptype.eq_ignore_ascii_case(crate::parttable::ESP)
                    || p.ptype.eq_ignore_ascii_case("0xef"))
                    && matches!(
                        p.content.get("type").and_then(Value::as_str),
                        Some("vfat" | "msdos")
                    )
                    && self
                        .layout
                        .device(&p.name)
                        .is_none_or(|v| v.get("esp_image").is_none())
            });
            if uefi && !formatted_esp {
                continue;
            }
            let why = if uefi {
                "illumos' UEFI loader on the new ESP (EFI/Boot), and gptzfsboot in the pool's boot area"
            } else {
                "illumos' gptzfsboot, in the pool's boot area"
            };
            for p in d
                .parts
                .iter()
                .filter(|p| p.content.get("type").and_then(Value::as_str) == Some("zfs_member"))
            {
                let raw = format!("/dev/rdsk/{}", super::illumos::slice_name(&tgt, p.number));
                // (No -m: the partition stage wrote the source's MBR code.)
                out.push(
                    Cmd::new(&["installboot", "-f", "-b", &format!("{t}/boot"), &raw]).why(why),
                );
            }
        }
        out
    }

    /// NetBSD's and OpenBSD's boot blocks, from the rescue system's
    /// /usr/mdec (the same system's): on BIOS, the primary bootstrap in the
    /// root partition (installboot); on UEFI, the ESP's loader.
    fn labelled_boot(&mut self, fam: Family) -> Vec<Cmd> {
        let t = self.opts.target.clone();
        let mut out = Vec::new();
        let uefi = self.layout.uefi();
        // The root filesystem's partition here.
        let root = self
            .layout
            .arr(&["storage", "fstab"])
            .iter()
            .find(|e| s(e, "file").as_deref() == Some("/"))
            .and_then(|e| s(e, "resolves_to"));
        let Some(root) = root else {
            self.problems
                .push("no fstab entry for /: no boot blocks can be installed".into());
            return out;
        };
        let Some(rdev) = self.names.dev(&root) else {
            return out;
        };
        if uefi {
            // The ESP (not mounted on the source, so not in the backup):
            // the loader on its removable path.
            for (src, tgt) in self.names.mapping.pairs.clone() {
                let Some(d) = self.layout.disk(&src).cloned() else {
                    continue;
                };
                for p in d
                    .parts
                    .iter()
                    .filter(|p| p.ptype.eq_ignore_ascii_case(crate::parttable::ESP))
                {
                    let node = part_node(fam, &d, &tgt, p.number);
                    let mnt = format!("{RUN_DIR}/esp");
                    out.push(
                        Cmd::new(&["sh", "-c", &esp_loader(&node, &mnt)])
                            .why("the ESP's loader (BOOTX64.EFI), as the source's"),
                    );
                }
            }
        } else if fam == Family::NetBsd {
            let ffs = if self
                .layout
                .content(&root)
                .and_then(|c| s(c, "version"))
                .as_deref()
                == Some("1")
            {
                "ffsv1"
            } else {
                "ffsv2"
            };
            let raw = rdev.replacen("/dev/", "/dev/r", 1);
            out.push(
                Cmd::new(&[
                    "installboot",
                    "-v",
                    &raw,
                    &format!("/usr/mdec/bootxx_{ffs}"),
                ])
                .why("NetBSD's primary bootstrap, in the root partition"),
            );
        } else if let Some(find) = self.softraid_of(&root) {
            // / in a softraid volume: installboot on the volume writes
            // biosboot to its chunk's disk and /boot to the chunk's boot area.
            out.push(
                Cmd::new(&["sh", "-c", &format!("{find}installboot -v -r {} \"$v\" /usr/mdec/biosboot /usr/mdec/boot", q(&t))])
                    .why("OpenBSD's biosboot, and /boot it loads, through the softraid volume / is in (onto its chunk)"),
            );
        } else {
            let disk = rdev
                .trim_start_matches("/dev/")
                .trim_end_matches(|c: char| c.is_ascii_lowercase())
                .to_string();
            out.push(
                Cmd::new(&[
                    "installboot",
                    "-v",
                    "-r",
                    &t,
                    &disk,
                    "/usr/mdec/biosboot",
                    "/usr/mdec/boot",
                ])
                .why("OpenBSD's biosboot, and /boot it loads"),
            );
        }
        out
    }

    pub(crate) fn other_close(&mut self, _fam: Family) -> Vec<Cmd> {
        let mut out = Vec::new();
        let mut mps = self.mount_points.clone();
        mps.sort_by_key(|m| std::cmp::Reverse(m.matches('/').count()));
        let t = self.opts.target.clone();
        if _fam.on_linux() {
            out.push(Cmd::new(&["umount", "--recursive", &t]).why("the restored tree"));
            out.extend(self.closers.iter().rev().cloned());
            return out;
        }
        if _fam == Family::Haiku {
            out.push(Cmd::new(&["unmount", &t]).why("the restored volume"));
            out.extend(self.closers.iter().rev().cloned());
            return out;
        }
        let list = match _fam {
            Family::Illumos => "awk '{ print $2 }' /etc/mnttab".to_string(),
            Family::NetBsd | Family::OpenBsd => {
                "mount | sed -n 's/^.* on \\(.*\\) type .*/\\1/p'".to_string()
            }
            _ => "mount -p | awk '{ print $2 }'".to_string(),
        };
        // On a softraid volume (OpenBSD), umount can find the restored
        // tree busy with no file open on it, fstat showing nothing: a
        // reference the kernel holds, seen twice in HoltTest1's
        // openbsd8-crypt runs and not reproduced on demand.  Then, and only
        // then, it unmounts with -f, which still writes everything out.
        let softraid = _fam == Family::OpenBsd
            && !self.layout.arr(&["storage", "softraid_volumes"]).is_empty();
        let umount = if softraid {
            super::softraid::UMOUNT_BUSY
        } else {
            "umount \"$m\" || exit"
        };
        // A target with a character to quote can't go in awk's program: it
        // goes to the shell's case as a quoted word instead.
        let script = if q(&t) == t {
            format!(
                "{list} | awk '$0 == \"{t}\" || index($0, \"{t}/\") == 1' | sort -r | while read -r m; do {umount}; done"
            )
        } else {
            format!(
                "t={}; {list} | sort -r | while read -r m; do case $m in \"$t\" | \"$t\"/*) {umount};; esac; done",
                q(&t)
            )
        };
        out.push(
            Cmd::new(&["sh", "-c", &script]).why("everything under the target, deepest first"),
        );
        let _ = mps;
        out.extend(self.closers.iter().rev().cloned());
        out
    }
}

/// newfs_msdos's words for FAT content C on DEV (FreeBSD, NetBSD,
/// OpenBSD and DragonFly read them alike): the source's FAT size, volume
/// ID, label, cluster size and media byte (the dump's hex text, read as
/// Linux's mkfs.fat -M reads it: RustAudit1 H7-09; newfs_msdos -m takes
/// 0x hex).
fn newfs_msdos(dev: &str, c: &Value) -> Vec<String> {
    let mut a = vec!["newfs_msdos".to_string()];
    if let Some(b) = c.get("fat_bits").and_then(Value::as_u64) {
        a.extend(["-F".into(), b.to_string()]);
    }
    if let Some(id) = s(c, "uuid").map(|u| u.replace('-', "")) {
        a.extend(["-I".into(), format!("0x{id}")]);
    }
    if let Some(l) = s(c, "label") {
        a.extend(["-L".into(), l]);
    }
    if let Some(n) = c.get("cluster_size").and_then(Value::as_u64) {
        a.extend([
            "-c".into(),
            (n / c
                .get("bytes_per_sector")
                .and_then(Value::as_u64)
                .unwrap_or(512)
                .max(1))
            .to_string(),
        ]);
    }
    if let Some(m) = c.get_u64_lenient("media") {
        a.extend(["-m".into(), format!("0x{m:02x}")]);
    }
    a.push(dev.to_string());
    a
}

/// A FAT as illumos' or Haiku's formatter makes it: the command, the node
/// `bko-holt fat-set` then writes, what that sets, and the source's fields
/// neither keeps (the note's words).
#[derive(Debug)]
struct FatMake {
    tool: &'static str,
    mkfs: Cmd,
    node: String,
    set: crate::load::fatset::Set,
    lost: Vec<String>,
}

fn num(c: &Value, k: &str) -> Option<u64> {
    c.get(k).and_then(Value::as_u64)
}

/// The source FAT's bytes per sector: 512, the only size either formatter
/// is given here (both take the device's own).
fn fat_512(c: &Value, tool: &str) -> Result<(), String> {
    match num(c, "bytes_per_sector") {
        Some(b) if b != 512 => Err(format!(
            "its FAT has {b}-byte sectors; {tool} makes the device's own, and bko-holt load gives it 512-byte ones only"
        )),
        _ => Ok(()),
    }
}

/// The source's volume ID, media byte and OEM name where they differ from
/// what the formatter writes (ID, MEDIA, OEM; None: one it can't foretell),
/// for fat-set; LOST gets an OEM name fat-set can't write.
fn fat_fields(
    c: &Value,
    id: Option<u32>,
    media: Option<u8>,
    oem: Option<&str>,
    lost: &mut Vec<String>,
) -> crate::load::fatset::Set {
    use crate::load::fatset::{Set, parse_id, parse_oem};
    let mut set = Set::default();
    match s(c, "uuid").map(|u| (parse_id(&u), u)) {
        Some((Some(v), _)) if Some(v) != id => set.id = Some(v),
        Some((None, u)) => lost.push(format!(
            "volume ID (the source's {u:?} isn't a FAT volume ID)"
        )),
        _ => {}
    }
    if let Some(m) = c
        .get_u64_lenient("media")
        .and_then(|m| u8::try_from(m).ok())
        .filter(|&m| Some(m) != media)
    {
        set.media = Some(m);
    }
    if let Some(o) = s(c, "oem_name").filter(|o| Some(o.as_str()) != oem) {
        match parse_oem(&o) {
            Some(b) => set.oem = Some(b),
            None => lost.push(format!("OEM name (the source's {o:?})")),
        }
    }
    set
}

/// The FAT's size in 512-byte sectors: the source's filesystem's, within
/// its partition (fit can only have made that bigger).
fn fat_sectors(c: &Value, part: &Value) -> Option<u64> {
    let p = num(part, "sectors").or_else(|| num(part, "size_bytes").map(|b| b / 512));
    match (num(c, "total_sectors"), p) {
        (Some(t), Some(p)) => Some(t.min(p)),
        (t, p) => t.or(p),
    }
}

/// illumos' mkfs -F pcfs (mkfs_pcfs(8), illumos-gate's
/// usr/src/cmd/fs.d/pcfs/mkfs) on DEV's raw node: on the slice itself
/// (nofdisk, so its size= the source FAT's sectors), with the source's FAT
/// type (fat=), sectors per cluster (spc=), reserved sectors (reserve=) and
/// label (b=).  It writes volume ID 0, media 0xF8, OEM name "MSWIN4.1", no
/// hidden sectors, 2 FATs and, for FAT12/16, 512 root entries, and asks
/// nothing when its standard input isn't a terminal (an empty one here).
/// Never hidden=: mkfs_pcfs's seek_nofdisk() also seeks that many sectors
/// into DEV and makes the FAT there (hidden=40 on the OmniOS rescue: the
/// boot sector at sector 40, sector 0 left as it was), so the source's
/// hidden sectors are fat-set's, like its volume ID.
fn mkfs_pcfs(dev: &str, c: &Value, part: &Value) -> Result<FatMake, String> {
    const TOOL: &str = "mkfs -F pcfs";
    fat_512(c, TOOL)?;
    let node = dev.replacen("/dev/dsk/", "/dev/rdsk/", 1);
    let size = fat_sectors(c, part).ok_or("its FAT's size isn't in the dump")?;
    let mut o = vec!["nofdisk".to_string(), format!("size={size}")];
    let mut lost = Vec::new();
    let bits = num(c, "fat_bits");
    if let Some(b) = bits {
        o.push(format!("fat={b}"));
    }
    match num(c, "sectors_per_cluster") {
        Some(n) if n.is_power_of_two() && n <= 128 => o.push(format!("spc={n}")),
        Some(n) => lost.push(format!("cluster size ({n} sectors)")),
        None => {}
    }
    match num(c, "reserved_sectors") {
        Some(r) if (if bits == Some(32) { 32 } else { 1 }..=0xffff).contains(&r) => {
            o.push(format!("reserve={r}"))
        }
        Some(r) => lost.push(format!("reserved sectors ({r})")),
        None => {}
    }
    match s(c, "label") {
        // getsubopt(3C) ends a value at a comma; mkfs_pcfs upper-cases it
        // (b=esp writes "ESP" on the OmniOS rescue), so a lower-case one
        // keeps its letters, not its case.
        Some(l)
            if l.len() <= 11
                && !l.contains(',')
                && l.bytes().all(|b| (0x20..0x7f).contains(&b)) =>
        {
            let up = l.to_ascii_uppercase();
            if up != l {
                lost.push(format!("label's case ({l:?}: it writes {up:?})"));
            }
            o.push(format!("b={up}"));
        }
        Some(l) => lost.push(format!(
            "label ({l:?}: it writes labels of up to 11 characters, without a comma)"
        )),
        None => lost.push("label (the source's FAT has none; it writes NONAME)".into()),
    }
    if let Some(n) = num(c, "fats").filter(|&n| n != 2) {
        lost.push(format!("FAT count ({n}; it makes 2)"));
    }
    if let Some(n) = num(c, "root_entries").filter(|&n| n != 512 && bits != Some(32)) {
        lost.push(format!("root directory size ({n} entries; it makes 512)"));
    }
    let mut set = fat_fields(c, Some(0), Some(0xf8), Some("MSWIN4.1"), &mut lost);
    set.hidden = num(c, "hidden_sectors")
        .filter(|&h| h > 0)
        .and_then(|h| u32::try_from(h).ok());
    let mkfs = Cmd {
        argv: vec![
            "mkfs".into(),
            "-F".into(),
            "pcfs".into(),
            "-o".into(),
            o.join(","),
            node.clone(),
        ],
        stdin: Some(String::new()),
        ..Cmd::default()
    };
    Ok(FatMake {
        tool: TOOL,
        mkfs,
        node,
        set,
        lost,
    })
}

/// Haiku's fat add-on's sectors per cluster for a FAT of BITS bits on a
/// partition of BYTES (mkdos.cpp's _dosfs_initialize, Microsoft's size
/// cutoffs), 512-byte sectors: 0 for a size it refuses.
fn haiku_fat_spc(bits: u64, bytes: u64) -> u64 {
    const GIB: u64 = 1 << 30;
    const MIB: u64 = 1 << 20;
    const FLOPPY_MAX: u64 = 2 * 80 * 36 * 512;
    // Each table ascends: the first cutoff the size is within.
    let cut = |table: &[(u64, u64)], default: u64| {
        table
            .iter()
            .find(|(max, _)| bytes <= *max)
            .map_or(default, |x| x.1)
    };
    match bits {
        12 if bytes >= 16 * MIB => 0,
        12 => cut(&[(737_280, 2), (1_474_560, 1), (2_949_120, 2)], 8),
        16 => cut(
            &[
                (FLOPPY_MAX, 0),
                (16 * MIB, 2),
                (128 * MIB, 4),
                (256 * MIB, 8),
                (512 * MIB, 16),
                (GIB, 32),
                (2 * GIB, 64),
            ],
            0,
        ),
        _ => cut(
            &[
                (66_600 * 512, 0),
                (532_480 * 512, 1),
                (8 * GIB, 8),
                (16 * GIB, 16),
                (32 * GIB, 32),
            ],
            64,
        ),
    }
}

/// Haiku's mkfs -t fat (its fat add-on's initialize: mkdos.cpp) on DEV,
/// the whole partition: the source's FAT type (`-o 'fat N'`) and label
/// (its volume name, which it needs).  It writes a volume ID from the
/// clock, media 0xF8 (0xF0 on a removable disk), OEM name "Haiku", the
/// partition's size as its hidden sectors, 2 FATs, 32 (FAT32) or 1
/// reserved sectors, 512 root entries (FAT12/16) and its own cluster size
/// for the partition's size.  -q: it asks nothing.
fn mkfs_haiku_fat(dev: &str, c: &Value, part: &Value) -> Result<FatMake, String> {
    const TOOL: &str = "Haiku's mkfs -t fat";
    fat_512(c, TOOL)?;
    let bytes = num(part, "size_bytes")
        .or_else(|| num(part, "sectors").map(|s| s * 512))
        .ok_or("its partition's size isn't in the dump")?;
    let bits = num(c, "fat_bits").unwrap_or(32);
    let spc = haiku_fat_spc(bits, bytes);
    if spc == 0 {
        return Err(format!(
            "{TOOL} can't make FAT{bits} on a partition of {bytes} bytes"
        ));
    }
    let mut lost = Vec::new();
    if let Some(n) = num(c, "sectors_per_cluster").filter(|&n| n != spc) {
        lost.push(format!("cluster size ({n} sectors; it makes {spc})"));
    }
    if let Some(r) = num(c, "reserved_sectors").filter(|&r| r != if bits == 32 { 32 } else { 1 }) {
        lost.push(format!("reserved sectors ({r})"));
    }
    if let Some(n) = num(c, "fats").filter(|&n| n != 2) {
        lost.push(format!("FAT count ({n}; it makes 2)"));
    }
    if let Some(n) = num(c, "root_entries").filter(|&n| n != 512 && bits != 32) {
        lost.push(format!("root directory size ({n} entries; it makes 512)"));
    }
    if num(c, "total_sectors").is_some_and(|t| t * 512 < bytes) {
        lost.push("size (it fills the partition)".into());
    }
    let label = match s(c, "label").or_else(|| s(c, "boot_sector_label")) {
        // Its name may not be empty or start with a space; it is made a
        // FAT label, upper-cased ('efi' writes "EFI" on the Haiku rescue),
        // so a lower-case one keeps its letters, not its case.
        Some(l)
            if !l.starts_with(' ')
                && l.len() <= 11
                && l.bytes().all(|b| (0x20..0x7f).contains(&b)) =>
        {
            let up = l.to_ascii_uppercase();
            if up != l {
                lost.push(format!("label's case ({l:?}: it writes {up:?})"));
            }
            up
        }
        Some(l) => {
            lost.push(format!("label ({l:?})"));
            "NO NAME".into()
        }
        None => {
            lost.push("label (the source's FAT has none; it needs one: NO NAME)".into());
            "NO NAME".into()
        }
    };
    if s(c, "uuid").is_none() {
        lost.push("volume ID (the source's FAT has none)".into());
    }
    // Its volume ID and (on a removable disk) media byte can't be foretold:
    // the source's are always set.
    let mut set = fat_fields(c, None, None, Some("Haiku"), &mut lost);
    set.hidden = num(c, "hidden_sectors").and_then(|h| u32::try_from(h).ok());
    let mut argv: Vec<String> = vec!["mkfs".into(), "-t".into(), "fat".into(), "-q".into()];
    if let Some(b) = num(c, "fat_bits") {
        argv.extend(["-o".into(), format!("fat {b}")]);
    }
    argv.extend([dev.to_string(), label]);
    Ok(FatMake {
        tool: TOOL,
        mkfs: Cmd {
            argv,
            ..Cmd::default()
        },
        node: dev.to_string(),
        set,
        lost,
    })
}

/// fat-set's arguments for S, or None when there's nothing to set.
fn fat_set_args(s: &crate::load::fatset::Set) -> Option<Vec<String>> {
    let mut a = Vec::new();
    if let Some(id) = s.id {
        a.extend([
            "--id".into(),
            format!("{:04X}-{:04X}", id >> 16, id & 0xffff),
        ]);
    }
    if let Some(m) = s.media {
        a.extend(["--media".into(), format!("0x{m:02x}")]);
    }
    if let Some(o) = &s.oem {
        a.extend([
            "--oem".into(),
            String::from_utf8_lossy(o).trim_end().to_string(),
        ]);
    }
    if let Some(h) = s.hidden {
        a.extend(["--hidden".into(), h.to_string()]);
    }
    (!a.is_empty()).then_some(a)
}

/// What S sets, as words for the step's comment.
fn fat_set_words(s: &crate::load::fatset::Set) -> String {
    let w: Vec<&str> = [
        (s.id.is_some(), "volume ID"),
        (s.media.is_some(), "media byte"),
        (s.oem.is_some(), "OEM name"),
        (s.hidden.is_some(), "hidden sectors"),
    ]
    .iter()
    .filter(|x| x.0)
    .map(|x| x.1)
    .collect();
    match w.as_slice() {
        [] => String::new(),
        [one] => one.to_string(),
        [rest @ .., last] => format!("{} and {last}", rest.join(", ")),
    }
}

/// The `sh -c` script that puts the rescue system's BOOTX64.EFI on the ESP
/// NODE (a NetBSD wedge by its GPT name, NAME=EFI system), mounted at MNT.
fn esp_loader(node: &str, mnt: &str) -> String {
    let (node, mnt) = (q(node), q(mnt));
    format!(
        "mkdir -p {mnt} && mount -t msdos {node} {mnt} && mkdir -p {mnt}/EFI/BOOT && cp /usr/mdec/bootx64.efi {mnt}/EFI/BOOT/BOOTX64.EFI; s=$?; umount {mnt}; exit $s"
    )
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::parttable::build::mbr_entry;
    use crate::sys::FakeRunner;
    use crate::testutil::Tree;

    const ROOT_LABEL: &str = "BACKOTTER-0123abcd";
    const L64_NAME: &str = "BKO_0123456789abcdef0123456789abcdef";

    fn opts() -> Options {
        match crate::load::parse_args(&["l.json".to_string()]).unwrap() {
            crate::load::Parsed::Run(o) => o,
            _ => unreachable!(),
        }
    }

    /// FreeBSD's rescue image as its build.sh lays it out, shrunk: slice 2 a
    /// bsdlabel whose "a", 16 sectors in, is the UFS2 root; slice 3 the
    /// config partition (with `config`).  1 MiB.
    fn freebsd_stick(config: bool) -> Vec<u8> {
        let mut img = vec![0u8; 2048 * 512];
        mbr_entry(&mut img[..512], 1, 0x80, 0xa5, 64, 400);
        let l = &mut img[65 * 512..66 * 512];
        l[0..4].copy_from_slice(&0x8256_4557u32.to_le_bytes());
        l[40..44].copy_from_slice(&512u32.to_le_bytes());
        l[132..136].copy_from_slice(&0x8256_4557u32.to_le_bytes());
        l[138..140].copy_from_slice(&8u16.to_le_bytes());
        l[148..152].copy_from_slice(&384u32.to_le_bytes());
        l[152..156].copy_from_slice(&16u32.to_le_bytes());
        l[180..184].copy_from_slice(&400u32.to_le_bytes());
        let ufs = crate::probe::ufs::sample::image(ROOT_LABEL);
        img[80 * 512..80 * 512 + ufs.len()].copy_from_slice(&ufs);
        if config {
            mbr_entry(&mut img[..512], 2, 0, 0x06, 1024, 1024);
            let fat = crate::probe::fat::sample::fat16(1024, 0x3f2a_9c71, STICK_CONFIG_LABEL);
            img[1024 * 512..].copy_from_slice(&fat);
        }
        img
    }

    fn freebsd_sys(t: &Tree, mounts: &str) -> Sys {
        let info = |n: &str| {
            format!(
                "{n}\n\t512         \t# sectorsize\n\t1048576     \t# mediasize in bytes (1.0M)\n"
            )
        };
        let r = FakeRunner::default()
            .with("sysctl -n kern.disks", "vtbd1 vtbd0")
            .with("diskinfo -v vtbd0", &info("vtbd0"))
            .with("diskinfo -v vtbd1", &info("vtbd1"))
            .with("mount -p", mounts);
        Sys::new(t.root(), Box::new(r), true)
    }

    #[test]
    fn the_freebsd_stick_holds_root_and_is_the_boot_medium() {
        // Its / is /dev/ufs/BACKOTTER-...: the bsdlabel's "a" (defect d), and
        // its config partition says it's the stick (defect b).
        let t = Tree::new("other-load-fbsd");
        t.file("/dev/vtbd0", freebsd_stick(true))
            .file("/dev/vtbd1", vec![0u8; 2048 * 512]);
        let s = freebsd_sys(
            &t,
            &format!(
                "/dev/ufs/{ROOT_LABEL}\t/\tufs\tro,noatime\t1 1\ntmpfs\t/tmp\ttmpfs\trw\t0 0\n"
            ),
        );
        let sys = scan(&s, Family::FreeBsd, &opts(), &[]);
        let stick = sys.disk("vtbd0").unwrap();
        assert!(stick.holds_root, "{:?}", stick.in_use);
        assert!(stick.boot_medium);
        assert!(
            stick.in_use.iter().any(|u| u == "vtbd0s2a is mounted at /"),
            "{:?}",
            stick.in_use
        );
        // The bsdlabel's partition isn't one of the disk's own.
        assert!(stick.parts.iter().all(|p| p.name != "vtbd0s2a"));
        let other = sys.disk("vtbd1").unwrap();
        assert!(!other.holds_root && !other.boot_medium && other.blank());
    }

    #[test]
    fn the_config_partition_alone_makes_the_stick() {
        // As on illumos, whose / is a RAM disk: nothing mounted names the
        // stick, and its config partition still does.
        let t = Tree::new("other-load-cfg");
        t.file("/dev/vtbd0", freebsd_stick(true))
            .file("/dev/vtbd1", vec![0u8; 2048 * 512]);
        let s = freebsd_sys(&t, "/dev/md0\t/\tufs\trw\t0 0\n");
        let sys = scan(&s, Family::FreeBsd, &opts(), &[]);
        let stick = sys.disk("vtbd0").unwrap();
        assert!(stick.boot_medium && !stick.holds_root);
        assert!(!sys.disk("vtbd1").unwrap().boot_medium);
        // No config partition: not the stick (and / still found, by its label).
        let t = Tree::new("other-load-nocfg");
        t.file("/dev/vtbd0", freebsd_stick(false))
            .file("/dev/vtbd1", vec![0u8; 2048 * 512]);
        let s = freebsd_sys(&t, &format!("/dev/ufs/{ROOT_LABEL}\t/\tufs\tro\t1 1\n"));
        let sys = scan(&s, Family::FreeBsd, &opts(), &[]);
        let d = sys.disk("vtbd0").unwrap();
        assert!(!d.boot_medium && d.holds_root);
        // A FAT with a label that only starts so isn't the config partition.
        let mut img = freebsd_stick(false);
        mbr_entry(&mut img[..512], 2, 0, 0x06, 1024, 1024);
        img[1024 * 512..].copy_from_slice(&crate::probe::fat::sample::fat16(1024, 1, "BACKOTTERX"));
        let t = Tree::new("other-load-otherfat");
        t.file("/dev/vtbd0", img)
            .file("/dev/vtbd1", vec![0u8; 2048 * 512]);
        let s = freebsd_sys(&t, "");
        assert!(
            !scan(&s, Family::FreeBsd, &opts(), &[])
                .disk("vtbd0")
                .unwrap()
                .boot_medium
        );
    }

    /// dumpfs -m's -s counts 512-byte units and newfs the device's own
    /// sectors: on a 4096-byte-sector provider (freebsd15-geli-ufs's p3,
    /// geli2.md) the source's 28819448 is 3602431 of them, which is what
    /// newfs's "maximum file system size" says there.
    #[test]
    fn newfs_size_is_the_devices_own_sectors() {
        let words = |t: &str| {
            t.split_whitespace()
                .map(str::to_string)
                .collect::<Vec<String>>()
        };
        let size = |t: &str, sector: u64| {
            let mut w = words(t);
            let why = newfs_size_in_sectors(&mut w, sector);
            (w.join(" "), why)
        };
        let (cmd, why) = size("newfs -O 2 -f 4096 -s 28819448 /dev/vtbd1p3.eli", 4096);
        assert_eq!(cmd, "newfs -O 2 -f 4096 -s 3602431 /dev/vtbd1p3.eli");
        assert_eq!(
            why.unwrap(),
            "dumpfs's -s 28819448 is in 512-byte units, and newfs counts this device's 4096-byte sectors: -s 3602431"
        );
        // 512-byte sectors (the guest's p4): the source's own number.
        assert_eq!(
            size("newfs -O 2 -U -s 1050616 /dev/vtbd1p4.eli", 512),
            (
                "newfs -O 2 -U -s 1050616 /dev/vtbd1p4.eli".to_string(),
                None
            )
        );
        // Not a whole number of sectors: down, never up.
        let (cmd, why) = size("newfs -s 28819455 /dev/vtbd1p3.eli", 4096);
        assert_eq!(cmd, "newfs -s 3602431 /dev/vtbd1p3.eli");
        assert!(why.unwrap().ends_with("-s 3602431, rounded down (28819455 of them isn't a whole number of 4096-byte sectors, and newfs must not be given more than the device holds)"));
        // Nothing to change: no -s, no number after it, not a number.
        for t in [
            "newfs -O 2 -U /dev/vtbd1p3.eli",
            "newfs -O 2 -s",
            "newfs -s all /dev/vtbd1p3.eli",
        ] {
            assert_eq!(size(t, 4096), (t.to_string(), None), "{t}");
        }
    }

    /// DragonFly's rescue image, shrunk: slice 2 a disklabel64 (in its second
    /// sector) named NAME.
    fn dragonfly_stick() -> Vec<u8> {
        let mut img = vec![0u8; 2048 * 512];
        mbr_entry(&mut img[..512], 1, 0x80, 0x6c, 64, 1024);
        let l = &mut img[65 * 512..66 * 512];
        l[0..4].copy_from_slice(&0xc446_4c59u32.to_le_bytes());
        l[72..72 + L64_NAME.len()].copy_from_slice(L64_NAME.as_bytes());
        img
    }

    #[test]
    fn the_dragonfly_stick_holds_root_by_its_label() {
        // Its / is /dev/part-by-label/NAME.a (defect c).
        let t = Tree::new("other-load-dfly");
        t.file("/dev/vbd0", dragonfly_stick())
            .file("/dev/vbd1", vec![0u8; 2048 * 512]);
        let info = |n: &str| {
            format!(
                "/dev/{n}        blksize=512  offset=0x000000000000 size=0x000000100000 1.00 MB\n"
            )
        };
        let r = FakeRunner::default()
            .with("sysctl -n kern.disks", "vbd0 vbd1")
            .with("diskinfo /dev/vbd0", &info("vbd0"))
            .with("diskinfo /dev/vbd1", &info("vbd1"))
            .with("mount -p", &format!("/dev/part-by-label/{L64_NAME}.a\t/\tufs\tro,noatime\t0 0\ntmpfs\t/tmp\ttmpfs\trw\t0 0\n"));
        let s = Sys::new(t.root(), Box::new(r), true);
        let sys = scan(&s, Family::DragonFly, &opts(), &[]);
        let stick = sys.disk("vbd0").unwrap();
        assert!(stick.holds_root, "{:?}", stick.in_use);
        assert!(
            stick.in_use.iter().any(|u| u == "vbd0s2a is mounted at /"),
            "{:?}",
            stick.in_use
        );
        assert!(!sys.disk("vbd1").unwrap().holds_root);
    }

    #[test]
    fn the_illumos_stick_is_the_boot_medium_by_its_config_partition() {
        // illumos' rescue image: an EFI label (GPT) whose 4th entry, slice
        // s3, is the config partition, and / the RAM disk, so nothing
        // mounted names the stick (defect b).
        use crate::parttable::build::{GptSpec, write_gpt};
        let mut img = vec![0u8; 2048 * 512];
        const DATA: &str = "ebd0a0a2-b9e5-4433-87c0-68b6b72699c7";
        write_gpt(
            &mut img,
            512,
            &GptSpec {
                disk_guid: "11111111-2222-3333-4444-555555555555",
                parts: vec![
                    (
                        "c12a7328-f81f-11d2-ba4b-00a0c93ec93b",
                        "00000000-0000-0000-0000-000000000001",
                        34,
                        99,
                        0,
                        "esp",
                    ),
                    (
                        DATA,
                        "00000000-0000-0000-0000-000000000002",
                        100,
                        199,
                        0,
                        "two",
                    ),
                    (
                        DATA,
                        "00000000-0000-0000-0000-000000000003",
                        200,
                        899,
                        0,
                        "three",
                    ),
                    (
                        DATA,
                        "00000000-0000-0000-0000-000000000004",
                        900,
                        1923,
                        0,
                        "config",
                    ),
                ],
            },
        );
        img[900 * 512..1924 * 512].copy_from_slice(&crate::probe::fat::sample::fat16(
            1024,
            0x0123_4567,
            STICK_CONFIG_LABEL,
        ));
        let t = Tree::new("other-load-illumos");
        t.file("/dev/rdsk/c1t0d0p0", img)
            .file("/dev/rdsk/c2t0d0p0", vec![0u8; 2048 * 512]);
        let r = FakeRunner::default()
            .with("diskinfo -Hp", "SCSI\tc1t0d0\tQEMU\tHARDDISK\t1048576\tno\tno\nSCSI\tc2t0d0\tQEMU\tHARDDISK\t1048576\tno\tno\n");
        t.file(
            "/etc/mnttab",
            "/devices/ramdisk:a\t/\tufs\trw,nologging\t0\nswap\t/tmp\ttmpfs\txattr\t0\n",
        );
        let s = Sys::new(t.root(), Box::new(r), true);
        let sys = scan(&s, Family::Illumos, &opts(), &[]);
        let stick = sys.disk("c1t0d0").unwrap();
        assert!(
            stick.parts.iter().any(|p| p.name == "c1t0d0s3"),
            "{:?}",
            stick.parts.iter().map(|p| &p.name).collect::<Vec<_>>()
        );
        assert!(
            stick
                .signatures
                .iter()
                .any(|s| s.starts_with("c1t0d0s3: vfat")),
            "{:?}",
            stick.signatures
        );
        assert!(stick.boot_medium && !stick.holds_root, "{:?}", stick.in_use);
        let other = sys.disk("c2t0d0").unwrap();
        assert!(!other.boot_medium && !other.holds_root && other.blank());
    }

    /// What a shell makes of SCRIPT: its syntax checked (sh -n).
    fn parses(script: &str) -> bool {
        std::process::Command::new("sh")
            .arg("-n")
            .arg("-c")
            .arg(script)
            .status()
            .unwrap()
            .success()
    }

    /// RustAudit1 H9-04: a --target with a space, quotes, $, a backtick and
    /// a newline, planned for each BSD's and illumos' corpus dump, is quoted
    /// in every sh -c script: no script holds it raw (quoted, its ' is
    /// '\'') and each one parses.  A UFS without .snap (ghostbsd26) has its
    /// .snap command so, and a geli guest its passphrase prompt.
    #[test]
    fn sh_scripts_quote_a_hostile_target() {
        use crate::load::{Parsed, Stage, commands, target::Mapping};
        let hostile = "/mnt/new d'isk\"$x`y`\nz";
        for g in [
            "freebsd15",
            "freebsd15-geli",
            "freebsd15-geli-ufs",
            "freebsd15-mirror",
            "ghostbsd26",
            "netbsd11",
            "openbsd8",
            "omnios58",
            "oi2610",
            "oi2610-bios",
            "dragonfly6",
            "alpinebsd",
        ] {
            let text = std::fs::read_to_string(format!(
                "{}/tests/corpus/{g}/src.json",
                env!("CARGO_MANIFEST_DIR")
            ))
            .unwrap();
            let l = Layout::from_json(crate::json::parse(&text).unwrap()).unwrap();
            let pairs = l
                .disks
                .iter()
                .map(|d| (d.name.clone(), d.name.clone()))
                .collect();
            let Ok(Parsed::Run(o)) =
                crate::load::parse_args(&["l.json".into(), "--target".into(), hostile.into()])
            else {
                unreachable!()
            };
            let b = commands::build(
                &l,
                &o,
                &Mapping {
                    pairs,
                    ..Mapping::default()
                },
            );
            let mut n = 0;
            for s in Stage::ALL {
                for c in b
                    .stage(s)
                    .iter()
                    .filter(|c| c.argv.len() > 2 && c.argv[0] == "sh" && c.argv[1] == "-c")
                {
                    let script = &c.argv[2];
                    assert!(!script.contains(hostile), "{g} {}: {script}", s.name());
                    assert!(parses(script), "{g} {}: {script}", s.name());
                    n += usize::from(script.contains("new d"));
                }
            }
            assert!(n > 0, "{g}: no sh -c script names the target");
        }
        // NetBSD's ESP by its GPT name.
        let e = esp_loader("NAME=EFI sys'part", "/var/run/bko-holt/esp");
        assert!(
            e.contains("mount -t msdos 'NAME=EFI sys'\\''part' /var/run/bko-holt/esp &&"),
            "{e}"
        );
        assert!(parses(&e));
        assert_eq!(
            esp_loader("NAME=EFI", "/m"),
            "mkdir -p /m && mount -t msdos NAME=EFI /m && mkdir -p /m/EFI/BOOT && cp /usr/mdec/bootx64.efi /m/EFI/BOOT/BOOTX64.EFI; s=$?; umount /m; exit $s"
        );
    }

    /// alpinebsd's corpus dump: NetBSD on a GPT (ld0: EFI, swap, AlpineBSD).
    fn alpinebsd() -> Layout {
        let text = std::fs::read_to_string(format!(
            "{}/tests/corpus/alpinebsd/src.json",
            env!("CARGO_MANIFEST_DIR")
        ))
        .unwrap();
        Layout::from_json(crate::json::parse(&text).unwrap()).unwrap()
    }

    /// RustAudit1 H9-03: a NetBSD GPT entry with no name is its wedge's
    /// GUID-named node (dkwedge_gpt.c), in lower case, never /dev/wd1mbrN;
    /// a named one is NAME=its name, and an MBR's partition stays as it was.
    #[test]
    fn an_unnamed_gpt_entry_is_its_guid() {
        let mut l = alpinebsd();
        let d = &mut l.disks[0];
        assert_eq!(part_node(Family::NetBsd, d, "wd1", 3), "NAME=AlpineBSD");
        d.parts[2].label = None;
        d.parts[2].uuid = Some("BC000000-0000-4000-8000-000000000004".into());
        assert_eq!(
            part_node(Family::NetBsd, d, "wd1", 3),
            "NAME=bc000000-0000-4000-8000-000000000004"
        );
        // Format and mount go there too.
        use crate::load::{Stage, commands, target::Mapping};
        let b = commands::build(
            &l,
            &opts(),
            &Mapping {
                pairs: vec![("ld0".into(), "wd1".into())],
                ..Mapping::default()
            },
        );
        let words: Vec<String> = [Stage::Format, Stage::Mount]
            .iter()
            .flat_map(|s| b.stage(*s).iter().map(|c| c.argv.join(" ")))
            .collect();
        assert!(
            words.iter().any(|w| w.starts_with("newfs ")
                && w.ends_with(" NAME=bc000000-0000-4000-8000-000000000004")),
            "{words:?}"
        );
        assert!(words.iter().all(|w| !w.contains("mbr")), "{words:?}");
        // Neither a name nor a GUID: as before.
        l.disks[0].parts[2].uuid = None;
        assert_eq!(
            part_node(Family::NetBsd, &l.disks[0], "wd1", 3),
            "/dev/wd1mbr3"
        );
    }

    /// RustAudit1 H9-02: a disk that isn't a target with a wedge of the name
    /// a target partition is to have (the source's own wd0, attached) is a
    /// problem; the target's own wedges, another name, or a layout that
    /// isn't NetBSD's are none.
    #[test]
    fn a_wedge_name_on_another_disk_is_refused() {
        let l = alpinebsd();
        let pairs = vec![("ld0".to_string(), "wd1".to_string())];
        let wedges = "/dev/rwd0: 3 wedges:\ndk0: EFI, 262144 blocks at 2048, type: msdos\ndk1: swap, 1048576 blocks at 264192, type: swap\ndk2: AlpineBSD, 31457280 blocks at 1312768, type: ffs\n";
        let sys = |w0: &str, w1: &str| {
            let r = FakeRunner::default()
                .with("sysctl -n hw.disknames", "wd0 wd1 dk0 dk1 dk2 cd0")
                .with("dkctl wd0 listwedges", w0)
                .with("dkctl wd1 listwedges", w1);
            Sys::new("/nonexistent", Box::new(r), true)
        };
        let p = wedge_clashes(&l, &pairs, &sys(wedges, ""));
        assert_eq!(p.len(), 3, "{p:?}");
        assert!(p[2].starts_with("ld0 -> /dev/wd1: dk2's wedge is to be named AlpineBSD, and wd0, which isn't a target, has a wedge of that name (dk2): "), "{p:?}");
        assert!(
            p.iter()
                .all(|x| x.code == crate::load::codes::ProblemCode::WedgeClash
                    && x.str_field("disk") == Some("ld0")
                    && x.str_field("target") == Some("wd1")),
            "{p:?}"
        );
        // On the target itself: an earlier run's, or what --erase deletes.
        assert!(wedge_clashes(&l, &pairs, &sys("", wedges)).is_empty());
        assert!(
            wedge_clashes(
                &l,
                &pairs,
                &sys("dk0: other, 2048 blocks at 2048, type: ffs\n", "")
            )
            .is_empty()
        );
        // Not NetBSD's layout.
        let mut fb = l.clone();
        fb.v = crate::json::parse(r#"{"os": {"family": "freebsd"}}"#).unwrap();
        assert!(wedge_clashes(&fb, &pairs, &sys(wedges, "")).is_empty());
    }

    /// RustAudit1 HD3-02: GPT names needn't be unique.  On one disk the
    /// kernel names the first entry by its name and a later one of the same
    /// name by its GUID (dk.c refuses, EEXIST; dkwedge_gpt.c retries), so
    /// that later one's node is its GUID-named wedge, and format and mount go
    /// there, not to the first entry's.  Across two targets which wedge gets
    /// the name depends on the order the disks' wedges are made: refused, one
    /// problem per clash; so is a later entry with no GUID to fall back on.
    #[test]
    fn duplicate_gpt_names_are_told_apart_or_refused() {
        use crate::load::{Stage, commands, target::Mapping};
        let sys = || {
            let r = FakeRunner::default()
                .with("sysctl -n hw.disknames", "wd0 wd1 wd2 cd0")
                .with("dkctl wd0 listwedges", "");
            Sys::new("/nonexistent", Box::new(r), true)
        };
        // One disk: the ESP's name on the FFS root too.
        let mut l = alpinebsd();
        l.disks[0].parts[2].label = l.disks[0].parts[0].label.clone();
        let d = &l.disks[0];
        assert_eq!(part_node(Family::NetBsd, d, "wd1", 1), "NAME=EFI");
        assert_eq!(
            part_node(Family::NetBsd, d, "wd1", 3),
            "NAME=bc000000-0000-4000-8000-000000000004"
        );
        let pairs = vec![("ld0".to_string(), "wd1".to_string())];
        assert!(wedge_clashes(&l, &pairs, &sys()).is_empty());
        let b = commands::build(
            &l,
            &opts(),
            &Mapping {
                pairs: pairs.clone(),
                ..Mapping::default()
            },
        );
        let words: Vec<String> = [Stage::Format, Stage::Mount]
            .iter()
            .flat_map(|s| b.stage(*s).iter().map(|c| c.argv.join(" ")))
            .collect();
        assert!(
            words.iter().any(|w| w.starts_with("newfs ")
                && w.ends_with(" NAME=bc000000-0000-4000-8000-000000000004")),
            "{words:?}"
        );
        assert!(
            words
                .iter()
                .all(|w| !(w.starts_with("newfs ") && w.ends_with(" NAME=EFI"))),
            "{words:?}"
        );
        // And the root is mounted from that wedge, not from the ESP's.
        assert!(
            words.iter().any(|w| w.starts_with("mount -t ffs ")
                && w.contains(" NAME=bc000000-0000-4000-8000-000000000004 /mnt/bko-target")),
            "{words:?}"
        );
        assert!(
            words
                .iter()
                .all(|w| !(w.starts_with("mount -t ffs ") && w.contains(" NAME=EFI "))),
            "{words:?}"
        );
        // A later entry of that name with no GUID: no wedge at all.
        l.disks[0].parts[2].uuid = None;
        let p = wedge_clashes(&l, &pairs, &sys());
        assert_eq!(p.len(), 1, "{p:?}");
        assert!(
            p[0].starts_with(
                "ld0 -> /dev/wd1: dk2's GPT name is EFI, as dk0's is, and it has no GUID"
            ),
            "{p:?}"
        );
        // Two targets sharing a name: one problem, for the second.
        let mut l = alpinebsd();
        let mut d1 = l.disks[0].clone();
        d1.name = "ld1".into();
        for (p, (name, label)) in
            d1.parts
                .iter_mut()
                .zip([("dk3", "EFI1"), ("dk4", "swap1"), ("dk5", "AlpineBSD")])
        {
            p.name = name.into();
            p.label = Some(label.into());
            p.uuid = p.uuid.as_ref().map(|u| u.replacen("bc", "bd", 1));
        }
        l.disks.push(d1);
        let pairs = vec![
            ("ld0".to_string(), "wd1".to_string()),
            ("ld1".to_string(), "wd2".to_string()),
        ];
        let p = wedge_clashes(&l, &pairs, &sys());
        assert_eq!(p.len(), 1, "{p:?}");
        assert!(
            p[0].starts_with(
                "ld1 -> /dev/wd2: dk5's wedge is to be named AlpineBSD, as dk2's on /dev/wd1 is"
            ),
            "{p:?}"
        );
        assert!(
            p[0].code == crate::load::codes::ProblemCode::WedgeClash
                && p[0].str_field("disk") == Some("ld1")
                && p[0].str_field("target") == Some("wd2"),
            "{p:?}"
        );
        // Distinct names on the two: none.
        l.disks[1].parts[2].label = Some("AlpineBSD1".into());
        assert!(wedge_clashes(&l, &pairs, &sys()).is_empty());
    }

    #[test]
    fn label64_names() {
        use crate::bytes::MemSource;
        let img = dragonfly_stick();
        assert_eq!(
            label64_name(&MemSource(img[64 * 512..1088 * 512].to_vec())).as_deref(),
            Some(L64_NAME)
        );
        assert_eq!(label64_name(&MemSource(vec![0u8; 8192])), None);
    }

    /// The source's FAT media byte (dump's hex text, "0xf0" on FreeBSD's
    /// ESPs) is kept, as Linux's mkfs.fat -M keeps it (RustAudit1 H7-09);
    /// a dump without one adds nothing.
    #[test]
    fn newfs_msdos_keeps_the_sources_media_byte() {
        let c = crate::json::parse(r#"{"type": "vfat", "fat_bits": 32, "uuid": "BC00-0001", "cluster_size": 512, "bytes_per_sector": 512, "media": "0xf0"}"#).unwrap();
        assert_eq!(
            newfs_msdos("/dev/vtbd0p1", &c).join(" "),
            "newfs_msdos -F 32 -I 0xBC000001 -c 1 -m 0xf0 /dev/vtbd0p1"
        );
        let c = crate::json::parse(r#"{"type": "vfat", "fat_bits": 16, "media": 248}"#).unwrap();
        assert_eq!(
            newfs_msdos("/dev/dk0", &c).join(" "),
            "newfs_msdos -F 16 -m 0xf8 /dev/dk0"
        );
        let c =
            crate::json::parse(r#"{"type": "vfat", "fat_bits": 32, "uuid": "BC00-0001"}"#).unwrap();
        assert_eq!(
            newfs_msdos("/dev/vtbd0p1", &c).join(" "),
            "newfs_msdos -F 32 -I 0xBC000001 /dev/vtbd0p1"
        );
    }

    /// OmniOS' and OpenIndiana's ESP (the corpus dumps' content): mkfs -F
    /// pcfs makes it as the source's, nothing to set after and nothing lost;
    /// FreeBSD's (newfs_msdos's: volume ID, media 0xF0, OEM name BSD4.4, no
    /// label, 40 hidden sectors) gets those four set by fat-set, the label
    /// said.
    #[test]
    fn mkfs_pcfs_makes_illumos_own_esp_and_sets_the_rest() {
        let omnios = crate::json::parse(r#"{"type": "vfat", "uuid": "0000-0000", "label": "NONAME", "version": "FAT32", "fat_bits": 32, "bytes_per_sector": 512, "sectors_per_cluster": 1, "cluster_size": 512, "reserved_sectors": 32, "fats": 2, "fat_sectors": 4064, "total_sectors": 524288, "size_bytes": 268435456, "hidden_sectors": 0, "media": "0xf8", "oem_name": "MSWIN4.1", "backup_boot_sector": 6, "boot_sector_label": "NONAME"}"#).unwrap();
        let part = crate::json::parse(r#"{"sectors": 524288, "size_bytes": 268435456}"#).unwrap();
        let f = mkfs_pcfs("/dev/dsk/c3t0d0s0", &omnios, &part).unwrap();
        assert_eq!(
            f.mkfs.argv.join(" "),
            "mkfs -F pcfs -o nofdisk,size=524288,fat=32,spc=1,reserve=32,b=NONAME /dev/rdsk/c3t0d0s0"
        );
        assert_eq!(
            f.mkfs.stdin.as_deref(),
            Some(""),
            "not a terminal: mkfs_pcfs asks nothing"
        );
        assert_eq!(
            (fat_set_args(&f.set), f.lost.len()),
            (None, 0),
            "{:?}",
            f.lost
        );
        let freebsd = crate::json::parse(r#"{"type": "vfat", "fat_bits": 32, "uuid": "BC00-0001", "label": null, "bytes_per_sector": 512, "sectors_per_cluster": 1, "reserved_sectors": 32, "fats": 2, "total_sectors": 532480, "hidden_sectors": 40, "media": "0xf0", "oem_name": "BSD4.4"}"#).unwrap();
        let f = mkfs_pcfs(
            "/dev/dsk/c3t0d0s0",
            &freebsd,
            &crate::json::parse(r#"{"sectors": 532480}"#).unwrap(),
        )
        .unwrap();
        // Its hidden sectors by fat-set, not hidden=, which moves the FAT.
        assert_eq!(
            f.mkfs.argv[4],
            "nofdisk,size=532480,fat=32,spc=1,reserve=32"
        );
        assert_eq!(
            fat_set_args(&f.set).unwrap().join(" "),
            "--id BC00-0001 --media 0xf0 --oem BSD4.4 --hidden 40"
        );
        assert_eq!(
            (f.node.as_str(), fat_set_words(&f.set).as_str()),
            (
                "/dev/rdsk/c3t0d0s0",
                "volume ID, media byte, OEM name and hidden sectors"
            )
        );
        assert_eq!(
            f.lost,
            ["label (the source's FAT has none; it writes NONAME)"]
        );
        // What it can't be told: a comma or lower case in the label, 1 FAT.
        let odd = crate::json::parse(r#"{"type": "vfat", "fat_bits": 16, "label": "a,b", "fats": 1, "root_entries": 256, "total_sectors": 9000, "bytes_per_sector": 512}"#).unwrap();
        let f = mkfs_pcfs(
            "/dev/dsk/c1t0d0s1",
            &odd,
            &crate::json::parse(r#"{"sectors": 8192}"#).unwrap(),
        )
        .unwrap();
        assert_eq!(f.mkfs.argv[4], "nofdisk,size=8192,fat=16");
        assert_eq!(f.lost.len(), 3, "{:?}", f.lost);
        // A lower-case label: its letters, upper-cased as mkfs_pcfs would.
        let lower = crate::json::parse(r#"{"type": "vfat", "fat_bits": 16, "label": "esp", "total_sectors": 8192, "bytes_per_sector": 512}"#).unwrap();
        let f = mkfs_pcfs("/dev/dsk/c1t0d0s1", &lower, &Value::Null).unwrap();
        assert_eq!(
            (f.mkfs.argv[4].as_str(), f.lost.as_slice()),
            (
                "nofdisk,size=8192,fat=16,b=ESP",
                &["label's case (\"esp\": it writes \"ESP\")".to_string()][..]
            )
        );
        let big = crate::json::parse(
            r#"{"type": "vfat", "fat_bits": 32, "bytes_per_sector": 4096, "total_sectors": 9000}"#,
        )
        .unwrap();
        assert!(
            mkfs_pcfs("/dev/dsk/c1t0d0s1", &big, &Value::Null)
                .unwrap_err()
                .contains("4096-byte sectors")
        );
    }

    /// Haiku's fat add-on picks the cluster size by the partition's size,
    /// as mkdos.cpp does, and refuses a FAT32 under 32.5 MB.
    #[test]
    fn haiku_fat_cluster_sizes() {
        const M: u64 = 1 << 20;
        assert_eq!(haiku_fat_spc(32, 260 * M), 1);
        assert_eq!(haiku_fat_spc(32, 532_480 * 512 + 512), 8);
        assert_eq!(haiku_fat_spc(32, 9 << 30), 16);
        assert_eq!(haiku_fat_spc(32, 64 << 30), 64);
        assert_eq!(haiku_fat_spc(32, 32 * M), 0);
        assert_eq!(haiku_fat_spc(16, 100 * M), 4);
        assert_eq!(haiku_fat_spc(16, 3 << 30), 0);
        assert_eq!(haiku_fat_spc(12, 1_000_000), 1);
        assert_eq!(haiku_fat_spc(12, 700_000), 2);
        assert_eq!(haiku_fat_spc(12, 20 * M), 0);
    }

    /// Haiku's mkfs -t fat upper-cases a volume name: a lower-case label
    /// keeps its letters (the note says its case), one with a leading space
    /// or none at all is NO NAME.
    #[test]
    fn haiku_fat_label_is_upper_cased() {
        let part = crate::json::parse(r#"{"sectors": 524288, "size_bytes": 268435456}"#).unwrap();
        let fat = |label: &str| {
            crate::json::parse(&format!(r#"{{"type": "vfat", "fat_bits": 32, "uuid": "1A2B-3C4D", "label": {label}, "bytes_per_sector": 512, "sectors_per_cluster": 1, "reserved_sectors": 32, "fats": 2, "total_sectors": 524288}}"#)).unwrap()
        };
        for (label, word, lost) in [
            (r#""EFI""#, "EFI", 0),
            (r#""efi""#, "EFI", 1),
            (r#"" efi""#, "NO NAME", 1),
            ("null", "NO NAME", 1),
        ] {
            let f = mkfs_haiku_fat("/dev/disk/scsi/0/0/0/1", &fat(label), &part).unwrap();
            assert_eq!(
                (f.mkfs.argv.last().map(String::as_str), f.lost.len()),
                (Some(word), lost),
                "{label}: {:?}",
                f.lost
            );
        }
    }

    /// haiku6's dump with a 256 MiB ESP after its BFS volume, unimaged
    /// (as the dump leaves a mounted one): the format stage makes it with Haiku's
    /// mkfs -t fat, then fat-set gives it the source's volume ID, media
    /// byte and hidden sectors; no newfs_msdos, which Haiku has none of.
    #[test]
    fn haiku_formats_fat_with_its_own_mkfs() {
        use crate::load::layout::Layout;
        use crate::load::target::Mapping;
        let mut v = crate::json::parse(include_str!("../../tests/corpus/haiku6/src.json")).unwrap();
        let parts = v
            .at_mut(&["storage", "disks", "0", "partitions"])
            .and_then(Value::as_array_mut)
            .unwrap();
        let esp_start = 2048 + 33_552_384 - 524_288;
        parts[0].put("sectors", 33_552_384u64 - 524_288);
        parts.push(crate::json::parse(&format!(
            r#"{{"name": "scsi_2_0_0_1", "number": 2, "start_lba": {esp_start}, "sectors": 524288, "size_bytes": 268435456, "type": "0xef", "bootable": false, "logical": false, "extended": false, "uuid": "00000000-02", "mounted_at": [],
                "content": {{"type": "vfat", "usage": "filesystem", "uuid": "1A2B-3C4D", "label": "EFI", "fat_bits": 32, "bytes_per_sector": 512, "sectors_per_cluster": 1, "cluster_size": 512, "reserved_sectors": 32, "fats": 2, "total_sectors": 524288, "hidden_sectors": {esp_start}, "media": "0xf8", "oem_name": "mkfs.fat"}}}}"#
        )).unwrap());
        let l = Layout::from_json(v).unwrap();
        let b = crate::load::commands::build(
            &l,
            &opts(),
            &Mapping {
                pairs: vec![("scsi_2_0_0".into(), "scsi_3_0_0".into())],
                ..Mapping::default()
            },
        );
        let format: Vec<String> = b
            .stages
            .iter()
            .filter(|(s, _)| *s == crate::load::Stage::Format)
            .flat_map(|(_, c)| c.iter().map(|c| c.argv.join(" ")))
            .collect();
        let exe = std::env::current_exe()
            .unwrap()
            .to_string_lossy()
            .into_owned();
        assert!(
            format.contains(&"mkfs -t fat -q -o fat 32 /dev/disk/scsi/3/0/0/1 EFI".to_string()),
            "{format:?}"
        );
        assert!(format.contains(&format!("{exe} fat-set /dev/disk/scsi/3/0/0/1 --id 1A2B-3C4D --media 0xf8 --oem mkfs.fat --hidden {esp_start}")), "{format:?}");
        assert!(
            !format.iter().any(|c| c.contains("newfs_msdos")),
            "{format:?}"
        );
        assert!(b.problems.is_empty(), "{:?}", b.problems);
        assert!(b.notes.iter().any(|n| n == "scsi_2_0_0_1: the new FAT holds nothing of the backup (a Haiku restore puts back the boot volume only)"), "{:?}", b.notes);
    }

    #[test]
    fn newfs_rejects_arbitrary_or_empty_commands() {
        for ok in [
            "newfs -O 2 -U -a 32 -b 32768 -d 32768 -e 4096 -f 4096 -g 16384 -h 64 -i 8192 -j -k 6400 -m 8 -o time -s 29359024 -t DEVICE",
            "newfs -O 2ea -b 16384 -f 2048 -n 1654272 -m 5 -o time DEVICE",
            "newfs -O 2 -U -n -L rootfs DEVICE",
        ] {
            let raw: Vec<&str> = ok.split_whitespace().collect();
            assert!(valid_newfs_words(&raw), "{ok}");
        }
        for bad in [
            "",
            "   ",
            "sh -c id DEVICE",
            "/sbin/newfs -O 2 DEVICE",
            "newfs",
            "newfs -O 2",
            "newfs -N DEVICE",
            "newfs /dev/sda1 DEVICE",
            "newfs DEVICE DEVICE",
            "newfs -s DEVICE",
            "newfs -o fast DEVICE",
        ] {
            let raw: Vec<&str> = bad.split_whitespace().collect();
            assert!(!valid_newfs_words(&raw), "{bad}");
        }
    }
}
