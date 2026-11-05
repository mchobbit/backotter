//! Root-only end-to-end tests that build real storage stacks with the
//! system's own tools and check the dump against those tools.  They change
//! the running system (loop devices, md arrays, dm devices, a ZFS pool), so
//! they're #[ignore]d: run them as root in a throwaway Debian 13 guest, e.g.
//! a `vm-run --snapshot` of testvm's debian13 or debian13-zfs:
//!
//!     BKO_HOLT_BIN=/root/bko-holt ./root_stack-* --ignored --test-threads 1
//!
//! Needs mdadm, cryptsetup, lvm2, xfsprogs (and zfsutils-linux for the ZFS
//! test).  Everything they create is torn down again, also on failure.

use bko_holt::json::{self, Value};
use std::path::PathBuf;
use std::process::Command;

const PATH: &str = "/usr/sbin:/usr/bin:/sbin:/bin";

fn sh(script: &str) -> String {
    let out = Command::new("/bin/sh").arg("-ec").arg(script).env("PATH", PATH).env("LC_ALL", "C").output().unwrap();
    assert!(out.status.success(), "script failed:\n{script}\n{}", String::from_utf8_lossy(&out.stderr));
    String::from_utf8_lossy(&out.stdout).trim().to_string()
}

fn sh_quiet(script: &str) {
    let _ = Command::new("/bin/sh").arg("-c").arg(script).env("PATH", PATH).output();
}

fn dump() -> Value {
    let exe = std::env::var("BKO_HOLT_BIN").unwrap_or_else(|_| env!("CARGO_BIN_EXE_bko-holt").to_string());
    let out = Command::new(exe).arg("dump").output().unwrap();
    assert!(out.status.success(), "{}", String::from_utf8_lossy(&out.stderr));
    json::parse(&String::from_utf8(out.stdout).unwrap()).unwrap()
}

fn as_root() -> bool {
    if bko_holt::sys::euid() != 0 {
        eprintln!("SKIP: needs root");
        return false;
    }
    true
}

/// Every device in the dump, flattened, by kernel name.
fn device<'a>(v: &'a Value, name: &str) -> &'a Value {
    for sec in ["disks", "md_arrays", "dm_devices", "loop_devices", "other_devices"] {
        for d in v.at(&["storage", sec]).and_then(Value::as_array).into_iter().flatten() {
            if d.get("name").and_then(Value::as_str) == Some(name) {
                return d;
            }
            for p in d.get("partitions").and_then(Value::as_array).into_iter().flatten() {
                if p.get("name").and_then(Value::as_str) == Some(name) {
                    return p;
                }
            }
        }
    }
    panic!("{name} not in the dump")
}

fn kname(path: &str) -> String {
    std::fs::canonicalize(path).unwrap().file_name().unwrap().to_string_lossy().into_owned()
}

fn st<'a>(v: &'a Value, p: &[&str]) -> &'a str {
    v.at(p).and_then(Value::as_str).unwrap_or_else(|| panic!("no string at {p:?} in {}", v.compact()))
}

struct Cleanup(String);
impl Drop for Cleanup {
    fn drop(&mut self) {
        sh_quiet(&self.0);
    }
}

