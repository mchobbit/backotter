#!/usr/bin/env python3
"""`bko-rescue --demo` driven through every widget by drive.py (RT8), in
full screen (UTF-8 colour, DEC mono, ASCII) and in plain mode, with the
fake bko-holt on the restore page: the passphrase handed on as a key file
and the backup password as a password file (the fake checks both), its
dry-run events followed with the gauge, both files removed after.  Also
its failure, Cancel, an unknown schema, a run the checks blocked, a
refusal document, bko-holt's own stop, a one-letter passphrase, SIGTERM
while it runs (and in plain mode SIGTERM, Ctrl-C and a hang-up), L and C,
P to plain, a resize, and too small a terminal; and holt_schema.py over the
fake's scenarios and bko-holt's golden documents (RT10h).

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
# The password file's text when nothing was typed on the password page
# (demo.rs's DEMO_PASSWORD).
DEMO_PASSWORD = "demo-repository-password"
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


def check_fake_log(name, log, typed, password=DEMO_PASSWORD, removed_within=5):
    """One call, its key file and password file checked and as typed, then
    both removed."""
    end = time.time() + removed_within
    while time.time() < end:
        ls = log_lines(log)
        if sum(1 for l in ls if "removed" in l or "left" in l) >= 2:
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
    a = c["argv"]
    check("--repo" in a and "--password-file" in a and "--through" in a, "%s: --repo and --password-file, as a real run's" % name)
    keys = {os.path.basename(k["path"]): k for k in c["keys"]}
    check(sorted(keys) == ["password", "sda3_crypt"], "%s: a key file and a password file (%s)" % (name, sorted(keys)))
    for what, base, text in (("key file", "sda3_crypt", typed), ("password file", "password", password)):
        k = keys.get(base)
        if not k:
            continue
        want = hashlib.sha256(text.encode()).hexdigest()
        check(k["sha256"] == want and k["bytes"] == len(text.encode()), "%s: the %s holds what was typed, no newline" % (name, what))
        check(not in_values(text, a), "%s: the %s's text isn't in argv" % (name, what))
        gone = [l for l in ls if l.get("removed") == k["path"]]
        check(bool(gone), "%s: the %s removed after the fake exited (%s)" % (name, what, gone or "left"))
        check(not os.path.exists(os.path.dirname(k["path"])), "%s: its directory removed too" % name)


def in_values(typed, argv):
    """The fake's rule: a whole argument or a NAME=PATH's PATH, or inside
    one for 8 characters or more."""
    vals = list(argv) + [a.split("=", 1)[1] for a in argv if "=" in a]
    return any(v == typed or (len(typed) >= 8 and typed in v) for v in vals)


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
        d.wait("would run", 8)
        check("Now:" in d.screen.text(), name + ": the Now line under the stages")
        d.wait("The fake restore went through every stage", 10)
        check(says(d, "It was a dry run: 13 commands would have run"), name + ": says it was a dry run")
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
        check_fake_log(name, log, typed, "s3cret")
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
        d.wait("would run", 10, since=m)
        d.wait("The fake restore went through every stage", 10, since=m)
        check(b"\r" not in d.text.replace(b"\r\n", b""), name + ": no line rewritten with CR")
        d.type("o", line=True)
        d.wait("The fake's restore: done.", 5)
        check(b"a password of 6 characters" in d.text and b"sftp://nas/plain" in d.text, name + ": the summary")
        d.type("o", line=True)
        finish(d, name)
        no_secret(d, typed, name)
        no_secret(d, "s3cret", name)
        check_fake_log(name, log, typed, "s3cret")
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
        check(says(d, "A command (grub-install) failed."), name + ": the failure's code and program")
        check(says(d, "Nothing in the backup has been changed."), name + ": says the backup is safe")
        golden("demo-7-failed.utf8-colour", d.dump())
        d.keys("o")
        d.wait("What the fake bko-holt wrote to stderr")
        check("grub-install: error: EFI variables" in d.screen.text(), name + ": Raw output shows stderr")
        d.keys("Esc")
        d.wait("Problem: the restore failed")
        d.keys("Enter")
        d.wait("The end of the demo")
        check(says(d, "The fake's restore: failed in boot: cmd_failed (exit status 1)."), name + ": the summary says how it failed")
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
        check("Esc: keep going" in d.screen.text(), name + ": the key line says Esc keeps going")
        d.keys("Esc")
        d.gone("Stop the restore?")
        check("Restoring lovelace" in d.screen.text(), name + ": Esc keeps going")
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


def one_ending(name, scenario, title, phrases, summary, golden_name=None):
    """A run of SCENARIO to its message: TITLE, each of PHRASES said, then
    the summary's SUMMARY."""
    env, log = fake_env(scenario)
    d = start(["--demo", "--holt", FAKE], env)
    try:
        to_restore_page(d)
        d.type("pw")
        d.keys("Enter")
        d.wait(title, 10)
        for p in phrases:
            check(says(d, p), "%s: says %r" % (name, p))
        check(says(d, "Nothing in the backup has been changed."), name + ": says the backup is safe")
        if golden_name:
            golden(golden_name, d.dump())
        d.keys("Enter")
        d.wait("The end of the demo")
        check(says(d, "The fake's restore: %s." % summary), "%s: the summary says %r" % (name, summary))
        d.keys("Enter")
        finish(d, name)
        check_fake_log(name, log, "pw")
    except (drive.Failed, screen.Fault) as e:
        check(False, "%s: %s\n%s" % (name, e, d.dump()))
        d.close()


