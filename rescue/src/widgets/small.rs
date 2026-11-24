//! The notice drawn instead of any screen when the terminal is smaller
//! than 80x24 (D10; rescue-screens.md, "Too small a terminal").  It is
//! drawn at the terminal's own size.  No focus: `p` goes on in plain
//! mode, `s` opens a shell, Enter measures the window again, and a
//! resize to 80x24 or more draws the screen that was due.  Below 40x10,
//! or when the box doesn't fit, it is three lines with no box.

use crate::screen::{Canvas, BACKGROUND, DIALOG};
use crate::term::tty::Size;
use crate::text::{self, Clean};

pub fn too_small(size: Size) -> Canvas {
    let (rows, cols) = (size.rows.max(1), size.cols.max(1));
    let mut c = Canvas::new(rows, cols, BACKGROUND);
    let o = Clean::ours;
    let keys = o(" p: plain mode | s: shell | Enter: check again");
    let body = o(&format!(
        "bko-rescue needs 80 columns and 24 rows.\nThis one has {cols} columns and {rows} rows.\n\n\
         Make the window bigger, or press p for plain mode: the same questions, one line at a time."
    ));
    let w = cols.saturating_sub(4).min(76);
    let lines = text::wrap(&body, usize::from(w.saturating_sub(4)).max(1));
    let h = lines.len() as u16 + 4;
    if rows < 10 || cols < 40 || 2 + h > rows - 1 {
        let short = [
            format!("bko-rescue needs 80x24; this is {cols}x{rows}."),
            "Make it bigger, or press p for plain mode.".to_string(),
            "p: plain | s: shell | Enter: check again".to_string(),
        ];
        for (i, l) in short.iter().enumerate() {
            c.text(i as u16, 0, &o(l).truncate(usize::from(cols)), BACKGROUND);
        }
        return c;
    }
    c.text(0, 0, &o(" BackOtter rescue"), BACKGROUND);
    c.frame(2, 2, h, w, DIALOG);
    c.shadow(2, 2, h, w);
    let title = o(" This window is too small ");
    if title.width() as u16 + 2 <= w {
        c.text(2, 2 + (w - title.width() as u16) / 2, &title, DIALOG);
    }
    for (i, l) in lines.iter().enumerate() {
        c.text(4 + i as u16, 4, l, DIALOG);
    }
    c.text(rows - 1, 0, &keys.truncate(usize::from(cols) - 1), BACKGROUND);
    c
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::term::out::Lines;

    fn sz(rows: u16, cols: u16) -> Size {
        Size { rows, cols, assumed: false }
    }

    #[test]
    fn small_sizes_get_three_lines() {
        for s in [sz(9, 60), sz(24, 39), sz(1, 1), sz(10, 40)] {
            let c = too_small(s);
            assert_eq!((c.rows(), c.cols()), (s.rows, s.cols));
            let d = c.dump(Lines::Ascii, true);
            assert!(!d.contains('+'), "no box at {s:?}");
        }
        let d = too_small(sz(3, 50)).dump(Lines::Ascii, true);
        assert!(d.starts_with("bko-rescue needs 80x24; this is 50x3."), "{d}");
    }

    #[test]
    fn a_box_where_it_fits() {
        let d = too_small(sz(20, 79)).dump(Lines::Utf8, true);
        assert!(d.contains("This one has 79 columns and 20 rows.") && d.contains("This window is too small"));
    }
}
