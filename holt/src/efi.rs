//! UEFI state from /sys/firmware/efi: boot mode, Secure Boot, the boot
//! entries (Boot####, BootOrder, BootCurrent) with their device paths
//! decoded, and the variables boot loaders leave behind (systemd's Boot
//! Loader Interface, which Debian's GRUB and systemd-boot both set, and
//! shim's MOK variables).

use crate::bytes::{guid, hex, le16, le32, le64, sub, u8_at, utf16le};
use crate::json::{Obj, Value};
use crate::sys::Sys;

pub const GLOBAL: &str = "8be4df61-93ca-11d2-aa0d-00e098032b8c";
pub const LOADER: &str = "4a67b082-0a4c-41cf-b6c7-440b29bb8c4f";
pub const SHIM: &str = "605dab50-e046-4300-abb6-3dd810dd8b23";
const EFIVARS: &str = "/sys/firmware/efi/efivars";

/// A variable's data (without its 4-byte attributes).
pub fn var(sys: &Sys, name: &str, vendor: &str) -> Option<Vec<u8>> {
    let b = sys.read_bytes(&format!("{EFIVARS}/{name}-{vendor}"))?;
    (b.len() >= 4).then(|| b[4..].to_vec())
}

#[derive(Debug, Clone, PartialEq)]
pub enum Node {
    Pci { device: u8, function: u8 },
    Acpi { hid: u32, uid: u32 },
    Scsi { target: u16, lun: u16 },
    Usb { port: u8, interface: u8 },
    Sata { port: u16, multiplier: u16, lun: u16 },
    Nvme { namespace: u32 },
    Mac,
    Ipv4,
    Ipv6,
    Uri(String),
    HardDrive { partition: u32, start: u64, size: u64, signature: String, format: u8 },
    CdRom { entry: u32 },
    File(String),
    FvFile(String),
    Fv(String),
    Vendor { kind: u8, guid: String },
    Bbs { device_type: u16, description: String },
    Other { kind: u8, subtype: u8 },
}

impl Node {
    pub fn text(&self) -> String {
        match self {
            Node::Pci { device, function } => format!("Pci(0x{device:x},0x{function:x})"),
            Node::Acpi { hid: 0x0a03_41d0, uid } => format!("PciRoot(0x{uid:x})"),
            Node::Acpi { hid: 0x0a08_41d0, uid } => format!("PcieRoot(0x{uid:x})"),
            Node::Acpi { hid, uid } => format!("Acpi(0x{hid:08x},0x{uid:x})"),
            Node::Scsi { target, lun } => format!("Scsi(0x{target:x},0x{lun:x})"),
            Node::Usb { port, interface } => format!("USB(0x{port:x},0x{interface:x})"),
            Node::Sata { port, multiplier, lun } => format!("Sata(0x{port:x},0x{multiplier:x},0x{lun:x})"),
            Node::Nvme { namespace } => format!("NVMe(0x{namespace:x})"),
            Node::Mac => "MAC()".into(),
            Node::Ipv4 => "IPv4()".into(),
            Node::Ipv6 => "IPv6()".into(),
            Node::Uri(u) => format!("Uri({u})"),
            Node::HardDrive { partition, start, size, signature, format } => {
                let f = match format {
                    1 => "MBR",
                    2 => "GPT",
                    _ => "?",
                };
                format!("HD({partition},{f},{signature},0x{start:x},0x{size:x})")
            }
            Node::CdRom { entry } => format!("CDROM(0x{entry:x})"),
            Node::File(p) => format!("File({p})"),
            Node::FvFile(g) => format!("FvFile({g})"),
            Node::Fv(g) => format!("Fv({g})"),
            Node::Vendor { kind, guid } => format!("Ven{}({guid})", match kind {
                1 => "Hw",
                3 => "Msg",
                4 => "Media",
                _ => "",
            }),
            Node::Bbs { device_type, description } => format!("BBS(0x{device_type:x},{description})"),
            Node::Other { kind, subtype } => format!("Path({kind},{subtype})"),
        }
    }
}

