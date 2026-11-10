# BackOtter's user interfaces: a Whiptail-style terminal UI on every stick

Status: draft for discussion.  Nothing here is built.  It proposes what
bko-holt has to change so a UI can drive it, and a Whiptail-style 80x24
terminal UI for the rescue sticks (decided 2026-11-09).  A graphical UI is for
the backup side, later.  Open questions are at the end.

## Goal

bko-holt is a command-line tool for administrators, and stays one.  Most
people restoring a dead machine aren't administrators, and they're doing it
at the worst moment: the machine is gone, the stick is the only thing that
boots, and one wrong disk choice destroys data.  They need a program that
walks them through the restore, shows what will be erased in words they can
check against the hardware, and says plainly what went wrong when something
does.

Non-goals for version 1:

- A graphical UI on the rescue sticks (see "Why not a GUI on the stick").
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

### Why not a GUI on the stick

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

So the stick's UI is a terminal UI, the same on every platform.  Haiku runs
it in a Terminal window.

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
  `disks` and `candidates`, and needs nothing else.
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
way (see open question 5).  Haiku has no LUKS or ZFS keys to worry about,
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
  when the terminal is resized (SIGWINCH), for ssh and Haiku's Terminal.
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
- **Line drawing, with an ASCII fallback** (`+-|`), picked by locale and
  `TERM`.  Serial consoles (`vt100`, `vt220`), syscons, tem and wscons get
  ASCII unless they show they can do UTF-8.  A setting forces either.
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
   them (see open question 4).
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

The needs are modest: the dialog widgets above, on screens that redraw on
resize.  It has to build
with Rust 1.85 on all seven platforms, offline (vendored), and run on a
VT100.  Two candidates:

- **ratatui** with **crossterm** or **termion**, vendored with `cargo
  vendor`.  Good widgets and well known.  Unknowns: whether crossterm
  builds and behaves on illumos and Haiku.  ratatui 0.30 needs Rust 1.86, so
  it would have to be 0.29.  termion is plain termios, which the BSDs and illumos
  have, but it's less maintained.
- **Our own small layer**: termios raw mode and a handful of VT100 escape
  sequences, with the widgets above on top.  Perhaps 1500 lines.  No
  dependencies, so it keeps bko-holt's rules, and it supports exactly the
  terminals in the table and no more.

Settle it with a spike before building screens: build a ratatui "hello,
list and password field" with each backend on all seven platforms, the way
`testvm/holttest1d/build-holt.sh` builds bko-holt.  Then run it on each
platform's text console and over serial.  If one backend passes everywhere,
use it.  Otherwise write our own layer.

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
2. **Does the no-dependencies rule extend to `bko-rescue`?**  If it does,
   the toolkit is our own layer and the spike only confirms the terminals.
3. **The name**: `bko-rescue`, or something else?
4. **Wi-Fi on the stick.**  Each platform has its own tools (iwd or
   wpa_supplicant on Linux, wpa_supplicant and ifconfig on the BSDs, nothing
   much on illumos).  Version 1 could support wired networks only, and say
   so on the Network screen.
5. **The repository password on Haiku**: a RAM file system made by
   `bko-rescue`, or a way to pass it to rustic-bko other than a file?
   rustic's options for that need checking against our fork.
6. **Progress from rustic-bko**: does upstream rustic already print restore
   progress as JSON, or do we add it in our fork?
