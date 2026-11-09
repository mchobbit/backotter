"""The ledger: one append-only JSONL file per machine (contract 1.2; rulings R8, R11, R26).

Files: HOST_ROOT/ledger/HOST.jsonl on each host (T2_LEDGER replaces it, for
tests) and MAC_ROOT/ledger/mac.jsonl on the Mac.  A row goes into the file of
the machine whose resources ran its cell; derived rows (carried, class) go
into mac.jsonl.  On the Mac, MAC_ROOT/ledger/HOST.jsonl are the hosts' files
pulled (pull()), HOST.lost.jsonl the rows a host's file has lost since,
pull.json the pulls' state and wear-HOST.json the wear join's cache.

A row is one canonical JSON object (keys sorted, no spaces, ASCII) and a
newline, at most 64 KiB.  Writing (Ledger.append):
  1. open O_RDWR|O_APPEND|O_CREAT (read access: for the last byte), flock
     LOCK_EX (retried for 30 s, else LockTimeout), read the last byte;
  2. a non-empty file not ending in a newline has a killed writer's torn
     line: the write starts with a newline (when that line is a whole row
     that lost only its newline, its origin counts: see below);
  3. the row and its newline in one os.write (looped on a short write),
     fsync, unlock.
A row whose origin is already in the file (a valid row) is not appended
again: its old id comes back.  A reader skips, and counts, a line that isn't
valid JSON, lacks v or id, or whose c doesn't match; a torn last line with no
newline is skipped silently.

A writer running as root creates missing directories (0755) and the file
(0644) with owner SUDO_UID:SUDO_GID when those are set, else the owner of
the nearest existing ancestor, and never changes an existing file's owner.

complete() fills a row's defaults, id, time, host, key and groups (from its
inputs) and c; validate() lists what is wrong with it; scrub() takes the
host's own disk serials out of its free text (fails, note, reasons, extra,
meta) before it is written.
"""

import errno
import fcntl
import json
import os
import random
import re
import sys
import threading
import time

from . import (CLASSES, FAMILIES, GROUPS, HEX16, HEX64, KINDS, NEED_VALUES, UTC_RE, V, VERDICTS,
               canonical, is_root, mac_root, parse_utc, sha256_hex, short_host, utc_now, utc_stamp)

MAX_LINE = 65536
LOCK_WAIT = 30.0
REASONS_MAX, TEXT_MAX = 20, 200
WEAR_WINDOW = (60, 900)   # R26: a jobs.log line counts for a row when it ends in [start - 60 s, t + 900 s]

ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9.-]*-\d{8}T\d{6}Z-[0-9a-f]{8}\Z")
COMMIT_RE = re.compile(r"[0-9a-f]{7,40}\Z")

# field: (type, nullable)
FIELDS = {
    "v": ("int", False), "id": ("str", False), "t": ("str", False), "start": ("str", True),
    "host": ("str", False), "origin": ("str", False), "family": ("str", False), "cell": ("str", False),
    "kind": ("str", False), "run": ("str", False), "word": ("str", False), "rule": ("str", False),
    "reasons": ("list", False), "attempt": ("int", False), "key": ("str", False), "groups": ("dict", False),
    "inputs": ("dict", False), "spec": ("dict", True), "planned_key": ("str", True), "valid": ("bool", False),
    "verdict": ("str", False), "class": ("str", False), "needs": ("dict", False), "counts": ("dict", False),
    "fails": ("list", False), "gm": ("num", False), "wall_s": ("int", False), "waits_s": ("int", False),
    "wear": ("dict", False), "ram_peak_kib": ("int", True), "seed": ("str", True), "commit": ("str", True),
    "committed": ("bool", False), "dirty": ("dict", True), "copy": ("str", False), "logs": ("str", True),
    "meta": ("dict", False), "extra": ("dict", False), "via": ("str", True), "note": ("str", True),
    "c": ("str", False),
}
# What complete() fills when a writer leaves it out.  The rest a writer must give:
# origin, family, cell, kind, run, word, rule, attempt, inputs, valid, verdict,
# class, commit, committed, copy (and start for a run row).
DEFAULTS = {
    "v": V, "start": None, "reasons": [], "spec": None, "planned_key": None, "needs": {},
    "counts": {}, "fails": [], "gm": 0.0, "wall_s": 0, "waits_s": 0, "wear": {}, "ram_peak_kib": None,
    "seed": None, "dirty": None, "logs": None, "meta": {}, "extra": {}, "via": None, "note": None,
}
COUNT_KEYS = ("PASS", "FAIL", "LIMIT")
# The classes each verdict may have (the stop rules read class; the writers set both).
VERDICT_CLASSES = {
    "PASS": ("pass", "known-flake"), "FAIL": ("fail", "harness"), "FLAKY-FAIL": ("fail",),
    "HARNESS": ("harness",), "LOST": ("lost", "harness"),
}
NEEDS_FAMILIES = ("stic", "holt")   # their PASS run rows always name their needs


class LedgerError(Exception):
    pass


class LockTimeout(LedgerError):
    pass


class InvalidRow(LedgerError):
    def __init__(self, errors):
        super().__init__("; ".join(errors))
        self.errors = errors