/// The device paths in a load option's FilePathList (usually one; some
/// firmware writes a vendor path before the real one).
pub fn parse_device_paths(b: &[u8]) -> Vec<Vec<Node>> {
    let mut paths = Vec::new();
    let mut cur = Vec::new();
    let mut i = 0;
    while i + 4 <= b.len() {
        let (t, st, len) = (b[i], b[i + 1], le16(b, i + 2) as usize);
        if len < 4 || i + len > b.len() {
            break;
        }
        let d = &b[i + 4..i + len];
        if t == 0x7f {
            if !cur.is_empty() {
                paths.push(std::mem::take(&mut cur));
            }
            // End of this instance (0x01) or of the whole path (0xff): either
            // way another path may follow.
            let _ = st;
            i += len;
            continue;
        }
        cur.push(match (t, st) {
            (1, 1) => Node::Pci { function: u8_at(d, 0), device: u8_at(d, 1) },
            (2, 1) => Node::Acpi { hid: le32(d, 0), uid: le32(d, 4) },
            (3, 2) => Node::Scsi { target: le16(d, 0), lun: le16(d, 2) },
            (3, 5) => Node::Usb { port: u8_at(d, 0), interface: u8_at(d, 1) },
            (3, 0x12) => Node::Sata { port: le16(d, 0), multiplier: le16(d, 2), lun: le16(d, 4) },
            (3, 0x17) => Node::Nvme { namespace: le32(d, 0) },
            (3, 0x0b) => Node::Mac,
            (3, 0x0c) => Node::Ipv4,
            (3, 0x0d) => Node::Ipv6,
            (3, 0x18) => Node::Uri(String::from_utf8_lossy(d).into_owned()),
            (4, 1) => {
                let format = u8_at(d, 36);
                let sig_type = u8_at(d, 37);
                let signature = match sig_type {
                    1 => format!("{:08x}", le32(d, 20)),
                    2 => guid(sub(d, 20, 16)),
                    _ => String::new(),
                };
                Node::HardDrive { partition: le32(d, 0), start: le64(d, 4), size: le64(d, 12), signature, format }
            }
            (4, 2) => Node::CdRom { entry: le32(d, 0) },
            (4, 4) => Node::File(utf16le(d)),
            (4, 6) => Node::FvFile(guid(d)),
            (4, 7) => Node::Fv(guid(d)),
            (1 | 3 | 4, 4 | 0x0a) if (t, st) != (4, 4) => Node::Vendor { kind: t, guid: guid(d) },
            (5, 1) => Node::Bbs { device_type: le16(d, 0), description: crate::bytes::cstr(sub(d, 4, d.len())) },
            _ => Node::Other { kind: t, subtype: st },
        });
        i += len;
    }
    if !cur.is_empty() {
        paths.push(cur);
    }
    paths
}

#[derive(Debug, Clone, PartialEq)]
pub struct LoadOption {
    pub attributes: u32,
    pub description: String,
    pub paths: Vec<Vec<Node>>,
    pub optional_data: usize,
}

pub fn parse_load_option(b: &[u8]) -> Option<LoadOption> {
    if b.len() < 6 {
        return None;
    }
    let attributes = le32(b, 0);
    let fpl = le16(b, 4) as usize;
    let mut i = 6;
    let mut units = Vec::new();
    loop {
        if i + 2 > b.len() {
            return None;
        }
        let u = le16(b, i);
        i += 2;
        if u == 0 {
            break;
        }
        units.push(u);
    }
    let description = String::from_utf16_lossy(&units);
    let end = i.checked_add(fpl).filter(|&e| e <= b.len())?;
    Some(LoadOption { attributes, description, paths: parse_device_paths(&b[i..end]), optional_data: b.len() - end })
}

