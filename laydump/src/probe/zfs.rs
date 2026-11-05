//! ZFS vdev labels.  Each label holds the pool's configuration as an
//! XDR-encoded nvlist (the format of /etc/zfs/zpool.cache too), parsed here
//! into JSON so a pool's layout shows even when it isn't imported.

use super::Content;
use crate::bytes::{be32, be64, ByteSource};
use crate::json::{Obj, Value};

const LABEL_SIZE: u64 = 256 << 10;
const NVLIST_OFFSET: u64 = 16 << 10;
const NVLIST_SIZE: usize = 112 << 10;

/// Offsets of the four labels on a device of `size` bytes.
pub fn label_offsets(size: u64) -> Vec<u64> {
    let psize = size - size % LABEL_SIZE;
    if psize < 4 * LABEL_SIZE {
        return vec![0];
    }
    vec![0, LABEL_SIZE, psize - 2 * LABEL_SIZE, psize - LABEL_SIZE]
}

pub fn probe(src: &dyn ByteSource) -> Option<Content> {
    let mut valid = 0;
    let mut front = false;
    let mut config = None;
    for (i, off) in label_offsets(src.size()).into_iter().enumerate() {
        let raw = src.read_at(off + NVLIST_OFFSET, NVLIST_SIZE);
        if let Ok(v) = unpack(&raw) {
            if v.get("pool_guid").is_some() || v.get("guid").is_some() {
                valid += 1;
                front |= i < 2;
                if config.is_none() {
                    config = Some(v);
                }
            }
        }
    }
    // Labels 2 and 3 sit at the end, where a disk whose last partition is a
    // vdev has them too: only a device with a label at its start is a vdev.
    if !front {
        return None;
    }
    let cfg = config?;
    let u = |k: &str| cfg.get(k).and_then(Value::as_u64);
    let state = match u("state") {
        Some(0) => "active",
        Some(1) => "exported",
        Some(2) => "destroyed",
        Some(3) => "spare",
        Some(4) => "l2cache",
        Some(5) => "uninitialized",
        _ => "unknown",
    };
    let d = Obj::new()
        .set("labels_valid", valid)
        .set("pool_name", cfg.get("name").cloned().unwrap_or(Value::Null))
        .set("pool_guid", u("pool_guid").map(|g| g.to_string()))
        .set("vdev_guid", u("guid").map(|g| g.to_string()))
        .set("top_guid", u("top_guid").map(|g| g.to_string()))
        .set("pool_state", state)
        .set("txg", u("txg"))
        .set("spa_version", u("version"))
        .set("hostname", cfg.get("hostname").cloned().unwrap_or(Value::Null))
        .set("hostid", u("hostid"));
    Some(Content {
        kind: "zfs_member".into(),
        usage: "filesystem",
        uuid: u("pool_guid").map(|g| g.to_string()),
        label: cfg.get("name").and_then(Value::as_str).map(str::to_string),
        version: u("version").map(|v| v.to_string()),
        details: d,
        extra: Some(cfg),
    })
}

// --- XDR nvlists ---------------------------------------------------------------

const MAX_DEPTH: usize = 32;
const MAX_ELEMENTS: u32 = 1 << 20;

struct Xdr<'a> {
    b: &'a [u8],
    i: usize,
}

impl Xdr<'_> {
    fn u32(&mut self) -> Result<u32, String> {
        if self.i + 4 > self.b.len() {
            return Err("nvlist truncated".into());
        }
        let v = be32(self.b, self.i);
        self.i += 4;
        Ok(v)
    }
    fn i32(&mut self) -> Result<i32, String> {
        self.u32().map(|v| v as i32)
    }
    fn u64(&mut self) -> Result<u64, String> {
        if self.i + 8 > self.b.len() {
            return Err("nvlist truncated".into());
        }
        let v = be64(self.b, self.i);
        self.i += 8;
        Ok(v)
    }
    fn opaque(&mut self, n: usize) -> Result<&[u8], String> {
        let padded = n.checked_add(3).ok_or("bad length")? & !3;
        if self.i + padded > self.b.len() {
            return Err("nvlist truncated".into());
        }
        let s = &self.b[self.i..self.i + n];
        self.i += padded;
        Ok(s)
    }
    fn string(&mut self) -> Result<String, String> {
        let n = self.u32()? as usize;
        Ok(String::from_utf8_lossy(self.opaque(n)?).into_owned())
    }
}

