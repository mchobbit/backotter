//! `bko-rescue --demo` (RT6-RT8): every widget in a row, in full screen
//! or plain mode as the profile says, for the pty runs and later spikes.
//!
//! Back goes a page back; `P` on the first page and `p` on the
//! too-small notice go on in plain mode at the same page.  Only a
//! password's length is ever shown.
//!
//! The last page before the summary runs a fake bko-holt, and only when
//! one is named with `--holt PATH` (`testvm/rescue/fake-bko-holt`).  It
//! asks a passphrase, hands it on as a key file (0600, in a new 0700
//! directory, no newline) and the backup password typed before as a
//! password file beside it, runs `PATH load ... --json --repo URL
//! --password-file FILE --key-file NAME=FILE` without `--commit` and with
//! `BKO_HOLT_NO_COMMIT=1`, and follows its events with the gauge.  Both
//! files go as soon as the fake has exited.  Without `--holt` nothing is
//! run: the demo never starts the real bko-holt by itself.
//!
//! While the fake runs, SIGINT is caught, and in plain mode SIGHUP and
//! SIGTERM too (the full screen's `Term` catches those already).  The
//! page then stops the fake and removes the key file before the signal
//! ends the program.  The fake runs in a process group of its own, so a
//! Ctrl-C on the terminal reaches only bko-rescue.
//!
//! The events are holt-json.md's `bko-holt-events` (RT10h): `Follow`
//! reads them as a dry run's are read, and stops the fake unless `start`
//! says it is a dry run that doesn't commit; RT12 writes the real
//! follower in `app/`.

use std::ffi::OsString;
use std::fs::{self, File, OpenOptions};
use std::io::{self, Read, Seek, SeekFrom};
use std::os::unix::fs::{DirBuilderExt as _, OpenOptionsExt as _};
use std::os::unix::process::CommandExt as _;
use std::path::{Path, PathBuf};
use std::process::{Child, Command, ExitStatus, Stdio};
use std::time::Instant;

use crate::json::{self, Value};
use crate::plain::{Plain, StdLine};
use crate::secret::{self, Secret};
use crate::term::ffi;
use crate::term::profile::Profile;
use crate::text::{cap, Clean};
use crate::ui::{Answer, Button, Chrome, Input, Menu, Message, Password, Progress, Question, Radio, TextView, Tick, Ui};
use crate::widgets::FullScreen;

/// What the pages were given, for the last one, and the fake to run.
#[derive(Default)]
pub struct Seen {
    pub machine: Option<usize>,
    pub address: String,
    pub password_chars: usize,
    /// The backup password, for the restore page's password file; wiped
    /// when the tour ends.
    pub password: Option<Secret>,
    pub action: Option<usize>,
    pub restore: Option<bool>,
    /// How the fake's restore ended, in words.
    pub run: Option<String>,
    /// `--holt PATH`: the fake bko-holt the restore page runs.
    pub holt: Option<String>,
    /// The pages are in plain mode, where nothing else catches SIGHUP
    /// and SIGTERM.
    pub plain: bool,
}

pub const PAGES: usize = 9;

/// The page that runs the fake.
pub const RESTORE_PAGE: usize = 7;

fn o(s: &str) -> Clean {
    Clean::ours(s)
}

/// One page.  `Ok` goes on, `Back` goes back.
pub fn page(ui: &mut dyn Ui, n: usize, seen: &mut Seen) -> Answer<()> {
    match n {
        0 => {
            let mut r = Radio::new(
                Chrome::ours("Machine", "Which machine is this?").first(),
                o("The stick knows these machines.  None of them was backed up from this computer.  Pick the one you are restoring."),
                vec![
                    o("lovelace   Debian 13      newest backup 22 Nov 2026 02:00 CET"),
                    o("babbage    Fedora 45      newest backup 21 Nov 2026 03:00 EST"),
                    o("hopper     FreeBSD 15.0   newest backup 18 Nov 2026 01:00 UTC"),
                ],
                None,
            );
            r.none = o("Pick a machine first: move to it with the arrows.");
            r.after = o("The one you pick is restored onto this computer, so check the name.");
            r.buttons = vec![Button::ok(), Button::shell()];
            match ui.radio(&r) {
                Answer::Ok(i) => {
                    seen.machine = Some(i);
                    Answer::Ok(())
                }
                // The first page has no Back.
                a => a.map(|_| ()),
            }
        }
        1 => {
            let mut i = Input::new(
                Chrome::ours("Machine", "Where is the backup?"),
                o("Type the address of your backup, as written on the sheet from when the backups were set up:"),
            );
            i.value = seen.address.clone();
            i.after = o("For example:  sftp://nas.local/backotter/lovelace");
            match ui.input(&i) {
                Answer::Ok(s) => {
                    seen.address = s;
                    Answer::Ok(())
                }
                a => a.map(|_| ()),
            }
        }
        2 => {
            let p = Password::new(
                Chrome::ours("Repository", "The backup password"),
                o("Type the backup password.  Each key shows as a *."),
            );
            match ui.password(&p) {
                Answer::Ok(s) => {
                    seen.password_chars = s.chars();
                    // Kept for the restore page's --password-file; the
                    // one it replaces is wiped as it goes.
                    seen.password = Some(s);
                    Answer::Ok(())
                }
                a => a.map(|_| ()),
            }
        }
        3 => {
            let items = [
                ("Restore", "put the backup back onto this computer's disks"),
                ("Check", "read the backup through without writing anything"),
                ("Keyboard", "change the keyboard layout"),
                ("Report", "save a bug report on the stick"),
                ("Shell", "a root shell; leaving it comes back here"),
            ];
            let m = Menu::new(
                Chrome::ours("Menu", "What now?"),
                o("A menu: the arrows move, Enter picks."),
                items.iter().map(|(t, d)| (o(t), o(d))).collect(),
            );
            match ui.menu(&m) {
                Answer::Ok(i) => {
                    seen.action = Some(i);
                    Answer::Ok(())
                }
                a => a.map(|_| ()),
            }
        }
        4 => {
            let mut q = Question::new(
                Chrome::ours("Review", "Restore now?"),
                o("Nothing will be written: this is the demo.  Yes and No both go on."),
            );
            q.no_first = true;
            q.buttons = vec![Button::back()];
            match ui.yes_no(&q) {
                Answer::Ok(b) => {
                    seen.restore = Some(b);
                    Answer::Ok(())
                }
                a => a.map(|_| ()),
            }
        }
        5 => {
            let mut lines = vec![o("# == partition (2 disks)"), o("# GPT, 4 partitions, as lovelace's sda")];
            lines.push(o("sfdisk --wipe always --wipe-partitions always /dev/nvme0n1 <<'EOF'"));
            lines.push(o("/dev/nvme0n1p1 : start=2048, size=1048576, type=C12A7328-F81F-11D2-BA4B-00A0C93EC93B, uuid=0F3A9B21-7C44-4E0D-9F11-2B6E8D0C5A17"));
            for i in 0..30 {
                lines.push(Clean::line(&format!("line {i} of the plan \x1b[31m(an escape, cleaned)"), cap::LOG_LINE));
            }
            let mut v = TextView::new(Chrome::ours("Review", "Every command, in order"), lines);
            v.buttons.push(Button::back());
            ui.view(&v)
        }
        6 => {
            let mut p = Progress::new(Chrome::ours("Restore", "Restoring lovelace"), o("[>] Copy your files back"));
            p.buttons = vec![Button::back()];
            let mut step = 0u8;
            let mut tick = || {
                step += 1;
                if step > 20 {
                    Tick::Done
                } else {
                    Tick::At {
                        text: (step == 20).then(|| o("[x] Copy your files back")),
                        pct: Some(step * 5),
                        line: Clean::ours(&format!("{:.1} of 61.2 GB", f32::from(step) * 3.06)),
                        next_ms: 150,
                    }
                }
            };
            ui.progress(&p, &mut tick)
        }
        RESTORE_PAGE => match seen.holt.clone() {
            Some(h) => restore(ui, &h, seen),
            None => {
                let mut m = Message::new(
                    Chrome::ours("Restore", "Nothing to run"),
                    o("This page runs a fake bko-holt and follows it with the gauge.  Name one with --holt PATH (testvm/rescue/fake-bko-holt) to see it.  The demo never runs the real bko-holt."),
                );
                m.buttons = vec![Button::ok(), Button::back()];
                let a = ui.message(&m);
                if a == Answer::Ok(()) {
                    seen.run = Some("not run".to_string());
                }
                a
            }
        },
        _ => {
            let pick = |v: Option<usize>| v.map_or("none".to_string(), |i| (i + 1).to_string());
            let text = format!(
                "Machine {}, action {}, restore {}, a password of {} characters, and the address:\n",
                pick(seen.machine),
                pick(seen.action),
                match seen.restore {
                    Some(true) => "yes",
                    Some(false) => "no",
                    None => "unanswered",
                },
                seen.password_chars,
            );
            let run = format!("\nThe fake's restore: {}.", seen.run.as_deref().unwrap_or("not reached"));
            let t = Clean::text(&text, cap::PROBLEM)
                .join("", &Clean::line(&seen.address, cap::LINE))
                .join("", &Clean::text(&run, cap::LINE));
            let mut m = Message::new(Chrome::ours("Done", "The end of the demo"), t);
            m.buttons = vec![Button::ok(), Button::back()];
            ui.message(&m)
        }
    }
}

/// Every page in turn on `ui`, from `start`.  Returns the page it
/// stopped on and the answer that stopped it: Ok after the last page,
/// Plain, Quit or Shell (which the demo doesn't open).
pub fn tour(ui: &mut dyn Ui, start: usize, seen: &mut Seen) -> (usize, Answer<()>) {
    let mut n = start;
    loop {
        match page(ui, n, seen) {
            Answer::Ok(()) if n + 1 == PAGES => return (n, Answer::Ok(())),
            Answer::Ok(()) => n += 1,
            Answer::Back => n = n.saturating_sub(1),
            Answer::Button(_) => {}
            a => return (n, a),
        }
    }
}

/// `--demo`: full screen, or plain mode when the profile says so or the
/// user asks.  `holt` is `--holt`'s fake.  The exit status.
pub fn run(p: &Profile, holt: Option<&str>) -> i32 {
    let mut seen = Seen { holt: holt.map(str::to_string), ..Seen::default() };
    let mut at = 0;
    if p.plain.is_none() {
        let mut fs = match FullScreen::open(p) {
            Ok(f) => f,
            Err(e) => {
                eprintln!("bko-rescue --demo: {e}");
                return 1;
            }
        };
        loop {
            let (n, a) = tour(&mut fs, at, &mut seen);
            at = n;
            match a {
                Answer::Shell => continue,
                Answer::Plain => break,
                Answer::Quit => {
                    let code = fs.signal().map_or(if fs.error().is_some() { 1 } else { 0 }, |s| 128 + s);
                    if let Some(e) = fs.error() {
                        let e = e.to_string();
                        drop(fs);
                        eprintln!("bko-rescue --demo: {e}");
                    }
                    return code;
                }
                _ => return 0,
            }
        }
        // The terminal back as it was, then plain mode at the same page.
        drop(fs);
    }
    seen.plain = true;
    let mut pl = Plain::new(StdLine::new(), p.lines);
    loop {
        let (n, a) = tour(&mut pl, at, &mut seen);
        at = n;
        match a {
            Answer::Shell | Answer::Plain => continue,
            Answer::Quit => return if pl.error().is_some() { 1 } else { 0 },
            _ => return 0,
        }
    }
}

// ---- The restore page: the fake bko-holt, a key file, its events ----

/// The demo's one encrypted container, the NAME of `--key-file NAME=FILE`.
pub const KEY_NAME: &str = "sda3_crypt";

/// The password file's name in the run's directory.
pub const PASSWORD_NAME: &str = "password";

