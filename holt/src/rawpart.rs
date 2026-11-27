//! Raw GPT partitions (docs/design/gnome-os.md, GN3): partitions a backup
//! takes as their bytes, not as a filesystem, and load writes back byte for
//! byte.  Generic: nothing here is one system's.
//!
//! - **dump** (`mark`): which GPT partitions are raw, by a `Rule` the caller
//!   chooses (`rule_for`; by default the Discoverable Partitions
//!   Specification's usr, usr-verity and usr-verity-sig types, unless
//!   mounted read-write).  Each gets `raw_partition` {image, why, empty,
//!   `size_bytes`, sha256} in `storage.disks[].partitions[]`, and, unless
//!   it's an empty slot, a backup source `fstype: "raw"` with `from: null`
//!   at `RAWPART_DIR/<PARTUUID>.img` of the backup's tree.  An empty slot
//!   (its GPT name one of the rule's empty labels: systemd-sysupdate's
//!   `_empty`) keeps its GPT entry, but not its bytes: load writes zeros
//!   (R6), so stale old data is neither backed up nor kept.
//! - **staging** (`stage`, `bko-holt rawpart-stage LAYOUT DIR`): the online
//!   backup's step that copies each raw partition sparse to
//!   `DIR/<PARTUUID>.img`, checking its size and SHA-256 against the dump's;
//!   the caller bind-mounts DIR at `RAWPART_DIR` of the tree it backs up
//!   (ht1.sh in GN7, bs1-backup in GN10).
//! - **load**: `load::rawvol` writes each image back (`bko-holt raw-write
//!   --size --sha256`), zeros an empty slot, and refuses a plan whose raw
//!   partition changed size; `format` leaves them alone and `--fit-largest`
//!   never resizes one.
//!
//! Images live under their own `RAWPART_DIR`, not `crate::qubes::RAW_DIR`,
//! whose `VG/LV.img` names a volume group called `part` would collide with;
//! by PARTUUID, which stays, not by GPT name, which a sysupdate system
//! changes at each update.

use crate::json::{Obj, Value};
use crate::mounts::Mount;
use crate::sys::Sys;

/// The directory of the backup's tree that holds the raw partitions'
/// images, one per partition at `RAWPART_DIR/<PARTUUID>.img`.
pub const RAWPART_DIR: &str = "/.bko-holt/rawpart";

/// The Discoverable Partitions Specification's /usr, /usr verity and /usr
/// verity signature types, for the architectures bko-holt might meet
/// (`systemd-id128 show --uuid`, systemd 257).
pub const DPS_USR_TYPES: [&str; 24] = [
    // x86-64: usr, usr-verity, usr-verity-sig
    "8484680c-9521-48c6-9c11-b0720656f69e",
    "77ff5f63-e7b6-4633-acf4-1565b864c0e6",
    "e7bb33fb-06cf-4e81-8273-e543b413e2e2",
    // x86
    "75250d76-8cc6-458e-bd66-bd47cc81a812",
    "8f461b0d-14ee-4e81-9aa9-049b6fb97abd",
    "974a71c0-de41-43c3-be5d-5c5ccd1ad2c0",
    // arm64
    "b0e01050-ee5f-4390-949a-9101b17104e9",
    "6e11a4e7-fbca-4ded-b9e9-e1a512bb664e",
    "c23ce4ff-44bd-4b00-b2d4-b41b3419e02a",
    // arm
    "7d0359a3-02b3-4f0a-865c-654403e70625",
    "c215d751-7bcd-4649-be90-6627490a4c05",
    "d7ff812f-37d1-4902-a810-d76ba57b975a",
    // riscv64
    "beaec34b-8442-439b-a40b-984381ed097d",
    "8f1056be-9b05-47c4-81d6-be53128e5b54",
    "d2f9000a-7a18-453f-b5cd-4d32f77a7b32",
    // loongarch64
    "e611c702-575c-4cbe-9a46-434fa0bf7e3f",
    "f46b2c26-59ae-48f0-9106-c50ed47f673d",
    "b024f315-d330-444c-8461-44bbde524e99",
    // ppc64le
    "15bb03af-77e7-4d4a-b12b-c0d084f7491c",
    "ee2b9983-21e8-4153-86d9-b6901a54d1ce",
    "c8bfbd1e-268e-4521-8bba-bf314c399557",
    // s390x
    "8a4f5770-50aa-4ed3-874a-99b710db6fea",
    "31741cc4-1a2a-4111-a581-e00b447d2d06",
    "3f324816-667b-46ae-86ee-9b0c0c6c11b4",
];

