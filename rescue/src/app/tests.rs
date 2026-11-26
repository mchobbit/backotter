//! The app's flows on the scripted Ui and a fake system (rescue-tui.md,
//! "Testing", App logic): each screen's way on, Back from each, every
//! failure path here, the secrets' files, the dry-run lock; and golden
//! screens of each screen at 80x24 (`tests/screens/app-*.txt`) and plain
//! mode's transcripts (`tests/screens/app-plain-*.txt`), under
//! BKO_RESCUE_GOLDEN as the widgets' goldens are.
//!
//! The fake system answers from testvm/rescue's fixtures: bko-holt from
//! a scenario's recorded calls (`scenarios/stick-*`), rustic-bko from
//! `rustic/NAME` (make-app-fixtures.py), so the pty runs and these see
//! the same documents.

use std::cell::RefCell;
use std::collections::VecDeque;
use std::fs;
use std::os::unix::fs::{DirBuilderExt as _, PermissionsExt as _};
use std::path::{Path, PathBuf};
use std::rc::Rc;

use super::*;
use crate::plain::tests::FakeLine;
use crate::plain::Plain;
use crate::screen::Canvas;
use crate::term::out::Lines;
use crate::testkit::golden;
use crate::ui::{Reply, Scripted};
use crate::widgets::Dialog;

const FIX: &str = concat!(env!("CARGO_MANIFEST_DIR"), "/../testvm/rescue");
const PASSWORD: &[u8] = b"correct horse";
const WORDS: &[u8] = b"legal winner thank year wave sausage worth useful legal winner thank yellow";

fn fixture(rel: &str) -> Vec<u8> {
    let p = format!("{FIX}/{rel}");
    fs::read(&p).unwrap_or_else(|e| panic!("{p}: {e}"))
}

/// A Restore's load: its command line, its environment's additions, how
/// it was started.
type Started = (String, Vec<(String, String)>, sys::How);

/// A command line, its environment's additions, its stdin.
type Ran = (String, Vec<(String, String)>, Option<Vec<u8>>);

/// What the fake system saw.
#[derive(Default)]
struct Seen {
    ran: Vec<Ran>,
    shells: usize,
    /// The password files rustic-bko was given, with their mode.
    password_files: Vec<(PathBuf, u32)>,
    reachable: VecDeque<bool>,
    layouts: Vec<String>,
    /// bko-holt isn't found for this many lookups (then it is: the
    /// shell fixed PATH).
    holt_hidden: usize,
    /// The scenario calls that answered `load`, in order.
    loads: Vec<String>,
    /// The Restore's loads: (command line, its environment, how), in order.
    started: Vec<Started>,
    /// The pids SIGTERM went to.
    terms: Vec<u32>,
    /// The pid of the load started last, from its events' start.
    load_pid: Option<u64>,
    /// bko-holt's lock, when not the load's own (a lock naming another).
    lock: Option<String>,
    /// Paths that are there (the stick's partition).
    exists: Vec<PathBuf>,
    /// Restarts and power offs run.
    offs: Vec<String>,
    /// Each load's --luks-header-dir, in order (None: it had none).
    header_dirs: Vec<Option<String>>,
}

struct Fake {
    base: PathBuf,
    holt: String,
    /// bko-holt's answers to --version and stick, when not the scenario's.
    version: Option<String>,
    stick: Option<String>,
    rustic: String,
    /// rustic-bko's `cat config`, when not the fixture's.
    config: Option<Vec<u8>>,
    seen: Rc<RefCell<Seen>>,
    tools: Vec<&'static str>,
    /// The scenario bko-holt's `load` calls are answered from (RT12), by
    /// their argv as fake-bko-holt answers them, and its `stick --copy`
    /// (the layout, written to OUT).
    disks: String,
    /// Calls answered in turn, as fake-bko-holt's FAKE_BKO_HOLT_STATE does:
    /// the first matching one not answered yet, then the last again.
    in_turn: bool,
    /// `stick --copy`'s exit and document, when not the scenario's (its
    /// OUT is still written, with stick-layouts' dump).
    copy: Option<(i32, String)>,
    /// `luks-headers`' answer: these files written to DIR as bko-holt
    /// writes them, and its document; or an exit and a document of its
    /// own (DIR not made).
    luks: Option<Luks>,
}

#[derive(Clone)]
enum Luks {
    Files(Vec<(String, Vec<u8>)>),
    Says(i32, String),
}

/// A scenario's events file as fake-bko-holt streams it: its lines but
/// comments, `sleep` and `delay`.
fn event_lines(b: &[u8]) -> VecDeque<String> {
    String::from_utf8_lossy(b)
        .lines()
        .map(str::trim)
        .filter(|l| !l.is_empty() && !l.starts_with('#') && !l.starts_with("sleep ") && !l.starts_with("delay "))
        .map(str::to_string)
        .collect()
}

/// A Restore's load, from a scenario's call: its events a few lines a
/// look, its exit once they are all read; a stop ends it as bko-holt's
/// does (stopping, end 5).
struct FakeLoad {
    lines: VecDeque<String>,
    exit: Option<i32>,
    stderr: Vec<u8>,
    seen: Rc<RefCell<Seen>>,
    detached: bool,
}

