#!/usr/bin/env python3
"""bko-rescue's first screens (RT11) driven on a pty by drive.py, against
the fake bko-holt (scenarios/app-three: stick-three's recorded calls and
report-bug's) and the fake rustic-bko (rustic/NAME: make-app-fixtures.py),
in full screen (UTF-8 colour, ASCII on vt220) and plain mode.

    testvm/rescue/app-test.py PATH/TO/bko-rescue [ONLY ...]

What it checks: Machine to Snapshot with two wrong passwords and the
hint; the recovery key's words on a pipe and its password as a file; the
password file handed to rustic-bko (0600, in a new 0700 directory, no
newline, not in argv or the environment: the fake's checks) and wiped,
with its directory, once bko-rescue ends, SIGTERM included; a repository
that isn't the stick's, its help screen, and a bug report kept in RAM when
the stick's partition can't be mounted; Type a location with no stick;
the dry-run lock (BKO_HOLT_NO_COMMIT=1 in every bko-holt call, and a stop
when bko-holt doesn't confirm it); and the Network screen.  Every screen
goes through screen.py's interpreter, which fails on any sequence the
profile may not send; the screens named are compared with golden files in
testvm/rescue/screens/app-*.txt as BKO_RESCUE_GOLDEN says (check, report,
bless).  ONLY runs the tests whose names contain one of the words.  Exit 0
when every check passes.  Python's stdlib only.
"""

import json
import os
import shutil
import signal
import stat
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import drive   # noqa: E402
import screen  # noqa: E402

BIN = os.path.abspath(sys.argv[1]) if len(sys.argv) > 1 else "bko-rescue"
ONLY = sys.argv[2:]
HOLT = os.path.join(HERE, "fake-bko-holt")
RUSTIC = os.path.join(HERE, "fake-rustic-bko")
SCREENS = os.path.join(HERE, "screens")
GOLDEN = os.environ.get("BKO_RESCUE_GOLDEN", "check") or "check"
PASSWORD = "correct horse"
WORDS = "legal winner thank year wave sausage worth useful legal winner thank yellow"
FAILS = []
TMP = tempfile.mkdtemp(prefix="bko-rescue-app-test-")


def check(ok, what):
    print(("ok   " if ok else "FAIL ") + what)
    if not ok:
        FAILS.append(what)
    return ok


def golden(name, text):
    path = os.path.join(SCREENS, name + ".txt")
    if GOLDEN == "bless":
        old = open(path).read() if os.path.exists(path) else None
        if old != text:
            with open(path, "w") as f:
                f.write(text)
            print("blessed %s" % name)
        return
    if not os.path.exists(path):
        check(GOLDEN == "report", "golden %s: missing (BKO_RESCUE_GOLDEN=bless writes it)" % name)
        return
    kept = open(path).read()
    if kept == text:
        check(True, "golden %s" % name)
        return
    a, b = kept.split("\n"), text.split("\n")
    diff = ["%4d - %s\n%4d + %s" % (i + 1, a[i] if i < len(a) else None, i + 1, b[i] if i < len(b) else None)
            for i in range(max(len(a), len(b))) if (a[i] if i < len(a) else None) != (b[i] if i < len(b) else None)]
    print("\n".join(diff[:12]))
    check(GOLDEN == "report", "golden %s matches" % name)


