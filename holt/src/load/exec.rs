//! Running the writing stages: the checks `plan` makes, then each stage's
//! commands, then a read-back of the disks to see the stage took.
//!
//! Without --commit a stage prints its commands and writes nothing.  Every
//! command and its output goes to `RUN_DIR/log/load.log`, which `boot` copies
//! into the restored system's /var/log/bko-holt/.
//!
//! Under --json (docs/design/holt-json.md, "bko-holt-events"; `RT10f`,
//! `RT10g`) the same run writes its events as they happen: each command's
//! output is read as it comes (`run_cmd_json`), its standard input is
//! /dev/null when the step gives none, it runs in a process group of its
//! own, the archiver's status lines become `progress` and its `[WARN]`
//! lines `warning`; SIGTERM stops the run between commands (the archiver's
//! group gets SIGINT), and a --commit run holds `RUN_DIR/load.lock`.  The
//! text the run would print is built all the same (the tests read it), but
//! only standard error is printed.

use super::codes::FailureCode;
use super::commands::Cmd;
use super::jsonout::{self, FailFacts};
use super::layout::Layout;
use super::status::{self, State};
use super::{
    EXIT_FAILED, EXIT_OK, EXIT_PLAN_PROBLEMS, EXIT_STOPPED, Options, RUN_DIR, Stage, plan,
};
use crate::json::{Obj, Value};
use crate::sys::Sys;
use std::io::{BufRead, Write};
use std::process::{Command, Stdio};
use std::time::{Duration, Instant};

/// TEXT into the log, a password= item's value as <password>
/// (`crate::redact::scrub_passwords`: LILO's copy of lilo.conf), whatever the
/// mode: the log goes to the restored /var/log/bko-holt and into
/// report-bug's bundle (`RustAudit1` HD1-01).
fn log_line(log: &mut Option<std::fs::File>, text: &str) {
    if let Some(f) = log {
        let text = crate::redact::scrub_passwords(text);
        let _ = f.write_all(text.as_bytes());
        if !text.ends_with('\n') {
            let _ = f.write_all(b"\n");
        }
    }
}

/// Runs one step for real.  A command's output goes to the log (and, for
/// the restore's archiver, the terminal sees nothing until it's done).  It
/// gives back, for a command that `warns`, its stderr's `warning:` lines,
/// for the terminal.  A read-back runs here, with SYS's probe.
fn run_cmd(c: &Cmd, log: &mut Option<std::fs::File>, sys: &Sys) -> Result<Vec<String>, String> {
    log_line(log, &c.shell());
    if c.is_note() {
        return Ok(Vec::new());
    }
    if let Some((dev, content)) = &c.check {
        super::readback::check(sys, dev, content)?;
        log_line(log, &format!("{dev}: read back as the source's"));
        return Ok(Vec::new());
    }
    if let Some(path) = &c.write {
        return write_file(path, c.stdin.as_deref().unwrap_or(""), c.private).map(|()| Vec::new());
    }
    let mut cmd = Command::new(&c.argv[0]);
    cmd.args(&c.argv[1..])
        .env("LC_ALL", "C")
        .envs(c.env.iter().map(|(k, v)| (k, v)))
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());
    // No stdin given: the terminal's, so cryptsetup can ask for a passphrase.
    cmd.stdin(if c.stdin.is_some() {
        Stdio::piped()
    } else {
        Stdio::inherit()
    });
    let mut child = cmd.spawn().map_err(|e| format!("{}: {e}", c.argv[0]))?;
    let feeder = feed(c, &mut child);
    let out = child
        .wait_with_output()
        .map_err(|e| format!("{}: {e}", c.argv[0]))?;
    let unfed = fed_error(feeder);
    log_line(log, &String::from_utf8_lossy(&out.stdout));
    log_line(log, &String::from_utf8_lossy(&out.stderr));
    if out.status.success() {
        if let Some(e) = unfed {
            return Err(format!("{}: writing its input: {e}", c.argv[0]));
        }
        let err = String::from_utf8_lossy(&out.stderr);
        Ok(if c.warns {
            err.lines()
                .filter(|l| l.contains("warning:"))
                .map(str::to_string)
                .collect()
        } else {
            Vec::new()
        })
    } else {
        let err = String::from_utf8_lossy(&out.stderr);
        let tail: Vec<&str> = err
            .lines()
            .rev()
            .take(5)
            .collect::<Vec<_>>()
            .into_iter()
            .rev()
            .collect();
        Err(format!(
            "{} failed ({}){}",
            c.argv[0],
            out.status,
            if tail.is_empty() {
                String::new()
            } else {
                format!(":\n    {}", tail.join("\n    "))
            }
        ))
    }
}

/// How a step failed: the text text mode prints after `bko-holt load:
/// STAGE: `, what the `failed` event adds, and whether it was the stop's
/// doing (the archiver's group sent SIGINT).
#[derive(Debug, Default, Clone, PartialEq)]
pub(crate) struct StepFail {
    pub text: String,
    pub facts: FailFacts,
    pub stopped: bool,
}

impl StepFail {
    fn of_text(text: String) -> StepFail {
        StepFail {
            text,
            ..StepFail::default()
        }
    }
}

/// Where a step is, for its events: its stage, its number in the stage,
/// and for one that runs the archiver, which of the stage's it is.
#[derive(Debug, Clone, Copy)]
pub(crate) struct At {
    pub stage: Stage,
    pub n: usize,
    pub part: Option<(usize, usize)>,
}

/// A run's events (none in text mode, nor when a test reads the text
/// alone), and whether it has been asked to stop.
pub struct Events<'a> {
    sink: Option<&'a mut dyn FnMut(&str)>,
    stop: &'a dyn Fn() -> bool,
    stopping: bool,
    /// The run's lock, let go just before `end`, so a reader acting on
    /// `end` finds it gone.
    lock: Option<Lock>,
}

impl<'a> Events<'a> {
    /// No events: STOP alone says when to stop.
    pub fn none(stop: &'a dyn Fn() -> bool) -> Events<'a> {
        Events {
            sink: None,
            stop,
            stopping: false,
            lock: None,
        }
    }

    /// Events to SINK, a line each; STOP says when to stop.
    pub fn to(sink: &'a mut dyn FnMut(&str), stop: &'a dyn Fn() -> bool) -> Events<'a> {
        Events {
            sink: Some(sink),
            stop,
            stopping: false,
            lock: None,
        }
    }

    fn emit(&mut self, v: Value) {
        if let Some(s) = self.sink.as_mut() {
            s(&jsonout::event_line(v));
        }
    }

    /// Whether a stop has been asked for: the first time it is seen, the
    /// `stopping` event.
    fn stop_asked(&mut self) -> bool {
        if !self.stopping && (self.stop)() {
            self.stopping = true;
            self.emit(jsonout::event("stopping").set("signal", "SIGTERM").build());
        }
        self.stopping
    }

    /// The `end` event, the lock let go first, and the panic hook told the
    /// stream is complete.
    fn end(&mut self, exit: u8, stopped: bool, t0: Instant) {
        self.lock = None;
        self.emit(jsonout::end_event(
            exit,
            stopped,
            jsonout::secs(t0.elapsed()),
        ));
        hook::note(|h| h.ended = true);
    }

    /// A `failed` event F, then `end` with exit 1.
    fn fail(&mut self, f: Obj, t0: Instant) {
        self.emit(f.build());
        self.end(EXIT_FAILED, false, t0);
    }
}

/// What runs one step under `run_core`: `run_cmd_json`, `run_cmd`, or a test's
/// stand-in.
type Step<'a> = &'a dyn Fn(
    &Cmd,
    &mut Option<std::fs::File>,
    &Sys,
    &mut Events<'_>,
    &At,
) -> Result<Vec<String>, StepFail>;

/// A step's log line: `shell()`'s, but under --json a private write's text
/// as its size and SHA-256 only (holt-json.md, "What differs under
/// --json" 7).
fn logged_shell(c: &Cmd) -> String {
    match (&c.write, &c.stdin) {
        (Some(_), Some(text)) if c.private => Cmd {
            stdin: Some(format!(
                "# ({} bytes, sha256 {}, private: not shown)\n",
                text.len(),
                crate::bytes::sha256_hex(text.as_bytes())
            )),
            ..c.clone()
        }
        .shell(),
        _ => c.shell(),
    }
}

/// One line of a child's output, from its standard output (false) or
/// error (true); None at the end of that stream.
type Piece = (bool, Option<Vec<u8>>);

/// Reads R a line at a time into TX, then says it ended.
fn reader<R: std::io::Read + Send + 'static>(
    r: R,
    is_err: bool,
    tx: std::sync::mpsc::Sender<Piece>,
) -> std::thread::JoinHandle<()> {
    std::thread::spawn(move || {
        let mut r = std::io::BufReader::new(r);
        loop {
            let mut buf = Vec::new();
            match r.read_until(b'\n', &mut buf) {
                Ok(0) | Err(_) => break,
                Ok(_) => {
                    if tx.send((is_err, Some(buf))).is_err() {
                        return;
                    }
                }
            }
        }
        let _ = tx.send((is_err, None));
    })
}

/// C's input written to CHILD on a thread of its own, beside the reading:
/// a child that writes much before it reads can't stall the two, and one
/// that exits before reading it all still has its exit and output seen.
fn feed(
    c: &Cmd,
    child: &mut std::process::Child,
) -> Option<std::thread::JoinHandle<std::io::Result<()>>> {
    let (Some(input), Some(mut pipe)) = (c.stdin.clone(), child.stdin.take()) else {
        return None;
    };
    Some(std::thread::spawn(move || pipe.write_all(input.as_bytes())))
}

/// How FEEDER's writing failed, if it did.
fn fed_error(
    feeder: Option<std::thread::JoinHandle<std::io::Result<()>>>,
) -> Option<std::io::Error> {
    feeder.and_then(|f| {
        f.join()
            .unwrap_or_else(|_| Err(std::io::Error::other("its writer panicked")))
            .err()
    })
}

/// `run_cmd` under --json: the same command, the same log (but the
/// archiver's progress lines, of which the last is kept, and a private
/// write's text), the same result; its standard input /dev/null when the
/// step gives none, in a process group of its own with SIGINT at its
/// default, its output read as it comes: the archiver's status lines as
/// `progress` (at most one a second, and the last), its `[WARN]` lines as
/// `warning`.  When a stop is asked for while it runs the archiver, its
/// group gets SIGINT, and the step is stopped whatever its exit.
fn run_cmd_json(
    c: &Cmd,
    log: &mut Option<std::fs::File>,
    sys: &Sys,
    ev: &mut Events<'_>,
    at: &At,
) -> Result<Vec<String>, StepFail> {
    use std::os::unix::process::{CommandExt, ExitStatusExt};
    log_line(log, &logged_shell(c));
    if c.is_note() {
        return Ok(Vec::new());
    }
    if let Some((dev, content)) = &c.check {
        super::readback::check(sys, dev, content).map_err(StepFail::of_text)?;
        log_line(log, &format!("{dev}: read back as the source's"));
        return Ok(Vec::new());
    }
    if let Some(path) = &c.write {
        return write_file(path, c.stdin.as_deref().unwrap_or(""), c.private)
            .map(|()| Vec::new())
            .map_err(StepFail::of_text);
    }
    let prog = &c.argv[0];
    let mut cmd = Command::new(prog);
    cmd.args(&c.argv[1..])
        .env("LC_ALL", "C")
        .envs(c.env.iter().map(|(k, v)| (k, v)))
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());
    // No prompt can be answered: nothing reads a terminal's input.
    cmd.stdin(if c.stdin.is_some() {
        Stdio::piped()
    } else {
        Stdio::null()
    });
    // A group of its own (its pgid its pid), so the stop's SIGINT reaches
    // the archiver inside an `sh -c` too (J7).
    cmd.process_group(0);
    // SAFETY: the closure runs in the child between fork and exec and only
    // calls signal(2), which is async-signal-safe; it touches no memory of
    // the parent's and takes no lock.
    unsafe {
        cmd.pre_exec(|| {
            crate::sys::sigint_to_default();
            Ok(())
        });
    }
    let mut child = cmd
        .spawn()
        .map_err(|e| StepFail::of_text(format!("{prog}: {e}")))?;
    let pgid = i32::try_from(child.id()).unwrap_or(0);
    hook::note(|h| h.child = (pgid > 0).then_some(pgid));
    let feeder = feed(c, &mut child);
    let (tx, rx) = std::sync::mpsc::channel::<Piece>();
    let mut readers = Vec::new();
    if let Some(o) = child.stdout.take() {
        readers.push(reader(o, false, tx.clone()));
    }
    if let Some(e) = child.stderr.take() {
        readers.push(reader(e, true, tx.clone()));
    }
    drop(tx);
    let stage = at.stage.name();
    let mut open = readers.len();
    // Standard output's lines in order, each marked when it is one of the
    // archiver's status lines; standard error as it came.
    let mut stdout: Vec<(Vec<u8>, bool)> = Vec::new();
    let mut stderr: Vec<u8> = Vec::new();
    let mut warnings = 0usize;
    // The newest status not yet sent, and when the last was.
    let mut pending: Option<(Option<u64>, Option<u64>, Option<u64>)> = None;
    let mut sent: Option<Instant> = None;
    let mut signalled = false;
    let t0 = Instant::now();
    let (part, parts) = at.part.unwrap_or((1, 1));
    let progress = |ev: &mut Events<'_>,
                    (bytes, total, secs): (Option<u64>, Option<u64>, Option<u64>),
                    errors: usize| {
        ev.emit(
            jsonout::event("progress")
                .set("stage", stage)
                .set("n", at.n)
                .set("part", part)
                .set("parts", parts)
                .set("bytes", bytes)
                .set("total_bytes", total)
                .set("secs", secs.unwrap_or_else(|| t0.elapsed().as_secs()))
                .set("errors", errors)
                .build(),
        );
    };
    while open > 0 {
        match rx.recv_timeout(Duration::from_millis(100)) {
            Ok((false, Some(l))) => {
                let st = if c.archiver {
                    jsonout::archiver_status(&String::from_utf8_lossy(&l))
                } else {
                    None
                };
                if st.is_some() {
                    pending = st;
                    // Only the last status line is logged: an earlier one's
                    // text needn't be kept (a long copy writes one a second).
                    if let Some(prev) = stdout.iter_mut().rev().find(|(_, s)| *s) {
                        prev.0 = Vec::new();
                    }
                }
                stdout.push((l, st.is_some()));
            }
            Ok((true, Some(l))) => {
                if c.archiver {
                    if let Some((text, item)) =
                        jsonout::archiver_warning(&String::from_utf8_lossy(&l))
                    {
                        warnings += 1;
                        ev.emit(
                            jsonout::event("warning")
                                .set("stage", stage)
                                .set("n", at.n)
                                .set("source", "archiver")
                                .set("text", jsonout::clipped(&text, 4096))
                                .set("item", item)
                                .build(),
                        );
                    }
                }
                stderr.extend_from_slice(&l);
            }
            Ok((_, None)) => open -= 1,
            Err(std::sync::mpsc::RecvTimeoutError::Timeout) => {}
            Err(std::sync::mpsc::RecvTimeoutError::Disconnected) => break,
        }
        if let Some(p) = pending {
            if sent.is_none_or(|t| t.elapsed() >= Duration::from_secs(1)) {
                progress(ev, p, warnings);
                pending = None;
                sent = Some(Instant::now());
            }
        }
        if ev.stop_asked() && c.archiver && !signalled && pgid > 0 {
            signalled = crate::sys::send_signal(-pgid, crate::sys::SIGINT);
            log_line(
                log,
                &format!("STOPPING: SIGINT to the archiver's process group {pgid}"),
            );
        }
    }
    for r in readers {
        let _ = r.join();
    }
    let waited = child.wait();
    hook::note(|h| h.child = None);
    let status = waited.map_err(|e| StepFail::of_text(format!("{prog}: {e}")))?;
    let unfed = fed_error(feeder);
    // The last status always.
    if let Some(p) = pending {
        progress(ev, p, warnings);
    }
    let last = stdout.iter().rposition(|(_, st)| *st);
    let mut kept = Vec::new();
    for (i, (l, st)) in stdout.iter().enumerate() {
        if !st || Some(i) == last {
            kept.extend_from_slice(l);
        }
    }
    log_line(log, &String::from_utf8_lossy(&kept));
    log_line(log, &String::from_utf8_lossy(&stderr));
    let err = String::from_utf8_lossy(&stderr);
    let tail: Vec<&str> = err
        .lines()
        .rev()
        .take(5)
        .collect::<Vec<_>>()
        .into_iter()
        .rev()
        .collect();
    let facts = FailFacts {
        status: status.code(),
        signal: status.signal(),
        tail: tail.iter().map(|l| l.to_string()).collect(),
    };
    if signalled {
        return Err(StepFail {
            text: format!("{prog} stopped on request ({status})"),
            facts,
            stopped: true,
        });
    }
    if status.success() {
        // A failed write matters only when the exit doesn't say more.
        if let Some(e) = unfed {
            return Err(StepFail {
                text: format!("{prog}: writing its input: {e}"),
                facts,
                stopped: false,
            });
        }
        Ok(if c.warns {
            err.lines()
                .filter(|l| l.contains("warning:"))
                .map(str::to_string)
                .collect()
        } else {
            Vec::new()
        })
    } else {
        let text = format!(
            "{prog} failed ({status}){}",
            if tail.is_empty() {
                String::new()
            } else {
                format!(":\n    {}", tail.join("\n    "))
            }
        );
        Err(StepFail {
            text,
            facts,
            stopped: false,
        })
    }
}

