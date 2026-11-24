//! The screen buffer: cells, the 80x24 canvas, and diffed redraw
//! (docs/design/rescue-tui.md, "Screen buffer and redraw").
//!
//! - A cell is a character or a line glyph, and its attributes.  Widgets
//!   draw into a `Canvas` and never touch the terminal; text goes in only
//!   as `Clean`, so an unsanitised string can't reach the screen.
//! - `Screen` keeps what is on the terminal and, for each frame, sends
//!   only the runs of cells that changed: a move, the attribute changes and
//!   the glyphs, with DEC's charset switched only around line glyphs.  At
//!   9600 baud that is what makes the screen usable.
//! - A full redraw at the start, after a resize or a profile change, and
//!   whenever the caller says (each new dialog, and every `refresh_s`).
//! - The canvas is centred on a bigger terminal, the margin filled with
//!   the background.  The terminal's last cell is never written, so no
//!   console scrolls.
//! - `Canvas::dump` is the golden-screen form: the glyph rows, then the
//!   attribute rows, then a legend of the letters.

use crate::term::out::{Attr, Caps, Colour, Glyph, Lines, Out};
use crate::term::tty::Size;
use crate::text::{self, Clean};

pub const ROWS: u16 = 24;
pub const COLS: u16 = 80;

/// Whiptail's palette: white on blue behind, black on grey dialogs, a
/// black shadow.  Focus is reverse and bold (`Attr::focus`), in colour
/// and in mono.  RT3 confirms each console shows it.
pub const BACKGROUND: Attr = Attr::colours(Colour::White, Colour::Blue);
pub const DIALOG: Attr = Attr::colours(Colour::Black, Colour::White);
pub const SHADOW: Attr = Attr { fg: None, bg: Some(Colour::Black), bold: false, reverse: false };

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Ch {
    /// A character of a `Clean` (one cell wide), or a space.
    Text(char),
    Line(Glyph),
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Cell {
    pub ch: Ch,
    pub attr: Attr,
}

impl Cell {
    pub const fn blank(attr: Attr) -> Cell {
        Cell { ch: Ch::Text(' '), attr }
    }
}

/// A grid of cells, rows and columns from 0.  Drawing off its edge is
/// clipped.  `cursor` is where the cursor goes when the console can't
/// hide it: the focused field.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Canvas {
    rows: u16,
    cols: u16,
    cells: Vec<Cell>,
    pub cursor: Option<(u16, u16)>,
}

impl Canvas {
    pub fn new(rows: u16, cols: u16, attr: Attr) -> Canvas {
        Canvas { rows, cols, cells: vec![Cell::blank(attr); usize::from(rows) * usize::from(cols)], cursor: None }
    }

    /// The 80x24 canvas, all background.
    pub fn standard() -> Canvas {
        Canvas::new(ROWS, COLS, BACKGROUND)
    }

    pub fn rows(&self) -> u16 {
        self.rows
    }

    pub fn cols(&self) -> u16 {
        self.cols
    }

    fn idx(&self, row: u16, col: u16) -> Option<usize> {
        (row < self.rows && col < self.cols).then(|| usize::from(row) * usize::from(self.cols) + usize::from(col))
    }

    pub fn get(&self, row: u16, col: u16) -> Option<Cell> {
        self.idx(row, col).map(|i| self.cells[i])
    }

    fn set(&mut self, row: u16, col: u16, c: Cell) {
        if let Some(i) = self.idx(row, col) {
            self.cells[i] = c;
        }
    }

    /// Spaces over `h` rows and `w` columns.
    pub fn fill(&mut self, row: u16, col: u16, h: u16, w: u16, attr: Attr) {
        for r in row..row.saturating_add(h) {
            for c in col..col.saturating_add(w) {
                self.set(r, c, Cell::blank(attr));
            }
        }
    }

