//! The app: the restore screens on the `Ui` trait (rescue-tui.md, "The
//! app on top", RT11; rescue-screens.md).  RT11 builds the first ones:
//! Machine (from `bko-holt stick --json`), Type a location, the Network
//! notice (wired only), Repository (the password, the hint after two
//! wrong tries, the recovery key through `bko-holt recovery-words
//! --json`), the keyboard offer and the Keyboard screen, Which machine?,
//! Snapshot, the problems' help screens these reach, and the bug report.
//! RT12's first part builds what comes after: Disks (disks.rs), the
//! Passphrases, the Resume offer and Review (review.rs), from bko-holt's
//! plan and status documents (plan.rs), the layout copied off the stick
//! (layout.rs).  Its second part (restore.rs) runs what Review confirmed:
//! the Restore (load detached, its events followed, Cancel and Stop),
//! Done, Failure with each stage's help and Try again, the restore log on
//! the stick, and the dry run's walk through every command.
//!
//! - The app never touches the terminal: it talks to a `Ui`, and to the
//!   system through `Sys` (sys.rs), so the screens' tests run on the
//!   scripted Ui and a fake system.
//! - `App::run` walks the steps until one needs the terminal itself (the
//!   shell, plain mode) or the program ends; `run` (below) is the runner
//!   that gives it a full screen or plain mode and comes back to the same
//!   step after the shell.
//! - Back goes one step up the history, to the last screen shown:
//!   screens skipped (Network) are skipped going back too.
//! - Secrets (the password, the recovery key's password) are only 0600
//!   files in the session's new 0700 directory in RAM, wiped when it ends
//!   (sys.rs's `Session`); the words reach bko-holt on a pipe.
//! - Everything from outside is cleaned before it is shown (`Clean`).

pub mod disks;
pub mod holt_args;
pub mod keyboard;
pub mod layout;
pub mod plan;
pub mod repo;
pub mod report;
pub mod restore;
pub mod review;
pub mod stick;
pub mod strings;
pub mod sys;
pub mod time;

#[cfg(test)]
mod tests;

use std::collections::BTreeMap;
use std::path::{Path, PathBuf};

use crate::json::{self, Value};
use crate::plain::{Plain, StdLine};
use crate::term::ffi;
use crate::term::profile::{self, Facts, Os, Profile};
use crate::text::{cap, Clean};
use crate::ui::{Answer, Button, Chrome, Input, Message, Password, Progress, Question, Radio, TextView, Tick, Ui, Words};
use crate::widgets::FullScreen;

use holt_args::HoltCmd;
use keyboard::{Keyboard, Xkb};
use repo::{Code, Place, Snap};
use stick::Stick;
use strings as s;
use sys::{Cmd, Output, Session, Sys};
use time::Zone;

/// The api document's schema this reads, and the documents it needs.
pub const API_SCHEMA: u64 = 1;
pub const NEEDS: &[(&str, u64)] = &[("stick", 1), ("recovery", 1), ("stick_copy", layout::COPY_SCHEMA), ("plan", plan::PLAN_SCHEMA), ("status", plan::STATUS_SCHEMA)];
/// How long `--version --json`, `stick --json` and `stick --copy` may
/// take.  `stick --json` reads and checks every machine's system.json
/// (each up to 64 MiB, a dump inside); `stick --copy` the chosen one's.
pub const HOLT_MS: u64 = 60_000;
/// A connection to the repository's server, to tell if the network is
/// there, waits this long.
pub const NET_MS: u64 = 3_000;
/// Try again on the Network screen waits for a route this long.
pub const NET_WAIT_S: u64 = 30;
/// The most of bko-holt's documents read: a stick's is small.
pub const DOC_MAX: usize = 4 * 1024 * 1024;

fn o(s: &str) -> Clean {
    Clean::ours(s)
}

/// `--holt`, `--rustic`, `--dry-run`.
#[derive(Clone, Debug)]
pub struct Opts {
    pub holt: Option<String>,
    pub rustic: String,
    pub dry_run: bool,
}

/// Where bko-rescue is running, for the keyboard and the texts.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Where {
    pub os: Os,
    /// On a pseudo-terminal: ssh (rescue-tui.md, "Console profiles").
    pub ssh: bool,
    pub serial: bool,
}

impl Where {
    pub fn of(f: &Facts, p: &Profile) -> Where {
        let tty = f.tty.as_deref().unwrap_or("");
        Where { os: f.os, ssh: f.os != Os::Haiku && profile::is_pty(f.os, tty), serial: p.serial }
    }
}

/// Why `App::run` came back.
#[derive(Debug, PartialEq, Eq)]
pub enum Exit {
    /// Open a shell; then run again at the same step.
    Shell,
    /// Go on in plain mode at the same step.
    Plain,
    /// A signal, a hang-up, or the terminal gone.
    Quit,
    Done(i32),
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Step {
    Start,
    Machine,
    Location,
    Network,
    Repository,
    Recovery,
    Which,
    Snapshot,
    Disks,
    /// The n-th key asked for.
    Passphrase(usize),
    Resume,
    Review,
    /// What Review confirmed, run (restore.rs): then Done or Failure.
    Restore,
}

/// What a step leads to.
#[derive(Debug, PartialEq, Eq)]
pub enum Go {
    /// The next step; this one goes on the history.
    To(Step),
    /// In this one's place: it isn't on the history.
    Swap(Step),
    /// A step from the start: the history is emptied.
    Reset(Step),
    Back,
    /// Back to STEP, the steps after it gone from the history (Review's
    /// "Go back and change it" goes to Disks, past Passphrases and
    /// Resume); where it isn't on the history, this step again.
    BackTo(Step),
    /// This step's screen again.
    Stay,
    Shell,
    Plain,
    Quit,
    Exit(i32),
}

impl Go {
    /// What an answer that isn't the dialog's own means.
    fn of<T>(a: Answer<T>) -> Go {
        match a {
            Answer::Back => Go::Back,
            Answer::Shell => Go::Shell,
            Answer::Plain => Go::Plain,
            Answer::Quit => Go::Quit,
            Answer::Ok(_) | Answer::Button(_) => Go::Stay,
        }
    }
}

// Buttons' numbers.
const B_LOCATION: usize = 1;
const B_KEYBOARD: usize = 2;
const B_RECOVERY: usize = 3;
const B_TRY: usize = 4;
const B_BY_HAND: usize = 5;
const B_REPORT: usize = 7;
const B_RAW: usize = 8;
const B_KEPT: usize = 9;
const B_FIX: usize = 20;

/// The repository a machine (or a typed address) names.
#[derive(Clone, Debug)]
pub struct Target {
    pub name: Clean,
    pub place: Place,
    pub repository_id: Option<String>,
}

pub struct App {
    pub sys: Box<dyn Sys>,
    pub(crate) session: Session,
    pub(crate) opts: Opts,
    pub(crate) wh: Where,
    pub(crate) holt: Option<PathBuf>,
    pub(crate) rustic: Option<PathBuf>,
    /// bko-holt's version, from its api document.
    pub(crate) version: Option<String>,
    pub(crate) stick: Option<Stick>,
    pub step: Step,
    pub(crate) history: Vec<Step>,
    /// The first screen: Machine, or Type a location by itself.
    pub(crate) first: Step,
    pub(crate) machine: Option<usize>,
    /// Why Type a location came up by itself.
    pub(crate) location_why: Option<&'static str>,
    pub(crate) typed: String,
    pub(crate) typed_bad: bool,
    pub(crate) location: Option<Place>,
    /// The hostname picked on "Which machine?".
    pub(crate) host: Option<String>,
    pub keyboard: Keyboard,
    /// The password file in the session's directory.
    pub(crate) password: Option<PathBuf>,
    pub(crate) tries: u32,
    pub(crate) recovery_note: Option<String>,
    pub(crate) snaps: Vec<Snap>,
    pub(crate) unread: usize,
    pub snap: Option<String>,
    /// What the last program said, for Raw output.
    pub(crate) raw: Vec<Clean>,
    /// The layout file load reads (layout.rs).
    pub(crate) layout: Option<PathBuf>,
    /// The LUKS2 headers that layout kept (its copy document's count):
    /// fetched from the backup before the first plan (layout.rs).
    pub(crate) layout_headers: u64,
    /// The snapshot the layout was found for, and whether it is that
    /// snapshot's own (its system.json in the backup) or the stick's copy,
    /// which is some backup's, not always that one's (layout.rs).
    pub(crate) layout_for: Option<String>,
    pub(crate) layout_own: bool,
    /// The stick's copy is used because the snapshot was asked for its own
    /// and holds none (`no_system_json`), not because it wasn't asked.
    pub(crate) layout_none_in_backup: bool,
    /// What Disks says of where the layout came from: the backup's own,
    /// unlike the stick's copy; or the stick's copy, and how old it is
    /// (stick-config.md's "Which copy wins", step 3).
    pub(crate) layout_note: Option<String>,
    /// Their directory in the session's, and the snapshot they are from.
    pub(crate) headers: Option<(PathBuf, Option<String>)>,
    /// The last plan bko-holt made.
    pub(crate) plan: Option<plan::Plan>,
    /// Each old disk put on a disk here: (old disk, node, what `--map`
    /// names it by).
    pub(crate) map: Vec<(String, String, String)>,
    /// The shrinks and grows agreed: (old disk, `--fit-largest` PCT,
    /// decided on the "you decide" screen).
    pub(crate) fit: Vec<(String, u64, bool)>,
    /// The passphrases typed: (group, its names, the 0600 file).
    pub(crate) keys: Vec<(String, Vec<String>, PathBuf)>,
    /// Advanced's filesystems: (mount point, type).
    pub(crate) reformat: Vec<(String, String)>,
    /// What `status --json` found on the disks mapped.
    pub(crate) status: Option<plan::Status>,
    /// Resume's answer: Some(true) Go on, Some(false) Start again.
    pub(crate) resume: Option<bool>,
    /// What the passphrases on file were last asked for: `resume` as it
    /// was then (None: before Resume).  Start again's are new (typed
    /// twice), Go on's the first time's (once), so a change of answer asks
    /// them again.
    pub(crate) pass_round: Option<bool>,
    /// Review's fields, as typed.
    pub(crate) serials: Vec<String>,
    /// The runs Review confirmed, in order (holt_args.rs, `Load::run_of`):
    /// one, or Start again's (J14) `--only close` when anything is open,
    /// `--only partition`, `--through close`.  Empty: none yet.
    pub(crate) reviewed: Vec<holt_args::Load>,
    /// The disks whose confirmation was typed on Review, as its plan
    /// named them, and whether that Review erased them (not Go on's):
    /// Try again and Install without an entry write only these.
    pub(crate) confirmed: Vec<plan::Disk>,
    pub(crate) confirmed_erase: bool,
    /// Why the Disks overview didn't go on, for its notice.
    pub(crate) disks_notice: Option<String>,
    /// The keyboard at start-up was offered.
    pub(crate) boot_offered: bool,
    /// bko-holt's RUN_DIR, from its api document: where its lock is.
    pub(crate) run_dir: Option<PathBuf>,
    /// Its api document lists `snapshot_layout`: a snapshot's own layout
    /// is asked for first (holt-json.md's open question 15).
    pub(crate) snapshot_layouts: bool,
    /// The Restore: its runs, the one running, how they ended.
    pub(crate) restore: Option<restore::Restore>,
    /// Files a bug report attaches (a failed run's events).
    pub(crate) attach: Vec<PathBuf>,
}

impl App {
    pub fn new(sys: Box<dyn Sys>, session: Session, opts: Opts, wh: Where) -> App {
        let holt = sys.which(opts.holt.as_deref().unwrap_or("bko-holt"));
        let rustic = sys.which(&opts.rustic);
        let can = wh.os == Os::Linux
            && !wh.ssh
            && !wh.serial
            && sys.which(keyboard::CKBCOMP).is_some()
            && sys.which(keyboard::LOADKEYS).is_some();
        App {
            sys,
            session,
            opts,
            wh,
            holt,
            rustic,
            version: None,
            stick: None,
            step: Step::Start,
            history: Vec::new(),
            first: Step::Machine,
            machine: None,
            location_why: None,
            typed: String::new(),
            typed_bad: false,
            location: None,
            host: None,
            keyboard: Keyboard::new(can),
            password: None,
            tries: 0,
            recovery_note: None,
            snaps: Vec::new(),
            unread: 0,
            snap: None,
            raw: Vec::new(),
            layout: None,
            layout_headers: 0,
            layout_for: None,
            layout_own: false,
            layout_none_in_backup: false,
            layout_note: None,
            headers: None,
            plan: None,
            map: Vec::new(),
            fit: Vec::new(),
            keys: Vec::new(),
            reformat: Vec::new(),
            status: None,
            resume: None,
            pass_round: None,
            serials: Vec::new(),
            reviewed: Vec::new(),
            confirmed: Vec::new(),
            confirmed_erase: false,
            disks_notice: None,
            boot_offered: false,
            run_dir: None,
            snapshot_layouts: false,
            restore: None,
            attach: Vec::new(),
        }
    }

