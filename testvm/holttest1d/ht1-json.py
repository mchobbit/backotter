#!/usr/bin/env python3
"""HoltTest1's HT_VARIANT=json checks (docs/design/holt-json.md, "Testing";
RT10j): what ht1.sh and ht1-bsd.sh (ht1-json.sh) ask of a rebuild run again
with --json beside the text run of the same arguments.

    ht1-json.py stream FILE commit|dry
        an events stream as a run that wrote (commit: each command run, its
        cmd_done 0, each stage checked) or a dry run (dry: each command
        `would`, no cmd_done, each stage unchecked, boot and close among
        them) writes it, ending `end` 0; its counts on stdout
    ht1-json.py same DRY COMMIT...
        the dry run's commands are the writing runs' (their events, in
        order), one for one
    ht1-json.py inlog LOG COMMIT...
        each command the events ran is in LOG (the text run's load.log), in
        order, as text mode logs it: its shell line, the archiver's
        --json-progress --progress-interval 1s as text mode's --no-progress
    ht1-json.py logcmp TEXT JSON [COMMIT...]
        two load.logs (or the restored /var/log/bko-holt of each run) alike
        but for what --json changes there (holt-json.md, "What differs under
        --json" 1 and 7): the argv lines (==== bko-holt load ...), the
        archiver's flag, its last status line (the only one the log keeps:
        no more status lines in JSON than archiver runs, its lines with the
        flag), and a private write's text, which the JSON log gives as its
        size and SHA-256 (checked against the text log's); and the output
        lines that differ between any two runs (VARIES: dd's time and rate,
        the rescuer's own pids and mount namespaces in umount-tree's lines).
        With the writing runs' events (COMMIT), every write they mark
        `private` must be one of those size-and-SHA-256 lines: a private
        text logged as it is fails
    ht1-json.py verdict TEXT JSON
        two judge.py outputs with the same verdict: the same DIFF lines and
        the same count of differences on the last line (what it ignored may
        differ: a boot's own fields, such as times)
    ht1-json.py doc FILE FORMAT [KEY=VALUE...] [--print KEY]
        a document of FORMAT whose top-level KEYs are as given (VALUE read
        as JSON when it parses, else a string); --print KEY prints KEY's
        value (a string as it is)
    ht1-json.py status FILE STAGE
        a status document's STAGE row: its state on stdout, exit 0 when it
        is done (assemble: any state, as the text run's check)
    ht1-json.py refused EVENTS CODE TARGET
        a writing run's events refused by the checks (start, `problems`,
        `end` 4, no command): among the problems one of CODE whose target
        is TARGET (its /dev/ or /dev/dsk/ left out), each problem's code on
        stdout
    ht1-json.py record OUT LOGDIR GUEST
        the --json calls a run saved in LOGDIR (json-NAME.argv beside their
        output) as the fake's scenario OUT (testvm/rescue/scenarios/...),
        one call a directory in the run's order (each stage's plan --json
        before it, status --json after it), key and password files' paths as
        `*`, and recorded.txt saying where from; then replays it (below)
    ht1-json.py replay SCENARIO
        a recorded scenario through testvm/rescue/fake-bko-holt with
        FAKE_BKO_HOLT_STATE, call by call in its order, as a client staging
        the run would: key and password files made for the `*`s, each
        --expect-plan the hash of the plan document the call before it got;
        each call must be answered by its own recording, as recorded

Exit 0 when it holds, 1 when it doesn't (the reasons on stdout), 2 for bad
usage.  Python's stdlib only.
"""

import difflib
import hashlib
import json
import os
import re
import shutil
import sys

JSON_FLAG = ["--json-progress", "--progress-interval", "1s"]
TEXT_FLAG = ["--no-progress"]
ARGV_LINE = "==== bko-holt load "
PRIVATE = re.compile(r"^# \((\d+) bytes, sha256 ([0-9a-f]{64}), private: not shown\)$")


def events(path):
    out = []
    with open(path, encoding="utf-8", errors="surrogateescape") as f:
        for n, l in enumerate(f, 1):
            l = l.strip()
            if not l:
                continue
            try:
                out.append(json.loads(l))
            except ValueError:
                raise SystemExit("%s:%d: not JSON: %s" % (path, n, l[:120]))
    return out


def fail(reasons):
    for r in reasons[:40]:
        print(r)
    if len(reasons) > 40:
        print("... and %d more" % (len(reasons) - 40))
    return 1


