//! The bug report (rescue-screens.md, "The bug report"): `r` or <
//! Report > on any help screen.  `bko-holt report-bug --output FILE`,
//! redacted by default, with the password hint and the typed address in
//! a `--strip-file`; saved to the stick's BACKOTTER partition as
//! `backotter/reports/bko-report-YYYYMMDD-HHMMSS.txt` (UTC), then
//! "Report saved" says where.  < Advanced > makes one that isn't
//! redacted, only after a typed YES, beside the first as
//! `...-UNREDACTED.txt`.
//!
//! - The file is made first in RAM (`/run/bko-rescue/reports/`, a 0700
//!   directory of ours), then copied onto the config partition, mounted
//!   read-write for this only at a new directory in the session's,
//!   synced, and unmounted.  No partition, a mount that fails, or a dry
//!   run (`DRY_RUN_TO_STICK`), and it stays in RAM, and the screen says
//!   it's lost at power off.
//! - The QR code of the mock (open question 4: version 2's quiet zone and
//!   GitHub's upper-case path untested) isn't drawn: the text is shown
//!   full width, as the mock does where there are no half-blocks.
//! - Nothing is sent from the stick: it only saves files.
//! - The stick keeps the REPORTS_KEPT newest reports, each with its
//!   `-UNREDACTED` twin as one pair, and removes older ones after it
//!   writes a new one, as it does the restore logs.  Before it saves one,
//!   it refuses when the save would leave the partition under
//!   REPORT_ROOM_MIN free (holt-json.md's open question 17): the report
//!   stays in RAM, and the screen says to copy old reports off.

use std::ffi::OsString;
use std::fs;
use std::io::{Read as _, Write as _};
use std::os::unix::fs::OpenOptionsExt as _;
use std::path::{Path, PathBuf};

use crate::term::profile::Os;
use crate::text::{cap, Clean};
use crate::ui::{Answer, Button, Chrome, Input, Message, TextView};

use super::holt_args::HoltCmd;
use super::restore::{LOGS_KEPT, LOG_GZ_MAX};
use super::strings as s;
use super::sys::{self, Cmd};
use super::{time, wait, App, Go};

/// Whether a dry run writes its reports to the stick's partition: no, so
/// a dry run writes nothing at all but RAM (a default taken: the dry
/// run's "nothing will be written" is about disks, but the stick is one).
pub const DRY_RUN_TO_STICK: bool = false;

/// Where on the partition reports go (stick-config.md's layout gains it:
/// rescue-screens.md's open question 9).
pub const REPORTS: &str = "backotter/reports";

/// How many reports the stick keeps, each with its `-UNREDACTED` twin as
/// one pair (holt-json.md's open question 17; bko-holt's stick.rs has the
/// same, for room_check's words).
pub const REPORTS_KEPT: usize = 5;

/// The most of one file copied to the stick (a report, its twin or a
/// restore log): a bigger one isn't copied, and stays in RAM.
pub const REPORT_MAX: u64 = 64 * 1024 * 1024;

/// The room a report's save must leave free on the partition: a machine's
/// restore logs, LOGS_KEPT of LOG_GZ_MAX and the one being written
/// (bko-holt's LOG_MAX x (LOGS_KEPT + 1), as stick.rs's room_check keeps for
/// each machine): 22 MiB.
pub const REPORT_ROOM_MIN: u64 = LOG_GZ_MAX * (LOGS_KEPT as u64 + 1);

/// How long report-bug may take: it runs `bko-holt dump` and plans.
pub const REPORT_MS: u64 = 600_000;

/// The most of a report the View it screen reads.
pub const VIEW_MAX: usize = 4 * 1024 * 1024;

// Buttons.
const B_VIEW: usize = 1;
const B_ADVANCED: usize = 2;

/// Each platform's mount of a FAT partition, and its unmount (*Check*
/// each on the images: RT13's boot-test runs a report).
pub fn mount_argv(os: Os, dev: &str, dir: &Path) -> (Vec<String>, Vec<String>) {
    let d = dir.display().to_string();
    let mount: Vec<&str> = match os {
        Os::Linux => vec!["mount", "-t", "vfat", "-o", "rw,nosuid,nodev,noexec", dev, &d],
        Os::FreeBsd | Os::DragonFly => vec!["mount_msdosfs", dev, &d],
        Os::NetBsd | Os::OpenBsd => vec!["mount_msdos", dev, &d],
        Os::Illumos => vec!["mount", "-F", "pcfs", dev, &d],
        Os::Haiku => vec!["mount", "-t", "fat", dev, &d],
    };
    let umount = match os {
        Os::Haiku => vec!["unmount", d.as_str()],
        _ => vec!["umount", d.as_str()],
    };
    (mount.into_iter().map(str::to_string).collect(), umount.into_iter().map(str::to_string).collect())
}

