//! `bko-holt clear-restricting-flags DIR` (not in the usage: run by load's
//! restore stage in each btrfs snapshot it makes of a base, before the
//! archiver's exact restore fills it: commands.rs `shared_snapshots`).
//!
//! A shared snapshot comes back as a writable snapshot of its base (the
//! subvolume it was a snapshot of on the source, restored by then), made
//! exact from the backup by `rustic-bko restore --exact`, which removes
//! what the backup's snapshot hasn't got and rewrites what differs.  An
//! entry of the base's that is immutable or append-only (chattr +i, +a)
//! refuses that: Linux lets no one unlink, rename or rewrite an immutable
//! file, nor remove anything from an immutable or append-only directory.
//! rustic-bko takes those flags off only where the backup's node for the
//! entry says it had them, so what the snapshot hasn't got at all keeps
//! them, and the restore fails, "Operation not permitted": BackShot2's
//! btrfs-1 content on anduinos2, planted in /srv after the deployment
//! snapshot of @root was taken, so in the base and not in the snapshot.
//!
//! This takes FS_IMMUTABLE_FL and FS_APPEND_FL off each regular file and
//! directory in DIR (DIR too) that has either, and changes nothing else: an
//! entry with neither isn't written to (its change time stays, and its
//! inode, which the base's shares).  The exact restore then puts back each
//! entry's flags as the backup's node has them: with a flag off, the
//! entry's metadata no longer matches its node's, so the restore sets it,
//! the flags last (rustic-bko's late flags pass), and what the snapshot
//! hasn't got is removed.  The snapshot ends with the source's flags,
//! exactly; the base keeps its own (this changes DIR alone).
//!
//! Each entry is opened by its name in its directory's descriptor, through
//! /proc/self/fd, so a tree deeper than PATH_MAX is walked whole; symlinks
//! aren't followed and special files aren't opened.  Each entry cleared is
//! printed, `clear-restricting-flags: PATH: i a` (what it had; PATH from
//! DIR, bytes outside printable ASCII as \xNN), then the count.  A file
//! system that keeps no inode flags (the ioctl's ENOTTY, EOPNOTSUPP,
//! EINVAL) has none to clear.  Anything else that fails is an error,
//! `bko-holt clear-restricting-flags: ...`, exit status 1, once the rest
//! is done.  Linux only (btrfs).

use std::fs::{File, FileType};
use std::io;
use std::os::unix::ffi::OsStrExt;
use std::os::unix::fs::MetadataExt;
use std::os::unix::io::AsRawFd;
use std::path::{Path, PathBuf};

/// FS_IMMUTABLE_FL (linux/fs.h).
pub const IMMUTABLE: u32 = 0x10;
/// FS_APPEND_FL.
pub const APPEND: u32 = 0x20;
/// The inode flags that refuse a restore's removing, replacing or
/// rewriting an entry.
pub const RESTRICTING: u32 = IMMUTABLE | APPEND;

/// An open file's inode flags, read and set.
pub trait Flags {
    /// Its flags; None where its file system keeps none.
    fn get(&mut self, f: &File) -> io::Result<Option<u32>>;
    /// Sets them to FLAGS.
    fn set(&mut self, f: &File, flags: u32) -> io::Result<()>;
}

/// What a walk did: the entries it looked at, those it cleared (each's
/// path from DIR, empty for DIR, and the restricting flags it had), and
/// what failed.
#[derive(Debug, Default)]
pub struct Walked {
    pub seen: u64,
    pub cleared: Vec<(Vec<u8>, u32)>,
    pub errors: Vec<String>,
}

/// PATH (from DIR) as printed: printable ASCII as it is but for `\`, the
/// rest as \xNN; DIR itself is ".".
pub fn show(rel: &[u8]) -> String {
    if rel.is_empty() {
        return ".".into();
    }
    let mut s = String::with_capacity(rel.len());
    for &b in rel {
        if (0x20..0x7f).contains(&b) && b != b'\\' {
            s.push(char::from(b));
        } else {
            s.push_str(&format!("\\x{b:02x}"));
        }
    }
    s
}

/// The flags FLAGS names, as chattr's letters.
pub fn letters(flags: u32) -> String {
    let mut v = Vec::new();
    if flags & IMMUTABLE != 0 {
        v.push("i");
    }
    if flags & APPEND != 0 {
        v.push("a");
    }
    v.join(" ")
}

