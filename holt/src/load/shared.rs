//! btrfs snapshots restored so that they share their extents
//! (docs/design/btrfs-snapshots.md): which subvolumes are shared snapshots,
//! what each is made from, where the backup holds them, which paths the
//! main restore leaves out, and the IDs they get.  Pure: the commands are
//! commands.rs's.

use std::collections::{BTreeMap, BTreeSet};

/// A subvolume, as the layout lists it.
#[derive(Debug, Clone, PartialEq)]
pub struct Sub {
    /// Its path from the top level, without `<FS_TREE>/`.
    pub path: String,
    pub id: Option<u64>,
    /// The generation it was last written in: a snapshot's creation, for
    /// one never written after (snapper's read-only ones).
    pub generation: Option<u64>,
    pub uuid: Option<String>,
    pub parent_uuid: Option<String>,
    pub readonly: bool,
}

/// A mount load makes of the filesystem: where, and which subvolume
/// (`""` for the top level).
#[derive(Debug, Clone, PartialEq)]
pub struct Mount {
    pub target: String,
    pub subvol: String,
}

/// A shared snapshot: made after the main restore as a snapshot of `base`,
/// then made exact from the backup's `backup`.
#[derive(Debug, Clone, PartialEq)]
pub struct Shared {
    pub path: String,
    /// The source's ID.
    pub id: Option<u64>,
    /// The ID it gets here.
    pub new_id: u64,
    /// Placeholders made just before it, so that it gets the source's ID.
    pub placeholders: Vec<u64>,
    /// Where the backup holds its contents.
    pub backup: String,
    /// The subvolume it is snapshotted from, restored before it.
    pub base: String,
    pub readonly: bool,
    /// What its restore may clone from: the backup path of a mounted
    /// subvolume of its family and that subvolume's path.
    pub clone_from: Option<(String, String)>,
    /// The subvolumes nested in it, a snapper slot's (transactional-update's
    /// `/etc`, one per slot: docs/design/aeon.md): made right after it,
    /// parents first, and filled by its restore.
    pub children: Vec<Child>,
}

/// A subvolume nested in a shared snapshot that is a snapper slot.  Its
/// slot's snapshot of the base has an empty directory where it is (btrfs
/// snapshots don't take nested subvolumes): that goes, and it is made in
/// its place, before the slot's restore fills both.
#[derive(Debug, Clone, PartialEq)]
pub struct Child {
    pub path: String,
    /// Its path below the slot (`etc`).
    pub rest: String,
    /// The source's ID.
    pub id: Option<u64>,
    /// The ID it gets here.
    pub new_id: u64,
    /// Placeholders made just before it, so that it gets the source's ID.
    pub placeholders: Vec<u64>,
    /// The base's own subvolume at `rest`, restored before it: it is made
    /// as a snapshot of that, sharing its extents, or else made empty.
    pub base: Option<String>,
    pub readonly: bool,
}

#[derive(Debug, Default, Clone, PartialEq)]
pub struct Plan {
    /// Backup paths where a mounted subvolume shows up a second time.
    pub aliases: Vec<String>,
    /// In the order they are made: each family's in its contents' (`gen`),
    /// which is the source's ID order but after a rollback, and the
    /// families interleaved by source ID, each after its base.
    pub shared: Vec<Shared>,
    pub notes: Vec<String>,
}

impl Plan {
    /// PATH is made after the main restore: a shared snapshot, or a
    /// subvolume nested in one (`Shared::children`).
    pub fn is_shared(&self, path: &str) -> bool {
        self.shared
            .iter()
            .any(|s| s.path == path || s.children.iter().any(|c| c.path == path))
    }

    /// The ID a subvolume made after the main restore gets here, by the
    /// source's ID.
    pub fn new_id_of(&self, src: u64) -> Option<u64> {
        self.shared.iter().find_map(|s| {
            if s.id == Some(src) {
                Some(s.new_id)
            } else {
                s.children
                    .iter()
                    .find(|c| c.id == Some(src))
                    .map(|c| c.new_id)
            }
        })
    }

    /// The ID PATH gets here, when it is made after the main restore.
    pub fn new_id_of_path(&self, path: &str) -> Option<u64> {
        self.shared.iter().find_map(|s| {
            if s.path == path {
                Some(s.new_id)
            } else {
                s.children.iter().find(|c| c.path == path).map(|c| c.new_id)
            }
        })
    }

    pub fn get(&self, path: &str) -> Option<&Shared> {
        self.shared.iter().find(|s| s.path == path)
    }

    /// The main restore's exclusions, as `--glob-file` lines: everything
    /// below an alias (its directory stays: the subvolume's root, whose
    /// metadata a backup of `/` holds only there) and below a shared
    /// snapshot's directory (which stays, empty: the backup has it).  A
    /// glob excludes only what it matches, not what is below it: hence
    /// `/**`.  Each path is escaped (`glob_literal`), so that one holding a
    /// glob metacharacter is matched as itself.
    pub fn globs(&self) -> Vec<String> {
        let mut g: Vec<String> = self
            .aliases
            .iter()
            .map(|a| format!("!{}/**", glob_literal(a)))
            .collect();
        g.extend(
            self.shared
                .iter()
                .map(|s| format!("!{}/**", glob_literal(&s.backup))),
        );
        g
    }
}

/// PATH as a `--glob-file` pattern that matches only itself.  rustic-bko
/// reads each line of the file with the ignore crate's `OverrideBuilder`
/// (`rustic_core`'s blob/tree/excludes.rs): gitignore syntax, compiled by
/// globset with backslash escapes on, where `\` before any character makes
/// it literal.  `*`, `?`, `[`, `]`, `{`, `}` (and `\` itself) are special
/// there; `,` is only within `{}`, `!` and `^` only within `[]`, and a
/// line's leading `!`/`#` and trailing spaces don't arise in the middle of
/// a `!PATH/**` line.  A newline can't be written: the file is read by
/// lines.
pub(crate) fn glob_literal(path: &str) -> String {
    let mut out = String::with_capacity(path.len());
    for c in path.chars() {
        if matches!(c, '\\' | '*' | '?' | '[' | ']' | '{' | '}') {
            out.push('\\');
        }
        out.push(c);
    }
    out
}

