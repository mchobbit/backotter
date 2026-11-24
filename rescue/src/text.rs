//! Every string from outside goes through here before it can be shown:
//! bko-holt's output, rustic-bko's, the config partition, the disks'
//! models, serials and labels, hostnames, paths, log lines and errors.
//! `Clean` is the only string type the screen takes, so an unsanitised
//! string can't reach a terminal (docs/design/rescue-tui.md, "Security").
//!
//! - C0 controls and DEL become `?`; a tab becomes a space.  A newline
//!   survives only in `Clean::text`, which a widget splits or wraps.
//! - C1 controls (U+0080-U+009F) become `?`: a terminal not in UTF-8 mode
//!   reads 0x9B as CSI.
//! - Non-ASCII is kept only from a table of width-1 letters (Latin-1,
//!   Latin Extended-A and -B, Greek, Cyrillic, less combining marks and
//!   unassigned code points).  Everything else becomes `?`: bidi controls,
//!   zero-width characters, wide characters, emoji, private use.
//! - In the DEC and ASCII line styles every non-ASCII character is shown
//!   as `?`.  That happens at render (`shown`), so a change of profile
//!   needs no cleaning again.
//!
//! So every character of a `Clean` is one cell wide, and its width is its
//! length in characters.

use crate::term::out::Lines;

/// The caps on fields from outside, in characters, applied before
/// wrapping.  A longer string is cut, its last character `>`.
pub mod cap {
    pub const HOSTNAME: usize = 64;
    pub const MODEL: usize = 48;
    pub const SERIAL: usize = 48;
    /// A problem's text.
    pub const PROBLEM: usize = 2048;
    pub const LOG_LINE: usize = 512;
    /// Anything else on one line: a label, a path, a snapshot's tag.
    pub const LINE: usize = 256;
}

/// The mark at the end of a string cut short, by a cap or to fit.
pub const CUT: char = '>';

/// Whether `c` may stay in a `Clean`: printable ASCII, or a width-1
/// letter from the table.
pub fn allowed(c: char) -> bool {
    match c as u32 {
        0x20..=0x7e => true,
        // Latin-1's letters and signs; the soft hyphen is width 0 on some
        // consoles.
        0xa0..=0xff => c != '\u{ad}',
        // Latin Extended-A and -B.
        0x100..=0x24f => true,
        // Greek, less its unassigned code points.
        0x370..=0x3ff => !matches!(c as u32, 0x378 | 0x379 | 0x380..=0x383 | 0x38b | 0x38d | 0x3a2),
        // Cyrillic, less its combining marks (U+0483-U+0489).
        0x400..=0x482 | 0x48a..=0x4ff => true,
        _ => false,
    }
}

/// A character as a profile shows it: what `allowed` keeps in UTF-8, only
/// printable ASCII in DEC and ASCII; anything else is `?`.
pub fn shown(c: char, lines: Lines) -> char {
    if c.is_ascii() {
        return if (' '..='~').contains(&c) { c } else { '?' };
    }
    if lines == Lines::Utf8 && allowed(c) {
        c
    } else {
        '?'
    }
}

/// A string from outside (argv, the environment, a tty's name) as any
/// console may print it: printable ASCII only, no cap.  For messages
/// outside the screen (stderr, `--profile`).
pub fn ascii(s: &str) -> String {
    s.chars().map(|c| shown(c, Lines::Ascii)).collect()
}

/// A sanitised string: every character is `allowed` (one cell wide), or a
/// newline in one made by `Clean::text`.
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct Clean(String);

impl Clean {
    /// One line: a newline becomes `?` like any control character.
    pub fn line(s: &str, cap: usize) -> Clean {
        Clean::make(s, cap, false)
    }

    /// Text that a widget wraps or splits: newlines kept, `\r\n` read as
    /// one newline.
    pub fn text(s: &str, cap: usize) -> Clean {
        Clean::make(s, cap, true)
    }

