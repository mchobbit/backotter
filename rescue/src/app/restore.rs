//! The Restore with its Cancel?, Done and Failure (rescue-screens.md,
//! sections 9, 10a and 10b), a stage's help, and the restore log
//! (rescue-tui.md, RT12).
//!
//! - The runs are Review's (`App::reviewed`): one `--through close
//!   --commit --expect-plan`, or Start again's three, or Try again's and
//!   Install without an entry's stage by stage.  Each is started on its
//!   own (sys.rs's `How`): detached in a real restore (`systemd-run`,
//!   else a session of its own), a direct child in a dry run, its events
//!   to a 0600 file in the session's directory, followed a tick at a time
//!   (at most EVENTS_PER_TICK) by demo.rs's `Follow`.
//! - A dry run stops its child at once unless `start` says `dry_run` true
//!   and `commit` false, or once a command runs (holt-json.md, "The
//!   flow" 6).  A restore whose `start` doesn't say `commit` true, or names
//!   another plan's hash or bko-holt's version, isn't the run asked for:
//!   Failure, never Done (Principle 6).
//! - Cancel asks first; Stop sends SIGTERM to load's pid as its lock
//!   names it (pid and start time), checked against `start`'s pid, never a
//!   group (J7); a dry run's child, ours and not reaped, by its pid.
//! - Done only when every run ended 0 with every stage it named through;
//!   anything else is Failure, with the stage's help.  Try again runs
//!   status's `next` to `close` a stage at a time, within this session
//!   (J14 interim: after a reboot only Start again).
//! - The log (bko-holt's own, `start.log`) is saved on the stick, gzipped,
//!   in the machine's `restore-logs/` (the newest LOGS_KEPT kept, at most
//!   LOG_GZ_MAX), and in RAM; a dry run writes nothing to the stick.
//! - While a detached load runs, the session's files are kept for it
//!   (`Session::keep`) and `run.json` beside the sessions says where, so a
//!   bko-rescue started again follows it and wipes them once it is gone.

use std::collections::VecDeque;
use std::path::{Path, PathBuf};
use std::time::Instant;

use crate::demo::{self, Failure, Follow, State};
use crate::json;
use crate::term::profile::Os;
use crate::text::{cap, Clean};
use crate::ui::{Act, Answer, Button, Chrome, Message, Progress, Question, TextView, Tick, Ui};

use super::holt_args::{HoltCmd, Load, Stage};
use super::plan::STAGES;
use super::strings as s;
use super::sys::{self, Cmd, How, Running};
use super::{time, wait, write_new, App, Go, Step};

fn o(x: &str) -> Clean {
    Clean::ours(x)
}

// Buttons.
pub(crate) const B_CANCEL: usize = 60;
pub(crate) const B_LOG: usize = 61;
pub(crate) const B_HELP: usize = 62;
pub(crate) const B_TRY: usize = 63;
pub(crate) const B_POWER: usize = 64;
pub(crate) const B_RESTART: usize = 65;
pub(crate) const B_REPORT: usize = 66;
pub(crate) const B_RAW: usize = 67;
pub(crate) const B_FIX: usize = 68;
pub(crate) const B_STOP: usize = 69;
pub(crate) const B_LEAVE: usize = 70;
pub(crate) const B_COMMANDS: usize = 71;
pub(crate) const B_ANYWAY: usize = 72;
pub(crate) const B_FOLLOW: usize = 73;

/// The most of the events file read at each look (rescue-tui.md: the
/// follower reads 4 MiB a tick).
pub const EVENTS_PER_TICK: usize = 4 * 1024 * 1024;
/// How often the screen looks at the events.
pub const TICK_MS: u64 = 250;
/// The log view's lines (rescue-screens.md, "9. Restore": its last 200).
pub const LOG_LINES: usize = 200;
/// What of a log is read for the view.
pub const LOG_VIEW_MAX: usize = 512 * 1024;
/// The restore log on the stick: at most this much compressed, the newest
/// LOGS_KEPT kept (stick-config.md, "Restore logs").
pub const LOG_GZ_MAX: u64 = 2 * 1024 * 1024;
pub const LOGS_KEPT: usize = 10;
/// What of bko-holt's log is saved, at most, before compressing.
pub const LOG_READ_MAX: usize = 32 * 1024 * 1024;
/// Where restore logs go on the stick, under the machine's folder.
pub const LOGS_DIR: &str = "restore-logs";
/// Time left is the rate over this long (rescue-screens.md, "9. Restore":
/// "the rate over the last minute of `progress`").
pub const RATE_WINDOW_MS: u64 = 60_000;
/// Lines of a stage's error Failure shows; Raw output has the rest.
pub const ERROR_LINES: usize = 6;
/// The boot loader's programs whose failure in the boot stage Help offers
/// "Install without an entry" for (rescue-screens.md, "A stage's command
/// failed"), beside holt's own `efi_vars`.
pub const BOOT_PROGRAMS: &[&str] = &["grub-install", "grub2-install", "efibootmgr"];
/// A dry run's Done offers Leave and the commands, never Restart or Power
/// off: `sudo bko-rescue --dry-run` runs on installed systems too (a
/// default taken: the mock is a restore's).
pub const DRY_RUN_RESTARTS: bool = false;
/// The file beside the sessions that says a detached load is running,
/// and in which session's files.
pub const RUN_JSON: &str = "run.json";
/// The most of run.json read.
const RUN_JSON_MAX: usize = 64 * 1024;
/// The most of bko-holt's lock read.
const LOCK_MAX: usize = 4096;

/// Each platform's restart and power off (*Check* each on the images at
/// RT13).
pub fn restart_argv(os: Os) -> &'static [&'static str] {
    match os {
        Os::Linux | Os::Illumos => &["reboot"],
        Os::Haiku => &["shutdown", "-r"],
        _ => &["shutdown", "-r", "now"],
    }
}

pub fn power_off_argv(os: Os) -> &'static [&'static str] {
    match os {
        Os::Linux | Os::Illumos => &["poweroff"],
        Os::Haiku => &["shutdown"],
        Os::OpenBsd => &["shutdown", "-hp", "now"],
        _ => &["shutdown", "-p", "now"],
    }
}

/// A stage on the screen.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Mark {
    Waiting,
    Running,
    Done,
    /// A dry run's: its commands would run.
    Would,
    Skipped,
    Failed,
    Stopped,
}

#[derive(Clone, Debug)]
pub struct Row {
    pub name: String,
    pub mark: Mark,
    pub took_ms: Option<u64>,
    /// A dry run's commands that would run.
    pub would: usize,
}

/// How the runs ended.
#[derive(Clone, Debug)]
pub enum End {
    Done,
    Stopped { stage: Option<String> },
    Failed { stage: Option<String>, code: String, what: String, failure: Option<Box<Failure>> },
}

/// Where the restore log went.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum LogAt {
    /// On the stick: the machine's folder and the file's name there.
    Stick { dir: String, file: String },
    /// In RAM only, and why not on the stick.
    Ram { path: String, why: String },
    None(String),
}

/// One `bko-holt load` running, or ended, and what its events said.
pub struct Run {
    job: Box<dyn Running>,
    pub follow: Follow,
    buf: Vec<u8>,
    t0: Instant,
    drained: bool,
    pub exit: Option<Option<i32>>,
    pub events: PathBuf,
    /// The plan hash it was given (`--expect-plan`), and whether it was to
    /// commit: what its `start` must say.
    expect: Option<String>,
    commit: bool,
    dry_run: bool,
    /// It was stopped by us for not being the run asked for.
    killed: Option<String>,
}

impl Run {
    fn ms(&self) -> u64 {
        u64::try_from(self.t0.elapsed().as_millis()).unwrap_or(u64::MAX)
    }

    fn pid(&self) -> Option<u32> {
        self.follow.pid.and_then(|p| u32::try_from(p).ok())
    }

    /// One look: whether it ended, then new events (read after the end
    /// is seen, so a read to the end after it has every event).  A dry
    /// run that isn't one is stopped at once.
    fn poll(&mut self) {
        if !self.drained {
            if self.exit.is_none() {
                self.exit = self.job.ended(self.pid());
            }
            let at_end = if self.follow.error.is_some() {
                true
            } else {
                self.buf.clear();
                let end = self.job.read(&mut self.buf, EVENTS_PER_TICK);
                let ms = self.ms();
                self.follow.feed(&self.buf, ms);
                end
            };
            if self.dry_run && self.killed.is_none() {
                let why = self.follow.error.as_ref().map(|e| e.as_str().to_string()).or_else(|| self.follow.not_dry().map(|_| s::NOT_ASKED.to_string()));
                if let Some(w) = why {
                    self.killed = Some(w);
                    self.job.kill();
                    self.exit = self.job.ended(self.pid());
                }
            }
            if self.exit.is_some() && at_end {
                self.drained = true;
            }
        }
    }

    fn finished(&self) -> bool {
        self.drained
    }
}

