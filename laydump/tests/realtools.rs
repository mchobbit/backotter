//! Cross-checks against Debian 13's own tools: filesystems, swap, LUKS and
//! partition tables made by mkfs.*, mkswap, cryptsetup, sfdisk and sgdisk in
//! image files, then read both by bko-laydump and by blkid, dumpe2fs,
//! xfs_db, btrfs, cryptsetup and sfdisk.  None of it needs root.
//!
//! These tests are meant for Debian 13: `runs_on_debian13` fails anywhere
//! else, and a missing tool fails its test (install the packages listed in
//! laydump/README.md) unless BKO_LAYDUMP_ALLOW_MISSING_TOOLS=1.

use bko_laydump::bytes::FileSource;
use bko_laydump::json::{self, Value};
use bko_laydump::parttable::{self, Table};
use bko_laydump::probe::{self, Content};
use bko_laydump::testutil::TempDir;
use std::collections::HashMap;
use std::path::{Path, PathBuf};
use std::process::Command;

const PATH: &str = "/usr/sbin:/usr/bin:/sbin:/bin";

fn tool(name: &str) -> Option<PathBuf> {
    PATH.split(':').map(|d| Path::new(d).join(name)).find(|p| p.is_file())
}

/// The tool's path; when it's missing, fails the test, or (with
/// BKO_LAYDUMP_ALLOW_MISSING_TOOLS=1) returns None so the test skips.
fn need(name: &str) -> Option<PathBuf> {
    match tool(name) {
        Some(p) => Some(p),
        None if std::env::var("BKO_LAYDUMP_ALLOW_MISSING_TOOLS").as_deref() == Ok("1") => {
            eprintln!("SKIP: {name} not installed");
            None
        }
        None => panic!("{name} is not installed (BKO_LAYDUMP_ALLOW_MISSING_TOOLS=1 skips)"),
    }
}

fn run(prog: &str, args: &[&str]) -> String {
    let exe = tool(prog).unwrap_or_else(|| panic!("{prog} missing"));
    let out = Command::new(&exe).args(args).env("PATH", PATH).env("LC_ALL", "C").output().unwrap();
    assert!(out.status.success(), "{prog} {args:?} failed: {}", String::from_utf8_lossy(&out.stderr));
    String::from_utf8_lossy(&out.stdout).into_owned()
}

fn run_stdin(prog: &str, args: &[&str], input: &str) -> String {
    use std::io::Write;
    let exe = tool(prog).unwrap_or_else(|| panic!("{prog} missing"));
    let mut child = Command::new(&exe)
        .args(args)
        .env("PATH", PATH)
        .env("LC_ALL", "C")
        .stdin(std::process::Stdio::piped())
        .stdout(std::process::Stdio::piped())
        .stderr(std::process::Stdio::piped())
        .spawn()
        .unwrap();
    child.stdin.take().unwrap().write_all(input.as_bytes()).unwrap();
    let out = child.wait_with_output().unwrap();
    assert!(out.status.success(), "{prog} {args:?} failed: {}", String::from_utf8_lossy(&out.stderr));
    String::from_utf8_lossy(&out.stdout).into_owned()
}

/// A sparse image file of `mib` MiB.
fn image(dir: &TempDir, name: &str, mib: u64) -> PathBuf {
    let p = dir.path().join(name);
    let f = std::fs::File::create(&p).unwrap();
    f.set_len(mib << 20).unwrap();
    p
}

fn s(p: &Path) -> &str {
    p.to_str().unwrap()
}

/// blkid's low-level probe of a file: KEY=VALUE pairs.
fn blkid(p: &Path) -> HashMap<String, String> {
    run("blkid", &["-p", "-o", "export", s(p)])
        .lines()
        .filter_map(|l| l.split_once('=').map(|(k, v)| (k.to_string(), v.to_string())))
        .collect()
}

fn probe_file(p: &Path) -> Vec<Content> {
    probe::probe_all(&FileSource::open(p).unwrap())
}