/// Content types bko-holt load makes (format's, or assemble's): with
/// `Rule::unmade`, a partition holding anything else, not mounted
/// read-write, is taken raw.  (`crate::qubes`'s rule for LVs is the same.)
pub const MADE: [&str; 17] = [
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

/// Which GPT partitions dump takes raw.  The OS detection that knows a
/// system (GN2's GNOME OS, say) chooses one by `rule_for`, or builds its
/// own and calls `mark` (or `mark_with` for a rule of its own making).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Rule {
    /// A partition of one of `DPS_USR_TYPES`, not mounted read-write.
    pub dps_usr: bool,
    /// A partition whose content isn't one load makes (`MADE`), or that
    /// holds nothing bko-holt recognises, not mounted read-write.  Off by
    /// default: a BIOS boot partition or another system's partition holds
    /// such content too, and what load does with those is unchanged.
    pub unmade: bool,
    /// GPT names (exact) of an empty slot: its entry kept, zeros written.
    pub empty_labels: &'static [&'static str],
    /// GPT names that start so are empty slots too.
    pub empty_prefixes: &'static [&'static str],
}

impl Rule {
    /// Every system's: DPS usr types, and sysupdate's `_empty` slot.
    pub const GENERIC: Rule = Rule {
        dps_usr: true,
        unmade: false,
        empty_labels: &["_empty"],
        empty_prefixes: &[],
    };

    /// What the rule makes of partition P (its dump JSON), RW whether it's
    /// mounted read-write itself (not through a dm device on it).
    pub fn decide(&self, p: &Value, rw: bool) -> Option<Pick> {
        if rw || p.get("extended").and_then(Value::as_bool) == Some(true) {
            return None;
        }
        let ty = p
            .get("type")
            .and_then(Value::as_str)
            .unwrap_or_default()
            .to_ascii_lowercase();
        let why = if self.dps_usr && DPS_USR_TYPES.contains(&ty.as_str()) {
            format!(
                "its type is {}, a Discoverable Partitions /usr type: its bytes, not its files",
                p.get("type_name")
                    .and_then(Value::as_str)
                    .map_or_else(|| ty.clone(), str::to_string)
            )
        } else if self.unmade {
            match p.at(&["content", "type"]).and_then(Value::as_str) {
                None => "nothing bko-holt recognises is on it".to_string(),
                Some(t) if !MADE.contains(&t) => {
                    format!("it holds {t}, which bko-holt load can't make")
                }
                Some(_) => return None,
            }
        } else {
            return None;
        };
        let label = p.get("partlabel").and_then(Value::as_str).unwrap_or("");
        if self.empty_labels.contains(&label)
            || self.empty_prefixes.iter().any(|x| label.starts_with(x))
        {
            return Some(Pick::Empty(format!(
                "an empty slot (GPT name \"{label}\"): its entry is kept, and zeros written, not its old bytes; {why}"
            )));
        }
        Some(Pick::Image(why))
    }
}

/// A raw partition: its image backed up, or an empty slot made zero.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Pick {
    Image(String),
    Empty(String),
}

/// The rule for the system os-release's ID names.  GN2 gives GNOME OS its
/// own (`unmade` on, and the installer's empty labels); every other system
/// takes `Rule::GENERIC`.
pub fn rule_for(_os_id: Option<&str>) -> Rule {
    Rule::GENERIC
}

/// The image path of the partition PARTUUID in the backup's tree.
pub fn image_path(partuuid: &str) -> String {
    format!("{RAWPART_DIR}/{}.img", partuuid.to_ascii_lowercase())
}