/// The Restore: its runs, the one running, the stages as shown.
pub struct Restore {
    pub runs: Vec<Load>,
    pub next: usize,
    pub run: Option<Run>,
    pub rows: Vec<Row>,
    pub t0: Instant,
    /// When it began, by the system's clock (seconds), and how long it
    /// took (ms): Done's "in 36 min".
    pub began: i64,
    pub took_ms: Option<u64>,
    pub stop_asked: bool,
    pub end: Option<End>,
    pub log: Option<LogAt>,
    /// The last run's events and stderr, for Failure's raw output and the
    /// report.
    pub last: Option<Follow>,
    pub stderr: Vec<Clean>,
    pub events: Option<PathBuf>,
    /// Commands a dry run showed, every run's.
    pub would: usize,
    pub cmds: Vec<Clean>,
    samples: VecDeque<(u64, u64)>,
    /// Followed from an earlier bko-rescue's run.json: its session's
    /// directory, wiped once it ends; no Try again.
    pub attached: Option<PathBuf>,
}

impl Restore {
    pub fn new(runs: Vec<Load>, done_before: &[String]) -> Restore {
        let rows = STAGES
            .iter()
            .map(|n| Row { name: n.to_string(), mark: if done_before.iter().any(|d| d == n) { Mark::Done } else { Mark::Waiting }, took_ms: None, would: 0 })
            .collect();
        Restore {
            runs,
            next: 0,
            run: None,
            rows,
            t0: Instant::now(),
            began: 0,
            took_ms: None,
            stop_asked: false,
            end: None,
            log: None,
            last: None,
            stderr: Vec::new(),
            events: None,
            would: 0,
            cmds: Vec::new(),
            samples: VecDeque::new(),
            attached: None,
        }
    }

    /// The rows, with the running run's stages over them.
    fn merged(&self) -> Vec<Row> {
        let mut rows = self.rows.clone();
        if let Some(r) = &self.run {
            merge(&mut rows, &r.follow, r.ms());
        }
        rows
    }

    /// The stage where it stopped: the one that failed, ran, or came next.
    fn stage_at(&self) -> Option<String> {
        let rows = self.merged();
        rows.iter()
            .find(|r| matches!(r.mark, Mark::Failed | Mark::Running | Mark::Stopped))
            .or_else(|| rows.iter().find(|r| r.mark == Mark::Waiting))
            .map(|r| r.name.clone())
    }

    /// Time left from the copy's rate over the last minute.
    fn left(&mut self, bytes: Option<u64>, total: Option<u64>, ms: u64) -> Option<String> {
        let (b, t) = (bytes?, total?);
        if self.samples.back().is_none_or(|x| x.1 != b) {
            self.samples.push_back((ms, b));
        }
        while self.samples.front().is_some_and(|x| ms.saturating_sub(x.0) > RATE_WINDOW_MS) && self.samples.len() > 2 {
            self.samples.pop_front();
        }
        let (&(t0, b0), &(t1, b1)) = (self.samples.front()?, self.samples.back()?);
        if t1 <= t0 || b1 <= b0 {
            return None;
        }
        let rate = (b1 - b0) as f64 / (t1 - t0) as f64;
        let left_ms = t.saturating_sub(b) as f64 / rate;
        let min = (left_ms / 60_000.0).ceil() as u64;
        Some(if min <= 1 && left_ms < 60_000.0 { s::LESS_THAN_A_MINUTE.to_string() } else { s::minutes_left(min) })
    }
}

/// A Follow's stages over the rows: what it ran, skipped or failed.
fn merge(rows: &mut [Row], f: &Follow, ms: u64) {
    for st in &f.stages {
        let Some(r) = rows.iter_mut().find(|r| r.name == st.name.as_str()) else { continue };
        let mark = match st.state {
            State::Waiting => continue,
            State::Running if f.stopped == Some(true) || f.ended.is_some() => Mark::Stopped,
            State::Running => Mark::Running,
            State::Done => Mark::Done,
            State::Would => Mark::Would,
            State::Skipped => Mark::Skipped,
            // Not plainly done: shown as skipped, never ticked.
            State::SkippedWhy => Mark::Skipped,
            State::Failed => Mark::Failed,
        };
        r.mark = mark;
        r.would = st.would;
        r.took_ms = match mark {
            Mark::Running => Some(ms.saturating_sub(st.from_ms)),
            _ => st.took_ms,
        };
    }
}

/// `2 s`, `21 min`, `1 h 5 min`.
pub fn dur(ms: u64) -> String {
    let s = ms / 1000;
    if s < 120 {
        format!("{s} s")
    } else if s < 3600 * 2 {
        format!("{} min", s / 60)
    } else {
        format!("{} h {} min", s / 3600, s / 60 % 60)
    }
}

/// A stage's plain words; the assemble stage's from what it runs.
pub fn label(name: &str, assemble: &[String]) -> String {
    if name == "assemble" {
        let enc = assemble.iter().any(|p| matches!(p.as_str(), "cryptsetup" | "geli" | "bioctl"));
        let raid = assemble.iter().any(|p| matches!(p.as_str(), "mdadm" | "pvcreate" | "vgcreate" | "lvcreate" | "zpool" | "vgchange" | "lvchange"));
        return match (enc, raid) {
            (false, true) => "Set up RAID and volumes",
            (true, true) => "Set up RAID and encryption",
            _ => "Set up encryption",
        }
        .to_string();
    }
    demo::stage_label(name).map_or_else(|| Clean::line(name, 32).as_str().to_string(), str::to_string)
}

fn mark_word(m: Mark) -> &'static str {
    match m {
        Mark::Waiting => "[ ]",
        Mark::Running => "[>]",
        Mark::Done => "[x]",
        Mark::Would => "[~]",
        Mark::Skipped => "[-]",
        Mark::Failed | Mark::Stopped => "[!]",
    }
}

/// The stages, a row each, as the Restore mock draws them.
fn rows_text(rows: &[Row], assemble: &[String]) -> String {
    let mut t = String::new();
    for r in rows {
        let after = match r.mark {
            Mark::Waiting => String::new(),
            Mark::Running => s::running_for(&dur(r.took_ms.unwrap_or(0))),
            Mark::Done => r.took_ms.map(dur).unwrap_or_default(),
            Mark::Would => s::would_run(r.would),
            Mark::Skipped => s::NOTHING_TO_DO.to_string(),
            Mark::Failed => s::FAILED_WORD.to_string(),
            Mark::Stopped => s::STOPPED_WORD.to_string(),
        };
        let row = format!("  {} {:<26}{after}", mark_word(r.mark), label(&r.name, assemble));
        t.push_str(row.trim_end());
        t.push('\n');
    }
    t
}

/// Plain mode's: only the stages begun, `[x] Make partitions: done in 2 s`.
fn rows_plain(rows: &[Row], assemble: &[String]) -> String {
    let mut t = String::new();
    for r in rows {
        let l = label(&r.name, assemble);
        let line = match r.mark {
            Mark::Waiting => continue,
            Mark::Running => s::plain_started(&l),
            Mark::Done => r.took_ms.map_or_else(|| l.clone(), |ms| s::done_in(&l, &dur(ms))),
            Mark::Would => s::plain_would(&l, r.would),
            Mark::Skipped => format!("{l}: {}", s::NOTHING_TO_DO),
            Mark::Failed => format!("{l}: {}", s::FAILED_WORD),
            Mark::Stopped => format!("{l}: {}", s::STOPPED_WORD),
        };
        t.push_str(&format!("{} {line}\n", mark_word(r.mark)));
    }
    t
}

/// Done's and Failure's stages, two a row (rescue-screens.md, 10a, 10b).
fn rows_two(rows: &[Row], assemble: &[String], took: Option<&str>) -> String {
    let cells: Vec<String> = rows.iter().map(|r| format!("{} {}", mark_word(r.mark), label(&r.name, assemble))).collect();
    let mut t = String::new();
    let mut i = 0;
    while i < cells.len() {
        let last = i + 2 >= cells.len();
        let mut line = match cells.get(i + 1) {
            Some(b) => format!("  {:<30}{b}", cells[i]),
            None => format!("  {}", cells[i]),
        };
        if let (true, Some(d)) = (last, took) {
            let w = line.chars().count();
            line.push_str(&" ".repeat(51usize.saturating_sub(w).max(2)));
            line.push_str(&s::in_time(d));
        }
        t.push_str(&line);
        t.push('\n');
        i += 2;
    }
    t
}

/// bko-holt's lock: its pid and start time (J8).
pub fn read_lock(b: &[u8]) -> Option<(u32, Option<String>)> {
    let v = json::parse(std::str::from_utf8(b).ok()?.trim()).ok()?;
    let pid = u32::try_from(v.get_u64("pid")?).ok()?;
    Some((pid, v.get_str("start").map(str::to_string)))
}