/// Our first signature agrees with blkid on TYPE, UUID, LABEL (and VERSION
/// where both report one).
fn assert_matches_blkid(p: &Path) -> Content {
    let ours = probe_file(p);
    let b = blkid(p);
    let c = ours.first().unwrap_or_else(|| panic!("nothing found in {}; blkid says {b:?}", p.display())).clone();
    assert_eq!(Some(&c.kind), b.get("TYPE"), "TYPE of {}", p.display());
    assert_eq!(c.uuid.as_ref().map(|u| u.to_lowercase()), b.get("UUID").map(|u| u.to_lowercase()), "UUID of {}", p.display());
    assert_eq!(c.label.as_ref(), b.get("LABEL"), "LABEL of {}", p.display());
    if let (Some(ours), Some(theirs)) = (&c.version, b.get("VERSION")) {
        assert_eq!(ours, theirs, "VERSION of {}", p.display());
    }
    if let Some(usage) = b.get("USAGE") {
        assert_eq!(c.usage, usage, "USAGE of {}", p.display());
    }
    c
}

fn detail<'a>(c: &'a Content, k: &str) -> &'a Value {
    c.details.0.iter().find(|(key, _)| key == k).map(|(_, v)| v).unwrap_or_else(|| panic!("no {k}"))
}

fn strs(v: &Value) -> Vec<String> {
    v.as_array().unwrap().iter().map(|x| x.as_str().unwrap().to_string()).collect()
}

#[test]
fn runs_on_debian13() {
    let os = std::fs::read_to_string("/etc/os-release").expect("/etc/os-release");
    let o = bko_laydump::osrel::parse(&os);
    assert!(
        o.get("ID") == Some("debian") && o.get("VERSION_ID") == Some("13"),
        "these tests are for Debian 13; this is {:?}",
        o.get("PRETTY_NAME")
    );
}

#[test]
fn ext_family_matches_blkid_and_dumpe2fs() {
    if need("mkfs.ext4").is_none() || need("dumpe2fs").is_none() {
        return;
    }
    let dir = TempDir::new("rt-ext");
    let cases: &[(&str, &[&str])] = &[
        ("ext4", &[]),
        ("ext4", &["-b", "4096", "-I", "128"]),
        ("ext4", &["-O", "^metadata_csum,^orphan_file,^64bit"]),
        ("ext4", &["-O", "casefold", "-E", "encoding=utf8"]),
        ("ext4", &["-O", "bigalloc", "-C", "65536"]),
        ("ext4", &["-O", "inline_data,quota,project,encrypt,large_dir,verity,stable_inodes,fast_commit"]),
        ("ext4", &["-O", "^has_journal"]),
        ("ext4", &["-T", "huge", "-m", "0", "-N", "4096"]),
        ("ext3", &[]),
        ("ext2", &[]),
    ];
    for (i, (kind, opts)) in cases.iter().enumerate() {
        let img = image(&dir, &format!("e{i}"), 64);
        let uuid = format!("0b5f04ed-e731-4ac6-933e-83ff2565{i:04x}");
        let label = format!("lbl{i}");
        let mut args = vec!["-q", "-F", "-t", kind, "-L", &label, "-U", &uuid];
        args.extend_from_slice(opts);
        args.push(s(&img));
        run("mke2fs", &args);
        let c = assert_matches_blkid(&img);
        assert_eq!(c.uuid.as_deref(), Some(uuid.as_str()));
        let d = run("dumpe2fs", &["-h", s(&img)]);
        let field = |name: &str| d.lines().find_map(|l| l.strip_prefix(name)).map(|v| v.trim().to_string());
        let features: Vec<String> = field("Filesystem features:").unwrap().split_whitespace().map(str::to_string).collect();
        let features = if features == ["(none)"] { vec![] } else { features };
        assert_eq!(strs(detail(&c, "features")), features, "features of case {i} {opts:?}");
        assert_eq!(detail(&c, "block_size").as_u64().unwrap().to_string(), field("Block size:").unwrap(), "case {i}");
        assert_eq!(detail(&c, "inode_size").as_u64().unwrap().to_string(), field("Inode size:").unwrap(), "case {i}");
        assert_eq!(detail(&c, "blocks").as_u64().unwrap().to_string(), field("Block count:").unwrap(), "case {i}");
        assert_eq!(detail(&c, "inodes").as_u64().unwrap().to_string(), field("Inode count:").unwrap(), "case {i}");
        assert_eq!(detail(&c, "reserved_blocks").as_u64().unwrap().to_string(), field("Reserved block count:").unwrap(), "case {i}");
        if let Some(fs) = field("Flex block group size:") {
            assert_eq!(detail(&c, "flex_bg_size").as_u64().unwrap().to_string(), fs, "case {i}");
        }
        if let Some(cs) = field("Cluster size:") {
            if c.details.0.iter().any(|(k, _)| k == "cluster_size") {
                assert_eq!(detail(&c, "cluster_size").as_u64().unwrap().to_string(), cs, "case {i}");
            }
        }
    }
}

