# BackOtter's user interfaces: a Whiptail-style terminal UI on every stick

Status: draft for discussion.  Nothing here is built.  It proposes what
bko-holt has to change so a UI can drive it, and a Whiptail-style 80x24
terminal UI for the rescue sticks (decided 2026-11-09), the default on all
seven.  The Linux stick also gets a full-screen web UI on localhost, from its
own boot entry (decided 2026-11-19; see "The graphical UI on the Linux
stick").  A UI reached over the network, and the backup side's UI, come
later.  Open questions are at the end.

## Goal

bko-holt is a command-line tool for administrators, and stays one.  Most
people restoring a dead machine aren't administrators, and they're doing it
at the worst moment: the machine is gone, the stick is the only thing that
boots, and one wrong disk choice destroys data.  They need a program that
walks them through the restore, shows what will be erased in words they can
check against the hardware, and says plainly what went wrong when something
does.

Non-goals for version 1:

- A graphical UI on the rescue sticks other than Linux's, or by default on
  Linux's (see "The graphical UI on the Linux stick").  The terminal UI comes
  first; the Linux graphical UI follows it.
- Driving a restore from another device over the network (see "Later").
- A UI for the backup side.  The backup tool (`backotter`, which
  `stick-config.md` refers to) isn't built yet; its UI is designed with it.
- Languages other than English.  Strings are kept in one place so that
  translating them later doesn't mean hunting through the code.

## Where a UI runs

There are two places, with very different limits:

1. **The running system**, for backups, choosing a repository, keeping the
   stick up to date, and restoring single files.  There's a working OS with
   its own display, so a graphical UI is reasonable there, later.
2. **The rescue stick**, to rebuild a whole machine.  This document is about
   this one.  The UI runs on whatever console the stick booted to:

| Platform  | Console it gets                          | Display server        |
|-----------|------------------------------------------|-----------------------|
| linux     | fbcon (UEFI), VGA text (BIOS); or serial | none                  |
| freebsd   | vt(4), UTF-8; or serial                  | none                  |
| netbsd    | wscons; or serial                        | none                  |
| openbsd   | wscons; or serial                        | none                  |
| dragonfly | syscons; or serial                       | none                  |
| illumos   | tem; or serial (OmniOS is a server OS)   | none                  |
| haiku     | Haiku's own desktop: a Terminal window   | app_server, always    |

Any of them may also be reached over ssh, when someone rescues a headless
server from another machine.

### The graphical UI on the Linux stick

Decided 2026-11-19: the terminal UI is the default on every stick, and the
Linux stick also offers a graphical UI from its own GRUB entry.  The menu
`stick/make-img.sh` writes gets a fourth entry:

```
BackOtter rescue                        (default: the terminal UI)
BackOtter rescue (graphical)            (new)
BackOtter rescue (safe graphics)        (nomodeset)
BackOtter rescue (serial console ttyS0)
```

- **What it is:** cage (a Wayland compositor that runs one program
  full-screen) runs a small browser, WPE WebKit's `cog`, on a page that
  `bko-rescue` serves on localhost.  The page drives the same steps as the
  terminal UI, through the same bko-holt protocol.
- **Localhost only.**  The server listens on 127.0.0.1 or a Unix socket,
  never on the network, so there's no pairing or encryption to design.
  Opening it to the network is the later step in "Later".
- **Its own casper layer.**  The entry's `layerfs-path=` loads a second
  squashfs with the compositor, Mesa, the browser and fonts.  The image file
  grows, but RAM is used only when someone picks the entry, and the default
  boot stays as slim as today.  The entry passes `bko.ui=gui`, which tells
  the console launcher to start cage instead of the terminal UI.
- **Failure is a reboot.**  The black screen that sank the 2026-11-09 idea
  can't be detected from software, but with the terminal UI as the default,
  recovering is just rebooting and taking the default.  The terminal UI stays
  on another VT (Ctrl+Alt+F2) while the graphical one runs.  There's no
  graphical variant of the safe-graphics entry.
- **Linux only.**  The BSDs, illumos and Haiku have their own boot loaders,
  and Wayland on them is far more work.  Their sticks keep the terminal UI.
- **HTML rather than a native toolkit or a framebuffer GUI,** so that the
  same page later serves a phone or laptop over the network.  A framebuffer
  GUI (DRM/KMS buffers, through Slint or our own drawing) would be a few MB
  instead of an estimated 200 to 400 MB and depend less on the GPU, but it
  would be a second UI used only on this screen.  *Check* the layer's size,
  and whether cog needs Mesa's software GL where no GPU driver attaches.

- **Plain HTML, CSS and JavaScript** (decided 2026-11-19): no framework
  (React and the like), no npm, no bundler, no build step.  The files ship on
  the image as written, in keeping with the no-crates rule on the Rust side.
  JavaScript does what a static page can't: it holds a connection to
  `bko-rescue` (Server-Sent Events or `fetch`) and updates the progress and
  stage ticks as bko-holt's events arrive.  It targets current WebKit (cog)
  only; the later network UI adds Safari, Chrome and Firefox, which handle
  the same plain modern JavaScript.  An estimated few hundred to a couple of
  thousand lines in all.  On the Rust side, `bko-rescue` gets a small
  HTTP/1.1 server (est. 300 to 500 lines) that serves the files, takes the
  answers and passes on the events, so the page and the terminal UI share
  the same logic underneath.

It comes after the terminal UI: both need bko-holt's `--json` protocol, and
the terminal UI's screens prove the flow first.

#### The 2026-11-09 decision, for the record

A framebuffer GUI by default, with this terminal UI as its fallback, was
looked at and turned down on 2026-11-09.  Drawing straight to the framebuffer
needs no display server and adds only a few MB, so size isn't the reason:

- It wouldn't reach most platforms.  It works on Linux, and on FreeBSD and
  NetBSD under UEFI.  OpenBSD (once a GPU driver attaches), DragonFly and
  illumos are impractical or unproven, and BIOS boots come up in text.
  Serial consoles and ssh have no screen at all.
- It isn't safer where it matters.  The erase step would show the same
  words and take the same typed confirmation.
- It fails in new ways.  A program that owns the display also owns
  recovering it, so a monitor power-cycle hours into a restore can leave a
  black screen the kernel's console would have survived.  A black screen
  can't be detected from software, so the fallback can't be automatic.
- It costs far more: an estimated 4,000 to 6,500 lines beyond this UI, and
  testing that needs real GPUs, because QEMU can't stand in for their drivers.

So the stick's default UI is a terminal UI, the same on every platform.
Haiku runs it in a Terminal window.  What changed on 2026-11-19 is the
default: as an opt-in boot entry, the black screen costs a reboot rather
than the restore.

## bko-holt stays a CLI; the UI is a separate program

The UI is a separate binary that runs `bko-holt` and `rustic-bko` the way
an administrator would, and reads their JSON.  It never links bko-holt's
code to call it directly:

- bko-holt keeps its rules: no dependencies, builds with Debian 13's cargo,
  and ships as one file.  A UI toolkit is a dependency.
- Whatever the UI does, an administrator can do by hand with the same
  commands, and the UI can show them (see "Review").  Nothing it does is
  hidden or special.
- The same protocol serves the terminal UI and test harnesses
  (HoltTest1 could read the events instead of parsing text).

Working name: **`bko-rescue`**, the stick's restore program.  It is also the
"restore code" that `stick-config.md`'s "Finding the config at boot" says
reads the config partition, which isn't built either.  So the two are one
program.

## What bko-holt has to change

### Today

- `dump` already writes JSON, and its exit status says unsupported (3).
- `load`'s `plan` and `status` write text for people.
- The writing stages collect their output in a `String` (`exec::run` returns
  `(code, out, err)`) and print it once the whole run is over.  A UI sees
  nothing until the end, and a restore takes hours.
- Problems are plain strings (`target.rs`, `plan.rs`).  The UI could only act
  on them by matching the wording, which breaks as soon as the wording changes.
- With no `--key-file`, cryptsetup asks on the terminal it inherits
  (`run_cmd` passes stdin through).  rustic-bko does the same without
  `--password-file`.  A UI that owns the terminal can't let them.

### `--json` for `load`

One flag.  `plan` and `status` then write one JSON document instead of text,
and the writing stages write events, one JSON object per line, flushed as
they happen.  Exit statuses don't change.  The human text stays the default
and stays as it is.

`plan --json`:

```json
{
  "format": "bko-holt-plan", "schema": 1, "version": "0.3.0",
  "source": {"hostname": "lovelace", "os": {"id": "debian", "version_id": "13"},
             "boot_mode": "uefi", "loader": "grub", "generated": "2026-11-04T02:00:11Z"},
  "disks": [
    {"source": {"name": "vda", "size": 500107862016, "model": "Samsung SSD 870",
                "serial": "S3Z9…", "wwid": "…"},
     "target": {"node": "sdb", "by_id": "/dev/disk/by-id/ata-CT512MX500…",
                "size": 512110190592, "model": "CT512MX500SSD1", "serial": "…",
                "blank": false, "matched_by": "map"}}
  ],
  "candidates": [
    {"node": "sda", "size": 32015679488, "model": "SanDisk Ultra", "serial": "…",
     "blank": false, "refused": "boot_disk"}
  ],
  "erase": ["sdb"],
  "keys": [
    {"group": "luks-root", "names": ["sda3_crypt", "sdb3_crypt"], "kind": "luks",
     "unlock": "passphrase", "given": false}
  ],
  "tokens": [{"container": "sda3_crypt", "kind": "systemd-tpm2"}],
  "problems": [
    {"code": "not_blank", "disk": "vda", "target": "sdb",
     "text": "vda -> /dev/sdb: the target isn't blank (gpt); give --erase /dev/sdb to wipe it"}
  ],
  "notes": [{"code": "pool_renamed", "text": "…"}],
  "stages": [
    {"name": "partition", "commands": [{"argv": ["sfdisk", "…"], "why": "…"}]}
  ]
}
```

- `candidates` lists this machine's whole disks that aren't targets, each
  with why it's refused, if it is (`boot_disk`, `running_root`, `in_use`,
  `read_only`, `too_small`, `sector_size`).  The mapping screen is built from
  `disks` and `candidates`, and needs nothing else.  `too_small` is the one a
  flag can answer: `--fit-largest PCT` fits the layout to a disk a little
  smaller (`layload.md`, "Fitting a disk of another size"), so a candidate
  refused for it also says how much smaller it is, and the plan the UI then
  asks for carries the percentage it offered.
