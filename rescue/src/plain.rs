//! Plain mode (docs/design/rescue-tui.md, "Plain mode"; rescue-screens.md,
//! "Plain mode"): the same `Ui` calls as numbered questions and answers,
//! one line at a time, for a screen reader, a dumb terminal or a serial
//! log.
//!
//! - No escape sequences at all, and nothing rewrites a line: every
//!   string is a `Clean`, and non-ASCII shows as `?` unless the locale
//!   is UTF-8.  Text wraps at 72 columns.
//! - Lines are read in cooked mode, at most 4 KiB each.  `ISIG` stays
//!   on, so Ctrl-C ends bko-rescue (the launcher starts it again).
//!   Reads wait in poll, so a signal caught meanwhile (`tty::Quiet`, or
//!   a caller that catches SIGINT, SIGHUP and SIGTERM while a child
//!   runs) ends the dialog with `Answer::Quit`.
//! - A password is read with echo, line editing and ISIG off
//!   (`tty::Quiet`), byte by byte into the `Secret`, never through a
//!   `String`: Backspace takes a character off, Ctrl-U empties it,
//!   Ctrl-C quits.  Nothing typed is shown.  A SIGHUP or SIGTERM then
//!   ends the program only once echo is back on.
//! - An empty line where text is typed shows the other choices, so no
//!   text or password can be taken for a command.
//! - Progress is one line per change of the text above the gauge, and a
//!   percentage line at most every 10 % or 60 s.

use std::fs::File;
use std::io::{self, Read as _, Write as _};
use std::mem::ManuallyDrop;
use std::time::Instant;

use crate::secret::Secret;
use crate::term::ffi;
use crate::term::out::Lines;
use crate::term::tty;
use crate::text::{self, Clean};
use crate::ui::{self, Act, Answer, Button, Chrome, Tick, Ui};

/// The longest line read; the rest of a longer one is dropped, and the
/// question asked again.
pub const LINE_MAX: usize = 4096;
/// Where plain mode wraps.
pub const WIDTH: usize = 72;
/// A percentage line at most this often, unless 10 % more is done.
pub const PCT_EVERY_MS: u64 = 60_000;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Byte {
    Got(u8),
    Timeout,
    Eof,
    /// A caught signal is waiting: the dialog ends, and what was typed
    /// of a line is dropped.
    Signal,
}

/// Where plain mode's lines go and its bytes come from.
pub trait Line {
    fn write(&mut self, s: &str) -> io::Result<()>;
    /// One byte, waiting at most `timeout_ms` (None: until one comes).
    fn read(&mut self, timeout_ms: Option<u64>) -> io::Result<Byte>;
    /// Echo and line editing off (for a password), or back on.
    fn quiet(&mut self, on: bool) -> io::Result<()>;
    fn now_ms(&mut self) -> u64;
}

/// fd 0 and stdout.  fd 0 is read a byte at a time, with no buffer of
/// ours, so a password's bytes go only into its `Secret`.
pub struct StdLine {
    input: ManuallyDrop<File>,
    start: Instant,
    quiet: Option<tty::Quiet>,
}

impl Default for StdLine {
    fn default() -> Self {
        StdLine::new()
    }
}

impl StdLine {
    pub fn new() -> StdLine {
        StdLine { input: ffi::stdin_file(), start: Instant::now(), quiet: None }
    }
}

impl Line for StdLine {
    fn write(&mut self, s: &str) -> io::Result<()> {
        let mut o = io::stdout().lock();
        o.write_all(s.as_bytes())?;
        o.flush()
    }

    fn read(&mut self, timeout_ms: Option<u64>) -> io::Result<Byte> {
        self.read_polled(timeout_ms)
    }

    fn quiet(&mut self, on: bool) -> io::Result<()> {
        use std::io::IsTerminal as _;
        if !on {
            self.quiet = None;
        } else if self.quiet.is_none() && io::stdin().is_terminal() {
            self.quiet = Some(tty::Quiet::enter()?);
        }
        Ok(())
    }

    fn now_ms(&mut self) -> u64 {
        u64::try_from(self.start.elapsed().as_millis()).unwrap_or(u64::MAX)
    }
}

impl StdLine {
    /// Wait in poll a second at a time, never in read, which glibc's
    /// signal() (SA_RESTART) would restart after a caught signal.  A
    /// caught signal waiting is `Byte::Signal`: with echo off the caller
    /// turns echo back on, and then the signal ends the program
    /// (`tty::Quiet`); the demo's restore page catches them while the
    /// fake runs, and cleans up first.  With no handler, nothing is ever
    /// pending: the signal has ended the program already.
    fn read_polled(&mut self, timeout_ms: Option<u64>) -> io::Result<Byte> {
        let start = self.now_ms();
        loop {
            if ffi::signal_pending() {
                return Ok(Byte::Signal);
            }
            let wait = match timeout_ms {
                Some(t) => {
                    let gone = self.now_ms().saturating_sub(start);
                    if gone >= t {
                        return Ok(Byte::Timeout);
                    }
                    (t - gone).min(1000)
                }
                None => 1000,
            };
            match ffi::poll_in(0, i32::try_from(wait).unwrap_or(1000)) {
                Ok(true) => return self.read_byte(),
                Ok(false) => {}
                Err(e) if e.kind() == io::ErrorKind::Interrupted => {}
                Err(e) => return Err(e),
            }
        }
    }

    fn read_byte(&mut self) -> io::Result<Byte> {
        let mut b = [0u8; 1];
        loop {
            return match self.input.read(&mut b) {
                Ok(0) => Ok(Byte::Eof),
                Ok(_) => Ok(Byte::Got(b[0])),
                Err(e) if e.kind() == io::ErrorKind::Interrupted => continue,
                // EIO: the terminal went away.
                Err(e) if e.raw_os_error() == Some(5) => Ok(Byte::Eof),
                Err(e) => Err(e),
            };
        }
    }
}

/// A line read.
enum Read {
    Line(String),
    TooLong,
    Timeout,
    Eof,
}

/// What a choice prompt got.
enum Pick {
    Num(usize),
    /// One of the lettered choices, by its index.
    Choice(usize),
    Quit,
}

/// A lettered choice: its letter, its words, and what it does.
struct Opt {
    key: char,
    label: Clean,
    act: Act,
}

/// Plain mode on a `Line`.
pub struct Plain<L: Line> {
    line: L,
    lines: Lines,
    started: bool,
    /// Bytes of a line not yet ended (a progress dialog reads with
    /// timeouts).
    pending: Vec<u8>,
    overflow: bool,
    /// A CR ended the last line: drop the LF after it.
    after_cr: bool,
    error: Option<io::Error>,
}

