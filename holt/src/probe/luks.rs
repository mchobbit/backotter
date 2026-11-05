//! LUKS1 and LUKS2 headers.  Records what cryptsetup would need to make the
//! same container again (cipher, key size, sector size, KDF, data offset,
//! label, UUID); never salts, digests or key material.

use super::Content;
use crate::bytes::{be16, be32, be64, sub, ByteSource};
use crate::json::{self, Obj, Value};

const MAGIC: &[u8] = b"LUKS\xba\xbe";
const MAGIC2: &[u8] = b"SKUL\xba\xbe"; // LUKS2 secondary header

pub fn probe(src: &dyn ByteSource) -> Option<Content> {
    let b = src.read_exact_at(0, 4096)?;
    if sub(&b, 0, 6) != MAGIC {
        return None;
    }
    match be16(&b, 6) {
        1 => Some(luks1(&b)),
        2 => Some(luks2(src, &b)),
        _ => None,
    }
}

fn luks1(b: &[u8]) -> Content {
    let mut active = 0;
    for i in 0..8 {
        if be32(b, 208 + 48 * i) == 0x00ac_71f3 {
            active += 1;
        }
    }
    let payload_sectors = be32(b, 104) as u64;
    let d = Obj::new()
        .set("cipher", format!("{}-{}", crate::bytes::cstr(sub(b, 8, 32)), crate::bytes::cstr(sub(b, 40, 32))))
        .set("hash", crate::bytes::cstr(sub(b, 72, 32)))
        .set("key_bits", be32(b, 108) as u64 * 8)
        .set("data_offset_bytes", payload_sectors * 512)
        .set("keyslots_active", active)
        .set("mk_iterations", be32(b, 164));
    Content {
        kind: "crypto_LUKS".into(),
        usage: "crypto",
        uuid: Some(crate::bytes::cstr(sub(b, 168, 40))),
        label: None,
        version: Some("1".into()),
        details: d,
        extra: None,
    }
}

fn luks2(src: &dyn ByteSource, b: &[u8]) -> Content {
    let hdr_size = be64(b, 8);
    let mut d = Obj::new().set("header_size", hdr_size).set("sequence_id", be64(b, 16));
    d.put_some("subsystem", crate::bytes::label(sub(b, 208, 48)));
    d.put("checksum_algorithm", crate::bytes::cstr(sub(b, 72, 32)));
    // The JSON area follows the 4 KiB binary header, up to hdr_size.
    if (16384..=4 << 20).contains(&hdr_size) {
        let area = src.read_at(4096, (hdr_size - 4096) as usize);
        let text = crate::bytes::cstr(&area);
        match json::parse(&text) {
            Ok(v) => summarize(&v, &mut d),
            Err(e) => d.put("json_error", format!("{} at {}", e.message, e.offset)),
        }
    } else {
        d.put("json_error", format!("implausible header size {hdr_size}"));
    }
    let secondary = src.read_at(hdr_size.min(4 << 20), 6);
    d.put("secondary_header", secondary == MAGIC2);
    Content {
        kind: "crypto_LUKS".into(),
        usage: "crypto",
        uuid: Some(crate::bytes::cstr(sub(b, 168, 40))),
        label: crate::bytes::label(sub(b, 24, 48)),
        version: Some("2".into()),
        details: d,
        extra: None,
    }
}

fn s(v: &Value, k: &str) -> Option<String> {
    v.get(k).and_then(|x| x.as_str().map(str::to_string).or_else(|| x.as_u64().map(|n| n.to_string())))
}

