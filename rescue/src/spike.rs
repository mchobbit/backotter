//! `bko-rescue --spike`: four cards drawn on RT2's first cut of the layer
//! (real raw mode and FFI, output not diffed yet), for RT3 to run on every
//! console and see what it does:
//!
//! 1. a dialog box with a title, a shadow and two buttons, one focused;
//! 2. a list of five items in a box three rows high, with scroll markers;
//! 3. a gauge from 0 to 100% over 10 s, twice a second, with the bytes
//!    each update took;
//! 4. a password field (`*` per key, Backspace), then the count typed.
//!
//! Under every card: the last 16 keys decoded and the size seen.  Keys
//! change the line style, colour and each sequence's fallback, so one run
//! shows a console in every profile.  `--spike panic` panics on purpose
//! after the first frame, to see the terminal come back.

use std::collections::VecDeque;
use std::io::{self, Write as _};
use std::time::{Duration, Instant};

use crate::term::ffi;
use crate::term::keys::{Decoder, Key};
use crate::term::out::{Attr, Caps, Colour, Glyph, Lines, Out};
use crate::term::profile::Profile;
use crate::term::tty::{self, Got, Input, RawMode, Size};

/// How long a lone ESC waits for more before it is Esc.  RT3 measures it
/// on serial lines and ssh.
pub const ESC_TIMEOUT: Duration = Duration::from_millis(100);
/// The poll timeout, when the size is read again.
const TICK_MS: u64 = 250;
const GAUGE_STEP: Duration = Duration::from_millis(500);
const GAUGE_STEPS: u32 = 20;
const BAR_COL: u16 = 16;
const BAR_LEN: u16 = 40;
const PCT_COL: u16 = 58;
const KEYS_KEPT: usize = 16;
const PW_MAX: usize = 1024;

const BG: Attr = Attr::colours(Colour::White, Colour::Blue);
const DLG: Attr = Attr::colours(Colour::Black, Colour::White);
const SHADOW: Attr = Attr { fg: None, bg: Some(Colour::Black), bold: false, reverse: false };

/// The box every card draws: rows 5-15, columns 11-70 of the canvas.
const BOX_TOP: u16 = 5;
const BOX_LEFT: u16 = 11;
const BOX_H: u16 = 11;
const BOX_W: u16 = 60;

const CARDS: [&str; 4] = ["a dialog", "a list", "a gauge", "a password"];
const ITEMS: [&str; 5] = [
    "item 1   the first of five",
    "item 2   the second",
    "item 3   the third",
    "item 4   the fourth",
    "item 5   the last",
];

/// Where the 80x24 canvas sits on the terminal: centred on a bigger one.
#[derive(Clone, Copy)]
struct Place {
    top: u16,
    left: u16,
    rows: u16,
    cols: u16,
}

impl Place {
    fn new(s: Size) -> Option<Place> {
        (s.rows >= 24 && s.cols >= 80).then(|| Place {
            top: (s.rows - 24) / 2,
            left: (s.cols - 80) / 2,
            rows: s.rows,
            cols: s.cols,
        })
    }

    /// How many of `n` cells from canvas (`row`, `col`) may be written:
    /// the terminal's last cell never is, so no console scrolls.
    fn fit(&self, row: u16, col: u16, n: u16) -> u16 {
        if n > 0 && self.top + row == self.rows && self.left + col + n - 1 >= self.cols {
            n.saturating_sub(1)
        } else {
            n
        }
    }
}

/// Which of `n` items show in `h` rows with `sel` selected, `top` the
/// first shown before: (first, how many, "^ more above", "v more below").
/// The markers take a row each, only when there is more that way.
pub fn list_window(n: usize, h: usize, sel: usize, top: usize) -> (usize, usize, bool, bool) {
    if n <= h {
        return (0, n, false, false);
    }
    let h = h.max(3);
    let mut top = top.min(sel);
    loop {
        let above = top > 0;
        let mut avail = h - usize::from(above);
        let below = top + avail < n;
        if below {
            avail -= 1;
        }
        if sel >= top + avail {
            top += 1;
            continue;
        }
        return (top, avail, above, below);
    }
}