    /// One of our own strings (later `app/strings.rs`), which never needs
    /// cleaning: debug builds and the tests check it comes out unchanged.
    pub fn ours(s: &str) -> Clean {
        let c = Clean::text(s, usize::MAX);
        debug_assert_eq!(c.0, s, "our own string isn't clean");
        c
    }

    fn make(s: &str, cap: usize, newlines: bool) -> Clean {
        let mut out = String::with_capacity(s.len().min(cap.saturating_mul(2)).min(64 * 1024));
        let mut n = 0;
        let mut it = s.chars().peekable();
        while let Some(c) = it.next() {
            if c == '\r' && newlines && it.peek() == Some(&'\n') {
                continue;
            }
            if n == cap {
                // There is more: the last kept character becomes the mark.
                if out.pop().is_some() {
                    out.push(CUT);
                }
                break;
            }
            out.push(match c {
                '\t' => ' ',
                '\n' if newlines => '\n',
                c if allowed(c) => c,
                _ => '?',
            });
            n += 1;
        }
        Clean(out)
    }

    pub fn as_str(&self) -> &str {
        &self.0
    }

    pub fn is_empty(&self) -> bool {
        self.0.is_empty()
    }

    /// Cells it takes on one line: its length in characters (newlines
    /// counted as one each, though a widget splits on them first).
    pub fn width(&self) -> usize {
        self.0.chars().count()
    }

    pub fn chars(&self) -> std::str::Chars<'_> {
        self.0.chars()
    }

    /// The lines of a `Clean::text`, each with no newline.
    pub fn lines(&self) -> Vec<Clean> {
        self.0.split('\n').map(|l| Clean(l.to_string())).collect()
    }

    /// At most `w` cells: a longer string keeps `w - 1` characters and
    /// ends in `>`.  A newline counts as a cell, as in `width`.
    pub fn truncate(&self, w: usize) -> Clean {
        if self.width() <= w {
            return self.clone();
        }
        if w == 0 {
            return Clean::default();
        }
        let mut s: String = self.0.chars().take(w - 1).collect();
        s.push(CUT);
        Clean(s)
    }

    /// Joined with another, as one string.
    pub fn join(&self, sep: &str, other: &Clean) -> Clean {
        let mut s = self.0.clone();
        s.push_str(Clean::ours(sep).as_str());
        s.push_str(&other.0);
        Clean(s)
    }
}

