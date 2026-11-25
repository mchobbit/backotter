//! Every word the app's screens say, in one place, for translation later
//! (rescue-tui.md, "The app on top", RT11).  A test checks each passes
//! through `Clean` unchanged.  Text with something from outside in it (a
//! hostname, an address) is made by a function here from the cleaned
//! parts; nothing from outside is ever a format string.

// ---- Screens' names (the title line's right) ----
pub const SCR_MACHINE: &str = "Machine";
pub const SCR_NETWORK: &str = "Network";
pub const SCR_REPOSITORY: &str = "Repository";
pub const SCR_KEYBOARD: &str = "Keyboard";
pub const SCR_SNAPSHOT: &str = "Snapshot";
pub const SCR_REPORT: &str = "Report";
pub const SCR_DISKS: &str = "Disks";
pub const SCR_START: &str = "Starting";
pub const START_TITLE: &str = "Reading the stick";
pub const CHECK_HOLT_TITLE: &str = "Starting";
pub const CHECKING_HOLT: &str = "Checking this stick's bko-holt.";

// ---- Machine ----
pub const MACHINE_TITLE: &str = "Which machine is this?";
pub const MACHINE_NONE_HERE: &str = "The stick knows these machines.  None of them was backed up from this computer.  Pick the one you are restoring.";
pub const MACHINE_AFTER: &str = "The one you pick is restored onto this computer, so check the name:\nit is the computer's name from when it was backed up.";
pub const MACHINE_PICK_FIRST: &str = "Pick a machine first: move to it with the arrows.";
pub const NO_NAME: &str = "(no name)";
pub const BTN_TYPE_LOCATION: &str = "Type a location";
pub const BTN_KEYBOARD: &str = "Keyboard";
pub fn machine_here(name: &str, when: &str) -> String {
    format!("The stick knows these machines.  {name} was backed up from this computer, newest backup {when}: it is the one chosen.  Check it is the one you are restoring.")
}
pub fn machine_row(name: &str, os: &str, when: &str) -> String {
    format!("{name}{os}newest backup {when}")
}
pub fn machine_row_here(name: &str, os: &str) -> String {
    format!("{name}{os}backed up from this computer")
}
pub fn machine_bad(name: &str, why: &str) -> String {
    format!("{name}   can't be chosen: {why}")
}
pub fn machine_blocked(name: &str, why: &str) -> String {
    format!("{name} can't be chosen: {why}.")
}
pub const STATE_CHECKSUM_BAD: &str = "its settings on the stick fail their checksum";
pub const STATE_CHECKSUM_MISSING: &str = "its settings on the stick have no checksum";
pub const STATE_SCHEMA_UNKNOWN: &str = "its settings are newer than this stick reads";
pub const STATE_UNREADABLE: &str = "its settings on the stick can't be read";
pub const STATE_OTHER: &str = "its settings on the stick aren't usable";
pub const STATE_NO_REPO: &str = "its settings don't say where its backups are";
pub const STATE_BAD_REPO: &str = "its backups' address isn't usable";

// ---- Type a location ----
pub const LOCATION_TITLE: &str = "Where is the backup?";
pub const LOCATION_ASK: &str = "Type the address of your backup, as written on the sheet from when the backups were set up:";
pub const LOCATION_EXAMPLES: &str = "For example:  sftp://nas.local/backotter/lovelace\n              rest:http://10.0.0.5:8000/lovelace";
pub const LOCATION_MISSING: &str = "This stick can't tell which machine to restore: its BACKOTTER partition, which holds its settings, is missing.";
pub const LOCATION_DAMAGED: &str = "This stick can't tell which machine to restore: its BACKOTTER partition, which holds its settings, is damaged.";
pub const LOCATION_UNREADABLE: &str = "This stick can't tell which machine to restore: its BACKOTTER partition, which holds its settings, couldn't be read.";
pub const LOCATION_EMPTY: &str = "This stick has no machines on it yet: none has been backed up to it.";
pub const LOCATION_NO_HOLT: &str = "This stick's bko-holt couldn't say which machines the stick knows.";
pub const LOCATION_EMPTY_FIELD: &str = "Type the address first.";
pub const LOCATION_BAD: &str = "That isn't an address bko-rescue can use.  It starts sftp://, rest:http:// or rest:https://, or it is a folder's path from /; a password goes in the next screen, never in the address.";
pub const KEYBOARD_BEFORE_TYPING: &str = "  Change it before typing: < Keyboard >.";

// ---- Two sticks (illumos) ----
pub const TWO_STICKS_TITLE: &str = "Two BackOtter sticks";
pub const TWO_STICKS: &str = "Two BackOtter sticks made from the same image are plugged in, and this one can't tell which it was started from.  Take out the one you didn't start from, then press Retry.";
pub const BTN_RETRY: &str = "Retry";

// ---- bko-holt itself ----
pub const HOLT_TITLE: &str = "Problem: this stick's bko-holt";
pub fn holt_missing(path: &str) -> String {
    format!("bko-rescue needs bko-holt to read this stick and restore, and it isn't here: {path}.")
}
pub const HOLT_TOO_OLD: &str = "This stick's bko-holt is too old for this bko-rescue: it doesn't speak the JSON protocol (bko-holt --version --json).  Use a stick whose bko-holt and bko-rescue were made together.";
pub fn holt_says(what: &str) -> String {
    format!("This stick's bko-holt gave an answer bko-rescue can't use: {what}.")
}
pub const HOLT_NO_LOCK: &str = "This is a dry run, but bko-holt didn't confirm its lock (BKO_HOLT_NO_COMMIT), so bko-rescue stops here: nothing has been run that writes.";
pub const STARTING: &str = "Reading this stick's settings.";
/// Every screen's, in a dry run (rescue-tui.md, "Dry run").
pub const DRY_RUN_BANNER: &str = "DRY RUN: nothing will be written";

// ---- Network ----
pub const NETWORK_TITLE: &str = "No network";
pub fn network_text(name: &str, host: &str) -> String {
    format!("{name}'s backups are on {host}, which this computer can't reach.  This stick uses wired networks only: plug a network cable from this computer into your router or network.")
}
pub const NETWORK_PORTS: &str = "Network ports:";
pub const NETWORK_NO_PORTS: &str = "This computer has no wired network port this stick can use.";
pub const NETWORK_CANT_LIST: &str = "This stick can't list the network ports here.";
pub fn port_row(n: usize, state: &str, name: &str) -> String {
    format!("  Port {n}   {state:<43}({name})")
}
pub const PORT_NO_CABLE: &str = "no cable plugged in";
pub const PORT_NO_ADDRESS: &str = "cable in, but no address yet";
pub const PORT_UP: &str = "cable in, with an address";
pub const PORT_UNKNOWN: &str = "can't tell";
pub const NO_WIFI: &str = "Wi-Fi can't be used from the rescue stick.";
pub const BTN_TRY_AGAIN: &str = "Try again";
pub const BTN_SET_BY_HAND: &str = "Set by hand";
pub const NETWORK_WAITING: &str = "Waiting for the network: plug in the cable if it isn't.";
pub const SET_BY_HAND_TITLE: &str = "Setting the address by hand";
pub const SET_BY_HAND: &str = "This bko-rescue can't set a network address by hand yet.  The shell can (on Linux: ip addr add ADDRESS/PREFIX dev PORT, then ip route add default via GATEWAY), and leaving it comes back here.";

