#!/usr/bin/env python3
"""Publish a set of rescue images for download (docs/design/img-speed.md §7, D7), on the Mac.

    img_publish.py [--host H] [--only P,P] [--out DIR] [--today YYYY-MM-DD]
                   [--remote-env K=V]... [--facts-dir DIR] [--dry-run] SET

stick/img-publish.sh runs it; the user starts it (or CarpetBeater's step).  Nothing publishes
by itself after farm rebuilds.

1. Facts.  img_round.py's facts, one ssh per host, from the build hosts that are not the fleet
   host (Chopin: its cooler is the weakest, and the farm keeps it free of xz), or --host's.
2. What is published.  Each platform of the set (all seven, or --only's) needs a current image
   on the chosen host that passed C4's rows: a committed ImgTest2 PASS of its test image's
   SHA-256 in the Mac's ledger whose +img panel passed or wasn't needed, no FAIL since, its
   provenance naming both images (img_round.py current_images and proven).  And its os_epoch
   within the cadence (D3: 14 days), so that a published image is never older in OS updates
   than the cadence allows.  A platform that has none is named, and the set is refused.
3. The host.  --host, else the least loaded host (load a core, as pick-host ranks) that holds
   a publishable image of every platform of the set.
4. Publish.  stick/img-publish-host.sh runs there (sent over ssh): under each platform's shared
   images/.lock-P it links (or copies) the release image into OUT/.SET.part/P and checks its
   SHA-256, compresses it there with xz -9 -T0 under a cmp-run.sh reservation, checks that
   the .xz decompresses to the raw image's SHA-256 (C8), writes P/SHA256SUMS with both, and
   renames the whole to OUT/SET.  It never writes images/P/.  OUT is --out, else the host's
   images/publish (its BKO_IMG_DIR, which --remote-env sets for a trial, /publish).

SET is a name ([A-Za-z0-9][A-Za-z0-9._-]*).  Exit 0 when published (or, with --dry-run,
publishable); 1 when the host step failed; 2 on bad usage; 3 when refused.
"""

import argparse
import datetime
import importlib.machinery
import importlib.util
import json
import os
import re
import shlex
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
T2 = os.path.join(REPO, "testvm", "test2")
HEX64 = re.compile(r"[0-9a-f]{64}\Z")
SET_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*\Z")


def _load(name, path):
    loader = importlib.machinery.SourceFileLoader(name, path)
    spec = importlib.util.spec_from_loader(name, loader)
    m = importlib.util.module_from_spec(spec)
    loader.exec_module(m)
    return m


R = _load("img_round_pub", os.path.join(HERE, "img_round.py"))
PLATFORMS = R.PLATFORMS
CADENCE_DAYS = R.CADENCE_DAYS


class Usage(Exception):
    pass


class Refused(Exception):
    pass


def publishable(gathered, plat, rows, today):
    """{host: (entry or None, why)}: each reached host's current image of PLAT, if publishable."""
    out = {}
    for h, im, why in R.current_images(gathered, plat):
        if why:
            out[h] = (None, why)
            continue
        ok, pwhy = R.proven(rows, plat, im.get("test_sha256"))
        if not ok:
            out[h] = (None, f"not proven (C4): {pwhy}")
            continue
        pv = im["provenance"]
        age = R.epoch_age(pv, today)
        epoch = (pv.get("base") or {}).get("os_epoch")
        if age is None or age > CADENCE_DAYS:
            out[h] = (None, f"past the cadence: its OS epoch {epoch or 'none'} is "
                            f"{'of no known age' if age is None else f'{age} days old'} (D3: {CADENCE_DAYS})")
            continue
        rel = pv.get("release") or {}
        name, sha = os.path.basename(str(rel.get("file") or "")), rel.get("sha256")
        if not name.endswith(".img") or not HEX64.match(str(sha or "")):
            out[h] = (None, "its provenance names no release image")
            continue
        if im.get("release_name") and im["release_name"] != name:
            out[h] = (None, f"its SHA256SUMS names {im['release_name']}, its provenance {name}")
            continue
        out[h] = ({"platform": plat, "release": name, "release_sha256": sha, "test_sha256": im["test_sha256"],
                   "os_epoch": epoch, "age_days": age, "built": pv.get("built"), "proof": pwhy}, None)
    return out