/// Letters plain mode gives buttons: their own, then b for Back, s for
/// Shell, o for OK, else the first free letter of the label, else any.
fn letters(buttons: &[Button], taken: &[char]) -> Vec<Opt> {
    let mut used: Vec<char> = taken.to_vec();
    used.extend(buttons.iter().filter_map(|b| b.key));
    let mut v = Vec::new();
    for b in buttons {
        let fixed = b.key.or(match b.act {
            Act::Back => Some('b'),
            Act::Shell => Some('s'),
            Act::Ok => Some('o'),
            Act::Other(_) => None,
        });
        let key = match fixed {
            Some(k) if b.key.is_some() || !used.contains(&k) => k,
            _ => {
                let from_label = b.label.chars().map(|c| c.to_ascii_lowercase()).filter(|c| c.is_ascii_lowercase());
                match from_label.chain('a'..='z').find(|c| !used.contains(c)) {
                    Some(k) => k,
                    None => continue,
                }
            }
        };
        used.push(key);
        v.push(Opt { key, label: lower_first(&b.label), act: b.act });
    }
    v
}

/// A button's label as a choice: "Use recovery key instead" reads "use
/// recovery key instead"; "OK" stays as it is.
fn lower_first(c: &Clean) -> Clean {
    let l = c.as_str();
    if l.chars().all(|c| !c.is_lowercase()) {
        return c.clone();
    }
    let mut cs = l.chars();
    let first: String = cs.next().map(|c| c.to_lowercase().collect()).unwrap_or_default();
    Clean::line(&(first + cs.as_str()), l.len() + 4)
}

impl<L: Line> Plain<L> {
    /// `lines` is the profile's: UTF-8 shows the letters `Clean` keeps,
    /// anything else shows them as `?`.
    pub fn new(line: L, lines: Lines) -> Plain<L> {
        Plain { line, lines, started: false, pending: Vec::new(), overflow: false, after_cr: false, error: None }
    }

    pub fn line(&mut self) -> &mut L {
        &mut self.line
    }

    pub fn into_line(self) -> L {
        self.line
    }

    /// The error that ended a dialog with `Answer::Quit`, if one did.
    pub fn error(&self) -> Option<&io::Error> {
        self.error.as_ref()
    }

    fn out(&mut self, s: &str) -> io::Result<()> {
        self.line.write(s)
    }

    /// One `Clean` line as this terminal shows it.
    fn shown(&self, c: &Clean) -> String {
        let lines = if self.lines == Lines::Utf8 { Lines::Utf8 } else { Lines::Ascii };
        c.chars().map(|ch| text::shown(ch, lines)).collect()
    }

    /// Text, wrapped at WIDTH, a line each.
    fn say(&mut self, t: &Clean) -> io::Result<()> {
        if t.is_empty() {
            return Ok(());
        }
        let mut s = String::new();
        for l in text::wrap(t, WIDTH) {
            s.push_str(&self.shown(&l));
            s.push('\n');
        }
        self.out(&s)
    }

    /// The first line ever, then the dialog's title.
    fn header(&mut self, c: &Chrome) -> io::Result<()> {
        if !self.started {
            self.started = true;
            self.out("BackOtter rescue (plain mode)\n")?;
        }
        self.say(&c.banner)?;
        self.say(&c.title)
    }

    fn read_line(&mut self, timeout: Option<u64>) -> io::Result<Read> {
        loop {
            let b = match self.line.read(timeout)? {
                Byte::Got(b) => b,
                Byte::Timeout => return Ok(Read::Timeout),
                Byte::Signal => {
                    self.pending.clear();
                    self.overflow = false;
                    return Ok(Read::Eof);
                }
                Byte::Eof if self.pending.is_empty() && !self.overflow => return Ok(Read::Eof),
                // A last line with no newline.
                Byte::Eof => b'\n',
            };
            if std::mem::take(&mut self.after_cr) && b == b'\n' {
                continue;
            }
            if b == b'\n' || b == b'\r' {
                self.after_cr = b == b'\r';
                let raw = std::mem::take(&mut self.pending);
                if std::mem::take(&mut self.overflow) {
                    return Ok(Read::TooLong);
                }
                let s: String = String::from_utf8_lossy(&raw).chars().filter(|c| !c.is_control()).collect();
                return Ok(Read::Line(s));
            }
            if self.pending.len() < LINE_MAX {
                self.pending.push(b);
            } else {
                self.overflow = true;
            }
        }
    }

    /// The lettered choices on lines of their own, then `Choose [...]: `,
    /// until a choice comes.  An empty or wrong line asks again.
    fn choose(&mut self, numbers: usize, opts: &[Opt]) -> io::Result<Pick> {
        self.choose_flow(&[], numbers, opts, None)
    }

    /// `choose`, with NUMBERED choices before the lettered ones, in the
    /// same flowing lines (a disk's list: `1) WDC ...   2) ...   b)
    /// back`).  An empty line is EMPTY's number when there is one.
    fn choose_flow(&mut self, numbered: &[Clean], numbers: usize, opts: &[Opt], empty: Option<usize>) -> io::Result<Pick> {
        let mut items: Vec<String> = numbered.iter().enumerate().map(|(i, l)| format!(" {}) {}", i + 1, self.shown(l))).collect();
        items.extend(opts.iter().map(|o| format!(" {}) {}", o.key, self.shown(&o.label))));
        let mut line = String::from(" ");
        let mut block = String::new();
        for item in items {
            if line.len() > 1 && line.chars().count() + 2 + item.chars().count() > WIDTH {
                block.push_str(&line);
                block.push('\n');
                line = String::from(" ");
            }
            if line.len() > 1 {
                line.push_str("  ");
            }
            line.push_str(&item);
        }
        if line.len() > 1 {
            block.push_str(&line);
            block.push('\n');
        }
        self.out(&block)?;
        self.ask(numbers.max(numbered.len()), opts, empty)
    }

    /// `Choose [...]: ` until a choice comes.
    fn ask(&mut self, numbers: usize, opts: &[Opt], empty: Option<usize>) -> io::Result<Pick> {
        let mut keys: Vec<String> = Vec::new();
        match numbers {
            0 => {}
            1 => keys.push("1".into()),
            n => keys.push(format!("1-{n}")),
        }
        keys.extend(opts.iter().map(|o| o.key.to_string()));
        let prompt = format!("Choose [{}]: ", keys.join(", "));
        loop {
            self.out(&prompt)?;
            let got = match self.read_line(None)? {
                Read::Line(s) => s,
                Read::TooLong => String::new(),
                Read::Eof => return Ok(Pick::Quit),
                Read::Timeout => continue,
            };
            let a = got.trim().to_lowercase();
            if a.is_empty() {
                if let Some(i) = empty {
                    return Ok(Pick::Num(i));
                }
                continue;
            }
            if let Ok(n) = a.parse::<usize>() {
                if (1..=numbers).contains(&n) {
                    return Ok(Pick::Num(n - 1));
                }
            }
            let mut cs = a.chars();
            if let (Some(c), None) = (cs.next(), cs.next()) {
                if let Some(i) = opts.iter().position(|o| o.key == c) {
                    return Ok(Pick::Choice(i));
                }
            }
            self.out(&format!("Type one of {}.\n", keys.join(", ")))?;
        }
    }

