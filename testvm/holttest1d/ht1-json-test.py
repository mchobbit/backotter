#!/usr/bin/env python3
"""Tests of ht1-json.py (HoltTest1's HT_VARIANT=json checks), on made-up
events and logs shaped as bko-holt's:

    python3 testvm/holttest1d/ht1-json-test.py
"""

import hashlib
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
PY = os.path.join(HERE, "ht1-json.py")
SCHEMA = os.path.join(HERE, "..", "rescue", "holt_schema.py")
TMP = tempfile.mkdtemp(prefix="ht1-json-test-")
fails = 0


def check(ok, what):
    global fails
    print(("ok    " if ok else "FAIL  ") + what)
    if not ok:
        fails += 1


def run(*a):
    p = subprocess.run([sys.executable, "-I", PY] + list(a), capture_output=True, text=True)
    return p.returncode, p.stdout + p.stderr


def put(name, text):
    p = os.path.join(TMP, name)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w") as f:
        f.write(text)
    return p


def stream_text(evs):
    return "".join(json.dumps(e, separators=(",", ":")) + "\n" for e in evs)


START = {"event": "start", "format": "bko-holt-events", "schema": 1, "version": "0.2.6", "generated": "2026-11-24T20:00:00Z",
         "pid": 42, "plan_sha256": "a" * 64, "log": "/run/bko-holt/log/load.log", "layout": {"hostname": "g", "generated": None}}
ARCH = ["/mnt/share/rustic-bko", "--repository", "/mnt/share/repo", "--password-file", "/run/ht1-keys/repo-password"]
CMDS = {
    "partition": [
        {"kind": "run", "argv": ["wipefs", "--all", "/dev/vdb"], "env": [["LC_ALL", "C"]], "why": "vda -> /dev/vdb: clear it", "stdin_bytes": None},
        {"kind": "run", "argv": ["sfdisk", "--wipe", "always", "/dev/vdb"], "env": [["LC_ALL", "C"]], "why": "the table", "stdin_bytes": 40},
    ],
    "restore": [
        {"kind": "run", "argv": ARCH + ["--json-progress", "--progress-interval", "1s", "restore", "--numeric-id", "latest:/mnt/ht1-src-g", "/mnt/bko-target"],
         "env": [["LC_ALL", "C"], ["RUSTIC_STRICT_EXIT", "1"]], "why": "copy", "stdin_bytes": None, "archiver": {"part": 1, "parts": 1}},
        {"kind": "write", "argv": [], "env": [], "why": "a key", "path": "/mnt/bko-target/etc/k ey", "stdin_bytes": 7, "private": True},
        {"kind": "note", "argv": [], "env": [], "why": "by hand", "stdin_bytes": None},
        {"kind": "run", "argv": ["bash", "-c", "one\n  two"], "env": [["LC_ALL", "C"]], "why": "a script", "stdin_bytes": None},
    ],
}


def make_stream(stages, commit, drop=None, change=None):
    evs = [dict(START, commit=commit, dry_run=not commit, stages=stages, stage_commands={s: len(CMDS[s]) for s in stages})]
    for s in stages:
        evs.append({"event": "stage", "stage": s, "commands": len(CMDS[s])})
        for i, c in enumerate(CMDS[s], 1):
            e = {"event": "cmd", "stage": s, "n": i, "of": len(CMDS[s]), "path": None, "dev": None, "private": False,
                 "archiver": None, "would": not commit}
            e.update(c)
            if change and change[0] == (s, i):
                e.update(change[1])
            evs.append(e)
            if e.get("archiver") and commit:
                evs.append({"event": "progress", "stage": s, "n": i, "part": 1, "parts": 1, "bytes": 10, "total_bytes": 10, "secs": 1, "errors": 0})
            if commit:
                evs.append({"event": "cmd_done", "stage": s, "n": i, "status": 0 if c["kind"] == "run" else None, "signal": None, "secs": 0.1})
        evs.append({"event": "stage_done", "stage": s, "secs": 0.2, "checked": commit})
    evs.append({"event": "end", "exit": 0, "stopped": False, "secs": 1.0})
    if drop:
        evs = [e for e in evs if not drop(e)]
    return evs


