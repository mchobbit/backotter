//! Qubes OS in `bko-holt dump` (docs/design/qubes.md), and the generic
//! pieces it needed:
//!
//! - **storage.qubes**: the pools and volumes `/var/lib/qubes/qubes.xml`
//!   names, read from dom0's root, so the dump says which qube each LV is;
//! - **raw volumes**: which LVs a backup takes as their bytes (a sparse
//!   image file at `/.bko-holt/raw/VG/LV.img` in the backup's tree) rather
//!   than as a filesystem: Qubes's volumes, thin snapshots and their
//!   origins, and generally an LV holding a partition table or nothing
//!   bko-holt load can make (raw_volumes);
//! - **thin usage**: a thin pool's used data and metadata and each thin
//!   LV's mapped bytes, from `dmsetup status`, for load's fit by
//!   provisioned data;
//! - **activation skip**: LVs that `vgchange -ay` skipped, activated
//!   read-only for the dump and let go after;
//! - **boot.xen**: the Xen hypervisor GRUB boots dom0 through.

use crate::json::{Obj, Value};
use crate::sys::Sys;

/// Where Qubes keeps its qubes and their volumes.
pub const XML: &str = "/var/lib/qubes/qubes.xml";

/// The directory of the backup's tree that holds the raw volumes' images,
/// one per LV at RAW_DIR/VG/LV.img.
pub const RAW_DIR: &str = "/.bko-holt/raw";

/// The release os-release's ID gives Qubes.
pub const OS_ID: &str = "qubes";

fn s(v: &Value, k: &str) -> Option<String> {
    v.get(k).and_then(Value::as_str).map(str::to_string)
}

// ---- qubes.xml ------------------------------------------------------------

/// One `<tag a="b">` (or `</tag>`, `<tag/>`) of an XML document, and the
/// text after it up to the next tag.
#[derive(Debug, Clone, PartialEq)]
struct Tag {
    name: String,
    close: bool,
    empty: bool,
    attrs: Vec<(String, String)>,
    text: String,
}

fn unescape(s: &str) -> String {
    s.replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("&quot;", "\"")
        .replace("&apos;", "'")
        .replace("&amp;", "&")
}

/// The document's tags in order (comments, declarations and CDATA left out).
fn tags(text: &str) -> Vec<Tag> {
    let mut out = Vec::new();
    let mut rest = text;
    while let Some(i) = rest.find('<') {
        rest = &rest[i + 1..];
        if let Some(r) = rest.strip_prefix("!--") {
            rest = r.find("-->").map_or("", |j| &r[j + 3..]);
            continue;
        }
        let Some(end) = rest.find('>') else { break };
        let body = &rest[..end];
        rest = &rest[end + 1..];
        if body.starts_with('?') || body.starts_with('!') {
            continue;
        }
        let text_end = rest.find('<').unwrap_or(rest.len());
        let txt = unescape(rest[..text_end].trim());
        let close = body.starts_with('/');
        let empty = body.ends_with('/');
        let body = body.trim_start_matches('/').trim_end_matches('/');
        let name_end = body.find(char::is_whitespace).unwrap_or(body.len());
        let name = body[..name_end].to_string();
        let mut attrs = Vec::new();
        let mut a = &body[name_end..];
        while let Some(eq) = a.find('=') {
            let key = a[..eq].trim().to_string();
            let after = a[eq + 1..].trim_start();
            let Some(q) = after.chars().next().filter(|c| *c == '"' || *c == '\'') else {
                break;
            };
            let Some(close_q) = after[1..].find(q) else {
                break;
            };
            attrs.push((key, unescape(&after[1..1 + close_q])));
            a = &after[close_q + 2..];
        }
        out.push(Tag {
            name,
            close,
            empty,
            attrs,
            text: txt,
        });
    }
    out
}

fn attr<'a>(t: &'a Tag, k: &str) -> Option<&'a str> {
    t.attrs
        .iter()
        .find(|(n, _)| n == k)
        .map(|(_, v)| v.as_str())
}

