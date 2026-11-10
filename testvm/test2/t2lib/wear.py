#!/usr/bin/env python3
"""Wear accounting for Test2 (contract 1.2 Merge, 1.9 Report; rulings R22, R26):

- tag sums: the physical-disk bytes of the jobs.log lines (iolog.sh's) whose
  tag= is a row's wear tag and whose end time falls in [row start - 60 s,
  row t + 900 s];
- diskstats windows: what each physical disk wrote between two
  /proc/diskstats readings (facts' preflight.diskstats), all writers;
- SMART day deltas: what each disk wrote per day by smart.log (the last
  reading of the day before to the day's last).

A disk is physical unless its name matches wear-report.py's VIRTUAL,
(zram|loop|nbd|md|ram|sr|zd)\\d+|dm-\\d+.  Serials (serial= in jobs.log,
smart.log's serial column) are read only to tell disks apart: nothing this
returns or prints holds one; disks are named by name and model.

Self-contained (stdlib, Python 3.9+, no t2lib imports): it runs on a host from stdin,
    python3 -I -B - join < wear.py      with _T2_DATA = {"log", "queries": [{id, tag, start, t}], "window"}
    python3 -I -B - tags T... [--log FILE] < wear.py
and as t2-host wear --tags T....
"""

import calendar
import collections
import json
import re
import sys

VIRTUAL = re.compile(r"(zram|loop|nbd|md|ram|sr|zd)\d+|dm-\d+")
STAMP = re.compile(r"(\d{4})-(\d\d)-(\d\d)T(\d\d):(\d\d):(\d\d)Z\Z")
DEFAULT_LOG = "/bkoscratch/wear/jobs.log"
DEFAULT_SMART = "/bkoscratch/wear/smart.log"
WINDOW = (60, 900)
EXTRA = ("tag", "swpeak", "mpeak", "host_dswap", "host_zram")   # serial= is never kept


def epoch(s):
    m = STAMP.match(s or "")
    if not m:
        return None
    return calendar.timegm(tuple(int(x) for x in m.groups()) + (0, 0, 0))


def physical(name):
    return not VIRTUAL.fullmatch(name)


class Job:
    __slots__ = ("end", "t", "host", "scope", "rc", "secs", "disks", "what", "x")

    def __init__(self, f, x, disks):
        self.end, self.t, self.host, self.scope, self.rc = f[0], epoch(f[0]), f[1], f[2], f[3]
        self.secs = int(f[4]) if f[4].isdigit() else None
        self.disks, self.what, self.x = disks, f[6], x

    @property
    def tag(self):
        return self.x.get("tag")

    def physical_bytes(self):
        return sum(v for k, v in self.disks.items() if physical(k))


def parse_jobs(text_):
    """jobs.log's lines (END HOST SCOPE RC SECS DISK=BYTES,... WHAT [KEY=VALUE...]), serials dropped."""
    out = []
    for ln in text_.split("\n"):
        f = ln.split("\t")
        if len(f) < 7 or not STAMP.match(f[0]):
            continue
        x = {}
        for kv in f[7:]:
            k, _, v = kv.partition("=")
            if k in EXTRA:
                x[k] = v
        disks = collections.Counter()
        if f[5] != "-":
            for kv in f[5].split(","):
                k, _, v = kv.partition("=")
                if k and v.isdigit():
                    disks[k] += int(v)
        out.append(Job(f, x, dict(disks)))
    return out


def read_jobs(path):
    try:
        with open(path, "rb") as fh:
            return parse_jobs(fh.read().decode("utf-8", "replace"))
    except OSError:
        return None


def tag_sums(jobs, tags, lo=None, hi=None):
    """{tag: physical bytes of its lines ending in [lo, hi] (epochs; None: unbounded), or None when no line}."""
    out = {}
    for tg in tags:
        hit = [j for j in jobs if j.tag == tg and j.t is not None
               and (lo is None or j.t >= lo) and (hi is None or j.t <= hi)]
        out[tg] = sum(j.physical_bytes() for j in hit) if hit else None
    return out


