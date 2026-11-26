#!/usr/bin/env python3
"""The shape of bko-holt's --json documents and events (holt-json.md), one
checker for both sides (RT10h): bko-holt's golden documents, the fake's
scenarios (fake-bko-holt) and every recorded run go through it, so the fake
can't drift from bko-holt.

    holt_schema.py [--quiet] PATH...

Each PATH is a file or a directory:

  - a document (`bko-holt-plan`, `-status`, `-stick`, `-stick-copy`,
    `-recovery`, `-error`, `-api`): one JSON
    value, on one line or pretty-printed (a golden's `version` VERSION lets
    GENERATED and PLAN_SHA256 stand in for the run's own);
  - an events stream (a file named `events` or `*.events`/`*.ndjson`): one
    JSON object a line, from `start` to `end`, or one `bko-holt-error`
    document alone; a scenario's `sleep`, `delay` and `#` lines are the
    fake's and skipped;
  - a directory: every plan.json and status.json under it, every *.json in a
    directory whose name ends in `-json` (holt/tests/fixtures/status-json),
    and every fake scenario call (a directory with an `argv`): its `stdout`
    as a document and its `events` as a stream.  A call with a file named
    `invalid` must fail the check, with that file's words (its first line)
    in the reason: a fixture that is wrong on purpose (demo-schema).

It knows every document's and event's required fields and their types; a
field it doesn't know is allowed (readers skip them), an event it doesn't
know is not (it is a typo, or the checker is behind bko-holt).  Exit 0 when
everything is as it should be, 1 when anything isn't, 2 for bad usage.

Python's stdlib only.
"""

import json
import os
import re
import sys

# The first bko-holt that writes schema 1 of the protocol (RT10a-RT10g
# landed while main's version was 0.2.6): a recorded run's start.version
# older than this can't be one of its runs.
MIN_VERSION = (0, 2, 6)
SCHEMA = 1
STAGES = ("partition", "assemble", "format", "mount", "restore", "boot", "close", "open")
ERROR_CODES = ("bad_args", "no_commit", "layout_unreadable", "layout_not_json", "layout_refused",
               "arch_mismatch", "schema_unknown", "json_not_here", "out_unsafe",
               # stick --copy's refusals (exit 1; holt-json.md, open question 8)
               "config_not_ok", "no_such_machine", "two_machines", "machine_not_ok", "no_layout", "copy_failed")
FAILURE_CODES = ("cmd_failed", "wrong_passphrase", "archiver_failed", "efi_vars", "readback_failed",
                 "not_done_after", "no_restored_tree", "commit_refused", "write_failed", "lock_held", "internal")
STATES = ("done", "partly", "not_done", "nothing", "unknown")
KINDS = ("run", "write", "check", "note")
# A golden's stand-ins (holt/tests/plan_json.rs replaces these).
GOLDEN = {"version": "VERSION", "generated": "GENERATED", "sha256": "PLAN_SHA256"}


# ---- types ------------------------------------------------------------------

class Opt:
    """A field that may be left out."""
    def __init__(self, t):
        self.t = t


class Or:
    def __init__(self, *ts):
        self.ts = ts


class List:
    def __init__(self, t):
        self.t = t


class Obj:
    """An object: FIELDS each required (Opt: may be left out), any other
    key allowed; VALUES, when given, the type of every value (a map)."""
    def __init__(self, fields=None, values=None):
        self.fields = fields or {}
        self.values = values


class Enum:
    def __init__(self, *vs):
        self.vs = vs


class Const:
    def __init__(self, v):
        self.v = v


class Re:
    """A string matching PATTERN; GOLDEN_KEY's stand-in too in a golden."""
    def __init__(self, pattern, what, golden_key=None):
        self.re = re.compile(pattern)
        self.what = what
        self.golden_key = golden_key


