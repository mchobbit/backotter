//! `bko-rescue --demo`, first cut (RT6, RT7): every widget in a row, in
//! full screen or plain mode as the profile says, for the pty runs and
//! later spikes.  RT8 adds the fake bko-holt: a typed passphrase handed
//! on as a key file, and its events followed with the gauge.
//!
//! Back goes a page back; `P` on the first page and `p` on the
//! too-small notice go on in plain mode at the same page.  Only the
//! password's length is ever shown.

use crate::plain::{Plain, StdLine};
use crate::term::profile::Profile;
use crate::text::{cap, Clean};
use crate::ui::{Answer, Button, Chrome, Input, Menu, Message, Password, Progress, Question, Radio, TextView, Tick, Ui};
use crate::widgets::FullScreen;

/// What the pages were given, for the last one.
#[derive(Default)]
pub struct Seen {
    pub machine: Option<usize>,
    pub address: String,
    pub password_chars: usize,
    pub action: Option<usize>,
    pub restore: Option<bool>,
}

pub const PAGES: usize = 8;

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
            let t = Clean::ours(&text).join("", &Clean::line(&seen.address, cap::LINE));
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
/// user asks.  The exit status.
pub fn run(p: &Profile) -> i32 {
    let mut seen = Seen::default();
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

#[cfg(test)]
mod tests {
    use super::*;
    use crate::ui::{Reply, Scripted};

    #[test]
    fn the_tour_goes_forward_and_back() {
        let mut ui = Scripted::new([
            Reply::Pick(1),
            Reply::Text("sftp://x".into()),
            Reply::Back,
            Reply::Text("sftp://y".into()),
            Reply::Secret(b"abc".to_vec()),
            Reply::Pick(0),
            Reply::No,
            Reply::Ok,
            Reply::Ok,
            Reply::Ok,
        ]);
        let mut seen = Seen::default();
        let (n, a) = tour(&mut ui, 0, &mut seen);
        assert_eq!((n, a), (PAGES - 1, Answer::Ok(())));
        assert_eq!(seen.machine, Some(1));
        assert_eq!(seen.address, "sftp://y");
        assert_eq!(seen.password_chars, 3);
        assert_eq!(seen.restore, Some(false));
        assert_eq!(ui.asked[3].0, "input", "Back from the password went to the address");
    }

    #[test]
    fn plain_and_quit_stop_it_where_it_is() {
        let mut ui = Scripted::new([Reply::Pick(0), Reply::Plain]);
        assert_eq!(tour(&mut ui, 0, &mut Seen::default()), (1, Answer::Plain));
        let mut ui = Scripted::new([Reply::Quit]);
        assert_eq!(tour(&mut ui, 3, &mut Seen::default()), (3, Answer::Quit));
    }
}
