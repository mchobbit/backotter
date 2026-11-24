#!/usr/bin/env python3
"""`bko-rescue --demo` driven through every widget by drive.py (RT8), in
full screen (UTF-8 colour, DEC mono, ASCII) and in plain mode, with the
fake bko-holt on the restore page: the passphrase handed on as a key file
(the fake checks it), its events followed with the gauge, the key file
removed after.  Also its failure, Cancel, an unknown schema, SIGTERM while
it runs, L and C, P to plain, a resize, and too small a terminal.

    testvm/rescue/demo-test.py PATH/TO/bko-rescue [ONLY ...]

Every screen is checked by screen.py's interpreter, which fails on any
sequence the profile may not send.  The screens named below are compared
with golden files in testvm/rescue/screens/, as BKO_RESCUE_GOLDEN says
(check, the default; report; bless).  ONLY runs the tests whose names
contain one of the words.  Exit 0 when every check passes.
"""

import hashlib
import json
import os
import shutil
import signal
import sys
import tempfile
import termios
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import drive   # noqa: E402
import screen  # noqa: E402

BIN = os.path.abspath(sys.argv[1]) if len(sys.argv) > 1 else "bko-rescue"
ONLY = sys.argv[2:]
FAKE = os.path.join(HERE, "fake-bko-holt")
SCREENS = os.path.join(HERE, "screens")
GOLDEN = os.environ.get("BKO_RESCUE_GOLDEN", "check") or "check"
FAILS = []
TMP = tempfile.mkdtemp(prefix="bko-rescue-demo-test-")


def check(ok, what):
    print(("ok   " if ok else "FAIL ") + what)
    if not ok:
        FAILS.append(what)
    return ok


def golden(name, text):
    path = os.path.join(SCREENS, name + ".txt")
    if GOLDEN == "bless":
        os.makedirs(SCREENS, exist_ok=True)
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
    diff = []
    for i in range(max(len(a), len(b))):
        x = a[i] if i < len(a) else None
        y = b[i] if i < len(b) else None
        if x != y:
            diff.append("%4d - %s\n%4d + %s" % (i + 1, x, i + 1, y))
    print("\n".join(diff[:30]))
    check(GOLDEN == "report", "golden %s differs (=report shows all, =bless takes it)" % name)


def fake_env(scenario="demo"):
    """The environment for a run with the fake: its scenario and a log."""
    log = os.path.join(TMP, "fake-%s-%d.log" % (scenario, time.time_ns()))
    return {"FAKE_BKO_HOLT_SCENARIO": os.path.join(HERE, "scenarios", scenario), "FAKE_BKO_HOLT_LOG": log,
            "TMPDIR": TMP}, log


def log_lines(log):
    if not os.path.exists(log):
        return []
    with open(log) as f:
        return [json.loads(l) for l in f if l.strip()]


def check_fake_log(name, log, typed, removed_within=5):
    """One call, its key file checked and as typed, then removed."""
    end = time.time() + removed_within
    while time.time() < end:
        ls = log_lines(log)
        if any("removed" in l or "left" in l for l in ls):
            break
        time.sleep(0.05)
    ls = log_lines(log)
    calls = [l for l in ls if "argv" in l]
    check(len(calls) == 1, "%s: the fake was run once (%d)" % (name, len(calls)))
    if not calls:
        return
    c = calls[0]
    check(c["ok"] and c["checks"] == [], "%s: the fake's key-file checks pass (%s)" % (name, c["checks"]))
    check(c["no_commit_env"] == "1" and "--commit" not in c["argv"], "%s: no --commit, BKO_HOLT_NO_COMMIT=1" % name)
    keys = c["keys"]
    want = hashlib.sha256(typed.encode()).hexdigest()
    check(len(keys) == 1 and keys[0]["sha256"] == want and keys[0]["bytes"] == len(typed.encode()),
          "%s: the key file holds what was typed, no newline" % name)
    check(all(typed not in a for a in c["argv"]), "%s: the passphrase isn't in argv" % name)
    if keys:
        p = keys[0]["path"]
        gone = [l for l in ls if l.get("removed") == p]
        check(bool(gone), "%s: the key file removed after the fake exited (%s)" % (name, gone or "left"))
        check(not os.path.exists(os.path.dirname(p)), "%s: its directory removed too" % name)