// ---- Repository ----
pub const PASSWORD_TITLE: &str = "The backup password";
pub fn kept_at(name: &str, place: &str, url: &str) -> String {
    format!("{name}'s backups are kept on {place}, at\n  {url}")
}
pub fn kept_in(name: &str, url: &str) -> String {
    format!("{name}'s backups are kept in this folder:\n  {url}")
}
pub const TYPE_PASSWORD: &str = "Type the backup password:";
pub const PASSWORD_NOT_THIS: &str = "It's the password chosen when the backups were set up, not the password for this computer.";
pub const PASSWORD_KEPT: &str = "The password typed before is kept: press Enter in the empty field to use it again.";
pub const TYPE_PASSWORD_PLAIN: &str = "Type the backup password, or press Enter alone for other choices:";
pub const PASSWORD_KEPT_PLAIN: &str = "The password typed before is kept: press Enter alone, then p, to use it again.";
pub const BTN_KEPT: &str = "Use the password typed before";
pub fn wrong_once(keyboard: &str) -> String {
    format!("That password didn't open the backups.  Check Caps Lock, and the keyboard: {keyboard}.")
}
pub fn wrong_again(keyboard: &str) -> String {
    format!("That password didn't open the backups either.  Check Caps Lock, and the keyboard: {keyboard}.")
}
pub fn set_to(name: &str) -> String {
    format!("it is set to {name}")
}
pub const KB_SSH_SHORT: &str = "it is your own computer's";
pub const KB_SERIAL_SHORT: &str = "it is your terminal's";
pub const YZ_SWAPPED: &str = "A layout with Y and Z swapped is a usual cause.";
pub const HINT_HEAD: &str = "Your hint, saved when the backups were set up:";
pub fn hint_line(hint: &str) -> String {
    format!("  \"{hint}\"")
}
pub fn tries(n: u32) -> String {
    format!("Tries so far: {n}")
}
pub const BTN_RECOVERY: &str = "Use recovery key instead";
pub const OPENING_TITLE: &str = "Opening the backups";
pub fn opening(name: &str, place: &str) -> String {
    format!("Opening {name}'s backups on {place}.")
}
pub fn listing(name: &str) -> String {
    format!("Reading the list of {name}'s backups.")
}

// ---- The keyboard line and the offer ----
pub fn keyboard_set(name: &str, from: &str) -> String {
    format!("Keyboard: {name}{from}.")
}
pub const FROM_DEFAULT: &str = "";
pub fn from_backup(machine: &str) -> String {
    format!(", from {machine}'s backup")
}
pub const FROM_CHOSEN: &str = ", chosen by you";
pub const K_CHANGES: &str = "  k changes it.";
pub fn backup_silent(machine: &str) -> String {
    format!("  {machine}'s backup doesn't say which layout it used.")
}
pub fn backup_unknown(machine: &str, layout: &str) -> String {
    format!("  {machine}'s backup names {layout}, which this stick can't load.")
}
pub const KEYBOARD_SSH: &str = "The keyboard layout is your own computer's.";
pub const KEYBOARD_SERIAL: &str = "The keyboard layout is your terminal's.";
pub const KEYBOARD_FIXED: &str = "This stick can't change the keyboard layout here.";
pub fn keyboard_fixed_backup(machine: &str, layout: &str) -> String {
    format!("This stick can't change the keyboard layout here.  {machine}'s backup names {layout}: set it by hand if the keys differ.")
}
pub const OFFER_TITLE: &str = "Keyboard for the password";
pub fn offer(machine: &str, short: &str, xkb: &str, now: &str) -> String {
    format!("{machine} used a {short} keyboard ({xkb}) when it was backed up.  This stick is set to {now}.\n\nSwitch to {short} for the password?")
}
pub fn offer_why(short: &str, now: &str) -> String {
    format!("On a {short} keyboard set to {now}, keys move and most symbols too, so the password would come out wrong.")
}
/// A variant this stick hasn't (rescue-screens.md, "The keyboard offer"):
/// its base layout is offered.  The mock's line names the dead keys; a
/// variant in general may move other keys, so this says only that.
pub fn offer_base(short: &str, variant: &str) -> String {
    format!("This stick has {short}, but not its \"{variant}\" variant: a few keys may differ from the old keyboard's.")
}
pub const OFFER_DESKTOP: &str = "Its desktop also had ";
pub fn btn_switch(short: &str) -> String {
    format!("Switch to {short}")
}
pub fn btn_keep(now: &str) -> String {
    format!("Keep {now}")
}
pub const BTN_ANOTHER_LAYOUT: &str = "Another layout";
pub fn offer_keys(now: &str) -> String {
    format!("Tab: buttons | Enter: press | Esc: keep {now}")
}
pub fn switched(name: &str) -> String {
    format!("Keyboard set to {name}.")
}
pub const LAYOUT_TITLE: &str = "Keyboard layout";
pub const LAYOUT_TEXT: &str = "Which keyboard is this?  Pick the layout printed on its keys.";
pub const TRY_IT: &str = "Try it here:";
pub fn whose(machine: &str) -> String {
    format!("{machine}'s")
}
pub const LAYOUT_FAILED_TITLE: &str = "The layout didn't load";
pub fn layout_failed(name: &str) -> String {
    format!("{name} couldn't be loaded, so the keyboard is as it was.")
}

// ---- Recovery key ----
pub const RECOVERY_TITLE: &str = "Recovery key";
pub const RECOVERY_TEXT: &str = "Type the 12 words printed on the recovery sheet when the backups were set up, in the sheet's order.  After four letters, the rest of a word is filled in for you.";
pub const RECOVERY_PLAIN: &str = "Type the 12 words from the recovery sheet on one line, with spaces.\nThe first four letters of each word are enough.";
pub const RECOVERY_AFTER: &str = "The last word holds a check, so most typing mistakes are caught here.";
pub const RECOVERY_CHECKING: &str = "Checking the recovery key.";
pub const RECOVERY_SUM: &str = "The 12 words don't add up: one is wrong, or two are swapped.  Check each against the sheet.";
pub fn recovery_count(n: u64) -> String {
    format!("That's {n} words, and a recovery key has 12.  Check them against the sheet.")
}
pub fn recovery_unknown(n: u64, suggest: &str) -> String {
    if suggest.is_empty() {
        format!("Word {n} isn't one of the recovery words.")
    } else {
        format!("Word {n} isn't one of the recovery words: {suggest}?")
    }
}
pub const RECOVERY_NOT_IT: &str = "Those 12 words are a recovery key, but not the one for these backups: the password they make didn't open them.";
pub const RECOVERY_PROBLEM_TITLE: &str = "The recovery key";

