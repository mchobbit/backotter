//! The escape sequences bko-rescue may send, and nothing else: move, clear,
//! SGR 0, 1, 7, 30-37 and 40-47, cursor hide and show, and DEC line drawing
//! on and off.  Never `38;5` or `48;5`, no queries, no `ESC [ K`.
//! Text goes through `text::shown`, so nothing else can be sent.
//!
//! An `Out` builds one frame's bytes in a Vec for a single write, and
//! keeps what the terminal's attributes and charset are, so it sends only
//! changes.  `screen.rs` diffs frames on top of it.

use crate::text::shown;

/// How boxes are drawn.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Lines {
    Utf8,
    Dec,
    Ascii,
}

impl Lines {
    pub fn parse(s: &str) -> Option<Lines> {
        match s.to_ascii_lowercase().as_str() {
            "utf8" | "utf-8" => Some(Lines::Utf8),
            "dec" => Some(Lines::Dec),
            "ascii" => Some(Lines::Ascii),
            _ => None,
        }
    }

    pub fn name(self) -> &'static str {
        match self {
            Lines::Utf8 => "utf8",
            Lines::Dec => "dec",
            Lines::Ascii => "ascii",
        }
    }

    pub fn next(self) -> Lines {
        match self {
            Lines::Utf8 => Lines::Dec,
            Lines::Dec => Lines::Ascii,
            Lines::Ascii => Lines::Utf8,
        }
    }
}

/// The capability flags: the sequences a console was seen to take (RT3
/// fills in the table in profile.rs).  Each has a fallback: one SGR
/// parameter at a time, the cursor parked at the focus, and clearing
/// with spaces.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Caps {
    pub joined_sgr: bool,
    pub hide_cursor: bool,
    pub clear: bool,
}

impl Caps {
    pub const ALL: Caps = Caps { joined_sgr: true, hide_cursor: true, clear: true };
    pub const NONE: Caps = Caps { joined_sgr: false, hide_cursor: false, clear: false };
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Colour {
    Black = 0,
    Red,
    Green,
    Yellow,
    Blue,
    Magenta,
    Cyan,
    White,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq, Default)]
pub struct Attr {
    pub fg: Option<Colour>,
    pub bg: Option<Colour>,
    pub bold: bool,
    pub reverse: bool,
}

impl Attr {
    pub const PLAIN: Attr = Attr { fg: None, bg: None, bold: false, reverse: false };

    pub const fn colours(fg: Colour, bg: Colour) -> Attr {
        Attr { fg: Some(fg), bg: Some(bg), bold: false, reverse: false }
    }

    /// Focus: reverse and bold, in colour and in mono.
    pub const fn focus(self) -> Attr {
        Attr { fg: self.fg, bg: self.bg, bold: true, reverse: true }
    }

    pub const fn reverse(self) -> Attr {
        Attr { fg: self.fg, bg: self.bg, bold: self.bold, reverse: true }
    }
}

/// A line-drawing glyph, turned into bytes by the profile.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Glyph {
    Horiz,
    Vert,
    TopLeft,
    TopRight,
    BottomLeft,
    BottomRight,
    TeeLeft,
    TeeRight,
    TeeTop,
    TeeBottom,
}

impl Glyph {
    pub fn utf8(self) -> char {
        match self {
            Glyph::Horiz => '\u{2500}',
            Glyph::Vert => '\u{2502}',
            Glyph::TopLeft => '\u{250c}',
            Glyph::TopRight => '\u{2510}',
            Glyph::BottomLeft => '\u{2514}',
            Glyph::BottomRight => '\u{2518}',
            Glyph::TeeLeft => '\u{251c}',
            Glyph::TeeRight => '\u{2524}',
            Glyph::TeeTop => '\u{252c}',
            Glyph::TeeBottom => '\u{2534}',
        }
    }

    pub fn dec(self) -> u8 {
        match self {
            Glyph::Horiz => b'q',
            Glyph::Vert => b'x',
            Glyph::TopLeft => b'l',
            Glyph::TopRight => b'k',
            Glyph::BottomLeft => b'm',
            Glyph::BottomRight => b'j',
            Glyph::TeeLeft => b't',
            Glyph::TeeRight => b'u',
            Glyph::TeeTop => b'w',
            Glyph::TeeBottom => b'v',
        }
    }

    pub fn ascii(self) -> u8 {
        match self {
            Glyph::Horiz => b'-',
            Glyph::Vert => b'|',
            _ => b'+',
        }
    }
}