/// A packed nvlist: a 4-byte header (encoding: 0 native, as in
/// /etc/zfs/zpool.cache; 1 XDR, as in vdev labels; then the native
/// encoding's byte order, 1 = little-endian), then the list.
pub fn unpack(b: &[u8]) -> Result<Value, String> {
    if b.len() < 12 {
        return Err("too short".into());
    }
    match b[0] {
        0 => {
            let mut n = Native { b, i: 12, little: b[1] == 1 };
            n.list(0)
        }
        1 => {
            let mut x = Xdr { b, i: 4 };
            nvlist(&mut x, 0)
        }
        e => Err(format!("unknown nvlist encoding {e}")),
    }
}

/// The native encoding: libnvpair's in-memory nvpairs copied out.  Each
/// pair is size (i32), name length (i16, with its NUL), reserved (i16),
/// element count (i32), type (i32), the name, then the value 8-aligned
/// from the pair's start; a size of 0 ends a list.  Embedded nvlists follow
/// right after the pair that holds them, each ended the same way.
struct Native<'a> {
    b: &'a [u8],
    i: usize,
    little: bool,
}

impl Native<'_> {
    fn n<const N: usize>(&self, o: usize) -> Result<[u8; N], String> {
        let s = self.b.get(o..o.checked_add(N).ok_or("bad offset")?).ok_or("nvlist truncated")?;
        let mut a: [u8; N] = s.try_into().unwrap();
        if !self.little {
            a.reverse();
        }
        Ok(a)
    }
    fn u16(&self, o: usize) -> Result<u16, String> {
        self.n::<2>(o).map(u16::from_le_bytes)
    }
    fn u32(&self, o: usize) -> Result<u32, String> {
        self.n::<4>(o).map(u32::from_le_bytes)
    }
    fn u64(&self, o: usize) -> Result<u64, String> {
        self.n::<8>(o).map(u64::from_le_bytes)
    }
    fn cstr(&self, o: usize) -> Result<String, String> {
        let rest = self.b.get(o..).ok_or("nvlist truncated")?;
        let end = rest.iter().position(|&c| c == 0).ok_or("unterminated string")?;
        Ok(String::from_utf8_lossy(&rest[..end]).into_owned())
    }

    fn list(&mut self, depth: usize) -> Result<Value, String> {
        if depth > MAX_DEPTH {
            return Err("nvlist nested too deeply".into());
        }
        let mut o = Obj::new();
        loop {
            let start = self.i;
            let size = self.u32(start)? as usize;
            if size == 0 {
                self.i += 4;
                break;
            }
            let name_len = self.u16(start + 4)? as usize;
            let nelem = self.u32(start + 8)?;
            let typ = self.u32(start + 12)? as i32;
            if size < 16 + name_len || start + size > self.b.len() || nelem > MAX_ELEMENTS {
                return Err("bad nvpair size".into());
            }
            let name = self.cstr(start + 16)?;
            // The value is 8-aligned from the pair's start (pairs themselves
            // start at 12 in the file, so not from the file's).
            let v = start + ((16 + name_len + 7) & !7);
            let scalar = |w: usize, signed: bool| -> Result<Value, String> {
                let raw = match w {
                    1 => crate::bytes::u8_at(self.b, v) as u64,
                    2 => self.u16(v)? as u64,
                    4 => self.u32(v)? as u64,
                    _ => self.u64(v)?,
                };
                Ok(if signed {
                    let shift = 64 - 8 * w as u32;
                    Value::Int(((raw << shift) as i64) >> shift)
                } else {
                    Value::UInt(raw)
                })
            };
            let elem = |k: usize, w: usize, signed: bool| -> Result<Value, String> {
                let p = v + k * w;
                let raw = match w {
                    1 => crate::bytes::u8_at(self.b, p) as u64,
                    2 => self.u16(p)? as u64,
                    4 => self.u32(p)? as u64,
                    _ => self.u64(p)?,
                };
                Ok(if signed {
                    let shift = 64 - 8 * w as u32;
                    Value::Int(((raw << shift) as i64) >> shift)
                } else {
                    Value::UInt(raw)
                })
            };
            let value = match typ {
                1 => Value::Bool(true),
                2 | 23 => scalar(1, false)?,
                22 => scalar(1, true)?,
                3 => scalar(2, true)?,
                4 => scalar(2, false)?,
                5 => scalar(4, true)?,
                6 => scalar(4, false)?,
                21 => Value::Bool(self.u32(v)? != 0),
                7 => scalar(8, true)?,
                8 | 18 => scalar(8, false)?,
                9 => Value::Str(self.cstr(v)?),
                10 => Value::Str(crate::bytes::hex(self.b.get(v..v + nelem as usize).ok_or("nvlist truncated")?)),
                11..=16 | 24..=26 => {
                    let (w, signed) = match typ {
                        11 => (2, true),
                        12 => (2, false),
                        13 => (4, true),
                        14 => (4, false),
                        15 => (8, true),
                        16 => (8, false),
                        24 => (4, false),
                        25 => (1, true),
                        _ => (1, false),
                    };
                    let a = (0..nelem as usize).map(|k| elem(k, w, signed)).collect::<Result<Vec<_>, _>>()?;
                    if typ == 24 {
                        Value::Arr(a.into_iter().map(|x| Value::Bool(x != Value::UInt(0))).collect())
                    } else {
                        Value::Arr(a)
                    }
                }
                17 => {
                    // nelem pointers, then the strings one after another.
                    let mut p = v + 8 * nelem as usize;
                    let mut a = Vec::new();
                    for _ in 0..nelem {
                        let s = self.cstr(p)?;
                        p += s.len() + 1;
                        a.push(Value::Str(s));
                    }
                    Value::Arr(a)
                }
                19 | 20 => Value::Null, // filled in below
                _ => Value::Null,
            };
            self.i = start + size;
            let value = match typ {
                19 => self.list(depth + 1)?,
                20 => {
                    let mut a = Vec::new();
                    for _ in 0..nelem {
                        a.push(self.list(depth + 1)?);
                    }
                    Value::Arr(a)
                }
                _ => value,
            };
            o.put(&name, value);
        }
        Ok(o.build())
    }
}