- `problems` and `notes` keep today's text and add a stable `code`, plus the
  disk or name each one is about.  The codes are a small enum next to the
  strings `target.rs`, `plan.rs` and `commands.rs` already build.  A code
  the UI doesn't know is shown as its text and blocks the restore, like any
  other problem.
- `keys` groups containers the way "Keys follow the original system" in
  `layload.md` does: one entry per prompt, and its `unlock` says whether the
  key is a passphrase to ask for or the temporary key load makes itself.
  `tokens` are the TPM2/FIDO2/clevis enrollments to redo after first boot.
- `stages` holds the same commands the text plan prints, as `argv` arrays.

`status --json` is the stage rows `status::rows` already computes:

```json
{"format": "bko-holt-status", "schema": 1,
 "stages": [{"name": "partition", "state": "done", "why": ""}, …],
 "next": "format"}
```

The writing stages with `--json`, one line each:

```
{"event":"start","format":"bko-holt-events","schema":1,"version":"0.3.0","stages":["partition",…],"commit":true,"log":"/run/bko-holt/log/load.log"}
{"event":"stage","stage":"partition"}
{"event":"skip","stage":"assemble","state":"nothing"}
{"event":"cmd","stage":"format","n":3,"of":9,"argv":["mke2fs","…"],"why":"…"}
{"event":"cmd_done","stage":"format","n":3,"status":0,"secs":1.4}
{"event":"progress","stage":"restore","files":18233,"bytes":4113000448,"total_bytes":61203000000}
{"event":"stage_done","stage":"format"}
{"event":"failed","stage":"boot","text":"grub-install failed (exit status: 1): …","log":"/run/bko-holt/log/load.log"}
{"event":"end","exit":1}
```