// ---- Which machine? (a typed location) ----
pub const WHICH_TITLE: &str = "Which machine?";
pub fn which_text(n: usize) -> String {
    format!("These backups are of {n} machines.  Pick the one you are restoring.")
}
pub const WHICH_PICK_FIRST: &str = "Pick a machine first: move to it with the arrows.";
pub fn which_row(name: &str, count: usize, when: &str) -> String {
    format!("{name}{count} backups, newest {when}")
}

// ---- Snapshot ----
pub const SNAPSHOT_TITLE: &str = "Which backup?";
pub fn snapshots_of(name: &str) -> String {
    format!("{name}'s backups, newest first:")
}
pub const NEWEST: &str = "newest";
pub fn zone_named(machine: &str, zone: &str, std: &str, dst: Option<&str>) -> String {
    match dst {
        Some(d) => format!("Times are in {machine}'s own time zone, {zone}: {std} in winter, {d} in summer."),
        None => format!("Times are in {machine}'s own time zone, {zone} ({std})."),
    }
}
pub fn zone_rule(machine: &str, std: &str, dst: Option<&str>) -> String {
    match dst {
        Some(d) => format!("Times are in {machine}'s own time zone: {std} in winter, {d} in summer."),
        None => format!("Times are in {machine}'s own time zone, {std}."),
    }
}
pub fn zone_offsets(machine: &str) -> String {
    format!("Times are {machine}'s offset from UTC, as each backup recorded it.")
}
pub const NEWEST_USUAL: &str = "  The newest is usually the one you want.";
pub const NO_SNAPSHOTS_TITLE: &str = "No backups";
pub fn no_snapshots(name: &str) -> String {
    format!("These backups hold none of {name}.  Its backups may be elsewhere: go back, and pick another machine or type the address.")
}
pub fn some_unread(n: usize) -> String {
    format!("{n} backups in the list couldn't be read, and aren't shown.")
}

// ---- 5. Disks (RT12) ----
pub const SCR_PASSPHRASES: &str = "Passphrases";
pub const SCR_RESUME: &str = "Resume";
pub const SCR_REVIEW: &str = "Review";
pub const SCR_RESTORE: &str = "Restore";
pub const DISKS_TITLE: &str = "Where each disk goes";
pub const DISKS_KEYS: &str = "Arrows: old disk | Enter: change where it goes | Tab: buttons | Esc: back";
pub const PLANNING_TITLE: &str = "Planning";
pub const PLANNING: &str = "bko-holt is working out where each disk goes.  Nothing is written.";
pub const PLANNING_FIT: &str = "bko-holt is working out the new sizes.  Nothing is written.";
pub const PLANNING_REVIEW: &str = "bko-holt is checking the whole plan.  Nothing is written.";
pub const STATUS_TITLE: &str = "Looking at the disks";
pub const STATUS_TEXT: &str = "bko-holt is looking for an earlier restore on these disks.  Nothing is written.";
pub fn had_disks(host: &str, n: usize) -> String {
    if n == 1 {
        format!("{host} had 1 disk.")
    } else {
        format!("{host} had {n} disks.")
    }
}
pub fn had_disks_choose(host: &str, n: usize) -> String {
    if n == 1 {
        format!("{host} had 1 disk.  Choose a disk of this computer for it.")
    } else {
        format!("{host} had {n} disks.  Choose a disk of this computer for each one.")
    }
}
pub fn old_disk(n: usize, name: &str, size: &str, role: &str) -> String {
    format!("Old disk {n}: {name}, {size}, {role}")
}
pub const ROLE_SYSTEM: &str = "the system";
pub const ROLE_NOTHING: &str = "nothing mounted from it";
pub fn goes_to(name: &str, size: &str, serial: Option<&str>) -> String {
    match serial {
        Some(x) => format!("{name}, {size}, serial {x}"),
        None => format!("{name}, {size}, no serial"),
    }
}
pub fn by_serial(serial: &str) -> String {
    format!("its serial {serial}")
}
pub const BY_WWID: &str = "its WWID";
pub const BY_IDENTITY: &str = "its identity";
pub fn same_disk(by: &str) -> String {
    format!("the same disk, found by {by}")
}
pub const WILL_BE_ERASED: &str = "will be ERASED, then filled from the backup";
pub const CHOSEN_BY_YOU: &str = "chosen by you";
pub const NO_TARGET: &str = "no disk chosen yet: Enter on it chooses one";
pub const PLAIN_NO_TARGET: &str = "No disk is chosen for it yet.";
pub fn plain_goes(goes: &str, chosen: bool) -> String {
    if chosen {
        format!("Goes to: {goes}.  Chosen by you.")
    } else {
        format!("Goes to: {goes}.")
    }
}
pub const PLAIN_ERASED: &str = "It will be ERASED, then filled from the backup.";
pub fn plain_own(date: &str) -> String {
    format!("It will be ERASED.  Anything saved on it after {date} is lost.")
}
pub fn own_disk(n: usize, host: &str, role: &str, date: &str) -> String {
    format!("Old disk {n} is {host}'s own {role} disk.  It will be erased and refilled from the {date} backup: anything saved on it after that is lost.")
}
pub const NOT_USED: &str = "Not used:";
pub const NOT_OFFERED: &str = "Not offered:";
pub const ALREADY_USED: &str = "Already used:";
pub fn for_old_disk(n: usize) -> String {
    format!("for old disk {n}")
}
pub const BLANK: &str = "blank: nothing on it";
pub fn has_data(partitions: usize, what: &str) -> String {
    match partitions {
        0 => format!("has data: {what}"),
        1 => format!("has data: 1 partition, {what}"),
        n => format!("has data: {n} partitions, {what}"),
    }
}
/// "has data: ..." or "blank: ..." as plain mode's sentence: "It has
/// data: ...", "It is blank: ...".
pub fn it(what: &str) -> String {
    if what.starts_with("has ") {
        format!("It {what}")
    } else {
        format!("It is {what}")
    }
}
pub const REFUSED_STICK: &str = "this stick, never a target";
pub const REFUSED_BACKUP: &str = "holds the backup being restored, never a target";
pub const REFUSED_ROOT: &str = "holds the system running now, never a target";
pub const REFUSED_IN_USE: &str = "in use now (mounted, or swap)";
pub const REFUSED_READ_ONLY: &str = "read-only";
pub const REFUSED_SECTOR: &str = "its sector size isn't the old disks'";
pub const BTN_OK: &str = "OK";
pub const BTN_THESE_ARE_RIGHT: &str = "These are right";
pub fn change_where(n: usize) -> String {
    format!("change where old disk {n} goes")
}
pub fn missing_target(n: usize) -> String {
    format!("Old disk {n} has no disk to go to yet: Enter on it chooses one.")
}
pub fn disk_problem(text: &str) -> String {
    format!("bko-holt can't take these choices: {text}")
}
pub const PLAN_FAILED_TITLE: &str = "bko-holt couldn't plan the restore";
pub fn plan_failed_what(why: &str) -> String {
    format!("bko-holt was asked for the plan, and said: {why}")
}
pub const PLAN_FAILED_WHY: &str = "It reads the backup's layout of the disks and this computer's disks; one of them isn't as it can use.  Raw output shows what it said.";
pub const PLAN_FAILED_TODO: &str = "Try again; if it fails the same way, make a report.";
pub const NO_LAYOUT_TITLE: &str = "the old disks' layout isn't here";
pub const NO_LAYOUT_WHY: &str = "bko-holt needs the layout the backup recorded (its system.json) to know the old disks.";
pub const NO_LAYOUT_TODO: &str = "Choose the machine on the first screen, with the stick it was set up on.  The shell can look at the BACKOTTER partition.";
pub const LAYOUT_NONE_TYPED: &str = "A typed address has no layout on this stick: the old disks' layout is on the stick a machine was set up with, and this bko-rescue can't take it from the backup yet.";
pub const LAYOUT_NO_PATH: &str = "bko-holt didn't name the machine's settings file in a form this stick takes.";
pub const LAYOUT_NO_DEV: &str = "This stick's BACKOTTER partition isn't named, so the machine's settings can't be read from it.";
pub const LAYOUT_UNREADABLE: &str = "The machine's settings file on the BACKOTTER partition couldn't be read.";
pub fn layout_mount_failed(why: &str) -> String {
    format!("The BACKOTTER partition couldn't be read: {why}.")
}