/// The command line after the program's path, as RT12's are: the
/// repository and, after these, `--password-file FILE --key-file
/// NAME=FILE`.  No `--commit`, ever.
pub const ARGS: &[&str] = &["load", "demo-system.json", "--json", "--through", "close", "--repo", DEMO_REPO];

/// The demo's repository: never reached, only named.
pub const DEMO_REPO: &str = "sftp://nas.local/backotter/lovelace";

/// The password file's text when none was typed on the password page (a
/// password file is never empty).
pub const DEMO_PASSWORD: &[u8] = b"demo-repository-password";

/// One event line at most (rescue-tui.md, "Hard limits in parsing").
pub const EVENT_LINE_MAX: usize = 64 * 1024;

/// The most of the fake's stderr kept for a message.
pub const STDERR_MAX: u64 = 64 * 1024;

/// The most stages a start event may name.
const STAGES_MAX: usize = 32;

/// The events file is read this much at a time, and at most
/// EVENTS_PER_POLL each look; the rest waits for the next.
const EVENTS_CHUNK: usize = 64 * 1024;
const EVENTS_PER_POLL: usize = 4 * 1024 * 1024;

const CANCEL: usize = 0;
const RAW: usize = 1;

/// The signals the restore page catches while the fake runs: SIGINT,
/// and in plain mode SIGHUP and SIGTERM.  Dropped after the `Run`, it
/// puts their default action back.  In plain mode a signal that came is
/// then raised again, so it ends the program once the fake is stopped
/// and the key file gone.  In full screen it is `Term`'s to take, which
/// puts the terminal back first.
struct Caught {
    plain: bool,
}

const PLAIN_SIGS: &[i32] = &[ffi::SIGINT, ffi::SIGHUP, ffi::SIGTERM];
const FULL_SIGS: &[i32] = &[ffi::SIGINT];

impl Caught {
    fn new(plain: bool) -> Caught {
        ffi::catch(if plain { PLAIN_SIGS } else { FULL_SIGS });
        Caught { plain }
    }
}

impl Drop for Caught {
    fn drop(&mut self) {
        if self.plain {
            ffi::default(PLAIN_SIGS);
            ffi::raise_pending();
        } else {
            ffi::default(FULL_SIGS);
        }
    }
}

/// The restore page: the passphrase, the run, then how it ended.
fn restore(ui: &mut dyn Ui, holt: &str, seen: &mut Seen) -> Answer<()> {
    let mut p = Password::new(
        Chrome::ours("Passphrases", "Unlock sda3_crypt"),
        o("The demo's disk is encrypted.  Type a passphrase for sda3_crypt: any will do.  The fake bko-holt checks only how it was handed on: as a file only bko-rescue can read, with no newline, gone once the fake has exited."),
    );
    p.after = o("Each key shows as a *.");
    let secret = match ui.password(&p) {
        Answer::Ok(s) => s,
        a => return a.map(|_| ()),
    };
    // Before the key file is written, and dropped only after the Run.
    let caught = Caught::new(seen.plain);
    let out = follow(ui, holt, secret, seen);
    drop(caught);
    let out = match out {
        Ok(o) => o,
        Err(a) => return a,
    };
    seen.run = Some(out.short.clone());
    let mut m = Message::new(Chrome::new(o("Restore"), out.title.clone()), out.text.clone());
    if !out.raw.is_empty() {
        m.buttons.push(Button::other("Raw output", Some('o'), RAW));
    }
    loop {
        match ui.message(&m) {
            Answer::Button(RAW) => {
                let mut v = TextView::new(Chrome::ours("Restore", "What the fake bko-holt wrote to stderr"), out.raw.clone());
                v.buttons.push(Button::back());
                if ui.view(&v) == Answer::Quit {
                    return Answer::Quit;
                }
            }
            a => return a,
        }
    }
}

/// Start the fake and follow it with the gauge, Cancel asking first.
/// How it ended, or the answer that left the page (a signal, `p` on the
/// too-small notice): the Run is dropped by then, so the fake is stopped
/// and the key file gone.
fn follow(ui: &mut dyn Ui, holt: &str, secret: Secret, seen: &mut Seen) -> Result<Outcome, Answer<()>> {
    let typed = seen.password.as_ref().filter(|p| !p.is_empty());
    let fallback = Secret::from_bytes(DEMO_PASSWORD);
    let started = Run::start(holt, &secret, typed.unwrap_or(&fallback), std::env::temp_dir().as_path());
    // The key file has it now: wiped.
    drop(secret);
    drop(fallback);
    let mut run = match started {
        Ok(r) => r,
        Err(e) => {
            let t = Clean::text(&format!("The fake bko-holt couldn't be started: {e}."), cap::PROBLEM);
            seen.run = Some("not started".to_string());
            return Err(ui.message(&Message::new(Chrome::ours("Restore", "Problem: nothing ran"), t)));
        }
    };
    let mut prog = Progress::new(Chrome::ours("Restore", "Restoring lovelace"), run.follow.text(0));
    prog.buttons = vec![Button::other("Cancel", None, CANCEL)];
    let stopped = loop {
        let mut tick = || {
            run.poll();
            if run.finished() {
                Tick::Done
            } else {
                let ms = run.ms();
                Tick::At { text: Some(run.follow.text(ms)), pct: run.follow.pct(), line: run.follow.figures(), next_ms: 100 }
            }
        };
        match ui.progress(&prog, &mut tick) {
            Answer::Ok(()) => break false,
            Answer::Button(CANCEL) => {
                let mut q = Question::new(
                    Chrome::ours("Restore", "Stop the restore?"),
                    o("Stopping now leaves the disks half made.  It's the fake, so nothing is lost."),
                );
                q.yes = o("Stop");
                q.no = o("Keep going");
                q.no_first = true;
                q.esc_no = true;
                match ui.yes_no(&q) {
                    Answer::Ok(true) => {
                        // It may have ended while the question was up:
                        // then it wasn't stopped, and says how it ended.
                        run.poll();
                        if run.finished() {
                            break false;
                        }
                        break true;
                    }
                    Answer::Ok(false) | Answer::Back | Answer::Button(_) => prog.text = run.follow.text(run.ms()),
                    a => return Err(a.map(|_| ())),
                }
            }
            // A signal, or `p` on the too-small notice (plain mode
            // starts this page again): the fake is stopped as the Run
            // goes.
            a => return Err(a),
        }
    };
    if stopped {
        run.stop();
    } else {
        run.wait();
    }
    Ok(run.outcome(stopped))
}

/// How a run ended, for its message.
pub struct Outcome {
    pub title: Clean,
    pub text: Clean,
    /// The summary page's words.
    pub short: String,
    /// The fake's stderr, its last 64 KiB, a line each.
    pub raw: Vec<Clean>,
}