- `start` comes first and `end` comes last, always, including after a
  failure.  A UI that sees a `schema` it doesn't know stops before
  `--commit` is ever passed.
- `progress` during `restore` needs rustic-bko to report its progress in
  a form load can read.  rustic-bko is our fork (`backotter` branch), so if
  upstream doesn't have such an output, we add a JSON progress option there.
  Until then, `restore` sends no `progress` events and the UI shows the stage
  as running, with a timer.
- Without `--json`, the text is what it is today, only printed as it goes
  rather than at the end.  That's worth doing on its own: an administrator
  watching a restore today sees nothing for hours.

In the code this means `exec::run` writes to a sink (one implementation
for text, one for JSON) instead of returning strings, with tests using a
sink that collects into a `String` as now.  The JSON writer is
`src/json.rs`'s.

### No prompts under `--json`

With `--json`, `plan` reports a problem (`key_needed`) for every key group
not given by `--key-file`.  It reports `password_needed` when there's
`--repo` without `--password-file`.  So nothing ever prompts on a terminal
the UI owns, and nothing can hang waiting for input nobody sees.

Secrets never travel in the protocol or on a command line.  The UI writes
each passphrase to a file under a private directory in RAM: mode 0700,
files 0600, no trailing newline, as `--key-file` already requires.  It
passes the paths, and deletes the files once `load` exits, whatever the
exit.  That uses only flags load already has.