pub struct Out {
    pub buf: Vec<u8>,
    pub lines: Lines,
    pub colour: bool,
    pub caps: Caps,
    cur: Option<Attr>,
    dec_on: bool,
}

impl Out {
    pub fn new(lines: Lines, colour: bool, caps: Caps) -> Out {
        Out { buf: Vec::with_capacity(8192), lines, colour, caps, cur: None, dec_on: false }
    }

    /// The frame so far, leaving the Out empty (its state kept).
    pub fn take(&mut self) -> Vec<u8> {
        std::mem::take(&mut self.buf)
    }

    /// Forget what the terminal's attributes and charset are (something
    /// else may have written to it), so the next `attr` sends them in full
    /// and the next glyph switches DEC on again.  In DEC, `ESC ( B` is sent
    /// whatever this Out thought: a terminal left with DEC on (a killed
    /// bko-rescue, another program) would show letters as line glyphs.
    pub fn forget(&mut self) {
        self.cur = None;
        if self.lines == Lines::Dec {
            self.buf.extend_from_slice(b"\x1b(B");
        }
        self.dec_on = false;
    }

    /// The attributes the terminal has now, as far as this Out knows.
    pub fn current(&self) -> Option<Attr> {
        self.cur
    }

    /// `a` as this profile sends it: without colours in mono.
    pub fn effective(&self, a: Attr) -> Attr {
        if self.colour {
            a
        } else {
            Attr { fg: None, bg: None, ..a }
        }
    }

    fn csi(&mut self) {
        self.buf.extend_from_slice(b"\x1b[");
    }

    /// Move to `row`, `col`, both from 1.
    pub fn move_to(&mut self, row: u16, col: u16) {
        self.csi();
        self.buf.extend_from_slice(format!("{row};{col}H").as_bytes());
    }

    /// `ESC [ 2 J` where the console takes it; false when it doesn't, and
    /// the caller clears with spaces.
    pub fn clear(&mut self) -> bool {
        if !self.caps.clear {
            return false;
        }
        self.attr(Attr::PLAIN);
        self.buf.extend_from_slice(b"\x1b[2J");
        true
    }

    pub fn hide_cursor(&mut self) {
        if self.caps.hide_cursor {
            self.buf.extend_from_slice(b"\x1b[?25l");
        }
    }

    pub fn show_cursor(&mut self) {
        if self.caps.hide_cursor {
            self.buf.extend_from_slice(b"\x1b[?25h");
        }
    }

    /// Set the attributes, sending only what changes: bold or reverse
    /// added, and colours changed, are sent alone; taking bold, reverse or
    /// a colour away needs `0` and everything again (22, 27, 39 and 49
    /// aren't in the set every console takes).
    pub fn attr(&mut self, a: Attr) {
        let a = self.effective(a);
        if self.cur == Some(a) {
            return;
        }
        let mut params: Vec<u8> = Vec::with_capacity(5);
        match self.cur {
            Some(c)
                if (a.bold || !c.bold)
                    && (a.reverse || !c.reverse)
                    && (a.fg.is_some() || c.fg.is_none())
                    && (a.bg.is_some() || c.bg.is_none()) =>
            {
                if a.bold && !c.bold {
                    params.push(1);
                }
                if a.reverse && !c.reverse {
                    params.push(7);
                }
                if let Some(f) = a.fg.filter(|_| a.fg != c.fg) {
                    params.push(30 + f as u8);
                }
                if let Some(b) = a.bg.filter(|_| a.bg != c.bg) {
                    params.push(40 + b as u8);
                }
            }
            _ => {
                params.push(0);
                if a.bold {
                    params.push(1);
                }
                if a.reverse {
                    params.push(7);
                }
                if let Some(f) = a.fg {
                    params.push(30 + f as u8);
                }
                if let Some(b) = a.bg {
                    params.push(40 + b as u8);
                }
            }
        }
        if self.caps.joined_sgr {
            self.csi();
            let joined: Vec<String> = params.iter().map(u8::to_string).collect();
            self.buf.extend_from_slice(joined.join(";").as_bytes());
            self.buf.push(b'm');
        } else {
            for p in params {
                self.csi();
                self.buf.extend_from_slice(p.to_string().as_bytes());
                self.buf.push(b'm');
            }
        }
        self.cur = Some(a);
    }

