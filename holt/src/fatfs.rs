//! Reading files off a FAT12, FAT16 or FAT32 file system without mounting
//! it: the rescue stick's config partition (stick.rs, holt-json.md's
//! `bko-holt-stick`), read through the boot disk's own device.
//!
//! Read-only, and nothing on the disk is trusted: every size and cluster
//! number is checked against the volume before it's used, a cluster chain
//! that loops or leaves the volume is an error, and the caller gives each
//! read its limit.  Long names (VFAT) are read when their checksum matches
//! the short entry after them, else the short name is used; names compare
//! without regard to ASCII case, as FAT's do.  `probe/fat.rs` reads only the
//! boot sector, for the dump; this reads directories and files.

use crate::bytes::{le16, le32, sub, u8_at, ByteSource};
use std::cell::OnceCell;

/// The most of the first FAT this reads into memory (a 256 MiB FAT16 has
/// 128 KiB of it; a FAT32 of 2 TiB with 32 KiB clusters 256 MiB).
const FAT_MAX: u64 = 16 << 20;

/// The most entries one directory may have here: a directory read stops
/// with an error past it.
pub const DIR_ENTRIES_MAX: usize = 16384;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Kind {
    Fat12,
    Fat16,
    Fat32,
}

/// A file or directory in a directory.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Entry {
    /// Its long name, or its short one (`NAME.EXT`, lower-cased as the
    /// entry's case flags say).
    pub name: String,
    pub dir: bool,
    /// Its first cluster (0 for an empty file).
    pub cluster: u32,
    /// Its size in bytes (0 for a directory).
    pub size: u32,
}

/// An open FAT volume.
pub struct Volume<'a> {
    src: &'a dyn ByteSource,
    pub kind: Kind,
    /// The boot sector's volume serial (`blkid`'s UUID, `XXXX-XXXX`).
    pub serial: Option<u32>,
    /// The boot sector's label, trailing spaces dropped.
    pub boot_label: Option<String>,
    cluster_bytes: u64,
    fat_start: u64,
    fat_bytes: u64,
    /// FAT12/16's fixed root directory: its offset and size.
    root: (u64, u64),
    root_cluster: u32,
    data_start: u64,
    /// How many data clusters there are (2 to clusters + 1 are valid).
    clusters: u32,
    fat: OnceCell<Result<Vec<u8>, String>>,
}