/// The failure code of step C in stage S, failed as F.
fn failure_code(c: &Cmd, s: Stage, f: &StepFail, sys: &Sys) -> FailureCode {
    if c.check.is_some() {
        return FailureCode::ReadbackFailed;
    }
    if c.write.is_some() {
        return FailureCode::WriteFailed;
    }
    // The archiver's own failure, which running it again may cure; but not
    // the check that it has the restore options a layout needs (its
    // `restore --help`), which only a newer archiver passes.
    if c.archiver && !asks_restore_help(c) {
        return FailureCode::ArchiverFailed;
    }
    let prog = jsonout::program_of(c).unwrap_or_default();
    // cryptsetup's exit 2 is "no key available with this passphrase": in
    // assemble, only a kept header's open can meet it (every other
    // container is opened with the key it was just made with).
    if prog == "cryptsetup"
        && s == Stage::Assemble
        && c.argv.iter().any(|a| a == "open" || a == "luksOpen")
        && f.facts.status == Some(2)
    {
        return FailureCode::WrongPassphrase;
    }
    // A step that writes UEFI variables on a machine that can't.
    if writes_efi_vars(c) && jsonout::efivars_here(sys) == Some(false) {
        return FailureCode::EfiVars;
    }
    FailureCode::CmdFailed
}

/// Whether step C writes UEFI variables: efibootmgr, or a grub-install
/// (grub2-install) for an EFI target (`--target=*-efi`) without
/// --no-nvram.  A BIOS grub-install, and an EFI one with --no-nvram (as
/// every one load builds), write none: their failure is `cmd_failed`'s.  The
/// program may stand anywhere in argv (after `chroot DIR`, or as the
/// arguments of `GRUB_BIOS_NILFS2`'s wrapper); a program named only inside
/// an `sh -c` script isn't looked for (no step load builds has one).
fn writes_efi_vars(c: &Cmd) -> bool {
    let named = |progs: &[&str]| {
        c.argv
            .iter()
            .any(|a| progs.contains(&a.rsplit('/').next().unwrap_or(a)))
    };
    if named(&["efibootmgr"]) {
        return true;
    }
    let efi_target = c.argv.iter().enumerate().any(|(i, a)| {
        let t = match a.strip_prefix("--target=") {
            Some(t) => Some(t),
            None if a == "--target" => c.argv.get(i + 1).map(String::as_str),
            None => None,
        };
        t.is_some_and(|t| t.ends_with("-efi"))
    });
    named(&["grub-install", "grub2-install"])
        && efi_target
        && !c.argv.iter().any(|a| a == "--no-nvram")
}

/// Whether step C is the check of the archiver's restore options (its
/// `restore --help`, commands.rs's for btrfs's snapshots and aliases).
fn asks_restore_help(c: &Cmd) -> bool {
    c.argv.iter().any(|a| a.contains(" restore --help > "))
}

fn state_of(layout: &Layout, opts: &Options, sys: &Sys, s: Stage) -> (State, String) {
    let (rows, _) = status::rows(layout, opts, sys);
    rows.into_iter()
        .find(|r| r.0 == s)
        .map(|r| (r.1, r.2))
        .unwrap_or((State::Unknown, String::new()))
}

/// What runs one step: `run_cmd`, or a test's stand-in.
type Runner<'a> = &'a dyn Fn(&Cmd, &mut Option<std::fs::File>, &Sys) -> Result<Vec<String>, String>;

/// The writing stages STAGES, as OPTS asks for them: the exit status, the
/// text standard output would print, and standard error.  Under --json each
/// command runs as `run_cmd_json` runs it, but no event is written (exec's
/// own tests read the text).
pub fn run(layout: &Layout, opts: &Options, sys: &Sys, stages: &[Stage]) -> (u8, String, String) {
    if opts.json {
        let never = || false;
        return run_core(
            layout,
            opts,
            sys,
            stages,
            &run_cmd_json,
            &mut Events::none(&never),
        );
    }
    run_with(layout, opts, sys, stages, &run_cmd)
}

/// `load ... --json` with a writing stage (holt-json.md, "bko-holt-events"):
/// run, each event written to SINK as it happens, stopped between commands
/// once SIGTERM has come (`crate::sys::catch_sigterm`).  Standard output is
/// the events alone: the text is dropped.
pub fn json_run(
    layout: &Layout,
    opts: &Options,
    sys: &Sys,
    stages: &[Stage],
    sink: &mut dyn FnMut(&str),
) -> (u8, String, String) {
    let stop = crate::sys::sigterm_taken;
    let mut ev = Events::to(sink, &stop);
    let (code, _, err) = run_core(layout, opts, sys, stages, &run_cmd_json, &mut ev);
    (code, String::new(), err)
}

/// run, each step run by STEP.  `RUN_DIR`'s own files (the log, boot.done,
/// the copy marks, the partition records) are SYS's: with --commit SYS is
/// the host's (--sysroot is refused), so they are `RUN_DIR`'s; in a test,
/// the test's tree.
fn run_with(
    layout: &Layout,
    opts: &Options,
    sys: &Sys,
    stages: &[Stage],
    step: Runner<'_>,
) -> (u8, String, String) {
    let never = || false;
    run_core(
        layout,
        opts,
        sys,
        stages,
        &|c, log, sys, _, _| step(c, log, sys).map_err(StepFail::of_text),
        &mut Events::none(&never),
    )
}

/// The writing run, each step run by STEP, its events to EV.
fn run_core(
    layout: &Layout,
    opts: &Options,
    sys: &Sys,
    stages: &[Stage],
    step: Step<'_>,
    ev: &mut Events<'_>,
) -> (u8, String, String) {
    let t0 = Instant::now();
    let mut out = String::new();
    let mut err = String::new();
    let commit_ok = opts.sysroot.is_none() && sys.privileged;
    // For the panic hook's own `start`, should a panic come before ours.
    hook::note(|h| {
        h.commit = opts.commit;
        h.layout = Some((
            layout.str_at(&["host", "hostname"]).map(str::to_string),
            layout.str_at(&["generated"]).map(str::to_string),
        ));
    });
    // Text mode takes no lock, but a --commit refuses while a --json load
    // holds one (J8): the shell is one key away from a detached load.
    if !opts.json && opts.commit {
        if let Some(holder) = lock_holder(sys) {
            return (
                EXIT_FAILED,
                out,
                format!("bko-holt load: {}\n", lock_held_text(holder)),
            );
        }
    }
    // Under --json a --commit run takes the lock before its checks, so
    // they read disks no other load is writing.
    let lock = (opts.json && opts.commit && commit_ok).then(|| Lock::take(sys));
    let p = plan::check(layout, opts, sys);
    let counts: Vec<(&str, usize)> = stages
        .iter()
        .map(|s| (s.name(), p.built.stage(*s).len()))
        .collect();
    ev.emit(jsonout::start_event(
        layout,
        opts,
        &counts,
        p.json.as_ref().map(|f| f.plan_sha256.as_str()),
    ));
    hook::note(|h| h.started = true);
    let log_path = opts.commit.then(|| format!("{RUN_DIR}/log/load.log"));
    let log_at = log_path.as_deref();
    match lock {
        Some(Ok(l)) => ev.lock = Some(l),
        Some(Err(LockErr::Held(holder))) => {
            let text = lock_held_text(holder);
            ev.fail(
                jsonout::failed_event(
                    None,
                    None,
                    FailureCode::LockHeld,
                    None,
                    &FailFacts::default(),
                    &text,
                    None,
                )
                .set("pid", holder),
                t0,
            );
            return (EXIT_FAILED, out, format!("bko-holt load: {text}\n"));
        }
        Some(Err(LockErr::Io(text))) => {
            ev.fail(
                jsonout::failed_event(
                    None,
                    None,
                    FailureCode::WriteFailed,
                    None,
                    &FailFacts::default(),
                    &text,
                    None,
                ),
                t0,
            );
            return (EXIT_FAILED, out, format!("bko-holt load: {text}\n"));
        }
        None => {}
    }
    if !p.problems.is_empty() {
        for x in &p.problems {
            err += &format!("bko-holt load: {x}\n");
        }
        err += "bko-holt load: nothing was written; `bko-holt load LAYOUT ...` without a stage shows the whole plan\n";
        ev.emit(
            jsonout::event("problems")
                .set("problems", p.problems.to_json())
                .set("exit", EXIT_PLAN_PROBLEMS)
                .build(),
        );
        ev.end(EXIT_PLAN_PROBLEMS, false, t0);
        return (EXIT_PLAN_PROBLEMS, out, err);
    }
    if opts.commit && !commit_ok {
        let why = if opts.sysroot.is_some() {
            "with --sysroot"
        } else {
            "except as root"
        };
        let text = format!("--commit can't write {why}");
        ev.fail(
            jsonout::failed_event(
                None,
                None,
                FailureCode::CommitRefused,
                None,
                &FailFacts::default(),
                &text,
                None,
            ),
            t0,
        );
        return (EXIT_FAILED, out, format!("bko-holt load: {text}\n"));
    }
    let mut log = None;
    if opts.commit {
        use std::os::unix::fs::{OpenOptionsExt, PermissionsExt};
        let _ = mkdir_private(&sys.p(RUN_DIR));
        let _ = mkdir_private(&sys.p(&format!("{RUN_DIR}/log")));
        let log_path = sys.p(&format!("{RUN_DIR}/log/load.log"));
        log = std::fs::OpenOptions::new()
            .create(true)
            .append(true)
            .mode(0o600)
            .open(&log_path)
            .ok();
        let _ = std::fs::set_permissions(&log_path, std::fs::Permissions::from_mode(0o600));
        log_line(
            &mut log,
            &format!(
                "==== bko-holt load {} {:?}",
                env!("CARGO_PKG_VERSION"),
                std::env::args().collect::<Vec<_>>()
            ),
        );
    }
    // A stop asked for (J7): no later command or stage runs, close
    // included; `end` says so, exit 5.
    let stopped = |ev: &mut Events<'_>,
                   log: &mut Option<std::fs::File>,
                   out: String,
                   mut err: String| {
        log_line(log, "STOPPED: on request (SIGTERM); nothing after this ran");
        err += &format!(
            "bko-holt load: stopped on request (SIGTERM): nothing after the command then running ran; the log is {RUN_DIR}/log/load.log\n"
        );
        ev.end(EXIT_STOPPED, true, t0);
        (EXIT_STOPPED, out, err)
    };
    // Once the partition stage runs on disks named with --erase, no later
    // stage is skipped as done: what status would read before them is what
    // was on the disks before the wipe (in a dry run, the old system's
    // filesystems and files, still there).  A stage the layout needs
    // nothing from is still skipped, and without --erase nothing changes.
    let mut wiped = false;
    // The copy marks' text (status::restore_mark_text), and the options
    // the restore's read-back reads the tree with.
    let mark = status::restore_mark_text(layout, &p.mapping);
    let mut tree_opts = opts.clone();
    tree_opts.json = false;
    for &s in stages {
        if ev.stop_asked() {
            return stopped(ev, &mut log, out, err);
        }
        // --through skips what's done already; --only does what it's told.
        if opts.through.is_some() {
            let (st, why) = state_of(layout, opts, sys, s);
            if st == State::Nothing || (st == State::Done && !wiped) {
                out += &format!("# ---- {}: {} already\n", s.name(), st_word(st));
                ev.emit(
                    jsonout::event("skip")
                        .set("stage", s.name())
                        .set(
                            "state",
                            if st == State::Nothing {
                                "nothing"
                            } else {
                                "done"
                            },
                        )
                        .set("why", why)
                        .build(),
                );
                continue;
            }
        }
        let marker = crate::other::restored_marker(&layout.v);
        // A dry run under --json goes through boot and close as it does the
        // other stages, printing what they would run (J11).
        let dry_json = opts.json && !opts.commit;
        if s == Stage::Boot && !dry_json && !sys.exists(&format!("{}/{marker}", opts.target)) {
            let text = format!(
                "{}/{marker} isn't there; restore the system into {} first",
                opts.target, opts.target
            );
            ev.fail(
                jsonout::failed_event(
                    Some(s.name()),
                    None,
                    FailureCode::NoRestoredTree,
                    None,
                    &FailFacts::default(),
                    &text,
                    log_at,
                ),
                t0,
            );
            return (EXIT_FAILED, out, format!("bko-holt load: boot: {text}\n"));
        }
        wiped |= s == Stage::Partition && !opts.erase.is_empty();
        let cmds = p.built.stage(s);
        out += &format!("# ---- {} ----\n", s.name());
        ev.emit(
            jsonout::event("stage")
                .set("stage", s.name())
                .set("commands", cmds.len())
                .build(),
        );
        hook::note(|h| {
            h.stage = Some(s.name());
            h.n = None;
        });
        let stage_t0 = Instant::now();
        let parts = cmds.iter().filter(|c| c.archiver).count();
        let mut part = 0;
        let mut part_of = |c: &Cmd| {
            c.archiver.then(|| {
                part += 1;
                (part, parts)
            })
        };
        if !opts.commit {
            for (i, c) in cmds.iter().enumerate() {
                if ev.stop_asked() {
                    return stopped(ev, &mut log, out, err);
                }
                out += &c.shell();
                ev.emit(jsonout::cmd_event(
                    s.name(),
                    i + 1,
                    cmds.len(),
                    c,
                    true,
                    part_of(c),
                ));
            }
            ev.emit(
                jsonout::event("stage_done")
                    .set("stage", s.name())
                    .set("secs", jsonout::secs(stage_t0.elapsed()))
                    .set("checked", false)
                    .build(),
            );
            continue;
        }
        log_line(&mut log, &format!("==== stage {}", s.name()));
        // A new tree needs its own boot stage.
        if s == Stage::Restore {
            let _ = std::fs::remove_file(sys.p(&status::boot_marker()));
            // The copy begins: not complete until the stage's end (J10).
            let _ = std::fs::remove_file(sys.p(&status::restore_done_marker()));
            if let Err(e) = write_mark(sys, &status::restore_started_marker(), &mark) {
                if opts.json {
                    log_line(&mut log, &format!("FAILED: {e}"));
                    ev.fail(
                        jsonout::failed_event(
                            Some(s.name()),
                            None,
                            FailureCode::WriteFailed,
                            None,
                            &FailFacts::default(),
                            &e,
                            log_at,
                        ),
                        t0,
                    );
                    return (EXIT_FAILED, out, format!("bko-holt load: restore: {e}\n"));
                }
            }
        }
        // A table load wrote is recorded only once the stage has run: until
        // then, with --erase, a rerun takes the disk as it finds it.
        if s == Stage::Partition {
            for (path, _) in &p.mapping.records {
                let _ = std::fs::remove_file(sys.p(path));
            }
            // The disks are new again: no copy has begun on them, and
            // under --json no boot stage has run for them either.
            for m in [
                status::restore_started_marker(),
                status::restore_done_marker(),
            ] {
                let _ = std::fs::remove_file(sys.p(&m));
            }
            if opts.json {
                let _ = std::fs::remove_file(sys.p(&status::boot_marker()));
            }
        }
        for (i, c) in cmds.iter().enumerate() {
            // A stop is taken between commands.
            if ev.stop_asked() {
                return stopped(ev, &mut log, out, err);
            }
            let at = At {
                stage: s,
                n: i + 1,
                part: part_of(c),
            };
            ev.emit(jsonout::cmd_event(
                s.name(),
                at.n,
                cmds.len(),
                c,
                false,
                at.part,
            ));
            hook::note(|h| h.n = Some(at.n));
            let cmd_t0 = Instant::now();
            let ran = step(c, &mut log, sys, ev, &at);
            let done = |status: Option<i32>, signal: Option<i32>| {
                jsonout::event("cmd_done")
                    .set("stage", s.name())
                    .set("n", at.n)
                    .set("status", status)
                    .set("signal", signal)
                    .set("secs", jsonout::secs(cmd_t0.elapsed()))
                    .build()
            };
            match ran {
                Ok(warnings) => {
                    // A `warns` command's warnings come with its end, but
                    // before its cmd_done: they are the command's.
                    for w in warnings {
                        ev.emit(
                            jsonout::event("warning")
                                .set("stage", s.name())
                                .set("n", at.n)
                                .set("source", "cmd")
                                .set("text", jsonout::clipped(&w, 4096))
                                .set("item", Value::Null)
                                .build(),
                        );
                        err += &format!("bko-holt load: {}: {w}\n", s.name());
                    }
                    ev.emit(done((jsonout::kind(c) == "run").then_some(0), None));
                }
                Err(f) => {
                    ev.emit(done(f.facts.status, f.facts.signal));
                    log_line(&mut log, &format!("FAILED: {}", f.text));
                    // Stopped: the archiver was interrupted, or the command
                    // that ran on after the stop failed; either way no
                    // `failed`, and nothing after it runs.
                    if f.stopped || ev.stop_asked() {
                        return stopped(ev, &mut log, out, err);
                    }
                    err += &format!("bko-holt load: {}: {}\n", s.name(), f.text);
                    err += &format!("bko-holt load: the log is {RUN_DIR}/log/load.log\n");
                    let code = failure_code(c, s, &f, sys);
                    ev.fail(
                        jsonout::failed_event(
                            Some(s.name()),
                            Some(at.n),
                            code,
                            Some(c),
                            &f.facts,
                            &f.text,
                            log_at,
                        ),
                        t0,
                    );
                    return (EXIT_FAILED, out, err);
                }
            }
        }
        hook::note(|h| h.n = None);
        if s == Stage::Partition {
            if let Err(e) = write_records(sys, &p.mapping.records) {
                log_line(&mut log, &format!("FAILED: {e}"));
                ev.fail(
                    jsonout::failed_event(
                        Some(s.name()),
                        None,
                        FailureCode::WriteFailed,
                        None,
                        &FailFacts::default(),
                        &e,
                        log_at,
                    ),
                    t0,
                );
                return (EXIT_FAILED, out, format!("bko-holt load: partition: {e}\n"));
            }
        }
        if s == Stage::Boot {
            if let Err(e) = std::fs::write(sys.p(&status::boot_marker()), "") {
                log_line(&mut log, &format!("FAILED: {e}"));
                let text = format!("{}: {e}", status::boot_marker());
                ev.fail(
                    jsonout::failed_event(
                        Some(s.name()),
                        None,
                        FailureCode::WriteFailed,
                        None,
                        &FailFacts::default(),
                        &text,
                        log_at,
                    ),
                    t0,
                );
                return (EXIT_FAILED, out, format!("bko-holt load: boot: {text}\n"));
            }
        }
        // Read the disks back: the stage must show as done.  The restore's
        // by the tree alone, as text mode reads it: under --json its row
        // needs the mark written next.
        let (st, why) = if s == Stage::Restore && opts.json {
            state_of(layout, &tree_opts, sys, s)
        } else {
            state_of(layout, opts, sys, s)
        };
        if !matches!(st, State::Done | State::Nothing) {
            let msg = format!(
                "{} ran, but the disks don't show it done: {}{}",
                s.name(),
                st_word(st),
                if why.is_empty() {
                    String::new()
                } else {
                    format!(" ({why})")
                }
            );
            log_line(&mut log, &format!("FAILED: {msg}"));
            ev.fail(
                jsonout::failed_event(
                    Some(s.name()),
                    None,
                    FailureCode::NotDoneAfter,
                    None,
                    &FailFacts::default(),
                    &msg,
                    log_at,
                ),
                t0,
            );
            return (EXIT_FAILED, out, format!("bko-holt load: {msg}\n"));
        }
        // The copy is complete: after the stage's last command (the shared
        // snapshots' flags, thin_restore, the atimes' capture all come
        // after the archiver) and its read-back (J10).
        if s == Stage::Restore {
            if let Err(e) = write_mark(sys, &status::restore_done_marker(), &mark) {
                if opts.json {
                    log_line(&mut log, &format!("FAILED: {e}"));
                    ev.fail(
                        jsonout::failed_event(
                            Some(s.name()),
                            None,
                            FailureCode::WriteFailed,
                            None,
                            &FailFacts::default(),
                            &e,
                            log_at,
                        ),
                        t0,
                    );
                    return (EXIT_FAILED, out, format!("bko-holt load: restore: {e}\n"));
                }
            }
        }
        out += &format!("# {} done\n", s.name());
        log_line(&mut log, &format!("==== stage {} done", s.name()));
        ev.emit(
            jsonout::event("stage_done")
                .set("stage", s.name())
                .set("secs", jsonout::secs(stage_t0.elapsed()))
                .set("checked", true)
                .build(),
        );
    }
    if !opts.commit {
        out += "\n# Nothing was written: add --commit to run these.\n";
    }
    ev.end(EXIT_OK, false, t0);
    (EXIT_OK, out, err)
}