    fn dec(&mut self, on: bool) {
        if self.dec_on != on {
            self.buf.extend_from_slice(if on { b"\x1b(0" } else { b"\x1b(B" });
            self.dec_on = on;
        }
    }

    /// Text, each character as `text::shown` lets it through for this
    /// line style, so no control character or escape is ever sent.
    pub fn text(&mut self, s: &str) {
        for c in s.chars() {
            self.char(c);
        }
    }

    /// One character, as `text::shown` lets it through.  A space is a
    /// space in DEC's line set too, so it leaves DEC as it is.
    pub fn char(&mut self, c: char) {
        if c == ' ' {
            self.buf.push(b' ');
            return;
        }
        self.dec(false);
        let mut tmp = [0u8; 4];
        let c = shown(c, self.lines);
        self.buf.extend_from_slice(c.encode_utf8(&mut tmp).as_bytes());
    }

    /// Spaces: they leave DEC as it is, as `char` does.
    pub fn spaces(&mut self, n: usize) {
        self.buf.resize(self.buf.len() + n, b' ');
    }

    pub fn glyphs(&mut self, g: Glyph, n: usize) {
        match self.lines {
            Lines::Utf8 => {
                let mut tmp = [0u8; 4];
                let s = g.utf8().encode_utf8(&mut tmp);
                for _ in 0..n {
                    self.buf.extend_from_slice(s.as_bytes());
                }
            }
            Lines::Dec => {
                self.dec(true);
                self.buf.resize(self.buf.len() + n, g.dec());
            }
            Lines::Ascii => self.buf.resize(self.buf.len() + n, g.ascii()),
        }
    }

    pub fn glyph(&mut self, g: Glyph) {
        self.glyphs(g, 1);
    }

    /// End a frame: DEC drawing off, so nothing after (a panic message,
    /// the shell) prints in line glyphs.
    pub fn end(&mut self) {
        self.dec(false);
    }