    /// Text, then numbered rows among the buttons' letters in flowing
    /// lines; a blocked row says why and asks again.
    #[allow(clippy::too_many_arguments)]
    fn flowing(&mut self, chrome: &Chrome, text: &Clean, rows: &[Clean], notice: Option<&Clean>, buttons: &[Button], empty: Option<usize>, blocked: &[(usize, Clean)]) -> io::Result<Answer<usize>> {
        self.header(chrome)?;
        self.say(text)?;
        if let Some(n) = notice {
            self.say(n)?;
        }
        let others: Vec<Button> = buttons.iter().filter(|b| b.act != Act::Ok).cloned().collect();
        let opts = letters(&others, &[]);
        let mut pick = self.choose_flow(rows, rows.len(), &opts, empty)?;
        loop {
            return Ok(match pick {
                Pick::Num(i) => match blocked.iter().find(|(b, _)| *b == i) {
                    Some((_, why)) => {
                        self.say(why)?;
                        pick = self.ask(rows.len(), &opts, empty)?;
                        continue;
                    }
                    None => Answer::Ok(i),
                },
                Pick::Choice(i) => Plain::<L>::act(opts[i].act),
                Pick::Quit => Answer::Quit,
            });
        }
    }

    fn passphrase_inner(&mut self, d: &ui::Passphrase) -> io::Result<Answer<Vec<Secret>>> {
        self.header(&d.chrome)?;
        self.say(&d.text)?;
        self.say(&d.after)?;
        if let Some(n) = &d.notice {
            self.say(n)?;
        }
        let has_others = d.buttons.iter().any(|b| b.act != Act::Ok);
        'again: loop {
            let mut got = Vec::new();
            for l in &d.labels {
                self.line.quiet(true)?;
                let prompt = format!("{} ", self.shown(l));
                if let Err(e) = self.out(&prompt) {
                    let _ = self.line.quiet(false);
                    return Err(e);
                }
                let s = self.read_secret();
                let back = self.line.quiet(false);
                let s = s?;
                back?;
                self.out("\n")?;
                match s {
                    None => return Ok(Answer::Quit),
                    Some((_, true)) => {
                        self.out(&format!("That's longer than {} bytes: type it again.\n", crate::secret::CAP))?;
                        continue 'again;
                    }
                    Some((s, false)) if s.is_empty() && got.is_empty() && has_others => {
                        if let Some(a) = self.other_choices(&d.buttons)? {
                            return Ok(a);
                        }
                        continue 'again;
                    }
                    Some((s, false)) => got.push(s),
                }
            }
            return Ok(Answer::Ok(got));
        }
    }

    fn review_inner(&mut self, d: &ui::Review, typed: &mut Vec<String>) -> io::Result<Answer<()>> {
        self.header(&d.chrome)?;
        self.say(&d.plain)?;
        if let Some(n) = &d.notice {
            self.say(n)?;
        }
        typed.resize(d.rows.len(), String::new());
        for (i, row) in d.rows.iter().enumerate() {
            if ui::matches(&typed[i], &row.expect) {
                continue;
            }
            loop {
                let q = format!("{} ", self.shown(&row.ask));
                self.out(&q)?;
                let got = match self.read_line(None)? {
                    Read::Eof => return Ok(Answer::Quit),
                    Read::Timeout | Read::TooLong => continue,
                    Read::Line(s) => s,
                };
                if got.trim().eq_ignore_ascii_case("b") {
                    return Ok(Answer::Back);
                }
                if ui::matches(&got, &row.expect) {
                    typed[i] = got.trim().to_string();
                    self.say(&row.confirmed)?;
                    break;
                }
                self.say(&row.wrong)?;
            }
        }
        let opts = letters(&d.buttons, &[]);
        loop {
            return Ok(match self.choose(0, &opts)? {
                Pick::Choice(i) if opts[i].act == Act::Ok => match &d.blocked {
                    Some(why) => {
                        self.say(why)?;
                        continue;
                    }
                    None => Answer::Ok(()),
                },
                Pick::Choice(i) => Plain::<L>::act(opts[i].act),
                Pick::Num(_) | Pick::Quit => Answer::Quit,
            });
        }
    }

    fn cycle_inner(&mut self, d: &ui::Cycle) -> io::Result<Answer<Vec<usize>>> {
        self.header(&d.chrome)?;
        self.say(&d.text)?;
        let mut choice: Vec<usize> = d.rows.iter().map(|r| if r.start < r.choices.len() { r.start } else { 0 }).collect();
        let opts = letters(&d.buttons, &[]);
        loop {
            let mut s = String::new();
            for (i, r) in d.rows.iter().enumerate() {
                let state = match (r.choices.len(), choice[i]) {
                    (0 | 1, _) => &d.cant,
                    (_, 0) => &d.as_was,
                    _ => &d.changed,
                };
                let c = r.choices.get(choice[i]).cloned().unwrap_or_default();
                s.push_str(&format!("  {}) {}{}  {}\n", i + 1, self.shown(&r.label), self.shown(&c), self.shown(state)));
            }
            self.out(&s)?;
            self.say(&d.after)?;
            match self.choose(d.rows.len(), &opts)? {
                Pick::Num(i) => {
                    let r = &d.rows[i];
                    if r.choices.len() < 2 {
                        self.say(&r.note)?;
                        continue;
                    }
                    self.say(&r.note)?;
                    let back = [Opt { key: 'b', label: Clean::ours("back"), act: Act::Back }];
                    if let Pick::Num(j) = self.choose_flow(&r.choices, r.choices.len(), &back, None)? {
                        choice[i] = j;
                    }
                }
                Pick::Choice(i) if opts[i].act == Act::Ok => return Ok(Answer::Ok(choice)),
                Pick::Choice(i) => return Ok(Plain::<L>::act(opts[i].act)),
                Pick::Quit => return Ok(Answer::Quit),
            }
        }
    }

    fn act<T>(a: Act) -> Answer<T> {
        match a {
            Act::Back => Answer::Back,
            Act::Shell => Answer::Shell,
            Act::Other(n) => Answer::Button(n),
            // OK is each dialog's own to answer.
            Act::Ok => Answer::Quit,
        }
    }

    fn done<T>(&mut self, r: io::Result<Answer<T>>) -> Answer<T> {
        r.unwrap_or_else(|e| {
            self.error = Some(e);
            Answer::Quit
        })
    }

    /// A numbered list: rows already laid out.
    fn list(&mut self, chrome: &Chrome, text: &Clean, rows: &[Clean], after: &Clean, buttons: &[Button]) -> io::Result<Answer<usize>> {
        self.header(chrome)?;
        self.say(text)?;
        let mut s = String::new();
        for (i, r) in rows.iter().enumerate() {
            s.push_str(&format!("  {}) {}\n", i + 1, self.shown(r)));
        }
        self.out(&s)?;
        self.say(after)?;
        let others: Vec<Button> = buttons.iter().filter(|b| b.act != Act::Ok).cloned().collect();
        let opts = letters(&others, &[]);
        Ok(match self.choose(rows.len(), &opts)? {
            Pick::Num(i) => Answer::Ok(i),
            Pick::Choice(i) => Plain::<L>::act(opts[i].act),
            Pick::Quit => Answer::Quit,
        })
    }

    /// After an empty line where text is typed: the other choices, and
    /// `t` to type again.  None means type again.
    fn other_choices<T>(&mut self, buttons: &[Button]) -> io::Result<Option<Answer<T>>> {
        let others: Vec<Button> = buttons.iter().filter(|b| b.act != Act::Ok).cloned().collect();
        let mut opts = letters(&others, &['t']);
        opts.push(Opt { key: 't', label: Clean::ours("type it again"), act: Act::Ok });
        Ok(match self.choose(0, &opts)? {
            Pick::Choice(i) if opts[i].act == Act::Ok => None,
            Pick::Choice(i) => Some(Plain::<L>::act(opts[i].act)),
            Pick::Num(_) => None,
            Pick::Quit => Some(Answer::Quit),
        })
    }

    fn input_inner(&mut self, d: &ui::Input) -> io::Result<Answer<String>> {
        self.header(&d.chrome)?;
        self.say(&d.text)?;
        self.say(&d.after)?;
        if let Some(n) = &d.notice {
            self.say(n)?;
        }
        let max = d.max.min(ui::INPUT_MAX);
        let has_others = d.buttons.iter().any(|b| b.act != Act::Ok);
        loop {
            self.out("> ")?;
            match self.read_line(None)? {
                Read::Eof => return Ok(Answer::Quit),
                Read::Timeout => continue,
                Read::TooLong => self.out(&format!("That's longer than {max} characters: type it again.\n"))?,
                Read::Line(s) if s.is_empty() && has_others => {
                    if let Some(a) = self.other_choices(&d.buttons)? {
                        return Ok(a);
                    }
                }
                Read::Line(s) if s.chars().count() > max => {
                    self.out(&format!("That's longer than {max} characters: type it again.\n"))?
                }
                Read::Line(s) => return Ok(Answer::Ok(s)),
            }
        }
    }

    /// The password's bytes, with echo off; None for Ctrl-C, Ctrl-D on
    /// an empty line, or the end of input.
    fn read_secret(&mut self) -> io::Result<Option<(Secret, bool)>> {
        let mut s = Secret::new();
        let mut too_long = false;
        loop {
            let b = match self.line.read(None)? {
                Byte::Got(b) => b,
                Byte::Timeout => continue,
                Byte::Eof | Byte::Signal => return Ok(None),
            };
            if std::mem::take(&mut self.after_cr) && b == b'\n' {
                continue;
            }
            match b {
                b'\r' | b'\n' => {
                    self.after_cr = b == b'\r';
                    return Ok(Some((s, too_long)));
                }
                0x03 => return Ok(None),
                0x04 if s.is_empty() => return Ok(None),
                0x7f | 0x08 => {
                    s.pop_char();
                }
                0x15 => {
                    s.clear();
                    too_long = false;
                }
                b if b < 0x20 => {}
                b => {
                    if !s.push_byte(b) {
                        too_long = true;
                    }
                }
            }
        }
    }

    fn password_inner(&mut self, d: &ui::Password) -> io::Result<Answer<Secret>> {
        self.header(&d.chrome)?;
        self.say(&d.text)?;
        self.say(&d.after)?;
        let has_others = d.buttons.iter().any(|b| b.act != Act::Ok);
        loop {
            // Quiet before the prompt, so nothing typed after it echoes.
            self.line.quiet(true)?;
            if let Err(e) = self.out("> ") {
                let _ = self.line.quiet(false);
                return Err(e);
            }
            let got = self.read_secret();
            // Echo back on whatever happened, then the newline the
            // unechoed Enter didn't print.
            let back = self.line.quiet(false);
            let got = got?;
            back?;
            self.out("\n")?;
            match got {
                None => return Ok(Answer::Quit),
                Some((_, true)) => self.out(&format!("That's longer than {} bytes: type it again.\n", crate::secret::CAP))?,
                Some((s, false)) if s.is_empty() && has_others => {
                    if let Some(a) = self.other_choices(&d.buttons)? {
                        return Ok(a);
                    }
                }
                Some((s, false)) => {
                    if s.non_ascii() {
                        self.out("That has a character that isn't plain ASCII: the keyboard must match the password's.\n")?;
                    }
                    return Ok(Answer::Ok(s));
                }
            }
        }
    }

    /// Text and buttons: Enter alone when OK is the only one.
    fn buttons_only(&mut self, buttons: &[Button]) -> io::Result<Answer<()>> {
        if buttons.len() <= 1 {
            loop {
                self.out("Press Enter to go on: ")?;
                match self.read_line(None)? {
                    Read::Eof => return Ok(Answer::Quit),
                    Read::Timeout => continue,
                    _ => return Ok(if buttons.is_empty() || buttons[0].act == Act::Ok {
                        Answer::Ok(())
                    } else {
                        Plain::<L>::act(buttons[0].act)
                    }),
                }
            }
        }
        let opts = letters(buttons, &[]);
        Ok(match self.choose(0, &opts)? {
            Pick::Choice(i) if opts[i].act == Act::Ok => Answer::Ok(()),
            Pick::Choice(i) => Plain::<L>::act(opts[i].act),
            Pick::Num(_) | Pick::Quit => Answer::Quit,
        })
    }

    fn progress_inner(&mut self, d: &ui::Progress, tick: &mut dyn FnMut() -> Tick) -> io::Result<Answer<()>> {
        self.header(&d.chrome)?;
        self.say(&d.text)?;
        let opts = letters(&d.buttons, &[]);
        if !opts.is_empty() {
            let keys: Vec<String> = opts.iter().map(|o| format!("{} and Enter to {}", o.key, self.shown(&o.label))).collect();
            self.say(&Clean::line(&format!("Type {}.", keys.join(", or ")), 1024))?;
        }
        let mut text: Vec<Clean> = text::wrap(&d.text, WIDTH);
        let mut last_pct: Option<(u8, u64)> = None;
        let mut last_line: Option<Clean> = None;
        loop {
            let now = self.line.now_ms();
            let next_ms = match tick() {
                Tick::Done => return Ok(Answer::Ok(())),
                Tick::At { text: t, pct, line, next_ms } => {
                    if let Some(t) = t {
                        let new = text::wrap(&t, WIDTH);
                        for (i, l) in new.iter().enumerate() {
                            if text.get(i) != Some(l) {
                                let s = format!("{}\n", self.shown(l));
                                self.out(&s)?;
                            }
                        }
                        text = new;
                    }
                    let due = match (pct, last_pct) {
                        (_, None) => true,
                        (Some(p), Some((q, at))) => p / 10 > q / 10 || (p != q && now >= at + PCT_EVERY_MS) || (p == 100 && q != 100),
                        (None, Some((_, at))) => last_line.as_ref() != Some(&line) && now >= at + PCT_EVERY_MS,
                    };
                    if due && (pct.is_some() || !line.is_empty()) {
                        let s = match pct {
                            Some(p) if line.is_empty() => format!("    {p} %\n"),
                            Some(p) => format!("    {p} %: {}\n", self.shown(&line)),
                            None => format!("    {}\n", self.shown(&line)),
                        };
                        self.out(&s)?;
                        last_pct = Some((pct.unwrap_or(0), now));
                        last_line = Some(line);
                    }
                    next_ms.max(10)
                }
            };
            // Wait for the next tick, reading any command typed.
            let until = now + next_ms;
            loop {
                let now = self.line.now_ms();
                if now >= until {
                    break;
                }
                match self.read_line(Some(until - now))? {
                    Read::Timeout => {}
                    Read::Eof => return Ok(Answer::Quit),
                    Read::TooLong => {}
                    Read::Line(s) => {
                        let a = s.trim().to_lowercase();
                        if let Some(o) = opts.iter().find(|o| a.len() == 1 && a.starts_with(o.key)) {
                            return Ok(if o.act == Act::Ok { Answer::Ok(()) } else { Plain::<L>::act(o.act) });
                        }
                    }
                }
            }
        }
    }

    /// The recovery key on one line, read back numbered, then tried or
    /// typed again (rescue-screens.md, "Plain mode").  The words echo:
    /// they are read off paper, and shown in clear on the screen too.
    fn words_inner(&mut self, d: &ui::Words) -> io::Result<Answer<Secret>> {
        use crate::bip39::{self, Typed, COUNT};
        self.header(&d.chrome)?;
        self.say(if d.plain.is_empty() { &d.text } else { &d.plain })?;
        self.say(&d.after)?;
        let has_others = d.buttons.iter().any(|b| b.act != Act::Ok);
        loop {
            self.out("> ")?;
            let got = match self.read_secret()? {
                None => return Ok(Answer::Quit),
                Some((_, true)) => {
                    self.out(&format!("That's longer than {} bytes: type it again.\n", crate::secret::CAP))?;
                    continue;
                }
                Some((s, false)) => s,
            };
            if got.is_empty() {
                if has_others {
                    if let Some(a) = self.other_choices(&d.buttons)? {
                        return Ok(a);
                    }
                }
                continue;
            }
            // Split at white space and commas, lower case, as bko-holt
            // reads them; numbers before a word are left out.
            let mut full: Vec<&'static str> = Vec::new();
            let mut wrong: Option<String> = None;
            let typed: Vec<String> = got
                .expose()
                .split(|b| b.is_ascii_whitespace() || *b == b',')
                .filter(|w| !w.is_empty() && !w.iter().all(u8::is_ascii_digit))
                .map(|w| String::from_utf8_lossy(w).to_lowercase())
                .collect();
            drop(got);
            for (i, w) in typed.iter().enumerate() {
                let shown = self.shown(&Clean::line(w, 16));
                match bip39::typed(w) {
                    Typed::Whole(x) | Typed::Completes(x) => full.push(x),
                    Typed::Partial => {
                        let some: Vec<&str> = bip39::starting(w).iter().take(3).copied().collect();
                        wrong.get_or_insert(format!("Word {}, {shown}, is the start of more than one word: {}.", i + 1, some.join(", ")));
                    }
                    _ => {
                        wrong.get_or_insert(format!("Word {}, {shown}, isn't the start of any recovery word.", i + 1));
                    }
                }
            }
            for t in typed {
                let mut v = t.into_bytes();
                crate::secret::wipe(&mut v);
            }
            if let Some(w) = wrong {
                self.out(&format!("{w}\n"))?;
                continue;
            }
            if full.len() != COUNT {
                self.out(&format!("That's {} words; the key has {COUNT}.  Type them again.\n", full.len()))?;
                continue;
            }
            let mut s = String::from("You typed:\n");
            for row in full.chunks(4).enumerate() {
                let mut l = String::new();
                for (j, w) in row.1.iter().enumerate() {
                    l.push_str(&format!("{:>3} {:<9}", row.0 * 4 + j + 1, w));
                }
                s.push_str(l.trim_end());
                s.push('\n');
            }
            // Shown in clear by design, but not left in memory after.
            let shown = self.out(&s);
            let mut b = s.into_bytes();
            crate::secret::wipe(&mut b);
            shown?;
            let mut opts = vec![
                Opt { key: 'y', label: Clean::ours("these are right, try them"), act: Act::Ok },
                Opt { key: 'n', label: Clean::ours("type them again"), act: Act::Other(usize::MAX) },
            ];
            let others: Vec<Button> = d.buttons.iter().filter(|b| b.act != Act::Ok).cloned().collect();
            opts.extend(letters(&others, &['y', 'n']));
            match self.choose(0, &opts)? {
                Pick::Choice(0) => {
                    let mut out = Secret::new();
                    for (i, w) in full.iter().enumerate() {
                        if i > 0 {
                            out.push_byte(b' ');
                        }
                        for b in w.bytes() {
                            out.push_byte(b);
                        }
                    }
                    return Ok(Answer::Ok(out));
                }
                Pick::Choice(1) => continue,
                Pick::Choice(i) => return Ok(Plain::<L>::act(opts[i].act)),
                Pick::Num(_) | Pick::Quit => return Ok(Answer::Quit),
            }
        }
    }
}

