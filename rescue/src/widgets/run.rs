//! The full-screen runner: the `Ui` on a terminal.  One loop per dialog
//! around the events (term/event.rs): keys to the dialog, resizes, the
//! periodic full redraw (the profile's `refresh_s`, which heals a screen
//! a kernel message garbled), a progress dialog's ticker and its timer,
//! and SIGHUP, SIGTERM and hang-ups, which end it with `Answer::Quit`.
//!
//! - Each new dialog is a full redraw; within one, only what changed is
//!   sent, once the keys that came together are all handled.
//! - A terminal smaller than 80x24 gets the too-small notice instead
//!   (D10), at its own size, until it grows.
//! - On the first screen (`Chrome::first`) `L` cycles the line style,
//!   `C` turns colour on and off, and `P` answers `Answer::Plain` (D4).
//!
//! Where events come from and bytes go is the `Io` trait: `Term` for the
//! real terminal, a fake in the tests.

use std::io;

use crate::screen::Screen;
use crate::secret::Secret;
use crate::term::event::{Event, Term};
use crate::term::keys::Key;
use crate::term::profile::Profile;
use crate::term::tty::{self, Size};
use crate::ui::{self, Act, Answer, Tick, Ui};

use super::small::too_small;
use super::{Dialog, Did, NO};

pub trait Io {
    /// The next event; `Tick` at `until` (from `now_ms`) if nothing came.
    fn next(&mut self, until: Option<u64>) -> io::Result<Event>;
    /// Whether events are waiting already.
    fn queued(&self) -> bool;
    fn now_ms(&mut self) -> u64;
    /// The terminal's size, read now.
    fn measure(&mut self) -> Size;
    /// One frame's bytes.
    fn write(&mut self, b: &[u8]) -> io::Result<()>;
    /// `L` or `C` changed the profile.
    fn set_profile(&mut self, p: &Profile);
}

impl Io for Term {
    fn next(&mut self, until: Option<u64>) -> io::Result<Event> {
        self.events.next(until)
    }

    fn queued(&self) -> bool {
        self.events.queued()
    }

    fn now_ms(&mut self) -> u64 {
        self.events.now_ms()
    }

    fn measure(&mut self) -> Size {
        tty::size()
    }

    fn write(&mut self, b: &[u8]) -> io::Result<()> {
        Term::write(self, b)
    }

    fn set_profile(&mut self, p: &Profile) {
        Term::set_profile(self, p);
    }
}

/// How a dialog's loop ended.
enum Ran {
    Act(Act),
    Plain,
    Quit,
    Done,
}

impl Ran {
    fn answer<T>(self) -> Answer<T> {
        match self {
            Ran::Act(Act::Back) => Answer::Back,
            Ran::Act(Act::Shell) => Answer::Shell,
            Ran::Act(Act::Other(n)) => Answer::Button(n),
            Ran::Plain => Answer::Plain,
            // OK and Done are each dialog's own to answer.
            Ran::Act(Act::Ok) | Ran::Quit | Ran::Done => Answer::Quit,
        }
    }
}

pub struct FullScreen<I: Io> {
    io: I,
    screen: Screen,
    pub profile: Profile,
    signal: Option<i32>,
    error: Option<io::Error>,
    /// When the last full frame went out.
    full_at: u64,
}

impl FullScreen<Term> {
    /// The real terminal, raw, for `p` (not a plain profile).
    pub fn open(p: &Profile) -> io::Result<FullScreen<Term>> {
        let term = Term::open(p)?;
        let size = term.events.size();
        Ok(FullScreen::new(term, p, size))
    }
}

impl<I: Io> FullScreen<I> {
    pub fn new(io: I, p: &Profile, size: Size) -> FullScreen<I> {
        FullScreen {
            io,
            screen: Screen::new(p.lines, p.colour, p.caps, size),
            profile: p.clone(),
            signal: None,
            error: None,
            full_at: 0,
        }
    }

