//! Whiptail's widgets, drawn into a canvas, taking keys, doing no I/O
//! (docs/design/rescue-tui.md, "Widgets"; rescue-screens.md,
//! "Conventions").
//!
//! - A `Dialog` is the title line, one box with its title in the top
//!   border, a shadow in colour, its parts (text, a list, a field, a text
//!   view, a gauge) one under another, a notice line, the buttons row, and
//!   the key line.  The box is 76 columns (3 to 78), text runs 72, and
//!   lists have their own frame with 66 columns of text.
//! - Focus is reverse and bold on one list row, field or button.  Tab
//!   moves it between the parts that take it and each button in turn;
//!   Enter presses what has it (on a part, the dialog's OK); Esc is Back
//!   where there is a Back button; a button's letter presses it when the
//!   focus isn't in a field.
//! - `FullScreen` (run.rs) is the runner: the poll loop, resizes, the
//!   periodic redraw, the too-small notice (D10), and `L`, `C` and `P` on
//!   the first screen.

pub mod field;
pub mod gauge;
pub mod list;
pub mod run;
pub mod small;
pub mod view;
pub mod words;

pub use run::{FullScreen, Io};

use crate::screen::{Canvas, BACKGROUND, COLS, DIALOG, ROWS};
use crate::term::keys::Key;
use crate::term::out::Attr;
use crate::text::{self, Clean, CUT};
use crate::ui::{self, Act, Button, Chrome};

use field::Field;
use gauge::Gauge;
use list::List;
use view::View;
use words::Words;

/// The box: columns 3 to 78, rows 2 to 23 at most.
pub const BOX_LEFT: u16 = 2;
pub const BOX_W: u16 = 76;
/// Where text starts in the box, and how wide it runs.
pub const TEXT_COL: u16 = BOX_LEFT + 2;
pub const TEXT_W: u16 = 72;
/// The rows the box may take: 2 to 23 (from 1).
const AREA_TOP: u16 = 1;
const AREA_H: u16 = ROWS - 2;
/// The most rows of parts: the box less its borders, the blank rows
/// around the parts and the buttons row.
pub const MAX_BODY: u16 = 17;
/// The gap between buttons.
const BUTTON_GAP: u16 = 4;
/// Cells of a radio list's "Try it here" field (the Keyboard screen).
pub const TRY_W: u16 = 40;
/// A sentinel `Act` for Question's No.
pub(crate) const NO: usize = usize::MAX;

/// Bold on the dialog: the notice line, and a menu's row when the focus
/// is elsewhere.
pub const NOTE: Attr = Attr { bold: true, ..DIALOG };

pub enum Part {
    /// Wrapped at TEXT_W; a newline starts a paragraph.
    Text(Clean),
    /// Blank rows.
    Gap(u16),
    List(List),
    Field(Field),
    View(View),
    Gauge(Gauge),
    /// The recovery key's twelve word fields.
    Words(Words),
}

impl Part {
    fn takes_focus(&self) -> bool {
        matches!(self, Part::List(_) | Part::Field(_) | Part::View(_) | Part::Words(_))
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Focus {
    None,
    Part(usize),
    Button(usize),
}

/// What a key did.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Did {
    Nothing,
    Changed,
    /// A button was pressed, by its index.
    Press(usize),
    /// A letter no button has, for the runner (`L`, `C`, `P`), lower case.
    Letter(char),
}

pub struct Dialog {
    pub chrome: Chrome,
    pub parts: Vec<Part>,
    pub buttons: Vec<Button>,
    pub focus: Focus,
    /// A line above the buttons: why a button can't be pressed yet.
    pub notice: Option<Clean>,
    /// The button Esc presses when there is no Back (a question's No).
    pub esc: Option<usize>,
    /// Rows each part takes, from the last layout.
    heights: Vec<u16>,
}

/// A button as drawn: `<  OK  >`, `< Back >`.  A label of one or two
/// cells, or a button alone, gets two spaces each side.
pub fn button_text(b: &Clean, alone: bool) -> Clean {
    let pad = if alone || b.width() <= 2 { "  " } else { " " };
    Clean::ours(&format!("<{pad}")).join("", b).join(&format!("{pad}>"), &Clean::default())
}

/// Text and a gap after it, when there is text.
fn text_part(parts: &mut Vec<Part>, t: &Clean) {
    if !t.is_empty() {
        parts.push(Part::Text(t.clone()));
        parts.push(Part::Gap(1));
    }
}

impl Dialog {
    pub fn new(chrome: Chrome, parts: Vec<Part>, buttons: Vec<Button>) -> Dialog {
        let mut d = Dialog { chrome, parts, buttons, focus: Focus::None, notice: None, esc: None, heights: Vec::new() };
        // Trailing gaps would only push the buttons down.
        while matches!(d.parts.last(), Some(Part::Gap(_))) {
            d.parts.pop();
        }
        d.focus = d.first_focus();
        d.layout();
        d
    }

