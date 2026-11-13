//! The system being described.  Every file the tool reads (/sys, /proc, /dev,
//! /etc, /run, /boot, /var) goes through `Sys`, relative to a root that is "/"
//! for real and a fake tree in tests; every command it runs goes through a
//! `Runner`, so tests can answer for it.

use crate::bytes::{ByteSource, FileSource};
use std::cell::RefCell;
use std::collections::HashMap;
use std::io::Read;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::time::{Duration, Instant};

pub trait Runner {
    /// The command's standard output when it exits 0, else None.
    fn run(&self, prog: &str, args: &[&str]) -> Option<String>;

    /// The same for a program named by its absolute path, wherever it is
    /// (load's archiver, which the rescue stick keeps in /usr/local/bin).
    /// The dump never uses this: it runs the system's own tools only.
    fn run_file(&self, path: &str, args: &[&str]) -> Option<String> {
        self.run(path, args)
    }

    /// The command's standard output whatever its exit status, for a tool
    /// that prints what's wanted and fails anyway (NetBSD's disklabel on a
    /// disk without a label: it prints the made-up one).
    fn run_any(&self, prog: &str, args: &[&str]) -> Option<String> {
        self.run(prog, args)
    }
}

/// Runs real commands, from the system directories only (NixOS's
/// /run/current-system/sw among them), in the C locale,
/// with a time limit (a hung `zpool` must not hang the dump).
pub struct RealRunner {
    pub timeout: Duration,
}

/// NixOS keeps its system's tools (btrfs, lvm, zfs, efibootmgr...) only in
/// /run/current-system/sw: without it, a NixOS dump lists no btrfs
/// subvolumes, and load makes none of those its fstab mounts.
const SYSTEM_PATH: &str = "/usr/sbin:/usr/bin:/sbin:/bin:/run/current-system/sw/sbin:/run/current-system/sw/bin";

impl Runner for RealRunner {
    fn run(&self, prog: &str, args: &[&str]) -> Option<String> {
        if prog.contains('/') {
            return None;
        }
        let exe = SYSTEM_PATH.split(':').map(|d| Path::new(d).join(prog)).find(|p| p.is_file())?;
        self.exec(&exe, args)
    }

    fn run_file(&self, path: &str, args: &[&str]) -> Option<String> {
        let p = Path::new(path);
        if !p.is_absolute() || !p.is_file() {
            return None;
        }
        self.exec(p, args)
    }

    fn run_any(&self, prog: &str, args: &[&str]) -> Option<String> {
        if prog.contains('/') {
            return None;
        }
        let exe = SYSTEM_PATH.split(':').map(|d| Path::new(d).join(prog)).find(|p| p.is_file())?;
        self.exec_status(&exe, args).map(|(out, _)| out)
    }
}

impl RealRunner {
    fn exec(&self, exe: &Path, args: &[&str]) -> Option<String> {
        match self.exec_status(exe, args)? {
            (out, true) => Some(out),
            _ => None,
        }
    }

    /// Standard output, and whether it exited 0 (None: it couldn't run, or
    /// ran out of time).
    fn exec_status(&self, exe: &Path, args: &[&str]) -> Option<(String, bool)> {
        let mut child = Command::new(exe)
            .args(args)
            .env("PATH", SYSTEM_PATH)
            .env("LC_ALL", "C")
            .stdin(Stdio::null())
            .stdout(Stdio::piped())
            .stderr(Stdio::null())
            .spawn()
            .ok()?;
        // Read stdout on a thread so a chatty command can't block on a full
        // pipe while we wait for it.
        let mut stdout = child.stdout.take()?;
        let reader = std::thread::spawn(move || {
            let mut buf = Vec::new();
            let _ = stdout.read_to_end(&mut buf);
            buf
        });
        let start = Instant::now();
        let status = loop {
            match child.try_wait() {
                Ok(Some(s)) => break Some(s),
                Ok(None) if start.elapsed() < self.timeout => std::thread::sleep(Duration::from_millis(20)),
                _ => {
                    let _ = child.kill();
                    let _ = child.wait();
                    break None;
                }
            }
        };
        let out = reader.join().ok()?;
        let ok = status?.success();
        Some((String::from_utf8_lossy(&out).into_owned(), ok))
    }
}

/// Answers from a table, for tests: key "prog arg1 arg2".
#[derive(Default)]
pub struct FakeRunner {
    pub answers: HashMap<String, String>,
    pub calls: RefCell<Vec<String>>,
}

