//! What the tests share: a small pseudo-random generator for the
//! fuzz-style tests, golden files, a readable form of a byte stream, and
//! the checker of every byte a profile sends.  No crates, so no
//! cargo-fuzz (it needs nightly too): the fuzz-style tests run inside
//! `cargo test`, 100,000 cases each, `BKO_RESCUE_FUZZ=N` for more.
//!
//! In the library, not behind `cfg(test)`, so the integration tests in
//! `tests/` can use it.  The binary never calls it.

use std::path::PathBuf;

use crate::term::out::{Attr, Caps, Colour, Glyph, Lines};
use crate::text;

/// xorshift64*: deterministic, so a failing case is the same next run.
pub struct Rng(u64);

impl Rng {
    pub fn new(seed: u64) -> Rng {
        Rng(seed.max(1))
    }

    pub fn next_u64(&mut self) -> u64 {
        let mut x = self.0;
        x ^= x >> 12;
        x ^= x << 25;
        x ^= x >> 27;
        self.0 = x;
        x.wrapping_mul(0x2545_f491_4f6c_dd1d)
    }

    /// 0 to `n - 1` (0 when `n` is 0).
    pub fn below(&mut self, n: u64) -> u64 {
        if n == 0 {
            0
        } else {
            self.next_u64() % n
        }
    }

    pub fn pick<'a, T>(&mut self, v: &'a [T]) -> &'a T {
        &v[self.below(v.len() as u64) as usize]
    }
}

