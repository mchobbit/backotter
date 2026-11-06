//! `bko-holt load` for the systems in other/: the target disks as the
//! system's own tools see them, and the stages that differ from Linux's.
//! ZFS (pools, datasets, mounts) and the archiver's restore are load's own
//! (commands.rs), with this system's device names.

use super::{freebsd, open, read_disk, Family, Found, RawDisk};
use crate::blockdev::{BlockDev, Kind};
use crate::json::Value;
use crate::load::commands::{Builder, Cmd};
use crate::load::layout::{Disk, TableKind};
use crate::load::target::{self, System, TargetDisk};
use crate::load::{Options, RUN_DIR};
use crate::mounts::{self, Mount};
use crate::sys::Sys;

/// The node of partition `n` of target disk `t` (a source partition of `d`).
/// NetBSD names a GPT's partitions as wedges, dkN in the order it finds
/// them: they're named by their GPT name (NAME=), which mount and newfs
/// take.  An MBR partition that holds a disklabel has no node of its own.
pub fn part_node(fam: Family, d: &Disk, t: &str, n: u32) -> String {
    match fam {
        Family::FreeBsd | Family::DragonFly => match d.table.as_ref().map(|t| t.kind) {
            Some(TableKind::Mbr) => format!("/dev/{t}s{n}"),
            _ => format!("/dev/{t}p{n}"),
        },
        Family::Haiku => super::haiku::part_path(t, n),
        Family::Redox | Family::Genode => crate::load::commands::part_path(t, n),
        Family::Illumos => format!("/dev/dsk/{}", super::illumos::slice_name(t, n)),
        Family::NetBsd => match (d.table.as_ref().map(|t| t.kind), d.parts.iter().find(|p| p.number == n).and_then(|p| p.label.clone())) {
            (Some(TableKind::Gpt), Some(l)) => format!("NAME={l}"),
            _ => format!("/dev/{t}mbr{n}"),
        },
        _ => format!("/dev/{t}{n}"),
    }
}

/// The node of the disklabel partition `src` of source disk `d` on target
/// disk `t` (NetBSD, OpenBSD: /dev/ld1a, /dev/sd1a).
pub fn label_node(layout: &crate::load::layout::Layout, d: &Disk, t: &str, src: &str) -> Option<String> {
    let v = layout.device(&d.name)?;
    let p = v.get("label_partitions")?.as_array()?.iter().find(|p| s(p, "name").as_deref() == Some(src))?;
    // DragonFly's disklabel64 is a slice's: vbd1s1d.
    match p.get("slice").and_then(Value::as_u64) {
        Some(n) => Some(format!("/dev/{t}s{n}{}", s(p, "letter")?)),
        None => Some(format!("/dev/{t}{}", s(p, "letter")?)),
    }
}

/// A disk's size here (NetBSD, OpenBSD): disklabel's total sectors.
fn labelled_size(sys: &Sys, disk: &str) -> Option<(u64, u64)> {
    let l = super::bsdlabel::parse(&sys.run_any("disklabel", &[disk])?);
    (l.total_sectors > 0).then_some((l.total_sectors * l.bytes_per_sector.max(512), l.bytes_per_sector.max(512)))
}

fn s(v: &Value, k: &str) -> Option<String> {
    v.get(k).and_then(Value::as_str).map(str::to_string)
}

