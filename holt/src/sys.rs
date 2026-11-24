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

    /// Whether PROG is installed where `run` looks for it: `run` says None
    /// both for a tool that isn't there and for one that ran and failed,
    /// and a caller whose answer differs (no zpool on a system with ZFS
    /// mounted) asks this.  True unless the runner knows otherwise.
    fn installed(&self, _prog: &str) -> bool {
        true
    }

    /// How long a command may run before it counts as failed; `Sys` gives
    /// its in-process calls that can hang (statvfs on a dead NFS mount)
    /// the same limit.
    fn time_limit(&self) -> Duration {
        TIME_LIMIT
    }
}

/// The time limit of the running system's commands (`Sys::host`), and any
/// runner's that doesn't say.
pub const TIME_LIMIT: Duration = Duration::from_secs(30);

/// Runs real commands, from the system directories only (NixOS's
/// /run/current-system/sw and Guix's /run/current-system/profile among
/// them), in the C locale,
/// with a time limit (a hung `zpool` must not hang the dump).
pub struct RealRunner {
    pub timeout: Duration,
}

/// NixOS keeps its system's tools (btrfs, lvm, zfs, efibootmgr...) only in
/// /run/current-system/sw: without it, a NixOS dump lists no btrfs
/// subvolumes, and load makes none of those its fstab mounts.  Guix System
/// keeps them in /run/current-system/profile (crate::store::SYSTEMS names
/// both, and its test holds this list to them).
pub(crate) const SYSTEM_PATH: &str = "/usr/sbin:/usr/bin:/sbin:/bin:/run/current-system/sw/sbin:/run/current-system/sw/bin:/run/current-system/profile/sbin:/run/current-system/profile/bin";

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

    fn installed(&self, prog: &str) -> bool {
        !prog.contains('/') && SYSTEM_PATH.split(':').any(|d| Path::new(d).join(prog).is_file())
    }

    fn time_limit(&self) -> Duration {
        self.timeout
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
        // pipe while we wait for it, capped so a runaway tool cannot OOM:
        // a byte past the cap, so output cut short is known as such.
        let stdout = child.stdout.take()?;
        let (tx, rx) = std::sync::mpsc::channel();
        std::thread::spawn(move || {
            let mut buf = Vec::new();
            let _ = stdout.take(MAX_STDOUT + 1).read_to_end(&mut buf);
            let _ = tx.send(buf);
        });
        let start = Instant::now();
        let status = loop {
            match child.try_wait() {
                Ok(Some(s)) => break Some(s),
                Ok(None) if start.elapsed() < self.timeout => std::thread::sleep(Duration::from_millis(20)),
                _ => {
                    // A process asleep in the kernel outlives SIGKILL until
                    // it wakes, maybe never (illumos' diskinfo -Hp in the
                    // rescuer, cinqcent 2026-11-21: two hours, until the
                    // test's own time limit): waited for a moment only, and
                    // left behind.  Its reader thread is left too.
                    let _ = child.kill();
                    let gone = Instant::now();
                    while !matches!(child.try_wait(), Ok(Some(_))) && gone.elapsed() < KILL_GRACE {
                        std::thread::sleep(Duration::from_millis(20));
                    }
                    break None;
                }
            }
        };
        let ok = status?.success();
        // Its output ends when the pipe closes: a process it left in the
        // background with the pipe open (or one stuck as above) gets what's
        // left of the time limit, then the command counts as timed out.
        let out = rx.recv_timeout(self.timeout.saturating_sub(start.elapsed()).max(KILL_GRACE)).ok()?;
        // More than the cap: the command fails, rather than give a listing
        // cut short as if it were whole.
        if out.len() as u64 > MAX_STDOUT {
            return None;
        }
        Some((String::from_utf8_lossy(&out).into_owned(), ok))
    }
}

/// The most standard output `RealRunner` takes from a command: one that
/// prints more fails.
const MAX_STDOUT: u64 = 64 << 20;

/// The largest file `Sys::read_bytes` (and `read`) reads whole.
pub const READ_MAX: u64 = 128 << 20;

/// BUF filled from R as far as it goes; how much was read (less only at
/// the end).
fn fill(r: &mut impl Read, buf: &mut [u8]) -> std::io::Result<usize> {
    let mut n = 0;
    while n < buf.len() {
        match r.read(&mut buf[n..]) {
            Ok(0) => break,
            Ok(k) => n += k,
            Err(e) if e.kind() == std::io::ErrorKind::Interrupted => {}
            Err(e) => return Err(e),
        }
    }
    Ok(n)
}

/// How long a command killed for running out of time, or its output, is
/// waited for before `RealRunner` gives up on it.
const KILL_GRACE: Duration = Duration::from_secs(2);

/// Answers from a table, for tests: key "prog arg1 arg2".  A command it
/// has no answer for fails; a program named by `without` isn't installed.
#[derive(Default)]
pub struct FakeRunner {
    pub answers: HashMap<String, String>,
    pub calls: RefCell<Vec<String>>,
    pub missing: Vec<String>,
}

impl FakeRunner {
    pub fn with(mut self, cmd: &str, out: &str) -> FakeRunner {
        self.answers.insert(cmd.to_string(), out.to_string());
        self
    }

