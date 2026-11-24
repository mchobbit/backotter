//! The C functions and constants bko-rescue uses, in four families: Linux,
//! the four BSDs, illumos and Haiku.  Values are the libc crate's, and
//! Haiku's are from R1/beta6's own headers (the libc crate's are older).
//! The header test (`bko-rescue --abi` and `abi/check.sh`) compares every
//! one of them with the C headers of the system it runs on.
//!
//! All of bko-rescue's unsafe code is in this file, in small safe wrappers.

use std::ffi::{c_char, c_int, c_short, CStr};
use std::fs::File;
use std::io;
use std::mem::{offset_of, size_of, ManuallyDrop};
use std::os::fd::FromRawFd;
use std::sync::atomic::{AtomicI32, Ordering};

#[cfg(not(any(
    target_os = "linux",
    target_os = "freebsd",
    target_os = "netbsd",
    target_os = "openbsd",
    target_os = "dragonfly",
    target_os = "illumos",
    target_os = "haiku"
)))]
compile_error!(
    "bko-rescue builds for Linux, FreeBSD, NetBSD, OpenBSD, DragonFly, illumos and Haiku only"
);

/// Linux, glibc's layout (the same on x86_64 and aarch64; musl's agrees).
#[cfg(target_os = "linux")]
mod sys {
    use std::ffi::{c_int, c_ulong};
    pub const OS: &str = "linux";
    pub type Tcflag = u32;
    pub type Speed = u32;
    pub const NCCS: usize = 32;
    #[repr(C)]
    #[derive(Clone, Copy, Default)]
    pub struct Termios {
        pub c_iflag: Tcflag,
        pub c_oflag: Tcflag,
        pub c_cflag: Tcflag,
        pub c_lflag: Tcflag,
        pub c_line: u8,
        pub c_cc: [u8; NCCS],
        pub c_ispeed: Speed,
        pub c_ospeed: Speed,
    }
    pub const TCSANOW: c_int = 0;
    pub const TCSADRAIN: c_int = 1;
    pub const BRKINT: Tcflag = 0o2;
    pub const INPCK: Tcflag = 0o20;
    pub const ISTRIP: Tcflag = 0o40;
    pub const INLCR: Tcflag = 0o100;
    pub const IGNCR: Tcflag = 0o200;
    pub const ICRNL: Tcflag = 0o400;
    pub const IXON: Tcflag = 0o2000;
    pub const ISIG: Tcflag = 0o1;
    pub const ICANON: Tcflag = 0o2;
    pub const ECHO: Tcflag = 0o10;
    pub const ECHONL: Tcflag = 0o100;
    pub const IEXTEN: Tcflag = 0o100000;
    pub const VTIME: usize = 5;
    pub const VMIN: usize = 6;
    pub type Req = c_ulong;
    pub const REQ_C: &str = "unsigned long";
    pub const TIOCGWINSZ: Req = 0x5413;
    pub type Nfds = c_ulong;
}