/// Picks the layout-relevant parts of the LUKS2 JSON metadata.
fn summarize(v: &Value, d: &mut Obj) {
    let mut segments = Vec::new();
    if let Some(Value::Obj(segs)) = v.get("segments") {
        for (id, seg) in segs {
            segments.push(
                Obj::new()
                    .set("id", id.as_str())
                    .set("type", s(seg, "type"))
                    .set("offset", s(seg, "offset"))
                    .set("size", s(seg, "size"))
                    .set("encryption", s(seg, "encryption"))
                    .set("sector_size", seg.get("sector_size").and_then(Value::as_u64))
                    .set("integrity", seg.get("integrity").and_then(|i| s(i, "type")))
                    .set("flags", seg.get("flags").cloned().unwrap_or(Value::Arr(vec![])))
                    .build(),
            );
        }
    }
    let mut keyslots = Vec::new();
    if let Some(Value::Obj(ks)) = v.get("keyslots") {
        for (id, k) in ks {
            let kdf = k.get("kdf");
            keyslots.push(
                Obj::new()
                    .set("id", id.as_str())
                    .set("type", s(k, "type"))
                    .set("key_bits", k.get("key_size").and_then(Value::as_u64).map(|n| n * 8))
                    .set("priority", k.get("priority").and_then(Value::as_u64))
                    .set("cipher", k.get("area").and_then(|a| s(a, "encryption")))
                    .set("kdf", kdf.and_then(|x| s(x, "type")))
                    .set("kdf_hash", kdf.and_then(|x| s(x, "hash")))
                    .set("kdf_time", kdf.and_then(|x| x.get("time")).and_then(Value::as_u64))
                    .set("kdf_memory", kdf.and_then(|x| x.get("memory")).and_then(Value::as_u64))
                    .set("kdf_cpus", kdf.and_then(|x| x.get("cpus")).and_then(Value::as_u64))
                    .set("kdf_iterations", kdf.and_then(|x| x.get("iterations")).and_then(Value::as_u64))
                    .build(),
            );
        }
    }
    let mut tokens = Vec::new();
    if let Some(Value::Obj(ts)) = v.get("tokens") {
        for (id, t) in ts {
            tokens.push(
                Obj::new()
                    .set("id", id.as_str())
                    .set("type", s(t, "type"))
                    .set("keyslots", t.get("keyslots").cloned().unwrap_or(Value::Arr(vec![])))
                    .build(),
            );
        }
    }
    let digest_hashes: Vec<String> = match v.get("digests") {
        Some(Value::Obj(ds)) => ds.iter().filter_map(|(_, dg)| s(dg, "hash")).collect(),
        _ => vec![],
    };
    let cfg = v.get("config");
    d.put("segments", segments);
    d.put("keyslots", keyslots);
    d.put("tokens", tokens);
    d.put("digest_hashes", digest_hashes);
    d.put("json_size", cfg.and_then(|c| s(c, "json_size")));
    d.put("keyslots_size", cfg.and_then(|c| s(c, "keyslots_size")));
    d.put("config_flags", cfg.and_then(|c| c.get("flags")).cloned().unwrap_or(Value::Arr(vec![])));
    d.put("config_requirements", cfg.and_then(|c| c.get("requirements")).cloned().unwrap_or(Value::Null));
}

