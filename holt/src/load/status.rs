//! `bko-holt load --only status`: what's on the target disks now, read from
//! the disks themselves, and which stage is next.  The boot stage is the one
//! exception, since nothing on the disks can tell this restore's boot stage
//! from the one before the backup: it's a mark in RUN_DIR, so after the rescue
//! system reboots the stage runs again, which is harmless.

use super::codes::{Problem, ProblemCode, Problems};
use super::commands;
use super::layout::Layout;
use super::target;
use super::{EXIT_OK, EXIT_PLAN_PROBLEMS, Options, RUN_DIR, Stage};
use crate::json::{Obj, Value};
use crate::other::Family;
use crate::sys::Sys;
use std::borrow::Cow;

/// Exists once the boot stage has run for the tree restored in this RUN_DIR.
pub fn boot_marker() -> String {
    format!("{RUN_DIR}/boot.done")
}

/// The copy marks (holt-json.md, J10), beside boot.done and never in the
/// restored tree: restore.started is written as the restore stage begins
/// and restore.done after its last command and its read-back, both with
/// restore_mark_text.  A partition stage that runs removes both (the disks
/// are new again).  Written in text mode too, where nothing reads them, so
/// a --json run after a text one sees the truth; under --json the restore
/// row is done only with restore.done, so a stopped or failed copy always
/// runs again.
pub fn restore_started_marker() -> String {
    format!("{RUN_DIR}/restore.started")
}

/// The copy's complete mark: see restore_started_marker.
pub fn restore_done_marker() -> String {
    format!("{RUN_DIR}/restore.done")
}

/// What the copy marks say: the layout (its host and dump time) and each
/// source disk with the target it went to.
pub fn restore_mark_text(layout: &Layout, mapping: &target::Mapping) -> String {
    let mut s = format!(
        "layout {} {}\n",
        layout.hostname(),
        layout.str_at(&["generated"]).unwrap_or("-")
    );
    for (src, tgt) in &mapping.pairs {
        s += &format!("target {src} {tgt}\n");
    }
    s
}

/// The targets restore.started names: a restore began on each in this
/// session, whatever layout's.
pub fn restore_started_on(sys: &Sys) -> Vec<String> {
    let Some(text) = sys.read(&restore_started_marker()) else {
        return Vec::new();
    };
    text.lines()
        .filter_map(|l| l.strip_prefix("target "))
        .filter_map(|r| r.split_whitespace().nth(1))
        .map(str::to_string)
        .collect()
}

/// Whether restore.started is this layout's.
fn restore_started_here(layout: &Layout, sys: &Sys) -> bool {
    let want = format!(
        "layout {} {}",
        layout.hostname(),
        layout.str_at(&["generated"]).unwrap_or("-")
    );
    sys.read(&restore_started_marker())
        .is_some_and(|t| t.lines().next() == Some(want.as_str()))
}

/// Whether restore.done says this layout's copy onto these targets ran to
/// its end in this session.
fn restore_complete(layout: &Layout, mapping: &target::Mapping, sys: &Sys) -> bool {
    sys.read(&restore_done_marker())
        .is_some_and(|t| t == restore_mark_text(layout, mapping))
}