impl<L: Line> Ui for Plain<L> {
    fn menu(&mut self, d: &ui::Menu) -> Answer<usize> {
        let w = d.items.iter().map(|(t, _)| t.width()).max().unwrap_or(0);
        let rows: Vec<Clean> = d
            .items
            .iter()
            .map(|(t, s)| if s.is_empty() { t.clone() } else { t.join(&" ".repeat(w - t.width() + 2), s) })
            .collect();
        if let Some(t) = &d.plain_text {
            let r = self.flowing(&d.chrome, t, &d.plain_items, d.notice.as_ref(), &d.buttons, None, &[]);
            return self.done(r);
        }
        let r = self.list(&d.chrome, &d.text, &rows, &d.after, &d.buttons);
        self.done(r)
    }

    fn radio(&mut self, d: &ui::Radio) -> Answer<usize> {
        if !d.plain_items.is_empty() {
            let text = if d.after.is_empty() { d.text.clone() } else { d.text.join("\n", &d.after) };
            let empty = d.chosen.filter(|_| d.empty_takes);
            let r = self.flowing(&d.chrome, &text, &d.plain_items, None, &d.buttons, empty, &d.blocked);
            return self.done(r);
        }
        loop {
            let r = self.list(&d.chrome, &d.text, &d.items, &d.after, &d.buttons);
            if let Ok(Answer::Ok(i)) = &r {
                if let Some((_, why)) = d.blocked.iter().find(|(b, _)| b == i) {
                    let line = format!("{}\n", self.shown(why));
                    if let Err(e) = self.out(&line) {
                        return self.done(Err(e));
                    }
                    continue;
                }
            }
            return self.done(r);
        }
    }

