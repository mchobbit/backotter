//! What's mounted and how the system means to mount it: /proc/self/mountinfo,
//! /proc/swaps, /etc/fstab, /etc/crypttab and /etc/mdadm/mdadm.conf.

use crate::json::{Obj, Value};

/// Undoes the kernel's and fstab's octal escapes (\040 for a space).
pub fn unescape(s: &str) -> String {
    let b = s.as_bytes();
    let mut out = Vec::with_capacity(b.len());
    let mut i = 0;
    while i < b.len() {
        if b[i] == b'\\' && i + 3 < b.len() && b[i + 1..i + 4].iter().all(|c| (b'0'..=b'7').contains(c)) {
            let v = (b[i + 1] - b'0') as u32 * 64 + (b[i + 2] - b'0') as u32 * 8 + (b[i + 3] - b'0') as u32;
            if v <= 0xff {
                out.push(v as u8);
                i += 4;
                continue;
            }
        }
        out.push(b[i]);
        i += 1;
    }
    String::from_utf8_lossy(&out).into_owned()
}

#[derive(Debug, Clone, PartialEq)]
pub struct Mount {
    pub id: u32,
    pub parent: u32,
    pub devno: String,
    pub root: String,
    pub target: String,
    pub options: String,
    pub fstype: String,
    pub source: String,
    pub super_options: String,
}

pub fn parse_mountinfo(text: &str) -> Vec<Mount> {
    let mut out = Vec::new();
    for line in text.lines() {
        let f: Vec<&str> = line.split(' ').collect();
        let Some(dash) = f.iter().position(|x| *x == "-") else { continue };
        if dash < 6 || f.len() < dash + 3 {
            continue;
        }
        let (Ok(id), Ok(parent)) = (f[0].parse(), f[1].parse()) else { continue };
        out.push(Mount {
            id,
            parent,
            devno: f[2].to_string(),
            root: unescape(f[3]),
            target: unescape(f[4]),
            options: f[5].to_string(),
            fstype: f[dash + 1].to_string(),
            source: unescape(f[dash + 2]),
            super_options: f.get(dash + 3).map(|s| s.to_string()).unwrap_or_default(),
        });
    }
    out
}

/// Virtual filesystems that say nothing about the disk layout.
const PSEUDO: &[&str] = &[
    "proc", "sysfs", "devtmpfs", "devpts", "tmpfs", "securityfs", "cgroup", "cgroup2", "pstore", "efivarfs", "bpf",
    "debugfs", "tracefs", "configfs", "fusectl", "mqueue", "hugetlbfs", "autofs", "binfmt_misc", "rpc_pipefs", "nsfs",
    "ramfs", "selinuxfs", "fuse.gvfsd-fuse", "fuse.portal", "nfsd", "overlay", "squashfs", "fuse.lxcfs",
];

pub fn is_layout_mount(m: &Mount) -> bool {
    !PSEUDO.contains(&m.fstype.as_str())
}

impl Mount {
    /// The btrfs subvolume (subvol=) of a btrfs mount.
    pub fn btrfs_subvol(&self) -> Option<String> {
        self.super_options.split(',').find_map(|o| o.strip_prefix("subvol=")).map(str::to_string)
    }
    pub fn to_json(&self, device: Option<&str>, usage: Option<(u64, u64, u64)>) -> Value {
        let mut o = Obj::new()
            .set("target", self.target.as_str())
            .set("source", self.source.as_str())
            .set("fstype", self.fstype.as_str())
            .set("devno", self.devno.as_str())
            .set("device", device)
            .set("root", self.root.as_str())
            .set("options", self.options.as_str())
            .set("super_options", self.super_options.as_str());
        if let Some((total, free, avail)) = usage {
            o.put("size_bytes", total);
            o.put("used_bytes", total.saturating_sub(free));
            o.put("available_bytes", avail);
        }
        o.build()
    }
}

#[derive(Debug, Clone, PartialEq)]
pub struct FstabEntry {
    pub spec: String,
    pub file: String,
    pub vfstype: String,
    pub options: String,
    pub freq: u32,
    pub passno: u32,
}

