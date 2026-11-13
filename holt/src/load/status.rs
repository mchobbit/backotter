//! `bko-holt load --only status`: what's on the target disks now, read from
//! the disks themselves, and which stage is next.  The boot stage is the one
//! exception, since nothing on the disks can tell this restore's boot stage
//! from the one before the backup: it's a mark in RUN_DIR, so after the rescue
//! system reboots the stage runs again, which is harmless.

use super::commands;
use super::layout::Layout;
use super::target;
use super::{Options, Stage, EXIT_OK, EXIT_PLAN_PROBLEMS, RUN_DIR};
use crate::sys::Sys;

/// Exists once the boot stage has run for the tree restored in this RUN_DIR.
pub fn boot_marker() -> String {
    format!("{RUN_DIR}/boot.done")
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum State {
    Done,
    Partly,
    NotDone,
    /// The layout needs nothing from this stage.
    Nothing,
    /// Can't tell (not root, or a tool isn't there).
    Unknown,
}

impl State {
    fn word(self) -> &'static str {
        match self {
            State::Done => "done",
            State::Partly => "partly",
            State::NotDone => "not done",
            State::Nothing => "nothing to do",
            State::Unknown => "unknown",
        }
    }
}

/// done/partly/not done from how many of `total` checks held.
fn tally(ok: usize, total: usize) -> State {
    match (ok, total) {
        (_, 0) => State::Nothing,
        (o, t) if o == t => State::Done,
        (0, _) => State::NotDone,
        _ => State::Partly,
    }
}

/// What's on a device node (its blkid TYPE), read from the device.
fn kind_at(sys: &Sys, node: &str) -> Option<String> {
    let src = sys.open_dev(&crate::other::node_path(sys, node))?;
    crate::probe::probe_all(&src).into_iter().next().map(|c| c.kind)
}

/// The UUID of what's on a device node, read from the device.
fn uuid_at(sys: &Sys, node: &str) -> Option<String> {
    let src = sys.open_dev(&crate::other::node_path(sys, node))?;
    crate::probe::probe_all(&src).into_iter().find_map(|c| c.uuid).map(|u| u.to_ascii_lowercase())
}