    fn first_focus(&self) -> Focus {
        match self.parts.iter().position(Part::takes_focus) {
            Some(i) => Focus::Part(i),
            None if self.buttons.is_empty() => Focus::None,
            None => Focus::Button(0),
        }
    }

    pub fn menu(m: &ui::Menu) -> Dialog {
        let mut parts = Vec::new();
        text_part(&mut parts, &m.text);
        parts.push(Part::List(List::menu(&m.items, m.start)));
        parts.push(Part::Gap(1));
        text_part(&mut parts, &m.after);
        Dialog::new(m.chrome.clone(), parts, m.buttons.clone())
    }

    pub fn radio(r: &ui::Radio) -> Dialog {
        let mut parts = Vec::new();
        text_part(&mut parts, &r.text);
        parts.push(Part::List(List::radio(&r.items, r.chosen)));
        parts.push(Part::Gap(1));
        text_part(&mut parts, &r.after);
        if let Some(l) = &r.try_it {
            parts.push(Part::Field(Field::text("", ui::INPUT_MAX).labelled(l.clone(), TRY_W)));
            parts.push(Part::Gap(1));
        }
        Dialog::new(r.chrome.clone(), parts, r.buttons.clone())
    }

    pub fn input(i: &ui::Input) -> Dialog {
        let mut parts = Vec::new();
        text_part(&mut parts, &i.text);
        let mut f = Field::text(&i.value, i.max.min(ui::INPUT_MAX));
        if let Some((l, w)) = &i.label {
            f = f.labelled(l.clone(), *w);
        }
        parts.push(Part::Field(f));
        parts.push(Part::Gap(1));
        text_part(&mut parts, &i.after);
        let mut d = Dialog::new(i.chrome.clone(), parts, i.buttons.clone());
        d.notice = i.notice.clone();
        d
    }

    pub fn words(w: &ui::Words) -> Dialog {
        let mut parts = Vec::new();
        text_part(&mut parts, &w.text);
        parts.push(Part::Words(Words::new()));
        parts.push(Part::Gap(1));
        text_part(&mut parts, &w.after);
        Dialog::new(w.chrome.clone(), parts, w.buttons.clone())
    }

    pub fn words_part(&self) -> Option<&Words> {
        self.parts.iter().find_map(|p| match p {
            Part::Words(w) => Some(w),
            _ => None,
        })
    }

    pub fn password(p: &ui::Password) -> Dialog {
        let mut parts = Vec::new();
        text_part(&mut parts, &p.text);
        parts.push(Part::Field(Field::password()));
        parts.push(Part::Gap(1));
        text_part(&mut parts, &p.after);
        Dialog::new(p.chrome.clone(), parts, p.buttons.clone())
    }

    pub fn question(q: &ui::Question) -> Dialog {
        let mut buttons = vec![Button { label: q.yes.clone(), key: None, act: Act::Ok }, Button {
            label: q.no.clone(),
            key: None,
            act: Act::Other(NO),
        }];
        buttons.extend(q.buttons.iter().cloned());
        let mut d = Dialog::new(q.chrome.clone(), vec![Part::Text(q.text.clone())], buttons);
        d.focus = Focus::Button(usize::from(q.no_first));
        if q.esc_no {
            d.esc = Some(1);
        }
        d
    }

    pub fn message(m: &ui::Message) -> Dialog {
        let mut d = Dialog::new(m.chrome.clone(), vec![Part::Text(m.text.clone())], m.buttons.clone());
        if m.focus < d.buttons.len() {
            d.focus = Focus::Button(m.focus);
        }
        d
    }

    pub fn view(v: &ui::TextView) -> Dialog {
        Dialog::new(v.chrome.clone(), vec![Part::View(View::new(v.lines.clone()))], v.buttons.clone())
    }

    pub fn progress(p: &ui::Progress) -> Dialog {
        let parts = vec![Part::Text(p.text.clone()), Part::Gap(1), Part::Gauge(Gauge::new())];
        let mut d = Dialog::new(p.chrome.clone(), parts, p.buttons.clone());
        // No focus while the gauge runs: Enter does nothing until Tab
        // reaches a button, and no cursor is parked away from the bar.
        d.focus = Focus::None;
        d
    }

