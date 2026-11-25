# bko-rescue

BackOtter's restore program for the rescue stick: one Whiptail-style 80x24
program on all seven rescue platforms (Linux, FreeBSD, NetBSD, OpenBSD,
DragonFly, illumos, Haiku), in Rust, with its own small terminal layer.
The plan is `docs/design/rescue-tui.md`; the screens are
`docs/design/rescue-screens.md`.

Status: RT1-RT8 and RT11.  The crate, the FFI and its header test,
the terminal layer (profiles, keys, events, restore), the sanitiser, the
screen buffer with diffed redraw, the golden-screen and byte-stream
harnesses, the console spike, the widgets with the `Ui` trait and the
full-screen runner, `Secret`, and plain mode; `--demo` shows every widget
and, with `--holt` naming the fake bko-holt, follows its events; the pty
harness (`testvm/rescue/`: `screen.py`, `drive.py`, `fake-bko-holt`).
RT3 ran the spike on every console of the seven images under QEMU and
the header test in every build guest; the profile table is what it
found ("What RT3 found" in the plan).  RT11's first screens (`src/app/`):
with no hidden option, `bko-rescue` runs Machine (`bko-holt stick
--json`), Type a location, the Network notice, Repository (the password,
the hint after two wrong ones, the recovery key through `bko-holt
recovery-words --json`), the keyboard offer and the Keyboard screen,
Which machine?, Snapshot, the problems' help screens and the bug report,
then a screen saying the disks and the restore (RT12) aren't built yet.

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
rsync -aR --delete --exclude target rescue holt/src/json.rs holt/src/bip39-english.txt \
  testvm/rescue HOST:backotter-work/rescue-build/
ssh HOST 'cd ~/backotter-work/rescue-build/rescue &&
  cargo build --locked --offline && cargo test --locked --offline &&
  sh abi/check.sh target/debug/bko-rescue &&
  python3 ../testvm/rescue/pty-test.py target/debug/bko-rescue &&
  python3 ../testvm/rescue/demo-test.py target/debug/bko-rescue &&
  python3 ../testvm/rescue/app-test.py target/debug/bko-rescue'
```

(`testvm/rescue` is for the pty tests and the app's fixtures;
`holt/src/bip39-english.txt` lets the test that `src/bip39-english.txt`
is holt's copy compare them, and is skipped, with a line saying so,
without it.)  `app-test.py` drives the app's screens with the fake
bko-holt (`scenarios/app-three`, `app-dry`) and the fake rustic-bko
(`fake-rustic-bko`, `rustic/NAME`, made by `make-app-fixtures.py`), in
full screen and plain mode; its goldens are `testvm/rescue/screens/app-*`.
The app's own goldens, one per screen, are `tests/screens/app-*.txt`
(`src/app/tests.rs`).  `demo-test.py`
drives `--demo` through every widget with `drive.py`, in full screen
(UTF-8 colour, DEC mono, ASCII) and plain mode, against the fake
bko-holt; its golden screens are `testvm/rescue/screens/`, under the
same `BKO_RESCUE_GOLDEN`.

- `BKO_RESCUE_GOLDEN=check` (the default), `report` or `bless`: what a
  difference from a golden file in `tests/screens/` or `tests/bytes/`
  does.  Bless on the farm and copy the files back to commit them.
- `BKO_RESCUE_FUZZ=N`: cases for each fuzz-style test (default 100,000;
  `cargo test --release` for long runs, as
  `BKO_RESCUE_FUZZ=5000000 cargo test --release --locked --offline`).

## Building for a platform, and the spike (RT3)

`testvm/holttest1d/build-holt.sh` builds bko-rescue with `HOLT_CRATE=rescue`,
in the platform's build guest (on the farm host for linux), and runs the
header test there in the same boot.  From a repo-shaped tree on the host
(rescue/, holt/src/json.rs and the testvm/ and stick/ of the same commit,
never the synced testvm/):

```sh
cd ~/backotter-work && HOLT_CRATE=rescue HOLT_SRC=TREE \
  TREE/testvm/holttest1d/build-holt.sh --reserved RUN PLATFORM
