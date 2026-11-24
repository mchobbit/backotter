//! What the program waits for: keys, a resize, a tick, a signal or a hang
//! up, from one loop around `poll` (docs/design/rescue-tui.md, "Size and
//! resize", "Input and keys", "Restoring the terminal").
//!
//! - Keys come from the decoder; a lone ESC becomes Esc once nothing more
//!   came within the profile's Esc timeout.
//! - The size is read again at every tick (250 ms) and when poll times
//!   out, so a resize is noticed without a SIGWINCH handler.
//! - SIGHUP and SIGTERM only set a flag (`ffi::catch_hup_term`); poll then
//!   returns early, and the next call says `Signal`.  The caller returns,
//!   and dropping `Term` puts the terminal back, then gives both signals
//!   their default action again (`ffi::default_hup_term`).
//!
//! The source of bytes, time and size is a trait, so all of it is tested
//! without a terminal.

use std::collections::VecDeque;
use std::io::{self, Write as _};
use std::time::Instant;

use super::ffi;
use super::keys::{Decoder, Key};
use super::profile::Profile;
use super::tty::{self, Got, Input, RawMode, Size};

/// How often the size is read again, at the latest.
pub const TICK_MS: u64 = 250;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Event {
    /// A key, and the number of the read it came in: keys of one read
    /// came together (Alt+key, a paste, a fast passphrase), which a
    /// widget may want to know.
    Key(Key, u32),
    /// The terminal has a new size: redraw in full.
    Resize(Size),
    /// The time the caller asked for came.
    Tick,
    /// SIGHUP or SIGTERM: put the terminal back and exit 128 + it.
    Signal(i32),
    /// The terminal hung up (read returned 0, or EIO).
    Hangup,
}

/// Where bytes, the size, the time and signals come from.
pub trait Source {
    fn read(&mut self, buf: &mut [u8], timeout_ms: u64) -> io::Result<Got>;
    fn size(&mut self) -> Size;
    /// Milliseconds from any fixed start.
    fn now_ms(&mut self) -> u64;
    /// A signal caught since the last call.
    fn signal(&mut self) -> Option<i32>;
}

/// The real terminal: fd 0, TIOCGWINSZ, the monotonic clock, and the
/// flag the signal handler sets.
pub struct TtySource {
    input: Input,
    start: Instant,
}

impl Default for TtySource {
    fn default() -> Self {
        TtySource::new()
    }
}

impl TtySource {
    pub fn new() -> TtySource {
        TtySource { input: Input::new(), start: Instant::now() }
    }
}

impl Source for TtySource {
    fn read(&mut self, buf: &mut [u8], timeout_ms: u64) -> io::Result<Got> {
        self.input.read(buf, i32::try_from(timeout_ms).unwrap_or(i32::MAX))
    }

    fn size(&mut self) -> Size {
        tty::size()
    }

    fn now_ms(&mut self) -> u64 {
        u64::try_from(self.start.elapsed().as_millis()).unwrap_or(u64::MAX)
    }

    fn signal(&mut self) -> Option<i32> {
        ffi::take_signal()
    }
}

pub struct Events<S: Source> {
    src: S,
    dec: Decoder,
    keys: Vec<Key>,
    queue: VecDeque<Event>,
    size: Size,
    size_at: u64,
    /// When the last byte of a half-read sequence came.
    pending_at: Option<u64>,
    reads: u32,
    hung_up: bool,
    pub esc_ms: u64,
}

impl<S: Source> Events<S> {
    pub fn new(mut src: S, esc_ms: u64) -> Events<S> {
        let size = src.size();
        let size_at = src.now_ms();
        Events {
            src,
            dec: Decoder::new(),
            keys: Vec::new(),
            queue: VecDeque::new(),
            size,
            size_at,
            pending_at: None,
            reads: 0,
            hung_up: false,
            esc_ms,
        }
    }

    /// The size the terminal had at the last look.
    pub fn size(&self) -> Size {
        self.size
    }

    pub fn now_ms(&mut self) -> u64 {
        self.src.now_ms()
    }

    /// Whether events are waiting already: a caller can leave drawing
    /// until they are all handled.
    pub fn queued(&self) -> bool {
        !self.queue.is_empty()
    }

    pub fn source(&mut self) -> &mut S {
        &mut self.src
    }