pub fn rows(layout: &Layout, opts: &Options, sys: &Sys) -> (Vec<(Stage, State, String)>, Vec<String>) {
    // The disks hold what --fit-largest made of the layout, so that's the
    // one every row is read against (target::prepare, fit.rs).
    let target::Prepared { system, mapping, layout, .. } = target::prepare(layout, opts, sys);
    let layout = &*layout;
    let built = commands::build(layout, opts, &mapping);
    let mut out = Vec::new();

    // partition: each table and partition as the source had them.
    let (mut ok, mut total, mut why) = (0, 0, vec![]);
    for (src, tgt) in &mapping.pairs {
        let (Some(d), Some(t)) = (layout.disk(src), system.disk(tgt)) else { continue };
        if d.table.is_none() {
            continue;
        }
        total += 1;
        // The same check decides whether --erase lets go of what holds a
        // target (target::release_holders).
        if target::partition_done(d, t) {
            ok += 1;
        } else if t.table.is_none() {
            why.push(format!("/dev/{tgt}: no partition table"));
        } else {
            why.push(format!("/dev/{tgt}: its table isn't the source's"));
        }
    }
    let st = if !sys.privileged && total > 0 { State::Unknown } else { tally(ok, total) };
    out.push((Stage::Partition, st, why.join("; ")));

    // assemble: the device nodes and pools.
    // A pool counts when it's imported from the target disks; the system
    // load runs on may have one of the same name of its own.
    let targets: Vec<&str> = mapping.pairs.iter().map(|(_, t)| t.as_str()).collect();
    let on_targets = |dev: &str| system.disks.iter().any(|d| targets.contains(&d.b.name.as_str()) && (d.b.name == dev || d.parts.iter().any(|p| p.name == dev)));
    let imported: Vec<String> = system.pools.iter().filter(|p| !p.devices.is_empty() && p.devices.iter().all(|d| on_targets(d))).map(|p| p.name.clone()).collect();
    let mut missing: Vec<String> = built.made.iter().filter(|(_, node)| !sys.exists(node)).map(|(_, n)| n.clone()).collect();
    missing.extend(built.pools.iter().filter(|p| !imported.contains(p)).map(|p| format!("pool {p}")));
    let total = built.made.len() + built.pools.len();
    out.push((Stage::Assemble, tally(total - missing.len(), total), if missing.is_empty() { String::new() } else { format!("missing: {}", missing.join(", ")) }));

    // format: each filesystem's UUID on its device, and the ZFS datasets.
    let datasets: Vec<String> = sys
        .run("zfs", &["list", "-H", "-o", "name"])
        .map(|o| o.lines().map(|l| l.trim().to_string()).filter(|d| imported.iter().any(|p| d == p || d.starts_with(&format!("{p}/")))).collect())
        .unwrap_or_default();
    let mut bad = vec![];
    for (node, ty, uuid) in &built.filesystems {
        // UFS and HAMMER2 can't be given the source's UUID: the type shows it.
        if matches!(ty.as_str(), "ufs" | "hammer2" | "befs" | "redoxfs") {
            if kind_at(sys, node).as_deref() != Some(ty.as_str()) {
                bad.push(format!("{node} ({ty})"));
            }
            continue;
        }
        let want = uuid.as_ref().map(|u| u.to_ascii_lowercase());
        if uuid_at(sys, node) != want || want.is_none() {
            bad.push(format!("{node} ({ty})"));
        }
    }
    bad.extend(built.datasets.iter().filter(|d| !datasets.contains(d)).cloned());
    let total = built.filesystems.len() + built.datasets.len();
    let st = if !sys.privileged && !built.filesystems.is_empty() { State::Unknown } else { tally(total - bad.len(), total) };
    out.push((Stage::Format, st, if bad.is_empty() { String::new() } else { format!("not made yet: {}", bad.join(", ")) }));

    // mount: the tree under --target.
    let mounted: Vec<&str> = system.mounts.iter().map(|m| m.target.as_str()).collect();
    let unmounted: Vec<String> = built.mount_points.iter().filter(|p| !mounted.contains(&p.as_str())).cloned().collect();
    let total = built.mount_points.len();
    out.push((Stage::Mount, tally(total - unmounted.len(), total), if unmounted.is_empty() { String::new() } else { format!("not mounted: {}", unmounted.join(", ")) }));
    let tree_up = total > 0 && unmounted.is_empty();

    // restore and boot: what's in the mounted tree.
    let t = &opts.target;
    // (lstat only: following /etc/os-release, a symlink, would bump its atime.)
    // On ostree, the physical root has no etc: the repository's config.
    let restored = tree_up
        && match crate::other::Family::of_layout(&layout.v) {
            None if crate::load::ostree::of(&layout.v).is_none() => sys.exists(&format!("{t}/etc/fstab")) && sys.exists(&format!("{t}/etc/os-release")),
            _ => sys.exists(&format!("{t}/{}", crate::other::restored_marker(&layout.v))),
        };
    // Not from the tree: a backup of a machine restored before holds that
    // restore's /var/log/bko-holt, so the log there says nothing about this one.
    let booted = tree_up && sys.exists(&boot_marker());
    let unseen = if tree_up { "" } else { "the tree isn't mounted, so this can't be seen" };
    out.push((Stage::Restore, if restored { State::Done } else if tree_up { State::NotDone } else { State::Unknown }, if restored { String::new() } else { unseen.into() }));
    // Sculpt's boot stage has nothing to do: its boot areas came back with
    // the partition table.
    if crate::other::Family::of_layout(&layout.v) == Some(crate::other::Family::Genode) {
        out.push((Stage::Boot, State::Nothing, String::new()));
    } else {
        out.push((Stage::Boot, if booted { State::Done } else if tree_up { State::NotDone } else { State::Unknown }, if booted { String::new() } else { unseen.into() }));
    }

    // close: nothing of ours left open, after everything was made.
    // Closed, pools and volumes can't be looked into: what was made shows as
    // each partition's signature (filesystem, md, LUKS, LVM or ZFS label)
    // carrying the source's UUID.
    let open = unmounted.len() < total || built.made.iter().any(|(_, n)| sys.exists(n)) || built.pools.iter().any(|p| imported.contains(p));
    let mut unsigned = vec![];
    for (src, tgt) in &mapping.pairs {
        let Some(d) = layout.disk(src) else { continue };
        for p in &d.parts {
            let ty = layout.content(&p.name).and_then(|c| c.get("type")).and_then(|t| t.as_str());
            // illumos can't give a pool the source's GUID.
            let illumos = crate::other::Family::of_layout(&layout.v) == Some(crate::other::Family::Illumos);
            if matches!(ty, Some("ufs" | "hammer2" | "befs" | "redoxfs")) || (illumos && ty == Some("zfs_member")) {
                continue;
            }
            let Some(want) = layout.content(&p.name).and_then(|c| c.get("uuid")).and_then(|u| u.as_str().map(str::to_string).or_else(|| u.as_u64().map(|n| n.to_string()))) else { continue };
            let node = commands::part_node(layout, d, tgt, p.number);
            if uuid_at(sys, &node) != Some(want.to_ascii_lowercase()) {
                unsigned.push(node);
            }
        }
    }
    let partitioned = out.iter().find(|r| r.0 == Stage::Partition).is_some_and(|r| r.1 == State::Done);
    let closed = partitioned && unsigned.is_empty() && !open;
    let why = if closed || !partitioned {
        String::new()
    } else if open {
        "still open".into()
    } else {
        format!("not made: {}", unsigned.join(", "))
    };
    out.push((Stage::Close, if closed { State::Done } else { State::NotDone }, why));

    let mut problems = mapping.problems;
    problems.retain(|p| !p.contains("isn't blank"));
    (out, problems)
}