class Run:
    """One bko-rescue with the fakes, its own RAM directory and log."""

    def __init__(self, name, args=(), holt="app-three", rustic="lovelace", term="xterm", reach=True, rows=24, cols=80, fake_mount=False, state=False):
        self.name = name
        self.dir = os.path.join(TMP, name)
        os.makedirs(self.dir, mode=0o700)
        self.run_dir = os.path.join(self.dir, "run")
        self.log = os.path.join(self.dir, "fake.log")
        # bko-holt's RUN_DIR as the fake has it: its lock goes there.
        self.holt_run = os.path.join(self.dir, "holt-run")
        os.makedirs(self.holt_run, mode=0o700)
        env = {
            "TERM": term,
            "BKO_RESCUE_RUN_DIR": self.run_dir,
            "FAKE_BKO_HOLT_SCENARIO": os.path.join(HERE, "scenarios", holt),
            "FAKE_BKO_RUSTIC_SCENARIO": os.path.join(HERE, "rustic", rustic),
            "FAKE_BKO_HOLT_LOG": self.log,
            "FAKE_BKO_HOLT_REMOVE_SECS": "120",
            "FAKE_BKO_HOLT_RUN_DIR": self.holt_run,
        }
        if state:
            # Calls answered in turn, as a recorded run's are.
            env["FAKE_BKO_HOLT_STATE"] = os.path.join(self.dir, "state")
        if reach:
            env["BKO_RESCUE_ASSUME_REACHABLE"] = "1"
        if fake_mount:
            # A mount that refuses every mount (fake-bin/mount): the
            # restore log's read-write one fails, so the log stays in RAM.
            env["PATH"] = os.path.join(HERE, "fake-bin") + ":/usr/bin:/bin"
        self.d = drive.Drive(BIN, ["--holt", HOLT, "--rustic", RUSTIC] + list(args), rows, cols, env, timeout=10)

    def says(self, phrase):
        t = self.d.dump() if self.d.plain else "".join(" " if c in screen.BOX else c for c in self.d.screen.text())
        flat = lambda x: " ".join(x.split())
        return flat(phrase) in flat(t)

    def wait_says(self, phrase, secs=10):
        end = time.time() + secs
        while not self.says(phrase):
            if time.time() > end or self.d.hung_up:
                raise drive.Failed("waited for %r" % phrase)
            self.d.pump(0.05)

    def calls(self):
        if not os.path.exists(self.log):
            return []
        with open(self.log) as f:
            return [json.loads(l) for l in f if l.strip()]

    def finish(self, code=0):
        got = self.d.exit(10)
        check(got == code, "%s: exit %d (%s %s)" % (self.name, code, got, self.d.err.strip()[-300:]))
        check(self.d.settings_back(), "%s: the terminal's settings put back" % self.name)
        if not self.d.plain:
            check(self.d.screen.cursor_shown, "%s: the cursor shown at the end" % self.name)
        self.d.close()
        # The session's directory is gone; only reports may stay.
        left = [n for n in os.listdir(self.run_dir) if n != "reports"] if os.path.isdir(self.run_dir) else []
        check(left == [], "%s: the session's directory removed (%s)" % (self.name, left))

    def secrets_checked(self, n_password_files, commit_ok=False):
        """Every call passed the fake's checks, and each key file it was
        given was removed: those that went at once, and the session's,
        when bko-rescue ended."""
        end = time.time() + 5
        while True:
            ls = self.calls()
            # A report and the layout are OUTs that are kept: not secrets.
            keys = {k["path"] for l in ls if "argv" in l and l["argv"][:1] not in (["report-bug"], ["stick"], ["snapshot-layout"]) for k in l.get("keys", [])}
            gone = {l["removed"] for l in ls if "removed" in l}
            if keys <= gone or time.time() > end:
                break
            time.sleep(0.1)
        calls = [l for l in ls if "argv" in l]
        check(all(c["ok"] and c["checks"] == [] for c in calls), "%s: every call passed the fake's checks (%s)" % (
            self.name, [(c["argv"][:2], c["checks"]) for c in calls if not c["ok"] or c["checks"]]))
        pw = [c for c in calls if "--password-file" in c["argv"]]
        if n_password_files is not None:
            check(len(pw) == n_password_files, "%s: rustic-bko given a password file %d times (%d)" % (self.name, n_password_files, len(pw)))
        check(keys <= gone, "%s: every key and password file removed (%s left)" % (self.name, sorted(keys - gone)))
        if not commit_ok:
            for c in calls:
                check("--commit" not in c["argv"], "%s: no --commit in %s" % (self.name, c["argv"][:2]))
        return calls


def to_password(r):
    d = r.d
    d.wait("Which machine is this?")
    d.keys("Down", "Enter")
    d.wait("The backup password")


def password(r, pw, then):
    r.d.type(pw)
    r.d.keys("Enter")
    r.wait_says(then)


def test_full_utf8_colour():
    r = Run("full")
    d = r.d
    d.wait("Which machine is this?")
    golden("app-machine.utf8-colour", d.dump())
    d.keys("Down")
    golden("app-machine-chosen.utf8-colour", d.dump())
    d.keys("Enter")
    d.wait("The backup password")
    golden("app-password.utf8-colour", d.dump())
    password(r, "wrong one", "Tries so far: 1")
    password(r, "wrong two", "Your hint, saved when the backups were set up")
    check(r.says('"the boat\'s name, then the year we moved"'), "full: the hint after two wrong passwords")
    golden("app-password-hint.utf8-colour", d.dump())
    check(b"wrong one" not in d.out and PASSWORD.encode() not in d.out, "full: no password ever sent to the screen")
    password(r, PASSWORD, "Which backup?")
    check(r.says("(*) 22 Nov 2026 02:00 UTC+01:00 61.2 GB newest a7f3c901"), "full: the newest backup chosen, its own offset")
    golden("app-snapshot.utf8-colour", d.dump())
    d.keys("Down", "Enter")
    # Disks: lovelace's system.json on this stick carries no layout, and
    # bko-holt's stick --copy says so (stick-three's recorded refusal).
    d.wait("Problem: the old disks' layout isn't here")
    check(r.says("bko-holt couldn't copy the layout off the BACKOTTER partition"), "full: the layout's copy is bko-holt's")
    copies = [c for c in r.calls() if c.get("argv", [])[:2] == ["stick", "--copy"]]
    check(len(copies) == 1 and copies[0]["call"] == "08-copy" and copies[0]["argv"][3].endswith("/layout.json"),
          "full: one stick --copy into the session (%s)" % [c.get("argv") for c in copies])
    d.signal(signal.SIGTERM)
    r.finish(143)
    calls = r.secrets_checked(4)
    pw_calls = [c["call"] for c in calls if "--password-file" in c["argv"]]
    check(pw_calls == ["09-wrong-config", "09-wrong-config", "01-config", "02-snapshots"], "full: two wrong passwords, then the right one (%s)" % pw_calls)