/// An absolute path with no `..`: one bko-holt named that may be read.
fn plain_path(p: &str) -> bool {
    p.starts_with('/') && !p.split('/').any(|c| c == "..") && !p.contains('\0') && p.len() < 4096
}

/// A name that can be a folder on the stick: an install ID.
fn plain_name(x: &str) -> bool {
    !x.is_empty() && x.len() <= 64 && x != "." && x != ".." && x.bytes().all(|b| b.is_ascii_alphanumeric() || matches!(b, b'-' | b'_' | b'.'))
}

impl App {
    fn assemble(&self) -> Vec<String> {
        self.plan.as_ref().map(|p| p.assemble.clone()).unwrap_or_default()
    }

    // ---- 9. Restore ----

    pub(crate) fn restore_screen(&mut self, ui: &mut dyn Ui) -> Go {
        if self.restore.is_none() {
            if self.reviewed.is_empty() {
                return Go::Back;
            }
            let mut r = Restore::new(self.reviewed.clone(), &[]);
            r.began = self.sys.now();
            self.restore = Some(r);
        }
        loop {
            if self.restore.is_none() {
                return Go::Back;
            }
            if self.restore.as_ref().is_some_and(|r| r.end.is_some()) {
                return self.end_screen(ui);
            }
            if self.restore.as_ref().is_some_and(|r| r.run.is_none()) {
                self.start_next();
                continue;
            }
            match self.follow(ui) {
                Some(g) => return g,
                None => self.run_ended(),
            }
        }
    }

    /// Start the next run, or, with none left, end Done.
    fn start_next(&mut self) {
        let now = self.sys.now();
        let Some(r) = self.restore.as_mut() else { return };
        let Some(l) = r.runs.get(r.next).cloned() else {
            r.end = Some(End::Done);
            r.took_ms = Some(u64::try_from(now.saturating_sub(r.began)).unwrap_or(0).saturating_mul(1000));
            self.restore_ended();
            return;
        };
        r.next += 1;
        r.stop_asked = false;
        let dry = self.opts.dry_run;
        let n = r.next;
        let Some(mut cmd) = self.holt_cmd(&HoltCmd::Load(&l)) else {
            self.fail_now(s::NOT_STARTED, s::holt_missing("bko-holt"));
            return;
        };
        cmd.timeout_ms = u64::MAX;
        let events = self.session.path(&format!("events-{n}"));
        let stderr = self.session.path(&format!("stderr-{n}"));
        sys::wipe_file(&events);
        sys::wipe_file(&stderr);
        let how = if dry { How::Child } else { How::Detached };
        match self.sys.start_load(cmd, how, &events, &stderr) {
            Ok(job) => {
                if job.detached() {
                    // Its key files and password outlive us, as it does.
                    self.session.keep = Some(None);
                    self.write_run_json(&events, &stderr, None);
                }
                let run = Run {
                    job,
                    follow: Follow::new(),
                    buf: Vec::new(),
                    t0: Instant::now(),
                    drained: false,
                    exit: None,
                    events: events.clone(),
                    expect: l.expect_plan.clone(),
                    commit: l.commit && !dry,
                    dry_run: dry,
                    killed: None,
                };
                if let Some(r) = self.restore.as_mut() {
                    r.events = Some(events);
                    r.run = Some(run);
                }
            }
            Err(e) => self.fail_now(s::NOT_STARTED, e),
        }
    }

    fn fail_now(&mut self, what: &str, why: String) {
        if let Some(r) = self.restore.as_mut() {
            let stage = r.stage_at();
            r.end = Some(End::Failed { stage, code: "not_started".into(), what: format!("{what}: {}", Clean::line(&why, cap::PROBLEM).as_str()), failure: None });
        }
        self.restore_ended();
    }

    /// The progress screen until the run ends (None), or an answer that
    /// leaves it.
    fn follow(&mut self, ui: &mut dyn Ui) -> Option<Go> {
        let full = ui.full_screen();
        let dry = self.opts.dry_run;
        let host = self.restore_host();
        let assemble = self.assemble();
        // Plain mode's first line is the screen's name, as its transcript.
        let title = if !full { s::SCR_RESTORE.to_string() } else if dry { s::dry_title(&host) } else { s::restoring_title(&host) };
        let mut p = Progress::new(Chrome::new(o(s::SCR_RESTORE), Clean::line(&title, cap::LINE)), Clean::default());
        if full {
            p.after = o(if dry { s::DRY_AFTER } else { s::RESTORE_AFTER });
            p.buttons = vec![Button::other(s::BTN_CANCEL, None, B_CANCEL)];
            p.hidden = vec![Button::other(s::BTN_LOG, Some('l'), B_LOG)];
            p.chrome.keys = Some(o(s::RESTORE_KEYS));
        } else {
            p.buttons = vec![Button::other(s::BTN_STOP_PLAIN, Some('c'), B_CANCEL)];
            p.hidden = vec![Button::other(s::BTN_LOG, Some('l'), B_LOG)];
        }
        loop {
            {
                let r = self.restore.as_mut()?;
                p.text = screen_text(r, &assemble, full, &host, dry);
            }
            let a = {
                let r = self.restore.as_mut()?;
                let assemble = &assemble;
                let host = &host;
                let mut tick = || {
                    let Some(run) = r.run.as_mut() else { return Tick::Done };
                    run.poll();
                    if run.finished() {
                        return Tick::Done;
                    }
                    let ms = run.ms();
                    let (bytes, total, pct) = (run.follow.bytes, run.follow.total, run.follow.pct());
                    let mut fig = run.follow.figures().as_str().to_string();
                    if let Some(l) = r.left(bytes, total, ms) {
                        fig = if fig.is_empty() { l } else if full { format!("{fig}   {l}") } else { format!("{fig}, {l}") };
                    }
                    Tick::At { text: Some(screen_text(r, assemble, full, host, dry)), pct, line: Clean::line(&fig, cap::LINE), next_ms: TICK_MS }
                };
                ui.progress(&p, &mut tick)
            };
            self.keep_for_run();
            match a {
                Answer::Ok(()) => {
                    if self.restore.as_ref().and_then(|r| r.run.as_ref()).is_some_and(Run::finished) {
                        return None;
                    }
                }
                Answer::Button(B_CANCEL) => {
                    if let Some(g) = self.cancel(ui) {
                        return Some(g);
                    }
                }
                Answer::Button(B_LOG) => {
                    if let Some(g) = self.log_view(ui) {
                        return Some(g);
                    }
                }
                // A signal, or plain mode: the run goes on (a detached
                // one outlives us; a dry run's child is stopped with the
                // session).
                Answer::Quit => return Some(Go::Quit),
                Answer::Plain => return Some(Go::Plain),
                Answer::Shell => return Some(Go::Shell),
                _ => {}
            }
        }
    }

    /// The host the restore is of: the machine's, else the run's own.
    fn restore_host(&self) -> String {
        if self.chosen().is_some() || self.host.is_some() {
            return self.host_name();
        }
        self.restore
            .as_ref()
            .and_then(|r| r.run.as_ref().and_then(|x| x.follow.hostname.clone()).or_else(|| r.last.as_ref().and_then(|f| f.hostname.clone())))
            .map_or_else(|| "this machine".to_string(), |h| h.as_str().to_string())
    }

    /// The session keeps its files while a detached load runs, and
    /// run.json says its pid once it is known.
    fn keep_for_run(&mut self) {
        let Some(run) = self.restore.as_ref().and_then(|r| r.run.as_ref()) else { return };
        if !run.job.detached() {
            return;
        }
        let pid = run.pid();
        if self.session.keep == Some(None) && pid.is_some() {
            self.session.keep = Some(pid);
            let (e, st) = (run.events.clone(), self.session.path(&format!("stderr-{}", self.restore.as_ref().map_or(0, |r| r.next))));
            self.write_run_json(&e, &st, pid);
        }
    }

    /// `run.json`, beside the sessions: where a detached load's events are,
    /// and its session (rescue-tui.md: a restarted bko-rescue "finds load
    /// still running and carries on here").
    fn write_run_json(&mut self, events: &Path, stderr: &Path, pid: Option<u32>) {
        let base = self.sys.run_base();
        let install = self.chosen().map(|m| m.install_id.clone()).unwrap_or_default();
        let doc = crate::json::Obj::new()
            .set("format", "bko-rescue-run")
            .set("session", self.session.dir.display().to_string())
            .set("events", events.display().to_string())
            .set("stderr", stderr.display().to_string())
            .set("pid", pid.map(u64::from))
            .set("host", self.host_name())
            .set("install_id", install)
            .build();
        let tmp = base.join(format!("{RUN_JSON}.{}", std::process::id()));
        sys::wipe_file(&tmp);
        if write_new(&tmp, doc.compact().as_bytes()).is_ok() {
            let _ = std::fs::rename(&tmp, base.join(RUN_JSON));
        }
    }