/// xfs_db's names for our feature words.
fn xfs_db_name(ours: &str) -> &str {
    match ours {
        "sparse" => "SPARSE_INODES",
        "inobtcount" => "INOBTCNT",
        "meta_uuid" => "META_UUID",
        _ => "",
    }
}

#[test]
fn xfs_matches_blkid_and_xfs_db() {
    if need("mkfs.xfs").is_none() || need("xfs_db").is_none() {
        return;
    }
    let dir = TempDir::new("rt-xfs");
    let cases: &[&[&str]] = &[
        &[],
        &["-m", "reflink=0,rmapbt=0"],
        &["-i", "sparse=0", "-m", "finobt=0"],
        &["-b", "size=1024"],
        &["-d", "agcount=8", "-s", "size=4096"],
        &["-i", "size=1024", "-n", "size=16384"],
        &["-d", "su=64k,sw=4"],
        &["-m", "bigtime=0,inobtcount=0", "-i", "nrext64=0"],
    ];
    for (i, opts) in cases.iter().enumerate() {
        // 1 GiB (sparse): 4 KiB sectors need a 64 MiB log.
        let img = image(&dir, &format!("x{i}"), 1024);
        let uuid = format!("a0a1a2a3-a4a5-a6a7-a8a9-aaabacad{i:04x}");
        let label = format!("xl{i}");
        let muuid = format!("uuid={uuid}");
        let mut args = vec!["-q", "-f", "-L", &label, "-m", &muuid];
        args.extend_from_slice(opts);
        args.push(s(&img));
        run("mkfs.xfs", &args);
        let c = assert_matches_blkid(&img);
        let db = run("xfs_db", &["-r", "-c", "version", "-c", "sb 0", "-c", "p blocksize agcount sectsize inodesize dblocks agblocks logblocks unit width", s(&img)]);
        let val = |k: &str| db.lines().find_map(|l| l.strip_prefix(&format!("{k} = "))).map(str::trim).unwrap_or_else(|| panic!("{k} not in {db}"));
        assert_eq!(detail(&c, "block_size").as_u64().unwrap().to_string(), val("blocksize"), "case {i}");
        assert_eq!(detail(&c, "ag_count").as_u64().unwrap().to_string(), val("agcount"), "case {i}");
        assert_eq!(detail(&c, "sector_size").as_u64().unwrap().to_string(), val("sectsize"), "case {i}");
        assert_eq!(detail(&c, "inode_size").as_u64().unwrap().to_string(), val("inodesize"), "case {i}");
        assert_eq!(detail(&c, "blocks").as_u64().unwrap().to_string(), val("dblocks"), "case {i}");
        assert_eq!(detail(&c, "ag_blocks").as_u64().unwrap().to_string(), val("agblocks"), "case {i}");
        assert_eq!(detail(&c, "log_blocks").as_u64().unwrap().to_string(), val("logblocks"), "case {i}");
        assert_eq!(detail(&c, "stripe_unit").as_u64().unwrap().to_string(), val("unit"), "case {i}");
        assert_eq!(detail(&c, "stripe_width").as_u64().unwrap().to_string(), val("width"), "case {i}");
        // "versionnum [0xb4a5+0x18a] = V5,NLINK,...": the same flags both ways.
        let names: Vec<String> = db.lines().find_map(|l| l.split_once("] = ")).unwrap().1.trim().split(',').map(str::to_string).collect();
        let mut ours: Vec<String> = strs(detail(&c, "version_flags")).iter().chain(strs(detail(&c, "features")).iter()).map(|f| {
            let n = xfs_db_name(f);
            if n.is_empty() { f.to_uppercase() } else { n.to_string() }
        }).collect();
        ours.push(format!("V{}", c.version.as_deref().unwrap()));
        let mut a = ours.clone();
        a.sort();
        a.dedup();
        let mut b = names.clone();
        b.sort();
        assert_eq!(a, b, "case {i} {opts:?}: ours {ours:?}, xfs_db {names:?}");
    }
}