def test_full_ascii_vt220():
    r = Run("vt220", term="vt220")
    r.d.wait("Which machine is this?")
    golden("app-machine.ascii-vt220", r.d.dump())
    # The list, then OK: Tab twice to < Type a location >.
    r.d.keys("Tab", "Tab", "Enter")
    r.d.wait("Where is the backup?")
    golden("app-location.ascii-vt220", r.d.dump())
    r.d.keys("Esc")
    r.d.wait("Which machine is this?")
    r.d.signal(signal.SIGTERM)
    r.finish(143)


def test_recovery_key():
    r = Run("recovery")
    to_password(r)
    # From the field past OK (and < Keyboard >, on a screen console).
    for _ in range(4):
        if r.says("Enter: Use recovery key instead"):
            break
        r.d.keys("Tab")
    check(r.says("Enter: Use recovery key instead"), "recovery: the focus on < Use recovery key instead >")
    r.d.keys("Enter")
    r.d.wait("Recovery key")
    r.d.type("lega winn ribbx")
    r.wait_says("Word 3 isn't one of the recovery words.")
    for _ in range(5):
        r.d.keys("BS")
    r.d.type("thank year wave saus wort usef legal winn thank yell")
    golden("app-recovery.utf8-colour", r.d.dump())
    r.d.keys("Enter")
    r.d.wait("Which backup?")
    r.d.keys("Enter")
    r.d.wait("Problem: the old disks' layout isn't here")
    r.d.signal(signal.SIGTERM)
    r.finish(143)
    calls = r.secrets_checked(2)
    rec = [c for c in calls if c["argv"][:1] == ["recovery-words"]]
    check(len(rec) == 1 and rec[0]["call"] == "03-recovery-good", "recovery: the words checked by bko-holt (%s)" % [c["call"] for c in rec])
    check(rec and WORDS not in " ".join(rec[0]["argv"]), "recovery: the words not in argv")
    out = rec[0]["argv"][1] if rec else ""
    used = [c for c in calls if "--password-file" in c["argv"]]
    check(all(c["argv"][c["argv"].index("--password-file") + 1] == out for c in used), "recovery: rustic-bko given bko-holt's OUT as the password file")


def test_plain():
    r = Run("plain", args=["--plain"])
    d = r.d
    d.wait("Which machine is this?")
    d.wait("Choose [1-3, t, s]: ")
    d.type("1", line=True)
    d.wait("Type the backup password, or press Enter alone for other choices:")
    m = d.mark()
    d.type("nope", line=True)
    d.wait("That password didn't open the backups.", since=m)
    m = d.mark()
    d.type("nope again", line=True)
    d.wait("Your hint, saved when the backups were set up:", since=m)
    m = d.mark()
    d.type(PASSWORD, line=True)
    d.wait("Choose [1-14, b]: ", since=m)
    d.type("1", line=True)
    # Disks: bko-holt's stick --copy finds no layout in lovelace's system.json.
    d.wait("Problem: the old disks' layout isn't here")
    d.wait("Choose [")
    # Plain mode raises the signal again once its files are gone.
    d.signal(signal.SIGTERM)
    r.finish(-15)
    text = d.dump()
    check("\x1b" not in text, "plain: no escape sequences")
    check("nope" not in text and PASSWORD not in text, "plain: nothing typed for a password shows")
    # Up to Disks' problem: its text quotes bko-holt's refusal.
    cut = text.find("Problem: the old disks' layout isn't here")
    golden("app-plain", text[:text.index("\n", cut) + 1] if cut >= 0 else text)
    r.secrets_checked(4)


def test_plain_signals():
    # The files wiped first, then the signal ends it as it would have.
    for sig, code in ((signal.SIGTERM, -15), (signal.SIGHUP, -1), (signal.SIGINT, -2)):
        r = Run("plain-sig-%d" % sig, args=["--plain"])
        d = r.d
        d.wait("Choose [1-3, t, s]: ")
        d.type("1", line=True)
        d.wait("Type the backup password, or press Enter alone for other choices:")
        d.type(PASSWORD, line=True)
        d.wait("Choose [1-14, b]: ")
        d.signal(sig)
        r.finish(code)
        r.secrets_checked(2)