    /// Cancel?: Keep going (the focus, and Esc) or Stop.
    fn cancel(&mut self, ui: &mut dyn Ui) -> Option<Go> {
        let dry = self.opts.dry_run;
        let host = self.restore_host();
        let copying = self.restore.as_ref().and_then(|r| r.stage_at()).as_deref() == Some("restore");
        let (title, text) = if dry { (s::DRY_STOP_TITLE, s::DRY_STOP_TEXT.to_string()) } else { (s::STOP_TITLE, s::stop_text(&host, copying)) };
        let mut m = Message::new(Chrome::ours(s::SCR_RESTORE, title), Clean::text(&text, cap::PROBLEM));
        // Esc is Keep going: it is the Back of this box.
        m.buttons = vec![Button::new(s::BTN_KEEP_GOING, Some('k'), Act::Back), Button::other(s::BTN_STOP, None, B_STOP)];
        m.chrome.keys = Some(o(s::STOP_KEYS));
        match ui.message(&m) {
            Answer::Button(B_STOP) => {}
            Answer::Back | Answer::Ok(()) | Answer::Button(_) => return None,
            a => return Some(Go::of(a)),
        }
        match self.stop_load() {
            Ok(()) => {
                if let Some(r) = self.restore.as_mut() {
                    r.stop_asked = true;
                }
                None
            }
            Err(why) => {
                let m = Message::new(Chrome::ours(s::SCR_RESTORE, s::STOP_FAILED_TITLE), Clean::text(&s::stop_failed(&why), cap::PROBLEM));
                match ui.message(&m) {
                    Answer::Ok(()) | Answer::Back => None,
                    a => Some(Go::of(a)),
                }
            }
        }
    }

    /// SIGTERM to load (J7): the pid its lock names, which must be the
    /// one its `start` said, and on Linux of the start time the lock
    /// recorded; a dry run's child (no lock: its runs don't commit) by
    /// its pid, ours and not reaped.
    fn stop_load(&mut self) -> Result<(), String> {
        let run_dir = self.run_dir.clone();
        let dry = self.opts.dry_run;
        let Some(run) = self.restore.as_mut().and_then(|r| r.run.as_mut()) else { return Ok(()) };
        run.poll();
        if run.finished() {
            return Ok(());
        }
        let pid = run.pid().ok_or(s::STOP_NO_PID)?;
        if dry || !run.commit {
            return if run.job.term(pid) { Ok(()) } else { Err(s::STOP_NOT_SENT.to_string()) };
        }
        let lock = run_dir.map(|d| d.join("load.lock")).and_then(|p| self.sys.read(&p, LOCK_MAX)).and_then(|b| read_lock(&b)).ok_or(s::STOP_NO_LOCK)?;
        if lock.0 != pid {
            return Err(s::STOP_LOCK_OTHER.to_string());
        }
        if let (Some(want), Some(got)) = (&lock.1, self.sys.proc_start(pid)) {
            if *want != got {
                return Err(s::STOP_LOCK_OTHER.to_string());
            }
        }
        let run = self.restore.as_mut().and_then(|r| r.run.as_mut()).ok_or(s::STOP_NOT_SENT)?;
        if run.job.term(pid) {
            Ok(())
        } else {
            Err(s::STOP_NOT_SENT.to_string())
        }
    }

    /// `l`: bko-holt's log, its last lines; a dry run's, the commands
    /// shown so far.
    fn log_view(&mut self, ui: &mut dyn Ui) -> Option<Go> {
        let dry = self.opts.dry_run;
        let lines = self.log_lines();
        let mut v = TextView::new(Chrome::ours(s::SCR_RESTORE, if dry { s::DRY_LOG_TITLE } else { s::LOG_TITLE }), lines);
        v.buttons.push(Button::back());
        match ui.view(&v) {
            Answer::Ok(()) | Answer::Back => None,
            a => Some(Go::of(a)),
        }
    }

    fn log_lines(&self) -> Vec<Clean> {
        let Some(r) = self.restore.as_ref() else { return Vec::new() };
        let f = r.run.as_ref().map(|x| &x.follow).or(r.last.as_ref());
        let mut cmds: Vec<Clean> = r.cmds.clone();
        if let Some(run) = &r.run {
            cmds.extend(run.follow.cmds.iter().cloned());
        }
        if self.opts.dry_run {
            let n = cmds.len().saturating_sub(demo::CMDS_MAX);
            return cmds.split_off(n);
        }
        let log = f.and_then(|f| f.log.clone()).filter(|p| plain_path(p));
        let text = log.and_then(|p| self.sys.read_tail(Path::new(&p), LOG_VIEW_MAX));
        match text {
            Some(b) => {
                let t = String::from_utf8_lossy(&b).to_string();
                let all: Vec<&str> = t.lines().collect();
                all[all.len().saturating_sub(LOG_LINES)..].iter().map(|l| Clean::line(l, cap::LOG_LINE)).collect()
            }
            None => vec![o(s::LOG_NONE)],
        }
    }

    /// A run ended: what it says, then the next run, or the end.
    fn run_ended(&mut self) {
        let version = self.version.clone();
        let Some(r) = self.restore.as_mut() else { return };
        let Some(run) = r.run.take() else { return };
        merge(&mut r.rows, &run.follow, run.ms());
        r.would += run.follow.would;
        r.cmds.extend(run.follow.cmds.iter().cloned());
        r.stderr = String::from_utf8_lossy(&run.job.stderr()).lines().map(|l| Clean::line(l, cap::LOG_LINE)).collect();
        let verdict = judge(&run, version.as_deref(), r.stop_asked);
        r.last = Some(run.follow);
        match verdict {
            None => {}
            Some(End::Stopped { stage }) => {
                let stage = stage.or_else(|| r.stage_at());
                for row in &mut r.rows {
                    if Some(&row.name) == stage.as_ref() && matches!(row.mark, Mark::Running | Mark::Waiting) {
                        row.mark = Mark::Stopped;
                    }
                }
                r.end = Some(End::Stopped { stage });
            }
            Some(End::Failed { stage, code, what, failure }) => {
                let stage = stage.or_else(|| r.stage_at());
                r.end = Some(End::Failed { stage, code, what, failure });
            }
            Some(End::Done) => {}
        }
        if r.end.is_some() {
            let now = self.sys.now();
            if let Some(r) = self.restore.as_mut() {
                r.took_ms = Some(u64::try_from(now.saturating_sub(r.began)).unwrap_or(0).saturating_mul(1000));
            }
            self.restore_ended();
        }
    }

    /// The runs are over: the session's files are its own again, the log
    /// saved.
    fn restore_ended(&mut self) {
        self.session.keep = None;
        let base = self.sys.run_base();
        let ours = std::fs::read(base.join(RUN_JSON)).ok().is_some_and(|b| String::from_utf8_lossy(&b).contains(&self.session.dir.display().to_string()));
        if ours {
            let _ = std::fs::remove_file(base.join(RUN_JSON));
        }
        if let Some(d) = self.restore.as_mut().and_then(|r| r.attached.take()) {
            sys::wipe_dir(&d);
            let _ = std::fs::remove_file(base.join(RUN_JSON));
        }
        let at = self.save_log();
        if let Some(r) = self.restore.as_mut() {
            r.log = Some(at);
        }
    }

    // ---- The restore log ----

    /// bko-holt's log, gzipped, into RAM and onto the stick's machine
    /// folder.
    fn save_log(&mut self) -> LogAt {
        if self.opts.dry_run {
            return LogAt::None(s::REPORT_DRY_RUN.to_string());
        }
        let Some(src) = self.restore.as_ref().and_then(|r| r.last.as_ref()).and_then(|f| f.log.clone()).filter(|p| plain_path(p)) else {
            return LogAt::None(s::LOG_NO_HOLT_LOG.to_string());
        };
        let Some(mut data) = self.sys.read_tail(Path::new(&src), LOG_READ_MAX) else { return LogAt::None(s::LOG_NOT_READ.to_string()) };
        let dir = self.sys.run_base().join(LOGS_DIR);
        if let Err(e) = sys::own_dir(&dir, None) {
            return LogAt::None(e.to_string());
        }
        let stamp = time::stamp_z(self.sys.now());
        let mut file = dir.join(format!("{stamp}.log"));
        // gzip in place, a smaller tail each time it is too big.
        for _ in 0..8 {
            sys::wipe_file(&file);
            let gz = dir.join(format!("{stamp}.log.gz"));
            sys::wipe_file(&gz);
            if write_new(&file, &data).is_err() {
                return LogAt::None(s::LOG_NOT_READ.to_string());
            }
            let Some(gzip) = self.sys.which("gzip") else { break };
            let mut c = Cmd::new(&gzip, vec!["-n".into(), "-9".into(), file.as_os_str().to_owned()]);
            c.timeout_ms = 120_000;
            let out = wait(self.sys.start(c), &mut *self.sys);
            let size = std::fs::metadata(&gz).map(|m| m.len()).ok();
            match size {
                Some(n) if out.code == Some(0) && n <= LOG_GZ_MAX => {
                    file = gz;
                    break;
                }
                Some(_) if out.code == Some(0) && data.len() > 64 * 1024 => {
                    sys::wipe_file(&gz);
                    let keep = data.len() / 2;
                    data = data.split_off(data.len() - keep);
                }
                _ => {
                    sys::wipe_file(&gz);
                    break;
                }
            }
        }
        let name = file.file_name().and_then(|n| n.to_str()).unwrap_or_default().to_string();
        let ram = file.display().to_string();
        let install = self.chosen().map(|m| m.install_id.clone()).filter(|x| plain_name(x));
        let Some(id) = install else { return LogAt::Ram { path: ram, why: s::LOG_NO_FOLDER.to_string() } };
        let rel = format!("backotter/machines/{id}/{LOGS_DIR}");
        match self.save_to_stick_dir(&file, &rel, Some(LOGS_KEPT)) {
            Ok(_) => LogAt::Stick { dir: format!("backotter/machines/{id}"), file: format!("{LOGS_DIR}/{name}") },
            Err(why) => LogAt::Ram { path: ram, why },
        }
    }