/// qubes.xml as storage.qubes records it: its pools (name, driver, and for
/// LVM the volume group and thin pool) and every qube's volumes (qube,
/// its class, volume name, pool, vid, revisions_to_keep, size, and the
/// snap_on_start/save_on_stop flags).  Nothing else of the file.
pub fn parse_xml(text: &str) -> Value {
    let mut pools = Vec::new();
    let mut volumes = Vec::new();
    let all = tags(text);
    let mut i = 0;
    // The domain being read: its class, name, and volumes so far.
    let mut dom: Option<(Option<String>, Option<String>, Vec<Obj>)> = None;
    let mut depth_props = 0usize;
    while i < all.len() {
        let t = &all[i];
        match (t.name.as_str(), t.close) {
            ("pool", false) => {
                let mut o = Obj::new()
                    .set("name", attr(t, "name"))
                    .set("driver", attr(t, "driver"));
                for k in ["volume_group", "thin_pool", "dir_path", "revisions_to_keep"] {
                    o.put_some(k, attr(t, k));
                }
                pools.push(o.build());
            }
            ("domain", false) => {
                dom = Some((attr(t, "class").map(str::to_string), None, Vec::new()));
                depth_props = 0;
            }
            ("domain", true) => {
                if let Some((class, name, vols)) = dom.take() {
                    for v in vols {
                        let mut o = Obj::new()
                            .set("qube", name.clone())
                            .set("class", class.clone());
                        o.0.extend(v.0);
                        volumes.push(o.build());
                    }
                }
            }
            ("properties", false) if !t.empty => depth_props += 1,
            ("properties", true) => depth_props = depth_props.saturating_sub(1),
            ("property", false) if depth_props == 1 && attr(t, "name") == Some("name") => {
                if let Some(d) = dom.as_mut() {
                    if d.1.is_none() {
                        d.1 = Some(t.text.clone());
                    }
                }
            }
            ("volume", false) => {
                if let Some((_, _, vols)) = dom.as_mut() {
                    let flag = |k: &str| attr(t, k).map(|v| v.eq_ignore_ascii_case("true"));
                    vols.push(
                        Obj::new()
                            .set("volume", attr(t, "name"))
                            .set("pool", attr(t, "pool"))
                            .set("vid", attr(t, "vid"))
                            .set(
                                "revisions_to_keep",
                                attr(t, "revisions_to_keep").and_then(|v| v.parse::<u64>().ok()),
                            )
                            .set(
                                "size_bytes",
                                attr(t, "size").and_then(|v| v.parse::<u64>().ok()),
                            )
                            .set("snap_on_start", flag("snap_on_start"))
                            .set("save_on_stop", flag("save_on_stop"))
                            .set("rw", flag("rw")),
                    );
                }
            }
            _ => {}
        }
        i += 1;
    }
    Obj::new()
        .set("xml", XML)
        .set("pools", pools)
        .set("volumes", volumes)
        .build()
}

/// storage.qubes: qubes.xml read from the system's root, when it is Qubes's
/// dom0 (os-release ID=qubes) and the file is there.
pub fn read(sys: &Sys, os_id: Option<&str>) -> Option<Value> {
    if os_id != Some(OS_ID) {
        return None;
    }
    match sys.read(XML) {
        Some(t) => Some(parse_xml(&t)),
        None => {
            sys.warn(format!("Qubes OS: {XML} couldn't be read, so the dump doesn't say which qube each LV is; the LVs in its pools are still taken as raw volumes by the general rule"));
            None
        }
    }
}

/// The qube volume (qube, volume) LV `vg/lv` is, by its vid: the volume
/// itself, its -snap (while the qube runs) or one of its revisions
/// (`-TIMESTAMP-back`).
pub fn volume_of<'a>(qubes: &'a Value, vg: &str, lv: &str) -> Option<&'a Value> {
    qubes
        .get("volumes")
        .and_then(Value::as_array)?
        .iter()
        .find(|v| {
            let Some((vvg, base)) = s(v, "vid").and_then(|id| {
                id.split_once('/')
                    .map(|(a, b)| (a.to_string(), b.to_string()))
            }) else {
                return false;
            };
            if vvg != vg {
                return false;
            }
            if lv == base || lv == format!("{base}-snap") {
                return true;
            }
            lv.strip_prefix(&format!("{base}-"))
                .and_then(|r| r.strip_suffix("-back"))
                .is_some_and(|ts| !ts.is_empty() && ts.bytes().all(|b| b.is_ascii_digit()))
        })
}

// ---- raw volumes ----------------------------------------------------------

/// Content types bko-holt load makes (format's, or assemble's): an LV that
/// holds anything else, unmounted, is taken raw.
const MADE: [&str; 17] = [
    "ext2",
    "ext3",
    "ext4",
    "xfs",
    "btrfs",
    "f2fs",
    "vfat",
    "msdos",
    "swap",
    "bcachefs",
    "nilfs2",
    "jfs",
    "crypto_LUKS",
    "LVM2_member",
    "linux_raid_member",
    "zfs_member",
    "xfs_external_log",
];

/// The image path of VG/LV in the backup's tree.
pub fn image_path(vg: &str, lv: &str) -> String {
    format!("{RAW_DIR}/{vg}/{lv}.img")
}