On Linux and the BSDs, `/run` (or `/var/run`) is in RAM on the stick.  On
Haiku it isn't: `RUN_DIR`'s comment notes Haiku's `/var` is the boot
volume's, and Haiku's stick is read-write BFS, so a password file there
would be written to the stick.  The Haiku build of `bko-rescue` has to make
a RAM file system for these first, or pass the repository password another
way (see open question 4).  Haiku has no LUKS or ZFS keys to worry about,
only the repository password.

## The stick's restore program

### What it looks like

Like `whiptail` or any other ncurses dialog: one modal dialog box per step, centred on a plain background
with a title line, asking one thing.  Its widgets are Whiptail's: a menu, a
radio list, a text box, a password box, a yes/no or message box, a progress
gauge and a scrolling text view, with `<OK>` and `<Back>` buttons in a row
under them.  Tab moves between the list and the buttons, and Enter presses
the one in focus.

`bko-rescue` draws these itself rather than running `whiptail` or `dialog`.
The seven systems don't share one such program (FreeBSD has `bsddialog`, for
one), and a shell script around them can't follow `load`'s events well.

### The terminal it can count on

- **80×24.**  Every screen is laid out for that size, and scrolls rather
  than cuts off.  A bigger terminal gets the same box, centred.  It redraws
  when the terminal is resized (see "Toolkit"), for ssh and Haiku's Terminal.
  Where the console can load a bigger font, the launcher picks the largest
  that still leaves 80×24, so the text is readable on a high-resolution
  screen.
- **VT100 and 8 colours**, nothing more assumed: no terminfo lookups, no
  256 colours, no mouse.  Whiptail's blue background and grey boxes where
  there's colour.  Bold and reverse video for focus, so a monochrome serial
  console still shows it.
- **Colour never carries meaning alone.**  What will be erased says
  ERASES in words.  Some consoles map colours oddly, and a serial log has
  none.
- **Line drawing, with an ASCII fallback** (`+-|`), picked per console:
  UTF-8 box characters on the Linux VT, FreeBSD vt(4), Haiku's Terminal
  and illumos's framebuffer tem (its Terminus fonts carry them); DEC
  special graphics (`ESC ( 0`) on NetBSD and OpenBSD wscons; ASCII on
  DragonFly's syscons, illumos in VGA text mode, and serial consoles
  (`vt100`, `vt220`).  Over ssh, the locale decides between UTF-8 and
  ASCII.  A setting forces any of the three.
- **Keyboard only, with a small set of keys**: arrows, Tab, Enter, Esc,
  Space, Backspace, letters.  No function keys or Alt combinations, which
  serial consoles and some ssh clients don't pass through.  Each screen shows
  its keys on the bottom line.
- **A plain mode** (`bko-rescue --plain`): the same steps as numbered
  questions and answers, one line at a time.  It serves dumb terminals,
  logging a session over serial, and anyone using their own screen reader
  over ssh (with an authorized key put on the stick beforehand).  The images
  carry no speech or braille tools, so the console itself isn't accessible.