/// Whether MOUNTS mount the device DEVNO read-write (itself, not through a
/// dm device on it).
fn mounted_rw(mounts: &[Mount], devno: &str) -> bool {
    mounts
        .iter()
        .any(|m| m.devno == devno && m.options.split(',').next() == Some("rw") && !devno.is_empty())
}

/// SHA-256 of SIZE bytes of SRC from its start, as `sha256sum` prints it,
/// or None when they can't all be read.
fn sha256_of(src: &dyn crate::bytes::ByteSource, size: u64) -> Option<String> {
    let mut h = crate::bytes::Sha256::new();
    let mut at = 0u64;
    while at < size {
        let n = (size - at).min(1 << 20) as usize;
        let b = src.read_exact_at(at, n)?;
        h.update(&b);
        at += n as u64;
    }
    Some(h.hex())
}

/// `mark_with` by RULE.
pub fn mark(sys: &Sys, disks: &mut [Value], mounts: &[Mount], rule: &Rule) -> Vec<Value> {
    mark_with(sys, disks, mounts, &|p, rw| rule.decide(p, rw))
}

/// Marks each GPT partition of DISKS that DECIDE picks (its dump JSON, and
/// whether it's mounted read-write) with `raw_partition`, and gives the
/// backup sources of those that aren't empty slots.  Each image's size is
/// the GPT entry's, exact, and its SHA-256 is read off the partition
/// device here (None, with a warning, when dump can't read it: load then
/// refuses the plan).
pub fn mark_with(
    sys: &Sys,
    disks: &mut [Value],
    mounts: &[Mount],
    decide: &dyn Fn(&Value, bool) -> Option<Pick>,
) -> Vec<Value> {
    let mut sources = Vec::new();
    for d in disks.iter_mut() {
        if d.at(&["partition_table", "type"]).and_then(Value::as_str) != Some("gpt") {
            continue;
        }
        let Some(Value::Arr(parts)) = d.get_mut("partitions") else {
            continue;
        };
        for p in parts.iter_mut() {
            let devno = p
                .get("devno")
                .and_then(Value::as_str)
                .unwrap_or_default()
                .to_string();
            let Some(pick) = decide(p, mounted_rw(mounts, &devno)) else {
                continue;
            };
            let name = p
                .get("name")
                .and_then(Value::as_str)
                .unwrap_or_default()
                .to_string();
            let Some(uuid) = p
                .get("uuid")
                .and_then(Value::as_str)
                .map(str::to_ascii_lowercase)
            else {
                sys.warn(format!(
                    "{name} would be a raw partition, but its GPT entry has no PARTUUID: left as it is"
                ));
                continue;
            };
            let size = p.get("size_bytes").and_then(Value::as_u64).unwrap_or(0);
            let (why, empty) = match &pick {
                Pick::Image(w) => (w.clone(), false),
                Pick::Empty(w) => (w.clone(), true),
            };
            let image = (!empty).then(|| image_path(&uuid));
            let sha = if empty {
                None
            } else {
                let path = p
                    .get("path")
                    .and_then(Value::as_str)
                    .map_or_else(|| format!("/dev/{name}"), str::to_string);
                match sys.open_dev(&path) {
                    Some(src) => {
                        use crate::bytes::ByteSource;
                        if src.size() != size {
                            sys.warn(format!("{name}: the device is {} bytes, its GPT entry {size}: the raw partition's image is the entry's", src.size()));
                        }
                        let h = sha256_of(&src, size);
                        if h.is_none() {
                            sys.warn(format!("{name}: a raw partition, but its {size} bytes couldn't all be read for its SHA-256: load refuses a plan without it"));
                        }
                        h
                    }
                    None => {
                        sys.warn(format!("{name}: a raw partition, but dump couldn't open {path} for its SHA-256 (not root?): load refuses a plan without it"));
                        None
                    }
                }
            };
            let r = Obj::new()
                .set("image", image.clone())
                .set("why", why.as_str())
                .set("empty", empty)
                .set("size_bytes", size)
                .set("sha256", sha)
                .build();
            p.put("raw_partition", r);
            let Some(image) = image else { continue };
            let source = format!("/dev/disk/by-partuuid/{uuid}");
            sources.push(
                Obj::new()
                    .set("path", image.as_str())
                    .set("from", Value::Null)
                    .set("device", name.as_str())
                    .set("fstype", "raw")
                    .set("source", source.as_str())
                    .set("subvol", Value::Null)
                    .set("note", format!(
                        "a raw partition ({why}): its {size} bytes as a sparse image file at this path of the backup's tree, not a mount (`bko-holt rawpart-stage LAYOUT DIR`, DIR bind-mounted at TREE{RAWPART_DIR}); load writes it back to the partition, exact"
                    ))
                    .build(),
            );
        }
    }
    sources
}

