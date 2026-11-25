//! The recovery key's twelve word fields (rescue-screens.md, "Recovery
//! key"): rows of three, as the recovery sheet prints them, and a line
//! under them for the completion or the word that can't be on the list.
//!
//! - Letters type into the word with the focus (a-z, either case; any
//!   other character is ignored).  Space takes the completion, or the
//!   letters as typed when they make a whole word already, and moves to
//!   the next word.  Backspace in an empty field goes back a word.  Tab
//!   is the dialog's: it leaves the fields for the buttons.
//! - The completion shows on its own line, never inside the field, so it
//!   can't be taken for typed letters with colour off or in ASCII.
//! - The words are shown in clear (they are read off paper) but kept in
//!   fixed buffers wiped on drop, and handed on as one `Secret`.

use crate::bip39::{self, Typed, COUNT, WORD_MAX};
use crate::screen::{Canvas, DIALOG};
use crate::secret::{self, Secret};
use crate::term::keys::Key;
use crate::text::Clean;

use super::{NOTE, TEXT_COL};

/// Cells inside a word's brackets: the longest word and the insertion
/// point.
pub const CELL_W: u16 = WORD_MAX as u16 + 1;
/// A word's number, right-aligned in this many cells, then ` [`.
const NUM_W: usize = 4;
/// From one word's number to the next one's.
const STRIDE: u16 = NUM_W as u16 + 2 + CELL_W + 1 + 3;
/// Words a row.
pub const PER_ROW: usize = 3;
/// Rows the part takes: four of words, a blank one, the line under.
pub const HEIGHT: u16 = (COUNT / PER_ROW) as u16 + 2;

pub struct Words {
    letters: [[u8; WORD_MAX]; COUNT],
    lens: [u8; COUNT],
    /// The word with the focus.
    pub cur: usize,
}

impl Default for Words {
    fn default() -> Self {
        Words::new()
    }
}

impl Drop for Words {
    fn drop(&mut self) {
        for w in &mut self.letters {
            secret::wipe(w);
        }
        self.lens = [0; COUNT];
    }
}

impl Words {
    pub fn new() -> Words {
        Words { letters: [[0; WORD_MAX]; COUNT], lens: [0; COUNT], cur: 0 }
    }

    /// Word `i`'s letters as typed.
    pub fn word(&self, i: usize) -> &str {
        let n = usize::from(self.lens[i]);
        // Only a-z is ever stored.
        std::str::from_utf8(&self.letters[i][..n]).unwrap_or("")
    }

    fn set(&mut self, i: usize, w: &str) {
        secret::wipe(&mut self.letters[i]);
        let n = w.len().min(WORD_MAX);
        self.letters[i][..n].copy_from_slice(&w.as_bytes()[..n]);
        self.lens[i] = n as u8;
    }

    /// A key; true when something changed.
    pub fn key(&mut self, k: Key) -> bool {
        let i = self.cur;
        match k {
            Key::Char(c) if c.is_ascii_alphabetic() => {
                let n = usize::from(self.lens[i]);
                if n >= WORD_MAX {
                    return false;
                }
                self.letters[i][n] = c.to_ascii_lowercase() as u8;
                self.lens[i] += 1;
                true
            }
            Key::Space => match bip39::typed(self.word(i)) {
                Typed::Whole(w) | Typed::Completes(w) => {
                    self.set(i, w);
                    if i + 1 < COUNT {
                        self.cur += 1;
                    }
                    true
                }
                _ => false,
            },
            Key::Backspace => {
                if self.lens[i] > 0 {
                    self.lens[i] -= 1;
                    self.letters[i][usize::from(self.lens[i])] = 0;
                    true
                } else if i > 0 {
                    self.cur -= 1;
                    true
                } else {
                    false
                }
            }
            _ => false,
        }
    }

    /// What the line under the fields says: the completion of the word
    /// with the focus, or the first word that can't be on the list.
    pub fn line(&self, focused: bool) -> Clean {
        let i = self.cur;
        if focused {
            match bip39::typed(self.word(i)) {
                Typed::Completes(w) => {
                    let then = if i + 1 < COUNT {
                        format!("Space takes it and moves to word {}.", i + 2)
                    } else {
                        "Space takes it.".to_string()
                    };
                    return Clean::ours(&format!("Word {}: {} -> {w}.  {then}", i + 1, self.word(i)));
                }
                Typed::Unknown => return Clean::ours(&format!("Word {} isn't one of the recovery words.", i + 1)),
                _ => {}
            }
        }
        match (0..COUNT).find(|&j| bip39::typed(self.word(j)) == Typed::Unknown) {
            Some(j) => Clean::ours(&format!("Word {} isn't one of the recovery words.", j + 1)),
            None => Clean::default(),
        }
    }