def test_stream():
    p = put("ok.events", stream_text(make_stream(["partition", "restore"], True)))
    rc, out = run("stream", p, "commit")
    check(rc == 0 and "6 commands, 6 run, 1 progress" in out, "stream: a writing run passes (%s)" % out.strip())
    if os.path.isfile(SCHEMA):
        q = subprocess.run([sys.executable, "-I", SCHEMA, "--quiet", p], capture_output=True, text=True)
        check(q.returncode == 0, "stream: the made-up run passes holt_schema.py too (%s)" % q.stdout.strip()[-300:])
    rc, out = run("stream", p, "dry")
    check(rc == 1 and "start.commit" in out, "stream: a writing run isn't a dry run")
    d = put("dry.events", stream_text(make_stream(["partition", "restore"], False)))
    rc, out = run("stream", d, "dry")
    check(rc == 0 and "would run" in out, "stream: a dry run passes (%s)" % out.strip())
    bad = make_stream(["partition"], True)
    bad[-2:-1] = []
    bad.insert(-1, {"event": "stage_done", "stage": "partition", "secs": 0.2, "checked": True})
    cases = {
        "a nonzero cmd_done": make_stream(["partition"], True, change=None)[:3] + [{"event": "cmd_done", "stage": "partition", "n": 1, "status": 1, "signal": None, "secs": 0.1}] + make_stream(["partition"], True)[4:],
        "end 1": make_stream(["partition"], True)[:-1] + [{"event": "end", "exit": 1, "stopped": False, "secs": 1}],
        "a missing cmd_done": make_stream(["partition"], True, drop=lambda e: e["event"] == "cmd_done" and e["n"] == 2),
        "a failed event": make_stream(["partition"], True)[:-1] + [{"event": "failed", "stage": None, "n": None, "code": "internal", "program": None, "argv": None, "status": None, "signal": None, "text": "x", "tail": [], "log": None}, {"event": "end", "exit": 1, "stopped": False, "secs": 1}],
        "a stage start didn't name": [dict(make_stream(["partition"], True)[0], stages=["partition", "format"])] + make_stream(["partition"], True)[1:],
    }
    for what, evs in cases.items():
        rc, out = run("stream", put("bad.events", stream_text(evs)), "commit")
        check(rc == 1, "stream: fails %s (%s)" % (what, out.strip().splitlines()[:1]))
    nb = make_stream(["partition"], False)
    nb[0]["stages"] = ["partition", "boot"]
    nb[0]["stage_commands"] = {"partition": 2, "boot": 0}
    rc, out = run("stream", put("noboot.events", stream_text(nb)), "dry")
    check(rc == 1 and "boot" in out, "stream: a dry run without its boot stage fails (J11)")
    err = put("err.events", json.dumps({"format": "bko-holt-error", "schema": 1, "code": "no_commit", "text": "x"}) + "\n")
    rc, out = run("stream", err, "dry")
    check(rc == 1 and "no_commit" in out, "stream: an error document says so")


def test_same():
    d = put("dry2.events", stream_text(make_stream(["partition", "restore"], False)))
    c1 = put("c-partition.events", stream_text(make_stream(["partition"], True)))
    c2 = put("c-restore.events", stream_text(make_stream(["restore"], True)))
    rc, out = run("same", d, c1, c2)
    check(rc == 0 and "6 commands" in out, "same: a dry run and the stages it would run (%s)" % out.strip())
    c3 = put("c-restore2.events", stream_text(make_stream(["restore"], True, change=(("restore", 1), {"argv": ["other"]}))))
    rc, out = run("same", d, c1, c3)
    check(rc == 1 and "#3" in out, "same: one argv unlike fails, naming it (%s)" % out.strip().splitlines()[-1:])
    rc, out = run("same", d, c1)
    check(rc == 1, "same: a stage short fails")


