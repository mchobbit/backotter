//! A password or passphrase, and the files it is handed on in
//! (docs/design/rescue-tui.md, "Security": "Passwords").
//!
//! - `Secret` is a buffer of fixed capacity (`CAP`, 1 KiB), allocated once
//!   and never grown, so no copy is left behind in freed memory.  It is
//!   zeroed with volatile writes and a compiler fence when dropped, and
//!   when a character is taken off it.
//! - It has no `Debug`, `Display` or `Clone`: it can't be printed or
//!   copied by accident.  A test checks there is no `Debug`.
//! - It is written only to a 0600 file made with `O_EXCL`, in a 0700
//!   directory in RAM, with no trailing newline (`write_file`).  Never to
//!   argv, the environment or a log.

use std::fs::{self, OpenOptions, Permissions};
use std::io::{self, Write};
use std::os::unix::fs::{DirBuilderExt as _, OpenOptionsExt as _, PermissionsExt as _};
use std::path::{Path, PathBuf};
use std::sync::atomic::{compiler_fence, Ordering};

/// The most a secret holds, in bytes.
pub const CAP: usize = 1024;

/// Zero `buf` so the compiler can't leave the writes out.
pub(crate) fn wipe(buf: &mut [u8]) {
    for b in buf.iter_mut() {
        // SAFETY: `b` is a unique reference to one initialised byte of
        // `buf`, so the pointer is valid, aligned and writable.
        unsafe { std::ptr::write_volatile(b as *mut u8, 0) };
    }
    compiler_fence(Ordering::SeqCst);
}

#[cfg(test)]
thread_local! {
    /// Whether the last Secret dropped on this thread was all zero after
    /// its wipe, and how many bytes it held.
    static LAST_DROP: std::cell::Cell<Option<(usize, bool)>> = const { std::cell::Cell::new(None) };
}

pub struct Secret {
    buf: Box<[u8; CAP]>,
    len: usize,
}

impl Default for Secret {
    fn default() -> Self {
        Secret::new()
    }
}

impl Secret {
    pub fn new() -> Secret {
        Secret { buf: Box::new([0u8; CAP]), len: 0 }
    }

    /// A secret from bytes, for tests and the scripted Ui.  The caller's
    /// copy is its own to wipe.
    pub fn from_bytes(b: &[u8]) -> Secret {
        let mut s = Secret::new();
        for &x in b.iter().take(CAP) {
            s.push_byte(x);
        }
        s
    }

    /// One more byte; false (and nothing kept) when it is full.
    pub fn push_byte(&mut self, b: u8) -> bool {
        if self.len == CAP {
            return false;
        }
        self.buf[self.len] = b;
        self.len += 1;
        true
    }

    /// One more character, as UTF-8; false (and nothing kept) when it
    /// doesn't fit whole.
    pub fn push_char(&mut self, c: char) -> bool {
        let mut tmp = [0u8; 4];
        let n = c.encode_utf8(&mut tmp).len();
        let ok = self.len + n <= CAP;
        if ok {
            self.buf[self.len..self.len + n].copy_from_slice(&tmp[..n]);
            self.len += n;
        }
        wipe(&mut tmp);
        ok
    }

    /// Take off the last character (its UTF-8 continuation bytes with it),
    /// zeroing what it held.  False when empty.
    pub fn pop_char(&mut self) -> bool {
        if self.len == 0 {
            return false;
        }
        let last = self.len - 1;
        let mut start = last;
        if self.buf[last] & 0xc0 == 0x80 {
            // Back over continuation bytes to the lead byte, at most three.
            let mut s = last;
            while s > 0 && last - s < 3 && self.buf[s] & 0xc0 == 0x80 {
                s -= 1;
            }
            let lead = self.buf[s];
            let want = match lead {
                0xf0.. => 4,
                0xe0.. => 3,
                0xc0.. => 2,
                _ => 0,
            };
            // A whole character goes whole; stray bytes (plain mode reads
            // bytes) go one at a time.
            if want == self.len - s {
                start = s;
            }
        }
        wipe(&mut self.buf[start..self.len]);
        self.len = start;
        true
    }

    /// Empty it, zeroed.
    pub fn clear(&mut self) {
        wipe(&mut self.buf[..]);
        self.len = 0;
    }

    /// Its length in bytes.
    pub fn len(&self) -> usize {
        self.len
    }

    pub fn is_empty(&self) -> bool {
        self.len == 0
    }

    pub fn is_full(&self) -> bool {
        self.len == CAP
    }

    /// How many `*` show: characters, a stray byte counted as one.
    pub fn chars(&self) -> usize {
        self.buf[..self.len].iter().filter(|&&b| b & 0xc0 != 0x80).count()
    }

    /// Whether any byte isn't ASCII: a console's keymap may send other
    /// bytes than the source system's desktop did, so the screens say so.
    pub fn non_ascii(&self) -> bool {
        self.buf[..self.len].iter().any(|&b| b >= 0x80)
    }

