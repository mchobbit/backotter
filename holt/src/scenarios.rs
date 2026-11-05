//! Whole-system tests: fake Debian 13 machines (their /sys, /proc, /dev,
//! /etc, /run, /boot and /var as a directory tree, the disks as image files
//! under /dev) laid out the way Debian's installer and BackOtter's test
//! guests lay real ones out, run through the full report.

use crate::bootcode::samples as grub;
use crate::json::Value;
use crate::parttable::build::{mbr_entry, write_gpt, GptSpec};
use crate::parttable::{BIOS_BOOT, ESP};
use crate::probe::{btrfs, ext, fat, luks, lvm, md, misc, zfs};
use crate::sys::{FakeRunner, Sys};
use crate::testutil::Tree;

const MIB: usize = 1 << 20;
const LINUX: &str = "0fc63daf-8483-4772-8e79-3d69d8477de4";
const RAID: &str = "a19d880f-05fc-4d3b-a006-743f0f84911e";

fn put(img: &mut [u8], at_sector: usize, data: &[u8]) {
    let o = at_sector * 512;
    let n = data.len().min(img.len() - o);
    img[o..o + n].copy_from_slice(&data[..n]);
}

fn run(t: &Tree, privileged: bool, runner: FakeRunner) -> Value {
    let sys = Sys::new(t.root(), Box::new(runner), privileged);
    let os = crate::osrel::read(&sys);
    crate::osrel::check_supported(os.as_ref()).unwrap();
    let v = crate::report::build(&sys, os.as_ref(), "2026-11-04T00:00:00Z");
    // Whatever the scenario, the output is valid JSON that reads back the same.
    // (Compared as text: a positive Int reads back as a UInt.)
    assert_eq!(crate::json::parse(&v.pretty()).unwrap().compact(), v.compact());
    assert_eq!(crate::json::parse(&v.compact()).unwrap().compact(), v.compact());
    v
}

fn s<'a>(v: &'a Value, path: &[&str]) -> &'a str {
    v.at(path).and_then(Value::as_str).unwrap_or_else(|| panic!("no string at {path:?}"))
}

fn u(v: &Value, path: &[&str]) -> u64 {
    v.at(path).and_then(Value::as_u64).unwrap_or_else(|| panic!("no number at {path:?}"))
}

fn arr<'a>(v: &'a Value, path: &[&str]) -> &'a Vec<Value> {
    v.at(path).and_then(Value::as_array).unwrap_or_else(|| panic!("no array at {path:?}"))
}