#[test]
fn btrfs_matches_blkid_and_dump_super() {
    if need("mkfs.btrfs").is_none() || need("btrfs").is_none() {
        return;
    }
    let dir = TempDir::new("rt-btrfs");
    let cases: &[&[&str]] = &[
        &[],
        &["--csum", "xxhash"],
        &["--csum", "sha256", "-n", "32768"],
        &["-O", "block-group-tree"],
        &["-O", "^no-holes,^free-space-tree"],
        &["-m", "single", "-d", "single"],
        &["-O", "raid1c34"],
        &["--csum", "blake2"],
    ];
    for (i, opts) in cases.iter().enumerate() {
        let img = image(&dir, &format!("b{i}"), 256);
        let uuid = format!("b0b1b2b3-b4b5-b6b7-b8b9-babbbcbd{i:04x}");
        let label = format!("bl{i}");
        let mut args = vec!["-q", "-f", "-L", &label, "-U", &uuid];
        args.extend_from_slice(opts);
        args.push(s(&img));
        run("mkfs.btrfs", &args);
        let c = assert_matches_blkid(&img);
        assert_eq!(c.uuid.as_deref(), Some(uuid.as_str()));
        let sub = blkid(&img).get("UUID_SUB").cloned();
        assert_eq!(c.details.0.iter().find(|(k, _)| k == "device").and_then(|(_, d)| d.get("uuid")).and_then(Value::as_str), sub.as_deref());
        let d = run("btrfs", &["inspect-internal", "dump-super", s(&img)]);
        // Flag names in "( A |\n B )" after each *_flags line.
        let flags = |field: &str| -> Vec<String> {
            let start = d.find(&format!("\n{field}")).unwrap_or_else(|| panic!("{field} not in dump"));
            let rest = &d[start + 1..];
            let line_end = rest.find('\n').unwrap();
            let after = &rest[line_end..];
            if !after.trim_start().starts_with('(') {
                return vec![];
            }
            let close = after.find(')').unwrap();
            after[..close].replace(['(', '|'], " ").split_whitespace().map(|w| w.to_lowercase()).collect()
        };
        assert_eq!(strs(detail(&c, "features")), flags("incompat_flags"), "case {i} {opts:?}");
        assert_eq!(strs(detail(&c, "compat_ro_features")), flags("compat_ro_flags"), "case {i} {opts:?}");
        let num = |k: &str| d.lines().find_map(|l| l.strip_prefix(k)).map(|v| v.split_whitespace().next().unwrap().to_string()).unwrap();
        assert_eq!(detail(&c, "node_size").as_u64().unwrap().to_string(), num("nodesize"), "case {i}");
        assert_eq!(detail(&c, "sector_size").as_u64().unwrap().to_string(), num("sectorsize"), "case {i}");
        assert_eq!(detail(&c, "total_bytes").as_u64().unwrap().to_string(), num("total_bytes"), "case {i}");
        let csum = d.lines().find_map(|l| l.strip_prefix("csum_type")).unwrap();
        let csum = csum.split('(').nth(1).unwrap().trim_end_matches(')');
        assert_eq!(detail(&c, "checksum").as_str().unwrap(), csum.replace("blake2", "blake2b").replace("blake2bb", "blake2b"), "case {i}");
    }
}

