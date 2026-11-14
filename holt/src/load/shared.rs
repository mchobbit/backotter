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
    pub gen: Option<u64>,
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
}

#[derive(Debug, Default, Clone, PartialEq)]
pub struct Plan {
    /// Backup paths where a mounted subvolume shows up a second time.
    pub aliases: Vec<String>,
    /// In the order they are made: their contents' (`gen`), which is the
    /// source's ID order but after a rollback.
    pub shared: Vec<Shared>,
    pub notes: Vec<String>,
}

impl Plan {
    pub fn is_shared(&self, path: &str) -> bool {
        self.shared.iter().any(|s| s.path == path)
    }

    pub fn get(&self, path: &str) -> Option<&Shared> {
        self.shared.iter().find(|s| s.path == path)
    }

    /// The main restore's exclusions, as `--glob-file` lines: everything
    /// below an alias (its directory stays: the subvolume's root, whose
    /// metadata a backup of `/` holds only there) and below a shared
    /// snapshot's directory (which stays, empty: the backup has it).  A
    /// glob excludes only what it matches, not what is below it: hence
    /// `/**`.
    pub fn globs(&self) -> Vec<String> {
        let mut g: Vec<String> = self.aliases.iter().map(|a| format!("!{a}/**")).collect();
        g.extend(self.shared.iter().map(|s| format!("!{}/**", s.backup)));
        g
    }
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
    if outer.is_empty() { p } else { p[outer.len()..].trim_start_matches('/') }
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
    let m = mounts.iter().filter(|m| m.subvol != path && under(path, &m.subvol)).max_by_key(|m| (m.subvol.len(), std::cmp::Reverse(m.target.clone())))?;
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

/// The shared snapshots of a filesystem with these subvolumes and mounts,
/// and the IDs everything gets.
pub fn plan(subs: &[Sub], mounts: &[Mount]) -> Plan {
    let mut plan = Plan { aliases: aliases(mounts), ..Plan::default() };
    let mounted: BTreeSet<&str> = mounts.iter().map(|m| m.subvol.as_str()).collect();
    let by_uuid: BTreeMap<&str, &Sub> = subs.iter().filter_map(|s| Some((s.uuid.as_deref()?, s))).collect();
    let mut cands: Vec<&Sub> = subs
        .iter()
        .filter(|s| !mounted.contains(s.path.as_str()))
        .filter(|s| s.parent_uuid.as_deref().is_some_and(|p| by_uuid.contains_key(p)) || snapper_slot(&s.path).is_some())
        .collect();
    // In the order their contents were last written: a chain from one to
    // the next changes least.  It is the ID order but after a rollback,
    // where snapper's old root (1) was written last of all.
    cands.sort_by_key(|s| (s.gen.unwrap_or(u64::MAX), s.id.unwrap_or(u64::MAX), s.path.clone()));
    let mut accepted: Vec<(String, Shared)> = Vec::new(); // (family, shared)
    for s in cands {
        let Some(backup) = backup_path(mounts, &s.path) else { continue };
        if let Some(inner) = subs.iter().find(|o| o.path != s.path && under(&o.path, &s.path)) {
            plan.notes.push(format!("{}: {} is nested in it, so it is restored as a full copy, not as a snapshot sharing its extents", s.path, inner.path));
            continue;
        }
        let family = match snapper_slot(&s.path) {
            Some((dir, _)) => format!("snapper:{dir}"),
            None => format!("origin:{}", s.parent_uuid.clone().unwrap_or_default()),
        };
        let restored = |p: &str, acc: &[(String, Shared)]| mounted.contains(p) || acc.iter().any(|(_, a)| a.path == p);
        let origin = s.parent_uuid.as_deref().and_then(|p| by_uuid.get(p)).map(|o| o.path.clone()).filter(|o| restored(o, &accepted));
        // The family's live subvolume: for snapper, the one mounted where
        // the .snapshots directory's parent is in the backup.
        let anchor = match snapper_slot(&s.path) {
            Some(_) => {
                let x = backup.strip_suffix("/snapshot").and_then(|b| b.rsplit_once('/')).and_then(|(b, _)| b.strip_suffix("/.snapshots")).map(|x| if x.is_empty() { "/" } else { x });
                x.and_then(|x| mounts.iter().find(|m| m.target == x)).map(|m| (m.target.clone(), m.subvol.clone()))
            }
            None => origin.as_ref().filter(|o| mounted.contains(o.as_str())).and_then(|o| mounts.iter().find(|m| &m.subvol == o)).map(|m| (m.target.clone(), m.subvol.clone())),
        };
        let prev = accepted.iter().rev().find(|(f, _)| *f == family).map(|(_, a)| a.path.clone());
        let Some(base) = prev.or(origin).or_else(|| anchor.as_ref().map(|a| a.1.clone())) else {
            plan.notes.push(format!("{}: nothing restored before it to make it from, so it is restored as a full copy", s.path));
            continue;
        };
        accepted.push((
            family,
            Shared { path: s.path.clone(), id: s.id, new_id: 0, placeholders: vec![], backup, base, readonly: s.readonly, clone_from: anchor },
        ));
    }
    // IDs: format makes the rest in ID order; these come after, in theirs.
    let shared: BTreeSet<String> = accepted.iter().map(|(_, a)| a.path.clone()).collect();
    let mut next = subs.iter().filter(|s| !shared.contains(&s.path)).filter_map(|s| s.id).max().map_or(256, |m| m + 1).max(256);
    for (_, mut a) in accepted {
        match a.id {
            Some(id) if id >= next && id - next <= 4096 => {
                a.placeholders = (next..id).collect();
                a.new_id = id;
                next = id + 1;
            }
            Some(id) => {
                plan.notes.push(format!("{}: ID {id} on the source, {next} here (a subvolume made after it on the source is made first here)", a.path));
                a.new_id = next;
                next += 1;
            }
            None => {
                a.new_id = next;
                next += 1;
            }
        }
        plan.shared.push(a);
    }
    plan
}

#[cfg(test)]
mod tests {
    use super::*;

