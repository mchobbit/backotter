//! storage.btrfs: each btrfs's devices, allocation profiles, subvolumes,
//! qgroups and properties.

use super::{Ctx, Dev};
use crate::json::{Obj, Value};
use crate::mounts::Mount;
use crate::sys::Sys;
use std::collections::BTreeMap;

/// A btrfs's allocation profiles ({"data": [...], "metadata": [...],
/// "system": [...]}) from /sys/fs/btrfs/UUID/allocation, which is there
/// only while the filesystem is mounted: None when it isn't.
pub(crate) fn btrfs_allocation(sys: &Sys, uuid: &str) -> Option<Value> {
    let sysfs = format!("/sys/fs/btrfs/{uuid}");
    if !sys.is_dir(&sysfs) {
        return None;
    }
    let mut alloc = Obj::new();
    for kind in ["data", "metadata", "system"] {
        let profiles: Vec<String> = sys
            .list(&format!("{sysfs}/allocation/{kind}"))
            .into_iter()
            .filter(|p| sys.is_dir(&format!("{sysfs}/allocation/{kind}/{p}")))
            .collect();
        alloc.put(kind, profiles);
    }
    Some(alloc.build())
}

/// storage.btrfs.  KNOWN: (UUID, allocation profiles) read earlier while
/// a mount of dump's own was there (ABRoot's future root,
/// crate::abroot::Dump::btrfs_profiles), for a filesystem sysfs no longer
/// shows.
pub(super) fn btrfs_json(sys: &Sys, ctx: &Ctx, known: &[(String, Value)]) -> Value {
    let mut fss: BTreeMap<String, Vec<&Dev>> = BTreeMap::new();
    for d in ctx.devs {
        if d.fs_type().as_deref() == Some("btrfs") {
            if let Some(u) = d.fs_uuid() {
                fss.entry(u).or_default().push(d);
            }
        }
    }
    let mut out = Vec::new();
    for (uuid, devs) in fss {
        let sysfs = format!("/sys/fs/btrfs/{uuid}");
        let alloc = btrfs_allocation(sys, &uuid).or_else(|| known.iter().find(|(u, _)| *u == uuid).map(|(_, v)| v.clone()));
        let all: Vec<&Mount> = ctx.mounts.iter().filter(|m| m.fstype == "btrfs" && devs.iter().any(|d| ctx.resolve(sys, &m.source).as_deref() == Some(d.b.name.as_str()))).collect();
        // The mounts of a subvolume's own root: not binds of a directory in
        // one (ostree's /etc, its FSROOT /root/ostree/deploy/.../etc, carries
        // the same subvol=/root as /sysroot, and may come first).
        let mut mounts: Vec<&Mount> = all.iter().copied().filter(|m| m.is_subvolume_root()).collect();
        if mounts.is_empty() {
            mounts = all.clone();
        }
        let mut subvols = Value::Null;
        let mut default = Value::Null;
        let mut qgroups = Value::Null;
        if let Some(m) = mounts.first() {
            if let Some(out) = sys.run("btrfs", &["subvolume", "list", "-a", "-p", "-u", "-q", &m.target]) {
                let ro: Vec<u64> = sys
                    .run("btrfs", &["subvolume", "list", "-a", "-r", &m.target])
                    .map(|o| o.lines().filter_map(parse_btrfs_subvol_line).filter_map(|v| v.get("id").and_then(Value::as_u64)).collect())
                    .unwrap_or_default();
                let list = out
                    .lines()
                    .filter_map(parse_btrfs_subvol_line)
                    .map(|mut v| {
                        let id = v.get("id").and_then(Value::as_u64).unwrap_or(0);
                        let path = v.get("path").and_then(Value::as_str).map(str::to_string);
                        let props = path.and_then(|p| btrfs_reachable_path(&mounts, &p)).map(|p| btrfs_properties(sys, &p));
                        if let Value::Obj(o) = &mut v {
                            o.push(("readonly".into(), Value::Bool(ro.contains(&id))));
                            o.push(("properties".into(), props.map(Obj::build).unwrap_or(Value::Null)));
                        }
                        v
                    })
                    .collect();
                subvols = Value::Arr(list);
            } else {
                // Without its list, load can't make its subvolumes: say so
                // (NixOS's btrfs is in /run/current-system/sw/bin, Guix's in
                // /run/current-system/profile/sbin).
                sys.warn(format!("btrfs {uuid}: `btrfs subvolume list {}` failed or btrfs isn't installed: its subvolumes aren't listed", m.target));
            }
            if let Some(out) = sys.run("btrfs", &["subvolume", "get-default", &m.target]) {
                default = parse_btrfs_subvol_line(out.trim()).unwrap_or(Value::from(out.trim()));
            }
            // Quotas: sysfs has a qgroups directory while they're enabled.
            if sys.is_dir(&format!("{sysfs}/qgroups")) {
                qgroups = match sys.run("btrfs", &["qgroup", "show", "-p", "-r", "-e", "--raw", &m.target]) {
                    Some(out) => Value::Arr(parse_btrfs_qgroups(&out)),
                    None => Obj::new().set("error", "btrfs qgroup show failed").build(),
                };
            }
        }
        out.push(
            Obj::new()
                .set("uuid", uuid.as_str())
                .set("label", devs.first().and_then(|d| d.fs_label()))
                .set("devices", devs.iter().map(|d| d.b.name.clone()).collect::<Vec<_>>())
                .set("mounted", !all.is_empty())
                .set("features", sys.is_dir(&sysfs).then(|| sys.list(&format!("{sysfs}/features"))))
                .set("allocation_profiles", alloc.unwrap_or(Value::Null))
                .set(
                    "mounted_subvolumes",
                    {
                        // By mount point: the mount order isn't the layout's.
                        let mut ms: Vec<_> = mounts.iter().map(|m| (m.target.clone(), m.btrfs_subvol())).collect();
                        ms.sort();
                        ms.into_iter().map(|(t, sv)| Obj::new().set("target", t).set("subvol", sv).build()).collect::<Vec<_>>()
                    },
                )
                .set("subvolumes", subvols)
                .set("default_subvolume", default)
                .set("quota_enabled", sys.is_dir(&sysfs).then(|| sys.is_dir(&format!("{sysfs}/qgroups"))))
                .set("qgroups", qgroups)
                .build(),
        );
    }
    Value::Arr(out)
}