impl<'a> Volume<'a> {
    /// The volume on SRC, from its boot sector.  An error says why it isn't
    /// a FAT file system this can read.
    pub fn open(src: &'a dyn ByteSource) -> Result<Volume<'a>, String> {
        let b = src.read_exact_at(0, 512).ok_or("its first sector can't be read")?;
        if b[510] != 0x55 || b[511] != 0xaa || !(b[0] == 0xeb || b[0] == 0xe9) {
            return Err("no FAT boot sector".into());
        }
        let bps = u64::from(le16(&b, 11));
        let spc = u64::from(u8_at(&b, 13));
        let reserved = u64::from(le16(&b, 14));
        let fats = u64::from(u8_at(&b, 16));
        let root_entries = u64::from(le16(&b, 17));
        let total16 = u64::from(le16(&b, 19));
        let fatsz16 = u64::from(le16(&b, 22));
        let total32 = u64::from(le32(&b, 32));
        if !matches!(bps, 512 | 1024 | 2048 | 4096) || spc == 0 || !spc.is_power_of_two() || reserved == 0 || fats == 0 || fats > 4 {
            return Err("its boot sector's geometry isn't FAT's".into());
        }
        let fatsz = if fatsz16 != 0 { fatsz16 } else { u64::from(le32(&b, 36)) };
        let total = if total16 != 0 { total16 } else { total32 };
        if fatsz == 0 || total == 0 {
            return Err("its boot sector gives no FAT size or no size".into());
        }
        let root_sectors = (root_entries * 32).div_ceil(bps);
        let meta = reserved + fats * fatsz + root_sectors;
        let clusters = total.checked_sub(meta).ok_or("its FATs are bigger than the volume")? / spc;
        let kind = if fatsz16 == 0 {
            Kind::Fat32
        } else if clusters < 4085 {
            Kind::Fat12
        } else {
            Kind::Fat16
        };
        if total * bps > src.size() {
            return Err(format!("its boot sector says {} bytes, and the partition has {}", total * bps, src.size()));
        }
        let clusters = u32::try_from(clusters).map_err(|_| "too many clusters".to_string())?;
        if clusters == 0 || clusters > 0x0fff_fff5 {
            return Err("no data clusters, or more than FAT32 has".into());
        }
        let ebr = if kind == Kind::Fat32 { 64 } else { 36 };
        let has_ebr = u8_at(&b, ebr + 2) == 0x29;
        let entry_bits = match kind {
            Kind::Fat12 => 12,
            Kind::Fat16 => 16,
            Kind::Fat32 => 32,
        };
        // The FAT itself must hold an entry for every cluster.
        if fatsz * bps * 8 < (u64::from(clusters) + 2) * entry_bits {
            return Err("its FAT is too small for its clusters".into());
        }
        Ok(Volume {
            src,
            kind,
            serial: has_ebr.then(|| le32(&b, ebr + 3)),
            boot_label: if has_ebr { crate::bytes::label(sub(&b, ebr + 7, 11)) } else { None },
            cluster_bytes: bps * spc,
            fat_start: reserved * bps,
            fat_bytes: fatsz * bps,
            root: ((reserved + fats * fatsz) * bps, root_sectors * bps),
            root_cluster: if kind == Kind::Fat32 { le32(&b, 44) & 0x0fff_ffff } else { 0 },
            data_start: meta * bps,
            clusters,
            fat: OnceCell::new(),
        })
    }

    /// The volume serial as blkid shows it (`3F2A-9C71`).
    pub fn serial_text(&self) -> Option<String> {
        self.serial.map(|s| format!("{:04X}-{:04X}", s >> 16, s & 0xffff))
    }

    /// The root directory's volume-label entry, the label people see.
    pub fn dir_label(&self) -> Option<String> {
        let raw = self.root_bytes().ok()?;
        for e in raw.chunks_exact(32) {
            match e[0] {
                0x00 => return None,
                0xe5 => continue,
                _ => {}
            }
            let attr = e[11];
            if attr & 0x0f != 0x0f && attr & 0x08 != 0 && attr & 0x10 == 0 {
                return crate::bytes::label(&e[..11]);
            }
        }
        None
    }

    fn fat(&self) -> Result<&[u8], String> {
        let r = self.fat.get_or_init(|| {
            let need = match self.kind {
                Kind::Fat12 => (u64::from(self.clusters) + 2) * 3 / 2 + 1,
                Kind::Fat16 => (u64::from(self.clusters) + 2) * 2,
                Kind::Fat32 => (u64::from(self.clusters) + 2) * 4,
            }
            .min(self.fat_bytes);
            if need > FAT_MAX {
                return Err(format!("its FAT is {need} bytes, more than this reads ({FAT_MAX})"));
            }
            self.src.read_exact_at(self.fat_start, need as usize).ok_or_else(|| "its FAT can't be read".to_string())
        });
        r.as_deref().map_err(Clone::clone)
    }

    /// The cluster after C in its chain: None at the chain's end.  A free,
    /// bad or out-of-range entry is an error: a chain never reaches one.
    fn next(&self, c: u32) -> Result<Option<u32>, String> {
        let fat = self.fat()?;
        let i = c as usize;
        let v = match self.kind {
            Kind::Fat12 => {
                let o = i + i / 2;
                let x = u32::from(le16(fat, o));
                if o + 2 > fat.len() {
                    return Err("a cluster chain leaves the FAT".into());
                }
                if i % 2 == 0 { x & 0xfff } else { x >> 4 }
            }
            Kind::Fat16 => {
                if i * 2 + 2 > fat.len() {
                    return Err("a cluster chain leaves the FAT".into());
                }
                u32::from(le16(fat, i * 2))
            }
            Kind::Fat32 => {
                if i * 4 + 4 > fat.len() {
                    return Err("a cluster chain leaves the FAT".into());
                }
                le32(fat, i * 4) & 0x0fff_ffff
            }
        };
        let end = match self.kind {
            Kind::Fat12 => 0xff8,
            Kind::Fat16 => 0xfff8,
            Kind::Fat32 => 0x0fff_fff8,
        };
        if v >= end {
            return Ok(None);
        }
        if v < 2 || v > self.clusters + 1 {
            return Err(format!("cluster {c}'s FAT entry ({v:#x}) is free, bad or past the volume's end"));
        }
        Ok(Some(v))
    }

    /// The clusters of the chain from FIRST, at most MAX of them.
    fn chain(&self, first: u32, max: u64) -> Result<Vec<u32>, String> {
        if first < 2 || first > self.clusters + 1 {
            return Err(format!("a file starts at cluster {first}, which the volume hasn't"));
        }
        let mut out = vec![first];
        let mut c = first;
        while let Some(n) = self.next(c)? {
            // Longer than the volume has clusters: it loops.
            if out.len() as u64 >= max || out.len() > self.clusters as usize {
                return Err("a cluster chain is longer than its file, or loops".into());
            }
            out.push(n);
            c = n;
        }
        Ok(out)
    }

    fn cluster_offset(&self, c: u32) -> u64 {
        self.data_start + u64::from(c - 2) * self.cluster_bytes
    }

    fn read_chain(&self, first: u32, max_clusters: u64) -> Result<Vec<u8>, String> {
        let mut out = Vec::new();
        for c in self.chain(first, max_clusters)? {
            let b = self.src.read_exact_at(self.cluster_offset(c), self.cluster_bytes as usize).ok_or_else(|| format!("cluster {c} can't be read"))?;
            out.extend_from_slice(&b);
        }
        Ok(out)
    }

    fn root_bytes(&self) -> Result<Vec<u8>, String> {
        if self.kind == Kind::Fat32 {
            let max = (DIR_ENTRIES_MAX as u64 * 32).div_ceil(self.cluster_bytes) + 1;
            return self.read_chain(self.root_cluster, max);
        }
        self.src.read_exact_at(self.root.0, self.root.1 as usize).ok_or_else(|| "the root directory can't be read".to_string())
    }

    /// The entries of directory DIR (the root when None), without `.`,
    /// `..`, deleted entries and the volume label.
    pub fn read_dir(&self, dir: Option<&Entry>) -> Result<Vec<Entry>, String> {
        let raw = match dir {
            None => self.root_bytes()?,
            Some(e) if !e.dir => return Err(format!("{} isn't a directory", e.name)),
            Some(e) if e.cluster == 0 => self.root_bytes()?,
            Some(e) => self.read_chain(e.cluster, (DIR_ENTRIES_MAX as u64 * 32).div_ceil(self.cluster_bytes) + 1)?,
        };
        let mut out = Vec::new();
        let mut long: Vec<(u8, [u16; 13], u8)> = Vec::new();
        for e in raw.chunks_exact(32) {
            if e[0] == 0x00 {
                break;
            }
            if e[0] == 0xe5 {
                long.clear();
                continue;
            }
            let attr = e[11];
            if attr & 0x3f == 0x0f {
                let mut u = [0u16; 13];
                for (k, o) in [1, 3, 5, 7, 9, 14, 16, 18, 20, 22, 24, 28, 30].into_iter().enumerate() {
                    u[k] = le16(e, o);
                }
                long.push((e[0], u, e[13]));
                continue;
            }
            let pieces = std::mem::take(&mut long);
            if attr & 0x08 != 0 {
                continue;
            }
            let short = &e[..11];
            if short == b".          " || short == b"..         " {
                continue;
            }
            let name = long_name(&pieces, short).unwrap_or_else(|| short_name(short, e[12]));
            let cluster = if self.kind == Kind::Fat32 { (u32::from(le16(e, 20)) << 16) | u32::from(le16(e, 26)) } else { u32::from(le16(e, 26)) };
            let dir = attr & 0x10 != 0;
            out.push(Entry { name, dir, cluster, size: if dir { 0 } else { le32(e, 28) } });
            if out.len() > DIR_ENTRIES_MAX {
                return Err(format!("a directory has more than {DIR_ENTRIES_MAX} entries"));
            }
        }
        Ok(out)
    }

    /// The entry at PATH (`backotter/machines`, slash-separated, from the
    /// root), its names compared without regard to ASCII case.
    pub fn find(&self, path: &str) -> Result<Option<Entry>, String> {
        let mut at: Option<Entry> = None;
        for part in path.split('/').filter(|p| !p.is_empty()) {
            if at.as_ref().is_some_and(|e| !e.dir) {
                return Ok(None);
            }
            match self.read_dir(at.as_ref())?.into_iter().find(|e| e.name.eq_ignore_ascii_case(part)) {
                Some(e) => at = Some(e),
                None => return Ok(None),
            }
        }
        Ok(at)
    }

    /// The bytes of file E, which must be no bigger than MAX: a bigger one
    /// is an error, read no further.
    pub fn read_file(&self, e: &Entry, max: u64) -> Result<Vec<u8>, String> {
        if e.dir {
            return Err(format!("{} is a directory", e.name));
        }
        let size = u64::from(e.size);
        if size > max {
            return Err(format!("{} is {size} bytes, more than {max}", e.name));
        }
        if size == 0 {
            return Ok(Vec::new());
        }
        let need = size.div_ceil(self.cluster_bytes);
        let mut b = self.read_chain(e.cluster, need)?;
        if (b.len() as u64) < size {
            return Err(format!("{} is {size} bytes, and its clusters hold {}", e.name, b.len()));
        }
        b.truncate(size as usize);
        Ok(b)
    }
}

/// The long name the pieces before SHORT spell, when they are whole: in
/// order from the last, numbered down to 1, each with SHORT's checksum.
fn long_name(pieces: &[(u8, [u16; 13], u8)], short: &[u8]) -> Option<String> {
    let (first, _, _) = pieces.first()?;
    if first & 0x40 == 0 {
        return None;
    }
    let n = usize::from(first & 0x1f);
    if n == 0 || n != pieces.len() {
        return None;
    }
    let sum = short.iter().fold(0u8, |s, &c| s.rotate_right(1).wrapping_add(c));
    let mut units = Vec::with_capacity(13 * n);
    for (k, (ord, u, ck)) in pieces.iter().rev().enumerate() {
        if usize::from(ord & 0x1f) != k + 1 || *ck != sum {
            return None;
        }
        units.extend_from_slice(u);
    }
    let end = units.iter().position(|&x| x == 0).unwrap_or(units.len());
    let name = String::from_utf16(&units[..end]).ok()?;
    (!name.is_empty()).then_some(name)
}

/// An 8.3 entry's name, `NAME.EXT`; byte 12's flags lower-case the base
/// (0x08) or the extension (0x10), as Windows writes `system.txt`.
fn short_name(s: &[u8], flags: u8) -> String {
    let conv = |b: &[u8], lower: bool| -> String {
        let t: String = b.iter().map(|&c| if c == 0x05 { 0xe5 } else { c }).map(char::from).collect();
        let t = t.trim_end_matches(' ').to_string();
        if lower { t.to_ascii_lowercase() } else { t }
    };
    let base = conv(&s[..8], flags & 0x08 != 0);
    let ext = conv(&s[8..11], flags & 0x10 != 0);
    if ext.is_empty() { base } else { format!("{base}.{ext}") }
}

/// FAT16 images for tests, laid out as `mkfs.fat -F 16` does (512-byte
/// sectors, 4 sectors a cluster, 512 root entries), with directories and
/// files under long names as mtools' `mcopy` writes them.  Not cfg(test):
/// the integration tests (tests/json_cli.rs) make sticks too.
#[doc(hidden)]
pub mod testimg {
    /// A file or directory to put in an image.
    pub enum Node {
        File(String, Vec<u8>),
        Dir(String, Vec<Node>),
    }

