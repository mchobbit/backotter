//! Golden screens (RT5): the sample screen at 80x24 in every line style,
//! colour and mono, as `Canvas::dump` writes it, kept in tests/screens/.
//! BKO_RESCUE_GOLDEN=check (the default), report or bless.

mod common;

use bko_rescue::term::out::Lines;
use bko_rescue::testkit::golden;

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

// The too-small notice is the widget's now: tests/widget_screens.rs.
