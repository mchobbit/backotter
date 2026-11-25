//! The dialog API the app talks to (docs/design/rescue-tui.md, "Modules"):
//! the `Ui` trait, what each dialog shows, and what comes back.  Three
//! implementations: `widgets::FullScreen` and `plain::Plain` for people,
//! `Scripted` (a queue of replies, a record of what was asked) for the
//! app's tests.  The app never touches the terminal.
//!
//! Every string a dialog shows is a `Clean`, so nothing unsanitised can
//! reach the screen or plain mode's lines.

use std::collections::VecDeque;

use crate::secret::Secret;
use crate::text::Clean;

/// What a dialog ended with.
#[derive(Debug, PartialEq, Eq)]
pub enum Answer<T> {
    /// OK, or Enter on the body: the choice, the text, the secret.
    Ok(T),
    /// < Back >, or Esc where the dialog has a Back button.
    Back,
    /// `s` or < Shell >.
    Shell,
    /// Another button, by the number its `Act::Other` gave it.
    Button(usize),
    /// `P` on the first screen, or `p` on the too-small notice: go on in
    /// plain mode at this step.
    Plain,
    /// A signal or a hang-up: put the terminal back and exit.
    Quit,
}

impl<T> Answer<T> {
    pub fn map<U>(self, f: impl FnOnce(T) -> U) -> Answer<U> {
        match self {
            Answer::Ok(t) => Answer::Ok(f(t)),
            Answer::Back => Answer::Back,
            Answer::Shell => Answer::Shell,
            Answer::Button(n) => Answer::Button(n),
            Answer::Plain => Answer::Plain,
            Answer::Quit => Answer::Quit,
        }
    }
}

/// What a button does.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Act {
    Ok,
    Back,
    Shell,
    Other(usize),
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Button {
    pub label: Clean,
    /// Its letter, pressed when the focus isn't in a text field; either
    /// case works.  Shown on the key line.
    pub key: Option<char>,
    pub act: Act,
}

impl Button {
    pub fn new(label: &str, key: Option<char>, act: Act) -> Button {
        Button { label: Clean::ours(label), key: key.map(|c| c.to_ascii_lowercase()), act }
    }

    pub fn ok() -> Button {
        Button::new("OK", None, Act::Ok)
    }

    /// Back: Esc presses it too.
    pub fn back() -> Button {
        Button::new("Back", None, Act::Back)
    }

    pub fn shell() -> Button {
        Button::new("Shell", Some('s'), Act::Shell)
    }

    pub fn other(label: &str, key: Option<char>, n: usize) -> Button {
        Button::new(label, key, Act::Other(n))
    }
}

/// What every dialog has around it.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Chrome {
    /// The screen's name, at the right of the title line.
    pub screen: Clean,
    /// The title, in the box's top border (in plain mode, its first line).
    pub title: Clean,
    /// The key line; None makes one from the dialog's parts and buttons.
    pub keys: Option<Clean>,
    /// The first screen: `L`, `C` and `P` work (D4), and the key line
    /// says so.
    pub first: bool,
    /// A line on every screen, in the title line (in plain mode, before
    /// the title): a dry run's `DRY RUN: nothing will be written`
    /// (rescue-tui.md, "Dry run").  Empty for none.
    pub banner: Clean,
}

impl Chrome {
    pub fn new(screen: Clean, title: Clean) -> Chrome {
        Chrome { screen, title, keys: None, first: false, banner: Clean::default() }
    }

    /// From our own strings.
    pub fn ours(screen: &str, title: &str) -> Chrome {
        Chrome::new(Clean::ours(screen), Clean::ours(title))
    }

    pub fn first(mut self) -> Chrome {
        self.first = true;
        self
    }
}

/// A list of things to do: Enter picks the row with the focus.  Each
/// item is a tag and a description, the tags lined up.
#[derive(Clone, Debug)]
pub struct Menu {
    pub chrome: Chrome,
    pub text: Clean,
    pub items: Vec<(Clean, Clean)>,
    pub start: usize,
    pub after: Clean,
    pub buttons: Vec<Button>,
}