    /// The twelve words, each completed, one space between, in a
    /// `Secret`; or why not yet, in words.
    pub fn take(&self) -> Result<Secret, Clean> {
        let mut s = Secret::new();
        for i in 0..COUNT {
            let w = self.word(i);
            let full = match bip39::typed(w) {
                Typed::Whole(x) | Typed::Completes(x) => x,
                Typed::Empty => return Err(Clean::ours(&format!("Type all {COUNT} words first: word {} is empty.", i + 1))),
                Typed::Unknown => return Err(Clean::ours(&format!("Word {} isn't one of the recovery words.", i + 1))),
                Typed::Partial => {
                    let some: Vec<&str> = bip39::starting(w).iter().take(3).copied().collect();
                    return Err(Clean::ours(&format!("Word {}: type more of it ({w}: {}).", i + 1, some.join(", "))));
                }
            };
            if i > 0 {
                s.push_byte(b' ');
            }
            for b in full.bytes() {
                s.push_byte(b);
            }
        }
        Ok(s)
    }

    pub fn draw(&self, c: &mut Canvas, row: u16, focused: bool) {
        let o = Clean::ours;
        for i in 0..COUNT {
            let r = row + (i / PER_ROW) as u16;
            let col = TEXT_COL + (i % PER_ROW) as u16 * STRIDE;
            c.text(r, col, &o(&format!("{:>w$} [", i + 1, w = NUM_W)), DIALOG);
            let f = col + NUM_W as u16 + 2;
            c.text(r, f + CELL_W, &o("]"), DIALOG);
            let here = focused && i == self.cur;
            let a = if here { DIALOG.focus() } else { DIALOG };
            c.fill(r, f, 1, CELL_W, a);
            let at = c.text(r, f, &o(self.word(i)), a);
            if here {
                c.fill(r, f + at, 1, 1, DIALOG);
                c.cursor = Some((r, f + at));
            }
        }
        let line = self.line(focused);
        if !line.is_empty() {
            c.text(row + HEIGHT - 1, TEXT_COL, &line, NOTE);
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn typing(w: &mut Words, s: &str) {
        for ch in s.chars() {
            w.key(if ch == ' ' { Key::Space } else { Key::Char(ch) });
        }
    }

    #[test]
    fn completion_space_and_backspace() {
        let mut w = Words::new();
        typing(&mut w, "harv ");
        assert_eq!((w.word(0), w.cur), ("harvest", 1));
        typing(&mut w, "act ");
        assert_eq!((w.word(1), w.cur), ("act", 2), "a whole word that starts longer ones");
        typing(&mut w, "ribb");
        assert_eq!(w.line(true).as_str(), "Word 3: ribb -> ribbon.  Space takes it and moves to word 4.");
        typing(&mut w, "x");
        assert_eq!(w.line(true).as_str(), "Word 3 isn't one of the recovery words.");
        assert!(!w.key(Key::Space), "an unknown word isn't taken");
        for _ in 0..5 {
            w.key(Key::Backspace);
        }
        assert_eq!((w.word(2), w.cur), ("", 2));
        assert!(w.key(Key::Backspace));
        assert_eq!(w.cur, 1, "Backspace in an empty field goes back a word");
        assert!(!w.key(Key::Char('7')), "only letters");
        typing(&mut w, "ZZZZZZZZZZ");
        assert_eq!(w.word(1).len(), WORD_MAX, "at most the longest word");
    }

    #[test]
    fn take_needs_twelve_list_words() {
        let mut w = Words::new();
        assert!(w.take().err().unwrap().as_str().contains("word 1 is empty"));
        typing(&mut w, "legal winner thank year wave sausage worth useful legal winner thank yell");
        let s = w.take().ok().unwrap();
        assert_eq!(s.expose(), b"legal winner thank year wave sausage worth useful legal winner thank yellow");
        w.cur = 10;
        for _ in 0..5 {
            w.key(Key::Backspace);
        }
        typing(&mut w, "tha");
        assert_eq!(w.take().err().unwrap().as_str(), "Word 11: type more of it (tha: thank, that).");
    }

    #[test]
    fn it_draws_as_the_mock_does() {
        let mut w = Words::new();
        typing(&mut w, "harvest orbit velvet cannon noble tiger quantum elbow ribb");
        let mut c = Canvas::new(6, 80, DIALOG);
        w.draw(&mut c, 0, true);
        let d = c.dump(crate::term::out::Lines::Utf8, false);
        let rows: Vec<&str> = d.lines().collect();
        assert_eq!(&rows[0][..58], "       1 [harvest  ]      2 [orbit    ]      3 [velvet   ]");
        assert_eq!(&rows[3][..58], "      10 [         ]     11 [         ]     12 [         ]");
        assert!(rows[5].contains("Word 9: ribb -> ribbon.  Space takes it and moves to word 10."));
    }
}