```

The binary lands in `/bkoscratch/rescue-build/bin/PLATFORM/bko-rescue`, the
header test's output beside it (`bko-rescue.abi.txt`) and in
`/bkoscratch/rescue-build/build-PLATFORM/abi.log`; exit 3 means built but the
header test failed.  With no `cc` on PATH (omnios58), check.sh takes `gcc`,
`clang` or `/opt/gcc-*/bin/gcc`; `HOLT_ABI_CC` names one.  Then
`testvm/rescue/spike.py` boots the platform's
current rescue image (BIOS and UEFI) and runs `--spike` on the serial line,
the screen (sendkey and screendumps) and ssh (LANG=C.UTF-8 and LANG=C), on a
host with the images (chopin, cinqcent):

```sh
cd ~/backotter-work && WEAR_TAG=rescue:spike:PLATFORM ./cmp-run.sh --reserve 2 4 \
  python3 -B TREE/testvm/rescue/spike.py PLATFORM /bkoscratch/rescue-build/bin/PLATFORM/bko-rescue \
  --out ~/backotter-work/rescue-spike/PLATFORM-RUN
```

It writes `summary.json` (every check: ok, FAIL, SKIPPED with why, JUDGE for
the PNGs) and per firmware the screens as PNGs and text, the serial log and
each console's bytes.  On the screen it also tries the other line styles,
mono and each capability by the spike's keys (`screen-NN-x-*.png`) and
shows SGR 30-37 on 40-47 at the shell (`x-colours`).  `--loader 'framebuffer
off; set os_console=text'` (illumos) and `--nomodeset` (Linux) reach VGA
text on a BIOS boot, which QEMU otherwise never gives them.  See spike.py's
docstring.

## Options

```
bko-rescue [--plain] [--lines utf8|dec|ascii] [--colour on|off]
           [--holt PATH] [--rustic PATH] [--dry-run]
```

`--lines` and `--colour` beat `BKO_RESCUE_LINES` and `BKO_RESCUE_COLOUR`,
which a launcher sets.  `--holt` and `--rustic` name the programs (each
resolved once to an absolute path); `--dry-run` sets
`BKO_HOLT_NO_COMMIT=1` for bko-holt and stops unless its `--version
--json` says the lock took, and `app/holt_args.rs`, where every bko-holt
command line is built, never adds `--commit` then (D14, J6).

