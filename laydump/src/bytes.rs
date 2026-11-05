//! Reading raw bytes: block devices and images through `ByteSource`, and the
//! little helpers that pull integers, GUIDs and strings out of on-disk
//! structures.  Every getter returns 0 / empty past the end of its slice
//! instead of panicking: on-disk data is never trusted.

use std::cell::OnceCell;
use std::fs::File;
use std::io::{Seek, SeekFrom};
use std::os::unix::fs::FileExt;
use std::path::Path;

/// Something that can be read at an offset: a block device, an image, or a
/// buffer in a test.
pub trait ByteSource {
    fn size(&self) -> u64;
    /// Up to `len` bytes at `offset`; fewer at the end or on a read error.
    fn read_at(&self, offset: u64, len: usize) -> Vec<u8>;

    /// Exactly `len` bytes at `offset`, or None.
    fn read_exact_at(&self, offset: u64, len: usize) -> Option<Vec<u8>> {
        let b = self.read_at(offset, len);
        (b.len() == len).then_some(b)
    }
}

pub struct MemSource(pub Vec<u8>);

impl ByteSource for MemSource {
    fn size(&self) -> u64 {
        self.0.len() as u64
    }
    fn read_at(&self, offset: u64, len: usize) -> Vec<u8> {
        let Ok(start) = usize::try_from(offset) else { return Vec::new() };
        if start >= self.0.len() {
            return Vec::new();
        }
        let end = start.saturating_add(len).min(self.0.len());
        self.0[start..end].to_vec()
    }
}

/// How much of a device's start `FileSource` keeps after the first read
/// there: every signature this tool looks for at the start of a device lies
/// within it (a ZFS label is the largest, 256 KiB).
pub const HEAD: usize = 1 << 20;

pub struct FileSource {
    file: File,
    size: u64,
    head: OnceCell<Vec<u8>>,
}

impl FileSource {
    /// Opens a block device or image read-only.  Its size is where a seek to
    /// the end lands, which works for block devices as well as files.
    pub fn open(path: &Path) -> std::io::Result<FileSource> {
        let mut file = File::open(path)?;
        let size = file.seek(SeekFrom::End(0))?;
        Ok(FileSource { file, size, head: OnceCell::new() })
    }

    fn raw_read(&self, offset: u64, len: usize) -> Vec<u8> {
        let mut buf = vec![0u8; len];
        let mut got = 0;
        while got < len {
            match self.file.read_at(&mut buf[got..], offset + got as u64) {
                Ok(0) => break,
                Ok(n) => got += n,
                Err(e) if e.kind() == std::io::ErrorKind::Interrupted => continue,
                Err(_) => break,
            }
        }
        buf.truncate(got);
        buf
    }
}

impl ByteSource for FileSource {
    fn size(&self) -> u64 {
        self.size
    }
    fn read_at(&self, offset: u64, len: usize) -> Vec<u8> {
        if offset.saturating_add(len as u64) <= HEAD as u64 {
            let head = self.head.get_or_init(|| self.raw_read(0, HEAD));
            return MemSource::slice(head, offset as usize, len);
        }
        self.raw_read(offset, len)
    }
}

impl MemSource {
    fn slice(b: &[u8], start: usize, len: usize) -> Vec<u8> {
        if start >= b.len() {
            return Vec::new();
        }
        b[start..(start + len).min(b.len())].to_vec()
    }
}

fn arr<const N: usize>(b: &[u8], o: usize) -> Option<[u8; N]> {
    b.get(o..o.checked_add(N)?).map(|s| s.try_into().unwrap())
}

pub fn le16(b: &[u8], o: usize) -> u16 {
    arr(b, o).map_or(0, u16::from_le_bytes)
}
pub fn le32(b: &[u8], o: usize) -> u32 {
    arr(b, o).map_or(0, u32::from_le_bytes)
}
pub fn le64(b: &[u8], o: usize) -> u64 {
    arr(b, o).map_or(0, u64::from_le_bytes)
}
pub fn be16(b: &[u8], o: usize) -> u16 {
    arr(b, o).map_or(0, u16::from_be_bytes)
}
pub fn be32(b: &[u8], o: usize) -> u32 {
    arr(b, o).map_or(0, u32::from_be_bytes)
}
pub fn be64(b: &[u8], o: usize) -> u64 {
    arr(b, o).map_or(0, u64::from_be_bytes)
}
pub fn u8_at(b: &[u8], o: usize) -> u8 {
    b.get(o).copied().unwrap_or(0)
}