    /// The steps from where it is, until one needs the terminal itself or
    /// the program ends.  A dry run says so on every screen (`Banner`).
    pub fn run(&mut self, ui: &mut dyn Ui) -> Exit {
        if self.opts.dry_run {
            let mut b = Banner { ui, text: o(s::DRY_RUN_BANNER) };
            return self.steps(&mut b);
        }
        self.steps(ui)
    }

    fn steps(&mut self, ui: &mut dyn Ui) -> Exit {
        loop {
            let g = match self.step {
                Step::Start => self.start(ui),
                Step::Machine => self.machine_screen(ui),
                Step::Location => self.location_screen(ui),
                Step::Network => self.network(ui),
                Step::Repository => self.repository(ui),
                Step::Recovery => self.recovery(ui),
                Step::Which => self.which(ui),
                Step::Snapshot => self.snapshot(ui),
                Step::Disks => self.disks(ui),
                Step::Passphrase(n) => self.passphrase(ui, n),
                Step::Resume => self.resume_screen(ui),
                Step::Review => self.review_screen(ui),
                Step::Restore => self.restore_screen(ui),
            };
            match g {
                Go::To(s) => {
                    self.history.push(self.step);
                    self.step = s;
                }
                Go::Swap(s) => self.step = s,
                Go::Reset(s) => {
                    self.history.clear();
                    self.step = s;
                }
                Go::Back => {
                    if let Some(s) = self.history.pop() {
                        self.step = s;
                    }
                }
                Go::BackTo(s) => {
                    if let Some(i) = self.history.iter().rposition(|h| *h == s) {
                        self.history.truncate(i);
                        self.step = s;
                    }
                }
                Go::Stay => {}
                Go::Shell => return Exit::Shell,
                Go::Plain => return Exit::Plain,
                Go::Quit => return Exit::Quit,
                Go::Exit(c) => return Exit::Done(c),
            }
        }
    }

    // ---- Running programs ----

    pub(crate) fn holt_cmd(&self, c: &HoltCmd) -> Option<Cmd> {
        let h = self.holt.as_ref()?;
        let mut cmd = Cmd::new(h, holt_args::argv(c, self.opts.dry_run));
        cmd.env = holt_args::env(self.opts.dry_run);
        cmd.stdout_max = DOC_MAX;
        cmd.timeout_ms = HOLT_MS;
        Some(cmd)
    }

    /// Run `cmd` with a gauge under `text`; its output, or what left the
    /// gauge (a signal, `p` on the too-small notice).
    pub(crate) fn run_job(&mut self, ui: &mut dyn Ui, screen: &str, title: &str, text: Clean, cmd: Cmd) -> Result<Output, Go> {
        let mut job = self.sys.start(cmd);
        let mut out: Option<Output> = None;
        let p = Progress::new(Chrome::ours(screen, title), text);
        let a = ui.progress(&p, &mut || match job.poll() {
            Some(x) => {
                out = Some(x);
                Tick::Done
            }
            None => Tick::At { text: None, pct: None, line: Clean::default(), next_ms: 100 },
        });
        match (a, out) {
            (Answer::Ok(()), Some(x)) => {
                self.raw = raw_lines(&x);
                Ok(x)
            }
            (Answer::Ok(()), None) => {
                job.kill();
                Err(Go::Stay)
            }
            (a, _) => {
                job.kill();
                Err(Go::of(a))
            }
        }
    }

    // ---- Start: bko-holt's version, then the stick ----

    fn start(&mut self, ui: &mut dyn Ui) -> Go {
        let name = self.opts.holt.clone().unwrap_or_else(|| "bko-holt".to_string());
        // Looked for again on each try: the shell may have fixed PATH or
        // mounted what was missing.
        self.holt = self.sys.which(&name);
        self.rustic = self.sys.which(&self.opts.rustic);
        let Some(cmd) = self.holt_cmd(&HoltCmd::Version) else {
            return self.holt_problem(ui, s::holt_missing(&name));
        };
        let out = match self.run_job(ui, s::SCR_START, s::CHECK_HOLT_TITLE, o(s::CHECKING_HOLT), cmd) {
            Ok(x) => x,
            Err(g) => return g,
        };
        match api(&out, self.opts.dry_run) {
            Ok((v, run_dir, snapshot_layouts)) => {
                self.version = Some(v);
                self.run_dir = run_dir;
                self.snapshot_layouts = snapshot_layouts;
            }
            Err(why) => return self.holt_problem(ui, why),
        }
        // A restore an earlier bko-rescue started, still running: follow
        // it; one that ended: its files wiped.
        if let Some(g) = self.earlier_run(ui) {
            return g;
        }
        let cmd = self.holt_cmd(&HoltCmd::Stick).expect("bko-holt was found");
        let out = match self.run_job(ui, s::SCR_START, s::START_TITLE, o(s::STARTING), cmd) {
            Ok(x) => x,
            Err(g) => return g,
        };
        let st = match document(&out, "bko-holt-stick").and_then(|v| stick::read(&v, self.version.as_deref())) {
            Ok(st) => st,
            Err(why) => return self.holt_problem(ui, s::holt_says(&why)),
        };
        let why = match st.state.as_str() {
            "ok" if st.machines.is_empty() => Some(s::LOCATION_EMPTY),
            "ok" => None,
            "missing" => Some(s::LOCATION_MISSING),
            "unreadable" => Some(s::LOCATION_UNREADABLE),
            "two_sticks" => {
                let mut m = Message::new(Chrome::ours(s::SCR_MACHINE, s::TWO_STICKS_TITLE).first(), o(s::TWO_STICKS));
                m.buttons = vec![Button::other(s::BTN_RETRY, None, B_TRY), Button::shell()];
                return match ui.message(&m) {
                    Answer::Button(_) | Answer::Ok(()) => Go::Stay,
                    a => Go::of(a),
                };
            }
            // `damaged`, and any state this doesn't know.
            _ => Some(s::LOCATION_DAMAGED),
        };
        self.stick = Some(st);
        match why {
            None => {
                self.first = Step::Machine;
                Go::Swap(Step::Machine)
            }
            Some(w) => {
                self.first = Step::Location;
                self.location_why = Some(w);
                Go::Swap(Step::Location)
            }
        }
    }

