//! LVM2 physical volumes: the PV label and header, and the volume group's
//! metadata text from the PV's metadata area, parsed into JSON.

use super::Content;
use crate::bytes::{le32, le64, sub, ByteSource};
use crate::json::{Obj, Value};

const LABEL_ID: &[u8] = b"LABELONE";
const LVM2_TYPE: &[u8] = b"LVM2 001";
const MDA_MAGIC: &[u8] = b" LVM2 x[5A%r0N*>";
const MDA_HEADER_SIZE: u64 = 512;
const MAX_METADATA: u64 = 16 << 20;

/// An LVM UUID (32 characters) in LVM's 6-4-4-4-4-4-6 grouping.
pub fn format_uuid(raw: &str) -> String {
    if raw.len() != 32 || !raw.is_ascii() {
        return raw.to_string();
    }
    let g = [6, 4, 4, 4, 4, 4, 6];
    let mut out = String::new();
    let mut i = 0;
    for (n, len) in g.iter().enumerate() {
        if n > 0 {
            out.push('-');
        }
        out.push_str(&raw[i..i + len]);
        i += len;
    }
    out
}

pub fn probe(src: &dyn ByteSource) -> Option<Content> {
    let head = src.read_exact_at(0, 2048)?;
    let (sector, label) = (0..4).map(|s| (s, sub(&head, s * 512, 512))).find(|(_, l)| sub(l, 0, 8) == LABEL_ID)?;
    if sub(label, 24, 8) != LVM2_TYPE {
        return None;
    }
    let ph_off = le32(label, 20) as usize;
    let ph = sub(&head, sector * 512 + ph_off, 2048 - (sector * 512 + ph_off).min(2048));
    let pv_uuid = crate::bytes::cstr(sub(ph, 0, 32));
    let dev_size = le64(ph, 32);
    // Two lists of (offset, size), each ended by a zero entry: data areas,
    // then metadata areas.
    let mut o = 40;
    let mut lists: [Vec<(u64, u64)>; 2] = [vec![], vec![]];
    for list in lists.iter_mut() {
        while o + 16 <= ph.len() {
            let (off, size) = (le64(ph, o), le64(ph, o + 8));
            o += 16;
            if off == 0 && size == 0 {
                break;
            }
            list.push((off, size));
            if list.len() > 8 {
                break;
            }
        }
    }
    let [data_areas, mdas] = lists;
    let mut d = Obj::new()
        .set("label_sector", sector)
        .set("device_size", dev_size)
        .set("data_area_offset", data_areas.first().map(|a| a.0))
        .set("metadata_areas", mdas.iter().map(|(o, s)| Obj::new().set("offset", *o).set("size", *s).build()).collect::<Vec<_>>());
    let mut extra = None;
    for &(off, size) in &mdas {
        match read_metadata(src, off, size) {
            Ok(text) => match parse_config(&text) {
                Ok(cfg) => {
                    if let Some(vg) = vg_summary(&cfg) {
                        d.put("vg_name", vg.get("name").cloned().unwrap_or(Value::Null));
                        d.put("vg_uuid", vg.get("id").cloned().unwrap_or(Value::Null));
                        extra = Some(vg);
                    }
                    break;
                }
                Err(e) => d.put("metadata_error", e),
            },
            Err(e) => d.put("metadata_error", e),
        }
    }
    if mdas.is_empty() {
        d.put("vg_name", Value::Null);
    }
    Some(Content {
        kind: "LVM2_member".into(),
        usage: "raid",
        uuid: Some(format_uuid(&pv_uuid)),
        label: None,
        version: Some("LVM2 001".into()),
        details: d,
        extra,
    })
}

