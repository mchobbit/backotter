# bko-rescue: the restore screens (mock-ups)

Status: Draft, 2026-11-24, revised after review on 2026-11-23 and after the
user's decisions of 2026-11-24, not approved.
Mock-ups for RT11 and RT12 of rescue-tui.md.

Every screen of the stick's restore program, drawn at 80x24 with the
widgets of `rescue-tui.md` ("Widgets"), in the flow of `ui.md` ("The stick's
restore program", "Screens") and `stick-config.md` ("Finding the config at
boot").  It adds what was decided in conversation from 2026-11-19 to
2026-11-24 and isn't in those docs yet: the snapshot screen, the password
hint, the recovery key, a help screen per problem, the bug report and its
unredacted Advanced form, the size-mismatch offer, the decision for a
bigger shrink, the grow choice for a much bigger disk, the keyboard
offers, times in the machine's own zone, the Advanced reformat screen and
plain mode's transcripts.  Where a screen
needs something bko-holt doesn't do yet, it says so, and "What the screens
need from bko-holt" lists it for RT10.

The sample machine is `lovelace` (Debian 13, UEFI, GRUB, LUKS), backed up on
22 November 2026 and restored onto a new computer, so no machine on the
stick was backed up from this one.  Its two old disks were a Samsung SSD 870
EVO 500 GB (the system) and a WD Red 2 TB (`/srv`).  The new computer has a
Crucial P3 1 TB, the same WD Red moved over, a Kingston A400 480 GB that
holds someone's photos, and the stick, a SanDisk Ultra 32 GB.  Disks are
named on screen by model, size and serial, as the plan reports them; kernel
names (`sda`, `nvme0n1`) appear only in the command plan and the log, since
the old machine's `sda` and this computer's `sda` are different disks.

## The flow

1. Machine, or Type a location (then "Which machine?" when the repository
   holds more than one and none is known).  The keyboard is US English
   until changed.
2. Network (only when wired DHCP found no route)
3. Repository: the host key and a login where needed, the keyboard offer
   (when the machine's backup names another layout), then the password,
   the hint after two wrong ones, or the recovery key
4. Snapshot
5. Disks: the overview, then a list per old disk.  A disk up to 10 %
   smaller or bigger opens an offer.  A bigger shrink, where the data
   still fits, opens a screen where the user decides.  A disk much bigger
   opens a choice: keep the old sizes, or grow / to fill it.  A disk the
   data doesn't fit can't be chosen.
6. Passphrases (only when the layout has encryption), after a second
   keyboard offer when the machine's start-up prompt uses another layout
7. Resume offer (only when `status` finds bko-holt's work on these disks)
8. Review, with Commands and Advanced
9. Restore, with the log (`l`) and Cancel
10. Done, or Failure

The Keyboard screen isn't a step: it opens from `k` or < Keyboard > on the
first screen, on Type a location, on the keyboard offers and on the
password screens, and goes back to the screen that opened it.

A problem at any step opens its help screen.  Back goes one step up this
list, to the last screen that was shown: screens that were skipped (Network,
Passphrases, Resume) are skipped going back too.  The exceptions: Machine
has no Back, being first; Type a location goes back to Machine, and has no
Back when it came up by itself; "Which machine?" goes back to Type a
location; Restore, Done and Failure have no Back.

## Conventions

- **The grid.**  The canvas is 80 columns by 24 rows, whatever the
  terminal's size; a bigger terminal gets it centred.  Row 1 is the title
  line: `BackOtter rescue` at the left, the screen's name at the right,
  ending in column 80.  There's no step count: Network, Passphrases and
  Resume come and go, so "step 4 of 10" would often be wrong.  Row 24 is
  the key line.  Rows 2 to 23 hold one dialog box, centred both ways.  The
  box is 76 columns wide (columns 3 to 78), with one space of padding each
  side, so text runs 72 columns.  Lists inside the box have their own frame
  and 66 columns of text.  Each mock below is the whole screen, all 24
  rows, blank ones included.
- **The box** has its title in the top border.  In colour it's Whiptail's:
  grey box, blue background, a black shadow one column right and one row
  down.  The shadow is colour only, so the mocks don't draw it.
- **Buttons** sit in a row at the bottom of the box, centred, each in angle
  brackets with the label padded: `<  OK  >   < Back >`.  A button that
  can't be pressed yet isn't drawn greyed: it says why in the body, and
  Enter on it shows that reason again.
- **Focus** is reverse video, with bold, on one list row, field or button.
  Code blocks can't show that, so the first bullet under each mock says
  where the focus starts.  Tab moves it between the body and each button in
  turn; Enter presses what has it; Esc is Back.
- **Choosing from a list.**  In a list of one choice, the choice follows
  the focus: the row with the focus is the one marked `(*)`, and Enter on
  it confirms it, as OK does.  There's no separate Space to select, so the
  row seen is always the row chosen.  (This changes `rescue-tui.md`'s radio
  list, whose Space selects; Space still does nothing harmful.)
- **The key line** lists the keys the screen takes, in plain ASCII in
  every console profile, so it reads even when the box characters don't.
  The keys are arrows, Tab, Enter, Esc, Space, Backspace and letters, never
  function keys or Alt.  Letters work when the focus isn't in a text field;
  in one, they're typed.  Letters are commands only, never a jump to a list
  row, and either case works.  Every button with a letter shows it on the
  key line, the same letter everywhere: `s` Shell, `h` Help, `r` Report,
  `o` Raw output, `c` Commands, `l` log, `v` View it, `k` Keyboard.
- **Lists** show as many rows as fit; a longer one scrolls with the focus,
  and shows `^ more above` and `v more below` as its first or last row,
  only when there is more that way.
- **Text** wraps at word boundaries to the box's 72 columns.  A long
  word (a path, a serial) never breaks: it goes on a line of its own.  Text
  views (Commands, the log, raw output, the report) never wrap: a long line
  is cut with `>` at the edge, and Left and Right scroll sideways.  Sizes
  are in GB or TB, decimal, with three significant figures, as disks are
  sold; serials are always shown whole.
- **Times** are UTC until a machine is chosen.  From then on they are in
  that machine's own time zone, as its backup recorded it, and always
  carry the zone: `22 Nov 2026 02:00 CET`.  The Machine screen shows each
  row in its own machine's zone.  Logs, file names and the bug report
  stay UTC, marked `Z`.  This is the proposed behaviour (decided
  2026-11-24, with open question 6 left on the fallbacks).
- **Plain words on the screens, technical ones in the text views.**  A
  screen says "backup", "network port", "Copy your files back"; the
  commands, kernel names and snapshot IDs are a key away (`c`, `l`, `o`).
- **Colour never carries meaning alone.**  What will be erased says ERASED
  in words; done stages say `[x]`; problems and failures say "Problem" in
  their title.  An offer with a safe fix (a disk a little smaller) isn't a
  problem, and its title says what it is.
- **Line style.**  UTF-8 box drawing as here, DEC special graphics where
  the console has it (NetBSD, OpenBSD), ASCII elsewhere (see "The ASCII
  fallback").  `L` and `C` on the first screen (Machine, or Type a
  location when it comes up by itself) change line style and colour (D4),
  and `P` there switches to plain mode.
- **Secrets** show as one `*` per character (D8).  The one exception is the
  recovery key's words, shown in clear (see "Recovery key").  Text fields
  take typing and Backspace only (D9).
- **Your backup is safe.**  Every Failure and help screen says, in one
  line, that nothing in the backup has been changed.  bko-rescue only ever
  reads the repository.

## Too small a terminal

Drawn instead of any screen when the terminal is smaller than 80x24, here
an ssh window of 60x14.  It uses only the space it has, so it's 14 rows,
not 24.

```text
 BackOtter rescue

  ┌────────────── This window is too small ──────────────┐
  │                                                      │
  │ bko-rescue needs 80 columns and 24 rows.             │
  │ This one has 60 columns and 14 rows.                 │
  │                                                      │
  │ Make the window bigger, or press p for plain mode:   │
  │ the same questions, one line at a time.              │
  │                                                      │
  └──────────────────────────────────────────────────────┘


 p: plain mode | s: shell | Enter: check again
```

- No focus; the screen waits for a key or a resize.  A resize to 80x24 or
  more draws the screen that was due (D10).
- `p` restarts in plain mode (`--plain`) at the same step; `s` opens a
  shell; Enter measures the window again.
- Below 40x10 it's three plain lines with no box.
- Fed by `TIOCGWINSZ` at start and on each poll tick.

## 1. Machine

The first screen.  The keyboard starts as US English: nothing is typed on
this screen, and by the first password the machine is known, so its own
layout can be offered then (see "The keyboard offer").  This changes
`ui.md`'s "Screens", where Keyboard is step 1.

```text
 BackOtter rescue                                                        Machine


  ┌───────────────────────── Which machine is this? ─────────────────────────┐
  │                                                                          │
  │ The stick knows these machines.  None of them was backed up from this    │
  │ computer.  Pick the one you are restoring.                               │
  │                                                                          │
  │  ┌────────────────────────────────────────────────────────────────────┐  │
  │  │ ( ) lovelace   Debian 13      newest backup 22 Nov 2026 02:00 CET  │  │
  │  │ ( ) babbage    Fedora 45      newest backup 21 Nov 2026 03:00 EST  │  │
  │  │ ( ) hopper     FreeBSD 15.0   newest backup 18 Nov 2026 01:00 UTC  │  │
  │  └────────────────────────────────────────────────────────────────────┘  │
  │                                                                          │
  │ The one you pick is restored onto this computer, so check the name:      │
  │ it is the computer's name from when it was backed up.                    │
  │                                                                          │
  │ Keyboard: English (US).  k changes it.                                   │
  │                                                                          │
  │       <  OK  >    < Type a location >    < Keyboard >    < Shell >       │
  └──────────────────────────────────────────────────────────────────────────┘


 Arrows | Enter: OK | k: keyboard | s: shell | L: lines | C: colour | P: plain
```

- When a machine on the stick was backed up from this very computer, its
  row ends "backed up from this computer", it has the focus, and the first
  line says so instead.  That's the case of restoring onto the same
  hardware.  Here, on a new computer, no row has it, nothing is chosen at
  first, and OK says "Pick a machine first" until an arrow moves the focus
  onto a row.
- With one machine on the stick it's still shown, so the name is seen.
- Each row's time is in that machine's own zone, from its `system.json`
  (the new `time` section), and says which.  A `system.json` without one
  shows UTC.
- Fed by `bko-holt stick --json` (D11): each machine's hostname, OS,
  `system.json`'s `written` and `sequence`, `dmi.system_uuid` (compared
  with this computer's), the checksum state, and now its `keyboard` and
  `time` sections.
- A machine whose checksum fails or whose schema the stick doesn't know is
  listed with that reason in words and can't be chosen.
- `s` or < Shell > opens a shell; leaving it comes back here.  `L` cycles
  UTF-8, DEC and ASCII lines; `C` turns colour on or off; `P` restarts in
  plain mode at this step.  They're on the first screen only, so they
  never fire by accident later.
- The boot menu says the same for anyone who can't see the screen: its
  entry "BackOtter rescue, plain text (for screen readers)" starts plain
  mode from the outset.
- < Type a location > opens the screen below.  There's no Back: this is
  the first screen.  Esc does nothing here.
- Over ssh, serial and on Haiku the keyboard line and < Keyboard > aren't
  drawn (see "Keyboard layout").
- Two sticks from one image on illumos: a message box asks for the other
  one to be removed (`stick-config.md`, step 1), with Retry and Shell.

### Type a location

When the stick's BACKOTTER partition (its settings) is missing or damaged,
or by choice.  Before it's drawn, bko-rescue looks for backups on this
computer's other disks (each filesystem mounted read-only, looking for a
rustic-bko repository), so typing is the fallback.

```text
 BackOtter rescue                                                        Machine
  ┌────────────────────────── Where is the backup? ──────────────────────────┐
  │                                                                          │
  │ This stick can't tell which machine to restore: its BACKOTTER            │
  │ partition, which holds its settings, is missing.                         │
  │                                                                          │
  │ Backups found on this computer's disks:                                  │
  │  ┌────────────────────────────────────────────────────────────────────┐  │
  │  │ (*) WD Elements 25A3, 4.00 TB: backups of lovelace and babbage     │  │
  │  └────────────────────────────────────────────────────────────────────┘  │
  │                                                                          │
  │ Or type the address of your backup, as written on the sheet from when    │
  │ the backups were set up:                                                 │
  │                                                                          │
  │ [                                                                   ]    │
  │                                                                          │
  │ For example:  sftp://nas.local/backotter/lovelace                        │
  │               rest:http://10.0.0.5:8000/lovelace                         │
  │ Keyboard: English (US).  Change it before typing: < Keyboard >.          │
  │                                                                          │
  │                  <  OK  >    < Keyboard >    < Shell >                   │
  └──────────────────────────────────────────────────────────────────────────┘

 Arrows: choose | Tab: next | Enter: OK | k, s: keyboard, shell (not in field)
```

- Focus starts on the list when something was found, else in the field.
  The first lines say why this screen came up (missing, checksum failed,
  unknown schema), or nothing when chosen.  The field starts empty: with
  no settings, nothing could know the address.
- A disk found this way is "the backup drive" from here on, and Disks
  never offers it as a target.
- OK goes to Network if the address needs one and there's no route, else
  to Repository.  A malformed address is answered in the body, in words.
- With no machine known, the repository may hold several.  After the
  password, a "Which machine?" list of the hostnames it has (the Machine
  screen's look, without the "this computer" check) comes before Snapshot;
  with one hostname it's skipped.
- < Shell > is here for a backup drive that needs mounting by hand.
- Drawn by itself, as here, it's the first screen: no Back, and `L`, `C`
  and `P` work as on Machine.  Chosen from Machine, it has < Back > after
  < Shell >, and Back goes to Machine.
- No machine is known yet, so there's no keyboard offer: a German keyboard
  set to US English types `&` for `/` (Shift+7), and the address would
  come out wrong.  The keyboard line asks for the layout first.  After
  the password, "Which machine?" makes the machine known, and the keyboard
  offer before the passphrases still applies.

## 2. Network

Only when the repository isn't local and wired DHCP, tried before any
screen, found no route to it.  The images are wired only (decided
2026-11-08).

```text
 BackOtter rescue                                                        Network



  ┌─────────────────────────────── No network ───────────────────────────────┐
  │                                                                          │
  │ lovelace's backups are on nas.local, which this computer can't reach.    │
  │ This stick uses wired networks only: plug a network cable from this      │
  │ computer into your router or network.                                    │
  │                                                                          │
  │  ┌── Network ports ───────────────────────────────────────────────────┐  │
  │  │ Port 1   no cable plugged in                       (enp3s0)        │  │
  │  │ Port 2   cable in, but no address after 30 s       (enp4s0)        │  │
  │  └────────────────────────────────────────────────────────────────────┘  │
  │                                                                          │
  │ Wi-Fi can't be used from the rescue stick.                               │
  │                                                                          │
  │        < Try again >    < Set by hand >    < Shell >    < Back >         │
  └──────────────────────────────────────────────────────────────────────────┘




 Tab: buttons | Enter: press | s: shell | Esc: back
```

- Focus starts on < Try again >, which asks every port with a cable for
  an address (DHCP) and shows a gauge of the 30 s wait.  It also tries by
  itself when a cable is plugged in.
- < Set by hand > is a form: port, address/prefix, gateway, DNS server,
  each a text field.
- Once there's a route, it goes on to Repository without a key press.
- Back goes to Machine.  The `repo_unreachable` help screen offers this
  screen as its fix.

## 3. Repository

The repository is where the backups are kept: here, a folder on nas.local.
The screens say "your backups" and name the place.

```text
 BackOtter rescue                                                     Repository



  ┌────────────────────────── The backup password ───────────────────────────┐
  │                                                                          │
  │ lovelace's backups are kept on nas.local, at                             │
  │   sftp://nas.local/backotter/lovelace                                    │
  │                                                                          │
  │ Type the backup password:                                                │
  │                                                                          │
  │ [**************                                                     ]    │
  │                                                                          │
  │ It's the password chosen when the backups were set up, not the           │
  │ password for this computer.                                              │
  │                                                                          │
  │ Keyboard: German (de, nodeadkeys), from lovelace's backup.               │
  │                                                                          │
  │    <  OK  >   < Keyboard >   < Use recovery key instead >   < Back >     │
  └──────────────────────────────────────────────────────────────────────────┘



 Type the password | Tab: buttons | Enter: OK | Esc: back
```

- Focus starts in the field.  Enter tries it: the password goes to a 0600
  file in RAM, and `rustic-bko snapshots --json` runs with
  `--password-file`.  A gauge shows while it works.
- It checks the repository's ID against the stick's (`stick-config.md`,
  "Which copy wins").  A mismatch is the `repo_id_mismatch` help screen,
  never a silent switch.
- The keyboard line says which layout is set and where it came from
  ("from lovelace's backup", "chosen by you", or "the stick's default").
  < Keyboard > opens the Keyboard screen; `k` would be typed in the field.
- < Use recovery key instead > opens the recovery key screen.  Back goes
  to Machine (or Network, when it was shown).
- Before this screen, for sftp: a host key the stick doesn't know is shown
  (below).  If the server asks for a login (the stick has no key for it),
  a screen titled "Log in to nas.local" asks for the user name and that
  password first, so the two passwords are never confused.  The keyboard
  offer comes before whichever password is asked first.

### The keyboard offer

Shown once, before the first password is typed (the login for nas.local
when there is one, else the backup password), when the machine's
`system.json` names a layout and it isn't the one set now.

```text
 BackOtter rescue                                                     Repository





  ┌─────────────────────── Keyboard for the password ────────────────────────┐
  │                                                                          │
  │ lovelace used a German keyboard (de, nodeadkeys) when it was backed      │
  │ up.  This stick is set to English (US).                                  │
  │                                                                          │
  │ Switch to German for the password?                                       │
  │                                                                          │
  │ On a German keyboard set to English (US), Y and Z are swapped and most   │
  │ symbols move, so the password would come out wrong.                      │
  │                                                                          │
  │    < Switch to German >   < Keep English (US) >   < Another layout >     │
  └──────────────────────────────────────────────────────────────────────────┘





 Tab: buttons | Enter: press | Esc: keep English (US)
```

- Focus starts on < Switch to German >: the backup's layout is the likely
  one.  Nothing is switched until a button is pressed, and the password
  screen then says which layout is set.  Esc keeps the layout set now.
  < Another layout > opens the Keyboard screen.
- The layout offered is `keyboard.canonical` from the machine's
  `system.json`: the one in use when the backups were set up (`at_setup`,
  when set-up recorded it), else the first of the users' desktop layouts,
  else the system's XKB layout, else the console map.  When the desktop
  had more than one (`de`, then `us`), the body adds "Its desktop also had
  English (US)", and the Keyboard screen lists those first.
- The physical keyboard may not be the old one (a new computer, a laptop
  with another keyboard), so this is an offer, never applied by itself.
- A layout this stick has only without its variant is offered as the
  base layout, with a line: "This stick has German, but not its 'no dead
  keys' variant: the accent keys wait for the next key."  A layout this
  stick can't load at all, or none recorded (an older `system.json`, or
  nothing found at backup time): no offer, and the password screen's
  keyboard line says "English (US).  lovelace's backup names Dvorak,
  which this stick can't load" or "lovelace's backup doesn't say which
  layout it used".  The stick's mapping table decides (open question 11).
- Not shown over ssh (the layout is the ssh client's) or on a serial
  console (the terminal's).  On Haiku, where the stick can't change the
  layout, there's no offer and no < Keyboard >; the keyboard line names
  the backup's layout, so it can be set in Haiku's Keymap preferences.
- Shown once per machine: Back and forward again doesn't repeat it.
  Picking another machine on Machine can show it again, for that one.

### Keyboard layout

The screen behind `k`, < Keyboard > and < Another layout >.  It's no
longer the first screen (it was step 1 of `ui.md`'s "Screens").

```text
 BackOtter rescue                                                       Keyboard

  ┌──────────────────────────── Keyboard layout ─────────────────────────────┐
  │                                                                          │
  │ Which keyboard is this?  Pick the layout printed on its keys.            │
  │                                                                          │
  │  ┌────────────────────────────────────────────────────────────────────┐  │
  │  │ (*) us              English (US)                                   │  │
  │  │ ( ) de nodeadkeys   German, no dead keys            lovelace's     │  │
  │  │ ( ) de              German                                         │  │
  │  │ ( ) uk              English (UK)                                   │  │
  │  │ ( ) fr              French (AZERTY)                                │  │
  │  │ ( ) be              Belgian                                        │  │
  │  │ ( ) es              Spanish                                        │  │
  │  │ v more below                                                       │  │
  │  └────────────────────────────────────────────────────────────────────┘  │
  │                                                                          │
  │ Try it here:  [                                        ]                 │
  │                                                                          │
  │                           <  OK  >    < Back >                           │
  └──────────────────────────────────────────────────────────────────────────┘


 Arrows: choose | Tab: buttons | Enter: OK | Esc: back, as it was
```

- Focus starts on the layout set now.  The machine's own layouts, once a
  machine is known, come next and say whose they are; then the rest.
- The layout is applied as the focus moves, with each system's own tool
  (`loadkeys`, `kbdcontrol`, `wsconsctl`, `kbd`), so the "Try it here"
  field tests it.  What's typed there is thrown away.  OK keeps it; Back
  and Esc put back the layout there was, and both return to the screen
  that opened this one.
- Rows are XKB names (layout and variant), the same names the dump
  records.  The Linux stick compiles any of them with `ckbcomp | loadkeys`;
  the BSD and illumos sticks use their mapping table, and a row it can't
  map isn't listed there.
- Letters don't jump in the list (see Conventions), so pressing `s` for
  Swedish or `l` for Lithuanian can't be misread: those are reached with
  the arrows.
- A default from the stick's `settings.toml` waits on D4 (nothing reads
  TOML yet, and `settings.toml` has no keyboard key); until then the
  default is `us` (decided 2026-11-24).
- Never drawn over ssh, serial or on Haiku.

### A server seen for the first time

```text
 BackOtter rescue                                                     Repository



  ┌─────────────────────────── Is this nas.local? ───────────────────────────┐
  │                                                                          │
  │ This computer has never talked to nas.local before.  nas.local says      │
  │ its fingerprint is:                                                      │
  │                                                                          │
  │   SHA256:Xm3q9V0b6kTq2r1cJ8YwZ4hL5dN7pF0sA2uE9gK1oRw                     │
  │                                                                          │
  │ To check it, run this on nas.local itself, or ask whoever runs it:       │
  │                                                                          │
  │   ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub                       │
  │                                                                          │
  │ If the two match, answer Yes.  If they don't, something may be           │
  │ pretending to be nas.local: answer No, and nothing is sent to it.        │
  │                                                                          │
  │                     < Yes, they match >    <  No  >                      │
  └──────────────────────────────────────────────────────────────────────────┘



 Tab: buttons | Enter: press | Esc: no
```

- Focus starts on < No >: trusting is never the default.
- Yes goes on (login, then password); the key is kept for this session
  only.  No goes back to Machine, where < Type a location > or another
  machine can be chosen.
- When set-up stored the server's key on the stick, this screen isn't
  shown, and a key that differs from the stored one is a problem screen
  (`repo_login_failed`), not a question.

### After two wrong passwords

```text
 BackOtter rescue                                                     Repository



  ┌────────────────────────── The backup password ───────────────────────────┐
  │                                                                          │
  │ That password didn't open the backups either.  Check Caps Lock, and      │
  │ the keyboard: it is set to German (de, nodeadkeys).                      │
  │                                                                          │
  │ Your hint, saved when the backups were set up:                           │
  │   "the boat's name, then the year we moved"                              │
  │                                                                          │
  │ Type the backup password:                                                │
  │                                                                          │
  │ [                                                                   ]    │
  │                                                                          │
  │ Tries so far: 2                                                          │
  │                                                                          │
  │    <  OK  >   < Keyboard >   < Use recovery key instead >   < Back >     │
  └──────────────────────────────────────────────────────────────────────────┘



 Type the password | Tab: buttons | Enter: OK | Esc: back
```

- After the first wrong password, the same screen says "That password
  didn't open the backups", without the hint lines.  The hint shows from
  the second wrong one on, never before (decided 2026-11-24).  It is
  readable by anyone who has the stick, since two wrong passwords show
  it, so set-up says it mustn't give the password away.
- The hint comes from the machine's `system.json` on the stick (a new
  `password_hint` field, open question 2), or from beside the repository
  when the stick has none.  Without one, those three lines aren't shown.
- < Keyboard > opens the Keyboard screen, and OK there comes back here
  with the hint kept.  Over ssh the line reads "The keyboard layout is
  your own computer's" and there's no button.
- The field is cleared.  No limit on tries; from the third, the body adds
  "a layout with Y and Z swapped is a usual cause".
- rustic-bko's "wrong password" is told apart from other failures by its
  exit and text; each other failure has its own code (see "Problems and
  their help").

### Recovery key

```text
 BackOtter rescue                                                     Repository


  ┌────────────────────────────── Recovery key ──────────────────────────────┐
  │                                                                          │
  │ Type the 12 words printed on the recovery sheet when the backups were    │
  │ set up, in the sheet's order.  After four letters, the rest of a word    │
  │ is filled in for you.                                                    │
  │                                                                          │
  │    1 [harvest  ]      2 [orbit    ]      3 [velvet   ]                   │
  │    4 [candle   ]      5 [noble    ]      6 [tiger    ]                   │
  │    7 [quantum  ]      8 [elbow    ]      9 [ribb     ]                   │
  │   10 [         ]     11 [         ]     12 [         ]                   │
  │                                                                          │
  │ Word 9: ribb -> ribbon.  Space takes it and moves to word 10.            │
  │                                                                          │
  │ The last word holds a check, so most typing mistakes are caught here.    │
  │                                                                          │
  │                           <  OK  >    < Back >                           │
  └──────────────────────────────────────────────────────────────────────────┘



 Letters type | Space: take the word | Tab: buttons | Enter: OK | Esc: back
```

- Focus starts in word 1.  The fields run across, in rows of three, as
  the recovery sheet prints them (set-up's sheet is to use this order).
- After four letters the completion shows on its own line below the
  fields (`ribb -> ribbon`), never inside the field, so it can't be taken
  for typed letters with colour off or in ASCII.  The first four letters
  of a BIP39 word are unique.  Space takes the completion, or the letters
  as typed when they already make a whole word (`act`, `add`, `zoo`: the
  three-letter words, which are also the start of longer ones), and moves
  on.  Tab keeps its usual job: it leaves the fields for the buttons.
- A word that can't be on the list is named at once: "Word 7 isn't one of
  the recovery words".  Backspace in an empty field goes back a word.
- On OK the checksum is checked before the repository is tried.  It can
  name a word only when it isn't on the list; otherwise it says "The 12
  words don't add up: one is wrong, or two are swapped.  Check each
  against the sheet" (open question 1).
- Each word is shown in clear, not as `*`: the key is read off paper, and
  checking it against the paper matters more.  The words are kept in a
  `Secret` like a password.
- Back goes to the password screen.

## 4. Snapshot

```text
 BackOtter rescue                                                       Snapshot

  ┌───────────────────────────── Which backup? ──────────────────────────────┐
  │                                                                          │
  │ lovelace's backups, newest first:                                        │
  │                                                                          │
  │  ┌────────────────────────────────────────────────────────────────────┐  │
  │  │ (*) 22 Nov 2026 02:00 CET    61.2 GB   newest       a7f3c901       │  │
  │  │ ( ) 21 Nov 2026 02:00 CET    61.0 GB                3be04d2a       │  │
  │  │ ( ) 20 Nov 2026 02:00 CET    60.9 GB                d1185e77       │  │
  │  │ ( ) 15 Nov 2026 02:00 CET    60.4 GB   weekly       4e6f1d08       │  │
  │  │ ( ) 01 Nov 2026 02:00 CET    59.8 GB   monthly      c03a77b2       │  │
  │  │ ( ) 01 Oct 2026 02:00 CEST   58.9 GB   monthly      5d2e90af       │  │
  │  │ v 8 more below                                                     │  │
  │  └────────────────────────────────────────────────────────────────────┘  │
  │                                                                          │
  │ Times are in lovelace's own time zone, Europe/Berlin: CET in winter,     │
  │ CEST in summer.  The newest is usually the one you want.                 │
  │                                                                          │
  │                           <  OK  >    < Back >                           │
  └──────────────────────────────────────────────────────────────────────────┘


 Arrows: choose | Tab: buttons | Enter: OK | Esc: back
```

- Focus starts on the newest backup.  Each row's snapshot ID comes last,
  for anyone matching it against a log; nothing asks for it.
- Times are shown in the machine's zone, each with its own abbreviation,
  so a summer backup says CEST.  Which zone, in this order: the `time`
  section's `zone`, when the image has zoneinfo for it (the TUI runs with
  `TZ=Europe/Berlin`, for its own process only); else its `posix_rule`
  (`CET-1CEST,M3.5.0,M10.5.0/3`), worked out by the TUI itself; else each
  snapshot's own offset, as rustic-bko stored it (`2026-11-22T02:00+01:00`),
  shown as "02:00 UTC+01:00".  The last line names what is used: "Times
  are lovelace's offset from UTC, as each backup recorded it" in the
  third case.  Open question 6 is on these fallbacks.
- Fed by `rustic-bko snapshots --json`, filtered to the machine's
  hostname (and its install ID tag), or to the hostname picked on "Which
  machine?".  Tags such as `weekly` are shown.
- The layout comes from the chosen backup's `system.json`.  If that
  differs from the stick's copy, a line says "This backup's disk layout
  differs from the stick's copy; the backup's is used".
- A machine with one backup still gets this screen, so the date is seen.
- Back goes to Repository, with the password kept.

## 5. Disks

`plan --json` with no `--map`, run once on entry.  Disks matched by
identity come chosen and say so.

```text
 BackOtter rescue                                                          Disks
  ┌────────────────────────── Where each disk goes ──────────────────────────┐
  │                                                                          │
  │ lovelace had 2 disks.  Choose a disk of this computer for each one.      │
  │                                                                          │
  │  ┌────────────────────────────────────────────────────────────────────┐  │
  │  │ Old disk 1: Samsung SSD 870 EVO 500GB, 500 GB, the system          │  │
  │  │   -> CT1000P3PSSD8, 1.00 TB, serial 2318E6F3A9C1                   │  │
  │  │      will be ERASED, then filled from the backup    chosen by you  │  │
  │  │ Old disk 2: WDC WD20EFRX-68EUZN0, 2.00 TB, /srv                    │  │
  │  │   -> the same disk, found by its serial WD-WCC4M5XK7HPL            │  │
  │  │      will be ERASED, then filled from the backup                   │  │
  │  └────────────────────────────────────────────────────────────────────┘  │
  │                                                                          │
  │ Old disk 2 is lovelace's own /srv disk.  It will be erased and refilled  │
  │ from the 22 Nov 2026 backup: anything saved on it after that is lost.    │
  │                                                                          │
  │ Not used:     KINGSTON SA400S37480G, 480 GB, serial 50026B7782A1F3C4     │
  │               has data: 1 partition, ext4 "photos", 312 GB used          │
  │ Not offered:  SanDisk Ultra, 32 GB: this stick, never a target           │
  │                                                                          │
  │                           <  OK  >    < Back >                           │
  └──────────────────────────────────────────────────────────────────────────┘
 Arrows: old disk | Enter: change where it goes | Tab: buttons | Esc: back
```

- Focus starts on the first old disk with no target, else on OK.  Enter
  on an old disk opens its list (below).
- Fed by `plan --json`'s `disks` (with `matched_by`) and `candidates`
  (with `refused`), and what each candidate holds: its partitions, their
  filesystem types and labels and the space used, or "blank".  bko-rescue
  reads that itself (`lsblk`, `blkid`, a read-only mount for "used"),
  unless the plan grows a field for it.
- Every row that goes to a disk says ERASED in words.  A disk matched by
  serial (the same disk moved over) gets the line under the list, and
  Review repeats it.
- Refused disks are always listed with the reason in words: this stick,
  the backup drive ("holds the backup being restored, never a target"),
  the running root, in use, read-only, too small, sector size.
- OK is pressed only when every old disk has a target; otherwise it says
  which one is missing.  A plan with a `not_mapped` problem comes back
  to this screen.
- Back goes to Snapshot.

### One disk

```text
 BackOtter rescue                                                          Disks
  ┌─────────────────── Old disk 1 of 2: where does it go? ───────────────────┐
  │                                                                          │
  │ From the backup of lovelace:                                             │
  │   Old disk 1: Samsung SSD 870 EVO 500GB, 500 GB, the system              │
  │   serial S6PXNM0T412345A                                                 │
  │                                                                          │
  │ Put it on this disk of this computer.  The disk you pick is ERASED.      │
  │  ┌────────────────────────────────────────────────────────────────────┐  │
  │  │ (*) CT1000P3PSSD8           1.00 TB  serial 2318E6F3A9C1           │  │
  │  │       blank: nothing on it                                         │  │
  │  │       500 GB bigger: left free, or / grows to fill it (you choose) │  │
  │  │ ( ) KINGSTON SA400S37480G    480 GB  serial 50026B7782A1F3C4       │  │
  │  │       has data: 1 partition, ext4 "photos", 312 GB used            │  │
  │  │       20 GB smaller: / would shrink from 465 GB to 445 GB          │  │
  │  └────────────────────────────────────────────────────────────────────┘  │
  │                                                                          │
  │ Already used:  WDC WD20EFRX-68EUZN0, for old disk 2                      │
  │ Not offered:   SanDisk Ultra, 32 GB: this stick, never a target          │
  │                                                                          │
  │                           <  OK  >    < Back >                           │
  └──────────────────────────────────────────────────────────────────────────┘

 Arrows: choose | Enter: OK | Tab: buttons | Esc: back
```

- Focus starts on the current target or the first candidate.  The row
  with the focus is the one chosen (Conventions), and Enter takes it, so
  arrowing to the KINGSTON and pressing Enter puts old disk 1 there.
- Each row has the whole serial, to check against the disk's label, and
  what's on the disk now, so the one with the photos is told from a blank
  one.
- A candidate that is smaller (the `too_small` refusal says how much)
  shows what a shrink would do, in words.  The percentage is the one
  bko-holt uses: how much the largest partition changes, here / by
  4.3 %.  Whether the data fits decides, not the percentage (decided
  2026-11-24).  Three cases:
  - up to 10 % and the data fits: taking it opens "This disk is a little
    smaller", the offer, so the shrink is agreed to, not implied;
  - more than 10 % and the data fits with the margin: the row says "much
    smaller: / would shrink from 465 GB to 285 GB, 38.7 %", and taking
    it opens "This disk is much smaller: you decide", where nothing is
    chosen at first;
  - the data doesn't fit: the row says "too small: / uses 41.7 GB; the
    most it could get is 29 GB", and it can't be taken.  Enter on it
    shows the figures in a sentence: "/ holds 41.7 GB; the most it could
    get on this disk is 29 GB" (drawn below, "Too small for the data").
- The data fits when the space used on the partition at the backup, and
  the margin, are no more than the most the partition could get on the
  new disk.  The margin proposed is 10 % of the space used plus 2 GiB
  (open question 15).  Where the space used comes from is below.
- A candidate a little bigger (10 % or less) says what the same flag
  would do the other way: "/ would grow from 465 GB to 488 GB", and
  taking it opens the same offer, worded for growing.
- A disk more than 10 % bigger, like this CT1000P3PSSD8, has no upper
  limit (decided 2026-11-24).  Its row says the extra is left free or /
  grows, and taking it opens "This disk is much bigger" (below), where
  keeping the old sizes is chosen at first.  (A shrink chosen for another
  disk can still grow it: see "This disk is a little smaller".)
- Enter or OK goes back to the overview, which runs `plan --json` with the
  new `--map`.  Back goes to the overview with nothing changed.

### Too small for the data

A variant: this computer has, in place of the Crucial and the Kingston, a
WDC WD3200AAKX 320 GB and a Transcend 64 GB SSD.

```text
 BackOtter rescue                                                          Disks

  ┌─────────────────── Old disk 1 of 2: where does it go? ───────────────────┐
  │                                                                          │
  │ From the backup of lovelace:                                             │
  │   Old disk 1: Samsung SSD 870 EVO 500GB, 500 GB, the system              │
  │   serial S6PXNM0T412345A                                                 │
  │                                                                          │
  │ Put it on this disk of this computer.  The disk you pick is ERASED.      │
  │  ┌────────────────────────────────────────────────────────────────────┐  │
  │  │ (*) WDC WD3200AAKX           320 GB  serial WD-WCAV2K318842        │  │
  │  │       blank: nothing on it                                         │  │
  │  │       much smaller: / would shrink from 465 GB to 285 GB, 38.7 %   │  │
  │  │ ( ) TS64GSSD370S              64 GB  serial F431270087             │  │
  │  │       too small: / uses 41.7 GB; the most it could get is 29 GB    │  │
  │  └────────────────────────────────────────────────────────────────────┘  │
  │                                                                          │
  │ Already used:  WDC WD20EFRX-68EUZN0, for old disk 2                      │
  │ Not offered:   SanDisk Ultra, 32 GB: this stick, never a target          │
  │                                                                          │
  │                           <  OK  >    < Back >                           │
  └──────────────────────────────────────────────────────────────────────────┘

 Arrows: choose | Enter: OK | Tab: buttons | Esc: back
```

- The row that can't be taken stays in the list, so the user sees why.
  With the focus on it, OK and Enter don't take it: they show, under the
  list, "/ holds 41.7 GB; the most it could get on this disk is 29 GB.
  It needs 48 GB, with room to spare.  Choose another disk."
- The most is the disk's size less the other partitions as they were,
  rounded down to bko-holt's alignment: 64 GB less 35 GB here.  It needs
  the space used and the margin: 41.7 GB, 4.2 GB and 2 GiB.
- The figures come from bko-holt's refusal (`data_wont_fit`, "What the
  screens need from bko-holt"), not from the stick's own sums, so the
  stick and bko-holt can't disagree.
- A disk bko-holt can't resize at all (a ZFS pool, another system's
  layout, a partition type fit doesn't know) says so instead, in words:
  "too small: / is on a ZFS pool, which isn't resized".

### Where the space used comes from

The space used is the file system's own figure, recorded by `dump` at the
backup.  The snapshot's size is a second source, but it counts the files,
not the file system's overhead, so it is never used without the margin.

- Linux: `storage.mounts` gives `size_bytes`, `used_bytes` and
  `available_bytes` for every mounted file system that isn't a pseudo one
  (statvfs on 64-bit, `df -Pk` on 32-bit; `report.rs` around line 1514,
  `mounts.rs`, `sys.rs`).  That covers ext2/3/4, XFS, btrfs, f2fs, JFS
  and NILFS2, under LUKS and LVM too.  A btrfs mount gives the whole file
  system's figure, whichever subvolume it mounts.  The swaps' `used_bytes`
  (line 1626) is swap in use, not data.
- An ext file system not mounted at the backup: fit falls back to the
  superblock's free blocks.  Any other one not mounted has no figure, and
  fit refuses the shrink ("the dump doesn't say how much of it is used").
- ZFS on Linux: each mounted dataset has its own figure (statvfs: what
  the dataset itself refers to, not its children or snapshots).  The
  pool's `allocated` and `free` are left out of the dump on purpose.
  bko-holt doesn't resize a pool, so a ZFS disk keeps its sizes: a smaller
  one is refused, a bigger one isn't grown.
- FreeBSD, DragonFly, NetBSD, OpenBSD, illumos and Haiku
  (`holt/src/other/`): mounts are recorded with no size or space used.
  bko-holt doesn't resize their layouts either: a smaller disk is refused
  ("a smaller target is still refused"), a bigger one keeps the old
  sizes.  So today a disk of another size changes nothing there.

### A much bigger disk: keep or grow

Old disk 1 put on the CT1000P3PSSD8, 500 GB bigger.

```text
 BackOtter rescue                                                          Disks


  ┌──────────────────────── This disk is much bigger ────────────────────────┐
  │                                                                          │
  │ Old disk 1 (Samsung SSD 870 EVO 500GB) was 500 GB.  The disk you chose,  │
  │ CT1000P3PSSD8, is 1.00 TB: 500 GB bigger.                                │
  │                                                                          │
  │ The partitions can stay as they were, with the extra 500 GB left free at │
  │ the end of the disk.  Or the system partition (/) can grow to fill it:   │
  │   Old size:    465 GB                                                    │
  │   New size:    965 GB                                                    │
  │ The other partitions stay as they were either way.                       │
  │                                                                          │
  │  ┌────────────────────────────────────────────────────────────────────┐  │
  │  │ (*) Keep the old sizes, and leave 500 GB free                      │  │
  │  │ ( ) Grow / to 965 GB, to fill the disk                             │  │
  │  └────────────────────────────────────────────────────────────────────┘  │
  │                                                                          │
  │                  <  OK  >    < Raw output >    < Back >                  │
  └──────────────────────────────────────────────────────────────────────────┘


 Arrows: choose | Tab: buttons | Enter: OK | o: raw output | Esc: back
```

- Until that option exists, the list row says "500 GB bigger: left free"
  only.
- Keeping the old sizes is chosen at first, as bko-holt does without a
  flag: nothing is lost either way, and space left free can be used
  later.  Enter alone keeps them.
- Growing has no upper limit (decided 2026-11-24).  bko-holt can't do it
  yet: `--fit-largest` grows by at most its percentage, and 50 % at most,
  so 465 GB would reach 697 GB.  The grow row waits for a grow-to-fill
  option ("What the screens need from bko-holt"); until then it isn't
  shown, and the body says the extra stays free.
- Grow keeps the mapping with that option; Review says "/ grows from
  465 GB to 965 GB, to fill the disk, as you chose".
- When bko-holt can't grow the layout (fit's notes give the reason: a ZFS
  pool, another system's layout, an MBR entry's limit), the grow row isn't
  there and the body gives the reason in words.
- A disk 10 % bigger or less gets the offer instead, worded for growing.
- OK takes the choice and goes back to the overview, as Enter on the disk
  list does.  Esc and Back go to the disk's list with nothing changed.

## 6. Passphrases

One screen per `keys` entry whose `unlock` is `passphrase`.  Before it,
`status --json` with the chosen map tells a fresh disk from one bko-holt
has already partitioned (see "Resume offer").

```text
 BackOtter rescue                                                    Passphrases



  ┌─────────────────────────── Passphrase 1 of 1 ────────────────────────────┐
  │                                                                          │
  │ This passphrase opens both encrypted disks: the system disk              │
  │ (CT1000P3PSSD8) and the /srv disk (WDC WD20EFRX-68EUZN0).                │
  │                                                                          │
  │ You will type it every time the computer starts.  It may be the old one  │
  │ or a new one.  If it is forgotten, nothing can open these disks: write   │
  │ it down.                                                                 │
  │                                                                          │
  │ Passphrase:     [****************                                ]       │
  │ Type it again:  [****************                                ]       │
  │                                                                          │
  │ Keyboard: English (US), as at lovelace's start-up prompt.                │
  │                                                                          │
  │                           <  OK  >    < Back >                           │
  └──────────────────────────────────────────────────────────────────────────┘



 Type | Tab: next field and buttons | Enter: next field, then OK | Esc: back
```

- Focus starts in the first field; Enter there moves to the second.  OK
  checks the two match; if not, both are cleared and the body says so.  An
  empty passphrase isn't taken.
- Each passphrase goes to a 0600 file in RAM, passed as `--key-file` for
  each container in the group; `plan --json` then no longer reports
  `key_needed` for it.
- When the backup kept the disk's own encryption header (`dump
  --keep-luks-header`, as on Ubuntu Core), only the old passphrase opens
  it: the screen asks once, for "the passphrase this disk had", and says
  why.  The plan's `keys` entry is to say whether a new one is possible
  (RT10).
- When the first `status` finds bko-holt's partitions already on these
  disks (a resume), the containers exist: the screen asks once, for "the
  passphrase given the first time", with no second field.  A wrong one
  shows when `assemble` opens them (`wrong_passphrase`), and its help
  screen asks again.
- TPM2, FIDO2 and clevis unlocking can't be restored; Done says how to set
  it up again.
- The keyboard line says which layout is set, and whether it is the one
  lovelace's start-up prompt uses (below).  When the dump doesn't know
  that prompt's layout, the line says so: "Which layout lovelace's
  start-up prompt uses isn't known; plain letters and digits are the
  safest".
- Back goes to the previous passphrase, then to Disks.

### The keyboard at start-up

Shown once, before the first passphrase screen, when the layout set now
isn't the one the machine's start-up prompt uses.  The passphrase typed
here is typed again at every start, at that prompt, so what matters is
that the same keys give the same characters there.

```text
 BackOtter rescue                                                    Passphrases




  ┌──────────────────── Keyboard for the disk passphrase ────────────────────┐
  │                                                                          │
  │ When lovelace starts, it asks for the disk passphrase before its German  │
  │ layout is loaded, so the keyboard acts as English (US) there.            │
  │                                                                          │
  │ Switch to English (US) now?  Then the passphrase you type here is typed  │
  │ the same way at each start.                                              │
  │                                                                          │
  │ With English (US) on a German keyboard, Y and Z are swapped and most     │
  │ symbols move.  A passphrase of the other letters, digits and spaces      │
  │ types the same with either layout.                                       │
  │                                                                          │
  │              < Switch to English (US) >    < Keep German >               │
  └──────────────────────────────────────────────────────────────────────────┘




 Tab: buttons | Enter: press | Esc: back
```

- Focus starts on < Switch to English (US) >, the choice that makes the
  start-up prompt take what is typed here.  Esc is Back (to Disks), not
  Keep, since keeping is the choice that can lock the disks.
- Fed by `keyboard.boot_prompt`: where the passphrase is typed at start
  and with which map.  lovelace is Debian: initramfs-tools loads the
  console map only with `KEYMAP=y` in `initramfs.conf` (initramfs.conf(5)),
  and it has the default, `n`, so the prompt is US (`us_fallback`).  On a
  system whose prompt loads the map (dracut's i18n module, mkinitcpio's
  `keymap` or `sd-vconsole` hook, booster's `vconsole: true`, mkinitfs's
  keymap feature) the offer is to switch to that map.  FreeBSD's loader
  (GELI), OpenBSD's boot (softraid) and GRUB's own prompt without a GRUB
  keymap (Manjaro's LUKS1) are always US.
- Not shown when the layouts already agree, when the dump doesn't know the
  prompt's layout (the Passphrases line says so instead), over ssh or
  serial, or on Haiku.
- The same holds for an old passphrase typed here (a kept header, a
  resume): it was set up at that prompt.
- The layout stays as chosen afterwards.  Review's serial fields show
  what is typed, so a swapped Y and Z there shows at once.

## 7. Resume offer

Shown only when `status` finds bko-holt's work on these disks.  `status`
can't see inside encrypted containers or an unmounted tree, so bko-rescue
first gets the passphrases, then opens and mounts what's there (`--only
assemble`, `--only mount`, which open what exists and never make it anew:
RT10 is to confirm that), then runs `status --json` again.  A stage still
"unknown" after that is looked at again after mounting, never taken as
done.

```text
 BackOtter rescue                                                         Resume


  ┌─────────────────── A restore was started here before ────────────────────┐
  │                                                                          │
  │ These disks already hold part of a restore of lovelace:                  │
  │   CT1000P3PSSD8, 1.00 TB, serial 2318E6F3A9C1                            │
  │   WDC WD20EFRX-68EUZN0, 2.00 TB, serial WD-WCC4M5XK7HPL                  │
  │                                                                          │
  │   [x] Make partitions           [x] Set up encryption                    │
  │   [x] Make filesystems          [x] Ready the filesystems                │
  │   [ ] Copy your files back      started, not known to be finished        │
  │   [ ] Make it bootable          [ ] Finish                               │
  │                                                                          │
  │ Go on: nothing is erased.  Your files are copied back again, over what   │
  │ is there, then the rest is done.                                         │
  │ Start again: everything on these disks is erased first.                  │
  │ Either way, Review comes next and asks for the serials.                  │
  │                                                                          │
  │                 < Go on >    < Start again >    < Back >                 │
  └──────────────────────────────────────────────────────────────────────────┘


 Tab: buttons | Enter: press | Esc: back
```

- Focus starts on < Go on >.  The disks are named, model and serial, so
  they can be checked against what was meant.
- "Copy your files back" counts as done only when load has marked it
  complete (a mark written when the archiver exits 0; RT10).  `status`
  today calls it done once `/etc/fstab` and `/etc/os-release` exist, which
  a stopped copy has too, so until that mark exists the copy always runs
  again.
- Go on leads to Review, which says "Nothing is erased: the restore goes
  on from "Copy your files back"" and still asks for the serials.  It then
  runs `load --only STAGE --commit` once per stage, from `status`'s `next`
  to `close`, with a fresh `status --json` between runs (load has no
  `--from`; `--through` always starts at `partition`).
- Start again goes to Review as for new disks.
- Back goes to Passphrases (or Disks, with no encryption).

## 8. Review

`plan --json` again, with the full argument list (below).  The only
confirmation before anything is written.

```text
 BackOtter rescue                                                         Review

  ┌──────────────────────────── Ready to restore ────────────────────────────┐
  │                                                                          │
  │ Restore lovelace (Debian 13) from the backup of 22 Nov 2026 02:00 CET,   │
  │ 61.2 GB.  Backed up from a different computer.                           │
  │                                                                          │
  │ These disks will be ERASED, then filled from the backup.  Type the last  │
  │ six characters of each serial.  Check it against the label on the disk   │
  │ if you can: that's how you know it is the right disk.                    │
  │  ┌────────────────────────────────────────────────────────────────────┐  │
  │  │ ERASED  1.00 TB  CT1000P3PSSD8          2318E6F3A9C1     [F3A9C1]  │  │
  │  │ ERASED  2.00 TB  WDC WD20EFRX-68EUZN0   WD-WCC4M5XK7HPL  [XK7H  ]  │  │
  │  └────────────────────────────────────────────────────────────────────┘  │
  │ The WDC WD20EFRX-68EUZN0 is lovelace's own /srv disk: anything saved     │
  │ on it after 22 Nov 2026 is lost.                                         │
  │ Changes: none.  No problems found.                                       │
  │ Not touched: this stick, the KINGSTON SA400S37480G.                      │
  │ Nothing has been changed on any disk yet.  Back is safe.                 │
  │                                                                          │
  │         < Restore >    < Commands >    < Advanced >    < Back >          │
  └──────────────────────────────────────────────────────────────────────────┘

 Type the serials | Tab: next | Enter on Restore: start | c: commands | Esc
```

- Focus starts in the first serial field.  Enter in a field moves to the
  next one; Enter after the last moves to < Restore >.  Only Enter on
  < Restore > starts.  `c` works when a button has the focus, so a serial
  with a C in it is typed, not taken as a key.
- < Restore > presses only when every field matches, case-insensitive;
  otherwise it says which disk doesn't.  A disk with no serial asks for its
  size and model instead.
- "Backed up from this computer" or "from a different computer" comes
  from comparing `dmi.system_uuid` with this computer's.
- Fed by `plan --json`: `source`, `erase`, `disks`, `candidates`,
  `problems`, `notes`.  Any problem left replaces "No problems found" with
  its line and blocks < Restore >; Enter on that line opens its help screen.
- The erase list scrolls when there are more disks than fit.  "Changes:"
  lists, one per line, what differs from the old machine: a partition that
  shrinks or grows on any disk (from fit's notes, since `--fit-largest`
  applies to every mapped disk), a filesystem made as another type, a new
  passphrase.  The fullest case is drawn below.  A shrink of more than
  10 %, chosen on the decision screen, says so: "/ shrinks by 38.7 %, from
  465 GB to 285 GB, as you chose", and a disk it grows says by how much.
  A grow chosen for a much bigger disk says "/ grows from 465 GB to
  965 GB, to fill the disk, as you chose".  The old sizes kept on a bigger
  disk aren't a change: Review says nothing of the space left free.
- The typed confirmation is the only one: bko-rescue passes each erased
  disk as `--erase` with `--commit`, and load doesn't ask again.
- On a resume the first line says "Nothing is erased: the restore goes on
  from ..." and the list's ERASED becomes "goes on"; the serials are still
  asked for.
- < Advanced > opens the reformat screen.  Back goes to Resume, or
  Passphrases, or Disks, whichever was last shown, and the fields are
  cleared.

### Review at its fullest

A variant to show the space suffices: lovelace with a third old disk
(`/home`, a Samsung 980 1 TB, put on the CT1000P3PSSD8), the system disk on
the KINGSTON with a shrink, and `/srv` made XFS.

```text
 BackOtter rescue                                                         Review
  ┌──────────────────────────── Ready to restore ────────────────────────────┐
  │                                                                          │
  │ Restore lovelace (Debian 13) from the backup of 22 Nov 2026 02:00 CET,   │
  │ 61.2 GB.  Backed up from a different computer.                           │
  │                                                                          │
  │ These disks will be ERASED, then filled from the backup.  Type the last  │
  │ six characters of each serial; check it on the disk's label if you can.  │
  │  ┌────────────────────────────────────────────────────────────────────┐  │
  │  │ ERASED   480 GB  KINGSTON SA400S37480G  50026B7782A1F3C4 [      ]  │  │
  │  │ ERASED  1.00 TB  CT1000P3PSSD8          2318E6F3A9C1     [      ]  │  │
  │  │ ERASED  2.00 TB  WDC WD20EFRX-68EUZN0   WD-WCC4M5XK7HPL  [      ]  │  │
  │  └────────────────────────────────────────────────────────────────────┘  │
  │ Changes:  / (the system partition) shrinks by 4.3 %, 465 to 445 GB.      │
  │           /srv is made as XFS, not ext4.                                 │
  │           The encrypted disks open with the passphrase you chose.        │
  │ The WDC WD20EFRX-68EUZN0 is lovelace's own /srv disk: anything saved     │
  │ on it after 22 Nov 2026 is lost.                                         │
  │ Not touched: this stick.  No problems found.                             │
  │ Nothing has been changed on any disk yet.  Back is safe.                 │
  │                                                                          │
  │         < Restore >    < Commands >    < Advanced >    < Back >          │
  └──────────────────────────────────────────────────────────────────────────┘
 Type the serials | Tab: next | Enter on Restore: start | c: commands | Esc
```

- Four disks still fit with one fewer "Changes" line; more than that, the
  list scrolls (`v 1 more below`) and stays three rows high.
- The passphrase line doesn't say "new": bko-rescue can't tell whether
  the one typed is the old one.  It names the disks it opens instead.

### The full argument list

Review's plan and Restore's load share one argument list, so the plan
reviewed is the run made.  For the sample:

```text
bko-holt load /run/bko-rescue/layout.json
  --repo sftp://nas.local/backotter/lovelace
  --password-file /run/bko-rescue/pw
  --snapshot a7f3c901
  --map sda=/dev/disk/by-id/nvme-CT1000P3PSSD8_2318E6F3A9C1
  --map sdb=/dev/disk/by-id/ata-WDC_WD20EFRX-68EUZN0_WD-WCC4M5XK7HPL
  --erase /dev/disk/by-id/nvme-CT1000P3PSSD8_2318E6F3A9C1
  --erase /dev/disk/by-id/ata-WDC_WD20EFRX-68EUZN0_WD-WCC4M5XK7HPL
  --key-file cryptroot=/run/bko-rescue/k/0
  --key-file cryptsrv=/run/bko-rescue/k/0
  [--fit-largest PCT] [--reformat MOUNT=TYPE ...] [--removable --no-nvram]
  --json
```

- Review adds `--through plan`; Restore adds `--through close --commit`;
  a resume or a retry adds `--only STAGE --commit`, one stage at a time.
- `--snapshot` is the ID chosen on Snapshot.  Without it load restores
  `latest`, which may not be the backup the user picked.
- The layout file is the chosen backup's `system.json`, or the stick's
  copy when they match.

### Commands

< Commands > or `c` from Review: the plan's `stages` as text.  The mock is
illustrative: the real text is what `bko-holt load --through plan` prints,
with each `why` as `#` lines above its command and each table as a
heredoc.

```text
 BackOtter rescue                                                         Review
  ┌──────────────────────── Every command, in order ─────────────────────────┐
  │ # == partition (2 disks)                                                 │
  │ # GPT, 4 partitions, as lovelace's sda                                   │
  │ wipefs --all /dev/nvme0n1                                                │
  │ sfdisk --wipe always --wipe-partitions always /dev/nvme0n1 <<'EOF'       │
  │ label: gpt                                                               │
  │ label-id: 7D2B4E10-3C9A-4F6B-9E21-5A8C0D4F3B77                           │
  │ first-lba: 2048                                                          │
  │ /dev/nvme0n1p1 : start=2048, size=1048576, type=C12A7328-F81F-11D2-BA4>  │
  │ /dev/nvme0n1p2 : start=1050624, size=2097152, type=0FC63DAF-8483-4772->  │
  │ EOF                                                                      │
  │ # == assemble                                                            │
  │ # cryptroot: LUKS2, the source's UUID and cipher, a new key              │
  │ cryptsetup luksFormat --batch-mode --type=luks2 --uuid=6f1c2a9e-4b7d-4>  │
  │ cryptsetup open --key-file /run/bko-rescue/k/0 /dev/nvme0n1p3 cryptroot  │
  │ # == format                                                              │
  │ mke2fs -F -q -t ext4 -O none,has_journal,ext_attr,dir_index,filetype,e>  │
  │ v more below                                                             │
  │                                                                          │
  │                               <  Close  >                                │
  └──────────────────────────────────────────────────────────────────────────┘

 Arrows: line | Left/Right: sideways | Space: page down | Enter/Esc: close
```

- A text view; focus on the text.  Lines are cut with `>` and scroll
  sideways (Conventions).  `^ more above` shows once the text has scrolled.
- Close and Esc go back to Review with its fields as they were.

### Advanced: another filesystem

From Review.  `--reformat MOUNT=TYPE` (`reformat.md`).

```text
 BackOtter rescue                                                         Review

  ┌─────────────── Advanced: restore onto another filesystem ────────────────┐
  │                                                                          │
  │ A filesystem can be made as ext4, XFS or btrfs instead.  Files, owners   │
  │ and permissions stay the same; fstab and the boot setup follow.          │
  │                                                                          │
  │  ┌────────────────────────────────────────────────────────────────────┐  │
  │  │ /          ext4    ->  ext4     as it was                          │  │
  │  │ /srv       ext4    ->  XFS      changed                            │  │
  │  │ /boot      ext4    ->  ext4     can't change: see below            │  │
  │  │ /boot/efi  vfat                 can't change: see below            │  │
  │  └────────────────────────────────────────────────────────────────────┘  │
  │                                                                          │
  │ /boot: GRUB reads /boot from it; under GRUB, --reformat makes ext4       │
  │ only.                                                                    │
  │                                                                          │
  │ Changing a filesystem clears the serials typed on Review, since what     │
  │ will be written has changed.                                             │
  │                                                                          │
  │                   <  OK  >    < Undo all >    < Back >                   │
  └──────────────────────────────────────────────────────────────────────────┘

 Arrows: row | Space: next type | Tab: buttons | Enter: OK | Esc: back
```

- Focus starts on the first row that can change; the mock shows it moved
  to `/boot`.  Space cycles ext4, XFS and btrfs; Enter is OK, as
  everywhere.
- Each change runs `plan --json` with the `--reformat` flags so far.  The
  focused row's refusal, if any, is shown in full below the list, and the
  row says "can't change".  Every text is bko-holt's own, from `problems`
  (`reformat.rs`): GRUB reading the filesystem allows ext4 only (that
  includes `/` when there's no separate `/boot`); a type other than ext4,
  XFS or btrfs isn't converted (here the EFI partition's vfat); the mount
  point is that type already; a btrfs on more than one device; the same
  filesystem mounted elsewhere too; the source lacks the new type's
  fsck or repair tool; the oldest installed kernel is older than 4.19
  (for XFS); hibernation into a swap file there.
- OK goes back to Review, with its serials cleared and its "Changes:"
  updated ("/srv is made as XFS, not ext4").  < Undo all > clears them.
- On a source that isn't Linux, and on ostree, ABRoot, NixOS and Guix
  sources (whose boot setup comes from their own tools), < Advanced > isn't
  drawn, and Review's "Changes:" line says "Another filesystem: not for
  this system".
- Back discards changes made on this visit.

## 9. Restore

The argument list with `--through close --commit`, started detached;
bko-rescue follows its events file.  In the mocks, `=` stands for a
reverse-video space: the bar is reverse-video spaces in every profile
(`rescue-tui.md`), needing no block glyphs and no colour.

```text
 BackOtter rescue                                                        Restore

  ┌─────────────────────────── Restoring lovelace ───────────────────────────┐
  │                                                                          │
  │   [x] Make partitions           2 s                                      │
  │   [x] Set up encryption         9 s                                      │
  │   [x] Make filesystems          4 s                                      │
  │   [x] Ready the filesystems     1 s                                      │
  │   [>] Copy your files back      running for 21 min                       │
  │   [ ] Make it bootable                                                   │
  │   [ ] Finish                                                             │
  │                                                                          │
  │ Now: copying your files back.                                            │
  │                                                                          │
  │ [=======================================                        ] 62 %   │
  │ 38.2 of 61.2 GB   184,233 files   about 14 min left                      │
  │                                                                          │
  │ Leave the computer on, with the stick and the network cable in, until    │
  │ this says Done.  You can walk away.                                      │
  │                                                                          │
  │                                < Cancel >                                │
  └──────────────────────────────────────────────────────────────────────────┘

 l: log | Tab: to Cancel | Enter on Cancel: stop (asks first) | Esc: nothing
```

- No focus at first: Enter does nothing until Tab reaches < Cancel >, so
  pressing Enter "to continue" never asks to stop.  Esc does nothing here.
- The stage labels are plain: partition, assemble, format, mount, restore,
  boot and close are "Make partitions", "Set up encryption" (or "Set up
  RAID and volumes", or both, from what the stage does), "Make
  filesystems", "Ready the filesystems", "Copy your files back", "Make it
  bootable", "Finish".  The log keeps bko-holt's names.
- Fed by the events: `stage`, `skip`, `cmd` (the "Now" line, in plain
  words per program, with the command itself in the log), `cmd_done`,
  `progress` (the bar and figures), `stage_done`, `failed`, `end`.  Time
  left is worked out from the rate over the last minute of `progress`
  (`bytes`, `total_bytes`); the event has no time-left field.
- Without `progress` events the bar is a timer only ("running for 21
  min").  A skipped stage shows `[-]` and "nothing to do".
- `l` shows the log's last 200 lines in a text view; Esc there comes back.
- `end` with exit 0 goes to Done; `failed`, or `end` with any other exit,
  goes to Failure.  If bko-rescue restarts, it finds load still running
  and carries on here.

### Cancel?

```text
 BackOtter rescue                                                        Restore





  ┌─────────────────────────── Stop the restore? ────────────────────────────┐
  │                                                                          │
  │ Your files are being copied back.  Stop interrupts the copy now; at      │
  │ any other step, the command running is let finish first.                 │
  │                                                                          │
  │ The disks are then half written: lovelace won't start from them.  Start  │
  │ the stick again and choose lovelace: it offers to go on.                 │
  │                                                                          │
  │ Your backup is safe: nothing in it has been changed.                     │
  │                                                                          │
  │                        < Keep going >    < Stop >                        │
  └──────────────────────────────────────────────────────────────────────────┘





 Tab: buttons | Enter: press | Esc: keep going
```

- Focus starts on < Keep going >, and Esc means the same.
- < Stop > sends load a stop request: SIGTERM, which load honours between
  commands and passes to the archiver as SIGINT, so rustic-bko ends
  cleanly within seconds rather than at the end of the copy.  load has no
  such handler today; RT10 adds it.  The restore screen shows
  "Stopping..." until `end`, and Failure then says "Stopped by you"
  instead of an error.
- The copy runs again in full on a resume (see "Resume offer").

## 10a. Done

```text
 BackOtter rescue                                                           Done
  ┌────────────────────────── lovelace is restored ──────────────────────────┐
  │                                                                          │
  │   [x] Make partitions           [x] Set up encryption                    │
  │   [x] Make filesystems          [x] Ready the filesystems                │
  │   [x] Copy your files back      [x] Make it bootable                     │
  │   [x] Finish                                       in 36 min             │
  │                                                                          │
  │ At the next start, type the passphrase you chose for the disks.          │
  │                                                                          │
  │ After the first start:                                                   │
  │   - To have the disks unlock by themselves again (TPM2), run as root:    │
  │       systemd-cryptenroll --tpm2-device=auto /dev/nvme0n1p3              │
  │     Until you do, the computer asks for the passphrase at each start.    │
  │   - /srv is XFS now: check anything that relied on ext4 there.           │
  │                                                                          │
  │ The log is on the stick, in lovelace's folder.                           │
  │                                                                          │
  │ Remove the stick, then press Enter to restart into lovelace.             │
  │                                                                          │
  │                < Restart >    < Shell >    < Power off >                 │
  └──────────────────────────────────────────────────────────────────────────┘

 Tab: buttons | Enter: press | s: shell
```

- Focus starts on < Restart >.  Each "after the first start" line comes
  from `tokens` and `notes` (TPM2 or FIDO2 to enrol again, a renamed pool,
  a reformat, a new UUID).
- The log is written to the config partition (read-write only for this,
  then flushed and read-only again), as
  `backotter/machines/<install id>/restore-logs/<time>.log.gz`, and to the
  repository if reachable.
- If the stick is still in when < Restart > is pressed, a message says
  "Remove the stick first, or the computer may start from it again", with
  < Restart anyway >.
- No Back: nothing is left to go back to.

## 10b. Failure

```text
 BackOtter rescue                                                        Failure

  ┌─────────── Problem: the restore stopped at "Make it bootable" ───────────┐
  │                                                                          │
  │   [x] Make partitions           [x] Set up encryption                    │
  │   [x] Make filesystems          [x] Ready the filesystems                │
  │   [x] Copy your files back      [!] Make it bootable                     │
  │   [ ] Finish                                                             │
  │                                                                          │
  │ grub-install failed (exit status 1):                                     │
  │   grub-install: error: efibootmgr failed to register the boot entry:     │
  │   No such file or directory.                                             │
  │                                                                          │
  │ Your backup is safe: nothing in it has been changed.                     │
  │                                                                          │
  │ The log is on the stick, in lovelace's folder:                           │
  │   backotter/machines/0b7e3c52-8d1f-4a6e-9c3b-2f5d7a1e4b90/               │
  │     restore-logs/20261123T141812Z.log.gz                                 │
  │                                                                          │
  │      < Help >  < Try again >  < Shell >  < Report >  < Power off >       │
  └──────────────────────────────────────────────────────────────────────────┘


 Tab: buttons | Enter: press | h: help | r: report | s: shell
```

- Focus starts on < Help >, which opens the stage's help screen (below).
- Fed by `failed` (stage, text, log) and `end`.  The stage's error is
  shown in full: in a text view if it's longer than the space here.
- < Try again > runs `status --json`, then `--only STAGE --commit` per
  stage from `next` to `close`.  < Shell > leaves the disks as they are,
  mounted at `/mnt/bko-target`, for a look or `bko-holt load` by hand.
  < Report > runs the bug report (below).
- No Back.

## Problems and their help

Every problem has a help screen: what happened, why, and what to do, in
plain words, the "backup is safe" line, and < Raw output > (`o`), which
shows the program's own text in a text view.  A fix is offered only where
it's known and safe, only as a button the user presses, and never with the
focus on it at first.  Otherwise the help screen offers Try again, Shell
and Report.

The codes come from two places.  bko-holt's are for `holt-json.md` (D12):

- `too_small`: a candidate's refusal, with how much smaller.  Fix: shrink
  the system partition (the offer up to 10 %, the decision screen beyond
  that), when the data fits, or another disk.
- `data_wont_fit` (new): a smaller candidate whose largest partition
  can't hold its data, with the figures: the space used at the backup,
  what it needs with the margin, and the most it could get.  No fix but
  another disk.
- `target_too_small` (new): a plan problem, a disk given by `--map` that
  the layout doesn't fit.  Fix: the same.
- `not_mapped`: a plan problem.  Fix: back to Disks.
- `key_needed`: a plan problem.  Fix: back to Passphrases.
- The reformat refusals: plan problems with `--reformat`.  No fix: shown
  on Advanced, where another type can be chosen.
- `wrong_passphrase`: not bko-holt's code but bko-rescue's reading of a
  `failed` event in `assemble` from `cryptsetup open` (plan opens no
  containers, so it can't know).  Fix: ask for the passphrase again.
- `stage_failed`: any other `failed` event.  Fix: per program where one
  is known (below), else Try again, Shell, Report.
- A code bko-rescue doesn't know: its text is shown and the restore is
  blocked.  Try again, Shell, Report.

bko-rescue's own, from its `rustic-bko snapshots` and sftp runs:

- `repo_unreachable`: no route to the server.  Fix: back to Network.
- `repo_not_found`: the server answers, but there's no repository at that
  address.  Fix: Type a location.
- `repo_login_failed`: the sftp login was refused, or the server's key
  isn't the one the stick has.  Fix: Type a location, or Shell.
- `wrong_password`: rustic-bko's "wrong password".  Fix: ask again, with
  the hint from the second wrong one on.
- `repo_id_mismatch`: the repository's ID isn't the stick's
  (`stick-config.md`, "Which copy wins").  Its own help screen: these
  aren't the backups the stick knows for this machine.  Back to Machine,
  Type a location, Shell, Report; never a silent switch.
- `repo_error`: anything else rustic-bko reports (a damaged repository, a
  crash).  Try again, Shell, Report.

### With a fix: the disk is a little smaller

```text
 BackOtter rescue                                                          Disks



  ┌───────────────────── This disk is a little smaller ──────────────────────┐
  │                                                                          │
  │ Old disk 1 (Samsung SSD 870 EVO 500GB) was 500 GB.  The disk you chose,  │
  │ KINGSTON SA400S37480G, is 480 GB: 20 GB smaller, so the partitions as    │
  │ they were don't fit.                                                     │
  │                                                                          │
  │ The system partition can be made smaller to fit.  It is shown as /, and  │
  │ holds the operating system and the home folders.                         │
  │   / will shrink from 465 GB to 445 GB, by 4.3 %.                         │
  │   Space used on /: 41.7 GB, so it fits with room to spare.               │
  │   The other partitions stay as they were.                                │
  │                                                                          │
  │ Or choose another disk.                                                  │
  │                                                                          │
  │    < Shrink the system partition >  < Another disk >  < Raw output >     │
  └──────────────────────────────────────────────────────────────────────────┘



 Tab: buttons | Enter: press | o: raw output | Esc: back to the disk list
```

- Focus starts on < Another disk >: a fix is never the default.
- Fed by the `too_small` refusal (how much smaller), and by a second
  `plan --json` with `--fit-largest PCT`, PCT being the smallest whole
  percentage that fits (here 5; the partition shrinks by 4.3 %).  Its
  notes give the new sizes and its problems say whether the files fit.
- The offer is made up to 10 %, when the data fits (decided 2026-11-24).
  Beyond 10 % the decision screen below is shown instead, when the data
  fits with the margin.  When it doesn't fit, the disk can't be chosen,
  however small the shrink ("Too small for the data").  bko-holt's own
  limit of 50 % (`FIT_MAX`, `load/mod.rs`) is a code limit, not a safety
  rule: a shrink beyond it waits for bko-holt ("What the screens need
  from bko-holt").  When bko-holt can't fit the layout at all (fit's
  notes give the reason, for the layouts it doesn't resize), there's no
  shrink button, and the text gives that reason in words.
- `--fit-largest` applies to every mapped disk: on another disk a little
  bigger, its largest partition would grow by up to the same percentage.
  The screen adds a line when that happens ("On CT1000P3PSSD8, / grows
  from 465 GB to 488 GB"), and Review lists it under "Changes:".
- < Shrink the system partition > keeps the mapping with the flag; Review
  then says "/ (the system partition) shrinks from 465 GB to 445 GB".  The
  grow offer for a disk a little bigger is the same screen, worded "This
  disk is a little bigger" and "/ will grow".
- Esc and < Another disk > go to the disk's list.

### A bigger difference: the user decides

A variant: old disk 1 put on a 320 GB disk, a WDC WD3200AAKX.  The
system partition would lose 38.7 %, more than the offer's 10 %, and the
41.7 GB it uses fits in 285 GB with room to spare.

```text
 BackOtter rescue                                                          Disks
  ┌───────────────── This disk is much smaller: you decide ──────────────────┐
  │                                                                          │
  │ Old disk 1 (Samsung SSD 870 EVO 500GB) was 500 GB.  The disk you chose,  │
  │ WDC WD3200AAKX, is 320 GB: 180 GB smaller.                               │
  │                                                                          │
  │ To fit, the system partition (/) would shrink by 38.7 %:                 │
  │   Old size:    465 GB                                                    │
  │   New size:    285 GB                                                    │
  │   Space used:  41.7 GB at the backup, so it fits with room to spare      │
  │ The other partitions stay as they were.                                  │
  │                                                                          │
  │ This stick offers a shrink of up to 10 % with a button.  This is more,   │
  │ so you decide, and nothing is chosen for you.                            │
  │                                                                          │
  │  ┌────────────────────────────────────────────────────────────────────┐  │
  │  │ ( ) Shrink / to 285 GB and restore onto WDC WD3200AAKX             │  │
  │  │ ( ) Choose another disk                                            │  │
  │  └────────────────────────────────────────────────────────────────────┘  │
  │                                                                          │
  │                  <  OK  >    < Raw output >    < Back >                  │
  └──────────────────────────────────────────────────────────────────────────┘

 Arrows: choose | Tab: buttons | Enter: OK | o: raw output | Esc: back
```

- No row is chosen at first, as on Machine: OK says "Choose one first"
  until an arrow moves the focus onto a row.  Neither answer is the
  default, and Enter before an arrow has moved the focus does nothing.
- The sizes are stated plainly: the old and new disk, the partition's old
  and new size, the percentage, and the space used at the backup.  There
  is no upper limit on the percentage: the data decides.  A disk the data
  wouldn't fit never gets this screen ("Too small for the data").
- Fed as the offer is: `too_small`, then `plan --json` with
  `--fit-largest 39`, the smallest whole percentage that fits.
- `--fit-largest` applies to every mapped disk, so a disk a little bigger
  elsewhere would grow by up to 39 %: the body adds that line, as the
  offer does.
- Shrink keeps the mapping with the flag; Review says "/ shrinks by
  38.7 %, from 465 GB to 285 GB, as you chose".  Choose another disk,
  Back and Esc go to the disk's list.
- Until bko-holt shrinks by more than 50 % (`FIT_MAX`, a code limit, not
  the rule), a shrink beyond that whose data fits can't be planned yet:
  the row says "much smaller", and this screen still states the sizes
  and the space used, says bko-holt can't shrink this far yet, and has
  only the Choose another disk row.

### A stage's command failed

```text
 BackOtter rescue                                                        Failure



  ┌───────────── Problem: the boot loader couldn't be installed ─────────────┐
  │                                                                          │
  │ What happened: your files are all back, but grub-install couldn't add    │
  │ lovelace to this computer's list of things to start (its boot menu).     │
  │                                                                          │
  │ Why: the stick was started in UEFI mode, so the list can be written,     │
  │ but this computer refused the new entry.  Some computers do.             │
  │                                                                          │
  │ What can be done: put the boot loader where every UEFI computer looks    │
  │ for it by itself, with no entry in the list.  It starts the same way.    │
  │                                                                          │
  │ Your backup is safe: nothing in it has been changed.                     │
  │                                                                          │
  │   < Install without an entry >  < Shell >  < Report >  < Raw output >    │
  └──────────────────────────────────────────────────────────────────────────┘




 Tab: buttons | Enter: press | s: shell | r: report | o: raw output | Esc: back
```

- Focus starts on < Shell >: the fix is offered, not chosen.  Esc goes
  back to Failure.
- < Install without an entry > runs `--only boot --removable --no-nvram
  --commit`, then `--only close --commit`, after the user's OK in a
  yes/no box that says what it does.
- The text is chosen by the stage, the program and what bko-rescue sees:
  started in UEFI mode or not (`/sys/firmware/efi`, or each system's own
  test).  Started in BIOS mode, it says instead: "Why: the stick was
  started in the older BIOS mode, where that list can't be written.  What
  to do: restart, and press F12, F11, F8 or Esc while the maker's logo
  shows.  Pick the stick's entry with UEFI in its name.  You will be asked
  again for the machine, the keyboard, the backup password, the backup,
  the disks and the passphrase; then bko-rescue offers to go on."  It
  promises the files aren't copied again only once load marks the copy
  complete (RT10); until then it says "the files are copied again".
- An unknown failure gets a general text ("a command bko-holt runs
  failed"), the command's output, and Try again, Shell and Report.
- `o` shows the command line, its exit status and its last output, from
  the `failed` event and the log.
- Nothing here changes the disks without the user's OK.

## The bug report

`r` or < Report > on Failure or any help screen.  It runs `bko-holt
report-bug --output FILE` (redacted by default), saves the file to the
stick's config partition, flushes, then says where it is.

```text
 BackOtter rescue                                                         Report
  ┌────────────────────────────── Report saved ──────────────────────────────┐
  │ ░░░░░░░░░░░░░░░░░░░░░░░░░░░░░                                            │
  │ ░░░░░░░░░░░░░░░░░░░░░░░░░░░░░   The report is on this stick, in its      │
  │ ░░░░░░░░░░░░░░░░░░░░░░░░░░░░░   BACKOTTER partition:                     │
  │ ░░░░░░░░░░░░░░░░░░░░░░░░░░░░░     backotter/reports/                     │
  │ ░░░░░░░░░░░░░░░░░░░░░░░░░░░░░       bko-report-20261123-142630.txt       │
  │ ░░░                       ░░░                                            │
  │ ░░░      QR CODE          ░░░   Plug the stick into another computer,    │
  │ ░░░    placeholder        ░░░   open an issue at                         │
  │ ░░░  version 2, 29 x 15   ░░░     github.com/mchobbit/backotter/issues   │
  │ ░░░                       ░░░   and attach the file, or scan the code    │
  │ ░░░░░░░░░░░░░░░░░░░░░░░░░░░░░   with a phone to open that page.          │
  │ ░░░░░░░░░░░░░░░░░░░░░░░░░░░░░                                            │
  │ ░░░░░░░░░░░░░░░░░░░░░░░░░░░░░   You need a free GitHub account.  If      │
  │ ░░░░░░░░░░░░░░░░░░░░░░░░░░░░░   you have none, send the file to          │
  │ ░░░░░░░░░░░░░░░░░░░░░░░░░░░░░   whoever set up your backups.             │
  │                                                                          │
  │ No passwords or keys are in it.  The hostname, serials, UUIDs and user   │
  │ names are replaced by stand-ins.  Advanced makes one that keeps them.    │
  │                                                                          │
  │                 <  OK  >    < View it >    < Advanced >                  │
  └──────────────────────────────────────────────────────────────────────────┘
 Tab: buttons | Enter: press | v: view the report | Esc: back
```

- Focus starts on OK, which goes back to the screen that asked.
- The file goes to `/backotter/reports/bko-report-YYYYMMDD-HHMMSS.txt`
  (UTC), report-bug's own default name, on the config partition, mounted
  read-write for this only, then `fsync` and a read-only remount.
- The QR code holds the issue URL in upper case
  (`HTTPS://GITHUB.COM/MCHOBBIT/BACKOTTER/ISSUES`), since a version 2 code
  holds upper case only in alphanumeric mode; the text shows it in lower
  case, as people type it.  Both open the same page (to be tested: open
  question 4).  bko-holt's own link (`ISSUES`, `/issues/new/choose`) is to
  change to `/issues` to match.
- The code is drawn with `▀▄█` where the profile has half-blocks (UTF-8
  consoles); elsewhere the box is the text alone, full width.
- No config partition: it goes to the backup drive when that's a local
  disk (`report/` beside the repository), else it stays in RAM at
  `/run/bko-rescue/` and the screen says "It's in this computer's memory
  only: it's lost when the computer is turned off", with < Shell > to
  copy it somewhere.
- < View it > opens the text view on the file, so the user sees what
  would be shared.
- < Advanced > opens the screen below.  It has no letter, so it is never
  pressed by a stray key.  The redacted report stays the default: it is
  made first, every time, and is the only one the QR code and the issue
  link are for.
- Nothing is sent from the stick: it only saves files.

### Advanced: a report that isn't redacted

```text
 BackOtter rescue                                                         Report
  ┌───────────────── Advanced: a report that isn't redacted ─────────────────┐
  │                                                                          │
  │ The report you saved replaces these with stand-ins.  This one keeps      │
  │ them as they are:                                                        │
  │  ┌────────────────────────────────────────────────────────────────────┐  │
  │  │ lovelace's hostname and machine-id                                 │  │
  │  │ disk serials and WWIDs, such as 2318E6F3A9C1                       │  │
  │  │ the UUIDs and GUIDs of disks, partitions and filesystems           │  │
  │  │ partition and filesystem labels, such as "photos"                  │  │
  │  │ user names, and the MAC addresses of network ports                 │  │
  │  │ ZFS GUIDs, hostids, disk signatures and FAT serials                │  │
  │  │ the backup's address (sftp://nas.local/...) and key-file paths     │  │
  │  └────────────────────────────────────────────────────────────────────┘  │
  │ Neither report holds a password, a passphrase or a key.                  │
  │                                                                          │
  │ GitHub issues are public: anyone can read them.  Don't attach this       │
  │ report to an issue.  Share it privately: with whoever set up your        │
  │ backups, or with the developer if they ask for it.                       │
  │ To make it, type YES:  [     ]                                           │
  │                                                                          │
  │                         < Make it >    < Back >                          │
  └──────────────────────────────────────────────────────────────────────────┘
 Type YES | Tab: buttons | Enter: make it, once YES is typed | Esc: back
```

- Focus starts in the field.  < Make it > and Enter do nothing until the
  field holds YES (either case); until then they say "Type YES to make
  it, or Esc to go back".  So the unredacted report is never made by
  Enter alone.
- The list is what `redact.rs` hides (its `Kind`s): hostnames,
  machine-ids, UUIDs and GUIDs, serials and WWIDs, labels, user names,
  MAC addresses, other identifiers, key-file paths, and the repository
  and password-file paths.  The stand-ins are listed by kind, with the
  examples taken from this machine.
- It runs `bko-holt report-bug --no-redact --output FILE`, with FILE the
  redacted report's name plus `-UNREDACTED`, beside it:
  `backotter/reports/bko-report-20261123-142630-UNREDACTED.txt`.  The
  file's own first line already says "NOT redacted".
- Then "Report saved" is shown again, naming both files, without the QR
  code, and with "The UNREDACTED file is for sharing privately: don't
  attach it to an issue" in place of the GitHub lines.
- The stick's BACKOTTER partition already holds these identifiers (each
  machine's `system.json` has them), so the file adds no new kind of
  data to the stick.  It is kept with the other reports (open question 9).
- Back and Esc go to "Report saved" with nothing made.

## The ASCII fallback

The Restore screen as it looks on DragonFly's syscons, illumos in VGA
text mode, every serial console (`vt100`, `vt220`) and ssh with a locale
that isn't UTF-8.  Box characters become `+`, `-` and `|`; the bar stays
reverse-video spaces (`=` here, as above).  NetBSD and OpenBSD wscons draw
the UTF-8 lines with DEC special graphics instead.  On a serial console
colour is off unless asked for (D3).

```text
 BackOtter rescue                                                        Restore

  +--------------------------- Restoring lovelace ---------------------------+
  |                                                                          |
  |   [x] Make partitions           2 s                                      |
  |   [x] Set up encryption         9 s                                      |
  |   [x] Make filesystems          4 s                                      |
  |   [x] Ready the filesystems     1 s                                      |
  |   [>] Copy your files back      running for 21 min                       |
  |   [ ] Make it bootable                                                   |
  |   [ ] Finish                                                             |
  |                                                                          |
  | Now: copying your files back.                                            |
  |                                                                          |
  | [=======================================                        ] 62 %   |
  | 38.2 of 61.2 GB   184,233 files   about 14 min left                      |
  |                                                                          |
  | Leave the computer on, with the stick and the network cable in, until    |
  | this says Done.  You can walk away.                                      |
  |                                                                          |
  |                                < Cancel >                                |
  +--------------------------------------------------------------------------+

 l: log | Tab: to Cancel | Enter on Cancel: stop (asks first) | Esc: nothing
```

- Focus, keys and events as on the UTF-8 screen; only the glyphs differ.
- The byte budget, with the figures line counted: a second's update is
  the bar's changed cells (a move and one attribute run, under 20 bytes)
  plus the figures' changed runs (the GB, the file count, now and then the
  percentage and time left: three or four runs of a move and a few
  digits, 30 to 50 bytes).  That's 50 to 70 bytes a second, over the
  40-byte gauge step of `rescue-tui.md`'s "Byte budget", which counts the
  bar alone, but 5 to 7 % of a 9600-baud line.  On DragonFly's 9600-baud
  console the figures are updated every 5 s, the bar every second.
- The QR code is never drawn in ASCII; the report screen shows its text
  full width.

## Plain mode

`--plain` (from the boot menu's plain entry, `P` on the first screen, or
`p` on the too-small notice): the same questions, one line at a time, as
`rescue-tui.md` ("Plain mode") draws them, for a screen reader or a serial
log.  No escape sequences; nothing rewrites a line.  Choices are numbered
or lettered, and every screen ends with a `Choose [...]:` prompt.  The
transcripts below show the screens that lean most on looks.

The keyboard offer and the password, with two wrong tries.  Nothing typed
for the password is echoed.  An empty line shows the other choices, so no
password can be mistaken for a command.

```text
Keyboard for the password
lovelace used a German keyboard (de, nodeadkeys) when it was backed up.
This stick is set to English (US).
  1) switch to German for the password   2) keep English (US)
  3) another layout
Choose [1-3]: 1
Keyboard set to German (de, nodeadkeys).
The backup password
lovelace's backups are kept at sftp://nas.local/backotter/lovelace.
Type the backup password, or press Enter alone for other choices:
That password didn't open the backups.  Check Caps Lock and the keyboard.
Type the backup password, or press Enter alone for other choices:
That password didn't open the backups either.  Tries so far: 2.
Your hint, saved when the backups were set up:
  "the boat's name, then the year we moved"
Type the backup password, or press Enter alone for other choices:
  k) keyboard   r) use the recovery key instead   b) back
  t) type the password again
Choose [k, r, b, t]:
```

Disks:

```text
BackOtter rescue (plain mode)
Disks
lovelace had 2 disks.
Old disk 1: Samsung SSD 870 EVO 500GB, 500 GB, the system.
  Goes to: CT1000P3PSSD8, 1.00 TB, serial 2318E6F3A9C1.  Chosen by you.
  It will be ERASED, then filled from the backup.
Old disk 2: WDC WD20EFRX-68EUZN0, 2.00 TB, /srv.
  Goes to: the same disk, found by its serial WD-WCC4M5XK7HPL.
  It will be ERASED.  Anything saved on it after 22 Nov 2026 is lost.
Not used: KINGSTON SA400S37480G, 480 GB, serial 50026B7782A1F3C4.
  It has data: 1 partition, ext4 "photos", 312 GB used.
Not offered: SanDisk Ultra, 32 GB: this stick.
  1) change where old disk 1 goes   2) change where old disk 2 goes
  o) these are right   b) back   s) shell
Choose [1-2, o, b, s]:
```

Review: each erased disk is read out with its serial spelled one character
at a time, so a screen reader says each letter, then the last six are
asked for one disk at a time.

```text
Review
Restore lovelace (Debian 13) from the backup of 22 Nov 2026 02:00 CET.
Backed up from a different computer.
Nothing has been changed on any disk yet.
These disks will be ERASED, then filled from the backup:
  Disk 1: CT1000P3PSSD8, 1.00 TB, serial 2 3 1 8 E 6 F 3 A 9 C 1
  Disk 2: WDC WD20EFRX-68EUZN0, 2.00 TB,
          serial W D - W C C 4 M 5 X K 7 H P L
Disk 2 is lovelace's own /srv disk: anything saved on it after
22 Nov 2026 is lost.
Changes: none.  No problems found.
Type the last six characters of disk 1's serial, or b to go back: f3a9c1
Disk 1 confirmed.
Type the last six characters of disk 2's serial, or b to go back: xk7hpl
Disk 2 confirmed.
  r) restore now   c) show every command   a) advanced   b) back
Choose [r, c, a, b]:
```

Recovery key: one line of 12 words, or of their first four letters, then
the words read back numbered.

```text
Recovery key
Type the 12 words from the recovery sheet on one line, with spaces.
The first four letters of each word are enough.
> harv orbi velv cand nobl tige quan elbo ribb zone kitc moun
You typed:
  1 harvest    2 orbit      3 velvet     4 candle
  5 noble      6 tiger      7 quantum    8 elbow
  9 ribbon    10 zone      11 kitchen   12 mountain
  y) these are right, try them   n) type them again   b) back
Choose [y, n, b]:
```

A word that can't be on the list is named ("Word 7, quax, isn't the start
of any recovery word"), and a failed checksum says "The 12 words don't add
up: one is wrong, or two are swapped".

Restore: a line per stage, and a percentage line at most every 10 % or
60 s.

```text
Restore
Restoring lovelace.  Leave the computer on, with the stick and the
network cable in, until this says Done.
[x] Make partitions: done in 2 s
[x] Set up encryption: done in 9 s
[x] Make filesystems: done in 4 s
[x] Ready the filesystems: done in 1 s
[>] Copy your files back: started
    10 %: 6.1 of 61.2 GB, about 32 min left
    20 %: 12.3 of 61.2 GB, about 27 min left
    30 %: 18.4 of 61.2 GB, about 24 min left
Type c and Enter to stop (it asks first), or l and Enter for the log.
```

Failure and its help:

```text
Problem: the restore stopped at "Make it bootable".
grub-install failed (exit status 1): efibootmgr failed to register the
boot entry: No such file or directory.
Your backup is safe: nothing in it has been changed.
  h) help   t) try again   s) shell   r) report   p) power off
Choose [h, t, s, r, p]: h
What happened: your files are all back, but grub-install couldn't add
lovelace to this computer's list of things to start.
Why: this computer refused the new entry.  Some computers do.
What can be done: put the boot loader where every UEFI computer looks
for it by itself, with no entry in the list.
  1) install without an entry (asks first)   o) raw output
  s) shell   r) report   b) back
Choose [1, o, s, r, b]: r
```

A disk much smaller (the decision screen).  Nothing is the default: an
empty line asks again.

```text
This disk is much smaller: you decide
Old disk 1 (Samsung SSD 870 EVO 500GB) was 500 GB.  The disk you chose,
WDC WD3200AAKX, is 320 GB: 180 GB smaller.
To fit, the system partition (/) would shrink by 38.7 %:
  old size 465 GB, new size 285 GB, space used 41.7 GB at the backup.
It fits with room to spare.  That is more than the 10 % this stick offers
by itself, so you decide.
  1) shrink / to 285 GB and restore onto WDC WD3200AAKX
  2) choose another disk   o) raw output   b) back
Choose [1, 2, o, b]:
Choose [1, 2, o, b]: 1
/ will shrink by 38.7 %, as you chose.
```

A disk much bigger.  An empty line keeps the old sizes.

```text
This disk is much bigger
Old disk 1 (Samsung SSD 870 EVO 500GB) was 500 GB.  The disk you chose,
CT1000P3PSSD8, is 1.00 TB: 500 GB bigger.
  1) keep the old sizes, and leave 500 GB free
  2) grow / from 465 GB to 965 GB, to fill the disk
  o) raw output   b) back
Choose [1, 2, o, b]:
The old sizes are kept.  500 GB stays free.
```

A disk the data doesn't fit, on the disk list ("Too small for the data").

```text
Old disk 1 of 2: where does it go?
Old disk 1: Samsung SSD 870 EVO 500GB, 500 GB, the system.
Put it on this disk of this computer.  The disk you pick is ERASED.
  1) WDC WD3200AAKX, 320 GB, serial WD-WCAV2K318842: much smaller
  2) TS64GSSD370S, 64 GB, serial F431270087: too small
  b) back
Choose [1-2, b]: 2
/ holds 41.7 GB; the most it could get on this disk is 29 GB.
It needs 48 GB, with room to spare.  Choose another disk.
Choose [1-2, b]:
```

The report, then its Advanced form:

```text
Report saved on this stick, in its BACKOTTER partition, as
backotter/reports/bko-report-20261123-142630.txt
Open an issue at https://github.com/mchobbit/backotter/issues
(github dot com, slash mchobbit, slash backotter, slash issues)
and attach the file.  You need a free GitHub account; without one, send
the file to whoever set up your backups.
  o) OK   v) view it   a) advanced: a report that isn't redacted
Choose [o, v, a]: a
A report that isn't redacted keeps lovelace's hostname and machine-id,
disk serials, UUIDs, labels, user names, MAC addresses, other IDs, the
backup's address and key-file paths.  It holds no password or key.
GitHub issues are public: don't attach it to an issue.  Share it
privately, with whoever set up your backups or the developer.
To make it, type YES, or press Enter alone to go back: yes
Saved beside the first, as
backotter/reports/bko-report-20261123-142630-UNREDACTED.txt
```

## What the screens need from bko-holt

For RT10 and `holt-json.md` (D12), each named above where it's used:

1. **A stop request.**  SIGTERM to load, honoured between commands and
   passed to the archiver as SIGINT; `end` then says it was stopped.
2. **A copy-complete mark.**  load writes it (on the target or in its run
   directory) only when the archiver exits 0, and `status` checks it
   instead of `/etc/fstab` and `/etc/os-release`.
3. **Safe re-runs of `assemble` and `mount`** on disks bko-holt has already
   partitioned: open what exists, never format it again.  To be confirmed
   in `layload.md`'s stages, or added.
4. **Codes**: `target_too_small` as a plan problem, `data_wont_fit` with
   its figures (8 below), and the codes above in `problems`; whether a
   group's `keys` entry can take a new passphrase (false when the header
   was kept).
5. **`ISSUES`** in `bugreport.rs` changes to `/issues`, to match the QR
   code.  `report-bug --no-redact` exists already; its first line ("NOT
   redacted: it holds this machine's hostnames, UUIDs, serials and
   labels") is to name every kind the Advanced screen lists.
6. **The keyboard layout, in the dump.**  A `keyboard` section in
   `system.json`, so the stick can read it before any password.  Today the
   dump records none: `holt/src` has no read of `vconsole.conf`,
   `/etc/default/keyboard` or any other keymap file, only test fixtures
   in `scenarios.rs`.  The shape, for lovelace:

   ```text
   "keyboard": {
     "console": {"source": "/etc/default/keyboard", "native": null,
                 "cmdline": null},
     "boot_prompt": {"where": "initramfs", "builder": "initramfs-tools",
                     "native": null, "us_fallback": true,
                     "why": "initramfs.conf KEYMAP=n"},
     "xkb": {"source": "/etc/default/keyboard", "model": "pc105",
             "layout": "de", "variant": "nodeadkeys", "options": null},
     "desktop": [{"source": "kxkbrc", "layouts": [
       {"layout": "de", "variant": "nodeadkeys"},
       {"layout": "us", "variant": null}]}],
     "at_setup": null,
     "canonical": {"layout": "de", "variant": "nodeadkeys",
                   "from": "xkb"},
     "notes": ["boot prompt uses US: initramfs.conf KEYMAP=n"]
   }
   ```

   - `console`: what the console loads at boot, in the platform's own
     name (`native`), and a keymap argument on the kernel command line.
     On Debian the console map is compiled from the XKB values by
     `setupcon`, so `native` is null and `xkb` holds the layout.
   - `boot_prompt`: where an encrypted root's passphrase is typed
     (`initramfs`, `grub`, `loader`, `boot`, `plymouth`, `none`), by which
     builder, with which map, and `us_fallback` when no map is loaded
     there.
   - `xkb`: the system default; `desktop`: the users' own layouts, in
     order, deduplicated and with no user names (`system.json` is readable
     before the password, `stick-config.md`).  A `/home` that isn't
     mounted or is encrypted gives `desktop: null`, not an error.
   - `at_setup`: filled once by the set-up UI, not the dump: the layout
     active when the repository password was typed, or `"ssh"`.  It is
     the best guess for that password, and needs its own hook in set-up.
   - `canonical`: the dump's own XKB (layout, variant), using systemd's
     `kbd-model-map` rules for console names.  The stick maps it to its
     platform's names (open question 11).
   - Every field may be null.  The schema version goes up; a stick that
     doesn't know the section uses `us`, as today.

   Where each system keeps it:

   - systemd Linux: `/etc/vconsole.conf` `KEYMAP=` (and `XKBLAYOUT=`
     and the like in newer systemd), `/etc/X11/xorg.conf.d/
     00-keyboard.conf`, `vconsole.keymap=` on the command line
     (vconsole.conf(5), localectl(1)).  The prompt: dracut's i18n
     module, mkinitcpio's `keymap` or `sd-vconsole` hook, booster's
     `vconsole: true` (dracut.cmdline(7), mkinitcpio(8)).
   - Debian family and Chimera: `/etc/default/keyboard` (keyboard(5));
     the prompt has it only with `KEYMAP=y` (initramfs.conf(5)).
   - OpenRC: `/etc/conf.d/keymaps`; Alpine `/etc/conf.d/loadkmap` (a
     `.bmap.gz` path, the name read from the file name).  Void
     `/etc/rc.conf` `KEYMAP=`; Slackware `/etc/rc.d/rc.keymap`.  NixOS
     through its generated `vconsole.conf`; Guix's `keyboard-layout` in
     `config.scm` (where it lands at run time is unchecked).
   - Desktops, per user: KDE `~/.config/kxkbrc`, GNOME and the like
     `org.gnome.desktop.input-sources` in dconf (a binary GVDB file),
     Xfce's xfconf `keyboard-layout.xml`, sway and Hyprland configs,
     COSMIC's `xkb_config`.
   - FreeBSD `/etc/rc.conf` `keymap=` (vt names, `de.noacc.kbd`;
     rc.conf(5), kbdcontrol(1)); DragonFly the same with syscons names
     (`german.iso`); NetBSD `/etc/wscons.conf` `encoding`
     (wscons.conf(5)); OpenBSD `/etc/kbdtype` (kbd(8)); illumos the
     `svc:/system/keymap:default` property `keymap/layout`, or `eeprom
     keyboard-layout` (kbd(1), unchecked on a guest); Haiku
     `~/config/settings/Key_map` (binary).
   - US by nature: FreeBSD's loader (GELI), OpenBSD's boot (softraid),
     GRUB's prompt without a GRUB keymap.
7. **The time zone, in the dump.**  A `time` section beside `keyboard`.
   Today the dump records only `storage.sys_tz_minuteswest` (the kernel's
   `sys_tz`, `sys.rs`), which is Linux only, gives minutes and no name,
   and is for FAT's `time_offset`; it stays, for that.  The shape:

   ```text
   "time": {
     "zone": "Europe/Berlin", "zone_from": "symlink",
     "zone_file": "/etc/localtime",
     "posix_rule": "CET-1CEST,M3.5.0,M10.5.0/3",
     "utc_offset_seconds": 3600, "abbreviation": "CET",
     "measured_at": "2026-11-22T01:00:00Z",
     "rtc": "utc", "rtc_from": "/etc/adjtime"
   }
   ```

   - `zone`: an Olson name, or null.  `zone_from`: `symlink`,
     `hint-file`, `content-match` (a copied `/etc/localtime` compared
     with the zoneinfo tree, preferring `zone1970.tab` names) or `none`.
   - `posix_rule`: the TZif file's last line, so the stick can work out
     DST without zoneinfo.  `utc_offset_seconds` and `abbreviation`: at
     backup time, the last resort only (wrong across a DST change).
   - `rtc`: `utc`, `local` or `unknown`, from the files below, never from
     `sys_tz`.
   - Linux: the `/etc/localtime` link target after `zoneinfo/`
     (localtime(5)); hints `/etc/timezone`, Slackware's
     `/etc/localtime-copied-from`; RTC `/etc/adjtime` (hwclock(8)), Void
     `HARDWARECLOCK`, OpenRC `/etc/conf.d/hwclock`.  FreeBSD and
     DragonFly: `/var/db/zoneinfo` (tzsetup(8)) and `/etc/wall_cmos_clock`
     (adjkerntz(8)).  NetBSD and OpenBSD: the link, `rtclocaltime` or
     `kern.utc_offset`.  illumos: `TZ=` in `/etc/default/init` (init(8))
     and `/etc/rtc_config` (rtc(8)).  Haiku: `~/config/settings/Time
     settings` (low confidence; check Haiku's source first).
   - rustic-bko already stores each snapshot's time with its offset, so
     the stick can show that with no zone at all.  bko-holt must not run
     rustic-bko under `TZ=UTC0`; only the test rigs do.
8. **A disk of another size** (decided 2026-11-24; "One disk", "Too
   small for the data", "A much bigger disk: keep or grow"):
   - Growing beyond 50 %.  `--fit-largest` takes 1 to 50 (`FIT_MAX` in
     `load/mod.rs`, checked when the option is read), and fit grows the
     largest partition by at most that share of it (`cap` in `fit.rs`),
     so 465 GB reaches 697 GB, never 965 GB.  Either allow more, or add a
     separate grow-to-fill option, such as `--grow-largest DISK`.
   - Per disk, if it can be.  `--fit-largest` applies to every mapped
     disk, so a shrink on one disk grows the largest partition of another
     bigger one.  The screens say so; an option per disk would end it.
   - A shrink checked by space used, not by a percentage cap: any shrink
     whose data fits with the margin.  Or `FIT_MAX` raised (to 99), with
     fit's data check (`data_fits`) deciding.  That check is there today:
     the shrink and max(1 GiB, 5 % of the space used) must be free
     (`SPARE_BYTES`, `SPARE_PCT`), and fit refuses in words.
   - A refusal code with the figures, `data_wont_fit`: the partition, the
     space used, what it needs with the margin and the most it could get,
     in bytes, so the stick says "/ holds 412 GB; the most it could get on
     this disk is 380 GB" without reading fit's sentence.  The most is
     wanted for a smaller disk without the flag too, in `plan --json`, so
     the list can say it before a second plan.
   - Space used for every supported file system and platform.  Today only
     Linux records it (statvfs, `report.rs`, `mounts.rs`).  The BSDs,
     illumos and Haiku record mounts with no size (`holt/src/other/`);
     ZFS's pool `allocated` and `free` are left out; a file system not
     mounted at the backup has none but ext's free blocks.  Each mount's
     size, used and available in every platform's dump (statfs on the
     BSDs, statvfs on illumos, `df` on Haiku), and the pool's figures,
     come first; fit resizing those layouts is later work.

The mapping from XKB names to each rescue platform's names belongs on the
stick, not in the dump, so it can be fixed without a new backup.

The run itself uses `--only` and `--through` as they are: no `--from`.

## Open questions

Answered on 2026-11-24:

- **3. What counts as "a little", and how far a disk may differ.**  10 %,
  offered by itself, either way.  Growing has no upper limit: a much
  bigger disk keeps the old sizes with the rest free (the default), or
  the largest partition grows to fill it, as the user chooses ("A much
  bigger disk: keep or grow").  Shrinking is decided by whether the data
  fits, not by a percentage: beyond 10 %, with the data fitting with a
  margin, it is the user's decision on its own screen ("A bigger
  difference: the user decides"), never a default; when the data doesn't
  fit, the disk is refused with the figures ("Too small for the data").
  bko-holt's 50 % is a code limit, not a safety rule.  This replaces the
  earlier rule that over 50 % is refused.
- **5. The stick's keyboard default.**  US English.  bko-holt's dump
  records the machine's layout, and the stick offers it before the first
  password, and again before the passphrases when the start-up prompt's
  layout differs ("The keyboard offer", "The keyboard at start-up").
- **6. Snapshot times (in part).**  UTC until a machine is chosen, then
  the machine's recorded zone, always named; logs stay UTC.  The
  fallbacks are still open (6 below).
- **8. The bug report's redaction.**  Redacted by default.  < Advanced >
  on "Report saved" makes an unredacted one, after the list of what it
  keeps, the warning that GitHub issues are public and a typed YES, saved
  beside the other as `...-UNREDACTED.txt`.
- The password hint shows after two wrong passwords, not one.

Still open:

- **1. The recovery key's check.**  A BIP39 checksum of 12 words is 4 bits:
  it catches most mistakes but can't say which word is wrong, and a wrong
  word that is on the list passes it one time in 16.  The screen can name
  a word only when it isn't on the list, and the user is told that.  Is
  "the 12 words don't add up" enough for the other case?
- **2. Where the password hint lives.**  In the machine's `system.json` on
  the stick (its schema gains a `password_hint` field; `stick-config.md`
  says it holds no secret, and a hint isn't one), beside the repository
  (a file readable without the password), or both?  Either way it's
  readable by anyone with the stick or the repository, which is what a
  hint is.
- **4. The QR code.**  Version 2 (25 x 25 modules) holds 47 alphanumeric
  characters at error correction level L, so the 44 of
  `HTTPS://GITHUB.COM/MCHOBBIT/BACKOTTER/ISSUES` fit there and at no
  higher level.  Drawn with half-blocks, a 2-module quiet zone makes it
  29 x 15; the standard asks for 4 (33 x 17).  Is 2 enough for phones
  (to be tried), and does GitHub take the upper-case path (to be tested
  before relying on it)?
- **6. Times, when the zone can't be used.**  Is the order on Snapshot
  right: the zone name with the image's zoneinfo, else the recorded
  POSIX rule worked out by the TUI, else each snapshot's own offset
  ("UTC+01:00")?  Should rows say `CET` or `Europe/Berlin`?  And is
  showing the machine's zone right at all, or would UTC with the zone
  named be clearer?  The user wasn't sure (2026-11-24).
- **7. The problem codes** not yet in `ui.md` and the help texts per
  failing program: bko-holt's in `holt-json.md` (D12), bko-rescue's own in
  `app/strings.rs`?
- **9. `stick-config.md`'s layout** gains `/backotter/reports/` for bug
  reports, beside each machine's `restore-logs/`.  Keep the newest 10
  there too, as with restore logs, and count a report and its
  UNREDACTED twin as one?
- **10. Plain mode's step line.**  `rescue-tui.md`'s example says "Step 2 of
  9"; these screens have dropped the count.  Drop it there too?
- **11. The mapping table.**  XKB (layout, variant) is the key.  The Linux
  stick needs no table (`ckbcomp | loadkeys`).  FreeBSD's vt, DragonFly's
  syscons, wscons and illumos need one, about 50 to 70 rows (`de
  nodeadkeys` is `de.noacc.kbd`, `german.iso`, `de.nodead`, `German`).
  Who writes it, where on the stick does it live, and what does a
  variant with no counterpart do: fall back to the base layout with a
  warning, as drawn, or not be offered?
- **12. Desktop or console layout.**  The repository password was typed at
  set-up, most likely on the desktop, in one of maybe several layouts;
  the dump runs as root at each backup, not then.  Is `at_setup` (a
  set-up hook) worth adding, and until it exists, is the first desktop
  layout the right guess?  Reading users' `kxkbrc` and dconf (binary
  GVDB) means new paths under `/home`: is that wanted, given they may
  be encrypted or not mounted?
- **13. The start-up prompt's layout.**  Which map each initramfs builder
  and boot loader really uses at the passphrase prompt is known with
  medium confidence only (initramfs-tools' `KEYMAP`, Plymouth reading
  `XKBLAYOUT` itself, Void's dracut, Guix).  Each needs checking on a
  guest before the second offer relies on it.  Should Passphrases also
  always suggest a passphrase of plain letters and digits?
- **14. Zoneinfo on the images.**  It looks present on all seven (the
  illumos keep list has `/usr/share/lib/zoneinfo`; the Linux slimming
  keeps tzdata; the BSD images are whole release sets), but nothing
  checks it.  Add a check to each `stick/*/checks.sh` (`test -f
  /usr/share/zoneinfo/Europe/Berlin`, illumos' own path) so a later
  slimming can't drop it?  Haiku has no zoneinfo tree: is the POSIX
  rule enough there?
- **15. The margin, and the snapshot's size.**  Proposed: the space used
  at the backup, plus 10 % of it, plus 2 GiB, must fit in the partition's
  new size (a proposal, not decided).  bko-holt's fit asks less today:
  the shrink and max(1 GiB, 5 % of the space used) free (`SPARE_BYTES`,
  `SPARE_PCT`).  One rule should serve both, and bko-holt's verdict is
  what the stick shows: which?  And where the dump has no figure (a
  file system not mounted at the backup), should the snapshot's size of
  the files under the mount point stand in, with the same margin, or is
  the disk refused as now?
- **16. A much bigger disk, every time?**  Taking a disk more than 10 %
  bigger opens "This disk is much bigger", whose default keeps the
  sizes: one more Enter in the most common case.  Or should the row take
  it with the old sizes, and only Review offer the grow?