/// FreeBSD, NetBSD, OpenBSD and DragonFly: 4.4BSD's termios, the same on
/// all four (OpenBSD's speeds are `int`, the others' `unsigned int`).
#[cfg(any(
    target_os = "freebsd",
    target_os = "netbsd",
    target_os = "openbsd",
    target_os = "dragonfly"
))]
mod sys {
    use std::ffi::{c_int, c_uint, c_ulong};
    #[cfg(target_os = "freebsd")]
    pub const OS: &str = "freebsd";
    #[cfg(target_os = "netbsd")]
    pub const OS: &str = "netbsd";
    #[cfg(target_os = "openbsd")]
    pub const OS: &str = "openbsd";
    #[cfg(target_os = "dragonfly")]
    pub const OS: &str = "dragonfly";
    pub type Tcflag = u32;
    pub type Speed = u32;
    pub const NCCS: usize = 20;
    #[repr(C)]
    #[derive(Clone, Copy, Default)]
    pub struct Termios {
        pub c_iflag: Tcflag,
        pub c_oflag: Tcflag,
        pub c_cflag: Tcflag,
        pub c_lflag: Tcflag,
        pub c_cc: [u8; NCCS],
        pub c_ispeed: Speed,
        pub c_ospeed: Speed,
    }
    pub const TCSANOW: c_int = 0;
    pub const TCSADRAIN: c_int = 1;
    pub const BRKINT: Tcflag = 0x2;
    pub const INPCK: Tcflag = 0x10;
    pub const ISTRIP: Tcflag = 0x20;
    pub const INLCR: Tcflag = 0x40;
    pub const IGNCR: Tcflag = 0x80;
    pub const ICRNL: Tcflag = 0x100;
    pub const IXON: Tcflag = 0x200;
    pub const ECHO: Tcflag = 0x8;
    pub const ECHONL: Tcflag = 0x10;
    pub const ISIG: Tcflag = 0x80;
    pub const ICANON: Tcflag = 0x100;
    pub const IEXTEN: Tcflag = 0x400;
    /// A status bit, not a setting: 4.4BSD's tty code sets it whenever a
    /// tcsetattr turns ICANON back on, and the next read clears it (RT3).
    pub const PENDIN: Tcflag = 0x2000_0000;
    pub const VMIN: usize = 16;
    pub const VTIME: usize = 17;
    pub type Req = c_ulong;
    pub const REQ_C: &str = "unsigned long";
    pub const TIOCGWINSZ: Req = 0x40087468;
    /// wscons's display type, `_IOR('W', 64, u_int)` (dev/wscons/wsconsio.h),
    /// and the two types that are VGA text.
    #[cfg(target_os = "openbsd")]
    pub const WSDISPLAYIO_GTYPE: Req = 0x4004_5740;
    #[cfg(target_os = "openbsd")]
    pub const WSDISPLAY_TYPE_ISAVGA: u32 = 7;
    #[cfg(target_os = "openbsd")]
    pub const WSDISPLAY_TYPE_PCIVGA: u32 = 8;
    pub type Nfds = c_uint;
}

/// illumos: SysV's termios, no speed fields, and TCSA* are ioctl numbers.
#[cfg(target_os = "illumos")]
mod sys {
    use std::ffi::{c_int, c_ulong};
    pub const OS: &str = "illumos";
    pub type Tcflag = u32;
    pub type Speed = u32;
    pub const NCCS: usize = 19;
    #[repr(C)]
    #[derive(Clone, Copy, Default)]
    pub struct Termios {
        pub c_iflag: Tcflag,
        pub c_oflag: Tcflag,
        pub c_cflag: Tcflag,
        pub c_lflag: Tcflag,
        pub c_cc: [u8; NCCS],
    }
    pub const TCSANOW: c_int = 0x540E;
    pub const TCSADRAIN: c_int = 0x540F;
    pub const BRKINT: Tcflag = 0o2;
    pub const INPCK: Tcflag = 0o20;
    pub const ISTRIP: Tcflag = 0o40;
    pub const INLCR: Tcflag = 0o100;
    pub const IGNCR: Tcflag = 0o200;
    pub const ICRNL: Tcflag = 0o400;
    pub const IXON: Tcflag = 0o2000;
    pub const ISIG: Tcflag = 0o1;
    pub const ICANON: Tcflag = 0o2;
    pub const ECHO: Tcflag = 0o10;
    pub const ECHONL: Tcflag = 0o100;
    pub const IEXTEN: Tcflag = 0o100000;
    pub const VMIN: usize = 4;
    pub const VTIME: usize = 5;
    pub type Req = c_int;
    pub const REQ_C: &str = "int";
    pub const TIOCGWINSZ: Req = 0x5468;
    pub type Nfds = c_ulong;
}