/// The current metadata text from the metadata area at `off`.
fn read_metadata(src: &dyn ByteSource, off: u64, size: u64) -> Result<String, String> {
    let h = src.read_exact_at(off, MDA_HEADER_SIZE as usize).ok_or("metadata area unreadable")?;
    if sub(&h, 4, 16) != MDA_MAGIC {
        return Err("no metadata area header".into());
    }
    let area_size = le64(&h, 32);
    let (loc_off, loc_size, flags) = (le64(&h, 40), le64(&h, 48), le32(&h, 60));
    if loc_off == 0 || loc_size == 0 {
        return Err("metadata area is empty (PV not in a volume group)".into());
    }
    if flags & 1 != 0 {
        return Err("metadata area is ignored".into());
    }
    if loc_size > MAX_METADATA || area_size <= MDA_HEADER_SIZE || (size != 0 && area_size > size) || loc_off >= area_size || loc_off < MDA_HEADER_SIZE {
        return Err("metadata location out of range".into());
    }
    let first = loc_size.min(area_size - loc_off);
    let mut text = src.read_at(off + loc_off, first as usize);
    if first < loc_size {
        // The area is a ring buffer after its header: wrap around.
        text.extend(src.read_at(off + MDA_HEADER_SIZE, (loc_size - first) as usize));
    }
    if text.len() as u64 != loc_size {
        return Err("metadata text truncated".into());
    }
    Ok(crate::bytes::cstr(&text))
}

// --- LVM's configuration syntax ---------------------------------------------

#[derive(Debug, Clone, PartialEq)]
enum Tok {
    Ident(String),
    Str(String),
    Num(Value),
    Punct(char),
}

fn tokenize(text: &str) -> Result<Vec<Tok>, String> {
    let mut out = Vec::new();
    let c: Vec<char> = text.chars().collect();
    let mut i = 0;
    while i < c.len() {
        let ch = c[i];
        match ch {
            ' ' | '\t' | '\n' | '\r' => i += 1,
            '#' => {
                while i < c.len() && c[i] != '\n' {
                    i += 1;
                }
            }
            '{' | '}' | '[' | ']' | '=' | ',' => {
                out.push(Tok::Punct(ch));
                i += 1;
            }
            '"' => {
                let mut s = String::new();
                i += 1;
                loop {
                    match c.get(i) {
                        None => return Err("unterminated string".into()),
                        Some('"') => {
                            i += 1;
                            break;
                        }
                        Some('\\') => {
                            if let Some(&n) = c.get(i + 1) {
                                s.push(n);
                            }
                            i += 2;
                        }
                        Some(&x) => {
                            s.push(x);
                            i += 1;
                        }
                    }
                }
                out.push(Tok::Str(s));
            }
            _ if ch.is_alphanumeric() || "_.+-/".contains(ch) => {
                let start = i;
                while i < c.len() && (c[i].is_alphanumeric() || "_.+-/".contains(c[i])) {
                    i += 1;
                }
                let t: String = c[start..i].iter().collect();
                // Names may start with a digit (an LV called "2024data"):
                // only a word that parses whole is a number.
                let num = if t.starts_with(|x: char| x.is_ascii_digit() || x == '-') {
                    t.parse::<i64>()
                        .map(|n| if n >= 0 { Value::UInt(n as u64) } else { Value::Int(n) })
                        .ok()
                        .or_else(|| t.parse::<f64>().ok().filter(|f| f.is_finite()).map(Value::Float))
                } else {
                    None
                };
                out.push(match num {
                    Some(n) => Tok::Num(n),
                    None => Tok::Ident(t),
                });
            }
            _ => return Err(format!("unexpected character {ch:?}")),
        }
    }
    Ok(out)
}

/// Parses LVM metadata text ("name { key = value ... }", arrays in [ ]) into
/// JSON objects, keys in their order.
pub fn parse_config(text: &str) -> Result<Value, String> {
    let toks = tokenize(text)?;
    let mut i = 0;
    let v = section_body(&toks, &mut i, 0)?;
    if i != toks.len() {
        return Err("unexpected '}'".into());
    }
    Ok(v)
}

