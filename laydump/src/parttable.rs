//! Partition tables read off the disk: MBR (with the extended partition's
//! chain of EBRs) and GPT (both headers, CRCs checked, and the protective or
//! hybrid MBR in front of it).

use crate::bytes::{crc32, guid, is_zero, le32, le64, sub, utf16le, ByteSource};
use crate::json::{Obj, Value};

#[derive(Debug, Clone, PartialEq)]
pub struct MbrPart {
    /// 1-4 primary, 5.. logical, numbered the way Linux does.
    pub number: u32,
    pub boot_flag: u8,
    pub ptype: u8,
    /// In logical sectors.
    pub start: u64,
    pub sectors: u64,
    pub logical: bool,
}

impl MbrPart {
    pub fn is_extended(&self) -> bool {
        is_extended(self.ptype)
    }
}

#[derive(Debug, Clone, PartialEq)]
pub struct Mbr {
    pub disk_signature: u32,
    pub boot_code: Vec<u8>,
    pub parts: Vec<MbrPart>,
    pub problems: Vec<String>,
}

#[derive(Debug, Clone, PartialEq)]
pub struct GptPart {
    pub number: u32,
    pub type_guid: String,
    pub uuid: String,
    pub first_lba: u64,
    pub last_lba: u64,
    pub attributes: u64,
    pub name: String,
}

#[derive(Debug, Clone, PartialEq)]
pub struct Gpt {
    pub revision: u32,
    pub header_size: u32,
    pub my_lba: u64,
    pub alternate_lba: u64,
    pub first_usable_lba: u64,
    pub last_usable_lba: u64,
    pub disk_guid: String,
    pub entries_lba: u64,
    pub entry_count: u32,
    pub entry_size: u32,
    pub primary_ok: bool,
    /// "ok", "bad-crc", "absent", "unreadable" or "mismatch".
    pub backup: &'static str,
    pub backup_lba: u64,
    pub parts: Vec<GptPart>,
    pub problems: Vec<String>,
}

#[derive(Debug, Clone, PartialEq)]
pub enum Table {
    Gpt { pmbr: Mbr, gpt: Gpt },
    Mbr(Mbr),
}

pub fn is_extended(t: u8) -> bool {
    matches!(t, 0x05 | 0x0f | 0x85)
}

struct RawEntry {
    boot_flag: u8,
    ptype: u8,
    start: u32,
    sectors: u32,
}

fn raw_entries(sector: &[u8]) -> [RawEntry; 4] {
    std::array::from_fn(|i| {
        let e = sub(sector, 446 + 16 * i, 16);
        RawEntry { boot_flag: crate::bytes::u8_at(e, 0), ptype: crate::bytes::u8_at(e, 4), start: le32(e, 8), sectors: le32(e, 12) }
    })
}

fn has_boot_signature(sector: &[u8]) -> bool {
    sector.len() >= 512 && sector[510] == 0x55 && sector[511] == 0xaa
}

/// The MBR in `sector0`, without following extended partitions.
pub fn parse_mbr(sector0: &[u8]) -> Option<Mbr> {
    if !has_boot_signature(sector0) {
        return None;
    }
    let mut problems = Vec::new();
    let mut parts = Vec::new();
    for (i, e) in raw_entries(sector0).iter().enumerate() {
        if e.ptype == 0 || e.sectors == 0 {
            continue;
        }
        if e.boot_flag != 0 && e.boot_flag != 0x80 {
            problems.push(format!("entry {} has boot flag 0x{:02x}", i + 1, e.boot_flag));
        }
        parts.push(MbrPart {
            number: i as u32 + 1,
            boot_flag: e.boot_flag,
            ptype: e.ptype,
            start: e.start as u64,
            sectors: e.sectors as u64,
            logical: false,
        });
    }
    Some(Mbr { disk_signature: le32(sector0, 440), boot_code: sector0[..440].to_vec(), parts, problems })
}

const MAX_LOGICAL: usize = 256;

/// The MBR with its logical partitions, followed through each extended
/// partition's chain of EBRs.
pub fn read_mbr(src: &dyn ByteSource, ss: u64) -> Option<Mbr> {
    let mut mbr = parse_mbr(&src.read_at(0, 512))?;
    let extended: Vec<MbrPart> = mbr.parts.iter().filter(|p| p.is_extended()).cloned().collect();
    let mut next_number = 5;
    for ext in extended {
        let mut cur = ext.start;
        let mut seen = std::collections::HashSet::new();
        for _ in 0..MAX_LOGICAL {
            if !seen.insert(cur) {
                mbr.problems.push(format!("EBR chain loops back to sector {cur}"));
                break;
            }
            let ebr = src.read_at(cur * ss, 512);
            if !has_boot_signature(&ebr) {
                mbr.problems.push(format!("no EBR signature at sector {cur}"));
                break;
            }
            let e = raw_entries(&ebr);
            if e[0].ptype != 0 && e[0].sectors != 0 {
                let start = cur + e[0].start as u64;
                if start + e[0].sectors as u64 > ext.start + ext.sectors {
                    mbr.problems.push(format!("logical partition {next_number} ends past its extended partition"));
                }
                mbr.parts.push(MbrPart {
                    number: next_number,
                    boot_flag: e[0].boot_flag,
                    ptype: e[0].ptype,
                    start,
                    sectors: e[0].sectors as u64,
                    logical: true,
                });
                next_number += 1;
            }
            if !is_extended(e[1].ptype) || e[1].start == 0 {
                break;
            }
            let next = ext.start + e[1].start as u64;
            if next >= ext.start + ext.sectors {
                mbr.problems.push(format!("EBR link at sector {cur} points outside its extended partition"));
                break;
            }
            cur = next;
        }
    }
    Some(mbr)
}