/// Which LVs are raw volumes, and why: marks each such dm device with
/// `raw_volume` {image, why, qube, volume} and gives its backup source.
/// The rule, in order: a mounted LV never (its files are backed up); a
/// Qubes volatile volume never (made empty); a volume qubes.xml names
/// (its revisions and -snap too); a thin LV that is a snapshot, or the
/// origin of one; an LV holding a partition table; an LV holding nothing
/// bko-holt recognises, or a type load can't make.  An unmounted
/// filesystem load can make stays a filesystem (made empty, as before).
pub fn raw_volumes(dm: &mut [Value], lvm: &Value, qubes: Option<&Value>) -> Vec<Value> {
    let vgs = lvm
        .get("volume_groups")
        .and_then(Value::as_array)
        .cloned()
        .unwrap_or_default();
    let lv_of = |vg: &str, lv: &str| -> Option<(Value, Value)> {
        let g = vgs.iter().find(|g| s(g, "name").as_deref() == Some(vg))?;
        let l = g
            .get("logical_volumes")
            .and_then(Value::as_array)?
            .iter()
            .find(|l| s(l, "name").as_deref() == Some(lv))?;
        Some((g.clone(), l.clone()))
    };
    let mut sources = Vec::new();
    for d in dm.iter_mut() {
        if d.at(&["dm", "target"]).and_then(Value::as_str) != Some("lvm")
            || d.at(&["dm", "lv_layer"]).is_some_and(|x| !x.is_null())
        {
            continue;
        }
        let (Some(vg), Some(lv)) = (
            d.at(&["dm", "vg"])
                .and_then(Value::as_str)
                .map(str::to_string),
            d.at(&["dm", "lv"])
                .and_then(Value::as_str)
                .map(str::to_string),
        ) else {
            continue;
        };
        let Some((g, l)) = lv_of(&vg, &lv) else {
            continue;
        };
        let visible = l
            .get("status")
            .and_then(Value::as_array)
            .is_some_and(|a| a.iter().any(|x| x.as_str() == Some("VISIBLE")));
        if !visible
            || d.get("mounted_at")
                .and_then(Value::as_array)
                .is_some_and(|a| !a.is_empty())
        {
            continue;
        }
        let seg = l.at(&["segments", "0"]).cloned().unwrap_or(Value::Null);
        let thin = s(&seg, "type").as_deref() == Some("thin");
        let origin = thin.then(|| s(&seg, "origin")).flatten();
        let is_origin = thin
            && g.get("logical_volumes")
                .and_then(Value::as_array)
                .into_iter()
                .flatten()
                .any(|o| {
                    o.at(&["segments", "0", "origin"]).and_then(Value::as_str) == Some(lv.as_str())
                });
        let q = qubes.and_then(|q| volume_of(q, &vg, &lv));
        let content_type = d
            .at(&["content", "type"])
            .and_then(Value::as_str)
            .map(str::to_string);
        let why = if q.is_some_and(|v| s(v, "volume").as_deref() == Some("volatile")) {
            None
        } else if let Some(v) = q {
            Some(format!(
                "qube {}'s {} volume, as qubes.xml names it",
                s(v, "qube").unwrap_or_default(),
                s(v, "volume").unwrap_or_default()
            ))
        } else if let Some(o) = &origin {
            Some(format!("a thin snapshot of {vg}/{o}"))
        } else if is_origin {
            Some("the origin of thin snapshots".to_string())
        } else if d.get("partition_table").is_some_and(|t| !t.is_null()) {
            Some("it holds a partition table (a disk image)".to_string())
        } else {
            match &content_type {
                None => Some("nothing bko-holt recognises is on it".to_string()),
                Some(t) if !MADE.contains(&t.as_str()) => {
                    Some(format!("it holds {t}, which bko-holt load can't make"))
                }
                Some(_) => None,
            }
        };
        let Some(why) = why else { continue };
        let image = image_path(&vg, &lv);
        let skip = l
            .get("flags")
            .and_then(Value::as_array)
            .into_iter()
            .flatten()
            .chain(
                l.get("status")
                    .and_then(Value::as_array)
                    .into_iter()
                    .flatten(),
            )
            .any(|x| x.as_str() == Some("ACTIVATION_SKIP"));
        let mut r = Obj::new()
            .set("image", image.as_str())
            .set("why", why.as_str());
        if let Some(v) = q {
            r.put("qube", s(v, "qube"));
            r.put("volume", s(v, "volume"));
        }
        // dm_json has no raw_volume, so it goes at the end.
        d.put("raw_volume", r.build());
        let source = format!("/dev/{vg}/{lv}");
        let note = format!(
            "a raw volume ({why}): the LV's bytes as a sparse image file at this path of the backup's tree, not a mount (e.g. `cp --sparse=always {source} TREE{image}`){}; load writes it back to {vg}/{lv}",
            if skip {
                "; it has activation skip: `lvchange -ay -K` it first"
            } else {
                ""
            }
        );
        sources.push(
            Obj::new()
                .set("path", image.as_str())
                .set("from", Value::Null)
                .set("device", s(d, "name"))
                .set("fstype", "raw")
                .set("source", source.as_str())
                .set("subvol", Value::Null)
                .set("note", note)
                .build(),
        );
    }
    sources
}