fn section_body(t: &[Tok], i: &mut usize, depth: usize) -> Result<Value, String> {
    if depth > 64 {
        return Err("nested too deeply".into());
    }
    let mut o = Obj::new();
    while *i < t.len() {
        let key = match &t[*i] {
            Tok::Punct('}') => break,
            Tok::Ident(k) | Tok::Str(k) => k.clone(),
            other => return Err(format!("expected a name, found {other:?}")),
        };
        *i += 1;
        match t.get(*i) {
            Some(Tok::Punct('{')) => {
                *i += 1;
                let v = section_body(t, i, depth + 1)?;
                if t.get(*i) != Some(&Tok::Punct('}')) {
                    return Err(format!("section {key} not closed"));
                }
                *i += 1;
                o.put(&key, v);
            }
            Some(Tok::Punct('=')) => {
                *i += 1;
                let v = value(t, i)?;
                o.put(&key, v);
            }
            _ => return Err(format!("expected '=' or '{{' after {key}")),
        }
    }
    Ok(o.build())
}

fn value(t: &[Tok], i: &mut usize) -> Result<Value, String> {
    let v = match t.get(*i) {
        Some(Tok::Str(s)) => Value::Str(s.clone()),
        Some(Tok::Num(n)) => n.clone(),
        Some(Tok::Ident(s)) => Value::Str(s.clone()),
        Some(Tok::Punct('[')) => {
            *i += 1;
            let mut a = Vec::new();
            loop {
                match t.get(*i) {
                    Some(Tok::Punct(']')) => break,
                    None => return Err("array not closed".into()),
                    _ => {}
                }
                a.push(value(t, i)?);
                match t.get(*i) {
                    Some(Tok::Punct(',')) => *i += 1,
                    Some(Tok::Punct(']')) => {}
                    _ => return Err("expected ',' or ']'".into()),
                }
            }
            Value::Arr(a)
        }
        other => return Err(format!("expected a value, found {other:?}")),
    };
    *i += 1;
    Ok(v)
}

/// The volume group in parsed metadata: the one top-level section that has
/// an id and a seqno.
pub fn vg_summary(cfg: &Value) -> Option<Value> {
    let (name, vg) = cfg.as_object()?.iter().find(|(_, v)| v.get("id").is_some() && v.get("seqno").is_some())?;
    let u = |v: &Value, k: &str| v.get(k).and_then(Value::as_u64);
    let extent_sectors = u(vg, "extent_size").unwrap_or(0);
    let mut pvs = Vec::new();
    if let Some(Value::Obj(list)) = vg.get("physical_volumes") {
        for (pn, pv) in list {
            pvs.push(
                Obj::new()
                    .set("name", pn.as_str())
                    .set("id", pv.get("id").cloned().unwrap_or(Value::Null))
                    .set("device_hint", pv.get("device").cloned().unwrap_or(Value::Null))
                    .set("dev_size_bytes", u(pv, "dev_size").map(|s| s * 512))
                    .set("pe_start_bytes", u(pv, "pe_start").map(|s| s * 512))
                    .set("pe_count", u(pv, "pe_count"))
                    .set("status", pv.get("status").cloned().unwrap_or(Value::Arr(vec![])))
                    .build(),
            );
        }
    }
    let mut lvs = Vec::new();
    if let Some(Value::Obj(list)) = vg.get("logical_volumes") {
        for (ln, lv) in list {
            let mut segs = Vec::new();
            let mut extents = 0;
            for (k, seg) in lv.as_object().into_iter().flatten() {
                if k.starts_with("segment") && seg.as_object().is_some() {
                    extents += u(seg, "extent_count").unwrap_or(0);
                    segs.push(seg.clone());
                }
            }
            lvs.push(
                Obj::new()
                    .set("name", ln.as_str())
                    .set("id", lv.get("id").cloned().unwrap_or(Value::Null))
                    .set("status", lv.get("status").cloned().unwrap_or(Value::Arr(vec![])))
                    .set("flags", lv.get("flags").cloned().unwrap_or(Value::Arr(vec![])))
                    .set("size_bytes", extents * extent_sectors * 512)
                    .set("creation_host", lv.get("creation_host").cloned().unwrap_or(Value::Null))
                    .set("creation_time", lv.get("creation_time").cloned().unwrap_or(Value::Null))
                    // Only where the LV has tags (load's lvcreate of a thin
                    // LV adds them).
                    .set_some("tags", lv.get("tags").filter(|t| t.as_array().is_some_and(|a| !a.is_empty())).cloned())
                    .set("segments", segs)
                    .build(),
            );
        }
    }
    Some(
        Obj::new()
            .set("name", name.as_str())
            .set("id", vg.get("id").cloned().unwrap_or(Value::Null))
            .set("seqno", u(vg, "seqno"))
            .set("format", vg.get("format").cloned().unwrap_or(Value::Null))
            .set("status", vg.get("status").cloned().unwrap_or(Value::Arr(vec![])))
            .set("flags", vg.get("flags").cloned().unwrap_or(Value::Arr(vec![])))
            .set("extent_size_bytes", extent_sectors * 512)
            .set("max_lv", u(vg, "max_lv"))
            .set("max_pv", u(vg, "max_pv"))
            .set("metadata_copies", u(vg, "metadata_copies"))
            .set("physical_volumes", pvs)
            .set("logical_volumes", lvs)
            .set("creation_host", cfg.get("creation_host").cloned().unwrap_or(Value::Null))
            .set("creation_time", cfg.get("creation_time").cloned().unwrap_or(Value::Null))
            .build(),
    )
}

