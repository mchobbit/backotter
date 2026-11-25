//! Review's disks, each with a field for its confirmation
//! (rescue-screens.md, "8. Review"): rows in their own frame, as a list's,
//! the field at each row's end, `[F3A9C1]`.  What is typed is shown as
//! it is (a serial isn't a secret); typing and Backspace only (D9).
//!
//! - Letters type into the field with the focus, so a serial with a C in
//!   it is typed, never taken for `c`.
//! - Up and Down move between the fields; Enter and Tab go to the next
//!   one, and from the last the dialog takes the focus on (to the first
//!   button for Enter, as Review's Restore).
//! - More disks than rows scroll, with the list's markers.

use crate::screen::{Canvas, DIALOG};
use crate::term::keys::Key;
use crate::text::{cap, Clean};

use super::list::{list_window, FRAME_COL, FRAME_W, ROW_COL, ROW_W};

/// The most characters a field takes.
pub const VALUE_MAX: usize = 32;

pub struct Serials {
    rows: Vec<Clean>,
    widths: Vec<u16>,
    pub values: Vec<String>,
    /// The field with the focus.
    pub cur: usize,
    top: usize,
    /// Rows shown, set by the dialog's layout.
    pub window: u16,
}

impl Serials {
    pub fn new(rows: &[(Clean, u16)], values: &[String]) -> Serials {
        let values = (0..rows.len()).map(|i| values.get(i).map(|v| v.chars().filter(|c| !c.is_control()).take(VALUE_MAX).collect()).unwrap_or_default()).collect();
        Serials {
            rows: rows.iter().map(|(t, _)| t.clone()).collect(),
            widths: rows.iter().map(|(_, w)| (*w).clamp(3, 24)).collect(),
            values,
            cur: 0,
            top: 0,
            window: 1,
        }
    }

    pub fn len(&self) -> usize {
        self.rows.len()
    }

    pub fn is_empty(&self) -> bool {
        self.rows.is_empty()
    }

    /// Put the focus on field `i`.
    pub fn focus(&mut self, i: usize) {
        if i < self.rows.len() {
            self.cur = i;
            self.top = list_window(self.rows.len(), usize::from(self.window), i, self.top).0;
        }
    }

    /// The next field; false at the last.
    pub fn advance(&mut self) -> bool {
        if self.cur + 1 < self.rows.len() {
            self.focus(self.cur + 1);
            true
        } else {
            false
        }
    }

    /// A character, Space, Backspace, Up or Down; true when something
    /// changed.
    pub fn key(&mut self, k: Key) -> bool {
        let Some(v) = self.values.get_mut(self.cur) else { return false };
        match k {
            Key::Char(c) if !c.is_control() => {
                if v.chars().count() >= VALUE_MAX {
                    return false;
                }
                v.push(c);
                true
            }
            Key::Space => {
                if v.chars().count() >= VALUE_MAX {
                    return false;
                }
                v.push(' ');
                true
            }
            Key::Backspace => v.pop().is_some(),
            Key::Up if self.cur > 0 => {
                self.focus(self.cur - 1);
                true
            }
            Key::Down => self.advance(),
            _ => false,
        }
    }

    /// The frame at `row`, the rows inside it.
    pub fn draw(&self, c: &mut Canvas, row: u16, focused: bool) {
        c.frame(row, FRAME_COL, self.window + 2, FRAME_W, DIALOG);
        let (first, count, above, below) = list_window(self.rows.len(), usize::from(self.window), self.cur, self.top);
        let mut r = row + 1;
        if above {
            c.text_in(r, ROW_COL, ROW_W, &Clean::ours("^ more above"), DIALOG);
            r += 1;
        }
        for i in (first..self.rows.len()).take(count) {
            let w = self.widths[i];
            // `[`, the field, `]`, one cell before the row's end.
            let right = ROW_COL + ROW_W - 2;
            let left = right - w - 1;
            c.text_in(r, ROW_COL, left - ROW_COL - 1, &self.rows[i], DIALOG);
            c.text(r, left, &Clean::ours("["), DIALOG);
            c.text(r, right, &Clean::ours("]"), DIALOG);
            let here = focused && self.cur == i;
            let a = if here { DIALOG.focus() } else { DIALOG };
            c.fill(r, left + 1, 1, w, a);
            let shown = Clean::line(&self.values[i], cap::LINE);
            // Its end shows when it is longer than the field.
            let room = usize::from(w) - usize::from(here);
            let shown = if shown.width() > room {
                let tail: String = shown.chars().skip(shown.width() + 1 - room).collect();
                Clean::ours("<").join("", &Clean::line(&tail, room))
            } else {
                shown
            };
            let at = c.text(r, left + 1, &shown, a);
            if here {
                c.fill(r, left + 1 + at, 1, 1, DIALOG);
                c.cursor = Some((r, left + 1 + at));
            }
            r += 1;
        }
        if below {
            c.text_in(r, ROW_COL, ROW_W, &Clean::ours("v more below"), DIALOG);
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::term::out::Lines;

    fn o(s: &str) -> Clean {
        Clean::ours(s)
    }

    #[test]
    fn typed_into_the_field_with_the_focus_and_scrolled() {
        let rows: Vec<(Clean, u16)> = (0..5).map(|i| (o(&format!("ERASED  1.00 TB  disk{i}")), 6)).collect();
        let mut s = Serials::new(&rows, &["a3".to_string()]);
        s.window = 3;
        assert_eq!(s.values, vec!["a3", "", "", "", ""]);
        for c in "cF".chars() {
            assert!(s.key(Key::Char(c)));
        }
        assert!(s.key(Key::Backspace));
        assert_eq!(s.values[0], "a3c");
        assert!(s.key(Key::Down) && s.key(Key::Char('x')));
        assert_eq!(s.values[1], "x");
        assert!(s.advance() && s.advance() && s.advance());
        assert!(!s.advance(), "the last");
        assert!(!s.key(Key::Down));
        let mut c = Canvas::new(6, 80, DIALOG);
        s.draw(&mut c, 0, true);
        let d = c.dump(Lines::Ascii, true);
        assert!(d.contains("^ more above") && d.contains("disk4") && d.contains("disk3"), "{d}");
        let line = d.lines().find(|l| l.contains("disk0") || l.contains("disk3")).unwrap_or("");
        assert!(line.contains("] |") || line.contains("]  |"), "the field at the row's end: {line:?}");
        assert_eq!(c.cursor.map(|(r, _)| r), Some(3));
    }
}
