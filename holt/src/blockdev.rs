//! The kernel's view of block devices: /sys/block and its partitions, how
//! they stack (holders and slaves), and what udev recorded about each in
//! /run/udev/data (the blkid results and /dev/disk links, readable without
//! root).

use crate::sys::Sys;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub enum Kind {
    #[default]
    Disk,
    Partition,
    Dm,
    Md,
    Loop,
    Zram,
    Rom,
    Floppy,
    Nbd,
    Ram,
}

impl Kind {
    pub fn name(self) -> &'static str {
        match self {
            Kind::Disk => "disk",
            Kind::Partition => "partition",
            Kind::Dm => "dm",
            Kind::Md => "md",
            Kind::Loop => "loop",
            Kind::Zram => "zram",
            Kind::Rom => "rom",
            Kind::Floppy => "floppy",
            Kind::Nbd => "nbd",
            Kind::Ram => "ram",
        }
    }
}

#[derive(Debug, Clone, Default)]
pub struct Udev {
    pub props: Vec<(String, String)>,
    pub links: Vec<String>,
}

impl Udev {
    pub fn get(&self, k: &str) -> Option<&str> {
        self.props.iter().find(|(key, _)| key == k).map(|(_, v)| v.as_str())
    }
}

/// /run/udev/data/bMAJ:MIN: "E:KEY=VALUE" properties and "S:link" symlinks
/// (relative to /dev).
pub fn parse_udev(text: &str) -> Udev {
    let mut u = Udev::default();
    for line in text.lines() {
        if let Some(e) = line.strip_prefix("E:") {
            if let Some((k, v)) = e.split_once('=') {
                u.props.push((k.to_string(), v.to_string()));
            }
        } else if let Some(s) = line.strip_prefix("S:") {
            u.links.push(format!("/dev/{s}"));
        }
    }
    u.links.sort();
    u
}

#[derive(Debug, Clone, Default)]
pub struct BlockDev {
    pub name: String,
    pub kind: Kind,
    /// "MAJ:MIN".
    pub devno: String,
    pub size_bytes: u64,
    /// The whole device a partition belongs to.
    pub parent: Option<String>,
    pub partition_number: Option<u32>,
    /// In 512-byte units, as sysfs reports it.
    pub start_sectors: Option<u64>,
    pub holders: Vec<String>,
    pub slaves: Vec<String>,
    pub logical_block_size: u64,
    pub physical_block_size: u64,
    pub rotational: Option<bool>,
    pub removable: Option<bool>,
    pub read_only: Option<bool>,
    pub discard_max_bytes: Option<u64>,
    pub model: Option<String>,
    pub vendor: Option<String>,
    pub serial: Option<String>,
    pub wwid: Option<String>,
    pub firmware: Option<String>,
    pub dm_name: Option<String>,
    pub dm_uuid: Option<String>,
    pub loop_backing_file: Option<String>,
    pub udev: Udev,
}

impl BlockDev {
    pub fn dev_path(&self) -> String {
        format!("/dev/{}", self.name)
    }
    pub fn sysfs(&self) -> String {
        match &self.parent {
            Some(p) => format!("/sys/block/{p}/{}", self.name),
            None => format!("/sys/block/{}", self.name),
        }
    }
    /// The transport the disk hangs off, best guess.
    pub fn transport(&self) -> Option<String> {
        if self.name.starts_with("nvme") {
            return Some("nvme".into());
        }
        if self.name.starts_with("mmcblk") {
            return Some("mmc".into());
        }
        if self.name.starts_with("vd") {
            return Some("virtio".into());
        }
        if self.name.starts_with("xvd") {
            return Some("xen".into());
        }
        match self.udev.get("ID_BUS") {
            Some("usb") => Some("usb".into()),
            Some("ata") => Some("sata".into()),
            Some("scsi") => {
                if self.udev.get("ID_PATH").is_some_and(|p| p.contains("virtio")) {
                    Some("virtio-scsi".into())
                } else {
                    Some("scsi".into())
                }
            }
            Some(other) => Some(other.to_string()),
            None => None,
        }
    }
}