#[test]
fn fat_matches_blkid() {
    if need("mkfs.fat").is_none() {
        return;
    }
    let dir = TempDir::new("rt-fat");
    for (i, (bits, mib, extra)) in [("12", 4u64, vec![]), ("16", 64, vec![]), ("32", 300, vec![]), ("32", 600, vec!["-s", "8"]), ("16", 256, vec!["-S", "4096"]), ("32", 100, vec!["-S", "4096"])]
        .into_iter()
        .enumerate()
    {
        let img = image(&dir, &format!("f{i}"), mib);
        let id = format!("{:08x}", 0x1a2b_3c00u32 + i as u32);
        let label = format!("FAT{i}");
        let mut args = vec!["-F", bits, "-n", &label, "-i", &id];
        args.extend(extra.iter().copied());
        args.push(s(&img));
        run("mkfs.fat", &args);
        let c = assert_matches_blkid(&img);
        assert_eq!(c.version.as_deref(), Some(format!("FAT{bits}").as_str()), "case {i}");
        // Relabel: fatlabel rewrites the root directory entry, which wins.
        if tool("fatlabel").is_some() {
            run("fatlabel", &[s(&img), "NEWNAME"]);
            let c = assert_matches_blkid(&img);
            assert_eq!(c.label.as_deref(), Some("NEWNAME"));
        }
    }
    // No label at all.
    let img = image(&dir, "nolabel", 64);
    run("mkfs.fat", &["-F", "32", s(&img)]);
    assert_eq!(assert_matches_blkid(&img).label, None);
}

#[test]
fn exfat_ntfs_jfs_iso_match_blkid() {
    let dir = TempDir::new("rt-misc");
    if need("mkfs.exfat").is_some() {
        let img = image(&dir, "exfat", 64);
        run("mkfs.exfat", &["-L", "EX", s(&img)]);
        let ours = probe_file(&img);
        assert_eq!(ours[0].kind, "exfat");
        assert_eq!(ours[0].uuid.as_deref(), blkid(&img).get("UUID").map(String::as_str));
    }
    if need("mkntfs").is_some() {
        let img = image(&dir, "ntfs", 64);
        run("mkntfs", &["-q", "-Q", "-F", "-L", "NT", s(&img)]);
        let ours = probe_file(&img);
        assert_eq!(ours[0].kind, "ntfs");
        assert_eq!(ours[0].uuid.as_deref(), blkid(&img).get("UUID").map(String::as_str));
    }
    if need("mkfs.jfs").is_some() {
        let img = image(&dir, "jfs", 64);
        run("mkfs.jfs", &["-q", "-L", "jfslbl", s(&img)]);
        assert_matches_blkid(&img);
    }
    if need("xorriso").is_some() {
        let src = dir.path().join("isosrc");
        std::fs::create_dir_all(&src).unwrap();
        std::fs::write(src.join("README"), "hello").unwrap();
        let img = dir.path().join("x.iso");
        let out = Command::new(tool("xorriso").unwrap())
            .args(["-as", "mkisofs", "-quiet", "-V", "BKO_TEST", "-o", s(&img), s(&src)])
            .output()
            .unwrap();
        assert!(out.status.success(), "{}", String::from_utf8_lossy(&out.stderr));
        assert_matches_blkid(&img);
    }
}

#[test]
fn swap_matches_blkid() {
    if need("mkswap").is_none() {
        return;
    }
    let dir = TempDir::new("rt-swap");
    for (i, extra) in [vec![], vec!["--pagesize", "65536"], vec!["--pagesize", "16384"]].into_iter().enumerate() {
        let img = image(&dir, &format!("s{i}"), 32);
        let uuid = format!("c0c1c2c3-c4c5-c6c7-c8c9-cacbcccd{i:04x}");
        let mut args = vec!["-q", "-L", "swp", "-U", &uuid];
        args.extend(extra.iter().copied());
        args.push(s(&img));
        run("mkswap", &args);
        let c = assert_matches_blkid(&img);
        let ps: u64 = extra.get(1).map(|p| p.parse().unwrap()).unwrap_or(4096);
        assert_eq!(detail(&c, "page_size").as_u64(), Some(ps));
        assert_eq!(detail(&c, "size_bytes").as_u64(), Some(32 << 20), "case {i}");
    }
}