impl sys::Running for FakeLoad {
    fn read(&mut self, buf: &mut Vec<u8>, _max: usize) -> bool {
        for _ in 0..25 {
            match self.lines.pop_front() {
                Some(l) => {
                    buf.extend_from_slice(l.as_bytes());
                    buf.push(b'\n');
                }
                None => break,
            }
        }
        self.lines.is_empty()
    }
    fn ended(&mut self, _pid: Option<u32>) -> Option<Option<i32>> {
        self.lines.is_empty().then_some(self.exit)
    }
    fn term(&mut self, pid: u32) -> bool {
        self.seen.borrow_mut().terms.push(pid);
        self.lines.clear();
        self.lines.push_back(r#"{"event":"stopping","signal":"SIGTERM"}"#.into());
        self.lines.push_back(r#"{"event":"end","exit":5,"stopped":true,"secs":1.0}"#.into());
        self.exit = Some(5);
        true
    }
    fn stderr(&self) -> Vec<u8> {
        self.stderr.clone()
    }
    fn detached(&self) -> bool {
        self.detached
    }
    fn kill(&mut self) {
        self.lines.clear();
        self.exit = None;
    }
}

/// Copy a tree (the stick's partition, as a mount showed it).
fn copy_tree(from: &Path, to: &Path) {
    let Ok(rd) = fs::read_dir(from) else { return };
    fs::create_dir_all(to).unwrap();
    for e in rd.flatten() {
        let p = e.path();
        if p.is_dir() {
            copy_tree(&p, &to.join(e.file_name()));
        } else {
            fs::copy(&p, to.join(e.file_name())).unwrap();
        }
    }
}

/// The first call (by name) in scenario directory DIR whose argv matches
/// A, as fake-bko-holt picks one without a state file.
fn find_call(dir: &str, a: &[String]) -> Option<PathBuf> {
    let mut calls: Vec<PathBuf> = fs::read_dir(dir).ok()?.flatten().map(|e| e.path()).filter(|p| p.join("argv").is_file()).collect();
    calls.sort();
    calls.into_iter().find(|c| {
        let want: Vec<String> = fs::read_to_string(c.join("argv")).unwrap().lines().map(str::to_string).collect();
        want.len() == a.len() && want.iter().zip(a).all(|(w, x)| glob(w.as_bytes(), x.as_bytes()))
    })
}

/// fnmatch's `*` and `?`, as fake-bko-holt matches a call's argv.
fn glob(p: &[u8], s: &[u8]) -> bool {
    match (p.first(), s.first()) {
        (None, None) => true,
        (Some(b'*'), _) => glob(&p[1..], s) || (!s.is_empty() && glob(p, &s[1..])),
        (Some(b'?'), Some(_)) => glob(&p[1..], &s[1..]),
        (Some(a), Some(b)) if a == b => glob(&p[1..], &s[1..]),
        _ => false,
    }
}

/// bko-holt's log, as the fake has it.
const FAKE_LOG: &str = "==== bko-holt load /run/bko-rescue/layout.json --json --through close\n# ---- partition ----\nwipefs --all /dev/vdb\n";


struct Ready(Output);

impl sys::Job for Ready {
    fn poll(&mut self) -> Option<Output> {
        Some(self.0.clone())
    }
    fn kill(&mut self) {}
}

fn ok(stdout: Vec<u8>) -> Output {
    Output { code: Some(0), stdout, ..Output::default() }
}

impl Fake {
    fn answer(&mut self, c: &Cmd, stdin: Option<&[u8]>) -> Output {
        let prog = c.prog.file_name().unwrap().to_string_lossy().to_string();
        let a: Vec<String> = c.args.iter().map(|x| x.to_string_lossy().to_string()).collect();
        match prog.as_str() {
            "bko-holt" => match a[0].as_str() {
                "--version" => ok(self.version.clone().map(String::into_bytes).unwrap_or_else(|| fixture(&format!("scenarios/{}/01-version/stdout", self.holt)))),
                "stick" if a.get(1).map(String::as_str) == Some("--copy") && self.copy.is_some() => {
                    let (code, doc) = self.copy.clone().unwrap();
                    write_new(Path::new(&a[3]), &fixture("scenarios/stick-layouts/03-copy/out")).unwrap();
                    Output { code: Some(code), stdout: doc.into_bytes(), ..Output::default() }
                }
                "stick" if a.get(1).map(String::as_str) == Some("--copy") => {
                    // The layout's copy: the disks scenario's recorded
                    // call, else the stick's (a refusal), writing its OUT.
                    for scen in [self.disks.clone(), self.holt.clone()] {
                        if let Some(c) = find_call(&format!("{FIX}/scenarios/{scen}"), &a) {
                            if let Ok(b) = fs::read(c.join("out")) {
                                write_new(Path::new(&a[3]), &b).unwrap();
                            }
                            let code = fs::read_to_string(c.join("exit")).ok().and_then(|x| x.trim().parse().ok()).unwrap_or(0);
                            return Output { code: Some(code), stdout: fs::read(c.join("stdout")).unwrap_or_default(), ..Output::default() };
                        }
                    }
                    Output { code: Some(98), stderr: format!("fake: no stick --copy call matches {a:?}").into_bytes(), ..Output::default() }
                }
                "stick" => {
                    let doc = self.stick.clone().map(String::into_bytes).unwrap_or_else(|| fixture(&format!("scenarios/{}/02-stick/stdout", self.holt)));
                    let state_ok = String::from_utf8_lossy(&doc).contains(r#""state":"ok","partition""#);
                    Output { code: Some(if state_ok { 0 } else { 1 }), stdout: doc, ..Output::default() }
                }
                "recovery-words" => {
                    let typed = String::from_utf8_lossy(stdin.unwrap_or(b"")).to_string();
                    for call in ["03-recovery-good", "04-recovery-unknown", "05-recovery-checksum", "06-recovery-count"] {
                        let want = String::from_utf8(fixture(&format!("scenarios/stick-three/{call}/stdin"))).unwrap();
                        if want.split_whitespace().eq(typed.split_whitespace()) {
                            let out = fixture(&format!("scenarios/stick-three/{call}/stdout"));
                            if call == "03-recovery-good" {
                                write_new(Path::new(&a[1]), &fixture(&format!("scenarios/stick-three/{call}/out"))).unwrap();
                                return ok(out);
                            }
                            return Output { code: Some(1), stdout: out, ..Output::default() };
                        }
                    }
                    Output { code: Some(2), stderr: b"no such words".to_vec(), ..Output::default() }
                }
                "luks-headers" => match self.luks.clone() {
                    Some(Luks::Files(files)) => {
                        let dir = PathBuf::from(&a[2]);
                        assert!(!dir.exists(), "DIR is new: {a:?}");
                        fs::DirBuilder::new().mode(0o700).create(&dir).unwrap();
                        let version = crate::json::parse(&String::from_utf8(fixture(&format!("scenarios/{}/01-version/stdout", self.holt))).unwrap()).unwrap().get_str("version").unwrap().to_string();
                        let snap = a.iter().position(|x| x == "--snapshot").map(|i| a[i + 1].clone()).unwrap_or_else(|| "latest".into());
                        let rows: Vec<String> = files
                            .iter()
                            .map(|(f, b)| {
                                let p = dir.join(f);
                                write_new(&p, b).unwrap();
                                format!(r#"{{"device":"vda5","label":null,"file":"{f}","path":"{}","from":"{snap}:/.bko-holt/luks-headers/{f}","bytes":{},"sha256":"{}"}}"#, p.display(), b.len(), "ab".repeat(32))
                            })
                            .collect();
                        let doc = format!(
                            r#"{{"format":"bko-holt-luks-headers","schema":1,"version":"{version}","generated":"2026-11-26T10:00:00Z","dry_run":false,"layout":"{}","dir":"{}","snapshot":"{snap}","repo_dir":"/.bko-holt/luks-headers","headers":[{}]}}"#,
                            a[1],
                            a[2],
                            rows.join(",")
                        );
                        ok(format!("{doc}\n").into_bytes())
                    }
                    Some(Luks::Says(code, doc)) => Output { code: Some(code), stdout: doc.into_bytes(), ..Output::default() },
                    None => panic!("a luks-headers call for a layout that kept none: {a:?}"),
                },
                "report-bug" => {
                    let unredacted = a.contains(&"--no-redact".to_string());
                    let text = if unredacted { "NOT redacted\n" } else { "a redacted report\nhost-1\n" };
                    write_new(Path::new(&a[2]), text.as_bytes()).unwrap();
                    ok(b"Wrote it.\n".to_vec())
                }
                "load" => match self.call_seen(&a) {
                    Some(c) => {
                        let code = fs::read_to_string(c.join("exit")).ok().and_then(|x| x.trim().parse().ok()).unwrap_or(0);
                        Output { code: Some(code), stdout: fs::read(c.join("stdout")).unwrap_or_default(), ..Output::default() }
                    }
                    None => Output { code: Some(98), stderr: format!("fake: no call of {} matches {a:?}", self.disks).into_bytes(), ..Output::default() },
                },
                _ => Output { code: Some(2), ..Output::default() },
            },
            "rustic-bko" => {
                let pf = PathBuf::from(&a[3]);
                let mode = fs::metadata(&pf).map(|m| m.permissions().mode() & 0o777).unwrap_or(0);
                self.seen.borrow_mut().password_files.push((pf.clone(), mode));
                let pw = fs::read(&pf).unwrap_or_default();
                if pw != PASSWORD && pw != WORDS {
                    return Output { code: Some(1), stderr: fixture(&format!("rustic/{}/09-wrong-config/stderr", self.rustic)), ..Output::default() };
                }
                let call = if a.contains(&"cat".to_string()) { "01-config" } else { "02-snapshots" };
                if let (Some(c), "01-config") = (&self.config, call) {
                    return ok(c.clone());
                }
                ok(fixture(&format!("rustic/{}/{call}/stdout", self.rustic)))
            }
            "ckbcomp" => {
                self.seen.borrow_mut().layouts.push(a.join(" "));
                ok(b"keymaps 0-127\n".to_vec())
            }
            "mount" if a.iter().any(|x| x.starts_with("ro")) => panic!("a read-only mount of the stick: bko-holt stick --copy gives the layout"),
            "umount" => {
                // What was written there (a restore log) is on the stick.
                let m = PathBuf::from(a.last().unwrap());
                if m.join("backotter").is_dir() {
                    copy_tree(&m.join("backotter"), &self.base.join("stick/backotter"));
                }
                let _ = fs::remove_dir_all(PathBuf::from(a.last().unwrap()).join("backotter/machines"));
                let _ = fs::remove_dir(PathBuf::from(a.last().unwrap()).join("backotter"));
                ok(Vec::new())
            }
            "loadkeys" | "mount" => ok(Vec::new()),
            "gzip" => {
                let f = PathBuf::from(a.last().unwrap());
                let mut gz = b"gzip:".to_vec();
                gz.extend(fs::read(&f).unwrap());
                write_new(&PathBuf::from(format!("{}.gz", f.display())), &gz).unwrap();
                fs::remove_file(&f).unwrap();
                ok(Vec::new())
            }
            "reboot" | "poweroff" | "shutdown" => {
                self.seen.borrow_mut().offs.push(format!("{prog} {}", a.join(" ")).trim().to_string());
                ok(Vec::new())
            }
            p => panic!("the fake runs no {p}"),
        }
    }

    /// call(), its --luks-header-dir left out of the match and kept as
    /// seen.
    fn call_seen(&mut self, a: &[String]) -> Option<PathBuf> {
        let mut a = a.to_vec();
        let at = a.iter().position(|x| x == "--luks-header-dir");
        self.seen.borrow_mut().header_dirs.push(at.map(|i| a[i + 1].clone()));
        if let Some(i) = at {
            a.drain(i..i + 2);
        }
        self.call(&a)
    }

    /// The scenario's call that answers load's argv `a`.
    fn call(&mut self, a: &[String]) -> Option<PathBuf> {
        let dir = format!("{FIX}/scenarios/{}", self.disks);
        let mut calls: Vec<PathBuf> = fs::read_dir(&dir).unwrap().flatten().map(|e| e.path()).filter(|p| p.join("argv").is_file()).collect();
        calls.sort();
        let hits: Vec<PathBuf> = calls
            .into_iter()
            .filter(|c| {
                let want: Vec<String> = fs::read_to_string(c.join("argv")).unwrap().lines().map(str::to_string).collect();
                want.len() == a.len() && want.iter().zip(a).all(|(w, x)| glob(w.as_bytes(), x.as_bytes()))
            })
            .collect();
        let name = |c: &PathBuf| c.file_name().unwrap().to_string_lossy().to_string();
        let mut seen = self.seen.borrow_mut();
        let c = if self.in_turn { hits.iter().find(|c| !seen.loads.contains(&name(c))).or(hits.last()).cloned() } else { hits.first().cloned() }?;
        seen.loads.push(name(&c));
        Some(c)
    }
}

impl Sys for Fake {
    fn start(&mut self, c: Cmd) -> Box<dyn sys::Job> {
        let stdin = c.stdin.as_ref().map(|s| s.expose().to_vec());
        self.seen.borrow_mut().ran.push((c.line(), c.env.clone(), stdin.clone()));
        let o = self.answer(&c, stdin.as_deref());
        Box::new(Ready(o))
    }
    fn start_load(&mut self, c: Cmd, how: sys::How, _events: &Path, _stderr: &Path) -> Result<Box<dyn sys::Running>, String> {
        self.seen.borrow_mut().started.push((c.line(), c.env.clone(), how));
        let a: Vec<String> = c.args.iter().map(|x| x.to_string_lossy().to_string()).collect();
        let call = self.call_seen(&a);
        let (lines, exit, stderr) = match &call {
            Some(d) => {
                let lines = event_lines(&fs::read(d.join("events")).unwrap_or_default());
                let exit = fs::read_to_string(d.join("exit")).ok().and_then(|x| x.trim().parse().ok()).unwrap_or(0);
                (lines, exit, fs::read(d.join("stderr")).unwrap_or_default())
            }
            None => (VecDeque::new(), 98, format!("fake: no call of {} matches {a:?}", self.disks).into_bytes()),
        };
        let pid = lines.front().and_then(|l| crate::json::parse(l).ok()).and_then(|v| v.get_u64("pid"));
        self.seen.borrow_mut().load_pid = pid;
        Ok(Box::new(FakeLoad { lines, exit: Some(exit), stderr, seen: self.seen.clone(), detached: how == sys::How::Detached }))
    }
    fn attach(&mut self, _events: &Path, _stderr: &Path) -> Result<Box<dyn sys::Running>, String> {
        Err("the fake attaches to nothing".into())
    }
    fn alive(&self, _pid: u32) -> bool {
        false
    }
    fn proc_start(&self, _pid: u32) -> Option<String> {
        None
    }
    fn exists(&self, path: &Path) -> bool {
        self.seen.borrow().exists.iter().any(|p| p == path)
    }
    fn read_tail(&self, path: &Path, max: usize) -> Option<Vec<u8>> {
        if path == Path::new("/run/bko-holt/log/load.log") {
            return Some(FAKE_LOG.as_bytes().to_vec());
        }
        let b = fs::read(path).ok()?;
        Some(b[b.len().saturating_sub(max)..].to_vec())
    }
    fn shell(&mut self) {
        self.seen.borrow_mut().shells += 1;
    }
    fn now(&self) -> i64 {
        time::parse_rfc3339("2026-11-23T14:26:30Z").unwrap().0
    }
    fn read(&self, path: &Path, max: usize) -> Option<Vec<u8>> {
        if path.starts_with(time::zoneinfo_dir()) {
            return None;
        }
        if path == Path::new("/run/bko-holt/load.lock") {
            let s = self.seen.borrow();
            return s.lock.clone().or_else(|| s.load_pid.map(|p| format!(r#"{{"pid": {p}, "start": null}}"#))).map(String::into_bytes);
        }
        fs::read(path).ok().filter(|b| b.len() <= max)
    }
    fn which(&self, name: &str) -> Option<PathBuf> {
        if name == "bko-holt" {
            let mut s = self.seen.borrow_mut();
            if s.holt_hidden > 0 {
                s.holt_hidden -= 1;
                return None;
            }
        }
        self.tools.contains(&name).then(|| PathBuf::from("/fake/bin").join(name))
    }
    fn ports(&self) -> Option<Vec<sys::Port>> {
        Some(vec![
            sys::Port { name: "enp3s0".into(), carrier: Some(false), has_address: Some(false) },
            sys::Port { name: "enp4s0".into(), carrier: Some(true), has_address: Some(false) },
        ])
    }
    fn reachable(&mut self, _: &str, _: u16, _: u64) -> bool {
        self.seen.borrow_mut().reachable.pop_front().unwrap_or(true)
    }
    fn run_base(&self) -> PathBuf {
        self.base.clone()
    }
    fn sleep_ms(&mut self, _: u64) {}
}

fn scratch() -> PathBuf {
    use std::sync::atomic::{AtomicUsize, Ordering};
    static N: AtomicUsize = AtomicUsize::new(0);
    let d = std::env::temp_dir().join(format!("bko-rescue-app-{}-{}", std::process::id(), N.fetch_add(1, Ordering::SeqCst)));
    let _ = fs::remove_dir_all(&d);
    fs::DirBuilder::new().mode(0o700).create(&d).unwrap();
    d
}

const LINUX: Where = Where { os: Os::Linux, ssh: false, serial: false };

/// A test's directory, removed when the test ends (after the app's
/// session, declared before it, is wiped).
struct Scratch(PathBuf);

impl Drop for Scratch {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.0);
    }
}

struct T {
    app: App,
    seen: Rc<RefCell<Seen>>,
    base: Scratch,
}

fn t_with(holt: &str, rustic: &str, wh: Where, dry_run: bool, f: impl FnOnce(&mut Fake)) -> T {
    let base = scratch();
    let seen = Rc::new(RefCell::new(Seen::default()));
    let mut fake = Fake {
        base: base.clone(),
        holt: holt.into(),
        version: None,
        stick: None,
        rustic: rustic.into(),
        config: None,
        seen: seen.clone(),
        tools: vec!["bko-holt", "rustic-bko", "ckbcomp", "loadkeys", "mount", "umount", "gzip", "reboot", "poweroff"],
        disks: "app-lovelace".into(),
        in_turn: false,
        copy: None,
        luks: None,
    };
    f(&mut fake);
    let session = Session::new(&base).unwrap();
    let app = App::new(Box::new(fake), session, Opts { holt: None, rustic: "rustic-bko".into(), dry_run }, wh);
    T { app, seen, base: Scratch(base) }
}

fn tt(holt: &str, rustic: &str) -> T {
    t_with(holt, rustic, LINUX, false, |_| {})
}

/// The scripted Ui, with each dialog's screen kept (UTF-8 colour, as the
/// goldens are) and its kind and title.
struct Shots {
    inner: Scripted,
    shots: Vec<(String, Canvas)>,
}

impl Shots {
    fn new(r: impl IntoIterator<Item = Reply>) -> Shots {
        Shots { inner: Scripted::new(r), shots: Vec::new() }
    }

    fn shot(&mut self, title: &str, mut d: Dialog) {
        self.shots.push((title.to_string(), d.canvas()));
    }

    /// The screen of the n-th dialog titled `title`, as text.
    fn text(&self, title: &str, n: usize) -> String {
        let c = &self.shots.iter().filter(|(t, _)| t == title).nth(n).unwrap_or_else(|| panic!("no {title:?} {n}: {:?}", self.titles())).1;
        c.dump(Lines::Utf8, true).lines().take(24).collect::<Vec<_>>().join("\n")
    }

    /// Its prose as one line: the box's borders gone, rows joined by a
    /// space, so a phrase the wrap broke is still found.
    fn flat(&self, title: &str, n: usize) -> String {
        let t = self.text(title, n);
        let rows: Vec<String> = t
            .lines()
            .map(|l| l.trim_matches(|c: char| c == ' ' || "│┌┐└┘─".contains(c)).trim().to_string())
            .filter(|l| !l.is_empty())
            .collect();
        rows.join(" ")
    }

    fn titles(&self) -> Vec<String> {
        self.shots.iter().map(|(t, _)| t.clone()).collect()
    }

    fn golden(&self, title: &str, n: usize, name: &str) {
        let c = &self.shots.iter().filter(|(t, _)| t == title).nth(n).unwrap().1;
        let mut s = c.dump(Lines::Utf8, true);
        s.push_str(&c.dump(Lines::Ascii, false).lines().take(24).collect::<Vec<_>>().join("\n"));
        s.push('\n');
        golden(&format!("tests/screens/app-{name}.txt"), &s);
    }

    fn done(&self) {
        assert!(self.inner.replies.is_empty(), "replies left: {:?}", self.inner.replies);
    }
}

impl Ui for Shots {
    fn menu(&mut self, d: &crate::ui::Menu) -> Answer<usize> {
        self.shot(d.chrome.title.as_str(), Dialog::menu(d));
        self.inner.menu(d)
    }
    fn radio(&mut self, d: &Radio) -> Answer<usize> {
        self.shot(d.chrome.title.as_str(), Dialog::radio(d));
        self.inner.radio(d)
    }
    fn input(&mut self, d: &Input) -> Answer<String> {
        self.shot(d.chrome.title.as_str(), Dialog::input(d));
        self.inner.input(d)
    }
    fn password(&mut self, d: &Password) -> Answer<crate::secret::Secret> {
        self.shot(d.chrome.title.as_str(), Dialog::password(d));
        self.inner.password(d)
    }
    fn yes_no(&mut self, d: &Question) -> Answer<bool> {
        self.shot(d.chrome.title.as_str(), Dialog::question(d));
        self.inner.yes_no(d)
    }
    fn message(&mut self, d: &Message) -> Answer<()> {
        self.shot(d.chrome.title.as_str(), Dialog::message(d));
        self.inner.message(d)
    }
    fn view(&mut self, d: &TextView) -> Answer<()> {
        self.shot(d.chrome.title.as_str(), Dialog::view(d));
        self.inner.view(d)
    }
    fn progress(&mut self, d: &Progress, tick: &mut dyn FnMut() -> Tick) -> Answer<()> {
        self.inner.progress(d, tick)
    }
    fn words(&mut self, d: &Words) -> Answer<crate::secret::Secret> {
        self.shot(d.chrome.title.as_str(), Dialog::words(d));
        self.inner.words(d)
    }
    fn full_screen(&self) -> bool {
        true
    }
    fn passphrase(&mut self, d: &crate::ui::Passphrase) -> Answer<Vec<crate::secret::Secret>> {
        self.shot(d.chrome.title.as_str(), Dialog::passphrase(d));
        self.inner.passphrase(d)
    }
    fn review(&mut self, d: &crate::ui::Review, typed: &mut Vec<String>) -> Answer<()> {
        self.shot(d.chrome.title.as_str(), Dialog::review(d, typed));
        self.inner.review(d, typed)
    }
    fn cycle(&mut self, d: &crate::ui::Cycle) -> Answer<Vec<usize>> {
        self.shot(d.chrome.title.as_str(), Dialog::cycle(d));
        self.inner.cycle(d)
    }
}

use Reply::{Back, Button as B, Ok as P, Pick, Secret as Sec};

/// The two runs before the first screen: --version and stick.
fn start() -> Vec<Reply> {
    vec![P, P]
}

fn script(parts: &[&[Reply]]) -> Vec<Reply> {
    parts.iter().flat_map(|p| p.iter().cloned()).collect()
}

fn ran(t: &T) -> Vec<String> {
    t.seen.borrow().ran.iter().map(|(l, _, _)| l.clone()).collect()
}

#[test]
fn machine_password_snapshot_and_on() {
    let mut t = tt("stick-three", "lovelace");
    let mut ui = Shots::new(script(&[&start(), &[Pick(0), Sec(PASSWORD.to_vec()), P, P, Pick(0), P, Reply::Quit]]));
    assert_eq!(t.app.run(&mut ui), Exit::Quit);
    ui.done();
    assert_eq!(ui.titles(), ["Which machine is this?", "The backup password", "Which backup?", "Where each disk goes"]);
    let r = ran(&t);
    assert_eq!(r[0], "/fake/bin/bko-holt --version --json");
    assert_eq!(r[1], "/fake/bin/bko-holt stick --json");
    let dir = t.app.session.dir.clone();
    let pw = dir.join("password").display().to_string();
    assert_eq!(r[2], format!("/fake/bin/rustic-bko --repository sftp://nas.local/backotter/lovelace --password-file {pw} --no-progress cat config"));
    assert_eq!(r[3], format!("/fake/bin/rustic-bko --repository sftp://nas.local/backotter/lovelace --password-file {pw} --no-progress snapshots --json"));
    assert!(t.seen.borrow().password_files.iter().all(|(p, m)| p.starts_with(&dir) && *m == 0o600));
    assert_eq!(fs::metadata(&dir).unwrap().permissions().mode() & 0o777, 0o700);
    assert!(!t.seen.borrow().ran.iter().any(|(l, _, _)| l.contains("correct horse")), "never in argv");
    assert_eq!(t.app.snap.as_deref().map(|s| &s[..8]), Some("a7f3c901"));
    let machine = ui.text("Which machine is this?", 0);
    assert!(machine.contains("( ) lovelace   Debian 13      newest backup 22 Nov 2026 01:00 UTC"), "{machine}");
    assert!(machine.contains("( ) hopper     FreeBSD 15.0   newest backup 18 Nov 2026 01:00 UTC"), "{machine}");
    let snap = ui.text("Which backup?", 0);
    assert!(snap.contains("(*) 22 Nov 2026 02:00 UTC+01:00    61.2 GB   newest       a7f3c901"), "{snap}");
    assert!(snap.contains("( ) 01 Oct 2026 02:00 UTC+02:00    58.9 GB   monthly      5d2e90af"), "{snap}");
    assert!(snap.contains("Times are lovelace's offset from UTC, as each backup recorded it."), "{snap}");
    ui.golden("Which machine is this?", 0, "machine");
    ui.golden("The backup password", 0, "password");
    ui.golden("Which backup?", 0, "snapshot");
    drop(t.app);
    assert!(!dir.exists(), "the session's files are wiped and its directory gone");
}

#[test]
fn two_wrong_passwords_then_the_hint() {
    let mut t = tt("stick-three", "lovelace");
    let wrong = || Sec(b"wrong one".to_vec());
    let mut ui = Shots::new(script(&[&start(), &[Pick(0), wrong(), P, wrong(), P, wrong(), P, Sec(PASSWORD.to_vec()), P, P, Back, Sec(Vec::new()), P, P, Pick(1), P, Reply::Quit]]));
    assert_eq!(t.app.run(&mut ui), Exit::Quit);
    ui.done();
    let first = ui.flat("The backup password", 1);
    assert!(first.contains("That password didn't open the backups.  Check Caps Lock") && !first.contains("hint"), "{first}");
    assert!(first.contains("Tries so far: 1"));
    let second = ui.flat("The backup password", 2);
    assert!(second.contains("either") && second.contains("Your hint, saved when the backups were set up:"), "{second}");
    assert!(second.contains("\"the boat's name, then the year we moved\""), "{second}");
    let third = ui.flat("The backup password", 3);
    assert!(third.contains("A layout with Y and Z swapped is a usual cause.") && third.contains("Tries so far: 3"), "{third}");
    // Back from Snapshot: the password kept, an empty field uses it.
    let back = ui.flat("The backup password", 4);
    assert!(back.contains("press Enter in the empty field"), "{back}");
    assert_eq!(t.app.snap.as_deref().map(|s| &s[..8]), Some("3be04d2a"));
    ui.golden("The backup password", 1, "password-wrong-1");
    ui.golden("The backup password", 2, "password-wrong-2");
    // Each wrong password's file was wiped before the next was written.
    let files: Vec<PathBuf> = t.seen.borrow().password_files.iter().map(|(p, _)| p.clone()).collect();
    assert!(files.iter().all(|p| p.ends_with("password")));
}

#[test]
fn the_recovery_key() {
    let mut t = tt("stick-three", "lovelace");
    let swapped = b"winner legal thank year wave sausage worth useful legal winner thank yellow".to_vec();
    let mut ui = Shots::new(script(&[&start(), &[Pick(0), B(B_RECOVERY), Sec(swapped), P, Sec(WORDS.to_vec()), P, P, P, Back, Back, Back, Pick(0), Sec(PASSWORD.to_vec()), P, P, Pick(0), P, Reply::Quit]]));
    assert_eq!(t.app.run(&mut ui), Exit::Quit);
    ui.done();
    let seen = t.seen.borrow();
    let rec: Vec<_> = seen.ran.iter().filter(|(l, _, _)| l.contains("recovery-words")).collect();
    assert_eq!(rec.len(), 2);
    let out = t.app.session.dir.join("recovery-password");
    assert_eq!(rec[0].0, format!("/fake/bin/bko-holt recovery-words {} --json", out.display()));
    assert_eq!(rec[1].2.as_deref(), Some(WORDS), "the words on a pipe, never in argv");
    assert!(!rec[1].0.contains("legal"));
    let again = ui.flat("Recovery key", 1);
    assert!(again.contains("The 12 words don't add up: one is wrong, or two are swapped."), "{again}");
    // The recovery key's password went to rustic-bko as a file of ours.
    assert!(seen.password_files.iter().any(|(p, m)| *p == out && *m == 0o600));
    ui.golden("Recovery key", 0, "recovery");
    // Back from Snapshot went to the password screen, then Machine.
    assert_eq!(ui.titles().iter().filter(|x| *x == "The backup password").count(), 3);
}

#[test]
fn no_route_shows_the_network_screen() {
    let mut t = tt("stick-three", "lovelace");
    // The check before Repository, Try again's 16 looks over 30 s, then
    // a route on the second Try again.
    t.seen.borrow_mut().reachable.extend(std::iter::repeat_n(false, 17).chain([true]));
    let mut ui = Shots::new(script(&[&start(), &[Pick(0), B(B_TRY), P, B(B_TRY), P, Back, B(B_BY_HAND), Reply::Ok, Reply::Shell]]));
    assert_eq!(t.app.run(&mut ui), Exit::Shell);
    ui.done();
    assert_eq!(t.app.step, Step::Network, "the shell comes back to the same screen");
    assert_eq!(ui.titles(), ["Which machine is this?", "No network", "No network", "The backup password", "No network", "Setting the address by hand", "No network"]);
    let net = ui.text("No network", 0);
    assert!(net.contains("Port 1   no cable plugged in"), "{net}");
    assert!(ui.flat("No network", 0).contains("lovelace's backups are on nas.local, which this computer can't reach."), "{net}");
    assert!(net.contains("Port 1   no cable plugged in") && net.contains("(enp4s0)"), "{net}");
    ui.golden("No network", 0, "network");
}

#[test]
fn a_stick_with_no_settings_types_a_location() {
    let mut t = tt("stick-missing", "shared");
    let mut ui = Shots::new(script(&[&start(), &[Reply::Text("nas.local/backotter".into()), Reply::Text("sftp://nas.local/backotter/shared".into()), Sec(PASSWORD.to_vec()), P, P, Pick(1), Pick(0), Back, Back, Back, Back, Reply::Text("sftp://u:pw@nas/x".into()), Reply::Quit]]));
    assert_eq!(t.app.run(&mut ui), Exit::Quit);
    ui.done();
    let first = ui.text("Where is the backup?", 0);
    assert!(ui.flat("Where is the backup?", 0).contains("its BACKOTTER partition, which holds its settings, is missing."), "{first}");
    assert!(first.contains("[                "), "{first}");
    assert!(!first.contains("< Back >"), "the first screen has no Back");
    let bad = ui.flat("Where is the backup?", 1);
    assert!(bad.contains("That isn't an address bko-rescue can use."), "{bad}");
    assert!(ui.flat("Where is the backup?", 3).contains("That isn't an address"), "a password in it is refused too");
    let which = ui.text("Which machine?", 0);
    assert!(which.contains("These backups are of 2 machines.") && which.contains("lovelace   2 backups, newest 22 Nov 2026 02:00 UTC+01:00"), "{which}");
    assert_eq!(t.app.step, Step::Location);
    let snap = ui.text("Which backup?", 0);
    assert!(snap.contains("babbage's backups, newest first:") && snap.contains("21 Nov 2026 03:00 UTC-05:00"), "{snap}");
    ui.golden("Where is the backup?", 0, "location-missing");
    ui.golden("Which machine?", 0, "which");
}

#[test]
fn type_a_location_from_machine_has_back() {
    let mut t = tt("stick-three", "lovelace");
    let mut ui = Shots::new(script(&[&start(), &[B(B_LOCATION), Back, Reply::Plain]]));
    assert_eq!(t.app.run(&mut ui), Exit::Plain);
    let loc = ui.text("Where is the backup?", 0);
    assert!(loc.contains("< Back >") && !loc.contains("missing"), "{loc}");
    assert_eq!(t.app.step, Step::Machine);
    ui.golden("Where is the backup?", 0, "location");
}

#[test]
fn another_repository_is_a_problem_never_a_switch() {
    let mut t = tt("stick-three", "mismatch");
    let mut ui = Shots::new(script(&[&start(), &[Pick(0), Sec(PASSWORD.to_vec()), P, B(B_FIX), Reply::Quit]]));
    assert_eq!(t.app.run(&mut ui), Exit::Quit);
    ui.done();
    let p = ui.text("Problem: these aren't this machine's backups", 0);
    assert!(ui.flat("Problem: these aren't this machine's backups", 0).contains("What happened: These aren't the backups the stick knows for lovelace"), "{p}");
    assert!(p.contains("Your backup is safe: nothing in it has been changed."), "{p}");
    assert!(p.contains("< Another machine >") && p.contains("< Type a location >") && p.contains("< Report >"), "{p}");
    assert!(!ran(&t).iter().any(|l| l.contains("snapshots")), "no backups read from a repository that isn't the stick's");
    assert_eq!((t.app.step, t.app.history.len()), (Step::Machine, 0));
    assert!(t.app.password.is_none(), "the password went with the machine");
    ui.golden("Problem: these aren't this machine's backups", 0, "problem-mismatch");
}

#[test]
fn rustics_failures_each_have_their_help() {
    for (err, title, golden_name) in [
        ("ssh: connect to host nas.local port 22: Connection refused", "Problem: the backups can't be reached", "problem-unreachable"),
        ("No repository config file found for `x`. Please check the repository.", "Problem: no backups at that address", "problem-not-found"),
        ("Host key verification failed.", "Problem: the server refused the login", "problem-login"),
        ("thread 'main' panicked at x", "Problem: the backups couldn't be read", "problem-error"),
    ] {
        let mut t = tt("stick-three", "lovelace");
        let stderr = err.as_bytes().to_vec();
        struct Failing(Fake, Vec<u8>);
        impl Sys for Failing {
            fn start(&mut self, c: Cmd) -> Box<dyn sys::Job> {
                if c.prog.ends_with("rustic-bko") {
                    return Box::new(Ready(Output { code: Some(1), stderr: self.1.clone(), ..Output::default() }));
                }
                self.0.start(c)
            }
            fn shell(&mut self) {}
            fn now(&self) -> i64 {
                self.0.now()
            }
            fn read(&self, p: &Path, m: usize) -> Option<Vec<u8>> {
                self.0.read(p, m)
            }
            fn which(&self, n: &str) -> Option<PathBuf> {
                self.0.which(n)
            }
            fn ports(&self) -> Option<Vec<sys::Port>> {
                None
            }
            fn reachable(&mut self, _: &str, _: u16, _: u64) -> bool {
                true
            }
            fn run_base(&self) -> PathBuf {
                self.0.run_base()
            }
            fn sleep_ms(&mut self, _: u64) {}
            fn start_load(&mut self, c: Cmd, how: sys::How, e: &Path, s: &Path) -> Result<Box<dyn sys::Running>, String> {
                self.0.start_load(c, how, e, s)
            }
            fn attach(&mut self, e: &Path, s: &Path) -> Result<Box<dyn sys::Running>, String> {
                self.0.attach(e, s)
            }
            fn alive(&self, p: u32) -> bool {
                self.0.alive(p)
            }
            fn proc_start(&self, p: u32) -> Option<String> {
                self.0.proc_start(p)
            }
            fn exists(&self, p: &Path) -> bool {
                self.0.exists(p)
            }
            fn read_tail(&self, p: &Path, m: usize) -> Option<Vec<u8>> {
                self.0.read_tail(p, m)
            }
        }
        let base = t.base.0.clone();
        let fake = Fake { base: base.clone(), holt: "stick-three".into(), version: None, stick: None, rustic: "lovelace".into(), disks: "app-lovelace".into(), config: None, seen: t.seen.clone(), tools: vec!["bko-holt", "rustic-bko"], in_turn: false, copy: None, luks: None };
        t.app = App::new(Box::new(Failing(fake, stderr)), Session::new(&base).unwrap(), Opts { holt: None, rustic: "rustic-bko".into(), dry_run: false }, LINUX);
        let mut ui = Shots::new(script(&[&start(), &[Pick(0), Sec(PASSWORD.to_vec()), P, B(B_RAW), Reply::Ok, Back, Reply::Quit]]));
        assert_eq!(t.app.run(&mut ui), Exit::Quit, "{title}");
        let p = ui.flat(title, 0);
        assert!(p.contains("Your backup is safe") && p.contains("< Raw output >"), "{p}");
        let raw = ui.text("What the program said", 0);
        assert!(raw.contains(&err[..20]), "{raw}");
        ui.golden(title, 0, golden_name);
        // Back from the help: the password screen again.
        assert_eq!(ui.titles().last().map(String::as_str), Some("The backup password"));
    }
}

#[test]
fn a_report_and_its_unredacted_twin() {
    let mut t = tt("stick-three", "mismatch");
    let mut ui = Shots::new(script(&[
        &start(),
        &[Pick(0), Sec(PASSWORD.to_vec()), P, B(B_REPORT), P, B(1), Reply::Ok, B(2), Reply::Text("no".into()), Reply::Text("yes".into()), P, Reply::Ok, Back, Reply::Quit],
    ]));
    assert_eq!(t.app.run(&mut ui), Exit::Quit);
    ui.done();
    let r = ran(&t);
    let reports = t.base.0.join("reports");
    let first = reports.join("bko-report-20261123-142630.txt");
    let second = reports.join("bko-report-20261123-142630-UNREDACTED.txt");
    let strip = t.app.session.path("strip");
    assert!(r.contains(&format!("/fake/bin/bko-holt report-bug --output {} --strip-file {}", first.display(), strip.display())), "{r:#?}");
    assert!(r.contains(&format!("/fake/bin/bko-holt report-bug --output {} --no-redact", second.display())), "{r:#?}");
    assert!(!strip.exists(), "the strip file is wiped after");
    let mnt = t.app.session.path("mnt");
    assert!(r.iter().any(|l| l == &format!("/fake/bin/mount -t vfat -o rw,nosuid,nodev,noexec /dev/sdc3 {}", mnt.display())), "{r:#?}");
    assert!(r.iter().filter(|l| l.starts_with("/fake/bin/umount")).count() == 2);
    let saved = ui.flat("Report saved", 0);
    assert!(saved.contains("backotter/reports/bko-report-20261123-142630.txt") && saved.contains("github.com/mchobbit/backotter/issues"), "{saved}");
    assert!(saved.contains("< Advanced >") && saved.contains("< View it >"), "{saved}");
    let view = ui.text("The report", 0);
    assert!(view.contains("a redacted report"), "{view}");
    let adv = ui.text("Advanced: a report that isn't redacted", 0);
    assert!(adv.contains("To make it, type YES:  [     ]") && adv.contains("GitHub issues are public"), "{adv}");
    let again = ui.flat("Advanced: a report that isn't redacted", 1);
    assert!(again.contains("Type YES to make it, or Esc to go back."), "{again}");
    // Report saved again after View it, then with both.
    let both = ui.flat("Report saved", 2);
    assert!(both.contains("-UNREDACTED.txt") && both.contains("don't attach it to an issue") && !both.contains("< Advanced >"), "{both}");
    ui.golden("Report saved", 0, "report-saved");
    ui.golden("Advanced: a report that isn't redacted", 0, "report-advanced");
    ui.golden("Report saved", 2, "report-saved-both");
}

#[test]
fn a_dry_run_locks_and_reports_stay_in_ram() {
    // bko-holt confirms the lock: no_commit true in its api document.
    let version = String::from_utf8(fixture("scenarios/stick-three/01-version/stdout")).unwrap().replace(r#""no_commit":false"#, r#""no_commit":true"#);
    let mut t = t_with("stick-three", "mismatch", LINUX, true, |f| f.version = Some(version));
    let mut ui = Shots::new(script(&[&start(), &[Pick(0), Sec(PASSWORD.to_vec()), P, B(B_REPORT), P, Reply::Ok, Back, Reply::Quit]]));
    assert_eq!(t.app.run(&mut ui), Exit::Quit);
    let seen = t.seen.borrow();
    for (l, env, _) in seen.ran.iter().filter(|(l, _, _)| l.starts_with("/fake/bin/bko-holt")) {
        assert!(env.contains(&("BKO_HOLT_NO_COMMIT".to_string(), "1".to_string())), "{l}: lock 2");
        assert!(!l.contains("--commit"), "{l}: lock 1");
    }
    assert!(!seen.ran.iter().any(|(l, _, _)| l.contains("mount")), "a dry run writes nothing to the stick");
    let saved = ui.flat("Report saved", 0);
    assert!(saved.contains("in this computer's memory only") && saved.contains("This is a dry run"), "{saved}");
    // No golden: its text names the test's own temporary directory.
    // Without the lock confirmed, it stops before the first screen.
    let mut t = t_with("stick-three", "lovelace", LINUX, true, |_| {});
    let mut ui = Shots::new(script(&[&[P], &[Reply::Shell]]));
    assert_eq!(t.app.run(&mut ui), Exit::Shell);
    let p = ui.flat("Problem: this stick's bko-holt", 0);
    assert!(p.contains("didn't confirm its lock"), "{p}");
}

#[test]
fn bko_holt_missing_too_old_or_two_sticks() {
    let mut t = t_with("stick-three", "lovelace", LINUX, false, |f| f.tools = vec!["rustic-bko"]);
    let mut ui = Shots::new([B(B_LOCATION), Reply::Quit]);
    assert_eq!(t.app.run(&mut ui), Exit::Quit);
    assert!(ui.flat("Problem: this stick's bko-holt", 0).contains("it isn't here"));
    assert!(ui.flat("Where is the backup?", 0).contains("couldn't say which machines"));
    let mut t = t_with("stick-three", "lovelace", LINUX, false, |f| f.version = Some("bko-holt 0.2.6\n".into()));
    let mut ui = Shots::new([P, Reply::Quit]);
    assert_eq!(t.app.run(&mut ui), Exit::Quit);
    assert!(ui.flat("Problem: this stick's bko-holt", 0).contains("too old for this bko-rescue"));
    ui.golden("Problem: this stick's bko-holt", 0, "holt-too-old");
    // One without stick --copy is too old too: said at the start, not on Disks.
    let api = String::from_utf8(fixture("scenarios/stick-three/01-version/stdout")).unwrap();
    let old = api.replace(r#""stick_copy":[1],"#, "");
    assert_ne!(old, api);
    let mut t = t_with("stick-three", "lovelace", LINUX, false, |f| f.version = Some(old));
    let mut ui = Shots::new([P, Reply::Quit]);
    assert_eq!(t.app.run(&mut ui), Exit::Quit);
    assert!(ui.flat("Problem: this stick's bko-holt", 0).contains("too old for this bko-rescue"));
    let two = String::from_utf8(fixture("scenarios/stick-missing/02-stick/stdout")).unwrap().replace(r#""state":"missing""#, r#""state":"two_sticks""#);
    let mut t = t_with("stick-missing", "lovelace", LINUX, false, |f| f.stick = Some(two));
    let mut ui = Shots::new(script(&[&start(), &[B(B_TRY), P, P, Reply::Quit]]));
    assert_eq!(t.app.run(&mut ui), Exit::Quit);
    assert!(ui.text("Two BackOtter sticks", 0).contains("< Retry >"));
}

#[test]
fn this_computer_and_machines_that_cant_be_chosen() {
    let mut t = tt("stick-this-computer", "lovelace");
    let mut ui = Shots::new(script(&[&start(), &[Reply::Quit]]));
    t.app.run(&mut ui);
    let m = ui.text("Which machine is this?", 0);
    assert!(m.contains("(*) lovelace   Debian 13      backed up from this computer"), "{m}");
    assert!(ui.flat("Which machine is this?", 0).contains("lovelace was backed up from this computer"), "{m}");
    ui.golden("Which machine is this?", 0, "machine-here");
    let mut t = tt("stick-failing", "lovelace");
    let mut ui = Shots::new(script(&[&start(), &[Reply::Quit]]));
    t.app.run(&mut ui);
    let m = ui.flat("Which machine is this?", 0);
    assert!(m.contains("can't be chosen: its settings on the stick fail"), "{m}");
    ui.golden("Which machine is this?", 0, "machine-failing");
}

/// lovelace's backup names German: the keyboard offer.
fn german_stick() -> String {
    String::from_utf8(fixture("scenarios/stick-three/02-stick/stdout")).unwrap().replacen(
        r#""keyboard":null"#,
        r#""keyboard":{"canonical":{"layout":"de","variant":"nodeadkeys","from":"xkb"},"desktop":[{"source":"kxkbrc","layouts":[{"layout":"de","variant":"nodeadkeys"},{"layout":"us","variant":null}]}]}"#,
        1,
    )
}

#[test]
fn the_keyboard_offer_and_the_keyboard_screen() {
    let stick = german_stick();
    let mut t = t_with("stick-three", "lovelace", LINUX, false, |f| f.stick = Some(stick));
    let mut ui = Shots::new(script(&[&start(), &[Pick(0), Reply::Yes, B(B_KEYBOARD), Pick(3), Reply::Quit]]));
    assert_eq!(t.app.run(&mut ui), Exit::Quit);
    ui.done();
    let offer = ui.flat("Keyboard for the password", 0);
    assert!(offer.contains("lovelace used a German keyboard (de, nodeadkeys) when it was backed up.") && offer.contains("< Switch to German >"), "{offer}");
    assert!(offer.contains("Its desktop also had English (US)."), "{offer}");
    let pw = ui.flat("The backup password", 0);
    assert!(pw.contains("Keyboard: German (de, nodeadkeys), from lovelace's backup."), "{pw}");
    let kb = ui.text("Keyboard layout", 0);
    assert!(kb.contains("(*) de nodeadkeys   German, no dead keys            lovelace's"), "{kb}");
    assert!(kb.contains("Try it here:  ["), "{kb}");
    // The scripted Ui moves no focus: the offer's switch, then OK's.
    assert_eq!(t.seen.borrow().layouts, ["-layout de -variant nodeadkeys", "-layout gb"]);
    assert_eq!(t.app.keyboard.set, Xkb { layout: "gb".into(), variant: None });
    ui.golden("Keyboard for the password", 0, "keyboard-offer");
    ui.golden("Keyboard layout", 0, "keyboard");
    // Shown once per machine: Back and on again doesn't repeat it.
    let stick = german_stick();
    let mut t = t_with("stick-three", "lovelace", LINUX, false, |f| f.stick = Some(stick));
    let mut ui = Shots::new(script(&[&start(), &[Pick(0), Reply::No, Back, Pick(0), Reply::Quit]]));
    t.app.run(&mut ui);
    assert_eq!(ui.titles().iter().filter(|x| *x == "Keyboard for the password").count(), 1);
    // Over ssh: no offer, no < Keyboard >, the client's layout.
    let stick = german_stick();
    let ssh = Where { os: Os::Linux, ssh: true, serial: false };
    let mut t = t_with("stick-three", "lovelace", ssh, false, |f| f.stick = Some(stick));
    let mut ui = Shots::new(script(&[&start(), &[Pick(0), Reply::Quit]]));
    t.app.run(&mut ui);
    let pw = ui.flat("The backup password", 0);
    assert!(pw.contains("The keyboard layout is your own computer's.") && !pw.contains("< Keyboard >"), "{pw}");
    assert!(!ui.text("Which machine is this?", 0).contains("Keyboard"));
    // Where the stick can't change it (the BSDs until the table, Haiku).
    let stick = german_stick();
    let bsd = Where { os: Os::FreeBsd, ssh: false, serial: false };
    let mut t = t_with("stick-three", "lovelace", bsd, false, |f| f.stick = Some(stick));
    let mut ui = Shots::new(script(&[&start(), &[Pick(0), Reply::Quit]]));
    t.app.run(&mut ui);
    assert!(ui.flat("The backup password", 0).contains("lovelace's backup names German (de, nodeadkeys): set it by hand"));
}

#[test]
fn plain_mode_transcripts() {
    for (name, typed) in [
        ("machine-password", "1\nwrong\nwrong again\ncorrect horse\n1\nl\n"),
        ("recovery", "1\n\nu\nharv qqqq\nlega winn thank year wave saus wort usef legal winn than yell\ny\n2\nl\n"),
    ] {
        let mut t = tt("stick-three", "lovelace");
        let mut pl = Plain::new(FakeLine::typed(typed), Lines::Utf8);
        let r = t.app.run(&mut pl);
        let out = pl.into_line().out;
        assert!(matches!(r, Exit::Done(0) | Exit::Quit), "{name}: {r:?}\n{out}");
        assert!(!out.contains('\x1b'), "no escape sequences at all");
        golden(&format!("tests/screens/app-plain-{name}.txt"), &out);
        if name == "recovery" {
            assert!(out.contains("Word 2, qqqq, isn't the start of any recovery word."), "{out}");
            assert!(out.contains("  1 legal      2 winner     3 thank      4 year"), "{out}");
        } else {
            assert!(out.contains("Your hint, saved when the backups were set up:"), "{out}");
            assert!(!out.contains("correct horse") && !out.contains("wrong again"), "nothing typed for a password shows");
        }
    }
}

#[test]
fn back_and_the_history() {
    // Machine -> Repository -> Back -> Machine; Machine has no Back.
    let mut t = tt("stick-three", "lovelace");
    let mut ui = Shots::new(script(&[&start(), &[Pick(1), Back, Back, Pick(0), Back, Reply::Quit]]));
    assert_eq!(t.app.run(&mut ui), Exit::Quit);
    assert_eq!(ui.titles(), ["Which machine is this?", "The backup password", "Which machine is this?", "Which machine is this?", "The backup password", "Which machine is this?"]);
    let pw = ui.text("The backup password", 0);
    assert!(pw.contains("babbage's backups are kept on 10.0.0.5, at") && pw.contains("rest:http://10.0.0.5:8000/babbage"), "{pw}");
}

/// stick-three's document with `from` replaced by `to` once.
fn stick_with(from: &str, to: &str) -> String {
    let doc = String::from_utf8(fixture("scenarios/stick-three/02-stick/stdout")).unwrap();
    assert!(doc.contains(from), "{from}");
    doc.replacen(from, to, 1)
}

#[test]
fn an_id_that_cant_be_checked_stops_as_a_mismatch_does() {
    // The repository's config has no readable id; then the stick's own is
    // too short to compare.  Neither lists a backup.
    for (config, stick) in [(Some(b"{\"version\":2}".to_vec()), None), (None, Some(stick_with(r#""repository_id":"fb1e7ec987523d2c""#, r#""repository_id":"fb1e""#)))] {
        let mut t = t_with("stick-three", "lovelace", LINUX, false, |f| {
            f.config = config;
            f.stick = stick;
        });
        let mut ui = Shots::new(script(&[&start(), &[Pick(0), Sec(PASSWORD.to_vec()), P, Reply::Quit]]));
        assert_eq!(t.app.run(&mut ui), Exit::Quit);
        ui.done();
        let p = ui.flat("Problem: these aren't this machine's backups", 0);
        assert!(p.contains("The stick couldn't check these are lovelace's backups"), "{p}");
        assert!(!ran(&t).iter().any(|l| l.contains("snapshots")), "no backups read from a repository not checked");
    }
    // A typed address names no ID: nothing to check, on to the list.
    assert_eq!(repo::id_check(None, None), repo::IdCheck::NoneKept);
}

#[test]
fn a_machine_whose_address_cant_be_opened_is_blocked() {
    for bad in ["s3:bucket/lovelace", "sftp://bko:secret@nas.local/backotter/lovelace", "sftp:nas.local:/backotter/lovelace"] {
        let stick = stick_with(r#""url":"sftp://nas.local/backotter/lovelace""#, &format!(r#""url":"{bad}""#));
        let mut t = t_with("stick-three", "lovelace", LINUX, false, |f| f.stick = Some(stick));
        let mut ui = Shots::new(script(&[&start(), &[Pick(0), Reply::Quit]]));
        assert_eq!(t.app.run(&mut ui), Exit::Quit);
        let m = ui.flat("Which machine is this?", 0);
        assert!(m.contains("lovelace   can't be chosen: its backups' address isn't usable"), "{bad}: {m}");
        // OK on it stays on Machine: no repository is opened.
        assert_eq!(ui.titles(), ["Which machine is this?", "Which machine is this?"], "{bad}");
        assert!(!ran(&t).iter().any(|l| l.contains("rustic-bko")), "{bad}");
    }
}

#[test]
fn try_again_looks_for_bko_holt_again() {
    let mut t = tt("stick-three", "lovelace");
    // The first start's look: not there; Try again's: it is.
    t.seen.borrow_mut().holt_hidden = 1;
    let mut ui = Shots::new(script(&[&[B(B_TRY)], &start(), &[Reply::Quit]]));
    assert_eq!(t.app.run(&mut ui), Exit::Quit);
    ui.done();
    assert_eq!(ui.titles(), ["Problem: this stick's bko-holt", "Which machine is this?"]);
}

#[test]
fn a_report_isnt_made_without_its_strip_file() {
    let mut t = tt("stick-three", "mismatch");
    // Something in the strip file's place that can't be replaced.
    let strip = t.app.session.path("strip");
    fs::create_dir(&strip).unwrap();
    fs::write(strip.join("x"), b"x").unwrap();
    let mut ui = Shots::new(script(&[&start(), &[Pick(0), Sec(PASSWORD.to_vec()), P, B(B_REPORT), Reply::Ok, Reply::Quit]]));
    assert_eq!(t.app.run(&mut ui), Exit::Quit);
    ui.done();
    let m = ui.flat("The report couldn't be made", 0);
    assert!(m.contains("couldn't be written, so no report was made"), "{m}");
    assert!(!ran(&t).iter().any(|l| l.contains("report-bug")), "never a report with the hint left in");
    fs::remove_dir_all(&strip).unwrap();
}

#[test]
fn a_dry_run_says_so_on_every_screen() {
    let version = String::from_utf8(fixture("scenarios/stick-three/01-version/stdout")).unwrap().replace(r#""no_commit":false"#, r#""no_commit":true"#);
    let mut t = t_with("stick-three", "mismatch", LINUX, true, |f| f.version = Some(version.clone()));
    let mut ui = Shots::new(script(&[&start(), &[Pick(0), Sec(PASSWORD.to_vec()), P, B(B_REPORT), P, Reply::Ok, Back, Back, B(B_LOCATION), Reply::Quit]]));
    assert_eq!(t.app.run(&mut ui), Exit::Quit);
    ui.done();
    assert!(ui.shots.len() >= 5, "{:?}", ui.titles());
    for (title, c) in &ui.shots {
        let top = c.dump(Lines::Utf8, false).lines().next().unwrap_or("").to_string();
        assert!(top.starts_with(" BackOtter rescue   DRY RUN: nothing will be written"), "{title}: {top}");
    }
    ui.golden("Which machine is this?", 0, "machine-dry-run");
    // Plain mode: a line before each title.
    let mut t = t_with("stick-three", "lovelace", LINUX, true, |f| f.version = Some(version));
    let mut pl = Plain::new(FakeLine::typed("t\n"), Lines::Utf8);
    t.app.run(&mut pl);
    let out = pl.into_line().out;
    assert!(out.contains("DRY RUN: nothing will be written\nWhich machine is this?\n"), "{out}");
    assert!(out.contains("DRY RUN: nothing will be written\nWhere is the backup?\n"), "{out}");
}

#[test]
fn plain_mode_uses_the_kept_password_from_its_menu() {
    let mut t = tt("stick-three", "lovelace");
    // The password, Back from Snapshot, Enter alone, p: the kept one.
    let mut pl = Plain::new(FakeLine::typed("1\ncorrect horse\nb\n\np\n1\n"), Lines::Utf8);
    assert_eq!(t.app.run(&mut pl), Exit::Quit);
    let out = pl.into_line().out;
    let flat = out.replace('\n', " ");
    assert!(out.contains("Type the backup password, or press Enter alone for other choices:"), "{out}");
    assert!(flat.contains("The password typed before is kept: press Enter alone, then p, to use it again."), "{out}");
    assert!(out.contains("p) use the password typed before"), "{out}");
    assert!(!out.contains("empty field"), "{out}");
    let opened = ran(&t).iter().filter(|l| l.ends_with("cat config")).count();
    assert_eq!(opened, 2, "opened twice, with one password typed");
    assert_eq!(t.app.snap.as_deref().map(|s| &s[..8]), Some("a7f3c901"));
}

#[test]
fn a_variant_the_stick_hasnt_offers_its_base_layout() {
    let stick = stick_with(r#""keyboard":null"#, r#""keyboard":{"canonical":{"layout":"de","variant":"neo","from":"xkb"}}"#);
    let mut t = t_with("stick-three", "lovelace", LINUX, false, |f| f.stick = Some(stick));
    let mut ui = Shots::new(script(&[&start(), &[Pick(0), Reply::Yes, Reply::Quit]]));
    assert_eq!(t.app.run(&mut ui), Exit::Quit);
    ui.done();
    let offer = ui.flat("Keyboard for the password", 0);
    assert!(offer.contains("lovelace used a German keyboard (de, neo) when it was backed up."), "{offer}");
    assert!(offer.contains("This stick has German, but not its \"neo\" variant"), "{offer}");
    assert_eq!(t.seen.borrow().layouts, ["-layout de"], "the base layout, no variant");
    assert_eq!(t.app.keyboard.set, Xkb { layout: "de".into(), variant: None });
    // A layout the stick hasn't at all: no offer.
    let stick = stick_with(r#""keyboard":null"#, r#""keyboard":{"canonical":{"layout":"zz","variant":"neo","from":"xkb"}}"#);
    let mut t = t_with("stick-three", "lovelace", LINUX, false, |f| f.stick = Some(stick));
    let mut ui = Shots::new(script(&[&start(), &[Pick(0), Reply::Quit]]));
    t.app.run(&mut ui);
    assert_eq!(ui.titles(), ["Which machine is this?", "The backup password"]);
}

#[test]
fn the_offer_is_once_per_machine_with_no_install_id_too() {
    let stick = german_stick().replacen(r#""install_id":"0b7e3c52-8d1f-4a6e-9c3b-2f5d7a1e4b90""#, r#""install_id":"""#, 1);
    let mut t = t_with("stick-three", "lovelace", LINUX, false, |f| f.stick = Some(stick));
    let mut ui = Shots::new(script(&[&start(), &[Pick(0), Reply::No, Back, Pick(0), Reply::Quit]]));
    t.app.run(&mut ui);
    assert_eq!(ui.titles().iter().filter(|x| *x == "Keyboard for the password").count(), 1);
}

// ---- RT12: Disks, Passphrases, Resume, Review ----

use super::disks::{B_DONE, B_KEEP, B_SHRINK};
use super::review::{B_ADVANCED, B_COMMANDS, B_GO_ON, B_START_AGAIN, B_UNDO};
use Reply::{Choices, Secrets, Serials};

/// Machine, the password, the newest backup: up to Disks' first plan.
fn to_disks() -> Vec<Reply> {
    script(&[&start(), &[Pick(0), Sec(PASSWORD.to_vec()), P, P, Pick(0), P]])
}

fn disks_t(scenario: &str, dry_run: bool) -> T {
    let s = scenario.to_string();
    let version = dry_run.then(|| String::from_utf8(fixture("scenarios/app-dry/01-version/stdout")).unwrap());
    t_with("stick-three", "lovelace", LINUX, dry_run, move |f| {
        f.disks = s;
        f.version = version;
    })
}

/// The one run Review confirmed.
fn only_run(t: &T) -> holt_args::Load {
    assert_eq!(t.app.reviewed.len(), 1, "{:#?}", t.app.reviewed);
    t.app.reviewed[0].clone()
}

fn holt_lines(t: &T) -> Vec<(String, Vec<(String, String)>)> {
    t.seen.borrow().ran.iter().filter(|(l, _, _)| l.starts_with("/fake/bin/bko-holt load")).map(|(l, e, _)| (l.clone(), e.clone())).collect()
}

#[test]
fn lovelace_from_disks_to_review() {
    let mut t = disks_t("app-lovelace", false);
    let pass = b"two words".to_vec();
    let mut ui = Shots::new(script(&[
        &to_disks(),
        // Old disk 1's list, the Crucial, much bigger: keep the sizes.
        &[Pick(0), Pick(0), Pick(0), P],
        // OK: the plan with both named, the status.
        &[B(B_DONE), P, P],
        // The passphrase: two that differ, then two the same.
        &[Secrets(vec![pass.clone(), b"two wordz".to_vec()]), Secrets(vec![pass.clone(), pass.clone()]), P],
        // Review: a wrong serial, then the right ones.
        &[P, Serials(vec!["000000".into(), "xk7hpl".into()]), Serials(vec!["F3A9C1".into(), "XK7HPL".into()])],
        &[P, Reply::Quit],
    ]));
    assert_eq!(t.app.run(&mut ui), Exit::Quit);
    ui.done();
    assert_eq!(
        ui.titles()[3..],
        [
            "Where each disk goes",
            "Old disk 1 of 2: where does it go?",
            "This disk is much bigger",
            "Where each disk goes",
            "Passphrase 1 of 1",
            "Passphrase 1 of 1",
            "Ready to restore",
            "Ready to restore",
            "Problem: the restore stopped at \"Make partitions\""
        ]
    );
    let d = ui.text("Where each disk goes", 0);
    assert!(d.contains("Old disk 1: Samsung SSD 870 EVO 500GB, 500 GB, the system"), "{d}");
    assert!(d.contains("-> no disk chosen yet"), "{d}");
    assert!(ui.flat("Where each disk goes", 0).contains("-> the same disk, found by its serial WD-WCC4M5XK7HPL"), "{d}");
    assert!(d.contains("Not offered:  SanDisk Ultra, 32.0 GB: this stick, never a target"), "{d}");
    let one = ui.text("Old disk 1 of 2: where does it go?", 0);
    assert!(one.contains("(*) CT1000P3PSSD8           1.00 TB  serial 2318E6F3A9C1"), "{one}");
    assert!(one.contains("500 GB bigger: left free"), "{one}");
    assert!(one.contains("( ) KINGSTON SA400S37480G    480 GB  serial 50026B7782A1F3C4"), "{one}");
    assert!(one.contains("has data: 1 partition, ext4 \"photos\", 312 GB used"), "{one}");
    assert!(one.contains("20.0 GB smaller: / would shrink from 465 GB to 445 GB"), "{one}");
    assert!(one.contains("Already used:  WDC WD20EFRX-68EUZN0, for old disk 2"), "{one}");
    let chosen = ui.text("Where each disk goes", 1);
    assert!(chosen.contains("-> CT1000P3PSSD8, 1.00 TB, serial 2318E6F3A9C1") && chosen.contains("chosen by you"), "{chosen}");
    assert!(ui.flat("Passphrase 1 of 1", 1).contains("The two didn't match"), "the mismatch is said");
    let review = ui.text("Ready to restore", 0);
    assert!(review.contains("ERASED  1.00 TB  CT1000P3PSSD8          2318E6F3A9C1"), "{review}");
    assert!(review.contains("ERASED  2.00 TB  WDC WD20EFRX-68EUZN0   WD-WCC4M5XK7HPL"), "{review}");
    assert!(ui.flat("Ready to restore", 0).contains("Restore lovelace (Debian 13) from the backup of 22 Nov 2026 02:00 UTC+01:00, 61.2 GB."), "{review}");
    assert!(ui.flat("Ready to restore", 1).contains("isn't the end of its serial"), "the wrong serial is named");
    // Every run's arguments: the key file is the session's, 0600, never
    // in argv; Review's plan names both disks to erase; nothing commits.
    let dir = t.app.session.dir.clone();
    let key = dir.join("k0");
    assert_eq!(fs::metadata(&key).unwrap().permissions().mode() & 0o777, 0o600);
    assert_eq!(fs::read(&key).unwrap(), pass);
    let lines = holt_lines(&t);
    assert!(lines.iter().all(|(l, e)| !l.contains("--commit") && !l.contains("two words") && e.is_empty()), "{lines:#?}");
    let review_plan = &lines.last().unwrap().0;
    assert!(review_plan.contains(&format!("--key-file cryptroot={} --key-file cryptsrv={}", key.display(), key.display())), "{review_plan}");
    assert!(review_plan.contains("--map sda=/dev/disk/by-id/nvme-CT1000P3PSSD8_2318E6F3A9C1 --map sdb=/dev/disk/by-id/ata-WDC_WD20EFRX-68EUZN0_WD-WCC4M5XK7HPL"), "{review_plan}");
    assert!(review_plan.contains("--erase /dev/disk/by-id/nvme-CT1000P3PSSD8_2318E6F3A9C1 --erase /dev/disk/by-id/ata-WDC_WD20EFRX-68EUZN0_WD-WCC4M5XK7HPL"), "{review_plan}");
    assert!(review_plan.contains(" --through plan "), "{review_plan}");
    // The run Review confirmed: its arguments, --through close, the
    // plan's hash, --commit.
    let run = only_run(&t);
    let words: Vec<String> = holt_args::argv(&HoltCmd::Load(&run), false).iter().map(|x| x.to_string_lossy().to_string()).collect();
    let w = words.join(" ");
    let sha = t.app.plan.as_ref().unwrap().plan_sha256.clone();
    assert!(w.contains("--through close") && w.ends_with("--commit") && w.contains(&format!("--expect-plan {sha}")), "{w}");
    let dry: Vec<String> = holt_args::argv(&HoltCmd::Load(&run), true).iter().map(|x| x.to_string_lossy().to_string()).collect();
    assert!(!dry.iter().any(|x| x == "--commit") && dry.iter().any(|x| x == "--erase"), "a dry run: --erase, never --commit");
    // The layout: bko-holt's copy off the stick, the session's own file,
    // once; nothing mounted for it.
    let layout = dir.join("layout.json");
    let r = ran(&t);
    let copies: Vec<&String> = r.iter().filter(|l| l.contains(" stick --copy ")).collect();
    assert_eq!(copies, [&format!("/fake/bin/bko-holt stick --copy 0b7e3c52-8d1f-4a6e-9c3b-2f5d7a1e4b90 {} --json", layout.display())], "{r:#?}");
    assert!(!r.iter().any(|l| l.contains("mount")), "{r:#?}");
    assert_eq!(fs::read(&layout).unwrap(), fixture("scenarios/stick-layouts/03-copy/out"));
    assert_eq!(fs::metadata(&layout).unwrap().permissions().mode() & 0o777, 0o600);
    assert!(lines.iter().all(|(l, _)| l.starts_with(&format!("/fake/bin/bko-holt load {} --json", layout.display()))), "{lines:#?}");
    ui.golden("Where each disk goes", 0, "disks");
    ui.golden("Old disk 1 of 2: where does it go?", 0, "one-disk");
    ui.golden("This disk is much bigger", 0, "much-bigger");
    ui.golden("Where each disk goes", 1, "disks-chosen");
    ui.golden("Passphrase 1 of 1", 0, "passphrase");
    ui.golden("Ready to restore", 0, "review");
    drop(t.app);
    assert!(!key.exists() && !dir.exists(), "the key file is wiped with the session");
}

#[test]
fn the_kingstons_offer_then_review_says_the_shrink() {
    let mut t = disks_t("app-lovelace", false);
    let pass = b"two words".to_vec();
    let mut ui = Shots::new(script(&[
        &to_disks(),
        // Old disk 1's list, the Kingston: the offer's second plan.
        &[Pick(0), Pick(1), P, B(B_SHRINK), P],
        &[B(B_DONE), P, P, Secrets(vec![pass.clone(), pass.clone()]), P, P],
        &[B(B_COMMANDS), P, Serials(vec!["A1F3C4".into(), "XK7HPL".into()]), P, Reply::Quit],
    ]));
    assert_eq!(t.app.run(&mut ui), Exit::Quit);
    ui.done();
    let offer = ui.flat("This disk is a little smaller", 0);
    assert!(offer.contains("KINGSTON SA400S37480G, is 480 GB: 20.0 GB smaller"), "{offer}");
    assert!(offer.contains("/ will shrink from 465 GB to 445 GB, by 4.3 %"), "{offer}");
    assert!(offer.contains("Space used on /: 41.7 GB, so it fits with room to spare."), "{offer}");
    let screen = ui.text("This disk is a little smaller", 0);
    assert!(screen.contains("< Shrink the system partition >") && screen.contains("< Another disk >"), "{screen}");
    assert_eq!(t.app.fit, vec![("sda".to_string(), 5, false)]);
    let review = ui.flat("Ready to restore", 0);
    assert!(review.contains("/ (the system partition) shrinks by 4.3 %, 465 GB to 445 GB."), "{review}");
    assert!(review.contains("The encrypted disks open with the passphrase you typed."), "{review}");
    let cmds = ui.text("Every command, in order", 0);
    assert!(cmds.contains("# ---- partition ----") && cmds.contains("wipefs --all /dev/sda"), "{cmds}");
    let lines = holt_lines(&t);
    assert!(lines.iter().filter(|(l, _)| !l.contains("--only status")).skip(1).all(|(l, _)| l.contains("--fit-largest 5")), "{lines:#?}");
    let run = only_run(&t);
    assert_eq!(run.fit_largest, Some(5));
    // A shrink agreed: bko-holt takes no --reformat with --fit-largest, so
    // Review offers no Advanced.
    assert!(!ui.text("Ready to restore", 0).contains("< Advanced >"), "{}", ui.text("Ready to restore", 0));
    ui.golden("This disk is a little smaller", 0, "offer-smaller");
    ui.golden("Ready to restore", 0, "review-fit");
    ui.golden("Every command, in order", 0, "commands");
}

#[test]
fn too_small_for_the_data_and_the_users_decision() {
    let mut t = disks_t("app-lovelace-small", false);
    let mut ui = Shots::new(script(&[
        &to_disks(),
        // The Transcend can't be taken (the app checks too); the WDC 320
        // GB opens the decision, OK with nothing chosen says so.
        &[Pick(0), Pick(1), Pick(0), P, Pick(0), P, Reply::Quit],
    ]));
    assert_eq!(t.app.run(&mut ui), Exit::Quit);
    ui.done();
    let one = ui.text("Old disk 1 of 2: where does it go?", 0);
    assert!(one.contains("much smaller: / would shrink from 465 GB to 285 GB, 38.7 %"), "{one}");
    assert!(one.contains("too small: / uses 41.7 GB; the most it could get is 28.9 GB"), "{one}");
    let decide = ui.flat("This disk is much smaller: you decide", 0);
    assert!(decide.contains("To fit, the system partition (/) would shrink by 38.7 %:") && decide.contains("New size:    285 GB") && decide.contains("Space used:  41.7 GB at the backup"), "{decide}");
    let rows = ui.text("This disk is much smaller: you decide", 0);
    assert!(rows.contains("( ) Shrink / to 285 GB and restore onto WDC WD3200AAKX") && rows.contains("( ) Choose another disk"), "nothing chosen at first: {rows}");
    assert_eq!(t.app.fit, vec![("sda".to_string(), 39, true)], "the user's decision");
    assert_eq!(t.app.map.len(), 1);
    ui.golden("Old disk 1 of 2: where does it go?", 0, "too-small");
    ui.golden("This disk is much smaller: you decide", 0, "decide");
}

#[test]
fn recorded_debian13_resumes_on_the_disks_it_made() {
    // The recorded plan and the status after its partition stage: this
    // session's records show the disks were made, and status says Go on.
    let mut t = disks_t("app-rec-debian13", false);
    let pass = b"for md1".to_vec();
    let mut ui = Shots::new(script(&[
        &to_disks(),
        &[B(B_DONE), P, P, Secrets(vec![pass.clone()]), P],
        // Neither has a serial: each its size and kernel name, which tell
        // the two alike apart.
        &[B(B_GO_ON), P, Serials(vec!["12.9 GB".into(), "12.9gb".into()]), Serials(vec!["12.9 GB vdb".into(), "12.9gb VDC".into()]), P, Reply::Quit],
    ]));
    assert_eq!(t.app.run(&mut ui), Exit::Quit);
    ui.done();
    let d = ui.text("Where each disk goes", 0);
    assert!(d.contains("Old disk 1: vda, 12.9 GB, the system") && d.contains("-> vdb, 12.9 GB, no serial"), "{d}");
    assert!(ui.flat("Where each disk goes", 0).contains("vda, 23.6 GB: holds the system running now, never a target"), "{d}");
    let pp = ui.flat("Passphrase 1 of 1", 0);
    assert!(pp.contains("type the passphrase given the first time"), "a resume asks once: {pp}");
    assert!(!ui.text("Passphrase 1 of 1", 0).contains("Type it again"), "one field");
    let r = ui.text("A restore was started here before", 0);
    assert!(r.contains("[x] Make partitions") && r.contains("[ ] Set up encryption"), "{r}");
    let review = ui.flat("Ready to restore", 0);
    assert!(review.contains("Nothing is erased: the restore goes on from \"Set up encryption\""), "{review}");
    let r = ui.text("Ready to restore", 0);
    assert!(r.contains("goes on 12.9 GB  vdb ") && r.contains("no serial: type 12.9 GB vdb") && r.contains("no serial: type 12.9 GB vdc"), "{r}");
    assert!(review.contains("A disk with no serial of its own is confirmed by its size and name here instead, as its row shows: 12.9 GB vdb."), "{review}");
    let wrong = ui.flat("Ready to restore", 1);
    assert!(wrong.contains("Disk 1 has no serial of its own: type 12.9 GB vdb, its size and name."), "the size alone isn't it: {wrong}");
    let stages: Vec<holt_args::Stage> = t.app.reviewed.iter().map(|l| l.stage.clone()).collect();
    let want: Vec<holt_args::Stage> = ["assemble", "format", "mount", "restore", "boot", "close"].iter().map(|x| holt_args::Stage::Only(x.to_string())).collect();
    assert_eq!(stages, want, "Go on: each stage from status's next, alone");
    let run = t.app.reviewed[0].clone();
    assert!(t.app.reviewed.iter().all(|l| l.erase.is_empty()), "Go on erases nothing");
    assert_eq!(t.seen.borrow().loads, ["10-plan", "11-plan", "14-status", "15-status", "13-plan"]);
    assert!(run.commit && run.expect_plan.is_some());
    ui.golden("Where each disk goes", 0, "disks-recorded");
    ui.golden("A restore was started here before", 0, "resume");
    ui.golden("Ready to restore", 0, "review-go-on");
}

#[test]
fn advanced_changes_a_filesystem_and_clears_the_serials() {
    let mut t = disks_t("app-lovelace", false);
    let pass = b"two words".to_vec();
    let mut ui = Shots::new(script(&[
        &to_disks(),
        &[Pick(0), Pick(0), Pick(0), P, B(B_DONE), P, P, Secrets(vec![pass.clone(), pass.clone()]), P, P],
        &[B(B_ADVANCED), Choices(vec![0, 0, 1]), P],
        // Again: XFS shown chosen; Undo all puts every row back.
        &[B(B_ADVANCED), B(B_UNDO), Choices(vec![0, 0, 0]), P, Reply::Quit],
    ]));
    assert_eq!(t.app.run(&mut ui), Exit::Quit);
    ui.done();
    assert!(ui.text("Advanced: restore onto another filesystem", 1).contains("/srv       ext4    ->  XFS      changed"), "{}", ui.text("Advanced: restore onto another filesystem", 1));
    let undone = ui.text("Advanced: restore onto another filesystem", 2);
    assert!(undone.contains("/srv       ext4    ->  ext4     as it was"), "Undo all: {undone}");
    assert!(t.app.reformat.is_empty(), "{:?}", t.app.reformat);
    assert!(!holt_lines(&t).last().unwrap().0.contains("--reformat"));
    let adv = ui.text("Advanced: restore onto another filesystem", 0);
    assert!(adv.contains("/          ext4    ->  ext4     as it was"), "{adv}");
    assert!(adv.contains("/boot      ext4    ->  ext4     can't change: see below"), "{adv}");
    assert!(adv.contains("/srv       ext4    ->  ext4     as it was"), "{adv}");
    let after = ui.flat("Ready to restore", 1);
    assert!(after.contains("/srv is made as XFS, not ext4."), "{after}");
    assert!(holt_lines(&t).iter().any(|(l, _)| l.contains("--reformat /srv=xfs")));
    ui.golden("Advanced: restore onto another filesystem", 0, "advanced-fs");
}

#[test]
fn a_dry_run_from_disks_to_review_never_commits() {
    let mut t = disks_t("app-lovelace", true);
    let pass = b"two words".to_vec();
    let mut ui = Shots::new(script(&[
        &to_disks(),
        &[Pick(0), Pick(0), Pick(0), P, B(B_DONE), P, P, Secrets(vec![pass.clone(), pass.clone()]), P, P],
        &[Serials(vec!["F3A9C1".into(), "XK7HPL".into()]), P, Reply::Quit],
    ]));
    assert_eq!(t.app.run(&mut ui), Exit::Quit);
    ui.done();
    // Every screen's bko-holt command went through holt_args: lock 1, no
    // --commit; lock 2, BKO_HOLT_NO_COMMIT=1.
    let seen = t.seen.borrow();
    let holt: Vec<_> = seen.ran.iter().filter(|(l, _, _)| l.starts_with("/fake/bin/bko-holt")).collect();
    assert!(holt.len() >= 8, "{holt:#?}");
    for (l, env, _) in &holt {
        assert!(!l.contains("--commit"), "{l}: lock 1");
        assert!(env.contains(&("BKO_HOLT_NO_COMMIT".to_string(), "1".to_string())), "{l}: lock 2");
    }
    assert!(holt.last().unwrap().0.contains("--erase"), "a dry run's Review passes --erase (J6)");
    let run = only_run(&t);
    assert!(run.commit, "Restore asks for --commit");
    let a: Vec<String> = holt_args::argv(&HoltCmd::Load(&run), true).iter().map(|x| x.to_string_lossy().to_string()).collect();
    assert!(!a.contains(&"--commit".to_string()) && a.contains(&"--expect-plan".to_string()), "{a:?}");
    let r = ui.text("Ready to restore", 0);
    assert!(r.contains("< Show what would happen >") && r.contains("DRY RUN: nothing will be written"), "{r}");
    // The Restore's run: a direct child, under the lock, no --commit.
    let started = &seen.started;
    assert_eq!(started.len(), 1, "{started:#?}");
    assert!(!started[0].0.contains("--commit") && started[0].0.contains("--through close"), "{}", started[0].0);
    assert!(started[0].1.contains(&("BKO_HOLT_NO_COMMIT".to_string(), "1".to_string())) && started[0].2 == sys::How::Child);
}

#[test]
fn plain_mode_disks_transcripts() {
    for (name, scen, typed) in [
        (
            "disks",
            "app-lovelace",
            // Disks: old disk 1, the Crucial, keep (Enter alone), these
            // are right; the passphrase twice; the serials; restore.
            "1\ncorrect horse\n1\n1\n1\n\no\ntwo words\ntwo words\nf3a9c1\nxk7hpl\nr\nl\n",
        ),
        ("too-small", "app-lovelace-small", "1\ncorrect horse\n1\n1\n2\n1\n\n1\nb\n"),
    ] {
        let mut t = disks_t(scen, false);
        let mut pl = Plain::new(FakeLine::typed(typed), Lines::Utf8);
        let r = t.app.run(&mut pl);
        let out = pl.into_line().out;
        assert!(matches!(r, Exit::Done(0) | Exit::Quit), "{name}: {r:?}\n{out}");
        assert!(!out.contains('\x1b'), "no escape sequences at all");
        assert!(!out.contains("two words") && !out.contains("correct horse"), "nothing typed for a secret shows");
        golden(&format!("tests/screens/app-plain-{name}.txt"), &out);
        if name == "disks" {
            assert!(out.contains("  1) change where old disk 1 goes   2) change where old disk 2 goes"), "{out}");
            assert!(out.contains("serial 2 3 1 8 E 6 F 3 A 9 C 1"), "{out}");
            assert!(out.contains("Disk 1 confirmed.") && out.contains("Disk 2 confirmed."), "{out}");
        } else {
            assert!(out.contains("/ holds 41.7 GB; the most it could get on this disk is 28.9 GB."), "{out}");
        }
    }
}

/// lovelace's stick, its keyboard German and its start-up prompt US (RT10l's
/// `boot_prompt`, as rescue-screens.md's mock has it: initramfs-tools
/// without KEYMAP=y).
fn german_boot_stick() -> String {
    String::from_utf8(fixture("scenarios/stick-three/02-stick/stdout")).unwrap().replacen(
        r#""keyboard":null"#,
        r#""keyboard":{"canonical":{"layout":"de","variant":null,"from":"xkb"},"boot_prompt":{"where":"initramfs","us_fallback":true,"native":null},"desktop":[]}"#,
        1,
    )
}

#[test]
fn the_keyboard_at_start_up_is_offered_before_the_passphrase() {
    let stick = german_boot_stick();
    let mut t = t_with("stick-three", "lovelace", LINUX, false, |f| f.stick = Some(stick));
    let pass = b"two words".to_vec();
    let mut ui = Shots::new(script(&[
        // German for the password, then on to Disks.
        &start(),
        &[Pick(0), Reply::Yes, Sec(PASSWORD.to_vec()), P, P, Pick(0), P],
        &[Pick(0), Pick(0), Pick(0), P, B(B_DONE), P, P],
        // Back from the offer: Disks, not Snapshot.
        &[Back, P, B(B_DONE), P, P],
        // The offer again; switch.
        &[Reply::Yes, Secrets(vec![pass.clone(), pass.clone()]), P, P, Reply::Quit],
    ]));
    assert_eq!(t.app.run(&mut ui), Exit::Quit);
    ui.done();
    let titles = ui.titles();
    let at = titles.iter().position(|x| x == "Keyboard for the disk passphrase").expect("offered");
    assert_eq!(titles[at + 1], "Where each disk goes", "Esc is Back, to Disks: {titles:?}");
    assert_eq!(titles.iter().filter(|x| *x == "Keyboard for the disk passphrase").count(), 2);
    let q = ui.flat("Keyboard for the disk passphrase", 0);
    assert!(q.contains("When lovelace starts, it asks for the disk passphrase before its German layout is loaded, so the keyboard acts as English (US) there."), "{q}");
    assert!(q.contains("With English (US) on a German keyboard, Y and Z are swapped and most symbols move."), "{q}");
    assert!(q.contains("< Switch to English (US) >") && q.contains("< Keep German >"), "{q}");
    let pp = ui.flat("Passphrase 1 of 1", 0);
    assert!(pp.contains("Keyboard: English (US), as at lovelace's start-up prompt."), "{pp}");
    assert_eq!(t.app.keyboard.set, keyboard::Xkb::of(&keyboard::US));
    assert!(t.seen.borrow().layouts.last().is_some_and(|l| l == "-layout us"), "{:?}", t.seen.borrow().layouts);
    ui.golden("Keyboard for the disk passphrase", 0, "boot-keyboard");
}

#[test]
fn start_again_asks_the_passphrase_twice_and_wipes_first() {
    let mut t = disks_t("app-rec-debian13", false);
    let (once, new) = (b"for md1".to_vec(), b"a new one".to_vec());
    let mut ui = Shots::new(script(&[
        &to_disks(),
        &[B(B_DONE), P, P, Secrets(vec![once.clone()]), P],
        // Start again: the containers are made anew, so twice.
        &[B(B_START_AGAIN), Secrets(vec![new.clone(), new.clone()])],
        &[P, Serials(vec!["12.9 GB vdb".into(), "12.9 GB vdc".into()]), P, Reply::Quit],
    ]));
    assert_eq!(t.app.run(&mut ui), Exit::Quit);
    ui.done();
    assert!(!ui.text("Passphrase 1 of 1", 0).contains("Type it again"), "the resume's: once");
    let pp = ui.text("Passphrase 1 of 1", 1);
    assert!(pp.contains("Type it again") && ui.flat("Passphrase 1 of 1", 1).contains("You will type it every time the computer starts"), "{pp}");
    let dir = t.app.session.dir.clone();
    assert_eq!(fs::read(dir.join("k0")).unwrap(), new, "the new one is on file");
    // Review: Start again's wiping run, every disk ERASED, no remaking.
    let r = ui.text("Ready to restore", 0);
    assert!(r.contains("ERASED  12.9 GB  vdb") && r.contains("ERASED  12.9 GB  vdc") && !r.contains("WRITTEN"), "{r}");
    assert!(ui.flat("Ready to restore", 0).contains("These disks will be ERASED"), "{r}");
    assert_eq!(t.seen.borrow().loads, ["10-plan", "11-plan", "14-status", "15-status", "12-plan"]);
    let review_plan = holt_lines(&t).last().unwrap().0.clone();
    // A plan, --through plan (`--only partition` would be that stage's dry
    // run under --json), every target to be erased.
    assert!(review_plan.contains(" --through plan ") && review_plan.matches(" --erase ").count() == 2, "{review_plan}");
    // The runs: nothing open, so --only partition, then --through close,
    // each with --erase, --commit and the plan's hash.
    let sha = t.app.plan.as_ref().unwrap().plan_sha256.clone();
    let stages: Vec<holt_args::Stage> = t.app.reviewed.iter().map(|l| l.stage.clone()).collect();
    assert_eq!(stages, [holt_args::Stage::Only("partition".into()), holt_args::Stage::Through("close".into())]);
    for l in &t.app.reviewed {
        assert!(l.commit && l.erase.len() == 2 && l.expect_plan.as_deref() == Some(sha.as_str()), "{l:?}");
        let dry: Vec<String> = holt_args::argv(&HoltCmd::Load(l), true).iter().map(|x| x.to_string_lossy().to_string()).collect();
        assert!(!dry.contains(&"--commit".to_string()), "{dry:?}");
    }
    // Something open: --only close comes first.
    let l = t.app.full_load(holt_args::Stage::Plan, true).unwrap();
    let runs = t.app.runs_of(&l, &sha, true);
    assert_eq!(runs.first().map(|r| r.stage.clone()), Some(holt_args::Stage::Only("close".into())));
    assert_eq!(runs.len(), 3);
}

#[test]
fn only_start_again_after_a_stopped_copy() {
    let mut t = disks_t("app-rec-debian13-again", false);
    let (once, new) = (b"for md1".to_vec(), b"a new one".to_vec());
    let mut ui = Shots::new(script(&[
        &to_disks(),
        &[B(B_DONE), P, P, Secrets(vec![once.clone()]), P],
        // Go on isn't there: its button number does nothing.
        &[B(B_GO_ON), B(B_START_AGAIN), Secrets(vec![new.clone(), new.clone()])],
        &[P, Serials(vec!["12.9 GB vdb".into(), "12.9 GB vdc".into()]), P, Reply::Quit],
    ]));
    assert_eq!(t.app.run(&mut ui), Exit::Quit);
    ui.done();
    let r = ui.flat("A restore was started here before", 0);
    assert!(r.contains("It can't be gone on with: vdb: this session's restore began there"), "{r}");
    assert!(!r.contains("< Go on >") && r.contains("< Start again >"), "{r}");
    assert!(ui.text("A restore was started here before", 0).contains("[ ] Copy your files back"), "{}", ui.text("A restore was started here before", 0));
    // Its open container: ours_busy is what --only close lets go of.
    let review = ui.flat("Ready to restore", 0);
    assert!(review.contains("No problems found."), "{review}");
    let stages: Vec<holt_args::Stage> = t.app.reviewed.iter().map(|l| l.stage.clone()).collect();
    assert_eq!(stages, [holt_args::Stage::Only("close".into()), holt_args::Stage::Only("partition".into()), holt_args::Stage::Through("close".into())]);
    ui.golden("A restore was started here before", 0, "resume-only-again");
}

#[test]
fn the_size_screens_other_rows() {
    for grow in [false, true] {
        let mut t = disks_t("app-lovelace-sizes", false);
        let mut ui = Shots::new(script(&[
            &to_disks(),
            // Old disk 1: the Kingston 200 GB (beyond the limit: only
            // another disk), the Toshiba (can't be taken), then the
            // Samsung 525 GB's offer.
            &[Pick(0), Pick(1), Pick(0), Pick(2), Pick(0), P],
            &[B(if grow { B_SHRINK } else { B_KEEP }), P, Reply::Quit],
        ]));
        assert_eq!(t.app.run(&mut ui), Exit::Quit);
        ui.done();
        // Each time back, the list shows the row last picked; a long
        // line wraps, never cut.
        let one = ui.flat("Old disk 1 of 2: where does it go?", 0);
        assert!(one.contains("25.0 GB bigger: / would grow from 465 GB to 490 GB (you choose)"), "{one}");
        let one = ui.flat("Old disk 1 of 2: where does it go?", 1);
        assert!(one.contains("KINGSTON SA400S37200G    200 GB  serial 50026B7682B05D11") && one.contains("much smaller: / would shrink from 465 GB to 165 GB, 64.5 %"), "{one}");
        let one = ui.flat("Old disk 1 of 2: where does it go?", 2);
        assert!(one.contains("too small: / can't be made smaller here"), "{one}");
        let beyond = ui.flat("This disk is much smaller: you decide", 0);
        assert!(beyond.contains("can't shrink a partition this far yet") && !beyond.contains("Shrink /"), "{beyond}");
        let offer = ui.text("This disk is a little bigger", 0);
        assert!(offer.contains("< Grow the system partition >") && offer.contains("< Keep the old sizes >"), "{offer}");
        assert_eq!(t.app.map.len(), 1);
        if grow {
            assert_eq!(t.app.fit, vec![("sda".to_string(), 6, false)]);
        } else {
            assert!(t.app.fit.is_empty(), "kept: {:?}", t.app.fit);
            ui.golden("Old disk 1 of 2: where does it go?", 0, "one-disk-sizes");
            ui.golden("Old disk 1 of 2: where does it go?", 2, "one-disk-cant-resize");
            ui.golden("This disk is a little bigger", 0, "offer-bigger");
            ui.golden("This disk is much smaller: you decide", 0, "beyond-limit");
        }
    }
}

#[test]
fn a_layout_bko_holt_wont_copy_is_said_in_its_words() {
    // stick-three's lovelace: today's system.json, with no layout in it
    // (recorded: bko-holt's no_layout refusal).
    let mut t = disks_t("stick-three", false);
    let mut ui = Shots::new(script(&[&to_disks(), &[Reply::Quit]]));
    assert_eq!(t.app.run(&mut ui), Exit::Quit);
    let p = ui.flat("Problem: the old disks' layout isn't here", 0);
    assert!(p.contains("bko-holt couldn't copy the layout off the BACKOTTER partition:") && p.contains("carries no disk layout"), "{p}");
    assert!(!p.contains("bko-holt stick --copy:"), "bko-holt named once: {p}");
    assert!(!t.app.session.path("layout.json").exists(), "nothing left for load");
    assert!(t.app.layout.is_none());
    assert!(!ran(&t).iter().any(|l| l.contains("bko-holt load")), "no plan without a layout");
}

#[test]
fn a_copy_that_isnt_as_bko_holt_says_is_wiped() {
    // A stick-copy document with OUT written, but not as it says: another
    // size, another machine, or an exit that isn't 0.
    let good = String::from_utf8(fixture("scenarios/stick-layouts/03-copy/stdout")).unwrap();
    let bytes = fixture("scenarios/stick-layouts/03-copy/out").len();
    let other = "2d3e4f5a-6b7c-4d8e-9fa0-b1c2d3e4f5a6";
    for (code, doc) in [
        (0, good.replace(&format!("\"bytes\":{bytes}"), &format!("\"bytes\":{}", bytes - 1))),
        (0, good.replace("0b7e3c52-8d1f-4a6e-9c3b-2f5d7a1e4b90\",\"path\"", &format!("{other}\",\"path\""))),
        (1, good.clone()),
    ] {
        assert!(code != 0 || doc != good, "the document is changed");
        let mut t = t_with("stick-three", "lovelace", LINUX, false, |f| {
            f.disks = "app-lovelace".into();
            f.copy = Some((code, doc.clone()));
        });
        let mut ui = Shots::new(script(&[&to_disks(), &[Reply::Quit]]));
        assert_eq!(t.app.run(&mut ui), Exit::Quit);
        let p = ui.flat("Problem: the old disks' layout isn't here", 0);
        assert!(p.contains(super::strings::LAYOUT_NOT_WRITTEN), "{p}");
        assert!(!t.app.session.path("layout.json").exists(), "wiped: {code} {doc}");
        assert!(t.app.layout.is_none());
        assert!(!ran(&t).iter().any(|l| l.contains("bko-holt load")), "no plan without a layout");
    }
    // As it says: taken.
    let mut t = t_with("stick-three", "lovelace", LINUX, false, |f| {
        f.disks = "app-lovelace".into();
        f.copy = Some((0, good.clone()));
    });
    let mut ui = Shots::new(script(&[&to_disks(), &[Reply::Quit]]));
    t.app.run(&mut ui);
    assert!(t.app.layout.is_some() && t.app.session.path("layout.json").exists());
}

/// stick-layouts' copy document saying its layout kept N LUKS2 headers.
fn copy_with_headers(n: u64) -> String {
    let good = String::from_utf8(fixture("scenarios/stick-layouts/03-copy/stdout")).unwrap();
    assert!(!good.contains("luks_headers"), "recorded before open question 12");
    good.replace("\"system_sha256\"", &format!("\"luks_headers\":{n},\"system_sha256\""))
}

fn headers_t(luks: Luks) -> T {
    t_with("stick-three", "lovelace", LINUX, false, |f| {
        f.disks = "app-lovelace".into();
        f.copy = Some((0, copy_with_headers(2)));
        f.luks = Some(luks);
    })
}

const HEADER_FILES: [&str; 2] = ["luks2-header-bc000000-0000-4000-8000-00000000000a.img", "luks2-header-bc000000-0000-4000-8000-00000000000c.img"];

#[test]
fn kept_headers_are_fetched_before_the_first_plan() {
    let files: Vec<(String, Vec<u8>)> = HEADER_FILES.iter().map(|f| (f.to_string(), format!("LUKS header {f}").into_bytes())).collect();
    let mut t = headers_t(Luks::Files(files.clone()));
    // The fetch's gauge, the plan's; then Back to Snapshot, another
    // backup: its headers fetched again.
    let mut ui = Shots::new(script(&[&start(), &[Pick(0), Sec(PASSWORD.to_vec()), P, P, Pick(0), P, P, Back, Pick(1), P, Reply::Quit]]));
    assert_eq!(t.app.run(&mut ui), Exit::Quit);
    let dir = t.app.session.dir.clone();
    let (layout, h, pw) = (dir.join("layout.json"), dir.join("h"), dir.join("password"));
    let r = ran(&t);
    let fetches: Vec<&String> = r.iter().filter(|l| l.contains(" luks-headers ")).collect();
    assert_eq!(fetches.len(), 2, "{r:#?}");
    let first = format!("/fake/bin/bko-holt luks-headers {} {} --repo sftp://nas.local/backotter/lovelace --password-file {} --snapshot ", layout.display(), h.display(), pw.display());
    assert!(fetches[0].starts_with(&first) && fetches[0].ends_with(" --json"), "{}", fetches[0]);
    assert!(fetches[0].contains("--snapshot a7f3c901"), "the newest backup's: {}", fetches[0]);
    assert!(fetches[1].starts_with(&first) && fetches[0] != fetches[1], "another snapshot's: {}", fetches[1]);
    // Before the first plan, which (as every load) names the directory.
    let at = |pat: &str| r.iter().position(|l| l.contains(pat)).unwrap();
    assert!(at(" luks-headers ") < at("bko-holt load"), "{r:#?}");
    let dirs = t.seen.borrow().header_dirs.clone();
    assert!(!dirs.is_empty() && dirs.iter().all(|d| d.as_deref() == Some(h.to_str().unwrap())), "{dirs:?}");
    let l = t.app.full_load(holt_args::Stage::Plan, true).unwrap();
    assert_eq!(l.luks_header_dir.as_deref(), Some(h.as_path()));
    // bko-holt's files, private, kept for the session; then wiped with it.
    assert_eq!(fs::metadata(&h).unwrap().permissions().mode() & 0o777, 0o700);
    for (f, b) in &files {
        assert_eq!(&fs::read(h.join(f)).unwrap(), b);
        assert_eq!(fs::metadata(h.join(f)).unwrap().permissions().mode() & 0o777, 0o600);
    }
    // Another machine or snapshot: gone, fetched again when needed.
    t.app.forget_disks();
    assert!(!h.exists() && t.app.headers.is_none());
    assert_eq!(t.app.full_load(holt_args::Stage::Plan, true).unwrap().luks_header_dir, None);
    drop(t.app);
    assert!(!dir.exists(), "the session's directory is gone, the headers' with it");
}

#[test]
fn headers_that_cant_be_fetched_stop_on_disks() {
    // bko-holt's refusal, in its words; nothing planned, nothing left.
    let refusal = r#"{"format":"bko-holt-error","schema":1,"version":"VERSION","generated":"2026-11-26T10:00:00Z","dry_run":false,"exit":1,"code":"header_mismatch","text":"bko-holt luks-headers: vda5's LUKS2 header (a7f3c901:/.bko-holt/luks-headers/x.img) isn't the header the dump kept: its SHA-256 isn't the one the dump recorded"}"#;
    let version = crate::json::parse(&String::from_utf8(fixture("scenarios/stick-three/01-version/stdout")).unwrap()).unwrap().get_str("version").unwrap().to_string();
    for (luks, words) in [
        (Luks::Says(1, format!("{}\n", refusal.replace("VERSION", &version))), "vda5's LUKS2 header (a7f3c901:/.bko-holt/luks-headers/x.img) isn't the header the dump kept"),
        // One header where two were kept: not as the layout says.
        (Luks::Files(vec![(HEADER_FILES[0].to_string(), b"LUKS".to_vec())]), super::strings::HEADERS_NOT_WRITTEN),
    ] {
        let mut t = headers_t(luks);
        let mut ui = Shots::new(script(&[&start(), &[Pick(0), Sec(PASSWORD.to_vec()), P, P, Pick(0), P, Reply::Quit]]));
        assert_eq!(t.app.run(&mut ui), Exit::Quit);
        let p = ui.flat("Problem: the encryption headers couldn't be fetched", 0);
        assert!(p.contains("bko-holt was asked for the old disks' encryption headers, and said:") && p.contains(words), "{p}");
        assert!(!p.contains("bko-holt luks-headers:"), "bko-holt named once: {p}");
        assert!(p.contains("choose the newest backup"), "{p}");
        assert!(!t.app.session.path("h").exists() && t.app.headers.is_none(), "nothing left");
        assert!(!ran(&t).iter().any(|l| l.contains("bko-holt load")), "no plan without the headers");
    }
}

#[test]
fn a_layout_that_kept_none_fetches_nothing() {
    let mut t = t_with("stick-three", "lovelace", LINUX, false, |f| {
        f.disks = "app-lovelace".into();
        f.copy = Some((0, copy_with_headers(0)));
    });
    let mut ui = Shots::new(script(&[&to_disks(), &[Reply::Quit]]));
    t.app.run(&mut ui);
    assert!(t.app.layout.is_some() && t.app.layout_headers == 0);
    assert!(!ran(&t).iter().any(|l| l.contains(" luks-headers ")), "the fake panics on one");
    assert!(t.seen.borrow().header_dirs.iter().all(Option::is_none));
}

#[test]
fn problems_inside_disks_go_back_to_disks() {
    // The Kingston's second plan fails: its Back is the overview.
    let mut t = disks_t("app-lovelace-trouble", false);
    let mut ui = Shots::new(script(&[&to_disks(), &[Pick(0), Pick(1), P, Back, P, Reply::Quit]]));
    assert_eq!(t.app.run(&mut ui), Exit::Quit);
    ui.done();
    let titles = ui.titles();
    assert_eq!(titles[titles.len() - 2..], ["Problem: bko-holt couldn't plan the restore", "Where each disk goes"], "{titles:?}");
    assert_eq!(t.app.step, Step::Disks);
    // A key this stick can't ask for: said, never skipped; Back is Disks.
    let mut t = disks_t("app-lovelace-trouble", false);
    let mut ui = Shots::new(script(&[&to_disks(), &[Pick(0), Pick(0), Pick(0), P, B(B_DONE), P, P, Back, P, Reply::Quit]]));
    assert_eq!(t.app.run(&mut ui), Exit::Quit);
    ui.done();
    let titles = ui.titles();
    assert_eq!(titles[titles.len() - 1], "Where each disk goes", "{titles:?}");
    let k = ui.flat("Problem: a disk this stick can't unlock", 0);
    assert!(k.contains("The encrypted container cryptroot needs a key this bko-rescue can't ask for.") && k.contains("key file kept on another disk"), "{k}");
    assert!(t.app.keys.is_empty() && !titles.iter().any(|x| x.starts_with("Passphrase")));
}

#[test]
fn a_plan_problem_at_review_has_its_help_and_fix() {
    let mut t = disks_t("app-lovelace-changed", false);
    let pass = b"two words".to_vec();
    let mut ui = Shots::new(script(&[
        &to_disks(),
        &[Pick(0), Pick(0), Pick(0), P, B(B_DONE), P, P, Secrets(vec![pass.clone(), pass.clone()]), P, P],
        // Restore: the problem's help screen; its fix goes to Disks.
        &[Serials(vec!["F3A9C1".into(), "XK7HPL".into()]), B(20), P, Reply::Quit],
    ]));
    assert_eq!(t.app.run(&mut ui), Exit::Quit);
    ui.done();
    let review = ui.flat("Ready to restore", 0);
    assert!(review.contains("Problem: --expect-plan"), "{review}");
    let help = ui.text("Problem: the plan has a problem", 0);
    assert!(ui.flat("Problem: the plan has a problem", 0).contains("a command or a disk's identity changed since the plan was reviewed"), "{help}");
    assert!(help.contains("< Go back and change it >"), "{help}");
    assert_eq!(ui.titles().last().map(String::as_str), Some("Where each disk goes"));
    assert_eq!(t.app.step, Step::Disks);
    assert_eq!(t.app.history.last(), Some(&Step::Snapshot), "Passphrases and Review gone from the history");
    assert!(t.app.serials.is_empty() && t.app.reviewed.is_empty());
    ui.golden("Problem: the plan has a problem", 0, "review-problem");
}

#[test]
fn review_blocks_a_disk_it_cant_show() {
    let mut t = disks_t("app-lovelace-unshown", false);
    let pass = b"two words".to_vec();
    let mut ui = Shots::new(script(&[
        &to_disks(),
        &[Pick(0), Pick(0), Pick(0), P, B(B_DONE), P, P, Secrets(vec![pass.clone(), pass.clone()]), P, P],
        &[Serials(vec!["XK7HPL".into()]), Reply::Quit],
    ]));
    assert_eq!(t.app.run(&mut ui), Exit::Quit);
    let r = ui.flat("Ready to restore", 0);
    assert!(r.contains("bko-holt's plan writes nvme0n1, which isn't among the disks found here, so it can't be confirmed"), "{r}");
    assert!(ui.flat("Ready to restore", 1).contains("bko-holt's plan writes nvme0n1"), "Restore says why");
    assert!(t.app.reviewed.is_empty(), "never a run");
}

#[test]
fn review_at_its_fullest() {
    // rescue-screens.md's fullest case: three disks, the system disk on
    // the Kingston with a shrink.  Its XFS line isn't there: bko-holt
    // refuses --reformat with --fit-largest, so a shrink and another
    // filesystem can't both be chosen.
    let disk = |node: &str, size: u64, model: &str, serial: &str| plan::Disk { node: node.into(), size_bytes: size, model: Some(model.into()), serial: Some(serial.into()), ..plan::Disk::default() };
    let disks = [
        disk("sda", 480_103_981_056, "KINGSTON SA400S37480G", "50026B7782A1F3C4"),
        disk("nvme0n1", 1_000_204_886_016, "CT1000P3PSSD8", "2318E6F3A9C1"),
        disk("sdb", 2_000_398_934_016, "WDC WD20EFRX-68EUZN0", "WD-WCC4M5XK7HPL"),
    ];
    let rows: Vec<crate::ui::SerialRow> = disks.iter().enumerate().map(|(i, c)| super::review::serial_row(i + 1, s::WORD_ERASED, c).0).collect();
    let text = format!("{}\n\n{}", s::review_head("lovelace", Some("Debian 13"), "22 Nov 2026 02:00 CET", Some("61.2 GB"), false), s::REVIEW_ERASE);
    let after = [
        format!("{:<10}{}", s::CHANGES, s::shrinks("/", "4.3", "465 GB", "445 GB", false)),
        format!("{:<10}{}", "", s::CHANGE_PASSPHRASE),
        s::own_disk_review("WDC WD20EFRX-68EUZN0", "lovelace", "/srv", "22 Nov 2026"),
        format!("{}  {}", s::not_touched(s::THIS_STICK), s::NO_PROBLEMS),
        s::NOTHING_CHANGED.to_string(),
    ];
    let mut r = crate::ui::Review {
        chrome: Chrome::ours(s::SCR_REVIEW, s::REVIEW_TITLE),
        text: Clean::text(&text, cap::PROBLEM),
        rows,
        after: Clean::text(&after.join("\n"), cap::PROBLEM),
        blocked: None,
        notice: None,
        plain: Clean::default(),
        buttons: vec![Button::new(s::BTN_RESTORE, None, crate::ui::Act::Ok), Button::other(s::BTN_COMMANDS, Some('c'), 40), Button::back()],
    };
    r.chrome.keys = Some(Clean::ours(s::REVIEW_KEYS));
    let mut ui = Shots::new(Vec::new());
    ui.shot("fullest", Dialog::review(&r, &[]));
    let screen = ui.text("fullest", 0);
    for want in ["50026B7782A1F3C4 [      ]", "2318E6F3A9C1     [      ]", "WD-WCC4M5XK7HPL  [      ]", "shrinks by 4.3 %", "Nothing has been changed on any disk yet."] {
        assert!(screen.contains(want), "{want}: {screen}");
    }
    ui.golden("fullest", 0, "review-fullest");
}

// ---- RT12 part 2: Restore, Done, Failure ----

use super::restore::{B_CANCEL, B_COMMANDS as B_SHOW, B_FIX as R_FIX, B_HELP, B_LEAVE, B_RAW as B_RAW_OUT, B_RESTART, B_STOP, B_TRY as R_TRY};

/// app-rec-*: to Review, its serials typed: up to the Restore's first
/// tick.
fn to_restore(pass: &[u8]) -> Vec<Reply> {
    script(&[
        &to_disks(),
        &[B(B_DONE), P, P, Secrets(vec![pass.to_vec(), pass.to_vec()]), P, P],
        &[Serials(vec!["12.9 GB vdb".into(), "12.9 GB vdc".into()])],
    ])
}

fn restore_t(scenario: &str, dry_run: bool) -> T {
    // Every call answered in turn, as fake-bko-holt's FAKE_BKO_HOLT_STATE.
    let s = scenario.to_string();
    let version = dry_run.then(|| String::from_utf8(fixture("scenarios/app-dry/01-version/stdout")).unwrap());
    t_with("stick-three", "lovelace", LINUX, dry_run, move |f| {
        f.disks = s;
        f.version = version;
        f.in_turn = true;
    })
}

#[test]
fn a_restore_runs_to_done_and_saves_its_log() {
    let mut t = restore_t("app-rec-restore", false);
    let mut ui = Shots::new(script(&[&to_restore(b"for md1"), &[P, B(B_RESTART)]]));
    assert_eq!(t.app.run(&mut ui), Exit::Done(0));
    ui.done();
    let sha = "d96eb1cb050075fea9a149802f66715587293889b6fbaa05015e53c0edfd54b1";
    let seen = t.seen.borrow();
    // One run: Review's arguments, --through close, the plan's hash,
    // --commit, detached; no BKO_HOLT_NO_COMMIT.
    assert_eq!(seen.started.len(), 1, "{:#?}", seen.started);
    let (line, env, how) = &seen.started[0];
    assert!(line.contains(" --through close ") && line.contains(&format!("--expect-plan {sha}")) && line.ends_with(" --commit"), "{line}");
    assert!(line.matches(" --erase ").count() == 2 && *how == sys::How::Detached && env.is_empty(), "{line} {env:?}");
    let done = ui.text("lovelace is restored", 0);
    assert!(done.contains("[x] Make partitions           [x] Set up RAID and encryption"), "{done}");
    assert!(done.contains("[x] Finish") && done.contains("< Restart >") && done.contains("< Power off >"), "{done}");
    let flat = ui.flat("lovelace is restored", 0);
    assert!(flat.contains("At the next start, type the passphrase you chose for the disks."), "{flat}");
    assert!(flat.contains("The log is on the stick, in lovelace's folder.") && flat.contains("Remove the stick, then press Enter to restart into lovelace."), "{flat}");
    // The log: gzipped, on the stick's partition, mounted read-write for
    // it, in the machine's folder.
    let log = t.base.0.join("stick/backotter/machines/0b7e3c52-8d1f-4a6e-9c3b-2f5d7a1e4b90/restore-logs/20261123T142630Z.log.gz");
    assert_eq!(fs::read(&log).unwrap(), format!("gzip:{FAKE_LOG}").into_bytes());
    assert!(seen.ran.iter().any(|(l, _, _)| l.starts_with("/fake/bin/mount -t vfat -o rw,nosuid,nodev,noexec /dev/sdc3 ")), "the partition mounted read-write for the log");
    assert_eq!(seen.offs, ["reboot"]);
    drop(seen);
    ui.golden("lovelace is restored", 0, "done");
    // Its files are its own again: run.json gone, the session wiped with it.
    assert!(!t.base.0.join("run.json").exists());
    let dir = t.app.session.dir.clone();
    drop(t.app);
    assert!(!dir.exists(), "the session's files wiped once the load is gone");
}

#[test]
fn restart_with_the_stick_in_asks_first() {
    let mut t = restore_t("app-rec-restore", false);
    t.seen.borrow_mut().exists.push(PathBuf::from("/dev/sdc3"));
    let mut ui = Shots::new(script(&[&to_restore(b"for md1"), &[P, B(B_RESTART), Back, B(B_RESTART), B(super::restore::B_ANYWAY)]]));
    assert_eq!(t.app.run(&mut ui), Exit::Done(0));
    ui.done();
    assert!(ui.flat("The stick is still in", 0).contains("Remove the stick first, or the computer may start from it again."));
    assert_eq!(t.seen.borrow().offs, ["reboot"], "once, after Restart anyway");
}

#[test]
fn a_dry_run_runs_every_command_as_would_and_writes_nothing() {
    let mut t = restore_t("app-rec-dry", true);
    let mut ui = Shots::new(script(&[&to_restore(b"for md1"), &[P, B(B_SHOW), P, B(B_LEAVE)]]));
    assert_eq!(t.app.run(&mut ui), Exit::Done(0));
    ui.done();
    let seen = t.seen.borrow();
    let (line, env, how) = &seen.started[0];
    assert!(!line.contains("--commit") && line.contains(" --through close ") && line.matches(" --erase ").count() == 2, "lock 1, and J6's --erase: {line}");
    assert!(env.contains(&("BKO_HOLT_NO_COMMIT".to_string(), "1".to_string())) && *how == sys::How::Child, "lock 2, a direct child");
    let done = ui.flat("The dry run of lovelace is done", 0);
    assert!(done.contains("Nothing was written: 92 commands would have run, and none did."), "{done}");
    let screen = ui.text("The dry run of lovelace is done", 0);
    assert!(screen.contains("DRY RUN: nothing will be written") && screen.contains("[~] Make it bootable") && screen.contains("[~] Finish"), "boot and close too (J11): {screen}");
    assert!(!screen.contains("Restart") && !screen.contains("Power off"), "{screen}");
    let cmds = ui.text("The commands that would run", 0);
    assert!(cmds.contains("partition 1/25: "), "{cmds}");
    // Nothing went to the stick: no read-write mount, no log.
    assert!(!seen.ran.iter().any(|(l, _, _)| l.contains(" -o rw,")), "{:#?}", seen.ran);
    assert!(!t.base.0.join("stick").exists() && !t.base.0.join("restore-logs").exists());
    drop(seen);
    ui.golden("The dry run of lovelace is done", 0, "dry-done");
}

#[test]
fn a_dry_run_that_commits_is_stopped_at_once() {
    let mut t = restore_t("app-rec-notdry", true);
    let mut ui = Shots::new(script(&[&to_restore(b"for md1"), &[P, Reply::Quit]]));
    assert_eq!(t.app.run(&mut ui), Exit::Quit);
    let f = ui.flat("Problem: the restore stopped at \"Make partitions\"", 0);
    assert!(f.contains("bko-holt didn't run what was asked for"), "{f}");
    assert!(!ui.titles().iter().any(|x| x.contains("is done")), "never Done");
}

#[test]
fn a_failed_boot_has_its_help_raw_output_and_try_again() {
    let mut t = restore_t("app-rec-fail", false);
    let mut ui = Shots::new(script(&[
        &to_restore(b"for md1"),
        // Failure, Help, Raw output, back twice, then Try again: status,
        // a plan, boot and close alone, Done.
        &[P, B(B_HELP), B(B_RAW_OUT), P, Back, B(R_TRY), P, P, P, P, Reply::Quit],
    ]));
    assert_eq!(t.app.run(&mut ui), Exit::Quit);
    ui.done();
    let title = "Problem: the restore stopped at \"Make it bootable\"";
    let f = ui.text(title, 0);
    assert!(f.contains("[x] Copy your files back      [!] Make it bootable"), "{f}");
    let flat = ui.flat(title, 0);
    assert!(flat.contains("grub-install failed (exit status 1): grub-install: error: efibootmgr failed to register the boot entry: No such file or directory."), "{flat}");
    assert!(flat.contains("Your backup is safe") && flat.contains("restore-logs/20261123T142630Z.log.gz"), "{flat}");
    assert!(f.contains("< Help >") && f.contains("< Try again >") && f.contains("< Power off >"), "{f}");
    let help = ui.flat("Problem: the boot loader couldn't be installed", 0);
    assert!(help.contains("this computer refused the new entry") && help.contains("< Install without an entry >"), "{help}");
    let raw = ui.text("What the command said", 0);
    assert!(raw.contains("Command: chroot /mnt/bko-target grub-install") && raw.contains("Ended with: exit status 1"), "{raw}");
    assert!(ui.titles().iter().any(|x| x == "lovelace is restored"), "{:?}", ui.titles());
    let seen = t.seen.borrow();
    let runs: Vec<&str> = seen.started.iter().map(|(l, _, _)| l.as_str()).collect();
    assert_eq!(runs.len(), 3, "{runs:#?}");
    assert!(runs[1].contains(" --only boot ") && runs[2].contains(" --only close ") && !runs[1].contains("--erase"), "{runs:#?}");
    assert!(seen.loads.ends_with(&["30-status".to_string(), "13-plan".into(), "31-boot".into(), "32-close".into()]), "{:?}", seen.loads);
    drop(seen);
    ui.golden(title, 0, "failure");
    ui.golden("Problem: the boot loader couldn't be installed", 0, "help-boot");
}

#[test]
fn install_without_an_entry_asks_then_runs_boot_and_close() {
    let mut t = restore_t("app-rec-fail", false);
    let mut ui = Shots::new(script(&[&to_restore(b"for md1"), &[P, B(B_HELP), B(R_FIX), Reply::No, B(R_FIX), Reply::Yes, P, P, P, Reply::Quit]]));
    assert_eq!(t.app.run(&mut ui), Exit::Quit);
    ui.done();
    let seen = t.seen.borrow();
    let runs: Vec<&str> = seen.started.iter().map(|(l, _, _)| l.as_str()).collect();
    assert!(runs[1].contains(" --only boot ") && runs[1].contains(" --removable --no-nvram ") && runs[1].ends_with(" --commit"), "{runs:#?}");
    assert!(runs[2].contains(" --only close ") && runs[2].contains(" --removable --no-nvram "), "{runs:#?}");
    assert!(ui.titles().iter().any(|x| x == "lovelace is restored"), "{:?}", ui.titles());
}

#[test]
fn cancel_asks_first_and_stop_signals_the_locks_pid() {
    let mut t = restore_t("app-rec-stop", false);
    let mut ui = Shots::new(script(&[&to_restore(b"for md1"), &[B(B_CANCEL), Back, B(B_CANCEL), B(B_STOP), P, Reply::Quit]]));
    assert_eq!(t.app.run(&mut ui), Exit::Quit);
    ui.done();
    let q = ui.text("Stop the restore?", 0);
    assert!(q.contains("< Keep going >    < Stop >") || q.contains("< Keep going >"), "{q}");
    let pid = t.seen.borrow().load_pid.unwrap() as u32;
    assert_eq!(t.seen.borrow().terms, [pid], "SIGTERM once, to load's own pid as its lock names it");
    let title = ui.titles().last().unwrap().clone();
    assert!(title.starts_with("Problem: the restore stopped at \""), "{title}");
    let f = ui.flat(&title, 0);
    assert!(f.contains("Stopped by you.") && f.contains("[!]"), "{f}");
    ui.golden("Stop the restore?", 0, "stop");
}

#[test]
fn a_lock_naming_another_process_isnt_signalled() {
    let mut t = restore_t("app-rec-stop", false);
    t.seen.borrow_mut().lock = Some(r#"{"pid": 1, "start": null}"#.into());
    let mut ui = Shots::new(script(&[&to_restore(b"for md1"), &[B(B_CANCEL), B(B_STOP), P, Reply::Quit]]));
    assert_eq!(t.app.run(&mut ui), Exit::Quit);
    assert!(t.seen.borrow().terms.is_empty(), "no signal");
    assert!(ui.flat("The restore couldn't be asked to stop", 0).contains("names another process"), "{:?}", ui.titles());
}

#[test]
fn a_signal_during_a_detached_restore_keeps_its_files() {
    let mut t = restore_t("app-rec-stop", false);
    let mut ui = Shots::new(script(&[&to_restore(b"for md1"), &[Reply::Quit]]));
    assert_eq!(t.app.run(&mut ui), Exit::Quit);
    let dir = t.app.session.dir.clone();
    assert!(t.base.0.join("run.json").exists(), "run.json says where its files are");
    let rj = fs::read_to_string(t.base.0.join("run.json")).unwrap();
    assert!(rj.contains(&dir.display().to_string()) && rj.contains("events-1"), "{rj}");
    let pid = t.seen.borrow().load_pid.map(|p| p as u32);
    assert_eq!(t.app.session.keep, Some(pid), "kept for load's pid, from its start");
    // A load still running (a pid that is alive: ours, here) keeps them.
    t.app.session.keep = Some(Some(std::process::id()));
    drop(t.app);
    assert!(dir.join("k0").exists() && dir.join("password").exists(), "the key files outlive bko-rescue, as load does");
    sys::wipe_dir(&dir);
}

#[test]
fn plain_mode_restore_transcript() {
    let mut t = restore_t("app-rec-restore", false);
    let typed = "1\ncorrect horse\n1\no\nfor md1\nfor md1\n12.9 GB vdb\n12.9 GB vdc\nr\n";
    // Nothing more typed for an hour: the run goes on to its end.
    let line = FakeLine::new(vec![crate::plain::tests::In::Bytes(typed.as_bytes().to_vec()), crate::plain::tests::In::Pause(3_600_000)]);
    let mut pl = Plain::new(line, Lines::Utf8);
    let r = t.app.run(&mut pl);
    let out = pl.into_line().out;
    assert!(matches!(r, Exit::Quit | Exit::Done(_)), "{r:?}\n{out}");
    assert!(!out.contains('\x1b') && !out.contains("for md1"), "{out}");
    let at = out.find("Choose [r, c, a, b]: Restore").unwrap_or(0);
    // A stage's time is the run's own clock's: the same in every run as N.
    let mut shown = String::new();
    for l in out[at..].lines() {
        let l = match l.find(": done in ") {
            Some(i) => format!("{}: done in N s", &l[..i]),
            None => l.to_string(),
        };
        shown.push_str(&l);
        shown.push('\n');
    }
    golden("tests/screens/app-plain-restore.txt", &shown);
    assert!(out.contains("[x] Copy your files back: done in"), "{out}");
}

#[test]
fn try_again_writes_only_the_disks_confirmed() {
    // Try again's new plan has another disk at vdb (16.1 GB, not the
    // 12.9 GB confirmed): never a run, its help, Back to Failure.
    let mut t = restore_t("app-rec-moved", false);
    let mut ui = Shots::new(script(&[&to_restore(b"for md1"), &[P, B(R_TRY), P, P, Back, Reply::Quit]]));
    assert_eq!(t.app.run(&mut ui), Exit::Quit);
    ui.done();
    let help = ui.flat("Problem: the disks changed", 0);
    assert!(help.contains("bko-holt's new plan writes vdb, but its size isn't that of the disk you confirmed on Review."), "{help}");
    assert!(help.contains("< Go back and change it >") && !help.contains("< Try again >"), "{help}");
    let failure = "Problem: the restore stopped at \"Make it bootable\"";
    assert_eq!(ui.titles().iter().filter(|x| *x == failure).count(), 2, "Back: Failure again: {:?}", ui.titles());
    let seen = t.seen.borrow();
    assert_eq!(seen.started.len(), 1, "nothing more started: {:#?}", seen.started);
    assert!(seen.loads.ends_with(&["30-status".to_string(), "13-plan".into()]), "{:?}", seen.loads);
}

#[test]
fn install_without_an_entry_writes_only_the_disks_confirmed() {
    let mut t = restore_t("app-rec-moved", false);
    let mut ui = Shots::new(script(&[&to_restore(b"for md1"), &[P, B(B_HELP), B(R_FIX), Reply::Yes, P, B(B_FIX), P, Reply::Quit]]));
    assert_eq!(t.app.run(&mut ui), Exit::Quit);
    ui.done();
    assert!(ui.flat("Problem: the disks changed", 0).contains("writes vdb, but its size isn't"), "{:?}", ui.titles());
    assert_eq!(t.seen.borrow().started.len(), 1, "no boot run");
    // Back to Disks (its plan first): Review's runs and confirmations are gone.
    assert_eq!(ui.titles().last().map(String::as_str), Some("Where each disk goes"), "{:?}", ui.titles());
    assert!(t.app.reviewed.is_empty() && t.app.confirmed.is_empty() && t.app.serials.is_empty() && t.app.restore.is_none());
}

#[test]
fn go_on_runs_each_stage_and_ticks_what_status_says_is_done() {
    // The recorded status after partition: Go on, each stage from
    // assemble to close alone, to Done with every stage ticked.
    let mut t = restore_t("app-rec-goon", false);
    let pass = b"for md1".to_vec();
    let mut ui = Shots::new(script(&[
        &to_disks(),
        &[B(B_DONE), P, P, Secrets(vec![pass.clone()]), P],
        &[B(B_GO_ON), P, Serials(vec!["12.9 GB vdb".into(), "12.9 GB vdc".into()]), P, P, P, P, P, P, Reply::Quit],
    ]));
    assert_eq!(t.app.run(&mut ui), Exit::Quit);
    ui.done();
    let seen = t.seen.borrow();
    let runs: Vec<&str> = seen.started.iter().map(|(l, _, _)| l.as_str()).collect();
    assert_eq!(runs.len(), 6, "{runs:#?}");
    for (r, st) in runs.iter().zip(["assemble", "format", "mount", "restore", "boot", "close"]) {
        assert!(r.contains(&format!(" --only {st} ")) && r.ends_with(" --commit") && !r.contains("--erase"), "{r}");
    }
    let done = ui.text("lovelace is restored", 0);
    assert!(done.contains("[x] Make partitions           [x] Set up RAID and encryption"), "partition as status says: {done}");
    assert!(done.contains("[x] Make it bootable") && done.contains("[x] Finish") && !done.contains("[ ]"), "{done}");
}