/// The debian13 test guest: BIOS, MBR, /boot ext4 (1), extended (2) holding
/// swap (5) and a btrfs root (6) in subvolume @rootfs; GRUB in the MBR and
/// the gap after it.
fn bios_btrfs() -> Tree {
    let t = Tree::new("scn-bios");
    t.debian13();
    let mut img = vec![0u8; 48 * MIB];
    img[..512].copy_from_slice(&grub::grub2_boot_img(1));
    img[440..444].copy_from_slice(&0x5eed_0001u32.to_le_bytes());
    // grub2_boot_img wrote the signature; the entries come next.
    put(&mut img, 1, &grub::grub2_diskboot_img());
    mbr_entry(&mut img[..512], 0, 0x80, 0x83, 2048, 16384); // /boot 8 MiB
    mbr_entry(&mut img[..512], 1, 0, 0x05, 18430, 79874); // extended
    let e1 = 18430 * 512;
    mbr_entry(&mut img[e1..e1 + 512], 0, 0, 0x82, 2, 8192);
    mbr_entry(&mut img[e1..e1 + 512], 1, 0, 0x05, 8194, 71680);
    let e2 = (18430 + 8194) * 512;
    mbr_entry(&mut img[e2..e2 + 512], 0, 0, 0x83, 2, 71678);
    put(&mut img, 2048, &ext::sample::image(0x4 | 0x8 | 0x10 | 0x20 | 0x1000, 0x2 | 0x40 | 0x80 | 0x200, 0x1 | 0x2 | 0x400, ""));
    put(&mut img, 18432, &misc::sample::swap(1024, ""));
    put(&mut img, 26626, &btrfs::sample::image("", 0x1 | 0x20 | 0x40 | 0x100 | 0x200));
    let boot_uuid = "10111213-1415-1617-1819-1a1b1c1d1e1f";
    let root_uuid = "b0b1b2b3-b4b5-b6b7-b8b9-babbbcbdbebf";
    let swap_uuid = "c0c1c2c3-c4c5-c6c7-c8c9-cacbcccdcecf";
    t.disk("vda", "254:0", &img, 512)
        .partition("vda", "vda1", "254:1", 1, 2048, 16384, &img)
        .partition("vda", "vda2", "254:2", 2, 18430, 2, &img)
        .partition("vda", "vda5", "254:5", 5, 18432, 8192, &img)
        .partition("vda", "vda6", "254:6", 6, 26626, 71678, &img)
        .file("/sys/block/vda/device/model", "")
        .file("/run/udev/data/b254:0", "S:disk/by-path/pci-0000:00:04.0\nE:ID_PATH=pci-0000:00:04.0\nE:ID_PART_TABLE_TYPE=dos\nE:ID_PART_TABLE_UUID=5eed0001\n")
        .file("/run/udev/data/b254:1", format!("S:disk/by-uuid/{boot_uuid}\nE:ID_FS_TYPE=ext4\nE:ID_FS_UUID={boot_uuid}\nE:ID_FS_USAGE=filesystem\nE:ID_FS_VERSION=1.0\nE:ID_PART_ENTRY_SCHEME=dos\nE:ID_PART_ENTRY_TYPE=0x83\nE:ID_PART_ENTRY_UUID=5eed0001-01\nE:ID_PART_ENTRY_FLAGS=0x80\n"))
        .file("/run/udev/data/b254:5", format!("E:ID_FS_TYPE=swap\nE:ID_FS_UUID={swap_uuid}\nE:ID_FS_USAGE=other\nE:ID_PART_ENTRY_TYPE=0x82\n"))
        .file("/run/udev/data/b254:6", format!("E:ID_FS_TYPE=btrfs\nE:ID_FS_UUID={root_uuid}\nE:ID_FS_USAGE=filesystem\nE:ID_PART_ENTRY_TYPE=0x83\nE:ID_PART_ENTRY_UUID=5eed0001-06\n"))
        .file("/proc/self/mountinfo", "26 1 0:25 /@rootfs / rw,relatime shared:1 - btrfs /dev/vda6 rw,space_cache=v2,subvolid=256,subvol=/@rootfs\n27 26 254:1 / /boot rw,relatime shared:2 - ext4 /dev/vda1 rw\n28 26 0:5 / /dev rw - devtmpfs udev rw\n")
        .file("/proc/swaps", "Filename\tType\tSize\tUsed\tPriority\n/dev/vda5 partition 4194300 0 -2\n")
        .file("/proc/cmdline", format!("BOOT_IMAGE=/vmlinuz-6.12.48+deb13-amd64 root=UUID={root_uuid} ro rootflags=subvol=@rootfs quiet\n"))
        .file("/proc/sys/kernel/osrelease", "6.12.48+deb13-amd64\n")
        .file("/proc/sys/kernel/hostname", "deb13\n")
        .file("/etc/fstab", format!("UUID={root_uuid} / btrfs defaults,subvol=@rootfs 0 0\nUUID={boot_uuid} /boot ext4 defaults 0 2\nUUID={swap_uuid} none swap sw 0 0\n/dev/sr0 /media/cdrom0 udf,iso9660 user,noauto 0 0\n"))
        .file("/var/lib/dpkg/status", "Package: grub-pc\nStatus: install ok installed\nArchitecture: amd64\nVersion: 2.12-9+deb13u2\n\nPackage: btrfs-progs\nStatus: install ok installed\nArchitecture: amd64\nVersion: 6.14-1\n\nPackage: initramfs-tools\nStatus: install ok installed\nArchitecture: all\nVersion: 0.148.3\n")
        .file("/var/cache/debconf/config.dat", "Name: grub-pc/install_devices\nTemplate: grub-pc/install_devices\nValue: /dev/disk/by-path/pci-0000:00:04.0\nOwners: grub-pc\nFlags: seen\n\n")
        .file("/boot/grub/i386-pc/normal.mod", "x")
        .file("/boot/grub/i386-pc/core.img", "x")
        .file("/boot/grub/grub.cfg", format!("insmod part_msdos\ninsmod ext2\nsearch --no-floppy --fs-uuid --set=root {boot_uuid}\nmenuentry 'Debian GNU/Linux' {{\n\tlinux\t/vmlinuz-6.12.48+deb13-amd64 root=UUID={root_uuid} ro rootflags=subvol=@rootfs quiet\n\tinitrd\t/initrd.img-6.12.48+deb13-amd64\n}}\n"))
        .file("/boot/vmlinuz-6.12.48+deb13-amd64", "k")
        .file("/boot/initrd.img-6.12.48+deb13-amd64", "i")
        .file("/etc/default/grub", "GRUB_DEFAULT=0\nGRUB_TIMEOUT=5\nGRUB_CMDLINE_LINUX=\"console=ttyS0\"\n");
    t
}