/// The file a container's temporary key is in (KeyAsk's `temporary`: the
/// source unlocked it with a key file the restore brings back), as the
/// builders name it.  A ZFS encryption root's is load's random key, named
/// as Builder::new_key and the keylocation name it.
fn temporary_key(k: &commands::KeyAsk) -> Option<String> {
    match k.kind {
        "luks" => commands::temp_key_name(k.names.first()?).map(|n| format!("{RUN_DIR}/keys/{n}")),
        "geli" => Some(crate::other::geli::tmp_key(k.devices.first()?)),
        "zfs" => Some(format!(
            "{RUN_DIR}/keys/{}",
            k.names.first()?.replace('/', "_")
        )),
        _ => None,
    }
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

    /// The word the status document gives it: State::word in snake case.
    pub fn json_word(self) -> &'static str {
        match self {
            State::Done => "done",
            State::Partly => "partly",
            State::NotDone => "not_done",
            State::Nothing => "nothing",
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

/// The device to read what's on a node from, for a layout of family FAM.
/// OpenBSD won't open the block device of a mounted filesystem (EBUSY):
/// once mount had run, format read as not done, /dev/sd1a or a softraid
/// volume's 739d98841b65e745.a (/dev/sd3a) unreadable.  There it's the raw
/// device (/dev/rsd3a), which reads the same; open_dev sizes it.
fn read_path(sys: &Sys, fam: Option<Family>, node: &str) -> String {
    let p = crate::other::node_path(sys, node);
    match fam {
        Some(Family::OpenBsd) if p.starts_with("/dev/") => p.replacen("/dev/", "/dev/r", 1),
        _ => p,
    }
}

/// What's on a device node (its blkid TYPE), read from the device.
pub(crate) fn kind_at(sys: &Sys, fam: Option<Family>, node: &str) -> Option<String> {
    let src = sys.open_dev(&read_path(sys, fam, node))?;
    crate::probe::probe_all(&src)
        .into_iter()
        .next()
        .map(|c| c.kind)
}

/// The UUID of what's on a device node, read from the device.
fn uuid_at(sys: &Sys, fam: Option<Family>, node: &str) -> Option<String> {
    let src = sys.open_dev(&read_path(sys, fam, node))?;
    crate::probe::probe_all(&src)
        .into_iter()
        .find_map(|c| c.uuid)
        .map(|u| u.to_ascii_lowercase())
}

/// The mount points of the tree still to mount, of UNMOUNTED (those not
/// mounted now): the inactive boot environments' under T/run/bko-be
/// (load/zfs_be.rs) are the restore's only, and the boot stage unmounts
/// them first, so once it has run (BOOT_RAN) they count as done.
fn tree_unmounted(unmounted: &[String], target: &str, boot_ran: bool) -> Vec<String> {
    let be_dir = format!("{target}{}", crate::zfs_be::BE_DIR);
    unmounted
        .iter()
        .filter(|p| !(boot_ran && (**p == be_dir || p.starts_with(&format!("{be_dir}/")))))
        .cloned()
        .collect()
}

pub fn rows(
    layout: &Layout,
    opts: &Options,
    sys: &Sys,
) -> (Vec<(Stage, State, String)>, Vec<String>) {
    let r = read(layout, opts, sys);
    (r.rows, r.problems.texts())
}

/// What status reads, and what it read it against: the rows, the mapping's
/// problems (not-blank left out), and the disks, mapping, layout and
/// commands they come from, for the status document.
pub(crate) struct Reading<'a> {
    pub rows: Vec<(Stage, State, String)>,
    pub problems: Problems,
    pub system: target::System,
    pub mapping: target::Mapping,
    /// The layout as the stages build from it (--fit-largest's, --pools').
    pub layout: Cow<'a, Layout>,
    pub built: commands::Built,
    /// The layout's pools imported from the target disks.
    pub imported: Vec<String>,
}

pub(crate) fn read<'a>(given: &'a Layout, opts: &Options, sys: &Sys) -> Reading<'a> {
    // The disks hold what --fit-largest made of the layout, so that's the
    // one every row is read against (target::prepare, fit.rs).
    let target::Prepared {
        system,
        mapping,
        layout: prepared,
        ..
    } = target::prepare(given, opts, sys);
    let layout = &*prepared;
    let built = commands::build(layout, opts, &mapping);
    let mut out = Vec::new();

    // partition: each table and partition as the source had them.
    let (mut ok, mut total, mut why) = (0, 0, vec![]);
    for (src, tgt) in &mapping.pairs {
        let (Some(d), Some(t)) = (layout.disk(src), system.disk(tgt)) else {
            continue;
        };
        // A disk with no table has nothing to check, unless it's named with
        // --erase: then the stage that wipes it is done only by its record.
        if d.table.is_none() && !target::is_erased(opts, t) {
            continue;
        }
        total += 1;
        // The same check decides whether --erase lets go of what holds a
        // target (target::release_holders).  With --erase, the source's
        // table counts only when this session's partition stage wrote it:
        // otherwise it's the source's own disk as found.
        if target::partition_counts_done(layout, d, t, opts, sys) {
            ok += 1;
        } else if target::unrecorded(layout, d, t, opts, sys) {
            why.push(if d.table.is_some() {
                format!("/dev/{tgt}: the source's table, but no record that load wrote it, and --erase wipes it")
            } else {
                format!("/dev/{tgt}: no record that load wiped it, and --erase wipes it")
            });
        } else if t.table.is_none() {
            why.push(format!("/dev/{tgt}: no partition table"));
        } else {
            why.push(format!("/dev/{tgt}: its table isn't the source's"));
        }
    }
    let st = if !sys.privileged && total > 0 {
        State::Unknown
    } else {
        tally(ok, total)
    };
    out.push((Stage::Partition, st, why.join("; ")));

    // assemble: the device nodes and pools.
    // A pool counts when it's imported from the target disks; the system
    // load runs on may have one of the same name of its own.
    let targets: Vec<&str> = mapping.pairs.iter().map(|(_, t)| t.as_str()).collect();
    let on_targets = |dev: &str| target::vdev_on_targets(&system, &targets, dev);
    let imported: Vec<String> = system
        .pools
        .iter()
        .filter(|p| !p.devices.is_empty() && p.devices.iter().all(|d| on_targets(d)))
        .map(|p| p.name.clone())
        .collect();
    let mut missing: Vec<String> = built
        .made
        .iter()
        .filter(|(_, node)| !sys.exists(&crate::other::node_path(sys, node)))
        .map(|(_, n)| n.clone())
        .collect();
    missing.extend(
        built
            .pools
            .iter()
            .filter(|p| !imported.contains(p))
            .map(|p| format!("pool {p}")),
    );
    let total = built.made.len() + built.pools.len();
    out.push((
        Stage::Assemble,
        tally(total - missing.len(), total),
        if missing.is_empty() {
            String::new()
        } else {
            format!("missing: {}", missing.join(", "))
        },
    ));

    // format: each filesystem's UUID on its device, and the ZFS datasets.
    let datasets: Vec<String> = sys
        .run("zfs", &["list", "-H", "-o", "name"])
        .map(|o| {
            o.lines()
                .map(|l| l.trim().to_string())
                .filter(|d| {
                    imported
                        .iter()
                        .any(|p| d == p || d.starts_with(&format!("{p}/")))
                })
                .collect()
        })
        .unwrap_or_default();
    let fam = Family::of_layout(&layout.v);
    let mut bad = vec![];
    for (node, ty, uuid) in &built.filesystems {
        // UFS and HAMMER2 can't be given the source's UUID: the type shows it.
        if matches!(ty.as_str(), "ufs" | "hammer2" | "befs" | "redoxfs") {
            if kind_at(sys, fam, node).as_deref() != Some(ty.as_str()) {
                bad.push(format!("{node} ({ty})"));
            }
            continue;
        }
        let want = uuid.as_ref().map(|u| u.to_ascii_lowercase());
        if uuid_at(sys, fam, node) != want || want.is_none() {
            bad.push(format!("{node} ({ty})"));
        }
    }
    bad.extend(
        built
            .datasets
            .iter()
            .filter(|d| !datasets.contains(d))
            .cloned(),
    );
    let total = built.filesystems.len() + built.datasets.len();
    let st = if !sys.privileged && !built.filesystems.is_empty() {
        State::Unknown
    } else {
        tally(total - bad.len(), total)
    };
    out.push((
        Stage::Format,
        st,
        if bad.is_empty() {
            String::new()
        } else {
            format!("not made yet: {}", bad.join(", "))
        },
    ));

    // mount: the tree under --target.
    let mounted: Vec<&str> = system.mounts.iter().map(|m| m.target.as_str()).collect();
    let unmounted_all: Vec<String> = built
        .mount_points
        .iter()
        .filter(|p| !mounted.contains(&p.as_str()))
        .cloned()
        .collect();
    let unmounted = tree_unmounted(&unmounted_all, &opts.target, sys.exists(&boot_marker()));
    let total = built.mount_points.len();
    out.push((
        Stage::Mount,
        tally(total - unmounted.len(), total),
        if unmounted.is_empty() {
            String::new()
        } else {
            format!("not mounted: {}", unmounted.join(", "))
        },
    ));
    let tree_up = total > 0 && unmounted.is_empty();

    // restore and boot: what's in the mounted tree.
    let t = &opts.target;
    // (lstat only: following /etc/os-release, a symlink, would bump its atime.)
    // On ostree, the physical root has no etc: the repository's config.  On
    // ABRoot, the present root's abimage.abr (its raw etc is the image's).
    // On Ubuntu Core, ubuntu-data's modeenv (it has no `/` of its own).
    let restored = tree_up
        && match crate::other::Family::of_layout(&layout.v) {
            None if crate::load::ostree::of(&layout.v).is_none()
                && crate::load::abroot::of(&layout.v).is_none()
                && crate::load::snapcore::of(&layout.v).is_none() =>
            {
                sys.exists(&format!("{t}/etc/fstab")) && sys.exists(&format!("{t}/etc/os-release"))
            }
            _ => sys.exists(&format!("{t}/{}", crate::other::restored_marker(&layout.v))),
        };
    // Not from the tree: a backup of a machine restored before holds that
    // restore's /var/log/bko-holt, so the log there says nothing about this one.
    let booted = tree_up && sys.exists(&boot_marker());
    let unseen = if tree_up {
        ""
    } else {
        "the tree isn't mounted, so this can't be seen"
    };
    // Under --json the tree's files aren't enough (a stopped copy has them,
    // and so has the old system on its own disk): this session's complete
    // mark is needed too (J10).  Text mode's row is as it was.
    if opts.json && restored && !restore_complete(layout, &mapping, sys) {
        out.push((
            Stage::Restore,
            State::Partly,
            "the copy didn't finish in this session".into(),
        ));
    } else {
        out.push((
            Stage::Restore,
            if restored {
                State::Done
            } else if tree_up {
                State::NotDone
            } else {
                State::Unknown
            },
            if restored {
                String::new()
            } else {
                unseen.into()
            },
        ));
    }
    // Sculpt's boot stage has nothing to do: its boot areas came back with
    // the partition table.
    if crate::other::Family::of_layout(&layout.v) == Some(crate::other::Family::Genode) {
        out.push((Stage::Boot, State::Nothing, String::new()));
    } else {
        out.push((
            Stage::Boot,
            if booted {
                State::Done
            } else if tree_up {
                State::NotDone
            } else {
                State::Unknown
            },
            if booted { String::new() } else { unseen.into() },
        ));
    }

    // close: nothing of ours left open, after everything was made.
    // Closed, pools and volumes can't be looked into: what was made shows as
    // each partition's signature (filesystem, md, LUKS, LVM or ZFS label)
    // carrying the source's UUID.
    // (An OpenBSD softraid volume stays attached after close, as
    // other/softraid.rs says: its partitions don't count as open.)
    let open = unmounted_all.len() < total
        || built.made.iter().any(|(_, n)| {
            !crate::other::softraid::is_volume_node(n)
                && sys.exists(&crate::other::node_path(sys, n))
        })
        || built.pools.iter().any(|p| imported.contains(p));
    // Bug: status::rows checked only partitioned disks (`d.parts`), skipped
    // whole-disk filesystems/containers (`table: null`) and non-UUID leaves, and
    // required Stage::Partition == Done for Stage::Close. On whole-disk layouts,
    // `--erase` reported every stage done right after partition wiped the disk,
    // while without `--erase` Partition is Nothing so Close stayed NotDone
    // forever and failed exec's post-stage check. Probe whole-disk and non-UUID
    // leaves too, and accept Partition == Nothing once signatures are verified.
    let mut unsigned = vec![];
    let mut checked = 0usize;
    let illumos = fam == Some(Family::Illumos);
    let mut check_raw = |name: &str, node: String| {
        let ty = layout
            .content(name)
            .and_then(|c| c.get("type"))
            .and_then(|t| t.as_str());
        if matches!(ty, Some("ufs" | "hammer2" | "befs" | "redoxfs")) {
            checked += 1;
            if kind_at(sys, fam, &node).as_deref() != ty {
                unsigned.push(node);
            }
            return;
        }
        if illumos && ty == Some("zfs_member") {
            checked += 1;
            if kind_at(sys, fam, &node).as_deref() != Some("zfs_member") {
                unsigned.push(node);
            }
            return;
        }
        let Some(want) = layout
            .content(name)
            .and_then(|c| c.get("uuid"))
            .and_then(|u| {
                u.as_str()
                    .map(str::to_string)
                    .or_else(|| u.as_u64().map(|n| n.to_string()))
            })
        else {
            return;
        };
        checked += 1;
        if uuid_at(sys, fam, &node) != Some(want.to_ascii_lowercase()) {
            unsigned.push(node);
        }
    };
    for (src, tgt) in &mapping.pairs {
        let Some(d) = layout.disk(src) else { continue };
        if d.table.is_none() && !d.content.is_null() {
            let node = if illumos {
                format!("/dev/dsk/{tgt}")
            } else {
                format!("/dev/{tgt}")
            };
            check_raw(&d.name, node);
        }
        for p in &d.parts {
            check_raw(&p.name, commands::part_node(layout, d, tgt, p.number));
        }
    }
    let partitioned = out
        .iter()
        .find(|r| r.0 == Stage::Partition)
        .is_some_and(|r| {
            r.1 == State::Done || (r.1 == State::Nothing && sys.privileged && checked > 0)
        });
    let closed = partitioned && unsigned.is_empty() && !open;
    let why = if closed || !partitioned {
        String::new()
    } else if open {
        "still open".into()
    } else {
        format!("not made: {}", unsigned.join(", "))
    };
    out.push((
        Stage::Close,
        if closed { State::Done } else { State::NotDone },
        why,
    ));

    let mut problems = mapping.problems.clone();
    problems.retain(|p| !p.contains("isn't blank"));
    Reading {
        rows: out,
        problems,
        system,
        mapping,
        layout: prepared,
        built,
        imported,
    }
}