    fn push_keys(&mut self) {
        let read = self.reads;
        self.queue.extend(self.keys.drain(..).map(|k| Event::Key(k, read)));
    }

    /// The next event.  `until` is a time (from `now_ms`) at which to
    /// return `Tick` if nothing else came first; None waits for an event.
    pub fn next(&mut self, until: Option<u64>) -> io::Result<Event> {
        let mut buf = [0u8; 256];
        loop {
            if let Some(sig) = self.src.signal() {
                return Ok(Event::Signal(sig));
            }
            if let Some(e) = self.queue.pop_front() {
                return Ok(e);
            }
            if self.hung_up {
                return Ok(Event::Hangup);
            }
            let now = self.src.now_ms();
            if let Some(t) = self.pending_at {
                if now >= t.saturating_add(self.esc_ms) {
                    self.dec.timeout(&mut self.keys);
                    self.pending_at = None;
                    self.reads = self.reads.wrapping_add(1);
                    self.push_keys();
                    continue;
                }
            }
            if now >= self.size_at.saturating_add(TICK_MS) {
                self.size_at = now;
                let s = self.src.size();
                if s != self.size {
                    self.size = s;
                    return Ok(Event::Resize(s));
                }
            }
            if until.is_some_and(|u| now >= u) {
                return Ok(Event::Tick);
            }
            let mut wait = self.size_at.saturating_add(TICK_MS).saturating_sub(now);
            if let Some(t) = self.pending_at {
                wait = wait.min(t.saturating_add(self.esc_ms).saturating_sub(now));
            }
            if let Some(u) = until {
                wait = wait.min(u.saturating_sub(now));
            }
            match self.src.read(&mut buf, wait.max(1))? {
                Got::Bytes(n) => {
                    self.reads = self.reads.wrapping_add(1);
                    for &b in &buf[..n.min(buf.len())] {
                        self.dec.feed(b, &mut self.keys);
                    }
                    self.pending_at = if self.dec.pending() { Some(self.src.now_ms()) } else { None };
                    self.push_keys();
                }
                // The loop looks at the time, the size and the flag.
                Got::Timeout | Got::Interrupted => {}
                Got::Eof => {
                    self.hung_up = true;
                    // A lone ESC that came last is still Esc.
                    self.dec.timeout(&mut self.keys);
                    self.push_keys();
                }
            }
        }
    }
}

/// The terminal in full-screen mode: SIGHUP and SIGTERM caught, raw mode
/// on (put back on drop, on a panic and by `tty::restore`), the events,
/// and stdout.  Dropping it puts everything back: the terminal first,
/// then the signals' default action, so a SIGTERM or SIGHUP after the
/// full-screen part ends the program again.  A signal the caller took
/// (`Event::Signal`) is its own to exit with, as 128 + it; one that came
/// and wasn't taken is raised again then.
pub struct Term {
    raw: RawMode,
    pub events: Events<TtySource>,
}

impl Term {
    pub fn open(p: &Profile) -> io::Result<Term> {
        // The handlers first: they only set a flag, and a signal between
        // raw mode and them would kill us with the terminal raw.
        if !ffi::catch_hup_term() {
            return Err(io::Error::other("can't catch SIGHUP and SIGTERM"));
        }
        let raw = match RawMode::enter(p.lines, p.caps) {
            Ok(r) => r,
            Err(e) => {
                ffi::default_hup_term();
                return Err(e);
            }
        };
        Ok(Term { raw, events: Events::new(TtySource::new(), p.esc_ms) })
    }

    /// One frame's bytes, in one write.
    pub fn write(&mut self, bytes: &[u8]) -> io::Result<()> {
        if bytes.is_empty() {
            return Ok(());
        }
        let mut out = io::stdout().lock();
        out.write_all(bytes)?;
        out.flush()
    }

    /// The profile changed (`L` or `C`): the screen is reset for it.
    pub fn set_profile(&mut self, p: &Profile) {
        self.raw.set_reset(p.lines, p.caps);
        self.events.esc_ms = p.esc_ms;
    }

    pub fn raw(&self) -> &RawMode {
        &self.raw
    }
}