    /// The signal that ended a dialog with `Answer::Quit`, to exit with
    /// as 128 + it once the terminal is back.
    pub fn signal(&self) -> Option<i32> {
        self.signal
    }

    /// The error that ended a dialog with `Answer::Quit`, if one did.
    pub fn error(&self) -> Option<&io::Error> {
        self.error.as_ref()
    }

    pub fn io(&mut self) -> &mut I {
        &mut self.io
    }

    pub fn into_io(self) -> I {
        self.io
    }

    fn small(&self) -> bool {
        !self.screen.fits(crate::screen::ROWS, crate::screen::COLS)
    }

    fn draw(&mut self, d: &mut Dialog) -> io::Result<()> {
        let canvas = if self.small() { too_small(self.screen.size()) } else { d.canvas() };
        let bytes = self.screen.frame(&canvas);
        self.io.write(&bytes)
    }

    fn change_profile(&mut self, lines: bool) {
        if lines {
            // Only the styles this console draws (profile.rs's table).
            self.profile.lines = self.profile.lines.next_in(self.profile.draws);
        } else {
            self.profile.colour = !self.profile.colour;
        }
        self.screen.set_profile(self.profile.lines, self.profile.colour, self.profile.caps);
        self.io.set_profile(&self.profile);
    }

    /// The loop of one dialog, until a button is pressed (`Ran::Act`),
    /// the ticker says Done, or it has to end.
    fn run(&mut self, d: &mut Dialog, mut ticker: Option<&mut dyn FnMut() -> Tick>, fresh: bool) -> Ran {
        match self.run_inner(d, &mut ticker, fresh) {
            Ok(r) => r,
            Err(e) => {
                self.error = Some(e);
                Ran::Quit
            }
        }
    }

    fn run_inner(&mut self, d: &mut Dialog, ticker: &mut Option<&mut dyn FnMut() -> Tick>, fresh: bool) -> io::Result<Ran> {
        let refresh_ms = self.profile.refresh_s.saturating_mul(1000).max(1000);
        let start = self.io.now_ms();
        if fresh {
            self.screen.invalidate();
        }
        let mut next_tick = ticker.is_some().then_some(start);
        loop {
            let now = self.io.now_ms();
            if let (Some(t), Some(f)) = (next_tick, ticker.as_mut()) {
                if now >= t {
                    match f() {
                        Tick::Done => return Ok(Ran::Done),
                        Tick::At { text, pct, line, next_ms } => {
                            d.set_progress(text, pct, line);
                            next_tick = Some(now.saturating_add(next_ms.max(10)));
                        }
                    }
                }
            }
            if ticker.is_some() {
                d.set_elapsed(now.saturating_sub(start) / 1000);
            }
            if now >= self.full_at.saturating_add(refresh_ms) {
                self.screen.invalidate();
            }
            if !self.io.queued() {
                if self.screen_is_full() {
                    self.full_at = now;
                }
                self.draw(d)?;
            }
            // Wake for the ticker, the timer's next second and the redraw.
            let mut until = self.full_at.saturating_add(refresh_ms);
            if let Some(t) = next_tick {
                until = until.min(t);
                let next_s = start + (now.saturating_sub(start) / 1000 + 1) * 1000;
                until = until.min(next_s);
            }
            match self.io.next(Some(until))? {
                Event::Key(k, _) if self.small() => match k {
                    Key::Char('p' | 'P') => return Ok(Ran::Plain),
                    Key::Char('s' | 'S') => return Ok(Ran::Act(Act::Shell)),
                    Key::Enter => {
                        let s = self.io.measure();
                        self.screen.resize(s);
                    }
                    _ => {}
                },
                Event::Key(k, _) => match d.key(k) {
                    Did::Press(i) => return Ok(Ran::Act(d.buttons[i].act)),
                    Did::Letter(c) if d.chrome.first => match c {
                        'l' => self.change_profile(true),
                        'c' => self.change_profile(false),
                        'p' => return Ok(Ran::Plain),
                        _ => {}
                    },
                    _ => {}
                },
                Event::Resize(s) => self.screen.resize(s),
                Event::Tick => {}
                Event::Signal(sig) => {
                    self.signal = Some(sig);
                    return Ok(Ran::Quit);
                }
                Event::Hangup => return Ok(Ran::Quit),
            }
        }
    }

