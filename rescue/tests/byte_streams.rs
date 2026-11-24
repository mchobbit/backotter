//! Byte streams (RT5): what each profile sends for the sample screen, a
//! full frame, a gauge step and a move of the list's selection, kept in
//! tests/bytes/; every byte checked against the profile's sequences; the
//! byte budgets; and diffed frames shown to leave the terminal as the
//! canvas says, over random walks.

mod common;

use bko_rescue::screen::{Canvas, Ch, Screen, BACKGROUND, DIALOG, SHADOW};
use bko_rescue::term::out::{Attr, Caps, Colour, Glyph, Lines, Out};
use bko_rescue::term::tty::Size;
use bko_rescue::testkit::{escape, fuzz_cases, golden, Checker, Rng, Rules};
use bko_rescue::text::{self, Clean};

const S80: Size = Size { rows: 24, cols: 80, assumed: false };

/// The budgets (docs/design/rescue-tui.md, "Screen buffer and redraw").
/// A gauge step's is for the steps after the first, as the spike measured
/// it: the first after a full frame pays for a whole attribute change once
/// (40-49 bytes).
const GAUGE_STEP_MAX: usize = 40;
const FIRST_GAUGE_STEP_MAX: usize = 50;
const LIST_MOVE_MAX: usize = 200;
const FULL_FRAME_MAX: usize = 4096;

fn rules(lines: Lines, colour: bool, caps: Caps, s: Size) -> Rules {
    Rules { lines, colour, caps, rows: s.rows, cols: s.cols }
}

/// The terminal, as the checker's model has it, shows `want` placed at
/// (`top`, `left`): every cell's glyph, and its attributes as sent (for
/// a space, only its background and reverse, which is all it shows).
fn shows(ck: &Checker, want: &Canvas, top: u16, left: u16, lines: Lines, colour: bool) -> Result<(), String> {
    let (rows, cols) = (ck.r.rows, ck.r.cols);
    for r in 0..want.rows() {
        for c in 0..want.cols() {
            let (tr, tc) = (top + r + 1, left + c + 1);
            if tr > rows || tc > cols || (tr, tc) == (rows, cols) {
                continue;
            }
            let Some(cell) = want.get(r, c) else { continue };
            let ch = match cell.ch {
                Ch::Text(x) => text::shown(x, lines),
                Ch::Line(g) if lines == Lines::Ascii => char::from(g.ascii()),
                Ch::Line(g) => g.utf8(),
            };
            let a = if colour { cell.attr } else { Attr { fg: None, bg: None, ..cell.attr } };
            let Some((sch, sa)) = ck.shown(tr, tc) else { continue };
            let attr_ok = if ch == ' ' { sa.bg == a.bg && sa.reverse == a.reverse } else { sa == a };
            if sch != ch || !attr_ok {
                return Err(format!("({tr},{tc}): shows {sch:?} {sa:?}, wanted {ch:?} {a:?}"));
            }
        }
    }
    Ok(())
}

