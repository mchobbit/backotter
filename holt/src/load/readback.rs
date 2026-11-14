//! format's read-back (layload.md, "Feature sets"): each filesystem and
//! swap area, once made, is read with bko-holt dump's own probe and held to
//! the source's content in the layout: its type, UUID, label, block size
//! and the features mkfs could have chosen.  A difference stops the stage.
//! It's the guard against a rescue image's mkfs that turns something on
//! by default which load didn't name (mkfs.f2fs can't even be asked to
//! leave a feature off).
//!
//! Also plan's check 4: a feature the probes can't name is one load can't
//! ask mkfs for, so the plan refuses it.

use super::commands::{bcachefs_version, BTRFS_FEATURES, EXT_SETTABLE, F2FS_SETTABLE};
use crate::json::Value;
use crate::sys::Sys;

/// The types format makes and reads back.
pub fn reads_back(ty: &str) -> bool {
    matches!(ty, "ext2" | "ext3" | "ext4" | "xfs" | "btrfs" | "f2fs" | "nilfs2" | "jfs" | "vfat" | "msdos" | "swap" | "bcachefs")
}

/// A name a probe gives a feature bit it doesn't know: ext's FEATURE_C13
/// (e2fsprogs' own spelling), XFS's incompat_bit15, btrfs's compat_ro_bit9,
/// F2FS's feature_bit20, NILFS2's compat_bit3.
pub fn is_unnamed(f: &str) -> bool {
    let digits = |s: &str| !s.is_empty() && s.bytes().all(|b| b.is_ascii_digit());
    if let Some(r) = f.strip_prefix("FEATURE_") {
        return r.len() > 1 && matches!(r.as_bytes()[0], b'C' | b'I' | b'R') && digits(&r[1..]);
    }
    f.rsplit_once("_bit").is_some_and(|(_, n)| digits(n))
}

/// Every feature a content records: `features`, and btrfs's
/// `compat_ro_features`.
fn features(c: &Value) -> Vec<String> {
    ["features", "compat_ro_features"]
        .iter()
        .flat_map(|k| c.get(k).and_then(Value::as_array).into_iter().flatten())
        .filter_map(Value::as_str)
        .map(str::to_string)
        .collect()
}

/// The source's features no probe can name (plan's check 4).
pub fn unnamed(c: &Value) -> Vec<String> {
    features(c).into_iter().filter(|f| is_unnamed(f)).collect()
}

/// Whether feature F of a TY filesystem is one the read-back holds to the
/// source's: one load asks mkfs for, or one mkfs could turn on by itself (a
/// bit the probe can't name).  Not one that records state (ext's
/// needs_recovery and orphan_present, XFS's needsrepair), nor one mkfs
/// can't set (ext's uninit_bg, btrfs's compress_zstd, F2FS's atomic_write).
/// XFS's meta_uuid records that the UUID was changed after mkfs; the UUID
/// itself is compared.
fn comparable(ty: &str, f: &str) -> bool {
    if is_unnamed(f) {
        return true;
    }
    match ty {
        "ext2" | "ext3" | "ext4" => EXT_SETTABLE.contains(&f),
        "xfs" => !matches!(f, "needsrepair" | "meta_uuid"),
        "btrfs" => f == "mixed_groups" || BTRFS_FEATURES.iter().any(|(sb, _)| *sb == f),
        "f2fs" => F2FS_SETTABLE.contains(&f),
        _ => true,
    }
}

/// The fields that hold each type's block size (and FAT's FAT size).
fn geometry(ty: &str) -> &'static [&'static str] {
    match ty {
        "btrfs" => &["sector_size"],
        "vfat" | "msdos" => &["fat_bits", "bytes_per_sector", "sectors_per_cluster"],
        "swap" => &["page_size"],
        _ => &["block_size"],
    }
}

/// The step's line in the plan.
pub fn line(dev: &str, content: &Value) -> String {
    let ty = content.get("type").and_then(Value::as_str).unwrap_or("?");
    let what = match ty {
        "vfat" | "msdos" => "with the source's FAT size, sector and cluster size",
        "swap" => "with the source's UUID, label and page size",
        "jfs" => "with the source's UUID, label and version",
        "bcachefs" => "with the source's UUID, label and on-disk version",
        // --reformat (reformat.rs): a new type, with the features it chose.
        _ if content.get("reformatted_from").is_some() => "with the source's UUID and the features --reformat chose",
        _ => "with the source's features",
    };
    format!("# check: {dev} is {ty} {what} (read back by bko-holt's own probe)\n")
}