// ---- thin usage, activation skip ------------------------------------------

/// `dmsetup status` of a thin-pool target: (metadata used, metadata total)
/// in 4 KiB blocks and (data used, data total) in the pool's chunks.
pub fn parse_pool_status(line: &str) -> Option<((u64, u64), (u64, u64))> {
    let f: Vec<&str> = line.split_whitespace().collect();
    let at = f.iter().position(|w| *w == "thin-pool")?;
    let frac = |w: &str| -> Option<(u64, u64)> {
        let (a, b) = w.split_once('/')?;
        Some((a.parse().ok()?, b.parse().ok()?))
    };
    Some((frac(f.get(at + 2)?)?, frac(f.get(at + 3)?)?))
}

/// `dmsetup status` of a thin target: its mapped sectors.
pub fn parse_thin_status(line: &str) -> Option<u64> {
    let f: Vec<&str> = line.split_whitespace().collect();
    let at = f.iter().position(|w| *w == "thin")?;
    f.get(at + 1)?.parse().ok()
}

/// Adds data_used_bytes and metadata_used_bytes to each thin pool LV of
/// storage.lvm and mapped_bytes to each thin LV, from `dmsetup status` of
/// their device-mapper devices (the pool's -tpool layer).
pub fn thin_usage(sys: &Sys, lvm: &mut Value, dm: &[Value]) {
    // By the LV's UUID (a pool's -tpool layer is named VG-POOL-tpool).
    let dm_name = |id: &str, layer: Option<&str>| -> Option<String> {
        dm.iter()
            .find(|d| {
                d.at(&["dm", "lv_uuid"]).and_then(Value::as_str) == Some(id)
                    && d.at(&["dm", "lv_layer"]).and_then(Value::as_str) == layer
            })
            .and_then(|d| {
                d.at(&["dm", "dm_name"])
                    .and_then(Value::as_str)
                    .map(str::to_string)
            })
    };
    // lvm_json's LVs have none of these keys, so each goes at the end.
    for g in lvm
        .get_mut("volume_groups")
        .and_then(Value::as_array_mut)
        .into_iter()
        .flatten()
    {
        for l in g
            .get_mut("logical_volumes")
            .and_then(Value::as_array_mut)
            .into_iter()
            .flatten()
        {
            let id = s(l, "id").unwrap_or_default();
            let ty = l
                .at(&["segments", "0", "type"])
                .and_then(Value::as_str)
                .unwrap_or("")
                .to_string();
            let chunk = l
                .at(&["segments", "0", "chunk_size"])
                .and_then(Value::as_u64)
                .unwrap_or(0)
                * 512;
            if ty == "thin-pool" {
                let Some(n) = dm_name(&id, Some("tpool")) else {
                    continue;
                };
                if let Some(((mu, _), (du, _))) = sys
                    .run("dmsetup", &["status", &n])
                    .as_deref()
                    .and_then(parse_pool_status)
                {
                    l.put("data_used_bytes", Value::UInt(du * chunk));
                    l.put("metadata_used_bytes", Value::UInt(mu * 4096));
                }
            } else if ty == "thin" {
                let Some(n) = dm_name(&id, None) else {
                    continue;
                };
                if let Some(m) = sys
                    .run("dmsetup", &["status", &n])
                    .as_deref()
                    .and_then(parse_thin_status)
                {
                    l.put("mapped_bytes", Value::UInt(m * 512));
                }
            }
        }
    }
}

/// The LVs `vgchange -ay` skipped (activation skip, `k` in lv_attr) that
/// aren't active, activated read-only (activation/read_only_volume_list, so
/// nothing about them changes) so the dump can read them; dropping the
/// Activated lets them go again.  Only as root, and only where lvs runs.
pub fn activate_skipped(sys: &Sys) -> Activated<'_> {
    if !sys.privileged {
        return Activated {
            sys,
            lvs: Vec::new(),
        };
    }
    let Some(out) = sys.run(
        "lvs",
        &[
            "--noheadings",
            "--separator",
            "|",
            "-o",
            "vg_name,lv_name,lv_attr",
        ],
    ) else {
        return Activated {
            sys,
            lvs: Vec::new(),
        };
    };
    let mut done = Vec::new();
    for l in out.lines() {
        let f: Vec<&str> = l.trim().split('|').collect();
        let [vg, lv, attr] = f.as_slice() else {
            continue;
        };
        let a: Vec<char> = attr.chars().collect();
        if a.get(9) != Some(&'k') || a.get(4) == Some(&'a') {
            continue;
        }
        let full = format!("{vg}/{lv}");
        let cfg = format!("activation {{ read_only_volume_list = [ \"{full}\" ] }}");
        if sys
            .run(
                "lvchange",
                &[
                    "--activate",
                    "y",
                    "--ignoreactivationskip",
                    "--config",
                    &cfg,
                    &full,
                ],
            )
            .is_some()
        {
            done.push(full);
        } else {
            sys.warn(format!("{full} has activation skip and couldn't be activated read-only for the dump (lvchange -ay -K failed): its content wasn't read"));
        }
    }
    Activated { sys, lvs: done }
}