/// `b[o..o+n]`, or an empty slice when out of range.
pub fn sub(b: &[u8], o: usize, n: usize) -> &[u8] {
    b.get(o..o.saturating_add(n)).unwrap_or(&[])
}

pub fn is_zero(b: &[u8]) -> bool {
    b.iter().all(|&x| x == 0)
}

/// A GUID as EFI stores it (first three fields little-endian), in the
/// lower-case form /dev/disk/by-partuuid uses.
pub fn guid(b: &[u8]) -> String {
    let b = sub(b, 0, 16);
    if b.len() < 16 {
        return String::new();
    }
    format!(
        "{:08x}-{:04x}-{:04x}-{:02x}{:02x}-{:02x}{:02x}{:02x}{:02x}{:02x}{:02x}",
        le32(b, 0),
        le16(b, 4),
        le16(b, 6),
        b[8], b[9], b[10], b[11], b[12], b[13], b[14], b[15]
    )
}

/// Sixteen bytes in on-disk order as a UUID (ext4, XFS, btrfs, md).
pub fn uuid(b: &[u8]) -> String {
    let b = sub(b, 0, 16);
    if b.len() < 16 {
        return String::new();
    }
    let h = hex(b);
    format!("{}-{}-{}-{}-{}", &h[0..8], &h[8..12], &h[12..16], &h[16..20], &h[20..32])
}

pub fn hex(b: &[u8]) -> String {
    let mut s = String::with_capacity(b.len() * 2);
    for x in b {
        s.push_str(&format!("{x:02x}"));
    }
    s
}

/// Bytes up to the first NUL, as text (invalid UTF-8 replaced).
pub fn cstr(b: &[u8]) -> String {
    let end = b.iter().position(|&c| c == 0).unwrap_or(b.len());
    String::from_utf8_lossy(&b[..end]).into_owned()
}

/// A fixed-size label field: up to the first NUL, trailing spaces dropped;
/// None when that leaves nothing.
pub fn label(b: &[u8]) -> Option<String> {
    let s = cstr(b);
    let s = s.trim_end_matches(' ');
    (!s.is_empty()).then(|| s.to_string())
}

/// UTF-16LE up to the first NUL code unit.
pub fn utf16le(b: &[u8]) -> String {
    let units: Vec<u16> = b
        .chunks_exact(2)
        .map(|c| u16::from_le_bytes([c[0], c[1]]))
        .take_while(|&u| u != 0)
        .collect();
    String::from_utf16_lossy(&units)
}

/// Whether `needle` occurs in `hay`.
pub fn contains(hay: &[u8], needle: &[u8]) -> bool {
    !needle.is_empty() && hay.windows(needle.len()).any(|w| w == needle)
}

/// CRC-32 (IEEE 802.3, reflected), as GPT and zlib use it.
pub fn crc32(data: &[u8]) -> u32 {
    crc32_update(0, data)
}

pub fn crc32_update(crc: u32, data: &[u8]) -> u32 {
    let mut c = !crc;
    for &b in data {
        c = CRC_TABLE[((c ^ b as u32) & 0xff) as usize] ^ (c >> 8);
    }
    !c
}