/// Where a subvolume (its path from the top level, as `btrfs subvolume list
/// -a` prints it) can be reached through one of the filesystem's mounts.
/// Only a mount of a subvolume's own root counts: a bind of a directory
/// inside one has the subvolume's subvol= but not its contents at its top.
pub fn btrfs_reachable_path(mounts: &[&Mount], path: &str) -> Option<String> {
    let path = path.strip_prefix("<FS_TREE>/").unwrap_or(path).trim_matches('/');
    for m in mounts.iter().filter(|m| m.is_subvolume_root()) {
        let sub = m.btrfs_subvol().unwrap_or_default();
        let sub = sub.trim_matches('/');
        let rest = if sub.is_empty() {
            Some(path)
        } else if path == sub {
            Some("")
        } else {
            path.strip_prefix(sub).and_then(|r| r.strip_prefix('/'))
        };
        if let Some(r) = rest {
            let t = m.target.trim_end_matches('/');
            return Some(if r.is_empty() { m.target.clone() } else { format!("{t}/{r}") });
        }
    }
    None
}

/// A subvolume's properties: `btrfs property get` for the subvolume (ro)
/// and for its root directory's inode (compression).
fn btrfs_properties(sys: &Sys, path: &str) -> Obj {
    let mut o = Obj::new();
    for t in ["s", "i"] {
        if let Some(out) = sys.run("btrfs", &["property", "get", "-t", t, path]) {
            for (k, v) in out.lines().filter_map(|l| l.trim().split_once('=')) {
                o.put(k, v);
            }
        }
    }
    o
}

/// `btrfs qgroup show -p -r -e --raw`: qgroupid, referenced, exclusive,
/// max referenced, max exclusive, parents, and (btrfs-progs 6.x) the path.
pub fn parse_btrfs_qgroups(out: &str) -> Vec<Value> {
    let num = |s: &str| match s {
        "none" | "-" => Value::Null,
        _ => s.parse::<u64>().map(Value::UInt).unwrap_or_else(|_| Value::from(s)),
    };
    out.lines()
        .skip_while(|l| !l.trim_start().starts_with("--"))
        .skip(1)
        .filter_map(|l| {
            let w: Vec<&str> = l.split_whitespace().collect();
            if w.len() < 6 || !w[0].contains('/') {
                return None;
            }
            // "-" (btrfs-progs 6) or "---" (older) when there's no parent.
            let parents: Vec<String> = if w[5].chars().all(|c| c == '-') { vec![] } else { w[5].split(',').map(str::to_string).collect() };
            Some(
                Obj::new()
                    .set("qgroupid", w[0])
                    .set("referenced", num(w[1]))
                    .set("exclusive", num(w[2]))
                    .set("max_referenced", num(w[3]))
                    .set("max_exclusive", num(w[4]))
                    .set("parents", parents)
                    .set("path", (w.len() > 6).then(|| w[6..].join(" ")))
                    .build(),
            )
        })
        .collect()
}

/// "ID 256 gen 10 parent 5 top level 5 parent_uuid - received_uuid - uuid ... path @rootfs"
pub fn parse_btrfs_subvol_line(l: &str) -> Option<Value> {
    let w: Vec<&str> = l.split_whitespace().collect();
    if w.first() != Some(&"ID") {
        return None;
    }
    let mut o = Obj::new();
    let mut i = 0;
    while i + 1 < w.len() {
        let (key, n) = match (w[i], w.get(i + 1)) {
            ("top", Some(&"level")) => ("top_level", 2),
            ("path", _) => {
                o.put("path", w[i + 1..].join(" "));
                break;
            }
            (k, _) => (k, 1),
        };
        let v = w.get(i + n).copied().unwrap_or("");
        let v = match v.parse::<u64>() {
            Ok(n) => Value::UInt(n),
            Err(_) if v == "-" => Value::Null,
            Err(_) => Value::from(v),
        };
        o.put(&key.to_ascii_lowercase(), v);
        i += n + 1;
    }
    Some(o.build())
}