def test_mismatch_and_report():
    r = Run("report", rustic="mismatch")
    to_password(r)
    password(r, PASSWORD, "Problem: these aren't this machine's backups")
    check(r.says("Your backup is safe: nothing in it has been changed."), "report: the backup-is-safe line")
    golden("app-problem-mismatch.utf8-colour", r.d.dump())
    r.d.keys("r")
    r.d.wait("Report saved", 20)
    # The fake's stick names /dev/sdc3; as a user, mount fails: RAM.
    check(r.says("in this computer's memory only"), "report: kept in RAM when the stick can't be mounted")
    reports = os.path.join(r.run_dir, "reports")
    files = sorted(os.listdir(reports)) if os.path.isdir(reports) else []
    check(len(files) == 1 and files[0].startswith("bko-report-") and files[0].endswith(".txt"), "report: one report in RAM (%s)" % files)
    if files:
        st = os.stat(os.path.join(reports, files[0]))
        check(stat.S_IMODE(st.st_mode) == 0o600 and stat.S_IMODE(os.stat(reports).st_mode) == 0o700, "report: 0600 in a 0700 directory")
    r.d.keys("v")
    r.d.wait("BackOtter bug report (fake-bko-holt's)")
    r.d.keys("Esc")
    r.d.wait("Report saved")
    # < Advanced > has no letter: Right twice from OK.
    r.d.keys("Right", "Right", "Enter")
    r.d.wait("Advanced: a report that isn't redacted")
    r.d.keys("Enter")
    r.wait_says("Type YES to make it, or Esc to go back.")
    r.d.type("yes")
    r.d.keys("Enter")
    r.d.wait("Both reports are saved")
    files = sorted(os.listdir(reports)) if os.path.isdir(reports) else []
    check(len(files) == 2 and any(f.endswith("-UNREDACTED.txt") for f in files), "report: its unredacted twin beside it (%s)" % files)
    r.d.keys("Enter")
    r.d.wait("Problem: these aren't this machine's backups")
    r.d.keys("Esc")
    r.d.wait("The backup password")
    r.d.signal(signal.SIGTERM)
    r.finish(143)
    calls = r.secrets_checked(1)
    bugs = [c for c in calls if c["argv"][:1] == ["report-bug"]]
    check(len(bugs) == 2 and "--strip-file" in bugs[0]["argv"] and "--no-redact" in bugs[1]["argv"], "report: report-bug redacted (with the hint stripped), then --no-redact")


def test_location_with_no_stick():
    r = Run("location", holt="stick-missing", rustic="shared")
    d = r.d
    d.wait("Where is the backup?")
    check(r.says("its BACKOTTER partition, which holds its settings, is missing."), "location: says why it came up")
    golden("app-location-missing.utf8-colour", d.dump())
    d.type("sftp://nas.local/backotter/shared")
    d.keys("Enter")
    d.wait("The backup password")
    password(r, PASSWORD, "Which machine?")
    golden("app-which.utf8-colour", d.dump())
    d.keys("Down", "Down", "Enter")
    d.wait("Which backup?")
    check(r.says("babbage's backups, newest first:"), "location: the machine picked")
    d.keys("Esc")
    d.wait("Which machine?")
    d.signal(signal.SIGTERM)
    r.finish(143)
    r.secrets_checked(2)


def test_dry_run():
    # The fake's api says no_commit false: bko-rescue stops at once.
    r = Run("dry-unlocked", args=["--dry-run"])
    r.d.wait("Problem: this stick's bko-holt")
    check(r.says("didn't confirm its lock (BKO_HOLT_NO_COMMIT)"), "dry run: stops when bko-holt doesn't confirm the lock")
    r.d.signal(signal.SIGTERM)
    r.finish(143)
    calls = [c for c in r.calls() if "argv" in c]
    check(calls and all(c["no_commit_env"] == "1" for c in calls), "dry run: BKO_HOLT_NO_COMMIT=1 for bko-holt")
    # app-dry's api confirms it: on to the screens.
    r = Run("dry", args=["--dry-run"], holt="app-dry")
    to_password(r)
    check(r.says("DRY RUN: nothing will be written"), "dry run: the banner on the password screen")
    password(r, PASSWORD, "Which backup?")
    check(r.says("DRY RUN: nothing will be written"), "dry run: the banner on Snapshot")
    r.d.signal(signal.SIGTERM)
    r.finish(143)
    calls = r.secrets_checked(2)
    holt = [c for c in calls if c["argv"][:1] in (["--version"], ["stick"])]
    check(len(holt) == 2 and all(c["no_commit_env"] == "1" for c in holt), "dry run: every bko-holt call under the lock")


def disks_to_review(r, dry=False):
    """Machine to Review on app-lovelace: old disk 1 on the Crucial (much
    bigger: the old sizes kept), the passphrase twice, the serials."""
    d = r.d
    to_password(r)
    password(r, PASSWORD, "Which backup?")
    d.keys("Enter")
    d.wait("Where each disk goes")
    check(r.says("-> no disk chosen yet"), "disks: old disk 1 has no disk yet")
    golden("app-disks.utf8-colour" if not dry else "app-disks-dry.utf8-colour", d.dump())
    d.keys("Enter")
    d.wait("Old disk 1 of 2: where does it go?")
    check(r.says("500 GB bigger: left free") and r.says("20.0 GB smaller: / would shrink from 465 GB to 445 GB"), "disks: each candidate's size in words")
    d.keys("Enter")
    d.wait("This disk is much bigger")
    d.keys("Enter")
    r.wait_says("chosen by you")
    d.keys("Enter")
    d.wait("Passphrase 1 of 1")
    d.type("two words")
    d.keys("Enter")
    d.type("two words")
    d.keys("Enter")
    d.wait("Ready to restore")
    golden("app-review.utf8-colour" if not dry else "app-review-dry.utf8-colour", d.dump())
    check(b"two words" not in d.out, "disks: the passphrase never sent to the screen")
    # A wrong serial is named; c is typed into the field, not Commands.
    d.type("c00000")
    d.keys("Enter")
    d.type("xk7hpl")
    d.keys("Enter", "Enter")
    r.wait_says("isn't the end of its serial")
    for _ in range(6):
        d.keys("BS")
    d.type("F3A9C1")
    # Confirmed: the focus on < Restore > (the Restore itself is
    # test_restore_*'s, on scenarios that answer its run).
    d.keys("Enter", "Enter")
    d.pump(0.3)
    check(r.says("Ready to restore") and not r.says("isn't the end of its serial"), "disks: both serials confirmed")


