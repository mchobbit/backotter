//! BIP39's English word list, for the recovery key screen's completion
//! (rescue-screens.md, "Recovery key"): a word filled in after four
//! letters, and a word that can't be on the list named as it is typed.
//!
//! The list isn't secret and is fixed by BIP39.  holt-json.md's open
//! question 9 (where bko-rescue gets it) is unanswered; this takes the
//! list as a copy of holt's own `holt/src/bip39-english.txt` beside this
//! file, so a build still needs only `rescue/` and `holt/src/json.rs`,
//! and a test checks the copy is holt's, byte for byte.  The check of a
//! whole key stays in bko-holt (`recovery-words OUT --json`, J21): this
//! only completes and names words.

use std::sync::OnceLock;

/// How many words a recovery key has.
pub const COUNT: usize = 12;

/// The longest word on the list.
pub const WORD_MAX: usize = 8;

/// Letters that pick a word: BIP39's words differ in their first four.
pub const UNIQUE: usize = 4;

const LIST_TEXT: &str = include_str!("bip39-english.txt");

/// The 2048 words, in the list's (alphabetical) order.
pub fn list() -> &'static [&'static str] {
    static LIST: OnceLock<Vec<&'static str>> = OnceLock::new();
    LIST.get_or_init(|| LIST_TEXT.lines().collect())
}

/// The words starting with `prefix` (lower case a-z).
pub fn starting(prefix: &str) -> &'static [&'static str] {
    let l = list();
    let from = l.partition_point(|w| *w < prefix);
    let to = from + l[from..].iter().take_while(|w| w.starts_with(prefix)).count();
    &l[from..to]
}

/// What the letters typed so far say.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Typed {
    /// Nothing typed.
    Empty,
    /// No word starts with these letters: it can't be on the list.
    Unknown,
    /// A whole word already (`act`, `zoo`, or all of `harvest`).
    Whole(&'static str),
    /// Four letters or more that pick one word: `ribb` -> `ribbon`.
    Completes(&'static str),
    /// The start of more than one word, or fewer than four letters.
    Partial,
}

pub fn typed(letters: &str) -> Typed {
    if letters.is_empty() {
        return Typed::Empty;
    }
    let ws = starting(letters);
    if ws.is_empty() {
        return Typed::Unknown;
    }
    if let Some(w) = ws.iter().find(|w| **w == letters) {
        return Typed::Whole(w);
    }
    if letters.len() >= UNIQUE && ws.len() == 1 {
        return Typed::Completes(ws[0]);
    }
    Typed::Partial
}

/// The word a typed word stands for, as bko-holt reads it: the word
/// itself, or four letters or more that pick one.
pub fn complete(letters: &str) -> Option<&'static str> {
    match typed(letters) {
        Typed::Whole(w) | Typed::Completes(w) => Some(w),
        _ => None,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_list_is_bip39s_and_holts() {
        let l = list();
        assert_eq!(l.len(), 2048);
        assert!(l.windows(2).all(|w| w[0] < w[1]), "sorted, no duplicates");
        assert!(l.iter().all(|w| (3..=WORD_MAX).contains(&w.len()) && w.bytes().all(|b| b.is_ascii_lowercase())));
        // Unique in their first four letters.
        let mut fours: Vec<&str> = l.iter().map(|w| &w[..w.len().min(UNIQUE)]).collect();
        fours.dedup();
        assert_eq!(fours.len(), 2048);
        assert_eq!((l[0], l[2047]), ("abandon", "zoo"));
        // The copy is holt's own (D2: shared, never edited apart).
        let holt = concat!(env!("CARGO_MANIFEST_DIR"), "/../holt/src/bip39-english.txt");
        match std::fs::read_to_string(holt) {
            Ok(h) => assert_eq!(h, LIST_TEXT, "src/bip39-english.txt isn't holt's: copy holt/src/bip39-english.txt"),
            Err(e) => eprintln!("{holt}: {e}: the copy isn't compared (build from a repo-shaped tree with holt/src to)"),
        }
    }

    #[test]
    fn completion_and_unknown_words() {
        assert_eq!(typed(""), Typed::Empty);
        assert_eq!(typed("ribb"), Typed::Completes("ribbon"));
        assert_eq!(typed("ri"), Typed::Partial);
        assert_eq!(typed("rib"), Typed::Whole("rib"), "a word, and the start of ribbon");
        assert_eq!(typed("act"), Typed::Whole("act"), "a whole word that starts longer ones");
        assert_eq!(typed("zoo"), Typed::Whole("zoo"));
        assert_eq!(typed("harvest"), Typed::Whole("harvest"));
        assert_eq!(typed("quax"), Typed::Unknown);
        assert_eq!(typed("ribbx"), Typed::Unknown);
        assert_eq!(complete("harv"), Some("harvest"));
        assert_eq!(complete("tha"), None, "thank, that: not a word yet");
        assert_eq!(starting("tha"), &["thank", "that"]);
    }
}