# --- rows -----------------------------------------------------------------------------

def make_id(host, t=None):
    """HOST-YYYYMMDDTHHMMSSZ-xxxxxxxx."""
    return f"{host}-{utc_stamp(t)}-{os.urandom(4).hex()}"


def row_c(row):
    """The first 16 hex of the sha256 of the canonical JSON of ROW without c."""
    return sha256_hex(canonical({k: v for k, v in row.items() if k != "c"}))[:16]


def _cut(s, n=TEXT_MAX):
    s = str(s)
    return s if len(s) <= n else s[:n - 3] + "..."


def scrub_text(s, serials):
    for ser in serials:
        if ser and ser in s:
            s = s.replace(ser, "[serial]")
    return s


def _scrub_deep(o, serials):
    if isinstance(o, str):
        return scrub_text(o, serials)
    if isinstance(o, list):
        return [_scrub_deep(x, serials) for x in o]
    if isinstance(o, dict):
        return {k: _scrub_deep(v, serials) for k, v in o.items()}
    return o


def scrub(row, serials):
    """ROW with SERIALS taken out of its free text: fails, note, reasons, extra, meta."""
    if not serials:
        return row
    r = dict(row)
    for k in ("fails", "note", "reasons", "extra", "meta"):
        if k in r and r[k] is not None:
            r[k] = _scrub_deep(r[k], serials)
    return r


_serials_cache = None


def local_serials():
    """This host's disk serials (/sys/block/*/device/serial, lsblk's SERIAL), longest
    first, for scrub().  They never leave this process."""
    global _serials_cache
    if _serials_cache is not None:
        return _serials_cache
    found = set()
    try:
        for d in os.listdir("/sys/block"):
            for f in ("device/serial", "serial"):
                try:
                    with open(os.path.join("/sys/block", d, f), "rb") as fh:
                        s = fh.read().decode("ascii", "replace").strip()
                    if len(s) >= 6:
                        found.add(s)
                except OSError:
                    pass
    except OSError:
        pass
    try:
        import subprocess
        out = subprocess.run(["lsblk", "-dnro", "SERIAL"], capture_output=True, text=True, timeout=5).stdout
        found.update(s.strip() for s in out.split("\n") if len(s.strip()) >= 6)
    except (OSError, ValueError, Exception):
        pass
    _serials_cache = sorted(found, key=len, reverse=True)
    return _serials_cache


def complete(row, host=None, t=None, serials=None):
    """A copy of ROW with its defaults, v, host, t, id, key and groups (from
    inputs, when it has none) filled, its texts cut to size, serials scrubbed,
    and c computed.  It doesn't validate: validate() does."""
    from . import keys
    r = dict(row)
    for k, v in DEFAULTS.items():
        if k not in r:
            r[k] = json.loads(json.dumps(v))
    r["v"] = r.get("v") or V
    r["host"] = r.get("host") or host or short_host()
    r["t"] = r.get("t") or utc_now(t)
    r["id"] = r.get("id") or make_id(r["host"], t)
    if isinstance(r.get("reasons"), list):
        r["reasons"] = [_cut(x) for x in r["reasons"][:REASONS_MAX]]
    if isinstance(r.get("fails"), list):
        r["fails"] = [_cut(x) for x in r["fails"][:REASONS_MAX]]
    if isinstance(r.get("counts"), dict):
        r["counts"] = dict({k: 0 for k in COUNT_KEYS}, **r["counts"])
    for k in ("wall_s", "waits_s"):
        if isinstance(r.get(k), float) and r[k] == r[k]:
            r[k] = int(round(r[k]))
    if isinstance(r.get("gm"), int) and not isinstance(r.get("gm"), bool):
        r["gm"] = float(r["gm"])
    if isinstance(r.get("gm"), float):
        r["gm"] = round(r["gm"], 3)
    if isinstance(r.get("inputs"), dict) and isinstance(r.get("cell"), str):
        try:
            r.setdefault("key", keys.key(r["cell"], r["inputs"]))
            r.setdefault("groups", keys.groups(r["inputs"]))
        except Exception:
            pass
    if serials:
        r = scrub(r, serials)
    r.pop("c", None)
    r["c"] = row_c(r)
    return r


def _type_ok(v, typ, nullable):
    if v is None:
        return nullable
    if typ == "int":
        return isinstance(v, int) and not isinstance(v, bool)
    if typ == "num":
        return isinstance(v, (int, float)) and not isinstance(v, bool) and v == v
    return isinstance(v, {"str": str, "bool": bool, "dict": dict, "list": list}[typ])


