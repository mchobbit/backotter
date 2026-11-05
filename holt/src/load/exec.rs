//! Running the writing stages: the checks `plan` makes, then each stage's
//! commands, then a read-back of the disks to see the stage took.
//!
//! Without --commit a stage prints its commands and writes nothing.  Every
//! command and its output goes to RUN_DIR/log/load.log, which `boot` copies
//! into the restored system's /var/log/bko-holt/.

use super::commands::Cmd;
use super::layout::Layout;
use super::status::{self, State};
use super::{plan, Options, Stage, EXIT_FAILED, EXIT_OK, EXIT_PLAN_PROBLEMS, RUN_DIR};
use crate::sys::Sys;
use std::io::Write;
use std::process::{Command, Stdio};

fn log_line(log: &mut Option<std::fs::File>, text: &str) {
    if let Some(f) = log {
        let _ = f.write_all(text.as_bytes());
        if !text.ends_with('\n') {
            let _ = f.write_all(b"\n");
        }
    }
}

/// Runs one step for real.  A command's output goes to the log (and, for
/// the restore's archiver, the terminal sees nothing until it's done).
fn run_cmd(c: &Cmd, log: &mut Option<std::fs::File>) -> Result<(), String> {
    log_line(log, &c.shell());
    if c.is_note() {
        return Ok(());
    }
    if let Some(path) = &c.write {
        if let Some(dir) = std::path::Path::new(path).parent() {
            std::fs::create_dir_all(dir).map_err(|e| format!("{}: {e}", dir.display()))?;
        }
        return std::fs::write(path, c.stdin.as_deref().unwrap_or("")).map_err(|e| format!("{path}: {e}"));
    }
    let mut cmd = Command::new(&c.argv[0]);
    cmd.args(&c.argv[1..]).env("LC_ALL", "C").stdout(Stdio::piped()).stderr(Stdio::piped());
    // No stdin given: the terminal's, so cryptsetup can ask for a passphrase.
    cmd.stdin(if c.stdin.is_some() { Stdio::piped() } else { Stdio::inherit() });
    let mut child = cmd.spawn().map_err(|e| format!("{}: {e}", c.argv[0]))?;
    if let (Some(input), Some(mut pipe)) = (&c.stdin, child.stdin.take()) {
        pipe.write_all(input.as_bytes()).map_err(|e| format!("{}: writing its input: {e}", c.argv[0]))?;
    }
    let out = child.wait_with_output().map_err(|e| format!("{}: {e}", c.argv[0]))?;
    log_line(log, &String::from_utf8_lossy(&out.stdout));
    log_line(log, &String::from_utf8_lossy(&out.stderr));
    if out.status.success() {
        Ok(())
    } else {
        let err = String::from_utf8_lossy(&out.stderr);
        let tail: Vec<&str> = err.lines().rev().take(5).collect::<Vec<_>>().into_iter().rev().collect();
        Err(format!("{} failed ({}){}", c.argv[0], out.status, if tail.is_empty() { String::new() } else { format!(":\n    {}", tail.join("\n    ")) }))
    }
}

fn state_of(layout: &Layout, opts: &Options, sys: &Sys, s: Stage) -> (State, String) {
    let (rows, _) = status::rows(layout, opts, sys);
    rows.into_iter().find(|r| r.0 == s).map(|r| (r.1, r.2)).unwrap_or((State::Unknown, String::new()))
}

pub fn run(layout: &Layout, opts: &Options, sys: &Sys, stages: &[Stage]) -> (u8, String, String) {
    let p = plan::check(layout, opts, sys);
    let mut out = String::new();
    let mut err = String::new();
    if !p.problems.is_empty() {
        for x in &p.problems {
            err += &format!("bko-holt load: {x}\n");
        }
        err += "bko-holt load: nothing was written; `bko-holt load LAYOUT ...` without a stage shows the whole plan\n";
        return (EXIT_PLAN_PROBLEMS, out, err);
    }
    if opts.commit && (opts.sysroot.is_some() || !sys.privileged) {
        let why = if opts.sysroot.is_some() { "with --sysroot" } else { "except as root" };
        return (EXIT_FAILED, out, format!("bko-holt load: --commit can't write {why}\n"));
    }
    let mut log = None;
    if opts.commit {
        let _ = std::fs::create_dir_all(format!("{RUN_DIR}/log"));
        log = std::fs::OpenOptions::new().create(true).append(true).open(format!("{RUN_DIR}/log/load.log")).ok();
        log_line(&mut log, &format!("==== bko-holt load {} {:?}", env!("CARGO_PKG_VERSION"), std::env::args().collect::<Vec<_>>()));
    }
    for &s in stages {
        // --through skips what's done already; --only does what it's told.
        if opts.through.is_some() {
            let (st, _) = state_of(layout, opts, sys, s);
            if matches!(st, State::Done | State::Nothing) {
                out += &format!("# ---- {}: {} already\n", s.name(), st_word(st));
                continue;
            }
        }
        let marker = crate::other::restored_marker(&layout.v);
        if s == Stage::Boot && !sys.exists(&format!("{}/{marker}", opts.target)) {
            return (EXIT_FAILED, out, format!("bko-holt load: boot: {}/{marker} isn't there; restore the system into {} first\n", opts.target, opts.target));
        }
        let cmds = p.built.stage(s);
        out += &format!("# ---- {} ----\n", s.name());
        if !opts.commit {
            for c in cmds {
                out += &c.shell();
            }
            continue;
        }
        log_line(&mut log, &format!("==== stage {}", s.name()));
        for c in cmds {
            if let Err(e) = run_cmd(c, &mut log) {
                log_line(&mut log, &format!("FAILED: {e}"));
                err += &format!("bko-holt load: {}: {e}\n", s.name());
                err += &format!("bko-holt load: the log is {RUN_DIR}/log/load.log\n");
                return (EXIT_FAILED, out, err);
            }
        }
        // Read the disks back: the stage must show as done.
        let (st, why) = state_of(layout, opts, sys, s);
        if !matches!(st, State::Done | State::Nothing) {
            let msg = format!("{} ran, but the disks don't show it done: {}{}", s.name(), st_word(st), if why.is_empty() { String::new() } else { format!(" ({why})") });
            log_line(&mut log, &format!("FAILED: {msg}"));
            return (EXIT_FAILED, out, format!("bko-holt load: {msg}\n"));
        }
        out += &format!("# {} done\n", s.name());
        log_line(&mut log, &format!("==== stage {} done", s.name()));
    }
    if !opts.commit {
        out += "\n# Nothing was written: add --commit to run these.\n";
    }
    (EXIT_OK, out, err)
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