    fn input(&mut self, d: &ui::Input) -> Answer<String> {
        let r = self.input_inner(d);
        self.done(r)
    }

    fn password(&mut self, d: &ui::Password) -> Answer<Secret> {
        let r = self.password_inner(d);
        self.done(r)
    }

    fn yes_no(&mut self, d: &ui::Question) -> Answer<bool> {
        let r = (|| -> io::Result<Answer<bool>> {
            self.header(&d.chrome)?;
            self.say(&d.text)?;
            let mut opts = vec![
                Opt { key: 'y', label: lower_first(&d.yes), act: Act::Ok },
                Opt { key: 'n', label: lower_first(&d.no), act: Act::Other(usize::MAX) },
            ];
            opts.extend(letters(&d.buttons, &['y', 'n']));
            Ok(match self.choose(0, &opts)? {
                Pick::Choice(0) => Answer::Ok(true),
                Pick::Choice(1) => Answer::Ok(false),
                Pick::Choice(i) => Plain::<L>::act(opts[i].act),
                Pick::Num(_) | Pick::Quit => Answer::Quit,
            })
        })();
        self.done(r)
    }

    fn message(&mut self, d: &ui::Message) -> Answer<()> {
        let r = (|| -> io::Result<Answer<()>> {
            self.header(&d.chrome)?;
            self.say(&d.text)?;
            self.buttons_only(&d.buttons)
        })();
        self.done(r)
    }