def says(d, phrase):
    """Whether the screen says `phrase`, read across wrapped lines: box
    glyphs dropped, every run of spaces and line breaks one space."""
    t = "".join(" " if c in screen.BOX else c for c in d.screen.text())
    flat = lambda x: " ".join(x.split())
    return flat(phrase) in flat(t)


def no_secret(d, typed, name):
    check(typed.encode() not in d.out, "%s: the passphrase never sent to the screen" % name)


def start(args, env=None, rows=24, cols=80):
    return drive.Drive(BIN, args, rows, cols, env)


def finish(d, name, code=0):
    got = d.exit()
    check(got == code, "%s: exit %d (%s %s)" % (name, code, got, d.err.strip()[-200:]))
    check(d.settings_back(), "%s: the terminal's settings put back" % name)
    if not d.plain:
        check(d.screen.cursor_shown, "%s: the cursor shown at the end" % name)
    d.close()


def walk_full(tag, args, extra_env=None, goldens=True):
    """Every page in full screen, each widget used, with the fake."""
    name = "full " + tag
    env, log = fake_env()
    env.update(extra_env or {})
    d = start(["--demo", "--holt", FAKE] + args, env)
    g = (lambda n: golden("demo-%s.%s" % (n, tag), d.dump())) if goldens else (lambda n: d.quiet())
    try:
        # 0: the radio list, nothing chosen; OK says why; the choice
        # follows the focus.
        d.wait("Which machine is this?")
        g("0-radio")
        d.keys("Enter")
        d.wait("Pick a machine first")
        d.keys("Down", "Down")
        check(any("babbage" in f for f in d.screen.focused()), name + ": the focus on the second row")
        row = next((l for l in d.screen.text().split("\n") if "babbage" in l), "")
        check("(*)" in row and d.screen.text().count("(*)") == 1, name + ": the row in focus is the one chosen")
        g("0-radio-chosen")
        d.keys("Enter")
        # 1: the text box: typing and Backspace.
        d.wait("Where is the backup?")
        d.type("sftp://nas/lovelacX")
        d.keys("BS")
        d.type("e")
        g("1-input")
        d.keys("Tab")
        check(any("OK" in f for f in d.screen.focused()), name + ": Tab moves the focus to OK")
        d.keys("Enter")
        # 2: the password box: a * a character.
        d.wait("The backup password")
        d.type("s3cret")
        check("******" in d.screen.text(), name + ": six stars for six characters")
        g("2-password")
        d.keys("Enter")
        # 3: the menu.
        d.wait("What now?")
        d.keys("Down", "Down", "Up")
        check(any("Check" in f for f in d.screen.focused()), name + ": the menu's focus moved down, down, up")
        g("3-menu")
        d.keys("Enter")
        # 4: yes or no, the focus on No first; Left to Yes.
        d.wait("Restore now?")
        check(any("No" in f for f in d.screen.focused()), name + ": the focus starts on No")
        g("4-yes-no")
        d.keys("Left")
        check(any("Yes" in f for f in d.screen.focused()), name + ": Left to Yes")
        d.keys("Enter")
        # 5: the text view: a page, a line, sideways.
        d.wait("Every command, in order")
        g("5-view")
        d.keys("Space", "Down", "Right")
        g("5-view-scrolled")
        d.keys("Left")
        check("\x1b" not in d.screen.text() and "?[31m(an escape, cleaned)" in d.screen.text(),
              name + ": an escape in the text is shown cleaned")
        d.keys("Enter")
        # 6: the gauge, by itself.
        d.wait("Restoring lovelace")
        d.wait("100 %", 8)
        # 7: the passphrase, then the fake's run followed.
        d.wait("Unlock sda3_crypt", 8)
        g("7-passphrase")
        typed = "correct horse"
        d.type(typed)
        d.keys("Enter")
        d.wait("Copy your files back", 5)
        d.wait("GB of 61.2 GB", 8)
        check("Now:" in d.screen.text(), name + ": the Now line under the stages")
        d.wait("The fake restore went through every stage", 10)
        check("[x]" not in d.screen.text(), name + ": the message replaced the gauge")
        g("7-done")
        d.keys("Enter")
        # 8: the summary.
        d.wait("The end of the demo")
        check(says(d, "The fake's restore: done.") and says(d, "a password of 6 characters")
              and "sftp://nas/lovelace" in d.screen.text(), name + ": the summary has every answer")
        g("8-summary")
        d.keys("Enter")
        finish(d, name)
        no_secret(d, typed, name)
        no_secret(d, "s3cret", name)
        check_fake_log(name, log, typed)
    except (drive.Failed, screen.Fault) as e:
        check(False, "%s: %s\n%s" % (name, e, d.dump()))
        d.close()