#[cfg(test)]
pub mod sample {
    pub const METADATA: &str = r#"vg0 {
id = "Yb3kFa-0Pz1-AbCd-EfGh-IjKl-MnOp-QrStUv"
seqno = 4
format = "lvm2"			# informational
status = ["RESIZEABLE", "READ", "WRITE"]
flags = []
extent_size = 8192		# 4 Megabytes
max_lv = 0
max_pv = 0
metadata_copies = 0

physical_volumes {

pv0 {
id = "Hc7n2m-aaaa-bbbb-cccc-dddd-eeee-ffffff"
device = "/dev/vda5"	# Hint only

status = ["ALLOCATABLE"]
flags = []
dev_size = 31455232	# 15 Gigabytes
pe_start = 2048
pe_count = 3839	# 14.9961 Gigabytes
}
}

logical_volumes {

root {
id = "r00t00-aaaa-bbbb-cccc-dddd-eeee-ffffff"
status = ["READ", "WRITE", "VISIBLE"]
flags = []
creation_time = 1762300000	# 2025-11-05 00:00:00 +0000
creation_host = "debian"
segment_count = 1

segment1 {
start_extent = 0
extent_count = 3000	# 11.7188 Gigabytes

type = "striped"
stripe_count = 1	# linear

stripes = [
"pv0", 0
]
}
}

swap_1 {
id = "sw4p00-aaaa-bbbb-cccc-dddd-eeee-ffffff"
status = ["READ", "WRITE", "VISIBLE"]
flags = []
creation_time = 1762300001
creation_host = "debian"
segment_count = 2

segment1 {
start_extent = 0
extent_count = 200

type = "striped"
stripe_count = 1

stripes = [
"pv0", 3000
]
}
segment2 {
start_extent = 200
extent_count = 39

type = "striped"
stripe_count = 1

stripes = [
"pv0", 3200
]
}
}
}

}
# Generated by LVM2 version 2.03.31(2) (2025-02-27): Wed Nov  5 00:00:01 2025

contents = "Text Format Volume Group"
version = 1

description = "Created *after* executing 'lvcreate'"

creation_host = "debian"	# Linux debian 6.12.48+deb13-amd64 #1 SMP PREEMPT_DYNAMIC Debian 6.12.48-1 x86_64
creation_time = 1762300001	# Wed Nov  5 00:00:01 2025
"#;

    /// Vanilla OS 3's VG `vos-root` (docs/design/vanillaos.md), as LVM
    /// writes it after the installer's make-thin-pool: the linear `init`
    /// (1 GiB), the thin pool `root` (data 19 GiB in root_tdata, metadata
    /// 1 GiB in root_tmeta, the 1 GiB lvol0_pmspare), and the thin LVs
    /// `root-a` and `root-b`, 19 GiB virtual each.  The hidden LVs have no
    /// VISIBLE in their status.  Its PV is pv()'s (Hc7n2m-...).
    pub const THIN: &str = r#"vos-root {