/// Wrap at word boundaries to `width` cells.  Each newline starts a new
/// paragraph, and an empty one is an empty line.  A paragraph's leading
/// spaces indent its first line.  The spaces between two words on one
/// line are kept as they are (two after a full stop stay two); at a break
/// they go.  A word longer than the width goes on lines of its own, cut
/// every `width` cells: a serial or a path is shown whole, if broken.
pub fn wrap(t: &Clean, width: usize) -> Vec<Clean> {
    let width = width.max(1);
    let mut lines = Vec::new();
    for para in t.as_str().split('\n') {
        let mut cur = String::new();
        let mut cur_w = 0;
        let mut started = false;
        let mut spaces = 0;
        // Each word with the spaces before it; the leading ones indent.
        let mut words: Vec<(usize, &str)> = Vec::new();
        for piece in para.split(' ') {
            if piece.is_empty() {
                spaces += 1;
            } else {
                words.push((spaces + usize::from(!words.is_empty()), piece));
                spaces = 0;
            }
        }
        for (i, (sp, word)) in words.into_iter().enumerate() {
            let ww = word.chars().count();
            // The indent counts only if the word fits after it.
            let sep = if started || (i == 0 && sp + ww <= width) { sp } else { 0 };
            if cur_w + sep + ww <= width {
                cur.extend(std::iter::repeat(' ').take(sep));
                cur.push_str(word);
                cur_w += sep + ww;
                started = true;
                continue;
            }
            // It doesn't fit: the line so far ends, the word starts the next.
            if started {
                lines.push(Clean(std::mem::take(&mut cur)));
            }
            let mut chars: Vec<char> = word.chars().collect();
            while chars.len() > width {
                lines.push(Clean(chars.drain(..width).collect()));
            }
            cur_w = chars.len();
            cur = chars.into_iter().collect();
            started = true;
        }
        lines.push(Clean(cur));
    }
    lines
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::testkit::{fuzz_cases, Rng};

    fn l(s: &str) -> Clean {
        Clean::line(s, cap::LINE)
    }

    #[test]
    fn controls_and_escapes_become_question_marks() {
        assert_eq!(l("a\x1b[31mb\u{9b}1m\x07\x7f\n").as_str(), "a?[31mb?1m???");
        assert_eq!(l("tab\there").as_str(), "tab here");
        assert_eq!(Clean::text("one\r\ntwo\nthree\r", cap::LINE).as_str(), "one\ntwo\nthree?");
        assert_eq!(l("\u{202e}evil\u{200b}\u{feff}").as_str(), "?evil??");
    }

    #[test]
    fn the_width_one_table() {
        assert_eq!(l("Łódź Ελλάδα Москва é ñ ß").as_str(), "Łódź Ελλάδα Москва é ñ ß");
        // Wide, emoji, combining, private use, soft hyphen, unassigned Greek.
        assert_eq!(l("中😀e\u{301}\u{e000}\u{ad}\u{378}\u{483}").as_str(), "??e?????");
        for c in ['\u{a0}', '\u{ff}', '\u{24f}', '\u{3c9}', '\u{4ff}'] {
            assert!(allowed(c), "{c:?}");
        }
        for c in ['\u{80}', '\u{9f}', '\u{250}', '\u{300}', '\u{36f}', '\u{500}', '\u{2500}'] {
            assert!(!allowed(c), "{c:?}");
        }
    }

    #[test]
    fn shown_per_line_style() {
        let s = l("é-x");
        let out = |lines| s.chars().map(|c| shown(c, lines)).collect::<String>();
        assert_eq!(out(Lines::Utf8), "é-x");
        assert_eq!(out(Lines::Dec), "?-x");
        assert_eq!(out(Lines::Ascii), "?-x");
        assert_eq!(ascii("pts/3\x1b]0;x\x07é"), "pts/3?]0;x??");
    }

    #[test]
    fn caps_cut_with_the_mark() {
        assert_eq!(Clean::line("abcdef", 4).as_str(), "abc>");
        assert_eq!(Clean::line("abcd", 4).as_str(), "abcd");
        assert_eq!(Clean::line("abc", 0).as_str(), "");
        let host = "h".repeat(100);
        assert_eq!(Clean::line(&host, cap::HOSTNAME).width(), cap::HOSTNAME);
        // CR LF counts once against the cap.
        assert_eq!(Clean::text("ab\r\ncd", 5).as_str(), "ab\ncd");
    }

    #[test]
    fn truncate_and_lines() {
        let s = l("/dev/disk/by-id/ata-SAMSUNG");
        assert_eq!(s.truncate(10).as_str(), "/dev/disk>");
        assert_eq!(s.truncate(100), s);
        assert_eq!(s.truncate(1).as_str(), ">");
        assert_eq!(s.truncate(0).as_str(), "");
        let t = Clean::text("a\n\nb", 10);
        assert_eq!(t.lines().iter().map(Clean::as_str).collect::<Vec<_>>(), ["a", "", "b"]);
    }

    fn w(s: &str, width: usize) -> Vec<String> {
        wrap(&Clean::text(s, cap::PROBLEM), width).iter().map(|c| c.as_str().to_string()).collect()
    }

    #[test]
    fn wrap_at_words() {
        assert_eq!(w("the quick brown fox jumps", 10), ["the quick", "brown fox", "jumps"]);
        assert_eq!(w("a  b   c", 10), ["a  b   c"]);
        assert_eq!(w("One.  Two.  Three.", 11), ["One.  Two.", "Three."]);
        assert_eq!(w("trailing   ", 20), ["trailing"]);
        assert_eq!(w("one\n\ntwo", 10), ["one", "", "two"]);
        assert_eq!(w("", 10), [""]);
        assert_eq!(w("  1) lovelace newest", 13), ["  1) lovelace", "newest"]);
        assert_eq!(w("exactly10c", 10), ["exactly10c"]);
    }

    #[test]
    fn a_long_word_goes_on_lines_of_its_own() {
        assert_eq!(w("serial ABCDEFGHIJKLMNOPQRSTUVWXYZ end", 10), ["serial", "ABCDEFGHIJ", "KLMNOPQRST", "UVWXYZ end"]);
        assert_eq!(w("ABCDEFGHIJ", 5), ["ABCDE", "FGHIJ"]);
    }

    #[test]
    fn our_strings_pass_unchanged() {
        for s in ["BackOtter rescue", "<  OK  >", "^ more above", "Tab: move | Enter: choose", "a\nb"] {
            assert_eq!(Clean::ours(s).as_str(), s);
        }
    }

    /// A random string of the kinds of characters that matter: ASCII, C0,
    /// C1, DEL, ESC sequences, bidi and zero-width, combining, wide and
    /// emoji, the width-1 table's letters, and their neighbours.
    pub(crate) fn hostile(r: &mut Rng, max: usize) -> String {
        const PICKS: &[&str] = &[
            "\x1b[2J", "\x1b]0;title\x07", "\x1bP", "\u{9b}31m", "\r\n", "\n", "\t", "\x7f", "\u{202e}",
            "\u{200b}", "\u{feff}", "e\u{301}", "中", "😀", "\u{e000}", "\u{ad}", "Łódź", "Ελλάδα",
            "Москва", "\u{483}", "\u{378}", "\u{10ffff}", "\u{2500}",
        ];
        let n = r.below(max as u64 + 1) as usize;
        let mut s = String::new();
        for _ in 0..n {
            match r.below(4) {
                0 => s.push_str(PICKS[r.below(PICKS.len() as u64) as usize]),
                1 => s.push(char::from(r.below(0x80) as u8)),
                2 => s.push(char::from_u32(r.below(0x600) as u32).unwrap_or('x')),
                _ => s.push(char::from_u32(r.below(0x11_0000) as u32).unwrap_or('y')),
            }
        }
        s
    }

    /// Fuzz-style (BKO_RESCUE_FUZZ=N for more): no control character or
    /// escape comes out, the width is the length, the caps hold, cleaning
    /// is idempotent, and each profile shows only what it may.
    #[test]
    fn random_strings() {
        let mut r = Rng::new(0x5eed_c1ea);
        for case in 0..fuzz_cases(100_000) {
            let s = hostile(&mut r, 24);
            let cap = 1 + r.below(40) as usize;
            let text = r.below(2) == 0;
            let c = if text { Clean::text(&s, cap) } else { Clean::line(&s, cap) };
            assert!(c.width() <= cap, "case {case}: {s:?} cap {cap}: {c:?}");
            for ch in c.chars() {
                assert!(allowed(ch) || (text && ch == '\n'), "case {case}: {s:?} gave {ch:?}");
            }
            assert_eq!(c.width(), c.as_str().chars().count());
            let again = if text { Clean::text(c.as_str(), cap) } else { Clean::line(c.as_str(), cap) };
            assert_eq!(again, c, "case {case}: not idempotent");
            for lines in [Lines::Utf8, Lines::Dec, Lines::Ascii] {
                for ch in c.chars().filter(|&ch| ch != '\n') {
                    let sh = shown(ch, lines);
                    assert!(!sh.is_control());
                    assert!(lines == Lines::Utf8 || sh.is_ascii());
                }
            }
            let width = 1 + r.below(30) as usize;
            for line in wrap(&c, width) {
                assert!(line.width() <= width, "case {case}: {line:?} wider than {width}");
                assert!(!line.as_str().contains('\n'));
            }
        }
    }
}
