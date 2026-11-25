//! What the app asks of the system, behind one trait so the screens'
//! tests run on a fake (`Sys`): the programs it starts (bko-holt,
//! rustic-bko, the keyboard tools, mount), the shell, the clock, files
//! it reads (zoneinfo), the network ports, and the session's private
//! directory in RAM.
//!
//! Every program gets a small, known environment (holt-json.md, Secrets
//! 8), never bko-rescue's own, standard input from /dev/null or a pipe
//! (never the terminal: nothing can prompt), its own process group, and
//! its standard output and error in files of the session's private
//! directory, read back capped once it exits.

use std::ffi::OsString;
use std::fs::{self, File, OpenOptions};
use std::io::{self, Read as _, Write as _};
use std::os::unix::fs::{DirBuilderExt as _, MetadataExt as _, OpenOptionsExt as _, PermissionsExt as _};
use std::os::unix::process::{CommandExt as _, ExitStatusExt as _};
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::time::{Duration, Instant};

use crate::secret::Secret;

/// The environment's variables a child may get from ours: only those the
/// pty tests' fakes read (`FAKE_BKO_HOLT_*`), which nothing on a stick
/// sets.
pub const PASS_PREFIX: &str = "FAKE_BKO_";

/// Set to 1 by the pty tests, whose repositories are fakes with no
/// server: every server counts as reachable.  Nothing on a stick sets it.
pub const ASSUME_REACHABLE: &str = "BKO_RESCUE_ASSUME_REACHABLE";

/// The PATH every child gets when ours is unset.
pub const DEFAULT_PATH: &str = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin";

/// A program to run.
pub struct Cmd {
    pub prog: PathBuf,
    pub args: Vec<OsString>,
    /// Added to the small environment (PATH, LC_ALL=C).
    pub env: Vec<(String, String)>,
    /// Bytes for its standard input through a pipe (the recovery words);
    /// None is /dev/null.  Kept in a `Secret`, wiped after the write.
    pub stdin: Option<Secret>,
    /// The most of its standard output kept; past it the run is
    /// `Output::too_big`.
    pub stdout_max: usize,
    /// Killed after this long.
    pub timeout_ms: u64,
}

impl Cmd {
    pub fn new(prog: &Path, args: Vec<OsString>) -> Cmd {
        Cmd { prog: prog.to_path_buf(), args, env: Vec::new(), stdin: None, stdout_max: 64 * 1024, timeout_ms: 120_000 }
    }

    /// The command line, for raw output and tests: the program and its
    /// arguments as given (never a secret: those go in files).
    pub fn line(&self) -> String {
        let mut v = vec![self.prog.display().to_string()];
        v.extend(self.args.iter().map(|a| a.to_string_lossy().to_string()));
        v.join(" ")
    }
}

/// How a program ended.
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct Output {
    /// Its exit status; None when a signal ended it, or it couldn't start.
    pub code: Option<i32>,
    pub stdout: Vec<u8>,
    /// Its last 64 KiB.
    pub stderr: Vec<u8>,
    /// Standard output was over `stdout_max`: `stdout` is empty then.
    pub too_big: bool,
    pub timed_out: bool,
    /// It couldn't be started: why.
    pub not_started: Option<String>,
}

impl Output {
    pub fn failed_to_start(why: String) -> Output {
        Output { not_started: Some(why), ..Output::default() }
    }

    pub fn stderr_text(&self) -> String {
        String::from_utf8_lossy(&self.stderr).to_string()
    }
}

/// A program running.
pub trait Job {
    /// How it ended, once it has.
    fn poll(&mut self) -> Option<Output>;
    fn kill(&mut self);
}

/// A network port (rescue-screens.md, "2. Network").
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Port {
    /// The kernel's name (`enp3s0`).
    pub name: String,
    /// A cable is in: None when the system can't tell.
    pub carrier: Option<bool>,
    pub has_address: Option<bool>,
}

