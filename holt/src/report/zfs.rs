//! storage.zfs: pools from their vdev labels and zpool.cache, and what
//! the zfs tools say of the imported ones (the BSDs and illumos build theirs
//! with `zfs_section` too).

use super::Ctx;
use crate::json::{Obj, Value};
use crate::probe::Content;
use crate::sys::Sys;
use std::collections::BTreeMap;

/// Imported pools' properties and datasets, from the zfs tools.  A zpool
/// that can't be run is no ZFS (null) only while no ZFS file system is
/// mounted (ZFS_MOUNTED, from the mount table); with one mounted, or with
/// pools and a zfs that can't list their datasets, the section says why in
/// its "error" (and the dump warns), and load refuses the dump
/// (commands::build): no pools or no datasets would be a wrong answer.
pub fn zfs_tools_json(sys: &Sys, zfs_mounted: bool) -> Value {
    let tool_error = |prog: &str, what: &str, lacks: &str| -> String {
        let why = if sys.installed(prog) {
            format!("`{prog} list` failed")
        } else {
            format!("{prog} isn't installed")
        };
        let e = format!(
            "{what}, but {why}, so the dump records {lacks}; bko-holt load refuses this dump: make {prog} work and dump again"
        );
        sys.warn(e.clone());
        e
    };
    let Some(list) = sys.run("zpool", &["list", "-H", "-o", "name"]) else {
        return if zfs_mounted {
            Obj::new()
                .set(
                    "error",
                    tool_error(
                        "zpool",
                        "ZFS file systems are mounted",
                        "no pools or datasets",
                    ),
                )
                .build()
        } else {
            Value::Null
        };
    };
    let mut pools = Vec::new();
    for name in list.lines().map(str::trim).filter(|l| !l.is_empty()) {
        let mut props = Obj::new();
        let mut local = Obj::new();
        let mut features = Obj::new();
        if let Some(out) = sys.run(
            "zpool",
            &[
                "get",
                "-H",
                "-p",
                "-o",
                "property,value,source",
                "all",
                name,
            ],
        ) {
            for l in out.lines() {
                let f: Vec<&str> = l.split('\t').collect();
                if f.len() < 3 {
                    continue;
                }
                if let Some(feat) = f[0].strip_prefix("feature@") {
                    features.put(feat, f[1]);
                } else if !matches!(
                    f[0],
                    "free"
                        | "allocated"
                        | "fragmentation"
                        | "capacity"
                        | "freeing"
                        | "leaked"
                        | "expandsize"
                        | "checkpoint"
                        | "dedupratio"
                        | "load_guid"
                        | "bcloneused"
                        | "bclonesaved"
                        | "bcloneratio"
                        | "dedup_table_size"
                        | "dedupcached"
                        | "dedupsaved"
                        | "dedupused"
                ) {
                    props.put(f[0], f[1]);
                    // What `zpool create -o` must set again: the properties
                    // set on this pool rather than defaulted.
                    if f[2] == "local" {
                        local.put(f[0], f[1]);
                    }
                }
            }
        }
        pools.push(
            Obj::new()
                .set("name", name)
                .set("properties", props)
                .set("local_properties", local)
                .set("features", features)
                .set("vdevs", zpool_vdevs(sys, name))
                .build(),
        );
    }
    let cols = "name,type,used,available,referenced,mountpoint,canmount,mounted,encryption,encryptionroot,keyformat,keylocation,keystatus,origin,compression,recordsize,volsize,volblocksize,acltype,xattr,dnodesize,normalization,utf8only,casesensitivity,atime,relatime,copies,com.sun:auto-snapshot";
    // A zfs without some of them (illumos': no acltype, no relatime)
    // refuses the list: then only the columns its datasets have.
    let first = list
        .lines()
        .map(str::trim)
        .find(|l| !l.is_empty())
        .unwrap_or("")
        .to_string();
    let cols: String = match sys.run(
        "zfs",
        &["list", "-H", "-p", "-t", "filesystem,volume", "-o", cols],
    ) {
        Some(_) => cols.to_string(),
        None => {
            // Every dataset's: a volume's (volsize) aren't a filesystem's.
            let known: Vec<String> = sys
                .run("zfs", &["get", "-H", "-o", "property", "all"])
                .unwrap_or_default()
                .lines()
                .map(|l| l.trim().to_string())
                .collect();
            let _ = &first;
            // (origin, volsize and the like show only on datasets that have
            // one: kept, as every zfs has them.)
            const CLASSIC: [&str; 9] = [
                "type",
                "origin",
                "volsize",
                "volblocksize",
                "mounted",
                "encryption",
                "encryptionroot",
                "keyformat",
                "keylocation",
            ];
            cols.split(',')
                .filter(|c| {
                    *c == "name"
                        || c.contains(':')
                        || CLASSIC.contains(c)
                        || known.iter().any(|k| k == c)
                })
                .collect::<Vec<_>>()
                .join(",")
        }
    };
    let cols = cols.as_str();
    let listed = sys.run(
        "zfs",
        &["list", "-H", "-p", "-t", "filesystem,volume", "-o", cols],
    );
    let error = (listed.is_none() && !pools.is_empty())
        .then(|| tool_error("zfs", "ZFS pools are imported", "none of their datasets"));
    let datasets: Vec<Value> = listed
        .map(|out| {
            let names: Vec<&str> = cols.split(',').collect();
            out.lines()
                .map(|l| {
                    let mut o = Obj::new();
                    for (k, v) in names.iter().zip(l.split('\t')) {
                        let v = if v == "-" {
                            Value::Null
                        } else {
                            v.parse::<u64>()
                                .map(Value::UInt)
                                .unwrap_or_else(|_| Value::from(v))
                        };
                        o.put(k, v);
                    }
                    o.build()
                })
                .collect()
        })
        .unwrap_or_default();
    // Every property set on a dataset or received with it, user properties
    // included: the fixed columns above are for people to read, these are
    // what `zfs create -o` needs to make the dataset again.
    let mut props: BTreeMap<String, Obj> = BTreeMap::new();
    if let Some(out) = sys.run(
        "zfs",
        &[
            "get",
            "-H",
            "-p",
            "-t",
            "filesystem,volume",
            "-s",
            "local,received",
            "-o",
            "name,property,value,source",
            "all",
        ],
    ) {
        for (ds, prop, value, source) in parse_zfs_get(&out) {
            props
                .entry(ds)
                .or_default()
                .put(&prop, Obj::new().set("value", value).set("source", source));
        }
    }
    let datasets: Vec<Value> = datasets
        .into_iter()
        .map(|mut d| {
            let name = d
                .get("name")
                .and_then(Value::as_str)
                .unwrap_or("")
                .to_string();
            if let Value::Obj(o) = &mut d {
                o.push((
                    "properties".into(),
                    props
                        .remove(&name)
                        .map(Obj::build)
                        .unwrap_or_else(|| Obj::new().build()),
                ));
            }
            d
        })
        .collect();
    Obj::new()
        .set("imported_pools", pools)
        .set("datasets", datasets)
        .set_some("error", error)
        .build()
}