impl Menu {
    pub fn new(chrome: Chrome, text: Clean, items: Vec<(Clean, Clean)>) -> Menu {
        Menu { chrome, text, items, start: 0, after: Clean::default(), buttons: vec![Button::ok(), Button::back()] }
    }
}

/// A list of one choice, where the choice follows the focus: the row
/// with the focus is the one marked `(*)` (rescue-screens.md,
/// "Conventions").  With `chosen` None nothing is marked until an arrow
/// moves the focus onto a row, and OK says `none` until then.
#[derive(Clone, Debug)]
pub struct Radio {
    pub chrome: Chrome,
    pub text: Clean,
    pub items: Vec<Clean>,
    pub chosen: Option<usize>,
    pub none: Clean,
    pub after: Clean,
    /// A field under the list to try the choice in, with these words
    /// before it (the Keyboard screen's `Try it here:`); what is typed
    /// there is thrown away.
    pub try_it: Option<Clean>,
    /// Rows that can't be chosen, each with why (a machine whose settings
    /// fail their checksum): OK on one says why, as `none` does.
    pub blocked: Vec<(usize, Clean)>,
    pub buttons: Vec<Button>,
}

impl Radio {
    pub fn new(chrome: Chrome, text: Clean, items: Vec<Clean>, chosen: Option<usize>) -> Radio {
        Radio {
            chrome,
            text,
            items,
            chosen,
            none: Clean::ours("Pick one first: move to it with the arrows."),
            after: Clean::default(),
            try_it: None,
            blocked: Vec::new(),
            buttons: vec![Button::ok(), Button::back()],
        }
    }
}

/// The most a text box takes, in characters.
pub const INPUT_MAX: usize = 256;

/// One line of text: typing and Backspace only (D9).
#[derive(Clone, Debug)]
pub struct Input {
    pub chrome: Chrome,
    pub text: Clean,
    /// What the field starts with.
    pub value: String,
    /// At most INPUT_MAX.
    pub max: usize,
    /// Words before a shorter field on its row, and the field's width
    /// (`To make it, type YES:  [     ]`); None is the whole row.
    pub label: Option<(Clean, u16)>,
    pub after: Clean,
    /// Said on the row above the buttons (in plain mode, after the
    /// text): why OK didn't take what was typed.
    pub notice: Option<Clean>,
    pub buttons: Vec<Button>,
}

impl Input {
    pub fn new(chrome: Chrome, text: Clean) -> Input {
        Input {
            chrome,
            text,
            value: String::new(),
            max: INPUT_MAX,
            label: None,
            after: Clean::default(),
            notice: None,
            buttons: vec![Button::ok(), Button::back()],
        }
    }
}

/// A password: `*` per character (D8), typing and Backspace only.
#[derive(Clone, Debug)]
pub struct Password {
    pub chrome: Chrome,
    pub text: Clean,
    pub after: Clean,
    pub buttons: Vec<Button>,
}

impl Password {
    pub fn new(chrome: Chrome, text: Clean) -> Password {
        Password { chrome, text, after: Clean::default(), buttons: vec![Button::ok(), Button::back()] }
    }
}

/// The recovery key's twelve words, shown in clear, each completed from
/// BIP39's list after four letters (rescue-screens.md, "Recovery key").
/// OK answers them completed, one space between, as a `Secret`.
#[derive(Clone, Debug)]
pub struct Words {
    pub chrome: Chrome,
    pub text: Clean,
    /// Plain mode's text in `text`'s place: the words go on one line
    /// there.
    pub plain: Clean,
    pub after: Clean,
    pub buttons: Vec<Button>,
}

impl Words {
    pub fn new(chrome: Chrome, text: Clean, plain: Clean) -> Words {
        Words { chrome, text, plain, after: Clean::default(), buttons: vec![Button::ok(), Button::back()] }
    }
}