### Screens

The flow follows "Finding the config at boot" in `stick-config.md`, which
already says what's chosen and confirmed.  Each step can go back.

1. **Keyboard.**  The layout comes first, because the next thing anyone
   types is a password, and a password typed on the wrong layout fails with
   no clue why.  The UI sets the console's keymap with each system's own tool:
   `loadkeys` on Linux, `kbdcontrol` on FreeBSD and DragonFly, `wsconsctl`
   on NetBSD and OpenBSD, `kbd` on illumos.  On Haiku the layout is the
   desktop's, and over ssh or serial it's the client's, so the step is
   skipped there.
2. **Machine.**  The machines on the stick's config partition.  The one
   whose DMI UUID matches is preselected, as `stick-config.md` says.  Each
   shows its hostname, the date of the newest backup the stick knows of, and
   how old the stick's copy is.  If the config is missing or damaged, the
   user can type a repository location instead.
3. **Network**, only when the repository isn't local and there's no
   working route to it.  Wired DHCP is tried before this screen.  The
   screen lists interfaces, and Wi-Fi networks where the platform can join
   them (see open question 3).
4. **Repository.**  The repository password, then the snapshot list from
   rustic-bko (`snapshots --json`), newest first.  It checks that the
   repository's ID matches the stick's (`stick-config.md`, "Which copy wins").
   The layout JSON comes from the chosen snapshot's `system.json`, or from
   the stick's copy when the repository can't be reached.
5. **Disks.**  `plan --json` with no `--map`: disks matched by identity
   come preselected and marked as such.  A menu lists the source disks and
   where each goes, and choosing one opens a radio list of `candidates`,
   each with its size, model and whole serial, so what's on screen can be
   checked against the label on the disk.  Refused disks are shown with the
   reason, so nobody wonders where a disk went:

   ```
    BackOtter rescue

     ┌──────────────────── Disk 1 of 2: where does it go? ────────────────────┐
     │                                                                        │
     │ From the backup of lovelace:                                           │
     │   vda  500 GB  Samsung SSD 870 EVO  serial S6PNNX0T123456A             │
     │                                                                        │
     │ Put it on this disk of this machine:                                   │
     │  ┌──────────────────────────────────────────────────────────────────┐  │
     │  │ (*) sdb   512 GB  CT512MX500SSD1      2213E6123456               │  │
     │  │ ( ) sdc   2.0 TB  WDC WD20EFRX-68E    WD-WX52D1234567            │  │
     │  └──────────────────────────────────────────────────────────────────┘  │
     │                                                                        │
     │ Not offered: sda  32 GB  SanDisk Ultra: this stick, never a target     │
     │                                                                        │
     │                      <  OK  >            < Back >                      │
     └────────────────────────────────────────────────────────────────────────┘

    Arrows move | Space selects | Tab goes to the buttons | Enter confirms
   ```

6. **Passphrases**, when the layout has encryption: one per entry in
   `keys` that's a passphrase, each typed twice.  The screen says which
   containers share it and that it may differ from the old one.  Any `tokens`
   are listed as things to enroll again after the first boot.
7. **Review.**  `plan --json` again, now with `--map` and the key files.
   It shows the hostname, the backup's date, and each disk that will be
   erased with its size, model and serial.  Any problem left blocks the
   next step and says what to do about it.  `c` shows the full command plan,
   the same text `bko-holt load` prints, for anyone who wants to read every
   command first.  To go on, the user types the last six characters of each
   erased disk's serial, as shown beside it, or its size and model where it
   has none.  A kernel name such as `sdb` can't be checked against the
   hardware.  That typed confirmation is the only one.  `bko-rescue` compares
   it, then passes the node names as `--erase`, with `--commit`, and load
   doesn't ask again (`layload.md`, "Safety", rule 2).
8. **Restore.**  `load --through close --json`: the stages with ticks, the
   current command, a progress bar during `restore`, and `l` to show the
   end of the log.  Esc doesn't cancel the restore.  Cancelling asks first,
   and then stops only between commands.
9. **Done, or not.**  On success: what to do after the first boot
   (re-enroll tokens), then remove the stick and reboot.  On failure: the
   stage and its error in full, where the log was saved (the stick's
   `restore-logs/`, as `stick-config.md` says), and the choices to try that
   stage again, open a shell, or power off.