#[test]
#[ignore]
fn md_luks_lvm_stack() {
    if !as_root() {
        return;
    }
    let dir = PathBuf::from(format!("/var/tmp/bko-holt-stack-{}", std::process::id()));
    let d = dir.display();
    let _cleanup = Cleanup(format!(
        "umount /mnt/bkold-ext4 /mnt/bkold-xfs; vgchange -an bkold-vg; cryptsetup close bkoldcrypt; \
         mdadm --stop /dev/md/bkold; for l in $(losetup -j {d}/a -O NAME -n) $(losetup -j {d}/b -O NAME -n); do losetup -d $l; done; \
         rm -rf {d} /mnt/bkold-ext4 /mnt/bkold-xfs"
    ));
    let (la, lb) = {
        let s = sh(&format!(
            "mkdir -p {d}; truncate -s 1G {d}/a {d}/b; \
             la=$(losetup --find --show --partscan {d}/a); lb=$(losetup --find --show --partscan {d}/b); \
             for l in $la $lb; do sfdisk -q $l >/dev/null <<EOF\nlabel: gpt\nsize=64M, type=uefi, name=esp\ntype=raid, name=bkoraid\nEOF\ndone; \
             udevadm settle; echo $la $lb"
        ));
        let mut w = s.split_whitespace().map(str::to_string);
        (w.next().unwrap(), w.next().unwrap())
    };
    sh(&format!(
        "mdadm --create /dev/md/bkold --run --quiet --level=1 --raid-devices=2 --metadata=1.2 --homehost=bkotest {la}p2 {lb}p2; \
         udevadm settle; printf testkey > {d}/key; \
         cryptsetup luksFormat -q --type luks2 --pbkdf pbkdf2 --pbkdf-force-iterations 1000 --label bkoldcrypt --sector-size 4096 --key-file {d}/key /dev/md/bkold; \
         cryptsetup open --key-file {d}/key /dev/md/bkold bkoldcrypt; \
         pvcreate -q /dev/mapper/bkoldcrypt; vgcreate -q -s 8M bkold-vg /dev/mapper/bkoldcrypt; \
         lvcreate -q -y -n ext-4 -L 200M bkold-vg; lvcreate -q -y -n xfs -L 400M -i 1 bkold-vg; \
         mkfs.fat -F 32 -n ESPA {la}p1 >/dev/null; \
         mkfs.ext4 -q -L bkoldext /dev/bkold-vg/ext-4; mkfs.xfs -q -L bkoldxfs /dev/bkold-vg/xfs; \
         mkdir -p /mnt/bkold-ext4 /mnt/bkold-xfs; mount /dev/bkold-vg/ext-4 /mnt/bkold-ext4; mount LABEL=bkoldxfs /mnt/bkold-xfs; udevadm settle"
    ));
    let v = dump();
    let md = kname("/dev/md/bkold");
    let crypt = kname("/dev/mapper/bkoldcrypt");
    let ext = kname("/dev/bkold-vg/ext-4");
    let xfs = kname("/dev/bkold-vg/xfs");
    let la_name = la.trim_start_matches("/dev/");
    // Partition table and the ESP on the loop device.
    let loopdev = device(&v, la_name);
    assert_eq!(st(loopdev, &["partition_table", "type"]), "gpt");
    let p1 = device(&v, &format!("{la_name}p1"));
    assert_eq!(st(p1, &["partlabel"]), "esp");
    assert_eq!(st(p1, &["content", "type"]), "vfat");
    assert_eq!(st(p1, &["content", "label"]), "ESPA");
    // md: superblock and sysfs agree with mdadm.
    let detail = sh("mdadm --detail --export /dev/md/bkold");
    let get = |k: &str| detail.lines().find_map(|l| l.strip_prefix(&format!("{k}="))).unwrap().to_string();
    let m = device(&v, &md);
    assert_eq!(st(m, &["md", "level"]), "raid1");
    assert_eq!(st(m, &["md", "uuid"]), get("MD_UUID"));
    let member = device(&v, &format!("{la_name}p2"));
    assert_eq!(st(member, &["content", "type"]), "linux_raid_member");
    assert_eq!(st(member, &["content", "mdadm_uuid"]), get("MD_UUID"));
    assert_eq!(st(member, &["content", "label"]), "bkotest:bkold");
    assert_eq!(st(member, &["content", "version"]), "1.2");
    // LUKS2 on md.
    assert_eq!(st(m, &["content", "type"]), "crypto_LUKS");
    assert_eq!(st(m, &["content", "label"]), "bkoldcrypt");
    assert_eq!(m.at(&["content", "segments", "0", "sector_size"]).and_then(Value::as_u64), Some(4096));
    let luks_uuid = sh("cryptsetup luksUUID /dev/md/bkold");
    assert_eq!(st(m, &["content", "uuid"]), luks_uuid);
    let c = device(&v, &crypt);
    assert_eq!(st(c, &["dm", "target"]), "crypt");
    assert_eq!(st(c, &["dm", "luks_uuid"]), luks_uuid);
    // LVM, checked against lvs/vgs.
    assert_eq!(st(c, &["content", "type"]), "LVM2_member");
    let vg = v.at(&["storage", "lvm", "volume_groups"]).and_then(Value::as_array).unwrap().iter().find(|g| g.get("name").and_then(Value::as_str) == Some("bkold-vg")).unwrap();
    assert_eq!(st(vg, &["id"]), sh("vgs --noheadings -o vg_uuid bkold-vg"));
    assert_eq!(vg.get("extent_size_bytes").and_then(Value::as_u64), Some(8 << 20));
    assert_eq!(st(vg, &["physical_volumes", "0", "device"]), crypt);
    for lv in vg.get("logical_volumes").and_then(Value::as_array).unwrap() {
        let name = st(lv, &["name"]);
        let size: u64 = sh(&format!("lvs --noheadings --units b --nosuffix -o lv_size bkold-vg/{name}")).parse().unwrap();
        assert_eq!(lv.get("size_bytes").and_then(Value::as_u64), Some(size), "{name}");
        assert_eq!(st(lv, &["id"]), sh(&format!("lvs --noheadings -o lv_uuid bkold-vg/{name}")));
        let dev = st(lv, &["active_device"]);
        assert_eq!(dev, if name == "xfs" { xfs.as_str() } else { ext.as_str() });
    }
    let e = device(&v, &ext);
    assert_eq!(st(e, &["dm", "vg"]), "bkold-vg");
    assert_eq!(st(e, &["dm", "lv"]), "ext-4");
    assert_eq!(st(e, &["content", "label"]), "bkoldext");
    assert_eq!(e.get("mounted_at"), Some(&Value::from(vec!["/mnt/bkold-ext4"])));
    let x = device(&v, &xfs);
    assert_eq!(st(x, &["content", "type"]), "xfs");
    assert_eq!(x.get("mounted_at"), Some(&Value::from(vec!["/mnt/bkold-xfs"])));
    let warnings = v.get("warnings").and_then(Value::as_array).unwrap();
    assert!(warnings.is_empty(), "{warnings:?}");
}

