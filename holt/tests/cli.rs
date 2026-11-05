//! The binary: arguments, exit codes, the Debian 13 gate, and a dump of the
//! machine the tests run on checked against lsblk.  `load` has its own tests
//! (tests/load_cli.rs).

use bko_holt::json::{self, Value};
use bko_holt::testutil::Tree;
use std::process::{Command, Output};

fn bin(args: &[&str], env: &[(&str, &str)]) -> Output {
    // BKO_HOLT_BIN: the binary, when these tests run outside the build
    // tree (copied into a test guest).
    let exe = std::env::var("BKO_HOLT_BIN").unwrap_or_else(|_| env!("CARGO_BIN_EXE_bko-holt").to_string());
    let mut c = Command::new(exe);
    c.args(args);
    for (k, v) in env {
        c.env(k, v);
    }
    c.output().unwrap()
}

fn stdout(o: &Output) -> String {
    String::from_utf8(o.stdout.clone()).unwrap()
}

fn stderr(o: &Output) -> String {
    String::from_utf8(o.stderr.clone()).unwrap()
}

#[test]
fn help_and_version() {
    for a in ["-h", "--help"] {
        let o = bin(&[a], &[]);
        assert_eq!(o.status.code(), Some(0));
        assert!(stdout(&o).starts_with("Usage: bko-holt dump"));
        let o = bin(&["dump", a], &[]);
        assert_eq!(o.status.code(), Some(0));
        assert!(stdout(&o).starts_with("Usage: bko-holt dump [--compact]"));
        let o = bin(&["load", a], &[]);
        assert_eq!(o.status.code(), Some(0));
        assert!(stdout(&o).starts_with("Usage: bko-holt load LAYOUT.json"));
    }
    for a in ["-V", "--version"] {
        let o = bin(&[a], &[]);
        assert_eq!(o.status.code(), Some(0));
        assert_eq!(stdout(&o), format!("bko-holt {}\n", env!("CARGO_PKG_VERSION")));
    }
}

#[test]
fn bad_arguments_exit_2() {
    let o = bin(&[], &[]);
    assert_eq!(o.status.code(), Some(2));
    assert!(stderr(&o).contains("say dump or load"));
    let o = bin(&["--frobnicate"], &[]);
    assert_eq!(o.status.code(), Some(2));
    assert!(stderr(&o).contains("unknown argument \"--frobnicate\""));
    assert!(stdout(&o).is_empty());
    let o = bin(&["dump", "--frobnicate"], &[]);
    assert_eq!(o.status.code(), Some(2));
    assert!(stderr(&o).contains("unknown argument \"--frobnicate\""));
    let o = bin(&["dump", "--sysroot"], &[]);
    assert_eq!(o.status.code(), Some(2));
    let o = bin(&["dump", "--sysroot", "/no/such/dir"], &[]);
    assert_eq!(o.status.code(), Some(2));
    assert!(stderr(&o).contains("not a directory"));
}

#[test]
fn refuses_other_systems_with_exit_3() {
    let t = Tree::new("cli-bookworm");
    t.file("/etc/os-release", "PRETTY_NAME=\"Debian GNU/Linux 12 (bookworm)\"\nID=debian\nVERSION_ID=\"12\"\n");
    let o = bin(&["dump", "--sysroot", t.root().to_str().unwrap()], &[]);
    assert_eq!(o.status.code(), Some(3));
    assert!(stderr(&o).contains("Debian GNU/Linux 12 (bookworm)"), "{}", stderr(&o));
    assert!(stdout(&o).is_empty());
    let empty = Tree::new("cli-empty");
    let o = bin(&["dump", "--sysroot", empty.root().to_str().unwrap()], &[]);
    assert_eq!(o.status.code(), Some(3));
}

#[test]
fn sysroot_dump_is_json() {
    let t = Tree::new("cli-sysroot");
    t.debian13().disk("vda", "254:0", &vec![0u8; 1 << 20], 512);
    let root = t.root().to_str().unwrap();
    let o = bin(&["dump", "--sysroot", root], &[("SOURCE_DATE_EPOCH", "1793828645")]);
    assert_eq!(o.status.code(), Some(0), "{}", stderr(&o));
    let text = stdout(&o);
    assert!(text.starts_with("{\n  \"format\": \"bko-holt\",\n  \"schema\": 1,\n"));
    let v = json::parse(&text).unwrap();
    assert_eq!(v.get("generated").and_then(Value::as_str), Some("2026-11-04T21:44:05Z"));
    assert_eq!(v.at(&["tool", "name"]).and_then(Value::as_str), Some("bko-holt"));
    assert_eq!(v.at(&["storage", "disks", "0", "name"]).and_then(Value::as_str), Some("vda"));
    let c = bin(&["dump", "--compact", "--sysroot", root], &[("SOURCE_DATE_EPOCH", "1793828645")]);
    let ct = stdout(&c);
    assert_eq!(ct.lines().count(), 1);
    assert_eq!(json::parse(&ct).unwrap().compact(), v.compact());
    // The same input gives the same output.
    let again = bin(&["dump", "--sysroot", root], &[("SOURCE_DATE_EPOCH", "1793828645")]);
    assert_eq!(stdout(&again), text);
}