impl FakeRunner {
    pub fn with(mut self, cmd: &str, out: &str) -> FakeRunner {
        self.answers.insert(cmd.to_string(), out.to_string());
        self
    }
}

impl Runner for FakeRunner {
    fn run(&self, prog: &str, args: &[&str]) -> Option<String> {
        let key = std::iter::once(prog).chain(args.iter().copied()).collect::<Vec<_>>().join(" ");
        self.calls.borrow_mut().push(key.clone());
        self.answers.get(&key).cloned()
    }
}

pub struct Sys {
    root: PathBuf,
    runner: Box<dyn Runner>,
    /// Whether we may read block devices and root-only files (euid 0).
    pub privileged: bool,
    warnings: RefCell<Vec<String>>,
}

impl Sys {
    pub fn new(root: impl Into<PathBuf>, runner: Box<dyn Runner>, privileged: bool) -> Sys {
        Sys { root: root.into(), runner, privileged, warnings: RefCell::new(Vec::new()) }
    }

    /// The running system, as whoever we are.
    pub fn host() -> Sys {
        Sys::new("/", Box::new(RealRunner { timeout: Duration::from_secs(30) }), euid() == 0)
    }

    pub fn root(&self) -> &Path {
        &self.root
    }

    pub fn p(&self, abs: &str) -> PathBuf {
        self.root.join(abs.trim_start_matches('/'))
    }

    pub fn read_bytes(&self, abs: &str) -> Option<Vec<u8>> {
        std::fs::read(self.p(abs)).ok()
    }

    pub fn read(&self, abs: &str) -> Option<String> {
        self.read_bytes(abs).map(|b| String::from_utf8_lossy(&b).into_owned())
    }

    /// The file's text with surrounding white space removed; None if missing
    /// or empty.
    pub fn read_trim(&self, abs: &str) -> Option<String> {
        let s = self.read(abs)?;
        let s = s.trim();
        (!s.is_empty()).then(|| s.to_string())
    }

    pub fn read_u64(&self, abs: &str) -> Option<u64> {
        self.read_trim(abs)?.parse().ok()
    }

    /// Entry names in a directory, sorted ("naturally": sda2 before sda10).
    pub fn list(&self, abs: &str) -> Vec<String> {
        let mut v: Vec<String> = std::fs::read_dir(self.p(abs))
            .map(|rd| rd.filter_map(|e| e.ok()).map(|e| e.file_name().to_string_lossy().into_owned()).collect())
            .unwrap_or_default();
        v.sort_by(|a, b| natural_cmp(a, b));
        v
    }

    pub fn readlink(&self, abs: &str) -> Option<String> {
        std::fs::read_link(self.p(abs)).ok().map(|p| p.to_string_lossy().into_owned())
    }

    pub fn exists(&self, abs: &str) -> bool {
        self.p(abs).symlink_metadata().is_ok()
    }

    pub fn is_dir(&self, abs: &str) -> bool {
        self.p(abs).is_dir()
    }

    pub fn is_file(&self, abs: &str) -> bool {
        self.p(abs).is_file()
    }

    pub fn file_size(&self, abs: &str) -> Option<u64> {
        std::fs::metadata(self.p(abs)).ok().filter(|m| m.is_file()).map(|m| m.len())
    }

    /// A block device (or image) to read on-disk structures from.  Only when
    /// privileged: an unprivileged read would fail anyway, and would leave a
    /// warning per device.
    pub fn open_dev(&self, abs: &str) -> Option<FileSource> {
        if !self.privileged {
            return None;
        }
        match FileSource::open(&self.p(abs)) {
            // A BSD's raw disk device seeks to 0 for its end: its size
            // comes from the system's own tools.  The file already open
            // takes it: NetBSD refuses a second open of a block device
            // (/dev/ld1a) while the first is open, and the device would
            // then read as empty.
            Ok(f) if f.size() == 0 => Some(match crate::other::dev_size(self, abs) {
                Some(n) => f.with_size(n),
                None => f,
            }),
            Ok(f) => Some(f),
            Err(e) => {
                self.warn(format!("{abs}: {e}"));
                None
            }
        }
    }

    pub fn run(&self, prog: &str, args: &[&str]) -> Option<String> {
        self.runner.run(prog, args)
    }

    /// Runs a system tool and keeps its output even when it fails; see
    /// `Runner::run_any`.
    pub fn run_any(&self, prog: &str, args: &[&str]) -> Option<String> {
        self.runner.run_any(prog, args)
    }