    /// Every line, whole: plain mode never cuts.
    fn view(&mut self, d: &ui::TextView) -> Answer<()> {
        let r = (|| -> io::Result<Answer<()>> {
            self.header(&d.chrome)?;
            let mut s = String::new();
            for l in &d.lines {
                for part in l.lines() {
                    s.push_str(&self.shown(&part));
                    s.push('\n');
                }
            }
            self.out(&s)?;
            self.buttons_only(&d.buttons)
        })();
        self.done(r)
    }

    fn progress(&mut self, d: &ui::Progress, tick: &mut dyn FnMut() -> Tick) -> Answer<()> {
        let r = self.progress_inner(d, tick);
        self.done(r)
    }

    fn passphrase(&mut self, d: &ui::Passphrase) -> Answer<Vec<Secret>> {
        let r = self.passphrase_inner(d);
        self.done(r)
    }

    fn review(&mut self, d: &ui::Review, typed: &mut Vec<String>) -> Answer<()> {
        let r = self.review_inner(d, typed);
        self.done(r)
    }

    fn cycle(&mut self, d: &ui::Cycle) -> Answer<Vec<usize>> {
        let r = self.cycle_inner(d);
        self.done(r)
    }

    fn words(&mut self, d: &ui::Words) -> Answer<Secret> {
        let r = self.words_inner(d);
        self.done(r)
    }
}

#[cfg(test)]
pub(crate) mod tests {
    use super::*;
    use crate::ui::{Input, Menu, Message, Password, Progress, Question, Radio, TextView};
    use std::collections::VecDeque;

    pub enum In {
        Bytes(Vec<u8>),
        Pause(u64),
    }

    /// Typed bytes, pauses, and what was written.  Time moves only as
    /// reads wait.  With nothing left, the end of input.
    #[derive(Default)]
    pub struct FakeLine {
        pub input: VecDeque<u8>,
        pub pauses: VecDeque<(usize, u64)>,
        pub out: String,
        pub now: u64,
        pub quiet: Vec<bool>,
        pub is_quiet: bool,
        /// What was written while quiet was on.
        pub out_quiet: String,
        read: usize,
    }

    impl FakeLine {
        pub fn new(script: Vec<In>) -> FakeLine {
            let mut f = FakeLine::default();
            for i in script {
                match i {
                    In::Bytes(b) => f.input.extend(b),
                    In::Pause(ms) => f.pauses.push_back((f.input.len() + f.read, ms)),
                }
            }
            f
        }

        pub fn typed(s: &str) -> FakeLine {
            FakeLine::new(vec![In::Bytes(s.as_bytes().to_vec())])
        }
    }

    impl Line for FakeLine {
        fn write(&mut self, s: &str) -> io::Result<()> {
            self.out.push_str(s);
            if self.is_quiet {
                self.out_quiet.push_str(s);
            }
            Ok(())
        }
        fn read(&mut self, timeout_ms: Option<u64>) -> io::Result<Byte> {
            if let Some(&(at, ms)) = self.pauses.front() {
                if at == self.read {
                    match timeout_ms {
                        Some(t) if t < ms => {
                            self.now += t;
                            self.pauses[0].1 -= t;
                            return Ok(Byte::Timeout);
                        }
                        _ => {
                            self.now += ms;
                            self.pauses.pop_front();
                        }
                    }
                }
            }
            match self.input.pop_front() {
                Some(b) => {
                    self.read += 1;
                    Ok(Byte::Got(b))
                }
                None => match timeout_ms {
                    Some(t) if !self.pauses.is_empty() => {
                        self.now += t;
                        Ok(Byte::Timeout)
                    }
                    _ => Ok(Byte::Eof),
                },
            }
        }
        fn quiet(&mut self, on: bool) -> io::Result<()> {
            self.quiet.push(on);
            self.is_quiet = on;
            Ok(())
        }
        fn now_ms(&mut self) -> u64 {
            self.now
        }
    }