STR, INT, NUM, BOOL, NULL, ANY = "str", "int", "num", "bool", "null", "any"
STAMP = Re(r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$", "an RFC 3339 UTC time", "generated")
VERSION = Re(r"^\d+\.\d+\.\d+", "a version", "version")
SHA = Re(r"^[0-9a-f]{64}$", "a SHA-256 in hex", "sha256")
S_ = Or(STR, NULL)
I_ = Or(INT, NULL)
B_ = Or(BOOL, NULL)


class Ctx:
    def __init__(self, golden=False, events=False):
        self.golden = golden
        # In an event, any long string may be given as {"bytes", "sha256"}
        # (holt-json.md, "Common rules").
        self.events = events


def tname(v):
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "a boolean"
    if isinstance(v, int):
        return "an integer"
    if isinstance(v, float):
        return "a number"
    if isinstance(v, str):
        return "a string"
    if isinstance(v, list):
        return "a list"
    return "an object"


def bounded(v):
    return isinstance(v, dict) and set(v) == {"bytes", "sha256"} and isinstance(v["bytes"], int) \
        and isinstance(v["sha256"], str) and SHA.re.match(v["sha256"]) is not None


def check(v, t, path, errs, ctx):
    """Append to ERRS what is wrong with V as T, at PATH."""
    if isinstance(t, Or):
        tries = []
        for x in t.ts:
            e = []
            check(v, x, path, e, ctx)
            if not e:
                return
            tries.append(e)
        errs.append(min(tries, key=len)[0] if len(tries) == 1 else "%s: %s, not one of its types (%s)" % (path, tname(v), "; ".join(e[0] for e in tries)))
        return
    if t == ANY:
        return
    if t == STR:
        if not (isinstance(v, str) or (ctx.events and bounded(v))):
            errs.append("%s: %s, not a string" % (path, tname(v)))
        return
    if t == INT:
        if not (isinstance(v, int) and not isinstance(v, bool)):
            errs.append("%s: %s, not an integer" % (path, tname(v)))
        return
    if t == NUM:
        if not (isinstance(v, (int, float)) and not isinstance(v, bool)):
            errs.append("%s: %s, not a number" % (path, tname(v)))
        return
    if t == BOOL:
        if not isinstance(v, bool):
            errs.append("%s: %s, not a boolean" % (path, tname(v)))
        return
    if t == NULL:
        if v is not None:
            errs.append("%s: %s, not null" % (path, tname(v)))
        return
    if isinstance(t, Const):
        if v != t.v or type(v) is not type(t.v):
            errs.append("%s: %s, not %s" % (path, json.dumps(v)[:60], json.dumps(t.v)))
        return
    if isinstance(t, Enum):
        if not isinstance(v, str) or v not in t.vs:
            errs.append("%s: %s, not one of %s" % (path, json.dumps(v)[:60], ", ".join(t.vs)))
        return
    if isinstance(t, Re):
        if ctx.golden and t.golden_key and v == GOLDEN[t.golden_key]:
            return
        if not isinstance(v, str) or not t.re.match(v):
            errs.append("%s: %s, not %s" % (path, json.dumps(v)[:80], t.what))
        return
    if isinstance(t, List):
        if not isinstance(v, list):
            errs.append("%s: %s, not a list" % (path, tname(v)))
            return
        for i, x in enumerate(v):
            check(x, t.t, "%s[%d]" % (path, i), errs, ctx)
        return
    if isinstance(t, Obj):
        if not isinstance(v, dict):
            errs.append("%s: %s, not an object" % (path, tname(v)))
            return
        for k, ft in t.fields.items():
            opt = isinstance(ft, Opt)
            if k not in v:
                if not opt:
                    errs.append("%s: no %r" % (path, k))
                continue
            check(v[k], ft.t if opt else ft, "%s.%s" % (path, k), errs, ctx)
        if t.values is not None:
            for k, x in v.items():
                check(x, t.values, "%s.%s" % (path, k), errs, ctx)
        return
    raise AssertionError("unknown type %r" % (t,))


# ---- the documents ----------------------------------------------------------

def header(fmt, **more):
    f = {"format": Const(fmt), "schema": Const(SCHEMA), "version": VERSION, "generated": STAMP, "dry_run": BOOL}
    f.update(more)
    return Obj(f)


CODED = Obj({"code": STR, "text": STR})

ERROR = header("bko-holt-error", exit=INT, code=Enum(*ERROR_CODES), text=STR)

# dump_schemas: Opt, as recordings from before 2026-11-26 (holt-json.md's open
# question 11) don't carry it; the marker's is null when it names none.
API = header("bko-holt-api", documents=Obj(values=List(INT)), dump_schemas=Opt(List(INT)), features=List(STR),
             platform=Obj({"os": STR, "arch": STR}), run_dir=STR, no_commit=BOOL)

DISK = {"node": STR, "path": STR, "by_id": S_, "size_bytes": INT, "model": S_, "serial": S_, "wwid": S_,
        "sector": INT, "transport": S_, "removable": B_}
CONTENT = Obj({"dev": STR, "type": S_, "label": S_, "size_bytes": I_, "used_bytes": I_})
SIZE = Obj({"source": STR, "verdict": STR, "fit_pct": I_, "change_pct": Or(NUM, NULL), "partition": S_,
            "mount": S_, "old_bytes": I_, "new_bytes": I_, "used_bytes": I_, "margin_bytes": I_,
            "needed_bytes": I_, "most_bytes": I_, "data_fits": B_, "reason": S_, "grow": ANY, "why": S_})
TARGET = Obj(dict(DISK, matched_by=Enum("map", "wwid", "serial"), same_identity=BOOL, blank=BOOL,
                  contents=List(CONTENT), ours=BOOL, recorded=BOOL, erase=BOOL, will_erase=BOOL,
                  writes=List(STR), size=Or(NULL, SIZE)))
CANDIDATE = Obj(dict(DISK, serial_shared=BOOL, target_of=S_, ours=BOOL, blank=BOOL, contents=List(CONTENT),
                     refused=Or(NULL, CODED), erase_needed=BOOL, sizes=List(SIZE)))
SOURCE_DISK = Obj({"name": STR, "size_bytes": I_, "model": S_, "serial": S_, "wwid": S_, "sector": I_, "table": S_,
                   "mounts": List(STR),
                   "largest": Or(NULL, Obj({"partition": STR, "size_bytes": INT, "holds": S_, "mount": S_, "used_bytes": I_}))})
KEY = Obj({"group": STR, "kind": Enum("luks", "luks_kept_header", "zfs", "geli", "softraid"), "names": List(STR),
           "devices": List(STR), "holds": List(STR),
           "unlock": Enum("passphrase", "old_passphrase", "temporary", "key_file_elsewhere"),
           "given": BOOL, "new_ok": BOOL, "min_bytes": INT, "max_bytes": INT, "shared_at_boot": BOOL})
PLAN_CMD = Obj({"kind": Enum(*KINDS), "argv": List(STR), "env": List(List(STR)), "why": S_, "path": S_, "dev": S_,
                "stdin": S_, "stdin_bytes": I_, "stdin_sha256": Or(SHA, NULL), "private": BOOL, "archiver": BOOL})
PLAN = header(
    "bko-holt-plan",
    ok=BOOL, exit=INT, plan_sha256=Or(SHA, NULL),
    layout=Obj({"path": STR, "sha256": SHA}),
    args=List(STR),
    source=Obj({"hostname": S_, "os": Obj({"id": S_, "version_id": S_, "name": S_}), "family": STR, "arch": S_,
                "boot_mode": S_, "loader": S_, "generated": S_, "dmi_uuid": S_, "reformat_possible": BOOL}),
    here=Obj({"dmi_uuid": S_, "boot_mode": S_, "efivars": B_, "arch": STR, "privileged": BOOL, "same_machine": BOOL}),
    disks=List(Obj({"source": SOURCE_DISK, "target": Or(NULL, TARGET)})),
    candidates=List(CANDIDATE),
    erase=List(STR),
    keys=List(KEY),
    tokens=List(Obj({"container": STR, "kind": S_})),
    reformat=List(Obj({"mount": STR, "type": S_, "to": Obj(values=Or(NULL, Obj({"code": STR, "reason": STR, "text": STR})))})),
    fit=List(Obj({"disk": STR, "target": STR})),
    problems=List(CODED), notes=List(CODED),
    stages=List(Obj({"name": Enum(*STAGES), "commands": List(PLAN_CMD)})),
    text=STR)

STATUS = header(
    "bko-holt-status",
    exit=INT, target=STR,
    stages=List(Obj({"name": Enum(*STAGES), "state": Enum(*STATES), "why": STR})),
    next=Or(Enum(*STAGES), NULL), all_done=BOOL,
    marks=Obj({"partitioned": List(STR), "restore_started": BOOL, "restore_complete": BOOL, "boot": BOOL}),
    targets=List(Obj({"source": STR, "node": STR, "ours": BOOL, "same_identity": BOOL, "recorded": BOOL})),
    temporary_keys=List(Obj({"name": STR, "kept": BOOL})),
    resume=Obj({"go_on": BOOL, "why": List(STR)}),
    open=Obj({"containers": List(Obj({"source": STR, "node": STR})), "pools": List(STR), "mounted": List(STR)}),
    problems=List(CODED))

# bko-holt stick --json (RT10k): the config partition and its machines.
UUID = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
CONFIG_STATES = ("ok", "missing", "damaged", "two_sticks", "unreadable")
MACHINE_STATES = ("ok", "checksum_bad", "checksum_missing", "schema_unknown", "unreadable")
MACHINE = Obj({"install_id": STR, "path": STR, "state": Enum(*MACHINE_STATES), "why": S_, "schema": I_,
               "known_schema": BOOL, "hostname": S_, "os": Obj({"id": S_, "version_id": S_}), "written": S_,
               "sequence": I_, "dmi_uuid": S_, "this_computer": BOOL,
               "repository": Or(NULL, Obj({"kind": S_, "url": S_})), "repository_id": S_,
               "keyboard": Or(NULL, Obj()), "time": Or(NULL, Obj()), "password_hint": S_})
STICK = header(
    "bko-holt-stick",
    boot_disk=Or(NULL, Obj({"node": STR, "found_by": Enum("cdrom_mount", "root_mount", "boot_mount", "marker_scan")})),
    config=Obj({"state": Enum(*CONFIG_STATES), "partition": I_, "label": S_, "why": S_, "dev": S_}),
    image=Or(NULL, Obj({"image_id": STR, "platform": S_, "built": S_, "system_schemas": List(INT),
                            "dump_schemas": Opt(Or(NULL, List(INT)))})),
    stick=Or(NULL, Obj({"stick_id": S_, "name": S_})),
    here=Obj({"dmi_uuid": S_}),
    machines=List(MACHINE), notes=List(CODED))

# bko-holt stick --copy INSTALL_ID OUT --json (open question 8): the layout
# system.json carries, written to OUT (a golden's out is "OUT").
STICK_COPY = header(
    "bko-holt-stick-copy",
    install_id=STR, path=STR, out=STR, bytes=INT, sha256=SHA, system_sha256=SHA)

# bko-holt recovery-words OUT --json (RT10p): the words' verdict, never the words.
RECOVERY = header(
    "bko-holt-recovery",
    ok=BOOL, words=INT, expected=INT, problem=Or(NULL, Enum("unknown_words", "count", "checksum")),
    unknown=List(Obj({"position": INT, "short": BOOL, "suggest": List(STR)})),
    checksum_ok=B_, written=BOOL, error=S_)

DOCUMENTS = {"bko-holt-plan": PLAN, "bko-holt-status": STATUS, "bko-holt-stick": STICK, "bko-holt-stick-copy": STICK_COPY,
             "bko-holt-recovery": RECOVERY,
             "bko-holt-error": ERROR, "bko-holt-api": API}

# ---- the events -------------------------------------------------------------

EVENTS = {
    "start": Obj({"format": Const("bko-holt-events"), "schema": Const(SCHEMA), "version": VERSION, "generated": STAMP,
                  "pid": INT, "commit": BOOL, "dry_run": BOOL, "stages": List(STR), "stage_commands": Obj(values=INT),
                  "plan_sha256": Or(SHA, NULL), "log": S_, "layout": Or(NULL, Obj({"hostname": S_, "generated": S_}))}),
    "problems": Obj({"problems": List(CODED), "exit": Const(4)}),
    "stage": Obj({"stage": STR, "commands": INT}),
    "skip": Obj({"stage": STR, "state": Enum("done", "nothing"), "why": STR}),
    "cmd": Obj({"stage": STR, "n": INT, "of": INT, "kind": Enum(*KINDS), "argv": List(STR), "env": List(List(STR)),
                "why": S_, "path": S_, "dev": S_, "stdin_bytes": I_, "private": BOOL,
                "archiver": Or(NULL, Obj({"part": INT, "parts": INT})), "would": BOOL}),
    "progress": Obj({"stage": STR, "n": INT, "part": INT, "parts": INT, "bytes": I_, "total_bytes": I_, "secs": NUM,
                     "errors": INT}),
    "warning": Obj({"stage": STR, "n": INT, "source": Enum("archiver", "cmd"), "text": STR, "item": S_}),
    "cmd_done": Obj({"stage": STR, "n": INT, "status": I_, "signal": I_, "secs": NUM}),
    "stage_done": Obj({"stage": STR, "secs": NUM, "checked": BOOL}),
    "stopping": Obj({"signal": STR}),
    "failed": Obj({"stage": S_, "n": I_, "code": Enum(*FAILURE_CODES), "program": S_, "argv": Or(NULL, List(STR)),
                   "status": I_, "signal": I_, "text": STR, "tail": List(STR), "log": S_}),
    "end": Obj({"exit": INT, "stopped": BOOL, "secs": NUM}),
}


def version_tuple(v):
    m = re.match(r"^(\d+)\.(\d+)\.(\d+)", v) if isinstance(v, str) else None
    return tuple(int(x) for x in m.groups()) if m else None


def check_document(v, where):
    """What is wrong with one document V, as a list of reasons."""
    if not isinstance(v, dict):
        return ["%s: %s, not a document" % (where, tname(v))]
    fmt = v.get("format")
    t = DOCUMENTS.get(fmt)
    if t is None:
        return ["%s: format %s, not a document this checker knows" % (where, json.dumps(fmt))]
    if v.get("schema") != SCHEMA:
        return ["%s: %s schema %s, not %d" % (where, fmt, json.dumps(v.get("schema")), SCHEMA)]
    errs = []
    check(v, t, where, errs, Ctx(golden=v.get("version") == GOLDEN["version"]))
    if fmt == "bko-holt-plan" and isinstance(v.get("ok"), bool) and isinstance(v.get("problems"), list):
        if v["ok"] != (not v["problems"]):
            errs.append("%s: ok is %s with %d problems" % (where, v["ok"], len(v["problems"])))
        if v.get("exit") != (0 if v["ok"] else 4):
            errs.append("%s: exit %s with ok %s" % (where, v.get("exit"), v["ok"]))
    if fmt == "bko-holt-stick" and not errs:
        state = v["config"]["state"]
        if state != "ok" and (v["machines"] or v["stick"] is not None):
            errs.append("%s: config %s, yet machines or stick given" % (where, state))
        if (state == "ok") != (v["config"]["why"] is None):
            errs.append("%s: config %s with why %s" % (where, state, json.dumps(v["config"]["why"])))
        for i, m in enumerate(v["machines"]):
            if (m["state"] == "ok") != (m["why"] is None) or (m["state"] == "ok" and not m["known_schema"]):
                errs.append("%s: machines[%d]: state %s, why %s, known_schema %s" % (where, i, m["state"], json.dumps(m["why"]), m["known_schema"]))
            # A machine's directory is its install ID, a UUID (never ../..).
            if not UUID.fullmatch(m["install_id"]) or m["path"] != "backotter/machines/%s/system.json" % m["install_id"]:
                errs.append("%s: machines[%d]: install_id %s, path %s" % (where, i, json.dumps(m["install_id"]), json.dumps(m["path"])))
        if sum(1 for m in v["machines"] if m["this_computer"]) > 1:
            errs.append("%s: more than one machine is this computer" % where)
    if fmt == "bko-holt-stick-copy" and not errs:
        if not UUID.fullmatch(v["install_id"]) or v["path"] != "backotter/machines/%s/system.json" % v["install_id"]:
            errs.append("%s: install_id %s, path %s" % (where, json.dumps(v["install_id"]), json.dumps(v["path"])))
        if not (v["out"].startswith("/") or v["out"] == "OUT"):
            errs.append("%s: out %s isn't absolute" % (where, json.dumps(v["out"])))
    if fmt == "bko-holt-recovery" and not errs:
        if v["ok"] != (v["problem"] is None) or (v["ok"] and v["checksum_ok"] is not True):
            errs.append("%s: ok %s with problem %s, checksum_ok %s" % (where, v["ok"], json.dumps(v["problem"]), v["checksum_ok"]))
        if v["written"] and not v["ok"]:
            errs.append("%s: written without ok" % where)
        if (v["problem"] == "unknown_words") != bool(v["unknown"]):
            errs.append("%s: problem %s with %d unknown words" % (where, json.dumps(v["problem"]), len(v["unknown"])))
        if any(len(u["suggest"]) > 3 for u in v["unknown"]):
            errs.append("%s: more than 3 suggestions for a word" % where)
    return errs


def check_stream(lines, where):
    """What is wrong with an events stream (LINES: (number, text) pairs),
    as a list of reasons: each event's fields, and their order."""
    errs = []
    evs = []
    for n, l in lines:
        try:
            evs.append((n, json.loads(l)))
        except ValueError as e:
            errs.append("%s:%d: not JSON (%s)" % (where, n, e))
    if errs:
        return errs
    if not evs:
        return ["%s: no events" % where]
    n0, first = evs[0]
    if isinstance(first, dict) and "event" not in first and first.get("format") == "bko-holt-error":
        errs = check_document(first, "%s:%d" % (where, n0))
        if len(evs) > 1:
            errs.append("%s:%d: a line after the bko-holt-error document, which is the whole stream" % (where, evs[1][0]))
        return errs
    stages = None       # start's names, when it gave any
    open_stage = None   # the stage inside which events go now
    pending = None      # a command that ran: (n), until its cmd_done
    last_n = 0
    seen = set()
    ended = False
    problems = False
    commit = None       # start's
    for i, (n, e) in enumerate(evs):
        at = "%s:%d" % (where, n)
        if not isinstance(e, dict) or not isinstance(e.get("event"), str):
            errs.append("%s: no \"event\"" % at)
            continue
        name = e["event"]
        if ended:
            errs.append("%s: %s after end" % (at, name))
            continue
        if i == 0 and name != "start":
            errs.append("%s: %s first, not start" % (at, name))
        t = EVENTS.get(name)
        if t is None:
            errs.append("%s: an event this checker doesn't know: %s" % (at, json.dumps(name)))
            continue
        if name == "start":
            if i != 0:
                errs.append("%s: a start that isn't first" % at)
            if e.get("schema") != SCHEMA or e.get("format") != "bko-holt-events":
                errs.append("%s: format %s schema %s, not bko-holt-events schema %d" % (at, json.dumps(e.get("format")), json.dumps(e.get("schema")), SCHEMA))
                return errs
        ev_errs = []
        check(e, t, "%s %s" % (at, name), ev_errs, Ctx(golden=e.get("version") == GOLDEN["version"], events=True))
        errs.extend(ev_errs)
        if name == "end":
            ended = True
        if ev_errs:
            continue
        st = e.get("stage")
        if name == "start":
            vt = version_tuple(e["version"])
            if vt is not None and vt < MIN_VERSION:
                errs.append("%s: version %s, older than %s, the first to write schema %d" % (at, e["version"], ".".join(map(str, MIN_VERSION)), SCHEMA))
            stages = e["stages"] or None
            commit = e["commit"]
            for s in e["stages"]:
                if s not in STAGES:
                    errs.append("%s: start names a stage %s" % (at, json.dumps(s)))
            continue
        if name in ("stage", "skip") and stages is not None and st not in stages:
            errs.append("%s: %s %s, not one of start's stages" % (at, name, json.dumps(st)))
        if name in ("stage", "skip"):
            if problems:
                errs.append("%s: %s %s after problems, when nothing runs" % (at, name, st))
            if open_stage is not None:
                errs.append("%s: %s while %s is still going" % (at, name, open_stage))
            if st in seen:
                errs.append("%s: %s %s a second time" % (at, name, st))
            seen.add(st)
            open_stage = st if name == "stage" else None
            last_n = 0
            continue
        if name == "problems":
            if seen:
                errs.append("%s: problems after a stage began" % at)
            problems = True
            continue
        if name == "stopping":
            continue
        if name == "end":
            if pending is not None:
                errs.append("%s: end with command %d of %s still running" % (at, pending, open_stage))
            if problems and e["exit"] != 4:
                errs.append("%s: end %d after problems, not 4" % (at, e["exit"]))
            if e["stopped"] and e["exit"] != 5:
                errs.append("%s: stopped, but exit %d, not 5" % (at, e["exit"]))
            if e["exit"] == 5 and not e["stopped"]:
                errs.append("%s: exit 5, but not stopped" % at)
            if e["exit"] == 0 and open_stage is not None:
                errs.append("%s: end 0 while %s is still going" % (at, open_stage))
            continue
        if name == "failed":
            if st is not None and open_stage is not None and st != open_stage:
                errs.append("%s: failed in %s while %s is going" % (at, st, open_stage))
            open_stage, pending = None, None
            continue
        # cmd, progress, warning, cmd_done, stage_done: inside their stage.
        if st != open_stage:
            errs.append("%s: %s in %s, but the stage going is %s" % (at, name, json.dumps(st), open_stage))
            continue
        if name == "cmd":
            if pending is not None:
                errs.append("%s: cmd %d before cmd %d's cmd_done" % (at, e["n"], pending))
            if not 1 <= e["n"] <= e["of"] or e["n"] <= last_n:
                errs.append("%s: cmd %d of %d after %d" % (at, e["n"], e["of"], last_n))
            last_n = e["n"]
            if commit is False and not e["would"]:
                errs.append("%s: cmd %d runs, but start says the run doesn't commit" % (at, e["n"]))
            pending = None if e["would"] else e["n"]
        elif name in ("progress", "warning"):
            if e["n"] != last_n:
                errs.append("%s: %s for command %d, the last was %d" % (at, name, e["n"], last_n))
        elif name == "cmd_done":
            if pending != e["n"]:
                errs.append("%s: cmd_done %d, but no command %d is running (one that would run has no cmd_done)" % (at, e["n"], e["n"]))
            pending = None
        elif name == "stage_done":
            if pending is not None:
                errs.append("%s: stage_done with command %d still running" % (at, pending))
            open_stage, pending = None, None
    if not ended:
        errs.append("%s: no end" % where)
    return errs


# ---- files ------------------------------------------------------------------

def stream_lines(text, fake):
    """An events file's lines, (number, text); the fake's own lines left out
    when FAKE."""
    out = []
    for n, raw in enumerate(text.splitlines(), 1):
        l = raw.strip()
        if not l:
            continue
        if fake and (l.startswith("#") or l.split()[0] in ("sleep", "delay")):
            continue
        out.append((n, l))
    return out


def is_stream(path):
    b = os.path.basename(path)
    return b == "events" or b.endswith(".events") or b.endswith(".ndjson")


def check_file(path, fake=False):
    try:
        with open(path, encoding="utf-8") as f:
            text = f.read()
    except (OSError, UnicodeDecodeError) as e:
        return ["%s: %s" % (path, e)]
    if is_stream(path):
        return check_stream(stream_lines(text, fake), path)
    try:
        v = json.loads(text)
    except ValueError as e:
        return ["%s: not JSON (%s)" % (path, e)]
    return check_document(v, path)


def check_call(d):
    """A fake scenario's call: its stdout and its events.  Returns (reasons,
    files checked)."""
    errs, n = [], 0
    for name in ("stdout", "events"):
        p = os.path.join(d, name)
        if os.path.isfile(p) and os.path.getsize(p) > 0:
            n += 1
            errs.extend(check_file(p, fake=True))
    inv = os.path.join(d, "invalid")
    if os.path.isfile(inv):
        with open(inv) as f:
            words = (f.readline().strip() or "?")
        if not errs:
            return ["%s: marked invalid (%s), but its files pass" % (d, words)], n
        if not any(words in e for e in errs):
            return ["%s: marked invalid (%s), but it fails for another reason: %s" % (d, words, errs[0])], n
        return [], n
    return errs, n


def walk(top):
    """Every file and call to check under TOP: (kind, path)."""
    out = []
    for d, subdirs, files in os.walk(top):
        subdirs.sort()
        if "argv" in files:
            out.append(("call", d))
            subdirs[:] = []
            continue
        for f in sorted(files):
            p = os.path.join(d, f)
            if f in ("plan.json", "status.json") or (f.endswith(".json") and os.path.basename(d).endswith("-json")) or is_stream(p):
                out.append(("file", p))
    return out


def main(argv):
    quiet = "--quiet" in argv
    paths = [a for a in argv if a != "--quiet"]
    if not paths or any(a.startswith("-") for a in paths):
        print(__doc__.strip().split("\n\n")[1], file=sys.stderr)
        return 2
    bad, n = [], 0
    for p in paths:
        if os.path.isdir(p):
            todo = walk(p)
            if not todo:
                bad.append("%s: nothing to check there" % p)
        elif os.path.exists(p):
            todo = [("file", p)]
        else:
            bad.append("%s: no such file or directory" % p)
            todo = []
        for kind, x in todo:
            if kind == "call":
                errs, k = check_call(x)
                n += k
            else:
                errs = check_file(x)
                n += 1
            bad.extend(errs)
            if not quiet:
                print(("ok    " if not errs else "FAIL  ") + x)
    for e in bad:
        print("  " + e)
    print("%d checked, %d problems" % (n, len(bad)))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