pub trait Sys {
    fn start(&mut self, c: Cmd) -> Box<dyn Job>;
    /// A shell on the terminal, until it exits.  The full screen has
    /// handed the terminal back first.
    fn shell(&mut self);
    /// Now, in seconds since 1970 (UTC).
    fn now(&self) -> i64;
    /// A file's bytes, at most `max` (zoneinfo, a report to view).
    fn read(&self, path: &Path, max: usize) -> Option<Vec<u8>>;
    /// A program on PATH, as an absolute path.
    fn which(&self, name: &str) -> Option<PathBuf>;
    fn ports(&self) -> Option<Vec<Port>>;
    /// Whether HOST:PORT takes a TCP connection within `ms`.
    fn reachable(&mut self, host: &str, port: u16, ms: u64) -> bool;
    /// Where bko-rescue keeps its files in RAM: `/run/bko-rescue`.
    fn run_base(&self) -> PathBuf;
    /// Wait a little (a gauge's step).
    fn sleep_ms(&mut self, ms: u64);
}

/// The real system.
pub struct RealSys {
    pub base: PathBuf,
    /// Where each job's output files go: the session's directory.
    pub scratch: PathBuf,
    n: u32,
}

impl RealSys {
    pub fn new(base: PathBuf, scratch: PathBuf) -> RealSys {
        RealSys { base, scratch, n: 0 }
    }
}

/// The small environment: PATH (ours, or DEFAULT_PATH), LC_ALL=C, the
/// fakes' variables, then the command's own.
pub fn small_env(extra: &[(String, String)]) -> Vec<(String, String)> {
    let path = std::env::var("PATH").ok().filter(|p| !p.is_empty()).unwrap_or_else(|| DEFAULT_PATH.to_string());
    let mut v = vec![("PATH".to_string(), path), ("LC_ALL".to_string(), "C".to_string())];
    for (k, val) in std::env::vars() {
        if k.starts_with(PASS_PREFIX) {
            v.push((k, val));
        }
    }
    v.extend(extra.iter().cloned());
    v
}

struct RealJob {
    child: Option<Child>,
    out: PathBuf,
    err: PathBuf,
    max: usize,
    started: Instant,
    timeout: Duration,
    timed_out: bool,
    done: Option<Output>,
}

/// The last `max` bytes of a file, or the first `max` and whether there
/// was more.
fn read_capped(path: &Path, max: usize, tail: bool) -> (Vec<u8>, bool) {
    let Ok(mut f) = File::open(path) else { return (Vec::new(), false) };
    let len = f.metadata().map(|m| m.len()).unwrap_or(0) as usize;
    if tail && len > max {
        use std::io::{Seek, SeekFrom};
        let _ = f.seek(SeekFrom::Start((len - max) as u64));
    }
    let mut b = Vec::new();
    let _ = (&mut f).take(max as u64 + 1).read_to_end(&mut b);
    let over = b.len() > max;
    b.truncate(max);
    (b, over)
}

impl RealJob {
    fn finish(&mut self, code: Option<i32>) -> Output {
        let (stdout, over) = read_capped(&self.out, self.max, false);
        let (stderr, _) = read_capped(&self.err, 64 * 1024, true);
        for p in [&self.out, &self.err] {
            wipe_file(p);
        }
        Output {
            code,
            stdout: if over { Vec::new() } else { stdout },
            stderr,
            too_big: over,
            timed_out: self.timed_out,
            not_started: None,
        }
    }
}

impl Job for RealJob {
    fn poll(&mut self) -> Option<Output> {
        if let Some(o) = &self.done {
            return Some(o.clone());
        }
        let c = self.child.as_mut()?;
        match c.try_wait() {
            Ok(Some(st)) => {
                self.child = None;
                let o = self.finish(st.code());
                self.done = Some(o.clone());
                Some(o)
            }
            Ok(None) if self.started.elapsed() >= self.timeout => {
                self.timed_out = true;
                self.kill();
                self.done.clone()
            }
            Ok(None) => None,
            Err(_) => {
                self.kill();
                self.done.clone()
            }
        }
    }