/// A device path bko-holt named, as a mount takes it.
fn plain_dev(d: &str) -> bool {
    d.starts_with("/dev/") && !d.contains("..") && d.bytes().all(|b| b.is_ascii_alphanumeric() || matches!(b, b'/' | b'_' | b'-' | b'.' | b','))
}

impl App {
    /// The reports' directory in RAM, outside the session's (which is
    /// wiped): a report that couldn't go to the stick stays there.
    fn reports_dir(&self) -> Result<PathBuf, String> {
        let d = self.sys.run_base().join("reports");
        sys::own_dir(&d, None).map_err(|e| e.to_string())?;
        Ok(d)
    }

    /// Make one report: redacted, or not.  Its path.
    fn make_report(&mut self, ui: &mut dyn crate::ui::Ui, path: &Path, no_redact: bool) -> Result<(), Go> {
        let mut strip_lines: Vec<String> = Vec::new();
        if let Some(h) = self.chosen().and_then(|m| m.hint.clone()) {
            strip_lines.push(h);
        }
        if !self.typed.is_empty() {
            strip_lines.push(self.typed.clone());
        }
        // Without its strip file a redacted report would keep the hint and
        // the typed address: none is made then.
        let strip = if strip_lines.is_empty() || no_redact {
            None
        } else {
            match self.session.strip_file(&strip_lines) {
                Ok(p) => Some(p),
                Err(e) => {
                    let text = format!("{}  {}", s::REPORT_NO_STRIP, Clean::line(&e.to_string(), cap::LOG_LINE).as_str());
                    let m = Message::new(Chrome::ours(s::SCR_REPORT, s::REPORT_FAILED_TITLE), Clean::text(&text, cap::PROBLEM));
                    return Err(match ui.message(&m) {
                        Answer::Ok(()) | Answer::Back => Go::Stay,
                        a => Go::of(a),
                    });
                }
            }
        };
        let attach = self.attach.clone();
        let Some(mut cmd) = self.holt_cmd(&HoltCmd::ReportBug { out: path, no_redact, strip: strip.as_deref(), attach: &attach }) else {
            return Err(Go::Stay);
        };
        cmd.timeout_ms = REPORT_MS;
        let r = self.run_job(ui, s::SCR_REPORT, s::MAKING_REPORT_TITLE, Clean::ours(s::MAKING_REPORT), cmd);
        if let Some(p) = &strip {
            sys::wipe_file(p);
        }
        let out = r?;
        if out.code != Some(0) || !path.is_file() {
            let mut m = Message::new(Chrome::ours(s::SCR_REPORT, s::REPORT_FAILED_TITLE), Clean::ours(s::REPORT_FAILED));
            m.buttons.push(Button::other(s::BTN_RAW, Some('o'), 9));
            loop {
                match ui.message(&m) {
                    Answer::Button(_) => {
                        if let Some(g) = self.raw_view(ui) {
                            return Err(g);
                        }
                    }
                    Answer::Ok(()) | Answer::Back => return Err(Go::Stay),
                    a => return Err(Go::of(a)),
                }
            }
        }
        Ok(())
    }

    /// Copy the report `file` into `backotter/reports` on the config
    /// partition, if the room it leaves allows, then the reports beyond
    /// the newest REPORTS_KEPT removed: its path there and how many older
    /// reports went, or why not.
    pub(crate) fn save_to_stick(&mut self, file: &Path) -> Result<(String, usize), String> {
        self.save_in(file, REPORTS, After::Reports)
    }

    /// Copy `file` into `rel` on the config partition, mounted read-write
    /// for this only; with `keep`, the restore logs there beyond the newest
    /// `keep` removed (stick-config.md, "Restore logs").  Its path there,
    /// or why not.
    pub(crate) fn save_to_stick_dir(&mut self, file: &Path, rel: &str, keep: Option<usize>) -> Result<String, String> {
        self.save_in(file, rel, After::Logs(keep)).map(|(p, _)| p)
    }