impl LoadOption {
    pub fn hard_drive(&self) -> Option<&Node> {
        self.paths.iter().flatten().find(|n| matches!(n, Node::HardDrive { .. }))
    }
    pub fn file(&self) -> Option<&str> {
        self.paths.iter().flatten().find_map(|n| if let Node::File(p) = n { Some(p.as_str()) } else { None })
    }
    pub fn text(&self) -> Vec<String> {
        self.paths.iter().map(|p| p.iter().map(Node::text).collect::<Vec<_>>().join("/")).collect()
    }
}

/// UTF-16 string variable (systemd's LoaderInfo and friends).
fn var_utf16(sys: &Sys, name: &str, vendor: &str) -> Option<String> {
    var(sys, name, vendor).map(|b| utf16le(&b)).filter(|s| !s.is_empty())
}

fn var_u8(sys: &Sys, name: &str) -> Option<u8> {
    var(sys, name, GLOBAL).and_then(|b| b.first().copied())
}

pub fn is_uefi(sys: &Sys) -> bool {
    sys.is_dir("/sys/firmware/efi")
}

pub fn firmware_json(sys: &Sys) -> Value {
    if !is_uefi(sys) {
        return Obj::new().set("mode", "bios").build();
    }
    let vars = sys.is_dir(EFIVARS) && !sys.list(EFIVARS).is_empty();
    if !vars {
        sys.warn("/sys/firmware/efi/efivars is empty or not mounted: no UEFI variables read");
    }
    Obj::new()
        .set("mode", "uefi")
        .set("platform_size", sys.read_u64("/sys/firmware/efi/fw_platform_size"))
        .set("secure_boot", var_u8(sys, "SecureBoot").map(|b| b == 1))
        .set("setup_mode", var_u8(sys, "SetupMode").map(|b| b == 1))
        .set("efivars_readable", vars)
        .build()
}

/// The boot manager's entries and the loader's own variables.
pub fn boot_json(sys: &Sys, partuuid_to_dev: &dyn Fn(&str) -> Option<String>) -> Value {
    if !is_uefi(sys) {
        return Value::Null;
    }
    let order: Vec<u16> = var(sys, "BootOrder", GLOBAL).map(|b| b.chunks_exact(2).map(|c| le16(c, 0)).collect()).unwrap_or_default();
    let current = var(sys, "BootCurrent", GLOBAL).filter(|b| b.len() >= 2).map(|b| le16(&b, 0));
    let next = var(sys, "BootNext", GLOBAL).filter(|b| b.len() >= 2).map(|b| le16(&b, 0));
    let timeout = var(sys, "Timeout", GLOBAL).filter(|b| b.len() >= 2).map(|b| le16(&b, 0));
    let mut nums: Vec<u16> = sys
        .list(EFIVARS)
        .iter()
        .filter_map(|n| n.strip_suffix(&format!("-{GLOBAL}")))
        .filter_map(|n| n.strip_prefix("Boot"))
        .filter(|n| n.len() == 4 && n.chars().all(|c| c.is_ascii_hexdigit()))
        .filter_map(|n| u16::from_str_radix(n, 16).ok())
        .collect();
    nums.sort_unstable();
    let mut entries = Vec::new();
    for n in nums {
        let name = format!("Boot{n:04X}");
        let Some(raw) = var(sys, &name, GLOBAL) else { continue };
        let mut o = Obj::new().set("number", format!("{n:04X}"));
        // The whole load option as the firmware stores it (attributes,
        // description, device path, optional data), so an entry whose
        // path isn't a plain HD/File pair can be written back as it was.
        o.put("load_option_hex", hex(&raw));
        match parse_load_option(&raw) {
            Some(lo) => {
                o.put("description", lo.description.as_str());
                o.put("active", lo.attributes & 1 != 0);
                o.put("hidden", lo.attributes & 8 != 0);
                o.put("category", if lo.attributes & 0x1f00 == 0x100 { "app" } else { "boot" });
                o.put("in_boot_order", order.iter().position(|&x| x == n));
                o.put("device_path", lo.text());
                if let Some(Node::HardDrive { partition, start, size, signature, format }) = lo.hard_drive() {
                    o.put(
                        "partition",
                        Obj::new()
                            .set("number", *partition)
                            .set("table", if *format == 2 { "gpt" } else { "dos" })
                            .set("uuid", signature.as_str())
                            .set("start_lba", *start)
                            .set("sectors", *size)
                            .set("device", if *format == 2 { partuuid_to_dev(signature) } else { None }),
                    );
                }
                o.put("file", lo.file());
                o.put("optional_data_bytes", lo.optional_data);
            }
            None => o.put("error", "unparsable load option"),
        }
        entries.push(o.build());
    }
    let shim_vars: Vec<String> = sys
        .list(EFIVARS)
        .iter()
        .filter_map(|n| n.strip_suffix(&format!("-{SHIM}")).map(str::to_string))
        .collect();
    Obj::new()
        .set("boot_current", current.map(|c| format!("{c:04X}")))
        .set("boot_next", next.map(|c| format!("{c:04X}")))
        .set("boot_order", order.iter().map(|n| format!("{n:04X}")).collect::<Vec<_>>())
        .set("timeout_seconds", timeout)
        .set("entries", entries)
        .set(
            "loader_interface",
            Obj::new()
                .set("loader_info", var_utf16(sys, "LoaderInfo", LOADER))
                .set("loader_device_partuuid", var_utf16(sys, "LoaderDevicePartUUID", LOADER).map(|s| s.to_lowercase()))
                .set("loader_image_identifier", var_utf16(sys, "LoaderImageIdentifier", LOADER))
                .set("loader_entry_selected", var_utf16(sys, "LoaderEntrySelected", LOADER))
                .set("loader_entry_default", var_utf16(sys, "LoaderEntryDefault", LOADER))
                .set("stub_info", var_utf16(sys, "StubInfo", LOADER)),
        )
        .set("shim_variables", shim_vars)
        .build()
}