    /// The bytes, to write to a key file or compare.  Named so every use
    /// stands out.
    pub fn expose(&self) -> &[u8] {
        &self.buf[..self.len]
    }

    /// Write it to a new file `name` in `dir`: mode 0600, made with
    /// O_EXCL (an existing file or a symlink there is an error), no
    /// trailing newline.  `dir` must be `ram_dir`'s.  Returns the path.
    pub fn write_file(&self, dir: &Path, name: &str) -> io::Result<PathBuf> {
        if !plain_name(name) {
            return Err(io::Error::new(io::ErrorKind::InvalidInput, "a key file's name is letters, digits, '.', '_' and '-'"));
        }
        check_dir(dir)?;
        let path = dir.join(name);
        let f = OpenOptions::new().write(true).create_new(true).mode(0o600).open(&path)?;
        // The umask can only take bits away; set exactly 0600 anyway.
        if let Err(e) = f.set_permissions(Permissions::from_mode(0o600)) {
            drop(f);
            let _ = fs::remove_file(&path);
            return Err(e);
        }
        write_or_remove(&path, f, self.expose())?;
        Ok(path)
    }

    #[cfg(test)]
    fn raw(&self) -> &[u8; CAP] {
        &self.buf
    }
}

impl Drop for Secret {
    fn drop(&mut self) {
        let len = self.len;
        self.clear();
        #[cfg(test)]
        {
            let zero = self.buf.iter().all(|&b| b == 0);
            LAST_DROP.with(|d| d.set(Some((len, zero))));
        }
        let _ = len;
    }
}

/// Write `bytes` to `w`, the new file at `path`.  If that fails (ENOSPC
/// on a small tmpfs, EIO), the file is removed: no part of a secret is
/// left behind where the caller can't find it.
fn write_or_remove(path: &Path, mut w: impl Write, bytes: &[u8]) -> io::Result<()> {
    let r = w.write_all(bytes).and_then(|()| w.flush());
    if r.is_err() {
        drop(w);
        let _ = fs::remove_file(path);
    }
    r
}

/// A key file's name: 1-64 of letters, digits, `.`, `_` and `-`, not
/// starting with a dot.
fn plain_name(n: &str) -> bool {
    !n.is_empty()
        && n.len() <= 64
        && !n.starts_with('.')
        && n.bytes().all(|b| b.is_ascii_alphanumeric() || matches!(b, b'.' | b'_' | b'-'))
}

/// A directory only we can read: a real directory (not a symlink), with
/// no group or other bits.
fn check_dir(dir: &Path) -> io::Result<()> {
    let m = fs::symlink_metadata(dir)?;
    if !m.is_dir() {
        return Err(io::Error::other(format!("{} is not a directory", dir.display())));
    }
    if m.permissions().mode() & 0o077 != 0 {
        return Err(io::Error::other(format!("{} can be read by others (mode {:o})", dir.display(), m.permissions().mode() & 0o777)));
    }
    Ok(())
}

/// The directory in RAM for key files: made 0700 if it isn't there; if
/// it is, it must be a directory with no group or other bits.
pub fn ram_dir(dir: &Path) -> io::Result<()> {
    match fs::DirBuilder::new().mode(0o700).create(dir) {
        Ok(()) => fs::set_permissions(dir, Permissions::from_mode(0o700))?,
        Err(e) if e.kind() == io::ErrorKind::AlreadyExists => {}
        Err(e) => return Err(e),
    }
    check_dir(dir)
}

