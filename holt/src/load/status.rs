//! `bko-holt load --only status`: what's on the target disks now, read from
//! the disks themselves (not from a state file), and which stage is next.

use super::commands;
use super::layout::Layout;
use super::target::{self, table_id};
use super::{Options, Stage, EXIT_OK, EXIT_PLAN_PROBLEMS};
use crate::sys::Sys;

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

/// The UUID of what's on a device node, read from the device.
fn uuid_at(sys: &Sys, node: &str) -> Option<String> {
    let src = sys.open_dev(node)?;
    crate::probe::probe_all(&src).into_iter().find_map(|c| c.uuid).map(|u| u.to_ascii_lowercase())
}

pub fn rows(layout: &Layout, opts: &Options, sys: &Sys) -> (Vec<(Stage, State, String)>, Vec<String>) {
    let system = target::scan(sys, opts, &target::layout_pools(layout));
    let mapping = target::map(layout, opts, &system, sys);
    let built = commands::build(layout, opts, &mapping);
    let mut out = Vec::new();

    // partition: each table and partition as the source had them.
    let (mut ok, mut total, mut why) = (0, 0, vec![]);
    for (src, tgt) in &mapping.pairs {
        let (Some(d), Some(t)) = (layout.disk(src), system.disk(tgt)) else { continue };
        let Some(want) = &d.table else { continue };
        total += 1;
        match &t.table {
            None => why.push(format!("/dev/{tgt}: no partition table")),
            Some(have) => {
                let ext = have.extents();
                let id_ok = want.id.as_ref().is_none_or(|id| table_id(have).eq_ignore_ascii_case(id));
                let parts_ok = d.parts.iter().all(|p| ext.iter().any(|&(n, s, len)| n == p.number && s == p.start_lba && len == p.sectors));
                if id_ok && parts_ok {
                    ok += 1;
                } else {
                    why.push(format!("/dev/{tgt}: its table isn't the source's"));
                }
            }
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
    let restored = tree_up && sys.exists(&format!("{t}/etc/fstab")) && sys.exists(&format!("{t}/etc/os-release"));
    let booted = tree_up && !sys.list(&format!("{t}/var/log/bko-holt")).is_empty();
    let unseen = if tree_up { "" } else { "the tree isn't mounted, so this can't be seen" };
    out.push((Stage::Restore, if restored { State::Done } else if tree_up { State::NotDone } else { State::Unknown }, if restored { String::new() } else { unseen.into() }));
    out.push((Stage::Boot, if booted { State::Done } else if tree_up { State::NotDone } else { State::Unknown }, if booted { String::new() } else { unseen.into() }));

    // close: nothing of ours left open, after everything was made.
    // Closed, pools and volumes can't be looked into: what was made shows as
    // each partition's signature (filesystem, md, LUKS, LVM or ZFS label)
    // carrying the source's UUID.
    let open = unmounted.len() < total || built.made.iter().any(|(_, n)| sys.exists(n)) || built.pools.iter().any(|p| imported.contains(p));
    let mut unsigned = vec![];
    for (src, tgt) in &mapping.pairs {
        let Some(d) = layout.disk(src) else { continue };
        for p in &d.parts {
            let Some(want) = layout.content(&p.name).and_then(|c| c.get("uuid")).and_then(|u| u.as_str().map(str::to_string).or_else(|| u.as_u64().map(|n| n.to_string()))) else { continue };
            let node = commands::part_path(tgt, p.number);
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

    #[test]
    fn tallies() {
        assert_eq!(tally(0, 0), State::Nothing);
        assert_eq!(tally(2, 2), State::Done);
        assert_eq!(tally(0, 2), State::NotDone);
        assert_eq!(tally(1, 2), State::Partly);
    }
}
