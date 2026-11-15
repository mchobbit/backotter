"""t2 batch's output, the impure part (docs/design/batch.md §5, §7; piece B5): what each entry's
cells were (its run's plan, the ledger, the tool's own record), summary.tsv, the capped fetch of
non-PASS cells' logs, report.txt and ANALYSE.md.  The pure rules are t2lib/batchreport.py's.

    t2 batch report BATCH [--prev BATCH] [--json]   the report (summary.tsv built first if missing)
    t2 batch summary BATCH [--no-fetch]             summary.tsv again from the ledger (and the logs)
    t2 batch log BATCH [ENTRY:]CELL [--tail N] [--head N] [--file NAME] [--list]

The ledger is the Mac's merged view (MAC_ROOT/ledger); on the real root it is pulled from the
batch's hosts first (T2_BATCH_PULL=1 asks for that on a test root, 0 never).

The log fetch (§5.3): per cell, its row's `logs` (HOST:PATH), at most FETCH_BYTES and
FETCH_FILES, a file bigger than its share kept as head and tail, and a FETCH.txt naming what was
left behind and where; into BATCH/logs/ENTRY/CELL/.  A host that doesn't answer leaves FETCH.txt
saying so (the summary's log column then names HOST:PATH).
"""

import io
import json
import os
import re
import shutil
import sys
import tarfile
import time

from . import batchreport as br

FETCH_BYTES = 8 << 20
FETCH_FILES = 50
FETCH_TIMEOUT = 300
PROCEDURE_START = "## Analysing a batch"
SUMMARY = "summary.tsv"

T2_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BATCH_MD = os.path.join(T2_DIR, "BATCH.md")


def read_json(path, default=None):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def read_text(path, default=None):
    try:
        with open(path, encoding="utf-8") as f:
            return f.read()
    except (OSError, UnicodeDecodeError):
        return default