/// `zfs get -H -o name,property,value,source` lines.
pub fn parse_zfs_get(out: &str) -> Vec<(String, String, String, String)> {
    out.lines()
        .filter_map(|l| {
            let f: Vec<&str> = l.splitn(4, '\t').collect();
            (f.len() == 4).then(|| {
                (
                    f[0].to_string(),
                    f[1].to_string(),
                    f[2].to_string(),
                    f[3].to_string(),
                )
            })
        })
        .collect()
}

/// An imported pool's whole vdev tree (data, log, special, dedup, cache and
/// spare vdevs), from `zpool status -j` (OpenZFS 2.3 and later): the labels
/// carry only the data and log vdevs, and each only its own top-level one.
fn zpool_vdevs(sys: &Sys, name: &str) -> Value {
    let Some(out) = sys.run("zpool", &["status", "-j", "--json-int", "-P", name]) else {
        // OpenZFS before 2.3 (Ubuntu 24.04's 2.2) has no -j: read the text.
        if let Some(t) = sys.run("zpool", &["status", "-P", name]) {
            if let Some(v) = parse_zpool_status_text(&t, name) {
                return v;
            }
        }
        sys.warn(format!("zpool status {name} failed: the pool's vdev tree (log, cache, spare vdevs) isn't recorded"));
        return Value::Null;
    };
    match crate::json::parse(&out) {
        Ok(v) => {
            let pool = v.at(&["pools", name]);
            let pick = |k: &str| pool.and_then(|p| p.get(k)).cloned().unwrap_or(Value::Null);
            Obj::new()
                .set("vdevs", pick("vdevs"))
                .set("logs", pick("logs"))
                .set("l2cache", pick("l2cache"))
                .set("spares", pick("spares"))
                .set("special", pick("special"))
                .set("dedup", pick("dedup"))
                .build()
        }
        Err(e) => {
            sys.warn(format!(
                "zpool status -j {name}: unreadable JSON ({} at {})",
                e.message, e.offset
            ));
            Value::Null
        }
    }
}