    /// Text at (`row`, `col`), up to its first newline (the rest is
    /// another line, the caller's to place).  Returns the cells written.
    pub fn text(&mut self, row: u16, col: u16, t: &Clean, attr: Attr) -> u16 {
        let mut n = 0u16;
        for ch in t.chars().take_while(|&c| c != '\n') {
            if col.saturating_add(n) >= self.cols {
                break;
            }
            self.set(row, col + n, Cell { ch: Ch::Text(ch), attr });
            n += 1;
        }
        n
    }

    /// Text in exactly `w` cells: padded with spaces, or cut with `>`.
    pub fn text_in(&mut self, row: u16, col: u16, w: u16, t: &Clean, attr: Attr) {
        let first = t.lines().into_iter().next().unwrap_or_default();
        let n = self.text(row, col, &first.truncate(usize::from(w)), attr);
        self.fill(row, col.saturating_add(n), 1, w - n.min(w), attr);
    }

    /// `n` line glyphs across.
    pub fn hline(&mut self, row: u16, col: u16, n: u16, g: Glyph, attr: Attr) {
        for c in col..col.saturating_add(n) {
            self.set(row, c, Cell { ch: Ch::Line(g), attr });
        }
    }

    /// `n` line glyphs down.
    pub fn vline(&mut self, row: u16, col: u16, n: u16, g: Glyph, attr: Attr) {
        for r in row..row.saturating_add(n) {
            self.set(r, col, Cell { ch: Ch::Line(g), attr });
        }
    }

    pub fn glyph(&mut self, row: u16, col: u16, g: Glyph, attr: Attr) {
        self.set(row, col, Cell { ch: Ch::Line(g), attr });
    }

    /// A box `h` x `w` with its inside blank, all in `attr`.
    pub fn frame(&mut self, row: u16, col: u16, h: u16, w: u16, attr: Attr) {
        if h < 2 || w < 2 {
            return;
        }
        self.fill(row + 1, col + 1, h - 2, w - 2, attr);
        self.glyph(row, col, Glyph::TopLeft, attr);
        self.hline(row, col + 1, w - 2, Glyph::Horiz, attr);
        self.glyph(row, col + w - 1, Glyph::TopRight, attr);
        self.vline(row + 1, col, h - 2, Glyph::Vert, attr);
        self.vline(row + 1, col + w - 1, h - 2, Glyph::Vert, attr);
        self.glyph(row + h - 1, col, Glyph::BottomLeft, attr);
        self.hline(row + h - 1, col + 1, w - 2, Glyph::Horiz, attr);
        self.glyph(row + h - 1, col + w - 1, Glyph::BottomRight, attr);
    }

    /// New attributes over an area, the glyphs kept: a shadow, or focus
    /// moved to a row.
    pub fn recolour(&mut self, row: u16, col: u16, h: u16, w: u16, attr: Attr) {
        for r in row..row.saturating_add(h) {
            for c in col..col.saturating_add(w) {
                if let Some(i) = self.idx(r, c) {
                    self.cells[i].attr = attr;
                }
            }
        }
    }

    /// A shadow one column right and one row down of the box at (`row`,
    /// `col`), `h` x `w`.  Colour only: in mono it shows as nothing.
    pub fn shadow(&mut self, row: u16, col: u16, h: u16, w: u16) {
        self.recolour(row + 1, col + w, h, 1, SHADOW);
        self.recolour(row + h, col + 1, 1, w, SHADOW);
    }