const GPT_SIG: &[u8] = b"EFI PART";

struct Header {
    revision: u32,
    header_size: u32,
    crc_ok: bool,
    my_lba: u64,
    alternate_lba: u64,
    first_usable: u64,
    last_usable: u64,
    disk_guid: String,
    entries_lba: u64,
    entry_count: u32,
    entry_size: u32,
    entries_crc: u32,
}

fn parse_header(b: &[u8], ss: usize) -> Option<Header> {
    if sub(b, 0, 8) != GPT_SIG {
        return None;
    }
    let header_size = le32(b, 12);
    let crc_ok = if (92..=ss as u32).contains(&header_size) && b.len() >= header_size as usize {
        let mut h = b[..header_size as usize].to_vec();
        h[16..20].fill(0);
        crc32(&h) == le32(b, 16)
    } else {
        false
    };
    Some(Header {
        revision: le32(b, 8),
        header_size,
        crc_ok,
        my_lba: le64(b, 24),
        alternate_lba: le64(b, 32),
        first_usable: le64(b, 40),
        last_usable: le64(b, 48),
        disk_guid: guid(sub(b, 56, 16)),
        entries_lba: le64(b, 72),
        entry_count: le32(b, 80),
        entry_size: le32(b, 84),
        entries_crc: le32(b, 88),
    })
}

const MAX_ENTRIES: u32 = 4096;

fn read_entries(src: &dyn ByteSource, ss: u64, h: &Header) -> Option<Vec<u8>> {
    if h.entry_count == 0 || h.entry_count > MAX_ENTRIES || h.entry_size < 128 || h.entry_size > 4096 || h.entry_size % 8 != 0 {
        return None;
    }
    let len = h.entry_count as usize * h.entry_size as usize;
    src.read_exact_at(h.entries_lba.checked_mul(ss)?, len)
}

fn header_valid(src: &dyn ByteSource, ss: u64, h: &Header) -> Option<Vec<u8>> {
    if !h.crc_ok {
        return None;
    }
    let e = read_entries(src, ss, h)?;
    (crc32(&e) == h.entries_crc).then_some(e)
}

/// The GPT, from the primary header, or the backup when the primary is bad.
/// None when neither header is there.
pub fn read_gpt(src: &dyn ByteSource, ss: u64) -> Option<Gpt> {
    let total = src.size() / ss;
    if total < 3 {
        return None;
    }
    let primary = parse_header(&src.read_at(ss, ss as usize), ss as usize);
    let primary_entries = primary.as_ref().and_then(|h| header_valid(src, ss, h));
    let backup_lba = match &primary {
        Some(h) if h.crc_ok && h.alternate_lba > 1 => h.alternate_lba,
        _ => total - 1,
    };
    let backup = if backup_lba < total {
        parse_header(&src.read_at(backup_lba * ss, ss as usize), ss as usize)
    } else {
        None
    };
    let backup_entries = backup.as_ref().and_then(|h| header_valid(src, ss, h));
    let backup_status: &'static str = if backup_lba >= total {
        "unreadable"
    } else {
        match (&backup, &backup_entries) {
            (None, _) => "absent",
            (Some(_), None) => "bad-crc",
            (Some(b), Some(_)) => match (&primary, &primary_entries) {
                (Some(p), Some(_)) if p.disk_guid != b.disk_guid || p.entries_crc != b.entries_crc => "mismatch",
                _ => "ok",
            },
        }
    };
    let (h, entries, primary_ok) = match (primary, primary_entries) {
        (Some(h), Some(e)) => (h, e, true),
        _ => match (backup, backup_entries) {
            (Some(h), Some(e)) => (h, e, false),
            _ => return None,
        },
    };
    let mut problems = Vec::new();
    if !primary_ok {
        problems.push("primary GPT header or its entries are damaged; using the backup".to_string());
    }
    let mut parts = Vec::new();
    for (i, e) in entries.chunks_exact(h.entry_size as usize).enumerate() {
        if is_zero(&e[0..16]) {
            continue;
        }
        let p = GptPart {
            number: i as u32 + 1,
            type_guid: guid(&e[0..16]),
            uuid: guid(&e[16..32]),
            first_lba: le64(e, 32),
            last_lba: le64(e, 40),
            attributes: le64(e, 48),
            name: utf16le(&e[56..128]),
        };
        if p.last_lba < p.first_lba {
            problems.push(format!("partition {} ends before it starts", p.number));
        } else if p.first_lba < h.first_usable || p.last_lba > h.last_usable {
            problems.push(format!("partition {} lies outside the usable area", p.number));
        }
        parts.push(p);
    }
    Some(Gpt {
        revision: h.revision,
        header_size: h.header_size,
        my_lba: h.my_lba,
        alternate_lba: h.alternate_lba,
        first_usable_lba: h.first_usable,
        last_usable_lba: h.last_usable,
        disk_guid: h.disk_guid,
        entries_lba: h.entries_lba,
        entry_count: h.entry_count,
        entry_size: h.entry_size,
        primary_ok,
        backup: backup_status,
        backup_lba,
        parts,
        problems,
    })
}

/// The disk's partition table, if it has one.
pub fn read_table(src: &dyn ByteSource, ss: u64) -> Option<Table> {
    let mbr = read_mbr(src, ss);
    let protective = mbr.as_ref().is_some_and(|m| m.parts.iter().any(|p| p.ptype == 0xee));
    // A GPT whose protective MBR was overwritten is still a GPT to the kernel
    // only with gpt=1 on the command line; read it anyway when there's no
    // MBR at all, as blkid does not, so the report shows it.
    if protective || mbr.is_none() {
        if let Some(gpt) = read_gpt(src, ss) {
            let pmbr = mbr.unwrap_or(Mbr { disk_signature: 0, boot_code: vec![0; 440], parts: vec![], problems: vec!["no protective MBR".into()] });
            return Some(Table::Gpt { pmbr, gpt });
        }
    }
    mbr.filter(|m| !m.parts.is_empty() || !is_zero(&m.boot_code)).map(Table::Mbr)
}

