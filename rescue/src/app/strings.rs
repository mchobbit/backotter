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

// ---- After Snapshot (RT12) ----
pub const NEXT_TITLE: &str = "Next: the disks";
pub fn next(name: &str, when: &str) -> String {
    format!("{name}'s backup of {when} is chosen.\n\nThe next screens, the disks and the restore itself, aren't in this bko-rescue yet.  Nothing has been changed on any disk.")
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
            next("x", "y"),
        ] {
            assert_eq!(Clean::text(&s, usize::MAX).as_str(), s);
        }
    }
}