    /// The golden-screen form, as a profile shows it: one line per row of
    /// glyphs (DEC's line glyphs drawn as UTF-8's, which look the same,
    /// ASCII's as `+-|`; characters as `text::shown` lets them through),
    /// a blank line, one line per row of attribute letters (as sent: no
    /// colours in mono), a blank line, and a legend of the letters used.
    pub fn dump(&self, lines: Lines, colour: bool) -> String {
        let eff = |a: Attr| if colour { a } else { Attr { fg: None, bg: None, ..a } };
        let mut s = String::new();
        for r in 0..self.rows {
            for c in 0..self.cols {
                let cell = self.cells[usize::from(r) * usize::from(self.cols) + usize::from(c)];
                s.push(match cell.ch {
                    Ch::Text(ch) => text::shown(ch, lines),
                    Ch::Line(g) if lines == Lines::Ascii => char::from(g.ascii()),
                    Ch::Line(g) => g.utf8(),
                });
            }
            s.push('\n');
        }
        s.push('\n');
        let mut used: Vec<(char, Attr)> = Vec::new();
        let mut others: Vec<Attr> = Vec::new();
        for r in 0..self.rows {
            for c in 0..self.cols {
                let a = eff(self.cells[usize::from(r) * usize::from(self.cols) + usize::from(c)].attr);
                let l = match LETTERS.iter().find(|(x, _)| *x == a) {
                    Some((_, l)) => *l,
                    None => {
                        let i = others.iter().position(|x| *x == a).unwrap_or_else(|| {
                            others.push(a);
                            others.len() - 1
                        });
                        char::from_digit(i as u32 % 10, 10).unwrap_or('#')
                    }
                };
                if !used.iter().any(|(u, x)| *u == l && *x == a) {
                    used.push((l, a));
                }
                s.push(l);
            }
            s.push('\n');
        }
        s.push('\n');
        used.sort_by_key(|(l, _)| *l);
        for (l, a) in used {
            s.push_str(&format!("{l} {}\n", sgr(a)));
        }
        s
    }
}

/// The attribute letters of a dump.  Others get digits, in order of first
/// appearance, and the legend says what each is.
const LETTERS: &[(Attr, char)] = &[
    (Attr::PLAIN, 'p'),
    (Attr::PLAIN.focus(), 'P'),
    (Attr::PLAIN.reverse(), 'R'),
    (Attr { bold: true, ..Attr::PLAIN }, 'o'),
    (BACKGROUND, 'b'),
    (BACKGROUND.focus(), 'B'),
    (DIALOG, 'd'),
    (DIALOG.focus(), 'D'),
    (DIALOG.reverse(), 'r'),
    (Attr { bold: true, ..DIALOG }, 'h'),
    (SHADOW, 's'),
];

/// An attribute as SGR parameters, as `Out` sends it in full.
pub fn sgr(a: Attr) -> String {
    let mut p = vec!["0".to_string()];
    if a.bold {
        p.push("1".into());
    }
    if a.reverse {
        p.push("7".into());
    }
    if let Some(f) = a.fg {
        p.push((30 + f as u8).to_string());
    }
    if let Some(b) = a.bg {
        p.push((40 + b as u8).to_string());
    }
    p.join(";")
}

/// Unchanged cells between two changed runs on a row that are written
/// again rather than moved over: a move is 6-8 bytes.
const JOIN_GAP: u16 = 4;

/// What is on the terminal, and the frames that bring it to a canvas.
pub struct Screen {
    out: Out,
    term: Size,
    /// The canvas on the terminal now, and where (top, left, from 0).
    shown: Option<(Canvas, u16, u16)>,
    full: bool,
    /// The next full frame clears the screen first, where the console
    /// clears: the first frame, after a resize, and after something else
    /// wrote to the terminal.  The terminal's last cell is never written,
    /// so only a clear empties it.  Where the console doesn't clear, that
    /// one cell may keep what was there.
    wipe: bool,
    /// The cursor's place, from 1; None when unknown (after the last
    /// column, or before the first frame).
    at: Option<(u16, u16)>,
    pub margin: Attr,
}

impl Screen {
    pub fn new(lines: Lines, colour: bool, caps: Caps, term: Size) -> Screen {
        Screen {
            out: Out::new(lines, colour, caps),
            term,
            shown: None,
            full: true,
            wipe: true,
            at: None,
            margin: BACKGROUND,
        }
    }

    pub fn lines(&self) -> Lines {
        self.out.lines
    }

    pub fn colour(&self) -> bool {
        self.out.colour
    }

    pub fn caps(&self) -> Caps {
        self.out.caps
    }

    pub fn size(&self) -> Size {
        self.term
    }