/// Takes the restricting flags off each regular file and directory in
/// DIR that has any, DIR included, through OPS.  Err when DIR can't be
/// walked at all (it isn't a directory, or can't be opened).
pub fn clear_tree(dir: &Path, ops: &mut impl Flags) -> Result<Walked, String> {
    let m = std::fs::symlink_metadata(dir).map_err(|e| format!("{}: {e}", dir.display()))?;
    if !m.is_dir() {
        return Err(format!("{}: not a directory", dir.display()));
    }
    let root = File::open(dir).map_err(|e| format!("{}: {e}", dir.display()))?;
    let mut w = Walked::default();
    one(&root, b"", ops, &mut w);
    walk(&root, &mut Vec::new(), ops, &mut w);
    Ok(w)
}

/// The entry F (REL): its restricting flags off, if it has any.
fn one(f: &File, rel: &[u8], ops: &mut impl Flags, w: &mut Walked) {
    w.seen += 1;
    match ops.get(f) {
        Ok(Some(fl)) if fl & RESTRICTING != 0 => match ops.set(f, fl & !RESTRICTING) {
            Ok(()) => w.cleared.push((rel.to_vec(), fl & RESTRICTING)),
            Err(e) => w.errors.push(format!("{}: its flags ({}) not taken off: {e}", show(rel), letters(fl))),
        },
        Ok(_) => {}
        Err(e) => w.errors.push(format!("{}: its flags not read: {e}", show(rel))),
    }
}

/// Whether two types are the same, a regular file or a directory.
fn same_kind(a: FileType, b: FileType) -> bool {
    (a.is_dir() && b.is_dir()) || (a.is_file() && b.is_file())
}

/// Each regular file and directory below DIR (REL), depth first, by name:
/// a descriptor open for each directory above the one being read, none for
/// the rest.
fn walk(dir: &File, rel: &mut Vec<u8>, ops: &mut impl Flags, w: &mut Walked) {
    let at = PathBuf::from(format!("/proc/self/fd/{}", dir.as_raw_fd()));
    let mut entries = Vec::new();
    match std::fs::read_dir(&at) {
        Ok(rd) => {
            for e in rd {
                match e.and_then(|e| Ok((e.file_name(), e.file_type()?))) {
                    Ok(x) => entries.push(x),
                    Err(err) => w.errors.push(format!("{}: not read whole: {err}", show(rel))),
                }
            }
        }
        Err(e) => {
            w.errors.push(format!("{}: not read: {e}", show(rel)));
            return;
        }
    }
    entries.sort_by(|a, b| a.0.cmp(&b.0));
    for (name, ft) in entries {
        if !(ft.is_dir() || ft.is_file()) {
            continue;
        }
        let len = rel.len();
        if !rel.is_empty() {
            rel.push(b'/');
        }
        rel.extend_from_slice(name.as_bytes());
        match File::open(at.join(&name)) {
            // What was opened is what the directory listed (not a symlink
            // or a special file put there since).
            Ok(f) => match f.metadata() {
                Ok(m) if same_kind(m.file_type(), ft) => {
                    one(&f, rel, ops, w);
                    if ft.is_dir() {
                        walk(&f, rel, ops, w);
                    }
                }
                Ok(m) => w.errors.push(format!("{}: changed while it was walked (mode {:o})", show(rel), m.mode())),
                Err(e) => w.errors.push(format!("{}: {e}", show(rel))),
            },
            Err(e) => w.errors.push(format!("{}: not opened: {e}", show(rel))),
        }
        rel.truncate(len);
    }
}

/// The kernel's FS_IOC_GETFLAGS and FS_IOC_SETFLAGS, where holt is built
/// for an architecture with the generic ioctl numbers.
pub struct Kernel;

#[cfg(all(target_os = "linux", any(target_arch = "x86_64", target_arch = "x86", target_arch = "aarch64", target_arch = "arm", target_arch = "riscv64", target_arch = "loongarch64")))]
mod sys {
    use std::os::raw::{c_int, c_long, c_ulong};