def test_full_utf8_colour():
    walk_full("utf8-colour", [])


def test_full_dec_mono():
    walk_full("dec-mono", ["--lines", "dec", "--colour", "off"])


def test_full_ascii_vt220():
    walk_full("ascii-mono", [], {"LANG": "C", "TERM": "vt220"})


def test_plain():
    name = "plain"
    env, log = fake_env()
    d = start(["--demo", "--plain", "--holt", FAKE], env)
    try:
        check(d.plain, name + ": --profile says plain mode")
        d.wait("Choose [1-3, s]: ")
        m = d.mark()
        d.type("2", line=True)
        d.wait("> ", since=m)
        m = d.mark()
        d.type("sftp://nas/plain", line=True)
        d.wait("Each key shows", since=m)
        d.wait("> ", since=m)
        a = d.attrs()
        check(a[3] & termios.ECHO == 0, name + ": echo off for the password")
        d.send(b"s3cretX\x7f\r")
        d.wait("Choose [1-5, b]: ")
        check(d.settings_back(), name + ": echo back on after it")
        d.type("4", line=True)
        d.wait("Choose [y, n, b]: ")
        d.type("n", line=True)
        d.wait("(an escape, cleaned)")
        d.wait("Choose [o, b]: ")
        m = d.mark()
        d.type("o", line=True)
        d.wait("    100 %: 61.2 of 61.2 GB", 8, since=m)
        d.wait("Unlock sda3_crypt", since=m)
        d.wait("> ", since=m)
        typed = "plain horse"
        m = d.mark()
        d.send(typed.encode() + b"\r")
        d.wait("Make partitions", 5, since=m)
        d.wait("100 %", 10, since=m)
        d.wait("The fake restore went through every stage", 10, since=m)
        check(b"\r" not in d.text.replace(b"\r\n", b""), name + ": no line rewritten with CR")
        d.type("o", line=True)
        d.wait("The fake's restore: done.", 5)
        check(b"a password of 6 characters" in d.text and b"sftp://nas/plain" in d.text, name + ": the summary")
        d.type("o", line=True)
        finish(d, name)
        no_secret(d, typed, name)
        no_secret(d, "s3cret", name)
        check_fake_log(name, log, typed)
    except (drive.Failed, screen.Fault) as e:
        check(False, "%s: %s\n%s" % (name, e, d.dump()[-3000:]))
        d.close()


