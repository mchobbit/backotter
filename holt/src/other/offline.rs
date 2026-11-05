//! `bko-holt dump --disk DEV...` on Linux: the report of a system that
//! can't run bko-holt itself, read off its disks from a Linux rescue system
//! (Redox, whose sshd serves no logins and whose libc this doesn't build
//! for, and Sculpt OS, Genode, which has no shell).  The system is told by
//! what's on the disks: RedoxFS is Redox's; a GPT partition named GENODE*
//! holding ext2 is Sculpt's.  Neither has an fstab: the report makes up one
//! line, its root filesystem's.  Raw boot areas (a BIOS boot partition, an
//! ESP that nothing mounts, the MBR's code) are kept byte for byte, for
//! load to write back.

use super::{found_json, read_disk, Family, RawDisk};
use crate::bytes::{ByteSource, FileSource, OffsetSource};
use crate::json::{Obj, Value};
use crate::parttable::Table;

use crate::sys::Sys;

/// GPT's BIOS boot partition (GRUB's core, Redox's stage 2).
pub const BIOS_BOOT: &str = "21686148-6449-6e6f-744e-656564454649";

pub fn report(sys: &Sys, devs: &[String], generated: &str) -> Result<Value, String> {
    let mut disks_json = Vec::new();
    let mut fam: Option<Family> = None;
    let mut root: Option<(String, String, Option<String>)> = None;
    for dev in devs {
        let src = FileSource::open(&sys.p(dev)).map_err(|e| format!("{dev}: {e}"))?;
        let name = dev.trim_start_matches("/dev/").to_string();
        let d = RawDisk { name: name.clone(), path: dev.clone(), size: src.size(), sector: 512, model: None, serial: None };
        let nm = name.clone();
        let (table, found) = read_disk(&src, &d, &|n, _| crate::load::commands::part_path(&nm, n).trim_start_matches("/dev/").to_string());
        let mut parts = Vec::new();
        for f in &found {
            let mut extra = Obj::new();
            let ty = f.json.0.iter().find(|(k, _)| k == "type").and_then(|(_, v)| v.as_str()).unwrap_or("").to_ascii_lowercase();
            let label = f.json.0.iter().find(|(k, _)| k == "partlabel").and_then(|(_, v)| v.as_str()).unwrap_or("").to_string();
            if ty == BIOS_BOOT {
                extra.put("boot_code", Obj::new().set("kind", "bios-boot").set("image_b64", super::image_b64(&OffsetSource { inner: &src, start: f.start, len: f.size })));
            }
            super::esp_image(&src, f, &[], &mut extra);
            let c = f.contents.first();
            if c.is_some_and(|c| c.kind == "redoxfs") {
                fam = Some(Family::Redox);
                root = Some((f.name.clone(), "redoxfs".into(), c.and_then(|c| c.uuid.clone())));
            } else if label.starts_with("GENODE") && c.is_some_and(|c| c.kind.starts_with("ext")) {
                fam = fam.or(Some(Family::Genode));
                root = root.or(Some((f.name.clone(), c.map(|c| c.kind.clone()).unwrap_or_default(), c.and_then(|c| c.uuid.clone()))));
            }
            parts.push(found_json(f, vec![], extra));
        }
        let mut o = Obj::new()
            .set("name", name.as_str())
            .set("path", dev.as_str())
            .set("kind", "disk")
            .set("size_bytes", d.size)
            .set("size", crate::report::human(d.size))
            .set("logical_sector_size", 512u64)
            .set("partition_table", table.as_ref().map(|t| t.header_json(512, d.size / 512)).unwrap_or(Value::Null))
            .set("partitions", parts);
        if let Some(t) = &table {
            let code = t.boot_code();
            o.put(
                "boot_code",
                Obj::new()
                    .set("kind", crate::bootcode::identify(code).kind)
                    .set("first_bytes", crate::bytes::hex(crate::bytes::sub(code, 0, 16)))
                    .set("code_b64", (!crate::bytes::is_zero(code)).then(|| crate::bytes::base64(crate::bytes::sub(code, 0, 440))))
                    // GRUB's core.img in the gap after the MBR, when there is one.
                    .set("gap_b64", match t {
                        Table::Mbr(m) => m.parts.iter().map(|p| u64::from(p.start)).min().filter(|&s| s > 1).map(|s| super::image_b64(&OffsetSource { inner: &src, start: 512, len: (s - 1) * 512 })),
                        _ => None,
                    }),
            );
        }
        o.put("mounted_at", Vec::<String>::new());
        disks_json.push(o.build());
    }
    let fam = fam.ok_or("the disks hold neither RedoxFS (Redox) nor a GENODE partition with ext2 (Sculpt OS): bko-holt dump --disk reads only those")?;
    let (rootdev, rootfs, rootuuid) = root.unwrap_or_default();
    let (id, name) = match fam {
        Family::Redox => ("redox", "Redox"),
        _ => ("sculpt", "Sculpt OS"),
    };
    let os = Obj::new().set("family", fam.name()).set("id", id).set("version_id", Value::Null).set("pretty_name", name).set("read_offline", true).set("architecture", "x86_64");
    let fstab = vec![Obj::new()
        .set("spec", rootuuid.map(|u| format!("UUID={u}")).unwrap_or_else(|| format!("/dev/{rootdev}")))
        .set("file", "/")
        .set("type", rootfs.as_str())
        .set("options", "")
        .set("freq", 0)
        .set("passno", 0)
        .set("resolves_to", rootdev.as_str())
        .set("made_up", true)
        .build()];
    let storage = Obj::new().set("disks", disks_json).set("mounts", Vec::<Value>::new()).set("swaps", Vec::<Value>::new()).set("fstab", fstab);
    let boot = Obj::new().set("loader", Obj::new().set("name", id).set("firmware", "bios").set("evidence", vec!["read off the disks: their boot areas are kept byte for byte".to_string()]));
    let mut out = Obj::new()
        .set("format", crate::report::FORMAT)
        .set("schema", crate::report::SCHEMA)
        .set("tool", Obj::new().set("name", "bko-holt").set("version", env!("CARGO_PKG_VERSION")))
        .set("generated", generated)
        .set("privileged", sys.privileged)
        .set("host", Obj::new().set("hostname", Value::Null))
        .set("os", os)
        .set("firmware", Obj::new().set("mode", "bios"))
        .set("storage", storage)
        .set("boot", boot);
    out.put("warnings", sys.warnings());
    Ok(out.build())
}