#[test]
fn luks_matches_blkid_and_dump() {
    if need("cryptsetup").is_none() {
        return;
    }
    let dir = TempDir::new("rt-luks");
    let key = dir.path().join("key");
    std::fs::write(&key, "not a secret").unwrap();
    let cases: &[&[&str]] = &[
        &["--type", "luks2", "--pbkdf", "pbkdf2", "--pbkdf-force-iterations", "1000", "--label", "cryptroot"],
        &["--type", "luks2", "--pbkdf", "argon2id", "--pbkdf-memory", "32", "--iter-time", "10", "--sector-size", "4096", "--subsystem", "bko"],
        &["--type", "luks2", "--pbkdf", "pbkdf2", "--pbkdf-force-iterations", "1000", "--cipher", "aes-cbc-essiv:sha256", "--key-size", "256", "--luks2-metadata-size", "64k", "--luks2-keyslots-size", "1m"],
        &["--type", "luks1", "--pbkdf-force-iterations", "1000"],
    ];
    for (i, opts) in cases.iter().enumerate() {
        let img = image(&dir, &format!("l{i}"), 32);
        let uuid = format!("6c3e5f0e-6b0a-4d6c-9a2e-8f7e1d2c{i:04x}");
        let mut args = vec!["luksFormat", "-q", "--key-file", s(&key), "--uuid", &uuid];
        args.extend_from_slice(opts);
        args.push(s(&img));
        run("cryptsetup", &args);
        let c = assert_matches_blkid(&img);
        assert_eq!(c.uuid.as_deref(), Some(uuid.as_str()));
        if c.version.as_deref() == Some("2") {
            let meta = json::parse(&run("cryptsetup", &["luksDump", "--dump-json-metadata", s(&img)])).unwrap();
            let d = c.details.clone().build();
            assert_eq!(d.at(&["segments", "0", "encryption"]), meta.at(&["segments", "0", "encryption"]), "case {i}");
            assert_eq!(d.at(&["segments", "0", "offset"]), meta.at(&["segments", "0", "offset"]), "case {i}");
            assert_eq!(d.at(&["segments", "0", "sector_size"]).and_then(Value::as_u64), meta.at(&["segments", "0", "sector_size"]).and_then(Value::as_u64), "case {i}");
            assert_eq!(d.at(&["keyslots", "0", "kdf"]), meta.at(&["keyslots", "0", "kdf", "type"]), "case {i}");
            assert_eq!(d.get("json_size"), meta.at(&["config", "json_size"]), "case {i}");
            assert_eq!(d.get("keyslots_size"), meta.at(&["config", "keyslots_size"]), "case {i}");
            assert_eq!(d.get("secondary_header"), Some(&Value::Bool(true)));
            // The report never carries salts or digests.
            assert!(!d.compact().contains("salt"), "case {i}");
        } else {
            let dump = run("cryptsetup", &["luksDump", s(&img)]);
            let field = |k: &str| dump.lines().find_map(|l| l.strip_prefix(k)).map(|v| v.trim().to_string()).unwrap();
            let d = c.details.clone().build();
            assert_eq!(d.get("cipher").and_then(Value::as_str).unwrap(), format!("{}-{}", field("Cipher name:"), field("Cipher mode:")));
            assert_eq!(d.get("hash").and_then(Value::as_str).unwrap(), field("Hash spec:"));
            assert_eq!(d.get("data_offset_bytes").and_then(Value::as_u64).unwrap(), field("Payload offset:").parse::<u64>().unwrap() * 512);
            assert_eq!(d.get("keyslots_active").and_then(Value::as_u64), Some(1));
        }
    }
}

/// sfdisk --json for a disk image, parsed.
fn sfdisk_json(p: &Path) -> Value {
    json::parse(&run("sfdisk", &["--json", s(p)])).unwrap()
}