#[test]
fn bios_mbr_btrfs_guest() {
    let t = bios_btrfs();
    let v = run(&t, true, FakeRunner::default().with("btrfs subvolume list -a -p -u -q /", "ID 256 gen 99 parent 5 top level 5 parent_uuid - uuid 6c8a1b2c-0000-4000-8000-000000000001 path @rootfs\n").with("btrfs subvolume get-default /", "ID 5 (FS_TREE)\n")
            .with("btrfs subvolume list -a -r /", "")
            .with("btrfs property get -t s /", "ro=false\n")
            .with("btrfs property get -t i /", "compression=zstd\n"));
    assert_eq!(s(&v, &["format"]), "bko-holt");
    assert_eq!(s(&v, &["firmware", "mode"]), "bios");
    assert_eq!(v.get("privileged"), Some(&Value::Bool(true)));
    let disk = &v.at(&["storage", "disks", "0"]).unwrap();
    assert_eq!(s(disk, &["name"]), "vda");
    assert_eq!(s(disk, &["transport"]), "virtio");
    assert_eq!(s(disk, &["partition_table", "type"]), "dos");
    assert_eq!(s(disk, &["partition_table", "disk_signature"]), "5eed0001");
    assert_eq!(s(disk, &["boot_code", "kind"]), "grub2");
    assert_eq!(disk.at(&["boot_code", "grub_core_present"]), Some(&Value::Bool(true)));
    assert_eq!(s(disk, &["boot_code", "grub_core_in"]), "post-mbr-gap");
    assert!(disk.get("content").unwrap().is_null());
    let parts = arr(disk, &["partitions"]);
    let names: Vec<&str> = parts.iter().map(|p| p.get("name").and_then(Value::as_str).unwrap()).collect();
    assert_eq!(names, vec!["vda1", "vda2", "vda5", "vda6"]);
    for p in parts {
        assert_eq!(p.get("matches_kernel"), Some(&Value::Bool(true)), "{}", p.compact());
    }
    assert_eq!(s(&parts[0], &["content", "type"]), "ext4");
    assert_eq!(parts[0].get("bootable"), Some(&Value::Bool(true)));
    assert_eq!(arr(&parts[0], &["mounted_at"])[0].as_str(), Some("/boot"));
    assert_eq!(parts[1].get("extended"), Some(&Value::Bool(true)));
    assert_eq!(s(&parts[2], &["content", "type"]), "swap");
    assert_eq!(parts[2].get("logical"), Some(&Value::Bool(true)));
    assert_eq!(s(&parts[3], &["content", "type"]), "btrfs");
    assert_eq!(s(&parts[3], &["uuid"]), "5eed0001-06");
    assert_eq!(arr(&parts[3], &["mounted_at"])[0].as_str(), Some("/"));
    // btrfs: mounted subvolume and the tool's subvolume list.
    let b = v.at(&["storage", "btrfs", "0"]).unwrap();
    assert_eq!(s(b, &["uuid"]), "b0b1b2b3-b4b5-b6b7-b8b9-babbbcbdbebf");
    assert_eq!(s(b, &["mounted_subvolumes", "0", "subvol"]), "/@rootfs");
    assert_eq!(s(b, &["subvolumes", "0", "path"]), "@rootfs");
    assert_eq!(u(b, &["default_subvolume", "id"]), 5);
    assert_eq!(b.at(&["subvolumes", "0", "readonly"]), Some(&Value::Bool(false)));
    assert_eq!(s(b, &["subvolumes", "0", "properties", "compression"]), "zstd");
    assert!(b.get("qgroups").unwrap().is_null());
    // fstab resolves to devices; the CD drive (absent) resolves to nothing.
    let f = arr(&v, &["storage", "fstab"]);
    assert_eq!(s(&f[0], &["resolves_to"]), "vda6");
    assert_eq!(s(&f[1], &["resolves_to"]), "vda1");
    assert_eq!(s(&f[2], &["resolves_to"]), "vda5");
    assert!(f[3].get("resolves_to").unwrap().is_null());
    assert_eq!(s(&v, &["storage", "swaps", "0", "device"]), "vda5");
    // Mounts: devtmpfs is left out; btrfs's anonymous devno still resolves.
    let m = arr(&v, &["storage", "mounts"]);
    assert_eq!(m.len(), 2);
    assert_eq!(s(&m[0], &["device"]), "vda6");
    // Boot.
    assert_eq!(s(&v, &["boot", "loader", "name"]), "grub");
    assert_eq!(s(&v, &["boot", "loader", "package"]), "grub-pc");
    assert_eq!(s(&v, &["boot", "grub", "debconf", "grub-pc/install_devices"]), "/dev/disk/by-path/pci-0000:00:04.0");
    assert_eq!(arr(&v, &["boot", "grub", "installed_platforms"])[0].as_str(), Some("i386-pc"));
    assert_eq!(s(&v, &["boot", "grub", "grub_cfg", "entries", "0"]), "Debian GNU/Linux");
    assert_eq!(s(&v, &["boot", "grub", "default_grub", "GRUB_CMDLINE_LINUX"]), "console=ttyS0");
    assert_eq!(s(&v, &["boot", "cmdline", "storage", "rootflags"]), "subvol=@rootfs");
    assert_eq!(v.at(&["boot", "kernels", "installed", "0", "running"]), Some(&Value::Bool(true)));
    assert_eq!(s(&v, &["boot", "initramfs", "generator"]), "initramfs-tools");
    assert!(v.at(&["boot", "efi"]).unwrap().is_null());
    // Topology reads as a tree.
    let topo = v.at(&["storage", "topology"]).unwrap().as_object().unwrap();
    assert_eq!(topo.len(), 1);
    assert!(topo[0].0.starts_with("vda (disk, 48 MiB, dos)"), "{}", topo[0].0);
    let kids: Vec<&str> = topo[0].1.as_object().unwrap().iter().map(|(k, _)| k.as_str()).collect();
    assert!(kids[3].starts_with("vda6 (partition, ") && kids[3].ends_with("btrfs on /"), "{kids:?}");
    assert!(arr(&v, &["warnings"]).is_empty(), "{:?}", v.get("warnings"));
    assert_eq!(s(&v, &["host", "hostname"]), "deb13");
}

#[test]
fn unprivileged_falls_back_to_udev() {
    let t = bios_btrfs();
    let v = run(&t, false, FakeRunner::default());
    assert_eq!(v.get("privileged"), Some(&Value::Bool(false)));
    let w = arr(&v, &["warnings"]);
    assert!(w.iter().any(|x| x.as_str().unwrap().contains("not running as root")));
    let disk = v.at(&["storage", "disks", "0"]).unwrap();
    assert_eq!(s(disk, &["partition_table", "type"]), "dos");
    assert_eq!(s(disk, &["partition_table", "source"]), "udev");
    assert!(disk.get("boot_code").is_none());
    let p1 = v.at(&["storage", "disks", "0", "partitions", "0"]).unwrap();
    assert_eq!(s(p1, &["content", "type"]), "ext4");
    assert_eq!(s(p1, &["content", "source"]), "udev");
    assert_eq!(s(p1, &["type_name"]), "Linux");
    assert_eq!(s(p1, &["uuid"]), "5eed0001-01");
    assert_eq!(u(p1, &["start_bytes"]), 2048 * 512);
    // fstab still resolves through udev's UUIDs.
    assert_eq!(s(&v, &["storage", "fstab", "0", "resolves_to"]), "vda6");
    // The loader still shows from the package, without the MBR.
    assert_eq!(s(&v, &["boot", "loader", "name"]), "grub");
}