id = "VoSr00-tAAA-BBBB-CCCC-DDDD-EEEE-FFFFFF"
seqno = 9
format = "lvm2"			# informational
status = ["RESIZEABLE", "READ", "WRITE"]
flags = []
extent_size = 8192		# 4 Megabytes
max_lv = 0
max_pv = 0
metadata_copies = 0

physical_volumes {

pv0 {
id = "Hc7n2m-aaaa-bbbb-cccc-dddd-eeee-ffffff"
device = "/dev/vda3"	# Hint only

status = ["ALLOCATABLE"]
flags = []
dev_size = 46348288	# 22.1006 Gigabytes
pe_start = 2048
pe_count = 5657	# 22.0977 Gigabytes
}
}

logical_volumes {

init {
id = "1n1t00-aaaa-bbbb-cccc-dddd-eeee-ffffff"
status = ["READ", "WRITE", "VISIBLE"]
flags = []
creation_time = 1762300000	# 2025-11-05 00:00:00 +0000
creation_host = "vanilla"
segment_count = 1

segment1 {
start_extent = 0
extent_count = 256	# 1024 Megabytes

type = "striped"
stripe_count = 1	# linear

stripes = [
"pv0", 0
]
}
}

root {
id = "p00l00-aaaa-bbbb-cccc-dddd-eeee-ffffff"
status = ["READ", "WRITE", "VISIBLE"]
flags = []
creation_time = 1762300001
creation_host = "vanilla"
segment_count = 1

segment1 {
start_extent = 0
extent_count = 4864	# 19 Gigabytes

type = "thin-pool"
metadata = "root_tmeta"
pool = "root_tdata"
transaction_id = 2
chunk_size = 128	# 64 Kilobytes
discards = "passdown"
zero_new_blocks = 1
}
}

root-a {
id = "r00ta0-aaaa-bbbb-cccc-dddd-eeee-ffffff"
status = ["READ", "WRITE", "VISIBLE"]
flags = []
creation_time = 1762300002
creation_host = "vanilla"
segment_count = 1

segment1 {
start_extent = 0
extent_count = 4864	# 19 Gigabytes

type = "thin"
thin_pool = "root"
transaction_id = 0
device_id = 1
}
}

root-b {
id = "r00tb0-aaaa-bbbb-cccc-dddd-eeee-ffffff"
status = ["READ", "WRITE", "VISIBLE"]
flags = []
creation_time = 1762300003
creation_host = "vanilla"
segment_count = 1

segment1 {
start_extent = 0
extent_count = 4864	# 19 Gigabytes

type = "thin"
thin_pool = "root"
transaction_id = 1
device_id = 2
}
}

lvol0_pmspare {
id = "sp4re0-aaaa-bbbb-cccc-dddd-eeee-ffffff"
status = ["READ", "WRITE"]
flags = []
creation_time = 1762300001
creation_host = "vanilla"
segment_count = 1

segment1 {
start_extent = 0
extent_count = 256	# 1024 Megabytes

type = "striped"
stripe_count = 1	# linear

stripes = [
"pv0", 256
]
}
}

root_tmeta {
id = "tm3ta0-aaaa-bbbb-cccc-dddd-eeee-ffffff"
status = ["READ", "WRITE"]
flags = []
creation_time = 1762300000
creation_host = "vanilla"
segment_count = 1

segment1 {
start_extent = 0
extent_count = 256	# 1024 Megabytes

type = "striped"
stripe_count = 1	# linear

stripes = [
"pv0", 512
]
}
}

root_tdata {
id = "td4ta0-aaaa-bbbb-cccc-dddd-eeee-ffffff"
status = ["READ", "WRITE"]
flags = []
creation_time = 1762300000
creation_host = "vanilla"
segment_count = 1

segment1 {
start_extent = 0
extent_count = 4864	# 19 Gigabytes

type = "striped"
stripe_count = 1	# linear

stripes = [
"pv0", 768
]
}
}
}

}
# Generated by LVM2 version 2.03.31(2) (2025-02-27): Wed Nov  5 00:00:03 2025

