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
//! directory, no newline), runs `PATH load ... --json --key-file
//! NAME=FILE` without `--commit` and with `BKO_HOLT_NO_COMMIT=1`, and
//! follows its events with the gauge.  The key file goes as soon as the
//! fake has exited.  Without `--holt` nothing is run: the demo never
//! starts the real bko-holt by itself.
//!
//! While the fake runs, SIGINT is caught, and in plain mode SIGHUP and
//! SIGTERM too (the full screen's `Term` catches those already).  The
//! page then stops the fake and removes the key file before the signal
//! ends the program.  The fake runs in a process group of its own, so a
//! Ctrl-C on the terminal reaches only bko-rescue.
//!
//! The events are ui.md's "`--json` for load".  That protocol isn't
//! built yet (RT10), so `Follow` reads a provisional form of it; RT12
//! writes the real follower in `app/`.

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
                    // Dropped here: wiped.
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

/// The command line after the program's path.  No `--commit`, ever.
pub const ARGS: &[&str] = &["load", "demo-system.json", "--json", "--through", "close"];

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
    let started = Run::start(holt, &secret, std::env::temp_dir().as_path());
    // The key file has it now: wiped.
    drop(secret);
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

/// The fake bko-holt running, its key file, events and stderr, all in
/// one new 0700 directory.  Dropping it stops the fake if it still runs
/// and removes the files and the directory.
pub struct Run {
    dir: PathBuf,
    key: Option<PathBuf>,
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
    /// Write the key file and start `holt`.
    pub fn start(holt: &str, secret: &Secret, base: &Path) -> io::Result<Run> {
        let dir = private_dir(base)?;
        // From here the Run owns the directory: an error drops it, and
        // Drop removes what was made.
        let mut run = Run {
            dir,
            key: None,
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

    /// The fake exited: take its status, then remove the key file at
    /// once, as load's would be once load exits.
    fn exited(&mut self, st: ExitStatus) {
        self.status = Some(st);
        self.child = None;
        self.remove_key();
    }

    fn remove_key(&mut self) {
        if let Some(k) = self.key.take() {
            let _ = fs::remove_file(k);
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
        if self.follow.error.is_some() || self.follow.commit == Some(true) {
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
        let raw = self.stderr_lines();
        let safe = "Nothing in the backup has been changed.";
        let code = self.status.and_then(|s| s.code());
        let mk = |title: &str, text: String, short: String| Outcome {
            title: Clean::line(title, cap::LINE),
            text: Clean::text(&text, cap::PROBLEM),
            short,
            raw: raw.clone(),
        };
        let f = &self.follow;
        if stopped && f.error.is_none() && f.commit != Some(true) {
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
        if f.commit == Some(true) {
            return mk(
                "Problem: the restore was stopped",
                format!("bko-holt said it would write to the disks, which the demo never asks for, so it was stopped.  {safe}"),
                "stopped: it would commit".to_string(),
            );
        }
        let status = match (code, self.status) {
            (Some(c), _) => format!("exit status {c}"),
            (None, Some(_)) => "killed by a signal".to_string(),
            (None, None) => "no exit status".to_string(),
        };
        match (&f.failed, f.ended) {
            (None, Some(0)) if code == Some(0) => mk(
                "Done",
                format!(
                    "The fake restore went through every stage, {} events in all, and ended with exit status 0.  \
                     The key file it was given is removed.",
                    f.events
                ),
                "done".to_string(),
            ),
            (Some((stage, text)), _) => mk(
                "Problem: the restore failed",
                format!(
                    "Stage {} failed: {}\n\nbko-holt ended with {status}.  {safe}",
                    stage.as_str(),
                    text.as_str()
                ),
                format!("failed in {} ({status})", stage.as_str()),
            ),
            (None, Some(e)) => mk(
                "Problem: the restore failed",
                format!("bko-holt's end event says exit {e}, and it ended with {status}.  {safe}"),
                format!("failed ({status})"),
            ),
            (None, None) => mk(
                "Problem: the restore failed",
                format!("bko-holt ended with {status} and without its end event.  {safe}"),
                format!("failed ({status}, no end event)"),
            ),
        }
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
    Skipped,
    Failed,
}

#[derive(Clone, Debug)]
pub struct Stage {
    pub name: Clean,
    pub state: State,
    /// When it started and how long it took, in ms.
    pub from_ms: u64,
    pub took_ms: Option<u64>,
}

/// bko-holt's events as they come (ui.md, "`--json` for load"), as a
/// gauge shows them.  PROVISIONAL: the protocol is RT10's, not built
/// yet; this reads the form ui.md gives, one JSON object a line:
/// `start` (format `bko-holt-events`, schema 1, `stages`, `commit`)
/// first, then `stage`, `skip`, `cmd`, `cmd_done`, `progress`,
/// `stage_done`, `failed`, and `end` (`exit`) last.  An event it doesn't
/// know is skipped; anything else wrong is an `error`, after which
/// nothing more is read.
#[derive(Clone, Debug, Default)]
pub struct Follow {
    pub stages: Vec<Stage>,
    pub started: bool,
    pub commit: Option<bool>,
    /// The "Now:" line: what the running command does.
    pub now: Option<Clean>,
    pub bytes: Option<(u64, u64)>,
    pub files: Option<u64>,
    pub failed: Option<(Clean, Clean)>,
    pub ended: Option<i64>,
    pub error: Option<Clean>,
    /// Events read, and those skipped as unknown.
    pub events: usize,
    pub unknown: usize,
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
        "sfdisk" | "sgdisk" | "parted" | "gpart" | "fdisk" | "disklabel" => "making partitions",
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
        // Until start names them, the stages ui.md lists.
        for n in ["partition", "assemble", "format", "mount", "restore", "boot", "close"] {
            f.stages.push(Stage { name: Clean::line(n, 32), state: State::Waiting, from_ms: 0, took_ms: None });
        }
        f
    }

    fn fail(&mut self, why: String) {
        if self.error.is_none() {
            self.error = Some(Clean::line(&why, cap::LINE));
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
        self.stages.push(Stage { name: Clean::line(name, 32), state: State::Waiting, from_ms: 0, took_ms: None });
        self.stages.last_mut()
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
        let Some(ev) = v.get_str("event").map(str::to_string) else {
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
                if let Some(a) = v.get_arr("stages") {
                    if a.len() > STAGES_MAX || a.iter().any(|x| x.as_str().is_none()) {
                        return self.fail("start's stages aren't a short list of names".to_string());
                    }
                    self.stages = a
                        .iter()
                        .filter_map(Value::as_str)
                        .map(|n| Stage { name: Clean::line(n, 32), state: State::Waiting, from_ms: 0, took_ms: None })
                        .collect();
                }
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
                self.files = None;
            }
            "skip" => {
                if let Some(s) = self.stage_mut(&v) {
                    s.state = State::Skipped;
                }
            }
            "cmd" => {
                let prog = v.get_arr("argv").and_then(|a| a.first()).and_then(Value::as_str).unwrap_or("?");
                let mut t = doing(prog);
                if let (Some(n), Some(of)) = (v.get_u64("n"), v.get_u64("of")) {
                    t.push_str(&format!(" ({n} of {of})"));
                }
                self.now = Some(Clean::line(&t, cap::LINE));
            }
            "cmd_done" => {}
            "progress" => {
                if let (Some(b), Some(t)) = (v.get_u64("bytes"), v.get_u64("total_bytes")) {
                    self.bytes = Some((b.min(t), t));
                }
                self.files = v.get_u64("files").or(self.files);
            }
            "stage_done" => {
                if let Some(s) = self.stage_mut(&v) {
                    s.state = State::Done;
                    s.took_ms = Some(ms.saturating_sub(s.from_ms));
                }
                self.now = None;
            }
            "failed" => {
                let text = Clean::line(v.get_str("text").unwrap_or("(no text)"), cap::PROBLEM);
                let stage = match self.stage_mut(&v) {
                    Some(s) => {
                        s.state = State::Failed;
                        s.name.clone()
                    }
                    None => Clean::line(v.get_str("stage").unwrap_or("?"), 32),
                };
                self.failed = Some((stage, text));
            }
            "end" => match v.get("exit").and_then(Value::as_i64) {
                Some(e) => self.ended = Some(e),
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
                State::Skipped => ("[-]", "nothing to do".to_string()),
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

    /// The bar, from `progress` events; None before any.
    pub fn pct(&self) -> Option<u8> {
        self.bytes.map(|(b, t)| if t == 0 { 100 } else { (u128::from(b) * 100 / u128::from(t)).min(100) as u8 })
    }

    /// The line under the bar: the figures, from `progress` events.
    pub fn figures(&self) -> Clean {
        let mut s = String::new();
        if let Some((b, t)) = self.bytes {
            s.push_str(&format!("{} of {}", size(b), size(t)));
        }
        if let Some(f) = self.files {
            if !s.is_empty() {
                s.push_str("   ");
            }
            s.push_str(&format!("{} files", thousands(f)));
        }
        Clean::line(&s, cap::LINE)
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

    const GOOD: &str = r#"{"event":"start","format":"bko-holt-events","schema":1,"version":"0.3.0","stages":["partition","assemble","format","mount","restore","boot","close"],"commit":false,"log":"/run/bko-holt/log/load.log"}
{"event":"stage","stage":"partition"}
{"event":"cmd","stage":"partition","n":1,"of":2,"argv":["sfdisk","/dev/sdb"],"why":"x"}
{"event":"cmd_done","stage":"partition","n":1,"status":0,"secs":0.4}
{"event":"stage_done","stage":"partition"}
{"event":"skip","stage":"assemble","state":"nothing"}
{"event":"stage","stage":"format"}
{"event":"cmd","stage":"format","n":3,"of":9,"argv":["/sbin/mke2fs","-t","ext4"],"why":"x"}
{"event":"stage_done","stage":"format"}
{"event":"stage","stage":"restore"}
{"event":"progress","stage":"restore","files":184233,"bytes":38200000000,"total_bytes":61200000000}
{"event":"wobble","stage":"restore"}
"#;

    fn fed(s: &str) -> Follow {
        let mut f = Follow::new();
        f.feed(s.as_bytes(), 5000);
        f
    }

    #[test]
    fn events_become_the_stages_the_bar_and_its_line() {
        let f = fed(GOOD);
        assert_eq!(f.error, None);
        assert_eq!((f.events, f.unknown, f.commit), (12, 1, Some(false)));
        let t = f.text(9000);
        let t = t.as_str();
        assert!(t.contains("[x] Make partitions           0 s\n"), "{t}");
        assert!(t.contains("[-] Set up encryption         nothing to do\n"), "{t}");
        assert!(t.contains("[>] Copy your files back      running for 4 s\n"), "{t}");
        assert!(t.contains("[ ] Make it bootable"), "{t}");
        assert!(t.ends_with("Now: Copy your files back."), "{t}");
        assert!(t.contains("[ ] Ready the filesystems\n"), "{t}");
        assert_eq!(f.pct(), Some(62));
        assert_eq!(f.figures().as_str(), "38.2 GB of 61.2 GB   184,233 files");
        let mut f = fed(GOOD);
        f.feed(b"{\"event\":\"cmd\",\"stage\":\"restore\",\"n\":7,\"of\":9,\"argv\":[\"rustic-bko\",\"restore\"]}\n", 6000);
        assert!(f.text(6000).as_str().ends_with("Now: copying your files back (7 of 9)."));
    }

    #[test]
    fn a_failure_and_the_end() {
        let mut f = fed(GOOD);
        f.feed(b"{\"event\":\"stage\",\"stage\":\"boot\"}\n{\"event\":\"failed\",\"stage\":\"boot\",\"text\":\"grub-install failed \\u001b[2J(exit status: 1)\"}\n{\"event\":\"end\",\"exit\":1}\n", 7000);
        assert_eq!(f.error, None);
        assert_eq!(f.ended, Some(1));
        let (stage, text) = f.failed.clone().unwrap();
        assert_eq!((stage.as_str(), text.as_str()), ("boot", "grub-install failed ?[2J(exit status: 1)"));
        assert!(f.text(7000).as_str().contains("[!] Make it bootable          FAILED"));
        f.feed(b"{\"event\":\"stage\",\"stage\":\"close\"}\n", 7000);
        assert_eq!(f.error.as_ref().map(Clean::as_str), Some("an event after end"));
    }

    #[test]
    fn what_stops_it() {
        let cases: &[(&str, &str)] = &[
            ("{\"event\":\"stage\",\"stage\":\"x\"}\n", "stage before start"),
            ("{\"event\":\"start\",\"format\":\"bko-holt-events\",\"schema\":2}\n", "format bko-holt-events schema 2, not one this bko-rescue knows"),
            ("{\"event\":\"start\",\"format\":\"other\",\"schema\":1}\n", "format other schema 1, not one this bko-rescue knows"),
            ("not json\n", "an event line that isn't JSON"),
            ("{\"no\":1}\n", "an event with no \"event\""),
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
        f.bytes = Some((u64::MAX, u64::MAX));
        assert_eq!(f.pct(), Some(100), "no overflow near u64::MAX");
        f.bytes = Some((u64::MAX / 2, u64::MAX));
        assert_eq!(f.pct(), Some(49));
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

    /// A fake in sh: checks the key file it is given, then writes events.
    fn sh_fake(dir: &Path, body: &str) -> String {
        use std::os::unix::fs::PermissionsExt as _;
        let p = dir.join("fake");
        let script = format!(
            "#!/bin/sh\nfor a; do case $a in {KEY_NAME}=*) k=${{a#{KEY_NAME}=}};; --commit) exit 7;; esac; done\n\
             [ \"$BKO_HOLT_NO_COMMIT\" = 1 ] || exit 8\n\
             [ \"$(cat \"$k\")\" = pw ] || exit 9\n\
             echo \"$k\" >\"{}/key-was\"\n{body}\n",
            dir.display()
        );
        fs::write(&p, script).unwrap();
        fs::set_permissions(&p, fs::Permissions::from_mode(0o755)).unwrap();
        p.to_string_lossy().to_string()
    }

    #[test]
    fn the_restore_page_runs_the_fake_and_removes_the_key() {
        let d = scratch("run");
        let ev = "{\"event\":\"start\",\"format\":\"bko-holt-events\",\"schema\":1,\"stages\":[\"partition\"],\"commit\":false}\n{\"event\":\"stage\",\"stage\":\"partition\"}\n{\"event\":\"stage_done\",\"stage\":\"partition\"}\n{\"event\":\"end\",\"exit\":0}";
        let fake = sh_fake(&d, &format!("printf '%s\\n' '{}'\nsleep 0.2", ev.replace('\n', "' '")));
        let mut ui = Scripted::new([Reply::Secret(b"pw".to_vec()), Reply::Ok, Reply::Ok]);
        let mut seen = Seen::default();
        assert_eq!(restore(&mut ui, &fake, &mut seen), Answer::Ok(()));
        assert_eq!(seen.run.as_deref(), Some("done"), "{:?}", ui.asked);
        assert_eq!(ui.asked[2], ("message", "Done".to_string()));
        let key = fs::read_to_string(d.join("key-was")).unwrap();
        let key = Path::new(key.trim());
        assert!(!key.exists(), "the key file is gone");
        assert!(!key.parent().unwrap().exists(), "its directory too");
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
        ] {
            let fake = sh_fake(&d, &format!("echo '{start}'\nexec sleep 30"));
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

    /// Scripted, but each question waits a while first, as a person
    /// would.
    struct Slow(Scripted, u64);

    impl Ui for Slow {
        fn menu(&mut self, d: &Menu) -> Answer<usize> {
            self.0.menu(d)
        }
        fn radio(&mut self, d: &Radio) -> Answer<usize> {
            self.0.radio(d)
        }
        fn input(&mut self, d: &Input) -> Answer<String> {
            self.0.input(d)
        }
        fn password(&mut self, d: &Password) -> Answer<Secret> {
            self.0.password(d)
        }
        fn yes_no(&mut self, d: &Question) -> Answer<bool> {
            std::thread::sleep(std::time::Duration::from_millis(self.1));
            self.0.yes_no(d)
        }
        fn message(&mut self, d: &Message) -> Answer<()> {
            self.0.message(d)
        }
        fn view(&mut self, d: &TextView) -> Answer<()> {
            self.0.view(d)
        }
        fn progress(&mut self, d: &Progress, tick: &mut dyn FnMut() -> Tick) -> Answer<()> {
            self.0.progress(d, tick)
        }
    }

    #[test]
    fn stop_after_it_ended_says_how_it_ended() {
        let d = scratch("ended");
        let ev = "{\"event\":\"start\",\"format\":\"bko-holt-events\",\"schema\":1,\"commit\":false}\n{\"event\":\"end\",\"exit\":0}";
        let fake = sh_fake(&d, &format!("printf '%s\\n' '{}'", ev.replace('\n', "' '")));
        // Cancel at the first tick; the fake ends while Stop is asked.
        let r = [Reply::Secret(b"pw".to_vec()), Reply::Button(CANCEL), Reply::Yes, Reply::Ok];
        let mut ui = Slow(Scripted::new(r), 1500);
        let mut seen = Seen::default();
        assert_eq!(restore(&mut ui, &fake, &mut seen), Answer::Ok(()));
        assert_eq!(seen.run.as_deref(), Some("done"), "{:?}", ui.0.asked);
        // Keep going (Back, as Esc gives) goes on following it.
        let fake = sh_fake(&d, &format!("sleep 0.3\nprintf '%s\\n' '{}'", ev.replace('\n', "' '")));
        let r = [Reply::Secret(b"pw".to_vec()), Reply::Button(CANCEL), Reply::Back, Reply::Ok, Reply::Ok];
        let mut ui = Scripted::new(r);
        let mut seen = Seen::default();
        assert_eq!(restore(&mut ui, &fake, &mut seen), Answer::Ok(()));
        assert_eq!(seen.run.as_deref(), Some("done"), "{:?}", ui.asked);
        // Plain from the question leaves the page, the fake stopped.
        let fake = sh_fake(&d, "exec sleep 30");
        let _ = fs::remove_file(d.join("key-was"));
        let mut ui = Slow(Scripted::new([Reply::Secret(b"pw".to_vec()), Reply::Button(CANCEL), Reply::Plain]), 700);
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
        let line = "{\"event\":\"progress\",\"stage\":\"restore\",\"files\":1,\"bytes\":1,\"total_bytes\":2}\n";
        let n = 3 * EVENTS_PER_POLL / line.len();
        let mut body = String::from("{\"event\":\"start\",\"format\":\"bko-holt-events\",\"schema\":1,\"commit\":false}\n");
        body.push_str(&line.repeat(n));
        body.push_str("{\"event\":\"end\",\"exit\":0}\n");
        fs::write(d.join("events.txt"), &body).unwrap();
        let fake = sh_fake(&d, &format!("cat '{}'", d.join("events.txt").display()));
        let mut run = Run::start(&fake, &Secret::from_bytes(b"pw"), &d).unwrap();
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
        let run = Run::start(&fake, &Secret::from_bytes(b"pw"), &d).unwrap();
        let dir = run.dir.clone();
        assert!(dir.join(KEY_NAME).exists());
        let t = Instant::now();
        drop(run);
        assert!(t.elapsed().as_secs() < 10);
        assert!(!dir.exists());
        fs::remove_dir_all(&d).unwrap();
    }
}