/// `zpool status -P POOL`'s config section, shaped as `zpool status -j`'s
/// vdev tree: {"vdevs": {POOL: {"vdev_type": "root", "vdevs": {...}}},
/// "logs": {...}, "l2cache", "spares", "special", "dedup"}.  Each level of
/// the tree is indented two spaces more than its parent.
pub fn parse_zpool_status_text(text: &str, pool: &str) -> Option<Value> {
    fn vtype(name: &str) -> &'static str {
        let word = name.rsplit_once('-').map(|(w, _)| w).unwrap_or(name);
        match word {
            "mirror" => "mirror",
            "replacing" => "replacing",
            "spare" => "spare",
            w if w.starts_with("raidz") => "raidz",
            w if w.starts_with("draid") => "draid",
            _ if name.starts_with('/') => "disk",
            // illumos names a leaf by its device alone (c3t0d0s1), as do
            // FreeBSD (vtbd0p3) and others without -P.
            _ if name.chars().any(|c| c.is_ascii_digit()) && !name.contains('-') => "disk",
            _ => "unknown",
        }
    }
    fn vdev(name: &str, kids: Vec<(String, Value)>) -> Value {
        let ty = vtype(name);
        let mut o = Obj::new().set("name", name).set("vdev_type", ty);
        if ty == "disk" {
            o.put("path", name);
        }
        if !kids.is_empty() {
            let mut k = Obj::new();
            for (n, v) in kids {
                k.put(&n, v);
            }
            o.put("vdevs", k.build());
        }
        o.build()
    }
    let mut lines = text.lines().skip_while(|l| l.trim() != "config:").skip(1);
    // Sections: (key, top-level vdevs as (name, children)).
    // (section, its top-level vdevs as (name, their children as (name, grandchildren))).
    type Kids = Vec<(String, Vec<(String, Value)>)>;
    let mut sections: Vec<(&str, Vec<(String, Kids)>)> = Vec::new();
    let mut seen_header = false;
    for l in lines.by_ref() {
        let body = l.trim_start_matches('\t');
        let name = body.split_whitespace().next().unwrap_or("");
        if !seen_header {
            seen_header = name == "NAME";
            continue;
        }
        if name.is_empty() || l.starts_with("errors:") {
            break;
        }
        let indent = body.len() - body.trim_start().len();
        match indent {
            0 => {
                let key = match name {
                    n if n == pool => "vdevs",
                    "logs" => "logs",
                    "cache" => "l2cache",
                    "spares" => "spares",
                    "special" => "special",
                    "dedup" => "dedup",
                    _ => return None,
                };
                sections.push((key, Vec::new()));
            }
            2 => sections.last_mut()?.1.push((name.to_string(), Vec::new())),
            4 => sections
                .last_mut()?
                .1
                .last_mut()?
                .1
                .push((name.to_string(), Vec::new())),
            // A replacing or spare vdev inside a mirror or raidz.
            6 => sections
                .last_mut()?
                .1
                .last_mut()?
                .1
                .last_mut()?
                .1
                .push((name.to_string(), vdev(name, vec![]))),
            _ => return None,
        }
    }
    if !sections.iter().any(|(k, _)| *k == "vdevs") {
        return None;
    }
    let mut out = Obj::new();
    for key in ["vdevs", "logs", "l2cache", "spares", "special", "dedup"] {
        let Some((_, tops)) = sections.iter().find(|(k, _)| *k == key) else {
            out.put(key, Value::Null);
            continue;
        };
        let mut t = Obj::new();
        for (n, kids) in tops {
            let kids: Vec<(String, Value)> = kids
                .iter()
                .map(|(cn, gk)| (cn.clone(), vdev(cn, gk.clone())))
                .collect();
            t.put(n, vdev(n, kids));
        }
        let t = t.build();
        if key == "vdevs" {
            out.put(
                key,
                Obj::new()
                    .set(
                        pool,
                        Obj::new()
                            .set("name", pool)
                            .set("vdev_type", "root")
                            .set("vdevs", t)
                            .build(),
                    )
                    .build(),
            );
        } else {
            out.put(key, t);
        }
    }
    Some(out.build())
}