#[cfg(test)]
pub mod sample {
    /// A LUKS2 header like `cryptsetup luksFormat --type luks2` makes, with
    /// one argon2id keyslot and the 16 KiB default header.
    pub fn luks2(uuid: &str, label: &str) -> Vec<u8> {
        let mut img = vec![0u8; 1 << 20];
        let json = r#"{"keyslots":{"0":{"type":"luks2","key_size":64,"af":{"type":"luks1","stripes":4000,"hash":"sha256"},"area":{"type":"raw","offset":"32768","size":"258048","encryption":"aes-xts-plain64","key_size":64},"kdf":{"type":"argon2id","time":4,"memory":1048576,"cpus":4,"salt":"c2FsdA=="}}},"tokens":{"0":{"type":"systemd-tpm2","keyslots":["0"]}},"segments":{"0":{"type":"crypt","offset":"16777216","size":"dynamic","iv_tweak":"0","encryption":"aes-xts-plain64","sector_size":4096}},"digests":{"0":{"type":"pbkdf2","keyslots":["0"],"segments":["0"],"hash":"sha256","iterations":1000,"salt":"eA==","digest":"eA=="}},"config":{"json_size":"12288","keyslots_size":"16744448","flags":["allow-discards"]}}"#;
        for off in [0usize, 16384] {
            let h = &mut img[off..off + 4096];
            h[0..6].copy_from_slice(if off == 0 { b"LUKS\xba\xbe" } else { b"SKUL\xba\xbe" });
            h[6..8].copy_from_slice(&2u16.to_be_bytes());
            h[8..16].copy_from_slice(&16384u64.to_be_bytes());
            h[16..24].copy_from_slice(&3u64.to_be_bytes());
            h[24..24 + label.len()].copy_from_slice(label.as_bytes());
            h[72..78].copy_from_slice(b"sha256");
            h[168..168 + uuid.len()].copy_from_slice(uuid.as_bytes());
            img[off + 4096..off + 4096 + json.len()].copy_from_slice(json.as_bytes());
        }
        img
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::bytes::MemSource;

    const U: &str = "6c3e5f0e-6b0a-4d6c-9a2e-8f7e1d2c3b4a";

    #[test]
    fn luks2_summary() {
        let c = probe(&MemSource(sample::luks2(U, "cryptroot"))).unwrap();
        assert_eq!(c.kind, "crypto_LUKS");
        assert_eq!(c.usage, "crypto");
        assert_eq!(c.version.as_deref(), Some("2"));
        assert_eq!(c.uuid.as_deref(), Some(U));
        assert_eq!(c.label.as_deref(), Some("cryptroot"));
        let d = c.details.build();
        assert_eq!(d.get("header_size").and_then(Value::as_u64), Some(16384));
        assert_eq!(d.get("secondary_header"), Some(&Value::Bool(true)));
        assert_eq!(d.at(&["segments", "0", "encryption"]).and_then(Value::as_str), Some("aes-xts-plain64"));
        assert_eq!(d.at(&["segments", "0", "offset"]).and_then(Value::as_str), Some("16777216"));
        assert_eq!(d.at(&["segments", "0", "sector_size"]).and_then(Value::as_u64), Some(4096));
        assert_eq!(d.at(&["keyslots", "0", "kdf"]).and_then(Value::as_str), Some("argon2id"));
        assert_eq!(d.at(&["keyslots", "0", "key_bits"]).and_then(Value::as_u64), Some(512));
        assert_eq!(d.at(&["keyslots", "0", "kdf_memory"]).and_then(Value::as_u64), Some(1048576));
        assert_eq!(d.at(&["tokens", "0", "type"]).and_then(Value::as_str), Some("systemd-tpm2"));
        assert_eq!(d.get("config_flags"), Some(&Value::from(vec!["allow-discards"])));
        assert_eq!(d.get("digest_hashes"), Some(&Value::from(vec!["sha256"])));
        // Secrets stay out of the report.
        let text = d.compact();
        for secret in ["salt", "digest\"", "c2FsdA"] {
            assert!(!text.contains(secret), "{secret} leaked: {text}");
        }
    }

    #[test]
    fn luks2_bad_json_and_sizes() {
        let mut img = sample::luks2(U, "");
        img[4096] = b'x';
        let d = probe(&MemSource(img)).unwrap().details.build();
        assert!(d.get("json_error").unwrap().as_str().unwrap().contains("unexpected character"));
        let mut img = sample::luks2(U, "");
        img[8..16].copy_from_slice(&100u64.to_be_bytes());
        let c = probe(&MemSource(img)).unwrap();
        assert_eq!(c.label, None);
        let d = c.details.build();
        assert!(d.get("json_error").unwrap().as_str().unwrap().contains("implausible"));
        let mut img = sample::luks2(U, "");
        img[16384] = 0;
        assert_eq!(probe(&MemSource(img)).unwrap().details.build().get("secondary_header"), Some(&Value::Bool(false)));
    }

    #[test]
    fn luks1_header() {
        let mut b = vec![0u8; 4096];
        b[0..6].copy_from_slice(MAGIC);
        b[6..8].copy_from_slice(&1u16.to_be_bytes());
        b[8..11].copy_from_slice(b"aes");
        b[40..52].copy_from_slice(b"xts-plain64\0");
        b[72..78].copy_from_slice(b"sha256");
        b[104..108].copy_from_slice(&4096u32.to_be_bytes());
        b[108..112].copy_from_slice(&64u32.to_be_bytes());
        b[168..168 + U.len()].copy_from_slice(U.as_bytes());
        b[208..212].copy_from_slice(&0x00ac_71f3u32.to_be_bytes());
        b[208 + 48 * 3..212 + 48 * 3].copy_from_slice(&0x00ac_71f3u32.to_be_bytes());
        b[208 + 48..212 + 48].copy_from_slice(&0x0000_deadu32.to_be_bytes());
        let c = probe(&MemSource(b.clone())).unwrap();
        assert_eq!(c.version.as_deref(), Some("1"));
        assert_eq!(c.uuid.as_deref(), Some(U));
        let d = c.details.build();
        assert_eq!(d.get("cipher").and_then(Value::as_str), Some("aes-xts-plain64"));
        assert_eq!(d.get("key_bits").and_then(Value::as_u64), Some(512));
        assert_eq!(d.get("data_offset_bytes").and_then(Value::as_u64), Some(4096 * 512));
        assert_eq!(d.get("keyslots_active").and_then(Value::as_u64), Some(2));
        b[7] = 3;
        assert!(probe(&MemSource(b)).is_none());
        assert!(probe(&MemSource(vec![0u8; 4096])).is_none());
    }
}
