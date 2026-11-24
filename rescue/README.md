# bko-rescue

BackOtter's restore program for the rescue stick: one Whiptail-style 80x24
program on all seven rescue platforms (Linux, FreeBSD, NetBSD, OpenBSD,
DragonFly, illumos, Haiku), in Rust, with its own small terminal layer.
The plan is `docs/design/rescue-tui.md`; the screens are
`docs/design/rescue-screens.md`.

Status: RT1, RT2 and RT4-RT7.  The crate, the FFI and its header test,
the terminal layer (profiles, keys, events, restore), the sanitiser, the
screen buffer with diffed redraw, the golden-screen and byte-stream
harnesses, the console spike, the widgets with the `Ui` trait and the
full-screen runner, `Secret`, and plain mode; `--demo` shows every widget.
RT3 (the spike on every console) hasn't run, so the profile table is
provisional.  No restore screens yet: `bko-rescue` with no hidden option
says so and exits 2.

## Rules

- No dependencies at all, no C (but `abi/abi.c`, the header test's).  It
  builds offline with Rust 1.85, edition 2021.
- `src/json.rs` is holt's, `holt/src/json.rs`, included by `#[path]`.
  Build from a repo-shaped tree: `rescue/` and `holt/src/json.rs`.
- Unsafe code only in `src/term/ffi.rs`, in small safe wrappers, each with
  a `// SAFETY:` comment, and the volatile wipe in `src/secret.rs`.
- Only SGR 0, 1, 7, 30-37 and 40-47; never `38;5` or `48;5`.  The terminal
  is put back on exit, on a panic and on SIGHUP and SIGTERM.

## Building and testing

The Mac has no Rust.  On a farm host, from the repo root:

```sh
rsync -aR --delete rescue holt/src/json.rs HOST:backotter-work/rescue-build/
ssh HOST 'cd ~/backotter-work/rescue-build/rescue &&
  cargo build --locked --offline && cargo test --locked --offline &&
  sh abi/check.sh target/debug/bko-rescue &&
  python3 ../testvm/rescue/pty-test.py target/debug/bko-rescue'
```

(add `testvm/rescue` to the rsync for the pty test).

- `BKO_RESCUE_GOLDEN=check` (the default), `report` or `bless`: what a
  difference from a golden file in `tests/screens/` or `tests/bytes/`
  does.  Bless on the farm and copy the files back to commit them.
- `BKO_RESCUE_FUZZ=N`: cases for each fuzz-style test (default 100,000;
  `cargo test --release` for long runs).

## Options

```
bko-rescue [--plain] [--lines utf8|dec|ascii] [--colour on|off]
           [--holt PATH] [--rustic PATH] [--dry-run]
```

`--lines` and `--colour` beat `BKO_RESCUE_LINES` and `BKO_RESCUE_COLOUR`,
which a launcher sets.  Hidden, for tests and checks:

| Option | What it does |
|---|---|
| `--abi` | our view of the C ABI, one fact a line, for the header test |
| `--check-tty` | raw mode set, read back, put back and read back; exit 0 when every field is as expected (for checks.sh) |
| `--profile` | which console this is and what it gets, and why: `os`, `tty`, `term`, `locale`, `size`, `mode`, `rule`, `lines`, `colour`, `caps`, `esc`, `refresh` |
| `--spike` | the four spike cards (box, list, gauge, password); `--spike panic` panics after the first frame; `--spike linger` waits 5 s after `q`, for the signal test |
| `--demo` | every widget in a row, in full screen or plain mode as the profile says (`--plain` forces it); RT8 adds the fake bko-holt |

In the spike, `1`-`4` and `n` change card, `l` the line style, `c`
colour, `j` joined SGR parameters, `h` cursor hiding, `e` clearing with
`ESC [ 2 J`, and `q` quits.  In the password field every key is typed;
Esc or Tab leaves it.  Keys that came in the same read as that Esc or Tab
(Alt+key, or an ESC in a fast passphrase) are shown as `*` and never run
as commands.

## The header test

```sh
sh abi/check.sh PATH/TO/bko-rescue [CC]
```

It builds `abi.c` with the system's `cc` and headers, diffs its lines with
`bko-rescue --abi`'s, and checks that each function links to the name the
C program imports (`nm -P`).  A wrong ioctl request type fails the C build
itself.  POSIX sh, so it runs in every build guest.  RT3 runs it in each
platform's build guest; so far it has run on Linux (czerny, Debian 13,
glibc).

## Layout

| File | What |
|---|---|
| `src/main.rs` | the options |
| `src/term/ffi.rs` | `extern "C"` and constants for the four families; `--abi` |
| `src/term/tty.rs` | raw mode and its guard, the size, reads with a timeout, `--check-tty` |
| `src/term/keys.rs` | bytes to keys, a pure state machine |
| `src/term/event.rs` | the poll loop: keys with the Esc timeout, resizes, ticks, signals, hang-up; `Term` |
| `src/term/out.rs` | the sequences each profile may send, line glyphs, attributes |
| `src/term/profile.rs` | which console this is: the table as data, provisional until RT3 |
| `src/text.rs` | the sanitiser: `Clean`, caps, width, wrap, truncate |
| `src/screen.rs` | cells, the canvas, diffed redraw, the golden dump |
| `src/testkit.rs` | for the tests: a generator, golden files, the byte checker |
| `src/spike.rs` | the spike's cards |
| `src/ui.rs` | the `Ui` trait, what each dialog shows, `Answer`, `Tick`, and `Scripted` for the app's tests |
| `src/widgets/` | the dialog (layout, focus, buttons, key line), list, field, view, gauge, the too-small notice, and `FullScreen`, the runner |
| `src/secret.rs` | `Secret`, the password buffer: fixed, wiped, no Debug; 0600 key files in a 0700 directory |
| `src/plain.rs` | plain mode: numbered questions, one line at a time |
| `src/demo.rs` | `--demo` |
| `tests/widget_screens.rs`, `tests/screens/w-*.txt` | every widget in every line style, colour and mono; their bytes in every profile; random keys |
| `tests/golden_screens.rs`, `tests/screens/` | golden screens of a sample, every line style, colour and mono |
| `tests/byte_streams.rs`, `tests/bytes/` | each profile's bytes, the sequence checker, the byte budgets, random walks |
| `abi/` | the header test's C side and its script |
| `../testvm/rescue/pty-test.py` | the layer on a real pty |
