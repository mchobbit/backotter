//! `bko-holt load --reformat MOUNT=TYPE` (docs/design/reformat.md): restore
//! onto another filesystem type than the source's.
//!
//! `work_out` reads the source's layout and the `--reformat`s and says what
//! changes: for each filesystem, its new type, features, label and fstab
//! entry, the mounts of it that become directories (a btrfs filesystem's
//! subvolumes), and every reason it can't be done.  `apply` makes the edited
//! layout every stage then works from, so `format` makes the new type and
//! `mount` mounts it once.  The restored system's files that name the old
//! filesystem are rewritten in the boot stage, by `bko-holt rewrite`
//! (rewrite.rs), which works the same change out again from the layout file.

use crate::json::{Obj, Value};

/// The types --reformat converts between.
pub const TYPES: [&str; 3] = ["ext4", "xfs", "btrfs"];

/// mkfs.xfs's kernel profiles (/usr/share/xfsprogs/mkfs/lts_X.Y.conf): the
/// oldest kernel each is for, and the features it turns on.  ftype and
/// lazysbcount are on in all of them (mkfs.xfs's defaults since 4.x).
const XFS_PROFILES: [((u32, u32), &[&str]); 7] = [
    ((4, 19), &["crc", "finobt", "sparse", "ftype", "lazysbcount"]),
    ((5, 4), &["crc", "finobt", "reflink", "sparse", "ftype", "lazysbcount"]),
    ((5, 10), &["crc", "finobt", "reflink", "sparse", "ftype", "lazysbcount"]),
    ((5, 15), &["crc", "finobt", "reflink", "inobtcount", "bigtime", "sparse", "ftype", "lazysbcount"]),
    ((6, 1), &["crc", "finobt", "reflink", "inobtcount", "bigtime", "sparse", "ftype", "lazysbcount"]),
    ((6, 6), &["crc", "finobt", "reflink", "rmapbt", "inobtcount", "bigtime", "sparse", "nrext64", "ftype", "lazysbcount"]),
    ((6, 12), &["crc", "finobt", "reflink", "rmapbt", "inobtcount", "bigtime", "sparse", "nrext64", "ftype", "lazysbcount"]),
];

/// ext4's features as e2fsprogs 1.43 to 1.46 make them by default.
const EXT4_BASE: [&str; 14] = [
    "has_journal", "ext_attr", "resize_inode", "dir_index", "filetype", "extent", "64bit", "flex_bg", "sparse_super", "large_file", "huge_file", "dir_nlink", "extra_isize", "metadata_csum",
];

/// Mount options any filesystem takes; every other one is the old type's.
const GENERIC_OPTIONS: [&str; 20] = [
    "defaults", "ro", "rw", "noatime", "relatime", "strictatime", "lazytime", "nolazytime", "nodiratime", "diratime", "nodev", "dev", "nosuid", "suid", "noexec", "exec",
    "nofail", "noauto", "auto", "sync",
];

#[derive(Debug, Clone, PartialEq)]
pub struct Change {
    /// The mount point it was named by, which stays a mount.
    pub mount: String,
    /// Its source device (nvme0n1p2).
    pub src: String,
    pub uuid: String,
    pub from: String,
    pub to: String,
    /// The label, cut to the new type's limit.
    pub label: Option<String>,
    /// Its other mount points, each now a directory of `mount`.
    pub folded: Vec<String>,
    /// btrfs subvolumes that weren't mounted, nor inside a mounted one.
    pub unmounted: Vec<String>,
    /// btrfs subvolumes inside a mounted one: plain directories now.
    pub nested: Vec<String>,
    pub features: Vec<String>,
    /// Where the features came from, for the plan.
    pub features_from: String,
    /// The kept fstab entry's options, and those dropped from it.
    pub options: String,
    pub dropped: Vec<String>,
    pub passno: u64,
}