/// The disks here, read as `dump` reads them, for load's checks.
pub fn scan(sys: &Sys, fam: Family, opts: &Options, ours: &[String]) -> System {
    let raw: Vec<RawDisk> = match fam {
        Family::FreeBsd => freebsd::disks(sys),
        Family::DragonFly => sys
            .run("sysctl", &["-n", "kern.disks"])
            .unwrap_or_default()
            .split_whitespace()
            .filter(|n| !["cd", "acd", "vn", "md"].iter().any(|p| n.starts_with(p) && n[p.len()..].chars().all(|c| c.is_ascii_digit())))
            .filter_map(|n| {
                let (sector, size) = super::dfly::parse_diskinfo(&sys.run("diskinfo", &[&format!("/dev/{n}")])?)?;
                Some(RawDisk { name: n.to_string(), path: format!("/dev/{n}"), size, sector, model: None, serial: None })
            })
            .collect(),
        Family::Haiku => super::haiku::disks(sys),
        Family::Illumos => super::illumos::disks(sys),
        // Rebuilt from Linux: scan is Linux's (target.rs).
        Family::Redox | Family::Genode => Vec::new(),
        Family::NetBsd | Family::OpenBsd => super::labelled::disk_names(fam, &sys.run("sysctl", &["-n", "hw.disknames"]).unwrap_or_default())
            .into_iter()
            .filter_map(|(n, duid)| {
                let (size, sector) = labelled_size(sys, &n)?;
                Some(RawDisk { path: format!("/dev/r{n}{}", super::labelled::raw_letter(fam)), name: n, size, sector, model: None, serial: duid })
            })
            .collect(),
    };
    let mount_list: Vec<mounts::FstabEntry> = match fam {
        Family::Illumos => super::illumos::parse_mnttab(&sys.read("/etc/mnttab").unwrap_or_default())
            .into_iter()
            .map(|(spec, file, vfstype, options)| mounts::FstabEntry { spec, file, vfstype, options, freq: 0, passno: 0 })
            .collect(),
        Family::Haiku => super::haiku::parse_df(&sys.run("df", &[]).unwrap_or_default())
            .into_iter()
            .map(|(file, vfstype, spec)| mounts::FstabEntry { spec, file, vfstype, options: String::new(), freq: 0, passno: 0 })
            .collect(),
        Family::NetBsd | Family::OpenBsd => sys
            .run("mount", &[])
            .unwrap_or_default()
            .lines()
            .filter_map(super::bsdlabel::parse_mount_line)
            .map(|(spec, file, vfstype, options)| mounts::FstabEntry { spec, file, vfstype, options, freq: 0, passno: 0 })
            .collect(),
        _ => sys.run("mount", &["-p"]).map(|t| mounts::parse_fstab(&t)).unwrap_or_default(),
    };
    let mounts: Vec<Mount> = mount_list
        .iter()
        .map(|m| Mount { target: m.file.clone(), source: m.spec.clone(), fstype: m.vfstype.clone(), options: m.options.clone(), ..Mount::default() })
        .collect();
    let swaps: Vec<String> = sys.run("swapctl", &["-l"]).unwrap_or_default().lines().skip(1).filter_map(|l| l.split_whitespace().next().map(str::to_string)).collect();
    let pools = target::imported_pools(sys);
    let mut seen: Vec<(RawDisk, Vec<Found>, Vec<(String, String)>)> = Vec::new();
    let mut tables = Vec::new();
    let mut whole = Vec::new();
    for d in raw {
        let (table, found, labels, w) = match open(sys, &d) {
            Some(src) => {
                let name = d.name.clone();
                let (t, f) = read_disk(&src, &d, &|n, t| match fam {
                    Family::FreeBsd => freebsd::part_name(&name, n, t),
                    Family::DragonFly => format!("{name}s{n}"),
                    Family::Haiku => format!("{name}_{}", n.saturating_sub(1)),
                    Family::Illumos => super::illumos::slice_name(&name, n),
                    _ => format!("{name}:{n}"),
                });
                let labels: Vec<(String, String)> =
                    f.iter().filter_map(|p| super::glabel(&crate::bytes::OffsetSource { inner: &src, start: p.start, len: p.size }).map(|l| (p.name.clone(), l))).collect();
                let w = if t.is_none() { crate::probe::probe_all(&src) } else { Vec::new() };
                (t, f, labels, w)
            }
            None => (None, Vec::new(), Vec::new(), Vec::new()),
        };
        tables.push(table);
        whole.push(w);
        seen.push((d, found, labels));
    }
    let mut disks = Vec::new();
    for (i, (d, found, _)) in seen.iter().enumerate() {
        let b = BlockDev { name: d.name.clone(), kind: Kind::Disk, size_bytes: d.size, logical_block_size: d.sector, physical_block_size: d.sector, model: d.model.clone(), serial: d.serial.clone(), ..BlockDev::default() };
        let parts: Vec<BlockDev> = found
            .iter()
            .map(|f| BlockDev { name: f.name.clone(), kind: Kind::Partition, size_bytes: f.size, parent: Some(d.name.clone()), partition_number: Some(f.number), start_sectors: Some(f.start / 512), ..BlockDev::default() })
            .collect();
        let mut signatures = Vec::new();
        let mut uuids = Vec::new();
        let named = found.iter().map(|f| (f.name.clone(), &f.contents)).chain(std::iter::once((d.name.clone(), &whole[i])));
        for (n, cs) in named {
            for c in cs.iter() {
                signatures.push(format!("{n}: {}{}{}", c.kind, c.uuid.as_ref().map(|u| format!(" UUID={u}")).unwrap_or_default(), c.label.as_ref().map(|l| format!(" LABEL={l}")).unwrap_or_default()));
                if let Some(u) = &c.uuid {
                    uuids.push(u.to_ascii_lowercase());
                }
            }
        }
        // A disklabel partition (ld1a, sd1a) or a wedge is this disk's too.
        // A slice, disklabel partition or wedge of this disk is it too.
        let mine = |n: &str| n == d.name || found.iter().any(|f| f.name == n) || (n.starts_with(&d.name) && n[d.name.len()..].starts_with(|c: char| !c.is_ascii_digit()));
        let mut in_use = Vec::new();
        let mut holds_root = false;
        for m in mount_list.iter().map(|m| {
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
        }).collect::<Vec<_>>().iter() {
            let haiku = (fam == Family::Haiku).then(|| m.spec.rsplit_once('/').map(|(dir, i)| format!("{}_{i}", super::haiku::name_of(dir)))).flatten();
            let Some(dev) = haiku
                .or_else(|| freebsd::resolve(&m.spec, &seen))
                .or_else(|| m.spec.strip_prefix("/dev/dsk/").or_else(|| m.spec.strip_prefix("/dev/")).map(str::to_string))
                .or_else(|| (!m.spec.contains('/') && m.spec.starts_with(&d.name)).then(|| m.spec.clone()))
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
            if let Some(dev) = freebsd::resolve(sw, &seen).or_else(|| sw.strip_prefix("/dev/").map(str::to_string)).filter(|x| mine(x)) {
                in_use.push(format!("{dev} is in use as swap"));
            }
        }
        for p in pools.iter().filter(|p| p.devices.iter().any(|x| mine(x))) {
            if !p.is_ours(ours, &opts.target) {
                in_use.push(format!("{} is a vdev of the imported pool {}", d.name, p.name));
            }
            if mount_list.iter().any(|m| m.file == "/" && m.vfstype == "zfs" && m.spec.split('/').next() == Some(p.name.as_str())) {
                holds_root = true;
            }
        }
        disks.push(TargetDisk { b, parts, table: tables[i].clone(), signatures, uuids, in_use, boot_medium: false, holds_root });
    }
    System { disks, mounts, pools }
}