/// Yes or no.  `buttons` are any more after the two (a Back, a Shell).
#[derive(Clone, Debug)]
pub struct Question {
    pub chrome: Chrome,
    pub text: Clean,
    pub yes: Clean,
    pub no: Clean,
    /// The focus starts on No.
    pub no_first: bool,
    /// Esc presses No, where there is no Back button.
    pub esc_no: bool,
    pub buttons: Vec<Button>,
}

impl Question {
    pub fn new(chrome: Chrome, text: Clean) -> Question {
        Question { chrome, text, yes: Clean::ours("Yes"), no: Clean::ours("No"), no_first: false, esc_no: false, buttons: Vec::new() }
    }
}

/// Text and an OK.
#[derive(Clone, Debug)]
pub struct Message {
    pub chrome: Chrome,
    pub text: Clean,
    pub buttons: Vec<Button>,
    /// The button the focus starts on: a fix is never the default
    /// (rescue-screens.md, "Problems and their help").
    pub focus: usize,
}

impl Message {
    pub fn new(chrome: Chrome, text: Clean) -> Message {
        Message { chrome, text, buttons: vec![Button::ok()], focus: 0 }
    }
}

/// Lines that never wrap (the command plan, the log, raw output): a long
/// line is cut with `>`, and Left and Right scroll sideways.
#[derive(Clone, Debug)]
pub struct TextView {
    pub chrome: Chrome,
    pub lines: Vec<Clean>,
    pub buttons: Vec<Button>,
}

impl TextView {
    pub fn new(chrome: Chrome, lines: Vec<Clean>) -> TextView {
        TextView { chrome, lines, buttons: vec![Button::new("Close", None, Act::Ok)] }
    }
}

/// A gauge under some text.  Nothing has the focus at first, so Enter
/// does nothing until Tab reaches a button; buttons' letters work.
#[derive(Clone, Debug)]
pub struct Progress {
    pub chrome: Chrome,
    pub text: Clean,
    pub buttons: Vec<Button>,
}

impl Progress {
    pub fn new(chrome: Chrome, text: Clean) -> Progress {
        Progress { chrome, text, buttons: Vec::new() }
    }
}

/// What a progress dialog's ticker says each time it is asked.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum Tick {
    At {
        /// New text above the gauge (the stages), or None to keep it.
        text: Option<Clean>,
        /// 0-100, or None for no bar (a timer only).
        pct: Option<u8>,
        /// The line under the bar.
        line: Clean,
        /// When to ask again.
        next_ms: u64,
    },
    Done,
}

pub trait Ui {
    fn menu(&mut self, d: &Menu) -> Answer<usize>;
    fn radio(&mut self, d: &Radio) -> Answer<usize>;
    fn input(&mut self, d: &Input) -> Answer<String>;
    fn password(&mut self, d: &Password) -> Answer<Secret>;
    fn yes_no(&mut self, d: &Question) -> Answer<bool>;
    fn message(&mut self, d: &Message) -> Answer<()>;
    fn view(&mut self, d: &TextView) -> Answer<()>;
    fn progress(&mut self, d: &Progress, tick: &mut dyn FnMut() -> Tick) -> Answer<()>;
    fn words(&mut self, d: &Words) -> Answer<Secret>;

    /// A radio list that tells `moved` each row the focus moves to, as
    /// it moves (the Keyboard screen applies a layout so its "Try it
    /// here" field tests it).  Where nothing can be tried as the focus
    /// moves (plain mode, the tests), it is `radio`.
    fn radio_live(&mut self, d: &Radio, moved: &mut dyn FnMut(usize)) -> Answer<usize> {
        let _ = moved;
        self.radio(d)
    }

    /// Whether the dialogs are full screen: the shell needs the terminal
    /// handed back first, and the keyboard can be tried as it changes.
    fn full_screen(&self) -> bool {
        false
    }
}

/// A reply the scripted Ui gives.  Its Debug never shows a secret's
/// bytes, only how many.
#[derive(Clone, PartialEq, Eq)]
pub enum Reply {
    /// A menu's or radio list's row.
    Pick(usize),
    Text(String),
    Secret(Vec<u8>),
    Yes,
    No,
    /// A message, a view, or a progress run to its end.
    Ok,
    Back,
    Shell,
    Button(usize),
    Plain,
    Quit,
}