fn same(a: &Value, b: &Value) -> bool {
    match (a.as_u64(), b.as_u64()) {
        (Some(x), Some(y)) => x == y,
        _ => a == b,
    }
}

fn shown(v: Option<&Value>) -> String {
    match v {
        None | Some(Value::Null) => "none".into(),
        Some(Value::Str(s)) => format!("\"{s}\""),
        Some(x) => x.compact(),
    }
}

/// What differs between the source's content WANT and GOT, what the probe
/// reads from DEV now: one sentence each, naming DEV.
pub fn differences(dev: &str, want: &Value, got: &Value) -> Vec<String> {
    let ty = want.get("type").and_then(Value::as_str).unwrap_or_default();
    let got_ty = got.get("type").and_then(Value::as_str).unwrap_or_default();
    let kind = |t: &str| if t == "msdos" { "vfat".to_string() } else { t.to_string() };
    if kind(ty) != kind(got_ty) {
        return vec![format!("{dev}: reads back as {got_ty}, not the source's {ty}")];
    }
    let mut out = Vec::new();
    if let Some(w) = want.get("uuid").and_then(Value::as_str) {
        let g = got.get("uuid").and_then(Value::as_str);
        if g.map(str::to_ascii_lowercase) != Some(w.to_ascii_lowercase()) {
            out.push(format!("{dev}: its UUID reads back as {}, not the source's {w}", g.unwrap_or("none")));
        }
    }
    let label = |v: &Value| v.get("label").filter(|l| !l.is_null()).cloned();
    if label(want) != label(got) {
        out.push(format!("{dev}: its label reads back as {}, not the source's {}", shown(label(got).as_ref()), shown(label(want).as_ref())));
    }
    for k in geometry(ty) {
        let Some(w) = want.get(k).filter(|w| !w.is_null()) else { continue };
        match got.get(k) {
            Some(g) if same(w, g) => {}
            g => out.push(format!("{dev}: its {k} reads back as {}, not the source's {}", shown(g), shown(Some(w)))),
        }
    }
    // The on-disk version: JFS's superblock version (load puts 1 back after
    // jfs_tune), and the bcachefs version format was asked for (its
    // version_min, or its version without one; the version itself may be
    // newer once a newer kernel has mounted it).
    if ty == "jfs" {
        if let Some(w) = want.get("version").and_then(Value::as_str) {
            let g = got.get("version").and_then(Value::as_str);
            if g != Some(w) {
                out.push(format!("{dev}: its superblock version reads back as {}, not the source's {w}", g.unwrap_or("none")));
            }
        }
    }
    if ty == "bcachefs" {
        let asked = want.get("version_min").or_else(|| want.get("version")).and_then(Value::as_str).filter(|v| bcachefs_version(v).is_some());
        if let Some(w) = asked {
            let g = got.get("version_min").and_then(Value::as_str);
            if g.and_then(bcachefs_version) != bcachefs_version(w) {
                out.push(format!("{dev}: its version_min reads back as {}, not {w}, the version format was asked for", g.unwrap_or("none")));
            }
        }
    }
    let (w, g) = (features(want), features(got));
    for f in g.iter().filter(|f| comparable(ty, f) && !w.contains(f)) {
        out.push(format!("{dev}: made with {f}, which the source's {ty} didn't have: the rescue image's mkfs turned it on"));
    }
    for f in w.iter().filter(|f| comparable(ty, f) && !g.contains(f)) {
        out.push(format!("{dev}: made without {f}, which the source's {ty} had: the rescue image's mkfs turned it off"));
    }
    out
}