    /// PROG isn't installed: every run of it fails, and `installed` says so.
    pub fn without(mut self, prog: &str) -> FakeRunner {
        self.missing.push(prog.to_string());
        self
    }
}

impl Runner for FakeRunner {
    fn run(&self, prog: &str, args: &[&str]) -> Option<String> {
        let key = std::iter::once(prog).chain(args.iter().copied()).collect::<Vec<_>>().join(" ");
        self.calls.borrow_mut().push(key.clone());
        if !self.installed(prog) {
            return None;
        }
        self.answers.get(&key).cloned()
    }

    fn installed(&self, prog: &str) -> bool {
        !self.missing.iter().any(|m| m == prog)
    }
}

pub struct Sys {
    root: PathBuf,
    runner: Box<dyn Runner>,
    /// Whether we may read block devices and root-only files (euid 0).
    pub privileged: bool,
    warnings: RefCell<Vec<String>>,
    /// The kernel's time zone (sys_tz's tz_minuteswest, minutes west of
    /// UTC) when this is the running system: Linux's vfat reads and writes
    /// FAT's local-time stamps through it (crate::report records it, load
    /// mounts the FAT targets with it).  None for a --sysroot or a test.
    sys_tz: Option<i32>,
}

impl Sys {
    pub fn new(root: impl Into<PathBuf>, runner: Box<dyn Runner>, privileged: bool) -> Sys {
        Sys { root: root.into(), runner, privileged, warnings: RefCell::new(Vec::new()), sys_tz: None }
    }

    /// The running system, as whoever we are.
    pub fn host() -> Sys {
        let mut s = Sys::new("/", Box::new(RealRunner { timeout: TIME_LIMIT }), euid() == 0);
        s.sys_tz = kernel_sys_tz();
        s
    }

    /// As though the kernel's sys_tz were `minuteswest` (tests).
    pub fn with_sys_tz(mut self, minuteswest: Option<i32>) -> Sys {
        self.sys_tz = minuteswest;
        self
    }

    /// The running kernel's sys_tz minutes west of UTC (`hwclock --systz`
    /// sets it, Artix's dinit at every boot): None off Linux, for a
    /// --sysroot, or where it couldn't be read.
    pub fn sys_tz_minuteswest(&self) -> Option<i32> {
        self.sys_tz
    }

    pub fn root(&self) -> &Path {
        &self.root
    }

    pub fn p(&self, abs: &str) -> PathBuf {
        self.root.join(abs.trim_start_matches('/'))
    }

    /// The file's bytes; None if it can't be read.  One larger than
    /// READ_MAX isn't read either, and that's said in the dump's warnings
    /// rather than passed off as a missing file.
    pub fn read_bytes(&self, abs: &str) -> Option<Vec<u8>> {
        match crate::bytes::read_bounded(&self.p(abs), READ_MAX) {
            Ok(b) => Some(b),
            Err(e) if e.kind() == std::io::ErrorKind::InvalidData => {
                self.warn(format!("{abs}: larger than {READ_MAX} bytes, so not read"));
                None
            }
            Err(_) => None,
        }
    }