/// The stage text mode names after `next:`: before close, the first one
/// not done; once the tree's closed, close counts as the last one done,
/// and there's none (None: "nothing; every stage is done").
fn next_stage(rows: &[(Stage, State, String)]) -> Option<Stage> {
    if rows.last().is_some_and(|r| r.1 == State::Done) {
        return None;
    }
    Some(
        rows.iter()
            .take_while(|r| r.0 != Stage::Close)
            .find(|r| !matches!(r.1, State::Done | State::Nothing))
            .map_or(Stage::Close, |r| r.0),
    )
}

pub fn run(layout: &Layout, opts: &Options, sys: &Sys) -> (u8, String, String) {
    let (rows, problems) = rows(layout, opts, sys);
    let mut out = format!(
        "# bko-holt load status for {} (target tree {})\n",
        opts.layout, opts.target
    );
    if !problems.is_empty() {
        out += "# The disks can't all be matched to the layout:\n";
        for p in &problems {
            out += &format!("#   - {p}\n");
        }
        return (EXIT_PLAN_PROBLEMS, out, STATUS_PROBLEMS.into());
    }
    for (stage, state, why) in &rows {
        out += &format!(
            "{:<10} {:<14}{}\n",
            stage.name(),
            state.word(),
            if why.is_empty() {
                String::new()
            } else {
                format!(" ({why})")
            }
        );
    }
    match next_stage(&rows) {
        None => out += "next: nothing; every stage is done\n",
        Some(s) => out += &format!("next: {}\n", s.name()),
    }
    (EXIT_OK, out, String::new())
}

/// What status prints on standard error when the disks can't be matched.
const STATUS_PROBLEMS: &str = "bko-holt load: status: see the problems above\n";

/// Under --json, the restore that began on a target in this session
/// (restore.started) is never made anew under (J19): a run in which
/// assemble or format would run, with no partition stage of its own wiping
/// the targets first, is refused on each target the restore began on, a
/// `remake_after_restore` per stage.  WOULD is what the run would run
/// (plan::would_run).
pub(crate) fn remake_problems(
    mapping: &target::Mapping,
    system: &target::System,
    would: &super::plan::WouldRun,
    sys: &Sys,
) -> Vec<Problem> {
    if would.partition || would.remaking.is_empty() {
        return Vec::new();
    }
    let begun = restore_started_on(sys);
    let mut out = Vec::new();
    for (src, tgt) in mapping.pairs.iter().filter(|(_, t)| begun.contains(t)) {
        let path = system
            .disk(tgt)
            .map_or_else(|| format!("/dev/{tgt}"), |t| t.path());
        for st in &would.remaking {
            let text = format!(
                "{src} -> {path}: {} would make what it makes anew there, over the copy this session's restore began on it; to start again, run --only partition with --erase {path}, which wipes it first",
                st.name()
            );
            out.push(
                Problem::new(ProblemCode::RemakeAfterRestore, text)
                    .with("disk", src)
                    .with("target", tgt)
                    .with("stage", st.name()),
            );
        }
    }
    out
}

/// `load LAYOUT --only status --json` (holt-json.md, "bko-holt-status"):
/// status's rows as a document, with the session's marks, each target's
/// identity, the temporary keys, whether "Go on" can be offered, and
/// what's open.  Standard error is text mode's for the same arguments.
pub fn json_run(layout: &Layout, opts: &Options, sys: &Sys) -> (u8, String, String) {
    let doc = status_doc(layout, opts, sys);
    let code = doc.get_u64("exit").map_or(EXIT_PLAN_PROBLEMS, |c| c as u8);
    let err = if code == EXIT_OK {
        String::new()
    } else {
        STATUS_PROBLEMS.to_string()
    };
    (code, super::jsonout::line(&doc), err)
}

/// The status document's `temporary_keys`: the builders' asks and each
/// ZFS encryption root's (as the plan's `keys` gives them) whose only key
/// is load's, with whether its file is still there; and, when LOST_MATTERS
/// (the copy began or they're open, and boot hasn't run), why each gone
/// one is no Go on.
fn temporary_keys(
    opts: &Options,
    r: &Reading,
    sys: &Sys,
    lost_matters: bool,
) -> (Vec<Value>, Vec<String>) {
    let zfs = super::jsonout::zfs_key_asks(&r.layout, opts, &r.built, &r.problems);
    let mut why = Vec::new();
    let keys = r
        .built
        .keys
        .iter()
        .chain(&zfs)
        .filter(|k| k.unlock == "temporary" && !k.given)
        .filter_map(|k| {
            let path = temporary_key(k)?;
            let name = k.names.first().cloned().unwrap_or_default();
            let kept = sys.exists(&path);
            if !kept && lost_matters {
                why.push(format!("{name}: its temporary key {path} is gone, and boot hasn't added the restored key file, so it can't be opened again"));
            }
            Some(Obj::new().set("name", name).set("kept", kept).build())
        })
        .collect();
    (keys, why)
}