contents = "Text Format Volume Group"
version = 1

description = "Created *after* executing 'lvcreate -n root-b -V 19456M --thinpool vos-root/root'"

creation_host = "vanilla"	# Linux vanilla 6.17.8-amd64 x86_64
creation_time = 1762300003	# Wed Nov  5 00:00:03 2025
"#;

    /// thin_vg: THIN as the dump has it (vg_summary's JSON).
    pub fn thin_vg() -> crate::json::Value {
        super::vg_summary(&super::parse_config(THIN).unwrap()).unwrap()
    }

    /// A PV with label in sector 1, one metadata area at 4096 (size 1 MiB)
    /// holding METADATA at `text_at` within the area (wrapping if needed).
    pub fn pv(text_at: u64, area_size: u64) -> Vec<u8> {
        pv_with(text_at, area_size, METADATA)
    }

    /// As `pv`, with `metadata` in place of METADATA.
    pub fn pv_with(text_at: u64, area_size: u64, metadata: &str) -> Vec<u8> {
        let mut img = vec![0u8; 4 << 20];
        let l = &mut img[512..1024];
        l[0..8].copy_from_slice(b"LABELONE");
        l[8..16].copy_from_slice(&1u64.to_le_bytes());
        l[20..24].copy_from_slice(&32u32.to_le_bytes());
        l[24..32].copy_from_slice(b"LVM2 001");
        let ph = &mut l[32..];
        ph[0..32].copy_from_slice(b"Hc7n2maaaabbbbccccddddeeeeffffff");
        ph[32..40].copy_from_slice(&(4u64 << 20).to_le_bytes());
        ph[40..48].copy_from_slice(&(1u64 << 20).to_le_bytes()); // data area
        // (terminator at 56..72 left zero), then the metadata area:
        ph[72..80].copy_from_slice(&4096u64.to_le_bytes());
        ph[80..88].copy_from_slice(&area_size.to_le_bytes());
        let text = metadata.as_bytes();
        let h = &mut img[4096..4096 + 512];
        h[4..20].copy_from_slice(b" LVM2 x[5A%r0N*>");
        h[20..24].copy_from_slice(&1u32.to_le_bytes());
        h[24..32].copy_from_slice(&4096u64.to_le_bytes());
        h[32..40].copy_from_slice(&area_size.to_le_bytes());
        h[40..48].copy_from_slice(&text_at.to_le_bytes());
        h[48..56].copy_from_slice(&(text.len() as u64).to_le_bytes());
        let first = (text.len() as u64).min(area_size - text_at) as usize;
        let start = 4096 + text_at as usize;
        img[start..start + first].copy_from_slice(&text[..first]);
        if first < text.len() {
            img[4096 + 512..4096 + 512 + text.len() - first].copy_from_slice(&text[first..]);
        }
        img
    }
}

#[cfg(test)]
mod tests {
    use super::sample::*;
    use super::*;
    use crate::bytes::MemSource;

    #[test]
    fn uuid_grouping() {
        assert_eq!(format_uuid("Hc7n2maaaabbbbccccddddeeeeffffff"), "Hc7n2m-aaaa-bbbb-cccc-dddd-eeee-ffffff");
        assert_eq!(format_uuid("short"), "short");
    }