    fn save_in(&mut self, file: &Path, rel: &str, after: After) -> Result<(String, usize), String> {
        if self.opts.dry_run && !DRY_RUN_TO_STICK {
            return Err(s::REPORT_DRY_RUN.to_string());
        }
        let dev = self.stick.as_ref().and_then(|st| st.dev.clone()).filter(|d| plain_dev(d)).ok_or("this stick has no BACKOTTER partition here")?;
        let name = file.file_name().and_then(|n| n.to_str()).ok_or("no name")?.to_string();
        let mnt = self.session.path("mnt");
        sys::own_dir(&mnt, None).map_err(|e| e.to_string())?;
        let (mount, umount) = mount_argv(self.wh.os, &dev, &mnt);
        let run = |app: &mut App, argv: &[String]| {
            let Some(prog) = app.sys.which(&argv[0]) else { return Err(format!("{} isn't here", argv[0])) };
            let mut c = Cmd::new(&prog, argv[1..].iter().map(OsString::from).collect());
            c.timeout_ms = 60_000;
            let out = wait(app.sys.start(c), &mut *app.sys);
            if out.code == Some(0) {
                Ok(())
            } else {
                Err(Clean::line(out.stderr_text().lines().next().unwrap_or("it failed"), cap::LOG_LINE).as_str().to_string())
            }
        };
        run(self, &mount)?;
        let dir = mnt.join(rel);
        let copied = match after {
            After::Logs(keep) => copy_in(file, &dir, &name).map_err(|e| e.to_string()).map(|()| {
                if let Some(k) = keep {
                    super::restore::prune(&dir, k);
                }
                0
            }),
            After::Reports => {
                let room = self.sys.room(&mnt);
                save_report(file, &dir, &name, room)
            }
        };
        let un = run(self, &umount);
        let _ = fs::remove_dir(&mnt);
        let removed = copied?;
        un?;
        Ok((format!("{rel}/{name}"), removed))
    }

    /// `r` or < Report >: make it, save it, say where.  None to go back to
    /// the screen that asked, else where to.
    pub(crate) fn report(&mut self, ui: &mut dyn crate::ui::Ui) -> Option<Go> {
        let dir = match self.reports_dir() {
            Ok(d) => d,
            Err(e) => {
                let m = Message::new(Chrome::ours(s::SCR_REPORT, s::REPORT_FAILED_TITLE), Clean::line(&e, cap::LOG_LINE));
                return match ui.message(&m) {
                    Answer::Ok(()) => None,
                    a => Some(Go::of(a)),
                };
            }
        };
        let stamp = time::stamp(self.sys.now());
        let name = format!("bko-report-{stamp}.txt");
        let path = dir.join(&name);
        if let Err(g) = self.make_report(ui, &path, false) {
            return (g != Go::Stay).then_some(g);
        }
        let (first, mut removed) = split(self.save_to_stick(&path));
        let mut second: Option<Result<String, String>> = None;
        let mut second_path: Option<PathBuf> = None;
        loop {
            let text = saved_text(&path, &first, second_path.as_deref().zip(second.as_ref()), removed);
            let mut m = Message::new(Chrome::ours(s::SCR_REPORT, s::REPORT_TITLE), Clean::text(&text, cap::PROBLEM));
            m.buttons = vec![Button::ok(), Button::other(s::BTN_VIEW, Some('v'), B_VIEW)];
            if second.is_none() {
                // No letter: never pressed by a stray key.
                m.buttons.push(Button::other(s::BTN_ADVANCED, None, B_ADVANCED));
            }
            let stuck = first.is_err() || second.as_ref().is_some_and(Result::is_err);
            if stuck {
                m.buttons.push(Button::shell());
            }
            match ui.message(&m) {
                Answer::Ok(()) | Answer::Back => return None,
                Answer::Button(B_VIEW) => {
                    let lines: Vec<Clean> = self
                        .sys
                        .read(&path, VIEW_MAX)
                        .map(|b| String::from_utf8_lossy(&b).lines().take(20_000).map(|l| Clean::line(l, cap::LOG_LINE)).collect())
                        .unwrap_or_default();
                    let mut v = TextView::new(Chrome::ours(s::SCR_REPORT, s::VIEW_TITLE), lines);
                    v.buttons.push(Button::back());
                    match ui.view(&v) {
                        Answer::Ok(()) | Answer::Back => {}
                        a => return Some(Go::of(a)),
                    }
                }
                Answer::Button(B_ADVANCED) => match self.advanced(ui) {
                    Ok(true) => {
                        let p = dir.join(format!("bko-report-{stamp}-UNREDACTED.txt"));
                        match self.make_report(ui, &p, true) {
                            Ok(()) => {
                                let (r, n) = split(self.save_to_stick(&p));
                                removed += n;
                                second = Some(r);
                                second_path = Some(p);
                            }
                            Err(Go::Stay) => {}
                            Err(g) => return Some(g),
                        }
                    }
                    Ok(false) => {}
                    Err(g) => return Some(g),
                },
                a => return Some(Go::of(a)),
            }
        }
    }