/// Dumps the machine the tests run on (a Debian 13 host or guest) and checks
/// it against lsblk.  As root this compares our own superblock reading with
/// udev/blkid's; as another user, the udev fallback.
#[test]
fn live_host_matches_lsblk() {
    let o = bin(&["dump"], &[]);
    assert_eq!(o.status.code(), Some(0), "{}", stderr(&o));
    let v = json::parse(&stdout(&o)).unwrap();
    assert_eq!(v.at(&["os", "id"]).and_then(Value::as_str), Some("debian"));
    let ls = Command::new("lsblk").args(["--json", "-b", "-o", "NAME,KNAME,SIZE,FSTYPE,UUID,LABEL,PTTYPE,PTUUID,TYPE,PARTUUID"]).output().unwrap();
    assert!(ls.status.success());
    let ls = json::parse(&String::from_utf8(ls.stdout).unwrap()).unwrap();
    // Flatten lsblk's tree.
    fn walk(v: &Value, out: &mut Vec<Value>) {
        for d in v.as_array().into_iter().flatten() {
            out.push(d.clone());
            if let Some(c) = d.get("children") {
                walk(c, out);
            }
        }
    }
    let mut lsblk = Vec::new();
    walk(ls.get("blockdevices").unwrap(), &mut lsblk);
    // Ours, flattened too.
    let mut ours: Vec<Value> = Vec::new();
    for sec in ["disks", "md_arrays", "dm_devices", "loop_devices", "other_devices"] {
        for d in v.at(&["storage", sec]).and_then(Value::as_array).into_iter().flatten() {
            ours.push(d.clone());
            for p in d.get("partitions").and_then(Value::as_array).into_iter().flatten() {
                ours.push(p.clone());
            }
        }
    }
    let privileged = v.get("privileged") == Some(&Value::Bool(true));
    let mut checked = 0;
    for l in &lsblk {
        let kname = l.get("kname").and_then(Value::as_str).unwrap();
        let size = l.get("size").and_then(Value::as_u64).unwrap_or(0);
        if size == 0 || l.get("type").and_then(Value::as_str) == Some("rom") {
            continue;
        }
        let d = ours.iter().find(|d| d.get("name").and_then(Value::as_str) == Some(kname)).unwrap_or_else(|| panic!("{kname} missing from the dump"));
        let our_size = d.get("size_bytes").and_then(Value::as_u64);
        assert_eq!(our_size, Some(size), "size of {kname}");
        let fstype = l.get("fstype").and_then(Value::as_str);
        let our_type = d.at(&["content", "type"]).and_then(Value::as_str);
        // blkid's known false positive: a partitioned disk whose last
        // partition is a ZFS vdev has that vdev's end labels at its own
        // end, and blkid calls the whole disk a zfs_member.  We don't.
        let blkid_zfs_quirk = privileged && fstype == Some("zfs_member") && l.get("pttype").is_some_and(|p| !p.is_null()) && l.get("type").and_then(Value::as_str) == Some("disk");
        if blkid_zfs_quirk {
            assert_eq!(our_type, None, "{kname}: whole disk with a table");
        } else if fstype.is_some() {
            assert_eq!(our_type, fstype, "content type of {kname} (privileged: {privileged})");
            let uuid = l.get("uuid").and_then(Value::as_str);
            let our_uuid = d.at(&["content", "uuid"]).and_then(Value::as_str);
            assert_eq!(our_uuid.map(str::to_lowercase), uuid.map(str::to_lowercase), "uuid of {kname}");
            assert_eq!(d.at(&["content", "label"]).and_then(Value::as_str), l.get("label").and_then(Value::as_str), "label of {kname}");
        }
        if let Some(pt) = l.get("pttype").and_then(Value::as_str) {
            if l.get("type").and_then(Value::as_str) != Some("part") {
                assert_eq!(d.at(&["partition_table", "type"]).and_then(Value::as_str), Some(pt), "table of {kname}");
            }
        }
        if let (Some(pu), Some("part")) = (l.get("partuuid").and_then(Value::as_str), l.get("type").and_then(Value::as_str)) {
            assert_eq!(d.get("uuid").and_then(Value::as_str).map(str::to_lowercase), Some(pu.to_lowercase()), "partuuid of {kname}");
        }
        checked += 1;
    }
    assert!(checked > 0, "lsblk listed nothing to check");
    eprintln!("checked {checked} devices against lsblk (privileged: {privileged})");
}