    fn kill(&mut self) {
        if let Some(mut c) = self.child.take() {
            let _ = c.kill();
            let st = c.wait().ok();
            let code = st.and_then(|s| if s.signal().is_some() { None } else { s.code() });
            let o = self.finish(code);
            self.done = Some(o);
        }
    }
}

impl Drop for RealJob {
    fn drop(&mut self) {
        self.kill();
        for p in [&self.out, &self.err] {
            wipe_file(p);
        }
    }
}

struct Done(Output);

impl Job for Done {
    fn poll(&mut self) -> Option<Output> {
        Some(self.0.clone())
    }
    fn kill(&mut self) {}
}

impl Sys for RealSys {
    fn start(&mut self, c: Cmd) -> Box<dyn Job> {
        self.n += 1;
        let out = self.scratch.join(format!("out-{}", self.n));
        let err = self.scratch.join(format!("err-{}", self.n));
        let made = || -> io::Result<(File, File)> {
            let o = OpenOptions::new().write(true).create_new(true).mode(0o600).open(&out)?;
            let e = OpenOptions::new().write(true).create_new(true).mode(0o600).open(&err)?;
            Ok((o, e))
        };
        let (o, e) = match made() {
            Ok(x) => x,
            Err(e) => return Box::new(Done(Output::failed_to_start(format!("its output files: {e}")))),
        };
        let mut cmd = Command::new(&c.prog);
        cmd.args(&c.args)
            .env_clear()
            .envs(small_env(&c.env))
            .stdin(if c.stdin.is_some() { Stdio::piped() } else { Stdio::null() })
            .stdout(o)
            .stderr(e)
            .process_group(0);
        let mut child = match cmd.spawn() {
            Ok(ch) => ch,
            Err(e) => {
                wipe_file(&out);
                wipe_file(&err);
                return Box::new(Done(Output::failed_to_start(e.to_string())));
            }
        };
        if let (Some(s), Some(mut pipe)) = (c.stdin.as_ref(), child.stdin.take()) {
            // At most 1 KiB: the pipe takes it whole.
            let _ = pipe.write_all(s.expose());
        }
        drop(c.stdin);
        Box::new(RealJob {
            child: Some(child),
            out,
            err,
            max: c.stdout_max,
            started: Instant::now(),
            timeout: Duration::from_millis(c.timeout_ms),
            timed_out: false,
            done: None,
        })
    }

    fn shell(&mut self) {
        let sh = ["/bin/sh", "/system/bin/sh"].into_iter().find(|p| Path::new(p).exists()).unwrap_or("/bin/sh");
        {
            use std::io::Write as _;
            let mut o = std::io::stdout().lock();
            let _ = o.write_all(b"A shell.  Type exit to go back to bko-rescue.\n");
            let _ = o.flush();
        }
        let _ = Command::new(sh).arg("-i").status();
    }

    fn now(&self) -> i64 {
        std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).map(|d| d.as_secs() as i64).unwrap_or(0)
    }

    fn read(&self, path: &Path, max: usize) -> Option<Vec<u8>> {
        let (b, over) = read_capped(path, max, false);
        (!over && fs::metadata(path).is_ok()).then_some(b)
    }

    fn which(&self, name: &str) -> Option<PathBuf> {
        which(name)
    }

    fn ports(&self) -> Option<Vec<Port>> {
        linux_ports()
    }

    fn reachable(&mut self, host: &str, port: u16, ms: u64) -> bool {
        // The pty tests' servers are fakes with no network behind them.
        if std::env::var_os(ASSUME_REACHABLE).is_some_and(|v| v == "1") {
            return true;
        }
        reachable(host, port, ms)
    }

    fn run_base(&self) -> PathBuf {
        self.base.clone()
    }

    fn sleep_ms(&mut self, ms: u64) {
        std::thread::sleep(Duration::from_millis(ms));
    }
}