    /// What puts the terminal back: attributes reset, DEC off, the cursor
    /// shown, the screen cleared, the cursor home.  Where `ESC [ 2 J` isn't
    /// used, spaces over every row of a `rows` x `cols` screen, the last
    /// row one cell short so nothing scrolls.
    pub fn reset_bytes(lines: Lines, caps: Caps, rows: u16, cols: u16) -> Vec<u8> {
        let mut v = b"\x1b[0m".to_vec();
        if lines == Lines::Dec {
            v.extend_from_slice(b"\x1b(B");
        }
        if caps.hide_cursor {
            v.extend_from_slice(b"\x1b[?25h");
        }
        if caps.clear {
            v.extend_from_slice(b"\x1b[2J");
        } else {
            for row in 1..=rows {
                v.extend_from_slice(format!("\x1b[{row};1H").as_bytes());
                let n = if row == rows { cols.saturating_sub(1) } else { cols };
                v.resize(v.len() + usize::from(n), b' ');
            }
        }
        v.extend_from_slice(b"\x1b[H");
        v
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    const BG: Attr = Attr::colours(Colour::White, Colour::Blue);
    const DLG: Attr = Attr::colours(Colour::Black, Colour::White);

    fn out(lines: Lines, colour: bool, caps: Caps) -> Out {
        Out::new(lines, colour, caps)
    }

    #[test]
    fn attributes_joined_and_one_by_one() {
        let mut o = out(Lines::Utf8, true, Caps::ALL);
        o.attr(BG);
        o.attr(BG);
        o.attr(DLG.focus());
        assert_eq!(o.take(), b"\x1b[0;37;44m\x1b[1;7;30;47m");
        let mut o = out(Lines::Utf8, true, Caps::NONE);
        o.attr(DLG);
        assert_eq!(o.take(), b"\x1b[0m\x1b[30m\x1b[47m");
    }

    #[test]
    fn adding_reverse_sends_only_seven() {
        let mut o = out(Lines::Utf8, true, Caps::ALL);
        o.attr(DLG);
        o.take();
        o.attr(DLG.reverse());
        o.attr(DLG);
        assert_eq!(o.take(), b"\x1b[7m\x1b[0;30;47m");
    }

    #[test]
    fn taking_away_resets_and_adding_doesnt() {
        let mut o = out(Lines::Utf8, true, Caps::NONE);
        o.attr(BG);
        o.take();
        o.attr(DLG);
        o.attr(Attr::PLAIN);
        o.attr(BG.focus());
        o.attr(BG);
        assert_eq!(o.take(), b"\x1b[30m\x1b[47m\x1b[0m\x1b[1m\x1b[7m\x1b[37m\x1b[44m\x1b[0m\x1b[37m\x1b[44m");
    }

    #[test]
    fn mono_drops_colours_only() {
        let mut o = out(Lines::Ascii, false, Caps::ALL);
        o.attr(BG);
        o.attr(DLG);
        o.attr(DLG.focus());
        assert_eq!(o.take(), b"\x1b[0m\x1b[1;7m");
    }

    #[test]
    fn line_styles() {
        for (lines, want) in [
            (Lines::Utf8, "┌─x│".as_bytes().to_vec()),
            (Lines::Dec, b"\x1b(0lq\x1b(Bx\x1b(0x\x1b(B".to_vec()),
            (Lines::Ascii, b"+-x|".to_vec()),
        ] {
            let mut o = out(lines, true, Caps::ALL);
            o.glyph(Glyph::TopLeft);
            o.glyph(Glyph::Horiz);
            o.text("x");
            o.glyph(Glyph::Vert);
            o.end();
            assert_eq!(o.take(), want, "{lines:?}");
        }
    }

    #[test]
    fn text_is_sanitised() {
        let mut o = out(Lines::Utf8, true, Caps::ALL);
        o.text("a\x1b[31mb\u{9b}c\u{202e}é\u{4e00}\u{1f600}\u{301}");
        assert_eq!(String::from_utf8(o.take()).unwrap(), "a?[31mb?c?é???");
        let mut o = out(Lines::Ascii, true, Caps::ALL);
        o.text("é\n");
        assert_eq!(o.take(), b"??");
    }

    #[test]
    fn spaces_keep_dec_on() {
        let mut o = out(Lines::Dec, true, Caps::ALL);
        o.glyph(Glyph::Vert);
        o.spaces(2);
        o.char(' ');
        o.glyph(Glyph::Vert);
        o.char('a');
        o.end();
        assert_eq!(o.take(), b"\x1b(0x   x\x1b(Ba");
    }

    #[test]
    fn forget_sends_everything_again() {
        let mut o = out(Lines::Dec, true, Caps::ALL);
        o.attr(BG);
        o.glyph(Glyph::Horiz);
        o.forget();
        o.attr(BG);
        o.glyph(Glyph::Horiz);
        o.end();
        assert_eq!(o.take(), b"\x1b[0;37;44m\x1b(0q\x1b(B\x1b[0;37;44m\x1b(0q\x1b(B");
        assert_eq!(o.current(), Some(BG));
        // DEC off is sent even when this Out thinks it is off already.
        let mut o = out(Lines::Dec, true, Caps::ALL);
        o.forget();
        o.text("a");
        assert_eq!(o.take(), b"\x1b(Ba");
        let mut o = out(Lines::Utf8, true, Caps::ALL);
        o.forget();
        assert!(o.take().is_empty(), "no ESC ( B outside DEC");
        assert_eq!(out(Lines::Utf8, false, Caps::ALL).effective(BG.focus()), Attr::PLAIN.focus());
    }

    #[test]
    fn never_256_colours_or_bright() {
        let mut o = out(Lines::Dec, true, Caps::ALL);
        for fg in [Colour::Black, Colour::White, Colour::Cyan] {
            for bg in [Colour::Blue, Colour::Red] {
                o.attr(Attr::colours(fg, bg).focus());
                o.attr(Attr::colours(fg, bg));
            }
        }
        let s = String::from_utf8(o.take()).unwrap();
        assert!(!s.contains("38;") && !s.contains("48;") && !s.contains("[9"));
    }

    #[test]
    fn reset_bytes_per_profile() {
        assert_eq!(Out::reset_bytes(Lines::Dec, Caps::ALL, 24, 80), b"\x1b[0m\x1b(B\x1b[?25h\x1b[2J\x1b[H");
        // The fallback blanks with spaces, the last cell left alone.
        assert_eq!(
            Out::reset_bytes(Lines::Ascii, Caps::NONE, 3, 4),
            b"\x1b[0m\x1b[1;1H    \x1b[2;1H    \x1b[3;1H   \x1b[H"
        );
        let v = Out::reset_bytes(Lines::Utf8, Caps::NONE, 24, 80);
        assert_eq!(v.iter().filter(|&&b| b == b' ').count(), 24 * 80 - 1);
    }
}