    pub fn file(name: &str, data: impl AsRef<[u8]>) -> Node {
        Node::File(name.to_string(), data.as_ref().to_vec())
    }

    pub fn dir(name: &str, nodes: Vec<Node>) -> Node {
        Node::Dir(name.to_string(), nodes)
    }

    const SPC: usize = 4;
    const CB: usize = SPC * 512;
    const RESERVED: usize = 4;
    const ROOT_ENTRIES: usize = 512;

    /// The image: TOTAL sectors, the boot sector's SERIAL and LABEL, the
    /// root's NODES.
    pub fn fat16(total: u16, serial: u32, label: &str, nodes: &[Node]) -> Vec<u8> {
        let mut img = boot16(total, serial, label);
        let fatsz = 64usize;
        let root_off = (RESERVED + 2 * fatsz) * 512;
        let data_off = root_off + ROOT_ENTRIES * 32;
        let mut b = Builder { img: &mut img, fatsz, data_off, next: 2, serial: 0 };
        b.set_fat(0, 0xfff8);
        b.set_fat(1, 0xffff);
        let mut root = Vec::new();
        if !label.is_empty() {
            let mut l = [b' '; 11];
            l[..label.len()].copy_from_slice(label.as_bytes());
            let mut e = [0u8; 32];
            e[..11].copy_from_slice(&l);
            e[11] = 0x08;
            root.extend_from_slice(&e);
        }
        for n in nodes {
            root.extend(b.node(n));
        }
        assert!(root.len() <= ROOT_ENTRIES * 32);
        b.img[root_off..root_off + root.len()].copy_from_slice(&root);
        img
    }