fn nvlist(x: &mut Xdr, depth: usize) -> Result<Value, String> {
    if depth > MAX_DEPTH {
        return Err("nvlist nested too deeply".into());
    }
    let _version = x.i32()?;
    let _flags = x.u32()?;
    let mut o = Obj::new();
    loop {
        let start = x.i;
        let encoded = x.u32()? as usize;
        let decoded = x.u32()?;
        if encoded == 0 && decoded == 0 {
            break;
        }
        if encoded < 8 || start + encoded > x.b.len() {
            return Err("bad nvpair size".into());
        }
        let name = x.string()?;
        let typ = x.i32()?;
        let nelem = x.u32()?;
        if nelem > MAX_ELEMENTS {
            return Err("nvpair has too many elements".into());
        }
        let v = match typ {
            1 => Value::Bool(true), // DATA_TYPE_BOOLEAN: present means true
            2 | 3 | 4 | 5 | 22 | 23 => Value::Int(x.i32()? as i64), // byte, int16, uint16, int32, int8, uint8
            6 => Value::UInt(x.u32()? as u64),
            7 => Value::Int(x.u64()? as i64),
            8 | 18 => Value::UInt(x.u64()?), // uint64, hrtime
            21 => Value::Bool(x.u32()? != 0),
            9 => Value::Str(x.string()?),
            10 => Value::Str(crate::bytes::hex(x.opaque(nelem as usize)?)),
            11..=16 | 24..=26 => {
                let count = x.u32()?;
                if count != nelem {
                    return Err("array count mismatch".into());
                }
                let wide = matches!(typ, 15 | 16);
                let mut a = Vec::with_capacity(nelem.min(4096) as usize);
                for _ in 0..nelem {
                    a.push(if wide {
                        if typ == 15 { Value::Int(x.u64()? as i64) } else { Value::UInt(x.u64()?) }
                    } else if typ == 24 {
                        Value::Bool(x.u32()? != 0)
                    } else if typ == 14 {
                        Value::UInt(x.u32()? as u64)
                    } else {
                        Value::Int(x.i32()? as i64)
                    });
                }
                Value::Arr(a)
            }
            17 => {
                let mut a = Vec::new();
                for _ in 0..nelem {
                    a.push(Value::Str(x.string()?));
                }
                Value::Arr(a)
            }
            19 => nvlist(x, depth + 1)?,
            20 => {
                let mut a = Vec::new();
                for _ in 0..nelem {
                    a.push(nvlist(x, depth + 1)?);
                }
                Value::Arr(a)
            }
            _ => {
                // Unknown type (double, ...): skip the pair by its size.
                x.i = start + encoded;
                Value::Null
            }
        };
        // A pair's size covers its embedded lists too.
        if x.i != start + encoded {
            return Err(format!("nvpair {name} size mismatch"));
        }
        o.put(&name, v);
    }
    Ok(o.build())
}