/// The LVs activate_skipped activated, deactivated when dropped, by a
/// panic's unwind too.  Drop it before taking sys.warnings(), so the
/// warnings of a deactivation that fails are kept.
#[must_use]
pub struct Activated<'a> {
    sys: &'a Sys,
    lvs: Vec<String>,
}

impl Drop for Activated<'_> {
    fn drop(&mut self) {
        deactivate(self.sys, &self.lvs);
    }
}

fn deactivate(sys: &Sys, lvs: &[String]) {
    for full in lvs {
        if sys.run("lvchange", &["--activate", "n", full]).is_none() {
            sys.warn(format!(
                "{full}: activated read-only for the dump, and `lvchange -an` failed after it"
            ));
        }
    }
}

// ---- boot.xen ---------------------------------------------------------------

/// A shell variable's value as /etc/default/grub sets it: the last
/// assignment, quotes taken off.
pub fn default_grub_var(text: &str, var: &str) -> Option<String> {
    let mut found = None;
    for l in text.lines() {
        let l = l.trim();
        if let Some(v) = l.strip_prefix(&format!("{var}=")) {
            let v = v.trim();
            let v = v
                .strip_prefix('"')
                .and_then(|x| x.strip_suffix('"'))
                .or_else(|| v.strip_prefix('\'').and_then(|x| x.strip_suffix('\'')))
                .unwrap_or(v);
            found = Some(v.to_string());
        }
    }
    found
}

/// The menu entries of a grub.cfg that boot through Xen: each one's title,
/// its multiboot2 line (the hypervisor and its arguments) and its module2
/// lines (the dom0 kernel with its command line, the initramfs).
pub fn xen_entries(cfg: &str) -> Vec<Value> {
    let mut out = Vec::new();
    let mut title: Option<String> = None;
    let mut mb: Option<String> = None;
    let mut mods: Vec<String> = Vec::new();
    let flush = |title: &mut Option<String>,
                 mb: &mut Option<String>,
                 mods: &mut Vec<String>,
                 out: &mut Vec<Value>| {
        if let Some(m) = mb.take() {
            out.push(
                Obj::new()
                    .set("title", title.clone())
                    .set("multiboot2", m)
                    .set("modules", std::mem::take(mods))
                    .build(),
            );
        }
        mods.clear();
        *title = None;
    };
    for l in cfg.lines() {
        let t = l.trim();
        if let Some(rest) = t.strip_prefix("menuentry ") {
            flush(&mut title, &mut mb, &mut mods, &mut out);
            let q = rest.chars().next().filter(|c| *c == '\'' || *c == '"');
            title = Some(match q {
                Some(q) => rest[1..].split(q).next().unwrap_or("").to_string(),
                None => rest.split_whitespace().next().unwrap_or("").to_string(),
            });
        } else if let Some(rest) = t
            .strip_prefix("multiboot2")
            .filter(|r| r.starts_with(char::is_whitespace))
        {
            mb = Some(rest.trim().to_string());
        } else if let Some(rest) = t
            .strip_prefix("module2")
            .filter(|r| r.starts_with(char::is_whitespace))
        {
            mods.push(rest.trim().to_string());
        }
    }
    flush(&mut title, &mut mb, &mut mods, &mut out);
    out
}