impl Table {
    pub fn boot_code(&self) -> &[u8] {
        match self {
            Table::Gpt { pmbr, .. } => &pmbr.boot_code,
            Table::Mbr(m) => &m.boot_code,
        }
    }

    /// (number, start LBA, sectors) of each partition, in order.
    pub fn extents(&self) -> Vec<(u32, u64, u64)> {
        match self {
            Table::Gpt { gpt, .. } => gpt.parts.iter().map(|p| (p.number, p.first_lba, p.last_lba.saturating_sub(p.first_lba) + 1)).collect(),
            Table::Mbr(m) => m.parts.iter().map(|p| (p.number, p.start, p.sectors)).collect(),
        }
    }

    /// The JSON of the table itself; its partitions come out separately
    /// through `partition_json`, merged with what the kernel sees.
    pub fn header_json(&self, ss: u64, total_sectors: u64) -> Value {
        match self {
            Table::Gpt { pmbr, gpt } => {
                let hybrid: Vec<&MbrPart> = pmbr.parts.iter().filter(|p| p.ptype != 0xee).collect();
                let mut problems = pmbr.problems.clone();
                problems.extend(gpt.problems.iter().cloned());
                Obj::new()
                    .set("type", "gpt")
                    .set("sector_size", ss)
                    .set("disk_guid", &gpt.disk_guid)
                    .set("revision", format!("{}.{}", gpt.revision >> 16, gpt.revision & 0xffff))
                    .set("header_size", gpt.header_size)
                    .set("first_usable_lba", gpt.first_usable_lba)
                    .set("last_usable_lba", gpt.last_usable_lba)
                    .set("entries_lba", gpt.entries_lba)
                    .set("entry_count", gpt.entry_count)
                    .set("entry_size", gpt.entry_size)
                    .set("primary_header_ok", gpt.primary_ok)
                    .set("backup_header", gpt.backup)
                    .set("backup_lba", gpt.backup_lba)
                    // false when the disk is bigger than the table thinks: an
                    // image written to a bigger disk, or a grown VM disk.
                    .set("backup_at_end", gpt.backup_lba + 1 == total_sectors)
                    .set(
                        "protective_mbr",
                        Obj::new()
                            .set("present", pmbr.parts.iter().any(|p| p.ptype == 0xee))
                            .set("hybrid", !hybrid.is_empty())
                            .set("entries", pmbr.parts.iter().map(|p| mbr_part_json(p, ss, pmbr.disk_signature)).collect::<Vec<_>>()),
                    )
                    .set("problems", problems)
                    .build()
            }
            Table::Mbr(m) => Obj::new()
                .set("type", "dos")
                .set("sector_size", ss)
                .set("disk_signature", format!("{:08x}", m.disk_signature))
                .set("problems", m.problems.clone())
                .build(),
        }
    }

    /// JSON for partition `number` as the table has it.
    pub fn partition_json(&self, number: u32, ss: u64) -> Option<Obj> {
        match self {
            Table::Gpt { gpt, .. } => gpt.parts.iter().find(|p| p.number == number).map(|p| gpt_part_json(p, ss)),
            Table::Mbr(m) => m.parts.iter().find(|p| p.number == number).map(|p| mbr_part_obj(p, ss, m.disk_signature)),
        }
    }
}

fn mbr_part_obj(p: &MbrPart, ss: u64, sig: u32) -> Obj {
    Obj::new()
        .set("number", p.number)
        .set("start_lba", p.start)
        .set("sectors", p.sectors)
        .set("start_bytes", p.start * ss)
        .set("size_bytes", p.sectors * ss)
        .set("type", format!("0x{:02x}", p.ptype))
        .set("type_name", mbr_type_name(p.ptype))
        .set("bootable", p.boot_flag == 0x80)
        .set("logical", p.logical)
        .set("extended", p.is_extended())
        // What the kernel and blkid call its PARTUUID.
        .set("uuid", format!("{sig:08x}-{:02x}", p.number))
}

fn mbr_part_json(p: &MbrPart, ss: u64, sig: u32) -> Value {
    mbr_part_obj(p, ss, sig).build()
}

fn gpt_part_json(p: &GptPart, ss: u64) -> Obj {
    let sectors = p.last_lba.saturating_sub(p.first_lba) + 1;
    Obj::new()
        .set("number", p.number)
        .set("start_lba", p.first_lba)
        .set("end_lba", p.last_lba)
        .set("sectors", sectors)
        .set("start_bytes", p.first_lba * ss)
        .set("size_bytes", sectors * ss)
        .set("type", &p.type_guid)
        .set("type_name", gpt_type_name(&p.type_guid))
        .set("uuid", &p.uuid)
        // The GPT partition name; "partlabel" as lsblk and /dev/disk/by-partlabel
        // call it, so it can't be taken for the device's name.
        .set("partlabel", &p.name)
        .set("attributes", format!("0x{:016x}", p.attributes))
        .set("attribute_flags", gpt_attribute_flags(p.attributes, &p.type_guid))
}