/// fstab(5): six white-space-separated fields, the last two optional.
pub fn parse_fstab(text: &str) -> Vec<FstabEntry> {
    let mut out = Vec::new();
    for line in text.lines() {
        let line = line.trim();
        if line.is_empty() || line.starts_with('#') {
            continue;
        }
        let f: Vec<&str> = line.split_whitespace().collect();
        if f.len() < 3 {
            continue;
        }
        out.push(FstabEntry {
            spec: unescape(f[0]),
            file: unescape(f[1]),
            vfstype: f[2].to_string(),
            options: f.get(3).unwrap_or(&"defaults").to_string(),
            freq: f.get(4).and_then(|x| x.parse().ok()).unwrap_or(0),
            passno: f.get(5).and_then(|x| x.parse().ok()).unwrap_or(0),
        });
    }
    out
}

impl FstabEntry {
    pub fn to_json(&self, resolves_to: Option<&str>) -> Value {
        Obj::new()
            .set("spec", self.spec.as_str())
            .set("file", self.file.as_str())
            .set("type", self.vfstype.as_str())
            .set("options", self.options.as_str())
            .set("freq", self.freq)
            .set("passno", self.passno)
            .set("resolves_to", resolves_to)
            .build()
    }
}

#[derive(Debug, Clone, PartialEq)]
pub struct CrypttabEntry {
    pub name: String,
    pub device: String,
    pub key: String,
    pub options: String,
}

/// crypttab(5): name, device, key file ("none" or "-": ask), options.
pub fn parse_crypttab(text: &str) -> Vec<CrypttabEntry> {
    let mut out = Vec::new();
    for line in text.lines() {
        let line = line.trim();
        if line.is_empty() || line.starts_with('#') {
            continue;
        }
        let f: Vec<&str> = line.split_whitespace().collect();
        if f.len() < 2 {
            continue;
        }
        out.push(CrypttabEntry {
            name: f[0].to_string(),
            device: f[1].to_string(),
            key: f.get(2).unwrap_or(&"none").to_string(),
            options: f.get(3).unwrap_or(&"").to_string(),
        });
    }
    out
}

impl CrypttabEntry {
    /// The key file's path is configuration, not a secret: it's reported, its
    /// contents never are.
    pub fn to_json(&self, resolves_to: Option<&str>) -> Value {
        let prompts = matches!(self.key.as_str(), "none" | "-" | "");
        Obj::new()
            .set("name", self.name.as_str())
            .set("device", self.device.as_str())
            .set("key_file", if prompts { None } else { Some(self.key.as_str()) })
            .set("options", self.options.split(',').filter(|o| !o.is_empty()).collect::<Vec<_>>())
            .set("resolves_to", resolves_to)
            .build()
    }
}

#[derive(Debug, Clone, PartialEq)]
pub struct Swap {
    pub file: String,
    pub kind: String,
    pub size_kib: u64,
    pub used_kib: u64,
    pub priority: i64,
}

pub fn parse_swaps(text: &str) -> Vec<Swap> {
    text.lines()
        .skip(1)
        .filter_map(|l| {
            let f: Vec<&str> = l.split_whitespace().collect();
            (f.len() >= 5).then(|| Swap {
                file: unescape(f[0]),
                kind: f[1].to_string(),
                size_kib: f[2].parse().unwrap_or(0),
                used_kib: f[3].parse().unwrap_or(0),
                priority: f[4].parse().unwrap_or(0),
            })
        })
        .collect()
}