TEXT_LOG = """==== bko-holt load 0.2.6 ["bko-holt", "load", "/mnt/share/layout.json", "--only", "partition", "--commit"]
==== stage partition
# vda -> /dev/vdb: clear it
wipefs --all /dev/vdb
/dev/vdb: 8 bytes were erased
4194304 bytes (4.2 MB, 4.0 MiB) copied, 0.00682417 s, 699 MB/s
# the table
sfdisk --wipe always /dev/vdb <<'EOF'
label: gpt
EOF
Checking that no-one is using this disk right now ... OK
==== stage partition done
==== bko-holt load 0.2.6 ["bko-holt", "load", "/mnt/share/layout.json", "--only", "restore", "--commit"]
==== stage restore
# copy
RUSTIC_STRICT_EXIT=1 /mnt/share/rustic-bko --repository /mnt/share/repo --password-file /run/ht1-keys/repo-password --no-progress restore --numeric-id latest:/mnt/ht1-src-g /mnt/bko-target
[INFO] restoring
# a key
cat > '/mnt/bko-target/etc/k ey' <<'EOF'
secret
EOF
# by hand
# a script
bash -c 'one
  two'
unmounting /mnt/bko-target in mnt:[4026532346] (pid 1001, systemd-logind)
unmounting /mnt/bko-target in mnt:[4026532351] (pid 1099, chronyd)
==== stage restore done
"""


def json_log(text):
    h = hashlib.sha256(b"secret\n").hexdigest()
    return (text.replace('"--commit"]', '"--commit", "--json", "--expect-plan", "x"]')
            .replace("--no-progress restore", "--json-progress --progress-interval 1s restore")
            .replace("[INFO] restoring\n", '{"message_type":"status","bytes_done":10}\n[INFO] restoring\n')
            .replace("secret\nEOF", "# (7 bytes, sha256 %s, private: not shown)\nEOF" % h)
            .replace("0.00682417 s, 699 MB/s", "0.0060642 s, 712 MB/s")
            .replace("(pid 1001, systemd-logind)\nunmounting /mnt/bko-target in mnt:[4026532351] (pid 1099, chronyd)",
                     "(pid 1088, chronyd)\nunmounting /mnt/bko-target in mnt:[4026532457] (pid 1094, systemd-logind)"))


def test_inlog():
    t = put("text.log", TEXT_LOG)
    c1 = put("i-partition.events", stream_text(make_stream(["partition"], True)))
    c2 = put("i-restore.events", stream_text(make_stream(["restore"], True)))
    rc, out = run("inlog", t, c1, c2)
    check(rc == 0 and "5 commands found" in out and "1 notes" in out, "inlog: every command in order, the flag as text mode's, a quoted path, env, a script of two lines (%s)" % out.strip())
    rc, out = run("inlog", t, c2, c1)
    check(rc == 1, "inlog: out of order fails")
    c4 = put("i-restore4.events", stream_text(make_stream(["restore"], True, change=(("restore", 1), {"env": [["LC_ALL", "C"], ["RUSTIC_STRICT_EXIT", "2"]]}))))
    rc, out = run("inlog", t, c1, c4)
    check(rc == 1 and "restore 1" in out, "inlog: an environment unlike the log's fails")


def test_logcmp():
    t = put("t.log", TEXT_LOG)
    j = put("j.log", json_log(TEXT_LOG))
    rc, out = run("logcmp", t, j)
    check(rc == 0 and "1 private write" in out, "logcmp: alike but for argv lines, the flag, the status line, a private write, dd's time and umount-tree's pids (%s)" % out.strip())
    j2 = put("j2.log", json_log(TEXT_LOG).replace("8 bytes were erased", "9 bytes were erased"))
    rc, out = run("logcmp", t, j2)
    check(rc == 1 and "9 bytes" in out, "logcmp: another line fails, shown")
    j4 = put("j4.log", json_log(TEXT_LOG).replace("(pid 1088, chronyd)", "(pid 1088, chronyd2)"))
    rc, out = run("logcmp", t, j4)
    check(rc == 1 and "chronyd2" in out, "logcmp: umount-tree's lines by pid and namespace only: another process fails")
    j3 = put("j3.log", json_log(TEXT_LOG).replace("7 bytes, sha256", "8 bytes, sha256"))
    rc, out = run("logcmp", t, j3)
    check(rc == 1, "logcmp: a private write whose size isn't the text's fails")