pub fn gpt_attribute_flags(a: u64, type_guid: &str) -> Vec<String> {
    let mut v = Vec::new();
    if a & 1 != 0 {
        v.push("required".to_string());
    }
    if a & 2 != 0 {
        v.push("no_block_io_protocol".to_string());
    }
    if a & 4 != 0 {
        v.push("legacy_bios_bootable".to_string());
    }
    for bit in 3..48 {
        if a & (1 << bit) != 0 {
            v.push(format!("bit{bit}"));
        }
    }
    // Bits 48-63 belong to the partition type.  Microsoft basic data and the
    // Linux types of the Discoverable Partitions Specification share these.
    let named = type_guid == MS_BASIC_DATA || gpt_type_name(type_guid).is_some_and(|n| n.starts_with("Linux"));
    for bit in 48..64 {
        if a & (1 << bit) == 0 {
            continue;
        }
        let name = match bit {
            59 if named => "grow_file_system",
            60 if named => "read_only",
            62 if named => "hidden",
            63 if named => "no_automount",
            _ => "",
        };
        v.push(if name.is_empty() { format!("bit{bit}") } else { name.to_string() });
    }
    v
}

pub const ESP: &str = "c12a7328-f81f-11d2-ba4b-00a0c93ec93b";
pub const BIOS_BOOT: &str = "21686148-6449-6e6f-744e-656564454649";
pub const XBOOTLDR: &str = "bc13c2ff-59e6-4262-a352-b275fd6f7172";
pub const MS_BASIC_DATA: &str = "ebd0a0a2-b9e5-4433-87c0-68b6b72699c7";

const GPT_TYPES: &[(&str, &str)] = &[
    (ESP, "EFI System"),
    (BIOS_BOOT, "BIOS boot"),
    ("0fc63daf-8483-4772-8e79-3d69d8477de4", "Linux filesystem"),
    ("4f68bce3-e8cd-4db1-96e7-fbcaf984b709", "Linux root (x86-64)"),
    ("44479540-f297-41b2-9af7-d131d5f0458a", "Linux root (x86)"),
    ("b921b045-1df0-41c3-af44-4c6f280d3fae", "Linux root (ARM-64)"),
    ("8484680c-9521-48c6-9c11-b0720656f69e", "Linux /usr (x86-64)"),
    ("b0e01050-ee5f-4390-949a-9101b17104e9", "Linux /usr (ARM-64)"),
    ("0657fd6d-a4ab-43c4-84e5-0933c84b4f4f", "Linux swap"),
    ("e6d6d379-f507-44c2-a23c-238f2a3df928", "Linux LVM"),
    ("a19d880f-05fc-4d3b-a006-743f0f84911e", "Linux RAID"),
    ("ca7d7ccb-63ed-4c53-861c-1742536059cc", "Linux LUKS"),
    ("7ffec5c9-2d00-49b7-8941-3ea10a5586b7", "Linux dm-crypt"),
    ("933ac7e1-2eb4-4f13-b844-0e14e2aef915", "Linux /home"),
    ("3b8f8425-20e0-4f3b-907f-1a25a76f98e8", "Linux /srv"),
    ("4d21b016-b534-45c2-a9fb-5c16e091fd2d", "Linux /var"),
    ("7ec6f557-3bc5-4aca-b293-16ef5df639d1", "Linux /var/tmp"),
    ("773f91ef-66d4-49b5-bd83-d683bf40ad16", "Linux user's home"),
    (XBOOTLDR, "Linux extended boot"),
    ("8da63339-0007-60c0-c436-083ac8230908", "Linux reserved"),
    ("6a898cc3-1dd2-11b2-99a6-080020736631", "Solaris /usr & Apple ZFS"),
    ("6a82cb45-1dd2-11b2-99a6-080020736631", "Solaris boot"),
    ("6a85cf4d-1dd2-11b2-99a6-080020736631", "Solaris root"),
    ("6a87c46f-1dd2-11b2-99a6-080020736631", "Solaris swap"),
    ("6a945a3b-1dd2-11b2-99a6-080020736631", "Solaris reserved 1"),
    ("e3c9e316-0b5c-4db8-817d-f92df00215ae", "Microsoft reserved"),
    (MS_BASIC_DATA, "Microsoft basic data"),
    ("de94bba4-06d1-4d40-a16a-bfd50179d6ac", "Windows recovery environment"),
    ("5808c8aa-7e8f-42e0-85d2-e1e90434cfb3", "Microsoft LDM metadata"),
    ("af9b60a0-1431-4f62-bc68-3311714a69ad", "Microsoft LDM data"),
    ("48465300-0000-11aa-aa11-00306543ecac", "Apple HFS/HFS+"),
    ("7c3457ef-0000-11aa-aa11-00306543ecac", "Apple APFS"),
    ("426f6f74-0000-11aa-aa11-00306543ecac", "Apple boot"),
    ("83bd6b9d-7f41-11dc-be0b-001560b84f0f", "FreeBSD boot"),
    ("516e7cb4-6ecf-11d6-8ff8-00022d09712b", "FreeBSD data"),
    ("516e7cb5-6ecf-11d6-8ff8-00022d09712b", "FreeBSD swap"),
    ("516e7cb6-6ecf-11d6-8ff8-00022d09712b", "FreeBSD UFS"),
    ("516e7cba-6ecf-11d6-8ff8-00022d09712b", "FreeBSD ZFS"),
    ("824cc7a0-36a8-11e3-890a-952519ad3f61", "OpenBSD data"),
    ("49f48d5a-b10e-11dc-b99b-0019d1879648", "NetBSD FFS"),
    ("2db519c4-b10f-11dc-b99b-0019d1879648", "NetBSD concatenated"),
    ("cef5a9ad-73bc-4601-89f3-cdeeeee321a1", "QNX6 file system"),
    ("42465331-3ba3-10f1-802a-4861696b7521", "Haiku BFS"),
    ("9d275380-40ad-11db-bf97-000c2911d1b8", "VMware VMFS"),
    ("fe3a2a5d-4f32-41a7-b725-accc3285a309", "ChromeOS kernel"),
    ("3cb8e202-3b7e-47dd-8a3c-7ff2a13cfcec", "ChromeOS root fs"),
];

