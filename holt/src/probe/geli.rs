//! FreeBSD's GELI (geom_eli) metadata, in the provider's last 512-byte
//! sector (struct g_eli_metadata, sys/geom/eli/g_eli.h): what `geli init`
//! needs to make the same provider again.  Only the fields before the salt
//! are read (offsets 0..47); the salt, the encrypted master keys and the
//! MD5 hash after them never leave this function.

use super::Content;
use crate::bytes::{le16, le32, le64, ByteSource};
use crate::json::{Obj, Value};

pub const MAGIC: &[u8] = b"GEOM::ELI";
/// G_ELI_VERSION in FreeBSD 15: the newest metadata version there is.
pub const VERSION_MAX: u32 = 7;
/// The bytes of the sector that are decoded: the salt starts here.
const KEPT: usize = 47;

/// md_flags' bits that are kept on disk (g_eli.h's "on-disk" flags), by
/// the names `geli list` prints them under.
pub const FLAGS: [(u32, &str); 10] = [
    (0x001, "ONETIME"),
    (0x002, "BOOT"),
    (0x004, "W-DETACH"),
    (0x008, "RW-DETACH"),
    (0x010, "AUTH"),
    (0x020, "READ-ONLY"),
    (0x040, "NODELETE"),
    (0x080, "GELIBOOT"),
    (0x100, "GELIDISPLAYPASS"),
    (0x200, "AUTORESIZE"),
];
pub const FLAG_BOOT: u32 = 0x002;
pub const FLAG_AUTH: u32 = 0x010;
pub const FLAG_NODELETE: u32 = 0x040;
pub const FLAG_GELIBOOT: u32 = 0x080;
pub const FLAG_GELIDISPLAYPASS: u32 = 0x100;
pub const FLAG_AUTORESIZE: u32 = 0x200;

/// sys/opencrypto/cryptodev.h's numbers for the algorithms GELI has used,
/// with geli's names for them (g_eli_algo2str).  3DES, Blowfish and
/// HMAC/MD5 are older releases'; FreeBSD 15's geli makes none of them.
pub const EALGOS: [(u16, &str); 6] = [(2, "3DES-CBC"), (3, "Blowfish-CBC"), (11, "AES-CBC"), (16, "NULL"), (21, "CAMELLIA-CBC"), (22, "AES-XTS")];
pub const AALGOS: [(u16, &str); 6] = [(6, "HMAC/MD5"), (7, "HMAC/SHA1"), (8, "HMAC/RIPEMD160"), (18, "HMAC/SHA256"), (19, "HMAC/SHA384"), (20, "HMAC/SHA512")];
/// What FreeBSD 15's kernel takes (eli_metadata_crypto_supported), so what
/// the rescue system's geli can make.
pub const EALGOS_MADE: [u16; 4] = [11, 16, 21, 22];
pub const AALGOS_MADE: [u16; 5] = [7, 8, 18, 19, 20];

pub fn ealgo_name(n: u16) -> Option<&'static str> {
    EALGOS.iter().find(|(id, _)| *id == n).map(|(_, s)| *s)
}

pub fn aalgo_name(n: u16) -> Option<&'static str> {
    AALGOS.iter().find(|(id, _)| *id == n).map(|(_, s)| *s)
}

/// The decoded fields: every one `geli init` takes, and the key slots.
#[derive(Debug, Clone, PartialEq)]
pub struct Meta {
    pub version: u32,
    pub flags: u32,
    pub ealgo: u16,
    pub keylen: u16,
    /// Version 0 has no aalgo.
    pub aalgo: Option<u16>,
    pub provsize: u64,
    pub sectorsize: u32,
    pub keys: u8,
    /// -1: no passphrase (geli init -P).
    pub iterations: i32,
}

/// The metadata in `src`'s last sector, if it holds GELI's.
pub fn read(src: &dyn ByteSource) -> Option<Meta> {
    let size = src.size();
    if size < 512 {
        return None;
    }
    let sector = src.read_exact_at(size - 512, 512)?;
    // Only the fields before the salt are looked at, or kept.
    decode(&sector[..KEPT])
}

/// The fields of a metadata sector's first 47 bytes (version 0's layout has
/// no aalgo, so its later fields sit two bytes earlier).
pub fn decode(b: &[u8]) -> Option<Meta> {
    if b.len() < KEPT || &b[..MAGIC.len()] != MAGIC || b[MAGIC.len()..16].iter().any(|&c| c != 0) {
        return None;
    }
    let version = le32(b, 16);
    if version > VERSION_MAX {
        // A newer version's layout isn't known; still GELI's.
        return Some(Meta { version, flags: le32(b, 20), ealgo: 0, keylen: 0, aalgo: None, provsize: 0, sectorsize: 0, keys: 0, iterations: 0 });
    }
    let (aalgo, p) = if version == 0 { (None, 28) } else { (Some(le16(b, 28)), 30) };
    Some(Meta {
        version,
        flags: le32(b, 20),
        ealgo: le16(b, 24),
        keylen: le16(b, 26),
        aalgo,
        provsize: le64(b, p),
        sectorsize: le32(b, p + 8),
        keys: b[p + 12],
        iterations: le32(b, p + 13) as i32,
    })
}

