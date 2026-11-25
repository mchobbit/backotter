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

use std::ffi::OsString;
use std::fs;
use std::io::{Read as _, Write as _};
use std::os::unix::fs::OpenOptionsExt as _;
use std::path::{Path, PathBuf};

use crate::term::profile::Os;
use crate::text::{cap, Clean};
use crate::ui::{Answer, Button, Chrome, Input, Message, TextView};

use super::holt_args::HoltCmd;
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
        let Some(mut cmd) = self.holt_cmd(&HoltCmd::ReportBug { out: path, no_redact, strip: strip.as_deref(), attach: &[] }) else {
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

    /// Copy `file` onto the config partition: its path there, or why
    /// not.
    pub(crate) fn save_to_stick(&mut self, file: &Path) -> Result<String, String> {
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
        let copied = copy_in(file, &mnt.join(REPORTS), &name);
        let un = run(self, &umount);
        let _ = fs::remove_dir(&mnt);
        copied.map_err(|e| e.to_string())?;
        un?;
        Ok(format!("{REPORTS}/{name}"))
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
        let first = self.save_to_stick(&path);
        let mut second: Option<Result<String, String>> = None;
        let mut second_path: Option<PathBuf> = None;
        loop {
            let text = saved_text(&path, &first, second_path.as_deref().zip(second.as_ref()));
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
                                second = Some(self.save_to_stick(&p));
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

/// "Report saved"'s text: where each is, and what to do with it.
fn saved_text(path: &Path, first: &Result<String, String>, second: Option<(&Path, &Result<String, String>)>) -> String {
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
            }
            t
        }
    };
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
/// directory synced too.
fn copy_in(src: &Path, dir: &Path, name: &str) -> std::io::Result<()> {
    fs::create_dir_all(dir)?;
    let mut b = Vec::new();
    fs::File::open(src)?.take(64 * 1024 * 1024).read_to_end(&mut b)?;
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