/// `inner` is `outer` or below it (`""` is the top level, above all).
fn under(inner: &str, outer: &str) -> bool {
    outer.is_empty() || inner == outer || inner.starts_with(&format!("{outer}/"))
}

fn join(target: &str, rest: &str) -> String {
    if rest.is_empty() {
        target.to_string()
    } else if target == "/" {
        format!("/{rest}")
    } else {
        format!("{target}/{rest}")
    }
}

/// `p` below the subvolume `outer`, as a path from it.
fn rest_of<'a>(p: &'a str, outer: &str) -> &'a str {
    if outer.is_empty() {
        p
    } else {
        p[outer.len()..].trim_start_matches('/')
    }
}

/// Whether a mount other than `at` covers `path`, a path below `at`.
fn covered(mounts: &[Mount], at: &str, path: &str) -> bool {
    mounts.iter().any(|m| {
        let t = m.target.as_str();
        t != at && under_dir(t, at) && (path == t || under_dir(path, t))
    })
}

/// The directory `p` is `dir` or below it.
fn under_dir(p: &str, dir: &str) -> bool {
    dir == "/" || p == dir || p.starts_with(&format!("{}/", dir.trim_end_matches('/')))
}

/// Where the backup holds the unmounted subvolume `path`: below the mount
/// of its deepest mounted ancestor, unless a mount covers it.
pub fn backup_path(mounts: &[Mount], path: &str) -> Option<String> {
    let m = mounts
        .iter()
        .filter(|m| m.subvol != path && under(path, &m.subvol))
        .max_by_key(|m| (m.subvol.len(), std::cmp::Reverse(m.target.clone())))?;
    let b = join(&m.target, rest_of(path, &m.subvol));
    (!covered(mounts, &m.target, &b)).then_some(b)
}

/// Where a mounted subvolume shows up again, nested in another one mounted.
pub fn aliases(mounts: &[Mount]) -> Vec<String> {
    let mut out = BTreeSet::new();
    for inner in mounts {
        for outer in mounts {
            if inner.subvol == outer.subvol || !under(&inner.subvol, &outer.subvol) {
                continue;
            }
            let a = join(&outer.target, rest_of(&inner.subvol, &outer.subvol));
            if !covered(mounts, &outer.target, &a) {
                out.insert(a);
            }
        }
    }
    out.into_iter().collect()
}

/// snapper's slot: `(X/.snapshots, N)` for `X/.snapshots/N/snapshot`.
pub(crate) fn snapper_slot(path: &str) -> Option<(&str, u64)> {
    let rest = path.strip_suffix("/snapshot")?;
    let (dir, n) = rest.rsplit_once('/')?;
    let n: u64 = n.parse().ok()?;
    (dir == ".snapshots" || dir.ends_with("/.snapshots")).then_some((dir, n))
}

/// A subvolume nested in a snapper slot: `(X/.snapshots/N/snapshot, REST)`
/// for `X/.snapshots/N/snapshot/REST` (transactional-update's `/etc`).
pub(crate) fn slot_child(path: &str) -> Option<(&str, &str)> {
    let mut at = 0;
    while let Some(i) = path[at..].find("/snapshot/") {
        let end = at + i + "/snapshot".len();
        if snapper_slot(&path[..end]).is_some() {
            return Some((&path[..end], &path[end + 1..])).filter(|(_, r)| !r.is_empty());
        }
        at = end;
    }
    None
}