def test_verdict_doc_status():
    a = put("a.txt", "DIFF x\nnote y\n3 differences, 4 ignored, of 9 fields\n")
    b = put("b.txt", "note z\nDIFF x\n3 differences, 4 ignored, of 9 fields\n")
    c = put("c.txt", "DIFF x\nDIFF w\n3 differences, 4 ignored, of 9 fields\n")
    check(run("verdict", a, b)[0] == 0, "verdict: the same DIFF lines and last line")
    check(run("verdict", a, c)[0] == 1, "verdict: another DIFF line fails")
    d = put("d.txt", "DIFF x\n3 differences, 6 ignored, of 9 fields\n")
    e = put("e.txt", "DIFF x\n4 differences, 4 ignored, of 9 fields\n")
    check(run("verdict", a, d)[0] == 0, "verdict: another count of ignored fields is the same verdict")
    check(run("verdict", a, e)[0] == 1, "verdict: another count of differences fails")
    p = put("plan.json", json.dumps({"format": "bko-holt-plan", "ok": True, "problems": [], "plan_sha256": "b" * 64}))
    rc, out = run("doc", p, "bko-holt-plan", "ok=true", "--print", "plan_sha256")
    check(rc == 0 and out.strip() == "b" * 64, "doc: a plan's field printed")
    rc, out = run("doc", p, "bko-holt-status")
    check(rc == 1, "doc: another format fails")
    e = put("err.json", json.dumps({"format": "bko-holt-error", "code": "no_commit", "exit": 2}))
    check(run("doc", e, "bko-holt-error", "code=no_commit", "exit=2")[0] == 0, "doc: an error document's code and exit")
    check(run("doc", e, "bko-holt-error", "code=bad_args")[0] == 1, "doc: a code unlike fails")
    pp = put("planp.json", json.dumps({"format": "bko-holt-plan", "ok": False, "problems": [{"code": "key_needed", "text": "k"}]}))
    rc, out = run("doc", pp, "bko-holt-plan", "ok=true")
    check(rc == 1 and "key_needed" in out, "doc: a plan's problems named")
    s = put("st.json", json.dumps({"format": "bko-holt-status", "stages": [{"name": "format", "state": "done", "why": ""}, {"name": "assemble", "state": "nothing", "why": "no containers"}]}))
    check(run("status", s, "format")[0] == 0, "status: a stage done")
    check(run("status", s, "assemble")[0] == 0, "status: assemble, any state")
    check(run("status", s, "mount")[0] == 1, "status: no row fails")


def test_record():
    d = os.path.join(TMP, "logs")
    os.makedirs(d)
    with open(os.path.join(d, "json-dry.argv"), "w") as f:
        f.write("load\n/mnt/share/layout.json\n--key-file\ncrypt=/run/ht1-keys/passphrase\n--password-file\n/run/ht1-keys/repo-password\n--through\nclose\n--json\n")
    with open(os.path.join(d, "json-dry.events"), "w") as f:
        f.write(stream_text(make_stream(["partition"], False)))
    with open(os.path.join(d, "json-dry.exit"), "w") as f:
        f.write("0\n")
    with open(os.path.join(d, "json-refuse-commit.argv"), "w") as f:
        f.write("load\nx\n--commit\n")
    with open(os.path.join(d, "json-refuse-commit.json"), "w") as f:
        f.write("{}\n")
    with open(os.path.join(d, "json-refuse-commit.exit"), "w") as f:
        f.write("2\n")
    out = os.path.join(TMP, "scen")
    rc, o = run("record", out, d, "g")
    calls = sorted(os.listdir(out)) if os.path.isdir(out) else []
    check(rc == 0 and calls == ["01-refuse-commit", "02-dry", "recorded.txt"], "record: one call a directory, in the run's order, and where from (%s)" % calls)
    with open(os.path.join(out, "02-dry", "argv")) as f:
        argv = f.read().split("\n")
    check("crypt=*" in argv and argv[argv.index("--password-file") + 1] == "*", "record: key and password files' paths as *")
    check(open(os.path.join(out, "01-refuse-commit", "exit")).read().strip() == "2" and not os.path.exists(os.path.join(out, "02-dry", "exit")),
          "record: a nonzero exit kept, 0 left out")


for t in (test_stream, test_same, test_inlog, test_logcmp, test_verdict_doc_status, test_record):
    t()
print("%d failed" % fails)
sys.exit(1 if fails else 0)