#[test]
fn gpt_matches_sfdisk() {
    if need("sfdisk").is_none() {
        return;
    }
    let dir = TempDir::new("rt-gpt");
    let img = image(&dir, "gpt", 256);
    run_stdin(
        "sfdisk",
        &["-q", s(&img)],
        "label: gpt\nlabel-id: 6F1C2C3D-0000-4000-8000-00000000AAAA\nfirst-lba: 34\n\
         start=34, size=2014, type=21686148-6449-6E6F-744E-656564454649, name=\"bios grub\"\n\
         size=100M, type=uefi, name=\"EFI system partition\", attrs=\"RequiredPartition\"\n\
         size=50M, type=linux, uuid=11112222-3333-4444-5555-666677778888, attrs=\"LegacyBIOSBootable GUID:60,63\"\n\
         size=20M, type=swap\n\
         type=lvm, name=\"Ünïcødé ☃\"\n",
    );
    let theirs = sfdisk_json(&img);
    let pt = theirs.get("partitiontable").unwrap();
    let t = parttable::read_table(&FileSource::open(&img).unwrap(), 512).unwrap();
    let Table::Gpt { gpt, .. } = &t else { panic!("not gpt") };
    assert!(gpt.primary_ok);
    assert_eq!(gpt.backup, "ok");
    assert_eq!(gpt.disk_guid, pt.get("id").unwrap().as_str().unwrap().to_lowercase());
    assert_eq!(gpt.first_usable_lba, pt.get("firstlba").unwrap().as_u64().unwrap());
    assert_eq!(gpt.last_usable_lba, pt.get("lastlba").unwrap().as_u64().unwrap());
    let parts = pt.get("partitions").unwrap().as_array().unwrap();
    assert_eq!(gpt.parts.len(), parts.len());
    for (ours, p) in gpt.parts.iter().zip(parts) {
        assert_eq!(ours.first_lba, p.get("start").unwrap().as_u64().unwrap());
        assert_eq!(ours.last_lba - ours.first_lba + 1, p.get("size").unwrap().as_u64().unwrap());
        assert_eq!(ours.type_guid, p.get("type").unwrap().as_str().unwrap().to_lowercase());
        assert_eq!(ours.uuid, p.get("uuid").unwrap().as_str().unwrap().to_lowercase());
        assert_eq!(ours.name, p.get("name").and_then(Value::as_str).unwrap_or(""));
    }
    assert_eq!(gpt.parts[1].attributes, 1);
    assert_eq!(gpt.parts[2].attributes, 4 | (1 << 60) | (1 << 63));
    assert_eq!(gpt.parts[4].name, "Ünïcødé ☃");
    // blkid agrees on the table's id.
    assert_eq!(blkid(&img).get("PTUUID").map(String::as_str), Some(gpt.disk_guid.as_str()));
    assert_eq!(blkid(&img).get("PTTYPE").map(String::as_str), Some("gpt"));
}

#[test]
fn gpt_4k_sectors_match_sfdisk() {
    if need("sfdisk").is_none() {
        return;
    }
    let dir = TempDir::new("rt-gpt4k");
    let img = image(&dir, "gpt4k", 64);
    run_stdin("sfdisk", &["-q", "--sector-size", "4096", s(&img)], "label: gpt\nsize=8M, type=uefi\ntype=linux\n");
    let out = run("sfdisk", &["--json", "--sector-size", "4096", s(&img)]);
    let theirs = json::parse(&out).unwrap();
    let Some(Table::Gpt { gpt, .. }) = parttable::read_table(&FileSource::open(&img).unwrap(), 4096) else { panic!("not gpt") };
    let parts = theirs.at(&["partitiontable", "partitions"]).unwrap().as_array().unwrap();
    for (ours, p) in gpt.parts.iter().zip(parts) {
        assert_eq!(ours.first_lba, p.get("start").unwrap().as_u64().unwrap());
    }
    assert_eq!(gpt.backup, "ok");
    assert_eq!(gpt.backup_lba, (64 << 20) / 4096 - 1);
}