def to_restore_page(d):
    """Through pages 0-6 quickly, to the passphrase."""
    d.wait("Which machine is this?")
    d.keys("Down", "Enter")
    d.wait("Where is the backup?")
    d.keys("Enter")
    d.wait("The backup password")
    d.keys("Enter")
    d.wait("What now?")
    d.keys("Enter")
    d.wait("Restore now?")
    d.keys("Enter")
    d.wait("Every command, in order")
    d.keys("Enter")
    d.wait("Unlock sda3_crypt", 8)


def test_failure():
    name = "failure"
    env, log = fake_env("demo-fail")
    d = start(["--demo", "--holt", FAKE], env)
    try:
        to_restore_page(d)
        d.type("pw")
        d.keys("Enter")
        d.wait("Problem: the restore failed", 10)
        check(says(d, "Stage boot failed: grub-install failed (exit status: 1): ?[31mEFI variables"),
              name + ": the failed event's text, its escape cleaned")
        check(says(d, "Nothing in the backup has been changed."), name + ": says the backup is safe")
        golden("demo-7-failed.utf8-colour", d.dump())
        d.keys("o")
        d.wait("What the fake bko-holt wrote to stderr")
        check("grub-install: error: EFI variables" in d.screen.text(), name + ": Raw output shows stderr")
        d.keys("Esc")
        d.wait("Problem: the restore failed")
        d.keys("Enter")
        d.wait("The end of the demo")
        check(says(d, "The fake's restore: failed in boot (exit status 1)."), name + ": the summary says how it failed")
        d.keys("Enter")
        finish(d, name)
        check_fake_log(name, log, "pw")
    except (drive.Failed, screen.Fault) as e:
        check(False, "%s: %s\n%s" % (name, e, d.dump()))
        d.close()


def test_cancel():
    name = "cancel"
    env, log = fake_env("demo-slow")
    d = start(["--demo", "--holt", FAKE], env)
    try:
        to_restore_page(d)
        d.type("pw")
        d.keys("Enter")
        d.wait("running for", 5)
        d.wait("GB of 61.2 GB", 5)
        check(d.screen.focused() == [], name + ": no focus while the gauge runs")
        d.keys("Enter")
        check("Stop the restore?" not in d.screen.text(), name + ": Enter does nothing until Tab")
        d.keys("Tab", "Enter")
        d.wait("Stop the restore?")
        check(any("Keep going" in f for f in d.screen.focused()), name + ": the focus starts on Keep going")
        d.keys("Enter")
        d.wait("Restoring lovelace")
        d.keys("Tab", "Enter")
        d.wait("Stop the restore?")
        d.keys("Left", "Enter")
        t = time.time()
        d.wait("The fake bko-holt was stopped", 5)
        check(time.time() - t < 3, name + ": stopped at once")
        d.keys("Enter")
        d.wait("The end of the demo")
        check(says(d, "The fake's restore: stopped."), name + ": the summary says stopped")
        d.keys("Enter")
        finish(d, name)
        check_fake_log(name, log, "pw")
    except (drive.Failed, screen.Fault) as e:
        check(False, "%s: %s\n%s" % (name, e, d.dump()))
        d.close()


def test_unknown_schema():
    name = "schema"
    env, log = fake_env("demo-schema")
    d = start(["--demo", "--holt", FAKE], env)
    try:
        to_restore_page(d)
        d.type("pw")
        d.keys("Enter")
        d.wait("Problem: the restore was stopped", 5)
        check(says(d, "format bko-holt-events schema 2, not one this bko-rescue knows"), name + ": it says why")
        d.keys("Enter")
        d.wait("The end of the demo")
        d.keys("Enter")
        finish(d, name)
        check_fake_log(name, log, "pw")
    except (drive.Failed, screen.Fault) as e:
        check(False, "%s: %s\n%s" % (name, e, d.dump()))
        d.close()