    /// Whether the next frame is a full one (a probe on a copy would cost
    /// a canvas; the screen says it by its size and state instead).
    fn screen_is_full(&self) -> bool {
        self.screen.pending_full()
    }
}

impl<I: Io> Ui for FullScreen<I> {
    fn menu(&mut self, m: &ui::Menu) -> Answer<usize> {
        let mut d = Dialog::menu(m);
        match self.run(&mut d, None, true) {
            Ran::Act(Act::Ok) => match d.list().and_then(|l| l.cur) {
                Some(i) => Answer::Ok(i),
                None => Answer::Back,
            },
            r => r.answer(),
        }
    }

    fn radio(&mut self, r: &ui::Radio) -> Answer<usize> {
        let mut d = Dialog::radio(r);
        let mut fresh = true;
        loop {
            match self.run(&mut d, None, fresh) {
                Ran::Act(Act::Ok) => match d.list().and_then(|l| l.cur) {
                    Some(i) => return Answer::Ok(i),
                    // Not greyed out: it says why, and again each time.
                    None => d.notice = Some(r.none.clone()),
                },
                x => return x.answer(),
            }
            fresh = false;
        }
    }

    fn input(&mut self, i: &ui::Input) -> Answer<String> {
        let mut d = Dialog::input(i);
        match self.run(&mut d, None, true) {
            Ran::Act(Act::Ok) => Answer::Ok(d.field().map(|f| f.value_text()).unwrap_or_default()),
            r => r.answer(),
        }
    }

    fn password(&mut self, p: &ui::Password) -> Answer<Secret> {
        let mut d = Dialog::password(p);
        // The dialog's own Secret is wiped when `d` drops, whatever the
        // answer.
        match self.run(&mut d, None, true) {
            Ran::Act(Act::Ok) => Answer::Ok(d.field_mut().map(|f| f.take_secret()).unwrap_or_default()),
            r => r.answer(),
        }
    }

    fn yes_no(&mut self, q: &ui::Question) -> Answer<bool> {
        let mut d = Dialog::question(q);
        match self.run(&mut d, None, true) {
            Ran::Act(Act::Ok) => Answer::Ok(true),
            Ran::Act(Act::Other(NO)) => Answer::Ok(false),
            r => r.answer(),
        }
    }

    fn message(&mut self, m: &ui::Message) -> Answer<()> {
        let mut d = Dialog::message(m);
        match self.run(&mut d, None, true) {
            Ran::Act(Act::Ok) => Answer::Ok(()),
            r => r.answer(),
        }
    }

    fn view(&mut self, v: &ui::TextView) -> Answer<()> {
        let mut d = Dialog::view(v);
        match self.run(&mut d, None, true) {
            Ran::Act(Act::Ok) => Answer::Ok(()),
            r => r.answer(),
        }
    }

    fn progress(&mut self, p: &ui::Progress, tick: &mut dyn FnMut() -> Tick) -> Answer<()> {
        let mut d = Dialog::progress(p);
        match self.run(&mut d, Some(tick), true) {
            Ran::Done | Ran::Act(Act::Ok) => Answer::Ok(()),
            r => r.answer(),
        }
    }
}

#[cfg(test)]
pub(crate) mod tests {
    use super::*;
    use crate::term::out::{Caps, LineSet, Lines};
    use crate::testkit::{Checker, Rules};
    use crate::text::Clean;
    use crate::ui::{Button, Chrome, Menu, Message, Password, Progress, Question, Radio};
    use std::collections::VecDeque;