The session's secrets (the password file, the recovery key's password)
are 0600 files in a new 0700 directory under `/run/bko-rescue`
(`/var/run/bko-rescue` off Linux), wiped with it when bko-rescue ends: on
exit, after a signal (caught for the whole session, in plain mode a
password read's too) and on a panic's unwinding.  Bug reports go to
`backotter/reports/` on the stick's BACKOTTER partition, else stay in
`/run/bko-rescue/reports/`.  For the tests, which don't run as root:
`BKO_RESCUE_RUN_DIR` names that directory, and
`BKO_RESCUE_ASSUME_REACHABLE=1` counts every server as reachable (the
fakes have none); nothing on a stick sets either.

Hidden, for tests and checks:

| Option | What it does |
|---|---|
| `--abi` | our view of the C ABI, one fact a line, for the header test |
| `--check-tty` | raw mode set, read back, put back and read back; exit 0 when every field is as expected (for checks.sh) |
| `--profile` | which console this is and what it gets, and why: `os`, `tty`, `term`, `locale`, `size`, `mode`, `rule`, `lines`, `draws` (the styles `L` cycles through), `colour`, `caps`, `esc`, `refresh` |
| `--spike` | the four spike cards (box, list, gauge, password); `--spike panic` panics after the first frame; `--spike linger` waits 5 s after `q`, for the signal test |
| `--demo` | every widget in a row, in full screen or plain mode as the profile says (`--plain` forces it); with `--holt testvm/rescue/fake-bko-holt`, its restore page hands a typed passphrase to the fake as a key file and the backup password as a password file, and follows its dry-run events (holt-json.md's `bko-holt-events`) with the gauge, stopping it unless they say a dry run (without `--holt` it runs nothing) |

In the spike, `1`-`4` and `n` change card, `l` the line style (all
three, whatever the console draws: the spike is for trying them), `c`
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
itself.  POSIX sh, so it runs in every build guest.  RT3 ran it in each
platform's build guest (and on Bigfoot for linux-arm64): it passes on all
seven.  The binaries other than Linux's are dynamic, so their imports
are checked too; Linux's static ones can't be.

## Layout

| File | What |
|---|---|
| `src/main.rs` | the options |
| `src/term/ffi.rs` | `extern "C"` and constants for the four families; `--abi` |
| `src/term/tty.rs` | raw mode and its guard, the size, reads with a timeout, `--check-tty` |
| `src/term/keys.rs` | bytes to keys, a pure state machine |
| `src/term/event.rs` | the poll loop: keys with the Esc timeout, resizes, ticks, signals, hang-up; `Term` |
| `src/term/out.rs` | the sequences each profile may send, line glyphs, attributes |
| `src/term/profile.rs` | which console this is: the table as data, from RT3's spike |
| `src/text.rs` | the sanitiser: `Clean`, caps, width, wrap, truncate |
| `src/screen.rs` | cells, the canvas, diffed redraw, the golden dump |
| `src/testkit.rs` | for the tests: a generator, golden files, the byte checker |
| `src/spike.rs` | the spike's cards |
| `src/ui.rs` | the `Ui` trait, what each dialog shows, `Answer`, `Tick`, and `Scripted` for the app's tests |
| `src/widgets/` | the dialog (layout, focus, buttons, key line), list, field, view, gauge, the too-small notice, and `FullScreen`, the runner |
| `src/secret.rs` | `Secret`, the password buffer: fixed, wiped, no Debug; 0600 key files in a 0700 directory |
| `src/plain.rs` | plain mode: numbered questions, one line at a time |
| `src/demo.rs` | `--demo` |
| `src/app/` | RT11's screens: `mod.rs` (the steps, the help screens, the runner), `strings.rs` (every word), `holt_args.rs` (every bko-holt command line, the dry-run lock), `stick.rs` (`stick --json`), `repo.rs` (rustic-bko: addresses, codes, snapshots), `time.rs` (zones, POSIX rules), `keyboard.rs`, `report.rs`, `sys.rs` (programs, the session's directory), `tests.rs` |
| `src/bip39.rs`, `src/bip39-english.txt` | the recovery key's word list, a copy of holt's, for completion |
| `src/widgets/words.rs` | the recovery key's twelve word fields |
| `tests/widget_screens.rs`, `tests/screens/w-*.txt` | every widget in every line style, colour and mono; their bytes in every profile; random keys |
| `tests/golden_screens.rs`, `tests/screens/` | golden screens of a sample, every line style, colour and mono |
| `tests/byte_streams.rs`, `tests/bytes/` | each profile's bytes, the sequence checker, the byte budgets, random walks |
| `abi/` | the header test's C side and its script |
| `../testvm/rescue/pty-test.py` | the layer on a real pty |
| `../testvm/rescue/screen.py` | a terminal that takes exactly what bko-rescue sends, and dumps the screen in the golden form |
| `../testvm/rescue/drive.py` | bko-rescue on a pty of a set size: keys in, waits on screen text; a script runner |
| `../testvm/rescue/fake-bko-holt`, `scenarios/` | replays a scenario (argv to stdout, events, stderr, exit), checks the key and password files it is given, and refuses `--commit` under `BKO_HOLT_NO_COMMIT` as bko-holt does |
| `../testvm/rescue/holt_schema.py` | bko-holt's documents and events checked against holt-json.md's schema: its goldens, the fake's scenarios, recorded runs |
| `../testvm/rescue/demo-test.py`, `screens/` | `--demo` through every widget, both modes, with the fake |
| `../testvm/rescue/app-test.py`, `fake-rustic-bko`, `rustic/`, `make-app-fixtures.py` | the app's screens on a pty with the fakes, both modes |