// ---- One old disk ----
pub fn one_disk_title(n: usize, of: usize) -> String {
    format!("Old disk {n} of {of}: where does it go?")
}
pub const ONE_DISK_KEYS: &str = "Arrows: choose | Enter: OK | Tab: buttons | Esc: back";
pub fn from_backup_of(host: &str) -> String {
    format!("From the backup of {host}:")
}
pub const PUT_IT_ON: &str = "Put it on this disk of this computer.  The disk you pick is ERASED.";
pub const NO_SERIAL: &str = "no serial";
pub const NO_CANDIDATES: &str = "No disk of this computer can take it.";
pub fn row_slack(diff: &str) -> String {
    format!("{diff} smaller: only free space at the end is lost")
}
pub fn row_smaller(diff: &str, m: &str, old: &str, new: &str) -> String {
    format!("{diff} smaller: {m} would shrink from {old} to {new}")
}
pub fn row_much_smaller(m: &str, old: &str, new: &str, pct: &str) -> String {
    if new.is_empty() {
        format!("much smaller: {m} would shrink from {old}, {pct} %")
    } else {
        format!("much smaller: {m} would shrink from {old} to {new}, {pct} %")
    }
}
pub fn row_bigger(diff: &str, m: &str, old: &str, new: &str) -> String {
    format!("{diff} bigger: {m} would grow from {old} to {new} (you choose)")
}
/// Until RT10n's grow to fill, "left free" only (rescue-screens.md).
pub fn row_much_bigger(diff: &str) -> String {
    format!("{diff} bigger: left free")
}
pub fn row_wont_fit(m: &str, used: &str, most: &str) -> String {
    format!("too small: {m} uses {used}; the most it could get is {most}")
}
pub fn row_cant_resize(m: &str) -> String {
    format!("too small: {m} can't be made smaller here")
}
pub fn row_unknown(verdict: &str) -> String {
    format!("can't be used: bko-holt says {verdict}")
}
pub fn wont_fit(m: &str, used: &str, most: &str, need: &str) -> String {
    format!("{m} holds {used}; the most it could get on this disk is {most}.  It needs {need}, with room to spare.  Choose another disk.")
}
pub fn cant_resize(why: &str) -> String {
    format!("{why}  Choose another disk.")
}
pub const CANT_RESIZE_WHY: &str = "bko-holt can't make this layout smaller.";
pub fn unknown_verdict(verdict: &str) -> String {
    format!("bko-holt's verdict on this disk is \"{verdict}\", which this bko-rescue doesn't know, so it isn't taken.  Choose another disk.")
}
pub const MUCH_SMALLER: &str = "much smaller";
pub const A_LITTLE_SMALLER: &str = "a little smaller";
pub const TOO_SMALL: &str = "too small";
pub const BIGGER: &str = "bigger";

// ---- The size screens ----
pub const LITTLE_SMALLER_TITLE: &str = "This disk is a little smaller";
pub const LITTLE_BIGGER_TITLE: &str = "This disk is a little bigger";
pub const MUCH_SMALLER_TITLE: &str = "This disk is much smaller: you decide";
pub const MUCH_BIGGER_TITLE: &str = "This disk is much bigger";
pub const OFFER_KEYS: &str = "Tab: buttons | Enter: press | o: raw output | Esc: back to the disk list";
#[allow(clippy::too_many_arguments)]
pub fn was_is(n: usize, old: &str, old_size: &str, new: &str, new_size: &str, diff: &str, smaller: bool) -> String {
    if smaller {
        format!("Old disk {n} ({old}) was {old_size}.  The disk you chose, {new}, is {new_size}: {diff} smaller.")
    } else {
        format!("Old disk {n} ({old}) was {old_size}.  The disk you chose, {new}, is {new_size}: {diff} bigger.")
    }
}
pub fn shrink_text(system: bool, m: &str, old: &str, new: &str, pct: &str, used: Option<&str>) -> String {
    let what = if system {
        format!("So the partitions as they were don't fit.  The system partition can be made smaller to fit.  It is shown as {m}, and holds the operating system and the home folders.")
    } else {
        format!("So the partitions as they were don't fit.  The largest partition, {m}, can be made smaller to fit.")
    };
    let used = match used {
        Some(u) => format!("  Space used on {m}: {u}, so it fits with room to spare."),
        None => format!("  bko-holt says the data on {m} fits."),
    };
    format!("{what}\n  {m} will shrink from {old} to {new}, by {pct} %.\n{used}\n  The other partitions stay as they were.")
}
pub const SLACK_TEXT: &str = "Only free space at the end of the old disk is lost: no partition changes size.  The table is fitted to the smaller disk.";
pub fn grow_text(system: bool, m: &str, old: &str, new: &str, pct: &str, diff: &str) -> String {
    let what = if system { format!("Or the system partition ({m}) can grow:") } else { format!("Or the largest partition ({m}) can grow:") };
    format!("The partitions can stay as they were, with the extra {diff} left free at the end of the disk.  {what}\n  {m} will grow from {old} to {new}, by {pct} %.\n  The other partitions stay as they were either way.")
}
pub fn grows_elsewhere(disk: &str, m: &str, old: &str, new: &str) -> String {
    format!("  On {disk}, {m} grows from {old} to {new} too: the same change applies to every disk.")
}
pub const OR_ANOTHER: &str = "Or choose another disk.";
pub fn fit_refused(text: &str) -> String {
    format!("bko-holt can't fit it there: {text}")
}
pub const BTN_SHRINK: &str = "Shrink the system partition";
pub const BTN_GROW: &str = "Grow the system partition";
pub const BTN_KEEP_SIZES: &str = "Keep the old sizes";
pub const BTN_ANOTHER_DISK: &str = "Another disk";
pub fn decide_sizes(m: &str, pct: &str, old: &str, new: &str, used: &str, fits: bool) -> String {
    let fit = if fits { "so it fits with room to spare" } else { "so it would fit" };
    if new.is_empty() {
        format!("To fit, {m} would shrink by {pct} %:\n  Old size:    {old}\n  Space used:  {used} at the backup, {fit}\nThe other partitions stay as they were.")
    } else {
        format!("To fit, {m} would shrink by {pct} %:\n  Old size:    {old}\n  New size:    {new}\n  Space used:  {used} at the backup, {fit}\nThe other partitions stay as they were.")
    }
}
pub const DECIDE_WHY: &str = "This stick offers a shrink of up to 10 % with a button.  This is more, so you decide, and nothing is chosen for you.";
pub const BEYOND_LIMIT: &str = "This stick's bko-holt can't shrink a partition this far yet, though the data would fit: this disk can't be used for now.";
pub fn shrink_row(m: &str, new: &str, disk: &str) -> String {
    format!("Shrink {m} to {new} and restore onto {disk}")
}
pub const CHOOSE_ANOTHER: &str = "Choose another disk";
pub const CHOOSE_ONE_FIRST: &str = "Choose one first: move to it with the arrows.";
pub fn much_bigger_text(diff: &str) -> String {
    format!("The partitions stay as they were, with the extra {diff} left free at the end of the disk, to be used later.  Growing the system partition to fill the disk isn't in this stick's bko-holt yet.")
}
pub fn keep_row(diff: &str) -> String {
    format!("Keep the old sizes, and leave {diff} free")
}