def validate(row, check_c=True):
    """What is wrong with ROW, as a list of messages (empty: it is a valid row)."""
    from . import keys
    err = []
    if not isinstance(row, dict):
        return ["not a JSON object"]
    for f, (typ, nullable) in FIELDS.items():
        if f not in row:
            err.append(f"no {f}")
        elif not _type_ok(row[f], typ, nullable):
            err.append(f"{f}: not a {typ}{' or null' if nullable else ''}: {row[f]!r:.60}")
    if err:
        return err
    if row["v"] != V:
        err.append(f"v: {row['v']}, not {V}")
    if not ID_RE.match(row["id"]):
        err.append(f"id: bad form {row['id']!r}")
    for f in ("t", "start"):
        if row[f] is not None and not UTC_RE.match(row[f]):
            err.append(f"{f}: not a UTC time YYYY-MM-DDTHH:MM:SSZ: {row[f]!r}")
    if row["family"] not in FAMILIES:
        err.append(f"family: {row['family']!r} not one of {', '.join(FAMILIES)}")
    if not row["cell"].startswith(row["family"] + ":") or len(row["cell"]) <= len(row["family"]) + 1:
        err.append(f"cell: {row['cell']!r} is not {row['family']}:NAME")
    if row["kind"] not in KINDS:
        err.append(f"kind: {row['kind']!r} not one of {', '.join(KINDS)}")
    if row["kind"] == "run" and row["start"] is None:
        err.append("start: a run row needs its start time")
    if row["kind"] in ("carried", "class") and not row["via"]:
        err.append(f"via: a {row['kind']} row names the row it comes from")
    if row["verdict"] not in VERDICTS:
        err.append(f"verdict: {row['verdict']!r} not one of {', '.join(VERDICTS)}")
    if row["class"] not in CLASSES:
        err.append(f"class: {row['class']!r} not one of {', '.join(CLASSES)}")
    if row["attempt"] < 1:
        err.append("attempt: 1 or more")
    for f in ("origin", "run", "word", "rule", "copy"):
        if not row[f]:
            err.append(f"{f}: empty")
    if not all(isinstance(x, str) and len(x) <= TEXT_MAX for x in row["reasons"]) or len(row["reasons"]) > REASONS_MAX:
        err.append(f"reasons: at most {REASONS_MAX} strings of {TEXT_MAX} characters")
    if not all(isinstance(x, str) and len(x) <= TEXT_MAX for x in row["fails"]) or len(row["fails"]) > REASONS_MAX:
        err.append(f"fails: at most {REASONS_MAX} strings of {TEXT_MAX} characters")
    inputs = row["inputs"]
    for g, names in inputs.items():
        if g not in GROUPS:
            err.append(f"inputs: unknown group {g!r}")
        elif not isinstance(names, dict):
            err.append(f"inputs.{g}: not an object")
        else:
            for n, v in names.items():
                if not keys.valid_value(v):
                    err.append(f"inputs.{g}.{n}: bad value {v!r:.80}")
    if err:
        return err
    if not HEX64.match(row["key"]):
        err.append("key: not 64 hex")
    elif row["key"] != keys.key(row["cell"], inputs):
        err.append("key: not the key of the row's cell and inputs")
    gd = keys.groups(inputs)
    if set(row["groups"]) != set(GROUPS) or any(not isinstance(v, str) or not HEX16.match(v) for v in row["groups"].values()):
        err.append(f"groups: one 16-hex digest for each of {', '.join(GROUPS)}")
    elif row["groups"] != gd:
        err.append("groups: not the digests of the row's inputs")
    if row["planned_key"] is not None and not HEX64.match(row["planned_key"]):
        err.append("planned_key: not 64 hex")
    elif row["valid"] and row["planned_key"] is not None and row["planned_key"] != row["key"]:
        err.append("valid: true, but the key isn't planned_key (such a row is valid: false)")
    if row["class"] not in VERDICT_CLASSES.get(row["verdict"], (row["class"],)):
        err.append(f"class: {row['class']!r} doesn't go with verdict {row['verdict']} "
                   f"({', '.join(VERDICT_CLASSES[row['verdict']])})")
    if row["kind"] == "run" and row["verdict"] == "PASS" and row["family"] in NEEDS_FAMILIES:
        bad = sorted(n for n, v in row["needs"].items() if v in ("FAIL", "LIMIT-NEW"))
        if not row["needs"]:
            err.append(f"needs: a {row['family']} PASS run row names what passed")
        elif bad:
            err.append(f"needs: a PASS run row with FAIL or LIMIT-NEW needs ({', '.join(bad[:5])})")
    if row["committed"] and row["commit"] is None:
        err.append("committed: true needs the commit (null: the tree's commit is unknown)")
    if row["spec"] is not None and (not isinstance(row["spec"].get("path"), str)
                                    or not (row["spec"].get("sha256") is None or HEX64.match(str(row["spec"].get("sha256"))))):
        err.append("spec: {path, sha256}")
    for n, v in row["needs"].items():
        if v not in NEED_VALUES:
            err.append(f"needs.{n}: {v!r} not one of {', '.join(NEED_VALUES)}")
    for n, v in row["counts"].items():
        if n not in COUNT_KEYS or not (isinstance(v, int) and not isinstance(v, bool) and v >= 0):
            err.append(f"counts: {n}={v!r} (PASS, FAIL, LIMIT: counts)")
    if row["gm"] < 0 or row["wall_s"] < 0 or row["waits_s"] < 0:
        err.append("gm, wall_s, waits_s: not negative")
    for tg, v in row["wear"].items():
        if v is not None and not (isinstance(v, int) and not isinstance(v, bool) and v >= 0):
            err.append(f"wear.{tg}: bytes or null")
    if row["ram_peak_kib"] is not None and row["ram_peak_kib"] < 0:
        err.append("ram_peak_kib: not negative")
    if row["commit"] is not None and not COMMIT_RE.match(row["commit"]):
        err.append(f"commit: 7 to 40 hex, not {row['commit']!r}")
    if row["dirty"] is not None:
        for p, v in row["dirty"].items():
            if v is not None and not (isinstance(v, str) and HEX64.match(v)):
                err.append(f"dirty.{p}: a sha256 or null")
    if row["logs"] is not None and ":" not in row["logs"]:
        err.append("logs: HOST:/path")
    if check_c:
        if not HEX16.match(row["c"]):
            err.append("c: not 16 hex")
        elif row["c"] != row_c(row):
            err.append("c: not the checksum of the row")
    line = canonical(row)
    if len(line) + 1 > MAX_LINE:
        err.append(f"the row is {len(line) + 1} bytes, over {MAX_LINE}")
    return err


