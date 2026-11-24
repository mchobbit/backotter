# bko-rescue: the terminal layer and its widgets

Status: approved 2026-11-19, with every recommendation (D1-D13); D14 (dry run) added at the user's request 2026-11-24.  RT1 and RT2 built 2026-11-24 (see "What RT1 and RT2 found"), RT4 and RT5 the same day ahead of RT3, with a provisional table (see "What RT4 and RT5 found"), RT6 and RT7 after them (see "What RT6 and RT7 found"), RT8 after those (see "What RT8 found"), RT3 the same day on every console of the seven images, its findings folded into the table (see "What RT3 found"); RT9 on are not.  RT10's own spec (D12), `holt-json.md`, approved 2026-11-24 with every recommendation (J1-J21); D14's first lock amended by J6.

An implementation plan for the stick's terminal UI that `ui.md` describes and
the user decided on 2026-11-19: one Whiptail-style 80x24 program,
`bko-rescue`, on all seven rescue platforms, in Rust, with our own small
terminal layer and widgets, no crates and no C by default.  It covers the
crate, the terminal layer, the screen buffer, the widgets, plain mode, the
console spike, building and shipping, and the tests.  The restore screens on
top are later steps, scoped here but not designed in detail: they wait on
bko-holt's `--json` protocol, which isn't built.

**Conventions.** est. marks an estimate.  *Check* marks a fact the repo
doesn't settle, to be settled at the step named (most at RT1's header test or
RT3's spike).  RT1-RT14 are the steps, D1-D13 the decisions left to the user.
Code references are to main at b9cacea plus the uncommitted tree, read
2026-11-19.  Nothing was built or run for this plan.

## Summary

- **One crate, `rescue/`,** beside `holt/`, building one file, `bko-rescue`.
  No dependencies: no crates.io, no C.  It builds offline with Debian 13's
  cargo (Rust 1.85 is the floor) and the way `build-holt.sh` builds bko-holt.
  It shares one file of holt's source, the JSON reader (`holt/src/json.rs`),
  and links nothing of bko-holt (D2).
- **Four layers:** the terminal (raw mode, size, keys, output profiles), a
  screen buffer with diffed redraw, Whiptail's widgets, and plain mode.  The
  app talks to one dialog API, a `Ui` trait, with three implementations:
  full screen, plain, and scripted (for tests).
- **FFI** is hand-written `extern "C"` in four `cfg` families (Linux, the
  four BSDs, illumos, Haiku).  A header test in each build guest compares our
  sizes, offsets, constants and symbol names with a small C program built
  there.  A C shim of about 50 lines comes in only on the triggers below.
- **Console profiles** pick UTF-8, DEC or ASCII line drawing, and colour or
  none, from the OS, the tty's name, TERM and the locale.  The launcher and
  two keys on the first screen can force either.
- **The spike comes first** (RT2, RT3): a box, a list, a gauge and a password
  field on every platform's screen console and serial console, OpenBSD and
  DragonFly first.  What it finds goes into the profile table before the
  layer is finished.
- **Shipping:** built in the same guests as bko-holt, pinned in its own set,
  on the images first as a tool whose self-checks run in every boot-test, and
  only later as the console.
- **Tests:** golden screens at 80x24, byte-level checks of what each profile
  sends, fuzz-style tests of the key decoder and every parser (all inside
  `cargo test`), a pty harness against a fake bko-holt, boot-test checks, and
  at the end a HoltTest1 variant that drives bko-rescue over the serial
  console.
- **Size:** about 1,750-2,400 lines for the layer, sanitiser, screen buffer
  and widgets.  ui.md's 1,400-1,900 counted the layer (400-600) and the
  widgets (1,000-1,300); the sanitiser and screen buffer add 300-450, and
  the layer comes out a little bigger (440-670).  Plain mode is 250-350
  more.  Tests not counted.
- **Time:** RT1-RT9, the TUI, ~8.5 d est.  RT10, the protocol in holt/,
  3-4 d est. here; its own spec (D12, `holt-json.md`) counts ~8 d for the
  core RT12 needs and ~16 d for all of it.  RT11-RT14, the screens, the
  launchers and whole-machine runs, ~9.5 d est.  About 26-34 d in all.

## Why this order

- **The consoles are the unknown, not the widgets.**  ui.md's research found
  the colour-code and line-drawing traps from source code.  Only a real
  console shows what else is there (cursor hiding, combined SGR parameters,
  key sequences, timing at 9600 baud).  So a spike on real consoles comes
  before the layer is finished, and its findings are table entries, not
  rewrites.
- **The protocol doesn't exist yet.**  The layer, the widgets, plain mode and
  their tests need none of it.  The screens that do (Disks, Review, Restore)
  come after RT10.
- **On the images early.**  Once bko-rescue is on every image as a tool,
  every image round's boot-test runs its terminal self-checks on the real
  image, long before it becomes the console.

## What's settled already

From ui.md ("The stick's restore program", "Toolkit") and the user,
2026-11-19.  Not repeated in detail here:

- 80x24, centred on a bigger terminal; VT100 and 8 colours; no terminfo, no
  mouse, no 256 colours.  Colour never carries meaning alone.
- Only SGR 0, 1 (bold), 7 (reverse), 30-37 and 40-47.  Never `38;5` or
  `48;5`: OpenBSD's wscons and DragonFly's syscons read them as blink,
  underline and reverse; NetBSD's wscons drops them on VGA text.
- Line drawing: UTF-8 on the Linux VT, FreeBSD vt(4), Haiku's Terminal and
  illumos's framebuffer tem; DEC special graphics on NetBSD and OpenBSD
  wscons; ASCII on DragonFly syscons, illumos VGA text and serial consoles.
  Over ssh the locale decides.  A setting forces any of the three.
- Haiku's `struct termios` comes from beta6's `termios.h` (the libc crate's
  is older), and is changed only by read-modify-write.  `nfds_t` is
  `unsigned long` on Linux, illumos and Haiku, `unsigned int` on the BSDs.
  No `cfmakeraw` (illumos lacks it): flags are cleared by hand everywhere.
