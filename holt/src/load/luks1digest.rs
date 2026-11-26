//! `bko-holt luks1-digest DEV VOLUME-KEY-FILE ITERATIONS` (not in the
//! usage: run by load's assemble stage after a LUKS1 luksFormat,
//! commands.rs `luks()`).
//!
//! A LUKS1 header checks a volume key by its digest: PBKDF2 of the key
//! with the header's hash, its mkDigestSalt and its mkDigestIterations, 20
//! bytes (cryptsetup's lib/luks1/keymanage.c).  cryptsetup's luksFormat
//! takes the keyslot's iterations (`--pbkdf-force-iterations`), but sets
//! the digest's itself: benchmarked, or 1000 (LUKS_MKD_ITERATIONS_MIN)
//! whenever the keyslot's count is forced, and no option names it.  So a
//! LUKS1 container load makes again would have 1000 where the source had
//! its own count (Guix's 394795: docs/design/guix.md "guix15-luks", run
//! glk3's judge).  This sets mkDigestIterations to ITERATIONS and the
//! digest to match, from the volume key load gave luksFormat
//! (`--volume-key-file`), in place: header bytes 112..132 (mkDigest) and
//! 164..168 (mkDigestIterations), nothing else.  First it checks that the
//! key is the header's (its digest with the header's own count), and after
//! writing it reads the header back; a header already at ITERATIONS is left
//! as it is.  SHA-256 headers only (cryptsetup's LUKS1 default since 1.7);
//! load doesn't run it for another hash.

use std::fs::OpenOptions;
use std::io::{Read, Seek, SeekFrom, Write};

const MAGIC: &[u8] = b"LUKS\xba\xbe";
const HEADER: usize = 592;
const DIGEST: usize = 112;
const DIGEST_LEN: usize = 20;
const SALT: usize = 132;
const SALT_LEN: usize = 32;
const ITER: usize = 164;
const KEY_BYTES: usize = 108;
const HASH: usize = 72;

/// HMAC-SHA-256 (RFC 2104) of MSG with KEY.
pub fn hmac_sha256(key: &[u8], msg: &[u8]) -> [u8; 32] {
    let mut k = [0u8; 64];
    if key.len() > 64 {
        k[..32].copy_from_slice(&crate::bytes::sha256(key));
    } else {
        k[..key.len()].copy_from_slice(key);
    }
    let mut inner = Vec::with_capacity(64 + msg.len());
    inner.extend(k.iter().map(|b| b ^ 0x36));
    inner.extend_from_slice(msg);
    let ih = crate::bytes::sha256(&inner);
    let mut outer = Vec::with_capacity(96);
    outer.extend(k.iter().map(|b| b ^ 0x5c));
    outer.extend_from_slice(&ih);
    crate::bytes::sha256(&outer)
}

/// PBKDF2-HMAC-SHA-256 (RFC 8018), LEN bytes.
pub fn pbkdf2_sha256(password: &[u8], salt: &[u8], iterations: u32, len: usize) -> Vec<u8> {
    let mut out = Vec::with_capacity(len);
    let mut block = 1u32;
    while out.len() < len {
        let mut m = salt.to_vec();
        m.extend(block.to_be_bytes());
        let mut u = hmac_sha256(password, &m);
        let mut t = u;
        for _ in 1..iterations.max(1) {
            u = hmac_sha256(password, &u);
            for (x, y) in t.iter_mut().zip(u) {
                *x ^= y;
            }
        }
        out.extend_from_slice(&t[..(len - out.len()).min(32)]);
        block += 1;
    }
    out
}

/// HEADER with its digest made for KEY at ITERATIONS: Ok(None) when it is
/// at ITERATIONS already.  Err when HEADER is no LUKS1 SHA-256 header or
/// KEY isn't its volume key.
pub fn redigest(header: &[u8], key: &[u8], iterations: u32) -> Result<Option<Vec<u8>>, String> {
    if header.len() < HEADER || &header[..6] != MAGIC || header[6..8] != [0, 1] {
        return Err("no LUKS1 header".into());
    }
    let hash = crate::bytes::cstr(&header[HASH..HASH + 32]);
    if hash != "sha256" {
        return Err(format!("its hash is {hash}: only sha256 is done"));
    }
    let key_bytes = crate::bytes::be32(header, KEY_BYTES) as usize;
    if key.len() != key_bytes {
        return Err(format!(
            "the volume key file has {} bytes, the header's key {key_bytes}",
            key.len()
        ));
    }
    if iterations < 1000 {
        return Err(format!(
            "{iterations} iterations: cryptsetup's least is 1000"
        ));
    }
    let salt = &header[SALT..SALT + SALT_LEN];
    let now = crate::bytes::be32(header, ITER);
    if pbkdf2_sha256(key, salt, now, DIGEST_LEN) != header[DIGEST..DIGEST + DIGEST_LEN] {
        return Err("the volume key file isn't this header's key (its digest differs)".into());
    }
    if now == iterations {
        return Ok(None);
    }
    let mut h = header[..HEADER].to_vec();
    h[DIGEST..DIGEST + DIGEST_LEN]
        .copy_from_slice(&pbkdf2_sha256(key, salt, iterations, DIGEST_LEN));
    h[ITER..ITER + 4].copy_from_slice(&iterations.to_be_bytes());
    Ok(Some(h))
}