/// One gauge update, from `from` steps to `to` (of GAUGE_STEPS): the
/// figures first, then only the bar's new cells, then the attributes back.
pub fn gauge_update(o: &mut Out, p_top: u16, p_left: u16, row: u16, from: u32, to: u32) {
    let cells = |s: u32| (u32::from(BAR_LEN) * s / GAUGE_STEPS) as u16;
    o.move_to(p_top + row, p_left + PCT_COL);
    o.attr(DLG);
    o.text(&format!("{:3}", to * 100 / GAUGE_STEPS));
    let (a, b) = (cells(from), cells(to));
    if b > a {
        o.move_to(p_top + row, p_left + BAR_COL + a);
        o.attr(DLG.reverse());
        o.spaces(usize::from(b - a));
        o.attr(DLG);
    }
}

#[derive(PartialEq, Eq)]
enum Next {
    Go,
    Quit,
}

struct Spike {
    o: Out,
    size: Size,
    card: usize,
    keys: VecDeque<String>,
    note: String,
    button: usize,
    list_sel: usize,
    list_top: usize,
    gauge_start: Instant,
    gauge_shown: u32,
    gauge_last: usize,
    gauge_most: usize,
    /// Only a count: the spike keeps no typed character.
    pw_count: usize,
    pw_focus: bool,
    /// The field was left by Esc or Tab in this read: the read's other
    /// characters are taken as typed too late (Alt+key, or an ESC in a
    /// fast passphrase), shown as `*` and never run as commands.
    left_field: bool,
    full: bool,
    card_dirty: bool,
}

impl Spike {
    fn new(p: &Profile) -> Spike {
        Spike {
            o: Out::new(p.lines, p.colour, p.caps),
            size: tty::size(),
            card: 0,
            keys: VecDeque::new(),
            note: String::new(),
            button: 0,
            list_sel: 0,
            list_top: 0,
            gauge_start: Instant::now(),
            gauge_shown: 0,
            gauge_last: 0,
            gauge_most: 0,
            pw_count: 0,
            pw_focus: true,
            left_field: false,
            full: true,
            card_dirty: false,
        }
    }

    fn place(&self) -> Option<Place> {
        Place::new(self.size)
    }

    /// Write `n` cells of `text` (padded with spaces) at canvas (`row`,
    /// `col`) in `a`.
    fn put(&mut self, p: Place, row: u16, col: u16, a: Attr, text: &str, n: u16) {
        let n = p.fit(row, col, n);
        self.o.move_to(p.top + row, p.left + col);
        self.o.attr(a);
        let t: String = text.chars().take(usize::from(n)).collect();
        let len = t.chars().count();
        self.o.text(&t);
        self.o.spaces(usize::from(n) - len);
    }

    fn reprofile(&mut self, lines: Lines, colour: bool, caps: Caps, raw: &RawMode) {
        self.o.end();
        let mut o = Out::new(lines, colour, caps);
        o.buf = self.o.take();
        self.o = o;
        raw.set_reset(lines, caps);
        self.full = true;
    }