/// boot.xen: present when /boot holds a Xen hypervisor (xen-VERSION.gz):
/// the versions there (newest last), GRUB_CMDLINE_XEN_DEFAULT and
/// GRUB_CMDLINE_LINUX from /etc/default/grub, whether GRUB's 20_linux_xen
/// is installed, and grub.cfg's Xen entries.
pub fn xen_json(sys: &Sys) -> Option<Value> {
    let mut gz: Vec<String> = sys
        .list("/boot")
        .into_iter()
        .filter(|n| n.starts_with("xen-") && n.ends_with(".gz"))
        .collect();
    if gz.is_empty() {
        return None;
    }
    gz.sort_by(|a, b| crate::sys::natural_cmp(a, b));
    let versions: Vec<String> = gz
        .iter()
        .map(|n| {
            n.trim_start_matches("xen-")
                .trim_end_matches(".gz")
                .to_string()
        })
        .collect();
    let dg = sys.read("/etc/default/grub").unwrap_or_default();
    let cfg = [
        "/boot/grub2/grub.cfg",
        "/boot/efi/EFI/qubes/grub.cfg",
        "/boot/grub/grub.cfg",
    ]
    .iter()
    .find_map(|p| sys.read(p).map(|t| (*p, t)));
    Some(
        Obj::new()
            .set("version", versions.last().cloned())
            .set("versions", versions)
            .set(
                "hypervisors",
                gz.iter().map(|n| format!("/boot/{n}")).collect::<Vec<_>>(),
            )
            .set(
                "grub_d_20_linux_xen",
                sys.is_file("/etc/grub.d/20_linux_xen"),
            )
            .set(
                "cmdline_xen_default",
                default_grub_var(&dg, "GRUB_CMDLINE_XEN_DEFAULT"),
            )
            .set("cmdline_linux", default_grub_var(&dg, "GRUB_CMDLINE_LINUX"))
            .set("grub_cfg", cfg.as_ref().map(|(p, _)| *p))
            .set(
                "entries",
                cfg.map(|(_, t)| xen_entries(&t)).unwrap_or_default(),
            )
            .build(),
    )
}

/// boot with `xen` added, when there is one.
pub fn add_to_boot(boot: Value, xen: Option<Value>) -> Value {
    match (boot, xen) {
        (Value::Obj(mut o), Some(x)) => {
            o.retain(|(k, _)| k != "xen");
            o.push(("xen".into(), x));
            Value::Obj(o)
        }
        (b, _) => b,
    }
}

/// os.product for Qubes: "Qubes OS 4.3".
pub fn product(os_id: Option<&str>, version_id: Option<&str>) -> Option<String> {
    (os_id == Some(OS_ID)).then(|| format!("Qubes OS {}", version_id.unwrap_or("?")))
}

#[cfg(test)]
pub(crate) mod sample {
    /// qubes.xml as Qubes 4.3.2 writes it (shortened to three qubes; its
    /// layout from the Q0/Q1 guest).
    pub const XML: &str = r#"<?xml version='1.0' encoding='utf-8'?>
<qubes version="3.0">
  <properties>
    <property name="default_template">fedora-44-xfce</property>
  </properties>
  <labels><label id="label-1" color="0xcc0000">red</label></labels>
  <pools>
    <pool dir_path="/var/lib/qubes" driver="file" name="varlibqubes" revisions_to_keep="1"/>
    <pool dir_path="/var/lib/qubes/vm-kernels" driver="linux-kernel" name="linux-kernel"/>
    <pool driver="lvm_thin" name="vm-pool" thin_pool="vm-pool" volume_group="qubes_dom0" revisions_to_keep="2"/>
  </pools>
  <domains>
    <domain id="domain-1" class="TemplateVM">
      <properties>
        <property name="label">black</property>
        <property name="name">fedora-44-xfce</property>
      </properties>
      <features><feature name="os">Linux</feature></features>
      <volume-config>
        <volume name="root" pool="vm-pool" vid="qubes_dom0/vm-fedora-44-xfce-root" revisions_to_keep="2" rw="True" save_on_stop="True" size="21474836480" snap_on_start="False"/>
        <volume name="private" pool="vm-pool" vid="qubes_dom0/vm-fedora-44-xfce-private" revisions_to_keep="2" rw="True" save_on_stop="True" size="2147483648" snap_on_start="False"/>
        <volume name="volatile" pool="vm-pool" vid="qubes_dom0/vm-fedora-44-xfce-volatile" rw="True" size="10737418240"/>
        <volume name="kernel" pool="linux-kernel" vid="6.18.51-1.qubes.fc41" rw="False"/>
      </volume-config>
    </domain>
    <domain id="domain-2" class="AppVM">
      <properties>
        <property name="name">work</property>
        <property name="template">fedora-44-xfce</property>
      </properties>
      <volume-config>
        <volume name="root" pool="vm-pool" vid="qubes_dom0/vm-work-root" rw="False" snap_on_start="True" source="qubes_dom0/vm-fedora-44-xfce-root"/>
        <volume name="private" pool="vm-pool" vid="qubes_dom0/vm-work-private" revisions_to_keep="2" rw="True" save_on_stop="True" size="2147483648"/>
        <volume name="volatile" pool="vm-pool" vid="qubes_dom0/vm-work-volatile" rw="True" size="10737418240"/>
      </volume-config>
    </domain>
    <domain id="domain-0" class="AdminVM">
      <properties><property name="name">dom0</property></properties>
    </domain>
  </domains>
</qubes>
"#;