fn one(dev: &str, key_file: &str, iterations: u32) -> Result<String, String> {
    let key = crate::bytes::read_bounded(std::path::Path::new(key_file), 1024)
        .map_err(|e| format!("{key_file}: {e}"))?;
    let mut f = OpenOptions::new()
        .read(true)
        .write(true)
        .open(dev)
        .map_err(|e| format!("{dev}: {e}"))?;
    let mut header = vec![0u8; HEADER];
    f.read_exact(&mut header)
        .map_err(|e| format!("{dev}: reading its header: {e}"))?;
    let from = crate::bytes::be32(&header, ITER);
    let Some(new) = redigest(&header, &key, iterations).map_err(|e| format!("{dev}: {e}"))? else {
        return Ok(format!(
            "{dev}: volume key digest at {iterations} iterations already"
        ));
    };
    f.seek(SeekFrom::Start(0))
        .and_then(|_| f.write_all(&new))
        .and_then(|_| f.sync_all())
        .map_err(|e| format!("{dev}: writing its header: {e}"))?;
    let mut back = vec![0u8; HEADER];
    f.seek(SeekFrom::Start(0))
        .and_then(|_| f.read_exact(&mut back))
        .map_err(|e| format!("{dev}: reading its header back: {e}"))?;
    if back != new {
        return Err(format!("{dev}: its header reads back other than written"));
    }
    Ok(format!(
        "{dev}: volume key digest {from} -> {iterations} iterations"
    ))
}