def check(row):
    """ROW if valid, else InvalidRow."""
    e = validate(row)
    if e:
        raise InvalidRow(e)
    return row


def encode(row):
    return (canonical(row) + "\n").encode("ascii")


# --- the writer --------------------------------------------------------------------------

def _owner_for(path):
    """(uid, gid) that a root writer gives what it creates on the way to PATH."""
    su, sg = os.environ.get("SUDO_UID", ""), os.environ.get("SUDO_GID", "")
    if su.isdigit() and sg.isdigit():
        return int(su), int(sg)
    p = os.path.abspath(path)
    while not os.path.exists(p) and os.path.dirname(p) != p:
        p = os.path.dirname(p)
    st = os.stat(p)
    return st.st_uid, st.st_gid


def ensure_dir(d, owner=None):
    """mkdir -p D with mode 0755, each new directory chowned to OWNER (as root)."""
    missing, p = [], os.path.abspath(d)
    while not os.path.isdir(p):
        missing.append(p)
        if os.path.dirname(p) == p:
            break
        p = os.path.dirname(p)
    for q in reversed(missing):
        try:
            os.mkdir(q, 0o755)
        except FileExistsError:
            continue
        os.chmod(q, 0o755)
        if owner is not None and is_root():
            os.chown(q, owner[0], owner[1])


def open_for_append(path):
    """The ledger file's fd (O_RDWR|O_APPEND), made with the ownership rule if new."""
    owner = _owner_for(path) if is_root() else None
    ensure_dir(os.path.dirname(os.path.abspath(path)), owner)
    try:
        fd = os.open(path, os.O_RDWR | os.O_APPEND | os.O_CREAT | os.O_EXCL, 0o644)
    except FileExistsError:
        return os.open(path, os.O_RDWR | os.O_APPEND)
    try:
        os.fchmod(fd, 0o644)
        if owner is not None:
            os.fchown(fd, owner[0], owner[1])
    except OSError:
        os.close(fd)
        raise
    return fd


def _lock(fd, wait, how=fcntl.LOCK_EX):
    end = time.monotonic() + wait
    while True:
        try:
            fcntl.flock(fd, how | fcntl.LOCK_NB)
            return
        except OSError as e:
            if e.errno not in (errno.EAGAIN, errno.EACCES, errno.EWOULDBLOCK):
                raise
        if time.monotonic() >= end:
            raise LockTimeout(f"no lock in {wait:.0f} s")
        time.sleep(random.uniform(0.0005, 0.004))


def _write_all(fd, data):
    view = memoryview(data)
    while view:
        n = os.write(fd, view)
        if n <= 0:
            raise OSError(errno.EIO, "short write")
        view = view[n:]


def parse_line(b):
    """The row on line B (bytes, no newline), or None: not JSON, no v or id, or a bad c."""
    try:
        r = json.loads(b)
    except (ValueError, UnicodeDecodeError):
        return None
    if not isinstance(r, dict) or "v" not in r or "id" not in r:
        return None
    if r.get("c") != row_c(r):
        return None
    return r


def find_origin(fd, size, origin, chunk=8 << 20):
    """The id of the valid row with ORIGIN in the first SIZE bytes of FD, or None.
    It reads in chunks with pread (an mmap of a writable fd costs ~3 ms on macOS),
    and only whole lines count: a torn tail never matches."""
    if size <= 0:
        return None
    needle = b'"origin":' + json.dumps(origin, ensure_ascii=True).encode("ascii")
    pos, carry = 0, b""
    while pos < size:
        b = os.pread(fd, min(chunk, size - pos), pos)
        if not b:
            break
        pos += len(b)
        buf = carry + b
        nl = buf.rfind(b"\n")
        if nl < 0:
            carry = buf
            continue
        whole, carry = buf[:nl + 1], buf[nl + 1:]
        i = whole.find(needle)
        while i >= 0:
            s = whole.rfind(b"\n", 0, i) + 1
            e = whole.find(b"\n", i)
            r = parse_line(whole[s:e])
            if r is not None and r.get("origin") == origin:
                return r["id"]
            i = whole.find(needle, e + 1)
    return None