    /// One key.  Letters are commands except in the password field.
    fn key(&mut self, k: Key, raw: &RawMode) -> Next {
        let in_field = self.card == 3 && self.pw_focus;
        let typed = matches!(k, Key::Char(_) | Key::Space);
        let secret = (in_field || self.left_field) && typed;
        self.keys.push_back(if secret { "*".to_string() } else { k.name() });
        while self.keys.len() > KEYS_KEPT {
            self.keys.pop_front();
        }
        self.card_dirty = true;
        if secret && !in_field {
            // Swallowed: neither typed nor a command.
            return Next::Go;
        }
        if in_field {
            match k {
                Key::Char(_) | Key::Space => self.pw_count = (self.pw_count + 1).min(PW_MAX),
                Key::Backspace => self.pw_count = self.pw_count.saturating_sub(1),
                Key::Enter => {
                    self.note = format!("count typed: {}", self.pw_count);
                    self.pw_count = 0;
                }
                Key::Esc | Key::Tab => {
                    self.pw_focus = false;
                    self.left_field = true;
                }
                _ => {}
            }
            return Next::Go;
        }
        if let Key::Char(c) = k {
            let (lines, colour, caps) = (self.o.lines, self.o.colour, self.o.caps);
            match c.to_ascii_lowercase() {
                'q' => return Next::Quit,
                c @ '1'..='4' => self.show(c as usize - '1' as usize),
                'n' => self.show((self.card + 1) % 4),
                'l' => self.reprofile(lines.next(), colour, caps, raw),
                'c' => self.reprofile(lines, !colour, caps, raw),
                'j' => self.reprofile(lines, colour, Caps { joined_sgr: !caps.joined_sgr, ..caps }, raw),
                'h' => {
                    // Show the cursor before giving up hiding it.
                    self.o.show_cursor();
                    self.reprofile(lines, colour, Caps { hide_cursor: !caps.hide_cursor, ..caps }, raw)
                }
                'e' => self.reprofile(lines, colour, Caps { clear: !caps.clear, ..caps }, raw),
                _ => {}
            }
            return Next::Go;
        }
        match (self.card, k) {
            (0, Key::Tab | Key::Left | Key::Right) => self.button = 1 - self.button,
            (0, Key::Enter) => {
                self.note = format!("pressed {}", if self.button == 0 { "OK" } else { "Back" })
            }
            (0, Key::Esc) => self.note = "Esc: Back".to_string(),
            (1, Key::Up) => self.list_sel = self.list_sel.saturating_sub(1),
            (1, Key::Down) => self.list_sel = (self.list_sel + 1).min(ITEMS.len() - 1),
            (1, Key::Enter) => self.note = format!("chose item {}", self.list_sel + 1),
            (2, Key::Space) => self.show(2),
            (3, Key::Enter | Key::Tab) => self.pw_focus = true,
            _ => {}
        }
        Next::Go
    }

    fn show(&mut self, card: usize) {
        self.card = card;
        self.note.clear();
        if card == 2 {
            self.gauge_start = Instant::now();
            self.gauge_shown = 0;
            self.gauge_last = 0;
            self.gauge_most = 0;
        }
        if card == 3 {
            self.pw_focus = true;
        }
        self.full = true;
    }

    fn frame(&mut self) {
        let Some(p) = self.place() else {
            self.o.hide_cursor();
            if !self.o.clear() {
                self.blank();
            }
            let s = self.size;
            self.o.move_to(1, 1);
            self.o.attr(Attr::PLAIN);
            self.o.text(&format!("bko-rescue --spike needs 80x24; this terminal is {}x{}.", s.cols, s.rows));
            self.o.move_to(2, 1);
            self.o.text("Make it bigger, or press q to quit.");
            self.full = false;
            self.card_dirty = false;
            return;
        };
        if self.full {
            self.o.hide_cursor();
            if !self.o.clear() {
                self.blank();
            }
            for row in 1..=24 {
                self.put(p, row, 1, BG, "", 80);
            }
            let right = format!("spike: card {} of 4, {}", self.card + 1, CARDS[self.card]);
            self.put(p, 1, 1, BG, " BackOtter rescue", 17);
            self.put(p, 1, 81 - right.len() as u16, BG, &right, right.len() as u16);
            let c = self.o.caps;
            let prof = format!(
                " lines {} | colour {} | sgr {} | cursor {} | clear {}",
                self.o.lines.name(),
                if self.o.colour { "on" } else { "off" },
                if c.joined_sgr { "joined" } else { "one-each" },
                if c.hide_cursor { "hide" } else { "park" },
                if c.clear { "2J" } else { "spaces" },
            );
            self.put(p, 2, 1, BG, &prof, 80);
        }
        if self.full || self.card_dirty {
            // The card last, so the dialog's attributes are left set for
            // the gauge's small updates.
            self.draw_info(p);
            self.draw_card(p);
        }
        self.full = false;
        self.card_dirty = false;
        self.park(p);
    }

    /// Spaces over the whole terminal, where `ESC [ 2 J` isn't used.
    fn blank(&mut self) {
        let s = self.size;
        self.o.attr(Attr::PLAIN);
        for row in 1..=s.rows {
            self.o.move_to(row, 1);
            let n = if row == s.rows { s.cols - 1 } else { s.cols };
            self.o.spaces(usize::from(n));
        }
    }