pub(super) fn zfs_json(sys: &Sys, ctx: &Ctx<'_>) -> Value {
    let members: Vec<(String, &Content)> = ctx
        .devs
        .iter()
        .flat_map(|d| d.contents.iter().map(move |c| (d.b.name.clone(), c)))
        .collect();
    zfs_section(
        sys,
        &members,
        &["/etc/zfs/zpool.cache"],
        ctx.mounts.iter().any(|m| m.fstype == "zfs"),
    )
}

/// The report's zfs section from the devices' signatures (name, content):
/// pools from their vdev labels, the first zpool.cache of `caches` there
/// is, and what zpool and zfs say of the imported pools (ZFS_MOUNTED: the
/// mount table has a file system of type zfs; see zfs_tools_json).
pub fn zfs_section(
    sys: &Sys,
    members: &[(String, &Content)],
    caches: &[&str],
    zfs_mounted: bool,
) -> Value {
    // Pools from the labels of their vdevs: each label has the pool's
    // config with its own top-level vdev; merge top-level vdevs by id.
    // pool GUID -> (first label's config, top-level vdevs by id, member devices)
    type Pool = (Value, BTreeMap<u64, Value>, Vec<String>);
    let mut pools: BTreeMap<String, Pool> = BTreeMap::new();
    for (name, c) in members {
        let (Some(cfg), "zfs_member") = (&c.extra, c.kind.as_str()) else {
            continue;
        };
        let guid = cfg
            .get("pool_guid")
            .and_then(Value::as_u64)
            .map(|g| g.to_string())
            .unwrap_or_default();
        let e = pools
            .entry(guid)
            .or_insert_with(|| (cfg.clone(), BTreeMap::new(), Vec::new()));
        e.2.push(name.clone());
        if let Some(t) = cfg.get("vdev_tree") {
            let id = t.get("id").and_then(Value::as_u64).unwrap_or(0);
            e.1.entry(id).or_insert_with(|| t.clone());
        }
    }
    let label_pools: Vec<Value> = pools
        .into_iter()
        .map(|(guid, (cfg, tops, devs))| {
            let g = |k: &str| cfg.get(k).cloned().unwrap_or(Value::Null);
            Obj::new()
                .set("name", g("name"))
                .set("pool_guid", guid)
                .set("state", g("state"))
                .set("txg", g("txg"))
                .set("version", g("version"))
                .set("hostname", g("hostname"))
                .set("hostid", g("hostid"))
                .set("vdev_children", g("vdev_children"))
                .set("top_level_vdevs", tops.into_values().collect::<Vec<_>>())
                .set("features_for_read", g("features_for_read"))
                .set("member_devices", devs)
                .build()
        })
        .collect();
    let cache = caches.iter().find_map(|p| sys.read_bytes(p)).map(|b| {
        match crate::probe::zfs::unpack(&b) {
            Ok(v) => v,
            Err(e) => Obj::new().set("error", e).build(),
        }
    });
    Obj::new()
        .set("pools_from_labels", label_pools)
        .set("zpool_cache", cache)
        .set("tools", zfs_tools_json(sys, zfs_mounted))
        .build()
}