    fn sub(path: &str, id: u64, uuid: &str, parent: Option<&str>, ro: bool) -> Sub {
        Sub { path: path.into(), id: Some(id), gen: Some(id), uuid: Some(uuid.into()), parent_uuid: parent.map(str::to_string), readonly: ro }
    }

    fn m(target: &str, subvol: &str) -> Mount {
        Mount { target: target.into(), subvol: subvol.into() }
    }

    /// openSUSE's: / is @/.snapshots/1/snapshot, /.snapshots is @/.snapshots.
    fn suse_mounts(root: &str) -> Vec<Mount> {
        vec![m("/", root), m("/.snapshots", "@/.snapshots"), m("/home", "@/home"), m("/var", "@/var")]
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
        let p = plan(&suse(&[sub("@/.snapshots/2/snapshot", 265, "u265", Some("u264"), true)]), &suse_mounts("@/.snapshots/1/snapshot"));
        assert_eq!(p.aliases, vec!["/.snapshots/1/snapshot".to_string()]);
        assert_eq!(p.shared.len(), 1);
        let s = &p.shared[0];
        assert_eq!((s.backup.as_str(), s.base.as_str(), s.new_id, s.readonly), ("/.snapshots/2/snapshot", "@/.snapshots/1/snapshot", 265, true));
        assert!(s.placeholders.is_empty());
        assert_eq!(s.clone_from, Some(("/".into(), "@/.snapshots/1/snapshot".into())));
        assert_eq!(p.globs(), vec!["!/.snapshots/1/snapshot/**".to_string(), "!/.snapshots/2/snapshot/**".into()]);
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
        let bases: Vec<(&str, &str, u64)> = p.shared.iter().map(|s| (s.path.as_str(), s.base.as_str(), s.new_id)).collect();
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
        subs.iter_mut().find(|s| s.id == Some(264)).unwrap().gen = Some(266);
        let p = plan(&subs, &suse_mounts("@/.snapshots/4/snapshot"));
        assert_eq!(p.aliases, vec!["/.snapshots/4/snapshot".to_string()]);
        let got: Vec<(&str, &str, u64)> = p.shared.iter().map(|s| (s.path.as_str(), s.base.as_str(), s.new_id)).collect();
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
        assert!(p.notes.iter().any(|n| n.starts_with("@/.snapshots/1/snapshot: ID 264 on the source, 269 here")), "{:?}", p.notes);
    }