    /// Whether the files at A and B hold the same bytes, read a piece at a
    /// time whatever their size; None if either can't be read.
    pub fn same_bytes(&self, a: &str, b: &str) -> Option<bool> {
        let (mut fa, mut fb) = (std::fs::File::open(self.p(a)).ok()?, std::fs::File::open(self.p(b)).ok()?);
        if fa.metadata().ok()?.len() != fb.metadata().ok()?.len() {
            return Some(false);
        }
        let (mut ba, mut bb) = (vec![0u8; 1 << 20], vec![0u8; 1 << 20]);
        loop {
            let n = fill(&mut fa, &mut ba).ok()?;
            let m = fill(&mut fb, &mut bb).ok()?;
            if n != m || ba[..n] != bb[..m] {
                return Some(false);
            }
            if n == 0 {
                return Some(true);
            }
        }
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

    /// Whether a system tool `run` would run is installed; see
    /// `Runner::installed`.
    pub fn installed(&self, prog: &str) -> bool {
        self.runner.installed(prog)
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

    /// Size, free and available bytes of the filesystem mounted at `abs`,
    /// from statvfs(2) within the runner's time limit: a hard NFS mount to
    /// a dead server, or a hung FUSE daemon, can't hang the dump (it gets
    /// None and a warning instead).
    #[cfg(all(target_os = "linux", target_pointer_width = "64"))]
    pub fn statvfs(&self, abs: &str) -> Option<(u64, u64, u64)> {
        self.usage_within(abs, self.runner.time_limit(), statvfs)
    }

    /// `probe`'s answer for `abs` (under the root), asked on a thread of its
    /// own and waited for at most `limit`.  Past it: None, a warning naming
    /// the mount, and the thread left behind, asleep in the kernel, as
    /// `RealRunner` leaves a stuck child; it dies with the process.
    #[cfg_attr(not(all(target_os = "linux", target_pointer_width = "64")), allow(dead_code))]
    fn usage_within(&self, abs: &str, limit: Duration, probe: fn(&Path) -> Option<(u64, u64, u64)>) -> Option<(u64, u64, u64)> {
        let path = std::sync::Arc::new(self.p(abs));
        let (tx, rx) = std::sync::mpsc::channel();
        let theirs = std::sync::Arc::clone(&path);
        let spawned = std::thread::Builder::new().name("bko-holt-statvfs".into()).spawn(move || {
            let _ = tx.send(probe(&theirs));
        });
        let Ok(handle) = spawned else {
            // No thread to be had: asked here, with no time limit, as
            // before there was one.
            return probe(&path);
        };
        match rx.recv_timeout(limit) {
            Ok(usage) => usage,
            Err(std::sync::mpsc::RecvTimeoutError::Timeout) => {
                self.warn(format!("{abs}: no answer from statvfs in {} s (a hung NFS or FUSE mount?); its usage is left out", limit.as_secs()));
                None
            }
            // The probe panicked (a bug, not a slow mount): the panic goes
            // on here, as it did when the probe ran on this thread, and the
            // dump doesn't claim the mount didn't answer.
            Err(std::sync::mpsc::RecvTimeoutError::Disconnected) => match handle.join() {
                Err(panic) => std::panic::resume_unwind(panic),
                Ok(()) => None,
            },
        }
    }

    /// Size, free and available bytes of the filesystem mounted at `abs`:
    /// elsewhere (32-bit Linux), from `df -Pk`.
    #[cfg(not(all(target_os = "linux", target_pointer_width = "64")))]
    pub fn statvfs(&self, abs: &str) -> Option<(u64, u64, u64)> {
        self.df_usage(abs)
    }

    /// `df -Pk`'s size, free and available bytes for `abs`, run through the
    /// runner like every other tool: from the system directories, with its
    /// time limit, so a hung NFS or FUSE mount can't hang the dump.  No
    /// test of `path.exists()` first: that stat would hang on such a mount
    /// with no time limit, and df fails on a missing path anyway.
    #[cfg_attr(all(target_os = "linux", target_pointer_width = "64"), allow(dead_code))]
    fn df_usage(&self, abs: &str) -> Option<(u64, u64, u64)> {
        let path = self.p(abs);
        let out = self.run("df", &["-Pk", path.to_str()?])?;
        parse_df_pk(&out)
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

// SAFETY: geteuid(2) has this signature on every target holt builds for,
// takes no arguments, has no preconditions and cannot fail, so it is safe
// to call.
unsafe extern "C" {
    safe fn geteuid() -> u32;
}

pub fn euid() -> u32 {
    geteuid()
}

/// The gettimeofday(2) syscall's number, where holt is built for that
/// architecture (none on the others: sys_tz is then not read).
#[cfg(target_os = "linux")]
const SYS_GETTIMEOFDAY: Option<std::os::raw::c_long> = if cfg!(target_arch = "x86_64") {
    Some(96)
} else if cfg!(any(target_arch = "aarch64", target_arch = "riscv64")) {
    Some(169)
} else if cfg!(any(target_arch = "x86", target_arch = "arm")) {
    Some(78)
} else {
    None
};

/// The kernel's sys_tz.tz_minuteswest, by the raw gettimeofday syscall:
/// glibc (2.31 on) zeroes the timezone it's given and musl ignores it, so
/// libc's gettimeofday can't say.  None when the call fails or answers
/// what settimeofday(2) would refuse (beyond 15 hours).
#[cfg(target_os = "linux")]
fn kernel_sys_tz() -> Option<i32> {
    use std::os::raw::{c_int, c_long};
    #[repr(C)]
    struct Timezone {
        tz_minuteswest: c_int,
        tz_dsttime: c_int,
    }
    // SAFETY: syscall(2) is variadic, long-numbered and returns long in
    // every Linux libc.
    unsafe extern "C" {
        fn syscall(n: c_long, ...) -> c_long;
    }
    let nr = SYS_GETTIMEOFDAY?;
    let mut tz = Timezone { tz_minuteswest: 0, tz_dsttime: 0 };
    // SAFETY: gettimeofday(NULL, &tz): the kernel skips a NULL timeval and
    // writes only a struct timezone (two ints) through the second pointer,
    // which points at one that lives for the call.
    let rc = unsafe { syscall(nr, std::ptr::null_mut::<u8>(), &mut tz as *mut Timezone) };
    let _ = tz.tz_dsttime;
    (rc == 0 && tz.tz_minuteswest.abs() <= 15 * 60).then_some(tz.tz_minuteswest)
}

#[cfg(not(target_os = "linux"))]
fn kernel_sys_tz() -> Option<i32> {
    None
}

#[cfg(all(target_os = "linux", target_pointer_width = "64"))]
fn statvfs(path: &Path) -> Option<(u64, u64, u64)> {
    use std::ffi::CString;
    use std::os::raw::{c_char, c_int};
    use std::os::unix::ffi::OsStrExt;

    // 64-bit Linux struct statvfs (glibc and musl share the same 112-byte
    // prefix through f_spare/__f_spare; extra trailing padding guards against
    // any libc variant with additional reserved words).
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
        _pad: [u64; 4],
    }
    // SAFETY: the declaration matches libc's statvfs(3) on 64-bit Linux;
    // the call site upholds its pointer requirements.
    unsafe extern "C" {
        fn statvfs(path: *const c_char, buf: *mut StatVfs) -> c_int;
    }
    let c = CString::new(path.as_os_str().as_bytes()).ok()?;
    let mut buf = std::mem::MaybeUninit::<StatVfs>::zeroed();
    // SAFETY: c is a valid NUL-terminated path and buf is zero-initialized
    // and at least as large as 64-bit Linux libc's struct statvfs.
    let rc = unsafe { statvfs(c.as_ptr(), buf.as_mut_ptr()) };
    if rc != 0 {
        return None;
    }
    // SAFETY: buf was zero-initialized (valid for all integer fields) and
    // statvfs succeeded.
    let s = unsafe { buf.assume_init() };
    usage_bytes(s.f_blocks, s.f_bfree, s.f_bavail, s.f_frsize)
}

/// Size, free and available bytes from statvfs's block counts, or None
/// when they can't be a filesystem's: a product past u64, or more free or
/// available blocks than the filesystem has.  A filesystem being written
/// to hard or torn down can say so (bcachefs's free is capacity - used,
/// which underflows); a dump then records no usage for that mount rather
/// than a wrong one (load's fit reads it), and doesn't die on the overflow.
#[cfg_attr(not(all(target_os = "linux", target_pointer_width = "64")), allow(dead_code))]
fn usage_bytes(blocks: u64, bfree: u64, bavail: u64, frsize: u64) -> Option<(u64, u64, u64)> {
    if bfree > blocks || bavail > blocks {
        return None;
    }
    Some((blocks.checked_mul(frsize)?, bfree.checked_mul(frsize)?, bavail.checked_mul(frsize)?))
}

/// Size, free and available bytes from POSIX `df -Pk` output ("Filesystem
/// 1024-blocks Used Available Capacity Mounted on" and one line), or None
/// when it doesn't say or can't be a filesystem's.  The numbers are the
/// three before the first "NN%" column that follows three numbers, so a
/// filesystem name or mount point with spaces in it doesn't shift them.
#[cfg_attr(all(target_os = "linux", target_pointer_width = "64"), allow(dead_code))]
fn parse_df_pk(text: &str) -> Option<(u64, u64, u64)> {
    let body: Vec<&str> = text.lines().skip(1).flat_map(str::split_whitespace).collect();
    let i = (3..body.len()).find(|&i| {
        body[i].len() > 1 && body[i].ends_with('%') && body[i][..body[i].len() - 1].bytes().all(|b| b.is_ascii_digit()) && body[i - 3..i].iter().all(|c| c.parse::<u64>().is_ok())
    })?;
    let total_k: u64 = body[i - 3].parse().ok()?;
    let used_k: u64 = body[i - 2].parse().ok()?;
    let avail_k: u64 = body[i - 1].parse().ok()?;
    let total = total_k.checked_mul(1024)?;
    let avail = avail_k.checked_mul(1024)?;
    let free = total.saturating_sub(used_k.saturating_mul(1024)).max(avail);
    (avail <= free && free <= total).then_some((total, free, avail))
}

// ---- signals and limits, for --json's stop (holt-json.md, J7) -------------

/// SIGINT's, SIGKILL's and SIGTERM's numbers: the same on every target
/// holt builds for (Linux, the BSDs, illumos; Haiku's are 2 and 15 too, as
/// rescue-tui.md's RT1 read them from its headers, and its signal.h's
/// SIGKILL is 9).
pub const SIGINT: i32 = 2;
pub const SIGKILL: i32 = 9;
pub const SIGTERM: i32 = 15;

/// RLIMIT_CORE's number: 4 on Linux, the BSDs and illumos, 0 on Haiku.
const RLIMIT_CORE: std::os::raw::c_int = if cfg!(target_os = "haiku") { 0 } else { 4 };

/// Set by the SIGTERM handler catch_sigterm installs.
static SIGTERM_TAKEN: std::sync::atomic::AtomicBool = std::sync::atomic::AtomicBool::new(false);

// SAFETY: signal(2), kill(2) and setrlimit(2) have these signatures on
// every target holt builds for: a handler is pointer-sized (SIG_DFL is 0,
// SIG_ERR all ones), pid_t is a C int, and setrlimit reads one struct
// rlimit of two rlim_t through its pointer.
unsafe extern "C" {
    fn signal(sig: std::os::raw::c_int, handler: usize) -> usize;
    fn kill(pid: std::os::raw::c_int, sig: std::os::raw::c_int) -> std::os::raw::c_int;
    fn setrlimit(resource: std::os::raw::c_int, rlim: *const [u64; 2]) -> std::os::raw::c_int;
}

/// The SIGTERM handler: it only sets the flag, and puts itself back for a
/// platform whose signal(2) resets the handler on delivery (illumos's), so
/// a second SIGTERM is taken the same way.
extern "C" fn on_sigterm(_sig: std::os::raw::c_int) {
    SIGTERM_TAKEN.store(true, std::sync::atomic::Ordering::SeqCst);
    // SAFETY: signal(2) is async-signal-safe, and on_sigterm is a valid
    // handler for as long as the program runs.
    unsafe {
        signal(SIGTERM, on_sigterm as extern "C" fn(std::os::raw::c_int) as usize);
    }
}

/// Under --json: SIGTERM sets a flag (sigterm_taken) instead of ending the
/// process, so a writing run stops between commands.  Whether the handler
/// went in.
pub fn catch_sigterm() -> bool {
    // SAFETY: on_sigterm only stores to an atomic and calls signal(2), both
    // async-signal-safe; it lives for the whole program.
    let old = unsafe { signal(SIGTERM, on_sigterm as extern "C" fn(std::os::raw::c_int) as usize) };
    old != usize::MAX
}

/// Whether SIGTERM has come since catch_sigterm.
pub fn sigterm_taken() -> bool {
    SIGTERM_TAKEN.load(std::sync::atomic::Ordering::SeqCst)
}

/// SIGINT back to its default action, for a child about to exec (load's
/// pre_exec under --json): a load started where SIGINT is ignored (a
/// background job of a script) would otherwise pass that on, and the
/// stop's SIGINT would never reach the archiver.  Async-signal-safe.
pub fn sigint_to_default() {
    // SAFETY: signal(2) with SIG_DFL (0) is async-signal-safe and has no
    // other precondition.
    unsafe {
        signal(SIGINT, 0);
    }
}

/// Sends SIG to PID (a negative PID: that process group).  Whether it was
/// sent.
pub fn send_signal(pid: i32, sig: i32) -> bool {
    // SAFETY: kill(2) takes any pid and signal number and reports what it
    // can't do by its return value.
    unsafe { kill(pid, sig) == 0 }
}

/// Whether process PID is alive: kill(PID, 0) succeeds, or fails only for
/// want of permission.
pub fn process_alive(pid: u32) -> bool {
    let Ok(p) = i32::try_from(pid) else { return false };
    if p <= 0 {
        return false;
    }
    // SAFETY: as send_signal: signal 0 only checks the process is there.
    let rc = unsafe { kill(p, 0) };
    rc == 0 || std::io::Error::last_os_error().kind() == std::io::ErrorKind::PermissionDenied
}

/// When process PID started, as a string that is the same for as long as
/// that process lives and differs for a later one given the same PID: on
/// Linux /proc/PID/stat's starttime (clock ticks since boot), on illumos
/// /proc/PID/psinfo's pr_start (seconds and nanoseconds since the epoch:
/// ps's stime turns from a time into a date after a day), on the BSDs
/// ps's lstart, read in the C locale and UTC whatever load's environment
/// (FreeBSD's is strftime's %c, in the caller's locale and zone).  None
/// where none of them says (Haiku's ps has no -o).
pub fn process_start(pid: u32) -> Option<String> {
    if cfg!(target_os = "linux") {
        let stat = std::fs::read_to_string(format!("/proc/{pid}/stat")).ok()?;
        // After the command's name, which is in parentheses and may hold
        // spaces: state is the first field, starttime the 20th.
        return stat.rsplit_once(')')?.1.split_whitespace().nth(19).map(str::to_string);
    }
    if cfg!(any(target_os = "illumos", target_os = "solaris")) {
        return psinfo_start(&std::fs::read(format!("/proc/{pid}/psinfo")).ok()?, pid);
    }
    if cfg!(target_os = "haiku") {
        return None;
    }
    let mut ps = Command::new("ps");
    ps.args(["-o", "lstart=", "-p", &pid.to_string()]).env_clear().env("LC_ALL", "C").env("TZ", "UTC");
    if let Some(path) = std::env::var_os("PATH") {
        ps.env("PATH", path);
    }
    let out = ps.stdin(Stdio::null()).stderr(Stdio::null()).output().ok()?;
    let s = String::from_utf8_lossy(&out.stdout).trim().to_string();
    (out.status.success() && !s.is_empty()).then_some(s)
}

/// pr_start of an illumos psinfo_t BUF (LP64, the only illumos holt is
/// built for: pr_pid at byte 8, pr_start's tv_sec and tv_nsec at 88 and
/// 96), as "SECS.NANOS"; None unless pr_pid is PID and the time is one.
fn psinfo_start(buf: &[u8], pid: u32) -> Option<String> {
    let at = |o: usize, n: usize| buf.get(o..o + n);
    let i64_at = |o: usize| at(o, 8).and_then(|b| b.try_into().ok()).map(i64::from_ne_bytes);
    let pr_pid = at(8, 4).and_then(|b| b.try_into().ok()).map(i32::from_ne_bytes)?;
    let (secs, nanos) = (i64_at(88)?, i64_at(96)?);
    (i64::from(pid) == i64::from(pr_pid) && secs > 0 && (0..1_000_000_000).contains(&nanos)).then(|| format!("{secs}.{nanos:09}"))
}

/// When this system booted, where process_start can't tell (Haiku: its
/// system_time(), microseconds since boot): a lock written before it is
/// one whose pid has come round again.  None elsewhere.
#[cfg(target_os = "haiku")]
pub fn boot_time() -> Option<std::time::SystemTime> {
    // SAFETY: system_time() is libroot's (OS.h), takes nothing, has no
    // preconditions and returns a bigtime_t (int64) for every caller.
    unsafe extern "C" {
        safe fn system_time() -> i64;
    }
    std::time::SystemTime::now().checked_sub(Duration::from_micros(u64::try_from(system_time()).ok()?))
}

/// When this system booted, where process_start can't tell: only Haiku's.
#[cfg(not(target_os = "haiku"))]
pub fn boot_time() -> Option<std::time::SystemTime> {
    None
}

/// No core dumps for this process or its children (cryptsetup and the
/// archiver hold keys): RLIMIT_CORE's soft and hard limits 0.  Whether it
/// took.  Zeros whatever rlim_t's width: two of them fit in the 16 bytes
/// passed.
pub fn no_core_dumps() -> bool {
    let zero = [0u64; 2];
    // SAFETY: zero is 16 bytes, at least one struct rlimit on every target,
    // and outlives the call, which only reads it.
    unsafe { setrlimit(RLIMIT_CORE, &zero) == 0 }
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
        assert!(r.installed("zpool"));
        let r = FakeRunner::default().with("zpool list -H", "rpool\n").without("zpool");
        assert_eq!(r.run("zpool", &["list", "-H"]), None);
        assert!(!r.installed("zpool") && r.installed("zfs"));
    }

    #[test]
    fn real_runner_runs_and_times_out() {
        let r = RealRunner { timeout: Duration::from_secs(5) };
        assert_eq!(r.run("echo", &["a", "b"]).as_deref(), Some("a b\n"));
        assert_eq!(r.run("false", &[]), None);
        assert_eq!(r.run("bko-no-such-program", &[]), None);
        // A tool that ran and failed is installed; one that isn't there isn't.
        assert!(r.installed("false") && !r.installed("bko-no-such-program") && !r.installed("/bin/echo"));
        // Only the system directories are searched.
        assert_eq!(r.run("../bin/echo", &["x"]), None);
        assert_eq!(r.run("/bin/echo", &["x"]), None);
        let quick = RealRunner { timeout: Duration::from_millis(200) };
        let t = Instant::now();
        assert_eq!(quick.run("sleep", &["10"]), None);
        assert!(t.elapsed() < Duration::from_secs(5));
        // A process left holding its output open doesn't hold up the run
        // past the time limit (and a grace), whether the command ran out of
        // time or exited.
        let t = Instant::now();
        assert_eq!(quick.run("sh", &["-c", "sleep 9 & exec sleep 9"]), None);
        assert_eq!(quick.run("sh", &["-c", "sleep 9 & echo hi"]), None);
        assert!(t.elapsed() < Duration::from_secs(7), "{:?}", t.elapsed());
        // One that ends within the limit is waited for.
        assert_eq!(r.run("sh", &["-c", "(sleep 1; echo late) & echo early"]).as_deref(), Some("early\nlate\n"));
        // A big output doesn't deadlock on the pipe.
        let big = r.run("head", &["-c", "1000000", "/dev/zero"]).unwrap();
        assert_eq!(big.len(), 1_000_000);
        // Output past the cap fails, even when the command exits 0 (here
        // the part past the cap fits the pipe): never a listing cut short.
        let cap = MAX_STDOUT.to_string();
        assert_eq!(r.run("head", &["-c", &cap, "/dev/zero"]).map(|o| o.len() as u64), Some(MAX_STDOUT));
        let over = (MAX_STDOUT + 100).to_string();
        assert_eq!(r.run("head", &["-c", &over, "/dev/zero"]), None);
        assert_eq!(r.run_any("head", &["-c", &over, "/dev/zero"]), None);
    }

    /// A file over READ_MAX isn't read, and the dump says so; same_bytes
    /// compares files of any size.
    #[test]
    fn read_cap_warns_and_same_bytes() {
        let t = crate::testutil::Tree::new("sys-read-cap");
        let s = Sys::new(t.root(), Box::new(FakeRunner::default()), true);
        let big = t.root().join("big");
        std::fs::File::create(&big).unwrap().set_len(READ_MAX + 1).unwrap();
        assert_eq!(s.read_bytes("/big"), None);
        assert_eq!(s.read_bytes("/missing"), None);
        assert_eq!(s.warnings(), vec![format!("/big: larger than {READ_MAX} bytes, so not read")]);
        let twin = t.root().join("twin");
        std::fs::File::create(&twin).unwrap().set_len(READ_MAX + 1).unwrap();
        assert_eq!(s.same_bytes("/big", "/twin"), Some(true));
        t.file("/a", "abc").file("/b", "abd").file("/c", "abc");
        assert_eq!((s.same_bytes("/a", "/b"), s.same_bytes("/a", "/c"), s.same_bytes("/a", "/big"), s.same_bytes("/a", "/missing")), (Some(false), Some(true), Some(false), None));
    }

    #[test]
    fn usage_within_times_out_on_a_hung_mount() {
        // RustAudit1 HD4-02: a probe that sleeps past the limit (statvfs on
        // a hard NFS mount to a dead server) gives None and a warning
        // within the limit, not a hung dump.
        fn hung(_: &Path) -> Option<(u64, u64, u64)> {
            std::thread::sleep(Duration::from_secs(5));
            Some((1, 1, 1))
        }
        fn quick(p: &Path) -> Option<(u64, u64, u64)> {
            p.to_str().filter(|p| p.ends_with("/mnt/ok")).map(|_| (3, 2, 1))
        }
        let s = Sys::new("/fake-root", Box::new(FakeRunner::default()), false);
        let start = Instant::now();
        assert_eq!(s.usage_within("/mnt/nfs", Duration::from_millis(100), hung), None);
        assert!(start.elapsed() < Duration::from_secs(2), "waited {:?}", start.elapsed());
        assert_eq!(s.warnings().len(), 1);
        assert!(s.warnings()[0].starts_with("/mnt/nfs: no answer from statvfs"), "{:?}", s.warnings());
        // A probe that answers in time: its answer, under the root, and no
        // warning.
        let s = Sys::new("/fake-root", Box::new(FakeRunner::default()), false);
        assert_eq!(s.usage_within("/mnt/ok", Duration::from_secs(5), quick), Some((3, 2, 1)));
        assert_eq!(s.usage_within("/mnt/other", Duration::from_secs(5), quick), None);
        assert!(s.warnings().is_empty());
        assert_eq!(FakeRunner::default().time_limit(), TIME_LIMIT);
        assert_eq!(RealRunner { timeout: Duration::from_millis(7) }.time_limit(), Duration::from_millis(7));
    }

    #[test]
    fn usage_within_passes_a_probe_panic_on() {
        // A probe that panics is a bug: the panic reaches the caller, as
        // when the probe ran on the caller's thread, and no "no answer"
        // warning is made up for it.
        fn broken(_: &Path) -> Option<(u64, u64, u64)> {
            panic!("probe bug")
        }
        let s = Sys::new("/fake-root", Box::new(FakeRunner::default()), false);
        let start = Instant::now();
        let r = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| s.usage_within("/mnt/x", Duration::from_secs(5), broken)));
        let panic = r.expect_err("the probe's panic");
        assert_eq!(panic.downcast_ref::<&str>(), Some(&"probe bug"));
        assert!(start.elapsed() < Duration::from_secs(4), "waited {:?}", start.elapsed());
        assert!(s.warnings().is_empty(), "{:?}", s.warnings());
    }