#[derive(Debug, Clone, PartialEq, Default)]
pub struct Reformat {
    pub changes: Vec<Change>,
    pub problems: Vec<String>,
    pub notes: Vec<String>,
}

fn s(v: &Value, k: &str) -> Option<String> {
    v.get(k).and_then(Value::as_str).map(str::to_string)
}

fn arr<'a>(v: &'a Value, path: &[&str]) -> &'a [Value] {
    v.at(path).and_then(Value::as_array).map(Vec::as_slice).unwrap_or(&[])
}

/// A device's entry (disk, partition, md array, dm device) by its source name.
fn device<'a>(v: &'a Value, name: &str) -> Option<&'a Value> {
    for sect in ["disks", "md_arrays", "dm_devices"] {
        for d in arr(v, &["storage", sect]) {
            if s(d, "name").as_deref() == Some(name) {
                return Some(d);
            }
            for p in d.get("partitions").and_then(Value::as_array).into_iter().flatten() {
                if s(p, "name").as_deref() == Some(name) {
                    return Some(p);
                }
            }
        }
    }
    None
}

/// "6.18.60-1-lts" -> (6, 18).
pub fn kernel_version(v: &str) -> Option<(u32, u32)> {
    let mut it = v.split(|c: char| !c.is_ascii_digit());
    Some((it.next()?.parse().ok()?, it.next()?.parse().ok()?))
}

/// "1.47.4-1" -> (1, 47).
fn package_version(v: &str) -> Option<(u32, u32)> {
    let v = v.split_once(':').map(|(_, r)| r).unwrap_or(v);
    kernel_version(v)
}

/// The oldest kernel the source has: those installed, and the one it ran
/// (os.kernel_release), which is all an Arch dump has (vmlinuz-linux names
/// no version).
fn oldest_kernel(v: &Value) -> Option<(u32, u32)> {
    let mut all: Vec<String> = arr(v, &["boot", "kernels", "installed"]).iter().filter_map(|k| s(k, "version")).collect();
    all.extend(v.at(&["os", "kernel_release"]).and_then(Value::as_str).map(str::to_string));
    all.iter().filter_map(|k| kernel_version(k)).min()
}

fn under(path: &str, top: &str) -> bool {
    top == "/" || path == top || path.starts_with(&format!("{top}/"))
}

/// The options of a reformatted filesystem's fstab entry: the generic ones
/// kept, the rest dropped (and returned).
pub fn generic_options(o: &str) -> (String, Vec<String>) {
    let (mut keep, mut drop) = (Vec::new(), Vec::new());
    for x in o.split(',').filter(|x| !x.is_empty()) {
        if GENERIC_OPTIONS.contains(&x) || x.starts_with("x-") || x.starts_with("comment=") {
            keep.push(x.to_string());
        } else {
            drop.push(x.to_string());
        }
    }
    (if keep.is_empty() { "defaults".to_string() } else { keep.join(",") }, drop)
}

/// A btrfs mount's subvolume path from its options (subvol=/@ -> "@"), or
/// by subvolid= from the filesystem's list; "" for the top level.
fn mounted_subvolume(options: &str, fs: &Value) -> String {
    for o in options.split(',') {
        if let Some(p) = o.strip_prefix("subvol=") {
            return p.trim_matches('/').to_string();
        }
        if let Some(id) = o.strip_prefix("subvolid=").and_then(|i| i.parse::<u64>().ok()) {
            if id == 5 {
                return String::new();
            }
            if let Some(p) = arr(fs, &["subvolumes"]).iter().find(|x| x.get("id").and_then(Value::as_u64) == Some(id)).and_then(|x| s(x, "path")) {
                return p.trim_start_matches("<FS_TREE>/").trim_matches('/').to_string();
            }
        }
    }
    // No option: the default subvolume.
    let def = fs.at(&["default_subvolume", "id"]).and_then(Value::as_u64).unwrap_or(5);
    if def == 5 {
        return String::new();
    }
    arr(fs, &["subvolumes"]).iter().find(|x| x.get("id").and_then(Value::as_u64) == Some(def)).and_then(|x| s(x, "path")).map(|p| p.trim_start_matches("<FS_TREE>/").trim_matches('/').to_string()).unwrap_or_default()
}