/// `name` on PATH (or the default PATH), as an absolute path; a name
/// with a `/` is taken as it is, made absolute.
pub fn which(name: &str) -> Option<PathBuf> {
    if name.contains('/') {
        let p = Path::new(name);
        let abs = if p.is_absolute() { p.to_path_buf() } else { std::env::current_dir().ok()?.join(p) };
        return is_exe(&abs).then_some(abs);
    }
    let path = std::env::var("PATH").ok().filter(|p| !p.is_empty()).unwrap_or_else(|| DEFAULT_PATH.to_string());
    path.split(':').filter(|d| d.starts_with('/')).map(|d| Path::new(d).join(name)).find(|p| is_exe(p))
}

fn is_exe(p: &Path) -> bool {
    fs::metadata(p).map(|m| m.is_file() && m.permissions().mode() & 0o111 != 0).unwrap_or(false)
}

/// Linux's wired ports, from /sys/class/net: not the loopback, not
/// Wi-Fi (the images are wired only), not virtual.  Elsewhere None: the
/// screen says the ports can't be listed.
fn linux_ports() -> Option<Vec<Port>> {
    if !cfg!(target_os = "linux") {
        return None;
    }
    let mut v = Vec::new();
    let rd = fs::read_dir("/sys/class/net").ok()?;
    let mut names: Vec<String> = rd.filter_map(|e| e.ok()).map(|e| e.file_name().to_string_lossy().to_string()).collect();
    names.sort();
    for n in names {
        let d = Path::new("/sys/class/net").join(&n);
        if n == "lo" || d.join("wireless").exists() || d.join("phy80211").exists() || !d.join("device").exists() {
            continue;
        }
        let carrier = fs::read_to_string(d.join("carrier")).ok().map(|s| s.trim() == "1");
        let has_address = Some(ipv4_on(&n));
        v.push(Port { name: n, carrier, has_address });
    }
    Some(v)
}

/// Whether the port has a route of its own in /proc/net/route (a DHCP
/// lease gives one).
fn ipv4_on(name: &str) -> bool {
    fs::read_to_string("/proc/net/route").map(|s| s.lines().skip(1).any(|l| l.split_whitespace().next() == Some(name))).unwrap_or(false)
}

/// A TCP connection to HOST:PORT within `ms`, the name looked up on a
/// thread of its own so a resolver that hangs can't hold the screen.
pub fn reachable(host: &str, port: u16, ms: u64) -> bool {
    use std::net::{TcpStream, ToSocketAddrs};
    let (tx, rx) = std::sync::mpsc::channel();
    let h = host.to_string();
    std::thread::spawn(move || {
        let addrs: Vec<_> = (h.as_str(), port).to_socket_addrs().map(|a| a.collect()).unwrap_or_default();
        let ok = addrs.iter().any(|a| TcpStream::connect_timeout(a, Duration::from_millis(ms.max(100))).is_ok());
        let _ = tx.send(ok);
    });
    rx.recv_timeout(Duration::from_millis(ms.saturating_mul(2).max(500))).unwrap_or(false)
}

/// Overwrites PATH with zeros to its length, then unlinks it
/// (holt-json.md, Secrets 6).  Only a regular file (lstat) is
/// overwritten: anything else in its place is only unlinked.
pub fn wipe_file(path: &Path) {
    if let Ok(m) = fs::symlink_metadata(path) {
        if m.is_file() {
            if let Ok(mut f) = OpenOptions::new().write(true).open(path) {
                let zeros = [0u8; 4096];
                let mut left = m.len();
                while left > 0 {
                    let n = left.min(zeros.len() as u64) as usize;
                    if f.write_all(&zeros[..n]).is_err() {
                        break;
                    }
                    left -= n as u64;
                }
                let _ = f.sync_data();
            }
        }
    }
    let _ = fs::remove_file(path);
}