// ---- one load at a time (J8) ---------------------------------------------

/// The lock a --json --commit run holds for as long as it runs: a line of
/// JSON, its pid and its start time (`crate::sys::process_start`), for a
/// restarted bko-rescue to find it and for the stop to signal it.
pub fn lock_path() -> String {
    format!("{RUN_DIR}/load.lock")
}

/// Who holds a lock: a live load (its pid, or None while the file is
/// being written), or nobody (the process is gone, or another has its
/// pid now).
#[derive(Debug, PartialEq)]
enum Holder {
    Live(Option<u32>),
    Stale,
}

/// Who holds the lock at PATH.
fn holder(path: &std::path::Path) -> Holder {
    holder_since(path, crate::sys::boot_time())
}

/// holder, the system booted at BOOT where that is known (Haiku's: there
/// no start time tells a reused pid, and `RUN_DIR` outlives a reboot), when
/// a lock written before it is stale whatever its pid.
fn holder_since(path: &std::path::Path, boot: Option<std::time::SystemTime>) -> Holder {
    let Ok(text) = std::fs::read_to_string(path) else {
        return Holder::Stale;
    };
    let modified = std::fs::metadata(path).and_then(|m| m.modified()).ok();
    if let (Some(m), Some(b)) = (modified, boot) {
        if m < b {
            return Holder::Stale;
        }
    }
    let v = crate::json::parse(text.trim()).ok();
    let Some(pid) = v
        .as_ref()
        .and_then(|v| v.get_u64("pid"))
        .and_then(|p| u32::try_from(p).ok())
    else {
        // Not a lock's line: one being written now, or one left torn long
        // ago.
        let young = modified
            .and_then(|m| m.elapsed().ok())
            .is_some_and(|age| age < Duration::from_secs(10));
        return if young {
            Holder::Live(None)
        } else {
            Holder::Stale
        };
    };
    if !crate::sys::process_alive(pid) {
        return Holder::Stale;
    }
    let started = v
        .as_ref()
        .and_then(|v| v.get_str("start"))
        .map(str::to_string);
    match (started, crate::sys::process_start(pid)) {
        (Some(a), Some(b)) if a != b => Holder::Stale,
        _ => Holder::Live(Some(pid)),
    }
}

/// The live holder of SYS's lock, if any.
fn lock_holder(sys: &Sys) -> Option<Option<u32>> {
    let path = sys.p(&lock_path());
    if !path.exists() {
        return None;
    }
    match holder(&path) {
        Holder::Live(pid) => Some(pid),
        Holder::Stale => None,
    }
}

/// What a run says when another holds the lock.
fn lock_held_text(holder: Option<u32>) -> String {
    match holder {
        Some(pid) => format!("another bko-holt load is running: pid {pid}"),
        None => "another bko-holt load is running (its lock is being written)".to_string(),
    }
}

#[derive(Debug)]
enum LockErr {
    Held(Option<u32>),
    Io(String),
}

/// The lock, held: removed when dropped (the run's end, however it ends;
/// the panic hook removes it too).
#[derive(Debug)]
struct Lock {
    path: std::path::PathBuf,
}

impl Lock {
    /// Takes SYS's lock: made anew (`O_EXCL`), or taken over from a holder
    /// that is gone.  A takeover holds `RUN_DIR/load.lock.takeover` (`O_EXCL`
    /// too) from its second look at the old lock until its own is written,
    /// so two runs that both found the old one stale can't both hold the
    /// lock (the second removing the first's new one).
    fn take(sys: &Sys) -> Result<Lock, LockErr> {
        use std::os::unix::fs::OpenOptionsExt;
        let path = sys.p(&lock_path());
        let guard = sys.p(&format!("{}.takeover", lock_path()));
        let _ = mkdir_private(&sys.p(RUN_DIR));
        let me = std::process::id();
        let line = Obj::new()
            .set("pid", me)
            .set("start", crate::sys::process_start(me))
            .build()
            .compact()
            + "\n";
        for _ in 0..50 {
            if let Some(l) = Lock::make(&path, &line)? {
                return Ok(l);
            }
            if let Holder::Live(pid) = holder(&path) {
                return Err(LockErr::Held(pid));
            }
            match std::fs::OpenOptions::new()
                .write(true)
                .create_new(true)
                .mode(0o600)
                .open(&guard)
            {
                Ok(_) => {
                    // Looked at again under the guard: a lock made since
                    // the first look is another's, and stays.
                    let gone = path.exists() && holder(&path) == Holder::Stale;
                    if gone {
                        let _ = std::fs::remove_file(&path);
                    }
                    let made = Lock::make(&path, &line);
                    let _ = std::fs::remove_file(&guard);
                    match made? {
                        Some(l) => return Ok(l),
                        None => continue,
                    }
                }
                Err(e) if e.kind() == std::io::ErrorKind::AlreadyExists => {
                    // Another run is taking it over; a guard left by one
                    // that died doing so goes after 10 s.
                    let old = std::fs::metadata(&guard)
                        .and_then(|m| m.modified())
                        .ok()
                        .and_then(|m| m.elapsed().ok())
                        .is_some_and(|age| age >= Duration::from_secs(10));
                    if old {
                        let _ = std::fs::remove_file(&guard);
                    } else {
                        std::thread::sleep(Duration::from_millis(20));
                    }
                }
                Err(e) => return Err(LockErr::Io(format!("{}.takeover: {e}", lock_path()))),
            }
        }
        Err(LockErr::Held(None))
    }

    /// The lock at PATH made anew with LINE (`O_EXCL`), or None when there is
    /// one.  A lock whose line can't be written is removed, not left to
    /// read as one being written.
    fn make(path: &std::path::Path, line: &str) -> Result<Option<Lock>, LockErr> {
        use std::os::unix::fs::OpenOptionsExt;
        match std::fs::OpenOptions::new()
            .write(true)
            .create_new(true)
            .mode(0o600)
            .open(path)
        {
            Ok(mut f) => {
                if let Err(e) = f.write_all(line.as_bytes()) {
                    let _ = std::fs::remove_file(path);
                    return Err(LockErr::Io(format!("{}: {e}", lock_path())));
                }
                hook::note(|h| h.lock = Some(path.to_path_buf()));
                Ok(Some(Lock {
                    path: path.to_path_buf(),
                }))
            }
            Err(e) if e.kind() == std::io::ErrorKind::AlreadyExists => Ok(None),
            Err(e) => Err(LockErr::Io(format!("{}: {e}", lock_path()))),
        }
    }
}

impl Drop for Lock {
    fn drop(&mut self) {
        release(&self.path);
        hook::note(|h| h.lock = None);
    }
}

/// Removes the lock at PATH if it is still this process's.
fn release(path: &std::path::Path) {
    let mine = std::fs::read_to_string(path)
        .ok()
        .and_then(|t| crate::json::parse(t.trim()).ok())
        .and_then(|v| v.get_u64("pid"))
        == Some(u64::from(std::process::id()));
    if mine {
        let _ = std::fs::remove_file(path);
    }
}

// ---- the panic hook under --json -----------------------------------------

/// `load --json` with a writing stage, before it runs: SIGTERM stops it
/// between commands instead of ending it (J7), no core dumps for it or its
/// children, and a panic ends the stream with `failed` (`internal`) and
/// `end` 101 and lets the lock go.  The real program only: a test would
/// change the whole test process's.
pub fn arm() {
    crate::sys::catch_sigterm();
    hook::arm();
}

mod hook {
    use crate::load::codes::FailureCode;
    use crate::load::jsonout;
    use std::sync::Mutex;
    use std::sync::atomic::{AtomicBool, Ordering};

    static ARMED: AtomicBool = AtomicBool::new(false);

    /// What the hook needs of the run: whether `start` and `end` have been
    /// written, the run's --commit and layout (host name, dump time) for a
    /// `start` of its own, where it is, the process group of the command
    /// running now, and the lock it holds.
    pub(super) struct State {
        pub started: bool,
        pub ended: bool,
        pub commit: bool,
        pub layout: Option<(Option<String>, Option<String>)>,
        pub stage: Option<&'static str>,
        pub n: Option<usize>,
        pub child: Option<i32>,
        pub lock: Option<std::path::PathBuf>,
        pub t0: Option<std::time::Instant>,
    }

    static STATE: Mutex<State> = Mutex::new(State {
        started: false,
        ended: false,
        commit: false,
        layout: None,
        stage: None,
        n: None,
        child: None,
        lock: None,
        t0: None,
    });

    /// Tells the hook of the run, when it is armed (the program's own run;
    /// never a test's).
    pub(super) fn note(f: impl FnOnce(&mut State)) {
        if ARMED.load(Ordering::SeqCst) {
            if let Ok(mut s) = STATE.lock() {
                f(&mut s);
            }
        }
    }

    pub(super) fn arm() {
        if let Ok(mut s) = STATE.lock() {
            s.t0 = Some(std::time::Instant::now());
        }
        ARMED.store(true, Ordering::SeqCst);
        let before = std::panic::take_hook();
        std::panic::set_hook(Box::new(move |info| {
            // The message on standard error, as ever.
            before(info);
            // A panic of a reader thread isn't the run's end.
            if std::thread::current().name() != Some("main") {
                return;
            }
            let Ok(mut s) = STATE.try_lock() else { return };
            if s.ended {
                return;
            }
            let msg = info
                .payload()
                .downcast_ref::<&str>()
                .map(|m| m.to_string())
                .or_else(|| info.payload().downcast_ref::<String>().cloned())
                .unwrap_or_else(|| "a panic".to_string());
            let at = info
                .location()
                .map(|l| format!(" at {}:{}", l.file(), l.line()))
                .unwrap_or_default();
            let text = format!("bko-holt panicked{at}: {msg}");
            // The command running now (in a group of its own, which only
            // load signals) dies with the run, and the lock goes, before
            // `end`: a Try again mustn't meet a second archiver on the
            // tree, nor the lock.
            if let Some(g) = s.child.take() {
                crate::sys::send_signal(-g, crate::sys::SIGKILL);
            }
            if let Some(p) = s.lock.take() {
                super::release(&p);
            }
            if !s.started {
                let layout = match &s.layout {
                    Some((host, generated)) => crate::json::Obj::new()
                        .set("hostname", host.clone())
                        .set("generated", generated.clone())
                        .build(),
                    None => crate::json::Value::Null,
                };
                let start = jsonout::event("start")
                    .set("format", "bko-holt-events")
                    .set("schema", jsonout::SCHEMA)
                    .set("version", env!("CARGO_PKG_VERSION"))
                    .set("generated", crate::timestamp())
                    .set("pid", std::process::id())
                    .set("commit", s.commit)
                    .set("dry_run", jsonout::dry_run())
                    .set("stages", Vec::<String>::new())
                    .set("stage_commands", crate::json::Obj::new())
                    .set("plan_sha256", crate::json::Value::Null)
                    .set(
                        "log",
                        s.commit
                            .then(|| format!("{}/log/load.log", crate::load::RUN_DIR)),
                    )
                    .set("layout", layout)
                    .build();
                jsonout::write_stdout(&jsonout::event_line(start));
            }
            let log = s
                .commit
                .then(|| format!("{}/log/load.log", crate::load::RUN_DIR));
            let failed = jsonout::failed_event(
                s.stage,
                s.n,
                FailureCode::Internal,
                None,
                &jsonout::FailFacts::default(),
                &text,
                log.as_deref(),
            );
            jsonout::write_stdout(&jsonout::event_line(failed.build()));
            let secs = s.t0.map(|t| jsonout::secs(t.elapsed())).unwrap_or(0.0);
            jsonout::write_stdout(&jsonout::event_line(jsonout::end_event(101, false, secs)));
            s.ended = true;
        }));
    }
}