fn status_doc(given: &Layout, opts: &Options, sys: &Sys) -> Value {
    let r = read(given, opts, sys);
    let layout = &*r.layout;
    let ok = r.problems.is_empty();
    let row = |s: Stage| r.rows.iter().find(|x| x.0 == s).map(|x| x.1);
    let uuids = target::layout_uuids(layout);
    let pairs: Vec<(&super::layout::Disk, &target::TargetDisk)> = r
        .mapping
        .pairs
        .iter()
        .filter_map(|(s, t)| Some((layout.disk(s)?, r.system.disk(t)?)))
        .collect();
    let recorded: Vec<&str> = pairs
        .iter()
        .filter(|(d, t)| target::load_wrote(layout, d, t, sys))
        .map(|(_, t)| t.b.name.as_str())
        .collect();
    let booted = sys.exists(&boot_marker());
    let started = restore_started_here(layout, sys);

    let stages: Vec<Value> = if ok {
        r.rows
            .iter()
            .map(|(s, st, why)| {
                Obj::new()
                    .set("name", s.name())
                    .set("state", st.json_word())
                    .set("why", why.as_str())
                    .build()
            })
            .collect()
    } else {
        Vec::new()
    };
    let next = if ok { next_stage(&r.rows) } else { None };
    let all_done = ok && next.is_none();
    let marks = Obj::new()
        .set("partitioned", recorded.clone())
        .set("restore_started", started)
        .set(
            "restore_complete",
            restore_complete(layout, &r.mapping, sys),
        )
        .set("boot", booted);
    let targets: Vec<Value> = pairs
        .iter()
        .map(|(d, t)| {
            Obj::new()
                .set("source", d.name.as_str())
                .set("node", t.b.name.as_str())
                .set("ours", t.is_ours(d, &uuids))
                .set("same_identity", target::same_identity(d, &t.b))
                .set("recorded", recorded.contains(&t.b.name.as_str()))
                .build()
        })
        .collect();

    // The containers whose only key is one load made: lost (gone before
    // boot added the restored key file) once the copy began on them or
    // they're open, since then a rerun can't make them anew.
    let assembled = matches!(row(Stage::Assemble), Some(State::Done | State::Partly));
    let (temporary, mut why) = temporary_keys(opts, &r, sys, !booted && (started || assembled));

    // Whether "Go on" (--through close as it stands) is safe: only on
    // disks this session's partition stage wrote (the old system's own
    // disk carries the layout's identifiers too, J19), no temporary key
    // lost, and nothing made anew over this session's restore (J14).
    if !ok {
        why.insert(0, "the disks can't all be matched to the layout".into());
    }
    for (_, t) in pairs
        .iter()
        .filter(|(_, t)| !recorded.contains(&t.b.name.as_str()))
    {
        why.push(format!("{}: this session has no record that load's partition stage wrote it (a reboot forgets them), so what's there can't be told from the old system's", t.path()));
    }
    let mut through = opts.clone();
    through.only = None;
    through.through = Some(Stage::Close);
    let would = super::plan::would_run(
        layout,
        &through,
        sys,
        &r.system,
        &r.mapping,
        &r.built,
        || r.rows.clone(),
    );
    for p in remake_problems(&r.mapping, &r.system, &would, sys) {
        why.push(format!("{}: the open stage isn't in this bko-holt", p.text));
    }
    let resume = Obj::new().set("go_on", why.is_empty()).set("why", why);

    // What of the layout is open now (as close reads it).
    let containers: Vec<Value> = r
        .built
        .made
        .iter()
        .filter(|(_, n)| {
            !crate::other::softraid::is_volume_node(n)
                && sys.exists(&crate::other::node_path(sys, n))
        })
        .map(|(src, node)| {
            Obj::new()
                .set("source", src.as_str())
                .set("node", node.as_str())
                .build()
        })
        .collect();
    let pools: Vec<&str> = r
        .built
        .pools
        .iter()
        .filter(|p| r.imported.contains(p))
        .map(String::as_str)
        .collect();
    let mounted_now: Vec<&str> = r.system.mounts.iter().map(|m| m.target.as_str()).collect();
    let mounted: Vec<&str> = r
        .built
        .mount_points
        .iter()
        .map(String::as_str)
        .filter(|p| mounted_now.contains(p))
        .collect();
    let open = Obj::new()
        .set("containers", containers)
        .set("pools", pools)
        .set("mounted", mounted);

    super::jsonout::header("bko-holt-status")
        .set("exit", if ok { EXIT_OK } else { EXIT_PLAN_PROBLEMS })
        .set("target", opts.target.as_str())
        .set("stages", stages)
        .set("next", next.map(Stage::name))
        .set("all_done", all_done)
        .set("marks", marks)
        .set("targets", targets)
        .set("temporary_keys", temporary)
        .set("resume", resume)
        .set("open", open)
        .set("problems", r.problems.to_json())
        .build()
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::load::layout::sample;
    use crate::load::{Parsed, parse_args};
    use crate::sys::FakeRunner;
    use crate::testutil::Tree;

    #[test]
    fn blank_disk_means_partition_next() {
        let t = Tree::new("load-status");
        t.disk("vdb", "253:16", &[], 512)
            .file("/sys/block/vdb/size", "33554432\n");
        t.file("/proc/self/mountinfo", "")
            .file("/proc/swaps", "Filename\tType\tSize\tUsed\tPriority\n");
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
        t.disk("vdb", "253:16", &fitted_mbr(), 512)
            .file("/sys/block/vdb/size", format!("{}\n", 33554432 - 2097152));
        t.file("/proc/self/mountinfo", "")
            .file("/proc/swaps", "Filename\tType\tSize\tUsed\tPriority\n");
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
        assert!(
            out.contains(
                "the table there is this layout's, fitted to the disk (vda3 is 1 GiB smaller there)"
            ),
            "{out}"
        );
        assert!(
            out.contains(
                "run the stages with the --fit-largest PCT that wrote it, or --erase /dev/vdb"
            ),
            "{out}"
        );
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
        t.disk("vdb", "253:16", &table, 512)
            .file("/sys/block/vdb/size", format!("{}\n", 33554432 + 2097152));
        t.file("/proc/self/mountinfo", "")
            .file("/proc/swaps", "Filename\tType\tSize\tUsed\tPriority\n");
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
        t.disk("vdb", "253:16", &[], 512)
            .file("/sys/block/vdb/size", "33554432\n");
        t.file("/proc/self/mountinfo", "36 35 253:17 / /mnt/bko-target rw,relatime - btrfs /dev/vdb2 rw\n37 36 253:18 / /mnt/bko-target/boot rw,relatime - ext4 /dev/vdb1 rw\n").file("/proc/swaps", "Filename\tType\tSize\tUsed\tPriority\n");
        t.file("/mnt/bko-target/etc/fstab", "")
            .file("/mnt/bko-target/etc/os-release", "ID=debian\n");
        // What a backup of a machine that was itself restored brings with it.
        t.file(
            "/mnt/bko-target/var/log/bko-holt/load.log",
            "==== stage boot done\n",
        );
        let s = Sys::new(t.root(), Box::new(FakeRunner::default()), true);
        let l = Layout::from_json(crate::json::parse(&sample::bios_mbr()).unwrap()).unwrap();
        let o = match parse_args(&["l.json".into(), "--map".into(), "vda=vdb".into()]).unwrap() {
            Parsed::Run(o) => o,
            _ => unreachable!(),
        };
        let boot = || {
            rows(&l, &o, &s)
                .0
                .into_iter()
                .find(|r| r.0 == Stage::Boot)
                .unwrap()
                .1
        };
        assert_eq!(boot(), State::NotDone);
        t.file(&boot_marker(), "");
        assert_eq!(boot(), State::Done);
    }

    #[test]
    fn a_pool_on_a_geli_provider_is_on_its_target() {
        // freebsd15-geli's restore: zroot's vdev is vtbd1p3.eli, the GELI
        // provider assemble attached on the target's vtbd1p3.
        use crate::blockdev::{BlockDev, Kind};
        let part = |n: &str| BlockDev {
            name: n.into(),
            kind: Kind::Partition,
            parent: Some("vtbd1".into()),
            ..BlockDev::default()
        };
        let disk = |n: &str, parts| target::TargetDisk {
            b: BlockDev {
                name: n.into(),
                kind: Kind::Disk,
                ..BlockDev::default()
            },
            parts,
            table: None,
            signatures: vec![],
            uuids: vec![],
            in_use: vec![],
            hard_use: vec![],
            boot_medium: false,
            holds_root: false,
        };
        let disks = vec![
            disk("vtbd0", vec![]),
            disk("vtbd1", vec![part("vtbd1p1"), part("vtbd1p3")]),
        ];
        let sys_bsd = target::System {
            disks,
            mounts: vec![],
            pools: vec![],
            pvs: vec![],
            devs: vec![],
            swaps: vec![],
        };
        for (dev, want) in [
            ("vtbd1p3.eli", true),
            ("vtbd1p3", true),
            ("vtbd1", true),
            ("vtbd1p2.eli", false),
            ("vtbd0p3.eli", false),
            ("vtbd0", false),
        ] {
            assert_eq!(
                target::vdev_on_targets(&sys_bsd, &["vtbd1"], dev),
                want,
                "{dev}"
            );
        }
        // Linux ZFS on LUKS/MD and sda vs sdaa1 prefix collision.
        let sda = disk(
            "sda",
            vec![BlockDev {
                name: "sda2".into(),
                kind: Kind::Partition,
                parent: Some("sda".into()),
                ..BlockDev::default()
            }],
        );
        let sdaa = disk(
            "sdaa",
            vec![BlockDev {
                name: "sdaa1".into(),
                kind: Kind::Partition,
                parent: Some("sdaa".into()),
                ..BlockDev::default()
            }],
        );
        let devs = vec![
            sda.b.clone(),
            sda.parts[0].clone(),
            sdaa.b.clone(),
            sdaa.parts[0].clone(),
            BlockDev {
                name: "dm-0".into(),
                kind: Kind::Dm,
                slaves: vec!["sda2".into()],
                ..BlockDev::default()
            },
            BlockDev {
                name: "dm-1".into(),
                kind: Kind::Dm,
                slaves: vec!["sda2".into(), "sdaa1".into()],
                ..BlockDev::default()
            },
        ];
        let sys_linux = target::System {
            disks: vec![sda, sdaa],
            mounts: vec![],
            pools: vec![],
            pvs: vec![],
            devs,
            swaps: vec![],
        };
        assert!(target::vdev_on_targets(&sys_linux, &["sda"], "dm-0"));
        assert!(!target::vdev_on_targets(&sys_linux, &["sda"], "dm-1"));
        assert!(!target::vdev_on_targets(&sys_linux, &["sda"], "sdaa1"));
    }

    /// tn6-3: the boot stage unmounts the inactive BEs it restored; after
    /// it they don't make the tree look unmounted (before, they do).
    #[test]
    fn restored_bes_unmounted_by_the_boot_stage() {
        let t = "/mnt/bko-target";
        let gone = vec![
            format!("{t}/run/bko-be/25.10.6"),
            format!("{t}/run/bko-be/25.10.6/usr"),
            format!("{t}/run/bko-bed"),
        ];
        assert_eq!(tree_unmounted(&gone, t, false), gone);
        assert_eq!(
            tree_unmounted(&gone, t, true),
            vec![format!("{t}/run/bko-bed")]
        );
        assert_eq!(
            tree_unmounted(&[format!("{t}/usr")], t, true),
            vec![format!("{t}/usr")]
        );
    }

    #[test]
    fn tallies() {
        assert_eq!(tally(0, 0), State::Nothing);
        assert_eq!(tally(2, 2), State::Done);
        assert_eq!(tally(0, 2), State::NotDone);
        assert_eq!(tally(1, 2), State::Partly);
    }

    // ---- the status document and the copy marks (RT10e) -----------------

    use crate::json::Value;

    const TOOLS: [&str; 20] = [
        "wipefs",
        "dd",
        "sfdisk",
        "udevadm",
        "mke2fs",
        "tune2fs",
        "mkswap",
        "mkfs.btrfs",
        "btrfs",
        "mkdir",
        "mount",
        "umount",
        "chroot",
        "cp",
        "sh",
        "bash",
        "cryptsetup",
        "grub-install",
        "update-grub",
        "rustic-bko",
    ];

    /// A rescue system: vdb 16 GiB (the sample's size) holding TABLE, every
    /// tool, BackOtter's rustic-bko, and a private password file /k/pw.
    fn rescue_j(name: &str, table: &[u8]) -> Tree {
        let t = Tree::new(name);
        t.disk("vdb", "253:16", table, 512)
            .file("/sys/block/vdb/size", "33554432\n");
        t.file("/proc/self/mountinfo", "")
            .file("/proc/swaps", "Filename\tType\tSize\tUsed\tPriority\n");
        for p in TOOLS {
            t.file(&format!("/usr/sbin/{p}"), "");
        }
        use std::os::unix::fs::PermissionsExt;
        t.file("/k/pw", "pw");
        std::fs::set_permissions(t.p("/k/pw"), std::fs::Permissions::from_mode(0o600)).unwrap();
        std::fs::set_permissions(t.p("/k"), std::fs::Permissions::from_mode(0o700)).unwrap();
        t
    }

    fn sys_j(t: &Tree, runner: FakeRunner) -> Sys {
        Sys::new(
            t.root(),
            Box::new(runner.with(
                "/usr/sbin/rustic-bko --version",
                "rustic 0.12.0+backotter.4\n",
            )),
            true,
        )
    }

    /// The sample layout's own MBR (its signature, its three partitions):
    /// what the source's disk as found, or one load partitioned, holds.
    fn source_mbr() -> Vec<u8> {
        let mut table = vec![0u8; 512];
        table[440..444].copy_from_slice(&0x1f2e_3d4cu32.to_le_bytes());
        for (slot, start, n) in [
            (0, 2048, 1953792),
            (1, 1955840, 3905536),
            (2, 5861376, 27693056),
        ] {
            crate::parttable::build::mbr_entry(&mut table, slot, 0, 0x83, start, n);
        }
        table
    }

    fn opts_j(a: &[&str]) -> Options {
        let mut v = vec!["l.json".to_string()];
        v.extend(a.iter().map(|s| s.to_string()));
        match parse_args(&v).unwrap() {
            Parsed::Run(o) => o,
            Parsed::Help => unreachable!(),
        }
    }

    /// load::run with A: its exit, its standard output as one document,
    /// its standard error.
    fn doc_of(layout: &str, a: &[&str], s: &Sys) -> (u8, Value, String) {
        let (code, out, err) = crate::load::run(&opts_j(a), layout, s);
        assert!(
            out.ends_with('\n') && out.matches('\n').count() == 1,
            "not one line: {out}"
        );
        (
            code,
            crate::json::parse(&out).unwrap_or_else(|e| panic!("{}: {out}", e.message)),
            err,
        )
    }

    fn strs(v: &Value, path: &[&str]) -> Vec<String> {
        v.arr_at(path)
            .iter()
            .filter_map(|x| x.as_str().map(str::to_string))
            .collect()
    }

    /// This session's partition record of vdb, as the partition stage
    /// writes it.
    fn record(t: &Tree, l: &str, s: &Sys) {
        let lay = Layout::from_json(crate::json::parse(l).unwrap()).unwrap();
        let m = target::prepare(&lay, &opts_j(&["--map", "vda=vdb"]), s).mapping;
        assert_eq!(m.records.len(), 1);
        let (path, text) = &m.records[0];
        t.file(path, text.as_str());
    }

    /// The copy marks' text for the sample layout onto vdb.
    fn mark(l: &str, s: &Sys) -> String {
        let lay = Layout::from_json(crate::json::parse(l).unwrap()).unwrap();
        restore_mark_text(
            &lay,
            &target::prepare(&lay, &opts_j(&["--map", "vda=vdb"]), s).mapping,
        )
    }

    /// The restored tree mounted: / and /boot under /mnt/bko-target, with
    /// the files status takes for a restored Debian.
    fn mounted(t: &Tree) {
        t.file("/proc/self/mountinfo", "36 35 253:19 / /mnt/bko-target rw,relatime - btrfs /dev/vdb3 rw\n37 36 253:17 / /mnt/bko-target/boot rw,relatime - ext4 /dev/vdb1 rw\n");
        t.file("/mnt/bko-target/etc/fstab", "")
            .file("/mnt/bko-target/etc/os-release", "ID=debian\n");
    }

    /// The document as tests/fixtures/status-json/NAME.json keeps it: pretty,
    /// its `generated` and `version` replaced.  BKO_HOLT_JSON_GOLDEN=bless
    /// writes it again.
    fn golden(name: &str, v: &Value) {
        let mut v = v.clone();
        if let Value::Obj(kv) = &mut v {
            for (k, x) in kv.iter_mut() {
                match k.as_str() {
                    "generated" => *x = Value::from("GENERATED"),
                    "version" => *x = Value::from("VERSION"),
                    _ => {}
                }
            }
        }
        let text = v.pretty() + "\n";
        let path = std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
            .join("tests/fixtures/status-json")
            .join(format!("{name}.json"));
        if std::env::var("BKO_HOLT_JSON_GOLDEN").as_deref() == Ok("bless") {
            assert_eq!(RUN_DIR, "/run/bko-holt", "the goldens are Linux's");
            std::fs::create_dir_all(path.parent().unwrap()).unwrap();
            std::fs::write(&path, &text).unwrap();
            return;
        }
        let want = std::fs::read_to_string(&path).unwrap_or_else(|e| {
            panic!(
                "{}: {e} (BKO_HOLT_JSON_GOLDEN=bless writes it)",
                path.display()
            )
        });
        assert_eq!(
            text,
            want,
            "{}: the status document changed (BKO_HOLT_JSON_GOLDEN=bless writes it again)",
            path.display()
        );
    }

    /// What each field of the status document is: the fields in the spec's
    /// order, each of its type, and each stage's.
    fn schema(v: &Value) {
        let keys: Vec<&str> = v
            .as_object()
            .unwrap()
            .iter()
            .map(|(k, _)| k.as_str())
            .collect();
        assert_eq!(
            keys,
            [
                "format",
                "schema",
                "version",
                "generated",
                "dry_run",
                "exit",
                "target",
                "stages",
                "next",
                "all_done",
                "marks",
                "targets",
                "temporary_keys",
                "resume",
                "open",
                "problems"
            ]
        );
        assert_eq!(
            (v.get_str("format"), v.get_u64("schema")),
            (Some("bko-holt-status"), Some(1))
        );
        assert!(
            v.get_str("generated").is_some_and(|g| g.ends_with('Z'))
                && v.get_bool("dry_run").is_some()
                && v.get_u64("exit").is_some()
        );
        assert!(
            v.get_str("target").is_some()
                && v.get("next")
                    .is_some_and(|n| n.is_null() || n.as_str().is_some())
                && v.get_bool("all_done").is_some()
        );
        for s in v.arr_at(&["stages"]) {
            assert!(
                s.get_str("name").is_some() && s.get_str("why").is_some(),
                "{s:?}"
            );
            assert!(
                ["done", "partly", "not_done", "nothing", "unknown"]
                    .contains(&s.get_str("state").unwrap()),
                "{s:?}"
            );
        }
        let m = v.get("marks").unwrap();
        assert!(
            m.get_arr("partitioned").is_some()
                && m.get_bool("restore_started").is_some()
                && m.get_bool("restore_complete").is_some()
                && m.get_bool("boot").is_some(),
            "{m:?}"
        );
        for t in v.arr_at(&["targets"]) {
            assert!(
                t.get_str("source").is_some()
                    && t.get_str("node").is_some()
                    && t.get_bool("ours").is_some()
                    && t.get_bool("same_identity").is_some()
                    && t.get_bool("recorded").is_some(),
                "{t:?}"
            );
        }
        for k in v.arr_at(&["temporary_keys"]) {
            assert!(
                k.get_str("name").is_some() && k.get_bool("kept").is_some(),
                "{k:?}"
            );
        }
        assert!(
            v.bool_at(&["resume", "go_on"]).is_some()
                && v.at(&["resume", "why"]).and_then(Value::as_array).is_some()
        );
        assert_eq!(
            v.bool_at(&["resume", "go_on"]),
            Some(v.arr_at(&["resume", "why"]).is_empty())
        );
        for k in ["containers", "pools", "mounted"] {
            assert!(
                v.at(&["open", k]).and_then(Value::as_array).is_some(),
                "{k}"
            );
        }
        assert!(
            v.arr_at(&["problems"])
                .iter()
                .all(|p| p.get_str("code").is_some() && p.get_str("text").is_some())
        );
    }

    /// The rows of the document are text mode's: each stage's name, its
    /// state's word in snake case, its why; next as text mode's.
    fn as_text(v: &Value, text: &str) {
        let rows: Vec<String> = v
            .arr_at(&["stages"])
            .iter()
            .map(|s| {
                let word = match s.get_str("state").unwrap() {
                    "not_done" => "not done",
                    "nothing" => "nothing to do",
                    w => w,
                };
                let why = s.get_str("why").unwrap();
                format!(
                    "{:<10} {:<14}{}\n",
                    s.get_str("name").unwrap(),
                    word,
                    if why.is_empty() {
                        String::new()
                    } else {
                        format!(" ({why})")
                    }
                )
            })
            .collect();
        let want: Vec<String> = text
            .split_inclusive('\n')
            .filter(|l| !l.starts_with('#') && !l.starts_with("next: "))
            .map(str::to_string)
            .collect();
        assert_eq!(rows, want, "{text}");
        let next = text.lines().find_map(|l| l.strip_prefix("next: ")).unwrap();
        match v.get_str("next") {
            Some(n) => assert_eq!(n, next),
            None => assert!(
                next.starts_with("nothing;") && v.get_bool("all_done") == Some(true),
                "{next}"
            ),
        }
    }

    /// The status document of a blank target: text mode's rows, nothing
    /// recorded, nothing open, and no "Go on" (nothing load did is there).
    #[test]
    fn status_document() {
        let t = rescue_j("status-json-blank", &[]);
        let s = sys_j(&t, FakeRunner::default());
        let l = sample::bios_mbr();
        let a = ["--map", "vda=vdb", "--only", "status"];
        let (code, v, err) = doc_of(&l, &[&a[..], &["--json"]].concat(), &s);
        let (tcode, text, terr) = crate::load::run(&opts_j(&a), &l, &s);
        assert_eq!((code, err.as_str()), (tcode, terr.as_str()));
        assert_eq!(code, EXIT_OK);
        schema(&v);
        as_text(&v, &text);
        assert_eq!(v.get_str("target"), Some("/mnt/bko-target"));
        assert_eq!(v.get_str("next"), Some("partition"));
        assert_eq!(
            v.at(&["marks"]).unwrap().compact(),
            r#"{"partitioned":[],"restore_started":false,"restore_complete":false,"boot":false}"#
        );
        assert_eq!(
            v.at(&["targets"]).unwrap().compact(),
            r#"[{"source":"vda","node":"vdb","ours":false,"same_identity":false,"recorded":false}]"#
        );
        assert_eq!(v.bool_at(&["resume", "go_on"]), Some(false));
        assert_eq!(
            strs(&v, &["resume", "why"]),
            [
                "/dev/vdb: this session has no record that load's partition stage wrote it (a reboot forgets them), so what's there can't be told from the old system's"
            ]
        );
        assert_eq!(
            v.at(&["open"]).unwrap().compact(),
            r#"{"containers":[],"pools":[],"mounted":[]}"#
        );
        golden("blank", &v);
        // A disk that went away: the mapping's problem, exit 4, no rows,
        // as text mode prints none.
        let (code, v, err) = doc_of(&l, &["--map", "vda=vdz", "--only", "status", "--json"], &s);
        assert_eq!((code, err.as_str()), (EXIT_PLAN_PROBLEMS, STATUS_PROBLEMS));
        schema(&v);
        assert!(
            v.arr_at(&["stages"]).is_empty()
                && v.get("next").is_some_and(Value::is_null)
                && v.get_bool("all_done") == Some(false)
        );
        assert_eq!(
            v.str_at(&["problems", "0", "code"]),
            Some("no_such_disk"),
            "{:?}",
            v.get("problems")
        );
        assert_eq!(
            strs(&v, &["resume", "why"])[0],
            "the disks can't all be matched to the layout"
        );
    }

    /// Under --json the restore row needs this session's complete mark
    /// (J10): the tree's files alone are "partly" (a stopped copy has them,
    /// and so has the old system), a mark of another mapping too; text
    /// mode's row is as it was.  "Go on" only on recorded disks, and not
    /// when it would make something anew over the copy (J14, J19).
    #[test]
    fn the_copy_marks() {
        let t = rescue_j("status-json-marks", &source_mbr());
        let s = sys_j(&t, FakeRunner::default());
        let l = sample::bios_mbr();
        let a = ["--map", "vda=vdb", "--only", "status"];
        let restore = |v: &Value| {
            v.arr_at(&["stages"])
                .iter()
                .find(|r| r.get_str("name") == Some("restore"))
                .map(|r| {
                    (
                        r.get_str("state").unwrap().to_string(),
                        r.get_str("why").unwrap().to_string(),
                    )
                })
                .unwrap()
        };
        let json = || doc_of(&l, &[&a[..], &["--json"]].concat(), &s).1;
        // Recorded, nothing begun: Go on (partition is done, format next).
        record(&t, &l, &s);
        let v = json();
        assert_eq!(strs(&v, &["marks", "partitioned"]), ["vdb"]);
        assert_eq!(
            v.at(&["targets", "0"]).unwrap().compact(),
            r#"{"source":"vda","node":"vdb","ours":true,"same_identity":false,"recorded":true}"#
        );
        assert_eq!(
            (v.bool_at(&["resume", "go_on"]), v.get_str("next")),
            (Some(true), Some("format")),
            "{:?}",
            v.get("resume")
        );
        golden("recorded", &v);
        // The tree mounted with a system in it, no mark: partly under
        // --json, done in text mode.
        mounted(&t);
        let (_, text, _) = crate::load::run(&opts_j(&a), &l, &s);
        assert!(text.contains("restore    done"), "{text}");
        let v = json();
        assert_eq!(
            restore(&v),
            (
                "partly".to_string(),
                "the copy didn't finish in this session".to_string()
            )
        );
        assert_eq!(
            strs(&v, &["open", "mounted"]),
            ["/mnt/bko-target", "/mnt/bko-target/boot"]
        );
        // The copy began here: its start mark.  --through close would make
        // format's filesystems anew over it: no Go on.
        t.file(&restore_started_marker(), mark(&l, &s));
        let v = json();
        assert_eq!(
            (
                v.bool_at(&["marks", "restore_started"]),
                v.bool_at(&["marks", "restore_complete"])
            ),
            (Some(true), Some(false))
        );
        assert_eq!(restore(&v).0, "partly");
        assert_eq!(v.bool_at(&["resume", "go_on"]), Some(false));
        assert_eq!(
            strs(&v, &["resume", "why"]),
            [
                "vda -> /dev/vdb: format would make what it makes anew there, over the copy this session's restore began on it; to start again, run --only partition with --erase /dev/vdb, which wipes it first: the open stage isn't in this bko-holt"
            ]
        );
        golden("copy-begun", &v);
        // A complete mark of another copy (another target) isn't this one's.
        t.file(
            &restore_done_marker(),
            mark(&l, &s).replace("target vda vdb", "target vda vdc"),
        );
        assert_eq!(restore(&json()).0, "partly");
        // This one's: done.
        t.file(&restore_done_marker(), mark(&l, &s));
        let v = json();
        assert_eq!(restore(&v), ("done".to_string(), String::new()));
        assert_eq!(v.bool_at(&["marks", "restore_complete"]), Some(true));
        golden("copy-done", &v);
        // Text mode's rows never read the marks.
        let (_, text2, _) = crate::load::run(&opts_j(&a), &l, &s);
        assert_eq!(text, text2);
        // Another layout's start mark: not this layout's copy, though it
        // names vdb, so nothing is made anew over it either.
        t.file(
            &restore_started_marker(),
            mark(&l, &s).replace("layout deb13", "layout other"),
        );
        let v = json();
        assert_eq!(v.bool_at(&["marks", "restore_started"]), Some(false));
        assert_eq!(v.bool_at(&["resume", "go_on"]), Some(false));
    }

    /// One LUKS container, cryptroot (vda3, holding /), that the source
    /// unlocked with a key file the restore brings back: load makes it a
    /// temporary key.
    fn luks_keyfile() -> String {
        sample::bios_mbr()
            .replacen(
                r#""content": {"type": "btrfs", "usage": "filesystem", "uuid": "b7b7b7b7-0000-4000-8000-000000000003""#,
                r#""content": {"type": "crypto_LUKS", "usage": "crypto", "uuid": "a3a3a3a3-0000-4000-8000-000000000003", "label": null, "version": "2"}, "x": {"type": "btrfs", "usage": "filesystem", "uuid": "b7b7b7b7-0000-4000-8000-000000000003""#,
                1,
            )
            .replace(
                r#""dm_devices": []"#,
                r#""dm_devices": [
                  {"name": "dm-1", "kind": "dm", "size_bytes": 14178844672, "slaves": ["vda3"], "holders": [],
                   "content": {"type": "btrfs", "usage": "filesystem", "uuid": "b7b7b7b7-0000-4000-8000-000000000003", "label": null, "node_size": 16384, "sector_size": 4096, "checksum": "crc32c", "devid": 1, "features": [], "compat_ro_features": []},
                   "dm": {"dm_name": "cryptroot", "mapper_path": "/dev/mapper/cryptroot", "target": "crypt", "crypt_type": "LUKS2"}}]"#,
            )
            .replace(r#""devices": ["vda3"]"#, r#""devices": ["dm-1"]"#)
            .replace(r#""resolves_to": "vda3""#, r#""resolves_to": "dm-1""#)
            .replace(
                r#""crypttab": null"#,
                r#""crypttab": [{"name": "cryptroot", "device": "UUID=a3a3a3a3-0000-4000-8000-000000000003", "key_file": "/etc/keys/root.key", "options": ["luks"], "resolves_to": "vda3"}]"#,
            )
    }

    /// The containers whose only key is load's temporary one, and whether
    /// it's still there; once the copy began, a lost one is no Go on.
    #[test]
    fn temporary_keys() {
        let t = rescue_j("status-json-keys", &source_mbr());
        let s = sys_j(&t, FakeRunner::default());
        let l = luks_keyfile();
        let a = ["--map", "vda=vdb", "--only", "status", "--json"];
        record(&t, &l, &s);
        let v = doc_of(&l, &a, &s).1;
        assert_eq!(
            v.at(&["temporary_keys"]).unwrap().compact(),
            r#"[{"name":"cryptroot","kept":false}]"#
        );
        // Not begun, not open: a rerun makes it anew with a new key.
        assert!(
            !strs(&v, &["resume", "why"])
                .iter()
                .any(|w| w.contains("temporary key")),
            "{:?}",
            v.get("resume")
        );
        // Begun: it can't be opened again.
        t.file(&restore_started_marker(), mark(&l, &s));
        let v = doc_of(&l, &a, &s).1;
        let key = format!("{RUN_DIR}/keys/cryptroot");
        assert!(strs(&v, &["resume", "why"]).contains(&format!("cryptroot: its temporary key {key} is gone, and boot hasn't added the restored key file, so it can't be opened again")), "{:?}", v.get("resume"));
        // Kept, or boot ran: no matter.
        t.file(&key, "k");
        let v = doc_of(&l, &a, &s).1;
        assert_eq!(
            v.at(&["temporary_keys"]).unwrap().compact(),
            r#"[{"name":"cryptroot","kept":true}]"#
        );
        assert!(
            !strs(&v, &["resume", "why"])
                .iter()
                .any(|w| w.contains("temporary key"))
        );
        std::fs::remove_file(t.p(&key)).unwrap();
        t.file(&boot_marker(), "");
        assert!(
            !strs(&doc_of(&l, &a, &s).1, &["resume", "why"])
                .iter()
                .any(|w| w.contains("temporary key"))
        );
        // A --key-file given for it: no temporary key at all.
        let mut k = a.to_vec();
        k.extend(["--key-file", "cryptroot=/k/pw"]);
        assert!(doc_of(&l, &k, &s).1.arr_at(&["temporary_keys"]).is_empty());
    }

    /// A ZFS encryption root keyed by a raw file (Ubuntu's rpool, its key
    /// in the keystore) and no --key-file: load's random key at
    /// RUN_DIR/keys/rpool is its only one, so it's listed, and a lost one
    /// is no Go on; with --key-file rpool=FILE there's none.
    #[test]
    fn a_zfs_encryption_roots_temporary_key() {
        let t = Tree::new("status-json-zfskey");
        for (d, n) in [("sdq", "65:0"), ("sdr", "65:16")] {
            t.disk(d, n, &[], 512)
                .file(&format!("/sys/block/{d}/size"), "33554432\n");
        }
        t.file("/proc/self/mountinfo", "")
            .file("/proc/swaps", "Filename\tType\tSize\tUsed\tPriority\n");
        let s = Sys::new(t.root(), Box::new(FakeRunner::default()), true);
        let text = std::fs::read_to_string(format!(
            "{}/tests/corpus/ubuntu26-zfsmirror/src.json",
            env!("CARGO_MANIFEST_DIR")
        ))
        .unwrap();
        let l = Layout::from_json(crate::json::parse(&text).unwrap()).unwrap();
        let a = [
            "--map", "vda=sdq", "--map", "vdb=sdr", "--only", "status", "--json",
        ];
        let keys = |extra: &[&str], lost_matters: bool| {
            let o = opts_j(&[&a[..], extra].concat());
            let (k, why) = super::temporary_keys(&o, &read(&l, &o, &s), &s, lost_matters);
            (Value::Arr(k).compact(), why)
        };
        let key = format!("{RUN_DIR}/keys/rpool");
        assert_eq!(
            keys(&[], false),
            (r#"[{"name":"rpool","kept":false}]"#.to_string(), vec![])
        );
        assert_eq!(
            keys(&[], true).1,
            [format!(
                "rpool: its temporary key {key} is gone, and boot hasn't added the restored key file, so it can't be opened again"
            )]
        );
        t.file(&key, "k");
        assert_eq!(
            keys(&[], true),
            (r#"[{"name":"rpool","kept":true}]"#.to_string(), vec![])
        );
        assert_eq!(
            keys(&["--key-file", "rpool=/k/rpool.key"], true),
            ("[]".to_string(), vec![])
        );
    }

    /// A copy that stopped (the archiver killed, the rescue system still
    /// up): every stage before restore done, the start mark and no
    /// complete one.  Restore is next and partly, and Go on is offered:
    /// --through close skips what's done, so nothing is made anew over it.
    #[test]
    fn a_stopped_copy_goes_on() {
        let t = rescue_j("status-json-stopped", &source_mbr());
        let s = sys_j(&t, FakeRunner::default());
        let l = sample::bios_mbr();
        record(&t, &l, &s);
        mounted(&t);
        // format's filesystems, each with the source's UUID.
        let with_uuid = |mut img: Vec<u8>, at: usize, uuid: &str| {
            let hex: String = uuid.chars().filter(|c| *c != '-').collect();
            for i in 0..16 {
                img[at + i] = u8::from_str_radix(&hex[2 * i..2 * i + 2], 16).unwrap();
            }
            img
        };
        t.file(
            "/dev/vdb1",
            with_uuid(
                crate::probe::ext::sample::image(0, 0, 0, ""),
                1024 + 104,
                "0b0e2c6e-1111-4222-8333-444455556666",
            ),
        );
        t.file(
            "/dev/vdb2",
            with_uuid(
                crate::probe::misc::sample::swap(16, ""),
                1036,
                "5e5e5e5e-0000-4000-8000-000000000002",
            ),
        );
        t.file(
            "/dev/vdb3",
            with_uuid(
                crate::probe::btrfs::sample::image("", 0),
                0x10020,
                "b7b7b7b7-0000-4000-8000-000000000003",
            ),
        );
        t.file(&restore_started_marker(), mark(&l, &s));
        let (code, v, _) = doc_of(
            &l,
            &[
                "--map",
                "vda=vdb",
                "--repo",
                "/srv/r",
                "--password-file",
                "/k/pw",
                "--only",
                "status",
                "--json",
            ],
            &s,
        );
        assert_eq!(code, EXIT_OK);
        let states: Vec<String> = v
            .arr_at(&["stages"])
            .iter()
            .map(|r| {
                format!(
                    "{} {}",
                    r.get_str("name").unwrap(),
                    r.get_str("state").unwrap()
                )
            })
            .collect();
        assert_eq!(
            states,
            [
                "partition done",
                "assemble nothing",
                "format done",
                "mount done",
                "restore partly",
                "boot not_done",
                "close not_done"
            ],
            "{:?}",
            v.get("stages")
        );
        assert_eq!(v.get_str("next"), Some("restore"));
        assert_eq!(
            v.at(&["marks"]).unwrap().compact(),
            r#"{"partitioned":["vdb"],"restore_started":true,"restore_complete":false,"boot":false}"#
        );
        assert_eq!(
            v.at(&["resume"]).unwrap().compact(),
            r#"{"go_on":true,"why":[]}"#
        );
    }

    /// remake_after_restore (J19): once a restore began on a target, a run
    /// that would make assemble's or format's anew there without its own
    /// partition stage wiping it first is refused, in the plan and the
    /// writing runs alike (a dry run's too); text mode doesn't refuse it.
    #[test]
    fn remake_after_restore() {
        let t = rescue_j("status-json-remake", &source_mbr());
        let s = sys_j(&t, FakeRunner::default());
        let l = sample::bios_mbr();
        let lay = Layout::from_json(crate::json::parse(&l).unwrap()).unwrap();
        record(&t, &l, &s);
        let base = [
            "--map",
            "vda=vdb",
            "--repo",
            "/srv/r",
            "--password-file",
            "/k/pw",
            "--json",
        ];
        let remakes = |v: &Value| {
            v.arr_at(&["problems"])
                .iter()
                .filter(|p| p.get_str("code") == Some("remake_after_restore"))
                .cloned()
                .collect::<Vec<_>>()
        };
        // A writing run without --commit, as exec runs it.
        let writing = |extra: &[&str]| {
            let o = opts_j(&[&base[..], extra].concat());
            crate::load::exec::run(&lay, &o, &s, &o.stages())
        };
        let text = "vda -> /dev/vdb: format would make what it makes anew there, over the copy this session's restore began on it; to start again, run --only partition with --erase /dev/vdb, which wipes it first";
        // Nothing begun: none.
        let (code, v, _) = doc_of(&l, &base, &s);
        assert!(
            remakes(&v).is_empty() && code == EXIT_OK,
            "{code} {:?}",
            v.get("problems")
        );
        let (code, out, err) = writing(&["--only", "format"]);
        assert_eq!(code, EXIT_OK, "{out}{err}");
        t.file(&restore_started_marker(), mark(&l, &s));
        // The plan (its run is --through close).
        let (code, v, _) = doc_of(&l, &base, &s);
        let r = remakes(&v);
        assert_eq!(
            (code, r.len()),
            (EXIT_PLAN_PROBLEMS, 1),
            "{:?}",
            v.get("problems")
        );
        assert_eq!(
            (
                r[0].get_str("disk"),
                r[0].get_str("target"),
                r[0].get_str("stage"),
                r[0].get_str("text")
            ),
            (Some("vda"), Some("vdb"), Some("format"), Some(text))
        );
        // The writing runs: nothing run.
        for extra in [&["--only", "format"][..], &["--through", "close"]] {
            let (code, out, err) = writing(extra);
            assert_eq!(code, EXIT_PLAN_PROBLEMS, "{extra:?}: {out}{err}");
            assert!(out.is_empty() && err.contains(text), "{extra:?}: {err}");
        }
        // The ways on: a run that wipes first, or one that makes nothing.
        for extra in [
            &["--only", "partition", "--erase", "/dev/vdb"][..],
            &["--only", "mount"],
            &["--only", "restore"],
            &["--through", "partition"],
        ] {
            let (code, out, err) = writing(extra);
            assert!(
                code == EXIT_OK && !err.contains("anew there"),
                "{extra:?}: {code} {out}{err}"
            );
        }
        // Text mode: no such refusal.
        let o = opts_j(&[&base[..6], &["--only", "format"]].concat());
        let (code, out, err) = crate::load::exec::run(&lay, &o, &s, &o.stages());
        assert!(
            code == EXIT_OK && !err.contains("anew there"),
            "{code}: {out}{err}"
        );
    }
}