    /// bko-holt missing, too old, or saying something that can't be
    /// used: Try again, Type a location (the repository needs only
    /// rustic-bko), Shell.
    fn holt_problem(&mut self, ui: &mut dyn Ui, why: String) -> Go {
        let mut m = Message::new(Chrome::ours(s::SCR_START, s::HOLT_TITLE).first(), Clean::text(&why, cap::PROBLEM));
        m.buttons = vec![Button::other(s::BTN_TRY_AGAIN, None, B_TRY), Button::other(s::BTN_TYPE_LOCATION, None, B_LOCATION), Button::shell()];
        if !self.raw.is_empty() {
            m.buttons.push(Button::other(s::BTN_RAW, Some('o'), B_RAW));
        }
        loop {
            match ui.message(&m) {
                Answer::Button(B_LOCATION) => {
                    self.first = Step::Location;
                    self.location_why = Some(s::LOCATION_NO_HOLT);
                    return Go::Swap(Step::Location);
                }
                Answer::Button(B_RAW) => {
                    if let Some(g) = self.raw_view(ui) {
                        return g;
                    }
                }
                Answer::Button(_) | Answer::Ok(()) => return Go::Stay,
                a => return Go::of(a),
            }
        }
    }

    // ---- 1. Machine ----

    pub(crate) fn machines(&self) -> &[stick::Machine] {
        self.stick.as_ref().map(|s| s.machines.as_slice()).unwrap_or(&[])
    }

    fn machine_screen(&mut self, ui: &mut dyn Ui) -> Go {
        let ms = self.machines().to_vec();
        let pad = |c: &Clean, w: usize| c.join(&" ".repeat(w.saturating_sub(c.width()) + 3), &Clean::default());
        let names: Vec<Clean> = ms.iter().map(stick::Machine::name).collect();
        let oses: Vec<Clean> = ms.iter().map(|m| Clean::line(m.os.as_deref().unwrap_or(""), 24)).collect();
        let nw = names.iter().map(Clean::width).max().unwrap_or(0);
        let ow = oses.iter().map(Clean::width).max().unwrap_or(0);
        let mut items = Vec::new();
        let mut blocked = Vec::new();
        for (i, m) in ms.iter().enumerate() {
            let (n, os) = (pad(&names[i], nw), pad(&oses[i], ow));
            if !m.ok() {
                let why = state_words(m);
                items.push(o(&s::machine_bad(names[i].as_str(), why)));
                blocked.push((i, o(&s::machine_blocked(names[i].as_str(), why))));
            } else if m.this_computer {
                items.push(o(&s::machine_row_here(n.as_str(), os.as_str())));
            } else {
                items.push(o(&s::machine_row(n.as_str(), os.as_str(), &self.machine_when(m))));
            }
        }
        let here = ms.iter().position(|m| m.this_computer && m.ok());
        let chosen = self.machine.or(here);
        let text = match here {
            Some(h) => o(&s::machine_here(names[h].as_str(), &self.machine_when(&ms[h]))),
            None => o(s::MACHINE_NONE_HERE),
        };
        let mut r = Radio::new(Chrome::ours(s::SCR_MACHINE, s::MACHINE_TITLE).first(), text, items, chosen);
        r.none = o(s::MACHINE_PICK_FIRST);
        r.blocked = blocked;
        let mut after = o(s::MACHINE_AFTER);
        if self.keyboard.can_change {
            after = after.join("\n\n", &o(&s::keyboard_set(&self.keyboard.set.name(), "")).join(s::K_CHANGES, &Clean::default()));
        }
        r.after = after;
        r.buttons = vec![Button::ok(), Button::other(s::BTN_TYPE_LOCATION, None, B_LOCATION)];
        if self.keyboard.can_change {
            r.buttons.push(Button::other(s::BTN_KEYBOARD, Some('k'), B_KEYBOARD));
        }
        r.buttons.push(Button::shell());
        match ui.radio(&r) {
            Answer::Ok(i) if ms.get(i).is_some_and(stick::Machine::ok) => {
                if self.machine != Some(i) {
                    self.forget_repository();
                }
                self.machine = Some(i);
                self.location = None;
                self.host = None;
                self.on_to_repository()
            }
            Answer::Ok(_) => Go::Stay,
            Answer::Button(B_LOCATION) => {
                self.location_why = None;
                Go::To(Step::Location)
            }
            Answer::Button(B_KEYBOARD) => self.keyboard_screen(ui),
            // No Back: this is the first screen.
            Answer::Back | Answer::Button(_) => Go::Stay,
            a => Go::of(a),
        }
    }

    /// When the machine was last backed up, in its own zone (UTC until
    /// its backup records one: RT10l).
    pub(crate) fn machine_when(&self, m: &stick::Machine) -> String {
        let Some((t, _)) = m.written.as_deref().and_then(time::parse_rfc3339) else { return "unknown".to_string() };
        match self.zone_of(m) {
            Zone::Offset => Zone::Utc.show(t, 0),
            z => z.show(t, 0),
        }
    }

    pub(crate) fn zone_of(&self, m: &stick::Machine) -> Zone {
        let sys = &self.sys;
        Zone::of(m.time.as_ref(), &|p: &str| sys.read(Path::new(p), time::TZIF_MAX))
    }

    /// A new machine or address: the password and backups go.
    fn forget_repository(&mut self) {
        if let Some(p) = self.password.take() {
            sys::wipe_file(&p);
        }
        self.tries = 0;
        self.snaps.clear();
        self.snap = None;
        self.recovery_note = None;
        self.forget_disks();
    }

    /// Another snapshot or machine: the disks' choices and the keys go.
    pub(crate) fn forget_disks(&mut self) {
        for (_, _, p) in self.keys.drain(..) {
            sys::wipe_file(&p);
        }
        // Another snapshot's headers and layout may differ: found again.
        self.forget_headers();
        self.forget_layout();
        self.plan = None;
        self.map.clear();
        self.fit.clear();
        self.reformat.clear();
        self.status = None;
        self.resume = None;
        self.pass_round = None;
        self.serials.clear();
        self.reviewed.clear();
        self.confirmed.clear();
        self.disks_notice = None;
        // Another machine's start-up prompt, or the same one's again.
        self.boot_offered = false;
    }

    /// The machine chosen, if one is.
    pub(crate) fn chosen(&self) -> Option<&stick::Machine> {
        self.machine.and_then(|i| self.machines().get(i))
    }

    /// The repository to open: the machine's, or the typed address's.
    pub(crate) fn target(&self) -> Option<Target> {
        if let Some(p) = &self.location {
            let name = self.host.as_deref().map_or_else(|| Clean::ours("this machine"), |h| Clean::line(h, cap::HOSTNAME));
            return Some(Target { name, place: p.clone(), repository_id: None });
        }
        let m = self.chosen()?;
        let place = repo::place(m.repo_url.as_deref()?)?;
        Some(Target { name: m.name(), place, repository_id: m.repository_id.clone() })
    }

    /// To Repository, or to Network first when the server can't be
    /// reached (the images try wired DHCP before any screen).
    fn on_to_repository(&mut self) -> Go {
        let Some(t) = self.target() else { return Go::Stay };
        if let Some(h) = &t.place.host {
            if !self.sys.reachable(h, t.place.port, NET_MS) {
                return Go::To(Step::Network);
            }
        }
        Go::To(Step::Repository)
    }

    // ---- Type a location ----

    fn location_screen(&mut self, ui: &mut dyn Ui) -> Go {
        let by_itself = self.first == Step::Location;
        let mut text = Clean::default();
        if let Some(w) = self.location_why.filter(|_| by_itself) {
            text = o(w).join("\n\n", &Clean::default());
        }
        let ask = if self.typed_bad { o(s::LOCATION_BAD) } else { o(s::LOCATION_ASK) };
        text = text.join("", &ask);
        let mut chrome = Chrome::ours(s::SCR_MACHINE, s::LOCATION_TITLE);
        if by_itself {
            chrome = chrome.first();
        }
        let mut d = Input::new(chrome, text);
        d.value = self.typed.clone();
        let mut after = o(s::LOCATION_EXAMPLES);
        if self.keyboard.can_change {
            after = after.join("\n", &o(&s::keyboard_set(&self.keyboard.set.name(), "")).join(s::KEYBOARD_BEFORE_TYPING, &Clean::default()));
        }
        d.after = after;
        d.buttons = vec![Button::ok()];
        if self.keyboard.can_change {
            d.buttons.push(Button::other(s::BTN_KEYBOARD, Some('k'), B_KEYBOARD));
        }
        d.buttons.push(Button::shell());
        if !by_itself {
            d.buttons.push(Button::back());
        }
        match ui.input(&d) {
            Answer::Ok(t) => {
                self.typed = t.trim().to_string();
                match repo::place(&self.typed) {
                    Some(p) => {
                        self.typed_bad = false;
                        if self.location.as_ref() != Some(&p) || self.machine.is_some() {
                            self.forget_repository();
                        }
                        self.location = Some(p);
                        self.machine = None;
                        self.host = None;
                        self.on_to_repository()
                    }
                    None => {
                        self.typed_bad = true;
                        Go::Stay
                    }
                }
            }
            Answer::Button(B_KEYBOARD) => self.keyboard_screen(ui),
            Answer::Back if by_itself => Go::Stay,
            a => Go::of(a),
        }
    }