/// Chopin itself: UEFI with Secure Boot off, NVMe, GPT with an ESP and an
/// ext4 root, shim + GRUB, its real EFI variables.
#[test]
fn uefi_gpt_nvme_shim() {
    use crate::efi::real::*;
    use crate::efi::{GLOBAL, LOADER, SHIM};
    let t = Tree::new("scn-uefi");
    t.debian13();
    let mut img = vec![0u8; 64 * MIB];
    write_gpt(
        &mut img,
        512,
        &GptSpec {
            disk_guid: "6f1c2c3d-0000-4000-8000-00000000aaaa",
            parts: vec![
                (ESP, "0b5f04ed-e731-4ac6-933e-83ff25657722", 2048, 73727, 0, "EFI System Partition"),
                (LINUX, "11112222-3333-4444-5555-666677778888", 73728, 131038, 0, ""),
            ],
        },
    );
    // 35 MiB: just enough clusters to be FAT32, as mkfs.fat -F 32 needs.
    let esp = fat::sample::fat32(71680, 0x1234_abcd, "");
    put(&mut img, 2048, &esp);
    put(&mut img, 73728, &ext::sample::image(0x4 | 0x1000, 0x2 | 0x40 | 0x80 | 0x200, 0x1 | 0x400, ""));
    let v_ = |n: &str, g: &str| format!("/sys/firmware/efi/efivars/{n}-{g}");
    t.disk("nvme1n1", "259:0", &img, 512)
        .partition("nvme1n1", "nvme1n1p1", "259:1", 1, 2048, 71680, &img)
        .partition("nvme1n1", "nvme1n1p2", "259:2", 2, 73728, 57311, &img)
        .file("/sys/block/nvme1n1/device/model", "CT2000P3SSD8\n")
        .file("/sys/block/nvme1n1/device/serial", "2240E6789ABC\n")
        .file("/sys/block/nvme1n1/device/firmware_rev", "P9CR40A\n")
        .file("/sys/firmware/efi/fw_platform_size", "64\n")
        .file(&v_("Boot0001", GLOBAL), unhex(BOOT0001_SHIM))
        .file(&v_("Boot0002", GLOBAL), unhex(BOOT0002_GRUB))
        .file(&v_("BootOrder", GLOBAL), unhex("07000000 0100 0200"))
        .file(&v_("BootCurrent", GLOBAL), unhex("06000000 0100"))
        .file(&v_("SecureBoot", GLOBAL), unhex("06000000 00"))
        .file(&v_("LoaderInfo", LOADER), unhex(LOADER_INFO))
        .file(&v_("MokListRT", SHIM), [0u8; 8])
        .symlink("/dev/disk/by-partuuid/0b5f04ed-e731-4ac6-933e-83ff25657722", "../../nvme1n1p1")
        .file("/proc/self/mountinfo", "26 1 259:2 / / rw,relatime shared:1 - ext4 /dev/nvme1n1p2 rw,errors=remount-ro\n27 26 259:1 / /boot/efi rw,relatime shared:2 - vfat /dev/nvme1n1p1 rw,fmask=0077\n")
        .file("/boot/efi/EFI/debian/shimx64.efi", vec![1u8; 300])
        .file("/boot/efi/EFI/debian/grubx64.efi", vec![2u8; 400])
        .file("/boot/efi/EFI/debian/mmx64.efi", vec![3u8; 200])
        .file("/boot/efi/EFI/debian/grub.cfg", "search.fs_uuid 10111213-1415-1617-1819-1a1b1c1d1e1f root \nset prefix=($root)'/boot/grub'\nconfigfile $prefix/grub.cfg\n")
        .file("/etc/fstab", "UUID=10111213-1415-1617-1819-1a1b1c1d1e1f / ext4 errors=remount-ro 0 1\nPARTUUID=0b5f04ed-e731-4ac6-933e-83ff25657722 /boot/efi vfat umask=0077 0 1\n")
        .file("/var/lib/dpkg/status", "Package: grub-efi-amd64-signed\nStatus: install ok installed\nArchitecture: amd64\nVersion: 1+2.12+9+deb13u2\n\nPackage: shim-signed\nStatus: install ok installed\nArchitecture: amd64\nVersion: 1.47+15.8-1\n")
        .file("/boot/grub/x86_64-efi/normal.mod", "x");
    let v = run(&t, true, FakeRunner::default());
    assert_eq!(s(&v, &["firmware", "mode"]), "uefi");
    assert_eq!(v.at(&["firmware", "secure_boot"]), Some(&Value::Bool(false)));
    let d = v.at(&["storage", "disks", "0"]).unwrap();
    assert_eq!(s(d, &["model"]), "CT2000P3SSD8");
    assert_eq!(s(d, &["serial"]), "2240E6789ABC");
    assert_eq!(s(d, &["transport"]), "nvme");
    assert_eq!(s(d, &["partition_table", "type"]), "gpt");
    assert_eq!(s(d, &["partition_table", "backup_header"]), "ok");
    assert_eq!(s(d, &["boot_code", "kind"]), "none");
    let p1 = v.at(&["storage", "disks", "0", "partitions", "0"]).unwrap();
    assert_eq!(s(p1, &["type_name"]), "EFI System");
    assert_eq!(s(p1, &["name"]), "nvme1n1p1");
    assert_eq!(s(p1, &["partlabel"]), "EFI System Partition");
    assert_eq!(s(p1, &["content", "type"]), "vfat");
    assert_eq!(s(p1, &["content", "uuid"]), "1234-ABCD");
    // fstab: UUID= and PARTUUID= both resolve.
    assert_eq!(s(&v, &["storage", "fstab", "0", "resolves_to"]), "nvme1n1p2");
    assert_eq!(s(&v, &["storage", "fstab", "1", "resolves_to"]), "nvme1n1p1");
    // The boot entry names the ESP by PARTUUID, which maps to its device.
    let e = v.at(&["boot", "efi", "entries", "0"]).unwrap();
    assert_eq!(s(e, &["partition", "device"]), "nvme1n1p1");
    assert_eq!(s(&v, &["boot", "efi", "boot_current"]), "0001");
    let l = v.at(&["boot", "loader"]).unwrap();
    assert_eq!(s(l, &["name"]), "grub");
    assert_eq!(l.get("via_shim"), Some(&Value::Bool(true)));
    assert_eq!(s(l, &["package"]), "grub-efi-amd64-signed");
    let esp0 = v.at(&["boot", "esps", "0"]).unwrap();
    assert_eq!(s(esp0, &["device"]), "nvme1n1p1");
    assert_eq!(arr(esp0, &["contents", "files"]).len(), 4);
    assert_eq!(s(esp0, &["contents", "debian_grub_stub", "search_uuids", "0"]), "10111213-1415-1617-1819-1a1b1c1d1e1f");
    assert_eq!(arr(&v, &["boot", "grub", "installed_platforms"])[0].as_str(), Some("x86_64-efi"));
    assert_eq!(s(&v, &["boot", "packages", "shim-signed"]), "1.47+15.8-1");
}