def test_disks_to_review():
    r = Run("disks", holt="app-lovelace", fake_mount=True)
    disks_to_review(r)
    r.d.signal(signal.SIGTERM)
    r.finish(143)
    # rustic-bko's two, and the six loads'.
    calls = r.secrets_checked(8)
    loads = [c for c in calls if c["argv"][:1] == ["load"]]
    check(len(loads) == 6, "disks: six plans and statuses (%d)" % len(loads))
    keyed = [c for c in loads if any(a.startswith("cryptroot=") for a in c["argv"])]
    check(len(keyed) == 2 and all(any(k["path"].endswith("/k0") for k in c["keys"]) for c in keyed), "disks: the passphrase as a key file, checked by the fake (%s)" % [c["keys"] for c in keyed])
    # The layout: bko-holt's copy off the stick, once, the file every load reads.
    copies = [c for c in calls if c["argv"][:2] == ["stick", "--copy"]]
    out = copies[0]["argv"][3] if len(copies) == 1 else None
    check(len(copies) == 1 and copies[0]["call"] == "03-copy" and all(c["argv"][1] == out for c in loads),
          "disks: one stick --copy, its OUT every load's layout (%s)" % [c["argv"][:4] for c in copies])


def test_disks_dry_run():
    r = Run("disks-dry", args=["--dry-run"], holt="app-lovelace-dry", fake_mount=True)
    disks_to_review(r, dry=True)
    check(r.says("DRY RUN: nothing will be written"), "dry run: the banner on the last screen")
    r.d.signal(signal.SIGTERM)
    r.finish(143)
    calls = r.secrets_checked(8)
    check(all(c["no_commit_env"] == "1" for c in calls if "argv" in c and c["argv"][:1] != ["--repository"]), "dry run: every bko-holt call under the lock")
    review = [c for c in calls if c["argv"][:1] == ["load"] and "--erase" in c["argv"] and any(a.startswith("cryptroot=") for a in c["argv"])]
    check(review != [], "dry run: Review's plan passes --erase (J6)")


def test_disks_kept_headers():
    """A layout that kept LUKS2 headers (open question 12): bko-holt
    luks-headers fetches them into the session's h/ after the password and
    the backup, before the first plan; every load gets --luks-header-dir;
    the headers (watched by the fake as key files are) go with the session."""
    r = Run("disks-luks", holt="app-lovelace-luks", fake_mount=True)
    d = r.d
    to_password(r)
    password(r, PASSWORD, "Which backup?")
    d.keys("Enter")
    d.wait("Where each disk goes")
    r.d.signal(signal.SIGTERM)
    r.finish(143)
    calls = r.secrets_checked(None)
    fetch = [c for c in calls if c["argv"][:1] == ["luks-headers"]]
    check(len(fetch) == 1 and fetch[0]["call"] == "04-luks-headers", "headers: one fetch (%s)" % [c["argv"][:3] for c in fetch])
    if not fetch:
        return
    a = fetch[0]["argv"]
    hdir = a[2]
    check(hdir.startswith(r.run_dir + "/") and hdir.endswith("/h") and a[1] == os.path.join(os.path.dirname(hdir), "layout.json"),
          "headers: DIR the session's h/, beside its layout (%s)" % a[:3])
    check(a[a.index("--snapshot") + 1] != "latest" and "--json" in a, "headers: the backup chosen, as JSON (%s)" % a)
    check(len([k for k in fetch[0]["keys"] if k.get("out")]) == 2, "headers: both files written by the fake (%s)" % fetch[0]["keys"])
    loads = [c for c in calls if c["argv"][:1] == ["load"]]
    check(loads != [] and all(c["argv"][c["argv"].index("--luks-header-dir") + 1] == hdir for c in loads if "--luks-header-dir" in c["argv"])
          and all("--luks-header-dir" in c["argv"] for c in loads), "headers: every load given --luks-header-dir %s" % hdir)
    check(loads != [] and calls.index(fetch[0]) < calls.index(loads[0]), "headers: fetched before the first plan")
    check(not os.path.exists(hdir), "headers: gone with the session")


