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

    def __init__(self, name, args=(), holt="app-three", rustic="lovelace", term="xterm", reach=True, rows=24, cols=80):
        self.name = name
        self.dir = os.path.join(TMP, name)
        os.makedirs(self.dir, mode=0o700)
        self.run_dir = os.path.join(self.dir, "run")
        self.log = os.path.join(self.dir, "fake.log")
        env = {
            "TERM": term,
            "BKO_RESCUE_RUN_DIR": self.run_dir,
            "FAKE_BKO_HOLT_SCENARIO": os.path.join(HERE, "scenarios", holt),
            "FAKE_BKO_RUSTIC_SCENARIO": os.path.join(HERE, "rustic", rustic),
            "FAKE_BKO_HOLT_LOG": self.log,
            "FAKE_BKO_HOLT_REMOVE_SECS": "120",
        }
        if reach:
            env["BKO_RESCUE_ASSUME_REACHABLE"] = "1"
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

    def secrets_checked(self, n_password_files):
        """Every call passed the fake's checks, and each key file it was
        given was removed: those that went at once, and the session's,
        when bko-rescue ended."""
        end = time.time() + 5
        while True:
            ls = self.calls()
            # A report is an OUT that is kept: not a secret.
            keys = {k["path"] for l in ls if "argv" in l and l["argv"][:1] != ["report-bug"] for k in l.get("keys", [])}
            gone = {l["removed"] for l in ls if "removed" in l}
            if keys <= gone or time.time() > end:
                break
            time.sleep(0.1)
        calls = [l for l in ls if "argv" in l]
        check(all(c["ok"] and c["checks"] == [] for c in calls), "%s: every call passed the fake's checks (%s)" % (
            self.name, [(c["argv"][:2], c["checks"]) for c in calls if not c["ok"] or c["checks"]]))
        pw = [c for c in calls if "--password-file" in c["argv"]]
        check(len(pw) == n_password_files, "%s: rustic-bko given a password file %d times (%d)" % (self.name, n_password_files, len(pw)))
        check(keys <= gone, "%s: every key and password file removed (%s left)" % (self.name, sorted(keys - gone)))
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
    d.wait("Next: the disks")
    check(r.says("lovelace's backup of 21 Nov 2026 02:00 UTC+01:00 is chosen."), "full: the backup picked")
    d.keys("Right", "Right", "Enter")
    r.finish(0)
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
    r.d.wait("Next: the disks")
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
    d.wait("Choose [b, s, l]: ")
    d.type("l", line=True)
    r.finish(0)
    text = d.dump()
    check("\x1b" not in text, "plain: no escape sequences")
    check("nope" not in text and PASSWORD not in text, "plain: nothing typed for a password shows")
    golden("app-plain", text)
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


TESTS = [test_shell_and_back, test_full_utf8_colour, test_full_ascii_vt220, test_recovery_key, test_plain, test_plain_signals,
         test_mismatch_and_report, test_location_with_no_stick, test_dry_run, test_network]


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