/// The shared snapshots of a filesystem with these subvolumes and mounts,
/// and the IDs everything gets.
pub fn plan(subs: &[Sub], mounts: &[Mount]) -> Plan {
    let mut plan = Plan {
        aliases: aliases(mounts),
        ..Plan::default()
    };
    let mounted: BTreeSet<&str> = mounts.iter().map(|m| m.subvol.as_str()).collect();
    let by_uuid: BTreeMap<&str, &Sub> = subs
        .iter()
        .filter_map(|s| Some((s.uuid.as_deref()?, s)))
        .collect();
    let mut cands: Vec<&Sub> = subs
        .iter()
        .filter(|s| !mounted.contains(s.path.as_str()))
        .filter(|s| {
            s.parent_uuid
                .as_deref()
                .is_some_and(|p| by_uuid.contains_key(p))
                || snapper_slot(&s.path).is_some()
        })
        // A slot's own nested subvolume goes with its slot: made after it,
        // as one of its children if the slot is shared, else by format.
        .filter(|s| slot_child(&s.path).is_none())
        .collect();
    // In the order their contents were last written: a chain from one to
    // the next changes least.  It is the ID order but after a rollback,
    // where snapper's old root (1) was written last of all.
    cands.sort_by_key(|s| {
        (
            s.generation.unwrap_or(u64::MAX),
            s.id.unwrap_or(u64::MAX),
            s.path.clone(),
        )
    });
    let cand_paths: BTreeSet<String> = cands.iter().map(|s| s.path.clone()).collect();
    let mut accepted: Vec<(String, Shared)> = Vec::new(); // (family, shared)
    for s in cands {
        let Some(backup) = backup_path(mounts, &s.path) else {
            continue;
        };
        // Subvolumes nested in it: a snapper slot's are its children (each
        // slot's /etc, on transactional-update), unless one is mounted;
        // any other snapshot with one is restored in full.
        let inner: Vec<&Sub> = subs
            .iter()
            .filter(|o| o.path != s.path && under(&o.path, &s.path))
            .collect();
        if let Some(i) = inner
            .iter()
            .find(|i| snapper_slot(&s.path).is_none() || mounted.contains(i.path.as_str()))
        {
            plan.notes.push(format!("{}: {} is nested in it, so it is restored as a full copy, not as a snapshot sharing its extents", s.path, i.path));
            continue;
        }
        let family = match snapper_slot(&s.path) {
            Some((dir, _)) => format!("snapper:{dir}"),
            None => format!("origin:{}", s.parent_uuid.clone().unwrap_or_default()),
        };
        let restored = |p: &str, acc: &[(String, Shared)]| {
            mounted.contains(p) || acc.iter().any(|(_, a)| a.path == p)
        };
        let origin = s
            .parent_uuid
            .as_deref()
            .and_then(|p| by_uuid.get(p))
            .map(|o| o.path.clone())
            .filter(|o| restored(o, &accepted));
        // The family's live subvolume: for snapper, the one mounted where
        // the .snapshots directory's parent is in the backup.
        let anchor = match snapper_slot(&s.path) {
            Some(_) => {
                let x = backup
                    .strip_suffix("/snapshot")
                    .and_then(|b| b.rsplit_once('/'))
                    .and_then(|(b, _)| b.strip_suffix("/.snapshots"))
                    .map(|x| if x.is_empty() { "/" } else { x });
                x.and_then(|x| mounts.iter().find(|m| m.target == x))
                    .map(|m| (m.target.clone(), m.subvol.clone()))
            }
            None => origin
                .as_ref()
                .filter(|o| mounted.contains(o.as_str()))
                .and_then(|o| mounts.iter().find(|m| &m.subvol == o))
                .map(|m| (m.target.clone(), m.subvol.clone())),
        };
        let prev = accepted
            .iter()
            .rev()
            .find(|(f, _)| *f == family)
            .map(|(_, a)| a.path.clone());
        let Some(base) = prev
            .or(origin)
            .or_else(|| anchor.as_ref().map(|a| a.1.clone()))
        else {
            plan.notes.push(format!(
                "{}: nothing restored before it to make it from, so it is restored as a full copy",
                s.path
            ));
            continue;
        };
        // Its children, parents first: each a snapshot of the base's own
        // subvolume at the same place, if the base has one made before it
        // (mounted, so made by format, or a child of an earlier shared one).
        let mut kids = inner.clone();
        kids.sort_by_key(|k| {
            (
                k.path.matches('/').count(),
                k.id.unwrap_or(u64::MAX),
                k.path.clone(),
            )
        });
        let children = kids
            .iter()
            .map(|k| {
                let rest = rest_of(&k.path, &s.path).to_string();
                let cand = format!("{base}/{rest}");
                let made = subs.iter().any(|o| o.path == cand)
                    && (mounted.contains(base.as_str()) && !cand_paths.contains(&cand)
                        || accepted
                            .iter()
                            .any(|(_, a)| a.children.iter().any(|c| c.path == cand)));
                Child {
                    path: k.path.clone(),
                    rest,
                    id: k.id,
                    new_id: 0,
                    placeholders: vec![],
                    base: made.then_some(cand),
                    readonly: k.readonly,
                }
            })
            .collect();
        accepted.push((
            family,
            Shared {
                path: s.path.clone(),
                id: s.id,
                new_id: 0,
                placeholders: vec![],
                backup,
                base,
                readonly: s.readonly,
                clone_from: anchor,
                children,
            },
        ));
    }
    // IDs: format makes the rest in ID order; these come after, in theirs,
    // each shared snapshot's children right after it.
    let shared: BTreeSet<String> = accepted
        .iter()
        .flat_map(|(_, a)| {
            std::iter::once(a.path.clone()).chain(a.children.iter().map(|c| c.path.clone()))
        })
        .collect();
    let mut next = subs
        .iter()
        .filter(|s| !shared.contains(&s.path))
        .filter_map(|s| s.id)
        .max()
        .map_or(256, |m| m + 1)
        .max(256);
    let accepted = interleave(accepted, next);
    let mut assign = |path: &str, id: Option<u64>, notes: &mut Vec<String>| -> (u64, Vec<u64>) {
        match id {
            Some(id) if keeps(id, next) => {
                let ph = (next..id).collect();
                next = id + 1;
                (id, ph)
            }
            Some(id) => {
                notes.push(format!("{path}: ID {id} on the source, {next} here (a subvolume made after it on the source is made first here)"));
                next += 1;
                (next - 1, vec![])
            }
            None => {
                next += 1;
                (next - 1, vec![])
            }
        }
    };
    for (_, mut a) in accepted {
        (a.new_id, a.placeholders) = assign(&a.path, a.id, &mut plan.notes);
        for c in a.children.iter_mut() {
            (c.new_id, c.placeholders) = assign(&c.path, c.id, &mut plan.notes);
        }
        plan.shared.push(a);
    }
    plan
}

/// A subvolume with the source's ID ID gets it here when NEXT is the next
/// free ID: placeholders burn the gap, up to 4096 of them.
fn keeps(id: u64, next: u64) -> bool {
    id >= next && id - next <= 4096
}

/// The next free ID after A and its children are made from NEXT, and how
/// many of them don't get the source's ID (plan's assign).
fn step(a: &Shared, next: u64) -> (u64, usize) {
    std::iter::once(a.id)
        .chain(a.children.iter().map(|c| c.id))
        .fold((next, 0), |(n, moved), id| match id {
            Some(id) if keeps(id, n) => (id + 1, moved),
            Some(_) => (n + 1, moved + 1),
            None => (n + 1, moved),
        })
}