    /// A view's box has no blank row above its text.
    fn is_view(&self) -> bool {
        matches!(self.parts.first(), Some(Part::View(_)))
    }

    /// Give each part its rows.  Gaps, fields, gauges and the lists'
    /// frames are fixed.  Each list and the view keep their least rows (3,
    /// or fewer when shorter); text takes what is left, in order, and text
    /// past that is cut.  Then the lists and the view share the rest.  So
    /// the parts never take more than MAX_BODY rows, and a list's focused
    /// row is never under the buttons.
    fn layout(&mut self) {
        let lines = |t: &Clean| u16::try_from(text::wrap(t, usize::from(TEXT_W)).len()).unwrap_or(u16::MAX);
        let len = |n: usize| u16::try_from(n).unwrap_or(u16::MAX);
        let least = |n: u16| n.clamp(1, 3);
        let mut fixed = 0u16;
        let mut text_want = 0u16;
        let mut flex_least = 0u16;
        let mut flexible = 0u16;
        for p in &self.parts {
            match p {
                Part::Text(t) => text_want = text_want.saturating_add(lines(t)),
                Part::Gap(n) => fixed = fixed.saturating_add(*n),
                Part::Field(_) => fixed = fixed.saturating_add(1),
                Part::Gauge(_) => fixed = fixed.saturating_add(2),
                Part::Words(_) => fixed = fixed.saturating_add(words::HEIGHT),
                Part::List(l) => {
                    fixed = fixed.saturating_add(2);
                    flexible += 1;
                    flex_least += least(len(l.len()));
                }
                Part::View(v) => {
                    flexible += 1;
                    flex_least += least(len(v.len()));
                }
            }
        }
        let mut text_room = MAX_BODY.saturating_sub(fixed.saturating_add(flex_least));
        let text_used = text_want.min(text_room);
        let mut left = MAX_BODY.saturating_sub(fixed.saturating_add(text_used));
        let share = left.checked_div(flexible).unwrap_or(0);
        // The least rows of the lists and view still to come: what one
        // may take never eats into theirs.
        let mut least_after = flex_least;
        let mut give = |n: u16, left: &mut u16| {
            let min = least(n);
            least_after -= min;
            let room = left.saturating_sub(least_after);
            let rows = n.min(share.max(1)).min(room).max(min);
            *left = left.saturating_sub(rows);
            rows
        };
        let mut heights = Vec::new();
        for p in &mut self.parts {
            let h = match p {
                Part::Text(t) => {
                    let h = lines(t).min(text_room);
                    text_room -= h;
                    h
                }
                Part::Gap(n) => *n,
                Part::Field(_) => 1,
                Part::Gauge(_) => 2,
                Part::Words(_) => words::HEIGHT,
                Part::List(l) => {
                    let rows = give(len(l.len()), &mut left);
                    l.window = rows;
                    rows + 2
                }
                Part::View(v) => {
                    let rows = give(len(v.len()), &mut left);
                    v.window = rows;
                    rows
                }
            };
            heights.push(h);
        }
        self.heights = heights;
    }

    /// The box's rows: borders, the blank row above the parts (none for a
    /// view), the parts, the blank or notice row, the buttons.
    fn box_h(&self) -> u16 {
        let body: u16 = self.heights.iter().sum::<u16>().min(MAX_BODY);
        let top_gap = u16::from(!self.is_view());
        let buttons = if self.buttons.is_empty() { 0 } else { 2 };
        2 + top_gap + body + buttons + u16::from(buttons == 0)
    }

