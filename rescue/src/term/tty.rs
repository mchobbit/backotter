//! Raw mode and putting it back, the window size, and reading with a
//! timeout.  Raw mode clears flags by hand (illumos has no cfmakeraw), in
//! a copy of what tcgetattr read (Haiku's struct is only ever changed that
//! way, and so is everyone's).

use std::fs::File;
use std::io::{self, Read as _, Write as _};
use std::mem::ManuallyDrop;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Mutex, Once, OnceLock, TryLockError};

use super::ffi::{self, Termios, TermiosBuf};

/// What raw mode clears and sets.  `c_cflag` (the line's speed and
/// framing) and OPOST are left as the launcher set them.
pub fn make_raw(t: &mut Termios) {
    t.c_lflag &= !(ffi::ICANON | ffi::ECHO | ffi::ECHONL | ffi::ISIG | ffi::IEXTEN);
    t.c_iflag &= !(ffi::BRKINT
        | ffi::ICRNL
        | ffi::INLCR
        | ffi::IGNCR
        | ffi::ISTRIP
        | ffi::INPCK
        | ffi::IXON);
    t.c_cc[ffi::VMIN] = 1;
    t.c_cc[ffi::VTIME] = 0;
}

/// Whether `t` reads back as raw: every bit cleared, VMIN 1, VTIME 0.
pub fn is_raw(t: &Termios) -> bool {
    let mut r = *t;
    make_raw(&mut r);
    r.c_lflag == t.c_lflag && r.c_iflag == t.c_iflag && r.c_cc == t.c_cc
}

static ORIGINAL: OnceLock<TermiosBuf> = OnceLock::new();
static RAW_ON: AtomicBool = AtomicBool::new(false);
static RESET: Mutex<Vec<u8>> = Mutex::new(Vec::new());
static HOOK: Once = Once::new();

/// Raw mode, put back when dropped, on a panic (before the message
/// prints) and by `restore`.
pub struct RawMode {
    _private: (),
}

impl RawMode {
    /// Raw mode on fd 0.  `reset` is what to write to the screen when
    /// putting it back (`Out::reset_bytes`).
    pub fn enter(reset: Vec<u8>) -> io::Result<RawMode> {
        let mut orig = TermiosBuf::new(0);
        ffi::get_attr(0, &mut orig)?;
        // The first original is the one to go back to, if raw mode is
        // entered twice.
        let orig = *ORIGINAL.get_or_init(|| orig);
        set_reset(reset);
        HOOK.call_once(|| {
            let prev = std::panic::take_hook();
            std::panic::set_hook(Box::new(move |info| {
                restore();
                prev(info);
            }));
        });
        let mut raw = orig;
        make_raw(&mut raw.t);
        RAW_ON.store(true, Ordering::SeqCst);
        ffi::set_attr(0, ffi::TCSADRAIN, &raw)?;
        let mut back = TermiosBuf::new(0);
        ffi::get_attr(0, &mut back)?;
        if !is_raw(&back.t) {
            restore();
            return Err(io::Error::other("the terminal didn't take raw mode"));
        }
        Ok(RawMode { _private: () })
    }

    /// A new reset sequence, after the profile changed.
    pub fn set_reset(&self, reset: Vec<u8>) {
        set_reset(reset);
    }
}

fn set_reset(reset: Vec<u8>) {
    match RESET.lock() {
        Ok(mut g) => *g = reset,
        Err(p) => *p.into_inner() = reset,
    }
}

impl Drop for RawMode {
    fn drop(&mut self) {
        restore();
    }
}