fn mkdir_private(dir: &std::path::Path) -> std::io::Result<()> {
    use std::os::unix::fs::{DirBuilderExt, PermissionsExt};
    std::fs::DirBuilder::new()
        .recursive(true)
        .mode(0o700)
        .create(dir)?;
    if dir.starts_with(RUN_DIR) {
        let _ = std::fs::set_permissions(dir, std::fs::Permissions::from_mode(0o700));
    }
    Ok(())
}

fn write_private(path: &str, text: &str) -> std::io::Result<()> {
    use std::os::unix::fs::{OpenOptionsExt, PermissionsExt};
    let mut f = std::fs::OpenOptions::new()
        .write(true)
        .create(true)
        .truncate(true)
        .mode(0o600)
        .open(path)?;
    let _ = f.set_permissions(std::fs::Permissions::from_mode(0o600));
    f.write_all(text.as_bytes())
}

/// A file a step writes.  Under `RUN_DIR` (bko-holt's own: keys, LVM
/// metadata, lists) it's private, 0600 in 0700 directories.  Anywhere else
/// it's the restored system's (Proxmox's /etc/kernel/proxmox-boot-uuids):
/// written as before, an existing file keeping its mode and owner, a new
/// one and its directories made under the umask; but one the step says is
/// PRIVATE (LILO's copy of lilo.conf, which can hold password=) is 0600,
/// in directories made under the umask.  There the file, or its
/// directory, being a symbolic link is refused: bko-holt writes from
/// outside the chroot, where the link would name a rescue system's file.
fn write_file(path: &str, text: &str, private: bool) -> Result<(), String> {
    let p = std::path::Path::new(path);
    let ours = p.starts_with(RUN_DIR);
    if !ours {
        for q in [p, p.parent().unwrap_or(p)] {
            if std::fs::symlink_metadata(q).is_ok_and(|m| m.file_type().is_symlink()) {
                return Err(format!(
                    "{}: a symbolic link in the restored system, which bko-holt doesn't write through: from the rescue system it names another file",
                    q.display()
                ));
            }
        }
    }
    if let Some(dir) = p.parent() {
        let made = if ours {
            mkdir_private(dir)
        } else {
            std::fs::create_dir_all(dir)
        };
        made.map_err(|e| format!("{}: {e}", dir.display()))?;
    }
    let written = if ours || private {
        write_private(path, text)
    } else {
        std::fs::write(path, text)
    };
    written.map_err(|e| format!("{path}: {e}"))
}

/// A copy mark (`status::restore_started_marker`) at PATH, through SYS (the
/// host's `RUN_DIR`, or a test's tree).
fn write_mark(sys: &Sys, path: &str, text: &str) -> Result<(), String> {
    write_file(&sys.p(path).to_string_lossy(), text, false)
}

/// The partition stage's record of each table it wrote (`target::table_record`),
/// through SYS as `write_mark`.
fn write_records(sys: &Sys, records: &[(String, String)]) -> Result<(), String> {
    for (path, text) in records {
        write_file(&sys.p(path).to_string_lossy(), text, false)?;
    }
    Ok(())
}