/// Debian's "Guided - use entire disk and set up encrypted LVM": GPT with
/// ESP, /boot, and LUKS2 holding a PV with root and swap_1.
#[test]
fn luks_lvm() {
    let t = Tree::new("scn-luks-lvm");
    t.debian13();
    let mut img = vec![0u8; 64 * MIB];
    let luks_uuid = "6c3e5f0e-6b0a-4d6c-9a2e-8f7e1d2c3b4a";
    write_gpt(
        &mut img,
        512,
        &GptSpec {
            disk_guid: "6f1c2c3d-0000-4000-8000-00000000bbbb",
            parts: vec![
                (ESP, "aaaaaaaa-0000-0000-0000-000000000001", 2048, 34815, 0, ""),
                (LINUX, "aaaaaaaa-0000-0000-0000-000000000002", 34816, 51199, 0, ""),
                (LINUX, "aaaaaaaa-0000-0000-0000-000000000003", 51200, 131038, 0, ""),
            ],
        },
    );
    put(&mut img, 2048, &fat::sample::fat32(32768, 1, "EFI"));
    put(&mut img, 34816, &ext::sample::image(0x4, 0x2 | 0x40, 0x1, "boot"));
    put(&mut img, 51200, &luks::sample::luks2(luks_uuid, ""));
    // The opened container (dm-0) holds the PV; the LVs are dm-1 and dm-2.
    let pv = lvm::sample::pv(512, 1 << 20);
    let vgid = "Yb3kFa0Pz1AbCdEfGhIjKlMnOpQrStUv";
    t.disk("sda", "8:0", &img, 512)
        .partition("sda", "sda1", "8:1", 1, 2048, 32768, &img)
        .partition("sda", "sda2", "8:2", 2, 34816, 16384, &img)
        .partition("sda", "sda3", "8:3", 3, 51200, 79839, &img)
        .file("/sys/block/sda/sda3/holders/dm-0", "")
        .file("/run/udev/data/b8:0", "E:ID_BUS=ata\nE:ID_SERIAL_SHORT=QM00001\n")
        .disk("dm-0", "253:0", &pv, 512)
        .file("/sys/block/dm-0/dm/name", "sda3_crypt\n")
        .file("/sys/block/dm-0/dm/uuid", "CRYPT-LUKS2-6c3e5f0e6b0a4d6c9a2e8f7e1d2c3b4a-sda3_crypt\n")
        .file("/sys/block/dm-0/slaves/sda3", "")
        .file("/sys/block/dm-0/holders/dm-1", "")
        .file("/sys/block/dm-0/holders/dm-2", "")
        .disk("dm-1", "253:1", &ext::sample::image(0x4, 0x2 | 0x40 | 0x80, 0x1, "root"), 512)
        .file("/sys/block/dm-1/dm/name", "vg0-root\n")
        .file("/sys/block/dm-1/dm/uuid", format!("LVM-{vgid}r00t00aaaabbbbccccddddeeeeffffff\n"))
        .file("/sys/block/dm-1/slaves/dm-0", "")
        .disk("dm-2", "253:2", &misc::sample::swap(16, ""), 512)
        .file("/sys/block/dm-2/dm/name", "vg0-swap_1\n")
        .file("/sys/block/dm-2/dm/uuid", format!("LVM-{vgid}sw4p00aaaabbbbccccddddeeeeffffff\n"))
        .file("/sys/block/dm-2/slaves/dm-0", "")
        .symlink("/dev/mapper/vg0-root", "../dm-1")
        .symlink("/dev/mapper/sda3_crypt", "../dm-0")
        .file("/proc/self/mountinfo", "26 1 253:1 / / rw,relatime - ext4 /dev/mapper/vg0-root rw\n27 26 8:2 / /boot rw - ext4 /dev/sda2 rw\n28 27 8:1 / /boot/efi rw - vfat /dev/sda1 rw\n")
        .file("/etc/crypttab", format!("sda3_crypt UUID={luks_uuid} none luks,discard\n"))
        .file("/etc/fstab", "/dev/mapper/vg0-root / ext4 errors=remount-ro 0 1\n/dev/mapper/vg0-swap_1 none swap sw 0 0\n");
    let v = run(&t, true, FakeRunner::default());
    let p3 = v.at(&["storage", "disks", "0", "partitions", "2"]).unwrap();
    assert_eq!(s(p3, &["content", "type"]), "crypto_LUKS");
    assert_eq!(s(p3, &["content", "uuid"]), luks_uuid);
    assert_eq!(s(p3, &["content", "segments", "0", "encryption"]), "aes-xts-plain64");
    assert_eq!(arr(p3, &["holders"])[0].as_str(), Some("dm-0"));
    // crypttab resolves the LUKS UUID to the partition.
    assert_eq!(s(&v, &["storage", "crypttab", "0", "resolves_to"]), "sda3");
    let dms = arr(&v, &["storage", "dm_devices"]);
    assert_eq!(dms.len(), 3);
    assert_eq!(s(&dms[0], &["dm", "target"]), "crypt");
    assert_eq!(s(&dms[0], &["dm", "luks_uuid"]), luks_uuid);
    assert_eq!(s(&dms[0], &["content", "type"]), "LVM2_member");
    assert_eq!(s(&dms[1], &["dm", "target"]), "lvm");
    assert_eq!(s(&dms[1], &["dm", "lv"]), "root");
    assert_eq!(arr(&dms[1], &["mounted_at"])[0].as_str(), Some("/"));
    // The VG, its PV on dm-0, and both LVs on their dm devices.
    let vg = v.at(&["storage", "lvm", "volume_groups", "0"]).unwrap();
    assert_eq!(s(vg, &["name"]), "vg0");
    assert_eq!(s(vg, &["physical_volumes", "0", "device"]), "dm-0");
    assert_eq!(s(vg, &["logical_volumes", "0", "active_device"]), "dm-1");
    assert_eq!(s(vg, &["logical_volumes", "1", "active_device"]), "dm-2");
    // fstab's /dev/mapper paths resolve through the symlinks.
    assert_eq!(s(&v, &["storage", "fstab", "0", "resolves_to"]), "dm-1");
    // The topology nests disk > partition > crypt > LVs.
    let topo = v.get("storage").unwrap().get("topology").unwrap().compact();
    assert!(topo.contains("sda3 (partition"), "{topo}");
    assert!(topo.contains("dm-0 (dm, 4 MiB, sda3_crypt) LVM2_member"), "{topo}");
    assert!(topo.contains("dm-1 (dm, 64 KiB, vg0-root) ext4 \\\"root\\\" on /"), "{topo}");
    // dm devices aren't topology roots of their own.
    assert_eq!(v.at(&["storage", "topology"]).unwrap().as_object().unwrap().len(), 1);
}