// ---- 6. Passphrases ----
pub fn passphrase_title(n: usize, of: usize) -> String {
    format!("Passphrase {n} of {of}")
}
pub fn opens(names: &[String], holds: &[String]) -> String {
    let what = if names.len() > 1 { format!("{} encrypted containers ({})", names.len(), names.join(", ")) } else { format!("the encrypted container {}", names.first().map(String::as_str).unwrap_or("")) };
    match holds.len() {
        0 => format!("This passphrase opens {what}."),
        1 => format!("This passphrase opens {what}, which holds {}.", holds[0]),
        _ => format!("This passphrase opens {what}, which hold {}.", holds.join(", ")),
    }
}
pub const PASS_NEW: &str = "You will type it every time the computer starts.  It may be the old one or a new one.  If it is forgotten, nothing can open these disks: write it down.";
pub const PASS_KEPT_HEADER: &str = "The backup kept this disk's own encryption header, so only the passphrase it had opens it: type the passphrase this disk had.";
pub const PASS_RESUME: &str = "These disks hold a restore started before, and its containers are made: type the passphrase given the first time.";
pub const LABEL_PASSPHRASE: &str = "Passphrase:";
pub const LABEL_AGAIN: &str = "Type it again:";
pub const PASSPHRASE_KEYS: &str = "Type | Tab: next field and buttons | Enter: next field, then OK | Esc: back";
pub const PASS_EMPTY: &str = "Type a passphrase first: an empty one isn't taken.";
pub const PASS_MISMATCH: &str = "The two didn't match, so both are cleared: type them again.";
pub fn pass_length(min: u64, max: u64) -> String {
    format!("It must be {min} to {max} characters long: type it again.")
}
pub fn kb_as_at_start(set: &str, host: &str) -> String {
    format!("Keyboard: {set}, as at {host}'s start-up prompt.")
}
pub fn kb_not_at_start(set: &str, host: &str, there: &str) -> String {
    format!("Keyboard: {set}.  {host}'s start-up prompt uses {there}: a passphrase of plain letters and digits is the safest.")
}
pub fn kb_start_unknown(set: &str, host: &str) -> String {
    format!("Keyboard: {set}.  Which layout {host}'s start-up prompt uses isn't known; plain letters and digits are the safest.")
}
pub const BOOT_KB_TITLE: &str = "Keyboard for the disk passphrase";
pub fn boot_kb(host: &str, now: &str, there: &str) -> String {
    format!("When {host} starts, it asks for the disk passphrase with the keyboard acting as {there}.  This stick is set to {now}.\n\nSwitch to {there} now?  Then the passphrase you type here is typed the same way at each start.\n\nA passphrase of plain letters, digits and spaces types the same with most layouts.")
}
pub const KEY_BLOCKED_TITLE: &str = "a disk this stick can't unlock";
pub fn key_blocked_what(group: &str) -> String {
    format!("The encrypted container {group} needs a key this bko-rescue can't ask for.")
}
pub const KEY_FILE_ELSEWHERE: &str = "The backed-up system opened it with a key file kept on another disk or outside the backup, which the restore can't bring back.";
pub fn unlock_unknown(kind: &str) -> String {
    format!("bko-holt says it is unlocked by \"{kind}\", which this bko-rescue doesn't know, so it never skips the question.")
}
pub const KEY_BLOCKED_TODO: &str = "Restore it from the shell with bko-holt load and --key-file, or make a report.";

// ---- 7. Resume offer ----
pub const RESUME_TITLE: &str = "A restore was started here before";
pub fn resume_head(host: &str) -> String {
    format!("These disks already hold part of a restore of {host}:")
}
pub const STARTED_NOT_FINISHED: &str = "started, not known to be finished";
pub const RESUME_CHOICES: &str = "Go on: nothing is erased.  What isn't done is done again, your files copied back over what is there, then the rest.\nStart again: everything on these disks is erased first.\nEither way, Review comes next and asks for the serials.";
pub fn resume_only_again(why: &str) -> String {
    format!("It can't be gone on with: {why}.\nStart again erases everything on these disks first.  Review comes next and asks for the serials.")
}
pub const BTN_GO_ON: &str = "Go on";
pub const BTN_START_AGAIN: &str = "Start again";

