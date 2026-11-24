//! Bytes to keys: a pure state machine, with no I/O, so it can be tested
//! and fuzzed alone.  The caller feeds every byte read, and calls `timeout`
//! when nothing more came within the Esc timeout while `pending` is true
//! (`event.rs` does both).
//!
//! - Arrows: `ESC [ A`-`D` and `ESC O A`-`D`, and their modifier forms
//!   (`ESC [ 1 ; 5 A`, `ESC O 5 A`) as the plain arrow.
//! - Enter: CR or LF, CR LF as one; keypad Enter `ESC O M` too.
//!   Backspace: 0x7F or 0x08.  Tab 0x09, Space 0x20.
//! - Esc: an ESC with nothing after it within the timeout.  ESC then any
//!   byte that doesn't start a sequence is Esc, then that byte's key.
//! - Any other CSI or SS3 sequence is read to its final byte and dropped,
//!   at most MAX_SEQ bytes; a longer one is dropped and the decoder starts
//!   again from the next byte.
//! - Other control bytes are dropped.  Printable bytes, and whole UTF-8
//!   sequences of non-control characters, are `Char`s.

/// The keys bko-rescue takes.  Text and password boxes take every `Char`.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Key {
    Up,
    Down,
    Left,
    Right,
    Tab,
    Enter,
    Esc,
    Space,
    Backspace,
    Char(char),
}

impl Key {
    /// A short name, for the spike's key log.  A `Char` is its character,
    /// which the caller still sanitises before showing.
    pub fn name(self) -> String {
        match self {
            Key::Up => "Up".into(),
            Key::Down => "Down".into(),
            Key::Left => "Left".into(),
            Key::Right => "Right".into(),
            Key::Tab => "Tab".into(),
            Key::Enter => "Enter".into(),
            Key::Esc => "Esc".into(),
            Key::Space => "Space".into(),
            Key::Backspace => "BS".into(),
            Key::Char(c) => c.to_string(),
        }
    }
}

/// The longest escape sequence read; a longer one is dropped and the
/// decoder starts again.
pub const MAX_SEQ: usize = 32;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum State {
    Ground,
    /// ESC seen.
    Esc,
    /// ESC [ seen, then `len` more bytes.
    Csi,
    /// ESC O seen, then `len` more bytes.
    Ss3,
    /// A UTF-8 lead byte seen: `need` more continuation bytes.
    Utf8 { need: u8, len: u8 },
}

pub struct Decoder {
    state: State,
    seq: [u8; MAX_SEQ],
    len: usize,
    cp: u32,
    after_cr: bool,
}

impl Default for Decoder {
    fn default() -> Self {
        Decoder::new()
    }
}

impl Decoder {
    pub fn new() -> Decoder {
        Decoder { state: State::Ground, seq: [0; MAX_SEQ], len: 0, cp: 0, after_cr: false }
    }

    /// True while a sequence is half read: the caller then waits only the
    /// Esc timeout before calling `timeout`.
    pub fn pending(&self) -> bool {
        self.state != State::Ground
    }

    /// Nothing more came: a lone ESC is Esc, anything else half read is
    /// dropped.
    pub fn timeout(&mut self, out: &mut Vec<Key>) {
        if self.state == State::Esc {
            out.push(Key::Esc);
        }
        self.reset();
    }

    fn reset(&mut self) {
        self.state = State::Ground;
        self.len = 0;
        self.cp = 0;
    }

    pub fn feed(&mut self, b: u8, out: &mut Vec<Key>) {
        let after_cr = std::mem::take(&mut self.after_cr);
        match self.state {
            State::Ground => self.ground(b, after_cr, out),
            State::Esc => match b {
                b'[' => {
                    self.state = State::Csi;
                    self.len = 0;
                }
                b'O' => {
                    self.state = State::Ss3;
                    self.len = 0;
                }
                0x1b => out.push(Key::Esc),
                _ => {
                    // ESC then a key (no Alt keys here): Esc, then the key.
                    out.push(Key::Esc);
                    self.reset();
                    self.ground(b, false, out);
                }
            },
            State::Csi | State::Ss3 => {
                if !(0x20..=0x7e).contains(&b) {
                    // Not part of a sequence: drop what was read, start again.
                    self.reset();
                    self.ground(b, false, out);
                } else if (0x40..=0x7e).contains(&b) {
                    let params = &self.seq[..self.len];
                    let k = if self.state == State::Csi { csi_key(params, b) } else { ss3_key(params, b) };
                    if let Some(k) = k {
                        out.push(k);
                    }
                    self.reset();
                } else if self.len + 2 >= MAX_SEQ {
                    // ESC, [ or O, and this byte make it longer than MAX_SEQ.
                    self.reset();
                } else {
                    self.seq[self.len] = b;
                    self.len += 1;
                }
            }
            State::Utf8 { need, len } => {
                if b & 0xc0 != 0x80 {
                    self.reset();
                    self.ground(b, false, out);
                    return;
                }
                self.cp = (self.cp << 6) | u32::from(b & 0x3f);
                if need > 1 {
                    self.state = State::Utf8 { need: need - 1, len };
                    return;
                }
                let min = match len {
                    2 => 0x80,
                    3 => 0x800,
                    _ => 0x1_0000,
                };
                if self.cp >= min {
                    if let Some(c) = char::from_u32(self.cp) {
                        if !c.is_control() {
                            out.push(Key::Char(c));
                        }
                    }
                }
                self.reset();
            }
        }
    }