    // ---- 10a. Done, 10b. Failure ----

    fn end_screen(&mut self, ui: &mut dyn Ui) -> Go {
        match self.restore.as_ref().and_then(|r| r.end.clone()) {
            Some(End::Done) if self.opts.dry_run => self.dry_done(ui),
            Some(End::Done) => self.done(ui),
            Some(e) => self.failure(ui, e),
            None => Go::Stay,
        }
    }

    fn log_words(&self, at_length: bool) -> String {
        let host = self.restore_host();
        match self.restore.as_ref().and_then(|r| r.log.clone()) {
            Some(LogAt::Stick { dir, file }) if at_length => s::log_on_stick_at(&host, &dir, &file),
            Some(LogAt::Stick { .. }) => s::log_on_stick(&host),
            Some(LogAt::Ram { path, why }) => s::log_in_ram(Clean::line(&path, cap::LINE).as_str(), Clean::line(&why, cap::LINE).as_str()),
            Some(LogAt::None(why)) => s::log_none(Clean::line(&why, cap::LINE).as_str()),
            None => String::new(),
        }
    }

    fn done(&mut self, ui: &mut dyn Ui) -> Go {
        let full = ui.full_screen();
        let host = self.restore_host();
        let r = self.restore.as_ref().expect("a restore ended");
        let assemble = self.assemble();
        let took = r.took_ms.map(dur);
        let mut text = if full { rows_two(&r.rows, &assemble, took.as_deref()) } else { rows_plain(&r.rows, &assemble) };
        text.push('\n');
        if !self.keys.is_empty() {
            text.push_str(s::DONE_PASSPHRASE);
            text.push_str("\n\n");
        }
        let after = self.after_first();
        if !after.is_empty() {
            text.push_str(s::AFTER_FIRST);
            for a in &after {
                text.push('\n');
                text.push_str(a);
            }
            text.push_str("\n\n");
        }
        text.push_str(&self.log_words(false));
        text.push_str("\n\n");
        text.push_str(&s::remove_stick(&host));
        let mut m = Message::new(Chrome::new(o(s::SCR_DONE), Clean::line(&s::done_title(&host), cap::LINE)), Clean::text(&text, cap::PROBLEM * 2));
        m.buttons = vec![Button::other(s::BTN_RESTART, None, B_RESTART), Button::shell(), Button::other(s::BTN_POWER_OFF, None, B_POWER)];
        m.chrome.keys = Some(o(s::DONE_KEYS));
        loop {
            match ui.message(&m) {
                Answer::Button(B_RESTART) => {
                    if let Some(g) = self.restart(ui) {
                        return g;
                    }
                }
                Answer::Button(B_POWER) => {
                    if let Some(g) = self.power_off(ui) {
                        return g;
                    }
                }
                // No Back: nothing is left to go back to.
                Answer::Back | Answer::Ok(()) | Answer::Button(_) => {}
                a => return Go::of(a),
            }
        }
    }

    /// "After the first start": the tokens to enrol again, the
    /// filesystems made anew (rescue-screens.md, 10a: from `tokens` and
    /// `notes`).
    fn after_first(&self) -> Vec<String> {
        let mut v = Vec::new();
        if let Some(p) = &self.plan {
            for (c, k) in &p.tokens {
                let c = Clean::line(c, 64).as_str().to_string();
                v.push(match k.as_str() {
                    "systemd-tpm2" | "tpm2" => s::token_tpm2(&c),
                    "systemd-fido2" | "fido2" => s::token_fido2(&c),
                    k => s::token_other(&c, Clean::line(k, 32).as_str()),
                });
            }
            for (m, t) in &self.reformat {
                let was = p.reformat.iter().find(|r| r.mount == *m).map(|r| super::review::fs_name(&r.kind)).unwrap_or_default();
                v.push(s::reformat_after(Clean::line(m, 64).as_str(), &super::review::fs_name(t), &was));
            }
        }
        v
    }

    fn dry_done(&mut self, ui: &mut dyn Ui) -> Go {
        let full = ui.full_screen();
        let host = self.restore_host();
        let r = self.restore.as_ref().expect("a restore ended");
        let assemble = self.assemble();
        let mut text = if full { rows_two(&r.rows, &assemble, None) } else { rows_plain(&r.rows, &assemble) };
        text.push('\n');
        text.push_str(&s::dry_done(r.would));
        let mut m = Message::new(Chrome::new(o(s::SCR_DONE), Clean::line(&s::dry_done_title(&host), cap::LINE)), Clean::text(&text, cap::PROBLEM));
        m.buttons = vec![Button::other(s::BTN_SHOW_COMMANDS, Some('l'), B_COMMANDS), Button::other(s::BTN_LEAVE, None, B_LEAVE), Button::shell()];
        if DRY_RUN_RESTARTS {
            m.buttons.push(Button::other(s::BTN_RESTART, None, B_RESTART));
        }
        m.chrome.keys = Some(o(s::DRY_DONE_KEYS));
        loop {
            match ui.message(&m) {
                Answer::Button(B_COMMANDS) => {
                    if let Some(g) = self.log_view(ui) {
                        return g;
                    }
                }
                Answer::Button(B_LEAVE) => return Go::Exit(0),
                Answer::Back | Answer::Ok(()) | Answer::Button(_) => {}
                a => return Go::of(a),
            }
        }
    }

    /// Restart: the stick still in is said first, with Restart anyway.
    fn restart(&mut self, ui: &mut dyn Ui) -> Option<Go> {
        let dev = self.stick.as_ref().and_then(|st| st.dev.clone());
        if dev.is_some_and(|d| self.sys.exists(Path::new(&d))) {
            let mut m = Message::new(Chrome::ours(s::SCR_DONE, s::STICK_IN_TITLE), o(s::STICK_IN));
            m.buttons = vec![Button::back(), Button::other(s::BTN_RESTART_ANYWAY, None, B_ANYWAY)];
            match ui.message(&m) {
                Answer::Button(B_ANYWAY) => {}
                Answer::Back | Answer::Ok(()) | Answer::Button(_) => return None,
                a => return Some(Go::of(a)),
            }
        }
        self.off(ui, restart_argv(self.wh.os), s::BTN_RESTART)
    }

    fn power_off(&mut self, ui: &mut dyn Ui) -> Option<Go> {
        self.off(ui, power_off_argv(self.wh.os), s::BTN_POWER_OFF)
    }

    /// Run the restart or power off; once it is under way bko-rescue ends.
    fn off(&mut self, ui: &mut dyn Ui, argv: &[&str], what: &str) -> Option<Go> {
        let why = match self.sys.which(argv[0]) {
            Some(prog) => {
                let mut c = Cmd::new(&prog, argv[1..].iter().map(Into::into).collect());
                c.timeout_ms = 60_000;
                let out = wait(self.sys.start(c), &mut *self.sys);
                if out.code == Some(0) {
                    return Some(Go::Exit(0));
                }
                super::first_line(&out)
            }
            None => format!("{} isn't here", argv[0]),
        };
        let m = Message::new(Chrome::ours(s::SCR_DONE, s::OFF_FAILED_TITLE), Clean::text(&s::off_failed(what, &why), cap::PROBLEM));
        match ui.message(&m) {
            Answer::Ok(()) | Answer::Back => None,
            a => Some(Go::of(a)),
        }
    }

