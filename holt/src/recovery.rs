//! Recovery keys: twelve words from BIP39's English list, chosen at random.
//!
//! A repository's recovery key is the password of a second key of the
//! repository (`rustic key add`, `restic key add`), beside the one from the
//! person's passphrase.  There is no cryptography of our own here, and the
//! words aren't derived from the passphrase: they are 128 bits from the
//! system's random source with BIP39's 4-bit checksum (the first 4 bits of
//! their SHA-256), written as BIP39 writes a 12-word mnemonic (BIP39's
//! mnemonic only: no seed, no PBKDF2, no wallet).  On a restore the person
//! types them back, and `check` reads them as people type (any case and
//! spacing, the first 4 letters of each word) and says what's wrong, by the
//! word's position, before the repository is ever tried.
//!
//! - `generate()`: new words, from the system's random source;
//! - `from_entropy(&[u8; 16])`: the words for 16 given bytes (BIP39's test
//!   vectors, and fixed tests);
//! - `check(text)`: the words someone typed, or the `Problem` with them;
//! - `password(&words)`: the repository key's password (a compatibility
//!   contract, see there).
//!
//! The list is BIP39's English one, byte for byte as bitcoin/bips has it
//! (<https://github.com/bitcoin/bips/blob/master/bip-0039/english.txt>, MIT
//! licence, BIP 39's own): 2048 words, sorted, of 3 to 8 letters, no two
//! with the same first 4 letters.  The tests pin its SHA-256.
//!
//! The words are a secret.  Nothing here logs or prints them; `Words`' and
//! `Problem`'s Debug don't show them; a `Problem`'s Display shows what was
//! typed, and its suggestions, so that the person typing can find the
//! mistake: it is for them, not for a log.  `Words`' Drop wipes the word
//! numbers and `Unknown`'s what was typed, with volatile writes.

use std::fmt;
use std::sync::OnceLock;

/// How many words a recovery key has.
pub const COUNT: usize = 12;

/// BIP39's English word list, one word a line.
const LIST_TEXT: &str = include_str!("bip39-english.txt");

/// The most suggestions an unknown word gets.
const MAX_SUGGESTIONS: usize = 4;

/// The 2048 words, in the list's (alphabetical) order.
fn list() -> &'static [&'static str] {
    static LIST: OnceLock<Vec<&'static str>> = OnceLock::new();
    LIST.get_or_init(|| {
        let v: Vec<&'static str> = LIST_TEXT.lines().collect();
        assert_eq!(
            v.len(),
            2048,
            "src/bip39-english.txt isn't BIP39's list of 2048 words"
        );
        v
    })
}

/// Zeroes BUF with volatile writes and a compiler fence, so the compiler
/// can't leave the writes out (std's optimisation hint promises nothing of
/// the kind: its documentation says so).
fn wipe<T: Copy + Default>(buf: &mut [T]) {
    for x in buf.iter_mut() {
        // SAFETY: `x` is a unique reference to one initialised element of
        // `buf`, so the pointer is valid, aligned and writable.
        unsafe { std::ptr::write_volatile(x, T::default()) };
    }
    std::sync::atomic::compiler_fence(std::sync::atomic::Ordering::SeqCst);
}

/// Overwrites sensitive byte buffers in place without allowing dead-store
/// elimination to elide the wipe.
pub fn zeroize_bytes(buf: &mut [u8]) {
    wipe(buf);
}

/// Overwrites a `String`'s UTF-8 buffer with NUL bytes and clears it.
pub fn zeroize_string(s: &mut String) {
    // SAFETY: 0x00 is valid UTF-8, preserving String's UTF-8 invariant.
    zeroize_bytes(unsafe { s.as_bytes_mut() });
    s.clear();
}

/// A recovery key: twelve words of the list whose checksum is right.  Its
/// Debug doesn't show them, and its Drop wipes the word indices.
#[derive(Clone, PartialEq, Eq)]
pub struct Words([u16; COUNT]);

impl Drop for Words {
    fn drop(&mut self) {
        wipe(&mut self.0);
    }
}

impl Words {
    /// The twelve words, in order, in full, in lower case.
    pub fn words(&self) -> [&'static str; COUNT] {
        self.0.map(|i| list()[usize::from(i)])
    }

    /// The 128 bits they hold (`from_entropy`'s inverse).
    #[cfg(test)]
    fn entropy(&self) -> [u8; 16] {
        decode(&self.0).0
    }
}

impl fmt::Debug for Words {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str("Words(12, not shown)")
    }
}

/// What's wrong with the words typed, by their positions counted from 1
/// (the numbers someone may type before them aren't counted).
#[derive(Clone, PartialEq, Eq)]
pub enum Problem {
    /// Words that are neither on the list nor the first 4 letters or more
    /// of a word on it, in order; and how many words there were in all.
    Unknown { words: Vec<Unknown>, count: usize },
    /// Every word is on the list, but there aren't 12: how many there are.
    Count(usize),
    /// Twelve words of the list that don't belong together: the checksum
    /// they carry is wrong.  A wrong word or two swapped, mostly; the
    /// checksum is 4 bits, so about 1 such mistake in 16 isn't caught.
    Checksum,
}

/// One word `check` couldn't read.  Its Debug doesn't show what was typed,
/// and its Drop wipes it.
#[derive(Clone, PartialEq, Eq)]
pub struct Unknown {
    /// Its position among the words typed, from 1.
    pub position: usize,
    /// It, as typed.
    pub typed: String,
    /// Fewer than 4 letters, and the beginning of words on the list: typed
    /// too short to tell which.
    pub short: bool,
    /// Words of the list close to it (at most 4): for a short one, the
    /// words it begins, when there are no more than that; else the one with
    /// the same first 4 letters, and those a letter away (one changed,
    /// missing, extra, or two neighbours swapped) in full or in their first
    /// letters.
    pub suggestions: Vec<&'static str>,
}