impl std::fmt::Debug for Reply {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Reply::Pick(i) => write!(f, "Pick({i})"),
            Reply::Text(s) => write!(f, "Text({s:?})"),
            Reply::Secret(b) => write!(f, "Secret(<{} bytes>)", b.len()),
            Reply::Yes => f.write_str("Yes"),
            Reply::No => f.write_str("No"),
            Reply::Ok => f.write_str("Ok"),
            Reply::Back => f.write_str("Back"),
            Reply::Shell => f.write_str("Shell"),
            Reply::Button(n) => write!(f, "Button({n})"),
            Reply::Plain => f.write_str("Plain"),
            Reply::Quit => f.write_str("Quit"),
        }
    }
}

/// The Ui for the app's tests: each dialog takes the next reply, and is
/// recorded as (kind, title).  A reply of the wrong kind, or none left,
/// is a test failure (a panic).
#[derive(Default)]
pub struct Scripted {
    pub replies: VecDeque<Reply>,
    pub asked: Vec<(&'static str, String)>,
}

impl Scripted {
    pub fn new(replies: impl IntoIterator<Item = Reply>) -> Scripted {
        Scripted { replies: replies.into_iter().collect(), asked: Vec::new() }
    }

    fn next<T>(&mut self, kind: &'static str, c: &Chrome, ok: impl FnOnce(Reply) -> Option<T>) -> Answer<T> {
        self.asked.push((kind, c.title.as_str().to_string()));
        let r = match self.replies.pop_front() {
            Some(r) => r,
            None => panic!("scripted Ui: no reply left for {kind} {:?}", c.title.as_str()),
        };
        match r {
            Reply::Back => Answer::Back,
            Reply::Shell => Answer::Shell,
            Reply::Button(n) => Answer::Button(n),
            Reply::Plain => Answer::Plain,
            Reply::Quit => Answer::Quit,
            other => {
                let what = format!("{other:?}");
                match ok(other) {
                    Some(t) => Answer::Ok(t),
                    None => panic!("scripted Ui: {what} isn't a reply to {kind} {:?}", c.title.as_str()),
                }
            }
        }
    }
}

impl Ui for Scripted {
    fn menu(&mut self, d: &Menu) -> Answer<usize> {
        let n = d.items.len();
        self.next("menu", &d.chrome, |r| match r {
            Reply::Pick(i) if i < n => Some(i),
            _ => None,
        })
    }

    fn radio(&mut self, d: &Radio) -> Answer<usize> {
        let n = d.items.len();
        self.next("radio", &d.chrome, |r| match r {
            Reply::Pick(i) if i < n => Some(i),
            _ => None,
        })
    }

    fn input(&mut self, d: &Input) -> Answer<String> {
        let max = d.max.min(INPUT_MAX);
        self.next("input", &d.chrome, |r| match r {
            Reply::Text(s) if s.chars().count() <= max => Some(s),
            _ => None,
        })
    }

    fn password(&mut self, d: &Password) -> Answer<Secret> {
        self.next("password", &d.chrome, |r| match r {
            Reply::Secret(mut b) => {
                let s = Secret::from_bytes(&b);
                crate::secret::wipe(&mut b);
                Some(s)
            }
            _ => None,
        })
    }

    fn yes_no(&mut self, d: &Question) -> Answer<bool> {
        self.next("yes_no", &d.chrome, |r| match r {
            Reply::Yes => Some(true),
            Reply::No => Some(false),
            _ => None,
        })
    }

    fn message(&mut self, d: &Message) -> Answer<()> {
        self.next("message", &d.chrome, |r| (r == Reply::Ok).then_some(()))
    }

    fn view(&mut self, d: &TextView) -> Answer<()> {
        self.next("view", &d.chrome, |r| (r == Reply::Ok).then_some(()))
    }

