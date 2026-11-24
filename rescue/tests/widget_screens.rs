//! Golden screens of every widget (RT6), each in every line style, colour
//! and mono, one file a widget in tests/screens/w-*.txt; the too-small
//! notice at 60x14 in tests/screens/too-small.*.txt.  And each widget's
//! bytes in every profile: only allowed sequences, the frame budgets, a
//! list move under 200 bytes (1200 when it scrolls), a gauge step under 40.
//! BKO_RESCUE_GOLDEN=check (the default), report or bless.

mod common;

use bko_rescue::screen::{Canvas, Screen};
use bko_rescue::term::keys::Key;
use bko_rescue::term::out::Lines;
use bko_rescue::term::tty::Size;
use bko_rescue::testkit::{golden, Checker, Rules};
use bko_rescue::text::{cap, Clean};
use bko_rescue::ui::{Button, Chrome, Input, Menu, Message, Password, Progress, Question, Radio, TextView};
use bko_rescue::widgets::small::too_small;
use bko_rescue::widgets::Dialog;

fn o(s: &str) -> Clean {
    Clean::ours(s)
}

fn keys(d: &mut Dialog, ks: &[Key]) {
    for &k in ks {
        d.key(k);
    }
}

fn machine() -> Radio {
    let mut r = Radio::new(
        Chrome::ours("Machine", "Which machine is this?").first(),
        o("The stick knows these machines.  None of them was backed up from this computer.  Pick the one you are restoring."),
        vec![
            o("lovelace   Debian 13      newest backup 22 Nov 2026 02:00 CET"),
            o("babbage    Fedora 45      newest backup 21 Nov 2026 03:00 EST"),
            Clean::line("Łódź\x1b[2J\u{202e}中   Gentoo   newest backup 18 Nov 2026 01:00 UTC", cap::LINE),
        ],
        None,
    );
    r.none = o("Pick a machine first: move to it with the arrows.");
    r.after = o("The one you pick is restored onto this computer, so check the name:\nit is the computer's name from when it was backed up.");
    r.buttons = vec![Button::ok(), Button::other("Type a location", None, 1), Button::other("Keyboard", Some('k'), 2), Button::shell()];
    r
}

fn actions(n: usize) -> Menu {
    let items = (0..n).map(|i| (o(&format!("Item {i}")), o(&format!("what item {i} does")))).collect();
    let mut m = Menu::new(Chrome::ours("Menu", "What now?"), o("A menu: the arrows move, Enter picks."), items);
    m.buttons.push(Button::shell());
    m
}

