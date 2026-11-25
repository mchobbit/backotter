//! Advanced's filesystems (rescue-screens.md, "Advanced: another
//! filesystem"): a list whose rows each cycle their choices with Space,
//! the first being what it was, and under the list the focused row's
//! note (why a type can't be had), wrapped.
//!
//! - Up and Down move the focus; Space takes the row's next choice; a
//!   row of one choice can't change, and says so.
//! - The part's rows: the list's frame, a blank row, NOTE_ROWS for the
//!   note.

use crate::screen::{Canvas, DIALOG};
use crate::term::keys::Key;
use crate::text::{self, Clean};
use crate::ui::CycleRow;

use super::list::{list_window, FRAME_COL, FRAME_W, ROW_COL, ROW_W};
use super::{TEXT_COL, TEXT_W};

/// Rows under the list for the focused row's note.
pub const NOTE_ROWS: u16 = 3;
/// Cells a choice takes before the row's state.
const CHOICE_W: usize = 9;

pub struct Cycle {
    rows: Vec<CycleRow>,
    pub choice: Vec<usize>,
    /// The row with the focus.
    pub cur: usize,
    top: usize,
    pub window: u16,
    words: [Clean; 3],
}

impl Cycle {
    pub fn new(rows: &[CycleRow], words: [Clean; 3]) -> Cycle {
        let choice = rows.iter().map(|r| if r.start < r.choices.len() { r.start } else { 0 }).collect();
        // The focus starts on the first row that can change.
        let cur = rows.iter().position(|r| r.choices.len() > 1).unwrap_or(0);
        Cycle { rows: rows.to_vec(), choice, cur, top: 0, window: 1, words }
    }

    pub fn len(&self) -> usize {
        self.rows.len()
    }

    pub fn is_empty(&self) -> bool {
        self.rows.is_empty()
    }

    /// Up, Down and Space; true when something changed.
    pub fn key(&mut self, k: Key) -> bool {
        let n = self.rows.len();
        if n == 0 {
            return false;
        }
        match k {
            Key::Up if self.cur > 0 => self.cur -= 1,
            Key::Down if self.cur + 1 < n => self.cur += 1,
            Key::Space => {
                let m = self.rows[self.cur].choices.len();
                if m < 2 {
                    return false;
                }
                self.choice[self.cur] = (self.choice[self.cur] + 1) % m;
                return true;
            }
            _ => return false,
        }
        self.top = list_window(n, usize::from(self.window), self.cur, self.top).0;
        true
    }

    /// A row's text: its label, its choice, its state.
    fn row_text(&self, i: usize) -> Clean {
        let r = &self.rows[i];
        let state = match (r.choices.len(), self.choice[i]) {
            (0 | 1, _) => &self.words[2],
            (_, 0) => &self.words[0],
            _ => &self.words[1],
        };
        let choice = if r.choices.len() > 1 { r.choices[self.choice[i]].clone() } else { r.choices.first().cloned().unwrap_or_default() };
        let pad = " ".repeat(CHOICE_W.saturating_sub(choice.width()));
        r.label.join("", &choice).join(&pad, state)
    }

    pub fn draw(&self, c: &mut Canvas, row: u16, focused: bool) {
        c.frame(row, FRAME_COL, self.window + 2, FRAME_W, DIALOG);
        let (first, count, above, below) = list_window(self.rows.len(), usize::from(self.window), self.cur, self.top);
        let mut r = row + 1;
        if above {
            c.text_in(r, ROW_COL, ROW_W, &Clean::ours("^ more above"), DIALOG);
            r += 1;
        }
        for i in (first..self.rows.len()).take(count) {
            let a = if self.cur == i && focused { DIALOG.focus() } else { DIALOG };
            c.text_in(r, ROW_COL, ROW_W, &self.row_text(i), a);
            if self.cur == i && focused {
                c.cursor = Some((r, ROW_COL));
            }
            r += 1;
        }
        if below {
            c.text_in(r, ROW_COL, ROW_W, &Clean::ours("v more below"), DIALOG);
        }
        // The focused row's note, under the list and a blank row.
        if let Some(note) = self.rows.get(self.cur).map(|r| &r.note) {
            let at = row + self.window + 3;
            for (k, l) in text::wrap(note, usize::from(TEXT_W)).iter().take(usize::from(NOTE_ROWS)).enumerate() {
                c.text(at + k as u16, TEXT_COL, l, DIALOG);
            }
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

    fn rows() -> Vec<CycleRow> {
        vec![
            CycleRow { label: o("/boot      ext4    ->  "), choices: vec![o("ext4")], start: 0, note: o("/boot: GRUB reads it.") },
            CycleRow { label: o("/srv       ext4    ->  "), choices: vec![o("ext4"), o("XFS"), o("btrfs")], start: 0, note: Clean::default() },
        ]
    }

    #[test]
    fn space_cycles_a_row_that_can_change() {
        let mut c = Cycle::new(&rows(), [o("as it was"), o("changed"), o("can't change: see below")]);
        c.window = 2;
        assert_eq!(c.cur, 1, "the first row that can change");
        assert!(c.key(Key::Space));
        assert_eq!(c.choice, vec![0, 1]);
        assert!(c.key(Key::Space) && c.key(Key::Space));
        assert_eq!(c.choice[1], 0, "round to what it was");
        assert!(c.key(Key::Up));
        assert!(!c.key(Key::Space), "one choice: no change");
        let mut cv = Canvas::new(10, 80, DIALOG);
        c.draw(&mut cv, 0, true);
        let d = cv.dump(Lines::Ascii, true);
        assert!(d.contains("/boot      ext4    ->  ext4     can't change: see below"), "{d}");
        assert!(d.contains("/srv       ext4    ->  ext4     as it was"), "{d}");
        assert!(d.contains("/boot: GRUB reads it."), "the focused row's note: {d}");
    }
}