/// `bko-holt luks1-digest DEV VOLUME-KEY-FILE ITERATIONS`.
pub fn main(args: &[String]) -> u8 {
    let [dev, key, iter] = args else {
        eprintln!("usage: bko-holt luks1-digest DEV VOLUME-KEY-FILE ITERATIONS");
        return 2;
    };
    let Ok(iterations) = iter.parse::<u32>() else {
        eprintln!("usage: bko-holt luks1-digest DEV VOLUME-KEY-FILE ITERATIONS");
        return 2;
    };
    match one(dev, key, iterations) {
        Ok(m) => {
            println!("luks1-digest: {m}");
            0
        }
        Err(e) => {
            eprintln!("bko-holt luks1-digest: {e}");
            1
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn hex(s: &str) -> Vec<u8> {
        (0..s.len())
            .step_by(2)
            .map(|i| u8::from_str_radix(&s[i..i + 2], 16).unwrap())
            .collect()
    }

    /// RFC 4231's test cases 1, 2 and 6 (a key longer than the block).
    #[test]
    fn hmac_sha256_vectors() {
        assert_eq!(
            hmac_sha256(&[0x0b; 20], b"Hi There").to_vec(),
            hex("b0344c61d8db38535ca8afceaf0bf12b881dc200c9833da726e9376c2e32cff7")
        );
        assert_eq!(
            hmac_sha256(b"Jefe", b"what do ya want for nothing?").to_vec(),
            hex("5bdcc146bf60754e6a042426089575c75a003f089d2739839dec58b964ec3843")
        );
        assert_eq!(
            hmac_sha256(
                &[0xaa; 131],
                b"Test Using Larger Than Block-Size Key - Hash Key First"
            )
            .to_vec(),
            hex("60e431591ee0b67f0d8a26aacbf5b77f8e0bc6213728c5140546040f0ee37f54")
        );
    }

    /// RFC 7914's PBKDF2-HMAC-SHA-256 vectors (section 11), and the
    /// common ones for "password"/"salt".
    #[test]
    fn pbkdf2_sha256_vectors() {
        assert_eq!(
            pbkdf2_sha256(b"passwd", b"salt", 1, 64),
            hex(
                "55ac046e56e3089fec1691c22544b605f94185216dde0465e68b9d57c20dacbc49ca9cccf179b645991664b39d77ef317c71b845b1e30bd509112041d3a19783"
            )
        );
        assert_eq!(
            pbkdf2_sha256(b"password", b"salt", 1, 32),
            hex("120fb6cffcf8b32c43e7225256c4f837a86548c92ccc35480805987cb70be17b")
        );
        assert_eq!(
            pbkdf2_sha256(b"password", b"salt", 2, 32),
            hex("ae4d0c95af6b46d32d0adff928f06dd02a303f8ef3c251dfd6e2d85a95474c43")
        );
        assert_eq!(
            pbkdf2_sha256(b"password", b"salt", 4096, 20),
            hex("c5e478d59288c841aa530db6845c4c8d962893a0")
        );
    }

    /// A LUKS1 header as cryptsetup lays one out, with KEY's digest at ITER.
    fn header(key: &[u8], iter: u32, hash: &str) -> Vec<u8> {
        let mut h = vec![0u8; HEADER];
        h[..6].copy_from_slice(MAGIC);
        h[6..8].copy_from_slice(&[0, 1]);
        h[8..11].copy_from_slice(b"aes");
        h[40..52].copy_from_slice(b"xts-plain64\0");
        h[HASH..HASH + hash.len()].copy_from_slice(hash.as_bytes());
        h[104..108].copy_from_slice(&4096u32.to_be_bytes());
        h[KEY_BYTES..KEY_BYTES + 4].copy_from_slice(&(key.len() as u32).to_be_bytes());
        for (i, b) in h[SALT..SALT + SALT_LEN].iter_mut().enumerate() {
            *b = i as u8 * 7 + 1;
        }
        let d = pbkdf2_sha256(key, &h[SALT..SALT + SALT_LEN], iter, DIGEST_LEN);
        h[DIGEST..DIGEST + DIGEST_LEN].copy_from_slice(&d);
        h[ITER..ITER + 4].copy_from_slice(&iter.to_be_bytes());
        h[168..172].copy_from_slice(b"e28d");
        h
    }

    #[test]
    fn redigest_sets_the_count_and_a_digest_the_key_checks_against() {
        let key = [0x5au8; 64];
        let h = header(&key, 1000, "sha256");
        let new = redigest(&h, &key, 2345).unwrap().unwrap();
        assert_eq!(crate::bytes::be32(&new, ITER), 2345);
        assert_eq!(
            new[DIGEST..DIGEST + DIGEST_LEN],
            pbkdf2_sha256(&key, &h[SALT..SALT + SALT_LEN], 2345, DIGEST_LEN)[..]
        );
        // Only the digest and its count changed.
        for (i, (a, b)) in h.iter().zip(&new).enumerate() {
            if !(DIGEST..DIGEST + DIGEST_LEN).contains(&i) && !(ITER..ITER + 4).contains(&i) {
                assert_eq!(a, b, "byte {i}");
            }
        }
        // Again: nothing to do.
        assert_eq!(redigest(&new, &key, 2345).unwrap(), None);
    }

    #[test]
    fn redigest_refuses_another_key_hash_or_header() {
        let key = [0x5au8; 64];
        let h = header(&key, 1000, "sha256");
        assert!(
            redigest(&h, &[0x5b; 64], 2345)
                .unwrap_err()
                .contains("isn't this header's key")
        );
        assert!(
            redigest(&h, &[0x5a; 32], 2345)
                .unwrap_err()
                .contains("32 bytes")
        );
        assert!(
            redigest(&h, &key, 999)
                .unwrap_err()
                .contains("least is 1000")
        );
        assert!(
            redigest(&header(&key, 1000, "sha1"), &key, 2345)
                .unwrap_err()
                .contains("sha1")
        );
        let mut two = h.clone();
        two[6..8].copy_from_slice(&[0, 2]);
        assert!(redigest(&two, &key, 2345).unwrap_err().contains("no LUKS1"));
        assert!(redigest(&h[..100], &key, 2345).is_err());
    }

    #[test]
    fn the_helper_rewrites_a_device_in_place() {
        let d = std::env::temp_dir().join(format!("bko-luks1-digest-{}", std::process::id()));
        std::fs::create_dir_all(&d).unwrap();
        let key = [0x11u8; 64];
        let dev = d.join("dev");
        let kf = d.join("vk");
        let mut img = header(&key, 1000, "sha256");
        img.extend(vec![0xeeu8; 4096]);
        std::fs::write(&dev, &img).unwrap();
        std::fs::write(&kf, key).unwrap();
        let (dv, kv) = (
            dev.to_string_lossy().into_owned(),
            kf.to_string_lossy().into_owned(),
        );
        assert_eq!(main(&[dv.clone(), kv.clone(), "3000".into()]), 0);
        let after = std::fs::read(&dev).unwrap();
        assert_eq!(crate::bytes::be32(&after, ITER), 3000);
        assert_eq!(after[HEADER..], img[HEADER..]);
        assert_eq!(redigest(&after, &key, 3000).unwrap(), None);
        assert!(one(&dv, &kv, 3000).unwrap().contains("already"));
        assert_eq!(main(&[dv.clone(), kv, "x".into()]), 2);
        assert_eq!(main(&[dv]), 2);
        std::fs::remove_dir_all(&d).unwrap();
    }
}