/// Encodes JSON-ish values as an XDR nvlist, for tests: strings, u64s,
/// nested objects, arrays of objects, and `true` as DATA_TYPE_BOOLEAN.
#[cfg(test)]
pub mod pack {
    use crate::json::Value;

    fn s(out: &mut Vec<u8>, t: &str) {
        out.extend((t.len() as u32).to_be_bytes());
        out.extend(t.as_bytes());
        while out.len() % 4 != 0 {
            out.push(0);
        }
    }

    pub fn nvlist(v: &Value, out: &mut Vec<u8>) {
        out.extend(0i32.to_be_bytes());
        out.extend(1u32.to_be_bytes());
        for (k, val) in v.as_object().unwrap() {
            let mut p = Vec::new();
            s(&mut p, k);
            match val {
                Value::Str(t) => {
                    p.extend(9i32.to_be_bytes());
                    p.extend(1u32.to_be_bytes());
                    s(&mut p, t);
                }
                Value::UInt(n) => {
                    p.extend(8i32.to_be_bytes());
                    p.extend(1u32.to_be_bytes());
                    p.extend(n.to_be_bytes());
                }
                Value::Bool(true) => {
                    p.extend(1i32.to_be_bytes());
                    p.extend(0u32.to_be_bytes());
                }
                Value::Obj(_) => {
                    p.extend(19i32.to_be_bytes());
                    p.extend(1u32.to_be_bytes());
                    nvlist(val, &mut p);
                }
                Value::Arr(a) => {
                    p.extend(20i32.to_be_bytes());
                    p.extend((a.len() as u32).to_be_bytes());
                    for e in a {
                        nvlist(e, &mut p);
                    }
                }
                _ => panic!("can't pack {val:?}"),
            }
            let size = p.len() as u32 + 8;
            out.extend(size.to_be_bytes());
            out.extend(size.to_be_bytes());
            out.extend(p);
        }
        out.extend([0u8; 8]);
    }

    pub fn packed(v: &Value) -> Vec<u8> {
        let mut out = vec![1u8, 1, 0, 0];
        nvlist(v, &mut out);
        out
    }

    /// A vdev of `size` bytes whose four labels hold `cfg`.
    pub fn vdev(size: usize, cfg: &Value) -> Vec<u8> {
        let mut img = vec![0u8; size];
        let p = packed(cfg);
        for off in super::label_offsets(size as u64) {
            let o = (off + super::NVLIST_OFFSET) as usize;
            img[o..o + p.len()].copy_from_slice(&p);
        }
        img
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::bytes::MemSource;
    use crate::json::parse;

    fn cfg() -> Value {
        parse(
            r#"{"version": 5000, "name": "rpool", "state": 0, "txg": 1234, "pool_guid": 1311768467294899695,
               "hostname": "debian", "top_guid": 42, "guid": 43, "vdev_children": 1,
               "vdev_tree": {"type": "mirror", "id": 0, "guid": 42, "ashift": 12, "asize": 1000,
                  "children": [{"type": "disk", "id": 0, "guid": 43, "path": "/dev/vda3", "whole_disk": 0},
                               {"type": "disk", "id": 1, "guid": 44, "path": "/dev/vdb3", "whole_disk": 0}]},
               "features_for_read": {"com.delphix:hole_birth": true, "com.delphix:embedded_data": true}}"#,
        )
        .unwrap()
    }