/// Cinqcent-style md RAID1 over two disks' partitions, ext4 on md0.
#[test]
fn md_raid1() {
    let t = Tree::new("scn-md");
    t.debian13();
    let mut a = vec![0u8; 16 * MIB];
    let mut b = vec![0u8; 16 * MIB];
    for (img, g) in [(&mut a, "a"), (&mut b, "b")] {
        let du = format!("6f1c2c3d-0000-4000-8000-00000000000{g}");
        let pu = format!("aaaaaaaa-0000-0000-0000-00000000000{g}");
        write_gpt(img, 512, &GptSpec { disk_guid: &du, parts: vec![(BIOS_BOOT, &pu, 34, 2047, 0, ""), (RAID, &pu, 2048, 32734, 0, "")] });
        let member = md::sample::v1(30687 * 512, 4096, "cinqcent:0", 1, if g == "a" { 0 } else { 1 }, if g == "a" { 0 } else { 1 });
        put(img, 2048, &member);
    }
    let array = ext::sample::image(0x4, 0x2 | 0x40 | 0x80, 0x1, "data");
    t.disk("sda", "8:0", &a, 512)
        .partition("sda", "sda1", "8:1", 1, 34, 2014, &a)
        .partition("sda", "sda2", "8:2", 2, 2048, 30687, &a)
        .file("/sys/block/sda/sda2/holders/md0", "")
        .disk("sdb", "8:16", &b, 512)
        .partition("sdb", "sdb1", "8:17", 1, 34, 2014, &b)
        .partition("sdb", "sdb2", "8:18", 2, 2048, 30687, &b)
        .file("/sys/block/sdb/sdb2/holders/md0", "")
        .disk("md0", "9:0", &array, 512)
        .file("/sys/block/md0/slaves/sda2", "")
        .file("/sys/block/md0/slaves/sdb2", "")
        .file("/sys/block/md0/md/level", "raid1\n")
        .file("/sys/block/md0/md/raid_disks", "2\n")
        .file("/sys/block/md0/md/metadata_version", "1.2\n")
        .file("/sys/block/md0/md/array_state", "clean\n")
        .file("/sys/block/md0/md/degraded", "0\n")
        .file("/sys/block/md0/md/dev-sda2/slot", "0\n")
        .file("/sys/block/md0/md/dev-sda2/state", "in_sync\n")
        .file("/sys/block/md0/md/dev-sdb2/slot", "1\n")
        .file("/sys/block/md0/md/dev-sdb2/state", "in_sync\n")
        .file("/run/udev/data/b9:0", "S:md/0\nE:MD_UUID=30313233:34353637:38393a3b:3c3d3e3f\nE:MD_NAME=cinqcent:0\nE:MD_DEVNAME=0\n")
        .file("/etc/mdadm/mdadm.conf", "HOMEHOST <system>\nARRAY /dev/md/0 metadata=1.2 UUID=30313233:34353637:38393a3b:3c3d3e3f name=cinqcent:0\n");
    let v = run(&t, true, FakeRunner::default());
    let md0 = v.at(&["storage", "md_arrays", "0"]).unwrap();
    assert_eq!(s(md0, &["md", "level"]), "raid1");
    assert_eq!(s(md0, &["md", "uuid"]), "30313233:34353637:38393a3b:3c3d3e3f");
    assert_eq!(arr(md0, &["md", "members"]).len(), 2);
    assert_eq!(s(md0, &["md", "members", "1", "state"]), "in_sync");
    assert_eq!(s(md0, &["content", "type"]), "ext4");
    assert_eq!(s(md0, &["content", "label"]), "data");
    let m = v.at(&["storage", "disks", "1", "partitions", "1", "content"]).unwrap();
    assert_eq!(s(m, &["type"]), "linux_raid_member");
    assert_eq!(s(m, &["mdadm_uuid"]), "30313233:34353637:38393a3b:3c3d3e3f");
    assert_eq!(s(m, &["device_role"]), "active 1");
    assert_eq!(s(&v, &["storage", "mdadm_conf", "arrays", "0", "name"]), "cinqcent:0");
    assert_eq!(s(&v, &["storage", "disks", "0", "partitions", "0", "type_name"]), "BIOS boot");
    // md0 shows under both members in the topology.
    let topo = v.at(&["storage", "topology"]).unwrap().compact();
    assert_eq!(topo.matches("md0 (md, 64 KiB) ext4").count(), 2, "{topo}");
}