// ---- 8. Review ----
pub const REVIEW_TITLE: &str = "Ready to restore";
pub const REVIEW_KEYS: &str = "Type the serials | Tab: next | Enter on Restore: start | c: commands | Esc";
pub fn review_head(host: &str, os: Option<&str>, when: &str, size: Option<&str>, same: bool) -> String {
    let os = os.map(|x| format!(" ({x})")).unwrap_or_default();
    let size = size.map(|x| format!(", {x}")).unwrap_or_default();
    let from = if same { "Backed up from this computer." } else { "Backed up from a different computer." };
    format!("Restore {host}{os} from the backup of {when}{size}.  {from}")
}
pub const REVIEW_ERASE: &str = "These disks will be ERASED, then filled from the backup.  Type the last six characters of each serial.  Check it against the label on the disk if you can: that's how you know it is the right disk.";
pub const REVIEW_ERASE_PLAIN: &str = "These disks will be ERASED, then filled from the backup:";
pub fn review_go_on(stage: &str) -> String {
    format!("Nothing is erased: the restore goes on from \"{stage}\".  Type the last six characters of each serial all the same.")
}
pub fn review_go_on_plain(stage: &str) -> String {
    format!("Nothing is erased: the restore goes on from \"{stage}\" on these disks:")
}
pub const WORD_ERASED: &str = "ERASED";
pub const WORD_WRITTEN: &str = "WRITTEN";
pub const WORD_GOES_ON: &str = "goes on";
pub fn plain_disk(n: usize, name: &str, size: &str) -> String {
    format!("Disk {n}: {name}, {size},")
}
pub fn ask_serial(n: usize) -> String {
    format!("Type the last six characters of disk {n}'s serial, or b to go back:")
}
pub fn ask_size(n: usize, expect: &str) -> String {
    format!("Disk {n} has no serial of its own: type its size and model, {expect}, or b to go back:")
}
pub fn wrong_serial(n: usize, name: &str) -> String {
    format!("What is typed for disk {n} ({name}) isn't the end of its serial.  Check it against the disk's label.")
}
pub fn wrong_size(n: usize, name: &str, expect: &str) -> String {
    format!("Disk {n} ({name}) has no serial of its own: type its size and model as shown, {expect}.")
}
pub fn confirmed(n: usize) -> String {
    format!("Disk {n} confirmed.")
}
pub fn own_disk_review(name: &str, host: &str, role: &str, date: &str) -> String {
    format!("The {name} is {host}'s own {role} disk: anything saved on it after {date} is lost.")
}
pub const CHANGES: &str = "Changes:";
pub const CHANGES_NONE: &str = "Changes: none.";
pub const NO_PROBLEMS: &str = "No problems found.";
pub fn review_problem(text: &str) -> String {
    format!("Problem: {text}")
}
pub fn not_touched(what: &str) -> String {
    format!("Not touched: {what}.")
}
pub const THIS_STICK: &str = "this stick";
pub const NOTHING_ELSE: &str = "no other disk";
pub const NOTHING_CHANGED: &str = "Nothing has been changed on any disk yet.  Back is safe.";
pub fn shrinks(m: &str, pct: &str, old: &str, new: &str, decided: bool) -> String {
    match (m, decided) {
        (_, true) => format!("{m} shrinks by {pct} %, from {old} to {new}, as you chose."),
        ("/", false) => format!("/ (the system partition) shrinks by {pct} %, {old} to {new}."),
        _ => format!("{m} shrinks by {pct} %, {old} to {new}."),
    }
}
pub fn grows(m: &str, pct: &str, old: &str, new: &str) -> String {
    format!("{m} grows by {pct} %, from {old} to {new}.")
}
pub fn made_as(m: &str, to: &str, was: &str) -> String {
    format!("{m} is made as {to}, not {was}.")
}
pub const CHANGE_PASSPHRASE: &str = "The encrypted disks open with the passphrase you typed.";
pub const NO_OTHER_FS: &str = "Another filesystem: not for this system.";
pub const BTN_RESTORE: &str = "Restore";
pub const BTN_RESTORE_NOW: &str = "Restore now";
pub const BTN_SHOW_WHAT: &str = "Show what would happen";
pub const BTN_COMMANDS: &str = "Commands";
pub const BTN_EVERY_COMMAND: &str = "Show every command";
pub const REVIEW_PROBLEM_TITLE: &str = "the plan has a problem";
pub fn review_problem_what(text: &str) -> String {
    format!("bko-holt found a problem with the plan, so the restore can't start: {text}")
}
pub const REVIEW_PROBLEM_WHY: &str = "bko-holt checks everything before it writes anything, and stops at the first thing it can't do as the backup was.";
pub const ERASE_PROBLEM_WHY: &str = "A disk that holds something is written only when it is named to be erased, as the typed serials do.";
pub const REVIEW_PROBLEM_TODO: &str = "Go back and change what it names, or make a report.";
pub const BTN_TO_DISKS: &str = "Go back and change it";
pub const COMMANDS_TITLE: &str = "Every command, in order";
pub const ADVANCED_FS_TITLE: &str = "Advanced: restore onto another filesystem";
pub const ADVANCED_FS_TEXT: &str = "A filesystem can be made as ext4, XFS or btrfs instead.  Files, owners and permissions stay the same; fstab and the boot setup follow.";
pub const ADVANCED_FS_AFTER: &str = "Changing a filesystem clears the serials typed on Review, since what will be written has changed.";
pub const AS_IT_WAS: &str = "as it was";
pub const CHANGED: &str = "changed";
pub const CANT_CHANGE: &str = "can't change: see below";
pub const BTN_UNDO_ALL: &str = "Undo all";

// ---- 9. Restore: RT12's second part ----
pub const NEXT_TITLE: &str = "Next: the restore";
pub fn next(name: &str, when: &str, dry_run: bool) -> String {
    let what = if dry_run { "The dry run's walk through every command" } else { "The restore itself" };
    format!("{name}'s backup of {when} is chosen, the disks are planned and confirmed.\n\n{what} isn't in this bko-rescue yet.  Nothing has been changed on any disk.")
}
pub const BTN_LEAVE: &str = "Leave bko-rescue";

// ---- Problems and their help ----
pub const BACKUP_SAFE: &str = "Your backup is safe: nothing in it has been changed.";
pub const WHAT_HAPPENED: &str = "What happened: ";
pub const WHY: &str = "Why: ";
pub const TODO: &str = "What can be done: ";
pub const BTN_REPORT: &str = "Report";
pub const BTN_RAW: &str = "Raw output";
pub const BTN_NETWORK: &str = "Network";
pub const BTN_MACHINE: &str = "Another machine";
pub const RAW_TITLE: &str = "What the program said";
pub fn problem_title(what: &str) -> String {
    format!("Problem: {what}")
}
pub fn unreachable_what(name: &str, host: &str) -> String {
    format!("{name}'s backups are on {host}, and this computer couldn't reach it.")
}
pub const UNREACHABLE_WHY: &str = "There's no route to the server: no network, the server is off, or its name isn't known here.";
pub const UNREACHABLE_TODO: &str = "Check the network cable and that the server is on, then try again.";
pub const UNREACHABLE_TITLE: &str = "the backups can't be reached";
pub fn not_found_what(url: &str) -> String {
    format!("Nothing at {url} holds backups.")
}
pub const NOT_FOUND_WHY: &str = "The server answered, but there's no repository at that address: it may be typed wrong, or the backups were moved.";
pub const NOT_FOUND_TODO: &str = "Type the address again, as written on the sheet from when the backups were set up.";
pub const NOT_FOUND_TITLE: &str = "no backups at that address";
pub fn login_what(host: &str) -> String {
    format!("{host} didn't let this stick log in.")
}
pub const LOGIN_WHY: &str = "The server refused the login, or its key isn't the one this stick knows (it may be another server pretending to be it).";
pub const LOGIN_TODO: &str = "Check the address, or log in from the shell to see what the server says.";
pub const LOGIN_TITLE: &str = "the server refused the login";
pub fn mismatch_what(name: &str) -> String {
    format!("These aren't the backups the stick knows for {name}: the repository's ID isn't the one in its settings.")
}
pub fn id_unchecked(name: &str) -> String {
    format!("The stick couldn't check these are {name}'s backups: the repository's ID, or the one in its settings, can't be read to compare.")
}
pub const MISMATCH_WHY: &str = "The address may point at another machine's backups, or the repository was made again since the stick was last updated.";
pub const MISMATCH_TODO: &str = "Pick another machine, or type the address of this one's backups.  bko-rescue never switches by itself.";
pub const MISMATCH_TITLE: &str = "these aren't this machine's backups";
pub fn error_what(name: &str) -> String {
    format!("rustic-bko couldn't read {name}'s backups.")
}
pub const ERROR_WHY: &str = "It reported an error of its own: Raw output shows what it said.";
pub const ERROR_TODO: &str = "Try again; if it fails the same way, make a report.";
pub const ERROR_TITLE: &str = "the backups couldn't be read";
pub fn rustic_missing(path: &str) -> String {
    format!("rustic-bko, which reads the backups, isn't here: {path}.")
}
pub const UNREADABLE_LIST: &str = "rustic-bko's list of backups couldn't be read.";

