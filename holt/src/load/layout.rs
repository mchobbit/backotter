//! A `bko-holt dump` read back: the contract checks, and typed views of the
//! parts `load` rebuilds.  Everything else stays in the JSON (`Layout::v`),
//! read where it's needed.

use crate::json::Value;

/// The formats `load` reads: this tool's, and the same document from before
/// the rename (bko-laydump 0.1).
pub const FORMATS: [&str; 2] = [crate::report::FORMAT, crate::report::FORMAT_OLD];
pub const SCHEMA: u64 = 1;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum TableKind {
    Gpt,
    Mbr,
}

#[derive(Debug, Clone, PartialEq)]
pub struct Table {
    pub kind: TableKind,
    /// The disk GUID (GPT) or the 32-bit disk signature as "0x1234abcd" (MBR).
    pub id: Option<String>,
    pub first_usable_lba: Option<u64>,
    pub last_usable_lba: Option<u64>,
    pub entry_count: Option<u64>,
}

#[derive(Debug, Clone, PartialEq)]
pub struct Part {
    pub number: u32,
    /// Its kernel name on the source (vda1, nvme0n1p2).
    pub name: String,
    pub start_lba: u64,
    pub sectors: u64,
    /// A GPT type GUID, or an MBR type as "0x83".
    pub ptype: String,
    pub uuid: Option<String>,
    pub label: Option<String>,
    pub attributes: u64,
    pub bootable: bool,
    pub extended: bool,
    pub logical: bool,
    pub content: Value,
}

#[derive(Debug, Clone, PartialEq)]
pub struct Disk {
    pub name: String,
    pub size_bytes: u64,
    pub sector_size: u64,
    pub model: Option<String>,
    pub serial: Option<String>,
    pub wwid: Option<String>,
    pub table: Option<Table>,
    pub parts: Vec<Part>,
    /// What's on the whole disk when it has no table (a pool member, a PV).
    pub content: Value,
    /// Whether the disk holds anything at all; an empty one needs no target.
    pub used: bool,
}

impl Disk {
    pub fn describe(&self) -> String {
        let mut s = format!("{} ({}", self.name, crate::report::human(self.size_bytes));
        if let Some(m) = &self.model {
            s += &format!(", {m}");
        }
        if let Some(n) = &self.serial {
            s += &format!(", serial {n}");
        }
        s + ")"
    }
}

#[derive(Debug, Clone)]
pub struct Layout {
    pub v: Value,
    pub disks: Vec<Disk>,
}

fn s(v: &Value, k: &str) -> Option<String> {
    v.get(k).and_then(Value::as_str).map(str::to_string)
}

fn u(v: &Value, k: &str) -> Option<u64> {
    v.get(k).and_then(Value::as_u64)
}

fn b(v: &Value, k: &str) -> bool {
    v.get(k).and_then(Value::as_bool).unwrap_or(false)
}

/// `"0x0000000000000001"` or a number.
fn attrs(v: &Value) -> u64 {
    match v.get("attributes") {
        Some(Value::Str(t)) => u64::from_str_radix(t.trim_start_matches("0x"), 16).unwrap_or(0),
        Some(x) => x.as_u64().unwrap_or(0),
        None => 0,
    }
}

fn table(v: &Value) -> Option<Table> {
    let kind = match v.get("type").and_then(Value::as_str)? {
        "gpt" => TableKind::Gpt,
        "dos" | "mbr" => TableKind::Mbr,
        _ => return None,
    };
    let id = match kind {
        TableKind::Gpt => s(v, "disk_guid"),
        // dump writes the MBR signature as bare hex ("ffe14ceb"); sfdisk
        // wants it with 0x, or reads it as decimal.
        TableKind::Mbr => match v.get("disk_signature") {
            Some(Value::Str(t)) => u32::from_str_radix(t.trim_start_matches("0x"), 16).ok().map(|n| format!("0x{n:08x}")),
            Some(x) => x.as_u64().map(|n| format!("0x{n:08x}")),
            None => None,
        },
    };
    Some(Table { kind, id, first_usable_lba: u(v, "first_usable_lba"), last_usable_lba: u(v, "last_usable_lba"), entry_count: u(v, "entry_count") })
}