/// Where key files go on this platform (the RAM directory); Haiku's is
/// ui.md's open question 4.
pub fn default_dir() -> &'static str {
    if cfg!(target_os = "linux") {
        "/run/bko-rescue"
    } else {
        "/var/run/bko-rescue"
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::marker::PhantomData;

    fn last_drop() -> Option<(usize, bool)> {
        LAST_DROP.with(|d| d.take())
    }

    #[test]
    fn dropping_zeroes_it() {
        let mut s = Secret::new();
        for c in "correct horse ß".chars() {
            assert!(s.push_char(c));
        }
        let n = s.len();
        drop(s);
        assert_eq!(last_drop(), Some((n, true)));
    }

    #[test]
    fn it_never_grows_and_stops_at_its_cap() {
        let mut s = Secret::new();
        let p = s.raw().as_ptr();
        for i in 0..CAP {
            assert!(s.push_byte(b'a' + (i % 26) as u8));
        }
        assert!(s.is_full());
        assert!(!s.push_byte(b'x'));
        assert!(!s.push_char('é'));
        assert_eq!(s.raw().as_ptr(), p, "the same buffer");
        // A two-byte character that doesn't fit whole isn't half kept.
        s.pop_char();
        assert!(!s.push_char('é'));
        assert_eq!(s.len(), CAP - 1);
    }

    #[test]
    fn pop_takes_a_whole_character_and_zeroes_it() {
        let mut s = Secret::new();
        s.push_char('a');
        s.push_char('ж');
        s.push_char('€');
        assert_eq!((s.len(), s.chars()), (6, 3));
        assert!(s.non_ascii());
        assert!(s.pop_char());
        assert_eq!(s.expose(), "aж".as_bytes());
        assert!(s.raw()[3..].iter().all(|&b| b == 0), "the euro sign's bytes are zeroed");
        s.pop_char();
        s.pop_char();
        assert!(!s.pop_char());
        assert!(s.raw().iter().all(|&b| b == 0));
        // Bytes that aren't UTF-8 (plain mode reads bytes) go one a time.
        let mut s = Secret::from_bytes(&[b'x', 0x80, 0x80]);
        assert_eq!(s.chars(), 1);
        s.pop_char();
        assert_eq!(s.expose(), &[b'x', 0x80]);
    }

    #[test]
    fn clear_zeroes_it() {
        let mut s = Secret::from_bytes(b"hunter2");
        s.clear();
        assert!(s.is_empty() && s.raw().iter().all(|&b| b == 0));
    }

    /// The autoref trick: the inherent method is picked only when
    /// `T: Debug`, so this says whether a type has Debug without a
    /// compile-fail test.
    struct Probe<T>(PhantomData<T>);
    trait NoDebug {
        fn has_debug(&self) -> bool {
            false
        }
    }
    impl<T> NoDebug for Probe<T> {}
    impl<T: std::fmt::Debug> Probe<T> {
        #[allow(dead_code)]
        fn has_debug(&self) -> bool {
            true
        }
    }

    #[test]
    fn it_has_no_debug() {
        assert!(Probe::<String>(PhantomData).has_debug(), "the probe works");
        assert!(!Probe::<Secret>(PhantomData).has_debug());
    }

    fn scratch(name: &str) -> PathBuf {
        let d = std::env::temp_dir().join(format!("bko-rescue-test-{}-{name}", std::process::id()));
        let _ = fs::remove_dir_all(&d);
        d
    }

    #[test]
    fn key_files_are_0600_exclusive_and_have_no_newline() {
        let d = scratch("keys");
        ram_dir(&d).unwrap();
        assert_eq!(fs::metadata(&d).unwrap().permissions().mode() & 0o777, 0o700);
        let s = Secret::from_bytes(b"pass phrase");
        let p = s.write_file(&d, "0").unwrap();
        assert_eq!(fs::read(&p).unwrap(), b"pass phrase");
        assert_eq!(fs::metadata(&p).unwrap().permissions().mode() & 0o777, 0o600);
        assert!(s.write_file(&d, "0").is_err(), "O_EXCL: never over an existing file");
        // Not a symlink planted there either.
        std::os::unix::fs::symlink("/dev/null", d.join("1")).unwrap();
        assert!(s.write_file(&d, "1").is_err());
        for bad in ["", "../x", "a/b", ".hidden", &"x".repeat(65)] {
            assert!(s.write_file(&d, bad).is_err(), "{bad:?}");
        }
        fs::remove_dir_all(&d).unwrap();
    }

    /// Takes `room` bytes, then fails as a full disk does.
    struct Full {
        room: usize,
    }

    impl Write for Full {
        fn write(&mut self, b: &[u8]) -> io::Result<usize> {
            if self.room == 0 {
                return Err(io::Error::other("no space left on device"));
            }
            let n = b.len().min(self.room);
            self.room -= n;
            Ok(n)
        }
        fn flush(&mut self) -> io::Result<()> {
            Ok(())
        }
    }

    #[test]
    fn a_failed_write_leaves_no_file() {
        let d = scratch("full");
        ram_dir(&d).unwrap();
        let p = d.join("k");
        fs::write(&p, b"pass").unwrap();
        assert!(write_or_remove(&p, Full { room: 4 }, b"pass phrase").is_err());
        assert!(!p.exists(), "the part written is gone");
        fs::write(&p, b"").unwrap();
        assert!(write_or_remove(&p, Full { room: 64 }, b"pass phrase").is_ok());
        assert!(p.exists(), "kept when the write worked");
        fs::remove_dir_all(&d).unwrap();
    }

    #[test]
    fn the_directory_must_be_ours_alone() {
        let d = scratch("open");
        fs::create_dir(&d).unwrap();
        fs::set_permissions(&d, Permissions::from_mode(0o755)).unwrap();
        assert!(ram_dir(&d).is_err(), "an existing 0755 directory");
        assert!(Secret::from_bytes(b"x").write_file(&d, "k").is_err());
        fs::remove_dir_all(&d).unwrap();
        let f = scratch("file");
        fs::write(&f, b"").unwrap();
        assert!(ram_dir(&f).is_err(), "a file");
        fs::remove_file(&f).unwrap();
        let l = scratch("link");
        std::os::unix::fs::symlink(std::env::temp_dir(), &l).unwrap();
        assert!(ram_dir(&l).is_err(), "a symlink");
        fs::remove_file(&l).unwrap();
    }
}