def test_sigterm_while_it_runs():
    name = "SIGTERM while the fake runs"
    env, log = fake_env("demo-slow")
    d = start(["--demo", "--holt", FAKE], env)
    try:
        to_restore_page(d)
        d.type("pw")
        d.keys("Enter")
        d.wait("GB of 61.2 GB", 5)
        d.signal(signal.SIGTERM)
        finish(d, name, 128 + signal.SIGTERM)
        check_fake_log(name, log, "pw")
    except (drive.Failed, screen.Fault) as e:
        check(False, "%s: %s\n%s" % (name, e, d.dump()))
        d.close()


def test_no_fake():
    name = "no --holt"
    d = start(["--demo"])
    try:
        to_restore_page_no_fake(d)
        check(says(d, "The demo never runs the real bko-holt."), name + ": says nothing is run")
        d.keys("Enter")
        d.wait("The fake's restore: not run.")
        d.keys("Enter")
        finish(d, name)
    except (drive.Failed, screen.Fault) as e:
        check(False, "%s: %s\n%s" % (name, e, d.dump()))
        d.close()


def to_restore_page_no_fake(d):
    d.wait("Which machine is this?")
    d.keys("Down", "Enter")
    for t in ("Where is the backup?", "The backup password", "What now?", "Restore now?", "Every command, in order"):
        d.wait(t)
        d.keys("Enter")
    d.wait("Nothing to run", 8)


def test_lines_colour_and_plain():
    name = "L, C, then P"
    d = start(["--demo"])
    try:
        d.wait("Which machine is this?")
        check("┌" in d.screen.text(), name + ": UTF-8 boxes first")
        d.set_profile(lines="dec")
        d.keys("L")
        check(b"\x1b(0" in d.out[-4000:] and "┌" in d.screen.text(), name + ": L gives DEC line drawing")
        d.set_profile(lines="ascii")
        d.keys("l")
        check("+---" in d.screen.text() and "─" not in d.screen.text(), name + ": L again gives ASCII")
        d.set_profile(colour=False)
        d.keys("C")
        check(all(a[0] is None and a[1] is None for row in d.screen.grid for _, a in row),
              name + ": C takes colour away")
        golden("demo-0-radio.after-l-l-c", d.dump())
        d.to_plain()
        d.keys("P")
        d.wait("Choose [1-3, s]: ")
        check(d.settings_back(), name + ": P puts the terminal back for plain mode")
        d.send(b"\x04")
        finish(d, name)
    except (drive.Failed, screen.Fault) as e:
        check(False, "%s: %s\n%s" % (name, e, d.dump()))
        d.close()


def test_resize_and_small():
    name = "resize"
    d = start(["--demo"], rows=30, cols=100)
    try:
        d.wait("Which machine is this?")
        check(d.screen.find("BackOtter rescue") == (4, 12), name + ": the canvas centred on 30x100 (%s)"
              % (d.screen.find("BackOtter rescue"),))
        d.resize(20, 60)
        d.wait("This window is too small", 2)
        d.resize(24, 80)
        d.wait("Which machine is this?", 2)
        check(d.screen.find("BackOtter rescue") == (1, 2), name + ": back to 80x24, redrawn")
        golden("demo-0-radio.after-resize", d.dump())
        d.signal(signal.SIGTERM)
        finish(d, name, 128 + signal.SIGTERM)
    except (drive.Failed, screen.Fault) as e:
        check(False, "%s: %s\n%s" % (name, e, d.dump()))
        d.close()


def test_script():
    """drive.py's own script runner, on the first two pages."""
    import subprocess
    script = os.path.join(TMP, "s.txt")
    with open(script, "w") as f:
        f.write("# two pages and out\nwait Which machine is this?\nkeys Down Enter\nwait Where is the backup?\n"
                "type abc\nfocus abc\ndump input\nsignal TERM\nexit 143\n")
    p = subprocess.run([sys.executable, os.path.join(HERE, "drive.py"), "--script", script, "--dumps", TMP,
                        "--", BIN, "--demo"], capture_output=True, text=True)
    check(p.returncode == 0 and "steps passed" in p.stdout, "drive.py --script (%d %s)" % (p.returncode, p.stderr[-300:]))
    check(os.path.exists(os.path.join(TMP, "input.txt")), "drive.py --script: dump written")
    with open(script, "w") as f:
        f.write("wait Which machine is this?\nwait 1 Not there\n")
    p = subprocess.run([sys.executable, os.path.join(HERE, "drive.py"), "--script", script, "--", BIN, "--demo"],
                       capture_output=True, text=True)
    check(p.returncode == 1 and "line 2" in p.stderr, "drive.py --script: a failed wait fails, naming the line")