#[test]
fn every_profile_full_frame_gauge_step_and_list_move() {
    for (lines, colour, caps) in common::profiles() {
        let name = common::profile_name(lines, colour, caps);
        let mut scr = Screen::new(lines, colour, caps, S80);
        let mut ck = Checker::new(rules(lines, colour, caps, S80));
        let full = scr.frame(&common::gauge_only(8));
        ck.feed(&full);
        let first = scr.frame(&common::gauge_only(9));
        ck.feed(&first);
        let gauge = scr.frame(&common::gauge_only(10));
        ck.feed(&gauge);
        let moved = common::sample(1, common::list_top(1, 0), 10);
        let list = scr.frame(&moved);
        ck.feed(&list);
        assert!(scr.frame(&moved).is_empty(), "{name}: an unchanged frame sends nothing");
        ck.feed(&Out::reset_bytes(lines, caps, 24, 80));
        assert_eq!(ck.result(), Ok(()), "{name}");
        assert!(full.len() < FULL_FRAME_MAX, "{name}: full frame {} bytes", full.len());
        assert!(first.len() < FIRST_GAUGE_STEP_MAX, "{name}: first gauge step {} bytes", first.len());
        assert!(gauge.len() < GAUGE_STEP_MAX, "{name}: gauge step {} bytes", gauge.len());
        assert!(list.len() < LIST_MOVE_MAX, "{name}: list move {} bytes", list.len());
        let text = format!(
            "# bko-rescue's bytes for the sample screen at 80x24: lines {}, colour {}, caps {}\n\
             # (no focus for the gauge steps, as on the Restore screen; then the focus on the list)\n\
             # full frame: {} bytes\n{}\
             # the first gauge step, 40 % to 45 %: {} bytes\n{}\
             # the next gauge step, 45 % to 50 %: {} bytes\n{}\
             # the list's selection down one: {} bytes\n{}",
            lines.name(),
            if colour { "on" } else { "off" },
            if caps == Caps::ALL { "all" } else { "none (one SGR parameter each, cursor parked, no clear)" },
            full.len(),
            escape(&full),
            first.len(),
            escape(&first),
            gauge.len(),
            escape(&gauge),
            list.len(),
            escape(&list),
        );
        golden(&format!("tests/bytes/sample.{name}.txt"), &text);
    }
}

/// The bytes leave the terminal showing the canvas, after the full frame
/// and after each diffed one.
#[test]
fn the_sample_frames_show_their_canvas() {
    for (lines, colour, caps) in common::profiles() {
        let name = common::profile_name(lines, colour, caps);
        let mut scr = Screen::new(lines, colour, caps, S80);
        let mut ck = Checker::new(rules(lines, colour, caps, S80));
        let mut top = 0;
        for (sel, step) in [(0, 0), (0, 1), (1, 1), (2, 5), (3, 5), (4, 20), (3, 20), (0, 20)] {
            top = common::list_top(sel, top);
            let c = common::sample(sel, top, step);
            ck.feed(&scr.frame(&c));
            assert_eq!(ck.result(), Ok(()), "{name}");
            assert_eq!(shows(&ck, &c, 0, 0, lines, colour), Ok(()), "{name}: sel {sel} step {step}");
        }
    }
}

/// A terminal something else wrote to first, and left with DEC on in the
/// DEC profile (a bko-rescue killed mid-frame): the first frame alone
/// shows the canvas, DEC switched off before any letter, and the last
/// cell emptied where the console clears.
#[test]
fn the_first_frame_over_foreign_output() {
    for (lines, colour, caps) in common::profiles() {
        let name = common::profile_name(lines, colour, caps);
        let mut scr = Screen::new(lines, colour, caps, S80);
        let mut ck = Checker::new(rules(lines, colour, caps, S80));
        let dec = lines == Lines::Dec;
        ck.foreign('#', dec);
        let c = common::sample(0, 0, 3);
        let f = scr.frame(&c);
        if dec {
            let off = f.windows(3).position(|w| w == b"\x1b(B");
            let letter = f.iter().position(|b| b.is_ascii_alphabetic());
            assert!(off.is_some() && off < letter, "{name}: ESC ( B before the first letter");
        }
        ck.feed(&f);
        assert_eq!(ck.result(), Ok(()), "{name}");
        assert_eq!(shows(&ck, &c, 0, 0, lines, colour), Ok(()), "{name}");
        let last = ck.shown(24, 80).map(|(ch, _)| ch);
        assert_eq!(last, Some(if caps.clear { ' ' } else { '#' }), "{name}: the last cell");
    }
}