/// A directory of ours with no group or other bits, made 0700 if it
/// isn't there: a real directory (not a symlink), owned by `uid` when
/// given.
pub fn own_dir(dir: &Path, uid: Option<u32>) -> io::Result<()> {
    match fs::DirBuilder::new().mode(0o700).create(dir) {
        Ok(()) => fs::set_permissions(dir, fs::Permissions::from_mode(0o700))?,
        Err(e) if e.kind() == io::ErrorKind::AlreadyExists => {}
        Err(e) => return Err(e),
    }
    let m = fs::symlink_metadata(dir)?;
    if !m.is_dir() {
        return Err(io::Error::other(format!("{} is not a directory", dir.display())));
    }
    if m.permissions().mode() & 0o077 != 0 {
        return Err(io::Error::other(format!("{} can be read by others (mode {:o})", dir.display(), m.permissions().mode() & 0o777)));
    }
    if let Some(u) = uid {
        if m.uid() != u {
            return Err(io::Error::other(format!("{} isn't bko-rescue's own (owner {})", dir.display(), m.uid())));
        }
    }
    Ok(())
}

/// The session's private directory: a new one under `base`, made here
/// with mode 0700 (never one that was there already), `base` itself
/// checked to be ours with no group or other bits.  Every file in it is
/// wiped and the directory removed when it drops: on Done, the shell's
/// exit, a caught signal or a panic's unwinding.
pub struct Session {
    pub dir: PathBuf,
}

impl Session {
    pub fn new(base: &Path) -> io::Result<Session> {
        own_dir(base, None)?;
        let pid = std::process::id();
        for i in 0..1000 {
            let d = base.join(format!("s-{pid}-{i}"));
            match fs::DirBuilder::new().mode(0o700).create(&d) {
                Ok(()) => {
                    fs::set_permissions(&d, fs::Permissions::from_mode(0o700))?;
                    let me = fs::symlink_metadata(&d)?.uid();
                    // The base is ours too: made by us, or root's /run
                    // made it for root, who we are.
                    let s = Session { dir: d };
                    own_dir(base, Some(me))?;
                    return Ok(s);
                }
                Err(e) if e.kind() == io::ErrorKind::AlreadyExists => continue,
                Err(e) => return Err(e),
            }
        }
        Err(io::Error::other("no free name for bko-rescue's directory"))
    }

    /// A path in the directory for a new file (not made).
    pub fn path(&self, name: &str) -> PathBuf {
        self.dir.join(name)
    }

    /// A secret written to a new 0600 file `name` (no newline), the file
    /// wiped first if one of ours is there (a password typed again).
    pub fn secret_file(&self, name: &str, s: &Secret) -> io::Result<PathBuf> {
        let p = self.path(name);
        wipe_file(&p);
        s.write_file(&self.dir, name)
    }

    /// A file of strings, one a line, for `report-bug --strip-file`.
    pub fn strip_file(&self, lines: &[String]) -> io::Result<PathBuf> {
        let p = self.path("strip");
        wipe_file(&p);
        let mut f = OpenOptions::new().write(true).create_new(true).mode(0o600).open(&p)?;
        for l in lines {
            let one: String = l.chars().filter(|c| !c.is_control()).collect();
            f.write_all(one.as_bytes())?;
            f.write_all(b"\n")?;
        }
        Ok(p)
    }
}

