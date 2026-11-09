"""The host probe, from the Mac (contract 1.4): facts_host.py runs on each host
from stdin (ssh m@HOST.local python3 -I -B - ARGS < facts_host.py), read only,
and the answers are kept in MAC_ROOT/facts/HOST.json.

    probe(host, want)         one host's facts (a dict; on failure {"host", "ok": False, "error"})
    probe_all(hosts, want)    {host: facts}, the hosts at once
    load(host)                the last facts kept for HOST, or None
    want_for(specs, sets)     the --want that keys.inputs needs for these specs

The planner never makes image sums: a guest or rescuer whose sum isn't cached
yet keys as img:unknown ("guest identity unknown: treated as changed"), and a
run's preflight calls t2-host imgsum for it.
"""

import json
import os
import threading
import time

from . import mac_root, utc_now

FACTS_SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "facts_host.py")


def facts_dir(root=None):
    return os.path.join(root or mac_root(), "facts")


def probe(host, want=None, timeout=20, save=True, root=None, host_root=None, copy_root=None):
    """HOST's facts.  HOST_ROOT and COPY_ROOT (default: T2_HOST_ROOT and
    T2_COPY_ROOT here, if set) are passed to the probe in the hosts' terms."""
    from . import hostrun
    args = []
    if want:
        args += ["--want", json.dumps(want, sort_keys=True, separators=(",", ":"))]
    hr = host_root or os.environ.get("T2_HOST_ROOT")
    cr = copy_root or os.environ.get("T2_COPY_ROOT")
    if hr:
        args += ["--host-root", hostrun.host_path(hr)]
    if cr:
        args += ["--copy-root", hostrun.host_path(cr)]
    t0 = time.monotonic()
    r = hostrun.ssh(host, script=FACTS_SCRIPT, args=args, timeout=timeout)
    if r.rc != 0:
        return {"host": host, "ok": False, "t": utc_now(), "error": r.errtext.strip()[-400:] or f"rc {r.rc}"}
    try:
        f = r.json()
    except ValueError as e:
        return {"host": host, "ok": False, "t": utc_now(), "error": f"bad answer: {e}"}
    f["ok"] = True
    f["wall_s"] = round(time.monotonic() - t0, 3)
    f.setdefault("name", host)
    if save:
        d = facts_dir(root)
        os.makedirs(d, exist_ok=True)
        tmp = os.path.join(d, f".{host}.json.{os.getpid()}.{threading.get_ident()}")
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(f, fh, sort_keys=True)
            fh.write("\n")
        os.replace(tmp, os.path.join(d, f"{host}.json"))
    return f


def probe_all(hosts, want=None, timeout=20, save=True, root=None):
    out = {}

    def one(h):
        try:
            out[h] = probe(h, want, timeout, save, root)
        except Exception as e:
            out[h] = {"host": h, "ok": False, "t": utc_now(), "error": f"{type(e).__name__}: {e}"}

    ths = [threading.Thread(target=one, args=(h,)) for h in hosts]
    for t in ths:
        t.start()
    for t in ths:
        t.join()
    return out


def load(host, root=None):
    try:
        with open(os.path.join(facts_dir(root), f"{host}.json"), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def want_for(specs, sets=(), families=("stic", "holt", "gate")):
    from . import keys
    return keys.wants(specs, families, sets)