#[cfg(test)]
pub mod real {
    /// Chopin's actual variables (Debian 13, MoreFine S500+ firmware), with
    /// the 4-byte attribute prefix as efivarfs shows it.
    pub fn unhex(s: &str) -> Vec<u8> {
        let h: String = s.chars().filter(|c| c.is_ascii_hexdigit()).collect();
        (0..h.len() / 2).map(|i| u8::from_str_radix(&h[2 * i..2 * i + 2], 16).unwrap()).collect()
    }
    pub const BOOT0000_WINDOWS: &str = "07000000 0900 0000 8c00 5700 6900 6e00 6400 6f00 7700 7300 2000 4200 6f00 6f00 7400 2000 4d00 6100 6e00 6100 6700 6500 7200 0000 0104 1400 e775 e299 a075 374b a2e6 c538 5e6c 00cb 7fff 0400 0401 2a00 0100 0000 0008 0000 0000 0000 0020 0800 0000 0000 8ec2 fcd4 1039 7648 86f2 564a 396c 6a27 0202 0404 4600 5c00 4500 4600 4900 5c00 4d00 6900 6300 7200 6f00 7300 6f00 6600 7400 5c00 4200 6f00 6f00 7400 5c00 6200 6f00 6f00 7400 6d00 6700 6600 7700 2e00 6500 6600 6900 0000 7fff 0400";
    pub const BOOT0001_SHIM: &str = "07000000 0100 0000 6200 6400 6500 6200 6900 6100 6e00 0000 0401 2a00 0100 0000 0008 0000 0000 0000 0000 1000 0000 0000 ed04 5f0b 31e7 c64a 933e 83ff 2565 7722 0202 0404 3400 5c00 4500 4600 4900 5c00 4400 4500 4200 4900 4100 4e00 5c00 5300 4800 4900 4d00 5800 3600 3400 2e00 4500 4600 4900 0000 7fff 0400";
    pub const BOOT0002_GRUB: &str = "07000000 0100 0000 6200 6400 6500 6200 6900 6100 6e00 0000 0401 2a00 0100 0000 0008 0000 0000 0000 0000 1000 0000 0000 ed04 5f0b 31e7 c64a 933e 83ff 2565 7722 0202 0404 3400 5c00 4500 4600 4900 5c00 4400 4500 4200 4900 4100 4e00 5c00 4700 5200 5500 4200 5800 3600 3400 2e00 4500 4600 4900 0000 7fff 0400 0000 424f";
    pub const BOOT0003_BBS: &str = "07000000 0100 0000 0d00 5500 4500 4600 4900 3a00 4300 4400 2f00 4400 5600 4400 2000 4400 7200 6900 7600 6500 0000 0501 0900 8100 0000 007f ff04 00";
    pub const LOADER_INFO: &str = "06000000 4700 5200 5500 4200 2000 3200 2e00 3100 3200 2d00 3900 2b00 6400 6500 6200 3100 3300 7500 3200 0000";
    pub const LOADER_PARTUUID: &str = "06000000 3000 6200 3500 6600 3000 3400 6500 6400 2d00 6500 3700 3300 3100 2d00 3400 6100 6300 3600 2d00 3900 3300 3300 6500 2d00 3800 3300 6600 6600 3200 3500 3600 3500 3700 3700 3200 3200 0000";
}