def test_disks_older_backup():
    """An older backup of a machine that kept LUKS2 headers (open question
    15): Disks asks that snapshot for its own layout (snapshot-layout, the
    machine's install ID), fetches its headers from the same snapshot, then
    plans from that layout; the stick's copy is asked for only after it, to
    compare (the same layout: no line), into a file of its own, wiped."""
    r = Run("disks-luks-older", holt="app-lovelace-luks-older", fake_mount=True)
    d = r.d
    to_password(r)
    password(r, PASSWORD, "Which backup?")
    d.keys("Down", "Enter")
    d.wait("Where each disk goes")
    check(not r.says("differs from the stick's copy") and not r.says("the stick's copy is used"), "older: the same layout, no line")
    r.d.signal(signal.SIGTERM)
    r.finish(143)
    calls = r.secrets_checked(None)
    own = [c for c in calls if c["argv"][:1] == ["snapshot-layout"]]
    fetch = [c for c in calls if c["argv"][:1] == ["luks-headers"]]
    check(len(own) == 1 and own[0]["call"] == "05-snapshot-layout", "older: one snapshot-layout (%s)" % [c["argv"][:2] for c in own])
    copies = [c for c in calls if c["argv"][:2] == ["stick", "--copy"]]
    check(len(copies) <= 1 and all(calls.index(c) > calls.index(own[0]) and c["argv"][3].endswith("/stick-layout.json") and not os.path.exists(c["argv"][3]) for c in copies) if own else False,
          "older: the stick's copy only to compare, after the backup's, wiped (%s)" % [c["argv"][3:4] for c in copies])
    if not own or not fetch:
        check(False, "older: a layout and a fetch (%d, %d)" % (len(own), len(fetch)))
        return
    a, f = own[0]["argv"], fetch[0]["argv"]
    snap = a[a.index("--snapshot") + 1]
    check(snap == f[f.index("--snapshot") + 1] and snap != "latest" and not snap.startswith("a7f3c901"),
          "older: the layout and the headers from the backup chosen, not the newest (%s, %s)" % (snap[:8], f[f.index("--snapshot") + 1][:8]))
    check(a[a.index("--install-id") + 1] == "0b7e3c52-8d1f-4a6e-9c3b-2f5d7a1e4b90", "older: the chosen machine's install ID (%s)" % a)
    check(a[1].startswith(r.run_dir + "/") and a[1].endswith("/layout.json") and f[1] == a[1], "older: the headers fetched against its layout (%s, %s)" % (a[1], f[1]))
    loads = [c for c in calls if c["argv"][:1] == ["load"]]
    check(loads != [] and all(c["argv"][1] == a[1] for c in loads), "older: every load reads the snapshot's own layout")
    check(calls.index(own[0]) < calls.index(fetch[0]) < calls.index(loads[0]) if loads else False, "older: layout, then headers, then the first plan")
    check(not os.path.exists(a[1]), "older: the layout gone with the session")


def test_network():
    r = Run("network", reach=False)
    d = r.d
    d.wait("Which machine is this?")
    d.keys("Down", "Enter")
    end = time.time() + 15
    while not (r.says("No network") or r.says("The backup password")) and time.time() < end:
        d.pump(0.1)
    if r.says("The backup password"):
        print("skip network: nas.local answers here")
    else:
        check(r.says("lovelace's backups are on nas.local, which this computer can't reach."), "network: the notice")
        check(r.says("Wi-Fi can't be used from the rescue stick."), "network: wired only")
        d.keys("Esc")
        d.wait("Which machine is this?")
    d.signal(signal.SIGTERM)
    r.finish(143)


def raw_until(d, want, secs=10):
    """The pty's bytes as they are (a shell's, not bko-rescue's), until
    `want` is among them."""
    buf, end = b"", time.time() + secs
    while want not in buf:
        if time.time() > end:
            raise drive.Failed("waited for %r in %r" % (want, buf[-200:]))
        b = d.t.read(0.1)
        if b:
            d.out += b
            buf += b
    return buf


def test_shell_and_back():
    # Full screen: s hands the terminal to a shell; exit comes back to the
    # same screen, drawn again in full.
    r = Run("shell")
    d = r.d
    d.wait("Which machine is this?")
    d.screen.end()
    os.write(d.t.master, b"s")
    raw_until(d, b"Type exit to go back to bko-rescue.")
    os.write(d.t.master, b"echo shell-$((6*7))\n")
    raw_until(d, b"shell-42")
    os.write(d.t.master, b"exit\n")
    # The full screen again, from its first frame: the shell's echo
    # before it is the shell's.
    buf = raw_until(d, b"\x1b[")
    d.screen = screen.Screen(24, 80, d.rules)
    d.screen.feed(buf[buf.index(b"\x1b["):])
    d.wait("Which machine is this?")
    d.keys("Down", "Enter")
    d.wait("The backup password")
    d.signal(signal.SIGINT)
    r.finish(130)
    # Plain mode: the same, at its own prompt.
    r = Run("shell-plain", args=["--plain"])
    d = r.d
    d.wait("Choose [1-3, t, s]: ")
    d.type("s", line=True)
    d.wait("Type exit to go back to bko-rescue.")
    m = d.mark()
    d.type("exit", line=True)
    d.wait("Choose [1-3, t, s]: ", since=m)
    d.signal(signal.SIGTERM)
    r.finish(-15)