pub fn gpt_type_name(g: &str) -> Option<&'static str> {
    GPT_TYPES.iter().find(|(k, _)| *k == g).map(|(_, v)| *v)
}

pub fn mbr_type_name(t: u8) -> Option<&'static str> {
    Some(match t {
        0x01 => "FAT12",
        0x04 => "FAT16 <32M",
        0x05 => "Extended",
        0x06 => "FAT16",
        0x07 => "HPFS/NTFS/exFAT",
        0x0b => "W95 FAT32",
        0x0c => "W95 FAT32 (LBA)",
        0x0e => "W95 FAT16 (LBA)",
        0x0f => "W95 Ext'd (LBA)",
        0x11 => "Hidden FAT12",
        0x12 => "Compaq diagnostics",
        0x14 => "Hidden FAT16 <32M",
        0x16 => "Hidden FAT16",
        0x17 => "Hidden HPFS/NTFS",
        0x1b => "Hidden W95 FAT32",
        0x1c => "Hidden W95 FAT32 (LBA)",
        0x1e => "Hidden W95 FAT16 (LBA)",
        0x27 => "Hidden NTFS WinRE",
        0x42 => "SFS",
        0x82 => "Linux swap / Solaris",
        0x83 => "Linux",
        0x84 => "OS/2 hidden or Intel hibernation",
        0x85 => "Linux extended",
        0x86 => "NTFS volume set",
        0x87 => "NTFS volume set",
        0x88 => "Linux plaintext",
        0x8e => "Linux LVM",
        0x96 => "ISO9660",
        0xa5 => "FreeBSD",
        0xa6 => "OpenBSD",
        0xa8 => "Darwin UFS",
        0xa9 => "NetBSD",
        0xab => "Darwin boot",
        0xaf => "HFS / HFS+",
        0xbe => "Solaris boot",
        0xbf => "Solaris",
        0xda => "Non-FS data",
        0xeb => "BeOS fs",
        0xee => "GPT",
        0xef => "EFI (FAT-12/16/32)",
        0xfb => "VMware VMFS",
        0xfc => "VMware VMKCORE",
        0xfd => "Linux raid autodetect",
        _ => return None,
    })
}

/// Test helpers that build tables byte by byte, shared with other modules'
/// tests.
#[cfg(test)]
pub mod build {
    use super::*;

    pub fn guid_bytes(g: &str) -> [u8; 16] {
        let h: String = g.chars().filter(|c| *c != '-').collect();
        let raw: Vec<u8> = (0..16).map(|i| u8::from_str_radix(&h[2 * i..2 * i + 2], 16).unwrap()).collect();
        let mut b = [0u8; 16];
        b[0..4].copy_from_slice(&[raw[3], raw[2], raw[1], raw[0]]);
        b[4..6].copy_from_slice(&[raw[5], raw[4]]);
        b[6..8].copy_from_slice(&[raw[7], raw[6]]);
        b[8..16].copy_from_slice(&raw[8..16]);
        b
    }

    pub fn mbr_entry(sector: &mut [u8], slot: usize, boot: u8, ptype: u8, start: u32, sectors: u32) {
        let o = 446 + 16 * slot;
        sector[o] = boot;
        sector[o + 4] = ptype;
        sector[o + 8..o + 12].copy_from_slice(&start.to_le_bytes());
        sector[o + 12..o + 16].copy_from_slice(&sectors.to_le_bytes());
        sector[510] = 0x55;
        sector[511] = 0xaa;
    }

    pub struct GptSpec<'a> {
        pub disk_guid: &'a str,
        /// (type, uuid, first, last, attributes, name)
        pub parts: Vec<(&'a str, &'a str, u64, u64, u64, &'a str)>,
    }

    /// Writes protective MBR, primary and backup GPT into `img` (whose size
    /// sets the disk's size) with `ss`-byte sectors and 128 entries.
    pub fn write_gpt(img: &mut [u8], ss: usize, spec: &GptSpec) {
        let total = (img.len() / ss) as u64;
        let mut pm = vec![0u8; 512];
        mbr_entry(&mut pm, 0, 0, 0xee, 1, (total - 1).min(u32::MAX as u64) as u32);
        img[..512].copy_from_slice(&pm);
        let entries_sectors = (128 * 128) / ss as u64;
        let mut entries = vec![0u8; 128 * 128];
        for (i, (t, u, f, l, a, n)) in spec.parts.iter().enumerate() {
            let e = &mut entries[i * 128..(i + 1) * 128];
            e[0..16].copy_from_slice(&guid_bytes(t));
            e[16..32].copy_from_slice(&guid_bytes(u));
            e[32..40].copy_from_slice(&f.to_le_bytes());
            e[40..48].copy_from_slice(&l.to_le_bytes());
            e[48..56].copy_from_slice(&a.to_le_bytes());
            for (j, c) in n.encode_utf16().take(36).enumerate() {
                e[56 + 2 * j..58 + 2 * j].copy_from_slice(&c.to_le_bytes());
            }
        }
        let ecrc = crc32(&entries);
        let first_usable = 2 + entries_sectors;
        let last_usable = total - 2 - entries_sectors;
        let header = |my: u64, alt: u64, elba: u64| {
            let mut h = vec![0u8; 92];
            h[0..8].copy_from_slice(GPT_SIG);
            h[8..12].copy_from_slice(&0x0001_0000u32.to_le_bytes());
            h[12..16].copy_from_slice(&92u32.to_le_bytes());
            h[24..32].copy_from_slice(&my.to_le_bytes());
            h[32..40].copy_from_slice(&alt.to_le_bytes());
            h[40..48].copy_from_slice(&first_usable.to_le_bytes());
            h[48..56].copy_from_slice(&last_usable.to_le_bytes());
            h[56..72].copy_from_slice(&guid_bytes(spec.disk_guid));
            h[72..80].copy_from_slice(&elba.to_le_bytes());
            h[80..84].copy_from_slice(&128u32.to_le_bytes());
            h[84..88].copy_from_slice(&128u32.to_le_bytes());
            h[88..92].copy_from_slice(&ecrc.to_le_bytes());
            let c = crc32(&h);
            h[16..20].copy_from_slice(&c.to_le_bytes());
            h
        };
        let p = header(1, total - 1, 2);
        img[ss..ss + 92].copy_from_slice(&p);
        img[2 * ss..2 * ss + entries.len()].copy_from_slice(&entries);
        let belba = total - 1 - entries_sectors;
        let b = header(total - 1, 1, belba);
        let bo = (belba as usize) * ss;
        img[bo..bo + entries.len()].copy_from_slice(&entries);
        let ho = (total as usize - 1) * ss;
        img[ho..ho + 92].copy_from_slice(&b);
    }
}