/// mdadm.conf: its ARRAY lines (device, then key=value words, which may
/// continue on lines starting with white space), plus the other keywords.
pub fn parse_mdadm_conf(text: &str) -> Value {
    // Join continuation lines first.
    let mut lines: Vec<String> = Vec::new();
    for raw in text.lines() {
        let l = raw.split('#').next().unwrap_or("");
        if l.trim().is_empty() {
            continue;
        }
        if l.starts_with([' ', '\t']) {
            if let Some(last) = lines.last_mut() {
                last.push(' ');
                last.push_str(l.trim());
                continue;
            }
        }
        lines.push(l.trim().to_string());
    }
    let mut arrays = Vec::new();
    let mut other = Obj::new();
    for l in lines {
        let mut words = l.split_whitespace();
        let Some(kw) = words.next() else { continue };
        if kw.eq_ignore_ascii_case("ARRAY") {
            let mut a = Obj::new();
            for w in words {
                match w.split_once('=') {
                    Some((k, v)) => a.put(&k.to_ascii_lowercase(), v),
                    None => a.put("device", w),
                }
            }
            arrays.push(a.build());
        } else {
            other.put(&kw.to_ascii_lowercase(), words.collect::<Vec<_>>().join(" "));
        }
    }
    Obj::new().set("arrays", arrays).set("settings", other).build()
}

#[cfg(test)]
mod tests {
    use super::*;

    const MOUNTINFO: &str = "22 1 0:21 / /sys rw,nosuid,nodev,noexec,relatime shared:7 - sysfs sysfs rw
26 1 259:2 / / rw,relatime shared:1 - ext4 /dev/nvme1n1p2 rw,errors=remount-ro
27 26 259:1 / /boot/efi rw,relatime shared:2 - vfat /dev/nvme1n1p1 rw,fmask=0077,dmask=0077,codepage=437,iocharset=ascii,shortname=mixed,utf8,errors=remount-ro
28 26 0:30 /@home /home rw,relatime shared:3 - btrfs /dev/vda5 rw,space_cache=v2,subvolid=257,subvol=/@home
29 26 0:40 / /mnt/my\\040disk rw master:1 propagate_from:2 - xfs /dev/sdb1 rw
30 26 0:41 / /tmp rw - tmpfs tmpfs rw
bogus line
31 x 0:1 / / - ext4 a b
";

    #[test]
    fn mountinfo() {
        let m = parse_mountinfo(MOUNTINFO);
        assert_eq!(m.len(), 6);
        assert_eq!(m[1].target, "/");
        assert_eq!(m[1].devno, "259:2");
        assert_eq!(m[1].source, "/dev/nvme1n1p2");
        assert_eq!(m[1].super_options, "rw,errors=remount-ro");
        assert_eq!(m[3].root, "/@home");
        assert_eq!(m[3].btrfs_subvol().as_deref(), Some("/@home"));
        assert_eq!(m[1].btrfs_subvol(), None);
        // Optional fields before the "-" are skipped, escapes undone.
        assert_eq!(m[4].target, "/mnt/my disk");
        assert_eq!(m[4].fstype, "xfs");
        let layout: Vec<&str> = m.iter().filter(|x| is_layout_mount(x)).map(|x| x.target.as_str()).collect();
        assert_eq!(layout, vec!["/", "/boot/efi", "/home", "/mnt/my disk"]);
        let j = m[1].to_json(Some("nvme1n1p2"), Some((100, 40, 30)));
        assert_eq!(j.get("used_bytes").and_then(Value::as_u64), Some(60));
        assert_eq!(j.get("available_bytes").and_then(Value::as_u64), Some(30));
        assert_eq!(j.get("device").and_then(Value::as_str), Some("nvme1n1p2"));
        assert!(m[0].to_json(None, None).get("size_bytes").is_none());
    }

    #[test]
    fn escapes() {
        assert_eq!(unescape("a\\040b\\011c\\012d\\134e"), "a b\tc\nd\\e");
        assert_eq!(unescape("\\04"), "\\04");
        assert_eq!(unescape("\\999"), "\\999");
        assert_eq!(unescape("\\400"), "\\400");
        assert_eq!(unescape("plain"), "plain");
        assert_eq!(unescape("end\\"), "end\\");
    }