def rec_to_review(r, passphrase="for md1"):
    """Machine to Review on an app-rec-* scenario (debian13-stack's recorded
    disks, both matched): the passphrase twice, the two disks' size and
    name typed (neither has a serial)."""
    d = r.d
    to_password(r)
    password(r, PASSWORD, "Which backup?")
    d.keys("Enter")
    d.wait("Where each disk goes")
    # Both matched: the focus is on OK.
    d.keys("Enter")
    d.wait("Passphrase 1 of 1")
    d.type(passphrase)
    d.keys("Enter")
    d.type(passphrase)
    d.keys("Enter")
    d.wait("Ready to restore")
    d.type("12.9GBvdb")
    d.keys("Enter")
    d.type("12.9GBvdc")
    d.keys("Enter")


def load_calls(r):
    return [c for c in r.calls() if "argv" in c and c["argv"][:1] == ["load"]]


def test_restore_to_done():
    r = Run("restore", holt="app-rec-restore", fake_mount=True, state=True)
    d = r.d
    rec_to_review(r)
    d.keys("Enter")
    d.wait("Restoring lovelace")
    check(r.says("Leave the computer on, with the stick and the network cable in"), "restore: the line under the gauge")
    r.wait_says("Now: copying your files back", 30)
    check(r.says("[x] Make partitions") and r.says("[>] Copy your files back"), "restore: the stages ticked as they end")
    r.wait_says("of 0.71 GB", 10)
    d.wait("lovelace is restored", 40)
    check(r.says("[x] Make it bootable") and r.says("[x] Finish"), "done: every stage ticked")
    check(r.says("At the next start, type the passphrase you chose for the disks."), "done: the passphrase line")
    check(b"for md1" not in d.out, "restore: the passphrase never on the screen")
    d.signal(signal.SIGTERM)
    r.finish(143)
    calls = r.secrets_checked(None, commit_ok=True)
    runs = [c for c in calls if c["argv"][:1] == ["load"] and "--commit" in c["argv"]]
    check(len(runs) == 1 and "--expect-plan" in runs[0]["argv"] and runs[0]["argv"][3:5] == ["--through", "close"], "restore: one run, --through close --commit --expect-plan (%s)" % [c["argv"][3:5] for c in runs])
    check(runs and runs[0]["no_commit_env"] is None, "restore: no BKO_HOLT_NO_COMMIT in a restore")
    check(runs and any(k["path"].endswith("/k0") for k in runs[0]["keys"]), "restore: the passphrase as a key file, checked by the fake")
    check(not os.path.exists(os.path.join(r.holt_run, "load.lock")), "restore: the fake's lock gone at its end")


def test_restore_dry_run():
    r = Run("restore-dry", args=["--dry-run"], holt="app-rec-dry", fake_mount=True, state=True)
    d = r.d
    rec_to_review(r)
    check(r.says("< Show what would happen >"), "dry run: Review's button")
    d.keys("Enter")
    d.wait("What restoring lovelace would do")
    check(r.says("DRY RUN: nothing will be written") and r.says("Nothing is written: each command is shown, not run."), "dry run: the banner and the line under the gauge")
    d.wait("The dry run of lovelace is done", 30)
    check(r.says("Nothing was written: 92 commands would have run, and none did."), "dry run: every command would run")
    check(r.says("[~] Make it bootable") and r.says("[~] Finish"), "dry run: boot and close too (J11)")
    check(not r.says("< Restart >") and not r.says("< Power off >"), "dry run: no Restart, no Power off")
    golden("app-dry-done.utf8-colour", d.dump())
    d.keys("l")
    d.wait("The commands that would run")
    check(r.says("partition 1/25: wipefs"), "dry run: the commands, in order")
    d.keys("Esc")
    d.wait("The dry run of lovelace is done")
    d.keys("Right", "Enter")
    r.finish(0)
    calls = r.secrets_checked(None)
    check(all(c["no_commit_env"] == "1" for c in calls if c["argv"][:1] in (["load"], ["--version"], ["stick"])), "dry run: every bko-holt call under the lock")
    dry = [c for c in load_calls(r) if c["argv"][3:5] == ["--through", "close"]]
    check(len(dry) == 1 and dry[0]["argv"].count("--erase") == 2, "dry run: the run, --erase but no --commit (J6)")