    fn draw_box(&mut self, p: Place, top: u16, left: u16, h: u16, w: u16, title: &str, shadow: bool) {
        let o = &mut self.o;
        o.move_to(p.top + top, p.left + left);
        o.attr(DLG);
        o.glyph(Glyph::TopLeft);
        o.glyphs(Glyph::Horiz, usize::from(w - 2));
        o.glyph(Glyph::TopRight);
        for r in 1..h - 1 {
            o.move_to(p.top + top + r, p.left + left);
            o.glyph(Glyph::Vert);
            o.spaces(usize::from(w - 2));
            o.glyph(Glyph::Vert);
        }
        o.move_to(p.top + top + h - 1, p.left + left);
        o.glyph(Glyph::BottomLeft);
        o.glyphs(Glyph::Horiz, usize::from(w - 2));
        o.glyph(Glyph::BottomRight);
        if !title.is_empty() {
            let t = format!(" {title} ");
            o.move_to(p.top + top, p.left + left + (w - t.len() as u16) / 2);
            o.text(&t);
        }
        if shadow && o.colour {
            for r in 1..=h {
                o.move_to(p.top + top + r, p.left + left + w);
                o.attr(SHADOW);
                o.spaces(1);
            }
            o.move_to(p.top + top + h, p.left + left + 1);
            o.spaces(usize::from(w - 1));
        }
    }

    fn body(&mut self, p: Place, row: u16, text: &str) {
        self.put(p, row, BOX_LEFT + 2, DLG, text, BOX_W - 4);
    }

    fn draw_card(&mut self, p: Place) {
        self.draw_box(p, BOX_TOP, BOX_LEFT, BOX_H, BOX_W, CARDS[self.card], true);
        match self.card {
            0 => {
                self.body(p, 7, "A dialog box with a title, a shadow and two buttons.");
                self.body(p, 8, "Tab, Left and Right move the focus; Enter presses.");
                self.body(p, 9, "Esc is Back.");
                let col = BOX_LEFT + 1 + (BOX_W - 2 - 19) / 2;
                let (ok, back) = if self.button == 0 { (DLG.focus(), DLG) } else { (DLG, DLG.focus()) };
                self.put(p, 13, col, ok, "<  OK  >", 8);
                self.put(p, 13, col + 8, DLG, "", 3);
                self.put(p, 13, col + 11, back, "< Back >", 8);
            }
            1 => {
                self.body(p, 7, "Five items in a list three rows high: Up and Down.");
                self.draw_box(p, 8, 16, 5, 50, "", false);
                let (first, count, above, below) = list_window(ITEMS.len(), 3, self.list_sel, self.list_top);
                self.list_top = first;
                let mut row = 9;
                if above {
                    self.put(p, row, 17, DLG, "^ more above", 48);
                    row += 1;
                }
                for (i, item) in ITEMS.iter().enumerate().skip(first).take(count) {
                    let a = if i == self.list_sel { DLG.focus() } else { DLG };
                    self.put(p, row, 17, a, item, 48);
                    row += 1;
                }
                if below {
                    self.put(p, row, 17, DLG, "v more below", 48);
                }
            }
            2 => {
                self.body(p, 7, "0 to 100% over 10 s, twice a second. Space starts again.");
                self.body(p, 9, "Copying files back");
                let shown = self.gauge_shown;
                self.put(p, 10, BAR_COL, DLG, "", BAR_LEN);
                self.put(p, 10, PCT_COL + 3, DLG, "%", 1);
                gauge_update(&mut self.o, p.top, p.left, 10, 0, shown);
                self.gauge_line(p);
            }
            _ => {
                self.body(p, 7, "Type: one * a key, Backspace removes one.");
                self.body(p, 8, "Enter shows the count; Esc or Tab leaves the field.");
                self.put(p, 10, 14, DLG, "Password:", 9);
                let stars = "*".repeat(self.pw_count.min(39));
                if self.pw_focus {
                    self.put(p, 10, 24, DLG.focus(), &stars, 40);
                } else {
                    let s = format!("{stars}{}", "_".repeat(40 - stars.len()));
                    self.put(p, 10, 24, DLG, &s, 40);
                }
            }
        }
        if self.card != 2 {
            let note = self.note.clone();
            self.body(p, 14, &note);
        }
    }