/// The raw partitions of a dump LAYOUT: (partition name, its PARTUUID,
/// its `raw_partition`).
pub fn of_layout(layout: &Value) -> Vec<(String, String, Value)> {
    let mut out = Vec::new();
    for d in layout
        .at(&["storage", "disks"])
        .and_then(Value::as_array)
        .into_iter()
        .flatten()
    {
        for p in d
            .get("partitions")
            .and_then(Value::as_array)
            .into_iter()
            .flatten()
        {
            if let Some(r) = p.get("raw_partition").filter(|r| !r.is_null()) {
                out.push((
                    p.get("name")
                        .and_then(Value::as_str)
                        .unwrap_or_default()
                        .to_string(),
                    p.get("uuid")
                        .and_then(Value::as_str)
                        .unwrap_or_default()
                        .to_ascii_lowercase(),
                    r.clone(),
                ));
            }
        }
    }
    out
}

// ---- staging: bko-holt rawpart-stage ---------------------------------------

/// One image staged: its file, size and SHA-256.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Staged {
    pub partuuid: String,
    pub file: std::path::PathBuf,
    pub bytes: u64,
    pub sha256: String,
}

/// Copies DEV (SIZE bytes) to OUT, sparse: a 4 KiB block of zeros is a
/// hole.  Returns its SHA-256.
fn copy_sparse(dev: &std::path::Path, out: &std::path::Path, size: u64) -> Result<String, String> {
    use std::io::Read;
    use std::os::unix::fs::FileExt;
    let mut f = std::fs::File::open(dev).map_err(|e| format!("{}: {e}", dev.display()))?;
    let o = std::fs::File::create(out).map_err(|e| format!("{}: {e}", out.display()))?;
    let mut h = crate::bytes::Sha256::new();
    let mut buf = vec![0u8; 1 << 20];
    let mut at = 0u64;
    while at < size {
        let want = (size - at).min(buf.len() as u64) as usize;
        let mut n = 0;
        while n < want {
            match f.read(&mut buf[n..want]) {
                Ok(0) => break,
                Ok(k) => n += k,
                Err(e) if e.kind() == std::io::ErrorKind::Interrupted => {}
                Err(e) => {
                    return Err(format!(
                        "{}: reading at {}: {e}",
                        dev.display(),
                        at + n as u64
                    ));
                }
            }
        }
        if n < want {
            return Err(format!(
                "{}: ended after {} bytes, not {size}",
                dev.display(),
                at + n as u64
            ));
        }
        h.update(&buf[..n]);
        for (i, b) in buf[..n].chunks(4096).enumerate() {
            if b.iter().any(|&x| x != 0) {
                o.write_all_at(b, at + (i * 4096) as u64)
                    .map_err(|e| format!("{}: {e}", out.display()))?;
            }
        }
        at += n as u64;
    }
    o.set_len(size)
        .and_then(|()| o.sync_all())
        .map_err(|e| format!("{}: {e}", out.display()))?;
    Ok(h.hex())
}