/// The debian13-zfs test guest: MBR, /boot ext4, swap, and a ZFS pool on
/// partition 3, imported, with the zfs tools answering.
#[test]
fn zfs_root() {
    let t = Tree::new("scn-zfs");
    t.debian13();
    let mut img = vec![0u8; 64 * MIB];
    mbr_entry(&mut img[..512], 0, 0x80, 0x83, 2048, 8192);
    mbr_entry(&mut img[..512], 1, 0, 0x82, 10240, 4096);
    mbr_entry(&mut img[..512], 2, 0, 0xbf, 14336, 116736);
    img[..440].copy_from_slice(&grub::grub2_boot_img(1)[..440]);
    let cfg = crate::json::parse(
        r#"{"version": 5000, "name": "rpool", "state": 0, "txg": 4, "pool_guid": 777, "hostname": "deb13zfs", "top_guid": 1, "guid": 1,
            "vdev_children": 1, "vdev_tree": {"type": "disk", "id": 0, "guid": 1, "path": "/dev/vda3", "whole_disk": 0, "ashift": 12},
            "features_for_read": {"com.delphix:hole_birth": true}}"#,
    )
    .unwrap();
    let vdev = zfs::pack::vdev(116736 * 512, &cfg);
    put(&mut img, 14336, &vdev);
    t.disk("vda", "254:0", &img, 512)
        .partition("vda", "vda1", "254:1", 1, 2048, 8192, &img)
        .partition("vda", "vda2", "254:2", 2, 10240, 4096, &img)
        .partition("vda", "vda3", "254:3", 3, 14336, 116736, &img)
        .file("/etc/zfs/zpool.cache", zfs::pack::packed(&crate::json::parse(r#"{"rpool": {"name": "rpool", "pool_guid": 777}}"#).unwrap()))
        .file("/proc/self/mountinfo", "26 1 0:24 / / rw,relatime shared:1 - zfs rpool/ROOT/debian rw,xattr,posixacl\n27 26 254:1 / /boot rw - ext4 /dev/vda1 rw\n")
        .file("/proc/cmdline", "BOOT_IMAGE=/vmlinuz root=ZFS=rpool/ROOT/debian ro\n");
    let runner = FakeRunner::default()
        .with("zpool list -H -o name", "rpool\n")
        .with("zpool get -H -p -o property,value,source all rpool", "size\t62277025792\t-\nashift\t12\tlocal\ncachefile\t/etc/zfs/zpool.cache\tlocal\nfree\t1\t-\nfeature@encryption\tenabled\tlocal\nfeature@async_destroy\tenabled\tlocal\nshort\n")
        .with(
            "zfs list -H -p -t filesystem,volume -o name,type,used,available,referenced,mountpoint,canmount,mounted,encryption,encryptionroot,keyformat,keylocation,keystatus,origin,compression,recordsize,volsize,volblocksize,acltype,xattr,dnodesize,normalization,utf8only,casesensitivity,atime,relatime,copies,com.sun:auto-snapshot",
            "rpool\tfilesystem\t1000\t2000\t96\t/\toff\tno\toff\t-\tnone\tnone\t-\t-\tlz4\t131072\t-\t-\tposix\tsa\tauto\tformD\ton\tsensitive\ton\ton\t1\t-\nrpool/ROOT/debian\tfilesystem\t900\t2000\t900\t/\tnoauto\tyes\toff\t-\tnone\tnone\t-\t-\tlz4\t131072\t-\t-\tposix\tsa\tauto\tformD\ton\tsensitive\ton\ton\t1\t-\n",
        )
        .with(
            "zfs get -H -p -t filesystem,volume -s local,received -o name,property,value,source all",
            "rpool\tcompression\tlz4\tlocal\nrpool\tcom.example:tag\tx y\tlocal\nrpool/ROOT/debian\tmountpoint\t/\tlocal\nrpool/ROOT/debian\tcanmount\tnoauto\tlocal\n",
        )
        .with(
            "zpool status -j --json-int -P rpool",
            r#"{"output_version": {"command": "zpool status", "vers_major": 0, "vers_minor": 1}, "pools": {"rpool": {"name": "rpool", "state": "ONLINE",
               "vdevs": {"rpool": {"name": "rpool", "vdev_type": "root", "vdevs": {"/dev/vda3": {"name": "/dev/vda3", "vdev_type": "disk", "path": "/dev/vda3"}}}}}}}"#,
        );
    let v = run(&t, true, runner);
    let p3 = v.at(&["storage", "disks", "0", "partitions", "2"]).unwrap();
    assert_eq!(s(p3, &["type_name"]), "Solaris");
    assert_eq!(s(p3, &["content", "type"]), "zfs_member");
    assert_eq!(s(p3, &["content", "label"]), "rpool");
    assert_eq!(u(p3, &["content", "labels_valid"]), 4);
    let pool = v.at(&["storage", "zfs", "pools_from_labels", "0"]).unwrap();
    assert_eq!(s(pool, &["name"]), "rpool");
    assert_eq!(s(pool, &["pool_guid"]), "777");
    assert_eq!(s(pool, &["top_level_vdevs", "0", "path"]), "/dev/vda3");
    assert_eq!(arr(pool, &["member_devices"])[0].as_str(), Some("vda3"));
    assert_eq!(u(&v, &["storage", "zfs", "zpool_cache", "rpool", "pool_guid"]), 777);
    let tools = v.at(&["storage", "zfs", "tools"]).unwrap();
    assert_eq!(s(tools, &["imported_pools", "0", "properties", "ashift"]), "12");
    assert!(tools.at(&["imported_pools", "0", "properties", "free"]).is_none());
    assert_eq!(s(tools, &["imported_pools", "0", "features", "encryption"]), "enabled");
    assert_eq!(s(tools, &["datasets", "1", "name"]), "rpool/ROOT/debian");
    assert_eq!(s(tools, &["datasets", "1", "canmount"]), "noauto");
    assert_eq!(u(tools, &["datasets", "1", "recordsize"]), 131072);
    assert!(tools.at(&["datasets", "1", "volsize"]).unwrap().is_null());
    assert_eq!(s(tools, &["datasets", "1", "casesensitivity"]), "sensitive");
    // Every local property, user properties too, with its source.
    assert_eq!(s(tools, &["datasets", "1", "properties", "canmount", "value"]), "noauto");
    assert_eq!(s(tools, &["datasets", "0", "properties", "com.example:tag", "value"]), "x y");
    assert_eq!(s(tools, &["datasets", "0", "properties", "compression", "source"]), "local");
    assert!(tools.at(&["datasets", "0", "properties", "canmount"]).is_none());
    // The pool's locally set properties, and its vdev tree from zpool status.
    assert_eq!(s(tools, &["imported_pools", "0", "local_properties", "ashift"]), "12");
    assert!(tools.at(&["imported_pools", "0", "local_properties", "size"]).is_none());
    assert_eq!(s(tools, &["imported_pools", "0", "vdevs", "vdevs", "rpool", "vdev_type"]), "root");
    assert_eq!(s(tools, &["imported_pools", "0", "vdevs", "vdevs", "rpool", "vdevs", "/dev/vda3", "path"]), "/dev/vda3");
    assert!(tools.at(&["imported_pools", "0", "vdevs", "logs"]).unwrap().is_null());
    let mounts = arr(&v, &["storage", "mounts"]);
    assert_eq!(s(&mounts[0], &["fstype"]), "zfs");
    assert_eq!(s(&mounts[0], &["source"]), "rpool/ROOT/debian");
    assert_eq!(s(&v, &["boot", "cmdline", "storage", "root"]), "ZFS=rpool/ROOT/debian");
}