#[test]
#[ignore]
fn zfs_mirror_pool() {
    if !as_root() || !std::path::Path::new("/usr/sbin/zpool").exists() {
        eprintln!("SKIP: needs root and zfsutils-linux");
        return;
    }
    let dir = PathBuf::from(format!("/var/tmp/bko-holt-zfs-{}", std::process::id()));
    let d = dir.display();
    let pool = format!("bkold{}", std::process::id());
    let _cleanup = Cleanup(format!(
        "zpool destroy -f {pool}; for l in $(losetup -j {d}/a -O NAME -n) $(losetup -j {d}/b -O NAME -n); do losetup -d $l; done; rm -rf {d}"
    ));
    let s = sh(&format!(
        "mkdir -p {d}; truncate -s 512M {d}/a {d}/b; la=$(losetup --find --show --partscan {d}/a); lb=$(losetup --find --show --partscan {d}/b); \
         zpool create -f -o ashift=12 -o cachefile=none -o compatibility=openzfs-2.1-linux -O compression=zstd -O mountpoint=none {pool} mirror $la $lb; \
         zfs create -o mountpoint=none -o recordsize=1M {pool}/data; zfs create -V 32M {pool}/vol; udevadm settle; echo $la $lb"
    ));
    let la = s.split_whitespace().next().unwrap().trim_start_matches("/dev/").to_string();
    // zpool partitions a real whole disk (the vdev is then partition 1) but
    // labels a loop device as it is: ask zpool which device it used.
    let vpath = sh(&format!("zpool list -v -H -P {pool} | awk '$1 ~ /^\\/dev\\// {{print $1; exit}}'"));
    let vname = kname(&vpath);
    let v = dump();
    let vdev = device(&v, &vname);
    assert_eq!(st(vdev, &["content", "type"]), "zfs_member");
    assert_eq!(st(vdev, &["content", "label"]), pool);
    let guid = sh(&format!("zpool get -H -p -o value guid {pool}"));
    assert_eq!(st(vdev, &["content", "uuid"]), guid);
    assert_eq!(vdev.at(&["content", "labels_valid"]).and_then(Value::as_u64), Some(4));
    if vname != la {
        // The whole disk isn't a vdev itself, whatever blkid says.
        assert!(device(&v, &la).get("content").unwrap().is_null());
    }
    let p = v.at(&["storage", "zfs", "pools_from_labels"]).and_then(Value::as_array).unwrap().iter().find(|p| p.get("name").and_then(Value::as_str) == Some(pool.as_str())).unwrap();
    assert_eq!(st(p, &["pool_guid"]), guid);
    assert_eq!(st(p, &["top_level_vdevs", "0", "type"]), "mirror");
    assert_eq!(p.at(&["top_level_vdevs", "0", "children"]).and_then(Value::as_array).map(Vec::len), Some(2));
    assert_eq!(p.get("member_devices").and_then(Value::as_array).map(Vec::len), Some(2));
    let ip = v.at(&["storage", "zfs", "tools", "imported_pools"]).and_then(Value::as_array).unwrap().iter().find(|x| x.get("name").and_then(Value::as_str) == Some(pool.as_str())).unwrap();
    assert_eq!(st(ip, &["properties", "compatibility"]), "openzfs-2.1-linux");
    assert_eq!(st(ip, &["properties", "ashift"]), "12");
    let features = ip.get("features").and_then(Value::as_object).unwrap();
    let want = sh(&format!("zpool get -H -o property all {pool} | grep -c '^feature@'"));
    assert_eq!(features.len().to_string(), want);
    let ds = v.at(&["storage", "zfs", "tools", "datasets"]).and_then(Value::as_array).unwrap();
    let data = ds.iter().find(|x| x.get("name").and_then(Value::as_str) == Some(format!("{pool}/data").as_str())).unwrap();
    assert_eq!(data.get("recordsize").and_then(Value::as_u64), Some(1 << 20));
    assert_eq!(st(data, &["compression"]), "zstd");
    let vol = ds.iter().find(|x| x.get("name").and_then(Value::as_str) == Some(format!("{pool}/vol").as_str())).unwrap();
    assert_eq!(st(vol, &["type"]), "volume");
    assert_eq!(vol.get("volsize").and_then(Value::as_u64), Some(32 << 20));
    // The zvol shows up as a block device too.
    let zd = kname(&format!("/dev/zvol/{pool}/vol"));
    assert_eq!(device(&v, &zd).get("size_bytes").and_then(Value::as_u64), Some(32 << 20));
}