/// The shared snapshots in the order they are made, and so numbered, from
/// NEXT, the first free ID.  Each family's own order stays ACCEPTED's
/// (their contents', which its bases chain along); between families the
/// order shares nothing, but it costs IDs.  So the families are merged by
/// source ID: of their next members, the one with the lowest ID is made
/// first, so that a snapshot keeps its source ID whenever its own family's
/// order allows.  Each waits until what it is made from is made: its base
/// and its children's (another family's shared snapshot, as its origin).
/// Where a family's own order costs IDs (one can't keep its ID, as after a
/// rollback), taking that one first can cost another family's; then the
/// merge that takes the members that keep their IDs first, or ACCEPTED's
/// order, if one of those keeps more.
fn interleave(accepted: Vec<(String, Shared)>, next: u64) -> Vec<(String, Shared)> {
    let moved = |o: &[usize]| {
        o.iter()
            .fold((next, 0), |(n, m), &i| {
                let (n, k) = step(&accepted[i].1, n);
                (n, m + k)
            })
            .1
    };
    let mut best = merge(&accepted, next, false);
    for o in [merge(&accepted, next, true), (0..accepted.len()).collect()] {
        if moved(&o) < moved(&best) {
            best = o;
        }
    }
    // Each index once (merge takes every member of every family).
    let mut slots: Vec<Option<(String, Shared)>> = accepted.into_iter().map(Some).collect();
    best.into_iter().filter_map(|i| slots[i].take()).collect()
}