fn part(v: &Value, sector: u64) -> Option<Part> {
    let start_lba = u(v, "start_lba").or_else(|| u(v, "start_bytes").map(|b| b / sector))?;
    let sectors = u(v, "sectors").or_else(|| u(v, "size_bytes").map(|b| b / sector))?;
    let ptype = match v.get("type") {
        Some(Value::Str(t)) => t.clone(),
        Some(x) => format!("0x{:02x}", x.as_u64().unwrap_or(0)),
        None => return None,
    };
    Some(Part {
        number: u(v, "number")? as u32,
        name: s(v, "name").unwrap_or_default(),
        start_lba,
        sectors,
        ptype,
        uuid: s(v, "uuid"),
        label: s(v, "partlabel").filter(|l| !l.is_empty()),
        attributes: attrs(v),
        bootable: b(v, "bootable"),
        extended: b(v, "extended"),
        logical: b(v, "logical"),
        content: v.get("content").cloned().unwrap_or(Value::Null),
    })
}

impl Layout {
    /// Checks the document is a dump `load` can read, and reads its disks.
    pub fn from_json(v: Value) -> Result<Layout, String> {
        let format = v.get("format").and_then(Value::as_str).unwrap_or("");
        if !FORMATS.contains(&format) {
            return Err(format!("not a bko-holt dump (format {format:?}, want {:?})", FORMATS[0]));
        }
        match v.get("schema").and_then(Value::as_u64) {
            Some(SCHEMA) => {}
            other => return Err(format!("schema {other:?} isn't one this bko-holt knows (it reads schema {SCHEMA})")),
        }
        let id = v.at(&["os", "id"]).and_then(Value::as_str).unwrap_or("");
        let ver = v.at(&["os", "version_id"]).and_then(Value::as_str).unwrap_or("");
        if !crate::osrel::supported(id, ver) {
            return Err(format!("the source system is {id} {ver}; bko-holt load supports only {}", crate::osrel::supported_names()));
        }
        if v.get("privileged").and_then(Value::as_bool) == Some(false) {
            return Err("the dump wasn't made as root, so it has udev's view of the disks only; dump again as root".into());
        }
        let mut disks = Vec::new();
        for d in v.at(&["storage", "disks"]).and_then(Value::as_array).into_iter().flatten() {
            if d.get("kind").and_then(Value::as_str) == Some("ram") {
                continue;
            }
            // A ZFS volume (zdN) is made inside its pool, not mapped to a disk.
            if d.get("links").and_then(Value::as_array).into_iter().flatten().filter_map(Value::as_str).any(|l| l.starts_with("/dev/zvol/")) {
                continue;
            }
            let sector = u(d, "logical_sector_size").unwrap_or(512).max(512);
            let tv = d.get("partition_table").cloned().unwrap_or(Value::Null);
            let table = table(&tv);
            if !tv.is_null() && table.is_none() {
                let t = tv.get("type").and_then(Value::as_str).unwrap_or("?");
                return Err(format!("{}: partition table type {t:?} isn't one bko-holt load can make", s(d, "name").unwrap_or_default()));
            }
            let parts: Vec<Part> = d.get("partitions").and_then(Value::as_array).into_iter().flatten().filter_map(|p| part(p, sector)).collect();
            let content = d.get("content").cloned().unwrap_or(Value::Null);
            disks.push(Disk {
                name: s(d, "name").unwrap_or_default(),
                size_bytes: u(d, "size_bytes").unwrap_or(0),
                sector_size: sector,
                model: s(d, "model"),
                serial: s(d, "serial"),
                wwid: s(d, "wwid"),
                used: table.is_some() || !content.is_null(),
                table,
                parts,
                content,
            });
        }
        Ok(Layout { v, disks })
    }

    pub fn disk(&self, name: &str) -> Option<&Disk> {
        self.disks.iter().find(|d| d.name == name)
    }

    pub fn arr(&self, path: &[&str]) -> &[Value] {
        self.v.at(path).and_then(Value::as_array).map(Vec::as_slice).unwrap_or(&[])
    }

    pub fn str_at(&self, path: &[&str]) -> Option<&str> {
        self.v.at(path).and_then(Value::as_str)
    }

    pub fn uefi(&self) -> bool {
        self.str_at(&["boot", "loader", "firmware"]) == Some("uefi") || self.str_at(&["firmware", "mode"]) == Some("uefi")
    }

    pub fn hostname(&self) -> &str {
        self.str_at(&["host", "hostname"]).unwrap_or("?")
    }

    /// The source system as people name it: os-release's PRETTY_NAME, else
    /// its ID and VERSION_ID.
    pub fn os_name(&self) -> String {
        match self.str_at(&["os", "pretty_name"]).map(str::trim) {
            Some(p) if !p.is_empty() => p.to_string(),
            _ => {
                let id = self.str_at(&["os", "id"]).unwrap_or("?");
                match self.str_at(&["os", "version_id"]) {
                    Some(v) if !v.is_empty() => format!("{id} {v}"),
                    _ => id.to_string(),
                }
            }
        }
    }