def write_text(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.tmp.{os.getpid()}"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, path)


def cell_slug(cell):
    return re.sub(r"[^A-Za-z0-9+._-]", "-", (cell or "-").replace(":", "-"))[:120] or "-"


# --- what an entry's cells were ------------------------------------------------------------------

STAMP_RE = re.compile(r"t2[gshvibw]-(\d{8}T\d{4}-[0-9a-f]{4})")


def run_match(kind, run):
    """The regex (fullmatch) a ledger row's `run` takes for an entry of KIND whose run is RUN, or
    None.  A t2 run: itself and its attempts (RUN-rN), an img run's holt panel (t2h-S-img) too; a
    composite (t2b-S, t2w-S): every child of its stamp; ht1 and stic1: the RUN / mark; bs2: the
    RUN and its tier 0 sweep RUN-t0."""
    if not run:
        return None
    q = re.escape(run)
    if kind == "t2":
        m = STAMP_RE.match(run)
        if m and run[:3] in ("t2b", "t2w"):
            return rf"t2[gshvi]-{re.escape(m.group(1))}(-img)?(-r\d+)?"
        if m and run[:3] == "t2i":
            return rf"({q}|t2h-{re.escape(m.group(1))}-img)(-r\d+)?"
        return rf"{q}(-r\d+)?"
    if kind == "bs2":
        return rf"{q}(-t0)?"
    return q


def plan_cells(run, root=None):
    """[{cell, status, reasons}] of RUN's plan.json (a composite's: each child's of its stamp)."""
    from . import run as runmod
    dirs = [runmod.run_dir(run, root)]
    m = STAMP_RE.match(run or "")
    if m and run[:3] in ("t2b", "t2w"):
        try:
            names = sorted(os.listdir(runmod.runs_dir(root)))
        except OSError:
            names = []
        dirs = [os.path.join(runmod.runs_dir(root), n) for n in names
                if re.fullmatch(rf"t2[gshvi]-{re.escape(m.group(1))}(-img)?", n)]
    out = []
    for d in dirs:
        pj = read_json(os.path.join(d, "plan.json"), {}) or {}
        for c in pj.get("cells") or []:
            if isinstance(c, dict) and c.get("cell"):
                out.append({"cell": c["cell"], "status": c.get("status"), "reasons": list(c.get("reasons") or [])})
    return out


def run_stops(run, root=None):
    """The run-wide stop rules RUN's report.txt names (bko.report_stops), or []: a due cell
    they left unstarted is NOT-RUN, not LOST."""
    from . import run as runmod
    from . import bko
    return bko.report_stops(os.path.join(runmod.run_dir(run, root), "report.txt"))


def ht1_cells(e):
    """{gv: cell} for an ht1 entry's todo lines (GUEST[+VARIANT][=D][%M] -> holt:GUEST[+VARIANT];
    the done rows' gv is GUEST[-VARIANT])."""
    out = {}
    for line in e.args[1:]:
        gvar = re.split(r"[=%]", line)[0]
        out[gvar.replace("+", "-")] = f"holt:{gvar}"
    return out


def ht1_tool(e, edir, run):
    """ht1's own record: the done rows (rc 0 PASS) and the lines left in the todo (LOST)."""
    cells = ht1_cells(e)
    out = []
    done = read_text(os.path.join(edir, "ht1-done.tsv"), "") or ""
    for ln in done.splitlines():
        f = ln.split("\t")
        if len(f) < 9 or f[0] == "end_utc":
            continue
        host, gv, rc, run_s = f[1], f[2], f[3], f[8]
        cell = cells.get(gv, f"holt:{gv}")
        out.append({"cell": cell, "verdict": "PASS" if rc == "0" else "FAIL", "host": host,
                    "wall_s": run_s if run_s.isdigit() else "-",
                    "reason": "" if rc == "0" else f"ht1 job exit {rc} (no ledger row: its done row only)",
                    "log": f"{host}:/bkoscratch/holttest1d/{run}-{gv}.log"})
    for ln in (read_text(os.path.join(edir, "ht1-left.txt"), "") or "").splitlines():
        ln = ln.strip()
        if ln:
            gvar = re.split(r"[=%]", ln)[0]
            out.append({"cell": f"holt:{gvar}", "verdict": "LOST", "class": "lost", "host": "-",
                        "reason": "left in the todo: no host of the run took it"})
    return out


def stic1_tool(edir):
    """drive.py's results.tsv (guest host exit pass fail limit seconds logdir)."""
    out = []
    for ln in (read_text(os.path.join(edir, "stic1", "results.tsv"), "") or "").splitlines()[1:]:
        f = ln.split("\t")
        if len(f) < 8:
            continue
        g, host, ex, _p, fail, _lim, secs, logdir = f[:8]
        ok = ex == "0" and fail == "0"
        lost = ex.upper() == "LOST"
        out.append({"cell": f"stic:{g}", "verdict": "PASS" if ok else ("LOST" if lost else "FAIL"), "host": host,
                    "wall_s": secs, "log": f"{host}:{logdir}" if logdir not in ("", "-") else None,
                    "reason": "" if ok else (f"drive.py: {ex}" if lost else f"check.sh exit {ex}, {fail} FAIL line(s)")})
    return out


def entry_facts(bdir, root=None):
    """[batchreport entry dict] for every entry of the batch in BDIR (its state.json and files)."""
    from . import batchfile as bf
    doc = read_json(os.path.join(bdir, "batch.json"), {}) or {}
    st = (read_json(os.path.join(bdir, "state.json"), {}) or {}).get("entries") or {}
    out = []
    for e in bf.Batch.from_json(doc["batch"]).entries:
        s = st.get(e.id) or {}
        edir = os.path.join(bdir, "entries", e.id)
        run = (read_text(os.path.join(edir, "run")) or "").strip() or s.get("run")
        wall = None
        if isinstance(s.get("started"), (int, float)) and isinstance(s.get("ended"), (int, float)):
            wall = s["ended"] - s["started"]
        x = {"id": e.id, "kind": e.kind, "status": s.get("status") or "pending", "exit": s.get("exit"),
             "run": run, "match": run_match(e.kind, run), "plan": [], "tool": [], "wall_s": wall,
             "attempt": s.get("attempts") or 1, "note": s.get("note"),
             "log": os.path.relpath(os.path.join(edir, "entry.log"), bdir)}
        if e.kind == "t2" and run:
            x["plan"] = plan_cells(run, root)
            x["stops"] = run_stops(run, root)
        elif e.kind == "ht1":
            x["plan"] = [{"cell": c, "status": "due"} for c in ht1_cells(e).values()] if run else []
            x["tool"] = ht1_tool(e, edir, run)
        elif e.kind == "stic1":
            x["plan"] = [{"cell": f"stic:{g}", "status": "due"} for g in e.args[1:] if not g.startswith("-")] \
                if run else []
            x["tool"] = stic1_tool(edir)
        out.append(x)
    return out


# --- the ledger -------------------------------------------------------------------------------------

def want_pull(root=None):
    from . import batch as B
    v = os.environ.get("T2_BATCH_PULL")
    if v in ("0", "1"):
        return v == "1"
    return B.real_root(root)


def ledger_rows(hosts=(), root=None, pull=None, log=None):
    """The Mac's merged ledger rows, pulled from HOSTS first when PULL (default want_pull())."""
    from . import ledger
    if (want_pull(root) if pull is None else pull) and hosts:
        try:
            got = ledger.pull(list(hosts), root, wear=False)
            bad = [h for h, r in got.items() if not (r or {}).get("ok")]
            if bad and log:
                log(f"ledger pull: no answer from {', '.join(bad)}: their mirrors as last pulled")
        except Exception as ex:   # noqa: BLE001 - the mirrors as they are
            if log:
                log(f"ledger pull failed ({type(ex).__name__}: {ex}): the mirrors as last pulled")
    try:
        return ledger.view(root, fill_wear=False).rows
    except Exception:   # noqa: BLE001 - no ledger: the tools' own records only
        return []


# --- the log fetch (§5.3) ------------------------------------------------------------------------------

# Runs on a host (python3 -I -B - PATH MAX_BYTES MAX_FILES): a tar of PATH's records on stdout,
# capped, with FETCH.txt.  Reads only.
FETCH_SCRIPT = r'''
import io, os, sys, tarfile, time
path, maxb, maxf = os.path.expanduser(sys.argv[1]), int(sys.argv[2]), int(sys.argv[3])
PRI = ("verdict", "judge", "report", "fails", "run.out", ".log", ".out", ".err", ".txt", ".json", ".tsv")
files = []
if os.path.isfile(path):
    files = [(os.path.basename(path), path)]
elif os.path.isdir(path):
    for d, ds, fs in os.walk(path):
        ds.sort()
        for n in sorted(fs):
            p = os.path.join(d, n)
            if os.path.isfile(p) and not os.path.islink(p):
                files.append((os.path.relpath(p, path), p))
def pri(x):
    n = x[0].lower()
    i = next((k for k, s in enumerate(PRI) if s in n), len(PRI))
    try:
        sz = os.path.getsize(x[1])
    except OSError:
        sz = 0
    return (i, sz, n)
files.sort(key=pri)
out = io.BytesIO()
t = tarfile.open(fileobj=out, mode="w")
left, kept, cut_, budget = [], [], [], maxb
def add(name, data):
    ti = tarfile.TarInfo(name)
    ti.size, ti.mtime, ti.mode = len(data), int(time.time()), 0o644
    t.addfile(ti, io.BytesIO(data))
for i, (rel, p) in enumerate(files):
    if len(kept) >= maxf - 1 or budget <= 0:
        left.append(rel)
        continue
    try:
        sz = os.path.getsize(p)
        share = max(4096, budget // max(1, min(maxf - 1 - len(kept), len(files) - i)))
        with open(p, "rb") as f:
            if sz <= share:
                data = f.read()
            else:
                h = f.read(share // 2)
                f.seek(max(0, sz - share // 2))
                data = h + b"\n[... t2 batch: %d bytes cut here; the whole file is on the host ...]\n" % (sz - share) + f.read()
                cut_.append("%s (%d bytes, head and tail kept)" % (rel, sz))
    except OSError as e:
        left.append("%s (%s)" % (rel, e))
        continue
    add("records/" + rel, data)
    kept.append(rel)
    budget -= len(data)
host = os.uname()[1]
note = ["t2 batch log fetch from %s:%s" % (host, path),
        "kept %d file(s), %d bytes (caps: %d files, %d bytes)" % (len(kept), maxb - budget, maxf, maxb)]
if not files:
    note.append("nothing there: %s %s" % (path, "is empty" if os.path.isdir(path) else "doesn't exist"))
note += ["cut: " + x for x in cut_] + ["left behind: " + x for x in left[:20]]
if len(left) > 20:
    note.append("left behind: and %d more" % (len(left) - 20))
if left or cut_:
    note.append("the rest: ssh to the host, %s" % path)
add("FETCH.txt", ("\n".join(note) + "\n").encode())
t.close()
sys.stdout.buffer.write(out.getvalue())
'''


def safe_extract(data, dest):
    """The tar DATA's regular files under DEST, nothing outside it: [names]."""
    names = []
    with tarfile.open(fileobj=io.BytesIO(data), mode="r") as t:
        for m in t.getmembers():
            n = os.path.normpath(m.name)
            if not m.isfile() or os.path.isabs(n) or n.startswith("..") or "/../" in f"/{n}/":
                continue
            p = os.path.join(dest, n)
            os.makedirs(os.path.dirname(p), exist_ok=True)
            f = t.extractfile(m)
            with open(p, "wb") as o:
                shutil.copyfileobj(f, o)
            names.append(n)
    return names


def split_logs(logs):
    """(host, path) of a row's `logs` (HOST:PATH), or (None, None)."""
    if not logs or not isinstance(logs, str) or ":" not in logs:
        return None, None
    h, _, p = logs.partition(":")
    if not h or not p or "/" in h:
        return None, None
    return h, p


def fetch_one(host, path, dest, maxb=FETCH_BYTES, maxf=FETCH_FILES, timeout=FETCH_TIMEOUT):
    """HOST:PATH's records into DEST (capped): True, or False with FETCH.txt saying why."""
    from . import batch as B
    from . import hostrun
    os.makedirs(dest, exist_ok=True)
    r = B.ssh_retry(lambda: hostrun.ssh(host, script=FETCH_SCRIPT, args=[path, maxb, maxf], timeout=timeout),
                    tries=int(os.environ.get("T2_BATCH_FETCH_TRIES") or 3), backoff=5.0)
    if r is not None and r.rc == 0 and r.out:
        try:
            safe_extract(r.out, dest)
            return True
        except (tarfile.TarError, OSError) as ex:
            why = f"the records didn't unpack: {type(ex).__name__}: {ex}"
    else:
        why = f"no answer (ssh exit {getattr(r, 'rc', '?')}: {(getattr(r, 'errtext', '') or '')[-200:].strip()})" \
            if r is None or r.rc in B.NO_ANSWER else f"exit {r.rc}: {r.errtext[-200:].strip()}"
    write_text(os.path.join(dest, "FETCH.txt"), f"t2 batch log fetch from {host}:{path} failed: {why}\n"
                                                f"the records: ssh {host}, {path}\n")
    return False


def fetch_logs(rows, bdir, mode="non-pass", log=None, fetcher=fetch_one):
    """The logs of ROWS' cells (MODE: non-pass, all, none) into BDIR/logs/ENTRY/CELL; each row's
    `log` set to the fetched dir (relative to BDIR), or to HOST:PATH when nothing was fetched."""
    n = 0
    for r in rows:
        logs = r.pop("_logs", None)
        if r.get("cell") == "-" or r.get("log") not in (None, "-", ""):
            continue
        host, path = split_logs(logs)
        if host is None:
            continue
        want = mode == "all" or (mode == "non-pass" and not br.passing(r))
        if not want:
            r["log"] = "-" if br.passing(r) else logs
            continue
        rel = os.path.join("logs", r["entry"], cell_slug(r["cell"]))
        dest = os.path.join(bdir, rel)
        if os.path.isdir(dest) and os.listdir(dest) == ["FETCH.txt"] and \
                " failed: " in (read_text(os.path.join(dest, "FETCH.txt")) or ""):
            shutil.rmtree(dest, ignore_errors=True)   # a fetch that failed: fetched again now
        if os.path.isdir(dest) and os.listdir(dest):
            r["log"] = rel                       # fetched before (a summary built again)
            continue
        ok = fetcher(host, path, dest)
        n += 1
        r["log"] = rel if ok or os.path.exists(os.path.join(dest, "FETCH.txt")) else logs
        if log:
            log(f"log {'fetched' if ok else 'NOT fetched'}: {r['entry']} {r['cell']} from {host}:{path}")
    for r in rows:
        r.pop("_logs", None)
    return n


# --- the baseline ---------------------------------------------------------------------------------

def prev_batch(bdir, root=None, override=None):
    """The baseline batch id: OVERRIDE, else batch.json's prev (--prev, set prev), else the newest
    earlier batch of the same file with a summary.tsv; None: no baseline."""
    from . import batch as B
    doc = read_json(os.path.join(bdir, "batch.json"), {}) or {}
    if override:
        return override
    if doc.get("prev"):
        return doc["prev"]
    me = os.path.basename(bdir)
    d = B.batches_dir(root)
    best = None
    for n in B.list_batches(root):
        if n == me:
            break
        bj = read_json(os.path.join(d, n, "batch.json"), {}) or {}
        if bj.get("file") == doc.get("file") and os.path.exists(os.path.join(d, n, SUMMARY)):
            best = n
    return best


def prev_rows(prev, root=None):
    from . import batch as B
    if not prev:
        return None
    text = read_text(os.path.join(B.batch_dir(prev, root), SUMMARY))
    return br.parse_tsv(text) if text is not None else None


# --- building the summary and the report -----------------------------------------------------------

def meta_of(bdir):
    from . import batchfile as bf
    doc = read_json(os.path.join(bdir, "batch.json"), {}) or {}
    st = read_json(os.path.join(bdir, "state.json"), {}) or {}
    b = bf.Batch.from_json(doc["batch"])
    t0, t1 = doc.get("created"), st.get("ended")
    iso = (lambda t: time.strftime("%Y-%m-%dT%H:%MZ", time.gmtime(t)) if isinstance(t, (int, float)) else None)
    stops = [ln.split(" ", 1)[1] for ln in (read_text(os.path.join(bdir, "batch.log"), "") or "").splitlines()
             if "safety valve" in ln or " stopped by " in ln]
    return {"batch": os.path.basename(bdir), "file": doc.get("file"), "hosts": doc.get("hosts") or [],
            "started": iso(t0), "ended": iso(t1), "exit": st.get("exit"),
            "wall_s": (t1 - t0) if isinstance(t0, (int, float)) and isinstance(t1, (int, float)) else None,
            "manual": [m.get("text") for m in b.manual], "stops": stops}


def entries_of(facts):
    return [{"id": f["id"], "status": f["status"], "exit": f.get("exit"), "wall_s": f.get("wall_s"),
             "run": f.get("run"), "note": f.get("note")} for f in facts]


def build(bdir, root=None, fetch=True, prev=None, log=None, pull=None, fetcher=fetch_one, rows_in=None):
    """summary.tsv (and the fetched logs, the prev file): (rows, report data)."""
    doc = read_json(os.path.join(bdir, "batch.json"), {}) or {}
    facts = entry_facts(bdir, root)
    led = rows_in if rows_in is not None else ledger_rows(doc.get("hosts") or [], root, pull, log)
    rows = br.summary_rows(facts, led)
    from . import batchfile as bf
    mode = bf.Batch.from_json(doc["batch"]).settings.get("log-fetch") or "non-pass"
    if fetch and mode != "none":
        fetch_logs(rows, bdir, mode, log, fetcher)
    else:
        for r in rows:
            logs = r.pop("_logs", None)
            if r.get("log") in (None, "-") and not br.passing(r) and logs:
                rel = os.path.join("logs", r["entry"], cell_slug(r["cell"]))
                r["log"] = rel if os.path.isdir(os.path.join(bdir, rel)) else logs
    pid = prev_batch(bdir, root, prev)
    base = prev_rows(pid, root)
    br.diff(rows, base or [])
    write_text(os.path.join(bdir, SUMMARY), br.to_tsv(rows))
    write_text(os.path.join(bdir, "prev"), (pid or "-") + "\n")
    rep = br.report(meta_of(bdir), entries_of(facts), rows, pid if base is not None else None,
                    br.fixed_or_gone(rows, base or []))
    return rows, rep


def procedure_text(path=BATCH_MD):
    """BATCH.md's "Analysing a batch" section (to the next ## heading), or a short stand-in."""
    text = read_text(path) or ""
    i = text.find(PROCEDURE_START)
    if i < 0:
        return ("## Analysing a batch\n\n1. `t2 batch report <BATCH>`.\n2. One log per new `fail` group: "
                "`t2 batch log <BATCH> ENTRY:CELL --tail 200`, at most 10.\n3. `harness` and `lost` by reason "
                "alone.\n4. `known` is the baseline's: name it, don't open it.\n5. One line per group.\n")
    j = text.find("\n## ", i + len(PROCEDURE_START))
    return text[i:j if j > 0 else len(text)]


def finish(bdir, root=None, log=None, fetcher=fetch_one):
    """At the batch's end: summary.tsv, the logs, report.txt and ANALYSE.md.  The report text."""
    rows, rep = build(bdir, root, True, None, log, None, fetcher)
    text = br.render(rep)
    write_text(os.path.join(bdir, "report.txt"), text)
    write_text(os.path.join(bdir, "ANALYSE.md"), br.render_analyse(rep, procedure_text(), os.path.basename(bdir)))
    return text


def entry_summary(bdir, eid, root=None, hosts=(), pull=None, status=None):
    """The summary rows of one entry now (for its retry shape and the valve), no fetch; STATUS:
    how it is ending (state.json doesn't say yet)."""
    facts = [f for f in entry_facts(bdir, root) if f["id"] == eid]
    for f in facts:
        if status:
            f["status"] = status
    rows = br.summary_rows(facts, ledger_rows(hosts, root, pull))
    for r in rows:
        r.pop("_logs", None)
    return rows


# --- the CLI ------------------------------------------------------------------------------------------

def _bdir(bid, root):
    from . import batch as B
    d = B.batch_dir(bid, root)
    if not os.path.isfile(os.path.join(d, "batch.json")):
        B.err(f"no batch {bid} in {B.batches_dir(root)}")
        return None
    return d


def cmd_report(argv, root=None):
    from . import batch as B
    opts, rest = B.take_opts(argv, {"--prev": True, "--json": False})
    if len(rest) != 1:
        raise B.Usage("t2 batch report BATCH [--prev BATCH] [--json]")
    d = _bdir(rest[0], root)
    if d is None:
        return B.EXIT_REFUSED
    text = read_text(os.path.join(d, SUMMARY))
    if text is None:
        rows, _ = build(d, root, fetch=False)
    else:
        rows = br.parse_tsv(text)
    pid = opts.get("--prev")
    if pid:                      # another baseline: this print only; summary.tsv keeps its own
        base = prev_rows(pid, root)
        if base is None:
            B.err(f"--prev {pid}: no such batch with a summary.tsv")
            return B.EXIT_REFUSED
        br.diff(rows, base)
    else:
        pid = (read_text(os.path.join(d, "prev"), "") or "").strip()
        pid = None if pid in ("", "-") else pid
        base = prev_rows(pid, root)
    rep = br.report(meta_of(d), entries_of(entry_facts(d, root)), rows, pid if base is not None else None,
                    br.fixed_or_gone(rows, base or []))
    if opts.get("--json"):
        B.say(br.to_json(rep))
    else:
        sys.stdout.write(br.render(rep))
        sys.stdout.flush()
    return B.EXIT_OK


def cmd_summary(argv, root=None):
    from . import batch as B
    opts, rest = B.take_opts(argv, {"--no-fetch": False, "--prev": True})
    if len(rest) != 1:
        raise B.Usage("t2 batch summary BATCH [--no-fetch] [--prev BATCH]")
    d = _bdir(rest[0], root)
    if d is None:
        return B.EXIT_REFUSED
    rows, rep = build(d, root, fetch=not opts.get("--no-fetch"), prev=opts.get("--prev"), log=B.say)
    if os.path.exists(os.path.join(d, "report.txt")):
        write_text(os.path.join(d, "report.txt"), br.render(rep))
        write_text(os.path.join(d, "ANALYSE.md"), br.render_analyse(rep, procedure_text(), os.path.basename(d)))
    B.say(f"{os.path.join(d, SUMMARY)}: {len(rows)} rows; " + ", ".join(
        f"{k} {v}" for k, v in sorted(br.counts(rows, "verdict").items())))
    return B.EXIT_OK


def find_row(rows, want):
    """The row WANT ([ENTRY:]CELL, a cell's slug, or an entry id for a cell-less row) names: the
    first non-PASS one when several do."""
    ent, cell = None, want
    if ":" in want:
        a, b = want.split(":", 1)
        if any(r["entry"] == a for r in rows):
            ent, cell = a, b
    hits = [r for r in rows if (ent is None or r["entry"] == ent)
            and (r["cell"] == cell or cell_slug(r["cell"]) == cell or (r["cell"] == "-" and r["entry"] == cell))]
    hits.sort(key=lambda r: br.passing(r))
    return hits[0] if hits else None


LOG_PRI = ("run.out", "verdict", "judge", "fails", ".log", ".out", ".err", "report")
FETCH_LINES = 20          # `t2 batch log`: FETCH.txt's lines printed at most
LINE_MAX = 2000           # characters of one printed line (a JSON verdict may be one line of MBs)
LOG_BYTES = 200_000       # bytes of the printed lines at most (400 lines of LINE_MAX would be 800 kB)


def _bytes_cap(lines, head):
    """LINES within LOG_BYTES, kept from the head (HEAD) or the tail: (lines, dropped)."""
    out, n = [], 0
    for x in (lines if head else reversed(lines)):
        n += len(x) + 1
        if n > LOG_BYTES and out:
            break
        out.append(x)
    return (out if head else out[::-1]), len(lines) - len(out)


def _files(d):
    out = []
    for dd, ds, fs in os.walk(d):
        ds.sort()
        for n in sorted(fs):
            p = os.path.join(dd, n)
            out.append((os.path.relpath(p, d), os.path.getsize(p)))
    return out


def primary(files):
    def k(x):
        n = x[0].lower()
        return (n == "fetch.txt", next((i for i, s in enumerate(LOG_PRI) if s in n), len(LOG_PRI)), -x[1], n)
    fs = sorted(files, key=k)
    return fs[0][0] if fs else None


def cmd_log(argv, root=None):
    """t2 batch log BATCH [ENTRY:]CELL [--tail N] [--head N] [--file NAME] [--list]: the cell's
    fetched records (its main log by default, its last N lines), never more than 400 lines."""
    from . import batch as B
    opts, rest = B.take_opts(argv, {"--tail": True, "--head": True, "--file": True, "--list": False})
    if len(rest) != 2:
        raise B.Usage("t2 batch log BATCH [ENTRY:]CELL [--tail N] [--head N] [--file NAME] [--list]")
    d = _bdir(rest[0], root)
    if d is None:
        return B.EXIT_REFUSED
    text = read_text(os.path.join(d, SUMMARY))
    if text is None:
        B.err(f"{rest[0]} has no summary.tsv yet: `t2 batch summary {rest[0]}`")
        return B.EXIT_REFUSED
    r = find_row(br.parse_tsv(text), rest[1])
    if r is None:
        B.err(f"no cell {rest[1]} in {rest[0]}'s summary.tsv")
        return B.EXIT_REFUSED
    try:
        n_tail = int(opts["--tail"]) if opts.get("--tail") else None
        n_head = int(opts["--head"]) if opts.get("--head") else None
    except ValueError:
        raise B.Usage("--tail and --head take a number") from None
    if n_tail is None and n_head is None:
        n_tail = 200
    B.say(f"# {r['entry']} {r['cell']}: {r['verdict']} ({r['class']}, {r['since']}) on {r['host']}, row {r['row']}"
          + (f": {r['reason']}" if r.get("reason") else ""))
    log = r.get("log") or "-"
    p = os.path.join(d, log) if log != "-" and not split_logs(log)[0] else None
    if p is None or not os.path.exists(p):
        B.say(f"# no fetched log ({'its records: ' + log if log != '-' else 'none recorded'}; "
              f"`t2 batch summary {rest[0]}` fetches again)")
        return B.EXIT_OK
    if os.path.isdir(p):
        files = _files(p)
        if opts.get("--list"):
            for n, sz in files:
                B.say(f"{sz:>10}  {n}")
            return B.EXIT_OK
        fetch_note = read_text(os.path.join(p, "FETCH.txt"))
        if fetch_note:
            fl = fetch_note.strip().splitlines()
            for x in fl[:FETCH_LINES]:
                B.say("# " + x[:LINE_MAX])
            if len(fl) > FETCH_LINES:
                B.say(f"# ... and {len(fl) - FETCH_LINES} more lines of FETCH.txt (--file FETCH.txt)")
        name = opts.get("--file") or primary(files)
        if name is None:
            B.say("# the fetched dir is empty")
            return B.EXIT_OK
        if os.path.normpath(name).startswith(".."):
            raise B.Usage("--file: a name inside the cell's fetched dir")
        if not os.path.exists(os.path.join(p, name)) and os.path.exists(os.path.join(p, "records", name)):
            name = os.path.join("records", name)
        B.say(f"# {name} ({', '.join(n for n, _ in files[:8])}{' ...' if len(files) > 8 else ''}; --list, --file NAME)")
        p = os.path.join(p, name)
    with open(p, "rb") as f:
        lines = f.read().decode("utf-8", "replace").splitlines()
    cap = 400
    cutl = (lambda x: x if len(x) <= LINE_MAX else x[:LINE_MAX] + f" [... {len(x) - LINE_MAX} more characters]")  # noqa: E731
    if n_head is not None:
        sel = [cutl(x) for x in lines[:min(n_head, cap)]]
        sel, _ = _bytes_cap(sel, head=True)
        more = len(lines) - len(sel)
        sys.stdout.write("\n".join(sel) + "\n" + (f"# ... {more} more lines\n" if more > 0 else ""))
    else:
        sel = [cutl(x) for x in lines[-min(n_tail, cap):]] if n_tail else []
        sel, _ = _bytes_cap(sel, head=False)
        more = len(lines) - len(sel)
        sys.stdout.write((f"# ... {more} lines above\n" if more > 0 else "") + "\n".join(sel) + "\n")
    sys.stdout.flush()
    return B.EXIT_OK