/// ACCEPTED's indices, its families merged (interleave): of the ready next
/// members, the lowest source ID first; with `KEEPING_FIRST`, those that keep
/// their IDs first.  ACCEPTED's own order has every base before what is
/// made from it, so its earliest member left is always ready; that is
/// taken if no other is (it can't arise).
fn merge(accepted: &[(String, Shared)], mut next: u64, keeping_first: bool) -> Vec<usize> {
    let mut families: Vec<(&str, std::collections::VecDeque<usize>)> = Vec::new();
    for (i, (f, _)) in accepted.iter().enumerate() {
        match families.iter_mut().find(|(g, _)| g == f) {
            Some((_, q)) => q.push_back(i),
            None => families.push((f, std::collections::VecDeque::from([i]))),
        }
    }
    // What is made later: shared snapshots and their children not yet taken.
    let mut pending: BTreeSet<&str> = accepted
        .iter()
        .flat_map(|(_, a)| {
            std::iter::once(a.path.as_str()).chain(a.children.iter().map(|c| c.path.as_str()))
        })
        .collect();
    let mut out = Vec::new();
    loop {
        // (family, member) of each family's next member.
        let heads: Vec<(usize, usize)> = families
            .iter()
            .enumerate()
            .filter_map(|(k, (_, q))| Some((k, *q.front()?)))
            .collect();
        let ready = |a: &Shared| {
            !pending.contains(a.base.as_str())
                && a.children
                    .iter()
                    .all(|c| c.base.as_deref().is_none_or(|b| !pending.contains(b)))
        };
        let key = |i: usize| {
            let a = &accepted[i].1;
            (
                keeping_first && !a.id.is_some_and(|id| keeps(id, next)),
                a.id.unwrap_or(u64::MAX),
                i,
            )
        };
        let Some((k, pick)) = heads
            .iter()
            .copied()
            .filter(|&(_, i)| ready(&accepted[i].1))
            .min_by_key(|&(_, i)| key(i))
            .or_else(|| heads.iter().copied().min_by_key(|&(_, i)| i))
        else {
            break;
        };
        families[k].1.pop_front();
        let a = &accepted[pick].1;
        pending.remove(a.path.as_str());
        for c in &a.children {
            pending.remove(c.path.as_str());
        }
        next = step(a, next).0;
        out.push(pick);
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    fn sub(path: &str, id: u64, uuid: &str, parent: Option<&str>, ro: bool) -> Sub {
        Sub {
            path: path.into(),
            id: Some(id),
            generation: Some(id),
            uuid: Some(uuid.into()),
            parent_uuid: parent.map(str::to_string),
            readonly: ro,
        }
    }

    fn m(target: &str, subvol: &str) -> Mount {
        Mount {
            target: target.into(),
            subvol: subvol.into(),
        }
    }

    /// openSUSE's: / is @/.snapshots/1/snapshot, /.snapshots is @/.snapshots.
    fn suse_mounts(root: &str) -> Vec<Mount> {
        vec![
            m("/", root),
            m("/.snapshots", "@/.snapshots"),
            m("/home", "@/home"),
            m("/var", "@/var"),
        ]
    }

    fn suse(extra: &[Sub]) -> Vec<Sub> {
        let mut v = vec![
            sub("@", 256, "u256", None, false),
            sub("@/var", 257, "u257", None, false),
            sub("@/home", 262, "u262", None, false),
            sub("@/.snapshots", 263, "u263", None, false),
            sub("@/.snapshots/1/snapshot", 264, "u264", None, false),
        ];
        v.extend_from_slice(extra);
        v
    }

    #[test]
    fn tumbleweed_one_snapshot() {
        let p = plan(
            &suse(&[sub(
                "@/.snapshots/2/snapshot",
                265,
                "u265",
                Some("u264"),
                true,
            )]),
            &suse_mounts("@/.snapshots/1/snapshot"),
        );
        assert_eq!(p.aliases, vec!["/.snapshots/1/snapshot".to_string()]);
        assert_eq!(p.shared.len(), 1);
        let s = &p.shared[0];
        assert_eq!(
            (s.backup.as_str(), s.base.as_str(), s.new_id, s.readonly),
            (
                "/.snapshots/2/snapshot",
                "@/.snapshots/1/snapshot",
                265,
                true
            )
        );
        assert!(s.placeholders.is_empty());
        assert_eq!(
            s.clone_from,
            Some(("/".into(), "@/.snapshots/1/snapshot".into()))
        );
        assert_eq!(
            p.globs(),
            vec![
                "!/.snapshots/1/snapshot/**".to_string(),
                "!/.snapshots/2/snapshot/**".into()
            ]
        );
        assert!(p.notes.is_empty());
    }

    #[test]
    fn a_chain_with_gaps_keeps_ids() {
        let p = plan(
            &suse(&[
                sub("@/.snapshots/2/snapshot", 265, "u265", Some("u264"), true),
                sub("@/.snapshots/3/snapshot", 266, "u266", Some("u264"), true),
                sub("@/.snapshots/5/snapshot", 270, "u270", Some("u264"), true),
            ]),
            &suse_mounts("@/.snapshots/1/snapshot"),
        );
        let bases: Vec<(&str, &str, u64)> = p
            .shared
            .iter()
            .map(|s| (s.path.as_str(), s.base.as_str(), s.new_id))
            .collect();
        assert_eq!(
            bases,
            vec![
                ("@/.snapshots/2/snapshot", "@/.snapshots/1/snapshot", 265),
                ("@/.snapshots/3/snapshot", "@/.snapshots/2/snapshot", 266),
                ("@/.snapshots/5/snapshot", "@/.snapshots/3/snapshot", 270),
            ]
        );
        assert_eq!(p.shared[2].placeholders, vec![267, 268, 269]);
    }

    #[test]
    fn after_a_rollback() {
        // snapper rollback 2: 3 is the old root's read-only copy, 4 the new
        // root (read-write, a snapshot of 2), the default and mounted at /.
        // The old root, 1, was written until the rollback: its gen is 3's.
        let mut subs = suse(&[
            sub("@/.snapshots/2/snapshot", 265, "u265", Some("u264"), true),
            sub("@/.snapshots/3/snapshot", 266, "u266", Some("u264"), true),
            sub("@/.snapshots/4/snapshot", 267, "u267", Some("u265"), false),
        ]);
        subs.iter_mut()
            .find(|s| s.id == Some(264))
            .unwrap()
            .generation = Some(266);
        let p = plan(&subs, &suse_mounts("@/.snapshots/4/snapshot"));
        assert_eq!(p.aliases, vec!["/.snapshots/4/snapshot".to_string()]);
        let got: Vec<(&str, &str, u64)> = p
            .shared
            .iter()
            .map(|s| (s.path.as_str(), s.base.as_str(), s.new_id))
            .collect();
        // In the order of their contents: 2 (made from the live root, as 1,
        // snapper's slot without a parent, comes later), 1, then 3 (1's copy).
        assert_eq!(
            got,
            vec![
                ("@/.snapshots/2/snapshot", "@/.snapshots/4/snapshot", 268),
                ("@/.snapshots/1/snapshot", "@/.snapshots/2/snapshot", 269),
                ("@/.snapshots/3/snapshot", "@/.snapshots/1/snapshot", 270),
            ]
        );
        assert_eq!(p.notes.len(), 3, "{:?}", p.notes);
        assert!(
            p.notes
                .iter()
                .any(|n| n.starts_with("@/.snapshots/1/snapshot: ID 264 on the source, 269 here")),
            "{:?}",
            p.notes
        );
    }

    /// `BackShot2` anduinos2: a deployment snapshot of @root (262) and a
    /// personal one of @home (263), two families; 262 was written after
    /// 263 was made.  The families interleave by ID: both keep theirs.
    #[test]
    fn two_families_keep_their_ids() {
        let root = "@snapshots/anduinos-btrfs-snapshots-manager/deployments/d1/root";
        let home = "@snapshots/anduinos-btrfs-snapshots-manager/personal/snapshots/p1/home";
        let mut subs = vec![
            sub("@root", 256, "r", None, false),
            sub("@home", 257, "h", None, false),
            sub("@snapshots", 258, "s", None, false),
            sub(root, 262, "d1", Some("r"), true),
            sub(home, 263, "p1", Some("h"), true),
        ];
        subs[3].generation = Some(31);
        subs[4].generation = Some(30);
        let p = plan(
            &subs,
            &[
                m("/", "@root"),
                m("/home", "@home"),
                m("/snapshots", "@snapshots"),
            ],
        );
        let got: Vec<(&str, &str, u64, Vec<u64>)> = p
            .shared
            .iter()
            .map(|s| {
                (
                    s.path.as_str(),
                    s.base.as_str(),
                    s.new_id,
                    s.placeholders.clone(),
                )
            })
            .collect();
        assert_eq!(
            got,
            vec![
                (root, "@root", 262, vec![259, 260, 261]),
                (home, "@home", 263, vec![])
            ]
        );
        assert!(p.notes.is_empty(), "{:?}", p.notes);
    }

    /// A family's own order still costs IDs where it isn't the ID order:
    /// the rollback's chain as in `after_a_rollback`, with another family's
    /// snapshot (of @/home, written first of all) that keeps its ID.
    #[test]
    fn a_family_reorder_still_renumbers() {
        let mut subs = suse(&[
            sub("@/.snapshots/2/snapshot", 265, "u265", Some("u264"), true),
            sub("@/.snapshots/3/snapshot", 266, "u266", Some("u264"), true),
            sub("@/.snapshots/4/snapshot", 267, "u267", Some("u265"), false),
            sub("@/home/.pre-update", 271, "u271", Some("u262"), true),
        ]);
        subs.iter_mut()
            .find(|s| s.id == Some(264))
            .unwrap()
            .generation = Some(266);
        subs.iter_mut()
            .find(|s| s.id == Some(271))
            .unwrap()
            .generation = Some(263);
        let p = plan(&subs, &suse_mounts("@/.snapshots/4/snapshot"));
        let got: Vec<(&str, &str, u64)> = p
            .shared
            .iter()
            .map(|s| (s.path.as_str(), s.base.as_str(), s.new_id))
            .collect();
        assert_eq!(
            got,
            vec![
                ("@/.snapshots/2/snapshot", "@/.snapshots/4/snapshot", 268),
                ("@/.snapshots/1/snapshot", "@/.snapshots/2/snapshot", 269),
                ("@/.snapshots/3/snapshot", "@/.snapshots/1/snapshot", 270),
                ("@/home/.pre-update", "@/home", 271),
            ]
        );
        assert_eq!(p.notes.len(), 3, "{:?}", p.notes);
        assert!(
            p.notes
                .iter()
                .any(|n| n.starts_with("@/.snapshots/1/snapshot: ID 264 on the source, 269 here")),
            "{:?}",
            p.notes
        );
        // Two snapshots of @/home, the older (265) written after the newer:
        // their family's order, so 265 is renumbered.  The snapshot of @/var
        // (267) is made before it, not after: taken first, 265 would cost
        // 267 its ID too.
        let mut subs = suse(&[
            sub("@/home/a", 265, "a", Some("u262"), true),
            sub("@/home/b", 266, "b", Some("u262"), true),
            sub("@/var/.pre", 267, "v", Some("u257"), true),
        ]);
        subs[5].generation = Some(270);
        let p = plan(&subs, &suse_mounts("@/.snapshots/1/snapshot"));
        let got: Vec<(&str, &str, u64)> = p
            .shared
            .iter()
            .map(|s| (s.path.as_str(), s.base.as_str(), s.new_id))
            .collect();
        assert_eq!(
            got,
            vec![
                ("@/home/b", "@/home", 266),
                ("@/var/.pre", "@/var", 267),
                ("@/home/a", "@/home/b", 268)
            ]
        );
        assert_eq!(p.notes, vec!["@/home/a: ID 265 on the source, 268 here (a subvolume made after it on the source is made first here)".to_string()]);
    }

    /// One family's origin is another's shared snapshot: the snapshot of it
    /// is made after it, though its own ID is lower.
    #[test]
    fn a_cross_family_origin_comes_first() {
        // Made first, the snapshot of copy would keep 260 and copy 261.
        let mut subs = vec![
            sub("@", 256, "a", None, false),
            sub("@/snaps/of-copy", 260, "c", Some("b"), true),
            sub("@/snaps/copy", 261, "b", Some("a"), false),
        ];
        subs[1].generation = Some(270);
        let p = plan(&subs, &[m("/", "@")]);
        let got: Vec<(&str, &str, u64)> = p
            .shared
            .iter()
            .map(|s| (s.path.as_str(), s.base.as_str(), s.new_id))
            .collect();
        assert_eq!(
            got,
            vec![
                ("@/snaps/copy", "@", 261),
                ("@/snaps/of-copy", "@/snaps/copy", 262)
            ]
        );
        assert_eq!(p.notes, vec!["@/snaps/of-copy: ID 260 on the source, 262 here (a subvolume made after it on the source is made first here)".to_string()]);
    }

    #[test]
    fn a_subvolume_made_after_a_snapshot_moves_its_id() {
        let p = plan(
            &suse(&[
                sub("@/.snapshots/2/snapshot", 265, "u265", Some("u264"), true),
                sub("@/var/lib/docker", 266, "u266", None, false),
            ]),
            &suse_mounts("@/.snapshots/1/snapshot"),
        );
        assert_eq!(p.shared[0].new_id, 267);
        assert!(p.notes[0].contains("ID 265 on the source, 267 here"));
    }

    #[test]
    fn a_home_config_and_its_live_subvolume() {
        let p = plan(
            &suse(&[
                sub("@/home/.snapshots", 265, "u265", None, false),
                sub(
                    "@/home/.snapshots/1/snapshot",
                    266,
                    "u266",
                    Some("u262"),
                    true,
                ),
            ]),
            &suse_mounts("@/.snapshots/1/snapshot"),
        );
        let s = p.get("@/home/.snapshots/1/snapshot").unwrap();
        assert_eq!(
            (s.backup.as_str(), s.base.as_str()),
            ("/home/.snapshots/1/snapshot", "@/home")
        );
        assert_eq!(s.clone_from, Some(("/home".into(), "@/home".into())));
    }

    #[test]
    fn not_in_the_backup_or_nested() {
        // Arch's @snapshots unmounted: no backup path, handled as before.
        let subs = vec![
            sub("@", 256, "a", None, false),
            sub("@snapshots", 257, "b", None, false),
            sub("@snapshots/1/snapshot", 258, "c", Some("a"), true),
        ];
        assert!(plan(&subs, &[m("/", "@")]).shared.is_empty());
        // Mounted: restored through its mount.
        assert!(
            plan(
                &subs,
                &[
                    m("/", "@"),
                    m("/.snapshots", "@snapshots"),
                    m("/x", "@snapshots/1/snapshot")
                ]
            )
            .shared
            .is_empty()
        );
        // A subvolume nested in it.
        let mut subs2 = subs.clone();
        subs2.push(sub("@snapshots/1/snapshot/inner", 259, "d", None, false));
        let p = plan(&subs2, &[m("/", "@"), m("/.snapshots", "@snapshots")]);
        assert!(p.shared.is_empty());
        assert!(p.notes[0].contains("is nested in it"));
    }

    #[test]
    fn a_plain_snapshot_outside_snapper() {
        let subs = vec![
            sub("@", 256, "a", None, false),
            sub("@/backups/2026", 257, "b", Some("a"), true),
        ];
        let p = plan(&subs, &[m("/", "@")]);
        assert_eq!(p.shared.len(), 1);
        assert_eq!(
            (p.shared[0].backup.as_str(), p.shared[0].base.as_str()),
            ("/backups/2026", "@")
        );
        assert_eq!(p.shared[0].clone_from, Some(("/".into(), "@".into())));
    }

    /// `RustAudit1` H7-01: a path holding a glob metacharacter is excluded
    /// as itself.  A plain snapshot @/backups/pre[1] and an alias of
    /// @data[2024] under the mounted top level: their lines escape each
    /// metacharacter, where `!/backups/pre[1]/**` would have matched
    /// /backups/pre1 and not the snapshot's own directory.
    #[test]
    fn globs_escape_metacharacters() {
        let subs = vec![
            sub("@", 256, "a", None, false),
            sub("@data[2024]", 257, "d", None, false),
            sub("@/backups/pre[1]", 258, "b", Some("a"), true),
        ];
        let p = plan(
            &subs,
            &[m("/", "@"), m("/data", "@data[2024]"), m("/mnt/top", "")],
        );
        assert_eq!(p.shared.len(), 1);
        assert_eq!(p.shared[0].backup, "/backups/pre[1]");
        assert_eq!(
            p.aliases,
            vec!["/mnt/top/@".to_string(), "/mnt/top/@data[2024]".into()]
        );
        assert_eq!(
            p.globs(),
            vec![
                "!/mnt/top/@/**".to_string(),
                "!/mnt/top/@data\\[2024\\]/**".into(),
                "!/backups/pre\\[1\\]/**".into()
            ]
        );
        // Each metacharacter, and the backslash itself.
        for (path, want) in [
            ("/b/pre[1]", "/b/pre\\[1\\]"),
            ("/b/a*b", "/b/a\\*b"),
            ("/b/a?b", "/b/a\\?b"),
            ("/b/{x,y}", "/b/\\{x,y\\}"),
            ("/b/a\\b", "/b/a\\\\b"),
            ("/b/[!^]-,~#! ", "/b/\\[!^\\]-,~#! "),
        ] {
            assert_eq!(glob_literal(path), want, "{path}");
        }
        // Ordinary paths are unchanged, so are their plans.
        assert_eq!(
            glob_literal("/.snapshots/1/snapshot"),
            "/.snapshots/1/snapshot"
        );
        assert_eq!(
            glob_literal("/home/über café/x-y_z.@+%"),
            "/home/über café/x-y_z.@+%"
        );
        // globset's reading of a backslash escape (any character after it is
        // literal) gives the path back, and no metacharacter is left bare.
        for path in ["/a[b]c*d?e{f,g}h\\i", "/[[]]**??{{}}\\\\", "/plain"] {
            let g = glob_literal(path);
            let (mut back, mut it) = (String::new(), g.chars());
            while let Some(c) = it.next() {
                assert!(!"*?[]{}".contains(c), "{g}: bare {c}");
                back.push(if c == '\\' {
                    it.next().expect("a dangling escape")
                } else {
                    c
                });
            }
            assert_eq!(back, path);
        }
    }

    #[test]
    fn aliases_and_covered_paths() {
        // The top level mounted too: every mounted subvolume shows again
        // under it.
        let ms = vec![m("/", "@"), m("/home", "@home"), m("/mnt/top", "")];
        assert_eq!(
            aliases(&ms),
            vec!["/mnt/top/@".to_string(), "/mnt/top/@home".into()]
        );
        // Nested and mounted where it is: no alias.
        assert!(aliases(&[m("/", "@"), m("/var", "@/var")]).is_empty());
        assert_eq!(
            backup_path(&[m("/", "@"), m("/var", "@/var")], "@/var/lib/x"),
            Some("/var/lib/x".into())
        );
        assert_eq!(
            backup_path(&[m("/", "@"), m("/var", "@/var")], "@other"),
            None
        );
    }

    /// The microos guest's (docs/design/aeon.md, "What M1 found"): / is
    /// slot 2, the default; each slot has its /etc nested in it, 2's a
    /// snapshot of 1's, made after @/var's and the other subvolumes.
    fn microos(extra: &[Sub]) -> Vec<Sub> {
        let mut v = vec![
            sub("@", 256, "u256", None, false),
            sub("@/.snapshots", 257, "u257", None, false),
            sub("@/.snapshots/1/snapshot", 258, "u258", None, true),
            sub("@/home", 259, "u259", None, false),
            sub("@/var", 263, "u263", None, false),
            sub("@/boot/writable", 264, "u264", None, false),
            sub("@/.snapshots/1/snapshot/etc", 268, "u268", None, false),
            sub("@/.snapshots/2/snapshot", 269, "u269", Some("u258"), true),
            sub(
                "@/.snapshots/2/snapshot/etc",
                270,
                "u270",
                Some("u268"),
                false,
            ),
            sub("@/var/lib/machines", 272, "u272", None, false),
        ];
        // gen as on the guest: 1 written last at 29, its /etc at 45, 2 at 47,
        // 2's /etc (the live one) after everything.
        for (id, generation) in [(258, 29), (268, 45), (269, 47), (270, 87)] {
            v.iter_mut().find(|s| s.id == Some(id)).unwrap().generation = Some(generation);
        }
        v.extend_from_slice(extra);
        v
    }

    fn microos_mounts(root: &str) -> Vec<Mount> {
        vec![
            m("/", root),
            m("/.snapshots", "@/.snapshots"),
            m("/home", "@/home"),
            m("/var", "@/var"),
            m("/boot/writable", "@/boot/writable"),
        ]
    }

    #[test]
    fn slot_children() {
        assert_eq!(
            slot_child("@/.snapshots/1/snapshot/etc"),
            Some(("@/.snapshots/1/snapshot", "etc"))
        );
        assert_eq!(
            slot_child("@/.snapshots/12/snapshot/var/lib/x"),
            Some(("@/.snapshots/12/snapshot", "var/lib/x"))
        );
        assert_eq!(
            slot_child("@/home/.snapshots/3/snapshot/etc"),
            Some(("@/home/.snapshots/3/snapshot", "etc"))
        );
        for p in [
            "@/.snapshots/1/snapshot",
            "@/.snapshots/x/snapshot/etc",
            "@/snapshot/etc",
            "@/var/lib/machines",
            "@/.snapshots/1/snapshots/etc",
        ] {
            assert_eq!(slot_child(p), None, "{p}");
        }
    }

    /// Slot 1 is shared, made from the live root (2) with its /etc as a
    /// child, a snapshot of the live /etc; 2's /etc is made by format.
    /// Both get new IDs: @/var/lib/machines (272) is made first.
    #[test]
    fn microos_nested_etc() {
        let p = plan(&microos(&[]), &microos_mounts("@/.snapshots/2/snapshot"));
        assert_eq!(p.aliases, vec!["/.snapshots/2/snapshot".to_string()]);
        assert_eq!(p.shared.len(), 1, "{p:?}");
        let s = &p.shared[0];
        assert_eq!(
            (
                s.path.as_str(),
                s.backup.as_str(),
                s.base.as_str(),
                s.new_id,
                s.readonly
            ),
            (
                "@/.snapshots/1/snapshot",
                "/.snapshots/1/snapshot",
                "@/.snapshots/2/snapshot",
                273,
                true
            )
        );
        assert_eq!(
            s.clone_from,
            Some(("/".into(), "@/.snapshots/2/snapshot".into()))
        );
        assert_eq!(
            s.children,
            vec![Child {
                path: "@/.snapshots/1/snapshot/etc".into(),
                rest: "etc".into(),
                id: Some(268),
                new_id: 274,
                placeholders: vec![],
                base: Some("@/.snapshots/2/snapshot/etc".into()),
                readonly: false
            }]
        );
        assert!(
            p.is_shared("@/.snapshots/1/snapshot/etc")
                && !p.is_shared("@/.snapshots/2/snapshot/etc")
                && !p.is_shared("@/.snapshots/2/snapshot")
        );
        assert_eq!(
            (p.new_id_of(258), p.new_id_of(268), p.new_id_of(269)),
            (Some(273), Some(274), None)
        );
        assert_eq!(p.new_id_of_path("@/.snapshots/1/snapshot/etc"), Some(274));
        // The child's contents are the slot's backup path's: one glob.
        assert_eq!(
            p.globs(),
            vec![
                "!/.snapshots/2/snapshot/**".to_string(),
                "!/.snapshots/1/snapshot/**".into()
            ]
        );
        assert_eq!(p.notes.len(), 2, "{:?}", p.notes);
        assert!(
            p.notes[0].starts_with("@/.snapshots/1/snapshot: ID 258 on the source, 273 here"),
            "{:?}",
            p.notes
        );
        assert!(
            p.notes[1].starts_with("@/.snapshots/1/snapshot/etc: ID 268 on the source, 274 here"),
            "{:?}",
            p.notes
        );
    }

    /// A pending update: slot 3 is the default, not booted (load mounts the
    /// booted slot, 2, at / to restore the backup's /).  3 is shared, made
    /// from 1 (its family's earlier member), its /etc from 1's /etc, the
    /// child made just before; and a slot whose /etc is mounted elsewhere
    /// falls back to a full copy.
    #[test]
    fn microos_pending_update() {
        let three = [
            sub("@/.snapshots/3/snapshot", 275, "u275", Some("u269"), true),
            sub(
                "@/.snapshots/3/snapshot/etc",
                276,
                "u276",
                Some("u270"),
                false,
            ),
        ];
        let p = plan(&microos(&three), &microos_mounts("@/.snapshots/2/snapshot"));
        let got: Vec<(&str, &str, u64, Vec<(&str, Option<&str>, u64)>)> = p
            .shared
            .iter()
            .map(|s| {
                (
                    s.path.as_str(),
                    s.base.as_str(),
                    s.new_id,
                    s.children
                        .iter()
                        .map(|c| (c.path.as_str(), c.base.as_deref(), c.new_id))
                        .collect(),
                )
            })
            .collect();
        assert_eq!(
            got,
            vec![
                (
                    "@/.snapshots/1/snapshot",
                    "@/.snapshots/2/snapshot",
                    273,
                    vec![(
                        "@/.snapshots/1/snapshot/etc",
                        Some("@/.snapshots/2/snapshot/etc"),
                        274
                    )]
                ),
                (
                    "@/.snapshots/3/snapshot",
                    "@/.snapshots/1/snapshot",
                    275,
                    vec![(
                        "@/.snapshots/3/snapshot/etc",
                        Some("@/.snapshots/1/snapshot/etc"),
                        276
                    )]
                ),
            ]
        );
        assert!(
            p.shared[1].placeholders.is_empty() && p.shared[1].children[0].placeholders.is_empty()
        );
        // 3's /etc mounted somewhere: 3 is restored in full.
        let mut ms = microos_mounts("@/.snapshots/2/snapshot");
        ms.push(m("/mnt/etc3", "@/.snapshots/3/snapshot/etc"));
        let p = plan(&microos(&three), &ms);
        assert!(
            p.get("@/.snapshots/3/snapshot").is_none()
                && p.get("@/.snapshots/1/snapshot").is_some()
        );
        assert!(p.notes.iter().any(|n| n == "@/.snapshots/3/snapshot: @/.snapshots/3/snapshot/etc is nested in it, so it is restored as a full copy, not as a snapshot sharing its extents"), "{:?}", p.notes);
    }

    /// A child keeps the source's ID where the IDs below it are free: its
    /// placeholders burn the gap between it and its slot.
    #[test]
    fn a_child_keeps_its_id() {
        let subs = vec![
            sub("@", 256, "a", None, false),
            sub("@/.snapshots", 257, "b", None, false),
            sub("@/.snapshots/1/snapshot", 258, "c", None, false),
            sub("@/.snapshots/2/snapshot", 260, "d", Some("c"), true),
            sub("@/.snapshots/2/snapshot/etc", 263, "e", None, false),
        ];
        let p = plan(
            &subs,
            &[
                m("/", "@/.snapshots/1/snapshot"),
                m("/.snapshots", "@/.snapshots"),
            ],
        );
        let s = p.get("@/.snapshots/2/snapshot").unwrap();
        assert_eq!((s.new_id, s.placeholders.clone()), (260, vec![259]));
        assert_eq!(
            (
                s.children[0].new_id,
                s.children[0].placeholders.clone(),
                s.children[0].base.clone()
            ),
            (263, vec![261, 262], None)
        );
        assert!(p.notes.is_empty(), "{:?}", p.notes);
    }

    #[test]
    fn snapper_slots() {
        assert_eq!(
            snapper_slot("@/.snapshots/12/snapshot"),
            Some(("@/.snapshots", 12))
        );
        assert_eq!(
            snapper_slot(".snapshots/1/snapshot"),
            Some((".snapshots", 1))
        );
        assert_eq!(snapper_slot("@/.snapshots/x/snapshot"), None);
        assert_eq!(snapper_slot("@/snaps/1/snapshot"), None);
    }
}