    #[test]
    fn parses_installer_metadata() {
        let cfg = parse_config(METADATA).unwrap();
        assert_eq!(cfg.at(&["vg0", "seqno"]).and_then(Value::as_u64), Some(4));
        assert_eq!(cfg.at(&["vg0", "status", "1"]).and_then(Value::as_str), Some("READ"));
        assert_eq!(cfg.at(&["vg0", "logical_volumes", "root", "segment1", "stripes", "0"]).and_then(Value::as_str), Some("pv0"));
        assert_eq!(cfg.get("contents").and_then(Value::as_str), Some("Text Format Volume Group"));
        let vg = vg_summary(&cfg).unwrap();
        assert_eq!(vg.get("name").and_then(Value::as_str), Some("vg0"));
        assert_eq!(vg.get("extent_size_bytes").and_then(Value::as_u64), Some(4 << 20));
        assert_eq!(vg.at(&["physical_volumes", "0", "pe_start_bytes"]).and_then(Value::as_u64), Some(1 << 20));
        assert_eq!(vg.at(&["physical_volumes", "0", "device_hint"]).and_then(Value::as_str), Some("/dev/vda5"));
        assert_eq!(vg.at(&["logical_volumes", "0", "name"]).and_then(Value::as_str), Some("root"));
        assert_eq!(vg.at(&["logical_volumes", "0", "size_bytes"]).and_then(Value::as_u64), Some(3000 * (4 << 20)));
        assert_eq!(vg.at(&["logical_volumes", "1", "size_bytes"]).and_then(Value::as_u64), Some(239 * (4 << 20)));
        assert_eq!(vg.at(&["logical_volumes", "1", "segments", "1", "start_extent"]).and_then(Value::as_u64), Some(200));
        assert_eq!(vg.get("creation_host").and_then(Value::as_str), Some("debian"));
    }