#[cfg(test)]
mod tests {
    use super::build::*;
    use super::*;
    use crate::bytes::MemSource;

    const ROOT: &str = "4f68bce3-e8cd-4db1-96e7-fbcaf984b709";
    const DG: &str = "11111111-2222-3333-4444-555555555555";

    fn gpt_disk(ss: usize, sectors: usize) -> Vec<u8> {
        let mut img = vec![0u8; ss * sectors];
        write_gpt(
            &mut img,
            ss,
            &GptSpec {
                disk_guid: DG,
                parts: vec![
                    (BIOS_BOOT, "aaaaaaaa-0000-0000-0000-000000000001", 34, 2047, 0, "bios"),
                    (ESP, "aaaaaaaa-0000-0000-0000-000000000002", 2048, 4095, 1, "EFI system partition"),
                    (ROOT, "aaaaaaaa-0000-0000-0000-000000000003", 4096, (sectors - 40) as u64, (1 << 63) | (1 << 59) | 4, "root ☃"),
                ],
            },
        );
        img
    }

    #[test]
    fn gpt_round_trip() {
        let img = gpt_disk(512, 8192);
        let t = read_table(&MemSource(img), 512).unwrap();
        let Table::Gpt { pmbr, gpt } = &t else { panic!("not gpt") };
        assert!(gpt.primary_ok);
        assert_eq!(gpt.backup, "ok");
        assert_eq!(gpt.backup_lba, 8191);
        assert_eq!(gpt.disk_guid, DG);
        assert_eq!(gpt.parts.len(), 3);
        assert_eq!(gpt.parts[1].type_guid, ESP);
        assert_eq!(gpt.parts[1].name, "EFI system partition");
        assert_eq!(gpt.parts[2].name, "root ☃");
        assert_eq!(gpt.parts[2].number, 3);
        assert!(gpt.problems.is_empty(), "{:?}", gpt.problems);
        assert_eq!(pmbr.parts.len(), 1);
        assert_eq!(pmbr.parts[0].ptype, 0xee);
        let h = t.header_json(512, 8192);
        assert_eq!(h.get("type").and_then(Value::as_str), Some("gpt"));
        assert_eq!(h.get("backup_at_end"), Some(&Value::Bool(true)));
        assert_eq!(h.at(&["protective_mbr", "hybrid"]), Some(&Value::Bool(false)));
        assert_eq!(h.get("revision").and_then(Value::as_str), Some("1.0"));
        let p = t.partition_json(3, 512).unwrap().build();
        assert_eq!(p.get("type_name").and_then(Value::as_str), Some("Linux root (x86-64)"));
        assert_eq!(p.get("start_bytes").and_then(Value::as_u64), Some(4096 * 512));
        assert_eq!(
            p.get("attribute_flags"),
            Some(&Value::from(vec!["legacy_bios_bootable", "grow_file_system", "no_automount"]))
        );
        assert_eq!(t.extents(), vec![(1, 34, 2014), (2, 2048, 2048), (3, 4096, 8192 - 40 - 4096 + 1)]);
        assert!(t.partition_json(9, 512).is_none());
    }

    #[test]
    fn gpt_4k_sectors() {
        let img = gpt_disk(4096, 2048);
        let Some(Table::Gpt { gpt, .. }) = read_table(&MemSource(img.clone()), 4096) else { panic!() };
        assert_eq!(gpt.parts.len(), 3);
        assert_eq!(gpt.backup, "ok");
        // Read with the wrong sector size, the header isn't where we look.
        assert!(matches!(read_table(&MemSource(img), 512), Some(Table::Mbr(_)) | None));
    }

    #[test]
    fn gpt_damaged_primary_uses_backup() {
        let mut img = gpt_disk(512, 8192);
        img[512 + 60] ^= 0xff; // corrupt the primary header's disk GUID
        let Some(Table::Gpt { gpt, .. }) = read_table(&MemSource(img.clone()), 512) else { panic!() };
        assert!(!gpt.primary_ok);
        assert_eq!(gpt.backup, "ok");
        assert_eq!(gpt.disk_guid, DG);
        assert_eq!(gpt.parts.len(), 3);
        assert!(gpt.problems[0].contains("backup"));
        // Damaged entries (not header) are caught by the entries CRC.
        let mut img2 = gpt_disk(512, 8192);
        img2[1024 + 200] ^= 1;
        let Some(Table::Gpt { gpt, .. }) = read_table(&MemSource(img2), 512) else { panic!() };
        assert!(!gpt.primary_ok);
        assert_eq!(gpt.parts.len(), 3);
    }