impl Drop for Term {
    fn drop(&mut self) {
        // The fields drop after this, so put the terminal back here
        // first (the guard's own drop then does nothing).
        tty::restore();
        ffi::default_hup_term();
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::testkit::{fuzz_cases, Rng};
    use Key::*;

    /// A script of bytes arriving at set times, sizes from a set time,
    /// and a signal at a set time.  Time moves only as reads wait.
    #[derive(Default)]
    struct Fake {
        now: u64,
        /// (when, bytes), in time order.
        input: VecDeque<(u64, Vec<u8>)>,
        sizes: Vec<(u64, Size)>,
        signal_at: Option<(u64, i32)>,
        eof_at: Option<u64>,
        reads: usize,
    }

    const S80: Size = Size { rows: 24, cols: 80, assumed: false };

    impl Source for Fake {
        fn read(&mut self, buf: &mut [u8], timeout_ms: u64) -> io::Result<Got> {
            self.reads += 1;
            assert!(self.reads < 1_000_000, "reading for ever");
            let end = self.now + timeout_ms;
            if let Some((t, _)) = self.signal_at {
                if t <= end {
                    self.now = self.now.max(t);
                    return Ok(Got::Interrupted);
                }
            }
            match self.input.front() {
                Some((t, _)) if *t <= end => {
                    let (t, b) = self.input.pop_front().unwrap_or_default();
                    self.now = self.now.max(t);
                    let n = b.len().min(buf.len());
                    buf[..n].copy_from_slice(&b[..n]);
                    if n < b.len() {
                        self.input.push_front((t, b[n..].to_vec()));
                    }
                    Ok(Got::Bytes(n))
                }
                _ if self.eof_at.is_some_and(|t| t <= end) => {
                    self.now = self.now.max(self.eof_at.unwrap_or(0));
                    Ok(Got::Eof)
                }
                _ => {
                    self.now = end;
                    Ok(Got::Timeout)
                }
            }
        }

        fn size(&mut self) -> Size {
            self.sizes.iter().rev().find(|(t, _)| *t <= self.now).map_or(S80, |(_, s)| *s)
        }

        fn now_ms(&mut self) -> u64 {
            self.now
        }

        fn signal(&mut self) -> Option<i32> {
            match self.signal_at {
                Some((t, s)) if t <= self.now => {
                    self.signal_at = None;
                    Some(s)
                }
                _ => None,
            }
        }
    }

    fn events(input: &[(u64, &[u8])]) -> Events<Fake> {
        let f = Fake { input: input.iter().map(|(t, b)| (*t, b.to_vec())).collect(), ..Fake::default() };
        Events::new(f, 100)
    }

    /// Every event until `end` ms, ticks every `tick` ms asked for.
    fn all(e: &mut Events<Fake>, end: u64) -> Vec<(u64, Event)> {
        let mut v = Vec::new();
        loop {
            let now = e.now_ms();
            if now >= end {
                return v;
            }
            let ev = e.next(Some(end)).unwrap();
            if ev == Event::Tick {
                return v;
            }
            v.push((e.now_ms(), ev));
            if matches!(ev, Event::Hangup | Event::Signal(_)) {
                return v;
            }
        }
    }

    fn just_keys(v: &[(u64, Event)]) -> Vec<Key> {
        v.iter().filter_map(|(_, e)| if let Event::Key(k, _) = e { Some(*k) } else { None }).collect()
    }

    #[test]
    fn a_lone_esc_waits_for_the_timeout() {
        let mut e = events(&[(10, b"\x1b")]);
        let v = all(&mut e, 1000);
        assert_eq!(just_keys(&v), [Esc]);
        // Esc comes at the timeout after the ESC, not before.
        assert_eq!(v[0].0, 110);
    }

    #[test]
    fn a_sequence_split_across_reads_within_the_timeout_is_one_key() {
        let mut e = events(&[(10, b"\x1b"), (60, b"["), (150, b"A"), (500, b"\x1b[B")]);
        assert_eq!(just_keys(&all(&mut e, 1000)), [Up, Down]);
    }

    #[test]
    fn a_split_longer_than_the_timeout_is_esc_then_keys() {
        // The plan's risk: on a slow line ESC [ A read as Esc, [ and A.
        let mut e = events(&[(10, b"\x1b"), (200, b"[A")]);
        assert_eq!(just_keys(&all(&mut e, 1000)), [Esc, Char('['), Char('A')]);
    }

    #[test]
    fn keys_of_one_read_share_its_number() {
        let mut e = events(&[(10, b"\x1bq"), (20, b"x")]);
        let v = all(&mut e, 1000);
        let reads: Vec<u32> = v.iter().filter_map(|(_, e)| if let Event::Key(_, r) = e { Some(*r) } else { None }).collect();
        assert_eq!(just_keys(&v), [Esc, Char('q'), Char('x')]);
        assert_eq!(reads[0], reads[1]);
        assert_ne!(reads[1], reads[2]);
    }

    #[test]
    fn a_resize_is_seen_within_a_tick() {
        let mut e = events(&[]);
        e.source().sizes.push((400, Size { rows: 30, cols: 100, assumed: false }));
        let v = all(&mut e, 2000);
        assert_eq!(v.len(), 1);
        let (t, ev) = v[0];
        assert_eq!(ev, Event::Resize(Size { rows: 30, cols: 100, assumed: false }));
        assert!((400..=400 + TICK_MS).contains(&t), "seen at {t}");
    }

    #[test]
    fn a_resize_is_seen_while_keys_keep_coming() {
        let input: Vec<(u64, &[u8])> = (0..40).map(|i| (i * 20, &b"x"[..])).collect();
        let mut e = events(&input);
        e.source().sizes.push((100, Size { rows: 30, cols: 100, assumed: false }));
        let v = all(&mut e, 1000);
        let at = v.iter().find(|(_, e)| matches!(e, Event::Resize(_))).map(|(t, _)| *t);
        assert!(at.is_some_and(|t| t <= 100 + TICK_MS), "{at:?}");
    }

    #[test]
    fn ticks_come_when_asked() {
        let mut e = events(&[]);
        assert_eq!(e.next(Some(1234)).unwrap(), Event::Tick);
        assert_eq!(e.now_ms(), 1234);
    }

    #[test]
    fn a_signal_ends_the_wait() {
        let mut e = events(&[(5000, b"x")]);
        e.source().signal_at = Some((300, 15));
        assert_eq!(e.next(None).unwrap(), Event::Signal(15));
        assert!(e.now_ms() < 5000);
    }

    #[test]
    fn a_hang_up_after_the_last_keys() {
        let mut e = events(&[(10, b"ab\x1b")]);
        e.source().eof_at = Some(50);
        let v = all(&mut e, 1000);
        assert_eq!(just_keys(&v), [Char('a'), Char('b'), Esc]);
        assert_eq!(v.last().map(|x| x.1), Some(Event::Hangup));
        assert_eq!(e.next(None).unwrap(), Event::Hangup);
    }

    #[test]
    fn big_reads_are_taken_in_pieces() {
        let big = vec![b'x'; 1000];
        let mut e = events(&[(10, &big)]);
        assert_eq!(just_keys(&all(&mut e, 100)).len(), 1000);
    }

    /// Fuzz-style: random keys sent as bytes arriving in random pieces at
    /// random times, every gap inside a sequence shorter than the Esc
    /// timeout, and Esc followed by a gap longer than it.  Exactly those
    /// keys come out, and each Esc no sooner than the timeout after its
    /// ESC.
    #[test]
    fn random_pieces_at_random_times() {
        let mut r = Rng::new(0xfeed_f00d);
        for case in 0..fuzz_cases(100_000) / 50 {
            let mut want = Vec::new();
            let mut input: Vec<(u64, Vec<u8>)> = Vec::new();
            let mut t = 0u64;
            for _ in 0..r.below(10) {
                let (k, b): (Key, &[u8]) = *r.pick(&[
                    (Up, &b"\x1b[A"[..]),
                    (Left, b"\x1bOD"),
                    (Right, b"\x1b[1;5C"),
                    (Enter, b"\r"),
                    (Tab, b"\t"),
                    (Char('q'), b"q"),
                    (Char('é'), "é".as_bytes()),
                    (Esc, b"\x1b"),
                ]);
                want.push(k);
                // Within a key, gaps under the timeout; between keys,
                // anything, but more than the timeout after an Esc.
                let mut i = 0;
                while i < b.len() {
                    let n = 1 + r.below(b.len() as u64) as usize;
                    input.push((t, b[i..(i + n).min(b.len())].to_vec()));
                    i += n;
                    t += r.below(90);
                }
                t += if k == Esc { 101 + r.below(500) } else { r.below(300) };
            }
            let mut e = Events::new(
                Fake { input: input.into_iter().collect(), ..Fake::default() },
                100,
            );
            let v = all(&mut e, t + 1000);
            assert_eq!(just_keys(&v), want, "case {case}");
        }
    }
}