#[cfg(test)]
mod tests {
    use super::real::*;
    use super::*;
    use crate::sys::FakeRunner;
    use crate::testutil::Tree;

    #[test]
    fn real_windows_entry_has_two_paths() {
        let b = unhex(BOOT0000_WINDOWS);
        let lo = parse_load_option(&b[4..]).unwrap();
        assert_eq!(lo.description, "Windows Boot Manager");
        assert_eq!(lo.attributes, 9); // active, hidden
        assert_eq!(lo.paths.len(), 2);
        assert!(matches!(lo.paths[0][0], Node::Vendor { kind: 1, .. }));
        assert_eq!(lo.file(), Some("\\EFI\\Microsoft\\Boot\\bootmgfw.efi"));
        let Some(Node::HardDrive { partition, start, size, signature, format }) = lo.hard_drive() else { panic!() };
        assert_eq!((*partition, *start, *size, *format), (1, 2048, 532480, 2));
        assert_eq!(signature, "d4fcc28e-3910-4876-86f2-564a396c6a27");
        assert_eq!(
            lo.text()[1],
            "HD(1,GPT,d4fcc28e-3910-4876-86f2-564a396c6a27,0x800,0x82000)/File(\\EFI\\Microsoft\\Boot\\bootmgfw.efi)"
        );
    }

    #[test]
    fn real_debian_entries() {
        let shim = parse_load_option(&unhex(BOOT0001_SHIM)[4..]).unwrap();
        assert_eq!(shim.description, "debian");
        assert_eq!(shim.file(), Some("\\EFI\\DEBIAN\\SHIMX64.EFI"));
        assert_eq!(shim.optional_data, 0);
        let grub = parse_load_option(&unhex(BOOT0002_GRUB)[4..]).unwrap();
        assert_eq!(grub.file(), Some("\\EFI\\DEBIAN\\GRUBX64.EFI"));
        assert_eq!(grub.optional_data, 4);
        let Some(Node::HardDrive { signature, .. }) = grub.hard_drive() else { panic!() };
        assert_eq!(signature, "0b5f04ed-e731-4ac6-933e-83ff25657722");
        let bbs = parse_load_option(&unhex(BOOT0003_BBS)[4..]).unwrap();
        assert_eq!(bbs.description, "UEFI:CD/DVD Drive");
        assert_eq!(bbs.paths[0][0], Node::Bbs { device_type: 0x81, description: String::new() });
        assert_eq!(bbs.file(), None);
        assert!(bbs.hard_drive().is_none());
    }