    #[test]
    fn gpt_backup_states() {
        // Bad backup header.
        let mut img = gpt_disk(512, 8192);
        img[8191 * 512 + 40] ^= 1;
        let Some(Table::Gpt { gpt, .. }) = read_table(&MemSource(img), 512) else { panic!() };
        assert!(gpt.primary_ok);
        assert_eq!(gpt.backup, "bad-crc");
        // Backup header missing altogether.
        let mut img = gpt_disk(512, 8192);
        img[8191 * 512..].fill(0);
        let Some(Table::Gpt { gpt, .. }) = read_table(&MemSource(img), 512) else { panic!() };
        assert_eq!(gpt.backup, "absent");
        // The image written onto a bigger disk: the backup sits mid-disk.
        let mut img = gpt_disk(512, 8192);
        img.extend(vec![0u8; 512 * 4096]);
        let t = read_table(&MemSource(img), 512).unwrap();
        let Table::Gpt { gpt, .. } = &t else { panic!() };
        assert_eq!(gpt.backup, "ok");
        assert_eq!(gpt.backup_lba, 8191);
        assert_eq!(t.header_json(512, 8192 + 4096).get("backup_at_end"), Some(&Value::Bool(false)));
        // A disk truncated below where the backup should be.
        let img = gpt_disk(512, 8192);
        let t = read_gpt(&MemSource(img[..512 * 6000].to_vec()), 512).unwrap();
        assert_eq!(t.backup, "unreadable");
    }

    #[test]
    fn gpt_mismatched_backup() {
        let mut a = gpt_disk(512, 8192);
        let mut other = vec![0u8; 512 * 8192];
        write_gpt(&mut other, 512, &GptSpec { disk_guid: "99999999-2222-3333-4444-555555555555", parts: vec![] });
        let n = a.len();
        a[n - 512 * 33..].copy_from_slice(&other[n - 512 * 33..]);
        let g = read_gpt(&MemSource(a), 512).unwrap();
        assert_eq!(g.backup, "mismatch");
    }

    #[test]
    fn gpt_bad_entry_geometry_and_problems() {
        let mut img = vec![0u8; 512 * 4096];
        write_gpt(
            &mut img,
            512,
            &GptSpec {
                disk_guid: DG,
                parts: vec![
                    (ROOT, "aaaaaaaa-0000-0000-0000-000000000001", 100, 50, 0, "backwards"),
                    (ROOT, "aaaaaaaa-0000-0000-0000-000000000002", 10, 50, 0, "early"),
                ],
            },
        );
        let g = read_gpt(&MemSource(img), 512).unwrap();
        assert_eq!(g.problems.len(), 2, "{:?}", g.problems);
        assert!(g.problems[0].contains("ends before"));
        assert!(g.problems[1].contains("outside"));
    }

    #[test]
    fn gpt_entry_size_sanity() {
        // A header claiming absurd entry geometry is rejected, not trusted.
        let mut img = gpt_disk(512, 8192);
        img[512 + 84..512 + 88].copy_from_slice(&7u32.to_le_bytes());
        let mut h = img[512..512 + 92].to_vec();
        h[16..20].fill(0);
        let c = crc32(&h);
        img[512 + 16..512 + 20].copy_from_slice(&c.to_le_bytes());
        let g = read_gpt(&MemSource(img), 512).unwrap();
        assert!(!g.primary_ok); // fell back to the intact backup
        assert_eq!(g.entry_size, 128);
        assert!(read_gpt(&MemSource(vec![0; 1024]), 512).is_none());
    }

    #[test]
    fn hybrid_mbr() {
        let mut img = gpt_disk(512, 8192);
        mbr_entry(&mut img[..512], 1, 0x80, 0x0c, 2048, 2048);
        let t = read_table(&MemSource(img), 512).unwrap();
        let h = t.header_json(512, 8192);
        assert_eq!(h.at(&["protective_mbr", "hybrid"]), Some(&Value::Bool(true)));
        assert_eq!(h.at(&["protective_mbr", "entries", "1", "type"]).and_then(Value::as_str), Some("0x0c"));
    }

    #[test]
    fn gpt_without_pmbr() {
        let mut img = gpt_disk(512, 8192);
        img[..512].fill(0);
        let Some(Table::Gpt { pmbr, gpt }) = read_table(&MemSource(img), 512) else { panic!() };
        assert_eq!(gpt.parts.len(), 3);
        assert_eq!(pmbr.problems, vec!["no protective MBR"]);
    }

    /// An MBR like Debian's installer writes: primary /boot, then an extended
    /// partition holding swap and /.
    fn dos_disk() -> Vec<u8> {
        let mut img = vec![0u8; 512 * 20000];
        img[440..444].copy_from_slice(&0x1234_abcdu32.to_le_bytes());
        mbr_entry(&mut img[..512], 0, 0x80, 0x83, 2048, 4096);
        mbr_entry(&mut img[..512], 1, 0, 0x05, 8190, 11810);
        // EBR 1 at 8190: logical 5 (swap) at +2, link to EBR 2.
        let e1 = 8190 * 512;
        mbr_entry(&mut img[e1..e1 + 512], 0, 0, 0x82, 2, 2048);
        mbr_entry(&mut img[e1..e1 + 512], 1, 0, 0x05, 2050, 9760);
        // EBR 2 at 8190+2050: logical 6 (/), no link.
        let e2 = (8190 + 2050) * 512;
        mbr_entry(&mut img[e2..e2 + 512], 0, 0, 0x83, 2, 9758);
        img
    }