def _torn_row(fd, size, chunk=1 << 16):
    """The row on FD's last line (it has no newline) if it is a whole valid row, else None."""
    pos, buf = size, b""
    while pos > 0 and len(buf) <= MAX_LINE:
        n = min(chunk, pos)
        pos -= n
        buf = os.pread(fd, n, pos) + buf
        nl = buf.rfind(b"\n")
        if nl >= 0:
            return parse_line(buf[nl + 1:])
    return parse_line(buf) if pos == 0 and len(buf) <= MAX_LINE else None


class Ledger:
    """One ledger file's writer."""

    def __init__(self, path, lock_wait=LOCK_WAIT):
        self.path = path
        self.lock_wait = lock_wait

    def append(self, row):
        """Append ROW (complete and valid); (id, True), or (the old id, False)
        when a row with its origin is already there."""
        return self.append_rows([row])[0]

    def append_rows(self, rows):
        """Append ROWS under one lock: [(id, appended)] in order."""
        rows = [check(r) for r in rows]
        lines = [encode(r) for r in rows]
        fd = open_for_append(self.path)
        out = []
        try:
            _lock(fd, self.lock_wait)
            try:
                written = {}
                for r, line in zip(rows, lines):
                    if r["origin"] in written:
                        out.append((written[r["origin"]], False))
                        continue
                    size = os.fstat(fd).st_size
                    old = find_origin(fd, size, r["origin"])
                    if old is not None:
                        out.append((old, False))
                        continue
                    data = line
                    if size > 0 and os.pread(fd, 1, size - 1) != b"\n":
                        # A killed writer's torn line ends here.  The newline makes it a
                        # whole line: when it is a whole row (it lost only its newline),
                        # its origin counts as present.
                        tr = _torn_row(fd, size)
                        if tr is not None and tr.get("origin") == r["origin"]:
                            _write_all(fd, b"\n")   # it is that origin's row: finish its line
                            os.fsync(fd)
                            written[r["origin"]] = tr["id"]
                            out.append((tr["id"], False))
                            continue
                        data = b"\n" + line
                    _write_all(fd, data)
                    os.fsync(fd)
                    written[r["origin"]] = r["id"]
                    out.append((r["id"], True))
            finally:
                fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)
        return out


def append(path, rows, lock_wait=LOCK_WAIT):
    """Append ROWS (each complete and valid) to the ledger at PATH: [(id, appended)]."""
    if isinstance(rows, dict):
        rows = [rows]
    return Ledger(path, lock_wait).append_rows(rows)


# --- the reader ---------------------------------------------------------------------------

class Read:
    def __init__(self, rows, bad, torn, size):
        self.rows, self.bad, self.torn, self.size = rows, bad, torn, size

    def __iter__(self):
        return iter((self.rows, self.bad, self.torn))


def parse(data):
    """(rows, bad lines, torn) of a ledger file's bytes."""
    lines = data.split(b"\n")
    tail = lines.pop()
    rows, bad = [], 0
    for ln in lines:
        if not ln.strip():
            continue
        r = parse_line(ln)
        if r is None:
            bad += 1
        else:
            rows.append(r)
    return Read(rows, bad, bool(tail), len(data))


def read(path):
    """The valid rows of the ledger at PATH in file order, the bad lines counted."""
    try:
        with open(path, "rb") as f:
            return parse(f.read())
    except FileNotFoundError:
        return Read([], 0, False, 0)


def merge(sources):
    """SOURCES [(name, rows)] -> (rows, warnings): the union deduplicated by id,
    the first kept when the same id has a different c, ordered by (t, id)."""
    seen, out, warn = {}, [], []
    for name, rows in sources:
        for r in rows:
            p = seen.get(r["id"])
            if p is None:
                seen[r["id"]] = (r.get("c"), name)
                out.append(r)
            elif p[0] != r.get("c"):
                warn.append(f"row {r['id']} differs between {p[1]} and {name}: {p[1]}'s kept")
    out.sort(key=lambda r: (r.get("t") or "", r["id"]))
    return out, warn


# --- the Mac's merged view ------------------------------------------------------------------

def ledger_dir(root=None):
    return os.path.join(root or mac_root(), "ledger")


def _read_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, ValueError):
        return default


def _write_atomic(path, data):
    d = os.path.dirname(path)
    os.makedirs(d, exist_ok=True)
    tmp = f"{path}.tmp.{os.getpid()}.{threading.get_ident()}"
    with open(tmp, "wb") as f:
        f.write(data if isinstance(data, bytes) else data.encode("utf-8"))
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


class View:
    """The Mac's merged ledger: rows (union, deduplicated, ordered by (t, id), wear
    filled from the cache), warnings, bad lines per file, and pull.json."""

    def __init__(self, rows, warnings, bad, pull, files):
        self.rows, self.warnings, self.bad, self.pull, self.files = rows, warnings, bad, pull, files

    def by_cell(self):
        out = {}
        for r in self.rows:
            out.setdefault(r["cell"], []).append(r)
        return out

    def unreachable(self):
        return sorted(h for h, s in (self.pull or {}).items() if isinstance(s, dict) and not s.get("ok", True))


