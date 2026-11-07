#!/usr/bin/env python3
"""What the test hosts' SSDs have written: per disk per day (SMART), and per
job (iolog.sh's cgroup counts), from the wear logs wear-setup.sh set up.

Usage: wear-report.py [--since YYYY-MM-DD] [--top N] [DIR ...]
DIR defaults to /bkoscratch/wear; give one per host (e.g. copies fetched
from each) for both at once.  Jobs are grouped by WHAT's first word (cmp-run's
TAG, or "reserve" plus the command's name); a qemu-nbd scope's line counts
as its job's.
"""
import argparse
import collections
import sys
from pathlib import Path

GB = 1e9


def gb(b: float) -> str:
    return f"{b / GB:,.1f} GB"


def smart(dirs, since):
    rows = collections.defaultdict(list)  # (host, disk, serial) -> [(utc, bytes, pct, model)]
    for d in dirs:
        p = Path(d) / "smart.log"
        if not p.exists():
            continue
        for ln in p.read_text().splitlines():
            f = ln.split("\t")
            if len(f) < 9 or f[5] == "-":
                continue
            rows[(f[1], f[2], f[4])].append((f[0], int(f[5]), f[6], f[3]))
    if not rows:
        print("No SMART readings (smart.log) yet.")
        return
    print("SSD writes per day (SMART; the first day starts at the first reading):")
    for (host, disk, serial), r in sorted(rows.items()):
        r.sort()
        model = r[-1][3]
        print(f"  {host} {disk} {model} ({serial}): {gb(r[-1][1])} in its life, {r[-1][2]}% used")
        last_of_day = {}
        for utc, b, _, _ in r:
            last_of_day[utc[:10]] = b
        days = sorted(last_of_day)
        prev = r[0][1]
        for day in days:
            if not since or day >= since:
                print(f"    {day}: {gb(last_of_day[day] - prev)}")
            prev = last_of_day[day]


def jobs(dirs, since, top):
    lines, nbd = [], collections.defaultdict(lambda: collections.Counter())
    for d in dirs:
        p = Path(d) / "jobs.log"
        if not p.exists():
            continue
        for ln in p.read_text().splitlines():
            f = ln.split("\t")
            if len(f) < 7 or (since and f[0][:10] < since):
                continue
            disks = collections.Counter()
            if f[5] != "-":
                for kv in f[5].split(","):
                    k, _, v = kv.partition("=")
                    disks[k] += int(v)
            if f[6].startswith("nbd for "):
                nbd[(f[1], f[6][len("nbd for "):])] += disks
            else:
                lines.append((f, disks))
    if not lines:
        print("No jobs logged (jobs.log) yet.")
        return
    by_group = collections.defaultdict(lambda: [0, 0, 0])  # group -> [jobs, bytes, seconds]
    each = []
    for f, disks in lines:
        disks = disks + nbd.get((f[1], f[2]), collections.Counter())
        total = sum(disks.values())
        what = f[6].split()
        group = what[0] if what[0] != "reserve" else "reserve " + (Path(what[1]).name if len(what) > 1 else "")
        g = by_group[(f[1], group)]
        g[0] += 1
        g[1] += total
        g[2] += int(f[4]) if f[4].isdigit() else 0
        each.append((total, f[1], f[0], f[6], disks))
    print("Disk writes per group of jobs (host, group: jobs, written, per job, hours):")
    for (host, group), (n, b, s) in sorted(by_group.items(), key=lambda x: -x[1][1]):
        print(f"  {host} {group}: {n} jobs, {gb(b)}, {gb(b / n)} each, {s / 3600:.1f} h")
    print(f"The {top} jobs that wrote most:")
    for total, host, utc, what, disks in sorted(each, reverse=True)[:top]:
        print(f"  {gb(total):>10}  {host} {utc} {what}  ({', '.join(f'{k} {gb(v)}' for k, v in disks.items())})")


def main():
    a = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    a.add_argument("--since")
    a.add_argument("--top", type=int, default=10)
    a.add_argument("dirs", nargs="*", default=["/bkoscratch/wear"])
    o = a.parse_args()
    smart(o.dirs, o.since)
    print()
    jobs(o.dirs, o.since, o.top)


if __name__ == "__main__":
    sys.exit(main())