fn cut_label(label: &str, max: usize) -> String {
    let mut out = String::new();
    for c in label.chars() {
        if out.len() + c.len_utf8() > max {
            break;
        }
        out.push(c);
    }
    out
}

/// Whether the source's boot loader reads the filesystem on SRC: GRUB, with
/// /boot on it (or no /boot of its own, and it holds /).
fn loader_reads(v: &Value, src: &str) -> bool {
    if v.at(&["boot", "loader", "name"]).and_then(Value::as_str) != Some("grub") {
        return false;
    }
    let fstab = arr(v, &["storage", "fstab"]);
    let on = |file: &str| fstab.iter().find(|e| s(e, "file").as_deref() == Some(file)).and_then(|e| s(e, "resolves_to"));
    match on("/boot") {
        Some(b) => b == src,
        None => on("/").as_deref() == Some(src),
    }
}

/// What --reformat changes, worked out from the source's layout.
pub fn work_out(v: &Value, reformats: &[(String, String)]) -> Reformat {
    let mut r = Reformat::default();
    if reformats.is_empty() {
        return r;
    }
    if v.get("load_reformat").is_some() {
        r.problems.push("this layout was edited by --reformat already; give the dump itself".into());
        return r;
    }
    if crate::other::Family::of_layout(v).is_some() {
        r.problems.push("--reformat is for Linux sources only".into());
        return r;
    }
    let fstab = arr(v, &["storage", "fstab"]);
    let packages = v.at(&["boot", "packages"]);
    let kernel = oldest_kernel(v);
    for (mount, to) in reformats {
        let what = format!("--reformat {mount}={to}");
        let Some(entry) = fstab.iter().find(|e| s(e, "file").as_deref() == Some(mount.as_str()) && s(e, "type").as_deref() != Some("swap")) else {
            r.problems.push(format!("{what}: no fstab entry mounts {mount}"));
            continue;
        };
        let Some(src) = s(entry, "resolves_to") else {
            r.problems.push(format!("{what}: fstab's {mount} isn't on the layout's disks"));
            continue;
        };
        let Some(content) = device(v, &src).and_then(|d| d.get("content")).filter(|c| !c.is_null()) else {
            r.problems.push(format!("{what}: {src} has no filesystem in the layout"));
            continue;
        };
        let from = s(content, "type").unwrap_or_default();
        if !TYPES.contains(&from.as_str()) {
            r.problems.push(format!("{what}: {mount} is {from} on {src}; --reformat converts ext4, XFS and btrfs"));
            continue;
        }
        if from == *to {
            r.problems.push(format!("{what}: {mount} is {from} already"));
            continue;
        }
        let Some(uuid) = s(content, "uuid") else {
            r.problems.push(format!("{what}: {src}'s filesystem has no UUID in the layout"));
            continue;
        };
        if r.changes.iter().any(|c| c.src == src) {
            r.problems.push(format!("{what}: {src} is reformatted twice"));
            continue;
        }
        let fs = arr(v, &["storage", "btrfs"]).iter().find(|f| s(f, "uuid").as_deref() == Some(uuid.as_str())).cloned().unwrap_or(Value::Null);
        if from == "btrfs" && arr(&fs, &["devices"]).len() > 1 {
            r.problems.push(format!("{what}: btrfs {uuid} is on {} devices; --reformat takes a btrfs on one", arr(&fs, &["devices"]).len()));
            continue;
        }
        // Every other mount of the same filesystem becomes a directory of
        // this one, so each must be under it.
        let mut folded = Vec::new();
        let mut mounted_subs = vec![mounted_subvolume(&s(entry, "options").unwrap_or_default(), &fs)];
        let mut bad = false;
        for e in fstab.iter().filter(|e| s(e, "resolves_to").as_deref() == Some(src.as_str()) && s(e, "type").as_deref() != Some("swap")) {
            let file = s(e, "file").unwrap_or_default();
            if file == *mount {
                continue;
            }
            if !under(&file, mount) {
                r.problems.push(format!("{what}: the same filesystem is mounted at {file} too, which isn't under {mount}; name the mount point that holds the others"));
                bad = true;
                continue;
            }
            mounted_subs.push(mounted_subvolume(&s(e, "options").unwrap_or_default(), &fs));
            folded.push(file);
        }
        if bad {
            continue;
        }
        let (mut unmounted, mut nested) = (Vec::new(), Vec::new());
        for sv in arr(&fs, &["subvolumes"]).iter().filter_map(|x| s(x, "path")) {
            let p = sv.trim_start_matches("<FS_TREE>/").trim_matches('/').to_string();
            if mounted_subs.contains(&p) {
                continue;
            }
            if mounted_subs.iter().any(|m| m.is_empty() || p.starts_with(&format!("{m}/"))) {
                nested.push(p);
            } else {
                unmounted.push(p);
            }
        }
        let reads = loader_reads(v, &src);
        if reads && to != "ext4" {
            r.problems.push(format!("{what}: GRUB reads /boot from it; under GRUB, --reformat makes ext4 only"));
            continue;
        }
        // The new type's tools on the restored system (its fsck, repair).
        let tool = match to.as_str() {
            "xfs" => "xfsprogs",
            "btrfs" => "btrfs-progs",
            _ => "e2fsprogs",
        };
        let tool_version = packages.and_then(|p| p.get(tool)).and_then(Value::as_str).and_then(package_version);
        match packages {
            Some(p) if p.get(tool).is_none_or(Value::is_null) => {
                r.problems.push(format!("{what}: the source doesn't have {tool} installed; install it there and back up again, or the restored system has no fsck or repair for {to}"));
                continue;
            }
            None => r.notes.push(format!("{what}: the layout doesn't list the source's packages, so whether it has {tool} isn't known")),
            _ => {}
        }
        let Some(kv) = kernel else {
            r.problems.push(format!("{what}: the layout lists no installed kernel, so the {to} features it can mount aren't known"));
            continue;
        };
        let (features, features_from) = match to.as_str() {
            "xfs" => match XFS_PROFILES.iter().rev().find(|(k, _)| *k <= kv) {
                Some(((a, b), f)) => (f.iter().map(|x| x.to_string()).collect(), format!("mkfs.xfs's lts_{a}.{b} profile (the oldest installed kernel is {}.{})", kv.0, kv.1)),
                None => {
                    r.problems.push(format!("{what}: the oldest installed kernel, {}.{}, is older than mkfs.xfs's oldest profile (4.19)", kv.0, kv.1));
                    continue;
                }
            },
            "ext4" => {
                let mut f: Vec<String> = EXT4_BASE.iter().map(|x| x.to_string()).collect();
                let new = tool_version.is_some_and(|t| t >= (1, 47)) && kv >= (5, 15);
                if new {
                    f.push("orphan_file".into());
                    // GRUB before 2.12 refuses metadata_csum_seed.
                    if !reads {
                        f.push("metadata_csum_seed".into());
                    }
                }
                let by = match tool_version {
                    Some((a, b)) => format!("e2fsprogs {a}.{b}'s defaults, for a {}.{} kernel{}", kv.0, kv.1, if reads { ", that GRUB reads" } else { "" }),
                    None => format!("e2fsprogs 1.43's defaults (its version isn't in the layout), for a {}.{} kernel", kv.0, kv.1),
                };
                (f, by)
            }
            _ => {
                let mut f = vec!["extended_iref".to_string(), "skinny_metadata".into(), "no_holes".into()];
                if kv >= (4, 5) {
                    f.push("free_space_tree".into());
                }
                (f, format!("mkfs.btrfs's defaults, for a {}.{} kernel", kv.0, kv.1))
            }
        };
        let (options, dropped) = generic_options(&s(entry, "options").unwrap_or_default());
        let passno = if to == "ext4" { if mount == "/" { 1 } else { 2 } } else { 0 };
        let label = s(content, "label").filter(|l| !l.is_empty()).map(|l| {
            let max = match to.as_str() {
                "xfs" => 12,
                "ext4" => 16,
                _ => 255,
            };
            let cut = cut_label(&l, max);
            if cut != l {
                r.notes.push(format!("{what}: the label {l:?} is cut to {cut:?}, {to}'s limit"));
            }
            cut
        });
        // Hibernation into a swap file on it: the file moves, resume_offset with it.
        let resume_offset = v.at(&["boot", "cmdline", "storage", "resume_offset"]).is_some_and(|x| !x.is_null());
        for sw in arr(v, &["storage", "swaps"]) {
            let Some(file) = s(sw, "file") else { continue };
            if s(sw, "type").as_deref() == Some("file") && under(&file, mount) {
                if resume_offset {
                    r.problems.push(format!("{what}: the system hibernates into the swap file {file} on it, and resume_offset= would no longer match"));
                    bad = true;
                } else {
                    r.notes.push(format!("{what}: the swap file {file} is on it"));
                }
            }
        }
        if bad {
            continue;
        }
        r.changes.push(Change {
            mount: mount.clone(),
            src,
            uuid,
            from,
            to: to.clone(),
            label,
            folded,
            unmounted,
            nested,
            features,
            features_from,
            options,
            dropped,
            passno,
        });
    }
    for c in &r.changes {
        let mut n = vec![format!(
            "reformat {}: {} {} -> {} (same UUID{}); its features: {}",
            c.src,
            c.from,
            c.uuid,
            c.to,
            c.label.as_ref().map(|l| format!(", label {l:?}")).unwrap_or_default(),
            c.features_from
        )];
        n.push(format!(
            "fstab's {}: {} with {} (pass {}){}",
            c.mount,
            c.to,
            c.options,
            c.passno,
            if c.dropped.is_empty() { String::new() } else { format!("; {} dropped", c.dropped.join(",")) }
        ));
        for f in &c.folded {
            n.push(format!("fstab's {f}: a directory of {} now (--reformat {}={})", c.mount, c.mount, c.to));
        }
        if !c.nested.is_empty() {
            n.push(format!("reformat {}: subvolumes {} become plain directories", c.src, c.nested.join(", ")));
        }
        for u in &c.unmounted {
            n.push(format!("reformat {}: subvolume {u} isn't mounted; it isn't recreated", c.src));
        }
        if c.from == "btrfs" {
            n.push(format!("reformat {}: chattr C and c and btrfs.* xattrs don't come along ({} refuses them; rustic-bko warns in load.log)", c.src, c.to));
        }
        if c.mount == "/" {
            n.push("reformat: boot rewrites fstab and the kernel command lines (rootflags=, rootfstype=), then checks the initramfs has the new type's module".into());
        } else {
            n.push(format!("reformat: boot rewrites fstab for {}", c.mount));
        }
        r.notes.extend(n);
    }
    r
}