/// Haiku R1/beta6: 16-bit flags, each speed split in two 16-bit halves
/// between them, then `c_line`, two bytes of padding and 11 control chars.
#[cfg(target_os = "haiku")]
mod sys {
    use std::ffi::{c_int, c_ulong};
    pub const OS: &str = "haiku";
    pub type Tcflag = u16;
    pub type Speed = u32;
    pub const NCCS: usize = 11;
    #[repr(C)]
    #[derive(Clone, Copy, Default)]
    pub struct Termios {
        pub c_iflag: Tcflag,
        pub c_ispeed: Tcflag,
        pub c_oflag: Tcflag,
        pub c_ospeed: Tcflag,
        pub c_cflag: Tcflag,
        pub c_ispeed_high: Tcflag,
        pub c_lflag: Tcflag,
        pub c_ospeed_high: Tcflag,
        pub c_line: u8,
        pub _padding: u8,
        pub _padding2: u8,
        pub c_cc: [u8; NCCS],
    }
    pub const TCSANOW: c_int = 0x01;
    pub const TCSADRAIN: c_int = 0x02;
    pub const BRKINT: Tcflag = 0x02;
    pub const INPCK: Tcflag = 0x10;
    pub const ISTRIP: Tcflag = 0x20;
    pub const INLCR: Tcflag = 0x40;
    pub const IGNCR: Tcflag = 0x80;
    pub const ICRNL: Tcflag = 0x100;
    pub const IXON: Tcflag = 0x400;
    pub const ISIG: Tcflag = 0x01;
    pub const ICANON: Tcflag = 0x02;
    pub const ECHO: Tcflag = 0x08;
    pub const ECHONL: Tcflag = 0x40;
    pub const IEXTEN: Tcflag = 0x200;
    pub const VMIN: usize = 4;
    pub const VTIME: usize = 5;
    pub type Req = c_ulong;
    pub const REQ_C: &str = "unsigned long";
    pub const TIOCGWINSZ: Req = 0x8000 + 12;
    pub type Nfds = c_ulong;
}

pub use sys::*;

#[repr(C)]
#[derive(Clone, Copy, Default, Debug, PartialEq, Eq)]
pub struct WinSize {
    pub ws_row: u16,
    pub ws_col: u16,
    pub ws_xpixel: u16,
    pub ws_ypixel: u16,
}

#[repr(C)]
struct PollFd {
    fd: c_int,
    events: c_short,
    revents: c_short,
}

const POLLIN: c_short = 1;
pub const SIGHUP: c_int = 1;
pub const SIGINT: c_int = 2;
pub const SIGTERM: c_int = 15;
/// `SIG_DFL`, a null handler in every family.
const SIG_DFL: usize = 0;

// SAFETY: each declaration matches the family's C prototype, as the header
// test checks in each build guest (sizes, offsets, constants, and the
// symbol names the C program imports).
unsafe extern "C" {
    fn tcgetattr(fd: c_int, t: *mut Termios) -> c_int;
    fn tcsetattr(fd: c_int, when: c_int, t: *const Termios) -> c_int;
    #[cfg(not(target_os = "haiku"))]
    fn ioctl(fd: c_int, req: Req, ...) -> c_int;
    // Haiku's C `ioctl` is a macro over this function, with size 0 for
    // three arguments (unistd.h, R1/beta6).
    #[cfg(target_os = "haiku")]
    fn __ioctl(fd: c_int, req: Req, arg: *mut std::ffi::c_void, size: usize) -> c_int;
    fn poll(fds: *mut PollFd, n: Nfds, timeout: c_int) -> c_int;
    fn signal(sig: c_int, handler: usize) -> usize;
    fn raise(sig: c_int) -> c_int;
    fn ttyname(fd: c_int) -> *mut c_char;
}

#[cfg(not(target_os = "haiku"))]
const IOCTL_LINK: &str = "ioctl";
#[cfg(target_os = "haiku")]
const IOCTL_LINK: &str = "__ioctl";

/// The names the functions above link to, for the header test.
const SYMS: &[(&str, &str)] = &[
    ("tcgetattr", "tcgetattr"),
    ("tcsetattr", "tcsetattr"),
    ("ioctl", IOCTL_LINK),
    ("poll", "poll"),
    ("signal", "signal"),
    ("raise", "raise"),
    ("ttyname", "ttyname"),
];

/// Spare bytes after our termios, so a system struct bigger than ours
/// can't overrun what tcgetattr writes into.
pub const SPARE: usize = 128;