Starting again after a failed or interrupted restore needs no state file.
`bko-rescue` runs `status --json` once the machine and disks are chosen, and
if stages are already done, offers to go on from the next one.
`layload.md`'s stages check the disks themselves, so this works across
reboots too.

The restore outlives the UI.  `bko-rescue` doesn't run `load` as its own
child: it starts it with stdout going to a file in RAM, and follows that file.
On Linux that takes a unit of its own (`systemd-run`), because tty1's console
runs inside `getty@tty1`, and systemd kills everything in that unit's control
group when the unit stops.  Elsewhere it's a new session.  If the UI crashes,
the console restarts it, it finds `load` still running, and it carries on
following.  So that this file stays small, `progress` events come at most once
a second, and load's JSON writer ignores write errors as its log does.

A shell is always one key away on the first screen (and from the failure
screen), for administrators who'd rather type `bko-holt load` themselves.

### Toolkit

Decided 2026-11-19: **our own small layer, with no crates.**  It is
termios raw mode, the window size, `poll` on standard input and a handful
of VT100 escape sequences, with the widgets above on top: about 1,400 to
1,900 lines.  It uses hand-written `extern "C"` declarations the way
`holt/src/sys.rs` does, so bko-rescue keeps bko-holt's rules (no
dependencies, Debian 13's cargo, one file) and builds the way
`testvm/holttest1d/build-holt.sh` builds bko-holt.

Why not a library (researched 2026-11-19, each claim checked against
crate and kernel source):

- **The Rust terminal crates send the wrong colour codes.**  crossterm and
  termion write even the 8 basic colours as 256-colour codes
  (`ESC[38;5;Nm`, `ESC[48;5;Nm`), with no way to ask for SGR 30–37/40–47.
  OpenBSD's wscons and DragonFly's syscons have no case for 38 and 48, so
  they read the numbers that follow as blink, underline and reverse, which
  also corrupts the reverse-video focus mark.  NetBSD's wscons drops the
  colour on a screen without 256 colours (VGA text).  The Linux VT,
  FreeBSD's teken and illumos's tem cope.
- **ratatui 0.29** (0.30 needs Rust 1.86) builds with Rust 1.85 once
  `instability` is held at 0.3.10, and crossterm 0.28 is in the ports of
  helix or television on Haiku, OmniOS and all four BSDs.  But it is only
  usable with a backend of our own, which is the hard half of our own layer
  anyway, plus 37 to 52 vendored crates.  termion has no resize handling;
  termwiz doesn't build on Haiku or DragonFly and needs terminfo.
- **cursive** has the closest widgets and a Whiptail-like theme, but the
  same colour codes, Unicode box characters with no ASCII fallback, and
  about 60 crates.  Its Dialog, SelectView and EditView are the model for
  ours.
- **curses**, through declarations of our own: NetBSD's curses has its own
  constants (attribute bits, ACS, key codes), so one set of declarations
  doesn't fit all seven, and curses brings back terminfo.  The images show
  why that matters: FreeBSD has only termcap.db, and DragonFly's serial
  console sets `TERM=dialup`, which its ncurses can't fall back from.
- **Running whiptail, dialog or bsddialog**: none ships on all seven, and
  one process per screen can't follow load's events.
- **Other languages**: Go doesn't run on Haiku without forks, Python is
  only on Linux and Haiku, and newt, CDK, notcurses and termbox2 each miss
  several platforms or need terminfo.  termbox2, bsddialog, OmniOS kayak's
  `dialog.rc` and DragonFly's `dfuife_curses` are useful reference code.

What the layer has to get right per platform:

- **Each platform's `struct termios`, `TCSA*`, `TIOCGWINSZ` and
  `SIGWINCH`**, in four families of `cfg` constants copied from the `libc`
  crate.  Except Haiku: libc has the layout from before R1/beta6, so ours
  comes from beta6's `termios.h`, and settings are only ever changed by
  reading them, editing and writing them back.
- **`poll`'s `nfds_t`** is an unsigned long on Linux, illumos and Haiku and
  an unsigned int on the four BSDs.