impl fmt::Display for Problem {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::Unknown { words, count } => {
                for (n, u) in words.iter().enumerate() {
                    if n > 0 {
                        writeln!(f)?;
                    }
                    write!(f, "word {}, \"{}\", ", u.position, u.typed)?;
                    if u.short {
                        f.write_str("is too short: type at least the first 4 letters of each word, or all of a shorter one")?;
                    } else {
                        f.write_str("isn't one of the recovery key's words")?;
                    }
                    if let Some((last, rest)) = u.suggestions.split_last() {
                        if rest.is_empty() {
                            write!(f, " (did you mean {last}?)")?;
                        } else {
                            write!(f, " (did you mean {} or {last}?)", rest.join(", "))?;
                        }
                    }
                }
                if *count != COUNT {
                    write!(f, "\nand there are {count} words: a recovery key has {COUNT}")?;
                }
                Ok(())
            }
            Self::Count(0) => write!(f, "no words: a recovery key has {COUNT}"),
            Self::Count(1) => write!(f, "1 word: a recovery key has {COUNT}"),
            Self::Count(n) => write!(f, "{n} words: a recovery key has {COUNT}"),
            Self::Checksum => f.write_str(
                "the 12 words are all on the list, but they don't belong together (their checksum is wrong): one of the words is wrong or the order is swapped",
            ),
        }
    }
}

/// Without the words: their positions and how many suggestions.
impl fmt::Debug for Problem {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::Unknown { words, count } => f
                .debug_struct("Unknown")
                .field("words", words)
                .field("count", count)
                .finish(),
            Self::Count(n) => f.debug_tuple("Count").field(n).finish(),
            Self::Checksum => f.write_str("Checksum"),
        }
    }
}

impl Drop for Unknown {
    fn drop(&mut self) {
        let n = self.typed.len();
        zeroize_string(&mut self.typed);
        #[cfg(test)]
        {
            // SAFETY: the first N bytes of the buffer were initialised
            // (they were the string's) and are still allocated: clear()
            // keeps the capacity.
            let zero = unsafe { std::slice::from_raw_parts(self.typed.as_ptr(), n) }
                .iter()
                .all(|&b| b == 0);
            tests::LAST_UNKNOWN_DROP.with(|d| d.set(Some((n, zero))));
        }
        let _ = n;
    }
}

impl fmt::Debug for Unknown {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(
            f,
            "Unknown {{ position: {}, short: {}, suggestions: {} }}",
            self.position,
            self.short,
            self.suggestions.len()
        )
    }
}

impl std::error::Error for Problem {}

/// New words: 16 bytes from the system's random source (`RANDOM_DEVICE`).
/// Fails, saying why, when it can't read them or they don't look random;
/// it never makes do with another source.
pub fn generate() -> Result<Words, String> {
    let mut e = random_bytes(RANDOM_DEVICE)?;
    let w = from_entropy(&e);
    zeroize_bytes(&mut e);
    Ok(w)
}

/// Where `generate` reads.  Linux: /dev/random, which since Linux 5.6 waits
/// only until the kernel's generator is seeded and is then the same as
/// /dev/urandom (/dev/urandom itself never waits, so very early in a boot it
/// could hand out bytes from an unseeded generator; every Linux `BackOtter`
/// supports runs 5.15 or later).  Everywhere else (FreeBSD, NetBSD, OpenBSD,
/// `DragonFly`, illumos, Haiku, macOS): /dev/urandom, the device each of them
/// documents for keys, and the one the rescue images' remote-access scripts
/// already read on the BSDs, illumos and Haiku.  FreeBSD's, OpenBSD's and
/// macOS's are the same device as their /dev/random.  NetBSD's never waits,
/// even on a system that was never seeded (no hardware source, no saved
/// seed: entropy(7)), and nothing here can tell.
#[cfg(target_os = "linux")]
const RANDOM_DEVICE: &str = "/dev/random";
#[cfg(not(target_os = "linux"))]
const RANDOM_DEVICE: &str = "/dev/urandom";

/// 16 bytes from the random device at PATH.  A plain file there (a bare
/// chroot's /dev, say) is refused, and so are 16 equal bytes: a broken source
/// such as /dev/zero in its place, not chance (one in 2^120).
fn random_bytes(path: &str) -> Result<[u8; 16], String> {
    use std::io::Read;
    let mut f = std::fs::File::open(path)
        .map_err(|e| format!("can't open {path}, the system's random source: {e}"))?;
    match f.metadata() {
        Ok(m) if m.is_file() => {
            return Err(format!(
                "{path} is a plain file, not the system's random source"
            ));
        }
        Ok(_) => {}
        Err(e) => return Err(format!("{path}: {e}")),
    }
    let mut b = [0u8; 16];
    f.read_exact(&mut b)
        .map_err(|e| format!("can't read 16 bytes from {path}, the system's random source: {e}"))?;
    if b.iter().all(|&x| x == b[0]) {
        return Err(format!(
            "{path} gave 16 equal bytes: it isn't a working random source"
        ));
    }
    Ok(b)
}