    #[test]
    #[cfg(all(target_os = "linux", target_pointer_width = "64"))]
    fn statvfs_of_root() {
        let s = Sys::new("/", Box::new(FakeRunner::default()), false);
        let (total, free, avail) = s.statvfs("/").expect("statvfs /");
        assert!(total > 0 && free <= total && avail <= free);
        assert_eq!(s.statvfs("/no/such/dir"), None);
    }

    /// sys_tz: read off the running kernel for the host only, never for a
    /// --sysroot (Sys::new) unless a test says so.
    #[test]
    fn sys_tz_is_the_hosts_only() {
        assert_eq!(Sys::new("/", Box::new(FakeRunner::default()), false).sys_tz_minuteswest(), None);
        assert_eq!(Sys::new("/", Box::new(FakeRunner::default()), false).with_sys_tz(Some(-60)).sys_tz_minuteswest(), Some(-60));
        if cfg!(all(target_os = "linux", any(target_arch = "x86_64", target_arch = "aarch64"))) {
            let tz = kernel_sys_tz().expect("gettimeofday's timezone");
            assert!(tz.abs() <= 15 * 60, "{tz}");
            assert_eq!(Sys::host().sys_tz_minuteswest(), Some(tz));
        }
    }

    #[test]
    fn df_usage_runs_df_through_the_runner() {
        let out = "Filesystem 1024-blocks Used Available Capacity Mounted on\n/dev/ada0p2 1000 400 500 45% /mnt/data\n";
        let s = Sys::new("/", Box::new(FakeRunner::default().with("df -Pk /mnt/data", out)), false);
        assert_eq!(s.df_usage("/mnt/data"), Some((1_024_000, 614_400, 512_000)));
        // No answer (df failed, timed out, or isn't installed): no usage.
        assert_eq!(s.df_usage("/mnt/other"), None);
        let none = Sys::new("/", Box::new(FakeRunner::default().with("df -Pk /mnt/data", out).without("df")), false);
        assert_eq!(none.df_usage("/mnt/data"), None);
        // Under a test root, df is asked about the tree's own directory.
        let t = Tree::new("sys-df");
        t.file("/mnt/data/x", "");
        let path = t.root().join("mnt/data");
        let r = FakeRunner::default().with(&format!("df -Pk {}", path.display()), out);
        let rooted = Sys::new(t.root(), Box::new(r), false);
        assert_eq!(rooted.df_usage("/mnt/data"), Some((1_024_000, 614_400, 512_000)));
    }