    #[test]
    fn device_path_nodes() {
        let mut b = Vec::new();
        let mut node = |t: u8, st: u8, d: &[u8]| {
            b.push(t);
            b.push(st);
            b.extend(((d.len() + 4) as u16).to_le_bytes());
            b.extend(d);
        };
        node(2, 1, &[0xd0, 0x41, 0x03, 0x0a, 0, 0, 0, 0]);
        node(1, 1, &[0, 0x1d]);
        node(3, 0x17, &[1, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0]);
        node(3, 0x12, &[2, 0, 0xff, 0xff, 0, 0]);
        node(3, 5, &[3, 0]);
        node(3, 2, &[1, 0, 0, 0]);
        node(3, 0x0b, &[0; 33]);
        node(3, 0x18, b"http://x/");
        node(4, 2, &[1, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0]);
        node(4, 6, &[0x11; 16]);
        node(9, 9, &[]);
        node(0x7f, 1, &[]);
        node(2, 1, &[0xd0, 0x41, 0x08, 0x0a, 1, 0, 0, 0]);
        node(0x7f, 0xff, &[]);
        let p = parse_device_paths(&b);
        assert_eq!(p.len(), 2);
        let t: Vec<String> = p[0].iter().map(Node::text).collect();
        assert_eq!(
            t,
            vec![
                "PciRoot(0x0)", "Pci(0x1d,0x0)", "NVMe(0x1)", "Sata(0x2,0xffff,0x0)", "USB(0x3,0x0)", "Scsi(0x1,0x0)", "MAC()",
                "Uri(http://x/)", "CDROM(0x1)", "FvFile(11111111-1111-1111-1111-111111111111)", "Path(9,9)"
            ]
        );
        assert_eq!(p[1][0].text(), "PcieRoot(0x1)");
        // Truncated or zero-length nodes stop the walk instead of looping.
        assert!(parse_device_paths(&[4, 4, 0, 0, 1, 2]).is_empty());
        assert!(parse_device_paths(&[4, 4, 200, 0]).is_empty());
        let mbr_hd: Vec<u8> = {
            let mut d = vec![0u8; 38];
            d[0] = 2;
            d[20..24].copy_from_slice(&0x1234_abcdu32.to_le_bytes());
            d[36] = 1;
            d[37] = 1;
            let mut v = vec![4, 1];
            v.extend(42u16.to_le_bytes());
            v.extend(d);
            v
        };
        assert_eq!(parse_device_paths(&mbr_hd)[0][0].text(), "HD(2,MBR,1234abcd,0x0,0x0)");
    }

    #[test]
    fn load_option_rejects() {
        assert!(parse_load_option(&[1, 0, 0]).is_none());
        // Description never terminated.
        assert!(parse_load_option(&[1, 0, 0, 0, 0, 0, 0x41, 0]).is_none());
        // FilePathList longer than the variable.
        assert!(parse_load_option(&[1, 0, 0, 0, 0x10, 0, 0, 0]).is_none());
    }

    fn efi_tree() -> Tree {
        let t = Tree::new("efi");
        let v = |n: &str, g: &str| format!("/sys/firmware/efi/efivars/{n}-{g}");
        t.file("/sys/firmware/efi/fw_platform_size", "64\n")
            .file(&v("Boot0000", GLOBAL), unhex(BOOT0000_WINDOWS))
            .file(&v("Boot0001", GLOBAL), unhex(BOOT0001_SHIM))
            .file(&v("Boot0002", GLOBAL), unhex(BOOT0002_GRUB))
            .file(&v("Boot0003", GLOBAL), unhex(BOOT0003_BBS))
            .file(&v("Boot000A", GLOBAL), [7u8, 0, 0, 0, 1])
            .file(&v("BootOrder", GLOBAL), unhex("07000000 0100 0200 0000 0300"))
            .file(&v("BootCurrent", GLOBAL), unhex("06000000 0100"))
            .file(&v("Timeout", GLOBAL), unhex("07000000 0500"))
            .file(&v("SecureBoot", GLOBAL), unhex("06000000 01"))
            .file(&v("SetupMode", GLOBAL), unhex("06000000 00"))
            .file(&v("BootXYZW", GLOBAL), [0u8; 4])
            .file(&v("LoaderInfo", LOADER), unhex(LOADER_INFO))
            .file(&v("LoaderDevicePartUUID", LOADER), unhex(LOADER_PARTUUID))
            .file(&v("MokListRT", SHIM), [0u8; 8])
            .file(&v("SbatLevelRT", SHIM), [0u8; 8]);
        t
    }