    /// _IOR('f', 1, long) and _IOW('f', 2, long), asm-generic's encoding
    /// (direction in bits 30-31: read 2, write 1; the size in 16-29).
    const SIZE: c_ulong = std::mem::size_of::<c_long>() as c_ulong;
    pub const GETFLAGS: c_ulong = (2 << 30) | (SIZE << 16) | ((b'f' as c_ulong) << 8) | 1;
    pub const SETFLAGS: c_ulong = (1 << 30) | (SIZE << 16) | ((b'f' as c_ulong) << 8) | 2;

    // SAFETY: ioctl(2) is variadic and returns int in every Linux libc; its
    // request is an unsigned long in glibc's and an int in musl's, passed
    // in a register either way, and both numbers here fit in 32 bits.
    unsafe extern "C" {
        pub fn ioctl(fd: c_int, request: c_ulong, ...) -> c_int;
    }
}

#[cfg(all(target_os = "linux", any(target_arch = "x86_64", target_arch = "x86", target_arch = "aarch64", target_arch = "arm", target_arch = "riscv64", target_arch = "loongarch64")))]
impl Flags for Kernel {
    fn get(&mut self, f: &File) -> io::Result<Option<u32>> {
        let mut v: std::os::raw::c_int = 0;
        // SAFETY: f's descriptor is open for the call; FS_IOC_GETFLAGS
        // writes one int (the kernel's, whatever the number's size says)
        // through the pointer, to v, which outlives the call.
        let rc = unsafe { sys::ioctl(f.as_raw_fd(), sys::GETFLAGS, &mut v as *mut std::os::raw::c_int) };
        if rc == 0 {
            return Ok(Some(v as u32));
        }
        let e = io::Error::last_os_error();
        // ENOTTY, EINVAL, EOPNOTSUPP: no inode flags here.
        match e.raw_os_error() {
            Some(25 | 22 | 95) => Ok(None),
            _ => Err(e),
        }
    }

    fn set(&mut self, f: &File, flags: u32) -> io::Result<()> {
        let v = flags as std::os::raw::c_int;
        // SAFETY: f's descriptor is open for the call; FS_IOC_SETFLAGS reads
        // one int through the pointer, from v, which outlives the call.
        let rc = unsafe { sys::ioctl(f.as_raw_fd(), sys::SETFLAGS, &v as *const std::os::raw::c_int) };
        if rc == 0 { Ok(()) } else { Err(io::Error::last_os_error()) }
    }
}

#[cfg(not(all(target_os = "linux", any(target_arch = "x86_64", target_arch = "x86", target_arch = "aarch64", target_arch = "arm", target_arch = "riscv64", target_arch = "loongarch64"))))]
impl Flags for Kernel {
    fn get(&mut self, _f: &File) -> io::Result<Option<u32>> {
        Err(io::Error::other("inode flags are read only on Linux, on an architecture holt knows the ioctl's number of"))
    }

    fn set(&mut self, _f: &File, _flags: u32) -> io::Result<()> {
        Err(io::Error::other("inode flags are set only on Linux"))
    }
}