/// The words for ENTROPY, as BIP39 makes a 12-word mnemonic: its 128 bits,
/// then the first 4 bits of its SHA-256, read 11 bits a word.
pub fn from_entropy(entropy: &[u8; 16]) -> Words {
    let mut bits = [0u8; 17];
    bits[..16].copy_from_slice(entropy);
    bits[16] = checksum(entropy) << 4;
    let mut w = [0u16; COUNT];
    for (i, x) in w.iter_mut().enumerate() {
        for k in 11 * i..11 * i + 11 {
            *x = (*x << 1) | u16::from((bits[k / 8] >> (7 - k % 8)) & 1);
        }
    }
    zeroize_bytes(&mut bits);
    let out = Words(w);
    wipe(&mut w);
    out
}

/// The 128 bits twelve word numbers hold, and whether the checksum they
/// carry (the last word's last 4 bits) is the right one.
fn decode(w: &[u16; COUNT]) -> ([u8; 16], bool) {
    let mut bits = [0u8; 17];
    for (i, &x) in w.iter().enumerate() {
        for b in 0..11 {
            if (x >> (10 - b)) & 1 == 1 {
                let k = 11 * i + b;
                bits[k / 8] |= 0x80 >> (k % 8);
            }
        }
    }
    let mut e = [0u8; 16];
    e.copy_from_slice(&bits[..16]);
    let ok = bits[16] >> 4 == checksum(&e);
    zeroize_bytes(&mut bits);
    (e, ok)
}

/// BIP39's checksum of 128 bits: the first 4 bits of their SHA-256.  Hashed
/// in one block on the stack, never copied to the heap, and the block, the
/// message schedule and the digest are wiped after.
fn checksum(entropy: &[u8; 16]) -> u8 {
    let mut block = [0u8; 64];
    let mut w = [0u32; 64];
    let mut digest = crate::bytes::sha256_short(entropy, &mut block, &mut w);
    let c = digest[0] >> 4;
    zeroize_bytes(&mut block);
    wipe(&mut w);
    zeroize_bytes(&mut digest);
    c
}

/// The words in TEXT, as someone would type them: in any case; between
/// them any white space (new lines too) or commas; each word in full or its
/// first 4 letters or more (the 3-letter words, such as act, in full: "act"
/// is act, not action).  Numbers typed before the words, as the paper they
/// were written on has them ("1.", "2)", "3:", "4"), are left out.
pub fn check(text: &str) -> Result<Words, Problem> {
    let mut found: Vec<u16> = Vec::new();
    let mut unknown: Vec<Unknown> = Vec::new();
    for t in typed(text) {
        let mut lower = t.to_lowercase();
        match lookup(&lower) {
            Some(i) => found.push(i),
            None => unknown.push(Unknown {
                position: found.len() + unknown.len() + 1,
                typed: t.to_string(),
                short: lower.chars().count() < 4
                    && list().iter().any(|w| w.starts_with(lower.as_str())),
                // Past the first 12 (a page pasted, not words typed): none.
                suggestions: if unknown.len() < COUNT {
                    near(&lower)
                } else {
                    Vec::new()
                },
            }),
        }
        zeroize_string(&mut lower);
    }
    let count = found.len() + unknown.len();
    if !unknown.is_empty() {
        wipe(&mut found);
        return Err(Problem::Unknown {
            words: unknown,
            count,
        });
    }
    if found.len() != COUNT {
        wipe(&mut found);
        return Err(Problem::Count(count));
    }
    let mut w = [0u16; COUNT];
    w.copy_from_slice(&found);
    wipe(&mut found);
    let (mut ent, ok) = decode(&w);
    zeroize_bytes(&mut ent);
    if !ok {
        wipe(&mut w);
        return Err(Problem::Checksum);
    }
    let out = Words(w);
    wipe(&mut w);
    Ok(out)
}

/// How many words `check` would see in TEXT, right or wrong (numbers left
/// out): for someone typing them, how far they've got.
pub fn typed_count(text: &str) -> usize {
    typed(text).count()
}

/// The words in TEXT, as `check` splits it.
fn typed(text: &str) -> impl Iterator<Item = &str> {
    text.split(|c: char| c.is_whitespace() || c == ',')
        .filter(|t| !t.is_empty() && !is_number(t))
}

/// The repository key's password for WORDS: the twelve words in full, in
/// lower case, one space between each two, nothing before or after (no new
/// line).  So a password file holding it, with a final new line or without,
/// is read the same by restic (which trims white space) and rustic (which
/// reads the first line).
///
/// A compatibility contract: every repository given a recovery key has
/// this as that key's password, so the form never changes (nor the list,
/// nor the case, nor the separator).  Whatever someone types, `check` turns
/// it into the same `Words`, and so into this same password.
pub fn password(words: &Words) -> String {
    words.words().join(" ")
}

/// A number someone typed before a word: digits, perhaps with one '.', ')'
/// or ':' after them.  No word on the list is one.
fn is_number(t: &str) -> bool {
    let d = t
        .strip_suffix(|c| matches!(c, '.' | ')' | ':'))
        .unwrap_or(t);
    !d.is_empty() && d.bytes().all(|b| b.is_ascii_digit())
}

/// The word T (lower case) stands for: itself, or else the one word it
/// begins when it's 4 letters or more (BIP39's words differ in their first
/// 4 letters, so 4 are always enough to tell them apart).
fn lookup(t: &str) -> Option<u16> {
    let l = list();
    match l.binary_search(&t) {
        Ok(i) => Some(i as u16),
        Err(i) if t.len() >= 4 => {
            // The words T begins sort just where T would go.
            let n = l[i..].iter().take_while(|w| w.starts_with(t)).count();
            (n == 1).then_some(i as u16)
        }
        Err(_) => None,
    }
}