/// A termios as tcgetattr fills it, with room to spare.  Settings are only
/// ever changed in a copy of one read from the terminal.
#[repr(C)]
#[derive(Clone, Copy)]
pub struct TermiosBuf {
    pub t: Termios,
    pub spare: [u8; SPARE],
}

impl TermiosBuf {
    pub fn new(fill: u8) -> TermiosBuf {
        TermiosBuf { t: Termios::default(), spare: [fill; SPARE] }
    }

    /// Every field by name, for comparing two settings.
    pub fn fields(&self) -> Vec<(String, u64)> {
        let t = &self.t;
        let mut v = vec![
            ("c_iflag".to_string(), u64::from(t.c_iflag)),
            ("c_oflag".to_string(), u64::from(t.c_oflag)),
            ("c_cflag".to_string(), u64::from(t.c_cflag)),
            ("c_lflag".to_string(), u64::from(t.c_lflag)),
        ];
        #[cfg(any(target_os = "linux", target_os = "haiku"))]
        v.push(("c_line".to_string(), u64::from(t.c_line)));
        #[cfg(not(target_os = "illumos"))]
        {
            v.push(("c_ispeed".to_string(), u64::from(t.c_ispeed)));
            v.push(("c_ospeed".to_string(), u64::from(t.c_ospeed)));
        }
        #[cfg(target_os = "haiku")]
        {
            v.push(("c_ispeed_high".to_string(), u64::from(t.c_ispeed_high)));
            v.push(("c_ospeed_high".to_string(), u64::from(t.c_ospeed_high)));
        }
        for (i, c) in t.c_cc.iter().enumerate() {
            v.push((format!("c_cc[{i}]"), u64::from(*c)));
        }
        v
    }
}

fn check(rc: c_int) -> io::Result<()> {
    if rc == -1 {
        Err(io::Error::last_os_error())
    } else {
        Ok(())
    }
}

/// tcgetattr into `buf`.
pub fn get_attr(fd: c_int, buf: &mut TermiosBuf) -> io::Result<()> {
    // The pointer is made from the whole buffer, not from `buf.t`, so it
    // covers the spare bytes too.  TermiosBuf is repr(C) with `t` first,
    // so it is the same address.
    let p = (buf as *mut TermiosBuf).cast::<Termios>();
    // SAFETY: p points at a valid, writable TermiosBuf: our struct plus
    // SPARE bytes, more than any family's struct termios, and p may reach
    // all of it.
    check(unsafe { tcgetattr(fd, p) })
}

/// tcsetattr from `buf`; `when` is TCSANOW or TCSADRAIN.
pub fn set_attr(fd: c_int, when: c_int, buf: &TermiosBuf) -> io::Result<()> {
    // From the whole buffer, as in get_attr.
    let p = (buf as *const TermiosBuf).cast::<Termios>();
    // SAFETY: p points at a termios read by tcgetattr (and edited only in
    // its named fields), followed by the spare bytes that read filled in
    // too, and p may reach all of them.
    check(unsafe { tcsetattr(fd, when, p) })
}

/// The window size, TIOCGWINSZ.
pub fn win_size(fd: c_int) -> io::Result<WinSize> {
    let mut ws = WinSize::default();
    check(winsize_ioctl(fd, &mut ws))?;
    Ok(ws)
}

#[cfg(not(target_os = "haiku"))]
fn winsize_ioctl(fd: c_int, ws: &mut WinSize) -> c_int {
    // SAFETY: TIOCGWINSZ writes one struct winsize (four unsigned shorts)
    // through the pointer, which points at a WinSize of that layout.
    unsafe { ioctl(fd, TIOCGWINSZ, ws as *mut WinSize) }
}

#[cfg(target_os = "haiku")]
fn winsize_ioctl(fd: c_int, ws: &mut WinSize) -> c_int {
    // SAFETY: as above; size 0 is what Haiku's ioctl macro passes for
    // three arguments.
    unsafe { __ioctl(fd, TIOCGWINSZ, (ws as *mut WinSize).cast(), 0) }
}