    // ---- 2. Network ----

    fn network(&mut self, ui: &mut dyn Ui) -> Go {
        let Some(t) = self.target() else { return Go::Back };
        let host = t.place.host.clone().unwrap_or_default();
        let mut text = s::network_text(t.name.as_str(), Clean::line(&host, cap::HOSTNAME).as_str());
        text.push_str("\n\n");
        match self.sys.ports() {
            Some(ps) if ps.is_empty() => text.push_str(s::NETWORK_NO_PORTS),
            Some(ps) => {
                text.push_str(s::NETWORK_PORTS);
                for (i, p) in ps.iter().enumerate() {
                    let state = match (p.carrier, p.has_address) {
                        (Some(false), _) => s::PORT_NO_CABLE,
                        (Some(true), Some(true)) => s::PORT_UP,
                        (Some(true), _) => s::PORT_NO_ADDRESS,
                        (None, _) => s::PORT_UNKNOWN,
                    };
                    text.push('\n');
                    text.push_str(&s::port_row(i + 1, state, Clean::line(&p.name, 16).as_str()));
                }
            }
            None => text.push_str(s::NETWORK_CANT_LIST),
        }
        text.push_str("\n\n");
        text.push_str(s::NO_WIFI);
        let mut m = Message::new(Chrome::ours(s::SCR_NETWORK, s::NETWORK_TITLE), Clean::text(&text, cap::PROBLEM));
        m.buttons = vec![Button::other(s::BTN_TRY_AGAIN, None, B_TRY), Button::other(s::BTN_SET_BY_HAND, None, B_BY_HAND), Button::shell(), Button::back()];
        match ui.message(&m) {
            Answer::Button(B_TRY) | Answer::Ok(()) => {
                let (h, port) = (host, t.place.port);
                let mut up = false;
                let mut waited = 0u64;
                let p = Progress::new(Chrome::ours(s::SCR_NETWORK, s::NETWORK_TITLE), o(s::NETWORK_WAITING));
                let sys = &mut self.sys;
                let a = ui.progress(&p, &mut || {
                    if sys.reachable(&h, port, 1_500) {
                        up = true;
                        return Tick::Done;
                    }
                    waited += 2;
                    if waited > NET_WAIT_S {
                        return Tick::Done;
                    }
                    let pct = (waited * 100 / NET_WAIT_S).min(100) as u8;
                    Tick::At { text: None, pct: Some(pct), line: o(&format!("{waited} of {NET_WAIT_S} s")), next_ms: 500 }
                });
                match a {
                    // Network was shown: Back from Repository comes here.
                    Answer::Ok(()) if up => Go::To(Step::Repository),
                    Answer::Ok(()) => Go::Stay,
                    a => Go::of(a),
                }
            }
            Answer::Button(B_BY_HAND) => {
                let mut m = Message::new(Chrome::ours(s::SCR_NETWORK, s::SET_BY_HAND_TITLE), o(s::SET_BY_HAND));
                m.buttons = vec![Button::ok(), Button::shell()];
                match ui.message(&m) {
                    Answer::Ok(()) => Go::Stay,
                    a => Go::of(a),
                }
            }
            a => Go::of(a),
        }
    }

    // ---- 3. Repository ----

    /// The keyboard line under a password, and why it is as it is.
    pub(crate) fn keyboard_line(&self) -> String {
        let name = self.chosen().map(|m| m.name().as_str().to_string()).unwrap_or_else(|| "this machine".to_string());
        let backup = self.chosen().and_then(|m| keyboard::canonical(m.keyboard.as_ref()));
        if self.wh.ssh {
            return s::KEYBOARD_SSH.to_string();
        }
        if self.wh.serial {
            return s::KEYBOARD_SERIAL.to_string();
        }
        if !self.keyboard.can_change {
            return match &backup {
                Some(b) => s::keyboard_fixed_backup(&name, &b.name()),
                None => s::KEYBOARD_FIXED.to_string(),
            };
        }
        let from = match &self.keyboard.from {
            keyboard::From::Default => s::FROM_DEFAULT.to_string(),
            keyboard::From::Backup(m) => s::from_backup(m),
            keyboard::From::Chosen => s::FROM_CHOSEN.to_string(),
        };
        let mut line = s::keyboard_set(&self.keyboard.set.name(), &from);
        if self.chosen().is_some() {
            match &backup {
                None => line.push_str(&s::backup_silent(&name)),
                Some(b) if b.row().is_none() && b.layout != "us" => line.push_str(&s::backup_unknown(&name, &b.name())),
                _ => {}
            }
        }
        line
    }

    /// The keyboard offer, once per machine, before the first password:
    /// when the machine's backup names a layout this stick has and it
    /// isn't the one set now.
    fn offer(&mut self, ui: &mut dyn Ui) -> Option<Go> {
        let m = self.chosen()?.clone();
        // Once per machine: by its install ID, or its row when it has none.
        let key = if m.install_id.is_empty() { format!("#{}", self.machine.unwrap_or(0)) } else { m.install_id.clone() };
        if !self.keyboard.can_change || self.keyboard.offered.contains(&key) {
            return None;
        }
        let named = keyboard::canonical(m.keyboard.as_ref())?;
        // A variant this stick hasn't: its base layout, saying so.
        let (want, missing) = match (named.row(), &named.variant) {
            (Some(_), _) => (named.clone(), None),
            (None, Some(v)) => {
                let base = keyboard::Xkb { layout: named.layout.clone(), variant: None };
                base.row()?;
                (base, Some(v.clone()))
            }
            (None, None) => return None,
        };
        if want == self.keyboard.set {
            return None;
        }
        self.keyboard.offered.push(key);
        let now = self.keyboard.set.name();
        // The layout the backup names, as it names it.
        let xkb = match &named.variant {
            Some(v) => format!("{}, {v}", named.layout),
            None => named.layout.clone(),
        };
        let short = want.short();
        let mut text = s::offer(m.name().as_str(), &short, &xkb, &now);
        let others: Vec<String> = keyboard::desktop(m.keyboard.as_ref()).iter().filter(|x| **x != want && **x != named).map(Xkb::name).collect();
        if !others.is_empty() {
            text.push_str("  ");
            text.push_str(s::OFFER_DESKTOP);
            text.push_str(&others.join(", "));
            text.push('.');
        }
        if let Some(v) = &missing {
            text.push_str("\n\n");
            text.push_str(&s::offer_base(&short, Clean::line(v, 32).as_str()));
        }
        text.push_str("\n\n");
        text.push_str(&s::offer_why(&short, &now));
        let mut q = Question::new(Chrome::ours(s::SCR_REPOSITORY, s::OFFER_TITLE), Clean::text(&text, cap::PROBLEM));
        q.yes = o(&s::btn_switch(&short));
        q.no = o(&s::btn_keep(&now));
        q.esc_no = true;
        q.buttons = vec![Button::other(s::BTN_ANOTHER_LAYOUT, None, B_KEYBOARD)];
        q.chrome.keys = Some(o(&s::offer_keys(&now)));
        match ui.yes_no(&q) {
            Answer::Ok(true) => {
                if let Err(e) = self.set_layout(&want) {
                    return Some(self.layout_failed(ui, &want, e));
                }
                self.keyboard.set = want;
                self.keyboard.from = keyboard::From::Backup(m.name().as_str().to_string());
                None
            }
            Answer::Ok(false) => None,
            Answer::Button(_) => match self.keyboard_screen(ui) {
                Go::Stay => None,
                g => Some(g),
            },
            a => Some(Go::of(a)),
        }
    }

