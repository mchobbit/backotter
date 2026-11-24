//! A sample screen drawn with the canvas alone (the widgets are RT6), laid
//! out as docs/design/rescue-screens.md's screens are: the title line, a
//! 76-column box with its title in the border and a shadow, wrapped text,
//! a list in its own frame that scrolls with markers, a gauge, buttons
//! with the focus, and the key line.  It is what the golden screens and
//! the byte budgets measure.

#![allow(dead_code)]

use bko_rescue::screen::{Canvas, BACKGROUND, DIALOG};
use bko_rescue::spike::list_window;
use bko_rescue::term::out::{Caps, Lines};
use bko_rescue::text::{self, cap, Clean};

pub const ITEMS: [&str; 5] = [
    "lovelace   Debian 13      newest backup 22 Nov 2026 02:00 CET",
    "babbage    Fedora 45      newest backup 21 Nov 2026 03:00 EST",
    "hopper     FreeBSD 15.0   newest backup 18 Nov 2026 01:00 UTC",
    "Łódź       Gentoo         newest backup 17 Nov 2026 23:10 CET",
    "x\x1b[2J\u{202e}中   (a hostile name, cleaned)",
];

/// The gauge: 40 cells, 20 steps of 5%.
pub const BAR: u16 = 40;
pub const STEPS: u32 = 20;

/// The sample with list row `sel` focused (the list scrolled from `top`)
/// and the gauge at `step` of STEPS.  The cursor is at the focused row.
pub fn sample(sel: usize, top: usize, step: u32) -> Canvas {
    let mut c = Canvas::standard();
    let o = Clean::ours;
    c.text(0, 0, &o(" BackOtter rescue"), BACKGROUND);
    let name = o("Machine");
    c.text(0, 80 - name.width() as u16, &name, BACKGROUND);
    // The box: columns 3 to 78 (from 1), rows 3 to 22.
    let (top_r, left, h, w) = (2u16, 2u16, 20u16, 76u16);
    c.frame(top_r, left, h, w, DIALOG);
    c.shadow(top_r, left, h, w);
    let title = o(" Which machine is this? ");
    c.text(top_r, left + (w - title.width() as u16) / 2, &title, DIALOG);
    let body = o("The stick knows these machines.  None of them was backed up from this computer.  Pick the one you are restoring.");
    for (i, l) in text::wrap(&body, 72).iter().enumerate() {
        c.text(top_r + 2 + i as u16, left + 2, l, DIALOG);
    }
    // The list: its own frame, 66 columns of text, 3 rows for 5 items.
    let (lt, ll) = (top_r + 5, left + 3);
    c.frame(lt, ll, 5, 70, DIALOG);
    let (first, count, above, below) = list_window(ITEMS.len(), 3, sel, top);
    let mut row = lt + 1;
    if above {
        c.text_in(row, ll + 2, 66, &o("^ more above"), DIALOG);
        row += 1;
    }
    for (i, item) in ITEMS.iter().enumerate().skip(first).take(count) {
        let mark = if i == sel { "(*) " } else { "( ) " };
        let t = o(mark).join("", &Clean::line(item, cap::LINE));
        let a = if i == sel { DIALOG.focus() } else { DIALOG };
        c.text_in(row, ll + 2, 66, &t, a);
        if i == sel {
            // Where the cursor goes when it can't hide: the focused row.
            c.cursor = Some((row, ll + 2));
        }
        row += 1;
    }
    if below {
        c.text_in(row, ll + 2, 66, &o("v more below"), DIALOG);
    }
    // The gauge: reverse-video spaces, the figures beside them.
    let gr = top_r + 12;
    c.text(gr, left + 2, &o("Copying files back"), DIALOG);
    let filled = (u32::from(BAR) * step / STEPS) as u16;
    c.text(gr + 1, left + 2, &o("["), DIALOG);
    c.fill(gr + 1, left + 3, 1, filled, DIALOG.reverse());
    c.text(gr + 1, left + 3 + BAR, &o("]"), DIALOG);
    let pct = Clean::ours(&format!("{:3} %", step * 100 / STEPS));
    c.text(gr + 1, left + 5 + BAR, &pct, DIALOG);
    let host = Clean::line("This computer: Łódź-ñ \x07(serial S7XMNF0Y787118)", cap::LINE);
    c.text(gr + 3, left + 2, &host, DIALOG);
    // Buttons, centred, the first focused.
    let (ok, back) = (o("<  OK  >"), o("< Back >"));
    let bw = ok.width() as u16 + 4 + back.width() as u16;
    let bc = left + (w - bw) / 2;
    c.text(top_r + h - 2, bc, &ok, DIALOG.focus());
    c.text(top_r + h - 2, bc + ok.width() as u16 + 4, &back, DIALOG);
    c.text(23, 0, &o(" Arrows | Enter: OK | s: shell | L: lines | C: colour | P: plain"), BACKGROUND);
    c
}

/// The list's first row shown after moving to `sel` from a window at
/// `top`, as the list keeps it.
/// The same with no focus, as the Restore screen is while its gauge
/// runs: a console that can't hide the cursor has nowhere to park it.
pub fn gauge_only(step: u32) -> Canvas {
    let mut c = sample(0, 0, step);
    c.cursor = None;
    c
}

pub fn list_top(sel: usize, top: usize) -> usize {
    list_window(ITEMS.len(), 3, sel, top).0
}

/// Every profile: three line styles, colour and mono, all the sequences
/// or every fallback.
pub fn profiles() -> Vec<(Lines, bool, Caps)> {
    let mut v = Vec::new();
    for lines in [Lines::Utf8, Lines::Dec, Lines::Ascii] {
        for colour in [true, false] {
            for caps in [Caps::ALL, Caps::NONE] {
                v.push((lines, colour, caps));
            }
        }
    }
    v
}

pub fn profile_name(lines: Lines, colour: bool, caps: Caps) -> String {
    format!(
        "{}-{}-{}",
        lines.name(),
        if colour { "colour" } else { "mono" },
        if caps == Caps::ALL { "all" } else { "none" }
    )
}