/// Words of the list close to T (lower case), for `Unknown::suggestions`.
/// By letters, not bytes: "ébandon" (é is a key of its own on some
/// keyboards) is a letter from abandon.
fn near(t: &str) -> Vec<&'static str> {
    // T's letters are part of the key: wiped once looked at.
    let mut tc: Vec<char> = t.chars().collect();
    let out = near_letters(t, &tc);
    wipe(&mut tc);
    out
}

/// `near`'s work, T's letters TC given.
fn near_letters(t: &str, tc: &[char]) -> Vec<&'static str> {
    fn add(out: &mut Vec<&'static str>, w: &'static str) {
        if !out.contains(&w) {
            out.push(w);
        }
    }
    let l = list();
    // Too short to tell (aba): the words it begins, when there are few.
    if tc.len() < 4 {
        let v: Vec<&'static str> = l.iter().copied().filter(|w| w.starts_with(t)).collect();
        if !v.is_empty() {
            return if v.len() <= MAX_SUGGESTIONS {
                v
            } else {
                Vec::new()
            };
        }
    }
    let mut out = Vec::new();
    // A word typed too far (abandoned) or wrong after its 4th letter
    // (abanxon): the one word with the same first 4 letters.
    if tc.len() >= 4 {
        let mut p4: String = tc[..4].iter().collect();
        if let Some(w) = l.iter().find(|w| w.starts_with(p4.as_str())) {
            add(&mut out, w);
        }
        zeroize_string(&mut p4);
    }
    let words: Vec<Vec<char>> = l.iter().map(|w| w.chars().collect()).collect();
    // A letter wrong in a word typed in full (abndon, cst)...
    for (w, wc) in l.iter().zip(&words) {
        if ed1(tc, wc) {
            add(&mut out, w);
        }
    }
    // ... or in its first letters (abam for aban).
    if tc.len() >= 4 {
        for (w, wc) in l.iter().zip(&words) {
            if (tc.len() - 1..=tc.len() + 1).any(|k| k >= 4 && k <= wc.len() && ed1(tc, &wc[..k])) {
                add(&mut out, w);
            }
        }
    }
    out.truncate(MAX_SUGGESTIONS);
    out
}