    /// Events at set times; time moves only as the runner waits.  With
    /// none left, a hang-up.
    pub struct FakeIo {
        pub script: VecDeque<(u64, Event)>,
        pub now: u64,
        pub size: Size,
        pub frames: Vec<Vec<u8>>,
        pub profiles: Vec<Profile>,
    }

    impl FakeIo {
        pub fn new(script: &[(u64, Event)]) -> FakeIo {
            FakeIo {
                script: script.iter().copied().collect(),
                now: 0,
                size: Size { rows: 24, cols: 80, assumed: false },
                frames: Vec::new(),
                profiles: Vec::new(),
            }
        }
    }

    impl Io for FakeIo {
        fn next(&mut self, until: Option<u64>) -> io::Result<Event> {
            match (self.script.front(), until) {
                (Some(&(at, _)), Some(u)) if at > u => {
                    self.now = self.now.max(u);
                    Ok(Event::Tick)
                }
                (Some(&(at, e)), _) => {
                    self.script.pop_front();
                    self.now = self.now.max(at);
                    if let Event::Resize(s) = e {
                        self.size = s;
                    }
                    Ok(e)
                }
                (None, _) => Ok(Event::Hangup),
            }
        }
        fn queued(&self) -> bool {
            false
        }
        fn now_ms(&mut self) -> u64 {
            self.now
        }
        fn measure(&mut self) -> Size {
            self.size
        }
        fn write(&mut self, b: &[u8]) -> io::Result<()> {
            self.frames.push(b.to_vec());
            Ok(())
        }
        fn set_profile(&mut self, p: &Profile) {
            self.profiles.push(p.clone());
        }
    }

    pub fn profile(lines: Lines, colour: bool, caps: Caps) -> Profile {
        Profile {
            plain: None,
            lines,
            colour,
            caps,
            draws: LineSet::ALL,
            esc_ms: 100,
            refresh_s: 60,
            serial: false,
            rule: "test".into(),
            lines_why: "test".into(),
            colour_why: "test".into(),
        }
    }

    fn keys(ks: &[Key]) -> Vec<(u64, Event)> {
        ks.iter().enumerate().map(|(i, &k)| (i as u64 * 10, Event::Key(k, i as u32))).collect()
    }

    fn ui(script: &[(u64, Event)]) -> FullScreen<FakeIo> {
        let p = profile(Lines::Utf8, true, Caps::ALL);
        FullScreen::new(FakeIo::new(script), &p, Size { rows: 24, cols: 80, assumed: false })
    }

    fn o(s: &str) -> Clean {
        Clean::ours(s)
    }

    fn menu() -> Menu {
        let items = (0..5).map(|i| (o(&format!("m{i}")), o("x"))).collect();
        Menu::new(Chrome::ours("Test", "A menu"), o("Pick one."), items)
    }

    /// Every frame sent is allowed for the profile; the screen they
    /// leave, as text.
    fn checked(f: &FullScreen<FakeIo>) -> String {
        let mut ck = Checker::new(Rules { lines: Lines::Utf8, colour: true, caps: Caps::ALL, rows: 24, cols: 80 });
        for fr in &f.io.frames {
            ck.feed(fr);
        }
        assert_eq!(ck.result(), Ok(()));
        let mut s = String::new();
        for r in 1..=24 {
            for c in 1..=80 {
                s.push(ck.shown(r, c).map_or(' ', |(ch, _)| ch));
            }
            s.push('\n');
        }
        s
    }

    #[test]
    fn a_menu_moves_and_enter_picks() {
        let mut f = ui(&keys(&[Key::Down, Key::Down, Key::Up, Key::Down, Key::Down, Key::Enter]));
        assert_eq!(f.menu(&menu()), Answer::Ok(3));
        checked(&f);
        // One full frame, then small diffs.
        assert!(f.io.frames[0].len() > 1500);
        assert!(f.io.frames[1..].iter().all(|fr| fr.len() < 200), "{:?}", f.io.frames.iter().map(Vec::len).collect::<Vec<_>>());
    }