    fn o(s: &str) -> Clean {
        Clean::ours(s)
    }

    fn plain(s: &str) -> Plain<FakeLine> {
        Plain::new(FakeLine::typed(s), Lines::Utf8)
    }

    fn out(p: Plain<FakeLine>) -> String {
        let s = p.into_line().out;
        assert!(!s.contains('\x1b') && !s.contains('\r'), "no escape sequences, nothing rewritten: {s:?}");
        s
    }

    fn machines() -> Menu {
        let items = vec![
            (o("lovelace"), o("newest backup 2026-11-04, the stick's copy is 3 days old")),
            (o("babbage"), o("newest backup 2026-10-30")),
        ];
        let mut m = Menu::new(Chrome::ours("Machine", "Machine"), Clean::default(), items);
        m.buttons.push(ui::Button::shell());
        m
    }

    #[test]
    fn a_menu_as_rescue_tui_md_draws_it() {
        let mut p = plain("2\n");
        assert_eq!(p.menu(&machines()), Answer::Ok(1));
        assert_eq!(
            out(p),
            "BackOtter rescue (plain mode)\nMachine\n\
             \x20 1) lovelace  newest backup 2026-11-04, the stick's copy is 3 days old\n\
             \x20 2) babbage   newest backup 2026-10-30\n\
             \x20 b) back   s) shell\n\
             Choose [1-2, b, s]: "
        );
    }

    #[test]
    fn wrong_and_empty_answers_ask_again() {
        let mut p = plain("\n9\nx\n  B \n");
        assert_eq!(p.menu(&machines()), Answer::Back);
        let s = out(p);
        assert_eq!(s.matches("Choose [1-2, b, s]: ").count(), 4);
        assert_eq!(s.matches("Type one of 1-2, b, s.").count(), 2, "{s}");
        assert_eq!(plain("s\n").menu(&machines()), Answer::Shell);
        assert_eq!(plain("").menu(&machines()), Answer::Quit, "the end of input");
    }

    #[test]
    fn a_line_longer_than_4_kib_is_asked_again() {
        let mut p = plain(&format!("{}\n1\n", "1".repeat(LINE_MAX + 10)));
        assert_eq!(p.menu(&machines()), Answer::Ok(0));
        assert_eq!(out(p).matches("Choose").count(), 2);
    }

    #[test]
    fn a_radio_list_and_its_buttons() {
        let mut r = Radio::new(Chrome::ours("Machine", "Which machine?"), o("Pick one."), vec![o("a"), o("b")], None);
        r.buttons = vec![ui::Button::ok(), ui::Button::other("Type a location", None, 3), ui::Button::other("Keyboard", Some('k'), 4)];
        let mut p = plain("t\n");
        assert_eq!(p.radio(&r), Answer::Button(3));
        assert!(out(p).contains("  t) type a location   k) keyboard\nChoose [1-2, t, k]: "));
    }

    #[test]
    fn text_is_typed_and_an_empty_line_shows_the_choices() {
        let i = Input::new(Chrome::ours("Machine", "Where is the backup?"), o("Type the address."));
        let mut p = plain("sftp://nas.local/x\n");
        assert_eq!(p.input(&i), Answer::Ok("sftp://nas.local/x".to_string()));
        let mut p = plain("\nt\nb\n\nb\n");
        assert_eq!(p.input(&i), Answer::Ok("b".to_string()), "t types again, and b is then text");
        assert_eq!(plain("\nb\n").input(&i), Answer::Back);
        let mut short = i.clone();
        short.max = 3;
        let mut p = plain("abcd\nabc\n");
        assert_eq!(p.input(&short), Answer::Ok("abc".to_string()));
        assert!(out(p).contains("longer than 3 characters"));
        // CR LF is one line end; control characters are dropped.
        assert_eq!(plain("a\x07b\r\n").input(&i), Answer::Ok("ab".to_string()));
    }

    #[test]
    fn a_password_is_read_quietly_byte_by_byte() {
        let pw = Password::new(Chrome::ours("Repository", "The backup password"), o("Type the backup password, or press Enter alone for other choices:"));
        let mut p = plain("hunterX\x7f2\r\n");
        match p.password(&pw) {
            Answer::Ok(s) => assert_eq!(s.expose(), b"hunter2"),
            _ => panic!("no secret"),
        }
        let f = p.into_line();
        assert_eq!(f.quiet, [true, false]);
        assert!(!f.out.contains("hunter"));
        assert_eq!(f.out_quiet, "> ", "only the prompt while quiet");
        assert!(f.out.ends_with("> \n"));
        // Ctrl-U empties it; non-ASCII is said; Ctrl-C quits with echo back.
        let mut p = plain("abc\x15d\u{e9}\n");
        match p.password(&pw) {
            Answer::Ok(s) => assert_eq!(s.expose(), "dé".as_bytes()),
            _ => panic!("no secret"),
        }
        assert!(p.into_line().out.contains("isn't plain ASCII"));
        let mut p = plain("abc\x03");
        assert!(matches!(p.password(&pw), Answer::Quit));
        assert_eq!(p.into_line().quiet, [true, false]);
        // An empty line: the other choices, b goes back.
        let mut p = plain("\nb\n");
        assert!(matches!(p.password(&pw), Answer::Back));
        assert!(p.into_line().out.contains("  b) back   t) type it again\nChoose [b, t]: "));
        // Too long: asked again.
        let mut p = plain(&format!("{}\nok\n", "x".repeat(crate::secret::CAP + 1)));
        match p.password(&pw) {
            Answer::Ok(s) => assert_eq!(s.expose(), b"ok"),
            _ => panic!("no secret"),
        }
    }

    #[test]
    fn yes_no_message_and_view() {
        let q = Question::new(Chrome::ours("a", "Restore now?"), o("Sure?"));
        let mut p = plain("n\n");
        assert_eq!(p.yes_no(&q), Answer::Ok(false));
        let s = out(p);
        assert!(s.ends_with("  y) yes   n) no\nChoose [y, n]: "), "{s:?}");
        let m = Message::new(Chrome::ours("a", "Done"), o("All back."));
        let mut p = plain("\n");
        assert_eq!(p.message(&m), Answer::Ok(()));
        assert!(out(p).ends_with("Done\nAll back.\nPress Enter to go on: "));
        let long = "x".repeat(300);
        let v = TextView::new(Chrome::ours("Review", "Every command"), vec![o(&long), o("two")]);
        let mut p = plain("\n");
        assert_eq!(p.view(&v), Answer::Ok(()));
        assert!(out(p).contains(&format!("{long}\ntwo\n")), "never cut in plain mode");
    }

