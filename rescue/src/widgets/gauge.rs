//! The gauge: a bar of reverse-video cells in brackets with the
//! percentage in figures beside it, so it needs no block glyphs and no
//! colour; under it a line of text and the time since it started.  With
//! no percentage there is no bar, only the line and the timer.

use crate::screen::{Canvas, DIALOG};
use crate::text::Clean;

use super::{TEXT_COL, TEXT_W};

/// Cells in the bar: 40, as RT5's sample, so a 5 % step is 2 cells and
/// stays under the 40-byte budget in every profile (62 cells, as wide as
/// the mock's, took 40 bytes with one SGR parameter each).
pub const BAR: u16 = 40;

pub struct Gauge {
    pub pct: Option<u8>,
    pub line: Clean,
    pub elapsed_s: u64,
}

impl Default for Gauge {
    fn default() -> Self {
        Gauge::new()
    }
}

/// `m:ss`, or `h:mm:ss` from an hour.
pub fn clock(s: u64) -> String {
    if s >= 3600 {
        format!("{}:{:02}:{:02}", s / 3600, s / 60 % 60, s % 60)
    } else {
        format!("{}:{:02}", s / 60, s % 60)
    }
}

impl Gauge {
    pub fn new() -> Gauge {
        Gauge { pct: Some(0), line: Clean::default(), elapsed_s: 0 }
    }

    pub fn set(&mut self, pct: Option<u8>, line: Clean) {
        self.pct = pct.map(|p| p.min(100));
        self.line = line;
    }

    /// Two rows from `row`: the bar, then the line and the timer.
    pub fn draw(&self, c: &mut Canvas, row: u16) {
        let o = Clean::ours;
        if let Some(p) = self.pct {
            let filled = (u32::from(BAR) * u32::from(p) / 100) as u16;
            c.text(row, TEXT_COL, &o("["), DIALOG);
            c.fill(row, TEXT_COL + 1, 1, filled, DIALOG.reverse());
            c.fill(row, TEXT_COL + 1 + filled, 1, BAR - filled, DIALOG);
            c.text(row, TEXT_COL + 1 + BAR, &o("]"), DIALOG);
            c.text(row, TEXT_COL + 2 + BAR, &o(&format!("{:4} %", p)), DIALOG);
        }
        let t = o(&clock(self.elapsed_s));
        let tw = t.width() as u16;
        c.text_in(row + 1, TEXT_COL, TEXT_W - tw - 1, &self.line, DIALOG);
        c.text(row + 1, TEXT_COL + TEXT_W - tw, &t, DIALOG);
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::term::out::Lines;

    #[test]
    fn the_bar_fills_in_reverse_video() {
        let mut g = Gauge::new();
        g.set(Some(50), Clean::ours("38.2 of 61.2 GB"));
        g.elapsed_s = 3725;
        let mut c = Canvas::new(2, 80, DIALOG);
        g.draw(&mut c, 0);
        let d = c.dump(Lines::Ascii, true);
        let l: Vec<&str> = d.lines().collect();
        assert!(l[0].starts_with("    [") && l[0].contains("]  50 %"), "{}", l[0]);
        assert_eq!(l[0].len(), 80);
        assert_eq!(l[0].find(']'), Some(45));
        assert!(l[1].starts_with("    38.2 of 61.2 GB") && l[1].trim_end().ends_with("1:02:05"), "{}", l[1]);
        // 20 reverse cells after the bracket, then plain ones.
        assert_eq!(&l[3][5..5 + 20], "r".repeat(20));
        assert_eq!(&l[3][25..26], "d");
        g.set(Some(200), Clean::default());
        assert_eq!(g.pct, Some(100));
    }

    #[test]
    fn no_percentage_no_bar() {
        let mut g = Gauge::new();
        g.set(None, Clean::ours("running"));
        let mut c = Canvas::new(2, 80, DIALOG);
        g.draw(&mut c, 0);
        let d = c.dump(Lines::Ascii, true);
        assert!(!d.lines().next().unwrap_or("").contains('['));
        assert_eq!(clock(59), "0:59");
        assert_eq!(clock(61), "1:01");
    }
}