    #[test]
    fn parse_df_pk_reads_posix_output() {
        let head = "Filesystem 1024-blocks Used Available Capacity Mounted on\n";
        assert_eq!(parse_df_pk(&format!("{head}/dev/ada0p2 1000 400 500 45% /")), Some((1_024_000, 614_400, 512_000)));
        // Spaces in the mount point and the filesystem's name.
        assert_eq!(parse_df_pk(&format!("{head}my pool/ds 1000 400 500 45% /mnt/my disk")), Some((1_024_000, 614_400, 512_000)));
        // A reserve: free (size - used) is more than available.
        assert_eq!(parse_df_pk(&format!("{head}/dev/sda1 1000 100 850 11% /")), Some((1_024_000, 921_600, 870_400)));
        // A long name on its own line (a df without -P's no-wrap).
        assert_eq!(parse_df_pk(&format!("{head}/dev/mapper/a-very-long-name\n 1000 400 500 45% /")), Some((1_024_000, 614_400, 512_000)));
        // No numbers, no data line, or numbers that can't be a filesystem's.
        assert_eq!(parse_df_pk(&format!("{head}proc - - - - /proc")), None);
        assert_eq!(parse_df_pk(head), None);
        assert_eq!(parse_df_pk(""), None);
        assert_eq!(parse_df_pk(&format!("{head}x 1000 400 2000 45% /")), None);
        assert_eq!(parse_df_pk(&format!("{head}x {} 0 0 0% /", u64::MAX)), None);
    }