- **illumos has no `cfmakeraw`**, so raw mode clears the flags by hand on
  every platform.
- **Serial consoles report a 0×0 window.**  The launcher sets 80×24 with
  `stty` before drawing.
- **illumos's tem prints an escape sequence it doesn't know**, so
  `ESC ( 0` shows as `(0` there.  The layer sends only the sequences each
  console is known to take.
- **The terminal is put back on a panic** as well as on exit.
- **Resizes are noticed by `poll` timing out** and asking for the window
  size again, rather than in a signal handler.

Before building screens, a spike checks the consoles rather than choosing
a library: draw a box, a list, a gauge and a password field on every
platform's text console and over serial, OpenBSD and DragonFly first.  A
test in each guest compares our struct sizes and field offsets with the C
headers.

## Testing

- The protocol: holt's unit tests, on the fake rescue systems
  `src/load/` already uses.  The JSON documents get checked field by field,
  and the events for a run with a failing command arrive in order with `end`
  last.
- The UI's screens: drive `bko-rescue` through a pty with scripted
  keystrokes against a fake `bko-holt` that replays recorded JSON.  That
  covers every screen, including failures, with no disks.
- Whole machines: `boot-test.py` already boots each image and runs checks
  inside it.  A HoltTest1 variant drives `bko-rescue` over the guest's serial
  console instead of calling `bko-holt load` directly, so every platform's
  real console gets exercised, in plain mode and full-screen mode.

## Later

### Restoring from another device

Postponed on 2026-11-19, for a later discussion: the same web UI, reached
from a phone or laptop over the network, from any platform's stick.  Notes
so far:

- The terminal UI brings the network up first, then offers "continue on
  another device", showing the URL and a one-time pairing code.  A version 2
  QR code (25×25 modules, about 29 columns by 15 rows in half-block
  characters) fits on 80×24 where the console can draw `▀▄█`.
- One controller at a time; the terminal UI shows who is connected and can
  take control back.
- Passwords would cross the network, so it needs TLS (a crate, or an
  external program) or a private link: USB tethering from the phone, or the
  stick as a Wi-Fi access point.
- It reopens the 2026-11-08 decision to keep the images wired only.
- A login secret on the config partition would let a machine with no screen
  be restored from a laptop.

### The backup side

The running system's UI comes with the backup tool.  Its choices, for when
that's designed:

- **A native toolkit per desktop**: GTK 4 / libadwaita for GNOME, Qt 6
  for KDE (Qt also runs on Haiku).  It looks right, at the cost of two code
  bases.
- **One cross-platform Rust toolkit**: Slint (X11, Wayland, macOS,
  Windows), or egui / iced.  One code base, which looks less native.
- **A local web UI** (`backotter serve` on localhost): any browser is the
  GUI, and it also serves restoring a headless server from another machine.
  The cost is a web server in the backup tool and the authentication that
  comes with one.

Whichever it is, it drives the backup tool through a protocol like
`load --json`'s, not by linking it.

## Open questions

1. **Who is the target user** for version 1: administrators who'd like a
   friendlier restore, or anyone at all?  This sets how much the screens
   explain, and how soon the backup side's GUI matters.
2. **The name**: `bko-rescue`, or something else?
3. **Wi-Fi on the stick.**  Each platform has its own tools (iwd or
   wpa_supplicant on Linux, wpa_supplicant and ifconfig on the BSDs, nothing
   much on illumos).  Version 1 could support wired networks only, and say
   so on the Network screen.
4. **The repository password on Haiku**: a RAM file system made by
   `bko-rescue`, or a way to pass it to rustic-bko other than a file?
   rustic's options for that need checking against our fork.

Answered:

- **Does the no-dependencies rule extend to `bko-rescue`?**  Yes, in
  effect: the toolkit is our own layer (see "Toolkit"), 2026-11-19.
- **Progress from rustic-bko**: upstream rustic has printed it as JSON
  since v0.11.3 (`--json-progress`, one NDJSON status line per interval on
  standard output: percent, bytes done and total, seconds elapsed and
  remaining).  It carries no phase name or schema version, and errors stay
  text on standard error, so bko-holt reads it and re-emits it as its own
  versioned `progress` events.  Front ends never parse rustic-bko.