def view(root=None, fill_wear=True):
    """The merged view of MAC_ROOT/ledger: mac.jsonl, then the HOST.jsonl
    mirrors and HOST.lost.jsonl files in name order."""
    d = ledger_dir(root)
    names = sorted(n for n in os.listdir(d) if n.endswith(".jsonl")) if os.path.isdir(d) else []
    names.sort(key=lambda n: (n != "mac.jsonl", n.endswith(".lost.jsonl"), n))
    sources, bad, files = [], {}, []
    for n in names:
        rd = read(os.path.join(d, n))
        host = n[:-len(".lost.jsonl")] if n.endswith(".lost.jsonl") else n[:-len(".jsonl")]
        if fill_wear and host != "mac":
            cache = _read_json(os.path.join(d, f"wear-{host}.json"), {}).get("rows", {})
            for r in rd.rows:
                w = r.get("wear") or {}
                ent = cache.get(r["id"]) if any(v is None for v in w.values()) else None
                if ent:
                    r["wear"] = {t: (v if v is not None else ent.get("wear", {}).get(t)) for t, v in w.items()}
        sources.append((n, rd.rows))
        bad[n] = rd.bad
        files.append(n)
    rows, warn = merge(sources)
    pull_state = _read_json(os.path.join(d, "pull.json"), {})
    for h, s in pull_state.items():
        if isinstance(s, dict) and s.get("warning"):
            warn.append(f"{h}: {s['warning']}")
    return View(rows, warn, bad, pull_state, files)


# --- pull --------------------------------------------------------------------------------------

# Runs on a host (python3 -I -B - ROOT OFFSET TAIL): reads HOST_ROOT/ledger/HOST.jsonl
# under a shared lock, released before any output, and writes nothing.  Prints a JSON header line, then the
# file's bytes from OFFSET (when the 4 KiB before it still hash to TAIL) or from
# 0, up to its last newline.
PULL_SCRIPT = r'''
import fcntl, hashlib, json, os, socket, sys, time
root, offset, tail = sys.argv[1], int(sys.argv[2]), sys.argv[3]
root = os.path.expandvars(root)
if root.startswith("~"):
    root = os.path.expanduser(root)
host = socket.gethostname().split(".")[0]
path = os.path.join(root, "ledger", host + ".jsonl")
out = sys.stdout.buffer
def head(**k):
    k.update(host=host, path=path)
    out.write(json.dumps(k, sort_keys=True).encode() + b"\n")
try:
    fd = os.open(path, os.O_RDONLY)
except FileNotFoundError:
    head(mode="none", size=0, upto=0, tail="")
    sys.exit(0)
end = time.monotonic() + 10
while True:
    try:
        fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
        break
    except OSError:
        if time.monotonic() > end:
            break
        time.sleep(0.01)
size = os.fstat(fd).st_size
start, mode = 0, "full"
if 0 < offset <= size:
    lo = max(0, offset - 4096)
    if hashlib.sha256(os.pread(fd, offset - lo, lo)).hexdigest() == tail:
        start, mode = offset, "incr"
chunks, pos = [], start
while pos < size:
    b = os.pread(fd, min(1 << 22, size - pos), pos)
    if not b:
        break
    chunks.append(b)
    pos += len(b)
data = b"".join(chunks)
cut = data.rfind(b"\n") + 1
upto = start + cut
lo = max(0, upto - 4096)
t = hashlib.sha256(os.pread(fd, upto - lo, lo)).hexdigest() if upto else ""
fcntl.flock(fd, fcntl.LOCK_UN)   # the bytes are in memory: a stalled reader never holds up a writer
os.close(fd)
head(mode=mode, size=size, start=start, upto=upto, tail=t)
out.write(data[:cut])
out.flush()
'''


def _tail_sha(data, upto):
    lo = max(0, upto - 4096)
    return sha256_hex(data[lo:upto]) if upto else ""


def _mirror_bytes(path):
    try:
        with open(path, "rb") as f:
            return f.read()
    except FileNotFoundError:
        return b""