/// The lflag bits the kernel sets by itself, which a check of settings
/// put back has to leave out: PENDIN on the BSDs, nothing elsewhere.
#[cfg(any(target_os = "freebsd", target_os = "netbsd", target_os = "openbsd", target_os = "dragonfly"))]
pub const KERNEL_LFLAG: Tcflag = PENDIN;
#[cfg(not(any(target_os = "freebsd", target_os = "netbsd", target_os = "openbsd", target_os = "dragonfly")))]
pub const KERNEL_LFLAG: Tcflag = 0;

/// Whether `fd` is a screen in VGA text mode (true) or on a framebuffer
/// (false), where the platform can tell; None where it can't, or `fd`
/// isn't a screen.  Only OpenBSD's wscons needs it (RT3: its framebuffer
/// font has no DEC line glyphs).
#[cfg(target_os = "openbsd")]
pub fn vga_text(fd: c_int) -> Option<bool> {
    let mut t: u32 = 0;
    // SAFETY: WSDISPLAYIO_GTYPE writes one u_int through the pointer,
    // which points at a u32; on a tty that isn't a wsdisplay it fails
    // with ENOTTY and writes nothing.
    let rc = unsafe { ioctl(fd, WSDISPLAYIO_GTYPE, &mut t as *mut u32) };
    (rc == 0).then_some(t == WSDISPLAY_TYPE_PCIVGA || t == WSDISPLAY_TYPE_ISAVGA)
}

#[cfg(not(target_os = "openbsd"))]
pub fn vga_text(_fd: c_int) -> Option<bool> {
    None
}

/// Wait up to `timeout_ms` for `fd` to be readable (or hung up).  An
/// interrupting signal is an `Interrupted` error.
pub fn poll_in(fd: c_int, timeout_ms: c_int) -> io::Result<bool> {
    let mut p = PollFd { fd, events: POLLIN, revents: 0 };
    // SAFETY: one valid pollfd, and n is 1.
    let rc = unsafe { poll(&mut p, 1, timeout_ms) };
    if rc < 0 {
        return Err(io::Error::last_os_error());
    }
    Ok(rc > 0 && p.revents != 0)
}

/// The terminal's name, without `/dev/`: `tty1`, `pts/3`, `ttyC0`.
pub fn tty_name(fd: c_int) -> Option<String> {
    // SAFETY: ttyname returns NULL or a NUL-terminated string in a static
    // buffer; we copy it at once, and only the main thread calls this.
    let p = unsafe { ttyname(fd) };
    if p.is_null() {
        return None;
    }
    // SAFETY: p is non-NULL and NUL-terminated (above).
    let s = unsafe { CStr::from_ptr(p) }.to_string_lossy().into_owned();
    Some(s.strip_prefix("/dev/").unwrap_or(&s).to_string())
}

static SIGNALLED: AtomicI32 = AtomicI32::new(0);

extern "C" fn on_signal(sig: c_int) {
    // An atomic store is all a handler may safely do here, and on illumos
    // signal() too.
    SIGNALLED.store(sig, Ordering::SeqCst);
    rearm(sig);
}

/// illumos's signal() is SysV's: the handler goes back to SIG_DFL before
/// it runs, so a second SIGTERM in the cleanup would kill us with the
/// terminal still raw.  Set it again at once.  The others keep it.
#[cfg(target_os = "illumos")]
fn rearm(sig: c_int) {
    let h = on_signal as extern "C" fn(c_int) as usize;
    // SAFETY: signal() is async-signal-safe (POSIX), and on_signal is an
    // extern "C" fn(int) that only does an atomic store and this.
    unsafe { signal(sig, h) };
}

#[cfg(not(target_os = "illumos"))]
fn rearm(_sig: c_int) {}

/// These signals only set a flag, read by `take_signal`.  poll then
/// returns early with EINTR.  False if one couldn't be caught.
pub fn catch(sigs: &[c_int]) -> bool {
    let h = on_signal as extern "C" fn(c_int) as usize;
    let mut ok = true;
    for &sig in sigs {
        // SAFETY: on_signal is an extern "C" fn(int) that only does an
        // atomic store, which is async-signal-safe.
        ok &= unsafe { signal(sig, h) } != usize::MAX;
    }
    ok
}