#[test]
fn no_zfs_tools_no_section() {
    let t = Tree::new("scn-nozfs");
    t.debian13();
    let v = run(&t, true, FakeRunner::default());
    assert!(v.at(&["storage", "zfs", "tools"]).unwrap().is_null());
    assert!(v.at(&["storage", "zfs", "zpool_cache"]).unwrap().is_null());
    assert!(arr(&v, &["storage", "disks"]).is_empty());
    assert!(v.at(&["storage", "fstab"]).unwrap().is_null());
    assert_eq!(s(&v, &["boot", "loader", "name"]), "unknown");
}

/// A disk with a filesystem straight on it (no table), a loop device, a
/// zram swap device and an empty CD drive.
#[test]
fn whole_disk_fs_loop_zram() {
    let t = Tree::new("scn-misc");
    t.debian13();
    let x = crate::probe::xfs::sample::image("bulk");
    t.disk("sdc", "8:32", &x, 4096)
        .disk("loop0", "7:0", &btrfs::sample::image("img", 0), 512)
        .file("/sys/block/loop0/loop/backing_file", "/var/lib/images/a.img\n")
        .disk("zram0", "252:0", &misc::sample::swap(16, ""), 4096)
        .disk("sr0", "11:0", &[], 2048)
        .disk("sr1", "11:1", &vec![0u8; 1 << 20], 2048)
        .disk("fd0", "2:0", &[0u8; 4096], 512)
        .file("/proc/swaps", "Filename Type Size Used Priority\n/dev/zram0 partition 64 0 100\n/swapfile file 1024 0 -2\n");
    let v = run(&t, true, FakeRunner::default());
    let d = v.at(&["storage", "disks", "0"]).unwrap();
    assert_eq!(s(d, &["name"]), "sdc");
    assert!(d.get("partition_table").unwrap().is_null());
    assert_eq!(s(d, &["content", "type"]), "xfs");
    assert_eq!(u(d, &["logical_sector_size"]), 4096);
    assert_eq!(s(&v, &["storage", "loop_devices", "0", "backing_file"]), "/var/lib/images/a.img");
    assert_eq!(s(&v, &["storage", "loop_devices", "0", "content", "type"]), "btrfs");
    let other = arr(&v, &["storage", "other_devices"]);
    // The empty sr0 is left out; sr1 (a disc in it) and the floppy are
    // listed but never read.
    let kinds: Vec<&str> = other.iter().map(|o| o.get("kind").and_then(Value::as_str).unwrap()).collect();
    assert_eq!(kinds, vec!["floppy", "rom", "zram"]);
    assert!(other[0].get("content").unwrap().is_null());
    assert!(other[1].get("content").unwrap().is_null());
    assert_eq!(s(&other[2], &["content", "type"]), "swap");
    assert!(arr(&v, &["warnings"]).is_empty());
    let sw = arr(&v, &["storage", "swaps"]);
    assert_eq!(s(&sw[0], &["device"]), "zram0");
    assert!(sw[1].get("device").unwrap().is_null());
    // zram isn't a topology root.
    let roots: Vec<String> = v.at(&["storage", "topology"]).unwrap().as_object().unwrap().iter().map(|(k, _)| k.clone()).collect();
    assert!(roots.iter().all(|r| !r.starts_with("zram")), "{roots:?}");
    assert_eq!(roots.len(), 2);
}

#[test]
fn unreadable_device_warns_and_carries_on() {
    let t = Tree::new("scn-unreadable");
    t.debian13();
    t.disk("sda", "8:0", &vec![0u8; MIB], 512);
    std::fs::remove_file(t.p("/dev/sda")).unwrap();
    let v = run(&t, true, FakeRunner::default());
    let w = arr(&v, &["warnings"]);
    assert!(w.iter().any(|x| x.as_str().unwrap().starts_with("/dev/sda:")), "{w:?}");
    assert_eq!(s(&v, &["storage", "disks", "0", "name"]), "sda");
}