fn kind_of(name: &str) -> Kind {
    let prefix = |p: &str| name.starts_with(p) && name[p.len()..].chars().next().is_some_and(|c| c.is_ascii_digit());
    if prefix("dm-") {
        Kind::Dm
    } else if prefix("md") {
        Kind::Md
    } else if prefix("loop") {
        Kind::Loop
    } else if prefix("zram") {
        Kind::Zram
    } else if prefix("sr") {
        Kind::Rom
    } else if prefix("fd") {
        Kind::Floppy
    } else if prefix("nbd") {
        Kind::Nbd
    } else if prefix("ram") {
        Kind::Ram
    } else {
        Kind::Disk
    }
}

fn read_dev(sys: &Sys, sysfs: &str, name: &str, kind: Kind, parent: Option<&str>) -> BlockDev {
    let devno = sys.read_trim(&format!("{sysfs}/dev")).unwrap_or_default();
    let queue = match parent {
        Some(p) => format!("/sys/block/{p}/queue"),
        None => format!("{sysfs}/queue"),
    };
    let flag = |p: &str| sys.read_trim(p).map(|v| v == "1");
    let attr = |p: &str| sys.read_trim(&format!("{sysfs}/device/{p}"));
    let udev = sys.read(&format!("/run/udev/data/b{devno}")).map(|t| parse_udev(&t)).unwrap_or_default();
    let whole = parent.is_none();
    BlockDev {
        name: name.to_string(),
        kind,
        size_bytes: sys.read_u64(&format!("{sysfs}/size")).unwrap_or(0) * 512,
        parent: parent.map(str::to_string),
        partition_number: sys.read_u64(&format!("{sysfs}/partition")).map(|n| n as u32),
        start_sectors: sys.read_u64(&format!("{sysfs}/start")),
        holders: sys.list(&format!("{sysfs}/holders")),
        slaves: sys.list(&format!("{sysfs}/slaves")),
        logical_block_size: sys.read_u64(&format!("{queue}/logical_block_size")).unwrap_or(512),
        physical_block_size: sys.read_u64(&format!("{queue}/physical_block_size")).unwrap_or(512),
        rotational: flag(&format!("{queue}/rotational")),
        removable: if whole { flag(&format!("{sysfs}/removable")) } else { None },
        read_only: flag(&format!("{sysfs}/ro")),
        discard_max_bytes: sys.read_u64(&format!("{queue}/discard_max_bytes")),
        model: if whole { attr("model") } else { None },
        vendor: if whole { attr("vendor") } else { None },
        serial: if whole { attr("serial").or_else(|| udev.get("ID_SERIAL_SHORT").map(str::to_string)) } else { None },
        wwid: if whole { attr("wwid").or_else(|| udev.get("ID_WWN").map(str::to_string)) } else { None },
        firmware: if whole { attr("firmware_rev").or_else(|| attr("rev")) } else { None },
        dm_name: sys.read_trim(&format!("{sysfs}/dm/name")),
        dm_uuid: sys.read_trim(&format!("{sysfs}/dm/uuid")),
        loop_backing_file: sys.read_trim(&format!("{sysfs}/loop/backing_file")),
        devno,
        udev,
    }
}

/// Every block device the kernel has, whole devices then their partitions,
/// in natural name order.  Empty devices (unused loop devices, empty CD
/// drives) are left out.
pub fn scan(sys: &Sys) -> Vec<BlockDev> {
    let mut out = Vec::new();
    for name in sys.list("/sys/block") {
        let sysfs = format!("/sys/block/{name}");
        let dev = read_dev(sys, &sysfs, &name, kind_of(&name), None);
        if dev.size_bytes == 0 {
            continue;
        }
        let parts: Vec<String> =
            sys.list(&sysfs).into_iter().filter(|e| sys.is_file(&format!("{sysfs}/{e}/partition"))).collect();
        out.push(dev);
        for p in parts {
            out.push(read_dev(sys, &format!("{sysfs}/{p}"), &p, Kind::Partition, Some(&name)));
        }
    }
    out
}

