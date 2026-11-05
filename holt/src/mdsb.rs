//! Rewriting one field of an md v1.x superblock, for `bko-holt load`'s
//! close stage: Linux 6.18 and later write the array's logical block size
//! at byte 224, which older kernels read as padding that must be zero, and
//! then refuse the member ("does not have a valid v1.2 superblock").  An
//! array made by a newer rescue system's kernel gets the source's value back.

use std::fs::OpenOptions;
use std::io::{Read, Seek, SeekFrom, Write};

const MAGIC: u32 = 0xa92b_4efc;
const LBS: usize = 224;
const CSUM: usize = 216;
const MAX_DEV: usize = 220;

fn le32(b: &[u8], o: usize) -> u32 {
    u32::from_le_bytes([b[o], b[o + 1], b[o + 2], b[o + 3]])
}

/// The kernel's calc_sb_1_csum: 32-bit words over 256 + 2 * max_dev bytes,
/// the checksum field as zero, the carry folded in.
pub fn csum(sb: &[u8]) -> u32 {
    let size = 256 + 2 * le32(sb, MAX_DEV) as usize;
    let mut sum: u64 = 0;
    let mut i = 0;
    while i + 4 <= size {
        sum += if i == CSUM { 0 } else { le32(sb, i) as u64 };
        i += 4;
    }
    if size - i == 2 {
        sum += u16::from_le_bytes([sb[i], sb[i + 1]]) as u64;
    }
    ((sum & 0xffff_ffff) + (sum >> 32)) as u32
}

/// Sets the field in the superblock at byte `offset` of `dev`.  Ok(true)
/// when it changed, Ok(false) when it already held `value`.
pub fn set_logical_block_size(dev: &str, offset: u64, value: u32) -> Result<bool, String> {
    let mut f = OpenOptions::new().read(true).write(true).open(dev).map_err(|e| format!("{dev}: {e}"))?;
    let mut sb = vec![0u8; 4096];
    f.seek(SeekFrom::Start(offset)).and_then(|_| f.read_exact(&mut sb)).map_err(|e| format!("{dev}: reading the superblock: {e}"))?;
    if le32(&sb, 0) != MAGIC || le32(&sb, 4) != 1 {
        return Err(format!("{dev}: no md v1 superblock at byte {offset}"));
    }
    if 256 + 2 * le32(&sb, MAX_DEV) as usize > sb.len() {
        return Err(format!("{dev}: max_dev {} is too big", le32(&sb, MAX_DEV)));
    }
    if csum(&sb) != le32(&sb, CSUM) {
        return Err(format!("{dev}: the superblock's checksum doesn't match; left alone"));
    }
    if le32(&sb, LBS) == value {
        return Ok(false);
    }
    sb[LBS..LBS + 4].copy_from_slice(&value.to_le_bytes());
    let c = csum(&sb);
    sb[CSUM..CSUM + 4].copy_from_slice(&c.to_le_bytes());
    f.seek(SeekFrom::Start(offset)).and_then(|_| f.write_all(&sb)).and_then(|_| f.sync_all()).map_err(|e| format!("{dev}: writing the superblock: {e}"))?;
    Ok(true)
}

/// `bko-holt md-lbs DEV OFFSET VALUE`, run by load's close stage.
pub fn main(args: &[String]) -> u8 {
    let [dev, off, val] = args else {
        eprintln!("usage: bko-holt md-lbs DEV SUPERBLOCK_OFFSET VALUE");
        return 2;
    };
    let (Ok(off), Ok(val)) = (off.parse::<u64>(), val.parse::<u32>()) else {
        eprintln!("bko-holt md-lbs: OFFSET and VALUE are numbers");
        return 2;
    };
    match set_logical_block_size(dev, off, val) {
        Ok(true) => {
            println!("{dev}: md superblock's logical block size set to {val}");
            0
        }
        Ok(false) => 0,
        Err(e) => {
            eprintln!("bko-holt md-lbs: {e}");
            1
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn checksum_and_rewrite() {
        // A superblock as Linux 7.0 left one (debian13-stack's restored md1):
        // max_dev 128, logical block size 512.
        let mut sb = vec![0u8; 4096];
        sb[0..4].copy_from_slice(&MAGIC.to_le_bytes());
        sb[4..8].copy_from_slice(&1u32.to_le_bytes());
        sb[MAX_DEV..MAX_DEV + 4].copy_from_slice(&128u32.to_le_bytes());
        sb[LBS..LBS + 4].copy_from_slice(&512u32.to_le_bytes());
        sb[256..258].copy_from_slice(&0u16.to_le_bytes());
        sb[258..260].copy_from_slice(&1u16.to_le_bytes());
        let c = csum(&sb);
        sb[CSUM..CSUM + 4].copy_from_slice(&c.to_le_bytes());
        let path = std::env::temp_dir().join(format!("bko-holt-mdsb-{}", std::process::id()));
        let mut img = vec![0u8; 4096];
        img.extend_from_slice(&sb);
        std::fs::write(&path, &img).unwrap();
        let p = path.to_str().unwrap();
        assert_eq!(set_logical_block_size(p, 4096, 0), Ok(true));
        let after = std::fs::read(&path).unwrap();
        let sb2 = &after[4096..];
        assert_eq!(le32(sb2, LBS), 0);
        assert_eq!(csum(sb2), le32(sb2, CSUM));
        assert_eq!(set_logical_block_size(p, 4096, 0), Ok(false));
        assert!(set_logical_block_size(p, 0, 0).unwrap_err().contains("no md v1 superblock"));
        std::fs::remove_file(&path).unwrap();
    }
}