def test_screen_py_faults():
    """screen.py refuses what bko-rescue never sends."""
    R = screen.Rules
    bad = [
        (R("utf8"), b"\x1b[1;1H\x1b[38;5;4m"), (R("utf8"), b"\x1b[1;1H\x1b[91m"), (R("utf8"), b"\x1b[K"),
        (R("utf8"), b"\x1b[6n"), (R("utf8"), b"\x1b7"), (R("utf8"), b"\x1b[25;1H"), (R("utf8"), b"x"),
        (R("utf8"), b"\x1b[1;1H\r"), (R("utf8"), b"\x1b[1;1H\xc2\x9b"), (R("utf8"), b"\x1b[1;1H\xe4\xb8\xad"),
        (R("utf8"), b"\x1b[24;80Hx"), (R("utf8"), b"\x1b[1;80Hxy"), (R("utf8"), b"\x1b(0"),
        (R("ascii", False, False, False, False), b"\x1b[1;1H\x1b[30m"),
        (R("ascii", False, False, False, False), b"\x1b[0;1m"),
        (R("ascii", False, False, False, False), b"\x1b[2J"), (R("ascii", False, False, False, False), b"\x1b[?25l"),
        (R("ascii"), b"\x1b[1;1H\xc3\xa9"), (R("dec"), b"\x1b(0\x1b[1;1Ha"),
    ]
    for rules, b in bad:
        s = screen.Screen(24, 80, rules)
        try:
            s.feed(b)
            s.end()
            check(False, "screen.py refuses %r" % b)
        except screen.Fault:
            pass
    s = screen.Screen(24, 80, R("dec"))
    whole = "\x1b[?25l\x1b[2J\x1b[1;1H\x1b[0;37;44mab\x1b(0lqk\x1b(B\x1b[2;1H\u00e9".encode()
    s = screen.Screen(24, 80, R("utf8"))
    whole = whole.replace(b"\x1b(0lqk\x1b(B", "\u250c\u2500\u2510".encode())
    s.feed(whole)
    s.end()
    for i in range(len(whole)):
        s2 = screen.Screen(24, 80, R("utf8"))
        s2.feed(whole[:i])
        s2.feed(whole[i:])
        s2.end()
        if s2.dump() != s.dump():
            check(False, "screen.py: split at %d reads the same" % i)
    d = screen.Screen(24, 80, R("dec"))
    d.feed(b"\x1b[1;1Hab\x1b(0lqk\x1b(B")
    check(d.text() == s.text().replace("\u00e9", " "), "screen.py: DEC line letters read as box glyphs")
    check("ab┌─┐" in s.text(), "screen.py: a stream split anywhere reads the same")
    check(not s.cursor_shown, "screen.py: the cursor hidden")


TESTS = [test_screen_py_faults, test_script, test_full_utf8_colour, test_full_dec_mono, test_full_ascii_vt220,
         test_plain, test_failure, test_cancel, test_unknown_schema, test_sigterm_while_it_runs, test_no_fake,
         test_lines_colour_and_plain, test_resize_and_small]

for f in TESTS:
    if ONLY and not any(w in f.__name__ for w in ONLY):
        continue
    print("==", f.__name__)
    f()
shutil.rmtree(TMP, ignore_errors=True)
print("%d failed" % len(FAILS))
sys.exit(1 if FAILS else 0)