    fn failure(&mut self, ui: &mut dyn Ui, end: End) -> Go {
        let full = ui.full_screen();
        let assemble = self.assemble();
        let (stage, body, stopped) = match &end {
            End::Stopped { stage } => (stage.clone(), s::STOPPED_BY_YOU.to_string(), true),
            End::Failed { stage, what, failure, .. } => (stage.clone(), failure_text(what, failure.as_deref()), false),
            End::Done => (None, String::new(), false),
        };
        let title = match &stage {
            Some(st) => s::stopped_at(&label(st, &assemble)),
            None => s::STOPPED_EARLY.to_string(),
        };
        let r = self.restore.as_ref().expect("a restore ended");
        let mut text = if full { format!("{}\n", rows_two(&r.rows, &assemble, None)) } else { String::new() };
        text.push_str(&body);
        text.push_str("\n\n");
        text.push_str(s::BACKUP_SAFE);
        let log = self.log_words(true);
        if !log.is_empty() {
            text.push_str("\n\n");
            text.push_str(&log);
        }
        let attached = r.attached.is_some() || self.reviewed.is_empty();
        if attached {
            text.push_str("\n\n");
            text.push_str(s::ATTACHED_NO_RETRY);
        }
        let mut m = Message::new(Chrome::new(o(s::SCR_FAILURE), Clean::line(&s::problem_title(&title), cap::LINE)), Clean::text(&text, cap::PROBLEM * 2));
        m.buttons = vec![Button::other(s::BTN_HELP, Some('h'), B_HELP)];
        if !attached {
            m.buttons.push(Button::other(s::BTN_TRY_AGAIN, None, B_TRY));
        }
        m.buttons.push(Button::shell());
        if self.holt.is_some() {
            m.buttons.push(Button::other(s::BTN_REPORT, Some('r'), B_REPORT));
        }
        m.buttons.push(if self.opts.dry_run { Button::other(s::BTN_LEAVE, None, B_LEAVE) } else { Button::other(s::BTN_POWER_OFF, None, B_POWER) });
        m.chrome.keys = Some(o(s::FAILURE_KEYS));
        loop {
            match ui.message(&m) {
                Answer::Button(B_HELP) => {
                    if let Some(g) = self.stage_help(ui, &end, stopped) {
                        return g;
                    }
                }
                Answer::Button(B_TRY) if !attached => return self.try_again(ui),
                Answer::Button(B_REPORT) => {
                    if let Some(g) = self.restore_report(ui) {
                        return g;
                    }
                }
                Answer::Button(B_LEAVE) => return Go::Exit(1),
                Answer::Button(B_POWER) => {
                    let mut q = Question::new(Chrome::ours(s::SCR_FAILURE, s::POWER_OFF_TITLE), o(s::POWER_OFF_TEXT));
                    q.yes = o(s::BTN_POWER_OFF);
                    q.no = o(s::BTN_BACK);
                    q.no_first = true;
                    q.esc_no = true;
                    match ui.yes_no(&q) {
                        Answer::Ok(true) => {
                            if let Some(g) = self.power_off(ui) {
                                return g;
                            }
                        }
                        Answer::Ok(false) | Answer::Back | Answer::Button(_) => {}
                        a => return Go::of(a),
                    }
                }
                Answer::Back | Answer::Ok(()) | Answer::Button(_) => {}
                a => return Go::of(a),
            }
        }
    }

    /// Report, with the last run's events attached (RT10i).
    fn restore_report(&mut self, ui: &mut dyn Ui) -> Option<Go> {
        self.attach = self.restore.as_ref().and_then(|r| r.events.clone()).into_iter().collect();
        let g = self.report(ui);
        self.attach.clear();
        g
    }

    /// Try again (rescue-screens.md, 10b): `status --json`, then each stage
    /// from its `next` to `close`, `--only STAGE --commit`, under a new
    /// plan's hash (the plan may change after partition: holt-json.md,
    /// "What RT10j found").  `--erase` only when partition runs again.
    fn try_again(&mut self, ui: &mut dyn Ui) -> Go {
        let Some(l) = self.full_load(Stage::Status, false) else { return Go::Stay };
        let st = match self.status_run(ui, &l) {
            Ok(st) => st,
            Err(g) => return if g == Go::Back { Go::Stay } else { g },
        };
        let Some(next) = st.next.clone().filter(|n| STAGES.contains(&n.as_str())) else {
            if st.all_done {
                let mut r = Restore::new(Vec::new(), &STAGES.iter().map(|x| x.to_string()).collect::<Vec<_>>());
                r.end = Some(End::Done);
                self.restore = Some(r);
            }
            return Go::Stay;
        };
        let erase = next == "partition";
        let Some(mut pl) = self.full_load(Stage::Plan, erase) else { return Go::Stay };
        if let Some(rm) = self.restore.as_ref().and_then(|r| r.runs.last()) {
            pl.removable = rm.removable;
        }
        let p = match self.plan_run(ui, &pl, s::PLANNING_REVIEW) {
            Ok(p) => p,
            Err(g) => return if g == Go::Back { Go::Stay } else { g },
        };
        let from = STAGES.iter().position(|x| *x == next).unwrap_or(0);
        let runs: Vec<Load> = STAGES[from..].iter().map(|x| pl.run_of(Stage::Only(x.to_string()), &p.plan_sha256)).collect();
        let done: Vec<String> = STAGES[..from].iter().map(|x| x.to_string()).collect();
        self.plan = Some(p);
        let mut r = Restore::new(runs, &done);
        r.began = self.sys.now();
        self.restore = Some(r);
        Go::Stay
    }

    /// A stage's help (rescue-screens.md, "A stage's command failed"):
    /// what happened, why, what to do, the backup-is-safe line, a fix only
    /// where one is known, never the focus.  None: back to Failure.
    fn stage_help(&mut self, ui: &mut dyn Ui, end: &End, stopped: bool) -> Option<Go> {
        let host = self.restore_host();
        let failure: Option<Failure> = match end {
            End::Failed { failure, .. } => failure.as_deref().cloned(),
            _ => None,
        };
        let code = match end {
            End::Failed { code, .. } => code.clone(),
            _ => String::new(),
        };
        let program = failure.as_ref().and_then(|f| f.program.as_ref()).map(|p| p.as_str().to_string()).unwrap_or_default();
        let stage = failure.as_ref().map(|f| f.stage.as_str().to_string()).unwrap_or_default();
        let uefi = self.plan.as_ref().and_then(|p| p.efivars);
        let attached = self.restore.as_ref().is_some_and(|r| r.attached.is_some()) || self.reviewed.is_empty();
        enum Fix {
            NoEntry,
            Passphrase,
            Disks,
        }
        let (title, what, why, todo, fix, again): (String, String, String, String, Option<Fix>, bool) = if stopped {
            (s::STOPPED_TITLE.into(), s::STOPPED_WHAT.into(), s::STOPPED_WHY.into(), s::STOPPED_TODO.into(), None, true)
        } else if code == "efi_vars" || (code == "cmd_failed" && stage == "boot" && BOOT_PROGRAMS.contains(&program.as_str())) {
            let bios = code == "efi_vars" || uefi == Some(false);
            let (why, todo) = if bios { (s::BOOT_WHY_BIOS, s::BOOT_TODO_BIOS) } else { (s::BOOT_WHY_UEFI, s::BOOT_TODO_UEFI) };
            let prog = if program.is_empty() { "the boot loader".to_string() } else { program.clone() };
            (s::BOOT_TITLE.into(), s::boot_what(&prog, &host), why.into(), todo.into(), Some(Fix::NoEntry), false)
        } else {
            match code.as_str() {
                "wrong_passphrase" => (s::PASSPHRASE_TITLE_HELP.into(), s::PASSPHRASE_WHAT.into(), s::PASSPHRASE_WHY.into(), s::PASSPHRASE_TODO.into(), Some(Fix::Passphrase), false),
                "archiver_failed" => (s::COPY_TITLE.into(), s::COPY_WHAT.into(), s::COPY_WHY.into(), s::COPY_TODO.into(), None, true),
                "lock_held" => (s::LOCK_TITLE.into(), s::LOCK_WHAT.into(), s::LOCK_WHY.into(), s::LOCK_TODO.into(), None, true),
                "problems" => {
                    let what = match end {
                        End::Failed { what, .. } => what.clone(),
                        _ => String::new(),
                    };
                    (s::CHECKS_TITLE.into(), what, s::CHECKS_WHY.into(), s::CHECKS_TODO.into(), Some(Fix::Disks), false)
                }
                "readback_failed" | "not_done_after" | "write_failed" | "internal" | "commit_refused" => {
                    let what = failure.as_ref().map(|f| f.text.as_str().to_string()).unwrap_or_default();
                    (s::GENERAL_TITLE.into(), what, s::GENERAL_WHY.into(), s::REPORT_TODO.into(), None, false)
                }
                _ => {
                    let what = match end {
                        End::Failed { what, failure: Some(f), .. } if what.is_empty() => f.text.as_str().to_string(),
                        End::Failed { what, .. } => what.clone(),
                        _ => String::new(),
                    };
                    (s::GENERAL_TITLE.into(), what, s::GENERAL_WHY.into(), s::GENERAL_TODO.into(), None, true)
                }
            }
        };
        let text = format!("{}{what}\n\n{}{why}\n\n{}{todo}\n\n{}", s::WHAT_HAPPENED, s::WHY, s::TODO, s::BACKUP_SAFE);
        let mut m = Message::new(Chrome::new(o(s::SCR_FAILURE), Clean::line(&s::problem_title(&title), cap::LINE)), Clean::text(&text, cap::PROBLEM));
        m.buttons = Vec::new();
        let fix_label = match fix {
            Some(Fix::NoEntry) => Some(s::BTN_NO_ENTRY),
            Some(Fix::Passphrase) => Some(s::BTN_PASSPHRASE_AGAIN),
            Some(Fix::Disks) => Some(s::BTN_TO_DISKS),
            None => None,
        };
        if let Some(lb) = fix_label.filter(|_| !attached) {
            m.buttons.push(Button::other(lb, None, B_FIX));
        }
        let first = m.buttons.len();
        if again && !attached {
            m.buttons.push(Button::other(s::BTN_TRY_AGAIN, None, B_TRY));
        }
        m.buttons.push(Button::shell());
        if self.holt.is_some() {
            m.buttons.push(Button::other(s::BTN_REPORT, Some('r'), B_REPORT));
        }
        m.buttons.push(Button::other(s::BTN_RAW, Some('o'), B_RAW));
        // Esc is back, as the mock's key line says; its buttons fill the
        // row, so < Back > is drawn only in plain mode, as a choice.
        if ui.full_screen() {
            m.esc_back = true;
        } else {
            m.buttons.push(Button::back());
        }
        // The fix is offered, not chosen.
        m.focus = if again && !attached { first + 1 } else { first };
        m.chrome.keys = Some(o(s::HELP_KEYS));
        loop {
            match ui.message(&m) {
                Answer::Button(B_FIX) => match fix {
                    Some(Fix::NoEntry) => {
                        let mut q = Question::new(Chrome::ours(s::SCR_FAILURE, s::NO_ENTRY_TITLE), o(s::NO_ENTRY_TEXT));
                        q.yes = o(s::BTN_NO_ENTRY);
                        q.no = o(s::BTN_BACK);
                        q.no_first = true;
                        q.esc_no = true;
                        match ui.yes_no(&q) {
                            Answer::Ok(true) => return Some(self.no_entry(ui)),
                            Answer::Ok(false) | Answer::Back | Answer::Button(_) => {}
                            a => return Some(Go::of(a)),
                        }
                    }
                    Some(Fix::Passphrase) => {
                        self.restore = None;
                        self.reviewed.clear();
                        self.serials.clear();
                        self.resume = None;
                        return Some(Go::BackTo(Step::Passphrase(0)));
                    }
                    Some(Fix::Disks) => {
                        self.restore = None;
                        self.reviewed.clear();
                        self.serials.clear();
                        return Some(Go::BackTo(Step::Disks));
                    }
                    None => {}
                },
                Answer::Button(B_TRY) => return Some(self.try_again(ui)),
                Answer::Button(B_REPORT) => {
                    if let Some(g) = self.restore_report(ui) {
                        return Some(g);
                    }
                }
                Answer::Button(B_RAW) => {
                    if let Some(g) = self.failed_raw(ui, failure.as_ref()) {
                        return Some(g);
                    }
                }
                Answer::Back => return None,
                Answer::Ok(()) | Answer::Button(_) => {}
                a => return Some(Go::of(a)),
            }
        }
    }