    /// Runs the program at an absolute path; see `Runner::run_file`.
    pub fn run_file(&self, path: &str, args: &[&str]) -> Option<String> {
        self.runner.run_file(path, args)
    }

    pub fn warn(&self, msg: impl Into<String>) {
        let msg = msg.into();
        let mut w = self.warnings.borrow_mut();
        if !w.contains(&msg) {
            w.push(msg);
        }
    }

    pub fn warnings(&self) -> Vec<String> {
        self.warnings.borrow().clone()
    }

    /// Size, free and available bytes of the filesystem mounted at `abs`.
    pub fn statvfs(&self, abs: &str) -> Option<(u64, u64, u64)> {
        statvfs(&self.p(abs))
    }
}

/// Compares names with runs of digits as numbers: "sda2" < "sda10".
pub fn natural_cmp(a: &str, b: &str) -> std::cmp::Ordering {
    use std::cmp::Ordering;
    let (mut x, mut y) = (a.as_bytes(), b.as_bytes());
    loop {
        match (x.first(), y.first()) {
            (None, None) => return a.cmp(b),
            (None, _) => return Ordering::Less,
            (_, None) => return Ordering::Greater,
            (Some(c), Some(d)) if c.is_ascii_digit() && d.is_ascii_digit() => {
                let nx = x.iter().take_while(|c| c.is_ascii_digit()).count();
                let ny = y.iter().take_while(|c| c.is_ascii_digit()).count();
                let (dx, dy) = (&x[..nx], &y[..ny]);
                let tx = dx.iter().skip_while(|&&c| c == b'0').count();
                let ty = dy.iter().skip_while(|&&c| c == b'0').count();
                let o = tx.cmp(&ty).then_with(|| dx[nx - tx..].cmp(&dy[ny - ty..]));
                if o != Ordering::Equal {
                    return o;
                }
                x = &x[nx..];
                y = &y[ny..];
            }
            (Some(c), Some(d)) => {
                if c != d {
                    return c.cmp(d);
                }
                x = &x[1..];
                y = &y[1..];
            }
        }
    }
}

extern "C" {
    fn geteuid() -> u32;
}

pub fn euid() -> u32 {
    // SAFETY: geteuid has no preconditions and cannot fail.
    unsafe { geteuid() }
}

#[cfg(all(target_os = "linux", target_pointer_width = "64"))]
fn statvfs(path: &Path) -> Option<(u64, u64, u64)> {
    use std::ffi::CString;
    use std::os::raw::{c_char, c_int};
    use std::os::unix::ffi::OsStrExt;

    // glibc's struct statvfs on 64-bit Linux (unsigned long is 64 bits).
    #[repr(C)]
    struct StatVfs {
        f_bsize: u64,
        f_frsize: u64,
        f_blocks: u64,
        f_bfree: u64,
        f_bavail: u64,
        f_files: u64,
        f_ffree: u64,
        f_favail: u64,
        f_fsid: u64,
        f_flag: u64,
        f_namemax: u64,
        f_spare: [c_int; 6],
    }
    extern "C" {
        fn statvfs(path: *const c_char, buf: *mut StatVfs) -> c_int;
    }
    let c = CString::new(path.as_os_str().as_bytes()).ok()?;
    let mut buf = std::mem::MaybeUninit::<StatVfs>::zeroed();
    // SAFETY: c is a valid NUL-terminated path and buf is large enough for
    // glibc's struct statvfs, which statvfs fills on success.
    let rc = unsafe { statvfs(c.as_ptr(), buf.as_mut_ptr()) };
    if rc != 0 {
        return None;
    }
    // SAFETY: statvfs returned 0, so it initialised buf.
    let s = unsafe { buf.assume_init() };
    let unit = s.f_frsize;
    Some((s.f_blocks * unit, s.f_bfree * unit, s.f_bavail * unit))
}