    /// The Advanced screen: true once YES is typed and Make it pressed.
    fn advanced(&mut self, ui: &mut dyn crate::ui::Ui) -> Result<bool, Go> {
        let mut text = format!("{}\n\n{}", s::ADVANCED_TEXT, s::ADVANCED_WARN);
        if !ui.full_screen() {
            text.push_str("\n\n");
            text.push_str(s::ADVANCED_PLAIN);
        }
        let mut d = Input::new(Chrome::ours(s::SCR_REPORT, s::ADVANCED_TITLE), Clean::text(&text, cap::PROBLEM));
        d.label = Some((Clean::ours(s::TYPE_YES), 5));
        d.max = 3;
        d.buttons = vec![Button { label: Clean::ours(s::BTN_MAKE_IT), key: None, act: crate::ui::Act::Ok }, Button::back()];
        loop {
            match ui.input(&d) {
                Answer::Ok(t) if t.trim().eq_ignore_ascii_case(s::YES) => return Ok(true),
                Answer::Ok(t) => {
                    d.value = t;
                    d.notice = Some(Clean::ours(s::TYPE_YES_FIRST));
                }
                Answer::Back => return Ok(false),
                a => return Err(Go::of(a)),
            }
        }
    }
}

/// A save's path, or why not, and how many older reports it removed.
fn split(r: Result<(String, usize), String>) -> (Result<String, String>, usize) {
    match r {
        Ok((p, n)) => (Ok(p), n),
        Err(e) => (Err(e), 0),
    }
}

/// What a save does after its copy.
enum After {
    /// A restore log: the logs beyond the newest N removed.
    Logs(Option<usize>),
    /// A bug report: the room checked first, then the reports beyond the
    /// newest REPORTS_KEPT removed.
    Reports,
}

/// A report's stamp (`YYYYMMDD-HHMMSS`) from its name, when it is one of
/// ours: `bko-report-STAMP.txt` or `bko-report-STAMP-UNREDACTED.txt`.
fn report_stamp(name: &str) -> Option<&str> {
    let rest = name.strip_prefix("bko-report-")?;
    let (stamp, tail) = (rest.get(..15)?, rest.get(15..)?);
    let b = stamp.as_bytes();
    let ok = b[..8].iter().all(u8::is_ascii_digit) && b[8] == b'-' && b[9..].iter().all(u8::is_ascii_digit);
    (ok && (tail == ".txt" || tail == "-UNREDACTED.txt")).then_some(stamp)
}

/// The reports in FILES (each name and size in `backotter/reports`) that
/// go once NAME is written: every one whose stamp isn't NAME's or among
/// the newest REPORTS_KEPT - 1 others, by name (a UTC timestamp).  NAME's
/// own pair always stays, even when the clock made its stamp older than
/// the others'.  Files that aren't reports stay.
fn reports_to_remove(files: &[(String, u64)], name: &str) -> Vec<String> {
    let new = report_stamp(name);
    let mut stamps: Vec<&str> = files.iter().filter_map(|(n, _)| report_stamp(n)).filter(|st| Some(*st) != new).collect();
    stamps.sort_unstable();
    stamps.dedup();
    let others = REPORTS_KEPT.saturating_sub(usize::from(new.is_some()));
    let gone = &stamps[..stamps.len().saturating_sub(others)];
    files.iter().filter(|(n, _)| report_stamp(n).is_some_and(|st| gone.contains(&st))).map(|(n, _)| n.clone()).collect()
}

/// N bytes in MiB to a tenth, rounded down (UP false) or up, as bko-holt's
/// room_check prints them: room down and need up, so the two never print
/// equal.
fn mib(n: u128, up: bool) -> String {
    let t = n * 10;
    let d = 1u128 << 20;
    let t = if up { t.div_ceil(d) } else { t / d };
    format!("{}.{} MiB", t / 10, t % 10)
}

/// Whether a report of LEN bytes may be saved where FILES are (None: no
/// `backotter/reports` yet, so it takes a cluster too), the partition
/// FREE bytes free in clusters of CLUSTER, once REMOVE go after it.  The
/// save is the copy and the removal after it: the room it leaves counts
/// the files it removes, and it must fit beside them first.  Each file
/// and folder takes whole clusters.  Why not, in words: they name the
/// older reports to copy off only when there are some (OLDER).
fn report_room(files: Option<&[(String, u64)]>, remove: &[String], len: u64, free: u64, cluster: u64, older: bool) -> Result<(), String> {
    if len > REPORT_MAX {
        return Err(s::report_too_big(&mib(len.into(), true), &mib(REPORT_MAX.into(), false)));
    }
    let cl = u128::from(cluster.max(1));
    let up = |n: u64| u128::from(n).div_ceil(cl) * cl;
    let new = up(len) + if files.is_none() { cl } else { 0 };
    let freed: u128 = files.unwrap_or(&[]).iter().filter(|(n, _)| remove.contains(n)).map(|(_, l)| up(*l)).sum();
    let free = u128::from(free);
    let min = u128::from(REPORT_ROOM_MIN);
    if free < new {
        return Err(s::report_no_room(&mib(new, true), &mib(free, false), older));
    }
    if free - new + freed < min {
        return Err(s::report_short(&mib(free - new + freed, false), &mib(min, true), older));
    }
    Ok(())
}