fn obj(v: &mut Value) -> Option<&mut Vec<(String, Value)>> {
    match v {
        Value::Obj(o) => Some(o),
        _ => None,
    }
}

fn get_mut<'a>(v: &'a mut Value, k: &str) -> Option<&'a mut Value> {
    obj(v)?.iter_mut().find(|(n, _)| n == k).map(|(_, x)| x)
}

fn put(v: &mut Value, k: &str, x: Value) {
    if let Some(o) = obj(v) {
        match o.iter_mut().find(|(n, _)| n == k) {
            Some(slot) => slot.1 = x,
            None => o.push((k.to_string(), x)),
        }
    }
}

fn storage_list<'a>(v: &'a mut Value, k: &str) -> Option<&'a mut Vec<Value>> {
    match get_mut(get_mut(v, "storage")?, k)? {
        Value::Arr(a) => Some(a),
        _ => None,
    }
}

/// The new content of a reformatted device.
fn content(c: &Change) -> Value {
    Obj::new()
        .set("type", c.to.as_str())
        .set("usage", "filesystem")
        .set("uuid", c.uuid.as_str())
        .set("label", c.label.clone())
        .set("features", c.features.iter().map(String::as_str).collect::<Vec<_>>())
        .set("reformatted_from", c.from.as_str())
        .build()
}