/// These signals back to their default action.
pub fn default(sigs: &[c_int]) {
    for &sig in sigs {
        // SAFETY: SIG_DFL is a valid handler for any catchable signal.
        unsafe { signal(sig, SIG_DFL) };
    }
}

/// A signal that came and wasn't taken yet, raised again now that its
/// action is the default, so it ends the program as it would have.
pub fn raise_pending() {
    if let Some(sig) = take_signal() {
        // SAFETY: raise() takes any signal number; this is one we caught.
        unsafe { raise(sig) };
    }
}

/// SIGHUP and SIGTERM only set a flag (`catch`).
pub fn catch_hup_term() -> bool {
    catch(&[SIGHUP, SIGTERM])
}

/// SIGHUP and SIGTERM back to their default action, once the terminal is
/// back: nothing reads the flag after the full-screen part, and a caught
/// signal would be lost.  A signal that came and wasn't taken yet is
/// raised again, so it ends the program as it would have.  One the
/// caller took (`Event::Signal`) is the caller's: it exits with 128 + it.
pub fn default_hup_term() {
    default(&[SIGHUP, SIGTERM]);
    raise_pending();
}

/// Whether a caught signal is waiting, without taking it.
pub fn signal_pending() -> bool {
    SIGNALLED.load(Ordering::SeqCst) != 0
}

/// The last signal caught, once.
pub fn take_signal() -> Option<c_int> {
    match SIGNALLED.swap(0, Ordering::SeqCst) {
        0 => None,
        s => Some(s),
    }
}

/// Standard input as a File, read directly (std's stdin buffers, which
/// would hide bytes from poll).  It is never closed.
pub fn stdin_file() -> ManuallyDrop<File> {
    // SAFETY: fd 0 stays open for the whole program, and ManuallyDrop
    // means this File never closes it.
    ManuallyDrop::new(unsafe { File::from_raw_fd(0) })
}