/// The files in DIR, each name and size; None when DIR isn't there.  An
/// entry that can't be read (a damaged FAT entry) is left out: it isn't
/// counted, and it isn't removed.
fn list_files(dir: &Path) -> std::io::Result<Option<Vec<(String, u64)>>> {
    let rd = match fs::read_dir(dir) {
        Ok(rd) => rd,
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => return Ok(None),
        Err(e) => return Err(e),
    };
    let mut v = Vec::new();
    for e in rd.flatten() {
        let Ok(m) = e.metadata() else { continue };
        if let Some(n) = e.file_name().to_str() {
            v.push((n.to_string(), if m.is_dir() { 0 } else { m.len() }));
        }
    }
    Ok(Some(v))
}

/// Save the report FILE as NAME in DIR (the partition's
/// `backotter/reports`, mounted), with ROOM the partition's (free, cluster)
/// as statvfs gives it: refused when its room is short, then copied, then
/// the older reports removed and DIR synced.  How many older reports
/// (pairs) went.  When the room can't be read, the copy goes ahead, as it
/// did before the check (a default taken: refusing would keep every
/// report off the stick where statvfs fails); a copy the partition can't
/// take still fails.  So does a folder that can't be listed: the copy
/// goes ahead unchecked, and nothing is removed.
fn save_report(file: &Path, dir: &Path, name: &str, room: Option<(u64, u64)>) -> Result<usize, String> {
    let len = fs::metadata(file).map_err(|e| e.to_string())?.len();
    let (room, files) = match list_files(dir) {
        Ok(files) => (room, files),
        Err(_) => (None, Some(Vec::new())),
    };
    let remove = reports_to_remove(files.as_deref().unwrap_or(&[]), name);
    if let Some((free, cluster)) = room {
        let new = report_stamp(name);
        let older = files.as_deref().unwrap_or(&[]).iter().filter_map(|(n, _)| report_stamp(n)).any(|st| Some(st) != new);
        report_room(files.as_deref(), &remove, len, free, cluster, older)?;
    }
    copy_in(file, dir, name).map_err(|e| e.to_string())?;
    let mut pairs: Vec<&str> = remove.iter().filter_map(|n| report_stamp(n)).collect();
    pairs.sort_unstable();
    pairs.dedup();
    for n in &remove {
        let _ = fs::remove_file(dir.join(n));
    }
    if !remove.is_empty() {
        if let Ok(d) = fs::File::open(dir) {
            let _ = d.sync_all();
        }
    }
    Ok(pairs.len())
}

/// "Report saved"'s text: where each is, and what to do with it; REMOVED
/// older reports went to make way.
fn saved_text(path: &Path, first: &Result<String, String>, second: Option<(&Path, &Result<String, String>)>, removed: usize) -> String {
    let where_ = |p: &Path, r: &Result<String, String>| match r {
        Ok(on) => on.clone(),
        Err(_) => p.display().to_string(),
    };
    let mut t = match second {
        None => match first {
            Ok(on) => s::report_on_stick(on),
            Err(why) => format!("{}\n{}", s::report_in_ram(&path.display().to_string()), s::report_why_ram(why)),
        },
        Some((p2, r2)) => {
            let mut t = s::report_both(&where_(path, first), &where_(p2, r2));
            if first.is_err() || r2.is_err() {
                t.push('\n');
                t.push_str(&s::report_in_ram(&path.parent().unwrap_or(path).display().to_string()));
                // Why: the room words say what to copy off.  Both, when the
                // two failed for different reasons.
                let why = match (first, r2) {
                    (Err(a), Err(b)) if a != b => Some(s::report_why_ram_each(a, b)),
                    (Err(w), _) | (_, Err(w)) => Some(s::report_why_ram(w)),
                    _ => None,
                };
                if let Some(why) = why {
                    t.push('\n');
                    t.push_str(&why);
                }
            }
            t
        }
    };
    if removed > 0 {
        t.push('\n');
        t.push_str(&s::reports_removed(removed));
    }
    t.push_str("\n\n");
    if second.is_some() {
        t.push_str(s::REPORT_PRIVATE);
    } else {
        t.push_str(s::REPORT_GITHUB);
        t.push_str("\n\n");
        t.push_str(s::REPORT_REDACTED);
    }
    t
}