    fn gauge_line(&mut self, p: Place) {
        let s = format!("last update {} bytes, the most {} bytes", self.gauge_last, self.gauge_most);
        self.body(p, 12, &s);
    }

    fn draw_info(&mut self, p: Place) {
        let keys: Vec<String> = self.keys.iter().cloned().collect();
        let mut k = format!(" keys: {}", keys.join(" "));
        while k.chars().count() > 79 {
            k.remove(7);
        }
        let s = self.size;
        let size = if s.assumed {
            " size 0x0 (or unknown), taken as 80x24".to_string()
        } else {
            format!(" size {}x{}", s.cols, s.rows)
        };
        let keyline = if self.card == 3 && self.pw_focus {
            " typing: any key | Backspace | Enter: the count | Esc or Tab: leave the field"
        } else {
            " 1-4 n: card | l: lines | c: colour | j: sgr | h: cursor | e: clear | q: quit"
        };
        self.put(p, 19, 1, BG, &k, 80);
        self.put(p, 20, 1, BG, &size, 80);
        self.put(p, 24, 1, BG, keyline, 80);
    }

    /// The cursor at the focus, which is where it shows if it can't hide.
    fn park(&mut self, p: Place) {
        let (row, col) = match self.card {
            0 => (13, BOX_LEFT + 1 + (BOX_W - 2 - 19) / 2 + 2 + 11 * self.button as u16),
            1 => (9, 17),
            2 => (10, BAR_COL),
            _ => (10, 24 + self.pw_count.min(39) as u16),
        };
        self.o.move_to(p.top + row, p.left + col);
        self.o.end();
    }

    /// The gauge's due step, drawn with only what changed.
    fn gauge_tick(&mut self) {
        if self.card != 2 || self.gauge_shown >= GAUGE_STEPS {
            return;
        }
        let due = ((self.gauge_start.elapsed().as_millis() / GAUGE_STEP.as_millis()) as u32).min(GAUGE_STEPS);
        if due <= self.gauge_shown {
            return;
        }
        let Some(p) = self.place() else { return };
        let before = self.o.buf.len();
        gauge_update(&mut self.o, p.top, p.left, 10, self.gauge_shown, due);
        self.gauge_last = self.o.buf.len() - before;
        self.gauge_most = self.gauge_most.max(self.gauge_last);
        self.gauge_shown = due;
        self.gauge_line(p);
        self.park(p);
    }

    /// How long to wait in poll: the tick, the Esc timeout, the gauge.
    fn timeout(&self, esc_since: Option<Instant>) -> Duration {
        let mut t = Duration::from_millis(TICK_MS);
        if let Some(e) = esc_since {
            t = t.min(ESC_TIMEOUT.saturating_sub(e.elapsed()));
        }
        if self.card == 2 && self.gauge_shown < GAUGE_STEPS {
            let next = self.gauge_start + GAUGE_STEP * (self.gauge_shown + 1);
            t = t.min(next.saturating_duration_since(Instant::now()));
        }
        t
    }

    fn flush(&mut self) -> io::Result<()> {
        let bytes = self.o.take();
        if bytes.is_empty() {
            return Ok(());
        }
        let mut out = io::stdout().lock();
        out.write_all(&bytes)?;
        out.flush()
    }
}

/// Run the spike on fd 0 and 1.  Returns the exit status.
pub fn run(p: &Profile, panic_on_purpose: bool) -> i32 {
    match run_inner(p, panic_on_purpose) {
        Ok(code) => code,
        Err(e) => {
            tty::restore();
            eprintln!("bko-rescue --spike: {e}");
            1
        }
    }
}

