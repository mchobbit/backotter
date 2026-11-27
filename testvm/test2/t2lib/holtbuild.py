"""Which build of bko-holt each platform ships: its VM build or its cross build
(docs/design/img-speed.md I12, §8.3, D11, D17).

testvm/test2/holt/builds.json (BUILDS) names the platforms switched to their cross build
(holt-cross.sh), each with the build kind E2 passed on (holt-cross.sh --kind: cross:TOOLCHAIN:
GUEST@SYSROOT12) and the E2 rounds that let it switch:

    {"schema": 1, "reference_days": 7,
     "cross": {"freebsd": {"kind": "cross:1.94.1:freebsd14@32199a051848", "since": "2026-11-27",
                           "e2": [{"code": CODE, "commit": "547cec4", "runs": [RUN_A, RUN_B]}, ...]}}}

A platform not named there (linux and haiku always) ships its VM build.  kinds() gives
HoltTest2's {PLAT: KIND}: the plan keys each holt cell by it (keys.py's holt_build), and the
runner builds that platform with holt-cross.sh alone and ships that build in CODE/PLAT/, the
one the cells test and img-refresh-holt.sh copies (C1).

The weekly VM reference (testvm/holttest1d/holt-ref.py, on the Mac) writes MAC_ROOT/holt-ref/
PLAT.json: {"last": RECORD, "pass": the last PASS RECORD}, a RECORD {"when", "result" PASS or
FAIL, "kind", "code", "runs", "guests", "why"}.  A platform whose last reference FAILed goes
back to its VM build (D11: until the difference is explained and a reference passes again).
One whose reference is due (no PASS, a PASS older than reference_days, or a PASS on another
kind than the pinned one: the toolchain or the sysroot's guest moved) keeps its cross build,
with a note in the plan saying so.  T2_HOLT_BUILD=vm (E2's and the reference's own runs) gives
every platform its VM build, with its cross check build beside it, as before I12.
"""

import datetime
import json
import os

BUILDS = "testvm/test2/holt/builds.json"
REF_DIR = "holt-ref"
REFERENCE_DAYS = 7
CROSS_ABLE = ("freebsd", "netbsd", "illumos", "openbsd", "dragonfly")   # (haiku: D9; linux: never)


def load(src):
    """BUILDS from SRC (a keys source), {} when it is missing or doesn't parse."""
    data = src.read(BUILDS) if src is not None else None
    if not data:
        return {}
    try:
        d = json.loads(data)
    except ValueError:
        return {}
    return d if isinstance(d, dict) else {}


def ref_path(mac_root, plat):
    return os.path.join(mac_root, REF_DIR, f"{plat}.json")


def read_ref(mac_root, plat):
    try:
        with open(ref_path(mac_root, plat), encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def write_ref(mac_root, plat, rec):
    """Record a reference RECORD for PLAT (its last, and its pass when it PASSed)."""
    d = read_ref(mac_root, plat)
    d["last"] = rec
    if rec.get("result") == "PASS":
        d["pass"] = rec
    p = ref_path(mac_root, plat)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    tmp = f"{p}.tmp.{os.getpid()}"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f, indent=1, sort_keys=True)
        f.write("\n")
    os.replace(tmp, p)
    return d


def _days(when, now):
    try:
        t = datetime.datetime.strptime(str(when)[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=datetime.timezone.utc)
    except ValueError:
        return None
    return (now - t).total_seconds() / 86400.0


def ref_due(builds, ref, plat, now=None):
    """Why PLAT's VM reference is due (None: it isn't).  REF: read_ref's."""
    now = now or datetime.datetime.now(datetime.timezone.utc)
    kind = ((builds.get("cross") or {}).get(plat) or {}).get("kind")
    days = builds.get("reference_days", REFERENCE_DAYS)
    p = (ref or {}).get("pass")
    if not p:
        return "no VM reference has passed"
    if p.get("kind") != kind:
        return f"the last PASS was on {p.get('kind')}, the pinned kind is {kind}"
    age = _days(p.get("when"), now)
    if age is None or age > days:
        return f"the last PASS was on {str(p.get('when'))[:10]}, over {days:g} days ago"
    return None


def kinds(src, env=None, mac_root=None, now=None):
    """({PLAT: KIND} of the platforms that ship their cross build, [notes for the plan])."""
    env = os.environ if env is None else env
    builds = load(src)
    cross = builds.get("cross") or {}
    if env.get("T2_HOLT_BUILD") == "vm":
        return {}, ([f"T2_HOLT_BUILD=vm: every platform's VM build (the cross builds of "
                     f"{', '.join(sorted(cross))} are check builds)"] if cross else [])
    if mac_root is None:
        from . import mac_root as default_mac_root
        mac_root = default_mac_root()
    out, notes = {}, []
    for plat in sorted(cross):
        e = cross[plat] if isinstance(cross[plat], dict) else {}
        kind = e.get("kind")
        if plat not in CROSS_ABLE or not isinstance(kind, str) or not kind.startswith("cross:"):
            notes.append(f"{BUILDS}: {plat} has no cross build kind this can ship ({kind!r}): its VM build")
            continue
        ref = read_ref(mac_root, plat)
        last = ref.get("last") or {}
        if last.get("result") == "FAIL":
            notes.append(f"bko-holt {plat}: its VM build, since the VM reference of {str(last.get('when'))[:10]} "
                         f"failed ({last.get('why') or 'see ' + ref_path(mac_root, plat)}): back on the cross build "
                         f"once a reference passes (testvm/holttest1d/holt-ref.py run {plat})")
            continue
        out[plat] = kind
        why = ref_due(builds, ref, plat, now)
        if why:
            notes.append(f"bko-holt {plat}: VM reference due ({why}): testvm/holttest1d/holt-ref.py run {plat}")
    return out, notes


def kind_of(info):
    """The build kind a build info (CODE/build-PLAT.json) records: its "build" for a cross
    build, else "vm" (build-holt.sh's records no kind)."""
    if isinstance(info, dict) and info.get("kind") == "cross" and isinstance(info.get("build"), str):
        return info["build"]
    return "vm"