def test_blocked():
    one_ending("blocked", "demo-blocked", "Problem: nothing ran",
               ["bko-holt's checks found problems, so it ran nothing:", "not_root: not running as root",
                "key_needed: sda3_crypt: give its new passphrase"],
               "blocked: not_root (exit status 4)", "demo-7-blocked.utf8-colour")


def test_refused():
    one_ending("refused", "demo-refused", "Problem: bko-holt refused the run",
               ["refused it before doing anything: bko-holt load: demo-system.json: No such file or directory",
                "(code layout_unreadable, exit status 2; bko-holt ended with exit status 2.)"],
               "refused: layout_unreadable")


def test_stopped_by_holt():
    one_ending("stopped by bko-holt", "demo-stopped", "bko-holt stopped on request",
               ["between commands, and said so (exit 5)."], "stopped by bko-holt (exit status 5)")


def test_schema_checker():
    """holt_schema.py, the checker both sides share: the fake's scenarios
    pass it (demo-schema failing as it is marked to), so do bko-holt's
    golden documents when this tree has them, and a broken document or
    stream fails it."""
    import subprocess
    checker = os.path.join(HERE, "holt_schema.py")
    run = lambda *paths: subprocess.run([sys.executable, "-I", checker, "--quiet"] + list(paths), capture_output=True, text=True)
    p = run(os.path.join(HERE, "scenarios"))
    check(p.returncode == 0, "holt_schema: the fake's scenarios pass (%s)" % p.stdout.strip()[-800:])
    goldens = [os.path.join(HERE, "..", "..", "holt", "tests", x) for x in ("corpus", "fixtures")]
    goldens = [g for g in goldens if os.path.isdir(g)]
    if goldens:
        p = run(*goldens)
        check(p.returncode == 0 and " 0 problems" in p.stdout, "holt_schema: bko-holt's golden documents pass (%s)" % p.stdout.strip()[-800:])
    else:
        print("skip holt_schema on the goldens: no holt/tests here")
    ev = os.path.join(HERE, "scenarios", "demo", "load", "events")
    with open(ev) as f:
        good = [l for l in f.read().splitlines() if l.startswith("{")]
    bad_cases = {
        "a missing field": [good[0].replace(',"pid":4711', "")] + good[1:],
        "a cmd_done for a command that would run": good[:4] + ['{"event":"cmd_done","stage":"partition","n":1,"status":0,"signal":null,"secs":0.1}'] + good[4:],
        "no end": good[:-1],
        "an event it doesn't know": good[:2] + ['{"event":"wobble"}'] + good[2:],
        "an old version": [good[0].replace('"version":"0.2.6"', '"version":"0.2.5"')] + good[1:],
        "a line after an error document": ['{"format":"bko-holt-error","schema":1,"version":"0.2.6","generated":"2026-11-24T10:00:00Z","dry_run":true,"exit":2,"code":"no_commit","text":"x"}', good[0]],
    }
    for what, lines in bad_cases.items():
        path = os.path.join(TMP, "bad.events")
        with open(path, "w") as f:
            f.write("\n".join(lines) + "\n")
        p = run(path)
        check(p.returncode == 1, "holt_schema: fails %s (%s)" % (what, p.stdout.strip().splitlines()[0:2]))
    # The fake's own refusal under the dry-run lock is a document it passes.
    env = dict(os.environ, BKO_HOLT_NO_COMMIT="yes")
    p = subprocess.run([sys.executable, FAKE, "load", "x.json", "--json", "--commit"], capture_output=True, text=True, env=env, cwd=TMP)
    doc = os.path.join(TMP, "refusal.json")
    with open(doc, "w") as f:
        f.write(p.stdout)
    q = run(doc)
    check(p.returncode == 2 and q.returncode == 0 and '"code":"no_commit"' in p.stdout
          and p.stderr == "bko-holt load: BKO_HOLT_NO_COMMIT=yes is set (a dry run), so --commit is refused; nothing was written to any disk\n",
          "the fake refuses --commit as bko-holt does, any value but 0 (%d %s %s)" % (p.returncode, p.stderr.strip(), q.stdout.strip()))
    env["BKO_HOLT_NO_COMMIT"] = "0"
    p = subprocess.run([sys.executable, FAKE, "load", "x.json", "--json", "--commit"], capture_output=True, text=True, env=env, cwd=TMP)
    check(p.returncode == 98, "the fake: BKO_HOLT_NO_COMMIT=0 is no lock (%d)" % p.returncode)


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