impl Meta {
    pub fn flag_names(&self) -> Vec<String> {
        let mut out: Vec<String> = FLAGS.iter().filter(|(bit, _)| self.flags & bit != 0).map(|(_, n)| n.to_string()).collect();
        let known: u32 = FLAGS.iter().map(|(b, _)| b).sum();
        if self.flags & !known != 0 {
            out.push(format!("0x{:x}", self.flags & !known));
        }
        out
    }

    pub fn auth(&self) -> bool {
        self.flags & FLAG_AUTH != 0
    }

    /// The key slots in use (md_keys' bits).
    pub fn slots(&self) -> Vec<u64> {
        (0..2).filter(|i| self.keys & (1 << i) != 0).collect()
    }

    /// The fields as the report has them: each algorithm by geli's name
    /// (null where it isn't one) and its number.
    pub fn details(&self) -> Obj {
        let aalgo = self.aalgo.filter(|_| self.auth());
        Obj::new()
            .set("flags", self.flag_names())
            .set("flags_raw", format!("0x{:x}", self.flags))
            .set("ealgo", ealgo_name(self.ealgo))
            .set("ealgo_id", self.ealgo)
            .set("keylen", self.keylen)
            .set("aalgo", aalgo.and_then(aalgo_name))
            .set("aalgo_id", self.aalgo)
            .set("provsize", self.provsize)
            .set("sectorsize", self.sectorsize)
            .set("keys", self.slots())
            .set("iterations", self.iterations)
    }
}

pub fn probe(src: &dyn ByteSource) -> Option<Content> {
    let m = read(src)?;
    Some(Content { kind: "geli".into(), usage: "crypto", uuid: None, label: None, version: Some(m.version.to_string()), details: m.details(), extra: None })
}

/// A report's geli fields read back (the partition's content, or its
/// `geli` record): what load needs of them.
pub fn from_json(v: &Value) -> Option<Meta> {
    let n = |k: &str| v.get(k).and_then(Value::as_u64);
    let version: u32 = match v.get("version")? {
        Value::Str(s) => s.parse().ok()?,
        x => x.as_u64()? as u32,
    };
    let flags = match v.get("flags_raw").and_then(Value::as_str) {
        Some(h) => u32::from_str_radix(h.trim_start_matches("0x"), 16).ok()?,
        None => v.get("flags").and_then(Value::as_array)?.iter().filter_map(Value::as_str).filter_map(|f| FLAGS.iter().find(|(_, n)| *n == f).map(|(b, _)| *b)).sum(),
    };
    let ealgo = n("ealgo_id").map(|x| x as u16).or_else(|| v.get("ealgo").and_then(Value::as_str).and_then(|s| EALGOS.iter().find(|(_, n)| n.eq_ignore_ascii_case(s)).map(|(i, _)| *i)))?;
    let aalgo = n("aalgo_id").map(|x| x as u16).or_else(|| v.get("aalgo").and_then(Value::as_str).and_then(|s| AALGOS.iter().find(|(_, n)| n.eq_ignore_ascii_case(s)).map(|(i, _)| *i)));
    let keys = v.get("keys").and_then(Value::as_array).map(|a| a.iter().filter_map(Value::as_u64).filter(|&i| i < 2).fold(0u8, |m, i| m | (1 << i))).unwrap_or(1);
    Some(Meta {
        version,
        flags,
        ealgo,
        keylen: n("keylen")? as u16,
        aalgo,
        provsize: n("provsize").unwrap_or(0),
        sectorsize: n("sectorsize")? as u32,
        keys,
        iterations: v.get("iterations").and_then(Value::as_i64).unwrap_or(-1) as i32,
    })
}

#[cfg(test)]
pub mod sample {
    /// A metadata sector as `geli init` writes it: the given fields, then a
    /// salt, two master-key slots and a hash of the bytes in `secret`.
    #[allow(clippy::too_many_arguments)]
    pub fn sector(version: u32, flags: u32, ealgo: u16, keylen: u16, aalgo: u16, provsize: u64, sectorsize: u32, keys: u8, iterations: i32, secret: u8) -> Vec<u8> {
        let mut s = vec![secret; 512];
        s[..16].fill(0);
        s[..9].copy_from_slice(super::MAGIC);
        s[16..20].copy_from_slice(&version.to_le_bytes());
        s[20..24].copy_from_slice(&flags.to_le_bytes());
        s[24..26].copy_from_slice(&ealgo.to_le_bytes());
        s[26..28].copy_from_slice(&keylen.to_le_bytes());
        let p = if version == 0 { 28 } else {
            s[28..30].copy_from_slice(&aalgo.to_le_bytes());
            30
        };
        s[p..p + 8].copy_from_slice(&provsize.to_le_bytes());
        s[p + 8..p + 12].copy_from_slice(&sectorsize.to_le_bytes());
        s[p + 12] = keys;
        s[p + 13..p + 17].copy_from_slice(&iterations.to_le_bytes());
        s
    }