    /// A new profile: the next frame is a full one.  A cursor the old
    /// caps hid is shown first when the new ones don't hide it, or it
    /// would stay hidden after exit too.
    pub fn set_profile(&mut self, lines: Lines, colour: bool, caps: Caps) {
        if self.out.caps.hide_cursor && !caps.hide_cursor {
            self.out.show_cursor();
        }
        self.out.end();
        let pending = self.out.take();
        self.out = Out::new(lines, colour, caps);
        self.out.buf = pending;
        self.full = true;
    }

    /// The terminal's size now; a change means a full redraw.
    pub fn resize(&mut self, s: Size) {
        if s != self.term {
            self.term = s;
            self.full = true;
            self.wipe = true;
        }
    }

    /// The next frame is a full one: a new dialog, or the periodic
    /// redraw.
    pub fn invalidate(&mut self) {
        self.full = true;
    }

    /// Something else wrote to the terminal (a child's output, a kernel
    /// message): the next frame is a full one, and clears the screen
    /// first where the console clears.
    pub fn written_over(&mut self) {
        self.full = true;
        self.wipe = true;
    }

    /// Whether a canvas of `rows` x `cols` fits the terminal.
    pub fn fits(&self, rows: u16, cols: u16) -> bool {
        self.term.rows >= rows && self.term.cols >= cols
    }

    /// Where a canvas goes: centred, or at the top left when it doesn't
    /// fit (and is clipped).
    fn place(&self, c: &Canvas) -> (u16, u16) {
        (self.term.rows.saturating_sub(c.rows) / 2, self.term.cols.saturating_sub(c.cols) / 2)
    }

    /// The terminal's last cell, from 1, which is never written.
    fn last(&self) -> (u16, u16) {
        (self.term.rows, self.term.cols)
    }

    fn goto(&mut self, row: u16, col: u16) {
        if self.at != Some((row, col)) {
            self.out.move_to(row, col);
            self.at = Some((row, col));
        }
    }

    /// One cell at the cursor, which is at (`row`, `col`).
    fn put(&mut self, row: u16, col: u16, cell: Cell) {
        if (row, col) == self.last() {
            // Skipped; the cursor stays, so the next write moves.
            self.at = None;
            return;
        }
        let want = self.out.effective(cell.attr);
        // A space looks the same in any foreground and in bold: keep the
        // attributes the terminal has, when only those differ.
        let same_look = cell.ch == Ch::Text(' ')
            && self.out.current().is_some_and(|c| c.bg == want.bg && c.reverse == want.reverse && !want.reverse);
        if !same_look {
            self.out.attr(want);
        }
        match cell.ch {
            Ch::Text(c) => self.out.char(c),
            Ch::Line(g) => self.out.glyph(g),
        }
        self.at = (col < self.term.cols).then_some((row, col + 1));
    }

    /// The bytes that bring the terminal to `want`.  Empty when nothing
    /// changed.
    pub fn frame(&mut self, want: &Canvas) -> Vec<u8> {
        let (top, left) = self.place(want);
        let same_place = matches!(&self.shown, Some((c, t, l)) if c.rows == want.rows && c.cols == want.cols && *t == top && *l == left);
        if self.full || !same_place {
            self.full_frame(want, top, left);
        } else if let Some((old, _, _)) = self.shown.take() {
            self.diff_frame(&old, want, top, left);
        }
        if !self.out.caps.hide_cursor {
            if let Some((r, c)) = want.cursor {
                if r < want.rows && c < want.cols && top + r < self.term.rows && left + c < self.term.cols {
                    self.goto(top + r + 1, left + c + 1);
                }
            }
        }
        self.out.end();
        self.shown = Some((want.clone(), top, left));
        self.full = false;
        self.wipe = false;
        self.out.take()
    }