/// A bigger terminal: the canvas centred, the margin in the background,
/// every byte allowed, the canvas shown.
#[test]
fn a_bigger_terminal() {
    let big = Size { rows: 30, cols: 100, assumed: false };
    for (lines, colour, caps) in common::profiles() {
        let name = common::profile_name(lines, colour, caps);
        let mut scr = Screen::new(lines, colour, caps, big);
        let mut ck = Checker::new(rules(lines, colour, caps, big));
        let c = common::sample(0, 0, 3);
        ck.feed(&scr.frame(&c));
        let d = common::sample(1, 0, 4);
        ck.feed(&scr.frame(&d));
        assert_eq!(ck.result(), Ok(()), "{name}");
        assert_eq!(shows(&ck, &d, 3, 10, lines, colour), Ok(()), "{name}");
        // The margin: in the background where there is colour.
        let margin = ck.shown(1, 1).map(|(_, a)| a.bg);
        assert_eq!(margin, Some(if colour { BACKGROUND.bg } else { None }), "{name}");
    }
}

/// Fuzz-style (BKO_RESCUE_FUZZ=N for more): random drawing on a canvas,
/// frame after frame, with full redraws and resizes now and then.  Every
/// stream is allowed by its profile, and the terminal always shows the
/// canvas.
#[test]
fn random_walks_show_their_canvas() {
    let mut r = Rng::new(0xca9_7a5);
    let steps = (fuzz_cases(100_000) / 400).max(10);
    const ATTRS: [Attr; 7] = [
        BACKGROUND,
        DIALOG,
        SHADOW,
        Attr::PLAIN,
        Attr::colours(Colour::Red, Colour::Green),
        Attr { bold: true, reverse: false, fg: Some(Colour::Yellow), bg: Some(Colour::Blue) },
        Attr { bold: false, reverse: true, fg: Some(Colour::Black), bg: Some(Colour::White) },
    ];
    const GLYPHS: [Glyph; 4] = [Glyph::Horiz, Glyph::Vert, Glyph::TopLeft, Glyph::TeeTop];
    const WORDS: [&str; 6] = ["a", "Łódź", "\x1b[2J", "中é", "long text, longer", "  "];
    for (lines, colour, caps) in common::profiles() {
        let name = common::profile_name(lines, colour, caps);
        let mut size = S80;
        let mut scr = Screen::new(lines, colour, caps, size);
        let mut ck = Checker::new(rules(lines, colour, caps, size));
        let mut c = Canvas::standard();
        for step in 0..steps {
            for _ in 0..1 + r.below(6) {
                let (row, col) = (r.below(24) as u16, r.below(80) as u16);
                let a = *r.pick(&ATTRS);
                match r.below(6) {
                    0 => {
                        c.text(row, col, &Clean::line(*r.pick(&WORDS), 64), a);
                    }
                    1 => c.fill(row, col, 1 + r.below(4) as u16, 1 + r.below(20) as u16, a),
                    2 => c.hline(row, col, 1 + r.below(30) as u16, *r.pick(&GLYPHS), a),
                    3 => c.frame(row, col, 2 + r.below(8) as u16, 2 + r.below(30) as u16, a),
                    4 => c.recolour(row, col, 1 + r.below(3) as u16, 1 + r.below(10) as u16, a),
                    _ => c.glyph(row, col, *r.pick(&GLYPHS), a),
                }
            }
            match r.below(40) {
                0 => scr.invalidate(),
                1 => {
                    // A resize: a new terminal, so a new model of it.
                    size = if size == S80 { Size { rows: 26, cols: 83, assumed: false } } else { S80 };
                    scr.resize(size);
                    ck = Checker::new(rules(lines, colour, caps, size));
                }
                _ => {}
            }
            ck.feed(&scr.frame(&c));
            assert_eq!(ck.result(), Ok(()), "{name}, step {step}");
            let (top, left) = ((size.rows - 24) / 2, (size.cols - 80) / 2);
            assert_eq!(shows(&ck, &c, top, left, lines, colour), Ok(()), "{name}, step {step}");
        }
    }
}
