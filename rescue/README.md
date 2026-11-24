# bko-rescue

BackOtter's restore program for the rescue stick: one Whiptail-style 80x24
program on all seven rescue platforms (Linux, FreeBSD, NetBSD, OpenBSD,
DragonFly, illumos, Haiku), in Rust, with its own small terminal layer.
The plan is `docs/design/rescue-tui.md`; the screens are
`docs/design/rescue-screens.md`.

Status: RT1 and RT2.  The crate, the FFI and its header test, and the
first cut of the terminal layer with the console spike.  No restore
screens yet: `bko-rescue` with no hidden option says so and exits 2.

## Rules

- No dependencies at all, no C (but `abi/abi.c`, the header test's).  It
  builds offline with Rust 1.85, edition 2021.
- `src/json.rs` is holt's, `holt/src/json.rs`, included by `#[path]`.
  Build from a repo-shaped tree: `rescue/` and `holt/src/json.rs`.
- Unsafe code only in `src/term/ffi.rs`, in small safe wrappers, each with
  a `// SAFETY:` comment.
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
| `--profile` | which console this is and what it gets, and why: `os`, `tty`, `term`, `locale`, `size`, `mode`, `rule`, `lines`, `colour`, `caps` |
| `--spike` | the four spike cards (box, list, gauge, password); `--spike panic` panics after the first frame |
| `--demo` | RT8; not built |

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
| `src/term/out.rs` | the sequences each profile may send, line glyphs, attributes |
| `src/term/profile.rs` | which console this is (the first table, before RT3) |
| `src/spike.rs` | the spike's cards |
| `abi/` | the header test's C side and its script |
| `../testvm/rescue/pty-test.py` | the layer on a real pty |