    /// The boot sector of such a volume, the rest zeros.
    fn boot16(total: u16, serial: u32, label: &str) -> Vec<u8> {
        let mut img = vec![0u8; usize::from(total) * 512];
        let b = &mut img[..512];
        b[0..3].copy_from_slice(&[0xeb, 0x3c, 0x90]);
        b[3..11].copy_from_slice(b"mkfs.fat");
        b[11..13].copy_from_slice(&512u16.to_le_bytes());
        b[13] = SPC as u8;
        b[14..16].copy_from_slice(&(RESERVED as u16).to_le_bytes());
        b[16] = 2;
        b[17..19].copy_from_slice(&(ROOT_ENTRIES as u16).to_le_bytes());
        b[19..21].copy_from_slice(&total.to_le_bytes());
        b[21] = 0xf8;
        b[22..24].copy_from_slice(&64u16.to_le_bytes());
        b[38] = 0x29;
        b[39..43].copy_from_slice(&serial.to_le_bytes());
        let mut l = *b"NO NAME    ";
        if !label.is_empty() {
            l = [b' '; 11];
            l[..label.len()].copy_from_slice(label.as_bytes());
        }
        b[43..54].copy_from_slice(&l);
        b[54..62].copy_from_slice(b"FAT16   ");
        b[510] = 0x55;
        b[511] = 0xaa;
        img
    }