# ---- stream -----------------------------------------------------------------

def stream(path, mode):
    evs = events(path)
    errs = []
    if not evs or not isinstance(evs[0], dict) or evs[0].get("event") != "start":
        first = evs[0] if evs else None
        if isinstance(first, dict) and first.get("format") == "bko-holt-error":
            return fail(["%s: a bko-holt-error document (%s): %s" % (path, first.get("code"), first.get("text"))])
        return fail(["%s: no start first" % path])
    st, end = evs[0], evs[-1]
    commit = mode == "commit"
    if st.get("commit") is not commit:
        errs.append("start.commit is %s, not %s" % (st.get("commit"), commit))
    if st.get("dry_run") is not (mode == "dry"):
        errs.append("start.dry_run is %s, not %s" % (st.get("dry_run"), mode == "dry"))
    if end.get("event") != "end" or end.get("exit") != 0 or end.get("stopped") is not False:
        errs.append("the last event is %s, not end 0" % json.dumps(end)[:200])
    want = st.get("stages") or []
    counts = st.get("stage_commands") or {}
    seen, stage, cmds, pending, stage_n = [], None, 0, None, None
    progress = warnings = ran = 0
    for e in evs[1:]:
        name = e.get("event")
        if name in ("failed", "problems", "stopping"):
            errs.append("a %s event: %s" % (name, json.dumps(e)[:300]))
        elif name == "skip":
            seen.append(e.get("stage"))
            if e.get("state") != "nothing":
                errs.append("stage %s skipped as %s: on new targets only a stage with nothing to do is" % (e.get("stage"), e.get("state")))
        elif name == "stage":
            stage, cmds = e.get("stage"), 0
            seen.append(stage)
            if e.get("commands") != counts.get(stage):
                errs.append("stage %s: %s commands, start counted %s" % (stage, e.get("commands"), counts.get(stage)))
            stage_n = e.get("commands")
        elif name == "cmd":
            cmds += 1
            if e.get("would") is not (not commit):
                errs.append("%s %d: would is %s" % (e.get("stage"), e.get("n"), e.get("would")))
            if e.get("n") != cmds or e.get("of") != stage_n:
                errs.append("%s: command %s of %s, counted %d of %s" % (e.get("stage"), e.get("n"), e.get("of"), cmds, stage_n))
            if pending is not None:
                errs.append("%s %d: no cmd_done before the next command" % (stage, pending["n"]))
            pending = e if commit else None
        elif name == "cmd_done":
            if not commit:
                errs.append("a cmd_done in a dry run: %s" % json.dumps(e))
            elif pending is None or e.get("n") != pending.get("n"):
                errs.append("a cmd_done for no command running: %s" % json.dumps(e))
            else:
                want_status = 0 if pending.get("kind") == "run" else None
                if e.get("status") != want_status or e.get("signal") is not None:
                    errs.append("%s %d (%s): status %s signal %s, not %s" % (stage, e.get("n"), pending.get("kind"), e.get("status"), e.get("signal"), want_status))
                ran += 1
            pending = None
        elif name == "stage_done":
            if pending is not None:
                errs.append("%s: stage_done with command %d still running" % (stage, pending["n"]))
            if e.get("checked") is not commit:
                errs.append("%s: stage_done checked %s" % (e.get("stage"), e.get("checked")))
            if cmds != stage_n:
                errs.append("%s: %d commands seen of %s" % (stage, cmds, stage_n))
            stage = None
        elif name == "progress":
            progress += 1
            if not commit:
                errs.append("a progress event in a dry run")
        elif name == "warning":
            warnings += 1
    if seen != want:
        errs.append("stages %s, start named %s" % (seen, want))
    if mode == "dry":
        for s in ("boot", "close"):
            if s in want and s not in seen:
                errs.append("the dry run has no %s stage (J11)" % s)
    if errs:
        return fail(["%s: %s" % (path, e) for e in errs])
    n = sum(counts.get(s, 0) for s in seen)
    print("%d stage%s, %d command%s%s%s%s" % (
        len(seen), "" if len(seen) == 1 else "s", n, "" if n == 1 else "s",
        ", %d run" % ran if commit else " would run",
        ", %d progress" % progress if progress else "",
        ", %d warning%s" % (warnings, "" if warnings == 1 else "s") if warnings else ""))
    return 0


# ---- the commands -----------------------------------------------------------