    #[test]
    fn fstab() {
        let f = parse_fstab(
            "# /etc/fstab: static file system information.
#
# <file system> <mount point>   <type>  <options>       <dump>  <pass>
# / was on /dev/vda3 during installation
UUID=2f1c0c47-3a54-4b3e-9e7c-1d2b3c4d5e6f /               btrfs   defaults,subvol=@rootfs 0       0
# /boot was on /dev/vda1 during installation
UUID=0d6e... /boot           ext4    defaults        0       2
/dev/mapper/vg0-swap none swap sw 0 0
/dev/sr0        /media/cdrom0   udf,iso9660 user,noauto     0       0
LABEL=My\\040Data /data xfs
short line
",
        );
        assert_eq!(f.len(), 5);
        assert_eq!(f[0].options, "defaults,subvol=@rootfs");
        assert_eq!(f[1].passno, 2);
        assert_eq!(f[2].file, "none");
        assert_eq!(f[4].spec, "LABEL=My Data");
        assert_eq!(f[4].options, "defaults");
        assert_eq!(f[4].freq, 0);
        let j = f[1].to_json(Some("vda1"));
        assert_eq!(j.get("resolves_to").and_then(Value::as_str), Some("vda1"));
        assert_eq!(j.get("type").and_then(Value::as_str), Some("ext4"));
    }

    #[test]
    fn crypttab() {
        let c = parse_crypttab("# <target name> <source device> <key file> <options>\nvda5_crypt UUID=abcd none luks,discard\nhome /dev/sdb1 /etc/keys/home.key luks\nswap /dev/sdc1 /dev/urandom swap,cipher=aes-xts-plain64\nlonely\nnokey UUID=x\n");
        assert_eq!(c.len(), 4);
        let j = c[0].to_json(Some("vda5"));
        assert!(j.get("key_file").unwrap().is_null());
        assert_eq!(j.get("options"), Some(&Value::from(vec!["luks", "discard"])));
        assert_eq!(c[1].to_json(None).get("key_file").and_then(Value::as_str), Some("/etc/keys/home.key"));
        assert_eq!(c[3].key, "none");
        assert_eq!(c[3].to_json(None).get("options"), Some(&Value::Arr(vec![])));
    }

    #[test]
    fn swaps() {
        let s = parse_swaps("Filename\t\t\t\tType\t\tSize\t\tUsed\t\tPriority\n/dev/zram0                              partition\t16777212\t0\t\t100\n/dev/nvme0n1p1                          partition\t4194300\t\t0\t\t10\n/swap\\040file file 1024 0 -2\nbad\n");
        assert_eq!(s.len(), 3);
        assert_eq!(s[0].file, "/dev/zram0");
        assert_eq!(s[0].priority, 100);
        assert_eq!(s[1].size_kib, 4194300);
        assert_eq!(s[2].file, "/swap file");
        assert_eq!(s[2].priority, -2);
        assert!(parse_swaps("").is_empty());
    }

    #[test]
    fn mdadm_conf() {
        let v = parse_mdadm_conf(
            "# mdadm.conf
HOMEHOST <system>
MAILADDR root
ARRAY /dev/md/0  metadata=1.2 UUID=a1b2c3d4:e5f6a7b8:c9d0e1f2:a3b4c5d6
   name=cinqcent:0
ARRAY /dev/md1 level=raid1 num-devices=2 UUID=00000000:11111111:22222222:33333333 # comment
",
        );
        assert_eq!(v.at(&["arrays", "0", "device"]).and_then(Value::as_str), Some("/dev/md/0"));
        assert_eq!(v.at(&["arrays", "0", "metadata"]).and_then(Value::as_str), Some("1.2"));
        assert_eq!(v.at(&["arrays", "0", "name"]).and_then(Value::as_str), Some("cinqcent:0"));
        assert_eq!(v.at(&["arrays", "1", "level"]).and_then(Value::as_str), Some("raid1"));
        assert_eq!(v.at(&["arrays", "1", "uuid"]).and_then(Value::as_str), Some("00000000:11111111:22222222:33333333"));
        assert_eq!(v.at(&["settings", "homehost"]).and_then(Value::as_str), Some("<system>"));
        assert_eq!(v.at(&["settings", "mailaddr"]).and_then(Value::as_str), Some("root"));
        let e = parse_mdadm_conf("   orphan continuation\n");
        assert_eq!(e.at(&["settings", "orphan"]).and_then(Value::as_str), Some("continuation"));
    }
}