/// Every widget, in the states worth seeing.
fn widgets() -> Vec<(&'static str, Canvas, Dialog)> {
    let mut v = Vec::new();
    let mut push = |name, mut d: Dialog| v.push((name, d.canvas(), d));

    let mut d = Dialog::radio(&machine());
    d.key(Key::Enter);
    // The notice, as the runner sets it on OK with nothing chosen.
    d.notice = Some(machine().none);
    push("w-radio-none", d);
    let mut d = Dialog::radio(&machine());
    keys(&mut d, &[Key::Down, Key::Down]);
    push("w-radio", d);

    let mut d = Dialog::menu(&actions(20));
    keys(&mut d, &[Key::Down; 14]);
    push("w-menu", d);
    let mut d = Dialog::menu(&actions(4));
    keys(&mut d, &[Key::Down, Key::Tab, Key::Tab]);
    push("w-menu-buttons", d);

    let mut i = Input::new(Chrome::ours("Machine", "Where is the backup?"), o("Type the address of your backup, as written on the sheet from when the backups were set up:"));
    i.after = o("For example:  sftp://nas.local/backotter/lovelace\n              rest:http://10.0.0.5:8000/lovelace");
    i.buttons = vec![Button::ok(), Button::other("Keyboard", Some('k'), 1), Button::shell(), Button::back()];
    let mut d = Dialog::input(&i);
    keys(&mut d, &"sftp://nas.local/backotter/love".chars().map(Key::Char).collect::<Vec<_>>());
    push("w-input", d);
    let mut d = Dialog::input(&i);
    let long = format!("sftp://nas.local/{}/lovelace", "very-long-folder-name/".repeat(5));
    keys(&mut d, &long.chars().map(Key::Char).collect::<Vec<_>>());
    push("w-input-long", d);

    let p = Password::new(Chrome::ours("Repository", "The backup password"), o("lovelace's backups are kept at sftp://nas.local/backotter/lovelace.\n\nType the backup password:"));
    let mut d = Dialog::password(&p);
    keys(&mut d, &[Key::Char('p'), Key::Char('ä'), Key::Space, Key::Char('s'), Key::Char('x'), Key::Backspace, Key::Char('s')]);
    push("w-password", d);

    let mut q = Question::new(Chrome::ours("Restore", "Stop the restore?"), o("The disks are part written: stopping now leaves this computer unable to start until a restore finishes.  Your backup is safe: nothing in it has been changed."));
    q.yes = o("Stop");
    q.no = o("Keep going");
    q.no_first = true;
    push("w-yes-no", Dialog::question(&q));

    let m = Message::new(Chrome::ours("Done", "lovelace is back"), o("Every file is back, and lovelace will start from the Crucial P3.  Take the stick out, then press Enter to restart."));
    push("w-message", Dialog::message(&m));

    let mut lines = vec![o("# == partition (2 disks)"), o("# GPT, 4 partitions, as lovelace's sda"), o("wipefs --all /dev/nvme0n1")];
    lines.push(o("/dev/nvme0n1p1 : start=2048, size=1048576, type=C12A7328-F81F-11D2-BA4B-00A0C93EC93B, uuid=0F3A9B21-7C44-4E0D-9F11-2B6E8D0C5A17"));
    for i in 0..30 {
        lines.push(Clean::line(&format!("cryptsetup open --key-file /run/bko-rescue/k/{i} /dev/nvme0n1p3 cryptroot \x1b[31mred"), cap::LOG_LINE));
    }
    let mut d = Dialog::view(&TextView::new(Chrome::ours("Review", "Every command, in order"), lines));
    keys(&mut d, &[Key::Down, Key::Down, Key::Down, Key::Right, Key::Right]);
    push("w-view", d);

    let mut p = Progress::new(Chrome::ours("Restore", "Restoring lovelace"), o(""));
    p.buttons = vec![Button::other("Cancel", None, 1)];
    let mut d = Dialog::progress(&p);
    d.set_progress(
        Some(o("  [x] Make partitions           2 s\n  [x] Set up encryption         9 s\n  [>] Copy your files back      running\n  [ ] Make it bootable\n\nNow: copying your files back.")),
        Some(62),
        o("38.2 of 61.2 GB   184,233 files   about 14 min left"),
    );
    d.set_elapsed(21 * 60 + 7);
    push("w-progress", d);
    v
}

fn all_styles(c: &Canvas) -> String {
    let mut s = String::new();
    for lines in [Lines::Utf8, Lines::Dec, Lines::Ascii] {
        for colour in [true, false] {
            s.push_str(&format!("== {} {} ==\n", lines.name(), if colour { "colour" } else { "mono" }));
            s.push_str(&c.dump(lines, colour));
        }
    }
    s
}

#[test]
fn every_widget_in_every_line_style_colour_and_mono() {
    for (name, c, _) in widgets() {
        golden(&format!("tests/screens/{name}.txt"), &all_styles(&c));
    }
}

/// rescue-screens.md's "Too small a terminal", 60x14, drawn at the
/// terminal's own size: the widget makes RT5's hand-drawn goldens.
#[test]
fn the_too_small_notice() {
    let c = too_small(Size { rows: 14, cols: 60, assumed: false });
    for lines in [Lines::Utf8, Lines::Ascii] {
        golden(&format!("tests/screens/too-small.{}-colour.txt", lines.name()), &c.dump(lines, true));
    }
}

fn rules(lines: Lines, colour: bool, caps: bko_rescue::term::out::Caps) -> Rules {
    Rules { lines, colour, caps, rows: 24, cols: 80 }
}

const S80: Size = Size { rows: 24, cols: 80, assumed: false };

#[test]
fn every_widget_sends_only_what_its_profile_takes() {
    for (lines, colour, caps) in common::profiles() {
        for (name, c, _) in widgets() {
            let mut scr = Screen::new(lines, colour, caps, S80);
            let f = scr.frame(&c);
            assert!(f.len() < 4096, "{name} {}: a full frame of {} bytes", common::profile_name(lines, colour, caps), f.len());
            let mut ck = Checker::new(rules(lines, colour, caps));
            ck.feed(&f);
            assert_eq!(ck.result(), Ok(()), "{name} {}", common::profile_name(lines, colour, caps));
        }
    }
}