// ---- The bug report ----
pub const MAKING_REPORT_TITLE: &str = "Making a report";
pub const MAKING_REPORT: &str = "Gathering what a bug report needs: the versions, this computer's disks, and what went wrong.";
pub const REPORT_TITLE: &str = "Report saved";
pub fn report_on_stick(path: &str) -> String {
    format!("The report is on this stick, in its BACKOTTER partition:\n  {path}")
}
pub fn report_in_ram(path: &str) -> String {
    format!("The report is at\n  {path}\nIt's in this computer's memory only: it's lost when the computer is turned off.  The shell can copy it somewhere.")
}
pub const REPORT_DRY_RUN: &str = "This is a dry run, so the report wasn't written to the stick.";
pub fn report_why_ram(why: &str) -> String {
    format!("It couldn't be saved on the stick: {why}.")
}
pub const REPORT_GITHUB: &str = "Plug the stick into another computer, open an issue at\n  github.com/mchobbit/backotter/issues\nand attach the file.\n\nYou need a free GitHub account.  If you have none, send the file to whoever set up your backups.";
pub const REPORT_REDACTED: &str = "No passwords or keys are in it.  The hostname, serials, UUIDs and user names are replaced by stand-ins.  Advanced makes one that keeps them.";
pub const REPORT_PRIVATE: &str = "The UNREDACTED file is for sharing privately: don't attach it to an issue.";
pub fn report_both(first: &str, second: &str) -> String {
    format!("Both reports are saved, beside each other:\n  {first}\n  {second}")
}
pub const BTN_VIEW: &str = "View it";
pub const BTN_ADVANCED: &str = "Advanced";
pub const REPORT_FAILED_TITLE: &str = "The report couldn't be made";
pub const REPORT_FAILED: &str = "bko-holt report-bug didn't write the report.  Raw output shows what it said.";
pub const REPORT_NO_STRIP: &str = "The list of what to take out of it (the password hint, the typed address) couldn't be written, so no report was made:";
pub const VIEW_TITLE: &str = "The report";
pub const ADVANCED_TITLE: &str = "Advanced: a report that isn't redacted";
pub const ADVANCED_TEXT: &str = "The report you saved replaces these with stand-ins.  This one keeps them as they are:\n  - the hostname and machine-id\n  - disk serials and WWIDs\n  - the UUIDs and GUIDs of disks, partitions and filesystems\n  - partition and filesystem labels\n  - user names, and the MAC addresses of network ports\n  - ZFS GUIDs, hostids, disk signatures and FAT serials\n  - the backup's address and key-file paths\nNeither report holds a password, a passphrase or a key.";
pub const ADVANCED_WARN: &str = "GitHub issues are public: anyone can read them.  Don't attach this report to an issue.  Share it privately: with whoever set up your backups, or with the developer if they ask for it.";
pub const ADVANCED_PLAIN: &str = "To make it, type YES.  Enter alone shows the other choices, Back among them.";
pub const TYPE_YES: &str = "To make it, type YES:";
pub const TYPE_YES_FIRST: &str = "Type YES to make it, or Esc to go back.";
pub const BTN_MAKE_IT: &str = "Make it";
pub const YES: &str = "yes";

// ---- Leaving ----
pub const SESSION_FAILED: &str = "bko-rescue can't make its directory in memory";