/// Copy a report into `dir` (made if missing) as a new file, synced, the
/// directory synced too.  One over REPORT_MAX isn't copied: the words
/// give its size as the file system says it (only REPORT_MAX + 1 bytes are
/// read).
fn copy_in(src: &Path, dir: &Path, name: &str) -> std::io::Result<()> {
    let mut b = Vec::new();
    let f = fs::File::open(src)?;
    let len = f.metadata().map(|m| m.len()).unwrap_or(0);
    f.take(REPORT_MAX + 1).read_to_end(&mut b)?;
    if b.len() as u64 > REPORT_MAX {
        let size = len.max(b.len() as u64);
        return Err(std::io::Error::other(s::report_too_big(&mib(size.into(), true), &mib(REPORT_MAX.into(), false))));
    }
    fs::create_dir_all(dir)?;
    let dest = dir.join(name);
    let mut f = fs::OpenOptions::new().write(true).create_new(true).mode(0o600).open(&dest)?;
    f.write_all(&b)?;
    f.sync_all()?;
    if let Ok(d) = fs::File::open(dir) {
        let _ = d.sync_all();
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    const MIB: u64 = 1 << 20;

    fn files(v: &[(&str, u64)]) -> Vec<(String, u64)> {
        v.iter().map(|(n, l)| (n.to_string(), *l)).collect()
    }

    #[test]
    fn report_names() {
        assert_eq!(report_stamp("bko-report-20261123-142630.txt"), Some("20261123-142630"));
        assert_eq!(report_stamp("bko-report-20261123-142630-UNREDACTED.txt"), Some("20261123-142630"));
        for n in ["bko-report-20261123-142630.txt.gz", "bko-report-2026112-142630.txt", "bko-report-20261123T142630.txt", "notes.txt", "bko-report-.txt", "bko-report-2026112é-142630.txt"] {
            assert_eq!(report_stamp(n), None, "{n}");
        }
        assert_eq!(REPORT_ROOM_MIN, 22 * MIB, "bko-holt's LOG_MAX x (LOGS_KEPT + 1)");
        assert_eq!((mib(22 * MIB as u128 - 1, false), mib(22 * MIB as u128 + 1, true)), ("21.9 MiB".to_string(), "22.1 MiB".to_string()));
    }

    #[test]
    fn the_five_newest_pairs_stay() {
        let pair = |d: u32| [format!("bko-report-202611{d:02}-090000.txt"), format!("bko-report-202611{d:02}-090000-UNREDACTED.txt")];
        let mut v: Vec<(String, u64)> = (1..=5).flat_map(pair).map(|n| (n, 10)).collect();
        v.push(("README.txt".into(), 10));
        // Five pairs and a sixth: the oldest pair goes, both of its files.
        assert_eq!(reports_to_remove(&v, "bko-report-20261123-142630.txt"), pair(1));
        // The sixth's twin joins its pair: the same go.
        assert_eq!(reports_to_remove(&v, "bko-report-20261123-142630-UNREDACTED.txt"), pair(1));
        // Already among them (its twin saved first): nothing goes.
        assert!(reports_to_remove(&v, "bko-report-20261105-090000-UNREDACTED.txt").is_empty());
        // A clock that makes the new one the oldest: it stays all the same.
        assert_eq!(reports_to_remove(&v, "bko-report-19700101-000000.txt"), pair(1));
        // Fewer than five: none go; many: all but the four newest.
        assert!(reports_to_remove(&v[..6], "bko-report-20261123-142630.txt").is_empty());
        let many: Vec<(String, u64)> = (1..=9).flat_map(pair).map(|n| (n, 10)).collect();
        assert_eq!(reports_to_remove(&many, "bko-report-20261123-142630.txt").len(), 10);
    }

    #[test]
    fn the_room_a_save_leaves() {
        let min = REPORT_ROOM_MIN;
        let none: Option<&[(String, u64)]> = None;
        let here = files(&[("bko-report-20261101-090000.txt", 3 * MIB), ("bko-report-20261101-090000-UNREDACTED.txt", 4096 + 1)]);
        // A new folder and the file, each in whole clusters.
        assert!(report_room(none, &[], 4097, min + 3 * 4096, 4096, true).is_ok());
        assert!(report_room(none, &[], 4097, min + 3 * 4096 - 1, 4096, true).is_err());
        assert!(report_room(Some(&here), &[], 4096, min + 4096, 4096, true).is_ok());
        assert_eq!(report_room(Some(&here), &[], 4096, min + 4095, 4096, true).unwrap_err(), s::report_short("21.9 MiB", "22.0 MiB", true));
        // What it removes after counts: the old pair's 3 MiB and two clusters.
        let gone: Vec<String> = here.iter().map(|(n, _)| n.clone()).collect();
        assert!(report_room(Some(&here), &gone, MIB, min - 2 * MIB - 2 * 4096, 4096, true).is_ok());
        assert!(report_room(Some(&here), &gone, MIB, min - 2 * MIB - 2 * 4096 - 1, 4096, true).is_err());
        // ...but it must fit beside them first.
        assert_eq!(report_room(Some(&here), &gone, MIB, MIB - 1, 4096, true).unwrap_err(), s::report_no_room("1.0 MiB", "0.9 MiB", true));
        // Over the cap, whatever the room; a 0 cluster counts bytes.
        assert_eq!(report_room(Some(&here), &[], REPORT_MAX + 1, u64::MAX, 4096, true).unwrap_err(), s::report_too_big("64.1 MiB", "64.0 MiB"));
        assert!(report_room(Some(&here), &[], REPORT_MAX, u64::MAX, 4096, true).is_ok());
        assert!(report_room(Some(&here), &[], 10, min + 10, 0, true).is_ok());
        assert!(report_room(Some(&here), &[], 10, min + 9, 0, true).is_err());
    }

    #[test]
    fn a_report_is_saved_then_the_oldest_go() {
        let d = std::env::temp_dir().join(format!("bko-rescue-report-{}", std::process::id()));
        let _ = fs::remove_dir_all(&d);
        let dir = d.join("reports");
        fs::create_dir_all(&dir).unwrap();
        for day in 1..=5 {
            fs::write(dir.join(format!("bko-report-202611{day:02}-090000.txt")), b"old\n").unwrap();
        }
        let src = d.join("new.txt");
        fs::write(&src, b"new\n").unwrap();
        // Refused: nothing written, nothing removed.
        assert!(save_report(&src, &dir, "bko-report-20261123-142630.txt", Some((REPORT_ROOM_MIN - 1, 4096))).is_err());
        assert_eq!(fs::read_dir(&dir).unwrap().count(), 5);
        assert_eq!(save_report(&src, &dir, "bko-report-20261123-142630.txt", Some((REPORT_ROOM_MIN + 4096, 4096))), Ok(1));
        assert!(!dir.join("bko-report-20261101-090000.txt").exists() && dir.join("bko-report-20261123-142630.txt").is_file());
        // Its twin: nothing more goes; a room unread doesn't stop it.
        assert_eq!(save_report(&src, &dir, "bko-report-20261123-142630-UNREDACTED.txt", None), Ok(0));
        assert_eq!(fs::read_dir(&dir).unwrap().count(), 6);
        // A first report makes the folder.
        let fresh = d.join("fresh/reports");
        fs::create_dir_all(d.join("fresh")).unwrap();
        assert_eq!(save_report(&src, &fresh, "bko-report-20261123-142630.txt", Some((u64::MAX, 4096))), Ok(0));
        assert!(fresh.join("bko-report-20261123-142630.txt").is_file());
        fs::remove_dir_all(&d).unwrap();
    }

    #[test]
    fn the_room_words_name_old_reports_only_when_there_are_some() {
        let d = std::env::temp_dir().join(format!("bko-rescue-report-words-{}", std::process::id()));
        let _ = fs::remove_dir_all(&d);
        let dir = d.join("reports");
        fs::create_dir_all(&dir).unwrap();
        let src = d.join("new.txt");
        fs::write(&src, b"new\n").unwrap();
        let short = Some((REPORT_ROOM_MIN - 1, 4096));
        // No reports there, only notes: the words name other files.
        fs::write(dir.join("notes.txt"), b"x").unwrap();
        let e = save_report(&src, &dir, "bko-report-20261123-142630.txt", short).unwrap_err();
        assert!(e.contains("copy files you don't need off the stick's BACKOTTER partition") && !e.contains("older reports"), "{e}");
        assert!(e.contains("and the restore logs need 22.0 MiB there."), "{e}");
        // Its own twin is no older report.
        fs::write(dir.join("bko-report-20261123-142630.txt"), b"x").unwrap();
        let e = save_report(&src, &dir, "bko-report-20261123-142630-UNREDACTED.txt", short).unwrap_err();
        assert!(!e.contains("older reports"), "{e}");
        // An older one: named.
        fs::write(dir.join("bko-report-20261101-090000.txt"), b"x").unwrap();
        let e = save_report(&src, &dir, "bko-report-20261123-142630-UNREDACTED.txt", short).unwrap_err();
        assert!(e.contains("copy the older reports in backotter/reports off the stick"), "{e}");
        let e = save_report(&src, &dir, "bko-report-20261123-142630-UNREDACTED.txt", Some((1, 4096))).unwrap_err();
        assert!(e.starts_with("it needs 0.1 MiB") && e.contains("older reports"), "{e}");
        fs::remove_dir_all(&d).unwrap();
    }

    #[test]
    fn a_folder_that_cant_be_listed_doesnt_stop_the_save() {
        use std::os::unix::fs::PermissionsExt as _;
        let d = std::env::temp_dir().join(format!("bko-rescue-report-unlisted-{}", std::process::id()));
        let _ = fs::remove_dir_all(&d);
        let dir = d.join("reports");
        fs::create_dir_all(&dir).unwrap();
        for day in 1..=6 {
            fs::write(dir.join(format!("bko-report-202611{day:02}-090000.txt")), b"old\n").unwrap();
        }
        let src = d.join("new.txt");
        fs::write(&src, b"new\n").unwrap();
        // Written and searched, not read: as a folder whose listing fails.
        fs::set_permissions(&dir, fs::Permissions::from_mode(0o300)).unwrap();
        let unlisted = fs::read_dir(&dir).is_err();
        // Too little room, but it can't be checked: the copy goes ahead,
        // and nothing is removed.
        let r = save_report(&src, &dir, "bko-report-20261123-142630.txt", Some((1, 4096)));
        fs::set_permissions(&dir, fs::Permissions::from_mode(0o700)).unwrap();
        if unlisted {
            assert_eq!(r, Ok(0));
            assert_eq!(fs::read_dir(&dir).unwrap().count(), 7);
        }
        fs::remove_dir_all(&d).unwrap();
    }

    #[test]
    fn a_file_over_the_cap_is_refused_with_its_size() {
        let d = std::env::temp_dir().join(format!("bko-rescue-report-big-{}", std::process::id()));
        let _ = fs::remove_dir_all(&d);
        fs::create_dir_all(&d).unwrap();
        let src = d.join("big.txt");
        // Sparse: 70 MiB on paper.
        fs::File::create(&src).unwrap().set_len(70 << 20).unwrap();
        let e = copy_in(&src, &d.join("out"), "big.txt").unwrap_err().to_string();
        assert_eq!(e, s::report_too_big("70.0 MiB", "64.0 MiB"));
        assert!(!d.join("out/big.txt").exists());
        fs::remove_dir_all(&d).unwrap();
    }

    #[test]
    fn both_reasons_show_when_both_saves_fail() {
        let p = Path::new("/run/bko-rescue/reports/bko-report-20261123-142630.txt");
        let p2 = Path::new("/run/bko-rescue/reports/bko-report-20261123-142630-UNREDACTED.txt");
        let (room, big): (Result<String, String>, Result<String, String>) = (Err("no room".into()), Err("too big".into()));
        let t = saved_text(p, &room, Some((p2, &big)), 0);
        assert!(t.contains(&s::report_why_ram_each("no room", "too big")), "{t}");
        let t = saved_text(p, &room, Some((p2, &room)), 0);
        assert!(t.contains(&s::report_why_ram("no room")) && !t.contains("UNREDACTED one couldn't"), "{t}");
        let ok: Result<String, String> = Ok("backotter/reports/x.txt".into());
        assert!(saved_text(p, &ok, Some((p2, &big)), 0).contains(&s::report_why_ram("too big")));
        assert!(saved_text(p, &room, Some((p2, &ok)), 0).contains(&s::report_why_ram("no room")));
    }

    #[test]
    fn mounts_per_platform() {
        let d = Path::new("/run/bko-rescue/s-1-0/mnt");
        let (m, u) = mount_argv(Os::Linux, "/dev/sdc3", d);
        assert_eq!(m.join(" "), "mount -t vfat -o rw,nosuid,nodev,noexec /dev/sdc3 /run/bko-rescue/s-1-0/mnt");
        assert_eq!(u.join(" "), "umount /run/bko-rescue/s-1-0/mnt");
        assert_eq!(mount_argv(Os::Illumos, "/dev/dsk/c2t0d0s3", d).0[..3], ["mount", "-F", "pcfs"]);
        assert!(plain_dev("/dev/sdc3") && plain_dev("/dev/dsk/c2t0d0s3") && plain_dev("/dev/disk/usb/0/0/2"));
        assert!(!plain_dev("/dev/../etc/x") && !plain_dev("sdc3") && !plain_dev("/dev/sd c"));
    }
}