def test_one_letter_passphrase():
    """Any passphrase will do, the page says: one letter that is in
    "--key-file" too."""
    name = "one-letter passphrase"
    env, log = fake_env()
    d = start(["--demo", "--holt", FAKE], env)
    try:
        to_restore_page(d)
        d.type("e")
        d.keys("Enter")
        d.wait("The fake restore went through every stage", 10)
        d.keys("Enter")
        d.wait("The end of the demo")
        d.keys("Enter")
        finish(d, name)
        check_fake_log(name, log, "e")
    except (drive.Failed, screen.Fault) as e:
        check(False, "%s: %s\n%s" % (name, e, d.dump()))
        d.close()


def to_restore_page_plain(d):
    """Pages 0-6 in plain mode, to the passphrase's prompt."""
    d.wait("Choose [1-3, s]: ")
    m = d.mark()
    d.type("2", line=True)
    d.wait("> ", since=m)
    m = d.mark()
    d.type("sftp://nas/plain", line=True)
    d.wait("Each key shows", since=m)
    d.wait("> ", since=m)
    d.send(b"s3cret\r")
    d.wait("Choose [1-5, b]: ")
    d.type("4", line=True)
    d.wait("Choose [y, n, b]: ")
    d.type("n", line=True)
    d.wait("Choose [o, b]: ")
    m = d.mark()
    d.type("o", line=True)
    d.wait("Unlock sda3_crypt", 8, since=m)
    d.wait("> ", since=m)


def plain_signal(kind, sig):
    """In plain mode, a signal while the fake runs: the fake stopped and
    the key file removed before the signal ends bko-rescue."""
    name = "plain, %s while the fake runs" % kind
    env, log = fake_env("demo-slow")
    d = start(["--demo", "--plain", "--holt", FAKE], env)
    try:
        to_restore_page_plain(d)
        m = d.mark()
        d.send(b"pw\r")
        d.wait("GB of 61.2 GB", 8, since=m)
        if kind == "Ctrl-C":
            d.send(b"\x03", settle=False)
        elif kind == "a hang-up":
            d.hang_up()
        else:
            d.signal(sig)
        got = d.exit()
        check(got == -sig, "%s: ended by the signal, after cleaning up (%s %s)" % (name, got, d.err.strip()[-200:]))
        if kind != "a hang-up":
            check(d.settings_back(), "%s: the terminal's settings as they were" % name)
        d.close()
        check_fake_log(name, log, "pw", "s3cret")
    except (drive.Failed, screen.Fault) as e:
        check(False, "%s: %s\n%s" % (name, e, d.dump()[-3000:]))
        d.close()


def test_plain_sigterm():
    plain_signal("SIGTERM", signal.SIGTERM)


def test_plain_ctrl_c():
    plain_signal("Ctrl-C", signal.SIGINT)


def test_plain_hang_up():
    plain_signal("a hang-up", signal.SIGHUP)


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


def test_plain_check_faults():
    """drive.py's plain-mode check refuses what bko-rescue never sends."""
    R = screen.Rules
    bad = [("utf8", b"a\xc2\x9b"), ("utf8", b"\xff"), ("utf8", b"a\x80b"), ("utf8", "\u4e2d".encode()),
           ("utf8", b"\x1b[2J"), ("utf8", b"a\x07"), ("utf8", b"a\x7f"), ("ascii", "\u00e9".encode()),
           ("utf8", b"ok\xc3")]
    for lines, b in bad:
        c = drive.PlainCheck(R(lines))
        try:
            c.feed(b)
            c.end()
            check(False, "plain check refuses %r (%s)" % (b, lines))
        except screen.Fault:
            pass
    good = "\u0141\u00f3d\u017a ok\tx\r\n".encode()
    c = drive.PlainCheck(R("utf8"))
    try:
        for i in range(len(good)):
            c.feed(good[i:i + 1])
        c.end()
        check(True, "plain check: letters text.rs allows, split a byte a read, pass")
    except screen.Fault as e:
        check(False, "plain check: %s" % e)


TESTS = [test_screen_py_faults, test_plain_check_faults, test_script, test_full_utf8_colour, test_full_dec_mono, test_full_ascii_vt220,
         test_plain, test_failure, test_cancel, test_unknown_schema, test_blocked, test_refused, test_stopped_by_holt,
         test_schema_checker, test_sigterm_while_it_runs,
         test_one_letter_passphrase, test_plain_sigterm, test_plain_ctrl_c, test_plain_hang_up, test_no_fake,
         test_lines_colour_and_plain, test_resize_and_small]

for f in TESTS:
    if ONLY and not any(w in f.__name__ for w in ONLY):
        continue
    print("==", f.__name__)
    f()
shutil.rmtree(TMP, ignore_errors=True)
print("%d failed" % len(FAILS))
sys.exit(1 if FAILS else 0)