/// Overwrites PATH with zeros to its length, then unlinks it (holt-json.md,
/// Secrets 6).  Only a regular file (lstat) is overwritten: anything else
/// in its place is only unlinked.
fn wipe_file(path: &Path) {
    use std::io::Write as _;
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

/// The fake bko-holt running, its key file, events and stderr, all in
/// one new 0700 directory.  Dropping it stops the fake if it still runs
/// and removes the files and the directory.
pub struct Run {
    dir: PathBuf,
    key: Option<PathBuf>,
    password: Option<PathBuf>,
    events: Option<File>,
    stderr: Option<PathBuf>,
    child: Option<Child>,
    status: Option<ExitStatus>,
    /// Every event read: the fake has exited and the file is read to its
    /// end, or the fake was stopped.
    drained: bool,
    /// EVENTS_CHUNK bytes, made once.
    buf: Vec<u8>,
    started: Instant,
    pub follow: Follow,
}

/// A new directory under `base` that only we can read: made here with
/// mode 0700, never one that was there already (someone else's could be
/// read by them).
fn private_dir(base: &Path) -> io::Result<PathBuf> {
    let pid = std::process::id();
    for i in 0..100 {
        let d = base.join(format!("bko-rescue-demo-{pid}-{i}"));
        match fs::DirBuilder::new().mode(0o700).create(&d) {
            Ok(()) => {
                // The umask can only take bits away; ram_dir checks it.
                secret::ram_dir(&d)?;
                return Ok(d);
            }
            Err(e) if e.kind() == io::ErrorKind::AlreadyExists => continue,
            Err(e) => return Err(e),
        }
    }
    Err(io::Error::other("no free name for the demo's directory"))
}

/// A new 0600 file, made with O_EXCL.
fn new_file(path: &Path) -> io::Result<File> {
    OpenOptions::new().write(true).create_new(true).mode(0o600).open(path)
}

impl Run {
    /// Write the key file and the password file and start `holt`.
    pub fn start(holt: &str, secret: &Secret, password: &Secret, base: &Path) -> io::Result<Run> {
        let dir = private_dir(base)?;
        // From here the Run owns the directory: an error drops it, and
        // Drop removes what was made.
        let mut run = Run {
            dir,
            key: None,
            password: None,
            events: None,
            stderr: None,
            child: None,
            status: None,
            drained: false,
            buf: vec![0u8; EVENTS_CHUNK],
            started: Instant::now(),
            follow: Follow::new(),
        };
        run.key = Some(secret.write_file(&run.dir, KEY_NAME)?);
        run.password = Some(password.write_file(&run.dir, PASSWORD_NAME)?);
        let ev = run.dir.join("events");
        let out = new_file(&ev)?;
        run.events = Some(File::open(&ev)?);
        let err_path = run.dir.join("stderr");
        let err = new_file(&err_path)?;
        run.stderr = Some(err_path);
        let mut kf = OsString::from(format!("{KEY_NAME}="));
        if let Some(k) = &run.key {
            kf.push(k);
        }
        let child = Command::new(holt)
            .args(ARGS)
            .arg("--password-file")
            .arg(run.password.as_deref().unwrap_or(Path::new("")))
            .arg("--key-file")
            .arg(kf)
            .env("BKO_HOLT_NO_COMMIT", "1")
            .stdin(Stdio::null())
            .stdout(out)
            .stderr(err)
            // A group of its own: a Ctrl-C on the terminal is ours.
            .process_group(0)
            .spawn()?;
        run.child = Some(child);
        run.started = Instant::now();
        Ok(run)
    }

    /// Milliseconds since the start.
    pub fn ms(&self) -> u64 {
        u64::try_from(self.started.elapsed().as_millis()).unwrap_or(u64::MAX)
    }

    /// Read what's new in the events file, EVENTS_CHUNK at a time and at
    /// most EVENTS_PER_POLL.  True when it read to the end, or the events
    /// stopped making sense.
    fn read_events(&mut self) -> bool {
        let ms = self.ms();
        let Some(f) = self.events.as_mut() else { return true };
        let buf = &mut self.buf;
        let mut got = 0;
        while got < EVENTS_PER_POLL {
            if self.follow.error.is_some() {
                return true;
            }
            // A regular file: this never blocks.
            match f.read(buf) {
                Ok(0) => return true,
                Ok(n) => {
                    got += n;
                    self.follow.feed(&buf[..n], ms);
                }
                Err(e) if e.kind() == io::ErrorKind::Interrupted => {}
                Err(_) => return true,
            }
        }
        self.follow.error.is_some()
    }

    /// The fake exited: take its status, then remove the key file and
    /// the password file at once, as load's would be once load exits.
    fn exited(&mut self, st: ExitStatus) {
        self.status = Some(st);
        self.child = None;
        self.remove_key();
    }

    fn remove_key(&mut self) {
        for k in [self.key.take(), self.password.take()].into_iter().flatten() {
            wipe_file(&k);
        }
    }

    /// One look: whether the fake has exited, then new events.  Events
    /// that make no sense, or a run that says it commits, stop it.
    pub fn poll(&mut self) {
        if let Some(c) = &mut self.child {
            match c.try_wait() {
                Ok(Some(st)) => self.exited(st),
                Ok(None) => {}
                Err(_) => return self.stop(),
            }
        }
        // Read after the exit is seen, so a read to the end after it
        // has every event.
        let at_end = self.read_events();
        if self.follow.error.is_some() || self.follow.not_dry().is_some() {
            return self.stop();
        }
        if self.child.is_none() && at_end {
            self.drained = true;
        }
    }

    /// Whether the fake has exited and its events are all read.
    pub fn finished(&self) -> bool {
        self.child.is_none() && self.drained
    }

    /// Reap the fake, blocking.
    fn reap(&mut self) {
        if let Some(mut c) = self.child.take() {
            match c.wait() {
                Ok(st) => self.exited(st),
                Err(_) => {
                    let _ = c.kill();
                    self.remove_key();
                }
            }
        }
    }

    /// Wait for the fake to exit, still reading its events, so events
    /// that make no sense stop it as they do while the gauge runs.
    pub fn wait(&mut self) {
        loop {
            self.poll();
            if self.finished() {
                return;
            }
            std::thread::sleep(std::time::Duration::from_millis(20));
        }
    }

    /// Stop the fake (SIGKILL) and reap it.  Events it left unread
    /// don't count then.
    pub fn stop(&mut self) {
        if let Some(c) = &mut self.child {
            let _ = c.kill();
        }
        self.reap();
        self.drained = true;
    }

    /// The last 64 KiB of the fake's stderr, a line each.
    fn stderr_lines(&self) -> Vec<Clean> {
        let Some(p) = &self.stderr else { return Vec::new() };
        let Ok(mut f) = File::open(p) else { return Vec::new() };
        let len = f.metadata().map(|m| m.len()).unwrap_or(0);
        let _ = f.seek(SeekFrom::Start(len.saturating_sub(STDERR_MAX)));
        let mut b = Vec::new();
        let _ = f.take(STDERR_MAX).read_to_end(&mut b);
        let s = String::from_utf8_lossy(&b);
        s.lines().map(|l| Clean::line(l, cap::LOG_LINE)).collect()
    }

    /// How it ended, once it has.
    pub fn outcome(&self, stopped: bool) -> Outcome {
        judge(&self.follow, self.status, self.stderr_lines(), stopped)
    }
}

/// How a run ended, from what its events said (`f`), its exit status, its
/// stderr's lines and whether Cancel stopped it.  Whatever doesn't make it
/// plainly done (a refusal, problems, a stop, an internal error, a skip
/// for a reason this bko-rescue doesn't know) isn't Done (Principle 6).
pub fn judge(f: &Follow, status: Option<ExitStatus>, raw: Vec<Clean>, stopped: bool) -> Outcome {
    let safe = "Nothing in the backup has been changed.";
    let code = status.and_then(|s| s.code());
    let mk = |title: &str, text: String, short: String| Outcome {
        title: Clean::line(title, cap::LINE),
        text: Clean::text(&text, cap::PROBLEM),
        short,
        raw: raw.clone(),
    };
    let not_dry = f.not_dry();
    if stopped && f.error.is_none() && not_dry.is_none() {
        return mk(
            "Stopped",
            format!("The fake bko-holt was stopped, so its restore stopped half way.  {safe}"),
            "stopped".to_string(),
        );
    }
    if let Some(e) = &f.error {
        return mk(
            "Problem: the restore was stopped",
            format!("bko-holt's events didn't make sense, so it was stopped: {}.  {safe}", e.as_str()),
            "stopped: bad events".to_string(),
        );
    }
    if let Some(why) = not_dry {
        let said = match why {
            NotDry::Commits => "bko-holt said it would write to the disks, which the demo never asks for",
            NotDry::NotDryRun => "bko-holt didn't say this is a dry run (BKO_HOLT_NO_COMMIT), which the demo always asks for",
            NotDry::Ran => "bko-holt ran a command instead of only showing it, which the demo never asks for",
        };
        return mk("Problem: the restore was stopped", format!("{said}, so it was stopped.  {safe}"), format!("stopped: {}", why.short()));
    }
    let status_words = match (code, status) {
        (Some(c), _) => format!("exit status {c}"),
        (None, Some(_)) => "killed by a signal".to_string(),
        (None, None) => "no exit status".to_string(),
    };
    if let Some(r) = &f.refusal {
        let exit = r.exit.map_or("no exit status in it".to_string(), |e| format!("exit status {e}"));
        return mk(
            "Problem: bko-holt refused the run",
            format!("bko-holt refused it before doing anything: {}\n\n(code {}, {exit}; bko-holt ended with {status_words}.)  {safe}", r.text.as_str(), r.code.as_str()),
            format!("refused: {}", r.code.as_str()),
        );
    }
    if f.ended == Some(101) {
        let what = f.failed.as_ref().map(|x| format!("  It said: {}", x.text.as_str())).unwrap_or_default();
        return mk(
            "Problem: bko-holt failed",
            format!("bko-holt stopped on an internal error: report it.{what}\n\n{safe}"),
            format!("internal error ({status_words})"),
        );
    }
    if f.ended == Some(5) || f.stopped == Some(true) {
        return mk(
            "Stopped",
            format!("bko-holt stopped on request, between commands, and said so (exit 5).  {safe}"),
            format!("stopped by bko-holt ({status_words})"),
        );
    }
    if let Some(ps) = &f.problems {
        let mut t = "bko-holt's checks found problems, so it ran nothing:\n".to_string();
        for (c, text) in ps {
            t.push_str(&format!("\n  {}: {}", c.as_str(), text.as_str()));
        }
        if ps.is_empty() {
            t.push_str("\n  (it named none)");
        }
        let first = ps.first().map_or("none named", |(c, _)| c.as_str());
        return mk("Problem: nothing ran", format!("{t}\n\n{safe}"), format!("blocked: {first} ({status_words})"));
    }
    match (&f.failed, f.ended) {
        (None, Some(0)) if code == Some(0) && f.odd_skip => mk(
            "Problem: the restore can't be called done",
            format!("bko-holt ended with exit status 0, but it skipped a stage for a reason this bko-rescue doesn't know, so the disks' status would have to be read first.  {safe}"),
            "unclear: a stage skipped for an unknown reason".to_string(),
        ),
        (None, Some(0)) if code == Some(0) && f.unfinished().is_some() => {
            let (name, why) = f.unfinished().unwrap_or_default();
            mk(
                "Problem: the restore can't be called done",
                format!("bko-holt ended with exit status 0, but stage {name} {why}, so the disks' status would have to be read first.  {safe}"),
                format!("unclear: stage {name} {why}"),
            )
        }
        (None, Some(0)) if code == Some(0) => {
            let dry = if f.would > 0 {
                format!("  It was a dry run: {} commands would have run, and none did.", f.would)
            } else {
                String::new()
            };
            mk(
                "Done",
                format!(
                    "The fake restore went through every stage, {} events in all, and ended with exit status 0.{dry}  \
                     The key file and the password file it was given are removed.",
                    f.events
                ),
                "done".to_string(),
            )
        }
        (Some(x), _) => {
            let mut t = format!("Stage {} failed: {}\n\n{}", x.stage.as_str(), x.text.as_str(), failure_help(x));
            // The tail, unless the text has it already (bko-holt's does).
            if x.tail.iter().any(|l| !x.text.as_str().contains(l.as_str())) {
                t.push_str("\n\nIts last lines:");
                for l in &x.tail {
                    t.push_str(&format!("\n    {}", l.as_str()));
                }
            }
            t.push_str(&format!("\n\nbko-holt ended with {status_words}.  {safe}"));
            mk("Problem: the restore failed", t, format!("failed in {}: {} ({status_words})", x.stage.as_str(), x.code.as_str()))
        }
        (None, Some(e)) => mk(
            "Problem: the restore failed",
            format!("bko-holt's end event says exit {e}, and it ended with {status_words}.  {safe}"),
            format!("failed ({status_words})"),
        ),
        (None, None) => mk(
            "Problem: the restore failed",
            format!("bko-holt ended with {status_words} and without its end event.  {safe}"),
            format!("failed ({status_words}, no end event)"),
        ),
    }
}

/// A failure's words of help, by its code (holt-json.md, "Failures"); a
/// code this bko-rescue doesn't know gets the general ones.
fn failure_help(x: &Failure) -> String {
    let program = x.program.as_ref().map(|p| format!(" ({})", p.as_str())).unwrap_or_default();
    match x.code.as_str() {
        "cmd_failed" => format!("A command{program} failed."),
        "wrong_passphrase" => "The passphrase didn't open the disk: it would be asked again.".to_string(),
        "archiver_failed" => "The copy failed: trying again copies again.".to_string(),
        "efi_vars" => format!("This computer's firmware took no boot entry{program}: the system can be installed without one."),
        "lock_held" => "Another bko-holt load is running here.".to_string(),
        _ => "Something in this stage went wrong: the log has the whole story.".to_string(),
    }
}

impl Drop for Run {
    fn drop(&mut self) {
        self.stop();
        self.remove_key();
        self.events = None;
        let _ = fs::remove_file(self.dir.join("events"));
        let _ = fs::remove_file(self.dir.join("stderr"));
        let _ = fs::remove_dir(&self.dir);
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum State {
    Waiting,
    Running,
    Done,
    /// A dry run's: its commands would run (`stage_done` with `checked:
    /// false`, after `cmd` events with `would: true`).
    Would,
    Skipped,
    /// Skipped for a reason this bko-rescue doesn't know (a `skip` state
    /// other than done and nothing).
    SkippedWhy,
    Failed,
}

#[derive(Clone, Debug)]
pub struct Stage {
    pub name: Clean,
    pub state: State,
    /// When it started and how long it took, in ms.
    pub from_ms: u64,
    pub took_ms: Option<u64>,
    /// Its commands that would run.
    pub would: usize,
    /// Its commands, as `start.stage_commands` counts them.
    pub commands: Option<u64>,
}

impl Stage {
    fn new(name: &str) -> Stage {
        Stage { name: Clean::line(name, 32), state: State::Waiting, from_ms: 0, took_ms: None, would: 0, commands: None }
    }
}

/// A `failed` event.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Failure {
    pub stage: Clean,
    pub text: Clean,
    pub code: Clean,
    pub program: Option<Clean>,
    pub tail: Vec<Clean>,
}

/// A `bko-holt-error` document as the stream's first line: bko-holt
/// refused before it began.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Refusal {
    pub code: Clean,
    pub text: Clean,
    pub exit: Option<i64>,
}

/// Why a run isn't the dry run the demo asked for.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum NotDry {
    /// `start.commit` isn't false.
    Commits,
    /// `start.dry_run` isn't true.
    NotDryRun,
    /// A `cmd` without `would: true`: it ran.
    Ran,
}

impl NotDry {
    fn short(self) -> &'static str {
        match self {
            NotDry::Commits => "it would commit",
            NotDry::NotDryRun => "not a dry run",
            NotDry::Ran => "a command ran",
        }
    }
}

/// The most problems a `problems` event's list is read for, and the most
/// lines of a failure's tail.
const PROBLEMS_MAX: usize = 64;
const TAIL_MAX: usize = 20;