/// The layout every stage works from: each change's device holds its new
/// filesystem, its btrfs entry is gone, its fstab entry is rewritten and its
/// folded mounts are out.  R's problems and notes go in as
/// load_reformat.{problems,notes}, for the plan.
pub fn apply(mut v: Value, r: &Reformat) -> Value {
    for c in &r.changes {
        for sect in ["disks", "md_arrays", "dm_devices"] {
            for d in storage_list(&mut v, sect).into_iter().flatten() {
                if d.get("name").and_then(Value::as_str) == Some(c.src.as_str()) {
                    put(d, "content", content(c));
                }
                if let Some(Value::Arr(parts)) = get_mut(d, "partitions") {
                    for p in parts {
                        if p.get("name").and_then(Value::as_str) == Some(c.src.as_str()) {
                            put(p, "content", content(c));
                        }
                    }
                }
            }
        }
        if let Some(list) = storage_list(&mut v, "btrfs") {
            list.retain(|f| f.get("uuid").and_then(Value::as_str) != Some(c.uuid.as_str()));
        }
        if let Some(list) = storage_list(&mut v, "fstab") {
            list.retain(|e| !(e.get("resolves_to").and_then(Value::as_str) == Some(c.src.as_str()) && e.get("file").and_then(Value::as_str).is_some_and(|f| c.folded.iter().any(|x| x == f))));
            for e in list.iter_mut() {
                if e.get("resolves_to").and_then(Value::as_str) == Some(c.src.as_str()) && e.get("file").and_then(Value::as_str) == Some(c.mount.as_str()) {
                    put(e, "type", Value::from(c.to.as_str()));
                    put(e, "options", Value::from(c.options.as_str()));
                    put(e, "passno", Value::Int(c.passno as i64));
                }
            }
        }
    }
    put(&mut v, "load_reformat", Obj::new().set("problems", r.problems.clone()).set("notes", r.notes.clone()).build());
    v
}