    #[test]
    fn label_offsets_math() {
        let size = 64u64 << 20;
        assert_eq!(label_offsets(size), vec![0, 256 << 10, size - (512 << 10), size - (256 << 10)]);
        // An unaligned size is rounded down to a label boundary first.
        assert_eq!(label_offsets(size + 1000)[3], size - (256 << 10));
        assert_eq!(label_offsets(512 << 10), vec![0]);
    }

    #[test]
    fn member_from_labels() {
        let c = probe(&MemSource(pack::vdev(4 << 20, &cfg()))).unwrap();
        assert_eq!(c.kind, "zfs_member");
        assert_eq!(c.label.as_deref(), Some("rpool"));
        assert_eq!(c.uuid.as_deref(), Some("1311768467294899695"));
        assert_eq!(c.version.as_deref(), Some("5000"));
        let d = c.details.build();
        assert_eq!(d.get("labels_valid").and_then(Value::as_u64), Some(4));
        assert_eq!(d.get("pool_state").and_then(Value::as_str), Some("active"));
        assert_eq!(d.get("vdev_guid").and_then(Value::as_str), Some("43"));
        let e = c.extra.unwrap();
        assert_eq!(e.at(&["vdev_tree", "children", "1", "path"]).and_then(Value::as_str), Some("/dev/vdb3"));
        assert_eq!(e.at(&["features_for_read", "com.delphix:hole_birth"]), Some(&Value::Bool(true)));
    }

    #[test]
    fn end_labels_alone_are_not_a_vdev() {
        // The disk holding a vdev partition at its end sees labels 2 and 3.
        let vdev = pack::vdev(4 << 20, &cfg());
        let mut disk = vec![0u8; 8 << 20];
        disk[4 << 20..].copy_from_slice(&vdev);
        assert!(probe(&MemSource(disk)).is_none());
        let mut img = pack::vdev(4 << 20, &cfg());
        img[(256 << 10) + (NVLIST_OFFSET as usize)] = 0xff; // L1 gone, L0 still there
        assert_eq!(probe(&MemSource(img)).unwrap().details.build().get("labels_valid").and_then(Value::as_u64), Some(3));
    }

    #[test]
    fn damaged_labels_still_found() {
        let mut img = pack::vdev(4 << 20, &cfg());
        img[(NVLIST_OFFSET as usize)..(NVLIST_OFFSET as usize) + 16].fill(0xff);
        let c = probe(&MemSource(img)).unwrap();
        assert_eq!(c.details.build().get("labels_valid").and_then(Value::as_u64), Some(3));
        assert!(probe(&MemSource(vec![0u8; 4 << 20])).is_none());
    }