def pull_one(host, root=None, remote_root=None, timeout=30, now=None):
    """Fetch HOST's ledger into MAC_ROOT/ledger/HOST.jsonl (incrementally while
    the mirror still matches the host's file, R26) and record the pull in
    pull.json; returns that record.  It writes nothing on the host.  An
    unreachable host keeps its last mirror (ok: false).  Rows that the host's
    file has lost stay, in HOST.lost.jsonl, with a warning."""
    from . import hostrun
    d = ledger_dir(root)
    os.makedirs(d, exist_ok=True)
    mirror = os.path.join(d, f"{host}.jsonl")
    lostf = os.path.join(d, f"{host}.lost.jsonl")
    statef = os.path.join(d, "pull.json")
    prev = _read_json(statef, {}).get(host)
    prev = prev if isinstance(prev, dict) else {}
    old = _mirror_bytes(mirror)
    offset = prev["size"] if isinstance(prev.get("size"), int) and len(old) == prev["size"] else 0
    tail = _tail_sha(old, offset) if offset else ""
    rr = hostrun.host_path(remote_root or os.environ.get("T2_HOST_ROOT") or "/bkoscratch/test2")
    res = hostrun.ssh(host, script=PULL_SCRIPT, args=[rr, str(offset), tail], timeout=timeout)
    with _pull_lock(d):
        state = _read_json(statef, {})
        rec = dict(state.get(host) if isinstance(state.get(host), dict) else {})
        rec["tried"] = utc_now(now)
        if res.rc != 0 or b"\n" not in res.out:
            rec.update(ok=False, error=(res.err.decode("utf-8", "replace").strip()[-300:] or f"rc {res.rc}"))
        elif _mirror_bytes(mirror) != old:
            rec["note"] = "another pull updated the mirror meanwhile; its copy kept"
        else:
            hline, _, data = res.out.partition(b"\n")
            h = json.loads(hline)
            new = old + data if h["mode"] == "incr" else data
            got = parse(data)
            if h["mode"] == "incr":
                nrows, nbad = rec.get("rows", 0) + len(got.rows), rec.get("bad", 0) + got.bad
            else:
                nrows, nbad = len(got.rows), got.bad
                if old:
                    have = {r["id"] for r in got.rows}
                    lost = [r for r in parse(old).rows if r["id"] not in have]
                    if lost:
                        prior = read(lostf).rows
                        known = {r["id"] for r in prior}
                        add = [r for r in lost if r["id"] not in known]
                        if add:
                            _write_atomic(lostf, b"".join(encode(r) for r in prior + add))
            _write_atomic(mirror, new)
            kept = len(read(lostf).rows)
            rec.update(ok=True, t=utc_now(now), mode=h["mode"], size=h["upto"], tail=h["tail"], rows=nrows,
                       bad=nbad, file=h.get("path"), remote_host=h.get("host"), error=None, lost=kept)
            rec.pop("note", None)
            if kept:
                rec["warning"] = (f"{kept} row(s) that {host}'s ledger file no longer has are kept "
                                  f"in {os.path.basename(lostf)}")
            else:
                rec.pop("warning", None)
        state[host] = rec
        _write_atomic(statef, json.dumps(state, indent=1, sort_keys=True) + "\n")
        return rec


class _pull_lock:
    """flock on MAC_ROOT/ledger/.pull.lock: one pull (per host) at a time."""

    def __init__(self, d):
        self.path = os.path.join(d, ".pull.lock")

    def __enter__(self):
        self.fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o644)
        _lock(self.fd, 120)
        return self

    def __exit__(self, *a):
        fcntl.flock(self.fd, fcntl.LOCK_UN)
        os.close(self.fd)


def fill_wear(host, root=None, timeout=30, now=None, jobs_log=None):
    """Join the null wear entries of HOST's rows with its jobs.log (R26's window),
    through wear.py run on the host; the answers go to wear-HOST.json.  A row's
    entries are final once its window has closed (t + 900 s) and aren't asked again."""
    from . import hostrun
    d = ledger_dir(root)
    rows = read(os.path.join(d, f"{host}.jsonl")).rows + read(os.path.join(d, f"{host}.lost.jsonl")).rows
    cpath = os.path.join(d, f"wear-{host}.json")
    cache = _read_json(cpath, {})
    ent = cache.setdefault("rows", {})
    q = []
    for r in rows:
        tags = [t for t, v in (r.get("wear") or {}).items() if v is None]
        if not tags or (ent.get(r["id"]) or {}).get("final"):
            continue
        for tg in tags:
            q.append({"id": r["id"], "tag": tg, "start": r.get("start") or r.get("t"), "t": r.get("t")})
    if not q:
        return {"asked": 0}
    with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "wear.py"), encoding="utf-8") as f:
        src = f.read()
    log = jobs_log or os.environ.get("T2_WEAR_LOG") or "/bkoscratch/wear/jobs.log"
    res = hostrun.ssh(host, script=src, args=["join"], data={"log": log, "queries": q,
                                                             "window": list(WEAR_WINDOW)}, timeout=timeout)
    if res.rc != 0:
        return {"asked": len(q), "error": res.err.decode("utf-8", "replace").strip()[-300:] or f"rc {res.rc}"}
    got = json.loads(res.out.decode("utf-8"))
    if got.get("error"):   # e.g. no jobs.log: nothing is recorded or made final
        return {"asked": len(q), "error": str(got["error"])[:300]}
    ans = got.get("results", {})
    tnow = time.time() if now is None else now
    for x in q:
        e = ent.setdefault(x["id"], {"wear": {}, "final": False})
        e["wear"][x["tag"]] = (ans.get(x["id"]) or {}).get(x["tag"])
        tt = parse_utc(x["t"]) or 0
        e["final"] = tnow > tt + WEAR_WINDOW[1] + 60
    cache["t"] = utc_now(now)
    _write_atomic(cpath, json.dumps(cache, indent=1, sort_keys=True) + "\n")
    return {"asked": len(q)}


def pull(hosts, root=None, remote_root=None, wear=True, timeout=30, now=None):
    """t2 pull: each host's ledger mirrored (in parallel), then the wear join.
    {host: pull record}; an unreachable host's record says ok: false."""
    out = {}

    def one(h):
        try:
            out[h] = pull_one(h, root, remote_root, timeout, now)
            if wear and out[h].get("ok"):
                out[h]["wear"] = fill_wear(h, root, timeout, now)
        except Exception as e:   # a pull never stops a plan: the mirror stays
            out[h] = {"ok": False, "error": f"{type(e).__name__}: {e}"}

    ths = [threading.Thread(target=one, args=(h,)) for h in hosts]
    for t in ths:
        t.start()
    for t in ths:
        t.join()
    return out