/// Reads DEV back and holds it to WANT, the source's content.
pub fn check(sys: &Sys, dev: &str, want: &Value) -> Result<(), String> {
    let src = sys.open_dev(&crate::other::node_path(sys, dev)).ok_or_else(|| format!("{dev}: can't be read back"))?;
    let found = crate::probe::probe_all(&src);
    let ty = want.get("type").and_then(Value::as_str).unwrap_or_default();
    let ty = if ty == "msdos" { "vfat" } else { ty };
    // The signature of the source's type, ahead of any other the probe
    // finds there; without one, the first, which is the difference.
    let Some(made) = found.iter().find(|c| c.kind == ty).or(found.first()) else {
        return Err(format!("{dev}: bko-holt's probe finds nothing on it, not the source's {ty}"));
    };
    let d = differences(dev, want, &made.to_json());
    if d.is_empty() {
        Ok(())
    } else {
        Err(d.join("; "))
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::bytes::MemSource;
    use crate::probe::{self, bcachefs, btrfs, ext, f2fs, fat, misc, nilfs2, xfs};

    /// What the probe reads from IMG, as the dump records it.
    fn content(img: Vec<u8>) -> Value {
        probe::probe_all(&MemSource(img)).first().expect("a sample the probe knows").to_json()
    }

    /// The source's content SRC with KEY set to VALUE, as a dump of another
    /// filesystem could have it.
    fn with(src: &Value, key: &str, value: Value) -> Value {
        let mut v = src.clone();
        if let Value::Obj(o) = &mut v {
            match o.iter_mut().find(|(k, _)| k == key) {
                Some((_, x)) => *x = value,
                None => o.push((key.to_string(), value)),
            }
        }
        v
    }

    fn one(d: Vec<String>) -> String {
        assert_eq!(d.len(), 1, "{d:?}");
        d.into_iter().next().unwrap()
    }

    // What Debian 13's mke2fs.conf gives ext4 (probe::ext's tests).
    const T_COMPAT: u32 = 0x0004 | 0x0008 | 0x0010 | 0x0020 | 0x1000;
    const T_INCOMPAT: u32 = 0x0002 | 0x0040 | 0x0080 | 0x0200 | 0x2000;
    const T_RO: u32 = 0x0001 | 0x0002 | 0x0008 | 0x0020 | 0x0040 | 0x0400;

    #[test]
    fn ext4() {
        // The source was dumped while mounted (needs_recovery, orphan_present):
        // state, not design.
        let src = content(ext::sample::image(T_COMPAT, T_INCOMPAT | 0x0004, T_RO | 0x10000, "root"));
        assert!(differences("/dev/vdb2", &src, &content(ext::sample::image(T_COMPAT, T_INCOMPAT, T_RO, "root"))).is_empty());
        // Debian 12's ext4 has no orphan_file; a newer mke2fs gave it one.
        let src = content(ext::sample::image(T_COMPAT & !0x1000, T_INCOMPAT, T_RO, "root"));
        let e = one(differences("/dev/vdb2", &src, &content(ext::sample::image(T_COMPAT, T_INCOMPAT, T_RO, "root"))));
        assert_eq!(e, "/dev/vdb2: made with orphan_file, which the source's ext4 didn't have: the rescue image's mkfs turned it on");
        // A feature mke2fs can't set (uninit_bg) isn't held to the source's.
        let src = content(ext::sample::image(T_COMPAT, T_INCOMPAT, T_RO | 0x0010, "root"));
        assert!(differences("/dev/vdb2", &src, &content(ext::sample::image(T_COMPAT, T_INCOMPAT, T_RO, "root"))).is_empty());
        // ext3 made as ext4 is another type.
        let src = content(ext::sample::image(0x0004, 0x0002, 0x0001, ""));
        let e = one(differences("/dev/vdb2", &src, &content(ext::sample::image(0x0004, 0x0002 | 0x0040, 0x0001, ""))));
        assert_eq!(e, "/dev/vdb2: reads back as ext4, not the source's ext3");
    }

    #[test]
    fn xfs() {
        let src = content(xfs::sample::image("data"));
        assert!(differences("/dev/sdq3", &src, &content(xfs::sample::image("data"))).is_empty());
        // Rocky 10's XFS has no exchange; xfsprogs 6.18 turns it on.
        let mut img = xfs::sample::image("data");
        img[216..220].copy_from_slice(&(0x2bu32 | 0x40).to_be_bytes());
        let e = one(differences("/dev/sdq3", &src, &content(img)));
        assert_eq!(e, "/dev/sdq3: made with exchange, which the source's xfs didn't have: the rescue image's mkfs turned it on");
        // needsrepair and meta_uuid aren't mkfs's choices.
        let mut img = xfs::sample::image("data");
        img[216..220].copy_from_slice(&(0x2bu32 | 0x10 | 0x04).to_be_bytes());
        assert!(differences("/dev/sdq3", &content(img), &content(xfs::sample::image("data"))).is_empty());
        // Another block size, and another label.
        let mut img = xfs::sample::image("other");
        img[4..8].copy_from_slice(&1024u32.to_be_bytes());
        let d = differences("/dev/sdq3", &src, &content(img));
        assert_eq!(d, vec!["/dev/sdq3: its label reads back as \"other\", not the source's \"data\"", "/dev/sdq3: its block_size reads back as 1024, not the source's 4096"]);
    }

    #[test]
    fn btrfs() {
        // compress_zstd is the kernel's, once something is compressed;
        // mkfs.btrfs never sets it.
        let src = content(btrfs::sample::image("fedora", 0x1 | 0x10 | 0x20 | 0x40 | 0x100 | 0x200));
        let made = content(btrfs::sample::image("fedora", 0x1 | 0x20 | 0x40 | 0x100 | 0x200));
        assert!(differences("/dev/vda3", &src, &made).is_empty());
        // block-group-tree (compat_ro) the source didn't have.
        let mut img = btrfs::sample::image("fedora", 0x1 | 0x20 | 0x40 | 0x100 | 0x200);
        img[0x10000 + 0xb4..0x10000 + 0xbc].copy_from_slice(&0xbu64.to_le_bytes());
        let e = one(differences("/dev/vda3", &src, &content(img)));
        assert_eq!(e, "/dev/vda3: made with block_group_tree, which the source's btrfs didn't have: the rescue image's mkfs turned it on");
        // no-holes off, mixed block groups on.
        let e = differences("/dev/vda3", &src, &content(btrfs::sample::image("fedora", 0x1 | 0x4 | 0x20 | 0x40 | 0x100)));
        assert_eq!(
            e,
            vec![
                "/dev/vda3: made with mixed_groups, which the source's btrfs didn't have: the rescue image's mkfs turned it on",
                "/dev/vda3: made without no_holes, which the source's btrfs had: the rescue image's mkfs turned it off"
            ]
        );
    }

    #[test]
    fn f2fs() {
        // The superblock's version is the f2fs-tools release that made it.
        let src = with(&content(f2fs::sample::image("mintroot", 0x0008 | 0x0020)), "version", "1.17".into());
        assert!(differences("/dev/vda2", &src, &content(f2fs::sample::image("mintroot", 0x0008 | 0x0020))).is_empty());
        // mkfs.f2fs can't be asked to leave sb_checksum off: only this sees it.
        let e = one(differences("/dev/vda2", &src, &content(f2fs::sample::image("mintroot", 0x0008 | 0x0020 | 0x0800))));
        assert_eq!(e, "/dev/vda2: made with sb_checksum, which the source's f2fs didn't have: the rescue image's mkfs turned it on");
        // atomic_write isn't mkfs.f2fs's to set.
        let src = content(f2fs::sample::image("mintroot", 0x0004));
        assert!(differences("/dev/vda2", &src, &content(f2fs::sample::image("mintroot", 0))).is_empty());
    }

    #[test]
    fn nilfs2() {
        let src = content(nilfs2::sample::image("zorinroot", 1));
        assert!(differences("/dev/vda2", &src, &content(nilfs2::sample::image("zorinroot", 1))).is_empty());
        let e = one(differences("/dev/vda2", &src, &content(nilfs2::sample::image("zorinroot", 0))));
        assert_eq!(e, "/dev/vda2: made without block_count, which the source's nilfs2 had: the rescue image's mkfs turned it off");
    }

    #[test]
    fn jfs() {
        let src = content(misc::sample::jfs(1, "root"));
        assert!(differences("/dev/vda2", &src, &content(misc::sample::jfs(1, "root"))).is_empty());
        // jfs_tune -U left version 2.
        let e = one(differences("/dev/vda2", &src, &content(misc::sample::jfs(2, "root"))));
        assert_eq!(e, "/dev/vda2: its superblock version reads back as 2, not the source's 1");
    }

    #[test]
    fn fat() {
        let src = content(fat::sample::fat32(1 << 20, 0x1234_abcd, "EFI"));
        assert!(differences("/dev/vda1", &src, &content(fat::sample::fat32(1 << 20, 0x1234_abcd, "EFI"))).is_empty());
        // The source's dump lowercased: the UUID is compared without case.
        let lower = with(&src, "uuid", "1234-abcd".into());
        assert!(differences("/dev/vda1", &lower, &content(fat::sample::fat32(1 << 20, 0x1234_abcd, "EFI"))).is_empty());
        let d = differences("/dev/vda1", &src, &content(fat::sample::fat16(32768, 0x1234_abcd, "EFI")));
        assert!(d.contains(&"/dev/vda1: its fat_bits reads back as 16, not the source's 32".to_string()), "{d:?}");
        let e = one(differences("/dev/vda1", &src, &content(fat::sample::fat32(1 << 20, 0x1234_abce, "EFI"))));
        assert_eq!(e, "/dev/vda1: its UUID reads back as 1234-ABCE, not the source's 1234-ABCD");
    }

    #[test]
    fn swap() {
        let src = content(misc::sample::swap(256, "swap0"));
        assert!(differences("/dev/vda3", &src, &content(misc::sample::swap(512, "swap0"))).is_empty());
        let e = one(differences("/dev/vda3", &src, &content(misc::sample::swap(256, ""))));
        assert_eq!(e, "/dev/vda3: its label reads back as none, not the source's \"swap0\"");
        let e = one(differences("/dev/vda3", &with(&src, "page_size", 65536u64.into()), &content(misc::sample::swap(256, "swap0"))));
        assert_eq!(e, "/dev/vda3: its page_size reads back as 4096, not the source's 65536");
    }

    #[test]
    fn bcachefs() {
        // NixOS 26.05's: written at 1.39 by its kernel, version_min 1.13,
        // which format is asked for.  The superblock's size is noise.
        let src = with(&with(&content(bcachefs::sample::image("nixroot", 0)), "version", "1.39".into()), "u64s", 999u64.into());
        assert!(differences("/dev/vda2", &src, &content(bcachefs::sample::image("nixroot", 0))).is_empty());
        let mut img = bcachefs::sample::image("nixroot", 0);
        img[4096 + 18..4096 + 20].copy_from_slice(&((1u16 << 10) | 39).to_le_bytes());
        let e = one(differences("/dev/vda2", &src, &content(img)));
        assert_eq!(e, "/dev/vda2: its version_min reads back as 1.39, not 1.13, the version format was asked for");
    }

    #[test]
    fn another_type_or_nothing() {
        let src = content(xfs::sample::image("data"));
        let e = one(differences("/dev/vdb1", &src, &content(misc::sample::swap(64, ""))));
        assert_eq!(e, "/dev/vdb1: reads back as swap, not the source's xfs");
    }

    /// check reads the device itself, through the Sys.
    #[test]
    fn check_reads_the_device() {
        use crate::sys::FakeRunner;
        let t = crate::testutil::Tree::new("load-readback");
        let mut img = xfs::sample::image("data");
        t.file("/dev/sdq3", &img);
        let s = Sys::new(t.root(), Box::new(FakeRunner::default()), true);
        let src = content(img.clone());
        assert_eq!(check(&s, "/dev/sdq3", &src), Ok(()));
        img[216..220].copy_from_slice(&(0x2bu32 | 0x40 | 0x80).to_be_bytes());
        t.file("/dev/sdq3", &img);
        let e = check(&s, "/dev/sdq3", &src).unwrap_err();
        assert_eq!(
            e,
            "/dev/sdq3: made with exchange, which the source's xfs didn't have: the rescue image's mkfs turned it on; /dev/sdq3: made with parent, which the source's xfs didn't have: the rescue image's mkfs turned it on"
        );
        t.file("/dev/sdq3", vec![0u8; 65536]);
        assert_eq!(check(&s, "/dev/sdq3", &src).unwrap_err(), "/dev/sdq3: bko-holt's probe finds nothing on it, not the source's xfs");
        assert_eq!(check(&s, "/dev/sdq9", &src).unwrap_err(), "/dev/sdq9: can't be read back");
    }

    #[test]
    fn unnamed_features() {
        for f in ["FEATURE_C13", "FEATURE_I5", "FEATURE_R20", "incompat_bit15", "compat_ro_bit9", "feature_bit20", "compat_bit3", "features2_bit0"] {
            assert!(is_unnamed(f), "{f}");
        }
        for f in ["FEATURE_", "FEATURE_X1", "has_journal", "64bit", "orphan_file", "incompat_bit", "rabbit"] {
            assert!(!is_unnamed(f), "{f}");
        }
        let mut img = btrfs::sample::image("", 0x1 | (1 << 40));
        img[0x10000 + 0xb4..0x10000 + 0xbc].copy_from_slice(&(3u64 | 1 << 9).to_le_bytes());
        assert_eq!(unnamed(&content(img)), vec!["incompat_bit40", "compat_ro_bit9"]);
        assert!(unnamed(&content(xfs::sample::image("x"))).is_empty());
    }

    #[test]
    fn plan_line() {
        let c = crate::load::commands::Cmd::read_back("/dev/sdq3", &content(xfs::sample::image("data")));
        assert_eq!(c.shell(), "# check: /dev/sdq3 is xfs with the source's features (read back by bko-holt's own probe)\n");
        assert!(!c.is_note());
        assert_eq!(line("/dev/vda3", &content(misc::sample::swap(64, ""))), "# check: /dev/vda3 is swap with the source's UUID, label and page size (read back by bko-holt's own probe)\n");
    }
}