    /// Every block device's entry by its kernel name on the source: disks,
    /// their partitions, md arrays and device-mapper devices.
    pub fn device(&self, name: &str) -> Option<&Value> {
        for sect in ["disks", "md_arrays", "dm_devices"] {
            for d in self.arr(&["storage", sect]) {
                if d.get("name").and_then(Value::as_str) == Some(name) {
                    return Some(d);
                }
                // A BSD disklabel's partitions (other/) are the disk's too.
                for k in ["partitions", "label_partitions"] {
                    for p in d.get(k).and_then(Value::as_array).into_iter().flatten() {
                        if p.get("name").and_then(Value::as_str) == Some(name) {
                            return Some(p);
                        }
                    }
                }
            }
        }
        None
    }

    /// The content (filesystem, container, member signature) of a source device.
    pub fn content(&self, name: &str) -> Option<&Value> {
        self.device(name).and_then(|d| d.get("content")).filter(|c| !c.is_null())
    }

    /// The source devices a device-mapper or md device sits on.
    pub fn slaves(&self, name: &str) -> Vec<String> {
        let Some(d) = self.device(name) else { return vec![] };
        if let Some(m) = d.at(&["md", "members"]).and_then(Value::as_array) {
            return m.iter().filter_map(|x| x.get("device").and_then(Value::as_str).map(str::to_string)).collect();
        }
        d.get("slaves").and_then(Value::as_array).into_iter().flatten().filter_map(|x| x.as_str().map(str::to_string)).collect()
    }

    /// The crypttab line for a LUKS container, found by the name it was
    /// opened under or by its UUID.
    pub fn crypttab_entry(&self, dm_name: &str, luks_uuid: Option<&str>) -> Option<&Value> {
        self.arr(&["storage", "crypttab"]).iter().find(|e| {
            e.get("name").and_then(Value::as_str) == Some(dm_name)
                || luks_uuid.is_some_and(|u| e.get("device").and_then(Value::as_str).is_some_and(|d| d.eq_ignore_ascii_case(&format!("UUID={u}"))))
        })
    }
}

#[cfg(test)]
pub mod sample {
    /// The same dump with the figures --fit-largest needs: the root
    /// filesystem's size on its partition, and how much of it is used.
    pub fn fittable() -> String {
        bios_mbr().replace("\"devid\": 1", "\"devid\": 1, \"total_bytes\": 14178844672").replace(
            "\"mounts\": []",
            "\"mounts\": [{\"target\": \"/\", \"source\": \"/dev/vda3\", \"device\": \"vda3\", \"fstype\": \"btrfs\", \"size_bytes\": 14178844672, \"used_bytes\": 2147483648}]",
        )
    }