    struct Builder<'a> {
        img: &'a mut Vec<u8>,
        fatsz: usize,
        data_off: usize,
        next: u16,
        serial: u16,
    }

    impl Builder<'_> {
        fn set_fat(&mut self, c: u16, v: u16) {
            for f in 0..2 {
                let o = (RESERVED + f * self.fatsz) * 512 + 2 * usize::from(c);
                self.img[o..o + 2].copy_from_slice(&v.to_le_bytes());
            }
        }

        /// DATA in a new chain of clusters: its first (0 when empty).
        fn store(&mut self, data: &[u8]) -> u16 {
            if data.is_empty() {
                return 0;
            }
            let n = data.len().div_ceil(CB);
            let first = self.next;
            for k in 0..n {
                let c = first + k as u16;
                let o = self.data_off + (usize::from(c) - 2) * CB;
                let piece = &data[k * CB..((k + 1) * CB).min(data.len())];
                self.img[o..o + piece.len()].copy_from_slice(piece);
                self.set_fat(c, if k + 1 == n { 0xffff } else { c + 1 });
            }
            self.next += n as u16;
            first
        }

        /// The directory entries for N, long-name pieces first.
        fn node(&mut self, n: &Node) -> Vec<u8> {
            let (name, cluster, size, attr) = match n {
                Node::File(name, data) => (name.clone(), self.store(data), data.len() as u32, 0x20u8),
                Node::Dir(name, kids) => {
                    let mut d = vec![0u8; 64];
                    d[..11].copy_from_slice(b".          ");
                    d[11] = 0x10;
                    d[32..43].copy_from_slice(b"..         ");
                    d[43] = 0x10;
                    for k in kids {
                        d.extend(self.node(k));
                    }
                    d.resize(d.len().div_ceil(CB) * CB, 0);
                    (name.clone(), self.store(&d), 0, 0x10u8)
                }
            };
            self.serial += 1;
            let short = format!("BKO{:05}   ", self.serial);
            let short = short.as_bytes();
            let sum = short.iter().fold(0u8, |s, &c| s.rotate_right(1).wrapping_add(c));
            let units: Vec<u16> = name.encode_utf16().collect();
            let pieces = units.len().div_ceil(13);
            let mut out = Vec::new();
            for p in (0..pieces).rev() {
                let mut e = [0u8; 32];
                e[0] = (p as u8 + 1) | if p + 1 == pieces { 0x40 } else { 0 };
                e[11] = 0x0f;
                e[13] = sum;
                for (k, o) in [1, 3, 5, 7, 9, 14, 16, 18, 20, 22, 24, 28, 30].into_iter().enumerate() {
                    let i = p * 13 + k;
                    let u = match i.cmp(&units.len()) {
                        std::cmp::Ordering::Less => units[i],
                        std::cmp::Ordering::Equal => 0,
                        std::cmp::Ordering::Greater => 0xffff,
                    };
                    e[o..o + 2].copy_from_slice(&u.to_le_bytes());
                }
                out.extend_from_slice(&e);
            }
            let mut e = [0u8; 32];
            e[..11].copy_from_slice(short);
            e[11] = attr;
            e[26..28].copy_from_slice(&cluster.to_le_bytes());
            e[28..32].copy_from_slice(&size.to_le_bytes());
            out.extend_from_slice(&e);
            out
        }
    }
}