/// `bko-rescue --abi`: our view of the C ABI, one fact a line, in the order
/// abi/abi.c prints the system's.  `sym` lines come last; the C side has
/// none (abi/check.sh finds them with nm).
pub fn abi_lines() -> Vec<String> {
    let mut v = Vec::new();
    let mut size = |name: &str, n: usize| v.push(format!("size {name} {n}"));
    size("pointer", size_of::<usize>());
    size("tcflag_t", size_of::<Tcflag>());
    size("cc_t", 1);
    size("speed_t", size_of::<Speed>());
    size("termios", size_of::<Termios>());
    let off = |f: &str, n: usize| format!("offset termios.{f} {n}");
    v.push(off("c_iflag", offset_of!(Termios, c_iflag)));
    v.push(off("c_oflag", offset_of!(Termios, c_oflag)));
    v.push(off("c_cflag", offset_of!(Termios, c_cflag)));
    v.push(off("c_lflag", offset_of!(Termios, c_lflag)));
    #[cfg(any(target_os = "linux", target_os = "haiku"))]
    v.push(off("c_line", offset_of!(Termios, c_line)));
    v.push(off("c_cc", offset_of!(Termios, c_cc)));
    #[cfg(not(target_os = "illumos"))]
    {
        v.push(off("c_ispeed", offset_of!(Termios, c_ispeed)));
        v.push(off("c_ospeed", offset_of!(Termios, c_ospeed)));
    }
    #[cfg(target_os = "haiku")]
    {
        v.push(off("c_ispeed_high", offset_of!(Termios, c_ispeed_high)));
        v.push(off("c_ospeed_high", offset_of!(Termios, c_ospeed_high)));
    }
    let mut konst = |name: &str, n: u64| v.push(format!("const {name} {n:#x}"));
    konst("NCCS", NCCS as u64);
    konst("TCSANOW", TCSANOW as u64);
    konst("TCSADRAIN", TCSADRAIN as u64);
    for (n, f) in [
        ("BRKINT", BRKINT),
        ("ICRNL", ICRNL),
        ("INLCR", INLCR),
        ("IGNCR", IGNCR),
        ("ISTRIP", ISTRIP),
        ("INPCK", INPCK),
        ("IXON", IXON),
        ("ICANON", ICANON),
        ("ECHO", ECHO),
        ("ECHONL", ECHONL),
        ("ISIG", ISIG),
        ("IEXTEN", IEXTEN),
    ] {
        konst(n, u64::from(f));
    }
    #[cfg(any(target_os = "freebsd", target_os = "netbsd", target_os = "openbsd", target_os = "dragonfly"))]
    konst("PENDIN", u64::from(PENDIN));
    konst("VMIN", VMIN as u64);
    konst("VTIME", VTIME as u64);
    konst("TIOCGWINSZ", TIOCGWINSZ as u64);
    #[cfg(target_os = "openbsd")]
    {
        konst("WSDISPLAYIO_GTYPE", WSDISPLAYIO_GTYPE as u64);
        konst("WSDISPLAY_TYPE_ISAVGA", u64::from(WSDISPLAY_TYPE_ISAVGA));
        konst("WSDISPLAY_TYPE_PCIVGA", u64::from(WSDISPLAY_TYPE_PCIVGA));
    }
    v.push(format!("type ioctl.request {REQ_C}"));
    v.push(format!("size winsize {}", size_of::<WinSize>()));
    v.push(format!("offset winsize.ws_row {}", offset_of!(WinSize, ws_row)));
    v.push(format!("offset winsize.ws_col {}", offset_of!(WinSize, ws_col)));
    v.push(format!("offset winsize.ws_xpixel {}", offset_of!(WinSize, ws_xpixel)));
    v.push(format!("offset winsize.ws_ypixel {}", offset_of!(WinSize, ws_ypixel)));
    v.push(format!("size nfds_t {}", size_of::<Nfds>()));
    v.push(format!("size pollfd {}", size_of::<PollFd>()));
    v.push(format!("offset pollfd.fd {}", offset_of!(PollFd, fd)));
    v.push(format!("offset pollfd.events {}", offset_of!(PollFd, events)));
    v.push(format!("offset pollfd.revents {}", offset_of!(PollFd, revents)));
    v.push(format!("const POLLIN {:#x}", POLLIN));
    v.push(format!("const SIGHUP {:#x}", SIGHUP));
    v.push(format!("const SIGINT {:#x}", SIGINT));
    v.push(format!("const SIGTERM {:#x}", SIGTERM));
    v.push(format!("const SIG_DFL {:#x}", SIG_DFL));
    for (f, link) in SYMS {
        v.push(format!("sym {f} {link}"));
    }
    v
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn abi_lines_are_unique_and_end_in_syms() {
        let v = abi_lines();
        let mut keys: Vec<String> =
            v.iter().map(|l| l.rsplit_once(' ').map(|(k, _)| k).unwrap_or(l).to_string()).collect();
        keys.sort();
        keys.dedup();
        assert_eq!(keys.len(), v.len());
        assert!(v.iter().skip_while(|l| !l.starts_with("sym ")).all(|l| l.starts_with("sym ")));
        assert_eq!(v.iter().filter(|l| l.starts_with("sym ")).count(), 7);
    }

    #[cfg(target_os = "linux")]
    #[test]
    fn linux_values_as_the_plan_expects() {
        let v = abi_lines();
        for want in [
            "size termios 60",
            "offset termios.c_lflag 12",
            "offset termios.c_cc 17",
            "const TIOCGWINSZ 0x5413",
            "size nfds_t 8",
            "size pollfd 8",
            "type ioctl.request unsigned long",
        ] {
            assert!(v.iter().any(|l| l == want), "{want}");
        }
    }

    #[test]
    fn fields_cover_every_control_char() {
        let b = TermiosBuf::new(0);
        assert_eq!(b.fields().iter().filter(|(n, _)| n.starts_with("c_cc[")).count(), NCCS);
    }
}