    #[test]
    fn usage_bytes_refuses_impossible_counts() {
        assert_eq!(usage_bytes(100, 40, 30, 4096), Some((409_600, 163_840, 122_880)));
        assert_eq!(usage_bytes(0, 0, 0, 2 << 20), Some((0, 0, 0)));
        // Free past the size: an underflowed capacity - used.
        assert_eq!(usage_bytes(100, u64::MAX - 5, 30, 4096), None);
        assert_eq!(usage_bytes(100, 40, 101, 4096), None);
        // A size past u64 in bytes.
        assert_eq!(usage_bytes(1 << 60, 1, 1, 4096), None);
        assert_eq!(usage_bytes(u64::MAX / 4096, 0, 0, 4096), Some(((u64::MAX / 4096) * 4096, 0, 0)));
    }

    /// The environment variable sigterm_child runs under: only then does it
    /// install the handler and signal itself (in a process of its own, so
    /// the flag never reaches the other tests).
    const SIGTERM_CHILD: &str = "BKO_HOLT_TEST_SIGTERM_CHILD";

    /// In a child of its own (catch_sigterm_in_a_child's): the handler is
    /// installed, a SIGTERM to this process only sets the flag, twice, and
    /// the core limit goes to 0.
    #[test]
    fn sigterm_child() {
        if std::env::var_os(SIGTERM_CHILD).is_none() {
            return;
        }
        assert!(!sigterm_taken());
        assert!(catch_sigterm());
        let me = std::process::id() as i32;
        for _ in 0..2 {
            assert!(send_signal(me, SIGTERM));
            let t0 = Instant::now();
            while !sigterm_taken() && t0.elapsed() < Duration::from_secs(5) {
                std::thread::sleep(Duration::from_millis(10));
            }
            assert!(sigterm_taken());
        }
        assert!(no_core_dumps());
        println!("SIGTERM-CHILD-OK");
    }

