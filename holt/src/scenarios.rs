//! Whole-system tests: fake Debian 13 machines, two Chimera Linux (BIOS
//! GRUB, UEFI systemd-boot), one Fedora Kinoite on ostree and the
//! Thinkpad's Gentoo (their /sys, /proc, /dev,
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
    // Redacted for a bug report, it still plans the same.
    if let Err(e) = crate::bugreport::check_plan(&v.pretty()) {
        panic!("redaction: {e}");
    }
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

/// What the btrfs tools answer in the bios_btrfs scenario.
fn bios_btrfs_runner() -> FakeRunner {
    FakeRunner::default()
        .with("btrfs subvolume list -a -p -u -q /", "ID 256 gen 99 parent 5 top level 5 parent_uuid - uuid 6c8a1b2c-0000-4000-8000-000000000001 path @rootfs\n")
        .with("btrfs subvolume get-default /", "ID 5 (FS_TREE)\n")
        .with("btrfs subvolume list -a -r /", "")
        .with("btrfs property get -t s /", "ro=false\n")
        .with("btrfs property get -t i /", "compression=zstd\n")
}

/// A btrfs whose tools the dump can't run (NixOS's, before its
/// /run/current-system/sw was on the dump's path): no subvolume list, and
/// a warning saying so.
#[test]
fn btrfs_without_its_tools_warns() {
    let v = run(&bios_btrfs(), true, FakeRunner::default());
    let b = v.at(&["storage", "btrfs", "0"]).unwrap();
    assert!(b.get("subvolumes").unwrap().is_null());
    assert_eq!(s(b, &["mounted_subvolumes", "0", "subvol"]), "/@rootfs");
    let w: Vec<&str> = arr(&v, &["warnings"]).iter().filter_map(Value::as_str).collect();
    assert!(w.contains(&"btrfs b0b1b2b3-b4b5-b6b7-b8b9-babbbcbdbebf: `btrfs subvolume list /` failed or btrfs isn't installed: its subvolumes aren't listed"), "{w:?}");
    let v = run(&bios_btrfs(), true, bios_btrfs_runner());
    assert!(!arr(&v, &["warnings"]).iter().any(|x| x.as_str().is_some_and(|x| x.contains("subvolume list"))));
}

#[test]
fn bios_mbr_btrfs_guest() {
    let t = bios_btrfs();
    let v = run(&t, true, bios_btrfs_runner());
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
    assert!(m.iter().all(|x| x.get("alias_of").is_none()));
    // Not ostree: the backup reads each mount where it is.
    assert_eq!(s(&v, &["storage", "physical_root"]), "/");
    let bs = arr(&v, &["storage", "backup_sources"]);
    let pairs: Vec<(&str, &str)> = bs.iter().map(|x| (s(x, &["path"]), s(x, &["from"]))).collect();
    assert_eq!(pairs, vec![("/", "/"), ("/boot", "/boot")]);
    assert_eq!(s(&bs[0], &["subvol"]), "/@rootfs");
    assert!(bs[1].get("subvol").unwrap().is_null());
    assert!(v.at(&["boot", "ostree"]).is_none());
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

/// The chimera guest's system on bios_btrfs's disk: Chimera's os-release
/// (ID quoted, no VERSION_ID), apk's database through /lib -> usr/lib, no
/// dpkg or debconf, initramfs-tools' kernel files as Chimera names them.
fn chimera_bios() -> Tree {
    let t = bios_btrfs();
    let v = "6.18.62-0-generic";
    for f in ["/var/lib/dpkg/status", "/var/cache/debconf/config.dat", "/etc/debian_version", "/boot/vmlinuz-6.12.48+deb13-amd64", "/boot/initrd.img-6.12.48+deb13-amd64"] {
        std::fs::remove_file(t.p(f)).unwrap();
    }
    let root_uuid = "b0b1b2b3-b4b5-b6b7-b8b9-babbbcbdbebf";
    t.file("/etc/os-release", "NAME=\"Chimera\"\nID=\"chimera\"\nPRETTY_NAME=\"Chimera Linux\"\n")
        .symlink("/lib", "usr/lib")
        .file(
            "/usr/lib/apk/db/installed",
            "C:Q1aaa=\nP:linux-lts\nV:6.18.62-r0\nA:x86_64\n\nC:Q1bbb=\nP:grub\nV:2.14-r1\nA:x86_64\n\nC:Q1ccc=\nP:grub-i386-pc\nV:2.14-r1\nA:x86_64\n\nC:Q1ddd=\nP:initramfs-tools\nV:0.148.3-r2\nA:noarch\n\nC:Q1eee=\nP:btrfs-progs\nV:6.17-r0\nA:x86_64\n\n",
        )
        .file(&format!("/boot/vmlinuz-{v}"), "k")
        .file(&format!("/boot/initrd.img-{v}"), "i")
        .file(&format!("/boot/config-{v}"), "")
        .dir(&format!("/usr/lib/modules/{v}/kernel"))
        .file("/etc/initramfs-tools/initramfs.conf", "MODULES=most\nCOMPRESS=zstd\n")
        .file("/proc/sys/kernel/osrelease", format!("{v}\n"))
        .file("/proc/sys/kernel/hostname", "chimera\n")
        .file("/proc/cmdline", format!("BOOT_IMAGE=/vmlinuz-{v} root=UUID={root_uuid} ro rootflags=subvol=@rootfs\n"))
        .file("/boot/grub/grub.cfg", format!("insmod part_msdos\ninsmod ext2\nmenuentry 'Chimera Linux' {{\n\tlinux\t/vmlinuz-{v} root=UUID={root_uuid} ro rootflags=subvol=@rootfs\n\tinitrd\t/initrd.img-{v}\n}}\n"));
    t
}

#[test]
fn chimera_bios_guest() {
    let v = run(&chimera_bios(), true, bios_btrfs_runner());
    assert_eq!(s(&v, &["os", "id"]), "chimera");
    assert!(v.at(&["os", "version_id"]).unwrap().is_null());
    assert_eq!(s(&v, &["os", "pretty_name"]), "Chimera Linux");
    assert_eq!(s(&v, &["boot", "loader", "name"]), "grub");
    assert_eq!(s(&v, &["boot", "loader", "firmware"]), "bios");
    assert_eq!(s(&v, &["boot", "loader", "package"]), "grub-i386-pc");
    assert!(v.at(&["boot", "grub", "debconf"]).unwrap().is_null());
    assert_eq!(s(&v, &["boot", "initramfs", "generator"]), "initramfs-tools");
    assert_eq!(s(&v, &["boot", "kernels", "installed", "0", "version"]), "6.18.62-0-generic");
    assert_eq!(arr(&v, &["boot", "kernels", "installed"]).len(), 1);
    assert_eq!(v.at(&["boot", "kernels", "installed", "0", "running"]), Some(&Value::Bool(true)));
    assert_eq!(v.at(&["boot", "kernels", "installed", "0", "modules_installed"]), Some(&Value::Bool(true)));
    assert_eq!(s(&v, &["boot", "packages", "initramfs-tools"]), "0.148.3-r2");
    assert_eq!(s(&v, &["boot", "packages", "linux-lts"]), "6.18.62-r0");
    assert!(arr(&v, &["warnings"]).is_empty(), "{:?}", v.get("warnings"));
    // load reads it, and boots it with Chimera's own tools.
    use crate::load::{commands, layout::Layout, target::Mapping, Parsed, Stage};
    let l = Layout::from_json(v).unwrap();
    let Ok(Parsed::Run(o)) = crate::load::parse_args(&["l.json".to_string()]) else { unreachable!() };
    let b = commands::build(&l, &o, &Mapping { pairs: vec![("vda".into(), "vdb".into())], ..Mapping::default() });
    let bt: String = b.stage(Stage::Boot).iter().map(commands::Cmd::shell).collect();
    assert!(bt.contains("chroot /mnt/bko-target update-initramfs -u -k all\n"), "{bt}");
    assert!(bt.contains("chroot /mnt/bko-target grub-install --target=i386-pc /dev/vdb\n"), "{bt}");
    assert!(bt.contains("chroot /mnt/bko-target env LANG=C grub-mkconfig -o /boot/grub/grub.cfg\n"), "{bt}");
    assert!(!bt.contains("update-grub"), "{bt}");
}

/// The chimera-uefi guest's boot, on uefi_shim's disk: GPT, the ESP as /boot
/// (vmlinuz-V and initrd.img-V on it), Chimera's systemd-boot without systemd
/// (LoaderInfo, apk's systemd-boot), gen-systemd-boot's entries, root ext4.
fn chimera_uefi() -> Tree {
    use crate::efi::{GLOBAL, LOADER};
    let t = Tree::new("scn-chimera-uefi");
    let v = "6.18.62-0-generic";
    let mut img = vec![0u8; 64 * MIB];
    write_gpt(
        &mut img,
        512,
        &GptSpec {
            disk_guid: "6f1c2c3d-0000-4000-8000-00000000bbbb",
            parts: vec![(ESP, "0b5f04ed-e731-4ac6-933e-83ff25657722", 2048, 73727, 0, ""), (LINUX, "11112222-3333-4444-5555-666677778888", 73728, 131038, 0, "")],
        },
    );
    put(&mut img, 2048, &fat::sample::fat32(71680, 0x1234_abcd, ""));
    put(&mut img, 73728, &ext::sample::image(0x4 | 0x1000, 0x2 | 0x40 | 0x80 | 0x200, 0x1 | 0x400, ""));
    let mut loader_info = vec![6u8, 0, 0, 0];
    for c in "systemd-boot 256.11".encode_utf16().chain([0]) {
        loader_info.extend(c.to_le_bytes());
    }
    let opts = "root=PARTUUID=11112222-3333-4444-5555-666677778888 ro console=ttyS0,115200n8";
    t.disk("vda", "254:0", &img, 512)
        .partition("vda", "vda1", "254:1", 1, 2048, 71680, &img)
        .partition("vda", "vda2", "254:2", 2, 73728, 57311, &img)
        .file("/sys/firmware/efi/fw_platform_size", "64\n")
        .file(&format!("/sys/firmware/efi/efivars/SecureBoot-{GLOBAL}"), [6u8, 0, 0, 0, 0])
        .file(&format!("/sys/firmware/efi/efivars/LoaderInfo-{LOADER}"), loader_info)
        .symlink("/dev/disk/by-partuuid/0b5f04ed-e731-4ac6-933e-83ff25657722", "../../vda1")
        .symlink("/dev/disk/by-partuuid/11112222-3333-4444-5555-666677778888", "../../vda2")
        .file("/proc/self/mountinfo", "26 1 254:2 / / rw,relatime shared:1 - ext4 /dev/vda2 rw\n27 26 254:1 / /boot rw,relatime shared:2 - vfat /dev/vda1 rw,fmask=0022\n")
        .file("/etc/fstab", "UUID=10111213-1415-1617-1819-1a1b1c1d1e1f / ext4 defaults 0 1\nPARTUUID=0b5f04ed-e731-4ac6-933e-83ff25657722 /boot vfat defaults 0 2\n")
        .file("/etc/os-release", "NAME=\"Chimera\"\nID=\"chimera\"\nPRETTY_NAME=\"Chimera Linux\"\n")
        .symlink("/lib", "usr/lib")
        .file(
            "/usr/lib/apk/db/installed",
            "C:Q1aaa=\nP:linux-lts\nV:6.18.62-r0\nA:x86_64\n\nC:Q1bbb=\nP:systemd-boot\nV:256.11-r5\nA:x86_64\n\nC:Q1ccc=\nP:systemd-boot-efi\nV:256.11-r5\nA:x86_64\n\nC:Q1ddd=\nP:initramfs-tools\nV:0.148.3-r2\nA:noarch\n\nC:Q1eee=\nP:efibootmgr\nV:18-r0\nA:x86_64\n\n",
        )
        .file("/boot/EFI/systemd/systemd-bootx64.efi", vec![1u8; 300])
        .file("/boot/EFI/BOOT/BOOTX64.EFI", vec![1u8; 300])
        .file("/boot/loader/loader.conf", "timeout 3\n")
        .file(&format!("/boot/loader/entries/chimera-{v}.conf"), format!("title Chimera Linux\nlinux /vmlinuz-{v}\ninitrd /initrd.img-{v}\noptions {opts} quiet splash\n"))
        .file(&format!("/boot/loader/entries/chimera-{v}-recovery.conf"), format!("title Chimera Linux\nlinux /vmlinuz-{v}\ninitrd /initrd.img-{v}\noptions {opts} single\n"))
        .file(&format!("/boot/vmlinuz-{v}"), "k")
        .file(&format!("/boot/initrd.img-{v}"), "i")
        .file(&format!("/boot/config-{v}"), "")
        .dir(&format!("/usr/lib/modules/{v}/kernel"))
        .file("/etc/initramfs-tools/initramfs.conf", "MODULES=most\nCOMPRESS=zstd\n")
        .file("/etc/default/systemd-boot-cmdline", "console=ttyS0,115200n8\n")
        .file("/proc/sys/kernel/osrelease", format!("{v}\n"))
        .file("/proc/sys/kernel/hostname", "chimera-uefi\n")
        .file("/proc/cmdline", format!("initrd=\\initrd.img-{v} {opts} quiet splash\n"));
    t
}

#[test]
fn chimera_uefi_guest() {
    let v = run(&chimera_uefi(), true, FakeRunner::default());
    assert_eq!(s(&v, &["os", "id"]), "chimera");
    assert_eq!(s(&v, &["firmware", "mode"]), "uefi");
    assert_eq!(s(&v, &["boot", "loader", "name"]), "systemd-boot");
    assert_eq!(s(&v, &["boot", "loader", "firmware"]), "uefi");
    assert_eq!(s(&v, &["boot", "loader", "package"]), "systemd-boot");
    assert_eq!(v.at(&["boot", "loader", "via_shim"]), Some(&Value::Bool(false)));
    assert_eq!(s(&v, &["boot", "packages", "systemd-boot"]), "256.11-r5");
    assert_eq!(s(&v, &["boot", "esps", "0", "mounted_at"]), "/boot");
    assert_eq!(arr(&v, &["boot", "systemd_boot", "entries"]).len(), 2);
    assert_eq!(s(&v, &["boot", "initramfs", "generator"]), "initramfs-tools");
    assert_eq!(s(&v, &["boot", "kernels", "installed", "0", "version"]), "6.18.62-0-generic");
    // load reinstalls systemd-boot, then gen-systemd-boot remakes the
    // entries after the initramfs; nothing GRUB's.
    use crate::load::{commands, layout::Layout, target::Mapping, Parsed, Stage};
    let l = Layout::from_json(v).unwrap();
    let Ok(Parsed::Run(o)) = crate::load::parse_args(&["l.json".to_string()]) else { unreachable!() };
    let b = commands::build(&l, &o, &Mapping { pairs: vec![("vda".into(), "vdb".into())], ..Mapping::default() });
    assert!(b.problems.is_empty(), "{:?}", b.problems);
    let bt: String = b.stage(Stage::Boot).iter().map(commands::Cmd::shell).collect();
    let initramfs = bt.find("chroot /mnt/bko-target update-initramfs -u -k all\n").expect(&bt);
    let bootctl = bt.find("chroot /mnt/bko-target bootctl install --no-variables\n").expect(&bt);
    let made = bt.find("chroot /mnt/bko-target sh -c 'gen-systemd-boot; r=$?;").expect(&bt);
    assert!(initramfs < bootctl && bootctl < made, "{bt}");
    assert!(!bt.contains("grub"), "{bt}");
}

/// CachyOS's Limine with the ESP as /boot, booted the way a machine with
/// no NVRAM entry for it boots (vm-run's fresh UEFI variables): through
/// Limine's copy at EFI/BOOT/BOOTX64.EFI, so no boot entry names Limine
/// and, with `loader_info` false, no LoaderInfo either.
fn limine_fallback(loader_info: bool) -> Tree {
    use crate::efi::{GLOBAL, LOADER};
    let t = Tree::new(if loader_info { "scn-limine-fallback" } else { "scn-limine-fallback-noinfo" });
    let v = "7.3.4-2-cachyos";
    let mut img = vec![0u8; 64 * MIB];
    write_gpt(
        &mut img,
        512,
        &GptSpec {
            disk_guid: "6f1c2c3d-0000-4000-8000-00000000cccc",
            parts: vec![(ESP, "1b5f04ed-e731-4ac6-933e-83ff25657722", 2048, 73727, 0, ""), (LINUX, "21112222-3333-4444-5555-666677778888", 73728, 131038, 0, "")],
        },
    );
    put(&mut img, 2048, &fat::sample::fat32(71680, 0x1234_abce, ""));
    put(&mut img, 73728, &ext::sample::image(0x4 | 0x1000, 0x2 | 0x40 | 0x80 | 0x200, 0x1 | 0x400, ""));
    let pkg = |n: &str, ver: &str| (format!("/var/lib/pacman/local/{n}-{ver}/desc"), format!("%NAME%\n{n}\n\n%VERSION%\n{ver}\n\n%ARCH%\nx86_64\n"));
    t.disk("vda", "254:0", &img, 512)
        .partition("vda", "vda1", "254:1", 1, 2048, 71680, &img)
        .partition("vda", "vda2", "254:2", 2, 73728, 57311, &img)
        .file("/sys/firmware/efi/fw_platform_size", "64\n")
        .file(&format!("/sys/firmware/efi/efivars/SecureBoot-{GLOBAL}"), [6u8, 0, 0, 0, 0])
        .symlink("/dev/disk/by-partuuid/1b5f04ed-e731-4ac6-933e-83ff25657722", "../../vda1")
        .symlink("/dev/disk/by-partuuid/21112222-3333-4444-5555-666677778888", "../../vda2")
        .file("/proc/self/mountinfo", "26 1 254:2 / / rw,relatime shared:1 - ext4 /dev/vda2 rw\n27 26 254:1 / /boot rw,relatime shared:2 - vfat /dev/vda1 rw,fmask=0077\n")
        .file("/etc/fstab", "UUID=10111213-1415-1617-1819-1a1b1c1d1e1f / ext4 defaults 0 1\nPARTUUID=1b5f04ed-e731-4ac6-933e-83ff25657722 /boot vfat umask=0077 0 2\n")
        .file("/etc/os-release", "NAME=\"CachyOS Linux\"\nID=cachyos\nID_LIKE=arch\nPRETTY_NAME=\"CachyOS\"\n")
        .file("/boot/EFI/limine/limine_x64.efi", vec![2u8; 300])
        .file("/boot/EFI/BOOT/BOOTX64.EFI", vec![2u8; 300])
        .file("/boot/limine.conf", "timeout: 5\n/CachyOS\n")
        .file(&format!("/boot/{}/linux-cachyos/vmlinuz", "e464d0233015401f967ec0aedf7f3cda"), "k")
        .file(&format!("/boot/{}/linux-cachyos/initramfs", "e464d0233015401f967ec0aedf7f3cda"), "i")
        .dir(&format!("/usr/lib/modules/{v}/kernel"))
        .file("/etc/mkinitcpio.conf", "HOOKS=(base udev autodetect microcode modconf block keyboard keymap consolefont filesystems fsck)\n")
        .file("/proc/sys/kernel/osrelease", format!("{v}\n"))
        .file("/proc/sys/kernel/hostname", "cachyos\n")
        .file("/proc/cmdline", "quiet nowatchdog rw root=PARTUUID=21112222-3333-4444-5555-666677778888\n");
    for (n, ver) in [("limine", "12.9.6-1"), ("limine-mkinitcpio-hook", "1.20.0-1"), ("mkinitcpio", "40-3"), ("linux-cachyos", "7.3.4-2"), ("efibootmgr", "18-3")] {
        let (path, desc) = pkg(n, ver);
        t.file(&path, desc);
    }
    if loader_info {
        let mut info = vec![6u8, 0, 0, 0];
        for c in "Limine 12.9.6".encode_utf16().chain([0]) {
            info.extend(c.to_le_bytes());
        }
        t.file(&format!("/sys/firmware/efi/efivars/LoaderInfo-{LOADER}"), info);
    }
    t
}

#[test]
fn limine_booted_at_the_fallback_path() {
    use crate::load::{commands, layout::Layout, target::Mapping, Parsed, Stage};
    for loader_info in [true, false] {
        let v = run(&limine_fallback(loader_info), true, FakeRunner::default());
        assert_eq!(s(&v, &["os", "id"]), "cachyos");
        assert_eq!(s(&v, &["boot", "loader", "name"]), "limine", "{}", v.at(&["boot", "loader"]).unwrap().compact());
        assert_eq!(s(&v, &["boot", "loader", "package"]), "limine");
        // load doesn't refuse it: the ESP brings Limine back, mkinitcpio
        // remakes the initramfs (its hook copies it into the machine-id
        // directory limine.conf boots).
        let l = Layout::from_json(v).unwrap();
        let Ok(Parsed::Run(o)) = crate::load::parse_args(&["l.json".to_string()]) else { unreachable!() };
        let b = commands::build(&l, &o, &Mapping { pairs: vec![("vda".into(), "vdb".into())], ..Mapping::default() });
        assert!(b.problems.is_empty(), "{loader_info}: {:?}", b.problems);
        let bt: String = b.stage(Stage::Boot).iter().map(commands::Cmd::shell).collect();
        assert!(bt.contains("chroot /mnt/bko-target mkinitcpio -P\n"), "{bt}");
        assert!(!bt.contains("grub"), "{bt}");
        assert!(b.notes.iter().any(|n| n.starts_with("Limine: ")), "{:?}", b.notes);
    }
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
fn uefi_shim() -> Tree {
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
    t
}

/// Chopin's own layout read back.
#[test]
fn uefi_gpt_nvme_shim() {
    let t = uefi_shim();
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
fn luks_lvm_tree() -> Tree {
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
    t
}

/// Debian's "Guided - use entire disk and set up encrypted LVM" read back.
#[test]
fn luks_lvm() {
    let luks_uuid = "6c3e5f0e-6b0a-4d6c-9a2e-8f7e1d2c3b4a";
    let t = luks_lvm_tree();
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

/// The VG UUID of lvm::sample::THIN, as device-mapper writes it.
const THIN_VG: &str = "VoSr00tAAABBBBCCCCDDDDEEEEFFFFFF";
const THIN_ESP: &str = "7a05e5e5-0000-4000-8000-000000000002";

/// Vanilla OS 3's disk on Debian 13 (docs/design/vanillaos.md; its ABRoot
/// isn't what this is about): GPT with vos-boot (ext4, /boot), vos-efi (the
/// ESP) and vos-root, the PV of lvm::sample::THIN's VG: `init` (ext4
/// vos-init), the thin pool `root` and its thin LVs `root-a` (btrfs vos-a,
/// /) and `root-b` (btrfs vos-b, at /part-future), each pool layer a
/// device-mapper device of its own (`-tdata`, `-tmeta`, `-tpool`, `-pool`
/// UUID suffixes).  shim and GRUB in EFI/vanilla.
fn thin_lvm_tree() -> Tree {
    use crate::efi::GLOBAL;
    let t = Tree::new("scn-thin-lvm");
    t.debian13();
    let mut img = vec![0u8; 64 * MIB];
    write_gpt(
        &mut img,
        512,
        &GptSpec {
            disk_guid: "7a05e5e5-0000-4000-8000-00000000d15c",
            parts: vec![
                (LINUX, "7a05e5e5-0000-4000-8000-000000000001", 2048, 18431, 0, "vos-boot"),
                (ESP, THIN_ESP, 18432, 90111, 0, "vos-efi"),
                (LINUX, "7a05e5e5-0000-4000-8000-000000000003", 90112, 131038, 0, "vos-root"),
            ],
        },
    );
    put(&mut img, 2048, &ext::sample::image(0x4 | 0x8 | 0x10 | 0x20, 0x2 | 0x40 | 0x80 | 0x200, 0x1 | 0x2 | 0x400, "vos-boot"));
    put(&mut img, 18432, &fat::sample::fat32(71680, 0x7a05_e5e5, "vos-efi"));
    put(&mut img, 90112, &lvm::sample::pv_with(512, 1 << 20, lvm::sample::THIN));
    let mut init = ext::sample::image(0x4 | 0x8 | 0x10 | 0x20, 0x2 | 0x40 | 0x80 | 0x200, 0x1 | 0x2 | 0x400, "vos-init");
    init[1024 + 104] = 0x20;
    let root_a = btrfs::sample::image("vos-a", 0x1 | 0x2 | 0x40 | 0x100 | 0x200);
    let mut root_b = root_a.clone();
    root_b[0x10020] = 0xc0;
    root_b[0x1012b..0x10130].copy_from_slice(b"vos-b");
    let lv = |id: &str| format!("LVM-{THIN_VG}{id}aaaabbbbccccddddeeeeffffff");
    let v_ = |n: &str| format!("/sys/firmware/efi/efivars/{n}-{GLOBAL}");
    let zero = vec![0u8; 0x20000];
    t.disk("vda", "254:0", &img, 512)
        .partition("vda", "vda1", "254:1", 1, 2048, 16384, &img)
        .partition("vda", "vda2", "254:2", 2, 18432, 71680, &img)
        .partition("vda", "vda3", "254:3", 3, 90112, 40927, &img)
        .file("/sys/block/vda/device/model", "")
        .file("/sys/block/vda/vda3/holders/dm-0", "")
        .file("/sys/block/vda/vda3/holders/dm-1", "")
        .file("/sys/block/vda/vda3/holders/dm-2", "");
    for (dm, devno, name, uuid, image, slaves) in [
        ("dm-0", "253:0", "vos--root-init", lv("1n1t00"), &init, vec!["vda3"]),
        ("dm-1", "253:1", "vos--root-root_tmeta", lv("tm3ta0") + "-tmeta", &zero, vec!["vda3"]),
        ("dm-2", "253:2", "vos--root-root_tdata", lv("td4ta0") + "-tdata", &zero, vec!["vda3"]),
        ("dm-3", "253:3", "vos--root-root-tpool", lv("p00l00") + "-tpool", &zero, vec!["dm-1", "dm-2"]),
        ("dm-4", "253:4", "vos--root-root--a", lv("r00ta0"), &root_a, vec!["dm-3"]),
        ("dm-5", "253:5", "vos--root-root--b", lv("r00tb0"), &root_b, vec!["dm-3"]),
        ("dm-6", "253:6", "vos--root-root", lv("p00l00") + "-pool", &zero, vec!["dm-3"]),
    ] {
        t.disk(dm, devno, image, 512).file(&format!("/sys/block/{dm}/dm/name"), format!("{name}\n")).file(&format!("/sys/block/{dm}/dm/uuid"), format!("{uuid}\n")).symlink(&format!("/dev/mapper/{name}"), &format!("../{dm}"));
        for s in slaves {
            t.file(&format!("/sys/block/{dm}/slaves/{s}"), "");
            if s.starts_with("dm-") {
                t.file(&format!("/sys/block/{s}/holders/{dm}"), "");
            }
        }
    }
    let (boot, a, b) = ("10111213-1415-1617-1819-1a1b1c1d1e1f", "b0b1b2b3-b4b5-b6b7-b8b9-babbbcbdbebf", "c0b1b2b3-b4b5-b6b7-b8b9-babbbcbdbebf");
    t.symlink(&format!("/dev/disk/by-partuuid/{THIN_ESP}"), "../../vda2")
        .file("/sys/firmware/efi/fw_platform_size", "64\n")
        .file(&v_("Boot0001"), efi_load_option("vanilla", 2, 18432, 71680, THIN_ESP, "\\EFI\\vanilla\\shimx64.efi"))
        .file(&v_("BootOrder"), [7u8, 0, 0, 0, 1, 0])
        .file(&v_("BootCurrent"), [6u8, 0, 0, 0, 1, 0])
        .file(&v_("SecureBoot"), [6u8, 0, 0, 0, 0])
        .file(
            "/proc/self/mountinfo",
            "26 1 253:4 / / ro,relatime shared:1 - btrfs /dev/mapper/vos--root-root--a ro,space_cache=v2,subvolid=5,subvol=/\n27 26 254:1 / /boot rw,relatime shared:2 - ext4 /dev/vda1 rw\n28 27 254:2 / /boot/efi rw,relatime shared:3 - vfat /dev/vda2 rw,fmask=0077,dmask=0077\n29 26 253:5 / /part-future rw,relatime shared:4 - btrfs /dev/mapper/vos--root-root--b rw,space_cache=v2,subvolid=5,subvol=/\n",
        )
        .file("/etc/fstab", format!("UUID={a} / btrfs defaults 0 0\nUUID={boot} /boot ext4 defaults 0 0\nUUID=7A05-E5E5 /boot/efi vfat umask=0077 0 2\nUUID={b} /part-future btrfs defaults 0 0\n"))
        .file("/boot/efi/EFI/vanilla/shimx64.efi", vec![1u8; 300])
        .file("/boot/efi/EFI/vanilla/grubx64.efi", vec![2u8; 400])
        .file("/boot/efi/EFI/vanilla/mmx64.efi", vec![3u8; 200])
        .file("/boot/efi/EFI/vanilla/grub.cfg", format!("search.fs_uuid {boot} root \nset prefix=($root)'/grub'\nconfigfile $prefix/grub.cfg\n"))
        .file("/var/lib/dpkg/status", "Package: grub-efi-amd64-signed\nStatus: install ok installed\nArchitecture: amd64\nVersion: 1+2.12+9+deb13u2\n\nPackage: shim-signed\nStatus: install ok installed\nArchitecture: amd64\nVersion: 1.47+15.8-1\n\nPackage: lvm2\nStatus: install ok installed\nArchitecture: amd64\nVersion: 2.03.31-2\n\nPackage: thin-provisioning-tools\nStatus: install ok installed\nArchitecture: amd64\nVersion: 1.1.0-4\n")
        .file("/boot/grub/x86_64-efi/normal.mod", "x");
    t
}

/// What btrfs answers for the two roots: no subvolumes.
fn thin_lvm_runner() -> FakeRunner {
    let mut r = FakeRunner::default();
    for m in ["/", "/part-future"] {
        r = r
            .with(&format!("btrfs subvolume list -a -p -u -q {m}"), "")
            .with(&format!("btrfs subvolume get-default {m}"), "ID 5 (FS_TREE)\n")
            .with(&format!("btrfs subvolume list -a -r {m}"), "")
            .with(&format!("btrfs property get -t s {m}"), "ro=false\n")
            .with(&format!("btrfs property get -t i {m}"), "");
    }
    r
}

/// The thin LVM disk planned, vda going to vdb: vgcfgrestore makes the PV,
/// the group and `init` with their UUIDs; lvcreate then makes the pool at
/// the dump's sizes and chunk size, with a spare as the source had, and
/// root-a and root-b in device-id order, by their names; the format stage
/// formats init and both thin LVs at their /dev/mapper names, with the
/// dump's UUIDs and labels, and nothing on the pool's own devices.
#[test]
fn thin_lvm() {
    use crate::load::{commands, layout::Layout, target::Mapping, Parsed, Stage};
    let v = run(&thin_lvm_tree(), true, thin_lvm_runner());
    assert!(arr(&v, &["warnings"]).is_empty(), "{:?}", v.get("warnings"));
    let vg = v.at(&["storage", "lvm", "volume_groups", "0"]).unwrap();
    assert_eq!(s(vg, &["name"]), "vos-root");
    assert_eq!(s(vg, &["physical_volumes", "0", "device"]), "vda3");
    assert_eq!(s(vg, &["logical_volumes", "2", "name"]), "root-a");
    assert_eq!(s(vg, &["logical_volumes", "2", "active_device"]), "dm-4");
    assert_eq!(s(vg, &["logical_volumes", "2", "segments", "0", "type"]), "thin");
    let dms = arr(&v, &["storage", "dm_devices"]);
    let tdata = dms.iter().position(|d| d.at(&["dm", "lv_layer"]).and_then(Value::as_str) == Some("tdata")).unwrap();
    let plan = |v: Value| {
        let l = Layout::from_json(v).unwrap();
        let Ok(Parsed::Run(o)) = crate::load::parse_args(&["l.json".to_string()]) else { unreachable!() };
        commands::build(&l, &o, &Mapping { pairs: vec![("vda".into(), "vdb".into())], ..Mapping::default() })
    };
    let b = plan(v.clone());
    assert!(b.problems.is_empty(), "{:?}", b.problems);
    let asm: String = b.stage(Stage::Assemble).iter().map(commands::Cmd::shell).collect();
    let lvm = |rest: &str| format!("--devicesfile '' --devices /dev/vdb3 {rest}\n");
    let lines = [
        format!("pvcreate {}", lvm("--uuid Hc7n2m-aaaa-bbbb-cccc-dddd-eeee-ffffff --restorefile /run/bko-holt/lvm/vos-root.vg /dev/vdb3")),
        format!("vgcfgrestore {}", lvm("--file /run/bko-holt/lvm/vos-root.vg vos-root")),
        format!("vgchange {}", lvm("--activate y vos-root")),
        format!("lvcreate {}", lvm("--yes --type thin-pool -n root -L 19456m --poolmetadatasize 1024m --chunksize 64k --discards passdown --zero y --poolmetadataspare y vos-root")),
        format!("lvcreate {}", lvm("--type thin -V 19456m -n root-a --thinpool root vos-root")),
        format!("lvcreate {}", lvm("--type thin -V 19456m -n root-b --thinpool root vos-root")),
    ];
    let mut at = 0;
    for l in &lines {
        let i = asm[at..].find(l.as_str()).unwrap_or_else(|| panic!("{l} not after {at}: {asm}")) + at;
        at = i + l.len();
    }
    assert_eq!(asm.lines().filter(|l| l.starts_with("lvcreate ")).count(), 3, "{asm}");
    // A pool that queues writes when full, as a new one does: no --errorwhenfull anywhere.
    assert!(!asm.contains("--errorwhenfull"), "{asm}");
    assert!(asm.contains("# volume group vos-root: thin pool root (data 19 GiB, metadata 1 GiB) made again by lvcreate, empty; its LV UUID, its data and metadata LVs', those of its thin LVs (vos-root/root-a, vos-root/root-b) and their device ids are new"), "{asm}");
    assert!(asm.contains("# thin LV vos-root/root-b: 19 GiB virtual, device id 2 on the source\n"), "{asm}");
    let vgfile = b.stage(Stage::Assemble).iter().find_map(|c| c.write.as_ref().and(c.stdin.clone())).unwrap();
    assert!(vgfile.contains("\t\tinit {\n") && !vgfile.contains("root-a") && !vgfile.contains("pmspare") && !vgfile.contains("thin"), "{vgfile}");
    let fmt: String = b.stage(Stage::Format).iter().map(commands::Cmd::shell).collect();
    assert!(fmt.contains("-L vos-init") && fmt.contains("-U 20111213-1415-1617-1819-1a1b1c1d1e1f") && fmt.contains("/dev/mapper/vos--root-init\n"), "{fmt}");
    for (label, uuid, dev) in [("vos-a", "b0b1b2b3-b4b5-b6b7-b8b9-babbbcbdbebf", "vos--root-root--a"), ("vos-b", "c0b1b2b3-b4b5-b6b7-b8b9-babbbcbdbebf", "vos--root-root--b")] {
        let l = fmt.lines().find(|l| l.starts_with("mkfs.btrfs") && l.ends_with(&format!("/dev/mapper/{dev}"))).unwrap_or_else(|| panic!("{dev}: {fmt}"));
        assert!(l.contains(&format!("-L {label}")) && l.contains(&format!("-U {uuid}")), "{l}");
    }
    assert!(!fmt.contains("tdata") && !fmt.contains("tmeta") && !fmt.contains("tpool"), "{fmt}");
    let mnt: String = b.stage(Stage::Mount).iter().map(commands::Cmd::shell).collect();
    assert!(mnt.contains("/dev/mapper/vos--root-root--a /mnt/bko-target\n"), "{mnt}");

    // A probe that found root-a's filesystem on the pool's data device
    // (where its blocks are): still formatted once, on root-a.
    let mut w = v.clone();
    let c = dms.iter().find(|d| s(d, &["name"]) == "dm-4").unwrap().get("content").cloned().unwrap();
    let Some(Value::Arr(all)) = w.get("storage").and_then(|s| s.get("dm_devices")).cloned() else { panic!() };
    let mut all = all;
    let Value::Obj(o) = &mut all[tdata] else { panic!() };
    o.iter_mut().find(|(k, _)| k == "content").unwrap().1 = c;
    set_at(&mut w, &["storage", "dm_devices"], Value::Arr(all));
    let fmt: String = plan(w).stage(Stage::Format).iter().map(commands::Cmd::shell).collect();
    assert_eq!(fmt.matches("mkfs.btrfs").count(), 2, "{fmt}");

    // Refused: a thin snapshot.
    let text = v.compact().replacen("\"device_id\":2", "\"device_id\":2,\"origin\":\"root-a\"", 1);
    let w = crate::json::parse(&text).unwrap();
    assert_ne!(w.compact(), v.compact());
    assert_eq!(plan(w).problems, vec!["volume group vos-root: thin LV vos-root/root-b is a thin snapshot of root-a; version 1 doesn't make thin snapshots"]);

    // A pool without zeroing, full-error on, its data striped; root-b
    // read-only, tagged, with ACTIVATION_SKIP: the flags go on at close,
    // before the group is deactivated.
    let c = v.compact();
    let at = c.find("\"name\":\"root_tdata\"").unwrap();
    let text = format!("{}{}", &c[..at], c[at..].replacen("\"stripe_count\":1", "\"stripe_count\":2,\"stripe_size\":128", 1))
        .replacen(",\"zero_new_blocks\":1", "", 1)
        .replacen("\"name\":\"root\",\"id\":\"p00l00-aaaa-bbbb-cccc-dddd-eeee-ffffff\",\"status\":[\"READ\",\"WRITE\",\"VISIBLE\"]", "\"name\":\"root\",\"id\":\"p00l00-aaaa-bbbb-cccc-dddd-eeee-ffffff\",\"status\":[\"READ\",\"WRITE\",\"VISIBLE\",\"ERROR_WHEN_FULL\"]", 1)
        .replacen("\"id\":\"r00tb0-aaaa-bbbb-cccc-dddd-eeee-ffffff\",\"status\":[\"READ\",\"WRITE\",\"VISIBLE\"],\"flags\":[]", "\"id\":\"r00tb0-aaaa-bbbb-cccc-dddd-eeee-ffffff\",\"status\":[\"READ\",\"VISIBLE\"],\"flags\":[\"ACTIVATION_SKIP\"],\"tags\":[\"keep\"]", 1);
    let b = plan(crate::json::parse(&text).unwrap());
    assert!(b.problems.is_empty(), "{:?}", b.problems);
    let asm: String = b.stage(Stage::Assemble).iter().map(commands::Cmd::shell).collect();
    assert!(asm.contains(&format!("lvcreate {}", lvm("--yes --type thin-pool -n root -L 19456m --poolmetadatasize 1024m -i 2 -I 64k --chunksize 64k --discards passdown --zero n --poolmetadataspare y vos-root"))), "{asm}");
    assert!(asm.contains(&format!("lvchange {}", lvm("--errorwhenfull y vos-root/root"))), "{asm}");
    // (lvm2 2.03's lvcreate refuses --errorwhenfull with --type thin-pool;
    // lvchange fails setting it to what it is already, so a pool queueing
    // writes, as the first plan's, gets no lvchange.)
    assert!(!asm.lines().any(|l| l.contains("--type thin-pool") && l.contains("--errorwhenfull")), "{asm}");
    assert!(asm.contains(&format!("lvcreate {}", lvm("--type thin -V 19456m -n root-b --thinpool root --addtag keep vos-root"))), "{asm}");
    let close: String = b.stage(Stage::Close).iter().map(commands::Cmd::shell).collect();
    let (skip, ro) = (close.find(&format!("lvchange {}", lvm("--setactivationskip y vos-root/root-b"))).expect(&close), close.find(&format!("lvchange {}", lvm("--permission r vos-root/root-b"))).expect(&close));
    let off = close.find("--activate n vos-root").expect(&close);
    assert!(skip < off && ro < off && !close.contains("root-a"), "{close}");
}

/// Vanilla OS's VG `vos-var` on vda4: one linear LV `var` (btrfs vos-var).
const VOS_VAR_VG: &str = r#"vos-var {
id = "VaRvg0-AAAA-BBBB-CCCC-DDDD-EEEE-FFFFFF"
seqno = 2
format = "lvm2"			# informational
status = ["RESIZEABLE", "READ", "WRITE"]
flags = []
extent_size = 8192		# 4 Megabytes
max_lv = 0
max_pv = 0
metadata_copies = 0

physical_volumes {

pv0 {
id = "VaRpv0-aaaa-bbbb-cccc-dddd-eeee-ffffff"
device = "/dev/vda4"	# Hint only

status = ["ALLOCATABLE"]
flags = []
dev_size = 49152000	# 23.4375 Gigabytes
pe_start = 2048
pe_count = 5999	# 23.4336 Gigabytes
}
}

logical_volumes {

var {
id = "v4r000-aaaa-bbbb-cccc-dddd-eeee-ffffff"
status = ["READ", "WRITE", "VISIBLE"]
flags = []
creation_time = 1762300004
creation_host = "vanilla"
segment_count = 1

segment1 {
start_extent = 0
extent_count = 5999	# 23.4336 Gigabytes

type = "striped"
stripe_count = 1	# linear

stripes = [
"pv0", 0
]
}
}
}

}
# Generated by LVM2 version 2.03.31(2) (2025-02-27): Wed Nov  5 00:00:04 2025

contents = "Text Format Volume Group"
version = 1

description = "Created *after* executing 'lvcreate -l 100%FREE -n var vos-var'"

creation_host = "vanilla"	# Linux vanilla 6.17.8-amd64 x86_64
creation_time = 1762300004	# Wed Nov  5 00:00:04 2025
"#;

const VANILLA_K: &str = "6.17.8-amd64";
const VANILLA_KB: &str = "6.18.1-amd64";
const VANILLA_A: &str = "b0b1b2b3-b4b5-b6b7-b8b9-babbbcbdbebf";
const VANILLA_B: &str = "c0b1b2b3-b4b5-b6b7-b8b9-babbbcbdbebf";
const VANILLA_INIT: &str = "20111213-1415-1617-1819-1a1b1c1d1e1f";

/// Vanilla OS 3 as its installer lays it out (docs/design/vanillaos.md):
/// GPT vos-boot (ext4 /boot), vos-efi (the ESP), vos-root (PV of VG
/// vos-root: the init LV, ext4 vos-init, and the thin pool `root` with
/// root-a and root-b, btrfs vos-a and vos-b) and vos-var (PV of VG
/// vos-var: btrfs vos-var at /var).  Running from vos-a, read-only, with
/// ABRoot's /etc overlay; fstab as mount-sys leaves it (/ prepended, /var
/// dropped); the installer's master grub.cfg and vos-a's abroot.cfg in
/// the init LV, which isn't mounted: dump reads it, and vos-b, through
/// read-only mounts of its own in /run/bko-holt (here, the trees already
/// there).  B_USED: vos-b filled by an `abroot upgrade` (its kernel and
/// an upgrade's abroot.cfg in init/vos-b), else a fresh install's empty B.
fn vanilla_tree(b_used: bool) -> Tree {
    use crate::efi::GLOBAL;
    let t = Tree::new("scn-vanilla");
    let mut img = vec![0u8; 80 * MIB];
    write_gpt(
        &mut img,
        512,
        &GptSpec {
            disk_guid: "7a05e5e5-0000-4000-8000-00000000d15d",
            parts: vec![
                (LINUX, "7a05e5e5-0000-4000-8000-000000000001", 2048, 18431, 0, "vos-boot"),
                (ESP, THIN_ESP, 18432, 90111, 0, "vos-efi"),
                (LINUX, "7a05e5e5-0000-4000-8000-000000000003", 90112, 131071, 0, "vos-root"),
                (LINUX, "7a05e5e5-0000-4000-8000-000000000004", 131072, 163806, 0, "vos-var"),
            ],
        },
    );
    put(&mut img, 2048, &ext::sample::image(0x4 | 0x8 | 0x10 | 0x20, 0x2 | 0x40 | 0x80 | 0x200, 0x1 | 0x2 | 0x400, "vos-boot"));
    put(&mut img, 18432, &fat::sample::fat32(71680, 0x7a05_e5e5, "vos-efi"));
    put(&mut img, 90112, &lvm::sample::pv_with(512, 1 << 20, lvm::sample::THIN));
    let mut var_pv = lvm::sample::pv_with(512, 1 << 20, VOS_VAR_VG);
    var_pv[544..576].copy_from_slice(b"VaRpv0aaaabbbbccccddddeeeeffffff");
    put(&mut img, 131072, &var_pv);
    let mut init = ext::sample::image(0x4 | 0x8 | 0x10 | 0x20, 0x2 | 0x40 | 0x80 | 0x200, 0x1 | 0x2 | 0x400, "vos-init");
    init[1024 + 104] = 0x20;
    let root_a = btrfs::sample::image("vos-a", 0x1 | 0x2 | 0x40 | 0x100 | 0x200);
    let mut root_b = root_a.clone();
    root_b[0x10020] = 0xc0;
    root_b[0x1012b..0x10130].copy_from_slice(b"vos-b");
    let mut var = btrfs::sample::image("vos-var", 0x1 | 0x2 | 0x40 | 0x100 | 0x200);
    var[0x10020] = 0xd0;
    let lv = |id: &str| format!("LVM-{THIN_VG}{id}aaaabbbbccccddddeeeeffffff");
    let v_ = |n: &str| format!("/sys/firmware/efi/efivars/{n}-{GLOBAL}");
    let zero = vec![0u8; 0x20000];
    t.disk("vda", "254:0", &img, 512)
        .partition("vda", "vda1", "254:1", 1, 2048, 16384, &img)
        .partition("vda", "vda2", "254:2", 2, 18432, 71680, &img)
        .partition("vda", "vda3", "254:3", 3, 90112, 40960, &img)
        .partition("vda", "vda4", "254:4", 4, 131072, 32735, &img)
        .file("/sys/block/vda/device/model", "")
        .file("/sys/block/vda/vda3/holders/dm-0", "")
        .file("/sys/block/vda/vda3/holders/dm-1", "")
        .file("/sys/block/vda/vda3/holders/dm-2", "")
        .file("/sys/block/vda/vda4/holders/dm-7", "");
    for (dm, devno, name, uuid, image, slaves) in [
        ("dm-0", "253:0", "vos--root-init", lv("1n1t00"), &init, vec!["vda3"]),
        ("dm-1", "253:1", "vos--root-root_tmeta", lv("tm3ta0") + "-tmeta", &zero, vec!["vda3"]),
        ("dm-2", "253:2", "vos--root-root_tdata", lv("td4ta0") + "-tdata", &zero, vec!["vda3"]),
        ("dm-3", "253:3", "vos--root-root-tpool", lv("p00l00") + "-tpool", &zero, vec!["dm-1", "dm-2"]),
        ("dm-4", "253:4", "vos--root-root--a", lv("r00ta0"), &root_a, vec!["dm-3"]),
        ("dm-5", "253:5", "vos--root-root--b", lv("r00tb0"), &root_b, vec!["dm-3"]),
        ("dm-6", "253:6", "vos--root-root", lv("p00l00") + "-pool", &zero, vec!["dm-3"]),
        ("dm-7", "253:7", "vos--var-var", "LVM-VaRvg0AAAABBBBCCCCDDDDEEEEFFFFFFv4r000aaaabbbbccccddddeeeeffffff".to_string(), &var, vec!["vda4"]),
    ] {
        t.disk(dm, devno, image, 512).file(&format!("/sys/block/{dm}/dm/name"), format!("{name}\n")).file(&format!("/sys/block/{dm}/dm/uuid"), format!("{uuid}\n")).symlink(&format!("/dev/mapper/{name}"), &format!("../{dm}"));
        for s in slaves {
            t.file(&format!("/sys/block/{dm}/slaves/{s}"), "");
            if s.starts_with("dm-") {
                t.file(&format!("/sys/block/{s}/holders/{dm}"), "");
            }
        }
    }
    let boot = "10111213-1415-1617-1819-1a1b1c1d1e1f";
    let base_cfg = "{\n    \"maxParallelDownloads\": 2,\n    \"registry\": \"ghcr.io\",\n    \"name\": \"vanilla-os/gnome\",\n    \"tag\": \"main\",\n    \"updateInitramfsCmd\": \"lpkg --unlock && /usr/sbin/update-initramfs -u && lpkg --lock\",\n    \"partLabelVar\": \"vos-var\",\n    \"partLabelA\": \"vos-a\",\n    \"partLabelB\": \"vos-b\",\n    \"partLabelBoot\": \"vos-boot\",\n    \"partLabelEfi\": \"vos-efi\",\n    \"PartCryptVar\": \"/dev/mapper/vos--var-var\",\n\n    \"thinProvisioning\": false,\n    \"thinInitVolume\": \"\"\n}\n";
    // The installer's: the image's with its last line cut, keys appended.
    let cut: Vec<&str> = base_cfg.lines().collect();
    let etc_cfg = format!("{},\n  \"PartCryptVar\": \"/dev/mapper/vos--var-var\",\n  \"thinProvisioning\": true,\n    \"thinInitVolume\": \"vos-init\"\n}}\n", cut[..cut.len() - 1].join("\n"));
    let os = "PRETTY_NAME=\"Vanilla OS 3.0\"\nNAME=\"Vanilla OS\"\nVERSION_ID=\"3.0\"\nVERSION=\"3.0 Reunion\"\nVERSION_CODENAME=reunion\nID=vanilla\nID_LIKE=debian\nHOME_URL=\"https://vanillaos.org/\"\n";
    let init_at = "/run/bko-holt/abroot-vos-init";
    t.file("/etc/os-release", os)
        .file("/usr/lib/os-release", os)
        .file("/usr/share/abroot/abroot.json", base_cfg)
        .file("/etc/abroot/abroot.json", etc_cfg)
        .file("/etc/abroot/kargs", "quiet splash bgrt_disable $vt_handoff lsm=integrity\n")
        .file("/abimage.abr", "{\n    \"digest\":\"sha256:a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1\",\n    \"timestamp\":\"2026-11-06T10:00:00Z\",\n    \"image\":\"ghcr.io/vanilla-os/gnome:main\"\n}\n")
        .file(&format!("/usr/lib/modules/{VANILLA_K}/modules.dep"), "")
        .file("/proc/sys/kernel/osrelease", format!("{VANILLA_K}\n"))
        .file("/proc/cmdline", format!("BOOT_IMAGE=(lvm/vos--root-init)/vos-a/vmlinuz-{VANILLA_K} root=UUID={VANILLA_A} quiet splash bgrt_disable lsm=integrity\n"))
        .dir("/boot/init")
        .dir("/part-future")
        .file("/boot/grub/grub.cfg", crate::abroot::tests_master())
        .file("/boot/grub/x86_64-efi/normal.mod", "x")
        .file(&format!("{init_at}/vos-a/vmlinuz-{VANILLA_K}"), vec![7u8; 300])
        .file(&format!("{init_at}/vos-a/initrd.img-{VANILLA_K}"), vec![8u8; 500])
        .file(&format!("{init_at}/vos-a/config-{VANILLA_K}"), "CONFIG_X=y\n")
        .file(&format!("{init_at}/vos-a/System.map-{VANILLA_K}"), "0 T x\n")
        .file(
            &format!("{init_at}/vos-a/abroot.cfg"),
            format!("insmod gzio\ninsmod part_gpt\ninsmod ext2\nlinux   (lvm/vos--root-init)/vos-a/vmlinuz-{VANILLA_K} root=UUID={VANILLA_A} quiet splash bgrt_disable $vt_handoff lsm=integrity\ninitrd  (lvm/vos--root-init)/vos-a/initrd.img-{VANILLA_K}\n"),
        )
        .dir(&format!("{init_at}/vos-b"))
        .dir("/run/bko-holt/abroot-vos-b");
    if b_used {
        t.file(&format!("{init_at}/vos-b/vmlinuz-{VANILLA_KB}"), vec![7u8; 310])
            .file(&format!("{init_at}/vos-b/initrd.img-{VANILLA_KB}"), vec![8u8; 510])
            .file(&format!("{init_at}/vos-b/config-{VANILLA_KB}"), "CONFIG_X=y\n")
            .file(
                &format!("{init_at}/vos-b/abroot.cfg"),
                // An upgrade's: grub-mkconfig's whole output, its 00_header's
                // font search (of vos-b's own UUID) first.
                format!(
                    "{}### BEGIN /etc/grub.d/10_linux ###\n  search --no-floppy --fs-uuid --set=root {VANILLA_INIT}\n  linux   /vos-b/vmlinuz-{VANILLA_KB} root=/dev/mapper/vos--root-root--b quiet splash bgrt_disable $vt_handoff lsm=integrity\n  initrd  /vos-b/initrd.img-{VANILLA_KB}\n### END /etc/grub.d/10_linux ###\n",
                    crate::abroot::tests_upgrade_header()
                ),
            )
            .file("/run/bko-holt/abroot-vos-b/abimage.abr", "{\"digest\":\"sha256:b2b2b2b2b2b2b2b2b2b2b2b2b2b2b2b2b2b2b2b2b2b2b2b2b2b2b2b2b2b2b2b2\",\"timestamp\":\"2026-11-10T10:00:00Z\",\"image\":\"ghcr.io/vanilla-os/gnome:main\"}")
            .file(&format!("/run/bko-holt/abroot-vos-b/usr/lib/modules/{VANILLA_KB}/modules.dep"), "");
    }
    t.symlink(&format!("/dev/disk/by-partuuid/{THIN_ESP}"), "../../vda2")
        .file("/sys/firmware/efi/fw_platform_size", "64\n")
        .file(&v_("Boot0001"), efi_load_option("vanilla", 2, 18432, 71680, THIN_ESP, "\\EFI\\vanilla\\shimx64.efi"))
        .file(&v_("BootOrder"), [7u8, 0, 0, 0, 1, 0])
        .file(&v_("BootCurrent"), [6u8, 0, 0, 0, 1, 0])
        .file(&v_("SecureBoot"), [6u8, 0, 0, 0, 1])
        .file(
            "/proc/self/mountinfo",
            "26 1 253:4 / / ro,relatime shared:1 - btrfs /dev/mapper/vos--root-root--a ro,space_cache=v2,subvolid=5,subvol=/\n27 26 0:36 / /var rw,relatime shared:2 - btrfs /dev/mapper/vos--var-var rw,space_cache=v2,subvolid=5,subvol=/\n28 26 0:37 / /etc rw,relatime shared:3 - overlay overlay rw,lowerdir=/etc,upperdir=/var/lib/abroot/etc/vos-a,workdir=/var/lib/abroot/etc/vos-a-work\n29 26 254:1 / /boot rw,relatime shared:4 - ext4 /dev/vda1 rw\n30 29 254:2 / /boot/efi rw,relatime shared:5 - vfat /dev/vda2 rw,fmask=0077,dmask=0077\n",
        )
        .file("/etc/fstab", format!("UUID={VANILLA_A} / btrfs ro,defaults 0 0\nUUID={boot} /boot ext4 defaults 0 2\nUUID=7A05-E5E5 /boot/efi vfat umask=0077 0 2\n"))
        .file("/boot/efi/EFI/vanilla/shimx64.efi", vec![1u8; 300])
        .file("/boot/efi/EFI/vanilla/grubx64.efi", vec![2u8; 400])
        .file("/boot/efi/EFI/vanilla/mmx64.efi", vec![3u8; 200])
        .file("/boot/efi/EFI/vanilla/grub.cfg", format!("search.fs_uuid {boot} root \nset prefix=($root)'/grub'\nconfigfile $prefix/grub.cfg\n"))
        .file("/var/lib/dpkg/status", "Package: grub-efi-amd64-signed\nStatus: install ok installed\nArchitecture: amd64\nVersion: 1+2.12+9+deb13u2\n\nPackage: shim-signed\nStatus: install ok installed\nArchitecture: amd64\nVersion: 1.47+15.8-1\n\nPackage: initramfs-tools\nStatus: install ok installed\nArchitecture: all\nVersion: 0.148.3\n\nPackage: lvm2\nStatus: install ok installed\nArchitecture: amd64\nVersion: 2.03.31-2\n\nPackage: thin-provisioning-tools\nStatus: install ok installed\nArchitecture: amd64\nVersion: 1.1.0-4\n\nPackage: abroot\nStatus: install ok installed\nArchitecture: amd64\nVersion: 2.6.0\n");
    t
}

/// What btrfs answers for / and /var (no subvolumes), and the read-only
/// mounts dump makes of the init LV and vos-b.
fn vanilla_runner() -> FakeRunner {
    let mut r = FakeRunner::default();
    for m in ["/", "/var"] {
        r = r
            .with(&format!("btrfs subvolume list -a -p -u -q {m}"), "")
            .with(&format!("btrfs subvolume get-default {m}"), "ID 5 (FS_TREE)\n")
            .with(&format!("btrfs subvolume list -a -r {m}"), "")
            .with(&format!("btrfs property get -t s {m}"), "ro=false\n")
            .with(&format!("btrfs property get -t i {m}"), "");
    }
    for (ty, node, label) in [("ext4", "vos--root-init", "vos-init"), ("btrfs", "vos--root-root--b", "vos-b")] {
        let at = format!("/run/bko-holt/abroot-{label}");
        r = r.with(&format!("mount -t {ty} -o {} /dev/mapper/{node} {at}", crate::abroot::ro_options(ty)), "").with(&format!("umount {at}"), "");
    }
    r
}

fn vanilla_plan(v: &Value, extra: &[&str]) -> crate::load::commands::Built {
    use crate::load::{commands, layout::Layout, target::Mapping, Parsed};
    let l = Layout::from_json(v.clone()).unwrap();
    let mut a = vec!["l.json".to_string()];
    a.extend(extra.iter().map(|s| s.to_string()));
    let Ok(Parsed::Run(o)) = crate::load::parse_args(&a) else { unreachable!() };
    commands::build(&l, &o, &Mapping { pairs: vec![("vda".into(), "vdb".into())], ..Mapping::default() })
}

/// Vanilla OS 3 with both roots used, running from vos-a: boot.abroot
/// (docs/design/vanillaos.md, V2) from abroot.json's merged keys, the
/// master grub.cfg, the init LV and vos-b read through dump's read-only
/// mounts; backup_sources in ABRoot's tree with the two unmounted
/// filesystems' mounts; the init LV's kernels.  Planned (V4): both VGs (the
/// thin pool by lvcreate), every filesystem with its label and UUID, the
/// tree mounted with boot/init, part-future and /var, and a boot stage
/// that regenerates nothing: the consistency check, the NVRAM entry, the
/// log.
#[test]
fn vanilla_guest() {
    use crate::load::{commands::Cmd, Stage};
    let v = run(&vanilla_tree(true), true, vanilla_runner());
    assert!(arr(&v, &["warnings"]).is_empty(), "{:?}", v.get("warnings"));
    assert_eq!(s(&v, &["os", "id"]), "vanilla");
    let a = v.at(&["boot", "abroot"]).expect("boot.abroot");
    assert_eq!(s(a, &["present"]), "vos-a");
    assert_eq!(s(a, &["future"]), "vos-b");
    assert_eq!(a.get("thin_two_roots"), Some(&Value::Bool(true)));
    assert_eq!(a.get("operation_lock"), Some(&Value::Bool(false)));
    assert_eq!(a.at(&["config", "thinProvisioning"]), Some(&Value::Bool(true)));
    assert_eq!(s(a, &["config", "thinInitVolume"]), "vos-init");
    assert_eq!(s(a, &["config", "PartCryptVar"]), "/dev/mapper/vos--var-var");
    assert_eq!(arr(a, &["config", "files"]).len(), 2);
    assert_eq!(s(a, &["grub", "default"]), "0");
    assert_eq!(s(a, &["grub", "default_label"]), "vos-a");
    assert_eq!(s(a, &["grub", "entries", "1", "title"]), "Previous State (B)");
    assert_eq!(s(a, &["grub", "entries", "1", "configfile"]), "/vos-b/abroot.cfg");
    assert_eq!(a.at(&["grub", "future_cfg"]), Some(&Value::Bool(false)));
    assert_eq!(s(a, &["init", "device"]), "dm-0");
    assert_eq!(s(a, &["init", "uuid"]), VANILLA_INIT);
    assert_eq!(a.at(&["init", "read"]), Some(&Value::Bool(true)));
    assert_eq!(s(a, &["var", "dm_name"]), "vos--var-var");
    assert_eq!(s(a, &["kargs"]), "quiet splash bgrt_disable $vt_handoff lsm=integrity");
    let (ra, rb) = (a.at(&["roots", "0"]).unwrap(), a.at(&["roots", "1"]).unwrap());
    assert_eq!((s(ra, &["label"]), s(ra, &["device"]), s(ra, &["uuid"])), ("vos-a", "dm-4", VANILLA_A));
    assert_eq!(ra.get("present"), Some(&Value::Bool(true)));
    assert!(s(ra, &["abimage", "digest"]).starts_with("sha256:a1a1"));
    assert_eq!(s(ra, &["init", "abroot_cfg", "root"]), format!("UUID={VANILLA_A}"));
    assert_eq!(arr(ra, &["init", "kernels"]), &vec![Value::from(VANILLA_K)]);
    assert_eq!((s(rb, &["label"]), s(rb, &["device"])), ("vos-b", "dm-5"));
    assert_eq!((rb.get("read"), rb.get("empty")), (Some(&Value::Bool(true)), Some(&Value::Bool(false))));
    assert!(s(rb, &["abimage", "digest"]).starts_with("sha256:b2b2"));
    assert_eq!(s(rb, &["init", "abroot_cfg", "search_uuid"]), VANILLA_INIT);
    assert_eq!(s(rb, &["init", "abroot_cfg", "root"]), "/dev/mapper/vos--root-root--b");
    // The init LV's kernels, each marked with its root; nothing in /boot.
    let ks = arr(&v, &["boot", "kernels", "installed"]);
    assert_eq!(ks.len(), 2, "{ks:?}");
    assert_eq!((s(&ks[0], &["version"]), s(&ks[0], &["dir"]), s(&ks[0], &["root_label"])), (VANILLA_K, "/boot/init/vos-a", "vos-a"));
    assert_eq!((ks[0].get("running"), ks[0].get("modules_installed")), (Some(&Value::Bool(true)), Some(&Value::Bool(true))));
    assert_eq!((s(&ks[1], &["version"]), s(&ks[1], &["root_label"])), (VANILLA_KB, "vos-b"));
    assert_eq!(u(&ks[1], &["vmlinuz_bytes"]), 310);
    // GRUB through shim, as for any Debian.
    assert_eq!(s(&v, &["boot", "loader", "name"]), "grub");
    assert_eq!(v.at(&["boot", "loader", "via_shim"]), Some(&Value::Bool(true)));
    // backup_sources: ABRoot's tree; the two unmounted ones with no `from`
    // and the mount a backup makes; `/` with the overlay's note.
    let bs = arr(&v, &["storage", "backup_sources"]);
    let paths: Vec<&str> = bs.iter().map(|b| b.get("path").and_then(Value::as_str).unwrap()).collect();
    assert_eq!(paths, vec!["/", "/boot", "/boot/efi", "/boot/init", "/part-future", "/var"]);
    assert!(s(&bs[0], &["note"]).contains("mount --bind / DIR"));
    for (i, node, ty, o) in [(3, "/dev/mapper/vos--root-init", "ext4", "ro,noload,nodev,nosuid,noexec"), (4, "/dev/mapper/vos--root-root--b", "btrfs", "ro,rescue=nologreplay,nodev,nosuid,noexec")] {
        assert!(bs[i].get("from").is_some_and(Value::is_null), "{:?}", bs[i]);
        assert_eq!((s(&bs[i], &["mount", "device"]), s(&bs[i], &["mount", "fstype"]), s(&bs[i], &["mount", "options"])), (node, ty, o));
        assert!(s(&bs[i], &["note"]).starts_with("not mounted on the running system"));
    }
    assert!(bs[5].get("mount").is_none() && s(&bs[5], &["from"]) == "/var");

    let b = vanilla_plan(&v, &[]);
    assert!(b.problems.is_empty(), "{:?}", b.problems);
    assert_eq!(crate::other::restored_marker(&v), "abimage.abr");
    assert!(b.notes.iter().any(|n| n.contains("restored as they were; no initramfs, no grub-install, no update-grub")), "{:?}", b.notes);
    assert!(b.notes.iter().any(|n| n.contains("detached")), "{:?}", b.notes);
    assert!(!b.notes.iter().any(|n| n.contains("was empty")), "{:?}", b.notes);
    let text = |st: Stage| b.stage(st).iter().map(Cmd::shell).collect::<String>();
    // Assemble: both VGs, the thin pool and its thin LVs by lvcreate.
    let asm = text(Stage::Assemble);
    for l in [
        "vgcfgrestore --devicesfile '' --devices /dev/vdb3 --file /run/bko-holt/lvm/vos-root.vg vos-root\n",
        "lvcreate --devicesfile '' --devices /dev/vdb3 --yes --type thin-pool -n root -L 19456m --poolmetadatasize 1024m --chunksize 64k --discards passdown --zero y --poolmetadataspare y vos-root\n",
        "lvcreate --devicesfile '' --devices /dev/vdb3 --type thin -V 19456m -n root-a --thinpool root vos-root\n",
        "lvcreate --devicesfile '' --devices /dev/vdb3 --type thin -V 19456m -n root-b --thinpool root vos-root\n",
        "pvcreate --devicesfile '' --devices /dev/vdb4 --uuid VaRpv0-aaaa-bbbb-cccc-dddd-eeee-ffffff --restorefile /run/bko-holt/lvm/vos-var.vg /dev/vdb4\n",
        "vgcfgrestore --devicesfile '' --devices /dev/vdb4 --file /run/bko-holt/lvm/vos-var.vg vos-var\n",
    ] {
        assert!(asm.contains(l), "{l}: {asm}");
    }
    // Format: every filesystem with its label and UUID.
    let fmt = text(Stage::Format);
    let line = |end: &str| fmt.lines().find(|l| l.starts_with("mk") && l.split_whitespace().any(|w| w == end)).unwrap_or_else(|| panic!("{end}: {fmt}")).to_string();
    for (end, label, uuid) in [
        ("/dev/mapper/vos--root-root--a", "vos-a", VANILLA_A),
        ("/dev/mapper/vos--root-root--b", "vos-b", VANILLA_B),
        ("/dev/mapper/vos--root-init", "vos-init", VANILLA_INIT),
        ("/dev/mapper/vos--var-var", "vos-var", "d0b1b2b3-b4b5-b6b7-b8b9-babbbcbdbebf"),
        ("/dev/vdb1", "vos-boot", "10111213-1415-1617-1819-1a1b1c1d1e1f"),
    ] {
        let l = line(end);
        assert!(l.contains(&format!("-L {label}")) && l.contains(&format!("-U {uuid}")), "{l}");
    }
    let esp = line("/dev/vdb2");
    assert!(esp.starts_with("mkfs.fat") && esp.contains("vos-efi") && esp.contains("7A05E5E5"), "{esp}");
    assert!(!fmt.contains("tdata") && !fmt.contains("tmeta") && !fmt.contains("tpool"), "{fmt}");
    // Mount: fstab's tree, then ABRoot's three, each made first.
    let m = text(Stage::Mount);
    let order = [
        "mount -t btrfs -o defaults /dev/mapper/vos--root-root--a /mnt/bko-target\n",
        "mount -t ext4 -o defaults /dev/vdb1 /mnt/bko-target/boot\n",
        "mkdir -p /mnt/bko-target/part-future\n# ABRoot: the other root (vos-b), where ABRoot mounts it (not in fstab)\nmount -t btrfs -o defaults /dev/mapper/vos--root-root--b /mnt/bko-target/part-future\n",
        "mkdir -p /mnt/bko-target/var\n# ABRoot: /var, which ABRoot's mount-sys mounts by label (not in fstab)\nmount -t btrfs -o defaults /dev/mapper/vos--var-var /mnt/bko-target/var\n",
        "/dev/vdb2 /mnt/bko-target/boot/efi\n",
        "mkdir -p /mnt/bko-target/boot/init\n# ABRoot: the init LV (both roots' kernels, initramfs and abroot.cfg), where ABRoot mounts it (not in fstab)\nmount -t ext4 -o defaults /dev/mapper/vos--root-init /mnt/bko-target/boot/init\n",
    ];
    let mut at = 0;
    for l in order {
        let i = m[at..].find(l).unwrap_or_else(|| panic!("{l} not after {at}: {m}")) + at;
        at = i + l.len();
    }
    assert!(b.mount_points.iter().any(|p| p == "/mnt/bko-target/boot/init") && b.mount_points.iter().any(|p| p == "/mnt/bko-target/part-future"), "{:?}", b.mount_points);
    // Boot: nothing regenerated, nothing chrooted.
    let bt = text(Stage::Boot);
    let run: String = bt.lines().filter(|l| !l.starts_with('#')).collect::<Vec<_>>().join("\n");
    for no in ["chroot", "update-initramfs", "grub-install", "update-grub", "grub-mkconfig", "--rbind", "dracut", "lvm-devices", "os-prober"] {
        assert!(!run.contains(no), "{no}: {bt}");
    }
    let pos = |x: &str| bt.find(x).unwrap_or_else(|| panic!("{x}: {bt}"));
    let listed = pos("atimes.before");
    let check = pos("t=$(readlink -f /mnt/bko-target) || exit 1; want=vos-a; ilv=vos--root-init; iuuid=20111213-1415-1617-1819-1a1b1c1d1e1f;");
    assert!(bt.contains(&format!("chk vos-a 0 {VANILLA_A} vos--root-root--a /dev/vos-root/root-a\nchk vos-b 0 {VANILLA_B} vos--root-root--b /dev/vos-root/root-b\n")), "{bt}");
    let nvram = pos("efibootmgr --quiet --create --disk /dev/vdb --part 2 --label vanilla --loader '\\EFI\\vanilla\\shimx64.efi'\n");
    let log = pos("mkdir -p /mnt/bko-target/var/log/bko-holt\n");
    let back = pos("atimes.after");
    assert!(listed < check && check < nvram && nvram < log && log < back, "{bt}");
    assert!(b.stage(Stage::Boot).iter().any(|c| c.warns && c.argv.get(2).is_some_and(|x| x.contains("chk vos-b"))));
    // --no-nvram: no UEFI entry.
    assert!(!vanilla_plan(&v, &["--no-nvram"]).stage(Stage::Boot).iter().map(Cmd::shell).collect::<String>().contains("efibootmgr"));
}

/// The future root's btrfs allocation profiles (V5's open finding): sysfs
/// has /sys/fs/btrfs/UUID only while it's mounted, so abroot::read records
/// them during its own read-only mount, storage.btrfs takes them, and
/// load's mkfs.btrfs gets -d/-m for vos-b as for the mounted vos-a.  This
/// runner makes vos-b's sysfs directory on dump's mount and removes it on
/// the umount, as the kernel does.
#[test]
fn vanilla_future_root_btrfs_profiles() {
    use crate::load::{commands::Cmd, Stage};
    struct SysfsMounts {
        inner: FakeRunner,
        sysfs: std::path::PathBuf,
    }
    impl crate::sys::Runner for SysfsMounts {
        fn run(&self, prog: &str, args: &[&str]) -> Option<String> {
            let out = self.inner.run(prog, args);
            if out.is_some() && args.last() == Some(&"/run/bko-holt/abroot-vos-b") {
                if prog == "mount" {
                    for (k, p) in [("data", "single"), ("metadata", "dup"), ("system", "dup")] {
                        std::fs::create_dir_all(self.sysfs.join(format!("allocation/{k}/{p}"))).unwrap();
                    }
                } else if prog == "umount" {
                    std::fs::remove_dir_all(&self.sysfs).unwrap();
                }
            }
            out
        }
    }
    let t = vanilla_tree(true);
    for k in ["data/single", "metadata/dup", "system/dup"] {
        t.dir(&format!("/sys/fs/btrfs/{VANILLA_A}/allocation/{k}"));
    }
    let profiles = |v: &Value, uuid: &str| {
        arr(v, &["storage", "btrfs"]).iter().find(|b| b.get("uuid").and_then(Value::as_str) == Some(uuid)).and_then(|b| b.get("allocation_profiles")).cloned().unwrap()
    };
    // Without the record (sysfs never had vos-b): null, mkfs.btrfs's defaults.
    let v0 = run(&t, true, vanilla_runner());
    assert!(profiles(&v0, VANILLA_B).is_null());
    let sys = Sys::new(t.root(), Box::new(SysfsMounts { inner: vanilla_runner(), sysfs: t.p(&format!("/sys/fs/btrfs/{VANILLA_B}")) }), true);
    let v = crate::report::build(&sys, crate::osrel::read(&sys).as_ref(), "2026-11-04T00:00:00Z");
    assert!(sys.warnings().is_empty(), "{:?}", sys.warnings());
    assert!(!t.p(&format!("/sys/fs/btrfs/{VANILLA_B}")).exists(), "dump's mount of vos-b is gone, and its sysfs with it");
    for uuid in [VANILLA_A, VANILLA_B] {
        let p = profiles(&v, uuid);
        assert_eq!((s(&p, &["data", "0"]), s(&p, &["metadata", "0"]), s(&p, &["system", "0"])), ("single", "dup", "dup"), "{uuid}: {p:?}");
    }
    let b = vanilla_plan(&v, &[]);
    assert!(b.problems.is_empty(), "{:?}", b.problems);
    let fmt = b.stage(Stage::Format).iter().map(Cmd::shell).collect::<String>();
    for end in ["/dev/mapper/vos--root-root--a", "/dev/mapper/vos--root-root--b"] {
        let l = fmt.lines().find(|l| l.starts_with("mkfs.btrfs") && l.split_whitespace().any(|w| w == end)).unwrap_or_else(|| panic!("{end}: {fmt}"));
        assert!(l.contains(" -d single -m dup "), "{l}");
    }
}

/// A fresh install: vos-b formatted but empty, init/vos-b empty.  The dump
/// says so (`empty`), the plan notes it, and the check passes it as a
/// warning; the refusals of version 1 (an upgrade running, BIOS, not the
/// thin two-root scheme, ABRoot v1's /.system, --new-uuids).
#[test]
fn vanilla_fresh_install_and_refusals() {
    let t = vanilla_tree(false);
    let v = run(&t, true, vanilla_runner());
    assert!(arr(&v, &["warnings"]).is_empty(), "{:?}", v.get("warnings"));
    let rb = v.at(&["boot", "abroot", "roots", "1"]).unwrap();
    assert_eq!((rb.get("empty"), rb.get("abimage")), (Some(&Value::Bool(true)), Some(&Value::Null)));
    assert_eq!(arr(rb, &["init", "kernels"]).len(), 0);
    assert_eq!(arr(&v, &["boot", "kernels", "installed"]).len(), 1);
    let b = vanilla_plan(&v, &[]);
    assert!(b.problems.is_empty(), "{:?}", b.problems);
    assert!(b.notes.iter().any(|n| n.starts_with("ABRoot: vos-b was empty on the source")), "{:?}", b.notes);
    let bt: String = b.stage(crate::load::Stage::Boot).iter().map(crate::load::commands::Cmd::shell).collect();
    assert!(bt.contains(&format!("chk vos-b 1 {VANILLA_B} ")), "{bt}");

    // Refusals.
    let has = |v: &Value, extra: &[&str], want: &str| {
        let p = vanilla_plan(v, extra).problems;
        assert!(p.iter().any(|x| x.contains(want)), "{want}: {p:?}");
    };
    let mut w = v.clone();
    set_at(&mut w, &["boot", "abroot", "operation_lock"], Value::Bool(true));
    has(&w, &[], "operation.lock was there when the dump was made");
    let mut w = v.clone();
    set_at(&mut w, &["boot", "abroot", "thin_two_roots"], Value::Bool(false));
    has(&w, &[], "ABRoot: not the layout version 1 restores");
    let mut w = v.clone();
    set_at(&mut w, &["boot", "abroot", "v1_system"], Value::Bool(true));
    has(&w, &[], "ABRoot v1's /.system");
    let mut w = v.clone();
    set_at(&mut w, &["boot", "loader", "firmware"], Value::Str("bios".into()));
    set_at(&mut w, &["firmware", "mode"], Value::Str("bios".into()));
    has(&w, &[], "ABRoot with BIOS boot isn't restored");
    let mut w = v.clone();
    set_at(&mut w, &["boot", "abroot", "grub", "default_label"], Value::Null);
    has(&w, &[], "the dump has no default entry of the master /boot/grub/grub.cfg");
    has(&v, &["--new-uuids"], "--new-uuids isn't built yet");
    // The source's grub.cfg after an upgrade, before the reboot into it.
    let mut w = v.clone();
    set_at(&mut w, &["boot", "abroot", "grub", "default_label"], Value::Str("vos-b".into()));
    let b = vanilla_plan(&w, &[]);
    assert!(b.notes.iter().any(|n| n.contains("grub.cfg boots vos-b next, not vos-a")), "{:?}", b.notes);
    // ...and here vos-b is empty: refused before anything is written.
    assert!(b.problems.iter().any(|p| p.contains("grub.cfg boots vos-b by default, and vos-b was empty")), "{:?}", b.problems);
    // Without the present root's abimage.abr, the gate is ABRoot's /var state.
    let c = v.compact();
    let at = c.find("\"abimage\":{\"digest\":\"sha256:a1a1").unwrap();
    let end = at + c[at..].find('}').unwrap() + 1;
    let w = crate::json::parse(&format!("{}\"abimage\":null{}", &c[..at], &c[end..])).unwrap();
    assert_eq!(crate::other::restored_marker(&w), "var/lib/abroot");
}

/// Cinqcent-style md RAID1 over two disks' partitions, ext4 on md0.
fn md_raid1_tree() -> Tree {
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
    t
}

/// Cinqcent-style md RAID1 over two disks' partitions, read back.
#[test]
fn md_raid1() {
    let t = md_raid1_tree();
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
fn zfs_root_tree() -> (Tree, FakeRunner) {
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
    (t, runner)
}

/// The debian13-zfs test guest read back.
#[test]
fn zfs_root() {
    let (t, runner) = zfs_root_tree();
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

/// The Thinkpad's planned Chimera install, which no test guest has: UEFI,
/// systemd-boot with the ESP as /boot (vda1: kernels and initramfs on it),
/// root on ZFS (vda2: pool rpool, rpool/ROOT/chimera at /, as chimera-zfs
/// names them), the pool's own dataset an encryption root with a passphrase
/// typed at boot (keylocation=prompt), /etc/hostid and zpool.cache.
fn chimera_uefi_zfs() -> (Tree, FakeRunner) {
    use crate::efi::{GLOBAL, LOADER};
    let t = Tree::new("scn-chimera-uefi-zfs");
    let v = "6.18.62-0-generic";
    let mut img = vec![0u8; 96 * MIB];
    write_gpt(
        &mut img,
        512,
        &GptSpec {
            disk_guid: "6f1c2c3d-0000-4000-8000-00000000cccc",
            parts: vec![
                (ESP, "0b5f04ed-e731-4ac6-933e-83ff25657722", 2048, 73727, 0, ""),
                ("6a898cc3-1dd2-11b2-99a6-080020c9b98d", "21112222-3333-4444-5555-666677778888", 73728, 190463, 0, ""),
            ],
        },
    );
    put(&mut img, 2048, &fat::sample::fat32(71680, 0x1234_abcd, ""));
    let cfg = crate::json::parse(
        r#"{"version": 5000, "name": "rpool", "state": 0, "txg": 4, "pool_guid": 888, "hostname": "thinkpad", "hostid": 305419896, "top_guid": 1, "guid": 1,
            "vdev_children": 1, "vdev_tree": {"type": "disk", "id": 0, "guid": 1, "path": "/dev/vda2", "whole_disk": 0, "ashift": 12},
            "features_for_read": {"com.delphix:hole_birth": true}}"#,
    )
    .unwrap();
    put(&mut img, 73728, &zfs::pack::vdev(116736 * 512, &cfg));
    let mut loader_info = vec![6u8, 0, 0, 0];
    for c in "systemd-boot 256.11".encode_utf16().chain([0]) {
        loader_info.extend(c.to_le_bytes());
    }
    let opts = "root=ZFS=rpool/ROOT/chimera ro";
    t.disk("vda", "254:0", &img, 512)
        .partition("vda", "vda1", "254:1", 1, 2048, 71680, &img)
        .partition("vda", "vda2", "254:2", 2, 73728, 116736, &img)
        .file("/sys/firmware/efi/fw_platform_size", "64\n")
        .file(&format!("/sys/firmware/efi/efivars/SecureBoot-{GLOBAL}"), [6u8, 0, 0, 0, 0])
        .file(&format!("/sys/firmware/efi/efivars/LoaderInfo-{LOADER}"), loader_info)
        .symlink("/dev/disk/by-partuuid/0b5f04ed-e731-4ac6-933e-83ff25657722", "../../vda1")
        .symlink("/dev/disk/by-partuuid/21112222-3333-4444-5555-666677778888", "../../vda2")
        .file("/proc/self/mountinfo", "26 1 0:24 / / rw,relatime shared:1 - zfs rpool/ROOT/chimera rw,xattr,posixacl\n27 26 254:1 / /boot rw,relatime shared:2 - vfat /dev/vda1 rw,fmask=0022\n")
        // Chimera's ZFS guide: no line for / (the pool mounts it), the ESP by UUID.
        .file("/etc/fstab", "UUID=1234-ABCD /boot vfat defaults 0 2\n")
        .file("/etc/os-release", "NAME=\"Chimera\"\nID=\"chimera\"\nPRETTY_NAME=\"Chimera Linux\"\n")
        .file("/etc/hostid", [0x78u8, 0x56, 0x34, 0x12])
        .file("/etc/zfs/zpool.cache", zfs::pack::packed(&crate::json::parse(r#"{"rpool": {"name": "rpool", "pool_guid": 888}}"#).unwrap()))
        .symlink("/lib", "usr/lib")
        .file(
            "/usr/lib/apk/db/installed",
            "C:Q1aaa=\nP:linux-lts\nV:6.18.62-r0\nA:x86_64\n\nC:Q1abb=\nP:linux-lts-zfs-bin\nV:2.4.4.6.18.62-r0\nA:x86_64\n\nC:Q1acc=\nP:zfs\nV:2.4.4-r0\nA:x86_64\n\n\
             C:Q1bbb=\nP:systemd-boot\nV:256.11-r5\nA:x86_64\n\nC:Q1ccc=\nP:systemd-boot-efi\nV:256.11-r5\nA:x86_64\n\nC:Q1ddd=\nP:initramfs-tools\nV:0.148.3-r2\nA:noarch\n\n\
             C:Q1eee=\nP:efibootmgr\nV:18-r0\nA:x86_64\n\n",
        )
        .file("/boot/EFI/systemd/systemd-bootx64.efi", vec![1u8; 300])
        .file("/boot/EFI/BOOT/BOOTX64.EFI", vec![1u8; 300])
        .file("/boot/loader/loader.conf", "timeout 3\n")
        .file(&format!("/boot/loader/entries/chimera-{v}.conf"), format!("title Chimera Linux\nlinux /vmlinuz-{v}\ninitrd /initrd.img-{v}\noptions {opts} quiet\n"))
        .file(&format!("/boot/loader/entries/chimera-{v}-recovery.conf"), format!("title Chimera Linux\nlinux /vmlinuz-{v}\ninitrd /initrd.img-{v}\noptions {opts} single\n"))
        .file(&format!("/boot/vmlinuz-{v}"), "k")
        .file(&format!("/boot/initrd.img-{v}"), "i")
        .file(&format!("/boot/config-{v}"), "")
        .dir(&format!("/usr/lib/modules/{v}/kernel"))
        .file(&format!("/usr/lib/modules/{v}/extra/zfs.ko.zst"), "z")
        .file("/etc/initramfs-tools/initramfs.conf", "MODULES=most\nCOMPRESS=zstd\n")
        .file("/etc/default/systemd-boot", "SYSTEMD_BOOT_CMDLINE=\"root=ZFS=rpool/ROOT/chimera\"\n")
        .file("/proc/sys/kernel/osrelease", format!("{v}\n"))
        .file("/proc/sys/kernel/hostname", "thinkpad\n")
        .file("/proc/cmdline", format!("initrd=\\initrd.img-{v} {opts} quiet\n"));
    let runner = FakeRunner::default()
        .with("zpool list -H -o name", "rpool\n")
        .with(
            "zpool get -H -p -o property,value,source all rpool",
            "size\t62277025792\t-\nashift\t12\tlocal\ncachefile\t/etc/zfs/zpool.cache\tlocal\nfree\t1\t-\nfeature@encryption\tactive\tlocal\nfeature@async_destroy\tenabled\tlocal\n",
        )
        .with(
            "zfs list -H -p -t filesystem,volume -o name,type,used,available,referenced,mountpoint,canmount,mounted,encryption,encryptionroot,keyformat,keylocation,keystatus,origin,compression,recordsize,volsize,volblocksize,acltype,xattr,dnodesize,normalization,utf8only,casesensitivity,atime,relatime,copies,com.sun:auto-snapshot",
            "rpool\tfilesystem\t1000\t2000\t96\t/\toff\tno\taes-256-gcm\trpool\tpassphrase\tprompt\tavailable\t-\tlz4\t131072\t-\t-\tposix\tsa\tauto\tnone\toff\tsensitive\ton\ton\t1\t-\n\
             rpool/ROOT\tfilesystem\t900\t2000\t96\tnone\toff\tno\taes-256-gcm\trpool\tpassphrase\tnone\tavailable\t-\tlz4\t131072\t-\t-\tposix\tsa\tauto\tnone\toff\tsensitive\ton\ton\t1\t-\n\
             rpool/ROOT/chimera\tfilesystem\t900\t2000\t900\t/\tnoauto\tyes\taes-256-gcm\trpool\tpassphrase\tnone\tavailable\t-\tlz4\t131072\t-\t-\tposix\tsa\tauto\tnone\toff\tsensitive\ton\ton\t1\t-\n",
        )
        .with(
            "zfs get -H -p -t filesystem,volume -s local,received -o name,property,value,source all",
            "rpool\tcompression\tlz4\tlocal\nrpool\tkeylocation\tprompt\tlocal\nrpool\tcanmount\toff\tlocal\nrpool\tmountpoint\t/\tlocal\nrpool\tacltype\tposix\tlocal\nrpool\txattr\tsa\tlocal\n\
             rpool/ROOT\tmountpoint\tnone\tlocal\nrpool/ROOT\tcanmount\toff\tlocal\nrpool/ROOT/chimera\tmountpoint\t/\tlocal\nrpool/ROOT/chimera\tcanmount\tnoauto\tlocal\n",
        )
        .with(
            "zpool status -j --json-int -P rpool",
            r#"{"output_version": {"command": "zpool status", "vers_major": 0, "vers_minor": 1}, "pools": {"rpool": {"name": "rpool", "state": "ONLINE",
               "vdevs": {"rpool": {"name": "rpool", "vdev_type": "root", "vdevs": {"/dev/vda2": {"name": "/dev/vda2", "vdev_type": "disk", "path": "/dev/vda2"}}}}}}}"#,
        );
    (t, runner)
}

/// bko-holt's plan for the Thinkpad's Chimera install: the pool made with
/// its features and the passphrase's encryption (the passphrase from
/// --key-file, then keylocation=prompt again), / from the dataset before the
/// ESP at /boot, zpool.cache before the initramfs, systemd-boot installed and
/// gen-systemd-boot's entries made after the initramfs; no GRUB.
#[test]
fn chimera_uefi_zfs_thinkpad() {
    let (t, runner) = chimera_uefi_zfs();
    let v = run(&t, true, runner);
    assert_eq!(s(&v, &["os", "id"]), "chimera");
    assert_eq!(s(&v, &["firmware", "mode"]), "uefi");
    assert_eq!(s(&v, &["boot", "loader", "name"]), "systemd-boot");
    assert_eq!(s(&v, &["boot", "packages", "systemd-boot"]), "256.11-r5");
    assert_eq!(s(&v, &["boot", "esps", "0", "mounted_at"]), "/boot");
    assert_eq!(s(&v, &["boot", "initramfs", "generator"]), "initramfs-tools");
    assert_eq!(s(&v, &["boot", "cmdline", "storage", "root"]), "ZFS=rpool/ROOT/chimera");
    assert_eq!(s(&v, &["storage", "mounts", "0", "source"]), "rpool/ROOT/chimera");
    let tools = v.at(&["storage", "zfs", "tools"]).unwrap();
    assert_eq!(s(tools, &["datasets", "0", "encryptionroot"]), "rpool");
    assert_eq!(s(tools, &["datasets", "0", "keyformat"]), "passphrase");
    assert_eq!(s(tools, &["datasets", "0", "keylocation"]), "prompt");
    assert_eq!(u(&v, &["storage", "zfs", "zpool_cache", "rpool", "pool_guid"]), 888);
    assert!(arr(&v, &["warnings"]).is_empty(), "{:?}", v.get("warnings"));

    use crate::load::{commands, layout::Layout, target::Mapping, Parsed, Stage};
    let l = Layout::from_json(v).unwrap();
    let plan = |extra: &[&str]| {
        let mut a = vec!["l.json".to_string()];
        a.extend(extra.iter().map(|s| s.to_string()));
        let Ok(Parsed::Run(o)) = crate::load::parse_args(&a) else { unreachable!() };
        commands::build(&l, &o, &Mapping { pairs: vec![("vda".into(), "nvme0n1".into())], ..Mapping::default() })
    };
    // A passphrase can't be made up: load asks for it (BackShot1's
    // layout_keys passes keys/disk-passphrase as --key-file rpool=...).
    let no_key = plan(&[]);
    assert!(no_key.problems.iter().any(|p| p.contains("ZFS encryption root rpool: give its new key with --key-file rpool=FILE")), "{:?}", no_key.problems);
    let b = plan(&["--key-file", "rpool=/run/bs1/disk-passphrase"]);
    assert!(b.problems.is_empty(), "{:?}", b.problems);
    let text = |st: Stage| -> String { b.stage(st).iter().map(commands::Cmd::shell).collect() };
    let asm = text(Stage::Assemble);
    let create = asm.lines().find(|l| l.starts_with("zpool create ")).expect(&asm);
    for w in ["-o feature@encryption=enabled", "-o ashift=12", "-O encryption=aes-256-gcm", "-O keyformat=passphrase", "-O keylocation=file:///run/bs1/disk-passphrase", "-R /mnt/bko-target rpool /dev/nvme0n1p2"] {
        assert!(create.contains(w), "{w}: {create}");
    }
    assert!(asm.contains("zpool reguid -g 888 rpool\n"), "{asm}");
    // Encryption is inherited below the root, never set again.
    let all: String = [Stage::Assemble, Stage::Format, Stage::Mount].into_iter().map(text).collect();
    for l in all.lines().filter(|l| l.starts_with("zfs create ")) {
        assert!(!l.contains("encryption") && !l.contains("keyformat") && !l.contains("keylocation"), "{l}");
    }
    assert!(all.contains("zfs create -u -o mountpoint=/ -o canmount=noauto rpool/ROOT/chimera\n"), "{all}");
    let mnt = text(Stage::Mount);
    let root = mnt.find("zfs mount rpool/ROOT/chimera\n").expect(&mnt);
    let esp = mnt.find("mount -t vfat -o defaults /dev/nvme0n1p1 /mnt/bko-target/boot\n").expect(&mnt);
    assert!(root < esp, "{mnt}");
    let bt = text(Stage::Boot);
    let prompt = bt.find("zfs set keylocation=prompt rpool\n").expect(&bt);
    let cache = bt.find("zpool set cachefile=/mnt/bko-target/etc/zfs/zpool.cache rpool\n").expect(&bt);
    let initramfs = bt.find("chroot /mnt/bko-target update-initramfs -u -k all\n").expect(&bt);
    let bootctl = bt.find("chroot /mnt/bko-target bootctl install --no-variables\n").expect(&bt);
    let made = bt.find("chroot /mnt/bko-target sh -c 'gen-systemd-boot; r=$?;").expect(&bt);
    assert!(prompt < initramfs && cache < initramfs && initramfs < bootctl && bootctl < made, "{bt}");
    assert!(!bt.contains("grub"), "{bt}");
    let close = text(Stage::Close);
    assert!(close.contains("zpool export rpool"), "{close}");
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

/// The debian13-zfs guest's dump onto vdb.
fn zfs_root_plan(v: Value) -> crate::load::commands::Built {
    use crate::load::{commands, layout::Layout, target::Mapping, Parsed};
    let l = Layout::from_json(v).unwrap();
    let Ok(Parsed::Run(o)) = crate::load::parse_args(&["l.json".to_string()]) else { unreachable!() };
    commands::build(&l, &o, &Mapping { pairs: vec![("vda".into(), "vdb".into())], ..Mapping::default() })
}

/// / on ZFS and no zpool installed: no pools would be a wrong answer, so
/// the dump says why (and warns) and load refuses it.  The same for a zpool
/// that's there and fails, and for pools whose datasets a missing zfs
/// can't list.
#[test]
fn zfs_mounted_without_zpool_is_refused() {
    let (t, runner) = zfs_root_tree();
    let v = run(&t, true, runner.without("zpool"));
    let e = s(&v, &["storage", "zfs", "tools", "error"]).to_string();
    assert_eq!(e, "ZFS file systems are mounted, but zpool isn't installed, so the dump records no pools or datasets; bko-holt load refuses this dump: make zpool work and dump again");
    assert!(arr(&v, &["warnings"]).iter().any(|w| w.as_str() == Some(e.as_str())), "{:?}", v.get("warnings"));
    let b = zfs_root_plan(v);
    assert!(b.problems.contains(&format!("ZFS: {e}")), "{:?}", b.problems);

    let (t, mut runner) = zfs_root_tree();
    runner.answers.remove("zpool list -H -o name");
    let v = run(&t, true, runner);
    let e = s(&v, &["storage", "zfs", "tools", "error"]);
    assert!(e.starts_with("ZFS file systems are mounted, but `zpool list` failed, so the dump records no pools or datasets; "), "{e}");
    assert!(zfs_root_plan(v.clone()).problems.iter().any(|p| p.starts_with("ZFS: ZFS file systems are mounted, but `zpool list` failed")));

    let (t, runner) = zfs_root_tree();
    let v = run(&t, true, runner.without("zfs"));
    assert_eq!(s(&v, &["storage", "zfs", "tools", "imported_pools", "0", "name"]), "rpool");
    assert!(arr(&v, &["storage", "zfs", "tools", "datasets"]).is_empty());
    let e = s(&v, &["storage", "zfs", "tools", "error"]);
    assert_eq!(e, "ZFS pools are imported, but zfs isn't installed, so the dump records none of their datasets; bko-holt load refuses this dump: make zfs work and dump again");
    assert!(zfs_root_plan(v.clone()).problems.contains(&format!("ZFS: {e}")));

    // With both there, none of it.
    let (t, runner) = zfs_root_tree();
    let v = run(&t, true, runner);
    assert_eq!(v.at(&["storage", "zfs", "tools", "error"]), None);
    assert!(!zfs_root_plan(v).problems.iter().any(|p| p.starts_with("ZFS: ")));
}

/// No ZFS mounted, and neither zpool nor zfs installed (most Linux
/// systems): no ZFS, as before, and nothing said of it.
#[test]
fn no_zfs_mounted_without_zpool_is_fine() {
    let t = Tree::new("scn-nozpool");
    t.debian13();
    t.file("/proc/self/mountinfo", "26 1 254:6 / / rw,relatime shared:1 - ext4 /dev/vda6 rw\n");
    let v = run(&t, true, FakeRunner::default().without("zpool").without("zfs"));
    assert!(v.at(&["storage", "zfs", "tools"]).unwrap().is_null());
    let w = arr(&v, &["warnings"]);
    assert!(!w.iter().filter_map(Value::as_str).any(|w| w.contains("zpool") || w.contains("ZFS")), "{w:?}");
}

/// A disk with a filesystem straight on it (no table), a loop device, a
/// zram swap device and an empty CD drive.
fn misc_tree() -> Tree {
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
    t
}

/// A disk with a filesystem straight on it, a loop device, zram and a CD.
#[test]
fn whole_disk_fs_loop_zram() {
    let t = misc_tree();
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

const KINOITE_CSUM: &str = "10e6a37fe91449ab0256dc707f50023505f76d29b6d34844b63843d0950f7de6";
const KINOITE_BOOTCSUM: &str = "1cc099369334fad8952283b3221e6d78fd79263e6dedc35ce40302458681d413";
const KINOITE_KERNEL: &str = "7.3.3-200.fc44.x86_64";

/// The kinoite44 guest (docs/design/bazzite.md, "What B1 found"), booted:
/// UEFI through shim; GPT with the ESP, an ext4 /boot and a btrfs whose
/// subvolumes root, home and var fstab mounts at /, /home and /var.  `/` is
/// a composefs overlay; the physical root (subvolume root) is at /sysroot,
/// the booted deployment's etc bound at /etc before it in mountinfo, and the
/// stateroot's var bound beneath it.  Two deployments of one commit: .1
/// booted and the default (ostree-2.conf), .0 the rollback.
fn kinoite_ostree() -> Tree {
    use crate::efi::real::*;
    use crate::efi::GLOBAL;
    let (c, b, v) = (KINOITE_CSUM, KINOITE_BOOTCSUM, KINOITE_KERNEL);
    let t = Tree::new("scn-ostree");
    let mut img = vec![0u8; 96 * MIB];
    write_gpt(
        &mut img,
        512,
        &GptSpec {
            disk_guid: "6f1c2c3d-0000-4000-8000-00000000cccc",
            parts: vec![
                (ESP, "e53caff6-0000-4000-8000-000000000001", 2048, 73727, 0, "EFI System Partition"),
                (LINUX, "e53caff6-0000-4000-8000-000000000002", 73728, 106495, 0, ""),
                (LINUX, "e53caff6-0000-4000-8000-000000000003", 106496, 196574, 0, ""),
            ],
        },
    );
    put(&mut img, 2048, &fat::sample::fat32(71680, 0x1e3b_f9f2, ""));
    put(&mut img, 73728, &ext::sample::image(0x4 | 0x1000, 0x2 | 0x40 | 0x80 | 0x200, 0x1 | 0x400, ""));
    put(&mut img, 106496, &btrfs::sample::image("fedora", 0x1 | 0x20 | 0x40 | 0x100 | 0x200));
    let boot_uuid = "10111213-1415-1617-1819-1a1b1c1d1e1f";
    let root_uuid = "b0b1b2b3-b4b5-b6b7-b8b9-babbbcbdbebf";
    // Fedora's shim entry: Debian's from Chopin, renamed (same lengths).
    let shim = BOOT0001_SHIM.replace("6400 6500 6200 6900 6100 6e00", "4600 6500 6400 6f00 7200 6100").replace("4400 4500 4200 4900 4100 4e00", "6600 6500 6400 6f00 7200 6100");
    let var = |n: &str| format!("/sys/firmware/efi/efivars/{n}-{GLOBAL}");
    let dep = |n: u32| format!("/sysroot/ostree/deploy/default/deploy/{c}.{n}");
    let fstab = format!("# Updated by bootc-fstab-edit.service\nUUID={root_uuid} / btrfs subvol=root,compress=zstd:1,ro 0 0\nUUID={boot_uuid} /boot ext4 defaults 1 2\nUUID=1E3B-F9F2 /boot/efi vfat defaults,uid=0,gid=0,umask=077,shortname=winnt 0 2\nUUID={root_uuid} /home btrfs subvol=home,compress=zstd:1 0 0\nUUID={root_uuid} /var btrfs subvol=var,compress=zstd:1 0 0\n");
    let bls = |n: u32, ser: u32, extra: &str| {
        format!("title Fedora Linux 44.20261111.0 (Kinoite) (ostree:{ser})\nversion {n}\noptions ostree=/ostree/boot.1/default/{b}/{ser} console=tty0 console=ttyS0,115200n8 root=UUID={root_uuid} rootflags=subvol=root rw{extra}\nlinux /ostree/default-{b}/vmlinuz-{v}\ninitrd /ostree/default-{b}/initramfs-{v}.img\n")
    };
    t.disk("vda", "253:0", &img, 512)
        .partition("vda", "vda1", "253:1", 1, 2048, 71680, &img)
        .partition("vda", "vda2", "253:2", 2, 73728, 32768, &img)
        .partition("vda", "vda3", "253:3", 3, 106496, 90079, &img)
        .file("/sys/firmware/efi/fw_platform_size", "64\n")
        .file(&var("Boot0001"), unhex(&shim))
        .file(&var("BootOrder"), unhex("07000000 0100"))
        .file(&var("BootCurrent"), unhex("06000000 0100"))
        .file(&var("SecureBoot"), unhex("06000000 00"))
        .file("/usr/lib/os-release", "NAME=\"Fedora Linux\"\nVERSION=\"44.20261111.0 (Kinoite)\"\nID=fedora\nVERSION_ID=44\nPRETTY_NAME=\"Fedora Linux 44.20261111.0 (Kinoite)\"\nVARIANT_ID=kinoite\nOSTREE_VERSION='44.20261111.0'\n")
        .symlink("/etc/os-release", "../usr/lib/os-release")
        .file("/run/ostree-booted", "")
        .file(
            "/proc/self/mountinfo",
            format!(
                "51 1 0:42 / / ro,relatime shared:1 - overlay composefs ro,seclabel,lowerdir+=/run/ostree/.private/cfsroot-lower,datadir+=/sysroot/ostree/repo/objects,redirect_dir=on,metacopy=on
48 51 0:37 /root/ostree/deploy/default/deploy/{c}.1/etc /etc rw,relatime shared:2 - btrfs /dev/vda3 rw,seclabel,discard=async,space_cache=v2,subvolid=258,subvol=/root
52 51 0:37 /root /sysroot ro,relatime shared:3 - btrfs /dev/vda3 rw,seclabel,discard=async,space_cache=v2,subvolid=258,subvol=/root
53 52 0:37 /root/ostree/deploy/default/var /sysroot/ostree/deploy/default/var rw,relatime shared:4 - btrfs /dev/vda3 rw,seclabel,discard=async,space_cache=v2,subvolid=258,subvol=/root
42 51 0:9 / /dev rw,nosuid shared:5 - devtmpfs devtmpfs rw,seclabel
125 51 0:37 /var /var rw,relatime shared:215 - btrfs /dev/vda3 rw,seclabel,discard=async,space_cache=v2,subvolid=256,subvol=/var
82 125 0:37 /home /var/home rw,relatime shared:203 - btrfs /dev/vda3 rw,seclabel,discard=async,space_cache=v2,subvolid=257,subvol=/home
120 51 253:2 / /boot rw,relatime shared:209 - ext4 /dev/vda2 rw,seclabel
220 120 253:1 / /boot/efi rw,relatime shared:221 - vfat /dev/vda1 rw,fmask=0077,dmask=0077,codepage=437,iocharset=ascii,shortname=winnt,errors=remount-ro
"
            ),
        )
        .file("/proc/swaps", "Filename\tType\tSize\tUsed\tPriority\n/dev/zram0 partition 3984380 0 100\n")
        .file("/proc/cmdline", format!("BOOT_IMAGE=(hd0,gpt2)/ostree/default-{b}/vmlinuz-{v} ostree=/ostree/boot.1/default/{b}/0 console=tty0 console=ttyS0,115200n8 root=UUID={root_uuid} rootflags=subvol=root rw bko.rollback=1\n"))
        .file("/proc/sys/kernel/osrelease", format!("{v}\n"))
        .file("/proc/sys/kernel/hostname", "kinoite44\n")
        .file("/etc/fstab", &fstab)
        .file("/etc/crypttab", "")
        .dir("/usr/lib/sysimage/rpm")
        .dir(&format!("/usr/lib/modules/{v}/kernel"))
        .file("/usr/lib/ostree/prepare-root.conf", "[composefs]\nenabled = yes\n[sysroot]\nreadonly = true\n")
        // The physical root.
        .file(&format!("{}/etc/fstab", dep(0)), &fstab)
        .file(&format!("{}/etc/fstab", dep(1)), &fstab)
        .file(&format!("{}/etc/bko-deployment", dep(1)), format!("deployment {c}.1\n"))
        .file(&format!("{}.origin", dep(0)), "[origin]\ncontainer-image-reference=ostree-unverified-registry:quay.io/fedora/fedora-kinoite:44\n")
        .file(&format!("{}.origin", dep(1)), "[origin]\ncontainer-image-reference=ostree-unverified-registry:quay.io/fedora/fedora-kinoite:44\n")
        .dir("/sysroot/ostree/deploy/default/var/tmp")
        .dir("/sysroot/ostree/deploy/default/backing")
        .file("/sysroot/ostree/repo/config", "[core]\nrepo_version=1\nmode=bare\n\n[remote \"default\"]\nurl=quay.io/fedora/fedora-kinoite@sha256:d78c9d54\ngpg-verify=false\n\n[sysroot]\nreadonly=true\nbootloader=none\n")
        .file("/sysroot/ostree/repo/objects/03/ce65e1429cd57bf8e169ef1eb52ede93040a1cc054cf160b1d1f8dafb5b3a2.file", "x")
        .symlink("/sysroot/ostree/boot.1", "boot.1.1")
        .symlink(&format!("/sysroot/ostree/boot.1.1/default/{b}/0"), &format!("../../../deploy/default/deploy/{c}.1"))
        .symlink(&format!("/sysroot/ostree/boot.1.1/default/{b}/1"), &format!("../../../deploy/default/deploy/{c}.0"))
        // /boot and the ESP.
        .symlink("/boot/boot", ".")
        .symlink("/boot/loader", "loader.1")
        .file("/boot/loader.1/entries/ostree-1.conf", bls(1, 1, ""))
        .file("/boot/loader.1/entries/ostree-2.conf", bls(2, 0, " bko.rollback=1"))
        .file(&format!("/boot/ostree/default-{b}/vmlinuz-{v}"), vec![0u8; 19])
        .file(&format!("/boot/ostree/default-{b}/initramfs-{v}.img"), vec![0u8; 24])
        .file(&format!("/boot/ostree/default-{b}/.vmlinuz-{v}.hmac"), "x")
        .file("/boot/bootupd-state.json", "{\"installed\":{\"EFI\":{}}}\n")
        .file("/boot/grub2/grub.cfg", "# Generated by bootupd\nset timeout=1\nsearch --no-floppy --set=root --fs-uuid 10111213-1415-1617-1819-1a1b1c1d1e1f\ninsmod blscfg\nblscfg\n")
        .file("/boot/grub2/grubenv", "# GRUB Environment Block\nboot_success=1\n######\n")
        .file("/boot/grub2/bootuuid.cfg", format!("set BOOT_UUID=\"{boot_uuid}\"\n"))
        .file("/boot/efi/EFI/fedora/shimx64.efi", vec![1u8; 300])
        .file("/boot/efi/EFI/fedora/grubx64.efi", vec![2u8; 400])
        .file("/boot/efi/EFI/fedora/grub.cfg", "search --no-floppy --root-dev-only --fs-uuid --set=dev 10111213-1415-1617-1819-1a1b1c1d1e1f\nset prefix=($dev)/grub2\nconfigfile $prefix/grub.cfg\n")
        .file("/boot/efi/EFI/fedora/bootuuid.cfg", format!("set BOOT_UUID=\"{boot_uuid}\"\n"))
        .file("/boot/efi/EFI/BOOT/BOOTX64.EFI", vec![1u8; 300]);
    t
}

/// What rpm, btrfs and bootc answer on kinoite44.  The btrfs tools are
/// asked at /sysroot, never at /etc (which comes first in mountinfo).
fn kinoite_runner() -> FakeRunner {
    let mut r = FakeRunner::default()
        .with(
            "rpm -qa --qf %{NAME}\t%{VERSION}-%{RELEASE}\t%{ARCH}\n",
            "grub2-efi-x64\t2.12-66.fc44\tx86_64\nshim-x64\t16.1-7\tx86_64\ngrub2-tools\t2.12-66.fc44\tx86_64\ndracut\t110-1.fc44\tx86_64\nbtrfs-progs\t6.17-1.fc44\tx86_64\nkernel-core\t7.3.3-200.fc44\tx86_64\nostree\t2026.4-1.fc44\tx86_64\nbootupd\t0.3.2-1.fc44\tx86_64\nefibootmgr\t18-11.fc44\tx86_64\n",
        )
        .with("btrfs subvolume list -a -p -u -q /sysroot", "ID 256 gen 113 parent 5 top level 5 parent_uuid - uuid 6c8a1b2c-0000-4000-8000-000000000256 path var\nID 257 gen 88 parent 5 top level 5 parent_uuid - uuid 6c8a1b2c-0000-4000-8000-000000000257 path home\nID 258 gen 113 parent 5 top level 5 parent_uuid - uuid 6c8a1b2c-0000-4000-8000-000000000258 path root\n")
        .with("btrfs subvolume list -a -r /sysroot", "")
        .with("btrfs subvolume get-default /sysroot", "ID 5 (FS_TREE)\n")
        .with(
            "bootc status --json",
            r#"{"apiVersion": "org.containers.bootc/v1", "kind": "BootcHost", "status": {"staged": null, "booted": {"image": {"image": {"image": "quay.io/fedora/fedora-kinoite:44", "transport": "registry"}, "version": "44.20261111.0", "imageDigest": "sha256:c5e91226b7c92214a5fff04ca2836504663bb8025792ad77c9c70b9c5ace3b19"}, "ostree": {"stateroot": "default", "deploySerial": 1}}}}"#,
        );
    for p in ["/sysroot", "/var", "/var/home"] {
        r = r.with(&format!("btrfs property get -t s {p}"), "ro=false\n").with(&format!("btrfs property get -t i {p}"), "");
    }
    r
}

#[test]
fn kinoite_ostree_guest() {
    let (c, b, kv) = (KINOITE_CSUM, KINOITE_BOOTCSUM, KINOITE_KERNEL);
    let v = run(&kinoite_ostree(), true, kinoite_runner());
    assert!(arr(&v, &["warnings"]).is_empty(), "{:?}", v.get("warnings"));
    assert_eq!(s(&v, &["os", "id"]), "fedora");
    assert_eq!(s(&v, &["firmware", "mode"]), "uefi");
    // Storage: the composefs / isn't a layout mount; /etc and the stateroot
    // var are aliases of /sysroot; the backup tree is fstab's.
    let st = v.get("storage").unwrap();
    assert_eq!(s(st, &["physical_root"]), "/sysroot");
    let ms = arr(st, &["mounts"]);
    let targets: Vec<&str> = ms.iter().map(|m| m.get("target").and_then(Value::as_str).unwrap()).collect();
    assert_eq!(targets, vec!["/etc", "/sysroot", "/sysroot/ostree/deploy/default/var", "/var", "/var/home", "/boot", "/boot/efi"]);
    let alias: Vec<Option<&str>> = ms.iter().map(|m| m.get("alias_of").and_then(Value::as_str)).collect();
    assert_eq!(alias, vec![Some("/sysroot"), None, Some("/sysroot"), None, None, None, None]);
    let src: Vec<(&str, &str, &str)> = arr(st, &["backup_sources"])
        .iter()
        .map(|x| (x.get("path").and_then(Value::as_str).unwrap(), x.get("from").and_then(Value::as_str).unwrap(), x.get("device").and_then(Value::as_str).unwrap()))
        .collect();
    assert_eq!(src, vec![("/", "/sysroot", "vda3"), ("/boot", "/boot", "vda2"), ("/boot/efi", "/boot/efi", "vda1"), ("/home", "/var/home", "vda3"), ("/var", "/var", "vda3")]);
    // Its paths are the mount points fstab gives, as load plans them.
    let mut fs: Vec<&str> = arr(st, &["fstab"]).iter().map(|e| e.get("file").and_then(Value::as_str).unwrap()).collect();
    fs.sort();
    assert_eq!(src.iter().map(|x| x.0).collect::<Vec<_>>(), fs);
    assert_eq!(s(st, &["fstab", "0", "resolves_to"]), "vda3");
    // btrfs: subvolume root's properties from /sysroot (not the /etc bind),
    // and only the subvolumes' own mounts.
    let bt = st.at(&["btrfs", "0"]).unwrap();
    let msv: Vec<(&str, &str)> = arr(bt, &["mounted_subvolumes"]).iter().map(|x| (x.get("target").and_then(Value::as_str).unwrap(), x.get("subvol").and_then(Value::as_str).unwrap())).collect();
    assert_eq!(msv, vec![("/sysroot", "/root"), ("/var", "/var"), ("/var/home", "/home")]);
    for (i, path) in [(0, "var"), (1, "home"), (2, "root")] {
        assert_eq!(s(bt, &["subvolumes", &i.to_string(), "path"]), path);
        assert_eq!(s(bt, &["subvolumes", &i.to_string(), "properties", "ro"]), "false", "{path}");
    }
    // Boot: GRUB through shim from Fedora's packages, bootupd's grub.cfg.
    let bo = v.get("boot").unwrap();
    assert_eq!(s(bo, &["loader", "name"]), "grub");
    assert_eq!(bo.at(&["loader", "via_shim"]), Some(&Value::Bool(true)));
    assert_eq!(s(bo, &["loader", "package"]), "grub2-efi-x64");
    assert_eq!(s(bo, &["loader", "shim_package"]), "shim-x64");
    assert_eq!(s(bo, &["grub", "dir"]), "/boot/grub2");
    assert!(bo.get("systemd_boot").unwrap().is_null(), "{}", bo.get("systemd_boot").unwrap().compact());
    assert_eq!(s(bo, &["initramfs", "generator"]), "prebuilt");
    let k = arr(bo, &["kernels", "installed"]);
    assert_eq!(k.len(), 1);
    assert_eq!(s(&k[0], &["version"]), kv);
    assert_eq!(s(&k[0], &["dir"]), format!("/boot/ostree/default-{b}"));
    assert_eq!(k[0].get("running"), Some(&Value::Bool(true)));
    assert_eq!(u(&k[0], &["initrd_bytes"]), 24);
    assert_eq!(s(bo, &["packages", "grub2-efi-x64"]), "2.12-66.fc44");
    // boot.ostree.
    let o = bo.get("ostree").unwrap();
    assert_eq!(o.get("booted"), Some(&Value::Bool(true)));
    assert_eq!(s(o, &["karg"]), format!("/ostree/boot.1/default/{b}/0"));
    assert_eq!(s(o, &["physical_root"]), "/sysroot");
    assert_eq!(s(o, &["stateroot"]), "default");
    assert_eq!(s(o, &["booted_deployment"]), format!("/ostree/deploy/default/deploy/{c}.1"));
    let d = arr(o, &["deployments"]);
    assert_eq!(d.len(), 2);
    assert_eq!((u(&d[0], &["serial"]), d[0].get("booted")), (0, Some(&Value::Bool(false))));
    assert_eq!((u(&d[1], &["serial"]), d[1].get("booted")), (1, Some(&Value::Bool(true))));
    assert_eq!(s(&d[1], &["checksum"]), c);
    assert_eq!(s(&d[1], &["origin"]), "ostree-unverified-registry:quay.io/fedora/fedora-kinoite:44");
    assert_eq!(d[0].get("has_etc"), Some(&Value::Bool(true)));
    let e = arr(o, &["entries"]);
    assert_eq!((s(&e[0], &["file"]), s(&e[0], &["version"]), s(&e[0], &["deployment"])), ("ostree-1.conf", "1", format!("/ostree/deploy/default/deploy/{c}.0").as_str()));
    assert_eq!((s(&e[1], &["file"]), s(&e[1], &["version"]), s(&e[1], &["deployment"])), ("ostree-2.conf", "2", format!("/ostree/deploy/default/deploy/{c}.1").as_str()));
    assert!(s(&e[1], &["options"]).ends_with(" rw bko.rollback=1"));
    assert_eq!(s(&e[1], &["linux"]), format!("/ostree/default-{b}/vmlinuz-{kv}"));
    assert_eq!(s(o, &["boot_loader"]), "loader.1");
    assert_eq!(s(o, &["boot_links", "boot.1"]), "boot.1.1");
    assert_eq!(s(o, &["prepare_root", "composefs"]), "yes");
    assert_eq!(s(o, &["prepare_root", "readonly"]), "true");
    assert_eq!(s(o, &["prepare_root", "files", "0"]), "/usr/lib/ostree/prepare-root.conf");
    assert_eq!(s(o, &["repo", "mode"]), "bare");
    assert_eq!(s(o, &["repo", "sysroot_readonly"]), "true");
    assert_eq!(s(o, &["repo", "sysroot_bootloader"]), "none");
    assert!(o.at(&["repo", "ex_fsverity"]).unwrap().is_null());
    assert_eq!(o.get("bootupd_state"), Some(&Value::Bool(true)));
    assert_eq!(s(o, &["grubenv", "boot_success"]), "1");
    assert!(o.at(&["grubenv", "boot_counter"]).unwrap().is_null());
    assert_eq!(s(o, &["image", "image"]), "quay.io/fedora/fedora-kinoite:44");
    assert_eq!(s(o, &["image", "digest"]), "sha256:c5e91226b7c92214a5fff04ca2836504663bb8025792ad77c9c70b9c5ace3b19");
}

/// The same tree as Bazzite names itself, and with composefs left to
/// prepare-root.conf in /etc and fs-verity required by the repository:
/// what load's plan problems will read.  Without /run/ostree-booted it's
/// still told by its composefs `/` and its ostree= karg.
#[test]
fn bazzite_ostree_guest() {
    let t = kinoite_ostree();
    std::fs::remove_file(t.p("/run/ostree-booted")).unwrap();
    std::fs::remove_file(t.p("/usr/lib/os-release")).unwrap();
    t.file("/usr/lib/os-release", "NAME=\"Bazzite\"\nID=bazzite\nID_LIKE=\"fedora\"\nVERSION_ID=44\nVARIANT_ID=bazzite\n")
        .file("/etc/ostree/prepare-root.conf", "[composefs]\nenabled = verity\n")
        .file("/sysroot/ostree/repo/config", "[core]\nmode=bare\n[ex-fsverity]\nrequired=true\n");
    let v = run(&t, true, kinoite_runner());
    assert_eq!(s(&v, &["os", "id"]), "bazzite");
    let o = v.at(&["boot", "ostree"]).unwrap();
    assert_eq!(o.get("booted"), Some(&Value::Bool(false)));
    assert_eq!(s(o, &["prepare_root", "composefs"]), "verity");
    // /etc's file replaces /usr/lib's whole: its readonly = true is gone.
    assert!(o.at(&["prepare_root", "readonly"]).unwrap().is_null());
    assert_eq!(arr(o, &["prepare_root", "files"]).len(), 2);
    assert_eq!(s(o, &["repo", "ex_fsverity", "required"]), "true");
    assert!(o.at(&["repo", "sysroot_bootloader"]).unwrap().is_null());
    // Without ostree's markers, the same tree is an ordinary system.
    std::fs::remove_file(t.p("/proc/self/mountinfo")).unwrap();
    t.file("/proc/self/mountinfo", "52 1 0:37 /root /sysroot ro - btrfs /dev/vda3 rw,subvolid=258,subvol=/root\n").file("/proc/cmdline", "root=UUID=x rw\n");
    let v = run(&t, true, kinoite_runner());
    assert!(v.at(&["boot", "ostree"]).is_none());
    assert_eq!(s(&v, &["storage", "backup_sources", "0", "path"]), "/sysroot");
}

/// On ostree, a mount no fstab entry matched is said, not left for restore
/// to find: here fstab's `/` names a subvolume that isn't the one at
/// /sysroot (as when its UUID resolves to another btrfs member), and then
/// /etc/fstab can't be read at all.
#[test]
fn ostree_unmatched_fstab_warns() {
    let t = kinoite_ostree();
    let fstab = std::fs::read_to_string(t.p("/etc/fstab")).unwrap();
    std::fs::remove_file(t.p("/etc/fstab")).unwrap();
    t.file("/etc/fstab", fstab.replace(" / btrfs subvol=root,", " / btrfs subvol=root2,").replace(" /home btrfs subvol=home,", " /home btrfs subvol=home2,"));
    let v = run(&t, true, kinoite_runner());
    let w: Vec<&str> = arr(&v, &["warnings"]).iter().map(|x| x.as_str().unwrap()).collect();
    assert_eq!(w.len(), 2, "{w:?}");
    assert!(w[0].starts_with("ostree: no running mount matched these fstab entries by device and subvol=: / (UUID=b0b1b2b3-b4b5-b6b7-b8b9-babbbcbdbebf -> vda3), /home (UUID="), "{}", w[0]);
    assert!(w[1].starts_with("ostree: no running mount matched fstab's `/`, so backup_sources has no `/`"), "{}", w[1]);
    let st = v.get("storage").unwrap();
    assert!(st.get("physical_root").unwrap().is_null());
    let paths: Vec<&str> = arr(st, &["backup_sources"]).iter().map(|x| x.get("path").and_then(Value::as_str).unwrap()).collect();
    assert_eq!(paths, vec!["/boot", "/boot/efi", "/sysroot", "/var", "/var/home"]);
    // boot.ostree still reads the physical root at /sysroot.
    assert_eq!(s(&v, &["boot", "ostree", "physical_root"]), "/sysroot");
    assert_eq!(arr(&v, &["boot", "ostree", "deployments"]).len(), 2);
    // No fstab: only the missing `/` is said.
    std::fs::remove_file(t.p("/etc/fstab")).unwrap();
    let v = run(&t, true, kinoite_runner());
    let w: Vec<&str> = arr(&v, &["warnings"]).iter().map(|x| x.as_str().unwrap()).collect();
    assert_eq!(w, vec!["ostree: /etc/fstab could not be read, so backup_sources has no `/`, storage.physical_root is null and boot.ostree read /sysroot"]);
}

/// An EFI boot entry as efivarfs gives it: its attributes, then the load
/// option (active), the description, the ESP's hard-drive node, the file.
fn efi_load_option(desc: &str, part: u32, start: u64, size: u64, partuuid: &str, file: &str) -> Vec<u8> {
    let utf16 = |t: &str| -> Vec<u8> { t.encode_utf16().chain([0]).flat_map(u16::to_le_bytes).collect() };
    let mut path = vec![4u8, 1, 42, 0];
    path.extend(part.to_le_bytes());
    path.extend(start.to_le_bytes());
    path.extend(size.to_le_bytes());
    path.extend(crate::parttable::build::guid_bytes(partuuid));
    path.extend([2u8, 2]);
    let f = utf16(file);
    path.extend([4u8, 4]);
    path.extend(((4 + f.len()) as u16).to_le_bytes());
    path.extend(f);
    path.extend([0x7fu8, 0xff, 4, 0]);
    let mut out = vec![7u8, 0, 0, 0];
    out.extend(1u32.to_le_bytes());
    out.extend((path.len() as u16).to_le_bytes());
    out.extend(utf16(desc));
    out.extend(path);
    out
}

/// `x` at `path` (object keys only), in place.
fn set_at(v: &mut Value, path: &[&str], x: Value) {
    let Some((last, rest)) = path.split_last() else { return };
    let mut cur = v;
    for p in rest {
        let Value::Obj(o) = cur else { panic!("no object at {p}") };
        cur = &mut o.iter_mut().find(|(k, _)| k == p).unwrap_or_else(|| panic!("no {p}")).1;
    }
    let Value::Obj(o) = cur else { panic!("no object for {last}") };
    match o.iter_mut().find(|(k, _)| k == last) {
        Some(slot) => slot.1 = x,
        None => o.push((last.to_string(), x)),
    }
}

const GENTOO_KV: &str = "6.18.63-gentoo-dist-bin";
const GENTOO_ESP: &str = "4e5f6a7b-1111-4222-8333-944455556666";

/// The Thinkpad's Gentoo in small, as the gentoo guest copies it: UEFI,
/// GPT with a 1 GiB ESP at /efi (here 35 MiB), a swap partition and an LVM
/// PV holding vg0 (root ext4, home XFS); OpenRC, merged /usr, Portage's
/// database, gentoo-kernel-bin installed by installkernel[dracut,grub]
/// (its install.conf, the .old copies `emerge --config` left), GRUB as
/// `gentoo` from NVRAM.
fn gentoo_thinkpad() -> Tree {
    use crate::efi::GLOBAL;
    const LVM_PV: &str = "e6d6d379-f507-44c2-a23c-238f2a3df928";
    const SWAP: &str = "0657fd6d-a4ab-43c4-84e5-0933c84b4f4f";
    let t = Tree::new("scn-gentoo");
    let k = GENTOO_KV;
    let mut img = vec![0u8; 48 * MIB];
    write_gpt(
        &mut img,
        512,
        &GptSpec {
            disk_guid: "6f1c2c3d-0000-4000-8000-00000000eeee",
            parts: vec![(ESP, GENTOO_ESP, 2048, 73727, 0, ""), (SWAP, "4e5f6a7b-1111-4222-8333-944455557777", 73728, 81919, 0, ""), (LVM_PV, "4e5f6a7b-1111-4222-8333-944455558888", 81920, 90111, 0, "")],
        },
    );
    put(&mut img, 2048, &fat::sample::fat32(71680, 0x1234_abcd, ""));
    put(&mut img, 73728, &misc::sample::swap(1024, ""));
    let meta = lvm::sample::METADATA.replace("swap_1 {", "home {").replace("sw4p00-", "h0me00-").replace("\"debian\"", "\"thinkpad\"");
    put(&mut img, 81920, &lvm::sample::pv_with(512, 1 << 20, &meta));
    let vgid = "Yb3kFa0Pz1AbCdEfGhIjKlMnOpQrStUv";
    let root_uuid = "10111213-1415-1617-1819-1a1b1c1d1e1f";
    let home_uuid = "a0a1a2a3-a4a5-a6a7-a8a9-aaabacadaeaf";
    let swap_uuid = "c0c1c2c3-c4c5-c6c7-c8c9-cacbcccdcecf";
    let v_ = |n: &str| format!("/sys/firmware/efi/efivars/{n}-{GLOBAL}");
    t.disk("nvme0n1", "259:0", &img, 512)
        .partition("nvme0n1", "nvme0n1p1", "259:1", 1, 2048, 71680, &img)
        .partition("nvme0n1", "nvme0n1p2", "259:2", 2, 73728, 8192, &img)
        .partition("nvme0n1", "nvme0n1p3", "259:3", 3, 81920, 8192, &img)
        .file("/sys/block/nvme0n1/nvme0n1p3/holders/dm-0", "")
        .file("/sys/block/nvme0n1/nvme0n1p3/holders/dm-1", "")
        .disk("dm-0", "253:0", &ext::sample::image(0x4 | 0x8 | 0x10 | 0x20, 0x2 | 0x40 | 0x80 | 0x200, 0x1 | 0x2 | 0x400, ""), 512)
        .file("/sys/block/dm-0/dm/name", "vg0-root\n")
        .file("/sys/block/dm-0/dm/uuid", format!("LVM-{vgid}r00t00aaaabbbbccccddddeeeeffffff\n"))
        .file("/sys/block/dm-0/slaves/nvme0n1p3", "")
        .disk("dm-1", "253:1", &crate::probe::xfs::sample::image(""), 512)
        .file("/sys/block/dm-1/dm/name", "vg0-home\n")
        .file("/sys/block/dm-1/dm/uuid", format!("LVM-{vgid}h0me00aaaabbbbccccddddeeeeffffff\n"))
        .file("/sys/block/dm-1/slaves/nvme0n1p3", "")
        .symlink("/dev/mapper/vg0-root", "../dm-0")
        .symlink("/dev/mapper/vg0-home", "../dm-1")
        .symlink(&format!("/dev/disk/by-partuuid/{GENTOO_ESP}"), "../../nvme0n1p1")
        .file("/sys/firmware/efi/fw_platform_size", "64\n")
        .file(&v_("Boot0001"), efi_load_option("gentoo", 1, 2048, 71680, GENTOO_ESP, "\\EFI\\gentoo\\grubx64.efi"))
        .file(&v_("BootOrder"), [7u8, 0, 0, 0, 1, 0])
        .file(&v_("BootCurrent"), [6u8, 0, 0, 0, 1, 0])
        .file(&v_("SecureBoot"), [6u8, 0, 0, 0, 0])
        .file(
            "/proc/self/mountinfo",
            "26 1 253:0 / / rw,noatime shared:1 - ext4 /dev/mapper/vg0-root rw\n27 26 259:1 / /efi rw,relatime shared:2 - vfat /dev/nvme0n1p1 rw,fmask=0077,dmask=0077\n28 26 253:1 / /home rw,noatime shared:3 - xfs /dev/mapper/vg0-home rw,attr2,inode64\n",
        )
        .file("/proc/swaps", "Filename\t\t\t\tType\t\tSize\t\tUsed\t\tPriority\n/dev/nvme0n1p2                          partition\t4096\t\t0\t\t-2\n")
        .file(
            "/etc/fstab",
            format!("UUID={root_uuid}  /      ext4  defaults,noatime  0 1\nUUID={home_uuid}  /home  xfs   defaults,noatime  0 2\nUUID=1234-ABCD  /efi   vfat  umask=0077        0 2\nUUID={swap_uuid}  none   swap  sw                0 0\n"),
        )
        .file("/etc/os-release", "NAME='Gentoo'\nID='gentoo'\nPRETTY_NAME='Gentoo Linux'\nVERSION='2.18'\nVERSION_ID='2.18'\nHOME_URL='https://www.gentoo.org/'\n")
        .file("/etc/gentoo-release", "Gentoo Base System release 2.18\n")
        .symlink("/lib", "usr/lib")
        .file("/usr/lib/kernel/install.conf", "# This file is managed by sys-kernel/installkernel\nlayout=grub\ninitrd_generator=dracut\nuki_generator=none\n")
        .dir("/etc/kernel/postinst.d")
        .file(&format!("/boot/vmlinuz-{k}"), "k")
        .file(&format!("/boot/initramfs-{k}.img"), "i")
        .file(&format!("/boot/config-{k}"), "")
        .file(&format!("/boot/System.map-{k}"), "")
        .file(&format!("/boot/vmlinuz-{k}.old"), "k")
        .file(&format!("/boot/initramfs-{k}.img.old"), "i")
        .file(&format!("/boot/config-{k}.old"), "")
        .file(&format!("/boot/System.map-{k}.old"), "")
        .file("/boot/amd-uc.img", "u")
        .file("/boot/intel-uc.img", "u")
        .file("/boot/grub/x86_64-efi/normal.mod", "x")
        .file(
            "/boot/grub/grub.cfg",
            format!("insmod part_gpt\ninsmod lvm\ninsmod ext2\nmenuentry 'Gentoo GNU/Linux' {{\n\tlinux\t/boot/vmlinuz-{k} root=UUID={root_uuid} ro\n\tinitrd\t/boot/intel-uc.img /boot/amd-uc.img /boot/initramfs-{k}.img\n}}\n"),
        )
        .file("/etc/default/grub", "GRUB_DISTRIBUTOR=\"Gentoo\"\n")
        .dir(&format!("/usr/lib/modules/{k}/kernel"))
        .symlink(&format!("/usr/lib/modules/{k}/vmlinuz"), &format!("../../../src/linux-{k}/arch/x86/boot/bzImage"))
        .file("/efi/EFI/gentoo/grubx64.efi", vec![2u8; 400])
        .file("/proc/sys/kernel/osrelease", format!("{k}\n"))
        .file("/proc/sys/kernel/hostname", "thinkpad\n")
        .file("/proc/cmdline", format!("BOOT_IMAGE=/boot/vmlinuz-{k} root=UUID={root_uuid} ro\n"));
    for (cat, pf, use_) in [
        ("sys-boot", "grub-2.16-r1", "device-mapper fonts nls"),
        ("sys-boot", "grub-themes-gentoo-1.0-r2", ""),
        ("sys-boot", "efibootmgr-18-r2", ""),
        ("sys-kernel", "dracut-112", ""),
        ("sys-kernel", "gentoo-kernel-bin-6.18.63", "abi_x86_64 amd64 elibc_glibc initramfs kernel_linux"),
        ("sys-kernel", "installkernel-70-r1", "dracut grub"),
        ("sys-kernel", "linux-firmware-20261019", ""),
        ("sys-fs", "lvm2-2.03.39", "lvm"),
        ("sys-fs", "xfsprogs-7.2.1", ""),
        ("sys-fs", "e2fsprogs-1.47.4", ""),
        ("sys-fs", "dosfstools-4.2", ""),
        ("sys-apps", "systemd-utils-258.3", "udev"),
        ("media-fonts", "font-adobe-100dpi-1.0.4", ""),
        ("acct-group", "avahi-0-r3", ""),
        ("net-dns", "avahi-0.8-r10", ""),
    ] {
        let d = format!("/var/db/pkg/{cat}/{pf}");
        t.file(&format!("{d}/PF"), format!("{pf}\n")).file(&format!("{d}/CATEGORY"), format!("{cat}\n")).file(&format!("{d}/CHOST"), "x86_64-pc-linux-gnu\n").file(&format!("{d}/USE"), format!("{use_}\n"));
    }
    // A merge under way isn't installed.
    t.file("/var/db/pkg/sys-kernel/-MERGING-gentoo-kernel-bin-6.18.64/PF", "gentoo-kernel-bin-6.18.64\n");
    t
}

/// The Thinkpad's Gentoo read back and planned: Portage's packages by bare
/// name, dracut from installkernel's install.conf, the .old copies apart
/// from the kernels; load remakes the initramfs by its installkernel name,
/// installs GRUB in /efi as `gentoo` and notes the .old copies.
#[test]
fn gentoo_thinkpad_guest() {
    let k = GENTOO_KV;
    let v = run(&gentoo_thinkpad(), true, FakeRunner::default());
    assert_eq!(s(&v, &["os", "id"]), "gentoo");
    assert_eq!(s(&v, &["os", "version_id"]), "2.18");
    assert_eq!(s(&v, &["os", "pretty_name"]), "Gentoo Linux");
    assert_eq!(s(&v, &["firmware", "mode"]), "uefi");
    assert_eq!(s(&v, &["boot", "packages", "grub"]), "2.16-r1");
    assert_eq!(s(&v, &["boot", "packages", "dracut"]), "112");
    assert_eq!(s(&v, &["boot", "packages", "gentoo-kernel-bin"]), "6.18.63");
    assert_eq!(s(&v, &["boot", "packages", "installkernel"]), "70-r1");
    assert_eq!(s(&v, &["boot", "packages", "lvm2"]), "2.03.39");
    assert_eq!(s(&v, &["boot", "packages", "systemd-utils"]), "258.3");
    assert!(v.at(&["boot", "packages", "grub-themes-gentoo"]).is_none());
    let l = v.at(&["boot", "loader"]).unwrap();
    assert_eq!(s(l, &["name"]), "grub");
    assert_eq!(l.get("via_shim"), Some(&Value::Bool(false)));
    // Not in detect_loader's UEFI list, as on Arch: harmless.
    assert!(l.get("package").unwrap().is_null());
    assert!(l.get("evidence").unwrap().compact().contains("\\\\EFI\\\\gentoo\\\\grubx64.efi"), "{}", l.compact());
    assert_eq!(s(&v, &["boot", "esps", "0", "mounted_at"]), "/efi");
    assert_eq!(s(&v, &["boot", "esps", "0", "device"]), "nvme0n1p1");
    let i = v.at(&["boot", "initramfs"]).unwrap();
    assert_eq!(s(i, &["generator"]), "dracut");
    assert_eq!(s(i, &["installkernel", "conf"]), "/usr/lib/kernel/install.conf");
    assert_eq!(s(i, &["installkernel", "layout"]), "grub");
    assert_eq!(s(i, &["installkernel", "uki_generator"]), "none");
    let ev: Vec<&str> = arr(i, &["evidence"]).iter().filter_map(Value::as_str).collect();
    assert_eq!(ev, vec!["/usr/lib/kernel/install.conf says initrd_generator=dracut", "gentoo-kernel-bin-6.18.63 is built with USE=initramfs"]);
    assert!(arr(i, &["lib_modules_initrd"]).is_empty());
    let kernels = arr(&v, &["boot", "kernels", "installed"]);
    assert_eq!(kernels.len(), 1, "{}", v.at(&["boot", "kernels"]).unwrap().compact());
    assert_eq!(s(&kernels[0], &["version"]), k);
    assert_eq!(u(&kernels[0], &["initrd_bytes"]), 1);
    assert_eq!(kernels[0].get("modules_installed"), Some(&Value::Bool(true)));
    let old: Vec<&str> = arr(&v, &["boot", "kernels", "old_copies"]).iter().filter_map(Value::as_str).collect();
    assert_eq!(old, vec![format!("System.map-{k}.old"), format!("config-{k}.old"), format!("initramfs-{k}.img.old"), format!("vmlinuz-{k}.old")]);
    let vg = v.at(&["storage", "lvm", "volume_groups", "0"]).unwrap();
    assert_eq!(s(vg, &["name"]), "vg0");
    assert_eq!(s(vg, &["logical_volumes", "1", "name"]), "home");
    assert!(arr(&v, &["warnings"]).is_empty(), "{:?}", v.get("warnings"));

    use crate::load::{commands, layout::Layout, target::Mapping, Parsed, Stage};
    let plan = |v: Value| {
        let l = Layout::from_json(v).unwrap();
        let Ok(Parsed::Run(o)) = crate::load::parse_args(&["l.json".to_string()]) else { unreachable!() };
        commands::build(&l, &o, &Mapping { pairs: vec![("nvme0n1".into(), "vda".into())], ..Mapping::default() })
    };
    let b = plan(v.clone());
    assert!(b.problems.is_empty(), "{:?}", b.problems);
    let bt: String = b.stage(Stage::Boot).iter().map(commands::Cmd::shell).collect();
    let dracut = bt.find(&format!("chroot /mnt/bko-target dracut --force /boot/initramfs-{k}.img {k}\n")).expect(&bt);
    let install = bt.find("chroot /mnt/bko-target grub-install --target=x86_64-efi --efi-directory=/efi --bootloader-id=gentoo --no-nvram\n").expect(&bt);
    let cfg = bt.find("chroot /mnt/bko-target env LANG=C grub-mkconfig -o /boot/grub/grub.cfg\n").expect(&bt);
    let nvram = bt.find("efibootmgr --quiet --create --disk /dev/vda --part 1 --label gentoo --loader '\\EFI\\gentoo\\grubx64.efi'\n").expect(&bt);
    assert!(dracut < install && install < cfg && cfg < nvram, "{bt}");
    assert!(!bt.contains("update-initramfs") && !bt.contains("update-grub") && !bt.contains("dracut --force --kver") && !bt.contains(".old"), "{bt}");
    assert!(b.notes.iter().any(|n| n == &format!("Gentoo: installkernel's .old copies in /boot (System.map-{k}.old, config-{k}.old, initramfs-{k}.img.old, vmlinuz-{k}.old) came back as they were: grub-mkconfig lists them, and this stage doesn't remake them")), "{:?}", b.notes);
    let mnt: String = b.stage(Stage::Mount).iter().map(commands::Cmd::shell).collect();
    assert!(mnt.contains("/mnt/bko-target/efi\n"), "{mnt}");

    // Each refusal names what it refuses.
    let with = |path: &[&str], val: Value| {
        let mut w = v.clone();
        set_at(&mut w, path, val);
        plan(w).problems
    };
    let p = with(&["boot", "initramfs", "generator"], "genkernel".into());
    assert_eq!(p, vec!["Gentoo: the initramfs is genkernel's; bko-holt load remakes only dracut's (or none)"]);
    assert!(with(&["boot", "initramfs", "generator"], "ugrd".into())[0].contains("ugrd's"));
    assert!(with(&["boot", "initramfs", "generator"], "mkinitcpio".into())[0].contains("initramfs generator mkinitcpio isn't one"));
    let p = with(&["boot", "initramfs", "installkernel", "uki_generator"], "ukify".into());
    assert_eq!(p, vec!["Gentoo: /usr/lib/kernel/install.conf says uki_generator=ukify; bko-holt load doesn't make unified kernel images"]);
    let p = with(&["boot", "initramfs", "installkernel", "layout"], "bls".into());
    assert!(p.len() == 1 && p[0].contains("says layout=bls; bko-holt load supports only installkernel's grub and compat layouts"), "{p:?}");
    assert!(with(&["boot", "initramfs", "installkernel", "layout"], "compat".into()).is_empty());
    assert!(with(&["boot", "initramfs", "installkernel", "layout"], Value::Null)[0].contains("names no layout"));
    let p = with(&["boot", "initramfs", "lib_modules_initrd"], vec![k].into());
    assert_eq!(p, vec![format!("Gentoo: kernel {k} has its initramfs at /lib/modules/{k}/initrd, where bko-holt load doesn't make one")]);
    let p = with(&["boot", "loader", "via_shim"], true.into());
    assert!(p.len() == 1 && p[0].starts_with("Gentoo through shim (Secure Boot, grub)"), "{p:?}");
    // none: nothing made, said.
    let mut w = v.clone();
    set_at(&mut w, &["boot", "initramfs", "generator"], "none".into());
    let b = plan(w);
    assert!(b.problems.is_empty(), "{:?}", b.problems);
    let bt: String = b.stage(Stage::Boot).iter().map(commands::Cmd::shell).collect();
    assert!(!bt.contains("dracut"), "{bt}");
    assert!(b.notes.iter().any(|n| n.starts_with("Gentoo: no initramfs generator")), "{:?}", b.notes);
}

/// Gentoo's plan for a Thinkpad tree changed by `change`, and its dump.
fn gentoo_plan(change: impl Fn(&Tree)) -> (Value, crate::load::commands::Built) {
    use crate::load::{commands, layout::Layout, target::Mapping, Parsed};
    let t = gentoo_thinkpad();
    change(&t);
    let v = run(&t, true, FakeRunner::default());
    let l = Layout::from_json(v.clone()).unwrap();
    let Ok(Parsed::Run(o)) = crate::load::parse_args(&["l.json".to_string()]) else { unreachable!() };
    (v, commands::build(&l, &o, &Mapping { pairs: vec![("nvme0n1".into(), "vda".into())], ..Mapping::default() }))
}

/// One dracut command per kernel with an initramfs, in version order; a
/// kernel with an initramfs but no /lib/modules/K keeps its restored image,
/// with a note; only dracut with no initramfs at all is refused; an image
/// at /lib/modules/K/initrd (through merged /usr's /lib) is found and
/// refused.
#[test]
fn gentoo_dracut_per_kernel() {
    use crate::load::{commands::Cmd, Stage};
    let k = GENTOO_KV;
    let (k2, k3) = ("6.18.9-gentoo-dist-bin", "6.19.1-gentoo-dist-bin");
    let no_modules = |kv: &str| format!("Gentoo: kernel {kv} has no /lib/modules/{kv}, so dracut can't remake its initramfs; the restored one stays");
    let dracut = |b: &crate::load::commands::Built| b.stage(Stage::Boot).iter().map(Cmd::shell).collect::<String>().lines().filter(|l| l.contains(" dracut ")).map(|l| format!("{l}\n")).collect::<Vec<_>>();
    let line = |kv: &str| format!("chroot /mnt/bko-target dracut --force /boot/initramfs-{kv}.img {kv}\n");

    // Three kernels: k2 and k with modules, k3 without.
    let (v, b) = gentoo_plan(|t| {
        t.file(&format!("/boot/vmlinuz-{k2}"), "k").file(&format!("/boot/initramfs-{k2}.img"), "i").dir(&format!("/usr/lib/modules/{k2}/kernel"));
        t.file(&format!("/boot/vmlinuz-{k3}"), "k").file(&format!("/boot/initramfs-{k3}.img"), "i");
    });
    let kernels: Vec<String> = arr(&v, &["boot", "kernels", "installed"]).iter().map(|x| s(x, &["version"]).to_string()).collect();
    assert_eq!(kernels, vec![k2, k, k3]);
    assert!(b.problems.is_empty(), "{:?}", b.problems);
    assert_eq!(dracut(&b), vec![line(k2), line(k)]);
    assert!(b.notes.contains(&no_modules(k3)), "{:?}", b.notes);

    // The only kernel has an initramfs but no modules: it keeps its image,
    // and that isn't a refusal.
    let (v, b) = gentoo_plan(|t| std::fs::remove_dir_all(t.p(&format!("/usr/lib/modules/{k}"))).unwrap());
    assert_eq!(arr(&v, &["boot", "kernels", "installed"])[0].get("modules_installed"), Some(&Value::Bool(false)));
    assert!(b.problems.is_empty(), "{:?}", b.problems);
    assert!(dracut(&b).is_empty());
    assert!(b.notes.contains(&no_modules(k)), "{:?}", b.notes);

    // install.conf says dracut, but no kernel has an initramfs.
    let (v, b) = gentoo_plan(|t| {
        for f in [format!("/boot/initramfs-{k}.img"), format!("/boot/initramfs-{k}.img.old")] {
            std::fs::remove_file(t.p(&f)).unwrap();
        }
    });
    assert_eq!(s(&v, &["boot", "initramfs", "generator"]), "dracut");
    assert_eq!(b.problems, vec!["Gentoo: the initramfs generator is dracut, but no kernel in /boot has an initramfs to remake"]);

    // systemd's kernel-install (installkernel[systemd], layout grub) names
    // the kernel kernel-K: the same kernel, its .old copy apart.
    let (v, b) = gentoo_plan(|t| {
        for (from, to) in [(format!("/boot/vmlinuz-{k}"), format!("/boot/kernel-{k}")), (format!("/boot/vmlinuz-{k}.old"), format!("/boot/kernel-{k}.old"))] {
            std::fs::rename(t.p(&from), t.p(&to)).unwrap();
        }
    });
    let kernels = arr(&v, &["boot", "kernels", "installed"]);
    assert_eq!(kernels.len(), 1);
    assert_eq!(u(&kernels[0], &["vmlinuz_bytes"]), 1);
    assert!(arr(&v, &["boot", "kernels", "old_copies"]).iter().any(|x| x.as_str() == Some(format!("kernel-{k}.old").as_str())));
    assert!(b.problems.is_empty(), "{:?}", b.problems);
    assert_eq!(dracut(&b), vec![line(k)]);

    // /lib/modules/K/initrd, read through /lib -> usr/lib.
    let (v, b) = gentoo_plan(|t| {
        t.file(&format!("/usr/lib/modules/{k}/initrd"), "i");
    });
    let lmi: Vec<&str> = arr(&v, &["boot", "initramfs", "lib_modules_initrd"]).iter().filter_map(Value::as_str).collect();
    assert_eq!(lmi, vec![k]);
    assert_eq!(b.problems, vec![format!("Gentoo: kernel {k} has its initramfs at /lib/modules/{k}/initrd, where bko-holt load doesn't make one")]);
}

/// A Gentoo without installkernel's configuration: none when /boot has no
/// initramfs, genkernel when it's installed, else dracut; and a hand-built
/// kernel without an initramfs plans no initramfs step.
#[test]
fn gentoo_generator_without_installkernel() {
    let k = GENTOO_KV;
    let t = gentoo_thinkpad();
    std::fs::remove_file(t.p("/usr/lib/kernel/install.conf")).unwrap();
    let gen = |t: &Tree| {
        let v = run(t, true, FakeRunner::default());
        let i = v.at(&["boot", "initramfs"]).unwrap().clone();
        (s(&i, &["generator"]).to_string(), arr(&i, &["evidence"]).iter().filter_map(|x| x.as_str().map(str::to_string)).collect::<Vec<_>>(), i.get("installkernel").unwrap().is_null(), v)
    };
    let (g, ev, no_ik, _) = gen(&t);
    assert_eq!(g, "dracut");
    assert!(no_ik);
    assert_eq!(ev[0], "no installkernel configuration (/etc/kernel/install.conf, /usr/lib/kernel/install.conf)");
    assert_eq!(ev[1], "/boot has initramfs-* and genkernel isn't installed");
    // An install.conf that names no initrd_generator: the /boot rules
    // decide, and the file is kept as evidence.
    t.file("/usr/lib/kernel/install.conf", "layout=grub\n");
    let (g, ev, no_ik, v) = gen(&t);
    assert_eq!(g, "dracut");
    assert!(!no_ik);
    assert_eq!(s(&v, &["boot", "initramfs", "installkernel", "layout"]), "grub");
    assert!(v.at(&["boot", "initramfs", "installkernel", "initrd_generator"]).unwrap().is_null());
    assert_eq!(ev[..2], ["/usr/lib/kernel/install.conf names no initrd_generator".to_string(), "/boot has initramfs-* and genkernel isn't installed".to_string()]);
    std::fs::remove_file(t.p("/usr/lib/kernel/install.conf")).unwrap();
    t.file("/var/db/pkg/sys-kernel/genkernel-4.3.17/PF", "genkernel-4.3.17\n");
    assert_eq!(gen(&t).0, "genkernel");
    for f in [format!("/boot/initramfs-{k}.img"), format!("/boot/initramfs-{k}.img.old")] {
        std::fs::remove_file(t.p(&f)).unwrap();
    }
    std::fs::write(t.p("/var/db/pkg/sys-kernel/gentoo-kernel-bin-6.18.63/USE"), "abi_x86_64 amd64 elibc_glibc kernel_linux\n").unwrap();
    let (g, ev, _, v) = gen(&t);
    assert_eq!(g, "none");
    assert_eq!(ev[1..], ["/boot has no initramfs-*".to_string(), "gentoo-kernel-bin-6.18.63 is built without USE=initramfs".to_string()]);
    use crate::load::{commands, layout::Layout, target::Mapping, Parsed, Stage};
    let l = Layout::from_json(v).unwrap();
    let Ok(Parsed::Run(o)) = crate::load::parse_args(&["l.json".to_string()]) else { unreachable!() };
    let b = commands::build(&l, &o, &Mapping { pairs: vec![("nvme0n1".into(), "vda".into())], ..Mapping::default() });
    assert!(b.problems.is_empty(), "{:?}", b.problems);
    let bt: String = b.stage(Stage::Boot).iter().map(commands::Cmd::shell).collect();
    assert!(!bt.contains("dracut") && !bt.contains("update-initramfs") && bt.contains("grub-install"), "{bt}");
    // installkernel's install.conf in /etc wins over /usr/lib's.
    t.file("/etc/kernel/install.conf", "layout=compat\ninitrd_generator=ugrd\nuki_generator=none\n");
    let (g, ev, _, _) = gen(&t);
    assert_eq!(g, "ugrd");
    assert_eq!(ev[0], "/etc/kernel/install.conf says initrd_generator=ugrd");
}

const PIKA_KV: &str = "7.3.3-pikaos";
const PIKA_ESP: &str = "5a6b7c8d-1111-4222-8333-944455556666";
/// The root's XFS (xfs::sample::image's UUID).
const PIKA_ROOT: &str = "a0a1a2a3-a4a5-a6a7-a8a9-aaabacadaeaf";

/// PikaOS 4 as its installer (pikainstall) lays it out, in small (the ESP
/// is 512 MiB there, /boot 1 GiB): GPT with a vfat ESP "EFI" at /boot/efi,
/// an ext4 /boot "PikaBoot" on an XBOOTLDR partition with no loader
/// entries, / XFS "PikaRoot"; rEFInd in EFI/refind and at the fallback
/// path with its theme and ext4 driver, refind_linux.conf in /boot;
/// booster's images (and a dpkg backup) beside kernel-pika's kernel,
/// initramfs-tools installed too; rEFInd booted from its NVRAM entry.
fn pikaos() -> Tree {
    use crate::efi::GLOBAL;
    use crate::parttable::XBOOTLDR;
    let t = Tree::new("scn-pikaos");
    let k = PIKA_KV;
    let mut img = vec![0u8; 48 * MIB];
    write_gpt(
        &mut img,
        512,
        &GptSpec {
            disk_guid: "7a1c2c3d-0000-4000-8000-00000000eeee",
            parts: vec![(ESP, PIKA_ESP, 2048, 34815, 0, "ESP"), (XBOOTLDR, "5a6b7c8d-1111-4222-8333-944455557777", 34816, 67583, 0, "boot"), (LINUX, "5a6b7c8d-1111-4222-8333-944455558888", 67584, 96255, 0, "root")],
        },
    );
    put(&mut img, 2048, &fat::sample::fat32(32768, 0x1234_abcd, "EFI"));
    put(&mut img, 34816, &ext::sample::image(0x4 | 0x8 | 0x10 | 0x20, 0x2 | 0x40 | 0x80 | 0x200, 0x1 | 0x2 | 0x400, "PikaBoot"));
    put(&mut img, 67584, &crate::probe::xfs::sample::image("PikaRoot"));
    let boot_uuid = "10111213-1415-1617-1819-1a1b1c1d1e1f";
    let v_ = |n: &str| format!("/sys/firmware/efi/efivars/{n}-{GLOBAL}");
    let refind = vec![3u8; 300];
    t.disk("vda", "254:0", &img, 512)
        .partition("vda", "vda1", "254:1", 1, 2048, 32768, &img)
        .partition("vda", "vda2", "254:2", 2, 34816, 32768, &img)
        .partition("vda", "vda3", "254:3", 3, 67584, 28672, &img)
        .file("/sys/block/vda/device/model", "")
        .symlink(&format!("/dev/disk/by-partuuid/{PIKA_ESP}"), "../../vda1")
        .file("/sys/firmware/efi/fw_platform_size", "64\n")
        .file(&v_("Boot0001"), efi_load_option("rEFInd Boot Manager", 1, 2048, 32768, PIKA_ESP, "\\EFI\\refind\\refind_x64.efi"))
        .file(&v_("BootOrder"), [7u8, 0, 0, 0, 1, 0])
        .file(&v_("BootCurrent"), [6u8, 0, 0, 0, 1, 0])
        .file(&v_("SecureBoot"), [6u8, 0, 0, 0, 0])
        .file(
            "/proc/self/mountinfo",
            "26 1 254:3 / / rw,relatime shared:1 - xfs /dev/vda3 rw,attr2,inode64\n27 26 254:2 / /boot rw,relatime shared:2 - ext4 /dev/vda2 rw\n28 27 254:1 / /boot/efi rw,relatime shared:3 - vfat /dev/vda1 rw,fmask=0077,dmask=0077\n",
        )
        .file("/proc/swaps", "Filename\t\t\t\tType\t\tSize\t\tUsed\t\tPriority\n/dev/zram0                              partition\t4096\t\t0\t\t100\n")
        .file("/etc/fstab", format!("UUID={PIKA_ROOT} / xfs defaults 0 1\nUUID={boot_uuid} /boot ext4 defaults 0 2\nUUID=1234-ABCD /boot/efi vfat umask=0077 0 2\n"))
        .file("/etc/os-release", "PRETTY_NAME=\"PikaOS 4\"\nNAME=\"PikaOS\"\nVERSION_ID=\"4\"\nVERSION=\"4\"\nVERSION_CODENAME=nest\nID=pika\nID_LIKE=debian\nDEBIAN_CODENAME=sid\n")
        .file("/etc/debian_version", "forky/sid\n")
        .symlink("/lib", "usr/lib")
        .file("/etc/booster.yaml", "# written by pikainstall\nvconsole: true\nextra_files: busybox\nenable_lvm: true\nmodules: usbhid,hid_generic\nmodules_force_load: usbhid,hid_generic\ncompression: lz4\nstrip: true\nenable_plymouth: true\n")
        .file(&format!("/boot/vmlinuz-{k}"), "k")
        .file(&format!("/boot/booster.img-{k}"), "bi")
        .file(&format!("/boot/booster.img-{k}.dpkg-bak"), "old")
        .file(&format!("/boot/config-{k}"), "")
        .file(&format!("/boot/System.map-{k}"), "")
        .file(
            "/boot/refind_linux.conf",
            format!("\"Boot with standard options\"  \"nowatchdog rootflags=rw,relatime root=UUID={PIKA_ROOT} quiet splash ---\"\n\"Boot with logging\"          \"nowatchdog rootflags=rw,relatime root=UUID={PIKA_ROOT} ---\"\n\"Boot with safe graphics\"    \"nowatchdog rootflags=rw,relatime root=UUID={PIKA_ROOT} nomodeset ---\"\n"),
        )
        .dir(&format!("/usr/lib/modules/{k}/kernel"))
        .file("/boot/efi/EFI/refind/refind_x64.efi", &refind)
        .file("/boot/efi/EFI/refind/refind.conf", "timeout 5\ninclude refind-dark/refind-dark.conf\n")
        .file("/boot/efi/EFI/refind/drivers_x64/ext4_x64.efi", vec![4u8; 100])
        .file("/boot/efi/EFI/refind/refind-dark/refind-dark.conf", "banner refind-dark/background.png\n")
        .file("/boot/efi/EFI/BOOT/bootx64.efi", &refind)
        .file("/boot/efi/EFI/BOOT/refind.conf", "timeout 5\ninclude refind-dark/refind-dark.conf\n")
        .file("/boot/efi/EFI/BOOT/drivers_x64/ext4_x64.efi", vec![4u8; 100])
        .file("/proc/sys/kernel/osrelease", format!("{k}\n"))
        .file("/proc/sys/kernel/hostname", "pika-pc\n")
        .file("/proc/cmdline", format!("initrd=\\booster.img-{k} nowatchdog rootflags=rw,relatime root=UUID={PIKA_ROOT} quiet splash ---\n"));
    let pkgs = [
        ("booster", "0.12.1-100pika1"),
        ("kernel-pika", "7.3.3-101pika1"),
        ("kernel-pika-runtime", "7.3.3-101pika1"),
        ("linux-image-7.3.3-pikaos", "7.3.3-101pika1"),
        ("linux-headers-7.3.3-pikaos", "7.3.3-101pika1"),
        ("refind", "0.14.2-100pika2"),
        ("pika-refind-theme", "1.0-100pika1"),
        ("refind-btrfs", "0.6.0-100pika1"),
        ("zram-tools", "0.3.5-1"),
        ("initramfs-tools", "0.148.3"),
        ("efibootmgr", "18-2"),
        ("xfsprogs", "6.17.0-1"),
        ("e2fsprogs", "1.47.3-1"),
        ("dosfstools", "4.2-1.2"),
    ];
    let status: String = pkgs.iter().map(|(n, v)| format!("Package: {n}\nStatus: install ok installed\nArchitecture: amd64\nVersion: {v}\n\n")).collect();
    t.file("/var/lib/dpkg/status", status);
    t
}

/// PikaOS's plan from a tree, the source disk vda going to vdb.
fn pikaos_plan(t: &Tree) -> (Value, crate::load::commands::Built) {
    use crate::load::{commands, layout::Layout, target::Mapping, Parsed};
    let v = run(t, true, FakeRunner::default());
    let l = Layout::from_json(v.clone()).unwrap();
    let Ok(Parsed::Run(o)) = crate::load::parse_args(&["l.json".to_string()]) else { unreachable!() };
    (v, commands::build(&l, &o, &Mapping { pairs: vec![("vda".into(), "vdb".into())], ..Mapping::default() }))
}

/// PikaOS read back and planned (docs/design/pikaos.md): booster over
/// initramfs-tools, with booster.yaml's keys; booster.img-K as K's image
/// and its .dpkg-bak apart; rEFInd from BootCurrent, and the XBOOTLDR /boot
/// not taken for systemd-boot's; refind_linux.conf recorded.  load remakes
/// booster's image for each kernel, installs no loader and runs neither
/// refind-install nor update-initramfs, and makes rEFInd's boot entry.
#[test]
fn pikaos_guest() {
    use crate::load::{commands::Cmd, Stage};
    let k = PIKA_KV;
    let (v, b) = pikaos_plan(&pikaos());
    assert_eq!(s(&v, &["os", "id"]), "pika");
    assert_eq!(s(&v, &["os", "pretty_name"]), "PikaOS 4");
    assert_eq!(s(&v, &["firmware", "mode"]), "uefi");
    for (p, ver) in [("booster", "0.12.1-100pika1"), ("kernel-pika", "7.3.3-101pika1"), ("kernel-pika-runtime", "7.3.3-101pika1"), ("pika-refind-theme", "1.0-100pika1"), ("refind-btrfs", "0.6.0-100pika1"), ("zram-tools", "0.3.5-1"), ("refind", "0.14.2-100pika2")] {
        assert_eq!(s(&v, &["boot", "packages", p]), ver, "{p}");
    }
    assert!(v.at(&["boot", "packages", "linux-image-7.3.3-pikaos"]).is_none());
    let i = v.at(&["boot", "initramfs"]).unwrap();
    assert_eq!(s(i, &["generator"]), "booster");
    assert_eq!(
        i.get("booster").unwrap().compact(),
        r#"{"conf":"/etc/booster.yaml","universal":null,"enable_lvm":true,"modules":"usbhid,hid_generic","modules_force_load":"usbhid,hid_generic","compression":"lz4"}"#
    );
    let kernels = arr(&v, &["boot", "kernels", "installed"]);
    assert_eq!(kernels.len(), 1, "{}", v.at(&["boot", "kernels"]).unwrap().compact());
    assert_eq!(s(&kernels[0], &["version"]), k);
    assert_eq!(u(&kernels[0], &["initrd_bytes"]), 2);
    assert_eq!(kernels[0].get("modules_installed"), Some(&Value::Bool(true)));
    let old: Vec<&str> = arr(&v, &["boot", "kernels", "old_copies"]).iter().filter_map(Value::as_str).collect();
    assert_eq!(old, vec![format!("booster.img-{k}.dpkg-bak")]);
    let l = v.at(&["boot", "loader"]).unwrap();
    assert_eq!(s(l, &["name"]), "refind");
    assert_eq!(s(l, &["package"]), "refind");
    assert_eq!(l.get("via_shim"), Some(&Value::Bool(false)));
    assert!(v.at(&["boot", "systemd_boot"]).unwrap().is_null(), "{}", v.at(&["boot", "systemd_boot"]).unwrap().compact());
    assert_eq!(s(&v, &["boot", "esps", "0", "mounted_at"]), "/boot/efi");
    let rc = v.at(&["boot", "refind", "linux_conf"]).unwrap();
    assert_eq!(s(rc, &["path"]), "/boot/refind_linux.conf");
    let lines = arr(rc, &["lines"]);
    assert_eq!(lines.len(), 3);
    assert_eq!(s(&lines[0], &["title"]), "Boot with standard options");
    assert_eq!(s(&lines[0], &["options"]), format!("nowatchdog rootflags=rw,relatime root=UUID={PIKA_ROOT} quiet splash ---"));
    assert_eq!(s(&lines[2], &["title"]), "Boot with safe graphics");
    let labels: Vec<&str> = arr(&v, &["storage", "disks", "0", "partitions"]).iter().map(|p| s(p, &["content", "label"])).collect();
    assert_eq!(labels, vec!["EFI", "PikaBoot", "PikaRoot"]);
    assert!(arr(&v, &["warnings"]).is_empty(), "{:?}", v.get("warnings"));

    assert!(b.problems.is_empty(), "{:?}", b.problems);
    let bt: String = b.stage(Stage::Boot).iter().map(Cmd::shell).collect();
    let booster: Vec<&str> = bt.lines().filter(|l| l.contains("booster build")).collect();
    assert_eq!(
        booster,
        vec![r#"chroot /mnt/bko-target sh -c 'n=0; for k in /lib/modules/*; do [ -d "$k/kernel" ] || continue; k=${k##*/}; booster build --force --kernel-version "$k" "/boot/booster.img-$k" || exit; n=$((n + 1)); done; [ "$n" -gt 0 ]'"#],
        "{bt}"
    );
    assert!(!bt.contains("update-initramfs") && !bt.contains("refind-install") && !bt.contains("grub") && !bt.contains("bootctl"), "{bt}");
    let nvram = bt.find("efibootmgr --quiet --create --disk /dev/vdb --part 1 --label 'rEFInd Boot Manager' --loader '\\EFI\\refind\\refind_x64.efi'\n").expect(&bt);
    assert!(bt.find("booster build").unwrap() < nvram, "{bt}");
    assert!(b.notes.iter().any(|n| n == "rEFInd: its EFI files, drivers, theme and refind.conf came back with the ESP, and refind_linux.conf with /boot; nothing to install"), "{:?}", b.notes);
    let mnt: String = b.stage(Stage::Mount).iter().map(Cmd::shell).collect();
    assert!(mnt.find("/mnt/bko-target/boot\n").unwrap() < mnt.find("/mnt/bko-target/boot/efi\n").unwrap(), "{mnt}");
}

/// PikaOS in "BOOTX64 mode" (or OVMF with blank variables): the firmware
/// booted the fallback path, which names no loader; the ESP's rEFInd files
/// say it's rEFInd.  The firmware's own entry is the one recreated.
#[test]
fn pikaos_fallback_boot() {
    use crate::efi::GLOBAL;
    use crate::load::{commands::Cmd, Stage};
    let t = pikaos();
    let v_ = |n: &str| format!("/sys/firmware/efi/efivars/{n}-{GLOBAL}");
    std::fs::remove_file(t.p(&v_("Boot0001"))).unwrap();
    t.file(&v_("Boot0002"), efi_load_option("UEFI Misc Device", 1, 2048, 32768, PIKA_ESP, "\\EFI\\BOOT\\BOOTX64.EFI"))
        .file(&v_("BootOrder"), [7u8, 0, 0, 0, 2, 0])
        .file(&v_("BootCurrent"), [6u8, 0, 0, 0, 2, 0]);
    let (v, b) = pikaos_plan(&t);
    let l = v.at(&["boot", "loader"]).unwrap();
    assert_eq!(s(l, &["name"]), "refind");
    let ev = l.get("evidence").unwrap().compact();
    assert!(ev.contains("that is the removable-media fallback path") && ev.contains("an ESP holds rEFInd's refind_x64.efi or refind.conf"), "{ev}");
    assert!(b.problems.is_empty(), "{:?}", b.problems);
    let bt: String = b.stage(Stage::Boot).iter().map(Cmd::shell).collect();
    assert!(!bt.contains("refind-install") && !bt.contains("refind_x64.efi"), "{bt}");
    assert!(bt.contains("efibootmgr --quiet --create --disk /dev/vdb --part 1 --label 'UEFI Misc Device' --loader '\\EFI\\BOOT\\BOOTX64.EFI'\n"), "{bt}");
    assert!(b.notes.iter().any(|n| n.starts_with("rEFInd: its EFI files")), "{:?}", b.notes);
    // Only the fallback copy (refind.conf beside bootx64.efi) is enough.
    for f in ["refind_x64.efi", "refind.conf", "drivers_x64/ext4_x64.efi", "refind-dark/refind-dark.conf"] {
        std::fs::remove_file(t.p(&format!("/boot/efi/EFI/refind/{f}"))).unwrap();
    }
    let (v, _) = pikaos_plan(&t);
    assert_eq!(s(&v, &["boot", "loader", "name"]), "refind");
}

/// fstab mounts the ESP at /efi, but this boot didn't: said, since the dump
/// then has no ESP and a backup none of its files.
#[test]
fn unmounted_esp_warns() {
    let t = gentoo_thinkpad();
    let mi = std::fs::read_to_string(t.p("/proc/self/mountinfo")).unwrap();
    std::fs::write(t.p("/proc/self/mountinfo"), mi.lines().filter(|l| !l.contains(" /efi ")).map(|l| format!("{l}\n")).collect::<String>()).unwrap();
    let v = run(&t, true, FakeRunner::default());
    let w: Vec<&str> = arr(&v, &["warnings"]).iter().filter_map(Value::as_str).collect();
    assert_eq!(w, vec!["fstab mounts a vfat /efi (UUID=1234-ABCD), but nothing is mounted there: this dump has no ESP and a backup would have none of its files; mount /efi and dump again"]);
    assert!(arr(&v, &["boot", "esps"]).is_empty());
    // A /boot/efi too; a vfat mounted elsewhere, or not vfat, isn't one.
    let fstab = std::fs::read_to_string(t.p("/etc/fstab")).unwrap();
    std::fs::write(t.p("/etc/fstab"), fstab.replace(" /efi ", " /boot/efi ")).unwrap();
    let v = run(&t, true, FakeRunner::default());
    assert!(arr(&v, &["warnings"])[0].as_str().unwrap().starts_with("fstab mounts a vfat /boot/efi (UUID=1234-ABCD)"));
    std::fs::write(t.p("/etc/fstab"), fstab.replace(" /efi ", " /mnt/stick ")).unwrap();
    assert!(arr(&run(&t, true, FakeRunner::default()), &["warnings"]).is_empty());
}

const BIGLINUX_ESP: &str = "b16b16b1-0000-4000-8000-000000000001";

/// BigLinux as its Calamares installs it (docs/design/biglinux.md): UEFI,
/// GPT with a 35 MiB ESP at /boot/efi and a btrfs whose @ and @home are /
/// and /home, mounted noatime,compress-force=zstd; GRUB as `BigLinux` from
/// NVRAM (EFI/BigLinux/grubx64.efi, the fallback EFI/boot/bootx64.efi);
/// mkinitcpio with faster-mkinitcpio's one preset; Timeshift with two
/// snapshots of @ in the top level's timeshift-btrfs/snapshots.
fn biglinux() -> Tree {
    use crate::efi::GLOBAL;
    let t = Tree::new("scn-biglinux");
    let mut img = vec![0u8; 96 * MIB];
    write_gpt(
        &mut img,
        512,
        &GptSpec {
            disk_guid: "6f1c2c3d-0000-4000-8000-00000000b16b",
            parts: vec![(ESP, BIGLINUX_ESP, 2048, 73727, 0, ""), (LINUX, "b16b16b1-0000-4000-8000-000000000002", 73728, 196574, 0, "")],
        },
    );
    put(&mut img, 2048, &fat::sample::fat32(71680, 0x0b16_0b16, ""));
    put(&mut img, 73728, &btrfs::sample::image("", 0x1 | 0x20 | 0x40 | 0x100 | 0x200));
    let root_uuid = "b0b1b2b3-b4b5-b6b7-b8b9-babbbcbdbebf";
    let opts = "rw,noatime,compress-force=zstd:3,discard=async,space_cache=v2";
    let pkg = |n: &str, ver: &str| (format!("/var/lib/pacman/local/{n}-{ver}/desc"), format!("%NAME%\n{n}\n\n%VERSION%\n{ver}\n\n%ARCH%\nx86_64\n"));
    let var = |n: &str| format!("/sys/firmware/efi/efivars/{n}-{GLOBAL}");
    t.disk("vda", "254:0", &img, 512)
        .partition("vda", "vda1", "254:1", 1, 2048, 71680, &img)
        .partition("vda", "vda2", "254:2", 2, 73728, 122847, &img)
        .symlink(&format!("/dev/disk/by-partuuid/{BIGLINUX_ESP}"), "../../vda1")
        .file("/sys/firmware/efi/fw_platform_size", "64\n")
        .file(&var("Boot0001"), efi_load_option("BigLinux", 1, 2048, 71680, BIGLINUX_ESP, "\\EFI\\BigLinux\\grubx64.efi"))
        .file(&var("BootOrder"), [7u8, 0, 0, 0, 1, 0])
        .file(&var("BootCurrent"), [6u8, 0, 0, 0, 1, 0])
        .file(&var("SecureBoot"), [6u8, 0, 0, 0, 0])
        .file(
            "/proc/self/mountinfo",
            format!("26 1 0:25 /@ / {opts} shared:1 - btrfs /dev/vda2 rw,subvolid=256,subvol=/@\n27 26 0:25 /@home /home {opts} shared:2 - btrfs /dev/vda2 rw,subvolid=257,subvol=/@home\n28 26 254:1 / /boot/efi rw,relatime shared:3 - vfat /dev/vda1 rw,fmask=0077,dmask=0077\n"),
        )
        .file("/proc/swaps", "Filename\tType\tSize\tUsed\tPriority\n/dev/zram0 partition 3984380 0 100\n")
        .file(
            "/etc/fstab",
            format!("UUID={root_uuid} / btrfs subvol=/@,defaults,noatime,compress-force=zstd,discard=async 0 0\nUUID={root_uuid} /home btrfs subvol=/@home,defaults,noatime,compress-force=zstd,discard=async 0 0\nUUID=0B16-0B16 /boot/efi vfat defaults,umask=0077 0 2\n"),
        )
        .file("/etc/os-release", "NAME=\"BigLinux based on Manjaro Linux\"\nPRETTY_NAME=\"BigLinux based on Manjaro Linux\"\nID=\"biglinux\"\nID_LIKE=arch\nBUILD_ID=rolling\n")
        .file("/etc/mkinitcpio.conf", "HOOKS=\"base systemd autodetect kms modconf plymouth block filesystems fsck\"\nCOMPRESSION=\"zstd\"\n")
        .file("/etc/mkinitcpio.d/linux618.preset", "ALL_kver=\"/boot/vmlinuz-6.18-x86_64\"\nPRESETS=('default')\ndefault_image=\"/boot/initramfs-6.18-x86_64.img\"\n")
        .file("/etc/default/grub", "GRUB_DISTRIBUTOR=\"BigLinux\"\nGRUB_DEFAULT=saved\nGRUB_DISABLE_OS_PROBER=false\n")
        .file("/etc/timeshift/timeshift.json", "{\"btrfs_mode\": \"true\", \"schedule_daily\": \"true\", \"btrfs_use_qgroup\": \"false\"}\n")
        .file("/boot/vmlinuz-6.18-x86_64", "k")
        .file("/boot/initramfs-6.18-x86_64.img", "i")
        .file("/boot/grub/x86_64-efi/normal.mod", "x")
        .file("/boot/grub/grub.cfg", format!("insmod part_gpt\ninsmod btrfs\nmenuentry 'BigLinux' {{\n\tlinux\t/@/boot/vmlinuz-6.18-x86_64 root=UUID={root_uuid} rw rootflags=subvol=@\n\tinitrd\t/@/boot/initramfs-6.18-x86_64.img\n}}\n"))
        .file("/boot/efi/EFI/BigLinux/grubx64.efi", vec![2u8; 400])
        .file("/boot/efi/EFI/boot/bootx64.efi", vec![2u8; 400])
        .dir("/usr/share/grub/themes/biglinux")
        .dir("/usr/lib/modules/6.18.49-1-MANJARO/kernel")
        .file("/proc/sys/kernel/osrelease", "6.18.49-1-MANJARO\n")
        .file("/proc/sys/kernel/hostname", "bigpc\n")
        .file("/proc/cmdline", format!("BOOT_IMAGE=/@/boot/vmlinuz-6.18-x86_64 root=UUID={root_uuid} rw rootflags=subvol=@ quiet splash\n"));
    for (n, ver) in [
        ("grub", "2:2.14-1"),
        ("install-grub", "2.14-1"),
        ("update-grub", "2:2.14-1"),
        ("grub-btrfs", "4.14-1"),
        ("timeshift", "25.12-1"),
        ("grub-btrfs-timeshift", "1.0-1"),
        ("mkinitcpio", "41-1"),
        ("faster-mkinitcpio", "1.0-1"),
        ("plymouth", "24.004.60-12"),
        ("linux618", "6.18.49-1"),
        ("btrfs-progs", "6.17-1"),
        ("dosfstools", "4.2-5"),
        ("efibootmgr", "18-3"),
        ("biglinux-keyring", "2026.10-1"),
    ] {
        let (path, desc) = pkg(n, ver);
        t.file(&path, desc);
    }
    t
}

/// What btrfs answers on BigLinux: @ and @home, and two Timeshift snapshots
/// of @ (parent @), mounted nowhere.
fn biglinux_runner() -> FakeRunner {
    let u = |n: u32| format!("6c8a1b2c-0000-4000-8000-000000000{n}");
    FakeRunner::default()
        .with(
            "btrfs subvolume list -a -p -u -q /",
            &format!(
                "ID 256 gen 900 parent 5 top level 5 parent_uuid - uuid {} path <FS_TREE>/@\nID 257 gen 880 parent 5 top level 5 parent_uuid - uuid {} path <FS_TREE>/@home\nID 258 gen 610 parent 5 top level 5 parent_uuid {} uuid {} path <FS_TREE>/timeshift-btrfs/snapshots/2026-11-14_10-00-01/@\nID 259 gen 870 parent 5 top level 5 parent_uuid {} uuid {} path <FS_TREE>/timeshift-btrfs/snapshots/2026-11-15_10-00-01/@\n",
                u(256),
                u(257),
                u(256),
                u(258),
                u(256),
                u(259)
            ),
        )
        .with("btrfs subvolume get-default /", "ID 5 (FS_TREE)\n")
        .with("btrfs subvolume list -a -r /", "")
        .with("btrfs property get -t s /", "ro=false\n")
        .with("btrfs property get -t i /", "")
        .with("btrfs property get -t s /home", "ro=false\n")
        .with("btrfs property get -t i /home", "")
}

/// BigLinux read back and planned: supported by its ID, its pacman
/// packages, GRUB as `BigLinux` (the source's spelling), mkinitcpio, the
/// Timeshift snapshots listed in the dump but left out by load with a note,
/// and /boot and /usr/share/grub defragmented uncompressed at the end.
#[test]
fn biglinux_guest() {
    use crate::load::{commands, layout::Layout, target::Mapping, Parsed, Stage};
    let v = run(&biglinux(), true, biglinux_runner());
    assert_eq!(s(&v, &["os", "id"]), "biglinux");
    assert_eq!(s(&v, &["firmware", "mode"]), "uefi");
    assert_eq!(s(&v, &["boot", "loader", "name"]), "grub");
    assert_eq!(s(&v, &["boot", "initramfs", "generator"]), "mkinitcpio");
    for (n, ver) in [("grub-btrfs", "4.14-1"), ("grub-btrfs-timeshift", "1.0-1"), ("faster-mkinitcpio", "1.0-1")] {
        assert_eq!(s(&v, &["boot", "packages", n]), ver);
    }
    // Names other guests have too stay out, so their fingerprints don't change.
    for n in ["biglinux-keyring", "timeshift", "plymouth", "install-grub", "update-grub"] {
        assert!(v.at(&["boot", "packages", n]).is_none(), "{n}");
    }
    assert_eq!(s(&v, &["boot", "esps", "0", "mounted_at"]), "/boot/efi");
    let subs: Vec<&str> = arr(&v, &["storage", "btrfs", "0", "subvolumes"]).iter().map(|x| s(x, &["path"])).collect();
    assert_eq!(subs, vec!["<FS_TREE>/@", "<FS_TREE>/@home", "<FS_TREE>/timeshift-btrfs/snapshots/2026-11-14_10-00-01/@", "<FS_TREE>/timeshift-btrfs/snapshots/2026-11-15_10-00-01/@"]);

    let l = Layout::from_json(v).unwrap();
    let Ok(Parsed::Run(o)) = crate::load::parse_args(&["l.json".to_string(), "--repo".into(), "/srv/repo".into(), "--password-file".into(), "/run/pw".into()]) else { unreachable!() };
    let b = commands::build(&l, &o, &Mapping { pairs: vec![("vda".into(), "vdb".into())], ..Mapping::default() });
    assert!(b.problems.is_empty(), "{:?}", b.problems);
    let all: String = [Stage::Partition, Stage::Assemble, Stage::Format, Stage::Mount, Stage::Restore, Stage::Boot, Stage::Close].iter().flat_map(|st| b.stage(*st)).map(commands::Cmd::shell).collect();
    let f: String = b.stage(Stage::Format).iter().map(commands::Cmd::shell).collect();
    let top = format!("/run/bko-holt/btrfs/{}", "b0b1b2b3-b4b5-b6b7-b8b9-babbbcbdbebf");
    assert!(f.contains(&format!("btrfs subvolume create {top}/@\n")) && f.contains(&format!("btrfs subvolume create {top}/@home\n")), "{f}");
    assert!(!all.contains("timeshift-btrfs"), "{all}");
    assert!(!all.lines().any(|l| l.contains("subvolume snapshot")), "{all}");
    assert!(b.notes.iter().any(|n| n == "btrfs b0b1b2b3-b4b5-b6b7-b8b9-babbbcbdbebf: 2 Timeshift snapshots (2026-11-14_10-00-01, 2026-11-15_10-00-01) are not in the backup; not made"), "{:?}", b.notes);
    let m: String = b.stage(Stage::Mount).iter().map(commands::Cmd::shell).collect();
    assert!(m.contains("compress-force=zstd") && m.contains("/mnt/bko-target/boot/efi\n"), "{m}");
    let bt: String = b.stage(Stage::Boot).iter().map(commands::Cmd::shell).collect();
    let mk = bt.find("chroot /mnt/bko-target mkinitcpio -P\n").expect(&bt);
    let install = bt.find("chroot /mnt/bko-target grub-install --target=x86_64-efi --efi-directory=/boot/efi --bootloader-id=BigLinux --no-nvram\n").expect(&bt);
    let cfg = bt.find("chroot /mnt/bko-target env LANG=C grub-mkconfig -o /boot/grub/grub.cfg\n").expect(&bt);
    let d1 = bt.find("btrfs filesystem defragment --nocomp -r /mnt/bko-target/boot\n").expect(&bt);
    let d2 = bt.find("btrfs filesystem defragment --nocomp -r /mnt/bko-target/usr/share/grub\n").expect(&bt);
    let nvram = bt.find("--label BigLinux --loader '\\EFI\\BigLinux\\grubx64.efi'\n").expect(&bt);
    assert!(mk < install && install < cfg && cfg < d1 && d1 < d2 && d2 < nvram, "{bt}");
}

const ANDUINOS_KV: &str = "7.0.0-38-generic";
const ANDUINOS_ESP: &str = "a2d00105-0000-4000-8000-000000000001";
/// The btrfs (btrfs::sample::image's UUID).
const ANDUINOS_ROOT: &str = "b0b1b2b3-b4b5-b6b7-b8b9-babbbcbdbebf";
/// The six subvolumes the installer makes, and where fstab mounts them.
const ANDUINOS_SUBVOLS: [(&str, &str); 6] = [("@root", "/"), ("@home", "/home"), ("@log", "/var/log"), ("@snapshots", "/.snapshots"), ("@containers", "/var/lib/containers"), ("@libvirt", "/var/lib/libvirt/images")];
/// The Disk Snapshots Manager's two read-only "New OS" baselines, in its own
/// tree in @snapshots (anduinos2's layout): System's of @root, Home's of
/// @home, each under a directory named by its record's id.
const ANDUINOS_BASELINES: [(&str, &str, u32); 2] = [
    ("@snapshots/anduinos-btrfs-snapshots-manager/deployments/a2d0d3b1-0000-4000-8000-000000000001/root", "@root", 256),
    ("@snapshots/anduinos-btrfs-snapshots-manager/personal/snapshots/a2d0d3b1-0000-4000-8000-000000000002/home", "@home", 257),
];

/// AnduinOS 2.0.5 as its installer lays it out for "erase disk"
/// (docs/design/anduinos.md, "What A1 found"), in small (the ESP is 1 GiB
/// there): GPT with a 2 MiB BIOS boot partition holding GRUB's i386-pc core
/// (boot.img in the protective MBR: the installer runs grub-install
/// --target=i386-pc too), a vfat ESP at /boot/efi, a swap partition and one
/// btrfs whose six subvolumes are mounted defaults,subvol=@X,compress=zstd:3,
/// noatime, with the Disk Snapshots Manager's read-only baselines of @root
/// and @home in @snapshots; dracut's /boot/initrd.img-K (Debian's dracut.conf.d
/// names it so); AnduinOS's GRUB, signed with the machine's MOK, behind
/// shim in EFI/AnduinOS and at the fallback path (shim, GRUB and
/// MokManager); booted from NVRAM's shim entry with Secure Boot on.
fn anduinos() -> Tree {
    use crate::efi::GLOBAL;
    const SWAP: &str = "0657fd6d-a4ab-43c4-84e5-0933c84b4f4f";
    let t = Tree::new("scn-anduinos");
    let k = ANDUINOS_KV;
    let mut img = vec![0u8; 100 * MIB];
    write_gpt(
        &mut img,
        512,
        &GptSpec {
            disk_guid: "a2d00105-0000-4000-8000-00000000d15c",
            parts: vec![
                (BIOS_BOOT, "a2d00105-0000-4000-8000-000000000004", 2048, 6143, 0, "bios-boot"),
                (ESP, ANDUINOS_ESP, 6144, 77823, 0, "efi-system"),
                (SWAP, "a2d00105-0000-4000-8000-000000000002", 77824, 86015, 0, "swap"),
                (LINUX, "a2d00105-0000-4000-8000-000000000003", 86016, 204766, 0, "root"),
            ],
        },
    );
    // grub-install --target=i386-pc: boot.img's code in the protective MBR,
    // core.img in the BIOS boot partition.
    img[..440].copy_from_slice(&grub::grub2_boot_img(2048)[..440]);
    put(&mut img, 2048, &grub::grub2_diskboot_img());
    put(&mut img, 6144, &fat::sample::fat32(71680, 0xa2d0_0105, ""));
    put(&mut img, 77824, &misc::sample::swap(1024, ""));
    put(&mut img, 86016, &btrfs::sample::image("", 0x1 | 0x20 | 0x40 | 0x100 | 0x200));
    let swap_uuid = "c0c1c2c3-c4c5-c6c7-c8c9-cacbcccdcecf";
    let var = |n: &str| format!("/sys/firmware/efi/efivars/{n}-{GLOBAL}");
    let mut mountinfo = String::new();
    let mut fstab = String::new();
    for (i, (sv, at)) in ANDUINOS_SUBVOLS.iter().enumerate() {
        let parent = if *at == "/" { 1 } else { 26 };
        mountinfo.push_str(&format!("{} {parent} 0:25 /{sv} {at} rw,noatime shared:{} - btrfs /dev/vda4 rw,compress=zstd:3,space_cache=v2,subvolid={},subvol=/{sv}\n", 26 + i, i + 1, 256 + i));
        fstab.push_str(&format!("UUID={ANDUINOS_ROOT} {at} btrfs defaults,subvol={sv},compress=zstd:3,noatime 0 {}\n", if *at == "/" { 1 } else { 2 }));
    }
    mountinfo.push_str("40 26 254:2 / /boot/efi rw,relatime shared:9 - vfat /dev/vda2 rw,fmask=0077,dmask=0077\n");
    fstab.push_str(&format!("UUID=A2D0-0105 /boot/efi vfat umask=0077 0 1\nUUID={swap_uuid} none swap sw 0 0\n"));
    let (shim, grub, mm) = (vec![1u8; 300], vec![2u8; 400], vec![3u8; 200]);
    let stub = format!("search.fs_uuid {ANDUINOS_ROOT} root \nset prefix=($root)'/@root/boot/grub'\nconfigfile $prefix/grub.cfg\n");
    t.disk("vda", "254:0", &img, 512)
        .partition("vda", "vda1", "254:1", 1, 2048, 4096, &img)
        .partition("vda", "vda2", "254:2", 2, 6144, 71680, &img)
        .partition("vda", "vda3", "254:3", 3, 77824, 8192, &img)
        .partition("vda", "vda4", "254:4", 4, 86016, 118751, &img)
        .file("/sys/block/vda/device/model", "")
        .symlink(&format!("/dev/disk/by-partuuid/{ANDUINOS_ESP}"), "../../vda2")
        .file("/sys/firmware/efi/fw_platform_size", "64\n")
        .file(&var("Boot0001"), efi_load_option("AnduinOS", 2, 6144, 71680, ANDUINOS_ESP, "\\EFI\\AnduinOS\\shimx64.efi"))
        .file(&var("BootOrder"), [7u8, 0, 0, 0, 1, 0])
        .file(&var("BootCurrent"), [6u8, 0, 0, 0, 1, 0])
        .file(&var("SecureBoot"), [6u8, 0, 0, 0, 1])
        .file("/proc/self/mountinfo", mountinfo)
        .file("/proc/swaps", "Filename\t\t\t\tType\t\tSize\t\tUsed\t\tPriority\n/dev/vda3                               partition\t4096\t\t0\t\t-2\n/dev/zram0                              partition\t2048\t\t0\t\t100\n")
        .file("/etc/fstab", fstab)
        .file("/usr/lib/os-release", "PRETTY_NAME=\"AnduinOS 2.0.5\"\nNAME=\"AnduinOS\"\nVERSION_ID=\"2.0.5\"\nVERSION=\"2.0.5 (Resolute Ringtail)\"\nVERSION_CODENAME=resolute\nID=anduinos\nID_LIKE=debian\nUBUNTU_CODENAME=resolute\n")
        .symlink("/etc/os-release", "../usr/lib/os-release")
        .file("/etc/debian_version", "forky/sid\n")
        .symlink("/lib", "usr/lib")
        .file("/etc/dracut.conf.d/01-debian.conf", "initrdname=\"initrd.img-${kernel}\"\n")
        .file(&format!("/boot/vmlinuz-{k}"), "k")
        .file(&format!("/boot/initrd.img-{k}"), "dracut")
        .file(&format!("/boot/config-{k}"), "")
        .file(&format!("/boot/System.map-{k}"), "")
        .symlink("/boot/vmlinuz", &format!("vmlinuz-{k}"))
        .symlink("/boot/initrd.img", &format!("initrd.img-{k}"))
        .file("/boot/grub/x86_64-efi/normal.mod", "x")
        .file("/boot/grub/i386-pc/normal.mod", "x")
        .file("/boot/grub/i386-pc/core.img", "x")
        .file(
            "/boot/grub/grub.cfg",
            format!("insmod part_gpt\ninsmod btrfs\nsearch --no-floppy --fs-uuid --set=root {ANDUINOS_ROOT}\nmenuentry 'AnduinOS' {{\n\tlinux\t/@root/boot/vmlinuz-{k} root=UUID={ANDUINOS_ROOT} ro rootflags=subvol=@root quiet splash\n\tinitrd\t/@root/boot/initrd.img-{k}\n}}\n"),
        )
        .file("/etc/default/grub", "GRUB_DEFAULT=0\nGRUB_DISTRIBUTOR=`( . /etc/os-release; echo ${NAME:-AnduinOS} ) 2>/dev/null || echo AnduinOS`\nGRUB_CMDLINE_LINUX_DEFAULT=\"quiet splash\"\n")
        .dir(&format!("/usr/lib/modules/{k}/kernel"))
        .file("/boot/efi/EFI/AnduinOS/shimx64.efi", &shim)
        .file("/boot/efi/EFI/AnduinOS/grubx64.efi", &grub)
        .file("/boot/efi/EFI/AnduinOS/mmx64.efi", &mm)
        .file("/boot/efi/EFI/AnduinOS/grub.cfg", &stub)
        .file("/boot/efi/EFI/BOOT/BOOTX64.EFI", &shim)
        .file("/boot/efi/EFI/BOOT/grubx64.efi", &grub)
        .file("/boot/efi/EFI/BOOT/mmx64.efi", &mm)
        .file("/var/lib/shim-signed/mok/MOK.der", b"0\x82\x03\x1fAnduinOS Secure Boot MOK (test)")
        .file("/var/lib/shim-signed/mok/MOK.priv", "not for the dump")
        .dir("/var/lib/anduinos-btrfs-snapshots-manager")
        .file("/proc/sys/kernel/osrelease", format!("{k}\n"))
        .file("/proc/sys/kernel/hostname", "anduinos-pc\n")
        .file("/proc/cmdline", format!("BOOT_IMAGE=/@root/boot/vmlinuz-{k} root=UUID={ANDUINOS_ROOT} ro rootflags=subvol=@root quiet splash\n"));
    let pkgs = [
        ("anduinos-grub", "2.0.5+repack1-2"),
        ("anduinos-shim-signed", "1.61+15.8-0ubuntu1+anduinos1"),
        ("anduinos-secureboot-toolkit", "2.0.5-4"),
        ("anduinos-btrfs-snapshots-manager", "2.0.5-3"),
        ("mokutil", "0.7.2-1"),
        ("sbsigntool", "0.9.4-3.2"),
        ("dracut", "110-11ubuntu0.1"),
        ("linux-generic-hwe-26.04", "7.0.0-38.38"),
        ("linux-image-7.0.0-38-generic", "7.0.0-38.38"),
        ("efibootmgr", "18-2"),
        ("btrfs-progs", "6.17.1-1"),
        ("dosfstools", "4.2-1.2"),
        ("e2fsprogs", "1.47.3-1"),
    ];
    let status: String = pkgs.iter().map(|(n, v)| format!("Package: {n}\nStatus: install ok installed\nArchitecture: amd64\nVersion: {v}\n\n")).collect();
    t.file("/var/lib/dpkg/status", status);
    t
}

/// What btrfs answers on AnduinOS: the six subvolumes, and the snapshot
/// manager's two read-only "New OS" baselines (of @root and @home) in its
/// tree in @snapshots.
fn anduinos_runner() -> FakeRunner {
    let u = |n: u32| format!("a2d0b7f5-0000-4000-8000-000000000{n}");
    let mut list = String::new();
    for (i, (sv, _)) in ANDUINOS_SUBVOLS.iter().enumerate() {
        let id = 256 + i as u32;
        list.push_str(&format!("ID {id} gen 900 parent 5 top level 5 parent_uuid - uuid {} path <FS_TREE>/{sv}\n", u(id)));
    }
    let mut ro = String::new();
    for (i, (path, _, parent)) in ANDUINOS_BASELINES.iter().enumerate() {
        let id = 262 + i as u32;
        list.push_str(&format!("ID {id} gen 640 parent 259 top level 259 parent_uuid {} uuid {} path <FS_TREE>/{path}\n", u(*parent), u(id)));
        ro.push_str(&format!("ID {id} gen 640 top level 259 path <FS_TREE>/{path}\n"));
    }
    let mut r = FakeRunner::default()
        .with("btrfs subvolume list -a -p -u -q /", &list)
        .with("btrfs subvolume get-default /", "ID 5 (FS_TREE)\n")
        .with("btrfs subvolume list -a -r /", &ro);
    for (path, _, _) in ANDUINOS_BASELINES {
        let at = format!("/.snapshots{}", &path["@snapshots".len()..]);
        r = r.with(&format!("btrfs property get -t s {at}"), "ro=true\n").with(&format!("btrfs property get -t i {at}"), "");
    }
    for (_, at) in ANDUINOS_SUBVOLS {
        r = r.with(&format!("btrfs property get -t s {at}"), "ro=false\n").with(&format!("btrfs property get -t i {at}"), "");
    }
    r
}

/// AnduinOS's plan from a tree, the source disk vda going to vdb.
fn anduinos_plan(t: &Tree) -> (Value, crate::load::commands::Built) {
    use crate::load::{commands, layout::Layout, target::Mapping, Parsed};
    let v = run(t, true, anduinos_runner());
    let l = Layout::from_json(v.clone()).unwrap();
    let Ok(Parsed::Run(o)) = crate::load::parse_args(&["l.json".to_string()]) else { unreachable!() };
    (v, commands::build(&l, &o, &Mapping { pairs: vec![("vda".into(), "vdb".into())], ..Mapping::default() }))
}

/// AnduinOS read back and planned (docs/design/anduinos.md): supported by
/// its ID; GRUB through shim from BootCurrent, the ESP directory's case
/// kept; boot.loader.mok with the certificate's SHA-256; dracut as the
/// generator; the six subvolumes and the read-only snapshot.  load runs
/// dracut's loop, no grub-install (the MOK-signed GRUB is the restored
/// ESP's), update-grub with os-prober off, makes shim's boot entry, and
/// notes the MOK and the snapshot manager's records.
#[test]
fn anduinos_guest() {
    use crate::load::{commands::Cmd, Stage};
    let k = ANDUINOS_KV;
    let (v, b) = anduinos_plan(&anduinos());
    assert_eq!(s(&v, &["os", "id"]), "anduinos");
    assert_eq!(s(&v, &["os", "version_id"]), "2.0.5");
    assert_eq!(s(&v, &["os", "pretty_name"]), "AnduinOS 2.0.5");
    assert_eq!(s(&v, &["firmware", "mode"]), "uefi");
    assert_eq!(v.at(&["firmware", "secure_boot"]), Some(&Value::Bool(true)));
    for (p, ver) in [("anduinos-grub", "2.0.5+repack1-2"), ("anduinos-shim-signed", "1.61+15.8-0ubuntu1+anduinos1"), ("anduinos-secureboot-toolkit", "2.0.5-4"), ("anduinos-btrfs-snapshots-manager", "2.0.5-3"), ("mokutil", "0.7.2-1"), ("dracut", "110-11ubuntu0.1")] {
        assert_eq!(s(&v, &["boot", "packages", p]), ver, "{p}");
    }
    let l = v.at(&["boot", "loader"]).unwrap();
    assert_eq!(s(l, &["name"]), "grub");
    assert_eq!(l.get("via_shim"), Some(&Value::Bool(true)));
    assert_eq!(s(l, &["package"]), "anduinos-grub");
    assert_eq!(s(l, &["shim_package"]), "anduinos-shim-signed");
    assert_eq!(s(l, &["mok", "cert"]), "/var/lib/shim-signed/mok/MOK.der");
    let fp = crate::bytes::sha256_hex(b"0\x82\x03\x1fAnduinOS Secure Boot MOK (test)");
    assert_eq!(s(l, &["mok", "sha256"]), fp);
    assert!(!v.compact().contains("not for the dump"));
    assert_eq!(s(&v, &["boot", "initramfs", "generator"]), "dracut");
    let kernels = arr(&v, &["boot", "kernels", "installed"]);
    assert_eq!(kernels.len(), 1, "{}", v.at(&["boot", "kernels"]).unwrap().compact());
    assert_eq!(s(&kernels[0], &["version"]), k);
    assert_eq!(u(&kernels[0], &["initrd_bytes"]), 6);
    let esp = v.at(&["boot", "esps", "0"]).unwrap();
    assert_eq!(s(esp, &["mounted_at"]), "/boot/efi");
    let files: Vec<(&str, Option<&str>)> = arr(esp, &["contents", "files"]).iter().map(|f| (s(f, &["path"]), f.get("same_as").and_then(Value::as_str))).collect();
    assert!(files.contains(&("EFI/BOOT/BOOTX64.EFI", Some("EFI/AnduinOS/shimx64.efi"))), "{files:?}");
    assert_eq!(files.len(), 7, "{files:?}");
    let subs: Vec<(String, bool)> = arr(&v, &["storage", "btrfs", "0", "subvolumes"]).iter().map(|x| (s(x, &["path"]).to_string(), x.get("readonly") == Some(&Value::Bool(true)))).collect();
    let mut want: Vec<(String, bool)> = ANDUINOS_SUBVOLS.iter().map(|(sv, _)| (format!("<FS_TREE>/{sv}"), false)).collect();
    want.extend(ANDUINOS_BASELINES.iter().map(|(p, _, _)| (format!("<FS_TREE>/{p}"), true)));
    assert_eq!(subs, want);
    // The BIOS boot partition and GRUB's i386-pc core in it: the installer's
    // BIOS install beside the UEFI one.
    let disk = v.at(&["storage", "disks", "0"]).unwrap();
    assert_eq!(s(disk, &["partitions", "0", "type_name"]), "BIOS boot");
    assert_eq!(s(disk, &["boot_code", "kind"]), "grub2");
    assert_eq!(disk.at(&["boot_code", "grub_core_present"]), Some(&Value::Bool(true)));
    assert_eq!(s(disk, &["boot_code", "grub_core_in"]), "partition");
    assert!(arr(&v, &["warnings"]).is_empty(), "{:?}", v.get("warnings"));

    assert!(b.problems.is_empty(), "{:?}", b.problems);
    let f: String = b.stage(Stage::Format).iter().map(Cmd::shell).collect();
    let top = format!("/run/bko-holt/btrfs/{ANDUINOS_ROOT}");
    for (sv, _) in ANDUINOS_SUBVOLS {
        assert!(f.contains(&format!("btrfs subvolume create {top}/{sv}\n")), "{sv}: {f}");
    }
    let p: String = b.stage(Stage::Partition).iter().map(Cmd::shell).collect();
    assert!(p.contains("/dev/vdb1 : start=2048, size=4096, type=21686148-6449-6E6F-744E-656564454649"), "{p}");
    let m: String = b.stage(Stage::Mount).iter().map(Cmd::shell).collect();
    assert!(m.contains("subvol=@libvirt,compress=zstd:3,noatime") && m.contains("/mnt/bko-target/var/lib/libvirt/images\n") && m.contains("/mnt/bko-target/boot/efi\n"), "{m}");
    // Both baselines come back as snapshots of their parents, made exact
    // from the backup (extents shared), then read-only again.
    let r: String = {
        use crate::load::{commands, layout::Layout, target::Mapping, Parsed};
        let Ok(Parsed::Run(o)) = crate::load::parse_args(&["l.json".to_string(), "--repo".into(), "/srv/repo".into(), "--password-file".into(), "/run/pw".into()]) else { unreachable!() };
        let rb = commands::build(&Layout::from_json(v.clone()).unwrap(), &o, &Mapping { pairs: vec![("vda".into(), "vdb".into())], ..Mapping::default() });
        assert!(rb.problems.is_empty(), "{:?}", rb.problems);
        rb.stage(Stage::Restore).iter().map(Cmd::shell).collect()
    };
    for (path, parent, _) in ANDUINOS_BASELINES {
        let at = &path["@snapshots".len()..];
        let from = if parent == "@root" { "/" } else { "/home" };
        assert!(r.contains(&format!("btrfs subvolume snapshot {top}/{parent} \"$d\"")), "{parent}: {r}");
        assert!(r.contains(&format!("--exact --clone-from {from}={top}/{parent} latest:/.snapshots{at} {top}/{path}")), "{path}: {r}");
        assert!(r.contains(&format!("btrfs property set -ts {top}/{path} ro true\n")), "{path}: {r}");
    }
    let bt: String = b.stage(Stage::Boot).iter().map(Cmd::shell).collect();
    let dracut = bt.find("chroot /mnt/bko-target sh -c 'n=0; for k in /lib/modules/*; do [ -d \"$k/kernel\" ] || continue; dracut --force --kver \"${k##*/}\" || exit; n=$((n + 1)); done; [ \"$n\" -gt 0 ]'\n").expect(&bt);
    let off = bt.find("[ ! -e /mnt/bko-target/usr/bin/os-prober ] || mount --bind /mnt/bko-target/usr/bin/true /mnt/bko-target/usr/bin/os-prober").expect(&bt);
    let cfg = bt.find("chroot /mnt/bko-target env LANG=C update-grub\n").expect(&bt);
    let nvram = bt.find("efibootmgr --quiet --create --disk /dev/vdb --part 2 --label AnduinOS --loader '\\EFI\\AnduinOS\\shimx64.efi'\n").expect(&bt);
    // The BIOS install made again (only the MBR, the BIOS boot partition
    // and /boot/grub/i386-pc), before update-grub; no EFI grub-install.
    let bios = bt.find("chroot /mnt/bko-target grub-install --target=i386-pc /dev/vdb\n").expect(&bt);
    assert!(dracut < off && off < cfg && cfg < nvram && dracut < bios && bios < cfg, "{bt}");
    assert_eq!(bt.matches("grub-install").count(), 1, "{bt}");
    assert!(!bt.contains("--target=x86_64-efi") && !bt.contains("update-initramfs") && !bt.contains("grub-mkconfig") && !bt.contains("mokutil") && !bt.contains("anduinos-grub-update"), "{bt}");
    assert!(
        b.notes.iter().any(|n| n.starts_with(&format!("AnduinOS: shim, MokManager and AnduinOS's GRUB, signed with the MOK /var/lib/shim-signed/mok/MOK.der (SHA-256 {fp}), are the restored ESP's; grub-install --target=x86_64-efi isn't run"))
            && n.ends_with("enroll it again (`mokutil --import /var/lib/shim-signed/mok/MOK.der`, then confirm it at MokManager on the next boot) or turn Secure Boot off")),
        "{:?}",
        b.notes
    );
    assert!(b.notes.iter().any(|n| n.starts_with("AnduinOS: the Disk Snapshots Manager's records name each snapshot by its Btrfs subvolume UUID")), "{:?}", b.notes);
}

/// AnduinOS booted at the fallback path (no NVRAM entry, OVMF with blank
/// variables), Secure Boot off and no MOK variables: EFI/BOOT/BOOTX64.EFI
/// is a copy of shim, so it's through shim still; and without the MOK in
/// the dump, shim and anduinos-grub alone keep the ESP as restored.
#[test]
fn anduinos_fallback_boot() {
    use crate::efi::GLOBAL;
    use crate::load::{commands::Cmd, Stage};
    let t = anduinos();
    let var = |n: &str| format!("/sys/firmware/efi/efivars/{n}-{GLOBAL}");
    std::fs::remove_file(t.p(&var("Boot0001"))).unwrap();
    t.file(&var("Boot0002"), efi_load_option("UEFI Misc Device", 2, 6144, 71680, ANDUINOS_ESP, "\\EFI\\BOOT\\BOOTX64.EFI"))
        .file(&var("BootOrder"), [7u8, 0, 0, 0, 2, 0])
        .file(&var("BootCurrent"), [6u8, 0, 0, 0, 2, 0])
        .file(&var("SecureBoot"), [6u8, 0, 0, 0, 0]);
    let (v, b) = anduinos_plan(&t);
    let l = v.at(&["boot", "loader"]).unwrap();
    assert_eq!(s(l, &["name"]), "grub");
    assert_eq!(l.get("via_shim"), Some(&Value::Bool(true)));
    assert!(l.get("evidence").unwrap().compact().contains("EFI/BOOT/BOOTX64.EFI is a copy of EFI/AnduinOS/shimx64.efi"), "{}", l.compact());
    assert!(b.problems.is_empty(), "{:?}", b.problems);
    let bt: String = b.stage(Stage::Boot).iter().map(Cmd::shell).collect();
    assert!(!bt.contains("--target=x86_64-efi") && bt.contains("grub-install --target=i386-pc /dev/vdb\n") && bt.contains("update-grub\n"), "{bt}");
    assert!(bt.contains("efibootmgr --quiet --create --disk /dev/vdb --part 2 --label 'UEFI Misc Device' --loader '\\EFI\\BOOT\\BOOTX64.EFI'\n"), "{bt}");
    std::fs::remove_file(t.p("/var/lib/shim-signed/mok/MOK.der")).unwrap();
    let (v, b) = anduinos_plan(&t);
    assert!(v.at(&["boot", "loader", "mok"]).is_none());
    let bt: String = b.stage(Stage::Boot).iter().map(Cmd::shell).collect();
    assert!(!bt.contains("--target=x86_64-efi") && bt.contains("grub-install --target=i386-pc /dev/vdb\n"), "{bt}");
    assert!(b.notes.iter().any(|n| n.contains("signed with the MOK /var/lib/shim-signed/mok/MOK.der (its fingerprint not in the dump)")), "{:?}", b.notes);
}

/// Kali's kernel (linux-image-amd64 7.1.5-1kali1).
const KALI_KV: &str = "7.1.5+kali-amd64";
/// The ESP's PARTUUID.
const KALI_ESP: &str = "ca11ca11-0000-4000-8000-000000000001";
/// Kali 2026.3's /usr/lib/os-release (base-files 2026.3.1).
const KALI_OS_RELEASE: &str = "PRETTY_NAME=\"Kali GNU/Linux Rolling\"\nNAME=\"Kali GNU/Linux\"\nVERSION_ID=\"2026.3\"\nVERSION=\"2026.3\"\nVERSION_CODENAME=kali-rolling\nID=kali\nID_LIKE=debian\nHOME_URL=\"https://www.kali.org/\"\nSUPPORT_URL=\"https://forums.kali.org/\"\nBUG_REPORT_URL=\"https://bugs.kali.org/\"\nANSI_COLOR=\"1;31\"\n";

/// Kali 2026.3 as d-i's guided "use entire disk" lays it out on UEFI
/// (docs/design/kali.md, "What K1 found"), in small: GPT with a vfat ESP at
/// /boot/efi, an ext4 root and a swap partition; initramfs-tools'
/// /boot/initrd.img-K; GRUB unsigned (grub-efi-amd64, no shim-signed, Secure
/// Boot off) in EFI/kali and, from force-efi-extra-removable, its copy at
/// the fallback path; booted from NVRAM's "kali" entry, as a real PC is.
fn kali() -> Tree {
    use crate::efi::GLOBAL;
    const SWAP: &str = "0657fd6d-a4ab-43c4-84e5-0933c84b4f4f";
    let t = Tree::new("scn-kali");
    let k = KALI_KV;
    let mut img = vec![0u8; 100 * MIB];
    write_gpt(
        &mut img,
        512,
        &GptSpec {
            disk_guid: "ca11ca11-0000-4000-8000-00000000d15c",
            parts: vec![
                (ESP, KALI_ESP, 2048, 73727, 0, ""),
                (LINUX, "ca11ca11-0000-4000-8000-000000000002", 73728, 196607, 0, ""),
                (SWAP, "ca11ca11-0000-4000-8000-000000000003", 196608, 204766, 0, ""),
            ],
        },
    );
    put(&mut img, 2048, &fat::sample::fat32(71680, 0xca11_0001, ""));
    put(&mut img, 73728, &ext::sample::image(0x4 | 0x1000, 0x2 | 0x40 | 0x80 | 0x200, 0x1 | 0x400, ""));
    put(&mut img, 196608, &misc::sample::swap(1024, ""));
    let (root, swap) = ("10111213-1415-1617-1819-1a1b1c1d1e1f", "c0c1c2c3-c4c5-c6c7-c8c9-cacbcccdcecf");
    let var = |n: &str| format!("/sys/firmware/efi/efivars/{n}-{GLOBAL}");
    let grub = vec![2u8; 400];
    t.disk("vda", "254:0", &img, 512)
        .partition("vda", "vda1", "254:1", 1, 2048, 71680, &img)
        .partition("vda", "vda2", "254:2", 2, 73728, 122880, &img)
        .partition("vda", "vda3", "254:3", 3, 196608, 8159, &img)
        .file("/sys/block/vda/device/model", "")
        .symlink(&format!("/dev/disk/by-partuuid/{KALI_ESP}"), "../../vda1")
        .file("/sys/firmware/efi/fw_platform_size", "64\n")
        .file(&var("Boot0001"), efi_load_option("kali", 1, 2048, 71680, KALI_ESP, "\\EFI\\kali\\grubx64.efi"))
        .file(&var("BootOrder"), [7u8, 0, 0, 0, 1, 0])
        .file(&var("BootCurrent"), [6u8, 0, 0, 0, 1, 0])
        .file(&var("SecureBoot"), [6u8, 0, 0, 0, 0])
        .file("/proc/self/mountinfo", "26 1 254:2 / / rw,relatime shared:1 - ext4 /dev/vda2 rw,errors=remount-ro\n27 26 254:1 / /boot/efi rw,relatime shared:2 - vfat /dev/vda1 rw,fmask=0077,dmask=0077\n")
        .file("/proc/swaps", "Filename\t\t\t\tType\t\tSize\t\tUsed\t\tPriority\n/dev/vda3                               partition\t4076\t\t0\t\t-2\n")
        .file("/etc/fstab", format!("UUID={root} / ext4 errors=remount-ro 0 1\nUUID=CA11-0001 /boot/efi vfat umask=0077 0 1\nUUID={swap} none swap sw 0 0\n"))
        .file("/usr/lib/os-release", KALI_OS_RELEASE)
        .symlink("/etc/os-release", "../usr/lib/os-release")
        .file("/etc/debian_version", "kali-rolling\n")
        .symlink("/lib", "usr/lib")
        .file(&format!("/boot/vmlinuz-{k}"), "k")
        .file(&format!("/boot/initrd.img-{k}"), "initramfs-tools")
        .file(&format!("/boot/config-{k}"), "")
        .file(&format!("/boot/System.map-{k}"), "")
        .file("/boot/grub/x86_64-efi/normal.mod", "x")
        .file(
            "/boot/grub/grub.cfg",
            format!("insmod part_gpt\ninsmod ext2\nsearch --no-floppy --fs-uuid --set=root {root}\nmenuentry 'Kali GNU/Linux' {{\n\tlinux\t/boot/vmlinuz-{k} root=UUID={root} ro quiet\n\tinitrd\t/boot/initrd.img-{k}\n}}\n"),
        )
        .file("/etc/default/grub", "GRUB_DEFAULT=0\nGRUB_TIMEOUT=5\nGRUB_DISTRIBUTOR=`( . /etc/os-release && echo ${NAME} )`\nGRUB_CMDLINE_LINUX_DEFAULT=\"quiet\"\n")
        .file("/etc/initramfs-tools/initramfs.conf", "MODULES=most\nCOMPRESS=zstd\n")
        .dir(&format!("/usr/lib/modules/{k}/kernel"))
        .file("/boot/efi/EFI/kali/grubx64.efi", &grub)
        .file("/boot/efi/EFI/BOOT/BOOTX64.EFI", &grub)
        .file("/proc/sys/kernel/osrelease", format!("{k}\n"))
        .file("/proc/sys/kernel/hostname", "kali\n")
        .file("/proc/cmdline", format!("BOOT_IMAGE=/boot/vmlinuz-{k} root=UUID={root} ro quiet\n"));
    let pkgs = [
        ("grub-efi-amd64", "2.14-2+kali1"),
        ("grub-efi-amd64-bin", "2.14-2+kali1"),
        ("grub-efi-amd64-unsigned", "2.14-2+kali1"),
        ("grub-common", "2.14-2+kali1"),
        ("grub2-common", "2.14-2+kali1"),
        ("linux-image-amd64", "7.1.5-1kali1"),
        ("linux-image-7.1.5+kali-amd64", "7.1.5-1kali1"),
        ("initramfs-tools", "0.151"),
        ("efibootmgr", "18-2"),
        ("os-prober", "1.84"),
        ("e2fsprogs", "1.47.3-1"),
        ("dosfstools", "4.2-1.2"),
    ];
    let status: String = pkgs.iter().map(|(n, v)| format!("Package: {n}\nStatus: install ok installed\nArchitecture: amd64\nVersion: {v}\n\n")).collect();
    t.file("/var/lib/dpkg/status", status);
    t
}

/// A plan from a tree, the source disk vda going to vdb.
fn kali_plan(t: &Tree) -> (Value, crate::load::commands::Built) {
    use crate::load::{commands, layout::Layout, target::Mapping, Parsed};
    let v = run(t, true, FakeRunner::default());
    let l = Layout::from_json(v.clone()).unwrap();
    let Ok(Parsed::Run(o)) = crate::load::parse_args(&["l.json".to_string()]) else { unreachable!() };
    (v, commands::build(&l, &o, &Mapping { pairs: vec![("vda".into(), "vdb".into())], ..Mapping::default() }))
}

/// Every stage's commands, in order.
fn plan_text(b: &crate::load::commands::Built) -> String {
    use crate::load::{commands::Cmd, Stage};
    Stage::ALL.iter().map(|st| format!("# {st:?}\n{}", b.stage(*st).iter().map(Cmd::shell).collect::<String>())).collect()
}

/// Kali read back and planned (docs/design/kali.md, K2): supported by its
/// ID; GRUB unsigned in EFI/kali (no shim) from BootCurrent, the fallback
/// copy beside it; initramfs-tools as the generator.  load runs
/// update-initramfs, grub-install into EFI/kali with no NVRAM write,
/// update-grub (not grub-mkconfig) with os-prober off, and makes the "kali"
/// entry again.  The plan is the one the same machine gets as Debian 13.
#[test]
fn kali_guest() {
    use crate::load::{commands::Cmd, Stage};
    let k = KALI_KV;
    let (v, b) = kali_plan(&kali());
    assert_eq!(s(&v, &["os", "id"]), "kali");
    assert_eq!(s(&v, &["os", "version_id"]), "2026.3");
    assert_eq!(s(&v, &["os", "version_codename"]), "kali-rolling");
    assert_eq!(s(&v, &["firmware", "mode"]), "uefi");
    assert_eq!(v.at(&["firmware", "secure_boot"]), Some(&Value::Bool(false)));
    let l = v.at(&["boot", "loader"]).unwrap();
    assert_eq!(s(l, &["name"]), "grub");
    assert_eq!(l.get("via_shim"), Some(&Value::Bool(false)));
    assert_eq!(s(l, &["package"]), "grub-efi-amd64");
    assert!(l.get("shim_package").is_none_or(Value::is_null), "{}", l.compact());
    for (p, ver) in [("grub-efi-amd64", "2.14-2+kali1"), ("linux-image-amd64", "7.1.5-1kali1"), ("initramfs-tools", "0.151")] {
        assert_eq!(s(&v, &["boot", "packages", p]), ver, "{p}");
    }
    assert!(v.at(&["boot", "packages", "shim-signed"]).is_none());
    assert_eq!(s(&v, &["boot", "initramfs", "generator"]), "initramfs-tools");
    let kernels = arr(&v, &["boot", "kernels", "installed"]);
    assert_eq!(kernels.len(), 1, "{}", v.at(&["boot", "kernels"]).unwrap().compact());
    assert_eq!(s(&kernels[0], &["version"]), k);
    let esp = v.at(&["boot", "esps", "0"]).unwrap();
    assert_eq!(s(esp, &["mounted_at"]), "/boot/efi");
    let files: Vec<(&str, Option<&str>)> = arr(esp, &["contents", "files"]).iter().map(|f| (s(f, &["path"]), f.get("same_as").and_then(Value::as_str))).collect();
    assert!(files.contains(&("EFI/BOOT/BOOTX64.EFI", Some("EFI/kali/grubx64.efi"))), "{files:?}");
    assert_eq!(files.len(), 2, "{files:?}");
    let disk = v.at(&["storage", "disks", "0"]).unwrap();
    assert_eq!(s(disk, &["partition_table", "type"]), "gpt");
    assert_eq!(s(disk, &["partitions", "0", "type_name"]), "EFI System");
    assert_eq!(s(disk, &["partitions", "1", "content", "type"]), "ext4");
    assert_eq!(s(disk, &["partitions", "2", "content", "type"]), "swap");
    assert!(arr(&v, &["warnings"]).is_empty(), "{:?}", v.get("warnings"));

    assert!(b.problems.is_empty(), "{:?}", b.problems);
    let f: String = b.stage(Stage::Format).iter().map(Cmd::shell).collect();
    assert!(f.contains("mke2fs ") && f.contains("mkswap ") && f.contains("mkfs.fat "), "{f}");
    let bt: String = b.stage(Stage::Boot).iter().map(Cmd::shell).collect();
    let i = bt.find("chroot /mnt/bko-target update-initramfs -u -k all\n").expect(&bt);
    let g = bt.find("chroot /mnt/bko-target grub-install --target=x86_64-efi --efi-directory=/boot/efi --bootloader-id=kali --no-nvram\n").expect(&bt);
    let off = bt.find("[ ! -e /mnt/bko-target/usr/bin/os-prober ] || mount --bind /mnt/bko-target/usr/bin/true /mnt/bko-target/usr/bin/os-prober").expect(&bt);
    let cfg = bt.find("chroot /mnt/bko-target env LANG=C update-grub\n").expect(&bt);
    let nvram = bt.find("efibootmgr --quiet --create --disk /dev/vdb --part 1 --label kali --loader '\\EFI\\kali\\grubx64.efi'\n").expect(&bt);
    assert!(i < g && g < off && off < cfg && cfg < nvram, "{bt}");
    assert_eq!(bt.matches("grub-install").count(), 1, "{bt}");
    assert!(!bt.contains("grub-mkconfig") && !bt.contains("--removable") && !bt.contains("i386-pc") && !bt.contains("dracut"), "{bt}");

    // The same machine as Debian 13: the same plan, word for word (the ESP
    // still says EFI/kali, so --bootloader-id is kali there too).
    let t = kali();
    std::fs::remove_file(t.p("/etc/os-release")).unwrap();
    std::fs::remove_file(t.p("/usr/lib/os-release")).unwrap();
    t.debian13();
    let (dv, db) = kali_plan(&t);
    assert_eq!(s(&dv, &["os", "id"]), "debian");
    assert!(db.problems.is_empty(), "{:?}", db.problems);
    assert_eq!(plan_text(&b), plan_text(&db));
    assert_eq!(b.notes, db.notes);
}

/// Kali under vm-run: blank OVMF variables, so the firmware booted the
/// fallback path, whose BOOTX64.EFI is a copy of EFI/kali's GRUB.  Still
/// GRUB without shim, still EFI/kali (from the ESP), and the firmware's
/// own entry made again.
#[test]
fn kali_fallback_boot() {
    use crate::efi::GLOBAL;
    use crate::load::{commands::Cmd, Stage};
    let t = kali();
    let var = |n: &str| format!("/sys/firmware/efi/efivars/{n}-{GLOBAL}");
    std::fs::remove_file(t.p(&var("Boot0001"))).unwrap();
    t.file(&var("Boot0002"), efi_load_option("UEFI Misc Device", 1, 2048, 71680, KALI_ESP, "\\EFI\\BOOT\\BOOTX64.EFI"))
        .file(&var("BootOrder"), [7u8, 0, 0, 0, 2, 0])
        .file(&var("BootCurrent"), [6u8, 0, 0, 0, 2, 0]);
    let (v, b) = kali_plan(&t);
    let l = v.at(&["boot", "loader"]).unwrap();
    assert_eq!(s(l, &["name"]), "grub");
    assert_eq!(l.get("via_shim"), Some(&Value::Bool(false)));
    assert!(b.problems.is_empty(), "{:?}", b.problems);
    let bt: String = b.stage(Stage::Boot).iter().map(Cmd::shell).collect();
    assert!(bt.contains("chroot /mnt/bko-target grub-install --target=x86_64-efi --efi-directory=/boot/efi --bootloader-id=kali --no-nvram\n") && bt.contains("env LANG=C update-grub\n"), "{bt}");
    assert!(bt.contains("efibootmgr --quiet --create --disk /dev/vdb --part 1 --label 'UEFI Misc Device' --loader '\\EFI\\BOOT\\BOOTX64.EFI'\n"), "{bt}");
    assert!(!bt.contains("grub-mkconfig"), "{bt}");
}

const ELEMENTARY_KV: &str = "6.14.0-37-generic";
/// The ESP's PARTUUID, and its vfat volume ID.
const ELEMENTARY_ESP: &str = "e1e0e1e0-0000-4000-8000-000000000001";
const ELEMENTARY_ESP_ID: &str = "E1E0-0008";
/// elementary OS 8.1's /etc/os-release (the elementary8 guest, docs/design/elementary.md).
const ELEMENTARY_OS_RELEASE: &str = "PRETTY_NAME=\"elementary OS 8\"\nNAME=\"elementary OS\"\nVERSION_ID=\"8\"\nVERSION=\"8.1\"\nVERSION_CODENAME=circe\nID=elementary\nID_LIKE=\"ubuntu debian\"\nUBUNTU_CODENAME=noble\nUBUNTU_VERSION_ID=\"24.04\"\nUBUNTU_CODENAME=noble\n";
/// The VG UUID of the `data` group (lvm::sample::METADATA's), as
/// device-mapper writes it.
const ELEMENTARY_VG: &str = "Yb3kFa0Pz1AbCdEfGhIjKlMnOpQrStUv";

/// elementary OS 8.1 as distinst lays out the default UEFI install
/// (docs/design/elementary.md), in small (the ESP is 278 MiB there): GPT
/// with a vfat ESP at /boot/efi and one LVM PV, VG `data` with LVs `root`
/// (ext4, /) and `swap`, both named by /dev/mapper in fstab.  Encrypted
/// (LUKS): an ext4 /boot partition between them, and the PV inside LUKS2
/// opened as `cryptdata` (crypttab `cryptdata UUID=… none luks`).
/// initramfs-tools' /boot/initrd.img-K; Ubuntu's shim and signed GRUB in
/// EFI/ubuntu (distinst maps "elementary OS" to ubuntu) and shim at the
/// fallback path.  distinst ran grub-install with
/// --boot-directory=/boot/efi/EFI/ubuntu, so the stub EFI/ubuntu/grub.cfg
/// points at EFI/ubuntu/grub/grub.cfg on the ESP, with the modules beside
/// it; /boot/grub/grub.cfg is update-grub's.  Booted from NVRAM's shim
/// entry with Secure Boot on.
fn elementary(luks: bool) -> Tree {
    use crate::efi::GLOBAL;
    let t = Tree::new(if luks { "scn-elementary-luks" } else { "scn-elementary" });
    let k = ELEMENTARY_KV;
    let luks_uuid = "e1e01005-6b0a-4d6c-9a2e-8f7e1d2c3b4a";
    let mut img = vec![0u8; 100 * MIB];
    let mut parts = vec![(ESP, ELEMENTARY_ESP, 2048, 73727, 0, "")];
    if luks {
        parts.push((LINUX, "e1e0e1e0-0000-4000-8000-000000000002", 73728, 90111, 0, ""));
    }
    let pv_start: u64 = if luks { 90112 } else { 73728 };
    parts.push((LINUX, if luks { "e1e0e1e0-0000-4000-8000-000000000003" } else { "e1e0e1e0-0000-4000-8000-000000000002" }, pv_start, 204766, 0, ""));
    write_gpt(&mut img, 512, &GptSpec { disk_guid: "e1e0e1e0-0000-4000-8000-00000000d15c", parts });
    put(&mut img, 2048, &fat::sample::fat32(71680, 0xe1e0_0008, ""));
    // The root's UUID is ext::sample's (10111213-...); /boot's differs.
    let root = "10111213-1415-1617-1819-1a1b1c1d1e1f";
    let boot = "b0111213-1415-1617-1819-1a1b1c1d1e1f";
    let mut boot_fs = ext::sample::image(0x4 | 0x1000, 0x2 | 0x40 | 0x80 | 0x200, 0x1 | 0x400, "");
    boot_fs[1024 + 104] = 0xb0;
    if luks {
        put(&mut img, 73728, &boot_fs);
    }
    let metadata = lvm::sample::METADATA.replacen("vg0 {", "data {", 1).replace("swap_1 {", "swap {").replace("/dev/vda5", if luks { "/dev/mapper/cryptdata" } else { "/dev/vda2" });
    let pv = lvm::sample::pv_with(512, 1 << 20, &metadata);
    let pv_part = if luks { "vda3" } else { "vda2" };
    if !luks {
        put(&mut img, pv_start as usize, &pv);
    } else {
        put(&mut img, pv_start as usize, &luks::sample::luks2(luks_uuid, ""));
    }
    let var = |n: &str| format!("/sys/firmware/efi/efivars/{n}-{GLOBAL}");
    t.disk("vda", "254:0", &img, 512)
        .partition("vda", "vda1", "254:1", 1, 2048, 71680, &img)
        .file("/sys/block/vda/device/model", "")
        .symlink(&format!("/dev/disk/by-partuuid/{ELEMENTARY_ESP}"), "../../vda1");
    if luks {
        t.partition("vda", "vda2", "254:2", 2, 73728, 16384, &img).partition("vda", "vda3", "254:3", 3, pv_start, 204767 - pv_start, &img);
    } else {
        t.partition("vda", "vda2", "254:2", 2, pv_start, 204767 - pv_start, &img);
    }
    // The LVs (and, encrypted, the LUKS mapping under them) as dm devices.
    let lv = |id: &str| format!("LVM-{ELEMENTARY_VG}{id}aaaabbbbccccddddeeeeffffff");
    let root_fs = ext::sample::image(0x4 | 0x1000, 0x2 | 0x40 | 0x80 | 0x200, 0x1 | 0x400, "");
    let swap_fs = misc::sample::swap(1024, "");
    let (lv_parent, first_lv) = if luks { ("dm-0", 1) } else { (pv_part, 0) };
    if luks {
        let hex = luks_uuid.replace('-', "");
        t.disk("dm-0", "253:0", &pv, 512)
            .file("/sys/block/dm-0/dm/name", "cryptdata\n")
            .file("/sys/block/dm-0/dm/uuid", format!("CRYPT-LUKS2-{hex}-cryptdata\n"))
            .file("/sys/block/dm-0/slaves/vda3", "")
            .file("/sys/block/vda/vda3/holders/dm-0", "")
            .symlink("/dev/mapper/cryptdata", "../dm-0");
    }
    for (i, (name, id, image)) in [("data-root", "r00t00", &root_fs), ("data-swap", "sw4p00", &swap_fs)].into_iter().enumerate() {
        let dm = format!("dm-{}", first_lv + i);
        t.disk(&dm, &format!("253:{}", first_lv + i), image, 512)
            .file(&format!("/sys/block/{dm}/dm/name"), format!("{name}\n"))
            .file(&format!("/sys/block/{dm}/dm/uuid"), format!("{}\n", lv(id)))
            .file(&format!("/sys/block/{dm}/slaves/{lv_parent}"), "")
            .symlink(&format!("/dev/mapper/{name}"), &format!("../{dm}"));
        if luks {
            t.file(&format!("/sys/block/dm-0/holders/{dm}"), "");
        } else {
            t.file(&format!("/sys/block/vda/{pv_part}/holders/{dm}"), "");
        }
    }
    let (root_dm, swap_dm) = if luks { ("253:1", "dm-2") } else { ("253:0", "dm-1") };
    let mut mountinfo = format!("26 1 {root_dm} / / rw,noatime shared:1 - ext4 /dev/mapper/data-root rw,errors=remount-ro\n");
    let mut fstab = format!("UUID={ELEMENTARY_ESP_ID}  /boot/efi  vfat  umask=0077  0  0\n");
    if luks {
        mountinfo.push_str("27 26 254:2 / /boot rw,relatime shared:2 - ext4 /dev/vda2 rw\n28 27 254:1 / /boot/efi rw,relatime shared:3 - vfat /dev/vda1 rw,fmask=0077,dmask=0077\n");
        fstab.push_str(&format!("UUID={boot}  /boot  ext4  noatime,errors=remount-ro  0  0\n"));
        t.file("/etc/crypttab", format!("cryptdata UUID={luks_uuid} none luks\n"));
    } else {
        mountinfo.push_str("27 26 254:1 / /boot/efi rw,relatime shared:2 - vfat /dev/vda1 rw,fmask=0077,dmask=0077\n");
    }
    fstab.push_str("/dev/mapper/data-root  /  ext4  noatime,errors=remount-ro  0  0\n/dev/mapper/data-swap  none  swap  defaults  0  0\n");
    // Where GRUB finds the kernels: / on the root LV, or /boot's own file system.
    let (search, kdir) = if luks { (boot, "") } else { (root, "/boot") };
    let (shim, grub, mm) = (vec![1u8; 300], vec![2u8; 400], vec![3u8; 200]);
    let full_cfg = format!(
        "insmod part_gpt\ninsmod ext2\nsearch --no-floppy --fs-uuid --set=root {search}\nmenuentry 'elementary OS 8' {{\n\tlinux\t{kdir}/vmlinuz-{k} root=/dev/mapper/data-root ro quiet splash\n\tinitrd\t{kdir}/initrd.img-{k}\n}}\n"
    );
    t.file("/sys/firmware/efi/fw_platform_size", "64\n")
        .file(&var("Boot0001"), efi_load_option("elementary OS 8", 1, 2048, 71680, ELEMENTARY_ESP, "\\EFI\\ubuntu\\shimx64.efi"))
        .file(&var("BootOrder"), [7u8, 0, 0, 0, 1, 0])
        .file(&var("BootCurrent"), [6u8, 0, 0, 0, 1, 0])
        .file(&var("SecureBoot"), [6u8, 0, 0, 0, 1])
        .file("/proc/self/mountinfo", mountinfo)
        .file("/proc/swaps", format!("Filename\t\t\t\tType\t\tSize\t\tUsed\t\tPriority\n/dev/{swap_dm}                               partition\t4194300\t\t0\t\t-2\n"))
        .file("/etc/fstab", fstab)
        .file("/usr/lib/os-release", ELEMENTARY_OS_RELEASE)
        .symlink("/etc/os-release", "../usr/lib/os-release")
        .file("/etc/debian_version", "trixie/sid\n")
        .symlink("/lib", "usr/lib")
        .file(&format!("/boot/vmlinuz-{k}"), "k")
        .file(&format!("/boot/initrd.img-{k}"), "initramfs-tools")
        .file(&format!("/boot/config-{k}"), "")
        .file(&format!("/boot/System.map-{k}"), "")
        .symlink("/boot/vmlinuz", &format!("vmlinuz-{k}"))
        .symlink("/boot/initrd.img", &format!("initrd.img-{k}"))
        .file("/boot/grub/grub.cfg", &full_cfg)
        .file("/etc/default/grub", "GRUB_DEFAULT=0\nGRUB_TIMEOUT_STYLE=hidden\nGRUB_TIMEOUT=0\nGRUB_DISTRIBUTOR=`( . /etc/os-release; echo ${NAME:-Ubuntu} ) 2>/dev/null || echo Ubuntu`\nGRUB_CMDLINE_LINUX_DEFAULT=\"quiet splash\"\nGRUB_ENABLE_CRYPTODISK=y\n")
        .file("/etc/initramfs-tools/initramfs.conf", "MODULES=most\nCOMPRESS=zstd\n")
        .dir(&format!("/usr/lib/modules/{k}/kernel"))
        .file("/boot/efi/EFI/ubuntu/shimx64.efi", &shim)
        .file("/boot/efi/EFI/ubuntu/grubx64.efi", &grub)
        .file("/boot/efi/EFI/ubuntu/mmx64.efi", &mm)
        .file("/boot/efi/EFI/ubuntu/BOOTX64.CSV", "shimx64.efi,ubuntu,,This is the boot entry for ubuntu\n")
        .file("/boot/efi/EFI/ubuntu/grub.cfg", format!("search.fs_uuid {ELEMENTARY_ESP_ID} root \nset prefix=($root)'/EFI/ubuntu/grub'\nconfigfile $prefix/grub.cfg\n"))
        .file("/boot/efi/EFI/ubuntu/grub/grub.cfg", &full_cfg)
        .file("/boot/efi/EFI/ubuntu/grub/x86_64-efi/normal.mod", "x")
        .file("/boot/efi/EFI/BOOT/BOOTX64.EFI", &shim)
        .file("/boot/efi/EFI/BOOT/fbx64.efi", vec![4u8; 100])
        .file("/boot/efi/EFI/BOOT/mmx64.efi", &mm)
        .file("/proc/sys/kernel/osrelease", format!("{k}\n"))
        .file("/proc/sys/kernel/hostname", "elementary-pc\n")
        .file("/proc/cmdline", format!("BOOT_IMAGE={kdir}/vmlinuz-{k} root=/dev/mapper/data-root ro quiet splash\n"));
    let mut pkgs = vec![
        ("shim-signed", "1.58+15.8-0ubuntu1"),
        ("grub-efi-amd64-signed", "1.202.5+2.12-1ubuntu7.3"),
        ("grub-efi-amd64", "2.12-1ubuntu7.3"),
        ("grub-efi-amd64-bin", "2.12-1ubuntu7.3"),
        ("grub-common", "2.12-1ubuntu7.3"),
        ("grub2-common", "2.12-1ubuntu7.3"),
        ("mokutil", "0.6.0-2build3"),
        ("linux-generic-hwe-24.04", "6.14.0-37.37~24.04.1"),
        ("linux-image-6.14.0-37-generic", "6.14.0-37.37~24.04.1"),
        ("initramfs-tools", "0.142ubuntu25.5"),
        ("lvm2", "2.03.16-3ubuntu3.2"),
        ("efibootmgr", "18-1build2"),
        ("os-prober", "1.81ubuntu4"),
        ("e2fsprogs", "1.47.0-2.4~exp1ubuntu4.1"),
        ("dosfstools", "4.2-1.1build1"),
    ];
    if luks {
        pkgs.extend([("cryptsetup", "2:2.7.0-1ubuntu4.2"), ("cryptsetup-initramfs", "2:2.7.0-1ubuntu4.2")]);
    }
    let status: String = pkgs.iter().map(|(n, v)| format!("Package: {n}\nStatus: install ok installed\nArchitecture: amd64\nVersion: {v}\n\n")).collect();
    t.file("/var/lib/dpkg/status", status);
    t
}

/// elementary OS's plan from a tree, the source disk vda going to vdb.
fn elementary_plan(t: &Tree) -> (Value, crate::load::commands::Built) {
    use crate::load::{commands, layout::Layout, target::Mapping, Parsed};
    let v = run(t, true, FakeRunner::default());
    let l = Layout::from_json(v.clone()).unwrap();
    let Ok(Parsed::Run(o)) = crate::load::parse_args(&["l.json".to_string()]) else { unreachable!() };
    (v, commands::build(&l, &o, &Mapping { pairs: vec![("vda".into(), "vdb".into())], ..Mapping::default() }))
}

/// elementary OS 8 read back and planned (docs/design/elementary.md, E2):
/// supported by its ID; GRUB through shim in EFI/ubuntu from BootCurrent;
/// the ESP's distinst layout (the stub, its grub/ directory, the fallback
/// shim); the `data` VG on the PV, `root` on /.  load makes the PV and the
/// group again with their UUIDs, runs update-initramfs, Ubuntu's
/// grub-install into EFI/ubuntu with no NVRAM write (shim and the signed
/// GRUB again, and a stub that points at /boot/grub), update-grub (not
/// grub-mkconfig) with os-prober off, and makes the shim entry again.
#[test]
fn elementary_guest() {
    use crate::load::{commands::Cmd, Stage};
    let k = ELEMENTARY_KV;
    for luks in [false, true] {
        let (v, b) = elementary_plan(&elementary(luks));
        assert_eq!(s(&v, &["os", "id"]), "elementary");
        assert_eq!(s(&v, &["os", "version_id"]), "8");
        assert_eq!(s(&v, &["os", "version_codename"]), "circe");
        assert_eq!(s(&v, &["firmware", "mode"]), "uefi");
        assert_eq!(v.at(&["firmware", "secure_boot"]), Some(&Value::Bool(true)));
        let l = v.at(&["boot", "loader"]).unwrap();
        assert_eq!(s(l, &["name"]), "grub");
        assert_eq!(l.get("via_shim"), Some(&Value::Bool(true)));
        assert_eq!(s(l, &["package"]), "grub-efi-amd64-signed");
        assert_eq!(s(l, &["shim_package"]), "shim-signed");
        assert_eq!(s(&v, &["boot", "grub", "dir"]), "/boot/grub");
        assert_eq!(s(&v, &["boot", "initramfs", "generator"]), "initramfs-tools");
        let kernels = arr(&v, &["boot", "kernels", "installed"]);
        assert_eq!(kernels.len(), 1, "{}", v.at(&["boot", "kernels"]).unwrap().compact());
        assert_eq!(s(&kernels[0], &["version"]), k);
        let esp = v.at(&["boot", "esps", "0"]).unwrap();
        assert_eq!(s(esp, &["mounted_at"]), "/boot/efi");
        let files: Vec<(&str, Option<&str>)> = arr(esp, &["contents", "files"]).iter().map(|f| (s(f, &["path"]), f.get("same_as").and_then(Value::as_str))).collect();
        assert!(files.contains(&("EFI/BOOT/BOOTX64.EFI", Some("EFI/ubuntu/shimx64.efi"))), "{files:?}");
        for f in ["EFI/ubuntu/grub.cfg", "EFI/ubuntu/grub/grub.cfg", "EFI/ubuntu/BOOTX64.CSV", "EFI/BOOT/fbx64.efi"] {
            assert!(files.iter().any(|(p, _)| *p == f), "{f}: {files:?}");
        }
        let vg = v.at(&["storage", "lvm", "volume_groups", "0"]).unwrap();
        assert_eq!(s(vg, &["name"]), "data");
        assert_eq!(s(vg, &["physical_volumes", "0", "device"]), if luks { "dm-0" } else { "vda2" });
        assert_eq!(s(vg, &["logical_volumes", "0", "name"]), "root");
        assert_eq!(s(vg, &["logical_volumes", "1", "name"]), "swap");
        let fstab = arr(&v, &["storage", "fstab"]);
        let root = fstab.iter().find(|e| s(e, &["file"]) == "/").unwrap();
        assert_eq!(s(root, &["spec"]), "/dev/mapper/data-root");
        assert_eq!(s(root, &["resolves_to"]), if luks { "dm-1" } else { "dm-0" });
        if luks {
            assert_eq!(s(&v, &["storage", "crypttab", "0", "name"]), "cryptdata");
            assert_eq!(s(&v, &["storage", "crypttab", "0", "resolves_to"]), "vda3");
        }
        assert!(arr(&v, &["warnings"]).is_empty(), "{:?}", v.get("warnings"));

        assert!(b.problems.is_empty(), "{:?}", b.problems);
        let asm: String = b.stage(Stage::Assemble).iter().map(Cmd::shell).collect();
        let pv = if luks { "/dev/mapper/cryptdata" } else { "/dev/vdb2" };
        let lvm = |rest: &str| format!("--devicesfile '' --devices {pv} {rest}\n");
        let lines = [
            format!("pvcreate {}", lvm(&format!("--uuid Hc7n2m-aaaa-bbbb-cccc-dddd-eeee-ffffff --restorefile /run/bko-holt/lvm/data.vg {pv}"))),
            format!("vgcfgrestore {}", lvm("--file /run/bko-holt/lvm/data.vg data")),
            format!("vgchange {}", lvm("--activate y data")),
        ];
        let mut at = 0;
        for l in &lines {
            let i = asm[at..].find(l.as_str()).unwrap_or_else(|| panic!("{l} not after {at}: {asm}")) + at;
            at = i + l.len();
        }
        if luks {
            let open = asm.find("cryptsetup open /dev/vdb3 cryptdata\n").expect(&asm);
            assert!(asm.contains("cryptsetup luksFormat ") && asm.contains(" --uuid=e1e01005-6b0a-4d6c-9a2e-8f7e1d2c3b4a /dev/vdb3\n") && open < asm.find("pvcreate ").unwrap(), "{asm}");
        }
        let f: String = b.stage(Stage::Format).iter().map(Cmd::shell).collect();
        assert!(f.contains("/dev/mapper/data-root\n") && f.contains("mkswap ") && f.contains("mkfs.fat "), "{f}");
        let bt: String = b.stage(Stage::Boot).iter().map(Cmd::shell).collect();
        let i = bt.find("chroot /mnt/bko-target update-initramfs -u -k all\n").expect(&bt);
        let g = bt.find("chroot /mnt/bko-target grub-install --target=x86_64-efi --efi-directory=/boot/efi --bootloader-id=ubuntu --no-nvram\n").expect(&bt);
        let off = bt.find("[ ! -e /mnt/bko-target/usr/bin/os-prober ] || mount --bind /mnt/bko-target/usr/bin/true /mnt/bko-target/usr/bin/os-prober").expect(&bt);
        let cfg = bt.find("chroot /mnt/bko-target env LANG=C update-grub\n").expect(&bt);
        let nvram = bt.find("efibootmgr --quiet --create --disk /dev/vdb --part 1 --label 'elementary OS 8' --loader '\\EFI\\ubuntu\\shimx64.efi'\n").expect(&bt);
        assert!(i < g && g < off && off < cfg && cfg < nvram, "{bt}");
        assert_eq!(bt.matches("grub-install").count(), 1, "{bt}");
        assert!(!bt.contains("grub-mkconfig") && !bt.contains("--removable") && !bt.contains("i386-pc") && !bt.contains("dracut"), "{bt}");
    }
}

const PVE_KV: &str = "7.0.4-1-pve";
/// Each disk's ESP: its PARTUUID and its vfat volume ID (as a serial).
const PVE_ESPS: [(&str, u32, &str); 2] = [("e5e5e5e5-0000-4000-8000-000000000001", 0x1234_abcd, "1234-ABCD"), ("e5e5e5e5-0000-4000-8000-000000000002", 0x5678_ef01, "5678-EF01")];

/// Proxmox VE 9.2 as its installer lays out ZFS root (docs/design/proxmox.md):
/// Debian 13's os-release, on each disk a BIOS boot partition, a 32 MiB ESP
/// that nothing mounts and a ZFS partition; `rpool` on one disk or a mirror
/// of two; proxmox-boot-tool in systemd-boot mode with one ESP a disk in
/// /etc/kernel/proxmox-boot-uuids, the kernels and loader entries on each;
/// pmxcfs at /etc/pve; a zvol in rpool/data.  The ESPs' files are in the
/// tree where dump mounts them (/run/bko-holt/esp-ID), the mount itself
/// answered by the runner.
fn proxmox(mirror: bool) -> (Tree, FakeRunner) {
    use crate::efi::{GLOBAL, LOADER};
    let t = Tree::new(if mirror { "scn-pve-mirror" } else { "scn-pve" });
    t.debian13();
    let k = PVE_KV;
    let disks: Vec<(&str, &str)> = if mirror { vec![("vda", "254:0"), ("vdb", "254:16")] } else { vec![("vda", "254:0")] };
    let leaf = |n: &str, i: u64| format!(r#"{{"type": "disk", "id": {i}, "guid": {}, "path": "/dev/{n}3", "whole_disk": 0}}"#, 11 + i);
    let tree = if mirror {
        format!(r#"{{"type": "mirror", "id": 0, "guid": 10, "ashift": 12, "children": [{}, {}]}}"#, leaf("vda", 0), leaf("vdb", 1))
    } else {
        r#"{"type": "disk", "id": 0, "guid": 11, "path": "/dev/vda3", "whole_disk": 0, "ashift": 12}"#.to_string()
    };
    let var = |n: &str| format!("/sys/firmware/efi/efivars/{n}-{GLOBAL}");
    let sd = vec![5u8; 300];
    let entry = format!(
        "title    Proxmox Virtual Environment\nversion  {k}\noptions  root=ZFS=rpool/ROOT/pve-1 boot=zfs\nlinux    /EFI/proxmox/{k}/vmlinuz-{k}\ninitrd   /EFI/proxmox/{k}/initrd.img-{k}\n"
    );
    let mut order = vec![7u8, 0, 0, 0];
    for (i, (d, devno)) in disks.iter().enumerate() {
        let (partuuid, serial, id) = PVE_ESPS[i];
        let mut img = vec![0u8; 96 * MIB];
        write_gpt(
            &mut img,
            512,
            &GptSpec {
                disk_guid: if i == 0 { "9e9e9e9e-0000-4000-8000-00000000000a" } else { "9e9e9e9e-0000-4000-8000-00000000000b" },
                parts: vec![
                    (BIOS_BOOT, if i == 0 { "9e9e9e9e-1111-4000-8000-00000000000a" } else { "9e9e9e9e-1111-4000-8000-00000000000b" }, 34, 2047, 0, ""),
                    (ESP, partuuid, 2048, 67583, 0, ""),
                    ("6a898cc3-1dd2-11b2-99a6-080020c9b98d", if i == 0 { "9e9e9e9e-3333-4000-8000-00000000000a" } else { "9e9e9e9e-3333-4000-8000-00000000000b" }, 67584, 184319, 0, ""),
                ],
            },
        );
        put(&mut img, 2048, &fat::sample::fat32(65536, serial, ""));
        let cfg = format!(
            r#"{{"version": 5000, "name": "rpool", "state": 0, "txg": 4, "pool_guid": 999, "hostname": "pve", "hostid": 305419896, "top_guid": {}, "guid": {},
                "vdev_children": 1, "vdev_tree": {tree}, "features_for_read": {{"com.delphix:hole_birth": true}}}}"#,
            if mirror { 10 } else { 11 },
            11 + i
        );
        put(&mut img, 67584, &zfs::pack::vdev(116736 * 512, &crate::json::parse(&cfg).unwrap()));
        let major = devno.split(':').next().unwrap();
        let minor: u32 = devno.split(':').nth(1).unwrap().parse().unwrap();
        t.disk(d, devno, &img, 512)
            .partition(d, &format!("{d}1"), &format!("{major}:{}", minor + 1), 1, 34, 2014, &img)
            .partition(d, &format!("{d}2"), &format!("{major}:{}", minor + 2), 2, 2048, 65536, &img)
            .partition(d, &format!("{d}3"), &format!("{major}:{}", minor + 3), 3, 67584, 116736, &img)
            .symlink(&format!("/dev/disk/by-partuuid/{partuuid}"), &format!("../../{d}2"))
            .symlink(&format!("/dev/disk/by-uuid/{id}"), &format!("../../{d}2"))
            .file(&var(&format!("Boot000{i}")), efi_load_option("Linux Boot Manager", 2, 2048, 65536, partuuid, "\\EFI\\systemd\\systemd-bootx64.efi"));
        order.extend([i as u8, 0]);
        let esp = format!("/run/bko-holt/esp-{id}");
        t.file(&format!("{esp}/EFI/systemd/systemd-bootx64.efi"), &sd)
            .file(&format!("{esp}/EFI/BOOT/BOOTX64.EFI"), &sd)
            .file(&format!("{esp}/EFI/proxmox/{k}/vmlinuz-{k}"), "k")
            .file(&format!("{esp}/EFI/proxmox/{k}/initrd.img-{k}"), "i")
            .file(&format!("{esp}/loader/loader.conf"), "timeout 3\ndefault proxmox-*\n")
            .file(&format!("{esp}/loader/entries/proxmox-{k}.conf"), &entry);
    }
    let mut loader_info = vec![6u8, 0, 0, 0];
    for c in "systemd-boot 257.9-1~deb13u1".encode_utf16().chain([0]) {
        loader_info.extend(c.to_le_bytes());
    }
    let ids: String = PVE_ESPS[..disks.len()].iter().map(|(_, _, id)| format!("{id}\n")).collect();
    t.file("/sys/firmware/efi/fw_platform_size", "64\n")
        .file(&var("BootOrder"), order)
        .file(&var("BootCurrent"), [6u8, 0, 0, 0, 0, 0])
        .file(&var("SecureBoot"), [6u8, 0, 0, 0, 0])
        .file(&format!("/sys/firmware/efi/efivars/LoaderInfo-{LOADER}"), loader_info)
        .file(
            "/proc/self/mountinfo",
            "26 1 0:24 / / rw,relatime shared:1 - zfs rpool/ROOT/pve-1 rw,xattr,posixacl\n\
             27 26 0:25 / /rpool rw,relatime shared:2 - zfs rpool rw,xattr,posixacl\n\
             28 27 0:26 / /rpool/data rw,relatime shared:3 - zfs rpool/data rw,xattr,posixacl\n\
             29 26 0:27 / /var/lib/vz rw,relatime shared:4 - zfs rpool/var-lib-vz rw,xattr,posixacl\n\
             30 26 0:50 / /etc/pve rw,nosuid,nodev,relatime shared:5 - fuse /dev/fuse rw,user_id=0,group_id=0,default_permissions,allow_other\n\
             31 26 0:51 / /var/lib/lxcfs rw,nosuid,nodev,relatime shared:6 - fuse.lxcfs lxcfs rw,user_id=0,group_id=0,allow_other\n",
        )
        .file("/etc/fstab", "# <file system> <mount point> <type> <options> <dump> <pass>\nproc /proc proc defaults 0 0\n")
        .file("/etc/hostid", [0x78u8, 0x56, 0x34, 0x12])
        .file("/etc/zfs/zpool.cache", zfs::pack::packed(&crate::json::parse(r#"{"rpool": {"name": "rpool", "pool_guid": 999}}"#).unwrap()))
        .file("/etc/kernel/proxmox-boot-uuids", ids)
        .file("/etc/kernel/cmdline", "root=ZFS=rpool/ROOT/pve-1 boot=zfs\n")
        .file(&format!("/boot/vmlinuz-{k}"), "k")
        .file(&format!("/boot/initrd.img-{k}"), "i")
        .file(&format!("/boot/config-{k}"), "")
        .file(&format!("/boot/System.map-{k}"), "")
        .symlink("/lib", "usr/lib")
        .dir(&format!("/usr/lib/modules/{k}/kernel"))
        .file("/etc/initramfs-tools/initramfs.conf", "MODULES=most\nCOMPRESS=zstd\n")
        .file("/proc/sys/kernel/osrelease", format!("{k}\n"))
        .file("/proc/sys/kernel/hostname", "pve\n")
        .file("/proc/cmdline", format!("initrd=\\EFI\\proxmox\\{k}\\initrd.img-{k} root=ZFS=rpool/ROOT/pve-1 boot=zfs\n"));
    let pkgs = [
        ("proxmox-ve", "9.2.0"),
        ("pve-manager", "9.2.3"),
        ("pve-cluster", "9.0.6"),
        ("proxmox-kernel-helper", "9.0.4"),
        ("proxmox-default-kernel", "2.0.0"),
        ("proxmox-kernel-7.0.4-1-pve-signed", "7.0.4-1"),
        ("zfsutils-linux", "2.4.1-pve1"),
        ("zfs-initramfs", "2.4.1-pve1"),
        ("initramfs-tools", "0.148.3"),
        ("systemd-boot", "257.9-1~deb13u1"),
        ("systemd-boot-efi", "257.9-1~deb13u1"),
        ("efibootmgr", "18-2"),
        ("dosfstools", "4.2-1.2"),
    ];
    let status: String = pkgs.iter().map(|(n, v)| format!("Package: {n}\nStatus: install ok installed\nArchitecture: amd64\nVersion: {v}\n\n")).collect();
    t.file("/var/lib/dpkg/status", status);
    let vdevs = if mirror {
        r#"{"mirror-0": {"name": "mirror-0", "vdev_type": "mirror", "vdevs": {"/dev/vda3": {"name": "/dev/vda3", "vdev_type": "disk", "path": "/dev/vda3"}, "/dev/vdb3": {"name": "/dev/vdb3", "vdev_type": "disk", "path": "/dev/vdb3"}}}}"#
    } else {
        r#"{"/dev/vda3": {"name": "/dev/vda3", "vdev_type": "disk", "path": "/dev/vda3"}}"#
    };
    let fs = |name: &str, mp: &str, canmount: &str, mounted: &str| format!("{name}\tfilesystem\t900\t2000\t96\t{mp}\t{canmount}\t{mounted}\toff\t-\tnone\tnone\t-\t-\ton\t131072\t-\t-\tposix\tsa\tauto\tnone\toff\tsensitive\ton\ton\t1\t-\n");
    let mut runner = FakeRunner::default()
        .with("zpool list -H -o name", "rpool\n")
        .with(
            "zpool get -H -p -o property,value,source all rpool",
            "size\t62277025792\t-\nashift\t12\tlocal\ncachefile\t-\tdefault\nfree\t1\t-\nfeature@encryption\tenabled\tlocal\nfeature@async_destroy\tenabled\tlocal\n",
        )
        .with(
            "zfs list -H -p -t filesystem,volume -o name,type,used,available,referenced,mountpoint,canmount,mounted,encryption,encryptionroot,keyformat,keylocation,keystatus,origin,compression,recordsize,volsize,volblocksize,acltype,xattr,dnodesize,normalization,utf8only,casesensitivity,atime,relatime,copies,com.sun:auto-snapshot",
            &[
                fs("rpool", "/rpool", "on", "yes"),
                fs("rpool/ROOT", "/rpool/ROOT", "on", "no"),
                fs("rpool/ROOT/pve-1", "/", "on", "yes"),
                fs("rpool/data", "/rpool/data", "on", "yes"),
                "rpool/data/vm-100-disk-0\tvolume\t67108864\t2000\t56\t-\t-\t-\toff\t-\tnone\tnone\t-\t-\ton\t-\t67108864\t16384\toff\t-\t-\t-\t-\t-\t-\t-\t1\t-\n".to_string(),
                fs("rpool/var-lib-vz", "/var/lib/vz", "on", "yes"),
            ]
            .concat(),
        )
        .with(
            "zfs get -H -p -t filesystem,volume -s local,received -o name,property,value,source all",
            "rpool\tcompression\ton\tlocal\nrpool\tacltype\tposix\tlocal\nrpool\txattr\tsa\tlocal\nrpool\tmountpoint\t/rpool\tlocal\nrpool/ROOT/pve-1\tmountpoint\t/\tlocal\nrpool/var-lib-vz\tmountpoint\t/var/lib/vz\tlocal\nrpool/data/vm-100-disk-0\tvolsize\t67108864\tlocal\n",
        )
        .with(
            "zpool status -j --json-int -P rpool",
            &format!(r#"{{"output_version": {{"command": "zpool status", "vers_major": 0, "vers_minor": 1}}, "pools": {{"rpool": {{"name": "rpool", "state": "ONLINE", "vdevs": {{"rpool": {{"name": "rpool", "vdev_type": "root", "vdevs": {vdevs}}}}}}}}}}}"#),
        );
    for (i, (d, _)) in disks.iter().enumerate() {
        let at = format!("/run/bko-holt/esp-{}", PVE_ESPS[i].2);
        runner = runner.with(&format!("mount -t vfat -o ro,noexec,nodev,nosuid /dev/{d}2 {at}"), "").with(&format!("umount {at}"), "");
    }
    (t, runner)
}

/// Proxmox VE's plan from a tree, the source disks vda (and vdb) going to
/// sda (and sdb); `extra`, more of load's arguments.
fn proxmox_plan(t: &Tree, runner: FakeRunner, mirror: bool, extra: &[&str]) -> (Value, crate::load::commands::Built) {
    use crate::load::{commands, layout::Layout, target::Mapping, Parsed};
    let v = run(t, true, runner);
    let l = Layout::from_json(v.clone()).unwrap();
    let mut a = vec!["l.json".to_string()];
    a.extend(extra.iter().map(|s| s.to_string()));
    let Ok(Parsed::Run(o)) = crate::load::parse_args(&a) else { unreachable!() };
    let mut pairs = vec![("vda".to_string(), "sda".to_string())];
    if mirror {
        pairs.push(("vdb".into(), "sdb".into()));
    }
    (v, commands::build(&l, &o, &Mapping { pairs, ..Mapping::default() }))
}

/// Proxmox VE 9 on ZFS root, one disk: the unmounted ESP found from
/// proxmox-boot-uuids and read through a private read-only mount, its mode,
/// files and loader entries; pmxcfs not a backup source; os.product.
#[test]
fn proxmox_guest() {
    use crate::load::{commands::Cmd, Stage};
    let (t, runner) = proxmox(false);
    let (v, b) = proxmox_plan(&t, runner, false, &[]);
    assert_eq!(s(&v, &["os", "id"]), "debian");
    assert_eq!(s(&v, &["os", "version_id"]), "13");
    assert_eq!(s(&v, &["os", "product"]), "Proxmox VE 9.2");
    for (p, ver) in [("proxmox-kernel-helper", "9.0.4"), ("proxmox-default-kernel", "2.0.0"), ("proxmox-ve", "9.2.0"), ("pve-manager", "9.2.3"), ("zfs-initramfs", "2.4.1-pve1")] {
        assert_eq!(s(&v, &["boot", "packages", p]), ver, "{p}");
    }
    let pb = v.at(&["boot", "proxmox_boot"]).unwrap();
    assert_eq!(pb.get("uuids").unwrap().compact(), r#"["1234-ABCD"]"#);
    let e = &arr(pb, &["esps"])[0];
    assert_eq!(s(e, &["uuid"]), "1234-ABCD");
    assert_eq!(s(e, &["device"]), "vda2");
    assert_eq!(s(e, &["mode"]), "systemd-boot");
    let files: Vec<&str> = arr(e, &["contents", "files"]).iter().map(|f| s(f, &["path"])).collect();
    for f in ["EFI/systemd/systemd-bootx64.efi", "EFI/BOOT/BOOTX64.EFI", &format!("EFI/proxmox/{PVE_KV}/vmlinuz-{PVE_KV}"), &format!("loader/entries/proxmox-{PVE_KV}.conf")] {
        assert!(files.contains(&f), "{f}: {files:?}");
    }
    assert_eq!(s(pb, &["cmdline"]), "root=ZFS=rpool/ROOT/pve-1 boot=zfs");
    assert!(pb.get("pin").unwrap().is_null() && pb.get("manual_kernels").unwrap().is_null());
    // The same ESP in boot.esps, mounted nowhere; systemd-boot's entries from it.
    let esps = arr(&v, &["boot", "esps"]);
    assert_eq!(esps.len(), 1);
    assert_eq!(s(&esps[0], &["device"]), "vda2");
    assert!(esps[0].get("mounted_at").unwrap().is_null());
    assert_eq!(s(&v, &["boot", "loader", "name"]), "systemd-boot");
    assert_eq!(s(&v, &["boot", "systemd_boot", "entries", "0", "file"]), format!("proxmox-{PVE_KV}.conf"));
    // pmxcfs isn't one of the layout's mounts, nor a backup source; lxcfs neither.
    let paths: Vec<&str> = arr(&v, &["storage", "backup_sources"]).iter().map(|b| s(b, &["path"])).collect();
    assert!(!paths.contains(&"/etc/pve") && !paths.contains(&"/var/lib/lxcfs") && paths.contains(&"/"), "{paths:?}");
    assert!(!arr(&v, &["storage", "mounts"]).iter().any(|m| s(m, &["target"]) == "/etc/pve"));
    let w: Vec<&str> = arr(&v, &["warnings"]).iter().filter_map(Value::as_str).collect();
    assert_eq!(w, vec!["/etc/pve is Proxmox VE's pmxcfs (FUSE), not a file system to back up: its data is /var/lib/pve-cluster/config.db, a file on /, and comes back with /"]);

    assert!(b.problems.is_empty(), "{:?}", b.problems);
    let text = |st: Stage| -> String { b.stage(st).iter().map(Cmd::shell).collect() };
    // The ESP, unmounted on the source, made again with its volume ID.
    let fmt = text(Stage::Format);
    assert_eq!(fmt.lines().filter(|l| l.starts_with("mkfs.fat ")).count(), 1, "{fmt}");
    assert!(fmt.lines().any(|l| l.starts_with("mkfs.fat ") && l.contains("-i 1234ABCD") && l.ends_with("/dev/sda2")), "{fmt}");
    let bt = text(Stage::Boot);
    let initramfs = bt.find("chroot /mnt/bko-target update-initramfs -u -k all\n").expect(&bt);
    let init = bt.find("chroot /mnt/bko-target proxmox-boot-tool init /dev/disk/by-uuid/1234-ABCD\n").expect(&bt);
    let refresh = bt.find("chroot /mnt/bko-target proxmox-boot-tool refresh\n").expect(&bt);
    let list = bt.find("cat > /mnt/bko-target/etc/kernel/proxmox-boot-uuids <<'EOF'\n1234-ABCD\nEOF\n").expect(&bt);
    assert!(initramfs < init && init < refresh && refresh < list, "{bt}");
    assert_eq!(bt.matches("proxmox-boot-tool init").count(), 1, "{bt}");
    assert_eq!(bt.matches("proxmox-boot-tool refresh").count(), 1, "{bt}");
    assert!(!bt.contains("bootctl install") && !bt.contains("grub-install"), "{bt}");
    // bootctl install (in init) makes "Linux Boot Manager" again.
    assert!(!bt.contains("efibootmgr --quiet --create"), "{bt}");
    assert!(b.notes.iter().any(|n| n == "UEFI entry \"Linux Boot Manager\" (vda2, \\EFI\\systemd\\systemd-bootx64.efi): proxmox-boot-tool init's bootctl install makes it again; not recreated"), "{:?}", b.notes);
    assert!(b.notes.iter().any(|n| n == "Proxmox: proxmox-boot-tool rebuilt 1 ESP (systemd-boot); their old content isn't in the archive"), "{:?}", b.notes);
    assert!(b.notes.iter().any(|n| n == "ZFS volume rpool/data/vm-100-disk-0: made empty; archivers back up files, not zvols; Proxmox: VM disks are vzdump's or Proxmox Backup Server's to back up"), "{:?}", b.notes);
    assert!(text(Stage::Close).contains("zpool export rpool"));
    // --no-nvram: init's bootctl over an empty tmpfs in efivarfs' place
    // (unmounting it made bootctl fail: pve6-1..4).
    let (t, runner) = proxmox(false);
    let (_, b) = proxmox_plan(&t, runner, false, &["--no-nvram"]);
    assert!(b.problems.is_empty(), "{:?}", b.problems);
    let bt: String = b.stage(Stage::Boot).iter().map(Cmd::shell).collect();
    assert!(bt.contains("unshare --mount --propagation private sh -c 'mount -t tmpfs -o mode=0755 bko-novars /mnt/bko-target/sys/firmware/efi/efivars && exec chroot /mnt/bko-target proxmox-boot-tool init /dev/disk/by-uuid/1234-ABCD'\n"), "{bt}");
    assert!(!bt.contains("umount /mnt/bko-target/sys/firmware/efi/efivars"), "{bt}");
    assert!(!bt.contains("efibootmgr"), "{bt}");
}

/// Proxmox VE 9 on a ZFS mirror: two unmounted ESPs, both formatted with
/// their volume IDs, both initialised in proxmox-boot-uuids' order, one
/// refresh.
#[test]
fn proxmox_mirror_guest() {
    use crate::load::{commands::Cmd, Stage};
    let (t, runner) = proxmox(true);
    let (v, b) = proxmox_plan(&t, runner, true, &[]);
    let pb = v.at(&["boot", "proxmox_boot"]).unwrap();
    assert_eq!(pb.get("uuids").unwrap().compact(), r#"["1234-ABCD","5678-EF01"]"#);
    let devs: Vec<&str> = arr(pb, &["esps"]).iter().map(|e| s(e, &["device"])).collect();
    assert_eq!(devs, vec!["vda2", "vdb2"]);
    assert!(arr(pb, &["esps"]).iter().all(|e| s(e, &["mode"]) == "systemd-boot"));
    assert_eq!(arr(&v, &["boot", "esps"]).len(), 2);
    assert!(b.problems.is_empty(), "{:?}", b.problems);
    let text = |st: Stage| -> String { b.stage(st).iter().map(Cmd::shell).collect() };
    let asm = text(Stage::Assemble);
    assert!(asm.lines().any(|l| l.starts_with("zpool create ") && l.contains("rpool mirror /dev/sda3 /dev/sdb3")), "{asm}");
    let fmt = text(Stage::Format);
    let fats: Vec<&str> = fmt.lines().filter(|l| l.starts_with("mkfs.fat ")).collect();
    assert_eq!(fats.len(), 2, "{fmt}");
    assert!(fats[0].contains("-i 1234ABCD") && fats[0].ends_with("/dev/sda2") && fats[1].contains("-i 5678EF01") && fats[1].ends_with("/dev/sdb2"), "{fats:?}");
    let bt = text(Stage::Boot);
    let a = bt.find("chroot /mnt/bko-target proxmox-boot-tool init /dev/disk/by-uuid/1234-ABCD\n").expect(&bt);
    let c = bt.find("chroot /mnt/bko-target proxmox-boot-tool init /dev/disk/by-uuid/5678-EF01\n").expect(&bt);
    let r = bt.find("chroot /mnt/bko-target proxmox-boot-tool refresh\n").expect(&bt);
    assert!(a < c && c < r, "{bt}");
    assert_eq!(bt.matches("proxmox-boot-tool refresh").count(), 1, "{bt}");
    assert!(bt.contains("cat > /mnt/bko-target/etc/kernel/proxmox-boot-uuids <<'EOF'\n1234-ABCD\n5678-EF01\nEOF\n"), "{bt}");
    assert!(!bt.contains("bootctl install") && !bt.contains("efibootmgr --quiet --create"), "{bt}");
    assert!(b.notes.iter().any(|n| n == "Proxmox: proxmox-boot-tool rebuilt 2 ESPs (systemd-boot); their old content isn't in the archive"), "{:?}", b.notes);
}

/// What load refuses on Proxmox: GRUB's modes (and shim), an ESP in the list
/// that no partition has, a release other than 9; unprivileged, dump mounts
/// nothing and the mode is unknown.
#[test]
fn proxmox_refusals() {
    // GRUB on BIOS: grub/i386-pc on the ESP, no systemd-boot.
    let (t, runner) = proxmox(false);
    let esp = "/run/bko-holt/esp-1234-ABCD";
    std::fs::remove_dir_all(t.p(&format!("{esp}/EFI/systemd"))).unwrap();
    std::fs::remove_dir_all(t.p(&format!("{esp}/loader"))).unwrap();
    t.dir(&format!("{esp}/grub/i386-pc"));
    let (v, b) = proxmox_plan(&t, runner, false, &[]);
    assert_eq!(s(&v, &["boot", "proxmox_boot", "esps", "0", "mode"]), "grub-bios");
    assert!(b.problems.iter().any(|p| p == "Proxmox: ESP 1234-ABCD (vda2) is in proxmox-boot-tool's GRUB mode (grub-bios); bko-holt load supports only its systemd-boot mode (GRUB's isn't tested)"), "{:?}", b.problems);
    let bt: String = b.stage(crate::load::Stage::Boot).iter().map(crate::load::commands::Cmd::shell).collect();
    assert!(bt.contains("chroot /mnt/bko-target proxmox-boot-tool init /dev/disk/by-uuid/1234-ABCD grub\n"), "{bt}");
    // GRUB on UEFI.
    let (t, runner) = proxmox(false);
    std::fs::remove_file(t.p(&format!("{esp}/EFI/systemd/systemd-bootx64.efi"))).unwrap();
    t.file(&format!("{esp}/EFI/proxmox/grubx64.efi"), vec![6u8; 200]);
    let (v, b) = proxmox_plan(&t, runner, false, &[]);
    assert_eq!(s(&v, &["boot", "proxmox_boot", "esps", "0", "mode"]), "grub-efi");
    assert!(b.problems.iter().any(|p| p.contains("GRUB mode (grub-efi)")), "{:?}", b.problems);
    // An ID in the list that nothing has.
    let (t, runner) = proxmox(false);
    t.file("/etc/kernel/proxmox-boot-uuids", "1234-ABCD\nDEAD-BEEF\n");
    let (v, b) = proxmox_plan(&t, runner, false, &[]);
    assert!(v.at(&["boot", "proxmox_boot", "esps", "1", "device"]).unwrap().is_null());
    assert!(arr(&v, &["warnings"]).iter().any(|w| w.as_str() == Some("/etc/kernel/proxmox-boot-uuids lists ESP DEAD-BEEF, but no file system here has that volume ID (proxmox-boot-tool status says the same)")));
    assert_eq!(b.problems, vec!["Proxmox: /etc/kernel/proxmox-boot-uuids lists ESP DEAD-BEEF, which no partition on the source has; proxmox-boot-tool couldn't rebuild it".to_string()]);
    // Proxmox VE 8.
    let (t, runner) = proxmox(false);
    let st = std::fs::read_to_string(t.p("/var/lib/dpkg/status")).unwrap();
    t.file("/var/lib/dpkg/status", st.replace("Version: 9.2.3", "Version: 8.4.1"));
    let (v, b) = proxmox_plan(&t, runner, false, &[]);
    assert_eq!(s(&v, &["os", "product"]), "Proxmox VE 8.4");
    assert_eq!(b.problems, vec!["Proxmox: the source is Proxmox VE 8.4; bko-holt load rebuilds proxmox-boot-tool's ESPs on Proxmox VE 9 only".to_string()]);
    // Not root: no mount, so no mode; load says so.
    let (t, runner) = proxmox(false);
    let sys = Sys::new(t.root(), Box::new(runner), false);
    let os = crate::osrel::read(&sys);
    let v = crate::report::build(&sys, os.as_ref(), "2026-11-04T00:00:00Z");
    let e = v.at(&["boot", "proxmox_boot", "esps", "0"]).unwrap();
    assert!(e.get("mode").unwrap().is_null() && e.get("contents").unwrap().is_null(), "{}", e.compact());
    // Without proxmox-kernel-helper the list is nobody's.
    let (t, runner) = proxmox(false);
    let st = std::fs::read_to_string(t.p("/var/lib/dpkg/status")).unwrap();
    t.file("/var/lib/dpkg/status", st.replace("Package: proxmox-kernel-helper", "Package: proxmox-kernel-helper-gone"));
    let v = run(&t, true, runner);
    assert!(v.at(&["boot", "proxmox_boot"]).is_none());
    assert!(arr(&v, &["boot", "esps"]).is_empty());
}

const GUIX_OS_RELEASE: &str = "NAME=\"Guix System\"\nID=guix\nPRETTY_NAME=\"Guix System\"\nLOGO=guix-icon\nHOME_URL=\"https://guix.gnu.org\"\n";
/// The ext4 root (ext::sample::image's UUID) and the btrfs root's label.
const GUIX_ROOT: &str = "10111213-1415-1617-1819-1a1b1c1d1e1f";
const GUIX_LABEL: &str = "guixroot";
const GUIX_ESP: &str = "9a1c0000-0000-4000-8000-000000000001";

/// A Guix System's store and boot files in `t`, as `guix system init` and
/// one `reconfigure` leave them (docs/design/guix.md): two generations,
/// system-1-link and system-2-link (the current one), each a store item
/// with its boot parameters (`root` its root device: `(uuid ext4
/// #vu8(...))` or `(file-system-label "L")`) and a profile whose manifest
/// lists `pkgs`; each generation's kernel and initrd in the store;
/// grub.cfg as 1.5.0 writes it (the installation ISO's, read on Chopin): a
/// top-level `search`, the serial terminal, a commented-out search for the locales, entries
/// that `search` again and boot with root=`arg`, gnu.system= and gnu.load=
/// (the store's paths under `prefix`, a btrfs subvolume's); `loader` the
/// parameters' bootloader-name.  Generation 2's parameters are 1.5.0's
/// (version 1, only the configuration's kernel arguments); generation 1's
/// an older Guix's (version 0, with --root=, --system= and --load=).
fn guix_store(t: &Tree, root: &str, arg: &str, search: &str, prefix: &str, loader: &str, pkgs: &[(&str, &str, &str)]) {
    let h = |c: char| c.to_string().repeat(32);
    let manifest: String = pkgs.iter().map(|(n, v, p)| format!("     (\"{n}\" \"{v}\" \"out\" \"{p}\" (propagated-inputs ()) (search-paths ()))\n")).collect();
    let mut cfg = format!("# This file was generated from your Guix configuration.  Any changes\n# will be lost upon reboot or reconfiguration.\n\n{search}\nserial --unit=0 --speed=115200\nterminal_input console serial\nterminal_output console serial\n# search --file --set /gnu/store/{}-grub-locales/en@quot.mo\n", h('l'));
    for (n, k, c) in [(2u32, "6.17.9", 't'), (1, "6.17.7", 's')] {
        let sys = format!("/gnu/store/{}-system", h(c));
        let kernel = format!("/gnu/store/{}-linux-libre-{k}/bzImage", h(if n == 2 { 'k' } else { 'j' }));
        let initrd = format!("/gnu/store/{}-raw-initrd/initrd.cpio.gz", h(if n == 2 { 'i' } else { 'h' }));
        let prof = format!("/gnu/store/{}-profile", h(if n == 2 { 'p' } else { 'q' }));
        let (version, args) = if n == 2 { (1, "\"console=tty0\" \"console=ttyS0,115200n8\"".to_string()) } else { (0, format!("\"--root={arg}\" \"--system={sys}\" \"--load={sys}/boot\" \"console=ttyS0,115200n8\"")) };
        t.file(&kernel, "k")
            .file(&initrd, "i")
            .file(&format!("{prof}/manifest"), format!(";; This file was automatically generated.\n(manifest\n  (version 4)\n  (packages\n    (\n{manifest}    )))\n"))
            .file(
                &format!("{sys}/parameters"),
                format!("(boot-parameters (version {version}) (label \"GNU with Linux-Libre {k}\") (root-device {root}) (kernel \"{kernel}\") (kernel-arguments ({args})) (initrd \"{initrd}\") (multiboot-modules ()) (locale \"en_US.utf8\") (store (device {root}) (mount-point \"/\") (directory-prefix #f) (crypto-devices ())) (bootloader-name {loader}) (bootloader-menu-entries ()))\n"),
            )
            .symlink(&format!("{sys}/profile"), &prof)
            .symlink(&format!("{sys}/kernel"), kernel.trim_end_matches("/bzImage"))
            .symlink(&format!("/var/guix/profiles/system-{n}-link"), &sys);
        let entry = format!("  {search}\n  linux {prefix}{kernel} root={arg} gnu.system={sys} gnu.load={sys}/boot console=tty0 console=ttyS0,115200n8\n  initrd {prefix}{initrd}\n}}\n");
        if n == 2 {
            cfg.push_str(&format!("menuentry \"GNU with Linux-Libre {k}\" {{\n{entry}submenu \"GNU system, old configurations...\" {{\n"));
        } else {
            cfg.push_str(&format!("menuentry \"GNU with Linux-Libre {k} (#1, 2026-11-14 10:00)\" {{\n{entry}}}\n"));
        }
    }
    t.symlink("/var/guix/profiles/system", "system-2-link")
        .file("/var/guix/db/db.sqlite", "SQLite format 3")
        .file("/etc/os-release", GUIX_OS_RELEASE)
        .file("/boot/grub/grub.cfg", cfg)
        .file("/proc/sys/kernel/osrelease", "6.17.9-gnu\n")
        .file("/proc/cmdline", format!("BOOT_IMAGE={prefix}/gnu/store/{}-linux-libre-6.17.9/bzImage root={arg} gnu.system=/gnu/store/{}-system gnu.load=/gnu/store/{}-system/boot console=ttyS0,115200n8\n", h('k'), h('t'), h('t')));
}

/// guix15 in small: UEFI, GPT with the ESP at /boot/efi and / ext4 by
/// UUID; grub-efi-bootloader's EFI/Guix/grubx64.efi from NVRAM; the
/// store's read-only bind mount over /gnu/store.
fn guix_uefi() -> Tree {
    use crate::efi::GLOBAL;
    let t = Tree::new("scn-guix-uefi");
    let mut img = vec![0u8; 64 * MIB];
    write_gpt(&mut img, 512, &GptSpec { disk_guid: "9a1c0000-0000-4000-8000-00000000d15c", parts: vec![(ESP, GUIX_ESP, 2048, 73727, 0, ""), (LINUX, "9a1c0000-0000-4000-8000-000000000002", 73728, 131038, 0, "")] });
    put(&mut img, 2048, &fat::sample::fat32(71680, 0x1234_abcd, ""));
    put(&mut img, 73728, &ext::sample::image(0x4 | 0x1000, 0x2 | 0x40 | 0x80 | 0x200, 0x1 | 0x400, ""));
    let var = |n: &str| format!("/sys/firmware/efi/efivars/{n}-{GLOBAL}");
    t.disk("vda", "254:0", &img, 512)
        .partition("vda", "vda1", "254:1", 1, 2048, 71680, &img)
        .partition("vda", "vda2", "254:2", 2, 73728, 57311, &img)
        .symlink(&format!("/dev/disk/by-partuuid/{GUIX_ESP}"), "../../vda1")
        .file("/sys/firmware/efi/fw_platform_size", "64\n")
        .file(&var("Boot0001"), efi_load_option("Guix", 1, 2048, 71680, GUIX_ESP, "\\EFI\\Guix\\grubx64.efi"))
        .file(&var("BootOrder"), [7u8, 0, 0, 0, 1, 0])
        .file(&var("BootCurrent"), [6u8, 0, 0, 0, 1, 0])
        .file(&var("SecureBoot"), [6u8, 0, 0, 0, 0])
        .file(
            "/proc/self/mountinfo",
            "26 1 254:2 / / rw,relatime shared:1 - ext4 /dev/vda2 rw\n27 26 254:1 / /boot/efi rw,relatime shared:2 - vfat /dev/vda1 rw,fmask=0022\n28 26 254:2 /gnu/store /gnu/store ro,relatime shared:3 - ext4 /dev/vda2 rw\n",
        )
        .file("/etc/fstab", format!("# This file was generated from your Guix configuration.  Any changes\n# will be lost upon reboot or reconfiguration.\n\nUUID={GUIX_ROOT}\t/\text4\tdefaults\t0\t1\nUUID=1234-ABCD\t/boot/efi\tvfat\tdefaults\t0\t2\n"))
        .file("/boot/grub/x86_64-efi/normal.mod", "x")
        .file("/boot/efi/EFI/Guix/grubx64.efi", vec![2u8; 400])
        .dir("/gnu/store/gggggggggggggggggggggggggggggggg-grub-efi-2.12/lib/grub/x86_64-efi")
        .file("/proc/sys/kernel/hostname", "guix15\n");
    guix_store(
        &t,
        "(uuid ext4 #vu8(16 17 18 19 20 21 22 23 24 25 26 27 28 29 30 31))",
        GUIX_ROOT,
        &format!("search --fs-uuid --set {GUIX_ROOT}"),
        "",
        "grub-efi",
        &[("e2fsprogs", "1.47.2", "/gnu/store/eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee-e2fsprogs-1.47.2"), ("shepherd", "1.0.9", "/gnu/store/dddddddddddddddddddddddddddddddd-shepherd-1.0.9")],
    );
    t
}

/// guix15-bios in small, as the real guest is (Guix 1.5.0): BIOS, GPT with
/// a BIOS boot partition (GRUB's core; boot.img in the protective MBR), a
/// swap partition (by UUID in config.scm only, not in fstab) and one btrfs
/// by label, subvolumes @ (/), @gnu (/gnu, compressed; the store's
/// read-only bind mount over /gnu/store) and @home; no GRUB in the store
/// (guix system init ran the installer's), grub-install's boot.img and
/// core.img in /boot/grub/i386-pc; grub.cfg by label with a serial
/// terminal, its store paths under /@gnu, root= the bare label.
fn guix_bios() -> Tree {
    const SWAP: &str = "0657fd6d-a4ab-43c4-84e5-0933c84b4f4f";
    let t = Tree::new("scn-guix-bios");
    let mut img = vec![0u8; 64 * MIB];
    write_gpt(
        &mut img,
        512,
        &GptSpec {
            disk_guid: "9a1c0000-0000-4000-8000-00000000b105",
            parts: vec![(BIOS_BOOT, "9a1c0000-0000-4000-8000-000000000011", 2048, 6143, 0, ""), (SWAP, "9a1c0000-0000-4000-8000-000000000013", 6144, 14335, 0, "swap"), (LINUX, "9a1c0000-0000-4000-8000-000000000012", 14336, 131038, 0, "")],
        },
    );
    img[..440].copy_from_slice(&grub::grub2_boot_img(2048)[..440]);
    put(&mut img, 2048, &grub::grub2_diskboot_img());
    put(&mut img, 6144, &misc::sample::swap(1024, "swap"));
    put(&mut img, 14336, &btrfs::sample::image(GUIX_LABEL, 0x1 | 0x20 | 0x40 | 0x100 | 0x200));
    let mut mountinfo = String::new();
    let mut fstab = String::from("# This file was generated from your Guix configuration.  Any changes\n# will be lost upon reboot or reconfiguration.\n\n");
    for (i, (sv, at, opt)) in GUIX_SUBVOLS.iter().enumerate() {
        let parent = if *at == "/" { 1 } else { 26 };
        let kopt = if opt.is_empty() { String::new() } else { format!("{opt}:3,") };
        mountinfo.push_str(&format!("{} {parent} 0:25 /{sv} {at} rw,noatime shared:{} - btrfs /dev/vda3 rw,{kopt}space_cache=v2,subvolid={},subvol=/{sv}\n", 26 + i, i + 1, 256 + i));
        fstab.push_str(&format!("LABEL={GUIX_LABEL}\t{at}\tbtrfs\tsubvol={sv}{}\t\n", if opt.is_empty() { String::new() } else { format!(",{opt}") }));
    }
    mountinfo.push_str("29 27 0:25 /@gnu/store /gnu/store ro,noatime shared:4 - btrfs /dev/vda3 rw,compress=zstd:3,space_cache=v2,subvolid=257,subvol=/@gnu\n");
    t.disk("vda", "254:0", &img, 512)
        .partition("vda", "vda1", "254:1", 1, 2048, 4096, &img)
        .partition("vda", "vda2", "254:2", 2, 6144, 8192, &img)
        .partition("vda", "vda3", "254:3", 3, 14336, 116703, &img)
        .file("/proc/self/mountinfo", mountinfo)
        .file("/proc/swaps", "Filename\t\t\t\tType\t\tSize\t\tUsed\t\tPriority\n/dev/vda2                               partition\t4096\t\t0\t\t-2\n")
        .file("/etc/fstab", fstab)
        .file("/boot/grub/i386-pc/normal.mod", "x")
        .file("/boot/grub/i386-pc/boot.img", vec![0u8; 512])
        .file("/boot/grub/i386-pc/core.img", vec![0u8; 73151])
        .dir("/gnu/store/llllllllllllllllllllllllllllllll-grub-locales")
        .file("/gnu/store/gggggggggggggggggggggggggggggggg-grub-image.png", "png")
        .file("/proc/sys/kernel/hostname", "guix15-bios\n");
    guix_store(
        &t,
        &format!("(file-system-label \"{GUIX_LABEL}\")"),
        GUIX_LABEL,
        &format!("search --label --set {GUIX_LABEL}"),
        "/@gnu",
        "grub",
        &[("btrfs-progs", "6.17", "/gnu/store/cccccccccccccccccccccccccccccccc-btrfs-progs-6.17"), ("shepherd", "1.0.9", "/gnu/store/dddddddddddddddddddddddddddddddd-shepherd-1.0.9")],
    );
    t
}

/// guix15-bios's subvolumes in fstab's order, where it mounts them, and
/// their options.
const GUIX_SUBVOLS: [(&str, &str, &str); 3] = [("@", "/", ""), ("@gnu", "/gnu", "compress=zstd"), ("@home", "/home", "")];

/// What btrfs answers on guix15-bios (from /run/current-system/profile/sbin).
fn guix_bios_runner() -> FakeRunner {
    let mut list = String::new();
    for (i, (sv, _, _)) in GUIX_SUBVOLS.iter().enumerate() {
        list.push_str(&format!("ID {} gen 90 parent 5 top level 5 parent_uuid - uuid 9a1cb7f5-0000-4000-8000-00000000025{i} path <FS_TREE>/{sv}\n", 256 + i));
    }
    let mut r = FakeRunner::default().with("btrfs subvolume list -a -p -u -q /", &list).with("btrfs subvolume get-default /", "ID 5 (FS_TREE)\n").with("btrfs subvolume list -a -r /", "");
    for (_, at, opt) in GUIX_SUBVOLS {
        r = r.with(&format!("btrfs property get -t s {at}"), "ro=false\n").with(&format!("btrfs property get -t i {at}"), if opt.is_empty() { "" } else { "compression=zstd:3\n" });
    }
    r
}

/// A Guix dump planned, vda going to vdb, with load's `args`.
fn guix_plan(v: &Value, args: &[&str]) -> crate::load::commands::Built {
    use crate::load::{commands, layout::Layout, target::Mapping, Parsed};
    let l = Layout::from_json(v.clone()).unwrap();
    let mut a = vec!["l.json".to_string()];
    a.extend(args.iter().map(|x| x.to_string()));
    let Ok(Parsed::Run(o)) = crate::load::parse_args(&a) else { unreachable!() };
    commands::build(&l, &o, &Mapping { pairs: vec![("vda".into(), "vdb".into())], ..Mapping::default() })
}

/// guix15 read back and planned (docs/design/guix.md, GX2): supported by
/// ID=guix alone; the packages from the system profile's manifest; both
/// generations' boot parameters and grub.cfg's searches in boot.store.
/// load rebuilds nothing, runs nothing in the chroot, makes the NVRAM
/// entry (and, with --removable, the fallback copy), and says why.
#[test]
fn guix_uefi_guest() {
    use crate::load::{commands::Cmd, Stage};
    let v = run(&guix_uefi(), true, FakeRunner::default());
    assert_eq!(s(&v, &["os", "id"]), "guix");
    assert!(v.at(&["os", "version_id"]).unwrap().is_null());
    assert_eq!(s(&v, &["os", "pretty_name"]), "Guix System");
    assert_eq!(s(&v, &["firmware", "mode"]), "uefi");
    assert_eq!(s(&v, &["boot", "loader", "name"]), "grub");
    assert_eq!(s(&v, &["boot", "packages", "shepherd"]), "1.0.9");
    assert_eq!(s(&v, &["boot", "packages", "e2fsprogs"]), "1.47.2");
    assert!(arr(&v, &["boot", "kernels", "installed"]).is_empty());
    let st = v.at(&["boot", "store"]).unwrap();
    assert_eq!(s(st, &["system"]), "guix");
    assert_eq!(u(st, &["current"]), 2);
    let g = arr(st, &["generations"]);
    assert_eq!(g.iter().map(|x| u(x, &["number"])).collect::<Vec<_>>(), vec![1, 2]);
    assert_eq!(s(&g[1], &["root_device", "kind"]), "uuid");
    assert_eq!(s(&g[1], &["root_device", "value"]), GUIX_ROOT);
    assert_eq!(s(&g[1], &["bootloader_name"]), "grub-efi");
    assert_eq!(s(&g[1], &["title"]), "GNU with Linux-Libre 6.17.9");
    assert!(s(&g[0], &["kernel"]).ends_with("-linux-libre-6.17.7/bzImage"));
    // 1.5.0's: only the configuration's arguments; the older one's --root=.
    assert_eq!(arr(&g[1], &["kernel_arguments"]).iter().filter_map(Value::as_str).collect::<Vec<_>>(), vec!["console=tty0", "console=ttyS0,115200n8"]);
    assert_eq!(s(&g[1], &["version"]), "1");
    assert_eq!(s(&g[0], &["kernel_arguments", "0"]), format!("--root={GUIX_ROOT}"));
    assert_eq!(s(&g[1], &["store_device", "value"]), GUIX_ROOT);
    assert!(st.get("grub").unwrap().is_null());
    assert_eq!(s(st, &["grub_cfg", "searches", "0", "kind"]), "fs-uuid");
    assert_eq!(s(st, &["grub_cfg", "searches", "0", "value"]), GUIX_ROOT);
    assert_eq!(arr(st, &["grub_cfg", "searches"]).len(), 1);
    assert_eq!(s(st, &["grub_cfg", "roots", "0"]), GUIX_ROOT);
    // The read-only store bind mount is an alias of /, not a file system
    // of its own or a backup source.
    let m = arr(&v, &["storage", "mounts"]);
    assert!(m.iter().filter(|m| s(m, &["target"]) == "/gnu/store").all(|m| m.get("alias_of").and_then(Value::as_str) == Some("/")), "{}", v.at(&["storage", "mounts"]).unwrap().compact());
    assert!(!arr(&v, &["storage", "backup_sources"]).iter().any(|x| s(x, &["path"]) == "/gnu/store"));

    let b = guix_plan(&v, &[]);
    assert!(b.problems.is_empty(), "{:?}", b.problems);
    let bt: String = b.stage(Stage::Boot).iter().map(Cmd::shell).collect();
    for no in ["grub-install", "grub-mkconfig", "update-grub", "update-initramfs", "dracut", "mkinitcpio", "setfiles", "BOOTX64.EFI"] {
        assert!(!bt.contains(no), "{no}: {bt}");
    }
    assert!(!b.stage(Stage::Boot).iter().any(|c| c.argv.first().map(String::as_str) == Some("chroot") && c.argv.get(2).is_some_and(|x| x.contains("grub"))), "{bt}");
    assert!(bt.contains("efibootmgr --quiet --create --disk /dev/vdb --part 1 --label Guix --loader '\\EFI\\Guix\\grubx64.efi'\n"), "{bt}");
    assert!(b.notes.iter().any(|n| n == "Guix System: each generation's kernel and initrd are store items, restored with /, where grub.cfg names them; nothing to rebuild"), "{:?}", b.notes);
    assert!(b.notes.iter().any(|n| n.starts_with("Guix System: UEFI GRUB's EFI image came back with the ESP")), "{:?}", b.notes);
    assert!(!b.notes.iter().any(|n| n.starts_with("NixOS")), "{:?}", b.notes);
    // --removable: the ESP's GRUB at the fallback path too, outside the chroot.
    let bt: String = guix_plan(&v, &["--removable"]).stage(Stage::Boot).iter().map(Cmd::shell).collect();
    assert!(bt.contains("cp /mnt/bko-target/boot/efi/EFI/Guix/grubx64.efi /mnt/bko-target/boot/efi/EFI/BOOT/BOOTX64.EFI"), "{bt}");
    assert!(!bt.contains("grub-install"), "{bt}");

    // grub.cfg searching for a file system the layout doesn't make, and a
    // generation booting a root it doesn't have, are problems.
    let mut w = v.clone();
    set_at(&mut w, &["boot", "store", "grub_cfg", "searches"], crate::json::parse(r#"[{"kind": "fs-uuid", "value": "deadbeef-0000-4000-8000-000000000000"}, {"kind": "file", "value": "/gnu/store/x/bzImage"}]"#).unwrap());
    let p = guix_plan(&w, &[]).problems;
    assert_eq!(p, vec!["Guix System: /boot/grub/grub.cfg searches --fs-uuid deadbeef-0000-4000-8000-000000000000, which no file system in the layout has; the restored GRUB couldn't find it"], "{p:?}");
    let mut w = v.clone();
    set_at(&mut w, &["boot", "store", "grub_cfg", "roots"], vec!["no-such-label", "/dev/vda2"].into());
    let Some(Value::Arr(gens)) = w.at(&["boot", "store", "generations"]).cloned() else { panic!() };
    let mut g0 = gens[0].clone();
    set_at(&mut g0, &["kernel_arguments"], vec!["--root=other-label", "--system=/gnu/store/x"].into());
    set_at(&mut w, &["boot", "store", "generations"], Value::Arr(vec![g0, gens[1].clone()]));
    let p = guix_plan(&w, &[]).problems;
    assert_eq!(
        p,
        vec![
            "Guix System: /boot/grub/grub.cfg boots with root no-such-label, which no file system in the layout has as its UUID or label; the restored initrd couldn't find its root",
            "Guix System: generation 1 boots with root other-label, which no file system in the layout has as its UUID or label; its initrd couldn't find its root",
        ],
        "{p:?}"
    );
    // 1.5.0's root: the generation's root device.
    let mut w = v.clone();
    let Some(Value::Arr(gens)) = w.at(&["boot", "store", "generations"]).cloned() else { panic!() };
    let mut g1 = gens[1].clone();
    set_at(&mut g1, &["root_device", "value"], "deadbeef-0000-4000-8000-000000000001".into());
    set_at(&mut w, &["boot", "store", "generations"], Value::Arr(vec![gens[0].clone(), g1]));
    assert_eq!(guix_plan(&w, &[]).problems, vec!["Guix System: generation 2's root device is UUID deadbeef-0000-4000-8000-000000000001, which no file system in the layout has; its initrd couldn't find its root"]);
}

/// guix15-bios read back and planned: the subvolume list (btrfs from the
/// profile), no GRUB in the store but grub-install's images in
/// /boot/grub/i386-pc, the swap partition fstab doesn't name; load makes
/// the three subvolumes and the swap, and writes the source's own boot.img
/// and core.img once per MBR with the rescue's grub-bios-setup, keeps
/// grub.cfg, and says why.  Without the images, a problem.
#[test]
fn guix_bios_guest() {
    use crate::load::{commands::Cmd, Stage};
    let v = run(&guix_bios(), true, guix_bios_runner());
    assert_eq!(s(&v, &["os", "id"]), "guix");
    assert_eq!(s(&v, &["firmware", "mode"]), "bios");
    assert_eq!(s(&v, &["boot", "loader", "name"]), "grub");
    assert_eq!(s(&v, &["boot", "packages", "btrfs-progs"]), "6.17");
    assert_eq!(s(&v, &["storage", "disks", "0", "boot_code", "kind"]), "grub2");
    let st = v.at(&["boot", "store"]).unwrap();
    assert!(st.get("grub").unwrap().is_null());
    assert_eq!(st.get("boot_img"), Some(&Value::Bool(true)));
    assert_eq!(st.get("core_img"), Some(&Value::Bool(true)));
    for g in 0..2 {
        let g = &arr(st, &["generations"])[g];
        assert_eq!(s(g, &["root_device", "kind"]), "label");
        assert_eq!(s(g, &["root_device", "value"]), GUIX_LABEL);
        assert_eq!(s(g, &["bootloader_name"]), "grub");
    }
    assert_eq!(s(st, &["grub_cfg", "searches", "0", "kind"]), "label");
    assert_eq!(arr(st, &["grub_cfg", "searches"]).len(), 1);
    assert_eq!(s(st, &["grub_cfg", "roots", "0"]), GUIX_LABEL);
    assert_eq!(arr(&v, &["storage", "btrfs", "0", "subvolumes"]).len(), 3);
    assert!(arr(&v, &["warnings"]).iter().all(|w| !w.as_str().unwrap_or("").contains("subvolume list")), "{:?}", v.get("warnings"));
    let m = arr(&v, &["storage", "mounts"]);
    assert!(m.iter().filter(|m| s(m, &["target"]) == "/gnu/store").all(|m| m.get("alias_of").is_some()), "{}", v.at(&["storage", "mounts"]).unwrap().compact());

    let b = guix_plan(&v, &[]);
    assert!(b.problems.is_empty(), "{:?}", b.problems);
    let f: String = b.stage(Stage::Format).iter().map(Cmd::shell).collect();
    for sv in ["@", "@home", "@gnu"] {
        assert!(f.contains(&format!("btrfs subvolume create /run/bko-holt/btrfs/b0b1b2b3-b4b5-b6b7-b8b9-babbbcbdbebf/{sv}\n")), "{sv}: {f}");
    }
    assert_eq!(f.matches("btrfs subvolume create").count(), 3, "{f}");
    assert!(f.contains(&format!("mkfs.btrfs")) && f.contains(&format!("-L {GUIX_LABEL}")), "{f}");
    // The swap partition fstab doesn't name: made from its partition.
    assert!(f.lines().any(|l| l.starts_with("mkswap ") && l.contains("-L swap") && l.ends_with(" /dev/vdb2")), "{f}");
    let mnt: String = b.stage(Stage::Mount).iter().map(Cmd::shell).collect();
    assert!(mnt.contains("/mnt/bko-target/gnu\n") && !mnt.contains("/mnt/bko-target/gnu/store"), "{mnt}");
    let boot = b.stage(Stage::Boot);
    let setups: Vec<&Cmd> = boot.iter().filter(|c| c.argv.iter().any(|a| a.contains("grub-bios-setup"))).collect();
    assert_eq!(setups.len(), 1, "{}", boot.iter().map(Cmd::shell).collect::<String>());
    assert_eq!(
        setups[0].argv,
        vec![
            "sh".to_string(),
            "-c".into(),
            "d=/mnt/bko-target/boot/grub/i386-pc; [ -f \"$d/boot.img\" ] && [ -f \"$d/core.img\" ] || { echo \"$d: no boot.img or core.img (the source's, restored with /boot/grub) to write to /dev/vdb\" >&2; exit 1; }; grub-bios-setup --directory=\"$d\" /dev/vdb".into()
        ]
    );
    assert_eq!(setups[0].why.as_deref(), Some("Guix System's own boot.img and core.img, restored in /boot/grub/i386-pc; the rescue's grub-bios-setup writes them to the new disk"));
    let bt: String = boot.iter().map(Cmd::shell).collect();
    for no in ["grub-install", "gnu/store", "grub-mkconfig", "update-grub", "update-initramfs", "dracut", "efibootmgr", "setfiles"] {
        assert!(!bt.contains(no), "{no}: {bt}");
    }
    assert!(b.notes.iter().any(|n| n == "Guix System: each generation's kernel and initrd are store items, restored with /, where grub.cfg names them; nothing to rebuild"), "{:?}", b.notes);
    assert!(b.notes.iter().any(|n| n.starts_with("Guix System: its store holds no GRUB to install with")), "{:?}", b.notes);
    // A source without grub-install's images: a problem.
    let mut w = v.clone();
    set_at(&mut w, &["boot", "store", "core_img"], false.into());
    assert_eq!(
        guix_plan(&w, &[]).problems,
        vec!["Guix System: the source's /boot/grub/i386-pc has no core.img, which grub-install leaves there and bko-holt load writes to the new disk's MBR and BIOS boot partition (its store has no GRUB to install with)"]
    );
    // A label no file system has: a problem.
    let mut w = v.clone();
    set_at(&mut w, &["boot", "store", "grub_cfg", "searches"], crate::json::parse(r#"[{"kind": "label", "value": "other-root"}]"#).unwrap());
    assert_eq!(guix_plan(&w, &[]).problems, vec!["Guix System: /boot/grub/grub.cfg searches --label other-root, which no file system in the layout has; the restored GRUB couldn't find it"]);
}

/// Every scenario's dump, by name, for tests that must hold over all of
/// them (load's fit.rs).
pub fn dumps() -> Vec<(&'static str, Value)> {
    let (zfs, runner) = zfs_root_tree();
    vec![
        ("bios_btrfs", run(&bios_btrfs(), true, bios_btrfs_runner())),
        ("uefi_shim", run(&uefi_shim(), true, FakeRunner::default())),
        ("luks_lvm", run(&luks_lvm_tree(), true, FakeRunner::default())),
        ("thin_lvm", run(&thin_lvm_tree(), true, thin_lvm_runner())),
        ("vanilla", run(&vanilla_tree(true), true, vanilla_runner())),
        ("vanilla_fresh", run(&vanilla_tree(false), true, vanilla_runner())),
        ("md_raid1", run(&md_raid1_tree(), true, FakeRunner::default())),
        ("zfs_root", run(&zfs, true, runner)),
        ("whole_disk_fs", run(&misc_tree(), true, FakeRunner::default())),
        ("kinoite_ostree", run(&kinoite_ostree(), true, kinoite_runner())),
        ("gentoo_thinkpad", run(&gentoo_thinkpad(), true, FakeRunner::default())),
        ("pikaos", run(&pikaos(), true, FakeRunner::default())),
        ("biglinux", run(&biglinux(), true, biglinux_runner())),
        ("anduinos", run(&anduinos(), true, anduinos_runner())),
        ("kali", run(&kali(), true, FakeRunner::default())),
        ("elementary", run(&elementary(false), true, FakeRunner::default())),
        ("elementary_luks", run(&elementary(true), true, FakeRunner::default())),
        ("proxmox", { let (t, r) = proxmox(false); run(&t, true, r) }),
        ("proxmox_mirror", { let (t, r) = proxmox(true); run(&t, true, r) }),
        ("guix_uefi", run(&guix_uefi(), true, FakeRunner::default())),
        ("guix_bios", run(&guix_bios(), true, guix_bios_runner())),
        ("truenas", { let (t, r) = truenas(false); run(&t, true, r) }),
        ("truenas_mirror", { let (t, r) = truenas(true); run(&t, true, r) }),
        ("zfsbootmenu", { let (t, r) = zfsbootmenu(false); run(&t, true, r) }),
        ("zfsbootmenu_enc", { let (t, r) = zfsbootmenu(true); run(&t, true, r) }),
    ]
}

// ---- illumos: OpenIndiana -------------------------------------------------

/// OpenIndiana Hipster 2026.10 as its text installer leaves it (the oi2610
/// guest, docs/design/openindiana.md), a `pkg update` later: the whole disk
/// from `zpool create -B` (slice 0 the ESP, unmounted; slice 1 rpool; slice
/// 8 reserved), two boot environments (openindiana, the installer's, and
/// openindiana-1, the booted bootfs, each with a /var child at
/// canmount=noauto), the swap and dump zvols, rpool/export/home/tester;
/// no os-release, only /etc/release.  `uefi` says whether prtconf shows
/// efi-systab.  illumos' own report reads it (crate::other::illumos), not
/// Linux's: a FakeRunner answers diskinfo, prtconf, zpool, zfs, beadm and
/// bootadm.
fn openindiana(uefi: bool) -> (Tree, FakeRunner) {
    const SOLARIS_ZFS: &str = "6a898cc3-1dd2-11b2-99a6-080020736631";
    const SOLARIS_RESERVED: &str = "6a945a3b-1dd2-11b2-99a6-080020736631";
    const NONE: &str = "00000000-0000-0000-0000-000000000000";
    let t = Tree::new("scn-openindiana");
    let mut img = vec![0u8; 64 * MIB];
    let (esp, pool, resv) = ((256u64, 16639u64), (16640u64, 114654u64), (114655u64, 131038u64));
    let mut parts = vec![(ESP, "01d1a000-0000-4000-8000-000000000002", esp.0, esp.1, 0, "loader"), (SOLARIS_ZFS, "01d1a000-0000-4000-8000-000000000003", pool.0, pool.1, 0, "zfs")];
    parts.extend((3..9).map(|_| (NONE, NONE, 0, 0, 0, "")));
    parts.push((SOLARIS_RESERVED, "01d1a000-0000-4000-8000-000000000004", resv.0, resv.1, 0, ""));
    write_gpt(&mut img, 512, &GptSpec { disk_guid: "01d1a000-0000-4000-8000-000000000001", parts });
    put(&mut img, esp.0 as usize, &fat::sample::fat32((esp.1 - esp.0 + 1) as u32, 0, ""));
    let cfg = crate::json::parse(
        r#"{"version": 5000, "name": "rpool", "state": 0, "txg": 4, "pool_guid": 4242, "hostname": "", "top_guid": 2, "guid": 2,
            "vdev_children": 1, "vdev_tree": {"type": "disk", "id": 0, "guid": 2, "path": "/dev/dsk/c3t0d0s1", "whole_disk": 1, "ashift": 12},
            "features_for_read": {"com.delphix:hole_birth": true, "com.delphix:embedded_data": true}}"#,
    )
    .unwrap();
    put(&mut img, pool.0 as usize, &zfs::pack::vdev(((pool.1 - pool.0 + 1) * 512) as usize, &cfg));
    t.file("/dev/rdsk/c3t0d0p0", &img)
        .file("/etc/release", "             OpenIndiana Hipster 2026.10 (powered by illumos)\n        OpenIndiana Project, part of The Illumos Foundation (C) 2010-2026\n                        Use is subject to license terms.\n                           Assembled 30 October 2026\n")
        .file("/etc/vfstab", "#device\t\tdevice\t\tmount\t\tFS\tfsck\tmount\tmount\n/devices\t-\t\t/devices\tdevfs\t-\tno\t-\n/proc\t\t-\t\t/proc\t\tproc\t-\tno\t-\n/dev/zvol/dsk/rpool/swap\t-\t-\tswap\t-\tno\t-\n")
        .file(
            "/etc/mnttab",
            "rpool/ROOT/openindiana-1\t/\tzfs\tdev=4010002\t0\n/devices\t/devices\tdevfs\tdev=8900000\t0\n\
             rpool/ROOT/openindiana-1/var\t/var\tzfs\trw,devices,setuid,nonbmand,exec,xattr,atime,dev=4010003\t0\n\
             swap\t/tmp\ttmpfs\txattr,dev=8b40002\t0\n\
             rpool/export\t/export\tzfs\trw,devices,setuid,nonbmand,exec,xattr,atime,dev=4010004\t0\n\
             rpool/export/home\t/export/home\tzfs\trw,devices,setuid,nonbmand,exec,xattr,atime,dev=4010005\t0\n\
             rpool/export/home/tester\t/export/home/tester\tzfs\trw,devices,setuid,nonbmand,exec,xattr,atime,dev=4010006\t0\n\
             rpool\t/rpool\tzfs\trw,devices,setuid,nonbmand,exec,xattr,atime,dev=4010007\t0\n",
        );
    // zfs list's columns (report.rs): name, type, used, available,
    // referenced, mountpoint, canmount, mounted, the encryption five,
    // origin, compression, recordsize, volsize, volblocksize, and ten more.
    let row = |name: &str, ty: &str, mp: &str, canmount: &str, mounted: &str, origin: &str, volsize: &str, vbs: &str| {
        let rs = if ty == "volume" { "-" } else { "131072" };
        format!("{name}\t{ty}\t1000\t2000\t96\t{mp}\t{canmount}\t{mounted}\toff\t-\tnone\tnone\t-\t{origin}\ton\t{rs}\t{volsize}\t{vbs}\t-\ton\t-\tnone\toff\tsensitive\ton\t-\t1\t-\n")
    };
    let fs = |name: &str, mp: &str, canmount: &str, mounted: &str| row(name, "filesystem", mp, canmount, mounted, "-", "-", "-");
    let list = [
        fs("rpool", "/rpool", "on", "yes"),
        fs("rpool/ROOT", "legacy", "off", "no"),
        fs("rpool/ROOT/openindiana", "/", "noauto", "no"),
        fs("rpool/ROOT/openindiana/var", "/var", "noauto", "no"),
        row("rpool/ROOT/openindiana-1", "filesystem", "/", "noauto", "yes", "rpool/ROOT/openindiana@2026-11-14-10:00:00", "-", "-"),
        row("rpool/ROOT/openindiana-1/var", "filesystem", "/var", "noauto", "yes", "rpool/ROOT/openindiana/var@2026-11-14-10:00:00", "-", "-"),
        row("rpool/dump", "volume", "-", "-", "-", "-", "1610612736", "131072"),
        fs("rpool/export", "/export", "on", "yes"),
        fs("rpool/export/home", "/export/home", "on", "yes"),
        fs("rpool/export/home/tester", "/export/home/tester", "on", "yes"),
        row("rpool/swap", "volume", "-", "-", "-", "-", "1073741824", "4096"),
    ]
    .concat();
    let local = "rpool/ROOT\tmountpoint\tlegacy\tlocal\nrpool/ROOT\tcanmount\toff\tlocal\n\
                 rpool/ROOT/openindiana\tmountpoint\t/\tlocal\nrpool/ROOT/openindiana\tcanmount\tnoauto\tlocal\nrpool/ROOT/openindiana\torg.opensolaris.libbe:uuid\t01d1a000-0000-4000-8000-000000000005\tlocal\n\
                 rpool/ROOT/openindiana/var\tcanmount\tnoauto\tlocal\n\
                 rpool/ROOT/openindiana-1\tmountpoint\t/\tlocal\nrpool/ROOT/openindiana-1\tcanmount\tnoauto\tlocal\nrpool/ROOT/openindiana-1\torg.opensolaris.libbe:uuid\t01d1a000-0000-4000-8000-000000000006\tlocal\n\
                 rpool/ROOT/openindiana-1/var\tcanmount\tnoauto\tlocal\n\
                 rpool/dump\tvolsize\t1610612736\tlocal\nrpool/dump\tchecksum\toff\tlocal\nrpool/dump\tcompression\toff\tlocal\nrpool/dump\trefreservation\t1610612736\tlocal\n\
                 rpool/export\tmountpoint\t/export\tlocal\n\
                 rpool/swap\tvolsize\t1073741824\tlocal\nrpool/swap\trefreservation\t1107427328\tlocal\n";
    let prtconf = if uefi { "System Configuration:  illumos  i86pc\n    name='efi-systab' type=int64 items=1\n        value=000000007f9ee018\n" } else { "System Configuration:  illumos  i86pc\n    name='bios-boot-device' type=string items=1\n        value='80'\n" };
    let runner = FakeRunner::default()
        .with("hostname", "oi2610\n")
        .with("prtconf -v", prtconf)
        .with("uname -s", "SunOS\n")
        .with("uname -r", "5.11\n")
        .with("uname -v", "illumos-2b8e1b2a1c\n")
        .with("diskinfo -Hp", &format!("-\tc3t0d0\tVirtio\tBlock Device\t{}\tno\tno\n", 64 * MIB))
        .with("zpool list -H -o name", "rpool\n")
        .with(
            "zpool get -H -p -o property,value,source all rpool",
            "size\t47781511168\t-\nbootfs\trpool/ROOT/openindiana-1\tlocal\nbootsize\t8388608\tlocal\nashift\t12\tlocal\nfeature@async_destroy\tenabled\tlocal\nfeature@lz4_compress\tactive\tlocal\nfeature@multi_vdev_crash_dump\tactive\tlocal\n",
        )
        .with(
            "zfs list -H -p -t filesystem,volume -o name,type,used,available,referenced,mountpoint,canmount,mounted,encryption,encryptionroot,keyformat,keylocation,keystatus,origin,compression,recordsize,volsize,volblocksize,acltype,xattr,dnodesize,normalization,utf8only,casesensitivity,atime,relatime,copies,com.sun:auto-snapshot",
            &list,
        )
        .with("zfs get -H -p -t filesystem,volume -s local,received -o name,property,value,source all", local)
        .with("zpool status -P rpool", "  pool: rpool\n state: ONLINE\n  scan: none requested\nconfig:\n\n\tNAME                 STATE     READ WRITE CKSUM\n\trpool                ONLINE       0     0     0\n\t  c3t0d0s1           ONLINE       0     0     0\n\nerrors: No known data errors\n")
        .with("beadm list -H", "openindiana;01d1a000-0000-4000-8000-000000000005;;;8910848;static;1793001600\nopenindiana-1;01d1a000-0000-4000-8000-000000000006;NR;/;2147483648;static;1794650400\n")
        .with("bootadm list-menu", "the location for the active menu is: /rpool/boot/menu.lst\nIndex\tDefault\tDataset\tMenu\n0\t-\trpool/ROOT/openindiana\topenindiana\n1\t*\trpool/ROOT/openindiana-1\topenindiana-1\n");
    (t, runner)
}

/// OpenIndiana's dump (illumos' report on the fake tree), and its plan onto
/// c4t0d0.
fn openindiana_plan(uefi: bool) -> (Value, crate::load::commands::Built) {
    use crate::load::{commands, layout::Layout, target::Mapping, Parsed};
    let (t, runner) = openindiana(uefi);
    let sys = Sys::new(t.root(), Box::new(runner), true);
    let os = crate::other::os_release(&sys);
    crate::osrel::check_supported(os.as_ref()).unwrap();
    let v = crate::other::illumos::report(&sys, crate::other::Family::Illumos, os.as_ref(), "2026-11-14T00:00:00Z");
    assert_eq!(crate::json::parse(&v.pretty()).unwrap().compact(), v.compact());
    if let Err(e) = crate::bugreport::check_plan(&v.pretty()) {
        panic!("redaction: {e}");
    }
    let l = Layout::from_json(v.clone()).unwrap();
    let Ok(Parsed::Run(o)) = crate::load::parse_args(&["l.json".to_string()]) else { unreachable!() };
    (v, commands::build(&l, &o, &Mapping { pairs: vec![("c3t0d0".into(), "c4t0d0".into())], ..Mapping::default() }))
}

/// OpenIndiana read back and planned (docs/design/openindiana.md, O2):
/// supported by its /etc/release; UEFI from prtconf's efi-systab; the
/// unmounted ESP kept byte for byte.  load makes the dump zvol as a volume
/// of its size and writes nothing into it, mounts only the booted BE and its
/// /var (after /), notes that the inactive BE comes back empty, and on UEFI
/// runs `bootadm update-archive` and no installboot (the firmware boots
/// EFI/BOOT/BOOTX64.EFI off the restored ESP, with no NVRAM entry).
#[test]
fn openindiana_guest() {
    use crate::load::{commands::Cmd, Stage};
    let (v, b) = openindiana_plan(true);
    assert_eq!(s(&v, &["os", "family"]), "illumos");
    assert_eq!(s(&v, &["os", "id"]), "openindiana");
    assert_eq!(s(&v, &["os", "version_id"]), "2026.10");
    assert_eq!(s(&v, &["os", "pretty_name"]), "OpenIndiana Hipster 2026.10");
    assert_eq!(s(&v, &["firmware", "mode"]), "uefi");
    assert_eq!(s(&v, &["host", "hostname"]), "oi2610");
    let p0 = v.at(&["storage", "disks", "0", "partitions", "0"]).unwrap();
    assert_eq!(s(p0, &["name"]), "c3t0d0s0");
    assert_eq!(s(p0, &["content", "type"]), "vfat");
    assert!(!arr(p0, &["esp_image"]).is_empty());
    let p1 = v.at(&["storage", "disks", "0", "partitions", "1"]).unwrap();
    assert_eq!((s(p1, &["name"]), s(p1, &["content", "type"])), ("c3t0d0s1", "zfs_member"));
    assert_eq!(s(v.at(&["storage", "disks", "0", "partitions", "2"]).unwrap(), &["name"]), "c3t0d0s8");
    assert!(b.problems.is_empty(), "{:?}", b.problems);
    let all = plan_text(&b);
    let st = |x: Stage| b.stage(x).iter().map(Cmd::shell).collect::<String>();
    // The ESP comes back with the table.
    assert!(b.notes.contains(&"c3t0d0s0: the ESP, written as the source's with the partition table".to_string()), "{:?}", b.notes);
    // The dump zvol: a volume of its size, its properties, and nothing in it.
    let f = st(Stage::Format);
    assert!(f.contains("zfs create -V 1610612736 -o checksum=off -o compression=off -o refreservation=1610612736 -o volblocksize=131072 rpool/dump\n"), "{f}");
    assert!(b.notes.contains(&"ZFS volume rpool/dump: made empty; archivers back up files, not zvols".to_string()), "{:?}", b.notes);
    assert_eq!(all.matches("rpool/dump").count(), 1, "{all}");
    assert!(!all.contains("dumpadm") && !all.contains("/dev/zvol"), "{all}");
    // Both BEs are made; only the booted one and its /var are mounted.
    for ds in ["rpool/ROOT/openindiana", "rpool/ROOT/openindiana/var", "rpool/ROOT/openindiana-1", "rpool/ROOT/openindiana-1/var"] {
        assert!(f.contains(&format!(" {ds}\n")), "{ds} in {f}");
    }
    let m = st(Stage::Mount);
    let root = m.find("zfs mount -O rpool/ROOT/openindiana-1\n").expect(&m);
    let var = m.find("zfs mount -O rpool/ROOT/openindiana-1/var\n").expect(&m);
    assert!(root < var, "{m}");
    assert!(!m.contains("rpool/ROOT/openindiana\n") && !m.contains("rpool/ROOT/openindiana/var\n") && !m.contains("mount -F"), "{m}");
    for ds in ["rpool/export", "rpool/export/home", "rpool/export/home/tester", "rpool"] {
        assert!(m.contains(&format!("zfs mount -O {ds}\n")), "{ds} in {m}");
    }
    let be = b.notes.iter().filter(|n| n.starts_with("boot environment ")).collect::<Vec<_>>();
    assert_eq!(be, ["boot environment openindiana (rpool/ROOT/openindiana) isn't the booted one: it comes back as an empty plain dataset (only the booted BE is restored)"], "{:?}", b.notes);
    assert!(st(Stage::Format).contains("zpool set bootfs=rpool/ROOT/openindiana-1 rpool\n"));
    // UEFI: the boot archive, and no installboot.
    let bt = st(Stage::Boot);
    assert!(bt.contains("bootadm update-archive -R /mnt/bko-target\n"), "{bt}");
    assert!(!all.contains("installboot") && !all.contains("efibootmgr"), "{all}");
    // OmniOS' bootadm remakes etc/versions/build from its build.* fragments
    // and deletes it with none: a fragment for the run, gone after.
    // A build whose mtime has 0 ns (which assemble_files wouldn't put
    // back) gets 1 ns for the run, after the fragment took the exact one,
    // and the fragment's back before it goes.
    let frag = bt.find("cp -p /mnt/bko-target/etc/versions/build /mnt/bko-target/etc/versions/build.bko-holt && ").expect(&bt);
    let ns = bt.find("case $s in *.000000000) /usr/gnu/bin/touch -m -d @${s%.*}.000000001 /mnt/bko-target/etc/versions/build;; esac'\n").expect(&bt);
    let archive = bt.find("bootadm update-archive -R /mnt/bko-target\n").expect(&bt);
    let back = bt.find("/usr/gnu/bin/touch -m -r /mnt/bko-target/etc/versions/build.bko-holt /mnt/bko-target/etc/versions/build && ").expect(&bt);
    let gone = bt.find("rm -f /mnt/bko-target/etc/versions/build.bko-holt").expect(&bt);
    let log = bt.find("cp -Rp ").expect(&bt);
    assert!(frag < ns && ns < archive && archive < back && back < gone && gone < log, "{bt}");
    assert!(bt.contains("for f in /mnt/bko-target/etc/versions/build.*; do if [ -e \"$f\" ] || [ -h \"$f\" ]; then exit 0; fi; done;"), "{bt}");
}

/// The same machine booted by BIOS (as oi2610-bios is): installboot puts
/// the restored system's gptzfsboot in rpool's slice, after the archive.
#[test]
fn openindiana_bios() {
    use crate::load::{commands::Cmd, Stage};
    let (v, b) = openindiana_plan(false);
    assert_eq!(s(&v, &["firmware", "mode"]), "bios");
    assert!(b.problems.is_empty(), "{:?}", b.problems);
    let bt: String = b.stage(Stage::Boot).iter().map(Cmd::shell).collect();
    let frag = bt.find("/etc/versions/build.bko-holt && ").expect(&bt);
    let archive = bt.find("bootadm update-archive -R /mnt/bko-target\n").expect(&bt);
    let gone = bt.find("rm -f /mnt/bko-target/etc/versions/build.bko-holt").expect(&bt);
    let ib = bt.find("installboot -f -b /mnt/bko-target/boot /dev/rdsk/c4t0d0s1\n").expect(&bt);
    assert!(frag < archive && archive < gone && archive < ib, "{bt}");
    assert_eq!(bt.matches("installboot").count(), 1, "{bt}");
}


/// OpenIndiana's report with RUNNER's answers, and its plan onto c4t0d0.
fn openindiana_with(t: &Tree, runner: FakeRunner) -> (Value, crate::load::commands::Built) {
    use crate::load::{commands, layout::Layout, target::Mapping, Parsed};
    let sys = Sys::new(t.root(), Box::new(runner), true);
    let os = crate::other::os_release(&sys);
    let v = crate::other::illumos::report(&sys, crate::other::Family::Illumos, os.as_ref(), "2026-11-14T00:00:00Z");
    let l = Layout::from_json(v.clone()).unwrap();
    let Ok(Parsed::Run(o)) = crate::load::parse_args(&["l.json".to_string()]) else { unreachable!() };
    (v, commands::build(&l, &o, &Mapping { pairs: vec![("c3t0d0".into(), "c4t0d0".into())], ..Mapping::default() }))
}

/// No prtconf to tell UEFI from BIOS: the firmware is unknown, not bios,
/// with a warning saying why, and load refuses the dump rather than put
/// BIOS boot blocks on a UEFI machine.  The same for a prtconf that fails.
#[test]
fn openindiana_without_prtconf_is_refused() {
    let (t, runner) = openindiana(true);
    let (v, b) = openindiana_with(&t, runner.without("prtconf"));
    assert_eq!(s(&v, &["firmware", "mode"]), "unknown");
    assert_eq!(s(&v, &["boot", "loader", "firmware"]), "unknown");
    let w = arr(&v, &["warnings"]);
    assert!(w.contains(&Value::from("the firmware mode (UEFI or BIOS) can't be told: prtconf isn't installed; the dump says unknown, and bko-holt load refuses it")), "{w:?}");
    let refused = "the dump couldn't tell UEFI from BIOS (its firmware mode is unknown; its warnings say why), so load can't choose the boot code to install: dump again once that tool works";
    assert!(b.problems.iter().any(|p| p == refused), "{:?}", b.problems);

    let (t, mut runner) = openindiana(true);
    runner.answers.remove("prtconf -v");
    let (v, b) = openindiana_with(&t, runner);
    assert_eq!(s(&v, &["firmware", "mode"]), "unknown");
    assert!(arr(&v, &["warnings"]).contains(&Value::from("the firmware mode (UEFI or BIOS) can't be told: `prtconf -v` failed; the dump says unknown, and bko-holt load refuses it")));
    assert!(b.problems.iter().any(|p| p == refused), "{:?}", b.problems);
}

/// illumos with its root pool mounted (mnttab) and no zpool: the dump
/// says so, and load refuses it.
#[test]
fn openindiana_without_zpool_is_refused() {
    let (t, runner) = openindiana(true);
    let (v, b) = openindiana_with(&t, runner.without("zpool"));
    let e = s(&v, &["storage", "zfs", "tools", "error"]);
    assert!(e.starts_with("ZFS file systems are mounted, but zpool isn't installed, "), "{e}");
    assert!(b.problems.contains(&format!("ZFS: {e}")), "{:?}", b.problems);
}

// ---- TrueNAS Community Edition 25.10 --------------------------------------

/// TrueNAS's kernel, as each BE's truenas:kernel_version names it.
const TN_KV: &str = "6.12.33-production+truenas";
/// The children of every TrueNAS boot environment (scale-build's fhs.py),
/// with their mountpoints.
const TN_CHILDREN: [(&str, &str); 15] = [
    ("audit", "/audit"),
    ("conf", "/conf"),
    ("data", "/data"),
    ("etc", "/etc"),
    ("home", "/home"),
    ("mnt", "/mnt"),
    ("opt", "/opt"),
    ("root", "/root"),
    ("usr", "/usr"),
    ("var", "/var"),
    ("var/ca-certificates", "/var/local/ca-certificates"),
    ("var/lib", "/var/lib"),
    ("var/lib/incus", "/var/lib/incus"),
    ("var/log", "/var/log"),
    ("var/log/journal", "/var/log/journal"),
];
/// The UUID in .system's netdata dataset's name.
const TN_UUID: &str = "0f1e2d3c4b5a69788796a5b4c3d2e1f0";
/// The datasets an update clones from the BE before it.
const TN_CLONED: [&str; 5] = ["audit", "data", "home", "root", "var/log"];
/// The datasets of a BE that are readonly=on (its root among them, "").
const TN_RO: [&str; 4] = ["", "conf", "opt", "usr"];

/// TrueNAS CE 25.10 as its installer and two updates leave it
/// (docs/design/truenas.md): Debian 12's os-release, /etc/version, the
/// middleware's marks; on each boot disk a BIOS boot partition, an ESP
/// (mkdosfs -F 32 -s 1 -n EFI) nothing mounts, and boot-pool's vdev.
/// `mirror` false: the truenas25 guest, UEFI, one disk, three BEs
/// (25.10.6 kept, 25.10.7, 25.10.8 booted and bootfs), .system on
/// boot-pool.  `mirror` true: truenas25-mirror, BIOS, boot-pool a mirror
/// of vda and vdb, two BEs (25.10.7, 25.10.8), and the data pool tank on
/// vdc holding .system.  Each update's audit, data, home, root and var/log
/// are clones of the BE before's.  The ESPs' files are where dump mounts
/// them (/run/bko-holt/esp-DEV), the inactive BEs' /etc/version where it
/// mounts their etc datasets; the runner answers those mounts.
fn truenas(mirror: bool) -> (Tree, FakeRunner) {
    use crate::efi::GLOBAL;
    const BF01: &str = "6a898cc3-1dd2-11b2-99a6-080020c9b98d";
    let t = Tree::new(if mirror { "scn-tn-mirror" } else { "scn-tn" });
    let bes: Vec<&str> = if mirror { vec!["25.10.7", "25.10.8"] } else { vec!["25.10.6", "25.10.7", "25.10.8"] };
    let active = "25.10.8";
    let disks: Vec<(&str, &str)> = if mirror { vec![("vda", "254:0"), ("vdb", "254:16")] } else { vec![("vda", "254:0")] };
    let leaf = |n: &str, i: u64| format!(r#"{{"type": "disk", "id": {i}, "guid": {}, "path": "/dev/{n}3", "whole_disk": 0}}"#, 21 + i);
    let tree = if mirror {
        format!(r#"{{"type": "mirror", "id": 0, "guid": 20, "ashift": 12, "children": [{}, {}]}}"#, leaf("vda", 0), leaf("vdb", 1))
    } else {
        r#"{"type": "disk", "id": 0, "guid": 21, "path": "/dev/vda3", "whole_disk": 0, "ashift": 12}"#.to_string()
    };
    let serials = [0x7e57_0001u32, 0x7e57_0002];
    let var = |n: &str| format!("/sys/firmware/efi/efivars/{n}-{GLOBAL}");
    let grubx64 = vec![7u8; 300];
    let mut order = vec![7u8, 0, 0, 0];
    for (i, (d, devno)) in disks.iter().enumerate() {
        let partuuid = format!("7e7e7e7e-2222-4000-8000-00000000000{i}");
        let mut img = vec![0u8; 96 * MIB];
        write_gpt(
            &mut img,
            512,
            &GptSpec {
                disk_guid: if i == 0 { "7e7e7e7e-0000-4000-8000-00000000000a" } else { "7e7e7e7e-0000-4000-8000-00000000000b" },
                parts: vec![
                    (BIOS_BOOT, if i == 0 { "7e7e7e7e-1111-4000-8000-00000000000a" } else { "7e7e7e7e-1111-4000-8000-00000000000b" }, 34, 2047, 0, ""),
                    (ESP, &partuuid, 2048, 67583, 0, ""),
                    (BF01, if i == 0 { "7e7e7e7e-3333-4000-8000-00000000000a" } else { "7e7e7e7e-3333-4000-8000-00000000000b" }, 67584, 184319, 0, ""),
                ],
            },
        );
        put(&mut img, 2048, &fat::sample::fat32(65536, serials[i], "EFI"));
        let cfg = format!(
            r#"{{"version": 5000, "name": "boot-pool", "state": 0, "txg": 4, "pool_guid": 777, "hostname": "truenas", "hostid": 305419896, "top_guid": {}, "guid": {},
                "vdev_children": 1, "vdev_tree": {tree}, "features_for_read": {{"com.delphix:hole_birth": true}}}}"#,
            if mirror { 20 } else { 21 },
            21 + i
        );
        put(&mut img, 67584, &zfs::pack::vdev(116736 * 512, &crate::json::parse(&cfg).unwrap()));
        let major = devno.split(':').next().unwrap();
        let minor: u32 = devno.split(':').nth(1).unwrap().parse().unwrap();
        t.disk(d, devno, &img, 512)
            .partition(d, &format!("{d}1"), &format!("{major}:{}", minor + 1), 1, 34, 2014, &img)
            .partition(d, &format!("{d}2"), &format!("{major}:{}", minor + 2), 2, 2048, 65536, &img)
            .partition(d, &format!("{d}3"), &format!("{major}:{}", minor + 3), 3, 67584, 116736, &img)
            .symlink(&format!("/dev/disk/by-partuuid/{partuuid}"), &format!("../../{d}2"));
        if !mirror {
            t.file(&var(&format!("Boot000{i}")), efi_load_option(&format!("TrueNAS-{i}"), 2, 2048, 65536, &partuuid, "\\EFI\\debian\\grubx64.efi"));
            order.extend([i as u8, 0]);
        }
        let esp = format!("/run/bko-holt/esp-{d}2");
        t.file(&format!("{esp}/EFI/debian/grubx64.efi"), &grubx64).file(&format!("{esp}/EFI/boot/bootx64.efi"), &grubx64);
    }
    if mirror {
        // The data pool: one GPT partition on vdc.
        let mut img = vec![0u8; 96 * MIB];
        write_gpt(&mut img, 512, &GptSpec { disk_guid: "7e7e7e7e-0000-4000-8000-00000000000c", parts: vec![(BF01, "7e7e7e7e-3333-4000-8000-00000000000c", 2048, 194559, 0, "")] });
        let cfg = r#"{"version": 5000, "name": "tank", "state": 0, "txg": 4, "pool_guid": 888, "hostname": "truenas", "hostid": 305419896, "top_guid": 31, "guid": 31,
            "vdev_children": 1, "vdev_tree": {"type": "disk", "id": 0, "guid": 31, "path": "/dev/vdc1", "whole_disk": 0, "ashift": 12}, "features_for_read": {"com.delphix:hole_birth": true}}"#;
        put(&mut img, 2048, &zfs::pack::vdev(192512 * 512, &crate::json::parse(cfg).unwrap()));
        t.disk("vdc", "254:32", &img, 512).partition("vdc", "vdc1", "254:33", 1, 2048, 192512, &img);
    } else {
        t.file("/sys/firmware/efi/fw_platform_size", "64\n").file(&var("BootOrder"), order).file(&var("BootCurrent"), [6u8, 0, 0, 0, 0, 0]).file(&var("SecureBoot"), [6u8, 0, 0, 0, 0]);
    }
    // Mounts: the booted BE at / and its children, boot-pool/grub, .system.
    let mut mi = format!("26 1 0:30 / / ro,relatime shared:1 - zfs boot-pool/ROOT/{active} ro,xattr,noacl\n");
    let mut n = 31;
    for (c, mp) in TN_CHILDREN {
        mi += &format!("{n} 26 0:{n} / {mp} rw,relatime shared:{n} - zfs boot-pool/ROOT/{active}/{c} rw,xattr,noacl\n");
        n += 1;
    }
    let sys_pool = if mirror { "tank" } else { "boot-pool" };
    mi += &format!("{n} 26 0:{n} / /boot/grub rw,relatime shared:{n} - zfs boot-pool/grub rw,xattr,noacl\n");
    n += 1;
    if mirror {
        mi += &format!("{n} 26 0:{n} / /mnt/tank rw,relatime shared:{n} - zfs tank rw,xattr,posixacl\n");
        n += 1;
        mi += &format!("{n} 26 0:{n} / /mnt/tank/share rw,relatime shared:{n} - zfs tank/share rw,xattr,posixacl\n");
        n += 1;
    }
    mi += &format!("{n} 26 0:{n} / /var/db/system rw,relatime shared:{n} - zfs {sys_pool}/.system rw,xattr,noacl\n");
    n += 1;
    let cores = n;
    mi += &format!("{n} 26 0:{n} / /var/db/system/cores rw,relatime shared:{n} - zfs {sys_pool}/.system/cores rw,xattr,noacl\n");
    n += 1;
    // The netdata dataset at a path that isn't its name; cores again at
    // systemd's coredump directory (the same dataset, twice: the 25.10.6
    // guest's).
    mi += &format!("{n} 26 0:{n} / /var/db/system/netdata rw,relatime shared:{n} - zfs {sys_pool}/.system/netdata-{TN_UUID} rw,xattr,noacl\n");
    n += 1;
    mi += &format!("{n} 26 0:{cores} / /var/lib/systemd/coredump rw,relatime shared:{n} - zfs {sys_pool}/.system/cores rw,xattr,noacl\n");
    t.file("/etc/os-release", "PRETTY_NAME=\"Debian GNU/Linux 12 (bookworm)\"\nNAME=\"Debian GNU/Linux\"\nVERSION_ID=\"12\"\nVERSION=\"12 (bookworm)\"\nVERSION_CODENAME=bookworm\nID=debian\n")
        .file("/etc/debian_version", "12.11\n")
        .file("/etc/version", format!("{active}\n"))
        .file("/data/freenas-v1.db", "SQLite format 3\0")
        .file("/usr/local/bin/truenas-grub.py", "#!/usr/bin/python3\n")
        .file("/proc/self/mountinfo", mi)
        .file("/etc/fstab", "boot-pool/grub\t/boot/grub\tzfs\trelatime,defaults\t0\t0\ntmpfs\t/run/lock\ttmpfs\tdefaults\t0\t0\n")
        .file("/etc/hostid", [0x78u8, 0x56, 0x34, 0x12])
        .file(&format!("/boot/vmlinuz-{TN_KV}"), "k")
        .file(&format!("/boot/initrd.img-{TN_KV}"), "i")
        .file(&format!("/boot/config-{TN_KV}"), "")
        .dir(&format!("/usr/lib/modules/{TN_KV}/kernel"))
        .symlink("/lib", "usr/lib")
        .file("/etc/initramfs-tools/initramfs.conf", "MODULES=most\n")
        .file("/boot/grub/grub.cfg", format!("set default=0\nmenuentry \"TrueNAS Scale GNU/Linux {active}\" {{\n  linux /ROOT/{active}@/boot/vmlinuz-{TN_KV} root=ZFS=boot-pool/ROOT/{active} ro\n}}\n"))
        .file("/proc/sys/kernel/osrelease", format!("{TN_KV}\n"))
        .file("/proc/sys/kernel/hostname", "truenas\n")
        .file("/proc/cmdline", format!("BOOT_IMAGE=/ROOT/{active}@/boot/vmlinuz-{TN_KV} root=ZFS=boot-pool/ROOT/{active} ro libata.allow_tpm=1\n"));
    let pkgs = [
        ("grub-pc", "2.06-13+deb12u1"),
        ("grub-efi-amd64-bin", "2.06-13+deb12u1"),
        ("grub-common", "2.06-13+deb12u1"),
        ("zfsutils-linux", "2.3.4-1"),
        ("zfs-initramfs", "2.3.4-1"),
        ("initramfs-tools", "0.142"),
        ("efibootmgr", "17-2"),
        ("dosfstools", "4.2-1"),
        ("middlewared", "25.10.8"),
        ("truenas", "25.10.8"),
    ];
    let status: String = pkgs.iter().map(|(n, v)| format!("Package: {n}\nStatus: install ok installed\nArchitecture: amd64\nVersion: {v}\n\n")).collect();
    t.file("/var/lib/dpkg/status", status);
    // zfs list's columns: name type used available referenced mountpoint
    // canmount mounted encryption encryptionroot keyformat keylocation
    // keystatus origin compression ... (report::zfs_tools_json).
    let fs = |name: &str, mp: &str, canmount: &str, mounted: &str, origin: &str| {
        format!("{name}\tfilesystem\t900\t2000\t96\t{mp}\t{canmount}\t{mounted}\toff\t-\tnone\tnone\t-\t{origin}\ton\t131072\t-\t-\toff\tsa\tauto\tformD\ton\tsensitive\ton\ton\t1\t-\n")
    };
    let mut list = fs("boot-pool", "none", "off", "no", "-") + &fs("boot-pool/ROOT", "none", "off", "no", "-") + &fs("boot-pool/grub", "legacy", "off", "yes", "-");
    let mut props = String::from("boot-pool\tcanmount\toff\tlocal\nboot-pool\tmountpoint\tnone\tlocal\nboot-pool\tcompression\ton\tlocal\nboot-pool\tacltype\toff\tlocal\nboot-pool\txattr\tsa\tlocal\nboot-pool\trelatime\ton\tlocal\nboot-pool\tdevices\toff\tlocal\nboot-pool/ROOT\tcanmount\toff\tlocal\nboot-pool/ROOT\torg.zectl:bootloader\tgrub\tlocal\nboot-pool/grub\tmountpoint\tlegacy\tlocal\nboot-pool/grub\tcanmount\toff\tlocal\n");
    for (bi, be) in bes.iter().enumerate() {
        let on = if *be == active { "yes" } else { "no" };
        let root = format!("boot-pool/ROOT/{be}");
        list += &fs(&root, "legacy", "on", on, "-");
        props += &format!("{root}\tmountpoint\tlegacy\tlocal\n{root}\treadonly\ton\tlocal\n{root}\t{}\t{TN_KV}\tlocal\n{root}\tzectl:keep\t{}\tlocal\n", crate::truenas::KERNEL_PROP, if bi == 0 && !mirror { "True" } else { "False" });
        for (c, mp) in TN_CHILDREN {
            let ds = format!("{root}/{c}");
            let origin = if bi > 0 && TN_CLONED.contains(&c) { format!("boot-pool/ROOT/{}/{c}@install-2026-09-0{bi}", bes[bi - 1]) } else { "-".to_string() };
            list += &fs(&ds, mp, "noauto", on, &origin);
            props += &format!("{ds}\tmountpoint\t{mp}\tlocal\n{ds}\tcanmount\tnoauto\tlocal\n{ds}\torg.zectl:bootloader\t\tlocal\n");
            if TN_RO.contains(&c) {
                props += &format!("{ds}\treadonly\ton\tlocal\n");
            }
        }
        if *be != active {
            t.file(&format!("/run/bko-holt/truenas-be-{be}/version"), format!("{be}\n"));
        }
    }
    for (ds, canmount) in [(format!("{sys_pool}/.system"), "on"), (format!("{sys_pool}/.system/cores"), "on"), (format!("{sys_pool}/.system/netdata-{TN_UUID}"), "noauto")] {
        list += &fs(&ds, "legacy", canmount, "yes", "-");
        props += &format!("{ds}\tmountpoint\tlegacy\tlocal\n");
        if canmount != "on" {
            props += &format!("{ds}\tcanmount\t{canmount}\tlocal\n");
        }
    }
    let mut pools = "boot-pool\n".to_string();
    if mirror {
        pools += "tank\n";
        list = fs("tank", "/mnt/tank", "on", "yes", "-") + &fs("tank/share", "/mnt/tank/share", "on", "yes", "-") + &list;
        props += "tank\tmountpoint\t/tank\tlocal\ntank/share\tmountpoint\t/tank/share\tinherited\n";
    }
    let vdevs = if mirror {
        r#"{"mirror-0": {"name": "mirror-0", "vdev_type": "mirror", "vdevs": {"/dev/vda3": {"name": "/dev/vda3", "vdev_type": "disk", "path": "/dev/vda3"}, "/dev/vdb3": {"name": "/dev/vdb3", "vdev_type": "disk", "path": "/dev/vdb3"}}}}"#
    } else {
        r#"{"/dev/vda3": {"name": "/dev/vda3", "vdev_type": "disk", "path": "/dev/vda3"}}"#
    };
    let status_of = |pool: &str, vdevs: &str| format!(r#"{{"output_version": {{"command": "zpool status", "vers_major": 0, "vers_minor": 1}}, "pools": {{"{pool}": {{"name": "{pool}", "state": "ONLINE", "vdevs": {{"{pool}": {{"name": "{pool}", "vdev_type": "root", "vdevs": {vdevs}}}}}}}}}}}"#);
    let mut runner = FakeRunner::default()
        .with("zpool list -H -o name", &pools)
        .with(
            "zpool get -H -p -o property,value,source all boot-pool",
            &format!("size\t31138512896\t-\nashift\t12\tlocal\ncachefile\tnone\tlocal\ncompatibility\tgrub2\tlocal\nbootfs\tboot-pool/ROOT/{active}\tlocal\nfree\t1\t-\nfeature@async_destroy\tenabled\tlocal\nfeature@lz4_compress\tactive\tlocal\n"),
        )
        .with(
            "zfs list -H -p -t filesystem,volume -o name,type,used,available,referenced,mountpoint,canmount,mounted,encryption,encryptionroot,keyformat,keylocation,keystatus,origin,compression,recordsize,volsize,volblocksize,acltype,xattr,dnodesize,normalization,utf8only,casesensitivity,atime,relatime,copies,com.sun:auto-snapshot",
            &list,
        )
        .with("zfs get -H -p -t filesystem,volume -s local,received -o name,property,value,source all", &props)
        .with("zpool status -j --json-int -P boot-pool", &status_of("boot-pool", vdevs));
    if mirror {
        runner = runner
            .with("zpool get -H -p -o property,value,source all tank", "size\t98784247808\t-\nashift\t12\tlocal\nfeature@async_destroy\tenabled\tlocal\n")
            .with("zpool status -j --json-int -P tank", &status_of("tank", r#"{"/dev/vdc1": {"name": "/dev/vdc1", "vdev_type": "disk", "path": "/dev/vdc1"}}"#));
    }
    for (d, _) in &disks {
        let at = format!("/run/bko-holt/esp-{d}2");
        runner = runner.with(&format!("mount -t vfat -o ro,noexec,nodev,nosuid /dev/{d}2 {at}"), "").with(&format!("umount {at}"), "");
    }
    for be in bes.iter().filter(|b| **b != active) {
        let at = format!("/run/bko-holt/truenas-be-{be}");
        runner = runner.with(&format!("mount -t zfs -o ro,zfsutil,nodev,nosuid,noexec boot-pool/ROOT/{be}/etc {at}"), "").with(&format!("umount {at}"), "");
    }
    (t, runner)
}

/// TrueNAS's dump and plan: the source disks going to sda (sdb, sdc);
/// `extra`, more of load's arguments; `pool_names`, the mapping's renames.
fn truenas_plan(mirror: bool, extra: &[&str], pool_names: &[(&str, &str)]) -> (Value, crate::load::commands::Built) {
    use crate::load::{commands, layout::Layout, target::Mapping, Parsed};
    let (t, runner) = truenas(mirror);
    let v = run(&t, true, runner);
    let l = Layout::from_json(v.clone()).unwrap();
    let mut a = vec!["l.json".to_string()];
    a.extend(extra.iter().map(|s| s.to_string()));
    let Ok(Parsed::Run(o)) = crate::load::parse_args(&a) else { unreachable!() };
    let only = (!o.pools.is_empty()).then(|| l.only_pools(&o.pools));
    let l = only.map_or(l, |x| x.layout);
    let mut pairs: Vec<(String, String)> = vec![("vda".into(), "sda".into())];
    if mirror {
        pairs.push(("vdb".into(), "sdb".into()));
        if l.disk("vdc").is_some() {
            pairs.push(("vdc".into(), "sdc".into()));
        }
    }
    let m = Mapping { pairs, pool_names: pool_names.iter().map(|(a, b)| (a.to_string(), b.to_string())).collect(), ..Mapping::default() };
    (v, commands::build(&l, &o, &m))
}

/// TrueNAS 25.10, one UEFI disk, three BEs: the inactive BEs in
/// backup_sources with their read-only mounts, boot.truenas, the ESP read
/// though nothing mounts it, os.product; the plan makes every BE's
/// datasets writable, mounts each inactive BE under run/bko-be and takes
/// it away again, holds readonly=on until the boot stage's end, and
/// reinstalls GRUB both ways with one update-grub and no initramfs.
#[test]
fn truenas_guest() {
    use crate::load::{commands::Cmd, Stage};
    let (v, b) = truenas_plan(false, &[], &[]);
    assert_eq!(s(&v, &["os", "id"]), "debian");
    assert_eq!(s(&v, &["os", "version_id"]), "12");
    assert_eq!(s(&v, &["os", "product"]), "TrueNAS 25.10.8");
    for (p, ver) in [("grub-pc", "2.06-13+deb12u1"), ("grub-efi-amd64-bin", "2.06-13+deb12u1"), ("middlewared", "25.10.8"), ("truenas", "25.10.8")] {
        assert_eq!(s(&v, &["boot", "packages", p]), ver, "{p}");
    }
    // The inactive BEs' sources: no `from`, their read-only mount.
    let src = arr(&v, &["storage", "backup_sources"]);
    let hinted: Vec<(&str, &str, &str)> = src.iter().filter(|b| b.get("from").unwrap().is_null()).map(|b| (s(b, &["path"]), s(b, &["mount", "device"]), s(b, &["mount", "options"]))).collect();
    assert_eq!(hinted.len(), 2 * 16, "{hinted:?}");
    assert!(hinted.contains(&("/run/bko-be/25.10.6", "boot-pool/ROOT/25.10.6", "ro,nodev,nosuid,noexec")), "{hinted:?}");
    assert!(hinted.contains(&("/run/bko-be/25.10.6/usr", "boot-pool/ROOT/25.10.6/usr", "ro,zfsutil,nodev,nosuid,noexec")), "{hinted:?}");
    assert!(hinted.contains(&("/run/bko-be/25.10.7/var/local/ca-certificates", "boot-pool/ROOT/25.10.7/var/ca-certificates", "ro,zfsutil,nodev,nosuid,noexec")), "{hinted:?}");
    assert!(!hinted.iter().any(|h| h.0.contains("25.10.8")), "{hinted:?}");
    let first = src.iter().find(|b| s(b, &["path"]) == "/run/bko-be/25.10.6").unwrap();
    assert_eq!(s(first, &["note"]), "ZFS boot environment 25.10.6 (not booted): a backup mounts it read-only here");
    assert_eq!(s(first, &["fstype"]), "zfs");
    // The legacy datasets mounted on the source: sources at their paths.
    let netdata = format!("boot-pool/.system/netdata-{TN_UUID}");
    for (p, ds) in [("/boot/grub", "boot-pool/grub"), ("/var/db/system", "boot-pool/.system"), ("/var/db/system/cores", "boot-pool/.system/cores"), ("/var/db/system/netdata", netdata.as_str()), ("/", "boot-pool/ROOT/25.10.8"), ("/usr", "boot-pool/ROOT/25.10.8/usr")] {
        assert!(src.iter().any(|b| s(b, &["path"]) == p && s(b, &["source"]) == ds && s(b, &["from"]) == p), "{p}");
    }
    // .system/cores, mounted twice: backed up once, at its first path.
    assert_eq!(src.iter().filter(|b| s(b, &["source"]) == "boot-pool/.system/cores").count(), 1);
    assert!(!src.iter().any(|b| s(b, &["path"]) == "/var/lib/systemd/coredump"));
    let cores = src.iter().find(|b| s(b, &["path"]) == "/var/db/system/cores").unwrap();
    assert_eq!(s(cores, &["note"]), "ZFS dataset boot-pool/.system/cores is also mounted at /var/lib/systemd/coredump: backed up here only, once");
    // boot.truenas: every BE, its version (read from its own etc), kernel,
    // keep and clones.
    let tn = v.at(&["boot", "truenas"]).unwrap();
    let bes = arr(tn, &["bes"]);
    let got: Vec<(&str, &str, &str, bool)> = bes.iter().map(|b| (s(b, &["name"]), s(b, &["version"]), s(b, &["keep"]), b.get("active").unwrap().as_bool().unwrap())).collect();
    assert_eq!(got, vec![("25.10.6", "25.10.6", "True", false), ("25.10.7", "25.10.7", "False", false), ("25.10.8", "25.10.8", "False", true)]);
    assert!(bes.iter().all(|b| s(b, &["kernel"]) == TN_KV));
    assert_eq!(arr(&bes[2], &["clones"]).len(), 5);
    assert_eq!(s(&bes[2], &["clones", "1", "origin"]), "boot-pool/ROOT/25.10.7/data@install-2026-09-02");
    assert_eq!(s(tn, &["esps", "0", "device"]), "vda2");
    assert_eq!(s(tn, &["esps", "0", "label"]), "EFI");
    // The ESP in boot.esps, mounted nowhere, with its files.
    let esps = arr(&v, &["boot", "esps"]);
    assert_eq!(esps.len(), 1);
    assert_eq!(s(&esps[0], &["device"]), "vda2");
    assert!(esps[0].get("mounted_at").unwrap().is_null());
    let files: Vec<&str> = arr(&esps[0], &["contents", "files"]).iter().map(|f| s(f, &["path"])).collect();
    assert!(files.contains(&"EFI/debian/grubx64.efi") && files.contains(&"EFI/boot/bootx64.efi"), "{files:?}");
    let w: Vec<&str> = arr(&v, &["warnings"]).iter().filter_map(Value::as_str).collect();
    assert!(w.is_empty(), "{w:?}");

    assert!(b.problems.is_empty(), "{:?}", b.problems);
    let text = |st: Stage| -> String { b.stage(st).iter().map(Cmd::shell).collect() };
    // readonly=on nowhere before the boot stage; there, once for each of
    // the 4 read-only datasets of each BE, after everything else.
    for st in [Stage::Partition, Stage::Assemble, Stage::Format, Stage::Mount, Stage::Restore] {
        assert!(!text(st).contains("readonly=on"), "{st:?}: {}", text(st));
    }
    let fmt = text(Stage::Format);
    assert!(!fmt.contains(" -t ") || !fmt.contains("zpool create -f -d -o cachefile=none -t"), "{fmt}");
    let asm = text(Stage::Assemble);
    assert!(asm.lines().any(|l| l.starts_with("zpool create ") && l.contains("-o compatibility=grub2") && l.contains(" boot-pool /dev/sda3") && !l.contains(" -t ")), "{asm}");
    for be in ["25.10.6", "25.10.7", "25.10.8"] {
        assert!(fmt.lines().any(|l| l.starts_with("zfs create -u ") && l.ends_with(&format!(" boot-pool/ROOT/{be}")) && l.contains("truenas:kernel_version=") && l.contains("zectl:keep=")), "{be}: {fmt}");
        for (c, _) in TN_CHILDREN {
            assert!(fmt.lines().any(|l| l.starts_with("zfs create -u ") && l.ends_with(&format!(" boot-pool/ROOT/{be}/{c}"))), "{be}/{c}: {fmt}");
        }
    }
    assert!(fmt.contains("zpool set bootfs=boot-pool/ROOT/25.10.8 boot-pool\n"), "{fmt}");
    assert!(fmt.lines().any(|l| l.starts_with("mkfs.fat ") && l.contains("-i 7E570001") && l.contains("-n EFI") && l.ends_with("/dev/sda2")), "{fmt}");
    let bt = text(Stage::Boot);
    let ro: Vec<&str> = bt.lines().filter(|l| l.starts_with("zfs set readonly=on ")).collect();
    assert_eq!(ro.len(), 3 * 4, "{bt}");
    assert!(ro.contains(&"zfs set readonly=on boot-pool/ROOT/25.10.6") && ro.contains(&"zfs set readonly=on boot-pool/ROOT/25.10.8/usr"), "{ro:?}");
    let last_ro = bt.rfind("zfs set readonly=on").unwrap();
    assert!(bt[last_ro..].lines().count() == 1, "readonly=on comes last: {bt}");
    assert!(bt.find("zfs set readonly=on").unwrap() > bt.find("update-grub").unwrap() && bt.find("zfs set readonly=on").unwrap() > bt.find("efibootmgr").unwrap(), "{bt}");
    // Mounts: the booted BE at /, its children by zfs mount, the legacy
    // datasets by mount, each inactive BE at run/bko-be.
    let mt = text(Stage::Mount);
    assert!(mt.contains("mount -t zfs boot-pool/ROOT/25.10.8 /mnt/bko-target\n"), "{mt}");
    assert!(mt.contains("zfs mount boot-pool/ROOT/25.10.8/usr\n"), "{mt}");
    assert!(mt.contains("mount -t zfs boot-pool/grub /mnt/bko-target/boot/grub\n") && mt.contains("mount -t zfs boot-pool/.system /mnt/bko-target/var/db/system\n") && mt.contains("mount -t zfs boot-pool/.system/cores /mnt/bko-target/var/db/system/cores\n"), "{mt}");
    assert!(mt.contains(&format!("mount -t zfs {netdata} /mnt/bko-target/var/db/system/netdata\n")), "{mt}");
    assert_eq!(mt.matches("boot-pool/grub /mnt").count(), 1, "{mt}");
    assert_eq!(mt.matches("mount -t zfs boot-pool/.system/cores ").count(), 1, "{mt}");
    assert!(!mt.contains("coredump"), "{mt}");
    assert!(b.notes.iter().any(|n| n == "ZFS dataset boot-pool/.system/cores was also mounted at /var/lib/systemd/coredump on the source: mounted at /var/db/system/cores only here (its files are restored once; the restored system mounts it again)"), "{:?}", b.notes);
    assert!(!b.notes.iter().any(|n| n.contains("/boot/grub")), "{:?}", b.notes);
    for be in ["25.10.6", "25.10.7"] {
        let root = mt.find(&format!("mount -t zfs boot-pool/ROOT/{be} /mnt/bko-target/run/bko-be/{be}\n")).expect(&mt);
        let usr = mt.find(&format!("mount -t zfs -o zfsutil boot-pool/ROOT/{be}/usr /mnt/bko-target/run/bko-be/{be}/usr\n")).expect(&mt);
        let ca = mt.find(&format!("mount -t zfs -o zfsutil boot-pool/ROOT/{be}/var/ca-certificates /mnt/bko-target/run/bko-be/{be}/var/local/ca-certificates\n")).expect(&mt);
        assert!(mt.find(&format!("mkdir -p /mnt/bko-target/run/bko-be/{be}\n")).unwrap() < root && root < usr && usr < ca, "{mt}");
        assert!(!mt.contains(&format!("zfs mount boot-pool/ROOT/{be}")), "{mt}");
        // Unmounted at the boot stage's start, deepest first, then rmdir.
        let u_ca = bt.find(&format!("umount /mnt/bko-target/run/bko-be/{be}/var/local/ca-certificates'\n")).expect(&bt);
        let u_root = bt.find(&format!("umount /mnt/bko-target/run/bko-be/{be}'\n")).expect(&bt);
        let rm = bt.find(&format!("rmdir /mnt/bko-target/run/bko-be/{be}'\n")).expect(&bt);
        assert!(u_ca < u_root && u_root < rm, "{bt}");
    }
    let rm_all = bt.find("sh -c '[ ! -d /mnt/bko-target/run/bko-be ] || rmdir /mnt/bko-target/run/bko-be'\n").expect(&bt);
    assert!(rm_all < bt.find("mount --rbind --make-rslave /run /mnt/bko-target/run").unwrap(), "{bt}");
    assert!(!bt.contains("rm -rf /mnt/bko-target/run"), "{bt}");
    // The truenas arm: no initramfs, i386-pc on the disk, x86_64-efi on
    // its ESP with the fallback copy, one update-grub, the NVRAM entry.
    assert!(!bt.contains("update-initramfs") && !bt.contains("truenas-initrd"), "{bt}");
    assert_eq!(bt.matches("grub-install --target=i386-pc /dev/sda\n").count(), 1, "{bt}");
    assert_eq!(bt.matches("grub-install --target=x86_64-efi --efi-directory=/boot/efi --bootloader-id=debian --recheck --no-floppy --no-nvram\n").count(), 1, "{bt}");
    let m = bt.find("mount -t vfat /dev/sda2 /mnt/bko-target/boot/efi\n").expect(&bt);
    let cp = bt.find("cp /mnt/bko-target/boot/efi/EFI/debian/grubx64.efi /mnt/bko-target/boot/efi/EFI/boot/bootx64.efi\n").expect(&bt);
    let um = bt.find("umount /mnt/bko-target/boot/efi\n").expect(&bt);
    assert!(m < cp && cp < um && um < bt.find("update-grub").unwrap(), "{bt}");
    assert_eq!(bt.matches("update-grub").count(), 1, "{bt}");
    assert!(bt.contains("efibootmgr --quiet --create --disk /dev/sda --part 2 --label TrueNAS-0 --loader '\\EFI\\debian\\grubx64.efi'\n"), "{bt}");
    assert!(b.notes.iter().any(|n| n == "TrueNAS: 3 boot environments restored (active 25.10.8); GRUB reinstalled on 1 disk and 1 ESP, its menu rebuilt"), "{:?}", b.notes);
    assert!(b.notes.iter().any(|n| n == "ZFS: 25.10.8's datasets audit, data, home, root, var/log were clones of 25.10.7's snapshots; they come back as full copies (no snapshot is remade)"), "{:?}", b.notes);
    assert!(b.notes.iter().any(|n| n.starts_with("ZFS: 25.10.7's datasets audit, data")), "{:?}", b.notes);
    assert!(!b.notes.iter().any(|n| n.starts_with("ZFS: 25.10.6's")), "{:?}", b.notes);
    assert!(!text(Stage::Format).contains("@"), "no snapshot or clone is made");
    assert!(text(Stage::Close).contains("zpool export boot-pool"));
    // --no-nvram: no entry.
    let (_, b) = truenas_plan(false, &["--no-nvram"], &[]);
    let bt: String = b.stage(Stage::Boot).iter().map(Cmd::shell).collect();
    assert!(!bt.contains("efibootmgr") && bt.contains("--target=x86_64-efi"), "{bt}");
}

/// What load refuses on TrueNAS: a release other than 25.10, and
/// boot-pool under another name here.
#[test]
fn truenas_refusals() {
    let (_, b) = truenas_plan(false, &[], &[("boot-pool", "boot-pool_bko")]);
    assert!(b.problems.iter().any(|p| p.starts_with("TrueNAS: boot-pool can't be renamed (its GRUB script names it)")), "{:?}", b.problems);
    let (t, runner) = truenas(false);
    t.file("/etc/version", "24.10.2\n");
    let v = run(&t, true, runner);
    assert_eq!(s(&v, &["os", "product"]), "TrueNAS 24.10.2");
    let l = crate::load::layout::Layout::from_json(v).unwrap();
    let Ok(crate::load::Parsed::Run(o)) = crate::load::parse_args(&["l.json".to_string()]) else { unreachable!() };
    let b = crate::load::commands::build(&l, &o, &crate::load::target::Mapping { pairs: vec![("vda".into(), "sda".into())], ..Default::default() });
    assert_eq!(b.problems, vec!["TrueNAS: the source is TrueNAS 24.10.2; bko-holt load supports TrueNAS 25.10 only".to_string()]);
    // Without the middleware's marks it's plain Debian 12: no boot.truenas,
    // no product; its inactive BEs are still sources (crate::zfs_be).
    let (t, runner) = truenas(false);
    std::fs::remove_file(t.p("/data/freenas-v1.db")).unwrap();
    let v = run(&t, true, runner);
    assert!(v.at(&["boot", "truenas"]).is_none() && v.at(&["os", "product"]).is_none());
    assert!(arr(&v, &["storage", "backup_sources"]).iter().any(|b| s(b, &["path"]) == "/run/bko-be/25.10.6"));
    // boot-pool/grub in fstab: mounted once, as the source had it, and
    // no note that it isn't on the layout's disks.
    let (t, runner) = truenas(false);
    t.file("/etc/fstab", "boot-pool/grub	/boot/grub	zfs	relatime,defaults	0	0
");
    let l = crate::load::layout::Layout::from_json(run(&t, true, runner)).unwrap();
    let b = crate::load::commands::build(&l, &o, &crate::load::target::Mapping { pairs: vec![("vda".into(), "sda".into())], ..Default::default() });
    let mt: String = b.stage(crate::load::Stage::Mount).iter().map(crate::load::commands::Cmd::shell).collect();
    assert_eq!(mt.matches("mount -t zfs boot-pool/grub /mnt/bko-target/boot/grub\n").count(), 1, "{mt}");
    assert!(!b.notes.iter().any(|n| n.contains("/boot/grub")), "{:?}", b.notes);
}

/// TrueNAS on a BIOS boot mirror with the data pool tank on vdc: GRUB on
/// both disks both ways, no NVRAM; tank restored as any pool by default,
/// left alone with --pools boot-pool, and a --map of its disk refused.
#[test]
fn truenas_mirror_guest() {
    use crate::load::{commands::Cmd, Stage};
    let (v, b) = truenas_plan(true, &[], &[]);
    assert_eq!(s(&v, &["firmware", "mode"]), "bios");
    let bes = arr(&v, &["boot", "truenas", "bes"]);
    assert_eq!(bes.len(), 2);
    assert_eq!(arr(&v, &["boot", "esps"]).len(), 2);
    assert!(b.problems.is_empty(), "{:?}", b.problems);
    let text = |b: &crate::load::commands::Built, st: Stage| -> String { b.stage(st).iter().map(Cmd::shell).collect() };
    let asm = text(&b, Stage::Assemble);
    assert!(asm.lines().any(|l| l.starts_with("zpool create ") && l.contains("boot-pool mirror /dev/sda3 /dev/sdb3")), "{asm}");
    assert!(asm.lines().any(|l| l.starts_with("zpool create ") && l.contains(" tank /dev/sdc1")), "{asm}");
    let bt = text(&b, Stage::Boot);
    for d in ["sda", "sdb"] {
        assert_eq!(bt.matches(&format!("grub-install --target=i386-pc /dev/{d}\n")).count(), 1, "{bt}");
        assert!(bt.contains(&format!("mount -t vfat /dev/{d}2 /mnt/bko-target/boot/efi\n")), "{bt}");
    }
    assert_eq!(bt.matches("--target=x86_64-efi").count(), 2, "{bt}");
    assert_eq!(bt.matches("update-grub").count(), 1, "{bt}");
    assert!(!bt.contains("efibootmgr") && !bt.contains("update-initramfs"), "{bt}");
    assert!(b.notes.iter().any(|n| n == "TrueNAS: 2 boot environments restored (active 25.10.8); GRUB reinstalled on 2 disks and 2 ESPs, its menu rebuilt"), "{:?}", b.notes);
    assert!(text(&b, Stage::Mount).contains("mount -t zfs tank/.system /mnt/bko-target/var/db/system\n"));
    // --pools boot-pool: nothing touches vdc's target, tank isn't made,
    // its files aren't restored.
    let (_, b) = truenas_plan(true, &["--pools", "boot-pool", "--repo", "/r"], &[]);
    assert!(b.problems.is_empty(), "{:?}", b.problems);
    let all: String = b.stages.iter().flat_map(|(_, c)| c.iter().map(Cmd::shell)).collect();
    assert!(!all.contains("sdc") && !all.contains("zpool create -f -d -o cachefile=none -o ashift=12 -o feature@async_destroy=enabled -R /mnt/bko-target tank"), "{all}");
    assert!(!all.lines().any(|l| l.starts_with("zfs create") && l.contains(" tank")), "{all}");
    assert!(!all.contains("mount -t zfs tank/.system"), "{all}");
    let rs = text(&b, Stage::Restore);
    assert!(rs.contains("!/mnt/tank/**\n") && rs.contains("!/var/db/system/**\n") && rs.contains("--glob-file /run/bko-holt/restore.globs"), "{rs}");
    let (tr, runner) = truenas(true);
    let l = crate::load::layout::Layout::from_json(run(&tr, true, runner)).unwrap();
    let only = l.only_pools(&["boot-pool".to_string()]);
    assert_eq!(only.disks, vec!["vdc".to_string()]);
    assert!(only.layout.disk("vdc").is_none() && only.problems.is_empty());
    assert_eq!(only.notes, vec!["--pools: pool tank on vdc left untouched (not partitioned, made or restored); its files under /mnt/tank, /mnt/tank/share, /var/db/system, /var/db/system/cores, /var/db/system/netdata aren't restored".to_string()]);
    assert_eq!(only.guids, vec![("tank".to_string(), "888".to_string())]);
    let Ok(crate::load::Parsed::Run(o)) = crate::load::parse_args(&["l.json", "--pools", "boot-pool", "--map", "vdc=/dev/sdc"].map(String::from)) else { unreachable!() };
    let sys = crate::load::target::System { disks: vec![], mounts: vec![], pools: vec![], pvs: vec![], devs: vec![], swaps: vec![] };
    let p = crate::load::target::left_out_pool_problems(&o, &only, &crate::load::target::Mapping::default(), &sys);
    assert_eq!(p, vec!["--map vdc=/dev/sdc: vdc holds a pool --pools leaves untouched, so it has no target".to_string()]);
    // A name the layout hasn't.
    assert_eq!(l.only_pools(&["boot".to_string()]).problems[0], "--pools boot: the layout has no ZFS pool boot (it has boot-pool, tank)");
}

// ---- ZFSBootMenu on Void ----------------------------------------------------

/// Void's kernels in the void-zbm guest: the default series in both BEs,
/// mainline in the default BE only.
const ZBM_LTS: &str = "6.12.63_1";
const ZBM_MAINLINE: &str = "6.18.9_1";

/// The void-zbm guest (docs/design/zfsbootmenu.md) as ZFSBootMenu's Void
/// UEFI guide leaves it, with the second BE its builder makes: GPT, the ESP
/// (vda1) at /boot/efi holding only ZFSBootMenu's release image at
/// EFI/ZBM/VMLINUZ.EFI and its copy VMLINUZ-BACKUP.EFI, the pool zroot
/// (vda2, compatibility=openzfs-2.3-linux) with zroot/ROOT (the shared
/// org.zfsbootmenu:commandline), the BEs zroot/ROOT/void (bootfs, booted,
/// both kernel series) and zroot/ROOT/void-6.12 (a clone of
/// void@pre-mainline, not mounted, its own commandline with %{parent}),
/// zroot/home; NVRAM "ZFSBootMenu (Backup)" and "ZFSBootMenu", the latter
/// first and booted.  `enc`: void-zbm-enc, zroot an encryption root with a
/// passphrase read from /etc/zfs/zroot.key inside the BE, and
/// org.zfsbootmenu:keysource on zroot.  The inactive BE's /boot is where
/// dump mounts it (/run/bko-holt/zbm-be-void-6.12); the runner answers
/// that mount.
fn zfsbootmenu(enc: bool) -> (Tree, FakeRunner) {
    use crate::efi::GLOBAL;
    use crate::zfsbootmenu::sample;
    const BF00: &str = "6a898cc3-1dd2-11b2-99a6-080020c9b98d";
    const ESP_UUID: &str = "2bb0e0e1-1111-4000-8000-00000000000a";
    let t = Tree::new(if enc { "scn-zbm-enc" } else { "scn-zbm" });
    let mut img = vec![0u8; 96 * MIB];
    write_gpt(
        &mut img,
        512,
        &GptSpec { disk_guid: "2bb0e0e1-0000-4000-8000-00000000000a", parts: vec![(ESP, ESP_UUID, 2048, 73727, 0, ""), (BF00, "2bb0e0e1-2222-4000-8000-00000000000a", 73728, 190463, 0, "")] },
    );
    put(&mut img, 2048, &fat::sample::fat32(71680, 0x2bb0_e0e1, ""));
    let cfg = crate::json::parse(
        r#"{"version": 5000, "name": "zroot", "state": 0, "txg": 4, "pool_guid": 999, "hostname": "void-zbm", "hostid": 305419896, "top_guid": 1, "guid": 1,
            "vdev_children": 1, "vdev_tree": {"type": "disk", "id": 0, "guid": 1, "path": "/dev/vda2", "whole_disk": 0, "ashift": 12},
            "features_for_read": {"com.delphix:hole_birth": true}}"#,
    )
    .unwrap();
    put(&mut img, 73728, &zfs::pack::vdev(116736 * 512, &cfg));
    let zbm = sample::uki(sample::RELEASE_OSREL, sample::RELEASE_CMDLINE, sample::RELEASE_KERNEL, true);
    let var = |n: &str| format!("/sys/firmware/efi/efivars/{n}-{GLOBAL}");
    let pkgs = [("base-system", "0.114_2"), ("dracut", "106_1"), ("efibootmgr", "18_2"), ("linux", "6.12_1"), ("linux6.12", ZBM_LTS), ("linux-mainline", "6.18_1"), ("linux6.18", ZBM_MAINLINE), ("zfs", "2.4.0_1"), ("dosfstools", "4.2_1")];
    let db: String = pkgs.iter().map(|(n, v)| format!("\t<key>{n}</key>\n\t<dict>\n\t\t<key>architecture</key>\n\t\t<string>x86_64</string>\n\t\t<key>pkgver</key>\n\t\t<string>{n}-{v}</string>\n\t</dict>\n")).collect();
    t.disk("vda", "254:0", &img, 512)
        .partition("vda", "vda1", "254:1", 1, 2048, 71680, &img)
        .partition("vda", "vda2", "254:2", 2, 73728, 116736, &img)
        .symlink(&format!("/dev/disk/by-partuuid/{ESP_UUID}"), "../../vda1")
        .file("/sys/firmware/efi/fw_platform_size", "64\n")
        .file(&var("Boot0000"), efi_load_option("ZFSBootMenu (Backup)", 1, 2048, 71680, ESP_UUID, "\\EFI\\ZBM\\VMLINUZ-BACKUP.EFI"))
        .file(&var("Boot0001"), efi_load_option("ZFSBootMenu", 1, 2048, 71680, ESP_UUID, "\\EFI\\ZBM\\VMLINUZ.EFI"))
        .file(&var("BootOrder"), [7u8, 0, 0, 0, 1, 0, 0, 0])
        .file(&var("BootCurrent"), [6u8, 0, 0, 0, 1, 0])
        .file(&var("SecureBoot"), [6u8, 0, 0, 0, 0])
        .file("/proc/self/mountinfo", "26 1 0:24 / / rw,relatime shared:1 - zfs zroot/ROOT/void rw,xattr,posixacl\n27 26 0:25 / /home rw,relatime shared:2 - zfs zroot/home rw,xattr,posixacl\n28 26 254:1 / /boot/efi rw,relatime shared:3 - vfat /dev/vda1 rw,fmask=0022\n")
        .file("/etc/fstab", "UUID=2BB0-E0E1 /boot/efi vfat defaults 0 0\n")
        .file("/etc/os-release", "NAME=\"Void\"\nID=\"void\"\nPRETTY_NAME=\"Void Linux\"\nHOME_URL=\"https://voidlinux.org/\"\n")
        .file("/etc/hostid", [0x78u8, 0x56, 0x34, 0x12])
        .file("/etc/dracut.conf.d/zol.conf", "nofsck=\"yes\"\nadd_dracutmodules+=\" zfs \"\nomit_dracutmodules+=\" btrfs \"\n")
        .file("/var/db/xbps/pkgdb-0.38.plist", format!("<?xml version=\"1.0\"?>\n<plist version=\"1.0\">\n<dict>\n{db}</dict>\n</plist>\n"))
        .symlink("/lib", "usr/lib")
        .file("/boot/efi/EFI/ZBM/VMLINUZ.EFI", &zbm)
        .file("/boot/efi/EFI/ZBM/VMLINUZ-BACKUP.EFI", &zbm)
        .file("/etc/bko-be", "void\n")
        .file("/proc/sys/kernel/osrelease", format!("{ZBM_MAINLINE}\n"))
        .file("/proc/sys/kernel/hostname", "void-zbm\n")
        .file("/proc/cmdline", "root=zfs:zroot/ROOT/void loglevel=4 console=tty0 console=ttyS0,115200n8\n");
    for k in [ZBM_LTS, ZBM_MAINLINE] {
        t.file(&format!("/boot/vmlinuz-{k}"), "k").file(&format!("/boot/initramfs-{k}.img"), "i").file(&format!("/boot/config-{k}"), "").dir(&format!("/usr/lib/modules/{k}/kernel"));
    }
    // The inactive BE's /boot, where dump mounts it.
    let at = "/run/bko-holt/zbm-be-void-6.12";
    t.file(&format!("{at}/boot/vmlinuz-{ZBM_LTS}"), "k").file(&format!("{at}/boot/initramfs-{ZBM_LTS}.img"), "i").file(&format!("{at}/boot/config-{ZBM_LTS}"), "");
    if enc {
        t.file("/etc/zfs/zroot.key", "not-the-real-passphrase");
    }
    // zfs list's columns (report::zfs_tools_json): name type used available
    // referenced mountpoint canmount mounted encryption encryptionroot
    // keyformat keylocation keystatus origin compression ...
    let (e, er, kf) = if enc { ("aes-256-gcm", "zroot", "passphrase") } else { ("off", "-", "none") };
    let fs = |name: &str, mp: &str, canmount: &str, mounted: &str, keyloc: &str, origin: &str| {
        let ks = if enc { "available" } else { "-" };
        format!("{name}\tfilesystem\t900\t2000\t96\t{mp}\t{canmount}\t{mounted}\t{e}\t{er}\t{kf}\t{keyloc}\t{ks}\t{origin}\tlz4\t131072\t-\t-\tposix\tsa\tauto\tnone\toff\tsensitive\ton\ton\t1\t-\n")
    };
    let rootloc = if enc { "file:///etc/zfs/zroot.key" } else { "none" };
    let list = fs("zroot", "none", "on", "no", rootloc, "-")
        + &fs("zroot/ROOT", "none", "on", "no", "none", "-")
        + &fs("zroot/ROOT/void", "/", "noauto", "yes", "none", "-")
        + &fs("zroot/ROOT/void-6.12", "/", "noauto", "no", "none", "zroot/ROOT/void@pre-mainline")
        + &fs("zroot/home", "/home", "on", "yes", "none", "-");
    let mut props = String::from(
        "zroot\tmountpoint\tnone\tlocal\nzroot\tcompression\tlz4\tlocal\nzroot\tacltype\tposix\tlocal\nzroot\txattr\tsa\tlocal\nzroot\trelatime\ton\tlocal\n\
         zroot/ROOT\tmountpoint\tnone\tlocal\nzroot/ROOT\torg.zfsbootmenu:commandline\tloglevel=4 console=tty0 console=ttyS0,115200n8\tlocal\n\
         zroot/ROOT/void\tmountpoint\t/\tlocal\nzroot/ROOT/void\tcanmount\tnoauto\tlocal\n\
         zroot/ROOT/void-6.12\tmountpoint\t/\tlocal\nzroot/ROOT/void-6.12\tcanmount\tnoauto\tlocal\nzroot/ROOT/void-6.12\torg.zfsbootmenu:commandline\t%{parent} bko.be=void-6.12\tlocal\n\
         zroot/home\tmountpoint\t/home\tlocal\n",
    );
    if enc {
        props += "zroot\tkeylocation\tfile:///etc/zfs/zroot.key\tlocal\nzroot\torg.zfsbootmenu:keysource\tzroot/ROOT/void\tlocal\n";
    }
    let runner = FakeRunner::default()
        .with("zpool list -H -o name", "zroot\n")
        .with(
            "zpool get -H -p -o property,value,source all zroot",
            &format!(
                "size\t59055800320\t-\nashift\t12\tlocal\nautotrim\ton\tlocal\ncompatibility\topenzfs-2.3-linux\tlocal\nbootfs\tzroot/ROOT/void\tlocal\nfree\t1\t-\nfeature@async_destroy\tenabled\tlocal\nfeature@lz4_compress\tactive\tlocal\n{}",
                if enc { "feature@encryption\tactive\tlocal\n" } else { "" }
            ),
        )
        .with(
            "zfs list -H -p -t filesystem,volume -o name,type,used,available,referenced,mountpoint,canmount,mounted,encryption,encryptionroot,keyformat,keylocation,keystatus,origin,compression,recordsize,volsize,volblocksize,acltype,xattr,dnodesize,normalization,utf8only,casesensitivity,atime,relatime,copies,com.sun:auto-snapshot",
            &list,
        )
        .with("zfs get -H -p -t filesystem,volume -s local,received -o name,property,value,source all", &props)
        .with(
            "zpool status -j --json-int -P zroot",
            r#"{"output_version": {"command": "zpool status", "vers_major": 0, "vers_minor": 1}, "pools": {"zroot": {"name": "zroot", "state": "ONLINE",
               "vdevs": {"zroot": {"name": "zroot", "vdev_type": "root", "vdevs": {"/dev/vda2": {"name": "/dev/vda2", "vdev_type": "disk", "path": "/dev/vda2"}}}}}}}"#,
        )
        .with(&format!("mount -t zfs -o ro,zfsutil,nodev,nosuid,noexec zroot/ROOT/void-6.12 {at}"), "")
        .with(&format!("umount {at}"), "");
    (t, runner)
}

/// ZFSBootMenu's dump (`v`, changed by `change` first) planned onto sda
/// with load's arguments `extra`.
fn zbm_plan(enc: bool, extra: &[&str], change: impl Fn(&mut Value)) -> (Value, crate::load::commands::Built) {
    use crate::load::{commands, layout::Layout, target::Mapping, Parsed};
    let (t, runner) = zfsbootmenu(enc);
    let mut v = run(&t, true, runner);
    change(&mut v);
    let l = Layout::from_json(v.clone()).unwrap();
    let mut a = vec!["l.json".to_string()];
    a.extend(extra.iter().map(|s| s.to_string()));
    let Ok(Parsed::Run(o)) = crate::load::parse_args(&a) else { unreachable!() };
    (v, commands::build(&l, &o, &Mapping { pairs: vec![("vda".into(), "sda".into())], ..Mapping::default() }))
}

/// void-zbm read back and planned: the loader named zfsbootmenu from
/// BootCurrent's image; boot.zfsbootmenu with both images (version, kernel,
/// SHA-256, embedded command line), the pool and both BEs with their /boot;
/// one hinted source for the inactive BE.  The plan makes every dataset
/// with its user properties intact, mounts void-6.12 under run/bko-be and
/// takes it away at the boot stage's start, remakes only the booted BE's
/// initramfs, installs no loader, and remakes both NVRAM entries in the
/// source's order.
#[test]
fn zfsbootmenu_guest() {
    use crate::load::{commands::Cmd, Stage};
    use crate::zfsbootmenu::sample;
    let (v, b) = zbm_plan(false, &[], |_| {});
    assert_eq!(s(&v, &["os", "id"]), "void");
    let w: Vec<&str> = arr(&v, &["warnings"]).iter().filter_map(Value::as_str).collect();
    assert!(w.is_empty(), "{w:?}");
    // The loader, from BootCurrent's file.
    let l = v.at(&["boot", "loader"]).unwrap();
    assert_eq!(s(l, &["name"]), "zfsbootmenu");
    assert!(l.get("package").unwrap().is_null());
    let why: Vec<&str> = arr(l, &["evidence"]).iter().filter_map(Value::as_str).collect();
    assert_eq!(why, ["firmware booted \\EFI\\ZBM\\VMLINUZ.EFI", "that is a ZFSBootMenu image (EFI/ZBM/VMLINUZ.EFI)"]);
    for (p, ver) in [("linux-mainline", "6.18_1"), ("zfs", "2.4.0_1"), ("dracut", "106_1")] {
        assert_eq!(s(&v, &["boot", "packages", p]), ver, "{p}");
    }
    assert_eq!(s(&v, &["boot", "initramfs", "generator"]), "dracut");
    // boot.zfsbootmenu.
    let z = v.at(&["boot", "zfsbootmenu"]).unwrap();
    let images = arr(z, &["images"]);
    let paths: Vec<(&str, &str)> = images.iter().map(|i| (s(i, &["path"]), s(i, &["why"]))).collect();
    assert_eq!(paths, [("EFI/ZBM/VMLINUZ-BACKUP.EFI", "it is in a zbm directory"), ("EFI/ZBM/VMLINUZ.EFI", "it is in a zbm directory")]);
    let i = &images[1];
    let zbm = sample::uki(sample::RELEASE_OSREL, sample::RELEASE_CMDLINE, sample::RELEASE_KERNEL, true);
    assert_eq!(s(i, &["sha256"]), crate::bytes::sha256_hex(&zbm));
    assert_eq!(u(i, &["size"]), zbm.len() as u64);
    assert_eq!((s(i, &["esp_device"]), s(i, &["esp_mount"])), ("vda1", "/boot/efi"));
    assert!(i.get("version").unwrap().is_null());
    assert_eq!(s(i, &["osrel"]), "Void Linux");
    assert_eq!(s(i, &["kernel"]), sample::RELEASE_KERNEL);
    assert_eq!(s(i, &["stub"]), "systemd-stub 256.6");
    assert_eq!(s(i, &["cmdline"]), sample::RELEASE_CMDLINE);
    assert_eq!(s(z, &["pools", "0", "name"]), "zroot");
    assert_eq!(s(z, &["pools", "0", "bootfs"]), "zroot/ROOT/void");
    let bes = arr(z, &["bes"]);
    let got: Vec<(&str, bool, bool, bool)> = bes.iter().map(|b| (s(b, &["dataset"]), b.get("bootfs").unwrap().as_bool().unwrap(), b.get("mounted").unwrap().as_bool().unwrap(), b.get("read").unwrap().as_bool().unwrap())).collect();
    assert_eq!(got, [("zroot/ROOT/void", true, true, true), ("zroot/ROOT/void-6.12", false, false, true)]);
    let names = |b: &Value, k: &str| -> Vec<String> { arr(b, &[k]).iter().filter_map(Value::as_str).map(str::to_string).collect() };
    assert_eq!(names(&bes[0], "kernels"), [format!("vmlinuz-{ZBM_LTS}"), format!("vmlinuz-{ZBM_MAINLINE}")]);
    assert_eq!(names(&bes[0], "initramfs"), [format!("initramfs-{ZBM_LTS}.img"), format!("initramfs-{ZBM_MAINLINE}.img")]);
    assert_eq!(names(&bes[1], "kernels"), [format!("vmlinuz-{ZBM_LTS}")]);
    assert_eq!(names(&bes[1], "initramfs"), [format!("initramfs-{ZBM_LTS}.img")]);
    assert!(bes[0].get("origin").unwrap().is_null());
    assert_eq!(s(&bes[1], &["origin"]), "zroot/ROOT/void@pre-mainline");
    assert_eq!(s(&bes[0], &["commandline"]), "loglevel=4 console=tty0 console=ttyS0,115200n8");
    assert_eq!(s(&bes[0], &["commandline_from"]), "zroot/ROOT");
    assert_eq!(s(&bes[1], &["commandline"]), "%{parent} bko.be=void-6.12");
    assert_eq!(s(&bes[1], &["commandline_from"]), "zroot/ROOT/void-6.12");
    // One hinted source: the inactive BE, read-only.
    let src = arr(&v, &["storage", "backup_sources"]);
    let hinted: Vec<(&str, &str, &str)> = src.iter().filter(|b| b.get("from").unwrap().is_null()).map(|b| (s(b, &["path"]), s(b, &["mount", "device"]), s(b, &["mount", "options"]))).collect();
    assert_eq!(hinted, [("/run/bko-be/void-6.12", "zroot/ROOT/void-6.12", "ro,zfsutil,nodev,nosuid,noexec")]);

    // The plan.
    assert!(b.problems.is_empty(), "{:?}", b.problems);
    let text = |st: Stage| -> String { b.stage(st).iter().map(Cmd::shell).collect() };
    let asm = text(Stage::Assemble);
    let create = asm.lines().find(|l| l.starts_with("zpool create ")).expect(&asm);
    for w in ["-o compatibility=openzfs-2.3-linux", "-o autotrim=on", "-O mountpoint=none", "-R /mnt/bko-target zroot /dev/sda2"] {
        assert!(create.contains(w), "{w}: {create}");
    }
    assert!(!create.contains("bootfs"), "{create}");
    assert!(asm.contains("zpool reguid -g 999 zroot\n"), "{asm}");
    // The user properties, ':' in the name, spaces and %{parent} in the
    // value, quoted for the shell but otherwise as the source had them.
    let fmt = text(Stage::Format);
    assert!(fmt.contains("zfs create -u -o mountpoint=none -o 'org.zfsbootmenu:commandline=loglevel=4 console=tty0 console=ttyS0,115200n8' zroot/ROOT\n"), "{fmt}");
    assert!(fmt.contains("zfs create -u -o mountpoint=/ -o canmount=noauto -o 'org.zfsbootmenu:commandline=%{parent} bko.be=void-6.12' zroot/ROOT/void-6.12\n"), "{fmt}");
    let mk = b.stage(Stage::Format).iter().find(|c| c.argv.last().map(String::as_str) == Some("zroot/ROOT/void-6.12")).unwrap();
    assert!(mk.argv.contains(&"org.zfsbootmenu:commandline=%{parent} bko.be=void-6.12".to_string()), "{:?}", mk.argv);
    assert!(fmt.contains("zpool set bootfs=zroot/ROOT/void zroot\n"), "{fmt}");
    assert!(!fmt.contains('@'), "no snapshot or clone is made: {fmt}");
    assert!(b.notes.iter().any(|n| n == "ZFS: void-6.12 (zroot/ROOT/void-6.12) was a clone of void's snapshots; it comes back as a full copy (no snapshot is remade)"), "{:?}", b.notes);
    // Mounts: the booted BE at /, then the inactive one at run/bko-be.
    let mt = text(Stage::Mount);
    let root = mt.find("zfs mount zroot/ROOT/void\n").expect(&mt);
    let be = mt.find("mount -t zfs -o zfsutil zroot/ROOT/void-6.12 /mnt/bko-target/run/bko-be/void-6.12\n").expect(&mt);
    let esp = mt.find("mount -t vfat -o defaults /dev/sda1 /mnt/bko-target/boot/efi\n").expect(&mt);
    assert!(root < be && root < esp, "{mt}");
    assert!(!mt.contains("zfs mount zroot/ROOT/void-6.12"), "{mt}");
    // The boot stage: the BE taken away first, the booted BE's initramfs,
    // no loader install, both entries in the source's order.
    let bt = text(Stage::Boot);
    let um = bt.find("umount /mnt/bko-target/run/bko-be/void-6.12'\n").expect(&bt);
    let rm = bt.find("rmdir /mnt/bko-target/run/bko-be'\n").expect(&bt);
    let dracut = bt.find("dracut --force --kver").expect(&bt);
    assert!(um < rm && rm < dracut, "{bt}");
    assert_eq!(bt.matches("dracut --force").count(), 1, "{bt}");
    for no in ["grub", "bootctl", "refind", "xbps-reconfigure", "BOOTX64"] {
        assert!(!bt.contains(no), "{no}: {bt}");
    }
    let backup = bt.find("efibootmgr --quiet --create --disk /dev/sda --part 1 --label 'ZFSBootMenu (Backup)' --loader '\\EFI\\ZBM\\VMLINUZ-BACKUP.EFI'\n").expect(&bt);
    let main = bt.find("efibootmgr --quiet --create --disk /dev/sda --part 1 --label ZFSBootMenu --loader '\\EFI\\ZBM\\VMLINUZ.EFI'\n").expect(&bt);
    assert!(dracut < backup && backup < main, "{bt}");
    assert_eq!(bt.matches("efibootmgr").count(), 2, "{bt}");
    let sha = crate::bytes::sha256_hex(&zbm);
    assert!(
        b.notes.iter().any(|n| *n == format!("ZFSBootMenu: its image EFI/ZBM/VMLINUZ.EFI on the ESP at /boot/efi (version not recorded (its .osrel is its build system's, Void Linux), kernel {}, SHA-256 {sha}) comes back with the ESP; nothing is installed", sample::RELEASE_KERNEL)),
        "{:?}",
        b.notes
    );
    assert!(b.notes.iter().any(|n| n.starts_with("ZFSBootMenu: its image EFI/ZBM/VMLINUZ-BACKUP.EFI ")), "{:?}", b.notes);
    assert!(b.notes.iter().any(|n| n.starts_with("ZFSBootMenu: only the booted boot environment's initramfs is remade; void-6.12's /boot stay as restored")), "{:?}", b.notes);
    assert!(!b.notes.iter().any(|n| n.contains("BOOTX64.EFI")), "{:?}", b.notes);
    assert!(!bt.contains("cmp -s"), "no key file: {bt}");
    // --no-nvram: no entry, and a note that the copy can't boot alone.
    let (_, b) = zbm_plan(false, &["--no-nvram"], |_| {});
    let bt: String = b.stage(Stage::Boot).iter().map(Cmd::shell).collect();
    assert!(!bt.contains("efibootmgr"), "{bt}");
    assert!(b.notes.iter().any(|n| n.starts_with("ZFSBootMenu: --no-nvram: no NVRAM entry is made, and the ESP has no ZFSBootMenu image at EFI/BOOT/BOOTX64.EFI")), "{:?}", b.notes);
    // An entry with optional data (a source made with efibootmgr -u):
    // passed again as UCS-2 text; binary data from a file.
    let with_data = |extra: &[u8]| {
        let extra = extra.to_vec();
        move |v: &mut Value| {
            let Some(Value::Arr(es)) = v.at(&["boot", "efi", "entries"]).cloned() else { panic!() };
            let mut es = es;
            let Value::Obj(o) = &mut es[0] else { panic!() };
            for (k, x) in o.iter_mut() {
                match k.as_str() {
                    "load_option_hex" => *x = Value::from(format!("{}{}", x.as_str().unwrap(), extra.iter().map(|b| format!("{b:02x}")).collect::<String>())),
                    "optional_data_bytes" => *x = Value::from(extra.len() as u64),
                    _ => {}
                }
            }
            set_at(v, &["boot", "efi", "entries"], Value::Arr(es));
        }
    };
    let text_arg: Vec<u8> = "zbm.prefer=zroot/ROOT/void-6.12!".encode_utf16().chain([0]).flat_map(u16::to_le_bytes).collect();
    let (_, b) = zbm_plan(false, &[], with_data(&text_arg));
    let bt: String = b.stage(Stage::Boot).iter().map(Cmd::shell).collect();
    assert!(bt.contains("--label 'ZFSBootMenu (Backup)' --loader '\\EFI\\ZBM\\VMLINUZ-BACKUP.EFI' --unicode 'zbm.prefer=zroot/ROOT/void-6.12!'\n"), "{bt}");
    assert!(bt.contains("--label ZFSBootMenu --loader '\\EFI\\ZBM\\VMLINUZ.EFI'\n"), "{bt}");
    let (_, b) = zbm_plan(false, &[], with_data(&[1, 2, 0xff]));
    let bt: String = b.stage(Stage::Boot).iter().map(Cmd::shell).collect();
    let f = format!("{}/efi-optional-data-0000", crate::load::RUN_DIR);
    assert!(bt.contains(&format!("sh -c 'printf '\\''\\001\\002\\377'\\'' > {f}'\n")), "{bt}");
    assert!(bt.contains(&format!("--loader '\\EFI\\ZBM\\VMLINUZ-BACKUP.EFI' --append-binary-args {f}\n")), "{bt}");
}

/// What load refuses for ZFSBootMenu: the BEs' pool not in the layout, a
/// bootfs not among the datasets, no image on an ESP fstab mounts.
#[test]
fn zfsbootmenu_refusals() {
    let (_, b) = zbm_plan(false, &[], |v| set_at(v, &["boot", "zfsbootmenu", "pools"], crate::json::parse(r#"[{"name": "tank", "bootfs": null}]"#).unwrap()));
    assert!(b.problems.iter().any(|p| p == "ZFSBootMenu: its boot environments' pool tank isn't in the layout, so nothing it boots would be restored"), "{:?}", b.problems);
    let (_, b) = zbm_plan(false, &[], |v| set_at(v, &["boot", "zfsbootmenu", "pools"], crate::json::parse(r#"[{"name": "zroot", "bootfs": "zroot/ROOT/gone"}]"#).unwrap()));
    assert!(b.problems.iter().any(|p| p == "ZFSBootMenu: pool zroot's bootfs zroot/ROOT/gone isn't among the layout's datasets, so ZFSBootMenu would have no default boot environment"), "{:?}", b.problems);
    let (_, b) = zbm_plan(false, &[], |v| set_at(v, &["storage", "fstab"], Value::Arr(vec![])));
    assert!(
        b.problems.iter().any(|p| p == "ZFSBootMenu: no image of it is on an ESP that fstab mounts and this layout makes (EFI/ZBM/VMLINUZ-BACKUP.EFI on /boot/efi, EFI/ZBM/VMLINUZ.EFI on /boot/efi), so the restore wouldn't bring it back"),
        "{:?}",
        b.problems
    );
}

/// void-zbm-enc: the passphrase lives in /etc/zfs/zroot.key inside the
/// encrypted BE.  Without --key-file the plan says which file holds it;
/// with it, the pool is made with the given file, the restored key file is
/// checked against it, and keylocation and keysource come back as the
/// source's.
#[test]
fn zfsbootmenu_encrypted() {
    use crate::load::{commands::Cmd, Stage};
    let (v, b) = zbm_plan(true, &[], |_| {});
    assert_eq!(s(&v, &["boot", "loader", "name"]), "zfsbootmenu");
    assert_eq!(s(&v, &["storage", "zfs", "tools", "datasets", "0", "keylocation"]), "file:///etc/zfs/zroot.key");
    assert!(
        b.problems.iter().any(|p| p == "ZFS encryption root zroot: give its new key with --key-file zroot=FILE (the source keeps its passphrase in /etc/zfs/zroot.key, inside the encrypted datasets: give that passphrase)"),
        "{:?}",
        b.problems
    );
    let (_, b) = zbm_plan(true, &["--key-file", "zroot=/root/zroot-passphrase"], |_| {});
    assert!(b.problems.is_empty(), "{:?}", b.problems);
    let text = |st: Stage| -> String { b.stage(st).iter().map(Cmd::shell).collect() };
    let asm = text(Stage::Assemble);
    let create = asm.lines().find(|l| l.starts_with("zpool create ")).expect(&asm);
    for w in ["-O encryption=aes-256-gcm", "-O keyformat=passphrase", "-O keylocation=file:///root/zroot-passphrase", "-O org.zfsbootmenu:keysource=zroot/ROOT/void"] {
        assert!(create.contains(w), "{w}: {create}");
    }
    let bt = text(Stage::Boot);
    let check = bt.find("sh -c 'cmp -s /root/zroot-passphrase /mnt/bko-target/etc/zfs/zroot.key || { echo '\\''ZFS encryption root zroot: the restored /etc/zfs/zroot.key is not the key --key-file zroot=/root/zroot-passphrase gave").expect(&bt);
    let loc = bt.find("zfs set keylocation=file:///etc/zfs/zroot.key zroot\n").expect(&bt);
    let dracut = bt.find("dracut --force --kver").expect(&bt);
    assert!(bt.find("umount /mnt/bko-target/run/bko-be/void-6.12'\n").unwrap() < check && check < loc && loc < dracut, "{bt}");
    assert_eq!(bt.matches("cmp -s").count(), 1, "{bt}");
}

/// A dump's JSON object member K, for writing (tests that reshape a dump).
fn member<'a>(v: &'a mut Value, k: &str) -> &'a mut Value {
    let Value::Obj(o) = v else { panic!("no object for {k}") };
    &mut o.iter_mut().find(|(n, _)| n == k).unwrap_or_else(|| panic!("no {k}")).1
}

fn corpus_dump(name: &str) -> Value {
    crate::json::parse(&std::fs::read_to_string(format!("{}/tests/corpus/{name}/src.json", env!("CARGO_MANIFEST_DIR"))).unwrap()).unwrap()
}

/// Off Linux nothing of crate::zfs_be applies: FreeBSD's inactive bectl
/// BE (freebsd14's) gets no backup source at dump, and load, even given
/// one, mounts nothing under run/bko-be and leaves no /run behind.
#[test]
fn freebsd_bes_stay_empty() {
    use crate::load::{commands::Cmd, Stage};
    let mut v = corpus_dump("freebsd14");
    let tools = v.at(&["storage", "zfs", "tools"]).unwrap().clone();
    let (bes, src, w) = crate::zfs_be::dump_sources(Some(crate::other::Family::FreeBsd), &tools);
    assert!(bes.is_empty() && src.is_empty() && w.is_empty());
    // (On Linux the same pool would have one: the gate is what keeps it out.)
    let (_, src, _) = crate::zfs_be::dump_sources(None, &tools);
    assert_eq!(src.len(), 1);
    assert_eq!(src[0].path, "/run/bko-be/14.5-RELEASE_2026-11-06_085140");
    let hinted = src[0].to_json();
    let Value::Obj(st) = member(&mut v, "storage") else { unreachable!() };
    st.retain(|(k, _)| k != "backup_sources");
    st.push(("backup_sources".into(), Value::Arr(vec![hinted])));
    let l = crate::load::layout::Layout::from_json(v).unwrap();
    let Ok(crate::load::Parsed::Run(o)) = crate::load::parse_args(&["l.json".to_string()]) else { unreachable!() };
    let b = crate::load::commands::build(&l, &o, &crate::load::target::Mapping { pairs: vec![("vtbd0".into(), "vtbd0".into())], ..Default::default() });
    let all: String = b.stages.iter().flat_map(|(_, c)| c.iter().map(Cmd::shell)).collect();
    assert!(!all.contains("bko-be"), "{all}");
    assert!(b.stage(Stage::Mount).iter().all(|c| !c.argv.iter().any(|a| a.contains("14.5-RELEASE"))));
}

/// --pools on a family other than Linux: the glob file the restore names
/// is written there too.
#[test]
fn freebsd_pools_glob_file() {
    use crate::load::{commands::Cmd, Stage};
    let mut v = corpus_dump("freebsd14");
    let z = member(member(&mut v, "storage"), "zfs");
    let Value::Obj(o) = z else { unreachable!() };
    o.push(("left_out_paths".into(), Value::Arr(vec![Value::from("/data")])));
    let l = crate::load::layout::Layout::from_json(v).unwrap();
    let Ok(crate::load::Parsed::Run(o)) = crate::load::parse_args(&["l.json".to_string(), "--repo".into(), "/r".into()]) else { unreachable!() };
    let b = crate::load::commands::build(&l, &o, &crate::load::target::Mapping { pairs: vec![("vtbd0".into(), "vtbd0".into())], ..Default::default() });
    let rs = b.stage(Stage::Restore);
    let w = rs.iter().position(|c| c.write.as_deref() == Some("/run/bko-holt/restore.globs")).expect("the glob file is written");
    assert_eq!(rs[w].stdin.as_deref(), Some("!/data/**\n"));
    let r = rs.iter().position(|c| c.argv.iter().any(|a| a.ends_with("restore.globs")) && c.write.is_none()).expect("the restore names it");
    assert!(w < r, "{}", rs.iter().map(Cmd::shell).collect::<String>());
}

/// --pools with more on a left-out disk than the pool (an ext4 /srv), a
/// left-out pool on no disk of the layout, one with no GUID.
#[test]
fn truenas_pools_refusals() {
    use crate::load::{commands::Cmd, Stage};
    let (t, runner) = truenas(true);
    let v = run(&t, true, runner);
    let keep = ["boot-pool".to_string()];
    // vdc2, ext4 at /srv, beside tank's vdc1.
    let mut w = v.clone();
    {
        let st = member(&mut w, "storage");
        let Value::Arr(disks) = member(st, "disks") else { unreachable!() };
        let vdc = disks.iter_mut().find(|d| d.get("name").and_then(Value::as_str) == Some("vdc")).unwrap();
        let Value::Arr(parts) = member(vdc, "partitions") else { unreachable!() };
        parts.push(crate::json::parse(&format!(r#"{{"name": "vdc2", "number": 2, "start_lba": 194560, "sectors": 1024, "type": "{LINUX}", "content": {{"type": "ext4", "uuid": "5e5e0000-0000-4000-8000-000000000099"}}}}"#)).unwrap());
        let Value::Arr(src) = member(st, "backup_sources") else { unreachable!() };
        src.push(crate::json::parse(r#"{"path": "/srv", "from": "/srv", "device": "vdc2", "fstype": "ext4", "source": "/dev/vdc2", "subvol": null}"#).unwrap());
    }
    let l = crate::load::layout::Layout::from_json(w).unwrap();
    let only = l.only_pools(&keep);
    assert!(only.problems.is_empty(), "{:?}", only.problems);
    assert!(only.notes.iter().any(|n| n == "--pools: the left-out disks also hold /srv (vdc2), which aren't made or restored either"), "{:?}", only.notes);
    let Ok(crate::load::Parsed::Run(o)) = crate::load::parse_args(&["l.json", "--pools", "boot-pool", "--repo", "/r"].map(String::from)) else { unreachable!() };
    let b = crate::load::commands::build(&only.layout, &o, &crate::load::target::Mapping { pairs: vec![("vda".into(), "sda".into()), ("vdb".into(), "sdb".into())], ..Default::default() });
    let rs: String = b.stage(Stage::Restore).iter().map(Cmd::shell).collect();
    assert!(rs.contains("!/srv/**\n") && rs.contains("!/mnt/tank/**\n"), "{rs}");
    assert!(!b.stage(Stage::Format).iter().map(Cmd::shell).collect::<String>().contains("5e5e0000"));
    // tank on no disk of the layout (its label unread).
    let mut w = v.clone();
    {
        let Value::Arr(disks) = member(member(&mut w, "storage"), "disks") else { unreachable!() };
        disks.retain(|d| d.get("name").and_then(Value::as_str) != Some("vdc"));
    }
    let only = crate::load::layout::Layout::from_json(w).unwrap().only_pools(&keep);
    assert!(only.problems.iter().any(|p| p.starts_with("--pools: pool tank is to be left untouched, but no disk of the layout holds a vdev of it")), "{:?}", only.problems);
    // tank with no GUID in the dump.
    let mut w = v.clone();
    {
        let Value::Arr(labels) = member(member(member(&mut w, "storage"), "zfs"), "pools_from_labels") else { unreachable!() };
        labels.retain(|p| p.get("name").and_then(Value::as_str) != Some("tank"));
    }
    let only = crate::load::layout::Layout::from_json(w).unwrap().only_pools(&keep);
    assert!(only.problems.iter().any(|p| p.starts_with("--pools: pool tank has no GUID in the dump")), "{:?}", only.problems);
    assert!(only.guids.is_empty());
}

/// A TrueNAS bootfs that isn't the booted BE: dump warns, load refuses,
/// naming both; a boot-pool disk with no target is refused too.
#[test]
fn truenas_bootfs_and_targets() {
    let (t, runner) = truenas(false);
    let runner = runner.with(
        "zpool get -H -p -o property,value,source all boot-pool",
        "size\t31138512896\t-\nashift\t12\tlocal\ncachefile\tnone\tlocal\ncompatibility\tgrub2\tlocal\nbootfs\tboot-pool/ROOT/25.10.7\tlocal\nfeature@async_destroy\tenabled\tlocal\n",
    );
    let v = run(&t, true, runner);
    let w: Vec<&str> = arr(&v, &["warnings"]).iter().filter_map(Value::as_str).collect();
    assert!(w.iter().any(|x| x.starts_with("ZFS pool boot-pool: its bootfs is boot-pool/ROOT/25.10.7, but the booted boot environment is boot-pool/ROOT/25.10.8")), "{w:?}");
    assert!(!arr(&v, &["storage", "backup_sources"]).iter().any(|b| s(b, &["path"]).starts_with("/run/bko-be/25.10.7")));
    let l = crate::load::layout::Layout::from_json(v).unwrap();
    let Ok(crate::load::Parsed::Run(o)) = crate::load::parse_args(&["l.json".to_string()]) else { unreachable!() };
    let b = crate::load::commands::build(&l, &o, &crate::load::target::Mapping { pairs: vec![("vda".into(), "sda".into())], ..Default::default() });
    assert!(b.problems.iter().any(|p| p.starts_with("ZFS: the pool's bootfs is boot-pool/ROOT/25.10.7, but the dump was made on boot environment boot-pool/ROOT/25.10.8")), "{:?}", b.problems);
    // The mirror with vdb unmapped.
    let (t, runner) = truenas(true);
    let l = crate::load::layout::Layout::from_json(run(&t, true, runner)).unwrap();
    let b = crate::load::commands::build(&l, &o, &crate::load::target::Mapping { pairs: vec![("vda".into(), "sda".into()), ("vdc".into(), "sdc".into())], ..Default::default() });
    assert!(b.problems.iter().any(|p| p == "TrueNAS: vdb holds a boot-pool vdev but has no target, so GRUB can't be installed on it"), "{:?}", b.problems);
}

// ---- Ubuntu Core 24 -------------------------------------------------------

const UC_DATA_UUID: &str = "0aa850ad-0ffb-41a5-ba7a-9479f0fb1d79";
const UC_SAVE_UUID: &str = "3472dc89-900a-4d67-8ac4-21ba0e88f040";
const UC_BOOT_UUID: &str = "179e8e2f-37a3-46c8-aa2d-352e2d4b6f72";
const UC_LUKS_DATA: &str = "b93c93b3-cf55-40e1-9972-c5b49656a810";
const UC_LUKS_SAVE: &str = "e1ba50bc-76db-4c8b-8e4d-df5b02ec295d";
const UC_SEED_PARTUUID: &str = "954a6c14-611b-4b9f-a025-0c0dcd9ac146";

/// An ext4 sample with label L and UUID U.
fn ext4_with(label: &str, uuid: &str) -> Vec<u8> {
    let mut img = ext::sample::image(0x4 | 0x8 | 0x10 | 0x20, 0x2 | 0x40 | 0x80 | 0x200, 0x1 | 0x2 | 0x400, label);
    let b: Vec<u8> = (0..16).map(|i| u8::from_str_radix(&uuid.replace('-', "")[2 * i..2 * i + 2], 16).unwrap()).collect();
    img[1024 + 104..1024 + 120].copy_from_slice(&b);
    img
}

/// Ubuntu Core 24 in run mode as the stock image installs it and
/// make-ubuntu-core.sh refreshes it (the uc24 and uc24-fde guests,
/// docs/design/ubuntu-core.md, "What U1 found"), shrunk: the pc gadget's
/// GPT (BIOS Boot with core.img, the MBR's boot.img, ubuntu-seed vfat
/// ABA0-ACE2, ubuntu-boot, ubuntu-save, ubuntu-data), `/` the core24 snap
/// on a loop device, the four partitions at snap-bootstrap's mount points
/// with snapd's bind mounts beside them, two recovery systems (the image's
/// 20240603 on pc-kernel 1803, and bko-20261114 on 3908), one kernel,
/// hello at two revisions.  FDE: ubuntu-save and ubuntu-data are LUKS2
/// (ubuntu-save-enc, ubuntu-data-enc) opened as dm-1 and dm-0.
fn snapcore_tree(fde: bool) -> Tree {
    use crate::efi::GLOBAL;
    let t = Tree::new(if fde { "scn-uc24-fde" } else { "scn-uc24" });
    let mut img = vec![0u8; 96 * MIB];
    write_gpt(
        &mut img,
        512,
        &GptSpec {
            disk_guid: "9c1b5d6e-0000-4000-8000-0000000000c0",
            parts: vec![
                (BIOS_BOOT, "2f78214f-e265-4e5f-aeb8-8c9d13caa2ce", 2048, 4095, 0, "BIOS Boot"),
                (ESP, UC_SEED_PARTUUID, 4096, 77823, 0, "ubuntu-seed"),
                (LINUX, "5ec61a69-7e69-4f28-b0e6-4ad2ba4d689c", 77824, 86015, 0, "ubuntu-boot"),
                (LINUX, "fb2212f5-3d65-4656-89ae-18b3bb660bfb", 86016, 126975, 0, "ubuntu-save"),
                (LINUX, "ca4a89d0-44fa-4fa8-a211-f1ca46ad4f55", 126976, 188415, 0, "ubuntu-data"),
            ],
        },
    );
    // pc-boot.img in the MBR's code (the GPT's protective entry after it),
    // pc-core.img in BIOS Boot.
    img[..440].copy_from_slice(&grub::grub2_boot_img(2048)[..440]);
    let mut core = grub::grub2_diskboot_img();
    core.extend(vec![0x5au8; 3 * 512]);
    put(&mut img, 2048, &core);
    put(&mut img, 4096, &fat::sample::fat32(73728, 0xaba0_ace2, "ubuntu-seed"));
    put(&mut img, 77824, &ext4_with("ubuntu-boot", UC_BOOT_UUID));
    let (save, data) = if fde {
        (luks::sample::luks2(UC_LUKS_SAVE, "ubuntu-save-enc"), luks::sample::luks2(UC_LUKS_DATA, "ubuntu-data-enc"))
    } else {
        (ext4_with("ubuntu-save", UC_SAVE_UUID), ext4_with("ubuntu-data", UC_DATA_UUID))
    };
    put(&mut img, 86016, &save);
    put(&mut img, 126976, &data);
    t.disk("vda", "254:0", &img, 512)
        .partition("vda", "vda1", "254:1", 1, 2048, 2048, &img)
        .partition("vda", "vda2", "254:2", 2, 4096, 73728, &img)
        .partition("vda", "vda3", "254:3", 3, 77824, 8192, &img)
        .partition("vda", "vda4", "254:4", 4, 86016, 40960, &img)
        .partition("vda", "vda5", "254:5", 5, 126976, 61440, &img)
        .file("/sys/block/vda/device/model", "")
        .disk("loop0", "7:0", &[0u8; 4096], 512)
        .file("/sys/block/loop0/loop/backing_file", "/var/lib/snapd/snaps/pc_196.snap\n")
        .disk("loop1", "7:1", &[0u8; 4096], 512)
        .file("/sys/block/loop1/loop/backing_file", "/var/lib/snapd/snaps/core24_2307.snap\n");
    let (dsrc, ssrc, ddev, sdev) = if fde {
        let dm = |dev: &str, devno: &str, name: &str, luks_uuid: &str, part: &str, image: Vec<u8>| {
            t.disk(dev, devno, &image, 512)
                .file(&format!("/sys/block/{dev}/dm/name"), format!("{name}\n"))
                .file(&format!("/sys/block/{dev}/dm/uuid"), format!("CRYPT-LUKS2-{}-{name}\n", luks_uuid.replace('-', "")))
                .file(&format!("/sys/block/{dev}/slaves/{part}"), "")
                .file(&format!("/sys/block/vda/{part}/holders/{dev}"), "")
                .symlink(&format!("/dev/mapper/{name}"), &format!("../{dev}"));
        };
        // snap-bootstrap's names: a new UUID each boot.
        dm("dm-0", "253:0", "ubuntu-data-c9cf40c5-5578-4f1e-a5ab-f65db95b8388", UC_LUKS_DATA, "vda5", ext4_with("ubuntu-data", UC_DATA_UUID));
        dm("dm-1", "253:1", "ubuntu-save-49a0d13a-7664-46aa-8520-97d6f9bc9b51", UC_LUKS_SAVE, "vda4", ext4_with("ubuntu-save", UC_SAVE_UUID));
        ("/dev/mapper/ubuntu-data-c9cf40c5-5578-4f1e-a5ab-f65db95b8388", "/dev/mapper/ubuntu-save-49a0d13a-7664-46aa-8520-97d6f9bc9b51", "253:0", "253:1")
    } else {
        ("/dev/vda5", "/dev/vda4", "254:5", "254:4")
    };
    t.file(
        "/proc/self/mountinfo",
        format!(
            "26 1 7:1 / / ro,relatime shared:1 - squashfs /dev/loop1 ro\n\
             27 26 254:3 / /run/mnt/ubuntu-boot rw,relatime shared:2 - ext4 /dev/vda3 rw\n\
             28 26 254:2 / /run/mnt/ubuntu-seed rw,relatime shared:3 - vfat /dev/vda2 rw,fmask=0022,dmask=0022\n\
             29 26 {ddev} / /run/mnt/data rw,relatime shared:4 - ext4 {dsrc} rw\n\
             30 26 {sdev} / /run/mnt/ubuntu-save rw,relatime shared:5 - ext4 {ssrc} rw\n\
             31 26 {ddev} / /writable rw,relatime shared:4 - ext4 {dsrc} rw\n\
             32 26 {ddev} /system-data/var/lib/snapd /var/lib/snapd rw,relatime shared:6 - ext4 {dsrc} rw\n\
             33 32 254:2 / /var/lib/snapd/seed rw,relatime shared:3 - vfat /dev/vda2 rw,fmask=0022,dmask=0022\n\
             34 32 {sdev} / /var/lib/snapd/save rw,relatime shared:5 - ext4 {ssrc} rw\n\
             35 26 7:0 / /run/mnt/gadget ro,relatime shared:7 - squashfs /dev/loop0 ro\n"
        ),
    );
    let d = "/run/mnt/data/system-data/var/lib/snapd";
    let v_ = |n: &str| format!("/sys/firmware/efi/efivars/{n}-{GLOBAL}");
    t.file("/etc/os-release", crate::osrel::tests::UBUNTU_CORE)
        .file("/usr/lib/os-release", crate::osrel::tests::UBUNTU_CORE)
        // What the running system's /etc/fstab holds (the real dumps: the
        // base snap's binds and tmpfs, none on the four partitions).
        .file("/etc/fstab", "rootfs / rootfs defaults 0 0\nnone /home none bind 0 0\ntmpfs /tmp tmpfs defaults 0 0\nnone /var/lib/snapd none bind 0 0\n")
        .file("/proc/sys/kernel/osrelease", "6.8.0-149-generic\n")
        .file("/proc/cmdline", "snapd_recovery_mode=run console=ttyS0,115200n8 console=tty1 panic=-1\n")
        .file(&format!("{d}/modeenv"), crate::snapcore::tests::MODEENV_TEXT)
        .file(&format!("{d}/state.json"), r#"{"data": {"snaps": {"core24": {"type": "base", "sequence": {"revisions": [{"snap": {"name": "core24", "revision": 423}}, {"snap": {"name": "core24", "revision": 2307}}]}, "current": 2307}, "hello": {"type": "app", "sequence": {"revisions": [{"snap": {"name": "hello", "revision": 42}}, {"snap": {"name": "hello", "revision": 29}}]}, "current": 29}, "pc": {"sequence": {"revisions": [{"snap": {"name": "pc", "revision": 178}}, {"snap": {"name": "pc", "revision": 196}}]}, "current": 196}, "pc-kernel": {"sequence": {"revisions": [{"snap": {"name": "pc-kernel", "revision": 1803}}, {"snap": {"name": "pc-kernel", "revision": 3908}}]}, "current": 3908}}}}"#);
    for f in ["core24_423", "core24_2307", "hello_42", "hello_29", "pc_178", "pc_196", "pc-kernel_1803", "pc-kernel_3908"] {
        t.file(&format!("{d}/snaps/{f}.snap"), "hsqs");
    }
    t.file("/run/mnt/data/bko-mark", "data\n")
        .file("/run/mnt/ubuntu-save/bko-mark", "save\n")
        .file("/run/mnt/ubuntu-save/device/fde/marker", if fde { "m" } else { "" })
        .file("/run/mnt/ubuntu-boot/device/model", crate::snapcore::tests::MODEL_TEXT)
        .file("/run/mnt/ubuntu-boot/EFI/boot/grubx64.efi", vec![2u8; 400])
        .file("/run/mnt/ubuntu-boot/EFI/ubuntu/grub.cfg", "# Snapd-Boot-Config-Edition: 3\n")
        .file("/run/mnt/ubuntu-boot/EFI/ubuntu/grubenv", crate::snapcore::tests::grubenv(&["kernel_status=", "snapd_extra_cmdline_args=", "snapd_full_cmdline_args=console=ttyS0,115200n8 console=tty1 panic=-1"]))
        .file("/run/mnt/ubuntu-boot/EFI/ubuntu/pc-kernel_3908.snap/kernel.efi", vec![9u8; 500])
        .file("/run/mnt/ubuntu-boot/EFI/ubuntu/pc-kernel_1803.snap/kernel.efi", vec![8u8; 500])
        .symlink("/run/mnt/ubuntu-boot/EFI/ubuntu/kernel.efi", "pc-kernel_3908.snap/kernel.efi")
        .file("/run/mnt/ubuntu-seed/EFI/boot/bootx64.efi", vec![1u8; 300])
        .file("/run/mnt/ubuntu-seed/EFI/boot/fbx64.efi", vec![3u8; 300])
        .file("/run/mnt/ubuntu-seed/EFI/ubuntu/shimx64.efi", vec![1u8; 300])
        .file("/run/mnt/ubuntu-seed/EFI/ubuntu/grubx64.efi", vec![2u8; 400])
        .file("/run/mnt/ubuntu-seed/EFI/ubuntu/bootx64.csv", "shimx64.efi,ubuntu\n")
        .file("/run/mnt/ubuntu-seed/EFI/ubuntu/grub.cfg", "# Snapd-Boot-Config-Edition: 2\n")
        .file("/run/mnt/ubuntu-seed/EFI/ubuntu/grubenv", crate::snapcore::tests::grubenv(&["snapd_recovery_mode=run", "snapd_recovery_system=", "snapd_good_recovery_systems=20240603,bko-20261114", "recovery_system_status=", "try_recovery_system="]));
    for (label, kernel) in [("20240603", "pc-kernel_1803"), ("bko-20261114", "pc-kernel_3908")] {
        t.file(&format!("/run/mnt/ubuntu-seed/systems/{label}/model"), crate::snapcore::tests::MODEL_TEXT)
            .file(&format!("/run/mnt/ubuntu-seed/systems/{label}/grubenv"), crate::snapcore::tests::grubenv(&[&format!("snapd_recovery_kernel=/snaps/{kernel}.snap"), "snapd_extra_cmdline_args=", "snapd_full_cmdline_args=console=ttyS0 console=tty1 panic=-1"]))
            .dir(&format!("/run/mnt/ubuntu-seed/systems/{label}/assertions"));
    }
    for f in ["core24_423", "core24_2307", "pc_178", "pc_196", "pc-kernel_1803", "pc-kernel_3908", "snapd_21759", "snapd_28332"] {
        t.file(&format!("/run/mnt/ubuntu-seed/snaps/{f}.snap"), "hsqs");
    }
    if fde {
        t.file("/run/mnt/ubuntu-boot/device/fde/ubuntu-data.sealed-key", vec![0u8; 64])
            .file("/run/mnt/ubuntu-seed/device/fde/ubuntu-data.recovery.sealed-key", vec![0u8; 64])
            .file("/run/mnt/ubuntu-seed/device/fde/ubuntu-save.recovery.sealed-key", vec![0u8; 64]);
    }
    // shim's fallback's entry, from the restored seed (U1).
    t.symlink(&format!("/dev/disk/by-partuuid/{UC_SEED_PARTUUID}"), "../../vda2")
        .file("/sys/firmware/efi/fw_platform_size", "64\n")
        .file(&v_("Boot0007"), efi_load_option("ubuntu", 2, 4096, 73728, UC_SEED_PARTUUID, "\\EFI\\ubuntu\\shimx64.efi"))
        .file(&v_("BootOrder"), [7u8, 0, 0, 0, 7, 0])
        .file(&v_("BootCurrent"), [6u8, 0, 0, 0, 7, 0])
        .file(&v_("SecureBoot"), [6u8, 0, 0, 0, 1]);
    t
}

/// The commands a dump of Ubuntu Core runs: the base snap's os-release,
/// `snap changes`, and `cryptsetup luksHeaderBackup` (which writes the
/// first bytes of the device, as far as the sample's header reaches).
struct SnapcoreRunner {
    inner: FakeRunner,
    root: std::path::PathBuf,
}

impl crate::sys::Runner for SnapcoreRunner {
    fn run(&self, prog: &str, args: &[&str]) -> Option<String> {
        if prog == "cryptsetup" && args.first() == Some(&"luksHeaderBackup") {
            let dev = std::fs::read(self.root.join(args[1].trim_start_matches('/'))).ok()?;
            std::fs::write(self.root.join(args[3].trim_start_matches('/')), &dev[..dev.len().min(2 * 16384 + 16744448)]).ok()?;
            return Some(String::new());
        }
        self.inner.run(prog, args)
    }
    fn installed(&self, prog: &str) -> bool {
        self.inner.installed(prog)
    }
}

fn snapcore_sys(t: &Tree, changes: &str) -> Sys {
    let base = "/run/mnt/data/system-data/var/lib/snapd/snaps/core24_2307.snap";
    let inner = FakeRunner::default()
        .with(&format!("unsquashfs -cat {base} usr/lib/os-release"), crate::osrel::tests::UBUNTU_CORE)
        .with("snap changes", &format!("ID   Status  Spawn               Ready               Summary\n1    Done    today at 12:30 UTC  today at 12:31 UTC  Initialize system state\n{changes}"));
    Sys::new(t.root(), Box::new(SnapcoreRunner { inner, root: t.root().to_path_buf() }), true)
}

/// `run`, through SYS (its runner a SnapcoreRunner).
fn snapcore_run(sys: &Sys) -> Value {
    let os = crate::osrel::read(sys);
    crate::osrel::check_supported(os.as_ref()).unwrap();
    let v = crate::report::build(sys, os.as_ref(), "2026-11-14T00:00:00Z");
    assert_eq!(crate::json::parse(&v.compact()).unwrap().compact(), v.compact());
    if let Err(e) = crate::bugreport::check_plan(&v.pretty()) {
        panic!("redaction: {e}");
    }
    v
}

fn snapcore_plan(v: &Value, extra: &[&str]) -> crate::load::commands::Built {
    use crate::load::{commands, layout::Layout, target::Mapping, Parsed};
    let l = Layout::from_json(v.clone()).unwrap();
    let mut a = vec!["/run/x/l.json".to_string()];
    a.extend(extra.iter().map(|s| s.to_string()));
    let Ok(Parsed::Run(o)) = crate::load::parse_args(&a) else { unreachable!() };
    commands::build(&l, &o, &Mapping { pairs: vec![("vda".into(), "vdb".into())], ..Mapping::default() })
}

/// The plain uc24: boot.snapcore (docs/design/ubuntu-core.md, U2) from
/// modeenv, the model, the seed and ubuntu-boot; os-release from the base
/// snap; every snap revision; the gadget's boot areas; backup_sources the
/// four roots in place of the base snap and its binds.  Planned (U3): the
/// gadget's partitions exactly, every filesystem with its label and UUID
/// (the seed's volume ID ABA0-ACE2), the four at snap-bootstrap's mount
/// points, and a boot stage that regenerates nothing: the check, the
/// boot areas, the log; no grub-install, no efibootmgr.
#[test]
fn snapcore_guest() {
    use crate::load::{commands::Cmd, Stage};
    let t = snapcore_tree(false);
    let v = snapcore_run(&snapcore_sys(&t, ""));
    assert!(arr(&v, &["warnings"]).is_empty(), "{:?}", v.get("warnings"));
    assert_eq!(s(&v, &["os", "id"]), "ubuntu-core");
    assert_eq!(crate::snapcore::refusal(&v), None);
    let sc = v.at(&["boot", "snapcore"]).expect("boot.snapcore");
    assert_eq!(sc.get("encrypted"), Some(&Value::Bool(false)));
    let roles: Vec<(&str, &str, &str, &str)> = arr(sc, &["partitions"]).iter().map(|p| (s(p, &["role"]), s(p, &["device"]), s(p, &["fs_label"]), s(p, &["at"]))).collect();
    assert_eq!(
        roles,
        vec![
            ("seed", "vda2", "ubuntu-seed", "/run/mnt/ubuntu-seed"),
            ("boot", "vda3", "ubuntu-boot", "/run/mnt/ubuntu-boot"),
            ("save", "vda4", "ubuntu-save", "/run/mnt/ubuntu-save"),
            ("data", "vda5", "ubuntu-data", "/run/mnt/data")
        ]
    );
    assert_eq!(s(sc, &["partitions", "0", "fs_uuid"]), "ABA0-ACE2");
    assert_eq!(s(sc, &["partitions", "0", "partuuid"]), UC_SEED_PARTUUID);
    assert_eq!(s(sc, &["modeenv", "mode"]), "run");
    assert_eq!(arr(sc, &["modeenv", "current_recovery_systems"]).len(), 2);
    assert_eq!(s(sc, &["modeenv", "current_kernels", "0"]), "pc-kernel_3908.snap");
    assert_eq!((s(sc, &["model", "gadget"]), s(sc, &["model", "grade"]), s(sc, &["model", "base"])), ("pc", "signed", "core24"));
    assert!(sc.at(&["model", "storage_safety"]).is_some_and(Value::is_null));
    assert_eq!((s(sc, &["os_release", "id"]), s(sc, &["os_release", "version_id"])), ("ubuntu-core", "24"));
    assert!(s(sc, &["os_release", "source"]).starts_with("base snap "));
    assert_eq!(s(sc, &["boot", "run_kernel"]), "pc-kernel_3908.snap/kernel.efi");
    assert_eq!(arr(sc, &["boot", "kernels"]).len(), 2);
    assert_eq!(s(sc, &["seed", "snapd_recovery_mode"]), "run");
    assert_eq!((s(sc, &["seed", "systems", "0", "label"]), s(sc, &["seed", "systems", "0", "kernel"])), ("20240603", "/snaps/pc-kernel_1803.snap"));
    assert_eq!(s(sc, &["seed", "systems", "1", "kernel"]), "/snaps/pc-kernel_3908.snap");
    let hello = arr(sc, &["snaps"]).iter().find(|x| x.get("name").and_then(Value::as_str) == Some("hello")).unwrap();
    assert_eq!((s(hello, &["current"]), arr(hello, &["revisions"]).len(), arr(hello, &["files"]).len()), ("29", 2, 2));
    assert_eq!(u(sc, &["changes_in_progress"]), 0);
    assert_eq!((s(sc, &["boot_areas", "disk"]), s(sc, &["boot_areas", "mbr_code_kind"])), ("vda", "grub2"));
    assert_eq!(u(sc, &["boot_areas", "bios_boot", "image_bytes"]), 2048);
    assert!(v.at(&["storage", "luks_headers"]).is_none());
    // backup_sources: the four roots, live, with the mounts an offline
    // backup makes; not / (the base snap) nor the binds.
    let bs = arr(&v, &["storage", "backup_sources"]);
    let paths: Vec<(&str, &str, &str)> = bs.iter().map(|b| (s(b, &["path"]), s(b, &["from"]), s(b, &["mount", "device"]))).collect();
    assert_eq!(
        paths,
        vec![
            ("/run/mnt/ubuntu-seed", "/run/mnt/ubuntu-seed", "/dev/disk/by-uuid/ABA0-ACE2"),
            ("/run/mnt/ubuntu-boot", "/run/mnt/ubuntu-boot", "/dev/disk/by-uuid/179e8e2f-37a3-46c8-aa2d-352e2d4b6f72"),
            ("/run/mnt/ubuntu-save", "/run/mnt/ubuntu-save", "/dev/disk/by-uuid/3472dc89-900a-4d67-8ac4-21ba0e88f040"),
            ("/run/mnt/data", "/run/mnt/data", "/dev/disk/by-uuid/0aa850ad-0ffb-41a5-ba7a-9479f0fb1d79")
        ]
    );
    assert_eq!(s(&bs[3], &["mount", "options"]), "ro,noload,nodev,nosuid,noexec");
    assert!(v.at(&["storage", "physical_root"]).is_some_and(Value::is_null));

    let b = snapcore_plan(&v, &[]);
    assert!(b.problems.is_empty(), "{:?}", b.problems);
    assert_eq!(crate::other::restored_marker(&v), "run/mnt/data/system-data/var/lib/snapd/modeenv");
    assert!(b.notes.iter().any(|n| n.starts_with("Ubuntu Core (unencrypted)")), "{:?}", b.notes);
    assert!(!b.notes.iter().any(|n| n.starts_with("fstab's")), "{:?}", b.notes);
    let text = |st: Stage| b.stage(st).iter().map(Cmd::shell).collect::<String>();
    let part = text(Stage::Partition);
    for l in [
        "/dev/vdb1 : start=2048, size=2048, type=21686148-6449-6E6F-744E-656564454649, uuid=2F78214F-E265-4E5F-AEB8-8C9D13CAA2CE, name=\"BIOS Boot\"\n",
        "/dev/vdb2 : start=4096, size=73728, type=C12A7328-F81F-11D2-BA4B-00A0C93EC93B, uuid=954A6C14-611B-4B9F-A025-0C0DCD9AC146, name=\"ubuntu-seed\"\n",
        "/dev/vdb3 : start=77824, size=8192, type=0FC63DAF-8483-4772-8E79-3D69D8477DE4, uuid=5EC61A69-7E69-4F28-B0E6-4AD2BA4D689C, name=\"ubuntu-boot\"\n",
        "/dev/vdb4 : start=86016, size=40960, type=0FC63DAF-8483-4772-8E79-3D69D8477DE4, uuid=FB2212F5-3D65-4656-89AE-18B3BB660BFB, name=\"ubuntu-save\"\n",
        "/dev/vdb5 : start=126976, size=61440, type=0FC63DAF-8483-4772-8E79-3D69D8477DE4, uuid=CA4A89D0-44FA-4FA8-A211-F1CA46AD4F55, name=\"ubuntu-data\"\n",
    ] {
        assert!(part.contains(l), "{l}: {part}");
    }
    let fmt = text(Stage::Format);
    let line = |end: &str| fmt.lines().find(|l| l.starts_with("mk") && l.split_whitespace().any(|w| w == end)).unwrap_or_else(|| panic!("{end}: {fmt}")).to_string();
    let seed = line("/dev/vdb2");
    assert!(seed.starts_with("mkfs.fat") && seed.contains("-i ABA0ACE2") && seed.contains("-n ubuntu-seed"), "{seed}");
    for (end, label, uuid) in [("/dev/vdb3", "ubuntu-boot", UC_BOOT_UUID), ("/dev/vdb4", "ubuntu-save", UC_SAVE_UUID), ("/dev/vdb5", "ubuntu-data", UC_DATA_UUID)] {
        let l = line(end);
        assert!(l.contains(&format!("-L {label}")) && l.contains(&format!("-U {uuid}")), "{l}");
    }
    let m = text(Stage::Mount);
    for l in [
        "mount -t vfat -o defaults /dev/vdb2 /mnt/bko-target/run/mnt/ubuntu-seed\n",
        "mount -t ext4 -o defaults /dev/vdb3 /mnt/bko-target/run/mnt/ubuntu-boot\n",
        "mount -t ext4 -o defaults /dev/vdb4 /mnt/bko-target/run/mnt/ubuntu-save\n",
        "mount -t ext4 -o defaults /dev/vdb5 /mnt/bko-target/run/mnt/data\n",
    ] {
        assert!(m.contains(l), "{l}: {m}");
    }
    assert!(!m.contains("/mnt/bko-target\n"), "{m}");
    let bt = text(Stage::Boot);
    // The atimes of the tree under run/mnt are listed, and put back.
    for st in [Stage::Restore, Stage::Boot] {
        let x = text(st);
        assert!(x.contains("-path '\\''/mnt/bko-target/run/*'\\'' ! -path /mnt/bko-target/run/mnt ! -path '\\''/mnt/bko-target/run/mnt/*'\\''") || x.contains("no --repo"), "{x}");
    }
    assert_eq!(bt.matches("! -path /mnt/bko-target/run/mnt ").count(), 2, "{bt}");
    let run: String = bt.lines().filter(|l| !l.starts_with('#')).collect::<Vec<_>>().join("\n");
    for no in ["chroot", "grub-install", "update-grub", "grub-mkconfig", "efibootmgr", "update-initramfs", "dracut", "--rbind", "grub-editenv"] {
        assert!(!run.contains(no), "{no}: {bt}");
    }
    let pos = |x: &str| bt.find(x).unwrap_or_else(|| panic!("{x}: {bt}"));
    let check = pos("wk=pc-kernel_3908.snap; ws=");
    let areas = pos(" snapcore-boot-areas /run/x/l.json vda /dev/vdb");
    let log = pos("mkdir -p /mnt/bko-target/run/mnt/data/system-data/var/log/bko-holt\n");
    let back = pos("atimes.after");
    assert!(check < areas && areas < log && log < back, "{bt}");
    // --new-machine is for FDE; --new-uuids isn't restored.
    assert!(snapcore_plan(&v, &["--new-machine"]).problems.iter().any(|p| p.starts_with("--new-machine is for an encrypted Ubuntu Core")));
    assert!(snapcore_plan(&v, &["--new-uuids"]).problems.iter().any(|p| p.starts_with("Ubuntu Core with --new-uuids isn't restored")));
    // Not Ubuntu Core: --new-machine is refused.
    let lv = self::run(&luks_lvm_tree(), true, FakeRunner::default());
    assert!(snapcore_plan(&lv, &["--new-machine"]).problems.iter().any(|p| p == "--new-machine is for an encrypted Ubuntu Core; this layout isn't one"));
}

/// uc24-fde: both LUKS2 headers kept (dump writes them beside the layout,
/// their SHA-256 in storage.luks_headers), written back by
/// luksHeaderRestore and opened with the recovery key's key file: no
/// luksFormat, no luksAddKey.  The same-machine promise in the notes;
/// --new-machine points the restored seed at recover mode of the image's
/// recovery system.
#[test]
fn snapcore_fde_guest() {
    use crate::load::{commands::Cmd, Stage};
    let t = snapcore_tree(true);
    let v = snapcore_run(&snapcore_sys(&t, ""));
    let w = arr(&v, &["warnings"]);
    assert!(w.len() == 1 && w[0].as_str().unwrap().starts_with("Ubuntu Core: the LUKS2 headers are files in /run/bko-holt/luks-headers (RAM, gone at the next boot): keep them with this dump"), "{w:?}");
    let sc = v.at(&["boot", "snapcore"]).unwrap();
    assert_eq!(sc.get("encrypted"), Some(&Value::Bool(true)));
    let data = arr(sc, &["partitions"])[3].clone();
    assert_eq!((s(&data, &["luks", "label"]), s(&data, &["luks", "dm_device"]), s(&data, &["fs_label"])), ("ubuntu-data-enc", "dm-0", "ubuntu-data"));
    assert_eq!(arr(sc, &["save", "fde_files"]).len(), 1);
    assert_eq!(arr(sc, &["seed", "fde_files"]).len(), 2);
    let hs = arr(&v, &["storage", "luks_headers"]);
    let dev: Vec<(&str, &str)> = hs.iter().map(|h| (s(h, &["device"]), s(h, &["file"]))).collect();
    assert_eq!(dev, vec![("vda4", "luks2-header-e1ba50bc-76db-4c8b-8e4d-df5b02ec295d.img"), ("vda5", "luks2-header-b93c93b3-cf55-40e1-9972-c5b49656a810.img")]);
    let hdir = t.p("/run/bko-holt/luks-headers").to_string_lossy().into_owned();
    for h in hs {
        assert_eq!(crate::luksheader::check_file(&format!("{hdir}/{}", s(h, &["file"])), h), None);
    }
    // backup_sources: the opened volumes, the hints by the inner UUIDs.
    let bs = arr(&v, &["storage", "backup_sources"]);
    assert_eq!(s(&bs[3], &["source"]), "/dev/mapper/ubuntu-data-c9cf40c5-5578-4f1e-a5ab-f65db95b8388");
    assert_eq!(s(&bs[3], &["device"]), "dm-0");
    assert!(s(&bs[3], &["note"]).contains("bko-holt recovery-key-file"));

    let key = "/run/key";
    let kf = ["--luks-header-dir", &hdir, "--key-file", &format!("ubuntu-data-enc={key}"), "--key-file", &format!("ubuntu-save-enc={key}")];
    let b = snapcore_plan(&v, &kf);
    assert!(b.problems.is_empty(), "{:?}", b.problems);
    assert!(b.notes.iter().any(|n| n.contains("On the same machine (the same TPM and Secure Boot variables, dbx included) it boots unattended only if nothing was resealed since the backup")), "{:?}", b.notes);
    assert!(b.notes.iter().any(|n| n.contains("must be kept with the dump")), "{:?}", b.notes);
    // A data segment of a fixed size must fit whole (a dynamic one, only
    // its offset).
    let fixed = crate::json::parse(&v.compact().replace("\"size\":\"dynamic\"", "\"size\":\"999999999999\"")).unwrap();
    let p = snapcore_plan(&fixed, &kf).problems;
    assert!(p.iter().any(|x| x.starts_with("vda5: its kept LUKS2 header's data segment needs 1000016777215 bytes")), "{p:?}");
    let text = |b: &crate::load::commands::Built, st: Stage| b.stage(st).iter().map(Cmd::shell).collect::<String>();
    let asm = text(&b, Stage::Assemble);
    for l in [
        format!("cryptsetup luksHeaderRestore --batch-mode /dev/vdb4 --header-backup-file {hdir}/luks2-header-e1ba50bc-76db-4c8b-8e4d-df5b02ec295d.img\n"),
        format!("cryptsetup open --key-file={key} /dev/vdb4 ubuntu-save-49a0d13a-7664-46aa-8520-97d6f9bc9b51\n"),
        format!("cryptsetup luksHeaderRestore --batch-mode /dev/vdb5 --header-backup-file {hdir}/luks2-header-b93c93b3-cf55-40e1-9972-c5b49656a810.img\n"),
        format!("cryptsetup open --key-file={key} /dev/vdb5 ubuntu-data-c9cf40c5-5578-4f1e-a5ab-f65db95b8388\n"),
    ] {
        assert!(asm.contains(&l), "{l}: {asm}");
    }
    let cmds: String = asm.lines().filter(|l| !l.starts_with('#')).collect::<Vec<_>>().join("\n");
    for no in ["luksFormat", "luksAddKey", "token"] {
        assert!(!cmds.contains(no), "{no}: {asm}");
    }
    let fmt = text(&b, Stage::Format);
    assert!(fmt.lines().any(|l| l.starts_with("mke2fs") && l.contains("-L ubuntu-data") && l.contains(UC_DATA_UUID) && l.contains(" /dev/mapper/ubuntu-data-c9cf40c5-5578-4f1e-a5ab-f65db95b8388 ")), "{fmt}");
    let m = text(&b, Stage::Mount);
    assert!(m.contains("mount -t ext4 -o defaults /dev/mapper/ubuntu-data-c9cf40c5-5578-4f1e-a5ab-f65db95b8388 /mnt/bko-target/run/mnt/data\n"), "{m}");
    assert!(text(&b, Stage::Close).contains("cryptsetup close ubuntu-data-c9cf40c5-5578-4f1e-a5ab-f65db95b8388"));
    let bt = text(&b, Stage::Boot);
    assert!(!bt.contains("efibootmgr") && !bt.contains("grub-editenv"), "{bt}");

    // --new-machine: recover mode of the image's system, by grub-editenv on
    // the restored seed, after the check.
    let b = snapcore_plan(&v, &[&kf[..], &["--new-machine"]].concat());
    assert!(b.problems.is_empty(), "{:?}", b.problems);
    let bt = text(&b, Stage::Boot);
    let ed = bt.find("grub-editenv /mnt/bko-target/run/mnt/ubuntu-seed/EFI/ubuntu/grubenv set snapd_recovery_mode=recover snapd_recovery_system=20240603\n").unwrap_or_else(|| panic!("{bt}"));
    assert!(bt.find("wk=pc-kernel_3908.snap").unwrap() < ed);
    assert!(b.notes.iter().any(|n| n.contains("--new-machine: the restored seed's grubenv boots recover mode of 20240603")), "{:?}", b.notes);

    // Without the key, or the header file: refused, naming what to do.
    let p = snapcore_plan(&v, &["--luks-header-dir", &hdir]).problems;
    assert!(p.iter().any(|x| x.contains("vda5: its LUKS2 header is kept, so it opens only with a key it already has: give --key-file ubuntu-data-enc=FILE") && x.contains("bko-holt recovery-key-file FILE")), "{p:?}");
    let p = snapcore_plan(&v, &kf[2..]).problems;
    assert!(p.iter().any(|x| x.starts_with("vda5: the LUKS2 header the dump kept: /run/x/luks2-header-b93c93b3-cf55-40e1-9972-c5b49656a810.img")), "{p:?}");
    std::fs::write(format!("{hdir}/luks2-header-b93c93b3-cf55-40e1-9972-c5b49656a810.img"), b"LUKS").unwrap();
    let p = snapcore_plan(&v, &kf).problems;
    assert!(p.iter().any(|x| x.contains("isn't the header the dump kept (its SHA-256 differs)")), "{p:?}");
    let p = snapcore_plan(&v, &[&kf[..], &["--new-uuids"]].concat()).problems;
    assert!(p.iter().any(|x| x.contains("--new-uuids, and the dump kept its LUKS2 header")), "{p:?}");
    // A dump that kept no header (not root, no cryptsetup): refused.
    let mut w = v.clone();
    if let Value::Obj(o) = &mut w {
        for (k, x) in o.iter_mut() {
            if k == "storage" {
                if let Value::Obj(so) = x {
                    so.retain(|(k, _)| k != "luks_headers");
                }
            }
        }
    }
    let p = snapcore_plan(&w, &kf).problems;
    assert!(p.iter().any(|x| x.starts_with("Ubuntu Core: the dump kept no LUKS2 header of ubuntu-data (vda5)")), "{p:?}");
    assert!(snapcore_plan(&w, &kf).stage(Stage::Assemble).iter().any(|c| c.argv.get(1).is_some_and(|a| a == "luksFormat")), "the generic path, refused by the problem above");
}

/// What the dump refuses (docs/design/ubuntu-core.md, "dump", v1): not run
/// mode, a kernel being tried, changes in progress, another gadget, a
/// locked ubuntu-data; and load's own refusal of a resized seed.
#[test]
fn snapcore_refusals() {
    let t = snapcore_tree(false);
    let refused = |t: &Tree, changes: &str| -> String {
        let sys = snapcore_sys(t, changes);
        let v = crate::report::build(&sys, crate::osrel::read(&sys).as_ref(), "2026-11-14T00:00:00Z");
        crate::snapcore::refusal(&v).unwrap_or_default()
    };
    let r = refused(&t, "7    Doing   today at 13:01 UTC  -                   Auto-refresh snap \"pc-kernel\"\n");
    assert!(r.contains("`snap changes` shows 1 change(s) in progress (7 Doing)"), "{r}");
    let d = "/run/mnt/data/system-data/var/lib/snapd/modeenv";
    t.file(d, crate::snapcore::tests::MODEENV_TEXT.replace("mode=run", "mode=recover"));
    assert!(refused(&t, "").contains("modeenv says mode=recover: dump runs in run mode"));
    t.file(d, crate::snapcore::tests::MODEENV_TEXT);
    t.file("/run/mnt/ubuntu-boot/EFI/ubuntu/grubenv", crate::snapcore::tests::grubenv(&["kernel_status=try"]));
    assert!(refused(&t, "").contains("a kernel refresh is being tried (the run grubenv's kernel_status=try)"));
    t.file("/run/mnt/ubuntu-boot/EFI/ubuntu/grubenv", crate::snapcore::tests::grubenv(&["kernel_status="]));
    t.file("/run/mnt/ubuntu-boot/device/model", crate::snapcore::tests::MODEL_TEXT.replace("name: pc\n", "name: pi\n"));
    assert!(refused(&t, "").contains("the model's gadget is pi, not pc"));
    t.file("/run/mnt/ubuntu-boot/device/model", crate::snapcore::tests::MODEL_TEXT);
    assert_eq!(refused(&t, ""), "");
    // A locked ubuntu-data (recover mode of a system that didn't unlock it).
    let t = snapcore_tree(true);
    std::fs::remove_file(t.p("/sys/block/vda/vda5/holders/dm-0")).unwrap();
    let r = refused(&t, "");
    assert!(r.contains("ubuntu-data (vda5, ubuntu-data-enc) is encrypted and not open") && r.contains("bko-holt recovery-key-file KEY"), "{r}");
    // A header that couldn't be kept (no cryptsetup): refused.
    let t = snapcore_tree(true);
    let sys = Sys::new(t.root(), Box::new(FakeRunner::default().without("cryptsetup")), true);
    let v = crate::report::build(&sys, crate::osrel::read(&sys).as_ref(), "2026-11-14T00:00:00Z");
    let r = crate::snapcore::refusal(&v).unwrap_or_default();
    assert!(r.contains("the LUKS2 header of vda4 wasn't kept") && r.contains("the LUKS2 header of vda5 wasn't kept"), "{r}");
    assert!(crate::dump(&sys).is_err());
    // A seed of another size (a fit that took the wrong partition).
    let t = snapcore_tree(false);
    let v = snapcore_run(&snapcore_sys(&t, ""));
    let c = v.compact();
    let at = c.find("\"partlabel\":\"ubuntu-seed\"").unwrap();
    let from = c[..at].rfind("\"size_bytes\":37748736").unwrap();
    let v = crate::json::parse(&format!("{}\"size_bytes\":{}{}", &c[..from], 40u64 << 20, &c[from + "\"size_bytes\":37748736".len()..])).unwrap();
    let p = snapcore_plan(&v, &[]).problems;
    assert!(p.iter().any(|x| x.starts_with("Ubuntu Core: ubuntu-seed (vda2) would be 40 MiB instead of the gadget's 36 MiB")), "{p:?}");
}