def test_restore_fail_help_try_again():
    r = Run("restore-fail", holt="app-rec-fail", fake_mount=True, state=True)
    d = r.d
    rec_to_review(r)
    d.keys("Enter")
    d.wait('Problem: the restore stopped at "Make it bootable"', 40)
    check(r.says("grub-install failed (exit status 1):"), "failure: the stage's error")
    check(r.says("Your backup is safe: nothing in it has been changed."), "failure: the backup-is-safe line")
    check(r.says("[!] Make it bootable") and r.says("[x] Copy your files back"), "failure: the stages")
    golden("app-failure.utf8-colour", d.dump())
    d.keys("h")
    d.wait("Problem: the boot loader couldn't be installed")
    check(r.says("< Install without an entry >") and r.says("Esc: back"), "help: the fix, and Esc back")
    golden("app-help-boot.utf8-colour", d.dump())
    d.keys("o")
    d.wait("What the command said")
    check(r.says("Ended with: exit status 1"), "help: raw output's exit status")
    d.keys("Esc")
    d.wait("Problem: the boot loader couldn't be installed")
    d.keys("Esc")
    d.wait('Problem: the restore stopped at "Make it bootable"')
    # Try again: the button after Help.
    d.keys("Right", "Enter")
    d.wait("lovelace is restored", 40)
    d.signal(signal.SIGTERM)
    r.finish(143)
    runs = [c["argv"][3:5] for c in load_calls(r) if "--commit" in c["argv"]]
    check(runs == [["--through", "close"], ["--only", "boot"], ["--only", "close"]], "try again: boot and close, each alone (%s)" % runs)
    r.secrets_checked(None, commit_ok=True)


def test_restore_stop():
    r = Run("restore-stop", holt="app-rec-stop", fake_mount=True, state=True)
    d = r.d
    rec_to_review(r)
    d.keys("Enter")
    r.wait_says("Now: copying your files back", 30)
    check(os.path.exists(os.path.join(r.holt_run, "load.lock")), "stop: the fake holds its lock while it runs")
    # Enter does nothing until Tab reaches Cancel; Esc does nothing.
    d.keys("Enter", "Esc")
    check(r.says("Now: copying your files back"), "stop: Enter and Esc do nothing")
    d.keys("Tab", "Enter")
    d.wait("Stop the restore?")
    check(r.says("Your files are being copied back.  Stop interrupts the copy now"), "stop: the copy's words")
    golden("app-stop.utf8-colour", d.dump())
    d.keys("Esc")
    r.wait_says("Now: copying your files back")
    d.keys("Tab", "Enter")
    d.wait("Stop the restore?")
    d.keys("Tab", "Enter")
    d.wait("Stopped by you.", 20)
    check(r.says('Problem: the restore stopped at "Copy your files back"') and r.says("[!] Copy your files back"), "stop: Failure says where")
    golden("app-stopped.utf8-colour", d.dump())
    check(not os.path.exists(os.path.join(r.holt_run, "load.lock")), "stop: the lock gone at the stopped run's end")
    d.signal(signal.SIGTERM)
    r.finish(143)
    r.secrets_checked(None, commit_ok=True)


def test_restore_plain():
    r = Run("restore-plain", args=["--plain"], holt="app-rec-restore", fake_mount=True, state=True)
    d = r.d
    d.wait("Choose [1-3, t, s]: ")
    d.type("1", line=True)
    d.wait("Type the backup password, or press Enter alone for other choices:")
    d.type(PASSWORD, line=True)
    d.wait("Choose [1-14, b]: ")
    d.type("1", line=True)
    d.wait("Choose [1-2, o, b, s]: ")
    d.type("o", line=True)
    d.wait("Passphrase: ")
    d.type("for md1", line=True)
    d.wait("Type it again: ")
    d.type("for md1", line=True)
    d.wait("or b to go back: ")
    m = d.mark()
    d.type("12.9 GB vdb", line=True)
    d.wait("Disk 2 has no serial of its own", since=m)
    d.type("12.9 GB vdc", line=True)
    d.wait("Choose [r, c, a, b]: ")
    d.type("r", line=True)
    d.wait("Type c and Enter to stop (it asks first), or l and Enter to see the log.")
    d.wait("lovelace is restored", 40)
    d.wait("Choose [r, s, p]: ")
    text = d.dump()
    check("\x1b" not in text and "for md1" not in text, "plain restore: no escapes, no passphrase")
    check("[x] Copy your files back: done in" in text, "plain restore: a line per stage")
    d.signal(signal.SIGTERM)
    r.finish(-15)
    r.secrets_checked(None, commit_ok=True)


TESTS = [test_shell_and_back, test_full_utf8_colour, test_full_ascii_vt220, test_recovery_key, test_plain, test_plain_signals,
         test_mismatch_and_report, test_location_with_no_stick, test_dry_run, test_network, test_disks_to_review, test_disks_dry_run, test_disks_kept_headers, test_disks_older_backup,
         test_restore_to_done, test_restore_dry_run, test_restore_fail_help_try_again, test_restore_stop, test_restore_plain]


def main():
    try:
        for t in TESTS:
            if ONLY and not any(w in t.__name__ for w in ONLY):
                continue
            print("--- %s" % t.__name__)
            try:
                t()
            except drive.Failed as e:
                check(False, "%s: %s" % (t.__name__, e))
    finally:
        shutil.rmtree(TMP, ignore_errors=True)
    print("%d failed" % len(FAILS))
    for f in FAILS:
        print("  " + f)
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
