//! Golden screens (RT5): the sample screen at 80x24 in every line style,
//! colour and mono, as `Canvas::dump` writes it, kept in tests/screens/.
//! BKO_RESCUE_GOLDEN=check (the default), report or bless.

mod common;

use bko_rescue::screen::{Canvas, BACKGROUND, DIALOG};
use bko_rescue::term::out::Lines;
use bko_rescue::testkit::golden;
use bko_rescue::text::Clean;

#[test]
fn the_sample_in_every_line_style_colour_and_mono() {
    let c = common::sample(0, 0, 8);
    for lines in [Lines::Utf8, Lines::Dec, Lines::Ascii] {
        for colour in [true, false] {
            let name = format!("tests/screens/sample.{}-{}.txt", lines.name(), if colour { "colour" } else { "mono" });
            golden(&name, &c.dump(lines, colour));
        }
    }
}

#[test]
fn the_sample_scrolled_with_both_markers() {
    let top = common::list_top(1, 0);
    let top = common::list_top(2, top);
    golden("tests/screens/sample-scrolled.utf8-colour.txt", &common::sample(2, top, 20).dump(Lines::Utf8, true));
}

/// rescue-screens.md's "Too small a terminal", 60x14, drawn at the
/// terminal's own size.
#[test]
fn too_small() {
    let mut c = Canvas::new(14, 60, BACKGROUND);
    let o = Clean::ours;
    c.text(0, 0, &o(" BackOtter rescue"), BACKGROUND);
    c.frame(2, 2, 10, 56, DIALOG);
    c.shadow(2, 2, 10, 56);
    let title = o(" This window is too small ");
    c.text(2, 2 + (56 - title.width() as u16) / 2, &title, DIALOG);
    for (i, l) in [
        "bko-rescue needs 80 columns and 24 rows.",
        "This one has 60 columns and 14 rows.",
        "",
        "Make the window bigger, or press p for plain mode:",
        "the same questions, one line at a time.",
    ]
    .iter()
    .enumerate()
    {
        c.text(4 + i as u16, 4, &o(l), DIALOG);
    }
    c.text(13, 0, &o(" p: plain mode | s: shell | Enter: check again"), BACKGROUND);
    for lines in [Lines::Utf8, Lines::Ascii] {
        golden(&format!("tests/screens/too-small.{}-colour.txt", lines.name()), &c.dump(lines, true));
    }
}