#[cfg(test)]
mod tests {
    use super::testimg::{dir, fat16, file};
    use super::*;
    use crate::bytes::MemSource;

    fn img() -> Vec<u8> {
        let big: Vec<u8> = (0..10_000u32).map(|i| (i % 251) as u8).collect();
        fat16(
            32768,
            0x3f2a_9c71,
            "BACKOTTER",
            &[
                dir("backotter", vec![file("stick.json", b"{}"), dir("machines", vec![dir("0b7e3c52-8d1f-4a6e-9c3b-2f5d7a1e4b90", vec![file("system.json", &big), file("system.json.sha256", b"x")])])]),
                file("README.txt", b"hello"),
            ],
        )
    }

    #[test]
    fn reads_directories_and_files_under_long_names() {
        let src = MemSource(img());
        let v = Volume::open(&src).unwrap();
        assert_eq!(v.kind, Kind::Fat16);
        assert_eq!(v.serial_text().as_deref(), Some("3F2A-9C71"));
        assert_eq!(v.boot_label.as_deref(), Some("BACKOTTER"));
        assert_eq!(v.dir_label().as_deref(), Some("BACKOTTER"));
        let root: Vec<String> = v.read_dir(None).unwrap().into_iter().map(|e| e.name).collect();
        assert_eq!(root, ["backotter", "README.txt"]);
        let m = v.find("BACKOTTER/Machines").unwrap().unwrap();
        assert!(m.dir);
        let ids = v.read_dir(Some(&m)).unwrap();
        assert_eq!(ids.len(), 1);
        assert_eq!(ids[0].name, "0b7e3c52-8d1f-4a6e-9c3b-2f5d7a1e4b90");
        let f = v.find("backotter/machines/0b7e3c52-8d1f-4a6e-9c3b-2f5d7a1e4b90/system.json").unwrap().unwrap();
        let b = v.read_file(&f, 1 << 20).unwrap();
        assert_eq!(b.len(), 10_000);
        assert!(b.iter().enumerate().all(|(i, &x)| x == (i % 251) as u8));
        assert_eq!(v.read_file(&v.find("README.txt").unwrap().unwrap(), 100).unwrap(), b"hello");
        assert!(v.find("backotter/nothing").unwrap().is_none());
        assert!(v.find("README.txt/x").unwrap().is_none());
        // Over the caller's limit: refused, unread.
        assert!(v.read_file(&f, 9_999).unwrap_err().contains("more than 9999"));
    }