def choose(gathered, plats, rows, today, host=None, table=None):
    """(host, {P: entry}, per-platform notes) or raise Refused, naming the platforms."""
    by = {p: publishable(gathered, p, rows, today) for p in plats}
    hosts = [h for h in gathered if (gathered[h].get("facts") is not None)]
    if host is not None:
        hosts = [h for h in hosts if h == host]
        if not hosts:
            raise Refused(f"{host} gave no facts ({(gathered.get(host) or {}).get('error', 'no answer')})")
    # Each platform must be publishable somewhere, else the set is refused by its name.
    nowhere = []
    for p in plats:
        if not any(by[p].get(h, (None, None))[0] for h in hosts):
            whys = "; ".join(f"{h}: {w}" for h, (e, w) in sorted(by[p].items()) if h in hosts) \
                or "no current image on " + ", ".join(hosts)
            nowhere.append(f"{p} ({whys})")
    if nowhere:
        raise Refused("not publishable: " + "; ".join(nowhere))
    full = [h for h in hosts if all(by[p].get(h, (None, None))[0] for p in plats)]
    if not full:
        lack = "; ".join(f"{h} lacks " + ", ".join(p for p in plats if not by[p].get(h, (None, None))[0])
                         for h in hosts)
        raise Refused(f"no host holds a publishable image of every platform ({lack}): img-xcopy.sh one there, "
                      "or publish with --only")
    rows_by = {r["name"]: r for r in (table or {}).get("hosts", ())} if isinstance(table, dict) else {}

    def load(h):
        return R.PICK.load_per_core((gathered[h].get("probe") or {}), rows_by.get(h) or {})
    best = sorted(full, key=lambda h: (load(h), hosts.index(h)))[0]
    return best, {p: by[p][best][0] for p in plats}


def host_script(remote_env):
    with open(os.path.join(HERE, "img-publish-host.sh"), encoding="utf-8") as f:
        sh = f.read()
    lines = [f"export {k}={shlex.quote(v)}" for k, v in remote_env]
    return "\n".join(lines + [sh])


def main(argv=None):
    ap = argparse.ArgumentParser(prog="img-publish.sh")
    ap.add_argument("--host")
    ap.add_argument("--only")
    ap.add_argument("--out")
    ap.add_argument("--today")
    ap.add_argument("--remote-env", action="append", default=[])
    ap.add_argument("--facts-dir")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--timeout", type=int, default=600)
    ap.add_argument("set")
    try:
        a = ap.parse_args(argv)
    except SystemExit as e:
        return 2 if e.code else 0
    try:
        if not SET_RE.match(a.set):
            raise Usage(f"SET is a name ([A-Za-z0-9._-]), not {a.set!r}")
        H = R.hosts_mod()
        table = H.load()
        fleet = {r["name"] for r in table["hosts"] if "fleet" in (r.get("roles") or ())}
        builders = [r["name"] for r in table["hosts"] if r.get("enabled", True) is not False
                    and "build" in (r.get("roles") or ()) and (r.get("arch") or "amd64") == "amd64"]
        if a.host:
            if a.host in fleet:
                raise Usage(f"--host {a.host}: not on the fleet host (D7: the farm keeps xz off it)")
            if a.host not in builders:
                raise Usage(f"--host: {a.host} is not a build host ({', '.join(builders)})")
            ask = [a.host]
        else:
            ask = H.tie_order([h for h in builders if h not in fleet], table)
        plats = R.pick_list(a.only, PLATFORMS, "--only") or list(PLATFORMS)
        env = []
        for kv in a.remote_env:
            k, eq, v = kv.partition("=")
            if not eq or not re.fullmatch(r"[A-Z_][A-Z0-9_]*", k):
                raise Usage(f"--remote-env K=V, not {kv!r}")
            env.append((k, v))
        today = datetime.date.fromisoformat(a.today) if a.today else datetime.datetime.now(datetime.timezone.utc).date()
        gathered = R.read_facts_dir(a.facts_dir, ask) if a.facts_dir else R.gather(H, ask, env, a.timeout)
        sys.path.insert(0, T2)
        from t2lib import ledger
        rows = ledger.view(fill_wear=False).rows
        host, entries = choose(gathered, plats, rows, today, a.host, table)
        print(f"img-publish {a.set}: on {host} ({today.isoformat()})")
        for p in plats:
            e = entries[p]
            print(f"  {p:<10} {e['release']}  {e['release_sha256'][:12]}  epoch {e['os_epoch']} "
                  f"({e['age_days']} d)  {e['proof']}")
        sys.stdout.flush()   # (before the host's own lines)
        if a.dry_run:
            return 0
        made = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        info = json.dumps({"set": a.set, "host": host, "made": made, "today": today.isoformat(),
                           "platforms": entries}, sort_keys=True)
        if a.out:
            env.append(("BKO_PUBLISH_DIR", a.out))
        args = [a.set, info] + [f"{p}={entries[p]['release']}={entries[p]['release_sha256']}" for p in plats]
        base, dests = R.PICK.ssh_argv(H, host, 6)
        cmd = "bash -s -- " + " ".join(shlex.quote(x) for x in args)
        r = subprocess.run(base + dests[0] + [cmd], input=host_script(env), text=True)
        if r.returncode == 3:
            print(f"img-publish: refused on {host}", file=sys.stderr)
            return 3
        return 0 if r.returncode == 0 else 1
    except (Usage, R.Usage) as e:
        print(f"img-publish: {e}", file=sys.stderr)
        return 2
    except (Refused, R.Refused) as e:
        print(f"img-publish: refused: {e}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    sys.exit(main())