    /// sigterm_child, run in a new process of this test binary.
    #[test]
    fn catch_sigterm_in_a_child() {
        let exe = std::env::current_exe().unwrap();
        let out = Command::new(exe)
            .args(["--exact", "sys::tests::sigterm_child", "--nocapture", "--test-threads", "1"])
            .env(SIGTERM_CHILD, "1")
            .stdin(Stdio::null())
            .output()
            .unwrap();
        let text = format!("{}{}", String::from_utf8_lossy(&out.stdout), String::from_utf8_lossy(&out.stderr));
        assert!(out.status.success() && text.contains("SIGTERM-CHILD-OK"), "{:?}: {text}", out.status);
    }

    #[test]
    fn processes_alive_and_started() {
        let me = std::process::id();
        assert!(process_alive(me));
        assert!(!process_alive(0));
        // A child that has exited and been waited for is gone.
        let mut c = Command::new("true").spawn().unwrap();
        let gone = c.id();
        c.wait().unwrap();
        assert!(!process_alive(gone) || process_start(gone).is_none());
        if cfg!(target_os = "linux") {
            let s = process_start(me).unwrap();
            assert!(s.bytes().all(|b| b.is_ascii_digit()), "{s}");
            assert_eq!(process_start(me), Some(s));
        }
        // Elsewhere ps's (the BSDs, and macOS where the tests run): the
        // same whatever load's own locale and zone.
        if !cfg!(any(target_os = "linux", target_os = "illumos", target_os = "solaris", target_os = "haiku")) {
            let s = process_start(me).unwrap();
            assert_eq!(process_start(me), Some(s));
        }
    }

    /// illumos's psinfo_t, LP64: pr_pid at 8, pr_start at 88.
    #[test]
    fn psinfo_starts() {
        let mut b = vec![0u8; 416];
        b[8..12].copy_from_slice(&4711i32.to_ne_bytes());
        b[88..96].copy_from_slice(&1_795_000_000i64.to_ne_bytes());
        b[96..104].copy_from_slice(&12_345i64.to_ne_bytes());
        assert_eq!(psinfo_start(&b, 4711).as_deref(), Some("1795000000.000012345"));
        // Another process's, a short read, no time.
        assert_eq!(psinfo_start(&b, 4712), None);
        assert_eq!(psinfo_start(&b[..100], 4711), None);
        b[96..104].copy_from_slice(&1_000_000_000i64.to_ne_bytes());
        assert_eq!(psinfo_start(&b, 4711), None);
        b[88..104].fill(0);
        assert_eq!(psinfo_start(&b, 4711), None);
        assert_eq!(boot_time().is_some(), cfg!(target_os = "haiku"));
    }
}