    /// A thin pool's and its thin LVs' segments come through with their own
    /// keys, and the hidden LVs keep their status (no VISIBLE).
    #[test]
    fn parses_thin_metadata() {
        let vg = thin_vg();
        assert_eq!(vg.get("name").and_then(Value::as_str), Some("vos-root"));
        let lvs = vg.get("logical_volumes").and_then(Value::as_array).unwrap();
        let names: Vec<&str> = lvs.iter().filter_map(|l| l.get("name").and_then(Value::as_str)).collect();
        assert_eq!(names, vec!["init", "root", "root-a", "root-b", "lvol0_pmspare", "root_tmeta", "root_tdata"]);
        let pool = lvs[1].at(&["segments", "0"]).unwrap();
        assert_eq!(pool.get("type").and_then(Value::as_str), Some("thin-pool"));
        assert_eq!(pool.get("metadata").and_then(Value::as_str), Some("root_tmeta"));
        assert_eq!(pool.get("pool").and_then(Value::as_str), Some("root_tdata"));
        assert_eq!(pool.get("chunk_size").and_then(Value::as_u64), Some(128));
        assert_eq!(pool.get("discards").and_then(Value::as_str), Some("passdown"));
        assert_eq!(pool.get("zero_new_blocks").and_then(Value::as_u64), Some(1));
        let thin = lvs[3].at(&["segments", "0"]).unwrap();
        assert_eq!(thin.get("thin_pool").and_then(Value::as_str), Some("root"));
        assert_eq!(thin.get("device_id").and_then(Value::as_u64), Some(2));
        assert_eq!(lvs[3].get("size_bytes").and_then(Value::as_u64), Some(19 << 30));
        assert_eq!(lvs[6].get("status").map(Value::compact).as_deref(), Some(r#"["READ","WRITE"]"#));
    }

    #[test]
    fn pv_probe_reads_metadata() {
        let c = probe(&MemSource(pv(512, 1 << 20))).unwrap();
        assert_eq!(c.kind, "LVM2_member");
        assert_eq!(c.uuid.as_deref(), Some("Hc7n2m-aaaa-bbbb-cccc-dddd-eeee-ffffff"));
        let d = c.details.build();
        assert_eq!(d.get("label_sector").and_then(Value::as_u64), Some(1));
        assert_eq!(d.get("vg_name").and_then(Value::as_str), Some("vg0"));
        assert_eq!(d.get("data_area_offset").and_then(Value::as_u64), Some(1 << 20));
        assert_eq!(d.at(&["metadata_areas", "0", "offset"]).and_then(Value::as_u64), Some(4096));
        assert_eq!(c.extra.unwrap().get("seqno").and_then(Value::as_u64), Some(4));
    }

    #[test]
    fn pv_metadata_wraps_around() {
        let area = 8192u64;
        let at = area - 1000;
        let c = probe(&MemSource(pv(at, area))).unwrap();
        assert!(c.details.clone().build().get("metadata_error").is_none(), "{:?}", c.details.build());
        assert_eq!(c.extra.unwrap().get("name").and_then(Value::as_str), Some("vg0"));
    }

    #[test]
    fn pv_metadata_problems() {
        // Empty location: an orphan PV.
        let mut img = pv(512, 1 << 20);
        img[4096 + 40..4096 + 56].fill(0);
        let c = probe(&MemSource(img)).unwrap();
        assert!(c.details.build().get("metadata_error").unwrap().as_str().unwrap().contains("not in a volume group"));
        // Ignored area.
        let mut img = pv(512, 1 << 20);
        img[4096 + 60] = 1;
        let c = probe(&MemSource(img)).unwrap();
        assert!(c.details.build().get("metadata_error").unwrap().as_str().unwrap().contains("ignored"));
        // No mda header magic.
        let mut img = pv(512, 1 << 20);
        img[4096 + 4] = b'x';
        assert!(probe(&MemSource(img)).unwrap().details.build().get("metadata_error").is_some());
        // Garbage text.
        let mut img = pv(512, 1 << 20);
        img[4096 + 512] = b'{';
        let c = probe(&MemSource(img)).unwrap();
        assert!(c.extra.is_none());
        assert!(c.details.build().get("metadata_error").is_some());
        // Absurd location.
        let mut img = pv(512, 1 << 20);
        img[4096 + 48..4096 + 56].copy_from_slice(&(1u64 << 40).to_le_bytes());
        assert!(probe(&MemSource(img)).unwrap().details.build().get("metadata_error").unwrap().as_str().unwrap().contains("range"));
    }

    #[test]
    fn not_a_pv() {
        assert!(probe(&MemSource(vec![0u8; 4096])).is_none());
        let mut img = pv(512, 1 << 20);
        img[512 + 24] = b'X';
        assert!(probe(&MemSource(img)).is_none());
        // A label in sector 0 is found too.
        let img = pv(512, 1 << 20);
        let mut moved = img.clone();
        moved[0..512].copy_from_slice(&img[512..1024]);
        moved[512..1024].fill(0);
        let c = probe(&MemSource(moved)).unwrap();
        assert_eq!(c.details.build().get("label_sector").and_then(Value::as_u64), Some(0));
    }

    #[test]
    fn config_syntax() {
        let v = parse_config("2024data { x = 1 }\nver = 1.2.3\na = -3\nb = 1.5\nc = [ ]\nd = [1, \"x\\\"y\", 2]\ne { f = bare_word }\n\"q k\" = 1").unwrap();
        assert_eq!(v.at(&["2024data", "x"]).and_then(Value::as_u64), Some(1));
        assert_eq!(v.get("ver").and_then(Value::as_str), Some("1.2.3"));
        assert_eq!(v.get("a"), Some(&Value::Int(-3)));
        assert_eq!(v.get("b"), Some(&Value::Float(1.5)));
        assert_eq!(v.get("c"), Some(&Value::Arr(vec![])));
        assert_eq!(v.at(&["d", "1"]).and_then(Value::as_str), Some("x\"y"));
        assert_eq!(v.at(&["e", "f"]).and_then(Value::as_str), Some("bare_word"));
        assert_eq!(v.get("q k").and_then(Value::as_u64), Some(1));
        for bad in ["a = ", "a {", "a = [1", "a = [1 2]", "}", "a b", "a = \"x", "= 1", "a = $"] {
            assert!(parse_config(bad).is_err(), "accepted {bad:?}");
        }
        let deep = "a { ".repeat(100) + &"} ".repeat(100);
        assert!(parse_config(&deep).is_err());
    }
}