/// bko-holt's events as they come (holt-json.md, `bko-holt-events`), as a
/// gauge shows them: one JSON object a line, `start` (format
/// `bko-holt-events`, schema 1, `stages`, `commit`, `dry_run`) first, then
/// `problems`, `stage`, `skip`, `cmd`, `cmd_done`, `progress`, `warning`,
/// `stopping`, `stage_done`, `failed`, and `end` (`exit`, `stopped`) last;
/// or, instead of all of them, one `bko-holt-error` document.  An event
/// or field it doesn't know is skipped; a value it acts on and doesn't
/// know fails closed (a skip's unknown reason, a failure's unknown code);
/// anything else wrong is an `error`, after which nothing more is read.
#[derive(Clone, Debug, Default)]
pub struct Follow {
    pub stages: Vec<Stage>,
    pub started: bool,
    pub commit: Option<bool>,
    pub dry_run: Option<bool>,
    /// bko-holt load's own pid, from `start` (for RT12's Cancel).
    pub pid: Option<u64>,
    /// The "Now:" line: what the running command does.
    pub now: Option<Clean>,
    /// The archiver's figures: bytes so far and the total, when known.
    pub bytes: Option<u64>,
    pub total: Option<u64>,
    /// The archiver run the figures are of, part of parts.
    pub part: Option<(u64, u64)>,
    pub warnings: usize,
    /// Commands that would run (a dry run's), and those that ran.
    pub would: usize,
    pub ran: usize,
    /// A `stopping` event came.
    pub stopping: bool,
    pub failed: Option<Failure>,
    /// A `problems` event's codes and texts: nothing ran.
    pub problems: Option<Vec<(Clean, Clean)>>,
    pub refusal: Option<Refusal>,
    /// A stage skipped for a reason this bko-rescue doesn't know.
    pub odd_skip: bool,
    pub ended: Option<i64>,
    /// `end.stopped`.
    pub stopped: Option<bool>,
    pub error: Option<Clean>,
    /// Events read, and those skipped as unknown.
    pub events: usize,
    pub unknown: usize,
    /// The last `cmd` was one that would run (it has no `cmd_done`).
    would_pending: bool,
    /// `start` named the run's stages (else the list is holt-json.md's,
    /// and which the run asked for isn't known).
    stages_named: bool,
    partial: Vec<u8>,
}

/// The stages' names in plain words (rescue-screens.md, "9. Restore").
pub fn stage_label(name: &str) -> Option<&'static str> {
    Some(match name {
        "partition" => "Make partitions",
        "assemble" => "Set up encryption",
        "format" => "Make filesystems",
        "mount" => "Ready the filesystems",
        "restore" => "Copy your files back",
        "boot" => "Make it bootable",
        "close" => "Finish",
        _ => return None,
    })
}

/// What a program does, in plain words, for the "Now:" line.
fn doing(prog: &str) -> String {
    let base = prog.rsplit('/').next().unwrap_or(prog);
    let w = match base {
        "sfdisk" | "sgdisk" | "parted" | "gpart" | "fdisk" | "disklabel" | "wipefs" => "making partitions",
        "cryptsetup" | "geli" => "setting up encryption",
        "mdadm" | "lvcreate" | "vgcreate" | "pvcreate" | "zpool" => "setting up RAID and volumes",
        "zfs" => "making datasets",
        "mount" | "umount" => "readying the filesystems",
        "rustic-bko" | "restic-bko" => "copying your files back",
        "grub-install" | "grub-mkconfig" | "update-grub" | "efibootmgr" | "bootctl" => "making it bootable",
        b if b.starts_with("mkfs") || b.starts_with("mke2fs") || b == "newfs" || b == "mkswap" => "making a filesystem",
        _ => return format!("running {}", Clean::line(base, 64).as_str()),
    };
    w.to_string()
}

/// Bytes in GB or TB, decimal, three significant figures.
pub fn size(n: u64) -> String {
    let gb = n as f64 / 1e9;
    let (v, unit) = if gb >= 1000.0 { (gb / 1000.0, "TB") } else { (gb, "GB") };
    if v >= 100.0 {
        format!("{v:.0} {unit}")
    } else if v >= 10.0 {
        format!("{v:.1} {unit}")
    } else {
        format!("{v:.2} {unit}")
    }
}

/// 184233 as 184,233.
pub fn thousands(n: u64) -> String {
    let s = n.to_string();
    let mut out = String::new();
    for (i, c) in s.chars().enumerate() {
        if i > 0 && (s.len() - i) % 3 == 0 {
            out.push(',');
        }
        out.push(c);
    }
    out
}

fn secs(ms: u64) -> String {
    let s = ms / 1000;
    if s >= 120 {
        format!("{} min", s / 60)
    } else {
        format!("{s} s")
    }
}

impl Follow {
    pub fn new() -> Follow {
        let mut f = Follow::default();
        // Until start names them, the stages holt-json.md lists.
        for n in ["partition", "assemble", "format", "mount", "restore", "boot", "close"] {
            f.stages.push(Stage::new(n));
        }
        f
    }

    fn fail(&mut self, why: String) {
        if self.error.is_none() {
            self.error = Some(Clean::line(&why, cap::LINE));
        }
    }

    /// A stage `start` named that the events don't show through (Principle
    /// 6: a stage not known to be done isn't done): its name and why.  A
    /// dry run's stage is through when all its commands would run.  None
    /// when start named no stages.
    pub fn unfinished(&self) -> Option<(String, String)> {
        self.stages.iter().filter(|_| self.stages_named).find_map(|s| {
            let why = match s.state {
                // An unknown skip has its own words (odd_skip).
                State::Done | State::Skipped | State::SkippedWhy => return None,
                State::Would => match s.commands {
                    Some(n) if n != s.would as u64 => format!("showed {} of its {n} commands", s.would),
                    _ => return None,
                },
                State::Waiting => "never began".to_string(),
                State::Running => "never finished".to_string(),
                State::Failed => "failed".to_string(),
            };
            Some((s.name.as_str().to_string(), why))
        })
    }

    /// Why this run isn't the dry run the demo asked for, once `start` is
    /// read (holt-json.md, "The flow" 6: stop at once unless `dry_run` is
    /// true and `commit` false), or a command really ran.
    pub fn not_dry(&self) -> Option<NotDry> {
        if !self.started {
            None
        } else if self.commit != Some(false) {
            Some(NotDry::Commits)
        } else if self.dry_run != Some(true) {
            Some(NotDry::NotDryRun)
        } else if self.ran > 0 {
            Some(NotDry::Ran)
        } else {
            None
        }
    }

    /// More bytes of the events file, at `ms` since the start.  Lines
    /// are taken whole; a line longer than EVENT_LINE_MAX is an error.
    pub fn feed(&mut self, bytes: &[u8], ms: u64) {
        for &b in bytes {
            if self.error.is_some() {
                return;
            }
            if b == b'\n' {
                let line = std::mem::take(&mut self.partial);
                self.line(&line, ms);
            } else if self.partial.len() >= EVENT_LINE_MAX {
                self.partial.clear();
                self.fail(format!("an event line over {} KiB", EVENT_LINE_MAX / 1024));
            } else {
                self.partial.push(b);
            }
        }
    }

    fn stage_mut(&mut self, v: &Value) -> Option<&mut Stage> {
        let name = v.get_str("stage")?;
        if let Some(i) = self.stages.iter().position(|s| s.name.as_str() == name) {
            return Some(&mut self.stages[i]);
        }
        if self.stages.len() >= STAGES_MAX {
            return None;
        }
        self.stages.push(Stage::new(name));
        self.stages.last_mut()
    }

    /// The first line, a `bko-holt-error` document: the whole stream.
    fn refused(&mut self, v: &Value) {
        if v.get_u64("schema") != Some(1) {
            return self.fail(format!(
                "format bko-holt-error schema {}, not one this bko-rescue knows",
                v.get_u64("schema").map_or("(none)".to_string(), |n| n.to_string())
            ));
        }
        self.events += 1;
        self.refusal = Some(Refusal {
            code: Clean::line(v.get_str("code").unwrap_or("(no code)"), 64),
            text: Clean::text(v.get_str("text").unwrap_or("(no text)"), cap::PROBLEM),
            exit: v.get("exit").and_then(Value::as_i64),
        });
    }

    fn line(&mut self, raw: &[u8], ms: u64) {
        let raw = raw.strip_suffix(b"\r").unwrap_or(raw);
        if raw.iter().all(u8::is_ascii_whitespace) {
            return;
        }
        let Ok(s) = std::str::from_utf8(raw) else {
            return self.fail("an event line that isn't UTF-8".to_string());
        };
        let v = match json::parse(s) {
            Ok(v) => v,
            Err(e) => return self.fail(format!("an event line that isn't JSON ({})", e.message)),
        };
        if self.refusal.is_some() {
            return self.fail("a line after bko-holt's error document, which is the whole stream".to_string());
        }
        let Some(ev) = v.get_str("event").map(str::to_string) else {
            if self.events == 0 && v.get_str("format") == Some("bko-holt-error") {
                return self.refused(&v);
            }
            return self.fail("an event with no \"event\"".to_string());
        };
        if self.ended.is_some() {
            return self.fail("an event after end".to_string());
        }
        self.events += 1;
        if !self.started && ev != "start" {
            return self.fail(format!("{} before start", Clean::line(&ev, 32).as_str()));
        }
        match ev.as_str() {
            "start" => {
                if self.started {
                    return self.fail("a second start".to_string());
                }
                let (fmt, schema) = (v.get_str("format"), v.get_u64("schema"));
                if fmt != Some("bko-holt-events") || schema != Some(1) {
                    return self.fail(format!(
                        "format {} schema {}, not one this bko-rescue knows",
                        Clean::line(fmt.unwrap_or("(none)"), 32).as_str(),
                        schema.map_or("(none)".to_string(), |n| n.to_string())
                    ));
                }
                self.started = true;
                self.commit = v.get_bool("commit");
                self.dry_run = v.get_bool("dry_run");
                self.pid = v.get_u64("pid");
                if let Some(a) = v.get_arr("stages") {
                    if a.len() > STAGES_MAX || a.iter().any(|x| x.as_str().is_none()) {
                        return self.fail("start's stages aren't a short list of names".to_string());
                    }
                    // A panic before the stages were known names none.
                    if !a.is_empty() {
                        self.stages = a.iter().filter_map(Value::as_str).map(Stage::new).collect();
                        self.stages_named = true;
                    }
                    let counts = v.get("stage_commands");
                    for s in &mut self.stages {
                        s.commands = counts.and_then(|c| c.get_u64(s.name.as_str()));
                    }
                }
            }
            "problems" => {
                let list = v.get_arr("problems").map(|a| a.as_slice()).unwrap_or_default();
                let ps = list
                    .iter()
                    .take(PROBLEMS_MAX)
                    .map(|p| match p.as_str() {
                        Some(t) => (Clean::line("other", 64), Clean::text(t, cap::PROBLEM)),
                        None => (
                            Clean::line(p.get_str("code").unwrap_or("other"), 64),
                            Clean::text(p.get_str("text").unwrap_or("(no text)"), cap::PROBLEM),
                        ),
                    })
                    .collect();
                self.problems = Some(ps);
            }
            "stage" => {
                let now = self.stages.iter_mut().filter(|s| s.state == State::Running);
                now.for_each(|s| s.state = State::Done);
                if let Some(s) = self.stage_mut(&v) {
                    s.state = State::Running;
                    s.from_ms = ms;
                }
                self.now = None;
                self.bytes = None;
                self.total = None;
                self.part = None;
            }
            "skip" => {
                // A state it doesn't know fails closed: the run isn't then
                // plainly done.
                let known = matches!(v.get_str("state"), Some("done" | "nothing"));
                self.odd_skip |= !known;
                if let Some(s) = self.stage_mut(&v) {
                    s.state = if known { State::Skipped } else { State::SkippedWhy };
                }
            }
            "cmd" => {
                let would = v.get_bool("would") == Some(true);
                self.would_pending = would;
                if would {
                    self.would += 1;
                    if let Some(s) = self.stage_mut(&v) {
                        s.would += 1;
                    }
                } else {
                    self.ran += 1;
                }
                let first = v.get_arr("argv").and_then(|a| a.first()).and_then(Value::as_str).unwrap_or("?");
                let field = |k: &str| Clean::line(v.get_str(k).unwrap_or("?"), 128).as_str().to_string();
                // A kind it doesn't know is shown as its text.
                let what = match v.get_str("kind") {
                    None | Some("run") => doing(first),
                    Some("write") => format!("writing {}", field("path")),
                    Some("check") => format!("checking {}", field("dev")),
                    Some("note") => field("why"),
                    Some(k) => format!("a {} step: {}", Clean::line(k, 32).as_str(), Clean::line(first, 64).as_str()),
                };
                let mut t = if would { format!("would run: {what}") } else { what };
                if let (Some(n), Some(of)) = (v.get_u64("n"), v.get_u64("of")) {
                    t.push_str(&format!(" (command {n} of {of} in this stage)"));
                }
                self.now = Some(Clean::line(&t, cap::LINE));
                // Each archiver run's bar starts again.
                if let Some(a) = v.get("archiver").filter(|a| a.get_u64("part").is_some()) {
                    self.part = a.get_u64("part").zip(a.get_u64("parts"));
                    self.bytes = None;
                    self.total = None;
                }
            }
            // A dry run's commands have none: one after them means it ran.
            "cmd_done" => {
                if std::mem::take(&mut self.would_pending) {
                    self.ran += 1;
                }
            }
            "progress" => {
                if let Some(b) = v.get_u64("bytes") {
                    self.bytes = Some(b);
                }
                // Null until it is known.
                if let Some(t) = v.get_u64("total_bytes") {
                    self.total = Some(t);
                }
                if let (Some(p), Some(ps)) = (v.get_u64("part"), v.get_u64("parts")) {
                    self.part = Some((p, ps));
                }
            }
            "warning" => self.warnings += 1,
            "stopping" => {
                self.stopping = true;
                self.now = Some(Clean::ours("stopping after the command now running"));
            }
            "stage_done" => {
                let checked = v.get_bool("checked");
                if let Some(s) = self.stage_mut(&v) {
                    s.state = if checked == Some(false) { State::Would } else { State::Done };
                    s.took_ms = Some(ms.saturating_sub(s.from_ms));
                }
                self.now = None;
            }
            "failed" => {
                let text = Clean::text(v.get_str("text").unwrap_or("(no text)"), cap::PROBLEM);
                let stage = match self.stage_mut(&v) {
                    Some(s) => {
                        s.state = State::Failed;
                        s.name.clone()
                    }
                    None => Clean::line(v.get_str("stage").unwrap_or("?"), 32),
                };
                let tail = v.get_arr("tail").map(|a| a.as_slice()).unwrap_or_default();
                self.failed = Some(Failure {
                    stage,
                    text,
                    code: Clean::line(v.get_str("code").unwrap_or("(no code)"), 64),
                    program: v.get_str("program").map(|p| Clean::line(p, 64)),
                    tail: tail.iter().filter_map(Value::as_str).take(TAIL_MAX).map(|l| Clean::line(l, cap::LOG_LINE)).collect(),
                });
            }
            "end" => match v.get("exit").and_then(Value::as_i64) {
                Some(e) => {
                    self.ended = Some(e);
                    self.stopped = v.get_bool("stopped");
                }
                None => self.fail("an end with no exit".to_string()),
            },
            _ => self.unknown += 1,
        }
    }