#[test]
fn dos_with_logicals_matches_sfdisk() {
    if need("sfdisk").is_none() {
        return;
    }
    let dir = TempDir::new("rt-dos");
    let img = image(&dir, "dos", 256);
    // What Debian's installer makes on a BIOS machine: /boot, then an
    // extended partition with swap and / (and a few more for good measure).
    run_stdin(
        "sfdisk",
        &["-q", s(&img)],
        "label: dos\nlabel-id: 0x5eed0001\nsize=50M, type=83, bootable\nsize=10M, type=ef\ntype=5\nsize=20M, type=82\nsize=30M, type=8e\nsize=10M, type=fd\ntype=83\n",
    );
    let theirs = sfdisk_json(&img);
    let pt = theirs.get("partitiontable").unwrap();
    let Some(Table::Mbr(m)) = parttable::read_table(&FileSource::open(&img).unwrap(), 512) else { panic!("not dos") };
    assert!(m.problems.is_empty(), "{:?}", m.problems);
    assert_eq!(format!("0x{:08x}", m.disk_signature), pt.get("id").unwrap().as_str().unwrap());
    let parts = pt.get("partitions").unwrap().as_array().unwrap();
    assert_eq!(m.parts.len(), parts.len());
    for (ours, p) in m.parts.iter().zip(parts) {
        let node = p.get("node").unwrap().as_str().unwrap();
        assert!(node.ends_with(&ours.number.to_string()), "{node} vs {}", ours.number);
        assert_eq!(ours.start, p.get("start").unwrap().as_u64().unwrap());
        assert_eq!(ours.sectors, p.get("size").unwrap().as_u64().unwrap());
        assert_eq!(format!("{:x}", ours.ptype), p.get("type").unwrap().as_str().unwrap());
        assert_eq!(ours.boot_flag == 0x80, p.get("bootable") == Some(&Value::Bool(true)));
    }
    assert_eq!(blkid(&img).get("PTUUID").map(String::as_str), Some("5eed0001"));
}

#[test]
fn hybrid_mbr_from_sgdisk() {
    if need("sgdisk").is_none() {
        return;
    }
    let dir = TempDir::new("rt-hybrid");
    let img = image(&dir, "hy", 64);
    // (sgdisk -q on an image file exits 0 without writing anything.)
    run("sgdisk", &["-o", "-n", "1:2048:+10M", "-t", "1:ef00", "-n", "2:0:0", "-t", "2:8300", s(&img)]);
    run("sgdisk", &["-h", "1", s(&img)]);
    let t = parttable::read_table(&FileSource::open(&img).unwrap(), 512).unwrap();
    let h = t.header_json(512, (64 << 20) / 512);
    assert_eq!(h.at(&["protective_mbr", "hybrid"]), Some(&Value::Bool(true)));
    let Table::Gpt { gpt, .. } = &t else { panic!() };
    assert_eq!(gpt.parts.len(), 2);
}

#[test]
fn partitions_with_filesystems_end_to_end() {
    // A partitioned image whose partitions hold real filesystems: probing
    // each partition's slice agrees with blkid on the same slice.
    if need("sfdisk").is_none() || need("mkfs.ext4").is_none() || need("mkfs.fat").is_none() || need("mkswap").is_none() {
        return;
    }
    let dir = TempDir::new("rt-e2e");
    let disk = image(&dir, "disk", 128);
    run_stdin("sfdisk", &["-q", s(&disk)], "label: gpt\nsize=40M, type=uefi\nsize=16M, type=swap\ntype=linux\n");
    let Some(Table::Gpt { gpt, .. }) = parttable::read_table(&FileSource::open(&disk).unwrap(), 512) else { panic!() };
    let mut whole = std::fs::read(&disk).unwrap();
    for (i, p) in gpt.parts.iter().enumerate() {
        let part = dir.path().join(format!("p{i}"));
        let len = (p.last_lba - p.first_lba + 1) * 512;
        std::fs::File::create(&part).unwrap().set_len(len).unwrap();
        match i {
            0 => run("mkfs.fat", &["-F", "32", "-n", "EFI", s(&part)]),
            1 => run("mkswap", &["-q", s(&part)]),
            _ => run("mkfs.ext4", &["-q", "-F", "-L", "root", s(&part)]),
        };
        let data = std::fs::read(&part).unwrap();
        let o = (p.first_lba * 512) as usize;
        whole[o..o + data.len()].copy_from_slice(&data);
        assert_matches_blkid(&part);
    }
    std::fs::write(&disk, &whole).unwrap();
    // The whole disk holds a table, not a filesystem, for both of us.
    let Some(Table::Gpt { gpt: again, .. }) = parttable::read_table(&FileSource::open(&disk).unwrap(), 512) else { panic!() };
    assert_eq!(again.parts.len(), 3);
    assert_eq!(blkid(&disk).get("TYPE"), None);
}