    /// The freebsd15-geli guest's provider (vtbd0p3), its metadata at the
    /// end of a `size`-byte image.
    pub fn guest(size: usize) -> Vec<u8> {
        let mut img = vec![0u8; size];
        img[size - 512..].copy_from_slice(&sector(7, 0x282, 22, 256, 0, 14757658624, 4096, 1, 3031038, 0xa5));
        img
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::bytes::MemSource;

    #[test]
    fn the_guests_metadata() {
        let c = probe(&MemSource(sample::guest(1 << 20))).unwrap();
        assert_eq!((c.kind.as_str(), c.usage, c.version.as_deref()), ("geli", "crypto", Some("7")));
        let d = c.details.build();
        assert_eq!(
            d.compact(),
            r#"{"flags":["BOOT","GELIBOOT","AUTORESIZE"],"flags_raw":"0x282","ealgo":"AES-XTS","ealgo_id":22,"keylen":256,"aalgo":null,"aalgo_id":0,"provsize":14757658624,"sectorsize":4096,"keys":[0],"iterations":3031038}"#
        );
        // Read back, as load reads it.
        let mut v = d.clone();
        if let Value::Obj(o) = &mut v {
            o.push(("version".into(), Value::from("7")));
        }
        let m = from_json(&v).unwrap();
        assert_eq!(m, Meta { version: 7, flags: 0x282, ealgo: 22, keylen: 256, aalgo: Some(0), provsize: 14757658624, sectorsize: 4096, keys: 1, iterations: 3031038 });
    }

    #[test]
    fn every_flag_and_both_layouts() {
        let all: u32 = FLAGS.iter().map(|(b, _)| b).sum();
        let m = decode(&sample::sector(7, all | 0x4000, 22, 128, 18, 1 << 30, 512, 3, -1, 0)[..47]).unwrap();
        let names: Vec<&str> = FLAGS.iter().map(|(_, n)| *n).collect();
        let mut want: Vec<String> = names.iter().map(|s| s.to_string()).collect();
        want.push("0x4000".into());
        assert_eq!(m.flag_names(), want);
        assert_eq!(m.slots(), vec![0, 1]);
        assert_eq!(m.iterations, -1);
        assert_eq!(m.details().build().get("aalgo").and_then(Value::as_str), Some("HMAC/SHA256"));
        // Version 0: no aalgo, the rest two bytes earlier.
        let m = decode(&sample::sector(0, 0x2, 11, 128, 0, 123456789, 512, 1, 5000, 0)[..47]).unwrap();
        assert_eq!((m.aalgo, m.provsize, m.sectorsize, m.keys, m.iterations), (None, 123456789, 512, 1, 5000));
        assert_eq!(ealgo_name(m.ealgo), Some("AES-CBC"));
        // A version this decoder doesn't know: GELI's, its fields left out.
        let m = decode(&sample::sector(8, 0x2, 22, 256, 0, 1, 4096, 1, 1, 0)[..47]).unwrap();
        assert_eq!((m.version, m.ealgo, m.sectorsize), (8, 0, 0));
    }

    #[test]
    fn not_geli() {
        let mut img = sample::guest(8192);
        img[8192 - 512] = b'g';
        assert!(probe(&MemSource(img)).is_none());
        // GEOM::ELIX: the magic padded with something other than NULs.
        let mut img = sample::guest(8192);
        img[8192 - 512 + 9] = b'X';
        assert!(probe(&MemSource(img)).is_none());
        // A glabel's sector, a blank one, a provider too small to hold one.
        let mut img = vec![0u8; 8192];
        img[8192 - 512..8192 - 500].copy_from_slice(b"GEOM::LABEL\0");
        assert!(probe(&MemSource(img)).is_none());
        assert!(probe(&MemSource(vec![0u8; 8192])).is_none());
        assert!(probe(&MemSource(sample::guest(512)[..511].to_vec())).is_none());
        // The metadata 512 bytes from a truncated provider's end isn't there.
        let img = sample::guest(8192);
        assert!(probe(&MemSource(img[..8192 - 100].to_vec())).is_none());
    }

    #[test]
    fn the_salt_and_master_keys_never_reach_the_json() {
        // Recognisable bytes everywhere from the salt on.
        let mut img = sample::guest(4096);
        let n = img.len();
        for (i, b) in img[n - 512 + 47..].iter_mut().enumerate() {
            *b = b"SECRETKEYBYTES"[i % 14];
        }
        let c = probe(&MemSource(img)).unwrap();
        let text = crate::probe::content_json(&[c]).compact();
        for needle in ["SECRET", "KEYBYTES", "534543524554", "U0VDUkVU"] {
            assert!(!text.contains(needle), "{needle} in {text}");
        }
        // Nothing past offset 47 changes the result.
        let a = probe(&MemSource(sample::guest(4096))).unwrap().details.build();
        let mut img = sample::guest(4096);
        img[4096 - 512 + 47..].fill(0x5a);
        assert_eq!(probe(&MemSource(img)).unwrap().details.build(), a);
    }
}