- Serial consoles report a 0x0 window: the launcher sets 80x24 with `stty`.
  illumos's tem prints escapes it doesn't know, so each console gets only
  what it takes.  The terminal is restored on panic and exit.  Resizes are
  noticed by `poll` timing out, not by a signal handler.  (ui.md's "What it
  looks like" still says "SIGWINCH"; its "Toolkit" section is the decision.)
- Keys: arrows, Tab, Enter, Esc (with a timeout), Space, Backspace and
  letters.
- Security: control characters and escape sequences stripped from every
  string shown; hard limits in every parser; passwords wiped after use,
  never in argv, only in a 0600 file in RAM.

## What the repo has

### In holt/, for reuse

| What | Reusable? | Why |
|---|---|---|
| `src/json.rs` (696 lines with tests) | Yes, as source | Uses only `std`; its tests use only `super::*`.  `parse` has a depth limit (256) but no size limit, so callers cap input first. |
| `src/sys.rs`'s FFI | The pattern only | Two declarations: `geteuid`, and `statvfs` with a `#[repr(C)]` struct for 64-bit Linux and a stub elsewhere, with SAFETY comments.  No termios, ioctl or poll anywhere in holt. |
| `sys::RealRunner` | No | It drops stderr (rustic-bko's errors are text there) and runs only tools in `SYSTEM_PATH`.  `FakeRunner`'s idea, a table of answers, is reused in the fake bko-holt. |
| `src/load/exec.rs` | No | Tied to `Cmd` and `Layout`, and it collects output in `String`s: the very thing ui.md wants changed. |
| `bytes::sha256_hex`, `probe/fat.rs` | Not as source | The config reader needs both (system.json's checksum; illumos's boot disk is found by its FAT boot sector).  `bytes.rs`'s tests need `crate::testutil`, so it can't be included alone.  See D11. |

A path dependency on the `bko_holt` crate works offline, but it isn't
clean.  It compiles all 57,000 lines of holt in every build guest for the
700 used.  It makes every holt commit (several sessions edit holt every day)
a reason to rebuild bko-rescue.  And it blurs ui.md's rule that the UI
"never links bko-holt's code".  So D2 recommends sharing `json.rs` as
source, and putting anything bigger in bko-holt itself (D11).

### The consoles, as the images start them today

| Platform | Screen | Serial | Started by |
|---|---|---|---|
| linux | `tty1`, `TERM` from agetty's `${TERM}` (*Check*: `linux`) | `ttyS0` (`ttyAMA0` on arm64 *Check*), 115200, `TERM` *Check* (`vt220`) | `getty@tty1` and `serial-getty@` drop-ins: `agetty --autologin root --login-program /usr/lib/backotter/console` |
| freebsd | `ttyv0`, TERM from `/etc/ttys`'s own field (*Check*: `xterm`) | `ttyu0`, `3wire.115200`, *Check* (`vt100`) | gettytab `backotter`, `backotter.serial`: `al=root:lo=/usr/local/lib/backotter/console` |
| netbsd | `ttyE0`, `wsvt25` (`wsconscfg -e vt100 0`) | `tty00`, `std.115200`, `vt100` | gettytab `bko.Pc`, `bko.serial` |
| openbsd | `ttyC0`, `vt220` | `tty00`, 115200, `vt220` | `autologin.c` (built in the image's build VM), which sets `TERM=vt220` on both |
| dragonfly | `ttyv0`, `cons25` | `ttyd0`, **9600**, `dialup` | gettytab `bko.Pc`, `bko.9600` |
| illumos | `/dev/console` (and `/dev/vt/N`), `sun-color` | `/dev/term/a`, or `/dev/console` when the kernel's console is `ttya`; 115200, `vt100` | SMF: `console-login` and `backotter/console:serial` run `on-tty` |
| haiku | a Terminal window (a pty), `TERM` *Check* (`xterm-256color`) | `on-serial.py`'s pty bridged to `/dev/ports/pc_serial0`, 115200, `vt100` | UserBootscript; launch_daemon runs `haiku/boot` |

All three launchers (`stick/layer/usr/lib/backotter/console`,
`stick/bsd/console`, `stick/haiku/console`) are today's placeholder: a status
screen and a menu ending `Choose [1-4]: `.

Three findings that shape the plan:

- **TERM can't tell OpenBSD's screen from its serial port** (`autologin.c`
  sets `vt220` on both), and **only TERM tells Haiku's two consoles apart**
  (both are ptys).  So detection goes by tty name first, TERM second.
- **DragonFly's serial console runs at 9600 baud** (960 bytes a second; that
  is boot-test.py's `TYPING_RATE` for it).  A full 80x24 colour frame is
  about 3 KB, about 3 s there.  Diffed redraw isn't an optimisation on that
  console; it's what makes it usable.
- **boot-test.py, img-boot.py and test_holt_img.py wait for
  `Choose [1-4]: ` and type `1` for the shell.**  ImgTest2's `+img` cells and
  every boot-test depend on it.  Making bko-rescue the console would break
  them unless they learn its first screen (D7, RT13).

### Test tools to build on

- `stick/boot-test.py`: boots an image in QEMU, drives the serial console
  (its `Console` class: `expect`, `send`, per-platform typing rates), runs
  `stick/PLATFORM/checks.sh` line by line.
- `testvm/holttest1d/img-boot.py`: HoltTest's `+img` variant, the image as
  the rescuer, sshd brought up from the serial shell with a per-run key.
- `testvm/console-boot.py`: types passphrases at boot prompts over serial.
- `testvm/qubes/qubes-drive.py` (`shot MONITOR PNG`: a screendump) and
  `testvm/guix/console-type.py` (typing on a virtual terminal with the
  monitor's `sendkey`): the screen console can be driven and photographed.
  `vm-run` passes QEMU options after `--`, so a monitor socket is one
  option.
- `testvm/holttest1d/build-holt.sh`: builds in each platform's build guest
  (`freebsd14`, `netbsd11`, `openbsd8`, `dragonfly6`, `omnios58`, `haiku6`),
  on a farm host for Linux, on Bigfoot for linux-arm64.  It checks
  `command -v cargo && command -v cc` in the guest and installs Rust (and
  gcc on illumos and Haiku) when missing, so every build guest has a C
  compiler by the time it builds.
- GateTest2's `gate:holt` (holt's `cargo test` in RAM) and `gate:bin`
  (`bincheck.sh`: static, ABI and version checks of shipped binaries).
- `testvm/pick-host` and `cmp-run.sh --reserve` for where jobs run.

## The crate

### Where it lives and how it builds

`rescue/` at the top of the repo, beside `holt/` (D1):

```toml
[package]
name = "bko-rescue"
version = "0.1.0"
edition = "2021"
rust-version = "1.85"
license = "GPL-3.0-or-later"      # it includes holt's json.rs
publish = false

# No dependencies, as holt/'s: offline, with Debian's own cargo.
[dependencies]

[lib]
name = "bko_rescue"
path = "src/lib.rs"

[[bin]]
name = "bko-rescue"
path = "src/main.rs"

[profile.release]
lto = true
strip = true
```

- `Cargo.lock` is committed; builds and tests run `--locked --offline`.
- No cargo workspace: holt/ stays a crate on its own, as its build scripts
  and `gate:holt` expect.
- `src/json.rs` is `#[path = "../../holt/src/json.rs"] mod json;`.  The
  build scripts ship the tree from the repo root, `rescue/` plus
  `holt/src/json.rs`, so the relative path holds in a guest.
- An unsupported `target_os` is a `compile_error!`, not a silent stub.  The
  Mac can't build it (it has no Rust, and macOS isn't a family); tests run
  on the farm.
- Rust features used are all older than 1.85: `offset_of!` (1.77, for the
  header test), `IsTerminal` (1.70), `OnceLock` (1.70).

### Modules

```
rescue/
  Cargo.toml  Cargo.lock  README.md
  abi/abi.c                 the header test's C side (RT1)
  src/main.rs               options: --plain --lines --colour --holt --rustic --dry-run,
                            and the hidden --abi --check-tty --profile --spike --demo
  src/lib.rs
  src/json.rs               holt's, by #[path]
  src/term/ffi.rs           extern "C" and the four families' constants      120-180
  src/term/tty.rs           raw mode guard, size, read with timeout, restore  80-120
  src/term/keys.rs          bytes to keys: a pure state machine               100-150
  src/term/event.rs         the poll loop: keys, Esc timeout, resize, signals (RT4)
  src/term/profile.rs       which console this is: lines, colour, caps        80-120
  src/term/out.rs           the escape sequences each profile may send        60-100
  src/text.rs               sanitise, width, wrap, truncate; the Clean type   100-150
  src/testkit.rs            for the tests: generator, goldens, byte checker (RT5)
  src/screen.rs             cells, the 80x24 canvas, diffed redraw            200-300
  src/widgets/              dialog frame, buttons, menu, radio list, input,
                            password, yes/no and message, gauge, text view;
                            focus and the full-screen runner                1,000-1,300
  src/ui.rs                 the Ui trait and the scripted implementation  (in the widgets'
  src/secret.rs             the password buffer and key files              count above)
  src/plain.rs              plain mode                                        250-350
  src/app/                  later (RT11, RT12): the screens, strings.rs, the
                            protocol client, following load
  tests/screens/            golden screens
  tests/bytes/              golden byte streams per profile
```

The terminal layer (`term/`) comes to 440-670 lines, against ui.md's
400-600: the profile table and the sequence rules are a little bigger than
counted then.  ui.md didn't count the screen buffer and the sanitiser.

The dialog API (RT6):

```rust
pub enum Answer<T> { Ok(T), Back, Shell, Quit }
pub trait Ui {
    fn menu(&mut self, d: &Menu) -> Answer<usize>;
    fn radio(&mut self, d: &Radio) -> Answer<usize>;
    fn input(&mut self, d: &Input) -> Answer<String>;
    fn password(&mut self, d: &Password) -> Answer<Secret>;
    fn yes_no(&mut self, d: &Question) -> Answer<bool>;
    fn message(&mut self, d: &Message) -> Answer<()>;
    fn view(&mut self, d: &TextView) -> Answer<()>;
    fn progress(&mut self, d: &Progress, tick: &mut dyn FnMut() -> Tick) -> Answer<()>;
}
```

`FullScreen` and `Plain` implement it for people, `Scripted` (a queue of
answers, a record of what was asked) for the app's tests.  The app never
touches the terminal.

## The terminal layer

### Raw mode

- Read the terminal's settings with `tcgetattr` into a buffer of our struct
  followed by 128 spare bytes, so that a real struct bigger than ours can't
  overrun it.  Keep that whole buffer as the original.
- Change only what raw mode needs, in place (read-modify-write, on every
  platform, not only Haiku): clear `ICANON ECHO ECHONL ISIG IEXTEN` in
  `c_lflag` and `BRKINT ICRNL INLCR IGNCR ISTRIP INPCK IXON` in `c_iflag`;
  set `VMIN` 1 and `VTIME` 0.  Leave `c_cflag` alone: the line's speed and
  framing are the launcher's.  Leave `OPOST` alone: every output is
  positioned explicitly, so it doesn't matter, and plain mode wants it.
- With `ISIG` off, Ctrl-C and Ctrl-Z arrive as bytes and are ignored.  No
  signal comes from the keyboard.
- Write the settings back with `tcsetattr(TCSADRAIN)`, then read them again
  and check the cleared bits read back cleared.  `--check-tty` does exactly
  this round trip and restores the original, for checks.sh.

### Size and resize

- `ioctl(TIOCGWINSZ)` at start and at every `poll` timeout (250 ms, est.).
  A changed size means a full redraw.
- 0x0 (a serial line, or Haiku's serial pty) is taken as 80x24, though the
  launcher sets it with `stty` too (RT13).  `--profile` says which it was.
- Bigger than 80x24: the 80x24 canvas is centred and the background fills
  the rest.  Smaller: see D10.

### Input and keys

- Read fd 0 directly (a `File` from the raw fd, never dropped), not
  `std::io::stdin()`, whose buffer would hide bytes from `poll`.  `poll` on
  fd 0 with the tick's timeout, or the Esc timeout when an ESC is pending.
- `keys.rs` is a pure state machine: bytes in, keys out, with no I/O, so it
  can be tested and fuzzed alone.
  - Arrows: `ESC [ A-D` and `ESC O A-D`; a modifier form (`ESC [ 1 ; 5 A`)
    is the plain arrow.
  - Enter is CR or LF; CR followed by LF is one Enter.  Backspace is 0x7F or
    0x08.  Tab 0x09, Space 0x20.
  - Esc: an ESC with nothing after it within the timeout (100 ms est.; RT3
    measures serial and IPMI-style lines, and serial may need longer).
  - Any other CSI or SS3 sequence is read to its final byte and dropped, at
    most 32 bytes; anything longer is dropped and the decoder resets.
  - Other control bytes are dropped.  Printable bytes, and whole UTF-8
    sequences, are characters.
- The small key set is for moving around: arrows, Tab, Enter, Esc, Space,
  Backspace and letters as commands.  Text and password boxes take every
  printable character, since a passphrase can hold anything and Review's
  confirmation is the end of a serial number, digits included.
- We never ask the terminal anything (no device attributes, no cursor
  report), so nothing ever arrives that isn't a key.

### Output and profiles

A profile is three things: the line style (UTF-8, DEC or ASCII), colour or
none, and the capabilities that console was seen to take (RT3).  `out.rs`
writes only:

| Sequence | Use | Sent |
|---|---|---|
| `ESC [ r ; c H` | move | always |
| `ESC [ 2 J` | clear | on a full redraw, where RT3 finds it works (*Check* syscons, tem); else spaces |
| `ESC [ … m` with 0, 1, 7, 30-37, 40-47 | attributes | always, joined with `;` where RT3 finds that works (*Check* syscons, tem); else one parameter each |
| `ESC [ ? 25 l` / `h` | hide and show the cursor | where RT3 finds it works; elsewhere the cursor is parked at the focused field |
| `ESC ( 0` / `ESC ( B` | DEC line drawing on and off | DEC profile only, around runs of line glyphs |

Nothing else: no `ESC [ K` (erasing with a background colour differs by
console), no scrolling regions, no `ESC 7`/`ESC 8`, no alternate screen, no
bright colours (90-97), no titles, no queries.  The last cell of the last
row is never written, so no console scrolls.

Line glyphs are stored in the buffer as glyphs and turned into bytes per
profile:

| Glyph | UTF-8 | DEC | ASCII |
|---|---|---|---|
| horizontal, vertical | U+2500, U+2502 | `q`, `x` | `-`, `\|` |
| corners | U+250C U+2510 U+2514 U+2518 | `l k m j` | `+` |
| tees | U+251C U+2524 U+252C U+2534 | `t u w v` | `+` |

The gauge's bar is reverse-video spaces in every profile, with the percentage
in figures beside it, so it needs no block glyphs and no colour.

Colours, where there are any: the background white on blue (37;44), the
dialog black on grey (30;47), its shadow black (40), focus reverse and bold.
In mono, focus is reverse and bold, and that's all.  The exact palette per
console is RT3's to confirm.

### Restoring the terminal

- A guard restores on every exit path: reset attributes, `ESC ( B` if DEC,
  show the cursor, clear, home, then the original termios (`TCSADRAIN`).
- A panic hook does the same before the message prints, so the message is
  readable.  The original is kept in a `OnceLock` the hook can reach.
- SIGTERM and SIGHUP get a handler that only sets an atomic flag; `poll`
  then returns `EINTR` (read through `std::io::Error::last_os_error`, so no
  errno FFI; *Check* on each family that `poll` isn't restarted) and the
  main loop restores and exits.  The only other handler is the demo's
  restore page's (RT8): while its child runs, SIGINT, and in plain mode
  SIGHUP and SIGTERM, set the same flag, so the child is stopped and the
  key file removed before the signal ends the program.
- Nothing can catch SIGKILL, so the launcher runs `stty sane` after
  bko-rescue exits for any reason, before starting it again (RT13).
- `read` returning 0 (the terminal hung up) ends the program cleanly.

### The FFI table

Declared in `term/ffi.rs`, one `cfg` block per family.  Expected values come
from the libc crate, Haiku's from beta6's `termios.h`.  **Every value in this
table is *Check* until RT1's header test passes in that family's guest.**

| Item | Linux (glibc; x86_64, aarch64) | FreeBSD, NetBSD, OpenBSD, DragonFly | illumos | Haiku |
|---|---|---|---|---|
| `tcgetattr`, `tcsetattr` | functions | functions | functions | functions |
| `struct termios`, expected size | 60 | 44 | 36 | 30 (beta6 header, RT1) |
| `TCSANOW`, `TCSADRAIN` | 0, 1 | 0, 1 | ioctl numbers (`TCSETS`, `TCSETSW`) | 1, 2 |
| flag bits, `VMIN`/`VTIME` indices | libc crate | libc crate, per OS (expected equal) | libc crate | beta6 header |
| `ioctl`'s request parameter | `unsigned long` | `unsigned long` | `int` | `unsigned long` *Check* |
| `TIOCGWINSZ` | 0x5413 | 0x40087468 | 0x5468 | 0x800c (`TCGETA + 12`) |
| `struct winsize` | four `unsigned short` | same | same | same |
| `poll`, `nfds_t` | `unsigned long` | `unsigned int` | `unsigned long` | `unsigned long` |
| `struct pollfd`, `POLLIN` | int, two shorts; 1 | same | same | same *Check* |
| `signal`, `SIGHUP`, `SIGINT`, `SIGTERM` | 1, 2, 15 | 1, 2, 15 | 1, 2, 15 | 1, 2, 15 *Check* |
| `raise`, `SIG_DFL` | function, 0 | function, 0 | function, 0 | function, 0 *Check* |
| `ttyname` | function | function | function | function |
| later (RT12): `setsid`, `kill` | functions | functions | functions | functions |

`ioctl` is declared variadic (`fn ioctl(fd: c_int, req: Req, ...) -> c_int`).
`isatty` comes from `std::io::IsTerminal`, errno from `std`.  Nothing else.

### The header test

- `bko-rescue --abi` prints our view, one fact a line:
  `size termios 60`, `offset termios.c_lflag 12`, `const TIOCGWINSZ
  0x5413`, `size nfds_t 8`, and so on, every item of the table, with
  `offset_of!` for the offsets.
- `rescue/abi/abi.c` (~80 lines) prints the same lines from the system's
  headers with `sizeof`, `offsetof` and the macros.
- **Symbol names too.**  Sizes and offsets can all agree while a header
  renames a function (NetBSD's `__RENAME`, for one).  The test lists the
  undefined symbols of the C program (`nm -u`) and of bko-rescue (its
  dynamic imports) for each function in the table, and compares them.  A
  rename is then one `#[link_name]`.
- **Where it runs:** in each build guest, in the same boot as the build,
  just after it: `cc -o abi abi.c && ./abi >c.txt && ./bko-rescue --abi
  >rs.txt && diff c.txt rs.txt`.  A difference fails the build.  Every build
  guest has `cc` (see "Test tools").  Linux runs it on the build host
  (Debian 13's glibc headers, *Check* `libc6-dev` on the hosts) and on
  Bigfoot for arm64.
- **Where the image differs from its build guest:** only FreeBSD, built on
  `freebsd14` for a 15.1 image.  The test runs once more in the `freebsd15`
  guest, whose base system has `cc`.  The other images' releases match their
  build guests (NetBSD 11.1, OpenBSD 8.0, DragonFly 6.4.2, OmniOS r151058,
  Haiku r1beta6).
- **On the images, without a compiler:** `bko-rescue --check-tty` (the raw
  mode round trip above, plus a check that every byte it didn't mean to
  change came back unchanged) runs in each image's checks.sh on the serial
  console.  It can't see a wrong offset that happens to round-trip, which is
  why the header test exists; it does catch a platform that ignores a
  setting.

### The C shim, and what triggers it

Not built unless one of these happens:

- the header test shows a layout or constant that depends on feature macros
  (`_POSIX_C_SOURCE`, `_XOPEN_SOURCE`, `__EXTENSIONS__`, `_BSD_SOURCE`) in a
  way one Rust declaration can't match for the library actually linked;
- a function in the table is a macro or inline in the header, so the C
  program calls something Rust can't name with `#[link_name]`;
- `--check-tty` or the spike shows a call misbehaving (a setting not taking,
  bytes changed) though the header test passes.

Then, on that platform only: `rescue/shim/tty.c`, about 50 lines, no strings
and no parsing: `bko_tty_raw(fd, save, len)`, `bko_tty_restore(fd, save,
len)`, `bko_tty_size(fd, &rows, &cols)`.  A `build.rs` compiles it with the
guest's `cc` through `std::process::Command` (no `cc` crate) when
`target_os` is that platform.  +0.5 d est.

## Console profiles

Decided in this order, first match wins:

1. **Plain mode:** `--plain`, or stdin or stdout not a terminal, or `TERM`
   unset or `dumb`.
2. **Overrides:** `--lines utf8|dec|ascii` and `--colour on|off`, or the same
   from `BKO_RESCUE_LINES` and `BKO_RESCUE_COLOUR`, which a launcher sets
   (D4).
3. **A pseudo-terminal on any platform but Haiku** is ssh (the images start
   no other).  The locale decides the lines: the first of `LC_ALL`,
   `LC_CTYPE`, `LANG` that is set, UTF-8 if it says `UTF-8` or `utf8`, else
   ASCII.  Colour unless `TERM` is `vt100`, `vt102` or `vt220`.  The tty's
   name decides, not `SSH_TTY`, which `sudo` drops (*Check*).
4. **By platform and tty name:**

| Platform | tty | Lines | Colour |
|---|---|---|---|
| linux | `tty1`-`tty63` | UTF-8 | yes |
| linux | `ttyS*`, `ttyAMA*`, `ttyUSB*`, `hvc*` | ASCII | D3 |
| freebsd | `ttyv*` | UTF-8 | yes |
| freebsd | `ttyu*` | ASCII | D3 |
| netbsd | `ttyE*` | DEC | yes |
| netbsd | `tty0*` | ASCII | D3 |
| openbsd | `ttyC*` | DEC | yes |
| openbsd | `tty0*` | ASCII | D3 |
| dragonfly | `ttyv*` | ASCII | yes |
| dragonfly | `ttyd*` | ASCII | D3 |
| illumos | `console` or `vt/N`, the kernel's console on the screen, framebuffer | UTF-8 | yes |
| illumos | the same, VGA text | ASCII | yes |
| illumos | `term/*`, or `console` when the kernel's console is `ttya` | ASCII | D3 |
| haiku | `TERM` starting `xterm` (the Terminal) | UTF-8 | yes |
| haiku | any other `TERM` (`on-serial.py` sets `vt100`) | ASCII | D3 |

5. **Anything else:** ASCII, no colour.

RT3 changed this table (OpenBSD's screen split by its display type, DragonFly's
and illumos's rows, `L` skipping what a console can't draw, Haiku's Terminal
row by locale): "What RT3 found" and `profile.rs` have the rows as built.

- **illumos's two tem modes** need a sign the spike has to find (*Check*):
  a UEFI boot is always a framebuffer; on a BIOS boot, candidates are the
  window's pixel size from `TIOCGWINSZ`, a size other than 80x25, or the
  loader's `framebuffer` setting in `prtconf -v`.  If nothing is reliable,
  illumos's screen is ASCII, with `--lines utf8` to force.
- **Over ssh on the non-Linux images** the client's locale never arrives:
  `stick/bsd/sshd_config.backotter` has no `AcceptEnv`.  Ubuntu's stock
  `sshd_config` has `AcceptEnv LANG LC_*` (*Check* on the slim image).  D13.
- **The first screen** shows `L: line style  C: colour` on its keys line.
  That line is ASCII text in every profile, so it stays readable when the
  boxes don't.  `L` cycles UTF-8, DEC, ASCII, skipping the styles the
  console doesn't draw (RT3: each row's `draws`); `C` toggles colour (D4).
- `bko-rescue --profile` prints what it decided and why (OS, tty, TERM,
  locale, size), for checks.sh and for bug reports.

## Screen buffer and redraw

- A cell is a glyph (a character, or a line glyph) and an attribute byte
  (foreground, background, bold, reverse).  The canvas is 80x24; the
  terminal's own size only places it.
- Two buffers: what's on the terminal and what's wanted.  A frame compares
  them row by row and, for each run of changed cells, writes one move, the
  attribute changes and the glyphs, with charset switches only around line
  glyphs (DEC).  The whole frame is built in a `Vec<u8>` and written with
  one `write_all`.
- **Byte budget** (tested, RT5): a gauge step is under 40 bytes; moving the
  selection in a list is under 200; a full colour frame is under 4 KB (UTF-8
  box glyphs are 3 bytes each).  At DragonFly's 9600 baud those are 40 ms,
  0.2 s and up to 4 s.
- **A full redraw** at the start, after a resize, at each new dialog, and
  every 60 s while a dialog waits (every 5 min on DragonFly's 9600-baud
  serial line).  Kernel messages print on the console on every platform and
  garble the screen; the periodic redraw heals it (see Risks).
- `screen.rs` dumps a canvas as text for golden screens: 24 lines of
  glyphs (line glyphs as UTF-8), then 24 lines of attribute letters.

## Widgets

Whiptail's set, with cursive's Dialog, SelectView and EditView as the model
(ui.md):

| Widget | What it does | Lines, est. |
|---|---|---|
| Dialog frame | centred box, title in the top border, shadow in colour, a body area, the buttons row; the backtitle (`BackOtter rescue`) on row 1 and the keys line on row 24 | 150-200 |
| Buttons row | `<  OK  >  < Back >`, focus by reverse and bold | 60-80 |
| Menu | tag and description per item, scrolls with `^` and `v` markers, arrows move, a letter jumps to the next item starting with it | 150-200 |
| Radio list | the menu's list with `(*)` and `( )`; Space selects | 80-120 |
| Text box | one line, scrolls sideways when longer than the field, a length limit; typing and Backspace only (D9) | 100-150 |
| Password box | the text box over a `Secret`, showing `*` per character (D8) | 60-80 |
| Yes/no, message | wrapped text, `<Yes> <No>` or `<OK>` | 80-100 |
| Gauge | the bar, the percentage in figures, a line of text, a timer | 60-80 |
| Text view | scrolling text (the command plan, the log's end); arrows by line, Space by page, Backspace back a page | 120-160 |
| Focus and runner | Tab cycles body and buttons; Enter presses; Esc is Back; the poll loop with ticks and resizes | 100-150 |

All of them draw into a canvas and take keys; none does I/O.  Each widget
takes only `Clean` strings (below), so an unsanitised string can't reach the
screen: the type system says so.

## Plain mode

- The same `Ui` calls as numbered questions and answers, one line at a
  time, in cooked mode:

  ```
  BackOtter rescue (plain mode)
  Step 2 of 9: Machine
    1) lovelace   newest backup 2026-11-04, the stick's copy is 3 days old
    2) babbage    newest backup 2026-10-30
    b) back   s) shell
  Choose [1-2, b, s]:
  ```

- No escape sequences at all.  Passwords are read with `ECHO` off (the same
  read-modify-write), byte by byte into the `Secret`, never through a
  `String`.
- Progress is one line per stage, and a percentage line at most every 10%
  or 60 s: nothing rewrites a line with CR, so a screen reader and a serial
  log both read it.
- `ISIG` stays on, so Ctrl-C ends bko-rescue; the launcher starts it again,
  and a restore keeps going (load isn't its child, RT12).
- The same sanitiser applies: plain mode prints the same untrusted strings.

## Security

- **Every outside string is sanitised before it can be shown.**  That's
  everything from bko-holt, rustic-bko, the config partition and the disks
  (models, serials, labels, hostnames, paths, log lines, error text).
  `text::clean` makes a `Clean`, the only string type widgets accept:
  - C0 controls and DEL become `?`; a tab becomes a space; newlines survive
    only where a widget wraps text (the text view splits on them first).
  - C1 controls (U+0080-U+009F) become `?`: a terminal not in UTF-8 mode
    reads 0x9B as CSI.
  - In the UTF-8 profile, non-ASCII is kept only from a table of width-1
    letters (Latin-1, Latin Extended-A and -B, Greek, Cyrillic), minus
    combining marks.  Everything else becomes `?`: bidi controls, zero-width
    characters, wide characters, emoji, private use.  In DEC and ASCII,
    every non-ASCII character becomes `?`.  The profile substitutes at
    render, so switching profiles needs no re-cleaning.
  - Each field has a cap before wrapping: hostname 64, model 48, serial 48,
    a problem's text 2 KiB, a log line 512 (est.).
  - Our own strings (later `app/strings.rs`) are checked by a test to pass
    through `clean` unchanged.
- **Hard limits in parsing:**
  - JSON is read only after capping its source: a plan 8 MiB, status
    64 KiB, one event line 64 KiB, a snapshot list 16 MiB, a `system.json`
    32 MiB (holt's corpus's largest dump is 3.3 MB, `omnios58-mirror`).
    `json.rs` adds its depth limit of 256.
  - The key decoder: 32 bytes a sequence.  Plain mode: 4 KiB a line.  Text
    box: 256 characters.  Password: 1 KiB.
  - A stderr kept for a message: its last 64 KiB.
- **Passwords:**
  - `Secret` is a buffer of fixed capacity (1 KiB) allocated once and never
    grown, so no copy is left in freed memory.  It is zeroed with volatile
    writes and a compiler fence on drop, and has no `Debug` or `Display`.
  - The screen shows `*`; no cell holds the secret.  The spike shows only a
    count.
  - Written only as ui.md says: a 0700 directory in RAM, a 0600 file made
    with `O_EXCL`, no trailing newline, the path passed as `--key-file
    NAME=PATH` or `--password-file`.  Never in argv, never in the
    environment, never in a log.
  - Removed when the program that reads it exits.  Load outlives the UI, so
    a restarted bko-rescue removes key files only once load's process is
    gone, not at its own start.
  - The RAM directory: `/run/bko-rescue` on Linux, `/var/run/bko-rescue` on
    the BSDs and illumos (OpenBSD's `/var` is an mfs, per its checks.sh;
    *Check* the others), and on Haiku ui.md's open question 4.
  - *Check* that no image writes core dumps and that none has swap.  If one
    does, `setrlimit(RLIMIT_CORE, 0)` joins the FFI table.

## The spike (RT2, RT3)

**What it is.**  `bko-rescue --spike` on RT2's first cut of the layer: real
raw mode and the real FFI, but output not yet diffed.  It draws four cards,
each in the profile the table above expects for that console and, by key, in
the other two line styles and in mono:

1. a dialog box with a title, a shadow and two buttons, one focused;
2. a list of five items in a box three high, one selected, with the scroll
   markers;
3. a gauge going from 0 to 100% over 10 s, updating twice a second, with the
   bytes each update took shown under it;
4. a password field (`*` per key, Backspace), then the count typed.

Under every card, the last 16 keys decoded, by name, and the size it sees.
`--spike panic` panics on purpose, to see the terminal come back.

**How it runs.**  On the current rescue images (one per platform) under
QEMU, with a monitor socket, from a new `testvm/rescue/spike.py`:

- the serial console: pick Shell at the placeholder menu, bring sshd up as
  img-boot.py does, copy the binary into `/tmp` (RAM), then run the spike on
  the serial line itself.  The bytes are logged and replayed through the
  pty harness's screen interpreter (RT8; for RT3 a first version of it).
- the screen console: `sendkey` picks Shell at the screen's own menu and
  types the command and the test keys; `screendump` saves a PNG of each
  card (qubes-drive.py's `shot`).  The PNGs are what is judged, by Claude or
  the user.
- BIOS and UEFI each, since they give different consoles (VGA text and a
  framebuffer).  OpenBSD's kernel console stays on the screen, or QEMU's VGA
  gets no wsdisplay (stick/openbsd/build.sh's note).
- ssh: from the host to the image's sshd, once with `LANG=C.UTF-8` and once
  with `LANG=C`, on Linux and one BSD.
- the header test in each build guest, in the same run.

**Order:** OpenBSD and DragonFly first (the consoles ui.md's research found
trouble on), then NetBSD, FreeBSD, illumos, Linux, Haiku.

**Pass criteria, every console:**

- box corners and lines continuous, in the expected style, nothing stray
  (no `(0` printed, no stray `[`);
- colour as intended: white on blue, black on grey, no blink, no underline;
  in mono, still readable;
- focus visible as reverse and bold, in colour and in mono;
- the list's selection moves with the arrows, and the markers show;
- the gauge fills, each update under 40 bytes;
- the password field shows one `*` per key, Backspace removes one, and the
  count is right;
- each of the arrows, Tab, Enter, Esc, Space, Backspace and a letter is
  decoded once and right; Esc alone is seen within the timeout;
- after quitting and after `--spike panic`, `stty -a` reads as before
  (scripted), echo works, and the cursor shows;
- the size: 80x24 on serial after `stty rows 24 columns 80`, and 80x24
  assumed from 0x0;
- `--check-tty` passes, and the header test passes in the build guest.

**And per platform:**

| Platform | Consoles | Expected | Also |
|---|---|---|---|
| openbsd | wscons on VGA text (BIOS) and on the framebuffer (UEFI); `tty00` | DEC, colour; serial ASCII | 30-37 and 40-47 show no blink, underline or stray reverse |
| dragonfly | syscons (BIOS; UEFI *Check*); `ttyd0` at 9600 | ASCII, colour; serial ASCII | a full frame's time at 9600 baud; typing at 960 bytes a second |
| netbsd | wscons on VGA text and genfb; `tty00` | DEC, colour | colour on VGA text |
| freebsd | vt(4) on VGA text and efifb; `ttyu0` | UTF-8, colour | box glyphs in VGA text mode |
| illumos | tem on VGA text and on the framebuffer; `ttya` | ASCII on text, UTF-8 on the framebuffer | no unknown escape printed; a sign telling the two modes apart |
| linux | VGA text (BIOS), fbcon (UEFI); `ttyS0`; ssh | UTF-8, colour; serial ASCII | ssh with both locales |
| haiku | the Terminal; the serial pty | UTF-8, colour; serial ASCII | a resize of the Terminal window redraws within 0.5 s; which arrow form the Terminal sends |

**What changes with the outcome.**  The findings go into this doc ("What
RT3 found") and into `profile.rs`'s table, before RT4:

- a console that misdraws its expected line style gets another default
  (say, OpenBSD's VGA text to ASCII): one table row;
- a sequence a console doesn't take (cursor hiding, joined SGR parameters,
  clear) is turned off for that console, with its fallback (park the cursor;
  one parameter each; clear with spaces);
- unreadable colours, or bold shown as blink, give that console a palette
  change or mono by default;
- the Esc timeout changes if keys split on serial or ssh;
- a header-test mismatch a declaration can fix is fixed in RT4; one of the
  shim's triggers adds the shim (+0.5 d) on that platform;
- no reliable sign for illumos's tem mode: ASCII there, UTF-8 by setting;
- full frames at 9600 baud too slow to use: plain mode by default on
  DragonFly's serial line, or a faster line (open question 2);
- a console that does better than planned (DragonFly's syscons showing CP437
  box glyphs, say) is noted, but changes nothing without the user (open
  question 1).

## Building and shipping

### Builds (RT3 for the spike, RT9 for good)

- `build-holt.sh` learns `HOLT_CRATE=rescue` (D5).  The source is a
  repo-shaped tree: `rescue/` and `holt/src/json.rs`, tarred from the root
  with those two paths.  The binary is `bko-rescue`, and the header test
  runs after the build in the same guest boot.  `--keep` lets one guest build
  both crates, as it already does for repeated builds.
- **Linux amd64:** on a farm host, rustup's 1.94.1 (`HOLT_RUST_TOOLCHAIN`),
  `+crt-static`, as bko-holt.  Also once per change with
  `CARGO=/usr/bin/cargo` (Debian 13's 1.85), to hold the floor (*Check*
  Debian's cargo is on the hosts).  ui.md says bko-rescue builds "with
  Debian 13's cargo"; in fact the shipped Linux bko-holt is built with
  rustup's 1.94.1, so 1.85 is a floor that has to be tested, not what ships.
- **linux-arm64:** natively on Bigfoot, as `build-holt.sh linux-arm64`
  builds bko-holt (docs/design/arm64.md, A2).
- **FreeBSD, NetBSD, OpenBSD, DragonFly, illumos, Haiku:** in `freebsd14`,
  `netbsd11`, `openbsd8`, `dragonfly6`, `omnios58`, `haiku6`, with their
  packaged Rust.  Each has `cc` for the header test.
- Jobs go through `pick-host` and `cmp-run.sh --reserve`, in RAM, as
  bko-holt's builds do.

### The set and the images

- Binaries go to a pinned set of their own,
  `~/backotter-work/rescue-bin/PLATFORM/bko-rescue` with `COMMIT` and
  `SHA256SUMS`, written only by a refresh script, as `img-refresh-holt.sh`
  writes holt-bin (D5).
- Images carry it as a tool from RT9: optional until RT13 (a missing binary
  is a warning, as `RUSTIC_REQUIRED` works for rustic-bko), required after.
  Its checks run in every boot-test from then on (D6).

### Files that change

RT9, the binary on the images:

| File | Change |
|---|---|
| `testvm/holttest1d/build-holt.sh` | `HOLT_CRATE=rescue`, the header test (begun in RT3) |
| new: `stick/img-refresh-rescue.sh` (or `img-refresh-holt.sh` taught both) | the rescue-bin set |
| `stick/make-img-bsd.sh` | `BKO_RESCUE_BIN`, copied into `in/` |
| `stick/build-backotter-layer.sh` | `/usr/local/bin/bko-rescue` from `BKO_RESCUE_BIN/linux/` (or `linux-arm64/`), with the same ELF-machine check |
| `stick/{freebsd,netbsd,openbsd,dragonfly}/build.sh` | install to `/usr/local/bin`; NetBSD's and OpenBSD's `in/` file lists; DragonFly's `ours_kb` |
| `stick/illumos/build.sh` | `usr/local/bin` (the keep list's `/usr/local/*` already covers it) |
| `stick/haiku/build.sh` | `system/non-packaged/bin`; `ours_kb` |
| `stick/img-one.sh` | `bins()` hashes it; `bko-rescue.commit` beside `bko-holt.commit` |
| `stick/*/checks.sh` (seven) | `bko-rescue --version`, `--check-tty`, `--profile` expecting serial and ASCII |
| `stick/*/manifest.sh` | `mf bin:bko-rescue` |
| `testvm/test2/gate/bincheck.sh` | bko-rescue in the static, ABI and version checks (*Check* with Test2's owners) |

RT13, bko-rescue as the console:

| File | Change |
|---|---|
| `stick/layer/usr/lib/backotter/console`, `stick/bsd/console`, `stick/haiku/console` | become launchers: on serial, `stty rows 24 columns 80` when the size is 0 0; run bko-rescue (not `exec`), then `stty sane`, in a loop; after three quick failures, today's menu, so a shell stays reachable; pick the largest font that leaves 80x24 where there is a tool for it (*Check* per platform) |
| `stick/illumos/on-tty` | the serial size (or left to the launcher) |
| `stick/haiku/on-serial.py` | the pty's window size, 80x24 (`TIOCSWINSZ` from Python's own modules) |
| `stick/boot-test.py`, `testvm/holttest1d/img-boot.py`, `testvm/test2/tests/test_holt_img.py` | the new first screen as a second `MENU` pattern beside the old one, so older images still work (D7).  The keys line is written as one run with one attribute, so its text is contiguous in the raw byte stream and a regex finds it |

The getty and SMF setups don't change: they still run `console`, which
keeps OpenBSD's `console-screen` and `console-serial` checks true.

## Testing

| Layer | Exists | New |
|---|---|---|
| Header test | none | `rescue/abi/abi.c` and `--abi`, in every build guest and `freebsd15` (RT1, RT3) |
| Unit tests | holt's `cargo test` pattern; `gate:holt` | `cargo test` of `keys`, `text`, `profile` (a table of OS, tty, TERM and locale cases), `screen` (diffs), each widget (RT4-RT7) |
| Golden screens | holt's golden plans (`BKO_HOLT_GOLDEN=check\|report\|bless`) | `tests/screens/*.txt`, every widget and dialog at 80x24, in UTF-8, DEC and ASCII, colour and mono; `BKO_RESCUE_GOLDEN` the same three ways (RT5, RT6) |
| Byte streams | none | `tests/bytes/`: a full frame and a gauge step per profile; a checker that every sequence sent is in that profile's set, never `38;5`, `48;5` or a query; the byte budgets (RT5) |
| Fuzz-style | none | inside `cargo test`, no cargo-fuzz (it needs crates and nightly): a small xorshift generator feeds random bytes, split at random points, with timeouts at random moments, to the key decoder (never panics, consumes everything, back to rest within 32 bytes), random strings to `clean` (no control byte out, width equals length, caps hold), truncated and mutated fixtures to the JSON caps, and lines to plain mode's reader.  100,000 cases a run; `BKO_RESCUE_FUZZ=N` for long runs (RT4, RT8) |
| App logic | none | the `Scripted` Ui: each screen's flow, Back from every step, every failure path (RT11, RT12) |
| pty runs | Python's stdlib `pty`, used by `on-serial.py` | `testvm/rescue/screen.py`: an interpreter of exactly the sequences bko-rescue sends, which fails on anything else and dumps the screen in the golden format; `drive.py`: bko-rescue on a pty of a set size, scripted keys, waits on screen text; `fake-bko-holt`: replays a scenario directory (argv to stdout, stderr and exit; `load --json` streams events with delays) and checks the key files it's given (0600, 0700 directory, no newline, removed after) (RT8) |
| Image checks | boot-test.py and checks.sh | the three checks above on every image (RT9) |
| Spike | qubes-drive.py's `shot`, console-type.py's `sendkey` | `testvm/rescue/spike.py`, kept for each new platform release (RT3) |
| Whole machines | HoltTest1, `HT_VARIANT=img` (img-boot.py) | `HT_VARIANT=rescue`: the image as the rescuer, and bko-rescue driven over the serial console instead of `bko-holt load` over ssh: plain mode, then full screen through `screen.py`; every platform (RT14) |

Where they run: `cargo test` and the pty runs on a Linux farm host, in RAM,
as `gate:holt` does; a `gate:rescue` layer for GateTest2 is open question 5.
The fixtures for RT8 are written by hand from ui.md's examples, and recorded
again from real runs once RT10 lands.

## Dry run

Added 2026-11-24 at the user's request, so the program can be tried on a
real machine without any risk to its disks.

`bko-rescue --dry-run` goes through the real flow on the real machine: its
real disks, the real repository and its snapshots, and the real `plan`
with its matching, refusals, size offers and problems.  It never writes:

- **`plan` and `load` run without `--commit`, and `--erase` is never
  passed.**  `load` without `--commit` runs only what reads and prints what
  each writing stage would run (`holt/README.md`, "What load does").  The
  Restore screen ticks through those commands, shown as "would run", instead
  of running them.
- **It says so on every screen:** the title line reads `DRY RUN: nothing will
  be written`, and Review's button reads < Show what would happen > rather
  than < Restore >.  The serials are still asked for, so the real
  confirmation can be practised.
- **Two locks, independent of each other**, so a bug in the UI can't wipe a
  disk:
  1. Every bko-holt command line is built in one place
     (`app/holt_args.rs`), and in dry-run mode that place can't add
     `--commit` or `--erase`.  A unit test runs every screen's command
     through it in dry-run mode and fails on either flag.
  2. bko-rescue sets `BKO_HOLT_NO_COMMIT=1` in bko-holt's environment.
     bko-holt itself then refuses `--commit` with an error (exit 2) before
     doing anything, so the lock holds even if the first one has a bug.
     This is a small change in holt/, part of RT10.
  (`holt-json.md` found that "`--erase` is never passed" doesn't keep the
  machine's own disk safe today, since a disk with the layout's
  identifiers needs no `--erase`; its J6 and J19 propose passing `--erase`
  in the dry run and gating `--commit` on it in bko-holt.  The user
  approved both 2026-11-24: lock 1 now reads "never adds `--commit`";
  a dry run may pass `--erase`.)
- **The archiver isn't run to restore,** since there's nothing to restore
  into.  `rustic-bko snapshots --json` and the repository checks are, since
  they only read.
- **Where it runs:** on the stick, from a "BackOtter rescue (dry run)" GRUB
  entry on Linux (RT13), or `--dry-run` from the first screen's shell
  elsewhere; or on an installed Linux system with `sudo bko-rescue
  --dry-run`, which needs root to read the disks as bko-holt does.
- **Not the same as `--demo`** (RT8): `--demo` replays recorded sessions
  through the fake bko-holt and needs no real disks or repository.  It's the
  way to see every failure and help screen; `--dry-run` is the way to see
  one's own machine.

## What RT1 and RT2 found

Built 2026-11-24 and tested on czerny (Debian 13, glibc, Rust 1.85.1).
The other six families are only compiled declarations until RT3.

- **Linux's header test passes:** 45 facts and 6 symbols agree with the
  glibc headers, for a debug and a stripped release build.  It catches a
  wrong constant (a diff), a wrong ioctl request type (abi.c then fails to
  build: it redeclares `ioctl` with ours) and a renamed symbol (`nm -P`).
- **Haiku R1/beta6's termios**, read from its `headers/posix/termios.h` at
  the `r1beta6` tag: `tcflag_t` is 16 bits, `speed_t` 32, and each speed
  is split into 16-bit halves between the flags: `c_iflag, c_ispeed,
  c_oflag, c_ospeed, c_cflag, c_ispeed_high, c_lflag, c_ospeed_high,
  c_line`, two padding bytes, `c_cc[11]`: 30 bytes.  The flag offsets are
  still 0, 4, 8 and 12.  TCSANOW is 1, TCSADRAIN 2, TIOCGWINSZ 0x800c,
  VMIN 4, VTIME 5; `nfds_t` is `unsigned long`; SIGHUP 1, SIGTERM 15.
- **Haiku's C `ioctl` is a macro** over `__ioctl(fd, cmd, arg, size)`
  (size 0 for three arguments).  That is one of the shim's triggers, but a
  declaration of `__ioctl` itself does it, so no shim: ffi.rs declares and
  calls `__ioctl`, and `--abi` names it, so the header test checks it.
- `poll` is never restarted by glibc's `signal()`: SIGTERM and SIGHUP end
  the spike at once with 143 and 129, and the settings come back.  The
  250 ms poll tick would notice the flag anyway, restarted or not.
- An undiffed full frame is about 4 KB in UTF-8 colour (3.6 KB in DEC or
  ASCII): the budget RT5's diffed redraw has to keep.  The gauge's own
  updates are 29-39 bytes in every profile (figures first, then only the
  bar's new cells, then the attributes back).
- czerny has no clippy for any of its toolchains (1.85.1, 1.91.0, 1.94.1,
  nightly), and no Debian cargo in `/usr/bin`; 1.85.1 is rustup's.  So
  RT1-RT2 had no clippy run; the lints are in Cargo.toml for when one is.
- The first profile table gives DragonFly's syscons and illumos's tem
  every fallback (one SGR parameter each, the cursor parked, clearing with
  spaces) until RT3 says what they take; illumos's tem is ASCII until a
  sign of the framebuffer is found.  An illumos `console` with TERM
  `sun-color` is the screen, any other TERM the serial console.

## What RT4 and RT5 found

Built 2026-11-24 on czerny (Rust 1.85.1), before RT3, so the profile
table is still provisional.  Tested by `cargo test` (96 tests, the
fuzz-style ones at 100,000 cases and once at 2,000,000 in release), the
pty test and the header test.

- **The table is data:** `profile::TABLE`, one row a console, each with
  its line style, colour, capability flags, Esc timeout and redraw
  period.  RT3's findings are one-row changes.  A test checks no row is
  hidden by one above it.
- **Esc timeouts (est., RT3 measures):** 100 ms on screens and over ssh,
  250 ms on serial lines and unknown consoles.  The full redraw while a
  dialog waits: 60 s, 300 s on DragonFly's 9600-baud line.  `--profile`
  prints both (`esc`, `refresh`).
- **A new file, `term/event.rs`:** the poll loop the plan gave the
  widgets' runner.  It gives keys (a lone ESC as Esc after the timeout,
  measured from the last byte), resizes (the size read every 250 ms even
  while keys keep coming), ticks the caller asks for, signals and
  hang-ups.  Each key carries the number of the read it came in, so a
  widget can tell keys that came together (Alt+key, a paste); the spike's
  password card needed that, and now runs on this loop.  `Term` bundles
  the signal handlers, raw mode and the loop.
- **The decoder** also takes SS3's old modifier form (`ESC O 5 A`) and
  keypad Enter (`ESC O M`), for a keypad left in application mode.
- **SGR is sent as changes:** adding bold or reverse, or changing a
  colour, needs no `0`; only taking one away does (22, 27, 39 and 49
  aren't in the set).  It saves 2 to 4 bytes a change.
- **Diffs compare looks, not cells:** a space in another foreground or
  in bold looks the same, so it isn't sent, and a space never switches
  attributes for that alone.  Spaces leave DEC's charset on.
- **Clearing:** a full redraw at exactly 80x24 writes every cell anyway,
  so `ESC [ 2 J` isn't sent then.  The first frame, a resize and
  foreign output (`Screen::written_over`) do clear first where the
  console clears: the last cell is never written, so only a clear
  empties it of a prompt or a kernel message.  Where the console
  doesn't clear, that one cell may keep it.  On a bigger terminal the
  margin is left to the clear only when it is plain (mono); in colour
  it is written as background spaces, since consoles clear in
  different colours.
- **DEC off at every full frame:** in the DEC profile a full frame
  starts with `ESC ( B` whatever bko-rescue thinks the charset is, so a
  terminal left with DEC on (a killed bko-rescue) shows letters again.
- **Signals after the full screen:** dropping `Term` gives SIGHUP and
  SIGTERM their default action again (`signal(sig, SIG_DFL)`), and
  raises one that came and wasn't taken, so they still end the program
  after the full-screen part.
- **Budgets, the sample screen (title, box with shadow, wrapped text, a
  framed list, a gauge, buttons, key line), all 12 profiles:** full
  frames 2.1-3.4 KB (UTF-8 colour 3,368 bytes, 3.5 s at 9600 baud); a
  gauge step 28-38 bytes; a move of the list's selection 151-160.  The
  first gauge step after a full frame pays for one whole attribute
  change, 27-47 bytes, so the 40-byte budget holds for the steps after
  it, the first is held to 50.  The gauge budget assumes no cursor to
  park: on a console that can't hide the cursor, a focus elsewhere costs
  each step a move back (about 8 bytes, 45 in all).  rescue-screens.md's
  Restore screen has no focus while the gauge runs, so RT6 keeps it so.
- **The byte checker** (`testkit::Checker`) also keeps a model of the
  screen.  Random walks of drawing, resizes and full redraws, in every
  profile, check each frame's bytes are allowed and that the screen then
  shows the canvas.
- **The golden dump** has a legend after the attribute rows: fixed
  letters for the palette (`b` background, `d` dialog, `D` its focus, `r`
  reverse, `s` shadow, `p`/`P` plain), digits for any other attribute.
- **`Clean`:** caps count characters (a problem's text is 2,048), and a
  string cut by a cap or to fit ends in `>`.  `Clean::ours` is for our own
  strings, checked unchanged in debug builds.  `wrap` keeps the spaces
  between words on a line (two after a full stop stay two), and cuts a
  word longer than the line every line's width, so a serial or a path is
  shown whole.
- Still no clippy on czerny, so none ran; the lints are in Cargo.toml.

## What RT6 and RT7 found

Built 2026-11-24 on czerny (Rust 1.85.1).  Tested by `cargo test` (164
tests: goldens of every widget in every line style, colour and mono, in
`tests/screens/w-*.txt`; each widget's bytes in all 12 profiles; key
handling per widget and through the runner on a fake terminal; random
keys into every widget and random bytes into plain mode; `Secret`'s
zeroing), and the pty test, which now runs `--demo` through every page
in full screen and in plain mode.

- **The `Answer` enum grew** two cases beside Ok, Back, Shell and Quit:
  `Button(n)` for a dialog's other buttons (< Type a location >, <
  Keyboard >, < Cancel >), each with its letter, and `Plain` for `P` on
  the first screen and `p` on the too-small notice.  Each dialog takes a
  `Vec<Button>`; a button's `Act` is Ok, Back, Shell or Other(n).
- **Letters are commands only,** as rescue-screens.md's Conventions say:
  the menu's "a letter jumps to the next item" (the Widgets table) isn't
  built.  The radio list's choice follows the focus; Space does nothing.
  A radio list can start with nothing chosen, and OK then says why on
  the notice row above the buttons, each time it is pressed.
- **Esc** is Back only where there is a Back button, and closes a text
  view.  Enter on a list, field or view presses the dialog's OK.  Left
  and Right move between buttons when one has the focus.
- **The key line** is made from the dialog when the caller gives none:
  the arrows, Tab, Enter's button, each button's letter, Esc, and the
  first screen's `L`, `C` and `P`.  Too long for the row, the arrows'
  words go, then Tab's.  "Enter:" names what Enter presses now: the
  focused button, or the dialog's OK from a list, field or view.  So
  "Stop the restore?" with the focus on < Keep going > says "Enter: Keep
  going", and it follows Tab, Left and Right.
- **The layout** keeps each list's and the view's least rows (3, or
  fewer when shorter) before text gets any.  Text past what is left is
  cut, its last line ending in `>`.  So a long problem text above a list
  never pushes the list over the buttons, the border or the key line.
- **Scrolling a list by a row** redraws every row: under 1200 bytes, not
  the 200 that "Screen buffer" plans for a move.  A move that doesn't
  scroll stays under 200, and the test checks both.  At 9600 baud
  (DragonFly's serial line) a scrolling step is about 1.25 s.  Kept as
  Whiptail does it, a row at a time; paging instead is the fix if RT3's
  serial runs find it too slow.
- **The insertion point** in a text or password box is one cell left
  out of the focus's reverse video, so it shows where the console hides
  the cursor too; where it can't, the cursor is parked there.
- **The gauge's bar is 40 cells,** not the mock's 62: with one SGR
  parameter each (the fallback profile) a 5 % step of a 62-cell bar took
  40 bytes, over the budget.  At 40 cells every step is under 40 bytes in
  all 12 profiles, the first one under 50.  The timer beside its line
  adds a few bytes once a second.
- **The runner** takes events through an `Io` trait (`Term`, or a fake
  in the tests).  It redraws in full at each new dialog and every
  `refresh_s` while one waits, and draws the too-small notice (D10)
  whenever the terminal is under 80x24; Enter there measures again.
- **`Secret`** is 1 KiB in one box, never grown; Backspace takes a whole
  UTF-8 character and zeroes it; drop zeroes it all with volatile writes
  and a fence (the only unsafe code outside ffi.rs).  A test checks it
  has no Debug.  `write_file` makes a 0600 file with O_EXCL, no newline,
  in a directory `ram_dir` made 0700 (an existing one with group or other
  bits, or a symlink, is refused).  A write that fails removes the
  file.  The scripted Ui's `Reply::Secret` prints as `Secret(<n
  bytes>)`, and its bytes are wiped once copied into the `Secret`.
- **Plain mode** reads a password with echo, ICANON and ISIG off
  (`tty::Quiet`), byte by byte into the `Secret`: Backspace, Ctrl-U and
  Ctrl-C (quit) are its own.  Elsewhere ISIG stays on.  While echo is
  off, SIGHUP and SIGTERM are caught: the read gives up, the settings go
  back, and then the signal ends the program (a shell sees 143).  The pty
  test sends SIGTERM at the password prompt.  An empty line
  where text is typed shows the other choices and `t) type it again`.
  Buttons get their letter, else b, s or o, else the first free letter
  of the label.
- **`--demo` is a first cut** (RT8's file, `src/demo.rs`): every widget
  in a row, Back a page back, `P` or the too-small notice's `p` going on
  in plain mode at the same page.  RT8 adds the fake bko-holt.
- Not built: a way for the runner to hand the terminal to a shell and
  take it back (`Answer::Shell` is the app's, RT11), and the dry-run
  banner (RT12).
- Still no clippy on czerny, so none ran.

## What RT8 found

Built 2026-11-24 on czerny (Rust 1.85.1, Python 3.13).  Tested by `cargo
test` (177 tests), `pty-test.py`, and `demo-test.py`, which drives
`--demo` through every widget with `drive.py` in full screen (UTF-8
colour, DEC mono, ASCII on `vt220`) and plain mode, against the fake
bko-holt, with 36 golden screens in `testvm/rescue/screens/`.  The long
fuzz runs: every fuzz-style test at `BKO_RESCUE_FUZZ=5000000` and at
`50000000` in release (5 min on czerny), with no failure.

- **`screen.py`** is `testkit::Checker` again in Python, for a real pty's
  bytes: a sequence or a UTF-8 character may be split across reads, so
  it waits for the rest.  `drive.py` runs `--profile` first, with the
  same options, environment and pty size, and holds the screen to that
  profile's line style, colour and caps.
- **Its dump** is `Canvas::dump`'s form, but a space that isn't reverse
  takes the plain letter for its background (`b`, `d`, `s`, `p`).  The
  diffed redraw doesn't resend a space whose foreground or bold alone
  differ, so those depend on what was drawn before; comparing them would
  make the goldens depend on the order of frames.  The terminal's last
  cell is never written, so it dumps as `p`.
- **The fake's events** are ui.md's form, written by hand and marked
  provisional, in four scenarios: a whole restore, a failed boot stage,
  one that stops for a minute (Cancel and signals), and a schema 2
  `start`.  They are to be recorded again from real runs after RT10.
- **The fake checks** each `--key-file`/`--password-file`: an absolute
  path to a regular file, 0600, ours, in our own 0700 directory, not
  empty, at most 1 KiB, no newline, its text in neither argv nor the
  environment; and it refuses `--commit` under `BKO_HOLT_NO_COMMIT=1`.
  A watcher it leaves behind logs whether each key file is removed after
  it exits.  Its log has a key's SHA-256, never its text.
- **`--demo` runs nothing without `--holt`:** its restore page then says
  so.  With `--holt`, it runs that program with `load demo-system.json
  --json --through close --key-file sda3_crypt=FILE`, never `--commit`,
  and with `BKO_HOLT_NO_COMMIT=1`.  A `start` of an unknown format or
  schema, a `start` with `commit: true`, or events that don't parse stop
  the fake at once.
- **The demo's key directory** is a new one under `$TMPDIR`
  (`bko-rescue-demo-PID-N`), made by bko-rescue itself with mode 0700,
  never one already there: `secret::ram_dir` accepts an existing 0700
  directory without checking its owner, which is safe in root's
  `/run/bko-rescue` but not in `/tmp`.  RT12 should check the owner too
  (one `geteuid` for the FFI table) or make the directory fresh.
- **The demo follows the events file** as RT12 will: stdout to a file,
  read each tick 64 KiB at a time and at most 4 MiB a tick, stderr to a
  second file for Raw output.  The fake is a child here, in a process
  group of its own, killed on Cancel, a signal or a panic; RT12's load is
  detached.  The key file goes as soon as the child is reaped.
- **Signals while the fake runs** (review): in plain mode nothing caught
  SIGHUP, SIGTERM or SIGINT, and ISIG is on, so any of them, Ctrl-C
  included, ended bko-rescue at once and left the key file, the events
  and the fake running.  The restore page now catches SIGINT, and in
  plain mode SIGHUP and SIGTERM too, while the fake runs.  Plain mode's
  reads wait in poll and give up when a caught signal is pending; the
  page drops the Run (the fake killed, the files and directory gone),
  then puts the default actions back and raises the signal again.  The
  full screen's `Term` still takes SIGHUP and SIGTERM itself, and now
  SIGINT from outside too while the fake runs.  `drive.py` and
  `pty-test.py` make the pty the program's controlling terminal, so
  Ctrl-C and a hang-up send real signals; `demo-test.py` checks SIGTERM,
  Ctrl-C and a hang-up in plain mode against the fake's log.
- **The fake's argv and environment check** looks for the key's text as
  a whole argument, a NAME=PATH's PATH or a whole value, and inside
  values only for keys of 8 bytes or more: "e" is in "--key-file" by
  chance.
- **Stop the restore?** takes Esc as Keep going (`Question::esc_no`,
  shown as "Esc: keep going"), and a run that ended while it was asked
  says how it ended rather than "Stopped".
- Still no clippy on czerny, so none ran.

## What RT3 found

Run 2026-11-24 from main at ea360c4, through pick-host and `cmp-run.sh
--reserve` on chopin, cinqcent and choubox (Bigfoot for linux-arm64): the
build and the header test in each platform's build guest, then spike.py on
each platform's `--test` rescue image (images of 2026-11-21, 5d639bf) in
BIOS and UEFI, on the serial line, the screen and ssh.  Every PNG was read.
The findings were folded in the same day: the table, PENDIN, OpenBSD's
display type, the styles `L` cycles through, and the spike's gaps.

**Builds and the header test.**  Every family's ffi.rs compiled the first
time, with no warning and no change, and every header test passed.  No
shim trigger came up anywhere.

| Platform | Built on | Rust | Header test |
|---|---|---|---|
| linux amd64 | chopin | rustup 1.94.1, +crt-static | 47 facts, 7 symbols; static, so the imports can't be checked, but a dynamic build of the same tree checked them |
| linux-arm64 | Bigfoot | rustup 1.94.1 | the same, static and dynamic |
| linux, the floor | choubox | Debian's cargo 1.85.1 | 47 and 7 |
| freebsd | freebsd14 (14.5-p2) | packaged 1.98.1 | 46 and 7, imports checked (dynamic, as on all but Linux); the binary runs on the 15.1 image |
| netbsd | netbsd11 (11.1) | pkgsrc 1.96.1 | 46 and 7 |
| openbsd | openbsd8 (8.0) | packaged 1.98.1 | 46 and 7 |
| dragonfly | dragonfly6 (6.4) | packaged 1.98.1 | 46 and 7 |
| illumos | omnios58 (r151058) | packaged 1.100.0 | 44 and 7, once given gcc: omnios58 has no `cc` |
| haiku | haiku6 (beta6) | rust_bin 1.94.1 | 49 and 7, `__ioctl` among them |

- Debian's cargo is on choubox only (the plan's *Check*), so the 1.85
  floor build runs there.
- The builds reproduce: chopin's and cinqcent's Linux binaries, and two
  illumos builds in fresh guests, were byte-identical.

**What held on every console.**

- **Keys:** every key was decoded once and right on every console, the
  SS3 Down too, with no key split at 250 ms (serial) or 100 ms (screens and
  ssh).  The timeouts stay.  Haiku's Terminal sends SS3 arrows in both
  cursor modes (and Home and End as `ESC O H`/`F`).
- **Putting the terminal back:** `stty -a` the same after the quit and the
  panic (once PENDIN is left out, below), exits 0 and 101, the cursor shown
  again, and echo working, everywhere.
- **Bytes:** first frames of 3,570 bytes (ASCII mono), 3,769 (ASCII colour)
  and 4,045 (UTF-8 colour); gauge updates at most 29 bytes in mono, 35 with
  joined SGR in colour, 39 with one parameter each, so under 40 always.
  Every byte stream was one the profile allows (screen.py's strict replay).
- **Size:** only Linux's agetty sets its serial line to 80x24.  FreeBSD's,
  NetBSD's, DragonFly's and illumos's serial lines start at 0x0, Haiku's
  serial pty and Terminal at 80x25.  0x0 taken as 80x24 worked on all of
  them; RT13's launchers still set 80x24 where it is 0x0.
- **Locale over ssh (D13):** a LANG sent by SendEnv never arrived on
  Linux, FreeBSD, NetBSD, OpenBSD, DragonFly or Haiku; it did on illumos,
  whose sshd_config is ours followed by OmniOS's stock one.  Linux's and
  FreeBSD's sessions have `C.UTF-8` of their own, so ssh is UTF-8 there
  whatever the client's locale; the other BSDs' and Haiku's have none, so
  ASCII.  With LANG set in the remote command, the pty rule chose right
  everywhere.  Contrary to the plan, the Linux image accepts no LANG either
  (no AcceptEnv on the slim image, or pam_env over it: not determined).
- **PENDIN (a bko-rescue bug, fixed):** on NetBSD, OpenBSD and DragonFly
  ptys, `--check-tty` failed with "c_lflag is 0x200005cf, not 0x5cf", and
  spike.py's `stty -a` comparison saw `-pendin` become `pendin`.  4.4BSD's
  tty code sets PENDIN itself whenever a tcsetattr turns ICANON back on;
  the next read clears it.  The restore was exact (plain `stty raw; stty
  -raw` does the same); serial lines and screens passed only because their
  shells had left PENDIN set already.  FreeBSD's newer tty layer didn't show
  it.
- **Mono focus:** reverse and bold is readable everywhere but low in
  contrast on several consoles, because the console brightens the
  foreground before it swaps: the Linux VT (#555 on #AAA, about 3.2:1),
  wscons on both BSDs, syscons; and NetBSD's genfb in colour too (light
  grey on dark grey, 2.4:1).  tem, vgacon and Haiku's Terminal show no bold
  at all, so reverse alone carries the focus there.  Plain reverse was
  crisp on every one.  Left as it is: see open question 6.

**Per platform.**

- **OpenBSD.**  Serial (`tty00`, 115200, BIOS and UEFI): PASS, 36 of 36.
  The screen on VGA text (BIOS, `vga1`, 80x25): PASS, DEC lines
  continuous, colour right, SGR 30-37 on 40-47 with no blink, underline or
  stray reverse (OpenBSD's "Also").  **The screen on efifb (UEFI, 128x64):
  FAIL: every DEC line is a `?`.**  rasops's font has no glyphs for the DEC
  set (VGA text's CP437 font has them); UTF-8 is `â` garbage, ASCII clean.
  Real machines on UEFI, or on inteldrm, radeondrm or amdgpu, are rasops
  too, so ASCII will be the common case on real hardware.  The two are told
  apart by `WSDISPLAYIO_GTYPE` on the tty (`wsconsctl display.type`:
  vga-pci against efifb).  ssh (`ttyp0`): PASS once PENDIN was masked.
- **DragonFly.**  Serial (`ttyd0`, 9600): every check passed, but QEMU
  doesn't pace its UART, so the plan's "a full frame's time at 9600 baud"
  could only be computed: 3,570 bytes is 3.7 s a full frame on a real line,
  and card 1 with six keys about 12 s.  Usable but slow; open question 2
  stays.  Typing at 960 bytes a second can't show sio dropping input.
  syscons (BIOS VGA text, and UEFI's framebuffer, which works with
  `--no-secure-boot`: the plan's *Check*): PASS in ASCII.  It takes joined
  SGR and `ESC [ 2 J`, but **prints `?25l` and `?25h` as text** (`25l`);
  cons25's own civis (`ESC [ = 1 S`) doesn't hide the cursor in QEMU
  either.  So the cursor is parked.  UTF-8 is mojibake and DEC prints
  letters; the VGA font's `|` is a broken bar.  ssh: FAIL from PENDIN
  alone.  Root's login shell is csh, which spike.py's commands didn't parse
  (fixed).
- **NetBSD.**  As the image starts them, the serial line and the screen's
  `ttyE0` have **no TERM**: getty with `lo=` (gettytab `bko.Pc`,
  `bko.serial`) gives its program an empty environment, so bko-rescue there
  would be in plain mode.  An image defect for RT13 (`:tt=wsvt25:` and
  `:tt=vt100:` in gettytab, or the launcher sets TERM).  With TERM from
  `/etc/ttys`: serial PASS; the screen on VGA text PASS (DEC, colour); on
  genfb (UEFI, 128x48) PARTIAL: `?25l` **leaves a frozen image of the
  cursor** where it was when hidden, redrawn whenever that cell is written
  (inside a dialog, on a real stick).  Parking works on both, so the row
  parks.  The genfb colour focus is the low-contrast one above.  ssh:
  PENDIN.  The `--test` image shows screen 1 (rc.d/wscons's root shell) at
  boot, not `ttyE0`'s menu, which rc.local adds without switching to it.
- **FreeBSD.**  Serial (`ttyu0`) PASS; the screen on efifb (UEFI, 160x50)
  PASS; ssh PASS.  The BIOS screen is vt(4) in true VGA text (80x25):
  PARTIAL, as UTF-8's horizontal lines show a 1-pixel gap at each cell's
  9th column, so they look finely dashed (corners and verticals are right).
  DEC draws the same glyphs with the same gaps and ASCII is worse, so the
  row stays UTF-8.  FreeBSD's stock spike.py run passed every check first
  time.
- **illumos.**  Serial (`term/a`) PASS; ssh PASS (the locale arrives).
  QEMU's BIOS boot is never VGA text by default: the loader sets a VBE
  framebuffer, so BIOS and UEFI both give tem on a 1280x800 framebuffer
  (127x43).  VGA text was reached by typing `framebuffer off; set
  os_console=text` at the loader's prompt.  **tem takes everything** (joined
  SGR, `?25l`, `ESC [ 2 J`) **and draws UTF-8 boxes in both modes**, CP437
  on VGA text, so the plan's sign telling the two modes apart isn't needed
  (one exists: `TIOCGWINSZ`'s pixel size is 0 on VGA text).  The provisional
  row (ASCII, every fallback) passed but parked a visible block cursor
  inside the focused button; a private build with UTF-8 and every cap passed
  every check in both modes.  DEC isn't supported: `(0` and `(B` print.
  Bold is invisible on the framebuffer; tem's 37 is light grey.  omnios58
  has no `cc` (check.sh now finds `/opt/gcc-14/bin/gcc`).
- **Linux.**  Serial (`ttyS0`) PASS; ssh PARTIAL only for D13 above.  The
  `--test` image's BIOS boot is bochs-drm fbcon (160x50), not the plan's VGA
  text; vgacon (80x25) was reached with `nomodeset` by a GRUB edit.  fbcon
  (BIOS and UEFI) and vgacon draw UTF-8 boxes continuously (CP437 on
  vgacon), colour, joined SGR, cursor hiding and `ESC [ 2 J`, and /dev/vcsu1
  read the key log back right at every step.  PARTIAL for two things:
  **DEC prints letters** (`lqqqk`; the Linux console ignores `ESC ( 0` in
  UTF-8 mode), so `L` gave broken boxes; and the mono focus above.  No
  spike on arm64: `ttyAMA0` stays *Check*.
- **Haiku.**  Serial (on-serial.py's pty) PASS; the Terminal (BIOS and
  UEFI) PARTIAL: UTF-8 and DEC draw the same glyphs, but vertical lines
  have 1-2 px gaps between rows (the font's line height), and bold doesn't
  show.  The "Also": a resize redraws promptly once the pty's size changes,
  but the Terminal itself takes 0.06-0.49 s to change it, so end to end it
  was 0.5-0.8 s, within 0.5 s in 3 of 9 tries.  Shrinking below 80x24 shows
  the too-small notice.  ssh (not in the plan's row): **FAIL: with LANG=C,
  UTF-8 lines**, because rule 3 skips Haiku's ptys and the Terminal's row
  matched any xterm TERM.  In one boot of five a notification (the
  bootstrap's `ssh-keygen -A`) took the focus from the Terminal, and the
  test's keys went to Tracker.

**What changed.**

- **The table** (profile.rs; `--profile` says each row's words):

| Console | Before | Now |
|---|---|---|
| Linux VT | UTF-8, every cap | the same; `L` skips DEC |
| NetBSD `ttyE*` | DEC, every cap | DEC, the cursor parked; `L` skips UTF-8 |
| OpenBSD `ttyC*` | DEC, every cap | VGA text (`WSDISPLAYIO_GTYPE` says vga-pci or ISA VGA): DEC, `L` skipping UTF-8; anything else, or the type unreadable: ASCII, `L` ASCII only |
| DragonFly `ttyv*` | ASCII, every fallback | ASCII, joined SGR, `ESC [ 2 J`, the cursor parked; `L` ASCII only |
| illumos's tem | ASCII, every fallback, with a VGA-text row planned | UTF-8, every cap, one row for both modes; `L` skips DEC |
| Haiku's Terminal row | UTF-8 | UTF-8 when the locale says so, else ASCII (the Terminal sets `LC_CTYPE=en.UTF-8`; ssh with no locale gets ASCII, as on the others) |
| FreeBSD, the serial lines, ssh | | unchanged |

  No row gets every fallback now; the fallbacks stay, tested, for the
  overrides and the spike.
- **`L` cycles only the styles the console draws** (each row's `draws`,
  printed by `--profile` as `draws utf8 dec ascii`).  Serial lines, ssh
  and unknown consoles keep all three, since the far end is unknown.
  `--lines` and `BKO_RESCUE_LINES` still force any style: a real console
  may do better than QEMU's.  The spike's own `l` still tries all three.
- **PENDIN:** ffi.rs names it (0x20000000) for the four BSDs as
  `KERNEL_LFLAG`, and `--check-tty`'s comparisons leave it out; abi.c
  checks the value.  Nothing else reads it.
- **OpenBSD's display type:** `WSDISPLAYIO_GTYPE` (`_IOR('W', 64, u_int)`)
  and `WSDISPLAY_TYPE_ISAVGA` and `_PCIVGA` in ffi.rs, checked by abi.c
  from `<dev/wscons/wsconsio.h>`; `Facts::vga_text`; rows can ask for VGA
  text (`ModeIs`).
- **check.sh** finds a compiler when `cc` is missing (gcc, clang,
  `/opt/gcc-*/bin/gcc`), and says so when there is none, rather than
  blaming the ioctl type.
- **spike.py:** every command over ssh runs under `sh -c` (csh); PENDIN is
  left out of the `stty -a` comparisons; a console with no TERM gets the
  type from `/etc/ttys`, said as SKIPPED with the reason; a spike that gave
  up before its first frame (plain mode) says so; on NetBSD Ctrl-Alt-F1
  before the screen's menu, on Haiku the Terminal made active first; on the
  screen, the other line styles, mono, the list in mono and each cap toggled
  (`x-*` PNGs), the panic's own frame, and SGR 30-37 on 40-47 at the shell
  (`x-colours`); DragonFly's first frame also in real-line seconds;
  `--loader` (illumos's VGA text) and `--nomodeset` (Linux's vgacon) for
  BIOS boots; the guest's files fetched without tar's `--exclude`, which
  illumos's tar lacks.

**Checked after the fold** (the same day, on czerny and the farm):

- czerny: `cargo test` 185 tests, the pty test 117 checks, `demo-test.py`
  and the header test (47 facts and 7 symbols) all pass, with no warning.
  No golden changed: czerny's pty is the ssh rule, which still cycles all
  three styles.
- Rebuilt through build-holt.sh with the header test, in each build guest:
  FreeBSD, NetBSD and DragonFly 47 facts (PENDIN added), OpenBSD 50
  (PENDIN, `WSDISPLAYIO_GTYPE` 0x40045740, ISAVGA 7 and PCIVGA 8, as
  ffi.rs has them), illumos 44 with no `HOLT_ABI_CC` (check.sh found gcc
  itself), Haiku 49.  All 7 symbols agree everywhere.
- spike.py, all consoles, BIOS and UEFI, on OpenBSD, NetBSD and DragonFly
  with the new binaries: no FAIL in any (346 checks ok on each).
  `--check-tty` and the `stty -a` comparisons pass on their ptys now.
  OpenBSD's screen says `OpenBSD's wscons on VGA text`, DEC, on BIOS and
  `on a framebuffer`, ASCII with clean `+-|` boxes, on UEFI: the display
  type is read right on both.  NetBSD's genfb, parked, shows no frozen
  cursor; DragonFly's syscons, parked with joined SGR and `2J`, prints
  nothing stray.  NetBSD's missing TERM is reported as SKIPPED with its
  reason, and the screen run is on `ttyE0` now.  DragonFly's runs needed no
  wrapper for csh.  Not re-run: FreeBSD, illumos, Linux and Haiku, whose
  spike behaviour the fold doesn't change beyond the rows checked by the
  unit tests (illumos's UTF-8 row was run in RT3 itself, as a private
  build; Haiku's locale rule only on ssh).

**What stays open.**

- **Open question 1, CP437:** only DragonFly's syscons would gain (illumos's
  tem and Linux's vgacon already map UTF-8 box glyphs to CP437 on VGA text,
  and FreeBSD's VGA text shows the same 9th-column gaps whatever is sent).
  Not tried.  Your call.
- **Open question 2, DragonFly's 9600 baud:** QEMU can't show it; computed,
  3.7 s a full frame and about 1.25 s a list scroll.  Raise the line to
  115200 (why the image chose 9600 is still *Check*), or plain mode by
  default there?  Your call.
- **D13:** the locale arrives over ssh on illumos only.  `AcceptEnv LANG
  LC_*` for the BSD and Haiku images, as decided; Linux needs it too, and
  FreeBSD's login class may still set `C.UTF-8` over it (not tried).
- **Open question 6, the palette:** mono focus as reverse without bold
  would be crisp on every console seen (and on NetBSD's genfb in colour
  too).  Changing it moves every mono golden.  Your call.
- **NetBSD's image:** no TERM on the screen and the serial line (RT13's
  launcher or gettytab), and the `--test` image showing screen 1.
- **Haiku:** the resize criterion (0.5 s) is the Terminal's own delay, not
  bko-rescue's: accept 0.8 s, or keep it as a known limit.  A desktop
  notification taking the focus at boot would hit a real user too.
- **Not seen yet:** the OpenBSD rows on real hardware (rasops on inteldrm
  and the like is assumed to be efifb's), `ttyAMA0` on arm64, any real
  9600-baud line, and real VGA, which fills the 9th column that QEMU's
  screendump leaves blank.
- Small: the spike parks its cursor in a button's third cell and on the
  list's first row, not the selection (spike only; RT6's widgets park at
  the insertion point); escaping to illumos's loader prompt from COM1 once
  left the kernel console on ttya (seen once, not followed up).

## The app on top (later)

Scoped here so the order is clear; each gets its design when it starts.

- **RT10, the protocol, in holt/** (ui.md, "What bko-holt has to change"):
  `plan --json`, `status --json`, the events with `--json`, `exec::run`
  writing to a sink, stable problem codes, `candidates` with their refusals,
  no prompts under `--json` (`key_needed`, `password_needed`), and
  rustic-bko's `--json-progress` read and sent on as `progress` events.  Its
  own spec first (D12): `holt-json.md`, steps RT10a-RT10p, the core
  RT10a-RT10j before RT12.  It can start any time and needs nothing from
  RT1-RT9.  It must land before RT12.
- **RT11, the first screens:** the `Ui` trait's users, `app/strings.rs` (all
  strings in one place, for translation later).  Keyboard (each system's own
  tool; *Check* `loadkeys`, `kbdcontrol`, `wsconsctl` and `kbd` on the
  images), Machine (the config partition, D11), the Network notice (wired
  only, ui.md's open question 3), Repository (the password, `rustic-bko
  snapshots --json`, the repository ID check).  The shell key on the first
  screen.  Fixtures and scripted tests; no bko-holt protocol needed yet.
- **RT12, the restore screens, on RT10:** Disks (`plan --json` without
  `--map`), Passphrases, Review (the typed serial confirmation, `c` for the
  command plan), `--dry-run` and its banner (see "Dry run"), Restore (load started detached: `systemd-run` on Linux, a
  new session through `setsid` elsewhere; stdout to a file in RAM, followed
  each tick; `l` for the log), Done or not.  Resuming with `status --json`
  after a crash or a reboot.  Restore logs on the stick, compressed by the
  system's `gzip` (*Check* on every image).
- **RT13, the launchers and test hooks** (files above), and the dry-run
  boot entry (see "Dry run").
- **RT14, whole machines:** HoltTest1 `HT_VARIANT=rescue`, every platform.
  The source's `system.json` goes onto the image's config partition in its
  throwaway overlay (a small tool, *Check* whether add-ssh-key.sh's way of
  writing to the partition fits).

## Steps

| | What it builds | Files | Tested by | est. | Needs |
|---|---|---|---|---|---|
| RT1 | The crate; `term/ffi.rs` for the four families; `--abi`; `abi/abi.c`; the comparison script | `rescue/Cargo.toml`, `Cargo.lock`, `src/{main,lib}.rs`, `src/term/ffi.rs`, `abi/` | the header test on a Linux host and on Bigfoot; a build with Debian's cargo | 0.5 d (~300 Rust, ~80 C) | none |
| RT2 | The first cut of the layer: raw mode, size, read with timeout, undiffed output in three line styles with and without colour; the spike's cards; `--check-tty`, `--profile` (first table) | `src/term/{tty,keys,out,profile}.rs`, `src/spike.rs` | unit tests of the decoder and the round trip on a host pty | 0.5 d (~400, most kept) | RT1 |
| RT3 | The spike runs and the header test everywhere; `build-holt.sh`'s `HOLT_CRATE=rescue` (its first part); "What RT3 found" in this doc | `testvm/holttest1d/build-holt.sh`, new `testvm/rescue/spike.py`, a first `screen.py` | the pass criteria above, per platform; PNGs and serial logs kept | 1.5 d | RT2; a free slot per platform's build guest (pick-host); Bigfoot for arm64 |
| RT4 | The layer proper: the profile table from RT3, the capability flags, the complete decoder with the Esc timeout, resize by poll timeout, restore on exit, panic and SIGTERM/SIGHUP; `text.rs` | `src/term/*`, `src/text.rs` | unit tests; fuzz-style decoder and sanitiser tests | 1 d (440-670 with RT1-RT2's part, + 100-150) | RT3 (the decoder and `text.rs` can start beside RT3) |
| RT5 | Screen buffer, diffed redraw, golden-screen and byte-stream harness | `src/screen.rs`, `tests/screens/`, `tests/bytes/` | goldens; the sequence checker; byte budgets | 0.5 d (200-300) | RT2 (beside RT3) |
| RT6 | The widgets, the `Ui` trait, the full-screen runner, `Secret` | `src/widgets/`, `src/ui.rs`, `src/secret.rs` | goldens of every widget in every profile; key handling per widget; `Secret` zeroing | 2 d (1,000-1,300) | RT4, RT5 |
| RT7 | Plain mode | `src/plain.rs` | unit tests on a fake terminal; a pty run | 0.5 d (250-350) | RT6 |
| RT8 | pty harness, fake bko-holt, `--demo` (every widget in a row, for the harness and later spikes; its last page hands a typed passphrase to the fake as a key file and follows the fake's events with the gauge), the long fuzz runs | `testvm/rescue/{screen,drive}.py`, `testvm/rescue/fake-bko-holt`, `src/demo.rs` | `--demo` driven through every widget in both modes; key files checked by the fake | 1 d (~450 Python) | RT6, RT7 |
| RT9 | All seven and linux-arm64 built and pinned; on the images as a tool; checks and manifests; one image round's boot-tests | the RT9 table | boot-test on every image (BIOS and UEFI); `gate:bin` | 1 d, plus the image round's own time | RT4 (RT6-RT8 for a useful `--demo`) |
| RT10 | bko-holt's `--json` protocol, `BKO_HOLT_NO_COMMIT` (D14), the erase gate: spec `holt-json.md` (D12), steps RT10a-RT10p there | `holt/src/load/*`, `holt/src/{main,bugreport,snapcore,recovery}.rs` (json.rs's writer already does what it needs) | holt's unit tests on the fake rescue systems; golden `plan.json`; events in order with `end` last; HoltTest1 `HT_VARIANT=json`; HoltTest2 unchanged | ~8 d core (RT10a-RT10j), ~16 d all (holt-json.md; 3-4 d counted here first) | `holt-json.md` approved; nothing from RT1-RT9 |
| RT11 | The app's first screens: Keyboard, Machine, Network notice, Repository; strings; the config reader (D11) | `src/app/` (and holt/ if D11) | scripted-Ui tests; pty scenarios with fixtures | 2.5 d | RT6, RT7, D11 |
| RT12 | Disks, Passphrases, Review, Restore, Done; detached load and following; resume; restore logs; `--dry-run` and `holt_args.rs`'s test (D14) | `src/app/` | scripted tests; pty scenarios on fixtures recorded from RT10, failures included | 3.5 d | RT10, RT11 |
| RT13 | Launchers on every image; test hooks; fonts; the dry-run GRUB entry (D14) | the RT13 table | boot-test reaching the shell through bko-rescue on every image; ImgTest2's `+img` cells unchanged | 1 d | RT12 (RT11 for a preview) |
| RT14 | HoltTest1 `HT_VARIANT=rescue` | `testvm/holttest1d/ht1.sh`, `ht1-bsd.sh`, a config-partition writer | one guest per platform, plain and full screen | 2.5 d | RT13 |
| | *≈ 21-22 d in all; RT1-RT9 ≈ 8.5 d* | | | | |

Source changes go through High-effort subagents, as the rest of bko's code
does.  Each step is committed on main when its tests pass; nothing is
pushed until the user says so.  `build-holt.sh`, boot-test.py and the
`stick/*/build.sh` scripts are edited by other sessions too: each brief says
which functions it may change, and the diffs stay small.

## Decisions

All approved by the user on 2026-11-19 as recommended.

- **D1. Where the crate lives.** `rescue/` at the top, beside `holt/`
  (*recommended*: a sibling, so `json.rs`'s relative path is short and
  neither crate's build scripts see the other's tree).  Or `holt/rescue/`,
  or `stick/rescue/`.
- **D2. What it takes from holt/.** `holt/src/json.rs` as source by
  `#[path]`, nothing linked (*recommended*: one file, no 57,000-line
  compile in each guest, and only a change to json.rs means a rescue
  rebuild).  Or
  a path dependency on `bko_holt`, which gets the SHA-256 and FAT code too
  at those costs.  Or a copy of json.rs, with a test that it matches.
- **D3. Colour on serial consoles.** Off by default, `--colour on` (or the
  first screen's `C`) to turn it on (*recommended*: `vt100` and `vt220`
  define no colour, the far end is unknown, and a monochrome serial log
  stays readable).  Or on by default, since most IPMI viewers show colour.
- **D4. Where the overrides come from.** Flags and the environment, which
  the launchers set, plus `L` and `C` on the first screen (*recommended*).
  A `settings.toml` key later, once something reads TOML (open question 4).
  Or flags only.
- **D5. Builds and the pinned set.** `build-holt.sh` builds it
  (`HOLT_CRATE=rescue`) into a set of its own, `rescue-bin`, with its own
  refresh (*recommended*: a bko-rescue change then never moves HoltTest2's
  holt `CODE`, which hashes `holt/**`, and the images' bko-holt pin is
  untouched).  Or into `holt-bin` beside bko-holt, one pin for both.
- **D6. When it goes on the images.** As a tool from RT9, optional, its
  self-checks in every boot-test; the console only at RT13 (*recommended*).
  Or nothing on the images until the app is done.
- **D7. How the tests reach a shell once bko-rescue is the console.** Its
  first screen's `s` key, and plain mode's first prompt, become a second
  `MENU` pattern in boot-test.py and img-boot.py, beside the old one
  (*recommended*).  Or keep today's menu as the launcher's first screen,
  with "Restore" as a new item, which keeps the tests as they are but puts
  a menu before the program.
- **D8. The password box.** `*` per character (*recommended*: Whiptail's
  way, and the user sees each key land).  Or nothing shown.
- **D9. Editing in the text box.** Typing and Backspace only in version 1
  (*recommended*: the passphrase box works the same, and it's ~50 lines
  less).  Or Left and Right to move within the field.
- **D10. A terminal smaller than 80x24** (an ssh window): a notice asking
  for 80x24, and `p` for plain mode (*recommended*).  Or draw what fits and
  scroll the canvas.
- **D11. Who reads the config partition** ("Finding the config at boot").
  bko-holt, as a new `bko-holt stick --json` (*recommended*: it already has
  every platform's disk listing, the FAT probe illumos's boot disk needs,
  SHA-256 for `system.json.sha256`, and fake systems to test it on; bko-rescue
  stays a front end).  Or bko-rescue itself, which then needs D2's path
  dependency or copies of that code.  Needed by RT11, adds ~1 d to RT10.
- **D12. The protocol's spec.** A short spec of its own,
  `docs/design/holt-json.md`, from ui.md's section, before RT10
  (*recommended*: problem codes and the `candidates` refusals need settling
  against target.rs, plan.rs and commands.rs).  Or build straight from
  ui.md.
- **D13. The locale over ssh on the non-Linux images.** Add `AcceptEnv LANG
  LC_*` to `stick/bsd/sshd_config.backotter` (and Haiku's sshd setup,
  *Check*) (*recommended*: one line, and without it every ssh session there
  is ASCII).  Or leave it, and let `--lines utf8` force it.
- **D14. A dry run** (the user's request, 2026-11-24): `--dry-run`, with
  the banner, the two locks and the boot entry in "Dry run".  Builds in
  RT10 (holt's `BKO_HOLT_NO_COMMIT`), RT12 (the flag, the banner and
  `holt_args.rs`'s test) and RT13 (the GRUB entry).

## Risks

- **Kernel messages on the console** (a USB disk going away, ZFS, every BSD
  prints to the console) overwrite the screen.  The periodic full redraw
  heals it; the launcher lowers the console's log level where it can
  (Linux: `dmesg -n 1`; *Check* the others).
- **A console stranger than its source suggests.**  The spike covers the
  QEMU consoles; real VGA, real framebuffers and IPMI viewers can still
  differ.  The `L` and `C` keys and the overrides are the way out.  A look
  at the Thinkpad's own screen during a BackShot run would check one real
  machine.
- **Esc over slow or bursty lines.**  A split `ESC [ A` reads as Esc
  (Back), then `[` and `A`.  The timeout is measured in RT3; serial may get
  a longer one.  Back is never destructive.
- **The header test can't see behaviour.**  `--check-tty` and the spike's
  `stty -a` comparison can.
- **9600 baud on DragonFly.**  Byte budgets are tested, but a full frame
  still takes 3-4 s there.  Plain mode is the fallback (open question 2).
- **Non-ASCII passphrases.**  A console's keymap and charset may send other
  bytes than the source system's desktop did, so a restored LUKS or
  repository passphrase might not match.  The password box takes bytes as
  they come, and says so when it sees a non-ASCII one.
- **Widgets cost more than counted,** the text view's wrapping and the
  focus rules especially.  The widget estimate is ui.md's own; if those two
  grow, RT6 grows (up to +1 d est.).  The design is cursive's, a known one.
- **Bigfoot** builds and tests arm64 (and had SIGBUS trouble on its USB
  disks before its reboot).  arm64 waits on it, not the rest.
- **Shared files:** `build-holt.sh`, boot-test.py, img-boot.py and the
  image scripts belong to running work of other sessions; changes are small
  and named in each brief.

## Open questions

1. **CP437 box glyphs** for DragonFly's syscons and illumos's VGA text
   (bytes 0xB3-0xDA, outside C1) would give real boxes where ASCII is
   planned.  Try it as a fifth spike card (~10 lines), and adopt it only if
   you want a fourth line style?
2. **DragonFly's serial console at 9600 baud** (gettytab `bko.9600`): raise
   it to 115200 as the other images have, or make plain mode its default?
   *Check* why the image chose 9600 (its sio console default, or more).
3. **ui.md's open question 4,** the repository password on Haiku, where
   `/var/run` isn't in RAM: needed by RT11, not by the layer.
4. **`settings.toml`:** stick-config.md says it's read with Python's
   `tomllib`, but bko-rescue is Rust with no crates, and Python is only on
   the Linux and Haiku images.  So whoever reads it (D11) needs a small
   TOML-subset reader of its own (~150 lines, with its own limits and
   fuzz-style tests).  Or the stick's settings move to JSON.
5. **A GateTest2 layer** (`gate:rescue`: the cargo tests and the pty runs),
   in Test2's plan, which other sessions are changing?
6. **The palette:** Whiptail's (as above), or something of our own?  RT3
   settles what each console can show, not what looks best.