def cmds_of(paths):
    out = []
    for p in paths:
        for e in events(p):
            if e.get("event") == "cmd":
                out.append(e)
    return out


def key(e):
    return json.dumps([e.get(k) for k in ("stage", "n", "of", "kind", "argv", "env", "why", "path", "dev",
                                          "stdin_bytes", "private", "archiver")], sort_keys=True)


def same(dry, commit):
    a, b = cmds_of([dry]), cmds_of(commit)
    if [key(x) for x in a] == [key(x) for x in b]:
        print("%d commands, the same in both" % len(a))
        return 0
    errs = ["the dry run has %d commands, the writing runs %d" % (len(a), len(b))]
    for i, (x, y) in enumerate(zip(a, b)):
        if key(x) != key(y):
            errs.append("the first that differs, #%d: dry %s; ran %s" % (i + 1, key(x)[:400], key(y)[:400]))
            break
    return fail(errs)


def quote(s):
    b = s.encode("utf-8", "surrogateescape")
    if b and all(c < 128 and (chr(c).isalnum() or chr(c) in "-_./:=,+@%^") for c in b):
        return s
    return "'" + s.replace("'", "'\\''") + "'"


def env_key_ok(k):
    return bool(re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", k))


def text_argv(argv):
    """ARGV as text mode builds it: the archiver's --json-progress flag back to --no-progress."""
    out, i = [], 0
    while i < len(argv):
        if argv[i:i + 3] == JSON_FLAG:
            out.extend(TEXT_FLAG)
            i += 3
        else:
            out.append(argv[i])
            i += 1
    return out


def shell_line(e):
    """The line Cmd::shell() logs for the step event E (the line of its
    command, before a here-document), and whether a here-document follows;
    None for a step that logs no such line (a note, a read-back) or whose
    argv the event bounded."""
    kind = e.get("kind")
    if kind == "write":
        line = "cat > " + quote(e.get("path") or "")
    elif kind == "run":
        argv = e.get("argv") or []
        if any(not isinstance(a, str) for a in argv):
            return None
        argv = text_argv(argv)
        env = list(e.get("env") or [])
        # run_cmd's LC_ALL=C first, which shell() doesn't write.
        if env and env[0] == ["LC_ALL", "C"]:
            env = env[1:]
        if all(env_key_ok(k) for k, _ in env):
            line = " ".join(["%s=%s" % (k, quote(v)) for k, v in env] + [quote(a) for a in argv])
        else:
            line = " ".join(["env"] + [quote("%s=%s" % (k, v)) for k, v in env if k and "=" not in k] + [quote(a) for a in argv])
    else:
        return None
    return line, e.get("stdin_bytes") is not None


def inlog(log, paths):
    with open(log, encoding="utf-8", errors="surrogateescape") as f:
        text = "\n" + f.read()
    at, found, skipped = 0, 0, 0
    for e in cmds_of(paths):
        s = shell_line(e)
        if s is None:
            skipped += 1
            continue
        line, heredoc = s
        # A line of its own (a script's newlines are the log's too), or the
        # start of a here-document's.
        needle = "\n" + line + (" <<'" if heredoc else "\n")
        hit = text.find(needle, at)
        if hit < 0:
            return fail(["%s: %s %s, the %s step, isn't in the text run's log after line %d: %s" % (
                log, e.get("stage"), e.get("n"), e.get("kind"), text.count("\n", 0, at), line[:300])])
        at, found = hit + len(needle) - (0 if heredoc else 1), found + 1
    print("%d commands found in order in the text run's log%s" % (found, ", %d notes and read-backs not looked for" % skipped if skipped else ""))
    return 0


# ---- the logs ---------------------------------------------------------------

def status_line(l):
    l = l.strip()
    if not l.startswith("{"):
        return False
    try:
        v = json.loads(l)
    except ValueError:
        return False
    return isinstance(v, dict) and v.get("message_type") == "status"


# Lines of a command's output that differ between any two runs, a text run
# and another text run as well: (the line's form, what stands for it).
VARIES = [
    # dd's summary, its time and rate: GNU (Linux, Haiku) and the BSDs'.
    (re.compile(r"^(\d+ bytes \(.*\) copied), [0-9.]+ s, [0-9.,]+ [kMGTP]?B/s$"), r"\1, TIME, RATE"),
    (re.compile(r"^(\d+) bytes transferred in [0-9.]+ secs \(\d+ bytes/sec\)$"), r"\1 bytes transferred in TIME (RATE)"),
    # umount-tree's: a process of the rescuer's own that held the target in
    # its mount namespace, by namespace and pid (listed in pid order).
    (re.compile(r"^unmounting (\S+) in mnt:\[\d+\] \(pid \d+, (.*)\)$"), r"unmounting \1 in mnt:[NS] (pid PID, \2)"),
]


def norm(text, is_json):
    out, group = [], []
    for l in text.split("\n"):
        if l.startswith(ARGV_LINE):
            continue
        if is_json:
            if status_line(l):
                continue
            l = l.replace(" " + " ".join(JSON_FLAG), " " + " ".join(TEXT_FLAG))
        varied = False
        for rx, sub in VARIES:
            if rx.match(l):
                l, varied = rx.sub(sub, l), True
                break
        # (A run of umount-tree's lines in an order of their own: sorted.)
        if varied and l.startswith("unmounting "):
            group.append(l)
            continue
        out.extend(sorted(group))
        group = []
        out.append(l)
    out.extend(sorted(group))
    return out


def private_ok(text_lines, json_lines):
    """A JSON log's private write: its one line, a size and SHA-256, against
    the text log's here-document body in its place."""
    if len(json_lines) != 1:
        return False
    m = PRIVATE.match(json_lines[0])
    if not m:
        return False
    body = "\n".join(text_lines) + "\n"
    n, h = int(m.group(1)), m.group(2)
    # shell() adds a newline to a text that has none at its end.
    for t in (body, body[:-1]):
        b = t.encode("utf-8", "surrogateescape")
        if len(b) == n and hashlib.sha256(b).hexdigest() == h:
            return True
    return False


def logcmp(tpath, jpath, commit=()):
    with open(tpath, encoding="utf-8", errors="surrogateescape") as f:
        t = norm(f.read(), False)
    with open(jpath, encoding="utf-8", errors="surrogateescape") as f:
        raw = f.read()
    j = norm(raw, True)
    # The log keeps each archiver run's last status line, no more: an
    # archiver run is a line of the log's with the flag.
    lines = raw.split("\n")
    nstatus = sum(1 for l in lines if status_line(l))
    nruns = sum(1 for l in lines if " " + " ".join(JSON_FLAG) in l)
    sm = difflib.SequenceMatcher(None, t, j, autojunk=False)
    errs, privates = [], 0
    for op, a0, a1, b0, b1 in sm.get_opcodes():
        if op == "equal":
            continue
        if op == "replace" and private_ok(t[a0:a1], j[b0:b1]):
            privates += 1
            continue
        errs.append("text %d-%d, json %d-%d:" % (a0 + 1, a1, b0 + 1, b1))
        errs.extend("  - " + l[:200] for l in t[a0:a1][:8])
        errs.extend("  + " + l[:200] for l in j[b0:b1][:8])
    if nstatus > nruns:
        errs.insert(0, "%s: %d archiver status lines for %d archiver run%s: the log keeps each run's last only" % (
            jpath, nstatus, nruns, "" if nruns == 1 else "s"))
    if commit:
        want = sum(1 for e in cmds_of(commit) if e.get("private") is True)
        if privates != want:
            errs.insert(0, "%s: %d private write%s by size and SHA-256, the events mark %d private: a private text is logged as it is" % (
                jpath, privates, "" if privates == 1 else "s", want))
    if errs:
        return fail(["%s and %s differ but for their argv lines and the archiver's flag:" % (tpath, jpath)] + errs)
    print("%d lines alike but for the argv lines and the archiver's flag and status%s" % (
        len(t), ", %d private write%s by size and SHA-256" % (privates, "" if privates == 1 else "s") if privates else ""))
    return 0


def verdict(tpath, jpath):
    """The verdict: the DIFF lines and the count of differences on the last
    line; what that line says it ignored (fields of the boot, such as times
    and pids, which vary from boot to boot) may differ."""
    def read(p):
        with open(p, encoding="utf-8", errors="surrogateescape") as f:
            ls = [l for l in f.read().split("\n") if l.strip()]
        last = ls[-1] if ls else ""
        m = re.match(r"^(\d+ differences?)\b", last)
        return sorted(l for l in ls if l.startswith("DIFF")), (m.group(1) if m else last), last
    (td, tl, tfull), (jd, jl, jfull) = read(tpath), read(jpath)
    errs = []
    if td != jd:
        errs.append("DIFF lines: text %d, json %d; first unlike: %s" % (len(td), len(jd), next(iter(sorted(set(td) ^ set(jd))), "?")[:300]))
    if tl != jl:
        errs.append("last lines: text %r, json %r" % (tfull[:200], jfull[:200]))
    if errs:
        return fail(errs)
    print("%d DIFF line%s, %s%s" % (len(td), "" if len(td) == 1 else "s", jfull[:200],
                                    "" if tfull == jfull else " (the text run's: %s)" % tfull[:200]))
    return 0


def bare(dev):
    for p in ("/dev/dsk/", "/dev/rdsk/", "/dev/"):
        if dev.startswith(p):
            return dev[len(p):]
    return dev


def refused(path, code, target):
    evs = events(path)
    errs = []
    if not evs or evs[0].get("event") != "start":
        return fail(["%s: no start first: %s" % (path, json.dumps(evs[0])[:300] if evs else "no events")])
    probs = [e for e in evs if e.get("event") == "problems"]
    ran = [e for e in evs if e.get("event") in ("stage", "cmd", "cmd_done")]
    end = evs[-1]
    if len(probs) != 1:
        errs.append("%d problems events, not 1" % len(probs))
    if ran:
        errs.append("a %s event: the checks refused nothing" % ran[0].get("event"))
    if end.get("event") != "end" or end.get("exit") != 4 or end.get("stopped") is not False:
        errs.append("the last event is %s, not end 4" % json.dumps(end)[:200])
    ps = [p for e in probs for p in e.get("problems") or [] if isinstance(p, dict)]
    if not any(p.get("code") == code and bare(str(p.get("target") or "")) == bare(target) for p in ps):
        errs.append("no %s problem for %s among: %s" % (code, target, "; ".join("%s (%s): %s" % (p.get("code"), p.get("target"), p.get("text")) for p in ps)[:600]))
    if errs:
        return fail(["%s: %s" % (path, e) for e in errs])
    print("refused, %s" % ", ".join("%s%s" % (p.get("code"), " " + str(p["target"]) if p.get("target") else "") for p in ps))
    return 0


# ---- documents --------------------------------------------------------------

def doc(path, fmt, rest):
    pr = None
    want = []
    i = 0
    while i < len(rest):
        if rest[i] == "--print" and i + 1 < len(rest):
            pr = rest[i + 1]
            i += 2
            continue
        k, sep, v = rest[i].partition("=")
        if not sep:
            raise SystemExit(2)
        try:
            v = json.loads(v)
        except ValueError:
            pass
        want.append((k, v))
        i += 1
    try:
        with open(path, encoding="utf-8") as f:
            d = json.loads(f.read())
    except (OSError, ValueError) as e:
        return fail(["%s: %s" % (path, e)])
    errs = []
    if not isinstance(d, dict) or d.get("format") != fmt:
        errs.append("%s: format %s, not %s" % (path, json.dumps(d.get("format") if isinstance(d, dict) else d)[:80], fmt))
    else:
        for k, v in want:
            if d.get(k) != v or type(d.get(k)) is not type(v):
                errs.append("%s: %s is %s, not %s" % (path, k, json.dumps(d.get(k))[:300], json.dumps(v)))
        if fmt == "bko-holt-plan" and d.get("problems"):
            errs.append("%s: problems: %s" % (path, "; ".join("%s: %s" % (p.get("code"), p.get("text")) for p in d["problems"])[:600]))
    if errs:
        return fail(errs)
    if pr is not None:
        v = d.get(pr)
        print(v if isinstance(v, str) else json.dumps(v))
    return 0


def status(path, stage):
    try:
        with open(path, encoding="utf-8") as f:
            d = json.loads(f.read())
    except (OSError, ValueError) as e:
        return fail(["%s: %s" % (path, e)])
    rows = [r for r in d.get("stages") or [] if isinstance(r, dict) and r.get("name") == stage]
    if d.get("format") != "bko-holt-status" or len(rows) != 1:
        return fail(["%s: no %s row in a bko-holt-status document" % (path, stage)])
    st = rows[0].get("state")
    print("%s %s%s" % (stage, st, (" (%s)" % rows[0].get("why")) if rows[0].get("why") else ""))
    return 0 if st == "done" or stage == "assemble" else 1


# ---- record -----------------------------------------------------------------

def masked(argv):
    """ARGV with the key and password files' paths as `*` (the fake's
    patterns: it checks the files it is handed, whatever their path)."""
    out, i = [], 0
    while i < len(argv):
        a = argv[i]
        out.append(a)
        if a == "--key-file" and i + 1 < len(argv):
            name = argv[i + 1].partition("=")[0]
            out.append(name + "=*")
            i += 2
            continue
        if a == "--password-file" and i + 1 < len(argv):
            out.append("*")
            i += 2
            continue
        i += 1
    return out


def record(out, logdir, guest):
    calls = sorted(f[:-len(".argv")] for f in os.listdir(logdir) if f.startswith("json-") and f.endswith(".argv"))
    if not calls:
        return fail(["%s: no json-*.argv" % logdir])
    if os.path.exists(out):
        shutil.rmtree(out)
    os.makedirs(out)
    order = {"version": 0, "refuse-commit": 1, "refuse-decoy": 2, "plan": 3, "dry": 4}
    stages = ["partition", "assemble", "format", "mount", "restore", "boot", "close"]

    def rank(c):
        n = c[len("json-"):]
        if n in order:
            return (order[n], 0, n)
        for i, s in enumerate(stages):
            for k, pre in enumerate(("plan-", "", "status-after-")):
                if n == pre + s:
                    return (10 + 3 * i + k, 0, n)
        return (99, 0, n)
    made = []
    for k, c in enumerate(sorted(calls, key=rank), 1):
        name = c[len("json-"):]
        d = os.path.join(out, "%02d-%s" % (k, name))
        os.makedirs(d)
        with open(os.path.join(logdir, c + ".argv"), encoding="utf-8") as f:
            argv = [l.rstrip("\n") for l in f if l.rstrip("\n")]
        with open(os.path.join(d, "argv"), "w") as f:
            f.write("\n".join(masked(argv)) + "\n")
        src = None
        for ext, dest in ((".events", "events"), (".json", "stdout")):
            if os.path.isfile(os.path.join(logdir, c + ext)):
                src = (os.path.join(logdir, c + ext), dest)
                break
        if src is None:
            return fail(["%s: no %s.events or .json beside its argv" % (logdir, c)])
        if src[1] == "events":
            with open(src[0], encoding="utf-8") as f, open(os.path.join(d, "events"), "w") as g:
                g.write("# Recorded from HoltTest1 HT_VARIANT=json, guest %s (testvm/holttest1d/ht1-json.py record)\n" % guest)
                g.write("delay 0.01\n")
                for l in f:
                    if l.strip():
                        g.write(l if l.endswith("\n") else l + "\n")
        else:
            shutil.copyfile(src[0], os.path.join(d, "stdout"))
        ex = os.path.join(logdir, c + ".exit")
        if os.path.isfile(ex):
            with open(ex) as f:
                code = f.read().strip()
            if code and code != "0":
                with open(os.path.join(d, "exit"), "w") as g:
                    g.write(code + "\n")
        err = os.path.join(logdir, c + ".err")
        if os.path.isfile(err) and os.path.getsize(err):
            shutil.copyfile(err, os.path.join(d, "stderr"))
        made.append(os.path.basename(d))
    first = None
    try:
        first = events(os.path.join(logdir, "json-dry.events"))[0]
    except (OSError, IndexError, SystemExit):
        pass
    with open(os.path.join(out, "recorded.txt"), "w") as f:
        f.write("Recorded by HoltTest1 HT_VARIANT=json (testvm/holttest1d/ht1-json.py record) from guest %s:\n"
                "bko-holt %s, the dry run's start at %s.  One call a directory, in the run's order.\n" % (
                    guest, (first or {}).get("version", "?"), (first or {}).get("generated", "?")))
    print("%d calls: %s" % (len(made), " ".join(made)))
    return replay(out)


FAKE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "rescue", "fake-bko-holt")