    fn ground(&mut self, b: u8, after_cr: bool, out: &mut Vec<Key>) {
        match b {
            0x1b => self.state = State::Esc,
            b'\r' => {
                out.push(Key::Enter);
                self.after_cr = true;
            }
            b'\n' if after_cr => {}
            b'\n' => out.push(Key::Enter),
            0x7f | 0x08 => out.push(Key::Backspace),
            b'\t' => out.push(Key::Tab),
            b' ' => out.push(Key::Space),
            0x21..=0x7e => out.push(Key::Char(char::from(b))),
            0xc2..=0xdf => self.lead(b & 0x1f, 1, 2),
            0xe0..=0xef => self.lead(b & 0x0f, 2, 3),
            0xf0..=0xf4 => self.lead(b & 0x07, 3, 4),
            // Other control bytes, stray continuation bytes, bad leads.
            _ => {}
        }
    }

    fn lead(&mut self, bits: u8, need: u8, len: u8) {
        self.cp = u32::from(bits);
        self.state = State::Utf8 { need, len };
    }
}

fn arrow(b: u8) -> Option<Key> {
    match b {
        b'A' => Some(Key::Up),
        b'B' => Some(Key::Down),
        b'C' => Some(Key::Right),
        b'D' => Some(Key::Left),
        _ => None,
    }
}

/// `ESC [ A`, and the modifier form `ESC [ 1 ; 5 A`, are arrows; every
/// other CSI sequence is dropped.
fn csi_key(params: &[u8], fin: u8) -> Option<Key> {
    let plain = params.is_empty()
        || (params.len() > 2
            && params.starts_with(b"1;")
            && params[2..].iter().all(u8::is_ascii_digit));
    if plain {
        arrow(fin)
    } else {
        None
    }
}