    fn repository(&mut self, ui: &mut dyn Ui) -> Go {
        let Some(t) = self.target() else { return Go::Back };
        if self.password.is_none() && self.tries == 0 {
            if let Some(g) = self.offer(ui) {
                return g;
            }
        }
        let url = Clean::line(&t.place.url, cap::LINE);
        let mut text = match &t.place.host {
            Some(h) => s::kept_at(t.name.as_str(), Clean::line(h, cap::HOSTNAME).as_str(), url.as_str()),
            None => s::kept_in(t.name.as_str(), url.as_str()),
        };
        text.push_str("\n\n");
        let kb = self.keyboard_line();
        if self.tries > 0 {
            let state = if self.wh.ssh {
                s::KB_SSH_SHORT.to_string()
            } else if self.wh.serial {
                s::KB_SERIAL_SHORT.to_string()
            } else {
                s::set_to(&self.keyboard.set.name())
            };
            text = if self.tries == 1 { s::wrong_once(&state) } else { s::wrong_again(&state) };
            if self.tries >= 3 {
                text.push_str("  ");
                text.push_str(s::YZ_SWAPPED);
            }
            text.push_str("\n\n");
            let hint = self.chosen().and_then(|m| m.hint.clone());
            if let (true, Some(h)) = (self.tries >= 2, hint) {
                text.push_str(s::HINT_HEAD);
                text.push('\n');
                text.push_str(&s::hint_line(Clean::line(&h, cap::LINE).as_str()));
                text.push_str("\n\n");
            }
        }
        // Plain mode: an empty line shows the other choices (the recovery
        // key, Back), so the prompt says so, as its transcript does.
        let full = ui.full_screen();
        text.push_str(if full { s::TYPE_PASSWORD } else { s::TYPE_PASSWORD_PLAIN });
        let mut d = Password::new(Chrome::ours(s::SCR_REPOSITORY, s::PASSWORD_TITLE), Clean::text(&text, cap::PROBLEM));
        d.after = if self.tries > 0 {
            o(&s::tries(self.tries))
        } else {
            let mut a = s::PASSWORD_NOT_THIS.to_string();
            if self.password.is_some() {
                a.push_str("  ");
                a.push_str(if full { s::PASSWORD_KEPT } else { s::PASSWORD_KEPT_PLAIN });
            }
            a.push_str("\n\n");
            a.push_str(&kb);
            Clean::text(&a, cap::PROBLEM)
        };
        d.buttons = vec![Button::ok()];
        if !full && self.password.is_some() && self.tries == 0 {
            // Plain mode's empty line is the menu: the kept password is a
            // choice there.  Full screen: Enter in the empty field.
            d.buttons.push(Button::other(s::BTN_KEPT, Some('p'), B_KEPT));
        }
        if self.keyboard.can_change {
            // No letter: `k` is typed in the field.
            d.buttons.push(Button::other(s::BTN_KEYBOARD, None, B_KEYBOARD));
        }
        if self.holt.is_some() {
            d.buttons.push(Button::other(s::BTN_RECOVERY, None, B_RECOVERY));
        }
        d.buttons.push(Button::back());
        match ui.password(&d) {
            Answer::Ok(secret) => {
                if secret.is_empty() {
                    return if self.password.is_some() { self.open(ui, &t) } else { Go::Stay };
                }
                match self.session.secret_file("password", &secret) {
                    Ok(p) => self.password = Some(p),
                    Err(e) => return self.failed_here(ui, s::SESSION_FAILED, &e.to_string()),
                }
                drop(secret);
                self.open(ui, &t)
            }
            Answer::Button(B_KEYBOARD) => self.keyboard_screen(ui),
            Answer::Button(B_RECOVERY) => Go::To(Step::Recovery),
            Answer::Button(B_KEPT) if self.password.is_some() => self.open(ui, &t),
            Answer::Back => {
                self.tries = 0;
                Go::Back
            }
            a => Go::of(a),
        }
    }

    /// Open the repository with the password file: its ID, then its
    /// backups.  A wrong password counts a try and asks again.
    fn open(&mut self, ui: &mut dyn Ui, t: &Target) -> Go {
        if self.rustic.is_none() {
            self.rustic = self.sys.which(&self.opts.rustic);
        }
        let Some(rustic) = self.rustic.clone() else {
            let why = s::rustic_missing(&self.opts.rustic);
            return self.problem(ui, Problem::repo(Code::Error, t, Some(why)));
        };
        let Some(pw) = self.password.clone() else { return Go::Stay };
        let place = t.place.host.as_deref().unwrap_or(&t.place.url).to_string();
        let shown = Clean::line(&place, cap::HOSTNAME);
        let c = repo::cmd(&rustic, &t.place.url, &pw, &["cat", "config"], repo::CONFIG_MAX);
        let out = match self.run_job(ui, s::SCR_REPOSITORY, s::OPENING_TITLE, o(&s::opening(t.name.as_str(), shown.as_str())), c) {
            Ok(x) => x,
            Err(g) => return g,
        };
        if let Some(code) = repo::classify(&out) {
            return self.repo_failed(ui, code, t);
        }
        // Never a silent switch: an ID the stick has that can't be
        // checked stops here as a mismatch does.
        let id = repo::config_id(&out.stdout);
        match repo::id_check(t.repository_id.as_deref(), id.as_deref()) {
            repo::IdCheck::NoneKept | repo::IdCheck::Matches => {}
            repo::IdCheck::Differs => return self.problem(ui, Problem::repo(Code::IdMismatch, t, None)),
            repo::IdCheck::CantCheck => return self.problem(ui, Problem::repo(Code::IdMismatch, t, Some(s::id_unchecked(t.name.as_str())))),
        }
        let c = repo::cmd(&rustic, &t.place.url, &pw, &["snapshots", "--json"], repo::SNAPSHOTS_MAX);
        let out = match self.run_job(ui, s::SCR_REPOSITORY, s::OPENING_TITLE, o(&s::listing(t.name.as_str())), c) {
            Ok(x) => x,
            Err(g) => return g,
        };
        if let Some(code) = repo::classify(&out) {
            return self.repo_failed(ui, code, t);
        }
        let (snaps, unread) = match repo::snapshots(&out.stdout) {
            Ok(x) => x,
            Err(_) => return self.problem(ui, Problem::repo(Code::Error, t, Some(s::UNREADABLE_LIST.to_string()))),
        };
        self.tries = 0;
        self.snaps = snaps;
        self.unread = unread;
        self.snap = None;
        // Back from Snapshot comes to the password screen, not the
        // recovery key's.
        if self.step == Step::Recovery {
            self.step = Step::Repository;
            if self.history.last() == Some(&Step::Repository) {
                self.history.pop();
            }
        }
        if self.location.is_some() && self.host.is_none() {
            let hosts = hostnames(&self.snaps);
            if hosts.len() == 1 {
                self.host = hosts.into_iter().next().map(|(h, _)| h);
            } else if hosts.len() > 1 {
                return Go::To(Step::Which);
            }
        }
        Go::To(Step::Snapshot)
    }

    fn repo_failed(&mut self, ui: &mut dyn Ui, code: Code, t: &Target) -> Go {
        if code == Code::WrongPassword {
            if self.step == Step::Recovery {
                self.recovery_note = Some(s::RECOVERY_NOT_IT.to_string());
                if let Some(p) = self.password.take() {
                    sys::wipe_file(&p);
                }
                return Go::Stay;
            }
            self.tries += 1;
            if let Some(p) = self.password.take() {
                sys::wipe_file(&p);
            }
            return Go::Stay;
        }
        self.problem(ui, Problem::repo(code, t, None))
    }

    /// Something of ours failed (the session's directory).
    fn failed_here(&mut self, ui: &mut dyn Ui, what: &str, why: &str) -> Go {
        let m = Message::new(Chrome::ours(s::SCR_REPOSITORY, &s::problem_title(what)), Clean::text(why, cap::PROBLEM));
        match ui.message(&m) {
            Answer::Ok(()) => Go::Stay,
            a => Go::of(a),
        }
    }

    // ---- The recovery key ----

    fn recovery(&mut self, ui: &mut dyn Ui) -> Go {
        let Some(t) = self.target() else { return Go::Back };
        let mut text = o(s::RECOVERY_TEXT);
        if let Some(n) = self.recovery_note.take() {
            text = Clean::text(&n, cap::PROBLEM).join("\n\n", &text);
        }
        let mut w = Words::new(Chrome::ours(s::SCR_REPOSITORY, s::RECOVERY_TITLE), text, o(s::RECOVERY_PLAIN));
        w.after = o(s::RECOVERY_AFTER);
        let words = match ui.words(&w) {
            Answer::Ok(x) => x,
            a => return Go::of(a),
        };
        let out_path = self.session.path("recovery-password");
        sys::wipe_file(&out_path);
        let Some(mut cmd) = self.holt_cmd(&HoltCmd::RecoveryWords { out: &out_path }) else { return Go::Back };
        cmd.stdin = Some(words);
        let out = match self.run_job(ui, s::SCR_REPOSITORY, s::RECOVERY_TITLE, o(s::RECOVERY_CHECKING), cmd) {
            Ok(x) => x,
            Err(g) => return g,
        };
        let version = self.version.clone();
        let doc = match document(&out, "bko-holt-recovery").and_then(|v| same_version(v, 1, version.as_deref())) {
            Ok(v) => v,
            Err(why) => {
                sys::wipe_file(&out_path);
                self.recovery_note = Some(s::holt_says(&why));
                return Go::Stay;
            }
        };
        let ok = doc.get_bool("ok") == Some(true) && doc.get_bool("written") == Some(true) && out.code == Some(0);
        if !ok {
            sys::wipe_file(&out_path);
            self.recovery_note = Some(recovery_problem(&doc));
            return Go::Stay;
        }
        if let Some(p) = self.password.take() {
            sys::wipe_file(&p);
        }
        self.password = Some(out_path);
        self.open(ui, &t)
    }

    // ---- Which machine? ----