/// Whether A and B are a letter apart: one changed, missing or extra, or
/// two neighbours swapped.
fn ed1<T: PartialEq>(a: &[T], b: &[T]) -> bool {
    if a == b {
        return false;
    }
    let (s, l) = if a.len() <= b.len() { (a, b) } else { (b, a) };
    // Where they first differ (they do: a == b is out).
    let i = s.iter().zip(l).take_while(|(x, y)| x == y).count();
    match l.len() - s.len() {
        0 => {
            s[i + 1..] == l[i + 1..]
                || (i + 1 < s.len()
                    && s[i] == l[i + 1]
                    && s[i + 1] == l[i]
                    && s[i + 2..] == l[i + 2..])
        }
        1 => s[i..] == l[i + 1..],
        _ => false,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    thread_local! {
        /// How many bytes the last `Unknown` dropped on this thread had
        /// typed, and whether they were all zero after its Drop.
        pub(super) static LAST_UNKNOWN_DROP: std::cell::Cell<Option<(usize, bool)>> = const { std::cell::Cell::new(None) };
    }

    /// BIP39's test vectors with 128 bits of entropy: every "english" entry
    /// of trezor/python-mnemonic's vectors.json whose entropy is 32 hex
    /// digits (its seed and xprv left out: we make no seed),
    /// <https://github.com/trezor/python-mnemonic/blob/master/vectors.json>
    /// (fetched 2026-11-19).
    const VECTORS: [(&str, &str); 8] = [
        (
            "00000000000000000000000000000000",
            "abandon abandon abandon abandon abandon abandon abandon abandon abandon abandon abandon about",
        ),
        (
            "7f7f7f7f7f7f7f7f7f7f7f7f7f7f7f7f",
            "legal winner thank year wave sausage worth useful legal winner thank yellow",
        ),
        (
            "80808080808080808080808080808080",
            "letter advice cage absurd amount doctor acoustic avoid letter advice cage above",
        ),
        (
            "ffffffffffffffffffffffffffffffff",
            "zoo zoo zoo zoo zoo zoo zoo zoo zoo zoo zoo wrong",
        ),
        (
            "9e885d952ad362caeb4efe34a8e91bd2",
            "ozone drill grab fiber curtain grace pudding thank cruise elder eight picnic",
        ),
        (
            "c0ba5a8e914111210f2bd131f3d5e08d",
            "scheme spot photo card baby mountain device kick cradle pact join borrow",
        ),
        (
            "23db8160a31d3e0dca3688ed941adbf3",
            "cat swing flag economy stadium alone churn speed unique patch report train",
        ),
        (
            "f30f8c1da665478f49b001d94c5fc452",
            "vessel ladder alter error federal sibling chat ability sun glass valve picture",
        ),
    ];

    fn hex16(h: &str) -> [u8; 16] {
        let mut e = [0u8; 16];
        for (i, x) in e.iter_mut().enumerate() {
            *x = u8::from_str_radix(&h[2 * i..2 * i + 2], 16).unwrap();
        }
        e
    }

    /// Fixed "random" entropies (splitmix64 from SEED), the same every run.
    fn entropies(seed: u64, n: usize) -> Vec<[u8; 16]> {
        let mut s = seed;
        let mut next = || {
            s = s.wrapping_add(0x9e37_79b9_7f4a_7c15);
            let mut z = s;
            z = (z ^ (z >> 30)).wrapping_mul(0xbf58_476d_1ce4_e5b9);
            z = (z ^ (z >> 27)).wrapping_mul(0x94d0_49bb_1331_11eb);
            z ^ (z >> 31)
        };
        (0..n)
            .map(|_| {
                let mut e = [0u8; 16];
                e[..8].copy_from_slice(&next().to_be_bytes());
                e[8..].copy_from_slice(&next().to_be_bytes());
                e
            })
            .collect()
    }

    fn unknown(r: Result<Words, Problem>) -> (Vec<Unknown>, usize) {
        match r {
            Err(Problem::Unknown { words, count }) => (words, count),
            other => panic!("not Unknown: {other:?}"),
        }
    }

    /// The list is bitcoin/bips' bip-0039/english.txt as it is
    /// (<https://raw.githubusercontent.com/bitcoin/bips/master/bip-0039/english.txt>,
    /// fetched 2026-11-19), and has what `check()` counts on.
    #[test]
    fn the_list_is_bip39s() {
        assert_eq!(
            crate::bytes::sha256_hex(LIST_TEXT.as_bytes()),
            "2f5eed53a4727b4bf8880d8f3f199efc90e58503646d9ff8eff3a2ed3b24dbda"
        );
        assert!(LIST_TEXT.ends_with('\n') && !LIST_TEXT.contains('\r'));
        let l = list();
        assert_eq!(l.len(), 2048);
        assert!(l.windows(2).all(|p| p[0] < p[1]), "sorted, no word twice");
        assert!(
            l.iter()
                .all(|w| (3..=8).contains(&w.len()) && w.bytes().all(|b| b.is_ascii_lowercase()))
        );
        let mut p4: Vec<&str> = l.iter().map(|w| &w[..4.min(w.len())]).collect();
        p4.sort();
        p4.dedup();
        assert_eq!(p4.len(), 2048, "no two words with the same first 4 letters");
        // The 3-letter words that begin longer ones: act (action...), can
        // (canal...): "act" must be act.
        let begin: Vec<&str> = l
            .iter()
            .copied()
            .filter(|w| w.len() == 3 && l.iter().any(|x| x != w && x.starts_with(w)))
            .collect();
        assert_eq!(begin.len(), 49);
        assert_eq!(&begin[..4], ["act", "add", "age", "air"]);
    }

    #[test]
    fn bip39_test_vectors() {
        for (hex, mnemonic) in VECTORS {
            let w = from_entropy(&hex16(hex));
            assert_eq!(password(&w), mnemonic, "{hex}");
            assert_eq!(w.words().join(" "), mnemonic);
            assert_eq!(w.entropy(), hex16(hex));
            assert_eq!(check(mnemonic), Ok(w));
        }
    }

    /// Many entropies: words and back, through `check()` of the password and
    /// of the same words abbreviated, shouted and spread out.
    #[test]
    fn round_trips() {
        let mut all = entropies(1, 2000);
        all.push([0; 16]);
        all.push([0xff; 16]);
        for bit in 0..128 {
            let mut e = [0u8; 16];
            e[bit / 8] = 0x80 >> (bit % 8);
            all.push(e);
            all.push(e.map(|b| !b));
        }
        let mut seen = std::collections::HashSet::new();
        for e in &all {
            let w = from_entropy(e);
            assert_eq!(w.entropy(), *e);
            let pw = password(&w);
            assert_eq!(pw.split(' ').count(), 12);
            assert_eq!(check(&pw).as_ref(), Ok(&w));
            let short: Vec<String> = w
                .words()
                .iter()
                .map(|x| x[..4.min(x.len())].to_string())
                .collect();
            assert_eq!(
                check(&short.join(" ")).as_ref(),
                Ok(&w),
                "{}",
                short.join(" ")
            );
            assert_eq!(
                check(&format!(
                    "\n  {}\t\n",
                    pw.to_uppercase().replace(' ', " \r\n ")
                ))
                .as_ref(),
                Ok(&w)
            );
            seen.insert(pw);
        }
        assert_eq!(
            seen.len(),
            all.len(),
            "different entropies, different words"
        );
    }

    #[test]
    fn abbreviations() {
        let w = from_entropy(&[0; 16]);
        assert_eq!(
            check("aban aban aban aban aban aban aban aban aban aban aban abou"),
            Ok(w.clone())
        );
        assert_eq!(
            check("aband abando abandon aban abandon aban abandon aban abandon aban abandon about"),
            Ok(w)
        );
        assert_eq!(
            password(
                &check("lega winn than year wave saus wort usef lega winn than yell").unwrap()
            ),
            VECTORS[1].1
        );
        // A 3-letter word is itself, though longer words begin with it;
        // their first 4 letters are theirs.
        assert_eq!(lookup("act").map(|i| list()[i as usize]), Some("act"));
        assert_eq!(lookup("acti").map(|i| list()[i as usize]), Some("action"));
        assert_eq!(lookup("acto").map(|i| list()[i as usize]), Some("actor"));
        assert_eq!(lookup("actr").map(|i| list()[i as usize]), Some("actress"));
        assert_eq!(lookup("cat").map(|i| list()[i as usize]), Some("cat"));
        assert_eq!(
            password(&check("cat swin flag econ stad alon chur spee uniq patc repo trai").unwrap()),
            VECTORS[6].1
        );
        // Fewer than 4 letters of a longer word, or more than the word: no.
        for t in ["", "a", "ab", "aba", "abandons", "abandonx", "actx", "zoos"] {
            assert_eq!(lookup(t), None, "{t:?}");
        }
    }

    #[test]
    fn case_spacing_and_numbers() {
        let w = from_entropy(&hex16(VECTORS[5].0));
        for text in [
            "SCHEME SPOT PHOTO CARD BABY MOUNTAIN DEVICE KICK CRADLE PACT JOIN BORROW",
            "Scheme Spot photo card baby mountain device kick cradle pact join Borrow\n",
            "  scheme\tspot\n\nphoto   card\r\nbaby mountain device kick cradle pact join borrow  ",
            "scheme,spot, photo ,card baby mountain device kick cradle pact join borrow",
            "scheme\u{a0}spot\u{2003}photo card baby mountain device kick cradle pact join borrow",
            "1. scheme 2. spot 3. photo 4. card 5. baby 6. mountain 7. device 8. kick 9. cradle 10. pact 11. join 12. borrow",
            "1 scheme 2 spot 3 photo 4 card\n5) baby 6) mountain 7: device 8: kick\n9 cradle 10 pact 11 join 12 borrow",
            "sche spot phot card baby moun devi kick crad pact join borr",
        ] {
            assert_eq!(check(text).as_ref(), Ok(&w), "{text:?}");
        }
        assert_eq!(typed_count("1. aban, 2. xq\n3) zoo 4"), 3);
        assert_eq!(typed_count(" \n"), 0);
        assert!(is_number("12") && is_number("1.") && is_number("3)") && is_number("4:"));
        assert!(
            !is_number(".")
                && !is_number("1.2")
                && !is_number("x1")
                && !is_number("1..")
                && !is_number("")
        );
    }

    #[test]
    fn wrong_count() {
        assert_eq!(check(""), Err(Problem::Count(0)));
        assert_eq!(check(" \n\t, "), Err(Problem::Count(0)));
        assert_eq!(check("1. 2. 3."), Err(Problem::Count(0)));
        assert_eq!(check("zoo"), Err(Problem::Count(1)));
        let v: Vec<&str> = VECTORS[1].1.split(' ').collect();
        assert_eq!(check(&v[..11].join(" ")), Err(Problem::Count(11)));
        assert_eq!(
            check(&format!("{} zoo", VECTORS[1].1)),
            Err(Problem::Count(13))
        );
        assert_eq!(
            check(&format!("{} {}", VECTORS[1].1, VECTORS[2].1)),
            Err(Problem::Count(24))
        );
        assert_eq!(
            Problem::Count(0).to_string(),
            "no words: a recovery key has 12"
        );
        assert_eq!(
            Problem::Count(1).to_string(),
            "1 word: a recovery key has 12"
        );
        assert_eq!(
            Problem::Count(11).to_string(),
            "11 words: a recovery key has 12"
        );
    }

    #[test]
    fn unknown_words_by_position() {
        // Words 3 and 7 wrong, counted after the numbers are left out.
        let (u, count) = unknown(check(
            "1. ozone 2. drill 3. grabb 4. fiber curtain grace xq thank cruise elder eight picnic",
        ));
        assert_eq!(count, 12);
        assert_eq!(u.len(), 2);
        assert_eq!(
            (
                u[0].position,
                u[0].typed.as_str(),
                u[0].short,
                u[0].suggestions.clone()
            ),
            (3, "grabb", false, vec!["grab", "rabbit"])
        );
        assert_eq!(
            (
                u[1].position,
                u[1].typed.as_str(),
                u[1].short,
                u[1].suggestions.len()
            ),
            (7, "xq", false, 0)
        );
        assert_eq!(
            Problem::Unknown { words: u, count }.to_string(),
            "word 3, \"grabb\", isn't one of the recovery key's words (did you mean grab or rabbit?)\nword 7, \"xq\", isn't one of the recovery key's words"
        );
        // As typed in the message, but read in any case.
        let (u, count) = unknown(check(
            "Ozone DRILL ABNDON fiber curtain grace pudding thank cruise elder eight",
        ));
        assert_eq!(
            (u.len(), u[0].position, u[0].typed.as_str(), count),
            (1, 3, "ABNDON", 11)
        );
        assert_eq!(
            Problem::Unknown { words: u, count }.to_string(),
            "word 3, \"ABNDON\", isn't one of the recovery key's words (did you mean abandon?)\nand there are 11 words: a recovery key has 12"
        );
        // Something else pasted: suggestions for the first 12 only.
        let (u, count) = unknown(check(&"abndon ".repeat(20)));
        assert_eq!(
            (
                u.len(),
                count,
                u[11].suggestions.len(),
                u[12].suggestions.len()
            ),
            (20, 20, 1, 0)
        );
        // Too short to tell which word.
        let (u, _) = unknown(check(
            "ab aba ozone drill grab fiber curtain grace pudding thank cruise elder",
        ));
        assert_eq!(
            (u[0].position, u[0].short, u[0].suggestions.len()),
            (1, true, 0)
        );
        assert_eq!(
            (u[1].position, u[1].short, u[1].suggestions.clone()),
            (2, true, vec!["abandon"])
        );
        assert_eq!(
            Problem::Unknown {
                words: u,
                count: 12
            }
            .to_string(),
            "word 1, \"ab\", is too short: type at least the first 4 letters of each word, or all of a shorter one\n\
             word 2, \"aba\", is too short: type at least the first 4 letters of each word, or all of a shorter one (did you mean abandon?)"
        );
    }

    /// The suggestions, as an independent reimplementation of `near()`'s
    /// rules (in Python, over the same list) gave them.
    #[test]
    fn suggestions() {
        let cases: [(&str, &[&str]); 17] = [
            ("acts", &["act", "action", "actor", "actress"]),
            ("cst", &["cat", "cost"]),
            ("abndon", &["abandon"]),
            ("abnadon", &["abandon"]),
            ("abandoned", &["abandon"]),
            ("abandon.", &["abandon"]),
            ("abam", &["abandon"]),
            ("grabb", &["grab", "rabbit"]),
            ("aba", &["abandon"]),
            ("zo", &["zone", "zoo"]),
            ("ab", &[]),
            ("legl", &["leg", "legal", "legend", "neglect"]),
            ("wiener", &["winner"]),
            ("xq", &[]),
            ("woz", &[]),
            ("ébandon", &["abandon"]),
            ("abandön", &["abandon"]),
        ];
        for (t, want) in cases {
            assert_eq!(near(t), want, "{t}");
        }
        let (words, count) = unknown(check(
            "acts drill grab fiber curtain grace pudding thank cruise elder eight picnic",
        ));
        assert!(
            Problem::Unknown { words, count }
                .to_string()
                .ends_with("(did you mean act, action, actor or actress?)")
        );
        assert!(
            ed1(b"ab", b"ba")
                && ed1(b"abc", b"ab")
                && ed1(b"ab", b"abc")
                && ed1(b"abc", b"axc")
                && ed1(b"abc", b"xbc")
                && ed1(b"abc", b"abx")
        );
        assert!(
            ed1(b"abcd", b"bacd")
                && ed1(b"abcd", b"abdc")
                && ed1(b"a", b"")
                && ed1(b"abc", b"bc")
                && ed1(b"abc", b"xabc")
        );
        assert!(
            !ed1(b"abc", b"abc")
                && !ed1(b"abc", b"cba")
                && !ed1(b"abc", b"a")
                && !ed1(b"abcd", b"badc")
                && !ed1(b"abcd", b"axcx")
        );
    }

    #[test]
    fn checksum_mismatch() {
        // Twelve words of the list whose checksum is wrong (all zeros need
        // "about" last).
        assert_eq!(check(&["abandon"; 12].join(" ")), Err(Problem::Checksum));
        assert_eq!(check(&["zoo"; 12].join(" ")), Err(Problem::Checksum));
        // Two words swapped: caught here...
        let mut v: Vec<&str> = VECTORS[4].1.split(' ').collect();
        v.swap(0, 1);
        assert_eq!(check(&v.join(" ")), Err(Problem::Checksum));
        assert!(
            Problem::Checksum
                .to_string()
                .ends_with("one of the words is wrong or the order is swapped")
        );
        // ... but the checksum is 4 bits: one swap in 16 or so gets past
        // (words 5 and 11 of this one, other words that also fit).
        let mut v: Vec<&str> = VECTORS[4].1.split(' ').collect();
        v.swap(4, 10);
        assert!(check(&v.join(" ")).is_ok());
    }

    /// What can be promised about a mistake in one word.
    ///
    /// 1. Always caught: a word that isn't on the list (nor the beginning
    ///    of one, 4 letters or more), and a last word changed to any of the
    ///    15 others whose first 7 bits are the same (the entropy is then the
    ///    same, so only the checksum changed).
    /// 2. A single-letter typo in one word (a letter changed, dropped,
    ///    added, or two neighbours swapped, a to z): over every word of the
    ///    list and every such typo of it, 99.45% make no word (refused, by
    ///    position) or still the same word (an abbreviation); 3449 of 626118
    ///    make another word of the list, and the checksum then catches all
    ///    but about 1 in 16 of those.
    /// 3. Any other word in its place: the checksum catches about 15 in 16.
    #[test]
    fn one_wrong_word() {
        let l = list();
        // 1.: the last word's 15 neighbours.
        let mut phrases: Vec<[u8; 16]> = VECTORS.iter().map(|(h, _)| hex16(h)).collect();
        phrases.extend(entropies(2, 200));
        for e in &phrases {
            let w = from_entropy(e);
            for c in 0..16u16 {
                let mut x = w.0;
                x[11] = (x[11] & !0xf) | c;
                if x != w.0 {
                    assert!(!decode(&x).1);
                    assert_eq!(
                        check(&x.map(|i| l[usize::from(i)]).join(" ")),
                        Err(Problem::Checksum)
                    );
                }
            }
        }
        // 2.: every typo of every word, and where it leads.
        let typos = |w: &str| -> std::collections::BTreeSet<String> {
            let b = w.as_bytes();
            let mut out = std::collections::BTreeSet::new();
            for i in 0..=b.len() {
                for c in b'a'..=b'z' {
                    let mut v = b.to_vec();
                    v.insert(i, c);
                    out.insert(String::from_utf8(v).unwrap());
                    if i < b.len() && c != b[i] {
                        let mut v = b.to_vec();
                        v[i] = c;
                        out.insert(String::from_utf8(v).unwrap());
                    }
                }
                if i < b.len() {
                    let mut v = b.to_vec();
                    v.remove(i);
                    out.insert(String::from_utf8(v).unwrap());
                }
                if i + 1 < b.len() && b[i] != b[i + 1] {
                    let mut v = b.to_vec();
                    v.swap(i, i + 1);
                    out.insert(String::from_utf8(v).unwrap());
                }
            }
            out.remove(w);
            out
        };
        let (mut total, mut same, mut other) = (0usize, 0usize, 0usize);
        // For each word, the other words its typos make.
        let mut to_other: Vec<Vec<u16>> = Vec::new();
        for (n, w) in l.iter().enumerate() {
            let mut o = Vec::new();
            for t in typos(w) {
                total += 1;
                match lookup(&t) {
                    None => {}
                    Some(i) if usize::from(i) == n => same += 1,
                    Some(i) => {
                        other += 1;
                        o.push(i);
                    }
                }
            }
            to_other.push(o);
        }
        assert_eq!((total, same, other), (626_118, 1503, 3449));
        // ... and in whole phrases, what the checksum lets past.
        let (mut tried, mut past) = (0usize, 0usize);
        for e in entropies(3, 100) {
            let w = from_entropy(&e);
            for p in 0..COUNT {
                for &i in &to_other[usize::from(w.0[p])] {
                    let mut x = w.0;
                    x[p] = i;
                    tried += 1;
                    if decode(&x).1 {
                        past += 1;
                    }
                }
            }
        }
        println!(
            "one-letter typos making another word, in 100 phrases: {tried}, {past} of them past the checksum"
        );
        assert!(tried > 1000, "{tried}");
        assert!(
            past * 10 < tried,
            "the checksum let {past} of {tried} typos past"
        );
        // 3.: every other word in each place.
        let (mut tried, mut past) = (0usize, 0usize);
        for e in entropies(4, 3) {
            let w = from_entropy(&e);
            for p in 0..COUNT {
                for i in 0..2048u16 {
                    if i != w.0[p] {
                        let mut x = w.0;
                        x[p] = i;
                        tried += 1;
                        if decode(&x).1 {
                            past += 1;
                        }
                    }
                }
            }
        }
        println!(
            "any other word in one place, in 3 phrases: {tried}, {past} of them past the checksum"
        );
        assert!(past * 10 < tried && past * 32 > tried, "{past} of {tried}");
    }

    #[test]
    fn password_is_the_canonical_form() {
        let w = check("  ZOO zoo Zoo zoo zoo zoo zoo zoo zoo zoo zoo WRON \n").unwrap();
        assert_eq!(
            password(&w),
            "zoo zoo zoo zoo zoo zoo zoo zoo zoo zoo zoo wrong"
        );
        let w = check("lega winn than year wave saus wort usef lega winn than yell").unwrap();
        let pw = password(&w);
        assert_eq!(pw, VECTORS[1].1);
        assert_eq!(pw.trim(), pw);
        assert!(pw.bytes().all(|b| b.is_ascii_lowercase() || b == b' ') && !pw.contains("  "));
    }

    #[test]
    fn secrets_stay_out_of_debug() {
        let w = from_entropy(&[0; 16]);
        assert_eq!(format!("{w:?}"), "Words(12, not shown)");
        let p = check("abandon abandon abndon abandon abandon abandon abandon abandon abandon abandon abandon about").unwrap_err();
        let d = format!("{p:?}");
        assert!(!d.contains("aband") && !d.contains("abnd"), "{d}");
        assert_eq!(
            d,
            "Unknown { words: [Unknown { position: 3, short: false, suggestions: 1 }], count: 12 }"
        );
    }

    #[test]
    fn generate_gives_twelve_good_words() {
        let a = generate().unwrap();
        let b = generate().unwrap();
        assert_ne!(a, b);
        assert_ne!(a.entropy(), b.entropy());
        for w in [&a, &b] {
            assert_eq!(w.words().len(), 12);
            assert_eq!(check(&password(w)).as_ref(), Ok(w));
        }
    }

    #[test]
    fn random_source_refusals() {
        assert!(
            random_bytes("/no/such/random")
                .unwrap_err()
                .contains("can't open /no/such/random")
        );
        // /dev/zero: a device, but not a random one.
        assert!(
            random_bytes("/dev/zero")
                .unwrap_err()
                .contains("16 equal bytes")
        );
        let t = crate::testutil::Tree::new("recovery-random");
        t.file("/urandom", "0123456789abcdefghij");
        let p = t.root().join("urandom");
        assert!(
            random_bytes(p.to_str().unwrap())
                .unwrap_err()
                .contains("is a plain file")
        );
        // An empty /dev (a directory read as a file) fails too.
        assert!(random_bytes(t.root().to_str().unwrap()).is_err());
        assert_eq!(random_bytes(RANDOM_DEVICE).unwrap().len(), 16);
    }

    #[test]
    fn wipe_zeroes_every_width() {
        let mut b = [0x5au8; 17];
        let mut h = [0xffffu16; 12];
        let mut w = [u32::MAX; 64];
        let mut c = vec!['é'; 9];
        wipe(&mut b);
        wipe(&mut h);
        wipe(&mut w);
        wipe(&mut c);
        assert!(
            b.iter().all(|&x| x == 0) && h.iter().all(|&x| x == 0) && w.iter().all(|&x| x == 0)
        );
        assert_eq!(c, vec!['\0'; 9]);
        let mut s = String::from("abandon");
        zeroize_string(&mut s);
        assert!(s.is_empty());
    }

    /// What was typed is wiped when its `Unknown` goes, so a caller that
    /// only prints or drops the `Problem` (bko-recovery-words) leaves none.
    #[test]
    fn dropping_an_unknown_wipes_what_was_typed() {
        LAST_UNKNOWN_DROP.with(|d| d.set(None));
        let (u, _) = unknown(check("abandon abandon grabbé"));
        assert_eq!(u[0].typed, "grabbé");
        drop(u);
        assert_eq!(
            LAST_UNKNOWN_DROP.with(std::cell::Cell::take),
            Some(("grabbé".len(), true))
        );
    }
}