    #[test]
    fn unpack_all_types() {
        // Hand-encode one pair of each scalar/array type.
        let mut out = vec![1u8, 1, 0, 0];
        out.extend(0i32.to_be_bytes());
        out.extend(1u32.to_be_bytes());
        let mut pair = |name: &str, typ: i32, nelem: u32, data: &[u8]| {
            let mut p = Vec::new();
            p.extend((name.len() as u32).to_be_bytes());
            p.extend(name.as_bytes());
            while p.len() % 4 != 0 {
                p.push(0);
            }
            p.extend(typ.to_be_bytes());
            p.extend(nelem.to_be_bytes());
            p.extend(data);
            let size = p.len() as u32 + 8;
            out.extend(size.to_be_bytes());
            out.extend(size.to_be_bytes());
            out.extend(p);
        };
        pair("b", 2, 1, &7i32.to_be_bytes());
        pair("i32", 5, 1, &(-5i32).to_be_bytes());
        pair("u32", 6, 1, &u32::MAX.to_be_bytes());
        pair("i64", 7, 1, &(-9i64).to_be_bytes());
        pair("hr", 18, 1, &77u64.to_be_bytes());
        pair("bv", 21, 1, &1u32.to_be_bytes());
        pair("ba", 10, 3, &[1, 2, 3, 0]);
        let mut ua = 2u32.to_be_bytes().to_vec();
        ua.extend(1u64.to_be_bytes());
        ua.extend(2u64.to_be_bytes());
        pair("u64a", 16, 2, &ua);
        let mut ia = 2u32.to_be_bytes().to_vec();
        ia.extend((-1i32).to_be_bytes());
        ia.extend(3i32.to_be_bytes());
        pair("i32a", 13, 2, &ia);
        let mut sa = Vec::new();
        for t in ["x", "yz"] {
            sa.extend((t.len() as u32).to_be_bytes());
            sa.extend(t.as_bytes());
            sa.extend(vec![0u8; (4 - t.len() % 4) % 4]);
        }
        pair("sa", 17, 2, &sa);
        pair("dbl", 27, 1, &1.5f64.to_be_bytes());
        pair("flag", 1, 0, &[]);
        out.extend([0u8; 8]);
        let v = unpack(&out).unwrap();
        assert_eq!(v.get("b"), Some(&Value::Int(7)));
        assert_eq!(v.get("i32"), Some(&Value::Int(-5)));
        assert_eq!(v.get("u32"), Some(&Value::UInt(u32::MAX as u64)));
        assert_eq!(v.get("i64"), Some(&Value::Int(-9)));
        assert_eq!(v.get("hr"), Some(&Value::UInt(77)));
        assert_eq!(v.get("bv"), Some(&Value::Bool(true)));
        assert_eq!(v.get("ba").and_then(Value::as_str), Some("010203"));
        assert_eq!(v.get("u64a"), Some(&Value::from(vec![1u64, 2])));
        assert_eq!(v.get("i32a"), Some(&Value::Arr(vec![Value::Int(-1), Value::Int(3)])));
        assert_eq!(v.get("sa"), Some(&Value::from(vec!["x", "yz"])));
        assert_eq!(v.get("dbl"), Some(&Value::Null));
        assert_eq!(v.get("flag"), Some(&Value::Bool(true)));
    }

    /// The debian13-zfs test guest's real files (OpenZFS 2.3.9).
    const REAL_CACHE: &[u8] = include_bytes!("../../tests/fixtures/debian13-zfs.zpool.cache");
    const REAL_LABEL: &[u8] = include_bytes!("../../tests/fixtures/debian13-zfs.label0.nvlist");

    #[test]
    fn real_zpool_cache_native_encoding() {
        let v = unpack(REAL_CACHE).unwrap();
        let p = v.get("rpool").unwrap();
        assert_eq!(p.get("version").and_then(Value::as_u64), Some(5000));
        assert_eq!(p.get("pool_guid").and_then(Value::as_u64), Some(15105800815207446011));
        assert_eq!(p.get("hostname").and_then(Value::as_str), Some("(none)"));
        assert_eq!(p.get("com.delphix:has_per_vdev_zaps"), Some(&Value::Bool(true)));
        assert_eq!(p.at(&["vdev_tree", "type"]).and_then(Value::as_str), Some("root"));
        assert_eq!(p.at(&["vdev_tree", "children", "0", "path"]).and_then(Value::as_str), Some("/dev/vda3"));
        assert_eq!(p.at(&["vdev_tree", "children", "0", "ashift"]).and_then(Value::as_u64), Some(12));
        assert_eq!(p.at(&["vdev_tree", "children", "0", "asize"]).and_then(Value::as_u64), Some(13952876544));
        let f: Vec<&str> = p.get("features_for_read").unwrap().as_object().unwrap().iter().map(|(k, _)| k.as_str()).collect();
        assert_eq!(f, vec!["com.delphix:hole_birth", "com.delphix:embedded_data", "com.klarasystems:vdev_zaps_v2"]);
        // Every truncation fails cleanly.
        for cut in (12..REAL_CACHE.len()).step_by(7) {
            let _ = unpack(&REAL_CACHE[..cut]);
        }
    }

