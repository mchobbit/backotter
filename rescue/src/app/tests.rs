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
}

struct Fake {
    base: PathBuf,
    holt: String,
    /// bko-holt's answers to --version and stick, when not the scenario's.
    version: Option<String>,
    stick: Option<String>,
    rustic: String,
    seen: Rc<RefCell<Seen>>,
    tools: Vec<&'static str>,
}

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
                "report-bug" => {
                    let unredacted = a.contains(&"--no-redact".to_string());
                    let text = if unredacted { "NOT redacted\n" } else { "a redacted report\nhost-1\n" };
                    write_new(Path::new(&a[2]), text.as_bytes()).unwrap();
                    ok(b"Wrote it.\n".to_vec())
                }
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
                ok(fixture(&format!("rustic/{}/{call}/stdout", self.rustic)))
            }
            "ckbcomp" => {
                self.seen.borrow_mut().layouts.push(a.join(" "));
                ok(b"keymaps 0-127\n".to_vec())
            }
            "loadkeys" | "mount" | "umount" => ok(Vec::new()),
            p => panic!("the fake runs no {p}"),
        }
    }
}

impl Sys for Fake {
    fn start(&mut self, c: Cmd) -> Box<dyn sys::Job> {
        let stdin = c.stdin.as_ref().map(|s| s.expose().to_vec());
        self.seen.borrow_mut().ran.push((c.line(), c.env.clone(), stdin.clone()));
        let o = self.answer(&c, stdin.as_deref());
        Box::new(Ready(o))
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
        fs::read(path).ok().filter(|b| b.len() <= max)
    }
    fn which(&self, name: &str) -> Option<PathBuf> {
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

struct T {
    app: App,
    seen: Rc<RefCell<Seen>>,
    base: PathBuf,
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
        seen: seen.clone(),
        tools: vec!["bko-holt", "rustic-bko", "ckbcomp", "loadkeys", "mount", "umount"],
    };
    f(&mut fake);
    let session = Session::new(&base).unwrap();
    let app = App::new(Box::new(fake), session, Opts { holt: None, rustic: "rustic-bko".into(), dry_run }, wh);
    T { app, seen, base }
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
    let mut ui = Shots::new(script(&[&start(), &[Pick(0), Sec(PASSWORD.to_vec()), P, P, Pick(0), B(B_LEAVE)]]));
    assert_eq!(t.app.run(&mut ui), Exit::Done(0));
    ui.done();
    assert_eq!(ui.titles(), ["Which machine is this?", "The backup password", "Which backup?", "Next: the disks"]);
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
    ui.golden("Next: the disks", 0, "next");
    drop(t.app);
    assert!(!dir.exists(), "the session's files are wiped and its directory gone");
    let _ = fs::remove_dir_all(&t.base);
}

#[test]
fn two_wrong_passwords_then_the_hint() {
    let mut t = tt("stick-three", "lovelace");
    let wrong = || Sec(b"wrong one".to_vec());
    let mut ui = Shots::new(script(&[&start(), &[Pick(0), wrong(), P, wrong(), P, wrong(), P, Sec(PASSWORD.to_vec()), P, P, Back, Sec(Vec::new()), P, P, Pick(1), B(B_LEAVE)]]));
    assert_eq!(t.app.run(&mut ui), Exit::Done(0));
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
    let mut ui = Shots::new(script(&[&start(), &[Pick(0), B(B_RECOVERY), Sec(swapped), P, Sec(WORDS.to_vec()), P, P, P, Back, Back, Back, Pick(0), Sec(PASSWORD.to_vec()), P, P, Pick(0), B(B_LEAVE)]]));
    assert_eq!(t.app.run(&mut ui), Exit::Done(0));
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
        }
        let base = t.base.clone();
        let fake = Fake { base: base.clone(), holt: "stick-three".into(), version: None, stick: None, rustic: "lovelace".into(), seen: t.seen.clone(), tools: vec!["bko-holt", "rustic-bko"] };
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
    let reports = t.base.join("reports");
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