const CRC_TABLE: [u32; 256] = {
    let mut t = [0u32; 256];
    let mut i = 0;
    while i < 256 {
        let mut c = i as u32;
        let mut k = 0;
        while k < 8 {
            c = if c & 1 != 0 { 0xedb8_8320 ^ (c >> 1) } else { c >> 1 };
            k += 1;
        }
        t[i] = c;
        i += 1;
    }
    t
};

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn crc32_check_values() {
        assert_eq!(crc32(b""), 0);
        assert_eq!(crc32(b"123456789"), 0xcbf4_3926);
        assert_eq!(crc32(b"The quick brown fox jumps over the lazy dog"), 0x414f_a339);
        let split = crc32_update(crc32(b"12345"), b"6789");
        assert_eq!(split, 0xcbf4_3926);
    }

    #[test]
    fn integers() {
        let b = [1u8, 2, 3, 4, 5, 6, 7, 8];
        assert_eq!(le16(&b, 0), 0x0201);
        assert_eq!(be16(&b, 0), 0x0102);
        assert_eq!(le32(&b, 0), 0x0403_0201);
        assert_eq!(be32(&b, 4), 0x0506_0708);
        assert_eq!(le64(&b, 0), 0x0807_0605_0403_0201);
        assert_eq!(be64(&b, 0), 0x0102_0304_0506_0708);
        assert_eq!(u8_at(&b, 7), 8);
        // Out of range reads are 0, never a panic.
        assert_eq!(le16(&b, 7), 0);
        assert_eq!(le32(&b, 6), 0);
        assert_eq!(le64(&b, 1), 0);
        assert_eq!(be64(&b, usize::MAX - 2), 0);
        assert_eq!(le16(&b, usize::MAX), 0);
        assert_eq!(le32(&b, usize::MAX - 1), 0);
        assert_eq!(u8_at(&b, 8), 0);
        assert_eq!(sub(&b, 6, 4), &[] as &[u8]);
        assert_eq!(sub(&b, usize::MAX, 4), &[] as &[u8]);
        assert_eq!(sub(&b, 6, 2), &[7, 8]);
    }

    #[test]
    fn guid_and_uuid_formats() {
        // The ESP type GUID as it sits in a GPT entry.
        let esp = [
            0x28, 0x73, 0x2a, 0xc1, 0x1f, 0xf8, 0xd2, 0x11, 0xba, 0x4b, 0x00, 0xa0, 0xc9, 0x3e, 0xc9,
            0x3b,
        ];
        assert_eq!(guid(&esp), "c12a7328-f81f-11d2-ba4b-00a0c93ec93b");
        let raw: Vec<u8> = (0u8..16).collect();
        assert_eq!(uuid(&raw), "00010203-0405-0607-0809-0a0b0c0d0e0f");
        assert_eq!(guid(&raw[..15]), "");
        assert_eq!(uuid(&raw[..3]), "");
    }

    #[test]
    fn strings() {
        assert_eq!(cstr(b"abc\0def"), "abc");
        assert_eq!(cstr(b"abc"), "abc");
        assert_eq!(cstr(b"\xffa\0"), "\u{fffd}a");
        assert_eq!(label(b"EFI        "), Some("EFI".into()));
        assert_eq!(label(b"\0\0\0"), None);
        assert_eq!(label(b"   "), None);
        assert_eq!(label(b"a b \0junk"), Some("a b".into()));
        let w: Vec<u8> = "Linux ☃".encode_utf16().flat_map(|u| u.to_le_bytes()).chain([0, 0, 0x41, 0]).collect();
        assert_eq!(utf16le(&w), "Linux ☃");
        assert_eq!(utf16le(&[0x41]), "");
        assert_eq!(hex(&[0, 0xab, 0x10]), "00ab10");
        assert!(contains(b"xxGRUB yy", b"GRUB "));
        assert!(!contains(b"GRU", b"GRUB"));
        assert!(!contains(b"abc", b""));
        assert!(is_zero(&[0, 0]));
        assert!(is_zero(&[]));
        assert!(!is_zero(&[0, 1]));
    }

    #[test]
    fn mem_source_reads() {
        let m = MemSource((0u8..10).collect());
        assert_eq!(m.size(), 10);
        assert_eq!(m.read_at(8, 4), vec![8, 9]);
        assert_eq!(m.read_at(10, 4), Vec::<u8>::new());
        assert_eq!(m.read_at(u64::MAX, 4), Vec::<u8>::new());
        assert_eq!(m.read_exact_at(2, 3), Some(vec![2, 3, 4]));
        assert_eq!(m.read_exact_at(8, 3), None);
    }

    #[test]
    fn file_source_reads_head_and_tail() {
        let dir = crate::testutil::TempDir::new("bytes");
        let path = dir.path().join("img");
        let mut data = vec![0u8; HEAD + 4096];
        for (i, b) in data.iter_mut().enumerate() {
            *b = (i % 251) as u8;
        }
        std::fs::write(&path, &data).unwrap();
        let f = FileSource::open(&path).unwrap();
        assert_eq!(f.size(), data.len() as u64);
        assert_eq!(f.read_at(0, 16), data[0..16].to_vec());
        // Straddling the cached head goes to the file.
        assert_eq!(f.read_at(HEAD as u64 - 8, 16), data[HEAD - 8..HEAD + 8].to_vec());
        assert_eq!(f.read_at(HEAD as u64 + 4000, 200), data[HEAD + 4000..].to_vec());
        assert_eq!(f.read_at(data.len() as u64, 10), Vec::<u8>::new());
        assert!(FileSource::open(&dir.path().join("missing")).is_err());
    }

    #[test]
    fn file_source_small_file() {
        let dir = crate::testutil::TempDir::new("bytes-small");
        let path = dir.path().join("img");
        std::fs::write(&path, b"hello").unwrap();
        let f = FileSource::open(&path).unwrap();
        assert_eq!(f.read_at(0, 512), b"hello".to_vec());
        assert_eq!(f.read_at(3, 2), b"lo".to_vec());
        assert_eq!(f.read_at(6, 2), Vec::<u8>::new());
    }
}