    #[test]
    fn firmware_and_boot_json() {
        let t = efi_tree();
        let s = Sys::new(t.root(), Box::new(FakeRunner::default()), false);
        let fw = firmware_json(&s);
        assert_eq!(fw.get("mode").and_then(Value::as_str), Some("uefi"));
        assert_eq!(fw.get("secure_boot"), Some(&Value::Bool(true)));
        assert_eq!(fw.get("setup_mode"), Some(&Value::Bool(false)));
        assert_eq!(fw.get("platform_size").and_then(Value::as_u64), Some(64));
        let lookup = |u: &str| (u == "0b5f04ed-e731-4ac6-933e-83ff25657722").then(|| "nvme1n1p1".to_string());
        let b = boot_json(&s, &lookup);
        assert_eq!(b.get("boot_current").and_then(Value::as_str), Some("0001"));
        assert_eq!(b.get("boot_order"), Some(&Value::from(vec!["0001", "0002", "0000", "0003"])));
        assert_eq!(b.get("timeout_seconds").and_then(Value::as_u64), Some(5));
        let e = b.get("entries").unwrap().as_array().unwrap();
        assert_eq!(e.len(), 5);
        assert_eq!(e[1].get("file").and_then(Value::as_str), Some("\\EFI\\DEBIAN\\SHIMX64.EFI"));
        assert_eq!(e[1].at(&["partition", "device"]).and_then(Value::as_str), Some("nvme1n1p1"));
        assert_eq!(e[1].get("in_boot_order").and_then(Value::as_u64), Some(0));
        // The raw load option, without efivarfs' 4 attribute bytes.
        assert_eq!(e[1].get("load_option_hex").and_then(Value::as_str), Some(hex(&unhex(BOOT0001_SHIM)[4..]).as_str()));
        assert_eq!(e[4].get("load_option_hex").and_then(Value::as_str), Some("01"));
        assert!(e[0].at(&["partition", "device"]).unwrap().is_null());
        assert_eq!(e[4].get("number").and_then(Value::as_str), Some("000A"));
        assert!(e[4].get("error").is_some());
        assert!(e[4].get("in_boot_order").is_none());
        assert_eq!(b.at(&["loader_interface", "loader_info"]).and_then(Value::as_str), Some("GRUB 2.12-9+deb13u2"));
        assert_eq!(
            b.at(&["loader_interface", "loader_device_partuuid"]).and_then(Value::as_str),
            Some("0b5f04ed-e731-4ac6-933e-83ff25657722")
        );
        assert!(b.at(&["loader_interface", "loader_entry_selected"]).unwrap().is_null());
        assert_eq!(b.get("shim_variables"), Some(&Value::from(vec!["MokListRT", "SbatLevelRT"])));
    }

    #[test]
    fn bios_and_empty_efivars() {
        let t = Tree::new("bios");
        let s = Sys::new(t.root(), Box::new(FakeRunner::default()), false);
        assert_eq!(firmware_json(&s).get("mode").and_then(Value::as_str), Some("bios"));
        assert_eq!(boot_json(&s, &|_| None), Value::Null);
        let t = Tree::new("efi-empty");
        t.dir("/sys/firmware/efi/efivars");
        let s = Sys::new(t.root(), Box::new(FakeRunner::default()), false);
        let fw = firmware_json(&s);
        assert_eq!(fw.get("efivars_readable"), Some(&Value::Bool(false)));
        assert!(fw.get("secure_boot").unwrap().is_null());
        assert_eq!(s.warnings().len(), 1);
        let b = boot_json(&s, &|_| None);
        assert_eq!(b.get("entries"), Some(&Value::Arr(vec![])));
    }
}
