//! The menu and the radio list: rows in their own frame (66 columns of
//! text), scrolling with the focus, with `^ more above` and `v more
//! below` as the first or last row only when there is more that way.
//!
//! In the radio list the choice follows the focus: the focused row is
//! the one marked `(*)`, so the row seen is always the row chosen
//! (rescue-screens.md, "Conventions").  Space does nothing.  A menu's
//! row with the focus is the one Enter picks; when the focus is on the
//! buttons it stays in bold.
//!
//! An item may take several rows (a newline in it): the Disks screens'
//! old disks and candidates.  It scrolls whole, and the focus marks all
//! its rows; a radio list's `(*)` is on its first, the rest indented.

use crate::screen::{Canvas, DIALOG};
use crate::term::keys::Key;
use crate::text::Clean;

use super::{NOTE, TEXT_COL};

/// The list's frame: column 6 (from 1), 70 wide; text at column 8, 66.
pub const FRAME_COL: u16 = TEXT_COL + 1;
pub const FRAME_W: u16 = 70;
pub const ROW_COL: u16 = FRAME_COL + 2;
pub const ROW_W: u16 = 66;

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

/// `list_window` for items of HS rows each: which items show in `h` rows
/// with `sel` selected, `top` the first shown before.  An item never shows
/// in part, unless it alone is taller than the window.
pub fn items_window(hs: &[usize], h: usize, sel: usize, top: usize) -> (usize, usize, bool, bool) {
    let n = hs.len();
    if hs.iter().sum::<usize>() <= h {
        return (0, n, false, false);
    }
    let h = h.max(3);
    let mut top = top.min(sel);
    loop {
        let above = top > 0;
        let room = h - usize::from(above);
        let (mut count, mut used) = (0, 0);
        while top + count < n && used + hs[top + count] <= room {
            used += hs[top + count];
            count += 1;
        }
        let below = top + count < n;
        if below {
            // A row for the marker.
            while count > 0 && used + 1 > room {
                count -= 1;
                used -= hs[top + count];
            }
        }
        if count == 0 {
            // Taller than the window: shown cut.
            return (top, 1, above, top + 1 < n);
        }
        if sel >= top + count {
            top += 1;
            continue;
        }
        return (top, count, above, below);
    }
}

pub struct List {
    /// Each item's rows.
    rows: Vec<Vec<Clean>>,
    pub radio: bool,
    /// The row with the focus; None in a radio list with nothing chosen.
    pub cur: Option<usize>,
    top: usize,
    /// Rows shown, set by the dialog's layout.
    pub window: u16,
}

impl List {
    /// Tags lined up, two spaces after the widest.
    pub fn menu(items: &[(Clean, Clean)], start: usize) -> List {
        let w = items.iter().map(|(t, _)| t.width()).max().unwrap_or(0);
        let rows = items
            .iter()
            .map(|(t, d)| {
                if d.is_empty() {
                    t.lines()
                } else {
                    vec![t.join(&" ".repeat(w - t.width() + 2), d)]
                }
            })
            .collect();
        let cur = (!items.is_empty()).then(|| start.min(items.len() - 1));
        List { rows, radio: false, cur, top: 0, window: 1 }
    }

    pub fn radio(items: &[Clean], chosen: Option<usize>) -> List {
        let cur = chosen.filter(|&c| c < items.len());
        List { rows: items.iter().map(Clean::lines).collect(), radio: true, cur, top: 0, window: 1 }
    }

    pub fn len(&self) -> usize {
        self.rows.len()
    }

    /// Rows all the items take.
    pub fn lines_len(&self) -> usize {
        self.rows.iter().map(Vec::len).sum()
    }

    fn heights(&self) -> Vec<usize> {
        self.rows.iter().map(Vec::len).collect()
    }

    pub fn is_empty(&self) -> bool {
        self.rows.is_empty()
    }

    /// The first row shown: it changes only when the list scrolls.
    pub fn top(&self) -> usize {
        self.top
    }

    /// Up and Down; true when the focus moved.  With nothing chosen
    /// either arrow lands on the first row.
    pub fn key(&mut self, k: Key) -> bool {
        let n = self.rows.len();
        if n == 0 {
            return false;
        }
        let next = match (self.cur, k) {
            (None, Key::Up | Key::Down) => 0,
            (Some(c), Key::Up) => c.saturating_sub(1),
            (Some(c), Key::Down) => (c + 1).min(n - 1),
            _ => return false,
        };
        let moved = self.cur != Some(next);
        self.cur = Some(next);
        self.top = items_window(&self.heights(), usize::from(self.window), next, self.top).0;
        moved
    }