    #[test]
    fn real_label_xdr_encoding() {
        let v = unpack(REAL_LABEL).unwrap();
        assert_eq!(v.get("name").and_then(Value::as_str), Some("rpool"));
        assert_eq!(v.get("pool_guid").and_then(Value::as_u64), Some(15105800815207446011));
        assert_eq!(v.get("guid").and_then(Value::as_u64), Some(12321095712930874282));
        assert_eq!(v.at(&["vdev_tree", "type"]).and_then(Value::as_str), Some("disk"));
        assert_eq!(v.at(&["vdev_tree", "path"]).and_then(Value::as_str), Some("/dev/vda3"));
        assert_eq!(v.at(&["features_for_read", "com.delphix:hole_birth"]), Some(&Value::Bool(true)));
        for cut in (12..REAL_LABEL.len()).step_by(5) {
            let _ = unpack(&REAL_LABEL[..cut]);
        }
    }

    #[test]
    fn native_big_endian_and_scalar_types() {
        // Build a native list by hand, both byte orders.
        for little in [true, false] {
            let w32 = |v: u32| if little { v.to_le_bytes() } else { v.to_be_bytes() };
            let w16 = |v: u16| if little { v.to_le_bytes() } else { v.to_be_bytes() };
            let mut b = vec![0u8, little as u8, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0];
            let mut pair = |name: &str, typ: u32, nelem: u32, val: &[u8]| {
                let name_len = name.len() + 1;
                let voff = (16 + name_len + 7) & !7;
                let size = (voff + val.len() + 7) & !7;
                let mut p = vec![0u8; size];
                p[0..4].copy_from_slice(&w32(size as u32));
                p[4..6].copy_from_slice(&w16(name_len as u16));
                p[8..12].copy_from_slice(&w32(nelem));
                p[12..16].copy_from_slice(&w32(typ));
                p[16..16 + name.len()].copy_from_slice(name.as_bytes());
                p[voff..voff + val.len()].copy_from_slice(val);
                b.extend(p);
            };
            pair("i8", 22, 1, &[0xfe]);
            pair("i16", 3, 1, &w16(0xfffd));
            pair("u32", 6, 1, &w32(7));
            pair("i32a", 13, 2, &[w32(0xffff_ffff), w32(2)].concat());
            let mut sa = vec![0u8; 16];
            sa.extend(b"ab\0c\0");
            pair("sa", 17, 2, &sa);
            pair("bv", 21, 1, &w32(1));
            pair("ba", 10, 2, &[9, 10]);
            b.extend([0u8; 4]);
            let v = unpack(&b).unwrap();
            assert_eq!(v.get("i8"), Some(&Value::Int(-2)), "little={little}");
            assert_eq!(v.get("i16"), Some(&Value::Int(-3)));
            assert_eq!(v.get("u32"), Some(&Value::UInt(7)));
            assert_eq!(v.get("i32a"), Some(&Value::Arr(vec![Value::Int(-1), Value::Int(2)])));
            assert_eq!(v.get("sa"), Some(&Value::from(vec!["ab", "c"])));
            assert_eq!(v.get("bv"), Some(&Value::Bool(true)));
            assert_eq!(v.get("ba").and_then(Value::as_str), Some("090a"));
        }
    }

    #[test]
    fn unpack_rejects_garbage() {
        assert!(unpack(&[]).is_err());
        assert!(unpack(&[7u8; 64]).unwrap_err().contains("unknown nvlist encoding"));
        assert!(unpack(&[0u8; 64]).is_ok()); // native, empty list
        let mut bad = vec![0u8, 1, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0];
        bad.extend(1000u32.to_le_bytes());
        bad.extend([0u8; 12]);
        assert!(unpack(&bad).is_err());
        let good = pack::packed(&cfg());
        for cut in [10, 40, good.len() / 2, good.len() - 4] {
            assert!(unpack(&good[..cut]).is_err(), "cut at {cut}");
        }
        // A pair claiming more bytes than exist.
        let mut bad = good.clone();
        bad[12..16].copy_from_slice(&0x7fff_fff0u32.to_be_bytes());
        assert!(unpack(&bad).is_err());
        // Deep nesting.
        let mut v = parse(r#"{"x": 1}"#).unwrap();
        for _ in 0..40 {
            v = Value::Obj(vec![("n".into(), v)]);
        }
        assert!(unpack(&pack::packed(&v)).unwrap_err().contains("deeply"));
    }
}