    /// Install without an entry: the boot stage again with `--removable
    /// --no-nvram`, then close, under that plan's hash.
    fn no_entry(&mut self, ui: &mut dyn Ui) -> Go {
        let Some(mut pl) = self.full_load(Stage::Plan, false) else { return Go::Stay };
        pl.removable = true;
        let p = match self.plan_run(ui, &pl, s::PLANNING_REVIEW) {
            Ok(p) => p,
            Err(g) => return if g == Go::Back { Go::Stay } else { g },
        };
        let runs = vec![pl.run_of(Stage::Only("boot".into()), &p.plan_sha256), pl.run_of(Stage::Only("close".into()), &p.plan_sha256)];
        let done: Vec<String> = STAGES.iter().take_while(|x| **x != "boot").map(|x| x.to_string()).collect();
        self.plan = Some(p);
        let mut r = Restore::new(runs, &done);
        r.began = self.sys.now();
        self.restore = Some(r);
        Go::Stay
    }

    /// `o`: the command, its exit status, its last output, bko-holt's own
    /// stderr.
    fn failed_raw(&mut self, ui: &mut dyn Ui, f: Option<&Failure>) -> Option<Go> {
        let mut lines: Vec<Clean> = Vec::new();
        if let Some(f) = f {
            let argv: Vec<&str> = f.argv.iter().map(Clean::as_str).collect();
            if !argv.is_empty() {
                lines.push(Clean::line(&s::raw_command(&argv.join(" ")), cap::LOG_LINE));
            }
            lines.push(o(&s::raw_status(&s::status_words(f.status, f.signal))));
            lines.extend(Clean::text(f.text.as_str(), cap::PROBLEM).lines());
            if !f.tail.is_empty() {
                lines.push(o(s::RAW_TAIL));
                lines.extend(f.tail.iter().map(|l| Clean::line(&format!("  {}", l.as_str()), cap::LOG_LINE)));
            }
        }
        let stderr = self.restore.as_ref().map(|r| r.stderr.clone()).unwrap_or_default();
        if !stderr.is_empty() {
            lines.push(Clean::default());
            lines.push(o(s::RAW_STDERR));
            lines.extend(stderr);
        }
        let mut v = TextView::new(Chrome::ours(s::SCR_FAILURE, s::RAW_FAILED_TITLE), lines);
        v.buttons.push(Button::back());
        match ui.view(&v) {
            Answer::Ok(()) | Answer::Back => None,
            a => Some(Go::of(a)),
        }
    }

    // ---- An earlier bko-rescue's restore ----

    /// At start: run.json names a session whose load is still running
    /// (follow it), or one whose load is gone (its files wiped now).
    pub(crate) fn earlier_run(&mut self, ui: &mut dyn Ui) -> Option<Go> {
        let base = self.sys.run_base();
        let b = self.sys.read(&base.join(RUN_JSON), RUN_JSON_MAX)?;
        let v = std::str::from_utf8(&b).ok().and_then(|t| json::parse(t.trim()).ok());
        let field = |k: &str| v.as_ref().and_then(|v| v.get_str(k)).map(PathBuf::from);
        let session = field("session").filter(|d| d.parent() == Some(base.as_path()) && d.file_name().and_then(|n| n.to_str()).is_some_and(|n| n.starts_with("s-")));
        let (Some(dir), Some(events), Some(stderr)) = (session, field("events"), field("stderr")) else {
            let _ = std::fs::remove_file(base.join(RUN_JSON));
            return None;
        };
        if dir == self.session.dir || !events.starts_with(&dir) || !stderr.starts_with(&dir) {
            let _ = std::fs::remove_file(base.join(RUN_JSON));
            return None;
        }
        // Its pid: run.json's, else its events' start.
        let mut f = Follow::new();
        if let Some(e) = self.sys.read_tail(&events, EVENTS_PER_TICK) {
            f.feed(&e, 0);
        }
        let pid = v.as_ref().and_then(|v| v.get_u64("pid")).or(f.pid).and_then(|p| u32::try_from(p).ok());
        let alive = pid.is_some_and(|p| self.sys.alive(p)) && f.ended.is_none();
        if !alive {
            sys::wipe_dir(&dir);
            let _ = std::fs::remove_file(base.join(RUN_JSON));
            return None;
        }
        let host = v.as_ref().and_then(|v| v.get_str("host")).map_or_else(|| "this machine".to_string(), |h| Clean::line(h, cap::HOSTNAME).as_str().to_string());
        let mut m = Message::new(Chrome::ours(s::SCR_RESTORE, s::EARLIER_TITLE).first(), Clean::text(&s::earlier(&host, pid.unwrap_or(0)), cap::PROBLEM));
        m.buttons = vec![Button::other(s::BTN_FOLLOW, None, B_FOLLOW), Button::shell()];
        match ui.message(&m) {
            Answer::Button(B_FOLLOW) | Answer::Ok(()) => {}
            a => return Some(Go::of(a)),
        }
        let job = match self.sys.attach(&events, &stderr) {
            Ok(j) => j,
            Err(_) => return None,
        };
        let mut r = Restore::new(Vec::new(), &[]);
        r.began = self.sys.now();
        r.attached = Some(dir);
        r.events = Some(events.clone());
        r.run = Some(Run {
            job,
            follow: Follow::new(),
            buf: Vec::new(),
            t0: Instant::now(),
            drained: false,
            exit: None,
            events,
            expect: None,
            commit: true,
            dry_run: false,
            killed: None,
        });
        self.restore = Some(r);
        self.first = Step::Restore;
        Some(Go::Swap(Step::Restore))
    }
}