/// Every constant above, for the test that they're clean.
pub const ALL: &[&str] = &[
    SCR_MACHINE, SCR_NETWORK, SCR_REPOSITORY, SCR_KEYBOARD, SCR_SNAPSHOT, SCR_REPORT, SCR_DISKS, SCR_START, START_TITLE, CHECK_HOLT_TITLE, CHECKING_HOLT, MACHINE_TITLE,
    MACHINE_NONE_HERE, MACHINE_AFTER, MACHINE_PICK_FIRST, NO_NAME, BTN_TYPE_LOCATION, BTN_KEYBOARD, STATE_CHECKSUM_BAD,
    STATE_CHECKSUM_MISSING, STATE_SCHEMA_UNKNOWN, STATE_UNREADABLE, STATE_OTHER, STATE_NO_REPO, STATE_BAD_REPO, LOCATION_TITLE, LOCATION_ASK,
    LOCATION_EXAMPLES, LOCATION_MISSING, LOCATION_DAMAGED, LOCATION_UNREADABLE, LOCATION_EMPTY, LOCATION_NO_HOLT,
    LOCATION_EMPTY_FIELD, LOCATION_BAD, KEYBOARD_BEFORE_TYPING, TWO_STICKS_TITLE, TWO_STICKS, BTN_RETRY, HOLT_TITLE,
    HOLT_TOO_OLD, HOLT_NO_LOCK, STARTING, DRY_RUN_BANNER, NETWORK_TITLE, NETWORK_PORTS, NETWORK_NO_PORTS, NETWORK_CANT_LIST, PORT_NO_CABLE,
    PORT_NO_ADDRESS, PORT_UP, PORT_UNKNOWN, NO_WIFI, BTN_TRY_AGAIN, BTN_SET_BY_HAND, NETWORK_WAITING, SET_BY_HAND_TITLE,
    SET_BY_HAND, KB_SSH_SHORT, KB_SERIAL_SHORT, PASSWORD_TITLE, TYPE_PASSWORD, PASSWORD_NOT_THIS, PASSWORD_KEPT, TYPE_PASSWORD_PLAIN, PASSWORD_KEPT_PLAIN, BTN_KEPT, YZ_SWAPPED, HINT_HEAD, BTN_RECOVERY,
    OPENING_TITLE, FROM_DEFAULT, FROM_CHOSEN, K_CHANGES, KEYBOARD_SSH, KEYBOARD_SERIAL, KEYBOARD_FIXED, OFFER_TITLE,
    OFFER_DESKTOP, BTN_ANOTHER_LAYOUT, LAYOUT_TITLE, LAYOUT_TEXT, TRY_IT, LAYOUT_FAILED_TITLE, RECOVERY_TITLE, RECOVERY_TEXT,
    RECOVERY_PLAIN, RECOVERY_AFTER, RECOVERY_CHECKING, RECOVERY_SUM, RECOVERY_NOT_IT, RECOVERY_PROBLEM_TITLE, WHICH_TITLE,
    WHICH_PICK_FIRST, SNAPSHOT_TITLE, NEWEST, NEWEST_USUAL, NO_SNAPSHOTS_TITLE, NEXT_TITLE, BTN_LEAVE, BACKUP_SAFE,
    WHAT_HAPPENED, WHY, TODO, BTN_REPORT, BTN_RAW, BTN_NETWORK, BTN_MACHINE, RAW_TITLE, UNREACHABLE_WHY, UNREACHABLE_TODO,
    UNREACHABLE_TITLE, NOT_FOUND_WHY, NOT_FOUND_TODO, NOT_FOUND_TITLE, LOGIN_WHY, LOGIN_TODO, LOGIN_TITLE, MISMATCH_WHY,
    MISMATCH_TODO, MISMATCH_TITLE, ERROR_WHY, ERROR_TODO, ERROR_TITLE, UNREADABLE_LIST, MAKING_REPORT_TITLE, MAKING_REPORT,
    REPORT_TITLE, REPORT_DRY_RUN, REPORT_GITHUB, REPORT_REDACTED, REPORT_PRIVATE, BTN_VIEW, BTN_ADVANCED,
    REPORT_FAILED_TITLE, REPORT_FAILED, REPORT_NO_STRIP, VIEW_TITLE, ADVANCED_TITLE, ADVANCED_TEXT, ADVANCED_WARN, ADVANCED_PLAIN, TYPE_YES,
    TYPE_YES_FIRST, BTN_MAKE_IT, YES, SESSION_FAILED,
    SCR_PASSPHRASES, SCR_RESUME, SCR_REVIEW, SCR_RESTORE, DISKS_TITLE, DISKS_KEYS, PLANNING_TITLE, PLANNING, PLANNING_FIT, PLANNING_REVIEW, STATUS_TITLE, STATUS_TEXT, ROLE_SYSTEM, ROLE_NOTHING, BY_WWID, BY_IDENTITY, WILL_BE_ERASED, CHOSEN_BY_YOU, NO_TARGET, PLAIN_NO_TARGET, PLAIN_ERASED, NOT_USED, NOT_OFFERED, ALREADY_USED, BLANK, REFUSED_STICK, REFUSED_BACKUP, REFUSED_ROOT, REFUSED_IN_USE, REFUSED_READ_ONLY, REFUSED_SECTOR, BTN_OK, BTN_THESE_ARE_RIGHT, PLAN_FAILED_TITLE, PLAN_FAILED_WHY, PLAN_FAILED_TODO, NO_LAYOUT_TITLE, NO_LAYOUT_WHY, NO_LAYOUT_TODO, LAYOUT_NONE_TYPED, LAYOUT_NO_PATH, LAYOUT_NO_DEV, LAYOUT_UNREADABLE, ONE_DISK_KEYS, PUT_IT_ON, NO_SERIAL, NO_CANDIDATES, CANT_RESIZE_WHY, MUCH_SMALLER, A_LITTLE_SMALLER, TOO_SMALL, BIGGER, LITTLE_SMALLER_TITLE, LITTLE_BIGGER_TITLE, MUCH_SMALLER_TITLE, MUCH_BIGGER_TITLE, OFFER_KEYS, SLACK_TEXT, OR_ANOTHER, BTN_SHRINK, BTN_GROW, BTN_KEEP_SIZES, BTN_ANOTHER_DISK, DECIDE_WHY, BEYOND_LIMIT, CHOOSE_ANOTHER, CHOOSE_ONE_FIRST, PASS_NEW, PASS_KEPT_HEADER, PASS_RESUME, LABEL_PASSPHRASE, LABEL_AGAIN, PASSPHRASE_KEYS, PASS_EMPTY, PASS_MISMATCH, BOOT_KB_TITLE, KEY_BLOCKED_TITLE, KEY_FILE_ELSEWHERE, KEY_BLOCKED_TODO, RESUME_TITLE, STARTED_NOT_FINISHED, RESUME_CHOICES, BTN_GO_ON, BTN_START_AGAIN, REVIEW_TITLE, REVIEW_KEYS, REVIEW_ERASE, REVIEW_ERASE_PLAIN, WORD_ERASED, WORD_WRITTEN, WORD_GOES_ON, CHANGES, CHANGES_NONE, NO_PROBLEMS, THIS_STICK, NOTHING_ELSE, NOTHING_CHANGED, CHANGE_PASSPHRASE, NO_OTHER_FS, BTN_RESTORE, BTN_RESTORE_NOW, BTN_SHOW_WHAT, BTN_COMMANDS, BTN_EVERY_COMMAND, REVIEW_PROBLEM_TITLE, REVIEW_PROBLEM_WHY, ERASE_PROBLEM_WHY, REVIEW_PROBLEM_TODO, BTN_TO_DISKS, COMMANDS_TITLE, ADVANCED_FS_TITLE, ADVANCED_FS_TEXT, ADVANCED_FS_AFTER, AS_IT_WAS, CHANGED, CANT_CHANGE, BTN_UNDO_ALL,
];

#[cfg(test)]
mod tests {
    use super::*;
    use crate::text::Clean;

    #[test]
    fn every_string_is_clean_as_it_is() {
        for s in ALL {
            assert_eq!(Clean::text(s, usize::MAX).as_str(), *s);
            assert!(s.is_ascii(), "{s:?}: plain ASCII, so every console shows it");
        }
        for s in [
            machine_here("x", "y"),
            network_text("x", "y"),
            offer("lovelace", "German", "de, nodeadkeys", "English (US)"),
            report_in_ram("/run/bko-rescue/reports/x"),
            next("x", "y", true),
            old_disk(1, "Samsung SSD 870 EVO 500GB", "500 GB", ROLE_SYSTEM),
            shrink_text(true, "/", "465 GB", "445 GB", "4.3", Some("41.7 GB")),
            review_head("lovelace", Some("Debian 13"), "22 Nov 2026 02:00 CET", Some("61.2 GB"), false),
            boot_kb("lovelace", "German", "English (US)"),
        ] {
            assert_eq!(Clean::text(&s, usize::MAX).as_str(), s);
        }
    }
}