/// Stages LAYOUT's raw partitions' images in DIR (made if it isn't there):
/// each read from `DEV_DIR/<PARTUUID>` (`/dev/disk/by-partuuid` on a
/// running system), copied sparse to `DIR/<PARTUUID>.img`, its size the
/// dump's and its SHA-256 the dump's, else an error (the partition
/// changed since the dump: dump again).  Other `*.img` files in DIR are
/// removed, so a reused DIR holds this layout's images only.
pub fn stage(
    layout: &Value,
    dir: &std::path::Path,
    dev_dir: &std::path::Path,
) -> Result<Vec<Staged>, String> {
    let raws: Vec<_> = of_layout(layout)
        .into_iter()
        .filter(|(_, _, r)| r.get("empty").and_then(Value::as_bool) != Some(true))
        .collect();
    std::fs::create_dir_all(dir).map_err(|e| format!("{}: {e}", dir.display()))?;
    let want: Vec<String> = raws.iter().map(|(_, u, _)| format!("{u}.img")).collect();
    for e in std::fs::read_dir(dir)
        .map_err(|e| format!("{}: {e}", dir.display()))?
        .flatten()
    {
        let n = e.file_name().to_string_lossy().into_owned();
        if n.ends_with(".img") && !want.contains(&n) {
            std::fs::remove_file(e.path()).map_err(|e| format!("{n}: {e}"))?;
        }
    }
    let mut out = Vec::new();
    for (name, uuid, r) in raws {
        if uuid.is_empty() {
            return Err(format!("{name}: a raw partition without a PARTUUID"));
        }
        let size = r.get("size_bytes").and_then(Value::as_u64).unwrap_or(0);
        let dev = dev_dir.join(&uuid);
        let file = dir.join(format!("{uuid}.img"));
        let sha = copy_sparse(&dev, &file, size).map_err(|e| format!("{name}: {e}"))?;
        match r.get("sha256").and_then(Value::as_str) {
            Some(want) if want != sha => {
                return Err(format!(
                    "{name} ({uuid}): its SHA-256 is {sha} now, {want} in the dump: it changed since, so dump again"
                ));
            }
            None => {
                return Err(format!(
                    "{name} ({uuid}): the dump has no SHA-256 for it (dump as root)"
                ));
            }
            Some(_) => {}
        }
        out.push(Staged {
            partuuid: uuid,
            file,
            bytes: size,
            sha256: sha,
        });
    }
    Ok(out)
}