impl Builder<'_> {
    /// The table, written by bko-holt itself from the dump (GUIDs, names
    /// and attributes exactly), with the boot code the source's MBR and
    /// boot partitions held; then GEOM labels.
    pub(crate) fn other_partition(&mut self, fam: Family) -> Vec<Cmd> {
        let exe = std::env::current_exe().map(|p| p.to_string_lossy().into_owned()).unwrap_or_else(|_| "bko-holt".into());
        let mut out = Vec::new();
        for (src, tgt) in self.names.mapping.pairs.clone() {
            let Some(d) = self.layout.disk(&src).cloned() else { continue };
            let dev = format!("/dev/{tgt}");
            if d.table.is_none() {
                out.push(Cmd::note(format!("{src} has no partition table: its content goes on {dev} itself")));
                continue;
            }
            if self.opts.erase.iter().any(|e| e == &dev || e == &tgt) || fam.on_linux() {
                match fam {
                    Family::Redox | Family::Genode => out.push(Cmd::new(&["wipefs", "--all", &dev]).why(format!("{src} -> {dev}: clear the old table and signatures"))),
                    Family::FreeBsd => out.push(Cmd::new(&["sh", "-c", &format!("gpart destroy -F {tgt} 2>/dev/null; dd if=/dev/zero of={dev} bs=1m count=1 2>/dev/null; true")]).why(format!("--erase {dev}: its old table"))),
                    Family::DragonFly => out.push(Cmd::new(&["sh", "-c", &format!("dd if=/dev/zero of={dev} bs=1m count=1 2>/dev/null; true")]).why(format!("--erase {dev}: its old table"))),
                    Family::Illumos => out.push(Cmd::new(&["sh", "-c", &format!("dd if=/dev/zero of=/dev/rdsk/{tgt}p0 bs=1048576 count=1 2>/dev/null; true")]).why(format!("--erase {tgt}: its old label"))),
                    Family::Haiku => {
                        let raw = format!("{}/raw", super::haiku::dir_of(&tgt));
                        out.push(Cmd::new(&["sh", "-c", &format!("dd if=/dev/zero of={raw} bs=1048576 count=1 2>/dev/null; true")]).why(format!("--erase {raw}: its old table")));
                    }
                    Family::NetBsd | Family::OpenBsd => {
                        let raw = format!("/dev/r{tgt}{}", super::labelled::raw_letter(fam));
                        if fam == Family::NetBsd {
                            out.push(Cmd::new(&["sh", "-c", &format!("dkctl {tgt} listwedges 2>/dev/null | sed -n 's/^\\(dk[0-9]*\\):.*/\\1/p' | while read -r w; do dkctl {tgt} delwedge $w; done; true")]).why(format!("--erase {dev}: its wedges")));
                        }
                        out.push(Cmd::new(&["sh", "-c", &format!("dd if=/dev/zero of={raw} bs=1m count=1 2>/dev/null; true")]).why(format!("--erase {dev}: its old table and label")));
                    }
                }
            }
            let (node, size) = match fam {
                Family::Redox | Family::Genode => (dev.clone(), format!("$(blockdev --getsize64 {dev})")),
                Family::Haiku => (format!("{}/raw", super::haiku::dir_of(&tgt)), "auto".to_string()),
                Family::Illumos => (format!("/dev/rdsk/{tgt}p0"), "auto".to_string()),
                Family::DragonFly => (dev.clone(), format!("$(printf %d 0x$(diskinfo {dev} | sed -n 's/.*size=0x\\([0-9a-fA-F]*\\).*/\\1/p'))")),
                Family::NetBsd | Family::OpenBsd => {
                    let raw = format!("/dev/r{tgt}{}", super::labelled::raw_letter(fam));
                    let size = format!("$(disklabel {tgt} | awk '/^bytes.sector:/ {{ b = $2 }} /^total sectors:/ {{ t = $3 }} END {{ print b * t }}')");
                    (raw, size)
                }
                _ => (dev.clone(), format!("$(diskinfo {dev} | cut -f3)")),
            };
            out.push(
                Cmd::new(&["sh", "-c", &format!("{exe} write-table {} {src} {node} {size}", crate::load::shell_quote(&self.opts.layout))])
                    .why(format!("{src} -> {dev}: the source's table, GUIDs and boot code exactly")),
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
                        let proto = format!("{RUN_DIR}/{src}s{n}.disklabel64");
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
                            let proto = format!("{RUN_DIR}/{src}.disklabel");
                            out.push(Cmd::write_file(&proto, text));
                            out.push(Cmd::new(&["disklabel", "-R", &tgt, &proto]).why(format!("{src}'s disklabel")));
                            // disklabel -R makes a DUID of its own; fstab names the source's.
                            if let Some(duid) = self.layout.device(&src).and_then(|v| s(v, "duid")) {
                                out.push(Cmd::new(&["sh", "-c", &format!("printf 'i\\n%s\\nw\\nq\\n' {duid} | disklabel -E {tgt} >/dev/null")]).why(format!("{src}'s disklabel UID, {duid}")));
                            }
                        }
                        (Some(TableKind::Gpt), None) if fam == Family::NetBsd => {
                            out.push(Cmd::new(&["sh", "-c", &format!("dkctl {tgt} listwedges | grep -q '^dk' || dkctl {tgt} makewedges")]).why("the GPT's partitions as wedges"));
                        }
                        _ => {}
                    }
                }
                _ => {}
            }
        }
        out
    }

    /// newfs as the source's dumpfs -m printed it, and the tunefs settings
    /// newfs doesn't take.
    pub(crate) fn other_newfs(&mut self, src: &str, dev: &str) -> Vec<Cmd> {
        let p = self.layout.device(src).cloned().unwrap_or(Value::Null);
        let Some(cmd) = s(&p, "newfs") else {
            self.problems.push(format!("{src}: UFS, but the dump has no newfs command for it (dumpfs -m)"));
            return vec![];
        };
        // On the partition itself, -s (the source's size) and all: a GEOM
        // label, labelled after, takes its last sector (GhostBSD's
        // installer makes the filesystem first, so that it spans it).
        // NetBSD's and OpenBSD's newfs take the raw (character) device.
        let node = match self.fam {
            Some(Family::NetBsd | Family::OpenBsd) if dev.starts_with("/dev/") => dev.replacen("/dev/", "/dev/r", 1),
            _ => dev.to_string(),
        };
        let mut words: Vec<String> = cmd.split_whitespace().map(|w| if w == "DEVICE" { node.clone() } else { w.to_string() }).collect();
        // DragonFly's dumpfs -m gives -s in fragments, newfs takes sectors:
        // the whole partition (what made it) instead.
        if self.fam == Some(Family::DragonFly) {
            if let Some(i) = words.iter().position(|w| w == "-s") {
                words.drain(i..(i + 2).min(words.len()));
            }
        }
        let mut out = vec![Cmd { argv: words, why: Some(format!("{src}: UFS as the source's (dumpfs -m)")), ..Cmd::default() }];
        let tf = p.get("tunefs").cloned().unwrap_or(Value::Null);
        for (flag, on) in [("-a", "POSIX.1e ACLs"), ("-N", "NFSv4 ACLs"), ("-l", "MAC multilabel")] {
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
        let pfs: Vec<String> = self.layout.device(src).and_then(|v| v.get("pfs")).and_then(Value::as_array).into_iter().flatten().filter_map(|x| x.as_str().map(str::to_string)).filter(|p| p != "LOCAL").collect();
        let mut a = vec!["newfs_hammer2".to_string()];
        for p in &pfs {
            a.extend(["-L".into(), p.clone()]);
        }
        a.push(dev.to_string());
        vec![Cmd { argv: a, why: Some(format!("{src}: HAMMER2, with the PFSs {}", pfs.join(", "))), ..Cmd::default() }]
    }

    /// BFS (Haiku) with the source's block size and volume name.
    pub(crate) fn other_mkbfs(&mut self, src: &str, dev: &str, c: &Value) -> Vec<Cmd> {
        let name = s(c, "label").unwrap_or_else(|| "Haiku".into());
        let mut a = vec!["mkfs".to_string(), "-t".into(), "bfs".into(), "-q".into()];
        if let Some(b) = c.get("block_size").and_then(Value::as_u64) {
            a.extend(["-o".into(), format!("block_size {b}")]);
        }
        a.extend([dev.to_string(), name]);
        vec![Cmd { argv: a, why: Some(format!("{src}: BFS, as the source's")), ..Cmd::default() }]
    }

    /// A FAT filesystem (an ESP) with the source's volume ID and label.
    pub(crate) fn other_newfs_msdos(&mut self, src: &str, dev: &str, c: &Value) -> Vec<Cmd> {
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
            a.extend(["-c".into(), (n / c.get("bytes_per_sector").and_then(Value::as_u64).unwrap_or(512).max(1)).to_string()]);
        }
        a.push(dev.to_string());
        vec![Cmd { argv: a, why: Some(format!("{src}: FAT, as the source's")), ..Cmd::default() }]
    }

    /// GEOM labels (glabel) on the partitions the source had them on,
    /// after their filesystems: a label takes the last sector.
    pub(crate) fn other_glabels(&mut self) -> Vec<Cmd> {
        let mut out = Vec::new();
        for (src, tgt) in self.names.mapping.pairs.clone() {
            let Some(d) = self.layout.disk(&src).cloned() else { continue };
            for p in &d.parts {
                if let Some(l) = self.layout.device(&p.name).and_then(|v| s(v, "glabel")) {
                    let node = part_node(Family::FreeBsd, &d, &tgt, p.number);
                    out.push(Cmd::new(&["glabel", "label", &l, &node]).why(format!("{}: GEOM label {l}, as the source's", p.name)));
                }
            }
        }
        out
    }

    /// The boot code went on with the table (partition stage); what's left
    /// is each pool's zpool.cache where the source kept it, and load's log.
    pub(crate) fn other_boot(&mut self, fam: Family) -> Vec<Cmd> {
        let t = self.opts.target.clone();
        let mut out = vec![Cmd::note("the boot code (MBR, freebsd-boot) was written with the partition table".to_string())];
        // newfs -U makes /.snap (for UFS snapshots); a source without it
        // gets none.
        for e in self.layout.arr(&["storage", "fstab"]).to_vec() {
            let (Some(file), Some(src)) = (s(&e, "file"), s(&e, "resolves_to")) else { continue };
            if self.layout.device(&src).and_then(|v| v.get("snap_dir")).and_then(Value::as_bool) == Some(false) {
                let top = format!("{t}{}", if file == "/" { "" } else { file.as_str() });
                let r = format!("{RUN_DIR}/snapref");
                out.push(Cmd::new(&["sh", "-c", &format!("[ ! -d {top}/.snap ] || {{ touch -r {top} {r} && rmdir {top}/.snap && touch -r {r} {top}; }}")]).why(format!("{file} had no .snap on the source (its directory's times kept)")));
            }
        }
        let cache = if self.layout.v.at(&["storage", "zfs", "zpool_cache"]).is_some_and(|c| !c.is_null()) { Some(()) } else { None };
        for p in self.pools.clone() {
            if cache.is_some() {
                out.push(
                    Cmd::new(&["sh", "-c", &format!("for c in /etc/zfs/zpool.cache /boot/zfs/zpool.cache; do [ -f {t}$c ] && zpool set cachefile={t}$c {p}; done; true")])
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
                let Some(d) = self.layout.disk(&src).cloned() else { continue };
                // makebootable takes the volume by a directory on it (Haiku's
                // partitions are no block devices it can open).
                let _ = (&d, &tgt);
                out.push(Cmd::new(&["makebootable", &t]).why("Haiku's boot block, on the restored volume"));
            }
        }
        // Sculpt's partition is its data: no log goes there.
        if fam == Family::Genode {
            self.notes.push("Sculpt OS: load's log stays in the rescue system (its partition has no place for it)".into());
            return out;
        }
        // Haiku's /var is the boot volume's system/var.
        let logdir = if fam == Family::Haiku { format!("{t}/system/var/log/bko-holt") } else { format!("{t}/var/log/bko-holt") };
        out.push(Cmd::new(&["mkdir", "-p", &logdir]));
        out.push(Cmd::new(&["cp", "-Rp", &format!("{RUN_DIR}/log/."), &format!("{logdir}/")]).why("this restore's log, on the restored system"));
        out
    }

    /// illumos: the boot archive remade (it holds zpool.cache, now the new
    /// pool's) and the BIOS boot blocks, pmbr and gptzfsboot, from the
    /// restored system's /boot (installboot -m); a UEFI ESP came back with
    /// the partition table.
    fn illumos_boot(&mut self) -> Vec<Cmd> {
        let t = self.opts.target.clone();
        let mut out = vec![Cmd::new(&["bootadm", "update-archive", "-R", &t]).why("the boot archive, with the new pool's zpool.cache")];
        if !self.layout.uefi() {
            for (src, tgt) in self.names.mapping.pairs.clone() {
                let Some(d) = self.layout.disk(&src).cloned() else { continue };
                for p in d.parts.iter().filter(|p| p.content.get("type").and_then(Value::as_str) == Some("zfs_member")) {
                    let raw = format!("/dev/rdsk/{}", super::illumos::slice_name(&tgt, p.number));
                    // (No -m: the partition stage wrote the source's MBR code.)
                    out.push(Cmd::new(&["installboot", "-f", "-b", &format!("{t}/boot"), &raw]).why("illumos' gptzfsboot, in the pool's boot area"));
                }
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
        let root = self.layout.arr(&["storage", "fstab"]).iter().find(|e| s(e, "file").as_deref() == Some("/")).and_then(|e| s(e, "resolves_to"));
        let Some(root) = root else {
            self.problems.push("no fstab entry for /: no boot blocks can be installed".into());
            return out;
        };
        let Some(rdev) = self.names.dev(&root) else { return out };
        if uefi {
            // The ESP (not mounted on the source, so not in the backup):
            // the loader on its removable path.
            for (src, tgt) in self.names.mapping.pairs.clone() {
                let Some(d) = self.layout.disk(&src).cloned() else { continue };
                for p in d.parts.iter().filter(|p| p.ptype.eq_ignore_ascii_case(crate::parttable::ESP)) {
                    let node = part_node(fam, &d, &tgt, p.number);
                    let mnt = format!("{RUN_DIR}/esp");
                    out.push(Cmd::new(&["sh", "-c", &format!("mkdir -p {mnt} && mount -t msdos {node} {mnt} && mkdir -p {mnt}/EFI/BOOT && cp /usr/mdec/bootx64.efi {mnt}/EFI/BOOT/BOOTX64.EFI; s=$?; umount {mnt}; exit $s")]).why("the ESP's loader (BOOTX64.EFI), as the source's"));
                }
            }
        } else if fam == Family::NetBsd {
            let ffs = if self.layout.content(&root).and_then(|c| s(c, "version")).as_deref() == Some("1") { "ffsv1" } else { "ffsv2" };
            let raw = rdev.replacen("/dev/", "/dev/r", 1);
            out.push(Cmd::new(&["installboot", "-v", &raw, &format!("/usr/mdec/bootxx_{ffs}")]).why("NetBSD's primary bootstrap, in the root partition"));
        } else {
            let disk = rdev.trim_start_matches("/dev/").trim_end_matches(|c: char| c.is_ascii_lowercase()).to_string();
            out.push(Cmd::new(&["installboot", "-v", "-r", &t, &disk, "/usr/mdec/biosboot", "/usr/mdec/boot"]).why("OpenBSD's biosboot, and /boot it loads"));
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
            Family::NetBsd | Family::OpenBsd => "mount | sed -n 's/^.* on \\(.*\\) type .*/\\1/p'".to_string(),
            _ => "mount -p | awk '{ print $2 }'".to_string(),
        };
        out.push(Cmd::new(&["sh", "-c", &format!("{list} | awk '$0 == \"{t}\" || index($0, \"{t}/\") == 1' | sort -r | while read -r m; do umount \"$m\" || exit; done")]).why("everything under the target, deepest first"));
        let _ = mps;
        out.extend(self.closers.iter().rev().cloned());
        out
    }
}
