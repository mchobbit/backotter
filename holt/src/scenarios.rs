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
    // --no-nvram: init's bootctl without the EFI variables.
    let (t, runner) = proxmox(false);
    let (_, b) = proxmox_plan(&t, runner, false, &["--no-nvram"]);
    assert!(b.problems.is_empty(), "{:?}", b.problems);
    let bt: String = b.stage(Stage::Boot).iter().map(Cmd::shell).collect();
    assert!(bt.contains("unshare --mount --propagation private sh -c 'umount /mnt/bko-target/sys/firmware/efi/efivars 2>/dev/null; exec chroot /mnt/bko-target proxmox-boot-tool init /dev/disk/by-uuid/1234-ABCD'\n"), "{bt}");
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
        ("md_raid1", run(&md_raid1_tree(), true, FakeRunner::default())),
        ("zfs_root", run(&zfs, true, runner)),
        ("whole_disk_fs", run(&misc_tree(), true, FakeRunner::default())),
        ("kinoite_ostree", run(&kinoite_ostree(), true, kinoite_runner())),
        ("gentoo_thinkpad", run(&gentoo_thinkpad(), true, FakeRunner::default())),
        ("pikaos", run(&pikaos(), true, FakeRunner::default())),
        ("biglinux", run(&biglinux(), true, biglinux_runner())),
        ("anduinos", run(&anduinos(), true, anduinos_runner())),
        ("kali", run(&kali(), true, FakeRunner::default())),
        ("proxmox", { let (t, r) = proxmox(false); run(&t, true, r) }),
        ("proxmox_mirror", { let (t, r) = proxmox(true); run(&t, true, r) }),
        ("guix_uefi", run(&guix_uefi(), true, FakeRunner::default())),
        ("guix_bios", run(&guix_bios(), true, guix_bios_runner())),
    ]
}