    fn which(&mut self, ui: &mut dyn Ui) -> Go {
        let hosts = hostnames(&self.snaps);
        let names: Vec<Clean> = hosts.iter().map(|(h, _)| Clean::line(h, cap::HOSTNAME)).collect();
        let w = names.iter().map(Clean::width).max().unwrap_or(0) + 3;
        let items: Vec<Clean> = hosts
            .iter()
            .zip(&names)
            .map(|((h, n), c)| {
                let newest = self.snaps.iter().find(|x| &x.hostname == h).map(|x| Zone::Offset.show(x.time, x.offset)).unwrap_or_default();
                let padded = c.join(&" ".repeat(w - c.width()), &Clean::default());
                o(&s::which_row(padded.as_str(), *n, &newest))
            })
            .collect();
        let mut r = Radio::new(Chrome::ours(s::SCR_MACHINE, s::WHICH_TITLE), o(&s::which_text(hosts.len())), items, None);
        r.none = o(s::WHICH_PICK_FIRST);
        match ui.radio(&r) {
            Answer::Ok(i) => match hosts.get(i) {
                Some((h, _)) => {
                    self.host = Some(h.clone());
                    Go::To(Step::Snapshot)
                }
                None => Go::Stay,
            },
            a => Go::of(a),
        }
    }

    // ---- 4. Snapshot ----

    fn snapshot(&mut self, ui: &mut dyn Ui) -> Go {
        let (name, host, zone) = match self.chosen() {
            Some(m) => (m.name(), m.hostname.clone(), self.zone_of(m)),
            None => (self.host.as_deref().map_or_else(|| o("this machine"), |h| Clean::line(h, cap::HOSTNAME)), self.host.clone(), Zone::Offset),
        };
        let list: Vec<Snap> = self.snaps.iter().filter(|x| host.as_ref().is_none_or(|h| &x.hostname == h)).cloned().collect();
        if list.is_empty() {
            let mut m = Message::new(Chrome::ours(s::SCR_SNAPSHOT, s::NO_SNAPSHOTS_TITLE), o(&s::no_snapshots(name.as_str())));
            m.buttons = vec![Button::back(), Button::shell()];
            return match ui.message(&m) {
                Answer::Ok(()) => Go::Back,
                a => Go::of(a),
            };
        }
        let times: Vec<String> = list.iter().map(|x| zone.show(x.time, x.offset)).collect();
        let tw = times.iter().map(String::len).max().unwrap_or(0).max(21) + 4;
        let items: Vec<Clean> = list
            .iter()
            .zip(&times)
            .enumerate()
            .map(|(i, (x, t))| {
                let size = x.bytes.map(crate::demo::size).unwrap_or_default();
                let tag = if i == 0 { s::NEWEST.to_string() } else { x.tags.join(",") };
                let tag = Clean::line(&tag, 12);
                o(&format!("{t:<tw$}{size:>7}   {:<13}", tag.as_str())).join("", &x.short_id())
            })
            .collect();
        let mut r = Radio::new(Chrome::ours(s::SCR_SNAPSHOT, s::SNAPSHOT_TITLE), o(&s::snapshots_of(name.as_str())), items, Some(0));
        let mut after = match &zone {
            Zone::Rule { name: Some(z), rule, .. } => {
                let (std, dst) = rule.names();
                s::zone_named(name.as_str(), Clean::line(z, 64).as_str(), &std, dst.as_deref())
            }
            Zone::Rule { rule, .. } => {
                let (std, dst) = rule.names();
                s::zone_rule(name.as_str(), &std, dst.as_deref())
            }
            _ => s::zone_offsets(name.as_str()),
        };
        after.push_str(s::NEWEST_USUAL);
        if self.unread > 0 {
            after.push('\n');
            after.push_str(&s::some_unread(self.unread));
        }
        r.after = Clean::text(&after, cap::PROBLEM);
        match ui.radio(&r) {
            Answer::Ok(i) => match list.get(i) {
                Some(x) => {
                    if self.snap.as_ref() != Some(&x.id) {
                        self.forget_disks();
                    }
                    self.snap = Some(x.id.clone());
                    Go::To(Step::Disks)
                }
                None => Go::Stay,
            },
            a => Go::of(a),
        }
    }

    // ---- The Keyboard screen ----

    /// Load a layout with the system's own tools.
    pub(crate) fn set_layout(&mut self, x: &Xkb) -> Result<(), String> {
        let (Some(ck), Some(lk)) = (self.sys.which(keyboard::CKBCOMP), self.sys.which(keyboard::LOADKEYS)) else {
            return Err("ckbcomp or loadkeys isn't here".to_string());
        };
        let mut c = Cmd::new(&ck, keyboard::ckbcomp_args(x).into_iter().map(Into::into).collect());
        c.stdout_max = 2 * 1024 * 1024;
        c.timeout_ms = 20_000;
        let out = wait(self.sys.start(c), &mut *self.sys);
        if out.code != Some(0) || out.stdout.is_empty() {
            return Err(first_line(&out));
        }
        let map = self.session.path("keymap");
        sys::wipe_file(&map);
        if let Err(e) = write_new(&map, &out.stdout) {
            return Err(e.to_string());
        }
        let mut c = Cmd::new(&lk, vec![map.as_os_str().to_owned()]);
        c.timeout_ms = 20_000;
        let out = wait(self.sys.start(c), &mut *self.sys);
        sys::wipe_file(&map);
        if out.code != Some(0) {
            return Err(first_line(&out));
        }
        Ok(())
    }

    fn layout_failed(&mut self, ui: &mut dyn Ui, x: &Xkb, why: String) -> Go {
        let text = format!("{}\n\n{}", s::layout_failed(&x.name()), Clean::line(&why, cap::LOG_LINE).as_str());
        let m = Message::new(Chrome::ours(s::SCR_KEYBOARD, s::LAYOUT_FAILED_TITLE), Clean::text(&text, cap::PROBLEM));
        match ui.message(&m) {
            Answer::Ok(()) => Go::Stay,
            a => Go::of(a),
        }
    }

    /// The layouts, as the Keyboard screen lists them: the one set now,
    /// the machine's own, then the rest.
    pub(crate) fn layout_rows(&self) -> Vec<(Xkb, Option<String>)> {
        let mut v: Vec<(Xkb, Option<String>)> = vec![(self.keyboard.set.clone(), None)];
        if let Some(m) = self.chosen() {
            let whose = s::whose(m.name().as_str());
            let mut own: Vec<Xkb> = keyboard::canonical(m.keyboard.as_ref()).into_iter().collect();
            own.extend(keyboard::desktop(m.keyboard.as_ref()));
            for x in own.into_iter().filter(|x| x.row().is_some()) {
                match v.iter_mut().find(|(y, _)| *y == x) {
                    Some(row) => row.1 = Some(whose.clone()),
                    None => v.push((x, Some(whose.clone()))),
                }
            }
        }
        for l in keyboard::LAYOUTS {
            let x = Xkb::of(l);
            if !v.iter().any(|(y, _)| *y == x) {
                v.push((x, None));
            }
        }
        v
    }

    fn keyboard_screen(&mut self, ui: &mut dyn Ui) -> Go {
        let rows = self.layout_rows();
        let items: Vec<Clean> = rows
            .iter()
            .map(|(x, whose)| {
                let tag = match &x.variant {
                    Some(v) => format!("{} {v}", x.layout),
                    None => x.layout.clone(),
                };
                let name = x.row().map(|r| r.name).unwrap_or("");
                o(format!("{tag:<16}{name:<32}{}", whose.as_deref().unwrap_or("")).trim_end())
            })
            .collect();
        let mut r = Radio::new(Chrome::ours(s::SCR_KEYBOARD, s::LAYOUT_TITLE), o(s::LAYOUT_TEXT), items, Some(0));
        r.try_it = Some(o(s::TRY_IT));
        let before = self.keyboard.set.clone();
        let mut tried: Option<Xkb> = None;
        let a = {
            let mut moved = |i: usize| {
                if let Some((x, _)) = rows.get(i) {
                    if self.set_layout(x).is_ok() {
                        tried = Some(x.clone());
                    }
                }
            };
            ui.radio_live(&r, &mut moved)
        };
        match a {
            Answer::Ok(i) => {
                let Some((x, _)) = rows.get(i) else { return Go::Stay };
                if tried.as_ref() != Some(x) {
                    if let Err(e) = self.set_layout(x) {
                        let x = x.clone();
                        return self.layout_failed(ui, &x, e);
                    }
                }
                if *x != self.keyboard.set {
                    self.keyboard.set = x.clone();
                    self.keyboard.from = keyboard::From::Chosen;
                }
                Go::Stay
            }
            a => {
                // Back and Esc: the layout there was.
                if tried.is_some() && tried.as_ref() != Some(&before) {
                    let _ = self.set_layout(&before);
                }
                match a {
                    Answer::Back => Go::Stay,
                    a => Go::of(a),
                }
            }
        }
    }

    // ---- Raw output ----

    /// What the last program said; None to go on, else where to.
    pub(crate) fn raw_view(&mut self, ui: &mut dyn Ui) -> Option<Go> {
        let mut v = TextView::new(Chrome::ours(s::SCR_REPOSITORY, s::RAW_TITLE), self.raw.clone());
        v.buttons.push(Button::back());
        match ui.view(&v) {
            Answer::Ok(()) | Answer::Back => None,
            a => Some(Go::of(a)),
        }
    }
}

/// A `Ui` that puts a banner on every dialog it passes on: the dry run's
/// (rescue-tui.md, "Dry run": "It says so on every screen"), so no
/// screen can be built without it.
struct Banner<'a> {
    ui: &'a mut dyn Ui,
    text: Clean,
}