/// Splits a device-mapper name the way LVM builds it: "vg-lv" with dashes
/// inside each name doubled.
pub fn split_lvm_dm_name(dm: &str) -> Option<(String, String)> {
    let mut vg = String::new();
    let b: Vec<char> = dm.chars().collect();
    let mut i = 0;
    while i < b.len() {
        if b[i] == '-' {
            if b.get(i + 1) == Some(&'-') {
                vg.push('-');
                i += 2;
                continue;
            }
            let lv = b[i + 1..].iter().collect::<String>().replace("--", "-");
            if vg.is_empty() || lv.is_empty() {
                return None;
            }
            return Some((vg, lv));
        }
        vg.push(b[i]);
        i += 1;
    }
    None
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::sys::FakeRunner;
    use crate::testutil::Tree;

    #[test]
    fn udev_db_parsing() {
        let u = parse_udev("S:disk/by-id/ata-X\nS:disk/by-path/pci-0000:00:1f.2-ata-1\nW:4\nI:123\nE:ID_FS_TYPE=ext4\nE:ID_FS_UUID=abc\nE:ID_PATH=pci-0000\nE:NOEQUALS\nG:systemd\nQ:systemd\nV:1\n");
        assert_eq!(u.get("ID_FS_TYPE"), Some("ext4"));
        assert_eq!(u.get("NOEQUALS"), None);
        assert_eq!(u.links, vec!["/dev/disk/by-id/ata-X", "/dev/disk/by-path/pci-0000:00:1f.2-ata-1"]);
        assert!(parse_udev("").props.is_empty());
    }

    #[test]
    fn kinds() {
        assert_eq!(kind_of("sda"), Kind::Disk);
        assert_eq!(kind_of("nvme0n1"), Kind::Disk);
        assert_eq!(kind_of("vda"), Kind::Disk);
        assert_eq!(kind_of("dm-0"), Kind::Dm);
        assert_eq!(kind_of("md127"), Kind::Md);
        assert_eq!(kind_of("mdx"), Kind::Disk);
        assert_eq!(kind_of("loop7"), Kind::Loop);
        assert_eq!(kind_of("zram0"), Kind::Zram);
        assert_eq!(kind_of("sr0"), Kind::Rom);
        assert_eq!(kind_of("fd0"), Kind::Floppy);
        assert_eq!(kind_of("nbd3"), Kind::Nbd);
        assert_eq!(kind_of("ram0"), Kind::Ram);
        assert_eq!(kind_of("mmcblk0"), Kind::Disk);
        for k in [Kind::Disk, Kind::Partition, Kind::Dm, Kind::Md, Kind::Loop, Kind::Zram, Kind::Rom, Kind::Floppy, Kind::Nbd, Kind::Ram] {
            assert!(!k.name().is_empty());
        }
    }

    #[test]
    fn lvm_names() {
        assert_eq!(split_lvm_dm_name("vg0-root"), Some(("vg0".into(), "root".into())));
        assert_eq!(split_lvm_dm_name("my--vg-lv--a"), Some(("my-vg".into(), "lv-a".into())));
        assert_eq!(split_lvm_dm_name("debian--vg-swap_1"), Some(("debian-vg".into(), "swap_1".into())));
        assert_eq!(split_lvm_dm_name("nodash"), None);
        assert_eq!(split_lvm_dm_name("-lv"), None);
        assert_eq!(split_lvm_dm_name("vg-"), None);
        assert_eq!(split_lvm_dm_name("vg-thin-tpool"), Some(("vg".into(), "thin-tpool".into())));
    }

    #[test]
    fn scans_fake_sysfs() {
        let t = Tree::new("blockdev");
        let img = vec![0u8; 1 << 20];
        t.disk("vda", "254:0", &img, 512)
            .partition("vda", "vda1", "254:1", 1, 2048, 1024, &img)
            .partition("vda", "vda2", "254:2", 2, 4096, 2048, &img)
            .partition("vda", "vda10", "254:10", 10, 8192, 100, &img)
            .file("/sys/block/vda/vda2/holders/dm-0", "")
            .disk("dm-0", "253:0", &img[..65536], 512)
            .file("/sys/block/dm-0/dm/name", "vg0-root\n")
            .file("/sys/block/dm-0/dm/uuid", "LVM-abc\n")
            .file("/sys/block/dm-0/slaves/vda2", "")
            .disk("loop0", "7:0", &[], 512)
            .disk("sr0", "11:0", &[], 2048)
            .file("/sys/block/vda/device/model", "QEMU HARDDISK   \n")
            .file("/sys/block/vda/queue/discard_max_bytes", "2147483136\n")
            .file("/run/udev/data/b254:0", "S:disk/by-path/virtio-pci-0000:00:04.0\nE:ID_SERIAL_SHORT=SER123\nE:ID_PATH=pci-0000:00:04.0\n");
        let s = Sys::new(t.root(), Box::new(FakeRunner::default()), false);
        let devs = scan(&s);
        let names: Vec<&str> = devs.iter().map(|d| d.name.as_str()).collect();
        assert_eq!(names, vec!["dm-0", "vda", "vda1", "vda2", "vda10"]);
        let vda = &devs[1];
        assert_eq!(vda.kind, Kind::Disk);
        assert_eq!(vda.size_bytes, 1 << 20);
        assert_eq!(vda.model.as_deref(), Some("QEMU HARDDISK"));
        assert_eq!(vda.serial.as_deref(), Some("SER123"));
        assert_eq!(vda.transport().as_deref(), Some("virtio"));
        assert_eq!(vda.discard_max_bytes, Some(2147483136));
        assert_eq!(vda.removable, Some(false));
        assert_eq!(vda.udev.links, vec!["/dev/disk/by-path/virtio-pci-0000:00:04.0"]);
        let vda2 = &devs[3];
        assert_eq!(vda2.kind, Kind::Partition);
        assert_eq!(vda2.parent.as_deref(), Some("vda"));
        assert_eq!(vda2.partition_number, Some(2));
        assert_eq!(vda2.start_sectors, Some(4096));
        assert_eq!(vda2.size_bytes, 2048 * 512);
        assert_eq!(vda2.holders, vec!["dm-0"]);
        assert_eq!(vda2.logical_block_size, 512);
        assert_eq!(vda2.model, None);
        assert_eq!(vda2.removable, None);
        assert_eq!(vda2.sysfs(), "/sys/block/vda/vda2");
        assert_eq!(vda2.dev_path(), "/dev/vda2");
        let dm = &devs[0];
        assert_eq!(dm.kind, Kind::Dm);
        assert_eq!(dm.dm_name.as_deref(), Some("vg0-root"));
        assert_eq!(dm.dm_uuid.as_deref(), Some("LVM-abc"));
        assert_eq!(dm.slaves, vec!["vda2"]);
        assert_eq!(dm.sysfs(), "/sys/block/dm-0");
    }

    #[test]
    fn transports() {
        let mk = |name: &str, props: &[(&str, &str)]| BlockDev {
            name: name.into(),
            kind: Kind::Disk,
            devno: String::new(),
            size_bytes: 1,
            parent: None,
            partition_number: None,
            start_sectors: None,
            holders: vec![],
            slaves: vec![],
            logical_block_size: 512,
            physical_block_size: 512,
            rotational: None,
            removable: None,
            read_only: None,
            discard_max_bytes: None,
            model: None,
            vendor: None,
            serial: None,
            wwid: None,
            firmware: None,
            dm_name: None,
            dm_uuid: None,
            loop_backing_file: None,
            udev: Udev { props: props.iter().map(|(k, v)| (k.to_string(), v.to_string())).collect(), links: vec![] },
        };
        assert_eq!(mk("nvme0n1", &[]).transport().as_deref(), Some("nvme"));
        assert_eq!(mk("mmcblk0", &[]).transport().as_deref(), Some("mmc"));
        assert_eq!(mk("xvda", &[]).transport().as_deref(), Some("xen"));
        assert_eq!(mk("sda", &[("ID_BUS", "ata")]).transport().as_deref(), Some("sata"));
        assert_eq!(mk("sda", &[("ID_BUS", "usb")]).transport().as_deref(), Some("usb"));
        assert_eq!(mk("sda", &[("ID_BUS", "scsi"), ("ID_PATH", "pci-0000:00:05.0-scsi-0:0:0:0")]).transport().as_deref(), Some("scsi"));
        assert_eq!(mk("sda", &[("ID_BUS", "scsi"), ("ID_PATH", "virtio-pci-0000:00:05.0-scsi-0:0:0:0")]).transport().as_deref(), Some("virtio-scsi"));
        assert_eq!(mk("sda", &[("ID_BUS", "ieee1394")]).transport().as_deref(), Some("ieee1394"));
        assert_eq!(mk("sda", &[]).transport(), None);
    }
}