/// Put the terminal back, once: the reset bytes, then the original
/// settings (TCSADRAIN, so the bytes go out first).
pub fn restore() {
    if !RAW_ON.swap(false, Ordering::SeqCst) {
        return;
    }
    let reset = match RESET.try_lock() {
        Ok(g) => g.clone(),
        Err(TryLockError::Poisoned(p)) => p.into_inner().clone(),
        Err(TryLockError::WouldBlock) => b"\x1b[0m".to_vec(),
    };
    let mut out = io::stdout();
    let _ = out.write_all(&reset);
    let _ = out.flush();
    if let Some(o) = ORIGINAL.get() {
        let _ = ffi::set_attr(0, ffi::TCSADRAIN, o);
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Size {
    pub rows: u16,
    pub cols: u16,
    /// The terminal said 0x0 (a serial line), or nothing: 80x24 assumed.
    pub assumed: bool,
}

/// The window size of fd 0; 0x0 or an error is taken as 80x24.
pub fn size() -> Size {
    match ffi::win_size(0) {
        Ok(ws) if ws.ws_row > 0 && ws.ws_col > 0 => {
            Size { rows: ws.ws_row, cols: ws.ws_col, assumed: false }
        }
        _ => Size { rows: 24, cols: 80, assumed: true },
    }
}

pub enum Got {
    Bytes(usize),
    Timeout,
    /// A signal came (SIGHUP, SIGTERM): `ffi::take_signal` says which.
    Interrupted,
    /// The terminal hung up.
    Eof,
}

/// fd 0, read directly.
pub struct Input {
    file: ManuallyDrop<File>,
}

impl Default for Input {
    fn default() -> Self {
        Input::new()
    }
}

impl Input {
    pub fn new() -> Input {
        Input { file: ffi::stdin_file() }
    }

    /// Wait up to `timeout_ms` for bytes, and read what is there.
    pub fn read(&mut self, buf: &mut [u8], timeout_ms: i32) -> io::Result<Got> {
        match ffi::poll_in(0, timeout_ms) {
            Ok(false) => return Ok(Got::Timeout),
            Ok(true) => {}
            Err(e) if e.kind() == io::ErrorKind::Interrupted => return Ok(Got::Interrupted),
            Err(e) => return Err(e),
        }
        match self.file.read(buf) {
            Ok(0) => Ok(Got::Eof),
            Ok(n) => Ok(Got::Bytes(n)),
            Err(e) if e.kind() == io::ErrorKind::Interrupted => Ok(Got::Interrupted),
            // EIO: the other end of a pty went away.  (Haiku's errnos are
            // negative; there it is an error, which ends the program too.)
            Err(e) if e.raw_os_error() == Some(5) => Ok(Got::Eof),
            Err(e) => Err(e),
        }
    }
}

/// `bko-rescue --check-tty`: the raw mode round trip, for checks.sh.  It
/// reads the settings, sets raw mode, reads them back and checks the
/// cleared bits read back cleared and every other field unchanged, then
/// puts the original back and checks that too.  It draws nothing.
/// Returns the lines to print and whether it passed.
pub fn check_tty() -> (Vec<String>, bool) {
    use std::io::IsTerminal;
    let mut v = Vec::new();
    if !io::stdin().is_terminal() {
        v.push("check-tty: FAIL: standard input is not a terminal".to_string());
        return (v, false);
    }
    let mut ok = true;
    let mut fail = |v: &mut Vec<String>, s: String| {
        v.push(format!("check-tty: FAIL: {s}"));
        ok = false;
    };
    // Two reads with different fill bytes: a spare byte changed in both is
    // one the system's struct termios has and ours doesn't.
    let (mut a, mut b) = (TermiosBuf::new(0xa5), TermiosBuf::new(0x5a));
    if let Err(e) = ffi::get_attr(0, &mut a).and_then(|_| ffi::get_attr(0, &mut b)) {
        fail(&mut v, format!("tcgetattr: {e}"));
        return (v, false);
    }
    let over = (0..ffi::SPARE).filter(|&i| a.spare[i] != 0xa5 || b.spare[i] != 0x5a).count();
    if over > 0 {
        fail(&mut v, format!("tcgetattr wrote {over} bytes past our struct termios"));
    }
    let orig = a;
    let mut raw = orig;
    make_raw(&mut raw.t);
    let mut back = TermiosBuf::new(0);
    let set = ffi::set_attr(0, ffi::TCSADRAIN, &raw).and_then(|_| ffi::get_attr(0, &mut back));
    // Whatever happened, the original goes back before anything else.
    let put = ffi::set_attr(0, ffi::TCSADRAIN, &orig);
    let mut again = TermiosBuf::new(0);
    let reread = ffi::get_attr(0, &mut again);
    match set {
        Err(e) => fail(&mut v, format!("setting raw mode: {e}")),
        Ok(()) => {
            if !is_raw(&back.t) {
                fail(&mut v, "raw mode's cleared bits didn't read back cleared".to_string());
            }
            for d in differences(&raw, &back) {
                fail(&mut v, format!("in raw mode, {d}"));
            }
        }
    }
    match put.and(reread) {
        Err(e) => fail(&mut v, format!("putting the settings back: {e}")),
        Ok(()) => {
            for d in differences(&orig, &again) {
                fail(&mut v, format!("after putting back, {d}"));
            }
        }
    }
    let n = orig.fields().len();
    if ok {
        v.push(format!("check-tty: ok: raw mode set and put back, {n} fields as expected"));
    }
    (v, ok)
}

fn differences(want: &TermiosBuf, got: &TermiosBuf) -> Vec<String> {
    want.fields()
        .into_iter()
        .zip(got.fields())
        .filter(|(w, g)| w.1 != g.1)
        .map(|(w, g)| format!("{} is {:#x}, not {:#x}", w.0, g.1, w.1))
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn raw_clears_only_its_bits() {
        let mut t = Termios::default();
        t.c_lflag = !0;
        t.c_iflag = !0;
        t.c_oflag = 0x5;
        t.c_cflag = 0x1234;
        t.c_cc = [7; ffi::NCCS];
        let before = t;
        make_raw(&mut t);
        assert!(is_raw(&t));
        assert!(!is_raw(&before));
        assert_eq!(t.c_lflag & ffi::ICANON, 0);
        assert_eq!(t.c_lflag & ffi::ISIG, 0);
        assert_ne!(t.c_lflag, 0, "other lflag bits stay");
        assert_eq!(t.c_iflag & ffi::IXON, 0);
        assert_eq!((t.c_oflag, t.c_cflag), (0x5, 0x1234));
        assert_eq!((t.c_cc[ffi::VMIN], t.c_cc[ffi::VTIME]), (1, 0));
        let others = (0..ffi::NCCS).filter(|&i| i != ffi::VMIN && i != ffi::VTIME);
        assert!(others.into_iter().all(|i| t.c_cc[i] == 7));
    }

    #[test]
    fn differences_name_the_field() {
        let a = TermiosBuf::new(0);
        let mut b = a;
        b.t.c_oflag = 3;
        assert_eq!(differences(&a, &b), ["c_oflag is 0x3, not 0x0"]);
    }
}