/// `ESC O A`, the old modifier form `ESC O 5 A`, and keypad Enter
/// `ESC O M` (a keypad left in application mode); the rest is dropped.
fn ss3_key(params: &[u8], fin: u8) -> Option<Key> {
    if !params.iter().all(u8::is_ascii_digit) {
        return None;
    }
    match fin {
        b'M' => Some(Key::Enter),
        _ => arrow(fin),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn keys(bytes: &[u8]) -> Vec<Key> {
        let mut d = Decoder::new();
        let mut out = Vec::new();
        for &b in bytes {
            d.feed(b, &mut out);
        }
        d.timeout(&mut out);
        out
    }

    use crate::testkit::{fuzz_cases, Rng};
    use Key::*;

    #[test]
    fn arrows_in_both_forms_and_with_modifiers() {
        assert_eq!(keys(b"\x1b[A\x1b[B\x1b[C\x1b[D"), [Up, Down, Right, Left]);
        assert_eq!(keys(b"\x1bOA\x1bOB\x1bOC\x1bOD"), [Up, Down, Right, Left]);
        assert_eq!(keys(b"\x1b[1;5A\x1b[1;2D"), [Up, Left]);
        assert_eq!(keys(b"\x1bO5A\x1bO2C"), [Up, Right]);
        assert_eq!(keys(b"\x1bOM"), [Enter]);
        // Not arrows: other parameters, an SS3 with a ';', other finals.
        assert_eq!(keys(b"\x1b[2;5A\x1bO1;5B\x1bOPx"), [Char('x')]);
    }

    #[test]
    fn enter_backspace_tab_space() {
        assert_eq!(keys(b"\r\n"), [Enter]);
        assert_eq!(keys(b"\r\r"), [Enter, Enter]);
        assert_eq!(keys(b"\n\n"), [Enter, Enter]);
        assert_eq!(keys(b"\r\n\n"), [Enter, Enter]);
        assert_eq!(keys(b"\x7f\x08\t "), [Backspace, Backspace, Tab, Space]);
    }

    #[test]
    fn esc_alone_needs_the_timeout() {
        let mut d = Decoder::new();
        let mut out = Vec::new();
        d.feed(0x1b, &mut out);
        assert!(out.is_empty() && d.pending());
        d.timeout(&mut out);
        assert_eq!(out, [Esc]);
        assert!(!d.pending());
        assert_eq!(keys(b"\x1b\x1b"), [Esc, Esc]);
        assert_eq!(keys(b"\x1bq"), [Esc, Char('q')]);
        assert_eq!(keys(b"\x1b\r"), [Esc, Enter]);
    }

    #[test]
    fn other_sequences_are_dropped() {
        // Delete, F1 (both forms), F5, Shift-Tab, a mouse report's start.
        assert_eq!(keys(b"\x1b[3~x\x1bOPy\x1b[11~\x1b[15~\x1b[Zz"), [Char('x'), Char('y'), Char('z')]);
        assert_eq!(keys(b"\x1b[2A"), []);
        // A control byte inside a sequence ends it; the byte counts afresh.
        assert_eq!(keys(b"\x1b[12\rk"), [Enter, Char('k')]);
        // ESC inside a sequence starts a new one.
        assert_eq!(keys(b"\x1b[1\x1b[B"), [Down]);
    }

    #[test]
    fn a_too_long_sequence_resets_within_max_seq() {
        let mut long = b"\x1b[".to_vec();
        long.extend(std::iter::repeat(b'1').take(40));
        long.push(b'A');
        let got = keys(&long);
        // The sequence is dropped at MAX_SEQ; what follows is read as keys.
        assert!(got.iter().all(|k| matches!(k, Char('1') | Char('A'))));
        assert_eq!(got.last(), Some(&Char('A')));
        assert!(got.len() < 40 - (MAX_SEQ - 2) + 2);
    }

    #[test]
    fn utf8_characters() {
        assert_eq!(keys("é€😀a".as_bytes()), [Char('é'), Char('€'), Char('😀'), Char('a')]);
        // Overlong, surrogate, cut short, stray continuation, C1 control.
        assert_eq!(keys(b"\xc0\xaf\xe0\x80\xafx"), [Char('x')]);
        assert_eq!(keys(b"\xed\xa0\x80y"), [Char('y')]);
        assert_eq!(keys(b"\xe2\x82z"), [Char('z')]);
        assert_eq!(keys(b"\x80\xbfw"), [Char('w')]);
        assert_eq!(keys(b"\xc2\x9bv"), [Char('v')]);
        assert_eq!(keys(b"\xf4\x90\x80\x80u"), [Char('u')]);
    }

    #[test]
    fn control_bytes_are_dropped() {
        assert_eq!(keys(b"\x00\x01\x03\x1a\x1ca\x7f"), [Char('a'), Backspace]);
    }

    #[test]
    fn split_reads_decode_the_same() {
        let all = b"ab\x1b[A\xc3\xa9\r\n\x1bOD\x1b[1;5C\t";
        let whole = keys(all);
        for cut in 0..all.len() {
            let mut d = Decoder::new();
            let mut out = Vec::new();
            for &b in &all[..cut] {
                d.feed(b, &mut out);
            }
            for &b in &all[cut..] {
                d.feed(b, &mut out);
            }
            d.timeout(&mut out);
            assert_eq!(out, whole, "cut at {cut}");
        }
    }

    /// Feed `bytes` cut into reads at random points, with a timeout
    /// (nothing more within the Esc timeout) after a read now and then.
    fn feed_split(r: &mut Rng, bytes: &[u8], timeouts: bool, out: &mut Vec<Key>) -> Decoder {
        let mut d = Decoder::new();
        let mut i = 0;
        while i < bytes.len() {
            let n = 1 + r.below(8) as usize;
            for &b in &bytes[i..(i + n).min(bytes.len())] {
                d.feed(b, out);
            }
            i += n;
            if timeouts && r.below(4) == 0 {
                d.timeout(out);
                assert!(!d.pending());
            }
        }
        d
    }

    /// Fuzz-style (BKO_RESCUE_FUZZ=N for more): random bytes, skewed to
    /// where the states are, read in random pieces with timeouts at random
    /// moments.  It never panics, consumes everything (a timeout always
    /// leaves it at rest), and never makes a control character.
    #[test]
    fn random_bytes_split_with_timeouts() {
        let mut r = Rng::new(0x2545_f491_4f6c_dd1d);
        let mut out = Vec::new();
        for case in 0..fuzz_cases(100_000) {
            let n = r.below(48) as usize;
            let bytes: Vec<u8> = (0..n)
                .map(|_| match r.below(8) {
                    0 => 0x1b,
                    1 => *r.pick(b"[O"),
                    2 => b'0' + r.below(10) as u8,
                    3 => *r.pick(b";ABCDM~\r\n"),
                    _ => r.below(256) as u8,
                })
                .collect();
            let mut d = feed_split(&mut r, &bytes, true, &mut out);
            d.timeout(&mut out);
            assert!(!d.pending(), "case {case}: not at rest after a timeout");
            assert!(out.iter().all(|k| !matches!(k, Char(c) if c.is_control())), "case {case}: a control Char");
            out.clear();
        }
    }

    /// A pending sequence never lasts past MAX_SEQ bytes, byte by byte.
    #[test]
    fn never_held_past_max_seq() {
        let mut r = Rng::new(0x9e37_79b9);
        let mut d = Decoder::new();
        let mut out = Vec::new();
        let mut held = 0;
        for _ in 0..fuzz_cases(100_000) * 2 {
            let b = match r.below(6) {
                0 => 0x1b,
                1 => b'[',
                2 => b'0' + r.below(10) as u8,
                3 => b';',
                _ => r.below(256) as u8,
            };
            d.feed(b, &mut out);
            held = if b == 0x1b {
                1
            } else if d.pending() {
                held + 1
            } else {
                0
            };
            assert!(held <= MAX_SEQ, "held {held} bytes");
            out.clear();
        }
    }

    /// Random keys, each sent in one of its forms, with sequences that
    /// mean nothing and control bytes between them, read in random
    /// pieces: exactly those keys come out.  Esc is an ESC with the
    /// timeout after it.
    #[test]
    fn random_keys_round_trip() {
        let mut r = Rng::new(0x0dd_ba11);
        const CHARS: &[char] = &['a', 'Z', '0', '~', '[', 'O', 'é', 'Ж', '€', '😀', 'ß'];
        const NOISE: &[&[u8]] = &[b"\x1b[15~", b"\x1b[Z", b"\x1b[3~", b"\x1bOP", b"\x1b[2;5A", b"\x00", b"\x03", b"\x1c"];
        for case in 0..fuzz_cases(100_000) / 10 {
            let mut want = Vec::new();
            // (bytes, a timeout after them)
            let mut parts: Vec<(Vec<u8>, bool)> = Vec::new();
            let mut last_cr = false;
            for _ in 0..r.below(12) {
                if r.below(4) == 0 {
                    parts.push((r.pick(NOISE).to_vec(), false));
                    last_cr = false;
                    continue;
                }
                let (k, b): (Key, Vec<u8>) = match r.below(10) {
                    0 => {
                        let k = *r.pick(&[Up, Down, Right, Left]);
                        let f = b"ABCD"[[Up, Down, Right, Left].iter().position(|&x| x == k).unwrap_or(0)];
                        let form = match r.below(4) {
                            0 => vec![0x1b, b'[', f],
                            1 => vec![0x1b, b'O', f],
                            2 => vec![0x1b, b'[', b'1', b';', b'5', f],
                            _ => vec![0x1b, b'O', b'2', f],
                        };
                        (k, form)
                    }
                    1 => {
                        let forms: &[&[u8]] = if last_cr { &[b"\r", b"\r\n"] } else { &[b"\r", b"\n", b"\r\n", b"\x1bOM"] };
                        (Enter, r.pick(forms).to_vec())
                    }
                    2 => (Esc, vec![0x1b]),
                    3 => (Backspace, vec![*r.pick(&[0x7f, 0x08])]),
                    4 => (Tab, vec![b'\t']),
                    5 => (Space, vec![b' ']),
                    _ => {
                        let c = *r.pick(CHARS);
                        (Char(c), c.to_string().into_bytes())
                    }
                };
                last_cr = b.last() == Some(&b'\r');
                want.push(k);
                parts.push((b, k == Esc));
            }
            let mut d = Decoder::new();
            let mut got = Vec::new();
            let all: Vec<(u8, bool)> = parts
                .iter()
                .flat_map(|(b, t)| b.iter().enumerate().map(move |(i, &x)| (x, *t && i + 1 == b.len())))
                .collect();
            let mut i = 0;
            while i < all.len() {
                let n = 1 + r.below(6) as usize;
                for &(b, t) in &all[i..(i + n).min(all.len())] {
                    d.feed(b, &mut got);
                    if t {
                        d.timeout(&mut got);
                    }
                }
                i += n;
            }
            d.timeout(&mut got);
            assert_eq!(got, want, "case {case}: {parts:?}");
        }
    }
}