    #[test]
    fn dos_with_logicals() {
        let t = read_table(&MemSource(dos_disk()), 512).unwrap();
        let Table::Mbr(m) = &t else { panic!() };
        assert_eq!(m.disk_signature, 0x1234_abcd);
        let got: Vec<(u32, u8, u64, u64, bool)> = m.parts.iter().map(|p| (p.number, p.ptype, p.start, p.sectors, p.logical)).collect();
        assert_eq!(
            got,
            vec![(1, 0x83, 2048, 4096, false), (2, 0x05, 8190, 11810, false), (5, 0x82, 8192, 2048, true), (6, 0x83, 10242, 9758, true)]
        );
        assert!(m.problems.is_empty(), "{:?}", m.problems);
        let h = t.header_json(512, 20000);
        assert_eq!(h.get("disk_signature").and_then(Value::as_str), Some("1234abcd"));
        let p1 = t.partition_json(1, 512).unwrap().build();
        assert_eq!(p1.get("bootable"), Some(&Value::Bool(true)));
        assert_eq!(p1.get("uuid").and_then(Value::as_str), Some("1234abcd-01"));
        assert_eq!(p1.get("type_name").and_then(Value::as_str), Some("Linux"));
        let p2 = t.partition_json(2, 512).unwrap().build();
        assert_eq!(p2.get("extended"), Some(&Value::Bool(true)));
        let p6 = t.partition_json(6, 512).unwrap().build();
        assert_eq!(p6.get("logical"), Some(&Value::Bool(true)));
        assert_eq!(p6.get("uuid").and_then(Value::as_str), Some("1234abcd-06"));
    }

    #[test]
    fn ebr_chain_faults() {
        // A loop.
        let mut img = dos_disk();
        let e2 = (8190 + 2050) * 512;
        mbr_entry(&mut img[e2..e2 + 512], 1, 0, 0x05, 2050, 100);
        let m = read_mbr(&MemSource(img), 512).unwrap();
        assert!(m.problems.iter().any(|p| p.contains("loops")), "{:?}", m.problems);
        assert_eq!(m.parts.len(), 4);
        // A missing EBR signature.
        let mut img = dos_disk();
        img[(8190 + 2050) * 512 + 510] = 0;
        let m = read_mbr(&MemSource(img), 512).unwrap();
        assert!(m.problems.iter().any(|p| p.contains("no EBR signature")));
        assert_eq!(m.parts.len(), 3);
        // A link pointing outside the extended partition.
        let mut img = dos_disk();
        let e1 = 8190 * 512;
        mbr_entry(&mut img[e1..e1 + 512], 1, 0, 0x05, 50000, 10);
        let m = read_mbr(&MemSource(img), 512).unwrap();
        assert!(m.problems.iter().any(|p| p.contains("outside")));
        // A logical partition overrunning its container.
        let mut img = dos_disk();
        mbr_entry(&mut img[e1..e1 + 512], 0, 0, 0x82, 2, 999_999);
        let m = read_mbr(&MemSource(img), 512).unwrap();
        assert!(m.problems.iter().any(|p| p.contains("ends past")));
    }

    #[test]
    fn mbr_odd_cases() {
        // No signature: no table.
        assert!(read_table(&MemSource(vec![0u8; 4096]), 512).is_none());
        // Signature but nothing at all: no table either.
        let mut s = vec![0u8; 4096];
        s[510] = 0x55;
        s[511] = 0xaa;
        assert!(read_table(&MemSource(s.clone()), 512).is_none());
        // Boot code but no partitions (e.g. GRUB left on a wiped disk) is
        // still reported, so its boot code shows.
        s[0] = 0xeb;
        assert!(matches!(read_table(&MemSource(s.clone()), 512), Some(Table::Mbr(_))));
        // A strange boot flag is a problem; zero-size entries are skipped.
        mbr_entry(&mut s, 0, 0x7f, 0x83, 1, 10);
        mbr_entry(&mut s, 1, 0, 0x83, 20, 0);
        let m = parse_mbr(&s).unwrap();
        assert_eq!(m.parts.len(), 1);
        assert_eq!(m.problems.len(), 1);
        assert!(parse_mbr(&s[..100]).is_none());
    }

    #[test]
    fn type_names() {
        assert_eq!(gpt_type_name(ESP), Some("EFI System"));
        assert_eq!(gpt_type_name("6a898cc3-1dd2-11b2-99a6-080020736631"), Some("Solaris /usr & Apple ZFS"));
        assert_eq!(gpt_type_name("00000000-0000-0000-0000-000000000001"), None);
        assert_eq!(mbr_type_name(0x83), Some("Linux"));
        assert_eq!(mbr_type_name(0xfd), Some("Linux raid autodetect"));
        assert_eq!(mbr_type_name(0x99), None);
        // Every GPT key is a well-formed lower-case GUID, and keys are unique.
        let mut seen = std::collections::HashSet::new();
        for (k, _) in GPT_TYPES {
            assert_eq!(k.len(), 36);
            assert!(k.chars().all(|c| c == '-' || c.is_ascii_digit() || ('a'..='f').contains(&c)), "{k}");
            assert!(seen.insert(*k), "duplicate {k}");
            assert_eq!(guid(&guid_bytes(k)), *k);
        }
    }

    #[test]
    fn attribute_flags() {
        assert_eq!(gpt_attribute_flags(0, ROOT), Vec::<String>::new());
        assert_eq!(gpt_attribute_flags(0b111, ESP), vec!["required", "no_block_io_protocol", "legacy_bios_bootable"]);
        assert_eq!(gpt_attribute_flags(1 << 10, ESP), vec!["bit10"]);
        assert_eq!(gpt_attribute_flags((1 << 60) | (1 << 62), MS_BASIC_DATA), vec!["read_only", "hidden"]);
        // On a type that doesn't define them, high bits stay numbered.
        assert_eq!(gpt_attribute_flags(1 << 60, ESP), vec!["bit60"]);
        assert_eq!(gpt_attribute_flags(1 << 50, ROOT), vec!["bit50"]);
    }
}