/// How many cases a fuzz-style test runs: BKO_RESCUE_FUZZ, or `default`.
pub fn fuzz_cases(default: usize) -> usize {
    match std::env::var("BKO_RESCUE_FUZZ") {
        Ok(v) => v.parse().unwrap_or_else(|_| panic!("BKO_RESCUE_FUZZ={v:?}: a number of cases")),
        Err(_) => default,
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Golden {
    /// A difference fails the test (the default).
    Check,
    /// Differences are printed, and the test goes on.
    Report,
    /// The golden files are written from what the code makes now.
    Bless,
}

impl Golden {
    pub fn mode() -> Golden {
        match std::env::var("BKO_RESCUE_GOLDEN").as_deref() {
            Err(_) | Ok("") | Ok("check") => Golden::Check,
            Ok("report") => Golden::Report,
            Ok("bless") => Golden::Bless,
            Ok(other) => panic!("BKO_RESCUE_GOLDEN={other:?}: check, report or bless"),
        }
    }
}

/// Compare `text` with the golden file `rel` (from the crate's root),
/// as BKO_RESCUE_GOLDEN says.
pub fn golden(rel: &str, text: &str) {
    let path: PathBuf = [env!("CARGO_MANIFEST_DIR"), rel].iter().collect();
    let mode = Golden::mode();
    if mode == Golden::Bless {
        if let Some(d) = path.parent() {
            std::fs::create_dir_all(d).unwrap();
        }
        if std::fs::read_to_string(&path).ok().as_deref() != Some(text) {
            std::fs::write(&path, text).unwrap();
            eprintln!("blessed {rel}");
        }
        return;
    }
    let kept = match std::fs::read_to_string(&path) {
        Ok(k) => k,
        Err(e) if mode == Golden::Report => {
            eprintln!("{rel}: {e} (BKO_RESCUE_GOLDEN=bless writes it)");
            return;
        }
        Err(e) => panic!("{rel}: {e} (BKO_RESCUE_GOLDEN=bless writes it)"),
    };
    if kept == text {
        return;
    }
    let diff = line_diff(&kept, text);
    match mode {
        Golden::Report => eprintln!("{rel} differs:\n{diff}"),
        _ => panic!("{rel} differs (BKO_RESCUE_GOLDEN=report shows all, =bless takes it):\n{diff}"),
    }
}

/// The lines that differ, by number: `-` the kept one, `+` the new.
pub fn line_diff(kept: &str, new: &str) -> String {
    let (a, b): (Vec<&str>, Vec<&str>) = (kept.lines().collect(), new.lines().collect());
    let mut out = String::new();
    for i in 0..a.len().max(b.len()) {
        let (x, y) = (a.get(i), b.get(i));
        if x != y {
            if let Some(x) = x {
                out.push_str(&format!("{:4} - {x}\n", i + 1));
            }
            if let Some(y) = y {
                out.push_str(&format!("{:4} + {y}\n", i + 1));
            }
        }
    }
    out
}

/// A byte stream as text, for golden files: ESC is `\e`, a backslash
/// `\\`, other bytes outside printable ASCII `\xNN` (UTF-8 too, so the
/// file says exactly what is sent).  A new line starts before each move.
pub fn escape(bytes: &[u8]) -> String {
    let mut s = String::new();
    for (i, &b) in bytes.iter().enumerate() {
        if b == 0x1b {
            let rest = &bytes[i..];
            let is_move = rest.len() > 2 && rest[1] == b'[' && {
                let end = rest[2..].iter().position(|&c| !(c.is_ascii_digit() || c == b';'));
                end.is_some_and(|e| rest[2 + e] == b'H')
            };
            if is_move && !s.is_empty() && !s.ends_with('\n') {
                s.push('\n');
            }
            s.push_str("\\e");
        } else if b == b'\\' {
            s.push_str("\\\\");
        } else if (0x20..0x7f).contains(&b) {
            s.push(char::from(b));
        } else {
            s.push_str(&format!("\\x{b:02x}"));
        }
    }
    s.push('\n');
    s
}

/// What a profile may send, for `Checker`.
#[derive(Clone, Copy, Debug)]
pub struct Rules {
    pub lines: Lines,
    pub colour: bool,
    pub caps: Caps,
    pub rows: u16,
    pub cols: u16,
}

/// Every sequence, character and cell a stream writes, checked against
/// the profile's rules (docs/design/rescue-tui.md, "Output and
/// profiles"): moves inside the screen, `ESC [ H`, `ESC [ 2 J` and the
/// cursor's `?25` only where the caps say, SGR with 0, 1, 7 and (in
/// colour) 30-37 and 40-47 only, joined only where the caps say, never
/// `38;5`/`48;5`, DEC's `ESC ( 0`/`ESC ( B` only in DEC, no other escape,
/// no control byte, no C1, valid UTF-8 of allowed characters only in
/// UTF-8, line letters only while DEC is on, no write past a line's end
/// and never the screen's last cell.  It keeps its state between feeds,
/// so a stream can be checked a frame at a time.
///
/// It also keeps what the screen would show (`shown`), as a terminal that
/// takes exactly these sequences would, so a test can compare a diffed
/// stream's result with the canvas it was meant to draw.
pub struct Checker {
    pub r: Rules,
    /// Where the next character goes, from 1; None before the first move
    /// and after the last column was written.
    at: Option<(u16, u16)>,
    dec: bool,
    /// Bytes of a UTF-8 character not yet complete.
    utf8: Vec<u8>,
    pub bytes: usize,
    pub cells: usize,
    pub errors: Vec<String>,
    attr: Attr,
    grid: Vec<(char, Attr)>,
}

const SGR_OK_MONO: &[u16] = &[0, 1, 7];

impl Checker {
    pub fn new(r: Rules) -> Checker {
        let n = usize::from(r.rows) * usize::from(r.cols);
        Checker {
            r,
            at: None,
            dec: false,
            utf8: Vec::new(),
            bytes: 0,
            cells: 0,
            errors: Vec::new(),
            attr: Attr::PLAIN,
            grid: vec![(' ', Attr::PLAIN); n],
        }
    }

    /// Something else wrote to the terminal before the stream: every cell
    /// shows `ch` in plain attributes, and DEC line drawing is left on or
    /// off.  The stream must then make the screen right by itself.
    pub fn foreign(&mut self, ch: char, dec: bool) {
        self.grid.iter_mut().for_each(|g| *g = (ch, Attr::PLAIN));
        self.dec = dec;
    }

    /// What the screen shows at (`row`, `col`), from 1: the character
    /// (DEC's line letters as UTF-8's box glyphs) and its attributes.
    pub fn shown(&self, row: u16, col: u16) -> Option<(char, Attr)> {
        let ok = (1..=self.r.rows).contains(&row) && (1..=self.r.cols).contains(&col);
        ok.then(|| self.grid[usize::from(row - 1) * usize::from(self.r.cols) + usize::from(col - 1)])
    }

    fn err(&mut self, at: usize, what: String) {
        if self.errors.len() < 20 {
            self.errors.push(format!("byte {at}: {what}"));
        }
    }

    pub fn feed(&mut self, b: &[u8]) {
        let base = self.bytes;
        self.bytes += b.len();
        let mut i = 0;
        while i < b.len() {
            let c = b[i];
            if !self.utf8.is_empty() && c & 0xc0 != 0x80 {
                self.err(base + i, "a UTF-8 character cut short".into());
                self.utf8.clear();
            }
            if c == 0x1b {
                i += self.sequence(&b[i..], base + i);
                continue;
            }
            if c < 0x20 || c == 0x7f {
                self.err(base + i, format!("control byte {c:#04x}"));
            } else if c < 0x80 {
                let mut ch = char::from(c);
                if self.dec && c != b' ' {
                    match dec_glyph(c) {
                        Some(g) => ch = g.utf8(),
                        None => self.err(base + i, format!("{:?} with DEC line drawing on", char::from(c))),
                    }
                }
                self.cell(base + i, ch);
            } else if self.r.lines != Lines::Utf8 {
                self.err(base + i, format!("byte {c:#04x} in a profile that isn't UTF-8"));
            } else {
                self.utf8.push(c);
                match std::str::from_utf8(&self.utf8) {
                    Ok(s) => {
                        let ch = s.chars().next().unwrap_or('?');
                        self.utf8.clear();
                        if !(text::allowed(ch) || is_box(ch)) {
                            self.err(base + i, format!("character {ch:?} isn't one the screen may show"));
                        }
                        if self.dec {
                            self.err(base + i, "UTF-8 with DEC line drawing on".into());
                        }
                        self.cell(base + i, ch);
                    }
                    Err(e) if e.error_len().is_some() || self.utf8.len() >= 4 => {
                        self.err(base + i, "invalid UTF-8".into());
                        self.utf8.clear();
                    }
                    Err(_) => {}
                }
            }
            i += 1;
        }
    }

    fn cell(&mut self, at: usize, ch: char) {
        self.cells += 1;
        let Some((row, col)) = self.at else {
            self.err(at, "a character with no move before it, or past a line's end".into());
            return;
        };
        if row == self.r.rows && col == self.r.cols {
            self.err(at, "the screen's last cell written (it would scroll)".into());
        }
        let i = usize::from(row - 1) * usize::from(self.r.cols) + usize::from(col - 1);
        self.grid[i] = (ch, self.attr);
        self.at = (col < self.r.cols).then_some((row, col + 1));
    }

    /// One escape sequence at the start of `b`; returns its length.
    fn sequence(&mut self, b: &[u8], at: usize) -> usize {
        if b.len() < 2 {
            self.err(at, "ESC at the end of the stream".into());
            return 1;
        }
        match b[1] {
            b'(' => {
                let n = 3.min(b.len());
                match b.get(2) {
                    Some(b'0') | Some(b'B') if self.r.lines == Lines::Dec => self.dec = b[2] == b'0',
                    _ => self.err(at, format!("{:?} (DEC charsets only in DEC)", String::from_utf8_lossy(&b[..n]))),
                }
                n
            }
            b'[' => {
                let end = b[2..].iter().position(|c| (0x40..=0x7e).contains(c));
                let Some(e) = end else {
                    self.err(at, "an unfinished CSI sequence".into());
                    return b.len();
                };
                let params = &b[2..2 + e];
                let fin = b[2 + e];
                let n = 3 + e;
                let p = String::from_utf8_lossy(params).to_string();
                match fin {
                    b'H' => self.move_to(&p, at),
                    b'J' if p == "2" && self.r.caps.clear => {
                        let a = self.attr;
                        self.grid.iter_mut().for_each(|g| *g = (' ', a));
                    }
                    b'h' | b'l' if p == "?25" && self.r.caps.hide_cursor => {}
                    b'm' => self.sgr(&p, at),
                    _ => self.err(at, format!("ESC [ {p} {} isn't one this profile sends", char::from(fin))),
                }
                n
            }
            other => {
                self.err(at, format!("ESC {:?}", char::from(other)));
                2
            }
        }
    }

    fn move_to(&mut self, p: &str, at: usize) {
        if p.is_empty() {
            self.at = Some((1, 1));
            return;
        }
        let rc: Vec<Option<u16>> = p.split(';').map(|x| x.parse().ok()).collect();
        match rc[..] {
            [Some(r), Some(c)] if (1..=self.r.rows).contains(&r) && (1..=self.r.cols).contains(&c) => {
                self.at = Some((r, c))
            }
            _ => {
                self.err(at, format!("move {p:?} outside {}x{}", self.r.cols, self.r.rows));
                self.at = None;
            }
        }
    }

    fn sgr(&mut self, p: &str, at: usize) {
        let ps: Vec<&str> = p.split(';').collect();
        if ps.len() > 1 && !self.r.caps.joined_sgr {
            self.err(at, format!("SGR {p:?} joined where the profile takes one parameter each"));
        }
        for x in ps {
            let ok = match x.parse::<u16>() {
                Ok(n) if SGR_OK_MONO.contains(&n) => true,
                Ok(n) if self.r.colour && ((30..=37).contains(&n) || (40..=47).contains(&n)) => true,
                _ => false,
            };
            if !ok {
                self.err(at, format!("SGR parameter {x:?} in {p:?}"));
                continue;
            }
            match x.parse::<u8>().unwrap_or(0) {
                0 => self.attr = Attr::PLAIN,
                1 => self.attr.bold = true,
                7 => self.attr.reverse = true,
                n @ 30..=37 => self.attr.fg = Some(colour(n - 30)),
                n => self.attr.bg = Some(colour(n - 40)),
            }
        }
    }

    /// The errors, as one result.
    pub fn result(&self) -> Result<(), String> {
        if self.errors.is_empty() && self.utf8.is_empty() {
            Ok(())
        } else {
            Err(self.errors.join("\n"))
        }
    }
}

fn colour(n: u8) -> Colour {
    use Colour::*;
    [Black, Red, Green, Yellow, Blue, Magenta, Cyan, White][usize::from(n & 7)]
}

const GLYPHS: [Glyph; 10] = {
    use Glyph::*;
    [Horiz, Vert, TopLeft, TopRight, BottomLeft, BottomRight, TeeLeft, TeeRight, TeeTop, TeeBottom]
};

fn dec_glyph(c: u8) -> Option<Glyph> {
    GLYPHS.iter().copied().find(|g| g.dec() == c)
}

fn is_box(c: char) -> bool {
    GLYPHS.iter().any(|g| g.utf8() == c)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn rules(lines: Lines, colour: bool, caps: Caps) -> Rules {
        Rules { lines, colour, caps, rows: 24, cols: 80 }
    }

    fn check(r: Rules, b: &[u8]) -> Result<(), String> {
        let mut c = Checker::new(r);
        c.feed(b);
        c.result()
    }

    #[test]
    fn the_checker_takes_what_out_sends() {
        let r = rules(Lines::Dec, true, Caps::ALL);
        assert_eq!(check(r, b"\x1b[?25l\x1b[2J\x1b[1;1H\x1b[0;37;44mab\x1b(0lqk\x1b(B\x1b[H"), Ok(()));
        let r = rules(Lines::Utf8, false, Caps::NONE);
        assert_eq!(check(r, "\x1b[3;4H\x1b[0m\x1b[7m┌é".as_bytes()), Ok(()));
    }

    #[test]
    fn the_checker_refuses_the_rest() {
        let all = rules(Lines::Utf8, true, Caps::ALL);
        for bad in [
            &b"\x1b[1;1H\x1b[38;5;4m"[..],
            b"\x1b[1;1H\x1b[48;5;1m",
            b"\x1b[1;1H\x1b[91m",
            b"\x1b[1;1H\x1b[K",
            b"\x1b[6n",
            b"\x1b[c",
            b"\x1b7",
            b"\x1b[25;1H",
            b"\x1b[1;81H",
            b"x",
            b"\x1b[1;1H\r",
            b"\x1b[1;1H\xc2\x9b",
            b"\x1b[1;1H\xe4\xb8\xad",
            b"\x1b[1;1H\xc3",
            b"\x1b[24;80Hx",
            b"\x1b[1;80Hxy",
            b"\x1b(0",
        ] {
            assert!(check(all, bad).is_err(), "{:?}", String::from_utf8_lossy(bad));
        }
        let mono = rules(Lines::Ascii, false, Caps::NONE);
        for bad in [&b"\x1b[1;1H\x1b[30m"[..], b"\x1b[0;1m", b"\x1b[2J", b"\x1b[?25l", b"\x1b[1;1H\xc3\xa9"] {
            assert!(check(mono, bad).is_err(), "{:?}", String::from_utf8_lossy(bad));
        }
        let dec = rules(Lines::Dec, true, Caps::ALL);
        assert!(check(dec, b"\x1b(0\x1b[1;1Ha").is_err());
        assert!(check(dec, b"\x1b(0\x1b[1;1Hq\x1b(B").is_ok());
    }

    #[test]
    fn the_checker_keeps_what_the_screen_shows() {
        let mut c = Checker::new(rules(Lines::Dec, true, Caps::ALL));
        c.feed(b"\x1b[2;3H\x1b[0;1;37;44mA\x1b(0q \x1b(B\x1b[0mz");
        assert_eq!(c.result(), Ok(()));
        let bg = Attr { bold: true, ..Attr::colours(Colour::White, Colour::Blue) };
        assert_eq!(c.shown(2, 3), Some(('A', bg)));
        assert_eq!(c.shown(2, 4), Some(('\u{2500}', bg)));
        assert_eq!(c.shown(2, 5), Some((' ', bg)));
        assert_eq!(c.shown(2, 6), Some(('z', Attr::PLAIN)));
        assert_eq!(c.shown(1, 1), Some((' ', Attr::PLAIN)));
        assert_eq!(c.shown(25, 1), None);
    }

    #[test]
    fn escape_breaks_lines_at_moves() {
        assert_eq!(escape(b"\x1b[0m\x1b[1;1Hab\\\x1b[2;1H\xe2\x94\x80"), "\\e[0m\n\\e[1;1Hab\\\\\n\\e[2;1H\\xe2\\x94\\x80\n");
    }

    #[test]
    fn rng_is_deterministic() {
        let (mut a, mut b) = (Rng::new(7), Rng::new(7));
        assert!((0..100).all(|_| a.next_u64() == b.next_u64()));
        assert!((0..1000).all(|_| a.below(10) < 10));
        assert_eq!(a.below(0), 0);
    }

    #[test]
    fn line_diff_numbers_lines() {
        assert_eq!(line_diff("a\nb\n", "a\nc\nd\n"), "   2 - b\n   2 + c\n   3 + d\n");
    }
}