    #[test]
    fn esc_backs_out_and_buttons_answer() {
        assert_eq!(ui(&keys(&[Key::Esc])).menu(&menu()), Answer::Back);
        let mut m = menu();
        m.buttons.push(Button::shell());
        m.buttons.push(Button::other("Keyboard", Some('k'), 7));
        assert_eq!(ui(&keys(&[Key::Char('s')])).menu(&m), Answer::Shell);
        assert_eq!(ui(&keys(&[Key::Char('K')])).menu(&m), Answer::Button(7));
        assert_eq!(ui(&keys(&[Key::Tab, Key::Tab, Key::Enter])).menu(&m), Answer::Back);
    }

    #[test]
    fn a_radio_with_nothing_chosen_says_why_ok_waits() {
        let r = Radio::new(Chrome::ours("Machine", "Which machine?"), o("Pick."), vec![o("a"), o("b")], None);
        let mut f = ui(&keys(&[Key::Enter, Key::Enter, Key::Down, Key::Down, Key::Enter]));
        assert_eq!(f.radio(&r), Answer::Ok(1));
        let shown = String::from_utf8_lossy(&f.io.frames.concat()).to_string();
        assert!(shown.contains("Pick one first"), "the notice");
        checked(&f);
    }

    #[test]
    fn a_password_is_typed_and_comes_back_as_a_secret() {
        let ks = [Key::Char('p'), Key::Char('w'), Key::Char('x'), Key::Backspace, Key::Space, Key::Char('s'), Key::Enter];
        let mut f = ui(&keys(&ks));
        match f.password(&Password::new(Chrome::ours("Repository", "The backup password"), o("Type it."))) {
            Answer::Ok(s) => assert_eq!(s.expose(), b"pw s"),
            _ => panic!("no secret"),
        }
        let sent = String::from_utf8_lossy(&f.io.frames.concat()).to_string();
        assert!(!sent.contains("pw"), "no cell ever holds the secret");
        assert!(checked(&f).contains("[****"));
    }

    #[test]
    fn yes_no_and_message() {
        let q = Question::new(Chrome::ours("a", "b"), o("Sure?"));
        assert_eq!(ui(&keys(&[Key::Enter])).yes_no(&q), Answer::Ok(true));
        assert_eq!(ui(&keys(&[Key::Right, Key::Enter])).yes_no(&q), Answer::Ok(false));
        assert_eq!(ui(&keys(&[Key::Enter])).message(&Message::new(Chrome::ours("a", "b"), o("Hi."))), Answer::Ok(()));
    }

    #[test]
    fn a_signal_or_hang_up_quits() {
        let mut f = ui(&[(5, Event::Signal(15))]);
        assert_eq!(f.menu(&menu()), Answer::Quit);
        assert_eq!(f.signal(), Some(15));
        let mut f = ui(&[]);
        assert_eq!(f.menu(&menu()), Answer::Quit);
        assert_eq!(f.signal(), None);
    }

    #[test]
    fn a_small_terminal_gets_the_notice_until_it_grows() {
        let small = Size { rows: 14, cols: 60, assumed: false };
        let p = profile(Lines::Utf8, true, Caps::ALL);
        let script = [
            (10, Event::Key(Key::Down, 1)),
            (20, Event::Resize(Size { rows: 24, cols: 80, assumed: false })),
            (30, Event::Key(Key::Enter, 2)),
        ];
        let mut f = FullScreen::new(FakeIo::new(&script), &p, small);
        assert_eq!(f.menu(&menu()), Answer::Ok(0), "Down went to the notice, not the menu");
        let first = String::from_utf8_lossy(&f.io.frames[0]).to_string();
        assert!(first.contains("This window is too small") && first.contains("60 columns and 14 rows"));
        let after = String::from_utf8_lossy(&f.io.frames.concat()).to_string();
        assert!(after.contains("A menu"), "the dialog after the resize");
        // p goes on in plain mode; Enter measures again.
        let mut f = FullScreen::new(FakeIo::new(&[(10, Event::Key(Key::Char('p'), 1))]), &p, small);
        assert_eq!(f.menu(&menu()), Answer::Plain);
        let mut f = FullScreen::new(FakeIo::new(&[(10, Event::Key(Key::Enter, 1)), (20, Event::Key(Key::Enter, 2))]), &p, small);
        f.io.size = Size { rows: 30, cols: 100, assumed: false };
        assert_eq!(f.menu(&menu()), Answer::Ok(0), "Enter measured 100x30, then Enter picked");
    }