impl Banner<'_> {
    fn on<D: Clone>(&self, d: &D, chrome: impl FnOnce(&mut D) -> &mut Chrome) -> D {
        let mut d = d.clone();
        chrome(&mut d).banner = self.text.clone();
        d
    }
}

impl Ui for Banner<'_> {
    fn menu(&mut self, d: &crate::ui::Menu) -> Answer<usize> {
        let d = self.on(d, |d| &mut d.chrome);
        self.ui.menu(&d)
    }
    fn radio(&mut self, d: &Radio) -> Answer<usize> {
        let d = self.on(d, |d| &mut d.chrome);
        self.ui.radio(&d)
    }
    fn input(&mut self, d: &Input) -> Answer<String> {
        let d = self.on(d, |d| &mut d.chrome);
        self.ui.input(&d)
    }
    fn password(&mut self, d: &Password) -> Answer<crate::secret::Secret> {
        let d = self.on(d, |d| &mut d.chrome);
        self.ui.password(&d)
    }
    fn yes_no(&mut self, d: &Question) -> Answer<bool> {
        let d = self.on(d, |d| &mut d.chrome);
        self.ui.yes_no(&d)
    }
    fn message(&mut self, d: &Message) -> Answer<()> {
        let d = self.on(d, |d| &mut d.chrome);
        self.ui.message(&d)
    }
    fn view(&mut self, d: &TextView) -> Answer<()> {
        let d = self.on(d, |d| &mut d.chrome);
        self.ui.view(&d)
    }
    fn progress(&mut self, d: &Progress, tick: &mut dyn FnMut() -> Tick) -> Answer<()> {
        let d = self.on(d, |d| &mut d.chrome);
        self.ui.progress(&d, tick)
    }
    fn words(&mut self, d: &Words) -> Answer<crate::secret::Secret> {
        let d = self.on(d, |d| &mut d.chrome);
        self.ui.words(&d)
    }
    fn radio_live(&mut self, d: &Radio, moved: &mut dyn FnMut(usize)) -> Answer<usize> {
        let d = self.on(d, |d| &mut d.chrome);
        self.ui.radio_live(&d, moved)
    }
    fn full_screen(&self) -> bool {
        self.ui.full_screen()
    }
    fn passphrase(&mut self, d: &crate::ui::Passphrase) -> Answer<Vec<crate::secret::Secret>> {
        let d = self.on(d, |d| &mut d.chrome);
        self.ui.passphrase(&d)
    }
    fn review(&mut self, d: &crate::ui::Review, typed: &mut Vec<String>) -> Answer<()> {
        let d = self.on(d, |d| &mut d.chrome);
        self.ui.review(&d, typed)
    }
    fn cycle(&mut self, d: &crate::ui::Cycle) -> Answer<Vec<usize>> {
        let d = self.on(d, |d| &mut d.chrome);
        self.ui.cycle(&d)
    }
}

/// A problem's help screen (rescue-screens.md, "Problems and their
/// help"): what happened, why, what to do, the backup-is-safe line.
pub struct Problem {
    pub code: &'static str,
    pub screen: &'static str,
    pub title: String,
    pub what: String,
    pub why: String,
    pub todo: String,
    /// The fixes known and safe, as buttons: never the focus at first.
    pub fixes: Vec<(String, Go)>,
    /// Try again is offered (it reruns the step).
    pub again: bool,
    /// Where Back goes: None, the screen that was showing, again.
    pub back: Option<Go>,
}

impl Problem {
    pub(crate) fn repo(code: Code, t: &Target, what: Option<String>) -> Problem {
        let host = t.place.host.as_deref().map(|h| Clean::line(h, cap::HOSTNAME).as_str().to_string()).unwrap_or_default();
        let url = Clean::line(&t.place.url, cap::LINE).as_str().to_string();
        let name = t.name.as_str();
        let (title, w, why, todo, fixes, again) = match code {
            Code::Unreachable => (
                s::UNREACHABLE_TITLE,
                s::unreachable_what(name, &host),
                s::UNREACHABLE_WHY,
                s::UNREACHABLE_TODO,
                vec![(s::BTN_NETWORK.to_string(), Go::Swap(Step::Network))],
                true,
            ),
            Code::NotFound => (s::NOT_FOUND_TITLE, s::not_found_what(&url), s::NOT_FOUND_WHY, s::NOT_FOUND_TODO, vec![(s::BTN_TYPE_LOCATION.to_string(), Go::To(Step::Location))], false),
            Code::LoginFailed => (s::LOGIN_TITLE, s::login_what(&host), s::LOGIN_WHY, s::LOGIN_TODO, vec![(s::BTN_TYPE_LOCATION.to_string(), Go::To(Step::Location))], false),
            Code::IdMismatch => (
                s::MISMATCH_TITLE,
                s::mismatch_what(name),
                s::MISMATCH_WHY,
                s::MISMATCH_TODO,
                vec![(s::BTN_MACHINE.to_string(), Go::Reset(Step::Machine)), (s::BTN_TYPE_LOCATION.to_string(), Go::To(Step::Location))],
                false,
            ),
            Code::Error | Code::WrongPassword => (s::ERROR_TITLE, s::error_what(name), s::ERROR_WHY, s::ERROR_TODO, Vec::new(), true),
        };
        Problem {
            code: code.name(),
            screen: s::SCR_REPOSITORY,
            title: title.to_string(),
            what: what.unwrap_or(w),
            why: why.to_string(),
            todo: todo.to_string(),
            fixes,
            again,
            back: None,
        }
    }

    pub fn text(&self) -> String {
        format!("{}{}\n\n{}{}\n\n{}{}\n\n{}", s::WHAT_HAPPENED, self.what, s::WHY, self.why, s::TODO, self.todo, s::BACKUP_SAFE)
    }
}

impl App {
    /// A problem's help screen, until the user picks a way on.
    pub(crate) fn problem(&mut self, ui: &mut dyn Ui, mut p: Problem) -> Go {
        // Another machine needs a stick that knows some.
        if self.machines().is_empty() {
            p.fixes.retain(|(_, g)| *g != Go::Reset(Step::Machine));
        }
        let mut m = Message::new(Chrome::ours(p.screen, &s::problem_title(&p.title)), Clean::text(&p.text(), cap::PROBLEM));
        m.buttons = Vec::new();
        for (i, (label, _)) in p.fixes.iter().enumerate() {
            m.buttons.push(Button::other(label, None, B_FIX + i));
        }
        let first = m.buttons.len();
        if p.again {
            m.buttons.push(Button::other(s::BTN_TRY_AGAIN, None, B_TRY));
        }
        m.buttons.push(Button::shell());
        if self.holt.is_some() {
            m.buttons.push(Button::other(s::BTN_REPORT, Some('r'), B_REPORT));
        }
        if !self.raw.is_empty() {
            m.buttons.push(Button::other(s::BTN_RAW, Some('o'), B_RAW));
        }
        m.buttons.push(Button::back());
        // A fix is never the default: the focus starts after them.
        m.focus = first;
        loop {
            match ui.message(&m) {
                Answer::Button(B_TRY) => return Go::Stay,
                Answer::Button(B_REPORT) => {
                    if let Some(g) = self.report(ui) {
                        return g;
                    }
                }
                Answer::Button(B_RAW) => {
                    if let Some(g) = self.raw_view(ui) {
                        return g;
                    }
                }
                Answer::Button(n) if n >= B_FIX && n - B_FIX < p.fixes.len() => {
                    let g = p.fixes.swap_remove(n - B_FIX).1;
                    if g == Go::Reset(Step::Machine) || g == Go::To(Step::Location) {
                        self.forget_repository();
                    }
                    return g;
                }
                // Back: the screen that was showing, again, unless the
                // problem says where.
                Answer::Back => return p.back.take().unwrap_or(Go::Stay),
                Answer::Ok(()) | Answer::Button(_) => return Go::Stay,
                a => return Go::of(a),
            }
        }
    }
}

/// A machine's state, in words.
fn state_words(m: &stick::Machine) -> &'static str {
    match m.state.as_str() {
        "ok" if m.repo_url.is_none() => s::STATE_NO_REPO,
        "ok" if m.hostname.is_some() => s::STATE_BAD_REPO,
        "ok" => s::STATE_OTHER,
        "checksum_bad" => s::STATE_CHECKSUM_BAD,
        "checksum_missing" => s::STATE_CHECKSUM_MISSING,
        "schema_unknown" => s::STATE_SCHEMA_UNKNOWN,
        "unreadable" => s::STATE_UNREADABLE,
        _ => s::STATE_OTHER,
    }
}

/// The hostnames in a snapshot list, newest first, with their counts.
pub fn hostnames(snaps: &[Snap]) -> Vec<(String, usize)> {
    let mut count: BTreeMap<&str, usize> = BTreeMap::new();
    let mut order: Vec<&str> = Vec::new();
    for x in snaps {
        if !count.contains_key(x.hostname.as_str()) {
            order.push(&x.hostname);
        }
        *count.entry(&x.hostname).or_default() += 1;
    }
    order.into_iter().map(|h| (h.to_string(), count[h])).collect()
}