    fn full_frame(&mut self, want: &Canvas, top: u16, left: u16) {
        // Something else may have written: send everything in full.
        self.out.forget();
        self.at = None;
        self.out.hide_cursor();
        let (rows, cols) = (self.term.rows, self.term.cols);
        let margins = rows > want.rows || cols > want.cols;
        // ESC [ 2 J clears in whatever background a console uses, so the
        // margin is left to it only when the margin is plain; else the
        // margin is spaces.  A wipe clears too, whatever the margin, and
        // every cell is written again but the last, which keeps the
        // console's clear colour.
        let plain_margin = margins && self.out.effective(self.margin) == Attr::PLAIN;
        let sent_clear = (self.wipe || plain_margin) && self.out.clear();
        if sent_clear {
            self.at = None;
        }
        let cleared = sent_clear && plain_margin;
        let margin = Cell::blank(self.margin);
        for r in 0..rows {
            let in_rows = r >= top && r - top < want.rows;
            if !in_rows && cleared {
                continue;
            }
            let (from, to) = if cleared { (left, (left + want.cols).min(cols)) } else { (0, cols) };
            if from >= to {
                continue;
            }
            self.goto(r + 1, from + 1);
            for c in from..to {
                let in_cols = c >= left && c - left < want.cols;
                let cell = match (in_rows && in_cols).then(|| want.get(r - top, c - left)).flatten() {
                    Some(cell) => cell,
                    None => margin,
                };
                self.put(r + 1, c + 1, cell);
            }
        }
    }

    /// Whether two cells look the same on this profile: the same glyph in
    /// the same attributes as sent, or spaces with the same background
    /// and no reverse (a space shows no foreground and no bold).
    fn looks_same(&self, a: Option<Cell>, b: Option<Cell>) -> bool {
        let (Some(a), Some(b)) = (a, b) else { return a == b };
        let (x, y) = (self.out.effective(a.attr), self.out.effective(b.attr));
        a.ch == b.ch && (x == y || (a.ch == Ch::Text(' ') && x.bg == y.bg && !x.reverse && !y.reverse))
    }