/// The progress screen's text: the stages, then what runs now; in plain
/// mode the line before them and the stages begun.
fn screen_text(r: &Restore, assemble: &[String], full: bool, host: &str, dry: bool) -> Clean {
    let rows = r.merged();
    if !full {
        let head = if dry { s::dry_plain(host) } else { s::restoring_plain(host) };
        let begun = rows_plain(&rows, assemble);
        let t = if begun.is_empty() { head } else { format!("{head}\n{}", begun.trim_end()) };
        return Clean::text(&t, cap::PROBLEM);
    }
    let mut t = rows_text(&rows, assemble);
    t.push('\n');
    let f = r.run.as_ref().map(|x| &x.follow);
    let now = match f {
        _ if r.stop_asked && f.is_some_and(|f| !f.stopping) => s::NOW_STOPPING.to_string(),
        Some(f) if f.error.is_some() => s::now_unreadable(f.error.as_ref().map_or("", Clean::as_str)),
        Some(f) if f.now.is_some() => s::now(f.now.as_ref().map_or("", Clean::as_str)),
        Some(f) if f.started => match rows.iter().find(|x| x.mark == Mark::Running) {
            Some(x) => s::now(&label(&x.name, assemble).to_lowercase()),
            None => s::NOW_BETWEEN.to_string(),
        },
        _ => s::NOW_STARTING.to_string(),
    };
    t.push_str(&now);
    Clean::text(&t, cap::PROBLEM)
}

/// A failed stage's error as Failure shows it: `grub-install failed (exit
/// status 1):` and bko-holt's lines under it, at most ERROR_LINES.
fn failure_text(what: &str, f: Option<&Failure>) -> String {
    let Some(f) = f else { return what.to_string() };
    let mut lines: Vec<String> = Vec::new();
    if let Some(p) = &f.program {
        lines.push(s::exit_status(p.as_str(), &s::status_words(f.status, f.signal)));
    }
    let body: Vec<&str> = f.text.as_str().lines().collect();
    // bko-holt's first line says what failed; Failure's own line says it
    // when the program is named.
    let skip = usize::from(f.program.as_ref().is_some_and(|p| body.first().is_some_and(|l| l.starts_with(&format!("{} failed", p.as_str())))));
    let rest: Vec<String> = body[skip..].iter().map(|l| format!("  {}", l.trim())).collect();
    let more = rest.len().saturating_sub(ERROR_LINES);
    lines.extend(rest.into_iter().take(ERROR_LINES));
    if more > 0 {
        lines.push(s::error_more(more));
    }
    lines.join("\n")
}

/// How a run ended, from its events and exit (holt-json.md, "Exit
/// statuses"): None when it is through and the next may start.  Whatever
/// isn't plainly done isn't (Principle 6).
fn judge(run: &Run, version: Option<&str>, stop_asked: bool) -> Option<End> {
    let f = &run.follow;
    let failed = |code: &str, what: String, failure: Option<Box<Failure>>| Some(End::Failed { stage: None, code: code.to_string(), what, failure });
    if let Some(why) = &run.killed {
        return failed("not_asked", format!("{}: {}.", s::NOT_ASKED, Clean::line(why, cap::LINE).as_str()), None);
    }
    if let Some(e) = &f.error {
        return failed("bad_events", format!("{}: {}.", s::BAD_EVENTS, e.as_str()), None);
    }
    let status = s::status_words(run.exit.flatten().map(i64::from), None);
    if let Some(r) = &f.refusal {
        return failed("refused", s::refused(r.text.as_str()), None);
    }
    if f.started {
        let commit_ok = if run.dry_run { f.not_dry().is_none() } else { f.commit == Some(run.commit) && f.dry_run == Some(false) };
        let version_ok = version.is_none_or(|v| f.version.as_deref() == Some(v));
        let hash_ok = run.expect.as_ref().is_none_or(|h| f.plan_sha256.as_ref() == Some(h));
        if !(commit_ok && version_ok && hash_ok) {
            return failed("not_asked", format!("{}.", s::NOT_ASKED), None);
        }
    }
    if f.ended == Some(5) || f.stopped == Some(true) {
        return Some(End::Stopped { stage: None });
    }
    if let Some(ps) = &f.problems {
        let text: Vec<String> = ps.iter().map(|(c, t)| format!("{} ({})", t.as_str(), c.as_str())).collect();
        return failed("problems", s::checks_stopped(&text.join("; ")), None);
    }
    if let Some(x) = &f.failed {
        let code = x.code.as_str().to_string();
        return Some(End::Failed { stage: Some(x.stage.as_str().to_string()), code, what: String::new(), failure: Some(Box::new(x.clone())) });
    }
    let exit_ok = run.exit.flatten().is_none_or(|c| c == 0) && run.exit.is_some();
    match f.ended {
        Some(0) if exit_ok && !f.odd_skip && f.unfinished().is_none() => None,
        Some(0) if exit_ok => failed("unclear", format!("{}.", s::UNCLEAR), None),
        // Stopped by us but ended otherwise: a stop that came too late.
        Some(e) if stop_asked && e == 0 => None,
        Some(e) => failed("exit", s::ended_with(&format!("bko-holt's end event says exit {e}"), &status), None),
        None => failed("no_end", s::ended_with(s::NO_END, &status), None),
    }
}

/// The restore logs in `dir` beyond the newest `keep`, by name (a UTC
/// timestamp), removed.
pub(crate) fn prune(dir: &Path, keep: usize) {
    let Ok(rd) = std::fs::read_dir(dir) else { return };
    let mut names: Vec<String> = rd
        .flatten()
        .filter_map(|e| e.file_name().to_str().map(str::to_string))
        .filter(|n| {
            let b = n.as_bytes();
            let ts = b.len() >= 16 && b[..8].iter().all(u8::is_ascii_digit) && b[8] == b'T' && b[9..15].iter().all(u8::is_ascii_digit) && b[15] == b'Z';
            ts && (n.ends_with(".log.gz") || n.ends_with(".log")) && n.len() <= 23
        })
        .collect();
    names.sort();
    let n = names.len().saturating_sub(keep);
    for x in &names[..n] {
        let _ = std::fs::remove_file(dir.join(x));
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn durations_and_labels() {
        assert_eq!(dur(2_100), "2 s");
        assert_eq!(dur(21 * 60_000 + 5), "21 min");
        assert_eq!(dur(3 * 3_600_000 + 5 * 60_000), "3 h 5 min");
        assert_eq!(label("assemble", &["cryptsetup".into()]), "Set up encryption");
        assert_eq!(label("assemble", &["mdadm".into(), "pvcreate".into()]), "Set up RAID and volumes");
        assert_eq!(label("assemble", &["mdadm".into(), "cryptsetup".into()]), "Set up RAID and encryption");
        assert_eq!(label("restore", &[]), "Copy your files back");
    }

    #[test]
    fn the_lock_and_paths() {
        assert_eq!(read_lock(br#"{"pid": 4711, "start": "123456"}"#), Some((4711, Some("123456".into()))));
        assert_eq!(read_lock(br#"{"pid": 4711, "start": null}"#), Some((4711, None)));
        assert_eq!(read_lock(b"not json"), None);
        assert_eq!(read_lock(br#"{"pid": -1}"#), None);
        assert!(plain_path("/run/bko-holt/log/load.log") && !plain_path("/run/../etc/shadow") && !plain_path("log"));
        assert!(plain_name("0b7e3c52-8d1f-4a6e-9c3b-2f5d7a1e4b90") && !plain_name("../../etc") && !plain_name("..") && !plain_name(""));
    }

    #[test]
    fn two_a_row_with_the_time() {
        let rows: Vec<Row> = STAGES.iter().map(|n| Row { name: n.to_string(), mark: Mark::Done, took_ms: None, would: 0 }).collect();
        let t = rows_two(&rows, &["cryptsetup".into()], Some("36 min"));
        let l: Vec<&str> = t.lines().collect();
        assert_eq!(l[0], "  [x] Make partitions           [x] Set up encryption");
        assert_eq!(l[3], format!("  [x] Finish{}in 36 min", " ".repeat(51 - 12)));
    }

    #[test]
    fn only_the_newest_logs_are_kept() {
        let d = std::env::temp_dir().join(format!("bko-rescue-prune-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&d);
        std::fs::create_dir_all(&d).unwrap();
        for i in 0..13 {
            std::fs::write(d.join(format!("202611{:02}T120000Z.log.gz", i + 1)), b"x").unwrap();
        }
        std::fs::write(d.join("notes.txt"), b"x").unwrap();
        prune(&d, 10);
        let mut left: Vec<String> = std::fs::read_dir(&d).unwrap().flatten().map(|e| e.file_name().to_string_lossy().to_string()).collect();
        left.sort();
        assert_eq!(left.len(), 11);
        assert_eq!(left[0], "20261104T120000Z.log.gz");
        assert!(left.contains(&"notes.txt".to_string()), "not a log: left alone");
        let _ = std::fs::remove_dir_all(&d);
    }
}
