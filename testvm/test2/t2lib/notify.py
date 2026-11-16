"""A finished run's phone notification: pushover.sh on the Mac (testvm/test2/README.md "Notification").

    claim()                     t2's main, first: the token of the outermost t2 (it sets T2_NOTIFIED_BY,
                                which its children inherit), or None in a nested one
    begun(run, word)            the run's work began: RunState (a family's run or resume), a composite's
                                children step (bko.py), the batch driver (batch.py); the first one names it
    outcome(run, detail)        its counts, for the message ("3 PASS, 1 FAIL")
    done(token, code, exc)      t2's main, last: one pushover.sh call when work began, whatever the end

Nothing here changes t2's exit code or output: a client that is missing, fails or hangs (TIMEOUT_S)
costs one line on stderr.  Off when T2_NOTIFY=0 or T2_PUSHOVER=none, and on a test MAC_ROOT unless
T2_NOTIFY=1 and T2_PUSHOVER names the client (a test's fake: a test never reaches the real one).
"""

import os
import shutil
import subprocess
import sys
import threading
import time

from . import is_default

MESSAGE = "Command-line test is complete. Send logs to Claude."
TITLE = "BackOtter"
MARK = "T2_NOTIFIED_BY"
CLIENT = "pushover.sh"
FALLBACK = ("/usr/local/bin/pushover.sh", "/opt/local/bin/pushover.sh")
TIMEOUT_S = 20
LIMIT = 900
OUTCOME = {0: "PASS", 1: "FAIL", 2: "REFUSED", 3: "ERROR", 130: "INTERRUPTED"}

_MU = threading.Lock()
_state = {}


def _reset():
    _state.clear()
    _state.update(run=None, word=None, t0=None, details={})


_reset()


def claim():
    """The outermost t2's token (its pid, now in T2_NOTIFIED_BY), or None: an outer t2 notifies."""
    if os.environ.get(MARK):
        return None
    _reset()
    tok = str(os.getpid())
    os.environ[MARK] = tok
    return tok


def begun(run, word):
    """RUN of WORD began its work (host work, or a batch's entries).  The first call names the
    notification: a composite's own id before its children's."""
    with _MU:
        if _state["run"] is None:
            _state.update(run=str(run), word=str(word), t0=time.time())


def outcome(run, detail):
    """RUN's counts for the message (a batch's statuses, a run's verdicts)."""
    with _MU:
        _state["details"][str(run)] = str(detail)


def fmt_dur(s):
    s = max(0, int(s))
    if s < 60:
        return f"{s}s"
    if s < 3600:
        return f"{s // 60}m {s % 60:02d}s"
    return f"{s // 3600}h {s % 3600 // 60:02d}m"


def message(run, word, code, exc=None, detail=None, secs=0):
    """MESSAGE, then one line: WORD run RUN: OUTCOME[: DETAIL] (DURATION).  At most LIMIT characters."""
    if exc is not None:
        what = f"ERROR ({type(exc).__name__}" + (f": {exc}" if str(exc) else "") + ")"
        what = what if len(what) <= 200 else what[:196] + "...)"
    else:
        what = OUTCOME.get(code, f"exit {code}")
    if detail:
        what += f": {detail}"
    text = f"{MESSAGE}\n{word} run {run}: {what} ({fmt_dur(secs)})"
    return text if len(text) <= LIMIT else text[:LIMIT - 3] + "..."


def client():
    """(path, None) of pushover.sh: T2_PUSHOVER, else PATH, /usr/local/bin, /opt/local/bin; or (None, why)."""
    want = os.environ.get("T2_PUSHOVER")
    if want:
        if os.path.isfile(want) and os.access(want, os.X_OK):
            return want, None
        return None, f"T2_PUSHOVER={want} is not an executable file"
    for p in [shutil.which(CLIENT)] + list(FALLBACK):
        if p and os.path.isfile(p) and os.access(p, os.X_OK):
            return p, None
    return None, f"no {CLIENT} (PATH, {', '.join(os.path.dirname(p) for p in FALLBACK)})"


def wanted():
    """True unless T2_NOTIFY=0 or T2_PUSHOVER=none; on a test MAC_ROOT only with T2_NOTIFY=1 and
    T2_PUSHOVER naming the client."""
    v = os.environ.get("T2_NOTIFY", "")
    pv = os.environ.get("T2_PUSHOVER", "")
    if v == "0" or pv.lower() == "none":
        return False
    if not is_default("mac"):
        return v == "1" and bool(pv)
    return True


def done(token, code=None, exc=None):
    """The outermost t2's last step: one pushover.sh call when a run's work began."""
    if token is None:
        return
    try:
        if os.environ.get(MARK) == token:
            del os.environ[MARK]
        with _MU:
            run, word, t0 = _state["run"], _state["word"], _state["t0"]
            detail = _state["details"].get(run) if run else None
        if run is None or not wanted():
            return
        path, why = client()
        if path is None:
            print(f"t2: no notification: {why}", file=sys.stderr, flush=True)
            return
        text = message(run, word, code, exc, detail, time.time() - (t0 or time.time()))
        r = subprocess.run([path, "-T", TITLE, "-m", text], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, timeout=TIMEOUT_S)
        if r.returncode != 0:
            print(f"t2: no notification: {CLIENT} exited {r.returncode}", file=sys.stderr, flush=True)
    except subprocess.TimeoutExpired:
        print(f"t2: no notification: {CLIENT} took over {TIMEOUT_S} s", file=sys.stderr, flush=True)
    except (Exception, KeyboardInterrupt) as e:   # noqa: BLE001 - a notification never changes t2's ending
        print(f"t2: no notification: {type(e).__name__}: {e}", file=sys.stderr, flush=True)
    finally:
        _reset()