    /// The whole 80x24 screen.
    pub fn canvas(&mut self) -> Canvas {
        self.layout();
        let mut c = Canvas::standard();
        let o = Clean::ours;
        c.text(0, 0, &o(" BackOtter rescue"), BACKGROUND);
        let name = self.chrome.screen.truncate(56);
        c.text(0, COLS - name.width() as u16, &name, BACKGROUND);
        let h = self.box_h().min(AREA_H);
        let top = AREA_TOP + (AREA_H - h) / 2;
        c.frame(top, BOX_LEFT, h, BOX_W, DIALOG);
        c.shadow(top, BOX_LEFT, h, BOX_W);
        if !self.chrome.title.is_empty() {
            let t = Clean::ours(" ").join("", &self.chrome.title.truncate(usize::from(BOX_W) - 6)).join(" ", &Clean::default());
            c.text(top, BOX_LEFT + (BOX_W - t.width() as u16) / 2, &t, DIALOG);
        }
        // The parts.
        let last = top + h - 1;
        let bottom = if self.buttons.is_empty() { last } else { last - 2 };
        let mut row = top + 1 + u16::from(!self.is_view());
        for (i, p) in self.parts.iter().enumerate() {
            let ph = self.heights[i];
            if row >= bottom {
                break;
            }
            let focused = self.focus == Focus::Part(i);
            match p {
                Part::Text(t) => {
                    let w = text::wrap(t, usize::from(TEXT_W));
                    let shown = usize::from(ph).min(w.len());
                    for (k, l) in w.iter().take(shown).enumerate() {
                        let r = row + k as u16;
                        if r >= bottom {
                            break;
                        }
                        // Text cut by the layout ends in CUT.
                        if k + 1 == shown && shown < w.len() {
                            let cut = l.truncate(usize::from(TEXT_W) - 2).join(" ", &Clean::ours(&CUT.to_string()));
                            c.text(r, TEXT_COL, &cut, DIALOG);
                        } else {
                            c.text(r, TEXT_COL, l, DIALOG);
                        }
                    }
                }
                Part::Gap(_) => {}
                Part::List(l) => l.draw(&mut c, row, focused),
                Part::Field(f) => f.draw(&mut c, row, focused),
                Part::View(v) => v.draw(&mut c, row, focused),
                Part::Gauge(g) => g.draw(&mut c, row),
                Part::Words(w) => w.draw(&mut c, row, focused),
            }
            row += ph;
        }
        // The notice, on the row above the buttons.
        if let Some(n) = self.notice.clone().or_else(|| self.hint()) {
            if !self.buttons.is_empty() && last >= 3 {
                c.text_in(last - 2, TEXT_COL, TEXT_W, &n, NOTE);
            }
        }
        if !self.buttons.is_empty() {
            self.draw_buttons(&mut c, last - 1);
        }
        let keys = self.chrome.keys.clone().unwrap_or_else(|| self.key_line());
        c.text(ROWS - 1, 0, &o(" ").join("", &keys).truncate(usize::from(COLS) - 1), BACKGROUND);
        c
    }

    /// A notice that comes from a part's state: a password with a
    /// character that isn't ASCII.
    fn hint(&self) -> Option<Clean> {
        self.parts.iter().find_map(|p| match p {
            Part::Field(f) if f.non_ascii() => {
                Some(Clean::ours("A key typed isn't plain ASCII: the keyboard must match the password's."))
            }
            _ => None,
        })
    }

    fn draw_buttons(&self, c: &mut Canvas, row: u16) {
        let alone = self.buttons.len() == 1;
        let texts: Vec<Clean> = self.buttons.iter().map(|b| button_text(&b.label.truncate(40), alone)).collect();
        let widths: u16 = texts.iter().map(|t| t.width() as u16).sum::<u16>();
        let between = texts.len() as u16 - 1;
        // Many buttons: the gaps narrow until the row fits inside the box.
        let gap = (1..=BUTTON_GAP).rev().find(|g| widths + g * between <= BOX_W - 4).unwrap_or(1);
        let total = widths + gap * between;
        let mut col = BOX_LEFT + BOX_W.saturating_sub(total) / 2;
        for (i, t) in texts.iter().enumerate() {
            let a = if self.focus == Focus::Button(i) { DIALOG.focus() } else { DIALOG };
            c.text(row, col, t, a);
            if self.focus == Focus::Button(i) {
                c.cursor = Some((row, col + 1));
            }
            col += t.width() as u16 + gap;
        }
    }