/// `bko-holt clear-restricting-flags DIR`.
pub fn main(args: &[String]) -> u8 {
    let [dir] = args else {
        eprintln!("usage: bko-holt clear-restricting-flags DIR");
        return 2;
    };
    let w = match clear_tree(Path::new(dir), &mut Kernel) {
        Ok(w) => w,
        Err(e) => {
            eprintln!("bko-holt clear-restricting-flags: {e}");
            return 1;
        }
    };
    for (rel, fl) in &w.cleared {
        println!("clear-restricting-flags: {}: {}", show(rel), letters(*fl));
    }
    println!(
        "clear-restricting-flags: {dir}: {} of {} entries were immutable or append-only, now neither, for the exact restore to give back as its snapshot has them",
        w.cleared.len(),
        w.seen
    );
    for e in &w.errors {
        eprintln!("bko-holt clear-restricting-flags: {e}");
    }
    u8::from(!w.errors.is_empty())
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::collections::HashMap;
    use std::os::unix::fs::MetadataExt;

    /// Inode flags kept by inode number, not in the file system: what a
    /// test gives a file, without the root that +i and +a need.
    #[derive(Default)]
    struct Fake {
        flags: HashMap<u64, u32>,
        sets: Vec<u64>,
        refuse: Option<u64>,
    }

    impl Flags for Fake {
        fn get(&mut self, f: &File) -> io::Result<Option<u32>> {
            Ok(Some(*self.flags.get(&f.metadata()?.ino()).unwrap_or(&0)))
        }
        fn set(&mut self, f: &File, flags: u32) -> io::Result<()> {
            let ino = f.metadata()?.ino();
            if self.refuse == Some(ino) {
                return Err(io::Error::from_raw_os_error(1));
            }
            self.sets.push(ino);
            self.flags.insert(ino, flags);
            Ok(())
        }
    }

    fn ino(p: &Path) -> u64 {
        std::fs::symlink_metadata(p).unwrap().ino()
    }

    /// anduinos2's case in small: a tree with an immutable file, an
    /// append-only one, an immutable directory, a hard link to the
    /// immutable file, a symlink to it and a FIFO.  Only those with flags
    /// are set, each once (the hard link's second name finds it cleared),
    /// keeping their other flags; the symlink and FIFO aren't opened.
    #[test]
    fn clears_only_what_has_them() {
        let t = crate::testutil::TempDir::new("unflag");
        let d = t.path().join("root");
        std::fs::create_dir_all(d.join("srv/bs2/b")).unwrap();
        std::fs::create_dir_all(d.join("etc")).unwrap();
        std::fs::write(d.join("srv/bs2/b/f26"), b"i").unwrap();
        std::fs::write(d.join("srv/bs2/b/f31"), b"a").unwrap();
        std::fs::write(d.join("etc/plain"), b"").unwrap();
        std::fs::hard_link(d.join("srv/bs2/b/f26"), d.join("etc/link")).unwrap();
        std::os::unix::fs::symlink("../srv/bs2/b/f26", d.join("etc/sym")).unwrap();
        let fifo = std::process::Command::new("mkfifo").arg(d.join("etc/fifo")).status().unwrap();
        assert!(fifo.success());
        let mut ops = Fake::default();
        ops.flags.insert(ino(&d.join("srv/bs2/b/f26")), IMMUTABLE | 0x0008_0000);
        ops.flags.insert(ino(&d.join("srv/bs2/b/f31")), APPEND);
        ops.flags.insert(ino(&d.join("srv/bs2")), IMMUTABLE);
        ops.flags.insert(ino(&d.join("etc/plain")), 0x0008_0000);
        let w = clear_tree(&d, &mut ops).unwrap();
        assert!(w.errors.is_empty(), "{:?}", w.errors);
        let got: Vec<(String, u32)> = w.cleared.iter().map(|(r, f)| (show(r), *f)).collect();
        // Depth first by name: etc/link before srv (f26's first name met).
        assert_eq!(got, vec![("etc/link".to_string(), IMMUTABLE), ("srv/bs2".into(), IMMUTABLE), ("srv/bs2/b/f31".into(), APPEND)]);
        assert_eq!(ops.flags[&ino(&d.join("srv/bs2/b/f26"))], 0x0008_0000);
        assert_eq!(ops.flags[&ino(&d.join("etc/plain"))], 0x0008_0000);
        assert_eq!(ops.sets.len(), 3, "only those with a restricting flag are set");
        // DIR, etc, link, plain, srv, bs2, b, f26, f31: not sym or fifo.
        assert_eq!(w.seen, 9);
    }

    /// A tree deeper than PATH_MAX (BackShot2's deep0) is walked whole:
    /// each entry opened by name in its directory's descriptor.
    #[test]
    fn walks_past_path_max() {
        let t = crate::testutil::TempDir::new("unflag-deep");
        let d = t.path().join("root");
        std::fs::create_dir(&d).unwrap();
        let name = "x".repeat(200);
        let mut cur = File::open(&d).unwrap();
        for _ in 0..30 {
            let at = PathBuf::from(format!("/proc/self/fd/{}", cur.as_raw_fd())).join(&name);
            std::fs::create_dir(&at).unwrap();
            cur = File::open(&at).unwrap();
        }
        let leaf = PathBuf::from(format!("/proc/self/fd/{}", cur.as_raw_fd())).join("leaf");
        std::fs::write(&leaf, b"x").unwrap();
        let leaf_ino = ino(&leaf);
        drop(cur);
        let mut ops = Fake::default();
        ops.flags.insert(leaf_ino, IMMUTABLE | APPEND);
        let w = clear_tree(&d, &mut ops).unwrap();
        assert!(w.errors.is_empty(), "{:?}", w.errors);
        assert_eq!(w.seen, 32);
        assert_eq!(w.cleared.len(), 1);
        let (rel, fl) = &w.cleared[0];
        assert!(rel.len() > 4096 && rel.ends_with(b"/leaf"), "{}", rel.len());
        assert_eq!(*fl, IMMUTABLE | APPEND);
        assert_eq!(ops.flags[&leaf_ino], 0);
        // TempDir removes it as it is dropped: a remove_dir_all of a tree
        // past PATH_MAX, which std does by descriptors too.
    }

    /// What can't be cleared is an error naming it, and the rest is still
    /// done; a DIR that isn't a directory is refused.
    #[test]
    fn a_failure_is_reported_and_the_rest_done() {
        let t = crate::testutil::TempDir::new("unflag-fail");
        let d = t.path().join("root");
        std::fs::create_dir(&d).unwrap();
        std::fs::write(d.join("a"), b"").unwrap();
        std::fs::write(d.join("b"), b"").unwrap();
        let mut ops = Fake::default();
        ops.flags.insert(ino(&d.join("a")), IMMUTABLE);
        ops.flags.insert(ino(&d.join("b")), APPEND);
        ops.refuse = Some(ino(&d.join("a")));
        let w = clear_tree(&d, &mut ops).unwrap();
        assert_eq!(w.errors, vec!["a: its flags (i) not taken off: Operation not permitted (os error 1)".to_string()]);
        assert_eq!(w.cleared, vec![(b"b".to_vec(), APPEND)]);
        assert!(clear_tree(&d.join("a"), &mut Fake::default()).unwrap_err().ends_with("/a: not a directory"));
        std::os::unix::fs::symlink(&d, t.path().join("link")).unwrap();
        assert!(clear_tree(&t.path().join("link"), &mut Fake::default()).unwrap_err().ends_with("/link: not a directory"));
    }

    #[test]
    fn names_are_shown_escaped() {
        assert_eq!(show(b""), ".");
        assert_eq!(show(b"a/b c"), "a/b c");
        assert_eq!(show(b"n\nl/\xff\\"), "n\\x0al/\\xff\\x5c");
        assert_eq!(letters(IMMUTABLE | APPEND | 0x80), "i a");
        assert_eq!(letters(0), "");
    }

    /// The kernel's own flags, as root on a file system that keeps them
    /// (the test's TMPDIR: tmpfs since Linux 6.0, ext4, btrfs, xfs): an
    /// immutable file and an append-only directory refuse their removal
    /// until the walk has cleared them, and their other flags stay.  Skipped
    /// (passing) where it can't be set up.
    #[cfg(target_os = "linux")]
    #[test]
    fn kernel_flags_cleared_as_root() {
        if crate::sys::euid() != 0 {
            eprintln!("kernel_flags_cleared_as_root: skipped, not root");
            return;
        }
        let t = crate::testutil::TempDir::new("unflag-root");
        let d = t.path().join("root");
        std::fs::create_dir_all(d.join("dir")).unwrap();
        std::fs::write(d.join("dir/f"), b"x").unwrap();
        let f = File::open(d.join("dir/f")).unwrap();
        let dir = File::open(d.join("dir")).unwrap();
        let mut k = Kernel;
        let Ok(Some(had)) = k.get(&f) else {
            eprintln!("kernel_flags_cleared_as_root: skipped, no inode flags in {}", t.path().display());
            return;
        };
        if k.set(&f, had | IMMUTABLE).is_err() {
            eprintln!("kernel_flags_cleared_as_root: skipped, +i refused in {}", t.path().display());
            return;
        }
        let dhad = k.get(&dir).unwrap().unwrap();
        k.set(&dir, dhad | APPEND).unwrap();
        assert!(std::fs::remove_file(d.join("dir/f")).is_err());
        let w = clear_tree(&d, &mut Kernel).unwrap();
        assert!(w.errors.is_empty(), "{:?}", w.errors);
        assert_eq!(w.cleared, vec![(b"dir".to_vec(), APPEND), (b"dir/f".to_vec(), IMMUTABLE)]);
        assert_eq!(k.get(&f).unwrap(), Some(had));
        assert_eq!(k.get(&dir).unwrap(), Some(dhad));
        std::fs::remove_file(d.join("dir/f")).unwrap();
    }
}