    /// The frame at `row`, the rows inside it.
    pub fn draw(&self, c: &mut Canvas, row: u16, focused: bool) {
        c.frame(row, FRAME_COL, self.window + 2, FRAME_W, DIALOG);
        let sel = self.cur.unwrap_or(self.top);
        let (first, count, above, below) = items_window(&self.heights(), usize::from(self.window), sel, self.top);
        let mut r = row + 1;
        let end = row + 1 + self.window;
        if above {
            c.text_in(r, ROW_COL, ROW_W, &Clean::ours("^ more above"), DIALOG);
            r += 1;
        }
        for (i, item) in self.rows.iter().enumerate().skip(first).take(count) {
            let here = self.cur == Some(i);
            let a = match (here, focused) {
                (true, true) => DIALOG.focus(),
                (true, false) if !self.radio => NOTE,
                _ => DIALOG,
            };
            for (k, line) in item.iter().enumerate() {
                if r >= end - u16::from(below) {
                    break;
                }
                let mark = match (self.radio, k, here) {
                    (false, _, _) => "",
                    (true, 0, true) => "(*) ",
                    (true, 0, false) => "( ) ",
                    (true, _, _) => "    ",
                };
                let t = Clean::ours(mark).join("", line);
                c.text_in(r, ROW_COL, ROW_W, &t, a);
                if here && focused && k == 0 {
                    c.cursor = Some((r, ROW_COL));
                }
                r += 1;
            }
        }
        if below {
            r = end - 1;
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
    fn list_window_scrolls_with_markers() {
        assert_eq!(list_window(5, 3, 0, 0), (0, 2, false, true));
        assert_eq!(list_window(5, 3, 2, 0), (2, 1, true, true));
        assert_eq!(list_window(5, 3, 4, 3), (3, 2, true, false));
        assert_eq!(list_window(3, 3, 2, 0), (0, 3, false, false));
    }

    #[test]
    fn items_window_is_list_window_for_rows_of_one() {
        for n in 0..12 {
            for h in 1..8 {
                for sel in 0..n {
                    for top in 0..n {
                        // list_window may count rows past the last item.
                        let (f, c, a, b) = list_window(n, h, sel, top);
                        assert_eq!(items_window(&vec![1; n], h, sel, top), (f, c.min(n - f), a, b), "n {n} h {h} sel {sel} top {top}");
                    }
                }
            }
        }
        // Items of three rows: whole ones only, the marker's row kept.
        assert_eq!(items_window(&[3, 3, 3], 7, 0, 0), (0, 2, false, true));
        assert_eq!(items_window(&[3, 3, 3], 7, 2, 0), (1, 2, true, false));
        assert_eq!(items_window(&[3, 3, 3], 9, 2, 0), (0, 3, false, false));
    }

    #[test]
    fn an_item_of_several_rows_is_drawn_whole() {
        let mut l = List::radio(&[o("disk a\n  blank"), o("disk b\n  has data")], Some(1));
        l.window = 4;
        let mut c = Canvas::new(8, 80, DIALOG);
        l.draw(&mut c, 0, true);
        let d = c.dump(Lines::Ascii, true);
        assert!(d.contains("( ) disk a") && d.contains("      blank") && d.contains("(*) disk b") && d.contains("      has data"), "{d}");
        assert_eq!(c.cursor, Some((3, ROW_COL)), "on the chosen item's first row");
        assert_eq!(l.lines_len(), 4);
    }

    #[test]
    fn menu_tags_line_up() {
        let l = List::menu(&[(o("a"), o("first")), (o("longer"), o("second")), (o("x"), o(""))], 9);
        assert_eq!(l.rows[0][0].as_str(), "a       first");
        assert_eq!(l.rows[1][0].as_str(), "longer  second");
        assert_eq!(l.rows[2][0].as_str(), "x");
        assert_eq!(l.cur, Some(2), "start is kept inside the list");
    }

    #[test]
    fn the_radio_choice_follows_the_focus() {
        let mut l = List::radio(&[o("a"), o("b"), o("c")], None);
        l.window = 3;
        assert_eq!(l.cur, None);
        assert!(!l.key(Key::Space), "Space does nothing");
        assert!(l.key(Key::Up));
        assert_eq!(l.cur, Some(0), "an arrow lands on the first row");
        assert!(l.key(Key::Down) && l.key(Key::Down));
        assert!(!l.key(Key::Down), "stops at the last");
        assert_eq!(l.cur, Some(2));
        let mut c = Canvas::new(8, 80, DIALOG);
        l.draw(&mut c, 0, true);
        let d = c.dump(Lines::Ascii, true);
        assert!(d.contains("( ) a") && d.contains("( ) b") && d.contains("(*) c"), "{d}");
        assert_eq!(c.cursor, Some((3, ROW_COL)));
        // Out of range: nothing chosen.
        assert_eq!(List::radio(&[o("a")], Some(4)).cur, None);
    }

    #[test]
    fn scrolling_keeps_the_focus_in_view() {
        let items: Vec<(Clean, Clean)> = (0..10).map(|i| (o(&format!("r{i}")), o(""))).collect();
        let mut l = List::menu(&items, 0);
        l.window = 4;
        for want in 1..10 {
            l.key(Key::Down);
            let (f, n, _, _) = list_window(10, 4, want, l.top);
            assert!(f <= want && want < f + n);
        }
        let mut c = Canvas::new(8, 80, DIALOG);
        l.draw(&mut c, 0, false);
        let d = c.dump(Lines::Ascii, true);
        assert!(d.contains("^ more above") && !d.contains("v more below") && d.contains("r9"), "{d}");
        // Not focused: the menu's row is bold, the cursor not parked.
        assert_eq!(c.get(4, ROW_COL).map(|x| x.attr), Some(NOTE));
        assert_eq!(c.cursor, None);
    }
}