def replay(scen):
    import subprocess
    import tempfile
    calls = sorted(d for d in os.listdir(scen) if os.path.isdir(os.path.join(scen, d)))
    if not calls:
        return fail(["%s: no calls" % scen])
    errs, hashed = [], 0
    with tempfile.TemporaryDirectory(prefix="ht1-replay-") as tmp:
        os.chmod(tmp, 0o700)
        keys = os.path.join(tmp, "keys")
        os.mkdir(keys, 0o700)
        state, logf = os.path.join(tmp, "state"), os.path.join(tmp, "fake.log")
        env = {k: v for k, v in os.environ.items() if not k.startswith(("FAKE_BKO_HOLT_", "BKO_HOLT_"))}
        env.update(FAKE_BKO_HOLT_SCENARIO=os.path.abspath(scen), FAKE_BKO_HOLT_STATE=state,
                   FAKE_BKO_HOLT_LOG=logf, FAKE_BKO_HOLT_REMOVE_SECS="0")

        def key(name):
            p = os.path.join(keys, "k%d" % len(os.listdir(keys)))
            fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            os.write(fd, hashlib.sha256(("replay %s %s" % (name, p)).encode()).hexdigest().encode())
            os.close(fd)
            return p
        plan = None
        for c in calls:
            d = os.path.join(scen, c)
            with open(os.path.join(d, "argv")) as f:
                pats = [l.rstrip("\n") for l in f if l.rstrip("\n")]
            argv, i = [], 0
            while i < len(pats):
                a = pats[i]
                argv.append(a)
                if a == "--key-file" and i + 1 < len(pats) and pats[i + 1].endswith("=*"):
                    argv.append(pats[i + 1][:-1] + key(pats[i + 1][:-2]))
                    i += 2
                    continue
                if a == "--password-file" and i + 1 < len(pats) and pats[i + 1] == "*":
                    argv.append(key("repo"))
                    i += 2
                    continue
                if a == "--expect-plan" and i + 1 < len(pats):
                    if plan is None:
                        errs.append("%s: --expect-plan, and no plan document before it" % c)
                        argv.append(pats[i + 1])
                    else:
                        argv.append(plan)
                        hashed += 1
                    i += 2
                    continue
                i += 1
            p = subprocess.run([sys.executable, "-I", FAKE] + argv, env=env, capture_output=True, timeout=600)
            want_exit = 0
            if os.path.isfile(os.path.join(d, "exit")):
                with open(os.path.join(d, "exit")) as f:
                    want_exit = int(f.read().strip())
            with open(logf) as f:
                got = [json.loads(l) for l in f if '"call"' in l][-1].get("call")
            if got != c:
                errs.append("%s: answered by %s (exit %d): %s" % (c, got, p.returncode, p.stderr.decode(errors="replace").strip()[:300]))
                continue
            if p.returncode != want_exit:
                errs.append("%s: exit %d, recorded %d" % (c, p.returncode, want_exit))
            out = p.stdout.decode("utf-8", "surrogateescape")
            if os.path.isfile(os.path.join(d, "stdout")):
                with open(os.path.join(d, "stdout"), encoding="utf-8", errors="surrogateescape") as f:
                    rec = f.read()
                try:
                    doc_ = json.loads(rec)
                except ValueError:
                    doc_ = None
                if isinstance(doc_, dict) and doc_.get("format") == "bko-holt-plan":
                    plan = doc_.get("plan_sha256")
            else:
                with open(os.path.join(d, "events"), encoding="utf-8", errors="surrogateescape") as f:
                    rec = "".join(l.strip() + "\n" for l in f if l.strip().startswith("{"))
            if out != rec:
                errs.append("%s: its output isn't the recording's" % c)
    if errs:
        return fail(["%s: %s" % (scen, e) for e in errs])
    print("%d calls replayed in order, each by its own recording, %d --expect-plan from the plan before it" % (len(calls), hashed))
    return 0


def main(a):
    try:
        if len(a) == 3 and a[0] == "stream" and a[2] in ("commit", "dry"):
            return stream(a[1], a[2])
        if len(a) >= 3 and a[0] == "same":
            return same(a[1], a[2:])
        if len(a) >= 3 and a[0] == "inlog":
            return inlog(a[1], a[2:])
        if len(a) >= 3 and a[0] == "logcmp":
            return logcmp(a[1], a[2], a[3:])
        if len(a) == 3 and a[0] == "verdict":
            return verdict(a[1], a[2])
        if len(a) >= 3 and a[0] == "doc":
            return doc(a[1], a[2], a[3:])
        if len(a) == 3 and a[0] == "status":
            return status(a[1], a[2])
        if len(a) == 4 and a[0] == "refused":
            return refused(a[1], a[2], a[3])
        if len(a) == 4 and a[0] == "record":
            return record(a[1], a[2], a[3])
        if len(a) == 2 and a[0] == "replay":
            return replay(a[1])
    except OSError as e:
        return fail([str(e)])
    print(__doc__.strip().split("\n\n")[1], file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