    /// A minimal dump: Debian 13, one BIOS + MBR disk with /boot, swap and /.
    pub fn bios_mbr() -> String {
        r#"{
  "format": "bko-holt", "schema": 1, "privileged": true,
  "host": {"hostname": "deb13"},
  "os": {"id": "debian", "version_id": "13"},
  "firmware": {"mode": "bios"},
  "storage": {
    "disks": [{
      "name": "vda", "kind": "disk", "size_bytes": 17179869184, "logical_sector_size": 512,
      "model": null, "serial": null, "wwid": null,
      "partition_table": {"type": "dos", "sector_size": 512, "disk_signature": "1f2e3d4c"},
      "partitions": [
        {"name": "vda1", "number": 1, "start_lba": 2048, "sectors": 1953792, "type": "0x83", "bootable": true,
         "content": {"type": "ext4", "usage": "filesystem", "uuid": "0b0e2c6e-1111-4222-8333-444455556666", "label": null,
                     "block_size": 4096, "inode_size": 256, "inodes": 61056, "blocks_per_group": 32768,
                     "features": ["has_journal", "ext_attr", "resize_inode", "dir_index", "filetype", "extent", "64bit", "flex_bg", "sparse_super", "large_file", "huge_file", "dir_nlink", "extra_isize", "metadata_csum", "needs_recovery"],
                     "hash_seed": "9a6c1c0e-aaaa-4bbb-8ccc-dddddddddddd", "flex_bg_size": 16, "reserved_blocks": 12211, "blocks": 244224}},
        {"name": "vda2", "number": 2, "start_lba": 1955840, "sectors": 3905536, "type": "0x82", "bootable": false,
         "content": {"type": "swap", "usage": "other", "uuid": "5e5e5e5e-0000-4000-8000-000000000002", "label": null, "page_size": 4096}},
        {"name": "vda3", "number": 3, "start_lba": 5861376, "sectors": 27693056, "type": "0x83", "bootable": false,
         "content": {"type": "btrfs", "usage": "filesystem", "uuid": "b7b7b7b7-0000-4000-8000-000000000003", "label": null,
                     "node_size": 16384, "sector_size": 4096, "checksum": "crc32c", "devid": 1,
                     "features": ["mixed_backref", "big_metadata", "extended_iref", "skinny_metadata", "no_holes"],
                     "compat_ro_features": ["free_space_tree", "free_space_tree_valid", "block_group_tree"]}}
      ],
      "content": null, "boot_code": {"kind": "grub2"}
    }],
    "md_arrays": [], "dm_devices": [],
    "lvm": {"volume_groups": []},
    "zfs": {"pools_from_labels": [], "tools": null},
    "btrfs": [{"uuid": "b7b7b7b7-0000-4000-8000-000000000003", "label": null, "devices": ["vda3"],
               "allocation_profiles": {"data": ["single"], "metadata": ["dup"], "system": ["dup"]},
               "subvolumes": [{"id": 256, "top_level": 5, "path": "<FS_TREE>/@rootfs", "readonly": false, "properties": {"compression": "zstd"}}],
               "default_subvolume": {"id": 5}, "quota_enabled": false, "qgroups": null}],
    "xfs_external_logs": [],
    "fstab": [
      {"spec": "UUID=b7b7b7b7-0000-4000-8000-000000000003", "file": "/", "type": "btrfs", "options": "defaults,subvol=@rootfs", "freq": 0, "passno": 0, "resolves_to": "vda3"},
      {"spec": "UUID=0b0e2c6e-1111-4222-8333-444455556666", "file": "/boot", "type": "ext4", "options": "defaults", "freq": 0, "passno": 2, "resolves_to": "vda1"},
      {"spec": "UUID=5e5e5e5e-0000-4000-8000-000000000002", "file": "none", "type": "swap", "options": "sw", "freq": 0, "passno": 0, "resolves_to": "vda2"}
    ],
    "crypttab": null, "mounts": [], "swaps": []
  },
  "boot": {
    "loader": {"name": "grub", "firmware": "bios", "via_shim": false},
    "efi": null,
    "grub": {"debconf": {"grub-pc/install_devices": "/dev/disk/by-id/virtio-x"}},
    "kernels": {"installed": [{"version": "6.12.48+deb13-amd64", "running": true}]}
  }
}"#
        .to_string()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn parse(t: &str) -> Result<Layout, String> {
        Layout::from_json(crate::json::parse(t).unwrap())
    }

    #[test]
    fn reads_the_sample() {
        let l = parse(&sample::bios_mbr()).unwrap();
        assert_eq!(l.disks.len(), 1);
        let d = &l.disks[0];
        assert_eq!(d.name, "vda");
        assert_eq!(d.table.as_ref().unwrap().kind, TableKind::Mbr);
        assert_eq!(d.table.as_ref().unwrap().id.as_deref(), Some("0x1f2e3d4c"));
        assert_eq!(d.parts.len(), 3);
        assert!(d.parts[0].bootable);
        assert_eq!(d.parts[1].ptype, "0x82");
        assert!(d.used);
        assert!(!l.uefi());
        assert_eq!(l.content("vda3").and_then(|c| c.get("type")).and_then(Value::as_str), Some("btrfs"));
        assert_eq!(l.hostname(), "deb13");
    }

    #[test]
    fn contract_checks() {
        let good = sample::bios_mbr();
        assert!(parse(&good.replace("\"bko-holt\"", "\"bko-laydump\"")).is_ok());
        assert!(parse(&good.replace("\"bko-holt\"", "\"other\"")).unwrap_err().contains("not a bko-holt dump"));
        assert!(parse(&good.replace("\"schema\": 1", "\"schema\": 2")).unwrap_err().contains("schema"));
        assert!(parse(&good.replace("\"version_id\": \"13\"", "\"version_id\": \"11\"")).unwrap_err().contains("supports only Debian 13, Linux Mint 22"));
        let mint = good.replace("\"id\": \"debian\", \"version_id\": \"13\"", "\"id\": \"linuxmint\", \"version_id\": \"22.3\"");
        assert!(parse(&mint).is_ok());
        assert!(parse(&good.replace("\"privileged\": true", "\"privileged\": false")).unwrap_err().contains("as root"));
        assert!(parse(&good.replace("\"type\": \"dos\"", "\"type\": \"sun\"")).unwrap_err().contains("isn't one"));
    }
}