    /// The key line made from the dialog: what the arrows do, Tab, Enter,
    /// the buttons' letters, Esc, and the first screen's keys.
    pub fn key_line(&self) -> Clean {
        let mut k: Vec<String> = Vec::new();
        let mut lists = false;
        for p in &self.parts {
            match p {
                Part::List(l) if l.radio => {
                    k.push("Arrows: choose".into());
                    lists = true;
                }
                Part::List(_) => {
                    k.push("Arrows: move".into());
                    lists = true;
                }
                Part::View(_) => k.push("Arrows: line | Left/Right: sideways | Space: page".into()),
                _ => {}
            }
        }
        let targets = self.parts.iter().filter(|p| p.takes_focus()).count() + self.buttons.len();
        if targets > 1 || (self.focus == Focus::None && targets > 0) {
            k.push("Tab: next".into());
        }
        if !lists && self.buttons.len() > 1 && self.parts.iter().all(|p| !p.takes_focus()) {
            k.push("Left/Right: button".into());
        }
        // Enter presses the focused button, or the dialog's OK from a part.
        let enter = match self.focus {
            Focus::Button(i) => Some(i),
            Focus::Part(_) => self.default_button(),
            Focus::None => None,
        };
        if let Some(b) = enter.and_then(|i| self.buttons.get(i)) {
            k.push(format!("Enter: {}", b.label.as_str()));
        }
        for b in &self.buttons {
            if let Some(l) = b.key {
                k.push(format!("{l}: {}", b.label.as_str().to_lowercase()));
            }
        }
        if self.back().is_some() {
            k.push("Esc: back".into());
        } else if let Some(b) = self.esc.and_then(|i| self.buttons.get(i)) {
            k.push(format!("Esc: {}", b.label.as_str().to_lowercase()));
        }
        if self.chrome.first {
            k.push("L: lines | C: colour | P: plain".into());
        }
        // Too long for the row: the arrows' words go, then Tab's.
        let fits = |k: &[String]| k.join(" | ").chars().count() < usize::from(COLS) - 1;
        if !fits(&k) {
            for s in k.iter_mut().filter(|s| s.starts_with("Arrows: ") && !s.contains('|')) {
                *s = "Arrows".into();
            }
        }
        for drop in ["Tab: next", "Left/Right: button"] {
            if !fits(&k) {
                k.retain(|s| s != drop);
            }
        }
        // The labels are Clean already, and so is all we add.
        Clean::line(&k.join(" | "), usize::from(COLS))
    }

    /// The button Enter presses from a part: the first OK, else the first.
    fn default_button(&self) -> Option<usize> {
        if self.buttons.is_empty() {
            return None;
        }
        Some(self.buttons.iter().position(|b| b.act == Act::Ok).unwrap_or(0))
    }

    fn back(&self) -> Option<usize> {
        self.buttons.iter().position(|b| b.act == Act::Back)
    }

    /// The parts and buttons Tab visits, in order.
    fn targets(&self) -> Vec<Focus> {
        let mut v: Vec<Focus> =
            self.parts.iter().enumerate().filter(|(_, p)| p.takes_focus()).map(|(i, _)| Focus::Part(i)).collect();
        v.extend((0..self.buttons.len()).map(Focus::Button));
        v
    }

    /// Whether the focus is in a text field, where letters are typed.
    pub fn typing(&self) -> bool {
        matches!(self.focus, Focus::Part(i) if matches!(self.parts.get(i), Some(Part::Field(_) | Part::Words(_))))
    }

    pub fn key(&mut self, k: Key) -> Did {
        self.layout();
        let did = self.key_inner(k);
        if did != Did::Nothing {
            self.notice = None;
        }
        did
    }

    fn key_inner(&mut self, k: Key) -> Did {
        // A field takes every character, Space and Backspace.
        if let Focus::Part(i) = self.focus {
            let typed = match (&mut self.parts[i], k) {
                (Part::Field(f), Key::Char(_) | Key::Space | Key::Backspace) => Some(f.key(k)),
                (Part::Words(w), Key::Char(_) | Key::Space | Key::Backspace) => Some(w.key(k)),
                (Part::List(l), Key::Up | Key::Down) => Some(l.key(k)),
                (Part::View(v), Key::Up | Key::Down | Key::Left | Key::Right | Key::Space | Key::Backspace) => {
                    Some(v.key(k))
                }
                _ => None,
            };
            if let Some(changed) = typed {
                return if changed { Did::Changed } else { Did::Nothing };
            }
        }
        match k {
            Key::Tab => {
                let t = self.targets();
                if t.is_empty() {
                    return Did::Nothing;
                }
                let at = t.iter().position(|f| *f == self.focus);
                self.focus = match at {
                    Some(i) => t[(i + 1) % t.len()],
                    None => t[0],
                };
                Did::Changed
            }
            Key::Enter => match self.focus {
                Focus::Button(i) => Did::Press(i),
                Focus::Part(_) => self.default_button().map_or(Did::Nothing, Did::Press),
                Focus::None => Did::Nothing,
            },
            // Esc is Back; with no Back, the button the dialog names (a
            // question's No); in a text view with neither, it closes.
            Key::Esc => match self.back().or(self.esc) {
                Some(i) => Did::Press(i),
                None if self.is_view() => self.default_button().map_or(Did::Nothing, Did::Press),
                None => Did::Nothing,
            },
            Key::Left | Key::Right => match self.focus {
                Focus::Button(i) => {
                    let n = self.buttons.len();
                    self.focus = Focus::Button(if k == Key::Left { (i + n - 1) % n } else { (i + 1) % n });
                    Did::Changed
                }
                _ => Did::Nothing,
            },
            Key::Char(c) if c.is_ascii_alphabetic() => {
                let c = c.to_ascii_lowercase();
                match self.buttons.iter().position(|b| b.key == Some(c)) {
                    Some(i) => Did::Press(i),
                    None => Did::Letter(c),
                }
            }
            _ => Did::Nothing,
        }
    }