    #[test]
    fn a_subvolume_made_after_a_snapshot_moves_its_id() {
        let p = plan(
            &suse(&[sub("@/.snapshots/2/snapshot", 265, "u265", Some("u264"), true), sub("@/var/lib/docker", 266, "u266", None, false)]),
            &suse_mounts("@/.snapshots/1/snapshot"),
        );
        assert_eq!(p.shared[0].new_id, 267);
        assert!(p.notes[0].contains("ID 265 on the source, 267 here"));
    }

    #[test]
    fn a_home_config_and_its_live_subvolume() {
        let p = plan(
            &suse(&[sub("@/home/.snapshots", 265, "u265", None, false), sub("@/home/.snapshots/1/snapshot", 266, "u266", Some("u262"), true)]),
            &suse_mounts("@/.snapshots/1/snapshot"),
        );
        let s = p.get("@/home/.snapshots/1/snapshot").unwrap();
        assert_eq!((s.backup.as_str(), s.base.as_str()), ("/home/.snapshots/1/snapshot", "@/home"));
        assert_eq!(s.clone_from, Some(("/home".into(), "@/home".into())));
    }

    #[test]
    fn not_in_the_backup_or_nested() {
        // Arch's @snapshots unmounted: no backup path, handled as before.
        let subs = vec![sub("@", 256, "a", None, false), sub("@snapshots", 257, "b", None, false), sub("@snapshots/1/snapshot", 258, "c", Some("a"), true)];
        assert!(plan(&subs, &[m("/", "@")]).shared.is_empty());
        // Mounted: restored through its mount.
        assert!(plan(&subs, &[m("/", "@"), m("/.snapshots", "@snapshots"), m("/x", "@snapshots/1/snapshot")]).shared.is_empty());
        // A subvolume nested in it.
        let mut subs2 = subs.clone();
        subs2.push(sub("@snapshots/1/snapshot/inner", 259, "d", None, false));
        let p = plan(&subs2, &[m("/", "@"), m("/.snapshots", "@snapshots")]);
        assert!(p.shared.is_empty());
        assert!(p.notes[0].contains("is nested in it"));
    }

    #[test]
    fn a_plain_snapshot_outside_snapper() {
        let subs = vec![sub("@", 256, "a", None, false), sub("@/backups/2026", 257, "b", Some("a"), true)];
        let p = plan(&subs, &[m("/", "@")]);
        assert_eq!(p.shared.len(), 1);
        assert_eq!((p.shared[0].backup.as_str(), p.shared[0].base.as_str()), ("/backups/2026", "@"));
        assert_eq!(p.shared[0].clone_from, Some(("/".into(), "@".into())));
    }

    #[test]
    fn aliases_and_covered_paths() {
        // The top level mounted too: every mounted subvolume shows again
        // under it.
        let ms = vec![m("/", "@"), m("/home", "@home"), m("/mnt/top", "")];
        assert_eq!(aliases(&ms), vec!["/mnt/top/@".to_string(), "/mnt/top/@home".into()]);
        // Nested and mounted where it is: no alias.
        assert!(aliases(&[m("/", "@"), m("/var", "@/var")]).is_empty());
        assert_eq!(backup_path(&[m("/", "@"), m("/var", "@/var")], "@/var/lib/x"), Some("/var/lib/x".into()));
        assert_eq!(backup_path(&[m("/", "@"), m("/var", "@/var")], "@other"), None);
    }

    #[test]
    fn snapper_slots() {
        assert_eq!(snapper_slot("@/.snapshots/12/snapshot"), Some(("@/.snapshots", 12)));
        assert_eq!(snapper_slot(".snapshots/1/snapshot"), Some((".snapshots", 1)));
        assert_eq!(snapper_slot("@/.snapshots/x/snapshot"), None);
        assert_eq!(snapper_slot("@/snaps/1/snapshot"), None);
    }
}
