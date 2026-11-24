//! The text box and the password box: one line in brackets, typing and
//! Backspace only (D9).  A value longer than the field scrolls sideways:
//! its end shows, after a `<`.  The password box keeps its value in a
//! `Secret` and shows one `*` per character (D8); no cell ever holds the
//! secret itself.
//!
//! The insertion point is a cell left out of the focus's reverse video,
//! so it shows where the next character goes even where the console
//! hides the cursor; where it can't, the cursor is parked there.

use crate::screen::{Canvas, DIALOG};
use crate::secret::Secret;
use crate::term::keys::Key;
use crate::text::{cap, Clean};

use super::TEXT_COL;

/// Cells inside the brackets.
pub const FIELD_W: u16 = 66;

enum Value {
    Text(String),
    Secret(Secret),
}

pub struct Field {
    value: Value,
    /// The most characters it takes.
    max: usize,
}

impl Field {
    /// A text box starting with `value` (cut to `max` characters, any
    /// control character dropped).
    pub fn text(value: &str, max: usize) -> Field {
        let v: String = value.chars().filter(|c| !c.is_control()).take(max).collect();
        Field { value: Value::Text(v), max }
    }

    pub fn password() -> Field {
        Field { value: Value::Secret(Secret::new()), max: crate::secret::CAP }
    }

    pub fn is_password(&self) -> bool {
        matches!(self.value, Value::Secret(_))
    }

    /// Characters typed.
    pub fn count(&self) -> usize {
        match &self.value {
            Value::Text(s) => s.chars().count(),
            Value::Secret(s) => s.chars(),
        }
    }

    /// The text box's value (empty for a password box).
    pub fn value_text(&self) -> String {
        match &self.value {
            Value::Text(s) => s.clone(),
            Value::Secret(_) => String::new(),
        }
    }

    /// The password, the field left empty.
    pub fn take_secret(&mut self) -> Secret {
        match &mut self.value {
            Value::Secret(s) => std::mem::take(s),
            Value::Text(_) => Secret::new(),
        }
    }

    pub fn non_ascii(&self) -> bool {
        matches!(&self.value, Value::Secret(s) if s.non_ascii())
    }

    /// A character, Space or Backspace; true when the value changed.
    /// Control characters never arrive as `Char` (the decoder drops
    /// them), and are refused here too.
    pub fn key(&mut self, k: Key) -> bool {
        let c = match k {
            Key::Char(c) if !c.is_control() => c,
            Key::Space => ' ',
            Key::Backspace => {
                return match &mut self.value {
                    Value::Text(s) => s.pop().is_some(),
                    Value::Secret(s) => s.pop_char(),
                }
            }
            _ => return false,
        };
        if self.count() >= self.max {
            return false;
        }
        match &mut self.value {
            Value::Text(s) => {
                s.push(c);
                true
            }
            Value::Secret(s) => s.push_char(c),
        }
    }

    /// `[`, the field, `]` at `row`.
    pub fn draw(&self, c: &mut Canvas, row: u16, focused: bool) {
        let o = Clean::ours;
        c.text(row, TEXT_COL, &o("["), DIALOG);
        c.text(row, TEXT_COL + 1 + FIELD_W, &o("]"), DIALOG);
        let w = usize::from(FIELD_W);
        // Room for the characters: the field less the insertion cell.
        let room = w - 1;
        let shown: Clean = match &self.value {
            Value::Text(s) => Clean::line(s, cap::LINE),
            Value::Secret(s) => o(&"*".repeat(s.chars().min(w))),
        };
        let n = shown.width();
        let visible = if n <= room {
            shown
        } else {
            let tail: String = shown.chars().skip(n - (room - 1)).collect();
            o("<").join("", &Clean::line(&tail, w))
        };
        let a = if focused { DIALOG.focus() } else { DIALOG };
        c.fill(row, TEXT_COL + 1, 1, FIELD_W, a);
        let at = c.text(row, TEXT_COL + 1, &visible, a);
        if focused {
            let col = TEXT_COL + 1 + at;
            c.fill(row, col, 1, 1, DIALOG);
            c.cursor = Some((row, col));
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::term::out::Lines;

    fn row(f: &Field, focused: bool) -> (String, String) {
        let mut c = Canvas::new(1, 80, DIALOG);
        f.draw(&mut c, 0, focused);
        let d = c.dump(Lines::Utf8, true);
        let mut l = d.lines();
        (l.next().unwrap_or("").to_string(), l.nth(1).unwrap_or("").to_string())
    }

    #[test]
    fn typing_and_backspace() {
        let mut f = Field::text("", 5);
        for c in "héllo!".chars() {
            f.key(Key::Char(c));
        }
        assert_eq!(f.value_text(), "héllo", "the length limit");
        assert!(f.key(Key::Backspace));
        f.key(Key::Space);
        assert_eq!(f.value_text(), "héll ");
        assert!(!f.key(Key::Left), "no moving within the field (D9)");
        assert!(!f.key(Key::Char('\u{7}')));
        let (glyphs, attrs) = row(&f, true);
        assert!(glyphs.starts_with("    [héll  "), "{glyphs:?}");
        // The insertion point: one cell out of the reverse video.
        assert_eq!(&attrs[5..12], "DDDDDdD", "{attrs:?}");
    }

    #[test]
    fn a_long_value_scrolls_sideways() {
        let v: String = (0..100).map(|i| char::from(b'a' + (i % 26) as u8)).collect();
        let f = Field::text(&v, 256);
        let (glyphs, _) = row(&f, true);
        let inside = &glyphs[5..5 + 66];
        assert!(inside.starts_with('<'));
        assert!(inside.trim_end().ends_with(&v[v.len() - 10..]), "the end shows: {inside:?}");
        assert_eq!(&glyphs[71..72], "]");
    }

    #[test]
    fn the_password_box_shows_stars_and_keeps_bytes() {
        let mut f = Field::password();
        for c in "pä ss".chars() {
            f.key(if c == ' ' { Key::Space } else { Key::Char(c) });
        }
        assert_eq!(f.count(), 5);
        assert!(f.non_ascii());
        let (glyphs, _) = row(&f, false);
        assert!(glyphs.starts_with("    [*****      "), "{glyphs:?}");
        assert!(!glyphs.contains('p'));
        f.key(Key::Backspace);
        let s = f.take_secret();
        assert_eq!(s.expose(), "pä s".as_bytes());
        assert_eq!(f.count(), 0, "taken");
    }

    #[test]
    fn a_hostile_value_is_cleaned_when_shown() {
        let f = Field::text("a\u{202e}b中", 256);
        let (glyphs, _) = row(&f, false);
        assert!(glyphs.starts_with("    [a?b?"), "{glyphs:?}");
    }
}