    #[test]
    fn the_first_screen_takes_l_c_and_p() {
        let mut m = menu();
        m.chrome = m.chrome.first();
        let mut f = ui(&keys(&[Key::Char('L'), Key::Char('c'), Key::Char('P')]));
        assert_eq!(f.menu(&m), Answer::Plain);
        assert_eq!((f.profile.lines, f.profile.colour), (Lines::Dec, false));
        assert_eq!(f.io.profiles.len(), 2);
        // Elsewhere they do nothing.
        let mut f = ui(&keys(&[Key::Char('l'), Key::Char('p'), Key::Enter]));
        assert_eq!(f.menu(&menu()), Answer::Ok(0));
        assert_eq!(f.profile.lines, Lines::Utf8);
    }

    /// On a console that can't draw DEC (the Linux VT), L skips it.
    #[test]
    fn l_skips_what_the_console_cant_draw() {
        let mut m = menu();
        m.chrome = m.chrome.first();
        let mut f = ui(&keys(&[Key::Char('L'), Key::Char('P')]));
        f.profile.draws = LineSet::NO_DEC;
        assert_eq!(f.menu(&m), Answer::Plain);
        assert_eq!(f.profile.lines, Lines::Ascii);
    }

    #[test]
    fn a_waiting_dialog_is_redrawn_in_full_every_refresh() {
        let mut f = ui(&[(150_000, Event::Key(Key::Enter, 1))]);
        assert_eq!(f.menu(&menu()), Answer::Ok(0));
        let full: Vec<usize> = f.io.frames.iter().map(Vec::len).filter(|&n| n > 1500).collect();
        assert_eq!(full.len(), 3, "at 0, 60 and 120 s: {:?}", f.io.frames.iter().map(Vec::len).collect::<Vec<_>>());
    }

    #[test]
    fn progress_ticks_to_done_with_small_steps() {
        let mut p = Progress::new(Chrome::ours("Restore", "Restoring lovelace"), o("Copying your files back."));
        p.buttons = vec![Button::other("Cancel", Some('x'), 1)];
        let mut f = ui(&[]);
        f.io.script.push_back((1_000_000, Event::Key(Key::Esc, 1)));
        let mut n = 0u8;
        let mut tick = || {
            n += 1;
            if n > 20 {
                Tick::Done
            } else {
                Tick::At { text: None, pct: Some(n * 5), line: o("of 61.2 GB"), next_ms: 500 }
            }
        };
        assert_eq!(f.progress(&p, &mut tick), Answer::Ok(()));
        let screen = checked(&f);
        // Past the first frame and the first step, each update is small:
        // the bar's new cells, the figures, and the timer once a second.
        let sizes: Vec<usize> = f.io.frames.iter().map(Vec::len).collect();
        assert!(sizes[2..].iter().all(|&s| s < 60), "{sizes:?}");
        assert!(screen.contains("100 %") && screen.contains("0:09"), "the timer counts:\n{screen}");
        // A button's letter leaves it, Enter doesn't.
        let mut f = ui(&keys(&[Key::Enter, Key::Char('x')]));
        let mut tick = || Tick::At { text: None, pct: None, line: o("running"), next_ms: 5 };
        assert_eq!(f.progress(&p, &mut tick), Answer::Button(1));
    }
}