    #[test]
    fn short_names_and_their_case_flags() {
        assert_eq!(short_name(b"README  TXT", 0), "README.TXT");
        assert_eq!(short_name(b"README  TXT", 0x18), "readme.txt");
        assert_eq!(short_name(b"BIN        ", 0x08), "bin");
        // A long name whose checksum isn't its short entry's: the short.
        let mut u = [0xffffu16; 13];
        u[..2].copy_from_slice(&[u16::from(b'a'), 0]);
        assert_eq!(long_name(&[(0x41, u, 0)], b"BKO00001   "), None);
        let sum = b"BKO00001   ".iter().fold(0u8, |s, &c| s.rotate_right(1).wrapping_add(c));
        assert_eq!(long_name(&[(0x41, u, sum)], b"BKO00001   ").as_deref(), Some("a"));
        // Pieces out of order, or the last one's flag missing: the short.
        assert_eq!(long_name(&[(0x01, u, sum)], b"BKO00001   "), None);
        assert_eq!(long_name(&[(0x42, u, sum), (0x02, u, sum)], b"BKO00001   "), None);
    }

    /// The FAT entry of cluster C (both copies) in a testimg image.
    fn set_fat(img: &mut [u8], c: usize, v: u16) {
        for f in 0..2 {
            let o = (4 + f * 64) * 512 + 2 * c;
            img[o..o + 2].copy_from_slice(&v.to_le_bytes());
        }
    }

    #[test]
    fn chains_that_loop_or_leave_the_volume_are_errors() {
        // README.txt is the last file stored: its cluster's entry made to
        // point back at itself, then past the end, then free.
        let base = img();
        let src = MemSource(base.clone());
        let v = Volume::open(&src).unwrap();
        let readme = v.find("README.txt").unwrap().unwrap();
        let c = readme.cluster as usize;
        drop(v);
        for (val, words) in [(c as u16, "loops"), (0xfff0, "past the volume's end"), (0, "free")] {
            let mut b = base.clone();
            set_fat(&mut b, c, val);
            let src = MemSource(b);
            let v = Volume::open(&src).unwrap();
            let mut e = readme.clone();
            e.size = 100_000;
            let err = v.read_file(&e, 1 << 20).unwrap_err();
            assert!(err.contains(words), "{val:#x}: {err}");
        }
        // A file that says it's bigger than its chain.
        let v = Volume::open(&src).unwrap();
        let mut e = readme.clone();
        e.size = 5000;
        assert!(v.read_file(&e, 1 << 20).unwrap_err().contains("longer than its file") || v.read_file(&e, 1 << 20).unwrap_err().contains("clusters hold"));
        // A first cluster the volume hasn't.
        e.cluster = 60_000;
        assert!(v.read_file(&e, 1 << 20).unwrap_err().contains("hasn't"));
    }

    #[test]
    fn boot_sectors_that_are_not_fat() {
        assert!(Volume::open(&MemSource(vec![0u8; 4096])).err().unwrap().contains("no FAT boot sector"));
        let mut b = img();
        b[13] = 3;
        assert!(Volume::open(&MemSource(b)).err().unwrap().contains("geometry"));
        // Cut short: the boot sector says more than the partition has.
        let b = img()[..1 << 20].to_vec();
        assert!(Volume::open(&MemSource(b)).err().unwrap().contains("partition has"));
    }
}