    /// The text above the gauge: a row a stage, then what runs now.
    pub fn text(&self, ms: u64) -> Clean {
        let mut s = String::new();
        for st in &self.stages {
            let label = stage_label(st.name.as_str()).unwrap_or(st.name.as_str());
            let (mark, after) = match st.state {
                State::Waiting => ("[ ]", String::new()),
                State::Running => ("[>]", format!("running for {}", secs(ms.saturating_sub(st.from_ms)))),
                State::Done => ("[x]", st.took_ms.map(secs).unwrap_or_default()),
                State::Would => ("[~]", format!("would run {} command{}", st.would, if st.would == 1 { "" } else { "s" })),
                State::Skipped => ("[-]", "nothing to do".to_string()),
                State::SkippedWhy => ("[-]", "skipped, why unknown".to_string()),
                State::Failed => ("[!]", "FAILED".to_string()),
            };
            let row = format!("  {mark} {label:<26}{after}");
            s.push_str(row.trim_end());
            s.push('\n');
        }
        s.push('\n');
        match (&self.now, self.started) {
            (Some(n), _) => s.push_str(&format!("Now: {}.", n.as_str())),
            (None, false) => s.push_str("Now: starting bko-holt."),
            (None, true) => match self.stages.iter().find(|st| st.state == State::Running) {
                Some(st) => s.push_str(&format!("Now: {}.", stage_label(st.name.as_str()).unwrap_or(st.name.as_str()))),
                None => s.push_str("Now: between stages."),
            },
        }
        // Every piece from outside is a Clean line already.
        Clean::text(&s, cap::PROBLEM)
    }

    /// The bar, from `progress` events; None before any, or while the
    /// total isn't known.
    pub fn pct(&self) -> Option<u8> {
        let (b, t) = (self.bytes?, self.total?);
        Some(if t == 0 { 100 } else { (u128::from(b.min(t)) * 100 / u128::from(t)).min(100) as u8 })
    }