/// `bko-holt rawpart-stage [--dev-dir DIR] LAYOUT DIR`: the dump LAYOUT's
/// raw partitions copied sparse into DIR, a line each on standard output
/// ("PARTUUID BYTES SHA256"); the caller bind-mounts DIR at
/// TREE/.bko-holt/rawpart for the backup.  Exit 0, 1 on a failure (a size
/// or SHA-256 not the dump's), 2 on bad arguments.
pub fn main(args: &[String]) -> u8 {
    let usage = "usage: bko-holt rawpart-stage [--dev-dir DIR] LAYOUT DIR";
    let mut dev_dir = std::path::PathBuf::from("/dev/disk/by-partuuid");
    let mut pos = Vec::new();
    let mut it = args.iter();
    while let Some(a) = it.next() {
        match a.as_str() {
            "--dev-dir" => match it.next() {
                Some(d) => dev_dir = d.into(),
                None => {
                    eprintln!("{usage}");
                    return 2;
                }
            },
            "-h" | "--help" => {
                println!(
                    "{usage}\n\nCopies the raw partitions a bko-holt dump lists (storage.disks[].partitions[].raw_partition, not the empty slots) sparse into DIR as PARTUUID.img, each checked against the dump's size and SHA-256, and removes DIR's other .img files.  Bind-mount DIR at TREE{RAWPART_DIR} while backing up TREE."
                );
                return 0;
            }
            x if x.starts_with('-') => {
                eprintln!("{usage}");
                return 2;
            }
            x => pos.push(x.to_string()),
        }
    }
    let [layout, dir] = pos.as_slice() else {
        eprintln!("{usage}");
        return 2;
    };
    let v = match std::fs::read_to_string(layout)
        .map_err(|e| e.to_string())
        .and_then(|t| crate::json::parse(&t).map_err(|e| e.to_string()))
    {
        Ok(v) => v,
        Err(e) => {
            eprintln!("bko-holt rawpart-stage: {layout}: {e}");
            return 1;
        }
    };
    match stage(&v, std::path::Path::new(dir), &dev_dir) {
        Ok(st) => {
            for s in &st {
                println!("{} {} {}", s.partuuid, s.bytes, s.sha256);
            }
            eprintln!(
                "bko-holt rawpart-stage: {} raw partition images in {dir}",
                st.len()
            );
            0
        }
        Err(e) => {
            eprintln!("bko-holt rawpart-stage: {e}");
            1
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::testutil::Tree;

    fn part(
        name: &str,
        ty: &str,
        uuid: &str,
        label: &str,
        size: u64,
        content: Option<&str>,
    ) -> Value {
        let c = content.map_or("null".to_string(), |t| format!("{{\"type\": \"{t}\"}}"));
        crate::json::parse(&format!(
            r#"{{"name": "{name}", "path": "/dev/{name}", "devno": "253:{}", "type": "{ty}", "uuid": "{uuid}", "partlabel": "{label}", "size_bytes": {size}, "content": {c}}}"#,
            name.len()
        ))
        .unwrap()
    }

    const USR: &str = "8484680c-9521-48c6-9c11-b0720656f69e";
    const VERITY: &str = "77ff5f63-e7b6-4633-acf4-1565b864c0e6";
    const LINUX: &str = "0fc63daf-8483-4772-8e79-3d69d8477de4";

    #[test]
    fn rules() {
        let g = Rule::GENERIC;
        assert!(matches!(
            g.decide(
                &part("vda3", USR, "a", "gnomeos_usr_51", 4096, Some("erofs")),
                false
            ),
            Some(Pick::Image(_))
        ));
        assert!(matches!(
            g.decide(&part("vda4", VERITY, "b", "_empty", 4096, None), false),
            Some(Pick::Empty(_))
        ));
        // Mounted read-write itself: its files are backed up.
        assert!(
            g.decide(&part("vda3", USR, "a", "usr", 4096, Some("ext4")), true)
                .is_none()
        );
        // Unmade content counts only with `unmade`.
        let other = part("vda6", LINUX, "c", "x", 4096, Some("erofs"));
        assert!(g.decide(&other, false).is_none());
        let u = Rule {
            unmade: true,
            empty_prefixes: &["os_usr_empty"],
            ..Rule::GENERIC
        };
        assert!(matches!(u.decide(&other, false), Some(Pick::Image(w)) if w.contains("erofs")));
        assert!(matches!(
            u.decide(&part("vda7", LINUX, "d", "", 4096, None), false),
            Some(Pick::Image(_))
        ));
        assert!(
            u.decide(&part("vda8", LINUX, "e", "", 4096, Some("btrfs")), false)
                .is_none()
        );
        assert!(matches!(
            u.decide(&part("vda9", USR, "f", "os_usr_empty_v", 4096, None), false),
            Some(Pick::Empty(_))
        ));
        assert_eq!(image_path("AB-CD"), "/.bko-holt/rawpart/ab-cd.img");
    }

    /// dump: the marks, the SHA-256 read off the device, the sources (none
    /// for the empty slot), nothing on an MBR disk.
    #[test]
    fn mark_and_stage() {
        let t = Tree::new("rawpart");
        let img: Vec<u8> = (0..20000u32)
            .map(|i| if i < 9000 { 0 } else { (i % 251) as u8 })
            .collect();
        t.file("/dev/vda3", &img);
        let sys = Sys::new(t.root(), Box::new(crate::sys::FakeRunner::default()), true);
        let ua = "11111111-2222-4333-8444-555555555555";
        let mut disks = vec![
            crate::json::parse(&format!(
                r#"{{"name": "vda", "partition_table": {{"type": "gpt"}}, "partitions": [{}, {}, {}]}}"#,
                part("vda3", USR, &ua.to_ascii_uppercase(), "gnomeos_usr_51", 20000, Some("erofs")).compact(),
                part("vda4", USR, "99999999-2222-4333-8444-555555555555", "_empty", 8192, None).compact(),
                part("vda5", LINUX, "88888888-2222-4333-8444-555555555555", "root", 8192, Some("btrfs")).compact(),
            ))
            .unwrap(),
            crate::json::parse(&format!(
                r#"{{"name": "vdb", "partition_table": {{"type": "dos"}}, "partitions": [{}]}}"#,
                part("vdb1", USR, "1", "", 512, None).compact()
            ))
            .unwrap(),
        ];
        let src = mark(&sys, &mut disks, &[], &Rule::GENERIC);
        assert_eq!(src.len(), 1);
        assert_eq!(
            src[0].get("path").and_then(Value::as_str),
            Some(image_path(ua).as_str())
        );
        assert_eq!(src[0].get("fstype").and_then(Value::as_str), Some("raw"));
        assert_eq!(
            src[0].get("source").and_then(Value::as_str),
            Some(format!("/dev/disk/by-partuuid/{ua}").as_str())
        );
        assert!(src[0].get("from").is_some_and(Value::is_null));
        let r = disks[0].at(&["partitions", "0", "raw_partition"]).unwrap();
        assert_eq!(
            r.get("sha256").and_then(Value::as_str),
            Some(crate::bytes::sha256_hex(&img).as_str())
        );
        assert_eq!(r.get("size_bytes").and_then(Value::as_u64), Some(20000));
        let e = disks[0].at(&["partitions", "1", "raw_partition"]).unwrap();
        assert_eq!(e.get("empty").and_then(Value::as_bool), Some(true));
        assert!(e.get("image").is_some_and(Value::is_null));
        assert!(disks[0].at(&["partitions", "2", "raw_partition"]).is_none());
        assert!(disks[1].at(&["partitions", "0", "raw_partition"]).is_none());
        assert!(sys.warnings().is_empty(), "{:?}", sys.warnings());

        // Staging: the image copied sparse and checked; a stale .img gone.
        let layout = crate::json::parse(&format!(
            r#"{{"storage": {{"disks": [{}]}}}}"#,
            disks[0].compact()
        ))
        .unwrap();
        let devs = t.p("/by-partuuid");
        std::fs::create_dir_all(&devs).unwrap();
        std::fs::write(devs.join(ua), &img).unwrap();
        let dir = t.p("/stage");
        std::fs::create_dir_all(&dir).unwrap();
        std::fs::write(dir.join("old.img"), b"x").unwrap();
        let st = stage(&layout, &dir, &devs).unwrap();
        assert_eq!(st.len(), 1);
        assert_eq!(std::fs::read(dir.join(format!("{ua}.img"))).unwrap(), img);
        assert!(!dir.join("old.img").exists());
        assert_eq!(st[0].sha256, crate::bytes::sha256_hex(&img));
        // Changed since the dump: refused.
        let mut changed = img.clone();
        changed[100] = 1;
        std::fs::write(devs.join(ua), &changed).unwrap();
        assert!(
            stage(&layout, &dir, &devs)
                .unwrap_err()
                .contains("changed since")
        );
        // Shorter than the dump's size: refused.
        std::fs::write(devs.join(ua), &img[..1000]).unwrap();
        assert!(
            stage(&layout, &dir, &devs)
                .unwrap_err()
                .contains("ended after 1000")
        );
    }

    /// Without root, dump can't read the device: no SHA-256, and a warning.
    #[test]
    fn unprivileged_has_no_hash() {
        let t = Tree::new("rawpart-unpriv");
        t.file("/dev/vda3", vec![1u8; 4096]);
        let sys = Sys::new(t.root(), Box::new(crate::sys::FakeRunner::default()), false);
        let mut disks =
            vec![crate::json::parse(&format!(
            r#"{{"name": "vda", "partition_table": {{"type": "gpt"}}, "partitions": [{}]}}"#,
            part("vda3", USR, "11111111-2222-4333-8444-555555555555", "u", 4096, None).compact()
        ))
        .unwrap()];
        assert_eq!(mark(&sys, &mut disks, &[], &Rule::GENERIC).len(), 1);
        assert!(
            disks[0]
                .at(&["partitions", "0", "raw_partition", "sha256"])
                .is_some_and(Value::is_null)
        );
        assert!(sys.warnings().iter().any(|w| w.contains("SHA-256")));
    }
}