#[cfg(not(all(target_os = "linux", target_pointer_width = "64")))]
fn statvfs(_: &Path) -> Option<(u64, u64, u64)> {
    None
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::testutil::Tree;
    use std::cmp::Ordering;

    #[test]
    fn natural_order() {
        let mut v = vec!["sda10", "sda2", "sda", "nvme0n1p10", "nvme0n1p9", "dm-11", "dm-2", "a01", "a1"];
        v.sort_by(|a, b| natural_cmp(a, b));
        assert_eq!(v, vec!["a01", "a1", "dm-2", "dm-11", "nvme0n1p9", "nvme0n1p10", "sda", "sda2", "sda10"]);
        assert_eq!(natural_cmp("x", "x"), Ordering::Equal);
        assert_eq!(natural_cmp("", "a"), Ordering::Less);
        assert_eq!(natural_cmp("b", ""), Ordering::Greater);
        assert_eq!(natural_cmp("a99999999999999999999999", "a100000000000000000000000"), Ordering::Less);
    }

    #[test]
    fn reads_under_root() {
        let t = Tree::new("sys");
        t.file("/a/b", "  hello \n").file("/n", "42\n").file("/empty", "\n").symlink("/l", "../x/target");
        let s = Sys::new(t.root(), Box::new(FakeRunner::default()), false);
        assert_eq!(s.read("/a/b").as_deref(), Some("  hello \n"));
        assert_eq!(s.read_trim("/a/b").as_deref(), Some("hello"));
        assert_eq!(s.read_trim("/empty"), None);
        assert_eq!(s.read_trim("/missing"), None);
        assert_eq!(s.read_u64("/n"), Some(42));
        assert_eq!(s.read_u64("/a/b"), None);
        assert_eq!(s.readlink("/l").as_deref(), Some("../x/target"));
        assert!(s.exists("/l"));
        assert!(!s.exists("/nope"));
        assert!(s.is_dir("/a"));
        assert!(s.is_file("/n"));
        assert_eq!(s.file_size("/n"), Some(3));
        assert_eq!(s.file_size("/a"), None);
        assert_eq!(s.list("/a"), vec!["b"]);
        assert!(s.list("/missing").is_empty());
    }

    #[test]
    fn open_dev_needs_privilege() {
        let t = Tree::new("sys-dev");
        t.file("/dev/sda", vec![1u8; 1024]);
        let unpriv = Sys::new(t.root(), Box::new(FakeRunner::default()), false);
        assert!(unpriv.open_dev("/dev/sda").is_none());
        assert!(unpriv.warnings().is_empty());
        let priv_ = Sys::new(t.root(), Box::new(FakeRunner::default()), true);
        assert!(priv_.open_dev("/dev/sda").is_some());
        assert!(priv_.open_dev("/dev/sdb").is_none());
        assert_eq!(priv_.warnings().len(), 1);
        assert!(priv_.warnings()[0].starts_with("/dev/sdb: "));
    }

    #[test]
    fn warnings_dedupe() {
        let s = Sys::new("/nonexistent", Box::new(FakeRunner::default()), false);
        s.warn("x");
        s.warn("x");
        s.warn("y");
        assert_eq!(s.warnings(), vec!["x", "y"]);
    }

    #[test]
    fn fake_runner_answers_and_records() {
        let r = FakeRunner::default().with("zpool list -H", "rpool\n");
        assert_eq!(r.run("zpool", &["list", "-H"]).as_deref(), Some("rpool\n"));
        assert_eq!(r.run("zpool", &["status"]), None);
        assert_eq!(*r.calls.borrow(), vec!["zpool list -H", "zpool status"]);
    }

    #[test]
    fn real_runner_runs_and_times_out() {
        let r = RealRunner { timeout: Duration::from_secs(5) };
        assert_eq!(r.run("echo", &["a", "b"]).as_deref(), Some("a b\n"));
        assert_eq!(r.run("false", &[]), None);
        assert_eq!(r.run("bko-no-such-program", &[]), None);
        // Only the system directories are searched.
        assert_eq!(r.run("../bin/echo", &["x"]), None);
        assert_eq!(r.run("/bin/echo", &["x"]), None);
        let quick = RealRunner { timeout: Duration::from_millis(200) };
        let t = Instant::now();
        assert_eq!(quick.run("sleep", &["10"]), None);
        assert!(t.elapsed() < Duration::from_secs(5));
        // A big output doesn't deadlock on the pipe.
        let big = r.run("head", &["-c", "1000000", "/dev/zero"]).unwrap();
        assert_eq!(big.len(), 1_000_000);
    }

    #[test]
    fn statvfs_of_root() {
        let s = Sys::new("/", Box::new(FakeRunner::default()), false);
        let (total, free, avail) = s.statvfs("/").expect("statvfs /");
        assert!(total > 0 && free <= total && avail <= free);
        assert_eq!(s.statvfs("/no/such/dir"), None);
    }
}