    /// Qubes 4.3's grub.cfg Xen entry (the Q0 guest's, shortened).
    pub const GRUB_CFG: &str = r#"### BEGIN /etc/grub.d/20_linux_xen ###
menuentry 'Qubes, with Xen hypervisor' --class qubes --class gnu-linux --class gnu --class os --class xen $menuentry_id_option 'xen-gnulinux-simple-ab' {
	insmod part_gpt
	echo	'Loading Xen 4.19.5 ...'
	multiboot2	/xen-4.19.5.gz placeholder console=com1 com1=115200,8n1 dom0_mem=min:1024M dom0_mem=max:4096M ucode=scan smt=off gnttab_max_frames=2048 gnttab_max_maptrack_frames=4096 ${xen_rm_opts}
	echo	'Loading Linux 6.18.51-1.qubes.fc41.x86_64 ...'
	module2	/vmlinuz-6.18.51-1.qubes.fc41.x86_64 placeholder root=/dev/mapper/qubes_dom0-root ro rd.luks.uuid=luks-bc000000-0000-4000-8000-000000000003 rd.lvm.lv=qubes_dom0/root rd.lvm.lv=qubes_dom0/swap console=hvc0 initcall_blacklist=print_s5_reset_status_mmio
	echo	'Loading initial ramdisk ...'
	module2 --nounzip   /initramfs-6.18.51-1.qubes.fc41.x86_64.img
}
### END /etc/grub.d/20_linux_xen ###
"#;

    pub const DEFAULT_GRUB: &str = r#"GRUB_TIMEOUT=5
GRUB_DISTRIBUTOR="$(sed 's, release .*$,,g' /etc/system-release)"
GRUB_CMDLINE_LINUX="rd.luks.uuid=luks-bc000000-0000-4000-8000-000000000003 rd.lvm.lv=qubes_dom0/root rd.lvm.lv=qubes_dom0/swap plymouth.ignore-serial-consoles console=hvc0 initcall_blacklist=print_s5_reset_status_mmio"
GRUB_CMDLINE_XEN_DEFAULT="console=none dom0_mem=min:1024M dom0_mem=max:4096M ucode=scan smt=off gnttab_max_frames=2048 gnttab_max_maptrack_frames=4096"
GRUB_CMDLINE_XEN_DEFAULT="console=com1 com1=115200,8n1 dom0_mem=min:1024M dom0_mem=max:4096M ucode=scan smt=off gnttab_max_frames=2048 gnttab_max_maptrack_frames=4096"
GRUB_TERMINAL="serial console"
"#;
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn qubes_xml() {
        let q = parse_xml(sample::XML);
        let pools = q.get("pools").and_then(Value::as_array).unwrap();
        assert_eq!(pools.len(), 3);
        assert_eq!(s(&pools[2], "volume_group").as_deref(), Some("qubes_dom0"));
        assert_eq!(s(&pools[2], "thin_pool").as_deref(), Some("vm-pool"));
        assert_eq!(s(&pools[2], "driver").as_deref(), Some("lvm_thin"));
        let vols = q.get("volumes").and_then(Value::as_array).unwrap();
        assert_eq!(vols.len(), 7);
        assert_eq!(s(&vols[0], "qube").as_deref(), Some("fedora-44-xfce"));
        assert_eq!(s(&vols[0], "class").as_deref(), Some("TemplateVM"));
        assert_eq!(
            vols[0].get("size_bytes").and_then(Value::as_u64),
            Some(20 << 30)
        );
        assert_eq!(
            vols[0].get("save_on_stop").and_then(Value::as_bool),
            Some(true)
        );
        assert_eq!(s(&vols[4], "qube").as_deref(), Some("work"));
        assert_eq!(
            vols[4].get("snap_on_start").and_then(Value::as_bool),
            Some(true)
        );
        // The label property and the global default_template aren't names.
        assert!(
            vols.iter()
                .all(|v| s(v, "qube").as_deref() != Some("black"))
        );
        // By LV name: the volume, its revisions, its -snap.
        assert_eq!(
            volume_of(&q, "qubes_dom0", "vm-work-private")
                .and_then(|v| s(v, "volume"))
                .as_deref(),
            Some("private")
        );
        assert!(volume_of(&q, "qubes_dom0", "vm-work-private-1794670270-back").is_some());
        assert!(volume_of(&q, "qubes_dom0", "vm-work-private-snap").is_some());
        assert!(volume_of(&q, "qubes_dom0", "vm-work-private-x-back").is_none());
        assert!(volume_of(&q, "other", "vm-work-private").is_none());
        assert!(volume_of(&q, "qubes_dom0", "root").is_none());
        // Escapes and comments.
        let q = parse_xml(
            "<!-- <domain> --><domains><domain class=\"AppVM\"><properties><property name=\"name\">a&amp;b</property></properties><volume-config><volume name='private' vid='vg/vm-a-private'/></volume-config></domain></domains>",
        );
        assert_eq!(
            q.at(&["volumes", "0", "qube"]).and_then(Value::as_str),
            Some("a&b")
        );
    }