    /// `Ok` runs the ticker to its end (at most 100,000 calls); any other
    /// reply asks it once first.
    fn progress(&mut self, d: &Progress, tick: &mut dyn FnMut() -> Tick) -> Answer<()> {
        let to_end = self.replies.front() == Some(&Reply::Ok);
        let calls = if to_end { 100_000 } else { 1 };
        for _ in 0..calls {
            if tick() == Tick::Done {
                break;
            }
        }
        self.next("progress", &d.chrome, |r| (r == Reply::Ok).then_some(()))
    }

    fn words(&mut self, d: &Words) -> Answer<Secret> {
        self.next("words", &d.chrome, |r| match r {
            Reply::Secret(mut b) => {
                let s = Secret::from_bytes(&b);
                crate::secret::wipe(&mut b);
                Some(s)
            }
            _ => None,
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn scripted_replies_in_order_and_records_what_was_asked() {
        let mut ui = Scripted::new([Reply::Pick(1), Reply::Back, Reply::Text("x".into()), Reply::Secret(b"pw".to_vec()), Reply::No]);
        let c = Chrome::ours("Machine", "Which machine?");
        let m = Menu::new(c.clone(), Clean::ours(""), vec![(Clean::ours("a"), Clean::ours("")); 2]);
        assert_eq!(ui.menu(&m), Answer::Ok(1));
        assert_eq!(ui.menu(&m), Answer::Back);
        assert_eq!(ui.input(&Input::new(c.clone(), Clean::ours(""))), Answer::Ok("x".to_string()));
        match ui.password(&Password::new(c.clone(), Clean::ours(""))) {
            Answer::Ok(s) => assert_eq!(s.expose(), b"pw"),
            _ => panic!("not a secret"),
        }
        assert_eq!(ui.yes_no(&Question::new(c, Clean::ours(""))), Answer::Ok(false));
        assert_eq!(ui.asked.len(), 5);
        assert_eq!(ui.asked[0], ("menu", "Which machine?".to_string()));
        assert_eq!(ui.asked[3].0, "password");
    }

    #[test]
    fn scripted_progress_runs_the_ticker_to_its_end() {
        let mut ui = Scripted::new([Reply::Ok, Reply::Button(3)]);
        let mut n = 0;
        let p = Progress::new(Chrome::ours("Restore", "Restoring"), Clean::ours(""));
        let mut t = || {
            n += 1;
            if n == 5 {
                Tick::Done
            } else {
                Tick::At { text: None, pct: Some(n * 10), line: Clean::ours(""), next_ms: 500 }
            }
        };
        assert_eq!(ui.progress(&p, &mut t), Answer::Ok(()));
        assert_eq!(ui.progress(&p, &mut t), Answer::Button(3));
        assert_eq!(n, 6);
    }

    #[test]
    #[should_panic(expected = "isn't a reply to")]
    fn a_wrong_reply_fails_the_test() {
        let mut ui = Scripted::new([Reply::Yes]);
        ui.menu(&Menu::new(Chrome::ours("a", "b"), Clean::ours(""), vec![]));
    }

    #[test]
    #[should_panic(expected = "no reply left")]
    fn running_out_fails_the_test() {
        Scripted::default().message(&Message::new(Chrome::ours("a", "b"), Clean::ours("")));
    }

    #[test]
    fn a_secret_reply_never_shows_its_bytes() {
        let r = Reply::Secret(b"hunter2".to_vec());
        assert_eq!(format!("{r:?}"), "Secret(<7 bytes>)");
        let mut ui = Scripted::new([r]);
        let got = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
            ui.yes_no(&Question::new(Chrome::ours("a", "b"), Clean::ours("")));
        }));
        let msg = got.err().and_then(|e| e.downcast::<String>().ok()).map(|s| *s).unwrap_or_default();
        assert!(msg.contains("isn't a reply to") && !msg.contains("hunter2"), "{msg}");
    }

    #[test]
    fn map_keeps_the_other_answers() {
        assert_eq!(Answer::Ok(2).map(|x| x * 2), Answer::Ok(4));
        assert_eq!(Answer::<u8>::Shell.map(|x| x), Answer::Shell);
        assert_eq!(Answer::<u8>::Button(7).map(|x| x), Answer::Button(7));
    }
}