impl Drop for Session {
    fn drop(&mut self) {
        if let Ok(rd) = fs::read_dir(&self.dir) {
            for e in rd.flatten() {
                let p = e.path();
                match fs::symlink_metadata(&p) {
                    Ok(m) if m.is_dir() => {
                        // A mount point left (a report's): only an empty
                        // one goes.
                        let _ = fs::remove_dir(&p);
                    }
                    _ => wipe_file(&p),
                }
            }
        }
        let _ = fs::remove_dir(&self.dir);
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn scratch(name: &str) -> PathBuf {
        let d = std::env::temp_dir().join(format!("bko-rescue-sys-{name}-{}", std::process::id()));
        let _ = fs::remove_dir_all(&d);
        fs::DirBuilder::new().mode(0o700).create(&d).unwrap();
        d
    }

    #[test]
    fn a_session_is_new_private_and_wiped() {
        let base = scratch("session");
        let s = Session::new(&base).unwrap();
        let m = fs::metadata(&s.dir).unwrap();
        assert_eq!(m.permissions().mode() & 0o777, 0o700);
        let p = s.secret_file("password", &Secret::from_bytes(b"hunter2")).unwrap();
        assert_eq!(fs::read(&p).unwrap(), b"hunter2");
        assert_eq!(fs::metadata(&p).unwrap().permissions().mode() & 0o777, 0o600);
        // Typed again: the old one wiped, a new one made.
        let p2 = s.secret_file("password", &Secret::from_bytes(b"x")).unwrap();
        assert_eq!((p.clone(), fs::read(&p2).unwrap()), (p2, b"x".to_vec()));
        let dir = s.dir.clone();
        drop(s);
        assert!(!dir.exists(), "removed with every file");
        // A base others can read is refused.
        fs::set_permissions(&base, fs::Permissions::from_mode(0o755)).unwrap();
        assert!(Session::new(&base).is_err());
        let _ = fs::remove_dir_all(&base);
    }

    #[test]
    fn a_job_runs_with_the_small_environment_and_its_output_capped() {
        let base = scratch("job");
        let mut sys = RealSys::new(base.clone(), base.clone());
        std::env::set_var("BKO_RESCUE_TEST_SECRET", "leak");
        let mut c = Cmd::new(Path::new("/bin/sh"), vec!["-c".into(), "env; echo oops >&2; exit 3".into()]);
        c.env.push(("BKO_HOLT_NO_COMMIT".into(), "1".into()));
        let mut j = sys.start(c);
        let o = loop {
            if let Some(o) = j.poll() {
                break o;
            }
            std::thread::sleep(Duration::from_millis(10));
        };
        let out = String::from_utf8_lossy(&o.stdout).to_string();
        assert_eq!(o.code, Some(3));
        assert!(out.contains("BKO_HOLT_NO_COMMIT=1") && out.contains("LC_ALL=C"), "{out}");
        assert!(!out.contains("BKO_RESCUE_TEST_SECRET"), "never our own environment: {out}");
        assert_eq!(o.stderr_text(), "oops\n");
        let mut c = Cmd::new(Path::new("/bin/sh"), vec!["-c".into(), "yes | head -c 100000".into()]);
        c.stdout_max = 1000;
        let mut j = sys.start(c);
        let o = loop {
            if let Some(o) = j.poll() {
                break o;
            }
            std::thread::sleep(Duration::from_millis(10));
        };
        assert!(o.too_big && o.stdout.is_empty());
        let mut c = Cmd::new(Path::new("/bin/sh"), vec!["-c".into(), "cat".into()]);
        c.stdin = Some(Secret::from_bytes(b"words on a pipe"));
        let mut j = sys.start(c);
        let o = loop {
            if let Some(o) = j.poll() {
                break o;
            }
            std::thread::sleep(Duration::from_millis(10));
        };
        assert_eq!(o.stdout, b"words on a pipe");
        let mut c = Cmd::new(Path::new("/bin/sh"), vec!["-c".into(), "sleep 30".into()]);
        c.timeout_ms = 100;
        let mut j = sys.start(c);
        let o = loop {
            if let Some(o) = j.poll() {
                break o;
            }
            std::thread::sleep(Duration::from_millis(10));
        };
        assert!(o.timed_out && o.code.is_none());
        assert_eq!(fs::read_dir(&base).unwrap().count(), 0, "every output file gone");
        let _ = fs::remove_dir_all(&base);
    }
}
