//! `bko-holt load` as a program: arguments, exit codes, and a plan and a
//! status on a fake rescue system (--sysroot), which runs no commands.

use bko_holt::testutil::Tree;
use std::process::{Command, Output};

fn bin(args: &[&str]) -> Output {
    let exe = std::env::var("BKO_HOLT_BIN").unwrap_or_else(|_| env!("CARGO_BIN_EXE_bko-holt").to_string());
    Command::new(exe).arg("load").args(args).output().unwrap()
}

fn out(o: &Output) -> String {
    String::from_utf8_lossy(&o.stdout).into_owned()
}

fn err(o: &Output) -> String {
    String::from_utf8_lossy(&o.stderr).into_owned()
}

/// A Debian 13 dump with one BIOS + MBR disk holding a single ext4.
const LAYOUT: &str = r#"{
  "format": "bko-holt", "schema": 1, "privileged": true,
  "host": {"hostname": "t"}, "os": {"id": "debian", "version_id": "13"},
  "storage": {
    "disks": [{"name": "vda", "kind": "disk", "size_bytes": 1073741824, "logical_sector_size": 512,
      "partition_table": {"type": "dos", "disk_signature": "0x00c0ffee"},
      "partitions": [{"name": "vda1", "number": 1, "start_lba": 2048, "sectors": 2095104, "type": "0x83", "bootable": true,
        "content": {"type": "ext4", "uuid": "11111111-2222-4333-8444-555555555555", "features": ["has_journal", "extent"]}}],
      "content": null, "boot_code": {"kind": "grub"}}],
    "fstab": [{"spec": "UUID=11111111-2222-4333-8444-555555555555", "file": "/", "type": "ext4", "options": "errors=remount-ro", "resolves_to": "vda1"}]
  },
  "boot": {"loader": {"name": "grub", "firmware": "bios"}}
}"#;

fn setup() -> (Tree, String) {
    let t = Tree::new("load-cli");
    t.file("/layout.json", LAYOUT);
    // A rescue system with a blank 1 GiB vdb.
    t.disk("vdb", "253:16", &[], 512).file("/sys/block/vdb/size", "2097152\n");
    t.file("/proc/self/mountinfo", "").file("/proc/swaps", "Filename\tType\tSize\tUsed\tPriority\n");
    let layout = t.p("/layout.json").to_string_lossy().into_owned();
    (t, layout)
}

#[test]
fn arguments() {
    let o = bin(&[]);
    assert_eq!(o.status.code(), Some(2));
    assert!(err(&o).contains("which layout?"));
    let o = bin(&["/nonexistent/layout.json"]);
    assert_eq!(o.status.code(), Some(2));
    let o = bin(&["x.json", "--through", "boot"]);
    assert_eq!(o.status.code(), Some(2));
    assert!(err(&o).contains("restore needs --repo"));
}

#[test]
fn not_a_layout_exits_3() {
    let (t, layout) = setup();
    std::fs::write(&layout, "{\"format\": \"something-else\"}").unwrap();
    let o = bin(&[&layout, "--sysroot", t.root().to_str().unwrap()]);
    assert_eq!(o.status.code(), Some(3));
    assert!(err(&o).contains("not a bko-holt dump"));
}

#[test]
fn plan_and_status() {
    let (t, layout) = setup();
    let root = t.root().to_str().unwrap();
    // Unprivileged (euid isn't 0 in most test runs) or not, a plan with no
    // tools installed in the fake tree has problems: exit 4.
    let o = bin(&[&layout, "--sysroot", root, "--map", "vda=vdb"]);
    assert_eq!(o.status.code(), Some(4), "{}{}", out(&o), err(&o));
    assert!(out(&o).contains("#   vda (1 GiB) -> /dev/vdb"), "{}", out(&o));
    assert!(out(&o).contains("isn't installed here"));
    assert!(out(&o).contains("# ---- partition ----"));
    let o = bin(&[&layout, "--sysroot", root, "--map", "vda=vdb", "--only", "status"]);
    assert_eq!(o.status.code(), Some(0), "{}{}", out(&o), err(&o));
    assert!(out(&o).contains("next: partition"), "{}", out(&o));
}

#[test]
fn writing_stages_refuse_until_built() {
    let (t, layout) = setup();
    let o = bin(&[&layout, "--sysroot", t.root().to_str().unwrap(), "--map", "vda=vdb", "--through", "mount", "--commit"]);
    assert_eq!(o.status.code(), Some(1));
    assert!(err(&o).contains("aren't built yet"));
}