fn st_word(s: State) -> &'static str {
    match s {
        State::Done => "done",
        State::Nothing => "nothing to do",
        State::Partly => "partly done",
        State::NotDone => "not done",
        State::Unknown => "can't tell",
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// A file written into the restored system (Proxmox's
    /// /etc/kernel/proxmox-boot-uuids, rewritten after proxmox-boot-tool
    /// init) keeps the mode it has; only `RUN_DIR`'s own files are made 0600.
    #[test]
    fn write_file_keeps_a_target_files_mode() {
        use std::os::unix::fs::PermissionsExt;
        let t = Tree::new("load-exec-write-mode");
        let f = t.root().join("etc/kernel/proxmox-boot-uuids");
        t.file("/etc/kernel/proxmox-boot-uuids", "OLD\n");
        std::fs::set_permissions(&f, std::fs::Permissions::from_mode(0o644)).unwrap();
        let c = Cmd::write_file(&f.to_string_lossy(), "1234-ABCD\n".to_string());
        assert_eq!(run_cmd(&c, &mut None, &Sys::host()), Ok(Vec::new()));
        assert_eq!(std::fs::read_to_string(&f).unwrap(), "1234-ABCD\n");
        assert_eq!(
            std::fs::metadata(&f).unwrap().permissions().mode() & 0o7777,
            0o644
        );
        // A new one, in a directory that isn't there yet, is made too.
        let n = t.root().join("etc/new/dir/file");
        assert_eq!(
            run_cmd(
                &Cmd::write_file(&n.to_string_lossy(), "x".to_string()),
                &mut None,
                &Sys::host()
            ),
            Ok(Vec::new())
        );
        assert_eq!(std::fs::read_to_string(&n).unwrap(), "x");
        // One the step says is private (LILO's copy of lilo.conf): 0600,
        // new or not.
        let l = t.root().join("etc/lilo.conf.bko-holt");
        assert_eq!(
            run_cmd(
                &Cmd::write_file(&l.to_string_lossy(), "password=x\n".to_string()).private(),
                &mut None,
                &Sys::host()
            ),
            Ok(Vec::new())
        );
        assert_eq!(
            std::fs::metadata(&l).unwrap().permissions().mode() & 0o7777,
            0o600
        );
        std::fs::set_permissions(&l, std::fs::Permissions::from_mode(0o644)).unwrap();
        assert_eq!(
            run_cmd(
                &Cmd::write_file(&l.to_string_lossy(), "password=y\n".to_string()).private(),
                &mut None,
                &Sys::host()
            ),
            Ok(Vec::new())
        );
        assert_eq!(std::fs::read_to_string(&l).unwrap(), "password=y\n");
        assert_eq!(
            std::fs::metadata(&l).unwrap().permissions().mode() & 0o7777,
            0o600
        );
    }

    /// A target file that is a symbolic link, or in a directory that is
    /// one, is refused, plain or private, and nothing is written where it
    /// points: from the rescue system an absolute link names the rescue
    /// system's own file.
    #[test]
    fn write_file_refuses_a_target_symlink() {
        let t = Tree::new("load-exec-write-symlink");
        let host = t.root().join("host");
        t.file("/host/file", "HOST\n");
        std::fs::create_dir_all(t.root().join("etc")).unwrap();
        let f = t.root().join("etc/lilo.conf.bko-holt");
        std::os::unix::fs::symlink(host.join("file"), &f).unwrap();
        for c in [
            Cmd::write_file(&f.to_string_lossy(), "password=x\n".to_string()),
            Cmd::write_file(&f.to_string_lossy(), "password=x\n".to_string()).private(),
        ] {
            let e = run_cmd(&c, &mut None, &Sys::host()).unwrap_err();
            assert!(
                e.starts_with(&format!(
                    "{}: a symbolic link in the restored system",
                    f.display()
                )),
                "{e}"
            );
        }
        // The directory a link: /etc/kernel -> the host's directory.
        let k = t.root().join("etc/kernel");
        std::os::unix::fs::symlink(&host, &k).unwrap();
        let u = k.join("proxmox-boot-uuids");
        let e = run_cmd(
            &Cmd::write_file(&u.to_string_lossy(), "1234-ABCD\n".to_string()),
            &mut None,
            &Sys::host(),
        )
        .unwrap_err();
        assert!(
            e.starts_with(&format!(
                "{}: a symbolic link in the restored system",
                k.display()
            )),
            "{e}"
        );
        assert_eq!(
            std::fs::read_to_string(host.join("file")).unwrap(),
            "HOST\n"
        );
        assert!(!host.join("proxmox-boot-uuids").exists());
    }

    /// A command that `warns` gives back its stderr's warning lines when it
    /// succeeds; one that doesn't gives back nothing; a failure ends with
    /// its stderr's last lines.
    #[test]
    fn run_cmd_warnings() {
        let s = Sys::host();
        let script = "echo 'x: warning: one' >&2; echo other >&2; echo 'x: warning: two' >&2";
        let c = Cmd::new(&["sh", "-c", script]);
        assert_eq!(run_cmd(&c, &mut None, &s), Ok(Vec::new()));
        assert_eq!(
            run_cmd(&c.clone().warns(), &mut None, &s),
            Ok(vec![
                "x: warning: one".to_string(),
                "x: warning: two".to_string()
            ])
        );
        let e = run_cmd(
            &Cmd::new(&[
                "sh",
                "-c",
                &format!("{script}; echo 'x: 1 failed' >&2; exit 1"),
            ])
            .warns(),
            &mut None,
            &s,
        )
        .unwrap_err();
        assert!(e.ends_with("x: warning: two\n    x: 1 failed"), "{e}");
    }

    /// A read-back runs in-process: the device as the source's passes, and
    /// a feature the source didn't have stops it, by name.
    #[test]
    fn run_cmd_reads_back() {
        use crate::sys::FakeRunner;
        let t = Tree::new("load-exec-readback");
        let img = crate::probe::ext::sample::image(0x0004, 0x0002 | 0x0040, 0x0001, "root");
        t.file("/dev/vdb1", &img);
        let s = Sys::new(t.root(), Box::new(FakeRunner::default()), true);
        let src = crate::probe::probe_all(&crate::bytes::MemSource(img))
            .remove(0)
            .to_json();
        let c = Cmd::read_back("/dev/vdb1", &src);
        assert_eq!(run_cmd(&c, &mut None, &s), Ok(Vec::new()));
        t.file(
            "/dev/vdb1",
            crate::probe::ext::sample::image(0x0004 | 0x1000, 0x0002 | 0x0040, 0x0001, "root"),
        );
        assert_eq!(
            run_cmd(&c, &mut None, &s),
            Err("/dev/vdb1: made with orphan_file, which the source's ext4 didn't have: the rescue image's mkfs turned it on".to_string())
        );
    }

    /// --through on the source's own disk as found, its table the source's
    /// and named with --erase: with no record that load wrote the table the
    /// partition stage runs, and every stage after it (the mount stage too,
    /// though something is mounted where it mounts); with the record, the
    /// stages status shows done are skipped, as before.
    #[test]
    fn through_runs_the_partition_stage_on_a_table_load_did_not_write() {
        use crate::load::layout::sample;
        use crate::load::{Parsed, parse_args, target};
        use crate::sys::FakeRunner;
        use crate::testutil::Tree;
        let t = Tree::new("load-exec-through");
        let mut table = vec![0u8; 512];
        table[440..444].copy_from_slice(&0x1f2e_3d4cu32.to_le_bytes());
        for (slot, start, n) in [
            (0, 2048, 1_953_792),
            (1, 1_955_840, 3_905_536),
            (2, 5_861_376, 27_693_056),
        ] {
            crate::parttable::build::mbr_entry(&mut table, slot, 0, 0x83, start, n);
        }
        t.disk("vdb", "253:16", &table, 512)
            .file("/sys/block/vdb/size", "33554432\n");
        t.file("/proc/self/mountinfo", "36 35 0:50 / /mnt/bko-target rw - tmpfs tmpfs rw\n37 36 0:51 / /mnt/bko-target/boot rw - tmpfs tmpfs rw\n");
        t.file("/proc/swaps", "Filename\tType\tSize\tUsed\tPriority\n");
        for p in [
            "wipefs",
            "dd",
            "sfdisk",
            "udevadm",
            "mke2fs",
            "tune2fs",
            "mkswap",
            "mkfs.btrfs",
            "btrfs",
            "mkdir",
            "mount",
            "umount",
            "chroot",
            "cp",
            "sh",
            "bash",
        ] {
            t.file(&format!("/usr/sbin/{p}"), "");
        }
        let s = Sys::new(t.root(), Box::new(FakeRunner::default()), true);
        let l = Layout::from_json(crate::json::parse(&sample::bios_mbr()).unwrap()).unwrap();
        let o = match parse_args(&[
            "l.json".into(),
            "--map".into(),
            "vda=vdb".into(),
            "--erase".into(),
            "/dev/vdb".into(),
            "--through".into(),
            "mount".into(),
        ])
        .unwrap()
        {
            Parsed::Run(o) => o,
            _ => unreachable!(),
        };
        let stages = [
            Stage::Partition,
            Stage::Assemble,
            Stage::Format,
            Stage::Mount,
        ];
        let (code, out, err) = run(&l, &o, &s, &stages);
        assert_eq!(code, EXIT_OK, "{out}{err}");
        assert!(out.contains("# ---- partition ----\n# vda -> /dev/vdb: clear the old table and signatures\nwipefs --all /dev/vdb\n"), "{out}");
        assert!(out.contains("# ---- mount ----\n"), "{out}");
        assert!(!out.contains("done already"), "{out}");
        assert!(
            out.contains("# ---- assemble: nothing to do already\n"),
            "{out}"
        );

        // The record this session's partition stage writes.
        let p = target::prepare(&l, &o, &s);
        assert_eq!(p.mapping.records.len(), 1);
        let (path, text) = &p.mapping.records[0];
        t.file(path, text.as_str());
        let (code, out, err) = run(&l, &o, &s, &stages);
        assert_eq!(code, EXIT_OK, "{out}{err}");
        assert!(out.contains("# ---- partition: done already\n"), "{out}");
        assert!(out.contains("# ---- format ----\n"), "{out}");
        assert!(out.contains("# ---- mount: done already\n"), "{out}");
    }

    /// The test's rescue system: every tool the stages run, and TABLE on vdb.
    fn rescue(name: &str, table: &[u8]) -> Tree {
        let t = Tree::new(name);
        t.disk("vdb", "253:16", table, 512)
            .file("/sys/block/vdb/size", "33554432\n");
        t.file("/proc/self/mountinfo", "")
            .file("/proc/swaps", "Filename\tType\tSize\tUsed\tPriority\n");
        for p in [
            "wipefs",
            "dd",
            "sfdisk",
            "udevadm",
            "mke2fs",
            "tune2fs",
            "mkswap",
            "mkfs.btrfs",
            "btrfs",
            "mkfs.xfs",
            "xfs_admin",
            "mkdir",
            "mount",
            "umount",
            "chroot",
            "cp",
            "sh",
            "bash",
        ] {
            t.file(&format!("/usr/sbin/{p}"), "");
        }
        t
    }

    /// A text run's log has a password= item's value as <password> (the
    /// copy of lilo.conf lilo runs on: its copy in the restored
    /// /var/log/bko-holt and report-bug's bundle come from the log), the
    /// file itself as it is (`RustAudit1` HD1-01).
    #[test]
    fn a_password_is_not_logged() {
        use crate::sys::FakeRunner;
        let t = Tree::new("load-exec-log-password");
        let s = Sys::new(t.root(), Box::new(FakeRunner::default()), true);
        let path = t
            .p("/mnt/bko-target/etc/lilo.conf.bko-holt")
            .to_string_lossy()
            .into_owned();
        let text = "boot=\"/dev/vda\"\npassword=\"s3cret\"\nimage=\"/boot/vmlinuz\"\n  password=\"my img pass\"\n";
        let log_path = t.p("/load.log");
        let mut log = Some(std::fs::File::create(&log_path).unwrap());
        run_cmd(
            &Cmd::write_file(&path, text.to_string()).private(),
            &mut log,
            &s,
        )
        .unwrap();
        run_cmd(&Cmd::note("password=s3cret"), &mut log, &s).unwrap();
        drop(log);
        let logged = std::fs::read_to_string(&log_path).unwrap();
        assert!(
            !logged.contains("s3cret") && !logged.contains("img pass"),
            "{logged}"
        );
        assert!(
            logged.contains(
                "password=\"<password>\"\nimage=\"/boot/vmlinuz\"\n  password=\"<password>\"\n"
            ),
            "{logged}"
        );
        assert_eq!(std::fs::read_to_string(&path).unwrap(), text);
    }

    fn opts_of(a: &[&str]) -> Options {
        let mut v = vec!["l.json".to_string()];
        v.extend(a.iter().map(|s| s.to_string()));
        match crate::load::parse_args(&v).unwrap() {
            crate::load::Parsed::Run(o) => o,
            _ => unreachable!(),
        }
    }

    /// Without --erase, a dry run --through mount on a blank disk skips
    /// what it always skipped: the stages the layout needs nothing from.
    #[test]
    fn without_erase_through_skips_as_before() {
        use crate::load::layout::sample;
        use crate::sys::FakeRunner;
        let t = rescue("load-exec-plain", &[]);
        let s = Sys::new(t.root(), Box::new(FakeRunner::default()), true);
        let l = Layout::from_json(crate::json::parse(&sample::bios_mbr()).unwrap()).unwrap();
        let (code, out, err) = run(
            &l,
            &opts_of(&["--map", "vda=vdb", "--through", "mount"]),
            &s,
            &[
                Stage::Partition,
                Stage::Assemble,
                Stage::Format,
                Stage::Mount,
            ],
        );
        assert_eq!(code, EXIT_OK, "{out}{err}");
        let heads: Vec<&str> = out.lines().filter(|l| l.starts_with("# ---- ")).collect();
        assert_eq!(
            heads,
            vec![
                "# ---- partition ----",
                "# ---- assemble: nothing to do already",
                "# ---- format ----",
                "# ---- mount ----"
            ],
            "{out}"
        );
    }

    /// The whole-disk XFS layout onto the source's own disk, the old XFS
    /// (the layout's UUID) on it, with --erase and no record: the
    /// partition stage runs, and format with it, though status would take
    /// the old filesystem for format's.
    #[test]
    fn through_wipes_a_disk_with_no_table_load_did_not_wipe() {
        use crate::load::layout::sample;
        use crate::sys::FakeRunner;
        let t = rescue("load-exec-whole", &crate::probe::xfs::sample::image(""));
        let s = Sys::new(t.root(), Box::new(FakeRunner::default()), true);
        let vdx = r#"{"name": "vdx", "kind": "disk", "size_bytes": 17179869184, "logical_sector_size": 512, "model": null, "serial": null, "wwid": null,
          "partition_table": null, "partitions": [], "boot_code": {"kind": "grub2"},
          "content": {"type": "xfs", "usage": "filesystem", "uuid": "a0a1a2a3-a4a5-a6a7-a8a9-aaabacadaeaf", "label": null}}"#;
        let fstab = r#"{"spec": "UUID=a0a1a2a3-a4a5-a6a7-a8a9-aaabacadaeaf", "file": "/", "type": "xfs", "options": "defaults", "freq": 0, "passno": 0, "resolves_to": "vdx"}"#;
        // The sample's disks, btrfs and fstab swapped for vdx's.
        let swap = |j: &str, from: &str, to: &str, with: &str| {
            let (a, rest) = j.split_at(j.find(from).unwrap());
            format!("{a}{with},\n    {}", &rest[rest.find(to).unwrap()..])
        };
        let j = swap(
            &sample::bios_mbr(),
            "\"disks\": [",
            "\"md_arrays\"",
            &format!("\"disks\": [{vdx}]"),
        );
        let j = swap(&j, "\"btrfs\": [", "\"xfs_external_logs\"", "\"btrfs\": []");
        let j = swap(
            &j,
            "\"fstab\": [",
            "\"crypttab\"",
            &format!("\"fstab\": [{fstab}]"),
        );
        let l = Layout::from_json(crate::json::parse(&j).unwrap()).unwrap();
        let stages = [Stage::Partition, Stage::Assemble, Stage::Format];
        let heads = |a: &[&str]| {
            let (code, out, err) = run(&l, &opts_of(a), &s, &stages);
            assert_eq!(code, EXIT_OK, "{a:?}: {out}{err}");
            out.lines()
                .filter(|l| l.starts_with("# ---- "))
                .map(str::to_string)
                .collect::<Vec<_>>()
        };
        // Without --erase, as before: the old XFS is taken for format's.
        assert_eq!(
            heads(&["--map", "vdx=vdb", "--through", "format"]),
            vec![
                "# ---- partition: nothing to do already",
                "# ---- assemble: nothing to do already",
                "# ---- format: done already"
            ]
        );
        let erase = [
            "--map",
            "vdx=vdb",
            "--erase",
            "/dev/vdb",
            "--through",
            "format",
        ];
        assert_eq!(
            heads(&erase),
            vec![
                "# ---- partition ----",
                "# ---- assemble: nothing to do already",
                "# ---- format ----"
            ]
        );
        let p = crate::load::target::prepare(&l, &opts_of(&erase), &s);
        let (path, text) = &p.mapping.records[0];
        t.file(path, text.as_str());
        assert_eq!(
            heads(&erase),
            vec![
                "# ---- partition: done already",
                "# ---- assemble: nothing to do already",
                "# ---- format: done already"
            ]
        );
    }

    // ---- the copy marks (RT10e, J10) --------------------------------------

    /// The sample layout's source disk as load partitioned it in this
    /// session (its table and this session's record), the tree mounted with
    /// a restored Debian in it, a private password file, and SYS's archiver
    /// answering as `BackOtter`'s.
    fn restored(name: &str, archiver: &str) -> (Tree, Sys, String) {
        use crate::load::layout::sample;
        use crate::sys::FakeRunner;
        use std::os::unix::fs::PermissionsExt;
        let mut table = vec![0u8; 512];
        table[440..444].copy_from_slice(&0x1f2e_3d4cu32.to_le_bytes());
        for (slot, start, n) in [
            (0, 2048, 1_953_792),
            (1, 1_955_840, 3_905_536),
            (2, 5_861_376, 27_693_056),
        ] {
            crate::parttable::build::mbr_entry(&mut table, slot, 0, 0x83, start, n);
        }
        let t = rescue(name, &table);
        t.file("/proc/self/mountinfo", "36 35 253:19 / /mnt/bko-target rw,relatime - btrfs /dev/vdb3 rw\n37 36 253:17 / /mnt/bko-target/boot rw,relatime - ext4 /dev/vdb1 rw\n");
        t.file("/mnt/bko-target/etc/fstab", "")
            .file("/mnt/bko-target/etc/os-release", "ID=debian\n");
        t.file("/k/pw", "pw");
        std::fs::set_permissions(t.p("/k/pw"), std::fs::Permissions::from_mode(0o600)).unwrap();
        std::fs::set_permissions(t.p("/k"), std::fs::Permissions::from_mode(0o700)).unwrap();
        let s = Sys::new(
            t.root(),
            Box::new(FakeRunner::default().with(
                &format!("{archiver} --version"),
                "rustic 0.12.0+backotter.4\n",
            )),
            true,
        );
        let l = sample::bios_mbr();
        let lay = Layout::from_json(crate::json::parse(&l).unwrap()).unwrap();
        let (path, text) = crate::load::target::prepare(&lay, &opts_of(&["--map", "vda=vdb"]), &s)
            .mapping
            .records
            .remove(0);
        t.file(&path, text);
        (t, s, l)
    }

    /// Under --json, --through skips restore only with this session's
    /// complete mark: the tree's files alone don't do; text mode skips by
    /// the files, as before.
    #[test]
    fn through_skips_restore_by_its_mark_under_json() {
        let (t, s, l) = restored("load-exec-marks", "/usr/sbin/rustic-bko");
        t.file("/usr/sbin/rustic-bko", "");
        let lay = Layout::from_json(crate::json::parse(&l).unwrap()).unwrap();
        let a = [
            "--map",
            "vda=vdb",
            "--repo",
            "/srv/r",
            "--password-file",
            "/k/pw",
            "--through",
            "restore",
        ];
        let stages = [
            Stage::Partition,
            Stage::Assemble,
            Stage::Format,
            Stage::Mount,
            Stage::Restore,
        ];
        let restore_head = |extra: &[&str]| {
            let (code, out, err) = run(&lay, &opts_of(&[&a[..], extra].concat()), &s, &stages);
            assert_eq!(code, EXIT_OK, "{extra:?}: {out}{err}");
            out.lines()
                .find(|l| l.starts_with("# ---- restore"))
                .unwrap()
                .to_string()
        };
        assert_eq!(restore_head(&[]), "# ---- restore: done already");
        assert_eq!(restore_head(&["--json"]), "# ---- restore ----");
        let m = crate::load::target::prepare(&lay, &opts_of(&["--map", "vda=vdb"]), &s).mapping;
        t.file(
            &status::restore_done_marker(),
            status::restore_mark_text(&lay, &m),
        );
        assert_eq!(restore_head(&["--json"]), "# ---- restore: done already");
        assert_eq!(restore_head(&[]), "# ---- restore: done already");
    }

    /// The restore stage writes its start mark as it begins (text mode
    /// too) and removes the complete one: a copy that fails leaves none,
    /// so under --json its row is partly and it runs again.
    #[test]
    fn a_failed_copy_leaves_no_complete_mark() {
        let t0 = Tree::new("load-exec-archiver");
        // The archiver: a script that fails as rustic-bko would, here at a
        // real path the run can start, and at the same path in the tree,
        // where plan looks for it.
        let archiver = t0.p("/fake-rustic").to_string_lossy().into_owned();
        t0.file(
            "/fake-rustic",
            "#!/bin/sh\necho 'error: the repository is gone' >&2\nexit 3\n",
        );
        use std::os::unix::fs::PermissionsExt;
        std::fs::set_permissions(t0.p("/fake-rustic"), std::fs::Permissions::from_mode(0o755))
            .unwrap();
        let (t, s, l) = restored("load-exec-fail", &archiver);
        t.file(&archiver, "");
        let lay = Layout::from_json(crate::json::parse(&l).unwrap()).unwrap();
        let m = crate::load::target::prepare(&lay, &opts_of(&["--map", "vda=vdb"]), &s).mapping;
        let mark = status::restore_mark_text(&lay, &m);
        assert_eq!(mark, "layout deb13 -\ntarget vda vdb\n");
        let a = [
            "--map",
            "vda=vdb",
            "--repo",
            "/srv/r",
            "--password-file",
            "/k/pw",
            "--archiver",
            archiver.as_str(),
            "--only",
            "restore",
            "--commit",
        ];
        for json in [false, true] {
            // A complete mark an earlier copy left.
            t.file(&status::restore_done_marker(), mark.as_str());
            let _ = std::fs::remove_file(t.p(&status::restore_started_marker()));
            let extra: &[&str] = if json { &["--json"] } else { &[] };
            let o = opts_of(&[&a[..], extra].concat());
            let (code, _, err) = run(&lay, &o, &s, &[Stage::Restore]);
            assert_eq!(code, EXIT_FAILED, "{json}: {err}");
            assert!(
                err.contains("exit status: 3") && err.contains("the repository is gone"),
                "{json}: {err}"
            );
            assert_eq!(
                std::fs::read_to_string(t.p(&status::restore_started_marker()))
                    .ok()
                    .as_deref(),
                Some(mark.as_str()),
                "{json}"
            );
            assert!(!t.p(&status::restore_done_marker()).exists(), "{json}");
        }
        // Under --json its row is partly: --through runs it again.
        let row = |json: bool| {
            let mut o = opts_of(&["--map", "vda=vdb"]);
            o.json = json;
            status::rows(&lay, &o, &s)
                .0
                .into_iter()
                .find(|r| r.0 == Stage::Restore)
                .map(|r| r.1)
                .unwrap()
        };
        assert_eq!((row(false), row(true)), (State::Done, State::Partly));
        // RUN_DIR's own files are SYS's: the log is the test's tree's.
        assert!(t.p(&format!("{RUN_DIR}/log/load.log")).exists());
    }

    /// (restore.started, restore.done, boot.done).
    type Marks = (Option<String>, Option<String>, bool);

    /// The marks as a step finds them in T.
    fn marks(t: &Tree) -> Marks {
        let read = |m: String| std::fs::read_to_string(t.p(&m)).ok();
        (
            read(status::restore_started_marker()),
            read(status::restore_done_marker()),
            t.p(&status::boot_marker()).exists(),
        )
    }

    /// The restore stage run by a stand-in for `run_cmd` that records each
    /// step's command line and the marks as it ran, and fails the steps
    /// FAIL picks.  Its exit, standard error, and what it saw.
    fn restore_with(
        t: &Tree,
        s: &Sys,
        lay: &Layout,
        json: bool,
        fail: &dyn Fn(&str, &[String]) -> bool,
    ) -> (u8, String, Vec<(String, Marks)>) {
        let mut a = vec![
            "--map",
            "vda=vdb",
            "--repo",
            "/srv/r",
            "--password-file",
            "/k/pw",
            "--only",
            "restore",
            "--commit",
        ];
        if json {
            a.push("--json");
        }
        let seen = RefCell::new(Vec::new());
        let step = |c: &Cmd, _: &mut Option<std::fs::File>, _: &Sys| {
            let line = c.shell();
            let before: Vec<String> = seen
                .borrow()
                .iter()
                .map(|x: &(String, Marks)| x.0.clone())
                .collect();
            seen.borrow_mut().push((line.clone(), marks(t)));
            if fail(&line, &before) {
                Err("it failed".to_string())
            } else {
                Ok(Vec::new())
            }
        };
        let (code, _, err) = run_with(lay, &opts_of(&a), s, &[Stage::Restore], &step);
        (code, err, seen.into_inner())
    }

    /// A copy is complete only once its last command (the atimes' capture,
    /// after the archiver) has run and its read-back passed: every step
    /// runs with the start mark and no complete one, the boot mark of the
    /// tree before gone; a failed read-back or a step after the archiver
    /// failing leaves no complete mark.  Text mode the same.
    #[test]
    fn the_complete_mark_comes_last() {
        let (t, s, l) = restored("load-exec-done", "/usr/sbin/rustic-bko");
        t.file("/usr/sbin/rustic-bko", "");
        let lay = Layout::from_json(crate::json::parse(&l).unwrap()).unwrap();
        let mark = status::restore_mark_text(
            &lay,
            &crate::load::target::prepare(&lay, &opts_of(&["--map", "vda=vdb"]), &s).mapping,
        );
        let archiver = |l: &str| l.contains("rustic-bko --repository /srv/r");
        let earlier = || {
            t.file(&status::restore_done_marker(), "an earlier copy's\n")
                .file(&status::boot_marker(), "");
            let _ = std::fs::remove_file(t.p(&status::restore_started_marker()));
        };
        for json in [false, true] {
            // It runs to its end.
            earlier();
            let (code, err, seen) = restore_with(&t, &s, &lay, json, &|_, _| false);
            assert_eq!(code, EXIT_OK, "{json}: {err}");
            let lines: Vec<&str> = seen.iter().map(|(l, _)| l.as_str()).collect();
            let at = lines
                .iter()
                .position(|l| archiver(l))
                .unwrap_or_else(|| panic!("{json}: no archiver: {lines:?}"));
            assert!(
                at + 1 < lines.len() && lines.last().unwrap().contains("atime"),
                "{json}: {lines:?}"
            );
            for (l, m) in &seen {
                assert_eq!(m, &(Some(mark.clone()), None, false), "{json}: {l}");
            }
            assert_eq!(
                marks(&t),
                (Some(mark.clone()), Some(mark.clone()), false),
                "{json}"
            );
            // A step after the archiver fails.
            earlier();
            let (code, err, seen) = restore_with(&t, &s, &lay, json, &|l, before| {
                !archiver(l) && before.iter().any(|b| archiver(b))
            });
            assert_eq!(code, EXIT_FAILED, "{json}: {err}");
            assert!(
                err.contains("bko-holt load: restore: it failed"),
                "{json}: {err}"
            );
            assert!(seen.iter().any(|(l, _)| archiver(l)), "{json}");
            assert_eq!(marks(&t), (Some(mark.clone()), None, false), "{json}");
            // Every step passes, but the tree doesn't show the system.
            earlier();
            std::fs::remove_file(t.p("/mnt/bko-target/etc/fstab")).unwrap();
            let (code, err, _) = restore_with(&t, &s, &lay, json, &|_, _| false);
            assert_eq!(code, EXIT_FAILED, "{json}: {err}");
            assert!(
                err.contains("restore ran, but the disks don't show it done"),
                "{json}: {err}"
            );
            assert_eq!(marks(&t), (Some(mark.clone()), None, false), "{json}");
            t.file("/mnt/bko-target/etc/fstab", "");
        }
    }

    /// A partition stage that runs removes both copy marks (the disks are
    /// new again), and boot.done only under --json; it records its table
    /// in SYS's `RUN_DIR`.
    #[test]
    fn the_partition_stage_clears_the_copy_marks() {
        let (t, s, l) = restored("load-exec-partition", "/usr/sbin/rustic-bko");
        t.file("/proc/self/mountinfo", "");
        let lay = Layout::from_json(crate::json::parse(&l).unwrap()).unwrap();
        let (record, text) =
            crate::load::target::prepare(&lay, &opts_of(&["--map", "vda=vdb"]), &s)
                .mapping
                .records
                .remove(0);
        for json in [false, true] {
            t.file(&status::restore_started_marker(), "x\n")
                .file(&status::restore_done_marker(), "x\n")
                .file(&status::boot_marker(), "");
            std::fs::remove_file(t.p(&record)).unwrap();
            let mut a = vec![
                "--map",
                "vda=vdb",
                "--erase",
                "/dev/vdb",
                "--only",
                "partition",
                "--commit",
            ];
            if json {
                a.push("--json");
            }
            let ran = Cell::new(0);
            let step = |_: &Cmd, _: &mut Option<std::fs::File>, _: &Sys| {
                ran.set(ran.get() + 1);
                Ok(Vec::new())
            };
            let (code, out, err) = run_with(&lay, &opts_of(&a), &s, &[Stage::Partition], &step);
            assert_eq!(code, EXIT_OK, "{json}: {out}{err}");
            assert!(ran.get() > 0, "{json}");
            assert_eq!(marks(&t), (None, None, !json), "{json}");
            assert_eq!(
                std::fs::read_to_string(t.p(&record)).ok().as_deref(),
                Some(text.as_str()),
                "{json}"
            );
        }
    }

    // ---- the events (RT10f, RT10g) ----------------------------------------

    use crate::json::Value;
    use crate::testutil::Tree;
    use std::cell::{Cell, RefCell};

    /// `run_core` with STEP and STOP, its events read back: the exit, the
    /// events, standard error.  Each event is one line within the
    /// reader's limit.
    fn events_with(
        lay: &Layout,
        a: &[&str],
        s: &Sys,
        stages: &[Stage],
        step: Step<'_>,
        stop: &dyn Fn() -> bool,
    ) -> (u8, Vec<Value>, String) {
        let lines = RefCell::new(Vec::new());
        let (code, err) = {
            let mut sink = |l: &str| lines.borrow_mut().push(l.to_string());
            let (code, _, err) = run_core(
                lay,
                &opts_of(a),
                s,
                stages,
                step,
                &mut Events::to(&mut sink, stop),
            );
            (code, err)
        };
        let evs = lines
            .into_inner()
            .iter()
            .map(|l| {
                assert!(
                    l.ends_with('\n')
                        && l.matches('\n').count() == 1
                        && l.len() <= jsonout::EVENT_LINE_MAX,
                    "{l}"
                );
                crate::json::parse(l).unwrap_or_else(|e| panic!("{}: {l}", e.message))
            })
            .collect();
        (code, evs, err)
    }

    fn names(evs: &[Value]) -> Vec<&str> {
        evs.iter().map(|e| e.get_str("event").unwrap()).collect()
    }

    fn of_kind<'a>(evs: &'a [Value], kind: &str) -> Vec<&'a Value> {
        evs.iter()
            .filter(|e| e.get_str("event") == Some(kind))
            .collect()
    }

    /// A private password file at /k/pw, as bko-rescue makes them.
    fn private_pw(t: &Tree) {
        use std::os::unix::fs::PermissionsExt;
        t.file("/k/pw", "pw");
        std::fs::set_permissions(t.p("/k/pw"), std::fs::Permissions::from_mode(0o600)).unwrap();
        std::fs::set_permissions(t.p("/k"), std::fs::Permissions::from_mode(0o700)).unwrap();
    }

    fn layout_of(l: &str) -> Layout {
        Layout::from_json(crate::json::parse(l).unwrap()).unwrap()
    }

    /// The start event, then the stream's own order: every stage it names
    /// either skipped or run; inside a stage cmd, (progress, warning),
    /// `cmd_done`; `stage_done` or failed; end last, once.
    fn assert_order(evs: &[Value]) {
        let n = names(evs);
        assert_eq!(n.first(), Some(&"start"), "{n:?}");
        assert_eq!(n.last(), Some(&"end"), "{n:?}");
        assert_eq!(
            n.iter().filter(|e| **e == "start" || **e == "end").count(),
            2,
            "{n:?}"
        );
        let start = &evs[0];
        assert_eq!(
            (start.get_str("format"), start.get_u64("schema")),
            (Some("bko-holt-events"), Some(1))
        );
        assert_eq!(start.get_u64("pid"), Some(u64::from(std::process::id())));
        let mut in_cmd = false;
        for e in &evs[1..evs.len() - 1] {
            match e.get_str("event").unwrap() {
                "cmd" => {
                    assert!(!in_cmd, "{n:?}");
                    in_cmd = e.get_bool("would") != Some(true);
                }
                // Each the running command's.
                "progress" | "warning" => assert!(in_cmd, "{n:?}"),
                "stopping" => {}
                "cmd_done" => {
                    assert!(in_cmd, "{n:?}");
                    in_cmd = false;
                }
                "stage" | "skip" | "stage_done" | "failed" | "problems" => {
                    assert!(!in_cmd, "{n:?}")
                }
                other => panic!("{other}: {n:?}"),
            }
        }
    }

    /// Without --commit, under --json: every stage the run asks for, boot
    /// and close too (J11), its commands as `would`, no `cmd_done`, each stage
    /// done unchecked; nothing run; end 0.  Text mode, as ever, stops at boot.
    #[test]
    fn a_dry_run_ticks_through_every_stage() {
        let t = rescue("load-exec-ev-dry", &[]);
        for p in [
            "rustic-bko",
            "grub-install",
            "update-grub",
            "update-initramfs",
            "efibootmgr",
        ] {
            t.file(&format!("/usr/sbin/{p}"), "");
        }
        private_pw(&t);
        let s = Sys::new(
            t.root(),
            Box::new(crate::sys::FakeRunner::default().with(
                "/usr/sbin/rustic-bko --version",
                "rustic 0.12.0+backotter.4\n",
            )),
            true,
        );
        let lay = layout_of(&crate::load::layout::sample::bios_mbr());
        let a = [
            "--map",
            "vda=vdb",
            "--repo",
            "/srv/r",
            "--password-file",
            "/k/pw",
            "--through",
            "close",
            "--json",
        ];
        let never = || false;
        let (code, evs, err) = events_with(
            &lay,
            &a,
            &s,
            &Stage::WRITING,
            &|_, _, _, _, _| panic!("a dry run runs nothing"),
            &never,
        );
        assert_eq!(code, EXIT_OK, "{err}{evs:?}");
        assert_order(&evs);
        let start = &evs[0];
        assert_eq!(
            (start.get_bool("commit"), start.get("log")),
            (Some(false), Some(&Value::Null))
        );
        let stages: Vec<&str> = start
            .get_arr("stages")
            .unwrap()
            .iter()
            .filter_map(Value::as_str)
            .collect();
        assert_eq!(stages, Stage::WRITING.map(|s| s.name()));
        let p = plan::check(&lay, &opts_of(&a), &s);
        for st in Stage::WRITING {
            assert_eq!(
                start.u64_at(&["stage_commands", st.name()]),
                Some(p.built.stage(st).len() as u64),
                "{st:?}"
            );
            let seen = evs.iter().any(|e| {
                matches!(e.get_str("event"), Some("stage" | "skip"))
                    && e.get_str("stage") == Some(st.name())
            });
            assert!(seen, "{st:?}: {:?}", names(&evs));
        }
        assert!(start.get_str("plan_sha256").is_some_and(|h| h.len() == 64));
        // boot ran as a dry run would.
        assert!(
            of_kind(&evs, "stage")
                .iter()
                .any(|e| e.get_str("stage") == Some("boot")),
            "{:?}",
            names(&evs)
        );
        let cmds = of_kind(&evs, "cmd");
        assert!(!cmds.is_empty() && cmds.iter().all(|c| c.get_bool("would") == Some(true)));
        assert!(cmds.iter().any(|c| c.get_str("stage") == Some("boot")));
        assert!(of_kind(&evs, "cmd_done").is_empty());
        assert!(
            of_kind(&evs, "stage_done")
                .iter()
                .all(|d| d.get_bool("checked") == Some(false))
        );
        // n of of within each stage.
        for e in &cmds {
            let st = Stage::parse(e.get_str("stage").unwrap()).unwrap();
            assert_eq!(e.get_u64("of"), Some(p.built.stage(st).len() as u64));
        }
        // The archiver's step in restore names its part.
        let arch: Vec<&&Value> = cmds
            .iter()
            .filter(|c| c.get("archiver").is_some_and(|a| !a.is_null()))
            .collect();
        assert!(!arch.is_empty() && arch.iter().all(|c| c.get_str("stage") == Some("restore")));
        assert_eq!(arch[0].u64_at(&["archiver", "part"]), Some(1));
        assert_eq!(
            arch[0].u64_at(&["archiver", "parts"]),
            Some(arch.len() as u64)
        );
        let end = evs.last().unwrap();
        assert_eq!(
            (end.get_u64("exit"), end.get_bool("stopped")),
            (Some(0), Some(false))
        );
        // Text mode, the same arguments but --json: stops at boot, as ever.
        let (code, _, err) = run(&lay, &opts_of(&a[..a.len() - 1]), &s, &Stage::WRITING);
        assert_eq!(code, EXIT_FAILED, "{err}");
        assert!(
            err.contains("bko-holt load: boot: /mnt/bko-target/etc/os-release isn't there")
                || err.contains("isn't there; restore the system into"),
            "{err}"
        );
    }

    /// With --commit: each step a cmd and its `cmd_done`, the stage done and
    /// checked, end 0, the lock held while it ran and gone after.
    #[test]
    fn a_commit_run_reports_each_step() {
        let (t, s, l) = restored("load-exec-ev-ok", "/usr/sbin/rustic-bko");
        t.file("/usr/sbin/rustic-bko", "");
        let lay = layout_of(&l);
        let a = [
            "--map",
            "vda=vdb",
            "--repo",
            "/srv/r",
            "--password-file",
            "/k/pw",
            "--only",
            "restore",
            "--commit",
            "--json",
        ];
        let never = || false;
        let locked = Cell::new(true);
        let (code, evs, err) = events_with(
            &lay,
            &a,
            &s,
            &[Stage::Restore],
            &|_, _, _, _, _| {
                locked.set(locked.get() && t.p(&lock_path()).exists());
                Ok(Vec::new())
            },
            &never,
        );
        assert_eq!(code, EXIT_OK, "{err}{evs:?}");
        assert_order(&evs);
        assert!(locked.get());
        assert!(!t.p(&lock_path()).exists());
        assert_eq!(
            evs[0].get_str("log"),
            Some(format!("{RUN_DIR}/log/load.log").as_str())
        );
        assert_eq!(evs[0].get_bool("commit"), Some(true));
        let cmds = of_kind(&evs, "cmd");
        let done = of_kind(&evs, "cmd_done");
        assert_eq!(cmds.len(), done.len());
        assert!(cmds.iter().all(|c| c.get_bool("would") == Some(false)));
        for (i, (c, d)) in cmds.iter().zip(&done).enumerate() {
            assert_eq!(
                (c.get_u64("n"), d.get_u64("n")),
                (Some(i as u64 + 1), Some(i as u64 + 1))
            );
            assert_eq!(c.get_u64("of"), Some(cmds.len() as u64));
            let want = if c.get_str("kind") == Some("run") {
                Value::UInt(0)
            } else {
                Value::Null
            };
            assert_eq!(d.get("status"), Some(&want));
        }
        let sd = of_kind(&evs, "stage_done");
        assert_eq!(sd.len(), 1);
        assert_eq!(sd[0].get_bool("checked"), Some(true));
        assert!(t.p(&status::restore_done_marker()).exists());
        // The archiver's step: kind run, its argv the --json run's.
        let arch = cmds
            .iter()
            .find(|c| c.at(&["archiver", "part"]).is_some())
            .unwrap();
        let argv: Vec<&str> = arch
            .get_arr("argv")
            .unwrap()
            .iter()
            .filter_map(Value::as_str)
            .collect();
        assert!(
            argv.windows(3)
                .any(|w| w == ["--json-progress", "--progress-interval", "1s"]),
            "{argv:?}"
        );
    }

    /// A `warns` command's warnings: inside it, before its `cmd_done`, and
    /// on standard error as text mode prints them.  The lock is gone by the
    /// time `end` is written.
    #[test]
    fn warnings_come_before_cmd_done() {
        let (t, s, l) = restored("load-exec-ev-warns", "/usr/sbin/rustic-bko");
        t.file("/usr/sbin/rustic-bko", "");
        let lay = layout_of(&l);
        let a = [
            "--map",
            "vda=vdb",
            "--repo",
            "/srv/r",
            "--password-file",
            "/k/pw",
            "--only",
            "restore",
            "--commit",
            "--json",
        ];
        let never = || false;
        let first = Cell::new(true);
        let step: Step<'_> = &|_, _, _, _, _| {
            Ok(if first.replace(false) {
                vec!["x: warning: one".to_string()]
            } else {
                Vec::new()
            })
        };
        let (code, evs, err) = events_with(&lay, &a, &s, &[Stage::Restore], step, &never);
        assert_eq!(code, EXIT_OK, "{err}{evs:?}");
        assert_order(&evs);
        let n = names(&evs);
        let w = n.iter().position(|e| *e == "warning").unwrap();
        assert_eq!(&n[w - 1..=w + 1], ["cmd", "warning", "cmd_done"], "{n:?}");
        assert_eq!(
            (
                evs[w].get_str("source"),
                evs[w].get_u64("n"),
                evs[w].get_str("text")
            ),
            (Some("cmd"), Some(1), Some("x: warning: one"))
        );
        assert!(
            err.contains("bko-holt load: restore: x: warning: one\n"),
            "{err}"
        );
        // The lock, as each line is written.
        let lines = RefCell::new(Vec::new());
        let mut sink = |l: &str| {
            lines
                .borrow_mut()
                .push((l.to_string(), t.p(&lock_path()).exists()))
        };
        first.set(true);
        let (code, _, err) = run_core(
            &lay,
            &opts_of(&a),
            &s,
            &[Stage::Restore],
            step,
            &mut Events::to(&mut sink, &never),
        );
        assert_eq!(code, EXIT_OK, "{err}");
        let lines = lines.into_inner();
        assert!(lines[..lines.len() - 1].iter().all(|(_, held)| *held));
        assert!(
            lines
                .last()
                .is_some_and(|(l, held)| l.contains("\"event\":\"end\"") && !held),
            "{lines:?}"
        );
    }

    /// A step that fails: its `cmd_done`, then failed with its code, then end
    /// 1; no `stage_done`, no complete mark.  Problems: nothing runs.
    #[test]
    fn a_failure_and_problems_end_the_stream() {
        let (t, s, l) = restored("load-exec-ev-fail", "/usr/sbin/rustic-bko");
        t.file("/usr/sbin/rustic-bko", "");
        let lay = layout_of(&l);
        let a = [
            "--map",
            "vda=vdb",
            "--repo",
            "/srv/r",
            "--password-file",
            "/k/pw",
            "--only",
            "restore",
            "--commit",
            "--json",
        ];
        let never = || false;
        let (code, evs, err) = events_with(
            &lay,
            &a,
            &s,
            &[Stage::Restore],
            &|c, _, _, _, _| {
                if c.archiver && c.argv.iter().any(|x| x == "restore") {
                    Err(StepFail {
                        text: "rustic-bko failed (exit status: 1):\n    boom".into(),
                        facts: FailFacts {
                            status: Some(1),
                            signal: None,
                            tail: vec!["boom".into()],
                        },
                        stopped: false,
                    })
                } else {
                    Ok(Vec::new())
                }
            },
            &never,
        );
        assert_eq!(code, EXIT_FAILED);
        assert_order(&evs);
        let n = names(&evs);
        assert_eq!(&n[n.len() - 3..], ["cmd_done", "failed", "end"], "{n:?}");
        assert!(of_kind(&evs, "stage_done").is_empty());
        let f = of_kind(&evs, "failed")[0];
        assert_eq!(f.get_str("code"), Some("archiver_failed"));
        assert_eq!(
            (f.get_str("stage"), f.get_str("program")),
            (Some("restore"), Some("rustic-bko"))
        );
        assert_eq!(
            f.get_str("text"),
            Some("rustic-bko failed (exit status: 1):\n    boom")
        );
        assert_eq!(f.get_u64("status"), Some(1));
        assert_eq!(f.get_arr("tail").map(|t| t.len()), Some(1));
        assert_eq!(
            f.get_u64("n"),
            of_kind(&evs, "cmd").last().unwrap().get_u64("n")
        );
        assert_eq!(evs.last().unwrap().get_u64("exit"), Some(1));
        assert!(
            err.contains("bko-holt load: restore: rustic-bko failed"),
            "{err}"
        );
        assert!(!t.p(&status::restore_done_marker()).exists());
        // Problems: no password file under --json.
        let a = [
            "--map", "vda=vdb", "--repo", "/srv/r", "--only", "restore", "--commit", "--json",
        ];
        let (code, evs, _) = events_with(
            &lay,
            &a,
            &s,
            &[Stage::Restore],
            &|_, _, _, _, _| panic!("nothing runs"),
            &never,
        );
        assert_eq!(code, EXIT_PLAN_PROBLEMS);
        assert_eq!(names(&evs), ["start", "problems", "end"]);
        assert!(
            evs[1]
                .get_arr("problems")
                .unwrap()
                .iter()
                .any(|p| p.get_str("code") == Some("password_needed"))
        );
        assert_eq!(
            (evs[1].get_u64("exit"), evs[2].get_u64("exit")),
            (Some(4), Some(4))
        );
        assert!(!t.p(&lock_path()).exists());
    }

    /// A stop asked for while a step runs: that step ends, then stopping,
    /// and end with stopped, exit 5; no later step, no `stage_done`, no
    /// complete mark.
    #[test]
    fn a_stop_ends_between_commands() {
        let (t, s, l) = restored("load-exec-ev-stop", "/usr/sbin/rustic-bko");
        t.file("/usr/sbin/rustic-bko", "");
        let lay = layout_of(&l);
        let a = [
            "--map",
            "vda=vdb",
            "--repo",
            "/srv/r",
            "--password-file",
            "/k/pw",
            "--only",
            "restore",
            "--commit",
            "--json",
        ];
        let asked = Cell::new(false);
        let ran = Cell::new(0);
        let stop = || asked.get();
        let (code, evs, err) = events_with(
            &lay,
            &a,
            &s,
            &[Stage::Restore],
            &|_, _, _, _, _| {
                ran.set(ran.get() + 1);
                asked.set(true);
                Ok(Vec::new())
            },
            &stop,
        );
        assert!(
            plan::check(&lay, &opts_of(&a), &s)
                .built
                .stage(Stage::Restore)
                .len()
                > 1
        );
        assert_eq!(code, EXIT_STOPPED, "{err}");
        assert_order(&evs);
        assert_eq!(ran.get(), 1);
        let n = names(&evs);
        assert_eq!(
            &n[n.len() - 4..],
            ["cmd", "cmd_done", "stopping", "end"],
            "{n:?}"
        );
        assert_eq!(of_kind(&evs, "cmd").len(), 1);
        assert!(of_kind(&evs, "stage_done").is_empty() && of_kind(&evs, "failed").is_empty());
        let end = evs.last().unwrap();
        assert_eq!(
            (end.get_u64("exit"), end.get_bool("stopped")),
            (Some(5), Some(true))
        );
        assert_eq!(
            of_kind(&evs, "stopping")[0].get_str("signal"),
            Some("SIGTERM")
        );
        assert!(err.contains("stopped on request"), "{err}");
        assert!(!t.p(&status::restore_done_marker()).exists());
        assert!(t.p(&status::restore_started_marker()).exists());
        assert!(!t.p(&lock_path()).exists());
        let log = std::fs::read_to_string(t.p(&format!("{RUN_DIR}/log/load.log"))).unwrap();
        assert!(log.contains("STOPPED: on request"), "{log}");
        // Asked for before the first: nothing runs.
        asked.set(true);
        ran.set(0);
        let (code, evs, _) = events_with(
            &lay,
            &a,
            &s,
            &[Stage::Restore],
            &|_, _, _, _, _| panic!("nothing runs"),
            &stop,
        );
        assert_eq!(code, EXIT_STOPPED);
        assert_eq!(names(&evs), ["start", "stopping", "end"]);
    }

    /// A fake archiver at T's /fake-rustic: rustic-bko's status lines on
    /// its standard output among its text, a `[WARN] restore` line in
    /// colour on its standard error; then it ends, or with WAIT waits up to
    /// 30 s for SIGINT, which it takes as rustic-bko does (exit 130).
    fn fake_archiver(t: &Tree, wait: bool) -> String {
        use std::os::unix::fs::PermissionsExt;
        let status = |i: u32| {
            format!(
                "{{\"message_type\":\"status\",\"seconds_elapsed\":{i},\"total_bytes\":300,\"bytes_done\":{},\"error_count\":0}}",
                i * 100
            )
        };
        let mut sh = String::from(
            "#!/bin/sh\ntrap 'echo interrupted >&2; exit 130' INT\necho 'Files:  3 to restore'\n",
        );
        for i in 1..=3 {
            sh += &format!("echo '{}'\n", status(i));
        }
        sh += "printf '\\033[33m[WARN]\\033[0m restore etc/x: setting extended attributes failed: nope\\n' >&2\necho 'restore done.'\n";
        sh += if wait {
            "i=0\nwhile [ $i -lt 300 ]; do sleep 0.1; i=$((i+1)); done\nexit 7\n"
        } else {
            "exit 0\n"
        };
        t.file("/fake-rustic", sh);
        std::fs::set_permissions(t.p("/fake-rustic"), std::fs::Permissions::from_mode(0o755))
            .unwrap();
        t.p("/fake-rustic").to_string_lossy().into_owned()
    }

    /// `run_cmd_json` of C with T's log, its events, STOP: the result, the
    /// events, the log.
    fn stream(
        t: &Tree,
        c: &Cmd,
        stop: &dyn Fn(&[Value]) -> bool,
    ) -> (Result<Vec<String>, StepFail>, Vec<Value>, String) {
        let path = t.p("/stream.log");
        let _ = std::fs::remove_file(&path);
        let mut log = Some(std::fs::File::create(&path).unwrap());
        let seen = RefCell::new(Vec::new());
        let r = {
            let mut sink = |l: &str| seen.borrow_mut().push(crate::json::parse(l).unwrap());
            let asked = || stop(&seen.borrow());
            let mut ev = Events::to(&mut sink, &asked);
            run_cmd_json(
                c,
                &mut log,
                &Sys::host(),
                &mut ev,
                &At {
                    stage: Stage::Restore,
                    n: 3,
                    part: Some((1, 2)),
                },
            )
        };
        drop(log);
        (
            r,
            seen.into_inner(),
            std::fs::read_to_string(&path).unwrap(),
        )
    }

    /// The archiver's status lines become progress (the first at once, the
    /// last always; part and parts the step's), its [WARN] lines warnings
    /// with their item, colour stripped; the log has its text and the last
    /// status line only.  The same inside an sh -c wrapper.
    #[test]
    fn the_archivers_progress_and_warnings() {
        let t = Tree::new("load-exec-ev-arch");
        let fake = fake_archiver(&t, false);
        for c in [
            Cmd::new(&[fake.as_str()]).archiver(),
            Cmd::new(&["sh", "-c", &format!("{fake}; echo after $? >&2")]).archiver(),
        ] {
            let (r, evs, log) = stream(&t, &c, &|_| false);
            assert_eq!(r, Ok(Vec::new()), "{log}");
            let prog = of_kind(&evs, "progress");
            assert!(prog.len() >= 2 && prog.len() <= 3, "{evs:?}");
            assert_eq!(prog[0].get_u64("bytes"), Some(100));
            let last = prog.last().unwrap();
            assert_eq!(
                (
                    last.get_u64("bytes"),
                    last.get_u64("total_bytes"),
                    last.get_u64("secs")
                ),
                (Some(300), Some(300), Some(3))
            );
            assert!(prog.iter().all(|p| p.get_str("stage") == Some("restore")
                && p.get_u64("n") == Some(3)
                && p.get_u64("part") == Some(1)
                && p.get_u64("parts") == Some(2)));
            let w = of_kind(&evs, "warning");
            assert_eq!(w.len(), 1);
            assert_eq!(
                w[0].get_str("text"),
                Some("restore etc/x: setting extended attributes failed: nope")
            );
            assert_eq!(
                (w[0].get_str("item"), w[0].get_str("source")),
                (Some("etc/x"), Some("archiver"))
            );
            assert!(
                prog.iter()
                    .all(|p| p.get_u64("errors").is_some_and(|e| e <= 1))
            );
            assert!(
                log.contains("Files:  3 to restore\n") && log.contains("restore done.\n"),
                "{log}"
            );
            assert!(
                log.contains("\"bytes_done\":300")
                    && !log.contains("\"bytes_done\":100")
                    && !log.contains("\"bytes_done\":200"),
                "{log}"
            );
            assert!(log.contains("[WARN]"), "{log}");
        }
        // A step that isn't the archiver's: its JSON lines are its output.
        let (r, evs, log) = stream(&t, &Cmd::new(&[fake.as_str()]), &|_| false);
        assert_eq!(r, Ok(Vec::new()));
        assert!(evs.is_empty(), "{evs:?}");
        assert!(log.contains("\"bytes_done\":100"), "{log}");
    }

    /// A stop while the archiver runs: SIGINT to its process group, inside
    /// an sh -c wrapper too; the step is stopped, whatever its exit.
    #[test]
    fn a_stop_interrupts_the_archiver() {
        let t = Tree::new("load-exec-ev-sigint");
        let fake = fake_archiver(&t, true);
        // Asked for once the last status has come through.
        let asked = |evs: &[Value]| {
            evs.iter()
                .any(|e| e.get_str("event") == Some("progress") && e.get_u64("bytes") == Some(300))
        };
        for c in [
            Cmd::new(&[fake.as_str()]).archiver(),
            Cmd::new(&["sh", "-c", &format!("{fake}; echo after $? >&2")]).archiver(),
        ] {
            let t0 = Instant::now();
            let (r, evs, log) = stream(&t, &c, &asked);
            let f = r.unwrap_err();
            assert!(f.stopped, "{f:?}");
            assert!(t0.elapsed() < Duration::from_secs(20), "{:?}", t0.elapsed());
            assert!(
                f.facts.status == Some(130) || f.facts.signal == Some(crate::sys::SIGINT),
                "{f:?}: {log}"
            );
            assert_eq!(of_kind(&evs, "stopping").len(), 1);
            assert!(
                log.contains("STOPPING: SIGINT to the archiver's process group"),
                "{log}"
            );
            assert!(log.contains("interrupted"), "{log}");
        }
        // A step that isn't the archiver's runs on to its end.
        let (r, evs, _) = stream(
            &t,
            &Cmd::new(&["sh", "-c", "sleep 0.3; echo done"]),
            &|_| true,
        );
        assert_eq!(r, Ok(Vec::new()));
        assert_eq!(names(&evs), ["stopping"]);
    }

    /// Under --json a step's output is read as it comes: a child that
    /// fills both pipes, and reads its input only after, runs to its end,
    /// all of it logged (in text mode too, its input written beside the
    /// reading); with no input given, standard input is /dev/null; a
    /// failure is worded as `run_cmd` words it, given input or not.
    #[test]
    fn streamed_children() {
        let t = Tree::new("load-exec-ev-pipes");
        let both = "i=0; while [ $i -lt 3000 ]; do echo oooooooooooooooooooooooooooooooooooooooooooooooooooooooooooooooooooooooooooooooooooooooooooooooo; echo eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee >&2; i=$((i+1)); done; wc -c";
        let input = "x".repeat(300_000);
        let (r, _, log) = stream(
            &t,
            &Cmd::new(&["sh", "-c", both]).input(input.clone()),
            &|_| false,
        );
        assert_eq!(r, Ok(Vec::new()));
        assert_eq!(log.matches("oooo\n").count(), 3000);
        assert_eq!(log.matches("eeee\n").count(), 3000);
        assert!(
            log.lines().any(|l| l.trim() == "300000"),
            "{}",
            &log[log.len().saturating_sub(300)..]
        );
        // Text mode the same.
        let path = t.p("/text.log");
        let mut text_log = Some(std::fs::File::create(&path).unwrap());
        assert_eq!(
            run_cmd(
                &Cmd::new(&["sh", "-c", both]).input(input.clone()),
                &mut text_log,
                &Sys::host()
            ),
            Ok(Vec::new())
        );
        drop(text_log);
        let log = std::fs::read_to_string(&path).unwrap();
        assert_eq!(
            (log.matches("oooo\n").count(), log.matches("eeee\n").count()),
            (3000, 3000)
        );
        assert!(
            log.lines().any(|l| l.trim() == "300000"),
            "{}",
            &log[log.len().saturating_sub(300)..]
        );
        // One that fails without reading its input: its exit and its
        // stderr, in both modes, not the write's broken pipe; one that
        // succeeds without reading it all: the write.
        let c =
            Cmd::new(&["sh", "-c", "echo 'bad partition line' >&2; exit 4"]).input(input.clone());
        let e = run_cmd(&c, &mut None, &Sys::host()).unwrap_err();
        assert_eq!(e, "sh failed (exit status: 4):\n    bad partition line");
        let (r, _, _) = stream(&t, &c, &|_| false);
        let f = r.unwrap_err();
        assert_eq!((f.text, f.facts.status), (e, Some(4)));
        let c = Cmd::new(&["sh", "-c", "exit 0"]).input(input);
        let e = run_cmd(&c, &mut None, &Sys::host()).unwrap_err();
        assert!(e.starts_with("sh: writing its input: "), "{e}");
        assert_eq!(stream(&t, &c, &|_| false).0.unwrap_err().text, e);
        let (r, _, log) = stream(
            &t,
            &Cmd::new(&["sh", "-c", "if read x; then echo got; else echo eof; fi"]),
            &|_| false,
        );
        assert_eq!(r, Ok(Vec::new()));
        assert!(log.contains("\neof\n"), "{log}");
        let c = Cmd::new(&["sh", "-c", "echo a >&2; echo 'b: warning: w' >&2; exit 4"]);
        let (r, _, _) = stream(&t, &c, &|_| false);
        let f = r.unwrap_err();
        assert_eq!(Err(f.text.clone()), run_cmd(&c, &mut None, &Sys::host()));
        assert_eq!(
            (f.facts.status, f.facts.signal, f.facts.tail.clone()),
            (
                Some(4),
                None,
                vec!["a".to_string(), "b: warning: w".to_string()]
            )
        );
        assert!(!f.stopped);
        let (r, _, _) = stream(
            &t,
            &Cmd::new(&["sh", "-c", "echo 'b: warning: w' >&2"]).warns(),
            &|_| false,
        );
        assert_eq!(r, Ok(vec!["b: warning: w".to_string()]));
        let (r, _, _) = stream(&t, &Cmd::new(&["/nonexistent/program"]), &|_| false);
        assert!(r.unwrap_err().text.starts_with("/nonexistent/program: "));
    }

    /// Under --json a private write's text never reaches the log: its size
    /// and SHA-256 do.  Text mode logs it, as ever.
    #[test]
    fn a_private_write_is_logged_by_its_hash() {
        let t = Tree::new("load-exec-ev-private");
        let f = t
            .p("/etc/lilo.conf.bko-holt")
            .to_string_lossy()
            .into_owned();
        let c = Cmd::write_file(&f, "password=secret\n".to_string()).private();
        let (r, _, log) = stream(&t, &c, &|_| false);
        assert_eq!(r, Ok(Vec::new()));
        assert_eq!(std::fs::read_to_string(&f).unwrap(), "password=secret\n");
        assert!(!log.contains("secret"), "{log}");
        assert!(
            log.contains(&format!(
                "# (16 bytes, sha256 {}, private: not shown)",
                crate::bytes::sha256_hex(b"password=secret\n")
            )),
            "{log}"
        );
        let path = t.p("/text.log");
        let mut text_log = Some(std::fs::File::create(&path).unwrap());
        assert_eq!(run_cmd(&c, &mut text_log, &Sys::host()), Ok(Vec::new()));
        drop(text_log);
        // Text mode logs the text, but never a password (RustAudit1 HD1-01).
        let text = std::fs::read_to_string(&path).unwrap();
        assert!(
            text.contains("password=<password>\n") && !text.contains("secret"),
            "{text}"
        );
    }

    /// The codes of a failed step.
    #[test]
    fn failure_codes() {
        let t = Tree::new("load-exec-ev-codes");
        let s = Sys::new(t.root(), Box::new(crate::sys::FakeRunner::default()), true);
        let exit = |n: i32| StepFail {
            facts: FailFacts {
                status: Some(n),
                ..FailFacts::default()
            },
            ..StepFail::default()
        };
        let open = Cmd::new(&[
            "cryptsetup",
            "open",
            "--key-file",
            "/k/0",
            "/dev/vdb3",
            "cryptroot",
        ]);
        assert_eq!(
            failure_code(&open, Stage::Assemble, &exit(2), &s),
            FailureCode::WrongPassphrase
        );
        assert_eq!(
            failure_code(&open, Stage::Assemble, &exit(1), &s),
            FailureCode::CmdFailed
        );
        assert_eq!(
            failure_code(&open, Stage::Format, &exit(2), &s),
            FailureCode::CmdFailed
        );
        assert_eq!(
            failure_code(
                &Cmd::new(&["cryptsetup", "luksFormat", "/dev/vdb3"]),
                Stage::Assemble,
                &exit(2),
                &s
            ),
            FailureCode::CmdFailed
        );
        // No efivarfs in this tree, and no /sys/firmware/efi: BIOS, no
        // variables to write.  Only a step that writes them fails for
        // want of them: efibootmgr, an EFI grub-install without
        // --no-nvram.
        let code = |argv: &[&str]| failure_code(&Cmd::new(argv), Stage::Boot, &exit(1), &s);
        assert_eq!(code(&["/usr/sbin/efibootmgr", "-c"]), FailureCode::EfiVars);
        assert_eq!(
            code(&[
                "chroot",
                "/mnt/bko-target",
                "grub-install",
                "--target=x86_64-efi",
                "--efi-directory=/boot/efi"
            ]),
            FailureCode::EfiVars
        );
        assert_eq!(
            code(&[
                "chroot",
                "/mnt/bko-target",
                "grub2-install",
                "--target",
                "arm64-efi"
            ]),
            FailureCode::EfiVars
        );
        // A BIOS install (the embedding area too small, say), named or
        // GRUB's default here; an EFI one that writes no entry; BIOS
        // through GRUB_BIOS_NILFS2's wrapper: none touches NVRAM.
        assert_eq!(
            code(&[
                "chroot",
                "/mnt/bko-target",
                "grub-install",
                "--target=i386-pc",
                "/dev/vdb"
            ]),
            FailureCode::CmdFailed
        );
        assert_eq!(
            code(&["chroot", "/mnt/bko-target", "grub-install"]),
            FailureCode::CmdFailed
        );
        assert_eq!(
            code(&[
                "chroot",
                "/mnt/bko-target",
                "grub-install",
                "--target=x86_64-efi",
                "--efi-directory=/boot/efi",
                "--bootloader-id=debian",
                "--no-nvram"
            ]),
            FailureCode::CmdFailed
        );
        assert_eq!(
            code(&[
                "sh",
                "-c",
                "x",
                "sh",
                "/dev/vdb",
                "chroot",
                "/mnt/bko-target",
                "grub-install",
                "--target=i386-pc",
                "/dev/vdb"
            ]),
            FailureCode::CmdFailed
        );
        assert_eq!(
            code(&["chroot", "/mnt/bko-target", "update-grub"]),
            FailureCode::CmdFailed
        );
        // The program a failed step names.
        assert_eq!(
            jsonout::program_of(&Cmd::new(&[
                "chroot",
                "/mnt/bko-target",
                "/usr/sbin/grub-install",
                "x"
            ]))
            .as_deref(),
            Some("grub-install")
        );
        assert_eq!(
            jsonout::program_of(&Cmd::new(&["env", "A=1", "B=2", "mkfs.xfs", "/dev/vdb1"]))
                .as_deref(),
            Some("mkfs.xfs")
        );
        assert_eq!(
            jsonout::program_of(&Cmd::new(&["sh", "-c", "x"])).as_deref(),
            Some("sh")
        );
        assert_eq!(jsonout::program_of(&Cmd::note("n")), None);
        // With the variables there, the same steps fail as any other.
        t.file(
            "/proc/self/mountinfo",
            "30 20 0:30 / /sys/firmware/efi/efivars rw,nosuid - efivarfs efivarfs rw\n",
        );
        assert_eq!(
            failure_code(&Cmd::new(&["efibootmgr", "-c"]), Stage::Boot, &exit(1), &s),
            FailureCode::CmdFailed
        );
        assert_eq!(
            failure_code(
                &Cmd::new(&["grub-install", "--target=x86_64-efi"]),
                Stage::Boot,
                &exit(1),
                &s
            ),
            FailureCode::CmdFailed
        );
        assert_eq!(
            failure_code(
                &Cmd::new(&["sh", "-c", "x"]).archiver(),
                Stage::Restore,
                &exit(1),
                &s
            ),
            FailureCode::ArchiverFailed
        );
        // The archiver's restore options checked (an old rustic-bko): no
        // second try passes that.
        let help = "rustic-bko restore --help > /run/bko-holt/restore.help 2>&1; grep -q -- '--exact' /run/bko-holt/restore.help || { echo no >&2; exit 1; }";
        assert_eq!(
            failure_code(
                &Cmd::new(&["sh", "-c", help]).archiver(),
                Stage::Restore,
                &exit(1),
                &s
            ),
            FailureCode::CmdFailed
        );
        // As the builder makes it (garuda's plan: the check, the copy and
        // the shared snapshots' scripts).
        let plan = crate::json::parse(
            &std::fs::read_to_string(concat!(
                env!("CARGO_MANIFEST_DIR"),
                "/tests/corpus/garuda/plan.json"
            ))
            .unwrap(),
        )
        .unwrap();
        let mut checks = 0;
        for st in plan.get_arr("stages").unwrap() {
            for c in st
                .arr_at(&["commands"])
                .iter()
                .filter(|c| c.get_bool("archiver") == Some(true))
            {
                let argv: Vec<&str> = c
                    .arr_at(&["argv"])
                    .iter()
                    .filter_map(Value::as_str)
                    .collect();
                let help = argv.iter().any(|a| a.contains("restore --help"));
                checks += usize::from(help);
                let want = if help {
                    FailureCode::CmdFailed
                } else {
                    FailureCode::ArchiverFailed
                };
                assert_eq!(
                    failure_code(&Cmd::new(&argv).archiver(), Stage::Restore, &exit(1), &s),
                    want,
                    "{argv:?}"
                );
            }
        }
        assert_eq!(checks, 1);
        assert_eq!(
            failure_code(
                &Cmd::write_file("/x", String::new()),
                Stage::Boot,
                &exit(1),
                &s
            ),
            FailureCode::WriteFailed
        );
        assert_eq!(
            failure_code(
                &Cmd::read_back("/dev/vdb1", &Value::Null),
                Stage::Format,
                &exit(1),
                &s
            ),
            FailureCode::ReadbackFailed
        );
    }

    /// Long argv elements go by size and hash; no event line is longer
    /// than a reader takes.
    #[test]
    fn long_texts_are_bounded() {
        let big = "s".repeat(20_000);
        let c = Cmd::new(&["sh", "-c", big.as_str()]);
        let v = crate::json::parse(&jsonout::event_line(jsonout::cmd_event(
            "restore", 1, 1, &c, false, None,
        )))
        .unwrap();
        assert_eq!(v.at(&["argv", "0"]).and_then(Value::as_str), Some("sh"));
        assert_eq!(v.u64_at(&["argv", "2", "bytes"]), Some(20_000));
        assert_eq!(
            v.str_at(&["argv", "2", "sha256"]).map(str::to_string),
            Some(crate::bytes::sha256_hex(big.as_bytes()))
        );
        let many: Vec<String> = (0..10)
            .map(|i| format!("{i}{}", "a".repeat(15_000)))
            .collect();
        let c = Cmd::new(&many);
        let l = jsonout::event_line(jsonout::cmd_event("restore", 1, 1, &c, false, None));
        assert!(
            l.len() <= jsonout::EVENT_LINE_MAX && crate::json::parse(&l).is_ok(),
            "{}",
            l.len()
        );
    }

    /// The lock: one holder at a time, let go when its run ends, taken over
    /// from a process that's gone; text mode's --commit refuses while a
    /// live one is held, and a --json run fails with `lock_held`.
    #[test]
    fn one_load_at_a_time() {
        let (t, s, l) = restored("load-exec-ev-lock", "/usr/sbin/rustic-bko");
        t.file("/usr/sbin/rustic-bko", "");
        let me = std::process::id();
        let a = Lock::take(&s).unwrap();
        let text = std::fs::read_to_string(t.p(&lock_path())).unwrap();
        assert_eq!(
            crate::json::parse(text.trim()).unwrap().get_u64("pid"),
            Some(u64::from(me))
        );
        assert!(matches!(Lock::take(&s), Err(LockErr::Held(Some(p))) if p == me));
        drop(a);
        assert!(!t.p(&lock_path()).exists());
        // A holder that's gone, or another process under its pid.
        let mut c = Command::new("true").spawn().unwrap();
        let gone = c.id();
        c.wait().unwrap();
        for line in [
            format!("{{\"pid\":{gone},\"start\":null}}\n"),
            format!("{{\"pid\":{me},\"start\":\"not-this-one\"}}\n"),
            "garbage".to_string(),
        ] {
            t.file(&lock_path(), line.as_str());
            if line == "garbage" {
                // Torn and young: taken as being written.
                assert!(matches!(Lock::take(&s), Err(LockErr::Held(None))));
                continue;
            }
            if line.contains("not-this-one") && crate::sys::process_start(me).is_none() {
                continue;
            }
            let l2 = Lock::take(&s).unwrap_or_else(|e| panic!("{line}: {e:?}"));
            drop(l2);
        }
        // Held by a live load (this process, as it started).
        let held = Obj::new()
            .set("pid", me)
            .set("start", crate::sys::process_start(me))
            .build()
            .compact()
            + "\n";
        t.file(&lock_path(), held.as_str());
        let lay = layout_of(&l);
        let a = [
            "--map",
            "vda=vdb",
            "--repo",
            "/srv/r",
            "--password-file",
            "/k/pw",
            "--only",
            "restore",
            "--commit",
        ];
        let (code, out, err) = run_with(&lay, &opts_of(&a), &s, &[Stage::Restore], &|_, _, _| {
            panic!("nothing runs")
        });
        assert_eq!(
            (code, out.as_str(), err),
            (
                EXIT_FAILED,
                "",
                format!("bko-holt load: another bko-holt load is running: pid {me}\n")
            )
        );
        // Without --commit text mode doesn't look.
        let (code, _, err) = run_with(
            &lay,
            &opts_of(&a[..a.len() - 1]),
            &s,
            &[Stage::Restore],
            &|_, _, _| panic!("nothing runs"),
        );
        assert_eq!(code, EXIT_OK, "{err}");
        let mut j = a.to_vec();
        j.push("--json");
        let never = || false;
        let (code, evs, err) = events_with(
            &lay,
            &j,
            &s,
            &[Stage::Restore],
            &|_, _, _, _, _| panic!("nothing runs"),
            &never,
        );
        assert_eq!(code, EXIT_FAILED);
        assert_eq!(names(&evs), ["start", "failed", "end"]);
        assert_eq!(
            (evs[1].get_str("code"), evs[1].get_u64("pid")),
            (Some("lock_held"), Some(u64::from(me)))
        );
        // No step failed: no program, no argv.
        assert_eq!(
            (evs[1].get("program"), evs[1].get("argv")),
            (Some(&Value::Null), Some(&Value::Null))
        );
        assert!(err.contains("another bko-holt load is running"), "{err}");
        // Not ours to remove.
        assert_eq!(std::fs::read_to_string(t.p(&lock_path())).unwrap(), held);
        // Where the boot time is known (Haiku's), a lock older than the
        // boot is stale, even with a live pid.
        let at = t.p(&lock_path());
        let hour_ago = std::time::SystemTime::now() - Duration::from_secs(3600);
        assert_eq!(holder_since(&at, Some(hour_ago)), Holder::Live(Some(me)));
        std::fs::File::options()
            .write(true)
            .open(&at)
            .unwrap()
            .set_modified(hour_ago - Duration::from_secs(60))
            .unwrap();
        assert_eq!(holder_since(&at, Some(hour_ago)), Holder::Stale);
        assert_eq!(holder_since(&at, None), Holder::Live(Some(me)));
        // A takeover under way (its guard young): a stale lock waits for
        // it, and stays as it is.
        let stale = format!("{{\"pid\":{gone},\"start\":null}}\n");
        t.file(&lock_path(), stale.as_str());
        let guard = t.p(&format!("{}.takeover", lock_path()));
        t.file(&format!("{}.takeover", lock_path()), "");
        assert!(matches!(Lock::take(&s), Err(LockErr::Held(None))));
        assert_eq!(std::fs::read_to_string(&at).unwrap(), stale);
        // One left by a run that died taking over: gone after 10 s, and
        // the lock taken over, the guard let go.
        std::fs::File::options()
            .write(true)
            .open(&guard)
            .unwrap()
            .set_modified(std::time::SystemTime::now() - Duration::from_secs(60))
            .unwrap();
        let l3 = Lock::take(&s).unwrap();
        assert!(!guard.exists());
        assert_eq!(
            crate::json::parse(std::fs::read_to_string(&at).unwrap().trim())
                .unwrap()
                .get_u64("pid"),
            Some(u64::from(me))
        );
        drop(l3);
        assert!(!at.exists());
    }

    /// The environment variable `panic_child` runs under.
    const PANIC_CHILD: &str = "BKO_HOLT_TEST_PANIC_CHILD";

    /// In a child of its own (`a_panic_ends_the_stream`'s): armed, a run
    /// begun, then a panic of the main thread's while a command runs in a
    /// group of its own; or ("early") a panic before the run's `start`.
    #[test]
    fn panic_child() {
        let Some(mode) = std::env::var_os(PANIC_CHILD) else {
            return;
        };
        use std::os::unix::process::{CommandExt, ExitStatusExt};
        arm();
        let t = Tree::new("load-exec-ev-panic");
        let lock = t.p("/load.lock");
        t.file(
            "/load.lock",
            format!("{{\"pid\":{}}}\n", std::process::id()),
        );
        let early = mode == "early";
        let mut child = Command::new("sleep")
            .arg("60")
            .process_group(0)
            .stdin(Stdio::null())
            .spawn()
            .unwrap();
        let pgid = i32::try_from(child.id()).unwrap();
        hook::note(|h| {
            h.started = !early;
            h.commit = true;
            h.layout = Some((
                Some("lovelace".to_string()),
                Some("2026-11-22T01:00:11Z".to_string()),
            ));
            h.stage = Some("restore");
            h.n = Some(3);
            h.child = (!early).then_some(pgid);
            h.lock = Some(lock.clone());
        });
        let r = std::thread::Builder::new()
            .name("main".into())
            .spawn(|| panic!("the test's panic"))
            .unwrap()
            .join();
        assert!(r.is_err());
        assert!(!lock.exists());
        if early {
            child.kill().unwrap();
            child.wait().unwrap();
        } else {
            // The running command died with the run.
            assert_eq!(child.wait().unwrap().signal(), Some(crate::sys::SIGKILL));
        }
        println!("PANIC-CHILD-OK");
    }

    /// A panic under --json: `failed` with `internal` and the panic's
    /// text, then `end` 101, the running command killed and the lock let
    /// go; before the run's `start`, a `start` of the hook's own, with the
    /// run's --commit and layout.
    #[test]
    fn a_panic_ends_the_stream() {
        for mode in ["started", "early"] {
            let out = Command::new(std::env::current_exe().unwrap())
                .args([
                    "--exact",
                    "load::exec::tests::panic_child",
                    "--nocapture",
                    "--test-threads",
                    "1",
                ])
                .env(PANIC_CHILD, mode)
                .stdin(Stdio::null())
                .output()
                .unwrap();
            let text = String::from_utf8_lossy(&out.stdout).into_owned();
            assert!(
                out.status.success() && text.contains("PANIC-CHILD-OK"),
                "{mode}: {text}{}",
                String::from_utf8_lossy(&out.stderr)
            );
            // libtest's own lines are on the same output: an event may
            // follow its "test ... " on the line.
            let mut evs: Vec<Value> = text
                .lines()
                .filter_map(|l| {
                    l.find("{\"event\"")
                        .map(|i| crate::json::parse(&l[i..]).unwrap())
                })
                .collect();
            if mode == "early" {
                assert_eq!(names(&evs).first(), Some(&"start"), "{text}");
                let start = evs.remove(0);
                assert_eq!(
                    (start.get_bool("commit"), start.get_str("log")),
                    (Some(true), Some(format!("{RUN_DIR}/log/load.log").as_str()))
                );
                assert_eq!(
                    (
                        start.str_at(&["layout", "hostname"]),
                        start.str_at(&["layout", "generated"])
                    ),
                    (Some("lovelace"), Some("2026-11-22T01:00:11Z"))
                );
            }
            assert_eq!(names(&evs), ["failed", "end"], "{text}");
            assert_eq!(
                (
                    evs[0].get_str("code"),
                    evs[0].get_str("stage"),
                    evs[0].get_u64("n")
                ),
                (Some("internal"), Some("restore"), Some(3))
            );
            assert!(
                evs[0].get_str("text").unwrap().contains("the test's panic"),
                "{text}"
            );
            assert_eq!(
                (evs[1].get_u64("exit"), evs[1].get_bool("stopped")),
                (Some(101), Some(false))
            );
        }
    }
}