    fn diff_frame(&mut self, old: &Canvas, want: &Canvas, top: u16, left: u16) {
        for r in 0..want.rows.min(self.term.rows.saturating_sub(top)) {
            let cols = want.cols.min(self.term.cols.saturating_sub(left));
            let changed = |s: &Screen, c: u16| !s.looks_same(old.get(r, c), want.get(r, c));
            let mut c = 0;
            while c < cols {
                if !changed(self, c) {
                    c += 1;
                    continue;
                }
                // A run: changed cells, joined across short gaps.
                let start = c;
                let mut end = c + 1;
                let mut gap = 0;
                let mut i = end;
                while i < cols {
                    if changed(self, i) {
                        end = i + 1;
                        gap = 0;
                    } else {
                        gap += 1;
                        if gap > JOIN_GAP {
                            break;
                        }
                    }
                    i += 1;
                }
                self.goto(top + r + 1, left + start + 1);
                for k in start..end {
                    if let Some(cell) = want.get(r, k) {
                        self.put(top + r + 1, left + k + 1, cell);
                    }
                }
                c = end;
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::testkit::{Checker, Rules};

    const S80: Size = Size { rows: 24, cols: 80, assumed: false };

    fn ours(s: &str) -> Clean {
        Clean::ours(s)
    }

    fn checked(scr: &Screen, frames: &[&[u8]]) {
        let mut ck = Checker::new(Rules {
            lines: scr.lines(),
            colour: scr.colour(),
            caps: scr.caps(),
            rows: scr.size().rows,
            cols: scr.size().cols,
        });
        for f in frames {
            ck.feed(f);
        }
        assert_eq!(ck.result(), Ok(()));
    }

    #[test]
    fn canvas_drawing_clips_and_pads() {
        let mut c = Canvas::new(3, 10, Attr::PLAIN);
        assert_eq!(c.text(0, 7, &ours("abcdef"), DIALOG), 3);
        c.text_in(1, 0, 5, &ours("toolong"), DIALOG);
        c.text_in(2, 0, 5, &ours("ab"), DIALOG);
        c.frame(0, 0, 0, 5, DIALOG);
        c.hline(5, 0, 3, Glyph::Horiz, DIALOG);
        let d = c.dump(Lines::Utf8, true);
        assert!(d.starts_with("       abc\ntool>     \nab        \n"), "{d}");
        assert_eq!(c.get(2, 4), Some(Cell::blank(DIALOG)));
        assert_eq!(c.get(3, 0), None);
    }

    #[test]
    fn dump_letters_and_legend() {
        let mut c = Canvas::new(2, 6, BACKGROUND);
        c.frame(0, 0, 2, 4, DIALOG);
        c.recolour(0, 4, 1, 1, DIALOG.focus());
        c.recolour(1, 4, 1, 1, Attr::colours(Colour::Red, Colour::Green));
        c.recolour(0, 5, 2, 1, SHADOW);
        let d = c.dump(Lines::Ascii, true);
        assert_eq!(d, "+--+  \n+--+  \n\nddddDs\ndddd0s\n\n0 0;31;42\nD 0;1;7;30;47\nd 0;30;47\ns 0;40\n");
        let mono = c.dump(Lines::Ascii, false);
        assert!(mono.contains("\nppppPp\npppppp\n") && mono.ends_with("P 0;1;7\np 0\n"), "{mono}");
        // The shadow: one column right and one row down.
        let mut c = Canvas::new(4, 6, BACKGROUND);
        c.shadow(0, 0, 2, 4);
        assert!(c.dump(Lines::Ascii, true).contains("\nbbbbbb\nbbbbsb\nbssssb\nbbbbbb\n"));
    }

    #[test]
    fn one_changed_cell_is_one_move_and_one_byte() {
        let mut s = Screen::new(Lines::Utf8, true, Caps::ALL, S80);
        let mut c = Canvas::standard();
        let first = s.frame(&c);
        c.text(5, 10, &ours("x"), BACKGROUND);
        let d = s.frame(&c);
        assert_eq!(d, b"\x1b[6;11Hx");
        assert!(s.frame(&c).is_empty(), "nothing changed, nothing sent");
        checked(&s, &[&first, &d]);
    }

    #[test]
    fn close_changes_are_joined_and_far_ones_moved_to() {
        let mut s = Screen::new(Lines::Ascii, true, Caps::ALL, S80);
        let mut c = Canvas::standard();
        s.frame(&c);
        c.text(0, 0, &ours("a"), BACKGROUND);
        c.text(0, 3, &ours("b"), BACKGROUND);
        c.text(0, 40, &ours("c"), BACKGROUND);
        assert_eq!(s.frame(&c), b"\x1b[1;1Ha  b\x1b[1;41Hc");
    }

    fn has_clear(f: &[u8]) -> bool {
        f.windows(4).any(|w| w == b"\x1b[2J")
    }

    #[test]
    fn a_full_frame_at_80x24_writes_every_cell_but_the_last() {
        for caps in [Caps::ALL, Caps::NONE] {
            let mut s = Screen::new(Lines::Utf8, true, caps, S80);
            let c = Canvas::standard();
            let f = s.frame(&c);
            let mut ck = Checker::new(Rules { lines: Lines::Utf8, colour: true, caps, rows: 24, cols: 80 });
            ck.feed(&f);
            assert_eq!(ck.result(), Ok(()));
            assert_eq!(ck.cells, 24 * 80 - 1);
            // The first frame clears where the console clears; a full
            // redraw after it doesn't.
            assert_eq!(has_clear(&f), caps.clear);
            s.invalidate();
            assert!(!has_clear(&s.frame(&c)), "no clear on a redraw at 80x24");
            s.written_over();
            assert_eq!(has_clear(&s.frame(&c)), caps.clear);
        }
    }

    /// Whatever was on the terminal before (a shell prompt, a kernel
    /// message) is gone from the last cell too, which is never written,
    /// where the console clears.  Where it doesn't, that cell keeps it.
    #[test]
    fn the_first_frame_empties_the_last_cell() {
        for caps in [Caps::ALL, Caps::NONE] {
            let mut s = Screen::new(Lines::Utf8, true, caps, S80);
            let mut ck = Checker::new(Rules { lines: Lines::Utf8, colour: true, caps, rows: 24, cols: 80 });
            ck.foreign('#', false);
            ck.feed(&s.frame(&Canvas::standard()));
            assert_eq!(ck.result(), Ok(()));
            let last = ck.shown(24, 80).map(|(ch, _)| ch);
            assert_eq!(last, Some(if caps.clear { ' ' } else { '#' }), "{caps:?}");
            assert_eq!(ck.shown(24, 79).map(|(ch, a)| (ch, a.bg)), Some((' ', BACKGROUND.bg)));
        }
    }

    /// A DEC terminal left with DEC on (a bko-rescue killed mid-frame, or
    /// another program): the first frame switches it off before any text.
    #[test]
    fn a_dec_first_frame_switches_dec_off_first() {
        let mut s = Screen::new(Lines::Dec, true, Caps::ALL, S80);
        let mut c = Canvas::standard();
        c.text(0, 0, &ours("abc"), BACKGROUND);
        c.frame(2, 2, 4, 10, DIALOG);
        let f = s.frame(&c);
        let off = f.windows(3).position(|w| w == b"\x1b(B");
        let text = f.iter().position(|&b| b == b'a');
        assert!(off.is_some() && off < text, "ESC ( B before the first letter");
        let mut ck = Checker::new(Rules { lines: Lines::Dec, colour: true, caps: Caps::ALL, rows: 24, cols: 80 });
        ck.foreign('q', true);
        ck.feed(&f);
        assert_eq!(ck.result(), Ok(()));
        assert_eq!(ck.shown(1, 1), Some(('a', BACKGROUND)));
        assert_eq!(ck.shown(3, 3).map(|(ch, _)| ch), Some(Glyph::TopLeft.utf8()));
        // A full redraw sends it again.
        s.invalidate();
        assert!(s.frame(&c).starts_with(b"\x1b(B"));
    }

    #[test]
    fn a_profile_change_shows_a_cursor_the_new_caps_cant_hide() {
        let mut s = Screen::new(Lines::Utf8, true, Caps::ALL, S80);
        let c = Canvas::standard();
        assert!(s.frame(&c).starts_with(b"\x1b[?25l"));
        s.set_profile(Lines::Utf8, true, Caps::NONE);
        assert!(s.frame(&c).starts_with(b"\x1b[?25h"));
        let mut s = Screen::new(Lines::Utf8, true, Caps::ALL, S80);
        s.frame(&c);
        s.set_profile(Lines::Ascii, true, Caps::ALL);
        let f = s.frame(&c);
        assert!(!f.windows(6).any(|w| w == b"\x1b[?25h"), "still hidden: not shown");
    }

    #[test]
    fn a_bigger_terminal_centres_the_canvas_in_the_background() {
        let big = Size { rows: 30, cols: 100, assumed: false };
        let mut s = Screen::new(Lines::Utf8, true, Caps::ALL, big);
        let mut c = Canvas::standard();
        c.text(0, 0, &ours("T"), BACKGROUND);
        let f = s.frame(&c);
        let text = String::from_utf8_lossy(&f);
        assert!(text.contains("\x1b[4;1H"), "rows above written too");
        assert!(text.contains(&format!("{}T", " ".repeat(10))), "the margin, then the canvas at column 11");
        checked(&s, &[&f]);
        // In mono the margin is plain, so ESC [ 2 J clears it and only the
        // canvas is written.
        let mut s = Screen::new(Lines::Ascii, false, Caps::ALL, big);
        let f = s.frame(&c);
        assert!(f.windows(4).any(|w| w == b"\x1b[2J"));
        let mut ck = Checker::new(Rules { lines: Lines::Ascii, colour: false, caps: Caps::ALL, rows: 30, cols: 100 });
        ck.feed(&f);
        assert_eq!((ck.result(), ck.cells), (Ok(()), 24 * 80));
    }

    #[test]
    fn resize_and_profile_changes_redraw_in_full() {
        let mut s = Screen::new(Lines::Utf8, true, Caps::ALL, S80);
        let c = Canvas::standard();
        let full = s.frame(&c).len();
        assert!(s.frame(&c).is_empty());
        s.resize(S80);
        assert!(s.frame(&c).is_empty(), "the same size isn't a resize");
        s.resize(Size { rows: 25, cols: 80, assumed: false });
        assert!(s.frame(&c).len() >= full);
        s.set_profile(Lines::Dec, true, Caps::ALL);
        assert!(s.frame(&c).len() >= full);
        s.invalidate();
        assert!(!s.frame(&c).is_empty());
    }

    #[test]
    fn spaces_keep_the_attributes_when_only_the_foreground_differs() {
        let mut s = Screen::new(Lines::Ascii, true, Caps::ALL, S80);
        let mut c = Canvas::standard();
        s.frame(&c);
        c.fill(2, 0, 1, 3, Attr::colours(Colour::Black, Colour::Blue));
        c.fill(3, 0, 1, 3, Attr { bold: true, ..BACKGROUND });
        assert!(s.frame(&c).is_empty(), "spaces in another foreground or bold look the same");
        c.text(2, 0, &ours("y"), Attr::colours(Colour::Black, Colour::Blue));
        assert_eq!(s.frame(&c), b"\x1b[3;1H\x1b[30my");
    }

    #[test]
    fn dec_switches_only_around_line_glyphs() {
        let mut s = Screen::new(Lines::Dec, false, Caps::ALL, S80);
        let mut c = Canvas::new(ROWS, COLS, Attr::PLAIN);
        s.frame(&c);
        c.frame(1, 1, 3, 5, Attr::PLAIN);
        c.text(2, 2, &ours("a"), Attr::PLAIN);
        let f = s.frame(&c);
        assert_eq!(String::from_utf8_lossy(&f), "\x1b[2;2H\x1b(0lqqqk\x1b[3;2Hx\x1b(Ba  \x1b(0x\x1b[4;2Hmqqqj\x1b(B");
        checked(&s, &[&f]);
    }

    #[test]
    fn the_cursor_is_parked_only_where_it_cant_hide() {
        let mut c = Canvas::standard();
        c.cursor = Some((9, 23));
        let mut s = Screen::new(Lines::Ascii, true, Caps::NONE, S80);
        let f = s.frame(&c);
        assert!(f.ends_with(b"\x1b[10;24H"));
        assert!(s.frame(&c).is_empty(), "already there");
        let mut s = Screen::new(Lines::Ascii, true, Caps::ALL, S80);
        let f = s.frame(&c);
        assert!(f.starts_with(b"\x1b[?25l") && !f.ends_with(b"\x1b[10;24H"));
    }

    #[test]
    fn a_canvas_bigger_than_the_terminal_is_clipped() {
        let small = Size { rows: 14, cols: 60, assumed: false };
        let mut s = Screen::new(Lines::Utf8, true, Caps::ALL, small);
        let mut c = Canvas::standard();
        let f = s.frame(&c);
        c.text(20, 70, &ours("off"), BACKGROUND);
        c.text(13, 0, &ours("on"), BACKGROUND);
        let d = s.frame(&c);
        assert_eq!(d, b"\x1b[14;1Hon");
        checked(&s, &[&f, &d]);
        // The too-small notice is drawn at the terminal's own size.
        let notice = Canvas::new(small.rows, small.cols, BACKGROUND);
        let f = s.frame(&notice);
        let mut ck = Checker::new(Rules { lines: Lines::Utf8, colour: true, caps: Caps::ALL, rows: 14, cols: 60 });
        ck.feed(&f);
        assert_eq!((ck.result(), ck.cells), (Ok(()), 14 * 60 - 1));
    }

    #[test]
    fn hostile_text_never_reaches_the_terminal() {
        let mut s = Screen::new(Lines::Utf8, true, Caps::ALL, S80);
        let mut c = Canvas::standard();
        let first = s.frame(&c);
        let evil = Clean::line("\x1b[2J\x1b]0;x\x07\u{9b}31m\u{202e}中é", 64);
        c.text(3, 3, &evil, DIALOG);
        let d = s.frame(&c);
        checked(&s, &[&first, &d]);
        assert!(String::from_utf8_lossy(&d).contains("?[2J?]0;x??31m??é"));
    }
}