    #[test]
    fn xen_boot() {
        let e = xen_entries(sample::GRUB_CFG);
        assert_eq!(e.len(), 1);
        assert_eq!(
            s(&e[0], "title").as_deref(),
            Some("Qubes, with Xen hypervisor")
        );
        assert!(
            s(&e[0], "multiboot2")
                .unwrap()
                .starts_with("/xen-4.19.5.gz placeholder console=com1")
        );
        let m = e[0].get("modules").and_then(Value::as_array).unwrap();
        assert_eq!(m.len(), 2);
        assert!(
            m[0].as_str()
                .unwrap()
                .contains("rd.luks.uuid=luks-bc000000-0000-4000-8000-000000000003")
        );
        assert_eq!(
            m[1].as_str(),
            Some("--nounzip   /initramfs-6.18.51-1.qubes.fc41.x86_64.img")
        );
        // The last assignment wins (the guest's %post replaces console=none).
        assert!(
            default_grub_var(sample::DEFAULT_GRUB, "GRUB_CMDLINE_XEN_DEFAULT")
                .unwrap()
                .starts_with("console=com1 ")
        );
        assert!(
            default_grub_var(sample::DEFAULT_GRUB, "GRUB_CMDLINE_LINUX")
                .unwrap()
                .contains("rd.lvm.lv=qubes_dom0/root")
        );
        assert_eq!(default_grub_var("", "X"), None);
        assert_eq!(
            product(Some("qubes"), Some("4.3")).as_deref(),
            Some("Qubes OS 4.3")
        );
        assert_eq!(product(Some("fedora"), Some("44")), None);
    }

    #[test]
    fn activated_until_dropped() {
        use crate::sys::FakeRunner;
        let lvs = "lvs --noheadings --separator | -o vg_name,lv_name,lv_attr";
        let on = "lvchange --activate y --ignoreactivationskip --config activation { read_only_volume_list = [ \"vg/lv\" ] } vg/lv";
        let failed =
            vec!["vg/lv: activated read-only for the dump, and `lvchange -an` failed after it"];
        let t = crate::testutil::Tree::new("qubes-activated");
        // Skipped and inactive (k at 9, no a at 4): activated, and
        // deactivated when the Activated goes.
        let r = || {
            FakeRunner::default()
                .with(
                    lvs,
                    "  vg|lv|Vwi---tz-k\n  vg|on|Vwi-a-tz-k\n  vg|plain|Vwi---tz--\n",
                )
                .with(on, "")
        };
        let s = Sys::new(
            t.root(),
            Box::new(r().with("lvchange --activate n vg/lv", "")),
            true,
        );
        drop(activate_skipped(&s));
        assert!(s.warnings().is_empty(), "{:?}", s.warnings());
        // The -an fails: said at the drop, not before.
        let s = Sys::new(t.root(), Box::new(r()), true);
        let a = activate_skipped(&s);
        assert_eq!(a.lvs, vec!["vg/lv"]);
        assert!(s.warnings().is_empty(), "{:?}", s.warnings());
        drop(a);
        assert_eq!(s.warnings(), failed);
        // A panic while it is held: the unwind deactivates too.
        let s = Sys::new(t.root(), Box::new(r()), true);
        let got = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
            let _a = activate_skipped(&s);
            panic!("in the dump");
        }));
        assert!(got.is_err());
        assert_eq!(s.warnings(), failed);
        // Not root: nothing activated, nothing to let go.
        let s = Sys::new(t.root(), Box::new(r()), false);
        drop(activate_skipped(&s));
        assert!(s.warnings().is_empty(), "{:?}", s.warnings());
    }

    #[test]
    fn dmsetup_status() {
        assert_eq!(
            parse_pool_status(
                "0 72302592 thin-pool 3 228/9216 3800/564800 - rw discard_passdown queue_if_no_space - 1024"
            ),
            Some(((228, 9216), (3800, 564800)))
        );
        assert_eq!(
            parse_thin_status("0 41943040 thin 2097152 41943039"),
            Some(2097152)
        );
        assert_eq!(parse_thin_status("0 41943040 thin Fail"), None);
        assert_eq!(parse_pool_status("0 8 linear"), None);
    }
}
