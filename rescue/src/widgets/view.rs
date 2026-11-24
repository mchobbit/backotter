//! The text view: lines that never wrap (the command plan, the log, raw
//! output, the report).  A long line is cut with `>` at the right edge,
//! and `<` at the left once scrolled sideways.  Up and Down move a line,
//! Space a page down, Backspace a page back, Left and Right 8 columns.
//! `^ more above` and `v more below` take the first or last row only
//! when there is more that way.

use crate::screen::{Canvas, DIALOG};
use crate::term::keys::Key;
use crate::text::{Clean, CUT};

use super::{TEXT_COL, TEXT_W};

/// Columns Left and Right move.
pub const SIDEWAYS: usize = 8;

pub struct View {
    lines: Vec<Clean>,
    top: usize,
    left: usize,
    /// Rows shown, set by the dialog's layout.
    pub window: u16,
}

impl View {
    /// Newlines inside a line start lines of their own.
    pub fn new(lines: Vec<Clean>) -> View {
        let lines = lines.iter().flat_map(|l| l.lines()).collect();
        View { lines, top: 0, left: 0, window: 1 }
    }

    pub fn len(&self) -> usize {
        self.lines.len()
    }

    pub fn is_empty(&self) -> bool {
        self.lines.is_empty()
    }

    /// The first line shown, and how far scrolled sideways.
    pub fn at(&self) -> (usize, usize) {
        (self.top, self.left)
    }

    /// The lines that fit at `top`, and the two markers.
    fn shown(&self, top: usize) -> (usize, bool, bool) {
        let w = usize::from(self.window).max(1);
        let n = self.lines.len();
        if n <= w {
            return (n, false, false);
        }
        let above = top > 0 && w > 1;
        let mut rows = w - usize::from(above);
        let below = top + rows < n && rows > 1;
        if below {
            rows -= 1;
        }
        (rows, above, below)
    }

    /// The last `top` that still fills the window.
    fn max_top(&self) -> usize {
        let w = usize::from(self.window).max(1);
        let n = self.lines.len();
        if n <= w {
            return 0;
        }
        // At the end only the "more above" marker shows.
        n - (w - usize::from(w > 1))
    }

    fn widest(&self) -> usize {
        self.lines.iter().map(Clean::width).max().unwrap_or(0)
    }

    /// True when the view moved.
    pub fn key(&mut self, k: Key) -> bool {
        let (top, left) = (self.top, self.left);
        let page = self.shown(self.top).0.saturating_sub(1).max(1);
        match k {
            Key::Up => self.top = self.top.saturating_sub(1),
            Key::Down => self.top = (self.top + 1).min(self.max_top()),
            Key::Space => self.top = (self.top + page).min(self.max_top()),
            Key::Backspace => self.top = self.top.saturating_sub(page),
            Key::Left => self.left = self.left.saturating_sub(SIDEWAYS),
            Key::Right => {
                let most = self.widest().saturating_sub(usize::from(TEXT_W) - 1);
                self.left = (self.left + SIDEWAYS).min(most.next_multiple_of(SIDEWAYS));
            }
            _ => {}
        }
        (top, left) != (self.top, self.left)
    }

    pub fn draw(&self, c: &mut Canvas, row: u16, focused: bool) {
        let (rows, above, below) = self.shown(self.top);
        let w = usize::from(TEXT_W);
        let mut r = row;
        if above {
            c.text_in(r, TEXT_COL, TEXT_W, &Clean::ours("^ more above"), DIALOG);
            r += 1;
        }
        for l in self.lines.iter().skip(self.top).take(rows) {
            let rest: String = l.chars().skip(self.left).collect();
            let mut s: String = if self.left > 0 && l.width() > self.left {
                std::iter::once('<').chain(rest.chars().skip(1)).collect()
            } else {
                rest
            };
            if s.chars().count() > w {
                s = s.chars().take(w - 1).chain(std::iter::once(CUT)).collect();
            }
            // Characters of a Clean, cut: still clean.
            c.text_in(r, TEXT_COL, TEXT_W, &Clean::line(&s, w), DIALOG);
            r += 1;
        }
        if below {
            c.text_in(r, TEXT_COL, TEXT_W, &Clean::ours("v more below"), DIALOG);
        }
        if focused {
            c.cursor = Some((row, TEXT_COL));
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::term::out::Lines;

    fn view(n: usize, window: u16) -> View {
        let mut v = View::new((0..n).map(|i| Clean::ours(&format!("line {i}"))).collect());
        v.window = window;
        v
    }

    fn shown(v: &View) -> Vec<String> {
        let mut c = Canvas::new(v.window, 80, DIALOG);
        v.draw(&mut c, 0, true);
        c.dump(Lines::Ascii, false).lines().take(usize::from(v.window)).map(|l| l[4..].trim_end().to_string()).collect()
    }

    #[test]
    fn lines_pages_and_the_ends() {
        let mut v = view(20, 5);
        assert_eq!(shown(&v), ["line 0", "line 1", "line 2", "line 3", "v more below"]);
        assert!(!v.key(Key::Up), "already at the top");
        v.key(Key::Down);
        assert_eq!(shown(&v), ["^ more above", "line 1", "line 2", "line 3", "v more below"]);
        v.key(Key::Space);
        assert_eq!(v.at().0, 3, "a page is the rows shown less one");
        for _ in 0..30 {
            v.key(Key::Down);
        }
        assert_eq!(shown(&v), ["^ more above", "line 16", "line 17", "line 18", "line 19"]);
        assert!(!v.key(Key::Space), "already at the end");
        v.key(Key::Backspace);
        assert_eq!(v.at().0, 13);
    }

    #[test]
    fn short_text_doesnt_scroll() {
        let mut v = view(3, 5);
        assert!(!v.key(Key::Down) && !v.key(Key::Space));
        assert_eq!(shown(&v)[..3], ["line 0", "line 1", "line 2"]);
    }

    #[test]
    fn long_lines_are_cut_and_scroll_sideways() {
        let long = format!("start{}end", "x".repeat(100));
        let mut v = View::new(vec![Clean::ours(&long), Clean::ours("short")]);
        v.window = 2;
        let s = shown(&v);
        assert_eq!(s[0].chars().count(), 72);
        assert!(s[0].starts_with("start") && s[0].ends_with('>'), "{}", s[0]);
        for _ in 0..20 {
            v.key(Key::Right);
        }
        let s = shown(&v);
        assert!(s[0].starts_with('<') && s[0].ends_with("end"), "{}", s[0]);
        assert_eq!(s[1], "", "a short line scrolled past is empty");
        assert!(v.key(Key::Left));
    }

    #[test]
    fn newlines_inside_a_line_split_it() {
        let v = View::new(vec![Clean::text("a\nb", 10)]);
        assert_eq!(v.len(), 2);
    }
}