# --- self-tests (t2-host self-test, U1.2) -----------------------------------------------------

def synthetic_row(worker, n, host=None, cell=None, origin=None):
    """A complete, valid row with synthetic inputs (self-tests only)."""
    from . import keys
    cell = cell or f"gate:selftest-{worker}"
    inputs = {"harness": {f"testvm/test2/selftest/{worker}.sh": "sha256:" + sha256_hex(f"{worker}")},
              "soft": {"kernel": "ver:selftest"}}
    t = utc_now()
    return complete({
        "origin": origin or f"selftest:{os.getpid()}:{worker}:{n}", "family": "gate", "cell": cell,
        "kind": "run", "run": f"selftest-{os.getpid()}", "word": "t2-host self-test", "rule": "test1",
        "reasons": [f"worker {worker} row {n}"], "attempt": 1, "inputs": inputs, "key": keys.key(cell, inputs),
        "valid": True, "verdict": "PASS", "class": "pass", "needs": {"selftest": "PASS"},
        "counts": {"PASS": 1}, "start": t, "commit": None, "committed": False, "copy": "selftest",
        "meta": {"pad": "x" * (n % 97)},
    }, host=host)


def _worker(path, worker, n):
    led = Ledger(path)
    for i in range(n):
        led.append(synthetic_row(worker, i))


def _half_writer(path):
    """Takes the lock, writes half a row, says so, and waits to be killed."""
    fd = open_for_append(path)
    _lock(fd, LOCK_WAIT)
    line = encode(synthetic_row(99, 0, origin="selftest:killed"))
    _write_all(fd, line[:len(line) // 2])
    os.fsync(fd)
    sys.stdout.write("half\n")
    sys.stdout.flush()
    time.sleep(60)
    os._exit(0)


def _spawn(code):
    import subprocess
    lib = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return subprocess.Popen([sys.executable, "-B", "-c", f"import sys; sys.path.insert(0, {lib!r}); {code}"],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def selftest_concurrency(path, workers=8, rows=500):
    """WORKERS processes append ROWS rows each to a fresh ledger at PATH at once:
    every row there once, no bad line, no duplicate id or origin."""
    t0 = time.monotonic()
    procs = [_spawn(f"from t2lib import ledger; ledger._worker({path!r}, {w}, {rows})") for w in range(workers)]
    errs = []
    for p in procs:
        _, e = p.communicate()
        if p.returncode != 0:
            errs.append(e.decode("utf-8", "replace").strip()[-400:])
    rd = read(path)
    ids = [r["id"] for r in rd.rows]
    origins = [r["origin"] for r in rd.rows]
    ok = (not errs and len(rd.rows) == workers * rows and rd.bad == 0 and not rd.torn
          and len(set(ids)) == len(ids) and len(set(origins)) == len(origins))
    return {"ok": ok, "rows": len(rd.rows), "want": workers * rows, "bad": rd.bad, "torn": rd.torn,
            "dup_ids": len(ids) - len(set(ids)), "dup_origins": len(origins) - len(set(origins)),
            "errors": errs, "seconds": round(time.monotonic() - t0, 2)}


def selftest_kill(path):
    """A writer killed with SIGKILL after half a row, then a good append: the torn
    line ends at a newline, the reader counts it bad, the next row is whole."""
    import signal
    led = Ledger(path)
    for i in range(3):
        led.append(synthetic_row(7, i))
    p = _spawn(f"from t2lib import ledger; ledger._half_writer({path!r})")
    line = p.stdout.readline()
    if line.strip() != b"half":
        p.kill()   # our own child
        _, err = p.communicate()
        return {"ok": False, "error": "the half writer didn't write: " + err.decode("utf-8", "replace")[-300:]}
    p.send_signal(signal.SIGKILL)   # our own child, mid-row
    p.communicate()
    torn_before = read(path).torn
    after = synthetic_row(7, 3)
    led.append(after)
    rd = read(path)
    good = [r["id"] for r in rd.rows]
    ok = torn_before and not rd.torn and rd.bad == 1 and len(rd.rows) == 4 and good[-1] == after["id"]
    return {"ok": ok, "torn_before": torn_before, "rows": len(rd.rows), "bad": rd.bad, "torn_after": rd.torn,
            "last_is_new": bool(good) and good[-1] == after["id"], "killed_rc": p.returncode}


def selftest_origin(path):
    """The same origin twice: one row, and the second append gives the first's id."""
    a = synthetic_row(5, 0, origin="selftest:same")
    b = synthetic_row(5, 1, origin="selftest:same")
    (ia, wa), = append(path, [a])
    (ib, wb), = append(path, [b])
    rd = read(path)
    ok = wa and not wb and ia == ib and sum(r["origin"] == "selftest:same" for r in rd.rows) == 1
    return {"ok": ok, "first": ia, "second": ib, "appended": [wa, wb]}