    /// The line under the bar: the figures, from `progress` events (no
    /// file count: J17), which archiver run, and the warnings so far.
    pub fn figures(&self) -> Clean {
        let mut parts: Vec<String> = Vec::new();
        match (self.bytes, self.total) {
            (Some(b), Some(t)) => parts.push(format!("{} of {}", size(b.min(t)), size(t))),
            (Some(b), None) => parts.push(format!("{} so far", size(b))),
            _ => {}
        }
        if let Some((p, ps)) = self.part.filter(|&(_, ps)| ps > 1) {
            parts.push(format!("part {p} of {ps}"));
        }
        if self.warnings > 0 {
            let n = u64::try_from(self.warnings).unwrap_or(u64::MAX);
            parts.push(format!("{} warning{}", thousands(n), if n == 1 { "" } else { "s" }));
        }
        Clean::line(&parts.join("   "), cap::LINE)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::testkit::{fuzz_cases, Rng};
    use crate::ui::{Reply, Scripted};

    fn tour_replies(n_back: bool) -> Vec<Reply> {
        let mut v = vec![Reply::Pick(1), Reply::Text("sftp://x".into())];
        if n_back {
            v.extend([Reply::Back, Reply::Text("sftp://y".into())]);
        }
        v.extend([Reply::Secret(b"abc".to_vec()), Reply::Pick(0), Reply::No, Reply::Ok, Reply::Ok]);
        v
    }

    #[test]
    fn the_tour_goes_forward_and_back() {
        let mut r = tour_replies(true);
        // The restore page with no fake, then the summary.
        r.extend([Reply::Ok, Reply::Ok]);
        let mut ui = Scripted::new(r);
        let mut seen = Seen::default();
        let (n, a) = tour(&mut ui, 0, &mut seen);
        assert_eq!((n, a), (PAGES - 1, Answer::Ok(())));
        assert_eq!(seen.machine, Some(1));
        assert_eq!(seen.address, "sftp://y");
        assert_eq!(seen.password_chars, 3);
        assert_eq!(seen.restore, Some(false));
        assert_eq!(seen.run.as_deref(), Some("not run"));
        assert_eq!(ui.asked[3].0, "input", "Back from the password went to the address");
        assert_eq!(ui.asked[ui.asked.len() - 2], ("message", "Nothing to run".to_string()));
    }

    #[test]
    fn plain_and_quit_stop_it_where_it_is() {
        let mut ui = Scripted::new([Reply::Pick(0), Reply::Plain]);
        assert_eq!(tour(&mut ui, 0, &mut Seen::default()), (1, Answer::Plain));
        let mut ui = Scripted::new([Reply::Quit]);
        assert_eq!(tour(&mut ui, 3, &mut Seen::default()), (3, Answer::Quit));
    }

    /// A dry run's events (holt-json.md, `bko-holt-events`), cut short in
    /// the restore stage.
    const GOOD: &str = r#"{"event":"start","format":"bko-holt-events","schema":1,"version":"0.2.6","generated":"2026-11-24T11:21:00Z","pid":4711,"commit":false,"dry_run":true,"stages":["partition","assemble","format","mount","restore","boot","close"],"stage_commands":{"partition":2},"plan_sha256":null,"log":null,"layout":null}
{"event":"stage","stage":"partition","commands":2}
{"event":"cmd","stage":"partition","n":1,"of":2,"kind":"run","argv":["wipefs","--all","/dev/sdb"],"why":"x","would":true}
{"event":"cmd","stage":"partition","n":2,"of":2,"kind":"run","argv":["sfdisk","/dev/sdb"],"why":"x","would":true}
{"event":"stage_done","stage":"partition","secs":0.01,"checked":false}
{"event":"skip","stage":"assemble","state":"nothing","why":""}
{"event":"stage","stage":"format","commands":9}
{"event":"cmd","stage":"format","n":3,"of":9,"kind":"run","argv":["/sbin/mke2fs","-t","ext4"],"why":"x","would":true}
{"event":"stage_done","stage":"format","secs":0.01,"checked":false}
{"event":"stage","stage":"restore","commands":1}
{"event":"cmd","stage":"restore","n":1,"of":1,"kind":"run","argv":["rustic-bko","restore"],"would":true,"archiver":{"part":2,"parts":21}}
{"event":"progress","stage":"restore","n":1,"part":2,"parts":21,"bytes":38200000000,"total_bytes":61200000000,"secs":82,"errors":0,"files":184233}
{"event":"warning","stage":"restore","n":1,"source":"archiver","text":"restore /x: no xattrs","item":"/x"}
{"event":"wobble","stage":"restore"}
"#;

    fn fed(s: &str) -> Follow {
        let mut f = Follow::new();
        f.feed(s.as_bytes(), 5000);
        f
    }

    fn exit(code: i32) -> Option<ExitStatus> {
        use std::os::unix::process::ExitStatusExt as _;
        Some(ExitStatus::from_raw(code << 8))
    }

    #[test]
    fn events_become_the_stages_the_bar_and_its_line() {
        let f = fed(GOOD);
        assert_eq!(f.error, None);
        assert_eq!((f.events, f.unknown, f.commit, f.dry_run, f.pid), (14, 1, Some(false), Some(true), Some(4711)));
        assert_eq!((f.would, f.ran, f.not_dry()), (4, 0, None));
        let t = f.text(9000);
        let t = t.as_str();
        assert!(t.contains("[~] Make partitions           would run 2 commands\n"), "{t}");
        assert!(t.contains("[-] Set up encryption         nothing to do\n"), "{t}");
        assert!(t.contains("[~] Make filesystems          would run 1 command\n"), "{t}");
        assert!(t.contains("[>] Copy your files back      running for 4 s\n"), "{t}");
        assert!(t.contains("[ ] Make it bootable"), "{t}");
        assert!(t.ends_with("Now: would run: copying your files back (command 1 of 1 in this stage)."), "{t}");
        assert!(t.contains("[ ] Ready the filesystems\n"), "{t}");
        assert_eq!(f.pct(), Some(62));
        // No file count (J17): the part and the warnings instead.
        assert_eq!(f.figures().as_str(), "38.2 GB of 61.2 GB   part 2 of 21   1 warning");
        // The next archiver run's bar starts again; its total not known yet.
        let mut f = fed(GOOD);
        f.feed(b"{\"event\":\"cmd\",\"stage\":\"restore\",\"n\":2,\"of\":3,\"kind\":\"run\",\"argv\":[\"sh\",\"-c\",\"x\"],\"would\":true,\"archiver\":{\"part\":3,\"parts\":21}}\n", 6000);
        assert_eq!((f.pct(), f.figures().as_str()), (None, "part 3 of 21   1 warning"));
        f.feed(b"{\"event\":\"progress\",\"stage\":\"restore\",\"n\":2,\"part\":3,\"parts\":21,\"bytes\":4113000448,\"total_bytes\":null}\n", 6000);
        assert_eq!((f.pct(), f.figures().as_str()), (None, "4.11 GB so far   part 3 of 21   1 warning"));
        assert!(f.text(6000).as_str().ends_with("Now: would run: running sh (command 2 of 3 in this stage)."));
        // The other kinds, one it doesn't know shown as its text.
        for (line, now) in [
            (r#"{"event":"cmd","stage":"boot","n":1,"of":4,"kind":"write","path":"/mnt/bko-target/etc/fstab","would":true}"#, "would run: writing /mnt/bko-target/etc/fstab (command 1 of 4 in this stage)"),
            (r#"{"event":"cmd","stage":"boot","n":2,"of":4,"kind":"check","dev":"/dev/sdb1","would":true}"#, "would run: checking /dev/sdb1 (command 2 of 4 in this stage)"),
            (r#"{"event":"cmd","stage":"boot","n":3,"of":4,"kind":"note","why":"the ESP is copied","would":true}"#, "would run: the ESP is copied (command 3 of 4 in this stage)"),
            (r#"{"event":"cmd","stage":"boot","n":4,"of":4,"kind":"teleport","argv":["beam"],"would":true}"#, "would run: a teleport step: beam (command 4 of 4 in this stage)"),
        ] {
            let mut f = fed(GOOD);
            f.feed(format!("{line}\n").as_bytes(), 6000);
            assert_eq!(f.now.as_ref().map(Clean::as_str), Some(now));
        }
    }

    #[test]
    fn a_failure_and_the_end() {
        let mut f = fed(GOOD);
        f.feed(b"{\"event\":\"stage\",\"stage\":\"boot\"}\n{\"event\":\"failed\",\"stage\":\"boot\",\"n\":1,\"code\":\"cmd_failed\",\"program\":\"grub-install\",\"text\":\"grub-install failed \\u001b[2J(exit status: 1):\\n    EFI\",\"tail\":[\"EFI\",\"more \\u001b[31m\"]}\n{\"event\":\"end\",\"exit\":1,\"stopped\":false}\n", 7000);
        assert_eq!(f.error, None);
        assert_eq!((f.ended, f.stopped), (Some(1), Some(false)));
        let x = f.failed.clone().unwrap();
        assert_eq!((x.stage.as_str(), x.text.as_str(), x.code.as_str()), ("boot", "grub-install failed ?[2J(exit status: 1):\n    EFI", "cmd_failed"));
        assert_eq!((x.program.as_ref().map(Clean::as_str), x.tail.iter().map(Clean::as_str).collect::<Vec<_>>()), (Some("grub-install"), vec!["EFI", "more ?[31m"]));
        assert!(f.text(7000).as_str().contains("[!] Make it bootable          FAILED"));
        let o = judge(&f, exit(1), Vec::new(), false);
        assert_eq!((o.title.as_str(), o.short.as_str()), ("Problem: the restore failed", "failed in boot: cmd_failed (exit status 1)"));
        let t = o.text.as_str();
        assert!(t.starts_with("Stage boot failed: grub-install failed ?[2J(exit status: 1):\n    EFI\n\nA command (grub-install) failed."), "{t}");
        assert!(t.contains("Its last lines:\n    EFI\n    more ?[31m\n\nbko-holt ended with exit status 1."), "{t}");
        // A code it doesn't know: the general help.
        let mut g = f.clone();
        g.failed.as_mut().unwrap().code = Clean::line("frobbed", 64);
        assert!(judge(&g, exit(1), Vec::new(), false).text.as_str().contains("Something in this stage went wrong"));
        f.feed(b"{\"event\":\"stage\",\"stage\":\"close\"}\n", 7000);
        assert_eq!(f.error.as_ref().map(Clean::as_str), Some("an event after end"));
    }

    /// The key and password files are zeroed, then unlinked (holt-json.md,
    /// Secrets 6): a second link to one sees only zeros.
    #[test]
    fn key_files_are_zeroed_then_unlinked() {
        let dir = std::env::temp_dir().join(format!("bko-demo-wipe-{}", std::process::id()));
        let _ = fs::remove_dir_all(&dir);
        fs::create_dir(&dir).unwrap();
        let (k, other) = (dir.join("0"), dir.join("seen"));
        fs::write(&k, "correct horse battery staple".repeat(300)).unwrap();
        fs::hard_link(&k, &other).unwrap();
        wipe_file(&k);
        assert!(fs::symlink_metadata(&k).is_err(), "unlinked");
        let left = fs::read(&other).unwrap();
        assert!(left.len() == 28 * 300 && left.iter().all(|&b| b == 0), "zeroed to its length");
        // A symlink in its place is unlinked, its target untouched.
        std::os::unix::fs::symlink(&other, &k).unwrap();
        fs::write(&other, "kept").unwrap();
        wipe_file(&k);
        assert!(fs::symlink_metadata(&k).is_err());
        assert_eq!(fs::read(&other).unwrap(), b"kept");
        fs::remove_dir_all(&dir).unwrap();
    }

    /// How each kind of ending reads (holt-json.md, demo.rs's changes 4-8).
    #[test]
    fn every_ending() {
        let start = r#"{"event":"start","format":"bko-holt-events","schema":1,"commit":false,"dry_run":true,"stages":["partition","boot"]}"#;
        let run = |body: &str, code: i32| {
            let f = fed(&format!("{start}\n{body}\n"));
            assert_eq!(f.error, None, "{body}");
            judge(&f, exit(code), Vec::new(), false)
        };
        // Every stage would run: Done, as a dry run.
        let o = run(r#"{"event":"stage","stage":"partition","commands":1}
{"event":"cmd","stage":"partition","n":1,"of":1,"argv":["sfdisk"],"would":true}
{"event":"stage_done","stage":"partition","checked":false}
{"event":"skip","stage":"boot","state":"done","why":""}
{"event":"end","exit":0,"stopped":false}"#, 0);
        assert_eq!((o.title.as_str(), o.short.as_str()), ("Done", "done"));
        assert!(o.text.as_str().contains("It was a dry run: 1 commands would have run, and none did."), "{}", o.text.as_str());
        // A skip for a reason it doesn't know: not Done.
        let o = run(r#"{"event":"skip","stage":"partition","state":"would_need_restore","why":""}
{"event":"end","exit":0,"stopped":false}"#, 0);
        assert_eq!(o.short, "unclear: a stage skipped for an unknown reason");
        // Principle 6: a stage not seen through isn't done, end 0 or not.
        let o = run(r#"{"event":"stage","stage":"partition","commands":1}
{"event":"end","exit":0,"stopped":false}"#, 0);
        assert_eq!((o.title.as_str(), o.short.as_str()), ("Problem: the restore can't be called done", "unclear: stage partition never finished"));
        let o = run(r#"{"event":"skip","stage":"partition","state":"done","why":""}
{"event":"end","exit":0,"stopped":false}"#, 0);
        assert_eq!(o.short, "unclear: stage boot never began");
        // Nor a dry run's stage that showed fewer commands than start counted.
        let counted = start.replace("\"dry_run\":true", "\"dry_run\":true,\"stage_commands\":{\"partition\":2,\"boot\":0}");
        let f = fed(&format!("{counted}\n{}\n", r#"{"event":"stage","stage":"partition","commands":2}
{"event":"cmd","stage":"partition","n":1,"of":2,"argv":["sfdisk"],"would":true}
{"event":"stage_done","stage":"partition","checked":false}
{"event":"stage","stage":"boot","commands":0}
{"event":"stage_done","stage":"boot","checked":false}
{"event":"end","exit":0,"stopped":false}"#));
        assert_eq!(judge(&f, exit(0), Vec::new(), false).short, "unclear: stage partition showed 1 of its 2 commands");
        let f = fed(&format!("{counted}\n{}\n", r#"{"event":"stage","stage":"partition","commands":2}
{"event":"cmd","stage":"partition","n":1,"of":2,"argv":["wipefs"],"would":true}
{"event":"cmd","stage":"partition","n":2,"of":2,"argv":["sfdisk"],"would":true}
{"event":"stage_done","stage":"partition","checked":false}
{"event":"stage","stage":"boot","commands":0}
{"event":"stage_done","stage":"boot","checked":false}
{"event":"end","exit":0,"stopped":false}"#));
        assert_eq!(judge(&f, exit(0), Vec::new(), false).short, "done");
        let f = fed(&format!("{start}\n{{\"event\":\"skip\",\"stage\":\"boot\",\"state\":\"frob\"}}\n"));
        assert!(f.text(0).as_str().contains("[-] Make it bootable          skipped, why unknown"), "{}", f.text(0).as_str());
        // Problems: the run blocked, nothing ran.
        let o = run(r#"{"event":"problems","problems":[{"code":"not_root","text":"load writes disks: run it as root"},"an old string"],"exit":4}
{"event":"end","exit":4,"stopped":false}"#, 4);
        assert_eq!((o.title.as_str(), o.short.as_str()), ("Problem: nothing ran", "blocked: not_root (exit status 4)"));
        assert!(o.text.as_str().contains("\n  not_root: load writes disks: run it as root\n  other: an old string\n"), "{}", o.text.as_str());
        // Stopped by bko-holt (exit 5), with its stopping event first.
        let f = fed(&format!("{start}\n{{\"event\":\"stage\",\"stage\":\"partition\"}}\n{{\"event\":\"stopping\",\"signal\":\"SIGTERM\"}}\n"));
        assert!(f.stopping && f.text(0).as_str().ends_with("Now: stopping after the command now running."));
        let o = run(r#"{"event":"stopping","signal":"SIGTERM"}
{"event":"end","exit":5,"stopped":true}"#, 5);
        assert_eq!((o.title.as_str(), o.short.as_str()), ("Stopped", "stopped by bko-holt (exit status 5)"));
        // 101: bko-holt's own failure, not a stage's.
        let o = run(r#"{"event":"failed","stage":null,"n":null,"code":"internal","text":"bko-holt panicked: index out of bounds"}
{"event":"end","exit":101,"stopped":false}"#, 101);
        assert_eq!((o.title.as_str(), o.short.as_str()), ("Problem: bko-holt failed", "internal error (exit status 101)"));
        assert!(o.text.as_str().starts_with("bko-holt stopped on an internal error: report it.  It said: bko-holt panicked"), "{}", o.text.as_str());
    }

    /// A first line that is a bko-holt-error document is the whole stream
    /// (change 6): the refusal, not "an event with no event".
    #[test]
    fn a_refusal_document() {
        let doc = r#"{"format":"bko-holt-error","schema":1,"version":"0.2.6","generated":"2026-11-24T10:00:00Z","dry_run":true,"exit":2,"code":"no_commit","text":"bko-holt load: BKO_HOLT_NO_COMMIT=1 is set (a dry run), so --commit is refused; nothing was written to any disk"}"#;
        let f = fed(&format!("{doc}\n"));
        assert_eq!((f.error.clone(), f.started, f.events), (None, false, 1));
        let r = f.refusal.clone().unwrap();
        assert_eq!((r.code.as_str(), r.exit), ("no_commit", Some(2)));
        let o = judge(&f, exit(2), Vec::new(), false);
        assert_eq!((o.title.as_str(), o.short.as_str()), ("Problem: bko-holt refused the run", "refused: no_commit"));
        assert!(o.text.as_str().contains("refused it before doing anything: bko-holt load: BKO_HOLT_NO_COMMIT=1 is set"), "{}", o.text.as_str());
        assert!(o.text.as_str().contains("(code no_commit, exit status 2; bko-holt ended with exit status 2.)"), "{}", o.text.as_str());
        // Nothing may follow it, and it must be the first line.
        let f = fed(&format!("{doc}\n{{\"event\":\"start\"}}\n"));
        assert_eq!(f.error.as_ref().map(Clean::as_str), Some("a line after bko-holt's error document, which is the whole stream"));
        let f = fed(&format!("{}\n{doc}\n", GOOD.lines().next().unwrap()));
        assert_eq!(f.error.as_ref().map(Clean::as_str), Some("an event with no \"event\""));
        let f = fed(&format!("{}\n", doc.replace("\"schema\":1", "\"schema\":2")));
        assert_eq!(f.error.as_ref().map(Clean::as_str), Some("format bko-holt-error schema 2, not one this bko-rescue knows"));
        assert_eq!(f.refusal, None);
    }

    /// The demo stops unless start says a dry run that doesn't commit
    /// (change 7), or once a command really runs.
    #[test]
    fn only_a_dry_run_is_followed() {
        let st = |more: &str| format!("{{\"event\":\"start\",\"format\":\"bko-holt-events\",\"schema\":1{more}}}\n");
        assert_eq!(fed(&st(",\"commit\":false,\"dry_run\":true")).not_dry(), None);
        assert_eq!(fed(&st(",\"commit\":true,\"dry_run\":true")).not_dry(), Some(NotDry::Commits));
        assert_eq!(fed(&st(",\"dry_run\":true")).not_dry(), Some(NotDry::Commits), "commit unsaid fails closed");
        assert_eq!(fed(&st(",\"commit\":false,\"dry_run\":false")).not_dry(), Some(NotDry::NotDryRun));
        assert_eq!(fed(&st(",\"commit\":false")).not_dry(), Some(NotDry::NotDryRun), "dry_run unsaid fails closed");
        let mut f = fed(&st(",\"commit\":false,\"dry_run\":true"));
        f.feed(b"{\"event\":\"stage\",\"stage\":\"partition\"}\n{\"event\":\"cmd\",\"stage\":\"partition\",\"n\":1,\"of\":1,\"argv\":[\"wipefs\"],\"would\":false}\n", 0);
        assert_eq!((f.ran, f.not_dry()), (1, Some(NotDry::Ran)));
        let o = judge(&f, None, Vec::new(), true);
        assert_eq!(o.short, "stopped: a command ran", "Cancel or not, the reason is the run's");
        // A cmd_done after one that would run: it ran after all.
        let mut f = fed(&st(",\"commit\":false,\"dry_run\":true"));
        f.feed(b"{\"event\":\"stage\",\"stage\":\"partition\"}\n{\"event\":\"cmd\",\"stage\":\"partition\",\"n\":1,\"of\":1,\"argv\":[\"wipefs\"],\"would\":true}\n", 0);
        assert_eq!(f.not_dry(), None);
        f.feed(b"{\"event\":\"cmd_done\",\"stage\":\"partition\",\"n\":1,\"exit\":0}\n", 0);
        assert_eq!((f.ran, f.not_dry()), (1, Some(NotDry::Ran)));
        assert_eq!(Follow::new().not_dry(), None, "nothing to say before start");
    }

    #[test]
    fn what_stops_it() {
        let cases: &[(&str, &str)] = &[
            ("{\"event\":\"stage\",\"stage\":\"x\"}\n", "stage before start"),
            ("{\"event\":\"start\",\"format\":\"bko-holt-events\",\"schema\":2}\n", "format bko-holt-events schema 2, not one this bko-rescue knows"),
            ("{\"event\":\"start\",\"format\":\"other\",\"schema\":1}\n", "format other schema 1, not one this bko-rescue knows"),
            ("not json\n", "an event line that isn't JSON"),
            ("{\"no\":1}\n", "an event with no \"event\""),
            ("{\"format\":\"bko-holt-plan\",\"schema\":1}\n", "an event with no \"event\""),
            ("\u{1b}\n", "an event line that isn't JSON"),
        ];
        for (input, want) in cases {
            let f = fed(input);
            let e = f.error.as_ref().map(Clean::as_str).unwrap_or("");
            assert!(e.starts_with(want), "{input:?}: {e:?}");
        }
        let mut f = Follow::new();
        f.feed(&[0xff, b'\n'], 0);
        assert_eq!(f.error.as_ref().map(Clean::as_str), Some("an event line that isn't UTF-8"));
        let mut f = Follow::new();
        f.feed(&vec![b'x'; EVENT_LINE_MAX + 1], 0);
        assert_eq!(f.error.as_ref().map(Clean::as_str), Some("an event line over 64 KiB"));
        let f = fed("{\"event\":\"start\",\"format\":\"bko-holt-events\",\"schema\":1,\"commit\":true}\n");
        assert_eq!((f.error.is_none(), f.commit), (true, Some(true)));
    }

    #[test]
    fn split_anywhere_reads_the_same() {
        let whole = fed(GOOD);
        for at in 0..GOOD.len() {
            let mut f = Follow::new();
            f.feed(&GOOD.as_bytes()[..at], 5000);
            f.feed(&GOOD.as_bytes()[at..], 5000);
            assert_eq!(f.text(9000), whole.text(9000), "split at {at}");
            assert_eq!((f.events, f.pct(), f.figures()), (whole.events, whole.pct(), whole.figures()));
        }
    }

    #[test]
    fn sizes_and_figures() {
        assert_eq!(size(61_203_000_000), "61.2 GB");
        assert_eq!(size(512_110_190_592), "512 GB");
        assert_eq!(size(4_113_000_448), "4.11 GB");
        assert_eq!(size(2_000_398_934_016), "2.00 TB");
        assert_eq!(thousands(184_233), "184,233");
        assert_eq!(thousands(1_000_000), "1,000,000");
        assert_eq!(thousands(12), "12");
        assert_eq!(doing("/usr/sbin/mkfs.vfat"), "making a filesystem");
        assert_eq!(doing("frob\u{1b}[2J"), "running frob?[2J");
        let mut f = Follow::new();
        (f.bytes, f.total) = (Some(u64::MAX), Some(u64::MAX));
        assert_eq!(f.pct(), Some(100), "no overflow near u64::MAX");
        f.bytes = Some(u64::MAX / 2);
        assert_eq!(f.pct(), Some(49));
        (f.bytes, f.total) = (Some(9), Some(3));
        assert_eq!((f.pct(), f.figures().as_str()), (Some(100), "0.00 GB of 0.00 GB"));
        f.total = Some(0);
        assert_eq!(f.pct(), Some(100));
        f.part = Some((1, 1));
        f.warnings = 1234;
        assert_eq!(f.figures().as_str(), "0.00 GB of 0.00 GB   1,234 warnings", "one part isn't shown");
    }

    /// Random bytes, and good lines cut and changed, never panic, and
    /// whatever comes out is shown only as Clean text inside its caps.
    #[test]
    fn fuzz_the_follower() {
        let mut rng = Rng::new(0x5eed_f011);
        let good: Vec<&str> = GOOD.lines().collect();
        let junk: &[&[u8]] = &[b"\n", b"{", b"}", b"\"", b"\\u0000", b"\x1b[2J", b"\xff", b"[", b"1e999", b"-1", b"null", b",", b":"];
        for _ in 0..fuzz_cases(100_000) / 20 {
            let mut f = Follow::new();
            let mut stream: Vec<u8> = Vec::new();
            for _ in 0..rng.below(12) {
                let mut l = rng.pick(&good).as_bytes().to_vec();
                match rng.below(4) {
                    0 => l.truncate(rng.below(l.len() as u64 + 1) as usize),
                    1 => {
                        let i = rng.below(l.len() as u64) as usize;
                        l[i] = rng.below(256) as u8;
                    }
                    2 => {
                        let i = rng.below(l.len() as u64 + 1) as usize;
                        let j: &[u8] = junk[rng.below(junk.len() as u64) as usize];
                        l.splice(i..i, j.iter().copied());
                    }
                    _ => {}
                }
                stream.extend(l);
                stream.push(b'\n');
            }
            for _ in 0..rng.below(64) {
                stream.push(rng.below(256) as u8);
            }
            let mut i = 0;
            while i < stream.len() {
                let n = 1 + rng.below(40) as usize;
                let end = (i + n).min(stream.len());
                f.feed(&stream[i..end], rng.below(100_000));
                i = end;
            }
            let t = f.text(rng.below(1_000_000));
            assert!(t.chars().all(|c| c == '\n' || crate::text::allowed(c)));
            assert!(f.stages.len() <= STAGES_MAX);
            assert!(f.partial.len() <= EVENT_LINE_MAX);
            let _ = (f.pct(), f.figures());
        }
    }

    fn scratch(name: &str) -> PathBuf {
        let d = std::env::temp_dir().join(format!("bko-rescue-demo-test-{}-{name}", std::process::id()));
        let _ = fs::remove_dir_all(&d);
        fs::create_dir(&d).unwrap();
        d
    }

    /// A fake in sh: checks the key file and the password file it is
    /// given, then writes events.  Once they are checked it leaves its pid
    /// in `pid`, then the key file's path in `key-was` and the password
    /// file's in `password-was` (whole, by a rename), so a test can wait
    /// for `key-was` and know the fake runs.
    ///
    /// A child sh writes the file, not this process: a test in another
    /// thread that forks to start its own fake while this one writes would
    /// carry the open file into its child until that execs, and starting
    /// this fake then fails with ETXTBSY ("Text file busy").  The child
    /// has exited before the fake is started, its copy with it.
    fn sh_fake(dir: &Path, body: &str) -> String {
        let p = dir.join("fake");
        let d = dir.display();
        let script = format!(
            "#!/bin/sh\nfor a; do case $prev in --password-file) p=$a;; esac; case $a in {KEY_NAME}=*) k=${{a#{KEY_NAME}=}};; --commit) exit 7;; esac; prev=$a; done\n\
             [ \"$BKO_HOLT_NO_COMMIT\" = 1 ] || exit 8\n\
             [ \"$(cat \"$k\")\" = pw ] || exit 9\n\
             [ -s \"$p\" ] || exit 10\n\
             echo $$ >\"{d}/pid\"\n\
             echo \"$p\" >\"{d}/password-was.new\" && mv \"{d}/password-was.new\" \"{d}/password-was\"\n\
             echo \"$k\" >\"{d}/key-was.new\" && mv \"{d}/key-was.new\" \"{d}/key-was\"\n{body}\n"
        );
        let st = Command::new("/bin/sh")
            .args(["-c", "printf '%s' \"$1\" >\"$2.new\" && chmod 755 \"$2.new\" && mv \"$2.new\" \"$2\"", "sh"])
            .arg(script)
            .arg(&p)
            .status()
            .unwrap();
        assert!(st.success(), "writing the fake: {st}");
        p.to_string_lossy().to_string()
    }

    /// A line for a fake's body: wait until the test opens `dir`'s gate.
    fn wait_for_gate(dir: &Path) -> String {
        format!("while [ ! -e '{}' ]; do sleep 0.01; done", dir.join("gate").display())
    }

    /// Wait until `f` holds, a millisecond at a time.  A minute means the
    /// test is stuck: it is never what a test relies on.
    fn wait_until(what: &str, mut f: impl FnMut() -> bool) {
        let t = Instant::now();
        while !f() {
            assert!(t.elapsed().as_secs() < 60, "waited a minute for {what}");
            std::thread::sleep(std::time::Duration::from_millis(1));
        }
    }

    /// Whether process `pid`, a child of ours, has exited and not been
    /// reaped yet (a zombie, as try_wait would then see it).
    fn is_zombie(pid: &str) -> bool {
        if cfg!(target_os = "linux") {
            // The state is the field after the name, which ends at the
            // last ')'.
            let s = fs::read_to_string(format!("/proc/{pid}/stat")).unwrap();
            s.rsplit_once(')').unwrap().1.trim_start().starts_with('Z')
        } else {
            let o = Command::new("ps").args(["-o", "stat=", "-p", pid]).output().unwrap();
            o.stdout.trim_ascii_start().starts_with(b"Z")
        }
    }

    #[test]
    fn the_restore_page_runs_the_fake_and_removes_the_key() {
        let d = scratch("run");
        let ev = "{\"event\":\"start\",\"format\":\"bko-holt-events\",\"schema\":1,\"stages\":[\"partition\"],\"commit\":false,\"dry_run\":true}\n{\"event\":\"stage\",\"stage\":\"partition\"}\n{\"event\":\"cmd\",\"stage\":\"partition\",\"n\":1,\"of\":1,\"argv\":[\"sfdisk\"],\"would\":true}\n{\"event\":\"stage_done\",\"stage\":\"partition\",\"checked\":false}\n{\"event\":\"end\",\"exit\":0,\"stopped\":false}";
        // Nothing typed on the password page: the demo's own password.
        let fake = sh_fake(&d, &format!("[ \"$(cat \"$p\")\" = demo-repository-password ] || exit 12\nprintf '%s\\n' '{}'\nsleep 0.2", ev.replace('\n', "' '")));
        let mut ui = Scripted::new([Reply::Secret(b"pw".to_vec()), Reply::Ok, Reply::Ok]);
        let mut seen = Seen::default();
        assert_eq!(restore(&mut ui, &fake, &mut seen), Answer::Ok(()));
        assert_eq!(seen.run.as_deref(), Some("done"), "{:?}", ui.asked);
        assert_eq!(ui.asked[2], ("message", "Done".to_string()));
        let key = fs::read_to_string(d.join("key-was")).unwrap();
        let key = Path::new(key.trim());
        assert!(!key.exists(), "the key file is gone");
        let pw = fs::read_to_string(d.join("password-was")).unwrap();
        assert!(pw.trim().ends_with("/password") && !Path::new(pw.trim()).exists(), "the password file too: {pw}");
        assert!(!key.parent().unwrap().exists(), "its directory too");
        // The password typed on its page is the one handed on.
        let fake2 = sh_fake(&d, &format!("[ \"$(cat \"$p\")\" = typed-pw ] || exit 11\nprintf '%s\\n' '{}'", ev.replace('\n', "' '")));
        let mut ui = Scripted::new([Reply::Secret(b"pw".to_vec()), Reply::Ok, Reply::Ok]);
        let mut seen = Seen { password: Some(Secret::from_bytes(b"typed-pw")), ..Seen::default() };
        assert_eq!(restore(&mut ui, &fake2, &mut seen), Answer::Ok(()));
        assert_eq!(seen.run.as_deref(), Some("done"), "{:?}", ui.asked);
        // A failing one keeps its stderr for Raw output.
        let fake = sh_fake(&d, "echo 'grub-install: \x1b[31merror' >&2\nexit 3");
        let mut ui = Scripted::new([Reply::Secret(b"pw".to_vec()), Reply::Ok, Reply::Button(RAW), Reply::Back, Reply::Ok]);
        let mut seen = Seen::default();
        assert_eq!(restore(&mut ui, &fake, &mut seen), Answer::Ok(()));
        assert_eq!(seen.run.as_deref(), Some("failed (exit status 3, no end event)"));
        assert_eq!(ui.asked[3].0, "view");
        fs::remove_dir_all(&d).unwrap();
    }

    #[test]
    fn a_wrong_schema_or_a_commit_stops_the_fake() {
        let d = scratch("stop");
        for (start, want) in [
            ("{\"event\":\"start\",\"format\":\"bko-holt-events\",\"schema\":9}", "stopped: bad events"),
            ("{\"event\":\"start\",\"format\":\"bko-holt-events\",\"schema\":1,\"commit\":true}", "stopped: it would commit"),
            ("{\"event\":\"start\",\"format\":\"bko-holt-events\",\"schema\":1,\"commit\":false}", "stopped: not a dry run"),
            (
                "{\"event\":\"start\",\"format\":\"bko-holt-events\",\"schema\":1,\"commit\":false,\"dry_run\":true}' '{\"event\":\"stage\",\"stage\":\"partition\"}' '{\"event\":\"cmd\",\"stage\":\"partition\",\"n\":1,\"of\":1,\"argv\":[\"wipefs\"],\"would\":false}",
                "stopped: a command ran",
            ),
        ] {
            let fake = sh_fake(&d, &format!("printf '%s\\n' '{start}'\nexec sleep 30"));
            let mut ui = Scripted::new([Reply::Secret(b"pw".to_vec()), Reply::Ok, Reply::Ok]);
            let mut seen = Seen::default();
            let t = Instant::now();
            assert_eq!(restore(&mut ui, &fake, &mut seen), Answer::Ok(()));
            assert_eq!(seen.run.as_deref(), Some(want));
            assert!(t.elapsed().as_secs() < 20, "it didn't wait for the fake's sleep");
        }
        // No such program: a message, and nothing left behind.
        let mut ui = Scripted::new([Reply::Secret(b"pw".to_vec()), Reply::Ok]);
        let mut seen = Seen::default();
        assert_eq!(restore(&mut ui, "/nonexistent/fake", &mut seen), Answer::Ok(()));
        assert_eq!(seen.run.as_deref(), Some("not started"));
        fs::remove_dir_all(&d).unwrap();
    }

    /// When a Gated Ui lets its fake end.
    #[derive(Clone, Copy, PartialEq)]
    enum Ends {
        /// While Stop is asked: the answer waits until the fake has
        /// exited.
        AtQuestion,
        /// When the gauge is shown again, which is followed to its end.
        AtGauge,
        /// Not at all: the fake runs on (one that doesn't wait for the
        /// gate).
        Never,
    }

    /// Scripted, with the gate of a fake that waits for it
    /// (`wait_for_gate`) in its hands, so the fake ends at a known point
    /// of the page and not after some sleep.  Each question first waits
    /// for the fake to have checked its key.
    struct Gated {
        ui: Scripted,
        dir: PathBuf,
        ends: Ends,
    }

    impl Gated {
        fn new(dir: &Path, ends: Ends, replies: impl IntoIterator<Item = Reply>) -> Gated {
            // What the last fake left.
            for f in ["gate", "pid", "key-was"] {
                let _ = fs::remove_file(dir.join(f));
            }
            Gated { ui: Scripted::new(replies), dir: dir.to_path_buf(), ends }
        }

        fn open_gate(&self) {
            fs::write(self.dir.join("gate"), "").unwrap();
        }
    }

    impl Ui for Gated {
        fn menu(&mut self, d: &Menu) -> Answer<usize> {
            self.ui.menu(d)
        }
        fn radio(&mut self, d: &Radio) -> Answer<usize> {
            self.ui.radio(d)
        }
        fn input(&mut self, d: &Input) -> Answer<String> {
            self.ui.input(d)
        }
        fn password(&mut self, d: &Password) -> Answer<Secret> {
            self.ui.password(d)
        }
        fn yes_no(&mut self, d: &Question) -> Answer<bool> {
            wait_until("the fake to check its key", || self.dir.join("key-was").exists());
            if self.ends == Ends::AtQuestion {
                self.open_gate();
                let pid = fs::read_to_string(self.dir.join("pid")).unwrap();
                wait_until("the fake to exit", || is_zombie(pid.trim()));
            }
            self.ui.yes_no(d)
        }
        fn message(&mut self, d: &Message) -> Answer<()> {
            self.ui.message(d)
        }
        fn view(&mut self, d: &TextView) -> Answer<()> {
            self.ui.view(d)
        }
        fn progress(&mut self, d: &Progress, tick: &mut dyn FnMut() -> Tick) -> Answer<()> {
            if self.ends == Ends::AtGauge && self.ui.replies.front() == Some(&Reply::Ok) {
                self.open_gate();
                wait_until("the gauge to see the end", || tick() == Tick::Done);
            }
            self.ui.progress(d, tick)
        }
    }

    #[test]
    fn stop_after_it_ended_says_how_it_ended() {
        let d = scratch("ended");
        let ev = "{\"event\":\"start\",\"format\":\"bko-holt-events\",\"schema\":1,\"commit\":false,\"dry_run\":true}\n{\"event\":\"end\",\"exit\":0,\"stopped\":false}";
        let gated = format!("{}\nprintf '%s\\n' '{}'", wait_for_gate(&d), ev.replace('\n', "' '"));
        let fake = sh_fake(&d, &gated);
        // Cancel at the first tick, the fake waiting for its gate; it
        // ends while Stop is asked.
        let r = [Reply::Secret(b"pw".to_vec()), Reply::Button(CANCEL), Reply::Yes, Reply::Ok];
        let mut ui = Gated::new(&d, Ends::AtQuestion, r);
        let mut seen = Seen::default();
        assert_eq!(restore(&mut ui, &fake, &mut seen), Answer::Ok(()), "{:?}", ui.ui.asked);
        assert_eq!(seen.run.as_deref(), Some("done"), "{:?}", ui.ui.asked);
        // Keep going (Back, as Esc gives) goes on following it: the end is
        // seen by the gauge, shown again.
        let r = [Reply::Secret(b"pw".to_vec()), Reply::Button(CANCEL), Reply::Back, Reply::Ok, Reply::Ok];
        let mut ui = Gated::new(&d, Ends::AtGauge, r);
        let mut seen = Seen::default();
        assert_eq!(restore(&mut ui, &fake, &mut seen), Answer::Ok(()), "{:?}", ui.ui.asked);
        assert_eq!(seen.run.as_deref(), Some("done"), "{:?}", ui.ui.asked);
        let kinds: Vec<_> = ui.ui.asked.iter().map(|a| a.0).collect();
        assert_eq!(kinds, ["password", "progress", "yes_no", "progress", "message"]);
        // Plain from the question leaves the page, the fake stopped.
        let fake = sh_fake(&d, "exec sleep 30");
        let mut ui = Gated::new(&d, Ends::Never, [Reply::Secret(b"pw".to_vec()), Reply::Button(CANCEL), Reply::Plain]);
        let t = Instant::now();
        assert_eq!(restore(&mut ui, &fake, &mut seen), Answer::Plain);
        assert!(t.elapsed().as_secs() < 20);
        let key = fs::read_to_string(d.join("key-was")).unwrap();
        assert!(!Path::new(key.trim()).exists(), "the key file is gone");
        fs::remove_dir_all(&d).unwrap();
    }

    /// A big events file is read a few MiB a look, and all of it in the
    /// end.
    #[test]
    fn a_big_events_file_is_read_in_pieces() {
        let d = scratch("big");
        let line = "{\"event\":\"progress\",\"stage\":\"restore\",\"n\":1,\"bytes\":1,\"total_bytes\":2}\n";
        let n = 3 * EVENTS_PER_POLL / line.len();
        let mut body = String::from("{\"event\":\"start\",\"format\":\"bko-holt-events\",\"schema\":1,\"commit\":false,\"dry_run\":true}\n");
        body.push_str(&line.repeat(n));
        body.push_str("{\"event\":\"end\",\"exit\":0}\n");
        fs::write(d.join("events.txt"), &body).unwrap();
        let fake = sh_fake(&d, &format!("cat '{}'", d.join("events.txt").display()));
        let mut run = Run::start(&fake, &Secret::from_bytes(b"pw"), &Secret::from_bytes(DEMO_PASSWORD), &d).unwrap();
        let mut looks = 0;
        while !run.finished() {
            run.poll();
            looks += 1;
            std::thread::sleep(std::time::Duration::from_millis(5));
        }
        assert!(looks >= 4, "{looks} looks");
        assert_eq!((run.follow.error.clone(), run.follow.ended, run.follow.events), (None, Some(0), n + 2));
        drop(run);
        fs::remove_dir_all(&d).unwrap();
    }

    #[test]
    fn a_run_dropped_early_stops_the_fake_and_cleans_up() {
        let d = scratch("drop");
        let fake = sh_fake(&d, "exec sleep 30");
        let run = Run::start(&fake, &Secret::from_bytes(b"pw"), &Secret::from_bytes(DEMO_PASSWORD), &d).unwrap();
        let dir = run.dir.clone();
        assert!(dir.join(KEY_NAME).exists() && dir.join(PASSWORD_NAME).exists());
        let t = Instant::now();
        drop(run);
        assert!(t.elapsed().as_secs() < 10);
        assert!(!dir.exists());
        fs::remove_dir_all(&d).unwrap();
    }
}