pub fn run(layout: &Layout, opts: &Options, sys: &Sys) -> (u8, String, String) {
    let (rows, problems) = rows(layout, opts, sys);
    let mut out = format!("# bko-holt load status for {} (target tree {})\n", opts.layout, opts.target);
    if !problems.is_empty() {
        out += "# The disks can't all be matched to the layout:\n";
        for p in &problems {
            out += &format!("#   - {p}\n");
        }
        return (EXIT_PLAN_PROBLEMS, out, "bko-holt load: status: see the problems above\n".into());
    }
    for (stage, state, why) in &rows {
        out += &format!("{:<10} {:<14}{}\n", stage.name(), state.word(), if why.is_empty() { String::new() } else { format!(" ({why})") });
    }
    // Before close, the next stage is the first one not done; once the
    // tree's closed, close counts as the last one done.
    let next = rows.iter().take_while(|r| r.0 != Stage::Close).find(|r| !matches!(r.1, State::Done | State::Nothing)).map(|r| r.0);
    match next {
        _ if rows.last().is_some_and(|r| r.1 == State::Done) => out += "next: nothing; every stage is done\n",
        Some(s) => out += &format!("next: {}\n", s.name()),
        None => out += "next: close\n",
    }
    (EXIT_OK, out, String::new())
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::load::layout::sample;
    use crate::load::{parse_args, Parsed};
    use crate::sys::FakeRunner;
    use crate::testutil::Tree;

    #[test]
    fn blank_disk_means_partition_next() {
        let t = Tree::new("load-status");
        t.disk("vdb", "253:16", &[], 512).file("/sys/block/vdb/size", "33554432\n");
        t.file("/proc/self/mountinfo", "").file("/proc/swaps", "Filename\tType\tSize\tUsed\tPriority\n");
        let s = Sys::new(t.root(), Box::new(FakeRunner::default()), true);
        let l = Layout::from_json(crate::json::parse(&sample::bios_mbr()).unwrap()).unwrap();
        let o = match parse_args(&["l.json".into(), "--map".into(), "vda=vdb".into()]).unwrap() {
            Parsed::Run(o) => o,
            _ => unreachable!(),
        };
        let (code, out, _) = run(&l, &o, &s);
        assert_eq!(code, EXIT_OK, "{out}");
        assert!(out.contains("partition  not done"), "{out}");
        assert!(out.contains("assemble   nothing to do"), "{out}");
        assert!(out.contains("next: partition\n"), "{out}");
    }

    /// The sample layout's MBR with its root partition `root` sectors long:
    /// as a fit of it wrote the table, or (any other number) as nothing did.
    fn mbr_with(root: u64) -> Vec<u8> {
        use crate::parttable::build::mbr_entry;
        let mut s = vec![0u8; 512];
        s[440..444].copy_from_slice(&0x1f2e_3d4cu32.to_le_bytes());
        mbr_entry(&mut s, 0, 0x80, 0x83, 2048, 1953792);
        mbr_entry(&mut s, 1, 0, 0x82, 1955840, 3905536);
        mbr_entry(&mut s, 2, 0, 0x83, 5861376, root as u32);
        s
    }

    /// An MBR as a fit of the sample layout wrote it: the root partition a
    /// GiB smaller, everything else where the source had it.
    fn fitted_mbr() -> Vec<u8> {
        mbr_with(27693056 - 2097152)
    }

    #[test]
    fn a_fitted_table_needs_the_same_flag_again() {
        let t = Tree::new("load-status-fit");
        t.disk("vdb", "253:16", &fitted_mbr(), 512).file("/sys/block/vdb/size", format!("{}\n", 33554432 - 2097152));
        t.file("/proc/self/mountinfo", "").file("/proc/swaps", "Filename\tType\tSize\tUsed\tPriority\n");
        let s = Sys::new(t.root(), Box::new(FakeRunner::default()), true);
        let l = Layout::from_json(crate::json::parse(&sample::fittable()).unwrap()).unwrap();
        let o = |a: &[&str]| {
            let mut v: Vec<String> = vec!["l.json".into(), "--map".into(), "vda=vdb".into()];
            v.extend(a.iter().map(|x| x.to_string()));
            match parse_args(&v).unwrap() {
                Parsed::Run(o) => o,
                _ => unreachable!(),
            }
        };
        // With the same --fit-largest, the table there is the planned one.
        let (code, out, _) = run(&l, &o(&["--fit-largest", "10"]), &s);
        assert_eq!(code, EXIT_OK, "{out}");
        assert!(out.contains("partition  done"), "{out}");
        // Without it, every stage would read the disk as the source's.
        let (code, out, err) = run(&l, &o(&[]), &s);
        assert_eq!(code, EXIT_PLAN_PROBLEMS, "{out}");
        assert!(out.contains("the table there is this layout's, fitted to the disk (vda3 is 1 GiB smaller there)"), "{out}");
        assert!(out.contains("run the stages with the --fit-largest PCT that wrote it, or --erase /dev/vdb"), "{out}");
        assert!(err.contains("see the problems above"));
        // Nor is another percentage the one that wrote it: 20% would take
        // the same GiB, so that one is the planned table too.
        let (code, out, _) = run(&l, &o(&["--fit-largest", "20"]), &s);
        assert_eq!(code, EXIT_OK, "{out}");
        assert!(out.contains("partition  done"), "{out}");
    }

    /// A rescue system whose vdb is a GiB bigger than the sample layout's
    /// source disk and holds `table`.
    fn bigger_target(table: Vec<u8>) -> Tree {
        let t = Tree::new("load-status-fit-bigger");
        t.disk("vdb", "253:16", &table, 512).file("/sys/block/vdb/size", format!("{}\n", 33554432 + 2097152));
        t.file("/proc/self/mountinfo", "").file("/proc/swaps", "Filename\tType\tSize\tUsed\tPriority\n");
        t
    }

    fn fit_opts(a: &[&str]) -> Options {
        let mut v: Vec<String> = vec!["l.json".into(), "--map".into(), "vda=vdb".into()];
        v.extend(a.iter().map(|x| x.to_string()));
        match parse_args(&v).unwrap() {
            Parsed::Run(o) => o,
            _ => unreachable!(),
        }
    }

    #[test]
    fn a_grown_table_is_cleared_by_erase() {
        // A target a GiB bigger holding this layout's table with the root
        // grown into it: --fit-largest 10% writes exactly that table, so
        // without the flag every later stage would misread the disk.
        let l = Layout::from_json(crate::json::parse(&sample::fittable()).unwrap()).unwrap();
        let t = bigger_target(mbr_with(27693056 + 2097152));
        let s = Sys::new(t.root(), Box::new(FakeRunner::default()), true);
        let (code, out, _) = run(&l, &fit_opts(&["--fit-largest", "10"]), &s);
        assert_eq!(code, EXIT_OK, "{out}");
        assert!(out.contains("partition  done"), "{out}");
        let (code, out, _) = run(&l, &fit_opts(&[]), &s);
        assert_eq!(code, EXIT_PLAN_PROBLEMS, "{out}");
        assert!(out.contains("vda3 is 1 GiB bigger there"), "{out}");
        // --erase wipes that table, which is what the refusal asks for.
        let (code, out, _) = run(&l, &fit_opts(&["--erase", "/dev/vdb"]), &s);
        assert_eq!(code, EXIT_OK, "{out}");
        assert!(out.contains("partition  not done"), "{out}");
        assert!(!out.contains("fitted to the disk"), "{out}");
    }

    #[test]
    fn a_table_no_fit_wrote_keeps_its_row() {
        // The same layout's table with its root grown by a megabyte: no
        // --fit-largest percentage writes that, so it isn't one of ours and
        // the rows are the ones a table that isn't the source's always got.
        let l = Layout::from_json(crate::json::parse(&sample::fittable()).unwrap()).unwrap();
        let t = bigger_target(mbr_with(27693056 + 2048));
        let s = Sys::new(t.root(), Box::new(FakeRunner::default()), true);
        let (code, out, _) = run(&l, &fit_opts(&[]), &s);
        assert_eq!(code, EXIT_OK, "{out}");
        assert!(out.contains("partition  not done"), "{out}");
        assert!(!out.contains("fitted to the disk"), "{out}");
    }

    #[test]
    fn a_restored_log_does_not_mean_the_boot_stage_ran() {
        let t = Tree::new("load-status-boot");
        t.disk("vdb", "253:16", &[], 512).file("/sys/block/vdb/size", "33554432\n");
        t.file("/proc/self/mountinfo", "36 35 253:17 / /mnt/bko-target rw,relatime - btrfs /dev/vdb2 rw\n37 36 253:18 / /mnt/bko-target/boot rw,relatime - ext4 /dev/vdb1 rw\n").file("/proc/swaps", "Filename\tType\tSize\tUsed\tPriority\n");
        t.file("/mnt/bko-target/etc/fstab", "").file("/mnt/bko-target/etc/os-release", "ID=debian\n");
        // What a backup of a machine that was itself restored brings with it.
        t.file("/mnt/bko-target/var/log/bko-holt/load.log", "==== stage boot done\n");
        let s = Sys::new(t.root(), Box::new(FakeRunner::default()), true);
        let l = Layout::from_json(crate::json::parse(&sample::bios_mbr()).unwrap()).unwrap();
        let o = match parse_args(&["l.json".into(), "--map".into(), "vda=vdb".into()]).unwrap() {
            Parsed::Run(o) => o,
            _ => unreachable!(),
        };
        let boot = || rows(&l, &o, &s).0.into_iter().find(|r| r.0 == Stage::Boot).unwrap().1;
        assert_eq!(boot(), State::NotDone);
        t.file(&boot_marker(), "");
        assert_eq!(boot(), State::Done);
    }

    #[test]
    fn tallies() {
        assert_eq!(tally(0, 0), State::Nothing);
        assert_eq!(tally(2, 2), State::Done);
        assert_eq!(tally(0, 2), State::NotDone);
        assert_eq!(tally(1, 2), State::Partly);
    }
}