fn run_inner(p: &Profile, panic_on_purpose: bool) -> io::Result<i32> {
    // The handlers first: they only set a flag, and a signal between raw
    // mode and them would kill us with the terminal raw.
    if !ffi::catch_hup_term() {
        return Err(io::Error::other("can't catch SIGHUP and SIGTERM"));
    }
    let raw = RawMode::enter(p.lines, p.caps)?;
    let mut s = Spike::new(p);
    s.frame();
    s.flush()?;
    if panic_on_purpose {
        std::thread::sleep(Duration::from_secs(1));
        panic!("bko-rescue --spike panic: a panic on purpose, to see the terminal come back");
    }
    let mut input = Input::new();
    let mut dec = Decoder::new();
    let mut buf = [0u8; 256];
    let mut keys = Vec::new();
    let mut esc_since: Option<Instant> = None;
    let mut code = 0;
    'run: loop {
        if let Some(sig) = ffi::take_signal() {
            code = 128 + sig;
            break;
        }
        let t = s.timeout(esc_since).as_millis() as i32;
        match input.read(&mut buf, t)? {
            Got::Bytes(n) => {
                for &b in &buf[..n] {
                    dec.feed(b, &mut keys);
                }
                esc_since = dec.pending().then(Instant::now);
            }
            Got::Timeout => {
                if esc_since.is_some_and(|e| e.elapsed() >= ESC_TIMEOUT) {
                    dec.timeout(&mut keys);
                    esc_since = None;
                }
                let now = tty::size();
                if now != s.size {
                    s.size = now;
                    s.full = true;
                }
            }
            Got::Interrupted => continue,
            Got::Eof => break,
        }
        for k in keys.drain(..) {
            if s.key(k, &raw) == Next::Quit {
                break 'run;
            }
        }
        // A new read is a new batch of keys.
        s.left_field = false;
        if s.full || s.card_dirty {
            s.frame();
        }
        s.gauge_tick();
        s.flush()?;
    }
    s.o.show_cursor();
    s.o.attr(Attr::PLAIN);
    if !s.o.clear() {
        s.blank();
    }
    s.o.end();
    s.flush()?;
    drop(raw);
    println!("bko-rescue --spike: done");
    Ok(code)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn list_window_scrolls_with_markers() {
        // Five items in three rows: a marker takes a row when there's more.
        assert_eq!(list_window(5, 3, 0, 0), (0, 2, false, true));
        assert_eq!(list_window(5, 3, 1, 0), (0, 2, false, true));
        assert_eq!(list_window(5, 3, 2, 0), (2, 1, true, true));
        assert_eq!(list_window(5, 3, 3, 2), (3, 2, true, false));
        assert_eq!(list_window(5, 3, 4, 3), (3, 2, true, false));
        assert_eq!(list_window(5, 3, 0, 3), (0, 2, false, true));
        assert_eq!(list_window(3, 3, 2, 0), (0, 3, false, false));
        for sel in 0..5 {
            let (f, c, _, _) = list_window(5, 3, sel, 0);
            assert!(f <= sel && sel < f + c);
        }
    }

    /// The pass criterion: each gauge update under 40 bytes, in every
    /// profile, after a frame left the dialog's attributes set.
    #[test]
    fn gauge_updates_are_small() {
        for lines in [Lines::Utf8, Lines::Dec, Lines::Ascii] {
            for colour in [true, false] {
                for caps in [Caps::ALL, Caps::NONE] {
                    let mut o = Out::new(lines, colour, caps);
                    o.attr(DLG);
                    o.take();
                    for step in 0..GAUGE_STEPS {
                        gauge_update(&mut o, 0, 0, 10, step, step + 1);
                        let n = o.take().len();
                        assert!(n < 40, "{lines:?} colour {colour} {caps:?} step {step}: {n} bytes");
                    }
                }
            }
        }
    }

    #[test]
    fn the_last_cell_is_never_written() {
        let p = Place::new(Size { rows: 24, cols: 80, assumed: false }).unwrap();
        assert_eq!(p.fit(24, 1, 80), 79);
        assert_eq!(p.fit(23, 1, 80), 80);
        let p = Place::new(Size { rows: 30, cols: 100, assumed: false }).unwrap();
        assert_eq!((p.top, p.left), (3, 10));
        assert_eq!(p.fit(24, 1, 80), 80);
        assert!(Place::new(Size { rows: 23, cols: 80, assumed: false }).is_none());
    }
}