    pub fn list(&self) -> Option<&List> {
        self.parts.iter().find_map(|p| match p {
            Part::List(l) => Some(l),
            _ => None,
        })
    }

    pub fn field(&self) -> Option<&Field> {
        self.parts.iter().find_map(|p| match p {
            Part::Field(f) => Some(f),
            _ => None,
        })
    }

    pub fn field_mut(&mut self) -> Option<&mut Field> {
        self.parts.iter_mut().find_map(|p| match p {
            Part::Field(f) => Some(f),
            _ => None,
        })
    }

    /// A ticker's news: new text above the gauge, the bar and its line.
    pub fn set_progress(&mut self, text: Option<Clean>, pct: Option<u8>, line: Clean) {
        for p in &mut self.parts {
            match p {
                Part::Text(t) => {
                    if let Some(n) = &text {
                        *t = n.clone();
                    }
                }
                Part::Gauge(g) => g.set(pct, line.clone()),
                _ => {}
            }
        }
    }

    pub fn set_elapsed(&mut self, secs: u64) {
        for p in &mut self.parts {
            if let Part::Gauge(g) = p {
                g.elapsed_s = secs;
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::term::out::Lines;
    use crate::ui::{Menu, Message, Progress, Question, Radio, TextView};

    fn o(s: &str) -> Clean {
        Clean::ours(s)
    }

    fn items(n: usize) -> Vec<(Clean, Clean)> {
        (0..n).map(|i| (o(&format!("item{i}")), o(&format!("the {i}th")))).collect()
    }

    fn rows(c: &Canvas) -> Vec<String> {
        c.dump(Lines::Utf8, true).lines().take(24).map(|s| s.to_string()).collect()
    }

    #[test]
    fn buttons_are_padded_as_the_mocks_draw_them() {
        assert_eq!(button_text(&o("OK"), false).as_str(), "<  OK  >");
        assert_eq!(button_text(&o("Back"), false).as_str(), "< Back >");
        assert_eq!(button_text(&o("Close"), true).as_str(), "<  Close  >");
        assert_eq!(button_text(&o("Type a location"), false).as_str(), "< Type a location >");
    }

    #[test]
    fn the_box_sits_where_the_mocks_put_it() {
        // Machine's: 13 rows of parts, an 18-row box from row 4.
        let mut r = Radio::new(Chrome::ours("Machine", "Which machine is this?"), o("one\ntwo"), vec![o("a"), o("b"), o("c")], None);
        r.after = o("three\nfour\n\nfive");
        r.buttons.push(Button::shell());
        let mut d = Dialog::radio(&r);
        let c = d.canvas();
        let rows = rows(&c);
        assert!(rows[3].starts_with("  ┌") && rows[3].ends_with("┐  "), "{:?}", rows[3]);
        assert!(rows[20].starts_with("  └"), "{:?}", rows[20]);
        assert!(rows[0].starts_with(" BackOtter rescue") && rows[0].ends_with("Machine"));
        assert!(rows[8].starts_with("  │  ┌"), "the list's own frame at column 6: {:?}", rows[8]);
        assert!(rows[9].starts_with("  │  │ ( ) a"), "{:?}", rows[9]);
        assert!(rows[19].contains("<  OK  >    < Back >    < Shell >"), "{:?}", rows[19]);
        assert!(rows[23].starts_with(" Arrows: choose | Tab: next | Enter: OK | s: shell | Esc: back"), "{:?}", rows[23]);
    }

    #[test]
    fn many_buttons_narrow_their_gaps_to_fit_the_box() {
        let mut m = Message::new(Chrome::ours("a", "Problem"), o("x"));
        m.buttons = ["Network", "Try again", "Shell", "Report", "Raw output", "Back"].iter().map(|l| Button::other(l, None, 1)).collect();
        let r = rows(&Dialog::message(&m).canvas());
        let row = r.iter().find(|l| l.contains("< Network >")).unwrap();
        assert!(row.starts_with("  │") && row.trim_end().ends_with('│'), "inside the box: {row:?}");
        assert!(row.contains("< Network > < Try again > < Shell > < Report > < Raw output > < Back >") || row.contains("< Network >  < Try again >"), "{row:?}");
    }

    #[test]
    fn tab_cycles_the_body_and_each_button() {
        let mut d = Dialog::menu(&Menu::new(Chrome::ours("a", "b"), o("x"), items(3)));
        assert_eq!(d.focus, Focus::Part(2));
        for want in [Focus::Button(0), Focus::Button(1), Focus::Part(2)] {
            assert_eq!(d.key(Key::Tab), Did::Changed);
            assert_eq!(d.focus, want);
        }
        d.key(Key::Tab);
        assert_eq!(d.key(Key::Right), Did::Changed);
        assert_eq!(d.focus, Focus::Button(1));
        assert_eq!(d.key(Key::Right), Did::Changed);
        assert_eq!(d.focus, Focus::Button(0), "Right wraps");
        assert_eq!(d.key(Key::Left), Did::Changed);
        assert_eq!(d.focus, Focus::Button(1));
        assert_eq!(d.key(Key::Enter), Did::Press(1));
    }

    #[test]
    fn enter_on_the_body_presses_ok_and_esc_presses_back() {
        let mut m = Menu::new(Chrome::ours("a", "b"), o("x"), items(3));
        m.buttons = vec![Button::shell(), Button::ok(), Button::back()];
        let mut d = Dialog::menu(&m);
        assert_eq!(d.key(Key::Enter), Did::Press(1));
        assert_eq!(d.key(Key::Esc), Did::Press(2));
        assert_eq!(d.key(Key::Char('S')), Did::Press(0), "either case");
        assert_eq!(d.key(Key::Char('L')), Did::Letter('l'));
        assert_eq!(d.key(Key::Char('3')), Did::Nothing, "letters only, never a jump to a row");
        // No Back button: Esc does nothing.
        let mut d = Dialog::message(&Message::new(Chrome::ours("a", "b"), o("hello")));
        assert_eq!(d.key(Key::Esc), Did::Nothing);
        assert_eq!(d.key(Key::Enter), Did::Press(0));
    }

    #[test]
    fn letters_are_typed_in_a_field_and_commands_elsewhere() {
        let mut i = ui::Input::new(Chrome::ours("a", "b"), o("Where?"));
        i.buttons.push(Button::shell());
        let mut d = Dialog::input(&i);
        assert!(d.typing());
        assert_eq!(d.key(Key::Char('s')), Did::Changed);
        assert_eq!(d.field().map(|f| f.value_text()), Some("s".to_string()));
        d.key(Key::Tab);
        assert_eq!(d.key(Key::Char('s')), Did::Press(2));
    }

    #[test]
    fn a_question_starts_where_it_says() {
        let mut q = Question::new(Chrome::ours("a", "b"), o("Sure?"));
        let d = Dialog::question(&q);
        assert_eq!(d.focus, Focus::Button(0));
        q.no_first = true;
        let mut d = Dialog::question(&q);
        assert_eq!(d.focus, Focus::Button(1));
        assert_eq!(d.key(Key::Enter), Did::Press(1));
        assert_eq!(d.buttons[1].act, Act::Other(NO));
        assert_eq!(d.key(Key::Esc), Did::Nothing, "Esc does nothing unless asked");
        q.no = o("Keep going");
        q.esc_no = true;
        let mut d = Dialog::question(&q);
        d.key(Key::Left);
        assert_eq!(d.key(Key::Esc), Did::Press(1), "Esc is No wherever the focus is");
        assert!(d.key_line().as_str().ends_with("Esc: keep going"), "{}", d.key_line().as_str());
        q.buttons = vec![Button::back()];
        let mut d = Dialog::question(&q);
        assert_eq!(d.key(Key::Esc), Did::Press(2), "a Back button comes first");
    }

    #[test]
    fn the_key_line_names_what_enter_presses() {
        let enter = |d: &Dialog| {
            let k = d.key_line();
            k.as_str().split(" | ").find(|s| s.starts_with("Enter: ")).map(str::to_string)
        };
        let mut q = Question::new(Chrome::ours("a", "b"), o("Sure?"));
        assert_eq!(enter(&Dialog::question(&q)).as_deref(), Some("Enter: Yes"));
        q.no_first = true;
        let mut d = Dialog::question(&q);
        assert_eq!(enter(&d).as_deref(), Some("Enter: No"), "the focus is on No");
        d.key(Key::Left);
        assert_eq!(enter(&d).as_deref(), Some("Enter: Yes"), "it follows Left and Right");
        let r = rows(&d.canvas());
        assert!(r[23].contains("Enter: Yes"), "and the canvas's key line: {:?}", r[23]);
        let mut d = Dialog::menu(&Menu::new(Chrome::ours("a", "b"), o("x"), items(3)));
        assert_eq!(enter(&d).as_deref(), Some("Enter: OK"), "from the list, the dialog's OK");
        d.key(Key::Tab);
        assert_eq!(enter(&d).as_deref(), Some("Enter: OK"));
        d.key(Key::Tab);
        assert_eq!(enter(&d).as_deref(), Some("Enter: Back"));
        let p = Progress::new(Chrome::ours("Restore", "Restoring"), o("stages"));
        assert_eq!(enter(&Dialog::progress(&p)), None, "no focus: Enter does nothing");
    }

    #[test]
    fn long_text_gives_way_to_the_list() {
        // A problem's text may be 2 KiB: here it wraps to well over 15
        // lines, above a radio list of 6 disks.
        let long = o(&"The disk you picked is smaller than the one it was backed up from.  ".repeat(16));
        let disks: Vec<Clean> = (0..6).map(|i| o(&format!("disk{i}  500 GB"))).collect();
        let mut r = Radio::new(Chrome::ours("Disks", "Which disk?"), long, disks, Some(0));
        r.buttons.push(Button::shell());
        let mut d = Dialog::radio(&r);
        for down in 0..=5 {
            let rs = rows(&d.canvas());
            let bottom = rs.iter().position(|l| l.starts_with("  └")).expect("the bottom border");
            assert!(rs[bottom].trim_end().ends_with('┘'), "the border is whole: {:?}", rs[bottom]);
            assert!(rs[bottom - 1].contains("<  OK  >    < Back >    < Shell >"), "{:?}", rs[bottom - 1]);
            let notice: String = rs[bottom - 2].chars().skip(3).collect();
            assert!(rs[bottom - 2].starts_with("  │") && notice.trim() == "│", "the notice row is clear: {:?}", rs[bottom - 2]);
            assert!(rs[23].starts_with(" Arrows: choose"), "the key line: {:?}", rs[23]);
            let chosen = format!("(*) disk{down}");
            let at = rs.iter().position(|l| l.contains(&chosen)).expect("the chosen row shows");
            assert!(at < bottom - 2, "above the buttons: row {at}");
            let cut = |l: &String| l.chars().skip(usize::from(TEXT_COL)).take(usize::from(TEXT_W)).collect::<String>().trim_end().ends_with(" >");
            assert!(rs.iter().any(cut), "the text is cut with >");
            assert!(d.heights.iter().sum::<u16>() <= MAX_BODY);
            d.key(Key::Down);
        }
    }

    #[test]
    fn progress_has_no_focus_until_tab() {
        let mut p = Progress::new(Chrome::ours("Restore", "Restoring"), o("stages"));
        p.buttons = vec![Button::other("Cancel", None, 1)];
        let mut d = Dialog::progress(&p);
        assert_eq!(d.key(Key::Enter), Did::Nothing, "Enter never asks to stop");
        assert_eq!(d.key(Key::Esc), Did::Nothing);
        d.key(Key::Tab);
        assert_eq!(d.key(Key::Enter), Did::Press(0));
        assert!(d.canvas().cursor.is_some());
    }

    #[test]
    fn a_long_list_shares_the_box_and_scrolls() {
        let mut d = Dialog::menu(&Menu::new(Chrome::ours("a", "b"), o("x"), items(40)));
        let c = d.canvas();
        let r = rows(&c);
        // 17 rows less the text, its gap, the frame: 13 list rows.
        assert_eq!(d.list().map(|l| l.window), Some(13));
        assert!(r.iter().any(|l| l.contains("v more below")));
        assert!(!r.iter().any(|l| l.contains("^ more above")));
        for _ in 0..20 {
            d.key(Key::Down);
        }
        let r = rows(&d.canvas());
        assert!(r.iter().any(|l| l.contains("^ more above")) && r.iter().any(|l| l.contains("item20")));
    }

    #[test]
    fn a_view_box_has_no_blank_row_above_its_text() {
        let lines: Vec<Clean> = (0..40).map(|i| o(&format!("line {i}"))).collect();
        let mut d = Dialog::view(&TextView::new(Chrome::ours("Review", "Every command, in order"), lines));
        let r = rows(&d.canvas());
        assert!(r[1].starts_with("  ┌"), "{:?}", r[1]);
        assert!(r[2].starts_with("  │ line 0"), "{:?}", r[2]);
        assert!(r[18].contains("v more below"), "{:?}", r[18]);
        assert!(r[20].contains("<  Close  >"));
        assert!(r[21].starts_with("  └"));
    }
}