#[test]
fn moving_in_a_list_or_a_view_is_small() {
    for (lines, colour, caps) in common::profiles() {
        let pn = common::profile_name(lines, colour, caps);
        for (name, mut d, ks) in [
            ("menu", Dialog::menu(&actions(20)), vec![Key::Down; 16]),
            ("radio", Dialog::radio(&machine()), vec![Key::Down, Key::Down, Key::Up]),
            ("buttons", Dialog::radio(&machine()), vec![Key::Tab, Key::Tab, Key::Right]),
        ] {
            let mut scr = Screen::new(lines, colour, caps, S80);
            let mut ck = Checker::new(rules(lines, colour, caps));
            ck.feed(&scr.frame(&d.canvas()));
            let mut scrolled = 0;
            for k in ks {
                let top = d.list().map(|l| l.top());
                d.key(k);
                let f = scr.frame(&d.canvas());
                // A step of the focus redraws two rows: under 200 bytes.
                // Scrolling by a row redraws every row, under 1200
                // (rescue-tui.md, "What RT6 and RT7 found").
                let scrolls = d.list().map(|l| l.top()) != top;
                scrolled += usize::from(scrolls);
                let budget = if scrolls { 1200 } else { 200 };
                assert!(f.len() < budget, "{name} {pn} {k:?}: {} bytes", f.len());
                ck.feed(&f);
            }
            assert_eq!(scrolled > 0, name == "menu", "{name}: only the menu scrolls here");
            assert_eq!(ck.result(), Ok(()), "{name} {pn}");
        }
    }
}

#[test]
fn a_gauge_step_is_under_40_bytes() {
    for (lines, colour, caps) in common::profiles() {
        let pn = common::profile_name(lines, colour, caps);
        let mut p = Progress::new(Chrome::ours("Restore", "Restoring lovelace"), o("[>] Copy your files back"));
        p.buttons = vec![Button::other("Cancel", Some('c'), 1)];
        let mut d = Dialog::progress(&p);
        // The runner asks the ticker before the first frame.
        d.set_progress(None, Some(0), o("of 61.2 GB"));
        let mut scr = Screen::new(lines, colour, caps, S80);
        let mut ck = Checker::new(rules(lines, colour, caps));
        ck.feed(&scr.frame(&d.canvas()));
        for step in 1..=20u8 {
            d.set_progress(None, Some(step * 5), o("of 61.2 GB"));
            let f = scr.frame(&d.canvas());
            let budget = if step == 1 { 50 } else { 40 };
            assert!(f.len() < budget, "{pn} step {step}: {} bytes: {}", f.len(), bko_rescue::testkit::escape(&f));
            ck.feed(&f);
        }
        assert_eq!(ck.result(), Ok(()), "{pn}");
    }
}

/// Random keys into every widget, in random profiles: never a panic,
/// every frame allowed, the focus always somewhere real.
#[test]
fn random_keys_into_every_widget() {
    use bko_rescue::testkit::{fuzz_cases, Rng};
    let ks = [
        Key::Up, Key::Down, Key::Left, Key::Right, Key::Tab, Key::Enter, Key::Esc, Key::Space, Key::Backspace,
        Key::Char('a'), Key::Char('L'), Key::Char('k'), Key::Char('\u{e9}'), Key::Char('\u{202e}'), Key::Char('中'),
    ];
    let profiles = common::profiles();
    let mut rng = Rng::new(0x5eed);
    let n = widgets().len();
    for case in 0..fuzz_cases(100_000) / 250 {
        let (lines, colour, caps) = *rng.pick(&profiles);
        let (name, _, mut d) = widgets().into_iter().nth(case % n).unwrap();
        let mut scr = Screen::new(lines, colour, caps, S80);
        let mut ck = Checker::new(rules(lines, colour, caps));
        ck.feed(&scr.frame(&d.canvas()));
        for _ in 0..rng.below(60) {
            d.key(*rng.pick(&ks));
            ck.feed(&scr.frame(&d.canvas()));
        }
        assert_eq!(ck.result(), Ok(()), "{name} {}", common::profile_name(lines, colour, caps));
    }
}