def tag_disks(jobs, tags, lo=None, hi=None):
    """{tag: {disk: bytes}} on physical disks, for the report's per-disk lines."""
    out = {}
    for j in jobs:
        if j.tag in tags and j.t is not None and (lo is None or j.t >= lo) and (hi is None or j.t <= hi):
            d = out.setdefault(j.tag, collections.Counter())
            for k, v in j.disks.items():
                if physical(k):
                    d[k] += v
    return {k: dict(v) for k, v in out.items()}


def join(jobs, queries, window=WINDOW):
    """{row id: {tag: bytes or None}}: each query {id, tag, start, t} by its window."""
    out = {}
    for q in queries:
        s, t = epoch(q.get("start")) or epoch(q.get("t")), epoch(q.get("t")) or epoch(q.get("start"))
        lo = None if s is None else s - window[0]
        hi = None if t is None else t + window[1]
        out.setdefault(q["id"], {})[q["tag"]] = tag_sums(jobs, [q["tag"]], lo, hi)[q["tag"]]
    return out


def diskstats_delta(before, after):
    """{disk: {"model", "bytes"}} written between two facts preflight.diskstats readings
    (sectors of 512 bytes); a disk missing from either reading, or whose counter
    went back (a reboot), gives None."""
    out = {}
    for n, a in (after or {}).items():
        b = (before or {}).get(n)
        if not physical(n):
            continue
        if b is None or a.get("sectors_written") is None or b.get("sectors_written") is None \
                or a["sectors_written"] < b["sectors_written"]:
            out[n] = {"model": a.get("model"), "bytes": None}
        else:
            out[n] = {"model": a.get("model"), "bytes": (a["sectors_written"] - b["sectors_written"]) * 512}
    return out


def parse_smart(text_):
    """smart.log's readings per disk: {disk id: [(utc, bytes, model, name)]}.  The
    disk id is an index in order of first appearance, from the serial (names swap
    between boots on Chopin), and never the serial itself."""
    ids, out = {}, collections.defaultdict(list)
    for ln in text_.split("\n"):
        f = ln.split("\t")
        if len(f) < 9 or not STAMP.match(f[0]) or not f[5].isdigit():
            continue
        ident = (f[1], f[4] if f[4] != "-" else "name:" + f[2])
        if ident not in ids:
            ids[ident] = len(ids)
        out[ids[ident]].append((f[0], int(f[5]), f[3], f[2], f[1]))
    for v in out.values():
        v.sort()
    return dict(out)


def smart_days(text_):
    """{(host, disk name, model): {day: bytes written}}, the disk named as its last
    reading names it; each day from the last reading before it to its own last
    (the first day from its first reading)."""
    out = {}
    for _, r in parse_smart(text_).items():
        last = {}
        for utc, b, model, name, host in r:
            last[utc[:10]] = (utc, b)
        prev = r[0][1]
        days = {}
        for day in sorted(last):
            days[day] = last[day][1] - prev
            prev = last[day][1]
        utc, b, model, name, host = r[-1]
        out[(host, name, model)] = days
    return out


def smart_today(text_, day):
    """[{host, disk, model, bytes}] written on DAY (YYYY-MM-DD) by SMART."""
    return [{"host": h, "disk": n, "model": m, "bytes": d.get(day)} for (h, n, m), d in sorted(smart_days(text_).items())]


def main(argv):
    data = json.loads(globals().get("_T2_DATA") or "null")
    cmd = argv[0] if argv else ""
    if cmd == "join":
        d = data or {}
        jobs = read_jobs(d.get("log") or DEFAULT_LOG)
        if jobs is None:
            out = {"results": {}, "error": "no jobs.log", "lines": 0}
        else:
            out = {"results": join(jobs, d.get("queries") or [], tuple(d.get("window") or WINDOW)), "lines": len(jobs)}
        sys.stdout.write(json.dumps(out, sort_keys=True) + "\n")
        return 0
    if cmd == "tags":
        log, tags, i = DEFAULT_LOG, [], 1
        while i < len(argv):
            if argv[i] == "--log":
                log, i = argv[i + 1], i + 2
            else:
                tags.append(argv[i])
                i += 1
        jobs = read_jobs(log)
        if jobs is None:
            sys.stderr.write(f"wear: no {log}\n")
            return 1
        sys.stdout.write(json.dumps(tag_sums(jobs, tags), sort_keys=True) + "\n")
        return 0
    sys.stderr.write("usage: wear.py join (with _T2_DATA) | tags TAG... [--log FILE]\n")
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