/// A run's standard error, a line each, cleaned, for Raw output.
pub(crate) fn raw_lines(x: &Output) -> Vec<Clean> {
    let mut v: Vec<Clean> = x.stderr_text().lines().rev().take(3000).map(|l| Clean::line(l, cap::LOG_LINE)).collect();
    v.reverse();
    if let Some(n) = &x.not_started {
        v.push(Clean::line(n, cap::LOG_LINE));
    }
    if x.timed_out {
        v.push(o("(it took too long, and was stopped)"));
    }
    v
}

fn first_line(x: &Output) -> String {
    x.not_started.clone().unwrap_or_else(|| x.stderr_text().lines().find(|l| !l.trim().is_empty()).unwrap_or("it failed").to_string())
}

/// Wait for a short job, polling.
pub(crate) fn wait(mut j: Box<dyn sys::Job>, sys: &mut dyn Sys) -> Output {
    loop {
        if let Some(x) = j.poll() {
            return x;
        }
        sys.sleep_ms(20);
    }
}

/// A new 0600 file holding `b`.
pub(crate) fn write_new(path: &Path, b: &[u8]) -> std::io::Result<()> {
    use std::io::Write as _;
    use std::os::unix::fs::OpenOptionsExt as _;
    let mut f = std::fs::OpenOptions::new().write(true).create_new(true).mode(0o600).open(path)?;
    f.write_all(b)?;
    f.sync_all()
}

/// A bko-holt document from a run's standard output: the format asked
/// for, or a `bko-holt-error` (its text), or why it isn't one.
pub(crate) fn document(x: &Output, format: &str) -> Result<Value, String> {
    if let Some(n) = &x.not_started {
        return Err(format!("it couldn't be started: {n}"));
    }
    if x.timed_out {
        return Err("it took too long, and was stopped".to_string());
    }
    if x.too_big {
        return Err("its answer was too big".to_string());
    }
    let text = std::str::from_utf8(&x.stdout).map_err(|_| "its answer isn't UTF-8".to_string())?;
    let v = json::parse(text.trim()).map_err(|_| match x.code {
        Some(c) => format!("its answer isn't JSON (exit {c})"),
        None => "its answer isn't JSON".to_string(),
    })?;
    match v.get_str("format") {
        Some(f) if f == format => Ok(v),
        Some("bko-holt-error") => Err(Clean::line(v.get_str("text").unwrap_or("an error"), cap::PROBLEM).as_str().to_string()),
        _ => Err(format!("its answer isn't a {format} document")),
    }
}

/// A document of `schema`, written by the bko-holt the api document
/// named (holt-json.md, "Versions and schemas").
pub(crate) fn same_version(v: Value, schema: u64, version: Option<&str>) -> Result<Value, String> {
    if v.get_u64("schema") != Some(schema) {
        return Err(format!("a document of schema {:?}, not {schema}", v.get("schema").map(Value::compact)));
    }
    match (version, v.get_str("version")) {
        (Some(want), Some(got)) if want != got => Err(format!("written by bko-holt {}, not {want}", Clean::line(got, 32).as_str())),
        _ => Ok(v),
    }
}

/// `--version --json`: its version, its RUN_DIR and whether it writes
/// `bko-holt-snapshot-layout` (open question 15: without it, the stick's
/// copy is the layout, as before), once it says it speaks what this reads
/// and, in a dry run, that its lock took.
pub(crate) fn api(x: &Output, dry_run: bool) -> Result<(String, Option<PathBuf>, bool), String> {
    let v = match document(x, "bko-holt-api") {
        Ok(v) => v,
        // Before RT10 it answered `bko-holt 0.2.6` in text.
        Err(_) if x.code == Some(0) && x.stdout.starts_with(b"bko-holt ") => return Err(s::HOLT_TOO_OLD.to_string()),
        Err(e) => return Err(s::holt_says(&e)),
    };
    if v.get_u64("schema") != Some(API_SCHEMA) {
        return Err(s::HOLT_TOO_OLD.to_string());
    }
    for (doc, schema) in NEEDS {
        let has = v.at(&["documents", doc]).and_then(Value::as_array).is_some_and(|a| a.iter().any(|n| n.as_u64() == Some(*schema)));
        if !has {
            return Err(s::HOLT_TOO_OLD.to_string());
        }
    }
    if dry_run && v.get_bool("no_commit") != Some(true) {
        return Err(s::HOLT_NO_LOCK.to_string());
    }
    let run_dir = v.get_str("run_dir").filter(|d| d.starts_with('/') && !d.split('/').any(|c| c == "..")).map(PathBuf::from);
    let version = v.get_str("version").map(str::to_string).ok_or_else(|| s::holt_says("no version"))?;
    let snapshot_layouts = v.at(&["documents", "snapshot_layout"]).and_then(Value::as_array).is_some_and(|a| a.iter().any(|n| n.as_u64() == Some(layout::SNAP_SCHEMA)));
    Ok((version, run_dir, snapshot_layouts))
}

/// The recovery document's verdict, in words.
fn recovery_problem(doc: &Value) -> String {
    match doc.get_str("problem") {
        Some("checksum") => s::RECOVERY_SUM.to_string(),
        Some("count") => s::recovery_count(doc.get_u64("words").unwrap_or(0)),
        Some("unknown_words") => {
            let u = doc.arr_at(&["unknown"]);
            let first = u.first();
            let n = first.and_then(|x| x.get_u64("position")).unwrap_or(0);
            let sug: Vec<String> = first
                .map(|x| x.arr_at(&["suggest"]).iter().filter_map(Value::as_str).take(3).map(|w| Clean::line(w, 16).as_str().to_string()).collect())
                .unwrap_or_default();
            s::recovery_unknown(n, &sug.join(", "))
        }
        _ => match doc.get_str("error") {
            Some(e) => s::holt_says(Clean::line(e, cap::LOG_LINE).as_str()),
            None => s::RECOVERY_SUM.to_string(),
        },
    }
}

// ---- The runner ----

/// The signals caught for the whole session, so a Ctrl-C, a hang-up or
/// a SIGTERM still wipes the session's files before it ends the program.
/// The full screen's `Term` catches SIGHUP and SIGTERM itself as well,
/// and gives them back to their default when it drops: they are caught
/// again at once.
const SIGS: &[i32] = &[ffi::SIGINT, ffi::SIGHUP, ffi::SIGTERM];

/// Where the session's directory goes: `BKO_RESCUE_RUN_DIR` (for the
/// tests, which don't run as root), else the platform's RAM directory.
pub fn run_base() -> PathBuf {
    match std::env::var("BKO_RESCUE_RUN_DIR") {
        Ok(d) if d.starts_with('/') => PathBuf::from(d),
        _ => PathBuf::from(crate::secret::default_dir()),
    }
}

/// `bko-rescue` with no hidden option: the screens, full screen or plain
/// as the profile says.  The exit status.
pub fn run(p: &Profile, f: &Facts, opts: Opts) -> i32 {
    let base = run_base();
    let session = match Session::new(&base) {
        Ok(s) => s,
        Err(e) => {
            eprintln!("bko-rescue: {}: {}: {}", s::SESSION_FAILED, base.display(), crate::text::ascii(&e.to_string()));
            return 1;
        }
    };
    let scratch = session.dir.clone();
    let sys = sys::RealSys::new(base, scratch);
    let mut app = App::new(Box::new(sys), session, opts, Where::of(f, p));
    let mut prof = p.clone();
    let mut plain = p.plain.is_some();
    let code;
    catch_all();
    loop {
        let r = if !plain {
            let mut fs = match FullScreen::open(&prof) {
                Ok(x) => x,
                Err(e) => {
                    eprintln!("bko-rescue: {}", crate::text::ascii(&e.to_string()));
                    code = 1;
                    break;
                }
            };
            let r = app.run(&mut fs);
            prof = fs.profile.clone();
            let sig = fs.signal();
            let err = fs.error().map(|e| e.to_string());
            drop(fs);
            catch_all();
            if r == Exit::Quit {
                if let Some(e) = &err {
                    eprintln!("bko-rescue: {}", crate::text::ascii(e));
                }
                // A signal the full screen took: 128 + it.
                code = sig.map_or(if err.is_some() { 1 } else { 0 }, |s| 128 + s);
                break;
            }
            r
        } else {
            let mut pl = Plain::new(StdLine::new(), prof.lines);
            let r = app.run(&mut pl);
            let err = pl.error().is_some();
            drop(pl);
            if r == Exit::Quit {
                // A signal still pending ends it below, by that signal.
                code = i32::from(err);
                break;
            }
            r
        };
        match r {
            Exit::Shell => {
                app.sys.shell();
                // A Ctrl-C typed in the shell was the shell's.
                match ffi::take_signal() {
                    Some(sig) if sig != ffi::SIGINT => {
                        code = 128 + sig;
                        break;
                    }
                    _ => {}
                }
            }
            Exit::Plain => plain = true,
            Exit::Done(c) => {
                code = c;
                break;
            }
            Exit::Quit => unreachable!("handled above"),
        }
    }
    // The session's files are wiped here, before a signal ends it.
    drop(app);
    crate::term::tty::keep_signals_caught(false);
    ffi::default(SIGS);
    ffi::raise_pending();
    code
}

/// The session's signals caught, a password read's `Quiet` included.
fn catch_all() {
    crate::term::tty::keep_signals_caught(true);
    ffi::catch(SIGS);
}