/// LAYOUT as the stages work from it with OPTS's --reformats: edited once
/// (load::run does it; `load_reformat` marks it done), else as it is.
pub fn edited(layout: &crate::load::layout::Layout, reformats: &[(String, String)]) -> Option<crate::load::layout::Layout> {
    if reformats.is_empty() || layout.v.get("load_reformat").is_some() {
        return None;
    }
    crate::load::layout::Layout::from_json(apply(layout.v.clone(), &work_out(&layout.v, reformats))).ok()
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::load::layout::sample;

    fn eos() -> Value {
        crate::json::parse(
            r#"{"format": "bko-holt", "schema": 1, "os": {"id": "endeavouros"},
  "storage": {
    "disks": [{"name": "nvme0n1", "partitions": [
      {"name": "nvme0n1p1", "number": 1, "content": {"type": "vfat", "uuid": "B9BB-8C26"}},
      {"name": "nvme0n1p2", "number": 2, "content": {"type": "btrfs", "uuid": "1b64a017-659b-4484-bf36-b97f639c3c35", "label": "endeavouros"}}]}],
    "btrfs": [{"uuid": "1b64a017-659b-4484-bf36-b97f639c3c35", "devices": ["nvme0n1p2"], "default_subvolume": {"id": 5},
               "subvolumes": [{"id": 256, "path": "<FS_TREE>/@"}, {"id": 257, "path": "<FS_TREE>/@home"}, {"id": 258, "path": "<FS_TREE>/@cache"},
                              {"id": 259, "path": "<FS_TREE>/@log"}, {"id": 260, "path": "@/var/lib/portables"}, {"id": 261, "path": "<FS_TREE>/@snapshots"}]}],
    "fstab": [
      {"spec": "UUID=B9BB-8C26", "file": "/efi", "type": "vfat", "options": "fmask=0137,dmask=0027", "passno": 2, "resolves_to": "nvme0n1p1"},
      {"spec": "UUID=1b64a017-659b-4484-bf36-b97f639c3c35", "file": "/", "type": "btrfs", "options": "subvol=/@,noatime,compress=zstd", "passno": 0, "resolves_to": "nvme0n1p2"},
      {"spec": "UUID=1b64a017-659b-4484-bf36-b97f639c3c35", "file": "/home", "type": "btrfs", "options": "subvol=/@home,noatime,compress=zstd", "passno": 0, "resolves_to": "nvme0n1p2"},
      {"spec": "UUID=1b64a017-659b-4484-bf36-b97f639c3c35", "file": "/var/cache", "type": "btrfs", "options": "subvol=/@cache,noatime,compress=zstd", "passno": 0, "resolves_to": "nvme0n1p2"},
      {"spec": "UUID=1b64a017-659b-4484-bf36-b97f639c3c35", "file": "/var/log", "type": "btrfs", "options": "subvol=/@log,noatime,compress=zstd", "passno": 0, "resolves_to": "nvme0n1p2"}
    ]},
  "boot": {"loader": {"name": "systemd-boot", "firmware": "uefi"},
           "kernels": {"installed": [{"version": "7.2.14-arch1-1"}, {"version": "6.18.60-1-lts"}]},
           "packages": {"xfsprogs": "7.2.1-1", "e2fsprogs": "1.47.4-1", "btrfs-progs": "7.3-1"}}}"#,
        )
        .unwrap()
    }

    fn re(a: &[(&str, &str)]) -> Vec<(String, String)> {
        a.iter().map(|(m, t)| (m.to_string(), t.to_string())).collect()
    }

    #[test]
    fn endeavouros_btrfs_onto_xfs() {
        let r = work_out(&eos(), &re(&[("/", "xfs")]));
        assert!(r.problems.is_empty(), "{:?}", r.problems);
        let c = &r.changes[0];
        assert_eq!((c.src.as_str(), c.from.as_str(), c.to.as_str()), ("nvme0n1p2", "btrfs", "xfs"));
        assert_eq!(c.folded, ["/home", "/var/cache", "/var/log"]);
        assert_eq!(c.nested, ["@/var/lib/portables"]);
        assert_eq!(c.unmounted, ["@snapshots"]);
        assert_eq!(c.options, "noatime");
        assert_eq!(c.dropped, ["subvol=/@", "compress=zstd"]);
        assert_eq!(c.passno, 0);
        assert_eq!(c.label.as_deref(), Some("endeavouros"));
        assert!(c.features_from.contains("lts_6.12"), "{}", c.features_from);
        assert!(c.features.contains(&"rmapbt".to_string()) && c.features.contains(&"nrext64".to_string()));
        assert!(r.notes.iter().any(|n| n.starts_with("fstab's /home: a directory of / now")), "{:?}", r.notes);

        let v = apply(eos(), &r);
        let fstab = v.at(&["storage", "fstab"]).and_then(Value::as_array).unwrap();
        assert_eq!(fstab.len(), 2);
        assert_eq!(fstab[1].get("type").and_then(Value::as_str), Some("xfs"));
        assert_eq!(fstab[1].get("options").and_then(Value::as_str), Some("noatime"));
        assert_eq!(v.at(&["storage", "btrfs"]).and_then(Value::as_array).map(Vec::len), Some(0));
        let c2 = v.at(&["storage", "disks", "0", "partitions", "1", "content"]).unwrap();
        assert_eq!(c2.get("type").and_then(Value::as_str), Some("xfs"));
        assert_eq!(c2.get("uuid").and_then(Value::as_str), Some("1b64a017-659b-4484-bf36-b97f639c3c35"));
        assert_eq!(v.at(&["load_reformat", "problems"]).and_then(Value::as_array).map(Vec::len), Some(0));
    }

    #[test]
    fn xfs_profile_follows_the_oldest_kernel() {
        let old = crate::json::parse(&eos().compact().replace("6.18.60-1-lts", "6.1.115-1-lts")).unwrap();
        let c = &work_out(&old, &re(&[("/", "xfs")])).changes[0];
        assert!(c.features_from.contains("lts_6.1 "), "{}", c.features_from);
        assert!(!c.features.contains(&"rmapbt".to_string()));
        let ancient = crate::json::parse(&eos().compact().replace("6.18.60-1-lts", "4.14.1")).unwrap();
        let r = work_out(&ancient, &re(&[("/", "xfs")]));
        assert!(r.problems[0].contains("older than mkfs.xfs's oldest profile"), "{:?}", r.problems);
    }

    #[test]
    fn ext4_features_and_label() {
        let r = work_out(&crate::json::parse(&eos().compact().replace("\"endeavouros\"}", "\"a-long-label-here\"}")).unwrap(), &re(&[("/", "ext4")]));
        let c = &r.changes[0];
        assert_eq!(c.passno, 1);
        assert!(c.features.contains(&"orphan_file".to_string()) && c.features.contains(&"metadata_csum_seed".to_string()));
        assert_eq!(c.label.as_deref(), Some("a-long-label-her"));
        assert!(r.notes.iter().any(|n| n.contains("is cut to")));
    }

    #[test]
    fn refusals() {
        let v = eos();
        let p = |a: &[(&str, &str)]| work_out(&v, &re(a)).problems;
        assert!(p(&[("/srv", "xfs")])[0].contains("no fstab entry mounts /srv"));
        assert!(p(&[("/efi", "xfs")])[0].contains("is vfat"));
        assert!(p(&[("/", "btrfs")])[0].contains("is btrfs already"));
        assert!(p(&[("/home", "xfs")])[0].contains("mounted at / too, which isn't under /home"));
        let no_tools = crate::json::parse(&v.compact().replace("\"xfsprogs\":\"7.2.1-1\",", "")).unwrap();
        assert!(work_out(&no_tools, &re(&[("/", "xfs")])).problems[0].contains("doesn't have xfsprogs installed"));
        // GRUB reads Debian 13's / (no /boot of its own here would) -- the
        // sample has /boot on ext4 apart, so / is free; /boot isn't.
        let deb = crate::json::parse(&sample::bios_mbr()).unwrap();
        assert!(work_out(&deb, &re(&[("/boot", "xfs")])).problems[0].contains("GRUB reads /boot"));
        let r = work_out(&deb, &re(&[("/boot", "btrfs")]));
        assert!(r.problems[0].contains("GRUB reads /boot"), "{:?}", r.problems);
    }

    #[test]
    fn options() {
        assert_eq!(generic_options("subvol=/@,noatime,compress=zstd,x-systemd.device-timeout=0"), ("noatime,x-systemd.device-timeout=0".to_string(), vec!["subvol=/@".to_string(), "compress=zstd".to_string()]));
        assert_eq!(generic_options("subvol=@").0, "defaults");
        assert_eq!(kernel_version("6.18.60-1-lts"), Some((6, 18)));
        assert_eq!(package_version("1:1.47.2-3"), Some((1, 47)));
    }
}