    #[test]
    fn hostile_strings_and_ascii_locales() {
        let m = Message::new(Chrome::ours("a", "t"), Clean::text("evil\x1b[2J\u{9b}31m and Łódź", 100));
        let mut p = Plain::new(FakeLine::typed("\n"), Lines::Ascii);
        p.message(&m);
        let s = out(p);
        assert!(s.contains("evil?[2J?31m and ??d?"), "{s}");
    }

    /// Random bytes at every question: never a panic, never a control
    /// byte or escape out, every answer one the dialog has.
    #[test]
    fn random_input_never_gets_out() {
        use crate::testkit::{fuzz_cases, Rng};
        let mut rng = Rng::new(0x9e37);
        let bytes: &[u8] = b"0123456789bsyntoq \n\r\x1b\x7f\x03\x15\x04\x9b\xc3\xa9[";
        for _ in 0..fuzz_cases(100_000) / 50 {
            let n = rng.below(40) as usize;
            let input: Vec<u8> = (0..n).map(|_| *rng.pick(bytes)).collect();
            let mut p = Plain::new(FakeLine::new(vec![In::Bytes(input)]), Lines::Utf8);
            match rng.below(4) {
                0 => {
                    if let Answer::Ok(i) = p.menu(&machines()) {
                        assert!(i < 2);
                    }
                }
                1 => {
                    let _ = p.password(&Password::new(Chrome::ours("a", "b"), o("x")));
                }
                2 => {
                    if let Answer::Ok(s) = p.input(&Input::new(Chrome::ours("a", "b"), o("x"))) {
                        assert!(!s.chars().any(char::is_control), "{s:?}");
                    }
                }
                _ => {
                    let _ = p.yes_no(&Question::new(Chrome::ours("a", "b"), o("x")));
                }
            }
            let f = p.into_line();
            assert!(f.out.bytes().all(|b| b == b'\n' || (0x20..0x7f).contains(&b) || b >= 0x80), "{:?}", f.out);
            assert!(!f.out.contains('\u{9b}'));
            assert_eq!(f.quiet.iter().filter(|&&q| q).count(), f.quiet.iter().filter(|&&q| !q).count(), "echo always back on");
        }
    }

    /// Bytes, then a caught signal for every read after them.
    struct SigLine(VecDeque<u8>);

    impl Line for SigLine {
        fn write(&mut self, _: &str) -> io::Result<()> {
            Ok(())
        }
        fn read(&mut self, _: Option<u64>) -> io::Result<Byte> {
            Ok(self.0.pop_front().map_or(Byte::Signal, Byte::Got))
        }
        fn quiet(&mut self, _: bool) -> io::Result<()> {
            Ok(())
        }
        fn now_ms(&mut self) -> u64 {
            0
        }
    }

    #[test]
    fn a_signal_quits_and_drops_a_half_typed_line() {
        let mut p = Plain::new(SigLine(b"2".iter().copied().collect()), Lines::Utf8);
        assert_eq!(p.menu(&machines()), Answer::Quit, "the 2 isn't taken as a line");
        let mut p = Plain::new(SigLine(b"abc".iter().copied().collect()), Lines::Utf8);
        assert!(matches!(p.password(&Password::new(Chrome::ours("a", "b"), o("x"))), Answer::Quit));
        let mut d = Progress::new(Chrome::ours("a", "b"), o("x"));
        d.buttons = vec![ui::Button::other("Cancel", Some('c'), 1)];
        let mut forever = || Tick::At { text: None, pct: None, line: o("running"), next_ms: 1000 };
        let mut p = Plain::new(SigLine(b"c".iter().copied().collect()), Lines::Utf8);
        assert_eq!(p.progress(&d, &mut forever), Answer::Quit);
    }

    #[test]
    fn progress_is_a_line_per_stage_and_10_percent() {
        let mut d = Progress::new(Chrome::ours("Restore", "Restore"), o("[>] Make partitions\n[ ] Copy your files back"));
        d.buttons = vec![ui::Button::other("Cancel", Some('c'), 1)];
        let mut n = 0u32;
        let mut tick = || {
            n += 1;
            match n {
                1..=40 => Tick::At {
                    text: (n == 3).then(|| o("[x] Make partitions\n[>] Copy your files back")),
                    pct: Some((n * 25 / 10).min(100) as u8),
                    line: o("of 61.2 GB"),
                    next_ms: 1000,
                },
                _ => Tick::Done,
            }
        };
        let mut p = Plain::new(FakeLine::new(vec![In::Pause(1_000_000)]), Lines::Utf8);
        assert_eq!(p.progress(&d, &mut tick), Answer::Ok(()));
        let s = out(p);
        assert!(s.contains("Type c and Enter to cancel.\n"));
        assert!(s.contains("[x] Make partitions\n[>] Copy your files back\n"), "{s}");
        assert_eq!(s.matches("[ ] Copy").count(), 1, "unchanged lines aren't printed again");
        let pct: Vec<&str> = s.lines().filter(|l| l.contains(" %")).collect();
        assert_eq!(pct.len(), 11, "0 %, then each 10 %: {pct:?}");
        assert!(pct[10].contains("100 %: of 61.2 GB"));
        // A slow one: a line every 60 s even within 10 %.
        let mut n = 0u32;
        let mut slow = || {
            n += 1;
            if n > 200 {
                Tick::Done
            } else {
                Tick::At { text: None, pct: Some((n / 20) as u8), line: Clean::default(), next_ms: 1000 }
            }
        };
        let mut p = Plain::new(FakeLine::new(vec![In::Pause(10_000_000)]), Lines::Utf8);
        p.progress(&Progress::new(Chrome::ours("a", "b"), o("x")), &mut slow);
        let s = out(p);
        assert_eq!(s.lines().filter(|l| l.ends_with(" %")).count(), 5, "at 0, 60, 120 and 180 s, and at 10 %: {s}");
        // A command typed while it runs.
        let mut forever = || Tick::At { text: None, pct: None, line: o("running"), next_ms: 1000 };
        let mut p = Plain::new(FakeLine::new(vec![In::Pause(3500), In::Bytes(b"c\n".to_vec())]), Lines::Utf8);
        assert_eq!(p.progress(&d, &mut forever), Answer::Button(1));
    }
}
